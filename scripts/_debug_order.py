# -*- coding: utf-8 -*-
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_utils as F
from feature_utils import load_odds_database, read_sql

conn = load_odds_database()
df = F.load_match_data_odds()
cols = ['goals_0','goals_1','goals_2','goals_3','goals_4','goals_5','goals_6','goals_7_plus']
raw = read_sql(f"SELECT match_id, timestamp, {', '.join(cols)} FROM total_goals_history", conn)
raw = raw.sort_values(['match_id','timestamp'], kind='mergesort')
first = raw.drop_duplicates(subset='match_id', keep='first').set_index('match_id')[cols]
last  = raw.drop_duplicates(subset='match_id', keep='last').set_index('match_id')[cols]
fi = list(first.index.astype(object))
li = list(last.index.astype(object))
print("first==last order:", fi == li)
print("len first:", len(fi), "len last:", len(li))
n_diff = sum(1 for a,b in zip(fi,li) if a!=b)
print("order diff count:", n_diff)
conn.close()