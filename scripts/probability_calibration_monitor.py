# -*- coding: utf-8 -*-
"""概率校准分档监控（P2-03）。

背景：
  单场 RPS / LogLoss 只能给出整体损失，无法暴露「某一概率档位」的系统性偏差。
  本脚本对模型三向概率做固定 5% 分桶，统计每一档「预测平均概率 vs 真实样本频率」，
  输出可靠性曲线（reliability curve）与校准偏差，并专门检测最高发缺陷——平局档位
  是否被系统性低估（其余类高估/低估同样给出诊断）。

输入：
  - assets/oof_predictions_*.csv（严格时序 OOF，含 XGB/LGB raw+platt 三向概率 + actual_result）

用法：
  python probability_calibration_monitor.py
  python probability_calibration_monitor.py --oof assets/oof_predictions_20260903_012452.csv --model mean --kind platt

输出：
  - reports/prob_calibration_monitor_<TS>.md   校准曲线 + 平局低估诊断报告
  - reports/prob_calibration_monitor_<TS>.json 原始分桶数据
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"

RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}
CLASS_NAMES = ["away", "draw", "home"]  # 与 actual_result 编码一致：0=away/1=draw/2=home
CLASS_CN = {"away": "客胜", "draw": "平局", "home": "主胜"}
N_BINS = 20  # 每 5% 一档
BIAS_ALERT = 0.02  # |真实频率 - 预测概率| 超过 2pp 视为该档位存在系统偏差


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def load_oof(csv_path: Path, model: str = "mean"):
    """读取 OOF CSV，构造三向概率矩阵 (N,3) [away, draw, home]。"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    df = df[df["actual_result"].notna()].copy()
    df["actual_result"] = df["actual_result"].astype(int)

    srcs = ["mean"] if model == "mean" else [model]
    if model == "mean":
        for kind in ("raw", "platt"):
            for d in ("away", "draw", "home"):
                df[f"mean_{kind}_{d}"] = (
                    df[f"xgb_{kind}_{d}"].values + df[f"lgb_{kind}_{d}"].values
                ) / 2.0
    for src in ("mean", "xgb", "lgb"):
        if src not in srcs:
            continue
        for kind in ("platt", "raw"):
            cols = [f"{src}_{kind}_{d}" for d in ("away", "draw", "home")]
            s = df[cols].sum(axis=1).values
            for c in cols:
                df[c] = df[c].values / np.where(s > 0, s, 1.0)
    return df


def prob_matrix(df: pd.DataFrame, src: str, kind: str) -> np.ndarray:
    cols = [f"{src}_{kind}_{d}" for d in ("away", "draw", "home")]
    return df[cols].values.astype(float)


