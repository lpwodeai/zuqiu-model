import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

MATCH_DATA = {
    'match_id': '2025-10-24_Leeds_United_West_Ham_United',
    'home_team': 'Leeds United',
    'away_team': 'West Ham United',
    'match_date': '2025-10-24',
    'match_time': '03:00',
    'season': '2025/2026',
    'round_num': '第9轮',
    'match_type': 'Premier League',
    'actual_wdl': '胜',
    'actual_handicap': '(-1)平',
    'actual_score': '2:1',
    'actual_total_goals': 3,
    'handicap': -1.0
}

WDL_HISTORY = [
    {'timestamp': '2025-10-23 13:58:20', 'win_a': 1.67, 'draw': 3.35, 'win_b': 4.35},
    {'timestamp': '2025-10-24 11:45:06', 'win_a': 1.68, 'draw': 3.42, 'win_b': 4.15},
    {'timestamp': '2025-10-24 15:24:24', 'win_a': 1.66, 'draw': 3.45, 'win_b': 4.25},
    {'timestamp': '2025-10-24 19:42:58', 'win_a': 1.69, 'draw': 3.40, 'win_b': 4.10},
    {'timestamp': '2025-10-24 20:04:05', 'win_a': 1.72, 'draw': 3.35, 'win_b': 4.00},
    {'timestamp': '2025-10-24 20:17:23', 'win_a': 1.76, 'draw': 3.30, 'win_b': 3.90}
]

HANDICAP_HISTORY = [
    {'timestamp': '2025-10-23 13:58:20', 'hcp_win': 3.10, 'hcp_draw': 3.40, 'hcp_lose': 1.95},
    {'timestamp': '2025-10-24 11:45:13', 'hcp_win': 3.17, 'hcp_draw': 3.42, 'hcp_lose': 1.92},
    {'timestamp': '2025-10-24 19:42:52', 'hcp_win': 3.22, 'hcp_draw': 3.48, 'hcp_lose': 1.88},
    {'timestamp': '2025-10-24 20:04:10', 'hcp_win': 3.30, 'hcp_draw': 3.55, 'hcp_lose': 1.84},
    {'timestamp': '2025-10-24 20:17:29', 'hcp_win': 3.38, 'hcp_draw': 3.60, 'hcp_lose': 1.80}
]

TOTAL_GOALS_HISTORY = [
    {'timestamp': '2025-10-23 13:58:20', 'goals_0': 11.00, 'goals_1': 4.50, 'goals_2': 3.25, 
     'goals_3': 3.60, 'goals_4': 5.70, 'goals_5': 10.50, 'goals_6': 20.00, 'goals_7_plus': 30.00},
    {'timestamp': '2025-10-24 18:43:58', 'goals_0': 11.00, 'goals_1': 4.50, 'goals_2': 3.25, 
     'goals_3': 3.60, 'goals_4': 5.70, 'goals_5': 10.50, 'goals_6': 20.00, 'goals_7_plus': 30.00},
    {'timestamp': '2025-10-24 18:56:39', 'goals_0': 11.00, 'goals_1': 4.50, 'goals_2': 3.30, 
     'goals_3': 3.55, 'goals_4': 5.70, 'goals_5': 10.50, 'goals_6': 20.00, 'goals_7_plus': 30.00}
]

