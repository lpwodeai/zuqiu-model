# -*- coding: utf-8 -*-
"""C-20260905-005 Phase A: 上下文赌博机策略梯度（Contextual Bandit Policy Gradient）——RL 长期累积 EV 优化目标最小可验证实验。

背景：
  系统性高估调查全部闭环（概率层/训练端 EV/MOD/Penalty/数据端/决策层/换市场），
  唯一剩余候选 = 「长期累积 EV 优化目标（RL 策略梯度）」。本脚本实施 Phase A：

  状态   s = [1, p_h, p_d, p_a, ln o_h, ln o_d, ln o_a]（temp_probs 冻结 + 500live 赔率）
  动作   a ∈ {no_bet, bet_home, bet_draw, bet_away}（K=4）
  回报   r(a) = 0（不投）/ (o_d−1)（命中）/ −1（未中）；实际结果已知 → 3 个投注动作回报全部可枚举
  目标   J(θ) = E_s[ V_π(s) ]，V_π(s) = Σ_a π(a|s)·r(a,s)（softmax 期望回报，无采样方差）

与已证伪三种训练端损失的本质区别：
  1. 不扭曲已校准概率（temp_probs 冻结），只学习投注策略层 → 避免平局召回崩溃/信号抹除
  2. 梯度由「实现回报×策略概率」驱动：高赔率方向若实际亏钱 → 期望回报负 → 自动收缩投注概率
     （对抗高赔率过度自信，且由真实回报驱动而非模型自身 EV，不自我强化）
  3. 动作空间全枚举回报（非轨迹采样）→ 无 off-policy 偏差与 REINFORCE 高方差

算法：
  策略 π(a|s) = softmax(z)，z = W·φ(s) + b
  损失 L = mean(V_π) + λ_ent·mean(H(π)) − λ_l2·mean(||W||²)
  梯度 dL/dW = (1/n)·Xᵀ[π ⊙ (A − λ_ent·(ln π + 1 + H[:,None]))] − 2λ_l2·W
  其中 A = R − V_π[:,None]（优势），H = −Σπ ln π
  Adam 优化，纯 numpy 实现

评价（与之前全实验同口径）：
  TSS(5) 折内 fit → 折外 argmax 决策（含 no_bet）→ 全量 OOF 平注 ROI
  z 检验用「逐注盈亏标准差」（避开 1/平均赔率 的 Jensen 陷阱）
  + 分折/分赛季稳健性 + λ_ent 扫描

用法：python rl_bet_policy_pg.py [--oof ...]
产物：reports/rl_bet_policy_pg_<TS>.md/.json
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
from edge_monotonic_fix import load_oof, prob_matrix, TemperatureScaler  # noqa: E402
from market_switch_backtest import load_markets  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
REPORT_DIR = PROJECT_DIR / "reports"

# 动作顺序: 0=no_bet, 1=home, 2=draw, 3=away
K = 4
ACTIONS = ["no_bet", "home", "draw", "away"]
RESULT_TO_DIRECTION = {2: 1, 1: 2, 0: 3}  # actual_result(0客/1平/2主) -> action idx(1主/2平/3客)


def build_features(p_row, o_row):
    """φ(s) = [1, p_h, p_d, p_a, ln o_h, ln o_d, ln o_a]（p 顺序 [away,draw,home] → 转 [home,draw,away]）。"""
    p_h, p_d, p_a = float(p_row[2]), float(p_row[1]), float(p_row[0])
    o_h, o_d, o_a = float(o_row[0]), float(o_row[1]), float(o_row[2])
    return np.array([1.0, p_h, p_d, p_a,
                     np.log(o_h), np.log(o_d), np.log(o_a)], dtype=np.float64)


def build_rewards(o_row, actual):
    """R = [r_no_bet, r_home, r_draw, r_away]（平注 1 单位）。"""
    o_h, o_d, o_a = float(o_row[0]), float(o_row[1]), float(o_row[2])
    r = np.zeros(K, dtype=np.float64)
    r[1] = (o_h - 1.0) if actual == 2 else -1.0
    r[2] = (o_d - 1.0) if actual == 1 else -1.0
    r[3] = (o_a - 1.0) if actual == 0 else -1.0
    return r


def train_policy(X, R, lambda_ent=0.1, lambda_l2=1e-4, lr=0.1, n_iter=400, seed=0):
    """softmax 期望回报最大化 + 熵正则（全枚举无采样），Adam 优化。"""
    rng = np.random.default_rng(seed)
    n, D = X.shape
    W = np.zeros((K, D), dtype=np.float64)
    b = np.zeros(K, dtype=np.float64)
    mW = np.zeros_like(W); vW = np.zeros_like(W)
    mb = np.zeros_like(b); vb = np.zeros_like(b)
    b1, b2, eps = 0.9, 0.999, 1e-8
    t = 0
    best = None
    for it in range(n_iter):
        t += 1
        z = X @ W.T + b
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        pi = e / e.sum(axis=1, keepdims=True)
        V = (pi * R).sum(axis=1)
        H = -(pi * np.log(np.clip(pi, 1e-12, 1.0))).sum(axis=1)
        A = R - V[:, None]
        inner = A - lambda_ent * (np.log(np.clip(pi, 1e-12, 1.0)) + 1.0 + H[:, None])
        gW = (pi * inner).T @ X / n - 2.0 * lambda_l2 * W
        gb = (pi * inner).sum(axis=0) / n
        # Adam
        mW = b1 * mW + (1 - b1) * gW
        vW = b2 * vW + (1 - b2) * gW * gW
        W += lr * (mW / (1 - b1 ** t)) / (np.sqrt(vW / (1 - b2 ** t)) + eps)
        mb = b1 * mb + (1 - b1) * gb
        vb = b2 * vb + (1 - b2) * gb * gb
        b += lr * (mb / (1 - b1 ** t)) / (np.sqrt(vb / (1 - b2 ** t)) + eps)
        if it % 100 == 0:
            best = (V.mean(), H.mean(), pi.copy(), W.copy(), b.copy())
    if best is None:
        best = (V.mean(), H.mean(), pi.copy(), W.copy(), b.copy())
    return best


def predict(X, W, b):
    z = X @ W.T + b
    z -= z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def season_of(date_str):
    try:
        d = pd.Timestamp(date_str)
    except Exception:
        return None
    y = d.year
    return f"{y-1}/{y}" if d.month < 8 else f"{y}/{y+1}"


def evaluate(decisions, odds_list, actual_list, dates, fold_ids):
    """argmax 决策（-1=no_bet, 1=home, 2=draw, 3=away）逐注盈亏统计 + 正确 z 检验。"""
    rows = []
    for d, o, a, dt, fd in zip(decisions, odds_list, actual_list, dates, fold_ids):
        if d <= 0:
            continue
        o_d = (o[0], o[1], o[2])[d - 1]
        won = int((d - 1) == (2 - a))  # actual: 0客/1平/2主; action: 1主/2平/3客
        profit = (o_d - 1.0) if won else -1.0
        rows.append({"fold": fd, "season": season_of(dt), "dir": d - 1,
                     "odds": o_d, "won": won, "profit": profit})
    if not rows:
        return {"n": 0}
    df = pd.DataFrame(rows)
    n = len(df)
    mean_p = df["profit"].mean()
    std_p = df["profit"].std(ddof=1) if n > 1 else 0.0
    z = mean_p / (std_p / np.sqrt(n)) if std_p > 0 else 0.0
    out = {"n": n, "hit_rate": df["won"].mean(), "roi": mean_p,
           "profit": df["profit"].sum(), "avg_odds": df["odds"].mean(),
           "std_profit": std_p, "z": z,
           "nobet_frac": 1.0 - n / len(decisions)}
    # 动作分布
    cnt = pd.Series(decisions).value_counts()
    out["action_dist"] = {ACTIONS[i]: int(cnt.get(i, 0)) for i in range(K)}
    # 分折 / 分赛季
    fold_agg, season_agg = {}, {}
    for (k, g) in df.groupby("fold"):
        gn = len(g)
        fold_agg[str(k)] = {"n": gn, "roi": g["profit"].mean(),
                            "hit": g["won"].mean(), "profit": g["profit"].sum()}
    for (k, g) in df.groupby("season"):
        gn = len(g)
        season_agg[str(k)] = {"n": gn, "roi": g["profit"].mean(),
                              "hit": g["won"].mean(), "profit": g["profit"].sum()}
    out["by_fold"] = fold_agg
    out["by_season"] = season_agg
    return out


def main():
    parser = argparse.ArgumentParser(description="RL Phase A: 上下文赌博机策略梯度")
    parser.add_argument("--oof", type=Path, default=ASSETS_DIR / "oof_predictions_20260903_012452.csv")
    parser.add_argument("--ent", type=float, nargs="+", default=[0.01, 0.1, 1.0],
                        help="熵正则系数扫描")
    parser.add_argument("--lr", type=float, default=0.1)
    parser.add_argument("--n-iter", type=int, default=400)
    args = parser.parse_args()

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    logging.disable(logging.WARNING)

    print("[1/4] 加载 OOF + TSS 折内 TempScaling...")
    df = load_oof(args.oof)
    df = df.sort_values("date").reset_index(drop=True)
    y_true = df["actual_result"].values.astype(int)
    raw_probs = prob_matrix(df, src="mean", kind="raw")
    N = len(df)
    temp_probs = np.zeros_like(raw_probs)
    tscv = TimeSeriesSplit(n_splits=5)
    fold_ids = np.full(N, -1, dtype=int)
    for fi, (tr_idx, va_idx) in enumerate(tscv.split(raw_probs)):
        ts = TemperatureScaler().fit(y_true[tr_idx], raw_probs[tr_idx])
        temp_probs[va_idx] = ts.transform(raw_probs[va_idx])
        fold_ids[va_idx] = fi
    covered = temp_probs.sum(axis=1) > 0
    sub = df[covered].reset_index(drop=True)
    temp_sub = temp_probs[covered]
    fold_sub = fold_ids[covered]
    print(f"   覆盖行: {int(covered.sum())} / {N}")

    print("[2/4] 构建 500live 赔率映射...")
    sub_key, markets = load_markets(sub)
    m = markets["500live(基线)"]
    print(f"   500live 对齐: {len(m)} 场")

    # 构建 bandit 数据集（有赔率场次）
    X_list, R_list, O_list, A_list, D_list, F_list = [], [], [], [], [], []
    for i in range(len(sub)):
        o = m.get(sub_key[i])
        if o is None:
            continue
        X_list.append(build_features(temp_sub[i], o))
        R_list.append(build_rewards(o, int(sub.iloc[i]["actual_result"])))
        O_list.append(o)
        A_list.append(int(sub.iloc[i]["actual_result"]))
        D_list.append(str(sub.iloc[i]["date"])[:10])
        F_list.append(int(fold_sub[i]))
    X = np.vstack(X_list)
    R = np.vstack(R_list)
    n_total = len(X_list)
    print(f"   bandit 样本: {n_total}（有赔率且有效概率）")

    print("[3/4] 逐折训练策略 + 折外预测（λ_ent 扫描）...")
    results = {}
    for le in args.ent:
        decisions = np.full(n_total, 0, dtype=int)  # 默认 no_bet
        pi_outs = np.zeros((n_total, K))
        for fi in range(tscv.n_splits):
            tr = np.where(np.array(F_list) < fi)[0]
            va = np.where(np.array(F_list) == fi)[0]
            if len(tr) < 100 or len(va) == 0:
                decisions[va] = 0
                continue
            W, b = train_policy(X[tr], R[tr], lambda_ent=le,
                                lambda_l2=1e-4, lr=args.lr, n_iter=args.n_iter,
                                seed=42 + fi)[3:]
            pi = predict(X[va], W, b)
            pi_outs[va] = pi
            decisions[va] = pi.argmax(axis=1)
        # 整体评价
        res = evaluate(decisions, O_list, A_list, D_list, np.array(F_list))
        # no_bet 占比 & 投注动作分布
        res["ent"] = le
        results[le] = res
        nbet = res["n"]
        roi = res["roi"] if nbet else float("nan")
        z = res["z"] if nbet else 0.0
        print(f"   λ_ent={le:<6} 投注n={nbet:<6} ROI={roi*100:+7.2f}%  z={z:+.2f}  "
              f"命中={res['hit_rate']*100 if nbet else 0:5.1f}%  "
              f"no_bet占比={res['nobet_frac']*100:5.1f}%  "
              f"动作分布={res['action_dist']}")

    print("[4/4] 生成报告...")
    lines = []
    lines.append("# RL Phase A: 上下文赌博机策略梯度（Contextual Bandit Policy Gradient）")
    lines.append("")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append(f"- OOF 文件: `{args.oof.name}`（mean raw，TSS 折内 TemperatureScaling，temp_probs 冻结）")
    lines.append(f"- 市场: 500live avg_live（基线市场）；状态 = [1, p_h, p_d, p_a, ln o_h, ln o_d, ln o_a]")
    lines.append(f"- 动作: no_bet / home / draw / away；回报: 平注 1 单位（命中 o−1，未中 −1，不投 0）")
    lines.append(f"- 算法: softmax 期望回报最大化（全枚举无采样）+ λ_ent 熵正则 + L2；Adam lr={args.lr} / {args.n_iter} iter")
    lines.append(f"- 训练: TSS(5) 折内 fit → 折外 argmax 决策（含 no_bet）→ 全量 OOF 评价")
    lines.append(f"- 基线对照: TempScaling EV 引擎（500live）平注ROI -3.71% n=9446")
    lines.append(f"- 评估子集: 折外可评估 {int(covered.sum())} 场；500live 对齐 bandit 样本 {n_total}")
    lines.append("")

    lines.append("## 一、整体 OOF 评价（按 λ_ent）")
    lines.append("")
    lines.append("| λ_ent | 投注n | 命中率 | 平注ROI | 盈亏 | 平均赔率 | z | no_bet占比 | 动作分布(不投/主/平/客) |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for le, res in results.items():
        if res["n"] == 0:
            lines.append(f"| {le} | 0 | — | — | — | — | — | {res['nobet_frac']*100:.1f}% | {res['action_dist']} |")
            continue
        d = res["action_dist"]
        lines.append(f"| {le} | {res['n']} | {res['hit_rate']*100:.1f}% | {res['roi']*100:+.2f}% | "
                     f"{res['profit']:+.2f} | {res['avg_odds']:.2f} | {res['z']:+.2f} | "
                     f"{res['nobet_frac']*100:.1f}% | {d['no_bet']}/{d['home']}/{d['draw']}/{d['away']} |")
    lines.append("")

    for le, res in results.items():
        if res["n"] == 0:
            lines.append(f"## λ_ent={le} — 无投注（策略全 no_bet），无法评价 ROI")
            lines.append("")
            continue
        lines.append(f"## λ_ent={le} — 整体 平注ROI {res['roi']*100:+.2f}%  n={res['n']}  "
                     f"z={res['z']:+.2f}  命中 {res['hit_rate']*100:.1f}%  均赔 {res['avg_odds']:.2f}")
        lines.append("")
        lines.append("### 分折（TSS）")
        lines.append("")
        lines.append("| 折 | n | 命中率 | 平注ROI | 盈亏 |")
        lines.append("|---|---|---|---|---|")
        for k in sorted(res["by_fold"]):
            v = res["by_fold"][k]
            lines.append(f"| 折{int(k)+1} | {v['n']} | {v['hit']*100:.1f}% | {v['roi']*100:+.2f}% | {v['profit']:+.2f} |")
        pos_fold = sum(1 for v in res["by_fold"].values() if v["roi"] > 0)
        lines.append(f"")
        lines.append(f"- 正收益折数: {pos_fold}/{len(res['by_fold'])}")
        lines.append("")
        lines.append("### 分赛季")
        lines.append("")
        lines.append("| 赛季 | n | 命中率 | 平注ROI | 盈亏 |")
        lines.append("|---|---|---|---|---|")
        for k in sorted(res["by_season"]):
            v = res["by_season"][k]
            lines.append(f"| {k} | {v['n']} | {v['hit']*100:.1f}% | {v['roi']*100:+.2f}% | {v['profit']:+.2f} |")
        pos_season = sum(1 for v in res["by_season"].values() if v["roi"] > 0)
        lines.append(f"")
        lines.append(f"- 正收益赛季数: {pos_season}/{len(res['by_season'])}")
        lines.append("")

    lines.append("## 结论")
    lines.append("")
    best_ent = None
    best_z = -1e9
    for le, res in results.items():
        if res["n"] >= 100 and res["z"] > best_z:
            best_z = res["z"]
            best_ent = le
    base_roi = -0.0371
    if best_ent is not None:
        b = results[best_ent]
        lines.append(f"- 最优（n≥100 且 z 最高）: λ_ent={best_ent}  ROI {b['roi']*100:+.2f}%  "
                     f"z={b['z']:+.2f}  n={b['n']}（基线 Temp -3.71% n=9446）")
    for le, res in results.items():
        if res["n"] >= 100:
            delta = (res["roi"] - base_roi) * 100
            sig = "✅ 显著正" if res["z"] >= 2.0 else ("❌ 不显著" if res["z"] < 1.96 else "⚠️ 边缘")
            lines.append(f"- λ_ent={le}: ROI {res['roi']*100:+.2f}%（vs 基线 Δ={delta:+.2f}pp）z={res['z']:+.2f} → {sig}")
        elif res["n"] == 0:
            lines.append(f"- λ_ent={le}: **策略坍缩为全不投**（n=0）→ 说明模型在该市场无可学习的正 EV 动作")
        else:
            lines.append(f"- λ_ent={le}: n={res['n']}<100 样本不足")
    nobet_all = all(res["n"] == 0 for res in results.values())
    if nobet_all:
        lines.append(f"- **Phase A 判定：策略在所有 λ_ent 下均坍缩为 no_bet**——模型概率冻结后，RL 无法从实现回报中学到任何正 EV 投注方向，与决策层结论（无统计可确认 edge）一致")
    elif all(res["z"] < 1.96 for res in results.values() if res["n"] >= 100):
        lines.append(f"- **Phase A 判定：RL 策略层未能产生统计显著正 ROI**——即使允许策略自由学习「投/不投/投哪」，OOF 仍无转正组合（n≥100 且 z≥1.96），RL 长期累积 EV 路径证伪；系统性高估调查至此全部闭环")
    else:
        lines.append(f"- **Phase A 判定：存在统计显著正 ROI 组合（z≥1.96）**——RL 策略层可能发现可投注 edge，需 Phase B 验证（分赛季/实盘小注）")

    md_path = REPORT_DIR / f"rl_bet_policy_pg_{RUN_TS}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path = REPORT_DIR / f"rl_bet_policy_pg_{RUN_TS}.json"
    json_path.write_text(json.dumps({
        "meta": {"oof": str(args.oof), "N": int(N), "covered": int(covered.sum()),
                 "bandit_n": int(n_total), "ent_sweep": list(args.ent),
                 "lr": args.lr, "n_iter": args.n_iter, "ts": RUN_TS,
                 "baseline_500live_flat_roi": base_roi},
        "results": {str(k): v for k, v in results.items()},
    }, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    print(f"\n✅ 报告: {md_path}")
    print(f"✅ 数据: {json_path}")


if __name__ == "__main__":
    main()
