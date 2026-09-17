# -*- coding: utf-8 -*-
"""补采巴伦西亚近3场缺失的 SofaScore 球员评分（rating）数据。

根因：塞维利亚vs巴伦西亚(2026-09-12)客场特征 sofa_rat_5g_away=2.797，
是因为巴伦西亚近5场中有3场（16416325/16416291/16421049）match_player_stats
缺 rating（首发11人均无评级），fillna(0) 后把加权均值从 ~7.0 拉到 ~2.8。
本脚本触发 collect_single_event 重采这3场并写库（幂等 INSERT OR REPLACE）。
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

EVENT_IDS = ["16416325", "16416291", "16421049"]
LEAGUE = "西甲"
SEASON = "25/26"


def main():
    logger = setup_logging("backfill-valencia")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    client = SofaScoreClient(logger=logger)
    try:
        init_db_schema_if_needed(conn, logger)
        for eid in EVENT_IDS:
            detail = fetch_event_detail(client, eid, logger)
            if not detail or "event" not in detail:
                print(f"[{eid}] detail 无数据，跳过")
                continue
            event = detail["event"]
            print(f"[{eid}] 开始补采: {event.get('homeTeam', {}).get('name')} vs "
                  f"{event.get('awayTeam', {}).get('name')}")
            result = collect_single_event(client, event, LEAGUE, SEASON, conn, logger, dry_run=False)
            conn.commit()
            print(f"[{eid}] 结果: status={result['status']} counts={result['counts']} "
                  f"errors={result['errors']}")
    finally:
        client.close()
        conn.close()

    # 复检
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    print("\n=== 复检 rating 覆盖 ===")
    for eid in EVENT_IDS:
        r = c.execute("""
            SELECT SUM(CASE WHEN is_starter=1 THEN 1 ELSE 0 END) AS n_starter,
                   SUM(CASE WHEN is_starter=1 AND rating>0 THEN 1 ELSE 0 END) AS n_starter_rated
            FROM match_player_stats
            WHERE stats_source='sofascore' AND fbref_match_id=?
        """, (eid,)).fetchone()
        print(eid, dict(r))
    conn.close()


if __name__ == "__main__":
    main()