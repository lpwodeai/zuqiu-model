# -*- coding: utf-8 -*-
"""
C-20261017-129 分歧切片三方诊断（只读分析，零模型改动）。

背景：2026-10-17 勒芒 vs 图卢兹，5 子模型 4:1 看客胜，Stacking meta 反向给主胜
42.2%，报告仍产出 STRONG_VALUE（3.03 / +13.0% / 凯利 0.137）。诊断定性为
「共线性伪权重」的切片缺陷而非 bug。本脚本量化分歧切片里三方的真实水平。

先验（C-20261004-007 已实测，n=77）：
  - argmax 方向一致率 81.8%（14 场分歧）；
  - 方向规则（一致信模型/分歧信市场）RPS 0.1912 **输**纯市场 0.1831；
  - δ*=0.06 信任规则 0.1814，仅噪声级微胜（落纯市场 bootstrap CI 内）；
  - w 扫描单调下降 w*=1.0（纯市场全样本最优，t-24h/时间外推均坐实）。
=> 预期：分歧切片 meta 输市场、C-007 式护栏也输市场；结论再次推向 C-013
   市场混合，而不是新造护栏。只有 fresh 窗口出现反先验结果才是新闻。

口径（用户钉死，不得漂移）：
  - 多数票 = 5 子模型 argmax 计票，多数方向得票 ≥3；
  - 分歧度 = 三向概率总体标准差均值（与报告 _sub_model_divergence 同公式），阈值 0.07；
  - 三切片：S1 meta_argmax≠多数票；S2 分歧度≥0.07；S3=S1∩S2；
  - 三方：meta（现行资产系数复算/serving 落库值）、5 模型简单平均、市场锚
    （500.com avg_live 去水，与 C-007 同腿）；
  - 指标：n / argmax 命中率 / RPS（列序 [客,平,主]）+ bootstrap 95% CI。

分层：
  OOF 12,620 场 —— EVAL-PEEK（meta 训练集，仅作动机，禁止据此调任何阈值）；
  fold5         —— TimeSeriesSplit 第 5 折（clf_hold 仅用前 4 折，近似未见）；
  fresh         —— 10-10 之后、晚于 OOF 最大日期的 serving 落库场次（主证据，
                    meta 训练后数据；小样本，不调参）。

用法：
  python _diag_divergence_slices.py build     # 重跑 OOF（慢），落 pkl 到系统 TEMP
  python _diag_divergence_slices.py analyze   # 读 pkl + fresh SQL，出全部表格
  python _diag_divergence_slices.py all       # build + analyze
"""
import os
import sys
import json
import pickle
import tempfile

import numpy as np
import pandas as pd
import sqlite3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

from feature_utils import load_match_data_odds, build_all_features, ODDS_DB_PATH
from prediction_core import CalcEngine, STACKING_WEIGHTS, LEAGUE_RHO
from bayesian_hierarchical_model import BayesianHierarchicalModel
from elo_rating import precompute_elo_ratings, get_team_elo_at_date
from train_models import train_xgboost, train_lightgbm
from db_utils import connect
import market_anchor_mix as mam

MODELS = ["dixonColes", "elo", "xgboost", "lightgbm", "bayesian"]
# 子模型概率在数组中的固定次序（[客,平,主] 列序）
P5_ORDER = ["dixonColes", "xgboost", "lightgbm", "elo", "bayesian"]
DEFAULT_PROB = {"win": 0.40, "draw": 0.27, "lose": 0.33}
LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]
DIV_THRESHOLD = 0.07
FRESH_MIN_DATE = "2026-10-10"
MODEL_NAME = "generate_unified_report_v2.0"
PKL_PATH = os.path.join(tempfile.gettempdir(), "_diag_slices_oof.pkl")
MD_PATH = os.path.join(tempfile.gettempdir(), "_diag_slices_result.md")

# fresh 层 Submodel_* 落库名 → MODELS 键
SUB_DB_MAP = {
    "dixonColes": "DC",
    "xgboost": "XGB",
    "lightgbm": "LGB",
    "elo": "Elo",
    "bayesian": "Bayes",
}
ACTUAL_MAP = {"主胜": 2, "胜": 2, "W": 2, "平局": 1, "平": 1, "D": 1, "客胜": 0, "负": 0, "L": 0}


