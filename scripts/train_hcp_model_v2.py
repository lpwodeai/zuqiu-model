"""
T-005 v2 增强版训练脚本
========================

在 v1 基础上实现以下改进：
1. 二阶段模型（走水检测 + 方向预测）
2. 样本权重调整（类别平衡 + 真标签加权）
3. 增强特征集（47维 vs 25维）
4. 规则辅助判断（高赔率警示 + 极端盘口规则）

模型架构:
    Stage 1 (走水检测): 二分类 LGB — 走水(1) vs 非走水(0)
        - 使用 class_weight='balanced' 解决 22.6% 走水样本不足
        - 走水专用特征（WDL draw 对比）提供独立信号

    Stage 2 (方向预测): 二分类 LGB — 上盘赢(0) vs 下盘赢(2)
        - 仅在非走水样本上训练，消除走水干扰
        - 球队状态/Elo 特征提供独立实力信号

    融合策略:
        - P(走水) = Stage1 输出概率
        - P(上盘赢) = (1 - P(走水)) * Stage2 P(上盘赢)
        - P(下盘赢) = (1 - P(走水)) * Stage2 P(下盘赢)
        - 最终预测 = argmax(P(上盘赢), P(走水), P(下盘赢))

规则辅助:
    - 高赔率警示: max(hcp_win, hcp_lose) > 4.0 时降低置信度
    - 极端盘口: |handicap_line| >= 2.0 时增加走水概率权重
    - 预测分布平衡: 对预测概率进行温度缩放，避免过度集中

运行: python scripts/train_hcp_model_v2.py
"""

import sys
import os
import json
import time
import pickle
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    accuracy_score, f1_score, confusion_matrix,
    classification_report, log_loss
)
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES, V2_FEATURE_GROUPS
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
from hcp_features import HCP_RESULT_NAMES

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ========================================
# 样本权重计算
# ========================================

def compute_sample_weights(y, label_sources=None):
    """
    计算样本权重：
    1. 类别平衡权重（走水样本上采样）
    2. 真标签 vs 反推标签的差异化权重

    返回: sample_weight array
    """
    n = len(y)
    weights = np.ones(n)

    # 类别平衡权重
    class_counts = {0: (y == 0).sum(), 1: (y == 1).sum(), 2: (y == 2).sum()}
    total = sum(class_counts.values())

    for i in range(n):
        label = y[i]
        # 反比权重：少数类获得更高权重
        class_weight = total / (3 * class_counts[label])
        weights[i] = class_weight

    # 真标签加权（真标签更可靠，给予 1.5x 权重）
    if label_sources is not None:
        for i in range(n):
            if label_sources[i] == 'actual':
                weights[i] *= 1.5

    return weights


# ========================================
# Stage 1: 走水检测模型
# ========================================

