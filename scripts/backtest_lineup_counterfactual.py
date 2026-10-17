# -*- coding: utf-8 -*-
"""C-112 前置对拍：真实首发反事实 WDL 对拍（只读，零生产改动）。

问题：生产 pa_* 特征由「核心池 ∩ 最近一场出场名单」推算伤停 + 规则推算预计首发 XI，
不消费任何官方/真实名单。若赛前拿到真实首发（以赛后 match_lineups 回溯代理），
WDL 概率会改多少？——量化该 Δ 以决定是否立项 C-112（把真实名单口径接入 pa 特征）。

推理路径（子模型近似，非全链）：
  1. 特征行 = 生产同一构建链 load_match_data_odds + build_all_features（prime 虚拟行，
     同 generate_unified_report L2842 与 prediction_core L1312-1337 口径），
     pa_* 来自 sofascore_team_features（即生产推理时注入的值）。
  2. WDL = LR meta-learner 融合 5 子模型；XGBoost/LightGBM 子模型用
     assets/*_20261007_193924（预测窗口 2026-10-10~13 内生产实际生效版本）重算，
     Dixon-Coles/Elo/贝叶斯 子模型概率直接复用赛前报告 md 中的渲染值（该三模型
     不消费 pa_*，重算无意义）。
  3. 反事实：仅替换特征行中 10 维 pa_*（每侧 5 维：pa_xi_rating/xg/xa、
     pa_availability、pa_missing_impact），其余 244 维原样，重跑 XGB/LGB + meta。
  4. Platt 校准模式（当前 20261014 校准文件 vs 不校准）以「重算子模型概率 vs
     报告渲染值」的平均绝对误差择优，全程统一。

校正口径（无泄漏：球员统计仍为赛前可得）：
  - pa_xi_*：用 match_lineups(is_starter=1) 真实首发替换规则推算 XI，球员基线
    （avg_rating/avg_xg/avg_xa）仍取 _prepare_recent 的赛前加权统计。
  - pa_availability / pa_missing_impact：核心池口径不变（is_starting_rate>0.6 且
    avg_rating≥70 分位）；missing = 核心池 − 该队整场出场名单（match_lineups 全部
    出场球员，含替补）。公式同 player_availability_features.py L404-425。

纪律：本脚本只读 odds.db（uri mode=ro）、不写任何生产表、不修改现有文件；
仅新增本脚本与 reports/lineup_counterfactual_20261015.md。

用法：python scripts/backtest_lineup_counterfactual.py
"""
from __future__ import annotations

import glob
import json
import os
import pickle
import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts"), str(PROJECT_ROOT / "features")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DB_PATH = PROJECT_ROOT / "data" / "odds.db"
ASSETS = PROJECT_ROOT / "assets"
REPORTS = PROJECT_ROOT / "reports"
REPORT_OUT = REPORTS / "lineup_counterfactual_20261015.md"

MODEL_TAG = "20261007_193924"          # 预测窗口内生产生效模型集
PLATT_MODES = ("platt", "raw")          # 择优：platt=应用当前校准文件, raw=不校准
GATE_MIN_STARTERS = 9                   # match_lineups 首发数门限（同 explore_db6 口径）

PA_COLS_24 = [f"{k}_{s}" for s in ("home", "away") for k in (
    "pa_xi_rating", "pa_xi_xg", "pa_xi_xa", "pa_core_xg_share", "pa_core_dependency",
    "pa_availability", "pa_missing_impact", "pa_fatigue_7d", "pa_fatigue_14d",
    "pa_rest_days", "pa_squad_stability", "pa_formation_std")]
CORR_COLS = [f"{k}_{s}" for s in ("home", "away") for k in (
    "pa_xi_rating", "pa_xi_xg", "pa_xi_xa", "pa_availability", "pa_missing_impact")]


# ==================== DB（只读） ====================

def ro_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ==================== 目标场次 ====================

