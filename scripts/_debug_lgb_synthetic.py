# -*- coding: utf-8 -*-
"""临时调试9: LGB init_score 是否参与 predict（合成数据验证）"""
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
print("mean(y) =", np.mean(y), " base =", base)

dA = lgb.Dataset(X, label=y)
mA = lgb.train(params, dA, num_boost_round=20)
dB = lgb.Dataset(X, label=y, init_score=np.full(n, base))
mB = lgb.train(params, dB, num_boost_round=20)

pa = mA.predict(X[:5])
pb = mB.predict(X[:5])
print("A(无init) predict:", pa)
print("B(有init) predict:", pb)
print("A 对数:", np.log(pa))
print("B 对数:", np.log(pb))
print("A vs B 对数差:", np.log(pa) - np.log(pb))
print("exp(base) =", np.exp(base))
