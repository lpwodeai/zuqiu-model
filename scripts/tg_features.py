"""
总进球赔率特征提取模块 (T-004)
===============================

从 odds.db 的 total_goals_history 表提取总进球赔率数据，计算20维赛前可用特征。

数据来源:
    - odds.db: total_goals_history 表（通过 match_id_mapping 关联 matches 表）
    - 映射成功率: 98.6%（3,957/4,015 场）

输出特征（20维）:
    【基础概率特征 8维】
    - tg_prob_0 ~ tg_prob_7_plus: goals_0~goals_7_plus 原始概率（归一化）

    【衍生特征 12维】
    - tg_over_25_prob:        总进球>2.5 概率
    - tg_under_25_prob:       总进球≤2.5 概率
    - tg_over_15_prob:        总进球>1.5 概率
    - tg_over_35_prob:        总进球>3.5 概率
    - tg_expected_goals:      期望总进球（0*P0+1*P1+...+7*P7）
    - tg_most_likely_goals:   最可能总进球数（argmax）
    - tg_most_likely_prob:    最可能进球数的概率
    - tg_entropy:             总进球分布熵（越高=越不确定）
    - tg_top3_concentration:  前3最可能进球数概率集中度
    - tg_variance:            总进球概率分布方差
    - tg_median_goals:        概率分布中位数进球数
    - tg_low_score_prob:      低进球概率（0-1球）

设计原则:
    - 严格使用赛前赔率数据（取每个 match_id 的最新时间戳记录）
    - 过滤 2026-07 时间戳（project_memory 规则）
    - 通过 match_id_mapping 关联 matches 表获取实际总进球
    - 缺失值使用联赛中位数 → 全局中位数填充
"""

import pandas as pd
import numpy as np
from typing import Any, Optional, Dict, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from feature_utils import build_match_alignment
from db_utils import connect, read_sql  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')

# 2026-07 时间戳过滤阈值（project_memory 规则）
MAX_VALID_TIMESTAMP = "2026-07-01 00:00:00"

# 总进球类别列名
GOAL_COLS = ['goals_0', 'goals_1', 'goals_2', 'goals_3', 
             'goals_4', 'goals_5', 'goals_6', 'goals_7_plus']


