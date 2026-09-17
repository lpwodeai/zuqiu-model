# -*- coding: utf-8 -*-
"""临时校验：新 searchsorted 定位 vs 旧全量布尔过滤，逐字段数值一致性 + 全量计时。"""
import sys
import time
import math
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_utils import load_match_data_odds, precompute_team_stats, build_team_features, get_global_league_stats

df = load_match_data_odds()
print(f"数据 {len(df)} 场", flush=True)

t0 = time.time()
team_stats_cache, league_stats, global_stats = precompute_team_stats(df)
print(f"precompute_team_stats: {time.time()-t0:.2f}s (teams={len(team_stats_cache)})", flush=True)

_EMPTY_D = np.array([], dtype=np.int64)
lookup = {}
for team, tm in team_stats_cache.items():
    di = tm['date'].values.astype('datetime64[D]').astype(np.int64)
    lookup[team] = (di, tm)

def _last_before_new(di, tm, md):
    if tm is None:
        return None
    pos = int(np.searchsorted(di, md, side='left')) - 1
    return tm.iloc[pos] if pos >= 0 else None

def _last_before_old(team, md):
    hist = team_stats_cache.get(team, pd.DataFrame())
    before = hist[hist['date'] < md]
    return before.iloc[-1] if len(before) > 0 else None

def _dict(last, ld, prefix, home):
    if last is not None:
        return {
            f'{prefix}_avg_goals': last['avg_goals'],
            f'{prefix}_avg_opp_goals': last['avg_opp_goals'],
            f'{prefix}_win_rate': last['win_rate'],
            f'{prefix}_draw_rate': last['draw_rate'],
            f'{prefix}_loss_rate': last['loss_rate'],
            f'{prefix}_goals_std': last['goals_std'],
            f'{prefix}_recent_form': last['recent_form'],
            f'{prefix}_form_trend': last['form_trend'],
            f'{prefix}_consecutive_wins': last['consecutive_wins'],
            f'{prefix}_consecutive_losses': last['consecutive_losses'],
            f'{prefix}_consecutive_undefeated': last['consecutive_undefeated'],
            f'{prefix}_games_played': last['games_played'],
            f'{prefix}_weighted_win_rate': last['weighted_win_rate'],
            f'{prefix}_weighted_avg_goals': last['weighted_avg_goals'],
            f'{prefix}_home_win_rate': last['home_win_rate'],
            f'{prefix}_away_win_rate': last['away_win_rate'],
            f'{prefix}_home_goals': last['home_avg_goals'],
            f'{prefix}_away_goals': last['away_avg_goals'],
            f'{prefix}_home_advantage': last['home_advantage'],
        }
    if home:
        return {
            f'{prefix}_avg_goals': ld['avg_home_goals'],
            f'{prefix}_avg_opp_goals': ld['avg_away_goals'],
            f'{prefix}_win_rate': ld['home_win_rate'],
            f'{prefix}_draw_rate': ld['draw_rate'],
            f'{prefix}_loss_rate': ld['away_win_rate'],
            f'{prefix}_goals_std': ld['goals_std'],
            f'{prefix}_recent_form': 1.0,
            f'{prefix}_form_trend': 0.0,
            f'{prefix}_consecutive_wins': 0,
            f'{prefix}_consecutive_losses': 0,
            f'{prefix}_consecutive_undefeated': 0,
            f'{prefix}_games_played': 0,
            f'{prefix}_weighted_win_rate': ld['home_win_rate'],
            f'{prefix}_weighted_avg_goals': ld['avg_home_goals'],
            f'{prefix}_home_win_rate': ld['home_win_rate'],
            f'{prefix}_away_win_rate': ld['away_win_rate'],
            f'{prefix}_home_goals': ld['avg_home_goals'],
            f'{prefix}_away_goals': ld['avg_away_goals'],
            f'{prefix}_home_advantage': ld['home_win_rate'] - ld['away_win_rate'],
        }
    return {
        f'{prefix}_avg_goals': ld['avg_away_goals'],
        f'{prefix}_avg_opp_goals': ld['avg_home_goals'],
        f'{prefix}_win_rate': ld['away_win_rate'],
        f'{prefix}_draw_rate': ld['draw_rate'],
        f'{prefix}_loss_rate': ld['home_win_rate'],
        f'{prefix}_goals_std': ld['goals_std'],
        f'{prefix}_recent_form': 1.0,
        f'{prefix}_form_trend': 0.0,
        f'{prefix}_consecutive_wins': 0,
        f'{prefix}_consecutive_losses': 0,
        f'{prefix}_consecutive_undefeated': 0,
        f'{prefix}_games_played': 0,
        f'{prefix}_weighted_win_rate': ld['away_win_rate'],
        f'{prefix}_weighted_avg_goals': ld['avg_away_goals'],
        f'{prefix}_home_win_rate': ld['home_win_rate'],
        f'{prefix}_away_win_rate': ld['away_win_rate'],
        f'{prefix}_home_goals': ld['avg_home_goals'],
        f'{prefix}_away_goals': ld['avg_away_goals'],
        f'{prefix}_home_advantage': ld['home_win_rate'] - ld['away_win_rate'],
    }

