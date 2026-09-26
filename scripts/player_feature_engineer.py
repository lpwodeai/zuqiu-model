import sqlite3
import pandas as pd
import numpy as np
import yaml
import os
import json
from datetime import datetime
from sklearn.feature_selection import mutual_info_classif
from sklearn.preprocessing import StandardScaler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"
CONFIG_PATH = BASE_DIR / "config.yaml"
OUTPUT_DIR = BASE_DIR / "output"

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

FOOTBALL_CONSTANTS = {
    'avg_minutes_per_match': 90,
    'avg_rating': 7.0,
    'avg_market_value': 2.0
}

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
        m.id as matchId,
        m.date,
        ht.name as home_team_name,
        ht.id as home_team_id,
        at.name as away_team_name,
        at.id as away_team_id,
        m.homeGoals,
        m.awayGoals,
        c.name as competition_name
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
    
    return df

def load_player_data(db_path=None):
    if db_path is None:
        db_path = DB_PATH
    
    conn = sqlite3.connect(db_path)
    
    players_query = """
    SELECT 
        p.id as playerId,
        p.name,
        p.teamId,
        p.position,
        p.positionGroup,
        p.age,
        p.height,
        p.weight,
        p.marketValue,
        p.isKeyPlayer,
        p.lineupRole,
        p.playerStatus
    FROM players p
    """
    players = pd.read_sql(players_query, conn)
    
    stats_query = """
    SELECT 
        ps.playerId,
        ps.season,
        ps.matches,
        ps.starts,
        ps.minutes,
        ps.goals,
        ps.assists,
        ps.xg,
        ps.xA,
        ps.shots,
        ps.shotsOnTarget,
        ps.bigChances,
        ps.bigChancesCreated,
        ps.tackles,
        ps.interceptions,
        ps.blocks,
        ps.duelsWon,
        ps.aerialWon,
        ps.dribblesCompleted,
        ps.dribblesAttempted,
        ps.passes,
        ps.passesCompleted,
        ps.keyPasses,
        ps.rating
    FROM player_stats ps
    """
    stats = pd.read_sql(stats_query, conn)
    
    conn.close()
    
    return players, stats

def build_player_basic_features(players):
    # 用 players 自身行索引（RangeIndex），赋值用 .values，避免与 playerId(1..1920) 索引错位
    features = pd.DataFrame(index=players.index)

    features['player_age'] = players['age'].fillna(25).values
    features['player_market_value'] = players['marketValue'].fillna(FOOTBALL_CONSTANTS['avg_market_value']).values
    features['player_height'] = players['height'].fillna(180).values
    features['player_weight'] = players['weight'].fillna(75).values

    features['player_is_key'] = players['isKeyPlayer'].fillna(0).astype(int).values

    features['player_role_starter'] = (players['lineupRole'] == 'starter').astype(int).values
    features['player_role_backup'] = (players['lineupRole'] == 'backup').astype(int).values

    features['player_status_available'] = (players['playerStatus'] == 'available').astype(int).values
    features['player_status_injured'] = (players['playerStatus'] == 'injured').astype(int).values
    features['player_status_suspended'] = (players['playerStatus'] == 'suspended').astype(int).values
    features['player_status_doubtful'] = (players['playerStatus'] == 'doubtful').astype(int).values

    pos_dummies = pd.get_dummies(players['positionGroup'], prefix='player_pos', drop_first=True)
    features = pd.concat([features, pos_dummies.reset_index(drop=True)], axis=1)
    
    feature_info = {
        'player_age': {'description': '球员年龄', 'source': 'players.age', 'calculation': '原始值，缺失填充25'},
        'player_market_value': {'description': '球员市场价值(亿欧元)', 'source': 'players.marketValue', 'calculation': '原始值，缺失填充2.0'},
        'player_height': {'description': '球员身高(cm)', 'source': 'players.height', 'calculation': '原始值，缺失填充180'},
        'player_weight': {'description': '球员体重(kg)', 'source': 'players.weight', 'calculation': '原始值，缺失填充75'},
        'player_is_key': {'description': '是否核心球员', 'source': 'players.isKeyPlayer', 'calculation': 'one-hot编码'},
        'player_role_starter': {'description': '是否首发角色', 'source': 'players.lineupRole', 'calculation': 'lineupRole=="starter"'},
        'player_role_backup': {'description': '是否替补角色', 'source': 'players.lineupRole', 'calculation': 'lineupRole=="backup"'},
        'player_status_available': {'description': '状态是否可用', 'source': 'players.playerStatus', 'calculation': 'status=="available"'},
        'player_status_injured': {'description': '状态是否受伤', 'source': 'players.playerStatus', 'calculation': 'status=="injured"'},
        'player_status_suspended': {'description': '状态是否停赛', 'source': 'players.playerStatus', 'calculation': 'status=="suspended"'},
        'player_status_doubtful': {'description': '状态是否存疑', 'source': 'players.playerStatus', 'calculation': 'status=="doubtful"'},
    }
    
    for pos in players['positionGroup'].unique():
        if pos != players['positionGroup'].unique()[0]:
            feature_info[f'player_pos_{pos}'] = {'description': f'位置是否为{pos}', 'source': 'players.positionGroup', 'calculation': 'one-hot编码'}
    
    return features, feature_info

