import sqlite3
import pandas as pd
import numpy as np
import yaml
import os
import json
import sys
from scipy.stats import poisson, skew, kurtosis
from sklearn.feature_selection import mutual_info_classif
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, log_loss, f1_score
from datetime import datetime

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from odds_temporal_features import build_odds_temporal_features_from_df

DATA_DIR = "g:/zuqiu/五大联赛专属模型/五大联赛专属模型"

CSV_FILES = {
    'EPL': 'EPL_2025-26.csv',
    'BUNDESLIGA': 'BUNDESLIGA_2025-26.csv',
    'LALIGA': 'LALIGA_2025-26.csv',
    'SERIEA': 'SERIEA_2025-26.csv',
    'LIGUE1': 'LIGUE1_2025-26.csv'
}

DB_PATH = "g:/zuqiu/五大联赛专属模型/五大联赛专属模型/data/five_leagues.db"
CONFIG_PATH = "g:/zuqiu/五大联赛专属模型/五大联赛专属模型/config.yaml"
OUTPUT_DIR = "g:/zuqiu/五大联赛专属模型/五大联赛专属模型/output"

os.makedirs(OUTPUT_DIR, exist_ok=True)

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
    'avg_yellow_cards_per_game': 2,
    'avg_saves_per_game': 3.5,
    'avg_total_goals': 2.7,
    'avg_draw_rate': 0.26,
    'home_goal_advantage': 0.30,
    'market_efficiency_baseline': 1.08
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

def normalize_league_data(df, league):
    normalized = df.copy()
    
    if '日期' in df.columns:
        normalized['date'] = pd.to_datetime(df['日期'], format='%d/%m/%Y', errors='coerce')
        normalized['home_team_name'] = df['主队']
        normalized['away_team_name'] = df['客队']
        normalized['homeGoals'] = df['主队进球']
        normalized['awayGoals'] = df['客队进球']
        normalized['homeShots'] = df['主队射门'] if '主队射门' in df.columns else np.nan
        normalized['awayShots'] = df['客队射门'] if '客队射门' in df.columns else np.nan
        normalized['homeShotsOnTarget'] = df['主队射正'] if '主队射正' in df.columns else np.nan
        normalized['awayShotsOnTarget'] = df['客队射正'] if '客队射正' in df.columns else np.nan
        normalized['homeCorners'] = df['主队角球'] if '主队角球' in df.columns else np.nan
        normalized['awayCorners'] = df['客队角球'] if '客队角球' in df.columns else np.nan
        normalized['homeYellowCards'] = df['主队黄牌'] if '主队黄牌' in df.columns else np.nan
        normalized['awayYellowCards'] = df['客队黄牌'] if '客队黄牌' in df.columns else np.nan
        normalized['homeFouls'] = df['主队犯规'] if '主队犯规' in df.columns else np.nan
        normalized['awayFouls'] = df['客队犯规'] if '客队犯规' in df.columns else np.nan
        normalized['homePossession'] = df['主队控球率'] if '主队控球率' in df.columns else np.nan
        
        normalized['bet365_主胜'] = df['bet365_主胜'] if 'bet365_主胜' in df.columns else np.nan
        normalized['bet365_平局'] = df['bet365_平局'] if 'bet365_平局' in df.columns else np.nan
        normalized['bet365_客胜'] = df['bet365_客胜'] if 'bet365_客胜' in df.columns else np.nan
        normalized['Pinnacle_主胜'] = df['Pinnacle_主胜'] if 'Pinnacle_主胜' in df.columns else np.nan
        normalized['Pinnacle_平局'] = df['Pinnacle_平局'] if 'Pinnacle_平局' in df.columns else np.nan
        normalized['Pinnacle_客胜'] = df['Pinnacle_客胜'] if 'Pinnacle_客胜' in df.columns else np.nan
        normalized['最高_主胜'] = df['最高_主胜'] if '最高_主胜' in df.columns else np.nan
        normalized['最高_平局'] = df['最高_平局'] if '最高_平局' in df.columns else np.nan
        normalized['最高_客胜'] = df['最高_客胜'] if '最高_客胜' in df.columns else np.nan
        normalized['平均_主胜'] = df['平均_主胜'] if '平均_主胜' in df.columns else np.nan
        normalized['平均_平局'] = df['平均_平局'] if '平均_平局' in df.columns else np.nan
        normalized['平均_客胜'] = df['平均_客胜'] if '平均_客胜' in df.columns else np.nan
        normalized['bet365_大2.5'] = df['bet365_大2.5'] if 'bet365_大2.5' in df.columns else np.nan
        normalized['bet365_小2.5'] = df['bet365_小2.5'] if 'bet365_小2.5' in df.columns else np.nan
        normalized['Pinnacle_大2.5'] = df['Pinnacle_大2.5'] if 'Pinnacle_大2.5' in df.columns else np.nan
        normalized['Pinnacle_小2.5'] = df['Pinnacle_小2.5'] if 'Pinnacle_小2.5' in df.columns else np.nan
        normalized['bet365_亚盘主'] = df['bet365_亚盘主'] if 'bet365_亚盘主' in df.columns else np.nan
        normalized['bet365_亚盘客'] = df['bet365_亚盘客'] if 'bet365_亚盘客' in df.columns else np.nan
        normalized['Pinnacle_亚盘主'] = df['Pinnacle_亚盘主'] if 'Pinnacle_亚盘主' in df.columns else np.nan
        normalized['Pinnacle_亚盘客'] = df['Pinnacle_亚盘客'] if 'Pinnacle_亚盘客' in df.columns else np.nan
        normalized['亚盘盘口'] = df['亚盘盘口'] if '亚盘盘口' in df.columns else np.nan
    else:
        normalized['date'] = pd.to_datetime(df['Date'], format='%d/%m/%Y', errors='coerce')
        normalized['home_team_name'] = df['HomeTeam']
        normalized['away_team_name'] = df['AwayTeam']
        normalized['homeGoals'] = df['FTHG']
        normalized['awayGoals'] = df['FTAG']
        normalized['homeShots'] = df['HS'] if 'HS' in df.columns else np.nan
        normalized['awayShots'] = df['AS'] if 'AS' in df.columns else np.nan
        normalized['homeShotsOnTarget'] = df['HST'] if 'HST' in df.columns else np.nan
        normalized['awayShotsOnTarget'] = df['AST'] if 'AST' in df.columns else np.nan
        normalized['homeCorners'] = df['HC'] if 'HC' in df.columns else np.nan
        normalized['awayCorners'] = df['AC'] if 'AC' in df.columns else np.nan
        normalized['homeYellowCards'] = df['HY'] if 'HY' in df.columns else np.nan
        normalized['awayYellowCards'] = df['AY'] if 'AY' in df.columns else np.nan
        normalized['homeFouls'] = df['HF'] if 'HF' in df.columns else np.nan
        normalized['awayFouls'] = df['AF'] if 'AF' in df.columns else np.nan
        normalized['homePossession'] = np.nan
        
        normalized['bet365_主胜'] = df['B365H'] if 'B365H' in df.columns else np.nan
        normalized['bet365_平局'] = df['B365D'] if 'B365D' in df.columns else np.nan
        normalized['bet365_客胜'] = df['B365A'] if 'B365A' in df.columns else np.nan
        normalized['Pinnacle_主胜'] = df['PSH'] if 'PSH' in df.columns else np.nan
        normalized['Pinnacle_平局'] = df['PSD'] if 'PSD' in df.columns else np.nan
        normalized['Pinnacle_客胜'] = df['PSA'] if 'PSA' in df.columns else np.nan
        normalized['最高_主胜'] = df['MaxH'] if 'MaxH' in df.columns else np.nan
        normalized['最高_平局'] = df['MaxD'] if 'MaxD' in df.columns else np.nan
        normalized['最高_客胜'] = df['MaxA'] if 'MaxA' in df.columns else np.nan
        normalized['平均_主胜'] = df['AvgH'] if 'AvgH' in df.columns else np.nan
        normalized['平均_平局'] = df['AvgD'] if 'AvgD' in df.columns else np.nan
        normalized['平均_客胜'] = df['AvgA'] if 'AvgA' in df.columns else np.nan
        normalized['bet365_大2.5'] = df['B365>2.5'] if 'B365>2.5' in df.columns else np.nan
        normalized['bet365_小2.5'] = df['B365<2.5'] if 'B365<2.5' in df.columns else np.nan
        normalized['Pinnacle_大2.5'] = df['P>2.5'] if 'P>2.5' in df.columns else np.nan
        normalized['Pinnacle_小2.5'] = df['P<2.5'] if 'P<2.5' in df.columns else np.nan
        normalized['bet365_亚盘主'] = df['B365AHH'] if 'B365AHH' in df.columns else np.nan
        normalized['bet365_亚盘客'] = df['B365AHA'] if 'B365AHA' in df.columns else np.nan
        normalized['Pinnacle_亚盘主'] = df['PAHH'] if 'PAHH' in df.columns else np.nan
        normalized['Pinnacle_亚盘客'] = df['PAHA'] if 'PAHA' in df.columns else np.nan
        normalized['亚盘盘口'] = df['AHh'] if 'AHh' in df.columns else np.nan
    
    normalized['competition_name'] = league
    return normalized

