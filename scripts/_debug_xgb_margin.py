# -*- coding: utf-8 -*-
"""临时调试2: XGB margin vs dump 全树重建"""
import os, sys, pickle
BASE = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
sys.path.insert(0, os.path.join(BASE, "scripts"))
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
from feature_utils import load_config, load_match_data_odds, build_all_features

CONFIG = load_config()
df = load_match_data_odds()
df_tail = df.tail(2000).reset_index(drop=True)
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
          'base_score': base}
dtrain = xgb.DMatrix(X_scaled_df.iloc[:train_size].values, label=y_home[:train_size])
dval = xgb.DMatrix(X_scaled_df.iloc[train_size:].values, label=y_home[train_size:])
model = xgb.train(params, dtrain, num_boost_round=50, evals=[(dval, 'val')],
                  early_stopping_rounds=20, verbose_eval=0)

row = 5
Xrow = X_scaled_df.iloc[[row]].values
py = model.predict(xgb.DMatrix(Xrow))[0]
margin = model.predict(xgb.DMatrix(Xrow), output_margin=True)[0]
print(f"base(log-mean)={base:.6f}  exp(base)={np.exp(base):.6f}")
print(f"py_λ={py:.6f}  py_margin={margin:.6f}  exp(py_margin)={np.exp(margin):.6f}")

# 全树重建 margin（leaf 原值，不乘 lr），对比 py_margin
feat = Xr.columns.tolist()
dumps = model.get_dump()
sum_leaf = 0.0
for tree_str in dumps:
    nodes = []
    for line in tree_str.split('\n'):
        line = line.strip()
        if not line: continue
        node_id = int(line.split(':')[0])
        if 'leaf=' in line:
            nodes.append({'node_id': node_id, 'leaf': float(line.split('leaf=')[1].strip())})
        else:
            parts = line.split('[')
            cond = parts[1].split(']')[0]
            feature, rest = cond.split('<')
            goto = parts[1].split(']')[1]
            nodes.append({'node_id': node_id, 'split': {
                'feature': feature.strip(), 'threshold': float(rest.strip()),
                'left': int(goto.split('yes=')[1].split(',')[0]),
                'right': int(goto.split('no=')[1].split(',')[0])}})
    node_map = {nd['node_id']: nd for nd in nodes}
    root = min(nd['node_id'] for nd in nodes)
    node = node_map[root]
    while node is not None and 'split' in node:
        fname = feat[int(node['split']['feature'][1:])]
        fval = float(X_scaled_df.iloc[row][fname])
        nxt = node['split']['left'] if fval < node['split']['threshold'] else node['split']['right']
        node = node_map.get(nxt)
        if node is None: break
    sum_leaf += node.get('leaf', 0.0) if node else 0.0

print(f"Σleaf(全{len(dumps)}树)={sum_leaf:.6f}")
print(f"base + lr*Σleaf = {base + 0.05*sum_leaf:.6f}  exp={np.exp(base + 0.05*sum_leaf):.6f}")
print(f"margin 重建(base+lr*Σleaf) vs py_margin: {base + 0.05*sum_leaf:.6f} vs {margin:.6f}")
print(f"若 base_score 按 λ 空间解释: log(base)={np.log(np.exp(base)):.6f}, log(base)+lr*Σleaf={np.log(np.exp(base)) + 0.05*sum_leaf:.6f}")
# 树0 前 3 棵 leaf 值范围
for i in range(3):
    leaves = [float(l.split('leaf=')[1]) for l in dumps[i].split('\n') if 'leaf=' in l]
    print(f"tree{i} leaves: min={min(leaves):.6f} max={max(leaves):.6f} n={len(leaves)}")
