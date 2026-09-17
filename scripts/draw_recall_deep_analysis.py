"""
平局召回率深度分析
==================

分析为什么 XGBoost/LightGBM 的平局召回率普遍低于 0.28 门槛，
探索类别分布、特征区分度和模型行为。
"""

import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

import sys
sys.path.insert(0, r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\scripts')

from feature_utils import load_match_data_odds, build_all_features
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, recall_score, classification_report
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier

print("=" * 70)
print("🔍 平局召回率深度分析")
print("=" * 70)

# 1. 数据加载
print("\n📊 加载数据...")
df = load_match_data_odds()
X, y = build_all_features(df, include_odds=True, include_elo=True,
                           include_temporal=True, include_score=True,
                           include_nonlinear=True)
print(f"   {len(y)} 场比赛, {X.shape[1]} 维特征")

# 2. 类别分布
print("\n" + "=" * 70)
print("  1. 类别分布分析")
print("=" * 70)

home_rate = (y == 0).mean()
draw_rate = (y == 1).mean()
away_rate = (y == 2).mean()
print(f"\n  总体分布:")
print(f"    主胜 (0): {home_rate:.2%} ({(y==0).sum()} 场)")
print(f"    平局 (1): {draw_rate:.2%} ({(y==1).sum()} 场)")
print(f"    客胜 (2): {away_rate:.2%} ({(y==2).sum()} 场)")

# 按联赛分组
if 'league' in df.columns:
    print(f"\n  按联赛分布:")
    leagues = df['league'].unique()
    for league in sorted(leagues):
        mask = df['league'] == league
        y_league = y[mask]
        if len(y_league) > 0:
            h = (y_league == 0).mean()
            d = (y_league == 1).mean()
            a = (y_league == 2).mean()
            print(f"    {league}: 主胜{h:.1%}, 平局{d:.1%}, 客胜{a:.1%} ({len(y_league)}场)")

# 3. 基线模型分析
print("\n" + "=" * 70)
print("  2. 基线模型预测行为分析")
print("=" * 70)

tscv = TimeSeriesSplit(n_splits=5)

for model_name, model_cls, model_params in [
    ("XGBoost", XGBClassifier, {'max_depth': 4, 'learning_rate': 0.03, 'n_estimators': 300,
                                  'use_label_encoder': False, 'eval_metric': 'mlogloss', 'verbosity': 0,
                                  'random_state': 42}),
    ("LightGBM", LGBMClassifier, {'max_depth': 3, 'learning_rate': 0.0106, 'n_estimators': 164,
                                    'verbosity': -1, 'objective': 'multiclass', 'num_class': 3,
                                    'random_state': 42}),
]:
    print(f"\n  📈 {model_name}:")
    all_y_true, all_y_pred = [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]

        model = model_cls(**model_params)
        if model_name == "XGBoost":
            model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        else:
            model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], callbacks=[])

        y_pred = model.predict(X_va)
        all_y_true.extend(y_va)
        all_y_pred.extend(y_pred)

    all_y_true = np.array(all_y_true)
    all_y_pred = np.array(all_y_pred)

    per_class = recall_score(all_y_true, all_y_pred, labels=[0, 1, 2], average=None)
    acc = accuracy_score(all_y_true, all_y_pred)

    home_r, draw_r, away_r = per_class[0], per_class[1], per_class[2]
    home_pct = (all_y_pred == 0).mean()
    draw_pct = (all_y_pred == 1).mean()
    away_pct = (all_y_pred == 2).mean()

    print(f"    准确率: {acc:.4f}")
    print(f"    召回率: 主胜={home_r:.4f}, 平局={draw_r:.4f}, 客胜={away_r:.4f}")
    print(f"    预测分布: 主胜={home_pct:.2%}, 平局={draw_pct:.2%}, 客胜={away_pct:.2%}")
    print(f"    真实分布: 主胜={home_rate:.2%}, 平局={draw_rate:.2%}, 客胜={away_rate:.2%}")

    # 分析平局预测的详细情况
    draw_mask_true = all_y_true == 1
    draw_mask_pred = all_y_pred == 1
    draw_precision = (all_y_true[draw_mask_pred] == 1).mean() if draw_mask_pred.sum() > 0 else 0
    print(f"    平局预测: 预测为平局{draw_pct:.2%}, 其中真实平局{draw_precision:.2%} (精确率)")
    print(f"    平局召回: 真实平局面 {draw_rate:.2%} 中, 被正确预测的比例 = {draw_r:.4f}")

    print(f"\n    分类报告:")
    print(f"    {classification_report(all_y_true, all_y_pred, target_names=['主胜', '平局', '客胜'], digits=4)}")

