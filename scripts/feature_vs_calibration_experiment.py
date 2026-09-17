"""
特征工程 + 后处理校准 对比实验
================================

实验设计:
1. 基线 (209维): 原始特征
2. 增强 (215维): 加入平局增强特征 (T-003.3)
3. 基线 + 校准: 209维 + 后处理校准
4. 增强 + 校准: 215维 + 后处理校准

每组使用 XGBoost 和 LightGBM, 5折 TimeSeriesSplit,
比较准确率、平局召回率、平局精确率。
"""

import os
import sys
import time
import warnings
import numpy as np

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, recall_score
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from draw_calibrator import DrawCalibrator, find_optimal_factor

from feature_utils import (
    load_match_data_odds, build_all_features,
    build_draw_enhanced_features
)

N_SPLITS = 5
RANDOM_SEED = 42

# Optuna 最优参数 (2026-08-21 调优结果)
XGB_BEST = {
    'max_depth': 4, 'learning_rate': 0.07, 'n_estimators': 178,
    'subsample': 0.785, 'colsample_bytree': 0.827,
    'reg_alpha': 0.0101, 'reg_lambda': 0.0282, 'gamma': 3.61,
    'min_child_weight': 3, 'max_delta_step': 0,
    'scale_pos_weight': 2.108,
    'random_state': RANDOM_SEED, 'eval_metric': 'mlogloss',
    'use_label_encoder': False, 'verbosity': 0,
}

LGB_BEST = {
    'max_depth': 6, 'num_leaves': 212, 'learning_rate': 0.12,
    'n_estimators': 105, 'subsample': 0.680, 'colsample_bytree': 0.540,
    'reg_alpha': 3.353, 'reg_lambda': 13.904, 'min_child_weight': 8,
    'min_data_in_leaf': 40, 'feature_fraction': 0.566,
    'bagging_fraction': 0.783, 'bagging_freq': 5,
    'random_state': RANDOM_SEED, 'verbosity': -1,
    'objective': 'multiclass', 'num_class': 3,
}

print("=" * 70)
print("🔬 特征工程 + 后处理校准 对比实验")
print("=" * 70)

# ==================== 数据加载 ====================
print("\n📊 加载数据...")
df = load_match_data_odds()
print(f"   {len(df)} 场比赛")

# ==================== 构建两组特征 ====================
print("\n🔧 构建基线特征 (不含平局增强)...")
X_base, y = build_all_features(df, include_odds=True, include_elo=True,
                                include_temporal=True, include_score=True,
                                include_nonlinear=True, include_draw_enhanced=False)
print(f"   基线特征: {X_base.shape[1]} 维")

# 在基线上添加增强特征
print("\n🔧 构建增强特征 (基线 + 6维平局增强)...")
draw_enhanced = build_draw_enhanced_features(X_base)
X_enhanced = X_base.copy()
for col in draw_enhanced.columns:
    X_enhanced[col] = draw_enhanced[col]
print(f"   增强特征: {X_enhanced.shape[1]} 维 (+{X_enhanced.shape[1] - X_base.shape[1]})")

# 样本权重
weights = np.ones(len(y))
weights[y == 1] = 1.2

# ==================== 交叉验证训练函数 ====================
def run_cv_experiment(X, y, weights, model_cls, model_params, model_name):
    """运行5折CV，返回每折的概率和标签"""
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    all_y_true = []
    all_y_proba = []
    fold_results = []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        w_tr = weights[train_idx]

        model = model_cls(**model_params)
        if model_name == "XGBoost":
            model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], verbose=False)
        else:
            model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], callbacks=[])

        y_pred = model.predict(X_va)
        y_proba = model.predict_proba(X_va)

        acc = accuracy_score(y_va, y_pred)
        per_class = recall_score(y_va, y_pred, labels=[0, 1, 2], average=None)
        ll = log_loss(y_va, y_proba, labels=[0, 1, 2])

        fold_results.append({
            'acc': acc, 'logloss': ll,
            'draw_recall': per_class[1],
            'home_recall': per_class[0],
            'away_recall': per_class[2],
        })

        all_y_true.extend(y_va)
        all_y_proba.append(y_proba)

    all_y_true = np.array(all_y_true)
    all_y_proba = np.vstack(all_y_proba)

    return all_y_true, all_y_proba, fold_results


def evaluate_baseline(y_true, y_proba, label):
    """评估无校准的基线"""
    y_pred = np.argmax(y_proba, axis=1)
    acc = accuracy_score(y_true, y_pred)
    per_class = recall_score(y_true, y_pred, labels=[0, 1, 2], average=None)
    ll = log_loss(y_true, y_proba, labels=[0, 1, 2])
    draw_mask = y_pred == 1
    draw_prec = (y_true[draw_mask] == 1).mean() if draw_mask.sum() > 0 else 0
    return {
        'label': label,
        'accuracy': acc,
        'log_loss': ll,
        'draw_recall': per_class[1],
        'draw_precision': draw_prec,
        'draw_pred_rate': draw_mask.mean(),
        'home_recall': per_class[0],
        'away_recall': per_class[2],
    }


# ==================== 运行4组实验 ====================
experiments = [
    (f"基线 ({X_base.shape[1]}维)", X_base),
    (f"增强 ({X_enhanced.shape[1]}维)", X_enhanced),
]

results = []
all_experiments = {}

