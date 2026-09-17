"""
T-005 v2 增强版特征工程
========================

在原 15 维 HCP 赔率特征基础上，新增以下特征组：

新增特征组:
    【走水专用特征 3维】— 从 wdl_history 提取 WDL 平局赔率，与 HCP 走水赔率对比
    【球队近期状态 12维】— 从 matches 表计算每支球队近5场 lag 统计（严格赛前）
    【休息天数 3维】— 两队上次比赛到本场比赛的休息天数差
    【红黄牌风险 4维】— 从 match_player_stats 提取近5场 lag 红黄牌数（停赛风险代理）
    【Elo特征 10维】— 已有，球队实力排名

总计: 15 (HCP) + 3 (走水专用) + 12 (球队状态) + 3 (休息天数) + 4 (红黄牌) + 10 (Elo) = 47 维

数据泄露防护:
    - 球队近期状态: 仅使用 match_date < 当前比赛日期的历史比赛
    - 红黄牌: 仅使用 match_date < 当前比赛日期的历史比赛
    - WDL/HCP 赔率: 使用赛前最新快照（timestamp < 2026-07-01）
    - 所有 lag 特征严格按日期过滤

运行: python scripts/hcp_features_v2.py
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
from collections import defaultdict
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(__file__))

# 模块级 logger：用于特征对齐过程的关键节点追踪
# 日志可被训练脚本 / 预测脚本复用，便于排查 match_id 错位与缺失
logger = logging.getLogger("hcp_features_v2")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[%(asctime)s][%(name)s][%(levelname)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

from hcp_features import (
    build_hcp_features, HCP_COLS, HCP_RESULT_NAMES,
    MAX_VALID_TIMESTAMP, normalize_probabilities, get_latest_snapshot,
    load_hcp_data, extract_hcp_features,
)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')


# ========================================
# 1. 走水专用特征 (3维)
# ========================================

def build_wdl_draw_features(conn: sqlite3.Connection) -> pd.DataFrame:
    """
    从 wdl_history 提取 WDL 平局赔率，与 HCP 走水赔率对比。

    核心思路:
        - WDL draw 赔率反映市场对"比赛平局"的预期
        - HCP draw 赔率反映市场对"让球后平局(走水)"的预期
        - 两者差异可揭示走水概率的独立信号

    返回 DataFrame (index=history_match_id, 3维特征):
        - wdl_draw_odds: WDL 平局赔率
        - wdl_draw_prob: WDL 平局隐含概率
        - draw_divergence: 走水概率差异 (|wdl_draw_prob - hcp_draw_prob|)
    """
    # 加载 WDL 最新快照
    query = """
        SELECT match_id, draw as wdl_draw
        FROM wdl_history
        WHERE timestamp < ?
          AND draw IS NOT NULL
          AND draw > 0
        ORDER BY match_id, timestamp
    """
    df_wdl = pd.read_sql(query, conn, params=(MAX_VALID_TIMESTAMP,))

    # 取每个 match_id 的最新快照
    wdl_latest = df_wdl.groupby('match_id').last().reset_index()
    wdl_latest = wdl_latest.rename(columns={'match_id': 'history_match_id'})

    # 加载 HCP 最新快照（复用 hcp_features 逻辑）
    hcp_raw = load_hcp_data(conn)
    hcp_latest = get_latest_snapshot(hcp_raw)
    hcp_normalized = normalize_probabilities(hcp_latest)

    # 从归一化的 hcp_draw 计算走水概率
    eps = 1e-10
    hcp_draw_prob = hcp_normalized['hcp_draw'].values / (
        hcp_normalized['hcp_win'] + hcp_normalized['hcp_draw'] + hcp_normalized['hcp_lose'] + eps
    ).values

    # 合并 WDL 和 HCP 数据
    merged = hcp_normalized.merge(
        wdl_latest[['history_match_id', 'wdl_draw']],
        on='history_match_id',
        how='left'
    )

    # 计算特征
    features = pd.DataFrame(index=merged.index)

    # WDL 平局赔率
    features['wdl_draw_odds'] = merged['wdl_draw'].fillna(3.5)  # 缺失用中位数填充

    # WDL 平局隐含概率（简单归一化，仅用 draw 赔率反推）
    features['wdl_draw_prob'] = 1.0 / np.clip(features['wdl_draw_odds'], eps, None)

    # 走水概率差异：|WDL draw prob - HCP draw prob|
    features['draw_divergence'] = np.abs(
        features['wdl_draw_prob'].values - hcp_draw_prob
    )

    # 使用 history_match_id 作为索引，后续与主特征合并
    features['history_match_id'] = merged['history_match_id'].values
    features = features.set_index('history_match_id')

    valid_count = features['wdl_draw_odds'].notna().sum()
    print(f"[WDL-DRAW] 走水专用特征: {len(features)} 场, WDL赔率覆盖: {valid_count} ({valid_count/len(features)*100:.1f}%)")

    return features


# ========================================
# 2. 球队近期状态特征 (12维)
# ========================================

def build_team_form_features(matches_meta: pd.DataFrame) -> pd.DataFrame:
    """
    从 matches 表计算每支球队近5场 lag 统计。

    严格赛前: 仅使用 match_date < 当前比赛日期的历史比赛。

    返回 DataFrame (index 与 matches_meta 相同, 12维特征):
        - home_recent_wins/draws/losses: 主队近5场胜/平/负场次
        - home_recent_goals_for/against: 主队近5场进/失球
        - home_recent_points: 主队近5场积分(胜3平1负0)
        - away_* (同上, 客队)
    """
    conn = sqlite3.connect(DB_PATH)

    # 加载所有带比分的比赛（按日期排序）
    query = """
        SELECT match_id, match_date, home_team, away_team, actual_score
        FROM matches
        WHERE actual_score IS NOT NULL AND actual_score != ''
        ORDER BY match_date
    """
    df_matches = pd.read_sql(query, conn)
    conn.close()

    def parse_score(s):
        try:
            s = str(s).strip()
            if '其它' in s or s == '':
                return None, None
            for sep in [':', '-']:
                if sep in s:
                    parts = s.split(sep)
                    return int(parts[0]), int(parts[1])
            return None, None
        except:
            return None, None

    # 预处理：解析所有比分
    records = []
    for _, row in df_matches.iterrows():
        hg, ag = parse_score(row['actual_score'])
        if hg is None:
            continue
        records.append({
            'date': row['match_date'],
            'home_team': row['home_team'],
            'away_team': row['away_team'],
            'home_goals': hg,
            'away_goals': ag,
        })
    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.sort_values('date').reset_index(drop=True)

    # 为每支球队构建历史记录索引
    # team_history[team] = list of (date, goals_for, goals_against, result)
    team_history = defaultdict(list)

    for _, row in df.iterrows():
        team_history[row['home_team']].append({
            'date': row['date'],
            'goals_for': row['home_goals'],
            'goals_against': row['away_goals'],
            'result': 'W' if row['home_goals'] > row['away_goals'] else ('D' if row['home_goals'] == row['away_goals'] else 'L')
        })
        team_history[row['away_team']].append({
            'date': row['date'],
            'goals_for': row['away_goals'],
            'goals_against': row['home_goals'],
            'result': 'W' if row['away_goals'] > row['home_goals'] else ('D' if row['away_goals'] == row['home_goals'] else 'L')
        })

    # 为每场比赛计算近5场统计（严格赛前）
    results = []
    for _, match in matches_meta.iterrows():
        match_date = pd.to_datetime(match['date'], errors='coerce')
        home = match['home_team']
        away = match['away_team']

        # 主队近5场（date < match_date）
        home_hist = [h for h in team_history.get(home, []) if h['date'] < match_date][-5:]
        # 客队近5场
        away_hist = [h for h in team_history.get(away, []) if h['date'] < match_date][-5:]

        # 主队统计
        if home_hist:
            home_wins = sum(1 for h in home_hist if h['result'] == 'W')
            home_draws = sum(1 for h in home_hist if h['result'] == 'D')
            home_losses = sum(1 for h in home_hist if h['result'] == 'L')
            home_gf = sum(h['goals_for'] for h in home_hist)
            home_ga = sum(h['goals_against'] for h in home_hist)
            home_pts = home_wins * 3 + home_draws
        else:
            home_wins = home_draws = home_losses = home_gf = home_ga = home_pts = 0

        # 客队统计
        if away_hist:
            away_wins = sum(1 for h in away_hist if h['result'] == 'W')
            away_draws = sum(1 for h in away_hist if h['result'] == 'D')
            away_losses = sum(1 for h in away_hist if h['result'] == 'L')
            away_gf = sum(h['goals_for'] for h in away_hist)
            away_ga = sum(h['goals_against'] for h in away_hist)
            away_pts = away_wins * 3 + away_draws
        else:
            away_wins = away_draws = away_losses = away_gf = away_ga = away_pts = 0

        results.append({
            'home_recent_wins': home_wins,
            'home_recent_draws': home_draws,
            'home_recent_losses': home_losses,
            'home_recent_goals_for': home_gf,
            'home_recent_goals_against': home_ga,
            'home_recent_points': home_pts,
            'away_recent_wins': away_wins,
            'away_recent_draws': away_draws,
            'away_recent_losses': away_losses,
            'away_recent_goals_for': away_gf,
            'away_recent_goals_against': away_ga,
            'away_recent_points': away_pts,
        })

    form_df = pd.DataFrame(results, index=matches_meta.index)

    # 归一化到5场
    for col in ['home_recent_wins', 'home_recent_draws', 'home_recent_losses',
                'away_recent_wins', 'away_recent_draws', 'away_recent_losses']:
        form_df[col] = form_df[col] / 5.0

    for col in ['home_recent_goals_for', 'home_recent_goals_against',
                'away_recent_goals_for', 'away_recent_goals_against']:
        form_df[col] = form_df[col] / 5.0

    form_df['home_recent_points'] = form_df['home_recent_points'] / 15.0  # 最大15分
    form_df['away_recent_points'] = form_df['away_recent_points'] / 15.0

    # 统计
    has_form = (form_df['home_recent_wins'] > 0) | (form_df['home_recent_draws'] > 0) | (form_df['home_recent_losses'] > 0)
    print(f"[FORM] 球队近期状态特征: {len(form_df)} 场, 有历史数据: {has_form.sum()} ({has_form.sum()/len(form_df)*100:.1f}%)")

    return form_df


# ========================================
# 3. 休息天数特征 (3维)
# ========================================

def build_rest_days_features(matches_meta: pd.DataFrame) -> pd.DataFrame:
    """
    计算两支球队从上一场比赛到本场比赛的休息天数。

    返回 DataFrame (3维):
        - rest_days_home: 主队休息天数
        - rest_days_away: 客队休息天数
        - rest_days_diff: 休息天数差 (home - away)
    """
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT match_date, home_team, away_team
        FROM matches
        WHERE actual_score IS NOT NULL AND actual_score != ''
        ORDER BY match_date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    df['date'] = pd.to_datetime(df['match_date'], errors='coerce')
    df = df.sort_values('date').reset_index(drop=True)

    # 构建每支球队的比赛日期列表
    team_dates = defaultdict(list)
    for _, row in df.iterrows():
        if pd.notna(row['date']):
            team_dates[row['home_team']].append(row['date'])
            team_dates[row['away_team']].append(row['date'])

    results = []
    for _, match in matches_meta.iterrows():
        match_date = pd.to_datetime(match['date'], errors='coerce')
        home = match['home_team']
        away = match['away_team']

        # 主队上次比赛日期
        home_prev = [d for d in team_dates.get(home, []) if d < match_date]
        home_rest = (match_date - home_prev[-1]).days if home_prev else 7  # 默认7天

        # 客队上次比赛日期
        away_prev = [d for d in team_dates.get(away, []) if d < match_date]
        away_rest = (match_date - away_prev[-1]).days if away_prev else 7

        results.append({
            'rest_days_home': min(home_rest, 21),  # cap at 21 days
            'rest_days_away': min(away_rest, 21),
            'rest_days_diff': min(home_rest, 21) - min(away_rest, 21),
        })

    rest_df = pd.DataFrame(results, index=matches_meta.index)

    # 归一化
    rest_df['rest_days_home'] = rest_df['rest_days_home'] / 7.0
    rest_df['rest_days_away'] = rest_df['rest_days_away'] / 7.0
    rest_df['rest_days_diff'] = rest_df['rest_days_diff'] / 7.0

    print(f"[REST] 休息天数特征: {len(rest_df)} 场")
    return rest_df


# ========================================
# 4. 红黄牌风险特征 (4维) — 停赛风险代理
# ========================================

def build_card_risk_features(matches_meta: pd.DataFrame) -> pd.DataFrame:
    """
    从 match_player_stats 提取每支球队近5场的红黄牌数（lag版本）。

    返回 DataFrame (4维):
        - home_yellow_cards_l5: 主队近5场黄牌总数
        - home_red_cards_l5: 主队近5场红牌总数
        - away_yellow_cards_l5: 客队近5场黄牌总数
        - away_red_cards_l5: 客队近5场红牌总数
    """
    conn = sqlite3.connect(DB_PATH)

    # 检查 match_player_stats 是否有红黄牌数据
    try:
        query = """
            SELECT mps.match_id, mps.team, mps.yellow_cards, mps.red_cards,
                   mt.match_date
            FROM match_player_stats mps
            INNER JOIN match_id_mapping mim ON mps.match_id = mim.sh_match_id
            INNER JOIN matches mt ON mim.matches_match_id = mt.match_id
            WHERE mps.yellow_cards IS NOT NULL
              AND mt.match_date IS NOT NULL
            ORDER BY mt.match_date
        """
        df = pd.read_sql(query, conn)
        conn.close()
    except Exception as e:
        print(f"[CARDS] 无法加载红黄牌数据: {e}")
        # 返回默认值
        return pd.DataFrame({
            'home_yellow_cards_l5': 0.0,
            'home_red_cards_l5': 0.0,
            'away_yellow_cards_l5': 0.0,
            'away_red_cards_l5': 0.0,
        }, index=matches_meta.index)

    if len(df) == 0:
        print(f"[CARDS] 无红黄牌数据，使用默认值")
        return pd.DataFrame({
            'home_yellow_cards_l5': 0.0,
            'home_red_cards_l5': 0.0,
            'away_yellow_cards_l5': 0.0,
            'away_red_cards_l5': 0.0,
        }, index=matches_meta.index)

    df['date'] = pd.to_datetime(df['match_date'], errors='coerce')

    # 按球队+日期聚合每场比赛的红黄牌总数
    team_match_cards = df.groupby(['team', 'date']).agg({
        'yellow_cards': 'sum',
        'red_cards': 'sum',
    }).reset_index()

    # 构建每支球队的历史红黄牌记录
    team_card_history = defaultdict(list)
    for _, row in team_match_cards.iterrows():
        team_card_history[row['team']].append({
            'date': row['date'],
            'yellow': row['yellow_cards'],
            'red': row['red_cards'],
        })

    # 为每场比赛计算近5场红黄牌统计
    results = []
    for _, match in matches_meta.iterrows():
        match_date = pd.to_datetime(match['date'], errors='coerce')
        home = match['home_team']
        away = match['away_team']

        # 主队近5场
        home_cards = [c for c in team_card_history.get(home, []) if c['date'] < match_date][-5:]
        home_y = sum(c['yellow'] for c in home_cards) if home_cards else 0
        home_r = sum(c['red'] for c in home_cards) if home_cards else 0

        # 客队近5场
        away_cards = [c for c in team_card_history.get(away, []) if c['date'] < match_date][-5:]
        away_y = sum(c['yellow'] for c in away_cards) if away_cards else 0
        away_r = sum(c['red'] for c in away_cards) if away_cards else 0

        results.append({
            'home_yellow_cards_l5': home_y / 5.0,
            'home_red_cards_l5': home_r / 5.0,
            'away_yellow_cards_l5': away_y / 5.0,
            'away_red_cards_l5': away_r / 5.0,
        })

    card_df = pd.DataFrame(results, index=matches_meta.index)

    has_data = (card_df['home_yellow_cards_l5'] > 0) | (card_df['away_yellow_cards_l5'] > 0)
    print(f"[CARDS] 红黄牌风险特征: {len(card_df)} 场, 有数据: {has_data.sum()} ({has_data.sum()/len(card_df)*100:.1f}%)")

    return card_df


# ========================================
# 5. 一站式构建所有特征
# ========================================

def build_all_features_v2(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    一站式构建 T-005 v2 增强版特征（71维）。

    特征组成:
        - HCP 赔率特征 (15维): 来自 hcp_features.py
        - 走水专用特征 (3维): WDL draw vs HCP draw 对比
        - 球队近期状态 (12维): 近5场 lag 胜负/进球
        - 休息天数 (3维): 两队休息天数差
        - 红黄牌风险 (4维): 近5场 lag 红黄牌
        - 对手调整 Lag 特征 (24维): A实力分层+B盘口类别+C H2H+D市场信号 (方向A新增)
        - Elo 特征 (10维): 来自 elo_rating.py（在 train 脚本中添加）

    返回:
        features DataFrame (index=matches_match_id, 71维特征 + metadata)
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    print("=" * 60)
    print("🔧 T-005 v2 增强版特征工程")
    print("=" * 60)
    logger.info("==== build_all_features_v2 启动 | DB_PATH=%s | close_conn=%s ====", DB_PATH, close_conn)

    # Step 1: 原始 HCP 特征 (15维)
    print("\n[Step 1] 构建 HCP 赔率特征 (15维)...")
    features = build_hcp_features(conn)
    logger.info("[Step 1] HCP 特征构建完成 | matches=%d | 列数=%d | index名称=%s",
                len(features), features.shape[1], features.index.name)

    # 保存 metadata 列
    meta_cols = ['actual_handicap', 'handicap_line', 'actual_score', 'league', 'date', 'home_team', 'away_team']
    meta_data = features[meta_cols].copy()

    # Step 2: 走水专用特征 (3维)
    print("\n[Step 2] 构建走水专用特征 (3维)...")
    wdl_draw_feats = build_wdl_draw_features(conn)
    logger.info("[Step 2] wdl_draw_feats 原始行数=%d | index名称=%s", len(wdl_draw_feats), wdl_draw_feats.index.name)

    # 合并到主特征（通过 history_match_id）
    features['history_match_id'] = features.index  # features 的 index 是 matches_match_id，但需要 history_match_id 来关联
    # 注意：features 的 index 是 matches_match_id，但 wdl_draw_feats 的 index 是 history_match_id
    # 需要通过 match_id_mapping 来关联

    # 重新构建关联：从 hcp_features 的 load_hcp_data 获取 history_match_id -> matches_match_id 映射
    hcp_raw = load_hcp_data(conn)
    id_mapping = hcp_raw[['history_match_id', 'matches_match_id']].drop_duplicates(subset=['history_match_id'])
    id_mapping_dict = id_mapping.set_index('history_match_id')['matches_match_id'].to_dict()
    logger.info("[Step 2] history_match_id→matches_match_id 映射条数=%d", len(id_mapping_dict))

    # 为 wdl_draw_feats 添加 matches_match_id
    wdl_draw_feats['matches_match_id'] = wdl_draw_feats.index.map(lambda x: id_mapping_dict.get(x))
    n_wdl_mapped = wdl_draw_feats['matches_match_id'].notna().sum()
    n_wdl_lost = wdl_draw_feats['matches_match_id'].isna().sum()
    logger.info("[Step 2] wdl_draw_feats 映射成功=%d | 映射失败=%d (无 matches_match_id 对应)",
                n_wdl_mapped, n_wdl_lost)
    wdl_draw_feats = wdl_draw_feats.dropna(subset=['matches_match_id'])
    wdl_draw_feats = wdl_draw_feats.set_index('matches_match_id')

    # 合并
    for col in ['wdl_draw_odds', 'wdl_draw_prob', 'draw_divergence']:
        features[col] = features.index.map(lambda x: wdl_draw_feats.loc[x, col] if x in wdl_draw_feats.index else np.nan)
        features[col] = pd.to_numeric(features[col], errors='coerce')

    # 填充缺失
    for col in ['wdl_draw_odds', 'wdl_draw_prob', 'draw_divergence']:
        features[col] = features[col].fillna(features[col].median())

    # Step 3: 球队近期状态特征 (12维)
    print("\n[Step 3] 构建球队近期状态特征 (12维)...")
    form_feats = build_team_form_features(meta_data)
    for col in form_feats.columns:
        features[col] = form_feats[col].values

    # Step 4: 休息天数特征 (3维)
    print("\n[Step 4] 构建休息天数特征 (3维)...")
    rest_feats = build_rest_days_features(meta_data)
    for col in rest_feats.columns:
        features[col] = rest_feats[col].values

    # Step 5: 红黄牌风险特征 (4维)
    print("\n[Step 5] 构建红黄牌风险特征 (4维)...")
    card_feats = build_card_risk_features(meta_data)
    for col in card_feats.columns:
        features[col] = card_feats[col].values

    # Step 6: 对手调整 Lag 特征 (24维) - 方向A新增
    print("\n[Step 6] 构建对手调整 Lag 特征 (24维)...")
    logger.info("[Step 6] ==== 对手Lag特征合并开始 ====")
    logger.info("[Step 6] 主表 features | matches=%d | index前5=%s", len(features), list(features.index[:5]))
    try:
        from hcp_opponent_lag_features import build_all_opponent_lag_features, OPP_LAG_FEATURE_GROUPS
        opp_feats = build_all_opponent_lag_features(debug_sample=0)
        logger.info("[Step 6] opp_feats 构建完成 | 行数=%d | 列数=%d | index名称=%s",
                    len(opp_feats), opp_feats.shape[1], opp_feats.index.name)
        logger.info("[Step 6] opp_feats index前5=%s", list(opp_feats.index[:5]))
        logger.info("[Step 6] opp_feats 缺失24维特征: %s",
                    [c for c in V2_FEATURE_GROUPS['opponent_lag'] if c not in opp_feats.columns])

        # 主表 index vs opp_feats index 的 overlap（排查对齐最关键指标）
        main_ids = set(features.index)
        opp_ids = set(opp_feats.index)
        overlap = main_ids & opp_ids
        only_main = main_ids - opp_ids
        only_opp = opp_ids - main_ids
        logger.info("[Step 6] match_id overlap | 共有=%d | 仅主表=%d | 仅opp=%d | 主表覆盖率=%.2f%%",
                    len(overlap), len(only_main), len(only_opp),
                    100.0 * len(overlap) / max(len(main_ids), 1))
        if only_main and len(only_main) <= 10:
            logger.warning("[Step 6] 主表中但opp缺失的 match_id (前10): %s", list(sorted(only_main))[:10])
        if only_opp and len(only_opp) <= 10:
            logger.warning("[Step 6] opp中但主表缺失的 match_id (前10): %s", list(sorted(only_opp))[:10])

        n_opp_merged = 0
        missing_cols_after_merge = []
        for col in V2_FEATURE_GROUPS['opponent_lag']:
            if col in opp_feats.columns:
                features[col] = features.index.map(
                    lambda mid: opp_feats.loc[mid, col] if mid in opp_feats.index else np.nan
                )
                features[col] = pd.to_numeric(features[col], errors='coerce')
                n_opp_merged += 1
                # 记录每列的命中率与缺失数
                n_na = features[col].isna().sum()
                if n_na > 0:
                    missing_cols_after_merge.append((col, int(n_na)))
        logger.info("[Step 6] 24维特征合并完成 | 成功合并列数=%d/24", n_opp_merged)
        all_match = features[V2_FEATURE_GROUPS['opponent_lag']].notna().all(axis=1).sum()
        logger.info("[Step 6] 全24维非缺失的样本数=%d/%d (%.2f%%)",
                    all_match, len(features), 100.0 * all_match / max(len(features), 1))
        if missing_cols_after_merge:
            logger.warning("[Step 6] 合并后存在缺失的列 | %s", missing_cols_after_merge[:8])

        # 用中位数填充缺失
        n_filled_total = 0
        for col in V2_FEATURE_GROUPS['opponent_lag']:
            if col in features.columns:
                n_na_before = int(features[col].isna().sum())
                med = features[col].median()
                if pd.notna(med):
                    features[col] = features[col].fillna(med)
                    n_filled_total += n_na_before
        logger.info("[Step 6] 中位数填充完成 | 累计填充缺失单元格=%d", n_filled_total)
        logger.info("[Step 6] ==== 对手Lag特征合并结束 ====")
    except Exception as e:
        logger.error("[Step 6] 对手调整 Lag 特征加载失败: %s", e, exc_info=True)
        print(f"  ⚠️ 对手调整 Lag 特征加载失败: {e}")
        print(f"     请确保 hcp_opponent_lag_features.py 可正常运行")
        import traceback
        traceback.print_exc()

    # 恢复 metadata 列
    for col in meta_cols:
        features[col] = meta_data[col].values

    # 统计（直接使用 V2_FEATURE_GROUPS 精确匹配，避免前缀/后缀模糊匹配导致的维度误算）
    hcp_cols = [c for c in V2_FEATURE_GROUPS['hcp'] if c in features.columns]
    wdl_cols = [c for c in V2_FEATURE_GROUPS['wdl_draw'] if c in features.columns]
    form_cols = [c for c in V2_FEATURE_GROUPS['form'] if c in features.columns]
    rest_cols = [c for c in V2_FEATURE_GROUPS['rest'] if c in features.columns]
    card_cols = [c for c in V2_FEATURE_GROUPS['cards'] if c in features.columns]
    opp_lag_cols = [c for c in V2_FEATURE_GROUPS['opponent_lag'] if c in features.columns]

    print(f"\n{'='*60}")
    print(f"✅ T-005 v2 特征构建完成")
    print(f"{'='*60}")
    print(f"  HCP 赔率特征: {len(hcp_cols)} 维")
    print(f"  走水专用特征: {len(wdl_cols)} 维")
    print(f"  球队近期状态: {len(form_cols)} 维")
    print(f"  休息天数: {len(rest_cols)} 维")
    print(f"  红黄牌风险: {len(card_cols)} 维")
    print(f"  对手调整 Lag: {len(opp_lag_cols)} 维")
    print(f"  Elo 特征: 10 维 (训练时添加)")
    print(f"  ────────────────────────")
    total_dim = len(hcp_cols) + len(wdl_cols) + len(form_cols) + len(rest_cols) + len(card_cols) + len(opp_lag_cols) + 10
    print(f"  总计: {total_dim} 维 (不含 metadata)")
    print(f"  总比赛数: {len(features)}")
    logger.info("==== build_all_features_v2 完成 | matches=%d | HCP=%d wdl=%d form=%d rest=%d card=%d opp_lag=%d elo=10 | 总维度=%d ====",
                len(features), len(hcp_cols), len(wdl_cols), len(form_cols),
                len(rest_cols), len(card_cols), len(opp_lag_cols), total_dim)

    if close_conn:
        conn.close()

    return features


# ========================================
# 特征组定义（用于训练脚本）
# ========================================

V2_FEATURE_GROUPS = {
    'hcp': [  # 15维 - 原始赔率特征
        'hcp_prob_win', 'hcp_prob_draw', 'hcp_prob_lose',
        'hcp_home_strength', 'hcp_draw_risk', 'hcp_confidence',
        'hcp_entropy', 'hcp_expected_value', 'hcp_volatility',
        'hcp_market_sentiment', 'hcp_underdog_ratio', 'hcp_favorite_margin',
        'hcp_balance', 'hcp_upset_risk', 'hcp_odds_skew',
    ],
    'wdl_draw': [  # 3维 - 走水专用
        'wdl_draw_odds', 'wdl_draw_prob', 'draw_divergence',
    ],
    'form': [  # 12维 - 球队近期状态
        'home_recent_wins', 'home_recent_draws', 'home_recent_losses',
        'home_recent_goals_for', 'home_recent_goals_against', 'home_recent_points',
        'away_recent_wins', 'away_recent_draws', 'away_recent_losses',
        'away_recent_goals_for', 'away_recent_goals_against', 'away_recent_points',
    ],
    'rest': [  # 3维 - 休息天数
        'rest_days_home', 'rest_days_away', 'rest_days_diff',
    ],
    'cards': [  # 4维 - 红黄牌风险
        'home_yellow_cards_l5', 'home_red_cards_l5',
        'away_yellow_cards_l5', 'away_red_cards_l5',
    ],
    'opponent_lag': [  # 24维 - 对手调整 Lag 特征 (方向A新增)
        # 特征组 A: 对手实力分层让球走水率 (8维)
        'home_hcp_draw_vs_stronger_l5', 'home_hcp_draw_vs_similar_l5',
        'home_hcp_draw_vs_weaker_l5', 'home_hcp_draw_vs_all_l10',
        'away_hcp_draw_vs_stronger_l5', 'away_hcp_draw_vs_similar_l5',
        'away_hcp_draw_vs_weaker_l5', 'away_hcp_draw_vs_all_l10',
        # 特征组 B: 盘口线类别专属 Lag (6维)
        'home_hcp_draw_at_give1_l10', 'home_hcp_draw_at_get1_l10', 'home_hcp_draw_at_give2_l10',
        'away_hcp_draw_at_give1_l10', 'away_hcp_draw_at_get1_l10', 'away_hcp_draw_at_give2_l10',
        # 特征组 C: 直接交锋（H2H）让球历史 (6维)
        'h2h_hcp_draw_rate', 'h2h_hcp_draw_count', 'h2h_total_matches',
        'h2h_last5_hcp_draws', 'home_h2h_hcp_draw_rate', 'away_h2h_hcp_draw_rate',
        # 特征组 D: 市场信号增强 (4维)
        'elo_gap_abs', 'hcp_draw_prob_rank',
        'opponent_season_draw_rate', 'market_draw_std',
    ],
    # elo: 10维 - 在训练脚本中动态添加
}

V2_ALL_FEATURES = (
    V2_FEATURE_GROUPS['hcp'] +
    V2_FEATURE_GROUPS['wdl_draw'] +
    V2_FEATURE_GROUPS['form'] +
    V2_FEATURE_GROUPS['rest'] +
    V2_FEATURE_GROUPS['cards'] +
    V2_FEATURE_GROUPS['opponent_lag']
)


if __name__ == "__main__":
    features = build_all_features_v2()
    print(f"\n特征列名: {V2_ALL_FEATURES}")
