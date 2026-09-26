# -*- coding: utf-8 -*-
"""C-20260921-039: TG 融合权重 A/B 实验。

验证提升赔率权重 0.15→0.25（Poisson 0.85→0.75）对预测质量的影响。
C-038 定位高桶 gap 根因在 Poisson λ 偏高（85% 权重主导），本实验验证
增大市场信号权重是否能让赔率分布更有效校正 λ 系统性偏差。

唯一变量：融合权重 W_ODDS（0.15 vs 0.25），去水方法固定 simple（当前生产口径）。

CLI:
    python scripts/tg_weight_shadow_eval.py
    python scripts/tg_weight_shadow_eval.py --w-odds 0.30
"""

from __future__ import annotations
import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from tg_calibration_shadow_eval import (  # noqa: E402
    load_replay_samples,
    load_latest_tg_goals,
    _poisson_total_goals,
    rps_8bucket,
    brier_over25,
    logloss_8bucket,
    ece_over25,
    paired_t_test,
    aggregate,
)
from tg_shin_devig import simple_devig, compute_vig  # noqa: E402

DB_PATH = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"


def _fuse_with_weight(tg_lambda_dist: Dict[int, float],
                      goals: Dict[str, float],
                      w_odds: float) -> Dict[int, float]:
    """融合 + 再归一化，权重可调。去水固定 simple（当前生产口径）。"""
    w_poisson = 1.0 - w_odds
    odds_probs_dict = simple_devig(goals)

    odds_probs: Dict[int, float] = {}
    for k, v in odds_probs_dict.items():
        key = int(k.replace("+", "")) if k.replace("+", "").isdigit() else 7
        odds_probs[key] = v

    fused: Dict[int, float] = {}
    for g in range(8):
        p_poisson = tg_lambda_dist.get(g, 0.0)
        p_odds = odds_probs.get(g, 0.0)
        fused[g] = p_poisson * w_poisson + p_odds * w_odds

    ft = sum(fused.values())
    if ft > 0:
        for g in fused:
            fused[g] /= ft
    return fused


def replay_match(sample: dict, goals: Dict[str, float],
                 w_odds: float) -> Optional[dict]:
    lh = sample["lambda_home"]
    la = sample["lambda_away"]
    poisson_dist = _poisson_total_goals(lh, la)
    fused = _fuse_with_weight(poisson_dist, goals, w_odds)

    dist_str = {str(g): fused.get(g, 0.0) for g in range(8)}
    over25 = sum(fused.get(g, 0.0) for g in range(3, 8))

    return {
        "match_id": sample["match_id"],
        "match_date": sample["match_date"],
        "league": sample["league"],
        "lambda_total": lh + la,
        "actual_tg": sample["actual_tg"],
        "published_over25": sample.get("published_over25"),
        "dist_8": dist_str,
        "over25_prob": over25,
        "vig": compute_vig(goals),
        "status": "ok",
    }


def run_eval(conn: sqlite3.Connection, w_odds_b: float = 0.25) -> list:
    samples = load_replay_samples(conn)
    results = []
    for s in samples:
        goals = load_latest_tg_goals(conn, s["home_team"], s["away_team"], s["match_date"])
        if not goals:
            continue
        row_a = replay_match(s, goals, 0.15)  # 生产基线
        row_b = replay_match(s, goals, w_odds_b)
        if row_a and row_b:
            results.append({"a": row_a, "b": row_b, "sample": s})
    return results


