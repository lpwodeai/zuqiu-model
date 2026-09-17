# -*- coding: utf-8 -*-
import sys, warnings
warnings.filterwarnings('ignore')
import pandas as pd, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_utils as F
from feature_utils import load_odds_database, read_sql

df = F.load_match_data_odds()
conn = load_odds_database()
cols = ['goals_0','goals_1','goals_2','goals_3','goals_4','goals_5','goals_6','goals_7_plus']
raw = read_sql(f"SELECT match_id, timestamp, {', '.join(cols)} FROM total_goals_history", conn)
raw = raw.sort_values(['match_id','timestamp'], kind='mergesort')
last = raw.drop_duplicates(subset='match_id', keep='last').set_index('match_id')[cols]

keys = last.index.astype(object)
last2 = last.copy(); last2.index = keys
mid = pd.Index(df['match_id'].astype(object))

print('last.index dtype:', last.index.dtype, '-> keys dtype:', keys.dtype)
print('mid dtype:', mid.dtype)
print('df match_id dtype:', df['match_id'].dtype)

# reindex 方法
r = last2.reindex(mid)
rn = r.to_numpy(dtype=float)
print('reindex shape:', rn.shape)

# 统计 reindex 命中数（非全NaN行）
nonnan = ~np.isnan(rn).all(axis=1)
print('reindex 命中行数:', nonnan.sum())

# 用 isin 命中数
print('isin 命中数:', mid.isin(keys).sum())

# 用 python set 命中数
keyset = set(keys.tolist())
print('python set 命中数:', sum(1 for m in mid.tolist() if m in keyset))

# 直接抽一个 row 看 reindex 是否命中
sample = df['match_id'].iloc[116]
print('row116 match_id:', repr(sample))
print('row116 in keyset:', sample in keyset)
print('row116 last.loc 值:', last2.loc[sample].tolist() if sample in keyset else 'NOT FOUND')

# 检查 sample 与 keys 中某个元素是否相等但类型不同
if sample in keyset:
    for k in keyset:
        if k == sample:
            print('equal key repr:', repr(k), 'type:', type(k), '== sample', k == sample)
            print('sample type:', type(sample))
            break