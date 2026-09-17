import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

# 直接查：match_date 在 2024年8月，match_type 却标 2023-2024
print("=== 2024-Aug 日期但 match_type 标 2023-2024 ===")
rows = conn.execute("""
    SELECT match_date, match_type, home_team, away_team
    FROM matches
    WHERE match_date LIKE '2024-08%'
      AND match_type LIKE '%2023-2024%'
    LIMIT 10
""").fetchall()
print(f"数量: {len(rows)}")
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]} vs {r[3]}")

# 2024-09
print("\n=== 2024-Sep 日期但 match_type 标 2023-2024 ===")
rows = conn.execute("""
    SELECT match_date, match_type, home_team, away_team
    FROM matches
    WHERE match_date LIKE '2024-09%'
      AND match_type LIKE '%2023-2024%'
    LIMIT 5
""").fetchall()
print(f"数量: {len(rows)}")
for r in rows:
    print(f"  {r[0]} | {r[1]} | {r[2]} vs {r[3]}")

# 统计总数
print("\n=== 按 (match_date年份, match_type赛季) 交叉统计 ===")
rows = conn.execute("""
    SELECT 
        substr(match_date, 1, 4) as date_year,
        CASE 
            WHEN match_type LIKE '%2023-2024%' THEN '23/24'
            WHEN match_type LIKE '%2024-2025%' THEN '24/25'
            WHEN match_type LIKE '%2025-2026%' THEN '25/26'
            WHEN match_type LIKE '%2026-2027%' THEN '26/27'
            ELSE '其他'
        END as type_season,
        COUNT(*) as cnt
    FROM matches
    WHERE match_type != ''
    GROUP BY date_year, type_season
    ORDER BY date_year, type_season
""").fetchall()
print(f"{'日期年':>8s} | {'标签赛季':>8s} | {'数量':>6s} | 状态")
print("-" * 50)
for r in rows:
    dy = r[0]
    ts = r[1]
    cnt = r[2]
    # 判断是否正确
    if ts == '其他':
        status = "⚠️"
    elif ts == '23/24' and dy in ('2023', '2024'):
        # 2023-08 ~ 2024-06, dates in 2023 or 2024
        if dy == '2024':
            # Could be Jan-Jun 2024 (correct) or Aug-Dec 2024 (wrong)
            status = "?"
        else:
            status = "✅"
    elif ts == '24/25' and dy in ('2024', '2025'):
        if dy == '2025':
            status = "?"
        else:
            status = "✅"
    elif ts == '25/26' and dy in ('2025', '2026'):
        if dy == '2026':
            status = "?"
        else:
            status = "✅"
    elif ts == '26/27' and dy in ('2026', '2027'):
        status = "?"
    else:
        status = "❌"
    print(f"{dy:>8s} | {ts:>8s} | {cnt:>6d} | {status}")

conn.close()