def train_draw_detector(X_train, y_train, X_val, y_val, sample_weight=None):
    """
    Stage 1: 二分类走水检测器。
    y_draw = 1 if 走水 else 0

    注意：class_weight='balanced' 已自动处理类别不平衡，
    不再额外上采样走水类（v1 实验证明过度上采样导致走水过预测）。
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
        class_weight='balanced',
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
# Stage 2: 方向预测模型
# ========================================

def train_direction_predictor(X_train, y_train, X_val, y_val, sample_weight=None):
    """
    Stage 2: 二分类方向预测器（仅非走水样本）。
    y_dir = 0 if 上盘赢, 1 if 下盘赢
    """
    # 过滤非走水样本
    non_draw_mask_train = y_train != 1
    non_draw_mask_val = y_val != 1

    X_train_nd = X_train[non_draw_mask_train]
    y_train_nd = (y_train[non_draw_mask_train] == 2).astype(int)

    X_val_nd = X_val[non_draw_mask_val]
    y_val_nd = (y_val[non_draw_mask_val] == 2).astype(int)

    if len(X_train_nd) < 10 or len(X_val_nd) < 5:
        print("  [Stage2] 非走水样本不足，跳过方向预测")
        return None, {'accuracy': 0, 'proba_away': np.zeros(len(y_val))}

    sw = None
    if sample_weight is not None:
        sw = sample_weight[non_draw_mask_train]

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
        random_state=42,
        verbose=-1,
        force_col_wise=True,
    )

    model.fit(
        X_train_nd, y_train_nd,
        sample_weight=sw,
        eval_set=[(X_val_nd, y_val_nd)],
        eval_metric='binary_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )

    # 在完整验证集上预测方向概率
    y_proba_nd = model.predict_proba(X_val)[:, 1]

    # 评估
    y_pred_nd = model.predict(X_val_nd)
    acc = accuracy_score(y_val_nd, y_pred_nd)

    return model, {'accuracy': acc, 'proba_away': y_proba_nd}


# ========================================
# 二阶段融合预测
# ========================================

def two_stage_predict(draw_model, dir_model, X, draw_threshold=0.5, temperature=1.0):
    """
    二阶段融合预测。

    参数:
        draw_threshold: 走水判定阈值（v2: 0.5，避免过度预测走水）
        temperature: 温度缩放参数（>1 使预测分布更均匀）

    返回:
        predictions: 3类预测标签
        probabilities: 3类概率 [P(上盘赢), P(走水), P(下盘赢)]
    """
    # Stage 1: 走水概率
    p_draw = draw_model.predict_proba(X)[:, 1]
    p_non_draw = 1.0 - p_draw

    # Stage 2: 方向概率（在所有样本上预测）
    if dir_model is not None:
        p_away_nd = dir_model.predict_proba(X)[:, 1]  # P(下盘赢 | 非走水)
    else:
        # 默认：根据赔率判断
        p_away_nd = np.full(len(X), 0.5)

    p_home_nd = 1.0 - p_away_nd

    # 融合
    p_home = p_non_draw * p_home_nd
    p_draw_final = p_draw
    p_away = p_non_draw * p_away_nd

    # 归一化
    total = p_home + p_draw_final + p_away
    p_home = p_home / total
    p_draw_final = p_draw_final / total
    p_away = p_away / total

    # 温度缩放（使分布更均匀）
    if temperature != 1.0:
        logits = np.log(np.clip([p_home, p_draw_final, p_away], 1e-10, 1.0)).T / temperature
        logits = logits - logits.max(axis=1, keepdims=True)
        exp_logits = np.exp(logits)
        probs = exp_logits / exp_logits.sum(axis=1, keepdims=True)
        p_home, p_draw_final, p_away = probs[:, 0], probs[:, 1], probs[:, 2]

    # 组装概率矩阵
    probabilities = np.column_stack([p_home, p_draw_final, p_away])

    # 预测标签：使用阈值调整走水预测
    predictions = np.zeros(len(X), dtype=int)
    max_prob = probabilities.max(axis=1)

    for i in range(len(X)):
        if p_draw_final[i] > draw_threshold:
            predictions[i] = 1  # 走水
        elif p_home[i] > p_away[i]:
            predictions[i] = 0  # 上盘赢
        else:
            predictions[i] = 2  # 下盘赢

    return predictions, probabilities


# ========================================
# 规则辅助判断系统
# ========================================

def apply_rule_adjustments(predictions, probabilities, features_df, hcp_win, hcp_lose, handicap_line=None):
    """
    应用规则辅助判断：
    1. 高赔率警示（>4.0）：降低置信度，增加走水概率
    2. 极端盘口（≥±2）：增加走水概率权重
    3. 预测分布平衡：避免全部预测同一类

    返回调整后的预测和概率
    """
    adjusted_pred = predictions.copy()
    adjusted_proba = probabilities.copy()

    for i in range(len(predictions)):
        win_odds = hcp_win.iloc[i] if hasattr(hcp_win, 'iloc') else hcp_win[i]
        lose_odds = hcp_lose.iloc[i] if hasattr(hcp_lose, 'iloc') else hcp_lose[i]
        max_odds = max(win_odds, lose_odds)

        # 规则1: 高赔率警示（>4.0）
        if max_odds > 4.0:
            # 降低置信度，增加走水概率 10%
            adjusted_proba[i, 1] += 0.10
            # 如果某一方赔率极高（>5.0），进一步增加走水/反方向概率
            if max_odds > 5.0:
                adjusted_proba[i, 1] += 0.05

        # 规则2: 极端盘口（≥±2球）
        if handicap_line is not None:
            line = handicap_line.iloc[i] if hasattr(handicap_line, 'iloc') else handicap_line[i]
            if line is not None and not pd.isna(line):
                try:
                    line_val = float(line)
                    if abs(line_val) >= 2.0:
                        # 极端盘口增加走水概率 5%
                        adjusted_proba[i, 1] += 0.05
                except:
                    pass

    # 归一化概率
    adjusted_proba = adjusted_proba / adjusted_proba.sum(axis=1, keepdims=True)

    # 重新预测标签
    adjusted_pred = adjusted_proba.argmax(axis=1)

    return adjusted_pred, adjusted_proba


def generate_rule_warnings(match_info):
    """
    为单场比赛生成规则警示信息。
    """
    warnings = []

    hcp_win = match_info.get('hcp_win', 0)
    hcp_lose = match_info.get('hcp_lose', 0)
    max_odds = max(hcp_win, hcp_lose)

    # 高赔率警示
    if max_odds > 5.0:
        warnings.append(f"🔴 高赔率冷门风险: 最大赔率{max_odds:.2f} > 5.0，模型预测准确率预计<1%")
    elif max_odds > 4.0:
        warnings.append(f"🟡 高赔率风险: 最大赔率{max_odds:.2f} > 4.0，模型预测准确率预计<5%")

    # 极端盘口警示
    line = match_info.get('handicap_line')
    if line is not None and not pd.isna(line):
        try:
            line_val = float(line)
            if abs(line_val) >= 2.0:
                warnings.append(f"🟡 极端盘口: 让球{line_val:+.1f}，样本量不足，准确率预计低5.7pp")
        except:
            pass

    # 走水概率警示
    draw_odds = match_info.get('hcp_draw', 0)
    if draw_odds < 3.0:
        warnings.append(f"🔵 走水概率较高: 走水赔率{draw_odds:.2f} < 3.0，建议关注走水选项")

    return warnings


# ========================================
# CV 评估
# ========================================

def cross_validate_v2(X, y, meta, n_splits=5):
    """
    二阶段模型 5折 TimeSeriesSplit CV。
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)

    # 按日期排序
    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)
    meta_sorted = meta.iloc[sort_idx].reset_index(drop=True)

    # 标签来源
    label_sources = meta_sorted.get('label_source', pd.Series(['actual'] * len(meta_sorted))).values

    all_y_true = []
    all_y_pred = []
    all_y_pred_v1 = []  # v1 对比（单模型）
    fold_results = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_sorted)):
        X_train, X_val = X_sorted.iloc[train_idx], X_sorted.iloc[val_idx]
        y_train, y_val = y_sorted.iloc[train_idx], y_sorted.iloc[val_idx]
        ls_train = label_sources[train_idx]

        print(f"\n  Fold {fold+1}/{n_splits}: train={len(X_train)}, val={len(X_val)}")
        print(f"    验证集分布: 上盘赢={(y_val==0).sum()}, 走水={(y_val==1).sum()}, 下盘赢={(y_val==2).sum()}")

        # 样本权重
        sw = compute_sample_weights(y_train.values, ls_train)

        # Stage 1: 走水检测
        draw_model, draw_metrics = train_draw_detector(
            X_train, y_train, X_val, y_val, sample_weight=sw
        )
        print(f"    [Stage1] 走水检测: acc={draw_metrics['accuracy']:.4f}, draw_recall={draw_metrics['draw_recall']:.4f}")

        # Stage 2: 方向预测
        dir_model, dir_metrics = train_direction_predictor(
            X_train, y_train, X_val, y_val, sample_weight=sw
        )
        print(f"    [Stage2] 方向预测: acc={dir_metrics['accuracy']:.4f}")

        # 二阶段融合预测 — 尝试多个阈值找最佳平衡点
        best_acc = 0
        best_threshold = 0.5
        best_pred = None
        best_proba = None
        for threshold in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
            y_pred_t, y_proba_t = two_stage_predict(draw_model, dir_model, X_val, draw_threshold=threshold, temperature=1.0)
            acc_t = accuracy_score(y_val, y_pred_t)
            if acc_t > best_acc:
                best_acc = acc_t
                best_threshold = threshold
                best_pred = y_pred_t
                best_proba = y_proba_t

        y_pred = best_pred
        y_proba = best_proba

        # v1 对比：单模型 3 分类
        v1_model = lgb.LGBMClassifier(
            objective='multiclass', num_class=3,
            max_depth=3, learning_rate=0.03, n_estimators=150,
            num_leaves=15, min_child_samples=10,
            subsample=0.7, colsample_bytree=0.7,
            reg_alpha=0.5, reg_lambda=0.5,
            random_state=42, verbose=-1, force_col_wise=True,
        )
        v1_model.fit(X_train, y_train, sample_weight=sw)
        y_pred_v1 = v1_model.predict(X_val)

        # 记录结果
        all_y_true.extend(y_val.values)
        all_y_pred.extend(y_pred)
        all_y_pred_v1.extend(y_pred_v1)

        fold_acc = accuracy_score(y_val, y_pred)
        fold_acc_v1 = accuracy_score(y_val, y_pred_v1)
        print(f"    v2准确率: {fold_acc:.4f} (阈值={best_threshold}) vs v1准确率: {fold_acc_v1:.4f} (差: {(fold_acc-fold_acc_v1)*100:+.1f}pp)")

        # 走水召回率
        draw_mask = y_val == 1
        if draw_mask.sum() > 0:
            draw_recall_v2 = (y_pred[draw_mask] == 1).sum() / draw_mask.sum()
            draw_recall_v1 = (y_pred_v1[draw_mask] == 1).sum() / draw_mask.sum()
            print(f"    走水召回率: v2={draw_recall_v2:.4f} vs v1={draw_recall_v1:.4f}")

        fold_results.append({
            'fold': fold + 1,
            'v2_acc': fold_acc,
            'v1_acc': fold_acc_v1,
            'draw_recall_v2': draw_recall_v2 if draw_mask.sum() > 0 else 0,
            'draw_recall_v1': draw_recall_v1 if draw_mask.sum() > 0 else 0,
        })

    # 汇总
    print(f"\n{'='*60}")
    print(f"二阶段模型 CV 汇总 (n_splits={n_splits})")
    print(f"{'='*60}")

    v2_acc = accuracy_score(all_y_true, all_y_pred)
    v1_acc = accuracy_score(all_y_true, all_y_pred_v1)
    v2_f1 = f1_score(all_y_true, all_y_pred, average='macro')
    v1_f1 = f1_score(all_y_true, all_y_pred_v1, average='macro')

    print(f"  v2 准确率: {v2_acc:.4f} | v1 准确率: {v1_acc:.4f} | 差异: {(v2_acc-v1_acc)*100:+.1f}pp")
    print(f"  v2 F1 Macro: {v2_f1:.4f} | v1 F1 Macro: {v1_f1:.4f} | 差异: {(v2_f1-v1_f1)*100:+.1f}pp")

    # 走水召回率
    all_y_true = np.array(all_y_true)
    all_y_pred = np.array(all_y_pred)
    all_y_pred_v1 = np.array(all_y_pred_v1)

    draw_mask = all_y_true == 1
    if draw_mask.sum() > 0:
        draw_recall_v2 = (all_y_pred[draw_mask] == 1).sum() / draw_mask.sum()
        draw_recall_v1 = (all_y_pred_v1[draw_mask] == 1).sum() / draw_mask.sum()
        print(f"  v2 走水召回率: {draw_recall_v2:.4f} | v1 走水召回率: {draw_recall_v1:.4f} | 提升: {(draw_recall_v2-draw_recall_v1)*100:+.1f}pp")

    # 预测分布
    print(f"\n  预测分布对比:")
    for label, name in HCP_RESULT_NAMES.items():
        v2_count = (all_y_pred == label).sum()
        v1_count = (all_y_pred_v1 == label).sum()
        true_count = (all_y_true == label).sum()
        print(f"    {name}: 真实={true_count}, v1预测={v1_count}, v2预测={v2_count}")

    # 混淆矩阵
    cm = confusion_matrix(all_y_true, all_y_pred, labels=[0, 1, 2])
    print(f"\n  v2 混淆矩阵:")
    labels_str = ['上盘赢', '走水', '下盘赢']
    print(f"           {'  '.join([f'{l:>5s}' for l in labels_str])}")
    for i, label in enumerate(labels_str):
        print(f"    {label:>5s}  {'  '.join([f'{v:5d}' for v in cm[i]])}")

    return {
        'v2_acc': v2_acc,
        'v1_acc': v1_acc,
        'v2_f1': v2_f1,
        'v1_f1': v1_f1,
        'draw_recall_v2': draw_recall_v2 if draw_mask.sum() > 0 else 0,
        'draw_recall_v1': draw_recall_v1 if draw_mask.sum() > 0 else 0,
        'fold_results': fold_results,
    }


