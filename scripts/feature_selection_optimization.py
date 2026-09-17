import os
import sys
import pandas as pd
import numpy as np
import sqlite3
import yaml
import json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'five_leagues.db')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.yaml')
FEATURE_IMPORTANCE_FILE = os.path.join(PROJECT_ROOT, 'data', 'feature_importance_history.json')

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

def create_feature_importance_table():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS feature_importance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feature_name TEXT,
                importance REAL,
                importance_type TEXT,
                ranking INTEGER,
                training_date TEXT,
                model_type TEXT,
                decay_weight REAL DEFAULT 1.0,
                cumulative_importance REAL DEFAULT 0.0,
                usage_count INTEGER DEFAULT 0,
                last_used_date TEXT,
                is_selected INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_feature_name ON feature_importance(feature_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_training_date ON feature_importance(training_date)
        """)
        conn.commit()
        conn.close()
        print('特征重要性表创建成功')
    except Exception as e:
        print(f'创建特征重要性表失败: {e}')

def calculate_feature_importance(model, feature_names, model_type='xgboost'):
    if model is None:
        return pd.DataFrame()
    
    if model_type == 'xgboost':
        try:
            if hasattr(model, 'get_score'):
                importance = model.get_score(importance_type='gain')
            elif hasattr(model, 'get_booster'):
                importance = model.get_booster().get_score(importance_type='gain')
            else:
                return pd.DataFrame()
            
            feature_name_map = {f'f{i}': name for i, name in enumerate(feature_names)}
            
            mapped_names = []
            mapped_importance = []
            for key, val in importance.items():
                if key.startswith('f'):
                    idx = int(key[1:])
                    if idx < len(feature_names):
                        mapped_names.append(feature_names[idx])
                        mapped_importance.append(val)
                    else:
                        mapped_names.append(key)
                        mapped_importance.append(val)
                else:
                    mapped_names.append(key)
                    mapped_importance.append(val)
            
            importance_df = pd.DataFrame({
                'feature_name': mapped_names,
                'importance': mapped_importance
            })
        except:
            return pd.DataFrame()
    elif model_type == 'lightgbm':
        try:
            importance = model.feature_importance(importance_type='gain')
            importance_df = pd.DataFrame({
                'feature_name': feature_names,
                'importance': importance
            })
        except:
            return pd.DataFrame()
    else:
        return pd.DataFrame()
    
    importance_df['importance_type'] = 'gain'
    importance_df['model_type'] = model_type
    importance_df['training_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    importance_df = importance_df.sort_values('importance', ascending=False)
    importance_df['ranking'] = range(1, len(importance_df) + 1)
    
    total_importance = importance_df['importance'].sum()
    if total_importance > 0:
        importance_df['importance'] = importance_df['importance'] / total_importance
    
    return importance_df

def calculate_mutual_info(X, y):
    from sklearn.feature_selection import mutual_info_classif
    
    X_clean = X.fillna(0).values
    mi_scores = mutual_info_classif(X_clean, y, random_state=42)
    
    mi_df = pd.DataFrame({
        'feature_name': X.columns,
        'importance': mi_scores,
        'importance_type': 'mutual_info',
        'model_type': 'feature_selection',
        'training_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })
    
    mi_df = mi_df.sort_values('importance', ascending=False)
    mi_df['ranking'] = range(1, len(mi_df) + 1)
    
    total_importance = mi_df['importance'].sum()
    if total_importance > 0:
        mi_df['importance'] = mi_df['importance'] / total_importance
    
    return mi_df

def update_feature_importance_history(importance_df):
    conn = sqlite3.connect(DB_PATH)
    
    for _, row in importance_df.iterrows():
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, cumulative_importance, usage_count FROM feature_importance 
            WHERE feature_name = ? AND model_type = ?
        """, (row['feature_name'], row['model_type']))
        
        existing = cursor.fetchone()
        
        if existing:
            new_cumulative = existing[1] * 0.9 + row['importance'] * 0.1
            new_usage_count = existing[2] + 1
            
            cursor.execute("""
                UPDATE feature_importance SET
                    importance = ?, ranking = ?, training_date = ?,
                    cumulative_importance = ?, usage_count = ?,
                    last_used_date = ?, updated_at = ?,
                    is_selected = CASE WHEN ? > 0.01 THEN 1 ELSE 0 END
                WHERE id = ?
            """, (
                row['importance'], row['ranking'], row['training_date'],
                new_cumulative, new_usage_count,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                new_cumulative, existing[0]
            ))
        else:
            cursor.execute("""
                INSERT INTO feature_importance (
                    feature_name, importance, importance_type, ranking,
                    training_date, model_type, cumulative_importance,
                    usage_count, last_used_date, is_selected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row['feature_name'], row['importance'], row['importance_type'],
                row['ranking'], row['training_date'], row['model_type'],
                row['importance'], 1,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                1 if row['importance'] > 0.01 else 0
            ))
        
        conn.commit()
    
    conn.close()
    print(f'已更新 {len(importance_df)} 个特征的重要性记录')

def get_selected_features(min_importance=0.01, min_usage_count=3):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT feature_name, cumulative_importance, usage_count 
            FROM feature_importance 
            WHERE cumulative_importance >= ? AND usage_count >= ?
            ORDER BY cumulative_importance DESC
        """, (min_importance, min_usage_count))
        
        results = cursor.fetchall()
        conn.close()
        
        return [row[0] for row in results]
    except Exception as e:
        print(f'获取选中特征失败: {e}')
        return []

