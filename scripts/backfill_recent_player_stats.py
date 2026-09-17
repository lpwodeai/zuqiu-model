# -*- coding: utf-8 -*-
"""补采其余 3 场赛前报告所涉 6 支球队近期缺失的 SofaScore 球员统计（16 场）。

根因：2026-27 新赛季前几轮（08-22~09-07）的 match_player_stats 采集大面积缺失
（无行 或 有行但 rating 全缺），导致「近5场」实际退化为「上赛季最后几场」，
赛前实力/状态评分存在滞后与 fillna(0) 轻微拉低。本脚本按 fbref_match_mapping
的 league/season 逐场 collect_single_event 重采（INSERT OR REPLACE 幂等）。
"""
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型")
sys.path.insert(0, str(PROJECT / "collection"))

from final_sofascore_collector import (
    SofaScoreClient,
    collect_single_event,
    fetch_event_detail,
    setup_logging,
    init_db_schema_if_needed,
    DB_PATH,
)

EVENT_IDS = [
    "16310922", "16283047", "16311125", "16283045",  # 08-22~08-25（有行但 rating 缺/部分缺）
    "16434026", "16284980", "16434039", "16284973",  # 08-29~08-30
    "16310935", "16310933",                            # 08-30~08-31
    "16434042", "16284999", "16434032", "16285000",  # 09-05~09-06
    "16310937",                                        # 09-06
    "16310941",                                        # 09-07
]


def main():
    logger = setup_logging("backfill-recent-6team")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    client = SofaScoreClient(logger=logger)
    ok, fail = 0, 0
    try:
        init_db_schema_if_needed(conn, logger)
        for eid in EVENT_IDS:
            meta = conn.execute(
                "SELECT league, season FROM fbref_match_mapping WHERE fbref_match_id=?",
                (eid,),
            ).fetchone()
            league = meta["league"] if meta and meta["league"] else "未知"
            season = meta["season"] if meta and meta["season"] else "26/27"

            detail = fetch_event_detail(client, eid, logger)
            if not detail or "event" not in detail:
                print(f"[{eid}] 无 detail，跳过")
                fail += 1
                continue
            event = detail["event"]
            print(f"[{eid}] {league}/{season} "
                  f"{event.get('homeTeam', {}).get('name')} vs {event.get('awayTeam', {}).get('name')}")
            try:
                result = collect_single_event(client, event, league, season, conn, logger, dry_run=False)
                conn.commit()
                ok += 1
                print(f"       -> {result['status']} counts={result['counts']}")
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                fail += 1
                print(f"       -> 失败: {e}")
    finally:
        client.close()
        conn.close()

    print(f"\n补采完成: 成功 {ok} / 失败 {fail} / 共 {len(EVENT_IDS)}")


if __name__ == "__main__":
    main()