import sqlite3
import pandas as pd
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

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

def import_match_from_data(match_data, wdl_history, handicap_history, total_goals_history, score_history):
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
            match_data['match_id'],
            match_data['home_team'],
            match_data['away_team'],
            match_data['match_date'],
            match_data['match_type'],
            match_data['handicap'],
            match_data['actual_wdl'],
            match_data['actual_handicap'],
            match_data['actual_score'],
            match_data['actual_total_goals']
        ))
        
        if wdl_history:
            for record in wdl_history:
                cursor.execute("""
                    INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                    VALUES (?, ?, ?, ?, ?)
                """, (match_data['match_id'], record['timestamp'], record['win_a'], record['draw'], record['win_b']))
        
        if handicap_history:
            for record in handicap_history:
                cursor.execute("""
                    INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                    VALUES (?, ?, ?, ?, ?)
                """, (match_data['match_id'], record['timestamp'], record['hcp_win'], record['hcp_draw'], record['hcp_lose']))
        
        if total_goals_history:
            for record in total_goals_history:
                cursor.execute("""
                    INSERT OR IGNORE INTO total_goals_history (match_id, timestamp, goals_0, goals_1, 
                                                               goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (match_data['match_id'], record['timestamp'], record['goals_0'], record['goals_1'],
                      record['goals_2'], record['goals_3'], record['goals_4'], record['goals_5'],
                      record['goals_6'], record['goals_7_plus']))
        
        if score_history:
            for record in score_history:
                cursor.execute("""
                    INSERT OR IGNORE INTO score_history (match_id, timestamp, score, odds)
                    VALUES (?, ?, ?, ?)
                """, (match_data['match_id'], record['timestamp'], record['score'], record['odds']))
        
        conn.commit()
        
        print("=" * 60)
        print("单场比赛数据导入完成")
        print("=" * 60)
        print(f"\n比赛信息:")
        print(f"  比赛ID: {match_data['match_id']}")
        print(f"  主队: {match_data['home_team']}")
        print(f"  客队: {match_data['away_team']}")
        print(f"  日期: {match_data['match_date']} {match_data.get('match_time', '')}")
        print(f"  赛季/轮次: {match_data.get('season', '')} {match_data.get('round_num', '')}")
        print(f"  实际结果: {match_data['actual_wdl']} ({match_data['actual_score']})")
        print(f"  让球结果: {match_data['actual_handicap']}")
        print(f"  总进球: {match_data['actual_total_goals']}")
        print(f"\n导入记录数:")
        print(f"  WDL历史: {len(wdl_history) if wdl_history else 0} 条")
        print(f"  让球历史: {len(handicap_history) if handicap_history else 0} 条")
        print(f"  总进球历史: {len(total_goals_history) if total_goals_history else 0} 条")
        print(f"  比分历史: {len(score_history) if score_history else 0} 条")
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"导入失败: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()
    
    return match_data['match_id']

def analyze_match_odds(match_id):
    conn = sqlite3.connect(DB_PATH)
    
    wdl_df = pd.read_sql(f"SELECT * FROM wdl_history WHERE match_id = '{match_id}' ORDER BY timestamp", conn)
    handicap_df = pd.read_sql(f"SELECT * FROM handicap_history WHERE match_id = '{match_id}' ORDER BY timestamp", conn)
    tg_df = pd.read_sql(f"SELECT * FROM total_goals_history WHERE match_id = '{match_id}' ORDER BY timestamp", conn)
    match_df = pd.read_sql(f"SELECT * FROM matches WHERE match_id = '{match_id}'", conn)
    
    conn.close()
    
    if match_df.empty:
        print(f"未找到比赛: {match_id}")
        return
    
    home_team = match_df.iloc[0]['home_team']
    away_team = match_df.iloc[0]['away_team']
    
    print("\n" + "=" * 60)
    print(f"赔率分析报告 - {home_team} vs {away_team}")
    print("=" * 60)
    
    if not wdl_df.empty:
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
    
    if not handicap_df.empty:
        print("\n2. 让球胜平负赔率趋势分析")
        print("-" * 60)
        print(f"{'时间':<20} | {'让胜':<8} | {'让平':<8} | {'让负':<8}")
        print("-" * 60)
        
        for _, row in handicap_df.iterrows():
            print(f"{row['timestamp']:<20} | {row['hcp_win']:<8.2f} | {row['hcp_draw']:<8.2f} | {row['hcp_lose']:<8.2f}")
    
    if not tg_df.empty:
        print("\n3. 总进球赔率分析")
        print("-" * 60)
        for _, row in tg_df.iterrows():
            print(f"\n时间: {row['timestamp']}")
            print(f"  0球: {row['goals_0']:.2f}  1球: {row['goals_1']:.2f}  2球: {row['goals_2']:.2f}  3球: {row['goals_3']:.2f}")
            print(f"  4球: {row['goals_4']:.2f}  5球: {row['goals_5']:.2f}  6球: {row['goals_6']:.2f}  7+: {row['goals_7_plus']:.2f}")
    
    if not wdl_df.empty:
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
    
    print("\n5. 综合分析")
    print("-" * 60)
    print(f"比赛结果: {match_df.iloc[0]['actual_wdl']} ({match_df.iloc[0]['actual_score']})")
    print(f"实际总进球: {match_df.iloc[0]['actual_total_goals']}")
    
    if not wdl_df.empty:
        print(f"\n赔率趋势解读:")
        home_odds_change = wdl_df['win_a'].iloc[-1] - wdl_df['win_a'].iloc[0]
        away_odds_change = wdl_df['win_b'].iloc[-1] - wdl_df['win_b'].iloc[0]
        
        if home_odds_change > 0:
            print(f"  - 主胜赔率上升 {home_odds_change:.2f}，市场对{home_team}信心下降")
        else:
            print(f"  - 主胜赔率下降 {abs(home_odds_change):.2f}，市场对{home_team}信心上升")
        
        if away_odds_change > 0:
            print(f"  - 客胜赔率上升 {away_odds_change:.2f}，市场对{away_team}信心下降")
        else:
            print(f"  - 客胜赔率下降 {abs(away_odds_change):.2f}，市场对{away_team}信心上升")
        
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
        
        actual_wdl = match_df.iloc[0]['actual_wdl']
        if actual_wdl == '负':
            if max_prob == final_prob_away:
                print(f"  实际结果: 客队获胜，与市场预测一致")
            else:
                print(f"  实际结果: 客队获胜，与市场预测不一致")
                print(f"  这是一场冷门比赛!")
        elif actual_wdl == '胜':
            if max_prob == final_prob_home:
                print(f"  实际结果: 主队获胜，与市场预测一致")
            else:
                print(f"  实际结果: 主队获胜，与市场预测不一致")
                print(f"  这是一场冷门比赛!")
        else:
            if max_prob == final_prob_draw:
                print(f"  实际结果: 平局，与市场预测一致")
            else:
                print(f"  实际结果: 平局，与市场预测不一致")
                print(f"  这是一场平局冷门比赛!")
    
    print("\n" + "=" * 60)

def update_backtest_stats():
    conn = sqlite3.connect(DB_PATH)
    
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM matches")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT actual_wdl, COUNT(*) FROM matches GROUP BY actual_wdl
    """)
    wdl_stats = cursor.fetchall()
    
    wdl_counts = {'胜': 0, '平': 0, '负': 0}
    for wdl, count in wdl_stats:
        wdl_counts[wdl] = count
    
    print("\n" + "=" * 60)
    print("全库回测统计更新")
    print("=" * 60)
    print(f"\n总比赛数: {total_matches}")
    print(f"\n胜平负分布:")
    print(f"  主胜: {wdl_counts['胜']} 场 ({wdl_counts['胜']/total_matches*100:.1f}%)")
    print(f"  平局: {wdl_counts['平']} 场 ({wdl_counts['平']/total_matches*100:.1f}%)")
    print(f"  客胜: {wdl_counts['负']} 场 ({wdl_counts['负']/total_matches*100:.1f}%)")
    
    cursor.execute("SELECT COUNT(*) FROM wdl_history")
    wdl_records = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM handicap_history")
    hcp_records = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_history")
    tg_records = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM score_history")
    score_records = cursor.fetchone()[0]
    
    print(f"\n赔率数据统计:")
    print(f"  WDL记录: {wdl_records} 条")
    print(f"  让球记录: {hcp_records} 条")
    print(f"  总进球记录: {tg_records} 条")
    print(f"  比分记录: {score_records} 条")
    
    conn.close()
    
    print("\n" + "=" * 60)

