# -*- coding: utf-8 -*-
"""C-107 全列 NULL 率清单（sofa 特征源表覆盖率普查）。

背景（C-100/C-103）：sofa_xg_5g 系统性 ~0.2-0.35（真实 1.2-1.6），C-100 只量了 xG 族。
本脚本补齐「全列」：两张源表逐列覆盖率 + 34 个 _SOFA_KEYS 逐键映射 +
JSON 可回收性（重解析回填 vs 真缺口）+ xG 覆盖率球队/赛季梯度。

口径（advisor 修正）：
  分母双列——all_rows（全表）vs played_rows（minutes_played>0，即聚合函数真实筛选口径）；
  二者之差 = 结构性行缺失（未出场/无数据行），非采集缺口。
  零值率——sofascore_team_features 经 _clean() 把 None→0.0，故该表 NULL 率≈0、
  真正的稀释信号是「恰好 0.0 的占比」。

只读（mode=ro）。产出 scripts/sofa_coverage_census.json + 控制台摘要。
"""
from __future__ import annotations

import collections
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ODDS_DB = BASE / "data" / "odds.db"
OUT = Path(__file__).resolve().parent / "sofa_coverage_census.json"

# 34 键 → match_player_stats 源列（从 sofascore_pre_match_features.aggregate_team_history 逐行提取）
KEY_MAP = {
    "sofa_rat_5g": ["rating"],
    "sofa_xg_5g": ["expected_goals"],
    "sofa_xa_5g": ["expected_assists"],
    "sofa_pass_sr_5g": ["accurate_pass_sofa", "total_pass_sofa"],
    "sofa_longball_sr_5g": ["accurate_long_balls", "total_long_balls"],
    "sofa_cross_sr_5g": ["accurate_crosses", "total_crosses"],
    "sofa_dribble_sr_5g": ["successful_dribbles", "total_dribbles"],
    "sofa_tackle_5g": ["total_tackles"],
    "sofa_interception_5g": ["interceptions"],
    "sofa_duel_sr_5g": ["duels_won", "duels_total"],
    "sofa_aerial_sr_5g": ["aerials_won_total", "aerials_total"],
    "sofa_recovery_5g": ["ball_recoveries_sofa"],
    "sofa_poss_lost_5g": ["possession_lost"],
    "sofa_big_chance_c_5g": ["big_chances_created"],
    "sofa_big_chance_m_5g": ["big_chances_missed"],
    "sofa_sprint_km_5g": ["meters_covered_sprinting_km"],
    "sofa_hsr_km_5g": ["meters_covered_high_speed_running_km"],
    "sofa_total_dist_km_5g": ["meters_covered_walking_km", "meters_covered_jogging_km",
                              "meters_covered_running_km", "meters_covered_high_speed_running_km",
                              "meters_covered_sprinting_km"],
    "sofa_gk_saves_5g": ["gk_saves_sofa"],
    "sofa_gk_goals_prev_5g": ["goals_prevented"],
    "sofa_clearance_5g": ["clearances"],
    "sofa_formation_consistency": [],          # 来自 match_lineups.formation
    "sofa_rat_std_5g": ["rating"],
    "sofa_fw_goals_5g": ["goals"], "sofa_fw_assists_5g": ["assists"],
    "sofa_fw_touches_5g": ["touches_sofa"], "sofa_fw_rat_5g": ["rating"],
    "sofa_mf_pass_5g": ["total_pass_sofa"], "sofa_mf_touches_5g": ["touches_sofa"],
    "sofa_mf_rat_5g": ["rating"],
    "sofa_df_interception_5g": ["interceptions"], "sofa_df_tackle_5g": ["total_tackles"],
    "sofa_df_duel_sr_5g": ["duels_won", "duels_total"], "sofa_df_rat_5g": ["rating"],
}

# 源列 → stats_json 里的 SofaScore camelCase 键（可回收性对拍；键名经真实 JSON 抽样校正）
JSON_KEY = {
    "expected_goals": "expectedGoals", "expected_assists": "expectedAssists",
    "big_chances_created": "bigChanceCreated", "big_chances_missed": "bigChanceMissed",
    "total_tackles": "totalTackle", "interceptions": "interceptionWon",
    "accurate_pass_sofa": "accuratePass", "total_pass_sofa": "totalPass",
    "touches_sofa": "touches", "rating": "rating",
    "gk_saves_sofa": "saves", "goals_prevented": "goalsPrevented",
    "clearances": "totalClearance", "possession_lost": "possessionLostCtrl",
    "ball_recoveries_sofa": "ballRecovery", "duels_won": "duelWon",
    "aerials_won_total": "aerialWon",
    "total_shots": "totalShots", "accurate_long_balls": "accurateLongBalls",
    "total_long_balls": "totalLongBalls", "accurate_crosses": "accurateCross",
    "total_crosses": "totalCross", "successful_dribbles": "wonContest",
    "total_dribbles": "totalContest",
    "meters_covered_sprinting_km": "metersCoveredSprintingKm",
    "meters_covered_high_speed_running_km": "metersCoveredHighSpeedRunningKm",
    "meters_covered_running_km": "metersCoveredRunningKm",
}


