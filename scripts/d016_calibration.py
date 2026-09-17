"""
D-016 概率校准：Platt Scaling
================================

在 D-017 最终60维特征集上，使用 CalibratedClassifierCV 进行概率校准。

策略：
- method='sigmoid' 即 Platt Scaling (二分类逻辑回归拟合概率)
- method='isotonic' 即保序回归 (非参数化)
- cv=TimeSeriesSplit(5) 保持时序交叉验证
- 评估指标：accuracy (argmax不变)、log_loss、Brier score

注意：
- 3分类场景下 Platt Scaling 通过一对一/一对多分解应用
- 校准通常不提升 accuracy (argmax不变)，但显著改善 log_loss 和 Brier score
"""

import os
import sys
import json
import time
import glob
import pickle
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from sklearn.calibration import CalibratedClassifierCV
from sklearn.preprocessing import label_binarize
from lightgbm import LGBMClassifier
from xgboost import XGBClassifier

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(__file__))
from feature_utils import load_match_data_odds, build_all_features

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports')
ASSETS_DIR = os.path.join(PROJECT_ROOT, 'assets')

N_SPLITS = 5
RANDOM_SEED = 42


def load_optuna_best():
    pattern = os.path.join(ASSETS_DIR, 'optuna_result_*.json')
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError("No Optuna result found.")
    latest = max(files, key=os.path.getctime)
    with open(latest, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_d017_features():
    """加载 D-017 最终60维特征列表"""
    pattern = os.path.join(ASSETS_DIR, 'd017_features_*.json')
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError("No D-017 result found. Run d017_feature_selection.py first.")
    latest = max(files, key=os.path.getctime)
    with open(latest, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data['final']['features'], data


def load_and_prepare_features():
    print("  📊 加载比赛数据...")
    df = load_match_data_odds()
    print(f"     {len(df)} 场比赛")

    print("  🔧 构建特征 (D-009~D-013)...")
    X, y = build_all_features(df, include_odds=True, include_elo=True, include_temporal=True)
    print(f"     全量特征: {X.shape[1]} 维")

    final_features, d017_data = load_d017_features()
    available = [f for f in final_features if f in X.columns]
    missing = [f for f in final_features if f not in X.columns]
    if missing:
        print(f"     ⚠️ 缺失特征 {len(missing)} 个: {missing[:5]}...")
    X = X[available]
    print(f"     D-017 最终特征: {X.shape[1]} 维")

    return X, y, df


def compute_sample_weights(df_meta):
    weights = np.ones(len(df_meta))
    if 'competition_name' in df_meta.columns:
        league_counts = df_meta['competition_name'].value_counts()
        total = len(df_meta)
        for league, count in league_counts.items():
            mask = df_meta['competition_name'] == league
            weights[mask] = total / (len(league_counts) * count)
    return weights


def compute_multiclass_brier(y_true, y_proba, n_classes=3):
    """计算多分类 Brier score (取平均)"""
    y_onehot = label_binarize(y_true, classes=list(range(n_classes)))
    brier_scores = []
    for cls in range(n_classes):
        brier = brier_score_loss(y_onehot[:, cls], y_proba[:, cls])
        brier_scores.append(brier)
    return float(np.mean(brier_scores))


def evaluate_uncalibrated_cv(X, y, params, sample_weights, model_name='lgb'):
    """评估未校准模型的CV性能"""
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    cv_accs, cv_lls, cv_briers = [], [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        w_tr = sample_weights[train_idx]

        if model_name == 'lgb':
            model = LGBMClassifier(**params)
            model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], callbacks=[])
        else:
            model = XGBClassifier(**params)
            model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], verbose=False)

        preds = model.predict(X_va)
        proba = model.predict_proba(X_va)

        cv_accs.append(accuracy_score(y_va, preds))
        cv_lls.append(log_loss(y_va, proba, labels=[0, 1, 2]))
        cv_briers.append(compute_multiclass_brier(y_va.values, proba))

    return {
        'accuracy_mean': float(np.mean(cv_accs)),
        'accuracy_std': float(np.std(cv_accs)),
        'logloss_mean': float(np.mean(cv_lls)),
        'brier_mean': float(np.mean(cv_briers)),
        'cv_accuracies': [float(a) for a in cv_accs],
    }


