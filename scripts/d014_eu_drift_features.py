# -*- coding: utf-8 -*-
"""
D-014 欧指漂移特征模块（C-20260923 新增）
================================================

数据源: odds.db 的 `odds500_ouzhi_summary`（百家欧指 avg_init→avg_live）
        + `odds500_match`（队名/日期对齐锚点）。

与 D-013 时序赔率漂移的差别:
    - D-013: 竞彩 wdl_history 时序末-首漂移（去水概率维度，单市场）
    - D-014: 欧指 avg_init → avg_live 漂移（赔率相对变化维度，跨市场共识）
    两者互补：D-013 是竞彩官方盘口自身漂移，D-014 是 57 家欧指共识漂移。

漂移惩罚校准结论（calibrate_drift_penalty.py）:
    β=-0.0126, CI=[-0.0667, 0.0415] 含 0, p=0.64
    → 漂移作为 EV 惩罚层无预测力（k=0）
    → 但作为主模型赛果预测特征可能有 RPS 增量（本模块验证）

特征（8 维）:
  1. eu_drift_home:       主胜赔率相对变化 (live-init)/init，>0 上升=变冷
  2. eu_drift_draw:       平局赔率相对变化
  3. eu_drift_away:       客胜赔率相对变化
  4. eu_drift_fav:        热门方向（home/away 中赔率较低者）的 drift
  5. eu_drift_ud:         冷门方向（home/away 中赔率较高者）的 drift
  6. eu_drift_magnitude:  三方向绝对漂移幅度之和 Σ|drift_i|
  7. eu_drift_entropy:    live 隐含概率熵 - init 隐含概率熵（负值=市场更确信）
  8. eu_drift_missing:    0/1 缺失标志（数据缺失时填 1，其他特征填 0.0）

train/serve 同源约束（project_memory 硬约束）:
    - 训练时：avg_live 是赛前最后欧指即时赔率（已完场回看）
    - 推理时：avg_live 是当前抓取的即时赔率（赛前一天仍在变化）
    - 用相对变化（drift 比例）而非绝对赔率值，部分缓解 train/serve 时点差异
    - 数据缺失时走 missing 分支：eu_drift_missing=1 + 其他特征填 0.0（中性=无漂移先验）
      禁止静默默认（与 upset_data_missing 设计一致，防 train/serve 分布漂移）

对齐口径:
    - 队名经 normalize_team_name 归一
    - (date, home_cn, away_cn) 三键与训练 df 严格对齐
    - 同场多抓取去重 keep=last（取最新快照）
"""

from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from feature_utils import normalize_team_name  # noqa: E402
from db_utils import connect, read_sql  # noqa: E402

DB_PATH = os.path.join(_PROJECT_ROOT, "data", "odds.db")

FEATURE_COLS = [
    "eu_drift_home", "eu_drift_draw", "eu_drift_away",
    "eu_drift_fav", "eu_drift_ud",
    "eu_drift_magnitude", "eu_drift_entropy",
    "eu_drift_missing",
]

# 缺失时的中性填充（drift=0 表示"无变化"作为先验，配合 missing=1 标志）
_MISSING_FILL = 0.0