def ro(path: Path):
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def census_player_stats(conn) -> tuple[list, list]:
    """单查询全列普查。分母双列（all / played），并区分「非空」与「非零」——
    解析器可能把缺值写成 0 而非 NULL，此时 IS NOT NULL 会虚高（如 interceptions
    列 100% 而源 JSON 键 interceptionWon 仅 40.7%）。"""
    cols = [c[1] for c in conn.execute("PRAGMA table_info('match_player_stats')")
            if c[1] not in ("id",)]
    exprs = ["COUNT(*) AS n_all",
             "SUM(CASE WHEN minutes_played > 0 THEN 1 ELSE 0 END) AS n_played"]
    for i, c in enumerate(cols):
        exprs.append(f'SUM(CASE WHEN minutes_played > 0 AND "{c}" IS NOT NULL THEN 1 ELSE 0 END) AS nn{i}')
        exprs.append(f'SUM(CASE WHEN minutes_played > 0 AND "{c}" = 0 THEN 1 ELSE 0 END) AS z{i}')
    row = conn.execute("SELECT " + ", ".join(exprs) + " FROM match_player_stats").fetchone()
    n_all, n_played = row[0], row[1]
    out = []
    for i, c in enumerate(cols):
        nn, z = row[2 + 2 * i], row[3 + 2 * i]
        out.append({"column": c,
                    "cov_played": round(nn / n_played * 100, 2) if n_played else 0.0,
                    "nonzero_played": round((nn - z) / n_played * 100, 2) if n_played else 0.0,
                    "zero_fill_played": round(z / n_played * 100, 2) if n_played else 0.0})
    out.sort(key=lambda r: r["nonzero_played"])
    return out, [n_all, n_played]


def census_team_features(conn) -> list:
    cols = [c[1] for c in conn.execute("PRAGMA table_info('sofascore_team_features')")]
    feat = [c for c in cols if c not in ("event_id", "match_id_cn", "match_date", "league",
                                         "home_team_cn", "away_team_cn")]
    exprs = ["COUNT(*) AS n"]
    for i, c in enumerate(feat):
        exprs.append(f'SUM(CASE WHEN "{c}" IS NULL THEN 1 ELSE 0 END) AS nl_{i}')
        exprs.append(f'SUM(CASE WHEN "{c}" = 0.0 THEN 1 ELSE 0 END) AS z_{i}')
    row = conn.execute("SELECT " + ", ".join(exprs) + " FROM sofascore_team_features").fetchone()
    n = row[0]
    out = []
    for i, c in enumerate(feat):
        nl, z = row[1 + 2 * i], row[2 + 2 * i]
        out.append({"column": c, "rows": n, "null_rate": round(nl / n * 100, 2),
                    "zero_rate": round(z / n * 100, 2)})
    out.sort(key=lambda r: -r["zero_rate"])
    return out


def json_recoverability(conn, cols: list[str]) -> dict:
    """抽样（rowid % 24 == 0，跨全表均匀）统计源列 NULL 但 JSON 键存在的比例。"""
    keys = {c: JSON_KEY[c] for c in cols if c in JSON_KEY}
    sample = conn.execute(
        "SELECT stats_json FROM match_player_stats "
        "WHERE minutes_played > 0 AND stats_json IS NOT NULL "
        "AND length(stats_json) > 50 AND rowid % 24 = 0").fetchall()
    present = collections.Counter()
    n = 0
    for (js,) in sample:
        try:
            d = json.loads(js)
        except Exception:  # noqa: BLE001
            continue
        if len(d) <= 3:
            continue  # 截断块（仅 2 键）不计入
        n += 1
        for c, jk in keys.items():
            if jk in d and d[jk] is not None:
                present[c] += 1
    return {"sample_n": n, "json_key_presence": {c: round(present[c] / n * 100, 2)
                                                  for c in keys} if n else {}}


