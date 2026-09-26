# -*- coding: utf-8 -*-
"""漂移惩罚系数校准脚本。

三步：
  Q1: 方向检验 — payout ~ drift_signed 回归，β 的 CI 含 0 则惩罚无方向性依据
  Q2: k 网格 — 滚动赛季 OOS，找使 OOS ROI 最大的 k
  Q3: flip 率 — 当前阈值(EV≥0.20, urs<0.15)下漂移惩罚改变决策的比例
"""
import argparse
import json
import math
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

BASE = Path(__file__).resolve().parent.parent  # 项目根

HIST_COLUMN_MAP = {
    "match_id": "match_id", "season": "season", "urs": "urs",
    "best_ev": "best_ev", "best_direction": "best_direction",
    "drift_signed": "drift_signed", "odds_close": "odds_close",
    "outcome": "outcome", "payout": "payout",
}


def load_hist(csv_path):
    df = pd.read_csv(csv_path)
    df = df.rename(columns={v: k for k, v in HIST_COLUMN_MAP.items() if v in df.columns})
    df = df.dropna(subset=["drift_signed", "best_ev", "urs", "payout"])
    return df


def q1_direction_test(df):
    """回归 payout ~ drift_signed，看 β 的符号和 CI。"""
    x = df["drift_signed"].values
    y = df["payout"].values
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)
    ci_lo = slope - 1.96 * std_err
    ci_hi = slope + 1.96 * std_err
    significant = ci_lo > 0 or ci_hi < 0
    direction = "正(赔率上升→更差收益, 正k成立)" if slope < 0 else "反(赔率上升→更好收益, 负k?)"
    # 注：slope < 0 意味着 drift 上升时 payout 下降 → 正 k 惩罚成立
    return {
        "beta": round(slope, 4), "std_err": round(std_err, 4),
        "ci_lo": round(ci_lo, 4), "ci_hi": round(ci_hi, 4),
        "p_value": round(p_value, 4), "r_squared": round(r_value**2, 6),
        "ci_contains_zero": ci_lo <= 0 <= ci_hi,
        "significant": significant,
        "direction": direction if significant else "不显著(CI含0, k应设0)",
        "n": len(df),
    }


