# -*- coding: utf-8 -*-
"""临时：定位 precompute_team_stats 剩余 28s 的具体来源（逐子步骤计时）。"""
import time
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_utils import load_match_data_odds, _resolve_feature_time_decay, calculate_time_decay_weights

df = load_match_data_odds()
all_teams = pd.concat([df['home_team_name'], df['away_team_name']]).unique()
print(f"teams={len(all_teams)}, rows={len(df)}", flush=True)

# 取前 10 个队，逐子步骤计时并外推
sample = list(all_teams[:10])
t_filter = t_cumsum = t_std = t_roll = t_iterrows = t_merge = t_decay = 0.0

for team in sample:
    t0 = time.time()
    tm = df[(df['home_team_name'] == team) | (df['away_team_name'] == team)].sort_values('date').copy()
    t_filter += time.time() - t0

    is_home = tm['home_team_name'] == team
    tg = np.where(is_home, tm['homeGoals'], tm['awayGoals'])
    og = np.where(is_home, tm['awayGoals'], tm['homeGoals'])
    tm['team_goals'] = tg; tm['opp_goals'] = og
    tm['is_win'] = (tg > og).astype(int)
    tm['is_draw'] = (tg == og).astype(int)
    tm['is_loss'] = (tg < og).astype(int)

    t0 = time.time()
    tm['cum_wins'] = tm['is_win'].cumsum()
    tm['cum_draws'] = tm['is_draw'].cumsum()
    tm['cum_losses'] = tm['is_loss'].cumsum()
    tm['cum_goals'] = tm['team_goals'].cumsum()
    tm['cum_opp_goals'] = tm['opp_goals'].cumsum()
    tm['games_played'] = np.arange(1, len(tm) + 1)
    tm['win_rate'] = tm['cum_wins'] / tm['games_played']
    tm['avg_goals'] = tm['cum_goals'] / tm['games_played']
    tm['avg_opp_goals'] = tm['cum_opp_goals'] / tm['games_played']
    t_cumsum += time.time() - t0

    t0 = time.time()
    tm['goals_std'] = tm['team_goals'].expanding(min_periods=1).std().fillna(0)
    t_std += time.time() - t0

    t0 = time.time()
    tm['recent_form'] = tm['is_win'].rolling(window=5, min_periods=1).mean()
    tm['form_trend'] = tm['is_win'].rolling(window=6).mean() - tm['is_win'].rolling(window=6).mean().shift(6)
    t_roll += time.time() - t0

    # consecutive iterrows
    t0 = time.time()
    cw = []; cl = []; cu = []
    cur_w = cur_l = cur_u = 0
    for _, row in tm.iterrows():
        if row['is_win'] == 1:
            cur_w += 1; cur_l = 0; cur_u += 1
        elif row['is_loss'] == 1:
            cur_w = 0; cur_l += 1; cur_u = 0
        else:
            cur_w = 0; cur_l = 0; cur_u += 1
        cw.append(cur_w); cl.append(cur_l); cu.append(cur_u)
    tm['consecutive_wins'] = cw
    t_iterrows += time.time() - t0

    # merge
    t0 = time.time()
    hm = tm['home_team_name'] == team
    home_s = tm[hm].copy(); away_s = tm[~hm].copy()
    if len(home_s) > 0:
        home_s['home_win_rate'] = home_s['is_win'].cumsum() / np.arange(1, len(home_s)+1)
        home_s['home_avg_goals'] = home_s['team_goals'].cumsum() / np.arange(1, len(home_s)+1)
    if len(away_s) > 0:
        away_s['away_win_rate'] = away_s['is_win'].cumsum() / np.arange(1, len(away_s)+1)
        away_s['away_avg_goals'] = away_s['team_goals'].cumsum() / np.arange(1, len(away_s)+1)
    if len(home_s) > 0:
        tm = tm.merge(home_s[['date','home_win_rate','home_avg_goals']], on='date', how='left')
    else:
        tm['home_win_rate'] = 0.0; tm['home_avg_goals'] = 0.0
    if len(away_s) > 0:
        tm = tm.merge(away_s[['date','away_win_rate','away_avg_goals']], on='date', how='left')
    else:
        tm['away_win_rate'] = 0.0; tm['away_avg_goals'] = 0.0
    tm[['home_win_rate','away_win_rate','home_avg_goals','away_avg_goals']] = tm[['home_win_rate','away_win_rate','home_avg_goals','away_avg_goals']].fillna(0)
    t_merge += time.time() - t0

    # weighted decay (滑动窗口版)
    t0 = time.time()
    cfg = _resolve_feature_time_decay()
    mh = cfg.get('max_history_days', 90)
    ds = tm['date']; wm_all = tm['is_win'].values; g_all = tm['team_goals'].values
    wwr = []; wag = []; st = 0
    for i in range(len(tm)):
        rref = ds.iloc[i]
        while st < i and (rref - ds.iloc[st]).days > mh:
            st += 1
        if i >= 3 and st < i:
            w = calculate_time_decay_weights(ds.iloc[st:i], rref, config=cfg)
            if w.sum() > 0:
                wwr.append(float((wm_all[st:i] * w).sum() / w.sum()))
                wag.append(float((g_all[st:i] * w).sum() / w.sum()))
                continue
        wwr.append(tm['win_rate'].iloc[i]); wag.append(tm['avg_goals'].iloc[i])
    t_decay += time.time() - t0

    tm['home_advantage'] = tm['home_win_rate'] - tm['away_win_rate']

n = len(sample)
print(f"外推到 {len(all_teams)} 队:", flush=True)
print(f"  filter:   {t_filter/n*len(all_teams):.1f}s", flush=True)
print(f"  cumsum:   {t_cumsum/n*len(all_teams):.1f}s", flush=True)
print(f"  std:      {t_std/n*len(all_teams):.1f}s", flush=True)
print(f"  rolling:  {t_roll/n*len(all_teams):.1f}s", flush=True)
print(f"  iterrows: {t_iterrows/n*len(all_teams):.1f}s", flush=True)
print(f"  merge:    {t_merge/n*len(all_teams):.1f}s", flush=True)
print(f"  decay:    {t_decay/n*len(all_teams):.1f}s", flush=True)