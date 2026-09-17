# -*- coding: utf-8 -*-
"""概率校准对比实验（基于严格时序 OOF 预测）。

背景：
  208 维模型在严格 OOF 回测中呈现系统性概率高估：LogLoss 0.99、平局召回
  0.12~0.15、平均 EV +21% vs 平注 ROI -7.8%。本脚本在同一时序 OOF 样本上，
  对比多种校准方法对概率质量的改善，并端到端评估 EV 回测 ROI 是否转正。

校准方法（全部基于「折内训练集拟合、折外验证集应用」，避免校准泄漏）：
  1. Baseline  : 现有 Platt 校准（OOF 生成时逐折已做）
  2. TempScaling: 多分类温度缩放 —— softmax(logits / T)，T 以折内 NLL 最优搜索
  3. VectorScaling: 逐类温度 + 偏置（3×T + 3×b），以折内 NLL 优化
  4. Isotonic-OVR: 对每类做一对其余保序回归，再归一化（Zadrozny & Elkan）
  5. Isotonic-OVR-Mono: 同上 + 单调性微调（renormalize 保序）
  6. TempScaling + DrawCalibrator (factor=0.95): 温度缩放后叠加平局阈值校准

评估维度：
  - 三分类 LogLoss / Brier / Accuracy
  - 每类 ECE（Expected Calibration Error, 10-bin）
  - 平局召回率 / 平局精度（必须 ≥0.28 目标线）
  - 端到端 EV 回测平注 ROI（复用 ev_engine + ev_backtest 赔率逻辑）

用法：
  python probability_calibration_benchmark.py
  python probability_calibration_benchmark.py --oof assets/oof_predictions_20260903_012452.csv

输出：
  - reports/calibration_benchmark_<TS>.md  详细对比报告
  - reports/calibration_benchmark_<TS>.json 原始数据
  - assets/calibrated_probs_<TS>.csv        逐场校准后概率（可复用于后续实验）
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar, minimize
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import accuracy_score, log_loss, recall_score, precision_score
from sklearn.model_selection import TimeSeriesSplit

warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"
DB_PATH = PROJECT_DIR / "data" / "odds.db"

sys.path.insert(0, str(SCRIPT_DIR))
from ev_engine import analyze_match, ModelProbabilities, OddsData  # noqa: E402
from draw_calibrator import DrawCalibrator  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402

RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}
CLASS_NAMES = ["away", "draw", "home"]  # 与 actual_result 一致: 0=away, 1=draw, 2=home
N_BINS_ECE = 10


# ============================================================
# 1. 数据加载
# ============================================================
def load_oof(csv_path: Path, model: str = "mean"):
    """读取 OOF CSV，返回 DataFrame 并构造模型概率矩阵。

    返回:
      df: DataFrame，新增列 mean_raw_home/draw/away + mean_platt_home/draw/away
    """
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    df = df[df["actual_result"].notna()].copy()
    df["actual_result"] = df["actual_result"].astype(int)

    # 构造 mean 模型概率（raw & platt）
    for prefix in ("raw", "platt"):
        for d in ("away", "draw", "home"):
            x = df[f"xgb_{prefix}_{d}"].values
            l = df[f"lgb_{prefix}_{d}"].values
            df[f"mean_{prefix}_{d}"] = (x + l) / 2.0

    # 确保 platt 归一化
    for src in ("xgb", "lgb", "mean"):
        cols = [f"{src}_platt_{d}" for d in ("away", "draw", "home")]
        s = df[cols].sum(axis=1).values
        for c in cols:
            df[c] = df[c].values / s

    return df


def prob_matrix(df, src: str, kind: str):
    """取 shape=(N,3) 的概率矩阵，列顺序 [away, draw, home] 即 [0,1,2]。"""
    cols = [f"{src}_{kind}_{d}" for d in ("away", "draw", "home")]
    return df[cols].values.astype(float)


def build_odds_bridge(db_path: Path):
    """同 ev_backtest.build_team_bridge，返回 {match_id/桥键: odds_tuple} + bridge。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    odds_map = {}
    bridge = {}
    cur.execute("SELECT match_id, match_date, home_team_cn, away_team_cn FROM odds500_match")
    for mid, md, h, a in cur.fetchall():
        if not mid:
            continue
        d = (md or "")[:10]
        bridge[(d, normalize_team_name(h), normalize_team_name(a))] = mid
    cur.execute(
        "SELECT match_id, avg_live_win, avg_live_draw, avg_live_lose FROM odds500_ouzhi_summary"
    )
    for mid, w, d, l in cur.fetchall():
        try:
            hw, dd, aw = float(w), float(d), float(l)
            if hw > 1 and dd > 1 and aw > 1:
                odds_map[mid] = (hw, dd, aw)
        except (TypeError, ValueError):
            continue
    conn.close()
    return odds_map, bridge