def xg_gradient(conn) -> dict:
    per_team = conn.execute(
        "SELECT team, COUNT(*) n, "
        "SUM(CASE WHEN expected_goals IS NOT NULL THEN 1 ELSE 0 END) nn "
        "FROM match_player_stats WHERE minutes_played > 0 "
        "GROUP BY team HAVING n >= 200 ORDER BY 1").fetchall()
    teams = [{"team": t, "n": n, "cov": round(nn / n * 100, 1)} for t, n, nn in per_team]
    teams.sort(key=lambda r: r["cov"])
    per_season = conn.execute(
        "SELECT CASE WHEN CAST(substr(match_id,6,2) AS INT) >= 7 "
        "THEN substr(match_id,1,4) ELSE CAST(CAST(substr(match_id,1,4) AS INT)-1 AS TEXT) END AS season, "
        "COUNT(*) n, SUM(CASE WHEN expected_goals IS NOT NULL THEN 1 ELSE 0 END) nn "
        "FROM match_player_stats WHERE minutes_played > 0 AND match_id LIKE '____-__-__%' "
        "GROUP BY season ORDER BY season").fetchall()
    seasons = [{"season": s, "n": n, "cov": round(nn / n * 100, 1)} for s, n, nn in per_season]
    return {"per_team_bottom10": teams[:10], "per_team_top10": teams[-10:][::-1],
            "per_season": seasons}


def main() -> int:
    conn = ro(ODDS_DB)
    try:
        cols, [n_all, n_played] = census_player_stats(conn)
        tf = census_team_features(conn)
        rec_cols = sorted({c for v in KEY_MAP.values() for c in v})
        jr = json_recoverability(conn, rec_cols)
        grad = xg_gradient(conn)
    finally:
        conn.close()

    zero_cols = [r for r in cols if r["cov_played"] == 0]
    low_cols = [r for r in cols if 0 < r["nonzero_played"] < 60]
    cons = [r for r in cols if r["nonzero_played"] >= 60]
    ledger = {
        "generated_from": "data/odds.db (mode=ro)",
        "denominators": {"all_rows": n_all, "played_rows(minutes>0)": n_played},
        "metric_note": "cov_played=非空率; nonzero_played=非零率; zero_fill_played=恰为0的占比"
                       "（解析器把缺值写 0 时非空率虚高，须看非零率）",
        "match_player_stats_columns": cols,
        "summary_player_stats": {"total_cols": len(cols), "dead_cols_0cov": len(zero_cols),
                                 "low_nonzero_cols<60pct": len(low_cols),
                                 "healthy_nonzero>=60pct": len(cons)},
        "feature_key_to_source": KEY_MAP,
        "sofascore_team_features_columns": tf,
        "json_recoverability": jr,
        "xg_gradient": grad,
    }
    OUT.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"=== match_player_stats（{n_all} 行 / played {n_played} 行）===")
    print(f"  总列 {len(cols)} | 死列(非空0%) {len(zero_cols)} | 非零<60% {len(low_cols)} | 健康(非零>=60%) {len(cons)}")
    print("  低非零率列（升序，col / 非空 / 非零 / 零填充）:")
    for r in low_cols:
        print(f"    {r['column']:38s} cov={r['cov_played']:6.2f}%  nonzero={r['nonzero_played']:6.2f}%  "
              f"zerofill={r['zero_fill_played']:6.2f}%")
    print("\n=== 34 键 → 源列（played 分母：非空 / 非零）===")
    cmap = {r["column"]: r for r in cols}
    for k, srcs in KEY_MAP.items():
        if not srcs:
            print(f"  {k:28s} ← (lineups.formation)")
            continue
        txt = "  ".join(
            f"{s}={cmap[s]['cov_played']}%/{cmap[s]['nonzero_played']}%" if s in cmap else f"{s}=?"
            for s in srcs)
        print(f"  {k:28s} ← {txt}")
    print(f"\n=== JSON 可回收性（样本 n={jr['sample_n']}）===")
    for c, p in jr["json_key_presence"].items():
        col = cmap.get(c, {})
        print(f"  {c:34s} 列={col.get('cov_played')}%  JSON键={p}%  → 可回收≈"
              f"{round(p - (col.get('cov_played') or 0),1)}pp")
    print(f"\n=== sofascore_team_features（{tf[0]['rows']} 行）零值率 top12 ===")
    for r in tf[:12]:
        print(f"    {r['column']:32s} null={r['null_rate']:6.2f}%  zero={r['zero_rate']:6.2f}%")
    print(f"\n=== xG 覆盖率球队梯度（bottom5 / top5）===")
    for r in grad["per_team_bottom10"][:5]:
        print(f"    低: {r['team']:24s} {r['cov']:5.1f}% (n={r['n']})")
    for r in grad["per_team_top10"][:5]:
        print(f"    高: {r['team']:24s} {r['cov']:5.1f}% (n={r['n']})")
    print("  按赛季:", [(s["season"], s["cov"]) for s in grad["per_season"]])
    print(f"\n台账已写出: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
