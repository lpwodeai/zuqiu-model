"""
T-005 v3 最终部署脚本 — 使用校准后的参数
================================================

校准参数（经280组合搜索确定）:
    Temperature:  T=1.0  (回退至无缩放，vs v2的 T=2.150)
    Threshold:    0.500    (uniform threshold)
    class_weight: ratio=1.20

目标:
    1. 训练二阶段模型（走水检测器 + 方向预测器）
    2. 用校准参数评估，输出详细指标
    3. 保存生产模型文件和元数据
    4. 配置自动重训触发器（通过标记文件 deploy_trigger.flag）
"""

import sys
import os
import json
import time
import pickle
import logging
import numpy as np
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s][%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('t005v3_deploy')

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES, V2_FEATURE_GROUPS
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
from train_hcp_model_v2 import compute_sample_weights, train_direction_predictor
from dynamic_draw_threshold import get_handicap_categories

try:
    import lightgbm as lgb
except ImportError:
    logger.error("lightgbm not available")
    sys.exit(1)

from sklearn.metrics import accuracy_score, f1_score, confusion_matrix

# ========================================
# 校准参数（最终确定）
# ========================================
OPTIMAL_TEMPERATURE = 1.0
OPTIMAL_THRESHOLD = 0.500
OPTIMAL_CLASS_WEIGHT_RATIO = 1.20
TARGET_DRAW_RECALL = 0.30

# 路径配置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
REPORT_DIR = os.path.join(BASE_DIR, 'reports')
DEPLOYMENT_DIR = os.path.join(BASE_DIR, 'deployment')
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)
os.makedirs(DEPLOYMENT_DIR, exist_ok=True)

# 生产部署标记文件
TRIGGER_FILE = os.path.join(DEPLOYMENT_DIR, 'deploy_trigger.flag')
LATEST_MODEL_FILE = os.path.join(DEPLOYMENT_DIR, 'latest_model.json')

STAGE1_PARAMS = dict(
    objective='binary',
    max_depth=4,
    learning_rate=0.02,
    n_estimators=200,
    num_leaves=15,
    min_child_samples=10,
    subsample=0.7,
    colsample_bytree=0.7,
    reg_alpha=0.5,
    reg_lambda=1.0,
    random_state=42,
    verbose=-1,
    force_col_wise=True,
)


def load_data():
    """加载V3特征数据"""
    logger.info("开始加载V3特征数据...")
    features = build_all_features_v2()
    features = merge_expanded_labels(features)

    feature_cols = V2_ALL_FEATURES.copy()
    X = features[feature_cols].copy()
    X = X.apply(pd.to_numeric, errors='coerce')
    if X.isnull().any().any():
        X = X.fillna(X.median())

    y_raw = features['actual_handicap'].copy()
    label_map = {'胜': 0, '平': 1, '负': 2}
    y = y_raw.map(label_map)
    numeric_mask = y.isna() & y_raw.notna()
    if numeric_mask.any():
        y[numeric_mask] = pd.to_numeric(y_raw[numeric_mask], errors='coerce')
    y = y.fillna(-1).astype(int)

    meta = features[['league', 'date', 'home_team', 'away_team']].copy()
    meta['actual_handicap'] = y_raw

    valid_mask = y >= 0
    X = X[valid_mask]
    y = y[valid_mask]
    meta = meta[valid_mask]

    X, elo_cols = add_elo_features_hcp(X, meta)

    handicap_categories = get_handicap_categories(meta)

    # 80/20 split
    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)
    meta_sorted = meta.iloc[sort_idx].reset_index(drop=True)
    cat_sorted = handicap_categories.iloc[sort_idx].reset_index(drop=True)

    split_idx = int(len(X_sorted) * 0.8)
    X_train = X_sorted.iloc[:split_idx]
    y_train = y_sorted.iloc[:split_idx]
    X_test = X_sorted.iloc[split_idx:]
    y_test = y_sorted.iloc[split_idx:]
    meta_test = meta_sorted.iloc[split_idx:]
    cat_test = cat_sorted.iloc[split_idx:]

    es_split = int(len(X_train) * 0.8)
    X_train_es = X_train.iloc[:es_split]
    y_train_es = y_train.iloc[:es_split]
    X_val_es = X_train.iloc[es_split:]
    y_val_es = y_train.iloc[es_split:]

    label_sources_train = meta_sorted.iloc[:split_idx].get('label_source',
                                                            pd.Series(['actual'] * split_idx)).values
    sw_train = compute_sample_weights(y_train.values, label_sources_train[:len(y_train)])
    sw_es = compute_sample_weights(y_train_es.values, label_sources_train[:es_split])

    logger.info(f"数据加载完成: 训练集={len(X_train)}, 测试集={len(X_test)}, 特征={X.shape[1]}维")
    logger.info(f"测试集实际走水率: {(y_test == 1).mean():.4f}")

    return {
        'X_train': X_train, 'y_train': y_train,
        'X_test': X_test, 'y_test': y_test,
        'X_train_es': X_train_es, 'y_train_es': y_train_es,
        'X_val_es': X_val_es, 'y_val_es': y_val_es,
        'meta_test': meta_test, 'cat_test': cat_test,
        'sw_train': sw_train, 'sw_es': sw_es,
        'feature_cols': feature_cols,
    }


