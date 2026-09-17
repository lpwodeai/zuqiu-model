# -*- coding: utf-8 -*-
"""edge 分桶单调回归修复实验。

背景：
  C-20260904-001 EV 择场 72 组合扫描证实「阈值抬升+置信过滤」纯择场无法使 ROI 转正，
  且发现核心反直觉规律：min_ev 抬升时平均 EV 升、ROI 反而恶化 → **edge 排序失效**
  （高 EV=高赔率方向实际胜率系统性低于 EV 隐含胜率，edge 分桶非单调，>10pp 桶仍 -6.53%）。

本脚本沿 CALIB-007 ③路径：对模型概率做**保序单调回归（isotonic reliability regression）**，
把「模型预测概率 p → 实际发生频率」的映射拉回单调一致，从而修正 edge 的系统性高估，
验证修正后 edge 分桶是否恢复单调、高 edge 桶 ROI 是否改善/转正。

关键设计（严格防泄漏 + 不破坏平局召回）：
  1. 输入 = OOF mean raw 概率（xgb+lgb 均值），在 **raw** 上做保序（避免对 Platt 再叠
     parametric 缩放，规避 CALIB-001/005 的坍缩陷阱）。
  2. Mono-Pooled：把三类的 (p, y) 全部 pooled 起来拟合**单一单调映射**
     p→P(y=1|p)（即可靠性图回归）。单一单调映射作用于三类概率后 **argmax 排序不变**，
     因此平局召回（argmax 口径）结构性保持，天然满足 CALIB-003。
  3. Mono-OVR（对照）：逐类一对其余保序回归（预期平局类可能坍缩，用于反证 pooled 的必要性）。
  4. 无泄漏：TimeSeriesSplit(5)（与 calibration benchmark 同构）折内训练集拟合、
     折外验证集应用，逐折拼回；评价严格限制在同一 9970 场子集（最早 1995 场属折1
     训练段无折外校准输出）。

评估：
  - 概率质量：LogLoss / Brier / Acc / 平局召回（argmax）
  - EV 回测：整体平注/凯利 ROI + **按 edge 分桶**的 n/命中率/ROI/模型概率/实现频率
    （验证修正后分桶单调性）

用法：
  python edge_monotonic_fix.py
  python edge_monotonic_fix.py --oof ../assets/oof_predictions_20260903_012452.csv

产物：
  reports/edge_monotonic_fix_<TS>.md/.json
  assets/edge_mono_probs_<TS>.csv（逐场修正后概率，供下游复现）
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ev_engine import analyze_match, calc_kelly, ModelProbabilities, OddsData  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"
DB_PATH = PROJECT_DIR / "data" / "odds.db"

RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}
CLASS_NAMES = ["away", "draw", "home"]  # actual_result: 0=away, 1=draw, 2=home
EDGE_BUCKETS = [(0.0, 0.03, "0~3pp"), (0.03, 0.06, "3~6pp"),
                (0.06, 0.10, "6~10pp"), (0.10, 1.0, ">10pp")]


def safe_logits(probs, eps=1e-8):
    p = np.clip(probs, eps, 1 - eps)
    logits = np.log(p)
    return logits - logits.mean(axis=1, keepdims=True)


class TemperatureScaler:
    """多分类温度缩放（与 calibration benchmark 同源，供 Mono-on-Temp 与对照使用）。"""

    def __init__(self):
        self.T_ = 1.0

    def fit(self, y_true, y_proba):
        logits = safe_logits(y_proba)

        def nll(T):
            if T <= 1e-3:
                return 1e10
            scaled = logits / T
            m = scaled.max(axis=1, keepdims=True)
            exp = np.exp(scaled - m)
            p = exp / exp.sum(axis=1, keepdims=True)
            p_cls = p[np.arange(len(p)), y_true]
            return -np.mean(np.log(np.clip(p_cls, 1e-9, 1 - 1e-9)))

        res = minimize_scalar(nll, bounds=(0.2, 5.0), method="bounded")
        self.T_ = float(res.x)
        return self

    def transform(self, y_proba):
        logits = safe_logits(y_proba)
        scaled = logits / self.T_
        m = scaled.max(axis=1, keepdims=True)
        exp = np.exp(scaled - m)
        p = exp / exp.sum(axis=1, keepdims=True)
        p = np.clip(p, 1e-7, 1 - 1e-7)
        return p / p.sum(axis=1, keepdims=True)


class PooledIsotonic:
    """单一单调可靠性回归：pool 三类 (p, y)，拟合 p → P(y|p)。

    由于映射对三类共用且保序，transform 后各场 argmax 不变 → 平局召回结构性保持。
    """

    def __init__(self):
        self.iso_ = None

    def fit(self, y_true, y_proba):
        N, C = y_proba.shape
        X = y_proba.ravel()  # (N*C,)
        if C == 1:
            Y = np.asarray(y_true, dtype=float).ravel()  # 二值 0/1
        else:
            Y = np.repeat(np.eye(C, dtype=int)[y_true], 1, axis=0).ravel()  # one-hot 展平
        iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6)
        iso.fit(X, Y)
        self.iso_ = iso
        return self

    def transform(self, y_proba):
        p = self.iso_.predict(y_proba.ravel()).reshape(y_proba.shape)
        p = np.clip(p, 1e-7, 1 - 1e-7)
        return p / p.sum(axis=1, keepdims=True)

    def predict(self, x):
        """对 1-D/2-D 输入做保序预测（供选择条件化路径单点调用）。"""
        return np.asarray(self.iso_.predict(np.asarray(x, dtype=float).ravel()))


class IsotonicOVR:
    """逐类一对其余保序回归（对照：预期平局类可能坍缩）。"""

    def __init__(self):
        self.isos_ = []

    def fit(self, y_true, y_proba):
        self.isos_ = []
        for c in range(y_proba.shape[1]):
            iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6)
            iso.fit(y_proba[:, c], (y_true == c).astype(int))
            self.isos_.append(iso)
        return self

    def transform(self, y_proba):
        p = np.column_stack([iso.predict(y_proba[:, c]) for c, iso in enumerate(self.isos_)])
        p = np.clip(p, 1e-7, 1 - 1e-7)
        return p / p.sum(axis=1, keepdims=True)


def load_oof(csv_path: Path):
    """读取 OOF CSV，构造 mean raw / mean platt 概率矩阵。"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    df = df[df["actual_result"].notna()].copy()
    df["actual_result"] = df["actual_result"].astype(int)
    for prefix in ("raw", "platt"):
        for d in ("away", "draw", "home"):
            df[f"mean_{prefix}_{d}"] = (df[f"xgb_{prefix}_{d}"] + df[f"lgb_{prefix}_{d}"]) / 2.0
    for src in ("xgb", "lgb", "mean"):
        cols = [f"{src}_platt_{d}" for d in ("away", "draw", "home")]
        s = df[cols].sum(axis=1).values
        for c in cols:
            df[c] = df[c].values / s
    return df


