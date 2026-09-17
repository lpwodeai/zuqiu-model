"""
阶段五：Stacking 集成方案 V2
==============================

改进要点（基于诊断反馈）:
1. Level-0 仅保留 XGB + LGB 两个强基模型，移除 LR 弱基模型
2. 使用 generate_oof_predictions 的全量 OOF 直接做 5 折元模型 CV，消除分布不匹配
3. 同时尝试 LogisticRegression 和浅层 LGBM 作为元学习器
4. 增强元特征：加入概率差值、方差、熵等统计特征
"""

import os
import sys
import json
import time
import pickle
import inspect
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier
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


def _safe_fit(model, X_train, y_train, X_val, y_val, sample_weight, model_name):
    fit_kwargs = {}
    sig = inspect.signature(model.fit)
    fit_params = sig.parameters

    if 'sample_weight' in fit_params:
        fit_kwargs['sample_weight'] = sample_weight

    if model_name == 'xgb':
        if 'eval_set' in fit_params:
            fit_kwargs['eval_set'] = [(X_val, y_val)]
        if 'verbose' in fit_params:
            fit_kwargs['verbose'] = False
    elif model_name == 'lgb':
        if 'eval_set' in fit_params:
            fit_kwargs['eval_set'] = [(X_val, y_val)]
        if 'callbacks' in fit_params:
            fit_kwargs['callbacks'] = []

    model.fit(X_train, y_train, **fit_kwargs)


def load_and_prepare_features():
    print("  📊 加载比赛数据...")
    df = load_match_data_odds()
    print(f"     {len(df)} 场比赛")

    print("  🔧 构建特征 (D-009~D-013)...")
    X, y = build_all_features(df, include_odds=True, include_elo=True, include_temporal=True)
    print(f"     特征维度: {X.shape[1]}, 样本数: {len(y)}")

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
        print(f"     D-011 降维: {X.shape[1]} 维")

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


def generate_oof_predictions(X, y, params_dict, sample_weights):
    """生成 Level-0 OOF 预测 (XGB + LGB)"""
    models_config = [
        ('xgb', XGBClassifier, params_dict.get('xgb_best', {})),
        ('lgb', LGBMClassifier, params_dict.get('lgb_best', {})),
    ]

    n_samples = len(y)
    n_classes = 3
    n_models = len(models_config)

    oof_probs = np.zeros((n_samples, n_classes * n_models))
    oof_labels = np.zeros((n_samples, n_models))

    tscv = TimeSeriesSplit(n_splits=N_SPLITS)

    for model_idx, (name, model_cls, params) in enumerate(models_config):
        col_start = model_idx * n_classes
        col_end = col_start + n_classes

        for fold_idx, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
            y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
            w_tr = sample_weights[train_idx]

            model = model_cls(**params)
            _safe_fit(model, X_tr, y_tr, X_va, y_va, w_tr, name)
            probs = model.predict_proba(X_va)
            preds = model.predict(X_va)

            oof_probs[val_idx, col_start:col_end] = probs
            oof_labels[val_idx, model_idx] = preds

            if fold_idx == 0:
                print(f"     {name} fold {fold_idx+1}/{N_SPLITS}: acc={accuracy_score(y_va, preds):.4f}")

    return oof_probs, oof_labels, [n for n, _, _ in models_config]


def build_meta_features(oof_probs, oof_labels):
    """构建增强元特征"""
    n_classes = 3
    n_models = oof_probs.shape[1] // n_classes

    feature_list = [oof_probs]

    hard_onehot = np.zeros((len(oof_probs), n_models * n_classes))
    for m_idx in range(n_models):
        for cls in range(n_classes):
            mask = oof_labels[:, m_idx] == cls
            hard_onehot[mask, m_idx * n_classes + cls] = 1.0
    feature_list.append(hard_onehot)

    confidence = np.zeros((len(oof_probs), n_models))
    for m_idx in range(n_models):
        probs = oof_probs[:, m_idx * n_classes:(m_idx + 1) * n_classes]
        top2 = np.sort(probs, axis=1)[:, -2:]
        confidence[:, m_idx] = top2[:, 1] - top2[:, 0]
    feature_list.append(confidence)

    if n_models >= 2:
        prob_diff = np.abs(oof_probs[:, :3] - oof_probs[:, 3:6])
        feature_list.append(prob_diff)

        prob_mean = (oof_probs[:, :3] + oof_probs[:, 3:6]) / 2
        feature_list.append(prob_mean)

        prob_std = np.zeros((len(oof_probs), n_classes))
        for cls in range(n_classes):
            models_probs = np.column_stack([oof_probs[:, cls], oof_probs[:, 3 + cls]])
            prob_std[:, cls] = np.std(models_probs, axis=1)
        feature_list.append(prob_std)

    entropy = np.zeros((len(oof_probs), n_models))
    for m_idx in range(n_models):
        probs = oof_probs[:, m_idx * n_classes:(m_idx + 1) * n_classes]
        entropy[:, m_idx] = -np.sum(probs * np.log(probs + 1e-10), axis=1)
    feature_list.append(entropy)

    meta_features = np.hstack(feature_list)
    return meta_features


