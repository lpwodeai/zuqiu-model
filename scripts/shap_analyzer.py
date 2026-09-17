import pandas as pd
import numpy as np
import json
import os
import yaml
from pathlib import Path

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    print("Warning: xgboost not installed")

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    print("Warning: lightgbm not installed")

try:
    import shap
except ImportError:
    shap = None
    print("Warning: shap not installed, will skip SHAP analysis")

from sklearn.preprocessing import StandardScaler
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.yaml"

from feature_utils import load_config, load_match_data, load_match_data_odds, build_features, build_team_features, build_all_features

CONFIG = load_config()

def train_model_for_shap(X_train, y_train, X_val, y_val, model_type='xgboost'):
    config = CONFIG.get('model', {}).get(model_type, {})
    
    if model_type == 'xgboost' and xgb is None:
        return None
    if model_type == 'lightgbm' and lgb is None:
        return None
    
    if model_type == 'xgboost':
        params = {
            'objective': 'multi:softprob',
            'num_class': 3,
            'eval_metric': 'mlogloss',
            'max_depth': config.get('max_depth', 3),
            'learning_rate': config.get('learning_rate', 0.05),
            'subsample': config.get('subsample', 0.7),
            'colsample_bytree': config.get('colsample_bytree', 0.7),
            'gamma': config.get('gamma', 0.2),
            'min_child_weight': config.get('min_child_weight', 5),
            'reg_alpha': config.get('reg_alpha', 0.3),
            'reg_lambda': config.get('reg_lambda', 5.0),
            'seed': 42,
            'nthread': 1
        }
        
        dtrain = xgb.DMatrix(X_train, label=y_train)
        dval = xgb.DMatrix(X_val, label=y_val)
        
        watchlist = [(dtrain, 'train'), (dval, 'val')]
        model = xgb.train(params, dtrain, num_boost_round=config.get('num_boost_round', 100),
                          evals=watchlist, early_stopping_rounds=config.get('early_stopping_rounds', 15),
                          verbose_eval=0)
    else:
        params = {
            'objective': 'multiclass',
            'num_class': 3,
            'metric': 'multi_logloss',
            'max_depth': config.get('max_depth', 3),
            'learning_rate': config.get('learning_rate', 0.05),
            'num_leaves': config.get('num_leaves', 15),
            'subsample': config.get('subsample', 0.7),
            'colsample_bytree': config.get('colsample_bytree', 0.7),
            'reg_alpha': config.get('reg_alpha', 0.3),
            'reg_lambda': config.get('reg_lambda', 5.0),
            'min_child_weight': config.get('min_child_weight', 5),
            'min_data_in_leaf': config.get('min_data_in_leaf', 20),
            'seed': 42,
            'verbose': 0
        }
        
        lgb_train = lgb.Dataset(X_train, y_train)
        lgb_val = lgb.Dataset(X_val, y_val, reference=lgb_train)
        
        callbacks = [lgb.early_stopping(stopping_rounds=config.get('early_stopping_rounds', 15)), lgb.log_evaluation(period=0)]
        
        model = lgb.train(params, lgb_train, num_boost_round=config.get('num_boost_round', 100),
                          valid_sets=[lgb_val], callbacks=callbacks)
    
    return model

