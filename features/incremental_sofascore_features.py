# -*- coding: utf-8 -*-
"""
SofaScore 赛前特征增量补齐（日常新增场次专用，不重建全表）

背景：
  全量重建（build_sofascore_pre_match_features）18353 场约 88 分钟，
  每日新增场次（如赛前 1~3 天新采集）走全量重建性价比过低。
  本脚本仅对 fbref_match_mapping 中已采集、但 sofascore_team_features
  缺失的比赛增量聚合插入，复用优化后的 precompute_team_views 预分组 +
  aggregate_team_history 新签名（C-20260907-003），PA 特征按真实数据
  单场计算（不再填 0），INSERT OR REPLACE 幂等可重复执行。

用法：
  python features/incremental_sofascore_features.py                # 全部缺失场次
  python features/incremental_sofascore_features.py --since 2026-09-05  # 仅该日之后的缺失场次
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

# ==================== 路径常量 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "features"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from sofascore_pre_match_features import (  # noqa: E402
    aggregate_team_history, build_match_id_cn, load_sofascore_lineups,
    load_sofascore_matches, load_sofascore_player_stats,
    normalize_team_name, precompute_team_views,
)
from player_availability_features import PlayerAvailabilityFeatures  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "odds.db"

# 68 维 SofaScore 特征键（34 维 × 主客），与全量重建列序一致
_SOFA_KEYS = [
    # 全队 23 维
    "sofa_rat_5g", "sofa_xg_5g", "sofa_xa_5g", "sofa_pass_sr_5g",
    "sofa_longball_sr_5g", "sofa_cross_sr_5g", "sofa_dribble_sr_5g",
    "sofa_tackle_5g", "sofa_interception_5g", "sofa_duel_sr_5g",
    "sofa_aerial_sr_5g", "sofa_recovery_5g", "sofa_poss_lost_5g",
    "sofa_big_chance_c_5g", "sofa_big_chance_m_5g", "sofa_sprint_km_5g",
    "sofa_hsr_km_5g", "sofa_total_dist_km_5g", "sofa_gk_saves_5g",
    "sofa_gk_goals_prev_5g", "sofa_clearance_5g",
    "sofa_formation_consistency", "sofa_rat_std_5g",
    # P2-10 位置-specific 11 维
    "sofa_fw_goals_5g", "sofa_fw_assists_5g", "sofa_fw_touches_5g", "sofa_fw_rat_5g",
    "sofa_mf_pass_5g", "sofa_mf_touches_5g", "sofa_mf_rat_5g",
    "sofa_df_interception_5g", "sofa_df_tackle_5g", "sofa_df_duel_sr_5g", "sofa_df_rat_5g",
]

_BASE_COLS = ["event_id", "match_id_cn", "match_date", "league",
              "home_team_cn", "away_team_cn"]

# 98 列 = 6 基础 + 68 SofaScore + 24 PA（feature_keys 已含主客后缀）
_TABLE_COLS = (
    _BASE_COLS
    + [f"{k}_home" for k in _SOFA_KEYS] + [f"{k}_away" for k in _SOFA_KEYS]
    + PlayerAvailabilityFeatures.feature_keys()
)


def _clean(v) -> float:
    """归一化特征值：None/NaN → 0.0（与全量重建 PA fillna(0.0) 行为一致）。"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    return v


