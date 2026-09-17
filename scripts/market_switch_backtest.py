# -*- coding: utf-8 -*-
"""C-20260905-003: 换投注市场重估 ROI — 竞彩末条 / 500.com 初盘 / 威廉希尔 live vs 基线 avg_live。

背景：
  系统性高估调查四层（概率/训练/数据/决策）全部证伪后唯一未验证维度 = **投注市场**。
  模型以竞彩赔率特征训练（13% 抽水），回测却一直用 500.com avg_live（5.4% 抽水）——
  训练域与投注域错位：模型学到的「错价结构」对应竞彩市场，换到 500.com 后 edge 信号
  被异域市场稀释。本脚本用同一模型概率（TSS 折内 TempScaling）对四个市场重跑 EV 回测：
  ①500live（基线对照）②竞彩末条快照（训练同域）③500.com 初盘均价 ④威廉希尔 live（sharp）。
  同时报告各市场隐含概率总和（抽水率），以「ROI vs 抽水」解释模型相对各市场的真实回收率。

设计：
  1. OOF mean raw + TSS(5) 折内 TemperatureScaling（与 edge_shrink/segment 同构）。
  2. 市场映射统一以「日期|中文主队|中文客队」为 key（OOF 自带），逐市场构建 odds 数组。
  3. 逐场 ev_engine（min_ev=0.02 / quarter-kelly cap=0.25）→ 整体 + edge 分桶 + 单调性。
  4. 各市场仅用「该市场有赔率的场次」回测（对齐域不同，n 不同，报告注明）。

用法：python market_switch_backtest.py [--oof ...]
产物：reports/market_switch_backtest_<TS>.md/.json
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
    EDGE_BUCKETS,
)
from ev_engine import analyze_match, ModelProbabilities, OddsData  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"
DB_PATH = PROJECT_DIR / "data" / "odds.db"

RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}


def edge_bucket(edge):
    for lo, hi, label in EDGE_BUCKETS:
        if lo <= edge < hi:
            return label
    return "unknown"


def load_markets(sub):
    """构建四个市场的 (key -> (home,draw,away)) 映射 + 每市场隐含概率总和均值。"""
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")

    # OOF key
    sub_key = (sub["date"].astype(str).str[:10] + "|" +
               sub["home_team_name"].map(normalize_team_name) + "|" +
               sub["away_team_name"].map(normalize_team_name)).tolist()

    # --- 500.com avg_live / avg_init（经 odds500_match 中文队名桥）---
    o500 = pd.read_sql_query("""
        SELECT s.match_id, s.avg_live_win, s.avg_live_draw, s.avg_live_lose,
               s.avg_init_win, s.avg_init_draw, s.avg_init_lose,
               m.match_date, m.home_team_cn, m.away_team_cn
        FROM odds500_ouzhi_summary s
        LEFT JOIN odds500_match m ON s.match_id = m.match_id
    """, conn)
    for c in ["avg_live_win", "avg_live_draw", "avg_live_lose",
              "avg_init_win", "avg_init_draw", "avg_init_lose"]:
        o500[c] = pd.to_numeric(o500[c], errors="coerce")
    o500 = o500[o500["match_date"].notna()].copy()
    o500["key"] = o500["match_date"].astype(str).str[:10] + "|" + \
        o500["home_team_cn"].map(normalize_team_name) + "|" + \
        o500["away_team_cn"].map(normalize_team_name)
    o500 = o500.drop_duplicates("key", keep="last")

    # --- 竞彩 wdl_history 末条快照 ---
    wdl = pd.read_sql_query("SELECT match_id, timestamp, win_a, win_b, draw FROM wdl_history", conn)
    for c in ["win_a", "win_b", "draw"]:
        wdl[c] = pd.to_numeric(wdl[c], errors="coerce")
    wdl = wdl[(wdl["win_a"] > 1) & (wdl["win_b"] > 1) & (wdl["draw"] > 1)].copy()
    wdl = wdl.sort_values(["match_id", "timestamp"])
    wdl_last = wdl.groupby("match_id").last().reset_index()
    parts = wdl_last["match_id"].astype(str).str.split("_", n=2, expand=True)
    wdl_last["key"] = parts[0] + "|" + parts[1].map(normalize_team_name) + "|" + \
        parts[2].map(normalize_team_name)
    wdl_last = wdl_last.drop_duplicates("key", keep="last")

    # --- 威廉希尔 live（company LIKE '威%'）---
    whl = pd.read_sql_query("""
        SELECT c.match_id, c.live_win, c.live_draw, c.live_lose,
               m.match_date, m.home_team_cn, m.away_team_cn
        FROM odds500_ouzhi_company c
        LEFT JOIN odds500_match m ON c.match_id = m.match_id
        WHERE c.company LIKE '威%%'
    """, conn)
    for c in ["live_win", "live_draw", "live_lose"]:
        whl[c] = pd.to_numeric(whl[c], errors="coerce")
    whl = whl[whl["match_date"].notna()].copy()
    whl["key"] = whl["match_date"].astype(str).str[:10] + "|" + \
        whl["home_team_cn"].map(normalize_team_name) + "|" + \
        whl["away_team_cn"].map(normalize_team_name)
    whl = whl.drop_duplicates("key", keep="last")
    conn.close()

    def build(m):
        d = m.set_index("key")
        out = {}
        for k in sub_key:
            if k not in d.index:
                continue
            r = d.loc[k]
            try:
                h, dr, a = float(r.iloc[0]), float(r.iloc[1]), float(r.iloc[2])
                if h > 1 and dr > 1 and a > 1:
                    out[k] = (h, dr, a)
            except (TypeError, ValueError):
                continue
        return out

    markets = {
        "500live(基线)": build(o500[["key", "avg_live_win", "avg_live_draw", "avg_live_lose"]]),
        "500init(初盘)": build(o500[["key", "avg_init_win", "avg_init_draw", "avg_init_lose"]]),
        "竞彩末条": build(wdl_last[["key", "win_a", "draw", "win_b"]]),   # win_a=主胜
        "威廉希尔live": build(whl[["key", "live_win", "live_draw", "live_lose"]]),
    }
    return sub_key, markets


def run_backtest(sub, probs, odds_map, sub_key):
    """逐场 ev_engine 回测，返回整体 + edge 分桶统计。"""
    flat_p, kelly_p, stakes, evs, odds_l, p_models = [], [], [], [], [], []
    wins = bets = 0
    by_edge = {k: {"n": 0, "wins": 0, "profit": 0.0, "pm": 0.0, "odds": 0.0, "ev": 0.0}
               for k in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]}
    impl_sum = []
    for i in range(len(sub)):
        o = odds_map.get(sub_key[i])
        if o is None:
            continue
        impl_sum.append(1.0 / o[0] + 1.0 / o[1] + 1.0 / o[2])
        p = probs[i]
        try:
            analysis = analyze_match(
                probs=ModelProbabilities(home=float(p[2]), draw=float(p[1]), away=float(p[0])),
                odds=OddsData(home=float(o[0]), draw=float(o[1]), away=float(o[2])),
                ev_threshold=0.02, kelly_strategy="quarter", kelly_cap=0.25)
        except Exception:
            continue
        cands = [d for d in (analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis)
                 if d.decision != "AVOID"]
        if not cands:
            continue
        bet_dir = max(cands, key=lambda d: d.ev)
        won = (RESULT_TO_DIRECTION[int(sub.iloc[i]["actual_result"])] == bet_dir.direction)
        odds_val = bet_dir.odds
        stake_k = bet_dir.kelly_clipped
        flat_p.append((odds_val - 1.0) if won else -1.0)
        kelly_p.append(stake_k * (odds_val - 1.0) if won else -stake_k)
        stakes.append(stake_k)
        evs.append(bet_dir.ev)
        odds_l.append(odds_val)
        p_models.append(bet_dir.p_model)
        wins += int(won)
        bets += 1
        b = edge_bucket(bet_dir.edge)
        v = by_edge[b]
        v["n"] += 1
        v["wins"] += int(won)
        v["profit"] += (odds_val - 1.0) if won else -1.0
        v["pm"] += bet_dir.p_model
        v["odds"] += odds_val
        v["ev"] += bet_dir.ev
    n = len(flat_p)
    buckets = {}
    for k, v in by_edge.items():
        if v["n"] == 0:
            continue
        nb = v["n"]
        buckets[k] = {"n": nb, "hit_rate": v["wins"] / nb, "flat_roi": v["profit"] / nb,
                      "flat_profit": v["profit"], "avg_model_p": v["pm"] / nb,
                      "avg_odds": v["odds"] / nb, "avg_ev": v["ev"] / nb}
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
        "avg_impl_sum": float(np.mean(impl_sum)) if impl_sum else 0.0,
        "by_edge": buckets,
    }


def main():
    parser = argparse.ArgumentParser(description="换投注市场重估 ROI")
    parser.add_argument("--oof", type=Path, default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    args = parser.parse_args()

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    logging.disable(logging.WARNING)

    print("[1/4] 加载 OOF 数据...")
    df = load_oof(args.oof)
    df = df.sort_values("date").reset_index(drop=True)
    y_true = df["actual_result"].values.astype(int)
    raw_probs = prob_matrix(df, src="mean", kind="raw")
    N = len(df)

    print("[2/4] TSS(5) 折内 TemperatureScaling...")
    temp_probs = np.zeros_like(raw_probs)
    tscv = TimeSeriesSplit(n_splits=5)
    for tr_idx, va_idx in tscv.split(raw_probs):
        ts = TemperatureScaler().fit(y_true[tr_idx], raw_probs[tr_idx])
        temp_probs[va_idx] = ts.transform(raw_probs[va_idx])
    covered = temp_probs.sum(axis=1) > 0
    sub = df[covered].reset_index(drop=True)
    temp_sub = temp_probs[covered]
    print(f"   覆盖行: {int(covered.sum())} / {N}")

    print("[3/4] 构建四市场赔率映射...")
    sub_key, markets = load_markets(sub)
    for name, m in markets.items():
        print(f"   {name:<16} 对齐场次: {len(m)}")

    print("[4/4] 逐市场 EV 回测 + 报告...")
    results = {}
    for name, m in markets.items():
        res = run_backtest(sub, temp_sub, m, sub_key)
        results[name] = res
        print(f"   {name:<16} 平ROI {res['flat_roi']*100:+6.2f}%  n={res['bets']}  "
              f"命中 {res['hit_rate']*100:5.1f}%  隐含和 {res['avg_impl_sum']:.4f}")

    lines = []
    lines.append("# 换投注市场重估 ROI — 竞彩末条 / 500 初盘 / 威廉希尔 live vs 基线 avg_live\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"- OOF 文件: `{args.oof.name}`（mean raw，TSS 折内 TemperatureScaling）")
    lines.append(f"- 模型概率: 同一 temp_probs（四市场共用，公平对比）；min_ev=0.02 / quarter-kelly cap=0.25")
    lines.append(f"- 评估子集: 折外可评估 {int(covered.sum())} 场；各市场仅用自身有赔率的场次\n")

    lines.append("## 一、整体 EV 回测（按市场）\n")
    lines.append("| 市场 | 对齐场次 | 投注n | 命中率 | 平注ROI | 盈亏 | 凯利ROI | 平均EV | 平均赔率 | 隐含和(抽水) |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for name, res in results.items():
        vig = (res["avg_impl_sum"] - 1.0) * 100
        lines.append(f"| {name} | {len(markets[name])} | {res['bets']} | {res['hit_rate']*100:.1f}% | "
                     f"{res['flat_roi']*100:+.2f}% | {res['flat_profit']:+.2f} | {res['kelly_roi']*100:+.2f}% | "
                     f"{res['avg_ev']*100:+.2f}% | {res['avg_odds']:.2f} | {res['avg_impl_sum']:.3f} "
                     f"({vig:+.1f}%) |")

    lines.append("\n## 二、edge 分桶（验证单调性）\n")
    lines.append("| 市场 | 桶 | n | 命中率 | 平注ROI | 模型概率 | 实现频率 | 高估(pp) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, res in results.items():
        for bk in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]:
            v = res["by_edge"].get(bk)
            if not v:
                continue
            over = (v["avg_model_p"] - v["hit_rate"]) * 100
            lines.append(f"| {name} | {bk} | {v['n']} | {v['hit_rate']*100:.1f}% | "
                         f"{v['flat_roi']*100:+.2f}% | {v['avg_model_p']*100:.1f}% | "
                         f"{v['hit_rate']*100:.1f}% | {over:+.1f} |")

    lines.append("\n## 三、单调性 + 转正\n")
    for name, res in results.items():
        seq = [(bk, res["by_edge"].get(bk)) for bk in ["0~3pp", "3~6pp", "6~10pp", ">10pp"]]
        seq = [(bk, v) for bk, v in seq if v and v["n"] >= 50]
        if len(seq) >= 2:
            rois = [v["flat_roi"] for _, v in seq]
            mono = all(rois[i] <= rois[i + 1] + 1e-9 for i in range(len(rois) - 1))
            detail = " → ".join(f"{bk} {v['flat_roi']*100:+.1f}%" for bk, v in seq)
            lines.append(f"- {name}: {'单调递增 ✅' if mono else '非单调 ❌'}  ({detail})")
        else:
            lines.append(f"- {name}: 分桶样本不足，无法判定单调性")
    pos = [(name, res) for name, res in results.items() if res["flat_roi"] > 0 and res["bets"] >= 100]
    lines.append(f"- ROI 转正组合（n≥100）: {len(pos)} 个")
    for name, res in pos:
        lines.append(f"  - {name}: ROI {res['flat_roi']*100:+.2f}%  n={res['bets']}  "
                     f"命中 {res['hit_rate']*100:.1f}%  隐含和 {res['avg_impl_sum']:.3f}")

    lines.append("\n## 四、结论\n")
    base = results["500live(基线)"]
    best = max((name for name, res in results.items() if res["bets"] >= 100),
               key=lambda nm: results[nm]["flat_roi"], default=None)
    if best:
        b = results[best]
        lines.append(f"- 最优市场: **{best}**  平注ROI {b['flat_roi']*100:+.2f}% "
                     f"(基线 avg_live {base['flat_roi']*100:+.2f}%, Δ={((b['flat_roi']-base['flat_roi'])*100):+.2f}pp)  n={b['bets']}")
    for name, res in results.items():
        vig = (res["avg_impl_sum"] - 1.0) * 100
        lines.append(f"- {name}: 模型相对该市场回收 = 市场抽水({vig:+.1f}%) + ROI({res['flat_roi']*100:+.2f}%) "
                     f"= {res['flat_roi']*100+vig:+.2f}pp（>0 即模型真实优于该市场）")

    md_path = REPORT_DIR / f"market_switch_backtest_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"market_switch_backtest_{RUN_TS}.json"
    json_path.write_text(json.dumps({
        "meta": {"oof": str(args.oof), "N": int(N), "covered": int(covered.sum()), "ts": RUN_TS},
        "results": {name: results[name] for name in results},
    }, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")


if __name__ == "__main__":
    main()
