# -*- coding: utf-8 -*-
"""
P0-C: 概念漂移 P(Y|X) 检测（Concept Drift Gate）
================================================
对生产预测（model_predictions.Score_grid_* → WDL 概率）按时间滚动窗口检测
条件可靠性漂移：**最近窗口 vs 前序等宽参考窗口** 的校准对比，超阈值告警。

设计原则（用户确认）:
  - **禁止自动重训**：本脚本为纯被动观测，只读 DB + 只写报告目录；
    不 import / 不调用任何训练模块，不触发任何自动训练流程。
  - **参考基线 = 前序等宽窗口**：按 match_date 排序，最近 N 场 vs 紧邻其前 N 场，
    隔离「近期」变化（控制联赛构成/赛季阶段等混淆）。

检测指标:
  1. ΔECE = recent 总 ECE − ref 总 ECE      ≥ --ece-delta        → 硬告警
  2. 局部桶漂移: recent 桶 |gap| ≥ --max-bin-gap 且 ref 同桶 |gap| ≤ --ref-bin-gap
     且桶 n ≥ --min-bin                                          → 硬告警
  3. LogLoss / Brier 相对劣化比 (recent/ref) ≥ --score-degrade   → 硬告警
  4. PSI (recent vs ref 预测概率分布，3 类均值)  ≥ --psi-alert    → 观察项(软)
                                            ≥ --psi-observe     → 观察项(软)
  5. 基础率 P(y) TV 距离                       ≥ --base-rate-tv  → 仅上下文

数据口径（复用 scripts/reliability_gate.py，P0-B）:
  - 预测概率: Score_grid_{h}_{a} 36 格累加 → WDL 概率（同一模型 t006_score_predictor_v5）
  - 真实赛果: matches.actual_wdl（缺失回退 actual_score 'H:A'）
  - 时间: matches.match_date（'YYYY-MM-DD'）

用法:
  python scripts/concept_drift_gate.py [--model-name t006_score_predictor_v5]
                                       [--db data/odds.db]
                                       [--window-size 120]   # 场次窗口（优先，默认）
                                       [--window-days D]     # 自然日窗口（二选一）
                                       [--min-samples 60]
                                       [--ece-delta 0.05] [--max-bin-gap 0.20]
                                       [--ref-bin-gap 0.10] [--min-bin 10]
                                       [--score-degrade 1.30] [--psi-observe 0.10]
                                       [--psi-alert 0.25] [--base-rate-tv 0.20]
                                       [--out-dir docs/concept_drift] [--no-chart]
退出码:
  0 = 无漂移（或样本不足 insufficient）
  1 = 检测到概念漂移（仅告警，需人工复核；不触发任何重训）
"""

import argparse
import json
import sqlite3
import sys
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from reliability_gate import (  # P0-B 复用（零改动）
    CLASS_NAMES,
    bin_reliability,
    ece_set,
    load_actuals,
    load_predictions,
)

warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_DB = PROJECT_DIR / "data" / "odds.db"
DEFAULT_MODEL = "t006_score_predictor_v5"
DEFAULT_OUT_DIR = PROJECT_DIR / "docs" / "concept_drift"

N_BINS = 10


# ============================================================
# 1. 数据加载（复用 P0-B load_predictions/load_actuals + 补 match_date）
# ============================================================
def load_dated(conn: sqlite3.Connection, model_name: str) -> list:
    """Score_grid → WDL 概率 + 真实赛果 + league + match_date（按日期升序）。"""
    rows = load_predictions(conn, model_name)
    data = load_actuals(conn, rows)
    cur = conn.cursor()
    cur.execute("SELECT match_id, match_date FROM matches")
    dates = {mid: d for mid, d in cur.fetchall()}
    out = []
    for r in data:
        d = dates.get(r["match_id"])
        if not d:
            continue
        out.append({
            "match_id": r["match_id"],
            "y": r["y"],
            "league": r["league"] or "",
            "proba": r["proba"],            # [客胜, 平局, 主胜]
            "match_date": str(d),
        })
    out.sort(key=lambda r: (r["match_date"], r["match_id"]))
    return out


