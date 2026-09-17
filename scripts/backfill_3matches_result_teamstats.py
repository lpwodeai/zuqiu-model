# -*- coding: utf-8 -*-
"""
3 场已完赛比赛 · 球队级统计 + 赛果回填
========================================
数据源：现有 SofaScore 原始 JSON（data/sofascore_raw/{联赛}/{event_id}/）
用途：
  1. 将 3 场的球队级统计（xG/xGOT/射门/射正/控球/角球/犯规/黄牌/扑救/
     禁区内触球/中柱/绝佳机会）写入 five_leagues.db.matches
  2. 用 event.json 的真实比分回填 odds.db.matches 的
     actual_score / actual_wdl / actual_total_goals

3 场比赛（2026-08-22）：
  1. 西甲 16416293  Real Betis vs Real Sociedad          （皇家贝蒂斯 vs 皇家社会）
  2. 英超 16363633  Arsenal  vs Coventry City             （阿森纳   vs 考文垂）
  3. 法甲 16310922  Olympique de Marseille vs RC Strasbourg（马赛     vs 斯特拉斯堡）

幂等：teams 按 name 查找/新增；matches 按 (date,competitionId,homeTeamId,
awayTeamId) 查重后 UPDATE 或 INSERT；odds.db 按 match_id UPDATE。
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
RAW_DIR = DATA_DIR / "sofascore_raw"

ODDS_DB = DATA_DIR / "odds.db"
FL_DB = DATA_DIR / "five_leagues.db"

# ============================================================
# 3 场比赛配置
#   competition_id : five_leagues.db.competitions.id
#   team_league    : five_leagues.db.teams.league
# ============================================================
MATCHES = [
    {
        "event_id": "16416293",
        "league_dir": "西甲",
        "competition_id": 2,      # La Liga
        "team_league": "SA",
        "country": "Spain",
        "date": "2026-08-22",
        "home_en": "Real Betis", "away_en": "Real Sociedad",
        "home_cn": "皇家贝蒂斯", "away_cn": "皇家社会",
    },
    {
        "event_id": "16363633",
        "league_dir": "英超",
        "competition_id": 1,      # Premier League
        "team_league": "PL",
        "country": "England",
        "date": "2026-08-22",
        "home_en": "Arsenal", "away_en": "Coventry City",
        "home_cn": "阿森纳", "away_cn": "考文垂",
    },
    {
        "event_id": "16310922",
        "league_dir": "法甲",
        "competition_id": 5,      # Ligue 1
        "team_league": "FL1",
        "country": "France",
        "date": "2026-08-22",
        "home_en": "Olympique de Marseille", "away_en": "RC Strasbourg",
        "home_cn": "马赛", "away_cn": "斯特拉斯堡",
    },
]


def read_raw(league_dir, event_id):
    """读取现有 SofaScore 原始 JSON：event.json + statistics.json。"""
    d = RAW_DIR / league_dir / event_id
    event = json.loads((d / "event.json").read_text(encoding="utf-8"))
    stats = json.loads((d / "statistics.json").read_text(encoding="utf-8"))
    return event.get("event", event), stats


def extract_score(event):
    """从 event.json 取最终比分 (home, away)。"""
    home = event["homeScore"]["current"]
    away = event["awayScore"]["current"]
    return int(home), int(away)


def extract_team_stats(stats):
    """从 statistics.json 取 ALL period，返回 {key: (homeValue, awayValue)}。"""
    out = {}
    for period in stats.get("statistics", []):
        if period.get("period") != "ALL":
            continue
        for grp in period.get("groups", []):
            for item in grp.get("statisticsItems", []):
                key = item.get("key")
                if not key:
                    continue
                out[key] = (
                    item.get("homeValue", item.get("home")),
                    item.get("awayValue", item.get("away")),
                )
    return out


def g(stats, key, home=True):
    """安全取值（home/away），缺省 0。"""
    pair = stats.get(key)
    if pair is None:
        return 0
    return pair[0] if home else pair[1]


def wdl(home_goals, away_goals):
    if home_goals > away_goals:
        return "主胜"
    if home_goals < away_goals:
        return "客胜"
    return "平"


def match_hash(date, home_en, away_en, score):
    return hashlib.md5(f"{date}|{home_en}|{away_en}|{score}".encode()).hexdigest()


def resolve_team_id(cur, name, country, league, now):
    row = cur.execute("SELECT id FROM teams WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO teams (name, shortName, country, league, createdAt, updatedAt) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, name[:3], country, league, now, now),
    )
    return cur.lastrowid


def backfill_odds(cur, m, home_goals, away_goals, now):
    score = f"{home_goals}-{away_goals}"
    total = home_goals + away_goals
    w = wdl(home_goals, away_goals)
    match_id = f"{m['date']}_{m['home_en']}_{m['away_en']}"
    cur.execute(
        "UPDATE matches SET actual_score=?, actual_wdl=?, actual_total_goals=?, updated_at=? "
        "WHERE match_id=?",
        (score, w, total, now, match_id),
    )
    return cur.rowcount, match_id, score, w, total


def backfill_five_leagues(cur, m, home_goals, away_goals, stats, now):
    hid = resolve_team_id(cur, m["home_cn"], m["country"], m["team_league"], now)
    aid = resolve_team_id(cur, m["away_cn"], m["country"], m["team_league"], now)

    score = f"{home_goals}-{away_goals}"
    h = {  # home/away 字段值
        "homeXg": g(stats, "expectedGoals", True),
        "awayXg": g(stats, "expectedGoals", False),
        "homeXgot": g(stats, "expectedGoalsOnTarget", True),
        "awayXgot": g(stats, "expectedGoalsOnTarget", False),
        "homeBigChances": g(stats, "bigChanceCreated", True),
        "awayBigChances": g(stats, "bigChanceCreated", False),
        "homeShots": g(stats, "totalShotsOnGoal", True),       # SofaScore: totalShotsOnGoal = 总射门
        "homeShotsOnTarget": g(stats, "shotsOnGoal", True),
        "awayShots": g(stats, "totalShotsOnGoal", False),
        "awayShotsOnTarget": g(stats, "shotsOnGoal", False),
        "homePossession": g(stats, "ballPossession", True),
        "homeCorners": g(stats, "cornerKicks", True),
        "awayCorners": g(stats, "cornerKicks", False),
        "homeFouls": g(stats, "fouls", True),
        "awayFouls": g(stats, "fouls", False),
        "homeYellowCards": g(stats, "yellowCards", True),
        "awayYellowCards": g(stats, "yellowCards", False),
        "homeSaves": g(stats, "goalkeeperSaves", True),
        "awaySaves": g(stats, "goalkeeperSaves", False),
        "homeTouchesBox": g(stats, "touchesInOppBox", True),
        "awayTouchesBox": g(stats, "touchesInOppBox", False),
        "homeHitsPost": g(stats, "hitWoodwork", True),
        "awayHitsPost": g(stats, "hitWoodwork", False),
    }

    existing = cur.execute(
        "SELECT id FROM matches WHERE date=? AND competitionId=? AND homeTeamId=? AND awayTeamId=?",
        (m["date"], m["competition_id"], hid, aid),
    ).fetchone()

    if existing:
        sets = ", ".join(f"{k}=?" for k in (
            "homeGoals", "awayGoals", "homeXg", "awayXg", "homeXgot", "awayXgot",
            "homeBigChances", "awayBigChances", "homeShots", "homeShotsOnTarget",
            "awayShots", "awayShotsOnTarget", "homePossession", "homeCorners",
            "awayCorners", "homeFouls", "awayFouls", "homeYellowCards",
            "awayYellowCards", "homeSaves", "awaySaves", "homeTouchesBox",
            "awayTouchesBox", "homeHitsPost", "awayHitsPost", "matchHash", "updatedAt",
        ))
        values = [home_goals, away_goals]
        for k in ("homeXg", "awayXg", "homeXgot", "awayXgot", "homeBigChances",
                  "awayBigChances", "homeShots", "homeShotsOnTarget", "awayShots",
                  "awayShotsOnTarget", "homePossession", "homeCorners", "awayCorners",
                  "homeFouls", "awayFouls", "homeYellowCards", "awayYellowCards",
                  "homeSaves", "awaySaves", "homeTouchesBox", "awayTouchesBox",
                  "homeHitsPost", "awayHitsPost"):
            values.append(h[k])
        values.append(match_hash(m["date"], m["home_en"], m["away_en"], score))
        values.append(now)
        cur.execute(f"UPDATE matches SET {sets} WHERE id=?", (*values, existing[0]))
        action = "UPDATE"
    else:
        cur.execute(
            """INSERT INTO matches
               (date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
                homeXg, awayXg, homeXgot, awayXgot, homeBigChances, awayBigChances,
                homeXa, awayXa, homeSaves, awaySaves, homeTouchesBox, awayTouchesBox,
                homeHitsPost, awayHitsPost, homeShots, homeShotsOnTarget,
                awayShots, awayShotsOnTarget, homePossession, homeCorners,
                awayCorners, homeFouls, awayFouls, homeYellowCards, awayYellowCards,
                matchHash, createdAt, updatedAt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (m["date"], hid, aid, home_goals, away_goals, m["competition_id"],
             h["homeXg"], h["awayXg"], h["homeXgot"], h["awayXgot"],
             h["homeBigChances"], h["awayBigChances"],
             0.0, 0.0, h["homeSaves"], h["awaySaves"],
             h["homeTouchesBox"], h["awayTouchesBox"],
             h["homeHitsPost"], h["awayHitsPost"],
             h["homeShots"], h["homeShotsOnTarget"], h["awayShots"], h["awayShotsOnTarget"],
             h["homePossession"], h["homeCorners"], h["awayCorners"],
             h["homeFouls"], h["awayFouls"], h["homeYellowCards"], h["awayYellowCards"],
             match_hash(m["date"], m["home_en"], m["away_en"], score), now, now),
        )
        action = "INSERT"
    return action, hid, aid, score


