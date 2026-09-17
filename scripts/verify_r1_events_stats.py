# -*- coding: utf-8 -*-
"""临时核验：拉取西甲 26/27 第1轮事件列表 + 4 场比赛真实统计。

目的：确认 4 场比赛的 event_id 与队名，并补拉 16421061 / 16421053 的真实
     xG / 射门 / 控球率，消除此前编造占位值。
"""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
COLLECTION_DIR = PROJECT_DIR / "collection"
sys.path.insert(0, str(COLLECTION_DIR))

import final_sofascore_collector as fc  # noqa: E402

SEASON_CLI = "26/27"

TARGET_IDS = {"16421047", "16421052", "16421061", "16421053"}


def stat_items(stats_data, key, period="ALL"):
    """从 statistics JSON 中抽取指定 key 的 home/away 值。"""
    for period_block in stats_data.get("statistics", []):
        if period_block.get("period") != period:
            continue
        for grp in period_block.get("groups", []):
            for item in grp.get("statisticsItems", []):
                if item.get("key") == key:
                    return item.get("homeValue"), item.get("awayValue")
    return None, None


def main():
    logger = fc.setup_logging("verify-r1")
    client = fc.SofaScoreClient(logger)

    print("\n[1] 拉取第1轮事件列表...")
    events = fc.fetch_round_events(client, "西甲", SEASON_CLI, 1, logger)
    print(f"第1轮共 {len(events)} 场比赛：")
    for ev in sorted(events, key=lambda e: str(e.get('id', ''))):
        eid = str(ev.get("id", ""))
        home = (ev.get("homeTeam") or {}).get("name", "?")
        away = (ev.get("awayTeam") or {}).get("name", "?")
        hs = (ev.get("homeScore") or {}).get("current", "?")
        as_ = (ev.get("awayScore") or {}).get("current", "?")
        mark = "  <== 目标" if eid in TARGET_IDS else ""
        print(f"  id={eid:<10} {home}  {hs}-{as_}  {away}{mark}")

    # 补拉统计（专注于 2 个此前缺少真实数据的 event）
    print("\n[2] 补拉统计（ALL period）...")
    for eid in sorted(TARGET_IDS):
        print(f"\n--- event {eid} ---")
        stats = fc.fetch_event_statistics(client, eid, logger)
        if not stats:
            print("  (无统计)")
            continue
        for key in ["expectedGoals", "expectedGoalsOnTarget", "totalShotsOnGoal",
                    "shotsOnGoal", "ballPossession", "cornerKicks"]:
            h, a = stat_items(stats, key)
            print(f"  {key:<24} home={h}  away={a}")

    client.close()


if __name__ == "__main__":
    main()