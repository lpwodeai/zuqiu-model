# -*- coding: utf-8 -*-
"""P2-02 低比分系统性低估诊断。

问题背景（docs/基于预测报告发现的问题.txt §4.4）：
  基础泊松假设两队进球互相独立，而足球大量保守防守比赛使 0-0、1-0 被系统性
  低估。需回测历史大量样本，统计「模型输出低比分概率 vs 真实发生频率」，量化
  是否存在系统性低估，作为后续 Dixon-Coles / ZIP 零膨胀泊松迭代的复现基线。

数据来源：
  - model_predictions（model_name='t006_score_predictor_v5'）：
      Score_grid_H_A → 0..5 × 0..5 共 36 格比分概率（每场概率和≈1）
      Lambda_home / Lambda_away → 模型 λ 进球期望
  - matches.actual_score → 实际比分（"H:A" 字符串，覆盖中/英文赛季 match_id）

用法：
  python low_score_diagnosis.py
  python low_score_diagnosis.py --db data/odds.db

输出：
  - reports/low_score_diagnosis_<TS>.md / .json
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"

# 低比分关注格子（行业已知共性缺陷重点：0-0 / 1-0 / 0-1）
LOW_SCORE_CELLS = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2)]


def parse_score(score_str):
    """'H:A' → (h, a)；非法返回 (None, None)。"""
    if not score_str or ":" not in str(score_str):
        return None, None
    try:
        h, a = str(score_str).split(":")[:2]
        return int(h), int(a)
    except (ValueError, IndexError):
        return None, None


def load_score_predictions(db_path):
    """读取比分概率网格 + λ + 实际比分，返回 list[dict]。

    每条：{match_id, league, date, actual_h, actual_a, total_goals,
           lambda_home, lambda_away, grid: {(h,a): prob}}
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    # 1. 比分概率网格 + λ（按 match_id 分组）
    grids = defaultdict(dict)     # match_id -> {(h,a): prob}
    lambdas = defaultdict(dict)   # match_id -> {home: x, away: y}
    cur.execute(
        "SELECT match_id, prediction_type, probability FROM model_predictions "
        "WHERE model_name='t006_score_predictor_v5' AND ("
        "prediction_type LIKE 'Score_grid%' OR prediction_type IN ('Lambda_home','Lambda_away'))"
    )
    for mid, ptype, prob in cur.fetchall():
        if ptype.startswith("Score_grid_"):
            try:
                _, _, hs, as_ = ptype.split("_")
                h, a = int(hs), int(as_)
                grids[mid][(h, a)] = float(prob)
            except (ValueError, IndexError):
                continue
        elif ptype == "Lambda_home":
            lambdas[mid]["home"] = float(prob)
        elif ptype == "Lambda_away":
            lambdas[mid]["away"] = float(prob)

    # 2. 实际比分 + 联赛（matches 表）
    actual = {}
    league_map = {}
    cur.execute("SELECT match_id, actual_score, league, match_date FROM matches")
    for mid, ascore, lg, md in cur.fetchall():
        h, a = parse_score(ascore)
        if h is None:
            continue
        actual[mid] = (h, a)
        league_map[mid] = lg or "Unknown"

    conn.close()

    rows = []
    for mid, grid in grids.items():
        if mid not in actual:
            continue
        h, a = actual[mid]
        lm = lambdas.get(mid, {})
        rows.append({
            "match_id": mid,
            "league": league_map.get(mid, "Unknown"),
            "actual_h": h,
            "actual_a": a,
            "total_goals": h + a,
            "lambda_home": lm.get("home"),
            "lambda_away": lm.get("away"),
            "grid": grid,
        })
    return rows


def prob_of(grid, h, a):
    """取格子概率，越界（比分超出 5）返回 0。"""
    return float(grid.get((h, a), 0.0)) if 0 <= h <= 5 and 0 <= a <= 5 else 0.0


