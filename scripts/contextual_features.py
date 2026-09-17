# -*- coding: utf-8 -*-
"""
P1-10: 情境化特征工程（Contextual Feature Engineering）
======================================================

数据源: 训练 df 本身（`feature_utils.load_match_data_odds` 产出，含 date/home/away/
        competition_name/homeGoals/awayGoals/result），按「严格早于当前比赛」的
        历史窗口构建，无数据泄漏。

特征（14 维）:
  A. 休息天数（3 维）
     - h_rest_days / a_rest_days: 距上一场比赛间隔天数（主/客）
     - rest_days_diff: 主客休息天数差
  B. 赛程密度（4 维，近 7/14 天比赛场次）
     - h_games_7d / a_games_7d / h_games_14d / a_games_14d
  C. 主客连续作战（2 维）
     - h_away_streak / a_away_streak: 连续客场场次（客场奔波的体能消耗）
  D. 积分/排名压力（3 维）
     - h_ppg_last5 / a_ppg_last5: 近 5 场场均积分（形势/争冠保级压力代理）
     - ppg_last5_diff: 双方近 5 场场均积分差
  E. 德比/新军身份（2 维）
     - is_derby: 是否是已知德比（curated 列表，13 组）
     - league_exp_diff: 本联赛历史赛季经验差（h - a，近似升班马/新军身份）

说明:
  - 所有统计严格使用 `date < 当前比赛 date` 的历史（searchsorted side='left'）。
  - 最早的比赛无历史，统一以联赛中位数/0 填充（由 build_team_features 同类策略）。
  - 队名用归一化中文名（feature_utils.normalize_team_name），与训练 df 严格一致。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# 已知德比（中文归一化名，夹在两个方向的同一对都视为德比）
DERBY_PAIRS = {
    frozenset({'曼彻斯特联', '曼彻斯特城'}),   # 曼市德比
    frozenset({'利物浦', '埃弗顿'}),           # 默西塞德德比
    frozenset({'阿森纳', '托特纳姆热刺'}),     # 北伦敦德比
    frozenset({'曼彻斯特联', '利物浦'}),       # 双红会
    frozenset({'皇家马德里', '巴塞罗那'}),     # 西班牙国家德比
    frozenset({'皇家马德里', '马德里竞技'}),   # 马德里德比
    frozenset({'塞维利亚', '皇家贝蒂斯'}),     # 塞维利亚德比
    frozenset({'国际米兰', 'AC米兰'}),         # 米兰德比
    frozenset({'尤文图斯', '国际米兰'}),       # 意大利德比
    frozenset({'尤文图斯', '都灵'}),           # 都灵德比
    frozenset({'罗马', '拉齐奥'}),             # 罗马德比
    frozenset({'拜仁慕尼黑', '多特蒙德'}),     # 德国国家德比
    frozenset({'巴黎圣日尔曼', '马赛'}),       # 法国国家德比
}

FEATURE_COLS = [
    "h_rest_days", "a_rest_days", "rest_days_diff",
    "h_games_7d", "a_games_7d", "h_games_14d", "a_games_14d",
    "h_away_streak", "a_away_streak",
    "h_ppg_last5", "a_ppg_last5", "ppg_last5_diff",
    "is_derby", "league_exp_diff",
]


def _season_of(ts: pd.Timestamp) -> int:
    """欧洲赛季锚定：7 月起计入下一赛季（8 月=新赛季首月）。"""
    return ts.year if ts.month >= 7 else ts.year - 1


def _build_team_index(df: pd.DataFrame):
    """构建每队按日期升序的历史数组（home/away 各一条记录）。

    返回:
        dates: dict[team -> np.ndarray[ordinal]]          升序
        is_home: dict[team -> np.ndarray[int]]           1=主 0=客
        points: dict[team -> np.ndarray[int]]            3胜/1平/0负
        league: dict[team -> np.ndarray[league]]         每场所在联赛
        season: dict[team -> np.ndarray[int]]            每场赛季锚定年
    """
    d = df.sort_values('date').reset_index(drop=True)
    from collections import defaultdict

    buckets = defaultdict(list)  # team -> list of (ordinal, is_home, points, league, season)

    dates = d['date']
    hg = d['homeGoals'].values
    ag = d['awayGoals'].values
    home = d['home_team_name'].values
    away = d['away_team_name'].values
    lg = d['competition_name'].values

    for i in range(len(d)):
        ordi = dates.iloc[i].toordinal()
        sea = _season_of(dates.iloc[i])
        h_pts = 3 if hg[i] > ag[i] else (1 if hg[i] == ag[i] else 0)
        a_pts = 3 if ag[i] > hg[i] else (1 if hg[i] == ag[i] else 0)
        buckets[home[i]].append((ordi, 1, h_pts, lg[i], sea))
        buckets[away[i]].append((ordi, 0, a_pts, lg[i], sea))

    dates, is_home, points, league, season = {}, {}, {}, {}, {}
    for team, rows in buckets.items():
        rows = sorted(rows, key=lambda r: r[0])
        dates[team] = np.array([r[0] for r in rows], dtype=np.int64)
        is_home[team] = np.array([r[1] for r in rows], dtype=np.int64)
        points[team] = np.array([r[2] for r in rows], dtype=np.int64)
        league[team] = np.array([r[3] for r in rows], dtype=object)
        season[team] = np.array([r[4] for r in rows], dtype=np.int64)
    return dates, is_home, points, league, season


def _league_exp_prior(dates, season, league, team, team_league, d_ord, cur_season):
    """该队在当前比赛之前、在本联赛中、早于当前赛季的赛季数（新军近似）。"""
    sd = dates.get(team)
    if sd is None:
        return 0.0
    idx = int(np.searchsorted(sd, d_ord, side='left'))
    if idx == 0:
        return 0.0
    lg = league.get(team)
    se = season.get(team)
    prior = set()
    for j in range(idx):
        if lg[j] == team_league and int(se[j]) < cur_season:
            prior.add(int(se[j]))
    return float(len(prior))


def build_contextual_features(df: pd.DataFrame) -> pd.DataFrame:
    """返回与训练 df 对齐的情境特征（index 同 df.index，15 维，缺失填联赛中位数/0）。"""
    dates, is_home, points, league, season = _build_team_index(df)

    df_dates = pd.to_datetime(df['date'])
    df_ord = df_dates.map(pd.Timestamp.toordinal).values
    home_names = df['home_team_name'].values
    away_names = df['away_team_name'].values
    leagues = df['competition_name'].values
    n = len(df)

    out = np.zeros((n, len(FEATURE_COLS)), dtype=float)

    for i in range(n):
        d_ord = int(df_ord[i])
        h = home_names[i]
        a = away_names[i]
        lg = leagues[i]
        sea = _season_of(df_dates.iloc[i])

        # --- 休息天数 ---
        hd = dates.get(h)
        h_rest = np.nan
        if hd is not None:
            hi = int(np.searchsorted(hd, d_ord, side='left'))
            if hi > 0:
                h_rest = float(d_ord - int(hd[hi - 1]))
        ad = dates.get(a)
        a_rest = np.nan
        if ad is not None:
            ai = int(np.searchsorted(ad, d_ord, side='left'))
            if ai > 0:
                a_rest = float(d_ord - int(ad[ai - 1]))

        # --- 赛程密度（近 7/14 天）---
        def games_in_window(team, win):
            td = dates.get(team)
            if td is None:
                return 0.0
            ti = int(np.searchsorted(td, d_ord, side='left'))
            if ti == 0:
                return 0.0
            lo = d_ord - win
            # 统计 (lo, d_ord) 内比赛场次（严格早于当前比赛）
            lo_idx = int(np.searchsorted(td[:ti], lo, side='right'))
            return float(ti - lo_idx)

        h_g7 = games_in_window(h, 7)
        a_g7 = games_in_window(a, 7)
        h_g14 = games_in_window(h, 14)
        a_g14 = games_in_window(a, 14)

        # --- 连续客场（主场队：连续客场；客队：连续客场）---
        def away_streak(team):
            ih = is_home.get(team)
            if ih is None:
                return 0.0
            ti = int(np.searchsorted(dates[team], d_ord, side='left'))
            s = 0
            for j in range(ti - 1, -1, -1):
                if ih[j] == 0:
                    s += 1
                else:
                    break
            return float(s)

        # --- 近 5 场场均积分 ---
        def ppg_last5(team):
            p = points.get(team)
            if p is None:
                return np.nan
            ti = int(np.searchsorted(dates[team], d_ord, side='left'))
            if ti == 0:
                return np.nan
            lo = max(0, ti - 5)
            return float(np.mean(p[lo:ti]))

        h_ppg = ppg_last5(h)
        a_ppg = ppg_last5(a)

        # --- 德比 / 联赛经验差 ---
        is_derby = 1.0 if frozenset({h, a}) in DERBY_PAIRS else 0.0
        h_exp = _league_exp_prior(dates, season, league, h, lg, d_ord, sea)
        a_exp = _league_exp_prior(dates, season, league, a, lg, d_ord, sea)

        out[i, :] = [
            h_rest, a_rest, (h_rest - a_rest) if (not np.isnan(h_rest) and not np.isnan(a_rest)) else np.nan,
            h_g7, a_g7, h_g14, a_g14,
            away_streak(h), away_streak(a),
            h_ppg, a_ppg, (h_ppg - a_ppg) if (not np.isnan(h_ppg) and not np.isnan(a_ppg)) else np.nan,
            is_derby, (h_exp - a_exp),
        ]

    feat = pd.DataFrame(out, index=df.index, columns=FEATURE_COLS)

    # 缺失值：先按联赛中位数，再按全局中位数兜底（与 build_team_features 同策略）
    feat = feat.replace([np.inf, -np.inf], np.nan)
    has_league = df['competition_name'].notna().all() if 'competition_name' in df.columns else False
    if has_league:
        try:
            league_med = feat.groupby(df['competition_name']).transform('median')
            feat = feat.fillna(league_med)
        except Exception:
            pass
    feat = feat.fillna(feat.median())
    feat = feat.fillna(0.0)

    return feat


if __name__ == '__main__':
    from feature_utils import load_match_data_odds, build_all_features

    df = load_match_data_odds()
    f = build_contextual_features(df)
    print(f"情境特征维度: {f.shape}")
    print("非零率:")
    for c in FEATURE_COLS:
        nz = (f[c] != 0).mean() * 100
        print(f"   {c}: {nz:.1f}%")
    print("样本：")
    print(f.head(3))