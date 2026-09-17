# -*- coding: utf-8 -*-
"""双轨回测系统（P2-06）。

严格区分两层评估，防止「全量命中率」掩盖「全量 EV 为负」的现实：
  轨道 A（第一层预测内核，全部场次）：RPS / LogLoss / 准确率 / 命中率 / Top-class ECE
  轨道 B（第二层 EV 决策引擎，仅 EV>0 筛选场次）：
      ROI / 最大回撤 / 最长连亏 / 盈亏比 / EV 偏差 + EV 分层切片
      （按 match_ev_category、edge 分桶、EV 分桶分别统计实际 ROI）

复用 ev_backtest 的赔率加载与队名桥接（build_team_bridge / load_model_predictions /
load_market_odds / season_from_date），复用 risk_monitor 的风险指标口径，保证与既有
回测结论可比、指标不漂移。

用法：
  python dual_track_backtest.py
  python dual_track_backtest.py --model mean --odds-source odds500_live --min-ev 0.02

输出：
  - reports/dual_track_backtest_<TS>.md / .json
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ev_engine import analyze_match, ModelProbabilities, OddsData  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402
from ev_backtest import (  # noqa: E402
    build_team_bridge, load_model_predictions, load_market_odds, season_from_date,
    _roi_summary, _edge_bucket,
)
from risk_monitor import compute_risk_metrics, evaluate_risk, RiskConfig  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "assets" / "oof_predictions_20260903_012452.csv"
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"

RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}
CLASS_ORDER = ["away", "draw", "home"]  # RPS 累积顺序（客胜<平局<主胜）


# ---------------------------------------------------------------------------
# 轨道 A：第一层预测内核质量
# ---------------------------------------------------------------------------
def _rps(p_home, p_draw, p_away, actual_direction):
    """三分类 Ranked Probability Score（累积顺序 [away, draw, home]，K-1=2）。"""
    probs = np.array([p_away, p_draw, p_home], dtype=float)
    idx = {"away": 0, "draw": 1, "home": 2}[actual_direction]
    obs = np.zeros(3)
    obs[idx] = 1.0
    fp = np.cumsum(probs)
    fo = np.cumsum(obs)
    return float(np.sum((fp - fo) ** 2) / 2.0)


def run_track_a(preds):
    """全部场次的 RPS / LogLoss / 准确率 / 命中率 / Top-class ECE。"""
    n = len(preds)
    rps_sum = 0.0
    ll_sum = 0.0
    hits = 0  # argmax 命中
    ece_sum = 0.0
    edges = np.linspace(0, 1, 11)
    # top-class ECE 分桶统计
    bins = [{"conf": [], "correct": []} for _ in range(10)]
    for p in preds:
        h, d, a = p["home"], p["draw"], p["away"]
        actual = p["actual_direction"]
        rps_sum += _rps(h, d, a, actual)
        probs = {"home": h, "draw": d, "away": a}
        p_correct = probs[actual]
        p_correct = max(min(p_correct, 1 - 1e-12), 1e-12)
        ll_sum += -np.log(p_correct)
        pred_dir = max(probs, key=probs.get)
        if pred_dir == actual:
            hits += 1
        conf = max(h, d, a)
        b = min(int(conf // 0.1), 9)
        bins[b]["conf"].append(conf)
        bins[b]["correct"].append(1.0 if pred_dir == actual else 0.0)
    ece = 0.0
    for b in bins:
        if b["conf"]:
            ece += (len(b["conf"]) / n) * abs(
                np.mean(b["correct"]) - np.mean(b["conf"]))
    return {
        "n": n,
        "rps": rps_sum / n if n else 0.0,
        "log_loss": ll_sum / n if n else 0.0,
        "accuracy": hits / n if n else 0.0,
        "hit_rate": hits / n if n else 0.0,
        "top_ece": ece,
    }


# ---------------------------------------------------------------------------
# 轨道 B：第二层 EV 决策引擎质量
# ---------------------------------------------------------------------------
def _ev_bucket(ev):
    if ev <= 0:
        return "≤0"
    for lo, hi, label in [(0.0, 0.02, "0~2%"), (0.02, 0.05, "2~5%"),
                          (0.05, 0.10, "5~10%"), (0.10, 0.20, "10~20%"),
                          (0.20, 1.0, ">20%")]:
        if lo <= ev < hi:
            return label
    return "unknown"


def run_track_b(preds, odds_map, min_ev, kelly_strategy, kelly_cap):
    """EV 筛选回测：逐场 analyze_match，选非 AVOID 最高 EV 方向下注。"""
    bets = []
    stats = {"total": len(preds), "with_odds": 0, "bet": 0, "no_odds": 0}
    for p in preds:
        o = odds_map.get(p.get("_odds_key") or p["match_id"])
        if o is None:
            stats["no_odds"] += 1
            continue
        stats["with_odds"] += 1
        try:
            analysis = analyze_match(
                probs=ModelProbabilities(home=p["home"], draw=p["draw"], away=p["away"]),
                odds=OddsData(home=o[0], draw=o[1], away=o[2]),
                ev_threshold=min_ev,
                kelly_strategy=kelly_strategy,
                kelly_cap=kelly_cap,
            )
        except Exception:
            continue
        candidates = [d for d in (analysis.home_analysis, analysis.draw_analysis,
                                  analysis.away_analysis) if d.decision != "AVOID"]
        if not candidates:
            continue
        bet_dir = max(candidates, key=lambda d: d.ev)
        won = (p["actual_direction"] == bet_dir.direction)
        odds_val = bet_dir.odds
        flat_profit = (odds_val - 1.0) if won else -1.0
        stake = bet_dir.kelly_clipped
        kelly_profit = stake * (odds_val - 1.0) if won else -stake
        bets.append({
            "match_id": p["match_id"],
            "league": p["league"],
            "date": p.get("date", ""),
            "season": season_from_date(p.get("date", "")),
            "direction": bet_dir.direction,
            "direction_cn": bet_dir.direction_cn,
            "odds": odds_val,
            "edge": bet_dir.edge,
            "ev": bet_dir.ev,
            "kelly": bet_dir.kelly_clipped,
            "decision": bet_dir.decision,
            "match_ev_category": analysis.match_ev_category,
            "ev_bucket": _ev_bucket(bet_dir.ev),
            "edge_bucket": _edge_bucket(bet_dir.edge),
            "won": won,
            "flat_profit": flat_profit,
            "kelly_profit": kelly_profit,
        })
        stats["bet"] += 1
    return bets, stats


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def _roi_table(groups):
    lines = ["| 分组 | 场次 | 命中 | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV |",
             "|---|---|---|---|---|---|---|---|"]
    for k, v in groups.items():
        lines.append(f"| {k} | {v['n']} | {v['hits']} | {v['hit_rate']*100:.1f}% | "
                     f"{v['flat_roi']*100:+.2f}% | {v['flat_profit']:+.2f} | "
                     f"{v['kelly_roi']*100:+.2f}% | {v['avg_ev']*100:+.2f}% |")
    return "\n".join(lines)


def _risk_table(rep):
    L = []
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 下注场次 | {rep.n_bets} |")
    L.append(f"| 命中率 | {rep.hit_rate*100:.1f}% |")
    L.append(f"| 平注ROI | {rep.flat_roi*100:+.2f}% |")
    L.append(f"| 最大回撤 | {rep.max_drawdown*100:.2f}% |")
    L.append(f"| 当前回撤 | {rep.current_drawdown*100:.2f}% |")
    L.append(f"| 最长连亏 | {rep.longest_losing_streak} 场 |")
    L.append(f"| 当前连亏 | {rep.current_losing_streak} 场 |")
    L.append(f"| 盈亏比 | {'∞（无亏损）' if rep.profit_factor is None else f'{rep.profit_factor:.2f}'} |")
    L.append(f"| 平均EV | {rep.avg_ev*100:+.2f}% |")
    L.append(f"| EV偏差 | {rep.ev_bias*100:+.2f}pp |")
    return "\n".join(L)


def generate_report(cfg, track_a, bets, stats, risk, slices):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    md_path = REPORT_DIR / f"dual_track_backtest_{ts}.md"
    json_path = REPORT_DIR / f"dual_track_backtest_{ts}.json"

    L = []
    L.append("# 双轨回测报告（P2-06）\n")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append("## 一、配置\n")
    L.append(f"- 模型概率: `{cfg.model}` | 赔率源: `{cfg.odds_source}`")
    L.append(f"- EV 阈值: {cfg.min_ev} | 凯利策略: {cfg.kelly_strategy} | 仓位上限: {cfg.kelly_cap}\n")
    L.append("## 二、数据概览\n")
    L.append(f"- 全量预测场次: {stats['total']}（有赔率: {stats['with_odds']}，无赔率: {stats['no_odds']}）")
    L.append(f"- 触发投注场次: {stats['bet']}（VALUE+MARGINAL）\n")

    L.append("## 三、轨道 A：第一层预测内核（全部场次）\n")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 样本量 | {track_a['n']} |")
    L.append(f"| RPS | {track_a['rps']:.4f} |")
    L.append(f"| LogLoss | {track_a['log_loss']:.4f} |")
    L.append(f"| 准确率(argmax) | {track_a['accuracy']*100:.2f}% |")
    L.append(f"| Top-class ECE | {track_a['top_ece']*100:.2f}% |\n")

    L.append("## 四、轨道 B：第二层 EV 决策引擎（EV>0 筛选）\n")
    L.append(_risk_table(risk))
    if risk.alerts:
        L.append("\n**告警:**")
        for a in risk.alerts:
            L.append(f"- ⚠️ {a}")
    L.append("\n## 五、EV 分层切片（实际 ROI）\n")
    for title, groups in slices.items():
        L.append(f"### {title}\n")
        L.append(_roi_table(groups))
        L.append("")

    L.append("---\n*本报告由 dual_track_backtest.py 自动生成*\n")
    md_path.write_text("\n".join(L), encoding="utf-8")

    def conv(o):
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        if isinstance(o, list):
            return [conv(v) for v in o]
        return o

    payload = {
        "config": vars(cfg), "stats": stats, "track_a": track_a,
        "track_b_risk": {
            "n_bets": risk.n_bets, "hit_rate": risk.hit_rate, "flat_roi": risk.flat_roi,
            "flat_profit": risk.flat_profit, "max_drawdown": risk.max_drawdown,
            "current_drawdown": risk.current_drawdown,
            "longest_losing_streak": risk.longest_losing_streak,
            "current_losing_streak": risk.current_losing_streak,
            "profit_factor": risk.profit_factor, "avg_ev": risk.avg_ev,
            "avg_odds": risk.avg_odds, "ev_bias": risk.ev_bias, "alerts": risk.alerts,
        },
        "slices": slices,
        "bets": bets,
    }
    json_path.write_text(json.dumps(conv(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="双轨回测系统（P2-06）")
    parser.add_argument("--csv", default=str(CSV_PATH))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--model", choices=["mean", "xgb", "lgb"], default="mean")
    parser.add_argument("--odds-source",
                        choices=["odds500_live", "odds500_init", "odds500_match", "wdl_history"],
                        default="odds500_live")
    parser.add_argument("--min-ev", type=float, default=0.02)
    parser.add_argument("--kelly-strategy", choices=["full", "half", "quarter"], default="quarter")
    parser.add_argument("--kelly-cap", type=float, default=0.25)
    args = parser.parse_args()

    print("加载模型概率...")
    preds = load_model_predictions(Path(args.csv), args.model)
    print(f"  模型预测场次: {len(preds)} (model={args.model})")

    print("加载市场赔率...")
    odds_map = load_market_odds(Path(args.db), args.odds_source)
    bridge = build_team_bridge(Path(args.db))
    bridged = 0
    for p in preds:
        key = p["match_id"]
        if key not in odds_map:
            alt = bridge.get((p.get("date"), normalize_team_name(p.get("team_home", "")),
                              normalize_team_name(p.get("team_away", ""))))
            if alt and alt in odds_map:
                key = alt
                bridged += 1
        p["_odds_key"] = key
    print(f"  赔率记录: {len(odds_map)} | 队名桥接解析: {bridged}")

    print("运行轨道 A（全量预测内核）...")
    track_a = run_track_a(preds)

    print("运行轨道 B（EV 筛选）...")
    bets, stats = run_track_b(preds, odds_map, args.min_ev, args.kelly_strategy, args.kelly_cap)

    # EV 分层切片
    slices = {
        "按 match_ev_category": _roi_summary(bets, key=lambda r: r["match_ev_category"]),
        "按 edge 分桶": _roi_summary(bets, key=lambda r: r["edge_bucket"]),
        "按 EV 分桶": _roi_summary(bets, key=lambda r: r["ev_bucket"]),
        "按决策": _roi_summary(bets, key=lambda r: r["decision"]),
        "按联赛": _roi_summary(bets, key=lambda r: r["league"]),
        "按赛季": _roi_summary(bets, key=lambda r: r["season"]),
    }

    # 风险监控（复用 risk_monitor 口径）
    risk = evaluate_risk(compute_risk_metrics(
        [{"won": b["won"], "odds": b["odds"], "ev": b["ev"], "profit": b["flat_profit"]}
         for b in bets]), RiskConfig())

    print("\n" + "=" * 60)
    print(f"轨道A: n={track_a['n']} RPS={track_a['rps']:.4f} LogLoss={track_a['log_loss']:.4f} "
          f"Acc={track_a['accuracy']*100:.2f}% ECE={track_a['top_ece']*100:.2f}%")
    print(f"轨道B: 下注={risk.n_bets} 命中率={risk.hit_rate*100:.1f}% 平注ROI={risk.flat_roi*100:+.2f}% "
          f"最大回撤={risk.max_drawdown*100:.2f}% 最长连亏={risk.longest_losing_streak} "
          f"盈亏比={'∞' if risk.profit_factor is None else f'{risk.profit_factor:.2f}'}")
    print("=" * 60)

    md_path, json_path = generate_report(args, track_a, bets, stats, risk, slices)
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")


if __name__ == "__main__":
    main()