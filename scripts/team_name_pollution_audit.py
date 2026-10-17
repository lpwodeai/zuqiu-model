# -*- coding: utf-8 -*-
"""存量污染审计（只读，C-097 后续 / C-098）。

探针库 = team_name_diff_ledger.json 中 OLD_DISAGREE 且 tm 语义错误的记录：
  错误目标名（tm 输出，如 '曼城'）→ 正确 canonical（如 'Manchester United'）。

方法：分表扫描 team 列值 ∈ {错误目标名} 的嫌疑行，逐行用 fbref_match_mapping
（权威 fixture：match_date + home/away_team_fbref）对证：
  CONFIRMED   = 该日期存在「正确队 vs 对手」的 fixture → 实锤污染
  LEGIT       = 该日期存在「错误名本队 vs 对手」的 fixture → 误伤（真曼城场次）
  UNVERIFIABLE = 缺日期/对手信息或两不沾
产出三张表：①按表×探针 嫌疑/实锤/误伤计数 ②实锤 match_id 清单+正确名 ③传导评估。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
sys.path.insert(0, str(SCRIPTS))
ODDS_DB = BASE / "data" / "odds.db"

from team_names import normalize_team_name as tn_norm  # noqa: E402

# 优先审计表（对模型影响排序）；其余带 team 列的表也会扫到但归为展示类
PRIORITY = ["wdl", "handicap", "total_goals", "score_history", "odds500_match",
            "matches", "matches_archive", "sofascore_team_features",
            "match_mapping", "match_mapping_old", "league_standing", "team_elo_snapshot"]
MID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+?)_(.+)$")

# 伪影探针（证伪后剔除，理由见 C-098）：
#  '萨勒尼塔纳' —— teams 表同俱乐部双 canonical（133 Salernitana / 155 US Salernitana 1919）
#                 并存导致 c_tm≠c_tn；该中文名本身正确，非 tm 错映射。
ARTIFACT_PROBES = {"萨勒尼塔纳"}


def load_probes() -> dict[str, set[str]]:
    """错误目标名 -> {正确 canonical_en}。"""
    ledger = json.loads((SCRIPTS / "team_name_diff_ledger.json").read_text(encoding="utf-8"))
    probes: dict[str, set[str]] = defaultdict(set)
    for r in ledger["buckets"]["OLD_DISAGREE"]:
        if r["tm"] and r["c_tm"] and r["c_tn"] and r["c_tm"] != r["c_tn"]:
            probes[r["tm"]].add(r["c_tn"])
    return dict(probes)


def load_fbref_fixtures(conn: sqlite3.Connection) -> dict[str, set[tuple[str, str]]]:
    """match_date -> {(home_fbref, away_fbref)}（权威 fixture）。"""
    idx: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for d, h, a in conn.execute(
            "SELECT match_date, home_team_fbref, away_team_fbref FROM fbref_match_mapping"):
        if d and h and a:
            idx[str(d)[:10]].add((h, a))
    return idx


def verdict(fixtures: dict[str, set[tuple[str, str]]], date: str | None,
            wrong_name: str, true_set: set[str], opponent_raw: str | None,
            probes: dict[str, set[str]] | None = None) -> str:
    if not date:
        return "UNVERIFIABLE"
    date = str(date)[:10]
    # 对手候选：对手本身是探针名时，用其「正确 canonical」而非 tn 解析
    # （防交叉误配：'阿森纳_莱切' 行用 Leicester 而非 Lecce 对证阿森纳侧）
    opp_cands: set[str] = set()
    if opponent_raw:
        if probes and opponent_raw in probes:
            opp_cands |= probes[opponent_raw]
        oc = tn_norm(opponent_raw)
        if oc:
            opp_cands.add(oc)
    wrong_c = tn_norm(wrong_name)
    fx = fixtures.get(date, set())
    for t in true_set:
        for oc in opp_cands:
            if (t, oc) in fx or (oc, t) in fx:
                return "CONFIRMED"
    # 无对手信息时不做「真队当天有比赛」放水判定（平行联赛撞日期 → 假阳性）
    for oc in opp_cands:
        if wrong_c and ((wrong_c, oc) in fx or (oc, wrong_c) in fx):
            return "LEGIT"
    if wrong_c and not opp_cands and any(wrong_c in p for p in fx):
        return "LEGIT"
    return "UNVERIFIABLE"


def main() -> None:
    probes = load_probes()
    wrong_names = set(probes) - ARTIFACT_PROBES
    print(f"探针库: {len(probes)} 个错误目标名 → {sorted(probes)}")

    conn = sqlite3.connect(str(ODDS_DB))
    fixtures = load_fbref_fixtures(conn)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    per_table: dict[str, dict] = {}
    confirmed_mids: list[dict] = []

    for t in tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{t}')")]
        team_cols = [c for c in cols if "team" in c.lower()
                     and not c.lower().endswith("_id") and not c.lower().endswith("_side")]
        has_mid = "match_id" in cols
        if not team_cols and not has_mid:
            continue
        date_col = next((c for c in cols if c in ("match_date", "date")), None)
        home_col = next((c for c in team_cols if "home" in c.lower()), None)
        away_col = next((c for c in team_cols if "away" in c.lower()), None)
        single_col = next((c for c in team_cols if c in ("team", "team_name")), None)

        stats = defaultdict(lambda: [0, 0, 0])  # probe -> [suspect, confirmed, legit]
        try:
            rows = conn.execute(f"SELECT * FROM '{t}'").fetchall()
        except Exception:
            continue
        ci = {c: i for i, c in enumerate(cols)}
        for row in rows:
            # 情形 A：match_id 内含错误名
            if has_mid:
                mid = row[ci["match_id"]]
                if mid:
                    m = MID_RE.match(str(mid))
                    if m:
                        d, h, a = m.groups()
                        for side, val, opp in (("home", h, a), ("away", a, h)):
                            if val in wrong_names:
                                v = verdict(fixtures, d, val, probes[val], opp, probes)
                                stats[val][0] += 1
                                if v == "CONFIRMED":
                                    stats[val][1] += 1
                                    confirmed_mids.append({
                                        "table": t, "match_id": str(mid),
                                        "wrong": val, "true": sorted(probes[val])[0]})
                                elif v == "LEGIT":
                                    stats[val][2] += 1
            # 情形 B：home/away 列
            if home_col and away_col:
                d = row[ci[date_col]] if date_col else None
                for col, opp_col in ((home_col, away_col), (away_col, home_col)):
                    val = row[ci[col]]
                    if val in wrong_names:
                        v = verdict(fixtures, d, val, probes[val], row[ci[opp_col]], probes)
                        stats[val][0] += 1
                        if v == "CONFIRMED":
                            stats[val][1] += 1
                            rec = {"table": t, "col": col, "wrong": val,
                                   "true": sorted(probes[val])[0], "date": str(d)[:10] if d else None}
                            if has_mid:
                                rec["match_id"] = str(row[ci["match_id"]])
                            confirmed_mids.append(rec)
                        elif v == "LEGIT":
                            stats[val][2] += 1
            # 情形 C：单 team 列（league_standing/team_elo_snapshot 等，无对手）
            elif single_col:
                val = row[ci[single_col]]
                if val in wrong_names:
                    d = row[ci[date_col]] if date_col else None
                    v = verdict(fixtures, d, val, probes[val], None, probes)
                    stats[val][0] += 1
                    if v == "CONFIRMED":
                        stats[val][1] += 1
                    elif v == "LEGIT":
                        stats[val][2] += 1
        if stats:
            per_table[t] = {
                "priority": "🔴" if any(p in t for p in PRIORITY[:8]) else "🟡",
                "probes": {k: {"suspect": s[0], "confirmed": s[1], "legit": s[2]}
                           for k, s in sorted(stats.items())},
            }
    conn.close()

    # ① 污染行数总表
    print("\n=== ① 污染行数总表（嫌疑/实锤/误伤）===")
    tot = [0, 0, 0]
    for t, info in sorted(per_table.items()):
        ts = sum(p["suspect"] for p in info["probes"].values())
        tc = sum(p["confirmed"] for p in info["probes"].values())
        tl = sum(p["legit"] for p in info["probes"].values())
        tot[0] += ts; tot[1] += tc; tot[2] += tl
        print(f"  {info['priority']} {t}: 嫌疑 {ts} / 实锤 {tc} / 误伤 {tl}")
        for p, c in info["probes"].items():
            print(f"      探针[{p}]: {c['suspect']}/{c['confirmed']}/{c['legit']}")
    print(f"  合计: 嫌疑 {tot[0]} / 实锤 {tot[1]} / 误伤 {tot[2]}")

    # ② 实锤清单
    print(f"\n=== ② 实锤 match_id/行清单（{len(confirmed_mids)} 条，前 30）===")
    for r in confirmed_mids[:30]:
        print(f"  {r}")

    # ③ 传导评估
    print("\n=== ③ 传导评估 ===")
    red = sum(sum(p["confirmed"] for p in i["probes"].values())
              for t, i in per_table.items() if i["priority"] == "🔴")
    yellow = tot[1] - red
    print(f"  🔴 特征/对齐消费表实锤: {red}  |  🟡 展示类实锤: {yellow}")

    out = SCRIPTS / "team_name_pollution_audit.json"
    # C-102: JSON 写出改显式 --write（防无参/--help 意外重生成——已发生两次）
    if "--write" in sys.argv:
        out.write_text(json.dumps({"probes": {k: sorted(v) for k, v in probes.items()},
                                   "per_table": per_table,
                                   "confirmed": confirmed_mids,
                                   "total": {"suspect": tot[0], "confirmed": tot[1], "legit": tot[2],
                                              "red": red, "yellow": yellow}},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n审计台账已写出: {out}")
    else:
        print(f"\n(只读模式：未写 {out.name}；需落盘请加 --write)")


if __name__ == "__main__":
    main()