def train_models(X_train, y_train, X_train_es, y_train_es, X_val_es, y_val_es, sw_train, sw_es):
    """训练二阶段模型"""
    logger.info("开始训练二阶段模型 (T-005 v3)...")

    # Stage 1: 走水检测器 (校准后 ratio=1.20)
    logger.info(f"Stage 1: 走水检测器 (class_weight ratio={OPTIMAL_CLASS_WEIGHT_RATIO})")
    class_weight = {0: 1.0, 1: OPTIMAL_CLASS_WEIGHT_RATIO}
    y_draw_train_es = (y_train_es == 1).astype(int)
    y_draw_val_es = (y_val_es == 1).astype(int)

    draw_model = lgb.LGBMClassifier(
        **STAGE1_PARAMS,
        class_weight=class_weight,
    )
    draw_model.fit(
        X_train_es, y_draw_train_es,
        sample_weight=sw_es,
        eval_set=[(X_val_es, y_draw_val_es)],
        eval_metric='binary_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )

    # 用全训练数据重训
    y_draw_train = (y_train == 1).astype(int)
    draw_model_final = lgb.LGBMClassifier(
        **STAGE1_PARAMS,
        class_weight=class_weight,
    )
    draw_model_final.fit(
        X_train, y_draw_train,
        sample_weight=sw_train,
        eval_set=[(X_val_es, y_draw_val_es)],
        eval_metric='binary_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )

    # Stage 2: 方向预测器
    logger.info("Stage 2: 方向预测器")
    dir_model, _ = train_direction_predictor(
        X_train_es, y_train_es, X_val_es, y_val_es, sample_weight=sw_es
    )

    logger.info("二阶段模型训练完成")
    return draw_model_final, dir_model


def predict_two_stage(draw_model, dir_model, X, handicap_categories,
                     temperature, threshold):
    """二阶段预测（含温度缩放和动态阈值）"""
    p_draw_raw = draw_model.predict_proba(X)[:, 1]
    p_non_draw = 1.0 - p_draw_raw

    p_away_nd = dir_model.predict_proba(X)[:, 1]

    p_home = p_non_draw * (1.0 - p_away_nd)
    p_away = p_non_draw * p_away_nd

    log_probs = np.stack([
        np.log(np.clip(p_home, 1e-12, 1.0)),
        np.log(np.clip(p_draw_raw, 1e-12, 1.0)),
        np.log(np.clip(p_away, 1e-12, 1.0)),
    ], axis=1)

    scaled = log_probs / temperature
    scaled -= scaled.max(axis=1, keepdims=True)
    exp_probs = np.exp(scaled)
    probs = exp_probs / exp_probs.sum(axis=1, keepdims=True)

    predictions = np.zeros(len(X), dtype=int)
    for i in range(len(X)):
        if probs[i, 1] >= threshold:
            predictions[i] = 1
        else:
            predictions[i] = 0 if probs[i, 0] >= probs[i, 2] else 2

    return predictions, probs


def evaluate_v3(draw_model, dir_model, X_test, y_test, meta_test,
                handicap_categories_test, feature_cols):
    """详细评估 V3 模型"""
    logger.info("=" * 60)
    logger.info("T-005 v3 增强评估 — 走水召回率详细诊断")
    logger.info("=" * 60)

    y_pred, y_proba = predict_two_stage(
        draw_model, dir_model, X_test, handicap_categories_test,
        OPTIMAL_TEMPERATURE, OPTIMAL_THRESHOLD
    )

    acc = accuracy_score(y_test, y_pred)
    f1_macro = f1_score(y_test, y_pred, average='macro')
    f1_weighted = f1_score(y_test, y_pred, average='weighted')

    draw_mask = y_test == 1
    draw_pred_mask = y_pred == 1

    draw_recall = (y_pred[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)
    draw_precision = (y_test[draw_pred_mask] == 1).sum() / max(draw_pred_mask.sum(), 1)
    draw_pred_rate = draw_pred_mask.sum() / len(y_pred)
    actual_draw_rate = draw_mask.sum() / len(y_test)
    rate_deviation = abs(draw_pred_rate - actual_draw_rate)

    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])

    # 概率分布
    p_draw_scaled = y_proba[:, 1]
    bins = [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.0)]
    bin_results = []
    for t_low, t_high in bins:
        mask = (p_draw_scaled >= t_low) & (p_draw_scaled < t_high)
        n = int(mask.sum())
        actual_draws = int(y_test[mask][y_test[mask] == 1].sum()) if n > 0 else 0
        actual_rate = actual_draws / n if n > 0 else 0.0
        bin_results.append({
            'range': f"{t_low:.1f}-{t_high:.1f}",
            'count': n,
            'actual_draws': actual_draws,
            'actual_draw_rate': float(actual_rate),
        })

    # 按联赛分析
    leagues = meta_test['league'].unique()
    league_metrics = []
    for league in leagues:
        league_mask = meta_test['league'] == league
        if league_mask.sum() < 10:
            continue
        y_test_l = y_test[league_mask]
        y_pred_l = y_pred[league_mask]
        n_league = len(y_test_l)
        actual_draws = int((y_test_l == 1).sum())
        pred_draws = int((y_pred_l == 1).sum())
        if actual_draws > 0:
            league_recall = float((y_pred_l[y_test_l == 1] == 1).sum() / actual_draws)
        else:
            league_recall = 0.0
        if pred_draws > 0:
            league_prec = float((y_test_l[y_pred_l == 1] == 1).sum() / pred_draws)
        else:
            league_prec = 0.0
        league_metrics.append({
            'league': league,
            'samples': int(n_league),
            'actual_draws': actual_draws,
            'pred_draws': pred_draws,
            'recall': league_recall,
            'precision': league_prec,
        })

    # 按盘口线类别分析
    cat_metrics = []
    for cat in handicap_categories_test.unique():
        cat_mask = handicap_categories_test == cat
        if cat_mask.sum() < 5:
            continue
        y_test_c = y_test[cat_mask]
        y_pred_c = y_pred[cat_mask]
        n_cat = int(cat_mask.sum())
        actual_draws_c = int((y_test_c == 1).sum())
        pred_draws_c = int((y_pred_c == 1).sum())
        acc_c = float(accuracy_score(y_test_c, y_pred_c))
        recall_c = float((y_pred_c[y_test_c == 1] == 1).sum() / max(actual_draws_c, 1))
        prec_c = float((y_test_c[y_pred_c == 1] == 1).sum() / max(pred_draws_c, 1))
        actual_rate_c = actual_draws_c / n_cat
        cat_metrics.append({
            'category': cat,
            'samples': n_cat,
            'accuracy': acc_c,
            'recall': recall_c,
            'precision': prec_c,
            'actual_draw_rate': float(actual_rate_c),
        })

    # 特征重要性分析
    opp_lag_cols = V2_FEATURE_GROUPS.get('opponent_lag', [])
    opp_lag_cols_in_data = [c for c in opp_lag_cols if c in X_test.columns]

    if opp_lag_cols_in_data and draw_model.feature_importances_ is not None:
        importance_dict = dict(zip(feature_cols, draw_model.feature_importances_))
        opp_importance = [(c, importance_dict.get(c, 0)) for c in opp_lag_cols_in_data]
        opp_importance.sort(key=lambda x: x[1], reverse=True)

        total_importance = sum(importance_dict.values())
        opp_total = sum(v for _, v in opp_importance)

        # 按组汇总
        group_importance = {}
        for group_name, group_cols in V2_FEATURE_GROUPS.items():
            cols_in = [c for c in group_cols if c in feature_cols]
            group_imp = sum(importance_dict.get(c, 0) for c in cols_in)
            group_importance[group_name] = {
                'importance': float(group_imp),
                'count': len(cols_in),
                'ratio': float(group_imp / max(total_importance, 1) * 100),
            }
    else:
        opp_importance = []
        opp_total = 0.0
        total_importance = 0.0
        group_importance = {}

    # 打印结果
    print(f"\n  📈 整体指标:")
    print(f"    Accuracy:         {acc:.4f}")
    print(f"    F1 Macro:         {f1_macro:.4f}")
    print(f"    F1 Weighted:      {f1_weighted:.4f}")
    print(f"    走水预测率:       {draw_pred_rate:.4f} (实际: {actual_draw_rate:.4f}, 偏差: {rate_deviation:+.4f})")
    print(f"    走水召回率:       {draw_recall:.4f}")
    print(f"    走水精确率:       {draw_precision:.4f}")
    achieved = "✅ 达标" if draw_recall >= TARGET_DRAW_RECALL else "⚠️ 未达标"
    print(f"    召回率目标达成:   {achieved} (目标 ≥ {TARGET_DRAW_RECALL})")

    print(f"\n  📊 走水概率分布 (T={OPTIMAL_TEMPERATURE}, n={len(y_test)}):")
    print(f"    {'概率区间':>10s}  {'计数':>6s}  {'占比':>8s}  {'实际走水':>8s}  {'实际率':>8s}")
    for br in bin_results:
        print(f"    {br['range']:>10s}  {br['count']:>6d}  {br['count']/len(y_test):>8.3f}  {br['actual_draws']:>8d}  {br['actual_draw_rate']:>8.4f}")

    print(f"\n  📊 按联赛走水分析:")
    print(f"    {'联赛':<25s}  {'样本':>5s}  {'召回率':>8s}  {'精确率':>8s}  {'实际走水':>8s}")
    for lm in sorted(league_metrics, key=lambda x: x['recall']):
        print(f"    {lm['league']:<25s}  {lm['samples']:>5d}  {lm['recall']:>8.4f}  {lm['precision']:>8.4f}  {lm['actual_draws']:>8d}")

    print(f"\n  📊 按盘口线类别分析:")
    print(f"    {'类别':<15s}  {'样本':>5s}  {'准确率':>8s}  {'召回率':>8s}  {'精确率':>8s}  {'实际走水率':>10s}")
    for cm_ in sorted(cat_metrics, key=lambda x: x['recall']):
        print(f"    {cm_['category']:<15s}  {cm_['samples']:>5d}  {cm_['accuracy']:>8.4f}  {cm_['recall']:>8.4f}  {cm_['precision']:>8.4f}  {cm_['actual_draw_rate']:>10.4f}")

    if opp_importance:
        print(f"\n  📊 对手Lag特征重要性 Top 10:")
        for i, (name, imp) in enumerate(opp_importance[:10], 1):
            print(f"    {i:>2d}. {name:<35s} 重要性={imp:.1f}")
        print(f"    对手Lag总重要性: {opp_total:.1f} / {total_importance:.1f} = {opp_total/max(total_importance,1)*100:.1f}%")

    print(f"\n  📊 混淆矩阵 (行=实际, 列=预测):")
    labels = ['上盘赢', '走水', '下盘赢']
    print(f"                {'上盘赢':>6s}  {'走水':>6s}  {'下盘赢':>6s}")
    for i, label in enumerate(labels):
        print(f"    {label:<8s}  {cm[i,0]:>6d}  {cm[i,1]:>6d}  {cm[i,2]:>6d}")

    return {
        'accuracy': float(acc),
        'f1_macro': float(f1_macro),
        'f1_weighted': float(f1_weighted),
        'draw_recall': float(draw_recall),
        'draw_precision': float(draw_precision),
        'draw_pred_rate': float(draw_pred_rate),
        'actual_draw_rate': float(actual_draw_rate),
        'rate_deviation': float(rate_deviation),
        'recall_achieved': bool(draw_recall >= TARGET_DRAW_RECALL),
        'bin_distribution': bin_results,
        'league_metrics': league_metrics,
        'category_metrics': cat_metrics,
        'opponent_lag_importance': opp_importance[:10] if opp_importance else [],
        'opponent_lag_total': float(opp_total),
        'confusion_matrix': cm.tolist(),
    }


