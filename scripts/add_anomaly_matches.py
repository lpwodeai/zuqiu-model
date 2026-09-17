import sqlite3
import numpy as np
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"
ODDS_DB_PATH = BASE_DIR / "data" / "odds.db"

ANOMALY_MATCHES = [
    {
        'match_id': '2025-10-18_Burnley_Leeds_United',
        'home_team_name': 'Burnley',
        'away_team_name': 'Leeds United',
        'date': '2025-10-18 22:00:00',
        'competition_name': 'Premier League',
        'homeGoals': 2,
        'awayGoals': 0,
        'actual_result': 2,
        'model_prediction': 0,
        'model_prob_home': 0.25,
        'model_prob_draw': 0.31,
        'model_prob_away': 0.44,
        'odds_implied_home': 0.25,
        'odds_implied_draw': 0.31,
        'odds_implied_away': 0.44,
        'prediction_error': 0.75,
        'model_odds_divergence': 0.0,
        'confidence_score': 0.44,
        'anomaly_type': 'model_odds_conflict',
        'anomaly_score': 0.85,
        'description': '冷门: 主队2:0获胜，市场预测客队胜率44.3%'
    },
    {
        'match_id': '2025-10-18_Crystal_Palace_Bournemouth',
        'home_team_name': 'Crystal Palace',
        'away_team_name': 'Bournemouth',
        'date': '2025-10-18 22:00:00',
        'competition_name': 'Premier League',
        'homeGoals': 3,
        'awayGoals': 3,
        'actual_result': 1,
        'model_prediction': 1,
        'model_prob_home': 0.45,
        'model_prob_draw': 0.29,
        'model_prob_away': 0.26,
        'odds_implied_home': 0.45,
        'odds_implied_draw': 0.29,
        'odds_implied_away': 0.26,
        'prediction_error': 0.71,
        'model_odds_divergence': 0.0,
        'confidence_score': 0.45,
        'anomaly_type': 'high_confidence_error',
        'anomaly_score': 0.78,
        'description': '平局冷门: 3:3平局，市场预测主队胜率44.5%'
    },
    {
        'match_id': '2025-10-18_Manchester_City_Everton',
        'home_team_name': 'Manchester City',
        'away_team_name': 'Everton',
        'date': '2025-10-18 22:00:00',
        'competition_name': 'Premier League',
        'homeGoals': 2,
        'awayGoals': 0,
        'actual_result': 2,
        'model_prediction': 0,
        'model_prob_home': 0.69,
        'model_prob_draw': 0.19,
        'model_prob_away': 0.12,
        'odds_implied_home': 0.69,
        'odds_implied_draw': 0.19,
        'odds_implied_away': 0.12,
        'prediction_error': 0.88,
        'model_odds_divergence': 0.0,
        'confidence_score': 0.69,
        'anomaly_type': 'high_confidence_error',
        'anomaly_score': 0.92,
        'description': '高置信度错误: 曼城2:0获胜，赔率隐含主胜69.2%，但赔率趋势系统预测客胜'
    },
    {
        'match_id': '2025-10-18_Sunderland_Wolverhampton_Wanderers',
        'home_team_name': 'Sunderland',
        'away_team_name': 'Wolverhampton Wanderers',
        'date': '2025-10-18 22:00:00',
        'competition_name': 'Premier League',
        'homeGoals': 2,
        'awayGoals': 0,
        'actual_result': 2,
        'model_prediction': 0,
        'model_prob_home': 0.35,
        'model_prob_draw': 0.31,
        'model_prob_away': 0.34,
        'odds_implied_home': 0.35,
        'odds_implied_draw': 0.31,
        'odds_implied_away': 0.34,
        'prediction_error': 0.65,
        'model_odds_divergence': 0.0,
        'confidence_score': 0.37,
        'anomaly_type': 'model_odds_conflict',
        'anomaly_score': 0.88,
        'description': '冷门: 桑德兰2:0获胜，主胜赔率从2.30升至2.50诱盘，客胜赔率从2.90降至2.65'
    },
    {
        'match_id': '2025-10-18_Brighton_Newcastle',
        'home_team_name': 'Brighton & Hove Albion',
        'away_team_name': 'Newcastle United',
        'date': '2025-10-18 22:00:00',
        'competition_name': 'Premier League',
        'homeGoals': 2,
        'awayGoals': 1,
        'actual_result': 2,
        'model_prediction': 1,
        'model_prob_home': 0.31,
        'model_prob_draw': 0.29,
        'model_prob_away': 0.40,
        'odds_implied_home': 0.31,
        'odds_implied_draw': 0.29,
        'odds_implied_away': 0.40,
        'prediction_error': 0.69,
        'model_odds_divergence': 0.0,
        'confidence_score': 0.48,
        'anomaly_type': 'model_odds_conflict',
        'anomaly_score': 0.85,
        'description': '冷门: 布莱顿2:1获胜，主胜赔率从2.48升至2.84诱盘，客胜赔率从2.37降至2.23'
    }
]

def calculate_prediction_error(model_probs, actual_result):
    if actual_result == 2:
        return 1 - model_probs[2]
    elif actual_result == 1:
        return 1 - model_probs[1]
    elif actual_result == 0:
        return 1 - model_probs[0]
    return 1.0

def calculate_model_odds_divergence(model_probs, odds_probs):
    kl_div = 0
    for i in range(3):
        if model_probs[i] > 0 and odds_probs[i] > 0:
            kl_div += model_probs[i] * np.log(model_probs[i] / odds_probs[i])
    return kl_div

