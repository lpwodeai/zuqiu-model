# -*- coding: utf-8 -*-
"""临时：验证 xg_deep_features 模块（P1-8-①）"""
import sys, os, time
sys.stdout.reconfigure(line_buffering=True)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from feature_utils import load_match_data_odds
from xg_deep_features import build_xg_deep_features, FEATURE_COLS

t0 = time.time()
df = load_match_data_odds()
print(f"训练 df: {len(df)} 场 | 加载 {time.time()-t0:.1f}s")

t0 = time.time()
X = build_xg_deep_features(df)
print(f"xG 深度特征: {X.shape[1]} 维 | 构建 {time.time()-t0:.1f}s")
print("列:", X.columns.tolist())

for c in FEATURE_COLS:
    non_missing = (X[c] != -1.0).mean() * 100
    print(f"  {c:24s} 非缺失覆盖率 = {non_missing:.1f}% | 均值={X[c][X[c]!=-1.0].mean():.4f} | 范围=[{X[c][X[c]!=-1.0].min():.4f}, {X[c][X[c]!=-1.0].max():.4f}]")

# 对齐性检查
import numpy as np
print("\n样本前 3 行:")
print(X.head(3).to_string())