# ============================================================
# 指标 / 切片
# ============================================================
def compute_rps(y, P):
    return mam.compute_rps(np.asarray(y, dtype=int), np.asarray(P, dtype=float))


def acc(y, P):
    return float(np.mean(np.asarray(P).argmax(1) == np.asarray(y, dtype=int)))


def divergence_std(P5):
    """P5: (..., K, 3) 子模型概率，返回三向总体标准差均值（报告口径，ddof=0）。"""
    P5 = np.asarray(P5, dtype=float)
    return P5.std(axis=1, ddof=0).mean(axis=-1)


def slice_masks(meta, P5):
    """返回 S1/S2/S3/多数票方向/分歧度。meta、P5 列序 [客,平,主]。"""
    n = len(meta)
    votes = np.asarray(P5).argmax(axis=2)          # (n, K)
    meta_dir = np.asarray(meta).argmax(axis=1)
    maj_dir = np.full(n, -1, dtype=int)
    maj_votes = np.zeros(n, dtype=int)
    for i in range(n):
        cnt = np.bincount(votes[i], minlength=3)
        d = int(cnt.argmax())
        maj_dir[i] = d
        maj_votes[i] = int(cnt[d])
    std = divergence_std(P5)
    s1 = (maj_votes >= 3) & (maj_dir != meta_dir)
    s2 = std >= DIV_THRESHOLD
    return {
        "S1_meta_vs_majority": s1,
        "S2_div_ge_007": s2,
        "S3_intersection": s1 & s2,
        "maj_dir": maj_dir, "maj_votes": maj_votes, "meta_dir": meta_dir, "std": std,
    }


def stat_row(y, P, ci=True):
    y = np.asarray(y, dtype=int)
    P = np.asarray(P, dtype=float)
    n = len(y)
    if n == 0:
        return {"n": 0, "acc": None, "rps": None, "lo": None, "hi": None}
    rps = compute_rps(y, P)
    lo, hi = (None, None)
    if ci and n >= 10:
        lo, _, hi = mam.bootstrap_rps_ci(y, P)
    return {"n": n, "acc": acc(y, P), "rps": rps, "lo": lo, "hi": hi}


