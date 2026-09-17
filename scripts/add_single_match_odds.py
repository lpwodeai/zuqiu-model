import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

match_data = {
    'match_id': '2025-08-23_布伦特福德_阿斯顿维拉',
    'home_team': '布伦特福德',
    'away_team': '阿斯顿维拉',
    'match_date': '2025-08-23',
    'match_type': 'Premier League',
    'handicap': 1.0,
    'actual_wdl': '胜',
    'actual_handicap': '(+1) 胜',
    'actual_score': '1:0',
    'actual_total_goals': 1,
}

wdl_records = [
    {'timestamp': '2025-08-22 09:52:19', 'win_a': 2.90, 'draw': 3.35, 'win_b': 2.06},
    {'timestamp': '2025-08-23 11:34:34', 'win_a': 3.02, 'draw': 3.35, 'win_b': 2.00},
    {'timestamp': '2025-08-23 14:45:49', 'win_a': 3.15, 'draw': 3.35, 'win_b': 1.95},
    {'timestamp': '2025-08-23 17:57:23', 'win_a': 3.25, 'draw': 3.40, 'win_b': 1.90},
]

handicap_records = [
    {'timestamp': '2025-08-22 09:52:18', 'hcp_win': 1.58, 'hcp_draw': 3.85, 'hcp_lose': 4.25},
    {'timestamp': '2025-08-23 11:34:48', 'hcp_win': 1.62, 'hcp_draw': 3.75, 'hcp_lose': 4.10},
    {'timestamp': '2025-08-23 14:45:59', 'hcp_win': 1.65, 'hcp_draw': 3.70, 'hcp_lose': 3.95},
    {'timestamp': '2025-08-23 17:57:31', 'hcp_win': 1.68, 'hcp_draw': 3.65, 'hcp_lose': 3.85},
]

total_goals_records = [
    {'timestamp': '2025-08-22 09:52:19', 'goals_0': 14.00, 'goals_1': 5.40, 'goals_2': 3.60, 'goals_3': 3.40, 'goals_4': 4.80, 'goals_5': 8.75, 'goals_6': 16.00, 'goals_7_plus': 26.00},
    {'timestamp': '2025-08-23 19:07:52', 'goals_0': 12.50, 'goals_1': 5.25, 'goals_2': 3.50, 'goals_3': 3.40, 'goals_4': 5.00, 'goals_5': 9.00, 'goals_6': 17.00, 'goals_7_plus': 30.00},
    {'timestamp': '2025-08-23 19:21:20', 'goals_0': 11.00, 'goals_1': 4.75, 'goals_2': 3.50, 'goals_3': 3.50, 'goals_4': 5.25, 'goals_5': 9.25, 'goals_6': 19.00, 'goals_7_plus': 33.00},
]

score_records = []

score_records_20250822_095219 = [
    ('1:0', 10.50), ('2:0', 15.00), ('2:1', 9.00), ('3:0', 35.00), ('3:1', 21.00), ('3:2', 24.00),
    ('4:0', 120.0), ('4:1', 60.00), ('4:2', 75.00), ('5:0', 400.0), ('5:1', 200.0), ('5:2', 300.0), ('胜其它', 90.00),
    ('0:0', 14.00), ('1:1', 6.50), ('2:2', 11.00), ('3:3', 45.00), ('平其它', 300.0),
    ('0:1', 9.00), ('0:2', 11.50), ('1:2', 7.00), ('0:3', 20.00), ('1:3', 14.00), ('2:3', 21.00),
    ('0:4', 50.00), ('1:4', 35.00), ('2:4', 60.00), ('0:5', 150.0), ('1:5', 110.0), ('2:5', 175.0), ('负其它', 50.00),
]

score_records_20250823_191022 = [
    ('1:0', 10.50), ('2:0', 15.00), ('2:1', 9.50), ('3:0', 37.00), ('3:1', 23.00), ('3:2', 26.00),
    ('4:0', 120.0), ('4:1', 75.00), ('4:2', 85.00), ('5:0', 400.0), ('5:1', 200.0), ('5:2', 300.0), ('胜其它', 100.0),
    ('0:0', 12.50), ('1:1', 6.50), ('2:2', 11.00), ('3:3', 45.00), ('平其它', 300.0),
    ('0:1', 8.75), ('0:2', 10.50), ('1:2', 7.00), ('0:3', 20.00), ('1:3', 14.00), ('2:3', 21.00),
    ('0:4', 50.00), ('1:4', 35.00), ('2:4', 60.00), ('0:5', 150.0), ('1:5', 110.0), ('2:5', 175.0), ('负其它', 50.00),
]

