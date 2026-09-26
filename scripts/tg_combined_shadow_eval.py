# -*- coding: utf-8 -*-
"""C-20260921-041: TG 联合实验（λ 校准 + Shin 去水 + 0.30 权重）。

三变量组合 A/B 评测 + 贡献度分解（8 组对比）：
  A  = 生产基线 (factor=1.0, simple, w=0.15)
  B1 = factor only (C-036)
  B2 = Shin only (C-038)
  B3 = w_odds=0.30 only (C-039/040)
  B4 = factor + w_odds=0.30
  B5 = Shin + w_odds=0.30
  B6 = factor + Shin (w=0.15)
  B7 = factor + Shin + w_odds=0.30 (full combined)

CLI:
    python scripts/tg_combined_shadow_eval.py
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
from tg_shin_devig import shin_devig, simple_devig, compute_vig  # noqa: E402
from tg_lambda_calibrator import get_calib_factor_hierarchical  # noqa: E402

DB_PATH = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"


def _fuse(poisson_dist: Dict[int, float], goals: Dict[str, float],
          devig: str = "simple", w_odds: float = 0.15) -> Dict[int, float]:
    """融合 + 再归一化，去水方法和权重可调。"""
    if devig == "shin":
        odds_probs_dict, _ = shin_devig(goals)
    else:
        odds_probs_dict = simple_devig(goals)

    odds_probs: Dict[int, float] = {}
    for k, v in odds_probs_dict.items():
        key = int(k.replace("+", "")) if k.replace("+", "").isdigit() else 7
        odds_probs[key] = v

    fused: Dict[int, float] = {}
    for g in range(8):
        p_poisson = poisson_dist.get(g, 0.0)
        p_odds = odds_probs.get(g, 0.0)
        if g == 7:
            p_poisson = sum(poisson_dist.get(gg, 0.0) for gg in range(7, 15))
        fused[g] = p_poisson * (1.0 - w_odds) + p_odds * w_odds

    ft = sum(fused.values())
    if ft > 0:
        for g in fused:
            fused[g] /= ft
    return fused


def replay(sample: dict, goals: Dict[str, float], conn: sqlite3.Connection,
           use_factor: bool = False, devig: str = "simple",
           w_odds: float = 0.15) -> Optional[dict]:
    """单场重放。"""
    lh, la = sample["lambda_home"], sample["lambda_away"]

    if use_factor:
        factor, _ = get_calib_factor_hierarchical(
            sample["match_date"], sample.get("league"), conn=conn)
        lh = lh * factor
        la = la * factor

    poisson_dist = _poisson_total_goals(lh, la)
    fused = _fuse(poisson_dist, goals, devig, w_odds)

    dist_str = {str(g): fused.get(g, 0.0) for g in range(8)}
    over25 = sum(fused.get(g, 0.0) for g in range(3, 8))

    return {
        "match_id": sample["match_id"],
        "match_date": sample["match_date"],
        "league": sample["league"],
        "lambda_total": sample["lambda_home"] + sample["lambda_away"],
        "actual_tg": sample["actual_tg"],
        "dist_8": dist_str,
        "over25_prob": over25,
        "status": "ok",
    }


# 实验组定义
GROUPS = [
    ("A",  dict(use_factor=False, devig="simple", w_odds=0.15)),
    ("B1", dict(use_factor=True,  devig="simple", w_odds=0.15)),
    ("B2", dict(use_factor=False, devig="shin",   w_odds=0.15)),
    ("B3", dict(use_factor=False, devig="simple", w_odds=0.30)),
    ("B4", dict(use_factor=True,  devig="simple", w_odds=0.30)),
    ("B5", dict(use_factor=False, devig="shin",   w_odds=0.30)),
    ("B6", dict(use_factor=True,  devig="shin",   w_odds=0.15)),
    ("B7", dict(use_factor=True,  devig="shin",   w_odds=0.30)),
]

GROUP_DESC = {
    "A":  "生产基线 (factor=1.0, simple, w=0.15)",
    "B1": "factor only (C-036)",
    "B2": "Shin only (C-038)",
    "B3": "w_odds=0.30 only (C-039/040)",
    "B4": "factor + w_odds=0.30",
    "B5": "Shin + w_odds=0.30",
    "B6": "factor + Shin (w=0.15)",
    "B7": "factor + Shin + w_odds=0.30 (FULL)",
}


def run_eval(conn: sqlite3.Connection) -> dict:
    samples = load_replay_samples(conn)
    results = {}  # group_name -> list of rows

    for s in samples:
        goals = load_latest_tg_goals(conn, s["home_team"], s["away_team"], s["match_date"])
        if not goals:
            continue
        for gname, cfg in GROUPS:
            row = replay(s, goals, conn, **cfg)
            if row:
                results.setdefault(gname, []).append(row)

    return results


def report(results: dict) -> str:
    lines = []
    lines.append("# TG 联合实验报告（λ 校准 + Shin 去水 + 0.30 权重）\n")
    lines.append(f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"变更号: C-20260921-041\n\n")

    n = len(results.get("A", []))
    lines.append(f"## 1. 样本概况\n\n- 总样本: {n} 场\n\n")

    # 全集指标对比
    lines.append("## 2. 全集指标对比\n\n")
    lines.append("| 组 | 描述 | RPS | Brier | ECE | 高桶gap | over25 | Top1 | Top3 |\n")
    lines.append("|---|---|---|---|---|---|---|---|---|\n")

    agg_cache = {}
    ece_cache = {}
    for gname, _ in GROUPS:
        rows = results.get(gname, [])
        if not rows:
            continue
        agg = aggregate(rows)
        agg_cache[gname] = agg
        ece_val, _ = ece_over25(rows)
        ece_cache[gname] = ece_val

        hi = [r for r in rows if r["over25_prob"] >= 0.8]
        gap_hi = (sum(r["over25_prob"] for r in hi) / len(hi)
                  - sum(1 for r in hi if r["actual_tg"] >= 3) / len(hi)) if hi else 0

        over25_mean = sum(r["over25_prob"] for r in rows) / len(rows)

        def top_hit(k):
            hits = 0
            for r in rows:
                d = r["dist_8"]
                items = sorted(d.items(), key=lambda x: -x[1])
                top_k = set(int(x[0]) for x in items[:k])
                if min(r["actual_tg"], 7) in top_k:
                    hits += 1
            return hits / len(rows)

        lines.append(f"| {gname} | {GROUP_DESC[gname]} | "
                     f"{agg.get('rps_mean',0):.4f} | {agg.get('brier_mean',0):.4f} | "
                     f"{ece_val:.4f} | {gap_hi:.3f} | {over25_mean:.4f} | "
                     f"{top_hit(1):.1%} | {top_hit(3):.1%} |\n")
    lines.append("")

    # 配对检验（A vs each B）
    lines.append("## 3. 配对显著性检验（A vs 各组）\n\n")
    lines.append("| 对比 | RPS Δ | p | Brier Δ | p |\n")
    lines.append("|---|---|---|---|---|\n")
    rows_a = results.get("A", [])
    for gname, _ in GROUPS[1:]:
        rows_b = results.get(gname, [])
        if not rows_b:
            continue
        rps_d = [rps_8bucket(r["dist_8"], r["actual_tg"])
                 - rps_8bucket(rows_a[i]["dist_8"], rows_a[i]["actual_tg"])
                 for i, r in enumerate(rows_b)]
        bri_d = [brier_over25(r["over25_prob"], r["actual_tg"])
                 - brier_over25(rows_a[i]["over25_prob"], rows_a[i]["actual_tg"])
                 for i, r in enumerate(rows_b)]
        _, _, p_rps = paired_t_test(rps_d)
        _, _, p_bri = paired_t_test(bri_d)
        rps_delta = agg_cache[gname].get('rps_mean', 0) - agg_cache['A'].get('rps_mean', 0)
        bri_delta = agg_cache[gname].get('brier_mean', 0) - agg_cache['A'].get('brier_mean', 0)
        sig_rps = " *" if p_rps < 0.05 else ""
        sig_bri = " *" if p_bri < 0.05 else ""
        lines.append(f"| A vs {gname} | {rps_delta:+.4f} | {p_rps:.3f}{sig_rps} | "
                     f"{bri_delta:+.4f} | {p_bri:.3f}{sig_bri} |\n")
    lines.append("")

    # 贡献度分解
    lines.append("## 4. 贡献度分解\n\n")
    lines.append("| 组合 | vs A RPS Δ | vs A Brier Δ | vs A 高桶gap Δ | 增量来源 |\n")
    lines.append("|---|---|---|---|---|\n")

    def delta(g, metric):
        if g not in agg_cache or "A" not in agg_cache:
            return 0
        return agg_cache[g].get(metric, 0) - agg_cache["A"].get(metric, 0)

    # B1 vs B4 (factor 增量在 w=0.30 上的效果)
    lines.append(f"| B1(factor) | {delta('B1','rps_mean'):+.4f} | "
                 f"{delta('B1','brier_mean'):+.4f} | — | factor 单独 |\n")
    lines.append(f"| B3(w=0.30) | {delta('B3','rps_mean'):+.4f} | "
                 f"{delta('B3','brier_mean'):+.4f} | — | 权重单独 |\n")
    lines.append(f"| B4(factor+w=0.30) | {delta('B4','rps_mean'):+.4f} | "
                 f"{delta('B4','brier_mean'):+.4f} | — | factor+权重 |\n")
    lines.append(f"| B5(Shin+w=0.30) | {delta('B5','rps_mean'):+.4f} | "
                 f"{delta('B5','brier_mean'):+.4f} | — | Shin+权重 |\n")
    lines.append(f"| **B7(FULL)** | {delta('B7','rps_mean'):+.4f} | "
                 f"{delta('B7','brier_mean'):+.4f} | — | **三合一** |\n")
    lines.append("")

    # B3 vs B7 增量（Shin+factor 在 w=0.30 上的边际贡献）
    if "B7" in agg_cache and "B3" in agg_cache:
        rps_gain = delta("B7", "rps_mean") - delta("B3", "rps_mean")
        bri_gain = delta("B7", "brier_mean") - delta("B3", "brier_mean")
        lines.append(f"**边际贡献**（B7 vs B3：factor+Shin 在 w=0.30 上的增量）\n")
        lines.append(f"- RPS 边际: {rps_gain:+.4f}\n")
        lines.append(f"- Brier 边际: {bri_gain:+.4f}\n\n")

    # 结论
    lines.append("## 5. 结论\n\n")
    best = min(agg_cache.keys(), key=lambda g: agg_cache[g].get("rps_mean", 1))
    rps_b7 = agg_cache.get("B7", {}).get("rps_mean", 0)
    rps_a = agg_cache.get("A", {}).get("rps_mean", 0)
    rps_b3 = agg_cache.get("B3", {}).get("rps_mean", 0)

    lines.append(f"- 最优组: **{best}** ({GROUP_DESC.get(best, '')})\n")
    lines.append(f"- B7(FULL) vs A: RPS {rps_a:.4f}→{rps_b7:.4f} (Δ={rps_b7-rps_a:+.4f})\n")
    lines.append(f"- B7(FULL) vs B3(w=0.30): RPS {rps_b3:.4f}→{rps_b7:.4f} (Δ={rps_b7-rps_b3:+.4f})\n")

    if rps_b7 < rps_b3:
        lines.append(f"- 联合效果 **优于** 单独 w=0.30：factor+Shin 有边际贡献\n")
    else:
        lines.append(f"- 联合效果 **不优于** 单独 w=0.30：factor+Shin 边际贡献可忽略\n")

    # 判定
    b7_bri = agg_cache.get("B7", {}).get("brier_mean", 1)
    lines.append(f"- B7 Brier={b7_bri:.4f} {'✅优于0.250' if b7_bri < 0.250 else '❌仍劣于0.250'}\n")

    verdict = "COMBINED_EFFECTIVE" if (rps_b7 < rps_b3 and rps_b7 < rps_a) else \
              "WEIGHT_DOMINANT" if rps_b7 >= rps_b3 else "INEFFECTIVE"
    lines.append(f"\n**判定: {verdict}**\n")
    lines.append(f"- 若 WEIGHT_DOMINANT：0.30 权重已捕获大部分增益，factor+Shin 边际可忽略\n")
    lines.append(f"- 若 COMBINED_EFFECTIVE：三者联合有协同效应，值得 Shadow 接入联合版\n")

    return "\n".join(lines)


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    results = run_eval(conn)
    if not results.get("A"):
        print("ERROR: 无可评测样本")
        return

    report_text = report(results)
    ts = time.strftime("%H%M%S")
    md_path = REPORT_DIR / f"tg_combined_eval_{ts}.md"
    json_path = REPORT_DIR / f"tg_combined_eval_{ts}.json"
    md_path.write_text(report_text, encoding="utf-8")
    json_path.write_text(json.dumps({
        gname: rows for gname, rows in results.items()
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(report_text)
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")
    conn.close()


if __name__ == "__main__":
    main()