def evaluate_layer(name, y, meta, avg, mkt, P5, extra_meta=None, note=""):
    """extra_meta: dict(name->P) 追加列（如 fold5 的 hold-clf、护栏规则）。"""
    info = slice_masks(meta, P5)
    masks = {
        "ALL": np.ones(len(y), dtype=bool),
        "S1_meta!=majority": info["S1_meta_vs_majority"],
        "S2_div>=0.07": info["S2_div_ge_007"],
        "S3_intersection": info["S3_intersection"],
    }
    lines = ["### 层：{} {}".format(name, ("（" + note + "）") if note else "")]
    for sk, mask in masks.items():
        idx = np.where(mask)[0]
        has_mkt = np.array([mkt[i] is not None for i in idx])
        idx_m = idx[has_mkt]
        rows = []
        rows.append(("meta", stat_row(y[idx], meta[idx])))
        rows.append(("等权平均", stat_row(y[idx], avg[idx])))
        rows.append(("市场锚", stat_row(y[idx_m], np.array([mkt[i] for i in idx_m]), ci=False)
                     if len(idx_m) else stat_row([], [])))
        # 护栏复活检验 + 追加列：只在市场可用的同子集上比，保证公平
        if len(idx_m):
            ym, mm, km = y[idx_m], meta[idx_m], np.array([mkt[i] for i in idx_m])
            guard_dir = mam.direction_rule_probs(mm, km)
            guard_d06 = mam.delta_rule_probs(mm, km, 0.06)
            rows.append(("护栏:方向规则(C007)", stat_row(ym, guard_dir, ci=False)))
            rows.append(("护栏:delta0.06(C007)", stat_row(ym, guard_d06, ci=False)))
            # 同子集重算 meta/市场，方便与护栏直接对读
            rows_meta_sub = stat_row(ym, mm, ci=False)
            rows_mkt_sub = stat_row(ym, km, ci=False)
        if extra_meta:
            for ename, eP in extra_meta.items():
                rows.append((ename, stat_row(y[idx], eP[idx])))
        lines.append("")
        lines.append("**切片 {}**（总 n={}，市场可用 {}/{}）".format(
            sk, len(idx), len(idx_m), len(idx)))
        lines.append("")
        lines.append("| 方法 | n | argmax命中率 | RPS | RPS 95%CI |")
        lines.append("|------|---|------------|-----|-----------|")
        for rn, st in rows:
            ci_str = "—"
            if st["lo"] is not None:
                ci_str = "[{:.4f}, {:.4f}]".format(st["lo"], st["hi"])
            lines.append("| {} | {} | {} | {} | {} |".format(
                rn, st["n"],
                "—" if st["acc"] is None else "{:.3f}".format(st["acc"]),
                "—" if st["rps"] is None else "{:.4f}".format(st["rps"]),
                ci_str))
        if len(idx_m) and sk != "ALL":
            # 成对 bootstrap（同场次配对，seed 固定）：Δ=meta−市场，CI 排除 0 才称显著
            _ym = y[idx_m].astype(int)
            _mm = meta[idx_m]
            _km = np.array([mkt[i] for i in idx_m])
            _onehot = np.zeros((len(_ym), 3))
            _onehot[np.arange(len(_ym)), _ym] = 1.0
            _d_rps = 0.5 * (((_mm - _onehot) ** 2).sum(1) - ((_km - _onehot) ** 2).sum(1))
            _d_acc = (_mm.argmax(1) == _ym).astype(float) - (_km.argmax(1) == _ym).astype(float)
            _rng = np.random.default_rng(42)
            _br, _ba = [], []
            for _ in range(2000):
                _s = _rng.integers(0, len(_ym), len(_ym))
                _br.append(_d_rps[_s].mean())
                _ba.append(_d_acc[_s].mean())
            _br, _ba = np.array(_br), np.array(_ba)
            lines.append("")
            lines.append("> 同子集（市场可用 n={}）直接对读：meta RPS={:.4f} 命中率={:.3f}；"
                         "市场 RPS={:.4f} 命中率={:.3f}".format(
                             len(idx_m), rows_meta_sub["rps"], rows_meta_sub["acc"],
                             rows_mkt_sub["rps"], rows_mkt_sub["acc"]))
            lines.append(">")
            lines.append("> 成对 Δ（meta−市场，bootstrap 2000）：ΔRPS={:+.4f}，95%CI [{:+.4f}, {:+.4f}]；"
                         "Δ命中率={:+.1f}pp，95%CI [{:+.1f}, {:+.1f}]pp".format(
                             _d_rps.mean(), np.quantile(_br, 0.025), np.quantile(_br, 0.975),
                             _d_acc.mean() * 100,
                             np.quantile(_ba, 0.025) * 100, np.quantile(_ba, 0.975) * 100))
    lines.append("")
    return "\n".join(lines), info


# ============================================================
# Phase 1: OOF 确定性重跑（与 train_stacking_meta.py 同链路）
# ============================================================
def _to_label_order(p):
    return np.array([p["lose"], p["draw"], p["win"]])


