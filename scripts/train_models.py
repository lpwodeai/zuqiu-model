import pandas as pd
import numpy as np
import json
import os
import yaml
import sqlite3
from datetime import datetime

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    print("Warning: xgboost not installed, will skip XGBoost training")

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    print("Warning: lightgbm not installed, will skip LightGBM training")

from sklearn.model_selection import train_test_split, TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.yaml"
ANOMALY_DB_PATH = BASE_DIR / "data" / "anomaly_samples.db"

# 平局决策阈值因子：提升平局预测召回率（class=1）
# 不修改概率，仅在分类时降低平局阈值，保持概率校准
# factor=1.5 时等价于原 draw_boost=0.5 的分类效果，但不破坏概率校准
DRAW_THRESHOLD_FACTOR = 1.5

# === 平局召回率后处理校准 (2026-08-21 精细搜索最优参数) ===
# 方案A: 决策阈值法 apply_draw_threshold (不改概率, 用 DRAW_THRESHOLD_FACTOR)
# 方案B: 概率缩放法 DrawCalibrator (改平局概率后重归一化, 用 DRAW_CALIBRATOR_FACTOR)
# 精细搜索 (5折CV, 209维XGBoost, 约束 draw_recall>=0.28):
#   - 最优 DrawCalibrator factor=0.885: acc=0.4884, draw_recall=0.2813 (准确率损失最小)
#   - 保守配置 factor=0.850:  acc=0.4849, draw_recall=0.3036 (召回率储备更高)
# 最终选择: factor=0.885，平局召回率刚好达标(>0.28)，准确率损失仅 1.12pp
DRAW_CALIBRATOR_FACTOR = 0.885  # 推荐值 (draw_recall>=0.28, 准确率最高)
DRAW_CALIBRATOR_CONSERVATIVE = 0.850  # 保守值 (draw_recall>=0.30)

from feature_utils import (
    load_config, load_match_data, load_match_data_odds, check_data_quality,
    build_features, build_team_features, build_all_features,
    build_odds_features, calc_h2h_stats, precompute_team_stats, get_global_league_stats
)

from odds_data_spec import TrainingLogger
from draw_calibrator import DrawCalibrator  # T-003.4 平局召回率后处理校准

CONFIG = load_config()

# === Optuna 调优最优参数 (2026-08-21, 30 trials × 2 models) ===
# 当 USE_OPTUNA_BEST_PARAMS=True 时，忽略 config.yaml 中的默认参数
USE_OPTUNA_BEST_PARAMS = True

XGB_OPTUNA_BEST = {
    'objective': 'multi:softprob',
    'num_class': 3,
    'eval_metric': 'mlogloss',
    'max_depth': 4,
    'learning_rate': 0.07,          # 文档推荐 0.07 不变
    'subsample': 0.785,
    'colsample_bytree': 0.827,
    'gamma': 5.0,                   # 进一步收紧: 4.5→5.0 (旧模型 4.956)
    'min_child_weight': 13,         # 对齐旧模型: 10→13 (旧模型 13)
    'max_delta_step': 0,
    'reg_alpha': 0.1,              # L1 正则
    'reg_lambda': 8.0,             # 对齐旧模型: 5.0→8.0 (旧模型 8.003)
    'scale_pos_weight': 2.108,
    'seed': 42,
    'nthread': -1,
}
XGB_OPTUNA_NUM_ROUNDS = 130  # 减少迭代: 178→130 (lr=0.07 下足够收敛)

LGB_OPTUNA_BEST = {
    'objective': 'multiclass',
    'num_class': 3,
    'metric': 'multi_logloss',
    'max_depth': 4,                 # 收紧: 6→4 (旧模型 3, 减少 50% 深度)
    'learning_rate': 0.12,
    'num_leaves': 128,              # 收紧: 212→128 (减少复杂度)
    'subsample': 0.680,
    'colsample_bytree': 0.540,
    'reg_alpha': 3.353,
    'reg_lambda': 13.904,
    'min_child_weight': 8,
    'min_data_in_leaf': 60,         # 收紧: 40→60 (增加叶节点最小样本)
    'feature_fraction': 0.566,
    'bagging_fraction': 0.783,
    'bagging_freq': 5,
    'seed': 42,
    'verbose': 0,
}
LGB_OPTUNA_NUM_ROUNDS = 80  # 减少迭代: 105→80 (lr=0.12 下足够收敛)

