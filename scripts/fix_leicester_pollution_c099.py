# -*- coding: utf-8 -*-
"""莱斯特城 match_id 污染修复（C-20261014-099，复用 C-20260918-023 模式）。

背景（C-098 审计实锤）：team_name_mapping.normalize_team_name 把 'Leicester'
错映射为 '莱切'（Lecce），4 张时序表 244 个 distinct match_id 被污染
（正确为 '莱切斯特城'），18,198 行全在特征对齐消费表：
  score_history 15,204 / wdl_history 1,201 / handicap_history 1,186 / total_goals_history 607。

修复单位 = team_name_pollution_audit.json 中 wrong='莱切' 的 CONFIRMED match_id 集合。
真莱切（意甲 Lecce）行全部在集合外，不受任何影响。

执行纪律（C-023 模式）：备份 → dry-run → 事务内级联 UPDATE →
build_match_alignment 覆盖率复核，任一表覆盖率下降即 ROLLBACK。

【状态（C-20261015-101）】本修复已于 2026-10-15 执行完毕（18,198 行、244 个 match_id，
覆盖率 wdl/hcp/tg/score 全部上升且分母不变、逐槽目标变更 0、254 维 hash 不变）。
执行后再跑本脚本：--execute 会因固定输入消失而零更新（幂等安全）；
若审计 JSON 被重建为"修复后版本"（莱切实锤=0），断言3 的规模校验会**主动中止**——
这是预期特性（防止对已修复数据重复施加修复），不是缺陷。

用法:
  python fix_leicester_pollution_c099.py --dry-run    # 只读预览（默认）
  python fix_leicester_pollution_c099.py --backup     # 导出受影响行 JSON 备份
  python fix_leicester_pollution_c099.py --execute    # 事务内执行 + 覆盖率回归自动回滚
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
sys.path.insert(0, str(SCRIPTS))
DB = BASE / "data" / "odds.db"
AUDIT_JSON = SCRIPTS / "team_name_pollution_audit.json"
BACKUP_DIR = BASE / "backups"

TABLES = ["wdl_history", "handicap_history", "total_goals_history", "score_history"]
MID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+?)_(.+)$")

from team_names import CANONICAL_CN, normalize_team_name as tn_norm  # noqa: E402


def load_fix_inputs() -> tuple[set[str], dict[str, str], str]:
    """从审计 JSON 固化 (实锤 match_id 集, old→new 映射, 目标中文名)，带六重断言。"""
    audit = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))

    # 断言1：探针语义未变（莱切 → Leicester City）
    assert audit["probes"].get("莱切") == ["Leicester City"], "探针定义与审计时不一致，禁止修复"

    # 断言2：目标中文名来自 P0 单一源
    target = CANONICAL_CN.get("Leicester City")
    assert target == "莱切斯特城", f"目标名异常: {target!r}（期望 莱切斯特城）"
    assert tn_norm(target) == "Leicester City", "目标名无法归一回 canonical，禁止修复"

    mids: set[str] = set()
    for r in audit["confirmed"]:
        if r["wrong"] == "莱切" and "match_id" in r:
            mids.add(r["match_id"])
    # 断言3：实锤集合非空且规模与审计一致
    assert 200 <= len(mids) <= 300, f"实锤 match_id 数异常: {len(mids)}"

    fix_map: dict[str, str] = {}
    for mid in mids:
        m = MID_RE.match(mid)
        # 断言4：格式可解析且恰有一侧为 莱切
        assert m, f"无法解析: {mid}"
        d, h, a = m.groups()
        assert (h == "莱切") != (a == "莱切"), f"莱切侧异常: {mid}"
        nh = target if h == "莱切" else h
        na = target if a == "莱切" else a
        fix_map[mid] = f"{d}_{nh}_{na}"
    # 断言5：映射无自反、无新冲突
    assert all(o != n for o, n in fix_map.items())
    assert len(set(fix_map.values())) == len(fix_map), "新 match_id 出现合并冲突"
    return mids, fix_map, target


def alignment_coverage(conn: sqlite3.Connection) -> dict[str, tuple[int, int]]:
    """真实 build_match_alignment 覆盖率：{表: (distinct数, 对齐数)}。"""
    from feature_utils import build_match_alignment
    out = {}
    for t in TABLES:
        total = conn.execute(f"SELECT COUNT(DISTINCT match_id) FROM {t}").fetchone()[0]
        out[t] = (total, len(build_match_alignment(conn, t)))
    return out


def collision_precheck(conn: sqlite3.Connection, fix_map: dict[str, str]) -> list[tuple[str, str, int]]:
    """新 match_id 在任一表已存在 → 修复前必须清零。"""
    hits = []
    for t in TABLES:
        for new in fix_map.values():
            c = conn.execute(f"SELECT COUNT(1) FROM {t} WHERE match_id=?", (new,)).fetchone()[0]
            if c:
                hits.append((t, new, c))
    return hits


def do_dry_run(conn: sqlite3.Connection, mids: set[str], fix_map: dict[str, str]) -> None:
    print(f"[dry-run] 实锤 match_id: {len(mids)} | 目标名: 莱切斯特城")
    print("\n按表影响行数:")
    total_rows = 0
    for t in TABLES:
        cnt = 0
        for old in mids:
            cnt += conn.execute(f"SELECT COUNT(1) FROM {t} WHERE match_id=?", (old,)).fetchone()[0]
        total_rows += cnt
        print(f"  {t}: {cnt} 行")
    print(f"  合计: {total_rows} 行")

    hits = collision_precheck(conn, fix_map)
    print(f"\n新 match_id 碰撞预检: {len(hits)} 处" + ("（通过）" if not hits else f"（必须清零）{hits[:5]}"))

    print("\n样例（前 8 条）:")
    for i, (old, new) in enumerate(list(fix_map.items())[:8]):
        print(f"  {old}\n    -> {new}")

    # 通道0 预后：matches 同日 fixture 的中文名拼写
    print("\nmatches 侧拼写抽样（决定通道0命中率）:")
    sample_dates = sorted({m[:10] for m in list(mids)[:6]})
    for d in sample_dates:
        row = conn.execute(
            "SELECT match_id FROM matches WHERE match_date=? AND (match_id LIKE '%莱切%' OR match_id LIKE '%Leicester%')",
            (d,)).fetchall()
        print(f"  {d}: {[r[0] for r in row][:3]}")

    cov = alignment_coverage(conn)
    print("\n对齐覆盖率基线（修复后不得下降）:")
    for t, (total, aligned) in cov.items():
        print(f"  {t}: {aligned}/{total} = {aligned/total:.4f}")
    conn.close()
    print("\n[dry-run] 未修改任何数据。--backup 导出备份，--execute 在窗口内执行。")


def do_backup(conn: sqlite3.Connection, mids: set[str]) -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"c099_leicester_backup_{ts}.json"
    dump: dict[str, list] = {}
    for t in TABLES:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{t}')")]
        rows = []
        for old in mids:
            for row in conn.execute(f"SELECT * FROM {t} WHERE match_id=?", (old,)).fetchall():
                rows.append(dict(zip(cols, row)))
        dump[t] = rows
        print(f"  {t}: 备份 {len(rows)} 行")
    out.write_text(json.dumps({"fix_map_note": "old match_id -> new match_id 见 fix_map",
                               "rows": dump}, ensure_ascii=False), encoding="utf-8")
    print(f"备份完成: {out}")
    return out


def do_execute(conn: sqlite3.Connection, mids: set[str], fix_map: dict[str, str]) -> None:
    hits = collision_precheck(conn, fix_map)
    if hits:
        print(f"[中止] 新 match_id 碰撞 {len(hits)} 处，先人工核查: {hits[:5]}")
        conn.close()
        raise SystemExit(2)

    cov_before = alignment_coverage(conn)
    print("覆盖率（修复前）:")
    for t, (total, aligned) in cov_before.items():
        print(f"  {t}: {aligned}/{total} = {aligned/total:.4f}")

    conn.isolation_level = None
    try:
        conn.execute("BEGIN")
        grand = 0
        for t in TABLES:
            updated = 0
            for old, new in fix_map.items():
                cur = conn.execute(f"UPDATE {t} SET match_id=? WHERE match_id=?", (new, old))
                updated += cur.rowcount
            grand += updated
            print(f"  {t}: 更新 {updated} 行")
        cov_after = alignment_coverage(conn)
        print("覆盖率（修复后）:")
        regress = []
        for t, (total, aligned) in cov_after.items():
            b = cov_before[t][1]
            print(f"  {t}: {aligned}/{total} = {aligned/total:.4f}（前 {b}，Δ{aligned-b:+d}）")
            if aligned < b:
                regress.append(t)
        if regress:
            conn.execute("ROLLBACK")
            print(f"\n[回滚] 覆盖率下降: {regress} —— 数据未变更")
            raise SystemExit(3)
        conn.execute("COMMIT")
        print(f"\n[完成] 共更新 {grand} 行，244 个 match_id 修复，覆盖率全部不降。")
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        conn.execute("ROLLBACK")
        print(f"\n[错误] {e} —— 已回滚")
        raise
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="莱斯特城 match_id 污染修复（C-099）")
    ap.add_argument("--dry-run", action="store_true", help="只读预览")
    ap.add_argument("--backup", action="store_true", help="导出受影响行 JSON 备份")
    ap.add_argument("--execute", action="store_true", help="事务内执行 + 覆盖率回归自动回滚")
    args = ap.parse_args()
    if not (args.dry_run or args.backup or args.execute):
        ap.print_help()
        return

    mids, fix_map, _ = load_fix_inputs()
    conn = sqlite3.connect(str(DB))
    if args.dry_run:
        do_dry_run(conn, mids, fix_map)
    elif args.backup:
        do_backup(conn, mids)
    elif args.execute:
        do_execute(conn, mids, fix_map)


if __name__ == "__main__":
    main()
