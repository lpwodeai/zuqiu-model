# -*- coding: utf-8 -*-
"""
C-20260823-003: 赔率特征精简 A/B 对比脚本

对比: 全量赔率特征 (~135维) vs 精简赔率特征 (~30维)
评估: 准确率 / LogLoss / Brier / 平局召回率 / 特征维度
"""

import os, sys, time, json
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import log_loss, brier_score_loss, accuracy_score
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

from feature_utils import build_all_features, load_match_data_odds

# ============================================================
# 配置
# ============================================================
N_SPLITS = 5
XGB_PARAMS = {
    'max_depth': 4, 'learning_rate': 0.07, 'n_estimators': 130,
    'subsample': 0.785, 'gamma': 5.0, 'reg_alpha': 0.1, 'reg_lambda': 8.0,
    'min_child_weight': 13, 'objective': 'multi:softprob', 'eval_metric': 'mlogloss',
    'random_state': 42, 'verbosity': 0, 'n_jobs': -1,
}

# ============================================================
# 加载数据
# ============================================================
print("=" * 70)
print("赔率特征精简 A/B 对比")
print("=" * 70)

print("\n[1/5] 加载比赛数据...")
df = load_match_data_odds()
print(f"  数据量: {len(df)} 场, 列数: {df.shape[1]}")

# 过滤掉没有标签的
df = df[df['result'].notna()].copy()
print(f"  有效样本: {len(df)} 场")

# ============================================================
# 构建两套特征
# ============================================================
print("\n[2/5] 构建特征...")

# 全量特征
t0 = time.time()
X_full, y_full = build_all_features(df, slim_odds=False)
t_full = time.time() - t0
full_dim = X_full.shape[1]

# 精简特征
t0 = time.time()
X_slim, y_slim = build_all_features(df, slim_odds=True)
t_slim = time.time() - t0
slim_dim = X_slim.shape[1]

print(f"\n  全量特征: {full_dim} 维 ({t_full:.1f}s)")
print(f"  精简特征: {slim_dim} 维 ({t_slim:.1f}s)")
print(f"  减少维度: {full_dim - slim_dim} 维 ({(1 - slim_dim/full_dim)*100:.1f}%)")
print(f"  构建速度: {t_full/t_slim:.1f}x 加速")

# 验证标签一致
assert (y_full == y_slim).all(), "标签不一致!"
print(f"  标签验证: OK ({len(y_full)} 场)")

# ============================================================
# 时间序列交叉验证
# ============================================================
print(f"\n[3/5] 时间序列交叉验证 ({N_SPLITS}折)...")

tscv = TimeSeriesSplit(n_splits=N_SPLITS)

results = {
    'full': {'accuracy': [], 'logloss': [], 'brier': [], 'draw_recall': [], 'folds': 0},
    'slim': {'accuracy': [], 'logloss': [], 'brier': [], 'draw_recall': [], 'folds': 0},
}

for fold, (train_idx, test_idx) in enumerate(tscv.split(X_full)):
    print(f"\n  --- Fold {fold+1}/{N_SPLITS} ---")
    print(f"  训练: {len(train_idx)} 场, 测试: {len(test_idx)} 场")

    for name, X_data, dim in [('full', X_full, full_dim), ('slim', X_slim, slim_dim)]:
        X_train, X_test = X_data.iloc[train_idx], X_data.iloc[test_idx]
        y_train, y_test = y_full.iloc[train_idx], y_full.iloc[test_idx]

        # 缩放
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)

        # 训练
        model = xgb.XGBClassifier(**XGB_PARAMS)
        model.fit(X_train_scaled, y_train, verbose=False)

        # 预测
        y_pred = model.predict(X_test_scaled)
        y_proba = model.predict_proba(X_test_scaled)

        # 指标
        acc = accuracy_score(y_test, y_pred)
        ll = log_loss(y_test, y_proba)
        br = brier_score_loss(y_test == 1, y_proba[:, 1])  # 平局 (class 1)

        # 平局召回率
        draw_mask = y_test == 1
        draw_recall = (y_pred[draw_mask] == 1).sum() / max(draw_mask.sum(), 1)

        results[name]['accuracy'].append(acc)
        results[name]['logloss'].append(ll)
        results[name]['brier'].append(br)
        results[name]['draw_recall'].append(draw_recall)
        results[name]['folds'] += 1

        print(f"  {name:6s} | Acc={acc*100:.2f}%  LogLoss={ll:.4f}  "
              f"Brier={br:.4f}  DrawRecall={draw_recall*100:.1f}%")