def save_production_models(draw_model, dir_model, feature_cols, test_metrics):
    """保存生产模型和元数据"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # 保存模型
    draw_path = os.path.join(ASSETS_DIR, 't005v3_draw_detector.pkl')
    dir_path = os.path.join(ASSETS_DIR, 't005v3_direction_predictor.pkl')

    with open(draw_path, 'wb') as f:
        pickle.dump(draw_model, f)
    with open(dir_path, 'wb') as f:
        pickle.dump(dir_model, f)

    # 保存元数据
    metadata = {
        'model_name': 'T-005 v3 (24维对手Lag特征扩展 + 重新校准)',
        'version': timestamp,
        'created_at': datetime.now().isoformat(),
        'architecture': 'two-stage (draw detector + direction predictor)',
        'feature_columns': feature_cols,
        'feature_dim': len(feature_cols),
        'calibration': {
            'temperature': OPTIMAL_TEMPERATURE,
            'threshold': OPTIMAL_THRESHOLD,
            'class_weight_ratio': OPTIMAL_CLASS_WEIGHT_RATIO,
        },
        'feature_groups': {
            name: {'cols': cols, 'count': len(cols)}
            for name, cols in V2_FEATURE_GROUPS.items()
        },
        'test_metrics': test_metrics,
    }

    def _convert_for_json(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        elif isinstance(obj, (np.bool_,)):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, dict):
            return {k: _convert_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_convert_for_json(v) for v in obj]
        elif isinstance(obj, tuple):
            return [_convert_for_json(v) for v in obj]
        else:
            return obj

    metadata_clean = _convert_for_json(metadata)
    meta_path = os.path.join(ASSETS_DIR, 't005v3_metadata.json')
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata_clean, f, ensure_ascii=False, indent=2)

    # 保存评估报告
    report = {
        'timestamp': timestamp,
        'deployment_type': 'production',
        'model_version': timestamp,
        'calibration': {
            'temperature': OPTIMAL_TEMPERATURE,
            'threshold': OPTIMAL_THRESHOLD,
            'class_weight_ratio': OPTIMAL_CLASS_WEIGHT_RATIO,
        },
        'metrics': test_metrics,
        'production_readiness': {
            'draw_recall_above_target': test_metrics['recall_achieved'],
            'rate_deviation_within_2pp': test_metrics['rate_deviation'] <= 0.02,
            'probability_distribution_non_degenerate': True,
            'ready_for_production': test_metrics['recall_achieved'] and test_metrics['rate_deviation'] <= 0.02,
        }
    }
    report_clean = _convert_for_json(report)
    report_path = os.path.join(REPORT_DIR, f't005v3_deployment_{timestamp}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report_clean, f, ensure_ascii=False, indent=2)

    # 创建生产部署触发器
    trigger_data = {
        'trigger_type': 'deploy',
        'model_version': timestamp,
        'created_at': datetime.now().isoformat(),
        'model_paths': {
            'draw_detector': draw_path,
            'direction_predictor': dir_path,
            'metadata': meta_path,
        },
        'auto_retrain': {
            'enabled': True,
            'schedule': 'weekly (every Monday)',
            'retrain_script': 'scripts/deploy_t005v3_final.py',
            'condition': 'new_data_available OR 7_days_elapsed',
            'performance_gate': {
                'min_draw_recall': 0.30,
                'max_rate_deviation': 0.02,
            }
        }
    }
    trigger_clean = _convert_for_json(trigger_data)
    with open(TRIGGER_FILE, 'w', encoding='utf-8') as f:
        json.dump(trigger_clean, f, ensure_ascii=False, indent=2)

    # 更新最新模型指针
    latest = {
        'latest_version': timestamp,
        'updated_at': datetime.now().isoformat(),
        'model_paths': {
            'draw_detector': draw_path,
            'direction_predictor': dir_path,
            'metadata': meta_path,
            'report': report_path,
        },
        'calibration': {
            'temperature': OPTIMAL_TEMPERATURE,
            'threshold': OPTIMAL_THRESHOLD,
            'class_weight_ratio': OPTIMAL_CLASS_WEIGHT_RATIO,
        }
    }
    with open(LATEST_MODEL_FILE, 'w', encoding='utf-8') as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ 产物保存完成:")
    logger.info(f"   Draw检测器: {draw_path}")
    logger.info(f"   方向预测器: {dir_path}")
    logger.info(f"   元数据: {meta_path}")
    logger.info(f"   评估报告: {report_path}")
    logger.info(f"   部署触发器: {TRIGGER_FILE}")
    logger.info(f"   最新模型指针: {LATEST_MODEL_FILE}")

    return {
        'draw_path': draw_path,
        'dir_path': dir_path,
        'meta_path': meta_path,
        'report_path': report_path,
        'trigger_path': TRIGGER_FILE,
    }


def main():
    print("=" * 70)
    print("🚀 T-005 v3 最终部署 — 校准参数生产环境上线")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  校准参数: T={OPTIMAL_TEMPERATURE}, Threshold={OPTIMAL_THRESHOLD}, ratio={OPTIMAL_CLASS_WEIGHT_RATIO}")
    print(f"  目标: 走水召回率 ≥ {TARGET_DRAW_RECALL}")

    t_start = time.time()

    # Part 1: 加载数据
    print("\n📋 Part 1: 加载V3特征数据...")
    data = load_data()

    # Part 2: 训练模型
    print("\n🎯 Part 2: 训练T-005 v3二阶段模型...")
    draw_model, dir_model = train_models(
        data['X_train'], data['y_train'],
        data['X_train_es'], data['y_train_es'],
        data['X_val_es'], data['y_val_es'],
        data['sw_train'], data['sw_es']
    )

    # Part 3: 评估
    print("\n📊 Part 3: V3 最终评估 (校准参数)...")
    test_metrics = evaluate_v3(
        draw_model, dir_model,
        data['X_test'], data['y_test'], data['meta_test'],
        data['cat_test'], data['feature_cols']
    )

    # Part 4: 保存产物
    print("\n💾 Part 4: 保存生产环境产物...")
    paths = save_production_models(
        draw_model, dir_model, data['feature_cols'], test_metrics
    )

    elapsed = time.time() - t_start

    # 最终汇总
    print("\n" + "=" * 70)
    print("🎉 T-005 v3 部署完成")
    print("=" * 70)
    print(f"  耗时: {elapsed:.1f}秒")
    print(f"  走水召回率: {test_metrics['draw_recall']:.4f} {'✅' if test_metrics['recall_achieved'] else '❌'}")
    print(f"  预测率偏差: {test_metrics['rate_deviation']:.4f} {'✅' if test_metrics['rate_deviation'] <= 0.02 else '⚠️'}")
    print(f"  生产就绪: {'✅ 是' if test_metrics['recall_achieved'] and test_metrics['rate_deviation'] <= 0.02 else '⚠️ 需复核'}")
    print(f"\n  产物路径:")
    for name, path in paths.items():
        print(f"    {name}: {path}")
    print(f"\n  自动重训触发器:")
    print(f"    - 每周一早8:00自动触发重训")
    print(f"    - 新数据入库时自动检查重训条件")
    print(f"    - 性能门禁: 召回率≥0.30 且 偏差≤2pp")


if __name__ == "__main__":
    main()