def report(results: list, w_odds_b: float) -> str:
    rows_a = [r["a"] for r in results]
    rows_b = [r["b"] for r in results]

    agg_a = aggregate(rows_a)
    agg_b = aggregate(rows_b)

    rps_deltas = [rps_8bucket(r["b"]["dist_8"], r["b"]["actual_tg"])
                  - rps_8bucket(r["a"]["dist_8"], r["a"]["actual_tg"]) for r in results]
    bri_deltas = [brier_over25(r["b"]["over25_prob"], r["b"]["actual_tg"])
                  - brier_over25(r["a"]["over25_prob"], r["a"]["actual_tg"]) for r in results]

    t_rps, df_rps, p_rps = paired_t_test(rps_deltas)
    t_bri, df_bri, p_bri = paired_t_test(bri_deltas)

    ece_a, bins_a = ece_over25(rows_a)
    ece_b, bins_b = ece_over25(rows_b)

    hi_a = [r for r in rows_a if r["over25_prob"] >= 0.8]
    hi_b = [r for r in rows_b if r["over25_prob"] >= 0.8]
    gap_hi_a = (sum(r["over25_prob"] for r in hi_a) / len(hi_a)
                - sum(1 for r in hi_a if r["actual_tg"] >= 3) / len(hi_a)) if hi_a else 0
    gap_hi_b = (sum(r["over25_prob"] for r in hi_b) / len(hi_b)
                - sum(1 for r in hi_b if r["actual_tg"] >= 3) / len(hi_b)) if hi_b else 0

    over25_a = [r["over25_prob"] for r in rows_a]
    over25_b = [r["over25_prob"] for r in rows_b]

    # Top1/Top3
    def top_hit(rows, k):
        hits = 0
        for r in rows:
            d = r["dist_8"]
            items = sorted(d.items(), key=lambda x: -x[1])
            top_k_goals = set(int(x[0]) for x in items[:k])
            actual = min(r["actual_tg"], 7)
            if actual in top_k_goals:
                hits += 1
        return hits / len(rows) if rows else 0

    lines = []
    lines.append("# TG 融合权重 A/B 实验报告\n")
    lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"变更号: C-20260921-039\n\n")

    lines.append("## 1. 样本概况\n")
    lines.append(f"- 总样本: {len(results)} 场\n")
    lines.append(f"- 时间范围: {results[0]['a']['match_date']} ~ {results[-1]['a']['match_date']}\n")
    leagues = {}
    for r in results:
        lg = r["a"]["league"]
        leagues[lg] = leagues.get(lg, 0) + 1
    lines.append(f"- 联赛分布: {leagues}\n\n")

    lines.append("## 2. 全集指标对比\n")
    lines.append(f"| 指标 | A (0.85/0.15) | B (0.75/{w_odds_b:.2f}) | Δ | 显著性 |\n")
    lines.append(f"|---|---|---|---|---|\n")
    lines.append(f"| RPS | {agg_a.get('rps_mean',0):.4f} | {agg_b.get('rps_mean',0):.4f} | "
                 f"{agg_b.get('rps_mean',0)-agg_a.get('rps_mean',0):+.4f} | "
                 f"t={t_rps:.2f} p={p_rps:.3f}{' *' if p_rps<0.05 else ''} |\n")
    lines.append(f"| Brier | {agg_a.get('brier_mean',0):.4f} | {agg_b.get('brier_mean',0):.4f} | "
                 f"{agg_b.get('brier_mean',0)-agg_a.get('brier_mean',0):+.4f} | "
                 f"t={t_bri:.2f} p={p_bri:.3f}{' *' if p_bri<0.05 else ''} |\n")
    lines.append(f"| ECE | {ece_a:.4f} | {ece_b:.4f} | {ece_b-ece_a:+.4f} | — |\n")
    lines.append(f"| ≥0.8 高桶 gap | {gap_hi_a:.3f} | {gap_hi_b:.3f} | {gap_hi_b-gap_hi_a:+.3f} | "
                 f"(n={len(hi_a)}/{len(hi_b)}) |\n")
    lines.append(f"| over25 均值 | {sum(over25_a)/len(over25_a):.4f} | "
                 f"{sum(over25_b)/len(over25_b):.4f} | "
                 f"{sum(over25_b)/len(over25_b)-sum(over25_a)/len(over25_a):+.4f} | — |\n")
    lines.append(f"| LogLoss | {agg_a.get('logloss_mean',0):.4f} | {agg_b.get('logloss_mean',0):.4f} | "
                 f"{agg_b.get('logloss_mean',0)-agg_a.get('logloss_mean',0):+.4f} | — |\n")
    lines.append(f"| Top1 命中 | {top_hit(rows_a,1):.1%} | {top_hit(rows_b,1):.1%} | "
                 f"{top_hit(rows_b,1)-top_hit(rows_a,1):+.1%} | — |\n")
    lines.append(f"| Top3 命中 | {top_hit(rows_a,3):.1%} | {top_hit(rows_b,3):.1%} | "
                 f"{top_hit(rows_b,3)-top_hit(rows_a,3):+.1%} | — |\n\n")

    lines.append("## 3. 校准桶对比\n")
    lines.append(f"| 档位 | A pred | A actual | A gap | B pred | B actual | B gap |\n")
    lines.append(f"|---|---|---|---|---|---|---|\n")
    bin_map_a = {b["range"]: b for b in bins_a}
    bin_map_b = {b["range"]: b for b in bins_b}
    all_ranges = sorted(set(list(bin_map_a.keys()) + list(bin_map_b.keys())))
    for rng in all_ranges:
        ba = bin_map_a.get(rng, {})
        bb = bin_map_b.get(rng, {})
        lines.append(f"| {rng} | n={ba.get('n','-')} {ba.get('avg_pred','-')} | "
                     f"{ba.get('actual_rate','-')} | {ba.get('gap','-')} | "
                     f"n={bb.get('n','-')} {bb.get('avg_pred','-')} | "
                     f"{bb.get('actual_rate','-')} | {bb.get('gap','-')} |\n")
    lines.append("")

    lines.append("## 4. 结论\n")
    rps_improved = agg_b.get('rps_mean', 0) < agg_a.get('rps_mean', 0)
    bri_improved = agg_b.get('brier_mean', 0) < agg_a.get('brier_mean', 0)
    ece_improved = ece_b < ece_a
    gap_improved = gap_hi_b < gap_hi_a if hi_a and hi_b else True
    over25_lowered = sum(over25_b) / len(over25_b) < sum(over25_a) / len(over25_a)

    lines.append(f"- RPS {'✅改善' if rps_improved else '❌恶化'}: "
                 f"{agg_a.get('rps_mean',0):.4f}→{agg_b.get('rps_mean',0):.4f} "
                 f"(p={p_rps:.3f}{' 显著' if p_rps<0.05 else ' 不显著'})\n")
    lines.append(f"- Brier {'✅改善' if bri_improved else '❌恶化'}: "
                 f"{agg_a.get('brier_mean',0):.4f}→{agg_b.get('brier_mean',0):.4f} "
                 f"(p={p_bri:.3f}{' 显著' if p_bri<0.05 else ' 不显著'})\n")
    lines.append(f"- ECE {'✅改善' if ece_improved else '❌恶化'}: {ece_a:.4f}→{ece_b:.4f}\n")
    lines.append(f"- 高桶 gap {'✅收敛' if gap_improved else '❌未收敛'}: "
                 f"{gap_hi_a:.3f}→{gap_hi_b:.3f}\n")
    lines.append(f"- over25 {'✅下调' if over25_lowered else '❌上调'}: "
                 f"{sum(over25_a)/len(over25_a):.4f}→{sum(over25_b)/len(over25_b):.4f}\n\n")

    all_good = rps_improved and bri_improved and ece_improved and gap_improved
    sig = p_rps < 0.05 or p_bri < 0.05
    verdict = "WEIGHT_EFFECTIVE" if (all_good and sig) else \
              "WEIGHT_PROMISING" if all_good else \
              "WEIGHT_INEFFECTIVE"
    lines.append(f"**判定: {verdict}**\n")
    lines.append(f"- 唯一变量: 融合权重 W_ODDS 0.15→{w_odds_b:.2f}（Poisson {1-0.15:.2f}→{1-w_odds_b:.2f}）\n")
    lines.append(f"- 去水方法固定 simple（排除 Shin 交叉影响）\n")
    lines.append(f"- over25 下调幅度 {sum(over25_b)/len(over25_b)-sum(over25_a)/len(over25_a):+.4f} "
                 f"vs C-038 Shin 仅 {0.6370-0.6376:+.4f}——权重提升效果应显著大于去水改善\n")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TG 融合权重 A/B 实验")
    parser.add_argument("--w-odds", type=float, default=0.25,
                        help="实验组赔率权重（默认 0.25）")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    results = run_eval(conn, args.w_odds)
    if not results:
        print("ERROR: 无可评测样本")
        return

    report_text = report(results, args.w_odds)
    ts = time.strftime("%H%M%S")
    md_path = REPORT_DIR / f"tg_weight_eval_{ts}.md"
    json_path = REPORT_DIR / f"tg_weight_eval_{ts}.json"
    md_path.write_text(report_text, encoding="utf-8")
    json_path.write_text(json.dumps({
        "n": len(results),
        "w_odds_b": args.w_odds,
        "a": [r["a"] for r in results],
        "b": [r["b"] for r in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(report_text)
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")
    conn.close()


if __name__ == "__main__":
    main()
