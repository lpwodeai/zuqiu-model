import pandas as pd
import numpy as np
import json
import os
import sqlite3

try:
    import yaml
except ImportError:
    yaml = None
    print("Warning: pyyaml not installed, will use default parameters")

# 队名归一化（用于 five_leagues.db ↔ odds.db 中文队名匹配）
try:
    from team_name_mapping import normalize_team_name
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
    try:
        from team_name_mapping import normalize_team_name
    except ImportError:
        def normalize_team_name(name, fuzzy_threshold=0.8):
            return name

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    print("Warning: xgboost not installed, will skip XGBoost training")

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    print("Warning: lightgbm not installed, will skip LightGBM training")

from sklearn.model_selection import train_test_split, TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss, confusion_matrix, classification_report
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.base import BaseEstimator, ClassifierMixin
from datetime import datetime, timedelta
from pathlib import Path

CONFIG = {}
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')

def load_config():
    global CONFIG
    if yaml is not None and os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                CONFIG = yaml.safe_load(f)
            print(f"✅ 配置文件已加载: {CONFIG_PATH}")
        except Exception as e:
            print(f"⚠️ 配置文件加载失败，使用默认参数: {e}")
    else:
        print("⚠️ 配置文件不存在或pyyaml未安装，使用默认参数")

load_config()

class XGBClassifierWrapper(BaseEstimator, ClassifierMixin):
    def __init__(self, params=None, num_boost_round=300, early_stopping_rounds=30):
        self.params = params if params else {}
        self.num_boost_round = num_boost_round
        self.early_stopping_rounds = early_stopping_rounds
        self.model = None
        self.feature_names = None
        self.classes_ = np.array([0, 1, 2])
    
    def fit(self, X, y):
        self.classes_ = np.unique(y)
        if isinstance(X, pd.DataFrame):
            self.feature_names = X.columns.tolist()
            X = X.values
        else:
            self.feature_names = [f'feature_{i}' for i in range(X.shape[1])]
        
        class_counts = np.bincount(y)
        class_weights = len(y) / (len(class_counts) * class_counts)
        sample_weights = np.array([class_weights[label] for label in y])
        
        dtrain = xgb.DMatrix(X, label=y, weight=sample_weights, feature_names=self.feature_names)
        
        watchlist = [(dtrain, 'train')]
        self.model = xgb.train(self.params, dtrain, num_boost_round=self.num_boost_round,
                              evals=watchlist, early_stopping_rounds=self.early_stopping_rounds,
                              verbose_eval=False)
        return self
    
    def predict_proba(self, X):
        if isinstance(X, pd.DataFrame):
            X = X.values
        dtest = xgb.DMatrix(X, feature_names=self.feature_names)
        return self.model.predict(dtest)
    
    def predict(self, X):
        proba = self.predict_proba(X)
        return np.argmax(proba, axis=1)

class LGBClassifierWrapper(BaseEstimator, ClassifierMixin):
    def __init__(self, params=None, num_boost_round=300, early_stopping_rounds=30):
        self.params = params if params else {}
        self.num_boost_round = num_boost_round
        self.early_stopping_rounds = early_stopping_rounds
        self.model = None
        self.feature_names = None
        self.classes_ = np.array([0, 1, 2])
    
    def fit(self, X, y):
        self.classes_ = np.unique(y)
        if isinstance(X, pd.DataFrame):
            self.feature_names = X.columns.tolist()
            X = X.values
        else:
            self.feature_names = [f'feature_{i}' for i in range(X.shape[1])]
        
        class_counts = np.bincount(y)
        class_weights = len(y) / (len(class_counts) * class_counts)
        sample_weights = np.array([class_weights[label] for label in y])
        
        lgb_train = lgb.Dataset(X, y, weight=sample_weights, feature_name=self.feature_names)
        
        callbacks = [lgb.early_stopping(stopping_rounds=self.early_stopping_rounds), 
                     lgb.log_evaluation(period=0)]
        
        self.model = lgb.train(self.params, lgb_train, num_boost_round=self.num_boost_round,
                               valid_sets=[lgb_train], callbacks=callbacks)
        return self
    
    def predict_proba(self, X):
        if isinstance(X, pd.DataFrame):
            X = X.values
        return self.model.predict(X)
    
    def predict(self, X):
        proba = self.predict_proba(X)
        return np.argmax(proba, axis=1)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR
OUTPUT_DIR = BASE_DIR / "assets"

# 数据库路径（SSOT：five_leagues.db 统计 + odds.db 赔率）
FIVE_LEAGUES_DB = BASE_DIR / "data" / "five_leagues.db"
ODDS_DB = BASE_DIR / "data" / "odds.db"


def load_match_data_from_db(db_path=None):
    """从 five_leagues.db 加载比赛数据（SSOT，替代原 CSV 路径）。

    返回与原 load_csv_data 兼容的 DataFrame：date, home_team_name, away_team_name,
    homeGoals, awayGoals, 各项统计列, competition_name（中文队名 + 英文联赛名）。
    赔率相关列（bet365_*/Pinnacle_*/最高_*/平均_*）由 load_close_odds_from_db merge 填充。
    """
    db_path = str(db_path) if db_path else str(FIVE_LEAGUES_DB)
    conn = sqlite3.connect(db_path)
    try:
        query = """
        SELECT
            m.date,
            ht.name as home_team_name,
            at.name as away_team_name,
            m.homeGoals,
            m.awayGoals,
            c.name as competition_name,
            m.homeShots,
            m.homeShotsOnTarget,
            m.awayShots,
            m.awayShotsOnTarget,
            m.homeCorners,
            m.awayCorners,
            m.homeFouls,
            m.awayFouls,
            m.homeYellowCards,
            m.awayYellowCards,
            m.homePossession
        FROM matches m
        LEFT JOIN teams ht ON m.homeTeamId = ht.id
        LEFT JOIN teams at ON m.awayTeamId = at.id
        LEFT JOIN competitions c ON m.competitionId = c.id
        WHERE m.homeGoals IS NOT NULL AND m.awayGoals IS NOT NULL
        ORDER BY m.date
        """
        df = pd.read_sql(query, conn)
    finally:
        conn.close()

    df['date'] = pd.to_datetime(df['date'], errors='coerce')

    # 初始化赔率列（与原 CSV 兼容），由 load_close_odds_from_db 填充
    odds_cols = [
        'bet365_主胜', 'bet365_平局', 'bet365_客胜',
        'Pinnacle_主胜', 'Pinnacle_平局', 'Pinnacle_客胜',
        '最高_主胜', '最高_平局', '最高_客胜',
        '平均_主胜', '平均_平局', '平均_客胜',
        'bet365_大2.5', 'bet365_小2.5', 'Pinnacle_大2.5', 'Pinnacle_小2.5',
        'bet365_亚盘主', 'bet365_亚盘客', 'Pinnacle_亚盘主', 'Pinnacle_亚盘客',
        '亚盘盘口',
    ]
    for col in odds_cols:
        df[col] = np.nan

    print(f"Loaded {len(df)} matches from five_leagues.db")
    return df


