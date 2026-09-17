# -*- coding: utf-8 -*-
"""
engine_ab_control.py — P1-C' 统一引擎双基线补对照
====================================================================
背景：统一引擎（DixonColes）已在 C-20260909-008 全量切换投产，但切换时
仅验证矩阵对齐/一致性（60/60），未做 legacy 与引擎在同一批「未来前瞻样本」
上的完整四指标对照。本脚本补上该对照，固化门禁与可回滚判定。

方法（见 .trae/documents/P1-C-统一引擎双基线补对照计划.md）：
  1. 样本筛选：model_predictions 读 Lambda_home/Lambda_away 按 match_id pivot 得
     λh/λa，JOIN matches 取赛果（home_goals/away_goals 优先，回退 actual_wdl/actual_score）。
  2. 双路径（同一 λ）：
     - legacy：CalcEngine.poisson_score_predict(λh, λa, max_goals, rho)
     - 引擎：DixonColesGenerator(max_goals).generate(λh, λa) → score_matrix
     各自累加边际 WDL，列序 [客胜, 平局, 主胜]（对齐 train_models.compute_rps）。
  3. 四指标：compute_rps + sklearn log_loss/accuracy_score + draw_recall。
  4. 门禁：RPS/LogLoss 引擎 ≤ legacy+ε；Acc/DrawRecall 引擎 ≥ legacy−ε。
     任一 FAIL → 输出回退指令（USE_UNIFIED_ENGINE=0 / unified_engine.enabled:false）。

只读 odds.db，回写单个 reports/engine_ab_control_{ts}.json，不触碰预测/训练写入。

用法：
  python scripts/engine_ab_control.py [--cutoff 2026-08-30]
      [--model-name t006_score_predictor_v5] [--max-goals 7] [--rho -0.30]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, log_loss

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from prediction_core import CalcEngine  # noqa: E402
from unified_prediction_engine import DixonColesGenerator  # noqa: E402
from train_models import compute_rps  # noqa: E402

ODDS_DB = BASE_DIR / "data" / "odds.db"
REPORTS_DIR = BASE_DIR / "reports"

_WDL_NORM = {"主胜": 2, "胜": 2, "平局": 1, "平": 1, "客胜": 0, "负": 0}
CLASS_NAMES = ["客胜", "平局", "主胜"]


# ============================================================
# 数据加载
# ============================================================
def _parse_score(score):
    """'H:A' 或 'H-A' → (0=客胜,1=平,2=主胜)；解析失败返回 None。"""
    try:
        s = str(score).strip()
        for sep in (":", "-"):
            if sep in s:
                h, a = s.split(sep)
                h, a = int(h), int(a)
                return 2 if h > a else (0 if h < a else 1)
        return None
    except (ValueError, AttributeError):
        return None


def resolve_actual(home_goals, away_goals, actual_wdl, actual_score):
    """真实赛果归一：整数进球列优先，回退 actual_wdl / actual_score。"""
    if home_goals is not None and away_goals is not None:
        return 2 if home_goals > away_goals else (0 if home_goals < away_goals else 1)
    if actual_wdl:
        y = _WDL_NORM.get(str(actual_wdl).strip())
        if y is not None:
            return y
    if actual_score:
        return _parse_score(actual_score)
    return None


def connect_db():
    conn = sqlite3.connect(str(ODDS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def load_samples(conn, model_name):
    """「有 λ + 有赛果」样本集。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT match_id, prediction_type, probability FROM model_predictions "
        "WHERE model_name = ? AND prediction_type IN ('Lambda_home','Lambda_away')",
        (model_name,),
    )
    lh, la = {}, {}
    for mid, ptype, prob in cur.fetchall():
        if prob is None:
            continue
        if ptype == "Lambda_home":
            lh[mid] = float(prob)
        else:
            la[mid] = float(prob)
    mids = set(lh) & set(la)

    meta = {}
    for r in conn.execute("SELECT match_id, match_date, home_goals, away_goals, "
                          "actual_wdl, actual_score, league FROM matches"):
        meta[r["match_id"]] = (r["match_date"] or "", r["home_goals"], r["away_goals"],
                               r["actual_wdl"], r["actual_score"], r["league"] or "")

    samples = []
    for mid in mids:
        if mid not in meta:
            continue
        mdate, hg, ag, wdl, score, league = meta[mid]
        y = resolve_actual(hg, ag, wdl, score)
        if y is None:
            continue
        samples.append({"match_id": mid, "date": mdate, "league": league,
                        "lh": lh[mid], "la": la[mid], "y": y})
    samples.sort(key=lambda s: s["date"])
    return samples


