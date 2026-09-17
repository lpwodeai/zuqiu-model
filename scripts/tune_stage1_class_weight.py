"""
T-005 v2 Stage1 class_weight 调优
=================================

从源头解决走水过预测问题：调整 stage1 走水检测器的 class_weight 参数。

问题诊断:
    当前 stage1 使用 class_weight='balanced'，自动给走水类(~22%)约 2.3x 权重，
    导致模型严重过预测走水（预测走水率 67% vs 实际 22%）。

搜索方案:
    以 w_draw / w_non_draw 比值 parametrize class_weight:
        ratio = 2.3  → 'balanced' (当前, 过预测)
        ratio = 1.5  → 温和走水加权
        ratio = 1.0  → 等权重
        ratio = 0.8  → 轻微抑制走水
        ratio = 0.6  → 中度抑制走水
        ratio = 0.5  → 强力抑制走水

评估流程:
    Phase 1: 对每个 ratio 运行 5折 CV，收集 OOF 概率，在 T=1.0 + 固定阈值 0.5 下评估
             → 找到 draw_prediction_rate 最接近 actual_draw_rate 的 ratio
    Phase 2: 用最优 ratio 重新运行 CV + 组合优化(T=2.150 + 动态阈值)
             → 对比 original(balanced) vs optimal class_weight 的完整效果

运行: python scripts/tune_stage1_class_weight.py
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
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
from train_hcp_model_v2 import compute_sample_weights, train_direction_predictor
from dynamic_draw_threshold import (
    get_handicap_categories,
    predict_batch_with_thresholds, find_optimal_threshold_per_category,
)
from temperature_tuning import apply_temperature

try:
    import lightgbm as lgb
except ImportError:
    print("ERROR: lightgbm not available")
    sys.exit(1)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

TEMPERATURE = 2.150
DRAW_THRESHOLD = 0.5


# ========================================
# 可配置 class_weight 的 Stage1 训练
# ========================================

def train_draw_detector_custom(X_train, y_train, X_val, y_val,
                                sample_weight=None, class_weight='balanced'):
    """
    Stage 1: 二分类走水检测器（可配置 class_weight）。

    参数:
        class_weight: 'balanced' / None / {0: w0, 1: w1}
                      ratio = w1/w0 控制走水预测倾向
                      ratio > 1 → 增加走水预测（过预测）
                      ratio < 1 → 抑制走水预测
    """
    y_draw_train = (y_train == 1).astype(int)
    y_draw_val = (y_val == 1).astype(int)

    model = lgb.LGBMClassifier(
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
        class_weight=class_weight,
        random_state=42,
        verbose=-1,
        force_col_wise=True,
    )

    model.fit(
        X_train, y_draw_train,
        sample_weight=sample_weight,
        eval_set=[(X_val, y_draw_val)],
        eval_metric='binary_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )

    y_pred = model.predict(X_val)
    y_proba = model.predict_proba(X_val)[:, 1]

    acc = accuracy_score(y_draw_val, y_pred)
    recall = (y_pred[y_draw_val == 1] == 1).sum() / max((y_draw_val == 1).sum(), 1)

    return model, {'accuracy': acc, 'draw_recall': recall, 'proba_draw': y_proba}


# ========================================
# CV 收集 OOF（可配置 class_weight）
# ========================================

def collect_oof_with_class_weight(X, y, meta, handicap_categories,
                                    class_weight='balanced', n_splits=5):
    """
    5折 CV 收集 OOF 概率，stage1 使用指定 class_weight。
    stage2 不受 class_weight 影响（仅训练非走水样本）。
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

        sw = compute_sample_weights(y_train.values, ls_train)

        # Stage 1: 走水检测（使用自定义 class_weight）
        draw_model, draw_metrics = train_draw_detector_custom(
            X_train, y_train, X_val, y_val,
            sample_weight=sw, class_weight=class_weight
        )

        # Stage 2: 方向预测（不受 class_weight 影响）
        dir_model, dir_metrics = train_direction_predictor(
            X_train, y_train, X_val, y_val, sample_weight=sw
        )

        # 获取概率
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

        total = p_home + p_draw_final + p_away
        p_home = p_home / total
        p_draw_final = p_draw_final / total
        p_away = p_away / total

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
# 评估
# ========================================

