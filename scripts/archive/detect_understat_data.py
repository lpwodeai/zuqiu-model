import requests, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://understat.com/match/30773",
    "X-Requested-With": "XMLHttpRequest",
}
url = "https://understat.com/getMatchData/30773"
r = requests.get(url, headers=headers, timeout=30)
print("status:", r.status_code)
data = r.json()
print("顶层 keys:", list(data.keys()))

# rosters 结构
rosters = data.get("rosters", {})
print("\nrosters keys:", list(rosters.keys()))
for side in ["h", "a"]:
    pl = rosters.get(side, {})
    print(f"  {side} 球员数: {len(pl)}")
    if pl:
        first = next(iter(pl.values()))
        print(f"  {side} 球员字段({len(first)}个):", list(first.keys()))

# shots 结构
shots = data.get("shots", {})
print("\nshots keys:", list(shots.keys()))
for side in ["h", "a"]:
    sh = shots.get(side, [])
    print(f"  {side} 射门数: {len(sh)}")
    if sh and isinstance(sh, list):
        first = sh[0]
        print(f"  {side} 射门字段({len(first)}个):", list(first.keys()))
        print(f"  {side} 射门样本:", {k: first[k] for k in list(first.keys())})

# 保存 sample
with open("data/understat_getMatchData_30773.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
print("\n已保存 data/understat_getMatchData_30773.json")