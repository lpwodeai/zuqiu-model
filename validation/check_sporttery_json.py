import json, os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
d = BASE_DIR / "data" / "sporttery_collected"
files = sorted(os.listdir(d))

# 统计
total_matches = 0
has_wdl = 0
has_hcp = 0
has_tg = 0
has_score = 0

for fname in files:
    fpath = os.path.join(d, fname)
    with open(fpath, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    total_matches += len(data)
    for match in data:
        if match.get("wdl_timing"):
            has_wdl += 1
        if match.get("handicap_timing"):
            has_hcp += 1
        if match.get("total_goals_timing"):
            has_tg += 1
        if match.get("score_timing"):
            has_score += 1

print(f"JSON 文件数: {len(files)}")
print(f"总比赛数: {total_matches}")
print(f"含 WDL 时序: {has_wdl} ({has_wdl*100/total_matches:.1f}%)" if total_matches > 0 else "N/A")
print(f"含 让球 时序: {has_hcp} ({has_hcp*100/total_matches:.1f}%)" if total_matches > 0 else "N/A")
print(f"含 总进球 时序: {has_tg} ({has_tg*100/total_matches:.1f}%)" if total_matches > 0 else "N/A")
print(f"含 比分 时序: {has_score} ({has_score*100/total_matches:.1f}%)" if total_matches > 0 else "N/A")

# 检查一个文件示例
fpath = os.path.join(d, files[0])
with open(fpath, "r", encoding="utf-8") as fh:
    data = json.load(fh)
print(f"\n示例文件: {files[0]}")
print(f"  比赛数: {len(data)}")
if data:
    m = data[0]
    print(f"  字段: {list(m.keys())}")
    print(f"  match_date: {m.get('match_date')}")
    print(f"  home_team: {m.get('home_team')}")
    print(f"  away_team: {m.get('away_team')}")
    wdl = m.get("wdl_timing", [])
    hcp = m.get("handicap_timing", [])
    print(f"  WDL时序点数: {len(wdl)}")
    print(f"  让球时序点数: {len(hcp)}")