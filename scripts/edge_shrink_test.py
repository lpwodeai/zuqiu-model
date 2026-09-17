# -*- coding: utf-8 -*-
"""C-20260905-003: 决策层 edge 收缩（Edge Shrink）测试。

背景：
  C-20260905-001 证伪训练端三条路径（EV 奖励 / MOD 蒸馏 / Penalty 惩罚）后，
  C-20260905-002 审计排除数据端根因，市场水平错位测试（本日）定位：
  - 选择膨胀（selection inflation）：模型 max 方向概率较市场去抽水高 +3~7pp（边际校准 ≠ 选择后校准）；
  - 分歧过度自信（disagreement overconfidence）：>10pp 桶模型 48.4% vs 实现 35.6%（高估 12.8pp），
    高分歧时市场更接近真相。
  推论：训练端改的是「高赔率过度自信」，但真问题是「选择效应 + 分歧效应」→ 在**决策层**直接按比例
  收缩 edge：p_used = p_market + s·(p_model − p_market)，s∈{1.0,0.75,0.5,0.25,0.0}，
  验证对冲分歧过度自信后 edge 分桶单调性恢复、ROI 能否转正。

设计（严格防泄漏 + 与 edge_monotonic_fix 同构）：
  1. 输入 = OOF mean raw 概率，TimeSeriesSplit(5) 折内 fit TemperatureScaler → 折外 temp_probs
     （复用 edge_monotonic_fix 最优校准路径 TempScaling(on raw)）。
  2. 市场锚点 p_market = ev_engine.remove_vig(avg_live odds)（即投注市场去抽水概率）。
  3. 对每个 s：p_used = p_market + s·(p_model − p_market)，clip [1e-7, 1−1e-7] 后归一化。
     s=1.0 退化为 Temp 基线（对照），s=0.0 退化为纯市场（edge=0 → 全 AVOID）。
  4. 用 p_used 跑 ev_engine（min_ev=0.02 / quarter-kelly cap=0.25），EV/edge/kelly 全部基于 p_used。
  5. 评估：整体平注/凯利 ROI + 按 edge 分桶（验证单调性）+ 转正组合扫描。

用法：
  python edge_shrink_test.py
  python edge_shrink_test.py --oof ../assets/oof_predictions_20260903_012452.csv \
      --shrink 1.0 --shrink 0.75 --shrink 0.5 --shrink 0.25 --shrink 0.0

产物：
  reports/edge_shrink_test_<TS>.md/.json
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from edge_monotonic_fix import (  # noqa: E402
    load_oof,
    prob_matrix,
    TemperatureScaler,
    build_odds_bridge,
    resolve_odds_key,
    ev_backtest_with_buckets,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"
DB_PATH = PROJECT_DIR / "data" / "odds.db"

EPS = 1e-7


def shrink_to_market(temp_probs, df, odds_map, bridge, s):
    """决策层 edge 收缩：p_used = p_market + s·(p_model − p_market)。

    无赔率的场次保留原概率（不参与回测），有赔率的场次按市场锚点收缩后 clip+归一化。
    p_market = remove_vig(avg_live odds)，与 ev_engine 内部去抽水口径一致。
    """
    out = np.zeros_like(temp_probs)
    for i in range(len(df)):
        key = resolve_odds_key(df.iloc[i], odds_map, bridge)
        if key is None:
            continue
        o = odds_map[key]  # (home, draw, away)
        inv = np.array([1.0 / o[2], 1.0 / o[1], 1.0 / o[0]])  # [away, draw, home]
        pm = inv / inv.sum()
        p_used = pm + s * (temp_probs[i] - pm)
        p_used = np.clip(p_used, EPS, 1.0 - EPS)
        p_used = p_used / p_used.sum()
        out[i] = p_used
    return out


def main():
    parser = argparse.ArgumentParser(description="决策层 edge 收缩（Edge Shrink）测试")
    parser.add_argument("--oof", type=Path, default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    parser.add_argument("--shrink", type=float, action="append",
                        default=[1.0, 0.75, 0.5, 0.25, 0.0],
                        help="收缩系数 s（可多次指定）")
    args = parser.parse_args()
    shrinks = sorted(set(args.shrink), reverse=True)

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    logging.disable(logging.WARNING)

    print("[1/4] 加载 OOF 数据与赔率...")
    df = load_oof(args.oof)
    df = df.sort_values("date").reset_index(drop=True)
    y_true = df["actual_result"].values.astype(int)
    raw_probs = prob_matrix(df, src="mean", kind="raw")   # (N,3) [away,draw,home]
    odds_map, bridge = build_odds_bridge(DB_PATH)
    N = len(df)
    print(f"   OOF 样本: {N}  赔率映射: {len(odds_map)}  桥接对: {len(bridge)}")

    print("[2/4] 时序 5 折 TemperatureScaling（折内 fit → 折外 transform）...")
    temp_probs = np.zeros_like(raw_probs)
    tscv = TimeSeriesSplit(n_splits=5)
    fold_info = []
    for fold, (tr_idx, va_idx) in enumerate(tscv.split(raw_probs)):
        y_tr = y_true[tr_idx]
        ts = TemperatureScaler().fit(y_tr, raw_probs[tr_idx])
        temp_probs[va_idx] = ts.transform(raw_probs[va_idx])
        fold_info.append({"fold": fold + 1, "tr": int(len(tr_idx)), "va": int(len(va_idx))})
    covered = temp_probs.sum(axis=1) > 0
    sub = df[covered].reset_index(drop=True)
    temp_sub = temp_probs[covered]
    print(f"   覆盖行(折外可评估): {int(covered.sum())} / {N}")

    print(f"[3/4] edge 收缩回测（s = {shrinks}）...")
    results = {}
    for s in shrinks:
        p_used = shrink_to_market(temp_sub, sub, odds_map, bridge, float(s))
        res = ev_backtest_with_buckets(sub, p_used, odds_map, bridge, min_ev=0.02)
        results[s] = res
        print(f"   s={s:<4} 平ROI {res['flat_roi']*100:+6.2f}%  n={res['bets']}  "
              f"命中 {res['hit_rate']*100:5.1f}%  avgEV {res['avg_ev']*100:+6.2f}%  "
              f"avg_p {res['avg_model_p']*100:5.1f}%")

    print("[4/4] 生成报告...")
    lines = []
    lines.append("# 决策层 edge 收缩（Edge Shrink）测试\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- OOF 文件: `{args.oof.name}`（mean raw，TSS 折内 TemperatureScaling）")
    lines.append(f"- 市场锚点: odds500_avg_live 去抽水（与 ev_engine.remove_vig 同口径，96.4% 对齐）")
    lines.append(f"- 收缩公式: p_used = p_market + s·(p_model − p_market)，s = {shrinks}")
    lines.append(f"- 无泄漏方式: TimeSeriesSplit(5) 折内拟合温度缩放 → 折外应用")
    lines.append(f"- 评估子集: 折外可评估 {int(covered.sum())} 场")
    lines.append(f"- 回测: min_ev=0.02 / quarter-kelly cap=0.25；s=1.0 即 Temp 基线对照\n")
    lines.append(f"- 折信息: {json.dumps(fold_info, ensure_ascii=False)}\n")

    lines.append("## 一、整体 EV 回测（按收缩系数 s）\n")
    lines.append("| s | 投注n | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV | 平均赔率 | 平均模型概率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for s in shrinks:
        e = results[s]
        lines.append(f"| {s:.2f} | {e['bets']} | {e['hit_rate']*100:.1f}% | {e['flat_roi']*100:+.2f}% | "
                     f"{e['flat_profit']:+.2f} | {e['kelly_roi']*100:+.2f}% | {e['avg_ev']*100:+.2f}% | "
                     f"{e['avg_odds']:.2f} | {e['avg_model_p']*100:.1f}% |")

    lines.append("\n## 二、按 edge 分桶（验证单调性恢复）\n")
    lines.append("| s | 桶 | n | 命中率 | 平注ROI | 模型概率 | 实现频率 | 高估幅度(pp) | 平均EV | 平均赔率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in shrinks:
        for bk in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]:
            v = results[s]["by_edge"].get(bk)
            if not v:
                continue
            over = (v["avg_model_p"] - v["hit_rate"]) * 100
            lines.append(f"| {s:.2f} | {bk} | {v['n']} | {v['hit_rate']*100:.1f}% | "
                         f"{v['flat_roi']*100:+.2f}% | {v['avg_model_p']*100:.1f}% | "
                         f"{v['hit_rate']*100:.1f}% | {over:+.1f} | {v['avg_ev']*100:+.2f}% | "
                         f"{v['avg_odds']:.2f} |")

    lines.append("\n## 三、单调性结论\n")
    for s in shrinks:
        seq = [(bk, results[s]["by_edge"].get(bk)) for bk in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]]
        seq = [(bk, v) for bk, v in seq if v and v["n"] >= 50]
        if len(seq) < 2:
            lines.append(f"- s={s:.2f}: 分桶样本不足，无法判定单调性")
            continue
        rois = [v["flat_roi"] for _, v in seq]
        mono = all(rois[i] <= rois[i + 1] + 1e-9 for i in range(len(rois) - 1))
        trend = "单调递增 ✅" if mono else "非单调 ❌"
        detail = " → ".join(f"{bk} {v['flat_roi']*100:+.1f}%" for bk, v in seq)
        lines.append(f"- s={s:.2f}: {trend}  ({detail})")

    base = results[1.0]
    cands = [(s, results[s]) for s in shrinks if results[s]["bets"] >= 100]
    best_s, best = (max(cands, key=lambda t: t[1]["flat_roi"]) if cands else (None, None))
    lines.append("\n## 四、结论\n")
    if best is None:
        lines.append("- 无可评估（投注 n<100）的组合。")
    else:
        lines.append(f"- 最优收缩: **s={best_s:.2f}**  平注ROI {best['flat_roi']*100:+.2f}% "
                     f"(s=1.0 Temp基线 {base['flat_roi']*100:+.2f}%, "
                     f"Δ={((best['flat_roi']-base['flat_roi'])*100):+.2f}pp)  n={best['bets']}")
    pos = [(s, results[s]) for s in shrinks if results[s]["flat_roi"] > 0 and results[s]["bets"] >= 100]
    lines.append(f"- ROI 转正组合（n≥100）: {len(pos)} 个")
    for s, e in pos:
        lines.append(f"  - s={s:.2f}: ROI {e['flat_roi']*100:+.2f}%  n={e['bets']}  "
                     f"命中 {e['hit_rate']*100:.1f}%")

    md_path = REPORT_DIR / f"edge_shrink_test_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"edge_shrink_test_{RUN_TS}.json"
    json_path.write_text(json.dumps({
        "meta": {"oof": str(args.oof), "N": int(N), "covered": int(covered.sum()),
                 "ts": RUN_TS, "shrinks": shrinks, "fold_info": fold_info},
        "ev_results": {str(s): results[s] for s in shrinks},
    }, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")


if __name__ == "__main__":
    main()
