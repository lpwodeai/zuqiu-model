# -*- coding: utf-8 -*-
"""p2_p4_dryrun.py — P2/P4 身份统一迁移 · 只读 dry-run（runbook §5 步骤 1-3）

⚠️ 本脚本**不修改 odds.db**。仅：
  ① 整库文件级备份（写入 data/backups/，不碰 DB）
  ② 影响清单：各带键表行数 + 键格式分布 + CN 键可映射率
  ③ 碰撞预检：折算后的新键在**本表内**是否已存在

映射策略（runbook §6 复用资产，优先级）：
  a. 表自带 match_id_en 列 → 直接取（通道2）
  b. match_id_mapping 桥表 sh_match_id→matches_match_id
  c. 队名中文→英文（team_names.CANONICAL_CN 反向）
  d. 已是英文键 → 视为无需迁移（unchanged）

用法：
  python scripts/p2_p4_dryrun.py            # 备份 + 全表分析
  python scripts/p2_p4_dryrun.py --no-backup
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"
BACKUP_DIR = PROJECT_DIR / "data" / "backups"
REPORT_DIR = PROJECT_DIR / "reports"

sys.path.insert(0, str(PROJECT_DIR / "scripts"))
try:
    from team_names import normalize_team_name  # canonical_en（含 TEAM_ALIASES 别名）
except Exception:  # noqa: BLE001
    def normalize_team_name(_raw):
        return None

CN_RE = re.compile(r"[\u4e00-\u9fff]")

# runbook §4 带键表（含 match_id 列）；sofascore_team_features 走 event_id 单列
TABLES_WITH_MATCH_ID = [
    "matches", "wdl_history", "handicap_history", "total_goals_history",
    "score_history", "model_predictions", "post_match_review", "matches_archive",
    "ab_test_log", "prematch_data_quality", "odds_grid_snapshot",
    "sofascore_post_match_cache",
]


def split_key(mid: str):
    """YYYY-MM-DD_home_away → (date, home, away)；解析失败返回 None。"""
    if not mid or len(mid) < 12 or mid[10:11] != "_":
        return None
    date = mid[:10]
    rest = mid[11:]
    if "_" not in rest:
        return None
    h, a = rest.split("_", 1)
    return date, h, a


def build_bridge(conn) -> dict:
    """match_id_mapping: sh_match_id -> matches_match_id。"""
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT sh_match_id, matches_match_id FROM match_id_mapping "
            "WHERE sh_match_id IS NOT NULL AND matches_match_id IS NOT NULL")}
    except sqlite3.Error:
        return {}


def translate_names(key: str):
    """CN/别名队名 → canonical_en（经 team_names.normalize_team_name，含别名表）。"""
    sp = split_key(key)
    if not sp:
        return None
    date, h, a = sp
    en_h, en_a = normalize_team_name(h), normalize_team_name(a)
    if not en_h or not en_a:
        return None
    return f"{date}_{en_h}_{en_a}"


def analyze_table(conn, table: str, bridge: dict) -> dict:
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
    has_en = "match_id_en" in cols
    sql = "SELECT match_id" + (", match_id_en" if has_en else "") + f' FROM "{table}"'
    rows = conn.execute(sql).fetchall()

    total = len(rows)
    existing = set()
    cn_rows = []
    for r in rows:
        mid = r[0]
        if mid:
            existing.add(mid)
        if mid and CN_RE.search(mid):
            cn_rows.append((mid, r[1] if has_en else None))

    stat = {
        "table": table, "total": total, "has_match_id_en": has_en,
        "en_keys": total - len(cn_rows), "cn_keys": len(cn_rows),
        "via_en_col": 0, "via_bridge": 0, "via_name": 0, "unmapped": 0,
        "collision": 0, "unmapped_sample": [],
    }

    new_keys = []
    for mid, en_col in cn_rows:
        cand = None
        if en_col:
            cand = en_col
            stat["via_en_col"] += 1
        if not cand and mid in bridge:
            cand = bridge[mid]
            stat["via_bridge"] += 1
        if not cand:
            cand = translate_names(mid)
            if cand:
                stat["via_name"] += 1
        if not cand:
            stat["unmapped"] += 1
            if len(stat["unmapped_sample"]) < 8:
                stat["unmapped_sample"].append(mid)
            continue
        if cand != mid:
            new_keys.append((mid, cand))

    # 碰撞预检：折算后新键已存在于本表（且非自身）
    for old, new in new_keys:
        if new in existing:
            stat["collision"] += 1

    stat["mapped"] = stat["via_en_col"] + stat["via_bridge"] + stat["via_name"]
    return stat


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ① 整库备份（文件级；不动 DB 内容）
    backup_path = None
    if not args.no_backup:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / f"odds.db.pre_p2p4_{ts}.bak"
        shutil.copy2(str(ODDS_DB), str(backup_path))
        print(f"[①] 备份完成: {backup_path}  ({backup_path.stat().st_size/1024/1024:.1f} MB)")

    conn = sqlite3.connect(str(ODDS_DB))
    bridge = build_bridge(conn)
    print(f"[i] 桥表 match_id_mapping 载入: {len(bridge)} 条")

    results = []
    print(f"[ii] 影响清单 + 碰撞预检（{len(TABLES_WITH_MATCH_ID)} 表）")
    print(f"{'表':<26}{'总行':>9}{'EN键':>9}{'CN键':>9}{'可映射':>9}{'不可映射':>10}{'碰撞':>7}")
    for t in TABLES_WITH_MATCH_ID:
        try:
            st = analyze_table(conn, t, bridge)
        except sqlite3.Error as e:
            print(f"{t:<26} ERROR: {e}")
            continue
        results.append(st)
        print(f"{t:<26}{st['total']:>9}{st['en_keys']:>9}{st['cn_keys']:>9}"
              f"{st['mapped']:>9}{st['unmapped']:>10}{st['collision']:>7}")
    conn.close()

    # ③ 报告
    tot_cn = sum(r["cn_keys"] for r in results)
    tot_map = sum(r["mapped"] for r in results)
    tot_unmap = sum(r["unmapped"] for r in results)
    tot_coll = sum(r["collision"] for r in results)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "backup": str(backup_path) if backup_path else None,
        "bridge_size": len(bridge),
        "summary": {"cn_keys": tot_cn, "mapped": tot_map, "unmapped": tot_unmap,
                    "collision": tot_coll,
                    "mapped_rate": round(tot_map / tot_cn, 4) if tot_cn else 0},
        "tables": results,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = REPORT_DIR / f"p2_p4_dryrun_{ts}.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [f"# P2/P4 迁移 dry-run 报告（{ts}）\n",
          f"> 只读 dry-run，**未修改 odds.db**。备份：{backup_path}\n",
          f"> 桥表 match_id_mapping：{len(bridge)} 条\n",
          "\n## 汇总\n",
          f"- CN 键合计 **{tot_cn}**｜可映射 **{tot_map}**（{tot_map/max(tot_cn,1):.1%}）"
          f"｜不可映射 **{tot_unmap}**｜**碰撞 {tot_coll}**\n",
          "\n## 分表明细\n",
          "| 表 | 总行 | EN键 | CN键 | 可映射 | 不可映射 | 碰撞 |\n",
          "|------|--:|--:|--:|--:|--:|--:|\n"]
    for r in results:
        md.append(f"| {r['table']} | {r['total']} | {r['en_keys']} | {r['cn_keys']} "
                  f"| {r['mapped']} | {r['unmapped']} | {r['collision']} |\n")
    md.append("\n## 不可映射样例（最多 8/表）\n")
    for r in results:
        if r["unmapped_sample"]:
            md.append(f"- **{r['table']}**（{r['unmapped']} 条）：{r['unmapped_sample']}\n")
    out_md = REPORT_DIR / f"p2_p4_dryrun_{ts}.md"
    out_md.write_text("".join(md), encoding="utf-8")

    print(f"\n[③] 报告: {out_md}\n[③] JSON: {out_json}")
    print(f"[汇总] CN键={tot_cn} 可映射={tot_map}({tot_map/max(tot_cn,1):.1%}) "
          f"不可映射={tot_unmap} 碰撞={tot_coll}")


if __name__ == "__main__":
    main()
