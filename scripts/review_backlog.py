# -*- coding: utf-8 -*-
"""
review_backlog.py — 模块 P1-A：人工审核待办（backlog）流转工具

===============================================
背景（模型优化评估报告 v2.0 §P1-A）：
  知识库内容建设依赖「≥4 置信度人工审核条目积累」，瓶颈在人工审核 backlog
  流转。本工具**只加速流转，不代人审核**：审核动作（同意/部分同意/不同意）仍由
  confidence_review.py（模块 A5）执行，本工具负责把待审场次排队、排序、分桶、
  统计老化，并输出「今日审核批」可直接复制粘贴的审核命令，让人工把 15 分钟时间
  花在决策上，而非「该审哪场、怎么找命令」上。

职责边界（严格只读，不改 human_reviewed/confidence_level 等任何审核态）：
  - --queue   待审 backlog 优先级队列（可立即审核 / 待补归因 两段）
  - --aging   老化分布（待审天数分桶），暴露沉积风险
  - --stats   待审全景（总数 / 可审 / 待归因 / 按联赛 / 按基线级别）
  - --pick N  生成今日审核批 → 控制台 + docs/review_queue/{date}_queue.md
              （含 match_id + confidence_review.py 直接命令，复制即用）

排序策略（透明可解释，FIFO 为主防沉积，辅以误差优先）：
  - 默认按 match_date 升序（最旧先审，阻止 backlog 老化沉积）
  - --order error 按 single_rps 降序（误差大优先，知识价值更高）
  - --order level  按基线可信度级别降序（越接近可写因子库的优先）
  - --order league 按联赛分组

数据源：data/odds.db post_match_review（human_reviewed=0）
基线分级：复用 confidence_review.baseline_level（A5 同一口径，避免双实现）

基础设施约定（§1.3）：
  - 路径 Path(__file__) 动态定位，禁硬编码盘符
  - odds.db 连接 PRAGMA busy_timeout = 5000
  - 纯读：不写回 post_match_review，不写 knowledge_base

用法：
  python scripts/review_backlog.py --stats
  python scripts/review_backlog.py --queue [--order date|error|level|league] [--limit N]
  python scripts/review_backlog.py --aging
  python scripts/review_backlog.py --pick 10
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"
QUEUE_DIR = PROJECT_DIR / "docs" / "review_queue"

# 老化分桶阈值（天）
AGING_BUCKETS = [
    ("0-1 天", 0, 1),
    ("1-3 天", 1, 3),
    ("3-7 天", 3, 7),
    ("7-30 天", 7, 30),
    ("30+ 天", 30, None),
]
DEFAULT_PICK = 10  # 每日审核目标（对齐 confidence_review.DEFAULT_LIST_LIMIT）


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _baseline_level(attr: Any) -> Dict[str, Any]:
    """复用 A5 基线分级口径（confidence_review.baseline_level），避免双实现。

    导入失败时兜底返回 1 级（不阻断 backlog 工具）。
    """
    try:
        from confidence_review import baseline_level
        return baseline_level({"attribution_json": attr})
    except Exception:  # pragma: no cover - 独立运行兜底
        if attr:
            return {"level": 2, "reason": "有归因（分级模块不可用，粗略判定）"}
        return {"level": 1, "reason": "无归因"}


def load_pending(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """读全部待审场次（human_reviewed=0），附基线分级 + 待审天数。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT match_id, match_date, league, home_team, away_team, actual_score, "
        "actual_wdl, wdl_correct, single_rps, single_logloss, prob_rank, "
        "attribution_json, confidence_level, data_quality_score, created_at "
        "FROM post_match_review WHERE human_reviewed=0"
    )
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    today = datetime.now().date()
    for r in rows:
        r["base"] = _baseline_level(r.get("attribution_json"))
        r["ready"] = bool(r.get("attribution_json"))
        r["aging_days"] = _aging_days(r.get("match_date"), today)
    return rows


def _aging_days(match_date: Optional[str], today) -> int:
    """待审天数（自比赛日起）。无效日期返回 0。"""
    if not match_date:
        return 0
    try:
        d = datetime.strptime(str(match_date)[:10], "%Y-%m-%d").date()
        return max(0, (today - d).days)
    except ValueError:
        return 0