def load_close_odds_from_db(df, db_path=None):
    """从 odds.db 加载每场 close 赔率，merge 到 df。

    匹配策略（3 级）：
      1. 精确：match_id = f"{date}_{home_cn}_{away_cn}" 直接命中
      2. 归一化：两边队名经 normalize_team_name 归一后再精确查
      3. LIKE 模糊：match_id LIKE '{date}_%{home}%' AND match_id LIKE '%{away}'

    odds.db 无博彩商区分，统一用 close.win/draw/lose 填充所有赔率列
    （bet365_主胜 = Pinnacle_主胜 = 最高_主胜 = 平均_主胜 = close.win 等）。
    handicap 列从 handicap_history close 加载；亚盘赔率用 hcp_win/hcp_draw/hcp_lose。
    匹配不到的场次保持 NaN（由 build_odds_features 的 fillna 兜底）。

    Returns:
        df（原地填充赔率列），hit_rate（float 0-1）
    """
    db_path = str(db_path) if db_path else str(ODDS_DB)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()

        # 1. 预构建 wdl_history / handicap_history close 查找表
        #    key: match_id → (win, draw, lose, ts)  取 timestamp 最大的一条作为 close
        cur.execute("SELECT match_id, win_a, draw, win_b, timestamp FROM wdl_history")
        wdl_last = {}
        for mid, win_a, draw, win_b, ts in cur.fetchall():
            if win_a is None or draw is None or win_b is None:
                continue
            prev = wdl_last.get(mid)
            if prev is None or (ts is not None and prev[3] is not None and ts > prev[3]):
                wdl_last[mid] = (win_a, draw, win_b, ts)

        cur.execute("SELECT match_id, hcp_win, hcp_draw, hcp_lose, timestamp FROM handicap_history")
        hcp_last = {}
        for mid, hcp_win, hcp_draw, hcp_lose, ts in cur.fetchall():
            if hcp_win is None or hcp_lose is None:
                continue
            prev = hcp_last.get(mid)
            if prev is None or (ts is not None and prev[3] is not None and ts > prev[3]):
                hcp_last[mid] = (hcp_win, hcp_draw, hcp_lose, ts)
    finally:
        # LIKE 模糊查询阶段还要用 conn，先不关；循环结束后在下方 finally 中关闭。
        pass

    hit = 0
    miss = 0
    total = len(df)
    miss_samples = []

    try:
        for idx, row in df.iterrows():
            date_val = row['date']
            if pd.isna(date_val):
                miss += 1
                continue
            date_prefix = str(date_val)[:10]
            home = row['home_team_name']
            away = row['away_team_name']
            if not isinstance(home, str) or not isinstance(away, str):
                miss += 1
                continue

            # 级别 1：精确
            mid = f"{date_prefix}_{home}_{away}"
            wdl = wdl_last.get(mid)
            hcp = hcp_last.get(mid)
            match_strategy = 'exact' if wdl else None

            # 级别 2：归一化两边
            if not wdl:
                home_n = normalize_team_name(home) or home
                away_n = normalize_team_name(away) or away
                if home_n != home or away_n != away:
                    mid_n = f"{date_prefix}_{home_n}_{away_n}"
                    wdl = wdl_last.get(mid_n)
                    hcp = hcp_last.get(mid_n)
                    if wdl:
                        match_strategy = 'normalized'
                        mid = mid_n

            # 级别 3：LIKE 模糊（match_id LIKE '{date}_%{home}%' AND match_id LIKE '%_{away}'）
            if not wdl:
                # 日期容差 ±2 天（应对 five_leagues.db 与 odds.db 时区/数据源日期差异）
                try:
                    d0 = datetime.strptime(date_prefix, '%Y-%m-%d')
                    date_candidates = [date_prefix] + [
                        (d0 + timedelta(days=off)).strftime('%Y-%m-%d')
                        for off in [-2, -1, 1, 2]
                    ]
                except (ValueError, TypeError):
                    date_candidates = [date_prefix]
                name_candidates = [(home, away), (normalize_team_name(home) or home, normalize_team_name(away) or away)]
                matched = False
                for dc in date_candidates:
                    for h, a in name_candidates:
                        if h is None or a is None:
                            continue
                        cur.execute(
                            "SELECT match_id FROM wdl_history "
                            "WHERE match_id LIKE ? AND match_id LIKE ? "
                            "ORDER BY match_id DESC LIMIT 1",
                            (f"{dc}_%{h}%", f"%_{a}"),
                        )
                        r = cur.fetchone()
                        if r:
                            mid = r[0]
                            wdl = wdl_last.get(mid)
                            hcp = hcp_last.get(mid)
                            match_strategy = 'like' if dc == date_prefix else 'like+datediff'
                            matched = True
                            break
                    if matched:
                        break

            if wdl:
                hit += 1
                win, draw, lose, _ = wdl
                # 胜平负单值列：所有博彩商列统一填 close 值
                df.at[idx, 'bet365_主胜'] = win
                df.at[idx, 'bet365_平局'] = draw
                df.at[idx, 'bet365_客胜'] = lose
                df.at[idx, 'Pinnacle_主胜'] = win
                df.at[idx, 'Pinnacle_平局'] = draw
                df.at[idx, 'Pinnacle_客胜'] = lose
                df.at[idx, '最高_主胜'] = win
                df.at[idx, '最高_平局'] = draw
                df.at[idx, '最高_客胜'] = lose
                df.at[idx, '平均_主胜'] = win
                df.at[idx, '平均_平局'] = draw
                df.at[idx, '平均_客胜'] = lose

                if hcp:
                    hwin, hdraw, hlose, _ = hcp
                    df.at[idx, 'bet365_亚盘主'] = hwin
                    df.at[idx, 'bet365_亚盘客'] = hlose
                    df.at[idx, 'Pinnacle_亚盘主'] = hwin
                    df.at[idx, 'Pinnacle_亚盘客'] = hlose
                    # 亚盘盘口：odds.db matches.handicap 为让球数，按需查询；此处从 hcp 行无法直接取盘口，
                    # 用 matches.handicap 字段（单独查一次）填到 '亚盘盘口'。
                    # 为效率起见，盘口由下方单独批量查一次。

            else:
                miss += 1
                if len(miss_samples) < 10:
                    miss_samples.append((date_prefix, home, away))
    finally:
        conn.close()

    # 单独批量补 '亚盘盘口'：从 odds.db matches.handicap 取（按已 match 到的 match_id）
    # 也尝试用 wdl_history 中的 match_id 反查 matches.handicap
    try:
        conn2 = sqlite3.connect(db_path)
        cur2 = conn2.cursor()
        for idx, row in df.iterrows():
            if not pd.isna(row['亚盘盘口']):
                continue
            date_val = row['date']
            if pd.isna(date_val):
                continue
            date_prefix = str(date_val)[:10]
            home = row['home_team_name']
            away = row['away_team_name']
            if not isinstance(home, str) or not isinstance(away, str):
                continue
            # 尝试多种 match_id 格式查询 matches.handicap（含日期容差 ±2 天）
            found = None
            try:
                d0 = datetime.strptime(date_prefix, '%Y-%m-%d')
                date_candidates = [date_prefix] + [
                    (d0 + timedelta(days=off)).strftime('%Y-%m-%d')
                    for off in [-2, -1, 1, 2]
                ]
            except (ValueError, TypeError):
                date_candidates = [date_prefix]
            name_candidates = [(home, away), (normalize_team_name(home) or home, normalize_team_name(away) or away)]
            # 精确 match_id（含日期容差）
            for dc in date_candidates:
                for h, a in name_candidates:
                    mid = f"{dc}_{h}_{a}"
                    cur2.execute("SELECT handicap FROM matches WHERE match_id = ?", (mid,))
                    r = cur2.fetchone()
                    if r and r[0] is not None:
                        found = r[0]
                        break
                if found is not None:
                    break
            # LIKE 模糊匹配 matches 表（含日期容差）
            if found is None:
                for dc in date_candidates:
                    for h, a in name_candidates:
                        cur2.execute(
                            "SELECT handicap FROM matches WHERE match_id LIKE ? AND match_id LIKE ? LIMIT 1",
                            (f"{dc}_%{h}%", f"%_{a}"),
                        )
                        r = cur2.fetchone()
                        if r and r[0] is not None:
                            found = r[0]
                            break
                    if found is not None:
                        break
            if found is not None:
                df.at[idx, '亚盘盘口'] = found
        conn2.close()
    except Exception as e:
        print(f"   ⚠️ 亚盘盘口加载失败: {e}")

    hit_rate = hit / total if total > 0 else 0.0
    print(f"赔率匹配: {hit}/{total} 命中 ({hit_rate*100:.1f}%), {miss} 场未命中（NaN）")
    if miss_samples:
        print(f"   未命中样本(前10): {miss_samples}")
    return df, hit_rate

