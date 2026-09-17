# -*- coding: utf-8 -*-
"""
西甲 2026-2027 第1轮 · 真实 SofaScore 数据重新回填
====================================================
用 SofaScore 真实赛后数据重新回填 4 场比赛，修正：
  1. 队名错误（R. Racing Club → 桑坦德竞技；Vallecano → Rayo Vallecano；补齐官方全名）
  2. xG / xGOT / 射门 / 控球率 等占位值 → 真实 SofaScore 球队级统计
  3. model_predictions 的 probability / confidence 占位 0.0 → NULL

数据源：SofaScore 公共 JSON API（final_sofascore_collector.py 复用）
落库：
  odds.db      : matches / model_predictions / fbref_match_mapping /
                 match_lineups / match_player_stats / fbref_players
  five_leagues.db : teams / matches（球队级统计）

4 场比赛（event_id 已通过 /unique-tournament/8/season/97268/events/round/1 核实）：
  1. 16421047 Deportivo Alavés 3-0 Getafe          (2026-08-16)
  2. 16421052 Sevilla 2-1 Rayo Vallecano           (2026-08-16)
  3. 16421061 Real Racing Club 2-2 Villarreal      (2026-08-16)
  4. 16421053 Espanyol 3-0 Levante UD              (2026-08-17)
"""

import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
COLLECTION_DIR = PROJECT_DIR / "collection"
sys.path.insert(0, str(COLLECTION_DIR))

import final_sofascore_collector as fc  # noqa: E402

ODDS_DB = PROJECT_DIR / "data" / "odds.db"
FL_DB = PROJECT_DIR / "data" / "five_leagues.db"

SEASON_DB = "2026-2027"   # 数据库 season 字段统一用 2026-2027
SEASON_CLI = "26/27"      # API 赛季 key
COMPETITION_ID = 4        # La Liga

# ============================================================
# 4 场比赛 · 真实数据（event_id / 官方全名 / 中文名 / 赛果 / 真实球队统计）
# 球队统计均来自 SofaScore /event/{id}/statistics 的 ALL period
# ============================================================
MATCHES = [
    {
        "event_id": "16421047",
        "home": "Deportivo Alavés", "away": "Getafe",
        "home_cn": "阿拉维斯", "away_cn": "赫塔菲",
        "date": "2026-08-16", "score": "3-0",
        "home_goals": 3, "away_goals": 0,
        "wdl": "主胜", "prediction": "主胜", "handicap": -1, "handicap_result": "胜", "total_goals": 3,
        "xg": (1.93, 0.24), "xgot": (2.35, 0.09), "bigchance": (3, 0),
        "shots": (18, 6), "sot": (8, 2), "possession": (52, 48),
        "corners": (5, 3), "fouls": (16, 13), "yc": (4, 3),
        "saves": (2, 5), "touches_box": (20, 9), "hits_post": (0, 0),
    },
    {
        "event_id": "16421052",
        "home": "Sevilla", "away": "Rayo Vallecano",
        "home_cn": "塞维利亚", "away_cn": "巴列卡诺",
        "date": "2026-08-16", "score": "2-1",
        "home_goals": 2, "away_goals": 1,
        "wdl": "主胜", "prediction": "主胜", "handicap": -1, "handicap_result": "平", "total_goals": 3,
        "xg": (2.19, 1.89), "xgot": (1.80, 1.77), "bigchance": (2, 3),
        "shots": (13, 6), "sot": (4, 3), "possession": (49, 51),
        "corners": (1, 2), "fouls": (18, 18), "yc": (4, 4),
        "saves": (2, 2), "touches_box": (28, 13), "hits_post": (0, 0),
    },
    {
        "event_id": "16421061",
        "home": "Real Racing Club", "away": "Villarreal",
        "home_cn": "桑坦德竞技", "away_cn": "比利亚雷亚尔",
        "date": "2026-08-16", "score": "2-2",
        "home_goals": 2, "away_goals": 2,
        "wdl": "平", "prediction": "客胜", "handicap": 1, "handicap_result": "胜", "total_goals": 4,
        "xg": (1.87, 0.67), "xgot": (1.39, 1.42), "bigchance": (2, 1),
        "shots": (17, 12), "sot": (3, 7), "possession": (41, 59),
        "corners": (6, 10), "fouls": (14, 14), "yc": (2, 4),
        "saves": (5, 3), "touches_box": (36, 49), "hits_post": (0, 0),
    },
    {
        "event_id": "16421053",
        "home": "Espanyol", "away": "Levante UD",
        "home_cn": "西班牙人", "away_cn": "莱万特",
        "date": "2026-08-17", "score": "3-0",
        "home_goals": 3, "away_goals": 0,
        "wdl": "主胜", "prediction": "主胜", "handicap": -1, "handicap_result": "胜", "total_goals": 3,
        "xg": (1.47, 0.19), "xgot": (2.27, 0.0), "bigchance": (2, 0),
        "shots": (16, 4), "sot": (6, 0), "possession": (54, 46),
        "corners": (0, 1), "fouls": (17, 7), "yc": (3, 0),
        "saves": (0, 3), "touches_box": (20, 5), "hits_post": (0, 0),
    },
]