for exp_name, X_data in experiments:
    for model_name, model_cls, model_params in [
        ("XGBoost", XGBClassifier, XGB_BEST),
        ("LightGBM", LGBMClassifier, LGB_BEST),
    ]:
        key = f"{exp_name} | {model_name}"
        print(f"\n  🔬 实验: {key}")
        t0 = time.time()
        y_true, y_proba, fold_res = run_cv_experiment(X_data, y, weights, model_cls, model_params, model_name)
        elapsed = time.time() - t0

        # 基线评估
        base_eval = evaluate_baseline(y_true, y_proba, key)
        base_eval['time'] = elapsed
        results.append(base_eval)

        # 校准评估 - 自动搜索 factor 使 draw_recall >= 0.28
        best_factor, cal_metrics = find_optimal_factor(y_true, y_proba, target_recall=0.28)
        cal_metrics['label'] = f"{key} + 校准(factor={best_factor:.2f})"
        cal_metrics['time'] = elapsed
        results.append(cal_metrics)

        all_experiments[key] = {
            'y_true': y_true,
            'y_proba': y_proba,
            'fold_results': fold_res,
        }

        fold_avg_acc = np.mean([f['acc'] for f in fold_res])
        fold_avg_draw = np.mean([f['draw_recall'] for f in fold_res])
        print(f"     CV准确率: {fold_avg_acc:.4f}, 平局召回率: {fold_avg_draw:.4f}")
        print(f"     校准后: 准确率={cal_metrics['accuracy']:.4f}, 平局召回率={cal_metrics['draw_recall']:.4f}, factor={best_factor:.2f}")

# ==================== 结果汇总 ====================
print("\n" + "=" * 70)
print("  📊 实验结果汇总")
print("=" * 70)

print(f"\n  {'实验':<45} | {'准确率':>7} | {'平局召回':>7} | {'平局精确':>7} | {'平局预测占比':>10}")
print(f"  {'-'*45}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}-+-{'-'*10}")

for r in results:
    print(f"  {r['label']:<45} | {r['accuracy']:.4f}  | {r['draw_recall']:.4f}  | {r['draw_precision']:.4f}  | {r['draw_pred_rate']:.4f}")

# ==================== 关键对比 ====================
print("\n" + "=" * 70)
print("  🔑 关键对比")
print("=" * 70)

# 对比1: 新特征效果
base_xgb = [r for r in results if '基线' in r['label'] and 'XGBoost' in r['label'] and '校准' not in r['label']][0]
enh_xgb = [r for r in results if '增强' in r['label'] and 'XGBoost' in r['label'] and '校准' not in r['label']][0]
base_lgb = [r for r in results if '基线' in r['label'] and 'LightGBM' in r['label'] and '校准' not in r['label']][0]
enh_lgb = [r for r in results if '增强' in r['label'] and 'LightGBM' in r['label'] and '校准' not in r['label']][0]

print(f"\n  1. 新特征效果 (XGBoost):")
print(f"     基线: acc={base_xgb['accuracy']:.4f}, draw_recall={base_xgb['draw_recall']:.4f}")
print(f"     增强: acc={enh_xgb['accuracy']:.4f}, draw_recall={enh_xgb['draw_recall']:.4f}")
print(f"     变化: acc={enh_xgb['accuracy']-base_xgb['accuracy']:+.4f}, draw_recall={enh_xgb['draw_recall']-base_xgb['draw_recall']:+.4f}")

print(f"\n  2. 新特征效果 (LightGBM):")
print(f"     基线: acc={base_lgb['accuracy']:.4f}, draw_recall={base_lgb['draw_recall']:.4f}")
print(f"     增强: acc={enh_lgb['accuracy']:.4f}, draw_recall={enh_lgb['draw_recall']:.4f}")
print(f"     变化: acc={enh_lgb['accuracy']-base_lgb['accuracy']:+.4f}, draw_recall={enh_lgb['draw_recall']-base_lgb['draw_recall']:+.4f}")

# 对比2: 校准效果
base_xgb_cal = [r for r in results if '基线' in r['label'] and 'XGBoost' in r['label'] and '校准' in r['label']][0]
enh_xgb_cal = [r for r in results if '增强' in r['label'] and 'XGBoost' in r['label'] and '校准' in r['label']][0]
base_lgb_cal = [r for r in results if '基线' in r['label'] and 'LightGBM' in r['label'] and '校准' in r['label']][0]
enh_lgb_cal = [r for r in results if '增强' in r['label'] and 'LightGBM' in r['label'] and '校准' in r['label']][0]

print(f"\n  3. 后处理校准效果 (XGBoost 增强版):")
print(f"     无校准: acc={enh_xgb['accuracy']:.4f}, draw_recall={enh_xgb['draw_recall']:.4f}")
print(f"     有校准: acc={enh_xgb_cal['accuracy']:.4f}, draw_recall={enh_xgb_cal['draw_recall']:.4f}")
print(f"     变化:   acc={enh_xgb_cal['accuracy']-enh_xgb['accuracy']:+.4f}, draw_recall={enh_xgb_cal['draw_recall']-enh_xgb['draw_recall']:+.4f}")

print(f"\n  4. 后处理校准效果 (LightGBM 增强版):")
print(f"     无校准: acc={enh_lgb['accuracy']:.4f}, draw_recall={enh_lgb['draw_recall']:.4f}")
print(f"     有校准: acc={enh_lgb_cal['accuracy']:.4f}, draw_recall={enh_lgb_cal['draw_recall']:.4f}")
print(f"     变化:   acc={enh_lgb_cal['accuracy']-enh_lgb['accuracy']:+.4f}, draw_recall={enh_lgb_cal['draw_recall']-enh_lgb['draw_recall']:+.4f}")

# 达标分析
print(f"\n  5. 是否达到 0.28 平局召回率门槛:")
for r in results:
    status = "✅" if r['draw_recall'] >= 0.28 else "❌"
    print(f"     {status} {r['label']}: draw_recall={r['draw_recall']:.4f}")

print("\n" + "=" * 70)
print("  ✅ 实验完成")
print("=" * 70)