def clean_and_transform_data(df):
    df = df.copy()
    
    df['result'] = np.where(df['homeGoals'] > df['awayGoals'], 2,
                           np.where(df['homeGoals'] < df['awayGoals'], 0, 1))
    df['goal_diff'] = df['homeGoals'] - df['awayGoals']
    df['total_goals'] = df['homeGoals'] + df['awayGoals']
    
    stats_cols = ['homeShots', 'awayShots', 'homeShotsOnTarget', 'awayShotsOnTarget',
                  'homeCorners', 'awayCorners', 'homeYellowCards', 'awayYellowCards',
                  'homeFouls', 'awayFouls', 'homePossession']
    
    for col in stats_cols:
        league_medians = df.groupby('competition_name')[col].transform('median')
        df[col] = df[col].fillna(league_medians)
        global_median = df[col].median()
        df[col] = df[col].fillna(global_median)
    
    df['awayPossession'] = 100 - df['homePossession']
    
    return df

def winsorize_series(series, lower_percentile=1, upper_percentile=99):
    clean = series.dropna()
    if len(clean) == 0:
        return series
    lower = np.percentile(clean, lower_percentile)
    upper = np.percentile(clean, upper_percentile)
    return series.clip(lower=lower, upper=upper)

def build_odds_features(df):
    features = pd.DataFrame()
    
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
        else:
            features[col] = np.nan
    
    features['pinnacle_h2a'] = features['Pinnacle_主胜'] / (features['Pinnacle_客胜'] + 0.01)
    features['pinnacle_prob_home'] = 1 / features['Pinnacle_主胜']
    features['pinnacle_prob_draw'] = 1 / features['Pinnacle_平局']
    features['pinnacle_prob_away'] = 1 / features['Pinnacle_客胜']
    features['pinnacle_prob_sum'] = features['pinnacle_prob_home'] + features['pinnacle_prob_draw'] + features['pinnacle_prob_away']
    features['pinnacle_implied_home'] = features['pinnacle_prob_home'] / features['pinnacle_prob_sum']
    features['pinnacle_implied_draw'] = features['pinnacle_prob_draw'] / features['pinnacle_prob_sum']
    features['pinnacle_implied_away'] = features['pinnacle_prob_away'] / features['pinnacle_prob_sum']
    
    features['avg_h2a'] = features['平均_主胜'] / (features['平均_客胜'] + 0.01)
    features['avg_prob_home'] = 1 / features['平均_主胜']
    features['avg_prob_draw'] = 1 / features['平均_平局']
    features['avg_prob_away'] = 1 / features['平均_客胜']
    features['avg_prob_sum'] = features['avg_prob_home'] + features['avg_prob_draw'] + features['avg_prob_away']
    features['avg_implied_home'] = features['avg_prob_home'] / features['avg_prob_sum']
    features['avg_implied_draw'] = features['avg_prob_draw'] / features['avg_prob_sum']
    features['avg_implied_away'] = features['avg_prob_away'] / features['avg_prob_sum']
    
    features['odds_diff_h2a'] = features['平均_主胜'] - features['平均_客胜']
    features['odds_spread'] = features['最高_主胜'] - features['Pinnacle_主胜']
    features['odds_margin'] = features['pinnacle_prob_sum'] - 1
    
    features['pinnacle_margin_flag'] = (features['pinnacle_prob_sum'] > 1.15).astype(int)
    features['avg_margin_flag'] = (features['avg_prob_sum'] > 1.15).astype(int)
    
    over_under_cols = ['bet365_大2.5', 'bet365_小2.5', 'Pinnacle_大2.5', 'Pinnacle_小2.5']
    for col in over_under_cols:
        col_data = get_col(col)
        if col_data is not None:
            col_data = col_data.fillna(col_data.median())
            col_data = winsorize_series(col_data, lower_percentile=1, upper_percentile=99)
            features[col] = col_data
        else:
            features[col] = np.nan
    
    features['pinnacle_over_prob'] = 1 / features['Pinnacle_大2.5']
    features['pinnacle_under_prob'] = 1 / features['Pinnacle_小2.5']
    features['pinnacle_over_under_ratio'] = features['Pinnacle_大2.5'] / (features['Pinnacle_小2.5'] + 0.01)
    
    asian_cols = ['bet365_亚盘主', 'bet365_亚盘客', 'Pinnacle_亚盘主', 'Pinnacle_亚盘客']
    for col in asian_cols:
        col_data = get_col(col)
        if col_data is not None:
            col_data = col_data.fillna(col_data.median())
            col_data = winsorize_series(col_data, lower_percentile=1, upper_percentile=99)
            features[col] = col_data
        else:
            features[col] = np.nan
    
    features['asian_handicap_diff'] = features['Pinnacle_亚盘主'] - features['Pinnacle_亚盘客']
    
    handicap_col = get_col('亚盘盘口')
    if handicap_col is not None:
        features['handicap_value'] = handicap_col.apply(extract_handicap)
    else:
        features['handicap_value'] = 0
    
    features['handicap_value'] = winsorize_series(features['handicap_value'], lower_percentile=1, upper_percentile=99)
    
    return features

