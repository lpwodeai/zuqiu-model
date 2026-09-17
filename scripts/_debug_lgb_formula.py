# -*- coding: utf-8 -*-
"""临时调试4: LGB poisson 约定验证（init_score=log(mean) → js: base + 1.0*Σleaf）"""
import os, sys
BASE = r"f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
sys.path.insert(0, os.path.join(BASE, "scripts"))
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.preprocessing import StandardScaler
from feature_utils import load_config, load_match_data_odds, build_all_features

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
params = {'objective': 'poisson', 'metric': 'poisson', 'max_depth': 3,
          'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 30,
          'feature_fraction': 0.8, 'bagging_fraction': 0.8, 'bagging_freq': 1,
          'reg_alpha': 0.1, 'reg_lambda': 8.0, 'seed': 42, 'verbose': -1}
ltr = lgb.Dataset(X_scaled_df.iloc[:train_size].values, label=y_home[:train_size],
                  init_score=np.full(train_size, base))
lva = lgb.Dataset(X_scaled_df.iloc[train_size:].values, label=y_home[train_size:], reference=ltr)
model = lgb.train(params, ltr, num_boost_round=30, valid_sets=[lva],
                  callbacks=[lgb.early_stopping(20)])

feat = Xr.columns.tolist()
dump = model.dump_model()
fnames = dump['feature_names']
print("tree0 keys:", list(dump['tree_info'][0]['tree_structure'].keys()))
import json
print("tree0 structure:", json.dumps(dump['tree_info'][0]['tree_structure'], default=str)[:600])
for row in [3, 5, 9]:
    py = model.predict(X_scaled_df.iloc[[row]].values)[0]
    py_margin = np.log(py)
    # 重建: base + Σleaf
    total = base
    for tree in dump['tree_info']:
        node = tree['tree_structure']
        while 'split_index' in node:
            fn = fnames[node['split_index']]
            if fn.startswith('Column_'):
                fn = feat[int(fn.split('_')[1])]
            fval = float(X_scaled_df.iloc[row][fn])
            node = node['left_child'] if fval < node['threshold'] else node['right_child']
        total += float(node['leaf_value'])
    print(f"row={row}: py_λ={py:.6f} py_margin={py_margin:.6f} 重建(base+Σleaf)={total:.6f} "
          f"exp={np.exp(total):.6f} diff={abs(py_margin-total):.2e}")
