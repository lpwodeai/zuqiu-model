"""
T-005 v2 温度缩放参数调优
========================

在验证集上搜索最优 temperature 值，优化概率校准和预测性能。

温度缩放原理:
    T > 1: 使预测分布更均匀（降低过拟合置信度）
    T < 1: 使预测分布更尖锐（增强置信度）
    T = 1: 无缩放

公式:
    p_scaled = softmax(log(p) / T)

方法（防泄露设计）:
    1. 5折 TimeSeriesSplit CV，收集 OOF 预测概率
       — 每个样本由未见过该样本的模型预测，无数据泄露
    2. OOF 数据按时间分两半：前半调参(tune) + 后半评估(eval)
       — 温度在 tune 上搜索，在 eval 上验证，防止过拟合
    3. 在调参集上搜索最优 T（分别优化 LogLoss / Accuracy / F1 Macro）
    4. 在评估集上验证最优 T 的效果
    5. 前向验证：用 fold k 的最优 T 预测 fold k+1（最诚实评估）
    6. 保存最优温度到 assets/ 供部署使用

运行: python scripts/temperature_tuning.py
"""

import sys
import os
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, f1_score, log_loss, confusion_matrix
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES, V2_FEATURE_GROUPS
from hcp_features import HCP_RESULT_NAMES
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
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

# 走水判定阈值（与部署模型一致，温度调优时保持固定）
DRAW_THRESHOLD = 0.5


# ========================================
# 温度缩放
# ========================================

def apply_temperature(probs, temperature):
    """
    对概率矩阵应用温度缩放。

    参数:
        probs: (n, 3) 概率矩阵 [p_home, p_draw, p_away]
        temperature: 标量 T

    返回:
        (n, 3) 缩放后的概率
    """
    if temperature == 1.0:
        return probs.copy()

    probs_clipped = np.clip(probs, 1e-10, 1.0)
    logits = np.log(probs_clipped) / temperature
    # softmax（减去最大值保证数值稳定性）
    logits = logits - logits.max(axis=1, keepdims=True)
    exp_logits = np.exp(logits)
    scaled = exp_logits / exp_logits.sum(axis=1, keepdims=True)
    return scaled


# ========================================
# 预测逻辑（与 two_stage_predict 的阈值应用一致）
# ========================================

def predict_with_draw_threshold(probs, threshold=DRAW_THRESHOLD):
    """
    应用走水阈值进行预测（与 two_stage_predict 逻辑一致）。

    参数:
        probs: (n, 3) 概率矩阵 [p_home, p_draw, p_away]
        threshold: 走水判定阈值

    返回:
        (n,) 预测标签 (0=上盘赢, 1=走水, 2=下盘赢)
    """
    p_home = probs[:, 0]
    p_draw = probs[:, 1]
    p_away = probs[:, 2]

    predictions = np.where(
        p_draw > threshold, 1,
        np.where(p_home > p_away, 0, 2)
    )
    return predictions.astype(int)


# ========================================
# CV 收集 OOF 概率
# ========================================

