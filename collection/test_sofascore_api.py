# -*- coding: utf-8 -*-
"""SofaScore API 快速验证脚本"""
import json
from curl_cffi import requests

BASE = "https://api.sofascore.com/api/v1"

def fetch(endpoint, save_as):
    url = BASE + endpoint
    print(f"\n=== GET {url} ===")
    try:
        r = requests.get(url, impersonate="chrome", timeout=15)
        print(f"Status: {r.status_code}")
        if r.status_code != 200:
            print(f"Response preview: {r.text[:300]}")
            return None
        data = r.json()
        print(f"Keys: {list(data.keys())[:20]}")
        with open(save_as, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Saved -> {save_as}")
        return data
    except Exception as e:
        print(f"ERROR: {e}")
        return None

# ============== 1. 2025-08-16 英超赛程（找比赛ID）==============
matches = fetch("/sport/football/scheduled-events/2025-08-16",
                "sofascore_2025-08-16_matches.json")

# ============== 2. 五大联赛赛事列表（确认tournament id）==============
tournaments = fetch("/config/top-tournaments/football",
                    "sofascore_top_tournaments.json")

# ============== 3. 已知ID：Bournemouth vs Liverpool（示例url里id:14025013）==============
# 这个是2026年的比赛，先用另一个已有的利物浦比赛（用户提供的detail-4343074是伯恩茅斯vs利物浦）
# 从上面的scheduled-events中找到event_id再继续

# ============== 4. 如果有event_id，测试阵容、统计、换人 ============
event_id = None
if matches:
    for evt in matches.get("events", []):
        tname = evt.get("tournament", {}).get("name", "")
        home = evt.get("homeTeam", {}).get("name", "")
        away = evt.get("awayTeam", {}).get("name", "")
        if ("Premier" in tname or "英超" in tname) and \
           (("Liverpool" in home or "Liverpool" in away) and
            ("Bournemouth" in home or "Bournemouth" in away)):
            event_id = evt.get("id")
            print(f"\n>>> FOUND: {tname} {home} vs {away} => event_id={event_id}")
            break

if event_id:
    # 基础信息
    fetch(f"/event/{event_id}", f"sofascore_match_{event_id}.json")
    # 统计（控球/xG/射门/传球等）
    fetch(f"/event/{event_id}/statistics", f"sofascore_stats_{event_id}.json")
    # 阵容（首发/替补/阵型/球员位置/号码）
    fetch(f"/event/{event_id}/lineups", f"sofascore_lineups_{event_id}.json")
    # 事件流（进球/红黄牌/换人时间）
    fetch(f"/event/{event_id}/incidents", f"sofascore_incidents_{event_id}.json")
    # 球员单场统计
    fetch(f"/event/{event_id}/player/{event_id}", f"sofascore_player_event_{event_id}.json")
else:
    print("\n未找到Bournemouth-Liverpool赛事，测试任意赛事的阵容/统计接口结构...")
    # 测试固定接口
    fetch("/event/14025013", "sofascore_match_14025013.json")
    fetch("/event/14025013/statistics", "sofascore_stats_14025013.json")
    fetch("/event/14025013/lineups", "sofascore_lineups_14025013.json")
    fetch("/event/14025013/incidents", "sofascore_incidents_14025013.json")

print("\n=== 验证结束 ===")
