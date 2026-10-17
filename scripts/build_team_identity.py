# -*- coding: utf-8 -*-
"""P0：球队身份层纯加法构建（C-20261014-095 → 落地 C-096）

边界（严格遵守 P0 规格，不越界到 P1/P2/P4）：
  ✅ 建 teams(team_id PK, canonical_en UNIQUE, canonical_cn) 表——canonical 锚 = fbref 英文名
  ✅ 建 team_names.py 单一源：合并 5+ 处散表 alias + 两份 normalize_team_name 差异台账
  ✅ 漂移清单（5 支队）纳入合并清单
  ❌ 不切换任何现有 import 方（feature_utils / 采集器 / 报告链 全部保持原调用）
  ❌ 不动 home_team_cn 2 行 legacy 中文值（P4，重训窗口）
  ❌ 不删任何旧表/旧函数（P1）
  ❌ 不碰 match_id 主键（P2）

幂等：CREATE TABLE IF NOT EXISTS + INSERT OR REPLACE；team_id 由 canonical_en 排序确定性分配。
只读构建：不调用任何采集/特征/报告链路。
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
sys.path.insert(0, str(SCRIPTS))

ODDS_DB = BASE / "data" / "odds.db"
TEAM_NAMES_PY = SCRIPTS / "team_names.py"
LEDGER_JSON = SCRIPTS / "team_names_diff_ledger.json"

# canonical 锚：fbref_match_mapping 的英文队名
CANON_SQL = (
    "SELECT DISTINCT home_team_fbref FROM fbref_match_mapping "
    "WHERE home_team_fbref IS NOT NULL AND home_team_fbref<>'' "
    "UNION "
    "SELECT DISTINCT away_team_fbref FROM fbref_match_mapping "
    "WHERE away_team_fbref IS NOT NULL AND away_team_fbref<>''"
)


# --------------------------------------------------------------------------
# 1. 采集全部散表 alias 源
# --------------------------------------------------------------------------
def _load_module_attr(mod_name: str, attr: str):
    try:
        mod = __import__(mod_name)
        return getattr(mod, attr, None)
    except Exception as e:  # noqa: BLE001
        print(f"  （警告：无法 import {mod_name}.{attr}: {e}）")
        return None


def _load_score_match_cn_to_en() -> dict[str, str]:
    """score_match_enhancer.py 的 cn→en 映射在函数内部，用 ast 抽取 supplements/aliases/new_aliases。"""
    f = SCRIPTS / "score_match_enhancer.py"
    if not f.exists():
        return {}
    src = f.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id in {"supplements", "aliases", "new_aliases"}:
                if isinstance(node.value, ast.Dict):
                    for k, v in zip(node.value.keys, node.value.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            if isinstance(k.value, str) and isinstance(v.value, str):
                                out[k.value] = v.value
    return out


def _load_team_mapping_table() -> dict[str, str]:
    conn = sqlite3.connect(str(ODDS_DB))
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT original_name, standard_name FROM team_mapping "
            "WHERE original_name IS NOT NULL AND standard_name IS NOT NULL")}
    finally:
        conn.close()


def gather_sources() -> dict[str, dict]:
    """返回 {source_name: {raw_name: cn_or_en}}；值为该源把 raw 归一到的目标名。"""
    sources: dict[str, dict] = {}

    # ① team_name_mapping.TEAM_ALIASES：key=cn, value=[aliases]
    ta = _load_module_attr("team_name_mapping", "TEAM_ALIASES") or {}
    flat: dict[str, str] = {}
    for cn, aliases in ta.items():
        flat[cn] = cn
        for a in aliases:
            flat[a] = cn
    sources["team_name_mapping.TEAM_ALIASES"] = flat

    # ② feature_utils.TEAM_NAME_MAP：en→cn
    sources["feature_utils.TEAM_NAME_MAP"] = dict(_load_module_attr("feature_utils", "TEAM_NAME_MAP") or {})

    # ③ fbref_anti_cloudflare.FBREF_TEAM_NAME_MAP：en→cn
    sources["fbref_anti_cloudflare.FBREF_TEAM_NAME_MAP"] = dict(
        _load_module_attr("fbref_anti_cloudflare", "FBREF_TEAM_NAME_MAP") or {})

    # ④ sporttery_sync_to_odds.TEAM_NAME_MAP：cn→cn 变体
    sources["sporttery_sync_to_odds.TEAM_NAME_MAP"] = dict(
        _load_module_attr("sporttery_sync_to_odds", "TEAM_NAME_MAP") or {})

    # ⑤ score_match_enhancer 内部 cn→en
    sources["score_match_enhancer(cn→en)"] = _load_score_match_cn_to_en()

    # ⑥ merge_understat_to_matches.UNDERSTAT_ALIASES：en→en（understat 名→标准英名）
    sources["merge_understat_to_matches.UNDERSTAT_ALIASES"] = dict(
        _load_module_attr("merge_understat_to_matches", "UNDERSTAT_ALIASES") or {})

    # ⑥b 手工补录：差分测试发现的真缺口（odds500 无变音德甲变体 → canonical_en）
    sources["manual_extra(C-097差分)"] = {
        "Duesseldorf": "Fortuna Düsseldorf",
        "Fortuna Duesseldorf": "Fortuna Düsseldorf",
        "Greuther Fuerth": "SpVgg Greuther Fürth",
        "Nuernberg": "1. FC Nürnberg",
    }

    # ⑦ odds.db.team_mapping：original_name→standard_name
    sources["odds.db.team_mapping"] = _load_team_mapping_table()

    return sources


# --------------------------------------------------------------------------
# 2. canonical_en 集合 + canonical_cn 解析
# --------------------------------------------------------------------------
def resolve_canonical_cn(canon_en: str, sources: dict[str, dict], ta_reverse: dict[str, str]) -> str | None:
    """解析 canonical_en → canonical_cn。优先级见 C-095 登记。"""
    # ① odds.db.team_mapping（DB 权威）
    if canon_en in sources["odds.db.team_mapping"]:
        return sources["odds.db.team_mapping"][canon_en]
    # ② feature_utils.TEAM_NAME_MAP
    if canon_en in sources["feature_utils.TEAM_NAME_MAP"]:
        return sources["feature_utils.TEAM_NAME_MAP"][canon_en]
    # ③ FBREF_TEAM_NAME_MAP
    if canon_en in sources["fbref_anti_cloudflare.FBREF_TEAM_NAME_MAP"]:
        return sources["fbref_anti_cloudflare.FBREF_TEAM_NAME_MAP"][canon_en]
    # ④ TEAM_ALIASES 反查（canon_en 出现在某 cn 的别名列表中）
    if canon_en in ta_reverse:
        return ta_reverse[canon_en]
    # ⑤ 子串兜底：canon_en 含某英文别名（≥4 字符，避免通用词）→ 该别名所属 cn
    for alias, cn in ta_reverse.items():
        if len(alias) >= 4 and alias in canon_en:
            return cn
    return None


def build_ta_reverse(ta: dict[str, list]) -> dict[str, str]:
    """alias(str) → cn_key（TEAM_ALIASES 的 key）。"""
    rev: dict[str, str] = {}
    for cn, aliases in ta.items():
        rev[cn] = cn
        for a in aliases:
            rev.setdefault(a, cn)
    return rev


# --------------------------------------------------------------------------
# 3. 合并 alias → team_names.py 单一源（raw_to_cn + cn_to_canon 两段式）
# --------------------------------------------------------------------------
def build_raw_to_cn(sources: dict[str, dict], ta: dict[str, list]) -> dict[str, str]:
    """全源合并：任意 raw → 中文名（canonical_cn）。
    方向归一：en→cn 源直接用；cn→en 源反转；cn→cn 源取标准侧；TEAM_ALIASES 全展开。
    """
    raw_to_cn: dict[str, str] = {}

    # en→cn 源
    for src in ("odds.db.team_mapping", "feature_utils.TEAM_NAME_MAP",
                "fbref_anti_cloudflare.FBREF_TEAM_NAME_MAP"):
        for raw, cn in sources[src].items():
            raw_to_cn.setdefault(str(raw), cn)

    # cn→en 源（反转）
    for cn, en in sources["score_match_enhancer(cn→en)"].items():
        raw_to_cn.setdefault(str(cn), cn)
        raw_to_cn.setdefault(str(en), cn)

    # cn→cn 变体源（sporttery）
    for a, b in sources["sporttery_sync_to_odds.TEAM_NAME_MAP"].items():
        raw_to_cn.setdefault(str(a), b)
        raw_to_cn.setdefault(str(b), b)

    # TEAM_ALIASES 全展开
    for cn, aliases in ta.items():
        raw_to_cn.setdefault(cn, cn)
        for a in aliases:
            raw_to_cn.setdefault(str(a), cn)

    return raw_to_cn


def build_cn_to_canon(canon_list: list[str], canonical_cn: dict[str, str | None],
                      raw_to_cn: dict[str, str], sporttery_map: dict[str, str],
                      ta: dict[str, list], canon_set: set[str]) -> dict[str, str]:
    """canonical_cn（及其 cn 变体）→ canonical_en。

    用 sporttery 的 cn→cn 归一表做不动点归一，把 '曼城'↔'曼彻斯特城' 这类
    同队异名收束到同一个 canonical_en。
    """
    def sporttery_norm(cn: str) -> str:
        seen: set[str] = set()
        while cn in sporttery_map and cn not in seen:
            seen.add(cn)
            cn = sporttery_map[cn]
        return cn

    # canonical_cn 归一化后 → canonical_en
    canon_cn_norm: dict[str, str] = {}
    for ce in canon_list:
        cn = canonical_cn.get(ce)
        if cn:
            canon_cn_norm.setdefault(sporttery_norm(cn), ce)

    cn_to_canon: dict[str, str] = {}
    for ce in canon_list:
        cn = canonical_cn.get(ce)
        if cn:
            cn_to_canon[cn] = ce
    # TEAM_ALIASES 直连：cn_key 若有英文别名恰好是 canonical_en，则 cn_key 及其全部别名
    # 都映射到该 canonical_en（收敛 '莱斯特城'↔'莱切斯特城' 等跨表 cn 变体）
    for cn_key, aliases in ta.items():
        hit = next((a for a in aliases if a in canon_set), None)
        if hit:
            cn_to_canon.setdefault(cn_key, hit)
            for a in aliases:
                cn_to_canon.setdefault(str(a), hit)
    # 展开：所有 raw（cn 名 或 en 别名）经 raw_to_cn 得到的 cn，归一后命中 canon_cn_norm
    for raw, cn in raw_to_cn.items():
        if not raw or not cn:
            continue
        cn_norm = sporttery_norm(cn)
        if cn_norm in canon_cn_norm:
            cn_to_canon.setdefault(str(raw), canon_cn_norm[cn_norm])
    return cn_to_canon


def resolve_raw_to_canon(raw: str, raw_to_cn: dict[str, str], cn_to_canon: dict[str, str],
                         canon_set: set[str]) -> str | None:
    """raw → canonical_en。"""
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    if raw in canon_set:
        return raw
    ce = cn_to_canon.get(raw)
    if ce:
        return ce
    cn = raw_to_cn.get(raw)
    if cn:
        ce = cn_to_canon.get(cn)
        if ce:
            return ce
    return None


def merge_aliases(canon_list: list[str], sources: dict[str, dict], ta: dict[str, list],
                  canonical_cn: dict[str, str | None]) -> tuple[dict[str, list[str]], dict[str, list]]:
    """返回 (TEAM_ALIASES{canon_en: [aliases]}, 漂移/未收录清单)。"""
    canon_set = set(canon_list)
    raw_to_cn = build_raw_to_cn(sources, ta)
    cn_to_canon = build_cn_to_canon(canon_list, canonical_cn, raw_to_cn,
                                    sources["sporttery_sync_to_odds.TEAM_NAME_MAP"],
                                    ta, canon_set)

    merged: dict[str, set[str]] = {c: set() for c in canon_list}
    drift: dict[str, list[str]] = {}

    for src_name, src_map in sources.items():
        for raw, target in src_map.items():
            ce = resolve_raw_to_canon(raw, raw_to_cn, cn_to_canon, canon_set)
            if ce is None:
                ce = resolve_raw_to_canon(target, raw_to_cn, cn_to_canon, canon_set)
            if ce is None:
                drift.setdefault(src_name, []).append(str(raw))
                continue
            merged[ce].add(str(raw))
            merged[ce].add(str(target))

    # TEAM_ALIASES 自身也要扫一遍（sources 里已扁平，但再保 cn_key 入列）
    for cn, aliases in ta.items():
        ce = resolve_raw_to_canon(cn, raw_to_cn, cn_to_canon, canon_set)
        if ce:
            merged[ce].add(cn)
            for a in aliases:
                merged[ce].add(str(a))

    # 确保 canonical_en 自身与 canonical_cn 都在 alias 里
    out: dict[str, list[str]] = {}
    for ce in canon_list:
        aliases = merged[ce]
        aliases.add(ce)
        cn = canonical_cn.get(ce)
        if cn:
            aliases.add(cn)
        out[ce] = sorted(aliases, key=lambda s: (s != ce, s.lower()))
    return out, drift


# --------------------------------------------------------------------------
# 5. 写库（teams 表）+ 写 team_names.py + 写 diff 台账
# --------------------------------------------------------------------------
def write_teams_table(rows: list[tuple[int, str, str | None]]) -> None:
    conn = sqlite3.connect(str(ODDS_DB))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS teams ("
            "team_id INTEGER PRIMARY KEY, "
            "canonical_en TEXT UNIQUE NOT NULL, "
            "canonical_cn TEXT)"
        )
        conn.execute("DELETE FROM teams")
        conn.executemany(
            "INSERT OR REPLACE INTO teams (team_id, canonical_en, canonical_cn) VALUES (?, ?, ?)",
            rows)
        conn.commit()
    finally:
        conn.close()


def write_team_names_py(canon_list: list[str], canonical_cn: dict[str, str | None],
                        merged: dict[str, list[str]]) -> None:
    lines: list[str] = []
    lines.append("# -*- coding: utf-8 -*-")
    lines.append('"""team_names.py — 球队别名单一权威源（P0 身份层，C-20261014-096）')
    lines.append("")
    lines.append("canonical 锚 = fbref 英文名（见 odds.db.teams.canonical_en）。")
    lines.append("本文件是全库**唯一**允许定义球队别名表的位置；任何散表 alias 均为 legacy，")
    lines.append("由 check_player_data_gap.py ③ 防复发锁监控。P0 阶段不切换任何消费者。")
    lines.append('"""')
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("CANONICAL_CN = {")
    for ce in canon_list:
        cn = canonical_cn.get(ce)
        lines.append(f"    {ce!r}: {cn!r},")
    lines.append("}")
    lines.append("")
    lines.append("TEAM_ALIASES = {")
    for ce in canon_list:
        lines.append(f"    {ce!r}: {merged[ce]!r},")
    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append("def normalize_team_name(raw: str) -> str | None:")
    lines.append("    \"\"\"原始队名（中/英/别名）→ canonical_en（fbref 英文名）。")
    lines.append("")
    lines.append("    两级匹配：canonical_en/别名精确命中优先；否则对每个 canonical_en 的 alias")
    lines.append("    列表做子串兜底（中文短简写允许双向，英文仅 name⊆raw 方向，避免通用词误配）。")
    lines.append("    \"\"\"")
    lines.append("    if not raw:")
    lines.append("        return None")
    lines.append("    raw = str(raw).strip()")
    lines.append("    rl = raw.lower()")
    lines.append("    # 1. 精确命中 canonical_en")
    lines.append("    for ce in TEAM_ALIASES:")
    lines.append("        if ce.lower() == rl:")
    lines.append("            return ce")
    lines.append("    # 2. 精确命中别名")
    lines.append("    for ce, aliases in TEAM_ALIASES.items():")
    lines.append("        for a in aliases:")
    lines.append("            if a.lower() == rl:")
    lines.append("                return ce")
    lines.append("    # 3. 子串兜底（中文短简写双向，英文仅 alias⊆raw）")
    lines.append("    has_cn = any('\\u4e00' <= c <= '\\u9fff' for c in raw)")
    lines.append("    for ce, aliases in TEAM_ALIASES.items():")
    lines.append("        for a in aliases:")
    lines.append("            al = a.lower()")
    lines.append("            if not al:")
    lines.append("                continue")
    lines.append("            if al in rl and not (al.isascii() and len(al) < 4):")
    lines.append("                return ce")
    lines.append("            if has_cn and rl in al:")
    lines.append("                return ce")
    lines.append("    return None")
    lines.append("")
    TEAM_NAMES_PY.write_text("\n".join(lines), encoding="utf-8")


