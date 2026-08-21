"""
阶段五：Optuna 超参数调优
==============================

使用 Optuna 对 XGBoost 和 LightGBM 进行贝叶斯优化超参数搜索，
目标：5折 TimeSeriesSplit CV 准确率提升至 52%+

搜索策略：
- XGBoost: 18 参数（max_depth, lr, n_estimators, subsample, colsample, reg_alpha, reg_lambda, gamma, min_child_weight, etc.）
- LightGBM: 18 参数（num_leaves, max_depth, lr, n_estimators, subsample, colsample, reg_alpha, reg_lambda, etc.）
- 集成权重: XGB vs LGB 动态搜索
"""

import os
import sys
import json
import time
import logging
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import optuna
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, recall_score
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier

warnings.filterwarnings('ignore')
optuna.logging.set_verbosity(optuna.logging.WARNING)

sys.path.insert(0, os.path.dirname(__file__))
from feature_utils import load_match_data_odds, build_all_features

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports')
ASSETS_DIR = os.path.join(PROJECT_ROOT, 'assets')

os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)

N_TRIALS_XGB = 30
N_TRIALS_LGB = 30
N_TRIALS_ENSEMBLE = 0  # D-015: ensemble 无增益，跳过
N_SPLITS = 5
RANDOM_SEED = 42
# DRAW_RECALL_MIN: 平局召回率下限
# 分析: 5折交叉验证中平局召回率分布在 0.07~0.38 之间，平均约 0.25
#       设为 0.20 更贴近实际可达水平，同时仍对极低召回率( < 15%)施加有效惩罚
# DRAW_RECALL_PENALTY: 惩罚强度（线性递减）
#       当 draw_recall=0.10 时, 额外扣除 (0.20-0.10)/0.20 * 0.03 = 0.015
#       当 draw_recall=0.15 时, 额外扣除 (0.20-0.15)/0.20 * 0.03 = 0.0075
#       当 draw_recall>=0.20 时, 无惩罚
DRAW_RECALL_MIN = 0.20
DRAW_RECALL_PENALTY = -0.03


def load_and_prepare_features():
    """加载数据并构建特征"""
    print("  📊 加载比赛数据...")
    df = load_match_data_odds()
    print(f"     {len(df)} 场比赛")

    print("  🔧 构建特征 (D-009~D-013)...")
    X, y = build_all_features(df, include_odds=True, include_elo=True, include_temporal=True,
                             include_score=True, include_nonlinear=True)
    print(f"     特征维度: {X.shape[1]}, 样本数: {len(y)}")

    print("  🎯 D-011 特征选择...")
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
        score_keep = [c for c in X.columns if c.startswith('score_')]
        available = list(set([f for f in selected if f in X.columns] + elo_keep + temporal_keep + score_keep))
        X = X[available]
        print(f"     降维后: {X.shape[1]} 维")
    else:
        print(f"     ⚠️ D-011 配置文件不存在，使用全部特征")

    return X, y, df


def compute_sample_weights(df_meta):
    """计算动态样本权重（联赛权重+德比权重）"""
    weights = np.ones(len(df_meta))

    if 'competition_name' in df_meta.columns:
        league_counts = df_meta['competition_name'].value_counts()
        total = len(df_meta)
        for league, count in league_counts.items():
            mask = df_meta['competition_name'] == league
            weights[mask] = total / (len(league_counts) * count)

    return weights


def compute_draw_recall_penalty(draw_recall):
    """计算平局召回率惩罚（分段非线性）
    
    策略:
    - draw_recall >= 0.20 (阈值): 无惩罚
    - 0.15 <= draw_recall < 0.20: 温和惩罚 (-0.03 * 比例)
    - draw_recall < 0.15: 严厉惩罚 (额外扣 -0.02)
    
    这样区分了"勉强可接受"和"严重失衡"两种情况
    """
    if draw_recall >= DRAW_RECALL_MIN:
        return 0.0
    
    # 基础惩罚
    penalty = DRAW_RECALL_PENALTY * (DRAW_RECALL_MIN - draw_recall) / DRAW_RECALL_MIN
    
    # 严重恶化时追加惩罚
    if draw_recall < 0.15:
        extra = 0.02 * (0.15 - draw_recall) / 0.15
        penalty -= extra
    
    return penalty


