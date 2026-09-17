import pandas as pd
import numpy as np
import json
import os
import sqlite3
from datetime import datetime

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    print("Warning: xgboost not installed")

try:
    import shap
    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False
    print("Warning: shap not installed, will skip SHAP analysis")

from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent.parent
MODEL_DIR = BASE_DIR / "assets"
DB_PATH = BASE_DIR / "data" / "odds.db"

from feature_utils import load_match_data, load_match_data_odds, build_features, build_team_features

def load_latest_model():
    """加载最新训练的模型和相关文件"""
    pkl_files = [f for f in os.listdir(MODEL_DIR) if f.endswith('_model_') and f.endswith('.pkl')]
    if not pkl_files:
        pkl_files = [f for f in os.listdir(MODEL_DIR) if f.endswith('.pkl') and 'model' in f.lower()]
    
    if not pkl_files:
        print("未找到模型文件")
        return None, None, None
    
    pkl_files.sort(key=lambda x: os.path.getmtime(os.path.join(MODEL_DIR, x)), reverse=True)
    model_file = pkl_files[0]
    
    timestamp = model_file.split('_')[2].replace('.pkl', '') if '_model_' in model_file else 'unknown'
    
    scaler_file = f"scaler_{timestamp}.pkl"
    features_file = f"features_{timestamp}.txt"
    
    model_path = os.path.join(MODEL_DIR, model_file)
    scaler_path = os.path.join(MODEL_DIR, scaler_file)
    features_path = os.path.join(MODEL_DIR, features_file)
    
    import joblib
    model = joblib.load(model_path)
    
    scaler = None
    if os.path.exists(scaler_path):
        scaler = joblib.load(scaler_path)
    
    selected_features = None
    if os.path.exists(features_path):
        with open(features_path, 'r') as f:
            selected_features = [line.strip() for line in f if line.strip()]
    
    print(f"加载模型: {model_file}")
    print(f"加载标准化器: {scaler_file if os.path.exists(scaler_path) else '未找到'}")
    print(f"加载特征列表: {features_file if os.path.exists(features_path) else '未找到'}")
    
    return model, scaler, selected_features

def prepare_shap_data(selected_features):
    """准备用于SHAP分析的数据"""
    df = load_match_data_odds()
    
    home_teams = df['home_team'].unique()
    away_teams = df['away_team'].unique()
    
    team_features = {}
    for team in set(list(home_teams) + list(away_teams)):
        team_features[team] = build_team_features(team, df)
    
    features_list = []
    for idx, row in df.iterrows():
        try:
            home_team = row['home_team']
            away_team = row['away_team']
            
            home_features = team_features.get(home_team, {})
            away_features = team_features.get(away_team, {})
            
            features = build_features(row, home_features, away_features)
            
            if selected_features:
                features = {k: v for k, v in features.items() if k in selected_features}
            
            features_list.append(features)
        except Exception as e:
            continue
    
    feature_df = pd.DataFrame(features_list)
    
    if selected_features:
        for feature in selected_features:
            if feature not in feature_df.columns:
                feature_df[feature] = 0
        feature_df = feature_df[selected_features]
    
    feature_df = feature_df.fillna(0)
    
    return feature_df

def compute_shap_importance(model, scaler, feature_df, top_n=20):
    """计算SHAP特征重要性"""
    if not SHAP_AVAILABLE:
        print("SHAP未安装，跳过SHAP分析")
        return None, None
    
    if isinstance(model, xgb.Booster):
        explainer = shap.TreeExplainer(model)
        X = feature_df.values
        if scaler:
            X = scaler.transform(feature_df)
        shap_values = explainer.shap_values(X)
    else:
        try:
            explainer = shap.KernelExplainer(model.predict_proba, feature_df.sample(min(100, len(feature_df))))
            shap_values = explainer.shap_values(feature_df)
        except:
            print("无法创建SHAP解释器")
            return None, None
    
    if isinstance(shap_values, list):
        shap_values = np.array(shap_values)
    
    feature_importance = {}
    for i, feature in enumerate(feature_df.columns):
        if len(shap_values.shape) == 3:
            importance = np.mean(np.abs(shap_values[:, :, i]))
        else:
            importance = np.mean(np.abs(shap_values[:, i]))
        feature_importance[feature] = importance
    
    sorted_features = sorted(feature_importance.items(), key=lambda x: -x[1])
    
    print(f"\n=== SHAP特征重要性 Top-{top_n} ===")
    for i, (feature, importance) in enumerate(sorted_features[:top_n]):
        print(f"{i+1:2d}. {feature}: {importance:.6f}")
    
    return sorted_features, shap_values

