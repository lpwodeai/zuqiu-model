import pandas as pd
import sqlite3
import os
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
EXCEL_PATH = BASE_DIR / "2025-2026 英超 .xlsx"
DB_PATH = BASE_DIR / "data" / "odds.db"

def parse_match_info(df):
    season = None
    round_num = None
    match_date = None
    match_time = None
    home_team = None
    away_team = None
    actual_wdl = None
    actual_handicap = None
    actual_score = None
    actual_total_goals = None
    handicap = None
    
    for i in range(len(df)):
        col0 = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
        
        if col0 == "比赛信息":
            if i + 2 < len(df):
                season = str(df.iloc[i+2, 0]).strip() if pd.notna(df.iloc[i+2, 0]) else None
                round_num = str(df.iloc[i+2, 1]).strip() if pd.notna(df.iloc[i+2, 1]) else None
                match_date = str(df.iloc[i+2, 2]).strip() if pd.notna(df.iloc[i+2, 2]) else None
                match_time = str(df.iloc[i+2, 3]).strip() if pd.notna(df.iloc[i+2, 3]) else None
                home_team = str(df.iloc[i+2, 4]).strip() if pd.notna(df.iloc[i+2, 4]) else None
                away_team = str(df.iloc[i+2, 5]).strip() if pd.notna(df.iloc[i+2, 5]) else None
        elif col0 == "开奖结果":
            j = i + 2
            while j < len(df):
                game_type = str(df.iloc[j, 2]).strip() if pd.notna(df.iloc[j, 2]) else ""
                result = str(df.iloc[j, 3]).strip() if pd.notna(df.iloc[j, 3]) else ""
                if game_type == "胜平负":
                    actual_wdl = result
                elif game_type == "让球胜平负":
                    actual_handicap = result
                elif game_type == "比分":
                    actual_score = result
                    if actual_score:
                        parts = actual_score.split(":")
                        if len(parts) == 2:
                            actual_total_goals = int(parts[0]) + int(parts[1])
                elif game_type == "总进球":
                    actual_total_goals = int(result) if result.isdigit() else actual_total_goals
                j += 1
                if j >= len(df) or pd.isna(df.iloc[j, 0]):
                    break
    
    if match_date and home_team and away_team:
        match_id = f"{match_date.replace('/', '-')}_{home_team}_{away_team}"
    else:
        match_id = f"unknown_{home_team}_{away_team}" if home_team and away_team else "unknown"
    
    return {
        'match_id': match_id,
        'home_team': home_team,
        'away_team': away_team,
        'match_date': match_date,
        'match_time': match_time,
        'season': season,
        'round_num': round_num,
        'actual_wdl': actual_wdl,
        'actual_handicap': actual_handicap,
        'actual_score': actual_score,
        'actual_total_goals': actual_total_goals,
        'handicap': handicap
    }

def parse_wdl_history(df, match_id):
    records = []
    start_row = None
    
    for i in range(len(df)):
        col0 = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
        if col0 == "胜平负固定奖金":
            start_row = i + 2
            break
    
    if start_row is None:
        return records
    
    for i in range(start_row, len(df)):
        timestamp = str(df.iloc[i, 2]).strip() if pd.notna(df.iloc[i, 2]) else ""
        if not timestamp or timestamp == "发布时间":
            continue
        if pd.isna(df.iloc[i, 0]):
            break
        
        try:
            win_a = float(df.iloc[i, 3])
            draw = float(df.iloc[i, 4])
            win_b = float(df.iloc[i, 5])
            records.append({
                'match_id': match_id,
                'timestamp': timestamp,
                'win_a': win_a,
                'draw': draw,
                'win_b': win_b
            })
        except:
            break
    
    return records