def build_player_efficiency_features(stats):
    # stats 为 RangeIndex，所有派生 Series 同源对齐（禁止用 playerId 作索引）
    features = pd.DataFrame(index=stats.index)
    
    minutes_norm = stats['minutes'] / FOOTBALL_CONSTANTS['avg_minutes_per_match']
    minutes_norm = minutes_norm.replace(0, np.nan)
    
    features['goals_per_90'] = stats['goals'] / minutes_norm
    features['assists_per_90'] = stats['assists'] / minutes_norm
    features['xg_per_90'] = stats['xg'] / minutes_norm
    features['xa_per_90'] = stats['xA'] / minutes_norm
    features['shots_per_90'] = stats['shots'] / minutes_norm
    features['shots_on_target_per_90'] = stats['shotsOnTarget'] / minutes_norm
    features['key_passes_per_90'] = stats['keyPasses'] / minutes_norm
    features['tackles_per_90'] = stats['tackles'] / minutes_norm
    features['interceptions_per_90'] = stats['interceptions'] / minutes_norm
    features['blocks_per_90'] = stats['blocks'] / minutes_norm
    features['duels_won_per_90'] = stats['duelsWon'] / minutes_norm
    features['aerial_won_per_90'] = stats['aerialWon'] / minutes_norm
    features['dribbles_completed_per_90'] = stats['dribblesCompleted'] / minutes_norm
    features['passes_per_90'] = stats['passes'] / minutes_norm
    
    features['shots_on_target_rate'] = stats['shotsOnTarget'] / (stats['shots'] + 0.01)
    features['pass_completion_rate'] = stats['passesCompleted'] / (stats['passes'] + 0.01)
    features['dribble_success_rate'] = stats['dribblesCompleted'] / (stats['dribblesAttempted'] + 0.01)
    features['aerial_win_rate'] = stats['aerialWon'] / (stats['duelsWon'] + 0.01)
    features['big_chance_conversion'] = stats['goals'] / (stats['bigChances'] + 0.01)
    features['goal_xg_ratio'] = stats['goals'] / (stats['xg'] + 0.01)
    
    features['avg_rating'] = stats['rating'].fillna(FOOTBALL_CONSTANTS['avg_rating'])
    features['starts_ratio'] = stats['starts'] / (stats['matches'] + 0.01)
    
    features = features.fillna(0)
    
    feature_info = {
        'goals_per_90': {'description': '每90分钟进球', 'source': 'player_stats', 'calculation': 'goals / (minutes/90)'},
        'assists_per_90': {'description': '每90分钟助攻', 'source': 'player_stats', 'calculation': 'assists / (minutes/90)'},
        'xg_per_90': {'description': '每90分钟预期进球', 'source': 'player_stats', 'calculation': 'xg / (minutes/90)'},
        'xa_per_90': {'description': '每90分钟预期助攻', 'source': 'player_stats', 'calculation': 'xA / (minutes/90)'},
        'shots_per_90': {'description': '每90分钟射门', 'source': 'player_stats', 'calculation': 'shots / (minutes/90)'},
        'shots_on_target_per_90': {'description': '每90分钟射正', 'source': 'player_stats', 'calculation': 'shotsOnTarget / (minutes/90)'},
        'key_passes_per_90': {'description': '每90分钟关键传球', 'source': 'player_stats', 'calculation': 'keyPasses / (minutes/90)'},
        'tackles_per_90': {'description': '每90分钟抢断', 'source': 'player_stats', 'calculation': 'tackles / (minutes/90)'},
        'interceptions_per_90': {'description': '每90分钟拦截', 'source': 'player_stats', 'calculation': 'interceptions / (minutes/90)'},
        'blocks_per_90': {'description': '每90分钟封堵', 'source': 'player_stats', 'calculation': 'blocks / (minutes/90)'},
        'duels_won_per_90': {'description': '每90分钟赢得对抗', 'source': 'player_stats', 'calculation': 'duelsWon / (minutes/90)'},
        'aerial_won_per_90': {'description': '每90分钟赢得空中对抗', 'source': 'player_stats', 'calculation': 'aerialWon / (minutes/90)'},
        'dribbles_completed_per_90': {'description': '每90分钟成功过人', 'source': 'player_stats', 'calculation': 'dribblesCompleted / (minutes/90)'},
        'passes_per_90': {'description': '每90分钟传球', 'source': 'player_stats', 'calculation': 'passes / (minutes/90)'},
        'shots_on_target_rate': {'description': '射正率', 'source': 'player_stats', 'calculation': 'shotsOnTarget / (shots + 0.01)'},
        'pass_completion_rate': {'description': '传球成功率', 'source': 'player_stats', 'calculation': 'passesCompleted / (passes + 0.01)'},
        'dribble_success_rate': {'description': '过人成功率', 'source': 'player_stats', 'calculation': 'dribblesCompleted / (dribblesAttempted + 0.01)'},
        'aerial_win_rate': {'description': '空中对抗成功率', 'source': 'player_stats', 'calculation': 'aerialWon / (duelsWon + 0.01)'},
        'big_chance_conversion': {'description': '大机会转化率', 'source': 'player_stats', 'calculation': 'goals / (bigChances + 0.01)'},
        'goal_xg_ratio': {'description': '进球/xg比率', 'source': 'player_stats', 'calculation': 'goals / (xg + 0.01)'},
        'avg_rating': {'description': '平均评分', 'source': 'player_stats.rating', 'calculation': '原始值，缺失填充7.0'},
        'starts_ratio': {'description': '首发率', 'source': 'player_stats', 'calculation': 'starts / (matches + 0.01)'},
    }
    
    return features, feature_info