def extract_handicap(handicap_str):
    if pd.isna(handicap_str):
        return 0
    if isinstance(handicap_str, str):
        try:
            return float(handicap_str)
        except:
            return 0
    return float(handicap_str)

def build_match_features(df):
    features = pd.DataFrame()
    
    league_dummies = pd.get_dummies(df['competition_name'], prefix='league')
    features = pd.concat([features, league_dummies], axis=1)
    
    return features

def calc_h2h_stats(h2h_hist, home_team, away_team):
    if len(h2h_hist) == 0:
        return {
            'matches': 0,
            'home_win_rate': np.nan,
            'away_win_rate': np.nan,
            'draw_rate': np.nan,
            'avg_goals_home': np.nan,
            'avg_goals_away': np.nan,
            'avg_total_goals': np.nan,
            'home_goals_last_5': np.nan,
            'away_goals_last_5': np.nan,
            'home_streak': 0,
            'away_streak': 0,
            'last_result': np.nan,
            'last_5_form': np.nan,
            'goal_diff_avg': np.nan,
            'goal_diff_std': np.nan
        }
    
    home_wins = 0
    away_wins = 0
    draws = 0
    home_goals_total = 0
    away_goals_total = 0
    
    for _, r in h2h_hist.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        
        home_goals_total += hg
        away_goals_total += ag
        
        if hg > ag:
            home_wins += 1
        elif hg < ag:
            away_wins += 1
        else:
            draws += 1
    
    total = len(h2h_hist)
    home_win_rate = home_wins / total
    away_win_rate = away_wins / total
    draw_rate = draws / total
    
    last_5 = h2h_hist.tail(5)
    home_goals_last_5 = 0
    away_goals_last_5 = 0
    last_5_form = 0
    
    for _, r in last_5.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        home_goals_last_5 += hg
        away_goals_last_5 += ag
        if hg > ag:
            last_5_form += 3
        elif hg == ag:
            last_5_form += 1
    
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
    
    goal_diffs = []
    for _, r in h2h_hist.iterrows():
        if r['home_team_name'] == home_team:
            goal_diffs.append(r['homeGoals'] - r['awayGoals'])
        else:
            goal_diffs.append(r['awayGoals'] - r['homeGoals'])
    
    return {
        'matches': total,
        'home_win_rate': home_win_rate,
        'away_win_rate': away_win_rate,
        'draw_rate': draw_rate,
        'avg_goals_home': home_goals_total / total,
        'avg_goals_away': away_goals_total / total,
        'avg_total_goals': (home_goals_total + away_goals_total) / total,
        'home_goals_last_5': home_goals_last_5 / len(last_5) if len(last_5) > 0 else np.nan,
        'away_goals_last_5': away_goals_last_5 / len(last_5) if len(last_5) > 0 else np.nan,
        'home_streak': home_streak,
        'away_streak': away_streak,
        'last_result': last_result,
        'last_5_form': last_5_form / (len(last_5) * 3) if len(last_5) > 0 else np.nan,
        'goal_diff_avg': np.mean(goal_diffs),
        'goal_diff_std': np.std(goal_diffs)
    }

