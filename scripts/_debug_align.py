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
raw = read_sql("SELECT match_id, timestamp, win_a, draw, win_b FROM wdl_history", conn)
print('raw cols', raw.dtypes.to_dict())
print('raw match_id dtype', raw['match_id'].dtype)
print('df match_id dtype', df['match_id'].dtype)

g = raw.groupby('match_id', sort=False)
first = g.nth(0)[['win_a','draw','win_b']]
print('first.index[:3]', list(first.index[:3]))
print('first.index type', type(first.index), first.index.dtype)

mid = df['match_id'].to_numpy()
print('df match_id[:3]', list(mid[:3]))
print('isin test row0:', mid[0] in set(first.index))
print('n matched via isin:', df['match_id'].isin(first.index).sum())

# direct python membership
idxset = set(first.index)
nmatch = sum(1 for m in mid if m in idxset)
print('python membership matched:', nmatch)

# compare a specific known chinese id
sample = '2016-08-13_南安普敦_沃特福德'
print('sample in first.index:', sample in idxset)
print('sample in raw match_id:', sample in set(raw['match_id']))