def generate_sample_json():
    sample = {
        "match_data": {
            "match_id": "2025-10-20_TeamA_TeamB",
            "home_team": "TeamA",
            "away_team": "TeamB",
            "match_date": "2025-10-20",
            "match_time": "20:00",
            "season": "2025/2026",
            "round_num": "第9轮",
            "match_type": "Premier League",
            "actual_wdl": "胜",
            "actual_handicap": "(-0.5)胜",
            "actual_score": "2:1",
            "actual_total_goals": 3,
            "handicap": -0.5
        },
        "wdl_history": [
            {"timestamp": "2025-10-18 10:00:00", "win_a": 1.80, "draw": 3.40, "win_b": 3.60},
            {"timestamp": "2025-10-20 19:00:00", "win_a": 1.75, "draw": 3.50, "win_b": 3.80}
        ],
        "handicap_history": [
            {"timestamp": "2025-10-18 10:00:00", "hcp_win": 2.00, "hcp_draw": 3.30, "hcp_lose": 3.00}
        ],
        "total_goals_history": [
            {"timestamp": "2025-10-18 10:00:00", "goals_0": 15.00, "goals_1": 6.00, "goals_2": 3.50,
             "goals_3": 3.00, "goals_4": 4.00, "goals_5": 6.50, "goals_6": 10.00, "goals_7_plus": 15.00}
        ],
        "score_history": [
            {"timestamp": "2025-10-18 10:00:00", "score": "2:1", "odds": 7.00}
        ]
    }
    
    with open("sample_match_data.json", "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)
    
    print("示例JSON文件已生成: sample_match_data.json")

def main():
    parser = argparse.ArgumentParser(description="导入比赛数据到数据库")
    parser.add_argument("--json", type=str, help="包含比赛数据的JSON文件路径")
    parser.add_argument("--generate", action="store_true", help="生成示例JSON模板")
    parser.add_argument("--analyze", type=str, help="分析指定match_id的赔率")
    parser.add_argument("--stats", action="store_true", help="显示全库统计")
    
    args = parser.parse_args()
    
    if args.generate:
        generate_sample_json()
        return
    
    if args.analyze:
        analyze_match_odds(args.analyze)
        return
    
    if args.stats:
        update_backtest_stats()
        return
    
    if args.json:
        with open(args.json, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        match_data = data['match_data']
        wdl_history = data.get('wdl_history', [])
        handicap_history = data.get('handicap_history', [])
        total_goals_history = data.get('total_goals_history', [])
        score_history = data.get('score_history', [])
        
        match_id = import_match_from_data(match_data, wdl_history, handicap_history, total_goals_history, score_history)
        analyze_match_odds(match_id)
        update_backtest_stats()
    else:
        print("请指定 --json 参数或使用 --generate 生成示例")
        parser.print_help()

if __name__ == "__main__":
    main()