def load_tg_data(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载总进球赔率数据，关联 match_id_mapping 和 matches 表。
    
    返回 DataFrame，包含:
        - history_match_id: 原始中文 match_id
        - matches_match_id: 映射后的英文 match_id
        - timestamp: 赔率时间戳
        - goals_0~goals_7_plus: 各进球数概率
        - actual_total_goals: 实际总进球（来自 matches 表）
        - league: 联赛名称
        - date: 比赛日期
        - home_team_name, away_team_name: 球队名
    """
    close_conn = False
    if conn is None:
        conn = connect(db_path=DB_PATH)
        close_conn = True
    
    # 1. 加载原始总进球赔率（暂不 JOIN matches，避免依赖单一桥表）
    df = read_sql(
        "SELECT match_id AS history_match_id, match_id_en, timestamp, "
        "goals_0, goals_1, goals_2, goals_3, "
        "goals_4, goals_5, goals_6, goals_7_plus "
        "FROM total_goals_history WHERE timestamp < ?",
        conn, params=(MAX_VALID_TIMESTAMP,)
    )

    # 2. 双通道对齐：history.match_id(中文) → matches.match_id(英文)
    alignment = build_match_alignment(conn, "total_goals_history")
    df["matches_match_id"] = df["history_match_id"].map(alignment)
    before = df["history_match_id"].nunique()
    dropped = df["matches_match_id"].isna().sum()
    df = df.dropna(subset=["matches_match_id"]).drop(columns=["match_id_en"])

    # 3. 合并 matches 元数据
    meta = read_sql(
        "SELECT match_id, actual_total_goals, match_type, match_date, "
        "home_team, away_team FROM matches",
        conn
    )
    df = df.merge(meta, left_on="matches_match_id", right_on="match_id", how="left") \
           .rename(columns={"match_type": "league", "match_date": "date"}) \
           .drop(columns=["match_id"]) \
           .sort_values(["history_match_id", "timestamp"]) \
           .reset_index(drop=True)

    if close_conn:
        conn.close()

    print(f"[TG] 加载原始数据: {len(df)} 条记录")
    print(f"[TG] 唯一 history_match_id: {df['history_match_id'].nunique()} / 原始 {before}")
    print(f"[TG] 唯一 matches_match_id: {df['matches_match_id'].nunique()}")
    print(f"[TG] 双通道对齐: 桥表+match_id_en 共 {len(alignment)} 场，丢弃未对齐 {dropped} 条")

    return df


def get_latest_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """
    取每个 match_id 的最新时间戳记录（赛前最后赔率快照）。
    
    参数:
        df: load_tg_data() 返回的 DataFrame
    
    返回:
        每个 match_id 仅保留最新一条记录的 DataFrame
    """
    # 按 match_id 分组，取 timestamp 最大的记录
    df_sorted = df.sort_values('timestamp')
    latest = df_sorted.groupby('history_match_id').last().reset_index()
    
    print(f"[TG] 最新快照: {len(latest)} 场比赛（从 {len(df)} 条记录中提取）")
    
    return latest


def normalize_probabilities(df: pd.DataFrame) -> pd.DataFrame:
    """
    归一化总进球概率，确保每行 goals_0~goals_7_plus 之和 = 1.0。
    
    处理异常：全0行 → 均匀分布；NaN → 0
    """
    goal_cols = GOAL_COLS
    
    # 填充 NaN
    df[goal_cols] = df[goal_cols].fillna(0.0)
    
    # 归一化
    row_sums = df[goal_cols].sum(axis=1)
    zero_mask = row_sums == 0
    
    if zero_mask.any():
        print(f"[TG] 警告: {zero_mask.sum()} 行概率全为0，使用均匀分布填充")
        for col in goal_cols:
            df.loc[zero_mask, col] = 1.0 / len(goal_cols)
        row_sums = df[goal_cols].sum(axis=1)
    
    # 归一化
    for col in goal_cols:
        df[col] = df[col] / row_sums
    
    # 验证
    new_sums = df[goal_cols].sum(axis=1)
    max_deviation = (new_sums - 1.0).abs().max()
    if max_deviation > 0.01:
        print(f"[TG] 警告: 归一化后最大偏差 = {max_deviation:.6f}")
    
    return df


def extract_tg_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    从总进球赔率数据中提取20维特征。
    
    参数:
        df: 已归一化的最新快照 DataFrame（含 goals_0~goals_7_plus）
    
    返回:
        features DataFrame，index 为 matches_match_id
    """
    goal_cols = GOAL_COLS
    probs = df[goal_cols].values  # shape: (n_samples, 8)
    
    features = pd.DataFrame(index=df.index)
    
    # ---- 基础概率特征 (8维) ----
    for i, col in enumerate(goal_cols):
        features[f'tg_prob_{i}'] = probs[:, i]
    
    # ---- 衍生特征 (12维) ----
    # 大/小球概率
    features['tg_over_25_prob'] = probs[:, 3:].sum(axis=1)  # goals_3~7_plus
    features['tg_under_25_prob'] = probs[:, :3].sum(axis=1)  # goals_0~2
    features['tg_over_15_prob'] = probs[:, 2:].sum(axis=1)   # goals_2~7_plus
    features['tg_over_35_prob'] = probs[:, 4:].sum(axis=1)   # goals_4~7_plus
    
    # 期望总进球: 0*P0 + 1*P1 + ... + 7*P7
    goal_values = np.array([0, 1, 2, 3, 4, 5, 6, 7])
    features['tg_expected_goals'] = (probs * goal_values).sum(axis=1)
    
    # 最可能进球数
    most_likely_idx = np.argmax(probs, axis=1)
    features['tg_most_likely_goals'] = goal_values[most_likely_idx]
    features['tg_most_likely_prob'] = np.max(probs, axis=1)
    
    # 熵: -sum(p*log(p))
    eps = 1e-10
    log_probs = np.log(np.clip(probs, eps, 1.0))
    features['tg_entropy'] = -np.sum(probs * log_probs, axis=1)
    
    # 前3集中度
    top3_probs = np.sort(probs, axis=1)[:, -3:]
    features['tg_top3_concentration'] = top3_probs.sum(axis=1)
    
    # 方差
    expected_sq = (probs * (goal_values ** 2)).sum(axis=1)
    features['tg_variance'] = expected_sq - features['tg_expected_goals'] ** 2
    features['tg_variance'] = features['tg_variance'].clip(lower=0)  # 防止数值误差导致负值
    
    # 中位数进球数（概率累积 ≥ 0.5 时的进球数）
    cumsum = np.cumsum(probs, axis=1)
    median_idx = np.argmax(cumsum >= 0.5, axis=1)
    features['tg_median_goals'] = goal_values[median_idx]
    
    # 低进球概率 (0-1球)
    features['tg_low_score_prob'] = probs[:, :2].sum(axis=1)
    
    # 添加 metadata 列
    features['matches_match_id'] = df['matches_match_id'].values
    features['actual_total_goals'] = df['actual_total_goals'].values
    features['league'] = df['league'].values
    features['date'] = df['date'].values
    features['home_team'] = df['home_team'].values
    features['away_team'] = df['away_team'].values
    
    # 设置 index
    features = features.set_index('matches_match_id')
    
    return features


def build_tg_features(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    一站式构建总进球预测特征。
    
    返回:
        features DataFrame (index=matches_match_id, 20维特征 + metadata)
    """
    # 1. 加载数据
    raw = load_tg_data(conn)
    
    # 2. 取最新快照
    latest = get_latest_snapshot(raw)
    
    # 3. 归一化概率
    normalized = normalize_probabilities(latest)
    
    # 4. 提取特征
    features = extract_tg_features(normalized)
    
    # 5. 统计
    valid = features['actual_total_goals'].notna().sum()
    total = len(features)
    print(f"\n[TG] 特征提取完成:")
    print(f"    总比赛数: {total}")
    print(f"    有实际总进球: {valid} ({valid/total*100:.1f}%)")
    print(f"    特征维度: {len([c for c in features.columns if c.startswith('tg_')])}")
    print(f"    联赛分布:")
    for league, count in features['league'].value_counts().items():
        print(f"      - {league}: {count} 场")
    
    return features


def get_tg_label_distribution(features: pd.DataFrame) -> pd.DataFrame:
    """
    获取总进球类别分布统计。
    """
    valid = features[features['actual_total_goals'].notna()].copy()
    valid['goals_category'] = valid['actual_total_goals'].apply(
        lambda x: f'{int(x)}球' if x < 6 else '6+球'
    )
    
    dist = valid['goals_category'].value_counts().sort_index()
    
    print("\n[TG] 总进球类别分布:")
    for cat, cnt in dist.items():
        print(f"    {cat}: {cnt} ({cnt/len(valid)*100:.1f}%)")
    
    over25 = (valid['actual_total_goals'] > 2).sum()
    under25 = (valid['actual_total_goals'] <= 2).sum()
    print(f"\n    大球 (>2.5): {over25} ({over25/len(valid)*100:.1f}%)")
    print(f"    小球 (≤2.5): {under25} ({under25/len(valid)*100:.1f}%)")
    
    return dist


if __name__ == "__main__":
    # 独立运行：数据探索
    features = build_tg_features()
    get_tg_label_distribution(features)
    
    print("\n[TG] 特征列名:")
    tg_cols = [c for c in features.columns if c.startswith('tg_')]
    for col in tg_cols:
        non_null = features[col].notna().sum()
        print(f"    {col}: non-null={non_null}/{len(features)}")
    
    print("\n[TG] 特征统计:")
    print(features[tg_cols].describe().to_string())