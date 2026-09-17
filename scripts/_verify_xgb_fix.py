"""Q5: XGB fix verification + feature export (快速版, 跳过完整特征构建)"""
import json, os, sys, warnings, csv, time
import numpy as np
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from prediction_core import init_models
from feature_utils import normalize_team_name, load_match_data_odds, build_all_features
import pandas as pd

print("=== 1. 加载模型 ===")
models = init_models()
wdl_models = models['wdl']
lgb = wdl_models['lgb_model']
xgb_model = wdl_models['xgb_model']
scaler = wdl_models['scaler']
features = wdl_models['features']
print(f"LGB: {type(lgb).__name__}, XGB: {type(xgb_model).__name__}, Scaler: {scaler.n_features_in_}d, Features: {len(features)}")

print("\n=== 2. 加载特征矩阵 ===")
df = load_match_data_odds()
print(f"加载: {len(df)} 场比赛")

# 只构建特征矩阵 (不包含赔率, 更快)
print("构建特征矩阵 (slim_odds=True)...")
X, _ = build_all_features(df, include_odds=True, slim_odds=True)
print(f"特征矩阵: {X.shape}")

# 测试单场: 埃弗顿 vs 水晶宫
print("\n=== 3. 测试 XGB 推理: 埃弗顿 vs 水晶宫 ===")
home_norm = normalize_team_name('埃弗顿')
away_norm = normalize_team_name('水晶宫')

# 在特征矩阵中找匹配
mask = (df['home_team_name'] == home_norm) | (df['away_team_name'] == away_norm)
match_idx = df[mask].index[-1] if mask.sum() > 0 else -1
print(f"匹配行数: {mask.sum()}, 使用索引: {match_idx}")

available = [f for f in features if f in X.columns]
missing = [f for f in features if f not in X.columns]
print(f"可用特征: {len(available)}/{len(features)}, 缺失: {len(missing)}")
if missing[:5]:
    print(f"缺失特征 (前5): {missing[:5]}")

# Scaler期望的特征名
expected_features = list(scaler.feature_names_in_)
actual_features = list(X.columns)

# 用Scaler的feature_names_in_来对齐
X_vec = np.zeros((1, len(expected_features)))
for j, feat in enumerate(expected_features):
    if feat in X.columns:
        X_vec[0, j] = X.iloc[match_idx][feat] if match_idx < len(X) else 0
    else:
        X_vec[0, j] = 0

X_scaled = scaler.transform(X_vec)

# LGB
print("\nLGB 推理:")
lgb_raw = lgb.predict(X_scaled)[0]
print(f"  Raw: {lgb_raw}")
print(f"  Win={lgb_raw[2]*100:.1f}% Draw={lgb_raw[1]*100:.1f}% Lose={lgb_raw[0]*100:.1f}%")

# XGB (修复后)
print("\nXGB 推理 (修复后):")
try:
    import xgboost as _xgb
    xgb_dmat = _xgb.DMatrix(X_scaled)
    xgb_raw = xgb_model.predict(xgb_dmat)[0]
    print(f"  Raw: {xgb_raw}")
    print(f"  Win={xgb_raw[2]*100:.1f}% Draw={xgb_raw[1]*100:.1f}% Lose={xgb_raw[0]*100:.1f}%")
    print("  ✅ XGB 推理成功!")
    xgb_status = 'OK'
except Exception as e:
    print(f"  ❌ 失败: {e}")
    import traceback; traceback.print_exc()
    xgb_status = f'Error: {e}'

# 导出特征到CSV
print("\n=== 4. 导出特征数据 ===")
fieldnames = ['feature_index', 'feature_name', 'raw_value', 'scaled_value']
csv_path = os.path.join(PROJECT_DIR, 'data', 'features_test_everton_20260823.csv')
with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.writer(f)
    writer.writerow(fieldnames)
    for j, feat in enumerate(expected_features):
        writer.writerow([j, feat, X_vec[0, j], X_scaled[0, j]])
print(f"特征已导出: {csv_path} ({len(expected_features)} 维)")
print(f"  - 非零特征: {np.count_nonzero(X_vec)}/{len(expected_features)}")

# 汇总
print(f"\n=== 5. XGB 修复验证结果 ===")
print(f"  XGB 状态: {xgb_status}")
print(f"  LGB: Win={lgb_raw[2]*100:.1f}% Draw={lgb_raw[1]*100:.1f}% Lose={lgb_raw[0]*100:.1f}%")
if xgb_status == 'OK':
    print(f"  XGB: Win={xgb_raw[2]*100:.1f}% Draw={xgb_raw[1]*100:.1f}% Lose={xgb_raw[0]*100:.1f}%")
    print(f"  差异: Win={abs(lgb_raw[2]-xgb_raw[2])*100:.1f}pp Draw={abs(lgb_raw[1]-xgb_raw[1])*100:.1f}pp Lose={abs(lgb_raw[0]-xgb_raw[0])*100:.1f}pp")