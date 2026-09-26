import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://understat.com/league/La_liga/2026",
    "X-Requested-With": "XMLHttpRequest",
}
r = requests.get("https://understat.com/getLeagueData/La_liga/2026", headers=headers, timeout=30)
data = r.json()
dates = data.get("dates", [])

print("总比赛数:", len(dates))
print("\ndates[0] 完整:", json.dumps(dates[0], ensure_ascii=False))
print("dates[1] 完整:", json.dumps(dates[1], ensure_ascii=False))

# 收集 match id + 轮次信息（按日期分组）
from collections import defaultdict
by_date = defaultdict(list)
for d in dates:
    by_date[d["datetime"].split(" ")[0]].append(d)

print("\n按日期分组:")
for date in sorted(by_date.keys()):
    ms = by_date[date]
    ids = [m["id"] for m in ms]
    print(f"  {date}: {len(ms)} 场, ids={ids}")

# 完整 match id 列表
all_ids = [d["id"] for d in dates]
print(f"\n全赛季 match id 总数: {len(all_ids)}")
print("前20:", all_ids[:20])