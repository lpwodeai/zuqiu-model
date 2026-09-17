"""
P1-14 赔率时序网格持久化
把 wdl / tg / handicap 的散乱时间戳快照，规整到 7 个固定时间网格，持久化到 DB。

时间网格: open / T-24h / T-12h / T-6h / T-2h / T-1h / close

用法:
  python scripts/odds_grid.py --refresh-all
  python scripts/odds_grid.py --match_id XXX
"""

import argparse
import os
import sys
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds.db")

GRID_KEYS = ["open", "t-24h", "t-12h", "t-6h", "t-2h", "t-1h", "close"]
GRID_OFFSETS_HOURS = {"t-24h": 24, "t-12h": 12, "t-6h": 6, "t-2h": 2, "t-1h": 1}
JUMP_THRESHOLD = 0.05


def ensure_grid_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS odds_grid_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            odds_type TEXT NOT NULL,
            grid_key TEXT NOT NULL,
            snapshot_time TEXT,
            val1 REAL,
            val2 REAL,
            val3 REAL,
            abnormal_jump TEXT,
            UNIQUE(match_id, odds_type, grid_key)
        )
    """)
    conn.commit()


def parse_kickoff_from_match_id(match_id):
    try:
        date_str = match_id.split("_")[0]
        return datetime.strptime(date_str + " 22:00:00", "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _load_wdl(cur, mid):
    rows = cur.execute(
        "SELECT timestamp, win_a, draw, win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp",
        (mid,)).fetchall()
    return [(r["timestamp"], r["win_a"], r["draw"], r["win_b"]) for r in rows]


def _load_tg(cur, mid):
    try:
        rows = cur.execute(
            "SELECT timestamp, over_2_5, under_2_5 FROM tg_history WHERE match_id=? ORDER BY timestamp",
            (mid,)).fetchall()
        return [(r["timestamp"], r["over_2_5"], None, r["under_2_5"]) for r in rows]
    except Exception:
        return []


def _load_hc(cur, mid):
    try:
        rows = cur.execute(
            "SELECT timestamp, handi_line, handi_win_a, handi_win_b FROM handicap_history WHERE match_id=? ORDER BY timestamp",
            (mid,)).fetchall()
        return [(r["timestamp"], r["handi_win_a"], r["handi_line"], r["handi_win_b"]) for r in rows]
    except Exception:
        return []


LOADERS = {"wdl": _load_wdl, "tg": _load_tg, "handicap": _load_hc}


def _parse_ts(ts_str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(ts_str, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None



def build_grid(records, kickoff_time=None):
    """把 [(timestamp, v1, v2, v3), ...] 映射到 7 个固定网格点，含异动标记。"""
    if not records:
        return {k: None for k in GRID_KEYS}

    parsed = []
    for rec in records:
        ts = _parse_ts(rec[0])
        if ts is not None:
            parsed.append((ts,) + tuple(rec[1:]))
    parsed.sort(key=lambda x: x[0])
    if not parsed:
        return {k: None for k in GRID_KEYS}

    grid = {}
    p0 = parsed[0]
    grid["open"] = {"snapshot_time": p0[0].strftime("%Y-%m-%d %H:%M:%S"), "v1": p0[1], "v2": p0[2], "v3": p0[3]}
    pn = parsed[-1]
    grid["close"] = {"snapshot_time": pn[0].strftime("%Y-%m-%d %H:%M:%S"), "v1": pn[1], "v2": pn[2], "v3": pn[3]}

    mid_keys = ["t-24h", "t-12h", "t-6h", "t-2h", "t-1h"]
    if kickoff_time:
        for key in mid_keys:
            target = kickoff_time - timedelta(hours=GRID_OFFSETS_HOURS[key])
            chosen = None
            for p in parsed:
                if p[0] <= target:
                    chosen = p
                else:
                    break
            if chosen:
                grid[key] = {"snapshot_time": chosen[0].strftime("%Y-%m-%d %H:%M:%S"), "v1": chosen[1], "v2": chosen[2], "v3": chosen[3]}
            else:
                grid[key] = None
    else:
        n = len(parsed)
        for i, key in enumerate(mid_keys):
            idx = min(int((i + 1) * n / 6), n - 1)
            p = parsed[idx]
            grid[key] = {"snapshot_time": p[0].strftime("%Y-%m-%d %H:%M:%S"), "v1": p[1], "v2": p[2], "v3": p[3]}

    # 异动检测（按 v1 赔率）
    prev_val = None
    for key in GRID_KEYS:
        cur = grid.get(key)
        jump = None
        if cur is not None and prev_val is not None and cur["v1"] is not None:
            delta = cur["v1"] - prev_val
            if abs(delta) >= JUMP_THRESHOLD:
                jump = ("DOWN" if delta < 0 else "UP") + f"({delta:+.3f})"
        if cur is not None:
            cur["abnormal_jump"] = jump
            prev_val = cur["v1"]

    return grid


def refresh_match_grid(match_id, conn=None):
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_grid_table(conn)
    kickoff = parse_kickoff_from_match_id(match_id)
    results = {}

    for odds_type, loader in LOADERS.items():
        records = loader(cur, match_id)
        if not records:
            continue
        grid = build_grid(records, kickoff)
        results[odds_type] = grid
        for key in GRID_KEYS:
            item = grid.get(key)
            if item is None:
                continue
            cur.execute("""
                INSERT OR REPLACE INTO odds_grid_snapshot
                (match_id, odds_type, grid_key, snapshot_time, val1, val2, val3, abnormal_jump)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (match_id, odds_type, key,
                  item.get("snapshot_time"), item.get("v1"), item.get("v2"), item.get("v3"),
                  item.get("abnormal_jump")))
    conn.commit()
    if own_conn:
        conn.close()
    return results


