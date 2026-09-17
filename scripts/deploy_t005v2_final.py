"""
T-005 v2 最终部署脚本（集成三步优化）
=====================================

集成三项优化参数，作为生产环境最终配置：
    1. Stage1 class_weight = {0: 1.0, 1: 1.5}  (ratio=1.5, 从源头减少走水过预测)
    2. Temperature Scaling T = 2.150            (概率校准, 优化 F1 Macro)
    3. 动态走水阈值（按盘口线类别）:
         home_get_1  → 0.400
         home_give_1 → 0.400
         home_give_2+→ 0.425
         其他类别    → 0.500 (默认)

本脚本功能:
    Part 1: 用 ratio=1.5 训练最终二阶段模型并持久化
    Part 2: 在测试集（最近 20% 比赛）上跑完整预测，分析盘口线分布
    Part 3: 提供单场预测接口 predict_single_match()

优化效果（vs v2 balanced 基线组合）:
    - Accuracy:  0.4847 → 0.4896 (+0.5pp)
    - F1 Macro:  0.4375 → 0.4488 (+1.1pp)
    - 走水召回率: 0.2098 → 0.2398 (+3.0pp)
    - 走水精确率: 0.2369 → 0.2651 (+2.8pp)
    - 走水预测率: 0.1994 → 0.2037 (实际 0.2252, 偏差缩小)

运行: python scripts/deploy_t005v2_final.py
"""

import sys
import os
import json
import pickle
import time
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES, V2_FEATURE_GROUPS
from hcp_features import HCP_RESULT_NAMES, MAX_VALID_TIMESTAMP
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp, find_latest_expanded_csv
from train_hcp_model_v2 import (
    compute_sample_weights, train_direction_predictor,
    apply_rule_adjustments, generate_rule_warnings,
)
from dynamic_draw_threshold import categorize_handicap_line, get_handicap_categories
from elo_rating import expected_score, DEFAULT_ELO, HOME_ADVANTAGE

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False
    print("ERROR: lightgbm not available")
    sys.exit(1)

from sklearn.metrics import accuracy_score, f1_score, confusion_matrix, log_loss

# ========================================
# 最终优化参数（三步优化产出）
# ========================================

# 任务3产出：Stage1 走水检测器的最优 class_weight
# ratio = w_draw / w_non_draw = 1.5
# 相比 'balanced'(≈2.3) 降低走水类权重，从源头减少过预测
OPTIMAL_CLASS_WEIGHT = {0: 1.0, 1: 1.5}

# 任务2产出：温度缩放参数（在 tune 集上搜索，最大化 F1 Macro）
OPTIMAL_TEMPERATURE = 2.150

# 任务1产出：按盘口线类别的动态走水阈值
# 在 T=2.150 + ratio=1.5 下重新搜索得到
OPTIMAL_DYNAMIC_THRESHOLDS = {
    'home_get_1': 0.400,
    'home_give_1': 0.400,
    'home_give_2+': 0.425,
}
DEFAULT_DRAW_THRESHOLD = 0.500

# 稳定性验证产出（2026-08-11）：方案A — 关闭 apply_rule_adjustments 规则引擎
# 根因：规则引擎在 30 场真实比赛上将 11/30 场（36.7%）从非走水强制改判为走水，
#       导致走水预测率从核心模型的 16.7% 跃升至 53.3%，严重偏离评估报告 21.3%。
# 决策：生产环境默认关闭规则引擎改判，仅保留 generate_rule_warnings 作为人工提示信号。
# 验证：30 场走水预测率 16.7%（落在 20±5% 区间），上盘赢/下盘赢预测占比与评估报告
#       偏差仅 1-3pp，证明核心模型在生产环境分布稳定。
USE_RULE_ENGINE = False  # False=方案A(默认,仅核心模型) / True=启用规则引擎改判

# 模型超参数（与 train_hcp_model_v2.py 保持一致）
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
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DB_PATH = os.path.join(DATA_DIR, 'odds.db')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ========================================
# Part 1: 最终模型训练（ratio=1.5）
# ========================================