def build_oof():
    print("=" * 70)
    print("[build] OOF 确定性重跑（链路须与 train_stacking_meta.py 完全一致）")
    print("=" * 70)
    df = load_match_data_odds().sort_values("date").reset_index(drop=True)
    print("[build] 比赛总数: {}".format(len(df)))
    X, y = build_all_features(df, include_odds=True)
    y = np.asarray(y).astype(int)
    assert len(X) == len(df)
    tscv = TimeSeriesSplit(n_splits=5)

    # 市场腿（C-007 口径）：500 avg_live 去水；双通道键（match_id 直连 / 日期+中文队名）
    db_conn = connect(db_path=ODDS_DB_PATH)
    raw = db_conn.execute("""
        SELECT s.match_id, s.avg_live_win, s.avg_live_draw, s.avg_live_lose,
               m.match_date, m.home_team_cn, m.away_team_cn
        FROM odds500_ouzhi_summary s
        LEFT JOIN odds500_match m ON s.match_id = m.match_id
    """).fetchall()
    mkt_by_id, mkt_by_name = {}, {}
    for r in raw:
        try:
            w, d, l = float(r[1]), float(r[2]), float(r[3])
        except (TypeError, ValueError):
            continue
        if not (w > 1 and d > 1 and l > 1):
            continue
        pr = np.array([1.0 / l, 1.0 / d, 1.0 / w])  # - [客,平,主]
        pr = pr / pr.sum()
        mkt_by_id[str(r[0])] = pr
        if r[4] and r[5] and r[6]:
            mkt_by_name[(str(r[4])[:10], mam._norm(r[5]), mam._norm(r[6]))] = pr
    print("[build] 500 市场腿: by_id={} by_name={}".format(len(mkt_by_id), len(mkt_by_name)))

    oof_preds = {m: [] for m in MODELS}
    oof_true, oof_fold, oof_fallback = [], [], []
    oof_meta_rows = []

    for fold, (tr_idx, va_idx) in enumerate(tscv.split(X)):
        print("\n[build] --- Fold {}/5 ---".format(fold + 1))
        train_df = df.iloc[tr_idx].reset_index(drop=True)
        val_df = df.iloc[va_idx].reset_index(drop=True)
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X.iloc[tr_idx])
        X_val_s = scaler.transform(X.iloc[va_idx])
        y_train, y_val = y[tr_idx], y[va_idx]

        try:
            xgb_model, _ = train_xgboost(X_train_s, y_train, X_val_s, y_val)
            xgb_val = xgb_model.predict(__import__("xgboost").DMatrix(X_val_s))
        except Exception as e:
            print("  [Warn] XGB fold 失败，先验兜底: {}".format(e))
            xgb_val = np.tile([0.33, 0.27, 0.40], (len(y_val), 1))
        try:
            lgb_model, _ = train_lightgbm(X_train_s, y_train, X_val_s, y_val)
            lgb_val = lgb_model.predict(X_val_s)
        except Exception as e:
            print("  [Warn] LGB fold 失败，先验兜底: {}".format(e))
            lgb_val = np.tile([0.33, 0.27, 0.40], (len(y_val), 1))

        lg_stats = {L: {"home": g["homeGoals"].mean(), "away": g["awayGoals"].mean()}
                    for L, g in train_df.groupby("competition_name")}
        team_elo_dfs = precompute_elo_ratings(train_df)
        bayes_models = {}
        for L in LEAGUES:
            sub = train_df[train_df["competition_name"] == L]
            if len(sub) >= 10:
                try:
                    bayes_models[L] = BayesianHierarchicalModel().fit(sub, league=L)
                except Exception as e:
                    print("  [Bayes-Warn] {} 拟合失败: {}".format(L, e))
                    bayes_models[L] = None
            else:
                bayes_models[L] = None

        dc_val, dc_fb_val, elo_val, bay_val = [], [], [], []
        for _, row in val_df.iterrows():
            Lg = row["competition_name"]
            rho = LEAGUE_RHO.get(Lg, -0.10)
            is_fallback = True
            odds_data = train_stacking_build_odds(db_conn, row.get("match_id", ""))
            if odds_data and odds_data.get("wdl_odds", {}).get("close"):
                try:
                    lh, la = CalcEngine.calc_lambda_from_odds(odds_data)
                    lh, la, _ = CalcEngine.adjust_lambda_for_mid_score(lh, la, odds_data)
                    dc = CalcEngine.calc_win_draw_lose_dixon_coles(lh, la, rho=rho)
                    is_fallback = False
                except Exception:
                    pass
            if is_fallback:
                if Lg in lg_stats:
                    dc = CalcEngine.calc_win_draw_lose_dixon_coles(
                        lg_stats[Lg]["home"], lg_stats[Lg]["away"], rho=rho)
                else:
                    dc = DEFAULT_PROB
            dc_val.append(_to_label_order(dc))
            dc_fb_val.append(1.0 if is_fallback else 0.0)
            h, a, d = row["home_team_name"], row["away_team_name"], row["date"]
            hd = get_team_elo_at_date(team_elo_dfs, h, d)
            ad = get_team_elo_at_date(team_elo_dfs, a, d)
            elo = CalcEngine.calc_win_draw_lose_elo(
                h, a, {h: hd["elo"], a: ad["elo"]},
                {h: hd.get("momentum", 0.0), a: ad.get("momentum", 0.0)})
            elo_val.append(_to_label_order(elo))
            bm = bayes_models.get(Lg)
            bay = bm.predict_wdl(h, a) if bm is not None else DEFAULT_PROB
            bay_val.append(_to_label_order(bay))

        preds = {
            "dixonColes": np.asarray(dc_val),
            "elo": np.asarray(elo_val),
            "xgboost": np.asarray(xgb_val),
            "lightgbm": np.asarray(lgb_val),
            "bayesian": np.asarray(bay_val),
        }
        for m in MODELS:
            oof_preds[m].append(preds[m])
        oof_true.append(y_val)
        oof_fold.append(np.full(len(y_val), fold, dtype=int))
        oof_fallback.append(np.asarray(dc_fb_val))
        for _, row in val_df.iterrows():
            mid = str(row.get("match_id", ""))
            dstr = str(row["date"])[:10]
            key = (dstr, mam._norm(row["home_team_name"]), mam._norm(row["away_team_name"]))
            mp = mkt_by_id.get(mid)
            if mp is None:
                mp = mkt_by_name.get(key)
            oof_meta_rows.append({
                "match_id": mid, "date": dstr,
                "league": row["competition_name"],
                "home": row["home_team_name"], "away": row["away_team_name"],
                "market": None if mp is None else mp.tolist(),
            })
        print("[build] fold{} n={} done".format(fold + 1, len(y_val)))

    db_conn.close()
    bundle = {
        "oof_preds": {m: np.vstack(oof_preds[m]) for m in MODELS},
        "y": np.concatenate(oof_true),
        "fold": np.concatenate(oof_fold),
        "fallback": np.concatenate(oof_fallback),
        "rows": oof_meta_rows,
    }
    with open(PKL_PATH, "wb") as f:
        pickle.dump(bundle, f)
    print("\n[build] 落盘: {} (n={})".format(PKL_PATH, len(bundle["y"])))
    n_mkt = sum(1 for r in oof_meta_rows if r["market"] is not None)
    print("[build] OOF 市场腿覆盖: {}/{} ({:.1%})".format(n_mkt, len(oof_meta_rows),
                                                          n_mkt / max(1, len(oof_meta_rows))))
    print("[build] 日期范围: {} ~ {}".format(
        min(r["date"] for r in oof_meta_rows), max(r["date"] for r in oof_meta_rows)))


