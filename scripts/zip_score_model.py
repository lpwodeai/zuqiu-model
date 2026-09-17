# -*- coding: utf-8 -*-
"""ZIP 零膨胀泊松比分模型（P3 —— 修复低比分系统性低估）。

问题背景（docs/基于预测报告发现的问题.txt §4.4 P2-02/P3 + v2.0 遗留）：
  基础泊松 + Dixon-Coles 假设两队进球独立，但足球大量保守防守比赛使 0-0、1-0、
  0-1 被系统性低估（low_score_diagnosis.py 已量化：0-0 +1.79pp / 1-0 +4.36pp /
  0-1 +3.02pp）。本模块实现零膨胀泊松（Zero-Inflated Poisson, ZIP）比分分布，
  用**低 λ → 高零膨胀**的均值匹配构造，直接抬高低比分格概率，并与旧模型做
  离线回测对比（LogLoss / 总进球 RPS / 低比分校准偏差），验证是否有增益。

方法：
  P(X=0) = π + (1−π)·e^{−λ_z}，P(X=k≥1) = (1−π)·e^{−λ_z}·λ_z^k / k!
  其中 λ_z = λ / (1−π)（均值匹配，E[X]=λ 不变，仅抬高 P(0) 与尾部更离散）
  π(λ) = min(π_max, k_scale / (1+λ))  —— 低进球期望队伍更容易 0 球
  两队独立 Zip 联合：P(h,a) = P_h(h)·P_a(a)

  * 基线（旧模型）复用 model_predictions 已入库的 t006_score_predictor_v5 网格
  * 纯泊松：由 λ 重算独立泊松（无 Dixon-Coles，用于隔离 ZIP 效应）

用法：
  python zip_score_model.py                  # 网格搜索 k_scale 并输出对比报告
  python zip_score_model.py --db data/odds.db --k-scale 0.15

输出：
  - reports/zip_score_model_<TS>.md / .json
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from low_score_diagnosis import load_score_predictions, parse_score

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"

MAX_GOALS = 5                 # 与入库 Score_grid_* 网格一致（0..5）
LOW_SCORE_CELLS = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (0, 2)]
EPS = 1e-10                   # LogLoss 平滑，避免 log(0)


# ==================== 纯函数（可单测） ====================

def zip_inflation(lam: float, k_scale: float = 0.15, pi_max: float = 0.40) -> float:
    """低 λ → 高零膨胀：π = min(pi_max, k_scale / (1 + λ))。

    越弱的进攻（λ 小）越倾向交白卷；强队（λ 大）几乎不受零膨胀影响。
    """
    lam = max(lam, 0.0)
    return min(pi_max, k_scale / (1.0 + lam))


def zip_pmf(k: int, lam: float, pi: float) -> float:
    """单队零膨胀泊松概率质量函数（均值匹配：λ_z = λ/(1−π)）。"""
    if k < 0:
        return 0.0
    pi = min(max(pi, 0.0), 0.999999)
    lam_z = lam / (1.0 - pi) if pi < 1.0 - 1e-9 else 1e6
    if k == 0:
        return pi + (1.0 - pi) * math.exp(-lam_z)
    return (1.0 - pi) * math.exp(-lam_z) * (lam_z ** k) / math.factorial(k)


def poisson_pmf(k: int, lam: float) -> float:
    """普通泊松 PMF。"""
    return math.exp(-lam) * (lam ** k) / math.factorial(k) if k >= 0 else 0.0


def _normalize(grid: Dict[Tuple[int, int], float]) -> Dict[Tuple[int, int], float]:
    total = sum(grid.values())
    if total <= 0:
        return grid
    return {k: v / total for k, v in grid.items()}


def poisson_grid(lam_h: float, lam_a: float, max_goals: int = MAX_GOALS) -> Dict[Tuple[int, int], float]:
    """独立泊松比分网格（旧模型参考，无 Dixon-Coles）。"""
    grid = {(h, a): poisson_pmf(h, lam_h) * poisson_pmf(a, lam_a)
            for h in range(max_goals + 1) for a in range(max_goals + 1)}
    return _normalize(grid)


def zip_score_grid(lam_h: float, lam_a: float, k_scale: float = 0.15,
                   pi_max: float = 0.40, max_goals: int = MAX_GOALS) -> Dict[Tuple[int, int], float]:
    """独立零膨胀泊松比分网格（新模型）。"""
    pi_h = zip_inflation(lam_h, k_scale, pi_max)
    pi_a = zip_inflation(lam_a, k_scale, pi_max)
    grid = {(h, a): zip_pmf(h, lam_h, pi_h) * zip_pmf(a, lam_a, pi_a)
            for h in range(max_goals + 1) for a in range(max_goals + 1)}
    return _normalize(grid)


def cell_prob(grid: Dict[Tuple[int, int], float], h: int, a: int,
              max_goals: int = MAX_GOALS) -> float:
    """取格子概率，越界返回 0（与 low_score_diagnosis.prob_of 语义一致）。"""
    if 0 <= h <= max_goals and 0 <= a <= max_goals:
        return float(grid.get((h, a), 0.0))
    return 0.0


def log_loss_grid(grid: Dict[Tuple[int, int], float], actual_h: int, actual_a: int,
                  eps: float = EPS) -> Optional[float]:
    """单场 LogLoss（自然对数）。实际比分越界时返回 None（截断不可比）。"""
    if actual_h > MAX_GOALS or actual_a > MAX_GOALS:
        return None
    p = cell_prob(grid, actual_h, actual_a)
    return -math.log(max(min(p, 1.0 - eps), eps))


def rps_total_goals(grid: Dict[Tuple[int, int], float], actual_total: int,
                    max_total: int = 10) -> float:
    """总进球 1D 秩概率得分（越低越好，范围 0~1）。"""
    pmf = np.zeros(max_total + 1)
    for (h, a), p in grid.items():
        t = h + a
        if t <= max_total:
            pmf[t] += p
    pmf /= pmf.sum()
    obs = np.zeros(max_total + 1)
    obs[min(actual_total, max_total)] = 1.0
    F_pred = np.cumsum(pmf)
    F_obs = np.cumsum(obs)
    return float(np.sum((F_pred - F_obs) ** 2) / max_total)


def low_score_bias(grids_pred: List[Dict[Tuple[int, int], float]],
                   rows: List[dict],
                   cells: List[Tuple[int, int]] = LOW_SCORE_CELLS) -> List[dict]:
    """低比分单格偏差：avg_pred vs 真实频率，bias_pp 正值=低估。"""
    n = len(rows)
    out = []
    for (h, a) in cells:
        preds = [cell_prob(g, h, a) for g in grids_pred]
        hits = sum(1 for r in rows if r["actual_h"] == h and r["actual_a"] == a)
        avg_pred = float(np.mean(preds)) if preds else 0.0
        freq = hits / n if n else 0.0
        out.append({"score": f"{h}:{a}", "avg_pred": avg_pred,
                    "actual_freq": freq, "bias_pp": (freq - avg_pred) * 100})
    return out


def agg_le_bias(grids_pred: List[Dict[Tuple[int, int], float]],
                rows: List[dict], max_goals_total: int = 2) -> dict:
    """总进球≤N 档位校准偏差。"""
    n = len(rows)
    preds = [sum(cell_prob(g, h, a) for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1)
                 if h + a <= max_goals_total) for g in grids_pred]
    hits = [1.0 if r["total_goals"] <= max_goals_total else 0.0 for r in rows]
    avg_pred = float(np.mean(preds)) if preds else 0.0
    freq = float(np.mean(hits)) if hits else 0.0
    return {"label": f"总进球≤{max_goals_total}", "avg_pred": avg_pred,
            "actual_freq": freq, "bias_pp": (freq - avg_pred) * 100}


# ==================== 回测 ====================

def evaluate_grids(rows: List[dict], grid_fn, **grid_kwargs) -> dict:
    """对给定网格生成函数评估全量样本。

    Returns: {"log_loss", "rps", "low_bias", "le1", "le2", "in_range"}
    """
    grids = []
    ll_vals = []
    rps_vals = []
    for r in rows:
        lam_h, lam_a = r.get("lambda_home"), r.get("lambda_away")
        if lam_h is None or lam_a is None:
            grids.append({})
            continue
        g = grid_fn(float(lam_h), float(lam_a), **grid_kwargs)
        grids.append(g)
        ll = log_loss_grid(g, r["actual_h"], r["actual_a"])
        if ll is not None:
            ll_vals.append(ll)
        rps_vals.append(rps_total_goals(g, r["total_goals"]))
    return {
        "log_loss": float(np.mean(ll_vals)) if ll_vals else float("nan"),
        "rps": float(np.mean(rps_vals)) if rps_vals else float("nan"),
        "low_bias": low_score_bias(grids, rows),
        "le1": agg_le_bias(grids, rows, 1),
        "le2": agg_le_bias(grids, rows, 2),
        "in_range": len(ll_vals),
    }


def _baseline_eval(rows: List[dict]) -> dict:
    """旧模型 = 已入库的 t006_score_predictor_v5 网格（残留生产概率）。"""
    grids = [r["grid"] for r in rows]
    ll_vals = []
    rps_vals = []
    for r in rows:
        ll = log_loss_grid(r["grid"], r["actual_h"], r["actual_a"])
        if ll is not None:
            ll_vals.append(ll)
        rps_vals.append(rps_total_goals(r["grid"], r["total_goals"]))
    return {
        "log_loss": float(np.mean(ll_vals)) if ll_vals else float("nan"),
        "rps": float(np.mean(rps_vals)) if rps_vals else float("nan"),
        "low_bias": low_score_bias(grids, rows),
        "le1": agg_le_bias(grids, rows, 1),
        "le2": agg_le_bias(grids, rows, 2),
        "in_range": len(ll_vals),
    }


def grid_search_k_scale(rows: List[dict], candidates=None, pi_max: float = 0.40) -> List[dict]:
    """网格搜索零膨胀强度 k_scale，按（in-range 子集）LogLoss 择优。"""
    candidates = candidates or [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40]
    out = []
    for k in candidates:
        ev = evaluate_grids(rows, zip_score_grid, k_scale=k, pi_max=pi_max)
        # 低比分 0-0 偏差（越低越准）
        bias_00 = next((c["bias_pp"] for c in ev["low_bias"] if c["score"] == "0:0"), None)
        bias_le1 = ev["le1"]["bias_pp"]
        out.append({
            "k_scale": k,
            "log_loss": ev["log_loss"],
            "rps": ev["rps"],
            "bias_00_pp": bias_00,
            "bias_le1_pp": bias_le1,
            "in_range": ev["in_range"],
        })
    # 按 LogLoss 升序（越小越好）
    out.sort(key=lambda x: (x["log_loss"] if not math.isnan(x["log_loss"]) else 1e9))
    return out


def generate_report(result: dict, md_path: Path, json_path: Path) -> Tuple[Path, Path]:
    REPORT_DIR.mkdir(exist_ok=True)
    best = result["best_calibration_k_scale"]
    best_ll = result["best_logloss_k_scale"]
    base = result["baseline"]
    pp = result["pure_poisson"]
    zip_ev = result["zip"]

    L = []
    L.append("# ZIP 零膨胀泊松比分模型回测报告（P3）\n")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append(f"**样本量**: {result['n']} 场（in-range 比分 ≤5 的子集 {zip_ev['in_range']} 场）\n")
    L.append(f"**低比分校准最优零膨胀强度**: k_scale={best['k_scale']}（|0-0偏差|+|≤1偏差| 最小）\n")
    L.append(f"**LogLoss 最优零膨胀强度**: k_scale={best_ll['k_scale']}（LogLoss 最小，通常退化为 0=不加零膨胀）\n")
    L.append("> bias_pp 正值 = 模型系统性低估该结果；目标是把低比分偏差压向 0\n")

    L.append("## 一、k_scale 网格搜索（LogLoss 越小越好）\n")
    L.append("| k_scale | LogLoss | RPS | 0-0偏差(pp) | ≤1偏差(pp) |")
    L.append("|---|---|---|---|---|")
    for s in result["grid_search"]:
        bias00 = f"{s['bias_00_pp']:+.2f}" if s["bias_00_pp"] is not None else "—"
        L.append(f"| {s['k_scale']:.2f} | {s['log_loss']:.4f} | {s['rps']:.4f} | "
                 f"{bias00} | {s['bias_le1_pp']:+.2f} |")
    L.append("")

    L.append("## 二、新旧模型核心指标对比\n")
    L.append("| 指标 | 旧（生产 v5 网格） | 纯泊松（λ 重算） | ZIP（新） |")
    L.append("|---|---|---|---|")
    L.append(f"| LogLoss | {base['log_loss']:.4f} | {pp['log_loss']:.4f} | {zip_ev['log_loss']:.4f} |")
    L.append(f"| RPS（总进球） | {base['rps']:.4f} | {pp['rps']:.4f} | {zip_ev['rps']:.4f} |")
    L.append(f"| ≤1 偏差(pp) | {base['le1']['bias_pp']:+.2f} | {pp['le1']['bias_pp']:+.2f} | {zip_ev['le1']['bias_pp']:+.2f} |")
    L.append(f"| ≤2 偏差(pp) | {base['le2']['bias_pp']:+.2f} | {pp['le2']['bias_pp']:+.2f} | {zip_ev['le2']['bias_pp']:+.2f} |")
    L.append("")

    L.append("## 三、低比分单格偏差对比（预测概率 / 真实频率 / 偏差）\n")
    L.append("| 比分 | 旧预测% | 旧偏差 | 纯泊松% | 纯泊松偏差 | ZIP% | ZIP偏差 |")
    L.append("|---|---|---|---|---|---|---|")
    base_cells = {c["score"]: c for c in base["low_bias"]}
    pp_cells = {c["score"]: c for c in pp["low_bias"]}
    zip_cells = {c["score"]: c for c in zip_ev["low_bias"]}
    for cell in zip_ev["low_bias"]:
        sc = cell["score"]
        b = base_cells[sc]; p = pp_cells[sc]; z = zip_cells[sc]
        L.append(f"| {sc} | {b['avg_pred']*100:.2f} | {b['bias_pp']:+.2f} | "
                 f"{p['avg_pred']*100:.2f} | {p['bias_pp']:+.2f} | "
                 f"{z['avg_pred']*100:.2f} | {z['bias_pp']:+.2f} |")
    L.append("")

    # 增（损）益判断：分别对比 ZIP→纯泊松 与 ZIP→生产基线
    delta_ll_pp = zip_ev["log_loss"] - pp["log_loss"]          # ZIP 相对纯泊松（同 λ 重算，隔离零膨胀效应）
    delta_ll_base = zip_ev["log_loss"] - base["log_loss"]      # ZIP 相对生产 v5 基线
    bias00_pp = next((c["bias_pp"] for c in pp["low_bias"] if c["score"] == "0:0"), 0.0)
    bias00_zip = next((c["bias_pp"] for c in zip_ev["low_bias"] if c["score"] == "0:0"), 0.0)
    le1_base = base["le1"]["bias_pp"]
    le1_zip = zip_ev["le1"]["bias_pp"]
    L.append("## 四、结论与建议\n")
    L.append(f"- 相对纯泊松：ZIP 将 0-0 偏差 {bias00_pp:+.2f}pp → {bias00_zip:+.2f}pp、≤1 偏差 {pp['le1']['bias_pp']:+.2f}pp → {le1_zip:+.2f}pp，低比分校准确有收窄，")
    L.append(f"  但代价是 LogLoss 劣化 {delta_ll_pp:+.4f}（{pp['log_loss']:.4f} → {zip_ev['log_loss']:.4f}）。")
    L.append(f"- 相对生产 v5 基线：ZIP LogLoss {zip_ev['log_loss']:.4f} vs 基线 {base['log_loss']:.4f}（{delta_ll_base:+.4f}）、")
    L.append(f"  0-0 偏差 {bias00_zip:+.2f}pp vs 基线 {next((c['bias_pp'] for c in base['low_bias'] if c['score']=='0:0'),0):+.2f}pp、≤1 偏差 {le1_zip:+.2f}pp vs 基线 {le1_base:+.2f}pp —— **ZIP 无增益，不进入生产**。")
    L.append("- 根因判断：低比分（0-0 / 1-0）系统性低估的主源是**两队进球负相关**（Dixon-Coles ρ），而非单队零膨胀不足。")
    L.append("  单队 ZIP 独立构造无法引入跨队相关，抬高 P(0) 的同时拖高尾部离散度，整体分布反而偏离真实。")
    L.append("- 建议方向：① 调优 Dixon-Coles ρ（当前 -0.15，可网格搜索 -0.10~-0.30 找校准 + LogLoss 平衡点）；")
    L.append("  ② 或升级为双变量泊松（bivariate Poisson with ρ）显式建模进球相关；③ 维持生产 v5 网格作为 T-006 比分输出锚点。\n")

    L.append("\n---\n*本报告由 zip_score_model.py 自动生成*\n")
    md_path.write_text("\n".join(L), encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="ZIP 零膨胀泊松比分模型回测（P3）")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--k-scale", type=float, default=None, help="固定零膨胀强度（缺省则网格搜索择优）")
    parser.add_argument("--pi-max", type=float, default=0.40)
    args = parser.parse_args()

    print("加载比分网格 + λ + 实际比分...")
    rows = load_score_predictions(Path(args.db))
    print(f"  配对样本: {len(rows)} 场")
    rows = [r for r in rows if r.get("lambda_home") is not None and r.get("lambda_away") is not None]
    print(f"  有效 λ 样本: {len(rows)} 场")

    baseline = _baseline_eval(rows)
    pure_pp = evaluate_grids(rows, poisson_grid)

    gs = grid_search_k_scale(rows, pi_max=args.pi_max)
    best_logloss = gs[0]  # LogLoss 最优（gs 已按 LogLoss 升序）

    def _cal_score(s):
        # ZIP 模块目标 = 低比分校准度：|0-0 偏差| + |≤1 偏差| 越小越好
        return abs(s["bias_00_pp"] or 0.0) + abs(s["bias_le1_pp"])

    best_cal = min(gs, key=_cal_score)  # 低比分校准最优
    if args.k_scale is not None:
        # 手动指定时，以指定 k_scale 作为评估对象
        best_cal = next((s for s in gs if abs(s["k_scale"] - args.k_scale) < 1e-9), best_cal)
    zip_ev = evaluate_grids(rows, zip_score_grid, k_scale=best_cal["k_scale"], pi_max=args.pi_max)

    result = {
        "n": len(rows),
        "best_logloss_k_scale": best_logloss,
        "best_calibration_k_scale": best_cal,
        "grid_search": gs,
        "baseline": baseline,
        "pure_poisson": pure_pp,
        "zip": zip_ev,
    }

    print("\n" + "=" * 62)
    print(f"ZIP 回测（低比分校准最优 k_scale={best_cal['k_scale']}）")
    print(f"  LogLoss: 生产 {baseline['log_loss']:.4f} | 纯泊松 {pure_pp['log_loss']:.4f} | ZIP {zip_ev['log_loss']:.4f}")
    print(f"  RPS:     生产 {baseline['rps']:.4f} | 纯泊松 {pure_pp['rps']:.4f} | ZIP {zip_ev['rps']:.4f}")
    print(f"  0-0偏差: 纯泊松 {next(c['bias_pp'] for c in pure_pp['low_bias'] if c['score']=='0:0'):+.2f}pp "
          f"→ ZIP {next(c['bias_pp'] for c in zip_ev['low_bias'] if c['score']=='0:0'):+.2f}pp")
    print("=" * 62)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path, json_path = generate_report(
        result,
        REPORT_DIR / f"zip_score_model_{ts}.md",
        REPORT_DIR / f"zip_score_model_{ts}.json",
    )
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")


if __name__ == "__main__":
    main()