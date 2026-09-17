import sqlite3
import pandas as pd
import numpy as np
import yaml
import os
from scipy.stats import poisson
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"
CONFIG_PATH = BASE_DIR / "config.yaml"

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

LEAGUE_CONSTANTS = CONFIG.get('league_specific', {})

DEFAULT_CONSTANTS = {
    'avg_home_goals': 1.5,
    'avg_away_goals': 1.2,
    'home_advantage_factor': 1.15,
    'avg_shots_per_game': 13,
    'avg_shots_on_target_per_game': 4.5,
    'avg_xg_per_game': 1.2,
    'avg_corners_per_game': 5,
    'avg_fouls_per_game': 10,
    'avg_yellow_cards_per_game': 2
}

def get_league_constants(league_name):
    if league_name in LEAGUE_CONSTANTS:
        return {**DEFAULT_CONSTANTS, **LEAGUE_CONSTANTS[league_name]}
    return DEFAULT_CONSTANTS

def calculate_time_decay_weights(dates, reference_date, half_life_days=14):
    if len(dates) == 0:
        return np.array([])
    
    if hasattr(dates, 'dt'):
        days_diff = (reference_date - dates).dt.days
    else:
        days_diff = np.array([(reference_date - d).days for d in dates])
    
    days_diff = np.array(days_diff)
    weights = np.exp(-days_diff * np.log(2) / half_life_days)
    weights = np.maximum(weights, 0.01)
    
    if weights.sum() > 0:
        weights = weights / weights.sum()
    
    return weights

def winsorize_series(series, lower_percentile=1, upper_percentile=99):
    if len(series.dropna()) == 0:
        return series
    lower = np.percentile(series.dropna(), lower_percentile)
    upper = np.percentile(series.dropna(), upper_percentile)
    return series.clip(lower=lower, upper=upper)