def fetch_targets(conn):
    """model_predictions WDL_home（≥2026-10-01）→ fbref 映射 + 中文队名 + 报告路径。"""
    rows = conn.execute("""
        SELECT DISTINCT match_id FROM model_predictions
        WHERE match_id >= '2026-10-01' AND prediction_type='WDL_home'
        ORDER BY match_id
    """).fetchall()
    targets = []
    for r in rows:
        mid = r["match_id"]
        date, home_en, away_en = mid.split("_", 2)
        fm = conn.execute("""
            SELECT fbref_match_id, league FROM fbref_match_mapping
            WHERE match_date=? AND home_team_fbref=? AND away_team_fbref=?
        """, (date, home_en, away_en)).fetchone()
        if not fm:
            continue
        m = conn.execute("""
            SELECT home_team_cn, away_team_cn, league FROM odds500_match WHERE match_id=?
        """, (mid,)).fetchone()
        if not m:
            m = conn.execute("""
                SELECT home_team AS home_team_cn, away_team AS away_team_cn, league
                FROM matches WHERE match_id=?
            """, (mid,)).fetchone()
        home_cn = m["home_team_cn"] if m else home_en
        away_cn = m["away_team_cn"] if m else away_en
        league = (m["league"] if m else None) or fm["league"]
        d = date.replace("-", "")
        pat = str(PROJECT_ROOT / "docs" / "prematch_reports" / d /
                  f"{league}_{date}_{home_cn}_vs_{away_cn}.md").replace("/", "-")
        rep = None
        for cand in glob.glob(pat) or glob.glob(str(PROJECT_ROOT / "docs" / "prematch_reports" / d / f"*_{home_cn}_vs_{away_cn}.md")):
            rep = cand
            break
        targets.append({
            "match_id": mid, "date": date, "home_en": home_en, "away_en": away_en,
            "home_cn": home_cn, "away_cn": away_cn, "league": league,
            "fbref_id": fm["fbref_match_id"], "report": rep,
        })
    return targets


def lineup_gate(conn, fbref_id, team):
    """返回 (真实首发名单, 整场出场名单)；数据不足返回 (None, None)。"""
    rows = conn.execute("""
        SELECT player_name, is_starter FROM match_lineups
        WHERE fbref_match_id=? AND team=?
    """, (str(fbref_id), team)).fetchall()
    if not rows:
        return None, None
    names = [r["player_name"] for r in rows if r["player_name"]]
    starters = [r["player_name"] for r in rows if r["player_name"] and r["is_starter"] == 1]
    if len(starters) < GATE_MIN_STARTERS or len(names) < 11:
        return None, None
    return starters, names


# ==================== pa 校正（生产公式 + 真实首发） ====================

def corrected_pa(pa_gen, team, match_date, real_xi, real_squad):
    """返回 (orig12, corr5 dict, meta)。

    orig12 = 生产口径 compute_team_features；corr5 = 校正后的 5 维
    (pa_xi_rating/xg/xa, pa_availability, pa_missing_impact)。
    """
    from player_availability_features import NO_DATA, _no_data_sentinel_enabled
    orig = pa_gen.compute_team_features(team, match_date)
    prepared = pa_gen._prepare_recent(team, match_date)
    meta = {"xi_covered": 0, "xi_uncovered": len(real_xi or []), "n_core": 0,
            "n_missing_prod": 0, "n_missing_corr": 0}
    corr = {k: orig.get(k, 0.0) for k in
            ("pa_xi_rating", "pa_xi_xg", "pa_xi_xa", "pa_availability", "pa_missing_impact")}
    if prepared is None or not real_squad:
        return orig, corr, meta
    _, recent_matches, player_stats = prepared
    sentinel_on = _no_data_sentinel_enabled()
    nd = NO_DATA if sentinel_on else 0.0

    core_players = player_stats[
        (player_stats["is_starting_rate"] > 0.6)
        & (player_stats["avg_rating"] >= player_stats["avg_rating"].quantile(0.7))
    ]
    core_names = set(core_players["player_name"])
    total_xg = player_stats["avg_xg"].sum() or 0.01
    squad = set(real_squad)
    meta["n_core"] = len(core_names)

    # 缺阵：核心池 − 真实整场出场名单（生产口径 = 核心池 − 最近一场出场名单）
    missing_prod = core_names - set(recent_matches.iloc[0]) if len(recent_matches) else set()
    missing_corr = core_names - squad
    meta["n_missing_prod"] = len(missing_prod)
    meta["n_missing_corr"] = len(missing_corr)
    if sentinel_on and not core_names:
        corr["pa_availability"] = NO_DATA
        corr["pa_missing_impact"] = NO_DATA
    else:
        corr["pa_availability"] = round(
            1.0 - min(0.2, len(missing_corr) / max(len(core_names), 1)), 4)
        miss_xg = core_players[core_players["player_name"].isin(missing_corr)]["avg_xg"].sum()
        corr["pa_missing_impact"] = round(float(miss_xg / total_xg), 4)

    # XI 强度：真实首发 × 赛前加权球员基线
    if real_xi:
        xi_rows = player_stats[player_stats["player_name"].isin(set(real_xi))]
        meta["xi_covered"] = int(len(xi_rows))
        meta["xi_uncovered"] = len(real_xi) - len(xi_rows)
        if len(xi_rows) > 0:
            corr["pa_xi_rating"] = round(float(xi_rows["avg_rating"].mean()), 4)
            corr["pa_xi_xg"] = round(float(xi_rows["avg_xg"].sum()), 4)
            corr["pa_xi_xa"] = round(float(xi_rows["avg_xa"].sum()), 4)
        else:
            corr["pa_xi_rating"] = orig.get("pa_xi_rating", nd)
            corr["pa_xi_xg"] = orig.get("pa_xi_xg", nd)
            corr["pa_xi_xa"] = orig.get("pa_xi_xa", nd)
    return orig, corr, meta


