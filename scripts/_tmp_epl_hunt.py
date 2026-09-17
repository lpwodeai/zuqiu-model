# -*- coding: utf-8 -*-
"""全面搜索英超赔率时序数据"""
import sqlite3, os, json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"

# 1. odds.db 中英超 match_type
print("=== 1. odds.db 英超 match_type ===")
c = sqlite3.connect(str(DATA / "odds.db"))
c.row_factory = sqlite3.Row
for p in ['%英超%', '%Premier%', '%premier%', '%epl%', '%EPL%']:
    rows = c.execute("SELECT DISTINCT match_type, COUNT(*) n FROM matches WHERE match_type LIKE ? GROUP BY match_type", (p,)).fetchall()
    for r in rows:
        print(f"  {r['match_type']:<40} {r['n']}场")

# 2. wdl_history 中英超相关 match_id 前缀
print("\n=== 2. wdl_history 英超 match_id 前缀 ===")
rows = c.execute("""
    SELECT DISTINCT substr(match_id,1,20) as pfx, COUNT(*) as n
    FROM wdl_history 
    WHERE match_id LIKE '%Arsenal%' OR match_id LIKE '%Liverpool%' OR match_id LIKE '%Man City%' 
       OR match_id LIKE '%Chelsea%' OR match_id LIKE '%Tottenham%' OR match_id LIKE '%Man Utd%'
    GROUP BY pfx ORDER BY n DESC
""").fetchall()
for r in rows:
    print(f"  {r['pfx']:<30} {r['n']}条")

# 3. handicap_history 英超
print("\n=== 3. handicap_history 英超 match_id 前缀 ===")
rows = c.execute("""
    SELECT DISTINCT substr(match_id,1,20) as pfx, COUNT(*) as n
    FROM handicap_history 
    WHERE match_id LIKE '%Arsenal%' OR match_id LIKE '%Liverpool%' OR match_id LIKE '%Man City%' 
       OR match_id LIKE '%Chelsea%' OR match_id LIKE '%Tottenham%' OR match_id LIKE '%Man Utd%'
    GROUP BY pfx ORDER BY n DESC
""").fetchall()
for r in rows:
    print(f"  {r['pfx']:<30} {r['n']}条")

# 4. total_goals_history 英超
print("\n=== 4. total_goals_history 英超 match_id 前缀 ===")
rows = c.execute("""
    SELECT DISTINCT substr(match_id,1,20) as pfx, COUNT(*) as n
    FROM total_goals_history 
    WHERE match_id LIKE '%Arsenal%' OR match_id LIKE '%Liverpool%' OR match_id LIKE '%Man City%' 
       OR match_id LIKE '%Chelsea%' OR match_id LIKE '%Tottenham%' OR match_id LIKE '%Man Utd%'
    GROUP BY pfx ORDER BY n DESC
""").fetchall()
for r in rows:
    print(f"  {r['pfx']:<30} {r['n']}条")

# 5. 英超出场次
print("\n=== 5. 英超 matches 表完赛情况 ===")
for t in ['英超2025-2026赛季', '英超2024-2025赛季', '英超2023-2024赛季', 'Premier League']:
    n = c.execute("SELECT COUNT(*) FROM matches WHERE match_type=?", (t,)).fetchone()[0]
    finish = c.execute("SELECT COUNT(*) FROM matches WHERE match_type=? AND actual_score IS NOT NULL AND actual_score!=''", (t,)).fetchone()[0]
    print(f"  {t:<30} 总{n}场, 完赛{finish}场")

c.close()

# 6. 搜索 docs 中关于赔率数据源的描述
print("\n=== 6. docs 中关于数据源的描述 ===")
import subprocess
result = subprocess.run(
    ['findstr', '/s', '/i', '/m', '赔率时序\\|英超.*赔率\\|epl.*odds\\|timing.*英超\\|时序.*采集\\|数据源.*赔率'],
    str(BASE / "docs"),
    capture_output=True, text=True, shell=True
)
lines = [l.strip() for l in result.stdout.split('\n') if l.strip()]
for l in lines[:20]:
    print(f"  {l}")

# 7. 检查 data/ 下的所有 txt 文件（按大小排序）
print("\n=== 7. data/ 下所有 txt 文件 ===")
for f in sorted(DATA.glob("*.txt"), key=lambda x: x.stat().st_size, reverse=True):
    print(f"  {f.name:<50} {f.stat().st_size/1024:.0f}KB")

# 8. 检查 data/ 下所有 xlsx 文件
print("\n=== 8. data/ 下所有 xlsx 文件 ===")
for f in sorted(DATA.glob("*.xlsx"), key=lambda x: x.stat().st_size, reverse=True):
    print(f"  {f.name:<50} {f.stat().st_size/1024:.0f}KB")