def prob_matrix(df, src, kind):
    cols = [f"{src}_{kind}_{d}" for d in ("away", "draw", "home")]
    return df[cols].values.astype(float)


def build_odds_bridge(db_path: Path):
    """odds500_live 映射 + 中文 match_id 队名桥（与 ev_threshold_sweep 同构）。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    odds_map, bridge = {}, {}
    cur.execute("SELECT match_id, match_date, home_team_cn, away_team_cn FROM odds500_match")
    for mid, md, h, a in cur.fetchall():
        if not mid:
            continue
        bridge[(md or "")[:10], normalize_team_name(h), normalize_team_name(a)] = mid
    cur.execute("SELECT match_id, avg_live_win, avg_live_draw, avg_live_lose FROM odds500_ouzhi_summary")
    for mid, w, d, l in cur.fetchall():
        try:
            hw, dd, aw = float(w), float(d), float(l)
            if hw > 1 and dd > 1 and aw > 1:
                odds_map[mid] = (hw, dd, aw)
        except (TypeError, ValueError):
            continue
    conn.close()
    return odds_map, bridge


def edge_bucket(edge):
    for lo, hi, label in EDGE_BUCKETS:
        if lo <= edge < hi:
            return label
    return "unknown"


def resolve_odds_key(row, odds_map, bridge):
    """直接 match_id 或中文队名桥回退，返回赔率键（None 表示无赔率）。"""
    key = row["match_id"]
    if key not in odds_map:
        alt = bridge.get(((row.get("date") or "")[:10],
                          normalize_team_name(row.get("home_team_name") or ""),
                          normalize_team_name(row.get("away_team_name") or "")))
        if alt and alt in odds_map:
            key = alt
        else:
            return None
    return key


def run_ev_for_row(row, o, probs_i):
    """对单场跑 ev_engine，返回 (analysis, 非AVOID候选的max-EV方向) 或 (None, None)。"""
    try:
        analysis = analyze_match(
            probs=ModelProbabilities(home=float(probs_i[2]), draw=float(probs_i[1]), away=float(probs_i[0])),
            odds=OddsData(home=float(o[0]), draw=float(o[1]), away=float(o[2])),
            ev_threshold=0.02, kelly_strategy="quarter", kelly_cap=0.25)
    except Exception:
        return None, None
    cands = [d for d in (analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis)
             if d.decision != "AVOID"]
    if not cands:
        return analysis, None
    return analysis, max(cands, key=lambda d: d.ev)


def fit_selected_iso(sub_df, temp_probs, odds_map, bridge):
    """在训练折上拟合「选择条件化」保序：对 EV 引擎选出的 max-EV 方向，
    收集 (p_model_selected, 是否命中) 拟合 p → P(win | 被选中)。
    该映射捕获 max-edge 选择的 winner's curse 高估。"""
    ps, ys = [], []
    for i in range(len(sub_df)):
        row = sub_df.iloc[i]
        key = resolve_odds_key(row, odds_map, bridge)
        if key is None:
            continue
        _, bet_dir = run_ev_for_row(row, odds_map[key], temp_probs[i])
        if bet_dir is None:
            continue
        ps.append(bet_dir.p_model)
        ys.append(int(RESULT_TO_DIRECTION[int(row["actual_result"])] == bet_dir.direction))
    ps = np.array(ps, dtype=float).reshape(-1, 1)
    ys = np.array(ys, dtype=int)
    iso = PooledIsotonic().fit(ys, ps)
    return iso


