# -*- coding: utf-8 -*-
"""P2-11: SQLite(odds.db) → PostgreSQL 迁移工具

数据规模: 1.5GB / 33 表 / 约 420 万行

分阶段执行（可断点重跑，幂等）:
  python scripts/pg_migrate.py --phase export       # SQLite → data/pg_export/*.csv + manifest.json
  python scripts/pg_migrate.py --phase createdb     # CREATE DATABASE odds
  python scripts/pg_migrate.py --phase schema       # 建表（含三张大表按日期分区）
  python scripts/pg_migrate.py --phase import       # COPY 导入（大表先进 staging）
  python scripts/pg_migrate.py --phase postprocess  # staging 物化日期 → 建索引 → ANALYZE
  python scripts/pg_migrate.py --phase verify       # 逐表行数校验
  python scripts/pg_migrate.py --phase all          # 顺序执行全部

设计要点:
  - 驱动: pg8000(纯 Python, pylibs/), 规避 Python 3.14 无 psycopg wheel 的问题
  - CSV NULL 约定: None → \\N (COPY NULL '\\N'), 空串保持 "" 引用形式, 语义无损
  - 分区表: match_player_stats / match_lineups (物化 match_date DATE, 按年 RANGE),
            score_history ("timestamp" → TIMESTAMPTZ, 按年 RANGE), 均带 DEFAULT 分区
  - 外键不迁移(分析型负载, 避免拖慢 COPY), 其余约束保真
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "pylibs"))

import pg8000.dbapi  # noqa: E402

SQLITE_PATH = PROJECT_ROOT / "data" / "odds.db"
SCHEMA_JSON = PROJECT_ROOT / "scripts" / "pg_migration_schema.json"
EXPORT_DIR = PROJECT_ROOT / "data" / "pg_export"

PG_HOST, PG_PORT = "localhost", 5432
PG_USER, PG_PASSWORD = "postgres", "postgres"
PG_DB = "odds"

# 三张分区大表
PARTITIONED_TABLES = {"match_player_stats", "match_lineups", "score_history"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# PG 追加性能索引（与 SQLite 迁移索引互补, 已人工对照去重）
EXTRA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mps_team_date ON match_player_stats(team, match_date)",
    "CREATE INDEX IF NOT EXISTS idx_mps_fbref ON match_player_stats(fbref_match_id)",
    "CREATE INDEX IF NOT EXISTS idx_ml_team_date ON match_lineups(team, match_date)",
    "CREATE INDEX IF NOT EXISTS idx_ml_fbref ON match_lineups(fbref_match_id)",
    'CREATE INDEX IF NOT EXISTS idx_score_match_ts ON score_history(match_id, "timestamp")',
    "CREATE INDEX IF NOT EXISTS idx_ouzhi_company_match ON odds500_ouzhi_company(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_matches_league_date ON matches(league, match_date)",
    "CREATE INDEX IF NOT EXISTS idx_upx_player_season ON understat_player_xg(player_id, season)",
    "CREATE INDEX IF NOT EXISTS idx_understat_shots_match ON understat_shots(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_odds500_match_match ON odds500_match(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_odds500_betting_match ON odds500_betting(match_id)",
]

# odds500_ouzhi_company 的 TEXT 数值列 → double precision 优化
#   - 纯数值列: 直接 cast
#   - 百分比列: 去 '%' 后存数值('72.06%' → 72.06, 保真; 下游按需 /100)
#   - kelly_*: 全 NULL 空列, 转数值类型保持一致
OUZHI_PLAIN_COLS = ["init_win", "init_draw", "init_lose",
                    "live_win", "live_draw", "live_lose"]
OUZHI_PCT_COLS = ["prob_init_win", "prob_init_draw", "prob_init_lose",
                  "prob_live_win", "prob_live_draw", "prob_live_lose",
                  "return_init", "return_live"]
OUZHI_NULL_COLS = ["kelly_init_win", "kelly_init_draw", "kelly_init_lose",
                   "kelly_live_win", "kelly_live_draw", "kelly_live_lose"]


# ==================== 通用工具 ====================

def pg_connect(db: str | None = None, autocommit: bool = False):
    return pg8000.dbapi.connect(
        user=PG_USER, password=PG_PASSWORD, host=PG_HOST, port=PG_PORT,
        database=db or PG_DB, timeout=3600,  # 大表 INSERT/ANALYZE 耗时长
    )


def q(ident: str) -> str:
    """带引号的标识符（处理 timestamp 等类型名冲突）。"""
    return f'"{ident}"'


def load_schema() -> dict:
    data = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))
    # 剥离 DDL 内注释（-- 开头到行尾），避免注释与下一列合并导致漏列
    for info in data.values():
        info["ddl"] = re.sub(r"--[^\n]*", "", info["ddl"])
    return data


def split_top_level(s: str) -> list[str]:
    """按顶层逗号切分列定义（括号内逗号不切）。"""
    parts, depth, buf = [], 0, []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


TYPE_MAP = {"INTEGER": "BIGINT", "INT": "INTEGER", "REAL": "DOUBLE PRECISION",
            "TEXT": "TEXT", "BLOB": "BYTEA", "DATETIME": "TIMESTAMPTZ",
            "BOOLEAN": "BOOLEAN", "DATE": "DATE"}


def load_type_overrides() -> dict[tuple[str, str], str]:
    """SQLite 宽松类型导致的实际数据类型覆盖: (table, col) -> PG 类型。"""
    path = PROJECT_ROOT / "scripts" / "pg_type_overrides.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for table, cols in data.get("float_in_int", {}).items():
        for c in cols:
            out[(table, c)] = "DOUBLE PRECISION"
    return out


PG_TYPE_OVERRIDES = load_type_overrides()


def convert_column(col_def: str, table: str) -> str:
    """转换一列的 SQLite 定义 → PG 定义。"""
    m = re.match(r'^("?\w+"?)\s+(\w+)\s*(.*)$', col_def, re.I)
    if not m:
        return col_def  # 约束或无法识别, 原样
    col_name = m.group(1).strip().strip('"')
    sqlite_type = m.group(2).upper()
    rest = m.group(3) or ""
    if sqlite_type not in TYPE_MAP:
        return col_def  # 未知类型(纯约束), 原样

    # id 自增主键
    if re.search(r"PRIMARY\s+KEY\s+AUTOINCREMENT", rest, re.I):
        return f"{q(col_name)} BIGINT GENERATED BY DEFAULT AS IDENTITY"

    # 类型映射
    pg_type = TYPE_MAP[sqlite_type]
    if (table, col_name) in PG_TYPE_OVERRIDES:
        pg_type = PG_TYPE_OVERRIDES[(table, col_name)]  # INTEGER 列实际含小数
    if col_name == "match_date" and table != "__keep_text__":
        pg_type = "DATE"
    elif col_name == "timestamp":
        pg_type = "TIMESTAMPTZ"
    if re.search(r"DEFAULT\s+CURRENT_TIMESTAMP", rest, re.I):
        rest = re.sub(r"DEFAULT\s+CURRENT_TIMESTAMP", "DEFAULT now()", rest, flags=re.I)
    rest = re.sub(r"DEFAULT\s*\(\s*datetime\s*\(\s*'now'\s*,\s*'localtime'\s*\)\s*\)",
                  "DEFAULT localtimestamp", rest, flags=re.I)
    # 放宽列级 NOT NULL：导出时脏日期/时间戳已置 NULL，保数据完整性优先
    rest = re.sub(r"\bNOT\s+NULL\b", "", rest, flags=re.I)
    return f"{q(col_name)} {pg_type} {rest}".strip()


def table_columns(ddl: str) -> list[str]:
    """从 CREATE TABLE DDL 提取列名列表（不含表级约束）。"""
    body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
    cols = []
    for part in split_top_level(body):
        m = re.match(r'^"?(\w+)"?\s+(INTEGER|INT|REAL|TEXT|BLOB|DATETIME|BOOLEAN|DATE)\b',
                     part.strip(), re.I)
        if m:
            cols.append(m.group(1))
    return cols


def build_partition_ddl(table: str, ddl: str, manifest: dict) -> list[str]:
    """为三张大表生成分区 DDL。"""
    cols = table_columns(ddl)
    body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
    col_defs = [convert_column(p, table) for p in split_top_level(body)
                if not re.match(r'^\s*(PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\b', p.strip(), re.I)]

    if table == "score_history":
        part_col = '"timestamp"'
        pk_extra = ""  # 分区键 timestamp 含 NULL(19,747 条坏时间戳), PK 会隐式 NOT NULL, 故去掉
        extra_cols = ""
        unique = 'UNIQUE (match_id, "timestamp", score)'
    else:
        part_col = "match_date"
        pk_extra = "match_date"
        extra_cols = ", match_date DATE"  # 物化列
        if table == "match_lineups":
            unique = "UNIQUE (match_date, match_id, team, player_name)"
        else:
            unique = ""

    years = manifest["partition_years"]  # e.g. [2019, 2026]
    pk_clause = f"PRIMARY KEY (id, {pk_extra})" if pk_extra else ""
    stmts = [
        f"CREATE TABLE {q(table)} (\n  " + ",\n  ".join(
            col_defs + ([pk_clause] if pk_clause else []) + ([unique] if unique else []))
        + f"{extra_cols}\n) PARTITION BY RANGE ({part_col})"
    ]
    for y in range(years[0], years[1] + 1):
        stmts.append(
            f"CREATE TABLE {q(f'{table}_p{y}')} PARTITION OF {q(table)} "
            f"FOR VALUES FROM ('{y}-01-01') TO ('{y + 1}-01-01')"
        )
    stmts.append(f"CREATE TABLE {q(table + '_pdef')} PARTITION OF {q(table)} DEFAULT")
    return stmts


# ==================== Phase 1: export ====================

def phase_export():
    EXPORT_DIR.mkdir(exist_ok=True)
    schema = load_schema()
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = None
    manifest = {"tables": {}, "partition_years": [2020, 2026]}

    # 分区年份范围依据 fbref_match_mapping 的日期范围
    lo, hi = conn.execute(
        "SELECT MIN(match_date), MAX(match_date) FROM fbref_match_mapping").fetchone()
    y0, y1 = int(lo[:4]) - 1, int(hi[:4]) + 1
    manifest["partition_years"] = [y0, y1]
    print(f"[export] partition years: {y0}-{y1}")

    for table in schema:
        cur = conn.execute(f'SELECT * FROM {q(table)}')
        cols = [d[0] for d in cur.description]
        path = EXPORT_DIR / f"{table}.csv"
        n_bad_date = n_bad_ts = 0
        date_idx = cols.index("match_date") if "match_date" in cols else -1
        ts_idx = cols.index("timestamp") if "timestamp" in cols else -1
        count = 0
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            while True:
                rows = cur.fetchmany(50000)
                if not rows:
                    break
                out = []
                for r in rows:
                    vals = []
                    for i, v in enumerate(r):
                        if v is None:
                            vals.append("\\N")
                        elif isinstance(v, bytes):
                            vals.append(v.decode("utf-8", "replace"))
                        else:
                            vals.append(v)
                    # 日期/时间戳格式验证
                    if date_idx >= 0 and vals[date_idx] != "\\N" and not DATE_RE.match(str(vals[date_idx])):
                        vals[date_idx] = "\\N"
                        n_bad_date += 1
                    if ts_idx >= 0 and vals[ts_idx] != "\\N" and not TS_RE.match(str(vals[ts_idx])):
                        vals[ts_idx] = "\\N"
                        n_bad_ts += 1
                    out.append(vals)
                writer.writerows(out)
                count += len(out)
        manifest["tables"][table] = {"rows": count, "bad_date": n_bad_date, "bad_ts": n_bad_ts}
        warn = f"  (bad_date={n_bad_date}, bad_ts={n_bad_ts})" if (n_bad_date or n_bad_ts) else ""
        print(f"[export] {table:36s} {count:>10,}{warn}")

    (EXPORT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    conn.close()
    print(f"[export] done -> {EXPORT_DIR}")


# ==================== Phase 2: createdb ====================

def phase_createdb():
    conn = pg_connect(db="postgres")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (PG_DB,))
    if cur.fetchone():
        print(f"[createdb] database '{PG_DB}' already exists")
    else:
        cur.execute(f'CREATE DATABASE {q(PG_DB)} ENCODING \'UTF8\' TEMPLATE template0')
        print(f"[createdb] database '{PG_DB}' created")
    cur.execute(f"ALTER DATABASE {q(PG_DB)} SET timezone TO 'Asia/Shanghai'")
    conn.close()


# ==================== Phase 3: schema ====================

def phase_schema():
    schema = load_schema()
    manifest = json.loads((EXPORT_DIR / "manifest.json").read_text(encoding="utf-8"))
    conn = pg_connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='match_player_stats'")
    if cur.fetchone():
        print("[schema] tables already exist, skip (drop them first to rebuild)")
        conn.close()
        return

    for table, info in schema.items():
        ddl = info["ddl"]
        if table in PARTITIONED_TABLES:
            stmts = build_partition_ddl(table, ddl, manifest)
        else:
            body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
            parts = []
            for p in split_top_level(body):
                ps = p.strip()
                if re.match(r"^FOREIGN\s+KEY", ps, re.I):
                    continue  # 不迁移外键
                parts.append(convert_column(ps, table))
            stmts = [f"CREATE TABLE {q(table)} (\n  " + ",\n  ".join(parts) + "\n)"]
        for s in stmts:
            cur.execute(s)
        print(f"[schema] {table} ok")
    conn.close()
    print("[schema] all tables created")


# ==================== Phase 4: import ====================

def copy_table(cur, table: str, target: str, cols: list[str]):
    path = EXPORT_DIR / f"{table}.csv"
    col_sql = ", ".join(q(c) for c in cols)
    with open(path, "rb") as f:
        # pg8000: COPY FROM STDIN 通过 execute 的 stream 参数实现
        cur.execute(
            f"COPY {q(target)} ({col_sql}) FROM STDIN WITH (FORMAT csv, NULL '\\N')",
            stream=f)


def phase_import():
    schema = load_schema()
    manifest = json.loads((EXPORT_DIR / "manifest.json").read_text(encoding="utf-8"))
    conn = pg_connect()
    cur = conn.cursor()
    for table in sorted(schema):
        cols = table_columns(schema[table]["ddl"])
        target = f"staging_{table}" if table in PARTITIONED_TABLES else table
        # 幂等跳过: 目标行数已与导出清单一致则复用（断点续传）
        expected = manifest.get("tables", {}).get(table, {}).get("rows")
        if expected is not None:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {q(target)}")
                if cur.fetchone()[0] == expected:
                    print(f"[import] {table} -> {target} already complete, skip")
                    continue
            except Exception:  # noqa: BLE001
                conn.rollback()  # 表不存在, 继续正常导入
        if table in PARTITIONED_TABLES:
            # staging 表：列定义与目标分区表相同但不分区、无物化列/主键
            body = schema[table]["ddl"][schema[table]["ddl"].index("(") + 1: schema[table]["ddl"].rindex(")")]
            parts = [convert_column(p, table) for p in split_top_level(body)
                     if not re.match(r'^\s*(PRIMARY\s+KEY|UNIQUE|FOREIGN\s+KEY|CHECK)\b', p.strip(), re.I)]
            parts = [re.sub(r"GENERATED BY DEFAULT AS IDENTITY", "", p) for p in parts]
            cur.execute(f"DROP TABLE IF EXISTS {q(target)}")
            cur.execute(f"CREATE TABLE {q(target)} (\n  " + ",\n  ".join(parts) + "\n)")
        else:
            cur.execute(f"TRUNCATE TABLE {q(target)}")  # 幂等: 清空后重导
        conn.commit()
        copy_table(cur, table, target, cols)
        conn.commit()
        print(f"[import] {table} -> {target}")
    conn.close()
    print("[import] all tables copied")


# ==================== Phase 5: postprocess ====================

STAGING_INSERTS = {
    "match_player_stats": """
        INSERT INTO match_player_stats
        SELECT s.*, fmm.match_date
        FROM staging_match_player_stats s
        LEFT JOIN fbref_match_mapping fmm ON s.fbref_match_id = fmm.fbref_match_id""",
    "match_lineups": """
        INSERT INTO match_lineups
        SELECT s.*, fmm.match_date
        FROM staging_match_lineups s
        LEFT JOIN fbref_match_mapping fmm ON s.fbref_match_id = fmm.fbref_match_id""",
    "score_history": """
        INSERT INTO score_history
        SELECT * FROM staging_score_history""",
}


def optimize_ouzhi_types(cur) -> int:
    """odds500_ouzhi_company: TEXT 数值列 → double precision (幂等)。

    返回实际转换的列数。
    """
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='odds500_ouzhi_company'
          AND data_type='double precision'""")
    done = {r[0] for r in cur.fetchall()}

    n = 0
    for col in OUZHI_PLAIN_COLS + OUZHI_NULL_COLS:
        if col in done:
            continue
        cur.execute(
            f"ALTER TABLE odds500_ouzhi_company "
            f"ALTER COLUMN {q(col)} TYPE double precision "
            f"USING NULLIF(BTRIM({q(col)}), '')::double precision")
        n += 1
    for col in OUZHI_PCT_COLS:
        if col in done:
            continue
        cur.execute(
            f"ALTER TABLE odds500_ouzhi_company "
            f"ALTER COLUMN {q(col)} TYPE double precision "
            f"USING NULLIF(BTRIM(REPLACE({q(col)}, '%', '')), '')::double precision")
        n += 1
    return n


