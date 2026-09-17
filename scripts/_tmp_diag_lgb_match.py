"""诊断 WDL LGB/XGB 队名匹配问题"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
from feature_utils import load_match_data_odds, normalize_team_name, build_all_features

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
print("=== 1. load_match_data_odds 返回的 df 列名 ===")
df = load_match_data_odds()
print(f"  列: {list(df.columns)}")

print("\n=== 2. df 中包含 Inter/国际米兰 的行 ===")
# 旧赛季用英文找：
mask_en = (df['home_team_name'].apply(lambda x: 'Inter' in str(x)))
if mask_en.sum() > 0:
    print(f"  旧赛季(含Inter): {mask_en.sum()} 条，最近1条: {df[mask_en].iloc[-1][['date','home_team_name','away_team_name']].to_dict()}")
# 新赛季用中文找：
mask_cn = (df['home_team_name'].apply(lambda x: '国际米兰' in str(x)))
if mask_cn.sum() > 0:
    print(f"  新赛季(含国际米兰): {mask_cn.sum()} 条，最近1条: {df[mask_cn].iloc[-1][['date','home_team_name','away_team_name']].to_dict()}")

print("\n=== 3. 英文→中文 normalize 测试 ===")
for name in ['Inter', 'SSC Napoli', 'Genoa', 'Udinese', 'Parma', 'Cagliari', 'Monza', 'Como', 'AS Roma', 'AC Milan']:
    print(f"  {name:15s} -> {normalize_team_name(name)}")

print("\n=== 4. 构建特征后，用传入的英文队名匹配 ===")
# 模拟 WDLPredictor 中的操作：
X_all, y_all = build_all_features(df, include_odds=True)
home_team_en = 'Inter'
away_team_en = 'Monza'
mask1 = (df['home_team_name'] == home_team_en) | (df['away_team_name'] == away_team_en)
print(f"  匹配 Inter/Monza (英文): {mask1.sum()} 条")

home_team_cn = normalize_team_name(home_team_en)
away_team_cn = normalize_team_name(away_team_en)
mask2 = (df['home_team_name'] == home_team_cn) | (df['away_team_name'] == away_team_cn)
print(f"  匹配 {home_team_cn}/{away_team_cn} (中文): {mask2.sum()} 条")
if mask2.sum() > 0:
    idx = df[mask2].index[-1]
    print(f"    最近匹配索引: idx={idx}, len(X_all)={len(X_all)}, X_match ok = {idx < len(X_all)}")
