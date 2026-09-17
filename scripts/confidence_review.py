# -*- coding: utf-8 -*-
"""
confidence_review.py — 模块 A5：可信度分级 + 人工审核流程（P1）

===============================================
背景（模型改进实施方案 v1.0 §三/A5，对齐指南 §2.3/§2.4）：
  可信度分级（以方案为准，post_match_review 表注释「3 级起写因子库」与方案冲突时以方案「≥4 级」为准）：
    1 级 = 纯模型归因，无外部数据验证        → 不写因子库
    2 级 = 模型归因 + 部分结构化外部数据验证  → 不写因子库
    3 级 = 模型归因 + 结构化外部数据完全验证  → 待人工审核
    4 级 = 3 级 + 人工审核确认               → 可写入因子库
    5 级 = 4 级 + 同类（同联赛同主因）≥3 场重复验证 → 可写入因子库
  人工审核流程：
    同意   → 升 4 级（同类 ≥3 场自动升 5）+ 写因子库（feature_insights.json，按 match_id 去重）
    不同意 → 备注写入 data/knowledge_base/human_corrections.json（衔接 B4），触发重归因提示
    部分同意 → 人工指定级别 + 备注
  审核效率目标：每天 10 场 ≤15 分钟（--list 默认前 10 场）
  飞书推送（lark-im）：项目当前未接入，--notify 预留钩子（文档标注待接入），不引入新依赖

基础设施约定（§1.3）：
  - 数据库路径动态定位：Path(__file__).resolve().parent.parent / data / odds.db
  - odds.db 连接必须 PRAGMA busy_timeout = 5000
  - 写库幂等：UPDATE 白名单列（human_reviewed/human_notes/reviewed_at/confidence_level），
    因子库与修正记录均按 match_id 去重

用法：
  python scripts/confidence_review.py --list [--date YYYY-MM-DD] [--limit 10] [--include-reviewed]
  python scripts/confidence_review.py --show <match_id>
  python scripts/confidence_review.py --approve <match_id> [--note 备注] [--notify]
  python scripts/confidence_review.py --partial <match_id> --level N [--note 备注]
  python scripts/confidence_review.py --disagree <match_id> --note 备注 [--correction-type TYPE] [--corrected-attribution 文本]
  python scripts/confidence_review.py --stats
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

# B1 统一模块：知识库读写统一走 knowledge_base_schema（避免双写路径）
from knowledge_base_schema import (
    add_correction,
    add_insight,
    count_corrections,
    count_insights,
    parse_attribution,
    primary_cause,
)

DEFAULT_LIST_LIMIT = 10  # 每天审核 10 场目标
LEVEL_APPROVE = 4        # 人工确认 → 4 级
LEVEL_REPEAT_5 = 5       # 同类 ≥3 场重复验证 → 5 级


# ==================== 连接 ====================
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ==================== 基线分级（未人工审核时最大 3 级） ====================
def _parse_attr(attr: Any) -> Optional[Dict[str, Any]]:
    """attribution_json 解析为统一 dict 结构（B1 统一模块 parse_attribution 的本地别名）。"""
    return parse_attribution(attr)


def baseline_level(review: Dict[str, Any]) -> Dict[str, Any]:
    """由现有数据推断未审核前的基线可信度（1-3 级，对齐方案分级语义）。

    - 无 attribution_json → 1 级（纯模型 + 赛果确认，无外部数据归因）
    - 有 attribution_json：
      - 归因证据完整（A3 confidence_level>=3 或命中维度>=3）→ 3 级（完整外部数据）
      - 其余 → 2 级（部分外部数据）
    返回 {"level": int, "reason": str}。
    """
    parsed = _parse_attr(review.get("attribution_json"))
    if parsed is None:
        return {"level": 1, "reason": "无 AI 归因（attribution_json 空），纯模型+赛果确认"}
    n_attrs = len(parsed.get("attributions") or [])
    # 仅「模型局限性归因」且权重≥99 = A3 判定无显著归因（历史样本不足），按 1 级
    only_model_limit = n_attrs == 1 and parsed["attributions"][0].get("type") == "模型局限性归因" \
        and float(parsed["attributions"][0].get("weight", 0)) >= 99
    if only_model_limit:
        return {"level": 1, "reason": "A3 判定无显著归因（模型局限性 weight≥99），纯模型"}
    if n_attrs >= 3 or parsed.get("confidence_level", 0) >= 3:
        return {"level": 3, "reason": f"归因完整（{n_attrs} 维命中），结构化外部数据验证充分"}
    if n_attrs >= 1:
        return {"level": 2, "reason": f"归因部分（{n_attrs} 维命中），仅部分外部数据验证"}
    return {"level": 1, "reason": "归因为空，按纯模型处理"}


# ==================== 同类重复验证 → 5 级 ====================
def _primary_cause(attr: Any) -> Optional[str]:
    """归因主因（B1 统一模块 primary_cause 的本地别名）。"""
    return primary_cause(attr)


def check_repeat_level5(conn: sqlite3.Connection, league: str, primary_cause: Optional[str]) -> bool:
    """同类（同联赛同主因）已人工审核确认（>=4 级）场次 >=3 → 满足 5 级重复验证。"""
    if not primary_cause:
        return False
    cur = conn.cursor()
    # 同联赛已审核确认（>=4 级）场次全量取回，按主因逐一解析统计
    cur.execute(
        "SELECT attribution_json FROM post_match_review "
        "WHERE league=? AND human_reviewed=1 AND confidence_level>=4",
        (league,),
    )
    n_same = 0
    for (attr_json,) in cur.fetchall():
        if _primary_cause(attr_json) == primary_cause:
            n_same += 1
    return n_same >= 3


# ==================== 因子库/修正记录写入（B1 统一：knowledge_base_schema.py） ====================
# A5 不再本地落盘——add_insight / add_correction 统一走 B1 模块
# （confidence>=4 写入门禁 + 按 match_id 去重幂等 + wrapper schema 迁移）。


# ==================== 审核动作 ====================
def get_review(conn: sqlite3.Connection, match_id: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM post_match_review WHERE match_id=?", (match_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def _apply_review(
    conn: sqlite3.Connection,
    review: Dict[str, Any],
    level: int,
    notes: str,
) -> None:
    """写回白名单列：human_reviewed / reviewed_at / confidence_level / human_notes。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    cur.execute(
        "UPDATE post_match_review SET human_reviewed=1, reviewed_at=?, "
        "confidence_level=?, human_notes=? WHERE match_id=?",
        (now, level, notes or None, review["match_id"]),
    )
    conn.commit()