def phase_postprocess():
    schema = load_schema()
    conn = pg_connect()
    cur = conn.cursor()

    # 1. staging → 分区表
    for table, sql in STAGING_INSERTS.items():
        try:
            cur.execute(f"SELECT COUNT(*) FROM {q(f'staging_{table}')}")
            n_src = cur.fetchone()[0]
        except Exception:  # noqa: BLE001  staging 已不存在 = 上次已成功迁移
            conn.rollback()
            print(f"[postprocess] {table}: staging gone, already migrated, skip")
            continue
        cur.execute(f"SELECT COUNT(*) FROM {q(table)}")
        n_dst = cur.fetchone()[0]
        if n_dst == n_src:
            print(f"[postprocess] {table}: already {n_dst:,} rows, skip insert")
        else:
            if n_dst:  # 幂等: 清掉上次失败残留的半成品数据
                cur.execute(f"TRUNCATE TABLE {q(table)}")
            cur.execute(sql)
            cur.execute(f"SELECT COUNT(*) FROM {q(table)}")
            n_dst = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM {q(f'{table}_pdef')}")
        n_def = cur.fetchone()[0]
        conn.commit()
        status = "OK" if n_src == n_dst else f"MISMATCH src={n_src} dst={n_dst}"
        print(f"[postprocess] {table}: {n_dst:,} rows ({n_def:,} in default) {status}")
        cur.execute(f"DROP TABLE IF EXISTS {q(f'staging_{table}')}")
        conn.commit()

    # 2. 迁移 SQLite 索引（跳过分区表上与分区键冲突的 UNIQUE 索引）
    for table, info in schema.items():
        for idx in info["indexes"]:
            sql = idx["sql"].replace("IF NOT EXISTS ", "")
            name = idx["name"]
            try:
                cur.execute(sql)
            except Exception as e:  # noqa: BLE001
                print(f"[postprocess] index {name} skipped: {type(e).__name__}")
                conn.rollback()
    conn.commit()

    # 3. 追加性能索引
    for sql in EXTRA_INDEXES:
        cur.execute(sql)
    conn.commit()
    print(f"[postprocess] extra indexes created: {len(EXTRA_INDEXES)}")

    # 3.5 odds500_ouzhi_company: TEXT 数值列 → double precision
    n_opt = optimize_ouzhi_types(cur)
    conn.commit()
    print(f"[postprocess] ouzhi_company columns optimized: {n_opt}")

    # 4. 统计信息
    cur.execute("ANALYZE")
    conn.commit()
    print("[postprocess] ANALYZE done")
    conn.close()


