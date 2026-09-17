# -*- coding: utf-8 -*-
"""C-20260905-003: 决策层分段诊断 — 赔率上限 × 方向分段 ROI 剖面。

背景：
  edge_shrink_test（s∈{1.0..0.0}）证伪「均匀收缩 edge」——ROI 卡在 -3.6% 无法转正；
  但 s=1.0 分桶发现：3~6pp 桶 ROI +0.28%（正）、6~10pp -5.96%（最大亏损桶 n=3566）、
  0~3pp -18.2%（n=499）；且 6~10pp/>10pp 桶「平均命中率 > 平均赔率盈亏平衡点」却仍负 ROI
  → 指向**桶内高赔率长尾**（同一桶内高赔率子集命中率远低于低赔率子集，Jensen 效应）拖累。
  本脚本验证：模型价值投注按「方向 × 方向赔率档」分段是否有正 ROI 段，以及**硬性赔率上限**
  （只投 odds ≤ cap 的价值方向）能否把整体 ROI 拉正——即「模型在低赔率（热门）方向是否有
  真实可开发 edge」。

设计（严格防泄漏 + 与 edge_shrink_test 同构）：
  1. 输入 = OOF mean raw，TSS(5) 折内 TemperatureScaling → 折外 temp_probs。
  2. ev_engine 逐场回测（min_ev=0.02 / quarter-kelly cap=0.25），逐笔记录
     direction / odds / edge / won / profit。
  3. 分段剖面：direction × odds 档（≤2.0 / 2~3 / 3~4.5 / 4.5~6 / >6）→ n/命中/平注ROI/盈亏平衡。
  4. 赔率上限扫描：只投 odds ≤ cap 的价值方向（cap∈{2.5,3.0,3.5,4.5,6.0,None}）→ 整体 ROI 转正？
  5. 上限 × 方向联合：低赔率段是否分方向仍有正 ROI（防单一方向凑正）。

用法：
  python edge_segment_test.py
  python edge_segment_test.py --oof ../assets/oof_predictions_20260903_012452.csv

产物：
  reports/edge_segment_test_<TS>.md/.json
"""
import argparse
import json
import logging
import os
import sys
from collections import defaultdict
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
    run_ev_for_row,
)

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"
DB_PATH = PROJECT_DIR / "data" / "odds.db"

RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}
ODDS_BANDS = [(0.0, 2.0, "<=2.0"), (2.0, 3.0, "2~3"), (3.0, 4.5, "3~4.5"),
              (4.5, 6.0, "4.5~6"), (6.0, 1e9, ">6")]


