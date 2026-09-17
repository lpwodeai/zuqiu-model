# -*- coding: utf-8 -*-
"""拉取西甲 26/27 第1轮全部比赛，确认真实队名与 event_id"""
import json
from curl_cffi import requests

BASE = "https://api.sofascore.com/api/v1"
# 西甲 uniqueTournamentId=8, 26/27 season=97268
endpoint = "/unique-tournament/8/season/97268/events/round/1"
url = BASE + endpoint
print(f"GET {url}")
r = requests.get(url, impersonate="chrome", timeout=20)
print("Status:", r.status_code)
if r.status_code != 200:
    print("Body:", r.text[:500])
    raise SystemExit(1)
data = r.json()
events = data.get("events", [])
print(f"事件数: {len(events)}\n")
for e in events:
    eid = e.get("id")
    home = e.get("homeTeam", {}).get("name")
    away = e.get("awayTeam", {}).get("name")
    hs = e.get("homeScore", {}).get("current")
    as_ = e.get("awayScore", {}).get("current")
    ts = e.get("startTimestamp")
    status = e.get("status", {}).get("description")
    print(f"event_id={eid} | {home} {hs}-{as_} {away} | ts={ts} | {status}")
    print(f"    home shortName={e.get('homeTeam',{}).get('shortName')} | away shortName={e.get('awayTeam',{}).get('shortName')}")