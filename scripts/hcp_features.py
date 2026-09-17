"""
让球胜平负赔率特征提取模块 (T-005)
==================================

从 odds.db 的 handicap_history 表提取让球赔率数据，计算15维赛前可用特征。

数据来源:
    - odds.db: handicap_history 表（通过 match_id_mapping 关联 matches 表）
    - 映射成功率: 98.6%（3,929/3,957 场）
    - 有实际让球结果: 242 场（actual_handicap 非空）

输出特征（15维）:
    【基础概率特征 3维】
    - hcp_prob_win, hcp_prob_draw, hcp_prob_lose: 归一化概率

    【衍生特征 12维】
    - hcp_home_strength:    上盘优势（win_prob - lose_prob）
    - hcp_draw_risk:        走水风险（draw_prob）
    - hcp_confidence:       市场信心（max probability）
    - hcp_entropy:          概率分布熵
    - hcp_expected_value:   隐含回报差（1/win - 1/lose）
    - hcp_volatility:       概率标准差
    - hcp_market_sentiment: 市场偏好（隐含概率比）
    - hcp_underdog_ratio:   冷门比（lose_prob / win_prob）
    - hcp_favorite_margin:  热门优势边际（1 - confidence）
    - hcp_balance:          实力均衡度（abs(win - lose)）
    - hcp_upset_risk:       冷门风险（draw + lose prob）
    - hcp_odds_skew:        赔率偏度

目标变量:
    - actual_handicap: 胜(上盘赢)=0, 平(走水)=1, 负(下盘赢)=2

设计原则:
    - 严格使用赛前赔率数据（取每个 match_id 的最新时间戳记录）
    - 过滤 2026-07 时间戳（project_memory 规则）
    - 通过 match_id_mapping 关联 matches 表获取实际让球结果
    - 缺失值使用联赛中位数 → 全局中位数填充
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, Dict, Tuple
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

# 让球赔率列名
HCP_COLS = ['hcp_win', 'hcp_draw', 'hcp_lose']

# 实际让球结果映射
HCP_RESULT_MAP = {'胜': 0, '平': 1, '负': 2}
HCP_RESULT_NAMES = {0: '上盘赢', 1: '走水', 2: '下盘赢'}


def load_hcp_data(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载让球赔率数据，关联 match_id_mapping 和 matches 表。

    返回 DataFrame，包含:
        - history_match_id: 原始中文 match_id
        - matches_match_id: 映射后的英文 match_id
        - timestamp: 赔率时间戳
        - hcp_win, hcp_draw, hcp_lose: 让球赔率
        - actual_handicap: 实际让球结果（来自 matches 表）
        - handicap: 让球盘口线
        - actual_score: 实际比分
        - league: 联赛名称
        - date: 比赛日期
        - home_team, away_team: 球队名
    """
    close_conn = False
    if conn is None:
        conn = connect(db_path=DB_PATH)
        close_conn = True

    # 1. 加载原始让球赔率（暂不 JOIN matches，避免依赖单一桥表）
    df = read_sql(
        "SELECT match_id AS history_match_id, match_id_en, timestamp, "
        "hcp_win, hcp_draw, hcp_lose "
        "FROM handicap_history WHERE timestamp < ?",
        conn, params=(MAX_VALID_TIMESTAMP,)
    )

    # 2. 双通道对齐：history.match_id(中文) → matches.match_id(英文)
    alignment = build_match_alignment(conn, "handicap_history")
    df["matches_match_id"] = df["history_match_id"].map(alignment)
    before = df["history_match_id"].nunique()
    dropped = df["matches_match_id"].isna().sum()
    df = df.dropna(subset=["matches_match_id"]).drop(columns=["match_id_en"])

    # 3. 合并 matches 元数据
    meta = read_sql(
        "SELECT match_id, actual_handicap, handicap, actual_score, "
        "match_type, match_date, home_team, away_team FROM matches",
        conn
    )
    df = df.merge(meta, left_on="matches_match_id", right_on="match_id", how="left") \
           .rename(columns={"handicap": "handicap_line",
                            "match_type": "league",
                            "match_date": "date"}) \
           .drop(columns=["match_id"]) \
           .sort_values(["history_match_id", "timestamp"]) \
           .reset_index(drop=True)

    if close_conn:
        conn.close()

    print(f"[HCP] 加载原始数据: {len(df)} 条记录")
    print(f"[HCP] 唯一 history_match_id: {df['history_match_id'].nunique()} / 原始 {before}")
    print(f"[HCP] 唯一 matches_match_id: {df['matches_match_id'].nunique()}")
    print(f"[HCP] 双通道对齐: 桥表+match_id_en 共 {len(alignment)} 场，丢弃未对齐 {dropped} 条")

    return df


