# -*- coding: utf-8 -*-
"""队名污染 tripwire 门（C-102）：只读 + delta 告警 + 永不自动修。

角色定位：管"发现"，不管"根治"——team_name_mapping.py 错映射与写入方切换（P1）
落地前，新污染理论上仍会产生；本门让污染产生后立刻被发现。

复用 team_name_pollution_audit 的 fbref 对证逻辑（load_probes / load_fbref_fixtures /
verdict），不做"含某名=FAIL"的字符串断言（莱切是 Lecce 合法名）。

退出码协议：
  0 = 干净（含 LEGIT / 白名单命中）
  1 = 发现新增污染（CONFIRMED 且未在白名单基线）→ 告警
  2 = 门自身执行错误 → 不应阻断训练，按运维告警处理

模式：
  默认     增量扫描（rowid 水印，新污染只可能来自采集器新写入）
  --full   全库扫描（回扫兜底，同时重置水印）
  --quiet  机器模式：stdout 末行输出 JSON 摘要（编排器消费）

修复纪律：门报出的污染，修复一律走 C-099 同款流程（备份+断言+覆盖率硬门+事务），
本门永不 UPDATE。新污染若属合法新队名歧义 → 补登白名单基线，不改阈值。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from team_name_pollution_audit import (  # noqa: E402
    ARTIFACT_PROBES, MID_RE, load_fbref_fixtures, load_probes, verdict)

ODDS_DB = BASE / "data" / "odds.db"
BASELINE = SCRIPTS / "team_pollution_gate_baseline.json"
STATE = SCRIPTS / "team_pollution_gate_state.json"


def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def run_gate(full: bool = False) -> dict:
    """扫描并返回摘要 dict（不直接决定进程退出码，由 main 翻译）。"""
    probes = load_probes()
    wrong_names = set(probes) - ARTIFACT_PROBES
    baseline = _load_json(BASELINE, {})
    wl_mids = set(baseline.get("match_ids", []))
    wl_triples = set(baseline.get("triples", []))
    state = _load_json(STATE, {})
    marks: dict = {} if full else dict(state.get("watermarks", {}))

    conn = sqlite3.connect(f"file:{ODDS_DB.as_posix()}?mode=ro", uri=True)
    alerts: list[dict] = []
    scanned: dict[str, int] = {}
    try:
        fixtures = load_fbref_fixtures(conn)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        new_marks: dict[str, int] = {}
        for t in tables:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{t}')")]
            team_cols = [c for c in cols if "team" in c.lower()
                         and not c.lower().endswith("_id") and not c.lower().endswith("_side")]
            has_mid = "match_id" in cols
            if not team_cols and not has_mid:
                continue
            try:
                max_rid = conn.execute(f"SELECT MAX(rowid) FROM '{t}'").fetchone()[0] or 0
            except Exception:  # noqa: BLE001
                continue
            new_marks[t] = max_rid
            if not full and t in marks and max_rid <= marks[t]:
                continue
            lo = 0 if full or t not in marks else marks[t]
            date_col = next((c for c in cols if c in ("match_date", "date")), None)
            home_col = next((c for c in team_cols if "home" in c.lower()), None)
            away_col = next((c for c in team_cols if "away" in c.lower()), None)
            single_col = next((c for c in team_cols if c in ("team", "team_name")), None)
            n = 0
            try:
                for row in conn.execute(
                        f"SELECT rowid, * FROM '{t}' WHERE rowid > {int(lo)}"):
                    n += 1
                    ci = {c: i + 1 for i, c in enumerate(cols)}
                    rid = row[0]
                    if has_mid:
                        mid = row[ci["match_id"]]
                        if mid:
                            m = MID_RE.match(str(mid))
                            if m:
                                d, h, a = m.groups()
                                for side, val, opp in (("home", h, a), ("away", a, h)):
                                    if val in wrong_names and str(mid) not in wl_mids:
                                        v = verdict(fixtures, d, val, probes[val], opp, probes)
                                        if v == "CONFIRMED":
                                            alerts.append({
                                                "table": t, "rowid": rid,
                                                "match_id": str(mid), "wrong": val,
                                                "true": sorted(probes[val])[0]})
                    if home_col and away_col:
                        d = row[ci[date_col]] if date_col else None
                        for col, opp_col in ((home_col, away_col), (away_col, home_col)):
                            val = row[ci[col]]
                            if val in wrong_names:
                                key = f"{val}|{str(d)[:10] if d else ''}|{row[ci[opp_col]]}"
                                if key in wl_triples:
                                    continue
                                v = verdict(fixtures, d, val, probes[val],
                                            row[ci[opp_col]], probes)
                                if v == "CONFIRMED":
                                    rec = {"table": t, "rowid": rid, "col": col,
                                           "wrong": val, "true": sorted(probes[val])[0],
                                           "date": str(d)[:10] if d else None}
                                    if has_mid:
                                        rec["match_id"] = str(row[ci["match_id"]])
                                    alerts.append(rec)
                    elif single_col:
                        val = row[ci[single_col]]
                        if val in wrong_names:
                            d = row[ci[date_col]] if date_col else None
                            key = f"{val}|{str(d)[:10] if d else ''}|"
                            if key in wl_triples:
                                continue
                            v = verdict(fixtures, d, val, probes[val], None, probes)
                            if v == "CONFIRMED":
                                alerts.append({"table": t, "rowid": rid, "col": single_col,
                                               "wrong": val, "true": sorted(probes[val])[0],
                                               "date": str(d)[:10] if d else None})
            except sqlite3.Error:
                continue
            scanned[t] = n
    finally:
        conn.close()

    if not full:
        state["watermarks"] = new_marks
    else:
        state["watermarks"] = new_marks
    state["last_scan"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["last_mode"] = "full" if full else "incremental"
    state["last_exit"] = 1 if alerts else 0
    try:
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return {"mode": "full" if full else "incremental",
            "new_confirmed": len(alerts), "alerts": alerts,
            "scanned_tables": scanned, "ts": state["last_scan"]}


def main() -> int:
    full = "--full" in sys.argv
    quiet = "--quiet" in sys.argv
    try:
        r = run_gate(full=full)
    except Exception as e:  # noqa: BLE001
        if quiet:
            print(json.dumps({"exit": 2, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False))
        else:
            print(f"[tripwire] 门自身错误: {type(e).__name__}: {e}")
        return 2
    if quiet:
        print(json.dumps({"exit": 1 if r["new_confirmed"] else 0,
                          "mode": r["mode"], "new_confirmed": r["new_confirmed"],
                          "alerts": r["alerts"], "ts": r["ts"]},
                         ensure_ascii=False))
        return 1 if r["new_confirmed"] else 0
    print(f"[tripwire] 模式={r['mode']} 时间={r['ts']} 扫描表={len(r['scanned_tables'])}")
    print(f"[tripwire] 新增污染（CONFIRMED，未在白名单）: {r['new_confirmed']}")
    for a in r["alerts"][:20]:
        print(f"  🔴 {a}")
    if not r["new_confirmed"]:
        print("  ✅ 干净（LEGIT/白名单/UNVERIFIABLE 均不告警）")
    print("修复纪律: 走 C-099 同款流程（备份+断言+覆盖率硬门+事务），本门永不自动修")
    return 1 if r["new_confirmed"] else 0


if __name__ == "__main__":
    sys.exit(main())
