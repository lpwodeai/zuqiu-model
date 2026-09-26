import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://understat.com/league/La_liga/2026",
    "X-Requested-With": "XMLHttpRequest",
}
r = requests.get("https://understat.com/getLeagueData/La_liga/2026", headers=headers, timeout=30)
data = r.json()

dates = data.get("dates", {})
print("dates 类型:", type(dates).__name__, "数量:", len(dates))
# 看结构
if isinstance(dates, dict):
    first_key = list(dates.keys())[0] if dates else None
    print("dates 首个 key:", first_key)
    if first_key:
        v = dates[first_key]
        print("dates[key] 类型:", type(v).__name__)
        if isinstance(v, list) and v:
            print("  list 元素字段:", list(v[0].keys()) if isinstance(v[0], dict) else v[0])
            print("  list 样本:", json.dumps(v[0], ensure_ascii=False)[:400])
        elif isinstance(v, dict):
            print("  dict keys:", list(v.keys())[:10])
            sub = list(v.values())[0]
            print("  dict value 字段:", list(sub.keys()) if isinstance(sub, dict) else sub)
elif isinstance(dates, list) and dates:
    print("dates[0] 字段:", list(dates[0].keys()) if isinstance(dates[0], dict) else dates[0])

# 收集所有 match id
match_ids = set()
def walk(obj):
    if isinstance(obj, dict):
        if obj.get("id") and obj.get("h_team"):  # match 记录特征
            match_ids.add(str(obj["id"]))
        for v in obj.values():
            walk(v)
    elif isinstance(obj, list):
        for v in obj:
            walk(v)
walk(dates)
print("\ndates 中 match id 数:", len(match_ids))
print("样例:", sorted(match_ids)[:20])