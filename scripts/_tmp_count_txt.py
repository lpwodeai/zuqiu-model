# -*- coding: utf-8 -*-
"""统计各联赛 TXT 赔率文件中的比赛场数"""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

for fname in ["德甲2025-2026完整时序赔率.txt", "西甲2025-2026完整时序赔率.txt",
              "意甲2025-2026完整时序赔率.txt", "法甲2025-2026完整时序赔率.txt"]:
    fp = DATA / fname
    if not fp.exists():
        print(f"{fname}: 不存在")
        continue
    text = fp.read_text(encoding="utf-8")
    # 每场比赛以 "Regular Season" 行开头
    matches = re.findall(r'(\d{4}/\d{4} Regular Season 第\d+轮 \d{4}-\d{2}-\d{2})', text)
    print(f"{fname}: {len(matches)} 场比赛, 大小 {fp.stat().st_size/1024:.0f}KB")

# 还查一下有没有英超 25/26
for f in DATA.glob("*英超*"):
    print(f"英超相关: {f.name} ({f.stat().st_size/1024:.0f}KB)")

# 统计 odds.db 里各联赛 matches 表有 actual_score 且有 handicap 的场数
import sqlite3
c = sqlite3.connect(str(DATA / "odds.db"))
c.row_factory = sqlite3.Row
print("\n=== odds.db matches 表 25/26 完赛(有比分) + 有盘口 ===")
for lg in ['英超', '西甲', '意甲', '德甲', '法甲']:
    n = c.execute(f"""
        SELECT COUNT(*) FROM matches
        WHERE match_type LIKE '{lg}2025-2026%'
          AND actual_score IS NOT NULL AND actual_score != ''
          AND handicap IS NOT NULL
    """).fetchone()[0]
    print(f"  {lg}: {n}")
c.close()