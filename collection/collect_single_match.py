"""SofaScore 单场比赛赛后数据抓取脚本。

用途：比赛结束后，针对单场 event_id 抓取/刷新赛后数据
      （阵容 + 球员统计 + 伤停 + 进球/红黄牌/换人 + 球队级统计）。

用法：
  # 基本用法（league/season 可选，默认从 DB 映射表自动推断）
  python collection/collect_single_match.py --event-id 16363633

  # 指定联赛和赛季
  python collection/collect_single_match.py --event-id 16363633 --league 英超 --season 26/27

  # 仅试跑不写库
  python collection/collect_single_match.py --event-id 16363633 --dry-run

特点：
  - 复用 final_sofascore_collector 的全部采集/解析/写库逻辑（含伤停数据）
  - 自动从 fbref_match_mapping 表推断 league/season（如果未显式指定）
  - INSERT OR REPLACE 幂等：可反复重跑刷新数据
  - 不更新进度文件：不影响批量采集的 --resume 逻辑
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# 动态定位项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from final_sofascore_collector import (
    DB_PATH,
    SofaScoreClient,
    collect_single_event,
    fetch_event_detail,
    init_db_schema_if_needed,
    setup_logging,
)


def infer_league_season(event_id: str, conn: sqlite3.Connection):
    """从 fbref_match_mapping 表自动推断联赛和赛季。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT league, season FROM fbref_match_mapping WHERE fbref_match_id=?",
        (event_id,),
    )
    row = cur.fetchone()
    if row:
        return row[0], row[1]
    return None, None


def main():
    parser = argparse.ArgumentParser(
        description="SofaScore 单场比赛赛后数据抓取",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python collection/collect_single_match.py --event-id 16363633
  python collection/collect_single_match.py --event-id 16363633 --league 英超 --season 26/27
  python collection/collect_single_match.py --event-id 16363633 --dry-run
        """,
    )
    parser.add_argument("--event-id", type=str, required=True,
                        help="SofaScore 的 event_id（如 16363633）")
    parser.add_argument("--league", type=str, default=None,
                        help="联赛名称（不指定则从 DB 映射表自动推断）")
    parser.add_argument("--season", type=str, default=None,
                        help="赛季（如 26/27，不指定则从 DB 映射表自动推断）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅采集不写库，原始 JSON 仍会落盘")

    args = parser.parse_args()
    event_id = args.event_id

    # 连接数据库（即使是 dry-run 也需要连接，用于推断 league/season）
    if not DB_PATH.exists():
        print(f"[ERROR] 数据库不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)

    # 推断 league/season
    league = args.league
    season = args.season
    if not league or not season:
        inferred_league, inferred_season = infer_league_season(event_id, conn)
        if inferred_league and inferred_season:
            league = league or inferred_league
            season = season or inferred_season
            print(f"[INFO] 从 DB 映射表推断: league={league}, season={season}")
        else:
            league = league or "英超"
            season = season or "26/27"
            print(f"[WARN] DB 映射表无此 event_id，使用默认: league={league}, season={season}")

    # 初始化日志 + schema + 客户端
    logger = setup_logging(season)
    init_db_schema_if_needed(conn, logger)
    client = SofaScoreClient(logger)

    print(f"[INFO] 开始抓取 event_id={event_id} | {league} | season={season} | dry_run={args.dry_run}")
    logger.info(f"[single-match] 开始抓取 event_id={event_id} | {league} | season={season}")

    # 先获取 event 详情（作为 event dict 传给 collect_single_event）
    event_detail = fetch_event_detail(client, event_id, logger)
    if not event_detail:
        print(f"[ERROR] 获取 event {event_id} 详情失败，可能是 event_id 错误或网络问题")
        sys.exit(1)

    # 调用 collect_single_event（内部会再次并发拉取 4 个接口，含伤停数据）
    result = collect_single_event(
        client, event_detail, league, season, conn, logger, dry_run=args.dry_run
    )

    # 输出结果摘要
    print()
    print("=" * 60)
    print("抓取结果摘要")
    print("=" * 60)
    print(f"  event_id : {result.get('event_id')}")
    print(f"  比赛     : {result.get('match')}")
    print(f"  日期     : {result.get('date')}")
    print(f"  联赛     : {result.get('league')}")
    print(f"  状态     : {result.get('status')}")
    print(f"  接口成功 : {result.get('api_success')}/4")
    counts = result.get("counts", {})
    if counts:
        print(f"  写库统计 :")
        print(f"    lineups         = {counts.get('lineups', 0)} 行")
        print(f"    player_stats    = {counts.get('player_stats', 0)} 行")
        print(f"    missing_players = {counts.get('missing_players', 0)} 行")
        print(f"    players_new     = {counts.get('players', 0)} 人")
        print(f"    mapping         = {'新增' if counts.get('mapping') else '已存在'}")
    if result.get("errors"):
        print(f"  错误     : {', '.join(result['errors'])}")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    main()
