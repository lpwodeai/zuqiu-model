"""
比赛级别 xG/射门特征提取模块
=============================

从 odds.db 的 match_player_stats 表提取球员级数据，聚合为比赛级 xG 和射门特征。

数据来源:
    - odds.db: match_player_stats 表（222,694条记录，5,265场比赛）
    - 与 matches 表直接关联率: 99.9%

特征设计（~20维，分主客队聚合）:
    【xG 特征 4维】
    - match_home_xg:          主队 expected_goals 总和
    - match_away_xg:          客队 expected_goals 总和
    - match_xg_diff:          主队 - 客队 xG 差值
    - match_total_xg:         两队 xG 总和

    【射门特征 6维】
    - match_home_total_shots: 主队总射门次数
    - match_away_total_shots: 客队总射门次数
    - match_shots_diff:       主队 - 客队射门差值
    - match_home_sot:         主队射正次数
    - match_away_sot:         客队射正次数
    - match_sot_diff:         主队 - 客队射正差值

    【进球/助攻特征 6维】
    - match_home_goals:       主队进球数
    - match_away_goals:       客队进球数
    - match_goal_diff:        主队 - 客队进球差
    - match_home_assists:     主队助攻数
    - match_away_assists:     客队助攻数
    - match_assist_diff:      主队 - 客队助攻差

    【评级特征 3维】
    - match_home_avg_rating:  主队平均评分
    - match_away_avg_rating:  客队平均评分
    - match_rating_diff:      主队 - 客队评分差

    【关键传球/机会 4维】
    - match_home_key_passes:  主队关键传球
    - match_away_key_passes:  客队关键传球
    - match_home_big_chances: 主队绝佳机会创造
    - match_away_big_chances: 客队绝佳机会创造

    【控球/传球 4维】
    - match_home_passes:      主队传球完成数
    - match_away_passes:      客队传球完成数
    - match_home_touches:     主队触球数（SofaScore来源）
    - match_away_touches:     客队触球数（SofaScore来源）

    【防守特征 4维】
    - match_home_tackles:     主队抢断
    - match_away_tackles:     客队抢断
    - match_home_interceptions: 主队拦截
    - match_away_interceptions: 客队拦截

总计: ~27维比赛级特征

设计原则:
    - 按主/客队分别聚合（通过 matches 表区分主客队）
    - 使用 SUM 聚合 xG/射门/进球等可加性指标
    - 使用 MEAN 聚合 rating 等均值指标
    - 缺失值使用联赛中位数 → 全局中位数填充
    - 严格使用赛前数据（此为赛后统计特征，仅在 T-004 训练验证中使用）
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')


def load_match_player_data(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载 match_player_stats 表数据，关联 matches 表获取主客队信息。
    
    返回 DataFrame，包含:
        - match_id: 比赛ID
        - team: 球员所属球队
        - home_team, away_team: 主队/客队名（来自 matches 表）
        - expected_goals, total_shots, shots_on_target, goals, assists
        - rating, key_passes, big_chances_created
        - passes_completed, touches_sofa, total_tackles, interceptions
        - league, match_date
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True
    
    query = """
        SELECT 
            mps.match_id,
            mps.team,
            mps.fbref_player_id,
            mps.player_name,
            mps.position,
            mps.is_starter,
            mps.minutes_played,
            mps.expected_goals,
            mps.xg,
            mps.total_shots,
            mps.shots_on_target,
            mps.goals,
            mps.assists,
            mps.rating,
            mps.key_passes,
            mps.big_chances_created,
            mps.passes_completed,
            mps.touches_sofa,
            mps.total_tackles,
            mps.interceptions,
            mps.shots,
            mps.crosses,
            mps.dribbles_completed_pos,
            mps.aerials_won,
            mps.ball_recoveries,
            mps.clearances,
            mps.fouls_committed,
            mps.fouls_drawn,
            mps.blocked_shots,
            mps.touches,
            mps.touches_att_pen_area,
            mps.progressive_passes,
            mps.progressive_carries,
            mt.home_team,
            mt.away_team,
            mt.match_type as league,
            mt.match_date
        FROM match_player_stats mps
        INNER JOIN matches mt ON mps.match_id = mt.match_id
    """
    
    df = pd.read_sql(query, conn)
    
    if close_conn:
        conn.close()
    
    print(f"[MATCH_LVL] 加载球员数据: {len(df)} 条记录")
    print(f"[MATCH_LVL] 唯一比赛数: {df['match_id'].nunique()}")
    print(f"[MATCH_LVL] 唯一球员数: {df['fbref_player_id'].nunique()}")
    
    return df


def determine_home_away(df: pd.DataFrame) -> pd.DataFrame:
    """
    判断每个球员记录属于主队还是客队。
    
    通过比较 team 列与 home_team/away_team 列来判断。
    """
    # 标记主客队
    df['is_home'] = df['team'] == df['home_team']
    df['is_away'] = df['team'] == df['away_team']
    
    # 检查未匹配的
    unmatched = df[~df['is_home'] & ~df['is_away']]
    if len(unmatched) > 0:
        print(f"[MATCH_LVL] 警告: {len(unmatched)} 条记录无法判断主客队 (team={unmatched['team'].unique()[:5]})")
    
    matched = df[df['is_home'] | df['is_away']]
    print(f"[MATCH_LVL] 主客队匹配: {len(matched)}/{len(df)} ({len(matched)/len(df)*100:.1f}%)")
    
    return matched


def aggregate_match_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    按 match_id 聚合球员级特征为比赛级特征。
    
    对每个比赛，分别计算主队和客队的聚合统计量。
    """
    # 定义聚合函数
    agg_funcs = {
        'expected_goals': 'sum',
        'xg': 'sum',
        'total_shots': 'sum',
        'shots_on_target': 'sum',
        'shots': 'sum',
        'goals': 'sum',
        'assists': 'sum',
        'rating': 'mean',
        'key_passes': 'sum',
        'big_chances_created': 'sum',
        'passes_completed': 'sum',
        'touches_sofa': 'sum',
        'touches': 'sum',
        'touches_att_pen_area': 'sum',
        'total_tackles': 'sum',
        'interceptions': 'sum',
        'crosses': 'sum',
        'dribbles_completed_pos': 'sum',
        'aerials_won': 'sum',
        'ball_recoveries': 'sum',
        'clearances': 'sum',
        'fouls_committed': 'sum',
        'fouls_drawn': 'sum',
        'blocked_shots': 'sum',
        'progressive_passes': 'sum',
        'progressive_carries': 'sum',
        'is_starter': 'sum',  # 首发人数
        'minutes_played': 'sum',  # 总出场时间
    }
    
    # 按主客队分别聚合
    home_df = df[df['is_home']].groupby('match_id').agg(agg_funcs)
    away_df = df[df['is_away']].groupby('match_id').agg(agg_funcs)
    
    # 重命名列
    home_df.columns = [f'home_{col}' for col in home_df.columns]
    away_df.columns = [f'away_{col}' for col in away_df.columns]
    
    # 合并主客队
    features = home_df.join(away_df, how='inner')
    
    # 添加 metadata
    meta = df.groupby('match_id').agg({
        'home_team': 'first',
        'away_team': 'first',
        'league': 'first',
        'match_date': 'first',
    })
    features = features.join(meta)
    
    # ---- 计算差值特征 ----
    for col in agg_funcs.keys():
        home_col = f'home_{col}'
        away_col = f'away_{col}'
        if home_col in features.columns and away_col in features.columns:
            features[f'diff_{col}'] = features[home_col] - features[away_col]
    
    # ---- 计算总计特征 ----
    features['total_xg'] = features['home_expected_goals'] + features['away_expected_goals']
    features['total_shots_all'] = features['home_total_shots'] + features['away_total_shots']
    features['total_sot'] = features['home_shots_on_target'] + features['away_shots_on_target']
    features['total_goals'] = features['home_goals'] + features['away_goals']
    
    # ---- 计算比率特征 ----
    # 射正率
    features['home_sot_pct'] = np.where(
        features['home_total_shots'] > 0,
        features['home_shots_on_target'] / features['home_total_shots'],
        0
    )
    features['away_sot_pct'] = np.where(
        features['away_total_shots'] > 0,
        features['away_shots_on_target'] / features['away_total_shots'],
        0
    )
    features['diff_sot_pct'] = features['home_sot_pct'] - features['away_sot_pct']
    
    # xG per shot (射门质量)
    features['home_xg_per_shot'] = np.where(
        features['home_total_shots'] > 0,
        features['home_expected_goals'] / features['home_total_shots'],
        0
    )
    features['away_xg_per_shot'] = np.where(
        features['away_total_shots'] > 0,
        features['away_expected_goals'] / features['away_total_shots'],
        0
    )
    features['diff_xg_per_shot'] = features['home_xg_per_shot'] - features['away_xg_per_shot']
    
    # 球员参与度
    features['home_players_used'] = features['home_is_starter']  # 出场球员数
    features['away_players_used'] = features['away_is_starter']
    
    # 人均 xG
    features['home_xg_per_player'] = np.where(
        features['home_players_used'] > 0,
        features['home_expected_goals'] / features['home_players_used'],
        0
    )
    features['away_xg_per_player'] = np.where(
        features['away_players_used'] > 0,
        features['away_expected_goals'] / features['away_players_used'],
        0
    )
    
    print(f"\n[MATCH_LVL] 聚合完成:")
    print(f"    比赛数: {len(features)}")
    print(f"    总特征维度: {len(features.columns)}")
    
    # 统计覆盖率
    key_cols = ['home_expected_goals', 'home_total_shots', 'home_shots_on_target', 
                'home_rating', 'home_goals', 'total_xg', 'total_shots_all']
    print(f"\n[MATCH_LVL] 关键特征覆盖率:")
    for col in key_cols:
        if col in features.columns:
            non_null = features[col].notna().sum()
            print(f"    {col}: {non_null}/{len(features)} ({non_null/len(features)*100:.1f}%)")
    
    return features


