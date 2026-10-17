# -*- coding: utf-8 -*-
"""复盘数据覆盖门（C-106）：500.com 赛后技术统计断供 tripwire。

背景（C-105）：EdgeOne 反爬 + Cookie 失效致 odds500_stat 断供两周无人察觉
（采集器本体正常，仅 stat 子页被拦）。本门 = 覆盖缺口检测 + Cookie 新鲜度探针，
只读、永不自动修（修复走 C-105 runbook：人工刷 Cookie → --pages stat 回补 → 重生成复盘）。

退出码协议（与 team_pollution_gate 同款）：
  0 = 干净 / 1 = 告警（覆盖缺口或探针被拦）/ 2 = 门自身错误

防误报：只看 30 天窗口；完赛 <48h 不算缺口（统计页赛后数小时才出）；
基线（2026-10-15 回补后）= 0 缺口，故任何缺口出现即有意义。

模式：
  默认      覆盖缺口检测（纯库内，不联网）
  --probe   Cookie/反爬 canary：抓一页最新完赛场次的 shuju 页，命中 EdgeOne 拦截签名 → exit 1
  --quiet   机器模式：末行输出 JSON 摘要
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ODDS_DB = BASE / "data" / "odds.db"
COOKIE_FILE = BASE / "data" / "cookies_500.json"

WINDOW_DAYS = 30
GRACE_DAYS = 2          # 完赛 48h 内不算缺口
PROBE_SIGS = ("EdgeOut", "刷新 Cookie", "刷新Cookie", "请开启JavaScript", "验证码")


def _ro_conn():
    return sqlite3.connect(f"file:{ODDS_DB.as_posix()}?mode=ro", uri=True)


def count_gaps(conn: sqlite3.Connection):
    """返回 (gaps_by_date, total)。gaps_by_date: [(date_str, n), ...] 升序。"""
    hi = (date.today() - timedelta(days=GRACE_DAYS)).isoformat()
    lo = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT substr(m.match_id,1,10) d, COUNT(*) FROM odds500_match m "
        "WHERE m.status='5' AND substr(m.match_id,1,10) BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM odds500_stat s WHERE s.match_id=m.match_id) "
        "GROUP BY d ORDER BY d", (lo, hi)).fetchall()
    return rows, sum(n for _, n in rows)


def run_coverage() -> dict:
    conn = _ro_conn()
    try:
        by_date, total = count_gaps(conn)
    finally:
        conn.close()
    return {"mode": "coverage", "window_days": WINDOW_DAYS, "grace_days": GRACE_DAYS,
            "total_gaps": total, "by_date": by_date,
            "alert": total > 0}


def run_probe(timeout: int = 20) -> dict:
    """抓最新完赛场次的 shuju 页，命中 EdgeOne 拦截签名 → blocked。"""
    conn = _ro_conn()
    try:
        row = conn.execute("SELECT fid, match_id FROM odds500_match WHERE status='5' "
                           "ORDER BY match_id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    if not row:
        return {"mode": "probe", "blocked": False, "note": "库内无完赛场次可探测"}
    fid, mid = row
    try:
        import requests
    except ImportError:
        return {"mode": "probe", "blocked": None, "error": "requests 不可用"}
    sess = requests.Session()
    sess.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/120.0.0.0 Safari/537.36",
                        "Referer": "https://odds.500.com/"})
    if COOKIE_FILE.exists():
        try:
            data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for name, value in data.items():
                    if name != "__user_agent":
                        sess.cookies.set(name, value, domain="odds.500.com", path="/")
        except Exception:  # noqa: BLE001
            pass
    url = f"https://odds.500.com/fenxi/shuju-{fid}.shtml"
    try:
        r = sess.get(url, timeout=timeout)
        text = r.text[:20000]
        blocked = any(sig in text for sig in PROBE_SIGS) or r.status_code in (403, 503)
        return {"mode": "probe", "blocked": bool(blocked), "http": r.status_code,
                "fid": fid, "match_id": mid[:40]}
    except Exception as e:  # noqa: BLE001
        return {"mode": "probe", "blocked": None,
                "error": f"{type(e).__name__}: {e}"}


def main() -> int:
    quiet = "--quiet" in sys.argv
    probe = "--probe" in sys.argv
    try:
        r = run_probe() if probe else run_coverage()
    except Exception as e:  # noqa: BLE001
        if quiet:
            print(json.dumps({"exit": 2, "error": f"{type(e).__name__}: {e}"},
                             ensure_ascii=False))
        else:
            print(f"[stat-gate] 门自身错误: {type(e).__name__}: {e}")
        return 2
    alert = bool(r.get("blocked")) or bool(r.get("alert"))
    if quiet:
        print(json.dumps({**r, "exit": 1 if alert else 0}, ensure_ascii=False))
        return 1 if alert else 0
    if r["mode"] == "coverage":
        print(f"[stat-gate] 覆盖检测（窗口 {r['window_days']}d / 宽限 {r['grace_days']}d）: "
              f"缺口 {r['total_gaps']} 场")
        for d, n in r["by_date"][:10]:
            print(f"  🔴 {d}: {n} 场缺统计")
        if not r["total_gaps"]:
            print("  ✅ 30 天窗口零缺口")
        print("修复: 人工刷 Cookie → final_500_collector --pages stat 回补 → 重生成复盘（C-105 runbook）")
    else:
        if r.get("blocked") is True:
            print(f"[stat-gate] 🔴 反爬拦截（HTTP {r.get('http')}）— Cookie 失效/EdgeOne 升级")
        elif r.get("blocked") is False:
            print(f"[stat-gate] ✅ 探针通过（HTTP {r.get('http')}，fid={r.get('fid')}）")
        else:
            print(f"[stat-gate] 🟡 探针未定: {r.get('error') or r.get('note')}")
    return 1 if alert else 0


if __name__ == "__main__":
    sys.exit(main())
