"""
测试数据 Fixtures
=================

提供可复用的合成测试数据，避免依赖完整数据库。
所有数据均为人工构造，确保测试可独立运行。
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def make_sample_matches(n=20, seed=42):
    """生成合成比赛数据（含结果）"""
    rng = np.random.RandomState(seed)
    teams = ['Arsenal', 'Chelsea', 'Liverpool', 'City', 'United',
             'Spurs', 'Everton', 'Villa', 'Newcastle', 'Brighton']
    leagues = ['Premier League', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1']

    rows = []
    base_date = datetime(2024, 8, 1)
    for i in range(n):
        home = teams[i % len(teams)]
        away = teams[(i + 3) % len(teams)]
        if home == away:
            away = teams[(i + 5) % len(teams)]
        home_goals = int(rng.poisson(1.5))
        away_goals = int(rng.poisson(1.1))
        result = 0 if home_goals > away_goals else (1 if home_goals == away_goals else 2)

        rows.append({
            'match_id': f'M{i:04d}',
            'home_team_name': home,
            'away_team_name': away,
            'home_goals': home_goals,
            'away_goals': away_goals,
            'result': result,
            'match_date': base_date + timedelta(days=i*7),
            'competition_name': leagues[i % len(leagues)],
            'season': '2024-25',
            'round': (i % 10) + 1,
        })
    return pd.DataFrame(rows)


def make_sample_odds_features(n=20, seed=42):
    """生成合成赔率特征（60维子集）"""
    rng = np.random.RandomState(seed)
    feature_names = [
        'wdl_win', 'wdl_draw', 'wdl_lose',
        'wdl_implied_win', 'wdl_implied_draw', 'wdl_implied_lose',
        'wdl_overround', 'wdl_favorite', 'wdl_close_win', 'wdl_close_draw',
        'handicap_home', 'handicap_away', 'handicap_line',
        'total_goals_line', 'total_goals_over', 'total_goals_under',
        'tg_most_likely', 'tg_most_likely_prob',
        'value_bet_home', 'value_bet_away',
        'odds_confidence', 'odds_coverage',
        'has_wdl_odds', 'has_hcp_odds', 'has_tg_odds',
    ]
    data = {}
    for name in feature_names:
        if name.startswith('has_'):
            data[name] = rng.randint(0, 2, size=n).astype(float)
        elif 'implied' in name or 'prob' in name or 'overround' in name:
            data[name] = rng.uniform(0.1, 0.6, size=n)
        elif 'line' in name:
            data[name] = rng.uniform(-2.5, 3.5, size=n)
        else:
            data[name] = rng.uniform(1.3, 5.5, size=n)
    return pd.DataFrame(data)


def make_sample_elo_features(n=20, seed=42):
    """生成合成 Elo 特征（10维）"""
    rng = np.random.RandomState(seed)
    feature_names = [
        'home_elo', 'away_elo', 'elo_diff', 'elo_ratio',
        'elo_home_expected', 'elo_away_expected', 'elo_draw_prob',
        'home_elo_momentum', 'away_elo_momentum', 'elo_confidence',
    ]
    data = {}
    for name in feature_names:
        if 'momentum' in name:
            data[name] = rng.uniform(-50, 50, size=n)
        elif 'prob' in name or 'expected' in name or 'ratio' in name or 'confidence' in name:
            data[name] = rng.uniform(0.1, 0.9, size=n)
        else:
            data[name] = rng.uniform(1300, 1800, size=n)
    return pd.DataFrame(data)


def make_sample_temporal_features(n=20, seed=42):
    """生成合成 D-013 时序赔率特征（10维）"""
    rng = np.random.RandomState(seed)
    feature_names = [
        'wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility',
        'wdl_win_acceleration', 'wdl_late_trend', 'wdl_early_trend',
        'wdl_mid_stability', 'wdl_sudden_jump',
        'wdl_update_frequency', 'wdl_total_change',
    ]
    data = {}
    for name in feature_names:
        if 'frequency' in name:
            data[name] = rng.uniform(0.5, 10.0, size=n)
        elif 'jump' in name:
            data[name] = rng.uniform(0.0, 0.5, size=n)
        else:
            data[name] = rng.uniform(0.0, 2.0, size=n)
    return pd.DataFrame(data)


def make_full_feature_set(n=20, seed=42):
    """生成完整60维特征集（赔率+Elo+时序）"""
    odds = make_sample_odds_features(n, seed)
    elo = make_sample_elo_features(n, seed)
    temporal = make_sample_temporal_features(n, seed)
    return pd.concat([odds, elo, temporal], axis=1)


def make_sample_labels(n=20, seed=42):
    """生成合成标签（0=主胜, 1=平, 2=客胜）"""
    rng = np.random.RandomState(seed)
    return pd.Series(rng.choice([0, 1, 2], size=n, p=[0.45, 0.25, 0.30]))


def make_sample_timing_series(n_points=5, seed=42):
    """生成合成时序赔率序列（单场比赛）"""
    rng = np.random.RandomState(seed)
    base = 2.0
    series = [base]
    for _ in range(n_points - 1):
        change = rng.normal(0, 0.1)
        series.append(max(1.01, series[-1] + change))
    return pd.Series(series)


def make_sample_match_mapping(n=10):
    """生成合成 match_mapping"""
    return pd.DataFrame({
        'odds_match_id': [f'M{i:04d}' for i in range(n)],
        'timing_match_id': [f'T{i:04d}' for i in range(n)],
    })