def select_match_features(features: pd.DataFrame) -> pd.DataFrame:
    """
    选择核心比赛级特征，添加 match_ 前缀以区分来源。
    
    返回精简的特征 DataFrame（index=match_id）。
    """
    # 核心特征列（按类别组织）
    core_cols = [
        # xG 特征 (8维)
        'home_expected_goals', 'away_expected_goals', 'diff_expected_goals', 'total_xg',
        'home_xg_per_shot', 'away_xg_per_shot', 'diff_xg_per_shot',
        'home_xg_per_player', 'away_xg_per_player',
        
        # 射门特征 (9维)
        'home_total_shots', 'away_total_shots', 'diff_total_shots', 'total_shots_all',
        'home_shots_on_target', 'away_shots_on_target', 'diff_shots_on_target', 'total_sot',
        'home_sot_pct', 'away_sot_pct', 'diff_sot_pct',
        
        # 进球/助攻 (6维)
        'home_goals', 'away_goals', 'diff_goals', 'total_goals',
        'home_assists', 'away_assists', 'diff_assists',
        
        # 评级 (3维)
        'home_rating', 'away_rating', 'diff_rating',
        
        # 关键传球/机会 (4维)
        'home_key_passes', 'away_key_passes', 'diff_key_passes',
        'home_big_chances_created', 'away_big_chances_created', 'diff_big_chances_created',
        
        # 传球 (3维)
        'home_passes_completed', 'away_passes_completed', 'diff_passes_completed',
        
        # 防守 (6维)
        'home_total_tackles', 'away_total_tackles', 'diff_total_tackles',
        'home_interceptions', 'away_interceptions', 'diff_interceptions',
        
        # 其他 (4维)
        'home_touches_sofa', 'away_touches_sofa', 'diff_touches_sofa',
        'home_touches_att_pen_area', 'away_touches_att_pen_area', 'diff_touches_att_pen_area',
        'home_clearances', 'away_clearances', 'diff_clearances',
        'home_crosses', 'away_crosses', 'diff_crosses',
        'home_dribbles_completed_pos', 'away_dribbles_completed_pos', 'diff_dribbles_completed_pos',
        'home_aerials_won', 'away_aerials_won', 'diff_aerials_won',
        'home_fouls_committed', 'away_fouls_committed', 'diff_fouls_committed',
        'home_fouls_drawn', 'away_fouls_drawn', 'diff_fouls_drawn',
        'home_ball_recoveries', 'away_ball_recoveries', 'diff_ball_recoveries',
        'home_progressive_passes', 'away_progressive_passes', 'diff_progressive_passes',
        'home_progressive_carries', 'away_progressive_carries', 'diff_progressive_carries',
    ]
    
    # 过滤存在的列
    available_cols = [c for c in core_cols if c in features.columns]
    missing = [c for c in core_cols if c not in features.columns]
    if missing:
        print(f"[MATCH_LVL] 缺失列 ({len(missing)}): {missing[:10]}...")
    
    # 选择特征 + metadata
    meta_cols = ['home_team', 'away_team', 'league', 'match_date']
    meta_available = [c for c in meta_cols if c in features.columns]
    
    selected = features[available_cols + meta_available].copy()
    
    # 添加 match_ 前缀
    rename_map = {}
    for col in available_cols:
        rename_map[col] = f'match_{col}'
    selected = selected.rename(columns=rename_map)
    
    print(f"\n[MATCH_LVL] 特征选择完成:")
    print(f"    特征维度: {len(available_cols)}")
    print(f"    Metadata: {len(meta_available)}")
    
    return selected


