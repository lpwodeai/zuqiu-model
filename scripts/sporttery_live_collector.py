"""
竞彩网当日比赛「赛前赔率时序」采集器（定时任务用，轻量 requests 版）
=====================================================================
功能：采集当日五大联赛未开赛（Selling）比赛的赛前赔率时序，写入 odds.db 时序表。
用途：每 2 小时运行一次，幂等累积赛前赔率随时间的变化（赔率未变动不重复写入）。

数据接口（已验证 2026-08-22）：
  - 赛程列表  GET .../getMatchListV1.qry?clientCode=3001
              返回 value.matchInfoList -> subMatchList，队名为中文全称
  - 单场赔率  GET .../getFixedBonusV1.qry?clientCode=3001&matchId={id}
              返回 value.oddsHistory（hadList/hhadList/ttgList/crsList，含 updateDate/updateTime）

写入目标：data/odds.db 的 wdl_history / handicap_history / total_goals_history / score_history
  （复用现有表结构与 match_id+timestamp 唯一约束，INSERT OR IGNORE 幂等）

match_id 格式：{YYYY-MM-DD}_{主队}_{客队}（中文名，经 TEAM_NAME_MAP 归一化）

使用：
  python scripts/sporttery_live_collector.py                # 采集今日五大联赛
  python scripts/sporttery_live_collector.py --date 2026-08-22
  python scripts/sporttery_live_collector.py --leagues 英超,西甲
  python scripts/sporttery_live_collector.py --dry-run      # 试跑不写库
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# 复用既有队名映射（同目录模块，仅标准库依赖）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sporttery_sync_to_odds import TEAM_NAME_MAP

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"

BASE_URL = "https://webapi.sporttery.cn/gateway/uniform/football"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.sporttery.cn/jc/zqszsc/",
}

LEAGUE_IDS = {"英超": "25", "意甲": "40", "西甲": "62", "德甲": "37", "法甲": "32"}
LEAGUE_ID_TO_NAME = {v: k for k, v in LEAGUE_IDS.items()}

CRS_FIELDS = {
    "s01s00": "1:0", "s02s00": "2:0", "s02s01": "2:1",
    "s03s00": "3:0", "s03s01": "3:1", "s03s02": "3:2",
    "s04s00": "4:0", "s04s01": "4:1", "s04s02": "4:2",
    "s05s00": "5:0", "s05s01": "5:1", "s05s02": "5:2",
    "-1sa": "胜其它",
    "s00s00": "0:0", "s01s01": "1:1", "s02s02": "2:2", "s03s03": "3:3",
    "-1sd": "平其它",
    "s00s01": "0:1", "s00s02": "0:2", "s01s02": "1:2",
    "s00s03": "0:3", "s01s03": "1:3", "s02s03": "2:3",
    "s00s04": "0:4", "s01s04": "1:4", "s02s04": "2:4",
    "s00s05": "0:5", "s01s05": "1:5", "s02s05": "2:5",
    "-1sh": "负其它",
}


def normalize_team(name: str) -> str:
    """竞彩网队名 -> 项目标准中文名（含别名归并）"""
    name = (name or "").strip()
    return TEAM_NAME_MAP.get(name, name)


def http_get_json(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=30)
    return r.json()


def fetch_match_list(target_date: str) -> list:
    """获取当日五大联赛「销售中（未开赛）」赛程"""
    d = http_get_json(f"{BASE_URL}/getMatchListV1.qry?clientCode=3001")
    if d.get("errorCode") != "0":
        print(f"  ⚠️ 赛程接口失败: {d.get('errorMessage')}")
        return []

    matches = []
    for mi in d.get("value", {}).get("matchInfoList", []):
        if mi.get("businessDate") != target_date:
            continue
        for s in mi.get("subMatchList", []):
            lid = str(s.get("leagueId", ""))
            if lid not in LEAGUE_ID_TO_NAME:
                continue
            if s.get("matchStatus") != "Selling":
                continue
            matches.append({
                "league": LEAGUE_ID_TO_NAME[lid],
                "sporttery_match_id": str(s.get("matchId", "")),
                "home_team": s.get("homeTeamAllName", ""),
                "away_team": s.get("awayTeamAllName", ""),
                "match_date": s.get("matchDate", mi.get("businessDate", "")),
                "match_time": s.get("matchTime", ""),
                "match_num": s.get("matchNumStr", ""),
            })
    return matches


def fetch_match_bonus(sid: str) -> dict:
    return http_get_json(f"{BASE_URL}/getFixedBonusV1.qry?clientCode=3001&matchId={sid}")


def parse_bonus_response(data: dict) -> dict:
    """解析 getFixedBonusV1 响应，提取四玩法时序（与 sporttery_collector.parse_fixed_bonus_response 等价）"""
    result = {"wdl_timing": [], "handicap_timing": [], "total_goals_timing": [], "score_timing": []}
    oh = (data.get("value") or {}).get("oddsHistory") or {}

    for item in oh.get("hadList", []):
        result["wdl_timing"].append({
            "timestamp": f"{item.get('updateDate', '')} {item.get('updateTime', '')}",
            "win_a": float(item.get("h") or 0),
            "draw": float(item.get("d") or 0),
            "win_b": float(item.get("a") or 0),
        })
    for item in oh.get("hhadList", []):
        result["handicap_timing"].append({
            "timestamp": f"{item.get('updateDate', '')} {item.get('updateTime', '')}",
            "win": float(item.get("h") or 0),
            "draw": float(item.get("d") or 0),
            "lose": float(item.get("a") or 0),
        })
    for item in oh.get("ttgList", []):
        result["total_goals_timing"].append({
            "timestamp": f"{item.get('updateDate', '')} {item.get('updateTime', '')}",
            "goals_0": float(item.get("s0") or 0),
            "goals_1": float(item.get("s1") or 0),
            "goals_2": float(item.get("s2") or 0),
            "goals_3": float(item.get("s3") or 0),
            "goals_4": float(item.get("s4") or 0),
            "goals_5": float(item.get("s5") or 0),
            "goals_6": float(item.get("s6") or 0),
            "goals_7_plus": float(item.get("s7") or 0),
        })
    for item in oh.get("crsList", []):
        ts = f"{item.get('updateDate', '')} {item.get('updateTime', '')}"
        for field, score_name in CRS_FIELDS.items():
            val = item.get(field, "0")
            if val and val != "0":
                try:
                    result["score_timing"].append({"timestamp": ts, "score": score_name, "odds": float(val)})
                except ValueError:
                    pass
    return result


def save_timing(m: dict, target_date: str) -> dict:
    """将解析出的四玩法时序写入四个时序表，返回各玩法新增行数"""
    mid = f"{target_date}_{normalize_team(m['home_team'])}_{normalize_team(m['away_team'])}"
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    t = m["timing"]
    stats = {"wdl": 0, "hcp": 0, "ttg": 0, "score": 0}

    for w in t["wdl_timing"]:
        cur.execute(
            "INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b) VALUES (?,?,?,?,?)",
            (mid, w["timestamp"], w["win_a"], w["draw"], w["win_b"]))
        stats["wdl"] += cur.rowcount

    for h in t["handicap_timing"]:
        cur.execute(
            "INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose) VALUES (?,?,?,?,?)",
            (mid, h["timestamp"], h["win"], h["draw"], h["lose"]))
        stats["hcp"] += cur.rowcount

    for g in t["total_goals_timing"]:
        cur.execute(
            "INSERT OR IGNORE INTO total_goals_history "
            "(match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, g["timestamp"], g["goals_0"], g["goals_1"], g["goals_2"], g["goals_3"],
             g["goals_4"], g["goals_5"], g["goals_6"], g["goals_7_plus"]))
        stats["ttg"] += cur.rowcount

    for sc in t["score_timing"]:
        cur.execute(
            "INSERT OR IGNORE INTO score_history (match_id, timestamp, score, odds) VALUES (?,?,?,?)",
            (mid, sc["timestamp"], sc["score"], sc["odds"]))
        stats["score"] += cur.rowcount

    conn.commit()
    conn.close()
    return stats


def run(date: str, leagues: set, dry_run: bool) -> None:
    print(f"{'='*68}")
    print(f"竞彩网当日赛前赔率时序采集 | 日期={date} | 联赛={sorted(leagues) or ['全部']}")
    print(f"{'='*68}")

    matches = fetch_match_list(date)
    if leagues:
        matches = [m for m in matches if m["league"] in leagues]

    print(f"📋 当日五大联赛未开赛赛事: {len(matches)} 场")
    if not matches:
        print("  无待采集赛事")
        return

    total = {"wdl": 0, "hcp": 0, "ttg": 0, "score": 0}
    for i, m in enumerate(matches, 1):
        print(f"  [{i}/{len(matches)}] {m['match_num']} {m['home_team']} vs {m['away_team']} "
              f"({m['league']} {m['match_time']})", end=" ")

        bonus = fetch_match_bonus(m["sporttery_match_id"])
        if not bonus or bonus.get("errorCode") != "0":
            print("❌ 接口失败")
            continue

        m["timing"] = parse_bonus_response(bonus)

        if dry_run:
            t = m["timing"]
            print(f"✅ [dry] WDL:{len(t['wdl_timing'])} HCP:{len(t['handicap_timing'])} "
                  f"TTG:{len(t['total_goals_timing'])} CRS:{len(t['score_timing'])}")
            continue

        stats = save_timing(m, date)
        for k in total:
            total[k] += stats[k]
        print(f"✅ 新增 WDL:{stats['wdl']} HCP:{stats['hcp']} TTG:{stats['ttg']} CRS:{stats['score']}")
        time.sleep(0.5)

    print(f"{'='*68}")
    if dry_run:
        print("🔍 试跑完成（未写库）")
    else:
        print(f"🎉 完成: 新增 WDL {total['wdl']} / HCP {total['hcp']} / TTG {total['ttg']} / CRS {total['score']} 行")
        print(f"💾 写入: {DB_PATH}")
    print(f"{'='*68}")


def main() -> None:
    parser = argparse.ArgumentParser(description="竞彩网当日赛前赔率时序采集器")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"),
                        help="采集日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--leagues", type=str, default=None,
                        help="联赛，逗号分隔，如 英超,西甲（默认全部五大联赛）")
    parser.add_argument("--dry-run", action="store_true", help="试跑不写库")
    args = parser.parse_args()

    leagues = set()
    if args.leagues:
        leagues = {x.strip() for x in args.leagues.split(",") if x.strip()}
        bad = leagues - set(LEAGUE_IDS)
        if bad:
            print(f"⚠️ 未知联赛: {bad}（可选: {list(LEAGUE_IDS)}）")
            sys.exit(1)

    run(args.date, leagues, args.dry_run)


if __name__ == "__main__":
    main()