def acc_selected_iso(sub_df, temp_probs, iso_sel, odds_map, bridge, acc, min_ev=0.02):
    """选择条件化回测（累加到 acc）：先用 Temp 概率选出 max-EV 方向，
    再用 iso_sel 修正该方向 p_model → 重算 edge/EV，满足 VALUE 才下注。"""
    for i in range(len(sub_df)):
        row = sub_df.iloc[i]
        key = resolve_odds_key(row, odds_map, bridge)
        if key is None:
            continue
        _, bet_dir = run_ev_for_row(row, odds_map[key], temp_probs[i])
        if bet_dir is None:
            continue
        p_corr = float(iso_sel.predict(np.array([[bet_dir.p_model]]))[0])
        p_corr = min(max(p_corr, 1e-7), 1 - 1e-7)
        new_edge = p_corr - bet_dir.p_market
        new_ev = p_corr * (bet_dir.odds - 1.0) - (1.0 - p_corr)
        if not (new_ev > min_ev and new_edge > 0):
            continue
        won = (RESULT_TO_DIRECTION[int(row["actual_result"])] == bet_dir.direction)
        odds_val = bet_dir.odds
        _, _, stake_k = calc_kelly(p_corr, odds_val, "quarter", 0.25)
        acc["flat_p"].append((odds_val - 1.0) if won else -1.0)
        acc["kelly_p"].append(stake_k * (odds_val - 1.0) if won else -stake_k)
        acc["stakes"].append(stake_k)
        acc["evs"].append(new_ev)
        acc["odds_l"].append(odds_val)
        acc["p_models"].append(p_corr)
        acc["wins"] += int(won)
        acc["bets"] += 1
        b = edge_bucket(new_edge)
        acc["by_edge"][b]["n"] += 1
        acc["by_edge"][b]["wins"] += int(won)
        acc["by_edge"][b]["profit"] += (odds_val - 1.0) if won else -1.0
        acc["by_edge"][b]["pm"] += p_corr
        acc["by_edge"][b]["odds"] += odds_val
        acc["by_edge"][b]["ev"] += new_ev


