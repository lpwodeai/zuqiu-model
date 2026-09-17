import json, sqlite3
from pathlib import Path
from collections import Counter

BASE_DIR = Path(__file__).resolve().parent.parent  # 项目根目录（collection 的上一级）
JSON_DIR = BASE_DIR / "data" / "sporttery_collected"
DB_PATH = str(BASE_DIR / "data" / "odds.db")

all_matches = []
for fp in sorted(JSON_DIR.glob("*.json")):
    with open(fp, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    fname = fp.stem
    parts = fname.split("_")
    league = parts[0]
    for m in data:
        m["_league"] = league
    all_matches.extend(data)

print(f"JSON总比赛数: {len(all_matches)}")
lc = Counter(m["_league"] for m in all_matches)
for l, c in sorted(lc.items()):
    print(f"  {l}: {c}")

conn = sqlite3.connect(DB_PATH)
existing = set()
for row in conn.execute("SELECT DISTINCT match_id FROM wdl_history"):
    existing.add(row[0])
conn.close()

need = 0
for m in all_matches:
    mid = f"{m['match_date']}_{m['home_team']}_{m['away_team']}"
    if mid not in existing:
        need += 1

print(f"已有WDL赔率: {len(existing)} 场")
print(f"需要补充: {need} 场")
print(f"预计耗时: ~{need * 2} 秒 (~{need*2//60} 分钟)")