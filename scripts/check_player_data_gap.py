# -*- coding: utf-8 -*-
"""球员级数据「缺口 vs 真实赛程间歇」自检（C-20261014-091 预防措施）

背景（C-091）：
    2026-09-22~10-09 出现「全库 match_player_stats 无球员级数据」，
    曾被误判为「采集缺口、待补采」。实测证伪——该窗为 26/27 赛季的
    **真实赛程间歇**（FIFA 窗口拉长的秋季长窗）：
      · fbref_match_mapping / matches / wdl_history 三源同窗均为 0 行
      · 轮次号连续无跳轮（英超 5→6、西甲 7→8、意甲 5→6、德甲 4→5、法甲 5→6）
      · 26/27 英超 mapping 完整 38 轮/380 场，w5=09-19/20 → w6=10-10/13
    → pa_rest_days 22~24 为**真值**，非缺口污染。

本脚本把该诊断固化为一次命令，防止未来再次误判：
    判据：**赛程有比赛（mapping 有行）但球员数据缺失 → 真缺口，报警**；
          赛程本身无比赛（mapping 0 行）→ 真实间歇，不报警。
    另含 ③ 队名一致性检查（C-20261014-094）：match_player_stats / match_lineups 的
    team 须全为 fbref_match_mapping 的英文规范名，否则相关场次 JOIN 会被漏掉。

只读，不写任何库；不改动生产链路。

用法：
    python scripts/check_player_data_gap.py                      # 近 30 天
    python scripts/check_player_data_gap.py --start 2026-09-15 --end 2026-10-14
    python scripts/check_player_data_gap.py --date 2026-10-13 --min-gap 14
    python scripts/check_player_data_gap.py --date 2026-10-13 --leagues 英超,西甲
"""
from __future__ import annotations

import argparse
import ast
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"

HEADERS = ["日期", "赛程场次", "有球员数据场次", "状态"]


