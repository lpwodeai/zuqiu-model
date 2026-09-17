# -*- coding: utf-8 -*-
"""验证五大联赛 + 25/26 赛季覆盖度"""
import json
from curl_cffi import requests

BASE = "https://api.sofascore.com/api/v1"

def fetch(url, timeout=15):
    try:
        r = requests.get(url, impersonate="chrome", timeout=timeout)
        if r.status_code != 200:
            return None, r.status_code
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 0

# 从已有match里拿到英超 uniqueTournament id=17, season id=76986
# 测试：获取某赛事的赛季列表，确认 25/26 存在

# === 已知五大联赛 uniqueTournament ids（根据官网） ===
# 英超 Premier League: id=17
# 西甲 La Liga: id=8
# 意甲 Serie A: id=23
# 德甲 Bundesliga: id=35
# 法甲 Ligue 1: id=34

leagues = [
    ("英超 Premier League", 17),
    ("西甲 La Liga", 8),
    ("意甲 Serie A", 23),
    ("德甲 Bundesliga", 35),
    ("法甲 Ligue 1", 34),
]

print("=== 五大联赛 25/26 赛季 + 数据标识 验证 ===\n")

results = {}
for name, tid in leagues:
    print(f"[{name}] uniqueTournamentId={tid}")
    # 1. 拿赛事信息 / uniqueTournament -> seasons
    info, code = fetch(f"{BASE}/unique-tournament/{tid}/info")
    seasons, code2 = fetch(f"{BASE}/unique-tournament/{tid}/seasons")
    
    season_2526 = None
    season_2526_id = None
    has_player_stats = None
    
    if info:
        ut = info.get("uniqueTournament", {})
        has_player_stats = ut.get("hasEventPlayerStatistics")
        print(f"  赛事Info: userCount={ut.get('userCount')}, hasEventPlayerStatistics={has_player_stats}")
    
    if seasons:
        s_list = seasons.get("seasons", [])
        # 列最近4个赛季
        print(f"  最近赛季:")
        for s in s_list[:8]:
            sname = s.get("name")
            sid = s.get("id")
            year = s.get("year")
            mark = " <== 目标赛季!" if "25/26" in str(year) or "25/26" in str(sname) else ""
            print(f"    - {sname} (year={year}, id={sid}){mark}")
            if not season_2526_id and ("25/26" in str(year) or "25/26" in str(sname)):
                season_2526_id = sid
                season_2526 = sname
    
    # 2. 如果有25/26赛季id，取standings + 任意一场比赛事件+球员统计确认
    has_lineups = None
    has_player_fields_sample = None
    sample_match_id = None
    
    if season_2526_id:
        # 拿赛事的event列表（取某轮/事件）
        # 先从 standings 里拿一轮events
        rounds, rc = fetch(f"{BASE}/unique-tournament/{tid}/season/{season_2526_id}/rounds")
        if rounds:
            rlist = rounds.get("rounds", [])
            if rlist:
                first_round = rlist[0]
                rid = first_round.get("round")
                events_url = f"{BASE}/unique-tournament/{tid}/season/{season_2526_id}/events/round/{rid}"
                events, ec = fetch(events_url)
                if events and events.get("events"):
                    e = events["events"][0]
                    sample_match_id = e.get("id")
                    ht = e.get("homeTeam", {}).get("name")
                    at = e.get("awayTeam", {}).get("name")
                    print(f"  第{rid}轮示例赛: {ht} vs {at} (id={sample_match_id})")
                    
                    # 拿该场 lineups
                    lu, lc = fetch(f"{BASE}/event/{sample_match_id}/lineups")
                    if lu and lu.get("confirmed") and lu.get("home", {}).get("players"):
                        ps = lu["home"]["players"]
                        # 找首发中带statistics的
                        for p in ps:
                            if not p.get("substitute") and p.get("statistics"):
                                has_player_fields_sample = list(p["statistics"].keys())
                                break
                        has_lineups = True
                        print(f"  阵容数据: OK (home球员数={len(ps)}, confirmed={lu.get('confirmed')})")
                        print(f"  home formation: {lu.get('home', {}).get('formation')}")
                        if has_player_fields_sample:
                            print(f"  单场球员统计字段数(样例): {len(has_player_fields_sample)}")
    
    results[name] = {
        "uniqueTournamentId": tid,
        "season_2526_id": season_2526_id,
        "season_2526_name": season_2526,
        "hasEventPlayerStatistics": has_player_stats,
        "sample_match_id": sample_match_id,
        "has_lineups_confirmed": has_lineups,
        "per_player_stat_fields": has_player_fields_sample,
    }

print("\n\n========== 五大联赛总览结果 ==========\n")
with open("sofascore_5_leagues_check.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
for name, r in results.items():
    ok = "✅" if r["season_2526_id"] and r["has_lineups_confirmed"] and r["per_player_stat_fields"] else "⚠️"
    fields_count = len(r["per_player_stat_fields"] or [])
    print(f"{ok} {name}: 25/26赛季ID={r['season_2526_id']}, 球员统计={r['hasEventPlayerStatistics']}, 单场字段数≈{fields_count}, 示例match_id={r['sample_match_id']}")

print("\nSaved -> sofascore_5_leagues_check.json")
