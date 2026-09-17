# -*- coding: utf-8 -*-
"""临时调试7: 逐行 py_margin vs 重建 raw 对照"""
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
base = float(np.log(max(float(np.mean(y_home[:train_size])), 1e-6)))

params = {'objective': 'count:poisson', 'eval_metric': 'poisson-nloglik',
          'max_depth': 3, 'learning_rate': 0.05, 'subsample': 0.8,
          'colsample_bytree': 0.8, 'min_child_weight': 10, 'gamma': 1.0,
          'reg_alpha': 0.1, 'reg_lambda': 8.0, 'seed': 42, 'nthread': -1,
          'base_score': float(np.exp(base))}
dx = xgb.DMatrix(X_scaled_df.iloc[:train_size].values, label=y_home[:train_size])
dv = xgb.DMatrix(X_scaled_df.iloc[train_size:].values, label=y_home[train_size:])
mx = xgb.train(params, dx, num_boost_round=30, evals=[(dv, 'val')],
               early_stopping_rounds=20, verbose_eval=0)

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

def js_raw(trees, row):
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
    return total

print("base =", base, " exp(base)=", np.exp(base))
for row in [train_size + i for i in (0, 1, 2, 3, 5)]:
    pmx = mx.predict(xgb.DMatrix(X_scaled_df.iloc[[row]].values), output_margin=True)[0]
    rx = js_raw(x_trees, row)
    pml = np.log(ml.predict(X_scaled_df.iloc[[row]].values)[0])
    rl = js_raw(l_trees, row)
    print(f"row={row}: XGB py_m={pmx:.6f} js_raw={rx:.6f} diff={pmx-rx:+.6f} | "
          f"LGB py_m={pml:.6f} js_raw={rl:.6f} diff={pml-rl:+.6f}")