def _pdate(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def daily_compare(conn: sqlite3.Connection, start: str, end: str, leagues: list[str] | None) -> None:
    """逐日对照「赛程场次(mapping)」vs「有球员数据的场次」，标出真缺口。"""
    lg_filter = ""
    params: list = [start, end]
    if leagues:
        lg_filter = " AND fmm.league IN (%s)" % ",".join("?" * len(leagues))
        params += leagues

    sched = dict(conn.execute(
        "SELECT fmm.match_date, COUNT(*) FROM fbref_match_mapping fmm "
        "WHERE fmm.match_date BETWEEN ? AND ?" + lg_filter + " GROUP BY fmm.match_date",
        params).fetchall())

    pparams: list = [start, end]
    if leagues:
        pparams += leagues
    stats = dict(conn.execute(
        "SELECT fmm.match_date, COUNT(DISTINCT mps.fbref_match_id) "
        "FROM match_player_stats mps JOIN fbref_match_mapping fmm "
        "  ON mps.fbref_match_id = fmm.fbref_match_id "
        "WHERE fmm.match_date BETWEEN ? AND ?" + lg_filter + " GROUP BY fmm.match_date",
        pparams).fetchall())

    print("=" * 72)
    print("① 逐日对照：赛程场次 vs 有球员数据场次（真缺口 = 有赛程但无球员数据）")
    print("=" * 72)
    print(f"  {'日期':<12}{'赛程场次':>10}{'有球员数据':>12}   状态")
    d0, d1 = _pdate(start), _pdate(end)
    gaps, breaks, ok = [], [], []
    cur = d0
    while cur <= d1:
        ds = cur.isoformat()
        n_s = sched.get(ds, 0)
        n_p = stats.get(ds, 0)
        if n_s == 0:
            status = "— 无赛程（真实间歇）"
            breaks.append(ds)
        elif n_p == 0:
            status = "⚠️ 真缺口：有赛程无球员数据"
            gaps.append(ds)
        else:
            status = "OK"
            ok.append(ds)
        if n_s or n_p:
            print(f"  {ds:<12}{n_s:>10}{n_p:>12}   {status}")
        cur += timedelta(days=1)

    print("-" * 72)
    print(f"  小结：无赛程日 {len(breaks)} 天 | 有数据日 {len(ok)} 天 | "
          f"**真缺口日 {len(gaps)} 天**")
    if gaps:
        print(f"  ⚠️ 报警：以下日期赛程有比赛但球员数据缺失 → 需排查采集：{gaps}")
    else:
        print("  ✅ 无真缺口：所有「有赛程」日期均已采集球员数据；空窗均为真实间歇。")


def long_gap_teams(conn: sqlite3.Connection, as_of: str, min_gap: int, leagues: list[str] | None) -> None:
    """列出「距 as_of 已停更 ≥min_gap 天」的球队，并判别其空窗是缺口还是真实间歇。

    判别：该队末场日期 → as_of 之间若 mapping 仍登记有该队的比赛 → 真缺口；
          若无 → 真实间歇（如 26/27 秋季长窗）。
    """
    lg_filter = ""
    params: list = []
    if leagues:
        lg_filter = " AND fmm.league IN (%s)" % ",".join("?" * len(leagues))
        params += leagues

    rows = conn.execute(
        "SELECT fmm.league, mps.team, MAX(fmm.match_date) AS last_d "
        "FROM match_player_stats mps JOIN fbref_match_mapping fmm "
        "  ON mps.fbref_match_id = fmm.fbref_match_id "
        "WHERE fmm.match_date <= ?" + lg_filter + " GROUP BY fmm.league, mps.team",
        [as_of] + params).fetchall()

    print()
    print("=" * 72)
    print(f"② 长间隔球队自检（截至 {as_of}，末场距今 ≥ {min_gap} 天）")
    print("=" * 72)
    if not rows:
        print("  （无数据）")
        return

    flagged = []
    for lg, team, last_d in rows:
        gap = (_pdate(as_of) - _pdate(last_d)).days
        if gap < min_gap:
            continue
        n_play = conn.execute(
            "SELECT COUNT(*) FROM fbref_match_mapping "
            "WHERE match_date > ? AND match_date <= ? AND league = ? "
            "  AND (home_team_fbref = ? OR away_team_fbref = ?)",
            (last_d, as_of, lg, team, team)).fetchone()[0]
        verdict = ("⚠️ 真缺口：间隔期内有已登记比赛但无球员数据"
                   if n_play else "✅ 真实间歇：间隔期内无比赛")
        flagged.append((lg, team, last_d, gap, n_play, verdict))

    if not flagged:
        print(f"  ✅ 无球队停更 ≥ {min_gap} 天。")
        return
    flagged.sort(key=lambda x: -x[3])
    for lg, team, last_d, gap, n_play, verdict in flagged:
        print(f"  [{lg}] {team:<28} 末场 {last_d} 距今 {gap} 天 "
              f"(期内赛程 {n_play} 场) → {verdict}")
    alarms = [f for f in flagged if f[4]]
    print("-" * 72)
    if alarms:
        print(f"  ⚠️ 需排查：{len(alarms)} 支球队疑似真缺口（见上）。")
    else:
        print(f"  ✅ 全部 {len(flagged)} 支长间隔球队均判定为真实间歇，无采集缺口。")


def team_name_consistency(conn: sqlite3.Connection) -> None:
    """③ 全库队名一致性断言（C-20261014-094/095，根治防复发门）。

    扫描 odds.db 所有「列名含 team」的列，逐列给出：
      · 含中日韩字符比例 → 识别**中英混列**（同列两套约定 = JOIN 定时炸弹）
      · 无法解析到「规范英文名 或 已知别名」的值 → 识别**真漂移**（新变体）
    canonical = fbref_match_mapping 的英文队名；别名表复用 TEAM_ALIASES（单一权威源）。
    只读，不写库。
    """
    print()
    print("=" * 72)
    print("③ 全库队名一致性（canonical=英文 fbref 名；别名源=TEAM_ALIASES）")
    print("=" * 72)

    canon = set()
    for col in ("home_team_fbref", "away_team_fbref"):
        canon |= set(r[0] for r in conn.execute(
            f"SELECT DISTINCT {col} FROM fbref_match_mapping WHERE {col} IS NOT NULL"))
    try:
        from team_name_mapping import TEAM_ALIASES  # type: ignore
        alias = set()
        for k, vs in TEAM_ALIASES.items():
            alias.add(k.lower())
            alias |= {str(v).lower() for v in vs}
    except Exception as e:  # noqa: BLE001
        alias = set()
        print(f"  （警告：TEAM_ALIASES 不可用，仅按 canonical 判定：{e}）")
    canon_l = {c.lower() for c in canon}
    known = canon_l | alias

    cjk = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")
    num = re.compile(r"^-?\d+(\.\d+)?$")
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    mixed, drifted = [], []
    for t in tables:
        try:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})")]
        except Exception:
            continue
        for c in [x for x in cols if "team" in x.lower()]:
            lc = c.lower()
            # 白名单：非队名语义列（数字 ID / 主客场标记）不参与队名一致性判定
            if lc.endswith("_id") or lc.endswith("_side"):
                continue
            try:
                vals = [r[0] for r in conn.execute(
                    f"SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL AND {c}<>''")]
            except Exception:
                continue
            if not vals:
                continue
            if all(num.match(str(v)) for v in vals):
                continue  # 纯数字列（team 数字编码）非队名
            n_cjk = sum(1 for v in vals if cjk.search(str(v)))
            bad = sorted(v for v in vals if str(v).lower() not in known)
            if 0 < n_cjk < len(vals):
                mixed.append((t, c, n_cjk, len(vals)))
            if bad:
                drifted.append((t, c, bad))

    for t, c, n_cjk, tot in mixed:
        print(f"  ⚠️ 混列 [{t}].{c}: 中文 {n_cjk}/{tot} ← 同列两套约定")
    for t, c, bad in drifted:
        print(f"  ⚠️ 漂移 [{t}].{c}: {len(bad)} 个值不在规范名/别名内 → {bad[:6]}")
    if not mixed:
        print("  ✅ 无中英混列")
    if not drifted:
        print("  ✅ 无队名漂移（所有值均可解析到规范名或已知别名）")
    if mixed or drifted:
        print(f"  ⚠️ 合计：混列 {len(mixed)} 个、漂移列 {len(drifted)} 个 → 需纳入归一/RETRAIN 窗口处理。")