def diagnose_low_score(rows):
    """核心诊断：低比分格子 + 总进球分档的预测 vs 实际。"""
    n = len(rows)

    # 2.1 单格低比分偏差
    cell_stats = []
    for (h, a) in LOW_SCORE_CELLS:
        preds = [prob_of(r["grid"], h, a) for r in rows]
        hits = sum(1 for r in rows if r["actual_h"] == h and r["actual_a"] == a)
        avg_pred = float(np.mean(preds)) if preds else 0.0
        freq = hits / n if n else 0.0
        cell_stats.append({
            "score": f"{h}:{a}",
            "n": n,
            "avg_pred": avg_pred,
            "actual_freq": freq,
            "bias_pp": (freq - avg_pred) * 100,  # 正值=低估
        })

    agg_stats = []
    for agg_kind in (0, 1, -1, 2):  # -1 表示 ≤1
        if agg_kind == 0:
            label = "总进球=0 (0:0)"
            preds = [prob_of(r["grid"], 0, 0) for r in rows]
            hits = [r["total_goals"] == 0 for r in rows]
        elif agg_kind == 1:
            label = "总进球=1"
            preds = [sum(prob_of(r["grid"], h, a) for h, a in [(1, 0), (0, 1)]) for r in rows]
            hits = [r["total_goals"] == 1 for r in rows]
        elif agg_kind == -1:
            label = "总进球≤1 (小球)"
            preds = [sum(prob_of(r["grid"], h, a) for h, a in [(0, 0), (1, 0), (0, 1)]) for r in rows]
            hits = [r["total_goals"] <= 1 for r in rows]
        else:
            label = "总进球≤2"
            preds = [sum(prob_of(r["grid"], h, a) for h in range(6) for a in range(6) if h + a <= 2) for r in rows]
            hits = [r["total_goals"] <= 2 for r in rows]

        avg_pred = float(np.mean(preds)) if preds else 0.0
        freq = float(np.mean(hits)) if hits else 0.0
        agg_stats.append({
            "label": label,
            "n": n,
            "avg_pred": avg_pred,
            "actual_freq": freq,
            "bias_pp": (freq - avg_pred) * 100,
        })

    # 2.3 总进球≤1 的可靠性分桶（预测概率 5pp 档 → 实际小球率）
    pred_le1 = [sum(prob_of(r["grid"], h, a) for h, a in [(0, 0), (1, 0), (0, 1)]) for r in rows]
    is_le1 = [1.0 if r["total_goals"] <= 1 else 0.0 for r in rows]
    buckets = []
    edges = np.arange(0.0, 0.61, 0.05)
    for i in range(len(edges)):
        lo = edges[i]
        hi = edges[i + 1] if i + 1 < len(edges) else 1.0
        m = [(p, y) for p, y in zip(pred_le1, is_le1) if lo <= p < hi]
        if not m:
            continue
        preds_b = [x[0] for x in m]
        y_b = [x[1] for x in m]
        buckets.append({
            "bin": f"{lo:.2f}~{hi:.2f}",
            "n": len(m),
            "avg_pred": float(np.mean(preds_b)),
            "actual_freq": float(np.mean(y_b)),
            "bias_pp": (float(np.mean(y_b)) - float(np.mean(preds_b))) * 100,
        })

    # 2.4 分联赛 总进球≤1 偏差
    league_stats = defaultdict(lambda: {"n": 0, "pred_sum": 0.0, "hit_sum": 0.0})
    for r in rows:
        lg = r["league"]
        p = sum(prob_of(r["grid"], h, a) for h, a in [(0, 0), (1, 0), (0, 1)])
        league_stats[lg]["n"] += 1
        league_stats[lg]["pred_sum"] += p
        league_stats[lg]["hit_sum"] += 1.0 if r["total_goals"] <= 1 else 0.0
    league_list = []
    for lg, v in sorted(league_stats.items(), key=lambda x: -x[1]["n"]):
        if v["n"] < 50:
            continue
        avg_pred = v["pred_sum"] / v["n"]
        freq = v["hit_sum"] / v["n"]
        league_list.append({
            "league": lg,
            "n": v["n"],
            "avg_pred": avg_pred,
            "actual_freq": freq,
            "bias_pp": (freq - avg_pred) * 100,
        })

    return {
        "n": n,
        "cell_stats": cell_stats,
        "agg_stats": agg_stats,
        "le1_buckets": buckets,
        "league_stats": league_list,
    }


