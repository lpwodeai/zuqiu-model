# -*- coding: utf-8 -*-
"""调试: 当前导出资产 lambda_model_export.js 的 lgb_home 树遍历与 py 一致性。"""
import os
import re
import json
import sys

BASE = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
sys.path.insert(0, os.path.join(BASE, "scripts"))

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from feature_utils import load_config, load_match_data_odds, build_all_features

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
feat = Xr.columns.tolist()

# 加载导出资产
p = os.path.join(BASE, "assets", "lambda_model_export.js")
s = open(p, encoding="utf-8").read()
m = re.search(r'=\s*(\{.*\})\s*;?\s*$', s, re.S)
d = json.loads(m.group(1))
lgb_home = d['models']['lgb_home']
print(f"lgb_home: base={lgb_home['base']:.6f} lr={lgb_home.get('lr')} n_trees={len(lgb_home['trees'])}")

def walk(tree, row):
    node_map = {nd['node_id']: nd for nd in tree['nodes']}
    root = min(nd['node_id'] for nd in tree['nodes'])
    node = node_map[root]
    path = []
    while node is not None and 'split' in node:
        fn = node['split']['feature']
        if fn.startswith('f') and fn[1:].isdigit():
            fname = feat[int(fn[1:])]
        else:
            fname = fn
        fval = float(X_scaled_df.iloc[row][fname])
        path.append((node['node_id'], fn, round(node['split']['threshold'], 5), round(fval, 5)))
        nxt = node['split']['left'] if fval < node['split']['threshold'] else node['split']['right']
        node = node_map.get(nxt)
        if node is None:
            path.append(('MISSING_NODE', nxt))
            break
    return node, path

row = 3816
# 抽样遍历: 每棵树统计是否命中叶子、是否断链
miss_cnt = 0
leaf_cnt = 0
paths = []
for ti, tree in enumerate(lgb_home['trees'][:10]):
    node, path = walk(tree, row)
    if node is None:
        miss_cnt += 1
    else:
        leaf_cnt += 1
    paths.append((ti, node, path[:5]))
print(f"\n前10棵树: 命中叶子={leaf_cnt} 断链(MISSING_NODE)={miss_cnt}")
for ti, node, path in paths:
    print(f"  tree{ti}: leaf={None if node is None else round(node.get('leaf', 0), 6)} path={path}")

# 重建 Σleaf
total = lgb_home['base']
sum_leaf = 0.0
for tree in lgb_home['trees']:
    node, _ = walk(tree, row)
    leaf = node.get('leaf', 0.0) if node else 0.0
    sum_leaf += leaf
print(f"\n重建: base={lgb_home['base']:.6f} Σleaf={sum_leaf:.6f} λ=exp(base+Σleaf)={np.exp(lgb_home['base'] + sum_leaf):.6f}")
