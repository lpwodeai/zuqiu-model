# -*- coding: utf-8 -*-
"""
build_feature_bridge.py — P0-D 114 维数据源缺口 Python→JS 桥接
====================================================================
目标：把训练侧独有的 114 维特征（sofa_* 68 / pa_* 24 / mkt_* 10 / odds_ts_* 12）
按与训练一致的公式在 Python 侧预计算，导出为 JS serving 可直接读取的真实值，
替换当前 JS `normalizeFeatures` 里对缺口特征的「均值填充 → 归一化 0」。

各组数据源与可计算性（对未来赛程）：
  sofa_* 68  : sofascore_team_features 表（已含未来赛程的预计算列）→ 可计算
  pa_*   24  : 同上（阵容/伤停/疲劳等）→ 可计算
  mkt_*  10  : odds500_match ⋈ odds500_ouzhi_summary（百家欧指共识 6 维）→ 可计算；
               dev 4 维（mkt_dev_*）依赖竞彩实时隐含概率，由 JS serving 侧计算
  odds_ts_* 12 : wdl_history / handicap_history / total_goals_history
               → 仅历史赛程有完整多时点序列（未来赛程常仅 1 快照，按训练语义
                  timestamp < MAX_VALID_TIMESTAMP 过滤后为空，正确回退均值填充）

约定：
  - 只输出「有真实值」的键；缺失键不输出，JS 端继续走均值填充（归一化 0）。
  - 输出值为训练同口径的原始值（未做 z-score），JS 端 `normalizeFeatures` 再做标准化。
  - 队名/联赛键用中文（与 sofascore_team_features / odds500_match 一致），
    JS 端经 LEAGUE_NAME_MAP + normalize_team_name 对齐。

用法：
  # 单场探针（打印 114 维中实际可算出的键与值）
  python scripts/build_feature_bridge.py --probe --league 西甲 \
      --home 塞维利亚 --away 巴伦西亚 --date 2026-09-11

  # 全量导出（未来赛程 → JSON，供 JS serving 加载）
  python scripts/build_feature_bridge.py --export --out assets/feature_bridge.json \
      [--date-from 2026-09-11]
"""
from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from feature_utils import normalize_team_name  # noqa: E402
from d013_temporal_odds import (  # noqa: E402
    MAX_VALID_TIMESTAMP,
    _GOAL_COLS,
    _wdl_feats_np,
    _hcp_feats_np,
    _ou_feats_np,
    _compute_cross_market,
)

ODDS_DB = BASE_DIR / "data" / "odds.db"
FEATURES_PKL = BASE_DIR / "assets" / "selected_features_20260908_004604.pkl"

_GAP_PREFIXES = ("sofa_", "pa_", "mkt_", "odds_ts_")


# ============================================================
# 特征名清单（单一来源：训练选特征 pkl）
# ============================================================
def load_gap_feature_names():
    with open(FEATURES_PKL, "rb") as f:
        d = pickle.load(f)
    feats = [str(x) for x in (d if isinstance(d, list)
                              else d.get("features", d.get("feature_names", [])))]
    return [x for x in feats if x.startswith(_GAP_PREFIXES)]


