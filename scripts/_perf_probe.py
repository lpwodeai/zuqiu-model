# -*- coding: utf-8 -*-
"""临时：分阶段计时，定位 build_team_features 剩余瓶颈。"""
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feature_utils import (
    load_match_data_odds, get_global_league_stats, precompute_team_stats,
    _h2h_meetings_index,
)

df = load_match_data_odds()
print(f"数据 {len(df)} 场", flush=True)

t0 = time.time()
gls = get_global_league_stats(df)
print(f"get_global_league_stats: {time.time()-t0:.2f}s", flush=True)

t0 = time.time()
cache, ls, gs = precompute_team_stats(df)
print(f"precompute_team_stats: {time.time()-t0:.2f}s (teams={len(cache)})", flush=True)

t0 = time.time()
h2h = _h2h_meetings_index(df)
print(f"_h2h_meetings_index: {time.time()-t0:.2f}s (pairs={len(h2h)})", flush=True)