SCORE_HISTORY = [
    {'timestamp': '2025-10-23 13:58:20', 'score': '1:0', 'odds': 6.25},
    {'timestamp': '2025-10-23 13:58:20', 'score': '2:0', 'odds': 7.50},
    {'timestamp': '2025-10-23 13:58:20', 'score': '2:1', 'odds': 7.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '3:0', 'odds': 13.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '3:1', 'odds': 12.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '3:2', 'odds': 25.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '4:0', 'odds': 30.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '4:1', 'odds': 30.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '4:2', 'odds': 55.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '5:0', 'odds': 90.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '5:1', 'odds': 80.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '5:2', 'odds': 150.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '胜其它', 'odds': 55.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '0:0', 'odds': 11.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '1:1', 'odds': 6.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '2:2', 'odds': 13.50},
    {'timestamp': '2025-10-23 13:58:20', 'score': '3:3', 'odds': 70.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '平其它', 'odds': 400.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '0:1', 'odds': 12.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '0:2', 'odds': 24.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '1:2', 'odds': 12.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '0:3', 'odds': 70.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '1:3', 'odds': 50.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '2:3', 'odds': 50.00},
    {'timestamp': '2025-10-23 13:58:20', 'score': '0:4', 'odds': 300.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '1:4', 'odds': 150.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '2:4', 'odds': 200.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '0:5', 'odds': 750.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '1:5', 'odds': 500.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '2:5', 'odds': 600.0},
    {'timestamp': '2025-10-23 13:58:20', 'score': '负其它', 'odds': 200.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '1:0', 'odds': 6.25},
    {'timestamp': '2025-10-24 20:19:02', 'score': '2:0', 'odds': 7.75},
    {'timestamp': '2025-10-24 20:19:02', 'score': '2:1', 'odds': 7.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '3:0', 'odds': 15.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '3:1', 'odds': 12.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '3:2', 'odds': 27.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '4:0', 'odds': 30.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '4:1', 'odds': 30.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '4:2', 'odds': 55.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '5:0', 'odds': 90.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '5:1', 'odds': 80.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '5:2', 'odds': 150.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '胜其它', 'odds': 55.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '0:0', 'odds': 11.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '1:1', 'odds': 6.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '2:2', 'odds': 13.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '3:3', 'odds': 60.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '平其它', 'odds': 300.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '0:1', 'odds': 12.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '0:2', 'odds': 24.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '1:2', 'odds': 12.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '0:3', 'odds': 60.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '1:3', 'odds': 45.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '2:3', 'odds': 45.00},
    {'timestamp': '2025-10-24 20:19:02', 'score': '0:4', 'odds': 200.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '1:4', 'odds': 125.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '2:4', 'odds': 150.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '0:5', 'odds': 600.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '1:5', 'odds': 400.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '2:5', 'odds': 500.0},
    {'timestamp': '2025-10-24 20:19:02', 'score': '负其它', 'odds': 150.0}
]

def create_tables(conn):
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            match_date TEXT,
            match_time TEXT,
            match_type TEXT,
            handicap REAL,
            actual_wdl TEXT,
            actual_handicap TEXT,
            actual_score TEXT,
            actual_total_goals INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wdl_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            timestamp TEXT,
            win_a REAL,
            draw REAL,
            win_b REAL,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS handicap_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            timestamp TEXT,
            hcp_win REAL,
            hcp_draw REAL,
            hcp_lose REAL,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS total_goals_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            timestamp TEXT,
            goals_0 REAL,
            goals_1 REAL,
            goals_2 REAL,
            goals_3 REAL,
            goals_4 REAL,
            goals_5 REAL,
            goals_6 REAL,
            goals_7_plus REAL,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS score_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            timestamp TEXT,
            score TEXT,
            odds REAL,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        )
    """)
    
    conn.commit()

def import_match():
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT OR REPLACE INTO matches (match_id, home_team, away_team, match_date, 
                                           match_type, handicap, actual_wdl, 
                                           actual_handicap, actual_score, actual_total_goals)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            MATCH_DATA['match_id'],
            MATCH_DATA['home_team'],
            MATCH_DATA['away_team'],
            MATCH_DATA['match_date'],
            MATCH_DATA['match_type'],
            MATCH_DATA['handicap'],
            MATCH_DATA['actual_wdl'],
            MATCH_DATA['actual_handicap'],
            MATCH_DATA['actual_score'],
            MATCH_DATA['actual_total_goals']
        ))
        
        for record in WDL_HISTORY:
            cursor.execute("""
                INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                VALUES (?, ?, ?, ?, ?)
            """, (MATCH_DATA['match_id'], record['timestamp'], record['win_a'], record['draw'], record['win_b']))
        
        for record in HANDICAP_HISTORY:
            cursor.execute("""
                INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                VALUES (?, ?, ?, ?, ?)
            """, (MATCH_DATA['match_id'], record['timestamp'], record['hcp_win'], record['hcp_draw'], record['hcp_lose']))
        
        for record in TOTAL_GOALS_HISTORY:
            cursor.execute("""
                INSERT OR IGNORE INTO total_goals_history (match_id, timestamp, goals_0, goals_1, 
                                                           goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (MATCH_DATA['match_id'], record['timestamp'], record['goals_0'], record['goals_1'],
                  record['goals_2'], record['goals_3'], record['goals_4'], record['goals_5'],
                  record['goals_6'], record['goals_7_plus']))
        
        for record in SCORE_HISTORY:
            cursor.execute("""
                INSERT OR IGNORE INTO score_history (match_id, timestamp, score, odds)
                VALUES (?, ?, ?, ?)
            """, (MATCH_DATA['match_id'], record['timestamp'], record['score'], record['odds']))
        
        conn.commit()
        
        print("=" * 60)
        print("单场比赛数据导入完成")
        print("=" * 60)
        print(f"\n比赛信息:")
        print(f"  比赛ID: {MATCH_DATA['match_id']}")
        print(f"  主队: {MATCH_DATA['home_team']}")
        print(f"  客队: {MATCH_DATA['away_team']}")
        print(f"  日期: {MATCH_DATA['match_date']} {MATCH_DATA['match_time']}")
        print(f"  赛季/轮次: {MATCH_DATA['season']} {MATCH_DATA['round_num']}")
        print(f"  实际结果: {MATCH_DATA['actual_wdl']} ({MATCH_DATA['actual_score']})")
        print(f"  让球结果: {MATCH_DATA['actual_handicap']}")
        print(f"  总进球: {MATCH_DATA['actual_total_goals']}")
        print(f"\n导入记录数:")
        print(f"  WDL历史: {len(WDL_HISTORY)} 条")
        print(f"  让球历史: {len(HANDICAP_HISTORY)} 条")
        print(f"  总进球历史: {len(TOTAL_GOALS_HISTORY)} 条")
        print(f"  比分历史: {len(SCORE_HISTORY)} 条")
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"导入失败: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
    finally:
        conn.close()
    
    return MATCH_DATA['match_id']