# 4. 不同学习率对平局召回率的影响
print("\n" + "=" * 70)
print("  3. 不同学习率对平局召回率的影响")
print("=" * 70)

print(f"\n  XGBoost (固定 max_depth=4, n_est=300):")
for lr in [0.03, 0.05, 0.07, 0.10, 0.12]:
    params = {'max_depth': 4, 'learning_rate': lr, 'n_estimators': 300,
              'use_label_encoder': False, 'eval_metric': 'mlogloss', 'verbosity': 0, 'random_state': 42}
    all_y_true, all_y_pred = [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        model = XGBClassifier(**params)
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
        all_y_true.extend(y_va)
        all_y_pred.extend(model.predict(X_va))

    per_class = recall_score(np.array(all_y_true), np.array(all_y_pred), labels=[0, 1, 2], average=None)
    acc = accuracy_score(np.array(all_y_true), np.array(all_y_pred))
    draw_pred_pct = (np.array(all_y_pred) == 1).mean()
    print(f"    lr={lr:.2f}: acc={acc:.4f}, draw_recall={per_class[1]:.4f}, draw_pred={draw_pred_pct:.2%}")

print(f"\n  LightGBM (固定 max_depth=3, n_est=200):")
for lr in [0.03, 0.05, 0.07, 0.10, 0.12]:
    params = {'max_depth': 3, 'learning_rate': lr, 'n_estimators': 200,
              'verbosity': -1, 'objective': 'multiclass', 'num_class': 3, 'random_state': 42}
    all_y_true, all_y_pred = [], []

    for train_idx, val_idx in tscv.split(X):
        X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]
        model = LGBMClassifier(**params)
        model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], callbacks=[])
        all_y_true.extend(y_va)
        all_y_pred.extend(model.predict(X_va))

    per_class = recall_score(np.array(all_y_true), np.array(all_y_pred), labels=[0, 1, 2], average=None)
    acc = accuracy_score(np.array(all_y_true), np.array(all_y_pred))
    draw_pred_pct = (np.array(all_y_pred) == 1).mean()
    print(f"    lr={lr:.2f}: acc={acc:.4f}, draw_recall={per_class[1]:.4f}, draw_pred={draw_pred_pct:.2%}")

# 5. 分析结论
print("\n" + "=" * 70)
print("  4. 分析结论")
print("=" * 70)
print(f"""
  问题诊断:
  ┌─────────────────────────────────────────────────────────────────────┐
  │ 1. 平局真实占比 ~25.6%, 但模型预测平局的比例偏低                        │
  │ 2. 平局召回率在 0.07~0.23 之间波动, 远低于门槛 0.28                    │
  │ 3. 提高学习率对平局召回率的提升有限                                    │
  │ 4. 模型倾向于预测主胜/客胜, 回避平局决策                                │
  │                                                                     │
  │ 根本原因:                                                            │
  │ a) 平局本身就是三类中最难区分的类别(主胜/客胜有明确信号)                 │
  │ b) 209维特征中, 平局信号本身较弱(更多是噪声而非模式)                     │
  │ c) 树模型的 max_depth=3~4 限制了决策复杂度                              │
  │                                                                     │
  │ 建议:                                                                │
  │ - 降低 DRAW_RECALL_MIN 到 0.15~0.20 (更现实的目标)                      │
  │ - 或使用 class_weight / scale_pos_weight 增强平局学习                   │
  │ - 特征工程: 添加「双方实力接近度」特征                                   │
  └─────────────────────────────────────────────────────────────────────┘
""")