def train_draw_detector_final(X_train, y_train, X_val, y_val, sample_weight=None):
    """
    Stage 1: 二分类走水检测器（使用最优 class_weight={0:1.0, 1:1.5}）。

    与 train_hcp_model_v2.py 的差异:
        - class_weight 从 'balanced'(≈2.3) 改为 {0:1.0, 1:1.5}
        - 降低走水类权重 → 减少走水过预测 → 提升走水精确率
    """
    y_draw_train = (y_train == 1).astype(int)
    y_draw_val = (y_val == 1).astype(int)

    model = lgb.LGBMClassifier(
        **STAGE1_PARAMS,
        class_weight=OPTIMAL_CLASS_WEIGHT,
    )

    model.fit(
        X_train, y_draw_train,
        sample_weight=sample_weight,
        eval_set=[(X_val, y_draw_val)],
        eval_metric='binary_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )

    y_pred = model.predict(X_val)
    acc = accuracy_score(y_draw_val, y_pred)
    recall = (y_pred[y_draw_val == 1] == 1).sum() / max((y_draw_val == 1).sum(), 1)

    return model, {'accuracy': acc, 'draw_recall': recall}


def two_stage_predict_final(draw_model, dir_model, X, handicap_categories=None,
                             temperature=OPTIMAL_TEMPERATURE,
                             threshold_map=OPTIMAL_DYNAMIC_THRESHOLDS):
    """
    最终二阶段融合预测（集成 T=2.150 + 动态阈值）。

    与 train_hcp_model_v2.two_stage_predict 的差异:
        1. 温度缩放默认 T=2.150（非 1.0）
        2. 走水阈值按盘口线类别动态调整（非固定 0.5）

    参数:
        handicap_categories: pd.Series, 每个样本的盘口线类别字符串
                            若为 None 则全部使用默认阈值 0.5
        temperature: 温度缩放参数
        threshold_map: {category: threshold} 映射

    返回:
        predictions: (n,) 3类预测标签
        probabilities: (n, 3) 概率矩阵
    """
    # Stage 1: 走水概率
    p_draw = draw_model.predict_proba(X)[:, 1]
    p_non_draw = 1.0 - p_draw

    # Stage 2: 方向概率
    if dir_model is not None:
        p_away_nd = dir_model.predict_proba(X)[:, 1]
    else:
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

    # 温度缩放（T=2.150 使分布更均匀）
    if temperature != 1.0:
        logits = np.log(np.clip([p_home, p_draw_final, p_away], 1e-10, 1.0)).T / temperature
        logits = logits - logits.max(axis=1, keepdims=True)
        exp_logits = np.exp(logits)
        probs = exp_logits / exp_logits.sum(axis=1, keepdims=True)
        p_home, p_draw_final, p_away = probs[:, 0], probs[:, 1], probs[:, 2]

    probabilities = np.column_stack([p_home, p_draw_final, p_away])

    # 动态阈值预测
    predictions = np.zeros(len(X), dtype=int)
    if handicap_categories is not None:
        thresholds = np.array([
            threshold_map.get(c, DEFAULT_DRAW_THRESHOLD)
            for c in handicap_categories
        ])
    else:
        thresholds = np.full(len(X), DEFAULT_DRAW_THRESHOLD)

    for i in range(len(X)):
        if p_draw_final[i] > thresholds[i]:
            predictions[i] = 1  # 走水
        elif p_home[i] > p_away[i]:
            predictions[i] = 0  # 上盘赢
        else:
            predictions[i] = 2  # 下盘赢

    return predictions, probabilities


# ========================================
# Elo 快照构建（复用 deploy_t005_model 逻辑）
# ========================================

def build_team_elo_snapshot():
    """基于历史比赛结果计算所有球队的最新 Elo 评分快照。"""
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT match_id, match_date, home_team, away_team, actual_score
        FROM matches
        WHERE actual_score IS NOT NULL
          AND actual_score != ''
        ORDER BY match_date
    """
    df = pd.read_sql(query, conn)
    conn.close()

    def parse_score(s):
        try:
            s = str(s).strip()
            if '其它' in s or s == '':
                return None, None
            for sep in [':', '-']:
                if sep in s:
                    parts = s.split(sep)
                    return int(parts[0]), int(parts[1])
            return None, None
        except:
            return None, None

    from elo_rating import update_elo, K_FACTOR
    elo_ratings = {}
    team_history = {}

    for _, row in df.iterrows():
        hg, ag = parse_score(row['actual_score'])
        if hg is None:
            continue
        home = row['home_team']
        away = row['away_team']
        date = row['match_date']

        elo_h = elo_ratings.get(home, DEFAULT_ELO)
        elo_a = elo_ratings.get(away, DEFAULT_ELO)

        if hg > ag:
            actual_h = 1.0
        elif hg == ag:
            actual_h = 0.5
        else:
            actual_h = 0.0

        new_elo_h, new_elo_a = update_elo(elo_h, elo_a, actual_h, K_FACTOR, HOME_ADVANTAGE)
        elo_ratings[home] = new_elo_h
        elo_ratings[away] = new_elo_a
        team_history.setdefault(home, []).append((date, new_elo_h))
        team_history.setdefault(away, []).append((date, new_elo_a))

    elo_momentum = {}
    for team, hist in team_history.items():
        if len(hist) >= 5:
            recent_5 = [h[1] for h in hist[-5:]]
            elo_momentum[team] = recent_5[-1] - recent_5[0]
        else:
            elo_momentum[team] = 0.0

    print(f"[ELO] 计算了 {len(elo_ratings)} 支球队的最新 Elo 评分")
    print(f"[ELO] Elo 评分范围: {min(elo_ratings.values()):.1f} ~ {max(elo_ratings.values()):.1f}")

    return {
        'elo_ratings': elo_ratings,
        'elo_momentum': elo_momentum,
        'default_elo': DEFAULT_ELO,
        'k_factor': K_FACTOR,
        'home_advantage': HOME_ADVANTAGE,
    }


def build_elo_features_for_prediction(home_team, away_team, elo_snapshot):
    """使用 Elo 快照为单场比赛构建 10 维 Elo 特征。"""
    elo_ratings = elo_snapshot['elo_ratings']
    elo_momentum = elo_snapshot['elo_momentum']
    home_adv = elo_snapshot.get('home_advantage', HOME_ADVANTAGE)

    home_elo = elo_ratings.get(home_team, DEFAULT_ELO)
    away_elo = elo_ratings.get(away_team, DEFAULT_ELO)

    elo_diff = home_elo - away_elo + home_adv
    elo_ratio = home_elo / max(away_elo, 1.0)
    elo_home_expected = expected_score(home_elo, away_elo, home_adv)
    elo_away_expected = 1.0 - elo_home_expected
    elo_draw_prob = 1.0 / (1.0 + np.exp(abs(elo_diff) / 100.0)) * 0.3 + 0.2
    home_momentum = elo_momentum.get(home_team, 0.0)
    away_momentum = elo_momentum.get(away_team, 0.0)
    elo_confidence = (home_elo + away_elo) / 3000.0

    return {
        'home_elo': home_elo, 'away_elo': away_elo,
        'elo_diff': elo_diff, 'elo_ratio': elo_ratio,
        'elo_home_expected': elo_home_expected, 'elo_away_expected': elo_away_expected,
        'elo_draw_prob': elo_draw_prob,
        'home_elo_momentum': home_momentum, 'away_elo_momentum': away_momentum,
        'elo_confidence': elo_confidence,
    }


# ========================================
# Part 2: 测试集完整预测 + 盘口线分布分析
# ========================================

def evaluate_on_test_set(draw_model, dir_model, X_test, y_test, meta_test,
                          handicap_categories_test):
    """
    在测试集上跑完整预测，分析盘口线分布是否符合预期。

    测试集 = 最近 20% 的比赛（按日期排序）
    """
    print("\n" + "=" * 70)
    print("📊 Part 2: 测试集完整预测 + 盘口线分布分析")
    print("=" * 70)

    # 应用最终优化参数预测
    y_pred, y_proba = two_stage_predict_final(
        draw_model, dir_model, X_test,
        handicap_categories=handicap_categories_test,
        temperature=OPTIMAL_TEMPERATURE,
        threshold_map=OPTIMAL_DYNAMIC_THRESHOLDS,
    )

    # 整体指标
    acc = accuracy_score(y_test, y_pred)
    f1_macro = f1_score(y_test, y_pred, average='macro')
    f1_weighted = f1_score(y_test, y_pred, average='weighted')
    ll = log_loss(y_test, np.clip(y_proba, 1e-10, 1.0))

    draw_mask = y_test == 1
    draw_recall = (y_pred[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)
    draw_pred_mask = y_pred == 1
    draw_precision = (y_test[draw_pred_mask] == 1).sum() / max(draw_pred_mask.sum(), 1)
    draw_pred_rate = draw_pred_mask.sum() / len(y_pred)
    actual_draw_rate = draw_mask.sum() / len(y_test)

    print(f"\n  📈 整体指标（测试集 {len(y_test)} 场）:")
    print(f"    Accuracy:      {acc:.4f}")
    print(f"    F1 Macro:      {f1_macro:.4f}")
    print(f"    F1 Weighted:   {f1_weighted:.4f}")
    print(f"    LogLoss:       {ll:.4f}")
    print(f"    走水预测率:    {draw_pred_rate:.4f} (实际走水率: {actual_draw_rate:.4f})")
    print(f"    走水召回率:    {draw_recall:.4f}")
    print(f"    走水精确率:    {draw_precision:.4f}")

    # 预测分布
    print(f"\n  📊 预测分布 vs 实际分布:")
    print(f"    {'类别':>8s}  {'预测':>6s}  {'实际':>6s}  {'偏差':>6s}")
    for label, name in HCP_RESULT_NAMES.items():
        pred_count = int((y_pred == label).sum())
        actual_count = int((y_test == label).sum())
        diff = pred_count - actual_count
        print(f"    {name:>8s}  {pred_count:6d}  {actual_count:6d}  {diff:+6d}")

    # 混淆矩阵
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])
    print(f"\n  📊 混淆矩阵:")
    labels_str = ['上盘赢', '走水', '下盘赢']
    print(f"             {'  '.join([f'{l:>5s}' for l in labels_str])}")
    for i, l in enumerate(labels_str):
        print(f"      {l:>5s}  {'  '.join([f'{v:5d}' for v in cm[i]])}")

    # 按盘口线类别分析
    print(f"\n  📊 按盘口线类别分析:")
    cats = handicap_categories_test.values
    print(f"    {'类别':>15s}  {'样本':>6s}  {'准确率':>7s}  {'走水预测率':>10s}  {'实际走水率':>10s}  {'使用阈值':>8s}")
    for cat in sorted(set(cats)):
        mask = cats == cat
        n = mask.sum()
        if n == 0:
            continue
        cat_acc = accuracy_score(y_test[mask], y_pred[mask])
        cat_draw_pred = (y_pred[mask] == 1).sum() / n
        cat_draw_actual = (y_test[mask] == 1).sum() / n
        threshold = OPTIMAL_DYNAMIC_THRESHOLDS.get(cat, DEFAULT_DRAW_THRESHOLD)
        print(f"    {cat:>15s}  {n:6d}  {cat_acc:7.4f}  {cat_draw_pred:10.4f}  {cat_draw_actual:10.4f}  {threshold:8.3f}")

    # 盘口线分布是否符合预期
    print(f"\n  🔍 盘口线分布合理性检查:")
    expected_dist = {
        'home_give_1': (0.55, 0.80),   # 让1球最常见
        'home_get_1': (0.15, 0.30),    # 受让1球次之
        'home_give_2+': (0.02, 0.08),  # 让2球较少
        'home_give_half': (0.02, 0.10),
        'home_get_half': (0.02, 0.10),
    }
    cat_counts = pd.Series(cats).value_counts()
    total = len(cats)
    all_reasonable = True
    for cat, (lo, hi) in expected_dist.items():
        count = cat_counts.get(cat, 0)
        ratio = count / total
        ok = lo <= ratio <= hi
        status = '✅' if ok else '⚠️'
        if not ok:
            all_reasonable = False
        print(f"    {status} {cat:>15s}: {count:4d} 场 ({ratio*100:5.1f}%, 预期 {lo*100:.0f}~{hi*100:.0f}%)")

    if all_reasonable:
        print(f"\n  ✅ 盘口线分布符合预期：让1球占主导（主场优势），受让1球次之，让2球最少")
    else:
        print(f"\n  ⚠️ 盘口线分布部分偏离预期，请检查反推模型")

    return {
        'accuracy': float(acc),
        'f1_macro': float(f1_macro),
        'f1_weighted': float(f1_weighted),
        'log_loss': float(ll),
        'draw_recall': float(draw_recall),
        'draw_precision': float(draw_precision),
        'draw_prediction_rate': float(draw_pred_rate),
        'actual_draw_rate': float(actual_draw_rate),
        'confusion_matrix': cm.tolist(),
        'distribution': {HCP_RESULT_NAMES[i]: int((y_pred == i).sum()) for i in range(3)},
        'actual_distribution': {HCP_RESULT_NAMES[i]: int((y_test == i).sum()) for i in range(3)},
        'by_category': {
            cat: {
                'n': int((cats == cat).sum()),
                'accuracy': float(accuracy_score(y_test[cats == cat], y_pred[cats == cat])),
                'threshold': float(OPTIMAL_DYNAMIC_THRESHOLDS.get(cat, DEFAULT_DRAW_THRESHOLD)),
            }
            for cat in sorted(set(cats))
        },
    }


# ========================================
# Part 3: 单场预测接口
# ========================================

def predict_single_match(home_team, away_team, match_date, league,
                          hcp_win, hcp_draw, hcp_lose, handicap_line,
                          draw_model, dir_model, elo_snapshot,
                          feature_template=None):
    """
    单场比赛预测接口（集成所有优化参数）。

    参数:
        home_team, away_team: 球队英文名
        match_date: 比赛日期 'YYYY-MM-DD'
        league: 联赛名
        hcp_win, hcp_draw, hcp_lose: 让球赔率
        handicap_line: 盘口线数值（负=主队让球，正=主队受让）
        draw_model, dir_model: 已加载的模型
        elo_snapshot: Elo 快照
        feature_template: 特征模板（如有则复用，否则需外部构建）

    返回:
        dict: 预测结果
    """
    # 盘口线类别
    category = categorize_handicap_line(handicap_line)
    threshold = OPTIMAL_DYNAMIC_THRESHOLDS.get(category, DEFAULT_DRAW_THRESHOLD)

    # Elo 特征
    elo_features = build_elo_features_for_prediction(home_team, away_team, elo_snapshot)

    # 规则警示
    match_info = {
        'hcp_win': hcp_win, 'hcp_draw': hcp_draw, 'hcp_lose': hcp_lose,
        'handicap_line': handicap_line,
    }
    warnings_list = generate_rule_warnings(match_info)

    # 如果有特征模板，进行预测
    if feature_template is not None:
        X_single = feature_template.copy()
        for k, v in elo_features.items():
            if k in X_single.columns:
                X_single.loc[X_single.index[0], k] = v

        handicap_cats = pd.Series([category])
        y_pred, y_proba = two_stage_predict_final(
            draw_model, dir_model, X_single,
            handicap_categories=handicap_cats,
            temperature=OPTIMAL_TEMPERATURE,
            threshold_map=OPTIMAL_DYNAMIC_THRESHOLDS,
        )

        # 方案A：默认关闭规则引擎改判（USE_RULE_ENGINE=False）
        # 仅当显式启用时才调用 apply_rule_adjustments
        if USE_RULE_ENGINE:
            hcp_win_s = pd.Series([hcp_win])
            hcp_lose_s = pd.Series([hcp_lose])
            handicap_s = pd.Series([handicap_line])
            y_pred_adj, y_proba_adj = apply_rule_adjustments(
                y_pred, y_proba, X_single, hcp_win_s, hcp_lose_s, handicap_s
            )
        else:
            y_pred_adj, y_proba_adj = y_pred, y_proba

        pred = int(y_pred_adj[0])
        proba = y_proba_adj[0]
    else:
        pred = None
        proba = None

    label_names = ['上盘赢', '走水', '下盘赢']
    return {
        'match': f"{home_team} vs {away_team}",
        'date': match_date,
        'league': league,
        'handicap_line': handicap_line,
        'handicap_category': category,
        'draw_threshold_used': threshold,
        'temperature': OPTIMAL_TEMPERATURE,
        'class_weight': str(OPTIMAL_CLASS_WEIGHT),
        'use_rule_engine': USE_RULE_ENGINE,
        'mode': 'plan_a_core_only' if not USE_RULE_ENGINE else 'with_rule_engine',
        'prediction': label_names[pred] if pred is not None else '需要特征模板',
        'probability': {
            '上盘赢': float(proba[0]) if proba is not None else None,
            '走水': float(proba[1]) if proba is not None else None,
            '下盘赢': float(proba[2]) if proba is not None else None,
        },
        'elo_features': elo_features,
        'warnings': warnings_list,
    }


# ========================================
# 主函数
# ========================================

def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🚀 T-005 v2 最终部署（集成三步优化）")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  优化参数:")
    print(f"    Stage1 class_weight: {OPTIMAL_CLASS_WEIGHT} (ratio=1.5)")
    print(f"    Temperature:         T={OPTIMAL_TEMPERATURE}")
    print(f"    动态阈值:            {OPTIMAL_DYNAMIC_THRESHOLDS}")
    print(f"    默认阈值:            {DEFAULT_DRAW_THRESHOLD}")

    # ========== Part 1: 训练最终模型 ==========

    print("\n" + "=" * 70)
    print("📋 Part 1: 训练最终二阶段模型（ratio=1.5）")
    print("=" * 70)

    # Step 1: 构建特征
    print("\n📊 Step 1: 构建 v2 增强特征 (47维)...")
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
    print(f"  最终特征: {X.shape[1]}维, 样本: {len(X)}场")

    # 获取盘口线类别
    handicap_categories = get_handicap_categories(meta)

    # Step 2: 划分训练集 / 测试集（80/20，按时间）
    print("\n📊 Step 2: 划分训练集(80%) / 测试集(20%)...")
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

    # 用于 early stopping 的验证集（训练集的后 20%）
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

    print(f"  训练集: {len(X_train)} 场 ({meta_sorted.iloc[:split_idx]['date'].min()} ~ {meta_sorted.iloc[:split_idx]['date'].max()})")
    print(f"  测试集: {len(X_test)} 场 ({meta_test['date'].min()} ~ {meta_test['date'].max()})")

    # Step 3: 训练最终模型
    print("\n🎯 Step 3: 训练最终二阶段模型（ratio=1.5）...")

    # Stage 1: 走水检测（ratio=1.5）
    draw_model, draw_metrics = train_draw_detector_final(
        X_train_es, y_train_es, X_val_es, y_val_es, sample_weight=sw_es
    )
    print(f"  [Stage1] 走水检测(ratio=1.5): acc={draw_metrics['accuracy']:.4f}, "
          f"draw_recall={draw_metrics['draw_recall']:.4f}")

    # Stage 2: 方向预测
    dir_model, dir_metrics = train_direction_predictor(
        X_train_es, y_train_es, X_val_es, y_val_es, sample_weight=sw_es
    )
    print(f"  [Stage2] 方向预测: acc={dir_metrics['accuracy']:.4f}")

    # 用全部训练数据重训最终模型
    print("  用全部训练数据重训最终模型...")
    final_draw_model, _ = train_draw_detector_final(
        X_train, y_train, X_val_es, y_val_es, sample_weight=sw_train
    )
    final_dir_model, _ = train_direction_predictor(
        X_train, y_train, X_val_es, y_val_es, sample_weight=sw_train
    )
    print(f"  ✅ 最终模型训练完成")

    # Step 4: 保存模型
    print("\n💾 Step 4: 保存最终模型...")

    draw_model_path = os.path.join(ASSETS_DIR, 't005v2_final_draw_detector.pkl')
    with open(draw_model_path, 'wb') as f:
        pickle.dump(final_draw_model, f)

    dir_model_path = os.path.join(ASSETS_DIR, 't005v2_final_direction_predictor.pkl')
    with open(dir_model_path, 'wb') as f:
        pickle.dump(final_dir_model, f)

    # Elo 快照
    elo_snapshot = build_team_elo_snapshot()
    elo_path = os.path.join(ASSETS_DIR, 't005v2_final_elo_ratings.json')
    with open(elo_path, 'w', encoding='utf-8') as f:
        json.dump(elo_snapshot, f, ensure_ascii=False, indent=2)

    # 元数据
    metadata = {
        'model_name': 'T-005 v2 Final (Three-Step Optimization + Plan A)',
        'version': timestamp,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'architecture': 'two-stage (draw detector + direction predictor)',
        'feature_columns': list(X.columns),
        'feature_dim': X.shape[1],
        'training_samples': len(X_train),
        'test_samples': len(X_test),
        'optimization_params': {
            'stage1_class_weight': OPTIMAL_CLASS_WEIGHT,
            'stage1_class_weight_ratio': 1.5,
            'temperature': OPTIMAL_TEMPERATURE,
            'dynamic_thresholds': OPTIMAL_DYNAMIC_THRESHOLDS,
            'default_threshold': DEFAULT_DRAW_THRESHOLD,
            'use_rule_engine': USE_RULE_ENGINE,
            'mode': 'plan_a_core_only' if not USE_RULE_ENGINE else 'with_rule_engine',
        },
        'optimization_history': [
            '任务1: 组合动态阈值+T=2.150 → F1 Macro 0.4375 (协同效应+)',
            '任务2: 全量验证集评估 → Accuracy 0.4847, 走水预测率0.1994',
            '任务3: stage1 class_weight调优 → ratio=1.5 最优',
            '稳定性验证(2026-08-11): 30场真实比赛发现规则引擎导致走水率从16.7%→53.3%，决策方案A关闭规则引擎',
        ],
        'improvements_over_balanced': {
            'accuracy': '+0.5pp (0.4847→0.4896)',
            'f1_macro': '+1.1pp (0.4375→0.4488)',
            'draw_recall': '+3.0pp (0.2098→0.2398)',
            'draw_precision': '+2.8pp (0.2369→0.2651)',
        },
        'plan_a_stability_validation': {
            'date': '2026-08-11',
            'n_matches': 30,
            'date_range': '2026-05-17 ~ 2026-05-25',
            'draw_pred_rate_no_rules': 0.1667,
            'draw_pred_rate_with_rules': 0.5333,
            'baseline_report_draw_pred_rate': 0.2133,
            'conclusion': '方案A(关闭规则引擎)走水率16.7%落在20±5%目标区间，与评估报告偏差4.66pp，验证通过',
            'rule_engine_override_count': 11,
            'rule_engine_override_rate': 0.3667,
        },
        'stage1_eval': {'accuracy': draw_metrics['accuracy'], 'draw_recall': draw_metrics['draw_recall']},
        'stage2_eval': {'accuracy': dir_metrics['accuracy']},
    }
    metadata_path = os.path.join(ASSETS_DIR, 't005v2_final_metadata.json')
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print(f"  ✅ Stage 1 模型: {draw_model_path}")
    print(f"  ✅ Stage 2 模型: {dir_model_path}")
    print(f"  ✅ Elo 快照:     {elo_path}")
    print(f"  ✅ 元数据:       {metadata_path}")

    # ========== Part 2: 测试集完整预测 ==========

    test_metrics = evaluate_on_test_set(
        final_draw_model, final_dir_model,
        X_test, y_test.values, meta_test, cat_test
    )

    # ========== 保存最终评估报告 ==========

    report = {
        'timestamp': timestamp,
        'model_version': 't005v2_final',
        'optimization_params': metadata['optimization_params'],
        'training_info': {
            'train_samples': len(X_train),
            'test_samples': len(X_test),
            'train_date_range': [str(meta_sorted.iloc[:split_idx]['date'].min()),
                                  str(meta_sorted.iloc[:split_idx]['date'].max())],
            'test_date_range': [str(meta_test['date'].min()), str(meta_test['date'].max())],
        },
        'test_metrics': test_metrics,
    }

    report_path = os.path.join(REPORT_DIR, f't005v2_final_deployment_{timestamp}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 最终评估报告: {report_path}")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ T-005 v2 最终部署完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")
    print(f"\n部署产物:")
    print(f"  1. {draw_model_path}")
    print(f"  2. {dir_model_path}")
    print(f"  3. {elo_path}")
    print(f"  4. {metadata_path}")
    print(f"  5. {report_path}")
    print(f"\n下一步: 使用 predict_single_match() 接口进行单场预测")


if __name__ == "__main__":
    main()
