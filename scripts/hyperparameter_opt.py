import pandas as pd
import numpy as np
import json
import os
import yaml
import pickle
import optuna
from pathlib import Path

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    print("Warning: xgboost not installed, will skip XGBoost optimization")

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    print("Warning: lightgbm not installed, will skip LightGBM optimization")

from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.yaml"
CACHE_DIR = BASE_DIR / "data" / "cache"

from feature_utils import load_config, load_match_data, load_match_data_odds, build_features, build_team_features, build_all_features

CONFIG = load_config()

def precompute_and_cache_features():
    os.makedirs(CACHE_DIR, exist_ok=True)
    
    cache_path = os.path.join(CACHE_DIR, 'features_cache.pkl')
    
    if os.path.exists(cache_path):
        print(f"加载缓存特征数据...")
        with open(cache_path, 'rb') as f:
            return pickle.load(f)
    
    print(f"重新计算特征数据...")
    df = load_match_data_odds()
    
    X, y = build_all_features(df)
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    X_scaled_df = pd.DataFrame(X_scaled, columns=X.columns, index=X.index)
    
    data = {
        'X': X,
        'X_scaled': X_scaled_df,
        'y': y,
        'feature_names': X.columns.tolist(),
        'scaler': scaler
    }
    
    with open(cache_path, 'wb') as f:
        pickle.dump(data, f)
    
    print(f"特征数据已缓存到 {cache_path}")
    return data

def suggest_params(trial, model_type):
    search_space = CONFIG.get('optuna', {}).get(f'{model_type}_search_space', {})
    
    params = {}
    
    for param_name, config in search_space.items():
        param_type = config.get('type', 'float')
        param_min = config.get('min', 0)
        param_max = config.get('max', 1)
        param_log = config.get('log', False)
        
        if param_type == 'int':
            params[param_name] = trial.suggest_int(param_name, param_min, param_max)
        elif param_type == 'float':
            params[param_name] = trial.suggest_float(param_name, param_min, param_max, log=param_log)
    
    return params

def objective_xgboost(trial, X_scaled, y):
    params = suggest_params(trial, 'xgboost')
    
    params.update({
        'objective': 'multi:softprob',
        'num_class': 3,
        'eval_metric': 'mlogloss',
        'seed': 42,
        'nthread': 1
    })
    
    tscv = TimeSeriesSplit(n_splits=3)
    accuracies = []
    
    for train_idx, val_idx in tscv.split(X_scaled):
        X_train_fold, X_val_fold = X_scaled.iloc[train_idx], X_scaled.iloc[val_idx]
        y_train_fold, y_val_fold = y.iloc[train_idx], y.iloc[val_idx]
        
        dtrain = xgb.DMatrix(X_train_fold, label=y_train_fold)
        dval = xgb.DMatrix(X_val_fold, label=y_val_fold)
        
        watchlist = [(dtrain, 'train'), (dval, 'val')]
        model = xgb.train(params, dtrain, num_boost_round=100, evals=watchlist,
                          early_stopping_rounds=15, verbose_eval=0)
        
        y_pred = model.predict(dval)
        y_pred_class = np.argmax(y_pred, axis=1)
        accuracies.append(accuracy_score(y_val_fold, y_pred_class))
    
    return np.mean(accuracies)

def objective_lightgbm(trial, X_scaled, y):
    params = suggest_params(trial, 'lightgbm')
    
    params.update({
        'objective': 'multiclass',
        'num_class': 3,
        'metric': 'multi_logloss',
        'seed': 42,
        'verbose': 0
    })
    
    tscv = TimeSeriesSplit(n_splits=3)
    accuracies = []
    
    for train_idx, val_idx in tscv.split(X_scaled):
        X_train_fold, X_val_fold = X_scaled.iloc[train_idx], X_scaled.iloc[val_idx]
        y_train_fold, y_val_fold = y.iloc[train_idx], y.iloc[val_idx]
        
        lgb_train = lgb.Dataset(X_train_fold, y_train_fold)
        lgb_val = lgb.Dataset(X_val_fold, y_val_fold, reference=lgb_train)
        
        callbacks = [lgb.early_stopping(stopping_rounds=15), lgb.log_evaluation(period=0)]
        
        model = lgb.train(params, lgb_train, num_boost_round=100,
                          valid_sets=[lgb_val], callbacks=callbacks)
        
        y_pred = model.predict(X_val_fold)
        y_pred_class = np.argmax(y_pred, axis=1)
        accuracies.append(accuracy_score(y_val_fold, y_pred_class))
    
    return np.mean(accuracies)

def optimize_model(model_type, X_scaled, y):
    optuna_config = CONFIG.get('optuna', {})
    n_trials = optuna_config.get('n_trials', 50)
    timeout = optuna_config.get('timeout_seconds', 3600)
    
    print(f"\n{'='*60}")
    print(f"Optuna 贝叶斯优化 - {model_type.upper()}")
    print(f"{'='*60}")
    
    if model_type == 'xgboost' and xgb is None:
        print("XGBoost not installed, skipping...")
        return None
    
    if model_type == 'lightgbm' and lgb is None:
        print("LightGBM not installed, skipping...")
        return None
    
    study = optuna.create_study(
        direction='maximize',
        pruner=optuna.pruners.MedianPruner(n_warmup_steps=5),
        sampler=optuna.samplers.TPESampler(seed=42)
    )
    
    if model_type == 'xgboost':
        study.optimize(
            lambda trial: objective_xgboost(trial, X_scaled, y),
            n_trials=n_trials,
            timeout=timeout,
            n_jobs=1
        )
    else:
        study.optimize(
            lambda trial: objective_lightgbm(trial, X_scaled, y),
            n_trials=n_trials,
            timeout=timeout,
            n_jobs=1
        )
    
    print(f"\n最佳参数 ({model_type}):")
    print(study.best_params)
    print(f"最佳准确率: {study.best_value:.4f}")
    
    return study.best_params

def update_config_with_best_params(xgb_best_params, lgb_best_params):
    config = load_config()
    
    if xgb_best_params:
        config['model']['xgboost'].update(xgb_best_params)
    
    if lgb_best_params:
        config['model']['lightgbm'].update(lgb_best_params)
    
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    
    print(f"\n配置文件已更新: {CONFIG_PATH}")

def main():
    print("=" * 60)
    print("Optuna 贝叶斯超参数优化")
    print("=" * 60)
    
    data = precompute_and_cache_features()
    X_scaled = data['X_scaled']
    y = data['y']
    
    print(f"\n特征维度: {X_scaled.shape[1]}")
    print(f"样本数量: {len(y)}")
    
    xgb_best_params = optimize_model('xgboost', X_scaled, y)
    lgb_best_params = optimize_model('lightgbm', X_scaled, y)
    
    update_config_with_best_params(xgb_best_params, lgb_best_params)
    
    print("\n" + "=" * 60)
    print("超参数优化完成!")
    print("=" * 60)

if __name__ == "__main__":
    main()