# ============================================================
# 双路径边际 WDL
# ============================================================
def marginal_wdl_legacy(lh, la, max_goals, rho):
    sp = CalcEngine.poisson_score_predict(lh, la, max_goals=max_goals, rho=rho)
    win = draw = lose = 0.0
    for key, p in sp.items():
        h, a = key.split(":")
        h, a = int(h), int(a)
        if h > a:
            win += p
        elif h == a:
            draw += p
        else:
            lose += p
    return np.array([lose, draw, win])


def marginal_wdl_engine(lh, la, max_goals, rho):
    gen = DixonColesGenerator(rho=rho, max_goals=max_goals)
    res = gen.generate(lh, la, verbose=False)
    m = res["score_matrix"]
    win = draw = lose = 0.0
    for h in range(m.shape[0]):
        for a in range(m.shape[1]):
            p = m[h, a]
            if h > a:
                win += p
            elif h == a:
                draw += p
            else:
                lose += p
    return np.array([lose, draw, win])


def spot_check(lh, la, max_goals, rho):
    """抽查同一 λ 下两路径比分矩阵逐格最大绝对差（预期 ≤ 1e-6）。"""
    sp = CalcEngine.poisson_score_predict(lh, la, max_goals=max_goals, rho=rho)
    gen = DixonColesGenerator(rho=rho, max_goals=max_goals)
    m = gen.generate(lh, la, verbose=False)["score_matrix"]
    max_diff = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            leg = sp.get(f"{h}:{a}", 0.0)
            max_diff = max(max_diff, abs(leg - m[h, a]))
    return float(max_diff)


# ============================================================
# 指标与门禁
# ============================================================
def run_arm(samples, max_goals, rho, path):
    proba, trues = [], []
    for s in samples:
        if path == "legacy":
            p = marginal_wdl_legacy(s["lh"], s["la"], max_goals, rho)
        else:
            p = marginal_wdl_engine(s["lh"], s["la"], max_goals, rho)
        proba.append(p)
        trues.append(s["y"])
    P = np.array(proba)
    Y = np.array(trues)
    rps = compute_rps(Y, P, path)
    ll = float(log_loss(Y, P, labels=[0, 1, 2]))
    pred = P.argmax(1)
    acc = float(accuracy_score(Y, pred))
    draw_tp = int(((pred == 1) & (Y == 1)).sum())
    draw_fn = int(((pred != 1) & (Y == 1)).sum())
    dr = draw_tp / (draw_tp + draw_fn) if (draw_tp + draw_fn) else float("nan")
    print(f"[{path}] RPS={rps:.4f} LogLoss={ll:.4f} Acc={acc:.4f} "
          f"DrawRecall={dr:.4f} (n={len(Y)})")
    return {"rps": rps, "logloss": ll, "acc": acc, "draw_recall": dr, "n": len(Y)}


def make_gate(legacy, engine):
    def _cmp_ok(metric):
        return not (np.isnan(engine[metric]) or np.isnan(legacy[metric]))
    gate = {}
    gate["rps"] = "PASS" if _cmp_ok("rps") and engine["rps"] <= legacy["rps"] + 1e-6 else "FAIL"
    gate["logloss"] = "PASS" if _cmp_ok("logloss") and engine["logloss"] <= legacy["logloss"] + 1e-6 else "FAIL"
    gate["acc"] = "PASS" if _cmp_ok("acc") and engine["acc"] >= legacy["acc"] - 1e-4 else "FAIL"
    gate["draw_recall"] = "PASS" if _cmp_ok("draw_recall") and engine["draw_recall"] >= legacy["draw_recall"] - 1e-6 else "FAIL"
    gate["draw_recall"] = "SKIP" if (np.isnan(engine["draw_recall"]) or np.isnan(legacy["draw_recall"])) else gate["draw_recall"]
    return gate


