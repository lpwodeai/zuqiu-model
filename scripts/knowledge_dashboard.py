# -*- coding: utf-8 -*-
"""
knowledge_dashboard.py — 模块 B5：效果监控看板（P1）

===============================================
背景（模型改进实施方案 v1.0 §四/B5，对齐指南 §3.5）：
  输出 docs/knowledge_dashboard/ 每周更新，监控知识库闭环进化效果。
  六项指标：
    1. 知识库条目数与可信度分布（4-5 级占比 ≥30%）
    2. 人工审核通过率（≥80%）与人工修正率（≤20%）
    3. 各联赛准确率趋势（接入知识库前后）
    4. 模型进化速度（每 30 场 ≥0.5pp）
    5. 知识库命中率（≥90%）
    6. （补充）复盘口径准确率（post_match_review 降级来源，注明口径）

数据源：
  - 知识库条目/可信度分布   → knowledge_base_schema.py 统计函数
  - 各联赛准确率趋势         → assets/final_training_report_*.json 的
                               stratified_evaluation.dimensions.by_league（复用分层评估输出）
  - 人工审核通过率/修正率    → data/odds.db post_match_review（human_reviewed/human_notes）
  - 模型进化速度             → 相邻两次训练报告 overall acc / total_matches 折算每 30 场
  - 知识库命中率             → 无读取埋点（B2 尚未记录每次读取），如实标注 N/A + 建议埋点

基础设施约定（§1.3）：路径 Path(__file__) 动态定位，禁硬编码盘符；
  odds.db 只读连接 busy_timeout=5000；输出不写库，纯读 + 落盘 markdown。

用法：
  python scripts/knowledge_dashboard.py                # 生成今日看板 + 更新 index.md
  python scripts/knowledge_dashboard.py --date YYYY-MM-DD
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
DATA_DIR = PROJECT_DIR / "data"
ASSETS_DIR = PROJECT_DIR / "assets"
DASHBOARD_DIR = PROJECT_DIR / "docs" / "knowledge_dashboard"
DB_PATH = DATA_DIR / "odds.db"

# 阈值（对齐方案 §B5）
TARGET_CONFIDENCE4_5 = 0.30      # 4-5 级占比 ≥30%
TARGET_PASS_RATE = 0.80          # 人工审核通过率 ≥80%
TARGET_CORRECTION_RATE = 0.20    # 人工修正率 ≤20%
TARGET_EVOLUTION = 0.005         # 每 30 场 ≥0.5pp
TARGET_HIT_RATE = 0.90           # 知识库命中率 ≥90%

LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]


# ==================== 数据源 1：知识库 ====================
def _load_kb_stats() -> Dict[str, Any]:
    """知识库条目数 + 可信度分布 + 样本权重/修正条数。"""
    import sys
    sys.path.insert(0, str(SCRIPTS_DIR))
    import knowledge_base_schema as kb

    insights_active = kb.count_insights("active")
    insights_all = kb.count_insights()
    conf_dist = kb.confidence_distribution("feature_insights")
    conf45 = sum(v for k, v in conf_dist.items() if k >= 4)
    total = sum(conf_dist.values())

    sw_active = sum(
        1 for lg in kb.LEAGUES
        for e in kb.load_entries(lg, "sample_weights")
        if e.get("status", "active") == "active"
    )
    corrections_open = kb.count_corrections("open")
    corrections_all = kb.count_corrections()

    by_league: Dict[str, int] = {}
    for lg in LEAGUES:
        n = len(kb.load_entries(lg, "feature_insights"))
        if n:
            by_league[lg] = n
    global_n = len(kb.load_entries("global", "feature_insights"))

    return {
        "insights_active": insights_active,
        "insights_all": insights_all,
        "conf_dist": conf_dist,
        "conf45": conf45,
        "conf45_ratio": (conf45 / total) if total else 0.0,
        "sw_active": sw_active,
        "corrections_open": corrections_open,
        "corrections_all": corrections_all,
        "by_league": by_league,
        "global_n": global_n,
    }


# ==================== 数据源 2：分层评估趋势（复用 stratified_evaluation 输出） ====================
def _load_training_reports() -> List[Dict[str, Any]]:
    """读取 assets/final_training_report_*.json，返回按时间排序的报告列表。

    每份含 timestamp / total_matches / overall_acc / by_league_acc。
    兼容缺失 stratified_evaluation 字段的旧报告（P2-14 前）。
    """
    reports: List[Dict[str, Any]] = []
    for path in sorted(ASSETS_DIR.glob("final_training_report_*.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        se = d.get("stratified_evaluation") or {}
        overall = se.get("overall") or {}
        by_league = (se.get("dimensions") or {}).get("by_league") or {}
        reports.append({
            "timestamp": d.get("timestamp", path.stem),
            "total_matches": int(d.get("total_matches") or 0),
            "overall_acc": float(overall.get("acc")) if overall.get("acc") is not None else None,
            "by_league": {str(lg): float(v.get("acc")) if v and v.get("acc") is not None else None
                          for lg, v in by_league.items()},
        })
    reports.sort(key=lambda r: r["timestamp"])
    return reports


# ==================== 数据源 3：复盘 + 人工审核 ====================
def _load_review_stats() -> Dict[str, Any]:
    """post_match_review 只读统计：联赛分布 / WDL 命中 / 人工审核通过与修正。"""
    stats: Dict[str, Any] = {
        "total": 0, "wdl_correct": 0, "reviewed": 0, "passed": 0, "corrected": 0,
        "by_league": {}, "pass_fail": [],
    }
    if not DB_PATH.exists():
        return stats
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        cur = conn.cursor()
        cur.execute("SELECT league, COUNT(*), SUM(wdl_correct) FROM post_match_review GROUP BY league")
        for lg, n, ok in cur.fetchall():
            stats["by_league"][lg] = {"n": int(n or 0), "wdl_correct": int(ok or 0)}
            stats["total"] += int(n or 0)
            stats["wdl_correct"] += int(ok or 0)
        cur.execute(
            "SELECT match_id, human_reviewed, human_notes FROM post_match_review "
            "WHERE human_reviewed=1"
        )
        for match_id, reviewed, notes in cur.fetchall():
            stats["reviewed"] += 1
            notes = notes or ""
            # 人工「不同意/修正」语义判定（A5 审核流，[不同意] 前缀触发重归因）
            if "[不同意]" in notes or "修正" in notes:
                stats["corrected"] += 1
                stats["pass_fail"].append((match_id, "修正", notes[:60]))
            else:
                stats["passed"] += 1
                stats["pass_fail"].append((match_id, "通过", notes[:60]))
    finally:
        conn.close()
    return stats


# ==================== 指标计算 ====================
def _judge(value: Optional[float], target: float, direction: str = "ge") -> str:
    """阈值判定：direction=ge 达标为 ≥target；le 达标为 ≤target。"""
    if value is None:
        return "ℹ️ N/A"
    if direction == "le":
        ok = value <= target
    else:
        ok = value >= target
    return "✅ 达标" if ok else "🔴 未达标"


def _evolution_rate(reports: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """模型进化速度：最近两份有效报告 Δacc / Δn × 30（每 30 场变化 pp）。

    只有一份有效报告或场次差 ≤0 时返回 None（数据不足）。
    """
    valid = [r for r in reports if r.get("overall_acc") is not None and r.get("total_matches")]
    if len(valid) < 2:
        return None
    a, b = valid[-2], valid[-1]
    dn = b["total_matches"] - a["total_matches"]
    if dn <= 0:
        return None
    dacc = b["overall_acc"] - a["overall_acc"]
    per30 = dacc / dn * 30.0
    return {
        "prev_ts": a["timestamp"][:10], "cur_ts": b["timestamp"][:10],
        "prev_acc": a["overall_acc"], "cur_acc": b["overall_acc"],
        "prev_n": a["total_matches"], "cur_n": b["total_matches"],
        "delta_n": dn, "delta_acc_pp": dacc * 100.0, "per30_pp": per30 * 100.0,
    }


def _hit_rate_note() -> str:
    """知识库命中率：B2 读取无埋点，如实标注 N/A + 建议。"""
    return (
        "无命中埋点：B2 赛前接入（get_insights → 报告十七节）未记录每次读取是否命中知识库。"
        "建议在 generate_unified_report.py 的十七节写入 `知识库命中/提示条目数` 到 model_predictions "
        "或独立 jsonl 埋点后，本指标方可计算。"
    )


# ==================== 看板渲染 ====================
def _render_md(date: str, kb: Dict[str, Any], rev: Dict[str, Any],
               reports: List[Dict[str, Any]], evo: Optional[Dict[str, Any]]) -> str:
    lines: List[str] = []

    # ---- 头部 ----
    lines.append(f"# 知识库效果监控看板（B5）")
    lines.append("")
    lines.append(f"> **生成时间**: {date}（每周更新）")
    lines.append(f"> **数据来源**: 知识库统计 / `assets/final_training_report_*.json`（分层评估）/ `data/odds.db` post_match_review")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- 指标 1：知识库条目与可信度分布 ----
    total = sum(kb["conf_dist"].values())
    lines.append("## 1. 知识库条目数与可信度分布")
    lines.append("")
    lines.append(f"| 项 | 值 | 目标 | 判定 |")
    lines.append(f"|----|----|------|:----:|")
    lines.append(f"| feature_insights 条目数（active/总） | {kb['insights_active']} / {kb['insights_all']} | — | — |")
    lines.append(f"| sample_weights 条目数（active） | {kb['sw_active']} | — | — |")
    lines.append(f"| human_corrections 条目数（open/总） | {kb['corrections_open']} / {kb['corrections_all']} | — | — |")
    conf45_ratio = kb["conf45_ratio"]
    lines.append(
        f"| 4-5 级条目占比 | {kb['conf45']}/{total} = {conf45_ratio:.1%} | ≥{TARGET_CONFIDENCE4_5:.0%} | "
        f"{_judge(conf45_ratio, TARGET_CONFIDENCE4_5, 'ge')} |"
    )
    lines.append("")
    if kb["conf_dist"]:
        dist_str = "、".join(f"{k} 级:{v} 条" for k, v in sorted(kb["conf_dist"].items()))
        lines.append(f"- 可信度分布（feature_insights）：{dist_str}")
    if kb["by_league"]:
        lg_str = "、".join(f"{lg}:{n}" for lg, n in kb["by_league"].items())
        lines.append(f"- 按联赛：{lg_str}" + (f"；global:{kb['global_n']}" if kb["global_n"] else ""))
    lines.append("")

    # ---- 指标 2：人工审核通过率 / 修正率 ----
    lines.append("## 2. 人工审核通过率与人工修正率")
    lines.append("")
    reviewed = rev["reviewed"]
    if reviewed == 0:
        lines.append("| 项 | 值 | 目标 | 判定 |")
        lines.append("|----|----|------|:----:|")
        lines.append("| 已审核场次 | 0 | — | — |")
        lines.append("| 通过率 | N/A | ≥80% | ℹ️ 数据不足（尚无审核记录） |")
        lines.append("| 修正率 | N/A | ≤20% | ℹ️ 数据不足（尚无审核记录） |")
        lines.append("")
        lines.append("- 说明：post_match_review 中 `human_reviewed=1` 的场次为 0，无法计算通过率/修正率。")
    else:
        pass_rate = rev["passed"] / reviewed
        corr_rate = rev["corrected"] / reviewed
        lines.append("| 项 | 值 | 目标 | 判定 |")
        lines.append("|----|----|------|:----:|")
        lines.append(f"| 已审核场次 | {reviewed}（通过 {rev['passed']} / 修正 {rev['corrected']}） | — | — |")
        lines.append(
            f"| 人工审核通过率 | {pass_rate:.1%} | ≥80% | {_judge(pass_rate, TARGET_PASS_RATE, 'ge')} |"
        )
        lines.append(
            f"| 人工修正率 | {corr_rate:.1%} | ≤20% | {_judge(corr_rate, TARGET_CORRECTION_RATE, 'le')} |"
        )
        lines.append("")
        if rev["pass_fail"]:
            lines.append("明细：")
            for mid, kind, note in rev["pass_fail"]:
                lines.append(f"  - `{mid}`：{kind}（{note}）")
        lines.append("")
        if rev["corrected"] > 0:
            lines.append(
                "- ⚠️ 存在人工修正：修正记录已写入 `human_corrections.json`，"
                "并在 B3/B4 训练与预测链路中被消费。"
            )
            lines.append("")

    # ---- 指标 3：各联赛准确率趋势（接入知识库前后） ----
    lines.append("## 3. 各联赛准确率趋势（接入知识库前后）")
    lines.append("")
    lines.append("> 数据源：`assets/final_training_report_*.json` → `stratified_evaluation.dimensions.by_league`（复用分层评估输出）。知识库于 2026-09-07 建立（C-20260907-011），09-08 起的重训报告为接入后基线。")
    lines.append("")
    if len(reports) < 2:
        lines.append("ℹ️ 训练报告不足 2 份，无法形成趋势。")
        lines.append("")
    else:
        header = "| 训练时间 | 样本 | 总体 Acc | " + " | ".join(LEAGUES) + " |"
        sep = "|---------|-----|----------|" + "|".join(["----------"] * len(LEAGUES)) + "|"
        lines.append(header)
        lines.append(sep)
        for r in reports:
            lg_cells = []
            for lg in LEAGUES:
                acc = r["by_league"].get(lg)
                lg_cells.append(f"{acc:.1%}" if acc is not None else "—")
            overall = f"{r['overall_acc']:.1%}" if r["overall_acc"] is not None else "—"
            if r["timestamp"][:10] >= "2026-09-08":
                mark = "（接入后）"
            elif r["overall_acc"] is None:
                mark = "（无分层评估）"
            else:
                mark = ""
            lines.append(
                f"| {r['timestamp'][:16]} {mark} | {r['total_matches']} | {overall} | " + " | ".join(lg_cells) + " |"
            )
        lines.append("")
        n_valid = sum(1 for r in reports if r["overall_acc"] is not None)
        if n_valid < 2:
            lines.append(
                f"> ℹ️ 目前仅 {n_valid} 份报告含分层评估（P2-14 报告字段于 2026-09-08 起落地），"
                "接入前趋势数据源缺失，跨报告趋势需后续周更累积。"
            )
            lines.append("")
        # 接入前后对比：接入前最后一份 vs 接入后最新一份
        after = [r for r in reports if r["timestamp"][:10] >= "2026-09-08" and r["overall_acc"] is not None]
        before = [r for r in reports if r["timestamp"][:10] < "2026-09-08" and r["overall_acc"] is not None]
        if after and before:
            a, b = before[-1], after[-1]
            lines.append("**接入前后对比（总体 Acc）**：")
            lines.append("")
            lines.append(f"| 对比 | 接入前（{a['timestamp'][:10]}） | 接入后（{b['timestamp'][:10]}） | 变化 |")
            lines.append(f"|------|------|------|------|")
            lines.append(
                f"| 总体 | {a['overall_acc']:.1%} | {b['overall_acc']:.1%} | "
                f"{b['overall_acc'] - a['overall_acc']:+.1%} |"
            )
            for lg in LEAGUES:
                acc_a, acc_b = a["by_league"].get(lg), b["by_league"].get(lg)
                if acc_a is None or acc_b is None:
                    continue
                lines.append(
                    f"| {lg} | {acc_a:.1%} | {acc_b:.1%} | {acc_b - acc_a:+.1%} |"
                )
            lines.append("")
            lines.append("> ⚠️ 注意：接入后仅 1 份报告（v8.3.1-l3w 全量重训），样本内 A/B 门禁已 PASS，但跨报告趋势需后续周更累积。")
            lines.append("")

    # ---- 指标 4：模型进化速度 ----
    lines.append("## 4. 模型进化速度（每 30 场 ≥0.5pp）")
    lines.append("")
    if evo is None:
        lines.append("ℹ️ 数据不足：需 ≥2 份含分层评估与样本数的训练报告。")
    else:
        lines.append(f"| 项 | 值 | 目标 | 判定 |")
        lines.append(f"|----|----|------|:----:|")
        lines.append(f"| 对比窗口 | {evo['prev_ts']} → {evo['cur_ts']} | — | — |")
        lines.append(f"| 样本增量 Δn | {evo['prev_n']} → {evo['cur_n']}（+{evo['delta_n']}） | — | — |")
        lines.append(f"| 总体 Acc 变化 | {evo['prev_acc']:.1%} → {evo['cur_acc']:.1%}（{evo['delta_acc_pp']:+.2f}pp） | — | — |")
        lines.append(
            f"| 每 30 场进化 | {evo['per30_pp']:+.2f}pp | ≥{TARGET_EVOLUTION*100:.1f}pp | "
            f"{_judge(evo['per30_pp'] / 100.0, TARGET_EVOLUTION, 'ge')} |"
        )
    lines.append("")

    # ---- 指标 5：知识库命中率 ----
    lines.append("## 5. 知识库命中率（≥90%）")
    lines.append("")
    lines.append("| 项 | 值 | 目标 | 判定 |")
    lines.append("|----|----|------|:----:|")
    lines.append("| 知识库命中率 | N/A | ≥90% | ℹ️ 待埋点 |")
    lines.append("")
    lines.append(f"- {_hit_rate_note()}")
    lines.append("")

    # ---- 补充：复盘口径准确率（降级来源） ----
    lines.append("## 6. 补充：复盘口径准确率（post_match_review）")
    lines.append("")
    lines.append("> 降级数据源：`post_match_review` 中 08-30 起的 19 场复盘（接入知识库前的赛果回写），供参考，非分层评估口径。")
    lines.append("")
    lines.append(f"| 项 | 值 |")
    lines.append(f"|----|----|")
    lines.append(f"| 复盘场次 | {rev['total']} |")
    if rev["total"]:
        lines.append(
            f"| WDL 命中 | {rev['wdl_correct']}/{rev['total']} = "
            f"{rev['wdl_correct']/rev['total']:.1%} |"
        )
    for lg, v in rev["by_league"].items():
        if v["n"]:
            lines.append(
                f"| {lg} | {v['wdl_correct']}/{v['n']} = "
                f"{v['wdl_correct']/v['n']:.1%} |"
            )
        else:
            lines.append(f"| {lg} | — |")
    lines.append("")

    # ---- 尾部汇总 ----
    lines.append("---")
    lines.append("")
    results = []
    conf_ok = _judge(conf45_ratio, TARGET_CONFIDENCE4_5, "ge")
    if reviewed:
        pass_ok = _judge(pass_rate, TARGET_PASS_RATE, "ge")
        corr_ok = _judge(corr_rate, TARGET_CORRECTION_RATE, "le")
    else:
        pass_ok = "ℹ️ N/A"
        corr_ok = "ℹ️ N/A"
    evo_ok = _judge((evo["per30_pp"] / 100.0) if evo else None, TARGET_EVOLUTION, "ge")
    hit_ok = "ℹ️ N/A"
    results = [
        ("1 可信度 4-5 级占比", conf_ok),
        ("2 审核通过率", pass_ok),
        ("2 修正率", corr_ok),
        ("4 进化速度", evo_ok),
        ("5 命中率", hit_ok),
    ]
    lines.append("**指标汇总**：")
    lines.append("")
    lines.append("| 指标 | 判定 |")
    lines.append("|------|:----:|")
    for name, s in results:
        lines.append(f"| {name} | {s} |")
    lines.append("")
    lines.append(f"*生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    return "\n".join(lines)


def _update_index(date: str) -> None:
    """更新 docs/knowledge_dashboard/index.md（最新看板置顶 + 历史列表）。"""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    entries = sorted(DASHBOARD_DIR.glob("*_dashboard.md"))
    entries.sort(reverse=True)
    lines = [
        "# 知识库效果监控看板索引（B5）",
        "",
        "> 每周更新；最新看板见下方置顶。",
        "",
        "## 最新",
        "",
    ]
    if entries:
        latest = entries[0]
        lines.append(f"- [{latest.stem}]({latest.name})（最新）")
    lines.append("")
    lines.append("## 历史")
    lines.append("")
    if len(entries) > 1:
        for p in entries[1:]:
            lines.append(f"- [{p.stem}]({p.name})")
    else:
        lines.append("- （暂无）")
    lines.append("")
    (DASHBOARD_DIR / "index.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="B5 知识库效果监控看板")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="看板日期 YYYY-MM-DD")
    args = ap.parse_args()

    kb = _load_kb_stats()
    rev = _load_review_stats()
    reports = _load_training_reports()
    evo = _evolution_rate(reports)

    md = _render_md(args.date, kb, rev, reports, evo)

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DASHBOARD_DIR / f"{args.date.replace('-', '')}_dashboard.md"
    out_path.write_text(md, encoding="utf-8")
    _update_index(args.date)

    print(f"✅ 看板已生成: {out_path}")
    print(f"   知识库条目(active): {kb['insights_active']} | 4-5级占比: {kb['conf45_ratio']:.1%}")
    print(f"   复盘场次: {rev['total']} | 已审核: {rev['reviewed']} | 通过: {rev['passed']} | 修正: {rev['corrected']}")
    print(f"   训练报告趋势: {len(reports)} 份 | 最新: {reports[-1]['timestamp'][:16] if reports else '—'}")
    if evo:
        print(f"   进化速度(每30场): {evo['per30_pp']:+.2f}pp")
    print(f"   index: {DASHBOARD_DIR / 'index.md'}")


if __name__ == "__main__":
    main()