def get_latest_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """
    取每个 match_id 的最新时间戳记录（赛前最后赔率快照）。
    """
    df_sorted = df.sort_values('timestamp')
    latest = df_sorted.groupby('history_match_id').last().reset_index()

    print(f"[HCP] 最新快照: {len(latest)} 场比赛（从 {len(df)} 条记录中提取）")

    return latest


def normalize_probabilities(df: pd.DataFrame) -> pd.DataFrame:
    """
    归一化让球赔率概率，确保每行 hcp_win/draw/lose 之和 = 1.0。

    处理异常：全0行 → 均匀分布；NaN → 0
    """
    hcp_cols = HCP_COLS

    # 填充 NaN
    df[hcp_cols] = df[hcp_cols].fillna(0.0)

    # 归一化
    row_sums = df[hcp_cols].sum(axis=1)
    zero_mask = row_sums == 0

    if zero_mask.any():
        print(f"[HCP] 警告: {zero_mask.sum()} 行概率全为0，使用均匀分布填充")
        for col in hcp_cols:
            df.loc[zero_mask, col] = 1.0 / len(hcp_cols)
        row_sums = df[hcp_cols].sum(axis=1)

    # 归一化
    for col in hcp_cols:
        df[col] = df[col] / row_sums

    # 验证
    new_sums = df[hcp_cols].sum(axis=1)
    max_deviation = (new_sums - 1.0).abs().max()
    if max_deviation > 0.01:
        print(f"[HCP] 警告: 归一化后最大偏差 = {max_deviation:.6f}")

    return df


