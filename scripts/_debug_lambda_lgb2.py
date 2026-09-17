# -*- coding: utf-8 -*-
"""诊断: LGB λ 模型 py predict vs dump 重建（pred_leaf 逐树定位分歧）。"""
import os
import sys
import json

BASE = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
sys.path.insert(0, os.path.join(BASE, "scripts"))

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from feature_utils import load_config, load_match_data_odds, build_all_features
import train_models as tm

CONFIG = load_config()
df = load_match_data_odds()
df_tail = df.tail(4000).reset_index(drop=True)
include_odds = CONFIG.get('training', {}).get('include_odds_features', True)
X, y = build_all_features(df_tail, include_odds=include_odds, ts_odds=True, consensus_odds=True)
df_meta = df_tail.loc[X.index].reset_index(drop=True) if len(X) == len(df_tail) else df_tail.iloc[:len(X)].reset_index(drop=True)
Xr = X.reset_index(drop=True)
scaler = StandardScaler()
X_scaled = scaler.fit_transform(Xr)
X_scaled_df = pd.DataFrame(X_scaled, columns=Xr.columns, index=Xr.index)
y_home = df_meta['homeGoals'].to_numpy(dtype=np.float64)
feat = Xr.columns.tolist()

n_total = len(X_scaled_df)
train_size = int(n_total * 0.8)
X_tr, X_va = Xr.iloc[:train_size].values, Xr.iloc[train_size:].values
y_tr = y_home[:train_size]

# 复刻 _train_lgb_poisson
params = {
    'objective': 'poisson', 'metric': 'poisson',
    'max_depth': tm.LAMBDA_MAX_DEPTH, 'learning_rate': tm.LAMBDA_LR,
    'num_leaves': tm.LAMBDA_NUM_LEAVES, 'min_data_in_leaf': 30,
    'feature_fraction': 0.8, 'bagging_fraction': 0.8, 'bagging_freq': 1,
    'reg_alpha': 0.1, 'reg_lambda': 8.0, 'seed': 42, 'verbose': -1,
}
base_score = float(np.log(max(float(np.mean(y_tr)), 1e-6)))
ltr = lgb.Dataset(X_tr, label=y_tr, init_score=np.full(len(y_tr), base_score))
model = lgb.train(params, ltr, num_boost_round=tm.LAMBDA_NUM_BOOST_ROUND)
print(f"base(log mean)={base_score:.6f}  n_trees={model.num_trees()}")
print(f"best_iteration={model.best_iteration}")

rows = [3816, 3550, 3722]
for row in rows:
    x = X_va[row - train_size:row - train_size + 1]
    py_pred = model.predict(x)[0]
    raw = model.predict(x, raw_score=True)[0]
    print(f"\nrow={row}: py_λ={py_pred:.6f} raw_score={raw:.6f} -> log(py)={np.log(py_pred):.6f}")
    print(f"  raw vs base+Σleaf 假设: base+?; raw - base = {raw - base_score:.6f} (若含 init_score)")

    # pred_leaf: py 实际叶子路径
    leaves = model.predict(x, pred_leaf=True)[0]
    dump = model.dump_model()
    trees = dump['tree_info']

    def walk_to_leaf(node, row_vals):
        while 'split_index' in node:
            fidx = int(node['split_feature'])
            fval = float(row_vals[fidx])
            if fval <= node['threshold']:
                node = node['left_child']
            else:
                node = node['right_child']
        return node

    sum_leaf_dump = 0.0
    sum_leaf_by_leafidx = 0.0
    leaf_ids = []
    for ti, tree in enumerate(trees):
        leaf = walk_to_leaf(tree['tree_structure'], x[0])
        lv = float(leaf['leaf_value'])
        sum_leaf_dump += lv
        # 定位 pred_leaf 叶子: DFS 编号
        node_counter = [0]

        def dfs(node):
            cur = node_counter[0]
            node_counter[0] += 1
            if 'split_index' in node:
                dfs(node['left_child'])
                dfs(node['right_child'])
            return cur
        dfs(tree['tree_structure'])
        leaf_ids.append((ti, lv, leaves[ti] if ti < len(leaves) else None))
    print(f"  Σleaf(dump遍历)={sum_leaf_dump:.6f} -> exp(base+Σleaf)={np.exp(base_score + sum_leaf_dump):.6f}")
    print(f"  Σleaf(dump遍历) -> exp(Σleaf)={np.exp(sum_leaf_dump):.6f}")
    # 与 py 对照
    print(f"  对照: py_λ={py_pred:.6f}  exp(base+Σleaf)={np.exp(base_score + sum_leaf_dump):.6f}  "
          f"raw/Σleaf差={raw - sum_leaf_dump:.6f}")