# ============================================================
# 2. 窗口划分：最近窗口 vs 前序等宽参考窗口
# ============================================================
def split_windows(rows: list, n_size=None, n_days=None, min_samples=60):
    """按 match_date 升序切分 recent / ref 等宽窗口。

    返回 (mode_str, recent, ref, insufficient)。
    --window-size:  最近 N 场 vs 紧邻前 N 场
    --window-days:  近 D 自然日 vs 其前 D 自然日
    """
    if n_size:
        recent = rows[-n_size:]
        ref = rows[-2 * n_size:-n_size]
        mode = f"size-{n_size}"
    else:
        last = datetime.strptime(rows[-1]["match_date"], "%Y-%m-%d")
        cutoff = (last - timedelta(days=n_days)).strftime("%Y-%m-%d")
        recent = [r for r in rows if r["match_date"] > cutoff]
        ref = [r for r in rows if r["match_date"] <= cutoff]
        ref_cutoff = (datetime.strptime(cutoff, "%Y-%m-%d") - timedelta(days=n_days)).strftime("%Y-%m-%d")
        ref = [r for r in ref if r["match_date"] > ref_cutoff]
        mode = f"days-{n_days}"
    insufficient = len(recent) < min_samples or len(ref) < min_samples
    return mode, recent, ref, insufficient


# ============================================================
# 3. 窗口指标：ECE / 分桶可靠性 / LogLoss / Brier
# ============================================================
def compute_window(win: list) -> dict:
    """单窗口指标：{n, 日期区间, 联赛top3, ece, ece_by_cls, bins, logloss, brier}。"""
    y = np.array([r["y"] for r in win])
    proba = np.stack([r["proba"] for r in win])
    per_cls, overall, bins_by_cls = ece_set(y, proba, n_bins=N_BINS)

    # LogLoss = mean(-log p[y])
    p_y = np.clip(proba[np.arange(len(y)), y], 1e-9, 1.0)
    logloss = float(-np.log(p_y).mean())

    # Brier = mean(Σ_c (p_c - 1{y=c})²)
    onehot = np.zeros_like(proba)
    onehot[np.arange(len(y)), y] = 1.0
    brier = float(((proba - onehot) ** 2).sum(axis=1).mean())

    dates = [r["match_date"] for r in win]
    lg = Counter(r["league"] for r in win)
    return {
        "n": len(win),
        "date_from": dates[0],
        "date_to": dates[-1],
        "league_top": lg.most_common(3),
        "ece": overall,
        "ece_by_cls": per_cls,
        "bins": bins_by_cls,          # list[3]，每类 10 桶 {lo,hi,n,conf,acc,gap}
        "logloss": logloss,
        "brier": brier,
    }


def _psi_one_dim(actual: np.ndarray, expected: np.ndarray, n_bins=N_BINS) -> float:
    """单变量 PSI：以 expected 分位数为桶界（防空桶），Σ(A-E)·ln(A/E)。"""
    if len(actual) == 0 or len(expected) == 0:
        return 0.0
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, n_bins + 1)))
    if len(edges) < 3:
        return 0.0
    n_b = len(edges) - 1
    a_idx = np.clip(np.digitize(actual, edges[1:-1]), 0, n_b - 1)
    e_idx = np.clip(np.digitize(expected, edges[1:-1]), 0, n_b - 1)
    a_share = np.bincount(a_idx, minlength=n_b) / max(len(actual), 1)
    e_share = np.bincount(e_idx, minlength=n_b) / max(len(expected), 1)
    e_share = np.maximum(e_share, 1e-6)
    return float(np.sum((a_share - e_share) * np.log(a_share / e_share)))


def psi_over_classes(recent_rows: list, ref_rows: list) -> float:
    """recent vs ref 预测概率分布 PSI（3 类均值）。"""
    pa = np.stack([r["proba"] for r in recent_rows])
    pe = np.stack([r["proba"] for r in ref_rows])
    return float(np.mean([_psi_one_dim(pa[:, c], pe[:, c]) for c in range(3)]))