def analyze_match_odds(match_id):
    conn = sqlite3.connect(DB_PATH)
    
    wdl_df = pd.read_sql(f"SELECT * FROM wdl_history WHERE match_id = '{match_id}' ORDER BY timestamp", conn)
    handicap_df = pd.read_sql(f"SELECT * FROM handicap_history WHERE match_id = '{match_id}' ORDER BY timestamp", conn)
    tg_df = pd.read_sql(f"SELECT * FROM total_goals_history WHERE match_id = '{match_id}' ORDER BY timestamp", conn)
    match_df = pd.read_sql(f"SELECT * FROM matches WHERE match_id = '{match_id}'", conn)
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("赔率分析报告 - Leeds United vs West Ham United")
    print("=" * 60)
    
    print("\n1. 胜平负赔率趋势分析")
    print("-" * 60)
    print(f"{'时间':<20} | {'主胜':<8} | {'平局':<8} | {'客胜':<8} | {'隐含主胜':<10} | {'隐含平局':<10} | {'隐含客胜':<10}")
    print("-" * 60)
    
    for _, row in wdl_df.iterrows():
        total = 1/row['win_a'] + 1/row['draw'] + 1/row['win_b']
        imp_home = (1/row['win_a']) / total
        imp_draw = (1/row['draw']) / total
        imp_away = (1/row['win_b']) / total
        print(f"{row['timestamp']:<20} | {row['win_a']:<8.2f} | {row['draw']:<8.2f} | {row['win_b']:<8.2f} | {imp_home:<10.4f} | {imp_draw:<10.4f} | {imp_away:<10.4f}")
    
    print(f"\n主胜赔率变化: {wdl_df['win_a'].iloc[0]:.2f} → {wdl_df['win_a'].iloc[-1]:.2f} ({wdl_df['win_a'].iloc[-1]-wdl_df['win_a'].iloc[0]:+.2f})")
    print(f"客胜赔率变化: {wdl_df['win_b'].iloc[0]:.2f} → {wdl_df['win_b'].iloc[-1]:.2f} ({wdl_df['win_b'].iloc[-1]-wdl_df['win_b'].iloc[0]:+.2f})")
    
    wdl_change_rate = (wdl_df['win_a'].iloc[-1] - wdl_df['win_a'].iloc[0]) / wdl_df['win_a'].iloc[0] * 100
    print(f"主胜赔率变化率: {wdl_change_rate:.2f}%")
    
    print("\n2. 让球胜平负赔率趋势分析 (-1)")
    print("-" * 60)
    print(f"{'时间':<20} | {'让胜':<8} | {'让平':<8} | {'让负':<8}")
    print("-" * 60)
    
    for _, row in handicap_df.iterrows():
        print(f"{row['timestamp']:<20} | {row['hcp_win']:<8.2f} | {row['hcp_draw']:<8.2f} | {row['hcp_lose']:<8.2f}")
    
    print(f"\n让负赔率变化: {handicap_df['hcp_lose'].iloc[0]:.2f} → {handicap_df['hcp_lose'].iloc[-1]:.2f} ({handicap_df['hcp_lose'].iloc[-1]-handicap_df['hcp_lose'].iloc[0]:+.2f})")
    
    print("\n3. 总进球赔率分析")
    print("-" * 60)
    for _, row in tg_df.iterrows():
        print(f"\n时间: {row['timestamp']}")
        print(f"  0球: {row['goals_0']:.2f}  1球: {row['goals_1']:.2f}  2球: {row['goals_2']:.2f}  3球: {row['goals_3']:.2f}")
        print(f"  4球: {row['goals_4']:.2f}  5球: {row['goals_5']:.2f}  6球: {row['goals_6']:.2f}  7+: {row['goals_7_plus']:.2f}")
    
    print("\n4. 赔率隐含概率分析")
    print("-" * 60)
    final_row = wdl_df.iloc[-1]
    total_implied = 1/final_row['win_a'] + 1/final_row['draw'] + 1/final_row['win_b']
    margin = (total_implied - 1) * 100
    print(f"最终赔率隐含概率总和: {total_implied:.4f}")
    print(f"庄家抽水(Margin): {margin:.2f}%")
    print(f"\n归一化隐含概率:")
    print(f"  主胜: {(1/final_row['win_a'])/total_implied*100:.2f}%")
    print(f"  平局: {(1/final_row['draw'])/total_implied*100:.2f}%")
    print(f"  客胜: {(1/final_row['win_b'])/total_implied*100:.2f}%")
    
    print("\n5. 比分赔率分析")
    print("-" * 60)
    conn = sqlite3.connect(DB_PATH)
    score_df = pd.read_sql(f"SELECT * FROM score_history WHERE match_id = '{match_id}' AND timestamp = '2025-10-24 20:19:02'", conn)
    conn.close()
    print(f"比赛前最新比分赔率(2025-10-24 20:19:02):")
    key_scores = ['2:1', '1:0', '2:0', '1:1', '2:2', '0:1']
    for score in key_scores:
        row = score_df[score_df['score'] == score]
        if not row.empty:
            label = f"{score} (实际结果)" if score == '2:1' else score
            print(f"  {label}: {row.iloc[0]['odds']:.2f}")
    
    print("\n6. 综合分析")
    print("-" * 60)
    print(f"比赛结果: {match_df.iloc[0]['actual_wdl']} ({match_df.iloc[0]['actual_score']})")
    print(f"实际总进球: {match_df.iloc[0]['actual_total_goals']}")
    print(f"\n赔率趋势解读:")
    print(f"  - 主胜赔率从1.67升至1.76，市场对主队信心有所下降")
    print(f"  - 客胜赔率从4.35降至3.90，市场对客队信心上升")
    print(f"  - 平局赔率从3.35降至3.30，临近比赛平局概率略有下降")
    print(f"  - 让球-1后让负赔率从1.95降至1.80，让负仍是市场主流")
    print(f"  - 总进球3球赔率从3.60降至3.55，大球概率略有上升")
    print(f"\n预测评估:")
    final_prob_home = (1/final_row['win_a']) / total_implied
    final_prob_draw = (1/final_row['draw']) / total_implied
    final_prob_away = (1/final_row['win_b']) / total_implied
    max_prob = max(final_prob_home, final_prob_draw, final_prob_away)
    
    if max_prob == final_prob_home:
        market_pred = f"主队获胜概率更高 ({final_prob_home*100:.1f}%)"
    elif max_prob == final_prob_away:
        market_pred = f"客队获胜概率更高 ({final_prob_away*100:.1f}%)"
    else:
        market_pred = f"平局概率更高 ({final_prob_draw*100:.1f}%)"
    
    print(f"  赔率市场预测: {market_pred}")
    
    if match_df.iloc[0]['actual_wdl'] == '平':
        if max_prob == final_prob_draw:
            print(f"  实际结果: 平局，与市场预测一致")
        else:
            print(f"  实际结果: 平局，与市场预测不一致")
            print(f"  这是一场平局冷门比赛!")
    elif match_df.iloc[0]['actual_wdl'] == '胜':
        if max_prob == final_prob_home:
            print(f"  实际结果: 主队获胜，与市场预测一致")
        else:
            print(f"  实际结果: 主队获胜，与市场预测不一致")
            print(f"  这是一场冷门比赛!")
    else:
        if max_prob == final_prob_away:
            print(f"  实际结果: 客队获胜，与市场预测一致")
        else:
            print(f"  实际结果: 客队获胜，与市场预测不一致")
            print(f"  这是一场冷门比赛!")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    match_id = import_match()
    analyze_match_odds(match_id)