def parse_handicap_history(df, match_id):
    records = []
    start_row = None
    handicap_val = None
    
    for i in range(len(df)):
        col0 = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
        if "让球胜平负固定奖金" in col0:
            match = re.search(r'让球\s*([+-]?\d+)', col0)
            if match:
                handicap_val = float(match.group(1))
            start_row = i + 2
            break
    
    if start_row is None:
        return records, handicap_val
    
    for i in range(start_row, len(df)):
        timestamp = str(df.iloc[i, 3]).strip() if pd.notna(df.iloc[i, 3]) else ""
        if not timestamp or timestamp == "发布时间":
            continue
        if pd.isna(df.iloc[i, 0]):
            break
        
        try:
            hcp_win = float(df.iloc[i, 4])
            hcp_draw = float(df.iloc[i, 5])
            hcp_lose = float(df.iloc[i, 6])
            records.append({
                'match_id': match_id,
                'timestamp': timestamp,
                'hcp_win': hcp_win,
                'hcp_draw': hcp_draw,
                'hcp_lose': hcp_lose
            })
        except:
            break
    
    return records, handicap_val

def parse_total_goals_history(df, match_id):
    records = []
    start_row = None
    
    for i in range(len(df)):
        col0 = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
        if col0 == "总进球固定奖金":
            start_row = i + 2
            break
    
    if start_row is None:
        return records
    
    for i in range(start_row, len(df)):
        timestamp = str(df.iloc[i, 2]).strip() if pd.notna(df.iloc[i, 2]) else ""
        if not timestamp or timestamp == "发布时间":
            continue
        if pd.isna(df.iloc[i, 0]):
            break
        
        try:
            goals_0 = float(df.iloc[i, 3]) if pd.notna(df.iloc[i, 3]) else None
            goals_1 = float(df.iloc[i, 4]) if pd.notna(df.iloc[i, 4]) else None
            goals_2 = float(df.iloc[i, 5]) if pd.notna(df.iloc[i, 5]) else None
            goals_3 = float(df.iloc[i, 6]) if pd.notna(df.iloc[i, 6]) else None
            goals_4 = float(df.iloc[i, 7]) if pd.notna(df.iloc[i, 7]) else None
            goals_5 = float(df.iloc[i, 8]) if pd.notna(df.iloc[i, 8]) else None
            goals_6 = float(df.iloc[i, 9]) if pd.notna(df.iloc[i, 9]) else None
            goals_7_plus = float(df.iloc[i, 10]) if pd.notna(df.iloc[i, 10]) else None
            
            records.append({
                'match_id': match_id,
                'timestamp': timestamp,
                'goals_0': goals_0,
                'goals_1': goals_1,
                'goals_2': goals_2,
                'goals_3': goals_3,
                'goals_4': goals_4,
                'goals_5': goals_5,
                'goals_6': goals_6,
                'goals_7_plus': goals_7_plus
            })
        except:
            break
    
    return records

def parse_score_history(df, match_id):
    records = []
    start_row = None
    score_cols = []
    
    for i in range(len(df)):
        col0 = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
        if col0 == "比分固定奖金":
            header_row = i + 1
            for j in range(3, len(df.columns)):
                col_name = str(df.iloc[header_row, j]).strip() if pd.notna(df.iloc[header_row, j]) else ""
                if col_name and col_name != "发布时间":
                    score_cols.append((j, col_name))
            start_row = i + 2
            break
    
    if start_row is None:
        return records
    
    current_timestamp = None
    
    for i in range(start_row, len(df)):
        timestamp = str(df.iloc[i, 2]).strip() if pd.notna(df.iloc[i, 2]) else ""
        
        if timestamp and timestamp != "发布时间":
            current_timestamp = timestamp
        
        if pd.notna(df.iloc[i, 0]) and current_timestamp:
            for j, score in score_cols:
                if pd.notna(df.iloc[i, j]):
                    try:
                        odds = float(df.iloc[i, j])
                        records.append({
                            'match_id': match_id,
                            'timestamp': current_timestamp,
                            'score': score,
                            'odds': odds
                        })
                    except:
                        continue
        elif pd.isna(df.iloc[i, 0]) and pd.notna(df.iloc[i, 3]):
            for j, score in score_cols:
                if pd.notna(df.iloc[i, j]):
                    try:
                        odds = float(df.iloc[i, j])
                        records.append({
                            'match_id': match_id,
                            'timestamp': current_timestamp,
                            'score': score,
                            'odds': odds
                        })
                    except:
                        continue
    
    return records