def evaluate_calibrated_cv(X, y, params, sample_weights, method, model_name='lgb'):
    """评估校准后模型的CV性能"""
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    cv_accs, cv_lls, cv_briers = [], [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        w_tr = sample_weights[train_idx]

        if model_name == 'lgb':
            base = LGBMClassifier(**params)
        else:
            base = XGBClassifier(**params)

        # CalibratedClassifierCV 内部会做 CV 训练 base 模型并拟合校准器
        # 注意：sample_weight 需通过 fit_params 传递
        calibrated = CalibratedClassifierCV(
            estimator=base,
            method=method,
            cv=TimeSeriesSplit(n_splits=3),
        )

        try:
            calibrated.fit(X_tr, y_tr, sample_weight=w_tr)
        except (TypeError, ValueError):
            calibrated.fit(X_tr, y_tr)

        preds = calibrated.predict(X_va)
        proba = calibrated.predict_proba(X_va)

        cv_accs.append(accuracy_score(y_va, preds))
        cv_lls.append(log_loss(y_va, proba, labels=[0, 1, 2]))
        cv_briers.append(compute_multiclass_brier(y_va.values, proba))

    return {
        'accuracy_mean': float(np.mean(cv_accs)),
        'accuracy_std': float(np.std(cv_accs)),
        'logloss_mean': float(np.mean(cv_lls)),
        'brier_mean': float(np.mean(cv_briers)),
        'cv_accuracies': [float(a) for a in cv_accs],
    }


def run_calibration():
    optuna_result = load_optuna_best()
    lgb_params = optuna_result['lgb_best']
    xgb_params = optuna_result['xgb_best']

    print("\n" + "=" * 70)
    print("📊 D-016 概率校准 (Platt Scaling & Isotonic)")
    print("=" * 70)

    print("\n  🔍 Step 1: 加载数据和 D-017 最终特征...")
    X, y, df_meta = load_and_prepare_features()
    sample_weights = compute_sample_weights(df_meta)
    print(f"     样本数: {len(y)}, 特征维度: {X.shape[1]}")

    # Step 2: 评估未校准基准
    print("\n  🔍 Step 2: 评估未校准基准模型...")
    t0 = time.time()
    baseline_lgb = evaluate_uncalibrated_cv(X, y, lgb_params, sample_weights, 'lgb')
    baseline_lgb_time = time.time() - t0
    print(f"     LGB 未校准:  CV={baseline_lgb['accuracy_mean']:.4f}±{baseline_lgb['accuracy_std']:.4f}, "
          f"LL={baseline_lgb['logloss_mean']:.4f}, Brier={baseline_lgb['brier_mean']:.4f} ({baseline_lgb_time:.1f}s)")

    t0 = time.time()
    baseline_xgb = evaluate_uncalibrated_cv(X, y, xgb_params, sample_weights, 'xgb')
    baseline_xgb_time = time.time() - t0
    print(f"     XGB 未校准:  CV={baseline_xgb['accuracy_mean']:.4f}±{baseline_xgb['accuracy_std']:.4f}, "
          f"LL={baseline_xgb['logloss_mean']:.4f}, Brier={baseline_xgb['brier_mean']:.4f} ({baseline_xgb_time:.1f}s)")

    # Step 3: Platt Scaling (sigmoid) 校准
    print("\n  🔍 Step 3: Platt Scaling (sigmoid) 校准...")
    t0 = time.time()
    platt_lgb = evaluate_calibrated_cv(X, y, lgb_params, sample_weights, 'sigmoid', 'lgb')
    platt_lgb_time = time.time() - t0
    print(f"     LGB+Platt:   CV={platt_lgb['accuracy_mean']:.4f}±{platt_lgb['accuracy_std']:.4f}, "
          f"LL={platt_lgb['logloss_mean']:.4f}, Brier={platt_lgb['brier_mean']:.4f} ({platt_lgb_time:.1f}s)")

    t0 = time.time()
    platt_xgb = evaluate_calibrated_cv(X, y, xgb_params, sample_weights, 'sigmoid', 'xgb')
    platt_xgb_time = time.time() - t0
    print(f"     XGB+Platt:   CV={platt_xgb['accuracy_mean']:.4f}±{platt_xgb['accuracy_std']:.4f}, "
          f"LL={platt_xgb['logloss_mean']:.4f}, Brier={platt_xgb['brier_mean']:.4f} ({platt_xgb_time:.1f}s)")

    # Step 4: Isotonic Regression 校准
    print("\n  🔍 Step 4: Isotonic Regression 校准...")
    t0 = time.time()
    iso_lgb = evaluate_calibrated_cv(X, y, lgb_params, sample_weights, 'isotonic', 'lgb')
    iso_lgb_time = time.time() - t0
    print(f"     LGB+Isotonic: CV={iso_lgb['accuracy_mean']:.4f}±{iso_lgb['accuracy_std']:.4f}, "
          f"LL={iso_lgb['logloss_mean']:.4f}, Brier={iso_lgb['brier_mean']:.4f} ({iso_lgb_time:.1f}s)")

    t0 = time.time()
    iso_xgb = evaluate_calibrated_cv(X, y, xgb_params, sample_weights, 'isotonic', 'xgb')
    iso_xgb_time = time.time() - t0
    print(f"     XGB+Isotonic: CV={iso_xgb['accuracy_mean']:.4f}±{iso_xgb['accuracy_std']:.4f}, "
          f"LL={iso_xgb['logloss_mean']:.4f}, Brier={iso_xgb['brier_mean']:.4f} ({iso_xgb_time:.1f}s)")

    # Step 5: 对比汇总
    print("\n  📊 校准效果对比:")
    print(f"     {'方案':<20} {'CV准确率':<14} {'LogLoss':<10} {'Brier':<10}")
    print("     " + "-" * 56)
    print(f"     {'LGB 未校准':<20} {baseline_lgb['accuracy_mean']:.4f}±{baseline_lgb['accuracy_std']:.4f} {baseline_lgb['logloss_mean']:.4f}   {baseline_lgb['brier_mean']:.4f}")
    print(f"     {'LGB + Platt':<20} {platt_lgb['accuracy_mean']:.4f}±{platt_lgb['accuracy_std']:.4f} {platt_lgb['logloss_mean']:.4f}   {platt_lgb['brier_mean']:.4f}")
    print(f"     {'LGB + Isotonic':<20} {iso_lgb['accuracy_mean']:.4f}±{iso_lgb['accuracy_std']:.4f} {iso_lgb['logloss_mean']:.4f}   {iso_lgb['brier_mean']:.4f}")
    print(f"     {'XGB 未校准':<20} {baseline_xgb['accuracy_mean']:.4f}±{baseline_xgb['accuracy_std']:.4f} {baseline_xgb['logloss_mean']:.4f}   {baseline_xgb['brier_mean']:.4f}")
    print(f"     {'XGB + Platt':<20} {platt_xgb['accuracy_mean']:.4f}±{platt_xgb['accuracy_std']:.4f} {platt_xgb['logloss_mean']:.4f}   {platt_xgb['brier_mean']:.4f}")
    print(f"     {'XGB + Isotonic':<20} {iso_xgb['accuracy_mean']:.4f}±{iso_xgb['accuracy_std']:.4f} {iso_xgb['logloss_mean']:.4f}   {iso_xgb['brier_mean']:.4f}")

    # Step 6: 计算改善幅度
    print("\n  📈 改善幅度 (相比未校准):")
    print(f"     {'方案':<20} {'Δ准确率':<12} {'ΔLogLoss':<12} {'ΔBrier':<12}")
    print("     " + "-" * 56)

    for name, base, cal in [
        ('LGB + Platt', baseline_lgb, platt_lgb),
        ('LGB + Isotonic', baseline_lgb, iso_lgb),
        ('XGB + Platt', baseline_xgb, platt_xgb),
        ('XGB + Isotonic', baseline_xgb, iso_xgb),
    ]:
        d_acc = cal['accuracy_mean'] - base['accuracy_mean']
        d_ll = cal['logloss_mean'] - base['logloss_mean']
        d_br = cal['brier_mean'] - base['brier_mean']
        print(f"     {name:<20} {d_acc:+.4f}      {d_ll:+.4f}      {d_br:+.4f}")

    # Step 7: 选出最优校准方案
    candidates = [
        ('LGB+Platt', platt_lgb, 'lgb', 'sigmoid'),
        ('LGB+Isotonic', iso_lgb, 'lgb', 'isotonic'),
        ('XGB+Platt', platt_xgb, 'xgb', 'sigmoid'),
        ('XGB+Isotonic', iso_xgb, 'xgb', 'isotonic'),
    ]
    best_name, best_result, best_model, best_method = max(candidates, key=lambda x: x[1]['logloss_mean'])

    print(f"\n  🏆 最优校准方案 (按LogLoss): {best_name}")
    print(f"     LogLoss: {best_result['logloss_mean']:.4f}")

    # Step 8: 训练最终校准模型
    print(f"\n  🔥 Step 8: 全量训练最终校准模型 ({best_name})...")
    if best_model == 'lgb':
        final_base = LGBMClassifier(**lgb_params)
    else:
        final_base = XGBClassifier(**xgb_params)

    final_calibrated = CalibratedClassifierCV(
        estimator=final_base,
        method=best_method,
        cv=TimeSeriesSplit(n_splits=N_SPLITS),
    )

    try:
        final_calibrated.fit(X, y, sample_weight=sample_weights)
    except (TypeError, ValueError):
        final_calibrated.fit(X, y)
    print(f"     最终模型训练完成")

    return {
        'baseline_lgb': baseline_lgb,
        'baseline_xgb': baseline_xgb,
        'platt_lgb': platt_lgb,
        'platt_xgb': platt_xgb,
        'iso_lgb': iso_lgb,
        'iso_xgb': iso_xgb,
        'best_name': best_name,
        'best_result': best_result,
        'final_model': final_calibrated,
        'best_method': best_method,
        'best_base_model': best_model,
    }


if __name__ == '__main__':
    result = run_calibration()

    print("\n" + "=" * 70)
    print("📊 D-016 概率校准最终结果")
    print("=" * 70)
    best = result['best_result']
    print(f"最优方案: {result['best_name']}")
    print(f"CV 准确率:  {best['accuracy_mean']:.4f} ({best['accuracy_mean']*100:.2f}%)")
    print(f"CV 标准差:  {best['accuracy_std']:.4f}")
    print(f"CV LogLoss: {best['logloss_mean']:.4f}")
    print(f"CV Brier:   {best['brier_mean']:.4f}")

    # 保存模型
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    model_path = os.path.join(ASSETS_DIR, f'd016_calibrated_{ts}.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump({
            'final_model': result['final_model'],
            'best_name': result['best_name'],
            'best_method': result['best_method'],
            'best_base_model': result['best_base_model'],
            'best_cv': best,
        }, f)
    print(f"\n💾 校准模型已保存: {model_path}")

    # 保存结果JSON
    json_path = os.path.join(ASSETS_DIR, f'd016_result_{ts}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'baseline_lgb': result['baseline_lgb'],
            'baseline_xgb': result['baseline_xgb'],
            'platt_lgb': result['platt_lgb'],
            'platt_xgb': result['platt_xgb'],
            'iso_lgb': result['iso_lgb'],
            'iso_xgb': result['iso_xgb'],
            'best_name': result['best_name'],
            'best_cv': best,
        }, f, indent=2, default=str, ensure_ascii=False)
    print(f"💾 结果JSON已保存: {json_path}")