def detect_feature_drift(feature_df, window_size=50, threshold=0.1):
    """检测特征漂移"""
    drift_results = {}
    
    for feature in feature_df.columns:
        series = feature_df[feature].dropna()
        if len(series) < window_size * 2:
            continue
        
        rolling_mean = series.rolling(window=window_size).mean()
        rolling_std = series.rolling(window=window_size).std()
        
        recent_mean = rolling_mean.iloc[-window_size:].mean()
        recent_std = rolling_std.iloc[-window_size:].mean()
        
        historical_mean = rolling_mean.iloc[:-window_size].mean()
        historical_std = rolling_std.iloc[:-window_size].mean()
        
        mean_diff = abs(recent_mean - historical_mean) / (historical_std + 0.0001) if historical_std > 0 else float('inf')
        std_diff = abs(recent_std - historical_std) / (historical_std + 0.0001) if historical_std > 0 else float('inf')
        
        drift_severity = max(mean_diff, std_diff)
        
        drift_results[feature] = {
            'mean_diff': mean_diff,
            'std_diff': std_diff,
            'drift_severity': drift_severity,
            'recent_mean': recent_mean,
            'historical_mean': historical_mean,
            'recent_std': recent_std,
            'historical_std': historical_std
        }
    
    sorted_drift = sorted(drift_results.items(), key=lambda x: -x[1]['drift_severity'])
    
    print("\n=== 特征漂移检测结果 ===")
    print(f"阈值: {threshold}")
    drifted_features = []
    for feature, stats in sorted_drift[:10]:
        status = "⚠️ 漂移" if stats['drift_severity'] > threshold else "✅ 正常"
        print(f"{status} {feature}: 漂移程度={stats['drift_severity']:.4f}, "
              f"均值变化={stats['mean_diff']:.4f}, 方差变化={stats['std_diff']:.4f}")
        if stats['drift_severity'] > threshold:
            drifted_features.append(feature)
    
    return drift_results, drifted_features

def save_feature_importance(importance_list, drifted_features=None):
    """保存特征重要性结果到数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feature_importance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_name TEXT,
            importance REAL,
            rank INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            model_version TEXT
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feature_drift (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature_name TEXT,
            drift_severity REAL,
            mean_diff REAL,
            std_diff REAL,
            recent_mean REAL,
            historical_mean REAL,
            recent_std REAL,
            historical_std REAL,
            is_drifted BOOLEAN,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    model_version = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    for i, (feature, importance) in enumerate(importance_list):
        cursor.execute("""
            INSERT INTO feature_importance (feature_name, importance, rank, model_version)
            VALUES (?, ?, ?, ?)
        """, (feature, importance, i + 1, model_version))
    
    if drifted_features:
        for feature, stats in drifted_features.items():
            cursor.execute("""
                INSERT INTO feature_drift (
                    feature_name, drift_severity, mean_diff, std_diff,
                    recent_mean, historical_mean, recent_std, historical_std, is_drifted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                feature, stats['drift_severity'], stats['mean_diff'], stats['std_diff'],
                stats['recent_mean'], stats['historical_mean'], stats['recent_std'], stats['historical_std'],
                stats['drift_severity'] > 0.1
            ))
    
    conn.commit()
    conn.close()
    
    print(f"\n特征重要性数据已保存到数据库，模型版本: {model_version}")

def generate_report(importance_list, shap_values, feature_df, drifted_features):
    """生成特征分析报告"""
    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_features': len(feature_df.columns),
        'top_features': [],
        'drifted_features': [],
        'feature_categories': {
            'team_strength': [],
            'recent_form': [],
            'h2h': [],
            'league': [],
            'odds': [],
            'other': []
        }
    }
    
    for i, (feature, importance) in enumerate(importance_list[:15]):
        report['top_features'].append({
            'rank': i + 1,
            'feature': feature,
            'importance': importance
        })
        
        if 'attack' in feature.lower() or 'defence' in feature.lower() or 'rating' in feature.lower():
            report['feature_categories']['team_strength'].append(feature)
        elif 'form' in feature.lower() or 'streak' in feature.lower() or 'recent' in feature.lower():
            report['feature_categories']['recent_form'].append(feature)
        elif 'h2h' in feature.lower():
            report['feature_categories']['h2h'].append(feature)
        elif 'league' in feature.lower():
            report['feature_categories']['league'].append(feature)
        elif 'wdl_' in feature or 'hcp_' in feature or 'tg_' in feature:
            report['feature_categories']['odds'].append(feature)
        else:
            report['feature_categories']['other'].append(feature)
    
    if drifted_features:
        for feature, stats in sorted(drifted_features.items(), key=lambda x: -x[1]['drift_severity'])[:5]:
            report['drifted_features'].append({
                'feature': feature,
                'drift_severity': stats['drift_severity'],
                'mean_diff': stats['mean_diff'],
                'std_diff': stats['std_diff']
            })
    
    report_path = os.path.join(MODEL_DIR, f"feature_analysis_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n特征分析报告已保存到: {report_path}")
    
    return report

def main():
    print("=" * 80)
    print("SHAP特征重要性分析与漂移监测")
    print("=" * 80)
    
    model, scaler, selected_features = load_latest_model()
    if not model:
        print("无法加载模型，退出")
        return
    
    if not selected_features:
        print("未找到特征列表，退出")
        return
    
    print(f"\n准备SHAP分析数据...")
    feature_df = prepare_shap_data(selected_features)
    print(f"数据准备完成，样本数: {len(feature_df)}, 特征数: {len(feature_df.columns)}")
    
    print(f"\n计算SHAP特征重要性...")
    importance_list, shap_values = compute_shap_importance(model, scaler, feature_df)
    
    if importance_list:
        print(f"\n检测特征漂移...")
        drift_results, drifted_features_list = detect_feature_drift(feature_df)
        
        print(f"\n保存分析结果...")
        save_feature_importance(importance_list, drift_results)
        
        print(f"\n生成分析报告...")
        report = generate_report(importance_list, shap_values, feature_df, drift_results)
        
        print("\n" + "=" * 80)
        print("分析完成")
        print("=" * 80)
        print(f"Top-5特征: {[f[0] for f in importance_list[:5]]}")
        if drifted_features_list:
            print(f"检测到漂移的特征: {drifted_features_list}")
        else:
            print("未检测到显著特征漂移")

if __name__ == "__main__":
    main()