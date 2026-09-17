import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://understat.com/league/La_liga/2026",
    "X-Requested-With": "XMLHttpRequest",
}
r = requests.get("https://understat.com/getLeagueData/La_liga/2026", headers=headers, timeout=30)
data = r.json()
print("顶层 keys:", list(data.keys()))

teams = data.get("teams", {})
print("球队数:", len(teams))
team_ids = list(teams.keys())
print("球队 id 列表:", team_ids)

# 看第一支球队 history 的字段
first_team = teams[team_ids[0]]
print("球队字段:", list(first_team.keys()))
hist = first_team.get("history", [])
print("history 条数:", len(hist))
if hist:
    print("history 单条字段:", list(hist[0].keys()))
    print("history 单条样本:", json.dumps(hist[0], ensure_ascii=False))

# 收集所有 match id（如果 history 里有）
match_ids = set()
for tid, t in teams.items():
    for h in t.get("history", []):
        for key in ["id", "match_id", "fid"]:
            if key in h and h[key]:
                match_ids.add(str(h[key]))
print("\n收集到的 match id 数:", len(match_ids))
print("样例:", sorted(match_ids)[:20])

# 保存
with open("data/understat_league_la_liga_2026.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)