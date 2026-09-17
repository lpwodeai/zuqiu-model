"""
精细化校准因子搜索
==================

在 draw_recall >= 0.28 的约束下搜索准确率最高的 factor。
使用 0.005 的细粒度步长，以及验证不同目标召回率门槛。
"""

import os
import sys
import warnings
import numpy as np

warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, recall_score, precision_score, f1_score
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from draw_calibrator import DrawCalibrator
from feature_utils import load_match_data_odds, build_all_features

N_SPLITS = 5
RANDOM_SEED = 42

XGB_BEST = {
    'max_depth': 4, 'learning_rate': 0.07, 'n_estimators': 178,
    'subsample': 0.785, 'colsample_bytree': 0.827,
    'reg_alpha': 0.0101, 'reg_lambda': 0.0282, 'gamma': 3.61,
    'min_child_weight': 3, 'max_delta_step': 0,
    'scale_pos_weight': 2.108,
    'random_state': RANDOM_SEED, 'eval_metric': 'mlogloss',
    'use_label_encoder': False, 'verbosity': 0,
}

print("=" * 70)
print("🔍 精细化校准因子搜索 (XGBoost 基线 209维)")
print("=" * 70)

print("\n📊 加载数据并训练 XGBoost (5折 CV)...")
df = load_match_data_odds()
X, y = build_all_features(df, include_odds=True, include_elo=True,
                          include_temporal=True, include_score=True,
                          include_nonlinear=True, include_draw_enhanced=False)
print(f"   特征: {X.shape[1]} 维, 样本: {len(y)}")

weights = np.ones(len(y))
weights[y == 1] = 1.2

tscv = TimeSeriesSplit(n_splits=N_SPLITS)
all_y_true, all_y_proba = [], []

for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
    X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
    y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
    w_tr = weights[train_idx]

    model = XGBClassifier(**XGB_BEST)
    model.fit(X_tr, y_tr, sample_weight=w_tr, eval_set=[(X_va, y_va)], verbose=False)

    all_y_true.extend(y_va)
    all_y_proba.append(model.predict_proba(X_va))
    print(f"   Fold {fold+1}: 训练完成")

y_true = np.array(all_y_true)
y_proba = np.vstack(all_y_proba)

# 无校准基线
y_pred_base = np.argmax(y_proba, axis=1)
base_acc = accuracy_score(y_true, y_pred_base)
base_per_class = recall_score(y_true, y_pred_base, labels=[0, 1, 2], average=None)
base_draw_recall = base_per_class[1]
base_f1_macro = f1_score(y_true, y_pred_base, average='macro')

print(f"\n  基线 (无校准):")
print(f"     准确率: {base_acc:.6f}")
print(f"     平局召回率: {base_draw_recall:.6f}")
print(f"     平局精确率: {precision_score(y_true, y_pred_base, labels=[0,1,2], average=None)[1]:.6f}")
print(f"     F1 macro: {base_f1_macro:.6f}")
print(f"\n  搜索 factor 范围 0.800 ~ 1.000, 步长 0.005...")

calibrator = DrawCalibrator()
results = []
for f in np.arange(0.800, 1.001, 0.005):
    y_pred = calibrator.predict(y_proba, factor=f)
    acc = accuracy_score(y_true, y_pred)
    per_class_recall = recall_score(y_true, y_pred, labels=[0, 1, 2], average=None)
    per_class_precision = precision_score(y_true, y_pred, labels=[0, 1, 2], average=None)
    draw_recall = per_class_recall[1]
    draw_precision = per_class_precision[1]
    f1_macro = f1_score(y_true, y_pred, average='macro')
    draw_pred = (y_pred == 1).mean()

    delta_acc = acc - base_acc
    delta_draw = draw_recall - base_draw_recall

    results.append({
        'factor': f,
        'acc': acc,
        'draw_recall': draw_recall,
        'draw_precision': draw_precision,
        'f1_macro': f1_macro,
        'draw_pred_rate': draw_pred,
        'delta_acc': delta_acc,
        'delta_draw': delta_draw,
        'ok_028': draw_recall >= 0.28,
        'ok_030': draw_recall >= 0.30,
    })

print(f"\n  factor 范围搜索结果 (draw_recall >= 0.28 的候选):")
print(f"\n  {'factor':>6} | {'准确率':>8} | {'平局召回':>8} | {'平局精确':>8} | {'F1宏':>8} | {'Δacc':>7} | {'Δdraw':>7}")
print(f"  {'-'*6}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*7}-+-{'-'*7}")

candidates_028 = [r for r in results if r['ok_028']]
for r in sorted(candidates_028, key=lambda x: -x['acc'])[:15]:
    mark = "⭐" if r == max(candidates_028, key=lambda x: x['acc']) else "  "
    print(f"  {mark}{r['factor']:.3f} | {r['acc']:.6f} | {r['draw_recall']:.6f} | {r['draw_precision']:.6f} | {r['f1_macro']:.6f} | {r['delta_acc']:+7.4f} | {r['delta_draw']:+7.4f}")

if candidates_028:
    best = max(candidates_028, key=lambda x: x['acc'])
    print(f"\n  🎯 最优 factor (draw_recall>=0.28, 准确率最高): factor={best['factor']:.3f}")
    print(f"     准确率: {best['acc']:.6f} (Δ{best['delta_acc']:+.4f})")
    print(f"     平局召回率: {best['draw_recall']:.6f} (Δ{best['delta_draw']:+.4f})")
    print(f"     平局精确率: {best['draw_precision']:.6f}")
    print(f"     F1 macro: {best['f1_macro']:.6f}")
    print(f"     平局预测占比: {best['draw_pred_rate']:.4f}")

print(f"\n  draw_recall >= 0.30 的候选:")
candidates_030 = [r for r in results if r['ok_030']]
if candidates_030:
    for r in sorted(candidates_030, key=lambda x: -x['acc'])[:5]:
        mark = "⭐" if r == max(candidates_030, key=lambda x: x['acc']) else "  "
        print(f"  {mark}{r['factor']:.3f} | {r['acc']:.6f} | {r['draw_recall']:.6f} | {r['draw_precision']:.6f} | {r['f1_macro']:.6f} | {r['delta_acc']:+7.4f} | {r['delta_draw']:+7.4f}")

    best030 = max(candidates_030, key=lambda x: x['acc'])
    print(f"\n  🎯 最优 factor (draw_recall>=0.30): factor={best030['factor']:.3f}, acc={best030['acc']:.6f}")
else:
    print(f"  无达标候选 (最高 draw_recall={max(r['draw_recall'] for r in results):.4f})")

# 打印 Pareto 前沿
print(f"\n  📈 Pareto 前沿 (准确率/平局召回率 不可同时更优):")
sorted_by_acc = sorted(results, key=lambda x: -x['acc'])
pareto = []
best_draw_seen = -1
for r in sorted_by_acc:
    if r['draw_recall'] > best_draw_seen:
        pareto.append(r)
        best_draw_seen = r['draw_recall']

for r in pareto:
    mark = "  "
    if r['draw_recall'] >= 0.30: mark = "🟢"
    elif r['draw_recall'] >= 0.28: mark = "✅"
    elif r['draw_recall'] >= base_draw_recall: mark = "▫️"
    else: mark = "❌"
    print(f"  {mark} factor={r['factor']:.3f}: acc={r['acc']:.4f}, draw_recall={r['draw_recall']:.4f}")

print("\n" + "=" * 70)
print("  ✅ 搜索完成")
print("=" * 70)