def load_csv_odds_data():
    dfs = []
    for league, filename in CSV_FILES.items():
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            df = pd.read_csv(filepath, encoding='utf-8')
            df = normalize_league_data(df, league)
            dfs.append(df)
            print(f"Loaded {len(df)} matches from {filename}")
        else:
            print(f"Warning: {filepath} not found")
    
    if not dfs:
        return pd.DataFrame()
    
    df = pd.concat(dfs, ignore_index=True)
    df = df.dropna(subset=['homeGoals', 'awayGoals']).sort_values('date').reset_index(drop=True)
    
    df['result'] = np.where(df['homeGoals'] > df['awayGoals'], 2,
                           np.where(df['homeGoals'] < df['awayGoals'], 0, 1))
    df['goal_diff'] = df['homeGoals'] - df['awayGoals']
    df['total_goals'] = df['homeGoals'] + df['awayGoals']
    
    return df

def load_match_data(db_path=None, include_csv_odds=True):
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
    
    if include_csv_odds:
        csv_df = load_csv_odds_data()
        if len(csv_df) > 0:
            db_keys = set(zip(df['date'].astype(str), df['home_team_name'], df['away_team_name']))
            csv_keys = list(zip(csv_df['date'].astype(str), csv_df['home_team_name'], csv_df['away_team_name']))
            csv_df = csv_df[~pd.Series(csv_keys).isin(db_keys)]
            df = pd.concat([df, csv_df], ignore_index=True).sort_values('date').reset_index(drop=True)
            print(f"Loaded {len(csv_df)} additional matches with odds data from CSV")
    
    return df

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
    
    features['is_evening_match'] = (df['date'].dt.hour >= 18).astype(int) if 'hour' in str(df['date'].dtype) else 0
    
    feature_info = {
        'month': {'description': '比赛月份', 'source': 'date', 'calculation': 'dt.month'},
        'day_of_week': {'description': '比赛周几', 'source': 'date', 'calculation': 'dt.dayofweek'},
        'is_weekend': {'description': '是否周末比赛', 'source': 'date', 'calculation': 'dayofweek >= 5'},
        'is_early_season': {'description': '是否赛季初期(8-9月)', 'source': 'date', 'calculation': 'month in [8,9]'},
        'is_mid_season': {'description': '是否赛季中期(10-2月)', 'source': 'date', 'calculation': 'month in [10,11,12,1,2]'},
        'is_late_season': {'description': '是否赛季末期(3-5月)', 'source': 'date', 'calculation': 'month in [3,4,5]'},
        'season_week': {'description': '赛季周数', 'source': 'date', 'calculation': '(date - season_start) // 7'},
        'days_since_season_start': {'description': '距赛季开始天数', 'source': 'date', 'calculation': 'date - season_start'},
        'is_evening_match': {'description': '是否晚间比赛', 'source': 'date', 'calculation': 'hour >= 18'}
    }
    
    return features, feature_info

def extract_handicap(handicap_str):
    if pd.isna(handicap_str):
        return 0
    if isinstance(handicap_str, str):
        try:
            return float(handicap_str)
        except:
            return 0
    return float(handicap_str)