def evaluate_meta_models_cv(meta_features, y, sample_weights):
    """用 5 折 CV 评估多个元学习器"""
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)

    meta_configs = [
        ('LR_C0.1', LogisticRegression(C=0.1, solver='lbfgs', max_iter=2000, random_state=RANDOM_SEED)),
        ('LR_C0.5', LogisticRegression(C=0.5, solver='lbfgs', max_iter=2000, random_state=RANDOM_SEED)),
        ('LR_C1.0', LogisticRegression(C=1.0, solver='lbfgs', max_iter=2000, random_state=RANDOM_SEED)),
        ('LGB_meta', LGBMClassifier(
            max_depth=3, num_leaves=8, learning_rate=0.05,
            n_estimators=100, random_state=RANDOM_SEED, verbosity=-1,
            objective='multiclass', num_class=3,
        )),
    ]

    results = {}

    for name, meta_model in meta_configs:
        cv_accs = []
        cv_lls = []

        for train_idx, val_idx in tscv.split(meta_features):
            X_tr, X_va = meta_features[train_idx], meta_features[val_idx]
            y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
            w_tr = sample_weights[train_idx]

            scaler = StandardScaler()
            X_tr_s = scaler.fit_transform(X_tr)
            X_va_s = scaler.transform(X_va)

            model = type(meta_model)(**{k: v for k, v in meta_model.get_params().items()
                                        if k in meta_model.__init__.__code__.co_varnames})

            try:
                model.fit(X_tr_s, y_tr, sample_weight=w_tr)
            except TypeError:
                model.fit(X_tr_s, y_tr)

            preds = model.predict(X_va_s)
            acc = accuracy_score(y_va, preds)
            cv_accs.append(acc)

            proba = model.predict_proba(X_va_s)
            ll = log_loss(y_va, proba, labels=[0, 1, 2])
            cv_lls.append(ll)

        results[name] = {
            'accuracy_mean': float(np.mean(cv_accs)),
            'accuracy_std': float(np.std(cv_accs)),
            'logloss_mean': float(np.mean(cv_lls)),
            'cv_accuracies': [float(a) for a in cv_accs],
        }
        print(f"     {name}: CV={np.mean(cv_accs):.4f}±{np.std(cv_accs):.4f}, LL={np.mean(cv_lls):.4f}")

    return results


def train_final_meta_model(meta_features, y):
    """在全量 OOF 上训练最终元模型"""
    scaler = StandardScaler()
    meta_scaled = scaler.fit_transform(meta_features)

    meta_model = LogisticRegression(C=0.5, solver='lbfgs', max_iter=2000, random_state=RANDOM_SEED)
    meta_model.fit(meta_scaled, y)

    return meta_model, scaler