def aggregate_team_player_stats(players, stats):
    # stats 已携带真实 playerId 列（build_all_player_features 中以 .values 附带）
    player_features = pd.merge(players, stats, on='playerId', how='left')
    
    pos_cols = [col for col in player_features.columns if col.startswith('player_pos_')]
    
    agg_dict = {
        'player_market_value': ['sum', 'mean', 'max'],
        'goals_per_90': ['sum', 'mean', 'max'],
        'assists_per_90': ['sum', 'mean'],
        'xg_per_90': ['sum', 'mean'],
        'xa_per_90': ['sum', 'mean'],
        'avg_rating': ['mean', 'max'],
        'player_is_key': ['sum', 'mean'],
        'player_status_available': ['sum', 'mean'],
        'player_status_injured': ['sum', 'mean'],
        'player_status_suspended': ['sum', 'mean'],
        'playerId': 'count',
        'starts_ratio': ['mean', 'max'],
        'pass_completion_rate': 'mean',
        'tackles_per_90': ['sum', 'mean'],
        'interceptions_per_90': ['sum', 'mean'],
        'blocks_per_90': ['sum', 'mean'],
        'duels_won_per_90': ['sum', 'mean'],
        'aerial_won_per_90': ['sum', 'mean'],
        'dribbles_completed_per_90': ['sum', 'mean'],
        'shots_on_target_rate': 'mean',
        'big_chance_conversion': 'mean',
    }
    
    for pos_col in pos_cols:
        agg_dict[pos_col] = ['sum', 'mean']
    
    team_agg = player_features.groupby('teamId').agg(agg_dict)
    
    team_agg.columns = ['_'.join(col).strip() for col in team_agg.columns.values]
    team_agg = team_agg.fillna(0)
    
    return team_agg