def build_match_level_features(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    一站式构建比赛级 xG/射门特征。
    
    返回:
        features DataFrame (index=match_id, ~50维比赛级特征)
    """
    # 1. 加载球员数据
    raw = load_match_player_data(conn)
    
    # 2. 判断主客队
    matched = determine_home_away(raw)
    
    # 3. 聚合为比赛级特征
    aggregated = aggregate_match_features(matched)
    
    # 4. 选择核心特征
    features = select_match_features(aggregated)
    
    return features


def build_match_features_for_tg(tg_features: pd.DataFrame, 
                                 conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    为 T-004 总进球预测构建比赛级特征，对齐 tg_features 的 index。
    
    参数:
        tg_features: build_tg_features() 返回的 DataFrame（index=matches_match_id）
        conn: 数据库连接
    
    返回:
        match_features DataFrame（index 对齐 tg_features.index）
    """
    # 构建比赛级特征
    match_feats = build_match_level_features(conn)
    
    # 对齐 index
    aligned = match_feats.reindex(tg_features.index)
    
    # 选择特征列（match_ 前缀，排除 metadata 列如 match_date）
    feature_cols = [c for c in aligned.columns if c.startswith('match_') and c != 'match_date']
    result = aligned[feature_cols].copy()
    
    # 确保所有列为数值类型
    for col in feature_cols:
        if result[col].dtype == object:
            result[col] = pd.to_numeric(result[col], errors='coerce')
    
    # 填充缺失值（按联赛 → 全局中位数）
    # 只对数值列进行填充
    numeric_cols = result.select_dtypes(include=[np.number]).columns.tolist()
    numeric_feature_cols = [c for c in feature_cols if c in numeric_cols]
    
    if 'league' in aligned.columns:
        for col in numeric_feature_cols:
            if result[col].isnull().any():
                try:
                    # 联赛中位数
                    league_medians = aligned.groupby('league')[col].transform('median')
                    result[col] = result[col].fillna(league_medians)
                except Exception:
                    pass
                # 全局中位数
                if result[col].isnull().any():
                    global_median = result[col].median()
                    if pd.notna(global_median):
                        result[col] = result[col].fillna(global_median)
                    else:
                        result[col] = result[col].fillna(0)
    else:
        for col in numeric_feature_cols:
            if result[col].isnull().any():
                result[col] = result[col].fillna(result[col].median() if result[col].notna().any() else 0)
    
    match_count = result.notna().all(axis=1).sum()
    print(f"\n[MATCH_LVL] T-004 对齐完成:")
    print(f"    特征维度: {len(feature_cols)}")
    print(f"    完整匹配: {match_count}/{len(tg_features)} ({match_count/len(tg_features)*100:.1f}%)")
    
    # 覆盖率详情
    print(f"\n[MATCH_LVL] T-004 特征覆盖率:")
    for col in feature_cols:
        non_null = result[col].notna().sum()
        coverage = non_null / len(result) * 100
        marker = '⚠️' if coverage < 50 else ('✅' if coverage >= 90 else '📊')
        print(f"    {marker} {col}: {non_null}/{len(result)} ({coverage:.1f}%)")
    
    return result


if __name__ == "__main__":
    # 独立运行：特征探索
    print("=" * 60)
    print("比赛级 xG/射门特征探索")
    print("=" * 60)
    
    features = build_match_level_features()
    
    print(f"\n[SUMMARY] 特征概览:")
    print(f"    比赛数: {len(features)}")
    match_cols = [c for c in features.columns if c.startswith('match_')]
    print(f"    特征维度: {len(match_cols)}")
    
    # 按联赛统计
    if 'league' in features.columns:
        print(f"\n[SUMMARY] 联赛分布:")
        for league, count in features['league'].value_counts().items():
            print(f"    {league}: {count} 场")
    
    # 特征统计
    print(f"\n[SUMMARY] 特征统计 (前10维):")
    stats = features[match_cols[:10]].describe()
    print(stats.to_string())
    
    # 相关性分析
    xg_cols = [c for c in match_cols if 'xg' in c.lower() or 'expected_goals' in c.lower()]
    shots_cols = [c for c in match_cols if 'shot' in c.lower() or 'sot' in c.lower()]
    
    if xg_cols:
        print(f"\n[SUMMARY] xG 特征相关性:")
        corr = features[xg_cols].corr()
        print(corr.to_string())