# ============================================================
# 汇总
# ============================================================
print(f"\n[4/5] 汇总对比...")

def summarize(name, res):
    r = {}
    for key in ['accuracy', 'logloss', 'brier', 'draw_recall']:
        vals = res[key]
        r[key] = {
            'mean': np.mean(vals),
            'std': np.std(vals),
            'min': np.min(vals),
            'max': np.max(vals),
        }
    return r

full_sum = summarize('full', results['full'])
slim_sum = summarize('slim', results['slim'])

print(f"\n{'='*70}")
print(f"{'指标':<20} {'全量特征':>15} {'精简特征':>15} {'差距':>15}")
print(f"{'='*70}")

for key, label in [('accuracy', '准确率'), ('logloss', 'LogLoss'), ('brier', 'Brier(平局)'), ('draw_recall', '平局召回率')]:
    fv = full_sum[key]['mean']
    sv = slim_sum[key]['mean']
    diff = sv - fv
    direction = '↑' if (key == 'accuracy' or key == 'draw_recall') and diff > 0 else \
                '↓' if (key == 'accuracy' or key == 'draw_recall') and diff < 0 else \
                '↑' if (key in ('logloss', 'brier')) and diff < 0 else \
                '↓' if (key in ('logloss', 'brier')) and diff > 0 else '→'
    if key == 'accuracy':
        print(f"{label:<20} {fv*100:>14.2f}% {sv*100:>14.2f}% {diff*100:>+14.2f}pp {direction}")
    elif key == 'draw_recall':
        print(f"{label:<20} {fv*100:>14.2f}% {sv*100:>14.2f}% {diff*100:>+14.2f}pp {direction}")
    else:
        print(f"{label:<20} {fv:>15.4f} {sv:>15.4f} {diff:>+15.4f} {direction}")

print(f"{'='*70}")
print(f"{'特征维度':<20} {full_dim:>15} {slim_dim:>15} {slim_dim-full_dim:>+15}")

# 方差稳定性
print(f"\n  方差对比:")
for key, label in [('accuracy', '准确率'), ('logloss', 'LogLoss')]:
    fv_std = full_sum[key]['std']
    sv_std = slim_sum[key]['std']
    print(f"  {label}: 全量±{fv_std:.4f} vs 精简±{sv_std:.4f} "
          f"({'更稳定' if sv_std < fv_std else '更波动'})")

# ============================================================
# 结论
# ============================================================
print(f"\n[5/5] 结论...")

acc_diff = (slim_sum['accuracy']['mean'] - full_sum['accuracy']['mean']) * 100
ll_diff = slim_sum['logloss']['mean'] - full_sum['logloss']['mean']

if abs(acc_diff) < 0.3:
    acc_verdict = "准确率持平（<0.3pp），精简特征对分类结果无显著影响"
elif acc_diff > 0:
    acc_verdict = f"准确率提升 {acc_diff:.2f}pp，精简特征泛化能力更强"
else:
    acc_verdict = f"准确率下降 {abs(acc_diff):.2f}pp，需评估是否接受"

if ll_diff < 0:
    ll_verdict = f"LogLoss 改善 {abs(ll_diff):.4f}，概率校准更优"
else:
    ll_verdict = f"LogLoss 恶化 {ll_diff:.4f}，概率校准变差"

print(f"\n  {acc_verdict}")
print(f"  {ll_verdict}")
print(f"  特征维度: {full_dim} → {slim_dim} (减少 {(1-slim_dim/full_dim)*100:.1f}%)")
print(f"  构建速度: {t_full/t_slim:.1f}x 加速")

# 保存结果
output = {
    'config': {'splits': N_SPLITS, 'samples': len(df), 'full_dim': full_dim, 'slim_dim': slim_dim},
    'full': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} for k, v in results['full'].items() if k != 'folds'},
    'slim': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} for k, v in results['slim'].items() if k != 'folds'},
    'verdict': {'accuracy': acc_verdict, 'logloss': ll_verdict},
}

result_path = BASE / "logs" / f"ab_test_odds_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.json"
os.makedirs(BASE / "logs", exist_ok=True)
with open(result_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print(f"\n  结果已保存: {result_path}")