def _connect():
    conn = sqlite3.connect(str(ODDS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


# ============================================================
# 组 1：sofa_* (68) + pa_* (24) — sofascore_team_features
# ============================================================
def load_sofa_pa(conn, league_cn, home_cn, away_cn, date=None):
    """读取 sofa_*/pa_* 共 92 维（先正向后反向匹配，再退化为不筛联赛取最近一场）。"""
    sofa_cols = [f"sofa_{i}" for i in []]
    # 实际列名以表结构为准
    all_cols = [r[1] for r in conn.execute("PRAGMA table_info(sofascore_team_features)")]
    target_cols = [c for c in all_cols if c.startswith("sofa_") or c.startswith("pa_")]

    def _pick(rows):
        if not rows:
            return None
        if date:
            dated = [r for r in rows if r["match_date"] and str(r["match_date"]) <= date]
            if dated:
                return max(dated, key=lambda r: str(r["match_date"]))
        return rows[-1]  # 最新一场

    row = None
    for h, a in ((home_cn, away_cn), (away_cn, home_cn)):
        if league_cn:
            rows = conn.execute(
                "SELECT * FROM sofascore_team_features "
                "WHERE home_team_cn=? AND away_team_cn=? AND league=?",
                (h, a, league_cn)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sofascore_team_features "
                "WHERE home_team_cn=? AND away_team_cn=?",
                (h, a)).fetchall()
        row = _pick(rows)
        if row is not None:
            break

    if row is None:
        return {}
    return {c: row[c] for c in target_cols if row[c] is not None}


# ============================================================
# 组 2：mkt_* 共识 6 维 — odds500_match ⋈ odds500_ouzhi_summary
# ============================================================
def _to_float(v, default=None):
    try:
        if v is None:
            return default
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).strip().replace("%", "").replace(",", "")
        if s in ("", "nan", "None", "inf", "-inf"):
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


def load_mkt_consensus(conn, home_cn, away_cn, date=None):
    """返回 mkt_imp_win/draw/lose + dispersion + company_count + return 共 6 维。

    mkt_dev_*（4 维）依赖竞彩实时隐含概率，由 JS serving 侧计算（见 feature-bridge.js）。
    """
    sql = (
        "SELECT m.home_team_cn, m.away_team_cn, m.match_date, "
        "s.company_count, s.avg_prob_init_win, s.avg_prob_init_draw, s.avg_prob_init_lose, "
        "s.disp_init_win, s.disp_init_draw, s.disp_init_lose, s.avg_return_init "
        "FROM odds500_match m JOIN odds500_ouzhi_summary s ON m.match_id = s.match_id "
        "WHERE m.home_team_cn=? AND m.away_team_cn=?"
    )
    params = []
    row = None
    for h, a in ((home_cn, away_cn), (away_cn, home_cn)):
        rows = conn.execute(sql, (h, a)).fetchall()
        if not rows:
            continue
        if date:
            dated = [r for r in rows if r["match_date"] and str(r["match_date"]) <= date]
            if dated:
                row = max(dated, key=lambda r: str(r["match_date"]))
                break
        row = rows[-1]
        if row is not None:
            break

    if row is None:
        return {}

    p_win = _to_float(row["avg_prob_init_win"]) or 0.0
    p_draw = _to_float(row["avg_prob_init_draw"]) or 0.0
    p_lose = _to_float(row["avg_prob_init_lose"]) or 0.0
    p_win, p_draw, p_lose = p_win / 100.0, p_draw / 100.0, p_lose / 100.0
    tot = p_win + p_draw + p_lose
    if tot <= 0:
        return {}

    disp = [_to_float(row["disp_init_win"]), _to_float(row["disp_init_draw"]),
            _to_float(row["disp_init_lose"])]
    disp = [d for d in disp if d is not None]
    company = _to_float(row["company_count"])
    ret = _to_float(row["avg_return_init"])

    out = {
        "mkt_imp_win": p_win / tot,
        "mkt_imp_draw": p_draw / tot,
        "mkt_imp_lose": p_lose / tot,
    }
    if disp:
        out["mkt_dispersion"] = float(np.mean(disp))
    if company is not None:
        out["mkt_company_count"] = company
    if ret is not None:
        out["mkt_return"] = ret
    return out


# ============================================================
# 组 3：odds_ts_* (12) — 三大赔率时序表（中文 match_id = date_主_客）
# ============================================================
def load_odds_ts(conn, home_cn, away_cn, date):
    if not date:
        return {}
    # 历史时序表的 match_id 用「原始中文名」，与 canonical 名可能不一致
    # （如 纽卡斯尔 vs 纽卡斯尔联），故尝试多组队名变体定位 match_id。
    h_n, a_n = normalize_team_name(home_cn), normalize_team_name(away_cn)
    name_pairs = []
    for (h, a) in ((home_cn, away_cn), (away_cn, home_cn),
                   (h_n, a_n), (a_n, h_n)):
        if (h, a) not in name_pairs:
            name_pairs.append((h, a))

    mid = None
    for h, a in name_pairs:
        cand = f"{date}_{h}_{a}"
        for t in ("wdl_history", "handicap_history", "total_goals_history"):
            n = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE match_id=?", (cand,)).fetchone()[0]
            if n:
                mid = cand
                break
        if mid:
            break
    if not mid:
        return {}

    def _wdl():
        rows = conn.execute(
            "SELECT win_a, draw, win_b FROM wdl_history "
            "WHERE match_id=? AND timestamp < ? ORDER BY timestamp",
            (mid, MAX_VALID_TIMESTAMP)).fetchall()
        if not rows:
            return None
        arr = np.array([[r["win_a"], r["draw"], r["win_b"]] for r in rows], dtype=float)
        return _wdl_feats_np(arr[:, 0], arr[:, 1], arr[:, 2])

    def _hcp():
        rows = conn.execute(
            "SELECT hcp_win, hcp_draw, hcp_lose FROM handicap_history "
            "WHERE match_id=? AND timestamp < ? ORDER BY timestamp",
            (mid, MAX_VALID_TIMESTAMP)).fetchall()
        if not rows:
            return None
        arr = np.array([[r["hcp_win"], r["hcp_draw"], r["hcp_lose"]] for r in rows], dtype=float)
        return _hcp_feats_np(arr[:, 0], arr[:, 1], arr[:, 2])

    def _ou():
        rows = conn.execute(
            f"SELECT {', '.join(_GOAL_COLS)} FROM total_goals_history "
            "WHERE match_id=? AND timestamp < ? ORDER BY timestamp",
            (mid, MAX_VALID_TIMESTAMP)).fetchall()
        if not rows:
            return None
        arr = np.array([[r[c] for c in _GOAL_COLS] for r in rows], dtype=float)
        return _ou_feats_np(arr)

    wdl_f, hcp_f, ou_f = _wdl(), _hcp(), _ou()

    out = {}
    for f in (wdl_f, hcp_f, ou_f):
        if f:
            out.update({k: v for k, v in f.items() if k.startswith("odds_ts_")})
    # 跨盘口一致性仅在 WDL 与让球盘同时有真实时序时才有意义
    if wdl_f and hcp_f:
        xmkt = _compute_cross_market(wdl_f, hcp_f)
        out.update({k: v for k, v in xmkt.items() if k.startswith("odds_ts_")})

    # 单快照时漂移/波动率自然为 0，属正常（无时序信息）
    return {k: float(v) for k, v in out.items()}


# ============================================================
# 汇总
# ============================================================
def compute_match_features(conn, league_cn, home_cn, away_cn, date=None):
    h = normalize_team_name(home_cn)
    a = normalize_team_name(away_cn)
    out = {}
    out.update(load_sofa_pa(conn, league_cn, h, a, date))
    out.update(load_mkt_consensus(conn, h, a, date))
    out.update(load_odds_ts(conn, home_cn, away_cn, date))
    # 仅保留 114 维名（剔除偶发的内部键），且值须为数字
    cap = set(load_gap_feature_names())
    result = {}
    for k, v in out.items():
        if k not in cap:
            continue
        fv = _to_float(v)
        if fv is not None:
            result[k] = fv
    return result


# ============================================================
# 导出：把所有未来赛程（sofascore 近未来 + 时序表近未来）并集导出为 JSON
# ============================================================
def _iter_upcoming_matches(conn, date_from):
    keys = {}
    for row in conn.execute(
        "SELECT match_date, league, home_team_cn, away_team_cn "
        "FROM sofascore_team_features WHERE match_date >= ?", (date_from,)).fetchall():
        keys[(row["home_team_cn"], row["away_team_cn"], row["match_date"], row["league"])] = 1
    # 赔率时序表补充（中文 match_id = date_主_客）
    for t in ("wdl_history", "handicap_history"):
        for (mid,) in conn.execute(
            f"SELECT DISTINCT match_id FROM {t} WHERE match_id LIKE '____-__-__\\_%'").fetchall():
            if not mid:
                continue
            parts = mid.split("_")
            if len(parts) < 3:
                continue
            d, h, a = parts[0], parts[1], parts[2]
            if d >= date_from:
                keys.setdefault((h, a, d, ""), 1)
    return [{"date": k[2], "league": k[3], "home": k[0], "away": k[1]}
            for k in keys.keys()]


def export_bridge(conn, date_from, out_path):
    matches = _iter_upcoming_matches(conn, date_from)
    bridge = {}
    gap_names = load_gap_feature_names()
    for m in matches:
        feats = compute_match_features(conn, m["league"], m["home"], m["away"], m["date"])
        if not feats:
            continue
        key = f"{m['date']}|{normalize_team_name(m['home'])}|{normalize_team_name(m['away'])}"
        bridge[key] = feats
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"generated": len(matches), "gap_features": gap_names,
                   "records": bridge}, f, ensure_ascii=False, indent=1)
    print(f"[feature_bridge] 导出 {len(bridge)} 场 → {out_path}")


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="P0-D 114 维数据源缺口 Python→JS 桥接")
    ap.add_argument("--probe", action="store_true", help="单场探针")
    ap.add_argument("--export", action="store_true", help="全量导出未来赛程桥接")
    ap.add_argument("--league", default="")
    ap.add_argument("--home", default="")
    ap.add_argument("--away", default="")
    ap.add_argument("--date", default="")
    ap.add_argument("--date-from", default="2026-09-11")
    ap.add_argument("--out", default=str(BASE_DIR / "assets" / "feature_bridge.json"))
    args = ap.parse_args()

    conn = _connect()
    try:
        if args.probe:
            feats = compute_match_features(conn, args.league, args.home, args.away, args.date)
            covered = sum(1 for _ in feats)
            total = len(load_gap_feature_names())
            print(f"[feature_bridge] {args.home} vs {args.away} ({args.date}): "
                  f"覆盖 {covered}/{total} 维")
            for k, v in sorted(feats.items()):
                print(f"  {k} = {v:.6f}")
        elif args.export:
            export_bridge(conn, args.date_from, args.out)
        else:
            ap.print_help()
    finally:
        conn.close()


if __name__ == "__main__":
    main()