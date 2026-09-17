import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "anomaly_samples.db"

def init_anomaly_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            match_date TEXT,
            actual_wdl TEXT,
            actual_score TEXT,
            ml_prediction TEXT,
            ml_confidence REAL,
            ml_probabilities TEXT,
            odds_prediction TEXT,
            odds_confidence REAL,
            fusion_prediction TEXT,
            anomaly_type TEXT,
            confidence_diff REAL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    print("异常样本库表结构初始化完成")

def add_anomaly_sample(match_id, home_team, away_team, match_date,
                       actual_wdl, actual_score,
                       ml_prediction=None, ml_confidence=None, ml_probabilities=None,
                       odds_prediction=None, odds_confidence=None,
                       fusion_prediction=None,
                       anomaly_type='爆冷', confidence_diff=None, notes=None):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    anomaly_types = ['ML错误', '赔率错误', '融合错误', '爆冷', '双重错误', '三重重错误']
    if anomaly_type not in anomaly_types:
        anomaly_type = '爆冷'
    
    cursor.execute("""
        INSERT OR REPLACE INTO anomaly_samples (
            match_id, home_team, away_team, match_date,
            actual_wdl, actual_score,
            ml_prediction, ml_confidence, ml_probabilities,
            odds_prediction, odds_confidence,
            fusion_prediction,
            anomaly_type, confidence_diff, notes,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        match_id, home_team, away_team, match_date,
        actual_wdl, actual_score,
        ml_prediction, ml_confidence, 
        pd.Series(ml_probabilities).to_json() if ml_probabilities else None,
        odds_prediction, odds_confidence,
        fusion_prediction,
        anomaly_type, confidence_diff, notes,
        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ))
    
    conn.commit()
    conn.close()
    print(f"异常样本已添加: {home_team} vs {away_team}")

def get_anomaly_samples(anomaly_type=None):
    conn = sqlite3.connect(DB_PATH)
    
    query = "SELECT * FROM anomaly_samples"
    params = []
    
    if anomaly_type:
        query += " WHERE anomaly_type = ?"
        params.append(anomaly_type)
    
    query += " ORDER BY match_date DESC"
    df = pd.read_sql(query, conn, params=params)
    conn.close()
    
    return df

def get_anomaly_stats():
    conn = sqlite3.connect(DB_PATH)
    
    stats = {}
    
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM anomaly_samples")
    stats['total'] = cursor.fetchone()[0]
    
    cursor.execute("SELECT anomaly_type, COUNT(*) FROM anomaly_samples GROUP BY anomaly_type")
    stats['by_type'] = dict(cursor.fetchall())
    
    cursor.execute("""
        SELECT 
            CASE 
                WHEN ml_prediction != actual_wdl AND odds_prediction != actual_wdl THEN 'ML+赔率都错'
                WHEN ml_prediction != actual_wdl THEN '仅ML错'
                WHEN odds_prediction != actual_wdl THEN '仅赔率错'
                ELSE '都没错'
            END as error_type,
            COUNT(*) 
        FROM anomaly_samples 
        WHERE ml_prediction IS NOT NULL AND odds_prediction IS NOT NULL
        GROUP BY error_type
    """)
    stats['error_distribution'] = dict(cursor.fetchall())
    
    conn.close()
    
    return stats

def batch_add_from_backtest(results):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    added_count = 0
    
    for r in results:
        match_info = r.get('match_info', {})
        match_id = r.get('match_id')
        if not match_id:
            continue
        
        home_team = match_info.get('home_team')
        away_team = match_info.get('away_team')
        match_date = match_info.get('match_date')
        actual_wdl = match_info.get('actual_wdl')
        actual_score = match_info.get('actual_score')
        
        ml_pred = r.get('ml_prediction', {})
        ml_prediction = ml_pred.get('prediction')
        ml_confidence = ml_pred.get('confidence')
        ml_probabilities = ml_pred.get('probabilities')
        
        odds_analysis = r.get('odds_analysis', {}).get('wdl', {})
        odds_prediction = odds_analysis.get('signal_label')
        odds_confidence = odds_analysis.get('confidence')
        
        fusion_prediction = r.get('final_prediction')
        
        if actual_wdl in ['胜', '平', '负']:
            ml_error = ml_prediction in ['胜', '平', '负'] and ml_prediction != actual_wdl
            odds_error = odds_prediction in ['胜', '平', '负'] and odds_prediction != actual_wdl
            fusion_error = fusion_prediction in ['胜', '平', '负'] and fusion_prediction != actual_wdl
            
            if ml_error or odds_error or fusion_error:
                if ml_error and odds_error and fusion_error:
                    anomaly_type = '三重重错误'
                elif ml_error and odds_error:
                    anomaly_type = '双重错误'
                elif ml_error:
                    anomaly_type = 'ML错误'
                elif odds_error:
                    anomaly_type = '赔率错误'
                else:
                    anomaly_type = '融合错误'
                
                confidence_diff = None
                if ml_confidence and ml_prediction != actual_wdl:
                    confidence_diff = -ml_confidence
                
                notes = []
                if ml_error:
                    notes.append(f"ML预测{ml_prediction}，实际{actual_wdl}")
                if odds_error:
                    notes.append(f"赔率预测{odds_prediction}，实际{actual_wdl}")
                if fusion_error:
                    notes.append(f"融合预测{fusion_prediction}，实际{actual_wdl}")
                notes = '; '.join(notes)
                
                cursor.execute("""
                    INSERT OR IGNORE INTO anomaly_samples (
                        match_id, home_team, away_team, match_date,
                        actual_wdl, actual_score,
                        ml_prediction, ml_confidence, ml_probabilities,
                        odds_prediction, odds_confidence,
                        fusion_prediction,
                        anomaly_type, confidence_diff, notes,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    match_id, home_team, away_team, match_date,
                    actual_wdl, actual_score,
                    ml_prediction, ml_confidence, 
                    pd.Series(ml_probabilities).to_json() if ml_probabilities else None,
                    odds_prediction, odds_confidence,
                    fusion_prediction,
                    anomaly_type, confidence_diff, notes,
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                ))
                added_count += 1
    
    conn.commit()
    conn.close()
    print(f"批量添加完成，共添加 {added_count} 条异常样本")
    
    return added_count

if __name__ == "__main__":
    init_anomaly_database()
    
    stats = get_anomaly_stats()
    print("\n异常样本库统计:")
    print(f"  总样本数: {stats['total']}")
    print(f"  按类型分布: {stats.get('by_type', {})}")
    print(f"  错误分布: {stats.get('error_distribution', {})}")
    
    df = get_anomaly_samples()
    if not df.empty:
        print(f"\n最近异常样本 ({len(df)} 条):")
        print(df[['match_date', 'home_team', 'away_team', 'actual_wdl', 'ml_prediction', 'anomaly_type']].to_string())