def build_match_player_features(match_df, team_player_stats):
    features = pd.DataFrame(index=match_df.index)

    team_cols = list(team_player_stats.columns)
    zero_row = {c: 0 for c in team_cols}
    team_dict = team_player_stats.to_dict(orient='index')

    def _map_teams(team_ids):
        # 无球员数据的球队（如 id 98/99）→ 零行；显式对齐比赛索引，禁止 list+NaN 构造
        rows = [team_dict.get(tid, zero_row) for tid in team_ids]
        return pd.DataFrame.from_records(rows, columns=team_cols, index=match_df.index)

    home_df = _map_teams(match_df['home_team_id'])
    away_df = _map_teams(match_df['away_team_id'])

    home_df.columns = ['home_' + col for col in home_df.columns]
    away_df.columns = ['away_' + col for col in away_df.columns]

    match_player_features = pd.concat([home_df, away_df], axis=1)
    
    diff_features = pd.DataFrame(index=match_df.index)
    
    numeric_cols = [col for col in team_player_stats.columns if '_sum' not in col]
    for col in numeric_cols:
        if col in team_player_stats.columns:
            diff_features[f'{col}_diff'] = match_player_features[f'home_{col}'] - match_player_features[f'away_{col}']
    
    features = pd.concat([match_player_features, diff_features], axis=1)
    
    features['home_total_value'] = match_player_features['home_player_market_value_sum']
    features['away_total_value'] = match_player_features['away_player_market_value_sum']
    features['market_value_diff'] = features['home_total_value'] - features['away_total_value']
    
    features['home_top_rating'] = match_player_features['home_avg_rating_max']
    features['away_top_rating'] = match_player_features['away_avg_rating_max']
    features['top_rating_diff'] = features['home_top_rating'] - features['away_top_rating']
    
    features['home_avg_rating'] = match_player_features['home_avg_rating_mean']
    features['away_avg_rating'] = match_player_features['away_avg_rating_mean']
    features['avg_rating_diff'] = features['home_avg_rating'] - features['away_avg_rating']
    
    features['home_key_player_count'] = match_player_features['home_player_is_key_sum']
    features['away_key_player_count'] = match_player_features['away_player_is_key_sum']
    features['key_player_diff'] = features['home_key_player_count'] - features['away_key_player_count']
    
    features['home_available_count'] = match_player_features['home_player_status_available_sum']
    features['away_available_count'] = match_player_features['away_player_status_available_sum']
    
    features['home_injured_count'] = match_player_features['home_player_status_injured_sum']
    features['away_injured_count'] = match_player_features['away_player_status_injured_sum']
    features['injured_count_diff'] = features['home_injured_count'] - features['away_injured_count']
    
    features['home_suspended_count'] = match_player_features['home_player_status_suspended_sum']
    features['away_suspended_count'] = match_player_features['away_player_status_suspended_sum']
    features['suspended_count_diff'] = features['home_suspended_count'] - features['away_suspended_count']
    
    features['home_attack_power'] = (match_player_features['home_goals_per_90_sum'] + 
                                    match_player_features['home_xg_per_90_sum']) / 2
    features['away_attack_power'] = (match_player_features['away_goals_per_90_sum'] + 
                                    match_player_features['away_xg_per_90_sum']) / 2
    features['attack_power_diff'] = features['home_attack_power'] - features['away_attack_power']
    
    features['home_defense_power'] = (match_player_features['home_tackles_per_90_sum'] + 
                                     match_player_features['home_interceptions_per_90_sum'] + 
                                     match_player_features['home_blocks_per_90_sum']) / 3
    features['away_defense_power'] = (match_player_features['away_tackles_per_90_sum'] + 
                                     match_player_features['away_interceptions_per_90_sum'] + 
                                     match_player_features['away_blocks_per_90_sum']) / 3
    features['defense_power_diff'] = features['home_defense_power'] - features['away_defense_power']
    
    features = features.fillna(0)

    # 结构性未知行（球队无球员数据）：winsorize 会把零 clip 到 p1，
    # 把"未知"伪造成弱队值，故在 clip 后恢复这些行为零
    home_unknown = np.array([t not in team_dict for t in match_df['home_team_id']])
    away_unknown = np.array([t not in team_dict for t in match_df['away_team_id']])

    for col in features.columns:
        if col.startswith('home_') or col.startswith('away_') or col.endswith('_diff'):
            features[col] = winsorize_series(features[col], lower_percentile=1, upper_percentile=99)

    home_cols = [c for c in features.columns if c.startswith('home_')]
    away_cols = [c for c in features.columns if c.startswith('away_')]
    diff_cols = [c for c in features.columns if c.endswith('_diff')]
    features.loc[home_unknown, home_cols] = 0
    features.loc[away_unknown, away_cols] = 0
    # 任一侧未知，差值无意义
    features.loc[home_unknown | away_unknown, diff_cols] = 0

    return features