def build_odds_features(df):
    features = pd.DataFrame(index=df.index)
    feature_info = {}
    
    col_mapping = {
        'bet365_主胜': 'B365H', 'bet365_平局': 'B365D', 'bet365_客胜': 'B365A',
        'Pinnacle_主胜': 'PSH', 'Pinnacle_平局': 'PSD', 'Pinnacle_客胜': 'PSA',
        '最高_主胜': 'MaxH', '最高_平局': 'MaxD', '最高_客胜': 'MaxA',
        '平均_主胜': 'AvgH', '平均_平局': 'AvgD', '平均_客胜': 'AvgA',
        'bet365_大2.5': 'B365>2.5', 'bet365_小2.5': 'B365<2.5',
        'Pinnacle_大2.5': 'P>2.5', 'Pinnacle_小2.5': 'P<2.5',
        'bet365_亚盘主': 'B365AHH', 'bet365_亚盘客': 'B365AHA',
        'Pinnacle_亚盘主': 'PAHH', 'Pinnacle_亚盘客': 'PAHA',
        '亚盘盘口': 'AHh'
    }
    
    def get_col(chinese_name):
        if chinese_name in df.columns:
            return df[chinese_name]
        english_name = col_mapping.get(chinese_name)
        if english_name and english_name in df.columns:
            return df[english_name]
        return None
    
    odds_cols = [
        'bet365_主胜', 'bet365_平局', 'bet365_客胜',
        'Pinnacle_主胜', 'Pinnacle_平局', 'Pinnacle_客胜',
        '最高_主胜', '最高_平局', '最高_客胜',
        '平均_主胜', '平均_平局', '平均_客胜'
    ]
    
    for col in odds_cols:
        col_data = get_col(col)
        if col_data is not None:
            col_data = col_data.fillna(col_data.median())
            col_data = winsorize_series(col_data, lower_percentile=1, upper_percentile=99)
            features[col] = col_data
            feature_info[col] = {'description': f'{col}赔率', 'source': 'CSV赔率数据', 'calculation': '原始值，缺失填充中位数'}
        else:
            features[col] = np.nan
            feature_info[col] = {'description': f'{col}赔率', 'source': 'CSV赔率数据', 'calculation': '无数据'}
    
    features['pinnacle_h2a'] = features['Pinnacle_主胜'] / (features['Pinnacle_客胜'] + 0.01)
    features['pinnacle_prob_home'] = 1 / (features['Pinnacle_主胜'] + 1e-8)
    features['pinnacle_prob_draw'] = 1 / (features['Pinnacle_平局'] + 1e-8)
    features['pinnacle_prob_away'] = 1 / (features['Pinnacle_客胜'] + 1e-8)
    features['pinnacle_prob_sum'] = features['pinnacle_prob_home'] + features['pinnacle_prob_draw'] + features['pinnacle_prob_away']
    features['pinnacle_prob_sum'] = features['pinnacle_prob_sum'].replace(0, 1.0)
    features['pinnacle_implied_home'] = features['pinnacle_prob_home'] / features['pinnacle_prob_sum']
    features['pinnacle_implied_draw'] = features['pinnacle_prob_draw'] / features['pinnacle_prob_sum']
    features['pinnacle_implied_away'] = features['pinnacle_prob_away'] / features['pinnacle_prob_sum']
    
    feature_info['pinnacle_h2a'] = {'description': 'Pinnacle主客赔率比', 'source': 'Pinnacle赔率', 'calculation': '主胜赔率/客胜赔率'}
    feature_info['pinnacle_prob_home'] = {'description': 'Pinnacle主胜隐含概率(未归一化)', 'source': 'Pinnacle赔率', 'calculation': '1/Pinnacle_主胜'}
    feature_info['pinnacle_prob_draw'] = {'description': 'Pinnacle平局隐含概率(未归一化)', 'source': 'Pinnacle赔率', 'calculation': '1/Pinnacle_平局'}
    feature_info['pinnacle_prob_away'] = {'description': 'Pinnacle客胜隐含概率(未归一化)', 'source': 'Pinnacle赔率', 'calculation': '1/Pinnacle_客胜'}
    feature_info['pinnacle_prob_sum'] = {'description': 'Pinnacle隐含概率和', 'source': 'Pinnacle赔率', 'calculation': '三个概率之和'}
    feature_info['pinnacle_implied_home'] = {'description': 'Pinnacle主胜隐含概率(归一化)', 'source': 'Pinnacle赔率', 'calculation': '主胜概率/总概率'}
    feature_info['pinnacle_implied_draw'] = {'description': 'Pinnacle平局隐含概率(归一化)', 'source': 'Pinnacle赔率', 'calculation': '平局概率/总概率'}
    feature_info['pinnacle_implied_away'] = {'description': 'Pinnacle客胜隐含概率(归一化)', 'source': 'Pinnacle赔率', 'calculation': '客胜概率/总概率'}
    
    features['avg_h2a'] = features['平均_主胜'] / (features['平均_客胜'] + 0.01)
    features['avg_prob_home'] = 1 / (features['平均_主胜'] + 1e-8)
    features['avg_prob_draw'] = 1 / (features['平均_平局'] + 1e-8)
    features['avg_prob_away'] = 1 / (features['平均_客胜'] + 1e-8)
    features['avg_prob_sum'] = features['avg_prob_home'] + features['avg_prob_draw'] + features['avg_prob_away']
    features['avg_prob_sum'] = features['avg_prob_sum'].replace(0, 1.0)
    features['avg_implied_home'] = features['avg_prob_home'] / features['avg_prob_sum']
    features['avg_implied_draw'] = features['avg_prob_draw'] / features['avg_prob_sum']
    features['avg_implied_away'] = features['avg_prob_away'] / features['avg_prob_sum']
    
    feature_info['avg_h2a'] = {'description': '平均主客赔率比', 'source': '平均赔率', 'calculation': '平均主胜赔率/平均客胜赔率'}
    feature_info['avg_prob_home'] = {'description': '平均主胜隐含概率(未归一化)', 'source': '平均赔率', 'calculation': '1/平均_主胜'}
    feature_info['avg_prob_draw'] = {'description': '平均平局隐含概率(未归一化)', 'source': '平均赔率', 'calculation': '1/平均_平局'}
    feature_info['avg_prob_away'] = {'description': '平均客胜隐含概率(未归一化)', 'source': '平均赔率', 'calculation': '1/平均_客胜'}
    feature_info['avg_prob_sum'] = {'description': '平均隐含概率和', 'source': '平均赔率', 'calculation': '三个概率之和'}
    feature_info['avg_implied_home'] = {'description': '平均主胜隐含概率(归一化)', 'source': '平均赔率', 'calculation': '主胜概率/总概率'}
    feature_info['avg_implied_draw'] = {'description': '平均平局隐含概率(归一化)', 'source': '平均赔率', 'calculation': '平局概率/总概率'}
    feature_info['avg_implied_away'] = {'description': '平均客胜隐含概率(归一化)', 'source': '平均赔率', 'calculation': '客胜概率/总概率'}
    
    features['odds_diff_h2a'] = features['平均_主胜'] - features['平均_客胜']
    features['odds_spread'] = features['最高_主胜'] - features['Pinnacle_主胜']
    features['odds_margin'] = features['pinnacle_prob_sum'] - 1
    
    feature_info['odds_diff_h2a'] = {'description': '主客赔率差值', 'source': '平均赔率', 'calculation': '平均主胜赔率 - 平均客胜赔率'}
    feature_info['odds_spread'] = {'description': '最高与Pinnacle赔率价差', 'source': '赔率数据', 'calculation': '最高主胜赔率 - Pinnacle主胜赔率'}
    feature_info['odds_margin'] = {'description': 'Pinnacle赔率margin', 'source': 'Pinnacle赔率', 'calculation': 'prob_sum - 1'}
    
    features['pinnacle_margin_flag'] = (features['pinnacle_prob_sum'] > 1.15).astype(int)
    features['avg_margin_flag'] = (features['avg_prob_sum'] > 1.15).astype(int)
    
    feature_info['pinnacle_margin_flag'] = {'description': 'Pinnacle高margin标记', 'source': 'Pinnacle赔率', 'calculation': 'prob_sum > 1.15'}
    feature_info['avg_margin_flag'] = {'description': '平均赔率高margin标记', 'source': '平均赔率', 'calculation': 'prob_sum > 1.15'}
    
    over_under_cols = ['bet365_大2.5', 'bet365_小2.5', 'Pinnacle_大2.5', 'Pinnacle_小2.5']
    for col in over_under_cols:
        col_data = get_col(col)
        if col_data is not None:
            col_data = col_data.fillna(col_data.median())
            col_data = winsorize_series(col_data, lower_percentile=1, upper_percentile=99)
            features[col] = col_data
            feature_info[col] = {'description': f'{col}赔率', 'source': 'CSV赔率数据', 'calculation': '原始值，缺失填充中位数'}
        else:
            features[col] = np.nan
            feature_info[col] = {'description': f'{col}赔率', 'source': 'CSV赔率数据', 'calculation': '无数据'}
    
    features['pinnacle_over_prob'] = 1 / (features['Pinnacle_大2.5'] + 1e-8)
    features['pinnacle_under_prob'] = 1 / (features['Pinnacle_小2.5'] + 1e-8)
    features['pinnacle_over_under_ratio'] = features['Pinnacle_大2.5'] / (features['Pinnacle_小2.5'] + 0.01)
    
    feature_info['pinnacle_over_prob'] = {'description': 'Pinnacle大球隐含概率', 'source': 'Pinnacle总进球赔率', 'calculation': '1/Pinnacle_大2.5'}
    feature_info['pinnacle_under_prob'] = {'description': 'Pinnacle小球隐含概率', 'source': 'Pinnacle总进球赔率', 'calculation': '1/Pinnacle_小2.5'}
    feature_info['pinnacle_over_under_ratio'] = {'description': 'Pinnacle大球小球赔率比', 'source': 'Pinnacle总进球赔率', 'calculation': '大球赔率/小球赔率'}
    
    asian_cols = ['bet365_亚盘主', 'bet365_亚盘客', 'Pinnacle_亚盘主', 'Pinnacle_亚盘客']
    for col in asian_cols:
        col_data = get_col(col)
        if col_data is not None:
            col_data = col_data.fillna(col_data.median())
            col_data = winsorize_series(col_data, lower_percentile=1, upper_percentile=99)
            features[col] = col_data
            feature_info[col] = {'description': f'{col}赔率', 'source': 'CSV赔率数据', 'calculation': '原始值，缺失填充中位数'}
        else:
            features[col] = np.nan
            feature_info[col] = {'description': f'{col}赔率', 'source': 'CSV赔率数据', 'calculation': '无数据'}
    
    features['asian_handicap_diff'] = features['Pinnacle_亚盘主'] - features['Pinnacle_亚盘客']
    
    feature_info['asian_handicap_diff'] = {'description': 'Pinnacle亚盘主客赔率差', 'source': 'Pinnacle亚盘赔率', 'calculation': '亚盘主赔率 - 亚盘客赔率'}
    
    handicap_col = get_col('亚盘盘口')
    if handicap_col is not None:
        features['handicap_value'] = handicap_col.apply(extract_handicap)
    else:
        features['handicap_value'] = 0
    
    features['handicap_value'] = winsorize_series(features['handicap_value'], lower_percentile=1, upper_percentile=99)
    feature_info['handicap_value'] = {'description': '亚盘盘口数值', 'source': 'CSV赔率数据', 'calculation': '提取盘口数值'}
    
    features['has_odds_data'] = (~features['Pinnacle_主胜'].isna() & ~features['Pinnacle_平局'].isna() & ~features['Pinnacle_客胜'].isna()).astype(int)
    feature_info['has_odds_data'] = {'description': '是否有赔率数据标记', 'source': 'Pinnacle赔率', 'calculation': '三项赔率均非空'}
    
    return features, feature_info

def build_league_features(df):
    features = pd.DataFrame(index=df.index)
    
    league_dummies = pd.get_dummies(df['competition_name'], prefix='league')
    features = pd.concat([features, league_dummies], axis=1)
    
    feature_info = {}
    for league in df['competition_name'].unique():
        feature_info[f'league_{league}'] = {'description': f'是否{league}', 'source': 'competition_name', 'calculation': 'one-hot编码'}
    
    return features, feature_info