def main():
    now = datetime.now().isoformat(sep=" ")
    print("=" * 76)
    print("3 场已完赛 · 球队统计 + 赛果回填（使用现有 SofaScore 原始 JSON）")
    print("=" * 76)

    oc = sqlite3.connect(str(ODDS_DB))
    ocur = oc.cursor()

    fc2 = sqlite3.connect(str(FL_DB))
    fcur = fc2.cursor()

    for m in MATCHES:
        event, stats = read_raw(m["league_dir"], m["event_id"])
        home_goals, away_goals = extract_score(event)
        ts = extract_team_stats(stats)

        # odds.db
        n1, mid, score, w, total = backfill_odds(ocur, m, home_goals, away_goals, now)
        # five_leagues.db
        action, hid, aid, _ = backfill_five_leagues(fcur, m, home_goals, away_goals, ts, now)

        print(f"\n[{m['league_dir']}] {m['home_cn']} vs {m['away_cn']}")
        print(f"  比分 {score} | WDL={w} | 总进球={total}")
        print(f"  odds.db matches   UPDATE {n1} 行 ({mid})")
        print(f"  five_leagues.db   {action}  (homeTeamId={hid}, awayTeamId={aid})")
        print(f"  xG {ts.get('expectedGoals')} | 射门 {ts.get('totalShotsOnGoal')} | "
              f"射正 {ts.get('shotsOnGoal')} | 控球 {ts.get('ballPossession')}")

    oc.commit()
    fc2.commit()
    oc.close()
    fc2.close()
    print("\n已提交。下面验证：")

    verify()


def verify():
    oc = sqlite3.connect(str(ODDS_DB))
    for r in oc.execute(
            "SELECT match_id, actual_score, actual_wdl, actual_total_goals FROM matches "
            "WHERE match_date='2026-08-22' ORDER BY match_id"):
        print("  odds.db:", r)
    oc.close()

    fc2 = sqlite3.connect(str(FL_DB))
    for r in fc2.execute(
            "SELECT competitionId, date, homeTeamId, awayTeamId, homeGoals, awayGoals, "
            "homeXg, awayXg, homeShots, homeShotsOnTarget, awayShots, homePossession "
            "FROM matches WHERE date='2026-08-22' ORDER BY competitionId"):
        print("  five_leagues.db:", r)
    fc2.close()


if __name__ == "__main__":
    main()