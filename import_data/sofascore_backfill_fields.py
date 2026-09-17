# -*- coding: utf-8 -*-
"""
SofaScore 数据回填脚本：从 stats_json 补算缺失字段
- wonContest → successful_dribbles
- totalContest → total_dribbles
- duelWon → duels_won（已有，校验）
- duelLost → duels_lost_sofa（新增列）
- duelWon+duelLost → duels_total（补算）
- aerialWon → aerials_won_total（已有，校验）
- aerialLost → aerials_lost_sofa（新增列）
- aerialWon+aerialLost → aerials_total（补算）

用法：python sofascore_backfill_fields.py
"""
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

# 需要确保存在的列（如果不存在则 ALTER TABLE ADD）
NEW_COLUMNS = [
    "duels_lost_sofa",
    "aerials_lost_sofa",
]

# stats_json key → DB 列名（需要回填的）
BACKFILL_MAP = {
    "wonContest": "successful_dribbles",
    "totalContest": "total_dribbles",
    "duelLost": "duels_lost_sofa",
    "aerialLost": "aerials_lost_sofa",
}


def ensure_columns(conn: sqlite3.Connection) -> None:
    """ 确保新列存在，不存在则 ALTER TABLE ADD """
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(match_player_stats)")
    existing = {row[1] for row in cur.fetchall()}
    for col in NEW_COLUMNS:
        if col not in existing:
            cur.execute(f"ALTER TABLE match_player_stats ADD COLUMN {col} REAL")
            print(f"[DDL] 已添加列: {col}")
    conn.commit()


def backfill_from_stats_json(conn: sqlite3.Connection) -> dict:
    """ 从 stats_json 解析并回填缺失字段 """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, stats_json FROM match_player_stats
        WHERE stats_source = 'sofascore' AND stats_json IS NOT NULL AND stats_json != ''
    """)
    rows = cur.fetchall()
    total = len(rows)
    print(f"[BACKFILL] 待处理行数: {total}")

    updated = 0
    errors = 0
    batch = []
    BATCH_SIZE = 500

    for row_id, stats_json_str in rows:
        try:
            stats = json.loads(stats_json_str)
        except (json.JSONDecodeError, TypeError):
            errors += 1
            continue

        updates = {}
        for sofa_key, col_name in BACKFILL_MAP.items():
            if sofa_key in stats:
                updates[col_name] = stats[sofa_key]

        # 补算 total 字段
        dw = stats.get("duelWon")
        dl = stats.get("duelLost")
        if dw is not None or dl is not None:
            updates["duels_total"] = (dw or 0) + (dl or 0)
            if dw is not None:
                updates["duels_won"] = dw

        aw = stats.get("aerialWon")
        al = stats.get("aerialLost")
        if aw is not None or al is not None:
            updates["aerials_total"] = (aw or 0) + (al or 0)
            if aw is not None:
                updates["aerials_won_total"] = aw

        if not updates:
            continue

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [row_id]
        batch.append((set_clause, values))

        if len(batch) >= BATCH_SIZE:
            _execute_batch(cur, batch)
            updated += len(batch)
            batch = []
            if updated % 5000 == 0:
                print(f"[BACKFILL] 进度: {updated}/{total} ({updated/total*100:.1f}%)")

    if batch:
        _execute_batch(cur, batch)
        updated += len(batch)

    conn.commit()
    print(f"[BACKFILL] ✅ 完成 | 更新={updated} | 错误={errors} | 总计={total}")
    return {"updated": updated, "errors": errors, "total": total}


def _execute_batch(cur: sqlite3.Cursor, batch: list) -> None:
    for set_clause, values in batch:
        cur.execute(f"UPDATE match_player_stats SET {set_clause} WHERE id = ?", values)


def verify_results(conn: sqlite3.Connection) -> None:
    """ 验证回填后字段非空率 """
    cur = conn.cursor()
    fields = [
        "successful_dribbles", "total_dribbles",
        "duels_won", "duels_lost_sofa", "duels_total",
        "aerials_won_total", "aerials_lost_sofa", "aerials_total",
    ]
    cur.execute("""
        SELECT COUNT(1) FROM match_player_stats
        WHERE stats_source = 'sofascore' AND COALESCE(minutes_played, 0) > 0
    """)
    total = cur.fetchone()[0]
    print(f"\n[VERIFY] 出场球员总数: {total}")
    for f in fields:
        cur.execute(f"""
            SELECT COUNT(1) FROM match_player_stats
            WHERE stats_source = 'sofascore' AND COALESCE(minutes_played, 0) > 0
              AND {f} IS NOT NULL AND {f} != ''
        """)
        n = cur.fetchone()[0]
        pct = n / total * 100 if total > 0 else 0
        mark = "✅" if pct >= 70 else "❌"
        print(f"  {mark} {f:30s} 非空率={pct:5.1f}% ({n}/{total})")


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERROR] DB not found: {DB_PATH}")
        return 2
    conn = sqlite3.connect(str(DB_PATH))
    try:
        ensure_columns(conn)
        backfill_from_stats_json(conn)
        verify_results(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