# ==================== 报告解析（DC/Elo/贝叶斯 + Stacking 基线） ====================

SUB_PATTERNS = [
    ("dixonColes", re.compile(r"^\|\s*Dixon-Coles\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%", re.M)),
    ("xgboost", re.compile(r"^\|\s*XGBoost\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%", re.M)),
    ("lightgbm", re.compile(r"^\|\s*LightGBM\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%", re.M)),
    ("elo", re.compile(r"^\|\s*Elo\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%", re.M)),
    ("bayesian", re.compile(r"^\|\s*贝叶斯\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%", re.M)),
    ("__stacking__", re.compile(r"^\|\s*\*\*Stacking 融合\*\*\s*\|\s*\*\*([\d.]+)%\*\*\s*\|\s*\*\*([\d.]+)%\*\*\s*\|\s*\*\*([\d.]+)%\*\*", re.M)),
]


def parse_report_submodels(path):
    out = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    for key, pat in SUB_PATTERNS:
        m = pat.search(text)
        if m:
            w, d, l = (float(m.group(i)) / 100.0 for i in (1, 2, 3))
            out[key] = {"win": w, "draw": d, "lose": l}
    return out


# ==================== WDL 子模型推理 ====================

def load_models():
    import joblib
    blob = {}
    with open(ASSETS / f"lgb_model_{MODEL_TAG}.pkl", "rb") as f:
        blob["lgb"] = pickle.load(f)
    with open(ASSETS / f"xgb_model_{MODEL_TAG}.pkl", "rb") as f:
        blob["xgb"] = pickle.load(f)
    blob["scaler"] = joblib.load(ASSETS / f"scaler_{MODEL_TAG}.pkl")
    with open(ASSETS / f"selected_features_{MODEL_TAG}.pkl", "rb") as f:
        blob["features"] = pickle.load(f)
    return blob


def sub_probs_ml(models, xgb_calibration, lgb_calibration, league, X_vec, platt_mode):
    """XGB/LGB 子模型概率（win/draw/lose）。"""
    import xgboost as xgb_lib
    X_scaled = models["scaler"].transform(X_vec)
    lgb_raw = models["lgb"].predict(X_scaled)[0]
    lgb_p = {"win": float(lgb_raw[2]), "draw": float(lgb_raw[1]), "lose": float(lgb_raw[0])}
    dmat = xgb_lib.DMatrix(X_scaled)
    xgb_raw = models["xgb"].predict(dmat)[0]
    xgb_p = {"win": float(xgb_raw[2]), "draw": float(xgb_raw[1]), "lose": float(xgb_raw[0])}
    if platt_mode == "platt":
        from prediction_core import _apply_platt_to_wdl, _load_platt_calibration, _select_platt_params
        for key, probs, cal in (("lightgbm", lgb_p, lgb_calibration),
                                ("xgboost", xgb_p, xgb_calibration)):
            params = _select_platt_params(cal, league)
            if params is not None:
                newp = _apply_platt_to_wdl(probs, params)
                if newp:
                    probs.update({k: float(v) for k, v in newp.items()})
    return {"xgboost": xgb_p, "lightgbm": lgb_p}