def build_match_features(df):
    features = pd.DataFrame(index=df.index)
    
    features['is_home'] = 1
    
    if 'home_league_strength' in df.columns and 'away_league_strength' in df.columns:
        features['league_strength_diff'] = df['home_league_strength'].fillna(0) - df['away_league_strength'].fillna(0)
    else:
        features['league_strength_diff'] = 0
    
    feature_info = {
        'is_home': {'description': '主场标记', 'source': '主场身份', 'calculation': '固定值1'},
        'league_strength_diff': {'description': '联赛实力差值', 'source': 'home_league_strength, away_league_strength', 'calculation': 'home_league_strength - away_league_strength'}
    }
    
    return features, feature_info

def calc_h2h_stats(df, home_team, away_team, match_date):
    h2h_hist = df[(((df['home_team_name'] == home_team) & (df['away_team_name'] == away_team)) |
                   ((df['home_team_name'] == away_team) & (df['away_team_name'] == home_team))) &
                  (df['date'] < match_date)].sort_values('date').tail(15)
    
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
            'h2h_goals_std': 1.0,
            'h2h_home_xg_avg': 1.2,
            'h2h_away_xg_avg': 1.0,
            'h2h_home_big_chances_avg': 2.0,
            'h2h_away_big_chances_avg': 1.5
        }
    
    home_wins = 0
    away_wins = 0
    draws = 0
    home_goals_total = 0
    away_goals_total = 0
    home_xg_total = 0
    away_xg_total = 0
    home_big_chances_total = 0
    away_big_chances_total = 0
    goal_diffs = []
    
    for _, r in h2h_hist.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
            hxg, axg = r['homeXg'] if pd.notna(r['homeXg']) else 0, r['awayXg'] if pd.notna(r['awayXg']) else 0
            hbc, abc = r['homeBigChances'] if pd.notna(r['homeBigChances']) else 0, r['awayBigChances'] if pd.notna(r['awayBigChances']) else 0
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
            hxg, axg = r['awayXg'] if pd.notna(r['awayXg']) else 0, r['homeXg'] if pd.notna(r['homeXg']) else 0
            hbc, abc = r['awayBigChances'] if pd.notna(r['awayBigChances']) else 0, r['homeBigChances'] if pd.notna(r['homeBigChances']) else 0
        
        home_goals_total += hg
        away_goals_total += ag
        home_xg_total += hxg
        away_xg_total += axg
        home_big_chances_total += hbc
        away_big_chances_total += abc
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
        'h2h_goals_std': np.std(goal_diffs) if len(goal_diffs) > 1 else 1.0,
        'h2h_home_xg_avg': home_xg_total / total,
        'h2h_away_xg_avg': away_xg_total / total,
        'h2h_home_big_chances_avg': home_big_chances_total / total,
        'h2h_away_big_chances_avg': away_big_chances_total / total
    }

