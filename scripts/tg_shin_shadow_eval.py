# -*- coding: utf-8 -*-
"""C-20260921-038: TG 赔率 Shin 去水 A/B 拆解实验。

在历史配对样本上对比 A（简单 1/赔率归一化）vs B（Shin 去水）的预测质量。
唯一变量：TG 赔率去水方法（simple multiplicative vs Shin insider model）。
其余完全一致：λ 引擎、0.85/0.15 融合权重、归一化、数据源、采样时间。

CLI:
    python scripts/tg_shin_shadow_eval.py [--league 英超]
    python scripts/tg_shin_shadow_eval.py --no-pooled
"""

from __future__ import annotations
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from tg_shin_devig import shin_devig, simple_devig, compute_vig  # noqa: E402
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
    W_POISSON,
    W_ODDS,
)

DB_PATH = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"


# ============================================================
# 融合（single-variable: 去水方法）
# ============================================================
def _fuse_with_devig(tg_lambda_dist: Dict[int, float],
                     goals: Dict[str, float],
                     devig_method: str = "simple") -> Tuple[Dict[int, float], float]:
    """0.85/0.15 融合 + 再归一化。

    Args:
        tg_lambda_dist: 8 档 Poisson/DC 分布
        goals: TG 赔率快照 {label: odds}
        devig_method: "simple"（1/赔率归一化）或 "shin"（Shin 去水）

    Returns:
        (fused_dist, z_or_zero): z 为 Shin insider rate（simple 时 0.0）
    """
    if devig_method == "shin":
        odds_probs_dict, z = shin_devig(goals)
    else:
        odds_probs_dict = simple_devig(goals)
        z = 0.0

    # 转换为 int key
    odds_probs: Dict[int, float] = {}
    for k, v in odds_probs_dict.items():
        key = int(k.replace("+", "")) if k.replace("+", "").isdigit() else 7
        odds_probs[key] = v

    fused: Dict[int, float] = {}
    for g in range(8):
        p_poisson = tg_lambda_dist.get(g, 0.0)
        p_odds = odds_probs.get(g, 0.0)
        fused[g] = p_poisson * W_POISSON + p_odds * W_ODDS

    ft = sum(fused.values())
    if ft > 0:
        for g in fused:
            fused[g] /= ft
    return fused, z


def replay_match(sample: dict, goals: Dict[str, float],
                 devig_method: str = "simple") -> Optional[dict]:
    """单场重放，返回 8 档分布 + over25 + 指标。"""
    lh = sample["lambda_home"]
    la = sample["lambda_away"]
    poisson_dist = _poisson_total_goals(lh, la)

    fused, z = _fuse_with_devig(poisson_dist, goals, devig_method)

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
        "shin_z": z,
        "status": "ok",
    }


def run_eval(conn: sqlite3.Connection, league: Optional[str] = None) -> List[dict]:
    """A/B 双跑所有样本。"""
    samples = load_replay_samples(conn, league)
    results = []
    for s in samples:
        goals = load_latest_tg_goals(conn, s["home_team"], s["away_team"], s["match_date"])
        if not goals:
            continue  # 无 TG 赔率快照，跳过
        row_a = replay_match(s, goals, "simple")
        row_b = replay_match(s, goals, "shin")
        if row_a and row_b:
            results.append({"a": row_a, "b": row_b, "sample": s, "goals": goals})
    return results


