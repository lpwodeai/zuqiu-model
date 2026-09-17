"""
D-017 特征精简：从85维降至60维核心集
======================================

策略：
1. 加载 Optuna 最优 LGB 参数，训练全量模型提取 feature_importance
2. 保留强制特征：Elo(10维) + Temporal(10维) = 20维
3. 从剩余 65 维（66赔率中65+19基础中的19... 实际是75维）按重要性排序
4. 批次削减：每删5维验证5折CV，记录性能变化
5. 确定目标60维（保留全部20维强制 + 精选40维）
"""

import os
import sys
import json
import time
import pickle
import warnings
import glob
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss
from lightgbm import LGBMClassifier

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))
from feature_utils import load_match_data_odds, build_all_features

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports')
ASSETS_DIR = os.path.join(PROJECT_ROOT, 'assets')

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

N_SPLITS = 5
RANDOM_SEED = 42


def load_optuna_best():
    pattern = os.path.join(ASSETS_DIR, 'optuna_result_*.json')
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError("No Optuna result found. Run optuna_tuning.py first.")
    latest = max(files, key=os.path.getctime)
    with open(latest, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_and_prepare_features():
    print("  📊 加载比赛数据...")
    df = load_match_data_odds()
    print(f"     {len(df)} 场比赛")

    print("  🔧 构建特征 (D-009~D-013)...")
    X, y = build_all_features(df, include_odds=True, include_elo=True, include_temporal=True)
    print(f"     全量特征: {X.shape[1]} 维, {len(y)} 样本")

    d011_path = os.path.join(REPORTS_DIR, 'd011_selected_features_latest.json')
    if os.path.exists(d011_path):
        with open(d011_path, 'r', encoding='utf-8') as f:
            d011_data = json.load(f)
        selected = d011_data['selected_features']
        elo_keep = [c for c in X.columns if c.startswith(('home_elo', 'away_elo', 'elo_'))]
        temporal_keep = [c for c in X.columns if c.startswith((
            'wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility',
            'wdl_win_acceleration', 'wdl_late_trend', 'wdl_early_trend',
            'wdl_mid_stability', 'wdl_sudden_jump', 'wdl_update_frequency',
            'wdl_total_change',
        ))]
        available = list(set([f for f in selected if f in X.columns] + elo_keep + temporal_keep))
        X = X[available]
        print(f"     D-011 降维后: {X.shape[1]} 维")

    return X, y, df


def get_forced_features(X):
    """获取强制保留的特征"""
    elo_features = [c for c in X.columns if c.startswith(('home_elo', 'away_elo', 'elo_'))]
    temporal_features = [c for c in X.columns if c.startswith((
        'wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility',
        'wdl_win_acceleration', 'wdl_late_trend', 'wdl_early_trend',
        'wdl_mid_stability', 'wdl_sudden_jump', 'wdl_update_frequency',
        'wdl_total_change',
    ))]
    forced = elo_features + temporal_features
    print(f"     强制特征: {len(forced)} 维 (Elo={len(elo_features)}, Temporal={len(temporal_features)})")
    return forced


def compute_sample_weights(df_meta):
    weights = np.ones(len(df_meta))
    if 'competition_name' in df_meta.columns:
        league_counts = df_meta['competition_name'].value_counts()
        total = len(df_meta)
        for league, count in league_counts.items():
            mask = df_meta['competition_name'] == league
            weights[mask] = total / (len(league_counts) * count)
    return weights


def evaluate_cv(X_subset, y, params, sample_weights, label=""):
    """评估特征子集的CV性能"""
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    cv_accs = []
    cv_lls = []

    for train_idx, val_idx in tscv.split(X_subset):
        X_tr, X_va = X_subset.iloc[train_idx], X_subset.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        w_tr = sample_weights[train_idx]

        model = LGBMClassifier(**params)
        model.fit(
            X_tr, y_tr, sample_weight=w_tr,
            eval_set=[(X_va, y_va)], callbacks=[],
        )

        preds = model.predict(X_va)
        acc = accuracy_score(y_va, preds)
        cv_accs.append(acc)

        proba = model.predict_proba(X_va)
        ll = log_loss(y_va, proba, labels=[0, 1, 2])
        cv_lls.append(ll)

    return {
        'accuracy_mean': float(np.mean(cv_accs)),
        'accuracy_std': float(np.std(cv_accs)),
        'logloss_mean': float(np.mean(cv_lls)),
        'cv_accuracies': [float(a) for a in cv_accs],
    }


def run_feature_selection():
    """执行特征精简主流程"""
    optuna_result = load_optuna_best()
    lgb_params = optuna_result['lgb_best']

    print("\n" + "=" * 70)
    print("📊 D-017 特征精简")
    print("=" * 70)

    print("\n  🔍 Step 1: 加载数据和特征...")
    X, y, df_meta = load_and_prepare_features()
    sample_weights = compute_sample_weights(df_meta)
    n_total = X.shape[1]
    print(f"     起始特征: {n_total} 维")

    forced = get_forced_features(X)
    n_forced = len(forced)
    print(f"     强制保留: {n_forced} 维")

    flexible_features = [c for c in X.columns if c not in forced]
    n_flexible = len(flexible_features)
    print(f"     可削减: {n_flexible} 维")

    # Step 2: 计算特征重要性
    print("\n  🔍 Step 2: 计算 LGB 特征重要性...")
    t0 = time.time()
    model = LGBMClassifier(**lgb_params)
    model.fit(
        X, y, sample_weight=sample_weights,
        eval_set=[(X.iloc[-100:], y.iloc[-100:])], callbacks=[],
    )
    importance = model.feature_importances_
    importance_time = time.time() - t0

    feat_imp = list(zip(X.columns, importance))
    feat_imp.sort(key=lambda x: x[1], reverse=True)

    print(f"     耗时: {importance_time:.1f}s")
    print(f"     Top 10 特征:")
    for feat, imp in feat_imp[:10]:
        print(f"       {feat}: {imp:.1f}")

    print(f"     Bottom 10 特征:")
    for feat, imp in feat_imp[-10:]:
        print(f"       {feat}: {imp:.1f}")

    # Step 3: 全量基准验证
    print("\n  🔍 Step 3: 全量基准CV验证...")
    baseline = evaluate_cv(X, y, lgb_params, sample_weights, "全量85维")
    print(f"     全量 {n_total} 维: CV={baseline['accuracy_mean']:.4f}±{baseline['accuracy_std']:.4f}, LL={baseline['logloss_mean']:.4f}")

    # Step 4: 特征重要性排序
    print("\n  🔍 Step 4: 按重要性排序可削减特征...")
    flexible_imp = [(f, i) for f, i in feat_imp if f not in forced]
    flexible_imp.sort(key=lambda x: x[1], reverse=True)

    # Step 5: 批次削减验证
    print("\n  🔍 Step 5: 批次削减验证 (每批5维)...")
    print(f"     {'维度':<8} {'CV准确率':<12} {'标准差':<10} {'LogLoss':<10} {'Δ vs基线':<12}")
    print("     " + "-" * 52)

    results_log = []
    current_features = list(X.columns)

    target_dim = 60
    min_flexible_keep = target_dim - n_forced

    while len(current_features) > target_dim:
        n_current = len(current_features)

        if n_current <= target_dim:
            break

        n_to_remove = min(5, n_current - target_dim)
        remove_candidates = [f for f, _ in flexible_imp if f in current_features and f not in forced][-n_to_remove:]

        if not remove_candidates:
            break

        for feat in remove_candidates:
            if feat in current_features:
                current_features.remove(feat)

        X_subset = X[current_features]
        result = evaluate_cv(X_subset, y, lgb_params, sample_weights, f"{len(current_features)}维")

        delta = result['accuracy_mean'] - baseline['accuracy_mean']
        marker = " ⚠️" if delta < -0.005 else " ✅" if delta >= 0 else ""

        print(f"     {len(current_features):<8} {result['accuracy_mean']:.4f}     {result['accuracy_std']:.4f}   {result['logloss_mean']:.4f}   {delta:+.4f}{marker}")

        results_log.append({
            'n_features': len(current_features),
            'accuracy': result['accuracy_mean'],
            'std': result['accuracy_std'],
            'logloss': result['logloss_mean'],
            'delta': delta,
            'removed': remove_candidates,
            'features': list(current_features),
        })
    # Step 6: 选择最佳子集
    print("\n  🔍 Step 6: 确定最终特征集...")
    # 用户目标：降至60维核心集；若60维结果可用则采用，否则选最佳
    target_result = None
    for r in results_log:
        if r['n_features'] == target_dim:
            target_result = r
            break

    if target_result is not None:
        best = target_result
        print(f"     采用用户目标 {target_dim} 维方案")
    else:
        best = max(results_log, key=lambda r: r['accuracy'] - abs(r['delta']) * 2 if r['delta'] < 0 else r['accuracy'])
        print(f"     采用自动最佳 {best['n_features']} 维方案")

    final_features = best['features']
    final_result = {
        'accuracy_mean': best['accuracy'],
        'accuracy_std': best['std'],
        'logloss_mean': best['logloss'],
        'features': final_features,
    }

    delta_final = final_result['accuracy_mean'] - baseline['accuracy_mean']
    print(f"     选定 {len(final_features)} 维 (CV={final_result['accuracy_mean']:.4f}±{final_result['accuracy_std']:.4f}, Δ={delta_final:+.4f})")

    # Step 7: 分析特征组成
    print(f"\n     最终特征组成 ({len(final_features)} 维):")
    temporal_prefixes = (
        'wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility',
        'wdl_win_acceleration', 'wdl_late_trend', 'wdl_early_trend',
        'wdl_mid_stability', 'wdl_sudden_jump', 'wdl_update_frequency',
        'wdl_total_change',
    )
    elo_count = len([f for f in final_features if f.startswith(('home_elo', 'away_elo', 'elo_'))])
    temporal_count = len([f for f in final_features if f.startswith(temporal_prefixes)])
    forced_total = elo_count + temporal_count
    flexible_final = len(final_features) - forced_total
    print(f"       - 强制保留特征: {forced_total} 维 (Elo={elo_count}, Temporal={temporal_count})")
    print(f"       - 精选保留特征: {flexible_final} 维 (赔率+基础)")

    # Step 8: 保存结果
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_path = os.path.join(ASSETS_DIR, f'd017_features_{ts}.json')
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump({
            'baseline': {
                'n_features': n_total,
                'accuracy': baseline['accuracy_mean'],
                'std': baseline['accuracy_std'],
                'logloss': baseline['logloss_mean'],
            },
            'final': {
                'n_features': len(final_features),
                'accuracy': final_result['accuracy_mean'],
                'std': final_result['accuracy_std'],
                'logloss': final_result['logloss_mean'],
                'features': final_features,
                'composition': {
                    'forced': forced_total,
                    'elo': elo_count,
                    'temporal': temporal_count,
                    'flexible': flexible_final,
                },
            },
            'results_log': results_log,
            'lgb_params': lgb_params,
        }, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n     💾 结果已保存: {save_path}")

    return {
        'baseline': baseline,
        'final': final_result,
        'results_log': results_log,
        'final_features': final_features,
    }


if __name__ == '__main__':
    result = run_feature_selection()

    print("\n" + "=" * 70)
    print("📊 D-017 特征精简结果")
    print("=" * 70)
    bl = result['baseline']
    fn = result['final']
    print(f"基准:  {bl['accuracy_mean']:.4f} ({bl['accuracy_mean']*100:.2f}%) @ 85维")
    print(f"最终:  {fn['accuracy_mean']:.4f} ({fn['accuracy_mean']*100:.2f}%) @ {len(result['final_features'])}维")
    delta = fn['accuracy_mean'] - bl['accuracy_mean']
    print(f"变化:  {delta:+.4f} ({delta*100:+.2f}%)")
    print(f"LogLoss变化: {fn['logloss_mean']:.4f} → {fn['logloss_mean']:.4f}")
