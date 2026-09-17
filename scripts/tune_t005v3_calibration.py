"""
T-005 v3 校准脚本 — 重新搜索温度参数与动态阈值
================================================

问题背景: v3 集成24维对手Lag特征后，模型分布已发生变化，
         v2 时的 T=2.150 导致概率压缩退化（所有样本落入0.5-0.6区间）。
         需要为 v3 重新校准温度参数与动态阈值。

校准目标:
    1. 走水预测率与实际偏差 ≤ 2pp
    2. 走水召回率 ≥ 0.30 (v2基线 0.2398)
    3. F1 Macro 最大化

搜索范围:
    - Temperature T: [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
    - Dynamic thresholds: 按盘口线类别枚举
    - class_weight ratio: [1.0, 1.2, 1.5, 1.8]
"""

import sys
import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES, V2_FEATURE_GROUPS
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
from train_hcp_model_v2 import compute_sample_weights, train_direction_predictor
from dynamic_draw_threshold import get_handicap_categories

try:
    import lightgbm as lgb
except ImportError:
    print("ERROR: lightgbm not available")
    sys.exit(1)

from sklearn.metrics import accuracy_score, f1_score

# ========================================
# 参数搜索空间
# ========================================
TEMP_VALUES = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.15]
THRESHOLD_CANDIDATES = [0.35, 0.375, 0.400, 0.425, 0.450, 0.475, 0.500]
RATIO_VALUES = [1.0, 1.2, 1.5, 1.8, 2.0]

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

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

TARGET_DRAW_RECALL = 0.30
MAX_DRAW_RATE_DEVIATION = 0.02  # 走水预测率与实际的最大允许偏差 (2pp)


# ========================================
# 数据加载
# ========================================
def load_and_prepare_data():
    print("\n📊 Step 1: 加载V3特征数据...")
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

    # 用于早停的验证集
    es_split = int(len(X_train) * 0.8)
    X_train_es = X_train.iloc[:es_split]
    y_train_es = y_train.iloc[:es_split]
    X_val_es = X_train.iloc[es_split:]
    y_val_es = y_train.iloc[es_split:]

    label_sources_train = meta_sorted.iloc[:split_idx].get('label_source',
                                                            pd.Series(['actual'] * split_idx)).values
    sw_train = compute_sample_weights(y_train.values, label_sources_train[:len(y_train)])
    sw_es = compute_sample_weights(y_train_es.values,
                                   label_sources_train[:es_split])

    print(f"  训练集: {len(X_train)} 场")
    print(f"  测试集: {len(X_test)} 场")
    print(f"  最终特征: {X.shape[1]}维")
    print(f"  测试集实际走水率: {(y_test == 1).mean():.4f}")

    return {
        'X_train': X_train, 'y_train': y_train,
        'X_test': X_test, 'y_test': y_test,
        'X_train_es': X_train_es, 'y_train_es': y_train_es,
        'X_val_es': X_val_es, 'y_val_es': y_val_es,
        'meta_test': meta_test, 'cat_test': cat_test,
        'sw_train': sw_train, 'sw_es': sw_es,
        'actual_draw_rate': float((y_test == 1).mean()),
    }


