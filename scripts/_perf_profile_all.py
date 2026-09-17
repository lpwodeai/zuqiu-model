# -*- coding: utf-8 -*-
"""临时profiling：逐模块计时 build_all_features 各特征模块（同 prediction_core 调用口径）。"""
import sys, time
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_utils import load_match_data_odds, build_features, build_team_features, build_odds_features_slim, build_draw_enhanced_features

def t(step, fn):
    t0 = time.time()
    r = fn()
    dt = time.time() - t0
    shape = getattr(r, 'shape', None)
    print(f"[{dt:7.2f}s] {step}  shape={shape}", flush=True)
    return r

df = load_match_data_odds()
print(f"df: {df.shape}", flush=True)

res = {}
res['basic'] = t('build_features', lambda: build_features(df))
res['team'] = t('build_team_features', lambda: build_team_features(df))
res['elo'] = t('build_elo_features', lambda: __import__('elo_rating').build_elo_features(df))
res['odds_slim'] = t('build_odds_features_slim', lambda: build_odds_features_slim(df))
res['d013'] = t('build_d013_features', lambda: __import__('d013_temporal_odds').build_d013_features(df))
res['score'] = t('build_score_features', lambda: __import__('score_features').build_score_features(df))

# 累积 X，供 draw_enhanced / consensus 依赖
X = pd.concat([res['basic'], res['team'], res['elo'], res['odds_slim'], res['d013'], res['score']], axis=1)
print(f"[累积] X={X.shape}", flush=True)
res['draw'] = t('build_draw_enhanced_features', lambda: build_draw_enhanced_features(X))
res['consensus'] = t('build_odds_consensus_features', lambda: __import__('odds_consensus_features').build_odds_consensus_features(df, X))
print("DONE", flush=True)