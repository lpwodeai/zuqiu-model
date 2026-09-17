"""P0-2: 特征维度对齐验证 — 训练/推理特征名一致性检查"""
import pickle, os, sys

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')

# 1. Load selected_features
with open(os.path.join(BASE, 'selected_features_20260815_215015.pkl'), 'rb') as f:
    sf = pickle.load(f)
print(f'=== selected_features: {len(sf)} 维 ===')

# 2. Load XGBoost
with open(os.path.join(BASE, 'xgb_model_20260815_215015.pkl'), 'rb') as f:
    xgb = pickle.load(f)
xgb_ft = xgb.feature_names if hasattr(xgb, 'feature_names') else None
if xgb_ft is None and hasattr(xgb, 'get_booster'):
    xgb_ft = xgb.get_booster().feature_names
print(f'=== XGBoost: {len(xgb_ft) if xgb_ft else "N/A"} 维 ===')

# 3. Load LightGBM
with open(os.path.join(BASE, 'lgb_model_20260815_215015.pkl'), 'rb') as f:
    lgb = pickle.load(f)
lgb_ft = None
if hasattr(lgb, 'feature_name_'):
    lgb_ft = lgb.feature_name_
elif hasattr(lgb, 'booster_'):
    lgb_ft = lgb.booster_.feature_name()
print(f'=== LightGBM: {len(lgb_ft) if lgb_ft else "N/A"} 维 ===')

# 4. Comparison
print('\n=== 对齐验证 ===')
print(f'selected_features     : {len(sf)}')
print(f'XGBoost feature_names : {len(xgb_ft) if xgb_ft else "N/A"}')
print(f'LightGBM feature_name_: {len(lgb_ft) if lgb_ft else "N/A"}')

if xgb_ft:
    xgb_match = list(sf) == list(xgb_ft)
    print(f'\nselected_features == XGBoost: {xgb_match}')
    if not xgb_match:
        only_sf = set(sf) - set(xgb_ft)
        only_xgb = set(xgb_ft) - set(sf)
        if only_sf: print(f'  仅在 selected_features 中: {only_sf}')
        if only_xgb: print(f'  仅在 XGBoost 中: {only_xgb}')

if lgb_ft:
    lgb_match = list(sf) == list(lgb_ft)
    print(f'selected_features == LightGBM: {lgb_match}')
    if not lgb_match:
        only_sf = set(sf) - set(lgb_ft)
        only_lgb = set(lgb_ft) - set(sf)
        if only_sf: print(f'  仅在 selected_features 中: {only_sf}')
        if only_lgb: print(f'  仅在 LightGBM 中: {only_lgb}')

if xgb_ft and lgb_ft:
    print(f'XGBoost == LightGBM: {list(xgb_ft) == list(lgb_ft)}')

# 5. 打印前5后5个特征名
if sf:
    print(f'\n前5个特征: {sf[:5]}')
    print(f'后5个特征: {sf[-5:]}')

# 6. 结论
print('\n=== 结论 ===')
all_ok = True
if xgb_ft and len(sf) != len(xgb_ft):
    print(f'❌ selected_features ({len(sf)}) != XGBoost ({len(xgb_ft)})')
    all_ok = False
if lgb_ft and len(sf) != len(lgb_ft):
    print(f'❌ selected_features ({len(sf)}) != LightGBM ({len(lgb_ft)})')
    all_ok = False
if all_ok:
    print(f'✅ 训练端特征维度一致: {len(sf)} 维')
    if xgb_ft and list(sf) == list(xgb_ft):
        print('✅ selected_features 与 XGBoost 特征名完全一致')
    if lgb_ft and list(sf) == list(lgb_ft):
        print('✅ selected_features 与 LightGBM 特征名完全一致')