# ============================================================
# 2. 校准器
# ============================================================
def safe_logits(probs, eps=1e-8):
    p = np.clip(probs, eps, 1 - eps)
    logits = np.log(p)
    return logits - logits.mean(axis=1, keepdims=True)  # 中心对齐，避免平移歧义


class TemperatureScaler:
    """多分类温度缩放: softmax(logits / T)。T 在折内搜索最小 NLL。"""

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
            eps = 1e-9
            p_cls = p[np.arange(len(p)), y_true]
            return -np.mean(np.log(np.clip(p_cls, eps, 1 - eps)))

        res = minimize_scalar(nll, bounds=(0.2, 5.0), method="bounded")
        self.T_ = float(res.x)
        return self

    def transform(self, y_proba):
        logits = safe_logits(y_proba)
        scaled = logits / self.T_
        m = scaled.max(axis=1, keepdims=True)
        exp = np.exp(scaled - m)
        p = exp / exp.sum(axis=1, keepdims=True)
        # 数值稳定：若某类出现极端 0/1（指数下溢），做轻微回拉
        eps = 1e-7
        p = np.clip(p, eps, 1 - eps)
        return p / p.sum(axis=1, keepdims=True)


class VectorScaler:
    """逐类温度 + 偏置: p_c = softmax( logits_c / T_c + b_c )。
    6 个参数通过折内 NLL 优化（L-BFGS-B），参数边界保证稳定性。
    """

    def __init__(self):
        self.params_ = None  # (T0, T1, T2, b0, b1, b2)

    def fit(self, y_true, y_proba):
        logits = safe_logits(y_proba)
        N, C = y_proba.shape
        one_hot = np.zeros_like(y_proba)
        one_hot[np.arange(N), y_true] = 1.0

        def nll(params):
            T = params[:C]
            b = params[C:]
            T = np.clip(T, 1e-2, None)
            scaled = logits / T + b
            m = scaled.max(axis=1, keepdims=True)
            exp = np.exp(scaled - m)
            p = exp / exp.sum(axis=1, keepdims=True)
            p_cls = np.sum(p * one_hot, axis=1)
            return -np.mean(np.log(np.clip(p_cls, 1e-9, 1 - eps_for_clip())))

        def eps_for_clip():
            return 1e-9

        x0 = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
        bounds = [(0.1, 8.0)] * C + [(-3.0, 3.0)] * C
        res = minimize(nll, x0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 500})
        self.params_ = res.x
        return self

    def transform(self, y_proba):
        logits = safe_logits(y_proba)
        C = 3
        T = self.params_[:C]
        b = self.params_[C:]
        scaled = logits / T + b
        m = scaled.max(axis=1, keepdims=True)
        exp = np.exp(scaled - m)
        p = exp / exp.sum(axis=1, keepdims=True)
        eps = 1e-7
        p = np.clip(p, eps, 1 - eps)
        return p / p.sum(axis=1, keepdims=True)


class IsotonicOVR:
    """一对其余保序回归。对每个类 c:
      - 训练: binary label = (y==c) vs probability y_proba[:, c]
      - 预测: 每类 Iso 预测后做归一化（在训练集上再做重标，保留概率总和一致性）
    """

    def __init__(self):
        self.isos_ = []  # list[IsotonicRegression]

    def fit(self, y_true, y_proba):
        N, C = y_proba.shape
        self.isos_ = []
        for c in range(C):
            y_bin = (y_true == c).astype(int)
            iso = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1 - 1e-6)
            iso.fit(y_proba[:, c], y_bin)
            self.isos_.append(iso)
        return self

    def transform(self, y_proba):
        cols = []
        for c, iso in enumerate(self.isos_):
            cols.append(iso.transform(y_proba[:, c]))
        p = np.column_stack(cols)
        # 若有类全为 0，会导致归一化 NaN
        eps = 1e-7
        p = np.clip(p, eps, 1 - eps)
        s = p.sum(axis=1, keepdims=True)
        return p / s


# ============================================================
# 3. 评估指标
# ============================================================
def multiclass_brier(y_true, y_proba, n_classes=3):
    one_hot = np.zeros((len(y_true), n_classes))
    one_hot[np.arange(len(y_true)), y_true] = 1.0
    return float(np.mean(np.sum((y_proba - one_hot) ** 2, axis=1) / n_classes))