def build_all_player_features(match_df):
    print("加载球员数据...")
    players, stats = load_player_data()
    print(f"  球员总数: {len(players)}")
    print(f"  球员统计总数: {len(stats)}")
    
    print("\n构建球员基础特征...")
    basic_features, basic_info = build_player_basic_features(players)
    print(f"  基础特征维度: {len(basic_features.columns)}")
    
    print("\n构建球员效率特征...")
    efficiency_features, efficiency_info = build_player_efficiency_features(stats)
    print(f"  效率特征维度: {len(efficiency_features.columns)}")
    
    player_features = pd.merge(
        basic_features,
        efficiency_features,
        left_index=True,
        right_index=True,
        how='outer'
    ).fillna(0)

    # 显式附带真实 playerId（1..1920）供后续聚合，用 .values 防止按索引错位
    player_features['playerId'] = players['playerId'].values
    
    print(f"\n合并球员特征...")
    print(f"  总球员特征维度: {len(player_features.columns)}")
    
    print("\n聚合球队球员统计...")
    team_player_stats = aggregate_team_player_stats(players, player_features)
    print(f"  球队聚合特征维度: {len(team_player_stats.columns)}")
    
    print("\n构建比赛级球员特征...")
    match_player_features = build_match_player_features(match_df, team_player_stats)
    print(f"  比赛级球员特征维度: {len(match_player_features.columns)}")
    
    all_feature_info = {}
    all_feature_info.update(basic_info)
    all_feature_info.update(efficiency_info)
    
    for col in match_player_features.columns:
        if col.startswith('home_'):
            base_col = col.replace('home_', '')
            desc = f'主队{all_feature_info.get(base_col, {}).get("description", base_col)}'
        elif col.startswith('away_'):
            base_col = col.replace('away_', '')
            desc = f'客队{all_feature_info.get(base_col, {}).get("description", base_col)}'
        elif col.endswith('_diff'):
            base_col = col.replace('_diff', '')
            desc = f'{all_feature_info.get(base_col, {}).get("description", base_col)}差值'
        else:
            desc = col
        all_feature_info[col] = {'description': desc, 'source': '球员数据', 'calculation': '聚合计算'}
    
    return match_player_features, all_feature_info

