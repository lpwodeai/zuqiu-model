# -*- coding: utf-8 -*-
"""一键修复 name_map.json，使所有映射精确匹配 matches 表"""
import sqlite3, json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"

# 1. 提取 matches 表所有英文队名
c = sqlite3.connect(str(DB))
en_teams = set()
for r in c.execute("SELECT match_id FROM matches WHERE match_id GLOB '*-*-*_*_*'").fetchall():
    parts = r[0].split("_")
    if len(parts) >= 3:
        en_teams.add(parts[1])
        en_teams.add(parts[2])
c.close()

# 2. 加载原始 name_map
with open(BASE / "assets" / "team_name_map.json", "r", encoding="utf-8") as f:
    name_map = json.load(f)

# 3. 修正每个映射
fixed = {}
for cn, en_val in name_map.items():
    en_lower = en_val.lower().strip()
    # 精确匹配
    found = None
    for en in en_teams:
        if en.lower().strip() == en_lower:
            found = en
            break
    if not found:
        # 模糊匹配
        for en in en_teams:
            el = en.lower().strip()
            if en_lower in el or el in en_lower:
                found = en
                break
    if not found:
        # 更宽松的匹配
        for en in en_teams:
            el = en.lower().strip()
            words = en_lower.split()
            if len(words) >= 2 and all(w in el for w in words):
                found = en
                break
    if found:
        fixed[cn] = found
    else:
        print(f"  无法匹配: {cn} → {en_val}")

# 4. 保存
with open(BASE / "assets" / "team_name_map.json", "w", encoding="utf-8") as f:
    json.dump(fixed, f, ensure_ascii=False, indent=2)
print(f"修正完成: {len(fixed)} 条映射 (原 {len(name_map)} 条)")

# 5. 列出未覆盖的队名
not_found = set(name_map.keys()) - set(fixed.keys())
if not_found:
    print(f"未匹配: {len(not_found)} 个")
    for cn in sorted(not_found):
        print(f"  {cn} → {name_map[cn]}")