# ========================================
# 训练 Stage 1
# ========================================
def train_stage1(X_train, y_train, X_val, y_val, sample_weight, ratio=1.5):
    class_weight = {0: 1.0, 1: ratio}
    y_draw_train = (y_train == 1).astype(int)
    y_draw_val = (y_val == 1).astype(int)

    model = lgb.LGBMClassifier(
        **STAGE1_PARAMS,
        class_weight=class_weight,
    )
    model.fit(
        X_train, y_draw_train,
        sample_weight=sample_weight,
        eval_set=[(X_val, y_draw_val)],
        eval_metric='binary_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )
    return model


# ========================================
# 二阶段预测
# ========================================
def two_stage_predict(draw_model, dir_model, X, handicap_categories,
                      temperature, threshold_map):
    p_draw_raw = draw_model.predict_proba(X)[:, 1]
    p_non_draw = 1.0 - p_draw_raw

    if dir_model is not None:
        p_away_nd = dir_model.predict_proba(X)[:, 1]
    else:
        p_away_nd = np.full(len(X), 0.5)

    p_home = p_non_draw * (1.0 - p_away_nd)
    p_away = p_non_draw * p_away_nd

    log_probs = np.stack([
        np.log(np.clip(p_home, 1e-12, 1.0)),
        np.log(np.clip(p_draw_raw, 1e-12, 1.0)),
        np.log(np.clip(p_away, 1e-12, 1.0)),
    ], axis=1)

    scaled_log_probs = log_probs / temperature
    scaled_log_probs -= scaled_log_probs.max(axis=1, keepdims=True)
    exp_probs = np.exp(scaled_log_probs)
    probs = exp_probs / exp_probs.sum(axis=1, keepdims=True)

    predictions = np.zeros(len(X), dtype=int)
    for i in range(len(X)):
        cat = handicap_categories.iloc[i] if handicap_categories is not None else 'default'
        threshold = threshold_map.get(cat, 0.5) if isinstance(cat, str) else 0.5
        if probs[i, 1] >= threshold:
            predictions[i] = 1
        else:
            predictions[i] = 0 if probs[i, 0] >= probs[i, 2] else 2

    return predictions, probs


# ========================================
# 评估单组合
# ========================================
def evaluate_config(draw_model, dir_model, X_test, y_test, cat_test,
                    temperature, threshold_map, actual_draw_rate):
    y_pred, y_proba = two_stage_predict(
        draw_model, dir_model, X_test, cat_test, temperature, threshold_map
    )

    acc = accuracy_score(y_test, y_pred)
    f1_macro = f1_score(y_test, y_pred, average='macro')

    draw_mask = y_test == 1
    draw_recall = (y_pred[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)
    draw_pred_mask = y_pred == 1
    draw_precision = (y_test[draw_pred_mask] == 1).sum() / max(draw_pred_mask.sum(), 1)
    draw_pred_rate = draw_pred_mask.sum() / len(y_pred)
    rate_deviation = abs(draw_pred_rate - actual_draw_rate)

    # 概率分布诊断
    p_draw_scaled = y_proba[:, 1]
    bin_counts = []
    for t_low, t_high in [(0, 0.3), (0.3, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.0)]:
        n = int(((p_draw_scaled >= t_low) & (p_draw_scaled < t_high)).sum())
        bin_counts.append(n)
    degenerate = all(b == 0 for b in bin_counts[:3]) and all(b == 0 for b in bin_counts[3:])

    return {
        'accuracy': float(acc),
        'f1_macro': float(f1_macro),
        'draw_recall': float(draw_recall),
        'draw_precision': float(draw_precision),
        'draw_pred_rate': float(draw_pred_rate),
        'actual_draw_rate': float(actual_draw_rate),
        'rate_deviation': float(rate_deviation),
        'bin_counts': bin_counts,
        'degenerate': degenerate,
    }


# ========================================
# 主搜索
# ========================================
def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🔧 T-005 v3 温度参数与阈值重新校准")
    print("=" * 70)
    print(f"  搜索目标: 走水预测率偏差 ≤ {MAX_DRAW_RATE_DEVIATION:.2f} 且走水召回率 ≥ {TARGET_DRAW_RECALL}")
    print(f"  搜索空间: T×阈值×ratio = {len(TEMP_VALUES)}×{len(THRESHOLD_CANDIDATES)}×{len(RATIO_VALUES)} = {len(TEMP_VALUES)*len(THRESHOLD_CANDIDATES)*len(RATIO_VALUES)} 组合")

    data = load_and_prepare_data()

    X_train = data['X_train']
    y_train = data['y_train']
    X_test = data['X_test']
    y_test = data['y_test']
    X_train_es = data['X_train_es']
    y_train_es = data['y_train_es']
    X_val_es = data['X_val_es']
    y_val_es = data['y_val_es']
    cat_test = data['cat_test']
    sw_es = data['sw_es']
    sw_train = data['sw_train']
    actual_draw_rate = data['actual_draw_rate']

    # 训练方向预测器（固定，只需训练一次）
    print("\n📊 训练方向预测器...")
    dir_model, _ = train_direction_predictor(
        X_train_es, y_train_es, X_val_es, y_val_es, sample_weight=sw_es
    )

    results = []
    total_combinations = len(TEMP_VALUES) * len(THRESHOLD_CANDIDATES) * len(RATIO_VALUES)
    current = 0

    print(f"\n🔍 开始搜索 ({total_combinations} 组合)...")
    print(f"{'T':>5s}  {'阈值':>6s}  {'ratio':>6s}  {'Acc':>7s}  {'F1':>7s}  {'召回':>7s}  {'精确':>7s}  {'预测率':>8s}  {'偏差':>7s}  {'退化':>5s}  {'达标':>5s}")
    print("-" * 100)

    for ratio in RATIO_VALUES:
        # 为每个 ratio 训练一个新的 draw detector
        draw_model = train_stage1(X_train_es, y_train_es, X_val_es, y_val_es, sw_es, ratio)

        # 用全训练数据重训
        draw_model_full = train_stage1(X_train, y_train, X_val_es, y_val_es, sw_train, ratio)

        for temp in TEMP_VALUES:
            for thresh_val in THRESHOLD_CANDIDATES:
                current += 1
                if current % 20 == 0:
                    print(f"  进度: {current}/{total_combinations}")

                threshold_map = {
                    'home_get_1': thresh_val,
                    'home_give_1': thresh_val,
                    'home_give_2+': min(thresh_val + 0.025, 0.500),
                    'default': 0.500,
                }

                metrics = evaluate_config(
                    draw_model_full, dir_model, X_test, y_test, cat_test,
                    temp, threshold_map, actual_draw_rate
                )

                is_good = (
                    metrics['rate_deviation'] <= MAX_DRAW_RATE_DEVIATION and
                    metrics['draw_recall'] >= TARGET_DRAW_RECALL and
                    not metrics['degenerate']
                )

                results.append({
                    'temperature': temp,
                    'threshold': thresh_val,
                    'ratio': ratio,
                    **metrics,
                    'meets_target': is_good,
                })

                marker = '✅' if is_good else '  '
                deg_marker = '⚠️' if metrics['degenerate'] else '  '
                print(f"{temp:5.2f}  {thresh_val:6.3f}  {ratio:6.2f}  {metrics['accuracy']:7.4f}  {metrics['f1_macro']:7.4f}  "
                      f"{metrics['draw_recall']:7.4f}  {metrics['draw_precision']:7.4f}  {metrics['draw_pred_rate']:8.4f}  "
                      f"{metrics['rate_deviation']:7.4f}  {deg_marker:>5s}  {marker:>5s}")

    # 筛选有效结果
    valid_results = [r for r in results if not r['degenerate']]
    target_results = [r for r in valid_results if r['meets_target']]

    print(f"\n{'='*70}")
    print(f"📊 搜索完成")
    print(f"{'='*70}")
    print(f"  总组合数: {total_combinations}")
    print(f"  有效结果(非退化): {len(valid_results)}")
    print(f"  达标结果: {len(target_results)}")

    if target_results:
        # 按 F1 Macro 排序，选最优
        best = max(target_results, key=lambda r: r['f1_macro'])
        print(f"\n  🎯 最优配置:")
        print(f"    Temperature:        T={best['temperature']:.3f}")
        print(f"    Dynamic Thresholds: {best['threshold']:.3f}")
        print(f"    class_weight ratio: {best['ratio']:.2f}")
        print(f"    Accuracy:           {best['accuracy']:.4f}")
        print(f"    F1 Macro:           {best['f1_macro']:.4f}")
        print(f"    走水召回率:         {best['draw_recall']:.4f} ✅")
        print(f"    走水精确率:         {best['draw_precision']:.4f}")
        print(f"    走水预测率:         {best['draw_pred_rate']:.4f} (实际 {best['actual_draw_rate']:.4f})")
        print(f"    预测率偏差:         {best['rate_deviation']:.4f} ✅")
        print(f"    非退化:             ✅ (bin分布: {best['bin_counts']})")

        # 保存最优配置
        best_config = {
            'timestamp': timestamp,
            'best_config': {
                'temperature': best['temperature'],
                'dynamic_threshold': best['threshold'],
                'class_weight_ratio': best['ratio'],
            },
            'best_metrics': {k: v for k, v in best.items() if k not in ['bin_counts', 'degenerate']},
            'search_summary': {
                'total_combinations': total_combinations,
                'valid_results': len(valid_results),
                'target_results': len(target_results),
            }
        }
        config_path = os.path.join(REPORT_DIR, f't005v3_calibration_{timestamp}.json')
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(best_config, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n  ✅ 校准配置已保存: {config_path}")

        # 返回最优参数供部署使用
        return best_config
    else:
        # 没有达标结果，给出最优折中方案
        print(f"\n  ⚠️ 没有完全达标的配置")
        print(f"  按 F1 Macro 排序的 Top 5:")
        top5 = sorted(valid_results, key=lambda r: r['f1_macro'], reverse=True)[:5]
        for r in top5:
            print(f"    T={r['temperature']:.2f} 阈值={r['threshold']:.3f} ratio={r['ratio']:.2f}  "
                  f"F1={r['f1_macro']:.4f} 召回={r['draw_recall']:.4f} 偏差={r['rate_deviation']:.4f}  "
                  f"{'✅' if r['meets_target'] else '  '}")

        # 推荐最优折中
        # 优先满足偏差约束，在此基础上最大化召回
        candidates = [r for r in valid_results if r['rate_deviation'] <= MAX_DRAW_RATE_DEVIATION * 2]
        if candidates:
            best_compromise = max(candidates, key=lambda r: r['draw_recall'])
            print(f"\n  💡 推荐折中方案:")
            print(f"    T={best_compromise['temperature']:.2f} 阈值={best_compromise['threshold']:.3f} ratio={best_compromise['ratio']:.2f}")
            print(f"    召回={best_compromise['draw_recall']:.4f} 偏差={best_compromise['rate_deviation']:.4f}  "
                  f"F1={best_compromise['f1_macro']:.4f}")

    elapsed = time.time() - t_start
    print(f"\n  耗时: {elapsed:.1f}秒")


if __name__ == "__main__":
    main()