def get_feature_importance_summary():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(DISTINCT feature_name) FROM feature_importance")
        total_features = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM feature_importance WHERE is_selected = 1")
        selected_count = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT feature_name, ROUND(cumulative_importance, 4), usage_count, is_selected
            FROM feature_importance
            ORDER BY cumulative_importance DESC
            LIMIT 20
        """)
        top_features = cursor.fetchall()
        
        cursor.execute("SELECT MAX(training_date) FROM feature_importance")
        latest_date = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_features': total_features,
            'selected_features': selected_count,
            'top_features': top_features,
            'latest_update': latest_date
        }
    except Exception as e:
        print(f'获取特征重要性摘要失败: {e}')
        return {'total_features': 0, 'selected_features': 0, 'top_features': [], 'latest_update': None}

def apply_feature_decay(half_life_days=30):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, cumulative_importance, last_used_date FROM feature_importance
        WHERE last_used_date IS NOT NULL
    """)
    
    rows = cursor.fetchall()
    
    for row in rows:
        feature_id, current_importance, last_used = row
        
        if last_used:
            try:
                last_date = datetime.strptime(last_used, '%Y-%m-%d %H:%M:%S')
                days_diff = (datetime.now() - last_date).days
                
                if days_diff > 0:
                    decay_factor = np.exp(-days_diff * np.log(2) / half_life_days)
                    new_importance = current_importance * decay_factor
                    
                    cursor.execute("""
                        UPDATE feature_importance SET
                            cumulative_importance = ?,
                            is_selected = CASE WHEN ? > 0.01 THEN 1 ELSE 0 END,
                            updated_at = ?
                        WHERE id = ?
                    """, (
                        new_importance,
                        new_importance,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        feature_id
                    ))
            except:
                pass
    
    conn.commit()
    conn.close()
    print('特征重要性衰减更新完成')

def save_importance_to_file(importance_df, filename=None):
    if filename is None:
        filename = FEATURE_IMPORTANCE_FILE
    
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            history = json.load(f)
    else:
        history = []
    
    record = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'features': importance_df.to_dict('records')
    }
    
    history.append(record)
    
    if len(history) > 50:
        history = history[-50:]
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    
    print(f'特征重要性历史已保存到 {filename}')

