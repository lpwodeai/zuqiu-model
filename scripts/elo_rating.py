"""
D-012 Elo Rating 特征计算模块
==============================

基于球队历史比赛结果计算 Elo 评分，生成赛前可用的实力排名特征。

核心参数:
    - 初始评分: 1500
    - K 因子: 32（标准 Elo）
    - 主场优势: 65 分（主队额外Elo加成，约0.5个进球优势）
    - 比赛结果: 主胜=1.0, 平局=0.5, 客胜=0.0

输出特征:
    - home_elo:          主队赛前 Elo 评分
    - away_elo:          客队赛前 Elo 评分
    - elo_diff:          Elo 差值 (home_elo - away_elo + 主场优势)
    - elo_ratio:         Elo 比率 (home_elo / away_elo)
    - elo_home_expected:  主队预期胜率 (基于Elo差值)
    - elo_away_expected: 客队预期胜率
    - elo_draw_prob:     平局概率估算 (基于Elo差值的非线性映射)
    - home_elo_momentum:  主队近5场Elo净变化 (动量)
    - away_elo_momentum: 客队近5场Elo净变化
    - elo_confidence:     Elo置信度 (双方Elo之和/3000, 越高实力越接近)

设计原则:
    - 严格使用赛前已完成比赛数据，防止数据泄露
    - 新球队初始评分 1500
    - 主客队 Elo 分开计算（主场优势已内置）
"""

import os
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional


# ========================================
# Elo Rating 核心函数
# ========================================

DEFAULT_ELO = 1500
K_FACTOR = 32
HOME_ADVANTAGE = 65.0
MIN_ELO = 1000
MAX_ELO = 2000


def expected_score(elo_a: float, elo_b: float, home_advantage: float = 0.0) -> float:
    """
    计算 Elo 预期胜率 (Team A vs Team B)

    参数:
        elo_a: Team A 的 Elo 评分
        elo_b: Team B 的 Elo 评分
        home_advantage: 主场优势加成 (Team A 为主队时使用)

    返回:
        float: Team A 的预期胜率 [0, 1]
    """
    rating_a = elo_a + home_advantage
    # 标准 Elo 公式: E_A = 1 / (1 + 10^((R_B - R_A) / 400))
    # 修复历史 bug: 原公式 (rating_a - elo_b) 符号反转，导致高分队期望得分反而低
    return 1.0 / (1.0 + 10 ** ((elo_b - rating_a) / 400.0))


def update_elo(elo_a: float, elo_b: float, actual_score_a: float,
               k_factor: float = K_FACTOR, home_advantage: float = 0.0) -> Tuple[float, float]:
    """
    更新双方 Elo 评分

    参数:
        elo_a: Team A 当前 Elo
        elo_b: Team B 当前 Elo
        actual_score_a: Team A 实际得分 (1.0=胜, 0.5=平, 0.0=负)
        k_factor: K 因子
        home_advantage: 主场优势 (Team A 为主队时使用)

    返回:
        Tuple[float, float]: (new_elo_a, new_elo_b)
    """
    expected_a = expected_score(elo_a, elo_b, home_advantage)
    expected_b = 1.0 - expected_a
    actual_b = 1.0 - actual_score_a

    new_elo_a = elo_a + k_factor * (actual_score_a - expected_a)
    new_elo_b = elo_b + k_factor * (actual_b - expected_b)

    new_elo_a = np.clip(new_elo_a, MIN_ELO, MAX_ELO)
    new_elo_b = np.clip(new_elo_b, MIN_ELO, MAX_ELO)

    return float(new_elo_a), float(new_elo_b)


def result_to_score(home_goals: int, away_goals: int) -> float:
    """
    将比赛结果转换为主队得分

    返回:
        float: 1.0=主胜, 0.5=平, 0.0=客胜
    """
    if home_goals > away_goals:
        return 1.0
    elif home_goals < away_goals:
        return 0.0
    else:
        return 0.5


def goal_diff_to_score(home_goals: int, away_goals: int) -> float:
    """
    将进球差转换为主队得分（使用进球差调整的Elo更新）

    进球差越大，Elo变化越大（K因子调整）
    """
    diff = abs(home_goals - away_goals)
    if home_goals > away_goals:
        base_score = 1.0
    elif home_goals < away_goals:
        base_score = 0.0
    else:
        base_score = 0.5

    if diff >= 3:
        return base_score
    elif diff == 2:
        return base_score
    else:
        return base_score


# ========================================
# Elo Rating 预计算
# ========================================