ROLLBACK_INSTR = (
    "回退指令：设置 USE_UNIFIED_ENGINE=0，或将 config.yaml 的 unified_engine.enabled 置为 false"
)


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="P1-C' 统一引擎双基线补对照")
    ap.add_argument("--cutoff", default="2026-08-30", help="未来前瞻子集分割日期（训练截止后）")
    ap.add_argument("--model-name", default="t006_score_predictor_v5", help="λ 落库的 model_name")
    ap.add_argument("--max-goals", type=int, default=7, help="比分矩阵最大进球截断")
    ap.add_argument("--rho", type=float, default=-0.30, help="Dixon-Coles τ 修正参数")
    args = ap.parse_args()

    conn = connect_db()
    samples = load_samples(conn, args.model_name)
    conn.close()
    print(f"模型 {args.model_name}：读入「有 λ + 有赛果」样本 {len(samples)} 场")

    if not samples:
        print("无可用样本，退出")
        sys.exit(2)

    full = samples
    future = [s for s in samples if (s["date"] or "") > args.cutoff]
    print(f"截止 {args.cutoff}：全量 {len(full)} 场 / 未来前瞻子集 {len(future)} 场")

    # 矩阵对齐抽查（首场）
    s0 = full[0]
    sd = spot_check(s0["lh"], s0["la"], args.max_goals, args.rho)
    print(f"矩阵对齐抽查（首场 λh={s0['lh']:.3f}/λa={s0['la']:.3f}）："
          f"两路径逐格最大绝对差 = {sd:.3e}")

    # 全量样本双路径四指标
    print("\n=== 全量样本（确定性算法，统计等价未来样本）===")
    leg_full = run_arm(full, args.max_goals, args.rho, "legacy")
    eng_full = run_arm(full, args.max_goals, args.rho, "engine")
    gate_full = make_gate(leg_full, eng_full)
    concl_full = "全部 PASS（引擎不劣化，可维持投产）" if all(
        v == "PASS" for v in gate_full.values()) else f"存在 FAIL（不投产）→ {ROLLBACK_INSTR}"
    print("门禁：", " | ".join(f"{k}={v}" for k, v in gate_full.items()))

    # 未来前瞻子集
    print("\n=== 未来前瞻子集 ===")
    if future:
        leg_fut = run_arm(future, args.max_goals, args.rho, "legacy")
        eng_fut = run_arm(future, args.max_goals, args.rho, "engine")
        if len(future) < 60:
            gate_fut = {k: "insufficient-sample" for k in ("rps", "logloss", "acc", "draw_recall")}
            concl_fut = f"insufficient-sample（n={len(future)} < 60，不强制门禁，仅供合规记录）"
        else:
            gate_fut = make_gate(leg_fut, eng_fut)
            concl_fut = "全部 PASS" if all(v == "PASS" for v in gate_fut.values()) \
                else f"存在 FAIL → {ROLLBACK_INSTR}"
        print("门禁：", " | ".join(f"{k}={v}" for k, v in gate_fut.items()))
    else:
        leg_fut = eng_fut = gate_fut = None
        concl_fut = "无未来前瞻样本"

    # 回写报告
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    report = {
        "generated_at": now,
        "model_name": args.model_name,
        "params": {"cutoff": args.cutoff, "max_goals": args.max_goals, "rho": args.rho},
        "matrix_alignment_max_abs_diff": sd,
        "full_sample": {
            "n": leg_full["n"], "legacy": leg_full, "engine": eng_full,
            "gate": gate_full, "conclusion": concl_full,
        },
        "future_subset": ({
            "n": leg_fut["n"], "legacy": leg_fut, "engine": eng_fut,
            "gate": gate_fut, "conclusion": concl_fut,
        } if future else {"n": 0, "conclusion": concl_fut}),
    }
    out = REPORTS_DIR / f"engine_ab_control_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n全量样本门禁结论：{concl_full}")
    print(f"未来前瞻子集结论：{concl_fut}")
    print(f"报告产物：{out}")


if __name__ == "__main__":
    main()