def _sort_key(order: str, r: Dict[str, Any]):
    """按指定维度构造排序键；date 默认 FIFO 最旧优先。"""
    if order == "error":
        rps = float(r.get("single_rps") or 0.0)
        return (-rps, r.get("match_date") or "", r.get("match_id") or "")
    if order == "level":
        return (-int(r["base"]["level"]), r.get("match_date") or "", r.get("match_id") or "")
    if order == "league":
        return (r.get("league") or "—", r.get("match_date") or "", r.get("match_id") or "")
    return (r.get("match_date") or "", r.get("match_id") or "")


def _fmt_line(r: Dict[str, Any]) -> str:
    """单条待审场次一行摘要（含基线分级 + 老化 + 误差标记）。"""
    mdate = r.get("match_date") or "?"
    lg = r.get("league") or "—"
    home = r.get("home_team") or "?"
    away = r.get("away_team") or "?"
    score = r.get("actual_score") or "?"
    awdl = r.get("actual_wdl") or "?"
    hit = "✓" if r.get("wdl_correct") else "✗"
    rps = r.get("single_rps")
    rps_s = f"RPS={rps:.3f}" if rps is not None else "RPS=?"
    level = r["base"]["level"]
    aging = r["aging_days"]
    flag = "🔥" if aging >= 7 else ""
    return (
        f"{mdate} [{lg}] {home} vs {away} {score}（{awdl}）hit={hit} ｜ "
        f"基线{level}级 ｜ {rps_s} ｜ 待审{aging}天{flag}"
    )


def cmd_stats(rows: List[Dict[str, Any]]) -> None:
    """待审全景：总数 / 可审 / 待归因 / 按联赛 / 按基线级别。"""
    total = len(rows)
    ready = [r for r in rows if r["ready"]]
    blocked = [r for r in rows if not r["ready"]]
    by_league: Dict[str, int] = {}
    by_level: Dict[int, int] = {}
    for r in rows:
        lg = r.get("league") or "—"
        by_league[lg] = by_league.get(lg, 0) + 1
        lv = r["base"]["level"]
        by_level[lv] = by_level.get(lv, 0) + 1
    print("=== 人工审核待办（backlog）全景 ===")
    print(f"待审总数       = {total}")
    print(f"  可立即审核   = {len(ready)}（有 attribution_json）")
    print(f"  待补归因     = {len(blocked)}（无归因，需先跑 attribution_engine.py）")
    print(f"按联赛         = {dict(sorted(by_league.items()))}")
    print(f"按基线级别     = {dict(sorted(by_level.items()))}")
    if blocked:
        print("\n  → 待补归因场次说明：先跑 A3 补归因，再回到本工具 --queue 进入审核队列。")
    if not rows:
        print("  （当前无待审场次，backlog 已清空或尚未产生复盘数据）")


def cmd_aging(rows: List[Dict[str, Any]]) -> None:
    """老化分布（待审天数分桶），暴露沉积风险。"""
    print("=== 待审老化分布 ===")
    if not rows:
        print("  （无待审场次）")
        return
    print(f"{'桶':<10} {'场次':>5} {'占比':>7}")
    print("-" * 26)
    for label, lo, hi in AGING_BUCKETS:
        if hi is None:
            n = sum(1 for r in rows if r["aging_days"] >= lo)
        else:
            n = sum(1 for r in rows if lo <= r["aging_days"] < hi)
        pct = n / len(rows) if rows else 0.0
        mark = "  ⚠️ 沉积风险" if n > 0 and (lo >= 7) else ""
        print(f"{label:<10} {n:>5} {pct:>6.0%}{mark}")
    stale = sum(1 for r in rows if r["aging_days"] >= 7)
    if stale:
        print(f"\n⚠️ {stale} 场待审超过 7 天，建议优先处置（--order date 最旧优先）。")