def precompute_elo_ratings(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    预计算所有球队的 Elo 评分历史

    按时间顺序遍历所有比赛，对每场比赛：
    1. 使用比赛前已完成的 Elo 作为赛前特征
    2. 比赛结束后更新双方 Elo

    参数:
        df: 比赛数据DataFrame，需包含:
            - date: 比赛日期
            - home_team_name: 主队名
            - away_team_name: 客队名
            - homeGoals: 主队进球
            - awayGoals: 客队进球
            - competition_name: 联赛名

    返回:
        Dict[str, pd.DataFrame]: 以球队名为key，值为该球队的Elo历史DataFrame
            DataFrame列: date, elo, opponent_elo, result, is_home, elo_change
    """
    df_sorted = df.sort_values('date').reset_index(drop=True)
    team_elo: Dict[str, float] = {}
    team_elo_history: Dict[str, List[Dict]] = {}

    # 预提取 numpy 数组，range 循环替代 iterrows（iterrows 逐行构造 Series 开销大）
    _home_names = df_sorted['home_team_name'].values
    _away_names = df_sorted['away_team_name'].values
    _dates = df_sorted['date'].values
    _hg_vals = df_sorted['homeGoals'].values
    _ag_vals = df_sorted['awayGoals'].values

    for i in range(len(df_sorted)):
        home_team = _home_names[i]
        away_team = _away_names[i]
        match_date = _dates[i]
        try:
            hg = int(_hg_vals[i])
            ag = int(_ag_vals[i])
        except (ValueError, TypeError):
            # 未进行比赛或无进球数据，跳过 Elo 更新
            continue

        if home_team not in team_elo:
            team_elo[home_team] = DEFAULT_ELO
            team_elo_history[home_team] = []
        if away_team not in team_elo:
            team_elo[away_team] = DEFAULT_ELO
            team_elo_history[away_team] = []

        home_elo_before = team_elo[home_team]
        away_elo_before = team_elo[away_team]

        actual_score_home = result_to_score(hg, ag)

        new_home_elo, new_away_elo = update_elo(
            home_elo_before, away_elo_before, actual_score_home,
            k_factor=K_FACTOR, home_advantage=HOME_ADVANTAGE
        )

        team_elo[home_team] = new_home_elo
        team_elo[away_team] = new_away_elo

        result_code = 2 if hg > ag else (0 if hg < ag else 1)

        team_elo_history[home_team].append({
            'date': match_date,
            'elo': home_elo_before,
            'opponent_elo': away_elo_before,
            'result': result_code,
            'is_home': True,
            'elo_change': new_home_elo - home_elo_before,
            'home_goals': hg,
            'away_goals': ag,
        })

        team_elo_history[away_team].append({
            'date': match_date,
            'elo': away_elo_before,
            'opponent_elo': home_elo_before,
            'result': result_code,
            'is_home': False,
            'elo_change': new_away_elo - away_elo_before,
            'home_goals': ag,
            'away_goals': hg,
        })

    team_elo_dfs = {}
    for team, history in team_elo_history.items():
        if history:
            team_elo_dfs[team] = pd.DataFrame(history)
        else:
            team_elo_dfs[team] = pd.DataFrame(columns=[
                'date', 'elo', 'opponent_elo', 'result', 'is_home',
                'elo_change', 'home_goals', 'away_goals'
            ])

    return team_elo_dfs


def get_team_elo_at_date(team_elo_dfs: Dict[str, pd.DataFrame], team: str,
                         match_date, window: int = 5) -> Dict:
    """
    获取指定球队在比赛日期前的 Elo 数据

    参数:
        team_elo_dfs: precompute_elo_ratings() 返回的球队Elo历史
        team: 球队名
        match_date: 比赛日期
        window: 动量计算窗口 (近N场)

    返回:
        Dict: {
            'elo': 赛前Elo评分,
            'momentum': 近N场Elo净变化,
            'matches_count': 已赛场次,
        }
    """
    if team not in team_elo_dfs:
        return {
            'elo': DEFAULT_ELO,
            'momentum': 0.0,
            'matches_count': 0,
        }

    team_hist = team_elo_dfs[team]
    before = team_hist[team_hist['date'] < match_date]

    if len(before) == 0:
        return {
            'elo': DEFAULT_ELO,
            'momentum': 0.0,
            'matches_count': 0,
        }

    last_elo = before.iloc[-1]['elo']

    recent = before.tail(window)
    momentum = recent['elo_change'].sum()

    return {
        'elo': float(last_elo),
        'momentum': float(momentum),
        'matches_count': len(before),
    }


def build_elo_features(df: pd.DataFrame, team_elo_dfs: Dict[str, pd.DataFrame] = None) -> pd.DataFrame:
    """
    构建 Elo Rating 特征矩阵

    参数:
        df: 比赛数据DataFrame
        team_elo_dfs: 预计算的球队Elo历史 (可选，如不传则自动计算)

    返回:
        pd.DataFrame: Elo特征矩阵，包含:
            home_elo, away_elo, elo_diff, elo_ratio,
            elo_home_expected, elo_away_expected, elo_draw_prob,
            home_elo_momentum, away_elo_momentum, elo_confidence
    """
    if team_elo_dfs is None:
        team_elo_dfs = precompute_elo_ratings(df)

    # 预构建每队 (日期int64, elo数组, elo_change数组) lookup，用 np.searchsorted
    # O(log m) 定位「该队 date<md 的最近一场」，替换逐场 get_team_elo_at_date 内部
    # team_hist[date<md] 全量布尔过滤的 O(n·m) 瓶颈（C-20260830-010）。
    def _int64_days(col):
        # 兼容 datetime64 与 object/字符串/StringDtype 日期：统一按 datetime64[D] 转天数
        # 用 pandas 类型判断而非 np.issubdtype，避免 pandas extension dtype 抛错
        # （C-20260908-ImportError 级运行时 bug：train_hcp_model_v2 Elo 特征构建失败）
        if pd.api.types.is_datetime64_any_dtype(col.dtype):
            return col.values.astype('datetime64[D]').astype(np.int64)
        return pd.to_datetime(col, format='mixed').values.astype('datetime64[D]').astype(np.int64)

    _EMPTY_ARR = np.array([], dtype=np.int64)
    _EMPTY_FLT = np.array([], dtype=np.float64)
    lookup: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for team, tdf in team_elo_dfs.items():
        if tdf is None or len(tdf) == 0:
            lookup[team] = (_EMPTY_ARR, _EMPTY_FLT, _EMPTY_FLT)
            continue
        di = _int64_days(tdf['date'])
        elo_arr = tdf['elo'].values.astype(np.float64)
        chg_arr = tdf['elo_change'].values.astype(np.float64)
        lookup[team] = (di, elo_arr, chg_arr)

    WINDOW = 5
    dates_all = _int64_days(df['date'])
    home_names = df['home_team_name'].values
    away_names = df['away_team_name'].values

    elo_feature_data = []
    for i in range(len(df)):
        md = dates_all[i]
        home_team = home_names[i]
        away_team = away_names[i]

        hdi, helo, hchg = lookup.get(home_team, (_EMPTY_ARR, _EMPTY_FLT, _EMPTY_FLT))
        adi, aelo, achg = lookup.get(away_team, (_EMPTY_ARR, _EMPTY_FLT, _EMPTY_FLT))

        k_h = int(np.searchsorted(hdi, md, side='left'))
        if k_h > 0:
            home_elo = helo[k_h - 1]
            home_momentum = float(hchg[max(0, k_h - WINDOW):k_h].sum())
        else:
            home_elo = DEFAULT_ELO
            home_momentum = 0.0

        k_a = int(np.searchsorted(adi, md, side='left'))
        if k_a > 0:
            away_elo = aelo[k_a - 1]
            away_momentum = float(achg[max(0, k_a - WINDOW):k_a].sum())
        else:
            away_elo = DEFAULT_ELO
            away_momentum = 0.0

        elo_diff = home_elo - away_elo + HOME_ADVANTAGE

        home_expected = expected_score(home_elo, away_elo, HOME_ADVANTAGE)
        away_expected = 1.0 - home_expected

        elo_ratio = home_elo / away_elo if away_elo > 0 else 1.0

        diff_normalized = elo_diff / 200.0
        elo_draw_prob = np.exp(-0.5 * (diff_normalized ** 2))
        elo_draw_prob = float(np.clip(elo_draw_prob, 0.0, 1.0))

        elo_confidence = (home_elo + away_elo) / 3000.0

        elo_feature_data.append({
            'home_elo': round(float(home_elo), 2),
            'away_elo': round(float(away_elo), 2),
            'elo_diff': round(float(elo_diff), 2),
            'elo_ratio': round(float(elo_ratio), 4),
            'elo_home_expected': round(float(home_expected), 4),
            'elo_away_expected': round(float(away_expected), 4),
            'elo_draw_prob': round(float(elo_draw_prob), 4),
            'home_elo_momentum': round(home_momentum, 2),
            'away_elo_momentum': round(away_momentum, 2),
            'elo_confidence': round(float(elo_confidence), 4),
        })

    elo_df = pd.DataFrame(elo_feature_data, index=df.index)

    for col in elo_df.columns:
        elo_df[col] = elo_df[col].fillna(DEFAULT_ELO if 'elo' in col and 'momentum' not in col else 0.0)

    return elo_df


def build_elo_features_legacy(df: pd.DataFrame, team_elo_dfs: Dict[str, pd.DataFrame] = None) -> pd.DataFrame:
    """构建 Elo Rating 特征矩阵【旧版，保留用于回退验证】。

    与新 build_elo_features 输出完全等价，区别在于逐行 iterrows + get_team_elo_at_date
    内部的 team_hist[date<md] 布尔过滤（O(n·m)）。保留用于正确性对比与回滚。
    """
    if team_elo_dfs is None:
        team_elo_dfs = precompute_elo_ratings(df)

    elo_feature_data = []

    for _, row in df.iterrows():
        home_team = row['home_team_name']
        away_team = row['away_team_name']
        match_date = row['date']

        home_info = get_team_elo_at_date(team_elo_dfs, home_team, match_date)
        away_info = get_team_elo_at_date(team_elo_dfs, away_team, match_date)

        home_elo = home_info['elo']
        away_elo = away_info['elo']

        elo_diff = home_elo - away_elo + HOME_ADVANTAGE

        home_expected = expected_score(home_elo, away_elo, HOME_ADVANTAGE)
        away_expected = 1.0 - home_expected

        elo_ratio = home_elo / away_elo if away_elo > 0 else 1.0

        diff_normalized = elo_diff / 200.0
        elo_draw_prob = np.exp(-0.5 * (diff_normalized ** 2))
        elo_draw_prob = float(np.clip(elo_draw_prob, 0.0, 1.0))

        elo_confidence = (home_elo + away_elo) / 3000.0

        elo_feature_data.append({
            'home_elo': round(home_elo, 2),
            'away_elo': round(away_elo, 2),
            'elo_diff': round(elo_diff, 2),
            'elo_ratio': round(elo_ratio, 4),
            'elo_home_expected': round(home_expected, 4),
            'elo_away_expected': round(away_expected, 4),
            'elo_draw_prob': round(elo_draw_prob, 4),
            'home_elo_momentum': round(home_info['momentum'], 2),
            'away_elo_momentum': round(away_info['momentum'], 2),
            'elo_confidence': round(elo_confidence, 4),
        })

    elo_df = pd.DataFrame(elo_feature_data, index=df.index)

    for col in elo_df.columns:
        elo_df[col] = elo_df[col].fillna(DEFAULT_ELO if 'elo' in col and 'momentum' not in col else 0.0)

    return elo_df


def get_elo_feature_names() -> List[str]:
    """返回所有 Elo 特征名列表"""
    return [
        'home_elo',
        'away_elo',
        'elo_diff',
        'elo_ratio',
        'elo_home_expected',
        'elo_away_expected',
        'elo_draw_prob',
        'home_elo_momentum',
        'away_elo_momentum',
        'elo_confidence',
    ]


# ========================================
# 独立运行验证
# ========================================

if __name__ == '__main__':
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from feature_utils import load_match_data_odds

    print("=" * 70)
    print("🧪 D-012 Elo Rating 特征验证")
    print("=" * 70)

    df = load_match_data_odds()
    print(f"  加载比赛数据: {len(df)} 场")

    print("\n  📊 预计算 Elo 评分...")
    team_elo_dfs = precompute_elo_ratings(df)
    print(f"  球队数: {len(team_elo_dfs)}")

    print("\n  📊 构建 Elo 特征矩阵...")
    elo_df = build_elo_features(df, team_elo_dfs)
    print(f"  Elo 特征维度: {elo_df.shape[1]}")
    print(f"  特征列表: {list(elo_df.columns)}")

    print("\n  📊 Elo 特征统计:")
    print(elo_df.describe().round(2))

    print("\n  📊 前10场比赛Elo预览:")
    preview = pd.DataFrame({
        'date': df['date'].dt.strftime('%Y-%m-%d'),
        'home': df['home_team_name'],
        'away': df['away_team_name'],
        'home_elo': elo_df['home_elo'],
        'away_elo': elo_df['away_elo'],
        'elo_diff': elo_df['elo_diff'],
        'home_expected': elo_df['elo_home_expected'],
    })
    print(preview.head(10).to_string(index=False))

    print("\n  📊 各联赛Elo分布:")
    df_with_elo = df.copy()
    df_with_elo['elo_diff'] = elo_df['elo_diff']
    for league in df_with_elo['competition_name'].unique():
        league_elo = df_with_elo[df_with_elo['competition_name'] == league]['elo_diff']
        print(f"    {league}: elo_diff 均值={league_elo.mean():.1f}, 标准差={league_elo.std():.1f}")

    print("\n✅ D-012 Elo Rating 模块验证完成!")