"""临时脚本：dump 当前 build_all_features 全部 254 列名，定位 46 个额外列（22 sofa + 24 非sofa）来源"""
import os
import sys
import pickle
import json

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
ROOT = os.path.dirname(_SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features

print("=" * 70)
print("[定位] 46 个额外列来源")
print("=" * 70)

# 训练期 selected_features
feat_pkl = os.path.join(ROOT, 'assets', 'selected_features_20260828_174103.pkl')
features = pickle.load(open(feat_pkl, 'rb'))
feat_set = set(features)
print(f"训练 selected_features: {len(features)}")

# 当前矩阵
df = load_match_data_odds()
X_all, _ = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
cols = list(X_all.columns)
print(f"当前 X_all: {len(cols)} 列")

extra = [c for c in cols if c not in feat_set]
missing = [f for f in features if f not in set(cols)]
print(f"额外(新增未训练): {len(extra)} ; 缺失(训练有当前无): {len(missing)}")

sofa_extra = [c for c in extra if 'sofa' in c.lower()]
non_sofa_extra = [c for c in extra if 'sofa' not in c.lower()]
print(f"\n新增 sofa_*: {len(sofa_extra)} 个")
for c in sofa_extra:
    print(f"   {c}")
print(f"\n新增 非sofa: {len(non_sofa_extra)} 个")
for c in non_sofa_extra:
    print(f"   {c}")

# 非sofa 按前缀归类，便于定位模块
from collections import Counter
prefix_counter = Counter()
for c in non_sofa_extra:
    prefix_counter[c.split('_')[0]] += 1
print(f"\n非sofa 新增列按前缀归类: {dict(prefix_counter)}")

# 保存完整列名到文件，避免重复构建
out = {
    'all_254_cols': cols,
    'train_208': features,
    'extra_46': extra,
    'sofa_extra_22': sofa_extra,
    'non_sofa_extra_24': non_sofa_extra,
}
out_path = os.path.join(ROOT, 'logs', '_tmp_extra_cols.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"\n[已保存] {out_path}")
print("[完成]")