def build_team_features(df):
    team_features_list = []
    
    for idx, row in df.iterrows():
        home_team = row['home_team_name']
        away_team = row['away_team_name']
        match_date = row['date']
        
        home_home_hist = df[(df['home_team_name'] == home_team) & (df['date'] < match_date)].tail(12)
        home_away_hist = df[(df['away_team_name'] == home_team) & (df['date'] < match_date)].tail(12)
        away_home_hist = df[(df['home_team_name'] == away_team) & (df['date'] < match_date)].tail(12)
        away_away_hist = df[(df['away_team_name'] == away_team) & (df['date'] < match_date)].tail(12)
        
        home_all_hist = pd.concat([home_home_hist, home_away_hist]).sort_values('date').tail(12)
        away_all_hist = pd.concat([away_home_hist, away_away_hist]).sort_values('date').tail(12)
        
        def calc_stats(hist, is_home=True):
            if len(hist) == 0:
                return {
                    'avg_goals': np.nan, 'avg_opp_goals': np.nan,
                    'win_rate': np.nan, 'draw_rate': np.nan, 'loss_rate': np.nan,
                    'recent_form': 0, 'games_played': 0,
                    'home_win_rate': np.nan, 'away_win_rate': np.nan,
                    'ema_goals': np.nan, 'ema_opp_goals': np.nan,
                    'form_trend': 0,
                    'consecutive_wins': 0, 'consecutive_losses': 0
                }
            
            goals_col = 'homeGoals' if is_home else 'awayGoals'
            opp_goals_col = 'awayGoals' if is_home else 'homeGoals'
            
            win_rate = (hist[goals_col] > hist[opp_goals_col]).mean()
            draw_rate = (hist[goals_col] == hist[opp_goals_col]).mean()
            loss_rate = (hist[goals_col] < hist[opp_goals_col]).mean()
            
            target_team = home_team if is_home else away_team
            home_mask = hist['home_team_name'] == target_team
            away_mask = hist['away_team_name'] == target_team
            home_hist = hist[home_mask]
            away_hist = hist[away_mask]
            home_win_rate = (home_hist[goals_col] > home_hist[opp_goals_col]).mean() if len(home_hist) > 0 else np.nan
            away_win_rate = (away_hist[goals_col] > away_hist[opp_goals_col]).mean() if len(away_hist) > 0 else np.nan
            
            recent_3 = hist.tail(3)
            recent_form = ((recent_3[goals_col] > recent_3[opp_goals_col]).sum() * 3 +
                          (recent_3[goals_col] == recent_3[opp_goals_col]).sum() * 1) / len(recent_3)
            
            time_decay_config = CONFIG.get('training', {}).get('time_decay', {})
            half_life_days = time_decay_config.get('half_life_days', 14)
            
            hist_sorted = hist.sort_values('date')
            days_since = (match_date - hist_sorted['date']).dt.days
            weights = np.exp(-days_since * np.log(2) / half_life_days)
            weights = weights / weights.sum()
            
            ema_goals = (hist_sorted[goals_col] * weights).sum()
            ema_opp_goals = (hist_sorted[opp_goals_col] * weights).sum()
            
            if len(hist) >= 6:
                recent_6 = hist_sorted.tail(6)
                old_6 = hist_sorted.head(6) if len(hist) >= 12 else hist_sorted.head(len(hist) // 2)
                recent_win_rate = (recent_6[goals_col] > recent_6[opp_goals_col]).mean()
                old_win_rate = (old_6[goals_col] > old_6[opp_goals_col]).mean()
                form_trend = recent_win_rate - old_win_rate
            else:
                form_trend = 0
            
            hist_sorted_rev = hist_sorted.iloc[::-1]
            consecutive_wins = 0
            for _, r in hist_sorted_rev.iterrows():
                if r[goals_col] > r[opp_goals_col]:
                    consecutive_wins += 1
                else:
                    break
            
            consecutive_losses = 0
            for _, r in hist_sorted_rev.iterrows():
                if r[goals_col] < r[opp_goals_col]:
                    consecutive_losses += 1
                else:
                    break
            
            return {
                'avg_goals': hist[goals_col].mean(),
                'avg_opp_goals': hist[opp_goals_col].mean(),
                'win_rate': win_rate,
                'draw_rate': draw_rate,
                'loss_rate': loss_rate,
                'recent_form': recent_form,
                'games_played': len(hist),
                'home_win_rate': home_win_rate,
                'away_win_rate': away_win_rate,
                'ema_goals': ema_goals,
                'ema_opp_goals': ema_opp_goals,
                'form_trend': form_trend,
                'consecutive_wins': consecutive_wins,
                'consecutive_losses': consecutive_losses
            }
        
        home_stats = calc_stats(home_all_hist, is_home=True)
        away_stats = calc_stats(away_all_hist, is_home=False)
        
        h2h_hist = df[((df['home_team_name'] == home_team) & (df['away_team_name'] == away_team)) |
                      ((df['home_team_name'] == away_team) & (df['away_team_name'] == home_team)) &
                      (df['date'] < match_date)].sort_values('date').tail(10)
        h2h_stats = calc_h2h_stats(h2h_hist, home_team, away_team)
        
        combined = {**{f'home_{k}': v for k, v in home_stats.items()},
                    **{f'away_{k}': v for k, v in away_stats.items()},
                    **{f'h2h_{k}': v for k, v in h2h_stats.items()}}
        
        combined['form_diff'] = home_stats['win_rate'] - away_stats['win_rate']
        combined['goals_diff'] = home_stats['avg_goals'] - away_stats['avg_goals']
        combined['defence_diff'] = away_stats['avg_opp_goals'] - home_stats['avg_opp_goals']
        combined['recent_form_diff'] = home_stats['recent_form'] - away_stats['recent_form']
        combined['home_advantage_diff'] = home_stats['home_win_rate'] - away_stats['away_win_rate']
        combined['ema_goals_diff'] = home_stats['ema_goals'] - away_stats['ema_goals']
        combined['ema_defence_diff'] = away_stats['ema_opp_goals'] - home_stats['ema_opp_goals']
        combined['form_trend_diff'] = home_stats['form_trend'] - away_stats['form_trend']
        combined['streak_diff'] = (home_stats['consecutive_wins'] - home_stats['consecutive_losses']) - \
                                  (away_stats['consecutive_wins'] - away_stats['consecutive_losses'])
        
        team_features_list.append(combined)
    
    team_features = pd.DataFrame(team_features_list, index=df.index)
    team_features['competition_name'] = df['competition_name']
    
    for col in team_features.columns:
        if col == 'competition_name':
            continue
        league_medians = team_features.groupby('competition_name')[col].transform('median')
        team_features[col] = team_features[col].fillna(league_medians)
        global_median = team_features[col].median()
        team_features[col] = team_features[col].fillna(global_median)
        team_features[col] = winsorize_series(team_features[col], lower_percentile=1, upper_percentile=99)
    
    team_features = team_features.drop(columns=['competition_name'])
    
    return team_features

def train_xgboost(X_train, y_train, X_val, y_val, feature_names):
    if xgb is None:
        return None, None
    
    xgb_config = CONFIG.get('model', {}).get('xgboost', {})
    
    params = {
        'objective': 'multi:softprob',
        'num_class': 3,
        'eval_metric': 'mlogloss',
        'max_depth': xgb_config.get('max_depth', 5),
        'learning_rate': xgb_config.get('learning_rate', 0.1),
        'subsample': xgb_config.get('subsample', 0.8),
        'colsample_bytree': xgb_config.get('colsample_bytree', 0.8),
        'gamma': xgb_config.get('gamma', 0.0),
        'min_child_weight': xgb_config.get('min_child_weight', 1),
        'reg_alpha': xgb_config.get('reg_alpha', 0.0),
        'reg_lambda': xgb_config.get('reg_lambda', 1.0),
        'seed': 42,
        'nthread': -1
    }
    
    num_boost_round = xgb_config.get('num_boost_round', 200)
    early_stopping_rounds = xgb_config.get('early_stopping_rounds', 20)
    
    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (len(class_counts) * class_counts)
    sample_weights = np.array([class_weights[label] for label in y_train])
    
    dtrain = xgb.DMatrix(X_train, label=y_train, weight=sample_weights, feature_names=feature_names)
    dval = xgb.DMatrix(X_val, label=y_val, feature_names=feature_names)
    
    watchlist = [(dtrain, 'train'), (dval, 'val')]
    model = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist,
                      early_stopping_rounds=early_stopping_rounds, verbose_eval=30)
    
    y_pred = model.predict(dval)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    
    print(f"\nXGBoost - Accuracy: {accuracy:.4f}, LogLoss: {ll:.4f}, Brier: {brier:.4f}")
    print("Confusion Matrix:")
    print(confusion_matrix(y_val, y_pred_class))
    print("\nClassification Report:")
    print(classification_report(y_val, y_pred_class))
    
    print("\nCalibrating XGBoost probabilities...")
    X_train_df = pd.DataFrame(X_train, columns=feature_names)
    X_val_df = pd.DataFrame(X_val, columns=feature_names)
    
    xgb_wrapper = XGBClassifierWrapper(params=params)
    xgb_wrapper.fit(X_train_df, y_train)
    
    try:
        calib_model = CalibratedClassifierCV(xgb_wrapper, method='sigmoid', cv=5)
        calib_model.fit(X_val_df, y_val)
        
        y_pred_calib = calib_model.predict_proba(X_val_df)
        y_pred_class_calib = np.argmax(y_pred_calib, axis=1)
        
        accuracy_calib = accuracy_score(y_val, y_pred_class_calib)
        ll_calib = log_loss(y_val, y_pred_calib)
        brier_calib = brier_score_loss(y_val, y_pred_calib, pos_label=2)
        
        print(f"XGBoost (Calibrated) - Accuracy: {accuracy_calib:.4f}, LogLoss: {ll_calib:.4f}, Brier: {brier_calib:.4f}")
        
        return (calib_model, model), {'accuracy': accuracy_calib, 'log_loss': ll_calib, 'brier': brier_calib,
                                      'raw_accuracy': accuracy, 'raw_log_loss': ll, 'raw_brier': brier}
    except Exception as e:
        print(f"Calibration failed: {e}")
        return (model, model), {'accuracy': accuracy, 'log_loss': ll, 'brier': brier}