def q2_k_grid(df, k_values=None):
    """滚动赛季 OOS：每个赛季做 OOS，其余做 IS，找最优 k。"""
    if k_values is None:
        k_values = [0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
    seasons = sorted(df["season"].unique())
    results = []
    for k in k_values:
        df["adj_ev"] = df["best_ev"] * (1 - k * df["drift_signed"])
        oos_rois = []
        for s in seasons:
            train = df[df["season"] != s]
            test = df[df["season"] == s]
            if len(test) < 10:
                continue
            # IS: 找最优阈值组合（urs_upper, ev_lower）
            # 简化：固定 urs<0.15, ev>=0.20，只看 k 的影响
            filt = test[(test["urs"] < 0.15) & (test["adj_ev"] >= 0.20)]
            if len(filt) < 5:
                continue
            roi = filt["payout"].mean()
            oos_rois.append(roi)
        avg_oos = np.mean(oos_rois) if oos_rois else None
        n_oos = sum(len(test[(test["urs"] < 0.15) & (test["adj_ev"] >= 0.20)]) for test in [df[df["season"] == s] for s in seasons if len(df[df["season"] == s]) >= 10])
        # 也算全样本（无 OOS）
        filt_all = df[(df["urs"] < 0.15) & (df["adj_ev"] >= 0.20)]
        roi_all = filt_all["payout"].mean() if len(filt_all) > 0 else None
        results.append({
            "k": k, "oos_roi_avg": round(avg_oos, 6) if avg_oos is not None else None,
            "n_oos_seasons": len(oos_rois),
            "full_roi": round(roi_all, 6) if roi_all is not None else None,
            "n_full": len(filt_all),
        })
    return results


def q3_flip_rate(df):
    """当前阈值下，漂移惩罚改变决策的比例。"""
    # 原始通过：urs<0.15 AND best_ev>=0.20
    df["pass_raw"] = (df["urs"] < 0.15) & (df["best_ev"] >= 0.20)
    # 漂移后通过（k=1）：urs<0.15 AND adj_ev>=0.20
    df["adj_ev_k1"] = df["best_ev"] * (1 - 1.0 * df["drift_signed"])
    df["pass_drift"] = (df["urs"] < 0.15) & (df["adj_ev_k1"] >= 0.20)
    # 只看原始通过的（漂移可能把它打下去）
    raw_pass = df[df["pass_raw"]]
    if len(raw_pass) == 0:
        return {"flip_rate": None, "n_raw_pass": 0, "n_flipped": 0}
    flipped = raw_pass[~raw_pass["pass_drift"]]
    return {
        "flip_rate": round(len(flipped) / len(raw_pass), 4),
        "n_raw_pass": len(raw_pass),
        "n_flipped": len(flipped),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="assets/drift_calib_signals.csv")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    csv_path = BASE / args.csv if not Path(args.csv).is_absolute() else Path(args.csv)
    df = load_hist(csv_path)
    print(f"加载: {len(df)} 行 (有 drift + ev + urs + payout)")
    print(f"赛季: {sorted(df['season'].unique())}")

    print("\n" + "=" * 60)
    print("Q1: 方向检验 (payout ~ drift_signed)")
    print("=" * 60)
    q1 = q1_direction_test(df)
    print(f"  β = {q1['beta']}  SE = {q1['std_err']}")
    print(f"  95% CI = [{q1['ci_lo']}, {q1['ci_hi']}]")
    print(f"  p = {q1['p_value']}  R² = {q1['r_squared']}")
    print(f"  CI 含 0: {q1['ci_contains_zero']}")
    print(f"  方向: {q1['direction']}")
    print(f"  n = {q1['n']}")

    print("\n" + "=" * 60)
    print("Q2: k 网格 (滚动赛季 OOS)")
    print("=" * 60)
    q2 = q2_k_grid(df)
    print(f"{'k':>6s} {'OOS_ROI':>10s} {'n_seasons':>10s} {'full_ROI':>10s} {'n_full':>6s}")
    for r in q2:
        print(f"  {r['k']:>6.1f} {str(r['oos_roi_avg']):>10s} {r['n_oos_seasons']:>10d} {str(r['full_roi']):>10s} {r['n_full']:>6d}")
    best_k = max(q2, key=lambda x: x["oos_roi_avg"] if x["oos_roi_avg"] is not None else -999)
    print(f"\n  最优 k = {best_k['k']} (OOS ROI = {best_k['oos_roi_avg']})")

    print("\n" + "=" * 60)
    print("Q3: flip 率 (k=1, 当前阈值)")
    print("=" * 60)
    q3 = q3_flip_rate(df)
    print(f"  原始通过: {q3['n_raw_pass']} 笔")
    print(f"  漂移翻转: {q3['n_flipped']} 笔 ({q3['flip_rate']*100:.1f}%)" if q3['flip_rate'] is not None else "  无数据")

    print("\n" + "=" * 60)
    print("决策建议")
    print("=" * 60)
    if q1["ci_contains_zero"]:
        print("  → β 的 CI 含 0，市场开→收盘移动对投注结果无显著预测力")
        print("  → k 应设为 0，漂移惩罚是无效复杂度")
        print("  → 保持 shadow mode 运行，不接入漂移惩罚")
    else:
        print(f"  → β = {q1['beta']}, 方向显著: {q1['direction']}")
        if best_k["oos_roi_avg"] is not None and best_k["k"] > 0:
            baseline = next(r for r in q2 if r["k"] == 0)
            delta = best_k["oos_roi_avg"] - (baseline["oos_roi_avg"] or 0)
            print(f"  → 最优 k = {best_k['k']}, OOS ROI 增量 = {delta:+.4f}")
            if q3.get("flip_rate", 0) < 0.03:
                print(f"  → 但 flip 率仅 {q3['flip_rate']*100:.1f}%（<3%），当前阈值下基本不改变决策")
                print("  → 保持 k=0 观察，等阈值调整或样本增加后再评估")
            else:
                print(f"  → flip 率 {q3['flip_rate']*100:.1f}%（≥3%），惩罚有实际影响")
                if delta > 0.02 and best_k["n_oos_seasons"] >= 5:
                    print("  → OOS 增量 ≥2pp 且 ≥5 赛季，可考虑设 k=" + str(best_k["k"]) + " 转正")
                else:
                    print("  → OOS 增量不足或赛季数不够，继续 shadow 累积")

    if args.out:
        report = {"q1": q1, "q2": q2, "q3": q3}
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已保存: {args.out}")


if __name__ == "__main__":
    main()