def finalize_acc(acc):
    """把累加器转成与 ev_backtest_with_buckets 同构的结果 dict。"""
    n = len(acc["flat_p"])
    buckets = {}
    for k in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]:
        v = acc["by_edge"].get(k)
        if not v or v["n"] == 0:
            continue
        nb = v["n"]
        buckets[k] = {
            "n": nb,
            "hit_rate": v["wins"] / nb,
            "flat_roi": v["profit"] / nb,
            "flat_profit": v["profit"],
            "avg_model_p": v["pm"] / nb,
            "avg_odds": v["odds"] / nb,
            "avg_ev": v["ev"] / nb,
        }
    return {
        "bets": n,
        "hit_rate": (acc["wins"] / n) if n else 0.0,
        "flat_roi": (sum(acc["flat_p"]) / n) if n else 0.0,
        "flat_profit": sum(acc["flat_p"]),
        "kelly_roi": (sum(acc["kelly_p"]) / sum(acc["stakes"])) if acc["stakes"] else 0.0,
        "kelly_profit": sum(acc["kelly_p"]),
        "avg_ev": (sum(acc["evs"]) / n) if n else 0.0,
        "avg_odds": (sum(acc["odds_l"]) / n) if n else 0.0,
        "avg_model_p": (sum(acc["p_models"]) / n) if n else 0.0,
        "by_edge": buckets,
    }


def ev_backtest_with_buckets(df, probs, odds_map, bridge, min_ev=0.02):
    """逐场 ev_engine 回测，返回整体统计 + 按 edge 分桶统计（含模型概率 vs 实现频率）。"""
    flat_p, kelly_p, stakes, evs, odds_l, p_models = [], [], [], [], [], []
    wins = bets = 0
    by_edge = defaultdict(lambda: {"n": 0, "wins": 0, "profit": 0.0,
                                   "pm": 0.0, "odds": 0.0, "ev": 0.0})

    for i in range(len(df)):
        row = df.iloc[i]
        key = resolve_odds_key(row, odds_map, bridge)
        if key is None:
            continue
        o = odds_map[key]
        p = probs[i]
        _, bet_dir = run_ev_for_row(row, o, p)
        if bet_dir is None:
            continue
        won = (RESULT_TO_DIRECTION[int(row["actual_result"])] == bet_dir.direction)
        odds_val = bet_dir.odds
        stake_k = bet_dir.kelly_clipped
        flat_p.append((odds_val - 1.0) if won else -1.0)
        kelly_p.append(stake_k * (odds_val - 1.0) if won else -stake_k)
        stakes.append(stake_k)
        evs.append(bet_dir.ev)
        odds_l.append(odds_val)
        p_models.append(bet_dir.p_model)
        if won:
            wins += 1
        bets += 1
        b = edge_bucket(bet_dir.edge)
        by_edge[b]["n"] += 1
        by_edge[b]["wins"] += int(won)
        by_edge[b]["profit"] += (odds_val - 1.0) if won else -1.0
        by_edge[b]["pm"] += bet_dir.p_model
        by_edge[b]["odds"] += odds_val
        by_edge[b]["ev"] += bet_dir.ev

    n = len(flat_p)
    buckets = {}
    for k, v in by_edge.items():
        nb = v["n"]
        buckets[k] = {
            "n": nb,
            "hit_rate": v["wins"] / nb if nb else 0.0,
            "flat_roi": v["profit"] / nb if nb else 0.0,
            "flat_profit": v["profit"],
            "avg_model_p": v["pm"] / nb if nb else 0.0,
            "avg_odds": v["odds"] / nb if nb else 0.0,
            "avg_ev": v["ev"] / nb if nb else 0.0,
            # 实现频率 = 命中率；模型概率 = avg_model_p → 分桶内「高估幅度」= avg_model_p - hit_rate
        }
    return {
        "bets": n,
        "hit_rate": (wins / n) if n else 0.0,
        "flat_roi": (sum(flat_p) / n) if n else 0.0,
        "flat_profit": sum(flat_p),
        "kelly_roi": (sum(kelly_p) / sum(stakes)) if stakes else 0.0,
        "kelly_profit": sum(kelly_p),
        "avg_ev": (sum(evs) / n) if n else 0.0,
        "avg_odds": (sum(odds_l) / n) if n else 0.0,
        "avg_model_p": (sum(p_models) / n) if n else 0.0,
        "by_edge": {k: buckets[k] for k in ["0~3pp", "3~6pp", "6~10pp", ">10pp"] if k in buckets},
    }