def close(a, b):
    if a is None or b is None:
        return a is b
    try:
        if math.isnan(float(a)) and math.isnan(float(b)):
            return True
    except (TypeError, ValueError):
        pass
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-12)
    except (TypeError, ValueError):
        return a == b

dates_all = df['date'].values.astype('datetime64[D]').astype(np.int64)

sample = df.sample(n=500, random_state=7)
bad = 0
for idx, row in sample.iterrows():
    home = row['home_team_name']; away = row['away_team_name']
    md_ts = row['date']; md = int(dates_all[df.index.get_loc(idx)])
    league = row['competition_name']
    ld = league_stats.get(league, global_stats)

    h_new = _last_before_new(*lookup.get(home, (_EMPTY_D, None)), md)
    h_old = _last_before_old(home, md_ts)
    a_new = _last_before_new(*lookup.get(away, (_EMPTY_D, None)), md)
    a_old = _last_before_old(away, md_ts)

    for nd, od, pk, ph in [(_dict(h_new, ld, 'home', True), _dict(h_old, ld, 'home', True), 'home', True),
                            (_dict(a_new, ld, 'away', False), _dict(a_old, ld, 'away', False), 'away', False)]:
        for k in nd:
            if not close(nd[k], od[k]):
                bad += 1
                if bad <= 10:
                    print(f"  [DIFF {pk}] {k}: new={nd[k]!r} old={od[k]!r} | {home} vs {away}", flush=True)

    # opponent 对比
    def opp_old_before(team):
        hist = team_stats_cache.get(team, pd.DataFrame())
        before = hist[hist['date'] < md_ts]
        return before
    def opp_new(tm, di):
        out = []
        if tm is not None and len(di) > 0:
            p = int(np.searchsorted(di, md, side='left')) - 1
            if p >= 0:
                for o in pd.unique(tm['away_team_name'].iloc[:p+1].iloc[:10]):
                    odi, otm = lookup.get(o, (_EMPTY_D, None))
                    if otm is None:
                        continue
                    p2 = int(np.searchsorted(odi, md, side='left')) - 1
                    if p2 + 1 >= 5:
                        out.append(otm['win_rate'].iloc[p2])
        return out
    def opp_old(team):
        b = opp_old_before(team)
        out = []
        if len(b) > 0:
            for o in pd.unique(b['away_team_name'][:10]):
                b2 = team_stats_cache.get(o, pd.DataFrame())[team_stats_cache.get(o, pd.DataFrame())['date'] < md_ts]
                if len(b2) >= 5:
                    out.append(b2.iloc[-1]['win_rate'])
        return out
    for tk, tn in [('home', home), ('away', away)]:
        di, tm = lookup.get(tn, (_EMPTY_D, None))
        nv = opp_new(tm, di)
        ov = opp_old(tn)
        if (len(nv) != len(ov)) or any(not close(a, b) for a, b in zip(nv, ov)):
            bad += 1
            if bad <= 10:
                print(f"  [DIFF opp {tk}] new={nv!r} old={ov!r}", flush=True)

print(f"正确性校验完成。不一致样本数: {bad}/500", flush=True)

# 全量计时
t0 = time.time()
tf = build_team_features(df)
print(f"全量 build_team_features: shape={tf.shape}, 用时 {time.time()-t0:.2f}s", flush=True)