def objective_xgb(trial, X, y, sample_weights):
    """Optuna XGBoost 目标函数"""
    lr = trial.suggest_float('learning_rate', 0.03, 0.12, step=0.01)
    n_estimators = trial.suggest_int('n_estimators', 100, 500)
    params = {
        'max_depth': trial.suggest_int('max_depth', 3, 10),
        'learning_rate': lr,
        'n_estimators': n_estimators,
        'subsample': trial.suggest_float('subsample', 0.6, 1.0),
        'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
        'reg_alpha': trial.suggest_float('reg_alpha', 0.05, 10.0, log=True),   # 下界 0.05 防过拟合
        'reg_lambda': trial.suggest_float('reg_lambda', 1.0, 20.0, log=True),  # 下界 1.0 防过拟合
        'gamma': trial.suggest_float('gamma', 1.0, 5.0),                        # 下界 1.0 防过拟合
        'min_child_weight': trial.suggest_int('min_child_weight', 5, 15),      # 下界 5 防过拟合
        'max_delta_step': trial.suggest_int('max_delta_step', 0, 10),
        'scale_pos_weight': trial.suggest_float('scale_pos_weight', 0.5, 3.0),
        'random_state': RANDOM_SEED,
        'eval_metric': 'mlogloss',
        'use_label_encoder': False,
        'verbosity': 0,
    }

    if trial.number < 5 or trial.number % 10 == 0:
        print(f"  [XGBoost Trial {trial.number}] lr={lr:.3f}, n_est={n_estimators}, "
              f"max_depth={params['max_depth']}, subsample={params['subsample']:.3f}, "
              f"colsample={params['colsample_bytree']:.3f}")

    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    cv_scores = []
    cv_loglosses = []
    cv_draw_recalls = []

    for fold_idx, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        w_tr = sample_weights[train_idx]

        model = XGBClassifier(**params)
        model.fit(
            X_tr, y_tr, sample_weight=w_tr,
            eval_set=[(X_va, y_va)],
            verbose=False,
        )

        y_pred = model.predict(X_va)
        acc = accuracy_score(y_va, y_pred)
        cv_scores.append(acc)

        y_proba = model.predict_proba(X_va)
        ll = log_loss(y_va, y_proba, labels=[0, 1, 2])
        cv_loglosses.append(ll)

        per_class_recall = recall_score(y_va, y_pred, labels=[0, 1, 2], average=None)
        draw_r = per_class_recall[1]
        cv_draw_recalls.append(draw_r)

        if trial.number < 5:
            print(f"    Fold {fold_idx+1}: acc={acc:.4f}, logloss={ll:.4f}, draw_recall={draw_r:.4f}")

    mean_acc = float(np.mean(cv_scores))
    mean_draw_recall = float(np.mean(cv_draw_recalls))

    penalty = compute_draw_recall_penalty(mean_draw_recall)
    
    if penalty != 0.0 and trial.number < 10:
        print(f"  ⚠️ [XGBoost Trial {trial.number}] 平局召回率 {mean_draw_recall:.4f} < {DRAW_RECALL_MIN:.2f}, "
              f"施加惩罚 {penalty:.6f} (扣 {abs(penalty):.4f}pp)")

    adjusted_score = mean_acc + penalty

    if trial.number < 5 or trial.number % 10 == 0:
        print(f"  [XGBoost Trial {trial.number}] 汇总: acc={mean_acc:.4f}, "
              f"draw_recall={mean_draw_recall:.4f}, penalty={penalty:.6f}, "
              f"adjusted={adjusted_score:.4f}")

    trial.set_user_attr('cv_logloss_mean', float(np.mean(cv_loglosses)))
    trial.set_user_attr('cv_logloss_std', float(np.std(cv_loglosses)))
    trial.set_user_attr('cv_acc_std', float(np.std(cv_scores)))
    trial.set_user_attr('cv_draw_recall_mean', mean_draw_recall)
    trial.set_user_attr('cv_draw_recall_std', float(np.std(cv_draw_recalls)))
    trial.set_user_attr('penalty', penalty)
    trial.set_user_attr('adjusted_score', adjusted_score)

    return adjusted_score