def load_importance_from_file(filename=None):
    if filename is None:
        filename = FEATURE_IMPORTANCE_FILE
    
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def analyze_feature_drift(importance_df, threshold=0.2):
    history = load_importance_from_file()
    
    if len(history) < 2:
        return {'drift_detected': False, 'drifting_features': []}
    
    recent_importance = {row['feature_name']: row['importance'] for row in importance_df.to_dict('records')}
    
    previous_record = history[-1]
    previous_importance = {row['feature_name']: row['importance'] for row in previous_record['features']}
    
    drifting_features = []
    
    for feature, current_imp in recent_importance.items():
        if feature in previous_importance:
            prev_imp = previous_importance[feature]
            if prev_imp > 0:
                change_ratio = abs(current_imp - prev_imp) / prev_imp
                if change_ratio > threshold:
                    drifting_features.append({
                        'feature_name': feature,
                        'current_importance': current_imp,
                        'previous_importance': prev_imp,
                        'change_ratio': change_ratio,
                        'drift_type': 'up' if current_imp > prev_imp else 'down'
                    })
    
    return {
        'drift_detected': len(drifting_features) > 0,
        'drifting_features': drifting_features,
        'total_features': len(recent_importance),
        'drifting_count': len(drifting_features)
    }

def optimize_feature_selection(X, y, models=None, feature_names=None):
    print('========== 特征选择优化 ==========')
    
    create_feature_importance_table()
    
    if feature_names is None:
        feature_names = X.columns.tolist()
    
    all_importance = []
    
    mi_df = calculate_mutual_info(X, y)
    if not mi_df.empty:
        all_importance.append(mi_df)
        print(f'互信息特征重要性计算完成，共 {len(mi_df)} 个特征')
    
    if models is not None:
        for model_info in models:
            model = model_info.get('model')
            model_type = model_info.get('type', 'xgboost')
            
            if model is not None:
                imp_df = calculate_feature_importance(model, feature_names, model_type)
                if not imp_df.empty:
                    all_importance.append(imp_df)
                    print(f'{model_type} 特征重要性计算完成，共 {len(imp_df)} 个特征')
    
    if all_importance:
        combined_df = pd.concat(all_importance)
        combined_df = combined_df.groupby('feature_name').agg({
            'importance': 'mean',
            'model_type': lambda x: ', '.join(set(x))
        }).reset_index()
        
        combined_df['importance_type'] = 'combined'
        combined_df['training_date'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        combined_df = combined_df.sort_values('importance', ascending=False)
        combined_df['ranking'] = range(1, len(combined_df) + 1)
        
        update_feature_importance_history(combined_df)
        save_importance_to_file(combined_df)
        
        drift_result = analyze_feature_drift(combined_df)
        if drift_result['drift_detected']:
            print(f'检测到 {drift_result["drifting_count"]} 个特征发生漂移')
            for feat in drift_result['drifting_features'][:5]:
                print(f'  {feat["feature_name"]}: {feat["drift_type"]} ({feat["change_ratio"]:.2%})')
        
        apply_feature_decay()
        
        summary = get_feature_importance_summary()
        print(f'\n特征重要性摘要:')
        print(f'  总特征数: {summary["total_features"]}')
        print(f'  选中特征数: {summary["selected_features"]}')
        print(f'  最新更新: {summary["latest_update"]}')
        print(f'\nTop 10 重要特征:')
        for feat in summary['top_features'][:10]:
            print(f'  {feat[0]}: {feat[1]:.4f} (使用次数: {feat[2]})')
        
        return combined_df
    
    return pd.DataFrame()

if __name__ == '__main__':
    create_feature_importance_table()
    
    summary = get_feature_importance_summary()
    print('特征重要性系统初始化完成')
    print(f'总特征数: {summary["total_features"]}')
    print(f'选中特征数: {summary["selected_features"]}')
