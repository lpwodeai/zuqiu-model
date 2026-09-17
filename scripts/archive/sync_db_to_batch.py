import sqlite3
import re
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
db_path = BASE_DIR / "data" / "odds_timing.db"
batch_path = BASE_DIR / "data" / "batch_import.py"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

with open(batch_path, 'r', encoding='utf-8') as f:
    batch_content = f.read()

batch_match_ids = set(re.findall(r"'match_id': '([^']+)'", batch_content))

cursor.execute("""
    SELECT m.match_id, m.home_team, m.away_team, m.match_date, m.match_time, 
           m.league, m.round, m.status, m.source
    FROM matches m
    WHERE m.match_id NOT IN ({})
    ORDER BY m.round, m.match_date, m.match_time
""".format(','.join('?' * len(batch_match_ids))), tuple(batch_match_ids))

missing_matches = cursor.fetchall()
print(f"发现 {len(missing_matches)} 场数据库中有但batch_import.py中没有的比赛")

new_matches_lines = []

for match in missing_matches:
    match_id, home_team, away_team, match_date, match_time, league, match_round, status, source = match
    
    cursor.execute("SELECT timestamp, win_a, draw, win_b FROM wdl_timing WHERE match_id = ? ORDER BY timestamp", (match_id,))
    wdl_data = cursor.fetchall()
    
    cursor.execute("SELECT timestamp, handicap, hcp_win, hcp_draw, hcp_lose FROM handicap_timing WHERE match_id = ? ORDER BY timestamp", (match_id,))
    handicap_data = cursor.fetchall()
    
    cursor.execute("SELECT timestamp, score, odds FROM score_timing WHERE match_id = ? ORDER BY timestamp", (match_id,))
    score_data = cursor.fetchall()
    score_dict = {}
    for ts, score, odds_val in score_data:
        if ts not in score_dict:
            score_dict[ts] = []
        score_dict[ts].append((score, odds_val))
    
    cursor.execute("SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus FROM total_goals_timing WHERE match_id = ? ORDER BY timestamp", (match_id,))
    tg_data = cursor.fetchall()
    
    cursor.execute("SELECT actual_score, actual_wdl, actual_handicap, actual_total_goals, verified FROM match_results WHERE match_id = ?", (match_id,))
    result_row = cursor.fetchone()
    result = None
    if result_row:
        result = {
            'actual_score': result_row[0],
            'actual_wdl': result_row[1],
            'actual_handicap': result_row[2],
            'actual_total_goals': result_row[3],
            'verified': result_row[4]
        }

    wdl_lines = []
    for ts, wa, dr, wb in wdl_data:
        wdl_lines.append(f"            ('{ts}', {wa}, {dr}, {wb}),")
    
    handicap_lines = []
    for ts, hcp, hw, hd, hl in handicap_data:
        handicap_lines.append(f"            ('{ts}', {hcp}, {hw}, {hd}, {hl}),")
    
    score_lines = []
    for ts, scores in score_dict.items():
        score_items = []
        for score, odds_val in scores:
            score_items.append(f"('{score}', {odds_val})")
        score_lines.append(f"            '{ts}': [")
        score_lines.append(f"                {', '.join(score_items)},")
        score_lines.append(f"            ],")
    
    tg_lines = []
    for ts, g0, g1, g2, g3, g4, g5, g6, g7 in tg_data:
        tg_lines.append(f"            ('{ts}', {g0}, {g1}, {g2}, {g3}, {g4}, {g5}, {g6}, {g7}),")
    
    result_lines = []
    if result:
        result_lines = [
            f"        'result': {{",
            f"            'actual_score': '{result['actual_score']}',",
            f"            'actual_wdl': '{result['actual_wdl']}',",
            f"            'actual_handicap': '{result['actual_handicap']}',",
            f"            'actual_total_goals': {result['actual_total_goals']},",
            f"            'verified': {result['verified']}",
            f"        }},",
        ]
    
    match_lines = [
        f"    # 第{match_round}轮 {home_team} vs {away_team}",
        f"    {{",
        f"        'match_id': '{match_id}',",
        f"        'home_team': '{home_team}',",
        f"        'away_team': '{away_team}',",
        f"        'match_date': '{match_date}',",
        f"        'match_time': '{match_time}',",
        f"        'league': '{league}',",
        f"        'round': {match_round},",
        f"        'status': '{status}',",
        f"        'source': '{source}',",
    ]
    
    if wdl_lines:
        match_lines.append("        'wdl_timing': [")
        match_lines.extend(wdl_lines)
        match_lines.append("        ],")
    
    if handicap_lines:
        match_lines.append("        'handicap_timing': [")
        match_lines.extend(handicap_lines)
        match_lines.append("        ],")
    
    if score_lines:
        match_lines.append("        'score_timing': {")
        match_lines.extend(score_lines)
        match_lines.append("        },")
    
    if tg_lines:
        match_lines.append("        'total_goals_timing': [")
        match_lines.extend(tg_lines)
        match_lines.append("        ],")
    
    if result_lines:
        match_lines.extend(result_lines)
    
    match_lines.append("    },")
    
    new_matches_lines.extend(match_lines)

conn.close()

with open(batch_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

insert_pos = -1
for i, line in enumerate(lines):
    if line.strip() == ']':
        insert_pos = i
        break

if insert_pos != -1:
    new_content = ''.join(lines[:insert_pos]) + '\n'.join(new_matches_lines) + '\n' + ''.join(lines[insert_pos:])
    
    with open(batch_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"成功添加 {len(missing_matches)} 场比赛数据到 batch_import.py")
    print(f"文件现在共有 {len(new_content.splitlines())} 行")
else:
    print("未找到MATCHES_DATA列表的结束位置")