def feature_selection_mutual_info(X, y, top_n=30):
    print(f"\n特征选择 (互信息, top_n={top_n})...")
    
    mi_scores = mutual_info_classif(X, y, random_state=42)
    
    mi_df = pd.DataFrame({
        'feature': X.columns,
        'mutual_info': mi_scores
    }).sort_values('mutual_info', ascending=False)
    
    selected = mi_df.head(top_n)
    
    print(f"\n互信息排名前10的球员特征:")
    for _, row in mi_df.head(10).iterrows():
        print(f"  {row['feature']}: {row['mutual_info']:.4f}")
    
    return mi_df, selected['feature'].tolist()

def analyze_feature_quality(X):
    print("\n球员特征质量分析:")
    print(f"  总特征数: {X.shape[1]}")
    print(f"  总样本数: {X.shape[0]}")
    
    missing_ratio = X.isnull().sum().mean() / X.shape[0] * 100
    print(f"  平均缺失率: {missing_ratio:.2f}%")
    
    constant_cols = [col for col in X.columns if X[col].nunique() <= 1]
    print(f"  常量特征数: {len(constant_cols)}")
    
    return {
        'total_features': X.shape[1],
        'total_samples': X.shape[0],
        'avg_missing_ratio': missing_ratio,
        'constant_features': constant_cols
    }

def save_player_features(X, feature_info, selected_features=None):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    X.to_csv(os.path.join(OUTPUT_DIR, f'player_features_{timestamp}.csv'), index=False, encoding='utf-8')
    print(f"\n球员特征矩阵已保存: player_features_{timestamp}.csv")
    
    if selected_features:
        pd.DataFrame({'feature': selected_features}).to_csv(
            os.path.join(OUTPUT_DIR, f'selected_player_features_{timestamp}.csv'), 
            index=False, encoding='utf-8'
        )
        print(f"选择的球员特征已保存: selected_player_features_{timestamp}.csv")
    
    with open(os.path.join(OUTPUT_DIR, f'player_feature_info_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump(feature_info, f, ensure_ascii=False, indent=2)
    print(f"球员特征信息已保存: player_feature_info_{timestamp}.json")
    
    return timestamp

def main():
    print("=" * 70)
    print("球员级特征工程 Pipeline")
    print("=" * 70)
    
    print("\n1. 加载比赛数据...")
    match_df = load_match_data()
    print(f"   比赛总数: {len(match_df)}")
    
    print("\n2. 构建球员特征...")
    X, feature_info = build_all_player_features(match_df)
    print(f"   球员特征维度: {X.shape[1]}")
    
    print("\n3. 特征质量分析...")
    quality_report = analyze_feature_quality(X)
    
    print("\n4. 特征选择...")
    mi_df, selected_features = feature_selection_mutual_info(X, match_df['result'], top_n=30)
    
    timestamp = save_player_features(X, feature_info, selected_features)
    
    mi_df.to_csv(os.path.join(OUTPUT_DIR, f'player_mutual_info_{timestamp}.csv'), index=False, encoding='utf-8')
    print(f"互信息排序已保存: player_mutual_info_{timestamp}.csv")
    
    print("\n" + "=" * 70)
    print("球员特征工程完成!")
    print("=" * 70)
    print(f"\n特征总维度: {X.shape[1]}")
    print(f"选择特征数: {len(selected_features)}")
    print(f"输出目录: {OUTPUT_DIR}")
    
    return X, match_df['result'], feature_info

if __name__ == "__main__":
    main()