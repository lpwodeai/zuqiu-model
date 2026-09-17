# -*- coding: utf-8 -*-
"""
knowledge_iteration.py — 模块 B3：自动迭代逻辑（P1）

===============================================
背景（模型改进实施方案 v1.0 §四/B3，对齐指南 §3.3 调整4）：
  自动迭代 = 从赛后复盘数据（post_match_review）自动识别「模型在哪类比赛上持续偏差」，
  生成优化建议报告 + 写入 L3 样本权重层（sample_weights.json），
  供重训时对该类样本加权。四种更新类型：

    | 更新类型 | 触发条件 | 输出 | 执行 |
    |---------|---------|------|------|
    | 单场更新 | 每场赛后 | 单场复盘报告 | 自动（模块 A） |
    | 赛事级更新 | 每联赛每 15-20 场 | 联赛优化建议报告 | 自动生成，人工确认后执行 |
    | 全局级更新 | 每 30-50 场 | 全局优化建议报告 | 自动生成，人工确认后执行 |
    | 触发式更新 | 某类比赛连续 5 场偏差率 >60% | 专项分析报告 | 立即触发，人工确认后执行 |

  核心原则（方案 §B3 + 项目既有 A/B 门禁文化）：
    - 优化建议**不自动执行**，报告由人工确认（--approve）后才触发重训
    - 任何新特征/新权重上线前必须过「真实数据 A/B（严格时序切分）」四指标门禁
      （RPS 不劣化 / LogLoss 不劣化 / Acc 稳定 / DrawRecall 不降）——由训练侧门禁执行，本模块只输出建议

  数据源：odds.db.post_match_review（wdl_correct 非空场次）
  输出：
    - docs/knowledge_iteration/{YYYYMMDD}_iteration_report.md  # 四类更新状态 + 建议清单
    - data/knowledge_base/{league}/sample_weights.json        # L3 建议条目（status=pending，--approve 转 active）

基础设施约定（§1.3）：路径 Path(__file__) 动态定位；知识库读写走 B1 统一模块。

用法：
  python scripts/knowledge_iteration.py --scan                    # 扫描复盘数据，生成建议报告（幂等）
  python scripts/knowledge_iteration.py --scan --league 英超       # 指定联赛
  python scripts/knowledge_iteration.py --list                    # 列出待确认（pending）建议
  python scripts/knowledge_iteration.py --approve <id>            # 人工确认 → active + 输出重训建议
  python scripts/knowledge_iteration.py --stats                   # 复盘/触发统计
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"
ITER_DIR = PROJECT_DIR / "docs" / "knowledge_iteration"

from knowledge_base_schema import (
    KNOWLEDGE_BASE,
    load_entries,
    primary_cause,
    save_entries,
)

# B3 阈值（方案 §B3，可按 --xxx 覆盖）
LEAGUE_MIN = 15       # 赛事级更新：每联赛复盘 ≥15 场
GLOBAL_MIN = 30       # 全局级更新：复盘 ≥30 场
TRIGGER_WINDOW = 5    # 触发式更新：连续 N 场
TRIGGER_RATE = 0.6    # 触发式更新：偏差率 >60%（未命中占比）
DEFAULT_WEIGHT = 1.2  # 建议样本权重


# ==================== 连接 ====================
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ==================== 复盘数据读取 ====================
def load_reviews(league: Optional[str] = None) -> List[Dict[str, Any]]:
    """读 post_match_review 全部已复盘场次（wdl_correct 非空），附主因。"""
    conn = _get_conn()
    cur = conn.cursor()
    sql = "SELECT * FROM post_match_review WHERE wdl_correct IS NOT NULL"
    args: List[Any] = []
    if league:
        sql += " AND league=?"
        args.append(league)
    cur.execute(sql, args)
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        # 联赛归一：post_match_review 少数行 league 可能为 NULL，统一为「未知」避免
        # 下游 sorted(prof.items()) 出现 NoneType 与 str 比较报错（C-20260910-014）。
        r["league"] = r.get("league") or "未知"
        r["primary_cause"] = primary_cause(r.get("attribution_json"))
    return rows


# ==================== 触发式检测（连续 N 场偏差率 >60%） ====================
def detect_triggers(
    reviews: List[Dict[str, Any]],
    window: int = TRIGGER_WINDOW,
    rate: float = TRIGGER_RATE,
) -> List[Dict[str, Any]]:
    """按 (league, primary_cause) 分组、按时间排序，滑动窗口检测偏差率。

    偏差 = wdl_correct=0（预测方向未命中）。返回触发组列表
    [{league, primary_cause, window, miss, total, miss_rate, matches[], last_date}]。
    """
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for r in reviews:
        if r.get("primary_cause"):
            groups[(r["league"], r["primary_cause"])].append(r)
    triggers: List[Dict[str, Any]] = []
    for (lg, cause), rows in groups.items():
        rows.sort(key=lambda x: (x.get("match_date") or "", x.get("match_id") or ""))
        # 滑动窗口：取该组最近 window 场（连续复盘场次）
        for i in range(len(rows) - window + 1):
            win = rows[i:i + window]
            miss = sum(1 for r in win if not r.get("wdl_correct"))
            if window > 0 and miss / window > rate:
                triggers.append({
                    "league": lg,
                    "primary_cause": cause,
                    "window": window,
                    "miss": miss,
                    "total": window,
                    "miss_rate": miss / window,
                    "matches": [r["match_id"] for r in win],
                    "last_date": win[-1].get("match_date"),
                })
                break  # 该组只记最近一次触发
    return triggers


# ==================== 联赛/全局级偏差画像 ====================
def league_profile(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """联赛级画像：场次数、命中率、主因分布、偏差最重主因。"""
    by_lg: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in reviews:
        by_lg[r["league"]].append(r)
    prof: Dict[str, Any] = {}
    for lg, rows in by_lg.items():
        n = len(rows)
        hit = sum(1 for r in rows if r.get("wdl_correct"))
        causes: Dict[str, int] = defaultdict(int)
        for r in rows:
            c = r.get("primary_cause") or "无归因"
            causes[c] += 1
        top_cause = max(causes, key=causes.get)
        prof[lg] = {
            "n": n, "hit_rate": hit / n, "miss_rate": 1 - hit / n,
            "causes": dict(causes), "top_cause": top_cause,
        }
    return prof


# ==================== L3 建议条目（B1 schema，status=pending） ====================
def _suggestion_exists(league: str, content_key: str) -> bool:
    for e in load_entries(league, "sample_weights"):
        if e.get("content", "").startswith(content_key):
            return True
    return False


def write_suggestion(
    league: str,
    content: str,
    source_matches: List[str],
    last_date: str,
    weight: float = DEFAULT_WEIGHT,
) -> bool:
    """写 L3 sample_weights 建议条目（status=pending 待人工确认）。

    按 content 前缀去重幂等。返回 True=新写入 / False=已存在跳过。
    """
    if _suggestion_exists(league, content.split("｜")[0][:12]):
        return False
    entries = load_entries(league, "sample_weights")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entries.append({
        "id": f"sw_{league}_{now[:10]}_{len(entries) + 1}",
        "type": "sample_weight",
        "content": content,
        "confidence": 3,                       # 待人工确认（--approve 后 4）
        "source_matches": source_matches,
        "created_at": now,
        "last_verified": last_date,
        "expire_condition": "重训验证一次后复核，连续3个迭代周期未确认则过期",
        "status": "pending",
        "suggested_weight": weight,
    })
    save_entries(league, "sample_weights", entries)
    return True


# ==================== 扫描主流程 ====================
def scan(league: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """扫描复盘数据，生成建议报告 + 写 L3 pending 建议（幂等）。"""
    reviews = load_reviews(league)
    n_total = len(reviews)
    prof = league_profile(reviews)
    triggers = detect_triggers(reviews)

    report: List[str] = []
    report.append(f"# 知识库自动迭代报告 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("")
    report.append(f"> 数据源: post_match_review 已复盘场次 **{n_total}** 场"
                  + (f"（联赛过滤: {league}）" if league else "")
                  + " | 原则：优化建议不自动执行，人工确认（--approve）后才触发重训")
    report.append("")

    # ① 赛事级更新
    report.append("## 一、赛事级更新（每联赛 ≥{0} 场）".format(LEAGUE_MIN))
    report.append("")
    report.append("| 联赛 | 已复盘 | 命中率 | 偏差率 | 是否达标 | 偏差最重主因 |")
    report.append("|------|:------:|:------:|:------:|:--------:|-------------|")
    for lg, p in sorted(prof.items()):
        ok = p["n"] >= LEAGUE_MIN
        report.append("| {} | {} | {:.0%} | {:.0%} | {} | {} |".format(
            lg, p["n"], p["hit_rate"], p["miss_rate"],
            "✅ 生成建议" if ok else "⏳ 还差 {} 场".format(LEAGUE_MIN - p["n"]),
            "{} ({} 场)".format(p["top_cause"], p["causes"][p["top_cause"]])))
    if not prof:
        report.append("| — | 0 | — | — | ⏳ 无复盘数据 | — |")
    report.append("")

    # ② 全局级更新
    report.append("## 二、全局级更新（复盘 ≥{0} 场）".format(GLOBAL_MIN))
    report.append("")
    if n_total >= GLOBAL_MIN:
        overall_miss = sum(1 for r in reviews if not r.get("wdl_correct"))
        report.append("✅ 已达阈值（{} 场）。全局偏差率 {:.0%}（{} 场未命中）。建议："
                      "人工确认后按主因分布对偏差最重类别做样本加权，并触发重训 A/B 验证。".format(
                          n_total, overall_miss / n_total, overall_miss))
    else:
        report.append("⏳ 未达阈值（当前 {} 场，还差 {} 场）。持续积累复盘数据（模块 A 每日自动）。".format(
            n_total, GLOBAL_MIN - n_total))
    report.append("")

    # ③ 触发式更新
    report.append("## 三、触发式更新（连续 {0} 场偏差率 >{1:.0%}）".format(TRIGGER_WINDOW, TRIGGER_RATE))
    report.append("")
    if triggers:
        report.append("| 联赛 | 主因类别 | 窗口偏差 | 偏差率 | 最近复盘 | 建议 |")
        report.append("|------|---------|:-------:|:------:|:--------:|------|")
        for t in triggers:
            report.append("| {} | {} | {}/{} | {:.0%} | {} | 样本权重 ×{:.1f} |".format(
                t["league"], t["primary_cause"], t["miss"], t["total"],
                t["miss_rate"], t["last_date"], DEFAULT_WEIGHT))
        report.append("")
        report.append("> 触发式建议已写入 L3 sample_weights（status=pending），人工确认后转 active 并触发重训。")
    else:
        report.append("（无触发。各 (联赛×主因) 组连续 {} 场偏差率均 ≤{:.0%}。）".format(TRIGGER_WINDOW, TRIGGER_RATE))
    report.append("")

    # ④ L3 样本权重建议清单（待人工确认）——先落盘建议再渲染，保证清单最新
    report.append("## 四、L3 样本权重建议清单（待人工确认）")
    report.append("")
    report.append("| ID | 联赛 | 建议内容 | 权重 | 状态 |")
    report.append("|----|------|---------|:----:|:----:|")

    # 写 L3 pending 建议（幂等）——必须在渲染清单前执行
    written = 0
    if not dry_run:
        for t in triggers:
            content = "{}『{}』连续{}场偏差率{:.0%}（{}/{}未命中），建议重训样本权重×{:.1f}".format(
                t["league"], t["primary_cause"], t["window"], t["miss_rate"],
                t["miss"], t["total"], DEFAULT_WEIGHT)
            if write_suggestion(t["league"], content, t["matches"], t["last_date"]):
                written += 1
        for lg, p in sorted(prof.items(), key=lambda kv: kv[1]["miss_rate"], reverse=True):
            if p["n"] >= LEAGUE_MIN and p["miss_rate"] > 0.55:
                content = "{}联赛复盘{}场命中率{:.0%}，主因分布{}，建议重训时按主因加权并做A/B门禁验证".format(
                    lg, p["n"], p["hit_rate"], json.dumps(p["causes"], ensure_ascii=False))
                if write_suggestion(lg, content, [], reviews[-1].get("match_date") or ""):
                    written += 1

    pending_count = 0
    for lg in sorted(prof):
        for e in load_entries(lg, "sample_weights"):
            pending_count += 1
            report.append("| {} | {} | {} | ×{:.1f} | {} |".format(
                e.get("id"), lg, e.get("content"), e.get("suggested_weight", 1.0), e.get("status")))
    if pending_count == 0:
        report.append("| — | — | （暂无建议） | — | — |")
    report.append("")
    report.append("---")
    report.append("")
    report.append("> 生成: knowledge_iteration.py | 确认命令: `python scripts/knowledge_iteration.py --approve <id>`")

    # 写报告
    out = ITER_DIR / "{}_iteration_report.md".format(datetime.now().strftime("%Y%m%d"))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run:
        out.write_text("\n".join(report), encoding="utf-8")
        print("✅ 报告: {}".format(out))
        print("✅ 新写入 L3 建议 {} 条（status=pending，--approve 确认）".format(written))
    else:
        print("（dry-run）报告将输出至: {}".format(out))
    return {"n_total": n_total, "triggers": len(triggers), "report": out}


# ==================== 建议确认 ====================
def list_suggestions(status: str = "pending") -> List[Dict[str, Any]]:
    out = []
    for lg in ["英超", "西甲", "意甲", "德甲", "法甲", "global"]:
        for e in load_entries(lg, "sample_weights"):
            if e.get("status") == status:
                e["_league"] = lg
                out.append(e)
    return out


def approve(sug_id: str) -> bool:
    """人工确认 → status pending→active + confidence 3→4 + 输出重训触发建议（不自动执行）。"""
    for lg in ["英超", "西甲", "意甲", "德甲", "法甲", "global"]:
        entries = load_entries(lg, "sample_weights")
        for e in entries:
            if e.get("id") == sug_id:
                if e.get("status") == "active":
                    print("  ⚠️ {} 已确认（active），跳过".format(sug_id))
                    return False
                e["status"] = "active"
                e["confidence"] = 4
                e["last_verified"] = datetime.now().strftime("%Y-%m-%d")
                save_entries(lg, "sample_weights", entries)
                print("✅ {} 已确认（active，confidence=4）".format(sug_id))
                print("   → 重训触发建议（人工执行，先过 A/B 四指标门禁）：")
                print("     - 训练侧：对 {} 类样本按 suggested_weight 加权重训".format(e["content"][:24]))
                print("     - 门禁：RPS 不劣化 / LogLoss 不劣化 / Acc 稳定 / DrawRecall 不降")
                return True
    print("  ❌ 未找到建议 {}".format(sug_id))
    return False


# ==================== 统计 ====================
def stats() -> None:
    reviews = load_reviews()
    prof = league_profile(reviews)
    triggers = detect_triggers(reviews)
    print("=== 知识库自动迭代统计 ===")
    print("复盘场次: {}（赛事级阈值 {} / 全局阈值 {}）".format(len(reviews), LEAGUE_MIN, GLOBAL_MIN))
    for lg, p in sorted(prof.items()):
        print("  {}: {} 场 命中率 {:.0%} 主因 {}".format(lg, p["n"], p["hit_rate"], p["causes"]))
    print("触发式: {} 组（连续 {} 场偏差率 >{:.0%}）".format(len(triggers), TRIGGER_WINDOW, TRIGGER_RATE))
    for t in triggers:
        print("  ⚠️ {} {}: {}/{} 未命中".format(t["league"], t["primary_cause"], t["miss"], t["total"]))
    pend = list_suggestions("pending")
    act = list_suggestions("active")
    print("L3 建议: pending {} 条 / active {} 条".format(len(pend), len(act)))


def main() -> None:
    parser = argparse.ArgumentParser(description="模块 B3：自动迭代逻辑")
    parser.add_argument("--scan", action="store_true", help="扫描复盘数据，生成建议报告 + 写 L3 pending 建议")
    parser.add_argument("--league", help="--scan 指定联赛")
    parser.add_argument("--dry-run", action="store_true", help="--scan 只预览不写盘")
    parser.add_argument("--list", action="store_true", help="列出待确认（pending）建议")
    parser.add_argument("--approve", help="人工确认建议 <id>（pending→active，触发重训建议）")
    parser.add_argument("--stats", action="store_true", help="复盘/触发统计")
    args = parser.parse_args()

    if args.scan:
        scan(args.league, args.dry_run)
    elif args.list:
        for s in list_suggestions():
            print("[{}] {} | {} | ×{:.1f}".format(
                s["_league"], s["id"], s["content"], s.get("suggested_weight", 1.0)))
    elif args.approve:
        approve(args.approve)
    elif args.stats:
        stats()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