def write_ledger(canon_list, canonical_cn, merged, drift, source_sizes, cn_unmapped):
    ledger = {
        "canonical_count": len(canon_list),
        "canonical_cn_unmapped": cn_unmapped,
        "source_sizes": source_sizes,
        "drift_unresolved_per_source": {k: v for k, v in drift.items() if v},
        "teams": [
            {"team_id": i + 1, "canonical_en": ce, "canonical_cn": canonical_cn.get(ce),
             "alias_count": len(merged[ce])}
            for i, ce in enumerate(canon_list)
        ],
    }
    LEDGER_JSON.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
def main() -> None:
    print("=" * 72)
    print("P0 球队身份层构建（纯加法，不切消费者）")
    print("=" * 72)

    sources = gather_sources()
    source_sizes = {k: len(v) for k, v in sources.items()}
    print("\n[1] 散表 alias 源规模：")
    for k, n in source_sizes.items():
        print(f"    {k}: {n}")

    # canonical 集
    conn = sqlite3.connect(str(ODDS_DB))
    canon_list = sorted(r[0] for r in conn.execute(CANON_SQL))
    conn.close()
    canon_set = set(canon_list)
    print(f"\n[2] canonical(fbref) 球队数: {len(canon_list)}")

    ta = _load_module_attr("team_name_mapping", "TEAM_ALIASES") or {}
    ta_reverse = build_ta_reverse(ta)

    # canonical_cn
    canonical_cn: dict[str, str | None] = {}
    cn_unmapped: list[str] = []
    for ce in canon_list:
        cn = resolve_canonical_cn(ce, sources, ta_reverse)
        canonical_cn[ce] = cn
        if cn is None:
            cn_unmapped.append(ce)
    print(f"[3] canonical_cn 未解析: {len(cn_unmapped)}")
    for c in cn_unmapped:
        print(f"    ⚠️ {c!r}")

    # 合并 alias
    merged, drift = merge_aliases(canon_list, sources, ta, canonical_cn)
    print(f"\n[4] 合并后 alias 总条数: {sum(len(v) for v in merged.values())}")
    unresolved = sum(len(v) for v in drift.values())
    print(f"    未解析 raw 条目: {unresolved}")
    for src, items in drift.items():
        if items:
            print(f"    {src}: {len(items)} 条 → {items[:8]}")

    # 写 teams 表（team_id = 排序序号，确定性）
    rows = [(i + 1, ce, canonical_cn.get(ce)) for i, ce in enumerate(canon_list)]
    write_teams_table(rows)
    print(f"\n[5] teams 表写入: {len(rows)} 行（team_id 1..{len(rows)}）")

    # 写 team_names.py
    write_team_names_py(canon_list, canonical_cn, merged)
    print(f"[6] team_names.py 写出: {TEAM_NAMES_PY}")

    # 写 diff 台账
    write_ledger(canon_list, canonical_cn, merged, drift, source_sizes, cn_unmapped)
    print(f"[7] diff 台账: {LEDGER_JSON}")

    # 验收
    conn = sqlite3.connect(str(ODDS_DB))
    n_teams = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    dup = conn.execute(
        "SELECT canonical_en, COUNT(*) c FROM teams GROUP BY canonical_en HAVING c > 1").fetchall()
    conn.close()
    print(f"\n[验收] teams 行数={n_teams} | 重复 canonical_en={dup}")
    assert n_teams == len(canon_list), "teams 行数 != canonical 数"
    assert not dup, "存在重复 canonical_en"
    print("✅ P0 身份层构建完成（纯加法，未切任何消费者）")


if __name__ == "__main__":
    main()