def refresh_all():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    match_ids = set()
    for table in ["wdl_history", "tg_history", "handicap_history"]:
        try:
            rows = cur.execute(f"SELECT DISTINCT match_id FROM {table}").fetchall()
            for r in rows:
                match_ids.add(r["match_id"])
        except Exception:
            pass

    print(f"共 {len(match_ids)} 场比赛，开始刷新网格...")
    cnt = 0
    for mid in sorted(match_ids):
        refresh_match_grid(mid, conn=conn)
        cnt += 1
        if cnt % 100 == 0:
            print(f"  已处理 {cnt}/{len(match_ids)}")
    print(f"完成！共 {cnt} 场。")
    conn.close()


def print_grid(match_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    results = refresh_match_grid(match_id, conn=conn)
    conn.close()

    labels_map = {
        "wdl": ("主胜", "平", "客胜"),
        "tg": ("大2.5", None, "小2.5"),
        "handicap": ("主让胜", "盘口", "客让胜"),
    }

    print(f"match_id: {match_id}")
    for odds_type, grid in results.items():
        print(f"\n--- {odds_type.upper()} 网格 ---")
        lab = labels_map.get(odds_type, ("v1", "v2", "v3"))
        hdr = f"  {'grid':8s}  {'时间':20s}  "
        for l in lab:
            if l:
                hdr += f"{l:>8s}  "
        hdr += "异动"
        print(hdr)
        print("  " + "-" * 70)
        for key in GRID_KEYS:
            item = grid.get(key)
            if item is None:
                print(f"  {key:8s}  (无数据)")
                continue
            line = f"  {key:8s}  {item.get('snapshot_time',''):20s}  "
            for i, l in enumerate(lab):
                if l:
                    v = item.get(f"v{i+1}")
                    line += f"{v:8.3f}  " if v is not None else f"{'--':>8s}  "
            line += item.get("abnormal_jump") or "-"
            print(line)


def main():
    parser = argparse.ArgumentParser(description="赔率时序网格持久化 (P1-14)")
    parser.add_argument("--refresh-all", action="store_true")
    parser.add_argument("--match_id", type=str, default=None)
    args = parser.parse_args()

    if args.refresh_all:
        refresh_all()
    elif args.match_id:
        print_grid(args.match_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

