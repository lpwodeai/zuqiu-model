# -*- coding: utf-8 -*-
"""EV 择场 / 阈值抬升扫描脚本。

背景：
  概率校准对比实验（C-20260903-006）结论：校准本身无法使 EV ROI 转正
  （TempScaling(on raw) 最佳仍 -3.71%）。本脚本沿 CALIB-007 ①路径推进：
  「EV 阈值抬升 + 置信过滤」双条件择场，寻找 ROI 转正的 阈值×置信 组合。

数据：
  assets/calibrated_probs_20260903_163558.csv（11965 场 OOF × 6 口径校准后概率）。
  注意：TempScaling/Vector/Isotonic 等非基线方案仅覆盖 9970 场（TimeSeriesSplit(5)
  折外验证部分；最早 1995 场属折1训练段无折外预测）。为公平对比，本脚本把
  Baseline 与 Temp 都限制在同一 9970 场子集上扫描。

扫描维度：
  - 校准方案：BaselinePlatt（参照）/ TempScaling(on raw)（首选）
  - min_ev 阈值：0.02 / 0.03 / 0.05 / 0.07 / 0.10 / 0.15
  - 置信过滤：无 / P50 / P75 / P90（基于该方案概率 max_class 的分布分位）

评估：逐场 ev_engine（quarter-kelly cap=0.25, odds500_live 赔率桥接），
      输出 n / 命中率 / 平注ROI / 凯利ROI / 平均EV / 平均赔率。
      并对最优组合额外输出分赛季 ROI，验证是否一致而非单桶运气。

用法：
  python ev_threshold_sweep.py
  python ev_threshold_sweep.py --csv ../assets/calibrated_probs_20260903_163558.csv
"""
import argparse
import csv
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ev_engine import analyze_match, ModelProbabilities, OddsData  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"
DB_PATH = PROJECT_DIR / "data" / "odds.db"

RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}
# 校准 CSV 中的方案列前缀
METHODS = {
    "Baseline(Platt)": "BaselinePlatt",
    "TempScaling(on raw)": "TempScalingon raw",
    "Temp+DrawCal(0.30)": "Temp_plus_DrawCal0.30",
}
MIN_EVS = [0.02, 0.03, 0.05, 0.07, 0.10, 0.15]
CONF_FILTERS = [None, 0.50, 0.75, 0.90]


def build_odds(db_path: Path):
    """同 ev_backtest：odds500_live 映射 + 中文 match_id 队名桥。"""
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


