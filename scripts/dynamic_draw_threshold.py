"""
T-005 v2 动态走水阈值优化
========================

根据盘口线类别自动调整走水检测阈值，替代固定阈值 0.5。

核心思路:
    不同盘口线下走水的基础概率不同：
    - 让1球(-1.0): 主队恰好赢1球即走水 → 走水概率较高 → 阈值应降低
    - 受让1球(+1.0): 客队恰好赢1球即走水 → 走水概率中等
    - 让2球(-2.0): 主队恰好赢2球即走水 → 走水概率较低 → 阈值应提高

方法（防泄露设计）:
    1. 5折 TimeSeriesSplit CV，收集 OOF (out-of-fold) 预测概率
       — 每个样本由未见过该样本的模型预测，无数据泄露
    2. OOF 数据按时间分两半：前半调参(tune) + 后半评估(eval)
       — 阈值在 tune 上搜索，在 eval 上验证，防止过拟合
    3. 按盘口线类别分组，搜索每组最优走水阈值（最大化 Macro-F1）
    4. 对比固定阈值(0.5) vs 动态阈值的准确率/召回率/F1
    5. 保存类别-阈值映射到 assets/ 供部署使用

运行: python scripts/dynamic_draw_threshold.py
"""

import sys
import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, f1_score, confusion_matrix, log_loss
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES, V2_FEATURE_GROUPS
from hcp_features import HCP_RESULT_NAMES
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp, find_latest_expanded_csv
from train_hcp_model_v2 import compute_sample_weights, train_draw_detector, train_direction_predictor

try:
    import lightgbm as lgb
except ImportError:
    print("ERROR: lightgbm not available")
    sys.exit(1)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ========================================
# 盘口线类别映射
# ========================================

def categorize_handicap_line(line):
    """将盘口线数值映射为类别字符串。

    约定（ handicap_line 为负 = 主队让球，正 = 主队受让）:
        home_give_2+:  主队让2球及以上
        home_give_1:   主队让1球
        home_give_half: 主队让半球等（-0.5 等）
        level:         平手（0）
        home_get_half: 主队受让半球等
        home_get_1:    主队受让1球
        home_get_2+:   主队受让2球及以上
        unknown:       无盘口线数据
    """
    if pd.isna(line):
        return 'unknown'
    try:
        line = float(line)
    except (ValueError, TypeError):
        return 'unknown'
    if line <= -2.0:
        return 'home_give_2+'
    elif line <= -1.0:
        return 'home_give_1'
    elif line < 0:
        return 'home_give_half'
    elif line == 0:
        return 'level'
    elif line < 1.0:
        return 'home_get_half'
    elif line < 2.0:
        return 'home_get_1'
    else:
        return 'home_get_2+'


def get_handicap_categories(meta):
    """从扩充数据集 CSV 获取每场比赛的盘口线类别。"""
    csv_path = find_latest_expanded_csv()
    if csv_path is None:
        print("[WARN] 未找到扩充数据集 CSV，所有比赛归为 unknown 类别")
        return pd.Series(['unknown'] * len(meta), index=meta.index)

    df_csv = pd.read_csv(csv_path)
    df_csv = df_csv.drop_duplicates(subset=['match_id'], keep='first')
    line_map = df_csv.set_index('match_id')['handicap_line_pred'].to_dict()

    categories = []
    for idx in meta.index:
        val = line_map.get(str(idx), np.nan)
        categories.append(categorize_handicap_line(val))

    return pd.Series(categories, index=meta.index, name='handicap_category')


# ========================================
# 预测逻辑（与 two_stage_predict 的阈值应用一致）
# ========================================

def predict_batch_with_thresholds(probs, thresholds):
    """
    向量化预测：对每个样本应用独立的走水阈值。

    参数:
        probs: (n, 3) 概率矩阵 [p_home, p_draw, p_away]
        thresholds: (n,) 每个样本的走水阈值

    返回:
        (n,) 预测标签 (0=上盘赢, 1=走水, 2=下盘赢)
    """
    p_home = probs[:, 0]
    p_draw = probs[:, 1]
    p_away = probs[:, 2]

    # 与 two_stage_predict 逻辑一致：
    #   p_draw > threshold → 走水
    #   否则 p_home > p_away → 上盘赢，否则下盘赢
    predictions = np.where(
        p_draw > thresholds, 1,
        np.where(p_home > p_away, 0, 2)
    )
    return predictions.astype(int)


