# -*- coding: utf-8 -*-
"""临时调试8: NaN 检查 + LGB 原始 dump 直接遍历"""
import os, sys
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
df_tail = df.tail(1200).reset_index(drop=True)
X, y = build_all_features(df_tail, include_odds=CONFIG.get('training', {}).get('include_odds_features', True),
                          ts_odds=True, consensus_odds=True)
Xr = X.reset_index(drop=True)
df_meta = df_tail.loc[X.index].reset_index(drop=True)
y_home = df_meta['homeGoals'].to_numpy(dtype=np.float64)
scaler = StandardScaler()
X_scaled = scaler.fit_transform(Xr)
X_scaled_df = pd.DataFrame(X_scaled, columns=Xr.columns, index=Xr.index)

nan_cols = X_scaled_df.columns[X_scaled_df.isna().any()].tolist()
print(f"NaN 列数: {len(nan_cols)}", nan_cols[:10])
if nan_cols:
    print("NaN 行数示例:", X_scaled_df[nan_cols[0]].isna().sum(), "列:", nan_cols[0])

train_size = int(len(Xr) * 0.8)
base = float(np.log(max(float(np.mean(y_home[:train_size])), 1e-6)))
lp = {'objective': 'poisson', 'metric': 'poisson', 'max_depth': 3,
      'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 30,
      'feature_fraction': 0.8, 'bagging_fraction': 0.8, 'bagging_freq': 1,
      'reg_alpha': 0.1, 'reg_lambda': 8.0, 'seed': 42, 'verbose': -1}
ltr = lgb.Dataset(X_scaled_df.iloc[:train_size].values, label=y_home[:train_size],
                  init_score=np.full(train_size, base))
lva = lgb.Dataset(X_scaled_df.iloc[train_size:].values, label=y_home[train_size:], reference=ltr)
ml = lgb.train(lp, ltr, num_boost_round=30, valid_sets=[lva],
               callbacks=[lgb.early_stopping(20)])

dump = ml.dump_model()
fnames = dump['feature_names']

def raw_traverse(node, row):
    total = 0.0
    while 'split_index' in node:
        fidx = int(node['split_feature'])
        fn = feat[fidx]
        fval = float(X_scaled_df.iloc[row][fn])
        # LGB: value <= threshold → left
        if fval <= node['threshold']:
            node = node['left_child']
        else:
            node = node['right_child']
    total = float(node['leaf_value'])
    return total

feat = Xr.columns.tolist()
for row in [train_size + i for i in (0, 1, 2)]:
    py = ml.predict(X_scaled_df.iloc[[row]].values)[0]
    s = sum(raw_traverse(t['tree_structure'], row) for t in dump['tree_info'])
    print(f"row={row}: py_λ={py:.6f} log={np.log(py):.6f} 直接遍历 base+Σleaf={base+s:.6f} exp={np.exp(base+s):.6f}")