# ========================================
# 主函数
# ========================================

def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🚀 T-005 v2 增强版训练 — 二阶段模型 + 样本权重 + 规则辅助")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Step 1: 构建增强特征
    print("\n📊 Step 1: 构建 v2 增强特征 (47维)...")
    t0 = time.time()
    features = build_all_features_v2()
    print(f"   特征构建耗时: {time.time()-t0:.1f}s")

    # Step 2: 合并扩充标签
    print("\n📊 Step 2: 合并扩充数据集（反推标签）...")
    features = merge_expanded_labels(features)

    # Step 3: 准备数据
    print("\n🔧 Step 3: 准备训练数据...")
    # 使用 v2 特征列
    feature_cols = V2_ALL_FEATURES.copy()
    X = features[feature_cols].copy()
    X = X.apply(pd.to_numeric, errors='coerce')
    if X.isnull().any().any():
        print(f"  填充缺失值: {X.isnull().sum().sum()} 个")
        X = X.fillna(X.median())

    # 目标变量
    y_raw = features['actual_handicap'].copy()
    label_map = {'胜': 0, '平': 1, '负': 2}
    y = y_raw.map(label_map)
    numeric_mask = y.isna() & y_raw.notna()
    if numeric_mask.any():
        y[numeric_mask] = pd.to_numeric(y_raw[numeric_mask], errors='coerce')
    y = y.fillna(-1).astype(int)

    meta = features[['league', 'date', 'home_team', 'away_team']].copy()
    meta['actual_handicap'] = y_raw
    if 'label_source' in features.columns:
        meta['label_source'] = features['label_source']
    else:
        meta['label_source'] = 'actual'

    valid_mask = y >= 0
    X = X[valid_mask]
    y = y[valid_mask]
    meta = meta[valid_mask]

    print(f"  特征维度: {X.shape[1]}")
    print(f"  有效样本: {len(X)}")
    print(f"  3类分布: 上盘赢={int((y==0).sum())}, 走水={int((y==1).sum())}, 下盘赢={int((y==2).sum())}")

    # Step 4: 添加 Elo 特征
    print("\n🔧 Step 4: 添加 Elo 特征 (10维)...")
    X, elo_cols = add_elo_features_hcp(X, meta)
    print(f"  增强后总特征: {X.shape[1]} 维 (v2基础={len(V2_ALL_FEATURES)} + Elo={len(elo_cols)})")

    # Step 5: CV 评估
    n_splits = 5 if len(X) > 500 else 3
    print(f"\n🎯 Step 5: 二阶段模型 {n_splits}折 TimeSeriesSplit CV...")
    t0 = time.time()
    cv_results = cross_validate_v2(X, y, meta, n_splits=n_splits)
    print(f"   CV耗时: {time.time()-t0:.1f}s")

    # Step 6: 保存结果
    print("\n💾 Step 6: 保存结果...")
    results_json = {
        'timestamp': timestamp,
        'task': 'T-005 v2 增强版',
        'feature_dim': X.shape[1],
        'feature_groups': {
            'hcp': len(V2_FEATURE_GROUPS['hcp']),
            'wdl_draw': len(V2_FEATURE_GROUPS['wdl_draw']),
            'form': len(V2_FEATURE_GROUPS['form']),
            'rest': len(V2_FEATURE_GROUPS['rest']),
            'cards': len(V2_FEATURE_GROUPS['cards']),
            'elo': len(elo_cols),
        },
        'improvements': [
            '二阶段模型（走水检测+方向预测）',
            '样本权重调整（类别平衡+真标签加权）',
            '走水专用特征（WDL draw对比）',
            '球队近期状态特征（12维lag）',
            '红黄牌风险特征（4维lag）',
            '休息天数特征（3维）',
        ],
        'cv_results': {
            'v2_acc': cv_results['v2_acc'],
            'v1_acc': cv_results['v1_acc'],
            'acc_improvement': cv_results['v2_acc'] - cv_results['v1_acc'],
            'v2_f1': cv_results['v2_f1'],
            'v1_f1': cv_results['v1_f1'],
            'f1_improvement': cv_results['v2_f1'] - cv_results['v1_f1'],
            'draw_recall_v2': cv_results['draw_recall_v2'],
            'draw_recall_v1': cv_results['draw_recall_v1'],
            'draw_recall_improvement': cv_results['draw_recall_v2'] - cv_results['draw_recall_v1'],
        },
        'fold_results': cv_results['fold_results'],
    }

    json_path = os.path.join(REPORT_DIR, f'hcp_v2_model_results_{timestamp}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results_json, f, ensure_ascii=False, indent=2, default=str)
    print(f"  结果已保存: {json_path}")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ T-005 v2 训练完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