# --------------------------------------------------------------------------
# ④ 队名别名单一源防复发锁（C-20261014-096 P0）
# --------------------------------------------------------------------------
# 规则：任何球队别名定义只允许出现在 team_names.py；出现在其他文件 = 门禁 FAIL。
# P0 阶段不删 legacy 散表（P1 才删），故 legacy 文件列入白名单但须满足 superset。
LEGACY_ALIAS_FILES = {
    "team_name_mapping.py", "feature_utils.py", "fbref_anti_cloudflare.py",
    "sporttery_sync_to_odds.py", "score_match_enhancer.py", "create_odds_match_mapping.py",
    # 以下为既有 legacy 队名散表，P1 阶段统一合并到 team_names.py 后删除
    "_fix_match_names.py", "feature_engineering_pipeline.py", "generate_team_attributes.py",
    "merge_epl_full_season.py", "merge_understat_to_matches.py",
}
SINGLE_SOURCE = "team_names.py"
# 模块级 alias 表变量名模式（team_names.py 之外出现即违规）
# 排除 LEAGUE 相关（联赛名映射，非队名别名）
_ALIAS_NAME_RE = re.compile(r"^(?!.*LEAGUE)(TEAM_[A-Z_]*|.*ALIASES?|.*TEAM_NAME_MAP|.*NAME_MAP|.*TEAM_MAP)$")


def _has_alias_dict(filepath: Path) -> list[str]:
    """用 ast 精确检测：文件是否定义了「值为 dict 字面量」的 alias 表变量。"""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and _ALIAS_NAME_RE.match(tgt.id):
                hits.append(tgt.id)
    return hits


def _collect_source_aliases() -> dict[str, set[str]]:
    """从 legacy 散表 + team_names 收集所有 alias 字符串集合。"""
    result: dict[str, set[str]] = {}
    try:
        from team_name_mapping import TEAM_ALIASES as _ta  # type: ignore
        s = set()
        for cn, aliases in _ta.items():
            s.add(cn); s |= {str(a) for a in aliases}
        result["team_name_mapping.TEAM_ALIASES"] = s
    except Exception:
        pass
    try:
        from feature_utils import TEAM_NAME_MAP as _m  # type: ignore
        result["feature_utils.TEAM_NAME_MAP"] = set(_m.keys()) | set(_m.values())
    except Exception:
        pass
    try:
        from fbref_anti_cloudflare import FBREF_TEAM_NAME_MAP as _m  # type: ignore
        result["fbref_anti_cloudflare.FBREF_TEAM_NAME_MAP"] = set(_m.keys()) | set(_m.values())
    except Exception:
        pass
    try:
        from sporttery_sync_to_odds import TEAM_NAME_MAP as _m  # type: ignore
        result["sporttery_sync_to_odds.TEAM_NAME_MAP"] = set(_m.keys()) | set(_m.values())
    except Exception:
        pass
    try:
        from team_names import TEAM_ALIASES as _t  # type: ignore
        s = set()
        for ce, aliases in _t.items():
            s.add(ce); s |= {str(a) for a in aliases}
        result["team_names.TEAM_ALIASES"] = s
    except Exception:
        pass
    # 其他既有 legacy 散表（P1 待合并）
    for mod, attr in [
        ("_fix_match_names", "NAME_MAP"),
        ("feature_engineering_pipeline", "TEAM_NAME_MAPPING"),
        ("generate_team_attributes", "TEAM_KEY_MAP_EN"),
        ("merge_epl_full_season", "TEAM_MAPPINGS"),
        ("merge_understat_to_matches", "UNDERSTAT_ALIASES"),
    ]:
        try:
            m = __import__(mod)
            d = getattr(m, attr, {})
            s = set()
            for k, v in d.items():
                s.add(str(k))
                if isinstance(v, (list, tuple, set)):
                    s |= {str(x) for x in v}
                else:
                    s.add(str(v))
            result[f"{mod}.{attr}"] = s
        except Exception:
            pass
    return result


