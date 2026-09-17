# -*- coding: utf-8 -*-
"""临时：自包含校验 goals_std / weighted_* 优化前后数值一致性（不依赖 cache 的 merge）。"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_utils import load_match_data_odds, _resolve_feature_time_decay, calculate_time_decay_weights

df = load_match_data_odds()
all_teams = pd.concat([df['home_team_name'], df['away_team_name']]).unique()
cfg = _resolve_feature_time_decay()

def build_raw(team):
    tm = df[(df['home_team_name'] == team) | (df['away_team_name'] == team)].sort_values('date').copy()
    is_home = tm['home_team_name'] == team
    tg = np.where(is_home, tm['homeGoals'], tm['awayGoals'])
    og = np.where(is_home, tm['awayGoals'], tm['homeGoals'])
    tm['team_goals'] = tg
    tm['opp_goals'] = og
    tm['is_win'] = (tg > og).astype(int)
    tm['is_draw'] = (tg == og).astype(int)
    tm['is_loss'] = (tg < og).astype(int)
    tm['games_played'] = np.arange(1, len(tm) + 1)
    tm['cum_wins'] = tm['is_win'].cumsum()
    tm['win_rate'] = tm['cum_wins'] / tm['games_played']
    tm['cum_goals'] = tm['team_goals'].cumsum()
    tm['avg_goals'] = tm['cum_goals'] / tm['games_played']
    return tm

def old_goals_std(tm):
    out = []
    for i in range(len(tm)):
        out.append(tm['team_goals'][:i+1].std() if i >= 1 else 0)
    return np.array(out, dtype=float)

def new_goals_std(tm):
    return tm['team_goals'].expanding(min_periods=1).std().fillna(0).values

def old_weighted(tm):
    wwr = []; wag = []
    for i in range(len(tm)):
        dates = tm['date'].iloc[:i]
        if len(dates) >= 3:
            w = calculate_time_decay_weights(dates, tm['date'].iloc[i], config=cfg)
            if w.sum() > 0:
                wm = tm['is_win'].iloc[:i].values
                g = tm['team_goals'].iloc[:i].values
                wwr.append((wm * w).sum() / w.sum())
                wag.append((g * w).sum() / w.sum())
            else:
                wwr.append(tm['win_rate'].iloc[i]); wag.append(tm['avg_goals'].iloc[i])
        else:
            wwr.append(tm['win_rate'].iloc[i]); wag.append(tm['avg_goals'].iloc[i])
    return np.array(wwr), np.array(wag)

def new_weighted(tm):
    alpha = np.log(2) / cfg.get('half_life_days', 14)
    max_hist = cfg.get('max_history_days', 90)
    day = tm['date'].values.astype('datetime64[D]').astype(np.int64)
    t_rel = (day - day[-1]).astype(np.float64)
    E = np.exp(alpha * t_rel)
    win = tm['is_win'].values.astype(np.float64)
    goals = tm['team_goals'].values.astype(np.float64)
    PE = np.concatenate([[0.0], np.cumsum(E)])
    PEW = np.concatenate([[0.0], np.cumsum(E * win)])
    PEG = np.concatenate([[0.0], np.cumsum(E * goals)])
    ww = np.empty(len(tm)); wg = np.empty(len(tm))
    start = 0
    for i in range(len(tm)):
        while start < i and (day[i] - day[start]) > max_hist:
            start += 1
        if i >= 3 and start < i:
            sE = PE[i] - PE[start]
            if sE > 0:
                ww[i] = (PEW[i] - PEW[start]) / sE
                wg[i] = (PEG[i] - PEG[start]) / sE
                continue
        ww[i] = tm['win_rate'].iloc[i]
        wg[i] = tm['avg_goals'].iloc[i]
    return ww, wg

max_std = max_wr = max_wg = 0.0
bad = 0
for team in all_teams:
    tm = build_raw(team)
    a_std, b_std = old_goals_std(tm), new_goals_std(tm)
    a_wr, a_wg = old_weighted(tm)
    b_wr, b_wg = new_weighted(tm)
    d_std = np.nanmax(np.abs(a_std - b_std))
    d_wr = np.nanmax(np.abs(a_wr - b_wr))
    d_wg = np.nanmax(np.abs(a_wg - b_wg))
    max_std = max(max_std, d_std); max_wr = max(max_wr, d_wr); max_wg = max(max_wg, d_wg)
    if d_std > 1e-9 or d_wr > 1e-9 or d_wg > 1e-9:
        bad += 1
        if bad <= 5:
            print(f"  [DIFF] {team}: std={d_std:.2e} wr={d_wr:.2e} wg={d_wg:.2e}", flush=True)

print(f"完成。有差异的队: {bad}/{len(all_teams)}", flush=True)
print(f"最大差异: goals_std={max_std:.2e}, weighted_win_rate={max_wr:.2e}, weighted_avg_goals={max_wg:.2e}", flush=True)