def base_rate_tv(recent_rows: list, ref_rows: list) -> float:
    """P(y) 基础率 TV 距离（仅上下文，不单独告警）。"""
    va = Counter(r["y"] for r in recent_rows)
    vb = Counter(r["y"] for r in ref_rows)
    na, nb = len(recent_rows), len(ref_rows)
    return float(sum(abs(va.get(k, 0) / na - vb.get(k, 0) / nb)
                     for k in set(va) | set(vb)))


# ============================================================
# 4. 对比判定
# ============================================================
def compare(recent_rows, ref_rows, recent_w, ref_w, t: dict):
    """产出 (indicators, violations[硬], observations[软])。"""
    indicators = {
        "ece_recent": recent_w["ece"],
        "ece_ref": ref_w["ece"],
        "delta_ece": recent_w["ece"] - ref_w["ece"],
        "logloss_recent": recent_w["logloss"],
        "logloss_ref": ref_w["logloss"],
        "logloss_ratio": recent_w["logloss"] / max(ref_w["logloss"], 1e-9),
        "brier_recent": recent_w["brier"],
        "brier_ref": ref_w["brier"],
        "brier_ratio": recent_w["brier"] / max(ref_w["brier"], 1e-9),
        "psi": psi_over_classes(recent_rows, ref_rows),
        "base_rate_tv": base_rate_tv(recent_rows, ref_rows),
    }
    violations, observations = [], []

    # 1) ΔECE
    if indicators["delta_ece"] >= t["ece_delta"]:
        violations.append(
            f"ΔECE {indicators['delta_ece']*100:+.2f}pp（近窗 {recent_w['ece']*100:.2f}% vs "
            f"参考 {ref_w['ece']*100:.2f}%）≥ 阈值 {t['ece_delta']*100:.2f}pp → P(Y|X) 条件校准漂移")

    # 2) 局部桶漂移：recent 桶 gap 大 且 ref 同桶正常
    for c in range(3):
        for br, bf in zip(recent_w["bins"][c], ref_w["bins"][c]):
            if (br["n"] >= t["min_bin"]
                    and abs(br["gap"]) >= t["max_bin_gap"]
                    and abs(bf["gap"]) <= t["ref_bin_gap"]):
                violations.append(
                    f"[{CLASS_NAMES[c]}] 桶[{br['lo']:.1f}-{br['hi']:.1f}] 近窗 "
                    f"预测 {br['conf']*100:.1f}% vs 命中 {br['acc']*100:.1f}% "
                    f"偏差 {br['gap']*100:+.1f}pp ≥ {t['max_bin_gap']*100:.0f}pp（n={br['n']}），"
                    f"参考窗同桶偏差 {bf['gap']*100:+.1f}pp（正常 ≤ {t['ref_bin_gap']*100:.0f}pp）→ 局部漂移")

    # 3) LogLoss / Brier 相对劣化
    if indicators["logloss_ratio"] >= t["score_degrade"]:
        violations.append(
            f"LogLoss 劣化比 {indicators['logloss_ratio']:.2f}（近窗 {recent_w['logloss']:.4f} vs "
            f"参考 {ref_w['logloss']:.4f}）≥ 阈值 {t['score_degrade']:.2f}")
    if indicators["brier_ratio"] >= t["score_degrade"]:
        violations.append(
            f"Brier 劣化比 {indicators['brier_ratio']:.2f}（近窗 {recent_w['brier']:.4f} vs "
            f"参考 {ref_w['brier']:.4f}）≥ 阈值 {t['score_degrade']:.2f}")

    # 4) PSI（软观察）
    if indicators["psi"] >= t["psi_alert"]:
        observations.append(
            f"PSI {indicators['psi']:.3f} ≥ {t['psi_alert']:.2f}：预测概率输入分布显著漂移（佐证强化）")
    elif indicators["psi"] >= t["psi_observe"]:
        observations.append(
            f"PSI {indicators['psi']:.3f} ≥ {t['psi_observe']:.2f}：预测概率输入分布轻度漂移（观察）")

    # 5) 基础率 TV（仅上下文）
    if indicators["base_rate_tv"] >= t["base_rate_tv"]:
        observations.append(
            f"基础率 P(y) TV {indicators['base_rate_tv']:.3f} ≥ {t['base_rate_tv']:.2f}（仅上下文，非 P(Y|X) 证据）")

    return indicators, violations, observations


