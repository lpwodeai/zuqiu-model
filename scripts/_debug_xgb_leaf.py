# -*- coding: utf-8 -*-
"""临时调试11: XGB pred_leaf 路径对比定位分歧"""
import os, sys
BASE = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
sys.path.insert(0, os.path.join(BASE, "scripts"))
import numpy as np
import pandas as pd
import xgboost as xgb
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

row = 960
leaf_idx = mx.predict(xgb.DMatrix(X_scaled_df.iloc[[row]].values), pred_leaf=True)[0]
print("XGB pred_leaf 索引:", leaf_idx)

feat = Xr.columns.tolist()
x_trees = tm._convert_xgb_poisson_to_js(mx)
# 找第几棵树、哪个节点分歧: 我的遍历 leaf vs XGB 的 leaf
for ti, tree in enumerate(x_trees):
    node_map = {nd['node_id']: nd for nd in tree['nodes']}
    root = min(nd['node_id'] for nd in tree['nodes'])
    node = node_map[root]
    visited = []
    while node is not None and 'split' in node:
        fname = feat[int(node['split']['feature'][1:])]
        fval = float(X_scaled_df.iloc[row][fname])
        thr = node['split']['threshold']
        visited.append((node['split']['feature'], fval, thr, fval < thr))
        nxt = node['split']['left'] if fval < thr else node['split']['right']
        node = node_map.get(nxt)
        if node is None: break
    my_leaf = node.get('leaf', 0.0) if node else 0.0
    # XGB 该树 leaf（pred_leaf 给出该树叶子序号，需映射）
    xgb_node = [nd for nd in tree['nodes'] if nd.get('leaf') is not None]
    xgb_leaf_val = None
    # pred_leaf 的叶子序号是全局树内节点 id？XGB pred_leaf 返回节点 id
    xgb_nid = int(leaf_idx[ti])
    xgb_leaf_val = node_map.get(xgb_nid, {}).get('leaf')
    if xgb_leaf_val is not None and abs(my_leaf - xgb_leaf_val) > 1e-8:
        print(f"树{ti} 分歧: my_leaf={my_leaf:.6f} xgb_leaf={xgb_leaf_val:.6f} 路径:")
        for v in visited:
            print("   ", v)
        if ti >= 2: break