def precompute_team_stats(df):
    all_teams = pd.concat([df['home_team_name'], df['away_team_name']]).unique()
    
    team_stats_cache = {}
    
    for team in all_teams:
        team_matches = df[(df['home_team_name'] == team) | (df['away_team_name'] == team)].sort_values('date').copy()
        
        is_home = team_matches['home_team_name'] == team
        team_goals = np.where(is_home, team_matches['homeGoals'], team_matches['awayGoals'])
        opp_goals = np.where(is_home, team_matches['awayGoals'], team_matches['homeGoals'])
        team_xg = np.where(is_home, team_matches['homeXg'].fillna(0), team_matches['awayXg'].fillna(0))
        team_xgot = np.where(is_home, team_matches['homeXgot'].fillna(0), team_matches['awayXgot'].fillna(0))
        
        team_matches['team_goals'] = team_goals
        team_matches['opp_goals'] = opp_goals
        team_matches['team_xg'] = team_xg
        team_matches['team_xgot'] = team_xgot
        team_matches['is_win'] = (team_goals > opp_goals).astype(int)
        team_matches['is_draw'] = (team_goals == opp_goals).astype(int)
        team_matches['is_loss'] = (team_goals < opp_goals).astype(int)
        
        team_matches['cum_wins'] = team_matches['is_win'].cumsum()
        team_matches['cum_draws'] = team_matches['is_draw'].cumsum()
        team_matches['cum_losses'] = team_matches['is_loss'].cumsum()
        team_matches['cum_goals'] = team_matches['team_goals'].cumsum()
        team_matches['cum_opp_goals'] = team_matches['opp_goals'].cumsum()
        team_matches['cum_xg'] = team_matches['team_xg'].cumsum()
        team_matches['games_played'] = np.arange(1, len(team_matches) + 1)
        
        team_matches['win_rate'] = team_matches['cum_wins'] / team_matches['games_played']
        team_matches['draw_rate'] = team_matches['cum_draws'] / team_matches['games_played']
        team_matches['loss_rate'] = team_matches['cum_losses'] / team_matches['games_played']
        team_matches['avg_goals'] = team_matches['cum_goals'] / team_matches['games_played']
        team_matches['avg_opp_goals'] = team_matches['cum_opp_goals'] / team_matches['games_played']
        team_matches['avg_xg'] = team_matches['cum_xg'] / team_matches['games_played']
        
        goals_std = []
        for i in range(len(team_matches)):
            goals_std.append(team_matches['team_goals'][:i+1].std() if i >= 1 else 0)
        team_matches['goals_std'] = goals_std
        
        team_matches['recent_form_3'] = team_matches['is_win'].rolling(window=3, min_periods=1).mean()
        team_matches['recent_form_5'] = team_matches['is_win'].rolling(window=5, min_periods=1).mean()
        team_matches['recent_form_10'] = team_matches['is_win'].rolling(window=10, min_periods=1).mean()
        team_matches['recent_form_15'] = team_matches['is_win'].rolling(window=15, min_periods=1).mean()
        
        team_matches['form_trend'] = team_matches['is_win'].rolling(window=10).mean() - team_matches['is_win'].rolling(window=10).mean().shift(10)
        team_matches['form_trend_5'] = team_matches['is_win'].rolling(window=5).mean() - team_matches['is_win'].rolling(window=5).mean().shift(5)
        
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
        
        dates = team_matches['date'].values
        n = len(dates)
        half_life_days = 14
        
        if n >= 2:
            days_diff = dates[None, :] - dates[:, None]
            days_diff = days_diff.astype('timedelta64[D]').astype(float)
            weights_matrix = np.exp(-days_diff * np.log(2) / half_life_days)
            np.fill_diagonal(weights_matrix, 0)
            weights_matrix[weights_matrix < 0] = 0
            weights_matrix = np.maximum(weights_matrix, 0.01)
            
            weights_sum = weights_matrix.sum(axis=1)
            weights_sum[weights_sum == 0] = 1
            
            weighted_win_rates = (weights_matrix @ team_matches['is_win'].values) / weights_sum
            weighted_avg_goals = (weights_matrix @ team_matches['team_goals'].values) / weights_sum
            weighted_avg_xg = (weights_matrix @ team_matches['team_xg'].values) / weights_sum
        else:
            weighted_win_rates = team_matches['win_rate'].values
            weighted_avg_goals = team_matches['avg_goals'].values
            weighted_avg_xg = team_matches['avg_xg'].values
        
        team_matches['weighted_win_rate'] = weighted_win_rates
        team_matches['weighted_avg_goals'] = weighted_avg_goals
        team_matches['weighted_avg_xg'] = weighted_avg_xg
        
        team_matches['home_advantage'] = team_matches['home_win_rate'] - team_matches['away_win_rate']
        
        league_name = team_matches['competition_name'].iloc[0] if len(team_matches) > 0 else ''
        league_const = get_league_constants(league_name)
        
        team_matches['attack_strength'] = team_matches['avg_goals'] / league_const['avg_home_goals']
        team_matches['defence_strength'] = team_matches['avg_opp_goals'] / league_const['avg_away_goals']
        team_matches['xg_efficiency'] = team_matches['avg_goals'] / (team_matches['avg_xg'] + 0.01)
        
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
        league_name = row['competition_name']
        league_const = get_league_constants(league_name)
        
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
                'home_avg_xg': h_last['avg_xg'],
                'home_goals_std': h_last['goals_std'],
                'home_recent_form_3': h_last['recent_form_3'],
                'home_recent_form_5': h_last['recent_form_5'],
                'home_recent_form_10': h_last['recent_form_10'],
                'home_recent_form_15': h_last['recent_form_15'],
                'home_form_trend': h_last['form_trend'],
                'home_form_trend_5': h_last['form_trend_5'],
                'home_consecutive_wins': h_last['consecutive_wins'],
                'home_consecutive_losses': h_last['consecutive_losses'],
                'home_consecutive_undefeated': h_last['consecutive_undefeated'],
                'home_games_played': h_last['games_played'],
                'home_weighted_win_rate': h_last['weighted_win_rate'],
                'home_weighted_avg_goals': h_last['weighted_avg_goals'],
                'home_weighted_avg_xg': h_last['weighted_avg_xg'],
                'home_home_win_rate': h_last['home_win_rate'],
                'home_away_win_rate': h_last['away_win_rate'],
                'home_home_goals': h_last['home_avg_goals'],
                'home_away_goals': h_last['away_avg_goals'],
                'home_home_advantage': h_last['home_advantage'],
                'home_attack_strength': h_last['attack_strength'],
                'home_defence_strength': h_last['defence_strength'],
                'home_xg_efficiency': h_last['xg_efficiency']
            }
        else:
            home_stats = {
                'home_win_rate': 0.33,
                'home_draw_rate': 0.34,
                'home_loss_rate': 0.33,
                'home_avg_goals': league_const['avg_home_goals'],
                'home_avg_opp_goals': league_const['avg_away_goals'],
                'home_avg_xg': league_const['avg_xg_per_game'],
                'home_goals_std': 1.0,
                'home_recent_form_3': 0.5,
                'home_recent_form_5': 0.5,
                'home_recent_form_10': 0.5,
                'home_recent_form_15': 0.5,
                'home_form_trend': 0.0,
                'home_form_trend_5': 0.0,
                'home_consecutive_wins': 0,
                'home_consecutive_losses': 0,
                'home_consecutive_undefeated': 0,
                'home_games_played': 0,
                'home_weighted_win_rate': 0.33,
                'home_weighted_avg_goals': league_const['avg_home_goals'],
                'home_weighted_avg_xg': league_const['avg_xg_per_game'],
                'home_home_win_rate': 0.45,
                'home_away_win_rate': 0.30,
                'home_home_goals': league_const['avg_home_goals'],
                'home_away_goals': league_const['avg_away_goals'],
                'home_home_advantage': league_const.get('home_goal_advantage', 0.30) / league_const['avg_home_goals'],
                'home_attack_strength': 1.0,
                'home_defence_strength': 1.0,
                'home_xg_efficiency': 1.0
            }
        
        if len(away_before) > 0:
            a_last = away_before.iloc[-1]
            away_stats = {
                'away_win_rate': a_last['win_rate'],
                'away_draw_rate': a_last['draw_rate'],
                'away_loss_rate': a_last['loss_rate'],
                'away_avg_goals': a_last['avg_goals'],
                'away_avg_opp_goals': a_last['avg_opp_goals'],
                'away_avg_xg': a_last['avg_xg'],
                'away_goals_std': a_last['goals_std'],
                'away_recent_form_3': a_last['recent_form_3'],
                'away_recent_form_5': a_last['recent_form_5'],
                'away_recent_form_10': a_last['recent_form_10'],
                'away_recent_form_15': a_last['recent_form_15'],
                'away_form_trend': a_last['form_trend'],
                'away_form_trend_5': a_last['form_trend_5'],
                'away_consecutive_wins': a_last['consecutive_wins'],
                'away_consecutive_losses': a_last['consecutive_losses'],
                'away_consecutive_undefeated': a_last['consecutive_undefeated'],
                'away_games_played': a_last['games_played'],
                'away_weighted_win_rate': a_last['weighted_win_rate'],
                'away_weighted_avg_goals': a_last['weighted_avg_goals'],
                'away_weighted_avg_xg': a_last['weighted_avg_xg'],
                'away_home_win_rate': a_last['home_win_rate'],
                'away_away_win_rate': a_last['away_win_rate'],
                'away_home_goals': a_last['home_avg_goals'],
                'away_away_goals': a_last['away_avg_goals'],
                'away_home_advantage': a_last['home_advantage'],
                'away_attack_strength': a_last['attack_strength'],
                'away_defence_strength': a_last['defence_strength'],
                'away_xg_efficiency': a_last['xg_efficiency']
            }
        else:
            away_stats = {
                'away_win_rate': 0.33,
                'away_draw_rate': 0.34,
                'away_loss_rate': 0.33,
                'away_avg_goals': league_const['avg_away_goals'],
                'away_avg_opp_goals': league_const['avg_home_goals'],
                'away_avg_xg': league_const['avg_xg_per_game'],
                'away_goals_std': 1.0,
                'away_recent_form_3': 0.5,
                'away_recent_form_5': 0.5,
                'away_recent_form_10': 0.5,
                'away_recent_form_15': 0.5,
                'away_form_trend': 0.0,
                'away_form_trend_5': 0.0,
                'away_consecutive_wins': 0,
                'away_consecutive_losses': 0,
                'away_consecutive_undefeated': 0,
                'away_games_played': 0,
                'away_weighted_win_rate': 0.33,
                'away_weighted_avg_goals': league_const['avg_away_goals'],
                'away_weighted_avg_xg': league_const['avg_xg_per_game'],
                'away_home_win_rate': 0.45,
                'away_away_win_rate': 0.30,
                'away_home_goals': league_const['avg_home_goals'],
                'away_away_goals': league_const['avg_away_goals'],
                'away_home_advantage': league_const.get('home_goal_advantage', 0.30) / league_const['avg_home_goals'],
                'away_attack_strength': 1.0,
                'away_defence_strength': 1.0,
                'away_xg_efficiency': 1.0
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
    team_features['xg_diff'] = team_features['home_avg_xg'] - team_features['away_avg_xg']
    team_features['recent_form_diff_3'] = team_features['home_recent_form_3'] - team_features['away_recent_form_3']
    team_features['recent_form_diff_5'] = team_features['home_recent_form_5'] - team_features['away_recent_form_5']
    team_features['recent_form_diff_10'] = team_features['home_recent_form_10'] - team_features['away_recent_form_10']
    team_features['recent_form_diff_15'] = team_features['home_recent_form_15'] - team_features['away_recent_form_15']
    team_features['form_trend_diff'] = team_features['home_form_trend'] - team_features['away_form_trend']
    team_features['form_trend_diff_5'] = team_features['home_form_trend_5'] - team_features['away_form_trend_5']
    
    team_features['streak_diff'] = (team_features['home_consecutive_wins'] - team_features['home_consecutive_losses']) - \
                                  (team_features['away_consecutive_wins'] - team_features['away_consecutive_losses'])
    team_features['undefeated_diff'] = team_features['home_consecutive_undefeated'] - team_features['away_consecutive_undefeated']
    
    team_features['weighted_form_diff'] = team_features['home_weighted_win_rate'] - team_features['away_weighted_win_rate']
    team_features['weighted_goals_diff'] = team_features['home_weighted_avg_goals'] - team_features['away_weighted_avg_goals']
    team_features['weighted_xg_diff'] = team_features['home_weighted_avg_xg'] - team_features['away_weighted_avg_xg']
    
    team_features['venue_diff'] = team_features['home_home_advantage'] - team_features['away_home_advantage']
    team_features['goals_stability_diff'] = team_features['away_goals_std'] - team_features['home_goals_std']
    
    team_features['attack_strength_diff'] = team_features['home_attack_strength'] - team_features['away_attack_strength']
    team_features['defence_strength_diff'] = team_features['home_defence_strength'] - team_features['away_defence_strength']
    team_features['xg_efficiency_diff'] = team_features['home_xg_efficiency'] - team_features['away_xg_efficiency']
    
    team_features['h2h_form_diff'] = team_features['h2h_home_win_rate'] - team_features['h2h_away_win_rate']
    team_features['h2h_goals_diff'] = team_features['h2h_avg_goals_home'] - team_features['h2h_avg_goals_away']
    team_features['h2h_xg_diff'] = team_features['h2h_home_xg_avg'] - team_features['h2h_away_xg_avg']
    
    league_medians = team_features.groupby(df['competition_name']).transform('median')
    team_features = team_features.fillna(league_medians)
    
    global_medians = team_features.median()
    team_features = team_features.fillna(global_medians)
    
    for col in team_features.columns:
        team_features[col] = winsorize_series(team_features[col], lower_percentile=1, upper_percentile=99)
    
    feature_info = {}
    
    home_info = {
        'home_win_rate': {'description': '主队胜率', 'source': '历史比赛', 'calculation': '累积胜利场数/总场数'},
        'home_draw_rate': {'description': '主队平局率', 'source': '历史比赛', 'calculation': '累积平局场数/总场数'},
        'home_loss_rate': {'description': '主队负率', 'source': '历史比赛', 'calculation': '累积负场数/总场数'},
        'home_avg_goals': {'description': '主队场均进球', 'source': '历史比赛', 'calculation': '累积进球/总场数'},
        'home_avg_opp_goals': {'description': '主队场均失球', 'source': '历史比赛', 'calculation': '累积失球/总场数'},
        'home_avg_xg': {'description': '主队场均预期进球', 'source': '历史比赛', 'calculation': '累积xg/总场数'},
        'home_goals_std': {'description': '主队进球标准差', 'source': '历史比赛', 'calculation': '进球序列标准差'},
        'home_recent_form_3': {'description': '主队近3场胜率', 'source': '历史比赛', 'calculation': '滚动窗口3场胜率'},
        'home_recent_form_5': {'description': '主队近5场胜率', 'source': '历史比赛', 'calculation': '滚动窗口5场胜率'},
        'home_recent_form_10': {'description': '主队近10场胜率', 'source': '历史比赛', 'calculation': '滚动窗口10场胜率'},
        'home_recent_form_15': {'description': '主队近15场胜率', 'source': '历史比赛', 'calculation': '滚动窗口15场胜率'},
        'home_form_trend': {'description': '主队近期状态趋势', 'source': '历史比赛', 'calculation': '近期胜率 - 前期胜率'},
        'home_form_trend_5': {'description': '主队短期状态趋势', 'source': '历史比赛', 'calculation': '近5场胜率 - 前5场胜率'},
        'home_consecutive_wins': {'description': '主队连胜场次', 'source': '历史比赛', 'calculation': '当前连胜计数'},
        'home_consecutive_losses': {'description': '主队连败场次', 'source': '历史比赛', 'calculation': '当前连败计数'},
        'home_consecutive_undefeated': {'description': '主队不败场次', 'source': '历史比赛', 'calculation': '当前不败计数'},
        'home_games_played': {'description': '主队已比赛场数', 'source': '历史比赛', 'calculation': '累计比赛场次'},
        'home_weighted_win_rate': {'description': '主队加权胜率(时间衰减)', 'source': '历史比赛', 'calculation': '指数衰减加权胜率'},
        'home_weighted_avg_goals': {'description': '主队加权场均进球', 'source': '历史比赛', 'calculation': '指数衰减加权进球'},
        'home_weighted_avg_xg': {'description': '主队加权场均xg', 'source': '历史比赛', 'calculation': '指数衰减加权xg'},
        'home_home_win_rate': {'description': '主队主场胜率', 'source': '主场比赛', 'calculation': '主场胜利场数/主场总场数'},
        'home_away_win_rate': {'description': '主队客场胜率', 'source': '客场比赛', 'calculation': '客场胜利场数/客场总场数'},
        'home_home_goals': {'description': '主队主场场均进球', 'source': '主场比赛', 'calculation': '主场进球/主场总场数'},
        'home_away_goals': {'description': '主队客场场均进球', 'source': '客场比赛', 'calculation': '客场进球/客场总场数'},
        'home_home_advantage': {'description': '主队主场优势', 'source': '主客场对比', 'calculation': '主场胜率 - 客场胜率'},
        'home_attack_strength': {'description': '主队进攻强度', 'source': '历史比赛', 'calculation': '场均进球/联赛平均主场进球'},
        'home_defence_strength': {'description': '主队防守强度', 'source': '历史比赛', 'calculation': '场均失球/联赛平均客场进球'},
        'home_xg_efficiency': {'description': '主队xg转化效率', 'source': '历史比赛', 'calculation': '场均进球/场均xg'}
    }
    
    away_info = {k.replace('home_', 'away_'): {'description': v['description'].replace('主队', '客队'), 
                                                 'source': v['source'], 'calculation': v['calculation']} 
                 for k, v in home_info.items()}
    
    h2h_info = {
        'h2h_matches': {'description': '历史交锋次数', 'source': '历史比赛', 'calculation': '两队历史交锋记录数'},
        'h2h_home_win_rate': {'description': '历史交锋主队胜率', 'source': '历史比赛', 'calculation': '主队获胜场次/总交锋'},
        'h2h_away_win_rate': {'description': '历史交锋客队胜率', 'source': '历史比赛', 'calculation': '客队获胜场次/总交锋'},
        'h2h_draw_rate': {'description': '历史交锋平局率', 'source': '历史比赛', 'calculation': '平局场次/总交锋'},
        'h2h_avg_goals_home': {'description': '历史交锋主队场均进球', 'source': '历史比赛', 'calculation': '主队进球/总交锋'},
        'h2h_avg_goals_away': {'description': '历史交锋客队场均进球', 'source': '历史比赛', 'calculation': '客队进球/总交锋'},
        'h2h_avg_total_goals': {'description': '历史交锋总进球', 'source': '历史比赛', 'calculation': '总进球/总交锋'},
        'h2h_goal_diff_avg': {'description': '历史交锋进球差值', 'source': '历史比赛', 'calculation': '进球差平均值'},
        'h2h_last_result': {'description': '最近交锋结果', 'source': '历史比赛', 'calculation': '最近一场结果编码'},
        'h2h_home_streak': {'description': '主队交锋连胜', 'source': '历史比赛', 'calculation': '主队近期连胜'},
        'h2h_away_streak': {'description': '客队交锋连胜', 'source': '历史比赛', 'calculation': '客队近期连胜'},
        'h2h_goals_std': {'description': '交锋进球标准差', 'source': '历史比赛', 'calculation': '进球差标准差'},
        'h2h_home_xg_avg': {'description': '交锋主队平均xg', 'source': '历史比赛', 'calculation': '主队xg/总交锋'},
        'h2h_away_xg_avg': {'description': '交锋客队平均xg', 'source': '历史比赛', 'calculation': '客队xg/总交锋'},
        'h2h_home_big_chances_avg': {'description': '交锋主队平均大机会', 'source': '历史比赛', 'calculation': '主队大机会/总交锋'},
        'h2h_away_big_chances_avg': {'description': '交锋客队平均大机会', 'source': '历史比赛', 'calculation': '客队大机会/总交锋'}
    }
    
    diff_info = {
        'form_diff': {'description': '胜率差值', 'source': 'home_win_rate, away_win_rate', 'calculation': 'home_win_rate - away_win_rate'},
        'goals_diff': {'description': '场均进球差值', 'source': 'home_avg_goals, away_avg_goals', 'calculation': 'home_avg_goals - away_avg_goals'},
        'defence_diff': {'description': '防守差值', 'source': 'home_avg_opp_goals, away_avg_opp_goals', 'calculation': 'away_avg_opp_goals - home_avg_opp_goals'},
        'xg_diff': {'description': '平均xg差值', 'source': 'home_avg_xg, away_avg_xg', 'calculation': 'home_avg_xg - away_avg_xg'},
        'recent_form_diff_3': {'description': '近3场胜率差值', 'source': 'home_recent_form_3, away_recent_form_3', 'calculation': 'home_recent_form_3 - away_recent_form_3'},
        'recent_form_diff_5': {'description': '近5场胜率差值', 'source': 'home_recent_form_5, away_recent_form_5', 'calculation': 'home_recent_form_5 - away_recent_form_5'},
        'recent_form_diff_10': {'description': '近10场胜率差值', 'source': 'home_recent_form_10, away_recent_form_10', 'calculation': 'home_recent_form_10 - away_recent_form_10'},
        'recent_form_diff_15': {'description': '近15场胜率差值', 'source': 'home_recent_form_15, away_recent_form_15', 'calculation': 'home_recent_form_15 - away_recent_form_15'},
        'form_trend_diff': {'description': '状态趋势差值', 'source': 'home_form_trend, away_form_trend', 'calculation': 'home_form_trend - away_form_trend'},
        'form_trend_diff_5': {'description': '短期趋势差值', 'source': 'home_form_trend_5, away_form_trend_5', 'calculation': 'home_form_trend_5 - away_form_trend_5'},
        'streak_diff': {'description': '连胜差值', 'source': 'consecutive_wins, consecutive_losses', 'calculation': '(home_win_streak - home_loss_streak) - (away_win_streak - away_loss_streak)'},
        'undefeated_diff': {'description': '不败场次差值', 'source': 'consecutive_undefeated', 'calculation': 'home_undefeated - away_undefeated'},
        'weighted_form_diff': {'description': '加权胜率差值', 'source': 'weighted_win_rate', 'calculation': 'home_weighted_win_rate - away_weighted_win_rate'},
        'weighted_goals_diff': {'description': '加权进球差值', 'source': 'weighted_avg_goals', 'calculation': 'home_weighted_avg_goals - away_weighted_avg_goals'},
        'weighted_xg_diff': {'description': '加权xg差值', 'source': 'weighted_avg_xg', 'calculation': 'home_weighted_avg_xg - away_weighted_avg_xg'},
        'venue_diff': {'description': '主场优势差值', 'source': 'home_advantage', 'calculation': 'home_home_advantage - away_home_advantage'},
        'goals_stability_diff': {'description': '进球稳定性差值', 'source': 'goals_std', 'calculation': 'away_goals_std - home_goals_std'},
        'attack_strength_diff': {'description': '进攻强度差值', 'source': 'attack_strength', 'calculation': 'home_attack_strength - away_attack_strength'},
        'defence_strength_diff': {'description': '防守强度差值', 'source': 'defence_strength', 'calculation': 'home_defence_strength - away_defence_strength'},
        'xg_efficiency_diff': {'description': 'xg效率差值', 'source': 'xg_efficiency', 'calculation': 'home_xg_efficiency - away_xg_efficiency'},
        'h2h_form_diff': {'description': '交锋胜率差值', 'source': 'h2h_win_rates', 'calculation': 'h2h_home_win_rate - h2h_away_win_rate'},
        'h2h_goals_diff': {'description': '交锋进球差值', 'source': 'h2h_avg_goals', 'calculation': 'h2h_avg_goals_home - h2h_avg_goals_away'},
        'h2h_xg_diff': {'description': '交锋xg差值', 'source': 'h2h_xg_avg', 'calculation': 'h2h_home_xg_avg - h2h_away_xg_avg'}
    }
    
    feature_info.update(home_info)
    feature_info.update(away_info)
    feature_info.update(h2h_info)
    feature_info.update(diff_info)
    
    return team_features, feature_info

def build_league_relative_features(df, match_features, team_features):
    features = pd.DataFrame(index=df.index)
    feature_info = {}
    
    if 'home_attack_strength' in team_features.columns and 'away_attack_strength' in team_features.columns:
        features['attack_strength_ratio'] = team_features['home_attack_strength'] / (team_features['away_attack_strength'] + 1e-8)
        feature_info['attack_strength_ratio'] = {'description': '攻防强度比率', 'source': 'attack_strength', 'calculation': 'home_attack_strength / away_attack_strength'}
    
    if 'home_defence_strength' in team_features.columns and 'away_defence_strength' in team_features.columns:
        features['defence_strength_ratio'] = team_features['home_defence_strength'] / (team_features['away_defence_strength'] + 1e-8)
        feature_info['defence_strength_ratio'] = {'description': '防守强度比率', 'source': 'defence_strength', 'calculation': 'home_defence_strength / away_defence_strength'}
    
    if 'home_avg_goals' in team_features.columns and 'away_avg_goals' in team_features.columns:
        features['goals_ratio'] = team_features['home_avg_goals'] / (team_features['away_avg_goals'] + 1e-8)
        feature_info['goals_ratio'] = {'description': '场均进球比率', 'source': 'home_avg_goals, away_avg_goals', 'calculation': 'home_avg_goals / away_avg_goals'}
    
    if 'home_avg_xg' in team_features.columns and 'away_avg_xg' in team_features.columns:
        features['xg_ratio'] = team_features['home_avg_xg'] / (team_features['away_avg_xg'] + 1e-8)
        feature_info['xg_ratio'] = {'description': '场均xg比率', 'source': 'home_avg_xg, away_avg_xg', 'calculation': 'home_avg_xg / away_avg_xg'}
    
    if 'home_recent_form_5' in team_features.columns and 'away_recent_form_5' in team_features.columns:
        features['recent_form_ratio'] = team_features['home_recent_form_5'] / (team_features['away_recent_form_5'] + 1e-8)
        feature_info['recent_form_ratio'] = {'description': '近5场胜率比率', 'source': 'recent_form_5', 'calculation': 'home_recent_form_5 / away_recent_form_5'}
    
    if 'home_weighted_win_rate' in team_features.columns and 'away_weighted_win_rate' in team_features.columns:
        features['weighted_form_ratio'] = team_features['home_weighted_win_rate'] / (team_features['away_weighted_win_rate'] + 1e-8)
        feature_info['weighted_form_ratio'] = {'description': '加权胜率比率', 'source': 'weighted_win_rate', 'calculation': 'home_weighted_win_rate / away_weighted_win_rate'}
    
    features['market_efficiency_baseline'] = df['competition_name'].map(
        lambda l: get_league_constants(l).get('market_efficiency_baseline', DEFAULT_CONSTANTS['market_efficiency_baseline'])
    )
    feature_info['market_efficiency_baseline'] = {'description': '联赛市场效率基线', 'source': 'competition_name', 'calculation': '从配置加载'}
    
    features['home_advantage_factor'] = df['competition_name'].map(
        lambda l: get_league_constants(l).get('home_advantage_factor', DEFAULT_CONSTANTS['home_advantage_factor'])
    )
    feature_info['home_advantage_factor'] = {'description': '联赛主场优势因子', 'source': 'competition_name', 'calculation': '从配置加载'}
    
    features['avg_draw_rate'] = df['competition_name'].map(
        lambda l: get_league_constants(l).get('avg_draw_rate', DEFAULT_CONSTANTS['avg_draw_rate'])
    )
    feature_info['avg_draw_rate'] = {'description': '联赛平局率', 'source': 'competition_name', 'calculation': '从配置加载'}
    
    return features, feature_info

def build_all_features(df):
    temporal_features, temporal_info = build_temporal_features(df)
    league_features, league_info = build_league_features(df)
    match_features, match_info = build_match_features(df)
    team_features, team_info = build_team_features(df)
    
    league_relative_features, league_relative_info = build_league_relative_features(df, match_features, team_features)
    
    static_odds_features, static_odds_info = build_odds_features(df)
    
    temporal_odds_features, temporal_odds_info = build_odds_temporal_features_from_df(df)
    
    all_features = pd.concat([temporal_features, league_features, match_features, team_features, 
                              league_relative_features, static_odds_features, temporal_odds_features], axis=1)
    all_features = all_features.loc[:, ~all_features.columns.duplicated()]
    
    for col in all_features.select_dtypes(include=[np.number]).columns:
        if col.startswith('league_') and '_' in col and not col.endswith('_mean') and not col.endswith('_std'):
            continue
        if col.startswith('is_'):
            continue
        all_features[col] = winsorize_series(all_features[col], lower_percentile=1, upper_percentile=99)
    
    all_features = all_features.fillna(0)
    
    for col in all_features.columns:
        if all_features[col].dtype == object:
            all_features = all_features.drop(col, axis=1)
            if col in static_odds_info:
                del static_odds_info[col]
            if col in temporal_odds_info:
                del temporal_odds_info[col]
    
    all_feature_info = {}
    all_feature_info.update(temporal_info)
    all_feature_info.update(league_info)
    all_feature_info.update(match_info)
    all_feature_info.update(team_info)
    all_feature_info.update(league_relative_info)
    all_feature_info.update(static_odds_info)
    all_feature_info.update(temporal_odds_info)
    
    return all_features, df['result'], all_feature_info

def analyze_features(X, feature_info):
    analysis_results = {}
    
    for col in X.columns:
        series = X[col]
        missing_ratio = series.isnull().sum() / len(X)
        
        if series.dtype in ['int64', 'float64']:
            analysis_results[col] = {
                'missing_ratio': float(missing_ratio),
                'mean': float(series.mean()),
                'std': float(series.std()),
                'min': float(series.min()),
                'max': float(series.max()),
                'median': float(series.median()),
                'skew': float(skew(series.dropna())),
                'kurtosis': float(kurtosis(series.dropna())),
                'p1': float(np.percentile(series.dropna(), 1)),
                'p5': float(np.percentile(series.dropna(), 5)),
                'p25': float(np.percentile(series.dropna(), 25)),
                'p75': float(np.percentile(series.dropna(), 75)),
                'p95': float(np.percentile(series.dropna(), 95)),
                'p99': float(np.percentile(series.dropna(), 99)),
                'description': feature_info.get(col, {}).get('description', ''),
                'source': feature_info.get(col, {}).get('source', ''),
                'calculation': feature_info.get(col, {}).get('calculation', '')
            }
        else:
            analysis_results[col] = {
                'missing_ratio': float(missing_ratio),
                'unique_values': int(series.nunique()),
                'value_counts': series.value_counts().to_dict(),
                'description': feature_info.get(col, {}).get('description', ''),
                'source': feature_info.get(col, {}).get('source', ''),
                'calculation': feature_info.get(col, {}).get('calculation', '')
            }
    
    return analysis_results

def detect_outliers(X, method='iqr', threshold=1.5):
    outliers = pd.DataFrame(index=X.index)
    
    for col in X.select_dtypes(include=[np.number]).columns:
        series = X[col]
        if method == 'iqr':
            q1 = series.quantile(0.25)
            q3 = series.quantile(0.75)
            iqr = q3 - q1
            lower_bound = q1 - threshold * iqr
            upper_bound = q3 + threshold * iqr
            outliers[col] = (series < lower_bound) | (series > upper_bound)
        elif method == 'zscore':
            z_scores = np.abs((series - series.mean()) / (series.std() + 1e-8))
            outliers[col] = z_scores > threshold
    
    return outliers

def feature_selection_mutual_info(X, y, top_n=50, threshold=None):
    mi_scores = mutual_info_classif(X, y, random_state=42)
    
    mi_df = pd.DataFrame({
        'feature': X.columns,
        'mutual_info': mi_scores
    }).sort_values('mutual_info', ascending=False)
    
    if threshold is not None:
        selected = mi_df[mi_df['mutual_info'] >= threshold]
    else:
        selected = mi_df.head(top_n)
    
    return mi_df, selected['feature'].tolist()

def validate_feature_subset(X, y, selected_features, all_features):
    X_selected = X[selected_features]
    X_train_all, X_test_all, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    X_train_selected, X_test_selected = X_train_all[selected_features], X_test_all[selected_features]
    
    scaler_all = StandardScaler()
    X_train_all_scaled = scaler_all.fit_transform(X_train_all)
    X_test_all_scaled = scaler_all.transform(X_test_all)
    
    scaler_selected = StandardScaler()
    X_train_selected_scaled = scaler_selected.fit_transform(X_train_selected)
    X_test_selected_scaled = scaler_selected.transform(X_test_selected)
    
    rf_all = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
    rf_all.fit(X_train_all_scaled, y_train)
    y_pred_all = rf_all.predict(X_test_all_scaled)
    y_proba_all = rf_all.predict_proba(X_test_all_scaled)
    
    rf_selected = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
    rf_selected.fit(X_train_selected_scaled, y_train)
    y_pred_selected = rf_selected.predict(X_test_selected_scaled)
    y_proba_selected = rf_selected.predict_proba(X_test_selected_scaled)
    
    results = {
        'all_features': {
            'accuracy': accuracy_score(y_test, y_pred_all),
            'log_loss': log_loss(y_test, y_proba_all),
            'f1_macro': f1_score(y_test, y_pred_all, average='macro'),
            'feature_count': len(all_features)
        },
        'selected_features': {
            'accuracy': accuracy_score(y_test, y_pred_selected),
            'log_loss': log_loss(y_test, y_proba_selected),
            'f1_macro': f1_score(y_test, y_pred_selected, average='macro'),
            'feature_count': len(selected_features)
        }
    }
    
    return results

def save_results(X, y, feature_info, analysis_results, mi_df, selected_features, validation_results):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    X.to_csv(os.path.join(OUTPUT_DIR, f'features_{timestamp}.csv'), index=False)
    y.to_csv(os.path.join(OUTPUT_DIR, f'labels_{timestamp}.csv'), index=False)
    
    with open(os.path.join(OUTPUT_DIR, f'feature_info_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump(feature_info, f, ensure_ascii=False, indent=2)
    
    with open(os.path.join(OUTPUT_DIR, f'feature_analysis_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump(analysis_results, f, ensure_ascii=False, indent=2)
    
    mi_df.to_csv(os.path.join(OUTPUT_DIR, f'mutual_info_ranking_{timestamp}.csv'), index=False)
    
    with open(os.path.join(OUTPUT_DIR, f'selected_features_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump({
            'selected_features': selected_features,
            'feature_count': len(selected_features),
            'selection_method': 'mutual_info',
            'timestamp': timestamp
        }, f, ensure_ascii=False, indent=2)
    
    with open(os.path.join(OUTPUT_DIR, f'validation_results_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump(validation_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n所有结果已保存到 {OUTPUT_DIR}")
    print(f"时间戳: {timestamp}")
    
    return timestamp

def main():
    print("=" * 80)
    print("特征工程管道 - 第二阶段")
    print("=" * 80)
    
    print("\n1. 加载比赛数据...")
    df = load_match_data()
    print(f"   加载记录数: {len(df)}")
    print(f"   日期范围: {df['date'].min().strftime('%Y-%m-%d')} 至 {df['date'].max().strftime('%Y-%m-%d')}")
    print(f"   联赛数量: {df['competition_name'].nunique()}")
    print(f"   球队数量: {pd.concat([df['home_team_name'], df['away_team_name']]).nunique()}")
    
    print("\n2. 构建所有特征...")
    X, y, feature_info = build_all_features(df)
    print(f"   生成特征维度: {X.shape[1]}")
    print(f"   样本数量: {X.shape[0]}")
    
    print("\n3. 特征基础统计分析...")
    analysis_results = analyze_features(X, feature_info)
    
    print("\n   缺失值统计:")
    missing_stats = {k: v['missing_ratio'] for k, v in analysis_results.items() if v['missing_ratio'] > 0}
    if len(missing_stats) == 0:
        print("     无缺失值")
    else:
        for feature, ratio in sorted(missing_stats.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"     {feature}: {ratio*100:.2f}%")
    
    print("\n   特征分布摘要:")
    numeric_features = [k for k, v in analysis_results.items() if 'mean' in v]
    print(f"     数值特征: {len(numeric_features)}")
    print(f"     类别特征: {len(X.columns) - len(numeric_features)}")
    
    print("\n4. 异常值检测...")
    outliers = detect_outliers(X)
    outlier_ratio = outliers.any(axis=1).mean()
    print(f"   包含异常值的样本比例: {outlier_ratio*100:.2f}%")
    
    print("\n5. 基于互信息的特征选择...")
    mi_df, selected_features = feature_selection_mutual_info(X, y, top_n=50)
    print(f"   选择特征数量: {len(selected_features)}")
    
    print("\n   特征重要性排名(前20):")
    for i, (_, row) in enumerate(mi_df.head(20).iterrows()):
        print(f"   {i+1:2d}. {row['feature']:40s} MI: {row['mutual_info']:.4f}")
    
    print("\n6. 验证特征子集有效性...")
    validation_results = validate_feature_subset(X, y, selected_features, X.columns.tolist())
    print(f"\n   全部特征 ({validation_results['all_features']['feature_count']}个):")
    print(f"     准确率: {validation_results['all_features']['accuracy']:.4f}")
    print(f"     LogLoss: {validation_results['all_features']['log_loss']:.4f}")
    print(f"     F1(macro): {validation_results['all_features']['f1_macro']:.4f}")
    print(f"\n   选择特征 ({validation_results['selected_features']['feature_count']}个):")
    print(f"     准确率: {validation_results['selected_features']['accuracy']:.4f}")
    print(f"     LogLoss: {validation_results['selected_features']['log_loss']:.4f}")
    print(f"     F1(macro): {validation_results['selected_features']['f1_macro']:.4f}")
    
    print("\n7. 保存结果...")
    timestamp = save_results(X, y, feature_info, analysis_results, mi_df, selected_features, validation_results)
    
    print("\n" + "=" * 80)
    print("特征工程管道完成")
    print("=" * 80)
    
    return {
        'feature_count': X.shape[1],
        'selected_feature_count': len(selected_features),
        'top_features': mi_df.head(10)['feature'].tolist(),
        'validation_results': validation_results,
        'timestamp': timestamp
    }

if __name__ == '__main__':
    results = main()