def objective_lgb(trial, X, y, sample_weights):
    """Optuna LightGBM 目标函数"""
    lr = trial.suggest_float('learning_rate', 0.03, 0.12, step=0.01)
    n_estimators = trial.suggest_int('n_estimators', 100, 500)
    params = {
        'max_depth': trial.suggest_int('max_depth', 3, 12),
        'num_leaves': trial.suggest_int('num_leaves', 8, 255),
        'learning_rate': lr,
        'n_estimators': n_estimators,
        'subsample': trial.suggest_float('subsample', 0.6, 1.0),
        'colsample_bytree': trial.suggest_float('colsample_bytree', 0.5, 1.0),
        'reg_alpha': trial.suggest_float('reg_alpha', 0.05, 10.0, log=True),   # 下界 0.05 防过拟合
        'reg_lambda': trial.suggest_float('reg_lambda', 1.0, 20.0, log=True),  # 下界 1.0 防过拟合
        'min_child_weight': trial.suggest_int('min_child_weight', 5, 15),      # 下界 5 防过拟合
        'min_data_in_leaf': trial.suggest_int('min_data_in_leaf', 20, 50),     # 下界 20 防过拟合
        'feature_fraction': trial.suggest_float('feature_fraction', 0.5, 1.0),
        'bagging_fraction': trial.suggest_float('bagging_fraction', 0.6, 1.0),
        'bagging_freq': trial.suggest_int('bagging_freq', 1, 10),
        'random_state': RANDOM_SEED,
        'verbosity': -1,
        'objective': 'multiclass',
        'num_class': 3,
    }

    if trial.number < 5 or trial.number % 10 == 0:
        print(f"  [LightGBM Trial {trial.number}] lr={lr:.3f}, n_est={n_estimators}, "
              f"max_depth={params['max_depth']}, num_leaves={params['num_leaves']}, "
              f"subsample={params['subsample']:.3f}")

    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    cv_scores = []
    cv_loglosses = []
    cv_draw_recalls = []

    for fold_idx, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        w_tr = sample_weights[train_idx]

        model = LGBMClassifier(**params)
        model.fit(
            X_tr, y_tr, sample_weight=w_tr,
            eval_set=[(X_va, y_va)],
            callbacks=[],
        )

        y_pred = model.predict(X_va)
        acc = accuracy_score(y_va, y_pred)
        cv_scores.append(acc)

        y_proba = model.predict_proba(X_va)
        ll = log_loss(y_va, y_proba, labels=[0, 1, 2])
        cv_loglosses.append(ll)

        per_class_recall = recall_score(y_va, y_pred, labels=[0, 1, 2], average=None)
        draw_r = per_class_recall[1]
        cv_draw_recalls.append(draw_r)

        if trial.number < 5:
            print(f"    Fold {fold_idx+1}: acc={acc:.4f}, logloss={ll:.4f}, draw_recall={draw_r:.4f}")

    mean_acc = float(np.mean(cv_scores))
    mean_draw_recall = float(np.mean(cv_draw_recalls))

    penalty = compute_draw_recall_penalty(mean_draw_recall)
    
    if penalty != 0.0 and trial.number < 10:
        print(f"  ⚠️ [LightGBM Trial {trial.number}] 平局召回率 {mean_draw_recall:.4f} < {DRAW_RECALL_MIN:.2f}, "
              f"施加惩罚 {penalty:.6f} (扣 {abs(penalty):.4f}pp)")

    adjusted_score = mean_acc + penalty

    if trial.number < 5 or trial.number % 10 == 0:
        print(f"  [LightGBM Trial {trial.number}] 汇总: acc={mean_acc:.4f}, "
              f"draw_recall={mean_draw_recall:.4f}, penalty={penalty:.6f}, "
              f"adjusted={adjusted_score:.4f}")

    trial.set_user_attr('cv_logloss_mean', float(np.mean(cv_loglosses)))
    trial.set_user_attr('cv_logloss_std', float(np.std(cv_loglosses)))
    trial.set_user_attr('cv_acc_std', float(np.std(cv_scores)))
    trial.set_user_attr('cv_draw_recall_mean', mean_draw_recall)
    trial.set_user_attr('cv_draw_recall_std', float(np.std(cv_draw_recalls)))
    trial.set_user_attr('penalty', penalty)
    trial.set_user_attr('adjusted_score', adjusted_score)

    return adjusted_score