# ---------------------------------------------------------------------------
# 校准分桶
# ---------------------------------------------------------------------------
def reliability_curve(y_true: np.ndarray, preds: np.ndarray, class_idx: int, n_bins: int = N_BINS):
    """单类可靠性曲线。class_idx: away=0/draw=1/home=2。

    返回 list[dict]：每档 {bin_low, bin_high, n, avg_pred, actual_freq, bias}
    """
    conf = preds[:, class_idx]
    correct = (y_true == class_idx).astype(float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        m = (conf >= lo) & (conf < hi) if b < n_bins - 1 else (conf >= lo) & (conf <= hi)
        n = int(m.sum())
        if n == 0:
            out.append({"bin_low": lo, "bin_high": hi, "n": 0,
                        "avg_pred": None, "actual_freq": None, "bias": None})
            continue
        avg = float(conf[m].mean())
        freq = float(correct[m].mean())
        out.append({
            "bin_low": round(lo, 3), "bin_high": round(hi, 3), "n": n,
            "avg_pred": round(avg, 4), "actual_freq": round(freq, 4),
            "bias": round(freq - avg, 4),
        })
    return out


def overall_ece(y_true: np.ndarray, preds: np.ndarray, n_bins: int = N_BINS):
    """多分类 top-class ECE。"""
    pred_cls = np.argmax(preds, axis=1)
    conf = preds.max(axis=1)
    correct = (y_true == pred_cls).astype(float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    N = len(y_true)
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        m = (conf >= lo) & (conf < hi) if b < n_bins - 1 else (conf >= lo) & (conf <= hi)
        if m.sum() == 0:
            continue
        ece += (m.sum() / N) * abs(correct[m].mean() - conf[m].mean())
    return float(ece)


def detect_class_bias(curve, direction="under"):
    """汇总某类分桶的系统性偏差（实际频率整体高于/低于预测概率）。"""
    valid = [b for b in curve if b["n"] > 0]
    if not valid:
        return {"n_bins": 0, "n_samples": 0, "weighted_bias": 0.0, "conclusion": "无样本"}
    n_samples = sum(b["n"] for b in valid)
    weighted_bias = sum(b["n"] * b["bias"] for b in valid) / n_samples
    if direction == "under":
        hit = [b for b in valid if b["bias"] >= BIAS_ALERT]
    else:
        hit = [b for b in valid if b["bias"] <= -BIAS_ALERT]
    conclusion = "显著" if hit and abs(weighted_bias) >= 0.01 else "无/轻微"
    return {
        "n_bins": len(valid), "n_samples": n_samples,
        "weighted_bias": round(weighted_bias, 4),
        "biased_bins": len(hit),
        "conclusion": conclusion,
    }


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def _curve_table(curve):
    lines = ["| 档位 | 场次 | 预测均值 | 真实频率 | 偏差 |", "|---|---|---|---|---|"]
    for b in curve:
        if b["n"] == 0:
            continue
        lines.append(
            f"| {b['bin_low']*100:.0f}~{b['bin_high']*100:.0f}% | {b['n']} | "
            f"{b['avg_pred']*100:.1f}% | {b['actual_freq']*100:.1f}% | {b['bias']*100:+.1f}pp |"
        )
    return "\n".join(lines)


def generate_report(cfg, df, y_true, preds, curves, diag, ece):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    md_path = REPORT_DIR / f"prob_calibration_monitor_{ts}.md"
    json_path = REPORT_DIR / f"prob_calibration_monitor_{ts}.json"

    L = []
    L.append("# 概率校准分档监控报告（P2-03）\n")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append(f"- 模型口径: `{cfg.model}` / `{cfg.kind}`")
    L.append(f"- OOF 文件: `{cfg.oof}`")
    L.append(f"- 样本量: {len(df)} 场（actual_result 过滤后）")
    L.append(f"- 分桶: 每 5% 一档（{N_BINS} 档）；偏差阈值 ±{int(BIAS_ALERT*100)}pp\n")

    L.append("## 一、整体校准度\n")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| Top-class ECE（{N_BINS}-bin） | {ece*100:.2f}% |\n")

    L.append("## 二、三类校准曲线\n")
    for ci, cname in enumerate(CLASS_NAMES):
        L.append(f"### {CLASS_CN[cname]}（class_idx={ci}）\n")
        L.append(_curve_table(curves[ci]))
        d = diag[cname]
        L.append(f"\n- 有效样本: {d['n_samples']} 场（{d['n_bins']} 档）")
        L.append(f"- 加权平均偏差: {d['weighted_bias']*100:+.2f}pp（>0=低估，<0=高估）")
        L.append(f"- 超过 ±{int(BIAS_ALERT*100)}pp 的档位数: {d['biased_bins']}")
        L.append(f"- 低估诊断: **{d['conclusion']}**\n")

    L.append("## 三、平局档位系统性低估结论\n")
    dd = diag["draw"]
    if dd["weighted_bias"] >= 0.005:
        L.append(
            f"✅ **确认存在平局系统性低估**：平局档位加权平均偏差为 "
            f"{dd['weighted_bias']*100:+.2f}pp（模型输出概率低于真实发生频率），"
            f"有 {dd['biased_bins']} 个档位偏差超过 +{int(BIAS_ALERT*100)}pp。"
            f"建议在 DrawCalibrator 基础上评估按档位分段提升平局概率的修正。"
        )
    elif dd["weighted_bias"] <= -0.005:
        L.append(
            f"⚠️ 平局档位整体**高估**（偏差 {dd['weighted_bias']*100:+.2f}pp），"
            f"与常见低估假设相反，需复核样本口径与模型版本。"
        )
    else:
        L.append(
            f"平局档位整体偏差 {dd['weighted_bias']*100:+.2f}pp，无显著系统性低估，"
            f"当前平局校准基本合理。"
        )
    L.append("\n---\n*本报告由 probability_calibration_monitor.py 自动生成*\n")

    md_path.write_text("\n".join(L), encoding="utf-8")

    payload = {
        "meta": {"model": cfg.model, "kind": cfg.kind, "oof": str(cfg.oof),
                 "n": int(len(df)), "ts": ts, "top_ece": ece},
        "curves": {cname: curves[i] for i, cname in enumerate(CLASS_NAMES)},
        "diagnosis": diag,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="概率校准分档监控（P2-03）")
    parser.add_argument("--oof", type=Path,
                        default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    parser.add_argument("--model", choices=["mean", "xgb", "lgb"], default="mean")
    parser.add_argument("--kind", choices=["platt", "raw"], default="platt")
    args = parser.parse_args()

    df = load_oof(args.oof, model=args.model)
    y_true = df["actual_result"].values.astype(int)
    preds = prob_matrix(df, src=args.model, kind=args.kind)
    if len(df) == 0:
        print("无有效样本")
        return

    curves = [reliability_curve(y_true, preds, ci) for ci in range(3)]
    diag = {
        cname: detect_class_bias(curves[ci], direction="under")
        for ci, cname in enumerate(CLASS_NAMES)
    }
    ece = overall_ece(y_true, preds)

    md_path, json_path = generate_report(args, df, y_true, preds, curves, diag, ece)
    print(f"✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")
    for cname in CLASS_NAMES:
        d = diag[cname]
        print(f"  {CLASS_CN[cname]}: 加权偏差 {d['weighted_bias']*100:+.2f}pp | "
              f"超阈值档位 {d['biased_bins']} | {d['conclusion']}")


if __name__ == "__main__":
    main()