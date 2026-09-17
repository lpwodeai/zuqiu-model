# -*- coding: utf-8 -*-
"""
run_shadow_incremental.py — P1-B shadow 并行（离线滚动 + 稳定判据）
====================================================================
背景（docs/bayesian_shadow_design.md，C-20260911-016）：
  P1-B 阶段2 的 shadow 并行 = 在 Python 离线侧，用「每日滚动任务」把
  全量重训模型（control＝生产现状，冻结）与增量 EKF（treatment＝实验）双跑，
  逐场「先预测→落库→后更新」，观察连续后验稳定性，为生产切换提供依据。

数据流（设计文档 §二）：
  1) 加载资产 assets/bayesian_model_{league}.json（control 臂，冻结）
  2) 恢复/初始化 BayesianIncrementalFilter（checkpoint 有则 load，否则 init_teams）
  3) 读取已完赛队列 match_date > 游标（升序，含赛果）
  4) 逐场：control.predict_wdl ──┬─> record_experiment(ab_test_log)
             treatment.predict_wdl ┘   served=control
             treatment.update(...)     ← 吸收赛果（严格先预测后更新）
  5) 保存 checkpoint（x/P/team_index/游标）
  6) --analyze：ab_test_framework.analyze + 后验不发散（trace(P) 阈值）

关键约束（设计文档 §五/§七/§十）：
  - 概率转序 [lose, draw, win]（对齐 train_models.compute_rps 列序，最高风险点）
  - 幂等：record_experiment 用 INSERT OR IGNORE（主键 exp+unit+variant）
  - 原子 checkpoint：一次 roll 完整结束后才 save，中途崩溃回退旧游标重吸收（不重复）
  - served 恒为 control（shadow 不影响生产，可随时停）

用法：
  python scripts/run_shadow_incremental.py --init
  python scripts/run_shadow_incremental.py --roll [--league 英超] [--tau-att X --tau-def X]  # τ 默认按 LEAGUE_TAU 逐联赛
  python scripts/run_shadow_incremental.py --analyze [--league 英超] [--min-n 60]

计划任务（每日，对齐 P0-E 模式）：
  schtasks /Create /TN "SoccerModel_P1BShadow" /TR "C:\\Python314\\python.exe F:\\zuqiu\\五大联赛专属模型\\五大联赛专属模型\\scripts\\run_shadow_incremental.py --roll" /SC DAILY /ST 13:00 /F
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from feature_utils import ODDS_DB_PATH, normalize_team_name  # noqa: E402
from bayesian_hierarchical_model import BayesianHierarchicalModel, LEAGUES  # noqa: E402
from bayesian_incremental import BayesianIncrementalFilter  # noqa: E402
from ab_test_framework import (  # noqa: E402
    analyze,
    connect_db,
    init_schema,
    record_experiment,
)

ASSETS_DIR = BASE_DIR / "assets"
SHADOW_DIR = BASE_DIR / "data" / "shadow"

# 后验不发散阈值：trace(P) ≤ TRACE_K × 2T（T=队数，2T=状态维度）
TRACE_K = 3.0

# 逐联赛推荐 τ（C-20260911-018 τ 标定产出，首次初始化默认值）
#   英超纯 RPS 最优为 0.02（ΔRPS -0.0103），但 Acc 掉 0.95pp，故按 Acc/RPS 权衡取 0.01。
LEAGUE_TAU = {
    "英超": 0.01,
    "西甲": 0.01,
    "意甲": 0.01,
    "德甲": 0.01,
    "法甲": 0.01,
}
DEFAULT_TAU = 0.01

# 生产切换评估相关口径（对齐 docs/bayesian_shadow_design.md §七/§八）
MIN_GATE_N = 60        # shadow 显著性最小样本（对齐 bayesian_incremental_ab.MIN_GATE_N=60）
STABLE_ROUNDS_K = 3    # 连续稳定轮数（每轮＝一次每日 --roll），达到即触发「生产切换评估」
MEAN_JUMP_WARN = 1.0   # 均值无阶跃「预警」阈值（宽松，暂只预警非硬门禁；观察数轮后收紧）
STABILITY_HIST_KEEP = 90  # 稳定性轨迹最多保留轮数


# ============================================================
# 路径 / 实验 ID
# ============================================================
def asset_path(league: str) -> Path:
    return ASSETS_DIR / f"bayesian_model_{league}.json"


def ckpt_path(league: str) -> Path:
    return SHADOW_DIR / f"p1b_shadow_{league}_state.json"


def exp_id(league: str) -> str:
    return f"p1b_shadow_{league}"


def stability_path(league: str) -> Path:
    """逐轮稳定性轨迹（后验层快照），供连续 K 轮判定。"""
    return SHADOW_DIR / f"p1b_shadow_{league}_stability.json"


def load_stability_history(league: str) -> list:
    """读取逐轮稳定性轨迹（list[dict]），不存在/损坏时返回空列表。"""
    p = stability_path(league)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def append_stability_round(league: str, entry: dict) -> None:
    """追加一轮后验层快照（保留最近 STABILITY_HIST_KEEP 轮）。"""
    p = stability_path(league)
    hist = load_stability_history(league)
    hist.append(entry)
    hist = hist[-STABILITY_HIST_KEEP:]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 已完赛队列加载（带 match_id，league 用 matches.league 干净名，与训练口径一致）
# ============================================================
def _derive_league(match_type: Optional[str]) -> Optional[str]:
    """从 match_type 前缀（如『英超2026-2027赛季』）派生联赛干净名；无法识别返回 None。"""
    if match_type is None:
        return None
    mt = str(match_type)
    for lg in LEAGUES:
        if mt.startswith(lg):
            return lg
    return None


def load_completed_matches() -> pd.DataFrame:
    """读 matches 表已完赛场次（含赛果），返回归一化 df。

    列：match_id, date, home_team_name, away_team_name, homeGoals, awayGoals, league。
    队名归一化与 bayesian_hierarchical_model.load_match_data 同口径（normalize_team_name）。

    league 兜底：部分写入口（prediction_db_writer 等）只写 match_type、漏写 league 列，
    新赛季比赛 league 恒为 NULL 会被 `league IS NOT NULL` 过滤断粮。此处改为：
    读取全量已完赛（仅需 actual_score 非空），对 league 为空的行用 match_type 前缀派生兜底。
    """
    conn = sqlite3.connect(ODDS_DB_PATH)
    try:
        query = """
            SELECT match_id, match_date AS date, home_team, away_team,
                   actual_score, league, match_type
            FROM matches
            WHERE actual_score IS NOT NULL
            ORDER BY match_date
        """
        df = pd.read_sql_query(query, conn)
    finally:
        conn.close()

    # league 兜底：为空时用 match_type 前缀派生，无法识别的行剔除（shadow 仅处理 5 大联赛）
    empty = df["league"].isna() | (df["league"].astype(str).str.strip() == "")
    df.loc[empty, "league"] = df.loc[empty, "match_type"].apply(_derive_league)
    df = df[df["league"].notna()].copy()

    df["date"] = pd.to_datetime(df["date"], format="mixed")
    df["home_team_name"] = df["home_team"].apply(normalize_team_name)
    df["away_team_name"] = df["away_team"].apply(normalize_team_name)

    home_goals: list = []
    away_goals: list = []
    for score in df["actual_score"]:
        if pd.isna(score):
            home_goals.append(np.nan)
            away_goals.append(np.nan)
            continue
        s = str(score)
        sep = "-" if "-" in s else (":" if ":" in s else None)
        if sep:
            parts = s.split(sep)
            try:
                home_goals.append(int(parts[0].strip()))
                away_goals.append(int(parts[1].strip()))
                continue
            except (ValueError, IndexError):
                pass
        home_goals.append(np.nan)
        away_goals.append(np.nan)

    df["homeGoals"] = home_goals
    df["awayGoals"] = away_goals
    df = df.dropna(subset=["homeGoals", "awayGoals"])
    df["homeGoals"] = df["homeGoals"].astype(int)
    df["awayGoals"] = df["awayGoals"].astype(int)
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ============================================================
# 单联赛滚动吸收
# ============================================================
def roll_league(conn, df: pd.DataFrame, league: str, tau_att: float, tau_def: float) -> Dict:
    lg_df = df[df["league"] == league].sort_values("date").reset_index(drop=True)
    if len(lg_df) == 0:
        return {"league": league, "skipped": True, "reason": "无比赛数据"}

    mpath = asset_path(league)
    if not mpath.exists():
        return {"league": league, "skipped": True, "reason": f"无资产 {mpath.name}"}

    model = BayesianHierarchicalModel.load(str(mpath))

    ckp = ckpt_path(league)
    if ckp.exists():
        flt = BayesianIncrementalFilter.load(str(ckp))
        print(f"[{league}] 从 checkpoint 恢复（游标 {flt._last_absorbed_date}）")
        tau_att, tau_def = flt.tau_att, flt.tau_def  # 恢复后沿用 checkpoint 的 τ
    else:
        flt = BayesianIncrementalFilter(
            model.mu, model.home_adv, model.rho,
            tau_att=tau_att, tau_def=tau_def, league=league,
        )
        flt.init_teams(model.teams, model.attack, model.defense,
                       model.attack_std, model.defense_std)
        print(f"[{league}] 初始化增量 filter（{len(model.teams)} 队，τ_att={tau_att} τ_def={tau_def}）")

    # 进度游标过滤：只吸收「严格晚于游标日期」的场次（游标语义＝当日已全部吸收）
    lg_df["day"] = lg_df["date"].dt.strftime("%Y-%m-%d")
    cur = flt._last_absorbed_date
    is_initial = cur is None
    new_df = lg_df[lg_df["day"] > cur] if cur else lg_df
    x_before = flt._x.copy() if flt._x.size else None  # 本轮吸收前状态（= 上一轮末态）

    n_absorbed = 0
    for _, row in new_df.iterrows():
        h, a = row["home_team_name"], row["away_team_name"]
        mid = str(row["match_id"])
        # 先预测（两臂），再落库，最后增量吸收（严格时序）
        c = model.predict_wdl(h, a)
        t = flt.predict_wdl(h, a)
        record_experiment(
            conn, exp_id(league), mid,
            {"control": [c["lose"], c["draw"], c["win"]],
             "treatment": [t["lose"], t["draw"], t["win"]]},
            served_variant="control", match_id=mid,
        )
        flt.update(h, a, int(row["homeGoals"]), int(row["awayGoals"]),
                   date=row["day"], match_id=mid)
        n_absorbed += 1

    # 原子保存 checkpoint（一次 roll 完整结束后才写）
    flt.save(str(ckp))

    # 后验层快照：均值无阶跃（相邻轮 x 位移 L2）+ 协方差 trace
    x_delta_norm = None
    if x_before is not None and flt._x.size and x_before.shape == flt._x.shape:
        x_delta_norm = float(np.linalg.norm(flt._x - x_before))
    trace_P = float(np.trace(flt._P)) if flt._P.size else None
    trace_diverged = bool(trace_P > TRACE_K * (2 * len(flt.team_index))) if trace_P is not None else None
    # 首轮（冷启动全量吸收）位移=累计漂移，非「单轮阶跃」，不做预警
    mean_jump_warn = bool(
        not is_initial and x_delta_norm is not None and x_delta_norm > MEAN_JUMP_WARN
    )

    print(f"[{league}] 吸收 {n_absorbed} 场，游标 → {flt._last_absorbed_date}，checkpoint 已保存 {ckp.name}")
    return {"league": league, "skipped": False, "absorbed": n_absorbed,
            "num_teams": len(flt.team_index), "cursor": flt._last_absorbed_date,
            "is_initial": is_initial, "x_delta_norm": x_delta_norm,
            "trace_P": trace_P, "trace_diverged": trace_diverged,
            "mean_jump_warn": mean_jump_warn}


# ============================================================
# 稳定判据：后验不发散（trace(P) 阈值）
# ============================================================
def stability_check(league: str, trace_k: float = TRACE_K) -> Dict:
    ckp = ckpt_path(league)
    if not ckp.exists():
        return {"league": league, "stability": "no-checkpoint"}
    st = json.loads(ckp.read_text(encoding="utf-8"))
    P = np.asarray(st["P"], dtype=float)
    T = len(st["team_index"])
    trace = float(np.trace(P))
    limit = trace_k * (2 * T)
    return {
        "league": league,
        "num_teams": T,
        "state_dim": 2 * T,
        "trace_P": round(trace, 4),
        "limit": round(limit, 4),
        "diverged": bool(trace > limit),
        "stability": "DIVERGED" if trace > limit else "STABLE",
        "cursor": st.get("cursor"),
    }


def consecutive_stable_rounds(league: str) -> int:
    """从最新一轮往回数，连续「后验稳定」轮数（每轮＝一次每日 --roll）。

    - absorbed == 0 的空轮跳过不计数、也不打断连续（当日无新完赛，不构成证据也不构成失败）。
    - trace_diverged 为 True 的轮打断连续。
    - 均值无阶跃仅为「预警」，暂不硬打断（先记录后门禁）。
    """
    cnt = 0
    for e in reversed(load_stability_history(league)):
        if e.get("absorbed", 0) <= 0:
            continue
        if e.get("trace_diverged") is True:
            break
        cnt += 1
    return cnt


# ============================================================
# CLI
# ============================================================
def main() -> None:
    ap = argparse.ArgumentParser(description="P1-B shadow 并行（离线滚动 + 稳定判据）")
    ap.add_argument("--init", action="store_true", help="初始化 ab_test_log 表")
    ap.add_argument("--roll", action="store_true", help="滚动吸收已完赛比赛（落库 + 更新 checkpoint）")
    ap.add_argument("--analyze", action="store_true", help="四指标显著性 + 后验稳定性判据")
    ap.add_argument("--league", default=None, help="仅处理指定联赛（默认全部 5 联赛）")
    ap.add_argument("--tau-att", type=float, default=None, help="attack 随机游走转移 std（首次初始化用，默认按 LEAGUE_TAU 逐联赛推荐）")
    ap.add_argument("--tau-def", type=float, default=None, help="defense 随机游走转移 std（首次初始化用，默认按 LEAGUE_TAU 逐联赛推荐）")
    ap.add_argument("--min-n", type=int, default=MIN_GATE_N, help="显著性最小样本数（--analyze 用，默认 60）")
    args = ap.parse_args()

    if not (args.init or args.roll or args.analyze):
        ap.print_help()
        sys.exit(0)

    leagues = [args.league] if args.league else LEAGUES

    if args.init:
        conn = connect_db()
        try:
            init_schema(conn)
            print("[shadow] ab_test_log 表已就绪")
        finally:
            conn.close()

    if args.roll:
        df = load_completed_matches()
        conn = connect_db()
        try:
            summary = []
            for lg in leagues:
                tau = LEAGUE_TAU.get(lg, DEFAULT_TAU)
                t_att = args.tau_att if args.tau_att is not None else tau
                t_def = args.tau_def if args.tau_def is not None else tau
                summary.append(roll_league(conn, df, lg, t_att, t_def))
            print("\n[shadow] 滚动汇总:")
            for s in summary:
                if s.get("skipped"):
                    print(f"  - {s['league']}: 跳过（{s.get('reason')}）")
                    continue
                # 记录一轮后验层快照（供连续 K 轮判定 + 均值无阶跃轨迹）
                append_stability_round(s["league"], {
                    "round_ts": datetime.now().isoformat(timespec="seconds"),
                    "absorbed": s["absorbed"],
                    "cursor": s["cursor"],
                    "num_teams": s["num_teams"],
                    "is_initial": s.get("is_initial", False),
                    "x_delta_norm": round(s["x_delta_norm"], 6) if s["x_delta_norm"] is not None else None,
                    "trace_P": round(s["trace_P"], 4) if s["trace_P"] is not None else None,
                    "trace_diverged": s.get("trace_diverged"),
                    "mean_jump_warn": s.get("mean_jump_warn", False),
                })
                jw = " ⚠均值阶跃预警" if s.get("mean_jump_warn") else ""
                print(f"  - {s['league']}: 吸收 {s['absorbed']} 场，队数 {s['num_teams']}，游标 {s['cursor']}"
                      f"，Δx={s['x_delta_norm'] if s['x_delta_norm'] is None else round(s['x_delta_norm'], 4)}{jw}")
        finally:
            conn.close()

    if args.analyze:
        print("\n[shadow] 稳定判据 + 显著性分析:")
        for lg in leagues:
            st = stability_check(lg)
            ab = analyze(str(ODDS_DB_PATH), exp_id(lg), args.min_n, paired=True)
            mn = (ab.get("tests") or {}).get("mcnemar") or {}
            n_paired = mn.get("n_paired", 0)
            net = mn.get("treatment_only_correct", 0) - mn.get("control_only_correct", 0)
            hist = load_stability_history(lg)
            last = hist[-1] if hist else {}
            consec = consecutive_stable_rounds(lg)
            gate = ab.get("gate")
            posterior_ok = st.get("stability") == "STABLE"
            # 指标层 + 后验层 + 连续K轮 → 生产切换评估触发
            indicator_ok = gate in ("NO-SIGNIFICANT-DIFF", "SIGNIFICANT-WIN") and n_paired >= MIN_GATE_N
            switch_eval = indicator_ok and posterior_ok and consec >= STABLE_ROUNDS_K
            print(f"\n=== {lg} ===")
            print(f"  后验: {st.get('stability')}（trace_P={st.get('trace_P')} / 阈值 {st.get('limit')}）")
            jw = last.get("mean_jump_warn")
            print(f"  均值阶跃: 最近一轮 Δx={last.get('x_delta_norm')}（预警阈值 {MEAN_JUMP_WARN}）"
                  f" → {'⚠预警' if jw else '不预警'}；连续后验稳定 {consec}/{STABLE_ROUNDS_K} 轮")
            print(f"  shadow 配对样本: n_paired={n_paired}，门禁={gate} "
                  f"（McNemar p={mn.get('p', float('nan')):.4f}，treatment 净胜={net:+d}）")
            for v, m in ab.get("metrics", {}).items():
                print(f"    {v:<10} 全量配对: RPS={m.get('rps', float('nan')):.4f} "
                      f"LogLoss={m.get('logloss', float('nan')):.4f} "
                      f"Acc={m.get('accuracy', float('nan')):.4f} "
                      f"DrawRecall={m.get('draw_recall', float('nan')):.4f}")
            print(f"  → 生产切换评估: {'触发（可进入人工复核）' if switch_eval else '未触发'}"
                  f"　[指标层{'✅' if indicator_ok else '❌'} · 后验{'✅' if posterior_ok else '❌'} · 连续轮{'✅' if consec >= STABLE_ROUNDS_K else '❌'}]")


if __name__ == "__main__":
    main()