def per_class_ece(y_true, y_proba, n_classes=3, n_bins=N_BINS_ECE):
    """返回 {class_index: ece_value}。ECE = sum (n_bin/N * |acc_bin - conf_bin|)。"""
    eces = {}
    for c in range(n_classes):
        conf = y_proba[:, c]
        correct = (y_true == c).astype(float)
        bins = np.linspace(0, 1, n_bins + 1)
        bin_ids = np.digitize(conf, bins[1:-1])
        ece = 0.0
        for b in range(n_bins):
            m = bin_ids == b
            if not m.any():
                continue
            n_b = m.sum()
            acc_b = correct[m].mean()
            conf_b = conf[m].mean()
            ece += (n_b / len(y_true)) * abs(acc_b - conf_b)
        eces[c] = float(ece)
    return eces


def expected_calibration_error(y_true, y_proba, n_bins=N_BINS_ECE):
    """多分类 top-class ECE（argmax 置信 vs argmax 准确率）。"""
    pred_cls = np.argmax(y_proba, axis=1)
    conf = y_proba.max(axis=1)
    correct = (y_true == pred_cls).astype(float)
    bins = np.linspace(0, 1, n_bins + 1)
    bin_ids = np.digitize(conf, bins[1:-1])
    ece = 0.0
    N = len(y_true)
    for b in range(n_bins):
        m = bin_ids == b
        if not m.any():
            continue
        ece += (m.sum() / N) * abs(correct[m].mean() - conf[m].mean())
    return float(ece)


def eval_metrics(y_true, y_proba, name=""):
    y_pred = np.argmax(y_proba, axis=1)
    acc = accuracy_score(y_true, y_pred)
    # 显式指定 labels=[0,1,2] 并对每类概率做 clip，避免极端类造成的 log_loss 爆炸
    p_clip = np.clip(y_proba, 1e-7, 1 - 1e-7)
    p_clip = p_clip / p_clip.sum(axis=1, keepdims=True)
    ll = log_loss(y_true, p_clip, labels=[0, 1, 2])
    brier = multiclass_brier(y_true, y_proba)
    recalls = recall_score(y_true, y_pred, labels=[0, 1, 2], average=None, zero_division=0)
    precs = precision_score(y_true, y_pred, labels=[0, 1, 2], average=None, zero_division=0)
    per_ece = per_class_ece(y_true, y_proba)
    top_ece = expected_calibration_error(y_true, y_proba)
    return {
        "name": name,
        "accuracy": float(acc),
        "log_loss": float(ll),
        "brier": float(brier),
        "recall_away": float(recalls[0]),
        "recall_draw": float(recalls[1]),
        "recall_home": float(recalls[2]),
        "precision_away": float(precs[0]),
        "precision_draw": float(precs[1]),
        "precision_home": float(precs[2]),
        "ece_away": per_ece[0],
        "ece_draw": per_ece[1],
        "ece_home": per_ece[2],
        "ece_top": top_ece,
        "mean_per_class_ece": float(np.mean([per_ece[0], per_ece[1], per_ece[2]])),
    }