def odds_match_id(m):
    """与 final_sofascore_collector.build_odds_match_id 保持一致（官方全名）。"""
    return fc.build_odds_match_id(m["date"], m["home"], m["away"])


def match_hash(m):
    return hashlib.md5(f"{m['date']}|{m['home']}|{m['away']}|{m['score']}".encode()).hexdigest()


# ============================================================
# Part 0 · 清理此前回填的错误占位数据
# ============================================================
def cleanup():
    old_ids = [
        "2026-08-16_Alaves_Getafe",
        "2026-08-16_Sevilla_Vallecano",
        "2026-08-16_R. Racing Club_Villarreal",
        "2026-08-17_Espanyol_Levante",
    ]
    q = ", ".join(f"'{i}'" for i in old_ids)

    # --- odds.db ---
    oc = sqlite3.connect(str(ODDS_DB))
    ocur = oc.cursor()
    ocur.execute(f"DELETE FROM matches WHERE match_id IN ({q})")
    print(f"[odds.db] 删除 matches: {ocur.rowcount} 条")
    ocur.execute(f"DELETE FROM model_predictions WHERE match_id IN ({q})")
    print(f"[odds.db] 删除 model_predictions: {ocur.rowcount} 条")
    ocur.execute("DELETE FROM fbref_match_mapping WHERE league='西甲' "
                 "AND season=? AND match_date >= '2026-08-16'", (SEASON_DB,))
    print(f"[odds.db] 删除 fbref_match_mapping: {ocur.rowcount} 条")
    # 幂等：清掉 4 个 event_id 的旧阵容/球员统计（若存在）
    ev_ids = ", ".join(f"'{m['event_id']}'" for m in MATCHES)
    ocur.execute(f"DELETE FROM match_lineups WHERE fbref_match_id IN ({ev_ids})")
    print(f"[odds.db] 删除 match_lineups: {ocur.rowcount} 条")
    ocur.execute(f"DELETE FROM match_player_stats WHERE fbref_match_id IN ({ev_ids})")
    print(f"[odds.db] 删除 match_player_stats: {ocur.rowcount} 条")
    oc.commit()
    oc.close()

    # --- five_leagues.db ---
    fc2 = sqlite3.connect(str(FL_DB))
    fcur = fc2.cursor()
    fcur.execute("DELETE FROM matches WHERE competitionId=? AND date IN ('2026-08-16','2026-08-17')",
                 (COMPETITION_ID,))
    print(f"[five_leagues.db] 删除 matches: {fcur.rowcount} 条")
    # 修正队名：错误地把「Real Racing Club」写成「拉科鲁尼亚」
    fcur.execute("UPDATE teams SET name='桑坦德竞技', shortName='桑坦德' "
                 "WHERE name='拉科鲁尼亚' AND league='LL'")
    print(f"[five_leagues.db] 修正球队名(拉科鲁尼亚→桑坦德竞技): {fcur.rowcount} 条")
    fc2.commit()
    fc2.close()