def cmd_approve(conn: sqlite3.Connection, match_id: str, note: Optional[str], notify: bool) -> int:
    review = get_review(conn, match_id)
    if review is None:
        print(f"❌ post_match_review 无 {match_id}，请先跑 A2/A4 采集复盘")
        return 1
    if review.get("human_reviewed"):
        print(f"⚠️ {match_id} 已审核（level={review.get('confidence_level')}），幂等跳过")
        return 0

    base = baseline_level(review)
    primary = _primary_cause(review.get("attribution_json"))
    # 同类重复验证 → 5 级
    level = LEVEL_APPROVE
    if check_repeat_level5(conn, review.get("league"), primary):
        level = LEVEL_REPEAT_5
        print(f"  ✅ 同类（{review.get('league')}｜{primary}）≥3 场重复验证，升 5 级")

    _apply_review(conn, review, level, note)
    print(f"✅ 已审核同意 {match_id}：基线 {base['level']} 级（{base['reason']}）→ {level} 级")

    # ≥4 级写因子库（B1 统一模块）
    written = add_insight(
        match_id, review.get("league"), level,
        review.get("attribution_json"), review.get("match_date"), note,
    )
    print(f"  {'📦 因子库已写入' if written else '  （因子库跳过或已存在）'}")

    if notify:
        push_lark_notify(match_id, f"复盘已审核通过（{level} 级）")
    return 0