# ============================================================
# 4. EV 回测（复用 ev_engine 逻辑，在整套校准概率上评估）
# ============================================================
def run_ev_backtest(df, calibrated_probs, odds_map, bridge,
                    min_ev=0.02, kelly_strategy="quarter", kelly_cap=0.25):
    """calibrated_probs: (N,3) [away,draw,home]。返回平注 ROI + 汇总。"""
    N = len(df)
    assert calibrated_probs.shape == (N, 3)
    flat_profits = []
    kelly_profits = []
    ev_list = []
    bets = 0
    with_odds = 0
    no_odds = 0
    for i in range(N):
        row = df.iloc[i]
        match_id = row["match_id"]
        key = match_id
        if key not in odds_map:
            alt = bridge.get(((row.get("date") or "")[:10],
                              normalize_team_name(row.get("home_team_name") or ""),
                              normalize_team_name(row.get("away_team_name") or "")))
            if alt and alt in odds_map:
                key = alt
        o = odds_map.get(key)
        if o is None:
            no_odds += 1
            continue
        with_odds += 1
        p = calibrated_probs[i]
        try:
            analysis = analyze_match(
                probs=ModelProbabilities(home=float(p[2]), draw=float(p[1]), away=float(p[0])),
                odds=OddsData(home=float(o[0]), draw=float(o[1]), away=float(o[2])),
                ev_threshold=min_ev,
                kelly_strategy=kelly_strategy,
                kelly_cap=kelly_cap,
            )
        except Exception:
            continue
        cands = [d for d in (analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis)
                 if d.decision != "AVOID"]
        if not cands:
            continue
        bet_dir = max(cands, key=lambda d: d.ev)
        actual = RESULT_TO_DIRECTION[int(row["actual_result"])]
        won = (actual == bet_dir.direction)
        odds_val = bet_dir.odds
        flat_profits.append((odds_val - 1.0) if won else -1.0)
        stake_k = bet_dir.kelly_clipped
        kelly_profits.append(stake_k * (odds_val - 1.0) if won else -stake_k)
        ev_list.append(bet_dir.ev)
        bets += 1
    n = len(flat_profits)
    if n == 0:
        return {"bets": 0, "with_odds": with_odds, "no_odds": no_odds}
    total_stake = n
    kelly_stake = sum(
        # 近似：直接累加
        0.0 if not flat_profits else 0.0
    )
    # 用真实累加
    kelly_stakes = []
    # 这里简化：我们在循环里未保存 stake 单独列，重算一遍
    # 直接算凯利 ROI：sum(kelly_profits) / sum(stakes)
    stakes_recompute = []
    # 为避免重循环，用更简单方式：凯利ROI = sum(kp) / sum(expected stake from kelly_cap=0.25 etc.)
    # 实际上 kelly_profits 已经有 stake 部分 (stake * profit_if_won or -stake)
    # 所以 ROI_kelly = sum(kp) / sum(stakes)。需要 stakes。
    # 简单做法：重新计算一遍。
    # 直接复用逻辑：
    return _detailed_ev(df, calibrated_probs, odds_map, bridge, min_ev, kelly_strategy, kelly_cap)


def _detailed_ev(df, calibrated_probs, odds_map, bridge, min_ev, kelly_strategy, kelly_cap):
    flat_profits = []
    kelly_profits = []
    stakes = []
    ev_list = []
    bets = 0
    with_odds = 0
    no_odds = 0
    wins = 0
    for i in range(len(df)):
        row = df.iloc[i]
        match_id = row["match_id"]
        key = match_id
        if key not in odds_map:
            alt = bridge.get(((row.get("date") or "")[:10],
                              normalize_team_name(row.get("home_team_name") or ""),
                              normalize_team_name(row.get("away_team_name") or "")))
            if alt and alt in odds_map:
                key = alt
        o = odds_map.get(key)
        if o is None:
            no_odds += 1
            continue
        with_odds += 1
        p = calibrated_probs[i]
        try:
            analysis = analyze_match(
                probs=ModelProbabilities(home=float(p[2]), draw=float(p[1]), away=float(p[0])),
                odds=OddsData(home=float(o[0]), draw=float(o[1]), away=float(o[2])),
                ev_threshold=min_ev,
                kelly_strategy=kelly_strategy,
                kelly_cap=kelly_cap,
            )
        except Exception:
            continue
        cands = [d for d in (analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis)
                 if d.decision != "AVOID"]
        if not cands:
            continue
        bet_dir = max(cands, key=lambda d: d.ev)
        actual = RESULT_TO_DIRECTION[int(row["actual_result"])]
        won = (actual == bet_dir.direction)
        odds_val = bet_dir.odds
        stake_k = bet_dir.kelly_clipped
        flat_profits.append((odds_val - 1.0) if won else -1.0)
        kelly_profits.append(stake_k * (odds_val - 1.0) if won else -stake_k)
        stakes.append(stake_k)
        ev_list.append(bet_dir.ev)
        if won:
            wins += 1
        bets += 1
    n = len(flat_profits)
    return {
        "with_odds": with_odds,
        "no_odds": no_odds,
        "bets": n,
        "hit_rate": (wins / n) if n else 0.0,
        "flat_roi": (sum(flat_profits) / n) if n else 0.0,
        "flat_profit": sum(flat_profits),
        "kelly_roi": (sum(kelly_profits) / sum(stakes)) if stakes else 0.0,
        "kelly_profit": sum(kelly_profits),
        "avg_ev": (sum(ev_list) / n) if n else 0.0,
    }