def load_match_data(db_path=None):
    if db_path is None:
        db_path = DB_PATH
    
    conn = sqlite3.connect(db_path)
    query = """
    SELECT 
        m.date,
        ht.name as home_team_name,
        at.name as away_team_name,
        m.homeGoals,
        m.awayGoals,
        c.name as competition_name,
        m.homeXg,
        m.awayXg,
        m.homeXgot,
        m.awayXgot,
        m.homeBigChances,
        m.awayBigChances,
        m.homeXa,
        m.awayXa,
        m.homeSaves,
        m.awaySaves,
        m.homeShots,
        m.homeShotsOnTarget,
        m.awayShots,
        m.awayShotsOnTarget,
        m.homePossession,
        m.homeCorners,
        m.awayCorners,
        m.homeFouls,
        m.awayFouls,
        m.homeYellowCards,
        m.awayYellowCards,
        m.homeTouchesBox,
        m.awayTouchesBox,
        m.homeHitsPost,
        m.awayHitsPost
    FROM matches m
    LEFT JOIN teams ht ON m.homeTeamId = ht.id
    LEFT JOIN teams at ON m.awayTeamId = at.id
    LEFT JOIN competitions c ON m.competitionId = c.id
    WHERE m.homeGoals IS NOT NULL AND m.awayGoals IS NOT NULL
    ORDER BY m.date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    
    df['date'] = pd.to_datetime(df['date'])
    df['result'] = np.where(df['homeGoals'] > df['awayGoals'], 2,
                           np.where(df['homeGoals'] < df['awayGoals'], 0, 1))
    df['goal_diff'] = df['homeGoals'] - df['awayGoals']
    df['total_goals'] = df['homeGoals'] + df['awayGoals']
    
    return df

def detect_synthetic_data(df):
    flags = pd.DataFrame(index=df.index)
    
    flags['is_suspicious_home_away_symmetry'] = (
        abs(df.groupby('competition_name')['homeGoals'].transform('mean') - 
            df.groupby('competition_name')['awayGoals'].transform('mean')) < 0.2
    )
    
    flags['is_suspicious_draw_rate'] = (
        (df.groupby('competition_name')['result'].transform(lambda x: (x == 1).mean()) < 0.05) |
        (df.groupby('competition_name')['result'].transform(lambda x: (x == 1).mean()) > 0.45)
    )
    
    flags['is_suspicious_goal_range'] = (
        (df['homeGoals'] > 8) | (df['awayGoals'] > 8) |
        (df['homeGoals'] < 0) | (df['awayGoals'] < 0)
    )
    
    flags['is_suspicious_xg'] = (
        (df['homeXg'] < 0) | (df['awayXg'] < 0) |
        (df['homeXg'] > 8) | (df['awayXg'] > 8)
    )
    
    flags['is_synthetic'] = flags.any(axis=1)
    
    return flags

def filter_synthetic_data(df):
    flags = detect_synthetic_data(df)
    original_count = len(df)
    filtered_df = df[~flags['is_synthetic']].copy()
    removed_count = original_count - len(filtered_df)
    
    print(f"\n数据质量过滤:")
    print(f"  原始记录: {original_count}")
    print(f"  移除合成数据: {removed_count} ({removed_count/original_count*100:.2f}%)")
    print(f"  保留记录: {len(filtered_df)}")
    
    return filtered_df

def build_temporal_features(df):
    features = pd.DataFrame(index=df.index)
    
    features['month'] = df['date'].dt.month
    features['day_of_week'] = df['date'].dt.dayofweek
    features['is_weekend'] = (df['date'].dt.dayofweek >= 5).astype(int)
    
    features['is_early_season'] = (df['date'].dt.month.isin([8, 9])).astype(int)
    features['is_mid_season'] = (df['date'].dt.month.isin([10, 11, 12, 1, 2])).astype(int)
    features['is_late_season'] = (df['date'].dt.month.isin([3, 4, 5])).astype(int)
    
    features['season_week'] = ((df['date'] - df.groupby(df['date'].dt.year)['date'].transform('min')).dt.days // 7).clip(0, 38)
    features['days_since_season_start'] = (df['date'] - df.groupby(df['date'].dt.year)['date'].transform('min')).dt.days
    
    return features

def build_league_features(df):
    features = pd.DataFrame(index=df.index)
    
    league_dummies = pd.get_dummies(df['competition_name'], prefix='league')
    features = pd.concat([features, league_dummies], axis=1)
    
    league_stats = df.groupby('competition_name').agg({
        'homeGoals': ['mean', 'std'],
        'awayGoals': ['mean', 'std'],
        'total_goals': ['mean', 'std'],
        'goal_diff': ['mean', 'std']
    })
    league_stats.columns = ['_'.join(col).strip() for col in league_stats.columns.values]
    
    for col in league_stats.columns:
        features[f'league_{col}'] = df['competition_name'].map(league_stats[col])
    
    return features

def build_match_features(df):
    features = pd.DataFrame(index=df.index)
    
    league_avg_xg = df['competition_name'].map(
        lambda l: get_league_constants(l).get('avg_xg_per_game', DEFAULT_CONSTANTS['avg_xg_per_game'])
    )
    league_avg_shots = df['competition_name'].map(
        lambda l: get_league_constants(l).get('avg_shots_per_game', DEFAULT_CONSTANTS['avg_shots_per_game'])
    )
    league_avg_shots_on_target = df['competition_name'].map(
        lambda l: get_league_constants(l).get('avg_shots_on_target_per_game', DEFAULT_CONSTANTS['avg_shots_on_target_per_game'])
    )
    league_avg_corners = df['competition_name'].map(
        lambda l: get_league_constants(l).get('avg_corners_per_game', DEFAULT_CONSTANTS['avg_corners_per_game'])
    )
    league_avg_fouls = df['competition_name'].map(
        lambda l: get_league_constants(l).get('avg_fouls_per_game', DEFAULT_CONSTANTS['avg_fouls_per_game'])
    )
    league_avg_yellow_cards = df['competition_name'].map(
        lambda l: get_league_constants(l).get('avg_yellow_cards_per_game', DEFAULT_CONSTANTS['avg_yellow_cards_per_game'])
    )
    
    features['home_xg'] = df['homeXg'].fillna(league_avg_xg)
    features['away_xg'] = df['awayXg'].fillna(league_avg_xg)
    features['home_xgot'] = df['homeXgot'].fillna(df['homeXg'].fillna(0.8))
    features['away_xgot'] = df['awayXgot'].fillna(df['awayXg'].fillna(0.8))
    
    features['xg_diff'] = features['home_xg'] - features['away_xg']
    features['xgot_diff'] = features['home_xgot'] - features['away_xgot']
    features['xg_ratio'] = features['home_xg'] / (features['away_xg'] + 0.01)
    features['xgot_ratio'] = features['home_xgot'] / (features['away_xgot'] + 0.01)
    features['total_xg'] = features['home_xg'] + features['away_xg']
    features['total_xgot'] = features['home_xgot'] + features['away_xgot']
    
    features['home_big_chances'] = df['homeBigChances'].fillna(0)
    features['away_big_chances'] = df['awayBigChances'].fillna(0)
    features['big_chances_diff'] = features['home_big_chances'] - features['away_big_chances']
    
    features['home_xa'] = df['homeXa'].fillna(0)
    features['away_xa'] = df['awayXa'].fillna(0)
    features['xa_diff'] = features['home_xa'] - features['away_xa']
    
    features['home_shots'] = df['homeShots'].fillna(league_avg_shots)
    features['away_shots'] = df['awayShots'].fillna(league_avg_shots)
    features['home_shots_on_target'] = df['homeShotsOnTarget'].fillna(league_avg_shots_on_target)
    features['away_shots_on_target'] = df['awayShotsOnTarget'].fillna(league_avg_shots_on_target)
    
    features['shots_diff'] = features['home_shots'] - features['away_shots']
    features['shots_on_target_diff'] = features['home_shots_on_target'] - features['away_shots_on_target']
    features['home_attack_rate'] = features['home_shots_on_target'] / (features['home_shots'] + 1)
    features['away_attack_rate'] = features['away_shots_on_target'] / (features['away_shots'] + 1)
    features['attack_rate_diff'] = features['home_attack_rate'] - features['away_attack_rate']
    
    features['home_xg_per_shot'] = features['home_xg'] / (features['home_shots'] + 1)
    features['away_xg_per_shot'] = features['away_xg'] / (features['away_shots'] + 1)
    features['xg_per_shot_diff'] = features['home_xg_per_shot'] - features['away_xg_per_shot']
    
    features['home_saves'] = df['homeSaves'].fillna(0)
    features['away_saves'] = df['awaySaves'].fillna(0)
    features['save_diff'] = features['home_saves'] - features['away_saves']
    features['save_rate_home'] = features['home_saves'] / (features['away_shots_on_target'] + 1)
    features['save_rate_away'] = features['away_saves'] / (features['home_shots_on_target'] + 1)
    features['save_rate_diff'] = features['save_rate_home'] - features['save_rate_away']
    
    features['home_possession'] = df['homePossession'].fillna(50)
    features['away_possession'] = 100 - features['home_possession']
    features['possession_diff'] = features['home_possession'] - features['away_possession']
    
    features['home_corners'] = df['homeCorners'].fillna(league_avg_corners)
    features['away_corners'] = df['awayCorners'].fillna(league_avg_corners)
    features['corner_diff'] = features['home_corners'] - features['away_corners']
    
    features['home_fouls'] = df['homeFouls'].fillna(league_avg_fouls)
    features['away_fouls'] = df['awayFouls'].fillna(league_avg_fouls)
    features['foul_diff'] = features['home_fouls'] - features['away_fouls']
    
    features['home_yellow_cards'] = df['homeYellowCards'].fillna(league_avg_yellow_cards)
    features['away_yellow_cards'] = df['awayYellowCards'].fillna(league_avg_yellow_cards)
    features['yellow_card_diff'] = features['home_yellow_cards'] - features['away_yellow_cards']
    
    features['home_goal_diff_hist'] = df.groupby('home_team_name')['goal_diff'].cumsum().shift(1).fillna(0)
    features['away_goal_diff_hist'] = df.groupby('away_team_name')['goal_diff'].cumsum().shift(1).fillna(0)
    
    features['home_points'] = df.groupby('home_team_name').apply(
        lambda x: (3 * (x['homeGoals'] > x['awayGoals']) + 1 * (x['homeGoals'] == x['awayGoals'])).cumsum().shift(1).fillna(0)
    ).reset_index(level=0, drop=True)
    features['away_points'] = df.groupby('away_team_name').apply(
        lambda x: (3 * (x['awayGoals'] > x['homeGoals']) + 1 * (x['awayGoals'] == x['homeGoals'])).cumsum().shift(1).fillna(0)
    ).reset_index(level=0, drop=True)
    
    return features

def calc_h2h_stats(df, home_team, away_team, match_date):
    h2h_hist = df[((df['home_team_name'] == home_team) & (df['away_team_name'] == away_team)) |
                  ((df['home_team_name'] == away_team) & (df['away_team_name'] == home_team)) &
                  (df['date'] < match_date)].sort_values('date').tail(10)
    
    if len(h2h_hist) == 0:
        return {
            'h2h_matches': 0,
            'h2h_home_win_rate': 0.33,
            'h2h_away_win_rate': 0.33,
            'h2h_draw_rate': 0.34,
            'h2h_avg_goals_home': 1.5,
            'h2h_avg_goals_away': 1.2,
            'h2h_avg_total_goals': 2.7,
            'h2h_goal_diff_avg': 0.3,
            'h2h_last_result': 1,
            'h2h_home_streak': 0,
            'h2h_away_streak': 0,
            'h2h_goals_std': 1.0
        }
    
    home_wins = 0
    away_wins = 0
    draws = 0
    home_goals_total = 0
    away_goals_total = 0
    goal_diffs = []
    
    for _, r in h2h_hist.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        
        home_goals_total += hg
        away_goals_total += ag
        goal_diffs.append(hg - ag)
        
        if hg > ag:
            home_wins += 1
        elif hg < ag:
            away_wins += 1
        else:
            draws += 1
    
    total = len(h2h_hist)
    
    h2h_sorted_rev = h2h_hist.iloc[::-1]
    home_streak = 0
    for _, r in h2h_sorted_rev.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        if hg > ag:
            home_streak += 1
        else:
            break
    
    away_streak = 0
    for _, r in h2h_sorted_rev.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        if hg < ag:
            away_streak += 1
        else:
            break
    
    last_row = h2h_hist.iloc[-1]
    if last_row['home_team_name'] == home_team:
        hg, ag = last_row['homeGoals'], last_row['awayGoals']
    else:
        hg, ag = last_row['awayGoals'], last_row['homeGoals']
    if hg > ag:
        last_result = 2
    elif hg < ag:
        last_result = 0
    else:
        last_result = 1
    
    return {
        'h2h_matches': total,
        'h2h_home_win_rate': home_wins / total,
        'h2h_away_win_rate': away_wins / total,
        'h2h_draw_rate': draws / total,
        'h2h_avg_goals_home': home_goals_total / total,
        'h2h_avg_goals_away': away_goals_total / total,
        'h2h_avg_total_goals': (home_goals_total + away_goals_total) / total,
        'h2h_goal_diff_avg': np.mean(goal_diffs),
        'h2h_last_result': last_result,
        'h2h_home_streak': home_streak,
        'h2h_away_streak': away_streak,
        'h2h_goals_std': np.std(goal_diffs) if len(goal_diffs) > 1 else 1.0
    }

def precompute_team_stats(df):
    all_teams = pd.concat([df['home_team_name'], df['away_team_name']]).unique()
    
    team_stats_cache = {}
    
    for team in all_teams:
        team_matches = df[(df['home_team_name'] == team) | (df['away_team_name'] == team)].sort_values('date').copy()
        
        is_home = team_matches['home_team_name'] == team
        team_goals = np.where(is_home, team_matches['homeGoals'], team_matches['awayGoals'])
        opp_goals = np.where(is_home, team_matches['awayGoals'], team_matches['homeGoals'])
        
        team_matches['team_goals'] = team_goals
        team_matches['opp_goals'] = opp_goals
        team_matches['is_win'] = (team_goals > opp_goals).astype(int)
        team_matches['is_draw'] = (team_goals == opp_goals).astype(int)
        team_matches['is_loss'] = (team_goals < opp_goals).astype(int)
        
        team_matches['cum_wins'] = team_matches['is_win'].cumsum()
        team_matches['cum_draws'] = team_matches['is_draw'].cumsum()
        team_matches['cum_losses'] = team_matches['is_loss'].cumsum()
        team_matches['cum_goals'] = team_matches['team_goals'].cumsum()
        team_matches['cum_opp_goals'] = team_matches['opp_goals'].cumsum()
        team_matches['games_played'] = np.arange(1, len(team_matches) + 1)
        
        team_matches['win_rate'] = team_matches['cum_wins'] / team_matches['games_played']
        team_matches['draw_rate'] = team_matches['cum_draws'] / team_matches['games_played']
        team_matches['loss_rate'] = team_matches['cum_losses'] / team_matches['games_played']
        team_matches['avg_goals'] = team_matches['cum_goals'] / team_matches['games_played']
        team_matches['avg_opp_goals'] = team_matches['cum_opp_goals'] / team_matches['games_played']
        
        goals_std = []
        for i in range(len(team_matches)):
            goals_std.append(team_matches['team_goals'][:i+1].std() if i >= 1 else 0)
        team_matches['goals_std'] = goals_std
        
        team_matches['recent_form_3'] = team_matches['is_win'].rolling(window=3, min_periods=1).mean()
        team_matches['recent_form_5'] = team_matches['is_win'].rolling(window=5, min_periods=1).mean()
        team_matches['recent_form_10'] = team_matches['is_win'].rolling(window=10, min_periods=1).mean()
        
        team_matches['form_trend'] = team_matches['is_win'].rolling(window=10).mean() - team_matches['is_win'].rolling(window=10).mean().shift(10)
        
        consecutive_wins = []
        consecutive_losses = []
        consecutive_undefeated = []
        current_wins = 0
        current_losses = 0
        current_undefeated = 0
        for _, row in team_matches.iterrows():
            if row['is_win'] == 1:
                current_wins += 1
                current_losses = 0
                current_undefeated += 1
            elif row['is_loss'] == 1:
                current_wins = 0
                current_losses += 1
                current_undefeated = 0
            else:
                current_wins = 0
                current_losses = 0
                current_undefeated += 1
            consecutive_wins.append(current_wins)
            consecutive_losses.append(current_losses)
            consecutive_undefeated.append(current_undefeated)
        
        team_matches['consecutive_wins'] = consecutive_wins
        team_matches['consecutive_losses'] = consecutive_losses
        team_matches['consecutive_undefeated'] = consecutive_undefeated
        
        home_mask = team_matches['home_team_name'] == team
        home_subset = team_matches[home_mask].copy()
        away_subset = team_matches[~home_mask].copy()
        
        if len(home_subset) > 0:
            home_subset['home_win_rate'] = home_subset['is_win'].cumsum() / np.arange(1, len(home_subset) + 1)
            home_subset['home_avg_goals'] = home_subset['team_goals'].cumsum() / np.arange(1, len(home_subset) + 1)
        if len(away_subset) > 0:
            away_subset['away_win_rate'] = away_subset['is_win'].cumsum() / np.arange(1, len(away_subset) + 1)
            away_subset['away_avg_goals'] = away_subset['team_goals'].cumsum() / np.arange(1, len(away_subset) + 1)
        
        if len(home_subset) > 0:
            team_matches = team_matches.merge(
                home_subset[['date', 'home_win_rate', 'home_avg_goals']],
                on='date', how='left'
            )
        else:
            team_matches['home_win_rate'] = 0.0
            team_matches['home_avg_goals'] = 0.0
        
        if len(away_subset) > 0:
            team_matches = team_matches.merge(
                away_subset[['date', 'away_win_rate', 'away_avg_goals']],
                on='date', how='left'
            )
        else:
            team_matches['away_win_rate'] = 0.0
            team_matches['away_avg_goals'] = 0.0
        
        team_matches[['home_win_rate', 'away_win_rate', 'home_avg_goals', 'away_avg_goals']] = \
            team_matches[['home_win_rate', 'away_win_rate', 'home_avg_goals', 'away_avg_goals']].fillna(0)
        
        weighted_win_rates = []
        weighted_avg_goals = []
        for i in range(len(team_matches)):
            dates = team_matches['date'].iloc[:i]
            if len(dates) >= 3:
                weights = calculate_time_decay_weights(dates, team_matches['date'].iloc[i])
                if weights.sum() > 0:
                    win_mask = team_matches['is_win'].iloc[:i].values
                    goals = team_matches['team_goals'].iloc[:i].values
                    weighted_win_rates.append((win_mask * weights).sum() / weights.sum())
                    weighted_avg_goals.append((goals * weights).sum() / weights.sum())
                else:
                    weighted_win_rates.append(team_matches['win_rate'].iloc[i])
                    weighted_avg_goals.append(team_matches['avg_goals'].iloc[i])
            else:
                weighted_win_rates.append(team_matches['win_rate'].iloc[i])
                weighted_avg_goals.append(team_matches['avg_goals'].iloc[i])
        
        team_matches['weighted_win_rate'] = weighted_win_rates
        team_matches['weighted_avg_goals'] = weighted_avg_goals
        
        team_matches['home_advantage'] = team_matches['home_win_rate'] - team_matches['away_win_rate']
        
        league_name = team_matches['competition_name'].iloc[0] if len(team_matches) > 0 else ''
        league_const = get_league_constants(league_name)
        
        team_matches['attack_strength'] = team_matches['avg_goals'] / league_const['avg_home_goals']
        team_matches['defence_strength'] = team_matches['avg_opp_goals'] / league_const['avg_away_goals']
        
        team_stats_cache[team] = team_matches
    
    return team_stats_cache

def build_team_features(df):
    team_stats_cache = precompute_team_stats(df)
    
    home_data = []
    away_data = []
    h2h_data = []
    
    for idx, row in df.iterrows():
        home_team = row['home_team_name']
        away_team = row['away_team_name']
        match_date = row['date']
        league = row['competition_name']
        league_const = get_league_constants(league)
        
        home_hist = team_stats_cache.get(home_team, pd.DataFrame())
        away_hist = team_stats_cache.get(away_team, pd.DataFrame())
        
        home_before = home_hist[home_hist['date'] < match_date]
        away_before = away_hist[away_hist['date'] < match_date]
        
        if len(home_before) > 0:
            h_last = home_before.iloc[-1]
            home_stats = {
                'home_win_rate': h_last['win_rate'],
                'home_draw_rate': h_last['draw_rate'],
                'home_loss_rate': h_last['loss_rate'],
                'home_avg_goals': h_last['avg_goals'],
                'home_avg_opp_goals': h_last['avg_opp_goals'],
                'home_goals_std': h_last['goals_std'],
                'home_recent_form_3': h_last['recent_form_3'],
                'home_recent_form_5': h_last['recent_form_5'],
                'home_recent_form_10': h_last['recent_form_10'],
                'home_form_trend': h_last['form_trend'],
                'home_consecutive_wins': h_last['consecutive_wins'],
                'home_consecutive_losses': h_last['consecutive_losses'],
                'home_consecutive_undefeated': h_last['consecutive_undefeated'],
                'home_games_played': h_last['games_played'],
                'home_weighted_win_rate': h_last['weighted_win_rate'],
                'home_weighted_avg_goals': h_last['weighted_avg_goals'],
                'home_home_win_rate': h_last['home_win_rate'],
                'home_away_win_rate': h_last['away_win_rate'],
                'home_home_goals': h_last['home_avg_goals'],
                'home_away_goals': h_last['away_avg_goals'],
                'home_home_advantage': h_last['home_advantage'],
                'home_attack_strength': h_last['attack_strength'],
                'home_defence_strength': h_last['defence_strength']
            }
        else:
            home_stats = {
                'home_win_rate': 0.33,
                'home_draw_rate': 0.34,
                'home_loss_rate': 0.33,
                'home_avg_goals': league_const['avg_home_goals'],
                'home_avg_opp_goals': league_const['avg_away_goals'],
                'home_goals_std': 1.0,
                'home_recent_form_3': 0.5,
                'home_recent_form_5': 0.5,
                'home_recent_form_10': 0.5,
                'home_form_trend': 0.0,
                'home_consecutive_wins': 0,
                'home_consecutive_losses': 0,
                'home_consecutive_undefeated': 0,
                'home_games_played': 0,
                'home_weighted_win_rate': 0.33,
                'home_weighted_avg_goals': league_const['avg_home_goals'],
                'home_home_win_rate': 0.45,
                'home_away_win_rate': 0.30,
                'home_home_goals': league_const['avg_home_goals'],
                'home_away_goals': league_const['avg_away_goals'],
                'home_home_advantage': league_const.get('home_goal_advantage', 0.30) / league_const['avg_home_goals'],
                'home_attack_strength': 1.0,
                'home_defence_strength': 1.0
            }
        
        if len(away_before) > 0:
            a_last = away_before.iloc[-1]
            away_stats = {
                'away_win_rate': a_last['win_rate'],
                'away_draw_rate': a_last['draw_rate'],
                'away_loss_rate': a_last['loss_rate'],
                'away_avg_goals': a_last['avg_goals'],
                'away_avg_opp_goals': a_last['avg_opp_goals'],
                'away_goals_std': a_last['goals_std'],
                'away_recent_form_3': a_last['recent_form_3'],
                'away_recent_form_5': a_last['recent_form_5'],
                'away_recent_form_10': a_last['recent_form_10'],
                'away_form_trend': a_last['form_trend'],
                'away_consecutive_wins': a_last['consecutive_wins'],
                'away_consecutive_losses': a_last['consecutive_losses'],
                'away_consecutive_undefeated': a_last['consecutive_undefeated'],
                'away_games_played': a_last['games_played'],
                'away_weighted_win_rate': a_last['weighted_win_rate'],
                'away_weighted_avg_goals': a_last['weighted_avg_goals'],
                'away_home_win_rate': a_last['home_win_rate'],
                'away_away_win_rate': a_last['away_win_rate'],
                'away_home_goals': a_last['home_avg_goals'],
                'away_away_goals': a_last['away_avg_goals'],
                'away_home_advantage': a_last['home_advantage'],
                'away_attack_strength': a_last['attack_strength'],
                'away_defence_strength': a_last['defence_strength']
            }
        else:
            away_stats = {
                'away_win_rate': 0.33,
                'away_draw_rate': 0.34,
                'away_loss_rate': 0.33,
                'away_avg_goals': league_const['avg_away_goals'],
                'away_avg_opp_goals': league_const['avg_home_goals'],
                'away_goals_std': 1.0,
                'away_recent_form_3': 0.5,
                'away_recent_form_5': 0.5,
                'away_recent_form_10': 0.5,
                'away_form_trend': 0.0,
                'away_consecutive_wins': 0,
                'away_consecutive_losses': 0,
                'away_consecutive_undefeated': 0,
                'away_games_played': 0,
                'away_weighted_win_rate': 0.33,
                'away_weighted_avg_goals': league_const['avg_away_goals'],
                'away_home_win_rate': 0.45,
                'away_away_win_rate': 0.30,
                'away_home_goals': league_const['avg_home_goals'],
                'away_away_goals': league_const['avg_away_goals'],
                'away_home_advantage': league_const.get('home_goal_advantage', 0.30) / league_const['avg_home_goals'],
                'away_attack_strength': 1.0,
                'away_defence_strength': 1.0
            }
        
        home_data.append(home_stats)
        away_data.append(away_stats)
        
        h2h = calc_h2h_stats(df, home_team, away_team, match_date)
        h2h_data.append(h2h)
    
    home_df = pd.DataFrame(home_data, index=df.index)
    away_df = pd.DataFrame(away_data, index=df.index)
    h2h_df = pd.DataFrame(h2h_data, index=df.index)
    
    team_features = pd.concat([home_df, away_df, h2h_df], axis=1)
    
    team_features['form_diff'] = team_features['home_win_rate'] - team_features['away_win_rate']
    team_features['goals_diff'] = team_features['home_avg_goals'] - team_features['away_avg_goals']
    team_features['defence_diff'] = team_features['away_avg_opp_goals'] - team_features['home_avg_opp_goals']
    team_features['recent_form_diff_3'] = team_features['home_recent_form_3'] - team_features['away_recent_form_3']
    team_features['recent_form_diff_5'] = team_features['home_recent_form_5'] - team_features['away_recent_form_5']
    team_features['recent_form_diff_10'] = team_features['home_recent_form_10'] - team_features['away_recent_form_10']
    team_features['form_trend_diff'] = team_features['home_form_trend'] - team_features['away_form_trend']
    
    team_features['streak_diff'] = (team_features['home_consecutive_wins'] - team_features['home_consecutive_losses']) - \
                                  (team_features['away_consecutive_wins'] - team_features['away_consecutive_losses'])
    team_features['undefeated_diff'] = team_features['home_consecutive_undefeated'] - team_features['away_consecutive_undefeated']
    
    team_features['weighted_form_diff'] = team_features['home_weighted_win_rate'] - team_features['away_weighted_win_rate']
    team_features['weighted_goals_diff'] = team_features['home_weighted_avg_goals'] - team_features['away_weighted_avg_goals']
    
    team_features['venue_diff'] = team_features['home_home_advantage'] - team_features['away_home_advantage']
    team_features['goals_stability_diff'] = team_features['away_goals_std'] - team_features['home_goals_std']
    
    team_features['attack_strength_diff'] = team_features['home_attack_strength'] - team_features['away_attack_strength']
    team_features['defence_strength_diff'] = team_features['home_defence_strength'] - team_features['away_defence_strength']
    
    team_features['h2h_form_diff'] = team_features['h2h_home_win_rate'] - team_features['h2h_away_win_rate']
    team_features['h2h_goals_diff'] = team_features['h2h_avg_goals_home'] - team_features['h2h_avg_goals_away']
    
    league_medians = team_features.groupby(df['competition_name']).transform('median')
    team_features = team_features.fillna(league_medians)
    
    global_medians = team_features.median()
    team_features = team_features.fillna(global_medians)
    
    for col in team_features.columns:
        team_features[col] = winsorize_series(team_features[col], lower_percentile=1, upper_percentile=99)
    
    return team_features

def build_all_features(df):
    temporal_features = build_temporal_features(df)
    league_features = build_league_features(df)
    match_features = build_match_features(df)
    team_features = build_team_features(df)
    
    all_features = pd.concat([temporal_features, league_features, match_features, team_features], axis=1)
    
    all_features = all_features.loc[:, ~all_features.columns.duplicated()]
    
    for col in all_features.select_dtypes(include=[np.number]).columns:
        if col.startswith('league_') and '_' in col and not col.endswith('_mean') and not col.endswith('_std'):
            continue
        if col.startswith('is_'):
            continue
        all_features[col] = winsorize_series(all_features[col], lower_percentile=1, upper_percentile=99)
    
    all_features = all_features.fillna(0)
    
    return all_features, df['result']