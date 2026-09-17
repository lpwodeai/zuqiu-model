# -*- coding: utf-8 -*-
"""临时调试6: 修复后 XGB/LGB λ 转换器 + base 约定快速验证"""
import os, sys
BASE = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
sys.path.insert(0, os.path.join(BASE, "scripts"))
import numpy as np
import pandas as pd
import xgboost as xgb
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from feature_utils import load_config, load_match_data_odds, build_all_features
import train_models as tm

CONFIG = load_config()
df = load_match_data_odds()
df_tail = df.tail(1200).reset_index(drop=True)
X, y = build_all_features(df_tail, include_odds=CONFIG.get('training', {}).get('include_odds_features', True),
                          ts_odds=True, consensus_odds=True)
Xr = X.reset_index(drop=True)
df_meta = df_tail.loc[X.index].reset_index(drop=True)
y_home = df_meta['homeGoals'].to_numpy(dtype=np.float64)
scaler = StandardScaler()
X_scaled = scaler.fit_transform(Xr)
X_scaled_df = pd.DataFrame(X_scaled, columns=Xr.columns, index=Xr.index)
train_size = int(len(Xr) * 0.8)

# XGB 快速模型
params = {'objective': 'count:poisson', 'eval_metric': 'poisson-nloglik',
          'max_depth': 3, 'learning_rate': 0.05, 'subsample': 0.8,
          'colsample_bytree': 0.8, 'min_child_weight': 10, 'gamma': 1.0,
          'reg_alpha': 0.1, 'reg_lambda': 8.0, 'seed': 42, 'nthread': -1}
base = float(np.log(max(float(np.mean(y_home[:train_size])), 1e-6)))
params['base_score'] = float(np.exp(base))
dx = xgb.DMatrix(X_scaled_df.iloc[:train_size].values, label=y_home[:train_size])
dv = xgb.DMatrix(X_scaled_df.iloc[train_size:].values, label=y_home[train_size:])
mx = xgb.train(params, dx, num_boost_round=30, evals=[(dv, 'val')],
               early_stopping_rounds=20, verbose_eval=0)

# LGB 快速模型
lp = {'objective': 'poisson', 'metric': 'poisson', 'max_depth': 3,
      'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 30,
      'feature_fraction': 0.8, 'bagging_fraction': 0.8, 'bagging_freq': 1,
      'reg_alpha': 0.1, 'reg_lambda': 8.0, 'seed': 42, 'verbose': -1}
ltr = lgb.Dataset(X_scaled_df.iloc[:train_size].values, label=y_home[:train_size],
                  init_score=np.full(train_size, base))
lva = lgb.Dataset(X_scaled_df.iloc[train_size:].values, label=y_home[train_size:], reference=ltr)
ml = lgb.train(lp, ltr, num_boost_round=30, valid_sets=[lva],
               callbacks=[lgb.early_stopping(20)])

x_trees = tm._convert_xgb_poisson_to_js(mx)
l_trees = tm._convert_lgb_poisson_to_js(ml)
feat = Xr.columns.tolist()

def js_pred(trees, row):
    total = base
    for tree in trees:
        node_map = {nd['node_id']: nd for nd in tree['nodes']}
        root = min(nd['node_id'] for nd in tree['nodes'])
        node = node_map[root]
        while node is not None and 'split' in node:
            fname = feat[int(node['split']['feature'][1:])]
            fval = float(X_scaled_df.iloc[row][fname])
            nxt = node['split']['left'] if fval < node['split']['threshold'] else node['split']['right']
            node = node_map.get(nxt)
            if node is None: break
        total += node.get('leaf', 0.0) if node else 0.0
    return np.exp(total)

max_dx = max_dl = 0.0
for row in range(train_size, train_size + 20):
    pyx = mx.predict(xgb.DMatrix(X_scaled_df.iloc[[row]].values))[0]
    pyl = ml.predict(X_scaled_df.iloc[[row]].values)[0]
    jsx = js_pred(x_trees, row)
    jsl = js_pred(l_trees, row)
    max_dx = max(max_dx, abs(jsx - pyx))
    max_dl = max(max_dl, abs(jsl - pyl))
print(f"XGB: py vs JS 最大偏差 = {max_dx:.3e}  {'OK' if max_dx < 1e-3 else 'FAIL'}")
print(f"LGB: py vs JS 最大偏差 = {max_dl:.3e}  {'OK' if max_dl < 1e-3 else 'FAIL'}")
print(f"tree counts: xgb={len(x_trees)} lgb={len(l_trees)}; xgb 单叶树={sum(1 for t in x_trees if all('split' not in n for n in t['nodes']))}")
# 抽样 split feature 合理性（LGB 应为真实列名索引）
snames = [nd['split']['feature'] for t in l_trees[:3] for nd in t['nodes'] if 'split' in nd]
print("LGB split features 抽样:", snames[:8])
