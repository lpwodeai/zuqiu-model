# -*- coding: utf-8 -*-
"""
bayesian_incremental_ab.py — P1-B 阶段1 + 阶段2 对照门禁
====================================================================
背景（评估报告 v2.0 §P1-B + docs/bayesian_incremental_design.md）：
  阶段 1 打磨基模：对现状贝叶斯层级模型做超参网格标定，产出各联赛基线表。
  阶段 2 增量更新：攻击/防守后验「全量重训（冻结）」vs「增量更新（EKF）」在
  同一批「未来前瞻样本」上的四指标并行对照 + 门禁 + 可回滚判定。

对照协议（设计文档 §4）：
  - 严格时间序列切分（按 match_date 升序，前 train_frac 训练 / 后段测试），无随机 shuffle。
  - 两臂共享同一 burn-in 全量 MAP（μ/home_adv/ρ + 初始 attack/defense + Laplace std）。
  - Path A（基线）：冻结训练集拟合的攻击/防守，直接预测测试集。
  - Path B（增量）：以同一 burn-in 初始化 EKF，逐场「先预测后更新」吸收测试集赛果。
  - 指标：RPS / LogLoss / Acc / DrawRecall（positive=平局，召回口径）。
  - 门禁（设计文档 §4.2）：增量 vs 全量重训「不劣化」判据，n<60 标 insufficient-sample。

只读 odds.db，回写 reports/bayesian_incremental_ab_{ts}.json，不触碰预测/训练写入。

用法：
  python scripts/bayesian_incremental_ab.py --ab --league 英超            # 单联赛对照
  python scripts/bayesian_incremental_ab.py --ab                          # 全部 5 联赛
  python scripts/bayesian_incremental_ab.py --grid --league 英超          # 阶段1 基线网格
  python scripts/bayesian_incremental_ab.py --ab --league 英超 --tau-att 0.05 --tau-def 0.05
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bayesian_hierarchical_model import (  # noqa: E402
    BayesianHierarchicalModel,
    LEAGUES,
    load_match_data,
)
from bayesian_incremental import BayesianIncrementalFilter  # noqa: E402

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

# 门禁容差（设计文档 §4.2）
EPS_RPS = 0.002
EPS_LL = 0.01
EPS_ACC = 0.01
EPS_DR = 0.01
MIN_GATE_N = 60  # 未来样本显著性下限


# ============================================================
# 指标
# ============================================================
def compute_four_metrics(y_true: np.ndarray, y_proba: np.ndarray) -> Dict[str, float]:
    """四指标：RPS / LogLoss / Acc / DrawRecall。列序 [客胜,平局,主胜]=[0,1,2]。"""
    n = len(y_true)
    y_true = np.asarray(y_true, dtype=int)
    y_proba = np.asarray(y_proba, dtype=float)
    o = np.zeros((n, 3))
    o[np.arange(n), y_true] = 1.0
    rps = float(np.mean(np.sum((np.cumsum(y_proba, axis=1) - np.cumsum(o, axis=1)) ** 2, axis=1)) / 2)
    clip = np.clip(y_proba, 1e-7, 1.0)
    ll = float(-np.mean(np.sum(o * np.log(clip), axis=1)))
    pred = y_proba.argmax(axis=1)
    acc = float(np.mean(pred == y_true))
    draw_tp = int(((pred == 1) & (y_true == 1)).sum())
    draw_fn = int(((pred != 1) & (y_true == 1)).sum())
    dr = draw_tp / (draw_tp + draw_fn) if (draw_tp + draw_fn) else float("nan")
    return {"n": n, "rps": rps, "logloss": ll, "acc": acc, "draw_recall": dr}


# ============================================================
# 两臂评估（共享 burn-in 拟合）
# ============================================================
def evaluate_frozen(base: BayesianHierarchicalModel, test_df) -> Dict[str, float]:
    """Path A（基线）：冻结训练集攻击/防守，直接预测测试集。"""
    y_true, y_proba = [], []
    for _, row in test_df.iterrows():
        wdl = base.predict_wdl(row["home_team_name"], row["away_team_name"])
        y_true.append(int(row["result"]))
        y_proba.append([wdl["lose"], wdl["draw"], wdl["win"]])
    return compute_four_metrics(np.array(y_true), np.array(y_proba))


def evaluate_incremental(
    base: BayesianHierarchicalModel,
    test_df,
    tau_att: float,
    tau_def: float,
    max_goals: int,
) -> Dict[str, float]:
    """Path B（增量）：以 burn-in 初始化 EKF，逐场「先预测后更新」。"""
    flt = BayesianIncrementalFilter(
        base.mu, base.home_adv, base.rho,
        tau_att=tau_att, tau_def=tau_def, max_goals=max_goals,
    )
    flt.init_teams(base.teams, base.attack, base.defense, base.attack_std, base.defense_std)

    y_true, y_proba = [], []
    for _, row in test_df.iterrows():
        h, a = row["home_team_name"], row["away_team_name"]
        wdl = flt.predict_wdl(h, a)  # 预测必须先于更新（严格时序）
        y_true.append(int(row["result"]))
        y_proba.append([wdl["lose"], wdl["draw"], wdl["win"]])
        flt.update(h, a, int(row["homeGoals"]), int(row["awayGoals"]))
    return compute_four_metrics(np.array(y_true), np.array(y_proba))


# ============================================================
# 门禁
# ============================================================
def make_gate(base: Dict[str, float], incr: Dict[str, float]) -> Tuple[Dict[str, str], str]:
    """增量 vs 全量重训「不劣化」门禁。"""
    n = incr["n"]
    if n < MIN_GATE_N:
        g = {k: "insufficient-sample" for k in ("rps", "logloss", "acc", "draw_recall")}
        return g, f"insufficient-sample（n={n} < {MIN_GATE_N}，不强制门禁）"

    def _ok(m):
        return not (np.isnan(base[m]) or np.isnan(incr[m]))

    g = {
        "rps": "PASS" if (_ok("rps") and incr["rps"] <= base["rps"] + EPS_RPS) else "FAIL",
        "logloss": "PASS" if (_ok("logloss") and incr["logloss"] <= base["logloss"] + EPS_LL) else "FAIL",
        "acc": "PASS" if (_ok("acc") and incr["acc"] >= base["acc"] - EPS_ACC) else "FAIL",
        "draw_recall": "PASS" if (_ok("draw_recall") and incr["draw_recall"] >= base["draw_recall"] - EPS_DR) else "FAIL",
    }
    if _ok("draw_recall") is False or (np.isnan(base["draw_recall"]) or np.isnan(incr["draw_recall"])):
        g["draw_recall"] = "SKIP"
    all_pass = all(v == "PASS" for v in g.values())
    concl = "全部 PASS（增量不劣化，可进入 shadow 并行）" if all_pass else "存在 FAIL（增量劣化，维持全量重训）"
    return g, concl


# ============================================================
# 数据切分
# ============================================================
def split_league(df, league: str, train_frac: float):
    lg = df[df["competition_name"] == league].sort_values("date").reset_index(drop=True)
    split = int(len(lg) * train_frac)
    return lg, lg.iloc[:split], lg.iloc[split:]


# ============================================================
# 阶段 1：基模打磨网格
# ============================================================
def run_grid(df, league: str, train_frac: float, sigmas: List[float], half_lives: List[Optional[float]]) -> Dict:
    lg, train, test = split_league(df, league, train_frac)
    if len(train) < 50 or len(test) < 10:
        return {"league": league, "skipped": True, "reason": f"样本不足 train={len(train)} test={len(test)}"}
    rows = []
    for sig in sigmas:
        for hl in half_lives:
            m = BayesianHierarchicalModel(sigma_att=sig, sigma_def=sig, time_decay_half_life=hl)
            m.fit(train, league=league)
            mtr = evaluate_frozen(m, test)
            rows.append({
                "sigma_att": sig, "sigma_def": sig, "time_decay_half_life": hl,
                "rps": round(mtr["rps"], 4), "logloss": round(mtr["logloss"], 4),
                "acc": round(mtr["acc"], 4), "draw_recall": round(mtr["draw_recall"], 4),
            })
    best = min(rows, key=lambda r: r["rps"])
    print(f"[{league}] 阶段1 基线网格（{len(rows)} 组合，train={len(train)} test={len(test)}）：")
    print("   sigma  half_life   RPS     LogLoss  Acc     DrawRecall")
    for r in rows:
        hl = "none" if r["time_decay_half_life"] is None else r["time_decay_half_life"]
        print(f"   {r['sigma_att']:<5}  {hl:<9}  {r['rps']:<7} {r['logloss']:<8} {r['acc']:<7} {r['draw_recall']}")
    print(f"   → 最优（RPS）: sigma={best['sigma_att']} half_life={best['time_decay_half_life']} RPS={best['rps']}")
    return {"league": league, "train_n": len(train), "test_n": len(test),
            "grid": rows, "best": best}


# ============================================================
# 阶段 2：A/B 对照
# ============================================================
def run_ab(df, league: str, train_frac: float, tau_att: float, tau_def: float, max_goals: int) -> Dict:
    lg, train, test = split_league(df, league, train_frac)
    if len(train) < 50 or len(test) < 10:
        return {"league": league, "skipped": True, "reason": f"样本不足 train={len(train)} test={len(test)}"}

    base = BayesianHierarchicalModel()
    base.fit(train, league=league)

    base_m = evaluate_frozen(base, test)
    incr_m = evaluate_incremental(base, test, tau_att, tau_def, max_goals)
    gate, concl = make_gate(base_m, incr_m)

    print(f"[{league}] train={len(train)} test={len(test)} teams={len(base.teams)} "
          f"tau_att={tau_att} tau_def={tau_def}")
    print(f"   基线(全量重训): RPS={base_m['rps']:.4f} LogLoss={base_m['logloss']:.4f} "
          f"Acc={base_m['acc']:.4f} DrawRecall={base_m['draw_recall']:.4f}")
    print(f"   增量(EKF):      RPS={incr_m['rps']:.4f} LogLoss={incr_m['logloss']:.4f} "
          f"Acc={incr_m['acc']:.4f} DrawRecall={incr_m['draw_recall']:.4f}")
    print("   门禁:", " | ".join(f"{k}={v}" for k, v in gate.items()))
    print(f"   结论: {concl}")

    return {
        "league": league, "train_n": len(train), "test_n": len(test),
        "num_teams": len(base.teams), "tau_att": tau_att, "tau_def": tau_def,
        "burn_in": {"mu": round(base.mu, 4), "home_adv": round(base.home_adv, 4), "rho": round(base.rho, 4)},
        "full_retrain": {k: (round(v, 4) if not isinstance(v, int) else v) for k, v in base_m.items()},
        "incremental": {k: (round(v, 4) if not isinstance(v, int) else v) for k, v in incr_m.items()},
        "gate": gate, "conclusion": concl,
    }


# ============================================================
# 阶段 2b：τ 逐联赛标定（增量转移方差网格）
# ============================================================
def run_tau_scan(
    df, league: str, train_frac: float, tau_list: List[float], max_goals: int
) -> Dict:
    """对单联赛在 τ 候选集上扫描增量 EKF 的转移方差（tau_att=tau_def=τ）。

    固定同一 burn-in（fit 一次），对每个 τ 跑 evaluate_incremental，
    用 make_gate 对照基线判「不劣化」，选出门禁全通过中 RPS 最优的 τ；
    若无全通过，选 ΔRPS 最接近基线（delta_rps 最小）的 τ 并标注。
    """
    lg, train, test = split_league(df, league, train_frac)
    if len(train) < 50 or len(test) < 10:
        return {"league": league, "skipped": True,
                "reason": f"样本不足 train={len(train)} test={len(test)}"}

    base = BayesianHierarchicalModel()
    base.fit(train, league=league)
    base_m = evaluate_frozen(base, test)  # 冻结臂不依赖 τ，跑一次

    rows = []
    for tau in tau_list:
        incr_m = evaluate_incremental(base, test, tau, tau, max_goals)
        gate, _ = make_gate(base_m, incr_m)
        has_fail = any(v == "FAIL" for v in gate.values())
        rows.append({
            "tau": tau,
            "rps": round(incr_m["rps"], 4),
            "logloss": round(incr_m["logloss"], 4),
            "acc": round(incr_m["acc"], 4),
            "draw_recall": round(incr_m["draw_recall"], 4),
            "delta_rps": round(incr_m["rps"] - base_m["rps"], 5),
            "delta_acc": round(incr_m["acc"] - base_m["acc"], 5),
            "gate": gate,
            "all_pass": not has_fail,
        })

    passing = [r for r in rows if r["all_pass"]]
    if passing:
        best = min(passing, key=lambda r: r["delta_rps"])
        pick_key = "min_delta_rps_among_passing"
    else:
        best = min(rows, key=lambda r: r["delta_rps"])
        pick_key = "min_delta_rps"

    print(f"[{league}] τ 标定（train={len(train)} test={len(test)} teams={len(base.teams)}）：")
    print(f"   基线 RPS={base_m['rps']:.4f} Acc={base_m['acc']:.4f} LogLoss={base_m['logloss']:.4f}")
    print("   τ         RPS     ΔRPS     Acc     ΔAcc    LogLoss  DrawRec  全过")
    for r in rows:
        ok = "✓" if r["all_pass"] else "✗"
        print(f"   {r['tau']:<7}  {r['rps']:<7} {r['delta_rps']:<+8} {r['acc']:<7} "
              f"{r['delta_acc']:<+8} {r['logloss']:<8} {r['draw_recall']:<7} {ok}")
    note = "无全通过，取 ΔRPS 最小" if pick_key == "min_delta_rps" else "全通过中 ΔRPS 最小（即 RPS 最优）"
    print(f"   → 推荐 τ={best['tau']}（{note}）")
    return {
        "league": league, "train_n": len(train), "test_n": len(test),
        "num_teams": len(base.teams),
        "base": {"rps": round(base_m["rps"], 4), "logloss": round(base_m["logloss"], 4),
                 "acc": round(base_m["acc"], 4), "draw_recall": round(base_m["draw_recall"], 4)},
        "scan": rows, "best": best, "pick_key": pick_key, "recommended_tau": best["tau"],
    }


# ============================================================
# 主流程
# ============================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="P1-B 贝叶斯增量底座：阶段1 基线网格 + 阶段2 A/B 对照门禁")
    ap.add_argument("--ab", action="store_true", help="阶段2：全量重训 vs 增量 EKF 的四指标对照")
    ap.add_argument("--grid", action="store_true", help="阶段1：现状模型超参网格基线标定")
    ap.add_argument("--tau-grid", action="store_true", help="阶段2b：逐联赛 τ 标定（增量转移方差网格）")
    ap.add_argument("--tau-list", default="0.005,0.01,0.02,0.03,0.05,0.08", help="--tau-grid 的 τ 候选（逗号分隔）")
    ap.add_argument("--league", default=None, help="仅处理指定联赛（默认全部 5 联赛）")
    ap.add_argument("--train-frac", type=float, default=0.8, help="时间序列切分训练占比（默认 0.8）")
    ap.add_argument("--tau-att", type=float, default=0.03, help="attack 随机游走状态转移 std（默认 0.03）")
    ap.add_argument("--tau-def", type=float, default=0.03, help="defense 随机游走状态转移 std（默认 0.03）")
    ap.add_argument("--max-goals", type=int, default=10, help="比分边际化最大进球（默认 10）")
    ap.add_argument("--sigmas", default="0.25,0.35,0.50", help="--grid 的 sigma 网格（逗号分隔）")
    ap.add_argument("--half-lives", default="none,150,300", help="--grid 的 half_life 网格（none 表无衰减）")
    ap.add_argument("--json", default=None, help="报告输出 JSON 路径（默认 reports/bayesian_incremental_ab_<ts>.json")
    args = ap.parse_args()

    if not (args.ab or args.grid or args.tau_grid):
        ap.print_help()
        sys.exit(0)

    df = load_match_data()
    leagues = [args.league] if args.league else LEAGUES

    sigmas = [float(s) for s in args.sigmas.split(",")]
    half_lives = [None if s.strip().lower() == "none" else float(s) for s in args.half_lives.split(",")]
    tau_list = [float(s) for s in args.tau_list.split(",")]

    modes = [m for m, on in (("grid", args.grid), ("ab", args.ab), ("tau_grid", args.tau_grid)) if on]
    results: Dict = {"generated_at": datetime.now().isoformat(), "mode": "+".join(modes),
                     "train_frac": args.train_frac, "leagues": leagues}

    if args.grid:
        grid_out = {}
        for lg in leagues:
            grid_out[lg] = run_grid(df, lg, args.train_frac, sigmas, half_lives)
        results["grid"] = grid_out
    if args.ab:
        ab_out = {}
        for lg in leagues:
            ab_out[lg] = run_ab(df, lg, args.train_frac, args.tau_att, args.tau_def, args.max_goals)
        results["ab"] = ab_out
    if args.tau_grid:
        tau_out = {}
        for lg in leagues:
            tau_out[lg] = run_tau_scan(df, lg, args.train_frac, tau_list, args.max_goals)
        results["tau_grid"] = tau_out

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.json) if args.json else REPORTS_DIR / f"bayesian_incremental_ab_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告产物：{out}")


if __name__ == "__main__":
    main()