def import_to_database(match_info, wdl_records, handicap_records, tg_records, score_records):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO matches (match_id, home_team, away_team, match_date, 
                                           match_type, handicap, actual_wdl, actual_handicap, 
                                           actual_score, actual_total_goals)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            match_info['match_id'],
            match_info['home_team'],
            match_info['away_team'],
            match_info['match_date'],
            'Premier League',
            match_info['handicap'],
            match_info['actual_wdl'],
            match_info['actual_handicap'],
            match_info['actual_score'],
            match_info['actual_total_goals']
        ))
        
        for record in wdl_records:
            cursor.execute("""
                INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                VALUES (?, ?, ?, ?, ?)
            """, (record['match_id'], record['timestamp'], record['win_a'], record['draw'], record['win_b']))
        
        for record in handicap_records:
            cursor.execute("""
                INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                VALUES (?, ?, ?, ?, ?)
            """, (record['match_id'], record['timestamp'], record['hcp_win'], record['hcp_draw'], record['hcp_lose']))
        
        for record in tg_records:
            cursor.execute("""
                INSERT OR IGNORE INTO total_goals_history (match_id, timestamp, goals_0, goals_1, 
                                                           goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (record['match_id'], record['timestamp'], record['goals_0'], record['goals_1'],
                  record['goals_2'], record['goals_3'], record['goals_4'], record['goals_5'],
                  record['goals_6'], record['goals_7_plus']))
        
        for record in score_records:
            cursor.execute("""
                INSERT OR IGNORE INTO score_history (match_id, timestamp, score, odds)
                VALUES (?, ?, ?, ?)
            """, (record['match_id'], record['timestamp'], record['score'], record['odds']))
        
        conn.commit()
        print(f"  ✓ 成功导入: {match_info['home_team']} vs {match_info['away_team']}")
        print(f"    - WDL历史: {len(wdl_records)} 条")
        print(f"    - 让球历史: {len(handicap_records)} 条")
        print(f"    - 总进球历史: {len(tg_records)} 条")
        print(f"    - 比分历史: {len(score_records)} 条")
        
    except Exception as e:
        print(f"  ✗ 导入失败: {e}")
        conn.rollback()
    finally:
        conn.close()

def main():
    print("=" * 60)
    print("从Excel导入赔率数据到数据库")
    print("=" * 60)
    
    xls = pd.ExcelFile(EXCEL_PATH)
    sheets = xls.sheet_names
    print(f"\n发现 {len(sheets)} 场比赛")
    
    total_wdl = 0
    total_handicap = 0
    total_tg = 0
    total_score = 0
    
    for i, sheet_name in enumerate(sheets):
        print(f"\n--- {i+1}. {sheet_name} ---")
        
        df = pd.read_excel(EXCEL_PATH, sheet_name=sheet_name)
        
        match_info = parse_match_info(df)
        print(f"  比赛ID: {match_info['match_id']}")
        
        wdl_records = parse_wdl_history(df, match_info['match_id'])
        handicap_records, handicap_val = parse_handicap_history(df, match_info['match_id'])
        match_info['handicap'] = handicap_val
        tg_records = parse_total_goals_history(df, match_info['match_id'])
        score_records = parse_score_history(df, match_info['match_id'])
        
        import_to_database(match_info, wdl_records, handicap_records, tg_records, score_records)
        
        total_wdl += len(wdl_records)
        total_handicap += len(handicap_records)
        total_tg += len(tg_records)
        total_score += len(score_records)
    
    print("\n" + "=" * 60)
    print(f"导入完成!")
    print(f"  比赛数: {len(sheets)}")
    print(f"  WDL历史记录: {total_wdl}")
    print(f"  让球历史记录: {total_handicap}")
    print(f"  总进球历史记录: {total_tg}")
    print(f"  比分历史记录: {total_score}")
    print("=" * 60)

if __name__ == "__main__":
    main()
