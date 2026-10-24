# -*- coding: utf-8 -*-
"""重复键扫描（只读软模式，C-20261022-190）

背景：同一物理场次的竞彩时序会因销售日跨零点分裂到两个日期键
（如 2026-10-19_Tottenham Hotspur_Coventry City 与 2026-10-20_... 并存）。
赛前解析器（generate_unified_report.find_sporttery_match_id）取"四表总数最多"的键，
复盘解析器（attribution_engine._find_odds_match_id）取"比赛日精确键"——两者读到不同时间线，
导致"赛前有 5 条快照、复盘只读 1 条"。

本脚本只扫描、不修改、不阻断（与 scan_orphans.py 姊妹）：
  - 默认 soft 模式：打印清单，退出码恒为 0
  - --strict：存在"合并后会新增记录"的键对时退出码 1（供未来门禁启用）
  - --json：机器可读输出

判据：
  adjacent  = 同对阵两键日期相差 <= --window（默认 1 天）
  adds_rows = 两键记录集合（按四表逐行去重）合并后，比"记录最多的单键"多出记录
              → 这类键对一旦在读取侧合并，会改变输入（预检 A 红线）

用法：
  python scripts/scan_duplicate_keys.py
  python scripts/scan_duplicate_keys.py --strict --json
"""
import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "data" / "odds.db"

TABLES = {
    "wdl_history": ("timestamp", "win_a", "draw", "win_b"),
    "handicap_history": ("timestamp", "hcp_win", "hcp_draw", "hcp_lose"),
    "total_goals_history": ("timestamp", "goals_0", "goals_1", "goals_2",
                            "goals_3", "goals_4", "goals_5", "goals_6", "goals_7_plus"),
    "score_history": ("timestamp", "score", "odds"),
}


def parse_key(match_id):
    """{YYYY-MM-DD}_{home}_{away} → (date, home, away)；格式不符返回 None。"""
    parts = match_id.split("_", 2)
    if len(parts) != 3:
        return None
    try:
        d = datetime.strptime(parts[0], "%Y-%m-%d").date()
    except ValueError:
        return None
    return d, parts[1], parts[2]


def fetch_rows(conn, table, match_id):
    cols = TABLES[table]
    sql = f"SELECT {','.join(cols)} FROM {table} WHERE match_id=?"
    return [tuple(r) for r in conn.execute(sql, (match_id,))]


def scan(db_path, since, window_days):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        keys = {r[0] for r in conn.execute("SELECT DISTINCT match_id FROM wdl_history")}
        meta = {}
        for k in keys:
            m = parse_key(k)
            if m and m[0] >= since:
                meta[k] = m

        pairs = defaultdict(list)
        for k, (d, home, away) in meta.items():
            pairs[frozenset((home, away))].append((d, home, away, k))

        results = []
        for team_set, lst in pairs.items():
            lst.sort()
            for i in range(len(lst) - 1):
                d1, h1, a1, k1 = lst[i]
                d2, h2, a2, k2 = lst[i + 1]
                if (d2 - d1).days > window_days:
                    continue
                per_table, extras = {}, {}
                totals = {}
                for t in TABLES:
                    r1, r2 = set(fetch_rows(conn, t, k1)), set(fetch_rows(conn, t, k2))
                    per_table[t] = {"k1": len(r1), "k2": len(r2), "union": len(r1 | r2)}
                    rich, poor = (r1, r2) if len(r1) >= len(r2) else (r2, r1)
                    diff = len((r1 | r2) - rich)
                    if diff:
                        extras[t] = diff
                    totals[t] = len(r1 | r2)
                results.append({
                    "key_1": k1,
                    "key_2": k2,
                    "date_gap_days": (d2 - d1).days,
                    "home_away_reversed": not (h1 == h2 and a1 == a2),
                    "tables": per_table,
                    "adds_rows": bool(extras),
                    "extra_rows": extras,
                })
        return results
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="重复键扫描（只读软模式）")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--since", default="2026-07-01", help="起始日期（默认本赛季）")
    ap.add_argument("--window", type=int, default=1, help="日期容差天数（默认 1）")
    ap.add_argument("--strict", action="store_true", help="存在新增记录键对时退出码 1")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    since = datetime.strptime(args.since, "%Y-%m-%d").date()
    rows = scan(Path(args.db), since, args.window)

    bad = [r for r in rows if r["adds_rows"]]
    reversed_pairs = [r for r in rows if r["home_away_reversed"]]

    if args.json:
        print(json.dumps({
            "total_pairs": len(rows),
            "adds_rows_pairs": len(bad),
            "reversed_pairs": len(reversed_pairs),
            "pairs": rows,
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print(f"重复键扫描（since={args.since}，window={args.window} 天，只读软模式）")
        print("=" * 72)
        print(f"相邻日同对阵键对: {len(rows)}")
        print(f"  合并会新增记录（红线）: {len(bad)}")
        print(f"  主客反转（双赛碰撞疑点）: {len(reversed_pairs)}")
        for r in rows:
            flag = "⚠️ 新增" if r["adds_rows"] else "✅ 等价"
            extra = f" {r['extra_rows']}" if r["extra_rows"] else ""
            rev = " [主客反转]" if r["home_away_reversed"] else ""
            print(f"  {flag}{extra}{rev}  {r['key_1']}  <->  {r['key_2']}")
        if not bad:
            print("\n结论：全部键对去重合并后与'记录最多的单键'等价 → 读取侧合并不改变输入。")

    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