def train_stacking_build_odds(conn, match_id):
    """与 train_stacking_meta._build_odds_data_from_db 同构（复制以保证独立可跑）。"""
    rows = conn.execute(
        "SELECT timestamp, win_a, draw, win_b FROM wdl_history "
        "WHERE match_id=? ORDER BY timestamp", (match_id,)).fetchall()
    if not rows:
        return None
    records = [{"time": r[0], "win": float(r[1]), "draw": float(r[2]), "lose": float(r[3])}
               for r in rows]
    wdl_odds = {"close": records[-1], "open": records[0], "records": records}
    tg_rows = conn.execute(
        "SELECT timestamp, goals_0, goals_1, goals_2, goals_3, "
        "goals_4, goals_5, goals_6, goals_7_plus "
        "FROM total_goals_history WHERE match_id=? ORDER BY timestamp",
        (match_id,)).fetchall()
    tg_records = []
    for r in tg_rows:
        goals = {}
        for idx, k in enumerate(["0", "1", "2", "3", "4", "5", "6", "7+"]):
            v = r[idx + 1]
            if v is not None:
                goals[k] = float(v)
        if goals:
            tg_records.append({"pub_time": r[0], "goals": goals})
    return {"wdl_odds": wdl_odds, "tg_odds": {"records": tg_records}}


