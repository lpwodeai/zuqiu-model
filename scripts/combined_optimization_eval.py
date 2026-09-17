"""
T-005 v2 组合优化评估
====================

将动态走水阈值与温度缩放(T=2.150)组合，评估协同效应。

5 种配置对比:
    A: 基线       — T=1.0,   固定阈值 0.5
    B: 动态阈值   — T=1.0,   类别特定阈值（T=1.0 上搜索）
    C: 温度缩放   — T=2.150, 固定阈值 0.5
    D: 组合(现用) — T=2.150, 类别特定阈值（T=1.0 上搜索, 直接复用）
    E: 组合(重调) — T=2.150, 类别特定阈值（T=2.150 上重新搜索）

防泄露设计:
    - OOF 概率由未见过该样本的模型预测
    - 动态阈值在前半 OOF (tune) 上搜索, 在后半 OOF (eval) 上验证
    - 配置 E 的重调阈值也在 tune 上搜索, eval 上验证

运行: python scripts/combined_optimization_eval.py
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
from train_hcp_model_v2 import compute_sample_weights, train_draw_detector, train_direction_predictor
from dynamic_draw_threshold import (
    categorize_handicap_line, get_handicap_categories,
    predict_batch_with_thresholds, find_optimal_threshold_per_category,
    collect_oof_probabilities as collect_oof_base,
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

# 固定参数
TEMPERATURE = 2.150
DRAW_THRESHOLD_FIXED = 0.5


# ========================================
# 评估函数
# ========================================

def evaluate_config(df_oof, temperature, threshold_map, label):
    """
    在给定温度 + 阈值映射下评估 OOF 数据。

    参数:
        df_oof: OOF DataFrame
        temperature: 温度缩放参数
        threshold_map: {category: threshold} 字典
        label: 配置名称

    返回:
        metrics dict
    """
    probs = df_oof[['p_home', 'p_draw', 'p_away']].values
    y_true = df_oof['y_true'].values
    categories = df_oof['category'].values

    # 1. 温度缩放
    scaled = apply_temperature(probs, temperature)

    # 2. 类别特定阈值
    thresholds = np.array([threshold_map.get(c, DRAW_THRESHOLD_FIXED) for c in categories])

    # 3. 预测
    preds = predict_batch_with_thresholds(scaled, thresholds)

    # 4. 指标
    acc = accuracy_score(y_true, preds)
    f1_macro = f1_score(y_true, preds, average='macro')
    f1_weighted = f1_score(y_true, preds, average='weighted')
    ll = log_loss(y_true, np.clip(scaled, 1e-10, 1.0))

    draw_mask = y_true == 1
    draw_recall = (preds[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)
    draw_pred_mask = preds == 1
    draw_precision = (y_true[draw_pred_mask] == 1).sum() / max(draw_pred_mask.sum(), 1)

    dist = {HCP_RESULT_NAMES[i]: int((preds == i).sum()) for i in range(3)}
    draw_pred_rate = dist.get('走水', 0) / len(preds)

    cm = confusion_matrix(y_true, preds, labels=[0, 1, 2])

    max_probs = scaled.max(axis=1)

    print(f"\n  [{label}]")
    print(f"    Accuracy:      {acc:.4f}")
    print(f"    F1 Macro:      {f1_macro:.4f}")
    print(f"    F1 Weighted:   {f1_weighted:.4f}")
    print(f"    LogLoss:       {ll:.4f}")
    print(f"    走水召回率:    {draw_recall:.4f}")
    print(f"    走水精确率:    {draw_precision:.4f}")
    print(f"    走水预测占比:  {draw_pred_rate:.4f} (实际走水率: {draw_mask.sum()/len(y_true):.4f})")
    print(f"    预测分布:      {dist}")
    print(f"    平均置信度:    {max_probs.mean():.4f}")

    return {
        'label': label,
        'temperature': float(temperature),
        'threshold_strategy': 'fixed' if len(set(threshold_map.values())) == 1 else 'dynamic',
        'accuracy': float(acc),
        'f1_macro': float(f1_macro),
        'f1_weighted': float(f1_weighted),
        'log_loss': float(ll),
        'draw_recall': float(draw_recall),
        'draw_precision': float(draw_precision),
        'draw_prediction_rate': float(draw_pred_rate),
        'actual_draw_rate': float(draw_mask.sum() / len(y_true)),
        'distribution': dist,
        'mean_confidence': float(max_probs.mean()),
        'confusion_matrix': cm.tolist(),
    }


def find_optimal_threshold_at_temperature(df_oof, temperature, min_samples=30):
    """在给定温度下搜索各类别最优阈值。"""
    probs = df_oof[['p_home', 'p_draw', 'p_away']].values
    scaled = apply_temperature(probs, temperature)

    df_temp = df_oof.copy()
    df_temp['p_home'] = scaled[:, 0]
    df_temp['p_draw'] = scaled[:, 1]
    df_temp['p_away'] = scaled[:, 2]

    return find_optimal_threshold_per_category(df_temp, min_samples)


# ========================================
# 报告生成
# ========================================

def generate_markdown_report(eval_metrics, full_metrics, threshold_configs, timestamp):
    """生成 Markdown 评估报告。"""
    lines = []
    lines.append(f"# T-005 v2 组合优化评估报告")
    lines.append(f"")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 评估方法: 5折 TimeSeriesSplit CV → OOF 分两半 (tune + eval)")
    lines.append(f"> 温度参数: T={TEMPERATURE}")
    lines.append(f"")

    # 配置说明
    lines.append(f"## 1. 评估配置")
    lines.append(f"")
    lines.append(f"| 配置 | Temperature | 阈值策略 | 阈值搜索温度 |")
    lines.append(f"|------|------------|----------|-------------|")
    lines.append(f"| A: 基线 | 1.0 | 固定 0.5 | — |")
    lines.append(f"| B: 动态阈值 | 1.0 | 类别特定 | T=1.0 |")
    lines.append(f"| C: 温度缩放 | {TEMPERATURE} | 固定 0.5 | — |")
    lines.append(f"| D: 组合(现用) | {TEMPERATURE} | 类别特定 | T=1.0 |")
    lines.append(f"| E: 组合(重调) | {TEMPERATURE} | 类别特定 | T={TEMPERATURE} |")
    lines.append(f"")

    # 阈值映射
    lines.append(f"## 2. 阈值映射")
    lines.append(f"")
    for name, thresholds in threshold_configs.items():
        lines.append(f"### {name}")
        lines.append(f"")
        lines.append(f"| 类别 | 阈值 |")
        lines.append(f"|------|------|")
        for cat, t in sorted(thresholds.items()):
            lines.append(f"| {cat} | {t:.3f} |")
        lines.append(f"")

    # 评估集对比
    lines.append(f"## 3. 评估集对比（诚实评估, 后半 OOF）")
    lines.append(f"")
    lines.append(f"| 配置 | Accuracy | F1 Macro | 走水召回 | 走水精确 | 走水预测率 | LogLoss |")
    lines.append(f"|------|----------|----------|---------|---------|-----------|---------|")
    for key in ['A', 'B', 'C', 'D', 'E']:
        m = eval_metrics[key]
        lines.append(
            f"| {m['label']} | {m['accuracy']:.4f} | {m['f1_macro']:.4f} | "
            f"{m['draw_recall']:.4f} | {m['draw_precision']:.4f} | "
            f"{m['draw_prediction_rate']:.4f} | {m['log_loss']:.4f} |"
        )
    lines.append(f"")

    # 改进幅度
    lines.append(f"## 4. 相对基线(A)的改进")
    lines.append(f"")
    lines.append(f"| 配置 | ΔAccuracy | ΔF1 Macro | Δ走水召回 | ΔLogLoss |")
    lines.append(f"|------|-----------|-----------|----------|---------|")
    base = eval_metrics['A']
    for key in ['B', 'C', 'D', 'E']:
        m = eval_metrics[key]
        lines.append(
            f"| {m['label']} | {(m['accuracy']-base['accuracy'])*100:+.1f}pp | "
            f"{(m['f1_macro']-base['f1_macro'])*100:+.1f}pp | "
            f"{(m['draw_recall']-base['draw_recall'])*100:+.1f}pp | "
            f"{(m['log_loss']-base['log_loss'])*100:+.1f}% |"
        )
    lines.append(f"")

    # 协同效应分析
    lines.append(f"## 5. 协同效应分析")
    lines.append(f"")
    b_acc = eval_metrics['B']['accuracy'] - base['accuracy']
    c_acc = eval_metrics['C']['accuracy'] - base['accuracy']
    d_acc = eval_metrics['D']['accuracy'] - base['accuracy']
    e_acc = eval_metrics['E']['accuracy'] - base['accuracy']
    synergy_d = d_acc - (b_acc + c_acc)
    synergy_e = e_acc - (b_acc + c_acc)

    lines.append(f"### Accuracy 协同效应")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| B(动态) 单独贡献 | {b_acc*100:+.1f}pp |")
    lines.append(f"| C(温度) 单独贡献 | {c_acc*100:+.1f}pp |")
    lines.append(f"| B+C 预期叠加 | {(b_acc+c_acc)*100:+.1f}pp |")
    lines.append(f"| D(组合现用) 实际 | {d_acc*100:+.1f}pp |")
    lines.append(f"| E(组合重调) 实际 | {e_acc*100:+.1f}pp |")
    lines.append(f"| D 协同效应 | {synergy_d*100:+.1f}pp |")
    lines.append(f"| E 协同效应 | {synergy_e*100:+.1f}pp |")
    lines.append(f"")

    b_f1 = eval_metrics['B']['f1_macro'] - base['f1_macro']
    c_f1 = eval_metrics['C']['f1_macro'] - base['f1_macro']
    d_f1 = eval_metrics['D']['f1_macro'] - base['f1_macro']
    e_f1 = eval_metrics['E']['f1_macro'] - base['f1_macro']
    lines.append(f"### F1 Macro 协同效应")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| B(动态) 单独贡献 | {b_f1*100:+.1f}pp |")
    lines.append(f"| C(温度) 单独贡献 | {c_f1*100:+.1f}pp |")
    lines.append(f"| B+C 预期叠加 | {(b_f1+c_f1)*100:+.1f}pp |")
    lines.append(f"| D(组合现用) 实际 | {d_f1*100:+.1f}pp |")
    lines.append(f"| E(组合重调) 实际 | {e_f1*100:+.1f}pp |")
    lines.append(f"| D 协同效应 | {(d_f1-(b_f1+c_f1))*100:+.1f}pp |")
    lines.append(f"| E 协同效应 | {(e_f1-(b_f1+c_f1))*100:+.1f}pp |")
    lines.append(f"")

    # 混淆矩阵
    lines.append(f"## 6. 混淆矩阵（评估集）")
    lines.append(f"")
    labels_str = ['上盘赢', '走水', '下盘赢']
    for key in ['A', 'D', 'E']:
        m = eval_metrics[key]
        cm = m['confusion_matrix']
        lines.append(f"### {m['label']}")
        lines.append(f"")
        lines.append(f"| | 预测上盘赢 | 预测走水 | 预测下盘赢 |")
        lines.append(f"|---|-----------|---------|-----------|")
        for i, l in enumerate(labels_str):
            lines.append(f"| 实际{l} | {cm[i][0]} | {cm[i][1]} | {cm[i][2]} |")
        lines.append(f"")

    # 全量 OOF 对比
    lines.append(f"## 7. 全量 OOF 对比（含调参集, 略乐观）")
    lines.append(f"")
    lines.append(f"| 配置 | Accuracy | F1 Macro | 走水召回 | LogLoss |")
    lines.append(f"|------|----------|----------|---------|---------|")
    for key in ['A', 'B', 'C', 'D', 'E']:
        m = full_metrics[key]
        lines.append(
            f"| {m['label']} | {m['accuracy']:.4f} | {m['f1_macro']:.4f} | "
            f"{m['draw_recall']:.4f} | {m['log_loss']:.4f} |"
        )
    lines.append(f"")

    # 推荐
    lines.append(f"## 8. 推荐方案")
    lines.append(f"")
    # 找评估集上 F1 最高的配置
    best_key = max(['A', 'B', 'C', 'D', 'E'], key=lambda k: eval_metrics[k]['f1_macro'])
    best = eval_metrics[best_key]
    lines.append(f"- **F1 Macro 最优**: {best['label']} (F1={best['f1_macro']:.4f}, Acc={best['accuracy']:.4f})")
    best_acc_key = max(['A', 'B', 'C', 'D', 'E'], key=lambda k: eval_metrics[k]['accuracy'])
    best_acc = eval_metrics[best_acc_key]
    lines.append(f"- **Accuracy 最优**: {best_acc['label']} (Acc={best_acc['accuracy']:.4f}, F1={best_acc['f1_macro']:.4f})")
    lines.append(f"")

    return '\n'.join(lines)


# ========================================
# 主函数
# ========================================

def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🔧 T-005 v2 组合优化评估 (动态阈值 + T=2.150)")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  温度: T={TEMPERATURE}")

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

    # Step 3: 收集 OOF 概率
    n_splits = 5 if len(X) > 500 else 3
    print(f"\n🎯 Step 3: {n_splits}折 CV 收集 OOF 概率...")
    t0 = time.time()
    df_oof = collect_oof_base(X, y, meta, handicap_categories, n_splits=n_splits)
    print(f"  OOF 收集完成: {len(df_oof)} 场, 耗时 {time.time()-t0:.1f}s")

    # Step 4: OOF 按时间分两半
    print("\n📊 Step 4: OOF 按时间分两半 (tune + eval)...")
    df_oof_sorted = df_oof.sort_values('date').reset_index(drop=True)
    split_point = len(df_oof_sorted) // 2
    df_tune = df_oof_sorted.iloc[:split_point]
    df_eval = df_oof_sorted.iloc[split_point:]
    print(f"  调参集: {len(df_tune)} 场, 评估集: {len(df_eval)} 场")

    # Step 5: 加载/搜索阈值映射
    print("\n🔍 Step 5: 准备阈值映射...")

    # B/D 用: T=1.0 上搜索的阈值（复用已有配置）
    config_path = os.path.join(ASSETS_DIR, 't005v2_dynamic_thresholds.json')
    if os.path.exists(config_path):
        with open(config_path, 'r', encoding='utf-8') as f:
            dyn_config = json.load(f)
        thresholds_t1 = dyn_config['category_thresholds']
        print(f"  已加载 T=1.0 阈值: {thresholds_t1}")
    else:
        print("  未找到已有配置, 在 tune 上重新搜索 T=1.0 阈值...")
        thresholds_t1 = find_optimal_threshold_at_temperature(df_tune, 1.0)
        print(f"  T=1.0 阈值: {thresholds_t1}")

    # E 用: T=2.150 上重新搜索的阈值
    print(f"\n  在 tune 上搜索 T={TEMPERATURE} 阈值...")
    thresholds_t2 = find_optimal_threshold_at_temperature(df_tune, TEMPERATURE)
    print(f"  T={TEMPERATURE} 阈值: {thresholds_t2}")

    # 固定阈值
    fixed_map = {cat: DRAW_THRESHOLD_FIXED for cat in df_eval['category'].unique()}

    threshold_configs = {
        'B/D (T=1.0 搜索)': thresholds_t1,
        f'E (T={TEMPERATURE} 搜索)': thresholds_t2,
    }

    # Step 6: 评估 5 种配置
    print("\n" + "=" * 70)
    print("📊 Step 6: 评估 5 种配置")
    print("=" * 70)

    print("\n── 评估集 (诚实评估) ──")
    eval_metrics = {}
    eval_metrics['A'] = evaluate_config(df_eval, 1.0, fixed_map, "A: 基线(T=1.0, 固定0.5)")
    eval_metrics['B'] = evaluate_config(df_eval, 1.0, thresholds_t1, "B: 动态阈值(T=1.0)")
    eval_metrics['C'] = evaluate_config(df_eval, TEMPERATURE, fixed_map, f"C: 温度缩放(T={TEMPERATURE})")
    eval_metrics['D'] = evaluate_config(df_eval, TEMPERATURE, thresholds_t1, f"D: 组合现用(T={TEMPERATURE}, T1阈值)")
    eval_metrics['E'] = evaluate_config(df_eval, TEMPERATURE, thresholds_t2, f"E: 组合重调(T={TEMPERATURE}, T2阈值)")

    print("\n── 全量 OOF (乐观上界) ──")
    fixed_map_full = {cat: DRAW_THRESHOLD_FIXED for cat in df_oof['category'].unique()}
    full_metrics = {}
    full_metrics['A'] = evaluate_config(df_oof, 1.0, fixed_map_full, "A: 基线(T=1.0, 固定0.5)")
    full_metrics['B'] = evaluate_config(df_oof, 1.0, thresholds_t1, "B: 动态阈值(T=1.0)")
    full_metrics['C'] = evaluate_config(df_oof, TEMPERATURE, fixed_map_full, f"C: 温度缩放(T={TEMPERATURE})")
    full_metrics['D'] = evaluate_config(df_oof, TEMPERATURE, thresholds_t1, f"D: 组合现用(T={TEMPERATURE}, T1阈值)")
    full_metrics['E'] = evaluate_config(df_oof, TEMPERATURE, thresholds_t2, f"E: 组合重调(T={TEMPERATURE}, T2阈值)")

    # Step 7: 汇总
    print("\n" + "=" * 70)
    print("📊 改进汇总（评估集, 相对基线 A）")
    print("=" * 70)

    base = eval_metrics['A']
    print(f"\n  {'配置':>30s}  {'Accuracy':>10s}  {'F1 Macro':>10s}  {'走水召回':>10s}  {'LogLoss':>10s}")
    print(f"  {'A: 基线':>30s}  {base['accuracy']:10.4f}  {base['f1_macro']:10.4f}  {base['draw_recall']:10.4f}  {base['log_loss']:10.4f}")
    for key in ['B', 'C', 'D', 'E']:
        m = eval_metrics[key]
        print(
            f"  {m['label']:>30s}  {m['accuracy']:10.4f}  {m['f1_macro']:10.4f}  {m['draw_recall']:10.4f}  {m['log_loss']:10.4f}"
        )
        print(
            f"  {'  ↪ 改进':>30s}  {(m['accuracy']-base['accuracy'])*100:+9.1f}pp  {(m['f1_macro']-base['f1_macro'])*100:+9.1f}pp  {(m['draw_recall']-base['draw_recall'])*100:+9.1f}pp  {(m['log_loss']-base['log_loss'])*100:+9.1f}%"
        )

    # 协同效应
    print(f"\n  协同效应分析 (Accuracy):")
    b_acc = eval_metrics['B']['accuracy'] - base['accuracy']
    c_acc = eval_metrics['C']['accuracy'] - base['accuracy']
    d_acc = eval_metrics['D']['accuracy'] - base['accuracy']
    e_acc = eval_metrics['E']['accuracy'] - base['accuracy']
    print(f"    B(动态) + C(温度) 预期叠加: {(b_acc+c_acc)*100:+.1f}pp")
    print(f"    D(组合现用) 实际: {d_acc*100:+.1f}pp (协同: {(d_acc-(b_acc+c_acc))*100:+.1f}pp)")
    print(f"    E(组合重调) 实际: {e_acc*100:+.1f}pp (协同: {(e_acc-(b_acc+c_acc))*100:+.1f}pp)")

    print(f"\n  协同效应分析 (F1 Macro):")
    b_f1 = eval_metrics['B']['f1_macro'] - base['f1_macro']
    c_f1 = eval_metrics['C']['f1_macro'] - base['f1_macro']
    d_f1 = eval_metrics['D']['f1_macro'] - base['f1_macro']
    e_f1 = eval_metrics['E']['f1_macro'] - base['f1_macro']
    print(f"    B(动态) + C(温度) 预期叠加: {(b_f1+c_f1)*100:+.1f}pp")
    print(f"    D(组合现用) 实际: {d_f1*100:+.1f}pp (协同: {(d_f1-(b_f1+c_f1))*100:+.1f}pp)")
    print(f"    E(组合重调) 实际: {e_f1*100:+.1f}pp (协同: {(e_f1-(b_f1+c_f1))*100:+.1f}pp)")

    # Step 8: 保存结果
    # 为评估集指标添加样本数信息
    for m in eval_metrics.values():
        m['_n_eval'] = len(df_eval)

    # JSON 报告
    report_json = {
        'timestamp': timestamp,
        'temperature': TEMPERATURE,
        'tuning_set_size': len(df_tune),
        'evaluation_set_size': len(df_eval),
        'threshold_configs': threshold_configs,
        'eval_metrics': {k: {kk: vv for kk, vv in v.items() if kk != '_n_eval'} for k, v in eval_metrics.items()},
        'full_oof_metrics': full_metrics,
        'synergy_analysis': {
            'accuracy': {
                'B_contribution': float(b_acc),
                'C_contribution': float(c_acc),
                'expected_additive': float(b_acc + c_acc),
                'D_actual': float(d_acc),
                'D_synergy': float(d_acc - (b_acc + c_acc)),
                'E_actual': float(e_acc),
                'E_synergy': float(e_acc - (b_acc + c_acc)),
            },
            'f1_macro': {
                'B_contribution': float(b_f1),
                'C_contribution': float(c_f1),
                'expected_additive': float(b_f1 + c_f1),
                'D_actual': float(d_f1),
                'D_synergy': float(d_f1 - (b_f1 + c_f1)),
                'E_actual': float(e_f1),
                'E_synergy': float(e_f1 - (b_f1 + c_f1)),
            },
        },
    }

    json_path = os.path.join(REPORT_DIR, f't005v2_combined_eval_{timestamp}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report_json, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 JSON 报告: {json_path}")

    # Markdown 报告
    md_report = generate_markdown_report(eval_metrics, full_metrics, threshold_configs, timestamp)
    md_path = os.path.join(REPORT_DIR, f't005v2_combined_eval_{timestamp}.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_report)
    print(f"💾 Markdown 报告: {md_path}")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ 组合优化评估完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
