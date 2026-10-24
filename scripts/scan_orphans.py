"""
Layer 2: 孤儿 match_id 全量扫描器（pre-flight fast-fail + 软告警双模式）

两种表、两种模式：
  FK 保护表（model_predictions/post_match_review/ab_test_log/features）
    → 硬模式：任何孤儿都 fast-fail（exit 1，带可行动清单）
  history 表（score/wdl/hcp/tg）
    → 软模式：只查 X_X 格式 bug（home==away 的 self-vs-self）
    → scope 外赛事不告警（有意保留，见 DATA_INTEGRITY_REGISTER.md 第 1 条）
    → exit 0，带告警行

用法：
    python scan_orphans.py                          # 默认：FK 表硬模式 + history 软模式
    python scan_orphans.py --mode strict            # 所有表硬模式（全量审计）
    python scan_orphans.py --tables model_predictions  # 只扫指定表
"""
import sqlite3, sys, time, argparse
from pathlib import Path

DB = Path(r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db')

# FK 保护表：开 PRAGMA foreign_keys=ON，任何孤儿都会阻塞写入 → 硬模式
FK_PROTECTED_TABLES = {
    "model_predictions",
    "post_match_review",
    "ab_test_log",
    "features",
}

# history 表：scope 外赛事是合法数据 → 软模式只查 X_X bug
HISTORY_TABLES = {
    "score_history",
    "wdl_history",
    "handicap_history",
    "total_goals_history",
}

ALL_TABLES = sorted(FK_PROTECTED_TABLES | HISTORY_TABLES)


def scan_fk_strict(conn: sqlite3.Connection, table: str) -> list:
    """FK 表硬模式：任何不在 matches/archive 的 match_id 都是问题"""
    return _scan_orphans(conn, table, "strict")


def scan_history_soft(conn: sqlite3.Connection, table: str) -> list:
    """history 表软模式：只查 X_X 格式（self-vs-self），scope 外合法赛事跳过"""
    return _scan_orphans(conn, table, "soft")


def _scan_orphans(conn, table, mode):
    t0 = time.perf_counter()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception as e:
        print(f"  ⚠ {table}: PRAGMA table_info 失败 — {e}")
        return []
    if "match_id" not in cols:
        return []

    if mode == "soft":
        # 软模式：只查三段格式 + 孤儿 + home==away
        orphan_sql = f"""
            SELECT match_id, COUNT(*) AS cnt
            FROM {table}
            WHERE length(match_id) - length(replace(match_id,'_','')) = 2
              AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.match_id = {table}.match_id)
              AND NOT EXISTS (SELECT 1 FROM matches_archive a WHERE a.match_id = {table}.match_id)
            GROUP BY match_id
        """
        # 再在 Python 层过滤 home==away
        rows = conn.execute(orphan_sql).fetchall()
        result = []
        for mid, cnt in rows:
            parts = mid.split('_', 2)
            if len(parts) == 3 and parts[1] == parts[2]:
                result.append((mid, cnt))
        rows = result
    else:
        # 硬模式：任何孤儿
        orphan_sql = f"""
            SELECT child.match_id, COUNT(*) AS cnt
            FROM {table} child
            WHERE child.match_id IS NOT NULL AND child.match_id != ''
              AND NOT EXISTS (SELECT 1 FROM matches m WHERE m.match_id = child.match_id)
              AND NOT EXISTS (SELECT 1 FROM matches_archive a WHERE a.match_id = child.match_id)
            GROUP BY child.match_id
            ORDER BY cnt DESC
        """
        rows = conn.execute(orphan_sql).fetchall()

    dt = (time.perf_counter() - t0) * 1000
    total_rows = sum(r[1] for r in rows)
    tag = "硬" if mode == "strict" else "软"
    label = "孤儿" if mode == "strict" else "self-vs-self bug"
    print(f"  [{table}] ({tag}模式) 扫描耗时 {dt:.0f}ms — {len(rows)} 个{label}, 共 {total_rows:,} 行")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", default=None, help="只扫指定表（逗号分隔），默认扫全部")
    ap.add_argument("--mode", default="default", choices=["default", "strict", "soft"],
                    help="default=FK表硬+history软; strict=全硬; soft=全软")
    ap.add_argument("--detail", action="store_true", help="打印每个孤儿的 match_id 和行数")
    args = ap.parse_args()

    tables = args.tables.split(",") if args.tables else ALL_TABLES

    conn = sqlite3.connect(str(DB))
    total_t0 = time.perf_counter()

    print(f"孤儿 match_id 扫描器 — {DB.name}")
    print(f"模式: {args.mode}")
    print(f"{'='*80}")

    grand_hard_fail = 0  # FK 表硬模式的孤儿数 → exit 1
    grand_soft_warn = 0  # history 表软模式的 bug 数 → exit 0
    all_results = {}

    for tbl in tables:
        if tbl not in ALL_TABLES:
            print(f"  ⚠ {tbl}: 不在已知表清单，跳过")
            continue

        # 决定模式
        if args.mode == "strict":
            mode = "strict"
        elif args.mode == "soft":
            mode = "soft"
        elif tbl in FK_PROTECTED_TABLES:
            mode = "strict"
        else:
            mode = "soft"

        rows = _scan_orphans(conn, tbl, mode)
        all_results[tbl] = (rows, mode)
        if mode == "strict":
            grand_hard_fail += len(rows)
        else:
            grand_soft_warn += len(rows)

    dt = (time.perf_counter() - total_t0) * 1000

    print(f"\n{'='*80}")
    print(f"硬模式孤儿 (FK 表, exit 1):  {grand_hard_fail} 个 match_id")
    print(f"软模式告警 (history 表, exit 0): {grand_soft_warn} 个 self-vs-self bug")
    print(f"总耗时: {dt:.0f}ms")

    # 输出可行动清单
    if args.detail or grand_hard_fail > 0 or grand_soft_warn > 0:
        for tbl, (rows, mode) in all_results.items():
            if not rows:
                continue
            tag = "[硬]" if mode == "strict" else "[软]"
            label = "孤儿" if mode == "strict" else "self-vs-self bug"
            print(f"\n{tag} [{tbl}] {label} ({len(rows)} 个):")
            for mid, cnt in rows[:20]:
                print(f"  {mid} ({cnt} 行)")
            if len(rows) > 20:
                print(f"  ... 共 {len(rows)} 个")

    if grand_hard_fail > 0:
        print(f"\n⛔ 硬模式孤儿 {grand_hard_fail} 个！PRAGMA foreign_keys=ON 后会阻塞写入。")
        print(f"  必须立即清理（详见 DATA_INTEGRITY_REGISTER.md）。")

    if grand_soft_warn > 0:
        print(f"\n⚠ 软模式告警 {grand_soft_warn} 个 self-vs-self bug。")
        print(f"  history 表 scope 外孤儿（合法赛事）不在此告警范围。")

    conn.close()
    sys.exit(1 if grand_hard_fail > 0 else 0)


if __name__ == "__main__":
    main()
