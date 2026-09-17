"""
SofaScore 球队级特征提取模块 (T-004 增强)
==========================================

从 odds.db 的 sofascore_team_features 表提取球队级近5场均值特征，
通过 match_id_en 关联 matches 表，为 T-004 总进球预测模型提供
正交于赔率的进攻/防守能力特征。

数据来源:
    - odds.db: sofascore_team_features 表（5,285 条，关联率 91.9%）
    - 关联方式: match_id_en → matches.match_id

特征设计:
    - 原始特征 (46维): 23 home + 23 away 近5场均值
    - 差值特征 (23维): home - away，直接衡量两队实力差距
    - 比值特征 (可选): home / away

特征分组:
    A. 进攻能力 (6维): xG, xA, 绝佳机会创造/错失, 传球成功率, 过人成功率
    B. 防守能力 (5维): 抢断, 拦截, 解围, 对抗成功率, 空中对抗成功率
    C. 跑动体能 (3维): 冲刺距离, 高强度跑, 总跑动距离
    D. 综合实力 (4维): 评分, 评分标准差, 阵型一致性, 丢失球权
    E. 门将表现 (2维): 扑救, 阻止进球
    F. 其他 (3维): 回收球权, 长传成功率, 传中成功率

设计原则:
    - 所有特征为赛前可用（近5场均值，不包含当前比赛）
    - 缺失值使用联赛中位数 → 全局中位数填充
    - 差值特征捕获两队实力差距，正交于赔率概率特征
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')

# SofaScore 特征定义
# 格式: (列名后缀, 显示名, 特征组)
SOFASCORE_FEATURE_DEFS = [
    # A. 进攻能力
    ('sofa_xg_5g', 'xG', 'attack'),
    ('sofa_xa_5g', 'xA', 'attack'),
    ('sofa_big_chance_c_5g', '创造绝佳机会', 'attack'),
    ('sofa_big_chance_m_5g', '错失绝佳机会', 'attack'),
    ('sofa_pass_sr_5g', '传球成功率', 'attack'),
    ('sofa_dribble_sr_5g', '过人成功率', 'attack'),
    # B. 防守能力
    ('sofa_tackle_5g', '抢断', 'defense'),
    ('sofa_interception_5g', '拦截', 'defense'),
    ('sofa_clearance_5g', '解围', 'defense'),
    ('sofa_duel_sr_5g', '对抗成功率', 'defense'),
    ('sofa_aerial_sr_5g', '空中对抗成功率', 'defense'),
    # C. 跑动体能
    ('sofa_sprint_km_5g', '冲刺距离', 'physical'),
    ('sofa_hsr_km_5g', '高强度跑', 'physical'),
    ('sofa_total_dist_km_5g', '总跑动距离', 'physical'),
    # D. 综合实力
    ('sofa_rat_5g', '综合评分', 'overall'),
    ('sofa_rat_std_5g', '评分标准差', 'overall'),
    ('sofa_formation_consistency', '阵型一致性', 'overall'),
    ('sofa_poss_lost_5g', '丢失球权', 'overall'),
    # E. 门将表现
    ('sofa_gk_saves_5g', '门将扑救', 'goalkeeper'),
    ('sofa_gk_goals_prev_5g', '门将阻止进球', 'goalkeeper'),
    # F. 其他
    ('sofa_recovery_5g', '回收球权', 'other'),
    ('sofa_longball_sr_5g', '长传成功率', 'other'),
    ('sofa_cross_sr_5g', '传中成功率', 'other'),
]


def load_sofascore_data(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载 sofascore_team_features 数据，通过 match_id_en 关联 matches 表。

    返回 DataFrame，包含:
        - match_id_en: 英文 match_id（关联键）
        - match_id_cn: 原始中文 match_id
        - match_date: 比赛日期
        - 所有 46 维数值特征（23 home + 23 away）
        - actual_total_goals: 实际总进球（来自 matches 表）
        - league: 联赛名称
        - home_team, away_team: 球队名
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    query = """
        SELECT 
            s.match_id_en,
            s.match_id_cn,
            s.match_date,
            s.home_team_cn,
            s.away_team_cn,
            s.league,
            s.sofa_rat_5g_home,
            s.sofa_xg_5g_home,
            s.sofa_xa_5g_home,
            s.sofa_pass_sr_5g_home,
            s.sofa_longball_sr_5g_home,
            s.sofa_cross_sr_5g_home,
            s.sofa_dribble_sr_5g_home,
            s.sofa_tackle_5g_home,
            s.sofa_interception_5g_home,
            s.sofa_duel_sr_5g_home,
            s.sofa_aerial_sr_5g_home,
            s.sofa_recovery_5g_home,
            s.sofa_poss_lost_5g_home,
            s.sofa_big_chance_c_5g_home,
            s.sofa_big_chance_m_5g_home,
            s.sofa_sprint_km_5g_home,
            s.sofa_hsr_km_5g_home,
            s.sofa_total_dist_km_5g_home,
            s.sofa_gk_saves_5g_home,
            s.sofa_gk_goals_prev_5g_home,
            s.sofa_clearance_5g_home,
            s.sofa_formation_consistency_home,
            s.sofa_rat_std_5g_home,
            s.sofa_rat_5g_away,
            s.sofa_xg_5g_away,
            s.sofa_xa_5g_away,
            s.sofa_pass_sr_5g_away,
            s.sofa_longball_sr_5g_away,
            s.sofa_cross_sr_5g_away,
            s.sofa_dribble_sr_5g_away,
            s.sofa_tackle_5g_away,
            s.sofa_interception_5g_away,
            s.sofa_duel_sr_5g_away,
            s.sofa_aerial_sr_5g_away,
            s.sofa_recovery_5g_away,
            s.sofa_poss_lost_5g_away,
            s.sofa_big_chance_c_5g_away,
            s.sofa_big_chance_m_5g_away,
            s.sofa_sprint_km_5g_away,
            s.sofa_hsr_km_5g_away,
            s.sofa_total_dist_km_5g_away,
            s.sofa_gk_saves_5g_away,
            s.sofa_gk_goals_prev_5g_away,
            s.sofa_clearance_5g_away,
            s.sofa_formation_consistency_away,
            s.sofa_rat_std_5g_away,
            mt.actual_total_goals,
            mt.match_type as mt_league,
            mt.home_team,
            mt.away_team
        FROM sofascore_team_features s
        INNER JOIN matches mt ON s.match_id_en = mt.match_id
    """

    df = pd.read_sql(query, conn)

    if close_conn:
        conn.close()

    # 使用 matches 表的联赛名（更准确）
    df['league'] = df['mt_league']
    df.drop(columns=['mt_league'], inplace=True)

    print(f"[SOFA] 加载数据: {len(df)} 条记录（关联率 {len(df)}/5285 = {len(df)/5285*100:.1f}%）")

    return df


def build_sofascore_features(df: pd.DataFrame, use_diff: bool = True, use_raw: bool = True) -> pd.DataFrame:
    """
    从 sofascore_team_features 数据构建特征矩阵。

    参数:
        df: load_sofascore_data() 返回的 DataFrame
        use_diff: 是否生成差值特征 (home - away)
        use_raw: 是否保留原始 home/away 特征

    返回:
        features DataFrame (index=match_id_en, sofa_ 前缀特征 + metadata)
    """
    features = pd.DataFrame(index=df.index)

    feat_count = 0

    for feat_suffix, feat_name, feat_group in SOFASCORE_FEATURE_DEFS:
        home_col = f'{feat_suffix}_home'
        away_col = f'{feat_suffix}_away'

        if home_col not in df.columns or away_col not in df.columns:
            continue

        home_vals = df[home_col].values
        away_vals = df[away_col].values

        if use_raw:
            # 原始 home/away 特征
            features[f'sofa_home_{feat_suffix}'] = home_vals
            features[f'sofa_away_{feat_suffix}'] = away_vals
            feat_count += 2

        if use_diff:
            # 差值特征 (home - away)
            features[f'sofa_diff_{feat_suffix}'] = home_vals - away_vals
            feat_count += 1

    # 填充缺失值
    null_count = features.isnull().sum().sum()
    if null_count > 0:
        print(f"[SOFA] 填充缺失值: {null_count} 个")
        # 使用中位数填充
        for col in features.columns:
            if features[col].isnull().any():
                features[col] = features[col].fillna(features[col].median())

    # 添加 metadata
    features['match_id_en'] = df['match_id_en'].values
    features['actual_total_goals'] = df['actual_total_goals'].values
    features['league'] = df['league'].values
    features['date'] = df['match_date'].values
    features['home_team'] = df['home_team'].values
    features['away_team'] = df['away_team'].values

    # 设置 index
    features = features.set_index('match_id_en')

    sofa_cols = [c for c in features.columns if c.startswith('sofa_')]
    print(f"[SOFA] 特征提取完成:")
    print(f"    总比赛数: {len(features)}")
    print(f"    特征维度: {len(sofa_cols)}")
    print(f"    有实际总进球: {features['actual_total_goals'].notna().sum()}")

    return features


def build_sofascore_features_for_tg(tg_features: pd.DataFrame,
                                     conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    为 T-004 总进球预测构建 SofaScore 增强特征。

    将 sofascore 特征与 tg_features 按 match_id 对齐。

    参数:
        tg_features: build_tg_features() 返回的 DataFrame (index=matches_match_id)
        conn: 数据库连接

    返回:
        pd.DataFrame: 与 tg_features index 对齐的 sofascore 特征矩阵
    """
    # 加载 sofascore 数据
    sofa_df = load_sofascore_data(conn)

    # 构建特征
    sofa_features = build_sofascore_features(sofa_df, use_diff=True, use_raw=False)

    # 对齐 index
    # tg_features index 是 matches_match_id，sofa_features index 是 match_id_en
    # 两者应该相同（都是 matches.match_id）
    aligned = sofa_features.reindex(tg_features.index)

    sofa_cols = [c for c in aligned.columns if c.startswith('sofa_')]
    matched = aligned[sofa_cols[0]].notna().sum() if sofa_cols else 0
    total = len(aligned)
    print(f"\n[SOFA→TG] 特征对齐:")
    print(f"    TG 样本数: {total}")
    print(f"    SofaScore 匹配: {matched} ({matched/total*100:.1f}%)")

    return aligned[sofa_cols]


if __name__ == "__main__":
    # 独立运行：数据探索
    print("=" * 60)
    print("SofaScore 球队级特征探索")
    print("=" * 60)

    df = load_sofascore_data()

    # 展示特征统计
    features = build_sofascore_features(df, use_diff=True, use_raw=False)

    sofa_cols = [c for c in features.columns if c.startswith('sofa_')]
    print(f"\n特征统计 (23维差值):")
    stats = features[sofa_cols].describe()
    print(stats.to_string())

    # 按联赛分组
    print(f"\n按联赛分布:")
    for league, count in features['league'].value_counts().items():
        print(f"  {league}: {count} 场")

    # 总进球分布
    valid = features[features['actual_total_goals'].notna()]
    print(f"\n总进球分布:")
    for g in range(7):
        cnt = (valid['actual_total_goals'].apply(lambda x: min(int(x), 6)) == g).sum()
        label = f'{g}球' if g < 6 else '6+球'
        print(f"  {label}: {cnt} ({cnt/len(valid)*100:.1f}%)")