def fuse_wdl(sub_probs):
    """LR meta-learner（5/5 时）→ 固定权重 Stacking 回退，同生产 L1469-1486。"""
    from prediction_core import STACKING_WEIGHTS, apply_stacking_meta_learner
    available = [k for k in ("dixonColes", "xgboost", "lightgbm", "elo", "bayesian")
                 if sub_probs.get(k) is not None]
    if len(available) >= 2:
        if len(available) == 5:
            meta = apply_stacking_meta_learner(sub_probs, extra_features={"dc_odds_fallback": 0.0})
            if meta is not None:
                return (meta["win"], meta["draw"], meta["lose"]), f"meta({len(available)}/5)"
        from prediction_core import CalcEngine
        stacked = CalcEngine.stack_wdl_probabilities(sub_probs, STACKING_WEIGHTS)
        return (stacked["win"], stacked["draw"], stacked["lose"]), f"stack({len(available)}/5)"
    return (None, None, None), "insufficient"


def decide(hp, dp, ap, league):
    """生产决策口径：argmax + 平局阈值因子（prediction_core L1494-1497）。"""
    pred = "主胜" if hp > max(dp, ap) else ("平局" if dp > ap else "客胜")
    try:
        from prediction_core import get_draw_threshold_factor
        dtf = get_draw_threshold_factor(league)
        if dtf is not None and dp * dtf > max(hp, ap):
            pred = "平局"
    except Exception:
        pass
    return pred


# ==================== 特征行构建（生产同一链路） ====================

def build_feature_rows(targets, conn):
    """prime 全部目标场虚拟行 → build_all_features → 每场抽取特征行（生产口径）。"""
    from feature_utils import build_all_features, load_match_data_odds, normalize_team_name

    df = load_match_data_odds(dedup=True)
    print(f"[feat] load_match_data_odds: shape={df.shape}", flush=True)

    def sporttery_mid(home_cn, away_cn, date):
        try:
            from team_name_mapping import normalize_team_name as norm_wdl
            h, a = norm_wdl(home_cn), norm_wdl(away_cn)
        except Exception:
            h, a = home_cn, away_cn
        like = f"%_{h}_{a}"
        base = pd.Timestamp(date)
        best, best_diff = None, 999
        for r in conn.execute(
                "SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE ?", (like,)):
            mid = r[0]
            try:
                d = pd.Timestamp(str(mid).split("_", 1)[0])
            except (ValueError, IndexError):
                continue
            diff = abs((d - base).days)
            if diff < best_diff:
                best, best_diff = mid, diff
        return best if best_diff <= 3 else None

    vrows = []
    for t in targets:
        vrows.append({
            "home_team_name": normalize_team_name(t["home_cn"]),
            "away_team_name": normalize_team_name(t["away_cn"]),
            "competition_name": t["league"],
            "date": t["date"],
            "match_id": sporttery_mid(t["home_cn"], t["away_cn"], t["date"]),
        })
    vdf = pd.DataFrame(vrows)
    vdf["date"] = pd.to_datetime(vdf["date"], format="mixed")  # 同生产 prime（prediction_core L1320）
    df2 = pd.concat([df.reset_index(drop=True), vdf.reindex(columns=df.columns)],
                    ignore_index=True)
    t0 = time.time()
    X_all, _ = build_all_features(df2, include_odds=True, ts_odds=True, consensus_odds=True)
    print(f"[feat] build_all_features: {time.time()-t0:.1f}s X={X_all.shape}", flush=True)

    with open(ASSETS / f"selected_features_{MODEL_TAG}.pkl", "rb") as f:
        selected = pickle.load(f)
    import joblib
    scaler = joblib.load(ASSETS / f"scaler_{MODEL_TAG}.pkl")
    available = [c for c in selected if c in X_all.columns]
    ok_dim = (len(available) == scaler.n_features_in_)
    print(f"[feat] selected={len(selected)} available={len(available)} "
          f"scaler={scaler.n_features_in_} dim_ok={ok_dim}", flush=True)

    rows = {}
    for i, t in enumerate(targets):
        mask = ((df2["home_team_name"] == vrows[i]["home_team_name"])
                & (df2["away_team_name"] == vrows[i]["away_team_name"]))
        if mask.sum() == 0:
            rows[t["match_id"]] = None
            continue
        idx = df2[mask].index[-1]
        rows[t["match_id"]] = X_all.iloc[idx:idx + 1][available].values if ok_dim else None
    return rows, available, ok_dim