# ========================================
# CV 收集 OOF 概率
# ========================================

def collect_oof_probabilities(X, y, meta, handicap_categories, n_splits=5):
    """
    5折 TimeSeriesSplit CV，收集 OOF 预测概率。

    每个样本由未见过该样本的模型预测（无数据泄露）。
    返回包含概率、真实标签、盘口线类别的 DataFrame。
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)

    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)
    meta_sorted = meta.iloc[sort_idx].reset_index(drop=True)
    cat_sorted = handicap_categories.iloc[sort_idx].reset_index(drop=True)

    label_sources = meta_sorted.get('label_source', pd.Series(['actual'] * len(meta_sorted))).values

    oof_data = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_sorted)):
        X_train, X_val = X_sorted.iloc[train_idx], X_sorted.iloc[val_idx]
        y_train, y_val = y_sorted.iloc[train_idx], y_sorted.iloc[val_idx]
        ls_train = label_sources[train_idx]

        print(f"\n  Fold {fold+1}/{n_splits}: train={len(X_train)}, val={len(X_val)}")

        sw = compute_sample_weights(y_train.values, ls_train)

        # Stage 1: 走水检测
        draw_model, draw_metrics = train_draw_detector(
            X_train, y_train, X_val, y_val, sample_weight=sw
        )
        print(f"    [Stage1] acc={draw_metrics['accuracy']:.4f}, draw_recall={draw_metrics['draw_recall']:.4f}")

        # Stage 2: 方向预测
        dir_model, dir_metrics = train_direction_predictor(
            X_train, y_train, X_val, y_val, sample_weight=sw
        )

        # 获取原始概率（temperature=1.0，未应用阈值）
        p_draw = draw_model.predict_proba(X_val)[:, 1]
        p_non_draw = 1.0 - p_draw

        if dir_model is not None:
            p_away_nd = dir_model.predict_proba(X_val)[:, 1]
        else:
            p_away_nd = np.full(len(X_val), 0.5)

        p_home_nd = 1.0 - p_away_nd

        p_home = p_non_draw * p_home_nd
        p_draw_final = p_draw
        p_away = p_non_draw * p_away_nd

        # 归一化
        total = p_home + p_draw_final + p_away
        p_home = p_home / total
        p_draw_final = p_draw_final / total
        p_away = p_away / total

        # 存储 OOF
        for i in range(len(val_idx)):
            oof_data.append({
                'fold': fold + 1,
                'y_true': int(y_val.iloc[i]),
                'p_home': float(p_home[i]),
                'p_draw': float(p_draw_final[i]),
                'p_away': float(p_away[i]),
                'category': cat_sorted.iloc[i],
                'date': str(meta_sorted.iloc[i]['date']),
            })

    return pd.DataFrame(oof_data)


# ========================================
# 按类别搜索最优阈值
# ========================================

def find_optimal_threshold_per_category(df_oof, min_samples=30):
    """
    对每个盘口线类别，搜索最优走水阈值（最大化 Macro-F1）。

    参数:
        df_oof: OOF 数据
        min_samples: 类别最小样本数，不足则用默认阈值 0.5

    返回:
        dict: category -> optimal_threshold
    """
    thresholds = {}
    threshold_grid = np.arange(0.25, 0.76, 0.025)

    probs = df_oof[['p_home', 'p_draw', 'p_away']].values
    y_true = df_oof['y_true'].values
    categories = df_oof['category'].values

    print(f"\n  {'类别':>15s}  {'样本数':>6s}  {'走水数':>6s}  {'走水率':>7s}  {'最优阈值':>8s}  {'F1 Macro':>9s}  {'走水召回':>8s}")

    for cat in sorted(df_oof['category'].unique()):
        mask = categories == cat
        n = mask.sum()

        if n < min_samples:
            print(f"  {cat:>15s}  {n:6d}  {'—':>6s}  {'—':>7s}  {'0.500':>8s}  {'—':>9s}  {'—':>8s}  (样本不足)")
            thresholds[cat] = 0.5
            continue

        cat_probs = probs[mask]
        cat_y = y_true[mask]
        draw_n = int((cat_y == 1).sum())
        draw_rate = draw_n / n * 100

        best_f1 = -1
        best_t = 0.5
        best_draw_recall = 0.0

        for t in threshold_grid:
            t_arr = np.full(n, t)
            preds = predict_batch_with_thresholds(cat_probs, t_arr)
            f1 = f1_score(cat_y, preds, average='macro')

            if f1 > best_f1:
                best_f1 = f1
                best_t = round(float(t), 3)
                # 走水召回率
                draw_mask = cat_y == 1
                if draw_mask.sum() > 0:
                    best_draw_recall = (preds[draw_mask] == 1).sum() / draw_mask.sum()
                else:
                    best_draw_recall = 0.0

        print(f"  {cat:>15s}  {n:6d}  {draw_n:6d}  {draw_rate:6.1f}%  {best_t:8.3f}  {best_f1:9.4f}  {best_draw_recall:8.4f}")
        thresholds[cat] = best_t

    return thresholds


# ========================================
# 评估
# ========================================

def evaluate_with_threshold_map(df_oof, threshold_map, label=""):
    """使用给定阈值映射评估 OOF 数据。"""
    probs = df_oof[['p_home', 'p_draw', 'p_away']].values
    y_true = df_oof['y_true'].values
    categories = df_oof['category'].values

    # 每个样本的阈值
    thresholds = np.array([threshold_map.get(c, 0.5) for c in categories])

    preds = predict_batch_with_thresholds(probs, thresholds)

    acc = accuracy_score(y_true, preds)
    f1_macro = f1_score(y_true, preds, average='macro')
    f1_weighted = f1_score(y_true, preds, average='weighted')

    draw_mask = y_true == 1
    draw_recall = (preds[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)
    draw_pred_mask = preds == 1
    draw_precision = (y_true[draw_pred_mask] == 1).sum() / max(draw_pred_mask.sum(), 1)

    probs_clipped = np.clip(probs, 1e-10, 1.0)
    ll = log_loss(y_true, probs_clipped)

    dist = {HCP_RESULT_NAMES[i]: int((preds == i).sum()) for i in range(3)}

    print(f"\n  [{label}]")
    print(f"    Accuracy:      {acc:.4f}")
    print(f"    F1 Macro:      {f1_macro:.4f}")
    print(f"    F1 Weighted:   {f1_weighted:.4f}")
    print(f"    走水召回率:    {draw_recall:.4f}")
    print(f"    走水精确率:    {draw_precision:.4f}")
    print(f"    LogLoss:       {ll:.4f}")
    print(f"    预测分布:      {dist}")

    # 混淆矩阵
    cm = confusion_matrix(y_true, preds, labels=[0, 1, 2])
    labels_str = ['上盘赢', '走水', '下盘赢']
    print(f"    混淆矩阵:")
    print(f"             {'  '.join([f'{l:>5s}' for l in labels_str])}")
    for i, l in enumerate(labels_str):
        print(f"      {l:>5s}  {'  '.join([f'{v:5d}' for v in cm[i]])}")

    return {
        'accuracy': float(acc),
        'f1_macro': float(f1_macro),
        'f1_weighted': float(f1_weighted),
        'draw_recall': float(draw_recall),
        'draw_precision': float(draw_precision),
        'log_loss': float(ll),
        'distribution': dist,
        'confusion_matrix': cm.tolist(),
    }


# ========================================
# 主函数
# ========================================

def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🎯 T-005 v2 动态走水阈值优化")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Step 1: 构建特征
    print("\n📊 Step 1: 构建 v2 特征 (47维)...")
    features = build_all_features_v2()
    features = merge_expanded_labels(features)

    feature_cols = V2_ALL_FEATURES.copy()
    X = features[feature_cols].copy()
    X = X.apply(pd.to_numeric, errors='coerce')
    if X.isnull().any().any():
        print(f"  填充缺失值: {X.isnull().sum().sum()} 个")
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
    meta['label_source'] = features.get('label_source', 'actual')

    valid_mask = y >= 0
    X = X[valid_mask]
    y = y[valid_mask]
    meta = meta[valid_mask]

    X, elo_cols = add_elo_features_hcp(X, meta)
    print(f"  特征: {X.shape[1]}维, 样本: {len(X)}场")

    # Step 2: 获取盘口线类别
    print("\n📊 Step 2: 获取盘口线类别...")
    handicap_categories = get_handicap_categories(meta)
    print(f"  盘口线类别分布:")
    for cat, count in handicap_categories.value_counts().sort_index().items():
        print(f"    {cat}: {count} 场")

    # Step 3: 收集 OOF 概率
    n_splits = 5 if len(X) > 500 else 3
    print(f"\n🎯 Step 3: {n_splits}折 TimeSeriesSplit CV 收集 OOF 概率...")
    t0 = time.time()
    df_oof = collect_oof_probabilities(X, y, meta, handicap_categories, n_splits=n_splits)
    print(f"  OOF 收集完成: {len(df_oof)} 场, 耗时 {time.time()-t0:.1f}s")

    # Step 4: 分析各类别走水基础概率
    print("\n📊 Step 4: 各盘口线类别的走水基础概率")
    print(f"    {'类别':>15s}  {'样本数':>6s}  {'走水数':>6s}  {'走水率':>7s}")
    for cat in sorted(df_oof['category'].unique()):
        subset = df_oof[df_oof['category'] == cat]
        n = len(subset)
        draw_n = int((subset['y_true'] == 1).sum())
        draw_rate = draw_n / n * 100 if n > 0 else 0
        print(f"    {cat:>15s}  {n:6d}  {draw_n:6d}  {draw_rate:6.1f}%")

    # Step 5: OOF 按时间分两半（前半调参 + 后半评估）
    print("\n📊 Step 5: OOF 数据按时间分两半（前半调参 + 后半评估）...")
    df_oof_sorted = df_oof.sort_values('date').reset_index(drop=True)
    split_point = len(df_oof_sorted) // 2
    df_tune = df_oof_sorted.iloc[:split_point]
    df_eval = df_oof_sorted.iloc[split_point:]
    print(f"  调参集: {len(df_tune)} 场, 评估集: {len(df_eval)} 场")

    # Step 6: 在调参集上搜索各类别最优阈值
    print("\n🔍 Step 6: 在调参集上搜索各类别最优走水阈值 (最大化 Macro-F1)...")
    category_thresholds = find_optimal_threshold_per_category(df_tune)

    print(f"\n  📋 类别-阈值映射:")
    for cat, t in sorted(category_thresholds.items()):
        print(f"    {cat:>15s}: {t:.3f}")

    # Step 7: 在评估集上对比固定阈值 vs 动态阈值
    print("\n" + "=" * 70)
    print("📊 Step 7: 评估集上对比固定阈值(0.5) vs 动态阈值")
    print("=" * 70)

    fixed_map = {cat: 0.5 for cat in df_eval['category'].unique()}
    metrics_fixed = evaluate_with_threshold_map(df_eval, fixed_map, label="固定阈值(0.5)")
    metrics_dynamic = evaluate_with_threshold_map(df_eval, category_thresholds, label="动态阈值")

    # 全量 OOF 对比（含调参集, 略乐观）
    print("\n" + "=" * 70)
    print("📊 全量 OOF 对比（含调参集, 略乐观上界）")
    print("=" * 70)
    fixed_map_full = {cat: 0.5 for cat in df_oof['category'].unique()}
    metrics_fixed_full = evaluate_with_threshold_map(df_oof, fixed_map_full, label="固定阈值(0.5) - 全量")
    metrics_dynamic_full = evaluate_with_threshold_map(df_oof, category_thresholds, label="动态阈值 - 全量")

    # Step 8: 改进汇总
    print("\n" + "=" * 70)
    print("📊 改进汇总")
    print("=" * 70)

    print(f"\n  评估集（后半 OOF, 诚实评估）:")
    print(f"    {'指标':>12s}  {'固定(0.5)':>10s}  {'动态':>10s}  {'改进':>10s}")
    print(f"    {'Accuracy':>12s}  {metrics_fixed['accuracy']:10.4f}  {metrics_dynamic['accuracy']:10.4f}  {(metrics_dynamic['accuracy']-metrics_fixed['accuracy'])*100:+9.1f}pp")
    print(f"    {'F1 Macro':>12s}  {metrics_fixed['f1_macro']:10.4f}  {metrics_dynamic['f1_macro']:10.4f}  {(metrics_dynamic['f1_macro']-metrics_fixed['f1_macro'])*100:+9.1f}pp")
    print(f"    {'走水召回率':>12s}  {metrics_fixed['draw_recall']:10.4f}  {metrics_dynamic['draw_recall']:10.4f}  {(metrics_dynamic['draw_recall']-metrics_fixed['draw_recall'])*100:+9.1f}pp")
    print(f"    {'走水精确率':>12s}  {metrics_fixed['draw_precision']:10.4f}  {metrics_dynamic['draw_precision']:10.4f}  {(metrics_dynamic['draw_precision']-metrics_fixed['draw_precision'])*100:+9.1f}pp")
    print(f"    {'LogLoss':>12s}  {metrics_fixed['log_loss']:10.4f}  {metrics_dynamic['log_loss']:10.4f}  {(metrics_dynamic['log_loss']-metrics_fixed['log_loss'])*100:+9.1f}%")

    print(f"\n  全量 OOF（乐观上界）:")
    print(f"    {'Accuracy':>12s}  {metrics_fixed_full['accuracy']:10.4f}  {metrics_dynamic_full['accuracy']:10.4f}  {(metrics_dynamic_full['accuracy']-metrics_fixed_full['accuracy'])*100:+9.1f}pp")
    print(f"    {'F1 Macro':>12s}  {metrics_fixed_full['f1_macro']:10.4f}  {metrics_dynamic_full['f1_macro']:10.4f}  {(metrics_dynamic_full['f1_macro']-metrics_fixed_full['f1_macro'])*100:+9.1f}pp")
    print(f"    {'走水召回率':>12s}  {metrics_fixed_full['draw_recall']:10.4f}  {metrics_dynamic_full['draw_recall']:10.4f}  {(metrics_dynamic_full['draw_recall']-metrics_fixed_full['draw_recall'])*100:+9.1f}pp")

    # Step 9: 保存阈值映射
    draw_base_rates = {}
    for cat in sorted(df_oof['category'].unique()):
        subset = df_oof[df_oof['category'] == cat]
        draw_base_rates[cat] = float((subset['y_true'] == 1).mean()) if len(subset) > 0 else 0.0

    threshold_config = {
        'version': timestamp,
        'method': 'dynamic_draw_threshold_by_handicap_category',
        'description': '按盘口线类别自动调整走水检测阈值',
        'tuning_set_size': len(df_tune),
        'evaluation_set_size': len(df_eval),
        'optimization_metric': 'f1_macro',
        'category_thresholds': category_thresholds,
        'default_threshold': 0.5,
        'draw_base_rate_by_category': draw_base_rates,
        'evaluation_metrics': {
            'fixed_threshold_0.5': metrics_fixed,
            'dynamic_threshold': metrics_dynamic,
        },
        'full_oof_metrics': {
            'fixed_threshold_0.5': metrics_fixed_full,
            'dynamic_threshold': metrics_dynamic_full,
        },
    }

    config_path = os.path.join(ASSETS_DIR, 't005v2_dynamic_thresholds.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(threshold_config, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 阈值映射已保存: {config_path}")

    # 保存 OOF 数据
    oof_path = os.path.join(REPORT_DIR, f't005v2_oof_dynamic_threshold_{timestamp}.csv')
    df_oof.to_csv(oof_path, index=False, encoding='utf-8-sig')
    print(f"💾 OOF 数据已保存: {oof_path}")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ 动态阈值优化完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