# ============================================================
# Part 1 · 采集 4 场比赛真实 SofaScore 阵容/球员统计/球员注册
# ============================================================
def collect_player_tables(logger):
    client = fc.SofaScoreClient(logger)
    conn = sqlite3.connect(str(ODDS_DB), check_same_thread=False)
    fc.init_db_schema_if_needed(conn, logger)

    # 拉取第1轮完整事件列表，取我们需要的 4 场
    events = fc.fetch_round_events(client, "西甲", SEASON_CLI, 1, logger)
    target_ids = {m["event_id"] for m in MATCHES}
    matched = [e for e in events if str(e.get("id", "")) in target_ids]

    if len(matched) != 4:
        logger.warning(f"期望 4 场，实际匹配到 {len(matched)} 场")

    results = []
    for ev in matched:
        eid = str(ev.get("id", ""))
        logger.info(f"=== 采集 event {eid} ===")
        r = fc.collect_single_event(client, ev, "西甲", SEASON_DB, conn, logger, dry_run=False)
        results.append(r)

    client.close()
    conn.close()
    return results


# ============================================================
# Part 2 · 修正比赛级数据（matches / model_predictions / 映射中文名 / 球队统计）
# ============================================================
def resolve_team_id(cur, cn_name):
    row = cur.execute("SELECT id FROM teams WHERE name=?", (cn_name,)).fetchone()
    return row[0] if row else None


