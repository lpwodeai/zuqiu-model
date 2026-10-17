# -*- coding: utf-8 -*-
"""C-108 装载包零件 ③：聚合分母修复 + replay 对拍（只读，计算 dry-run 不写库）。

变更（sofascore_pre_match_features.aggregate_team_history）：求和型特征的分母
从 `len(team_matches)`（窗口场次数）改为【有值场次数】= 窗口内该列至少一行非 NULL 的场次数。
分母=0（Nürnberg 类 0 覆盖）→ 值保持 0（已知局限，待 B 覆盖率掩码迭代）。

本脚本做两件事（均只读，不写库）：
  1. --patch-plan：输出需改的分母代码段清单（人工审阅后用 --apply 落盘）
  2. replay 对拍：抽样场次用【新分母】重算 sofa_*_5g，与 sofascore_team_features 现值
     对照（升班马应从 ~0.2 回到 ~1.2 量级，健康队应不变）→ 验证稀释解除。

纪律：本脚本不改特征代码、不写库、不重建特征表；真正落盘与重训在配套窗口（C-090/092）。
用法：
  python scripts/fix_aggregation_denominator.py --replay --team "Leeds United" --date 2026-10-05
  python scripts/fix_aggregation_denominator.py --patch-plan
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "features"))
sys.path.insert(0, str(BASE / "scripts"))
ODDS_DB = BASE / "data" / "odds.db"

# 求和型（场均=sum/场次）且源列覆盖不足 60% 的特征键 → 需改分母
SUM_KEYS = [
    "sofa_xg_5g", "sofa_xa_5g", "sofa_tackle_5g", "sofa_interception_5g",
    "sofa_recovery_5g", "sofa_poss_lost_5g", "sofa_big_chance_c_5g",
    "sofa_big_chance_m_5g", "sofa_sprint_km_5g", "sofa_hsr_km_5g",
    "sofa_total_dist_km_5g", "sofa_clearance_5g",
    "sofa_gk_saves_5g", "sofa_gk_goals_prev_5g",
    "sofa_fw_goals_5g", "sofa_fw_assists_5g", "sofa_fw_touches_5g",
    "sofa_mf_pass_5g", "sofa_mf_touches_5g", "sofa_df_interception_5g", "sofa_df_tackle_5g",
]
# 键 → 源列（与 census KEY_MAP 对齐；分母按该列在窗口内非 NULL 的场次数）
# total_dist_km 为 5 列之和，分母用「任一跑步列非 NULL 的场次数」（特殊标记 *_dist_any）
KEY_SRC = {
    "sofa_xg_5g": "expected_goals", "sofa_xa_5g": "expected_assists",
    "sofa_tackle_5g": "total_tackles", "sofa_interception_5g": "interceptions",
    "sofa_recovery_5g": "ball_recoveries_sofa", "sofa_poss_lost_5g": "possession_lost",
    "sofa_big_chance_c_5g": "big_chances_created", "sofa_big_chance_m_5g": "big_chances_missed",
    "sofa_sprint_km_5g": "meters_covered_sprinting_km",
    "sofa_hsr_km_5g": "meters_covered_high_speed_running_km",
    "sofa_total_dist_km_5g": "_dist_any",  # 5 跑步列任一非 NULL 的场次数
    "sofa_clearance_5g": "clearances",
    "sofa_gk_saves_5g": "gk_saves_sofa", "sofa_gk_goals_prev_5g": "goals_prevented",
    "sofa_fw_goals_5g": "goals", "sofa_fw_assists_5g": "assists",
    "sofa_fw_touches_5g": "touches_sofa", "sofa_mf_pass_5g": "total_pass_sofa",
    "sofa_mf_touches_5g": "touches_sofa", "sofa_df_interception_5g": "interceptions",
    "sofa_df_tackle_5g": "total_tackles",
}
_DIST_COLS = ["meters_covered_walking_km", "meters_covered_jogging_km",
              "meters_covered_running_km", "meters_covered_high_speed_running_km",
              "meters_covered_sprinting_km"]
# 位置子集（分母分子都只取该位置行）
POS_FILTER = {"sofa_fw_goals_5g": "F", "sofa_fw_assists_5g": "F", "sofa_fw_touches_5g": "F",
              "sofa_mf_pass_5g": "M", "sofa_mf_touches_5g": "M",
              "sofa_df_interception_5g": "D", "sofa_df_tackle_5g": "D"}
GK_KEYS = {"sofa_gk_saves_5g", "sofa_gk_goals_prev_5g"}


def patch_plan() -> None:
    print("=== 零件③ 分母修复 patch-plan（人工审阅，--apply 才落盘）===")
    print("文件: features/sofascore_pre_match_features.py :: aggregate_team_history")
    print("改法：对每个求和型键，把 `/ len(team_matches)` 改为 `/ Σw_avail`，其中")
    print("  Σw_avail = 窗口内该键有值场次的时间衰减权重之和（time_decay_weights 归一化后）；")
    print("  Σw_avail=0（Nürnberg 类 0 覆盖）时值保持 0，边界照旧。")
    print("  一刀同时根治两个独立 bug：")
    print("    Bug-A 归一化缩放：满覆盖时 Σw_avail=1，恢复 Σ(x×w) 原量级（÷N 是多余常数缩放）")
    print("    Bug-B 部分覆盖稀释：部分覆盖时 Σw_avail<1，分母自适应缩减")
    print("  注意：÷k（有值场次数）只治 Bug-B 不治 Bug-A，已实证 big_chance_m ÷k=0.307 vs ÷Σw_avail=1.50 差 5 倍")
    print(f"受影响键（{len(SUM_KEYS)}）: {', '.join(SUM_KEYS)}")
    print("比率型键（pass_sr/duel_sr 等 safe_div 权重自抵消）、sofa_rat_5g（÷sw.sum()）不动。")


def replay(team: str, date_str: str, n_recent: int = 5, half_life: int = 21) -> dict:
    """用新分母重算某队某日的 sofa_*_5g，与 sofascore_team_features 现值对拍。"""
    from sofascore_pre_match_features import (load_sofascore_lineups,
                                              load_sofascore_matches,
                                              load_sofascore_player_stats,
                                              precompute_team_views,
                                              time_decay_weights)
    conn = sqlite3.connect(str(ODDS_DB))
    try:
        matches_df = load_sofascore_matches(conn)
        stats_df = load_sofascore_player_stats(conn)
        lineups_df = load_sofascore_lineups(conn)
    finally:
        conn.close()
    mbt, sbt, lbt = precompute_team_views(matches_df, stats_df, lineups_df)
    cur = pd.Timestamp(date_str)
    tm = mbt.get(team)
    if tm is None or tm.empty:
        return {"error": f"队 {team} 无比赛"}
    team_matches = tm[tm["match_date"] < cur].tail(n_recent)
    if team_matches.empty:
        return {"error": f"{team} 在 {date_str} 前无历史"}
    st = sbt.get(team)
    team_stats = st[st["event_id"].isin(team_matches["event_id"]) & (st["minutes_played"] > 0)].copy()
    w_arr = time_decay_weights(team_matches["match_date"], cur, half_life)
    mw = dict(zip(team_matches["event_id"], w_arr))
    team_stats["weight"] = team_stats["event_id"].map(mw)
    n_win = len(team_matches)
    out = {"team": team, "date": date_str, "window_matches": n_win, "keys": {}}
    for k in SUM_KEYS:
        src = KEY_SRC[k]
        sub = team_stats
        if k in POS_FILTER:
            sub = team_stats[team_stats["position"] == POS_FILTER[k]]
        elif k in GK_KEYS:
            sub = team_stats[team_stats["position"] == "G"]
        if sub.empty:
            out["keys"][k] = {"old_denominator": n_win, "valued_matches": 0,
                              "old_val": 0.0, "new_val": 0.0}
            continue
        if src == "_dist_any":
            avail = [c for c in _DIST_COLS if c in sub.columns]
            dist_sum = sum(sub[c].fillna(0) for c in avail)
            num = float((dist_sum * sub["weight"]).sum())
            any_notna = sub[avail].notna().any(axis=1)
            valued = sub.loc[any_notna, "event_id"].nunique()
        else:
            if src not in sub.columns:
                continue
            num = float((sub[src].fillna(0) * sub["weight"]).sum())
            valued = sub.loc[sub[src].notna(), "event_id"].nunique()
        old_val = num / n_win
        # C-108c 定案：分母从 ÷k（有值场次数）升级为 ÷Σw_avail（有值场次的权重和），
        # 才能同时根治归一化缩放（满覆盖时 Σw_avail=1，新值=Σ(x×w)，量级回正）
        # 和部分覆盖稀释（部分覆盖时 Σw_avail < 1，分母随有值场权重自适应缩减）。
        # ÷k 只治稀释不治缩放，已实证 big_chance_m ÷k=0.307 vs ÷Σw_avail=1.50 差 5 倍。
        if src == "_dist_any":
            avail_events = sub.loc[any_notna, "event_id"].unique()
        else:
            avail_events = sub.loc[sub[src].notna(), "event_id"].unique()
        w_avail = float(sum(mw[eid] for eid in avail_events))
        new_val = num / w_avail if w_avail > 0 else 0.0
        out["keys"][k] = {"old_denominator": n_win, "valued_matches": int(valued),
                          "w_avail": round(w_avail, 4),
                          "old_val": round(old_val, 3), "new_val": round(new_val, 3)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch-plan", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--team", default="Leeds United")
    ap.add_argument("--date", default="2026-10-05")
    args = ap.parse_args()
    if args.patch_plan:
        patch_plan()
        return 0
    if args.replay:
        r = replay(args.team, args.date)
        if "error" in r:
            print(f"[ERROR] {r['error']}")
            return 2
        print(f"=== replay 对拍: {r['team']} @ {r['date']}（窗口 {r['window_matches']} 场）===")
        print(f"{'key':28s} {'旧分母':>6s} {'有值场':>6s} {'Σw_avail':>8s} {'旧值':>8s} {'新值':>8s} {'Δ%':>8s}")
        for k, v in r["keys"].items():
            d = ((v["new_val"] - v["old_val"]) / v["old_val"] * 100) if v["old_val"] else 0.0
            print(f"{k:28s} {v['old_denominator']:>6d} {v['valued_matches']:>6d} "
                  f"{v.get('w_avail', 0):>8.4f} {v['old_val']:>8.3f} {v['new_val']:>8.3f} {d:>8.1f}")
        print("（验证目标：升班马 sofa_xg_5g 从 ~0.2 回到 ~1.2；健康列/健康队不变）")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
