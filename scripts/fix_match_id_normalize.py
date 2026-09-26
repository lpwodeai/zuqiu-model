# -*- coding: utf-8 -*-
"""
时序表 match_id 队名归一化修正（C-20260918-023）
=============================================

问题：sporttery_live_collector 之前仅用 TEAM_NAME_MAP.get(name, name) 简单映射，
     竞彩 API 返回的队名不在映射表时直接用原始短名（如「皇马」「伊普斯」），
     导致 wdl_history 等 4 张时序表的 match_id 与 500.com 归一化后的中文标准名不一致。

修正：扫描 4 张时序表 match_id，拆分 {date}_{home}_{away}，
     对 home/away 调 normalize_team_name() 4 级归一化，生成新 match_id 并更新。

用法:
  python fix_match_id_normalize.py --dry-run    # 仅统计，不修改
  python fix_match_id_normalize.py --execute   # 执行修正（事务内，出错回滚）
"""
import sys, sqlite3, argparse
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from team_name_mapping import normalize_team_name

TIMING_TABLES = ["wdl_history", "handicap_history", "total_goals_history", "score_history"]


def build_fix_map(conn):
    """扫描所有时序表 DISTINCT match_id，构建 old→new 映射。"""
    fix_map = {}
    for tbl in TIMING_TABLES:
        rows = conn.execute(
            f"SELECT DISTINCT match_id FROM {tbl} WHERE match_id LIKE '____-__-__%'"
        ).fetchall()
        for (mid,) in rows:
            if mid in fix_map:
                continue
            parts = mid.split("_", 2)
            if len(parts) != 3:
                continue
            date, home, away = parts
            nh = normalize_team_name(home)
            na = normalize_team_name(away)
            if nh and nh != home:
                new_home = nh
            else:
                new_home = home
            if na and na != away:
                new_away = na
            else:
                new_away = away
            new_mid = f"{date}_{new_home}_{new_away}"
            if new_mid != mid:
                fix_map[mid] = new_mid
    return fix_map


def main():
    parser = argparse.ArgumentParser(description="时序表 match_id 队名归一化修正")
    parser.add_argument("--dry-run", action="store_true", help="仅统计不修改")
    parser.add_argument("--execute", action="store_true", help="执行修正")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.print_help()
        return

    conn = sqlite3.connect(str(DB))
    conn.isolation_level = None  # 手动事务

    fix_map = build_fix_map(conn)
    print(f"共发现 {len(fix_map)} 个 match_id 需归一化\n")
    print("样例（前 15 条）:")
    for i, (old, new) in enumerate(list(fix_map.items())[:15]):
        print(f"  {old}")
        print(f"  -> {new}\n")

    if args.dry_run:
        # 按表统计影响行数
        print("\n按表统计影响行数:")
        for tbl in TIMING_TABLES:
            total = 0
            for old in fix_map:
                cnt = conn.execute(
                    f"SELECT COUNT(1) FROM {tbl} WHERE match_id=?", (old,)
                ).fetchone()[0]
                total += cnt
            print(f"  {tbl}: {total} 行")
        print("\n[dry-run] 未修改任何数据。加 --execute 执行修正。")
        conn.close()
        return

    if args.execute:
        print("\n执行修正（事务内）...")
        try:
            conn.execute("BEGIN")
            total_updated = 0
            for tbl in TIMING_TABLES:
                tbl_updated = 0
                tbl_deleted = 0
                for old, new in fix_map.items():
                    # old 是否还在表中（可能已被前一个映射合并删除）
                    cnt = conn.execute(
                        f"SELECT COUNT(1) FROM {tbl} WHERE match_id=?", (old,)
                    ).fetchone()[0]
                    if cnt == 0:
                        continue
                    # 先删除 new 中和 old 有相同 timestamp 的行（避免 UNIQUE 冲突）
                    # old/new 是同一场比赛的不同命名，保留 old 的行（更新为 new）
                    del_cur = conn.execute(
                        f"DELETE FROM {tbl} WHERE match_id=? AND timestamp IN "
                        f"(SELECT timestamp FROM {tbl} WHERE match_id=?)",
                        (new, old),
                    )
                    tbl_deleted += del_cur.rowcount
                    # 更新 old→new
                    cur = conn.execute(
                        f"UPDATE {tbl} SET match_id=? WHERE match_id=?", (new, old)
                    )
                    tbl_updated += cur.rowcount
                print(f"  {tbl}: 更新 {tbl_updated} 行, 删除冲突 {tbl_deleted} 行")
                total_updated += tbl_updated
            conn.execute("COMMIT")
            print(f"\n[完成] 共更新 {total_updated} 行，涉及 {len(fix_map)} 个 match_id")
        except Exception as e:
            conn.execute("ROLLBACK")
            print(f"\n[错误] {e}，已回滚")
            raise
        finally:
            conn.close()


if __name__ == "__main__":
    main()