def get_anomaly_match_ids():
    try:
        conn = sqlite3.connect(ANOMALY_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT match_id FROM anomaly_samples")
        anomaly_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        return anomaly_ids
    except Exception as e:
        print(f"读取异常样本库失败: {e}")
        return []

def create_sample_weights(df, anomaly_weight=3.0, double_error_weight=4.0):
    anomaly_ids = get_anomaly_match_ids()
    if not anomaly_ids:
        print("未找到异常样本，使用等权重")
        return np.ones(len(df))
    
    print(f"\n加载到 {len(anomaly_ids)} 个异常样本")
    
    cn_to_en_mapping = {
        '利物浦': 'Liverpool',
        '切尔西': 'Chelsea',
        '阿森纳': 'Arsenal',
        '曼城': 'Manchester_City',
        '曼联': 'Manchester_United',
        '热刺': 'Tottenham_Hotspur',
        '纽卡斯尔': 'Newcastle_United',
        '布莱顿': 'Brighton_&_Hove_Albion',
        '伯恩茅斯': 'AFC_Bournemouth',
        '利兹联': 'Leeds_United',
        '埃弗顿': 'Everton',
        '阿斯顿维拉': 'Aston_Villa',
        '富勒姆': 'Fulham',
        '桑德兰': 'Sunderland',
        '西汉姆': 'West_Ham_United',
        '伯恩利': 'Burnley',
        '狼队': 'Wolverhampton_Wanderers',
        '诺丁汉森林': 'Nottingham_Forest',
        '布伦特福德': 'Brentford',
        '水晶宫': 'Crystal_Palace',
    }
    
    weights = np.ones(len(df))
    
    for idx, row in df.iterrows():
        date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        home_cn = str(row['home_team_name']).strip()
        away_cn = str(row['away_team_name']).strip()
        
        home_en = cn_to_en_mapping.get(home_cn, home_cn).replace(' ', '_')
        away_en = cn_to_en_mapping.get(away_cn, away_cn).replace(' ', '_')
        
        match_id_cn = f"{date_str}_{home_cn.replace(' ', '_')}_{away_cn.replace(' ', '_')}"
        match_id_en = f"{date_str}_{home_en}_{away_en}"
        
        matched_id = None
        if match_id_cn in anomaly_ids:
            matched_id = match_id_cn
        elif match_id_en in anomaly_ids:
            matched_id = match_id_en
        
        if matched_id:
            try:
                conn = sqlite3.connect(ANOMALY_DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT anomaly_type FROM anomaly_samples WHERE match_id = ?", (matched_id,))
                result = cursor.fetchone()
                conn.close()
                
                if result and result[0] == '双重错误':
                    weights[idx] = double_error_weight
                    print(f"  双重错误样本: {matched_id} -> 权重 {double_error_weight}")
                else:
                    weights[idx] = anomaly_weight
                    print(f"  异常样本: {matched_id} -> 权重 {anomaly_weight}")
            except Exception as e:
                weights[idx] = anomaly_weight
    
    print(f"\n样本权重统计:")
    print(f"  正常样本: {len(weights[weights == 1.0])}")
    print(f"  异常样本(权重{anomaly_weight}): {len(weights[weights == anomaly_weight])}")
    print(f"  双重错误样本(权重{double_error_weight}): {len(weights[weights == double_error_weight])}")
    print(f"  平均权重: {weights.mean():.2f}")
    
    return weights

def train_xgboost(X_train, y_train, X_val, y_val, params=None, sample_weights=None,
                  num_boost_round=None):
    if xgb is None:
        return None, None
    
    xgb_config = CONFIG.get('model', {}).get('xgboost', {})
    
    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (3 * class_counts)
    print(f"XGBoost - 类别权重: {class_weights}")
    
    if params is None:
        if USE_OPTUNA_BEST_PARAMS:
            # 使用 Optuna 调优最优参数
            params = dict(XGB_OPTUNA_BEST)
            print(f"XGBoost - 使用 Optuna 最优参数 (lr={params['learning_rate']}, "
                  f"max_depth={params['max_depth']})")
        else:
            params = {
                'objective': 'multi:softprob',
                'num_class': 3,
                'eval_metric': 'mlogloss',
                'max_depth': xgb_config.get('max_depth', 2),
                'learning_rate': xgb_config.get('learning_rate', 0.03),
                'subsample': xgb_config.get('subsample', 0.6),
                'colsample_bytree': xgb_config.get('colsample_bytree', 0.6),
                'gamma': xgb_config.get('gamma', 0.5),
                'min_child_weight': xgb_config.get('min_child_weight', 10),
                'reg_alpha': xgb_config.get('reg_alpha', 1.0),
                'reg_lambda': xgb_config.get('reg_lambda', 10.0),
                'seed': 42,
                'nthread': -1
            }
    
    # scale_pos_weight 在多分类中由 sample_weight + class_weight 承担，从 params 移除
    if 'scale_pos_weight' in params:
        del params['scale_pos_weight']
    # nthread 新版 xgb 不接受
    if 'nthread' in params:
        del params['nthread']
    
    if num_boost_round is None:
        num_boost_round = (
            XGB_OPTUNA_NUM_ROUNDS if USE_OPTUNA_BEST_PARAMS
            else xgb_config.get('num_boost_round', 100)
        )
    
    dtrain = xgb.DMatrix(X_train, label=y_train)
    if sample_weights is not None:
        dtrain.set_weight(sample_weights[:len(y_train)] * class_weights[y_train])
    else:
        dtrain.set_weight(class_weights[y_train])
    
    dval = xgb.DMatrix(X_val, label=y_val)
    
    watchlist = [(dtrain, 'train'), (dval, 'val')]
    es_rounds = (5 if USE_OPTUNA_BEST_PARAMS
                 else xgb_config.get('early_stopping_rounds', 15))
    model = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist,
                      early_stopping_rounds=es_rounds, verbose_eval=0)
    
    y_pred = model.predict(dval)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    
    y_pred_train = model.predict(dtrain)
    train_accuracy = accuracy_score(y_train, np.argmax(y_pred_train, axis=1))
    train_ll = log_loss(y_train, y_pred_train)
    
    print(f"\nXGBoost - Train Accuracy: {train_accuracy:.4f}, Train LogLoss: {train_ll:.4f}")
    print(f"XGBoost - Val Accuracy: {accuracy:.4f}, Val LogLoss: {ll:.4f}, Brier: {brier:.4f}")
    
    return model, {'accuracy': accuracy, 'log_loss': ll, 'brier': brier, 
                   'train_accuracy': train_accuracy, 'train_log_loss': train_ll}

def train_lightgbm(X_train, y_train, X_val, y_val, params=None, sample_weights=None,
                   num_boost_round=None):
    if lgb is None:
        return None, None
    
    lgb_config = CONFIG.get('model', {}).get('lightgbm', {})
    
    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (3 * class_counts)
    print(f"LightGBM - 类别权重: {class_weights}")
    
    if params is None:
        if USE_OPTUNA_BEST_PARAMS:
            params = dict(LGB_OPTUNA_BEST)
            print(f"LightGBM - 使用 Optuna 最优参数 (lr={params['learning_rate']}, "
                  f"max_depth={params['max_depth']}, num_leaves={params['num_leaves']})")
        else:
            params = {
                'objective': 'multiclass',
                'num_class': 3,
                'metric': 'multi_logloss',
                'max_depth': lgb_config.get('max_depth', 2),
                'learning_rate': lgb_config.get('learning_rate', 0.03),
                'num_leaves': lgb_config.get('num_leaves', 8),
                'subsample': lgb_config.get('subsample', 0.6),
                'colsample_bytree': lgb_config.get('colsample_bytree', 0.6),
                'reg_alpha': lgb_config.get('reg_alpha', 1.0),
                'reg_lambda': lgb_config.get('reg_lambda', 10.0),
                'min_child_weight': lgb_config.get('min_child_weight', 10),
                'min_data_in_leaf': lgb_config.get('min_data_in_leaf', 30),
                'seed': 42,
                'verbose': 0
            }
    
    if sample_weights is not None:
        final_weights = sample_weights[:len(y_train)] * class_weights[y_train]
    else:
        final_weights = class_weights[y_train]
    
    lgb_train = lgb.Dataset(X_train, y_train, weight=final_weights)
    lgb_val = lgb.Dataset(X_val, y_val, reference=lgb_train)
    
    if num_boost_round is None:
        num_boost_round = (
            LGB_OPTUNA_NUM_ROUNDS if USE_OPTUNA_BEST_PARAMS
            else lgb_config.get('num_boost_round', 100)
        )
    es_rounds = (5 if USE_OPTUNA_BEST_PARAMS
                 else lgb_config.get('early_stopping_rounds', 15))
    
    callbacks = [lgb.early_stopping(stopping_rounds=es_rounds), lgb.log_evaluation(period=0)]
    
    model = lgb.train(params, lgb_train, num_boost_round=num_boost_round,
                      valid_sets=[lgb_val], callbacks=callbacks)
    
    y_pred = model.predict(X_val)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    
    y_pred_train = model.predict(X_train)
    train_accuracy = accuracy_score(y_train, np.argmax(y_pred_train, axis=1))
    train_ll = log_loss(y_train, y_pred_train)
    
    print(f"\nLightGBM - Train Accuracy: {train_accuracy:.4f}, Train LogLoss: {train_ll:.4f}")
    print(f"LightGBM - Val Accuracy: {accuracy:.4f}, Val LogLoss: {ll:.4f}, Brier: {brier:.4f}")
    
    return model, {'accuracy': accuracy, 'log_loss': ll, 'brier': brier,
                   'train_accuracy': train_accuracy, 'train_log_loss': train_ll}

def apply_draw_threshold(probs, factor=DRAW_THRESHOLD_FACTOR):
    """
    平局决策阈值调整：不修改概率，仅调整分类决策。
    
    保持概率分布不变（sum=1.0），仅在 argmax 时对平局类（class=1）
    应用阈值因子。如果 平局概率 × factor > max(客胜概率, 主胜概率)，
    则预测为平局。
    
    相比 draw_boost 的优势：
    - 概率保持校准（ECE不受影响）
    - 三分类概率和恒为 1.0（无需重归一化）
    - 价值投注 edge 计算不受影响
    """
    pred = np.argmax(probs, axis=1)
    # 平局概率 × factor 超过客胜和主胜时，覆盖为平局
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

def fit_platt_scaling(y_true, y_proba):
    calibrated_proba = []
    for class_idx in range(y_proba.shape[1]):
        y_binary = (y_true == class_idx).astype(int)
        X_prob = y_proba[:, class_idx].reshape(-1, 1)
        
        clf = LogisticRegression(solver='lbfgs', max_iter=100)
        try:
            clf.fit(X_prob, y_binary)
            a = float(clf.coef_[0][0])
            b = float(clf.intercept_[0])
        except:
            a = 1.0
            b = 0.0
        
        calibrated_proba.append({'a': a, 'b': b})
    
    return calibrated_proba

def compute_ece(y_true, y_proba, n_bins=10):
    """Expected Calibration Error: 概率校准度量，越低越好"""
    ece = 0.0
    for class_idx in range(y_proba.shape[1]):
        y_binary = (y_true == class_idx).astype(int)
        prob = y_proba[:, class_idx]
        bin_edges = np.linspace(0, 1, n_bins + 1)
        for i in range(n_bins):
            mask = (prob >= bin_edges[i]) & (prob < bin_edges[i + 1])
            if mask.sum() > 0:
                acc = y_binary[mask].mean()
                conf = prob[mask].mean()
                ece += (mask.sum() / len(y_true)) * abs(acc - conf)
    return ece / y_proba.shape[1]

def compute_ece_per_class(y_true, y_proba, n_bins=10):
    """逐类别 ECE"""
    ece_list = []
    for class_idx in range(y_proba.shape[1]):
        y_binary = (y_true == class_idx).astype(int)
        prob = y_proba[:, class_idx]
        bin_edges = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            mask = (prob >= bin_edges[i]) & (prob < bin_edges[i + 1])
            if mask.sum() > 0:
                acc = y_binary[mask].mean()
                conf = prob[mask].mean()
                ece += (mask.sum() / len(y_true)) * abs(acc - conf)
        ece_list.append(ece)
    return ece_list

def apply_platt_scaling(probs, calibration_params):
    calibrated = np.zeros_like(probs)
    for class_idx in range(probs.shape[1]):
        a = calibration_params[class_idx]['a']
        b = calibration_params[class_idx]['b']
        logit = a * probs[:, class_idx] + b
        calibrated[:, class_idx] = 1 / (1 + np.exp(-logit))
    
    row_sums = calibrated.sum(axis=1, keepdims=True)
    calibrated = calibrated / np.maximum(row_sums, 1e-10)
    
    return calibrated

def convert_xgb_to_js(model):
    if model is None:
        return None
    
    trees = []
    base_score = model.attributes().get('base_score', 0.5)
    
    tree_dump = model.get_dump()
    for i in range(len(tree_dump)):
        tree_str = tree_dump[i]
        nodes = []
        current_node = {}
        
        for line in tree_str.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('0:'):
                if current_node:
                    nodes.append(current_node)
                current_node = {'node_id': 0}
                parts = line[2:].split('[')
                if len(parts) > 1:
                    cond_part = parts[1].split(']')[0]
                    feature, rest = cond_part.split('<')
                    current_node['split'] = {
                        'feature': feature.strip(),
                        'threshold': float(rest.strip())
                    }
                    goto_part = parts[1].split(']')[1]
                    left = int(goto_part.split('yes=')[1].split(',')[0])
                    right = int(goto_part.split('no=')[1].split(',')[0])
                    current_node['split']['left'] = left
                    current_node['split']['right'] = right
                else:
                    current_node['leaf'] = float(line.split(':')[1].split('leaf=')[1].strip())
            else:
                node_id = int(line.split(':')[0])
                if 'leaf=' in line:
                    leaf_val = float(line.split('leaf=')[1].strip())
                    nodes.append({'node_id': node_id, 'leaf': leaf_val})
                else:
                    parts = line.split('[')
                    cond_part = parts[1].split(']')[0]
                    feature, rest = cond_part.split('<')
                    split_info = {
                        'feature': feature.strip(),
                        'threshold': float(rest.strip())
                    }
                    goto_part = parts[1].split(']')[1]
                    left = int(goto_part.split('yes=')[1].split(',')[0])
                    right = int(goto_part.split('no=')[1].split(',')[0])
                    split_info['left'] = left
                    split_info['right'] = right
                    nodes.append({'node_id': node_id, 'split': split_info})
        
        trees.append({'nodes': nodes})
    
    js_model = {
        'base': base_score,
        'lr': 0.1,
        'trees': trees
    }
    
    return js_model

def convert_lgb_to_js(model):
    if model is None:
        return None
    
    trees = []
    base_score = 0.5
    
    tree_info = model.dump_model()
    for tree in tree_info['tree_info']:
        nodes = []
        node_counter = 0
        
        def parse_node(node):
            nonlocal node_counter
            current_id = node_counter
            node_counter += 1
            
            if 'split_index' in node:
                feature_name = tree_info['feature_names'][node['split_index']]
                left_id = node_counter
                right_id = node_counter + 1
                
                nodes.append({
                    'node_id': current_id,
                    'split': {
                        'feature': feature_name,
                        'threshold': node['threshold'],
                        'left': left_id,
                        'right': right_id
                    }
                })
                
                parse_node(node['left_child'])
                parse_node(node['right_child'])
            else:
                leaf_value = node['leaf_value']
                if isinstance(leaf_value, (list, tuple)):
                    leaf_value = leaf_value[0]
                nodes.append({
                    'node_id': current_id,
                    'leaf': leaf_value
                })
        
        parse_node(tree['tree_structure'])
        trees.append({'nodes': nodes})
    
    js_model = {
        'base': base_score,
        'lr': 0.1,
        'trees': trees
    }
    
    return js_model

def save_model_to_js(js_model, model_name):
    output_path = os.path.join(OUTPUT_DIR, f"{model_name.lower()}_model_export.js")
    
    js_content = f"var {model_name.upper()}_MODEL = {json.dumps(js_model, indent=2)};"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print(f"\nSaved {model_name} model to {output_path}")
    return output_path

def save_calibration_params(calibration_params, model_name):
    output_path = os.path.join(OUTPUT_DIR, f"{model_name.lower()}_calibration_params.js")
    
    js_content = f"var {model_name.upper()}_CALIBRATION = {json.dumps(calibration_params, indent=2)};"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print(f"\nSaved {model_name} calibration params to {output_path}")
    return output_path

def evaluate_with_time_series_split(X, y, feature_names, n_splits=5, sample_weights=None):
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    xgb_results = []
    lgb_results = []
    
    print(f"\n{'='*60}")
    print(f"滚动窗口验证 ({n_splits}折时间序列交叉验证)")
    print(f"{'='*60}")
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        print(f"\n--- 第 {fold+1}/{n_splits} 折 ---")
        
        X_train_fold, X_val_fold = X.iloc[train_idx], X.iloc[val_idx]
        y_train_fold, y_val_fold = y.iloc[train_idx], y.iloc[val_idx]
        
        fold_weights = sample_weights[train_idx] if sample_weights is not None else None
        
        print(f"  训练集: {len(X_train_fold)} 场, 验证集: {len(X_val_fold)} 场")
        
        scaler_fold = StandardScaler()
        X_train_scaled = scaler_fold.fit_transform(X_train_fold)
        X_val_scaled = scaler_fold.transform(X_val_fold)
        
        if xgb is not None:
            xgb_model_fold, xgb_metrics_fold = train_xgboost(X_train_scaled, y_train_fold.values,
                                                              X_val_scaled, y_val_fold.values,
                                                              sample_weights=fold_weights)
            if xgb_metrics_fold:
                xgb_results.append(xgb_metrics_fold)
        
        if lgb is not None:
            lgb_model_fold, lgb_metrics_fold = train_lightgbm(X_train_scaled, y_train_fold.values,
                                                               X_val_scaled, y_val_fold.values,
                                                               sample_weights=fold_weights)
            if lgb_metrics_fold:
                lgb_results.append(lgb_metrics_fold)
    
    print(f"\n{'='*60}")
    print("滚动窗口验证汇总")
    print(f"{'='*60}")
    
    if xgb_results:
        print("\nXGBoost 交叉验证结果:")
        acc_list = [r['accuracy'] for r in xgb_results]
        ll_list = [r['log_loss'] for r in xgb_results]
        train_acc_list = [r.get('train_accuracy', r['accuracy']) for r in xgb_results]
        train_ll_list = [r.get('train_log_loss', r['log_loss']) for r in xgb_results]
        print(f"  训练准确率: {np.mean(train_acc_list):.4f} ± {np.std(train_acc_list):.4f}")
        print(f"  训练LogLoss: {np.mean(train_ll_list):.4f} ± {np.std(train_ll_list):.4f}")
        print(f"  验证准确率: {np.mean(acc_list):.4f} ± {np.std(acc_list):.4f}")
        print(f"  验证LogLoss: {np.mean(ll_list):.4f} ± {np.std(ll_list):.4f}")
    
    if lgb_results:
        print("\nLightGBM 交叉验证结果:")
        acc_list = [r['accuracy'] for r in lgb_results]
        ll_list = [r['log_loss'] for r in lgb_results]
        train_acc_list = [r.get('train_accuracy', r['accuracy']) for r in lgb_results]
        train_ll_list = [r.get('train_log_loss', r['log_loss']) for r in lgb_results]
        print(f"  训练准确率: {np.mean(train_acc_list):.4f} ± {np.std(train_acc_list):.4f}")
        print(f"  训练LogLoss: {np.mean(train_ll_list):.4f} ± {np.std(train_ll_list):.4f}")
        print(f"  验证准确率: {np.mean(acc_list):.4f} ± {np.std(acc_list):.4f}")
        print(f"  验证LogLoss: {np.mean(ll_list):.4f} ± {np.std(ll_list):.4f}")
    
    return xgb_results, lgb_results

def main(incremental=False, version=None):
    logger = TrainingLogger(f"training_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    
    print("=" * 60)
    print("足球比赛预测模型训练Pipeline" + (" (增量训练)" if incremental else "") + (f" [版本: {version}]" if version else ""))
    print("=" * 60)
    
    print("\n1. 加载比赛数据...")
    df = load_match_data_odds()
    print(f"   共加载 {len(df)} 场比赛")
    
    logger.log_data_loading({
        'total_matches': len(df),
        'incremental': incremental,
        'data_source': 'odds.db',
        'date_range': f"{df['date'].min().date()} to {df['date'].max().date()}" if len(df) > 0 else 'N/A'
    })
    
    check_data_quality(df)
    
    if incremental:
        batch_size = CONFIG.get('training', {}).get('incremental', {}).get('batch_size', 100)
        df = df.tail(batch_size)
        print(f"   增量训练模式，使用最近 {len(df)} 场比赛")
    
    print("\n2. 构建特征...")
    include_odds = CONFIG.get('training', {}).get('include_odds_features', True)
    print(f"   赔率特征集成: {'开启' if include_odds else '关闭'}")
    
    X, y = build_all_features(df, include_odds=include_odds)
    
    # 分离特征类型用于日志记录
    basic_features = build_features(df)
    team_features = build_team_features(df)
    team_features = team_features.drop(columns=[col for col in team_features.columns if col in basic_features.columns])
    
    odds_feature_count = X.shape[1] - basic_features.shape[1] - team_features.shape[1]
    
    print(f"   基础特征维度: {basic_features.shape[1]}")
    print(f"   球队特征维度: {team_features.shape[1]}")
    print(f"   赔率特征维度: {odds_feature_count}")
    print(f"   总特征维度: {X.shape[1]}")
    
    logger.log_feature_engineering({
        'basic_features': basic_features.shape[1],
        'team_features': team_features.shape[1],
        'odds_features': odds_feature_count,
        'total_features': X.shape[1],
        'include_odds_features': include_odds,
        'feature_names': X.columns.tolist()[:10] + ['...'] if X.shape[1] > 10 else X.columns.tolist()
    })
    
    print("\n3. 构建异常样本加权...")
    sample_weights = create_sample_weights(df)
    
    print("\n4. 滚动窗口验证...")
    xgb_cv_results, lgb_cv_results = evaluate_with_time_series_split(X, y, X.columns.tolist(), n_splits=5, sample_weights=sample_weights)
    
    if xgb_cv_results:
        cv_acc = np.mean([r['accuracy'] for r in xgb_cv_results])
        cv_ll = np.mean([r['log_loss'] for r in xgb_cv_results])
        logger.log_evaluation('滚动窗口验证(XGBoost)', {
            'mean_accuracy': cv_acc,
            'mean_log_loss': cv_ll,
            'n_splits': 5,
            'fold_results': [{k: v for k, v in r.items() if k != 'model'} for r in xgb_cv_results]
        })
    
    print("\n5. 特征标准化...")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    X_scaled_df = pd.DataFrame(X_scaled, columns=X.columns, index=X.index)
    
    print("\n6. 划分训练/测试集 (时间序列分割)...")
    validation_split = CONFIG.get('training', {}).get('validation_split', 0.2)
    train_size = int(len(df) * (1 - validation_split))
    
    if train_size < 10:
        train_size = max(1, int(len(df) * 0.5))
    
    X_train, X_val = X_scaled_df.iloc[:train_size], X_scaled_df.iloc[train_size:]
    y_train, y_val = y.iloc[:train_size], y.iloc[train_size:]
    
    print(f"   训练集: {len(X_train)} 场")
    print(f"   验证集: {len(X_val)} 场")
    print(f"   训练集结果分布: {y_train.value_counts().to_dict()}")
    print(f"   验证集结果分布: {y_val.value_counts().to_dict()}")
    
    logger.log('DATA_SPLIT', '数据集划分完成', details={
        'train_size': len(X_train),
        'val_size': len(X_val),
        'train_distribution': y_train.value_counts().to_dict(),
        'val_distribution': y_val.value_counts().to_dict(),
        'validation_split': validation_split
    })
    
    print("\n7. 训练XGBoost模型...")
    xgb_model, xgb_metrics = train_xgboost(X_train.values, y_train.values, 
                                           X_val.values, y_val.values,
                                           sample_weights=sample_weights)
    
    if xgb_metrics:
        logger.log_model_training(
            model_name='XGBoost',
            params={
                'max_depth': CONFIG.get('model', {}).get('xgboost', {}).get('max_depth', 2),
                'learning_rate': CONFIG.get('model', {}).get('xgboost', {}).get('learning_rate', 0.03),
                'n_estimators': CONFIG.get('model', {}).get('xgboost', {}).get('num_boost_round', 100)
            },
            iteration=CONFIG.get('model', {}).get('xgboost', {}).get('num_boost_round', 100),
            train_loss=xgb_metrics.get('train_log_loss', 0),
            val_loss=xgb_metrics.get('log_loss', 0),
            metrics=xgb_metrics
        )
    
    print("\n8. 训练LightGBM模型...")
    lgb_model, lgb_metrics = train_lightgbm(X_train.values, y_train.values,
                                            X_val.values, y_val.values,
                                            sample_weights=sample_weights)
    
    if lgb_metrics:
        logger.log_model_training(
            model_name='LightGBM',
            params={
                'max_depth': CONFIG.get('model', {}).get('lightgbm', {}).get('max_depth', 2),
                'learning_rate': CONFIG.get('model', {}).get('lightgbm', {}).get('learning_rate', 0.03),
                'n_estimators': CONFIG.get('model', {}).get('lightgbm', {}).get('num_boost_round', 100)
            },
            iteration=CONFIG.get('model', {}).get('lightgbm', {}).get('num_boost_round', 100),
            train_loss=lgb_metrics.get('train_log_loss', 0),
            val_loss=lgb_metrics.get('log_loss', 0),
            metrics=lgb_metrics
        )
    
    print("\n9. 概率校准 (Platt Scaling)...")
    calibration_enabled = CONFIG.get('calibration', {}).get('enabled', True)
    
    xgb_calibration = None
    lgb_calibration = None
    
    if calibration_enabled and xgb_model:
        dtrain = xgb.DMatrix(X_val.values, label=y_val.values)
        xgb_val_probs = xgb_model.predict(dtrain)
        xgb_calibration = fit_platt_scaling(y_val.values, xgb_val_probs)
        calibrated_probs = apply_platt_scaling(xgb_val_probs, xgb_calibration)
        calibrated_acc = accuracy_score(y_val, np.argmax(calibrated_probs, axis=1))
        calibrated_brier = brier_score_loss(y_val, calibrated_probs, pos_label=2)
        print(f"  XGBoost 校准后 - Val Accuracy: {calibrated_acc:.4f}, Brier: {calibrated_brier:.4f}")
        
        logger.log_evaluation('XGBoost校准', {
            'calibrated_accuracy': calibrated_acc,
            'calibrated_brier': calibrated_brier,
            'calibration_enabled': calibration_enabled
        })
    
    if calibration_enabled and lgb_model:
        lgb_val_probs = lgb_model.predict(X_val.values)
        lgb_calibration = fit_platt_scaling(y_val.values, lgb_val_probs)
        calibrated_probs = apply_platt_scaling(lgb_val_probs, lgb_calibration)
        calibrated_acc = accuracy_score(y_val, np.argmax(calibrated_probs, axis=1))
        calibrated_brier = brier_score_loss(y_val, calibrated_probs, pos_label=2)
        print(f"  LightGBM 校准后 - Val Accuracy: {calibrated_acc:.4f}, Brier: {calibrated_brier:.4f}")
        
        logger.log_evaluation('LightGBM校准', {
            'calibrated_accuracy': calibrated_acc,
            'calibrated_brier': calibrated_brier,
            'calibration_enabled': calibration_enabled
        })
    
    print(f"\n9.5 平局后处理校准 (自适应精细搜索 + dr>=0.28 约束下最小化准确率损失)...")

    final_report = {
        'timestamp': datetime.now().isoformat(),
        'optuna_params_used': USE_OPTUNA_BEST_PARAMS,
        'xgb_optuna_best': {
            **{k: v for k, v in XGB_OPTUNA_BEST.items()
               if k not in ['nthread', 'scale_pos_weight']},
            'n_estimators': XGB_OPTUNA_NUM_ROUNDS,
        } if USE_OPTUNA_BEST_PARAMS else None,
        'lgb_optuna_best': {
            **LGB_OPTUNA_BEST,
            'n_estimators': LGB_OPTUNA_NUM_ROUNDS,
        } if USE_OPTUNA_BEST_PARAMS else None,
        'draw_calibrator_factor': DRAW_CALIBRATOR_FACTOR,
        'draw_threshold_factor': DRAW_THRESHOLD_FACTOR,
        'validation_split': validation_split,
        'feature_dim': X.shape[1],
        'total_matches': len(df),
        'models': {}
    }

    from sklearn.metrics import classification_report, f1_score, precision_score, recall_score

    # 自适应精细搜索: 在 dr>=DRAW_RECALL_TARGET(0.28) 约束下最大化准确率
    DRAW_RECALL_TARGET = 0.28

    def search_best_draw_calibrator(probs, y_true,
                                    factor_min=0.50, factor_max=1.00, factor_step=0.01,
                                    recall_target=DRAW_RECALL_TARGET):
        """精细搜索 DrawCalibrator factor，优先满足平局召回，其次最大化准确率"""
        best_valid = None  # (factor, accuracy, draw_recall)
        best_any = None    # 兜底：dr 最高的
        for factor in np.arange(factor_min, factor_max + 1e-9, factor_step):
            cal = DrawCalibrator(factor=factor)
            p_cal = cal.calibrate(probs)
            pred = np.argmax(p_cal, axis=1)
            cr = classification_report(y_true, pred, labels=[0, 1, 2],
                                       target_names=['客胜', '平局', '主胜'],
                                       output_dict=True, zero_division=0)
            acc = accuracy_score(y_true, pred)
            dr = cr['平局']['recall']
            if dr >= recall_target:
                if best_valid is None or acc > best_valid[1]:
                    best_valid = (factor, acc, dr)
            if best_any is None or dr > best_any[2]:
                best_any = (factor, acc, dr)
        return best_valid if best_valid is not None else best_any

    def search_best_draw_threshold(probs, y_true,
                                   factor_min=1.00, factor_max=2.50, factor_step=0.05,
                                   recall_target=DRAW_RECALL_TARGET):
        """精细搜索 Threshold factor，优先满足平局召回，其次最大化准确率"""
        best_valid = None
        best_any = None
        for factor in np.arange(factor_min, factor_max + 1e-9, factor_step):
            pred = apply_draw_threshold(probs, factor)
            cr = classification_report(y_true, pred, labels=[0, 1, 2],
                                       target_names=['客胜', '平局', '主胜'],
                                       output_dict=True, zero_division=0)
            acc = accuracy_score(y_true, pred)
            dr = cr['平局']['recall']
            if dr >= recall_target:
                if best_valid is None or acc > best_valid[1]:
                    best_valid = (factor, acc, dr)
            if best_any is None or dr > best_any[2]:
                best_any = (factor, acc, dr)
        return best_valid if best_valid is not None else best_any

    for model_name, model, val_probs_fn, calibration_data, train_metrics in [
        ("XGBoost", xgb_model, (lambda m, xv: m.predict(xgb.DMatrix(xv)) if m else None),
         (xgb_calibration, 'XGBoost'), xgb_metrics),
        ("LightGBM", lgb_model, (lambda m, xv: m.predict(xv) if m else None),
         (lgb_calibration, 'LightGBM'), lgb_metrics),
    ]:
        if model is None:
            continue

        val_probs = val_probs_fn(model, X_val.values)
        calib_data, mname = calibration_data
        if calib_data:
            val_probs = apply_platt_scaling(val_probs, calib_data)

        # === 自适应精细搜索：Threshold 和 DrawCalibrator 各自最优 ===
        print(f"\n  >> {mname} 自适应搜索：在平局召回率≥{DRAW_RECALL_TARGET}前提下最小化准确率损失...")
        thr_best = search_best_draw_threshold(val_probs, y_val)
        cal_best = search_best_draw_calibrator(val_probs, y_val)

        # Original 基准
        orig_pred = np.argmax(val_probs, axis=1)
        acc_orig = accuracy_score(y_val, orig_pred)
        cr_orig = classification_report(y_val, orig_pred, output_dict=True,
                                        labels=[0, 1, 2],
                                        target_names=['客胜', '平局', '主胜'],
                                        zero_division=0)
        ece_orig = compute_ece(y_val, val_probs, n_bins=10)

        # Threshold (自适应最优)
        thr_factor_opt, acc_thr_opt, dr_thr_opt = thr_best
        thr_pred_opt = apply_draw_threshold(val_probs, thr_factor_opt)
        cr_thr_opt = classification_report(y_val, thr_pred_opt, output_dict=True,
                                           labels=[0, 1, 2],
                                           target_names=['客胜', '平局', '主胜'],
                                           zero_division=0)

        # DrawCalibrator (自适应最优)
        cal_factor_opt, acc_cal_opt, dr_cal_opt = cal_best
        calibrator_opt = DrawCalibrator(factor=cal_factor_opt)
        cal_probs_opt = calibrator_opt.calibrate(val_probs)
        cal_pred_opt = np.argmax(cal_probs_opt, axis=1)
        cr_cal_opt = classification_report(y_val, cal_pred_opt, output_dict=True,
                                           labels=[0, 1, 2],
                                           target_names=['客胜', '平局', '主胜'],
                                           zero_division=0)
        ece_cal_opt = compute_ece(y_val, cal_probs_opt, n_bins=10)
        ll_cal_opt = log_loss(y_val, cal_probs_opt, labels=[0, 1, 2])

        # 最终推荐：draw_recall >= 0.28 约束下选最优
        # 策略：优先 Threshold（不破坏概率 ECE），仅当 DrawCalibrator 准确率
        #       高出 Threshold >= 0.5pp 时才选 DrawCalibrator（补偿 ECE 恶化代价）
        candidates = [
            ('Original', acc_orig, cr_orig['平局']['recall'], orig_pred, 'original', None, None),
            (f'Threshold(F={thr_factor_opt:.2f})*', acc_thr_opt, dr_thr_opt, thr_pred_opt, 'threshold',
             thr_factor_opt, None),
            (f'DrawCalibrator(F={cal_factor_opt:.3f})*', acc_cal_opt, dr_cal_opt, cal_pred_opt, 'calibrator',
             None, cal_factor_opt),
        ]
        valid_cands = [c for c in candidates if c[2] >= DRAW_RECALL_TARGET]
        if valid_cands:
            # 在达标候选中：优先 Threshold，仅当 DrawCal 准确率高出 >=0.5pp 才选它
            valid_threshold = [c for c in valid_cands if c[4] == 'threshold']
            valid_calibrator = [c for c in valid_cands if c[4] == 'calibrator']
            if valid_threshold:
                best = max(valid_threshold, key=lambda x: x[1])
                if valid_calibrator:
                    cal_best = max(valid_calibrator, key=lambda x: x[1])
                    if cal_best[1] - best[1] >= 0.005:  # DrawCal 需高出 >=0.5pp 才选
                        best = cal_best
                best_name, best_acc, best_dr, best_pred, best_method, best_thr_f, best_cal_f = best
            else:
                best_name, best_acc, best_dr, best_pred, best_method, best_thr_f, best_cal_f = max(
                    valid_cands, key=lambda x: x[1])
        else:
            best_name, best_acc, best_dr, best_pred, best_method, best_thr_f, best_cal_f = max(
                candidates, key=lambda x: x[2])

        per_class_final = recall_score(y_val, best_pred, labels=[0, 1, 2], average=None)
        f1_final = f1_score(y_val, best_pred, average='macro')
        best_cr = classification_report(y_val, best_pred, output_dict=True,
                                        labels=[0, 1, 2],
                                        target_names=['客胜', '平局', '主胜'],
                                        zero_division=0)

        train_val_gap = (train_metrics.get('train_accuracy', 0) - best_acc) if train_metrics else 0.0

        overfit_flag = '❌' if train_val_gap > 0.06 else '⚠️' if train_val_gap > 0.03 else '✅'
        draw_meet_flag = '✅' if best_dr >= 0.28 else ('⚠️' if best_dr >= 0.20 else '❌')

        print(f"\n  ┌──────────────────────────────────────────────────────────────────────┐")
        print(f"  │  {mname:^71}  │")
        print(f"  ├──────────────────────────────────────────────────────────────────────┤")
        print(f"  │  训练准确率: {train_metrics.get('train_accuracy', 0):.4f}   │ 验证准确率(原始): {acc_orig:.4f}   │")
        print(f"  │  过拟合程度: {train_val_gap:+.4f}   {overfit_flag:<4} (gap>6%=严重)              │")
        print(f"  ├────────── 平局召回率对比 (自适应搜索最优) ─────────────────────────────────┤")
        print(f"  │  {'方法':<42} {'准确率':>8}  {'平局召回':>8}  {'达标':<4}   │")
        for (name, a, dr, pred, method, tf, cf) in candidates:
            mflag = '✅' if dr >= 0.28 else ('  ' if dr >= 0.20 else '❌')
            sel = '←选' if name == best_name else '   '
            print(f"  │  {name:<42} {a:>8.4f}  {dr:>8.4f}  {mflag:<4} {sel} │")
        print(f"  ├──────────────────────────────────────────────────────────────────────┤")
        print(f"  │  最终推荐: {best_name}")
        print(f"  │  最终准确率: {best_acc:.4f}   │ 平局召回率: {best_dr:.4f} {draw_meet_flag}    │")
        print(f"  │  准确率损失(vs原始): {best_acc - acc_orig:+.4f}   (目标: dr≥0.28下最小)       │")
        print(f"  │  F1(macro):  {f1_final:.4f}   │ 平局精确率: {best_cr['平局']['precision']:.4f}     │")
        print(f"  │  概率校准 ECE: 原始={ece_orig:.4f} │ DrawCal={ece_cal_opt:.4f}                 │")
        print(f"  └──────────────────────────────────────────────────────────────────────┘")
        logger.log_evaluation(f'{mname}_阈值搜索对比', {
            'threshold': {'factor': float(thr_factor_opt), 'accuracy': float(acc_thr_opt), 'draw_recall': float(dr_thr_opt)},
            'calibrator': {'factor': float(cal_factor_opt), 'accuracy': float(acc_cal_opt), 'draw_recall': float(dr_cal_opt)},
            'original': {'accuracy': float(acc_orig), 'draw_recall': float(cr_orig['平局']['recall'])},
            'best': {'label': best_name, 'method': best_method, 'accuracy': float(best_acc), 'draw_recall': float(best_dr)},
        })

        final_report['models'][mname] = {
            'training': {
                'train_accuracy': float(train_metrics.get('train_accuracy', 0)),
                'train_logloss': float(train_metrics.get('train_log_loss', 0)),
                'val_accuracy_original': float(acc_orig),
                'val_logloss_original': float(train_metrics.get('log_loss', 0)),
                'train_val_accuracy_gap': float(train_val_gap),
                'overfit_level': 'severe' if train_val_gap > 0.06
                else ('moderate' if train_val_gap > 0.03 else 'mild'),
            },
            'adaptive_search': {
                'draw_recall_target': DRAW_RECALL_TARGET,
                'threshold_search': {
                    'factor_min': 1.00, 'factor_max': 2.50, 'factor_step': 0.05,
                    'best_factor': float(thr_factor_opt),
                    'best_accuracy': float(acc_thr_opt),
                    'best_draw_recall': float(dr_thr_opt),
                    'best_draw_precision': float(cr_thr_opt['平局']['precision']),
                },
                'draw_calibrator_search': {
                    'factor_min': 0.50, 'factor_max': 1.00, 'factor_step': 0.01,
                    'best_factor': float(cal_factor_opt),
                    'best_accuracy': float(acc_cal_opt),
                    'best_draw_recall': float(dr_cal_opt),
                    'best_draw_precision': float(cr_cal_opt['平局']['precision']),
                },
            },
            'candidates': {
                method_key: {
                    'label': name,
                    'accuracy': float(a),
                    'draw_recall': float(dr),
                    'draw_precision': float(
                        (classification_report(y_val, pred, output_dict=True,
                                                labels=[0, 1, 2],
                                                target_names=['客胜', '平局', '主胜'],
                                                zero_division=0))['平局']['precision']),
                    'f1_macro': float(f1_score(y_val, pred, average='macro')),
                    'draw_target_028_met': bool(dr >= 0.28),
                    'classification_report': classification_report(
                        y_val, pred, output_dict=True,
                        labels=[0, 1, 2],
                        target_names=['客胜', '平局', '主胜'],
                        zero_division=0),
                    'threshold_factor': float(tf) if tf is not None else None,
                    'calibrator_factor': float(cf) if cf is not None else None,
                }
                for (name, a, dr, pred, method_key, tf, cf) in candidates
            },
            'recommended': {
                'method': best_method,
                'label': best_name,
                'draw_calibrator_factor': float(best_cal_f) if best_cal_f is not None else None,
                'draw_threshold_factor': float(best_thr_f) if best_thr_f is not None else None,
                'accuracy': float(best_acc),
                'draw_recall': float(best_dr),
                'draw_recall_target_028_met': bool(best_dr >= 0.28),
                'draw_precision': float(best_cr['平局']['precision']),
                'f1_macro': float(f1_final),
                'ece_original': float(ece_orig),
                'ece_after_calibration': float(ece_cal_opt),
                'logloss_after_calibration': float(ll_cal_opt),
                'home_recall': float(per_class_final[2]),
                'away_recall': float(per_class_final[0]),
                'accuracy_loss_vs_original_pp': float((best_acc - acc_orig) * 100),
            },
        }
        logger.log_evaluation(f'{mname}平局校准汇总', final_report['models'][mname]['recommended'])

    import joblib
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    print(f"\n10. 保存校准器参数与评估报告...")
    calibrator_params_path = os.path.join(OUTPUT_DIR, f'draw_calibrator_params_{timestamp}.json')
    per_model_calibration = {}
    for mname, mdata in final_report['models'].items():
        rec = mdata['recommended']
        per_model_calibration[mname] = {
            'method': rec['method'],
            'label': rec['label'],
            'draw_threshold_factor': rec.get('draw_threshold_factor'),
            'draw_calibrator_factor': rec.get('draw_calibrator_factor'),
            'accuracy': rec['accuracy'],
            'draw_recall': rec['draw_recall'],
            'draw_recall_target_028_met': rec['draw_recall_target_028_met'],
            'accuracy_loss_vs_original_pp': rec.get('accuracy_loss_vs_original_pp'),
        }
    calibrator_params = {
        'method': 'Adaptive hybrid search (Threshold + DrawCalibrator, constrained optimization)',
        'strategy': '优先满足平局召回率≥0.28，再最大化准确率（最小化准确率损失）',
        'draw_recall_target': DRAW_RECALL_TARGET,
        'original_defaults': {
            'draw_threshold_factor_default': DRAW_THRESHOLD_FACTOR,
            'draw_calibrator_factor_default': DRAW_CALIBRATOR_FACTOR,
            'draw_calibrator_factor_conservative': DRAW_CALIBRATOR_CONSERVATIVE,
        },
        'per_model': per_model_calibration,
        'search_details': {
            mname: mdata.get('adaptive_search', {})
            for mname, mdata in final_report['models'].items()
        },
    }
    with open(calibrator_params_path, 'w', encoding='utf-8') as f:
        json.dump(calibrator_params, f, ensure_ascii=False, indent=2)
    print(f"   校准器参数: {calibrator_params_path}")

    report_path = os.path.join(OUTPUT_DIR, f'final_training_report_{timestamp}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(final_report, f, ensure_ascii=False, indent=2)
    print(f"   最终评估报告: {report_path}")
    
    print("\n10. 保存模型为pkl格式...")
    saved_models = []
    if xgb_model:
        xgb_path = os.path.join(OUTPUT_DIR, f'xgb_model_{timestamp}.pkl')
        joblib.dump(xgb_model, xgb_path)
        print(f"   XGBoost模型: {xgb_path}")
        saved_models.append('XGBoost')
    
    if lgb_model:
        lgb_path = os.path.join(OUTPUT_DIR, f'lgb_model_{timestamp}.pkl')
        joblib.dump(lgb_model, lgb_path)
        print(f"   LightGBM模型: {lgb_path}")
    
    scaler_pkl_path = os.path.join(OUTPUT_DIR, f'scaler_{timestamp}.pkl')
    joblib.dump(scaler, scaler_pkl_path)
    print(f"   标准化器: {scaler_pkl_path}")
    
    features_pkl_path = os.path.join(OUTPUT_DIR, f'selected_features_{timestamp}.pkl')
    joblib.dump(X.columns.tolist(), features_pkl_path)
    print(f"   特征列表: {features_pkl_path}")
    
    print("\n11. 保存特征标准化参数...")
    scaler_params = {
        'mean': scaler.mean_.tolist(),
        'scale': scaler.scale_.tolist(),
        'feature_names': X.columns.tolist()
    }
    scaler_path = os.path.join(OUTPUT_DIR, 'feature_scaler_params.js')
    with open(scaler_path, 'w', encoding='utf-8') as f:
        f.write(f"var FEATURE_SCALER_PARAMS = {json.dumps(scaler_params, indent=2)};")
    print(f"   已保存到 {scaler_path}")
    
    print("\n11. 转换模型为JavaScript格式...")
    if xgb_model:
        xgb_js = convert_xgb_to_js(xgb_model)
        save_model_to_js(xgb_js, 'XGB')
    
    if lgb_model:
        lgb_js = convert_lgb_to_js(lgb_model)
        save_model_to_js(lgb_js, 'LGB')
    
    print("\n12. 保存概率校准参数...")
    if xgb_calibration:
        save_calibration_params(xgb_calibration, 'XGB')
    
    if lgb_calibration:
        save_calibration_params(lgb_calibration, 'LGB')
    
    print("\n" + "=" * 60)
    print("训练完成!")
    print("=" * 60)
    
    if xgb_metrics:
        print(f"\nXGBoost 指标:")
        print(f"  准确率: {xgb_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {xgb_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {xgb_metrics['brier']:.4f}")
    
    if lgb_metrics:
        print(f"\nLightGBM 指标:")
        print(f"  准确率: {lgb_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {lgb_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {lgb_metrics['brier']:.4f}")
    
    print("\n特征重要性:")
    if xgb_model:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 8))
        xgb.plot_importance(xgb_model, ax=ax, max_num_features=15)
        plt.savefig(os.path.join(OUTPUT_DIR, 'xgb_importance.png'))
        plt.close()
        print("  XGBoost: 已保存到 xgb_importance.png")
    
    if lgb_model:
        fig, ax = plt.subplots(figsize=(12, 8))
        lgb.plot_importance(lgb_model, ax=ax, max_num_features=15)
        plt.savefig(os.path.join(OUTPUT_DIR, 'lgb_importance.png'))
        plt.close()
        print("  LightGBM: 已保存到 lgb_importance.png")
    
    # 记录训练完成
    logger.log_training_completion({
        'models_trained': saved_models,
        'best_model': 'XGBoost' if xgb_metrics and (not lgb_metrics or xgb_metrics.get('accuracy', 0) > lgb_metrics.get('accuracy', 0)) else 'LightGBM',
        'best_accuracy': xgb_metrics.get('accuracy', 0) if xgb_metrics else (lgb_metrics.get('accuracy', 0) if lgb_metrics else 0),
        'output_dir': str(OUTPUT_DIR),
        'timestamp': timestamp,
        'total_features': X.shape[1],
        'total_matches': len(df),
        'config': {
            'validation_split': validation_split,
            'incremental': incremental,
            'calibration_enabled': calibration_enabled
        }
    })
    
    print(f"\n训练日志已保存到: {logger.log_file}")
    print("日志摘要:")
    print(logger.get_log_summary())
    
    final_brier = None
    final_accuracy = None
    if xgb_metrics:
        final_brier = xgb_metrics['brier']
        final_accuracy = xgb_metrics['accuracy']
    elif lgb_metrics:
        final_brier = lgb_metrics['brier']
        final_accuracy = lgb_metrics['accuracy']
    
    print(f"\nbrier_score: {final_brier:.4f}" if final_brier else "\nbrier_score: null")
    print(f"accuracy: {final_accuracy:.4f}" if final_accuracy else "accuracy: null")
    print(f"version: {version}" if version else "version: null")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='足球比赛预测模型训练')
    parser.add_argument('--incremental', action='store_true', help='增量训练模式')
    parser.add_argument('--version', type=str, help='模型版本号')
    args = parser.parse_args()
    main(incremental=args.incremental, version=args.version)