def analyze_with_shap(model, X, feature_names, model_type, sample_size=500):
    if shap is None:
        print("SHAP not installed, skipping analysis")
        return None
    
    print(f"\n{'='*60}")
    print(f"SHAP分析 - {model_type.upper()}")
    print(f"{'='*60}")
    
    X_sample = X.sample(min(sample_size, len(X)), random_state=42)
    
    if model_type == 'xgboost':
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_sample)
    elif model_type == 'lightgbm':
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_sample)
    else:
        print(f"不支持的模型类型: {model_type}")
        return None
    
    result = {
        'feature_names': feature_names,
        'model_type': model_type,
        'summary': {}
    }
    
    shap_array = np.array(shap_values)
    if shap_array.ndim == 3:
        shap_values_list = [shap_array[:, :, i] for i in range(shap_array.shape[2])]
    else:
        shap_values_list = shap_values
    
    for class_idx in range(3):
        class_name = ['away_win', 'draw', 'home_win'][class_idx]
        class_shap = shap_values_list[class_idx]
        
        mean_abs_shap = np.abs(class_shap).mean(axis=0)
        feature_importance = pd.DataFrame({
            'feature': feature_names,
            'shap_importance': mean_abs_shap
        }).sort_values('shap_importance', ascending=False)
        
        result['summary'][class_name] = feature_importance.head(20).to_dict(orient='records')
        
        plt.figure(figsize=(12, 8))
        shap.summary_plot(class_shap, X_sample, feature_names=feature_names, max_display=15,
                          show=False, plot_type='bar')
        plt.title(f'SHAP Feature Importance - {class_name}')
        plt.tight_layout()
        plt.savefig(os.path.join(OUTPUT_DIR, f'shap_{model_type}_{class_name}_importance.png'))
        plt.close()
        
        plt.figure(figsize=(12, 8))
        shap.summary_plot(class_shap, X_sample, feature_names=feature_names, max_display=15,
                          show=False)
        plt.title(f'SHAP Summary Plot - {class_name}')
        plt.tight_layout()
        plt.savefig(os.path.join(OUTPUT_DIR, f'shap_{model_type}_{class_name}_summary.png'))
        plt.close()
    
    plt.figure(figsize=(12, 8))
    shap.summary_plot(shap_values, X_sample, feature_names=feature_names, max_display=15,
                      show=False, class_names=['客胜', '平局', '主胜'])
    plt.title(f'SHAP Summary Plot - {model_type}')
    plt.tight_layout()
    plt.savefig(os.path.join(OUTPUT_DIR, f'shap_{model_type}_all_classes.png'))
    plt.close()
    
    expected_value = explainer.expected_value
    if isinstance(expected_value, np.ndarray):
        result['expected_value'] = expected_value.tolist()
    elif isinstance(expected_value, list):
        result['expected_value'] = expected_value
    else:
        result['expected_value'] = expected_value
    
    print(f"SHAP分析完成，已保存图表到 {OUTPUT_DIR}")
    
    return result

def convert_to_serializable(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    elif isinstance(obj, dict):
        return {key: convert_to_serializable(value) for key, value in obj.items()}
    else:
        return obj

def generate_shap_report(shap_results):
    if shap_results is None:
        return
    
    report = {
        'generated_at': pd.Timestamp.now().isoformat(),
        'models': {}
    }
    
    for model_type, result in shap_results.items():
        report['models'][model_type] = {
            'expected_value': result.get('expected_value'),
            'feature_importance': {}
        }
        
        for class_name, features in result.get('summary', {}).items():
            report['models'][model_type]['feature_importance'][class_name] = features
    
    report_path = os.path.join(OUTPUT_DIR, 'shap_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(convert_to_serializable(report), f, indent=2, ensure_ascii=False)
    
    print(f"\nSHAP报告已保存到 {report_path}")
    
    print("\n特征重要性排名:")
    for model_type, result in shap_results.items():
        print(f"\n{model_type.upper()}:")
        for class_name, features in result.get('summary', {}).items():
            print(f"  {class_name}:")
            for i, feat in enumerate(features[:10], 1):
                print(f"    {i}. {feat['feature']}: {feat['shap_importance']:.4f}")

def main():
    print("=" * 60)
    print("SHAP可解释性分析工具")
    print("=" * 60)
    
    print("\n加载比赛数据...")
    df = load_match_data_odds()
    
    print("构建特征...")
    X, y = build_all_features(df)
    feature_names = X.columns.tolist()
    
    print(f"\n特征维度: {X.shape[1]}")
    print(f"样本数量: {len(y)}")
    
    validation_split = CONFIG.get('training', {}).get('validation_split', 0.2)
    train_size = int(len(df) * (1 - validation_split))
    
    X_train = X.iloc[:train_size]
    X_val = X.iloc[train_size:]
    y_train = y.iloc[:train_size]
    y_val = y.iloc[train_size:]
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    
    print(f"\n训练集: {len(X_train)} 场")
    print(f"验证集: {len(X_val)} 场")
    
    shap_results = {}
    
    print("\n训练XGBoost模型...")
    xgb_model = train_model_for_shap(X_train_scaled, y_train.values, X_val_scaled, y_val.values, 'xgboost')
    if xgb_model:
        X_val_df_scaled = pd.DataFrame(X_val_scaled, columns=feature_names)
        shap_results['xgboost'] = analyze_with_shap(xgb_model, X_val_df_scaled, feature_names, 'xgboost')
    
    print("\n训练LightGBM模型...")
    lgb_model = train_model_for_shap(X_train_scaled, y_train.values, X_val_scaled, y_val.values, 'lightgbm')
    if lgb_model:
        X_val_df_scaled = pd.DataFrame(X_val_scaled, columns=feature_names)
        shap_results['lightgbm'] = analyze_with_shap(lgb_model, X_val_df_scaled, feature_names, 'lightgbm')
    
    generate_shap_report(shap_results)
    
    print("\n" + "=" * 60)
    print("SHAP分析完成!")
    print("=" * 60)

if __name__ == "__main__":
    main()