def run_stacking(X, y, df_meta, params_dict=None):
    """运行 Stacking V2"""
    sample_weights = compute_sample_weights(df_meta)

    if params_dict is None:
        params_dict = {}
        from stacking_ensemble import load_optuna_best
        optuna_result = load_optuna_best()
        if optuna_result:
            params_dict['xgb_best'] = optuna_result['xgb_best']
            params_dict['lgb_best'] = optuna_result['lgb_best']
        else:
            raise ValueError("No Optuna result found. Run optuna_tuning.py first.")

    print("\n" + "=" * 70)
    print("🏗️  Stacking V2 集成模型构建")
    print("=" * 70)

    # Step 1: 生成 OOF
    print("\n  📝 Step 1: 生成 Level-0 OOF 预测 (XGB + LGB)...")
    t0 = time.time()
    oof_probs, oof_labels, model_names = generate_oof_predictions(X, y, params_dict, sample_weights)
    oof_time = time.time() - t0
    print(f"     OOF 矩阵: {oof_probs.shape}, 耗时 {oof_time:.1f}s")

    # Step 2: 构建增强元特征
    print("\n  🔧 Step 2: 构建增强元特征...")
    meta_features = build_meta_features(oof_probs, oof_labels)
    print(f"     元特征维度: {meta_features.shape[1]}")

    # Step 3: CV 评估多个元学习器
    print("\n  📊 Step 3: 元学习器 CV 评估...")
    t0 = time.time()
    meta_results = evaluate_meta_models_cv(meta_features, y, sample_weights)
    cv_time = time.time() - t0

    # Step 4: 选择最佳元模型并训练
    best_name = max(meta_results, key=lambda k: meta_results[k]['accuracy_mean'])
    best_result = meta_results[best_name]
    print(f"\n  🏆 最佳元学习器: {best_name} ({best_result['accuracy_mean']:.4f})")

    print("\n  🧠 Step 4: 训练最终元模型...")
    final_meta, final_scaler = train_final_meta_model(meta_features, y)

    # Step 5: 全量训练基模型
    print("\n  🔥 Step 5: 全量训练 Level-0 基模型...")
    final_models = {}
    for name, model_cls_key in [('xgb', 'xgb_best'), ('lgb', 'lgb_best')]:
        cls = XGBClassifier if name == 'xgb' else LGBMClassifier
        params = params_dict.get(model_cls_key, {})
        model = cls(**params)
        _safe_fit(model, X, y, X.iloc[:1], y.iloc[:1], sample_weights, name)
        final_models[name] = model
        print(f"     {name}: 训练完成")

    return {
        'meta_model': final_meta,
        'meta_scaler': final_scaler,
        'final_models': final_models,
        'meta_results': meta_results,
        'best_meta_name': best_name,
        'best_cv': best_result,
        'oof_probs': oof_probs,
        'oof_labels': oof_labels,
        'meta_features': meta_features,
        'oof_time': oof_time,
        'cv_time': cv_time,
    }


def load_optuna_best():
    import glob
    pattern = os.path.join(ASSETS_DIR, 'optuna_result_*.json')
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    latest = max(files, key=os.path.getctime)
    with open(latest, 'r', encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__':
    print("=" * 70)
    print("🏗️  Stacking V2 集成方案")
    print("=" * 70)

    X, y, df_meta = load_and_prepare_features()
    optuna_result = load_optuna_best()

    if optuna_result:
        print(f'\n📂 加载 Optuna 结果: XGB CV={optuna_result["xgb_cv"]:.4f}, LGB CV={optuna_result["lgb_cv"]:.4f}')
        params_dict = {
            'xgb_best': optuna_result['xgb_best'],
            'lgb_best': optuna_result['lgb_best'],
        }
    else:
        print('\n⚠️  未找到 Optuna 结果，请先运行 optuna_tuning.py')
        sys.exit(1)

    result = run_stacking(X, y, df_meta, params_dict)

    print("\n" + "=" * 70)
    print("📊 Stacking V2 最终结果")
    print("=" * 70)
    best = result['best_cv']
    print(f"最佳元学习器: {result['best_meta_name']}")
    print(f"CV Accuracy:  {best['accuracy_mean']:.4f} ({best['accuracy_mean']*100:.2f}%)")
    print(f"CV Std:       {best['accuracy_std']:.4f}")
    print(f"CV LogLoss:   {best['logloss_mean']:.4f}")
    print(f"Fold Accs:    {[f'{a:.4f}' for a in best['cv_accuracies']]}")

    print("\n--- 对比 ---")
    print(f"XGB 单模型:   {optuna_result['xgb_cv']:.4f} ({optuna_result['xgb_cv']*100:.2f}%)")
    print(f"LGB 单模型:   {optuna_result['lgb_cv']:.4f} ({optuna_result['lgb_cv']*100:.2f}%)")
    stacking_acc = best['accuracy_mean']
    print(f"Stacking V2: {stacking_acc:.4f} ({stacking_acc*100:.2f}%)")
    best_single = max(optuna_result['xgb_cv'], optuna_result['lgb_cv'])
    improvement = stacking_acc - best_single
    print(f"改进幅度:    {improvement:+.4f} ({improvement*100:+.2f}%)")

    # Save
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    model_path = os.path.join(ASSETS_DIR, f'stacking_v2_{ts}.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump({
            'meta_model': result['meta_model'],
            'meta_scaler': result['meta_scaler'],
            'final_models': result['final_models'],
            'meta_results': result['meta_results'],
            'best_meta_name': result['best_meta_name'],
            'best_cv': best,
            'xgb_params': params_dict.get('xgb_best', {}),
            'lgb_params': params_dict.get('lgb_best', {}),
        }, f)
    print(f"\n💾 模型已保存: {model_path}")