def train_lightgbm(X_train, y_train, X_val, y_val, feature_names):
    if lgb is None:
        return None, None
    
    lgb_config = CONFIG.get('model', {}).get('lightgbm', {})
    
    params = {
        'objective': 'multiclass',
        'num_class': 3,
        'metric': 'multi_logloss',
        'max_depth': lgb_config.get('max_depth', 5),
        'learning_rate': lgb_config.get('learning_rate', 0.1),
        'num_leaves': lgb_config.get('num_leaves', 31),
        'subsample': lgb_config.get('subsample', 0.8),
        'colsample_bytree': lgb_config.get('colsample_bytree', 0.8),
        'reg_alpha': lgb_config.get('reg_alpha', 0.0),
        'reg_lambda': lgb_config.get('reg_lambda', 1.0),
        'min_child_weight': lgb_config.get('min_child_weight', 1),
        'seed': 42,
        'verbose': 1
    }
    
    num_boost_round = lgb_config.get('num_boost_round', 200)
    early_stopping_rounds = lgb_config.get('early_stopping_rounds', 20)
    
    class_counts_lgb = np.bincount(y_train)
    class_weights_lgb = len(y_train) / (len(class_counts_lgb) * class_counts_lgb)
    sample_weights_lgb = np.array([class_weights_lgb[label] for label in y_train])
    
    lgb_train = lgb.Dataset(X_train, y_train, weight=sample_weights_lgb, feature_name=feature_names)
    lgb_val = lgb.Dataset(X_val, y_val, reference=lgb_train, feature_name=feature_names)
    
    callbacks = [lgb.early_stopping(stopping_rounds=early_stopping_rounds), lgb.log_evaluation(period=30)]
    
    model = lgb.train(params, lgb_train, num_boost_round=num_boost_round,
                      valid_sets=[lgb_val], callbacks=callbacks)
    
    y_pred = model.predict(X_val)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    
    print(f"\nLightGBM - Accuracy: {accuracy:.4f}, LogLoss: {ll:.4f}, Brier: {brier:.4f}")
    print("Confusion Matrix:")
    print(confusion_matrix(y_val, y_pred_class))
    print("\nClassification Report:")
    print(classification_report(y_val, y_pred_class))
    
    print("\nCalibrating LightGBM probabilities...")
    X_train_df = pd.DataFrame(X_train, columns=feature_names)
    X_val_df = pd.DataFrame(X_val, columns=feature_names)
    
    lgb_wrapper = LGBClassifierWrapper(params=params)
    lgb_wrapper.fit(X_train_df, y_train)
    
    try:
        calib_model = CalibratedClassifierCV(lgb_wrapper, method='sigmoid', cv=5)
        calib_model.fit(X_val_df, y_val)
        
        y_pred_calib = calib_model.predict_proba(X_val_df)
        y_pred_class_calib = np.argmax(y_pred_calib, axis=1)
        
        accuracy_calib = accuracy_score(y_val, y_pred_class_calib)
        ll_calib = log_loss(y_val, y_pred_calib)
        brier_calib = brier_score_loss(y_val, y_pred_calib, pos_label=2)
        
        print(f"LightGBM (Calibrated) - Accuracy: {accuracy_calib:.4f}, LogLoss: {ll_calib:.4f}, Brier: {brier_calib:.4f}")
        
        return (calib_model, model), {'accuracy': accuracy_calib, 'log_loss': ll_calib, 'brier': brier_calib,
                                      'raw_accuracy': accuracy, 'raw_log_loss': ll, 'raw_brier': brier}
    except Exception as e:
        print(f"Calibration failed: {e}")
        return (model, model), {'accuracy': accuracy, 'log_loss': ll, 'brier': brier}

def convert_xgb_to_js(model):
    if model is None:
        return None
    
    trees = []
    base_score = model.attributes().get('base_score', 0.5)
    
    tree_dump = model.get_dump()
    for i in range(len(tree_dump)):
        tree_str = tree_dump[i]
        nodes = []
        current_node = {}
        
        for line in tree_str.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('0:'):
                if current_node:
                    nodes.append(current_node)
                current_node = {'node_id': 0}
                parts = line[2:].split('[')
                if len(parts) > 1:
                    cond_part = parts[1].split(']')[0]
                    feature, rest = cond_part.split('<')
                    current_node['split'] = {
                        'feature': feature.strip(),
                        'threshold': float(rest.strip())
                    }
                    goto_part = parts[1].split(']')[1]
                    left = int(goto_part.split('yes=')[1].split(',')[0])
                    right = int(goto_part.split('no=')[1].split(',')[0])
                    current_node['split']['left'] = left
                    current_node['split']['right'] = right
                else:
                    current_node['leaf'] = float(line.split(':')[1].split('leaf=')[1].strip())
            else:
                node_id = int(line.split(':')[0])
                if 'leaf=' in line:
                    leaf_val = float(line.split('leaf=')[1].strip())
                    nodes.append({'node_id': node_id, 'leaf': leaf_val})
                else:
                    parts = line.split('[')
                    cond_part = parts[1].split(']')[0]
                    feature, rest = cond_part.split('<')
                    split_info = {
                        'feature': feature.strip(),
                        'threshold': float(rest.strip())
                    }
                    goto_part = parts[1].split(']')[1]
                    left = int(goto_part.split('yes=')[1].split(',')[0])
                    right = int(goto_part.split('no=')[1].split(',')[0])
                    split_info['left'] = left
                    split_info['right'] = right
                    nodes.append({'node_id': node_id, 'split': split_info})
        
        trees.append({'nodes': nodes})
    
    js_model = {
        'base': base_score,
        'lr': 0.08,
        'trees': trees
    }
    
    return js_model