def collect_oof_probabilities(X, y, meta, n_splits=5):
    """
    5折 TimeSeriesSplit CV，收集 OOF 预测概率（temperature=1.0 原始概率）。

    每个样本由未见过该样本的模型预测（无数据泄露）。
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)

    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)
    meta_sorted = meta.iloc[sort_idx].reset_index(drop=True)

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

        # 获取原始概率（temperature=1.0）
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
                'date': str(meta_sorted.iloc[i]['date']),
            })

    return pd.DataFrame(oof_data)


# ========================================
# 温度搜索
# ========================================

def search_optimal_temperature(df_oof, metric='log_loss'):
    """
    在给定数据上搜索最优温度。

    参数:
        df_oof: OOF 数据
        metric: 优化指标 ('log_loss', 'accuracy', 'f1_macro')

    返回:
        (optimal_temperature, metric_value, search_results_list)
    """
    probs = df_oof[['p_home', 'p_draw', 'p_away']].values
    y_true = df_oof['y_true'].values

    temperature_grid = np.arange(0.5, 2.55, 0.05)
    results = []

    for t in temperature_grid:
        scaled = apply_temperature(probs, t)
        preds = predict_with_draw_threshold(scaled, DRAW_THRESHOLD)

        if metric == 'log_loss':
            scaled_clipped = np.clip(scaled, 1e-10, 1.0)
            value = log_loss(y_true, scaled_clipped)
        elif metric == 'accuracy':
            value = accuracy_score(y_true, preds)
        elif metric == 'f1_macro':
            value = f1_score(y_true, preds, average='macro')
        else:
            raise ValueError(f"Unknown metric: {metric}")

        results.append({
            'temperature': round(float(t), 3),
            'metric_value': float(value),
        })

    # 找最优
    if metric == 'log_loss':
        best = min(results, key=lambda x: x['metric_value'])
    else:
        best = max(results, key=lambda x: x['metric_value'])

    return best['temperature'], best['metric_value'], results


# ========================================
# 评估
# ========================================

def evaluate_at_temperature(df_oof, temperature, label=""):
    """在给定温度下评估预测性能。"""
    probs = df_oof[['p_home', 'p_draw', 'p_away']].values
    y_true = df_oof['y_true'].values

    scaled = apply_temperature(probs, temperature)
    preds = predict_with_draw_threshold(scaled, DRAW_THRESHOLD)

    acc = accuracy_score(y_true, preds)
    f1_macro = f1_score(y_true, preds, average='macro')
    f1_weighted = f1_score(y_true, preds, average='weighted')
    ll = log_loss(y_true, np.clip(scaled, 1e-10, 1.0))

    draw_mask = y_true == 1
    draw_recall = (preds[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)

    dist = {HCP_RESULT_NAMES[i]: int((preds == i).sum()) for i in range(3)}

    # 置信度统计
    max_probs = scaled.max(axis=1)

    print(f"\n  [{label}] T={temperature:.3f}")
    print(f"    Accuracy:      {acc:.4f}")
    print(f"    F1 Macro:      {f1_macro:.4f}")
    print(f"    F1 Weighted:   {f1_weighted:.4f}")
    print(f"    LogLoss:       {ll:.4f}")
    print(f"    走水召回率:    {draw_recall:.4f}")
    print(f"    预测分布:      {dist}")
    print(f"    平均置信度:    {max_probs.mean():.4f} (±{max_probs.std():.4f})")

    # 混淆矩阵
    cm = confusion_matrix(y_true, preds, labels=[0, 1, 2])
    labels_str = ['上盘赢', '走水', '下盘赢']
    print(f"    混淆矩阵:")
    print(f"             {'  '.join([f'{l:>5s}' for l in labels_str])}")
    for i, l in enumerate(labels_str):
        print(f"      {l:>5s}  {'  '.join([f'{v:5d}' for v in cm[i]])}")

    return {
        'temperature': float(temperature),
        'accuracy': float(acc),
        'f1_macro': float(f1_macro),
        'f1_weighted': float(f1_weighted),
        'log_loss': float(ll),
        'draw_recall': float(draw_recall),
        'distribution': dist,
        'mean_confidence': float(max_probs.mean()),
        'std_confidence': float(max_probs.std()),
        'confusion_matrix': cm.tolist(),
    }


# ========================================
# 前向验证（最诚实评估）
# ========================================

def forward_validation(df_oof):
    """
    前向验证：用 fold k 的最优 T 预测 fold k+1。

    这是最诚实的评估方式：温度在历史 fold 上确定，
    应用到未来 fold 上，模拟真实部署场景。
    """
    print("\n📊 前向验证: 用 fold k 的最优 T 预测 fold k+1")

    folds = sorted(df_oof['fold'].unique())
    forward_results = []

    for i in range(len(folds) - 1):
        tune_fold = folds[i]
        eval_fold = folds[i + 1]

        df_tune = df_oof[df_oof['fold'] == tune_fold]
        df_eval = df_oof[df_oof['fold'] == eval_fold]

        if len(df_tune) < 30 or len(df_eval) < 30:
            continue

        # 在 fold k 上找最优 T（优化 LogLoss）
        optimal_t, _, _ = search_optimal_temperature(df_tune, metric='log_loss')

        # 在 fold k+1 上评估
        print(f"\n  ── fold {tune_fold} → fold {eval_fold} ──")
        print(f"  调参集(fold {tune_fold}): {len(df_tune)} 场 → 最优 T={optimal_t:.3f}")
        metrics = evaluate_at_temperature(
            df_eval, optimal_t,
            label=f"前向 fold{tune_fold}→fold{eval_fold}"
        )

        # 同时评估 T=1.0 基线
        metrics_base = evaluate_at_temperature(
            df_eval, 1.0,
            label=f"基线 fold{eval_fold} T=1.0"
        )

        forward_results.append({
            'tune_fold': tune_fold,
            'eval_fold': eval_fold,
            'optimal_t_on_tune': float(optimal_t),
            'optimal_t_metrics': metrics,
            'baseline_t1_metrics': metrics_base,
        })

    if forward_results:
        avg_t = np.mean([r['optimal_t_on_tune'] for r in forward_results])
        avg_acc_opt = np.mean([r['optimal_t_metrics']['accuracy'] for r in forward_results])
        avg_acc_base = np.mean([r['baseline_t1_metrics']['accuracy'] for r in forward_results])
        avg_ll_opt = np.mean([r['optimal_t_metrics']['log_loss'] for r in forward_results])
        avg_ll_base = np.mean([r['baseline_t1_metrics']['log_loss'] for r in forward_results])

        print(f"\n  前向验证汇总:")
        print(f"    平均最优 T:     {avg_t:.3f}")
        print(f"    Accuracy:  T=1.0 {avg_acc_base:.4f} → 最优T {avg_acc_opt:.4f} ({(avg_acc_opt-avg_acc_base)*100:+.1f}pp)")
        print(f"    LogLoss:   T=1.0 {avg_ll_base:.4f} → 最优T {avg_ll_opt:.4f} ({(avg_ll_opt-avg_ll_base)*100:+.1f}%)")

    return forward_results


# ========================================
# 主函数
# ========================================

def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🌡️ T-005 v2 温度缩放参数调优")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  走水判定阈值: {DRAW_THRESHOLD} (固定, 仅调温度)")

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

    # Step 2: 收集 OOF 概率
    n_splits = 5 if len(X) > 500 else 3
    print(f"\n🎯 Step 2: {n_splits}折 TimeSeriesSplit CV 收集 OOF 概率...")
    t0 = time.time()
    df_oof = collect_oof_probabilities(X, y, meta, n_splits=n_splits)
    print(f"  OOF 收集完成: {len(df_oof)} 场, 耗时 {time.time()-t0:.1f}s")

    # Step 3: OOF 按时间分两半（前半调参 + 后半评估）
    print("\n📊 Step 3: OOF 数据按时间分两半（前半调参 + 后半评估）...")
    df_oof_sorted = df_oof.sort_values('date').reset_index(drop=True)
    split_point = len(df_oof_sorted) // 2
    df_tune = df_oof_sorted.iloc[:split_point]
    df_eval = df_oof_sorted.iloc[split_point:]
    print(f"  调参集: {len(df_tune)} 场, 评估集: {len(df_eval)} 场")

    # Step 4: 在调参集上搜索最优温度
    print("\n" + "=" * 70)
    print("🔍 Step 4: 在调参集上搜索最优温度")
    print("=" * 70)

    optimal_configs = {}
    for metric in ['log_loss', 'accuracy', 'f1_macro']:
        print(f"\n  ── 优化指标: {metric} ──")
        optimal_t, optimal_value, search_results = search_optimal_temperature(df_tune, metric=metric)

        # 打印 Top 5 温度
        if metric == 'log_loss':
            top5 = sorted(search_results, key=lambda x: x['metric_value'])[:5]
        else:
            top5 = sorted(search_results, key=lambda x: -x['metric_value'])[:5]

        print(f"  Top 5 温度:")
        for r in top5:
            print(f"    T={r['temperature']:.3f}, {metric}={r['metric_value']:.4f}")

        print(f"  ✅ 最优 T={optimal_t:.3f} ({metric}={optimal_value:.4f})")

        optimal_configs[metric] = {
            'temperature': float(optimal_t),
            'metric_value': float(optimal_value),
        }

    # Step 5: 在评估集上验证
    print("\n" + "=" * 70)
    print("📊 Step 5: 在评估集上验证最优温度")
    print("=" * 70)

    # 基线: T=1.0
    print("\n  ── 基线 T=1.0 ──")
    metrics_base = evaluate_at_temperature(df_eval, 1.0, label="基线 T=1.0")

    # 各指标最优 T
    eval_metrics = {}
    for metric, config in optimal_configs.items():
        t = config['temperature']
        print(f"\n  ── 最优 T (优化 {metric}) ──")
        eval_metrics[metric] = evaluate_at_temperature(df_eval, t, label=f"最优T({metric})")

    # Step 6: 前向验证
    print("\n" + "=" * 70)
    print("📊 Step 6: 前向验证（最诚实评估）")
    print("=" * 70)
    forward_results = forward_validation(df_oof)

    # Step 7: 改进汇总
    print("\n" + "=" * 70)
    print("📊 改进汇总")
    print("=" * 70)

    best_t = optimal_configs['log_loss']['temperature']
    opt = eval_metrics['log_loss']

    print(f"\n  评估集（后半 OOF, 诚实评估, T 优化 LogLoss）:")
    print(f"    {'指标':>12s}  {'T=1.0':>10s}  {'最优T':>10s}  {'改进':>10s}")
    print(f"    {'Accuracy':>12s}  {metrics_base['accuracy']:10.4f}  {opt['accuracy']:10.4f}  {(opt['accuracy']-metrics_base['accuracy'])*100:+9.1f}pp")
    print(f"    {'F1 Macro':>12s}  {metrics_base['f1_macro']:10.4f}  {opt['f1_macro']:10.4f}  {(opt['f1_macro']-metrics_base['f1_macro'])*100:+9.1f}pp")
    print(f"    {'LogLoss':>12s}  {metrics_base['log_loss']:10.4f}  {opt['log_loss']:10.4f}  {(opt['log_loss']-metrics_base['log_loss'])*100:+9.1f}%")
    print(f"    {'走水召回率':>12s}  {metrics_base['draw_recall']:10.4f}  {opt['draw_recall']:10.4f}  {(opt['draw_recall']-metrics_base['draw_recall'])*100:+9.1f}pp")
    print(f"    {'平均置信度':>12s}  {metrics_base['mean_confidence']:10.4f}  {opt['mean_confidence']:10.4f}  {(opt['mean_confidence']-metrics_base['mean_confidence'])*100:+9.1f}%")

    print(f"\n  各指标最优温度:")
    for metric, config in optimal_configs.items():
        print(f"    {metric:>12s}: T={config['temperature']:.3f}")

    print(f"\n  💡 推荐温度: T={best_t:.3f} (基于 LogLoss 优化)")
    print(f"     该温度下 LogLoss 从 {metrics_base['log_loss']:.4f} {'降至' if opt['log_loss'] < metrics_base['log_loss'] else '升至'} {opt['log_loss']:.4f}")

    # Step 8: 保存结果
    config = {
        'version': timestamp,
        'method': 'temperature_scaling_tuning',
        'description': '在验证集上搜索最优温度缩放参数',
        'draw_threshold_fixed': DRAW_THRESHOLD,
        'tuning_set_size': len(df_tune),
        'evaluation_set_size': len(df_eval),
        'optimal_temperature': {
            'log_loss': optimal_configs['log_loss']['temperature'],
            'accuracy': optimal_configs['accuracy']['temperature'],
            'f1_macro': optimal_configs['f1_macro']['temperature'],
        },
        'recommended_temperature': float(best_t),
        'evaluation_metrics': {
            'baseline_t1': metrics_base,
            'optimal_log_loss': eval_metrics['log_loss'],
            'optimal_accuracy': eval_metrics['accuracy'],
            'optimal_f1': eval_metrics['f1_macro'],
        },
        'forward_validation': forward_results,
    }

    config_path = os.path.join(ASSETS_DIR, 't005v2_optimal_temperature.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 最优温度配置已保存: {config_path}")

    # 保存 OOF 数据
    oof_path = os.path.join(REPORT_DIR, f't005v2_oof_temperature_{timestamp}.csv')
    df_oof.to_csv(oof_path, index=False, encoding='utf-8-sig')
    print(f"💾 OOF 数据已保存: {oof_path}")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ 温度缩放调优完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