def incremental_rebuild(since: str | None = None, n_recent: int = 5,
                        half_life_days: int = 21) -> int:
    """增量补齐缺失场次特征，返回本次插入行数（幂等）。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 1. 加载 + 预分组（复用优化后路径）
    matches_df = load_sofascore_matches(conn)
    stats_df = load_sofascore_player_stats(conn)
    lineups_df = load_sofascore_lineups(conn)
    print(f"[增量特征] 比赛={len(matches_df)} | 球员统计={len(stats_df)} | 阵容={len(lineups_df)}")
    matches_by_team, stats_by_team, lineups_by_team = precompute_team_views(
        matches_df, stats_df, lineups_df)

    # 2. 找缺失场次
    existing = {r[0] for r in conn.execute(
        "SELECT event_id FROM sofascore_team_features").fetchall()}
    missing = matches_df[~matches_df["event_id"].isin(existing)]
    if since:
        missing = missing[missing["match_date"] >= since]
    print(f"[增量特征] 缺失 {len(missing)} 场" + (f"（>= {since}）" if since else ""))
    if missing.empty:
        conn.close()
        return 0

    # 3. 逐场生成（SofaScore 预分组取数 + PA 真实计算）
    pa_gen = PlayerAvailabilityFeatures(db_path=DB_PATH)
    rows = []
    for idx, match in missing.iterrows():
        home_feats = aggregate_team_history(
            match["home_team"], match["match_date"],
            matches_by_team, stats_by_team, lineups_by_team, n_recent, half_life_days)
        away_feats = aggregate_team_history(
            match["away_team"], match["match_date"],
            matches_by_team, stats_by_team, lineups_by_team, n_recent, half_life_days)
        home_pa = pa_gen.compute_team_features(
            match["home_team"], match["match_date"], n_recent, half_life_days)
        away_pa = pa_gen.compute_team_features(
            match["away_team"], match["match_date"], n_recent, half_life_days)

        row = {
            "event_id": match["event_id"],
            "match_id_cn": build_match_id_cn(
                match["match_date"].strftime("%Y-%m-%d"),
                match["home_team_cn"], match["away_team_cn"]),
            "match_date": match["match_date"].strftime("%Y-%m-%d"),
            "league": match["league"],
            "home_team_cn": normalize_team_name(match["home_team_cn"]),
            "away_team_cn": normalize_team_name(match["away_team_cn"]),
        }
        for k in _SOFA_KEYS:
            row[f"{k}_home"] = _clean(home_feats.get(k, 0.0))
            row[f"{k}_away"] = _clean(away_feats.get(k, 0.0))
        for k in PlayerAvailabilityFeatures.feature_keys():
            row[k] = _clean(home_pa.get(k.replace("_home", ""), 0.0)) if k.endswith("_home") \
                else _clean(away_pa.get(k.replace("_away", ""), 0.0))
        rows.append(row)
        print(f"  生成: {row['match_date']} {row['home_team_cn']} vs {row['away_team_cn']} "
              f"(event={row['event_id']})")
    pa_gen.close()

    # 4. 幂等插入（INSERT OR REPLACE 保留旧行、补缺/覆盖）
    cols_sql = ",".join(f'"{c}"' for c in _TABLE_COLS)
    ph = ",".join("?" for _ in _TABLE_COLS)
    sql = f"INSERT OR REPLACE INTO sofascore_team_features ({cols_sql}) VALUES ({ph})"
    cur = conn.executemany(sql, [[r[c] for c in _TABLE_COLS] for r in rows])
    conn.commit()
    inserted = cur.rowcount if cur.rowcount is not None else len(rows)
    print(f"[增量特征] ✅ 已写入 {inserted} 行（幂等）")

    # 5. 验证
    after = conn.execute(
        "SELECT COUNT(*) FROM sofascore_team_features").fetchone()[0]
    still_missing = conn.execute(
        "SELECT COUNT(*) FROM fbref_match_mapping fmm "
        "WHERE fmm.fbref_match_url LIKE '%sofascore%' "
        "AND fmm.fbref_match_id NOT IN (SELECT event_id FROM sofascore_team_features)"
    ).fetchone()[0]
    print(f"[增量特征] 表行数={after} | 仍缺失={still_missing}")
    conn.close()
    return inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SofaScore 特征增量补齐")
    parser.add_argument("--since", default=None, help="仅处理该日期(含)之后的缺失场次 YYYY-MM-DD")
    parser.add_argument("--n-recent", type=int, default=5)
    parser.add_argument("--half-life-days", type=int, default=21)
    args = parser.parse_args()
    n = incremental_rebuild(since=args.since, n_recent=args.n_recent,
                            half_life_days=args.half_life_days)
    print(f"本次增量插入: {n} 行")
