# -*- coding: utf-8 -*-
"""临时调试3: 多公式对照，确定 count:poisson 下 base_score 与 dump leaf 的精确语义"""
import os, sys
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
mean_tr = float(np.mean(y_home[:train_size]))
base_log = float(np.log(max(mean_tr, 1e-6)))
params = {'objective': 'count:poisson', 'eval_metric': 'poisson-nloglik',
          'max_depth': 3, 'learning_rate': 0.05, 'subsample': 0.8,
          'colsample_bytree': 0.8, 'min_child_weight': 10, 'gamma': 1.0,
          'reg_alpha': 0.1, 'reg_lambda': 8.0, 'seed': 42, 'nthread': -1,
          'base_score': base_log}
dtrain = xgb.DMatrix(X_scaled_df.iloc[:train_size].values, label=y_home[:train_size])
dval = xgb.DMatrix(X_scaled_df.iloc[train_size:].values, label=y_home[train_size:])
model = xgb.train(params, dtrain, num_boost_round=50, evals=[(dval, 'val')],
                  early_stopping_rounds=20, verbose_eval=0)

print(f"mean_tr={mean_tr:.6f}  base_log={base_log:.6f}")

feat = Xr.columns.tolist()
dumps = model.get_dump()
nodes_by_tree = []
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
    nodes_by_tree.append(nodes)

def sum_leaf(row):
    total = 0.0
    for nodes in nodes_by_tree:
        node_map = {nd['node_id']: nd for nd in nodes}
        root = min(nd['node_id'] for nd in nodes)
        node = node_map[root]
        while node is not None and 'split' in node:
            fname = feat[int(node['split']['feature'][1:])]
            fval = float(X_scaled_df.iloc[row][fname])
            nxt = node['split']['left'] if fval < node['split']['threshold'] else node['split']['right']
            node = node_map.get(nxt)
            if node is None: break
        total += node.get('leaf', 0.0) if node else 0.0
    return total

for row in [3, 5, 9, 20]:
    py_margin = model.predict(xgb.DMatrix(X_scaled_df.iloc[[row]].values), output_margin=True)[0]
    sl = sum_leaf(row)
    print(f"\nrow={row}: py_margin={py_margin:.6f} py_λ={np.exp(py_margin):.6f} Σleaf={sl:.6f}")
    print(f"  A: base_log + 0.05*Σleaf = {base_log + 0.05*sl:.6f} (exp={np.exp(base_log+0.05*sl):.6f})")
    print(f"  B: base_log + Σleaf      = {base_log + sl:.6f} (exp={np.exp(base_log+sl):.6f})")
    print(f"  C: log(base_log) + Σleaf = {np.log(abs(base_log)) + sl:.6f}")
    print(f"  D: base_log - 0.05*Σleaf = {base_log - 0.05*sl:.6f}")
    print(f"  E: log(base_log) + 0.05*Σleaf = {np.log(abs(base_log)) + 0.05*sl:.6f}")