def objective_ensemble(trial, X, y, xgb_params, lgb_params, sample_weights):
    """Optuna 集成权重目标函数"""
    weight_xgb = trial.suggest_float('weight_xgb', 0.1, 0.9)
    weight_lgb = 1.0 - weight_xgb

    if weight_lgb < 0.05:
        weight_lgb = 0.05
        weight_xgb = 0.95

    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    cv_scores = []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        w_tr = sample_weights[train_idx]

        xgb = XGBClassifier(**xgb_params)
        xgb.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], verbose=False)
        lgb = LGBMClassifier(**lgb_params)
        lgb.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], callbacks=[])

        p_xgb = xgb.predict_proba(X_va)
        p_lgb = lgb.predict_proba(X_va)
        p_ens = weight_xgb * p_xgb + weight_lgb * p_lgb

        y_pred = np.argmax(p_ens, axis=1)
        acc = accuracy_score(y_va, y_pred)
        cv_scores.append(acc)

    return float(np.mean(cv_scores))


def run_optuna_tuning(X, y, df_meta):
    """运行 Optuna 超参数调优"""
    sample_weights = compute_sample_weights(df_meta)

    print("\n" + "=" * 70)
    print("🔍 Optuna 超参数调优")
    print("=" * 70)

    print(f"\n  📊 特征矩阵: {X.shape[1]} 维 × {len(y)} 样本")
    print(f"  📊 交叉验证: {N_SPLITS} 折 TimeSeriesSplit")
    print(f"  📊 搜索轮数: XGB={N_TRIALS_XGB}, LGB={N_TRIALS_LGB}, 集成={N_TRIALS_ENSEMBLE}")

    # XGBoost 调优
    print("\n" + "─" * 50)
    print("  🔴 阶段1: XGBoost 超参数搜索...")
    t0 = time.time()

    study_xgb = optuna.create_study(
        direction='maximize',
        sampler=optuna.samplers.TPESampler(seed=RANDOM_SEED, n_startup_trials=15),
    )
    study_xgb.optimize(
        lambda trial: objective_xgb(trial, X, y, sample_weights),
        n_trials=N_TRIALS_XGB,
        show_progress_bar=False,
    )

    xgb_best = study_xgb.best_params
    xgb_best['random_state'] = RANDOM_SEED
    xgb_best['eval_metric'] = 'mlogloss'
    xgb_best['use_label_encoder'] = False
    xgb_best['verbosity'] = 0
    xgb_best['n_estimators'] = int(xgb_best['n_estimators'])
    xgb_best['min_child_weight'] = int(xgb_best['min_child_weight'])
    xgb_best['max_depth'] = int(xgb_best['max_depth'])
    xgb_best['max_delta_step'] = int(xgb_best['max_delta_step'])

    xgb_time = time.time() - t0
    print(f"  ✅ XGBoost 搜索完成: 最优CV={study_xgb.best_value:.4f}, 耗时{xgb_time:.1f}s")
    print(f"     最优参数: max_depth={xgb_best.get('max_depth')}, lr={xgb_best.get('learning_rate'):.4f}, n_est={xgb_best.get('n_estimators')}")
    print(f"     LogLoss: {study_xgb.best_trial.user_attrs.get('cv_logloss_mean', 'N/A'):.4f}")

    # LightGBM 调优
    print("\n" + "─" * 50)
    print("  🟢 阶段2: LightGBM 超参数搜索...")
    t0 = time.time()

    study_lgb = optuna.create_study(
        direction='maximize',
        sampler=optuna.samplers.TPESampler(seed=RANDOM_SEED + 100, n_startup_trials=15),
    )
    study_lgb.optimize(
        lambda trial: objective_lgb(trial, X, y, sample_weights),
        n_trials=N_TRIALS_LGB,
        show_progress_bar=False,
    )

    lgb_best = study_lgb.best_params
    lgb_best['random_state'] = RANDOM_SEED
    lgb_best['verbosity'] = -1
    lgb_best['objective'] = 'multiclass'
    lgb_best['num_class'] = 3
    lgb_best['n_estimators'] = int(lgb_best['n_estimators'])
    lgb_best['num_leaves'] = int(lgb_best['num_leaves'])
    lgb_best['max_depth'] = int(lgb_best['max_depth'])
    lgb_best['min_child_weight'] = int(lgb_best['min_child_weight'])
    lgb_best['min_data_in_leaf'] = int(lgb_best['min_data_in_leaf'])

    lgb_time = time.time() - t0
    print(f"  ✅ LightGBM 搜索完成: 最优CV={study_lgb.best_value:.4f}, 耗时{lgb_time:.1f}s")
    print(f"     最优参数: max_depth={lgb_best.get('max_depth')}, num_leaves={lgb_best.get('num_leaves')}, lr={lgb_best.get('learning_rate'):.4f}")
    print(f"     LogLoss: {study_lgb.best_trial.user_attrs.get('cv_logloss_mean', 'N/A'):.4f}")

    # 集成权重搜索
    if N_TRIALS_ENSEMBLE > 0:
        print("\n" + "─" * 50)
        print("  🎯 阶段3: 集成权重搜索...")
        t0 = time.time()

        study_ens = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=RANDOM_SEED + 200, n_startup_trials=5),
        )
        study_ens.optimize(
            lambda trial: objective_ensemble(trial, X, y, xgb_best, lgb_best, sample_weights),
            n_trials=N_TRIALS_ENSEMBLE,
            show_progress_bar=False,
        )

        weight_xgb = study_ens.best_params.get('weight_xgb', 0.5)
        weight_lgb = 1.0 - weight_xgb
        ens_time = time.time() - t0
        ens_cv = study_ens.best_value
        print(f"  ✅ 集成搜索完成: 最优CV={ens_cv:.4f}, 耗时{ens_time:.1f}s")
        print(f"     最优权重: XGB={weight_xgb:.3f}, LGB={weight_lgb:.3f}")
    else:
        print("\n  ⏭️  跳过集成权重搜索（D-015: ensemble 无增益，N_TRIALS_ENSEMBLE=0）")
        weight_xgb = 0.5
        weight_lgb = 0.5
        ens_time = 0
        ens_cv = None

    total_time = xgb_time + lgb_time + ens_time

    return {
        'xgb_best': xgb_best,
        'lgb_best': lgb_best,
        'weight_xgb': weight_xgb,
        'weight_lgb': weight_lgb,
        'xgb_cv': study_xgb.best_value,
        'lgb_cv': study_lgb.best_value,
        'ens_cv': ens_cv,
        'xgb_logloss': study_xgb.best_trial.user_attrs.get('cv_logloss_mean', None),
        'lgb_logloss': study_lgb.best_trial.user_attrs.get('cv_logloss_mean', None),
        'total_time': total_time,
    }


