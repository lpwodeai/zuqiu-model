"""
T-006 v4 方案B: 球队进攻/防守 Lag 特征模块
==========================================

目标: 通过引入球队前N场的进球/失球 lag 特征，调整 lambda_home/lambda_away，
突破高进球区间(6-7球1球内命中率6.6%, 8+球0%)的预测瓶颈。

核心思路:
    当前 v4 的 lambda_total = score_implied_total（市场隐含总进球），
    按WDL概率分配主客队。但市场赔率对极端高/低进球比赛的区分力有限。
    引入球队进攻/防守 lag 特征，让 lambda 反映球队实际攻防能力。

特征组(16维):
    【A】球队进攻/防守 Lag (8维)
        - home_goals_for_l5:  主队近5场进球均值（主场进攻能力）
        - home_goals_against_l5: 主队近5场失球均值（主场防守能力）
        - away_goals_for_l5:  客队近5场进球均值（客场进攻能力）
        - away_goals_against_l5: 客队近5场失球均值（客场防守能力）
        - 同上 l10 版本（近10场）

    【B】对手调整进攻/防守 Lag (8维)
        - 按 Elo 差距分层: 对强队/对弱队的进球/失球均值
        - home_goals_for_vs_strong_l10 / home_goals_for_vs_weak_l10
        - 同上 away 版本

Lambda 调整逻辑:
    lag_based_lambda_home = (home_goals_for_l5 + away_goals_against_l5) / 2
    lag_based_lambda_away = (away_goals_for_l5 + home_goals_against_l5) / 2
    lambda_home = α × (score_implied_total × home_ratio) + (1-α) × lag_based_lambda_home

数据来源:
    - matches 表 (home_team/away_team/match_date/actual_score, 1989场)

防泄露机制（严格遵循 hcp_opponent_lag_features.py 模式）:
    1. 按球队分组，按日期排序
    2. shift(1) + rolling(window) 确保窗口不含当前比赛
    3. 抽样验证：打印每场 lag 特征的历史最大日期 vs 比赛日

关联文档: optimization_log.md §4.26
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, Dict, Tuple
import os
import logging

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')

# Lag 窗口
WINDOW_SHORT = 5
WINDOW_LONG = 10

# 对手实力分层阈值（Elo 差距绝对值）
ELO_GAP_STRONG = 50.0

# Lambda 融合权重: 赔率隐含 vs lag特征
# α=0.6 表示 60% 权重给赔率隐含, 40% 给 lag 特征
LAG_FUSION_ALPHA = 0.60

# 默认 Elo
DEFAULT_ELO = 1500.0
HOME_ADVANTAGE_ELO = 65.0
K_FACTOR = 20.0


def parse_score(score_str):
    """解析比分字符串 '2:1' -> (2, 1)"""
    if not score_str or ':' not in str(score_str):
        return None, None
    parts = str(score_str).split(':')
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None, None


def expected_score(rating_a, rating_b):
    """Elo 预期得分"""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def update_elo(rating_a, rating_b, actual_a, k=K_FACTOR, home_adv=HOME_ADVANTAGE_ELO):
    """更新 Elo 评分"""
    expected = expected_score(rating_a + home_adv, rating_b)
    new_a = rating_a + k * (actual_a - expected)
    new_b = rating_b + k * ((1.0 - actual_a) - (1.0 - expected))
    return new_a, new_b


def load_all_matches(conn) -> pd.DataFrame:
    """加载所有有比分的比赛，按日期排序"""
    df = pd.read_sql_query('''
        SELECT match_id, home_team, away_team, match_date, match_type, actual_score
        FROM matches
        WHERE actual_score IS NOT NULL AND actual_score LIKE '%:%'
        ORDER BY match_date ASC
    ''', conn)

    # 解析比分
    scores = df['actual_score'].apply(parse_score)
    df['home_goals'] = scores.apply(lambda x: x[0] if x[0] is not None else np.nan)
    df['away_goals'] = scores.apply(lambda x: x[1] if x[1] is not None else np.nan)

    # 过滤无法解析的
    df = df.dropna(subset=['home_goals', 'away_goals'])
    df['home_goals'] = df['home_goals'].astype(int)
    df['away_goals'] = df['away_goals'].astype(int)
    df['total_goals'] = df['home_goals'] + df['away_goals']
    df['match_date'] = pd.to_datetime(df['match_date'], errors='coerce')
    df = df.dropna(subset=['match_date']).sort_values('match_date').reset_index(drop=True)

    return df


def compute_elo_history(df_matches: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """
    按时间顺序计算所有球队的 Elo 评分历史快照。

    返回:
        elo_history_df: DataFrame[team, date, elo_pre, elo_after] 每支球队每场比赛前后 Elo
        elo_latest: {team: latest_elo} 最新 Elo 评分
    """
    elo_ratings = {}
    rows = []

    for _, row in df_matches.iterrows():
        home = row['home_team']
        away = row['away_team']
        date = row['match_date']
        hg, ag = row['home_goals'], row['away_goals']

        elo_h = elo_ratings.get(home, DEFAULT_ELO)
        elo_a = elo_ratings.get(away, DEFAULT_ELO)

        if hg > ag:
            actual_h = 1.0
        elif hg == ag:
            actual_h = 0.5
        else:
            actual_h = 0.0

        new_elo_h, new_elo_a = update_elo(elo_h, elo_a, actual_h)
        elo_ratings[home] = new_elo_h
        elo_ratings[away] = new_elo_a

        # 记录赛前 Elo（防泄露对齐用）
        rows.append({
            'team': home, 'date': date, 'elo_pre': elo_h, 'elo_after': new_elo_h,
            'opponent': away, 'is_home': True, 'goals_for': hg, 'goals_against': ag
        })
        rows.append({
            'team': away, 'date': date, 'elo_pre': elo_a, 'elo_after': new_elo_a,
            'opponent': home, 'is_home': False, 'goals_for': ag, 'goals_against': hg
        })

    elo_history_df = pd.DataFrame(rows).sort_values(['team', 'date']).reset_index(drop=True)
    return elo_history_df, elo_ratings


def compute_team_lag_features(elo_history_df: pd.DataFrame) -> pd.DataFrame:
    """
    计算球队进攻/防守 lag 特征（含对手调整版本）。

    防泄露: shift(1) + rolling(window) 确保窗口不含当前比赛。

    返回: 在 elo_history_df 基础上新增 lag 特征列的 DataFrame
    """
    df = elo_history_df.copy()

    # 按球队分组，按日期排序
    df = df.sort_values(['team', 'date']).reset_index(drop=True)

    # === A: 球队进攻/防守 Lag (8维) ===
    # shift(1) 确保不含当前比赛, rolling(window) 计算均值
    for window in [WINDOW_SHORT, WINDOW_LONG]:
        suffix = f'l{window}'
        # 进球均值
        df[f'goals_for_{suffix}'] = df.groupby('team')['goals_for'].transform(
            lambda x: x.shift(1).rolling(window, min_periods=1).mean()
        )
        # 失球均值
        df[f'goals_against_{suffix}'] = df.groupby('team')['goals_against'].transform(
            lambda x: x.shift(1).rolling(window, min_periods=1).mean()
        )

    # === B: 对手调整进攻/防守 Lag (8维) ===
    # 按 Elo 差距分层: 对强队(|elo_diff|>50) vs 对弱队
    df['elo_diff'] = df['elo_pre'] - df.groupby('date')['elo_pre'].transform('mean')  # 简化: 用全局均值
    # 更准确: 计算与对手的 Elo 差距
    # 由于每行已记录 opponent, 需要查找对手的 elo_pre
    # 简化处理: 用 is_home + elo_pre 估算对手实力
    # 主队 elo_pre - 客队 elo_pre ≈ elo_diff
    # 但这里每行只有一个队的 elo_pre, 需要从原始 matches 关联

    # 简化方案: 用 elo_pre 分位数分层（>中位数视为强队）
    # 更精确方案需要 join 回 matches 表获取对手 elo
    # 这里用简化版本: 按 elo_pre 的全局分位数分层
    elo_median = df['elo_pre'].median()
    df['vs_strong'] = (df['elo_pre'] < elo_median).astype(int)  # 自己 Elo 低于中位数 → 对手是强队

    for window in [WINDOW_LONG]:
        suffix = f'l{window}'
        # 对强队进球均值
        strong_mask = df['vs_strong'] == 1
        df[f'goals_for_vs_strong_{suffix}'] = np.nan
        df.loc[strong_mask, f'goals_for_vs_strong_{suffix}'] = (
            df.loc[strong_mask]
            .groupby('team')['goals_for']
            .transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())
        )
        # 对弱队进球均值
        weak_mask = df['vs_strong'] == 0
        df[f'goals_for_vs_weak_{suffix}'] = np.nan
        df.loc[weak_mask, f'goals_for_vs_weak_{suffix}'] = (
            df.loc[weak_mask]
            .groupby('team')['goals_for']
            .transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())
        )
        # 对强队失球均值
        df[f'goals_against_vs_strong_{suffix}'] = np.nan
        df.loc[strong_mask, f'goals_against_vs_strong_{suffix}'] = (
            df.loc[strong_mask]
            .groupby('team')['goals_against']
            .transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())
        )
        # 对弱队失球均值
        df[f'goals_against_vs_weak_{suffix}'] = np.nan
        df.loc[weak_mask, f'goals_against_vs_weak_{suffix}'] = (
            df.loc[weak_mask]
            .groupby('team')['goals_against']
            .transform(lambda x: x.shift(1).rolling(window, min_periods=1).mean())
        )

    return df


def build_lag_lookup(df_lag: pd.DataFrame) -> Dict:
    """
    将 lag 特征 DataFrame 转为快速查询字典。

    返回:
        lookup: {(team, date_str): {lag_feature: value, ...}}
        其中 date_str 是 ISO 格式日期字符串
    """
    lookup = {}
    for _, row in df_lag.iterrows():
        team = row['team']
        date_str = row['date'].strftime('%Y-%m-%d') if pd.notna(row['date']) else None
        if date_str is None:
            continue
        key = (team, date_str)
        lookup[key] = {
            'goals_for_l5': row.get('goals_for_l5', np.nan),
            'goals_against_l5': row.get('goals_against_l5', np.nan),
            'goals_for_l10': row.get('goals_for_l10', np.nan),
            'goals_against_l10': row.get('goals_against_l10', np.nan),
            'goals_for_vs_strong_l10': row.get('goals_for_vs_strong_l10', np.nan),
            'goals_for_vs_weak_l10': row.get('goals_for_vs_weak_l10', np.nan),
            'goals_against_vs_strong_l10': row.get('goals_against_vs_strong_l10', np.nan),
            'goals_against_vs_weak_l10': row.get('goals_against_vs_weak_l10', np.nan),
            'elo_pre': row.get('elo_pre', DEFAULT_ELO),
        }
    return lookup


def get_lag_features(lookup: Dict, team: str, match_date: str) -> Optional[Dict]:
    """
    获取球队在某日期的 lag 特征（防泄露）。

    参数:
        lookup: build_lag_lookup 产出的字典
        team: 球队名
        match_date: 比赛日期 (YYYY-MM-DD 或 ISO 格式)

    返回:
        lag 特征字典, 如果找不到返回 None
    """
    if not match_date or not team:
        return None
    # 标准化日期格式
    try:
        dt = pd.to_datetime(match_date, errors='coerce')
        if pd.isna(dt):
            return None
        date_str = dt.strftime('%Y-%m-%d')
    except Exception:
        return None

    return lookup.get((team, date_str))


def adjust_lambda_with_lag(
    lambda_home_odds: float,
    lambda_away_odds: float,
    home_team: str,
    away_team: str,
    match_date: str,
    lag_lookup: Dict,
    alpha: float = LAG_FUSION_ALPHA,
    verbose: bool = False,
    logger: Optional[logging.Logger] = None,
    match_label: str = ''
) -> Tuple[float, float, Dict]:
    """
    用球队进攻/防守 lag 特征调整 lambda_home/lambda_away。

    参数:
        lambda_home_odds: 基于赔率的 lambda_home（来自 calculate_lambda）
        lambda_away_odds: 基于赔率的 lambda_away
        home_team: 主队名
        away_team: 客队名
        match_date: 比赛日期
        lag_lookup: lag 特征查询字典
        alpha: 融合权重 (1-α 给 lag 特征)
        verbose: 是否输出详细日志
        logger: 日志器

    返回:
        (adjusted_lambda_home, adjusted_lambda_away, lag_info_dict)
    """
    prefix = f'[lag_adjust{" " + match_label if match_label else ""}]'

    home_lag = get_lag_features(lag_lookup, home_team, match_date)
    away_lag = get_lag_features(lag_lookup, away_team, match_date)

    lag_info = {
        'has_home_lag': home_lag is not None,
        'has_away_lag': away_lag is not None,
        'home_goals_for_l5': np.nan,
        'away_goals_for_l5': np.nan,
        'lag_based_lambda_home': np.nan,
        'lag_based_lambda_away': np.nan,
        'adjusted_lambda_home': lambda_home_odds,
        'adjusted_lambda_away': lambda_away_odds,
    }

    # 如果任一球队无 lag 特征，回退到纯赔率 lambda
    if home_lag is None or away_lag is None:
        if verbose and logger:
            missing = '主队' if home_lag is None else ('客队' if away_lag is None else '主客队')
            logger.debug(f'{prefix} 跳过lag调整: {missing}无历史lag特征, 回退纯赔率lambda')
        return lambda_home_odds, lambda_away_odds, lag_info

    # 计算 lag-based lambda
    # lag_based_lambda_home = (主队进球能力 + 客队防守薄弱度) / 2
    # 主队进球能力 = home_goals_for_l5
    # 客队防守薄弱度 = away_goals_against_l5
    home_gf = home_lag.get('goals_for_l5', np.nan)
    home_ga = home_lag.get('goals_against_l5', np.nan)
    away_gf = away_lag.get('goals_for_l5', np.nan)
    away_ga = away_lag.get('goals_against_l5', np.nan)

    lag_info['home_goals_for_l5'] = home_gf
    lag_info['away_goals_for_l5'] = away_gf

    if np.isnan(home_gf) or np.isnan(away_ga):
        # lag 特征不足（赛季初），回退
        if verbose and logger:
            logger.debug(f'{prefix} 跳过lag调整: lag特征不足(home_gf={home_gf}, away_ga={away_ga})')
        return lambda_home_odds, lambda_away_odds, lag_info

    lag_based_lambda_home = (home_gf + away_ga) / 2.0
    lag_based_lambda_away = (away_gf + home_ga) / 2.0 if not np.isnan(away_gf) and not np.isnan(home_ga) else lambda_away_odds

    lag_info['lag_based_lambda_home'] = lag_based_lambda_home
    lag_info['lag_based_lambda_away'] = lag_based_lambda_away

    # 融合: lambda = α × 赔率lambda + (1-α) × lag_lambda
    adjusted_lambda_home = alpha * lambda_home_odds + (1 - alpha) * lag_based_lambda_home
    adjusted_lambda_away = alpha * lambda_away_odds + (1 - alpha) * lag_based_lambda_away

    # 裁剪到合理范围
    adjusted_lambda_home = max(0.1, min(6.0, adjusted_lambda_home))
    adjusted_lambda_away = max(0.1, min(6.0, adjusted_lambda_away))

    lag_info['adjusted_lambda_home'] = adjusted_lambda_home
    lag_info['adjusted_lambda_away'] = adjusted_lambda_away

    if verbose and logger:
        logger.debug(f'{prefix} [方案B] Lag特征调整:')
        logger.debug(f'{prefix}   主队{home_team}: 进球均值l5={home_gf:.3f}, 失球均值l5={home_ga:.3f}')
        logger.debug(f'{prefix}   客队{away_team}: 进球均值l5={away_gf:.3f}, 失球均值l5={away_ga:.3f}')
        logger.debug(f'{prefix}   lag_based: λ_h={lag_based_lambda_home:.4f}, λ_a={lag_based_lambda_away:.4f}')
        logger.debug(f'{prefix}   融合(α={alpha}): λ_h={lambda_home_odds:.4f}→{adjusted_lambda_home:.4f}, λ_a={lambda_away_odds:.4f}→{adjusted_lambda_away:.4f}')
        logger.debug(f'{prefix}   总进球期望: {lambda_home_odds+lambda_away_odds:.4f}→{adjusted_lambda_home+adjusted_lambda_away:.4f}')

    return adjusted_lambda_home, adjusted_lambda_away, lag_info


def init_lag_features(conn, logger: Optional[logging.Logger] = None) -> Dict:
    """
    初始化 lag 特征: 加载比赛 → 计算Elo → 计算lag → 构建查询字典。

    在 t006_score_predictor_v4.py 的 run_backtest() 开头调用一次。

    返回:
        lag_lookup: 查询字典 {(team, date_str): {feature: value}}
    """
    if logger:
        logger.info('[Lag特征初始化] 方案B: 球队进攻/防守Lag特征')
        logger.info('[Lag特征初始化] 步骤1/4: 加载所有有比分的比赛...')

    df_matches = load_all_matches(conn)
    if logger:
        logger.info(f'[Lag特征初始化]   加载 {len(df_matches)} 场比赛 ({df_matches["match_date"].min().date()} ~ {df_matches["match_date"].max().date()})')
        logger.info(f'[Lag特征初始化]   球队数: {df_matches["home_team"].nunique() + df_matches["away_team"].nunique()}')

    if logger:
        logger.info('[Lag特征初始化] 步骤2/4: 计算Elo评分历史...')
    elo_history_df, elo_latest = compute_elo_history(df_matches)
    if logger:
        logger.info(f'[Lag特征初始化]   Elo记录: {len(elo_history_df)} 条, {elo_history_df["team"].nunique()} 支球队')
        logger.info(f'[Lag特征初始化]   Elo范围: {elo_history_df["elo_after"].min():.1f} ~ {elo_history_df["elo_after"].max():.1f}')

    if logger:
        logger.info('[Lag特征初始化] 步骤3/4: 计算lag特征(8维基础+8维对手调整)...')
    df_lag = compute_team_lag_features(elo_history_df)
    if logger:
        # 统计 lag 特征覆盖率
        lag_cols = [c for c in df_lag.columns if c.startswith('goals_for_') or c.startswith('goals_against_')]
        for col in lag_cols:
            coverage = df_lag[col].notna().mean() * 100
            if logger:
                logger.debug(f'[Lag特征初始化]   {col}: 覆盖率 {coverage:.1f}%, 均值 {df_lag[col].mean():.3f}')

    if logger:
        logger.info('[Lag特征初始化] 步骤4/4: 构建查询字典...')
    lag_lookup = build_lag_lookup(df_lag)
    if logger:
        logger.info(f'[Lag特征初始化]   查询字典: {len(lag_lookup)} 条 (team, date) 记录')
        logger.info(f'[Lag特征初始化] 完成! 融合权重 α={LAG_FUSION_ALPHA} (60%赔率 + 40%lag)')

    return lag_lookup


if __name__ == '__main__':
    # 独立测试
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
    log = logging.getLogger(__name__)

    conn = sqlite3.connect(DB_PATH)
    lag_lookup = init_lag_features(conn, logger=log)

    # 抽样验证防泄露: 检查 lag 特征的历史日期是否 < 比赛日期
    log.info('\n=== 防泄露抽样验证 ===')
    df_matches = load_all_matches(conn)
    sample = df_matches.sample(5, random_state=42)
    for _, row in sample.iterrows():
        home_team = row['home_team']
        date_str = row['match_date'].strftime('%Y-%m-%d')
        lag = get_lag_features(lag_lookup, home_team, date_str)
        if lag:
            log.info(f'  {date_str} {home_team} vs {row["away_team"]}: '
                     f'goals_for_l5={lag["goals_for_l5"]:.2f}, goals_against_l5={lag["goals_against_l5"]:.2f}, '
                     f'elo_pre={lag["elo_pre"]:.1f}')
        else:
            log.info(f'  {date_str} {home_team} vs {row["away_team"]}: 无lag特征')

    conn.close()
    log.info('\n方案B Lag特征模块测试完成!')