def convert_lgb_to_js(model):
    if model is None:
        return None
    
    trees = []
    base_score = 0.5
    
    tree_info = model.dump_model()
    for tree in tree_info['tree_info']:
        nodes = []
        node_counter = 0
        
        def parse_node(node):
            nonlocal node_counter
            current_id = node_counter
            node_counter += 1
            
            if 'split_index' in node:
                feature_name = tree_info['feature_names'][node['split_index']]
                left_id = node_counter
                right_id = node_counter + 1
                
                nodes.append({
                    'node_id': current_id,
                    'split': {
                        'feature': feature_name,
                        'threshold': node['threshold'],
                        'left': left_id,
                        'right': right_id
                    }
                })
                
                parse_node(node['left_child'])
                parse_node(node['right_child'])
            else:
                leaf_value = node['leaf_value']
                if isinstance(leaf_value, (list, tuple)):
                    leaf_value = leaf_value[0]
                nodes.append({
                    'node_id': current_id,
                    'leaf': leaf_value
                })
        
        parse_node(tree['tree_structure'])
        trees.append({'nodes': nodes})
    
    js_model = {
        'base': base_score,
        'lr': 0.08,
        'trees': trees
    }
    
    return js_model

def save_model_to_js(js_model, model_name):
    output_path = os.path.join(OUTPUT_DIR, f"{model_name.lower()}_model_export.js")
    
    js_content = f"var {model_name.upper()}_MODEL = {json.dumps(js_model, indent=2)};"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print(f"\nSaved {model_name} model to {output_path}")
    return output_path

def calculate_bet_returns(X_val, y_val, y_pred, odds_df, stake=100):
    results = []
    for i, (actual, pred_probs) in enumerate(zip(y_val, y_pred)):
        pred_class = np.argmax(pred_probs)
        
        if pred_class == 2:
            odds = odds_df.iloc[i]['Pinnacle_主胜']
            bet_type = '主胜'
        elif pred_class == 1:
            odds = odds_df.iloc[i]['Pinnacle_平局']
            bet_type = '平局'
        else:
            odds = odds_df.iloc[i]['Pinnacle_客胜']
            bet_type = '客胜'
        
        if pred_class == actual:
            profit = stake * (odds - 1)
            won = True
        else:
            profit = -stake
            won = False
        
        results.append({
            'pred': pred_class,
            'actual': actual,
            'won': won,
            'odds': odds,
            'stake': stake,
            'profit': profit,
            'bet_type': bet_type,
            'confidence': pred_probs[pred_class]
        })
    
    results_df = pd.DataFrame(results)
    total_bets = len(results_df)
    winning_bets = results_df['won'].sum()
    total_profit = results_df['profit'].sum()
    total_staked = total_bets * stake
    
    print(f"\n=== 回测结果 ===")
    print(f"总投注次数: {total_bets}")
    print(f"获胜次数: {winning_bets}")
    print(f"胜率: {winning_bets/total_bets:.4f}")
    print(f"总投入: {total_staked:.2f}")
    print(f"总盈亏: {total_profit:.2f}")
    print(f"回报率: {total_profit/total_staked*100:.2f}%")
    
    return results_df

def evaluate_with_time_series_split(X, y, odds_features, feature_names, n_splits=5):
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    xgb_results = []
    lgb_results = []
    
    print(f"\n{'='*60}")
    print(f"滚动窗口验证 ({n_splits}折时间序列交叉验证)")
    print(f"{'='*60}")
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        print(f"\n--- 第 {fold+1}/{n_splits} 折 ---")
        
        X_train_fold, X_val_fold = X.iloc[train_idx], X.iloc[val_idx]
        y_train_fold, y_val_fold = y.iloc[train_idx], y.iloc[val_idx]
        odds_val_fold = odds_features.iloc[val_idx]
        
        print(f"  训练集: {len(X_train_fold)} 场, 验证集: {len(X_val_fold)} 场")
        
        scaler_fold = StandardScaler()
        X_train_scaled = scaler_fold.fit_transform(X_train_fold)
        X_val_scaled = scaler_fold.transform(X_val_fold)
        
        if xgb is not None:
            xgb_models_fold, xgb_metrics_fold = train_xgboost(X_train_scaled, y_train_fold.values,
                                                              X_val_scaled, y_val_fold.values, feature_names)
            if xgb_models_fold and xgb_metrics_fold:
                xgb_results.append(xgb_metrics_fold)
                xgb_calib_fold, xgb_raw_fold = xgb_models_fold
                if hasattr(xgb_calib_fold, 'predict_proba'):
                    y_pred = xgb_calib_fold.predict_proba(X_val_scaled)
                else:
                    y_pred = xgb_calib_fold.predict(xgb.DMatrix(X_val_scaled, feature_names=feature_names))
                calculate_bet_returns(X_val_fold, y_val_fold, y_pred, odds_val_fold)
        
        if lgb is not None:
            lgb_models_fold, lgb_metrics_fold = train_lightgbm(X_train_scaled, y_train_fold.values,
                                                               X_val_scaled, y_val_fold.values, feature_names)
            if lgb_models_fold and lgb_metrics_fold:
                lgb_results.append(lgb_metrics_fold)
                lgb_calib_fold, lgb_raw_fold = lgb_models_fold
                if hasattr(lgb_calib_fold, 'predict_proba'):
                    y_pred = lgb_calib_fold.predict_proba(X_val_scaled)
                else:
                    y_pred = lgb_calib_fold.predict(X_val_scaled)
                calculate_bet_returns(X_val_fold, y_val_fold, y_pred, odds_val_fold)
    
    print(f"\n{'='*60}")
    print("滚动窗口验证汇总")
    print(f"{'='*60}")
    
    if xgb_results:
        print("\nXGBoost 交叉验证结果:")
        acc_list = [r['accuracy'] for r in xgb_results]
        ll_list = [r['log_loss'] for r in xgb_results]
        brier_list = [r['brier'] for r in xgb_results]
        print(f"  准确率: {np.mean(acc_list):.4f} ± {np.std(acc_list):.4f}")
        print(f"  LogLoss: {np.mean(ll_list):.4f} ± {np.std(ll_list):.4f}")
        print(f"  Brier Score: {np.mean(brier_list):.4f} ± {np.std(brier_list):.4f}")
    
    if lgb_results:
        print("\nLightGBM 交叉验证结果:")
        acc_list = [r['accuracy'] for r in lgb_results]
        ll_list = [r['log_loss'] for r in lgb_results]
        brier_list = [r['brier'] for r in lgb_results]
        print(f"  准确率: {np.mean(acc_list):.4f} ± {np.std(acc_list):.4f}")
        print(f"  LogLoss: {np.mean(ll_list):.4f} ± {np.std(ll_list):.4f}")
        print(f"  Brier Score: {np.mean(brier_list):.4f} ± {np.std(brier_list):.4f}")
    
    return xgb_results, lgb_results

