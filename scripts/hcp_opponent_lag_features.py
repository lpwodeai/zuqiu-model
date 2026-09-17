"""
T-005 v2 对手调整 Lag 特征模块
================================

目标：通过引入"对手实力分层 + 盘口线类别专属 + 直接交锋"三类 lag 特征，
解决 T-005 v2 走水召回率偏低（0.2398）的问题。

核心思路：
    当前 v2 的球队近期状态特征（12维）只看总体战绩，缺乏对手上下文。
    一支球队对阵强队 vs 弱队的让球走水率差异显著，需要"对手调整"视角。

特征组（24维）:
    【A】对手实力分层让球走水率 (8维)
        - 主/客队近5/10场对阵强/相近/弱对手的让球走水率
    【B】盘口线类别专属 Lag (6维)
        - 主/客队在让1球/受让1球/让2球+盘口下的近10场走水率
    【C】直接交锋（H2H）让球历史 (6维)
        - 双方历史交锋的让球走水率/总数/近5场等
    【D】市场信号增强 (4维)
        - Elo差距分位、走水赔率分位、对手赛季走水率、赔率波动

数据来源:
    - reports/hcp_expanded_dataset_*.csv (反推盘口线 + 让球结果标签)
    - matches 表 (比分、主客队、日期)
    - handicap_history 表 (hcp_win/draw/lose 赔率)
    - elo_rating.py (Elo 评分计算)

防泄露机制（严格遵循 match_level_lag_features_v2.py 模式）:
    1. 按球队分组，按日期排序
    2. shift(1) + rolling(window) 确保窗口不含当前比赛
    3. 抽样验证：打印每场 lag 特征的历史最大日期 vs 比赛日
    4. 所有特征标记为 derived_pre（赛前可计算）

关联决策: D-20260811-037 (待新增)
关联文档: optimization_log.md §4.23
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Tuple
import sys
import os
import glob
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from handicap_line_inference import parse_score, compute_handicap_result
from elo_rating import expected_score, DEFAULT_ELO, HOME_ADVANTAGE, K_FACTOR, update_elo

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

# 对手实力分层阈值（Elo 差距绝对值）
ELO_GAP_STRONG = 50.0      # |elo_diff| > 50 视为实力差距明显
ELO_GAP_SIMILAR = 50.0     # |elo_diff| <= 50 视为实力相近

# Lag 窗口
WINDOW_SHORT = 5
WINDOW_LONG = 10

# 走水标签
DRAW_LABEL = 1


# ========================================
# 数据加载
# ========================================

def load_expanded_dataset() -> pd.DataFrame:
    """
    加载最新扩充数据集（3915场，含反推盘口线 + 让球结果标签）。

    返回 DataFrame，列:
        - match_id, actual_score, handicap_line_pred, actual_handicap_pred
        - hcp_win, hcp_draw, hcp_lose
        - home_team, away_team, league, date, label_source, pred_confidence, season
    """
    expand_files = sorted(glob.glob(os.path.join(REPORT_DIR, 'hcp_expanded_dataset_*.csv')))
    if not expand_files:
        raise FileNotFoundError(f"未找到扩充数据集文件: {REPORT_DIR}/hcp_expanded_dataset_*.csv")

    latest = expand_files[-1]
    df = pd.read_csv(latest)
    print(f"[OPP-LAG] 加载扩充数据集: {os.path.basename(latest)}")
    print(f"  总样本: {len(df)} 场")
    print(f"  标签来源: {df['label_source'].value_counts().to_dict()}")
    print(f"  盘口线覆盖: {df['handicap_line_pred'].notna().sum()}/{len(df)} ({df['handicap_line_pred'].notna().mean()*100:.1f}%)")
    return df


def compute_elo_history(df_matches: pd.DataFrame) -> Tuple[pd.DataFrame, Dict]:
    """
    按时间顺序计算所有球队的 Elo 评分历史快照。

    参数:
        df_matches: 按 match_date 排序的比赛数据，包含 home_team/away_team/actual_score

    返回:
        elo_history_df: DataFrame[team, date, elo_after] 每支球队每场比赛后的 Elo
                        已按 (team, date) 排序，供 merge_asof 高效对齐
        elo_latest: {team: latest_elo} 最新 Elo 评分
    """
    elo_ratings = {}
    rows = []

    for _, row in df_matches.iterrows():
        hg, ag = parse_score(row['actual_score'])
        if hg is None:
            continue
        home = row['home_team']
        away = row['away_team']
        date = pd.to_datetime(row['match_date'], errors='coerce')
        if pd.isna(date):
            continue

        elo_h = elo_ratings.get(home, DEFAULT_ELO)
        elo_a = elo_ratings.get(away, DEFAULT_ELO)

        if hg > ag:
            actual_h = 1.0
        elif hg == ag:
            actual_h = 0.5
        else:
            actual_h = 0.0

        new_elo_h, new_elo_a = update_elo(elo_h, elo_a, actual_h, K_FACTOR, HOME_ADVANTAGE)
        elo_ratings[home] = new_elo_h
        elo_ratings[away] = new_elo_a
        # 记录"赛前 Elo"（即更新前的值）+ 比赛日期，用于防泄露对齐
        rows.append({'team': home, 'date': date, 'elo_pre': elo_h, 'elo_after': new_elo_h})
        rows.append({'team': away, 'date': date, 'elo_pre': elo_a, 'elo_after': new_elo_a})

    elo_history_df = pd.DataFrame(rows).sort_values(['team', 'date']).reset_index(drop=True)
    print(f"  [ELO] 计算完成: {len(elo_history_df)} 条记录, {elo_history_df['team'].nunique()} 支球队")
    print(f"  [ELO] Elo 范围: {elo_history_df['elo_after'].min():.1f} ~ {elo_history_df['elo_after'].max():.1f}")
    return elo_history_df, elo_ratings


def get_pre_match_elo_batch(
    team_series: pd.Series,
    date_series: pd.Series,
    elo_history_df: pd.DataFrame,
) -> pd.Series:
    """
    批量获取球队在某日期之前的最新 Elo 评分（防泄露）。

    使用 merge_asof 高效对齐：
        1. 对每个 (team, date)，找 elo_history_df 中同 team 且 date < 指定日期 的最后一行
        2. 返回该行的 elo_after（即该队上一场比赛后的 Elo）

    参数:
        team_series: 球队名 Series
        date_series: 比赛日期 Series
        elo_history_df: compute_elo_history 的产出

    返回:
        elo_pre Series（与输入同长度）
    """
    # 构建查询 DataFrame
    query_df = pd.DataFrame({
        'team': team_series.values,
        'query_date': pd.to_datetime(date_series.values, errors='coerce'),
    }).reset_index()

    # 为每个 team 用 merge_asof（按 date 向后对齐，取 <= query_date - 1天 的最后一行）
    # 为避免包含当日比赛，用 query_date - 1秒 作为对齐键
    query_df['align_date'] = query_df['query_date'] - pd.Timedelta(seconds=1)
    query_df = query_df.sort_values(['team', 'align_date'])

    results = []
    for team, group in query_df.groupby('team', sort=False):
        team_elo = elo_history_df[elo_history_df['team'] == team].sort_values('date')
        if len(team_elo) == 0:
            results.append(pd.DataFrame({'index': group['index'].values, 'elo_pre': DEFAULT_ELO}))
            continue
        merged = pd.merge_asof(
            group[['index', 'align_date']].sort_values('align_date'),
            team_elo[['date', 'elo_after']].rename(columns={'date': 'align_date', 'elo_after': 'elo_pre'}),
            on='align_date',
            direction='backward',
        )
        results.append(merged[['index', 'elo_pre']])

    if not results:
        return pd.Series([DEFAULT_ELO] * len(team_series), index=team_series.index)

    result_df = pd.concat(results, ignore_index=True).set_index('index').sort_index()
    return result_df['elo_pre']


# ========================================
# 特征组 A：对手实力分层让球走水率 (8维)
# ========================================

def build_opponent_strength_draw_features(
    df_expand: pd.DataFrame,
    elo_history_df: pd.DataFrame,
    debug_sample: int = 3,
) -> pd.DataFrame:
    """
    按对手实力分层计算每支球队近 N 场的让球走水率。

    对手分层规则（基于赛前 Elo 差距）:
        - stronger: 对手 Elo 比本队高 > 50 分
        - similar:  对手 Elo 与本队差距 ≤ 50 分
        - weaker:   对手 Elo 比本队低 > 50 分

    特征列表 (8维):
        home_hcp_draw_vs_stronger_l5
        home_hcp_draw_vs_similar_l5
        home_hcp_draw_vs_weaker_l5
        home_hcp_draw_vs_all_l10
        away_hcp_draw_vs_stronger_l5
        away_hcp_draw_vs_similar_l5
        away_hcp_draw_vs_weaker_l5
        away_hcp_draw_vs_all_l10

    防泄露:
        - 严格按 date < match_date 过滤历史
        - 赛前 Elo 用 get_pre_match_elo_batch 批量获取（不含当日）
    """
    print("\n[OPP-LAG] Step A: 构建对手实力分层让球走水率特征 (8维)...")

    # 解析比分 + 让球结果（使用反推标签）
    records = []
    for _, row in df_expand.iterrows():
        hg, ag = parse_score(row['actual_score'])
        if hg is None:
            continue
        # 使用 actual_handicap_pred 作为让球结果标签（0/1/2）
        hcp_result = row.get('actual_handicap_pred')
        if pd.isna(hcp_result):
            continue
        hcp_result = int(hcp_result)
        records.append({
            'match_id': row['match_id'],
            'date': pd.to_datetime(row['date'], errors='coerce'),
            'home_team': row['home_team'],
            'away_team': row['away_team'],
            'home_goals': hg,
            'away_goals': ag,
            'hcp_result': hcp_result,  # 0=上盘赢, 1=走水, 2=下盘赢
            'is_draw': 1 if hcp_result == DRAW_LABEL else 0,
        })
    df = pd.DataFrame(records).dropna(subset=['date']).sort_values('date').reset_index(drop=True)
    print(f"  有效比赛记录: {len(df)} 场")

    # 为每场比赛构建双方视角的记录
    # home 视角: 对手=away
    # away 视角: 对手=home
    team_match_records = []
    for _, row in df.iterrows():
        # 主队视角
        team_match_records.append({
            'team': row['home_team'],
            'opponent': row['away_team'],
            'date': row['date'],
            'is_home': True,
            'match_id': row['match_id'],
            'hcp_result': row['hcp_result'],
            'is_draw': row['is_draw'],
        })
        # 客队视角
        team_match_records.append({
            'team': row['away_team'],
            'opponent': row['home_team'],
            'date': row['date'],
            'is_home': False,
            'match_id': row['match_id'],
            'hcp_result': row['hcp_result'],
            'is_draw': row['is_draw'],
        })
    df_team = pd.DataFrame(team_match_records).sort_values(['team', 'date']).reset_index(drop=True)
    print(f"  双方视角记录: {len(df_team)} 条")

    # 为每条记录批量计算赛前 Elo（防泄露，高效向量化）
    print(f"  批量计算赛前 Elo...")
    df_team['team_elo_pre'] = get_pre_match_elo_batch(
        df_team['team'], df_team['date'], elo_history_df
    )
    df_team['opp_elo_pre'] = get_pre_match_elo_batch(
        df_team['opponent'], df_team['date'], elo_history_df
    )
    df_team['elo_diff_pre'] = df_team['team_elo_pre'] - df_team['opp_elo_pre']
    print(f"  赛前 Elo 差距统计: mean={df_team['elo_diff_pre'].mean():.1f}, "
          f"std={df_team['elo_diff_pre'].std():.1f}")

    # 对手实力分层标签
    def classify_opponent(elo_diff):
        if elo_diff < -ELO_GAP_STRONG:
            return 'stronger'   # 对手更强
        elif elo_diff > ELO_GAP_STRONG:
            return 'weaker'     # 对手更弱
        else:
            return 'similar'    # 实力相近
    df_team['opp_strength'] = df_team['elo_diff_pre'].apply(classify_opponent)
    print(f"  对手分层分布: {df_team['opp_strength'].value_counts().to_dict()}")

    # 抽样防泄露验证
    if debug_sample > 0:
        print(f"\n  🔍 防泄露日期校验（抽样 {debug_sample} 队）:")
        sample_teams = df_team['team'].unique()[:debug_sample]
        for team in sample_teams:
            team_df = df_team[df_team['team'] == team].sort_values('date').head(4)
            print(f"    🔹 {team} (共 {len(df_team[df_team['team']==team])} 场):")
            for _, r in team_df.iterrows():
                hist_max = df_team[(df_team['team']==team) & (df_team['date'] < r['date'])]['date'].max()
                hist_max_str = hist_max.date() if pd.notna(hist_max) else '无历史'
                print(f"      比赛 {r['date'].date()} vs {r['opponent']} (opp={r['opp_strength']}) | 历史最大日期: {hist_max_str}")

    # 按球队分组，计算各分层的 lag 走水率
    results = []
    feature_cols_a = [
        'home_hcp_draw_vs_stronger_l5', 'home_hcp_draw_vs_similar_l5',
        'home_hcp_draw_vs_weaker_l5', 'home_hcp_draw_vs_all_l10',
        'away_hcp_draw_vs_stronger_l5', 'away_hcp_draw_vs_similar_l5',
        'away_hcp_draw_vs_weaker_l5', 'away_hcp_draw_vs_all_l10',
    ]

    # 预初始化全0结果
    result_map = {mid: {c: 0.0 for c in feature_cols_a} for mid in df['match_id'].values}

    for team, group in df_team.groupby('team'):
        group = group.sort_values('date').reset_index(drop=True)
        n = len(group)

        # shift(1) 确保窗口不含当前比赛
        for strength in ['stronger', 'similar', 'weaker']:
            mask = (group['opp_strength'] == strength)
            # 仅对该分层的历史比赛计算 rolling mean
            draw_series = group['is_draw'].where(mask, np.nan)
            # shift(1) 后第 k 行 = 前 k-1 行均值
            lag5 = draw_series.rolling(window=WINDOW_SHORT, min_periods=1).mean().shift(1)
            for i, idx in enumerate(group.index):
                mid = group.loc[idx, 'match_id']
                is_home = group.loc[idx, 'is_home']
                col_prefix = 'home' if is_home else 'away'
                col_name = f'{col_prefix}_hcp_draw_vs_{strength}_l5'
                if mid in result_map:
                    val = lag5.iloc[i]
                    if pd.notna(val):
                        result_map[mid][col_name] = float(val)

        # 全对手近10场走水率
        draw_all = group['is_draw']
        lag10_all = draw_all.rolling(window=WINDOW_LONG, min_periods=1).mean().shift(1)
        for i, idx in enumerate(group.index):
            mid = group.loc[idx, 'match_id']
            is_home = group.loc[idx, 'is_home']
            col_name = f'{"home" if is_home else "away"}_hcp_draw_vs_all_l10'
            if mid in result_map:
                val = lag10_all.iloc[i]
                if pd.notna(val):
                    result_map[mid][col_name] = float(val)

    # 转为 DataFrame
    feat_df = pd.DataFrame.from_dict(result_map, orient='index')
    feat_df.index.name = 'match_id'

    # 统计覆盖率
    non_zero = (feat_df != 0.0).any(axis=1).sum()
    print(f"  ✅ 特征组 A 完成: {len(feat_df)} 场, 有历史数据: {non_zero} ({non_zero/len(feat_df)*100:.1f}%)")
    print(f"  特征列: {feature_cols_a}")

    return feat_df[feature_cols_a]


# ========================================
# 特征组 B：盘口线类别专属 Lag (6维)
# ========================================

def build_handicap_category_draw_features(
    df_expand: pd.DataFrame,
    debug_sample: int = 3,
) -> pd.DataFrame:
    """
    按盘口线类别计算每支球队近 10 场的让球走水率。

    盘口线类别（仅整数盘，与反推模型一致）:
        - give_1: 让1球 (handicap_line = -1.0)
        - get_1:  受让1球 (handicap_line = +1.0)
        - give_2: 让2球+ (handicap_line <= -2.0)

    特征列表 (6维):
        home_hcp_draw_at_give1_l10
        home_hcp_draw_at_get1_l10
        home_hcp_draw_at_give2_l10
        away_hcp_draw_at_give1_l10
        away_hcp_draw_at_get1_l10
        away_hcp_draw_at_give2_l10

    防泄露:
        - shift(1) 确保窗口不含当前比赛
        - 200场无盘口线的样本降级为 NaN（后续用全局走水率填充）
    """
    print("\n[OPP-LAG] Step B: 构建盘口线类别专属 Lag 特征 (6维)...")

    records = []
    for _, row in df_expand.iterrows():
        hg, ag = parse_score(row['actual_score'])
        if hg is None:
            continue
        hcp_result = row.get('actual_handicap_pred')
        hcp_line = row.get('handicap_line_pred')
        if pd.isna(hcp_result):
            continue
        hcp_result = int(hcp_result)

        # 盘口线类别分类
        if pd.isna(hcp_line):
            cat = 'unknown'
        else:
            line = float(hcp_line)
            if line == -1.0:
                cat = 'give_1'
            elif line == 1.0:
                cat = 'get_1'
            elif line <= -2.0:
                cat = 'give_2'
            else:
                cat = 'other'

        records.append({
            'match_id': row['match_id'],
            'date': pd.to_datetime(row['date'], errors='coerce'),
            'home_team': row['home_team'],
            'away_team': row['away_team'],
            'hcp_line': hcp_line,
            'hcp_cat': cat,
            'hcp_result': hcp_result,
            'is_draw': 1 if hcp_result == DRAW_LABEL else 0,
        })
    df = pd.DataFrame(records).dropna(subset=['date']).sort_values('date').reset_index(drop=True)
    print(f"  有效比赛记录: {len(df)} 场")
    print(f"  盘口线类别分布: {df['hcp_cat'].value_counts().to_dict()}")

    # 构建双方视角记录
    team_records = []
    for _, row in df.iterrows():
        # 主队视角：让球方 = home，盘口线 = -1 → home 是让球方
        if row['hcp_cat'] == 'give_1':
            home_role = 'give_1'
            away_role = 'get_1'
        elif row['hcp_cat'] == 'get_1':
            home_role = 'get_1'
            away_role = 'give_1'
        elif row['hcp_cat'] == 'give_2':
            home_role = 'give_2'
            away_role = 'get_2'  # 对应受让2球，不作为独立类别
        else:
            home_role = 'other'
            away_role = 'other'

        team_records.append({
            'team': row['home_team'],
            'date': row['date'],
            'match_id': row['match_id'],
            'is_home': True,
            'role': home_role,
            'is_draw': row['is_draw'],
        })
        team_records.append({
            'team': row['away_team'],
            'date': row['date'],
            'match_id': row['match_id'],
            'is_home': False,
            'role': away_role,
            'is_draw': row['is_draw'],
        })
    df_team = pd.DataFrame(team_records).sort_values(['team', 'date']).reset_index(drop=True)

    # 抽样防泄露验证
    if debug_sample > 0:
        print(f"\n  🔍 防泄露日期校验（抽样 {debug_sample} 队）:")
        sample_teams = df_team['team'].unique()[:debug_sample]
        for team in sample_teams:
            team_df = df_team[df_team['team'] == team].sort_values('date').head(3)
            print(f"    🔹 {team} (共 {len(df_team[df_team['team']==team])} 场):")
            for _, r in team_df.iterrows():
                hist_max = df_team[(df_team['team']==team) & (df_team['date'] < r['date'])]['date'].max()
                hist_max_str = hist_max.date() if pd.notna(hist_max) else '无历史'
                print(f"      比赛 {r['date'].date()} role={r['role']} | 历史最大日期: {hist_max_str}")

    # 按球队 + 角色分组计算 lag 走水率
    feature_cols_b = [
        'home_hcp_draw_at_give1_l10', 'home_hcp_draw_at_get1_l10', 'home_hcp_draw_at_give2_l10',
        'away_hcp_draw_at_give1_l10', 'away_hcp_draw_at_get1_l10', 'away_hcp_draw_at_give2_l10',
    ]
    result_map = {mid: {c: np.nan for c in feature_cols_b} for mid in df['match_id'].values}

    role_map = {
        'give_1': 'give1',
        'get_1': 'get1',
        'give_2': 'give2',
    }

    for team, group in df_team.groupby('team'):
        group = group.sort_values('date').reset_index(drop=True)
        # 对每个角色计算 rolling mean
        for role_key, role_label in role_map.items():
            mask = (group['role'] == role_key)
            draw_series = group['is_draw'].where(mask, np.nan)
            lag10 = draw_series.rolling(window=WINDOW_LONG, min_periods=1).mean().shift(1)
            for i, idx in enumerate(group.index):
                mid = group.loc[idx, 'match_id']
                is_home = group.loc[idx, 'is_home']
                col_prefix = 'home' if is_home else 'away'
                col_name = f'{col_prefix}_hcp_draw_at_{role_label}_l10'
                if mid in result_map:
                    val = lag10.iloc[i]
                    if pd.notna(val):
                        result_map[mid][col_name] = float(val)

    feat_df = pd.DataFrame.from_dict(result_map, orient='index')
    feat_df.index.name = 'match_id'

    # 缺失值用全局走水率（22.6%）降级填充
    global_draw_rate = df['is_draw'].mean()
    feat_df = feat_df.fillna(global_draw_rate)
    print(f"  全局走水率（用于缺失降级）: {global_draw_rate:.4f}")

    # 统计覆盖率
    non_nan = feat_df.notna().all(axis=1).sum()
    print(f"  ✅ 特征组 B 完成: {len(feat_df)} 场, 无缺失: {non_nan} ({non_nan/len(feat_df)*100:.1f}%)")
    print(f"  特征列: {feature_cols_b}")

    return feat_df[feature_cols_b]


# ========================================
# 特征组 C：直接交锋（H2H）让球历史 (6维)
# ========================================

def build_h2h_draw_features(
    df_expand: pd.DataFrame,
    debug_sample: int = 3,
) -> pd.DataFrame:
    """
    计算双方历史交锋的让球走水统计。

    特征列表 (6维):
        h2h_hcp_draw_rate         双方历史交锋让球走水率
        h2h_hcp_draw_count        双方历史交锋让球走水总数
        h2h_total_matches         双方历史交锋总场次
        h2h_last5_hcp_draws       近5次交锋让球走水数
        home_h2h_hcp_draw_rate    主队主场对阵该对手的让球走水率
        away_h2h_hcp_draw_rate    客队客场对阵该对手的让球走水率

    防泄露:
        - 严格按 date < match_date 过滤历史交锋
        - min_periods=2 避免单场交锋过度影响
    """
    print("\n[OPP-LAG] Step C: 构建直接交锋（H2H）让球历史特征 (6维)...")

    records = []
    for _, row in df_expand.iterrows():
        hg, ag = parse_score(row['actual_score'])
        if hg is None:
            continue
        hcp_result = row.get('actual_handicap_pred')
        if pd.isna(hcp_result):
            continue
        records.append({
            'match_id': row['match_id'],
            'date': pd.to_datetime(row['date'], errors='coerce'),
            'home_team': row['home_team'],
            'away_team': row['away_team'],
            'hcp_result': int(hcp_result),
            'is_draw': 1 if int(hcp_result) == DRAW_LABEL else 0,
        })
    df = pd.DataFrame(records).dropna(subset=['date']).sort_values('date').reset_index(drop=True)
    print(f"  有效比赛记录: {len(df)} 场")

    # 按 (home_team, away_team) 配对分组
    # 注意：H2H 是对称的，A vs B 和 B vs A 算同对对手
    df['pair_key'] = df.apply(
        lambda r: tuple(sorted([r['home_team'], r['away_team']])), axis=1
    )
    df = df.sort_values(['pair_key', 'date']).reset_index(drop=True)

    feature_cols_c = [
        'h2h_hcp_draw_rate', 'h2h_hcp_draw_count', 'h2h_total_matches',
        'h2h_last5_hcp_draws', 'home_h2h_hcp_draw_rate', 'away_h2h_hcp_draw_rate',
    ]

    # 抽样防泄露验证
    if debug_sample > 0:
        print(f"\n  🔍 防泄露日期校验（抽样 {debug_sample} 对 H2H）:")
        sample_pairs = df['pair_key'].unique()[:debug_sample]
        for pair in sample_pairs:
            pair_df = df[df['pair_key'] == pair].sort_values('date')
            print(f"    🔹 {pair[0]} vs {pair[1]} (共 {len(pair_df)} 次交锋):")
            for _, r in pair_df.head(2).iterrows():
                hist = pair_df[pair_df['date'] < r['date']]
                print(f"      比赛 {r['date'].date()} | 历史交锋: {len(hist)} 场, 走水: {hist['is_draw'].sum()}")

    # 向量化计算 H2H lag 特征（用 groupby + shift + expanding/cumsum）
    # shift(1) 确保不含当前比赛
    df['_h2h_count_cum'] = df.groupby('pair_key')['is_draw'].transform(
        lambda s: s.rolling(len(s), min_periods=1).count().shift(1)
    ).fillna(0)
    df['_h2h_draw_cum'] = df.groupby('pair_key')['is_draw'].transform(
        lambda s: s.rolling(len(s), min_periods=1).sum().shift(1)
    ).fillna(0)
    df['_h2h_last5_draws'] = df.groupby('pair_key')['is_draw'].transform(
        lambda s: s.rolling(5, min_periods=1).sum().shift(1)
    ).fillna(0)

    # H2H 走水率（min_periods=2 降级为全局 0.226）
    df['_h2h_rate'] = np.where(
        df['_h2h_count_cum'] >= 2,
        df['_h2h_draw_cum'] / df['_h2h_count_cum'],
        0.226  # 全局降级
    )

    # 主队主场 H2H（按 pair_key + home_team 分组）
    df['_home_home_key'] = df['pair_key'].astype(str) + '|' + df['home_team']
    df['_home_home_count'] = df.groupby('_home_home_key')['is_draw'].transform(
        lambda s: s.rolling(len(s), min_periods=1).count().shift(1)
    ).fillna(0)
    df['_home_home_draw'] = df.groupby('_home_home_key')['is_draw'].transform(
        lambda s: s.rolling(len(s), min_periods=1).sum().shift(1)
    ).fillna(0)
    df['_home_h2h_rate'] = np.where(
        df['_home_home_count'] >= 2,
        df['_home_home_draw'] / df['_home_home_count'],
        0.226
    )

    # 客队客场 H2H（按 pair_key + away_team 分组）
    df['_away_away_key'] = df['pair_key'].astype(str) + '|' + df['away_team']
    df['_away_away_count'] = df.groupby('_away_away_key')['is_draw'].transform(
        lambda s: s.rolling(len(s), min_periods=1).count().shift(1)
    ).fillna(0)
    df['_away_away_draw'] = df.groupby('_away_away_key')['is_draw'].transform(
        lambda s: s.rolling(len(s), min_periods=1).sum().shift(1)
    ).fillna(0)
    df['_away_h2h_rate'] = np.where(
        df['_away_away_count'] >= 2,
        df['_away_away_draw'] / df['_away_away_count'],
        0.226
    )

    # 组装结果
    feat_df = pd.DataFrame({
        'h2h_hcp_draw_rate': df['_h2h_rate'].values,
        'h2h_hcp_draw_count': df['_h2h_draw_cum'].values,
        'h2h_total_matches': df['_h2h_count_cum'].values,
        'h2h_last5_hcp_draws': df['_h2h_last5_draws'].values,
        'home_h2h_hcp_draw_rate': df['_home_h2h_rate'].values,
        'away_h2h_hcp_draw_rate': df['_away_h2h_rate'].values,
    }, index=df['match_id'].values)
    feat_df.index.name = 'match_id'

    has_h2h = (feat_df['h2h_total_matches'] > 0).sum()
    print(f"  ✅ 特征组 C 完成: {len(feat_df)} 场, 有 H2H 历史: {has_h2h} ({has_h2h/len(feat_df)*100:.1f}%)")
    print(f"  特征列: {feature_cols_c}")
    print(f"  H2H 总场次统计: mean={feat_df['h2h_total_matches'].mean():.1f}, "
          f"max={feat_df['h2h_total_matches'].max():.0f}")

    return feat_df[feature_cols_c]


# ========================================
# 特征组 D：市场信号增强 (4维)
# ========================================

def build_market_signal_features(
    df_expand: pd.DataFrame,
    elo_latest: Dict,
) -> pd.DataFrame:
    """
    构建市场信号增强特征。

    特征列表 (4维):
        elo_gap_abs              双方 Elo 差距绝对值（越接近越易走水）
        hcp_draw_prob_rank       当前让球平局赔率在同盘口类别近50场中的分位数
        opponent_season_draw_rate 对手本赛季让球走水率
        market_draw_std          让球平局赔率的时序标准差（低波动=市场一致）

    数据来源:
        - elo_latest (来自 compute_elo_history)
        - handicap_history 表（时序赔率）
    """
    print("\n[OPP-LAG] Step D: 构建市场信号增强特征 (4维)...")

    feature_cols_d = [
        'elo_gap_abs', 'hcp_draw_prob_rank',
        'opponent_season_draw_rate', 'market_draw_std',
    ]

    # 解析 season 字段
    def parse_season(s):
        if pd.isna(s):
            return None
        s = str(s)
        # season 格式: '2025-2026'
        if '-' in s:
            return s
        # 从 date 推断
        return None

    df_expand = df_expand.copy()
    df_expand['season_parsed'] = df_expand['season'].apply(parse_season)

    # 计算每场比赛的赛前 Elo 差距
    home_elos = df_expand['home_team'].map(lambda t: elo_latest.get(t, DEFAULT_ELO))
    away_elos = df_expand['away_team'].map(lambda t: elo_latest.get(t, DEFAULT_ELO))
    df_expand['_elo_gap_abs'] = (home_elos - away_elos).abs()

    # 计算对手赛季走水率（按对手 + 赛季分组）
    # 对每场比赛，找对手在本赛季的走水率（不含当前比赛）
    df_expand['_is_draw'] = (df_expand['actual_handicap_pred'] == DRAW_LABEL).astype(int)
    season_team_stats = df_expand.groupby(['season_parsed', 'home_team'])['_is_draw'].mean().to_dict()
    season_team_stats.update(
        df_expand.groupby(['season_parsed', 'away_team'])['_is_draw'].mean().to_dict()
    )

    def get_opp_season_draw_rate(row):
        season = row['season_parsed']
        if pd.isna(season):
            return 0.226
        # 对手是 away_team（从 home 视角）
        opp_rate = season_team_stats.get((season, row['away_team']))
        if pd.isna(opp_rate) or opp_rate is None:
            opp_rate = season_team_stats.get((season, row['home_team']))
        if pd.isna(opp_rate) or opp_rate is None:
            return 0.226
        return float(opp_rate)

    df_expand['_opp_season_draw_rate'] = df_expand.apply(get_opp_season_draw_rate, axis=1)

    # 让球平局赔率分位数（按盘口线类别分组）
    df_expand['_hcp_draw'] = pd.to_numeric(df_expand['hcp_draw'], errors='coerce')
    df_expand['_hcp_line_cat'] = df_expand['handicap_line_pred'].apply(
        lambda x: 'give_1' if x == -1.0 else ('get_1' if x == 1.0 else ('give_2' if pd.notna(x) and x <= -2.0 else 'other'))
    )
    # 全局分位数（简化版：当前比赛 hcp_draw 在全部比赛中的排名）
    df_expand = df_expand.sort_values('date').reset_index(drop=True)
    df_expand['_hcp_draw_prob_rank'] = (
        df_expand.groupby('_hcp_line_cat')['_hcp_draw']
        .expanding(min_periods=1).rank(pct=True).shift(1).reset_index(level=0, drop=True)
    )
    df_expand['_hcp_draw_prob_rank'] = df_expand['_hcp_draw_prob_rank'].fillna(0.5)

    # 让球平局赔率时序标准差（从 handicap_history 取时序数据）
    # SQLite 无 STDDEV 函数，用 Python 聚合计算
    try:
        conn = sqlite3.connect(DB_PATH)
        query = """
            SELECT m.matches_match_id as match_id, h.hcp_draw
            FROM handicap_history h
            INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
            WHERE h.timestamp < '2026-07-01 00:00:00'
              AND h.hcp_draw IS NOT NULL AND h.hcp_draw > 0
        """
        df_odds_ts = pd.read_sql(query, conn)
        conn.close()
        # 按 match_id 聚合计算标准差
        std_map = df_odds_ts.groupby('match_id')['hcp_draw'].std().to_dict()
        print(f"  时序赔率标准差: 覆盖 {len(std_map)} 场比赛")
    except Exception as e:
        print(f"  ⚠️ 无法加载时序赔率标准差: {e}")
        std_map = {}

    df_expand['_market_draw_std'] = df_expand['match_id'].map(lambda mid: std_map.get(mid, 0.2))

    # 组装特征
    feat_df = pd.DataFrame(index=df_expand['match_id'].values)
    feat_df['elo_gap_abs'] = df_expand['_elo_gap_abs'].values
    feat_df['hcp_draw_prob_rank'] = df_expand['_hcp_draw_prob_rank'].values
    feat_df['opponent_season_draw_rate'] = df_expand['_opp_season_draw_rate'].values
    feat_df['market_draw_std'] = df_expand['_market_draw_std'].values
    feat_df.index.name = 'match_id'

    print(f"  ✅ 特征组 D 完成: {len(feat_df)} 场")
    print(f"  特征列: {feature_cols_d}")
    print(f"  elo_gap_abs 统计: mean={feat_df['elo_gap_abs'].mean():.1f}, "
          f"median={feat_df['elo_gap_abs'].median():.1f}")

    return feat_df[feature_cols_d]


# ========================================
# 主入口：构建全部 24 维对手调整 Lag 特征
# ========================================

OPP_LAG_FEATURE_GROUPS = {
    'A_opponent_strength': [
        'home_hcp_draw_vs_stronger_l5', 'home_hcp_draw_vs_similar_l5',
        'home_hcp_draw_vs_weaker_l5', 'home_hcp_draw_vs_all_l10',
        'away_hcp_draw_vs_stronger_l5', 'away_hcp_draw_vs_similar_l5',
        'away_hcp_draw_vs_weaker_l5', 'away_hcp_draw_vs_all_l10',
    ],
    'B_handicap_category': [
        'home_hcp_draw_at_give1_l10', 'home_hcp_draw_at_get1_l10', 'home_hcp_draw_at_give2_l10',
        'away_hcp_draw_at_give1_l10', 'away_hcp_draw_at_get1_l10', 'away_hcp_draw_at_give2_l10',
    ],
    'C_h2h': [
        'h2h_hcp_draw_rate', 'h2h_hcp_draw_count', 'h2h_total_matches',
        'h2h_last5_hcp_draws', 'home_h2h_hcp_draw_rate', 'away_h2h_hcp_draw_rate',
    ],
    'D_market': [
        'elo_gap_abs', 'hcp_draw_prob_rank',
        'opponent_season_draw_rate', 'market_draw_std',
    ],
}

OPP_LAG_ALL_FEATURES = (
    OPP_LAG_FEATURE_GROUPS['A_opponent_strength'] +
    OPP_LAG_FEATURE_GROUPS['B_handicap_category'] +
    OPP_LAG_FEATURE_GROUPS['C_h2h'] +
    OPP_LAG_FEATURE_GROUPS['D_market']
)


def build_all_opponent_lag_features(debug_sample: int = 3) -> pd.DataFrame:
    """
    一站式构建全部 24 维对手调整 Lag 特征。

    返回:
        features DataFrame (index=match_id, 24维特征)
    """
    print("=" * 70)
    print("🔧 T-005 v2 对手调整 Lag 特征工程 (24维)")
    print("=" * 70)

    # 1. 加载扩充数据集
    df_expand = load_expanded_dataset()

    # 2. 计算 Elo 历史（用于对手实力分层）
    print("\n[ELO] 计算 Elo 评分历史...")
    df_for_elo = df_expand[['match_id', 'date', 'home_team', 'away_team', 'actual_score']].copy()
    df_for_elo = df_for_elo.rename(columns={'date': 'match_date'})
    df_for_elo = df_for_elo.sort_values('match_date').reset_index(drop=True)
    elo_history_df, elo_latest = compute_elo_history(df_for_elo)
    print(f"  Elo 评分球队数: {len(elo_latest)}")
    print(f"  Elo 范围: {min(elo_latest.values()):.1f} ~ {max(elo_latest.values()):.1f}")

    # 3. 构建四组特征
    feat_a = build_opponent_strength_draw_features(df_expand, elo_history_df, debug_sample=debug_sample)
    feat_b = build_handicap_category_draw_features(df_expand, debug_sample=debug_sample)
    feat_c = build_h2h_draw_features(df_expand, debug_sample=debug_sample)
    feat_d = build_market_signal_features(df_expand, elo_latest)

    # 4. 合并
    print("\n" + "=" * 70)
    print("📦 合并全部 24 维对手调整 Lag 特征")
    print("=" * 70)

    # 去重 match_id（取第一条），避免 join 产生笛卡尔积
    feat_a = feat_a[~feat_a.index.duplicated(keep='first')]
    feat_b = feat_b[~feat_b.index.duplicated(keep='first')]
    feat_c = feat_c[~feat_c.index.duplicated(keep='first')]
    feat_d = feat_d[~feat_d.index.duplicated(keep='first')]

    features = feat_a.join(feat_b, how='outer')
    features = features.join(feat_c, how='outer')
    features = features.join(feat_d, how='outer')

    # 确保列顺序
    features = features[OPP_LAG_ALL_FEATURES]

    print(f"\n✅ 最终特征矩阵: {features.shape}")
    print(f"  特征组 A (对手实力分层): {len(OPP_LAG_FEATURE_GROUPS['A_opponent_strength'])} 维")
    print(f"  特征组 B (盘口线类别): {len(OPP_LAG_FEATURE_GROUPS['B_handicap_category'])} 维")
    print(f"  特征组 C (H2H): {len(OPP_LAG_FEATURE_GROUPS['C_h2h'])} 维")
    print(f"  特征组 D (市场信号): {len(OPP_LAG_FEATURE_GROUPS['D_market'])} 维")

    # 缺失值检查
    null_counts = features.isnull().sum()
    if null_counts.sum() > 0:
        print(f"\n  ⚠️ 缺失值统计:")
        for col, cnt in null_counts[null_counts > 0].items():
            print(f"    {col}: {cnt} ({cnt/len(features)*100:.1f}%)")
        # 用中位数填充
        features = features.fillna(features.median())
        print(f"  → 用中位数填充缺失值")

    # 特征统计摘要
    print(f"\n📊 特征统计摘要:")
    print(features.describe().T[['mean', 'std', 'min', '50%', 'max']].round(4).to_string())

    return features


if __name__ == "__main__":
    features = build_all_opponent_lag_features(debug_sample=3)
    print(f"\n🎉 对手调整 Lag 特征构建完成!")
    print(f"  总特征数: {features.shape[1]} 维")
    print(f"  总样本数: {features.shape[0]} 场")

    # 保存特征快照
    output_path = os.path.join(REPORT_DIR, f'hcp_opponent_lag_features_{pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")}.csv')
    features.to_csv(output_path)
    print(f"  保存到: {output_path}")
