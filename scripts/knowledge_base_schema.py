# -*- coding: utf-8 -*-
"""
knowledge_base_schema.py — 模块 B1：知识库目录与 schema 统一（P1）

===============================================
背景（模型改进实施方案 v1.0 §四/B1，对齐指南 §3.1）：
  data/knowledge_base/ 按联赛分类，统一外层 wrapper + 条目 schema：
    data/knowledge_base/
      ├── 英超/ 西甲/ 意甲/ 德甲/ 法甲/ global/
      │   ├── feature_insights.json    # L2：什么特征在什么场景有效
      │   ├── sample_weights.json      # L3：哪类比赛预测偏差大
      │   └── rules.json               # L1：统计先验/规则覆盖
      └── human_corrections.json       # 人工修正记录（全局）
  每个 JSON 文件外层统一结构（对齐指南 §3.1，加 layer 自描述）：
    {"version": "1.0", "league": "英超", "layer": "feature_insights",
     "updated_at": "2026-09-08", "entries": [...]}
  feature_insights 条目结构（对齐方案 §B1 / 指南 §3.1）：
    {id, type, content, confidence, source_matches[], created_at,
     last_verified, expire_condition, status}
  human_corrections 条目结构（对齐方案 §B4）：
    {id, match_id, league, match_type, correction_type, original_attribution,
     corrected_attribution, reason, created_at, applied_count, status}
  规则：
    - confidence>=4 才写入 feature_insights（写入门禁）
    - 按 match_id 去重幂等
    - 每月清理失效条目（cleanup_expired：last_verified 超期且 status=active → expired）

职责（统一读写入口，A5/B2/B5 复用，避免双写路径）：
  - ensure_structure()：幂等创建 6 联赛 × 3 层 + human_corrections 统一文件；
    A5 存量数组结构（旧最小落盘）读取时自动迁移为 wrapper，不丢数据
  - load_entries/save_entries：统一读写（兼容新旧双形态）
  - add_insight / add_correction：A5 复用（替代 confidence_review.py 内部最小落盘）
  - parse_attribution / primary_cause：attribution_json 双形态解析（A5 基线分级复用）
  - get_insights：B2 赛前预测接入知识库的读取 API
  - cleanup_expired：B1 每月清理失效条目
  - count_insights / count_corrections：B5 效果监控看板指标

基础设施约定（§1.3）：路径 Path(__file__) 动态定位，禁硬编码盘符。

用法：
  python scripts/knowledge_base_schema.py --init           # 幂等创建 + 迁移 A5 存量
  python scripts/knowledge_base_schema.py --list [league] [--layer L]
  python scripts/knowledge_base_schema.py --stats
  python scripts/knowledge_base_schema.py --cleanup [--max-age N]   # 默认 90 天
  python scripts/knowledge_base_schema.py --verify
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE = PROJECT_DIR / "data" / "knowledge_base"

# 统一 schema 常量（对齐方案 §B1 / 指南 §3.1）
LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲", "global"]
LAYERS = ["feature_insights", "sample_weights", "rules"]
HUMAN_CORRECTIONS_LAYER = "human_corrections"  # 根目录全局文件，layer 自描述
SCHEMA_VERSION = "1.0"
MIN_CONFIDENCE = 4          # 写入门禁：≥4 级才写入 feature_insights
DEFAULT_EXPIRE_DAYS = 90    # 每月清理阈值：last_verified 超期且 active → expired
DEFAULT_EXPIRE_CONDITION = "连续5场不再验证则降级"

# feature_insights 条目必填字段（对齐方案 §B1）
INSIGHT_REQUIRED_FIELDS = [
    "id", "type", "content", "confidence", "source_matches",
    "created_at", "last_verified", "expire_condition", "status",
]
# human_corrections 条目必填字段（对齐方案 §B4）
CORRECTION_REQUIRED_FIELDS = [
    "id", "match_id", "league", "match_type", "correction_type",
    "original_attribution", "corrected_attribution", "reason",
    "created_at", "applied_count", "status",
]


# ==================== 路径 ====================
def _layer_path(league: str, layer: str) -> Path:
    return KNOWLEDGE_BASE / league / f"{layer}.json"


def _human_path() -> Path:
    return KNOWLEDGE_BASE / f"{HUMAN_CORRECTIONS_LAYER}.json"


def _normalize_league(league: Optional[str]) -> str:
    """联赛未命中列表时落 global（全局经验），幂等不报错。"""
    if league in LEAGUES:
        return league
    return "global"


# ==================== 统一读写（兼容新旧双形态） ====================
def _load_file(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _save_wrapper(path: Path, league: str, layer: str, entries: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "version": SCHEMA_VERSION,
            "league": league,
            "layer": layer,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entries": entries,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_entries(league: str, layer: str) -> List[Dict[str, Any]]:
    """统一读取入口：兼容两种存储形态。

    - 新 wrapper（dict + entries）：直接返回 entries
    - A5 旧数组（list）：读入后自动以 wrapper 写回（幂等迁移，不丢数据）
    文件缺失/损坏 → 返回空列表（不抛错，由 ensure_structure 负责补齐）。
    """
    path = _layer_path(league, layer)
    data = _load_file(path)
    if isinstance(data, list):
        _save_wrapper(path, league, layer, data)
        return data
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return data["entries"]
    return []


def save_entries(league: str, layer: str, entries: List[Dict[str, Any]]) -> None:
    _save_wrapper(_layer_path(league, layer), league, layer, entries)


def load_human_corrections() -> List[Dict[str, Any]]:
    """human_corrections 统一读取（根目录全局文件，非 global/ 子目录）。

    兼容新旧双形态（wrapper / A5 旧数组），逻辑同 load_entries。
    """
    data = _load_file(_human_path())
    if isinstance(data, list):
        _save_wrapper(_human_path(), "global", HUMAN_CORRECTIONS_LAYER, data)
        return data
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return data["entries"]
    return []


def save_human_corrections(entries: List[Dict[str, Any]]) -> None:
    """human_corrections 统一写入（根目录全局文件，layer 自描述为 global）。"""
    _save_wrapper(_human_path(), "global", HUMAN_CORRECTIONS_LAYER, entries)


# ==================== attribution_json 解析（A3 双形态兼容） ====================
def parse_attribution(attr: Any) -> Optional[Dict[str, Any]]:
    """attribution_json 解析为统一 dict 结构。

    兼容两种存储形态：完整对象 {"attributions", "primary_cause", "confidence_level"}
    （A3 修复写回后）与历史遗留 attributions 数组（A3 旧写回）——数组时
    primary_cause 兜底取最高权重归因的 type。
    """
    if not attr:
        return None
    if isinstance(attr, str):
        try:
            attr = json.loads(attr)
        except (ValueError, TypeError):
            return None
    if isinstance(attr, dict):
        return attr
    if isinstance(attr, list):
        if not attr:
            return None
        top = max(attr, key=lambda a: float(a.get("weight", 0) or 0))
        return {"attributions": attr, "primary_cause": top.get("type"), "confidence_level": None}
    return None


def primary_cause(attr: Any) -> Optional[str]:
    """归因主因：优先 primary_cause 字段，缺失时取 attributions 最高权重 type。"""
    parsed = parse_attribution(attr)
    if not parsed:
        return None
    cause = parsed.get("primary_cause")
    if cause:
        return cause
    attrs = parsed.get("attributions") or []
    return max(attrs, key=lambda a: float(a.get("weight", 0) or 0)).get("type") if attrs else None


# ==================== 写入（A5 复用，统一路径） ====================
def add_insight(
    match_id: str,
    league: Optional[str],
    level: int,
    attribution: Any,
    match_date: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """写因子库 {league}/feature_insights.json（对齐 §B1 条目 schema）。

    - 写入门禁：confidence>=4 才写入（方案 §A5 语义）
    - 去重幂等：按 source_matches 含 match_id 判定，已存在跳过
    - 无 AI 归因主因时跳过（仅赛果确认不写因子库）
    返回 True=新写入 / False=跳过或已存在。
    """
    if int(level or 0) < MIN_CONFIDENCE:
        print(f"  ⚠️ {match_id} confidence={level}<{MIN_CONFIDENCE} 级，拒绝写入因子库")
        return False
    primary = primary_cause(attribution)
    if not primary:
        print(f"  ⚠️ {match_id} 无 AI 归因主因，跳过因子库写入（仅赛果确认）")
        return False
    # content = 主因 + 最高权重归因 description（迁移 A5 原逻辑）
    content = primary
    parsed = parse_attribution(attribution) or {}
    attrs = sorted(
        (parsed.get("attributions") or []),
        key=lambda a: float(a.get("weight", 0) or 0),
        reverse=True,
    )
    if attrs and attrs[0].get("description"):
        content = f"{primary}｜{attrs[0]['description']}"
    league_norm = _normalize_league(league)
    entries = load_entries(league_norm, "feature_insights")
    if any(match_id in (it.get("source_matches") or []) for it in entries):
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entries.append({
        "id": f"insight_{match_id}",
        "type": "insight",
        "content": content,
        "confidence": int(level),
        "source_matches": [match_id],
        "created_at": now,
        "last_verified": match_date or now[:10],
        "expire_condition": DEFAULT_EXPIRE_CONDITION,
        "status": "active",
        "note": note,
    })
    save_entries(league_norm, "feature_insights", entries)
    return True


def add_correction(
    match_id: str,
    league: Optional[str],
    correction_type: str,
    original_attribution: Optional[str],
    corrected_attribution: Optional[str],
    reason: str,
) -> bool:
    """写 data/knowledge_base/human_corrections.json（对齐 §B4 条目 schema）。

    按 match_id 去重幂等，返回 True=新写入 / False=已存在跳过。
    """
    entries = load_human_corrections()
    if any(it.get("match_id") == match_id for it in entries):
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entries.append({
        "id": f"corr_{match_id}",
        "match_id": match_id,
        "league": league,
        "match_type": "post_match",
        "correction_type": correction_type,
        "original_attribution": original_attribution,
        "corrected_attribution": corrected_attribution,
        "reason": reason,
        "created_at": now,
        "applied_count": 0,
        "status": "open",
    })
    save_human_corrections(entries)
    return True


# ==================== B4 消费端：人工修正记忆三级（C-20260908-013） ====================
def get_correction_rules(min_count: int = 1) -> List[Dict[str, Any]]:
    """B4-1 短期规则覆盖层：聚合 human_corrections 为可规则化修正提示。

    按 (league, correction_type, corrected_attribution) 分组统计出现次数，
    供赛前预测/报告作为「该联赛该类偏差的已知人工修正规则」提示。
    返回规则列表，每条含 count + sample_matches（来源场次）+ reasons。
    """
    rules: Dict[tuple, Dict[str, Any]] = {}
    for it in load_human_corrections():
        if it.get("status") not in ("open", "applied"):
            continue
        lg = it.get("league") or "global"
        ctype = it.get("correction_type") or "attribution"
        ca = it.get("corrected_attribution") or "—"
        key = (lg, ctype, ca)
        r = rules.setdefault(key, {
            "league": lg, "correction_type": ctype, "corrected_attribution": ca,
            "count": 0, "sample_matches": [], "reasons": [],
        })
        r["count"] += 1
        if len(r["sample_matches"]) < 3 and it.get("match_id"):
            r["sample_matches"].append(it["match_id"])
        if it.get("reason") and len(r["reasons"]) < 3:
            r["reasons"].append(it["reason"])
    return [r for r in rules.values() if r["count"] >= min_count]


def mark_correction_applied(match_id: str, new_status: str = "applied") -> bool:
    """B4-2 应用标记：修正条目 applied_count+1，并转状态（默认 applied）。

    训练侧对修正场次加权命中后调用，幂等（match_id 不存在返回 False）。
    """
    entries = load_human_corrections()
    changed = False
    for it in entries:
        if it.get("match_id") == match_id:
            it["applied_count"] = int(it.get("applied_count") or 0) + 1
            it["status"] = new_status
            changed = True
            break
    if changed:
        save_human_corrections(entries)
    return changed


def export_corrections_fewshot(league: Optional[str] = None) -> str:
    """B4-3 长期 LLM 上下文记忆（仅探索）：导出人工修正为 few-shot 文本。

    供预测解释/复盘报告作为参考上下文；不参与概率计算。
    """
    lines = []
    for it in load_human_corrections():
        if league and it.get("league") not in (league, None):
            continue
        if it.get("status") not in ("open", "applied"):
            continue
        lines.append("- {}（{}）：原『{}』→ 修正『{}』，原因：{}（应用 {} 次，状态 {}）".format(
            it.get("match_id"), it.get("league") or "—",
            it.get("original_attribution") or "—", it.get("corrected_attribution") or "—",
            it.get("reason") or "—", it.get("applied_count") or 0, it.get("status")))
    if not lines:
        return "（暂无人工修正记录）"
    return "\n".join(lines)


# ==================== 读取 API（B2 赛前预测接入） ====================
def get_insights(
    league: Optional[str],
    layer: str = "feature_insights",
    min_confidence: int = MIN_CONFIDENCE,
    status: str = "active",
) -> List[Dict[str, Any]]:
    """B2 赛前预测接入：读同联赛知识库条目（默认 L2 有效因子 + 未失效）。

    global 作为兜底合并：同联赛命中条目在前，global 在后（层级由 league 优先）。
    """
    league_norm = _normalize_league(league)
    out: List[Dict[str, Any]] = []
    for src in (league_norm, "global") if league_norm != "global" else ("global",):
        for e in load_entries(src, layer):
            if e.get("status", "active") == status and int(e.get("confidence") or 0) >= min_confidence:
                e = dict(e)
                e["_league"] = src
                out.append(e)
    return out


# ==================== 统计（B5 看板指标） ====================
def count_insights(status: Optional[str] = None) -> int:
    """全量 feature_insights 条目数（可选按 status 过滤）。"""
    n = 0
    for league in LEAGUES:
        for e in load_entries(league, "feature_insights"):
            if status is None or e.get("status", "active") == status:
                n += 1
    return n


def count_corrections(status: Optional[str] = None) -> int:
    """human_corrections 条目数（可选按 status 过滤）。"""
    n = 0
    for e in load_human_corrections():
        if status is None or e.get("status", "open") == status:
            n += 1
    return n


def confidence_distribution(layer: str = "feature_insights") -> Dict[int, int]:
    """条目可信度分布（B5：4-5 级占比 ≥30%）。"""
    dist: Dict[int, int] = {}
    for league in LEAGUES:
        for e in load_entries(league, layer):
            c = int(e.get("confidence") or 0)
            dist[c] = dist.get(c, 0) + 1
    return dist


# ==================== 目录结构幂等创建 + 存量迁移 ====================
def ensure_structure() -> Dict[str, List[str]]:
    """幂等创建 6 联赛 × 3 层 + human_corrections 统一文件，并迁移 A5 存量数组。

    返回统计 {"created": [...], "migrated": [...], "existing": [...]}。
    """
    stats: Dict[str, List[str]] = {"created": [], "migrated": [], "existing": []}
    for league in LEAGUES:
        for layer in LAYERS:
            path = _layer_path(league, layer)
            rel = str(path.relative_to(KNOWLEDGE_BASE))
            data = _load_file(path)
            if data is None:
                _save_wrapper(path, league, layer, [])
                stats["created"].append(rel)
            elif isinstance(data, list):
                _save_wrapper(path, league, layer, data)
                stats["migrated"].append(rel)
            else:
                stats["existing"].append(rel)
    path = _human_path()
    data = _load_file(path)
    if data is None:
        _save_wrapper(path, "global", HUMAN_CORRECTIONS_LAYER, [])
        stats["created"].append(path.name)
    elif isinstance(data, list):
        _save_wrapper(path, "global", HUMAN_CORRECTIONS_LAYER, data)
        stats["migrated"].append(path.name)
    else:
        stats["existing"].append(path.name)
    return stats


# ==================== 每月清理失效条目（B1） ====================
def cleanup_expired(max_age_days: int = DEFAULT_EXPIRE_DAYS) -> Dict[str, int]:
    """last_verified 超期且 status=active 的条目 → expired（保留可追溯，幂等）。

    返回 {"checked": 文件数, "expired": 失效条数, "kept": 保留条数}。
    """
    cutoff = (datetime.now() - timedelta(days=max_age_days)).strftime("%Y-%m-%d")
    stats = {"checked": 0, "expired": 0, "kept": 0}
    for league in LEAGUES:
        for layer in LAYERS:
            entries = load_entries(league, layer)
            changed = False
            for e in entries:
                verified = e.get("last_verified")
                if e.get("status", "active") == "active" and verified and verified < cutoff:
                    e["status"] = "expired"
                    changed = True
                    stats["expired"] += 1
                else:
                    stats["kept"] += 1
            if changed:
                save_entries(league, layer, entries)
            stats["checked"] += 1
    return stats


# ==================== schema 校验（--verify） ====================
def verify_schema() -> List[str]:
    """全量校验 19 个文件：wrapper 结构 / entries 是 list / 条目必填字段 / 门禁合规。

    返回问题列表（空 = 全部合规）。
    """
    problems: List[str] = []
    for league in LEAGUES:
        for layer in LAYERS:
            path = _layer_path(league, layer)
            rel = str(path.relative_to(KNOWLEDGE_BASE))
            data = _load_file(path)
            if data is None:
                problems.append(f"{rel}: 文件缺失或损坏")
                continue
            if isinstance(data, list):  # 未迁移的旧数组
                problems.append(f"{rel}: 旧数组结构未迁移为 wrapper")
                continue
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                problems.append(f"{rel}: 缺 entries 列表")
                continue
            if data.get("league") != league or data.get("layer") != layer:
                problems.append(f"{rel}: wrapper league/layer 与路径不符")
            required = INSIGHT_REQUIRED_FIELDS if layer == "feature_insights" else []
            for i, e in enumerate(data["entries"]):
                for f in required:
                    if f not in e:
                        problems.append(f"{rel} entries[{i}]: 缺字段 {f}")
                if layer == "feature_insights" and int(e.get("confidence") or 0) < MIN_CONFIDENCE:
                    problems.append(f"{rel} entries[{i}]: confidence<{MIN_CONFIDENCE} 违反写入门禁")
    path = _human_path()
    data = _load_file(path)
    if data is None:
        problems.append(f"{path.name}: 文件缺失或损坏")
    elif isinstance(data, list):
        problems.append(f"{path.name}: 旧数组结构未迁移为 wrapper")
    elif not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        problems.append(f"{path.name}: 缺 entries 列表")
    else:
        for i, e in enumerate(data["entries"]):
            for f in CORRECTION_REQUIRED_FIELDS:
                if f not in e:
                    problems.append(f"{path.name} entries[{i}]: 缺字段 {f}")
    return problems


# ==================== CLI ====================
def main() -> None:
    parser = argparse.ArgumentParser(description="模块 B1：知识库目录与 schema 统一")
    parser.add_argument("--init", action="store_true", help="幂等创建 6 联赛×3 层 + human_corrections，迁移 A5 存量")
    parser.add_argument("--list", help="列出某联赛条目（缺省显示全部联赛）")
    parser.add_argument("--layer", default="feature_insights", help="--list 指定层（默认 feature_insights）")
    parser.add_argument("--stats", action="store_true", help="条目统计（B5 指标）")
    parser.add_argument("--cleanup", action="store_true", help="清理失效条目（last_verified 超期 active→expired）")
    parser.add_argument("--max-age", type=int, default=DEFAULT_EXPIRE_DAYS, help="--cleanup 超期天数（默认 90）")
    parser.add_argument("--verify", action="store_true", help="全量 schema 校验")
    args = parser.parse_args()

    if args.init:
        stats = ensure_structure()
        print("=== knowledge_base 结构幂等创建/迁移 ===")
        print(f"新建 {len(stats['created'])} 个：{stats['created']}")
        print(f"迁移 {len(stats['migrated'])} 个：{stats['migrated']}")
        print(f"已存在 {len(stats['existing'])} 个")
    elif args.stats:
        dist = confidence_distribution()
        total = sum(dist.values())
        high = sum(v for k, v in dist.items() if k >= MIN_CONFIDENCE)
        print("=== knowledge_base 统计（对齐 B5 看板指标）===")
        print(f"factor_insights 总条目 = {total}（4-5 级 {high} 条，占比 {high / total:.0%}，目标 ≥30% 仅 L2 语义）")
        print(f"可信度分布 = {dict(sorted(dist.items()))}")
        print(f"human_corrections = {count_corrections()}（status=open {count_corrections('open')}）")
    elif args.cleanup:
        r = cleanup_expired(args.max_age)
        print(f"=== 失效条目清理（阈值 {args.max_age} 天）===")
        print(f"检查文件 {r['checked']} 个，失效 {r['expired']} 条 → expired，保留 {r['kept']} 条")
    elif args.verify:
        problems = verify_schema()
        if problems:
            print(f"=== schema 校验：发现 {len(problems)} 个问题 ===")
            for p in problems:
                print(f"  ❌ {p}")
        else:
            print("=== schema 校验：19 个文件全部合规 ===")
    else:
        # 默认 --list 全部
        leagues = [args.list] if args.list else LEAGUES
        print(f"=== knowledge_base 条目（layer={args.layer}）===")
        n = 0
        for lg in leagues:
            entries = load_entries(lg, args.layer)
            if entries:
                print(f"[{lg}] {len(entries)} 条")
                for e in entries:
                    n += 1
                    print(f"  {n:>2}. [{e.get('confidence')}级/{e.get('status')}] {e.get('id')}: {e.get('content')}")
        if n == 0:
            print("（空）")


if __name__ == "__main__":
    main()