def main():
    print("=" * 60)
    print("足球比赛预测模型回测训练 (包含赔率数据)")
    print("=" * 60)
    
    print("\n1. 加载数据库数据 (five_leagues.db + odds.db)...")
    raw_df = load_match_data_from_db()
    print(f"   原始数据: {len(raw_df)} 场比赛")
    print("   加载 close 赔率 (odds.db wdl_history/handicap_history)...")
    raw_df, odds_hit_rate = load_close_odds_from_db(raw_df)
    print(f"   赔率匹配命中率: {odds_hit_rate*100:.1f}%")
    
    print("\n2. 清洗和转换数据...")
    df = clean_and_transform_data(raw_df)
    df = df.sort_values('date').reset_index(drop=True)
    
    print(f"   有效数据: {len(df)} 场比赛")
    print(f"   比赛结果分布:")
    print(df['result'].value_counts())
    
    print("\n3. 构建特征...")
    print("   3.1 赔率特征...")
    odds_features = build_odds_features(df)
    print(f"      赔率特征: {odds_features.shape[1]} 个")
    
    print("   3.2 比赛特征...")
    match_features = build_match_features(df)
    print(f"      比赛特征: {match_features.shape[1]} 个")
    
    print("   3.3 球队历史特征...")
    team_features = build_team_features(df)
    print(f"      球队特征: {team_features.shape[1]} 个")
    
    team_features = team_features.drop(columns=[col for col in team_features.columns if col in match_features.columns])
    
    X_train_features = pd.concat([match_features, team_features], axis=1)
    X_with_odds = pd.concat([odds_features, match_features, team_features], axis=1)
    y = df['result']
    
    print(f"\n   训练特征维度(不含赔率): {X_train_features.shape[1]}")
    print(f"   训练特征列表: {list(X_train_features.columns)}")
    print(f"   赔率特征维度: {odds_features.shape[1]}")
    
    print("\n4. 特征标准化...")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_train_features)
    X_scaled_df = pd.DataFrame(X_scaled, columns=X_train_features.columns, index=X_train_features.index)
    
    print("\n5. 滚动窗口验证...")
    evaluate_with_time_series_split(X_scaled_df, y, odds_features, X_train_features.columns.tolist(), n_splits=5)
    
    print("\n6. 划分训练/测试集 (时间序列分割)...")
    train_size = int(len(df) * 0.75)
    X_train, X_val = X_scaled_df.iloc[:train_size], X_scaled_df.iloc[train_size:]
    y_train, y_val = y.iloc[:train_size], y.iloc[train_size:]
    
    odds_val = odds_features.iloc[train_size:]
    
    print(f"   训练集: {len(X_train)} 场")
    print(f"   验证集: {len(X_val)} 场")
    
    print("\n7. 训练XGBoost模型...")
    xgb_models, xgb_metrics = train_xgboost(X_train.values, y_train.values, 
                                            X_val.values, y_val.values, X_train_features.columns.tolist())
    
    if xgb_models:
        xgb_calib_model, xgb_raw_model = xgb_models
        if hasattr(xgb_calib_model, 'predict_proba'):
            y_pred_xgb = xgb_calib_model.predict_proba(X_val)
        else:
            y_pred_xgb = xgb_calib_model.predict(xgb.DMatrix(X_val.values, feature_names=X_train_features.columns.tolist()))
        calculate_bet_returns(X_val, y_val, y_pred_xgb, odds_val)
    
    print("\n8. 训练LightGBM模型...")
    lgb_models, lgb_metrics = train_lightgbm(X_train.values, y_train.values,
                                             X_val.values, y_val.values, X_train_features.columns.tolist())
    
    if lgb_models:
        lgb_calib_model, lgb_raw_model = lgb_models
        if hasattr(lgb_calib_model, 'predict_proba'):
            y_pred_lgb = lgb_calib_model.predict_proba(X_val)
        else:
            y_pred_lgb = lgb_calib_model.predict(X_val.values)
        calculate_bet_returns(X_val, y_val, y_pred_lgb, odds_val)
    
    print("\n9. 保存特征标准化参数...")
    scaler_params = {
        'mean': scaler.mean_.tolist(),
        'scale': scaler.scale_.tolist(),
        'feature_names': X_train_features.columns.tolist()
    }
    scaler_path = os.path.join(OUTPUT_DIR, 'feature_scaler_params.js')
    with open(scaler_path, 'w', encoding='utf-8') as f:
        f.write(f"var FEATURE_SCALER_PARAMS = {json.dumps(scaler_params, indent=2)};")
    print(f"   已保存到 {scaler_path}")
    
    print("\n10. 转换模型为JavaScript格式...")
    if xgb_models:
        xgb_calib_model, xgb_raw_model = xgb_models
        xgb_js = convert_xgb_to_js(xgb_raw_model)
        save_model_to_js(xgb_js, 'XGB')
    
    if lgb_models:
        lgb_calib_model, lgb_raw_model = lgb_models
        lgb_js = convert_lgb_to_js(lgb_raw_model)
        save_model_to_js(lgb_js, 'LGB')
    
    print("\n" + "=" * 60)
    print("回测训练完成!")
    print("=" * 60)
    
    if xgb_metrics:
        print(f"\nXGBoost 指标:")
        print(f"  原始准确率: {xgb_metrics.get('raw_accuracy', xgb_metrics['accuracy']):.4f}")
        print(f"  校准准确率: {xgb_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {xgb_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {xgb_metrics['brier']:.4f}")
    
    if lgb_metrics:
        print(f"\nLightGBM 指标:")
        print(f"  原始准确率: {lgb_metrics.get('raw_accuracy', lgb_metrics['accuracy']):.4f}")
        print(f"  校准准确率: {lgb_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {lgb_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {lgb_metrics['brier']:.4f}")
    
    print("\n特征重要性:")
    if xgb_models:
        xgb_calib_model, xgb_raw_model = xgb_models
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 8))
        xgb.plot_importance(xgb_raw_model, ax=ax, max_num_features=20)
        plt.savefig(os.path.join(OUTPUT_DIR, 'xgb_importance_no_odds.png'))
        plt.close()
        print("  XGBoost: 已保存到 xgb_importance_no_odds.png")
    
    if lgb_models:
        lgb_calib_model, lgb_raw_model = lgb_models
        fig, ax = plt.subplots(figsize=(12, 8))
        lgb.plot_importance(lgb_raw_model, ax=ax, max_num_features=20)
        plt.savefig(os.path.join(OUTPUT_DIR, 'lgb_importance_no_odds.png'))
        plt.close()
        print("  LightGBM: 已保存到 lgb_importance_no_odds.png")

if __name__ == "__main__":
    main()