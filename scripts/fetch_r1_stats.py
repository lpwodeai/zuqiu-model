# -*- coding: utf-8 -*-
"""拉取 4 场比赛 SofaScore 球队级统计，确认 key 结构与 xG/射门/控球真实值"""
import json
from curl_cffi import requests

BASE = "https://api.sofascore.com/api/v1"
EVENTS = ["16421047", "16421052", "16421061", "16421053"]

for eid in EVENTS:
    url = f"{BASE}/event/{eid}/statistics"
    r = requests.get(url, impersonate="chrome", timeout=20)
    print(f"\n{'='*70}\nEVENT {eid} | status={r.status_code}")
    if r.status_code != 200:
        print("  body:", r.text[:300])
        continue
    data = r.json()
    for period in data.get("statistics", []):
        pname = period.get("period")
        if pname != "ALL":
            continue
        for grp in period.get("groups", []):
            gname = grp.get("groupName")
            for item in grp.get("statisticsItems", []):
                k = item.get("key")
                hv = item.get("homeValue", item.get("home"))
                av = item.get("awayValue", item.get("away"))
                print(f"  [{gname}] {k}: home={hv} away={av}")