def cmd_queue(rows: List[Dict[str, Any]], order: str, limit: Optional[int]) -> None:
    """待审队列：可立即审核（按排序）在前，待补归因在后。"""
    ready = [r for r in rows if r["ready"]]
    blocked = [r for r in rows if not r["ready"]]
    ready.sort(key=lambda r: _sort_key(order, r))
    blocked.sort(key=lambda r: _sort_key(order, r))
    if limit:
        ready = ready[:limit]

    order_label = {"date": "最旧优先(FIFO)", "error": "误差大优先", "level": "基线级别高优先", "league": "按联赛"}
    print(f"=== 待审队列（排序：{order_label.get(order, order)}）===")
    print(f"可立即审核 {len(ready)} 场 / 待补归因 {len(blocked)} 场\n")
    if ready:
        print("【A. 可立即审核】（有归因，可直接进 confidence_review.py）")
        for i, r in enumerate(ready, 1):
            print(f"  {i:>2}. {_fmt_line(r)}")
            print(f"      match_id = {r['match_id']}")
    else:
        print("【A. 可立即审核】（无）")
    if blocked:
        print("\n【B. 待补归因】（无 attribution_json，先跑 A3 再回队列）")
        for i, r in enumerate(blocked, 1):
            print(f"  {i:>2}. {_fmt_line(r)}")
            print(f"      match_id = {r['match_id']}")
            print(f"      → python scripts/attribution_engine.py --match-id {r['match_id']} --collect --write")
    print("\n→ 审核动作命令：")
    print("  python scripts/confidence_review.py --show <match_id>     # 看详情")
    print("  python scripts/confidence_review.py --approve <match_id>  # 同意（升4级+写因子库）")
    print("  python scripts/confidence_review.py --partial <match_id> --level N  # 部分同意")
    print("  python scripts/confidence_review.py --disagree <match_id> --note 备注  # 不同意")


def cmd_pick(rows: List[Dict[str, Any]], n: int) -> None:
    """生成今日审核批：前 N 场可审 match_id + 复制即用命令，落盘 md。"""
    ready = sorted([r for r in rows if r["ready"]], key=lambda r: _sort_key("date", r))[:n]
    date = datetime.now().strftime("%Y-%m-%d")
    print(f"=== 今日审核批（{date}，前 {n} 场可审）===")
    if not ready:
        print("  （无可审场次：backlog 为空或全部待补归因）")
        return
    lines: List[str] = [
        f"# 人工审核批 — {date}",
        "",
        f"> 生成：`review_backlog.py --pick {n}`（背景：模型优化评估报告 v2.0 P1-A）",
        f"> 待审总数 {len(rows)}，本批取前 {len(ready)} 场可审（最旧优先）。",
        "",
        "| # | match_id | 联赛 | 对阵 | 基线级别 | RPS | 待审天数 |",
        "|---|----------|------|------|:-------:|:----:|:-------:|",
    ]
    cmds: List[str] = []
    for i, r in enumerate(ready, 1):
        lines.append(
            f"| {i} | `{r['match_id']}` | {r.get('league') or '—'} | "
            f"{r.get('home_team') or '?'} vs {r.get('away_team') or '?'} | "
            f"{r['base']['level']} | {r.get('single_rps') if r.get('single_rps') is not None else '—'} | "
            f"{r['aging_days']} |"
        )
        cmds.append(f"python scripts/confidence_review.py --approve {r['match_id']}")

    lines.append("")
    lines.append("## 逐场审核（复制即用）")
    lines.append("")
    for c in cmds:
        lines.append(f"- `{c}`  # 同意（同类≥3场自动升5级）；如需部分/不同意见下方")
    lines.append("")
    lines.append("```bash")
    lines.append("python scripts/confidence_review.py --show <match_id>       # 先看详情")
    lines.append("python scripts/confidence_review.py --partial <match_id> --level N  # 部分同意")
    lines.append("python scripts/confidence_review.py --disagree <match_id> --note 备注  # 不同意")
    lines.append("```")
    lines.append("")

    out = QUEUE_DIR / f"{date.replace('-', '')}_queue.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")

    for i, r in enumerate(ready, 1):
        print(f"  {i:>2}. [{r['match_id']}] {_fmt_line(r)}")
    print(f"\n✅ 审核批已落盘: {out}")
    print("复制即用审核命令：")
    for c in cmds:
        print(f"  {c}")


def main() -> None:
    parser = argparse.ArgumentParser(description="模块 P1-A：人工审核 backlog 流转工具（只读，不代人审核）")
    parser.add_argument("--stats", action="store_true", help="待审全景统计")
    parser.add_argument("--queue", action="store_true", help="待审队列（默认排序最旧优先）")
    parser.add_argument("--order", default="date", choices=["date", "error", "level", "league"],
                        help="--queue 排序维度（默认 date）")
    parser.add_argument("--limit", type=int, default=None, help="--queue 仅显示前 N 场可审")
    parser.add_argument("--aging", action="store_true", help="待审老化分布")
    parser.add_argument("--pick", type=int, default=None, metavar="N",
                        help="生成今日审核批（前 N 场可审）并落盘 md")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        rows = load_pending(conn)
        if args.stats:
            cmd_stats(rows)
        elif args.aging:
            cmd_aging(rows)
        elif args.pick is not None:
            cmd_pick(rows, args.pick)
        else:
            cmd_queue(rows, args.order, args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()