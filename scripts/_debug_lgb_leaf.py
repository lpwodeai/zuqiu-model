# -*- coding: utf-8 -*-
"""临时调试10: 用 pred_leaf 精确定位 LGB init_score 语义"""
import numpy as np
import lightgbm as lgb

rng = np.random.default_rng(0)
n = 2000
X = rng.normal(size=(n, 5))
true_lambda = np.exp(0.3 + 0.2 * X[:, 0] - 0.1 * X[:, 1])
y = rng.poisson(true_lambda)

params = {'objective': 'poisson', 'metric': 'poisson', 'max_depth': 3,
          'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 30,
          'verbose': -1, 'seed': 42}
base = float(np.log(max(float(np.mean(y)), 1e-6)))
print("base =", base, " exp(base) =", np.exp(base))

dB = lgb.Dataset(X, label=y, init_score=np.full(n, base))
mB = lgb.train(params, dB, num_boost_round=20)

row = X[:1]
py = mB.predict(row)[0]
leaf_idx = mB.predict(row, pred_leaf=True)[0]
dump = mB.dump_model()

# 按 pred_leaf 收集各树叶值（LGB 叶子序 = 模型内叶子序）
leaves = []
for ti, tree in enumerate(dump['tree_info']):
    leaves.append(tree['tree_structure'])  # 需要按 leaf_index 映射

# 用 dump 直接遍历得到 Σleaf（与模型一致路径）
feat_names = dump['feature_names']

def walk(node):
    while 'split_index' in node:
        fidx = int(node['split_feature'])
        fval = float(row[0][fidx])
        if fval <= node['threshold']:
            node = node['left_child']
        else:
            node = node['right_child']
    return float(node['leaf_value'])

s = sum(walk(t['tree_structure']) for t in dump['tree_info'])
print(f"py = {py:.6f}  log(py) = {np.log(py):.6f}")
print(f"Σleaf(直接遍历) = {s:.6f}")
print(f"log(py) - Σleaf = {np.log(py) - s:+.6f}")
print(f"Σleaf + base = {s + base:.6f}")
print(f"Σleaf - base = {s - base:.6f}")
# LGB 模型自身 base 相关属性
for attr in ('objective', 'average_output', 'monotone_constraints'):
    print(f"  model attr {attr}:", getattr(mB, attr, None))