# ============================================================
# 5. 滚动趋势（近 1 年按 ISO 周聚合周 ECE）
# ============================================================
def trend_series(rows: list, months=12) -> list:
    last = datetime.strptime(rows[-1]["match_date"], "%Y-%m-%d")
    cutoff = last - timedelta(days=months * 30)
    weekly = defaultdict(list)
    for r in rows:
        d = datetime.strptime(r["match_date"], "%Y-%m-%d")
        if d < cutoff:
            continue
        iso = d.isocalendar()
        weekly[(iso[0], iso[1])].append(r)
    out = []
    for k in sorted(weekly):
        w = compute_window(weekly[k])
        out.append({"week": f"{k[0]}-W{k[1]:02d}", "n": w["n"],
                    "ece": w["ece"], "ece_draw": w["ece_by_cls"][1],
                    "logloss": w["logloss"]})
    return out


# ============================================================
# 6. 图表：周 ECE 时序 + 近/参窗可靠性图叠加
# ============================================================
def render_charts(trend: list, recent_w: dict, ref_w: dict, out_dir: Path, t: dict):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # 面板 1: 周 ECE 时序
    fig, ax = plt.subplots(figsize=(11, 4.5))
    xs = list(range(len(trend)))
    ax.plot(xs, [p["ece"] * 100 for p in trend], "o-", color="#2c3e50",
            label="周 ECE（近 1 年，ISO 周）")
    ax.axhline(recent_w["ece"] * 100, color="#c0392b", ls="-", lw=1.5,
               label=f"近窗 ECE {recent_w['ece']*100:.2f}%")
    ax.axhline(ref_w["ece"] * 100, color="#2980b9", ls="--", lw=1.5,
               label=f"参考窗 ECE {ref_w['ece']*100:.2f}%")
    ax.axhline((ref_w["ece"] + t["ece_delta"]) * 100, color="#e67e22", ls=":",
               label=f"告警线（参考+{t['ece_delta']*100:.0f}pp）")
    if trend:
        ax.set_xticks(xs[::max(1, len(xs) // 12)])
        ax.set_xticklabels([p["week"] for p in trend][::max(1, len(xs) // 12)],
                           rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("ECE (%)")
    ax.set_title("P0-C 概念漂移 — 滚动周 ECE 趋势（t006_score_predictor_v5）")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(out_dir / "ece_trend.png", dpi=120, bbox_inches="tight")
    plt.close(fig)

    # 面板 2: 近/参窗可靠性图叠加（3 类）
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharey=True)
    colors = ["#c0392b", "#2980b9", "#27ae60"]
    for c, ax in enumerate(axes):
        for bins, style, lbl in [(ref_w["bins"][c], "--", "参考窗"),
                                 (recent_w["bins"][c], "-", "近窗")]:
            conf = [b["conf"] for b in bins if b["n"] > 0]
            acc = [b["acc"] for b in bins if b["n"] > 0]
            ax.plot(conf, acc, style, color=colors[c], label=lbl)
        ax.plot([0, 1], [0, 1], "k--", lw=0.8, alpha=0.6)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("预测概率")
        ax.set_ylabel("实际命中率")
        ax.set_title(f"{CLASS_NAMES[c]}")
        ax.legend(fontsize=8)
    fig.suptitle("近窗 vs 参考窗 Reliability（P0-C 概念漂移）", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "reliability_overlay.png", dpi=120, bbox_inches="tight")
    plt.close(fig)


# ============================================================
# 7. 输出：md / json
# ============================================================
def _fmt_ece(s):
    return (f"{s['ece']*100:.2f}% (客{s['ece_by_cls'][0]*100:.2f}/"
            f"平{s['ece_by_cls'][1]*100:.2f}/主{s['ece_by_cls'][2]*100:.2f})")


def write_md(report: dict, out_dir: Path, model_name: str):
    ind = report["indicators"]
    violations = report["violations"]
    observations = report["observations"]
    verdict = report["verdict"]
    lines = [
        "# 概念漂移检测报告（P0-C Concept Drift Gate）",
        "",
        f"**生成时间**: {report['generated_at']}",
        f"**模型**: `{model_name}`",
        f"**窗口模式**: `{report['window_mode']}`（近窗 n={report['recent']['n']}，"
        f"参考窗 n={report['ref']['n']}）",
        f"**近窗日期区间**: {report['recent']['date_from']} ~ {report['recent']['date_to']}",
        f"**参考窗日期区间**: {report['ref']['date_from']} ~ {report['ref']['date_to']}",
        "",
        "## 一、两窗口对比总览",
        "",
        "| 窗口 | n | 日期区间 | 总 ECE | LogLoss | Brier | 联赛 Top3 |",
        "|---|---|---|---|---|---|---|",
        f"| 近窗 | {report['recent']['n']} | {report['recent']['date_from']}~{report['recent']['date_to']} | "
        f"{_fmt_ece(report['recent'])} | {report['recent']['logloss']:.4f} | "
        f"{report['recent']['brier']:.4f} | {report['recent']['league_top']} |",
        f"| 参考窗 | {report['ref']['n']} | {report['ref']['date_from']}~{report['ref']['date_to']} | "
        f"{_fmt_ece(report['ref'])} | {report['ref']['logloss']:.4f} | "
        f"{report['ref']['brier']:.4f} | {report['ref']['league_top']} |",
        "",
        "## 二、核心指标",
        "",
        "| 指标 | 近窗 | 参考窗 | 判定 |",
        "|---|---|---|---|",
        f"| ΔECE（总 ECE 差值） | {ind['ece_recent']*100:.2f}% | {ind['ece_ref']*100:.2f}% | "
        f"{ind['delta_ece']*100:+.2f}pp（阈值 {report['thresholds']['ece_delta']*100:.2f}pp）|",
        f"| LogLoss 劣化比 | {ind['logloss_recent']:.4f} | {ind['logloss_ref']:.4f} | "
        f"{ind['logloss_ratio']:.2f}x（阈值 {report['thresholds']['score_degrade']:.2f}x）|",
        f"| Brier 劣化比 | {ind['brier_recent']:.4f} | {ind['brier_ref']:.4f} | "
        f"{ind['brier_ratio']:.2f}x（阈值 {report['thresholds']['score_degrade']:.2f}x）|",
        f"| PSI（预测概率分布） | — | — | {ind['psi']:.3f}（观察 "
        f"{report['thresholds']['psi_observe']:.2f} / 强化 {report['thresholds']['psi_alert']:.2f}）|",
        f"| 基础率 P(y) TV | — | — | {ind['base_rate_tv']:.3f}（阈值 "
        f"{report['thresholds']['base_rate_tv']:.2f}，仅上下文）|",
        "",
        f"## 三、检测结果：{verdict['status']}",
        "",
    ]
    if violations:
        lines += ["**违规项（检测到概念漂移，需人工复核）**:", ""]
        lines += [f"1. {v}" for v in violations]
    else:
        lines.append("**无违规项：近窗条件可靠性相对参考窗未显著漂移。**")
    if observations:
        lines += ["", "**观察项（软信号，不告警）**:", ""]
        lines += [f"- {o}" for o in observations]
    lines += [
        "",
        "## 四、分桶明细（近窗每类每桶 conf/acc/gap，红标 = 触发局部漂移）",
        "",
        "| 类别 | 桶 | n | 近窗预测均值 | 近窗命中率 | 近窗偏差 | 参考窗偏差 |",
        "|---|---|---|---|---|---|---|",
    ]
    flagged = set()
    for c in range(3):
        for br, bf in zip(report["recent"]["bins"][c], report["ref"]["bins"][c]):
            mark = ""
            if (br["n"] >= report["thresholds"]["min_bin"]
                    and abs(br["gap"]) >= report["thresholds"]["max_bin_gap"]
                    and abs(bf["gap"]) <= report["thresholds"]["ref_bin_gap"]):
                mark = " 🔴"
            lines.append(
                f"| {CLASS_NAMES[c]} | {br['lo']:.1f}-{br['hi']:.1f} | {br['n']} | "
                f"{br['conf']*100:.2f}% | {br['acc']*100:.2f}% | {br['gap']*100:+.2f}pp | "
                f"{bf['gap']*100:+.2f}pp{mark} |")
    lines += ["", "## 五、滚动趋势（近 1 年周 ECE）", ""]
    if (out_dir / "ece_trend.png").exists():
        lines += ["![周 ECE 趋势](ece_trend.png)", ""]
    if (out_dir / "reliability_overlay.png").exists():
        lines += ["![近/参窗可靠性叠加](reliability_overlay.png)", ""]
    if report["trend"]:
        lines += ["| 周 | n | ECE | ECE_draw | LogLoss |",
                  "|---|---|---|---|---|"]
        for p in report["trend"]:
            lines.append(
                f"| {p['week']} | {p['n']} | {p['ece']*100:.2f}% | "
                f"{p['ece_draw']*100:.2f}% | {p['logloss']:.4f} |")
    lines += ["", "## 六、结论", ""]
    if verdict["status"] == "PASS":
        lines.append("**结论**: 近窗与参考窗条件可靠性一致，未检测到 P(Y|X) 概念漂移。")
    else:
        lines.append("**结论**: 检测到 P(Y|X) 概念漂移，**需人工复核**（检查伤病/战术/市场环境变化、"
                     "数据源质量等）；本报告**不触发任何自动重训**（P0-C 红线：仅告警）。")
    (out_dir / "concept_drift.md").write_text("\n".join(lines), encoding="utf-8")


# ============================================================
# 8. 主流程
# ============================================================
def run_concept_drift(db_path, model_name=DEFAULT_MODEL, window_size=120, window_days=None,
                      min_samples=60, ece_delta=0.05, max_bin_gap=0.20, ref_bin_gap=0.10,
                      min_bin=10, score_degrade=1.30, psi_observe=0.10, psi_alert=0.25,
                      base_rate_tv=0.20, out_dir=DEFAULT_OUT_DIR, render=True):
    conn = sqlite3.connect(str(db_path))
    rows = load_dated(conn, model_name)
    conn.close()

    if len(rows) < min_samples:
        raise SystemExit(
            f"[SKIP] 模型 {model_name} 有效样本 {len(rows)} < min_samples {min_samples}，无法检测")

    mode, recent, ref, insufficient = split_windows(
        rows, n_size=window_size, n_days=window_days, min_samples=min_samples)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out_dir = out_dir / datetime.now().strftime("%Y%m%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    if insufficient:
        report = {
            "generated_at": ts, "model_name": model_name, "window_mode": mode,
            "status": "insufficient_sample",
            "n_recent": len(recent), "n_ref": len(ref), "min_samples": min_samples,
            "message": f"近窗 {len(recent)} / 参考窗 {len(ref)} 场，样本不足，不告警（exit 0）",
        }
        (out_dir / "concept_drift.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=float),
            encoding="utf-8")
        print("[SKIP] 样本不足，不告警")
        return report, "PASS"

    recent_w = compute_window(recent)
    ref_w = compute_window(ref)
    t = {"ece_delta": ece_delta, "max_bin_gap": max_bin_gap, "ref_bin_gap": ref_bin_gap,
         "min_bin": min_bin, "score_degrade": score_degrade,
         "psi_observe": psi_observe, "psi_alert": psi_alert, "base_rate_tv": base_rate_tv}
    indicators, violations, observations = compare(recent, ref, recent_w, ref_w, t)
    verdict = {"status": "FAIL" if violations else "PASS"}

    # 合并 ECE（一致性核对用，与 P0-B 全库 ECE 同口径）
    merged = compute_window(recent + ref)

    report = {
        "generated_at": ts,
        "model_name": model_name,
        "window_mode": mode,
        "n_recent": len(recent),
        "n_ref": len(ref),
        "recent": recent_w,
        "ref": ref_w,
        "merged_ece": merged["ece"],
        "indicators": indicators,
        "violations": violations,
        "observations": observations,
        "thresholds": t,
        "trend": trend_series(rows),
        "verdict": verdict,
    }
    if render:
        try:
            render_charts(report["trend"], recent_w, ref_w, out_dir, t)
        except Exception as e:
            print(f"[WARN] 图表渲染失败（跳过）: {e}")
    write_md(report, out_dir, model_name)
    (out_dir / "concept_drift.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8")
    return report, verdict


def main():
    ap = argparse.ArgumentParser(description="P0-C 概念漂移 P(Y|X) 检测（仅告警，禁止自动重训）")
    ap.add_argument("--model-name", default=DEFAULT_MODEL)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--window-size", type=int, default=None,
                    help="场次窗口（默认 120 ≈ 4 周；与 --window-days 二选一）")
    ap.add_argument("--window-days", type=int, default=None,
                    help="自然日窗口（与 --window-size 二选一）")
    ap.add_argument("--min-samples", type=int, default=60)
    ap.add_argument("--ece-delta", type=float, default=0.05)
    ap.add_argument("--max-bin-gap", type=float, default=0.20)
    ap.add_argument("--ref-bin-gap", type=float, default=0.10)
    ap.add_argument("--min-bin", type=int, default=10)
    ap.add_argument("--score-degrade", type=float, default=1.30)
    ap.add_argument("--psi-observe", type=float, default=0.10)
    ap.add_argument("--psi-alert", type=float, default=0.25)
    ap.add_argument("--base-rate-tv", type=float, default=0.20)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args()

    if args.window_size is not None and args.window_days is not None:
        ap.error("--window-size 与 --window-days 只能二选一")
    if args.window_days is not None:
        window_size = None  # 自然日窗口模式
    else:
        window_size = args.window_size if args.window_size is not None else 120

    report, verdict = run_concept_drift(
        args.db, args.model_name,
        window_size=window_size, window_days=args.window_days,
        min_samples=args.min_samples,
        ece_delta=args.ece_delta, max_bin_gap=args.max_bin_gap,
        ref_bin_gap=args.ref_bin_gap, min_bin=args.min_bin,
        score_degrade=args.score_degrade, psi_observe=args.psi_observe,
        psi_alert=args.psi_alert, base_rate_tv=args.base_rate_tv,
        out_dir=args.out_dir, render=not args.no_chart)

    if report.get("status") == "insufficient_sample":
        print(report["message"])
        return 0

    ind = report["indicators"]
    rw, fw = report["recent"], report["ref"]
    print("=" * 62)
    print("[P0-C] 概念漂移 P(Y|X) 检测")
    print(f"  模型: {report['model_name']}  窗口: {report['window_mode']}")
    print(f"  近窗: n={rw['n']}  {rw['date_from']}~{rw['date_to']}  ECE {rw['ece']*100:.2f}%  "
          f"LogLoss {rw['logloss']:.4f}  Brier {rw['brier']:.4f}")
    print(f"  参考: n={fw['n']}  {fw['date_from']}~{fw['date_to']}  ECE {fw['ece']*100:.2f}%  "
          f"LogLoss {fw['logloss']:.4f}  Brier {fw['brier']:.4f}")
    print(f"  ΔECE {ind['delta_ece']*100:+.2f}pp | LogLoss 比 {ind['logloss_ratio']:.2f}x | "
          f"Brier 比 {ind['brier_ratio']:.2f}x | PSI {ind['psi']:.3f} | 基础率TV {ind['base_rate_tv']:.3f}")
    print(f"  合并 ECE（近+参窗，一致性参考）: {report['merged_ece']*100:.2f}%")
    print("-" * 62)
    print(f"[{'FAIL' if verdict['status']=='FAIL' else 'PASS'}] 概念漂移检测: {verdict['status']}"
          + ("" if verdict["status"] == "PASS" else " — 检测到漂移，需人工复核（仅告警，不重训）"))
    for v in report["violations"]:
        print(f"    ! {v}")
    for o in report["observations"]:
        print(f"    - {o}")
    print(f"  报告: {args.out_dir / datetime.now().strftime('%Y%m%d') / 'concept_drift.md'}")
    print(f"  数据: {args.out_dir / datetime.now().strftime('%Y%m%d') / 'concept_drift.json'}")
    print("=" * 62)
    return 1 if verdict["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