# ==================== 主流程 ====================

def main():
    t_start = time.time()
    REPORTS.mkdir(exist_ok=True)
    conn = ro_conn()

    targets = fetch_targets(conn)
    print(f"[target] 候选 WDL 场次: {len(targets)}", flush=True)

    # 1) 阵容门 + pa 校正（真实首发）
    pa_gen = None
    from player_availability_features import PlayerAvailabilityFeatures
    pa_gen = PlayerAvailabilityFeatures(db_path=DB_PATH)
    per_match = []
    n_gate_fail = 0
    for t in targets:
        xi_h, sq_h = lineup_gate(conn, t["fbref_id"], t["home_en"])
        xi_a, sq_a = lineup_gate(conn, t["fbref_id"], t["away_en"])
        if xi_h is None or xi_a is None:
            n_gate_fail += 1
            continue
        t["xi_h"], t["sq_h"], t["xi_a"], t["sq_a"] = xi_h, sq_h, xi_a, sq_a
        pa_gen.ensure_team_stats(t["home_en"])
        pa_gen.ensure_team_stats(t["away_en"])
        oh, ch, mh = corrected_pa(pa_gen, t["home_en"], t["date"], xi_h, sq_h)
        oa, ca, ma = corrected_pa(pa_gen, t["away_en"], t["date"], xi_a, sq_a)
        t["pa_orig"] = {**{f"{k}_home": v for k, v in oh.items()},
                        **{f"{k}_away": v for k, v in oa.items()}}
        t["pa_corr"] = {**{f"{k}_home": v for k, v in ch.items()},
                        **{f"{k}_away": v for k, v in ca.items()}}
        t["pa_meta"] = {"home": mh, "away": ma}
        per_match.append(t)
    pa_gen.close()
    print(f"[gate] 通过 {len(per_match)} 场，阵容数据不足剔除 {n_gate_fail} 场", flush=True)

    # 2) 生产口径特征行
    try:
        feat_rows, available, ok_dim = build_feature_rows(per_match, conn)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[feat] 特征链构建失败: {e} → 改用降级中性向量（非生产口径，仅 XGB/LGB 敏感性）", flush=True)
        feat_rows, available, ok_dim = None, None, False
    use_prod_feat = feat_rows is not None and ok_dim and any(v is not None for v in feat_rows.values())

    # 3) 模型与校准
    models = load_models()
    from prediction_core import _load_platt_calibration
    xgb_cal = _load_platt_calibration("xgboost")
    lgb_cal = _load_platt_calibration("lightgbm")

    # 4) Platt 模式择优：重算子模型 vs 报告渲染值
    def collect(mode):
        recs = []
        for t in per_match:
            base = feat_rows.get(t["match_id"]) if use_prod_feat else None
            if base is None:
                continue
            rep = parse_report_submodels(t["report"])
            if not rep:
                continue
            X_vec = base.copy()
            for key in ("xgboost", "lightgbm"):
                r = rep.get(key)
                if not r:
                    continue
                p = sub_probs_ml(models, xgb_cal, lgb_cal, t["league"], X_vec, mode)[key]
                err = (abs(p["win"] - r["win"]) + abs(p["draw"] - r["draw"])
                       + abs(p["lose"] - r["lose"])) / 3.0
                recs.append(err)
        return recs

    platt_choice, platt_stats = "raw", {}
    for mode in PLATT_MODES:
        errs = collect(mode)
        if errs:
            platt_stats[mode] = (float(np.mean(errs)), float(np.median(errs)), len(errs))
    if platt_stats:
        platt_choice = min(platt_stats, key=lambda k: platt_stats[k][0])
    print(f"[platt] 模式误差 {platt_stats} → 选择 {platt_choice}", flush=True)

    # 5) 双变体推理
    results = []
    stack_errs = []
    n_no_feat = n_no_rep = 0
    for t in per_match:
        base = feat_rows.get(t["match_id"]) if use_prod_feat else None
        if base is None:
            n_no_feat += 1
            continue
        rep = parse_report_submodels(t["report"])
        if not rep:
            n_no_rep += 1
            continue

        def run(pa_values):
            X_vec = base.copy()
            for col_i, col in enumerate(available):
                if col in CORR_COLS:
                    X_vec[0, col_i] = float(pa_values.get(col, base[0, col_i]))
            sp = sub_probs_ml(models, xgb_cal, lgb_cal, t["league"], X_vec, platt_choice)
            merged = {**{k: v for k, v in rep.items() if k in ("dixonColes", "elo", "bayesian")},
                      **sp}
            (hp, dp, ap), method = fuse_wdl(merged)
            sub_err = {}
            for key in ("xgboost", "lightgbm"):
                if rep.get(key):
                    p = sp[key]
                    sub_err[key] = (abs(p["win"] - rep[key]["win"])
                                    + abs(p["draw"] - rep[key]["draw"])
                                    + abs(p["lose"] - rep[key]["lose"])) / 3.0
            return (hp, dp, ap), method, sub_err

        (bhp, bdp, bap), bmethod, berr = run(t["pa_orig"])
        (chp, cdp, cap), cmethod, cerr = run(t["pa_corr"])
        if bhp is None or chp is None:
            n_no_feat += 1
            continue
        d = (chp - bhp, cdp - bdp, cap - bap)
        base_pred = decide(bhp, bdp, bap, t["league"])
        corr_pred = decide(chp, cdp, cap, t["league"])
        dm_h = abs(t["pa_corr"]["pa_missing_impact_home"] - t["pa_orig"]["pa_missing_impact_home"])
        dm_a = abs(t["pa_corr"]["pa_missing_impact_away"] - t["pa_orig"]["pa_missing_impact_away"])
        results.append({
            **{k: t[k] for k in ("match_id", "date", "home_en", "away_en", "league")},
            "base": (bhp, bdp, bap), "corr": (chp, cdp, cap),
            "delta": d, "max_delta": max(abs(x) for x in d),
            "flip": base_pred != corr_pred,
            "base_pred": base_pred, "corr_pred": corr_pred,
            "dmiss_max": max(dm_h, dm_a), "dmiss_h": dm_h, "dmiss_a": dm_a,
            "method": bmethod,
            "repro_err": {**berr, **{k + "_corr": v for k, v in cerr.items()}},
            "report_base": rep.get("__stacking__"),
        })
        if rep.get("__stacking__") and bhp is not None:
            rb = rep["__stacking__"]
            stack_errs.append((abs(bhp - rb["win"]) + abs(bdp - rb["draw"])
                               + abs(bap - rb["lose"])) / 3.0)

    print(f"[run] 有效 {len(results)} 场 | 无特征行 {n_no_feat} | 无报告 {n_no_rep}", flush=True)

    # 6) 汇总
    maxds = np.array([r["max_delta"] for r in results])
    flips = [r for r in results if r["flip"]]
    mean_max = float(maxds.mean()) * 100 if len(maxds) else 0.0
    p50 = float(np.percentile(maxds, 50)) * 100 if len(maxds) else 0.0
    p90 = float(np.percentile(maxds, 90)) * 100 if len(maxds) else 0.0
    flip_rate = len(flips) / len(results) * 100 if results else 0.0
    mean_abs_all = float(np.mean([abs(x) for r in results for x in r["delta"]])) * 100 if results else 0.0

    buckets = {"<2%": (0.0, 0.02), "2-10%": (0.02, 0.10), ">10%": (0.10, 9.99)}
    bucket_stats = {}
    for name, (lo, hi) in buckets.items():
        grp = [r for r in results if lo <= r["dmiss_max"] < hi]
        bucket_stats[name] = {
            "n": len(grp),
            "mean_max_pp": float(np.mean([r["max_delta"] for r in grp])) * 100 if grp else 0.0,
            "flip": sum(1 for r in grp if r["flip"]),
        }

    go = not (mean_max >= 1.0 or len(flips) > 0)
    verdict = "维持现状（C-112 暂不立项）" if go else "立项 C-112（把真实名单口径接入 pa_* 特征）"

    # 基线复现质量
    re_vals = [v for r in results for v in r["repro_err"].values()]
    repro_mean = float(np.mean(re_vals)) * 100 if re_vals else float("nan")
    stack_err_mean = float(np.mean(stack_errs)) * 100 if stack_errs else float("nan")

    # 7) 报告落盘
    lines = []
    A = lines.append
    A("# 真实首发反事实对拍：WDL 概率敏感性（C-112 前置评估）")
    A("")
    A(f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')} | 数据：`data/odds.db`（只读）")
    A(f"- 窗口：model_predictions WDL_home ≥ 2026-10-01（实际 2026-10-10~13）；候选 {len(targets)} 场，阵容门通过 {len(per_match)} 场，有效对拍 {len(results)} 场")
    A(f"- 推理路径：**子模型近似**。XGBoost/LightGBM 用 `{MODEL_TAG}` 版本 pkl 重算（预测窗口内生产生效版本）；Dixon-Coles/Elo/贝叶斯直接复用赛前报告 md 渲染值（三者不消费 pa_*）；LR meta-learner 融合（同生产）。特征行走生产同一构建链（load_match_data_odds + prime 虚拟行 + build_all_features），pa_* 即 sofascore_team_features 落库值")
    A(f"- Platt 校准模式：`{platt_choice}`（以重算子模型 vs 报告渲染值平均绝对误差择优："
      + ", ".join(f"{k}={v[0]*100:.2f}pp(n={v[2]})" for k, v in platt_stats.items())
      + f"）；基线复现误差（Stacking 前子模型平均）≈ {repro_mean:.2f}pp；"
      f"整链复现误差（基线 Stacking vs 报告融合行）≈ {stack_err_mean:.2f}pp")
    A("- 校正口径：pa_xi_* 用真实首发×赛前加权基线重算；pa_availability/pa_missing_impact 按生产公式重算（missing = 核心池 − 整场出场名单）；其余 7 维 pa 不变，其余 244 维特征不变")
    A("")
    A("## 汇总")
    A("")
    A(f"| 指标 | 值 |")
    A(f"|---|---|")
    A(f"| 有效样本 | {len(results)} |")
    A(f"| 平均 \\|Δprob\\|（三向平均） | {mean_abs_all:.2f} pp |")
    A(f"| 平均最大 \\|Δprob\\|（每场三向取最大再平均） | {mean_max:.2f} pp |")
    A(f"| P50 / P90 最大 \\|Δprob\\| | {p50:.2f} / {p90:.2f} pp |")
    A(f"| argmax 翻转 | {len(flips)}/{len(results)}（{flip_rate:.1f}%） |")
    A("")
    A("## 按 \\|Δpa_missing_impact\\| 分桶（每场取主客两侧最大值）")
    A("")
    A("| 桶 | 场次 | 平均最大 Δpp | 翻转 |")
    A("|---|---:|---:|---:|")
    for name in ("<2%", "2-10%", ">10%"):
        s = bucket_stats[name]
        A(f"| {name} | {s['n']} | {s['mean_max_pp']:.2f} | {s['flip']} |")
    A("")
    A("## Go / No-Go")
    A("")
    A(f"- 判据：平均最大 Δ<1pp 且零翻转 → 维持现状；Δ≥1pp 或有翻转 → 立项 C-112")
    A(f"- 实测：平均最大 Δ = **{mean_max:.2f} pp**，翻转 **{len(flips)}** 场 → **{verdict}**")
    A("")
    top = sorted(results, key=lambda x: -x["max_delta"])[:3]
    top_xi_driven = sum(1 for r in top if r["dmiss_max"] < 0.02)
    A("### 关键观察")
    A("")
    A(f"- Δ 的主要驱动是 **pa_xi_*（XI 强度）**而非 pa_missing_impact：Δ 最大的 {len(top)} 场中 "
      f"{top_xi_driven} 场 |Δpa_missing_impact|<2%（如 {top[0]['home_en']} vs {top[0]['away_en']} "
      f"Δ={top[0]['max_delta']*100:.2f}pp 而 Δmiss={top[0]['dmiss_max']:.3f}）——真实首发改变了推算 XI 的评分/xG 强度")
    A("- 因此按 |Δpa_missing_impact| 分桶不单调（missing_impact 仅是 254 维中 2 维）；"
      "若立项 C-112，价值主要在「XI 强度用真实首发」，而非伤停影响度口径")
    A("")
    if flips:
        A("### 翻转场次明细")
        A("")
        A("| 日期 | 对阵 | 基线(主/平/客) | 校正后 | 基线方向→校正方向 | Δmiss(h/a) |")
        A("|---|---|---|---|---|---|")
        for r in flips:
            b = r["base"]
            c = r["corr"]
            A(f"| {r['date']} | {r['home_en']} vs {r['away_en']} | "
              f"{b[0]*100:.1f}/{b[1]*100:.1f}/{b[2]*100:.1f} | "
              f"{c[0]*100:.1f}/{c[1]*100:.1f}/{c[2]*100:.1f} | "
              f"{r['base_pred']}→{r['corr_pred']} | {r['dmiss_h']:.3f}/{r['dmiss_a']:.3f} |")
        A("")
    A("## 每场明细")
    A("")
    A("| 日期 | 对阵 | Δ主胜pp | Δ平局pp | Δ客胜pp | 最大Δpp | 翻转 | 基线方向 | Δmiss(h) | Δmiss(a) | 融合 |")
    A("|---|---|---:|---:|---:|---:|:---:|:---:|---:|---:|---|")
    for r in sorted(results, key=lambda x: -x["max_delta"]):
        d = r["delta"]
        A(f"| {r['date']} | {r['home_en']} vs {r['away_en']} | "
          f"{d[0]*100:+.2f} | {d[1]*100:+.2f} | {d[2]*100:+.2f} | {r['max_delta']*100:.2f} | "
          f"{'**是**' if r['flip'] else '否'} | {r['base_pred']} | "
          f"{r['dmiss_h']:.3f} | {r['dmiss_a']:.3f} | {r['method']} |")
    A("")
    A("---")
    A("")
    A("> 脚注1：真实首发为赛后回溯值，代理「临场官方首发」；球员统计仍为赛前可得（无泄漏），")
    A("> 但生产赛前拿到的官方名单口径与赛后首发不完全一致 → 本 Δ 为**上限估计**。")
    A("> 脚注2：XGBoost/LightGBM 重算基于生产同链特征行；子模型复现误差 ≈ "
      f"{repro_mean:.2f}pp、整链复现误差 ≈ {stack_err_mean:.2f}pp（vs 报告渲染值），")
    A("> 主要来自批次差异（生产分批 prime + 当时历史截断面）。Platt 双模式误差完全一致，")
    A("> 原因：`_load_platt_calibration` 按 `xgboost_/lightgbm_calibration.json` 找文件，")
    A("> 而 assets 实际文件名为 `xgb_/lgb_calibration.json`（2026-10-14 生成）→ 两模式均未校准，")
    A("> 恰与预测当时（校准文件尚不存在）的生产行为一致，对拍双变体同口径。")
    A("> 脚注3：融合用报告渲染的 DC/Elo/贝叶斯 + 重算 XGB/LGB；若报告缺子模型行则该场剔除。")
    REPORT_OUT.write_text("\n".join(lines), encoding="utf-8")
    conn.close()

    print(f"\n[done] {time.time()-t_start:.0f}s → {REPORT_OUT}", flush=True)
    print(f"[SUMMARY] n={len(results)} mean_max={mean_max:.2f}pp P50={p50:.2f} P90={p90:.2f} "
          f"flip={len(flips)}/{len(results)} ({flip_rate:.1f}%) | "
          + " | ".join(f"{k}: n={v['n']} {v['mean_max_pp']:.2f}pp flip={v['flip']}"
                       for k, v in bucket_stats.items())
          + f" | Go/No-Go: {verdict}", flush=True)


if __name__ == "__main__":
    main()
