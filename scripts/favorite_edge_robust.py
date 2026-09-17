# -*- coding: utf-8 -*-
"""C-20260905-003: 热门价值段稳健性验证 — cap∈{2.0,2.25,2.5,2.75} × 分折/分赛季/分方向。

背景：
  edge_segment_test 发现 cap=2.5（只投方向赔率≤2.5 的价值方向）整体平注ROI +1.44%（n=751），
  主要来自客胜热门（away≤2.5 n=706 +1.69%），是全程首个转正组合。本脚本验证该信号是否
  跨时间折/跨赛季稳健（防单折单季凑正），并确认邻近阈值（2.0/2.25/2.75）不敏感。

设计：
  1. 与 edge_shrink/segment 同构：OOF mean raw + TSS(5) 折内 TemperatureScaling。
  2. 逐场 EV 回测（min_ev=0.02 / quarter-kelly cap=0.25），仅保留方向赔率≤cap 的价值投注。
  3. 稳健性三维：
     - 分折（TSS 5 折，折1训练段无输出）：各折 ROI 是否多数为正；
     - 分赛季（8 月分界）：正收益是否跨季均匀；
     - 分方向（home/draw/away）：确认正收益归属客/主胜热门而非平局。
  4. 空值护栏：单折/单季 n<30 记「样本不足」，避免小样本误判。

用法：python favorite_edge_robust.py [--oof ...]
产物：reports/favorite_edge_robust_<TS>.md/.json
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


def season_of(d):
    y = d.year
    return f"{y-1}/{y}" if d.month < 8 else f"{y}/{y+1}"


def main():
    parser = argparse.ArgumentParser(description="热门价值段稳健性验证")
    parser.add_argument("--oof", type=Path, default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    parser.add_argument("--cap", type=float, action="append",
                        default=[2.0, 2.25, 2.5, 2.75])
    args = parser.parse_args()
    caps = sorted(set(args.cap))

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

    print("[2/4] 时序 5 折 TemperatureScaling + 折标签...")
    temp_probs = np.zeros_like(raw_probs)
    fold_label = np.full(N, -1, dtype=int)
    tscv = TimeSeriesSplit(n_splits=5)
    fold_info = []
    for f, (tr_idx, va_idx) in enumerate(tscv.split(raw_probs)):
        ts = TemperatureScaler().fit(y_true[tr_idx], raw_probs[tr_idx])
        temp_probs[va_idx] = ts.transform(raw_probs[va_idx])
        fold_label[va_idx] = f + 1
        fold_info.append({"fold": f + 1, "tr": int(len(tr_idx)), "va": int(len(va_idx))})
    covered = temp_probs.sum(axis=1) > 0
    sub = df[covered].reset_index(drop=True)
    temp_sub = temp_probs[covered]
    fold_sub = fold_label[covered]
    print(f"   覆盖行: {int(covered.sum())} / {N}")

    print("[3/4] 逐场 EV 回测（记录全部价值投注）...")
    recs = []
    for i in range(len(sub)):
        row = sub.iloc[i]
        key = resolve_odds_key(row, odds_map, bridge)
        if key is None:
            continue
        _, bet_dir = run_ev_for_row(row, odds_map[key], temp_sub[i])
        if bet_dir is None:
            continue
        won = (RESULT_TO_DIRECTION[int(row["actual_result"])] == bet_dir.direction)
        recs.append({
            "fold": int(fold_sub[i]),
            "season": season_of(pd.Timestamp(row["date"])),
            "direction": bet_dir.direction,
            "odds": bet_dir.odds,
            "won": int(won),
            "profit": (bet_dir.odds - 1.0) if won else -1.0,
        })
    B = pd.DataFrame(recs)
    print(f"   价值投注总笔数: {len(B)}")

    def agg(g):
        n = len(g)
        if n == 0:
            return None
        return {"n": n, "hit": g["won"].mean(), "roi": g["profit"].mean(),
                "profit": g["profit"].sum(), "odds": g["odds"].mean()}

    print("[4/4] 生成报告...")
    lines = []
    lines.append("# 热门价值段稳健性验证（cap 赔率上限 × 分折/分赛季/分方向）\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- OOF 文件: `{args.oof.name}`（mean raw，TSS 折内 TemperatureScaling）")
    lines.append(f"- 回测: min_ev=0.02 / quarter-kelly cap=0.25；仅保留方向赔率 ≤ cap 的价值投注")
    lines.append(f"- 评估子集: 折外可评估 {int(covered.sum())} 场；价值投注 {len(B)} 笔\n")
    lines.append(f"- 折信息: {json.dumps(fold_info, ensure_ascii=False)}\n")

    for cap in caps:
        g = B[B["odds"] <= cap]
        a = agg(g)
        if a is None:
            lines.append(f"\n## cap={cap} — 无投注\n")
            continue
        lines.append(f"\n## cap={cap} — 整体 平注ROI {a['roi']*100:+.2f}%  n={a['n']}  "
                     f"命中 {a['hit']*100:.1f}% 均赔 {a['odds']:.2f}\n")

        lines.append("### 分折（TSS）\n")
        lines.append("| 折 | n | 命中率 | 平注ROI | 盈亏 |")
        lines.append("|---|---|---|---|---|")
        n_pos = n_ok = 0
        for f in range(1, 6):
            seg = g[g["fold"] == f]
            if len(seg) < 30:
                lines.append(f"| 折{f} | {len(seg)} | — | 样本不足 | — |")
                continue
            s = agg(seg)
            n_ok += 1
            n_pos += int(s["roi"] > 0)
            lines.append(f"| 折{f} | {s['n']} | {s['hit']*100:.1f}% | {s['roi']*100:+.2f}% | {s['profit']:+.2f} |")
        lines.append(f"\n- 正收益折数: {n_pos}/{n_ok}（n≥30 折）\n")

        lines.append("### 分赛季\n")
        lines.append("| 赛季 | n | 命中率 | 平注ROI | 盈亏 |")
        lines.append("|---|---|---|---|---|")
        n_pos_s = n_ok_s = 0
        for sname, seg in g.groupby("season"):
            if len(seg) < 30:
                lines.append(f"| {sname} | {len(seg)} | — | 样本不足 | — |")
                continue
            s = agg(seg)
            n_ok_s += 1
            n_pos_s += int(s["roi"] > 0)
            lines.append(f"| {sname} | {s['n']} | {s['hit']*100:.1f}% | {s['roi']*100:+.2f}% | {s['profit']:+.2f} |")
        lines.append(f"\n- 正收益赛季数: {n_pos_s}/{n_ok_s}（n≥30 赛季）\n")

        lines.append("### 分方向\n")
        lines.append("| 方向 | n | 命中率 | 平注ROI | 盈亏 |")
        lines.append("|---|---|---|---|---|")
        for d in ["home", "draw", "away"]:
            seg = g[g["direction"] == d]
            if len(seg) < 30:
                lines.append(f"| {d} | {len(seg)} | — | 样本不足 | — |")
                continue
            s = agg(seg)
            lines.append(f"| {d} | {s['n']} | {s['hit']*100:.1f}% | {s['roi']*100:+.2f}% | {s['profit']:+.2f} |")

    lines.append("\n## 结论\n")
    best = None
    for cap in caps:
        g = B[B["odds"] <= cap]
        a = agg(g)
        if a and a["n"] >= 100 and (best is None or a["roi"] > best[1]["roi"]):
            best = (cap, a)
    if best:
        cap, a = best
        lines.append(f"- 最优上限: cap={cap}  ROI {a['roi']*100:+.2f}%  n={a['n']}")
        g = B[B["odds"] <= cap]
        folds = [agg(g[g["fold"] == f]) for f in range(1, 6)]
        folds = [s for s in folds if s and s["n"] >= 30]
        pos_f = sum(1 for s in folds if s["roi"] > 0)
        lines.append(f"- 分折正收益: {pos_f}/{len(folds)} 折（n≥30）；"
                     f"{'稳健 ✅' if len(folds) >= 4 and pos_f >= 4 else '不稳健 ❌/边缘 ⚠️'}")
        # 显著性：命中率 vs 盈亏平衡 的单样本 z 检验（约等于标准误倍数）
        se = np.sqrt(a["hit"] * (1 - a["hit"]) / a["n"]) if a["n"] else 0.0
        z = (a["hit"] - 1.0 / a["odds"]) / se if se > 0 else 0.0
        lines.append(f"- 注：n={a['n']}、ROI {a['roi']*100:+.2f}%，命中 {a['hit']*100:.1f}% 与盈亏平衡 "
                     f"{1.0/a['odds']*100:.1f}% 差距 ≈ {(a['hit']-1.0/a['odds'])*100:.1f}pp，"
                     f"z≈{z:+.2f}（|z|<2 视为统计不显著）")
    else:
        lines.append("- 无可评估（n≥100）组合。")

    md_path = REPORT_DIR / f"favorite_edge_robust_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"favorite_edge_robust_{RUN_TS}.json"
    json_path.write_text(json.dumps({
        "meta": {"oof": str(args.oof), "N": int(N), "covered": int(covered.sum()),
                 "n_bets": int(len(B)), "ts": RUN_TS, "caps": caps, "fold_info": fold_info},
        "summary": {"best_cap": best[0] if best else None,
                    "best_roi": best[1]["roi"] if best else None,
                    "best_n": best[1]["n"] if best else None},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")


if __name__ == "__main__":
    main()