def odds_band(odds):
    for lo, hi, label in ODDS_BANDS:
        if lo <= odds < hi:
            return label
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="决策层分段诊断 — 赔率上限 × 方向")
    parser.add_argument("--oof", type=Path, default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    parser.add_argument("--cap", type=float, action="append",
                        default=[2.5, 3.0, 3.5, 4.5, 6.0],
                        help="赔率上限（可多次指定，另附无上限对照）")
    args = parser.parse_args()
    caps = sorted(set(args.cap)) + [None]

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    logging.disable(logging.WARNING)

    print("[1/4] 加载 OOF 数据与赔率...")
    df = load_oof(args.oof)
    df = df.sort_values("date").reset_index(drop=True)
    y_true = df["actual_result"].values.astype(int)
    raw_probs = prob_matrix(df, src="mean", kind="raw")
    odds_map, bridge = build_odds_bridge(DB_PATH)
    N = len(df)
    print(f"   OOF 样本: {N}  赔率映射: {len(odds_map)}  桥接对: {len(bridge)}")

    print("[2/4] 时序 5 折 TemperatureScaling...")
    temp_probs = np.zeros_like(raw_probs)
    tscv = TimeSeriesSplit(n_splits=5)
    for tr_idx, va_idx in tscv.split(raw_probs):
        ts = TemperatureScaler().fit(y_true[tr_idx], raw_probs[tr_idx])
        temp_probs[va_idx] = ts.transform(raw_probs[va_idx])
    covered = temp_probs.sum(axis=1) > 0
    sub = df[covered].reset_index(drop=True)
    temp_sub = temp_probs[covered]
    print(f"   覆盖行(折外可评估): {int(covered.sum())} / {N}")

    print("[3/4] 逐场 EV 回测 + 记录分段...")
    bets = []  # (direction, odds, edge, won, profit)
    n_bridge = 0
    for i in range(len(sub)):
        row = sub.iloc[i]
        key = resolve_odds_key(row, odds_map, bridge)
        if key is None:
            continue
        n_bridge += 1
        _, bet_dir = run_ev_for_row(row, odds_map[key], temp_sub[i])
        if bet_dir is None:
            continue
        won = (RESULT_TO_DIRECTION[int(row["actual_result"])] == bet_dir.direction)
        bets.append((bet_dir.direction, bet_dir.odds, bet_dir.edge,
                     int(won), (bet_dir.odds - 1.0) if won else -1.0))
    B = pd.DataFrame(bets, columns=["direction", "odds", "edge", "won", "profit"])
    print(f"   有赔率对齐: {n_bridge}  价值投注笔数: {len(B)}")

    def agg(g):
        if len(g) == 0:
            return None
        n = len(g)
        return {"n": n, "hit": g["won"].mean(), "roi": g["profit"].mean(),
                "profit": g["profit"].sum(), "be": (1.0 / g["odds"]).mean()}

    # 分段剖面：direction × odds 档
    print("[4/4] 生成报告...")
    lines = []
    lines.append("# 决策层分段诊断 — 赔率上限 × 方向 ROI 剖面\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- OOF 文件: `{args.oof.name}`（mean raw，TSS 折内 TemperatureScaling）")
    lines.append(f"- 回测: min_ev=0.02 / quarter-kelly cap=0.25；价值投注总笔数 {len(B)}")
    lines.append(f"- 评估子集: 折外可评估 {int(covered.sum())} 场（有赔率 {n_bridge}）\n")

    lines.append("## 一、方向 × 方向赔率档 ROI 剖面\n")
    lines.append("| 方向 | 赔率档 | n | 命中率 | 平注ROI | 盈亏 | 平均盈亏平衡 | 平均赔率 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for d in ["home", "draw", "away"]:
        g = B[B["direction"] == d]
        lines.append(f"| **{d}** | 合计 | {len(g)} | {g['won'].mean()*100:.1f}% | "
                     f"{g['profit'].mean()*100:+.2f}% | {g['profit'].sum():+.2f} | — | {g['odds'].mean():.2f} |")
        for lo, hi, label in ODDS_BANDS:
            seg = g[(g["odds"] >= lo) & (g["odds"] < hi)]
            if len(seg) < 50:
                continue
            a = agg(seg)
            lines.append(f"| {d} | {label} | {a['n']} | {a['hit']*100:.1f}% | {a['roi']*100:+.2f}% | "
                         f"{a['profit']:+.2f} | {a['be']*100:.1f}% | {seg['odds'].mean():.2f} |")

    lines.append("\n## 二、赔率上限扫描（只投 odds ≤ cap 的价值方向）\n")
    lines.append("| cap | 投注n | 命中率 | 平注ROI | 盈亏 | 平均赔率 | 平均EV |")
    lines.append("|---|---|---|---|---|---|---|")
    for cap in caps:
        g = B if cap is None else B[B["odds"] <= cap]
        if len(g) == 0:
            lines.append(f"| {'None' if cap is None else cap} | 0 | — | — | — | — | — |")
            continue
        a = agg(g)
        ev = (g["won"] * (g["odds"] - 1.0) - (1 - g["won"])).mean()
        lines.append(f"| {'None' if cap is None else cap} | {a['n']} | {a['hit']*100:.1f}% | "
                     f"{a['roi']*100:+.2f}% | {a['profit']:+.2f} | {g['odds'].mean():.2f} | "
                     f"{ev*100:+.2f}% |")

    lines.append("\n## 三、上限 × 方向联合（防单一方向凑正）\n")
    lines.append("| cap | 方向 | 投注n | 命中率 | 平注ROI | 盈亏 |")
    lines.append("|---|---|---|---|---|---|")
    for cap in caps:
        g = B if cap is None else B[B["odds"] <= cap]
        for d in ["home", "draw", "away"]:
            seg = g[g["direction"] == d]
            if len(seg) < 50:
                continue
            a = agg(seg)
            lines.append(f"| {'None' if cap is None else cap} | {d} | {a['n']} | "
                         f"{a['hit']*100:.1f}% | {a['roi']*100:+.2f}% | {a['profit']:+.2f} |")

    # 结论：找 n≥100 且 ROI>0 的段/组合
    lines.append("\n## 四、结论\n")
    pos_seg = []
    for d in ["home", "draw", "away"]:
        g = B[B["direction"] == d]
        for lo, hi, label in ODDS_BANDS:
            seg = g[(g["odds"] >= lo) & (g["odds"] < hi)]
            if len(seg) >= 100 and seg["profit"].mean() > 0:
                pos_seg.append(f"方向={d} 赔率档={label}  n={len(seg)}  ROI={seg['profit'].mean()*100:+.2f}%")
    pos_cap = [(f"{c}" if c else "None", agg(B if c is None else B[B["odds"] <= c]))
               for c in caps if (B if c is None else B[B["odds"] <= c]).shape[0] >= 100]
    pos_cap = [(c, a) for c, a in pos_cap if a["roi"] > 0]
    lines.append(f"- 方向×赔率档正 ROI 段（n≥100）: {len(pos_seg)} 个")
    for s in pos_seg:
        lines.append(f"  - {s}")
    lines.append(f"- 赔率上限整体转正组合（n≥100）: {len(pos_cap)} 个")
    for c, a in pos_cap:
        lines.append(f"  - cap={c}: ROI {a['roi']*100:+.2f}%  n={a['n']}  命中 {a['hit']*100:.1f}%")

    md_path = REPORT_DIR / f"edge_segment_test_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"edge_segment_test_{RUN_TS}.json"
    json_path.write_text(json.dumps({
        "meta": {"oof": str(args.oof), "N": int(N), "covered": int(covered.sum()),
                 "n_bridge": int(n_bridge), "n_bets": int(len(B)), "ts": RUN_TS},
        "summary": {"pos_segments": pos_seg, "pos_caps": pos_cap},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")


if __name__ == "__main__":
    main()