# ==================== Phase 6: verify ====================

def phase_verify():
    manifest = json.loads((EXPORT_DIR / "manifest.json").read_text(encoding="utf-8"))
    conn = pg_connect()
    cur = conn.cursor()
    ok = total_diff = 0
    print(f"{'table':38s} {'sqlite':>12s} {'pg':>12s} {'match':>6s}")
    for table, info in manifest["tables"].items():
        cur.execute(f"SELECT COUNT(*) FROM {q(table)}")
        n_pg = cur.fetchone()[0]
        n_sq = info["rows"]
        match = "OK" if n_pg == n_sq else "DIFF"
        if match == "OK":
            ok += 1
        else:
            total_diff += 1
        print(f"{table:38s} {n_sq:>12,} {n_pg:>12,} {match:>6s}")
    conn.close()
    print(f"\n[verify] {ok} tables OK, {total_diff} mismatched")


PHASES = {
    "export": phase_export,
    "createdb": phase_createdb,
    "schema": phase_schema,
    "import": phase_import,
    "postprocess": phase_postprocess,
    "verify": phase_verify,
}


def main():
    parser = argparse.ArgumentParser(description="P2-11 SQLite → PostgreSQL 迁移")
    parser.add_argument("--phase", choices=[*PHASES, "all"], required=True)
    args = parser.parse_args()
    t0 = datetime.now()
    if args.phase == "all":
        for name, fn in PHASES.items():
            print(f"\n========== PHASE {name} ==========")
            fn()
    else:
        PHASES[args.phase]()
    print(f"\nelapsed: {datetime.now() - t0}")


if __name__ == "__main__":
    main()
