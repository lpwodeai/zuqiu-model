# -*- coding: utf-8 -*-
"""P0-E 实盘小注验证 — ROI / z 检验评估（Pre-registered Evaluation）

从台账 data/live_trial_away_favorite_ledger.csv 的 settled 条目计算评估指标，
落地协议 §四评估准则：
  1. 注数 / 胜率 / 累计投入 / 累计盈亏 / 平注 ROI
  2. 盈亏平衡命中率 = 1/平均赔率（协议口径），vs 实际命中率 z 检验
  3. ROI 95% 置信区间（每注盈亏样本标准差），是否包含 0
  4. 分月/分段时间收益

⚠️ 判定原则（协议 §四注意）：z 检验在赔率长尾下会高估显著性（Jensen 效应），
**最终以净盈亏金额为准绳，显著性仅作参考**。

用法:
  python scripts/live_trial_evaluation.py [--ledger data/live_trial_away_favorite_ledger.csv]
                                          [--out-dir docs/live_trial_eval]
                                          [--min-n 100]      # z 显著判定最小样本
产出:
  docs/live_trial_eval/live_trial_eval_<TS>.md + 终端摘要
退出码: 0=正常（样本不足也返回 0）
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_LEDGER = PROJECT_DIR / "data" / "live_trial_away_favorite_ledger.csv"
DEFAULT_OUT_DIR = PROJECT_DIR / "docs" / "live_trial_eval"

LEDGER_COLS = [
    "bet_id", "match_id_en", "match_date", "league",
    "home_en", "away_en", "away_odds", "p_away", "ev",
    "stake_cny", "status", "result", "profit_cny", "slip_ts",
]


def _zscore(hit, p0, n):
    """二项比例 z 检验（含连续性校正）。"""
    if n == 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    se = np.sqrt(p0 * (1 - p0) / n)
    if se == 0:
        return 0.0
    return float((hit - p0) / se)


def evaluate(ledger: pd.DataFrame, min_n=100) -> dict:
    """返回评估指标 dict。"""
    out = {"n_total": len(ledger), "n_pending": int((ledger["status"] == "pending").sum()),
           "n_settled": int((ledger["status"] == "settled").sum())}
    if out["n_settled"] == 0:
        out["status"] = "no_settled"
        return out

    s = ledger[ledger["status"] == "settled"].copy()
    s["profit_cny"] = pd.to_numeric(s["profit_cny"], errors="coerce").fillna(0.0)
    s["away_odds"] = pd.to_numeric(s["away_odds"], errors="coerce")
    s["stake_cny"] = pd.to_numeric(s["stake_cny"], errors="coerce").fillna(20.0)

    n = len(s)
    wins = int((s["result"] == "W").sum())
    hit = wins / n
    stake = float(s["stake_cny"].sum())
    profit = float(s["profit_cny"].sum())
    roi = profit / stake if stake > 0 else 0.0

    odds_mean = float(s["away_odds"].mean()) if s["away_odds"].notna().any() else np.nan
    breakeven = 1.0 / odds_mean if odds_mean and odds_mean > 1 else np.nan

    z_hit = _zscore(hit, breakeven, n) if np.isfinite(breakeven) else 0.0

    # ROI 95% CI（每注盈亏样本分布）
    per_bet = s["profit_cny"].values
    sd = float(np.std(per_bet, ddof=1)) if n > 1 else 0.0
    se_roi = sd / np.sqrt(n) / (stake / n) if stake > 0 else 0.0  # 每注盈亏 se / 每注本金
    ci_lo, ci_hi = roi - 1.96 * se_roi, roi + 1.96 * se_roi

    # 分段（按月）
    seg = {}
    if "match_date" in s.columns:
        s["month"] = s["match_date"].astype(str).str[:7]
        for m, g in s.groupby("month"):
            gstake = float(g["stake_cny"].sum())
            gprofit = float(g["profit_cny"].sum())
            seg[m] = {"n": int(len(g)), "wins": int((g["result"] == "W").sum()),
                      "profit_cny": round(gprofit, 2),
                      "roi": round(gprofit / gstake * 100, 2) if gstake > 0 else 0.0}

    out.update({
        "status": "ok",
        "n_settled": n,
        "wins": wins,
        "losses": n - wins,
        "hit_rate": hit,
        "total_stake_cny": round(stake, 2),
        "total_profit_cny": round(profit, 2),
        "roi": roi,
        "roi_pct": round(roi * 100, 2),
        "odds_mean": round(odds_mean, 4) if np.isfinite(odds_mean) else None,
        "breakeven_hit_rate": round(breakeven, 4) if np.isfinite(breakeven) else None,
        "z_hit_rate": round(z_hit, 3),
        "roi_ci_95": [round(ci_lo * 100, 2), round(ci_hi * 100, 2)],
        "segments_by_month": seg,
        "min_n_for_sig": min_n,
        "significant": n >= min_n and z_hit >= 1.96,
    })
    return out


def write_report(eval_result: dict, out_dir: Path, run_ts: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 实盘小注验证评估报告（P0-E Pre-registered Evaluation）",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**协议**: 客胜≤2.5 / EV>0 / 平注¥20 / 300 注停止（rules frozen）",
        "",
    ]
    if eval_result["status"] == "no_settled":
        lines += [
            "## 结果：样本不足",
            "",
            f"- 台账总条目: {eval_result['n_total']}（pending {eval_result['n_pending']} / "
            f"settled {eval_result['n_settled']}）",
            "- 暂无已结算投注，无法评估。待 `--settle` 结算后重跑。",
        ]
    else:
        e = eval_result
        lines += [
            "## 一、总览",
            "",
            f"| 指标 | 值 |",
            "|---|---|",
            f"| 已结算注数 | {e['n_settled']} |",
            f"| 胜 / 负 | {e['wins']} / {e['losses']} |",
            f"| 实际命中率 | {e['hit_rate']*100:.2f}% |",
            f"| 平均赔率 | {e['odds_mean']} |",
            f"| 盈亏平衡命中率（1/平均赔率） | {e['breakeven_hit_rate']*100:.2f}% |",
            f"| 命中率 z 值 | {e['z_hit_rate']:+.3f}（显著判定需 n≥{e['min_n_for_sig']} 且 z≥1.96） |",
            f"| 累计投入 | ¥{e['total_stake_cny']:.2f} |",
            f"| 累计盈亏 | **¥{e['total_profit_cny']:+.2f}** |",
            f"| 平注 ROI | **{e['roi_pct']:+.2f}%**（95% CI {e['roi_ci_95'][0]:+.2f}% ~ "
            f"{e['roi_ci_95'][1]:+.2f}%） |",
            "",
            "## 二、判定",
            "",
        ]
        if e["significant"]:
            lines.append("**命中率显著高于盈亏平衡点（z ≥ 1.96，n ≥ 100）→ 提示正 edge**（仍需以净盈亏为准）。")
        elif e["n_settled"] < e["min_n_for_sig"]:
            lines.append(f"**样本不足**（{e['n_settled']}/{e['min_n_for_sig']}），显著性仅作参考。")
        else:
            lines.append("**命中率未显著高于盈亏平衡点。**")
        if e["roi_ci_95"][1] < 0:
            lines.append("**ROI 95% CI 上界 < 0 → 净盈亏显著为负**（Jensen 效应下以净盈亏为准绳）。")
        elif e["roi_ci_95"][0] > 0:
            lines.append("**ROI 95% CI 下界 > 0 → 净盈亏显著为正**。")
        else:
            lines.append("ROI 95% CI 包含 0 → 净盈亏暂无法区分真假 edge。")
        lines += [
            "",
            "## 三、分月收益",
            "",
            "| 月份 | n | 胜 | 盈亏(¥) | ROI |",
            "|---|---|---|---|---|",
        ]
        for m, g in e["segments_by_month"].items():
            lines.append(f"| {m} | {g['n']} | {g['wins']} | {g['profit_cny']:+.2f} | {g['roi']:+.2f}% |")
        lines += ["", "## 四、注意事项", "",
                  "> ⚠️ z 检验在赔率长尾下高估显著性（Jensen 效应，C-20260905-004 教训）；",
                  "> **最终以净盈亏金额为准绳，显著性仅作参考**。",
                  "> 预注册规则冻结，本报告不改变规则；若 100 注 ROI 显著为负需人工决策是否提前终止（记录 change_log）。"]
    text = "\n".join(lines)
    path = out_dir / f"live_trial_eval_{run_ts}.md"
    path.write_text(text, encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser(description="P0-E 实盘台账 ROI/z 评估")
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--min-n", type=int, default=100,
                    help="z 显著判定最小样本（协议默认 100）")
    args = ap.parse_args()

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if not args.ledger.exists():
        print(f"[SKIP] 台账不存在: {args.ledger}（尚无投注，样本不足）")
        return 0
    ledger = pd.read_csv(args.ledger, dtype={"bet_id": str})
    for c in LEDGER_COLS:
        if c not in ledger.columns:
            ledger[c] = ""
    res = evaluate(ledger, min_n=args.min_n)
    path = write_report(res, args.out_dir, run_ts)

    print("=" * 62)
    print("[P0-E] 实盘小注验证评估")
    print(f"  台账: {args.ledger}")
    if res["status"] == "no_settled":
        print(f"  已结算: 0 注（pending {res['n_pending']}）— 样本不足，先 --settle 结算")
    else:
        print(f"  已结算: {res['n_settled']} 注（胜 {res['wins']} / 负 {res['losses']}）")
        print(f"  命中率: {res['hit_rate']*100:.2f}% vs 盈亏平衡 {res['breakeven_hit_rate']*100:.2f}% "
              f"(z={res['z_hit_rate']:+.3f})")
        print(f"  累计盈亏: ¥{res['total_profit_cny']:+.2f} | ROI {res['roi_pct']:+.2f}% "
              f"(95% CI {res['roi_ci_95'][0]:+.2f}% ~ {res['roi_ci_95'][1]:+.2f}%)")
        for m, g in res["segments_by_month"].items():
            print(f"    [{m}] n={g['n']} ROI={g['roi']:+.2f}%")
    print(f"  报告: {path}")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
