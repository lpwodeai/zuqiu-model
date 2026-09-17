# -*- coding: utf-8 -*-
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_utils as F

df = F.load_match_data_odds()
sub = df.iloc[[116]].reset_index(drop=True)
print("match_id:", sub['match_id'].iloc[0])
out = F.build_odds_features(sub)
for c in ['tg_under_25_prob','tg_over_25_prob','tg_most_likely','tg_most_likely_prob','tg_expected','tg_record_count','has_tg_odds']:
    print("  %s = %s" % (c, out[c].iloc[0]))