def cmd_partial(conn: sqlite3.Connection, match_id: str, level: int, note: Optional[str]) -> int:
    review = get_review(conn, match_id)
    if review is None:
        print(f"❌ post_match_review 无 {match_id}")
        return 1
    if review.get("human_reviewed"):
        print(f"⚠️ {match_id} 已审核，幂等跳过（如需改级别请先确认再操作）")
        return 0
    if not (1 <= level <= 5):
        print("❌ --level 必须在 1-5 之间")
        return 1
    notes = f"[部分同意→{level}级]" + (f" {note}" if note else "")
    _apply_review(conn, review, level, notes)
    print(f"✅ 已部分同意 {match_id}：人工指定 {level} 级")
    # 部分同意且 >=4 级同样写因子库（人工已确认该结论，B1 统一模块）
    if level >= 4:
        written = add_insight(
            match_id, review.get("league"), level,
            review.get("attribution_json"), review.get("match_date"), notes,
        )
        print(f"  {'📦 因子库已写入' if written else '  （因子库跳过或已存在）'}")
    else:
        print("  ⚠️ <4 级不写因子库")
    return 0


def cmd_disagree(
    conn: sqlite3.Connection,
    match_id: str,
    note: str,
    correction_type: str,
    corrected_attribution: Optional[str],
) -> int:
    review = get_review(conn, match_id)
    if review is None:
        print(f"❌ post_match_review 无 {match_id}")
        return 1
    if not note:
        print("❌ --disagree 必须提供 --note 备注")
        return 1
    if review.get("human_reviewed"):
        print(f"⚠️ {match_id} 已审核，幂等跳过")
        return 0

    orig = _primary_cause(review.get("attribution_json")) or "无归因"
    written = add_correction(
        match_id, review.get("league"), correction_type,
        orig, corrected_attribution, note,
    )
    base = baseline_level(review)
    # 不同意：记录修正，保持基线（不升 4 级），标记已审
    _apply_review(conn, review, base["level"],
                  f"[不同意] {note}" + (f" 修正归因：{corrected_attribution}" if corrected_attribution else ""))
    print(f"✅ 已审核不同意 {match_id}：保持基线 {base['level']} 级，修正记录 {'已写入' if written else '已存在'}")
    print(f"  🔁 建议重跑 A3 重归因：python scripts/attribution_engine.py --match-id {match_id} --collect --write")
    return 0


# ==================== 待审列表 ====================
def cmd_list(conn: sqlite3.Connection, date: Optional[str], limit: int, include_reviewed: bool) -> None:
    where = "" if include_reviewed else "WHERE human_reviewed=0"
    params: List[Any] = []
    if date:
        prefix = "WHERE " if not where else "WHERE " + where[len("WHERE "):] + " AND "
        where = f"{where} AND match_date=?" if where else "WHERE match_date=?"
        params = [date]
    cur = conn.cursor()
    cur.execute(
        f"SELECT match_id, match_date, league, home_team, away_team, actual_score, "
        f"actual_wdl, wdl_correct, human_reviewed, confidence_level, attribution_json "
        f"FROM post_match_review {where} ORDER BY match_date DESC, match_id LIMIT ?",
        params + [limit],
    )
    rows = cur.fetchall()
    if not rows:
        print("（无待审场次）")
        return
    title = f"待审列表（前 {limit} 场，目标每日 10 场 ≤15 分钟）" if not include_reviewed else f"列表（前 {limit} 场）"
    print(f"=== {title} ===")
    for i, r in enumerate(rows, 1):
        match_id, mdate, league, home, away, score, awdl, wdl_c, reviewed, cl, attr = r
        if reviewed:
            flag = f"已审 level={cl}"
        else:
            base = baseline_level({"attribution_json": attr})
            flag = f"待审 基线{base['level']}级"
        attr_flag = "有归因" if attr else "无归因"
        print(
            f"{i:>2}. {mdate} [{league}] {home} vs {away} "
            f"{score or '?'} ({awdl or '?'}) hit={wdl_c} ｜ {flag} ｜ {attr_flag}"
        )


def cmd_show(conn: sqlite3.Connection, match_id: str) -> None:
    review = get_review(conn, match_id)
    if review is None:
        print(f"❌ post_match_review 无 {match_id}")
        return
    base = baseline_level(review)
    print(f"match_id      = {review['match_id']}")
    print(f"date/league   = {review.get('match_date')} / {review.get('league')}")
    print(f"teams         = {review.get('home_team')} vs {review.get('away_team')}")
    print(f"actual        = {review.get('actual_score')}（{review.get('actual_wdl')}）")
    print(f"wdl_correct   = {review.get('wdl_correct')}  rps={review.get('single_rps')}  rank={review.get('prob_rank')}")
    print(f"基线分级       = {base['level']} 级（{base['reason']}）")
    print(f"人工审核       = {review.get('human_reviewed')}  reviewed_at={review.get('reviewed_at')}")
    print(f"当前级别       = {review.get('confidence_level')}")
    print(f"human_notes   = {review.get('human_notes')}")
    attr = review.get("attribution_json")
    if attr:
        print(f"--- attribution_json ---")
        print(attr if len(attr) < 1200 else attr[:1200] + "…")
    else:
        print("--- attribution_json 空：可先跑 A3 补归因 ---")