if __name__ == '__main__':
    print("=" * 70)
    print("🎯 Optuna 超参数调优")
    print("=" * 70)
    print(f"  目标: CV 准确率 52%+")
    print(f"  搜索轮数: XGB={N_TRIALS_XGB}, LGB={N_TRIALS_LGB}, 集成={N_TRIALS_ENSEMBLE}")

    X, y, df_meta = load_and_prepare_features()

    result = run_optuna_tuning(X, y, df_meta)

    print("\n" + "=" * 70)
    print("📊 Optuna 调优结果汇总")
    print("=" * 70)
    print(f"  XGBoost 最优CV:  {result['xgb_cv']:.4f} ({result['xgb_cv']*100:.2f}%)")
    print(f"  LightGBM 最优CV: {result['lgb_cv']:.4f} ({result['lgb_cv']*100:.2f}%)")
    if result['ens_cv'] is not None:
        print(f"  集成最优CV:      {result['ens_cv']:.4f} ({result['ens_cv']*100:.2f}%)")
    print(f"  最优权重:        XGB={result['weight_xgb']:.3f}, LGB={result['weight_lgb']:.3f}")
    print(f"  总耗时:          {result['total_time']:.1f}s")

    # 保存结果
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_path = os.path.join(ASSETS_DIR, f'optuna_result_{ts}.json')
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, default=str, ensure_ascii=False)
    print(f"\n  💾 结果已保存: {save_path}")