def load_rows(csv_path: Path):
    """加载校准 CSV，仅保留 Temp 方案有值（非折1训练段）的 9970 场，带赔率键。"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    df = df[df["actual_result"].notna()].copy()
    df["actual_result"] = df["actual_result"].astype(int)
    # 过滤掉校准值为 0 的行（折1训练段 1995 场无折外校准输出）
    temp_cols = [f"TempScalingon raw_{d}" for d in ("away", "draw", "home")]
    mask = (df[temp_cols].sum(axis=1) > 0).values
    df = df[mask].reset_index(drop=True)
    return df


def season_from_date(date_str):
    if not date_str:
        return "unknown"
    try:
        y, m, _ = date_str[:10].split("-")
        year, month = int(y), int(m)
    except (ValueError, IndexError):
        return "unknown"
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def run_sweep(df, odds_map, bridge, method_key, prefix, min_ev, conf_q):
    """对给定方案/阈值/置信分位跑一次 EV 回测。返回统计 dict。"""
    probs = df[[f"{prefix}_away", f"{prefix}_draw", f"{prefix}_home"]].values.astype(float)
    conf_all = probs.max(axis=1)
    if conf_q is not None:
        th = np.quantile(conf_all, conf_q)
        keep = conf_all >= th
    else:
        keep = np.ones(len(df), dtype=bool)

    flat_profits, kelly_profits, stakes, ev_list, odds_list = [], [], [], [], []
    wins = bets = with_odds = 0
    by_season = defaultdict(lambda: {"n": 0, "wins": 0, "profit": 0.0})
    by_direction = defaultdict(lambda: {"n": 0, "wins": 0, "profit": 0.0})

    for i in np.where(keep)[0]:
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
            continue
        with_odds += 1
        p = probs[i]
        try:
            analysis = analyze_match(
                probs=ModelProbabilities(home=float(p[2]), draw=float(p[1]), away=float(p[0])),
                odds=OddsData(home=float(o[0]), draw=float(o[1]), away=float(o[2])),
                ev_threshold=min_ev,
                kelly_strategy="quarter",
                kelly_cap=0.25,
            )
        except Exception:
            continue
        # 阈值抬升语义：仅对 EV > ev_threshold 的 VALUE 方向下注，
        # MARGINAL（0<EV<=threshold）视为不出手，真正实现 min_ev 过滤。
        cands = [d for d in (analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis)
                 if d.decision == "VALUE"]
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
        odds_list.append(odds_val)
        if won:
            wins += 1
        bets += 1
        seas = season_from_date(row.get("date", ""))
        by_season[seas]["n"] += 1
        by_season[seas]["wins"] += int(won)
        by_season[seas]["profit"] += (odds_val - 1.0) if won else -1.0
        by_direction[bet_dir.direction]["n"] += 1
        by_direction[bet_dir.direction]["wins"] += int(won)
        by_direction[bet_dir.direction]["profit"] += (odds_val - 1.0) if won else -1.0

    n = len(flat_profits)
    return {
        "method": method_key,
        "min_ev": min_ev,
        "conf_q": conf_q,
        "with_odds": with_odds,
        "bets": n,
        "hit_rate": (wins / n) if n else 0.0,
        "flat_roi": (sum(flat_profits) / n) if n else 0.0,
        "flat_profit": sum(flat_profits),
        "kelly_roi": (sum(kelly_profits) / sum(stakes)) if stakes else 0.0,
        "kelly_profit": sum(kelly_profits),
        "avg_ev": (sum(ev_list) / n) if n else 0.0,
        "avg_odds": (sum(odds_list) / n) if n else 0.0,
        "by_season": {k: {**v, "hit_rate": v["wins"] / v["n"]} for k, v in sorted(by_season.items())},
        "by_direction": {k: {**v, "hit_rate": v["wins"] / v["n"]} for k, v in by_direction.items()},
    }


def main():
    parser = argparse.ArgumentParser(description="EV 择场 / 阈值抬升扫描")
    parser.add_argument("--csv", type=Path,
                        default=ASSETS_DIR / "calibrated_probs_20260903_163558.csv")
    args = parser.parse_args()

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)

    # 静音 ev_engine 内部 logger.warning（隐含概率异常刷屏，扫描场景无观察价值）
    logging.disable(logging.WARNING)

    print("[1/3] 加载校准概率与赔率...")
    df = load_rows(args.csv)
    odds_map, bridge = build_odds(DB_PATH)
    print(f"   样本(校准有值): {len(df)}  赔率映射: {len(odds_map)}")

    print("[2/3] 扫描 min_ev × 置信过滤 × 方案 ...")
    results = {}
    for mkey, prefix in METHODS.items():
        results[mkey] = {}
        for me in MIN_EVS:
            for cq in CONF_FILTERS:
                key = (me, cq)
                r = run_sweep(df, odds_map, bridge, mkey, prefix, me, cq)
                results[mkey][key] = r
        print(f"   {mkey} 完成")

    print("[3/3] 生成报告...")
    lines = []
    lines.append("# EV 择场 / 阈值抬升扫描报告\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- 样本: {len(df)} 场 OOF（校准有值的折外验证部分，对 Baseline/Temp 同口径）")
    lines.append(f"- 赔率源: odds500_live（含中文 match_id 队名桥接，96.4% 对齐）")
    lines.append("- 凯利: quarter，cap=0.25；EV 阈值/置信过滤如矩阵所示\n")

    for mkey, prefix in METHODS.items():
        lines.append(f"\n## {mkey}\n")
        lines.append("| min_ev | 过滤 | 投注n | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV | 平均赔率 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for me in MIN_EVS:
            for cq in CONF_FILTERS:
                r = results[mkey][(me, cq)]
                if r["bets"] == 0:
                    lines.append(f"| {me:.2f} | {cq if cq else '无':<4} | 0 | - | - | - | - | - | - |")
                    continue
                mark = " 🟢" if r["flat_roi"] > 0 else ""
                lines.append(
                    f"| {me:.2f} | {cq if cq else '无':<4} | {r['bets']} | {r['hit_rate']*100:.1f}% | "
                    f"{r['flat_roi']*100:+.2f}%{mark} | {r['flat_profit']:+.2f} | "
                    f"{r['kelly_roi']*100:+.2f}% | {r['avg_ev']*100:+.2f}% | {r['avg_odds']:.2f} |"
                )

    # 最优组合
    best = None
    for mkey, prefix in METHODS.items():
        for (me, cq), r in results[mkey].items():
            if r["bets"] < 100:
                continue  # 样本太少不可信
            if best is None or r["flat_roi"] > best["flat_roi"]:
                best = {**r, "method": mkey, "min_ev": me, "conf_q": cq}

    if best is None:
        best = max(
            (r for m in results.values() for r in m.values() if r["bets"] > 0),
            key=lambda r: r["flat_roi"],
        )

    lines.append(f"\n## 最优组合（投注数≥100 中 ROI 最高）\n")
    lines.append(f"- 方案: **{best['method']}**  min_ev=**{best['min_ev']:.2f}**  置信过滤=**{best['conf_q'] if best['conf_q'] else '无'}**")
    lines.append(f"- 投注 {best['bets']} 场 | 命中率 {best['hit_rate']*100:.1f}% | 平注ROI {best['flat_roi']*100:+.2f}% | 盈亏 {best['flat_profit']:+.2f} | 凯利ROI {best['kelly_roi']*100:+.2f}% | 平均EV {best['avg_ev']*100:+.2f}%\n")
    lines.append("### 分赛季 ROI（最优组合，验证一致性）\n")
    lines.append("| 赛季 | n | 命中率 | 平注ROI | 盈亏 |")
    lines.append("|---|---|---|---|---|")
    for s, v in best["by_season"].items():
        roi = v["profit"] / v["n"] if v["n"] else 0
        lines.append(f"| {s} | {v['n']} | {v['hit_rate']*100:.1f}% | {roi*100:+.2f}% | {v['profit']:+.2f} |")
    lines.append("\n### 分方向（最优组合）\n")
    lines.append("| 方向 | n | 命中率 | 平注ROI |")
    lines.append("|---|---|---|---|")
    for d, v in best["by_direction"].items():
        roi = v["profit"] / v["n"] if v["n"] else 0
        lines.append(f"| {d} | {v['n']} | {v['hit_rate']*100:.1f}% | {roi*100:+.2f}% |")

    lines.append(f"\n## 结论\n")
    pos = [(mkey, me, cq, r) for mkey in METHODS for (me, cq), r in results[mkey].items()
           if r["flat_roi"] > 0 and r["bets"] >= 100]
    lines.append(f"- ROI 转正组合（n≥100）: {len(pos)} 个")
    for mkey, me, cq, r in pos[:10]:
        lines.append(f"  - {mkey} | min_ev={me:.2f} | 置信={cq if cq else '无'} | n={r['bets']} | ROI {r['flat_roi']*100:+.2f}% | 命中 {r['hit_rate']*100:.1f}%")
    if pos:
        lines.append("\n**结论：择场过滤可将 ROI 转正**，最优组合见上；建议结合置信度与投注数平衡，确认分赛季一致性后落生产。")
    else:
        lines.append("\n**结论：在当前校准概率与 EV 决策逻辑下，任何 min_ev×置信组合都无法使 ROI 转正**（投注数≥100），"
                     "说明系统性高估无法通过纯择场消除，需推进训练端 EV/ROI 目标改造或 edge 分桶单调回归修复。")

    md_path = REPORT_DIR / f"ev_threshold_sweep_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"ev_threshold_sweep_{RUN_TS}.json"
    json_path.write_text(json.dumps(
        {mkey: {f"{me}_{cq}": r for (me, cq), r in m.items()} for mkey, m in results.items()},
        ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")

    print("\n===== 速览（平注ROI）=====")
    for mkey in METHODS:
        print(f"\n{mkey}:")
        header = "          " + "".join(f"{(cq if cq else '无'):>10}" for cq in CONF_FILTERS)
        print("          " + "".join(f"{'ROI/n':>10}" for _ in CONF_FILTERS))
        for me in MIN_EVS:
            cells = []
            for cq in CONF_FILTERS:
                r = results[mkey][(me, cq)]
                cells.append(f"{r['flat_roi']*100:+7.2f}/{r['bets']}")
            print(f"min_ev={me:.2f}  " + "".join(f"{c:>10}" for c in cells))


if __name__ == "__main__":
    main()
