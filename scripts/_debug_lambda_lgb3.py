# -*- coding: utf-8 -*-
"""确认: 生产路径 _train_lgb_poisson (valid_sets+early_stopping) 下 predict 是否含 init_score。"""
import os
import sys

BASE = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
sys.path.insert(0, os.path.join(BASE, "scripts"))

import numpy as np
import pandas as pd
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

n_total = len(X_scaled_df)
train_size = int(n_total * 0.8)
X_tr, X_va = Xr.iloc[:train_size].values, Xr.iloc[train_size:].values
yh_tr = y_home[:train_size]

model, base, lr = tm._train_lgb_poisson(X_tr, yh_tr, X_va, y_va=y_home[train_size:])
print(f"生产路径: base={base:.6f} lr={lr} n_trees={model.num_trees()} best_iteration={model.best_iteration}")

rows = [3816, 3550, 3722]
feat = Xr.columns.tolist()
for row in rows:
    x = X_scaled_df.iloc[[row]].values
    py_pred = model.predict(x)[0]
    raw = model.predict(x, raw_score=True)[0]
    print(f"\nrow={row}: py_λ={py_pred:.6f} raw_score={raw:.6f} log(py)={np.log(py_pred):.6f}")
    print(f"  含init_score假设 exp(base+raw)={np.exp(base + raw):.6f} | 不含 exp(raw)={np.exp(raw):.6f}")

    # 逐树 dump 重建 Σleaf
    dump = model.dump_model()

    def walk(node, row_vals):
        while 'split_index' in node:
            fidx = int(node['split_feature'])
            fval = float(row_vals[fidx])
            if fval <= node['threshold']:
                node = node['left_child']
            else:
                node = node['right_child']
        return float(node['leaf_value'])

    sum_leaf = sum(walk(t['tree_structure'], x[0]) for t in dump['tree_info'])
    print(f"  Σleaf(dump)={sum_leaf:.6f} | raw-Σleaf={raw - sum_leaf:.2e}")

# 也验证 LGB 的 leaf 是否含学习率: 用 pred_leaf 定位 + dump 单树
x = X_scaled_df.iloc[[3816]].values
leaves = model.predict(x, pred_leaf=True)[0]
dump = model.dump_model()
print(f"\npred_leaf[0]={leaves[0]} n_trees={len(dump['tree_info'])}")

def dfs_idx(node, target, counter):
    cur = counter[0]; counter[0] += 1
    if cur == target:
        return node
    if 'split_index' in node:
        r = dfs_idx(node['left_child'], target, counter)
        if r is not None:
            return r
        return dfs_idx(node['right_child'], target, counter)
    return None

# 前 3 棵树: dump leaf vs 实际
for ti in range(3):
    t = dump['tree_info'][ti]
    lv_leaf = walk(t['tree_structure'], x[0])
    target_leaf = None
    counter = [0]
    def dfs_find(node, target, counter):
        cur = counter[0]; counter[0] += 1
        if cur == target:
            return node
        if 'split_index' in node:
            r = dfs_find(node['left_child'], target, counter)
            if r is not None:
                return r
            return dfs_find(node['right_child'], target, counter)
        return None
    target_leaf = dfs_find(t['tree_structure'], int(leaves[ti]), counter)
    print(f"  tree{ti}: dump遍历leaf={lv_leaf:.6f} pred_leaf={leaves[ti]} 该叶leaf_value={target_leaf['leaf_value'] if target_leaf else '?'}")
