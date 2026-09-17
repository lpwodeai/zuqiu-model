# -*- coding: utf-8 -*-
"""
P1-11: 多博彩公司赔率一致性（竞彩 vs 百家欧指共识偏离度）
========================================================

数据源: odds.db 的 `odds500_ouzhi_summary`（57 家欧指共识/均价/分歧度）
        + `odds500_match`（队名/日期对齐锚点）。

特征（10 维）:
  A. 市场共识去水概率（3 维）— 百家欧指 avg_prob_init_*（本就约归一）
     - mkt_imp_win / mkt_imp_draw / mkt_imp_lose
  B. 竞彩-共识偏离度（3 维）— 竞彩隐含概率(X.wdl_implied_*) - 共识概率
     - mkt_dev_win / mkt_dev_draw / mkt_dev_lose
       dev > 0: 竞彩比市场更看好该结果（竞彩「过热」）
       dev < 0: 竞彩比市场更看淡该结果（市场共识「冷门」被低估）
  C. 总偏离度（1 维）— mkt_dev_abs = |dev_win|+|dev_draw|+|dev_lose|
  D. 庄家分歧度（1 维）— mkt_dispersion = mean(disp_init_win/draw/lose)
  E. 庄家覆盖度（1 维）— mkt_company_count
  F. 市场抽水（1 维）— mkt_return = avg_return_init（返还率，越低抽水越高）

说明:
  - 队名/日期经 normalize_team_name 归一，与训练 df 严格对齐（date + 主客中文名）。
  - 缺失（无百家欧指覆盖）统一填充 -1.0（与 sofa/xG sentinel 一致）。
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from feature_utils import normalize_team_name  # noqa: E402

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_PROJECT_ROOT, "data", "odds.db")
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from db_utils import connect, read_sql  # noqa: E402

FEATURE_COLS = [
    "mkt_imp_win", "mkt_imp_draw", "mkt_imp_lose",
    "mkt_dev_win", "mkt_dev_draw", "mkt_dev_lose",
    "mkt_dev_abs",
    "mkt_dispersion", "mkt_company_count", "mkt_return",
]


def _to_float(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('%', '').replace(',', '')
    try:
        return float(s)
    except ValueError:
        return np.nan


def _load_consensus():
    """载入百家欧指共识（odds500_match ⋈ odds500_ouzhi_summary），返回归一 DataFrame。"""
    conn = connect(db_path=DB_PATH)
    try:
        omatch = read_sql(
            "SELECT match_id, home_team_cn, away_team_cn, match_date FROM odds500_match",
            conn,
        )
        summary = read_sql(
            "SELECT match_id, company_count, "
            "avg_init_win, avg_init_draw, avg_init_lose, "
            "avg_prob_init_win, avg_prob_init_draw, avg_prob_init_lose, "
            "disp_init_win, disp_init_draw, disp_init_lose, "
            "avg_return_init "
            "FROM odds500_ouzhi_summary",
            conn,
        )
    finally:
        conn.close()

    if omatch.empty or summary.empty:
        return pd.DataFrame()

    df = omatch.merge(summary, on="match_id", how="inner")
    df["home_cn"] = df["home_team_cn"].apply(normalize_team_name)
    df["away_cn"] = df["away_team_cn"].apply(normalize_team_name)
    df["date"] = pd.to_datetime(df["match_date"], format="mixed").dt.normalize()

    # 解析数值列
    df["company_count"] = df["company_count"].apply(_to_float)
    df["disp_init_win"] = df["disp_init_win"].apply(_to_float)
    df["disp_init_draw"] = df["disp_init_draw"].apply(_to_float)
    df["disp_init_lose"] = df["disp_init_lose"].apply(_to_float)
    df["avg_return_init"] = df["avg_return_init"].apply(_to_float)

    # 共识去水概率：优先 avg_prob_init_*（约已归一）；缺失回退 1/avg_init_* 归一
    p_win = df["avg_prob_init_win"].apply(lambda v: _to_float(v) / 100.0)
    p_draw = df["avg_prob_init_draw"].apply(lambda v: _to_float(v) / 100.0)
    p_lose = df["avg_prob_init_lose"].apply(lambda v: _to_float(v) / 100.0)

    fallback = p_win.isna() | p_draw.isna() | p_lose.isna()
    if fallback.any():
        raw_w = df["avg_init_win"].apply(_to_float)
        raw_d = df["avg_init_draw"].apply(_to_float)
        raw_l = df["avg_init_lose"].apply(_to_float)
        inv_w = 1.0 / raw_w.replace(0, np.nan)
        inv_d = 1.0 / raw_d.replace(0, np.nan)
        inv_l = 1.0 / raw_l.replace(0, np.nan)
        tot = inv_w + inv_d + inv_l
        p_win = p_win.where(~fallback, inv_w / tot)
        p_draw = p_draw.where(~fallback, inv_d / tot)
        p_lose = p_lose.where(~fallback, inv_l / tot)

    # 再次归一（去水兜底）
    tot = (p_win + p_draw + p_lose).replace(0, np.nan)
    df["mkt_imp_win"] = (p_win / tot).astype(float)
    df["mkt_imp_draw"] = (p_draw / tot).astype(float)
    df["mkt_imp_lose"] = (p_lose / tot).astype(float)

    df["mkt_dispersion"] = (df[["disp_init_win", "disp_init_draw", "disp_init_lose"]]
                            .mean(axis=1))
    df["mkt_company_count"] = df["company_count"]
    df["mkt_return"] = df["avg_return_init"]

    # 去重（同一场比赛可能多盘口/多抓取）
    df = df.drop_duplicates(subset=["date", "home_cn", "away_cn"], keep="last")

    cols = ["date", "home_cn", "away_cn", "mkt_imp_win", "mkt_imp_draw",
            "mkt_imp_lose", "mkt_dispersion", "mkt_company_count", "mkt_return"]
    return df[cols]


def build_odds_consensus_features(df: pd.DataFrame, X: pd.DataFrame | None = None) -> pd.DataFrame:
    """返回与训练 df 对齐的赔率一致性特征（index 同 df.index，10 维，缺失填 -1.0）。

    偏离度 dev = 竞彩隐含概率(X.wdl_implied_*) - 市场共识概率(mkt_imp_*)。
    """
    cons = _load_consensus()
    if cons.empty:
        return pd.DataFrame(index=df.index, columns=FEATURE_COLS, dtype=float).fillna(-1.0)

    df_key = df[["date", "home_team_name", "away_team_name"]].copy()
    df_key["date"] = pd.to_datetime(df_key["date"]).dt.normalize()
    df_key["_idx"] = np.arange(len(df_key))

    merged = df_key.merge(
        cons,
        left_on=["date", "home_team_name", "away_team_name"],
        right_on=["date", "home_cn", "away_cn"],
        how="left",
    )
    merged = merged.sort_values("_idx").reset_index(drop=True)

    coverage = merged["mkt_imp_win"].notna().mean() * 100 if len(merged) else 0
    print(f"   [P1-11] 百家欧指共识对齐覆盖率: {coverage:.1f}% "
          f"({int(merged['mkt_imp_win'].notna().sum())}/{len(merged)})")

    out = pd.DataFrame(index=df.index)
    for c in ["mkt_imp_win", "mkt_imp_draw", "mkt_imp_lose",
              "mkt_dispersion", "mkt_company_count", "mkt_return"]:
        out[c] = merged[c].values

    # 偏离度：竞彩隐含概率 - 共识概率（X 已在 build_all_features 中构建完毕）
    if X is not None and "wdl_implied_win" in X.columns:
        out["mkt_dev_win"] = X["wdl_implied_win"].values - out["mkt_imp_win"]
        out["mkt_dev_draw"] = X["wdl_implied_draw"].values - out["mkt_imp_draw"]
        out["mkt_dev_lose"] = X["wdl_implied_lose"].values - out["mkt_imp_lose"]
    else:
        out["mkt_dev_win"] = np.nan
        out["mkt_dev_draw"] = np.nan
        out["mkt_dev_lose"] = np.nan

    out["mkt_dev_abs"] = (out["mkt_dev_win"].abs()
                          + out["mkt_dev_draw"].abs()
                          + out["mkt_dev_lose"].abs())

    out = out[FEATURE_COLS]
    return out.fillna(-1.0)


if __name__ == "__main__":
    from feature_utils import load_match_data_odds

    df = load_match_data_odds()
    # 仅测试共识概率部分（不含偏离度，X=None）
    f = build_odds_consensus_features(df, X=None)
    print(f"赔率一致性特征维度: {f.shape}")
    print("覆盖/统计:")
    for c in FEATURE_COLS:
        nz = (f[c] != -1.0).mean() * 100
        print(f"   {c}: 非sentinel {nz:.1f}%")
    print(f.head(3))