def main():
    parser = argparse.ArgumentParser(description="edge 分桶单调回归修复")
    parser.add_argument("--oof", type=Path, default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    args = parser.parse_args()

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    logging.disable(logging.WARNING)

    print("[1/4] 加载 OOF 数据与赔率...")
    df = load_oof(args.oof)
    df = df.sort_values("date").reset_index(drop=True)
    y_true = df["actual_result"].values.astype(int)
    raw_probs = prob_matrix(df, src="mean", kind="raw")   # (N,3) [away,draw,home]
    platt_probs = prob_matrix(df, src="mean", kind="platt")
    odds_map, bridge = build_odds_bridge(DB_PATH)
    N = len(df)
    print(f"   OOF 样本: {N}  赔率映射: {len(odds_map)}  桥接对: {len(bridge)}")

    print("[2/4] 时序 5 折保序回归（折内 fit → 折外 transform）...")
    methods = ["Baseline(Platt)", "TempScaling(on raw)",
               "Mono-Pooled(on raw)", "Mono-OVR(on raw)", "Mono-Pooled(on Temp)"]
    outputs = {m: np.zeros_like(raw_probs) for m in methods}
    outputs["Baseline(Platt)"] = platt_probs.copy()

    # Mono-Selected(on Temp)：选择条件化保序（winner's curse 修正），独立累加
    sel_acc = {"flat_p": [], "kelly_p": [], "stakes": [], "evs": [], "odds_l": [],
               "p_models": [], "wins": 0, "bets": 0,
               "by_edge": defaultdict(lambda: {"n": 0, "wins": 0, "profit": 0.0,
                                               "pm": 0.0, "odds": 0.0, "ev": 0.0})}

    tscv = TimeSeriesSplit(n_splits=5)
    fold_info = []
    for fold, (tr_idx, va_idx) in enumerate(tscv.split(raw_probs)):
        print(f"     折 {fold + 1}/5: tr={len(tr_idx)}  va={len(va_idx)}")
        y_tr = y_true[tr_idx]
        fold_info.append({"fold": fold + 1, "tr": int(len(tr_idx)), "va": int(len(va_idx))})

        # TempScaling(on raw) —— 对照（当前首选校准）
        ts = TemperatureScaler().fit(y_tr, raw_probs[tr_idx])
        outputs["TempScaling(on raw)"][va_idx] = ts.transform(raw_probs[va_idx])
        temp_va = outputs["TempScaling(on raw)"][va_idx]
        temp_tr = ts.transform(raw_probs[tr_idx])

        # Mono-Pooled(on raw)：单一单调可靠性映射
        mono = PooledIsotonic().fit(y_tr, raw_probs[tr_idx])
        outputs["Mono-Pooled(on raw)"][va_idx] = mono.transform(raw_probs[va_idx])

        # Mono-OVR(on raw)：逐类保序（对照，预期平局坍缩）
        ovr = IsotonicOVR().fit(y_tr, raw_probs[tr_idx])
        outputs["Mono-OVR(on raw)"][va_idx] = ovr.transform(raw_probs[va_idx])

        # Mono-Pooled(on Temp)：先温度缩放再保序（叠加非参修正，pooled 保平局召回）
        mono2 = PooledIsotonic().fit(y_tr, temp_tr)
        outputs["Mono-Pooled(on Temp)"][va_idx] = mono2.transform(temp_va)

        # Mono-Selected(on Temp)：训练折拟合「被选中方向」保序，验证折应用（逐折累加）
        iso_sel = fit_selected_iso(
            df.iloc[tr_idx].reset_index(drop=True), temp_tr, odds_map, bridge)
        acc_selected_iso(df.iloc[va_idx].reset_index(drop=True), temp_va, iso_sel,
                         odds_map, bridge, sel_acc, min_ev=0.02)

    # 覆盖掩码：仅保留有折外校准输出的行（最早折1训练段 0）
    covered = outputs["Mono-Pooled(on raw)"].sum(axis=1) > 0
    sub = df[covered].reset_index(drop=True)
    y_sub = y_true[covered]
    sub_probs = {m: outputs[m][covered] for m in methods}
    print(f"   覆盖行(折外可评估): {int(covered.sum())} / {N}")

    print("[3/4] 概率质量 + EV 回测（含 edge 分桶）...")
    metrics, ev_results = {}, {}
    for m in methods:
        p = sub_probs[m]
        pred = p.argmax(axis=1)
        metrics[m] = {
            "log_loss": float(log_loss(y_sub, p, labels=[0, 1, 2])),
            "brier": float(np.mean(np.sum((p - np.eye(3)[y_sub]) ** 2, axis=1) / 3)),
            "acc": float(accuracy_score(y_sub, pred)),
            # 平局召回（argmax 口径，项目硬性约束 CALIB-003：≥0.28）
            "recall_draw_argmax": float(np.sum((y_sub == 1) & (pred == 1)) / max(np.sum(y_sub == 1), 1)),
        }
        ev_results[m] = ev_backtest_with_buckets(sub, p, odds_map, bridge, min_ev=0.02)
        print(f"   {m:<24} 平ROI {ev_results[m]['flat_roi']*100:+6.2f}%  n={ev_results[m]['bets']}  "
              f"平召 {metrics[m]['recall_draw_argmax']*100:5.1f}%  LogLoss {metrics[m]['log_loss']:.4f}")

    # Mono-Selected(on Temp)：无独立全矩阵，复用 Temp 的 argmax 平局召回（选择/排序不变）
    sel_res = finalize_acc(sel_acc)
    ev_results["Mono-Selected(on Temp)"] = sel_res
    metrics["Mono-Selected(on Temp)"] = dict(metrics["TempScaling(on raw)"])
    print(f"   {'Mono-Selected(on Temp)':<24} 平ROI {sel_res['flat_roi']*100:+6.2f}%  "
          f"n={sel_res['bets']}  平召 {metrics['TempScaling(on raw)']['recall_draw_argmax']*100:5.1f}%  "
          f"(winner's curse 修正)")

    print("[4/4] 生成报告...")
    lines = []
    lines.append("# edge 分桶单调回归修复实验\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- OOF 文件: `{args.oof.name}`（mean raw + mean platt）")
    lines.append(f"- 无泄漏方式: TimeSeriesSplit(5) 折内训练集拟合保序回归 → 折外验证集应用")
    lines.append(f"- 评估子集: 折外可评估 {int(covered.sum())} 场（最早 {N - int(covered.sum())} 场为折1训练段）")
    lines.append(f"- 赔率源: odds500_live + 中文队名桥接（96.4% 对齐）；min_ev=0.02 / quarter-kelly cap=0.25\n")
    lines.append(f"- 折信息: {json.dumps(fold_info, ensure_ascii=False)}\n")

    all_methods = methods + ["Mono-Selected(on Temp)"]

    lines.append("## 一、概率质量\n")
    lines.append("| 方法 | LogLoss | Brier | Acc | 平局召回(argmax) | 平局召回达标(≥0.28) |")
    lines.append("|---|---|---|---|---|---|")
    for m in methods:
        md = metrics[m]
        flag = "✅" if md["recall_draw_argmax"] >= 0.28 else "❌"
        lines.append(f"| {m} | {md['log_loss']:.4f} | {md['brier']:.4f} | {md['acc']*100:.2f}% | "
                     f"{md['recall_draw_argmax']*100:.2f}% | {flag} |")
    md = metrics["Mono-Selected(on Temp)"]
    lines.append(f"| Mono-Selected(on Temp) | — | — | — | {md['recall_draw_argmax']*100:.2f}%（复用Temp） | "
                 f"{'✅' if md['recall_draw_argmax']>=0.28 else '❌'} |")

    lines.append("\n## 二、EV 回测整体\n")
    lines.append("| 方法 | 投注n | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV | 平均赔率 | 平均模型概率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for m in all_methods:
        e = ev_results[m]
        lines.append(f"| {m} | {e['bets']} | {e['hit_rate']*100:.1f}% | {e['flat_roi']*100:+.2f}% | "
                     f"{e['flat_profit']:+.2f} | {e['kelly_roi']*100:+.2f}% | {e['avg_ev']*100:+.2f}% | "
                     f"{e['avg_odds']:.2f} | {e['avg_model_p']*100:.1f}% |")

    lines.append("\n## 三、按 edge 分桶（验证单调性恢复）\n")
    lines.append("| 方法 | 桶 | n | 命中率 | 平注ROI | 模型概率 | 实现频率 | 高估幅度(pp) | 平均EV | 平均赔率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for m in all_methods:
        for bk in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]:
            v = ev_results[m]["by_edge"].get(bk)
            if not v:
                continue
            over = (v["avg_model_p"] - v["hit_rate"]) * 100
            lines.append(f"| {m} | {bk} | {v['n']} | {v['hit_rate']*100:.1f}% | {v['flat_roi']*100:+.2f}% | "
                         f"{v['avg_model_p']*100:.1f}% | {v['hit_rate']*100:.1f}% | {over:+.1f} | "
                         f"{v['avg_ev']*100:+.2f}% | {v['avg_odds']:.2f} |")

    # 单调性判定：按 0-3 < 3-6 < 6-10 < >10 检查 ROI 是否单调递增
    lines.append("\n## 四、单调性结论\n")
    for m in all_methods:
        seq = [(bk, ev_results[m]["by_edge"].get(bk)) for bk in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]]
        seq = [(bk, v) for bk, v in seq if v and v["n"] >= 50]
        if len(seq) < 2:
            lines.append(f"- {m}: 分桶样本不足，无法判定单调性")
            continue
        rois = [v["flat_roi"] for _, v in seq]
        mono = all(rois[i] <= rois[i + 1] + 1e-9 for i in range(len(rois) - 1))
        trend = "单调递增 ✅" if mono else "非单调 ❌"
        detail = " → ".join(f"{bk} {v['flat_roi']*100:+.1f}%" for bk, v in seq)
        lines.append(f"- {m}: {trend}  ({detail})")

    base = ev_results["Baseline(Platt)"]
    best = max((m for m in all_methods if ev_results[m]["bets"] >= 100),
               key=lambda m: ev_results[m]["flat_roi"], default=None)
    lines.append(f"\n## 五、结论\n")
    if best is None:
        lines.append("- 无可评估（投注 n<100）的组合。")
    else:
        be = ev_results[best]
        lines.append(f"- 最优方案: **{best}**  平注ROI {be['flat_roi']*100:+.2f}% "
                     f"(基线 {base['flat_roi']*100:+.2f}%, Δ={((be['flat_roi']-base['flat_roi'])*100):+.2f}pp)  n={be['bets']}")
    pos = [(m, ev_results[m]) for m in all_methods if ev_results[m]["flat_roi"] > 0 and ev_results[m]["bets"] >= 100]
    lines.append(f"- ROI 转正组合（n≥100）: {len(pos)} 个")
    for m, e in pos:
        lines.append(f"  - {m}: ROI {e['flat_roi']*100:+.2f}%  n={e['bets']}  平召 {metrics[m]['recall_draw_argmax']*100:.1f}%")

    md_path = REPORT_DIR / f"edge_monotonic_fix_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"edge_monotonic_fix_{RUN_TS}.json"
    json_path.write_text(json.dumps({
        "meta": {"oof": str(args.oof), "N": int(N), "covered": int(covered.sum()),
                 "ts": RUN_TS, "fold_info": fold_info},
        "metrics": metrics,
        "ev_results": ev_results,
    }, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")

    # 导出修正后概率 CSV
    export_rows = []
    for i in np.where(covered)[0]:
        row = df.iloc[i]
        rec = {"match_id": row["match_id"], "date": row.get("date", ""),
               "home_team_name": row.get("home_team_name", ""),
               "away_team_name": row.get("away_team_name", ""),
               "competition_name": row.get("competition_name", ""),
               "actual_result": int(row["actual_result"])}
        for m in methods:
            safe = m.replace("(", "").replace(")", "").replace("+", "_plus_")
            p = outputs[m][i]
            rec[f"{safe}_away"] = float(p[0])
            rec[f"{safe}_draw"] = float(p[1])
            rec[f"{safe}_home"] = float(p[2])
        export_rows.append(rec)
    csv_path = ASSETS_DIR / f"edge_mono_probs_{RUN_TS}.csv"
    pd.DataFrame(export_rows).to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"✅ 修正后概率 CSV: {csv_path}")


if __name__ == "__main__":
    main()
