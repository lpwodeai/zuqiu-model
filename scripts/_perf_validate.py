# -*- coding: utf-8 -*-
"""临时验证脚本：对比优化前后 build_team_features 的 h2h 统计是否一致，并计时。"""
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feature_utils import (
    load_match_data_odds, build_team_features, calc_h2h_stats,
    _h2h_meetings_index, _h2h_stats_from_records,
)

print("加载数据 ...")
t0 = time.time()
df = load_match_data_odds()
print(f"  加载完成: {len(df)} 场, 用时 {time.time()-t0:.1f}s")

# 1. 正确性对比：采样若干场，对比旧 calc_h2h_stats vs 新 _h2h_stats_from_records
print("\n[正确性] 对比 h2h 统计（采样 500 场）...")
h2h_index = _h2h_meetings_index(df)
mismatch = 0
sample = df.sample(n=min(500, len(df)), random_state=42)
for _, row in sample.iterrows():
    home, away, md = row['home_team_name'], row['away_team_name'], row['date']
    old = calc_h2h_stats(df, home, away, md)
    recs = [r for r in h2h_index.get(frozenset((home, away)), ()) if r.date < md][-10:]
    new = _h2h_stats_from_records(recs, home)
    if set(old.keys()) != set(new.keys()):
        mismatch += 1
        print(f"  键不一致: {home} vs {away} {md} -> old keys {set(old)-set(new)}, new keys {set(new)-set(old)}")
        continue
    for k in old:
        ov, nv = old[k], new[k]
        # NaN 视为相等
        if isinstance(ov, float) and isinstance(nv, float):
            import math
            if math.isnan(ov) and math.isnan(nv):
                continue
        if ov != nv:
            mismatch += 1
            print(f"  不一致 [{k}]: old={ov!r} new={nv!r} | {home} vs {away} @ {md}")
            break
print(f"  完成。不一致样本数: {mismatch}")

# 2. 计时 build_team_features
print("\n[计时] build_team_features ...")
t0 = time.time()
tf = build_team_features(df)
print(f"  完成: shape={tf.shape}, 用时 {time.time()-t0:.1f}s")