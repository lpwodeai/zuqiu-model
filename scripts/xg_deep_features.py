# -*- coding: utf-8 -*-
"""
P1-8: xG 差值趋势 + 联赛分位特征
===================================

数据源: odds.db 的 SofaScore 球员级 xG（`match_player_stats.expected_goals`）
        + `fbref_match_mapping`（队名/联赛/日期对齐）。

特征（主客各 3 维，共 6 维）:
  - xg_diff_recent_{home,away}: 近 N 场 (xG - xGA) 均值（当前 xG 净差值水平）
  - xg_diff_trend_{home,away}: 近 N 场 (xG - xGA) 线性斜率（动量信号）
  - xg_diff_pct_{home,away}: 该队近 N 场 xG 净差值在同联赛各队中的分位（0~1，相对强弱）

说明:
  - 复用 `features.sofascore_pre_match_features` 的 loaders 与 `normalize_team_name`，
    队名/日期与训练 df（中文归一化）严格对齐。
  - 趋势/分位均「截止比赛当日」计算（严格早于当前比赛），无数据泄漏。
  - 缺失（无 xG 历史）统一填充 -1.0（与 sofa 特征 sentinel 一致）。
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

# 使 features 包可导入（项目根）+ scripts 内模块可导入
# scripts/xg_deep_features.py 的 dirname(dirname(...)) = 项目根（scripts 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from features.sofascore_pre_match_features import (  # noqa: E402
    load_sofascore_matches,
    load_sofascore_player_stats,
)
from feature_utils import normalize_team_name  # noqa: E402
from db_utils import connect  # noqa: E402

DB_PATH = os.path.join(_PROJECT_ROOT, "data", "odds.db")

# 趋势/分位窗口（近 N 场）
N_RECENT = 8

# 高频查询的 league 干净名（训练 competition_name 可能带「2017-2018赛季」后缀）
_LEAGUES = ("英超", "意甲", "西甲", "德甲", "法甲")


def _clean_league(name):
    if name is None:
        return None
    s = str(name)
    for lg in _LEAGUES:
        if s.startswith(lg):
            return lg
    return s


def _team_series(matches_df, stats_df):
    """构建每队（中文归一化名）的升序 (date_ordinal, xg_diff, league) 序列。

    返回: dict[team -> (dates: np.ndarray[int], diffs: np.ndarray[float], leagues: list[str])]
    """
    # 每队每场 xG（按 SofaScore 英文队名分组求和）
    xg = (
        stats_df.groupby(["event_id", "team"], as_index=False)["expected_goals"]
        .sum()
        .rename(columns={"expected_goals": "xg"})
    )

    m = matches_df[
        ["event_id", "home_team", "away_team", "home_team_cn", "away_team_cn",
         "league", "match_date"]
    ].copy()

    m = m.merge(
        xg.rename(columns={"team": "home_team", "xg": "xg_home"}),
        on=["event_id", "home_team"], how="left",
    )
    m = m.merge(
        xg.rename(columns={"team": "away_team", "xg": "xg_away"}),
        on=["event_id", "away_team"], how="left",
    )
    m = m.dropna(subset=["xg_home", "xg_away"])

    series = {}
    for _, r in m.iterrows():
        d_ord = pd.Timestamp(r["match_date"]).toordinal()
        home_cn = r["home_team_cn"]       # 已在 loader 内归一化
        away_cn = r["away_team_cn"]
        lg = r["league"]
        series.setdefault(home_cn, []).append((d_ord, float(r["xg_home"] - r["xg_away"]), lg))
        series.setdefault(away_cn, []).append((d_ord, float(r["xg_away"] - r["xg_home"]), lg))

    out = {}
    for team, rows in series.items():
        rows = sorted(rows, key=lambda x: x[0])
        out[team] = (
            np.array([r[0] for r in rows], dtype=int),
            np.array([r[1] for r in rows], dtype=float),
            [r[2] for r in rows],
        )
    return out


def _window(team, date_ord, series, n):
    """返回该队 date_ord 之前（严格 <）最近 n 场的 (dates, diffs)；无则 (None, None)。"""
    rec = series.get(team)
    if rec is None:
        return None, None
    dates, diffs, _ = rec
    idx = int(np.searchsorted(dates, date_ord, side="left"))
    if idx == 0:
        return None, None
    lo = max(0, idx - n)
    return dates[lo:idx], diffs[lo:idx]


def _slope(dates, diffs):
    """近 n 场 xg_diff 的线性斜率；点不足 2 返回 0。"""
    if diffs is None or len(diffs) < 2:
        return 0.0
    x = dates.astype(float)
    if np.ptp(x) == 0:
        x = np.arange(len(diffs), dtype=float)
    return float(np.polyfit(x, diffs, 1)[0])


def build_xg_deep_features(df):
    """返回与训练 df 对齐的 xG 深度特征（index 同 df.index，6 维，缺失填 -1.0）。"""
    conn = connect(db_path=DB_PATH)
    try:
        matches_df = load_sofascore_matches(conn)
        stats_df = load_sofascore_player_stats(conn)
    finally:
        conn.close()

    if matches_df.empty or stats_df.empty:
        return pd.DataFrame(index=df.index)

    series = _team_series(matches_df, stats_df)

    # 联赛名 → 该联赛所有中文队名（用于分位），取该队最后一场所属联赛（赛季内固定）
    league_teams = defaultdict(list)
    for team in series:
        lg = series[team][2][-1] if series[team][2] else None
        if lg:
            league_teams[lg].append(team)

    df_dates = pd.to_datetime(df["date"]).map(pd.Timestamp.toordinal).values
    leagues = df["competition_name"].map(_clean_league).values
    home_names = df["home_team_name"].values
    away_names = df["away_team_name"].values

    n_rows = len(df)
    rec_h, rec_a = [np.nan] * n_rows, [np.nan] * n_rows
    tr_h, tr_a = [np.nan] * n_rows, [np.nan] * n_rows
    pct_h, pct_a = [np.nan] * n_rows, [np.nan] * n_rows

    for i in range(n_rows):
        d_ord = df_dates[i]
        lg = leagues[i]
        hn = normalize_team_name(home_names[i])
        an = normalize_team_name(away_names[i])

        h_dates, h_diffs = _window(hn, d_ord, series, N_RECENT)
        a_dates, a_diffs = _window(an, d_ord, series, N_RECENT)

        h_recent = float(np.mean(h_diffs)) if (h_diffs is not None and len(h_diffs)) else np.nan
        a_recent = float(np.mean(a_diffs)) if (a_diffs is not None and len(a_diffs)) else np.nan
        rec_h[i], rec_a[i] = h_recent, a_recent
        tr_h[i] = _slope(h_dates, h_diffs) if h_diffs is not None else np.nan
        tr_a[i] = _slope(a_dates, a_diffs) if a_diffs is not None else np.nan

        # 同联赛各队「截至当日近 N 场 xg_diff 均值」→ 分位
        h_pct, a_pct = np.nan, np.nan
        if lg in league_teams:
            vals = []
            for t in league_teams[lg]:
                _, td = _window(t, d_ord, series, N_RECENT)
                if td is not None and len(td):
                    vals.append(float(np.mean(td)))
            if vals:
                if not np.isnan(h_recent):
                    h_pct = float(np.mean([v < h_recent for v in vals]))
                if not np.isnan(a_recent):
                    a_pct = float(np.mean([v < a_recent for v in vals]))
        pct_h[i], pct_a[i] = h_pct, a_pct

    out = pd.DataFrame(
        {
            "xg_diff_recent_home": rec_h,
            "xg_diff_recent_away": rec_a,
            "xg_diff_trend_home": tr_h,
            "xg_diff_trend_away": tr_a,
            "xg_diff_pct_home": pct_h,
            "xg_diff_pct_away": pct_a,
        },
        index=df.index,
    )
    return out.fillna(-1.0)


FEATURE_COLS = [
    "xg_diff_recent_home", "xg_diff_recent_away",
    "xg_diff_trend_home", "xg_diff_trend_away",
    "xg_diff_pct_home", "xg_diff_pct_away",
]