"""
D-011 特征选择与降维脚本
========================

目标：将特征从 130 维降至 60-70 维核心集，提升模型泛化能力。

三种特征选择方法：
1. 相关性分析（剔除 |corr|>0.9 的高相关特征对）
2. XGBoost 特征重要性（gain + weight 双指标）
3. RFE 递归特征消除（基于 XGBClassifier）

详细日志：记录每个被剔除特征的剔除原因和相关性数值/重要性分数。

输出：
- reports/d011_feature_selection_report_{timestamp}.json（完整选择报告）
- reports/d011_selected_features_{timestamp}.json（选中特征列表）
- reports/d011_elimination_log_{timestamp}.csv（剔除日志，含原因和数值）

使用：
    python scripts/feature_selection_d011.py [--target-features 65] [--corr-threshold 0.9]
"""

import os
import sys
import json
import argparse
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.feature_selection import RFE

# 添加项目根目录到 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from feature_utils import load_match_data_odds, build_all_features, ODDS_DB_PATH
from feature_temporal import detect_leakage, feature_temporal_split, validate_no_leakage

# 强制保留的赔率特征前缀（最强预测信号，D-011 任务要求保留所有赔率特征）
ODDS_FEATURE_PREFIXES = (
    'wdl_', 'hcp_', 'tg_', 'has_', 'odds_',
    'bookmaker_margin', 'value_bet_',
    'score_',  # T-003.1: 比分赔率特征（强制保留）
)