# ============================================================
# 5. 主流程：时序折内校准 + 逐折拼接 + 整体评估
# ============================================================
def run_calibration_benchmark(oof_path: Path, model: str):
    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)

    print("[1/5] 加载 OOF 数据与赔率...")
    df = load_oof(oof_path, model=model)
    N = len(df)
    # 排序（严格按日期升序，保证 TSS 拆分时序一致）
    df = df.sort_values("date").reset_index(drop=True)
    y_true = df["actual_result"].values.astype(int)
    # 取 base 概率：Platt 校准后的概率作为 baseline 输入（与 ev_backtest 一致）
    base_probs = prob_matrix(df, src=model, kind="platt")  # (N,3) [away,draw,home]
    # raw 概率：供温度缩放使用 logits 更精确（注意：raw 本身也是归一化的概率）
    raw_probs = prob_matrix(df, src=model, kind="raw")

    odds_map, bridge = build_odds_bridge(DB_PATH)
    print(f"    OOF 样本: {N}  赔率映射: {len(odds_map)}  桥接对: {len(bridge)}")

    print("[2/5] 时序 5 折校准（逐折 fit→transform 防泄漏）...")
    tscv = TimeSeriesSplit(n_splits=5)

    # 方法说明：
    #   - Baseline(Platt)  : 现有 OOF Platt 概率（对照）
    #   - TempScaling      : 直接对 raw 概率反推 logits 做温度缩放（避免对 Platt 再缩放松弛）
    #   - VectorScaling    : 同上，逐类独立 T+b
    #   - Isotonic-OVR     : 对 Platt 概率做一对其余保序回归（单调修正更适合后校准概率）
    #   - Temp+DrawCal(0.30) : 温度缩放 + 平局召回目标 0.30 的 DrawCalibrator
    #   - Iso+DrawCal(0.30)  : 保序回归 + 平局召回目标 0.30 的 DrawCalibrator
    method_names = [
        "Baseline(Platt)",
        "TempScaling(on raw)",
        "VectorScaling(on raw)",
        "Isotonic-OVR(on platt)",
        "Temp+DrawCal(0.30)",
        "Iso+DrawCal(0.30)",
    ]
    method_outputs = {m: np.zeros_like(base_probs) for m in method_names}
    # Baseline 直接用 Platt
    method_outputs["Baseline(Platt)"] = base_probs.copy()

    # 其他方法：逐折拟合后填入验证集
    scalers_temp = []
    scalers_vec = []
    scalers_iso = []
    scalers_draw_t = []
    scalers_draw_iso = []
    for fold, (tr_idx, va_idx) in enumerate(tscv.split(base_probs)):
        print(f"    折 {fold + 1}/5: tr={len(tr_idx)}  va={len(va_idx)}")
        y_tr = y_true[tr_idx]

        # --- Temp & Vector: 用 raw 概率拟合（raw 更贴近原始 logits，未被 Platt 二次变换）
        ts = TemperatureScaler().fit(y_tr, raw_probs[tr_idx])
        method_outputs["TempScaling(on raw)"][va_idx] = ts.transform(raw_probs[va_idx])
        scalers_temp.append(ts.T_)

        vs = VectorScaler().fit(y_tr, raw_probs[tr_idx])
        method_outputs["VectorScaling(on raw)"][va_idx] = vs.transform(raw_probs[va_idx])
        scalers_vec.append(vs.params_.tolist())

        # --- Isotonic-OVR: 对 Platt 概率修正（单调保序后校准）
        iso = IsotonicOVR().fit(y_tr, base_probs[tr_idx])
        iso_va = iso.transform(base_probs[va_idx])
        method_outputs["Isotonic-OVR(on platt)"][va_idx] = iso_va
        scalers_iso.append(True)

        # --- Temp + DrawCalibrator (target_recall=0.30)
        ts2 = TemperatureScaler().fit(y_tr, raw_probs[tr_idx])
        temp_tr = ts2.transform(raw_probs[tr_idx])
        temp_va = ts2.transform(raw_probs[va_idx])
        dc_t = DrawCalibrator(target_recall=0.30).fit(
            y_tr, temp_tr[:, [2, 1, 0]]  # [H, D, A] order
        )
        cal_va_hda = dc_t.calibrate(temp_va[:, [2, 1, 0]], factor=dc_t.best_factor_)
        method_outputs["Temp+DrawCal(0.30)"][va_idx] = cal_va_hda[:, [2, 1, 0]]
        scalers_draw_t.append((ts2.T_, dc_t.best_factor_))

        # --- Iso + DrawCalibrator (target_recall=0.30)
        iso2 = IsotonicOVR().fit(y_tr, base_probs[tr_idx])
        iso_tr = iso2.transform(base_probs[tr_idx])
        iso_va2 = iso2.transform(base_probs[va_idx])
        dc_i = DrawCalibrator(target_recall=0.30).fit(
            y_tr, iso_tr[:, [2, 1, 0]]
        )
        cal_va_hda_i = dc_i.calibrate(iso_va2[:, [2, 1, 0]], factor=dc_i.best_factor_)
        method_outputs["Iso+DrawCal(0.30)"][va_idx] = cal_va_hda_i[:, [2, 1, 0]]
        scalers_draw_iso.append((dc_i.best_factor_,))

    # 汇总 Temperature/Vector 折参数（用于诊断）
    meta_params = {
        "TempScaling_T_per_fold": scalers_temp,
        "TempScaling_T_mean": float(np.mean(scalers_temp)),
        "VectorScaling_params_per_fold": scalers_vec,
        "Temp_DrawCal_per_fold": scalers_draw_t,
        "Iso_DrawCal_per_fold_factor": scalers_draw_iso,
    }

    print("[3/5] 概率质量评估（LogLoss / Brier / ECE / 平局召回）...")
    metrics = {}
    for m in method_names:
        metrics[m] = eval_metrics(y_true, method_outputs[m], name=m)

    print("[4/5] EV 回测（平注 ROI / 凯利 ROI）...")
    ev_results = {}
    for m in method_names:
        print(f"    跑 {m} ...")
        ev_results[m] = _detailed_ev(df, method_outputs[m], odds_map, bridge,
                                     min_ev=0.02, kelly_strategy="quarter", kelly_cap=0.25)

    print("[5/5] 生成报告与导出...")
    # --- 导出校准后概率 CSV ---
    cal_csv_path = ASSETS_DIR / f"calibrated_probs_{RUN_TS}.csv"
    export_rows = []
    for i in range(N):
        row = df.iloc[i]
        rec = {
            "match_id": row["match_id"],
            "date": row.get("date", ""),
            "home_team_name": row.get("home_team_name", ""),
            "away_team_name": row.get("away_team_name", ""),
            "competition_name": row.get("competition_name", ""),
            "actual_result": int(row["actual_result"]),
        }
        for m in method_names:
            safe = m.replace("(", "").replace(")", "").replace("+", "_plus_").replace("/", "_")
            p = method_outputs[m][i]
            rec[f"{safe}_away"] = float(p[0])
            rec[f"{safe}_draw"] = float(p[1])
            rec[f"{safe}_home"] = float(p[2])
        export_rows.append(rec)
    pd.DataFrame(export_rows).to_csv(cal_csv_path, index=False, encoding="utf-8-sig")
    print(f"    校准后概率 CSV: {cal_csv_path}")

    # --- 对比报告 ---
    report_lines = []
    report_lines.append("# 概率校准对比实验（严格时序 OOF）\n")
    report_lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    report_lines.append(f"- 模型口径: `{model}` (使用 OOF Platt 输出作为校准基线)\n")
    report_lines.append(f"- OOF 文件: `{oof_path.name}`\n")
    report_lines.append(f"- 样本量: {N} 场（按日期升序，TimeSeriesSplit(5)）\n")
    report_lines.append(f"- 平局召回目标线: **≥ 0.28**（项目硬性约束）\n")
    report_lines.append(f"- 概率列顺序: [away, draw, home] ≡ [0, 1, 2]\n")
    report_lines.append("\n## 一、校准方法说明\n")
    report_lines.append("| 方法 | 拟合方式 | 参数复杂度 | 说明 |")
    report_lines.append("|---|---|---|---|")
    report_lines.append("| Baseline(Platt) | OOF 逐折 Platt  | 2 params × 3 class × per-model | 当前生产校准（对照） |")
    report_lines.append("| TempScaling(on raw) | 折内搜索 T，最小化 NLL（基于 raw 概率反推 logits） | 1 scalar T | 统一温度缩放，不改变排序（避开 Platt 二次变换） |")
    report_lines.append("| VectorScaling(on raw) | L-BFGS-B 最小化折内 NLL（基于 raw 概率反推 logits） | 3 T + 3 bias (6 维) | 逐类独立温度/偏置，拟合能力更强 |")
    report_lines.append("| Isotonic-OVR(on platt) | 每类保序回归一对其余（输入为 Platt 概率） | 非参数（单调分段） | Zadrozny & Elkan 多分类策略，单调修正 Platt 偏置 |")
    report_lines.append("| Temp+DrawCal(0.30) | 温度缩放(raw) + 平局阈值校准（目标召回 0.30） | T + factor | 温度先压过度自信 + factor 抬升平局召回 |")
    report_lines.append("| Iso+DrawCal(0.30) | 保序回归(Platt) + 平局阈值校准（目标召回 0.30） | 非参数 + factor | 单调修正 + 平局召回双目标 |\n")

    report_lines.append("## 二、概率质量对比（关键指标）\n")
    # 指标对比表
    key_cols = [("accuracy", "准确率"), ("log_loss", "LogLoss"), ("brier", "Brier"),
                ("recall_draw", "平局召回"), ("precision_draw", "平局精度"),
                ("ece_top", "Top-class ECE"), ("mean_per_class_ece", "每类ECE均值")]
    header = "| 方法 | " + " | ".join(cn for _, cn in key_cols) + " |"
    sep = "|---" * (len(key_cols) + 1) + "|"
    report_lines.append(header)
    report_lines.append(sep)
    for m in method_names:
        row = metrics[m]
        line = f"| {m} | "
        parts = []
        for k, cn in key_cols:
            v = row[k]
            if k == "log_loss" or k == "brier":
                parts.append(f"{v:.4f}")
            elif k == "ece_top" or k == "mean_per_class_ece":
                parts.append(f"{v*100:.2f}%")
            else:
                parts.append(f"{v*100:.2f}%")
        report_lines.append(line + " | ".join(parts) + " |")

    # 平局召回达标情况
    report_lines.append("\n### 平局召回达标检查\n")
    report_lines.append("| 方法 | 平局召回 | 平局精度 | 达标 |")
    report_lines.append("|---|---|---|---|")
    for m in method_names:
        r = metrics[m]["recall_draw"]
        p = metrics[m]["precision_draw"]
        flag = "✅" if r >= 0.28 else "❌"
        report_lines.append(f"| {m} | {r*100:.2f}% | {p*100:.2f}% | {flag} |")

    report_lines.append("\n## 三、各类别 ECE 拆解（10-bin，越小越校准）\n")
    report_lines.append("| 方法 | ECE_away | ECE_draw | ECE_home | Top-class ECE |")
    report_lines.append("|---|---|---|---|---|")
    for m in method_names:
        r = metrics[m]
        report_lines.append(f"| {m} | {r['ece_away']*100:.2f}% | {r['ece_draw']*100:.2f}% | "
                            f"{r['ece_home']*100:.2f}% | {r['ece_top']*100:.2f}% |")

    report_lines.append("\n## 四、端到端 EV 回测（odds500_live，min_ev=0.02，quarter-kelly cap=0.25）\n")
    report_lines.append("| 方法 | 有赔率 | 投注数 | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 凯利盈亏 | 平均EV |")
    report_lines.append("|---|---|---|---|---|---|---|---|---|")
    for m in method_names:
        e = ev_results[m]
        report_lines.append(
            f"| {m} | {e.get('with_odds','-')} | {e.get('bets','-')} | "
            f"{e.get('hit_rate',0)*100:.1f}% | {e.get('flat_roi',0)*100:+.2f}% | "
            f"{e.get('flat_profit',0):+.2f} | {e.get('kelly_roi',0)*100:+.2f}% | "
            f"{e.get('kelly_profit',0):+.2f} | {e.get('avg_ev',0)*100:+.2f}% |"
        )

    report_lines.append("\n## 五、校准参数诊断（折内拟合值）\n")
    report_lines.append(f"- **Temperature Scaling T per fold**: {scalers_temp}")
    report_lines.append(f"- **Temperature Scaling mean(T)**: {float(np.mean(scalers_temp)):.3f}")
    report_lines.append(f"- **Temp + DrawCal (T, factor) per fold**: {scalers_draw_t}")
    report_lines.append(f"- **Iso + DrawCal (factor) per fold**: {scalers_draw_iso}")
    report_lines.append("- VectorScaling 每折参数详见 JSON。\n")

    # --- 排名结论 ---
    report_lines.append("## 六、结论与推荐\n")
    # 排序：优先平局召回≥0.28 → 然后平注 ROI（越接近 0 或正越好）→ 然后 LogLoss 下降幅度
    ranked = sorted(
        method_names,
        key=lambda m: (
            0 if metrics[m]["recall_draw"] >= 0.28 else 1,        # 平局召回达标优先
            -ev_results[m].get("flat_roi", -1),                   # 然后 ROI 高在前
            metrics[m]["log_loss"],                               # 然后 LogLoss 低在前
        ),
    )
    baseline_roi = ev_results["Baseline(Platt)"].get("flat_roi", 0)
    baseline_ll = metrics["Baseline(Platt)"]["log_loss"]
    baseline_dr = metrics["Baseline(Platt)"]["recall_draw"]
    report_lines.append("### 综合排名（平局召回达标→ROI→LogLoss）\n")
    report_lines.append("| 排名 | 方法 | 平局召回达标 | 平注ROI | ΔROI vs 基线 | LogLoss | ΔLogLoss vs 基线 |")
    report_lines.append("|---|---|---|---|---|---|---|")
    for i, m in enumerate(ranked):
        e = ev_results[m].get("flat_roi", 0)
        ll = metrics[m]["log_loss"]
        dr = metrics[m]["recall_draw"]
        flag = "✅" if dr >= 0.28 else "❌"
        report_lines.append(
            f"| {i+1} | {m} | {flag} | {e*100:+.2f}% | {(e - baseline_roi)*100:+.2f}pp | "
            f"{ll:.4f} | {(ll - baseline_ll)*100:+.2f}pp |"
        )

    best = ranked[0]
    report_lines.append(f"\n**推荐方案: `{best}`**\n")
    report_lines.append(
        f"- 平局召回: {metrics[best]['recall_draw']*100:.2f}% "
        f"({'达标' if metrics[best]['recall_draw']>=0.28 else '未达标，需再叠加 DrawCalibrator'})\n"
        f"- 平注 ROI: {ev_results[best].get('flat_roi',0)*100:+.2f}% "
        f"(基线: {baseline_roi*100:+.2f}%, Δ={(ev_results[best].get('flat_roi',0)-baseline_roi)*100:+.2f}pp)\n"
        f"- LogLoss: {metrics[best]['log_loss']:.4f} "
        f"(基线: {baseline_ll:.4f}, Δ={(metrics[best]['log_loss']-baseline_ll)*100:+.2f}pp)\n"
    )
    if ev_results[best].get("flat_roi", -1) < 0:
        report_lines.append(
            "⚠️ **注意：最佳方案平注 ROI 仍为负**，说明仅靠概率校准不足以"
            "在现有 EV 决策逻辑下转正。后续可叠加：\n"
            "  1) EV 阈值抬升（min_ev=0.05~0.10）+ 择场过滤（如仅 confidence ≥ P75 的场次）；\n"
            "  2) 对 edge 做分桶单调回归再切层（当前 >10pp 桶仍 -6.53% 需诊断）；\n"
            "  3) 训练端直接以 EV/ROI 为优化目标（替代 WDL 交叉熵）。\n"
        )

    md_path = REPORT_DIR / f"calibration_benchmark_{RUN_TS}.md"
    md_path.write_text("\n".join(report_lines), encoding="utf-8")
    json_path = REPORT_DIR / f"calibration_benchmark_{RUN_TS}.json"
    json_path.write_text(
        json.dumps({
            "meta": {
                "model": model,
                "oof_path": str(oof_path),
                "N": N,
                "ts": RUN_TS,
            },
            "meta_params": meta_params,
            "metrics": metrics,
            "ev_results": ev_results,
            "ranking": ranked,
            "baseline_roi": baseline_roi,
            "baseline_log_loss": baseline_ll,
            "baseline_draw_recall": baseline_dr,
        }, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8",
    )
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")
    print(f"✅ 校准后概率 CSV: {cal_csv_path}")
    print()
    print("===== 关键结论（控制台速览） =====")
    print(f"{'方法':<22}  {'平ROI':>8}  {'凯利ROI':>8}  {'LogLoss':>8}  {'平召':>6}  {'TopECE':>7}")
    for m in method_names:
        e = ev_results[m]
        me = metrics[m]
        print(f"{m:<22}  {e.get('flat_roi',0)*100:+7.2f}%  {e.get('kelly_roi',0)*100:+7.2f}%  "
              f"{me['log_loss']:8.4f}  {me['recall_draw']*100:5.2f}%  {me['ece_top']*100:6.2f}%")
    print(f"\n🏆 推荐方案: {ranked[0]}")
    return md_path, json_path, cal_csv_path, ranked


def main():
    parser = argparse.ArgumentParser(description="概率校准对比实验（严格时序 OOF）")
    parser.add_argument("--oof", type=Path,
                        default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    parser.add_argument("--model", choices=["mean", "xgb", "lgb"], default="mean")
    args = parser.parse_args()
    run_calibration_benchmark(args.oof, args.model)


if __name__ == "__main__":
    main()