def fix_match_level(logger):
    now = datetime.now().isoformat()

    # --- odds.db: matches / model_predictions / fbref_match_mapping ---
    oc = sqlite3.connect(str(ODDS_DB))
    ocur = oc.cursor()

    for m in MATCHES:
        mid = odds_match_id(m)
        ocur.execute(
            """INSERT INTO matches
               (match_id, home_team, away_team, match_date, match_type,
                handicap, actual_wdl, actual_handicap, actual_score, actual_total_goals,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, m["home"], m["away"], m["date"], "西甲2026-2027赛季",
             m["handicap"], m["wdl"], m["handicap_result"], m["score"], m["total_goals"],
             now, now),
        )
        # model_predictions：probability / confidence 不存在的真实值 → NULL
        ocur.execute(
            """INSERT INTO model_predictions
               (match_id, model_name, prediction_type, prediction, probability, confidence, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mid, "generate_unified_report_v2.0", "WDL", m["prediction"], None, None, now),
        )
        # fbref_match_mapping：补中文名、统一比分格式
        ocur.execute(
            """UPDATE fbref_match_mapping
               SET home_team_cn=?, away_team_cn=?, fbref_score=?
               WHERE fbref_match_id=? AND league='西甲' AND season=?""",
            (m["home_cn"], m["away_cn"], m["score"], m["event_id"], SEASON_DB),
        )
    oc.commit()
    oc.close()

    # --- five_leagues.db: teams 补齐 + matches 真实统计 ---
    fc2 = sqlite3.connect(str(FL_DB))
    fcur = fc2.cursor()

    # 确保 8 支球队存在（全用正确中文名）
    teams = []
    for m in MATCHES:
        teams.append((m["home"], m["home_cn"]))
        teams.append((m["away"], m["away_cn"]))
    team_ids = {}
    for en, cn in teams:
        rid = resolve_team_id(fcur, cn)
        if rid is None:
            fcur.execute(
                "INSERT INTO teams (name, shortName, country, league, createdAt, updatedAt) "
                "VALUES (?, ?, 'Spain', 'LL', ?, ?)",
                (cn, cn[:3], now, now),
            )
            rid = fcur.lastrowid
            print(f"[five_leagues.db] 新增球队: {cn} (id={rid})")
        team_ids[en] = rid

    for m in MATCHES:
        hid = team_ids[m["home"]]
        aid = team_ids[m["away"]]
        fcur.execute(
            """INSERT INTO matches
               (date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
                homeXg, awayXg, homeXgot, awayXgot, homeBigChances, awayBigChances,
                homeXa, awayXa, homeSaves, awaySaves, homeTouchesBox, awayTouchesBox,
                homeHitsPost, awayHitsPost, homeShots, homeShotsOnTarget,
                awayShots, awayShotsOnTarget, homePossession, homeCorners,
                awayCorners, homeFouls, awayFouls, homeYellowCards, awayYellowCards,
                matchHash, createdAt, updatedAt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m["date"], hid, aid, m["home_goals"], m["away_goals"], COMPETITION_ID,
             m["xg"][0], m["xg"][1], m["xgot"][0], m["xgot"][1],
             m["bigchance"][0], m["bigchance"][1],
             0.0, 0.0, m["saves"][0], m["saves"][1],
             m["touches_box"][0], m["touches_box"][1],
             m["hits_post"][0], m["hits_post"][1],
             m["shots"][0], m["sot"][0], m["shots"][1], m["sot"][1],
             m["possession"][0], m["corners"][0], m["corners"][1],
             m["fouls"][0], m["fouls"][1], m["yc"][0], m["yc"][1],
             match_hash(m), now, now),
        )
    fc2.commit()
    fc2.close()
    print(f"[five_leagues.db] 回填 matches: {len(MATCHES)} 条（真实统计）")


# ============================================================
# 验证
# ============================================================
def verify():
    print("\n" + "=" * 70)
    print("=== 回填结果验证 ===")
    print("=" * 70)

    oc = sqlite3.connect(str(ODDS_DB))
    ocur = oc.cursor()
    print("\n--- odds.db matches ---")
    for r in ocur.execute("SELECT match_id, home_team, away_team, actual_score, actual_wdl "
                          "FROM matches WHERE match_date >= '2026-08-16' ORDER BY match_date"):
        print("  ", r)
    print("\n--- odds.db model_predictions (probability/confidence 应均为 None) ---")
    for r in ocur.execute("SELECT match_id, prediction, probability, confidence "
                          "FROM model_predictions WHERE match_id LIKE '2026-08-1%'"):
        print("  ", r)
    print("\n--- odds.db fbref_match_mapping (队名/中文名/event_id) ---")
    for r in ocur.execute("SELECT fbref_match_id, home_team_fbref, away_team_fbref, "
                          "home_team_cn, away_team_cn, fbref_score "
                          "FROM fbref_match_mapping WHERE league='西甲' AND season=? "
                          "AND match_date >= '2026-08-16'", (SEASON_DB,)):
        print("  ", r)
    print("\n--- odds.db match_lineups / match_player_stats 计数（按 event_id） ---")
    for m in MATCHES:
        l = ocur.execute("SELECT COUNT(*) FROM match_lineups WHERE fbref_match_id=?",
                         (m["event_id"],)).fetchone()[0]
        p = ocur.execute("SELECT COUNT(*) FROM match_player_stats WHERE fbref_match_id=?",
                         (m["event_id"],)).fetchone()[0]
        print(f"  {m['event_id']} {m['home']} vs {m['away']}: lineups={l} player_stats={p}")
    print("\n--- odds.db fbref_players 新增（近 4 场 first_seen_match） ---")
    for r in ocur.execute(
            "SELECT player_name_en, primary_team_cn, primary_position, first_seen_match "
            "FROM fbref_players WHERE first_seen_match >= '2026-08-16' ORDER BY first_seen_match LIMIT 10"):
        print("  ", r)
    oc.close()

    fc2 = sqlite3.connect(str(FL_DB))
    fcur = fc2.cursor()
    print("\n--- five_leagues.db teams（桑坦德竞技应替换拉科鲁尼亚） ---")
    for r in fcur.execute("SELECT id, name, shortName, league FROM teams "
                          "WHERE name IN ('桑坦德竞技','拉科鲁尼亚','巴列卡诺')"):
        print("  ", r)
    print("\n--- five_leagues.db matches（真实 xG/射门/控球） ---")
    for r in fcur.execute(
            "SELECT date, homeTeamId, awayTeamId, homeGoals, awayGoals, "
            "homeXg, awayXg, homeShots, awayShots, homePossession "
            "FROM matches WHERE competitionId=? AND date >= '2026-08-16' ORDER BY date", (COMPETITION_ID,)):
        print("  ", r)
    fc2.close()


def main():
    print("=" * 70)
    print("西甲第1轮 · 真实 SofaScore 数据重新回填")
    print("=" * 70)
    for m in MATCHES:
        print(f"  {m['event_id']}  {m['home']} vs {m['away']}  ({m['score']})")

    logger = fc.setup_logging("26-27-r1-rebackfill")

    print("\n[1/3] 清理旧占位数据...")
    cleanup()

    print("\n[2/3] 采集真实 SofaScore 阵容/球员统计...")
    results = collect_player_tables(logger)
    for r in results:
        print(f"  event {r.get('event_id')} | status={r.get('status')} | "
              f"lineups={r.get('counts', {}).get('lineups', 0)} "
              f"player_stats={r.get('counts', {}).get('player_stats', 0)} "
              f"players_new={r.get('counts', {}).get('players', 0)}")

    print("\n[3/3] 修正比赛级数据（队名/xG/概率置信度）...")
    fix_match_level(logger)

    verify()


if __name__ == "__main__":
    main()