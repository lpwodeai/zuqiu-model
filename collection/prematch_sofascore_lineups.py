# -*- coding: utf-8 -*-
"""赛前官方首发/伤停采集器（SofaScore）

背景：
  历史 SofaScore 链路只抓已完赛场次（训练特征用），赛前报告 §十二 的
  首发评分/可用性/缺阵影响全部由历史出场连续性「推算」，未使用官方伤停。
  实测（2026-09-17）：SofaScore 对未开赛场次的 /event/{id}/lineups 返回
  confirmed=False + 预测首发 11 人 + 官方 missingPlayers（伤病/停赛+预计复出日），
  /event/{id}/injuries 端点不存在（404）。开球前 ~1h 该接口自动转 confirmed=True。

职责（对 fbref_match_mapping 中已注册的未赛场次）：
  1. match_predicted_lineups（新表）：预测/官方首发 11 人 + 阵型 + confirmed 标记
  2. match_missing_players（复用现有写入）：官方伤停名单刷新（幂等，可反复重跑）
  3. data/sofascore_raw/{联赛}/{event_id}/prematch_lineups.json 冷存储

下游消费：
  scripts/player_injury_source.py --sync-sofascore → player_injuries（official）
  features/player_availability_features.py 对未赛场次做官方缺阵校正（pa_* 特征）

用法：
  python collection/prematch_sofascore_lineups.py                       # 今天起 2 天内
  python collection/prematch_sofascore_lineups.py --date 2026-09-18 --days-ahead 1
  python collection/prematch_sofascore_lineups.py --leagues 西甲 --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

MODEL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODEL_PROJECT_ROOT / "collection"))

from final_sofascore_collector import (  # noqa: E402
    SofaScoreClient,
    fetch_event_detail,
    fetch_event_lineups,
    parse_missing_players,
    write_missing_players_to_db,
)

DB_PATH = MODEL_PROJECT_ROOT / "data" / "odds.db"
RAW_JSON_DIR = MODEL_PROJECT_ROOT / "data" / "sofascore_raw"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prematch_lineups")

DDL_PREDICTED_LINEUPS = """
CREATE TABLE IF NOT EXISTS match_predicted_lineups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    fbref_match_id TEXT NOT NULL,
    team TEXT NOT NULL,
    side TEXT NOT NULL,
    confirmed INTEGER DEFAULT 0,
    formation TEXT,
    player_name TEXT NOT NULL,
    player_id TEXT,
    position TEXT,
    shirt_number INTEGER,
    is_starter INTEGER DEFAULT 1,
    avg_rating REAL,
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(fbref_match_id, side, player_name)
)
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(DDL_PREDICTED_LINEUPS)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pred_lineups_match "
                 "ON match_predicted_lineups(fbref_match_id)")
    conn.commit()


def discover_upcoming_events(conn: sqlite3.Connection, date_from: str,
                             date_to: str, leagues: List[str]) -> List[Dict[str, Any]]:
    """从 fbref_match_mapping 找未赛场次（SofaScore 已注册、比分空）。

    mapping 行由 final_sofascore_collector 轮次枚举时注册（home_team_cn 实存英文队名，
    odds_match_id 为 {日期}_{主EN}_{客EN}），与赛后写入键完全一致，无需队名匹配。
    """
    sql = (
        "SELECT fbref_match_id, odds_match_id, match_date, league, "
        "       home_team_cn, away_team_cn "
        "FROM fbref_match_mapping "
        "WHERE fbref_match_url LIKE '%sofascore%' "
        "  AND match_date >= ? AND match_date <= ? "
        "  AND (fbref_score IS NULL OR fbref_score = '')"
    )
    params: List[Any] = [date_from, date_to]
    if leagues:
        sql += f" AND league IN ({','.join('?' for _ in leagues)})"
        params.extend(leagues)
    sql += " ORDER BY match_date, league"
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        out.append({
            "event_id": str(r["fbref_match_id"]),
            "odds_match_id": r["odds_match_id"],
            "match_date": r["match_date"],
            "league": r["league"],
            "home_team": r["home_team_cn"] or "",
            "away_team": r["away_team_cn"] or "",
        })
    return out


def parse_predicted_players(side_data: Dict[str, Any], side: str) -> List[Dict[str, Any]]:
    """解析预测首发球员（保留 avgRating=赛季均分，供报告展示）。"""
    players = []
    for p in (side_data.get("players") or []):
        player_obj = p.get("player") or {}
        if not player_obj.get("id") or not player_obj.get("name"):
            continue
        players.append({
            "player_id": str(player_obj["id"]),
            "player_name": player_obj["name"],
            "position": p.get("position") or player_obj.get("position") or "",
            "shirt_number": p.get("shirtNumber") or player_obj.get("jerseyNumber"),
            "is_starter": int(not p.get("substitute", False)),
            "avg_rating": p.get("avgRating"),
        })
    return players


