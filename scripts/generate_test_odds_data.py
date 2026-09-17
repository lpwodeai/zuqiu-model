import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MATCH_DB_PATH = BASE_DIR / "data" / "five_leagues.db"
ODDS_DB_PATH = BASE_DIR / "data" / "odds.db"


def generate_wdl_history(match_id, base_win_a=2.0, base_draw=3.2, base_win_b=3.5, volatility=0.1):
    records = []
    now = datetime.now()
    
    for i in range(20):
        timestamp = (now - timedelta(hours=i * 4)).strftime('%Y-%m-%d %H:%M:%S')
        noise = np.random.normal(0, volatility, 3)
        
        win_a = max(1.01, base_win_a + noise[0] + i * 0.02)
        draw = max(1.01, base_draw + noise[1])
        win_b = max(1.01, base_win_b + noise[2] - i * 0.015)
        
        records.append({
            'match_id': match_id,
            'timestamp': timestamp,
            'win_a': round(win_a, 2),
            'draw': round(draw, 2),
            'win_b': round(win_b, 2)
        })
    
    return records


def generate_handicap_history(match_id, base_hcp_win=1.85, base_hcp_draw=3.4, base_hcp_lose=3.8, volatility=0.1):
    records = []
    now = datetime.now()
    
    for i in range(15):
        timestamp = (now - timedelta(hours=i * 6)).strftime('%Y-%m-%d %H:%M:%S')
        noise = np.random.normal(0, volatility, 3)
        
        hcp_win = max(1.01, base_hcp_win + noise[0])
        hcp_draw = max(1.01, base_hcp_draw + noise[1])
        hcp_lose = max(1.01, base_hcp_lose + noise[2])
        
        records.append({
            'match_id': match_id,
            'timestamp': timestamp,
            'hcp_win': round(hcp_win, 2),
            'hcp_draw': round(hcp_draw, 2),
            'hcp_lose': round(hcp_lose, 2)
        })
    
    return records


def generate_total_goals_history(match_id, volatility=0.15):
    records = []
    now = datetime.now()
    
    base_odds = {
        'goals_0': 15.0,
        'goals_1': 7.0,
        'goals_2': 4.5,
        'goals_3': 3.8,
        'goals_4': 4.2,
        'goals_5': 6.0,
        'goals_6': 10.0,
        'goals_7_plus': 15.0
    }
    
    for i in range(12):
        timestamp = (now - timedelta(hours=i * 8)).strftime('%Y-%m-%d %H:%M:%S')
        
        record = {'match_id': match_id, 'timestamp': timestamp}
        
        for key, base in base_odds.items():
            noise = np.random.normal(0, volatility)
            record[key] = max(1.01, round(base + noise, 2))
        
        records.append(record)
    
    return records


def generate_score_history(match_id):
    records = []
    now = datetime.now()
    
    score_odds = {
        '1:0': 5.5, '2:0': 8.0, '2:1': 6.5, '3:0': 12.0, '3:1': 9.0,
        '0:0': 7.0, '1:1': 4.5, '2:2': 6.0,
        '0:1': 6.0, '0:2': 9.0, '1:2': 7.0, '0:3': 14.0, '1:3': 11.0
    }
    
    for i in range(8):
        timestamp = (now - timedelta(hours=i * 12)).strftime('%Y-%m-%d %H:%M:%S')
        
        for score, base_odds in score_odds.items():
            noise = np.random.normal(0, 0.2)
            odds = max(1.01, round(base_odds + noise, 2))
            
            records.append({
                'match_id': match_id,
                'timestamp': timestamp,
                'score': score,
                'odds': odds
            })
    
    return records


def insert_test_data(conn, match_ids):
    cursor = conn.cursor()
    
    for match_id in match_ids:
        wdl_records = generate_wdl_history(match_id)
        for rec in wdl_records:
            cursor.execute('''
                INSERT OR REPLACE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                VALUES (?, ?, ?, ?, ?)
            ''', (rec['match_id'], rec['timestamp'], rec['win_a'], rec['draw'], rec['win_b']))
        
        hcp_records = generate_handicap_history(match_id)
        for rec in hcp_records:
            cursor.execute('''
                INSERT OR REPLACE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                VALUES (?, ?, ?, ?, ?)
            ''', (rec['match_id'], rec['timestamp'], rec['hcp_win'], rec['hcp_draw'], rec['hcp_lose']))
        
        tg_records = generate_total_goals_history(match_id)
        for rec in tg_records:
            cursor.execute('''
                INSERT OR REPLACE INTO total_goals_history 
                (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (rec['match_id'], rec['timestamp'], 
                  rec['goals_0'], rec['goals_1'], rec['goals_2'], rec['goals_3'],
                  rec['goals_4'], rec['goals_5'], rec['goals_6'], rec['goals_7_plus']))
        
        score_records = generate_score_history(match_id)
        for rec in score_records:
            cursor.execute('''
                INSERT OR REPLACE INTO score_history (match_id, timestamp, score, odds)
                VALUES (?, ?, ?, ?)
            ''', (rec['match_id'], rec['timestamp'], rec['score'], rec['odds']))
    
    conn.commit()
    print(f"已插入 {len(match_ids)} 场比赛的测试赔率数据")


def main():
    match_conn = sqlite3.connect(MATCH_DB_PATH)
    query = """
        SELECT m.date, ht.name as home_team, at.name as away_team
        FROM matches m
        LEFT JOIN teams ht ON m.homeTeamId = ht.id
        LEFT JOIN teams at ON m.awayTeamId = at.id
        ORDER BY m.date
        LIMIT 100
    """
    match_df = pd.read_sql(query, match_conn)
    match_conn.close()
    
    match_ids = []
    for _, row in match_df.iterrows():
        date_str = row['date']
        if isinstance(date_str, pd.Timestamp):
            date_str = date_str.strftime('%Y-%m-%d')
        elif isinstance(date_str, str):
            date_str = date_str[:10]
        match_id = f"{date_str}_{row['home_team']}_{row['away_team']}"
        match_ids.append(match_id)
    
    conn = sqlite3.connect(ODDS_DB_PATH)
    
    cursor = conn.cursor()
    cursor.execute("DELETE FROM wdl_history")
    cursor.execute("DELETE FROM handicap_history")
    cursor.execute("DELETE FROM total_goals_history")
    cursor.execute("DELETE FROM score_history")
    conn.commit()
    
    insert_test_data(conn, match_ids)
    
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM wdl_history")
    wdl_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM handicap_history")
    hcp_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM total_goals_history")
    tg_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM score_history")
    score_count = cursor.fetchone()[0]
    
    print(f"\n数据库统计:")
    print(f"  wdl_history: {wdl_count} 条记录")
    print(f"  handicap_history: {hcp_count} 条记录")
    print(f"  total_goals_history: {tg_count} 条记录")
    print(f"  score_history: {score_count} 条记录")
    
    conn.close()
    print("\n测试数据生成完成!")


if __name__ == "__main__":
    main()