def log_section(title):
    """打印分节标题"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def log_subsection(title):
    """打印子节标题"""
    print(f"\n  --- {title} ---")


def is_odds_feature(feature_name):
    """判断是否为赔率特征（强制保留）"""
    return feature_name.startswith(ODDS_FEATURE_PREFIXES)


def step_a_correlation_analysis(X, threshold=0.9, forced_keep=None):
    """
    Step A: 相关性分析
    - 计算 Pearson 相关系数矩阵
    - 识别 |corr|>threshold 的高相关特征对
    - 剔除每对中重要性较低的特征（此处先按字母顺序保留第一个，后续由 XGB 重要性修正）

    返回：
        eliminated: list of dict, 每个被剔除特征的详细信息
        remaining_features: list, 剩余特征名
    """
    if forced_keep is None:
        forced_keep = set()

    log_subsection(f"Step A: 相关性分析（阈值={threshold}）")

    # 仅对数值列计算相关性
    numeric_X = X.select_dtypes(include=[np.number])
    corr_matrix = numeric_X.corr(method='pearson').abs()

    # 提取上三角矩阵（避免重复对）
    upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))

    # 找出高相关特征对
    high_corr_pairs = []
    for col in upper.columns:
        high_corr = upper[col][upper[col] > threshold]
        for idx, val in high_corr.items():
            high_corr_pairs.append({
                'feature_a': col,
                'feature_b': idx,
                'correlation': float(val),
            })

    print(f"    发现 {len(high_corr_pairs)} 对高相关特征（|corr| > {threshold}）")

    # 决定剔除哪个：保留 forced_keep 中的特征，否则保留字母序较小的
    eliminated = []
    to_drop = set()

    for pair in high_corr_pairs:
        a, b = pair['feature_a'], pair['feature_b']

        # 两个都在强制保留中：跳过（仅记录警告）
        if a in forced_keep and b in forced_keep:
            print(f"    ⚠️  强制保留对均高相关: {a} ↔ {b} (corr={pair['correlation']:.4f})，保留两者")
            continue

        # 一个在强制保留中：剔除另一个
        if a in forced_keep:
            drop_feat, keep_feat = b, a
        elif b in forced_keep:
            drop_feat, keep_feat = a, b
        else:
            # 两者都可剔除：保留字母序较小的（后续 XGB 重要性会进一步筛选）
            drop_feat, keep_feat = (b, a) if a < b else (a, b)

        if drop_feat in to_drop:
            continue

        to_drop.add(drop_feat)
        eliminated.append({
            'feature': drop_feat,
            'method': 'correlation',
            'reason': f"与 {keep_feat} 高相关 (|corr|={pair['correlation']:.4f} > {threshold})",
            'details': {
                'paired_with': keep_feat,
                'correlation': pair['correlation'],
                'threshold': threshold,
                'forced_keep': keep_feat in forced_keep,
            }
        })
        print(f"    ❌ 剔除 {drop_feat:30s} ↔ 保留 {keep_feat:30s} (corr={pair['correlation']:.4f})")

    remaining_features = [f for f in X.columns if f not in to_drop]
    print(f"    相关性分析结果: 剔除 {len(to_drop)} 个，剩余 {len(remaining_features)} 个")

    return eliminated, remaining_features, high_corr_pairs


def step_b_xgboost_importance(X, y, top_k=None):
    """
    Step B: XGBoost 特征重要性（gain + weight 双指标）

    修复：使用 model.feature_importances_（sklearn API 标准）+ booster.get_score()
    双通道获取，并正确处理特征名映射（原始列名 vs f0/f1 格式）。

    返回：
        importance_df: DataFrame, 包含 feature/gain/gain_rank/weight/weight_rank/avg_rank
        eliminated: list of dict, 低重要性特征（按 top_k 截断）
    """
    log_subsection("Step B: XGBoost 特征重要性（gain + weight）")

    from xgboost import XGBClassifier

    # 使用与 train_models_v2.py 一致的参数
    model = XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=1.0,
        reg_lambda=5.0,
        gamma=0.1,
        min_child_weight=5,
        tree_method='hist',
        random_state=42,
        verbosity=0,
        n_jobs=-1,
    )

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        model.fit(X, y)

    # === 通道1: sklearn API 的 feature_importances_（gain 归一化） ===
    sklearn_gain = np.asarray(model.feature_importances_, dtype=float)

    # === 通道2: booster.get_score()（原始 gain + weight） ===
    booster = model.get_booster()
    gain_imp_raw = booster.get_score(importance_type='gain')
    weight_imp_raw = booster.get_score(importance_type='weight')

    feature_names = list(X.columns)
    f_map = {f'f{i}': name for i, name in enumerate(feature_names)}

    # get_score() 的键可能是原始列名（sklearn API）或 f0/f1（原生 API）
    # 统一映射到原始特征名
    def _lookup(name, raw_dict):
        # 直接命中
        if name in raw_dict:
            return float(raw_dict[name])
        # f-index 命中
        for i, fn in enumerate(feature_names):
            if fn == name:
                key = f'f{i}'
                if key in raw_dict:
                    return float(raw_dict[key])
        return 0.0

    rows = []
    for i, name in enumerate(feature_names):
        # 优先用 sklearn 的 gain（更可靠），回退到 booster 的 gain
        gain_val = sklearn_gain[i] if sklearn_gain[i] > 0 else _lookup(name, gain_imp_raw)
        weight_val = _lookup(name, weight_imp_raw)
        rows.append({
            'feature': name,
            'gain': float(gain_val),
            'weight': float(weight_val),
            'is_odds': is_odds_feature(name),
        })

    df = pd.DataFrame(rows)

    # 诊断：打印非零特征数
    n_nonzero_gain = int((df['gain'] > 0).sum())
    n_nonzero_weight = int((df['weight'] > 0).sum())
    print(f"    诊断: gain>0 的特征 {n_nonzero_gain}/{len(df)}, weight>0 的特征 {n_nonzero_weight}/{len(df)}")

    if n_nonzero_gain == 0:
        # 极端情况：所有特征 gain=0，可能是模型未学到任何东西
        # 回退到使用方差作为重要性（至少能区分常数特征）
        print(f"    ⚠️  所有特征 gain=0，回退到方差作为重要性代理")
        var_series = X.var().fillna(0).reindex(df['feature'].values).fillna(0)
        df['gain'] = var_series.values

    # 排名（1=最重要）
    df['gain_rank'] = df['gain'].rank(ascending=False, method='min').astype(int)
    df['weight_rank'] = df['weight'].rank(ascending=False, method='min').astype(int)
    df['avg_rank'] = ((df['gain_rank'] + df['weight_rank']) / 2).round(2)
    df = df.sort_values('avg_rank').reset_index(drop=True)

    print(f"\n    Top 10 特征（按 avg_rank）:")
    for _, r in df.head(10).iterrows():
        tag = "[赔率]" if r['is_odds'] else "[基础]"
        print(f"      {r['feature']:30s} {tag}  gain={r['gain']:.4f}  weight={r['weight']:.0f}  avg_rank={r['avg_rank']}")

    print(f"\n    末位 10 特征（候选剔除）:")
    for _, r in df.tail(10).iterrows():
        tag = "[赔率]" if r['is_odds'] else "[基础]"
        print(f"      {r['feature']:30s} {tag}  gain={r['gain']:.4f}  weight={r['weight']:.0f}  avg_rank={r['avg_rank']}")

    # 低重要性特征（如果指定 top_k，则 rank > top_k 的为候选剔除）
    eliminated = []
    if top_k is not None and top_k < len(df):
        low_imp = df[df['avg_rank'] > top_k]
        for _, r in low_imp.iterrows():
            eliminated.append({
                'feature': r['feature'],
                'method': 'xgboost_importance',
                'reason': f"低重要性 (avg_rank={r['avg_rank']:.1f} > {top_k}, gain={r['gain']:.4f}, weight={r['weight']:.0f})",
                'details': {
                    'gain': float(r['gain']),
                    'weight': float(r['weight']),
                    'gain_rank': int(r['gain_rank']),
                    'weight_rank': int(r['weight_rank']),
                    'avg_rank': float(r['avg_rank']),
                    'is_odds_feature': bool(r['is_odds']),
                }
            })

    return df, eliminated


def step_c_rfe(X, y, n_features_to_select=65, step=5):
    """
    Step C: RFE 递归特征消除
    - 基于 XGBClassifier
    - 每轮剔除 step 个最不重要特征

    返回：
        selected_features: list, RFE 选中的特征
        eliminated: list of dict, RFE 剔除的特征
        ranking: array, RFE 排名（1=选中）
    """
    log_subsection(f"Step C: RFE 递归特征消除（目标={n_features_to_select}, step={step}）")

    from xgboost import XGBClassifier

    estimator = XGBClassifier(
        n_estimators=100,  # RFE 内部训练，减少估计器数量加速
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=1.0,
        reg_lambda=5.0,
        gamma=0.1,
        tree_method='hist',
        random_state=42,
        verbosity=0,
        n_jobs=-1,
    )

    rfe = RFE(estimator=estimator, n_features_to_select=n_features_to_select, step=step)

    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        rfe.fit(X, y)

    selected_mask = rfe.support_
    ranking = rfe.ranking_

    feature_names = list(X.columns)
    selected_features = [f for f, s in zip(feature_names, selected_mask) if s]
    eliminated_features = [f for f, s in zip(feature_names, selected_mask) if not s]

    print(f"    RFE 选中: {len(selected_features)} 个，剔除: {len(eliminated_features)} 个")

    # 记录每个被剔除特征的 RFE 排名
    eliminated = []
    feat_to_rank = {f: int(r) for f, r in zip(feature_names, ranking)}
    for f in eliminated_features:
        eliminated.append({
            'feature': f,
            'method': 'rfe',
            'reason': f"RFE 排名 {feat_to_rank[f]}（>{n_features_to_select}，未被选中）",
            'details': {
                'rfe_ranking': feat_to_rank[f],
                'n_features_to_select': n_features_to_select,
                'is_odds_feature': is_odds_feature(f),
            }
        })

    # 打印被剔除的赔率特征（这些会被强制保留）
    rfe_eliminated_odds = [f for f in eliminated_features if is_odds_feature(f)]
    if rfe_eliminated_odds:
        print(f"    ⚠️  RFE 剔除了 {len(rfe_eliminated_odds)} 个赔率特征（将强制保留）:")
        for f in rfe_eliminated_odds:
            print(f"       {f:30s} (RFE rank={feat_to_rank[f]})")

    return selected_features, eliminated, ranking


def aggregate_selection(X, y, target_features=65, corr_threshold=0.9):
    """
    综合三种方法进行特征选择：
    1. 强制保留所有赔率特征（D-011 要求保留最强预测信号）
    2. 相关性分析剔除高相关对
    3. XGBoost 重要性排序
    4. RFE 验证

    最终选择策略：
    - 所有赔率特征强制保留（即使相关性高也保留 gain 更高的一个）
    - 剩余配额从基础/球队特征中按 XGB avg_rank 升序选取
    - 如果总特征数 > target_features，则从基础特征中剔除最低 rank 的

    返回：
        final_selected: list, 最终选中的特征
        elimination_log: list of dict, 完整剔除日志
        importance_df: DataFrame, XGB 重要性
        corr_pairs: list, 高相关对
    """
    log_section("D-011 特征选择与降维 - 综合分析")

    feature_names = list(X.columns)
    n_total = len(feature_names)
    odds_features = [f for f in feature_names if is_odds_feature(f)]
    non_odds_features = [f for f in feature_names if not is_odds_feature(f)]

    print(f"  总特征数: {n_total}")
    print(f"  赔率特征（强制保留）: {len(odds_features)}")
    print(f"  基础/球队特征（参与选择）: {len(non_odds_features)}")

    forced_keep = set(odds_features)

    # === Step A: 相关性分析 ===
    corr_eliminated, remaining_after_corr, corr_pairs = step_a_correlation_analysis(
        X, threshold=corr_threshold, forced_keep=forced_keep
    )

    # === Step B: XGBoost 重要性 ===
    importance_df, _ = step_b_xgboost_importance(X, y, top_k=None)

    # === Step C: RFE 验证（目标稍高于最终目标，留出赔率特征配额） ===
    # RFE 目标：target_features - 已强制保留的赔率特征数 + 一些 buffer
    # 但 RFE 会强制剔除一些赔率特征，这些会被 forced_keep 覆盖
    rfe_target = max(target_features, len(odds_features) + 10)
    rfe_selected, rfe_eliminated, rfe_ranking = step_c_rfe(
        X, y, n_features_to_select=rfe_target, step=5
    )

    # === 综合决策：最终特征集 ===
    log_subsection("综合决策：最终特征集")

    # 策略：
    # 1. 强制保留所有赔率特征
    # 2. 从非赔率特征中，按 XGB avg_rank 升序选取，跳过已被相关性分析剔除的
    # 3. 直到总数达到 target_features
    final_selected = list(odds_features)  # 强制保留
    remaining_quota = target_features - len(odds_features)

    if remaining_quota <= 0:
        print(f"    ⚠️  赔率特征 {len(odds_features)} 已超过目标 {target_features}，仅保留赔率特征")
    else:
        # 非赔率特征按 avg_rank 升序排序
        non_odds_imp = importance_df[~importance_df['is_odds']].sort_values('avg_rank')
        # 跳过被相关性分析剔除的（除非被强制保留，但这里都是非赔率特征）
        corr_eliminated_set = {e['feature'] for e in corr_eliminated}

        for _, r in non_odds_imp.iterrows():
            if len(final_selected) >= target_features:
                break
            feat = r['feature']
            if feat in corr_eliminated_set:
                # 已被相关性分析剔除，跳过
                continue
            final_selected.append(feat)

    final_selected_set = set(final_selected)
    n_final = len(final_selected)
    print(f"    最终选中: {n_final} 个特征（目标 {target_features}）")
    print(f"      - 赔率特征: {len([f for f in final_selected if is_odds_feature(f)])}")
    print(f"      - 基础/球队特征: {len([f for f in final_selected if not is_odds_feature(f)])}")

    # === 生成完整剔除日志 ===
    log_subsection("剔除日志汇总")
    elimination_log = []

    # 1. 相关性分析剔除
    for e in corr_eliminated:
        elimination_log.append(e)

    # 2. 低重要性剔除（未被选中且未被相关性分析剔除的非赔率特征）
    corr_eliminated_set = {e['feature'] for e in corr_eliminated}
    for _, r in importance_df.iterrows():
        feat = r['feature']
        if feat in final_selected_set:
            continue
        if feat in corr_eliminated_set:
            continue
        if is_odds_feature(feat):
            # 赔率特征强制保留，不应出现在剔除列表
            continue
        elimination_log.append({
            'feature': feat,
            'method': 'xgboost_importance',
            'reason': f"低重要性 (avg_rank={r['avg_rank']:.1f}, gain={r['gain']:.4f}, weight={r['weight']:.0f})",
            'details': {
                'gain': float(r['gain']),
                'weight': float(r['weight']),
                'gain_rank': int(r['gain_rank']),
                'weight_rank': int(r['weight_rank']),
                'avg_rank': float(r['avg_rank']),
                'is_odds_feature': False,
            }
        })

    # 打印剔除日志
    print(f"\n    共剔除 {len(elimination_log)} 个特征:")
    print(f"    {'特征名':30s} {'方法':20s} {'原因'}")
    print(f"    {'-'*30} {'-'*20} {'-'*60}")
    for e in elimination_log:
        print(f"    {e['feature']:30s} {e['method']:20s} {e['reason']}")

    return final_selected, elimination_log, importance_df, corr_pairs


def save_reports(final_selected, elimination_log, importance_df, corr_pairs,
                 X_original, timestamp, target_features):
    """保存特征选择报告"""
    reports_dir = os.path.join(PROJECT_ROOT, 'reports')
    os.makedirs(reports_dir, exist_ok=True)

    # 1. 选中特征列表（JSON）
    selected_path = os.path.join(reports_dir, f'd011_selected_features_{timestamp}.json')
    selected_data = {
        'timestamp': timestamp,
        'target_features': target_features,
        'actual_features': len(final_selected),
        'selected_features': final_selected,
        'odds_features': [f for f in final_selected if is_odds_feature(f)],
        'basic_features': [f for f in final_selected if not is_odds_feature(f)],
        'stats': {
            'total_before': X_original.shape[1],
            'total_after': len(final_selected),
            'eliminated_count': len(elimination_log),
            'reduction_ratio': round(1 - len(final_selected) / X_original.shape[1], 4),
        }
    }
    with open(selected_path, 'w', encoding='utf-8') as f:
        json.dump(selected_data, f, ensure_ascii=False, indent=2)
    print(f"\n  💾 选中特征已保存: {selected_path}")

    # 2. 剔除日志（CSV）
    elim_path = os.path.join(reports_dir, f'd011_elimination_log_{timestamp}.csv')
    elim_rows = []
    for e in elimination_log:
        row = {
            'feature': e['feature'],
            'method': e['method'],
            'reason': e['reason'],
        }
        for k, v in e.get('details', {}).items():
            row[f'detail_{k}'] = v
        elim_rows.append(row)
    pd.DataFrame(elim_rows).to_csv(elim_path, index=False, encoding='utf-8-sig')
    print(f"  💾 剔除日志已保存: {elim_path}")

    # 3. 完整报告（JSON）
    report_path = os.path.join(reports_dir, f'd011_feature_selection_report_{timestamp}.json')
    report = {
        'timestamp': timestamp,
        'task': 'D-011 特征选择与降维',
        'input_features': X_original.shape[1],
        'target_features': target_features,
        'output_features': len(final_selected),
        'reduction_ratio': round(1 - len(final_selected) / X_original.shape[1], 4),
        'methods': {
            'correlation_threshold': 0.9,
            'xgboost_importance_type': 'gain+weight',
            'rfe_enabled': True,
        },
        'high_correlation_pairs': corr_pairs,
        'elimination_log': elimination_log,
        'importance_top20': importance_df.head(20)[
            ['feature', 'gain', 'weight', 'gain_rank', 'weight_rank', 'avg_rank', 'is_odds']
        ].to_dict(orient='records'),
        'importance_bottom20': importance_df.tail(20)[
            ['feature', 'gain', 'weight', 'gain_rank', 'weight_rank', 'avg_rank', 'is_odds']
        ].to_dict(orient='records'),
        'selected_features': final_selected,
    }
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"  💾 完整报告已保存: {report_path}")

    # 4. 同时保存一个稳定的最新版本（无时间戳），便于 train_models_v2.py 读取
    latest_path = os.path.join(reports_dir, 'd011_selected_features_latest.json')
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(selected_data, f, ensure_ascii=False, indent=2)
    print(f"  💾 最新版本（稳定路径）: {latest_path}")

    return selected_path, elim_path, report_path, latest_path


def main():
    parser = argparse.ArgumentParser(description='D-011 特征选择与降维')
    parser.add_argument('--target-features', type=int, default=80,
                        help='目标特征数（默认80，范围60-100）')
    parser.add_argument('--corr-threshold', type=float, default=0.9,
                        help='相关性阈值（默认0.9）')
    args = parser.parse_args()

    if not (60 <= args.target_features <= 100):
        print(f"⚠️  target-features 应在 60-100 范围内，当前: {args.target_features}")
        print(f"    已自动调整为 80")
        args.target_features = 80

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    log_section("D-011 特征选择与降维")
    print(f"  时间: {timestamp}")
    print(f"  目标特征数: {args.target_features}")
    print(f"  相关性阈值: {args.corr_threshold}")

    # === Step 1: 加载数据并构建特征 ===
    log_section("Step 1: 加载数据并构建特征矩阵")
    df = load_match_data_odds()
    X, y = build_all_features(df, include_odds=True, include_elo=True, include_temporal=True,
                             include_score=True, include_nonlinear=True)
    print(f"  原始特征矩阵: {X.shape}")

    # D-009 泄露检测
    detection = detect_leakage(X)
    X = feature_temporal_split(X, verbose=False)
    validate_no_leakage(X, context="D-011 特征选择前")
    print(f"  泄露检测后特征矩阵: {X.shape}")
    print(f"  泄露特征: {len(detection['leakage_features'])} 个")

    X_original = X.copy()

    # === Step 2: 综合特征选择 ===
    final_selected, elimination_log, importance_df, corr_pairs = aggregate_selection(
        X, y, target_features=args.target_features, corr_threshold=args.corr_threshold
    )

    # === Step 3: 保存报告 ===
    log_section("Step 3: 保存报告")
    selected_path, elim_path, report_path, latest_path = save_reports(
        final_selected, elimination_log, importance_df, corr_pairs,
        X_original, timestamp, args.target_features
    )

    # === 总结 ===
    log_section("D-011 完成")
    print(f"  输入特征: {X_original.shape[1]} 维")
    print(f"  输出特征: {len(final_selected)} 维")
    print(f"  剔除数量: {len(elimination_log)} 个")
    print(f"  降维比例: {round(1 - len(final_selected) / X_original.shape[1], 4) * 100}%")
    print(f"\n  下一步:")
    print(f"    1. 运行验证脚本: python scripts/validate_d011_feature_selection.py")
    print(f"    2. 在 train_models_v2.py 中集成 D-011 特征过滤")

    return final_selected, elimination_log


if __name__ == '__main__':
    main()
