# -*- coding: utf-8 -*-
"""C-20260905-003: 竞彩市场真实回收率稳健性验证 — 模型相对竞彩末条 +5.3pp 优势是否真实。

背景：market_switch_backtest 发现模型在四市场中**竞彩末条**回收率最高（抽水 13.6% + ROI -8.32%
= 真实回收 +5.32pp，vs 500 系市场 1.2~1.9pp）——「模型以竞彩特征训练 → 更识别竞彩错价」假设
获支持。本脚本验证该 +5.3pp 是否统计显著（命中率 vs 盈亏平衡 z 检验）且跨折/跨赛季/分方向稳健。

设计：与 market_switch_backtest 同构（OOF mean raw + TSS(5) 折内 TempScaling + 竞彩末条赔率），
仅对竞彩市场输出：整体 z 检验 + 分折 + 分赛季 + 分方向（ROI 与真实回收率双口径）。

用法：python market_recovery_robust.py [--oof ...]
产物：reports/market_recovery_robust_<TS>.md/.json
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
from edge_monotonic_fix import load_oof, prob_matrix, TemperatureScaler  # noqa: E402
from ev_engine import analyze_match, ModelProbabilities, OddsData  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402

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
    parser = argparse.ArgumentParser(description="竞彩市场真实回收率稳健性验证")
    parser.add_argument("--oof", type=Path, default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    args = parser.parse_args()

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    logging.disable(logging.WARNING)

    print("[1/4] 加载 OOF + TSS TempScaling...")
    df = load_oof(args.oof)
    df = df.sort_values("date").reset_index(drop=True)
    y_true = df["actual_result"].values.astype(int)
    raw_probs = prob_matrix(df, src="mean", kind="raw")
    temp_probs = np.zeros_like(raw_probs)
    fold_label = np.full(len(df), -1, dtype=int)
    tscv = TimeSeriesSplit(n_splits=5)
    for f, (tr_idx, va_idx) in enumerate(tscv.split(raw_probs)):
        ts = TemperatureScaler().fit(y_true[tr_idx], raw_probs[tr_idx])
        temp_probs[va_idx] = ts.transform(raw_probs[va_idx])
        fold_label[va_idx] = f + 1
    covered = temp_probs.sum(axis=1) > 0
    sub = df[covered].reset_index(drop=True)
    temp_sub = temp_probs[covered]
    fold_sub = fold_label[covered]

    print("[2/4] 加载竞彩末条赔率...")
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")
    wdl = pd.read_sql_query("SELECT match_id, timestamp, win_a, win_b, draw FROM wdl_history", conn)
    conn.close()
    for c in ["win_a", "win_b", "draw"]:
        wdl[c] = pd.to_numeric(wdl[c], errors="coerce")
    wdl = wdl[(wdl["win_a"] > 1) & (wdl["win_b"] > 1) & (wdl["draw"] > 1)].copy()
    wdl = wdl.sort_values(["match_id", "timestamp"])
    wdl_last = wdl.groupby("match_id").last().reset_index()
    parts = wdl_last["match_id"].astype(str).str.split("_", n=2, expand=True)
    wdl_last["key"] = parts[0] + "|" + parts[1].map(normalize_team_name) + "|" + \
        parts[2].map(normalize_team_name)
    wdl_last = wdl_last.drop_duplicates("key", keep="last")
    sp_map = wdl_last.set_index("key")[["win_a", "draw", "win_b"]]

    print("[3/4] 逐场 EV 回测（竞彩市场）...")
    recs = []
    for i in range(len(sub)):
        row = sub.iloc[i]
        key = (str(row["date"])[:10] + "|" + normalize_team_name(row["home_team_name"]) + "|" +
               normalize_team_name(row["away_team_name"]))
        if key not in sp_map.index:
            continue
        r = sp_map.loc[key]
        o = (float(r["win_a"]), float(r["draw"]), float(r["win_b"]))
        p = temp_sub[i]
        try:
            analysis = analyze_match(
                probs=ModelProbabilities(home=float(p[2]), draw=float(p[1]), away=float(p[0])),
                odds=OddsData(home=o[0], draw=o[1], away=o[2]),
                ev_threshold=0.02, kelly_strategy="quarter", kelly_cap=0.25)
        except Exception:
            continue
        cands = [d for d in (analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis)
                 if d.decision != "AVOID"]
        if not cands:
            continue
        bet_dir = max(cands, key=lambda d: d.ev)
        won = (RESULT_TO_DIRECTION[int(row["actual_result"])] == bet_dir.direction)
        recs.append({"fold": int(fold_sub[i]), "season": season_of(pd.Timestamp(row["date"])),
                     "direction": bet_dir.direction, "odds": bet_dir.odds,
                     "won": int(won), "profit": (bet_dir.odds - 1.0) if won else -1.0,
                     "impl": 1.0 / o[0] + 1.0 / o[1] + 1.0 / o[2]})
    B = pd.DataFrame(recs)
    print(f"   竞彩价值投注: {len(B)}")

    def agg(g):
        n = len(g)
        if n == 0:
            return None
        hit = g["won"].mean()
        odds = g["odds"].mean()
        se = np.sqrt(hit * (1 - hit) / n)
        z = (hit - 1.0 / odds) / se if se > 0 else 0.0
        roi = g["profit"].mean()
        vig = g["impl"].mean() - 1.0
        return {"n": n, "hit": hit, "odds": odds, "roi": roi, "profit": g["profit"].sum(),
                "vig": vig, "z": z, "recovery": vig + roi}

    print("[4/4] 生成报告...")
    lines = []
    lines.append("# 竞彩市场真实回收率稳健性验证（模型 vs 竞彩末条快照）\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- OOF 文件: `{args.oof.name}`（mean raw，TSS 折内 TemperatureScaling）")
    lines.append(f"- 市场: 竞彩 wdl_history 末条快照（win_a=主胜）；min_ev=0.02 / quarter-kelly cap=0.25")
    lines.append(f"- 评估子集: 折外可评估 {int(covered.sum())} 场；竞彩对齐投注 {len(B)} 笔\n")

    a = agg(B)
    lines.append(f"## 整体 — 投注 {a['n']} 命中 {a['hit']*100:.1f}% 均赔 {a['odds']:.2f} "
                 f"ROI {a['roi']*100:+.2f}% 抽水 {a['vig']*100:+.1f}% 真实回收 {a['recovery']*100:+.2f}pp "
                 f"z={a['z']:+.2f}\n")

    lines.append("### 分折（TSS5）\n")
    lines.append("| 折 | n | 命中率 | 平注ROI | 抽水 | 真实回收(pp) | z |")
    lines.append("|---|---|---|---|---|---|---|")
    n_pos_f = n_ok_f = 0
    for f in range(1, 6):
        seg = B[B["fold"] == f]
        if len(seg) < 30:
            lines.append(f"| 折{f} | {len(seg)} | — | 样本不足 | — | — | — |")
            continue
        s = agg(seg)
        n_ok_f += 1
        n_pos_f += int(s["recovery"] > 0)
        lines.append(f"| 折{f} | {s['n']} | {s['hit']*100:.1f}% | {s['roi']*100:+.2f}% | "
                     f"{s['vig']*100:+.1f}% | {s['recovery']*100:+.2f} | {s['z']:+.2f} |")
    lines.append(f"\n- 回收为正折数: {n_pos_f}/{n_ok_f}（n≥30）\n")

    lines.append("### 分赛季\n")
    lines.append("| 赛季 | n | 命中率 | 平注ROI | 抽水 | 真实回收(pp) | z |")
    lines.append("|---|---|---|---|---|---|---|")
    n_pos_s = n_ok_s = 0
    for sname, seg in B.groupby("season"):
        if len(seg) < 30:
            lines.append(f"| {sname} | {len(seg)} | — | 样本不足 | — | — | — |")
            continue
        s = agg(seg)
        n_ok_s += 1
        n_pos_s += int(s["recovery"] > 0)
        lines.append(f"| {sname} | {s['n']} | {s['hit']*100:.1f}% | {s['roi']*100:+.2f}% | "
                     f"{s['vig']*100:+.1f}% | {s['recovery']*100:+.2f} | {s['z']:+.2f} |")
    lines.append(f"\n- 回收为正赛季数: {n_pos_s}/{n_ok_s}（n≥30）\n")

    lines.append("### 分方向\n")
    lines.append("| 方向 | n | 命中率 | 平注ROI | 抽水 | 真实回收(pp) | z |")
    lines.append("|---|---|---|---|---|---|---|")
    for d in ["home", "draw", "away"]:
        seg = B[B["direction"] == d]
        if len(seg) < 30:
            lines.append(f"| {d} | {len(seg)} | — | 样本不足 | — | — | — |")
            continue
        s = agg(seg)
        lines.append(f"| {d} | {s['n']} | {s['hit']*100:.1f}% | {s['roi']*100:+.2f}% | "
                     f"{s['vig']*100:+.1f}% | {s['recovery']*100:+.2f} | {s['z']:+.2f} |")

    lines.append("\n## 结论\n")
    lines.append(f"- 整体真实回收 {a['recovery']*100:+.2f}pp，z={a['z']:+.2f}；"
                 f"{'显著 ✅' if abs(a['z']) >= 2 else '不显著 ❌'}")
    lines.append(f"- 分折回收为正: {n_pos_f}/{n_ok_f}；分赛季回收为正: {n_pos_s}/{n_ok_s}")
    lines.append(f"- ROI 转正: {'✅' if a['roi'] > 0 else '❌'}（ROI {a['roi']*100:+.2f}%，"
                 f"抽水 {a['vig']*100:+.1f}% 需先覆盖）")

    md_path = REPORT_DIR / f"market_recovery_robust_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"market_recovery_robust_{RUN_TS}.json"
    json_path.write_text(json.dumps({
        "meta": {"oof": str(args.oof), "N": int(len(df)), "covered": int(covered.sum()),
                 "n_bets": int(len(B)), "ts": RUN_TS},
        "overall": {k: float(v) for k, v in a.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")


if __name__ == "__main__":
    main()