def generate_report(report, md_path=None, json_path=None):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    md_path = md_path or (REPORT_DIR / f"low_score_diagnosis_{ts}.md")
    json_path = json_path or (REPORT_DIR / f"low_score_diagnosis_{ts}.json")

    L = []
    L.append("# 低比分系统性低估诊断报告（P2-02）\n")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append(f"**样本量**: {report['n']} 场（T-006 v5 比分预测 × 实际比分）\n")
    L.append("> bias_pp = 真实频率 − 模型预测概率（**正值 = 模型系统性低估该结果**）\n")

    L.append("## 一、低比分单格偏差\n")
    L.append("| 比分 | 样本量 | 模型预测概率 | 真实发生频率 | 偏差(pp) |")
    L.append("|---|---|---|---|---|")
    for c in report["cell_stats"]:
        L.append(f"| {c['score']} | {c['n']} | {c['avg_pred']*100:.2f}% | "
                 f"{c['actual_freq']*100:.2f}% | {c['bias_pp']:+.2f} |")
    L.append("")

    L.append("## 二、总进球分档偏差\n")
    L.append("| 档位 | 样本量 | 模型预测概率 | 真实发生频率 | 偏差(pp) |")
    L.append("|---|---|---|---|---|")
    for c in report["agg_stats"]:
        L.append(f"| {c['label']} | {c['n']} | {c['avg_pred']*100:.2f}% | "
                 f"{c['actual_freq']*100:.2f}% | {c['bias_pp']:+.2f} |")
    L.append("")

    L.append("## 三、总进球≤1 可靠性分桶（预测概率 vs 实际小球率）\n")
    L.append("| 预测概率档 | 样本量 | 平均预测 | 实际小球率 | 偏差(pp) |")
    L.append("|---|---|---|---|---|")
    for b in report["le1_buckets"]:
        L.append(f"| {b['bin']} | {b['n']} | {b['avg_pred']*100:.2f}% | "
                 f"{b['actual_freq']*100:.2f}% | {b['bias_pp']:+.2f} |")
    L.append("")

    L.append("## 四、分联赛 总进球≤1 偏差\n")
    L.append("| 联赛 | 样本量 | 模型预测概率 | 真实发生频率 | 偏差(pp) |")
    L.append("|---|---|---|---|---|")
    for c in report["league_stats"]:
        L.append(f"| {c['league']} | {c['n']} | {c['avg_pred']*100:.2f}% | "
                 f"{c['actual_freq']*100:.2f}% | {c['bias_pp']:+.2f} |")
    L.append("\n---\n*本报告由 low_score_diagnosis.py 自动生成*\n")

    md_path.write_text("\n".join(L), encoding="utf-8")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="低比分系统性低估诊断（P2-02）")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    print("加载比分预测 + 实际比分...")
    rows = load_score_predictions(Path(args.db))
    print(f"  配对样本: {len(rows)} 场")

    report = diagnose_low_score(rows)
    print("\n" + "=" * 60)
    print(f"低比分低估诊断（n={report['n']}）")
    for c in report["cell_stats"]:
        print(f"  {c['score']}: 预测 {c['avg_pred']*100:.2f}% vs 实际 {c['actual_freq']*100:.2f}% "
              f"(偏差 {c['bias_pp']:+.2f}pp)")
    print("=" * 60)

    md_path, json_path = generate_report(report)
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")


if __name__ == "__main__":
    main()