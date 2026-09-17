# -*- coding: utf-8 -*-
"""综合校验：新向量化 build_odds_features vs legacy，逐列对比（关闭 winsorize）。"""
import sys, warnings, time
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_utils as F

df = F.load_match_data_odds()
F.winsorize_series = lambda s, lower_percentile=1, upper_percentile=99: s

t0=time.time(); old = F._build_odds_features_legacy(df); print("[legacy]", round(time.time()-t0,2), "s", flush=True)
t0=time.time(); new = F.build_odds_features(df); print("[new]", round(time.time()-t0,2), "s", flush=True)

cols = [c for c in new.columns if c in old.columns]
print(f"\n{'column':28s} {'diff':>6s}  note")
print("-"*60)
for c in cols:
    a = old[c].to_numpy(dtype=float)
    b = new[c].to_numpy(dtype=float)
    diff = int(np.sum(~np.isclose(a, b, rtol=1e-6, atol=1e-6, equal_nan=True)))
    note = ""
    if diff and c.startswith('tg'):
        # 这些差异是否全部来自「末条记录 goals_3..7 含 NULL」的部分数据行
        note = "  <-- tg部分NULL行"
    print(f"{c:28s} {diff:>6d}{note}")

# 精确定量：tg 各列的差异行是否与「partial-NaN」行一致
# 部分NaN行 = 末条 tg 行 goals_3..7 有 NULL
cols8 = ['goals_0','goals_1','goals_2','goals_3','goals_4','goals_5','goals_6','goals_7_plus']
conn = F.load_odds_database()
from feature_utils import read_sql
raw = read_sql(f"SELECT match_id, timestamp, {', '.join(cols8)} FROM total_goals_history", conn)
raw = raw.sort_values(['match_id','timestamp'], kind='mergesort')
last = raw.drop_duplicates(subset='match_id', keep='last').set_index('match_id')[cols8]
last['partial'] = last[['goals_3','goals_4','goals_5','goals_6','goals_7_plus']].isna().any(axis=1)
partial_ids = set(last.index[last['partial']])
mid = df['match_id'].astype(object)
is_partial = np.array([m in partial_ids for m in mid])

for c in ['tg_under_25_prob','tg_over_25_prob','tg_most_likely','tg_most_likely_prob','tg_expected']:
    a = old[c].to_numpy(dtype=float); b = new[c].to_numpy(dtype=float)
    diff = np.where(~np.isclose(a,b,rtol=1e-6,atol=1e-6,equal_nan=True))[0]
    non_partial_diff = [i for i in diff if not is_partial[i]]
    print(f"{c}: total_diff={len(diff)} diff_non_partial={len(non_partial_diff)} partial_rows_in_data={int(is_partial.sum())}")
    if non_partial_diff:
        print("  !! 存在非partial差异行:", non_partial_diff[:5])