score_records_20250823_192113 = [
    ('1:0', 10.50), ('2:0', 15.00), ('2:1', 9.75), ('3:0', 40.00), ('3:1', 25.00), ('3:2', 30.00),
    ('4:0', 125.0), ('4:1', 75.00), ('4:2', 85.00), ('5:0', 400.0), ('5:1', 200.0), ('5:2', 300.0), ('胜其它', 100.0),
    ('0:0', 11.00), ('1:1', 6.50), ('2:2', 11.00), ('3:3', 45.00), ('平其它', 300.0),
    ('0:1', 8.50), ('0:2', 10.50), ('1:2', 7.00), ('0:3', 20.00), ('1:3', 14.00), ('2:3', 21.00),
    ('0:4', 50.00), ('1:4', 35.00), ('2:4', 60.00), ('0:5', 150.0), ('1:5', 110.0), ('2:5', 175.0), ('负其它', 50.00),
]

for score, odds in score_records_20250822_095219:
    score_records.append({'timestamp': '2025-08-22 09:52:19', 'score': score, 'odds': odds})

for score, odds in score_records_20250823_191022:
    score_records.append({'timestamp': '2025-08-23 19:10:22', 'score': score, 'odds': odds})

for score, odds in score_records_20250823_192113:
    score_records.append({'timestamp': '2025-08-23 19:21:13', 'score': score, 'odds': odds})

def import_match_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO matches (match_id, home_team, away_team, match_date, 
                                           match_type, handicap, actual_wdl, actual_handicap, 
                                           actual_score, actual_total_goals, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            match_data['match_id'],
            match_data['home_team'],
            match_data['away_team'],
            match_data['match_date'],
            match_data['match_type'],
            match_data['handicap'],
            match_data['actual_wdl'],
            match_data['actual_handicap'],
            match_data['actual_score'],
            match_data['actual_total_goals'],
            now,
            now
        ))
        
        for record in wdl_records:
            cursor.execute("""
                INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                VALUES (?, ?, ?, ?, ?)
            """, (match_data['match_id'], record['timestamp'], record['win_a'], record['draw'], record['win_b']))
        
        for record in handicap_records:
            cursor.execute("""
                INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                VALUES (?, ?, ?, ?, ?)
            """, (match_data['match_id'], record['timestamp'], record['hcp_win'], record['hcp_draw'], record['hcp_lose']))
        
        for record in total_goals_records:
            cursor.execute("""
                INSERT OR IGNORE INTO total_goals_history (match_id, timestamp, goals_0, goals_1, 
                                                           goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (match_data['match_id'], record['timestamp'], record['goals_0'], record['goals_1'],
                  record['goals_2'], record['goals_3'], record['goals_4'], record['goals_5'],
                  record['goals_6'], record['goals_7_plus']))
        
        for record in score_records:
            cursor.execute("""
                INSERT OR IGNORE INTO score_history (match_id, timestamp, score, odds)
                VALUES (?, ?, ?, ?)
            """, (match_data['match_id'], record['timestamp'], record['score'], record['odds']))
        
        conn.commit()
        
        print("=" * 60)
        print(f"成功导入: {match_data['home_team']} vs {match_data['away_team']}")
        print(f"比赛ID: {match_data['match_id']}")
        print(f"比赛日期: {match_data['match_date']}")
        print(f"让球: +{match_data['handicap']}")
        print(f"实际结果: {match_data['actual_wdl']} | {match_data['actual_score']} | 总进球: {match_data['actual_total_goals']}")
        print("=" * 60)
        print(f"  WDL历史: {len(wdl_records)} 条")
        print(f"  让球历史: {len(handicap_records)} 条")
        print(f"  总进球历史: {len(total_goals_records)} 条")
        print(f"  比分历史: {len(score_records)} 条")
        print("=" * 60)
        
    except Exception as e:
        print(f"导入失败: {e}")
        conn.rollback()
    finally:
        conn.close()

def verify_import():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM matches WHERE match_id = ?", (match_data['match_id'],))
    match = cursor.fetchone()
    if match:
        print("\n✓ 比赛信息已导入")
        print(f"  home_team: {match[2]}, away_team: {match[3]}")
        print(f"  match_date: {match[4]}")
        print(f"  actual_wdl: {match[7]}, actual_score: {match[9]}, actual_total_goals: {match[10]}")
    
    cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id = ?", (match_data['match_id'],))
    count = cursor.fetchone()[0]
    print(f"✓ WDL赔率记录: {count} 条")
    
    cursor.execute("SELECT COUNT(*) FROM handicap_history WHERE match_id = ?", (match_data['match_id'],))
    count = cursor.fetchone()[0]
    print(f"✓ 让球赔率记录: {count} 条")
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_history WHERE match_id = ?", (match_data['match_id'],))
    count = cursor.fetchone()[0]
    print(f"✓ 总进球赔率记录: {count} 条")
    
    cursor.execute("SELECT COUNT(*) FROM score_history WHERE match_id = ?", (match_data['match_id'],))
    count = cursor.fetchone()[0]
    print(f"✓ 比分赔率记录: {count} 条")
    
    conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("添加单场比赛赔率数据")
    print("=" * 60)
    
    import_match_data()
    verify_import()