def evaluate_oof(df_oof, temperature=1.0, threshold_map=None, label=""):
    """评估 OOF 数据在给定温度 + 阈值下的表现。"""
    probs = df_oof[['p_home', 'p_draw', 'p_away']].values
    y_true = df_oof['y_true'].values
    categories = df_oof['category'].values

    scaled = apply_temperature(probs, temperature)

    if threshold_map is None:
        threshold_map = {c: DRAW_THRESHOLD for c in df_oof['category'].unique()}

    thresholds = np.array([threshold_map.get(c, DRAW_THRESHOLD) for c in categories])
    preds = predict_batch_with_thresholds(scaled, thresholds)

    acc = accuracy_score(y_true, preds)
    f1_macro = f1_score(y_true, preds, average='macro')
    ll = log_loss(y_true, np.clip(scaled, 1e-10, 1.0))

    draw_mask = y_true == 1
    draw_recall = (preds[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)
    draw_pred_mask = preds == 1
    draw_precision = (y_true[draw_pred_mask] == 1).sum() / max(draw_pred_mask.sum(), 1)
    draw_pred_rate = draw_pred_mask.sum() / len(preds)
    actual_draw_rate = draw_mask.sum() / len(y_true)

    dist = {HCP_RESULT_NAMES[i]: int((preds == i).sum()) for i in range(3)}

    return {
        'label': label,
        'accuracy': float(acc),
        'f1_macro': float(f1_macro),
        'log_loss': float(ll),
        'draw_recall': float(draw_recall),
        'draw_precision': float(draw_precision),
        'draw_prediction_rate': float(draw_pred_rate),
        'actual_draw_rate': float(actual_draw_rate),
        'distribution': dist,
    }


# ========================================
# Phase 1: 搜索 class_weight
# ========================================

def search_class_weight(X, y, meta, handicap_categories, n_splits=5):
    """
    搜索不同的 class_weight ratio，评估对走水预测的影响。

    返回: (results_df, best_ratio, best_metrics)
    """
    # 定义候选 ratio
    # ratio = w_draw / w_non_draw
    # 'balanced' ≈ 2.3 (自动计算)
    candidates = [
        ('balanced(2.3)', 'balanced'),
        ('ratio=1.5', {0: 1.0, 1: 1.5}),
        ('ratio=1.0', {0: 1.0, 1: 1.0}),
        ('ratio=0.8', {0: 1.0, 1: 0.8}),
        ('ratio=0.6', {0: 1.0, 1: 0.6}),
        ('ratio=0.5', {0: 1.0, 1: 0.5}),
        ('ratio=0.4', {0: 1.0, 1: 0.4}),
    ]

    results = []

    for name, cw in candidates:
        print(f"\n  ── {name} ──")
        t0 = time.time()
        df_oof = collect_oof_with_class_weight(
            X, y, meta, handicap_categories,
            class_weight=cw, n_splits=n_splits
        )
        metrics = evaluate_oof(df_oof, temperature=1.0, label=name)
        elapsed = time.time() - t0

        print(f"    Accuracy={metrics['accuracy']:.4f}, F1={metrics['f1_macro']:.4f}, "
              f"走水预测率={metrics['draw_prediction_rate']:.4f} (实际={metrics['actual_draw_rate']:.4f}), "
              f"走水召回={metrics['draw_recall']:.4f}, 精确={metrics['draw_precision']:.4f}, "
              f"耗时={elapsed:.1f}s")

        results.append({
            'name': name,
            'class_weight': str(cw),
            **metrics,
            'n_samples': len(df_oof),
        })

    results_df = pd.DataFrame(results)

    # 找最优: 最大化 F1 macro
    best_idx = results_df['f1_macro'].idxmax()
    best = results_df.iloc[best_idx]

    # 找走水预测率最接近实际的
    results_df['draw_rate_gap'] = abs(results_df['draw_prediction_rate'] - results_df['actual_draw_rate'])
    best_gap_idx = results_df['draw_rate_gap'].idxmin()
    best_gap = results_df.iloc[best_gap_idx]

    print(f"\n  📊 搜索结果汇总:")
    print(f"    {'配置':>15s}  {'Accuracy':>9s}  {'F1 Macro':>9s}  {'走水预测率':>9s}  {'走水召回':>9s}  {'走水精确':>9s}  {'预测率偏差':>9s}")
    for _, r in results_df.iterrows():
        print(f"    {r['name']:>15s}  {r['accuracy']:9.4f}  {r['f1_macro']:9.4f}  "
              f"{r['draw_prediction_rate']:9.4f}  {r['draw_recall']:9.4f}  {r['draw_precision']:9.4f}  "
              f"{r['draw_rate_gap']:9.4f}")

    print(f"\n  ✅ F1 最优: {best['name']} (F1={best['f1_macro']:.4f})")
    print(f"  ✅ 走水预测率最接近实际: {best_gap['name']} (偏差={best_gap['draw_rate_gap']:.4f})")

    return results_df, candidates, best, best_gap


# ========================================
# Phase 2: 用最优 class_weight 重新评估组合优化
# ========================================

def phase2_combined_eval(X, y, meta, handicap_categories,
                          best_cw, best_cw_name, n_splits=5):
    """
    用最优 class_weight 重新运行 CV + 组合优化(T=2.150 + 动态阈值)。
    与 original(balanced) 对比。
    """
    print("\n" + "=" * 70)
    print("📊 Phase 2: 用最优 class_weight 重新评估组合优化")
    print("=" * 70)

    # 1. 用最优 class_weight 收集 OOF
    print(f"\n  使用 {best_cw_name} 重新收集 OOF...")
    df_oof_opt = collect_oof_with_class_weight(
        X, y, meta, handicap_categories,
        class_weight=best_cw, n_splits=n_splits
    )

    # 2. 用 original(balanced) 收集 OOF（对比基线）
    print(f"  使用 balanced(2.3) 重新收集 OOF (对比)...")
    df_oof_orig = collect_oof_with_class_weight(
        X, y, meta, handicap_categories,
        class_weight='balanced', n_splits=n_splits
    )

    # 3. 分 tune + eval
    def split_oof(df):
        df_s = df.sort_values('date').reset_index(drop=True)
        sp = len(df_s) // 2
        return df_s.iloc[:sp], df_s.iloc[sp:]

    df_tune_opt, df_eval_opt = split_oof(df_oof_opt)
    df_tune_orig, df_eval_orig = split_oof(df_oof_orig)

    print(f"  调参集: {len(df_tune_opt)} 场, 评估集: {len(df_eval_opt)} 场")

    # 4. 在 tune 上搜索动态阈值 (T=2.150)
    print(f"\n  在 tune 上搜索动态阈值 (T={TEMPERATURE})...")

    # optimal class_weight 的阈值
    probs_tune_opt = df_tune_opt[['p_home', 'p_draw', 'p_away']].values
    scaled_tune_opt = apply_temperature(probs_tune_opt, TEMPERATURE)
    df_tune_opt_t = df_tune_opt.copy()
    df_tune_opt_t['p_home'] = scaled_tune_opt[:, 0]
    df_tune_opt_t['p_draw'] = scaled_tune_opt[:, 1]
    df_tune_opt_t['p_away'] = scaled_tune_opt[:, 2]
    thresholds_opt = find_optimal_threshold_per_category(df_tune_opt_t)
    print(f"    最优 cw 阈值: {thresholds_opt}")

    # original(balanced) 的阈值
    probs_tune_orig = df_tune_orig[['p_home', 'p_draw', 'p_away']].values
    scaled_tune_orig = apply_temperature(probs_tune_orig, TEMPERATURE)
    df_tune_orig_t = df_tune_orig.copy()
    df_tune_orig_t['p_home'] = scaled_tune_orig[:, 0]
    df_tune_orig_t['p_draw'] = scaled_tune_orig[:, 1]
    df_tune_orig_t['p_away'] = scaled_tune_orig[:, 2]
    thresholds_orig = find_optimal_threshold_per_category(df_tune_orig_t)
    print(f"    balanced 阈值: {thresholds_orig}")

    # 5. 评估 4 种配置
    print("\n  ── 评估集对比 ──")

    fixed_map = {c: DRAW_THRESHOLD for c in df_eval_opt['category'].unique()}

    configs = {
        'orig_base': ('balanced 基线', df_eval_orig, 1.0, fixed_map),
        'orig_combo': (f'balanced 组合(T={TEMPERATURE}+动态)', df_eval_orig, TEMPERATURE, thresholds_orig),
        'opt_base': (f'{best_cw_name} 基线', df_eval_opt, 1.0, fixed_map),
        'opt_combo': (f'{best_cw_name} 组合(T={TEMPERATURE}+动态)', df_eval_opt, TEMPERATURE, thresholds_opt),
    }

    eval_results = {}
    for key, (label, df_eval, temp, t_map) in configs.items():
        metrics = evaluate_oof(df_eval, temperature=temp, threshold_map=t_map, label=label)
        eval_results[key] = metrics

        print(f"\n  [{label}]")
        print(f"    Accuracy={metrics['accuracy']:.4f}, F1={metrics['f1_macro']:.4f}, "
              f"走水预测率={metrics['draw_prediction_rate']:.4f}, "
              f"走水召回={metrics['draw_recall']:.4f}, 精确={metrics['draw_precision']:.4f}")

    # 6. 混淆矩阵
    print(f"\n  混淆矩阵对比:")
    for key in ['orig_combo', 'opt_combo']:
        label = configs[key][0]
        df_eval = configs[key][1]
        temp = configs[key][2]
        t_map = configs[key][3]

        probs = df_eval[['p_home', 'p_draw', 'p_away']].values
        scaled = apply_temperature(probs, temp)
        cats = df_eval['category'].values
        thresholds = np.array([t_map.get(c, DRAW_THRESHOLD) for c in cats])
        preds = predict_batch_with_thresholds(scaled, thresholds)
        y_true = df_eval['y_true'].values

        cm = confusion_matrix(y_true, preds, labels=[0, 1, 2])
        print(f"\n    [{label}]")
        labels_str = ['上盘赢', '走水', '下盘赢']
        print(f"             {'  '.join([f'{l:>5s}' for l in labels_str])}")
        for i, l in enumerate(labels_str):
            print(f"      {l:>5s}  {'  '.join([f'{v:5d}' for v in cm[i]])}")

        eval_results[key]['confusion_matrix'] = cm.tolist()

    return eval_results, thresholds_opt


# ========================================
# 主函数
# ========================================

def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🔧 T-005 v2 Stage1 class_weight 调优")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  目标: 从源头减少走水过预测, 提升走水精确率和整体准确率")

    # Step 1: 构建特征
    print("\n📊 Step 1: 构建 v2 特征 (47维)...")
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

    # ===== Phase 1: 搜索 class_weight =====
    print("\n" + "=" * 70)
    print("🔍 Phase 1: 搜索 stage1 class_weight")
    print("=" * 70)

    n_splits = 5 if len(X) > 500 else 3
    results_df, candidates, best_f1, best_gap = search_class_weight(
        X, y, meta, handicap_categories, n_splits=n_splits
    )

    # 选择最优: 综合考虑 F1 和走水预测率偏差
    # 优先选择走水预测率接近实际的, 同时 F1 不太差
    print(f"\n  选择策略: 走水预测率最接近实际且 F1 >= 80% * max_F1")
    max_f1 = results_df['f1_macro'].max()
    f1_threshold = max_f1 * 0.80
    eligible = results_df[results_df['f1_macro'] >= f1_threshold]
    best_idx = eligible['draw_rate_gap'].idxmin()
    best_row = results_df.iloc[best_idx]

    # 获取对应的 class_weight
    best_name = best_row['name']
    best_cw = None
    for name, cw in candidates:
        if name == best_name:
            best_cw = cw
            break

    print(f"\n  ✅ 选定: {best_name}")
    print(f"     F1={best_row['f1_macro']:.4f}, 走水预测率={best_row['draw_prediction_rate']:.4f}, "
          f"走水召回={best_row['draw_recall']:.4f}, 精确={best_row['draw_precision']:.4f}")

    # ===== Phase 2: 组合优化对比 =====
    print("\n" + "=" * 70)
    print("📊 Phase 2: 组合优化对比 (最优 cw vs balanced)")
    print("=" * 70)

    eval_results, optimal_thresholds = phase2_combined_eval(
        X, y, meta, handicap_categories,
        best_cw=best_cw, best_cw_name=best_name, n_splits=n_splits
    )

    # ===== 汇总 =====
    print("\n" + "=" * 70)
    print("📊 最终汇总")
    print("=" * 70)

    print(f"\n  {'配置':>35s}  {'Accuracy':>9s}  {'F1 Macro':>9s}  {'走水预测率':>9s}  {'走水召回':>9s}  {'走水精确':>9s}")
    for key in ['orig_base', 'orig_combo', 'opt_base', 'opt_combo']:
        m = eval_results[key]
        print(f"  {m['label']:>35s}  {m['accuracy']:9.4f}  {m['f1_macro']:9.4f}  "
              f"{m['draw_prediction_rate']:9.4f}  {m['draw_recall']:9.4f}  {m['draw_precision']:9.4f}")

    # 改进
    orig = eval_results['orig_combo']
    opt = eval_results['opt_combo']
    print(f"\n  组合优化对比 (最优cw vs balanced, 均含 T={TEMPERATURE}+动态阈值):")
    print(f"    Accuracy:    {orig['accuracy']:.4f} → {opt['accuracy']:.4f} ({(opt['accuracy']-orig['accuracy'])*100:+.1f}pp)")
    print(f"    F1 Macro:    {orig['f1_macro']:.4f} → {opt['f1_macro']:.4f} ({(opt['f1_macro']-orig['f1_macro'])*100:+.1f}pp)")
    print(f"    走水预测率:  {orig['draw_prediction_rate']:.4f} → {opt['draw_prediction_rate']:.4f} (实际: {opt['actual_draw_rate']:.4f})")
    print(f"    走水召回率:  {orig['draw_recall']:.4f} → {opt['draw_recall']:.4f} ({(opt['draw_recall']-orig['draw_recall'])*100:+.1f}pp)")
    print(f"    走水精确率:  {orig['draw_precision']:.4f} → {opt['draw_precision']:.4f} ({(opt['draw_precision']-orig['draw_precision'])*100:+.1f}pp)")

    # 保存结果
    config = {
        'timestamp': timestamp,
        'method': 'stage1_class_weight_tuning',
        'description': '调整 stage1 走水检测器的 class_weight, 从源头减少过预测',
        'phase1_search_results': results_df.to_dict('records'),
        'selected_class_weight': {
            'name': best_name,
            'value': str(best_cw),
            'f1_macro': float(best_row['f1_macro']),
            'draw_prediction_rate': float(best_row['draw_prediction_rate']),
            'draw_recall': float(best_row['draw_recall']),
            'draw_precision': float(best_row['draw_precision']),
        },
        'phase2_comparison': eval_results,
        'optimal_thresholds_at_T2_150': optimal_thresholds,
        'temperature': TEMPERATURE,
    }

    config_path = os.path.join(ASSETS_DIR, 't005v2_optimal_class_weight.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 配置已保存: {config_path}")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ class_weight 调优完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
