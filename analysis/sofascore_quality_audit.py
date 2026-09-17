# -*- coding: utf-8 -*-
"""
SofaScore 数据质量审计脚本
- 审计字段：13 个核心 SofaScore 列非空率（per 联赛 / 全局）
- 阵容质量：阵型非空率 / 首发11人合规率 / 每场队长数=2率 / 换人 sub_reason 非空率
- 事件校验：fbref_match_mapping.fbref_score 比分 vs 球员表 goals 聚合交叉校验
- 产物：控制台 + MD 报告 (docs/audit/sofascore_quality_audit_YYYYMMDD.md)
- 用法：
    python sofascore_quality_audit.py                          # 全库审计 (默认 odds.db)
    python sofascore_quality_audit.py --league 英超             # 仅英超
    python sofascore_quality_audit.py --db /path/to/other.db   # 自定义DB
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ==================== 常量：DB 路径（与 collector 一致） ====================
PROJECT_ROOT = Path(__file__).resolve().parent  # analysis/
MODEL_PROJECT_ROOT = PROJECT_ROOT.parent  # 项目根目录 (五大联赛专属模型/五大联赛专属模型)
DEFAULT_DB_PATH = MODEL_PROJECT_ROOT / "data" / "odds.db"
AUDIT_REPORT_DIR = MODEL_PROJECT_ROOT / "docs" / "audit"

# 13 个核心 SofaScore 扩展列（非空率目标 ≥90%）
AUDIT_CORE_FIELDS: List[str] = [
    "rating",                  # 综合评分（SofaScore 专有，最核心）
    "expected_goals",          # xG
    "expected_assists",        # xA
    "accurate_pass_sofa",      # 准确传球数
    "total_pass_sofa",         # 总传球数
    "accurate_long_balls",     # 准确长传
    "meters_covered_sprinting_km",  # 冲刺距离（跑动类5项中最具代表性）
    "duels_won",               # 对抗胜利
    "aerials_won_total",       # 空中对抗总成功
    "ball_recoveries_sofa",    # 抢回球权
    "possession_lost",         # 丢球
    "big_chances_created",     # 创造绝佳机会
    "gk_saves_sofa",           # 门将扑救（GK 专属，期望非空率 ~2/40=5% 属正常）
]

# DB 表 → 只统计 stats_source='sofascore' 的行
STATS_SOURCE_FILTER = "stats_source = 'sofascore'"
MAPPING_SOURCE_FILTER = "fbref_match_url LIKE '%sofascore%'"


def _pct(num: int, den: int) -> str:
    if den == 0:
        return "N/A"
    return f"{num / den * 100:.1f}% ({num}/{den})"


def _avg(values: List[float]) -> str:
    if not values:
        return "N/A"
    return f"{sum(values) / len(values):.2f}"


def get_db_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def get_leagues(conn: sqlite3.Connection, only_league: Optional[str]) -> List[str]:
    cur = conn.cursor()
    if only_league:
        return [only_league]
    cur.execute(f"SELECT DISTINCT league FROM fbref_match_mapping WHERE {MAPPING_SOURCE_FILTER} ORDER BY league")
    return [row["league"] for row in cur.fetchall()]


def _base_filter_stats(league: Optional[str]) -> Tuple[str, List[Any]]:
    """ 为 match_player_stats 构造基础 WHERE：
        - stats_source='sofascore'
        - 联赛过滤（JOIN mapping）
        - **仅统计有实际出场球员（minutes_played > 0）**：未出场替补无statistics属预期合理
    """
    where = f"WHERE {STATS_SOURCE_FILTER} AND COALESCE(minutes_played, 0) > 0"
    params: List[Any] = []
    if league:
        where += (
            " AND fbref_match_id IN ("
            "   SELECT fbref_match_id FROM fbref_match_mapping "
            f"  WHERE league = ? AND {MAPPING_SOURCE_FILTER})"
        )
        params.append(league)
    return where, params


def audit_field_nonnull(conn: sqlite3.Connection, league: Optional[str]) -> Dict[str, Tuple[int, int]]:
    """ 统计 AUDIT_CORE_FIELDS 每列 (非空行数, 总行数)；league=None 为全局
        **仅统计出场过的球员**（minutes_played > 0）
    """
    where, params = _base_filter_stats(league)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(1) AS total FROM match_player_stats {where}", params)
    total = cur.fetchone()["total"]
    result: Dict[str, Tuple[int, int]] = {}
    for f in AUDIT_CORE_FIELDS:
        cur.execute(
            f"SELECT COUNT(1) AS nonnull FROM match_player_stats {where} "
            f" AND {f} IS NOT NULL AND "
            f" (CASE WHEN typeof({f})='text' THEN {f} != '' ELSE 1 END)",
            params,
        )
        nonnull = cur.fetchone()["nonnull"]
        result[f] = (nonnull, total)
    return result


def audit_player_stats_field_count(conn: sqlite3.Connection, league: Optional[str]) -> Dict[str, List[int]]:
    """ 每名**出场球员**的13核心字段非空数分布（目标≥8） """
    where, params = _base_filter_stats(league)
    sum_case = " + ".join([
        f"(CASE WHEN {f} IS NOT NULL AND "
        f"(CASE WHEN typeof({f})='text' THEN {f} != '' ELSE 1 END) THEN 1 ELSE 0 END)"
        for f in AUDIT_CORE_FIELDS
    ])
    sql = f"SELECT player_name, team, ({sum_case}) AS cnt FROM match_player_stats {where}"
    cur = conn.cursor()
    cur.execute(sql, params)
    buckets: Dict[str, List[int]] = {"<5": [], "5~7": [], "8~10": [], "11~13": []}
    for r in cur.fetchall():
        c = r["cnt"]
        if c < 5:
            buckets["<5"].append(c)
        elif c <= 7:
            buckets["5~7"].append(c)
        elif c <= 10:
            buckets["8~10"].append(c)
        else:
            buckets["11~13"].append(c)
    return buckets


def audit_lineups(conn: sqlite3.Connection, league: Optional[str]) -> Dict[str, Any]:
    where = "WHERE 1=1"
    params: List[Any] = []
    if league:
        where += " AND fbref_match_id IN (SELECT fbref_match_id FROM fbref_match_mapping WHERE league = ? AND " + MAPPING_SOURCE_FILTER + ")"
        params.append(league)
    else:
        where += " AND fbref_match_id IN (SELECT fbref_match_id FROM fbref_match_mapping WHERE " + MAPPING_SOURCE_FILTER + ")"
    cur = conn.cursor()
    # 比赛数 & 球员数
    cur.execute(f"SELECT COUNT(DISTINCT fbref_match_id) AS m_cnt, COUNT(1) AS p_cnt FROM match_lineups {where}", params)
    row = cur.fetchone()
    match_cnt = row["m_cnt"]
    player_cnt = row["p_cnt"]

    # 阵型非空率（每场每队各一条，取 per match 后再聚合）
    cur.execute(f"""
        SELECT COUNT(1) AS total,
               SUM(CASE WHEN formation IS NOT NULL AND formation != '' THEN 1 ELSE 0 END) AS ok
        FROM (SELECT DISTINCT fbref_match_id, team, formation FROM match_lineups {where})
    """, params)
    r = cur.fetchone()
    formation_ok = (r["ok"], r["total"])

    # 首发11人合规率（每场每队首发=11人，则为合规）
    cur.execute(f"""
        SELECT fbref_match_id, team, COUNT(1) AS starters
        FROM match_lineups {where} AND is_starter = 1
        GROUP BY fbref_match_id, team
    """, params)
    team_match_rows = cur.fetchall()
    total_tm = len(team_match_rows)
    ok_tm = sum(1 for r in team_match_rows if r["starters"] == 11)
    starter_ok = (ok_tm, total_tm)

    # 队长标识每场应恰好2人
    cur.execute(f"""
        SELECT fbref_match_id, COUNT(1) AS capt_cnt
        FROM match_lineups {where} AND captain = 1
        GROUP BY fbref_match_id
    """, params)
    capt_rows = cur.fetchall()
    capt_match_total = len(capt_rows)
    capt_match_ok = sum(1 for r in capt_rows if r["capt_cnt"] == 2)
    captain_ok = (capt_match_ok, capt_match_total if capt_match_total else match_cnt)

    # 换人信息完备率（只要 sub_in_time 或 sub_out_time 非空就算有换人记录；sub_reason 非空才算完整）
    cur.execute(f"""
        SELECT COUNT(1) AS has_sub,
               SUM(CASE WHEN sub_reason IS NOT NULL AND sub_reason != '' THEN 1 ELSE 0 END) AS has_reason
        FROM match_lineups {where} AND (sub_in_time IS NOT NULL OR sub_out_time IS NOT NULL)
    """, params)
    r2 = cur.fetchone()
    sub_with_reason = (r2["has_reason"] or 0, r2["has_sub"] or 0)
    sub_total_cnt = (r2["has_sub"] or 0, player_cnt)  # 多少球员实际发生了换人

    return {
        "match_cnt": match_cnt,
        "player_cnt": player_cnt,
        "formation_ok": formation_ok,
        "starter_11_ok": starter_ok,
        "captain_2_ok": captain_ok,
        "sub_with_reason": sub_with_reason,
        "sub_ratio": sub_total_cnt,
    }


def audit_goals_crosscheck(conn: sqlite3.Connection, league: Optional[str]) -> Dict[str, Any]:
    """ 比分 vs 球员表 goals 聚合 交叉校验 """
    where = f"WHERE {MAPPING_SOURCE_FILTER}"
    params: List[Any] = []
    if league:
        where += " AND league = ?"
        params.append(league)
    cur = conn.cursor()
    cur.execute(f"SELECT fbref_match_id, fbref_score, home_team_cn, away_team_cn FROM fbref_match_mapping {where} AND fbref_score IS NOT NULL", params)
    matches = cur.fetchall()
    total = len(matches)
    matched = 0
    mismatches: List[Dict[str, Any]] = []
    for m in matches:
        score = m["fbref_score"]
        if ":" not in str(score):
            continue
        try:
            hg_mp, ag_mp = [int(x.strip()) for x in str(score).split(":")]
        except Exception:
            continue
        mid = m["fbref_match_id"]
        cur.execute(f"""
            SELECT
                (SELECT COALESCE(SUM(goals),0) FROM match_player_stats s
                 WHERE s.fbref_match_id = ? AND s.team = ?) AS hg_ps,
                (SELECT COALESCE(SUM(goals),0) FROM match_player_stats s
                 WHERE s.fbref_match_id = ? AND s.team = ?) AS ag_ps
        """, [mid, m["home_team_cn"], mid, m["away_team_cn"]])
        r = cur.fetchone()
        if (hg_mp, ag_mp) == (r["hg_ps"], r["ag_ps"]):
            matched += 1
        else:
            mismatches.append({
                "match_id": mid,
                "home": m["home_team_cn"],
                "away": m["away_team_cn"],
                "mapping_score": f"{hg_mp}:{ag_mp}",
                "player_stats_sum": f"{r['hg_ps']}:{r['ag_ps']}",
            })
    return {
        "total": total,
        "matched": matched,
        "mismatch_rate": _pct(total - matched, total),
        "top_mismatches": mismatches[:5],
    }


def audit_table_counts(conn: sqlite3.Connection, league: Optional[str]) -> Dict[str, int]:
    """ 4 张表行数统计（全按 fbref_match_mapping JOIN，统一过滤联赛 & 来源） """
    cur = conn.cursor()

    # --- 1) fbref_match_mapping ---
    sql_map = f"SELECT COUNT(1) FROM fbref_match_mapping WHERE {MAPPING_SOURCE_FILTER}"
    params: List[Any] = []
    if league:
        sql_map += " AND league = ?"
        params.append(league)
    cur.execute(sql_map, params)
    mapping_cnt = cur.fetchone()[0]

    # --- 2) fbref_players（SofaScore 注册总人数，不区分联赛） ---
    cur.execute("SELECT COUNT(1) FROM fbref_players WHERE fbref_player_url LIKE '%sofascore%'")
    players_cnt = cur.fetchone()[0]

    # --- 3) match_lineups & match_player_stats: JOIN fbref_match_mapping 统一过滤 ---
    join_where = f"""
      FROM fbref_match_mapping m
      WHERE {MAPPING_SOURCE_FILTER}
    """
    jp: List[Any] = []
    if league:
        join_where += " AND m.league = ?"
        jp.append(league)

    cur.execute(f"SELECT COALESCE(SUM((SELECT COUNT(1) FROM match_lineups l WHERE l.fbref_match_id = m.fbref_match_id)), 0) {join_where}", jp)
    lineups_cnt = cur.fetchone()[0] or 0
    cur.execute(f"SELECT COALESCE(SUM((SELECT COUNT(1) FROM match_player_stats s WHERE s.fbref_match_id = m.fbref_match_id AND s.{STATS_SOURCE_FILTER})), 0) {join_where}", jp)
    stats_cnt = cur.fetchone()[0] or 0

    return {
        "fbref_match_mapping": mapping_cnt,
        "fbref_players": players_cnt,
        "match_lineups": lineups_cnt,
        "match_player_stats": stats_cnt,
    }


def build_report(conn: sqlite3.Connection, only_league: Optional[str]) -> str:
    leagues: List[Optional[str]] = [None]  # None = 全局
    league_list = get_leagues(conn, only_league)
    leagues.extend(league_list)
    lines: List[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"# SofaScore 数据质量审计报告  \n**生成时间**: {now}  \n**DB**: `{DEFAULT_DB_PATH}`\n")
    lines.append("---\n")

    for lg in leagues:
        title = "🌐 全局汇总" if lg is None else f"🏆 联赛：{lg}"
        lines.append(f"## {title}\n")

        # 1) 基础计数
        counts = audit_table_counts(conn, lg)
        lines.append("### 📊 表行数统计\n")
        lines.append("| 表 | 行数 |")
        lines.append("|---|---:|")
        for k, v in counts.items():
            lines.append(f"| `{k}` | {v:,} |")
        lines.append("")

        # 2) 13 核心字段非空率
        lines.append("### 🔬 13 个核心 SofaScore 字段非空率（目标 ≥90%，门将列除外）\n")
        field_res = audit_field_nonnull(conn, lg)
        lines.append("| 字段 | 说明 | 非空率 (非空/总数) | 达标? |")
        lines.append("|---|---|---:|:---:|")
        goalie_special = {"gk_saves_sofa"}
        for f in AUDIT_CORE_FIELDS:
            nonnull, total = field_res[f]
            pct_str = _pct(nonnull, total)
            threshold = 0.5 if f in goalie_special else 0.90
            ok = (nonnull / total >= threshold) if total > 0 else True
            mark = "✅" if ok else "❌"
            lines.append(f"| `{f}` | {f} | {pct_str} | {mark} |")
        lines.append("")

        # 3) 球员字段数分布（人均≥8算合格）
        bucket = audit_player_stats_field_count(conn, lg)
        lines.append("### 📦 每名球员 13 核心字段非空数分布（目标 ≥8）\n")
        lines.append("| 字段数区间 | 球员数 | 占比 |")
        lines.append("|---:|---:|---:|")
        total_players = sum(len(v) for v in bucket.values())
        for k, v in bucket.items():
            lines.append(f"| {k} | {len(v):,} | {_pct(len(v), total_players)} |")
        lines.append("")

        # 4) 阵容质量
        lu = audit_lineups(conn, lg)
        lines.append("### ⚽ 阵容 & 换人质量\n")
        lines.append(f"- 比赛数: **{lu['match_cnt']}** | 阵容球员行数: **{lu['player_cnt']:,}**")
        lines.append(f"- 阵型 `formation` 非空率: **{_pct(*lu['formation_ok'])}** (目标 100%)")
        lines.append(f"- 首发11人合规率 (每场每队恰好11首发): **{_pct(*lu['starter_11_ok'])}** (目标 ≥98%)")
        lines.append(f"- 每场队长数=2 合规率: **{_pct(*lu['captain_2_ok'])}** (目标 100%)")
        lines.append(f"- 发生换人且 `sub_reason` 完备率: **{_pct(*lu['sub_with_reason'])}** (目标 ≥85%)")
        lines.append(f"- 阵容中实际发生换人的球员比例: **{_pct(*lu['sub_ratio'])}**")
        lines.append("")

        # 5) 比分交叉校验
        gc = audit_goals_crosscheck(conn, lg)
        lines.append("### 🎯 比分交叉校验 (mapping.fbref_score vs player_stats.goals 聚合)\n")
        lines.append(f"- 已完成有比分的比赛数: **{gc['total']}** | 完全一致: **{gc['matched']}** | 不一致率: **{gc['mismatch_rate']}**")
        if gc["top_mismatches"]:
            lines.append("\n前5场不一致明细：\n")
            lines.append("| match_id | 主队 | 客队 | mapping比分 | 球员表求和 |")
            lines.append("|---|---|---|---:|---:|")
            for mm in gc["top_mismatches"]:
                lines.append(f"| {mm['match_id']} | {mm['home']} | {mm['away']} | `{mm['mapping_score']}` | `{mm['player_stats_sum']}` |")
            lines.append("")
            lines.append("> 注：轻微不一致通常是**乌龙球**（不计入任何得分球员的 goals）或**点球大战进球**（不计入常规90min），需结合 incidents.json 核实；一般 2-5% 不一致属正常范围。\n")
        lines.append("")

        lines.append("---\n")

    # 6) 全局 QA 总结
    lines.append("## ✅ QA 通过标准（建议全量采集前满足）\n")
    lines.append("- [ ] 全局 `rating` 非空率 ≥ 95%（SofaScore核心评分）")
    lines.append("- [ ] 全局 `expected_goals / expected_assists` 非空率 ≥ 90%")
    lines.append("- [ ] 全局首发11人合规率 ≥ 98%")
    lines.append("- [ ] 全局换人 sub_reason 完备率 ≥ 85%")
    lines.append("- [ ] 全局8~13字段非空人数占比 ≥ 80%")
    lines.append("- [ ] 比分交叉校验不一致率 ≤ 5%")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="SofaScore 数据质量审计 (odds.db)")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB_PATH), help="SQLite DB 路径")
    parser.add_argument("--league", type=str, default=None, help="仅审计某联赛 (如 '英超')")
    parser.add_argument("--report-only", action="store_true", help="仅打印控制台报告，不写MD文件")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] DB not found: {db_path}")
        return 2

    summary_path: Optional[Path] = None  # 关键输出变量先初始化（避免locals()检查坑）
    try:
        conn = get_db_connection(db_path)
        report = build_report(conn, args.league)
        print(report)
        if not args.report_only:
            AUDIT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = f"_{args.league}" if args.league else ""
            summary_path = AUDIT_REPORT_DIR / f"sofascore_quality_audit{suffix}_{stamp}.md"
            summary_path.write_text(report, encoding="utf-8")
            print(f"\n📝 审计报告已保存: {summary_path}")
        conn.close()
        return 0
    except Exception as e:
        print(f"[FATAL] 审计异常: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
