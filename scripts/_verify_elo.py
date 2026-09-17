# -*- coding: utf-8 -*-
"""验证 elo_rating 向量化改写（precompute_elo_ratings + build_elo_features）与旧实现输出一致，并计时。"""
import sys, time, warnings
warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_utils as F
import elo_rating as E

df = F.load_match_data_odds()
print(f"df={df.shape}", flush=True)


def old_precompute(df):
    """旧版 precompute_elo_ratings（iterrows 逐行遍历）。"""
    df_sorted = df.sort_values('date').reset_index(drop=True)
    team_elo = {}
    team_elo_history = {}
    for _, row in df_sorted.iterrows():
        home_team = row['home_team_name']
        away_team = row['away_team_name']
        match_date = row['date']
        try:
            hg = int(row['homeGoals'])
            ag = int(row['awayGoals'])
        except (ValueError, TypeError):
            continue
        if home_team not in team_elo:
            team_elo[home_team] = E.DEFAULT_ELO
            team_elo_history[home_team] = []
        if away_team not in team_elo:
            team_elo[away_team] = E.DEFAULT_ELO
            team_elo_history[away_team] = []
        home_elo_before = team_elo[home_team]
        away_elo_before = team_elo[away_team]
        actual_score_home = E.result_to_score(hg, ag)
        new_home_elo, new_away_elo = E.update_elo(
            home_elo_before, away_elo_before, actual_score_home,
            k_factor=E.K_FACTOR, home_advantage=E.HOME_ADVANTAGE)
        team_elo[home_team] = new_home_elo
        team_elo[away_team] = new_away_elo
        result_code = 2 if hg > ag else (0 if hg < ag else 1)
        team_elo_history[home_team].append({
            'date': match_date, 'elo': home_elo_before, 'opponent_elo': away_elo_before,
            'result': result_code, 'is_home': True, 'elo_change': new_home_elo - home_elo_before,
            'home_goals': hg, 'away_goals': ag})
        team_elo_history[away_team].append({
            'date': match_date, 'elo': away_elo_before, 'opponent_elo': home_elo_before,
            'result': result_code, 'is_home': False, 'elo_change': new_away_elo - away_elo_before,
            'home_goals': ag, 'away_goals': hg})
    out = {}
    cols = ['date', 'elo', 'opponent_elo', 'result', 'is_home',
            'elo_change', 'home_goals', 'away_goals']
    for team, history in team_elo_history.items():
        out[team] = pd.DataFrame(history) if history else pd.DataFrame(columns=cols)
    return out


# ---- 1. precompute 等价性 ----
t0 = time.time(); new_pre = E.precompute_elo_ratings(df); t_newpre = time.time() - t0
t0 = time.time(); old_pre = old_precompute(df); t_oldpre = time.time() - t0
assert set(new_pre) == set(old_pre), f"precompute 球队集不一致 {len(new_pre)} vs {len(old_pre)}"
pre_bad = 0
for team in new_pre:
    a, b = new_pre[team], old_pre[team]
    if len(a) != len(b):
        pre_bad += 1
        continue
    if not np.allclose(a['elo'].to_numpy(dtype=float), b['elo'].to_numpy(dtype=float),
                       rtol=1e-12, atol=1e-12, equal_nan=True):
        pre_bad += 1
        continue
    if not np.allclose(a['elo_change'].to_numpy(dtype=float), b['elo_change'].to_numpy(dtype=float),
                       rtol=1e-12, atol=1e-12, equal_nan=True):
        pre_bad += 1
        continue
    if not a['date'].reset_index(drop=True).equals(b['date'].reset_index(drop=True)):
        pre_bad += 1
print(f"[precompute] 球队 {len(new_pre)}，不一致 {pre_bad}；新 {t_newpre:.2f}s vs 旧 {t_oldpre:.2f}s", flush=True)

# ---- 2. build_elo_features 等价性 + 计时 ----
t0 = time.time(); new_feat = E.build_elo_features(df); t_new = time.time() - t0
t0 = time.time(); old_feat = E.build_elo_features_legacy(df); t_old = time.time() - t0
assert list(new_feat.columns) == list(old_feat.columns), "columns differ"
assert list(new_feat.index) == list(old_feat.index), "index differ"
diff = new_feat[old_feat.columns].to_numpy(dtype=float) - old_feat[old_feat.columns].to_numpy(dtype=float)
max_abs = float(np.nanmax(np.abs(diff)))
n_bad = int((np.abs(diff) > 1e-6).sum())
print(f"[build] 不一致元素 {n_bad}, 最大绝对差 {max_abs:.10f}", flush=True)
print(f"[timing] elo新 {t_new:.2f}s, elo旧 {t_old:.2f}s", flush=True)
print("DONE", flush=True)