def report(results: list) -> str:
    """生成评测报告。"""
    rows_a = [r["a"] for r in results]
    rows_b = [r["b"] for r in results]

    agg_a = aggregate(rows_a)
    agg_b = aggregate(rows_b)

    # 配对检验
    rps_deltas = [rps_8bucket(r["b"]["dist_8"], r["b"]["actual_tg"])
                  - rps_8bucket(r["a"]["dist_8"], r["a"]["actual_tg"]) for r in results]
    bri_deltas = [brier_over25(r["b"]["over25_prob"], r["b"]["actual_tg"])
                  - brier_over25(r["a"]["over25_prob"], r["a"]["actual_tg"]) for r in results]

    t_rps, df_rps, p_rps = paired_t_test(rps_deltas)
    t_bri, df_bri, p_bri = paired_t_test(bri_deltas)

    # ECE
    ece_a, bins_a = ece_over25(rows_a)
    ece_b, bins_b = ece_over25(rows_b)

    # 高桶 gap (>=0.8)
    hi_a = [r for r in rows_a if r["over25_prob"] >= 0.8]
    hi_b = [r for r in rows_b if r["over25_prob"] >= 0.8]
    gap_hi_a = (sum(r["over25_prob"] for r in hi_a) / len(hi_a)
                - sum(1 for r in hi_a if r["actual_tg"] >= 3) / len(hi_a)) if hi_a else 0
    gap_hi_b = (sum(r["over25_prob"] for r in hi_b) / len(hi_b)
                - sum(1 for r in hi_b if r["actual_tg"] >= 3) / len(hi_b)) if hi_b else 0

    # vig & z 统计
    vigs = [r["a"]["vig"] for r in results]
    zs = [r["b"]["shin_z"] for r in results]

    # over25 对比
    over25_a = [r["a"]["over25_prob"] for r in results]
    over25_b = [r["b"]["over25_prob"] for r in results]

    # 校准桶
    calib_a = [(r["over25_prob"], r["actual_tg"]) for r in rows_a]
    calib_b = [(r["over25_prob"], r["actual_tg"]) for r in rows_b]

    lines = []
    lines.append("# TG Shin 去水 A/B 拆解实验报告\n")
    lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"变更号: C-20260921-038\n\n")

    lines.append("## 1. 样本概况\n")
    lines.append(f"- 总样本: {len(results)} 场\n")
    lines.append(f"- 时间范围: {results[0]['a']['match_date']} ~ {results[-1]['a']['match_date']}\n")
    leagues = {}
    for r in results:
        lg = r["a"]["league"]
        leagues[lg] = leagues.get(lg, 0) + 1
    lines.append(f"- 联赛分布: {leagues}\n")
    lines.append(f"- vig 均值: {sum(vigs)/len(vigs):.1%} (min={min(vigs):.1%} max={max(vigs):.1%})\n")
    lines.append(f"- Shin z 均值: {sum(zs)/len(zs):.4f} (min={min(zs):.3f} max={max(zs):.3f})\n\n")

    lines.append("## 2. 全集指标对比\n")
    lines.append(f"| 指标 | A (simple) | B (shin) | Δ | 显著性 |\n")
    lines.append(f"|---|---|---|---|---|\n")
    lines.append(f"| RPS | {agg_a.get('rps_mean',0):.4f} | {agg_b.get('rps_mean',0):.4f} | "
                 f"{agg_b.get('rps_mean',0)-agg_a.get('rps_mean',0):+.4f} | "
                 f"t={t_rps:.2f} p={p_rps:.3f} |\n")
    lines.append(f"| Brier | {agg_a.get('brier_mean',0):.4f} | {agg_b.get('brier_mean',0):.4f} | "
                 f"{agg_b.get('brier_mean',0)-agg_a.get('brier_mean',0):+.4f} | "
                 f"t={t_bri:.2f} p={p_bri:.3f} |\n")
    lines.append(f"| ECE | {ece_a:.4f} | {ece_b:.4f} | {ece_b-ece_a:+.4f} | — |\n")
    lines.append(f"| ≥0.8 高桶 gap | {gap_hi_a:.3f} | {gap_hi_b:.3f} | {gap_hi_b-gap_hi_a:+.3f} | "
                 f"(n={len(hi_a)}/{len(hi_b)}) |\n")
    lines.append(f"| over25 均值 | {sum(over25_a)/len(over25_a):.4f} | "
                 f"{sum(over25_b)/len(over25_b):.4f} | "
                 f"{sum(over25_b)/len(over25_b)-sum(over25_a)/len(over25_a):+.4f} | — |\n")
    lines.append(f"| LogLoss | {agg_a.get('logloss_mean',0):.4f} | {agg_b.get('logloss_mean',0):.4f} | "
                 f"{agg_b.get('logloss_mean',0)-agg_a.get('logloss_mean',0):+.4f} | — |\n\n")

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
                 f"(p={p_rps:.3f}{'显著' if p_rps<0.05 else '不显著'})\n")
    lines.append(f"- Brier {'✅改善' if bri_improved else '❌恶化'}: "
                 f"{agg_a.get('brier_mean',0):.4f}→{agg_b.get('brier_mean',0):.4f}\n")
    lines.append(f"- ECE {'✅改善' if ece_improved else '❌恶化'}: {ece_a:.4f}→{ece_b:.4f}\n")
    lines.append(f"- 高桶 gap {'✅收敛' if gap_improved else '❌未收敛'}: "
                 f"{gap_hi_a:.3f}→{gap_hi_b:.3f}\n")
    lines.append(f"- over25 {'✅下调' if over25_lowered else '❌上调'}: "
                 f"{sum(over25_a)/len(over25_a):.4f}→{sum(over25_b)/len(over25_b):.4f}\n\n")

    verdict = "SHIN_DEVIG_{}".format(
        "EFFECTIVE" if (rps_improved and bri_improved and ece_improved) else
        "PARTIAL" if (bri_improved or ece_improved or over25_lowered) else "INEFFECTIVE")
    lines.append(f"**判定: {verdict}**\n")
    lines.append(f"- 唯一变量: TG 赔率去水方法 (simple 1/odds → Shin insider model)\n")
    lines.append(f"- Shin z 均值 {sum(zs)/len(zs):.4f} 表示市场存在 "
                 f"{'轻度' if sum(zs)/len(zs)<0.05 else '中度' if sum(zs)/len(zs)<0.1 else '重度'} "
                 f"favorite-longshot bias\n")
    lines.append(f"- 融合权重 Poisson 85% / Odds 15%，Shin 影响幅度受 15% 权重限制\n")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="TG Shin 去水 A/B 拆解实验")
    parser.add_argument("--league", default=None, help="限定联赛")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    results = run_eval(conn, args.league)
    if not results:
        print("ERROR: 无可评测样本")
        return

    report_text = report(results)
    ts = time.strftime("%H%M%S")
    md_path = REPORT_DIR / f"tg_shin_devig_eval_{ts}.md"
    json_path = REPORT_DIR / f"tg_shin_devig_eval_{ts}.json"
    md_path.write_text(report_text, encoding="utf-8")
    json_path.write_text(json.dumps({
        "n": len(results),
        "a": [r["a"] for r in results],
        "b": [r["b"] for r in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(report_text)
    print(f"\n报告已写入: {md_path}")
    print(f"数据已写入: {json_path}")
    conn.close()


if __name__ == "__main__":
    main()