def alias_single_source_lock() -> bool:
    """④ 防复发锁：team_names.py 必须是所有别名的超集；新 alias 文件 = FAIL。"""
    print()
    print("=" * 72)
    print("④ 队名别名单一源防复发锁（team_names.py 为唯一权威源）")
    print("=" * 72)

    sources = _collect_source_aliases()
    single = sources.get("team_names.TEAM_ALIASES", set())
    try:
        from team_names import normalize_team_name as _tn_norm  # type: ignore
    except Exception:
        _tn_norm = None  # noqa: N806
    if not single:
        print("  ⚠️ team_names.TEAM_ALIASES 不可用 → 锁降级为仅检测新文件")
    ok = True

    # ① superset 检查：每个 legacy 源的「属于 canonical 队」的 alias 必须都在 team_names 里
    #    （非 5 大联赛队别名如 Braunschweig/Rodez 不在 canonical 集，属正常出界，跳过）
    for src, aliases in sources.items():
        if src == "team_names.TEAM_ALIASES":
            continue
        in_scope = set()
        out_scope = 0
        for a in aliases:
            if _tn_norm and _tn_norm(a) is not None:
                in_scope.add(a)
            elif a in single:
                in_scope.add(a)
            else:
                out_scope += 1
        missing = sorted(a for a in in_scope if a not in single)
        if missing:
            ok = False
            print(f"  ❌ {src}: {len(missing)} 个 canonical 队别名未收入 team_names → {missing[:8]}")
        else:
            print(f"  ✅ {src}: 全部 {len(in_scope)} 个 canonical 队别名已覆盖（出界非 5 大队 {out_scope} 个跳过）")

    # ② 新 alias 文件检测：扫描 scripts/ + features/ 下所有 .py（跳过 archive/）
    import os
    scan_dirs = [BASE / "scripts", BASE / "features"]
    offenders: list[str] = []
    for d in scan_dirs:
        if not d.exists():
            continue
        for f in sorted(d.rglob("*.py")):
            if "archive" in f.parts:
                continue
            fname = f.name
            if fname == SINGLE_SOURCE or fname in LEGACY_ALIAS_FILES:
                continue
            if "test" in fname.lower():
                continue
            hits = _has_alias_dict(f)
            if hits:
                offenders.append(f"{f.relative_to(BASE)}: {hits}")
    if offenders:
        ok = False
        print("  ❌ 发现 team_names.py 之外的新 alias 定义（值为 dict 的 alias 表）：")
        for o in offenders[:20]:
            print(f"    - {o}")
    else:
        print("  ✅ 无 team_names.py 之外的新 alias 定义（legacy 散表已白名单，待 P1 删除）")

    if ok:
        print("  ✅ 防复发锁通过：单一源 superset + 无新 alias 文件")
    else:
        print("  ❌ 防复发锁 FAIL —— 见上")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="球员级数据缺口 vs 真实赛程间歇 自检（只读）")
    ap.add_argument("--date", help="截止日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--start", help="逐日对照起始日（默认截止日 -30 天）")
    ap.add_argument("--end", help="逐日对照截止日（默认同 --date）")
    ap.add_argument("--min-gap", type=int, default=14, help="长间隔阈值（天，默认 14）")
    ap.add_argument("--leagues", help="逗号分隔联赛过滤，如 英超,西甲（默认全部）")
    args = ap.parse_args()

    as_of = args.date or date.today().isoformat()
    end = args.end or as_of
    if args.start:
        start = args.start
        if _pdate(start) > _pdate(end):
            start, end = end, start
    else:
        start = (_pdate(end) - timedelta(days=30)).isoformat()
    leagues = [s.strip() for s in args.leagues.split(",") if s.strip()] if args.leagues else None

    if not DB.exists():
        raise SystemExit(f"DB 不存在：{DB}")
    conn = sqlite3.connect(str(DB))
    try:
        print(f"DB: {DB}")
        print(f"区间: {start} ~ {end} | 联赛: {leagues or '全部'}")
        print()
        daily_compare(conn, start, end, leagues)
        long_gap_teams(conn, as_of, args.min_gap, leagues)
        team_name_consistency(conn)
        lock_ok = alias_single_source_lock()
    finally:
        conn.close()
    if not lock_ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