def create_anomaly_table(conn):
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            date TEXT,
            home_team_name TEXT,
            away_team_name TEXT,
            competition_name TEXT,
            homeGoals INTEGER,
            awayGoals INTEGER,
            model_prediction INTEGER,
            model_prob_home REAL,
            model_prob_draw REAL,
            model_prob_away REAL,
            odds_implied_home REAL,
            odds_implied_draw REAL,
            odds_implied_away REAL,
            prediction_error REAL,
            model_odds_divergence REAL,
            confidence_score REAL,
            anomaly_type TEXT,
            anomaly_score REAL,
            feedback_count INTEGER DEFAULT 0,
            last_feedback_date TEXT,
            description TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("ALTER TABLE anomaly_matches ADD COLUMN description TEXT")
    except sqlite3.OperationalError:
        pass
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_date ON anomaly_matches(date)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomaly_type ON anomaly_matches(anomaly_type)
    """)
    conn.commit()

def add_anomaly_matches():
    conn = sqlite3.connect(DB_PATH)
    create_anomaly_table(conn)
    cursor = conn.cursor()
    
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    insert_count = 0
    update_count = 0
    
    for match in ANOMALY_MATCHES:
        cursor.execute("""
            SELECT id FROM anomaly_matches 
            WHERE home_team_name = ? AND away_team_name = ? AND date LIKE ?
        """, (match['home_team_name'], match['away_team_name'], match['date'][:10] + '%'))
        existing = cursor.fetchone()
        
        model_probs = [match['model_prob_away'], match['model_prob_draw'], match['model_prob_home']]
        odds_probs = [match['odds_implied_away'], match['odds_implied_draw'], match['odds_implied_home']]
        prediction_error = calculate_prediction_error(model_probs, match['actual_result'])
        divergence = calculate_model_odds_divergence(model_probs, odds_probs)
        
        if existing:
            cursor.execute("""
                UPDATE anomaly_matches SET
                    homeGoals = ?, awayGoals = ?,
                    model_prediction = ?, model_prob_home = ?, 
                    model_prob_draw = ?, model_prob_away = ?,
                    odds_implied_home = ?, odds_implied_draw = ?, odds_implied_away = ?,
                    prediction_error = ?, model_odds_divergence = ?,
                    confidence_score = ?, anomaly_type = ?, anomaly_score = ?,
                    feedback_count = feedback_count + 1,
                    last_feedback_date = ?, updated_at = ?,
                    description = ?
                WHERE id = ?
            """, (
                match['homeGoals'], match['awayGoals'],
                match['model_prediction'], match['model_prob_home'],
                match['model_prob_draw'], match['model_prob_away'],
                match['odds_implied_home'], match['odds_implied_draw'],
                match['odds_implied_away'], prediction_error,
                divergence, match['confidence_score'],
                match['anomaly_type'], match['anomaly_score'],
                current_time, current_time,
                match['description'],
                existing[0]
            ))
            update_count += 1
        else:
            cursor.execute("""
                INSERT INTO anomaly_matches (
                    match_id, date, home_team_name, away_team_name,
                    competition_name, homeGoals, awayGoals,
                    model_prediction, model_prob_home, model_prob_draw, model_prob_away,
                    odds_implied_home, odds_implied_draw, odds_implied_away,
                    prediction_error, model_odds_divergence,
                    confidence_score, anomaly_type, anomaly_score,
                    feedback_count, last_feedback_date,
                    description, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                match['match_id'], match['date'], match['home_team_name'], match['away_team_name'],
                match['competition_name'], match['homeGoals'], match['awayGoals'],
                match['model_prediction'], match['model_prob_home'], match['model_prob_draw'], match['model_prob_away'],
                match['odds_implied_home'], match['odds_implied_draw'], match['odds_implied_away'],
                prediction_error, divergence,
                match['confidence_score'], match['anomaly_type'], match['anomaly_score'],
                1, current_time,
                match['description'], current_time, current_time
            ))
            insert_count += 1
    
    conn.commit()
    conn.close()
    
    print("=" * 60)
    print("异常样本添加完成")
    print("=" * 60)
    print(f"\n新增记录: {insert_count} 条")
    print(f"更新记录: {update_count} 条")
    print("\n新增的异常样本:")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    for match in ANOMALY_MATCHES:
        cursor.execute("""
            SELECT * FROM anomaly_matches 
            WHERE home_team_name = ? AND away_team_name = ? AND date LIKE ?
        """, (match['home_team_name'], match['away_team_name'], match['date'][:10] + '%'))
        record = cursor.fetchone()
        if record:
            print(f"\n  {match['home_team_name']} vs {match['away_team_name']}")
            print(f"    日期: {match['date']}")
            print(f"    比分: {match['homeGoals']}:{match['awayGoals']}")
            print(f"    异常类型: {match['anomaly_type']}")
            print(f"    异常分数: {match['anomaly_score']:.2f}")
            print(f"    描述: {match['description']}")
    conn.close()
    
    print("\n" + "=" * 60)

def verify_anomaly_table():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM anomaly_matches')
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT anomaly_type, COUNT(*) FROM anomaly_matches GROUP BY anomaly_type")
    types = cursor.fetchall()
    
    cursor.execute("SELECT * FROM anomaly_matches ORDER BY date DESC LIMIT 5")
    recent = cursor.fetchall()
    
    conn.close()
    
    print(f"\n验证结果:")
    print(f"  anomaly_matches表总记录数: {total}")
    print(f"\n  异常类型分布:")
    for anomaly_type, count in types:
        print(f"    {anomaly_type}: {count} 条")
    
    print(f"\n  最近5条记录:")
    for rec in recent:
        print(f"    {rec[3]} vs {rec[4]} - {rec[1]} - {rec[18]}")

if __name__ == "__main__":
    add_anomaly_matches()
    verify_anomaly_table()