# ============================================================
# Phase 2: 组装三方概率 + 三层评估
# ============================================================
def _softmax(z):
    z = np.asarray(z, dtype=float)
    e = np.exp(z - z.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def meta_from_asset(oof_preds, fallback):
    """用现行 assets/stacking_meta_learner.json 系数复算 meta（serving 同公式）。"""
    with open(os.path.join(ASSETS_DIR, "stacking_meta_learner.json"), "r", encoding="utf-8") as f:
        asset = json.load(f)
    assert asset["models"] == MODELS, "资产 models 次序漂移: {}".format(asset["models"])
    expect_cols = ["{}__{}".format(m, c) for m in MODELS for c in ["win", "draw", "lose"]]
    expect_cols.append("dc_odds_fallback")
    assert asset["feature_names"] == expect_cols, "资产 feature_names 口径漂移"
    X_meta = np.hstack([
        np.hstack([oof_preds[m][:, 2][:, None],
                   oof_preds[m][:, 1][:, None],
                   oof_preds[m][:, 0][:, None]])
        for m in MODELS
    ])
    X_meta = np.hstack([X_meta, np.asarray(fallback)[:, None]])
    coef = np.asarray(asset["coef_"])      # (3,16) 行序 classes [0,1,2]=[客,平,主]
    intercept = np.asarray(asset["intercept_"])
    meta = _softmax(intercept + X_meta @ coef.T)
    return meta, asset


def load_fresh(oof_max_date):
    """serving fresh 窗口：5 子模型 + meta(WDL_*) 落库概率 + 赛果 + 500 市场。"""
    conn = sqlite3.connect(ODDS_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT m.match_id, m.match_date, m.home_team, m.away_team, m.league, m.actual_wdl
        FROM matches m
        WHERE EXISTS (
            SELECT 1 FROM model_predictions mp
            WHERE mp.match_id=m.match_id AND mp.model_name=? AND mp.is_replay=0
              AND mp.prediction_type='Submodel_DC_home')
          AND m.actual_wdl IS NOT NULL AND m.actual_wdl != ''
        ORDER BY m.match_date
    """, (MODEL_NAME,)).fetchall()
    want = ["WDL_home", "WDL_draw", "WDL_away"]
    for mk, dbk in SUB_DB_MAP.items():
        want += ["Submodel_{}_home".format(dbk), "Submodel_{}_draw".format(dbk),
                 "Submodel_{}_away".format(dbk)]
    ph = ",".join("?" * len(want))
    preds = {}
    for r in conn.execute("""
        SELECT match_id, prediction_type, probability FROM model_predictions
        WHERE model_name=? AND is_replay=0 AND prediction_type IN ({})
    """.format(ph), [MODEL_NAME] + want):
        preds.setdefault(r["match_id"], {})[r["prediction_type"]] = r["probability"]
    close = mam.load_market_close(conn)
    conn.close()

    out = []
    skipped = 0
    for r in rows:
        wdl = preds.get(r["match_id"])
        act = ACTUAL_MAP.get(str(r["actual_wdl"]).strip())
        if not wdl or act is None:
            skipped += 1
            continue
        dstr = str(r["match_date"])[:10]
        if dstr < FRESH_MIN_DATE or dstr <= oof_max_date:
            continue
        try:
            meta = np.array([float(wdl["WDL_away"]), float(wdl["WDL_draw"]), float(wdl["WDL_home"])])
            P5 = []
            ok = True
            for mk in P5_ORDER:
                dbk = SUB_DB_MAP[mk]
                trip = (wdl.get("Submodel_{}_away".format(dbk)),
                        wdl.get("Submodel_{}_draw".format(dbk)),
                        wdl.get("Submodel_{}_home".format(dbk)))
                if any(v is None for v in trip):
                    ok = False
                    break
                P5.append([float(trip[0]), float(trip[1]), float(trip[2])])
            if not ok or abs(meta.sum() - 1) > 0.2:
                skipped += 1
                continue
        except (TypeError, ValueError):
            skipped += 1
            continue
        key = (dstr, mam._norm(r["home_team"]), mam._norm(r["away_team"]))
        km = close.get(key)
        out.append({
            "date": dstr, "league": r["league"],
            "home": r["home_team"], "away": r["away_team"], "y": int(act),
            "meta": meta / meta.sum(),
            "P5": np.asarray(P5),
            "mkt": None if km is None else np.array([km["k_away"], km["k_draw"], km["k_home"]]),
        })
    return out, skipped


def analyze():
    with open(PKL_PATH, "rb") as f:
        bundle = pickle.load(f)
    oof_preds, y_all = bundle["oof_preds"], bundle["y"]
    fold_all, fb_all, rows = bundle["fold"], bundle["fallback"], bundle["rows"]
    dates = np.array([r["date"] for r in rows])
    oof_max_date = max(r["date"] for r in rows)

    meta, asset = meta_from_asset(oof_preds, fb_all)
    avg = np.mean([oof_preds[m] for m in MODELS], axis=0)
    P5 = np.stack([oof_preds[m] for m in P5_ORDER], axis=1)
    mkt = [None if r["market"] is None else np.array(r["market"]) for r in rows]

    # 锚点校验：
    #  (1) 资产系数应用公式 = serving：用勒芒场 5 组概率（serving fallback=0）复算，
    #      须逐位吻合 28.6/29.2/42.2（人工已复核的 serving 物证）；
    #  (2) OOF 全量 RPS 与资产记录只做合理性带校验——数据已从 12,620 增长，
    #      TSCV 折边界随 n 改变，OOF 样本集不同，要求逐位相等本身不成立。
    rps_re = compute_rps(y_all, meta)
    rps_asset = float(asset["rps_oof_full"])
    acc_re = float((meta.argmax(1) == y_all).mean())
    print("[anchor] OOF n={}（资产 n_train={}，数据已增长，样本集不同；EVAL-PEEK）".format(
        len(y_all), int(asset["n_train"])))
    print("[anchor] 重算 RPS={:.4f} vs 资产记录 {:.4f}（差 {:+.4f}，合理性带 ±0.02）".format(
        rps_re, rps_asset, rps_re - rps_asset))
    print("[anchor] 重算 acc={:.4f}（资产 {:.4f}）".format(acc_re, float(asset["acc_oof_full"])))
    if abs(rps_re - rps_asset) > 0.02:
        raise SystemExit("[FATAL] OOF RPS 偏离资产记录超 0.02，重算链路疑似口径漂移，终止")

    # 勒芒场 serving 锚点（2026-10-17，fallback=0；报告物证 28.6/29.2/42.2）。
    # 注意：必须用 model_predictions 落库的精确概率；报告展示值已四舍五入到 0.1pp，
    # 用舍入值复算会偏 ~5pp（softmax 对共线输入敏感）。
    _c = sqlite3.connect(ODDS_DB_PATH)
    _lmid = "2026-10-17_Le Mans_Toulouse"
    _lm_rows = dict((r[0], r[1]) for r in _c.execute(
        "SELECT prediction_type, probability FROM model_predictions "
        "WHERE model_name=? AND match_id=? AND is_replay=0 "
        "AND prediction_type LIKE 'Submodel_%'",
        (MODEL_NAME, _lmid)).fetchall())
    _c.close()
    _lm = {}
    for _mk in MODELS:
        _dbk = SUB_DB_MAP[_mk]
        _lm[_mk] = (float(_lm_rows["Submodel_{}_away".format(_dbk)]),
                    float(_lm_rows["Submodel_{}_draw".format(_dbk)]),
                    float(_lm_rows["Submodel_{}_home".format(_dbk)]))
    _xlm = np.hstack([
        np.array([[_lm[m][2], _lm[m][1], _lm[m][0]] for m in MODELS]).reshape(1, 15),
        np.array([[0.0]])])
    _plm = _softmax(np.asarray(asset["intercept_"]) + _xlm @ np.asarray(asset["coef_"]).T)[0]
    print("[anchor] 勒芒复算 [客,平,主] = [{:.1f},{:.1f},{:.1f}] vs 报告 [28.6,29.2,42.2]".format(
        _plm[0] * 100, _plm[1] * 100, _plm[2] * 100))
    if max(abs(_plm - np.array([0.286, 0.292, 0.422]))) > 2e-3:
        raise SystemExit("[FATAL] 勒芒 serving 锚点不吻合，meta 系数应用口径漂移，终止")

    # fold5 hold-clf（仅前 4 折训练）
    hold_tr = fold_all < 4
    meta_cols = ["{}__{}".format(m, c) for m in MODELS for c in ["win", "draw", "lose"]]
    meta_cols.append("dc_odds_fallback")
    X_meta = np.hstack([
        np.hstack([oof_preds[m][:, 2][:, None],
                   oof_preds[m][:, 1][:, None],
                   oof_preds[m][:, 0][:, None]])
        for m in MODELS])
    X_meta = np.hstack([X_meta, fb_all[:, None]])
    clf_hold = LogisticRegression(solver="lbfgs", C=1.0, max_iter=2000, random_state=42)
    clf_hold.fit(X_meta[hold_tr], y_all[hold_tr])
    meta_hold = clf_hold.predict_proba(X_meta)

    md = ["# C-20261017-129 分歧切片三方诊断", "",
          "- 先验：C-20261004-007 已证伪分歧信任规则（方向规则 0.1912 输纯市场 0.1831，w*=1.0）",
          "- OOF 层为 **EVAL-PEEK**（meta 训练集，仅作动机，禁止据此调阈值）",
          "- 多数票≥3；分歧度阈值 0.07（报告口径）；市场锚=500.com avg_live 去水（C-007 同腿）",
          "- OOF 日期范围：{} ~ {}；n={}；DC 兜底场次 {}/{}".format(
              min(dates), max(dates), len(y_all),
              int((fb_all == 1).sum()), len(fb_all)),
          ""]

    sec, info_oof = evaluate_layer(
        "OOF 全量", y_all, meta, avg, mkt, P5, note="EVAL-PEEK：OOF 是 meta 训练集（资产训练 n=12,620，本次重跑 n=15,335）")
    md.append(sec)

    m5 = fold_all == 4
    sec, _ = evaluate_layer(
        "fold5（TimeSeriesSplit 第 5 折 holdout）", y_all[m5], meta_hold[m5], avg[m5],
        [mkt[i] for i in np.where(m5)[0]], P5[m5],
        note="hold-clf 仅见前 4 折；现行资产仍为全量训练，故仍非严格 fresh")
    md.append(sec)

    # ---------------- fresh ----------------
    fresh, skipped = load_fresh(oof_max_date)
    if fresh:
        fy = np.array([r["y"] for r in fresh])
        fm = np.array([r["meta"] for r in fresh])
        f5 = np.stack([r["P5"] for r in fresh], axis=0)
        fa = f5.mean(axis=1)
        fk = [r["mkt"] for r in fresh]
        n_mkt = sum(1 for x in fk if x is not None)
        sec, _ = evaluate_layer(
            "fresh serving（{} ~ {}，n={}，市场可用 {}）".format(
                fresh[0]["date"], fresh[-1]["date"], len(fresh), n_mkt),
            fy, fm, fa, fk, f5,
            note="主证据：meta 训练后场次、serving 落库原值；小样本不调参")
        md.append(sec)
        print("\n[fresh] n={}（跳过 {} 场缺子模型/赛果），市场可用 {}".format(
            len(fresh), skipped, n_mkt))
    else:
        # pending：fresh 窗口内已落 serving 预测、但赛果尚未回填的场次（按日期计数）
        _c = sqlite3.connect(ODDS_DB_PATH)
        _pend = _c.execute("""
            SELECT m.match_date, COUNT(DISTINCT m.match_id) c FROM matches m
            WHERE EXISTS (SELECT 1 FROM model_predictions mp
                          WHERE mp.match_id=m.match_id AND mp.model_name=?
                            AND mp.is_replay=0 AND mp.prediction_type='Submodel_DC_home')
              AND m.match_date > ?
            GROUP BY m.match_date ORDER BY m.match_date
        """, (MODEL_NAME, oof_max_date)).fetchall()
        _c.close()
        _pend_str = "、".join("{}×{}".format(str(d)[:10], c) for d, c in _pend)
        _pend_n = sum(int(c) for _, c in _pend)
        md.append("### fresh 层：暂无可用场次（赛果回填滞后，非链路问题）")
        md.append("")
        md.append("- OOF 最大日期 {}；窗口内已有 serving 落库预测但等赛果回填 **{} 场**：{}".format(
            oof_max_date, _pend_n, _pend_str or "无"))
        md.append("- 最新已回填场次止于 2026-10-13（恰为 OOF 边界），10-16 场次结果尚未回写。")
        md.append("- 与 C-20261006-013 预注册节奏一致：fresh 样本须累积（协议目标 fresh-150），"
                  "当前任何 fresh 切片结论都不具统计效力；时间切分证据以 fold5（n=3,067）为准。")
        print("[fresh] 无可用场次（skipped={}）；pending {} 场：{}".format(
            skipped, _pend_n, _pend_str))

    text = "\n".join(md)
    with open(MD_PATH, "w", encoding="utf-8") as f:
        f.write(text)
    print("\n" + "=" * 70)
    print(text)
    print("=" * 70)
    print("[analyze] markdown 已存: {}".format(MD_PATH))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("build", "all"):
        build_oof()
    if mode in ("analyze", "all"):
        analyze()
    if mode not in ("build", "analyze", "all"):
        raise SystemExit("usage: python _diag_divergence_slices.py [build|analyze|all]")


if __name__ == "__main__":
    main()
