"""导出 odds.db 数据到 Excel"""
import sqlite3
import os
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
except ImportError:
    os.system("pip install openpyxl -q")
    import openpyxl

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
OUTPUT = BASE_DIR / "data" / "odds_data_export_v2.xlsx"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

wb = openpyxl.Workbook()

# ============ Sheet 1: 比赛总览 ============
ws1 = wb.active
ws1.title = "比赛总览"

headers1 = ["赛季", "联赛", "比赛数", "WDL时序行数", "场均WDL时间点", "让球时序行数", "场均让球时间点", "总进球时序行数", "场均总进球时间点"]
ws1.append(headers1)

c.execute("""
    SELECT m.match_type,
           COUNT(DISTINCT m.match_id) as matches,
           COUNT(w.id) as wdl_rows,
           ROUND(CAST(COUNT(w.id) AS FLOAT)/COUNT(DISTINCT m.match_id), 1) as avg_wdl,
           COUNT(h.id) as hcp_rows,
           ROUND(CAST(COUNT(h.id) AS FLOAT)/COUNT(DISTINCT m.match_id), 1) as avg_hcp,
           COUNT(tg.id) as tg_rows,
           ROUND(CAST(COUNT(tg.id) AS FLOAT)/COUNT(DISTINCT m.match_id), 1) as avg_tg
    FROM matches m
    LEFT JOIN wdl_history w ON m.match_id = w.match_id
    LEFT JOIN handicap_history h ON m.match_id = h.match_id
    LEFT JOIN total_goals_history tg ON m.match_id = tg.match_id
    GROUP BY m.match_type
    ORDER BY m.match_type
""")

for row in c.fetchall():
    match_type = row[0]
    parts = match_type.replace("赛季", "").split("202")
    league = parts[0]
    season = "202" + parts[1] + "赛季" if len(parts) > 1 else match_type
    ws1.append([season, league] + list(row[1:]))

# 总计行
c.execute("SELECT COUNT(*) FROM matches")
total_m = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM wdl_history")
total_w = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM handicap_history")
total_h = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM total_goals_history")
total_tg = c.fetchone()[0]
ws1.append(["合计", "", total_m, total_w, round(total_w/total_m, 1), total_h, round(total_h/total_m, 1), total_tg, round(total_tg/total_m, 1)])

# ============ Sheet 2: 比赛明细 ============
ws2 = wb.create_sheet("比赛明细")

headers2 = ["id", "match_id", "主队", "客队", "比赛日期", "match_type", "让球盘", "赛果(WDL)", "让球赛果", "比分", "总进球", "创建时间"]
ws2.append(headers2)

c.execute("SELECT id, match_id, home_team, away_team, match_date, match_type, handicap, actual_wdl, actual_handicap, actual_score, actual_total_goals, created_at FROM matches ORDER BY match_date, match_type")
for row in c.fetchall():
    ws2.append(list(row))

# ============ Sheet 3: WDL时序赔率(样例) ============
ws3 = wb.create_sheet("WDL时序赔率_样例")

c.execute("""
    SELECT w.match_id, m.home_team, m.away_team, m.match_date, m.match_type,
           w.timestamp, w.win_a, w.draw, w.win_b
    FROM wdl_history w
    JOIN matches m ON w.match_id = m.match_id
    ORDER BY m.match_date, w.match_id, w.timestamp
    LIMIT 5000
""")
ws3.append(["match_id", "主队", "客队", "比赛日期", "match_type", "时间戳", "主胜赔率", "平局赔率", "客胜赔率"])
for row in c.fetchall():
    ws3.append(list(row))

# ============ Sheet 4: 让球时序赔率(样例) ============
ws4 = wb.create_sheet("让球时序赔率_样例")

c.execute("""
    SELECT h.match_id, m.home_team, m.away_team, m.match_date, m.match_type,
           h.timestamp, h.hcp_win, h.hcp_draw, h.hcp_lose
    FROM handicap_history h
    JOIN matches m ON h.match_id = m.match_id
    ORDER BY m.match_date, h.match_id, h.timestamp
    LIMIT 5000
""")
ws4.append(["match_id", "主队", "客队", "比赛日期", "match_type", "时间戳", "让球主胜赔率", "让球平局赔率", "让球客胜赔率"])
for row in c.fetchall():
    ws4.append(list(row))

# ============ Sheet 5: 总进球时序赔率(样例) ============
ws5 = wb.create_sheet("总进球时序赔率_样例")

c.execute("""
    SELECT tg.match_id, m.home_team, m.away_team, m.match_date, m.match_type,
           tg.timestamp, tg.goals_0, tg.goals_1, tg.goals_2, tg.goals_3, tg.goals_4, tg.goals_5, tg.goals_6, tg.goals_7_plus
    FROM total_goals_history tg
    JOIN matches m ON tg.match_id = m.match_id
    ORDER BY m.match_date, tg.match_id, tg.timestamp
    LIMIT 5000
""")
ws5.append(["match_id", "主队", "客队", "比赛日期", "match_type", "时间戳", "0球", "1球", "2球", "3球", "4球", "5球", "6球", "7+球"])
for row in c.fetchall():
    ws5.append(list(row))

# ============ Sheet 5b: 比分赔率时序(样例) ============
ws5b = wb.create_sheet("比分赔率时序_样例")

c.execute("""
    SELECT s.match_id, m.home_team, m.away_team, m.match_date, m.match_type,
           s.timestamp, s.score, s.odds
    FROM score_history s
    JOIN matches m ON s.match_id = m.match_id
    ORDER BY m.match_date, s.match_id, s.timestamp, s.score
    LIMIT 5000
""")
ws5b.append(["match_id", "主队", "客队", "比赛日期", "match_type", "时间戳", "比分", "赔率"])
for row in c.fetchall():
    ws5b.append(list(row))

# ============ Sheet 6: WDL时间点分布 ============
ws6 = wb.create_sheet("WDL时间点分布")

c.execute("""
    SELECT m.match_type,
           CASE WHEN cnt = 1 THEN '1个(仅终盘)'
                WHEN cnt <= 3 THEN '2-3个'
                WHEN cnt <= 5 THEN '4-5个'
                WHEN cnt <= 10 THEN '6-10个'
                ELSE '10+个' END as bucket,
           COUNT(*) as match_count
    FROM (
        SELECT w.match_id, COUNT(*) as cnt
        FROM wdl_history w
        GROUP BY w.match_id
    ) sub
    JOIN matches m ON sub.match_id = m.match_id
    GROUP BY m.match_type, bucket
    ORDER BY m.match_type, MIN(sub.cnt)
""")
ws6.append(["match_type", "时间点区间", "比赛数"])
for row in c.fetchall():
    ws6.append(list(row))

# ============ 格式化 ============
for ws in [ws1, ws2, ws3, ws4, ws5, ws5b, ws6]:
    for cell in ws[1]:
        cell.font = openpyxl.styles.Font(bold=True)
        cell.fill = openpyxl.styles.PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        cell.font = openpyxl.styles.Font(bold=True, color="FFFFFF")
    ws.auto_filter.ref = ws.dimensions
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18

conn.close()

wb.save(OUTPUT)
print(f"✅ Excel 已生成: {OUTPUT}")
print(f"   包含 6 个 Sheet: 比赛总览 / 比赛明细 / WDL时序赔率_样例 / 让球时序赔率_样例 / 总进球时序赔率_样例 / WDL时间点分布")