def _to_float(v):
    """安全转 float，处理 None/空串/百分号/逗号。"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('%', '').replace(',', '')
    try:
        return float(s)
    except ValueError:
        return np.nan


def _drift(live: float, init: float) -> float:
    """计算相对漂移 (live - init) / init，init <= 0 时返回 nan。"""
    if not (init > 0) or not math.isfinite(init) or not math.isfinite(live):
        return np.nan
    return (live - init) / init


def _entropy(p_win: float, p_draw: float, p_lose: float) -> float:
    """三向概率的香农熵（自然对数底）。任一概率为 nan 返回 nan。"""
    ps = [p_win, p_draw, p_lose]
    if any(not math.isfinite(p) for p in ps):
        return np.nan
    s = sum(ps)
    if s <= 0:
        return np.nan
    ps = [p / s for p in ps]
    return -sum(p * math.log(p) for p in ps if p > 0)


def _implied_probs(o_win: float, o_draw: float, o_lose: float):
    """从赔率算去水隐含概率 (1/odds 归一)。"""
    if not all(o > 0 and math.isfinite(o) for o in [o_win, o_draw, o_lose]):
        return np.nan, np.nan, np.nan
    p_w = 1.0 / o_win
    p_d = 1.0 / o_draw
    p_l = 1.0 / o_lose
    s = p_w + p_d + p_l
    if s <= 0:
        return np.nan, np.nan, np.nan
    return p_w / s, p_d / s, p_l / s


def _load_eu_drift():
    """载入百家欧指初赔→即时赔（odds500_match ⋈ odds500_ouzhi_summary）。"""
    conn = connect(db_path=DB_PATH)
    try:
        omatch = read_sql(
            "SELECT match_id, home_team_cn, away_team_cn, match_date FROM odds500_match",
            conn,
        )
        summary = read_sql(
            "SELECT match_id, "
            "avg_init_win, avg_init_draw, avg_init_lose, "
            "avg_live_win, avg_live_draw, avg_live_lose "
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

    # 解析数值
    for c in ["avg_init_win", "avg_init_draw", "avg_init_lose",
              "avg_live_win", "avg_live_draw", "avg_live_lose"]:
        df[c] = df[c].apply(_to_float)

    # 计算三方向漂移
    df["drift_home"] = df.apply(
        lambda r: _drift(r["avg_live_win"], r["avg_init_win"]), axis=1)
    df["drift_draw"] = df.apply(
        lambda r: _drift(r["avg_live_draw"], r["avg_init_draw"]), axis=1)
    df["drift_away"] = df.apply(
        lambda r: _drift(r["avg_live_lose"], r["avg_init_lose"]), axis=1)

    # 热门/冷门方向漂移
    def _fav_ud(row):
        iw, il = row["avg_init_win"], row["avg_init_lose"]
        if not (math.isfinite(iw) and math.isfinite(il) and iw > 0 and il > 0):
            return np.nan, np.nan
        if iw <= il:
            # 主胜是热门
            return row["drift_home"], row["drift_away"]
        else:
            # 客胜是热门
            return row["drift_away"], row["drift_home"]
    fav_ud = df.apply(_fav_ud, axis=1, result_type="expand")
    df["drift_fav"] = fav_ud[0]
    df["drift_ud"] = fav_ud[1]

    # 总漂移幅度
    df["drift_magnitude"] = df[["drift_home", "drift_draw", "drift_away"]].abs().sum(axis=1)

    # 熵变化
    def _entropy_change(row):
        iw, id_, il = row["avg_init_win"], row["avg_init_draw"], row["avg_init_lose"]
        lw, ld_, ll = row["avg_live_win"], row["avg_live_draw"], row["avg_live_lose"]
        p_init = _implied_probs(iw, id_, il)
        p_live = _implied_probs(lw, ld_, ll)
        e_init = _entropy(*p_init)
        e_live = _entropy(*p_live)
        if not math.isfinite(e_init) or not math.isfinite(e_live):
            return np.nan
        return e_live - e_init
    df["drift_entropy"] = df.apply(_entropy_change, axis=1)

    # 有效行标记（三方向 drift 均 finite）
    df["_valid"] = df[["drift_home", "drift_draw", "drift_away"]].notna().all(axis=1)

    # 去重（同场多抓取取最新）
    df = df.drop_duplicates(subset=["date", "home_cn", "away_cn"], keep="last")

    cols = ["date", "home_cn", "away_cn",
            "drift_home", "drift_draw", "drift_away",
            "drift_fav", "drift_ud",
            "drift_magnitude", "drift_entropy", "_valid"]
    return df[cols]


def build_d014_features(df: pd.DataFrame) -> pd.DataFrame:
    """返回与训练 df 对齐的欧指漂移特征（index 同 df.index，8 维）。

    缺失场次（无欧指覆盖或数据无效）：
        - eu_drift_missing = 1
        - 其他 7 个连续特征填 0.0（中性=无漂移先验，由 missing 标志区分）
    """
    drift = _load_eu_drift()
    out = pd.DataFrame(index=df.index, columns=FEATURE_COLS, dtype=float)
    # 默认全部 missing
    out.loc[:, :] = 0.0
    out["eu_drift_missing"] = 1

    if drift.empty:
        print("   [WARN] D-014 欧指漂移数据为空，全部填 missing")
        return out

    df_key = df[["date", "home_team_name", "away_team_name"]].copy()
    df_key["date"] = pd.to_datetime(df_key["date"]).dt.normalize()
    df_key["_idx"] = np.arange(len(df_key))

    merged = df_key.merge(
        drift,
        left_on=["date", "home_team_name", "away_team_name"],
        right_on=["date", "home_cn", "away_cn"],
        how="left",
    )
    merged = merged.sort_values("_idx").reset_index(drop=True)

    valid_mask = merged["_valid"] == True  # noqa: E712
    coverage = valid_mask.mean() * 100 if len(merged) else 0
    n_valid = int(valid_mask.sum())
    print(f"   [D-014] 欧指漂移对齐覆盖率: {coverage:.1f}% ({n_valid}/{len(merged)})")

    # 有效行填充实际值，无效行保持 missing 默认
    out["eu_drift_home"] = np.where(valid_mask, merged["drift_home"].values, _MISSING_FILL)
    out["eu_drift_draw"] = np.where(valid_mask, merged["drift_draw"].values, _MISSING_FILL)
    out["eu_drift_away"] = np.where(valid_mask, merged["drift_away"].values, _MISSING_FILL)
    out["eu_drift_fav"] = np.where(valid_mask, merged["drift_fav"].values, _MISSING_FILL)
    out["eu_drift_ud"] = np.where(valid_mask, merged["drift_ud"].values, _MISSING_FILL)
    out["eu_drift_magnitude"] = np.where(valid_mask, merged["drift_magnitude"].values, _MISSING_FILL)
    out["eu_drift_entropy"] = np.where(valid_mask, merged["drift_entropy"].values, _MISSING_FILL)
    out["eu_drift_missing"] = np.where(valid_mask, 0, 1)

    return out


if __name__ == "__main__":
    # 自测：加载训练 df 看覆盖率
    from feature_utils import load_match_data_odds

    df = load_match_data_odds()
    f = build_d014_features(df)
    print(f"\nD-014 漂移特征维度: {f.shape}")
    print("\n覆盖率/统计:")
    for c in FEATURE_COLS:
        if c == "eu_drift_missing":
            n_miss = int(f[c].sum())
            print(f"   {c}: missing={n_miss} ({n_miss/len(f)*100:.1f}%)")
        else:
            valid = f[c][f["eu_drift_missing"] == 0]
            if len(valid) > 0:
                print(f"   {c}: mean={valid.mean():.4f} std={valid.std():.4f} "
                      f"min={valid.min():.4f} max={valid.max():.4f}")
            else:
                print(f"   {c}: 无有效数据")
    print("\n前 3 行样本:")
    print(f.head(3))