# ==================== 统计（对齐 B5 指标） ====================
def cmd_stats(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM post_match_review")
    total = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM post_match_review WHERE human_reviewed=1")
    reviewed = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*) FROM post_match_review "
        "WHERE human_reviewed=1 AND confidence_level>=4"
    )
    high_conf = cur.fetchone()[0]
    approve_rate = (high_conf / reviewed) if reviewed else 0.0
    corrections = count_corrections()
    correction_rate = (corrections / reviewed) if reviewed else 0.0
    insights = count_insights()
    print("=== 人工审核统计（对齐 B5 看板指标）===")
    print(f"复盘总数       = {total}")
    print(f"已审 / 待审    = {reviewed} / {total - reviewed}")
    print(f"4-5 级条目数   = {high_conf}（占比 {high_conf / total:.0%}，目标 ≥30%）")
    print(f"审核通过率     = {approve_rate:.0%}（目标 ≥80%）")
    print(f"人工修正记录   = {corrections}（修正率 {correction_rate:.0%}，目标 ≤20%）")
    print(f"因子库条目数   = {insights}（≥4 级写入）")
    if high_conf / total < 0.3:
        print("  ⚠️ 4-5 级占比未达 30%，需持续审核升级")


# ==================== 飞书推送钩子（预留，待接入 lark-im） ====================
def push_lark_notify(match_id: str, message: str) -> None:
    """飞书推送钩子（预留接口）。

    项目当前未接入 lark-im 技能（无脚本/依赖）。接入时在此调用飞书 webhook 或 lark-im
    脚本推送复盘报告链接 + 审核结论；未接入时仅打印占位信息，不阻断本地审核闭环。
    """
    print(f"  📤 [飞书推送待接入 lark-im] {match_id}：{message}")


# ==================== 主入口 ====================
def main() -> None:
    parser = argparse.ArgumentParser(description="模块 A5：可信度分级 + 人工审核流程")
    parser.add_argument("--list", action="store_true", help="待审列表（默认前 10 场）")
    parser.add_argument("--date", help="按比赛日筛选（--list 用）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIST_LIMIT, help="列表条数")
    parser.add_argument("--include-reviewed", action="store_true", help="列表含已审场次")
    parser.add_argument("--show", help="查看单场详情与基线分级")
    parser.add_argument("--approve", help="同意审核 → 升 4 级（同类≥3 场自动升 5）+ 写因子库")
    parser.add_argument("--partial", help="部分同意 → 人工指定级别")
    parser.add_argument("--level", type=int, help="--partial 指定级别 1-5")
    parser.add_argument("--disagree", help="不同意 → 写 human_corrections.json + 触发重归因提示")
    parser.add_argument("--note", help="人工审核备注")
    parser.add_argument("--correction-type", default="attribution",
                        help="--disagree 修正类型（默认 attribution）")
    parser.add_argument("--corrected-attribution", help="--disagree 修正后的归因主因")
    parser.add_argument("--notify", action="store_true", help="触发飞书推送钩子（当前占位待接入）")
    parser.add_argument("--stats", action="store_true", help="审核统计（B5 指标）")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        if args.stats:
            cmd_stats(conn)
        elif args.list:
            cmd_list(conn, args.date, args.limit, args.include_reviewed)
        elif args.show:
            cmd_show(conn, args.show)
        elif args.approve:
            sys.exit(cmd_approve(conn, args.approve, args.note, args.notify))
        elif args.partial:
            sys.exit(cmd_partial(conn, args.partial, args.level, args.note))
        elif args.disagree:
            sys.exit(cmd_disagree(conn, args.disagree, args.note,
                                  args.correction_type, args.corrected_attribution))
        else:
            parser.print_help()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