def collect_one(client: SofaScoreClient, event: Dict[str, Any],
                conn: sqlite3.Connection, dry_run: bool) -> Dict[str, int]:
    """采集单场：预测首发 + 官方伤停，写入 2 张表 + 冷存储。"""
    event_id = event["event_id"]
    counts = {"predicted": 0, "missing": 0}

    detail = fetch_event_detail(client, event_id, logger)
    if not detail or "event" not in detail:
        logger.warning(f"[event {event_id}] event detail 无数据，跳过")
        return counts
    ev = detail["event"]
    home_name = (ev.get("homeTeam") or {}).get("name") or event["home_team"]
    away_name = (ev.get("awayTeam") or {}).get("name") or event["away_team"]
    status_type = (ev.get("status") or {}).get("type", "")
    if status_type in ("finished", "ended"):
        logger.info(f"[event {event_id}] 已完赛，跳过赛前采集（赛后链路接管）")
        return counts

    lineups_data = fetch_event_lineups(client, event_id, logger)
    if not lineups_data:
        logger.info(f"[event {event_id}] lineups 无数据（可能临近开赛才生成），跳过")
        return counts

    confirmed = bool(lineups_data.get("confirmed", False))
    odds_match_id = event["odds_match_id"]

    if dry_run:
        for side, team in (("home", home_name), ("away", away_name)):
            sd = lineups_data.get(side) or {}
            mp = parse_missing_players(sd, event_id, side, logger)
            logger.info(f"[DRY] [{event_id}] {team} confirmed={confirmed} "
                        f"formation={sd.get('formation')} "
                        f"players={len(sd.get('players') or [])} missing={len(mp)}")
            counts["missing"] += len(mp)
        return counts

    cursor = conn.cursor()
    try:
        for side, team in (("home", home_name), ("away", away_name)):
            sd = lineups_data.get(side) or {}
            # 1. 预测首发 → match_predicted_lineups
            for p in parse_predicted_players(sd, side):
                cursor.execute(
                    """INSERT OR REPLACE INTO match_predicted_lineups
                       (match_id, fbref_match_id, team, side, confirmed, formation,
                        player_name, player_id, position, shirt_number, is_starter,
                        avg_rating)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (odds_match_id, event_id, team, side, int(confirmed),
                     sd.get("formation") or "", p["player_name"], p["player_id"],
                     p["position"], p["shirt_number"], p["is_starter"],
                     p["avg_rating"]),
                )
                counts["predicted"] += 1
            # 2. 官方伤停 → match_missing_players（复用赛后写入，幂等可刷新）
            mp = parse_missing_players(sd, event_id, side, logger)
            counts["missing"] += write_missing_players_to_db(
                cursor, odds_match_id, event_id, team, mp, logger
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"[event {event_id}] 写库异常已回滚: {type(e).__name__}: {e}")
        return counts

    # 3. 冷存储原始 JSON
    try:
        out_dir = RAW_JSON_DIR / event["league"] / event_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "prematch_lineups.json").write_text(
            json.dumps(lineups_data, ensure_ascii=False), encoding="utf-8")
    except OSError as oe:
        logger.warning(f"[event {event_id}] 原始 JSON 落盘失败: {oe}")

    logger.info(f"[event {event_id}] {home_name} vs {away_name} "
                f"({event['match_date']} {event['league']}) confirmed={confirmed} "
                f"predicted={counts['predicted']} missing={counts['missing']}")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="赛前官方首发/伤停采集器（SofaScore）")
    parser.add_argument("--date", default=None, help="起始日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days-ahead", type=int, default=2, help="向后覆盖天数（默认 2）")
    parser.add_argument("--leagues", default="all", help="联赛，逗号分隔（默认 all）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理场次（0=不限）")
    parser.add_argument("--dry-run", action="store_true", help="只演练不写库")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    date_from = args.date or datetime.now().strftime("%Y-%m-%d")
    date_to = (datetime.strptime(date_from, "%Y-%m-%d")
               + timedelta(days=args.days_ahead)).strftime("%Y-%m-%d")
    leagues = ([] if args.leagues == "all"
               else [x.strip() for x in args.leagues.split(",") if x.strip()])

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    events = discover_upcoming_events(conn, date_from, date_to, leagues)
    if args.limit > 0:
        events = events[:args.limit]
    logger.info(f"发现 {len(events)} 场未赛场次（{date_from} ~ {date_to}，"
                f"leagues={args.leagues}）")

    if not args.dry_run:
        ensure_tables(conn)

    client = SofaScoreClient(logger=logger)
    totals = {"predicted": 0, "missing": 0, "ok": 0, "skip": 0}
    for i, ev in enumerate(events, 1):
        counts = collect_one(client, ev, conn, args.dry_run)
        totals["predicted"] += counts["predicted"]
        totals["missing"] += counts["missing"]
        totals["ok" if (counts["predicted"] or counts["missing"]) else "skip"] += 1
        if i < len(events):
            time.sleep(0.8)  # 反爬节流
    conn.close()

    logger.info(f"完成：处理 {len(events)} 场（有效 {totals['ok']} / 跳过 {totals['skip']}）| "
                f"预测首发 {totals['predicted']} 条 | 官方伤停 {totals['missing']} 条")
    if not args.dry_run and totals["missing"] > 0:
        logger.info("下一步：python scripts/player_injury_source.py --sync-sofascore "
                    "同步到 player_injuries，再重算 features 即可生效")
    return 0


if __name__ == "__main__":
    sys.exit(main())