def extract_hcp_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    从让球赔率数据中提取15维特征。

    参数:
        df: 已归一化的最新快照 DataFrame（含 hcp_win/draw/lose）

    返回:
        features DataFrame，index 为 matches_match_id
    """
    hcp_cols = HCP_COLS
    probs = df[hcp_cols].values  # shape: (n_samples, 3)

    features = pd.DataFrame(index=df.index)

    # ---- 基础概率特征 (3维) ----
    features['hcp_prob_win'] = probs[:, 0]
    features['hcp_prob_draw'] = probs[:, 1]
    features['hcp_prob_lose'] = probs[:, 2]

    # ---- 衍生特征 (12维) ----
    # 上盘优势
    features['hcp_home_strength'] = probs[:, 0] - probs[:, 2]

    # 走水风险
    features['hcp_draw_risk'] = probs[:, 1]

    # 市场信心
    features['hcp_confidence'] = np.max(probs, axis=1)

    # 熵: -sum(p*log(p))
    eps = 1e-10
    log_probs = np.log(np.clip(probs, eps, 1.0))
    features['hcp_entropy'] = -np.sum(probs * log_probs, axis=1)

    # 隐含回报差: 1/hcp_win - 1/hcp_lose
    raw_win = df['hcp_win'].values
    raw_lose = df['hcp_lose'].values
    features['hcp_expected_value'] = (1.0 / np.clip(raw_win, eps, None)) - (1.0 / np.clip(raw_lose, eps, None))

    # 概率标准差
    features['hcp_volatility'] = np.std(probs, axis=1)

    # 市场偏好: (1/hcp_win) / sum(1/hcp_i)
    inv_probs = 1.0 / np.clip(df[hcp_cols].values, eps, None)
    inv_sum = inv_probs.sum(axis=1)
    features['hcp_market_sentiment'] = inv_probs[:, 0] / inv_sum

    # 冷门比
    features['hcp_underdog_ratio'] = probs[:, 2] / np.clip(probs[:, 0], eps, None)

    # 热门优势边际
    features['hcp_favorite_margin'] = 1.0 - features['hcp_confidence']

    # 实力均衡度
    features['hcp_balance'] = np.abs(probs[:, 0] - probs[:, 2])

    # 冷门风险
    features['hcp_upset_risk'] = probs[:, 1] + probs[:, 2]

    # 赔率偏度
    raw_all = df[hcp_cols].values
    row_sums_raw = raw_all.sum(axis=1)
    features['hcp_odds_skew'] = (raw_win - raw_lose) / np.clip(row_sums_raw, eps, None)

    # 添加 metadata 列
    features['matches_match_id'] = df['matches_match_id'].values
    features['actual_handicap'] = df['actual_handicap'].values
    features['handicap_line'] = df['handicap_line'].values
    features['actual_score'] = df['actual_score'].values
    features['league'] = df['league'].values
    features['date'] = df['date'].values
    features['home_team'] = df['home_team'].values
    features['away_team'] = df['away_team'].values

    # 设置 index
    features = features.set_index('matches_match_id')

    return features


def build_hcp_features(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    一站式构建让球胜平负预测特征。

    返回:
        features DataFrame (index=matches_match_id, 15维特征 + metadata)
    """
    # 1. 加载数据
    raw = load_hcp_data(conn)

    # 2. 取最新快照
    latest = get_latest_snapshot(raw)

    # 3. 归一化概率
    normalized = normalize_probabilities(latest)

    # 4. 提取特征
    features = extract_hcp_features(normalized)

    # 5. 统计
    valid = features['actual_handicap'].notna().sum()
    total = len(features)
    print(f"\n[HCP] 特征提取完成:")
    print(f"    总比赛数: {total}")
    print(f"    有实际让球结果: {valid} ({valid/total*100:.1f}%)")
    print(f"    特征维度: {len([c for c in features.columns if c.startswith('hcp_')])}")
    print(f"    联赛分布:")
    for league, count in features['league'].value_counts().items():
        print(f"      - {league}: {count} 场")

    return features


def get_hcp_label_distribution(features: pd.DataFrame) -> pd.DataFrame:
    """
    获取让球结果类别分布统计。
    """
    valid = features[features['actual_handicap'].notna()].copy()

    print("\n[HCP] 让球结果类别分布:")
    for result_code, name in HCP_RESULT_NAMES.items():
        count = (valid['actual_handicap'] == result_code).sum()
        print(f"    {name} ({result_code}): {count} ({count/len(valid)*100:.1f}%)")

    # 有盘口线的分布
    has_line = valid[valid['handicap_line'].notna()]
    if len(has_line) > 0:
        print(f"\n[HCP] 有盘口线的比赛: {len(has_line)} 场")
        print(f"    盘口线分布:")
        for line, count in has_line['handicap_line'].value_counts().sort_index().items():
            print(f"      {line:+.1f}: {count} 场")

    return None


if __name__ == "__main__":
    # 独立运行：数据探索
    features = build_hcp_features()
    get_hcp_label_distribution(features)

    print("\n[HCP] 特征列名:")
    hcp_cols = [c for c in features.columns if c.startswith('hcp_')]
    for col in hcp_cols:
        non_null = features[col].notna().sum()
        print(f"    {col}: non-null={non_null}/{len(features)}")

    print("\n[HCP] 特征统计:")
    print(features[hcp_cols].describe().to_string())