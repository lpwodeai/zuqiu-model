# -*- coding: utf-8 -*-
"""P2-11 预案 L2: SQLite 健康体检（C-20260929-017）

信号 → 红黄绿（阈值含实测依据，详见框架文档 §3.4 末预案段）:
  库体积:  >4GB 黄 / >8GB 红   （当前 1.5GB 基线，SQLite 舒适区约 5-10GB）
  locked:  近 7 天 ≥1 黄 / ≥10 红（读 data/sqlite_health.json，由 db_utils.py L1 钩子累计）
  WAL:     >256MB 黄           （checkpoint 异常信号）
  行数:    仅展示核心表 top5，不设阈值

退出码: 0=绿 1=黄 2=红；--json 追加输出原始 JSON（人类摘要始终先输出）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"
HEALTH_JSON = PROJECT_ROOT / "data" / "sqlite_health.json"

WARN_DB_GB, CRIT_DB_GB = 4, 8
WARN_LOCKED_7D, CRIT_LOCKED_7D = 1, 10
WARN_WAL_MB = 256
# 核心表白名单（全量 33 表逐表 COUNT 在 1.5GB 库上过慢，白名单覆盖主要体积来源；
# odds500% 前缀表动态展开）
CORE_TABLES = ["matches", "wdl_history", "score_history", "match_player_stats", "match_lineups"]


def _gb(n: float) -> float:
    return n / 1024 ** 3


def _mb(n: float) -> float:
    return n / 1024 ** 2


def main() -> int:
    import sqlite3

    checks: list[dict] = []
    summary: list[str] = []

    # 1) 库体积 + WAL/SHM
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    wal = DB_PATH.with_name(DB_PATH.name + "-wal")
    shm = DB_PATH.with_name(DB_PATH.name + "-shm")
    wal_size = wal.stat().st_size if wal.exists() else 0
    gb = _gb(db_size)
    level = "red" if gb > CRIT_DB_GB else ("yellow" if gb > WARN_DB_GB else "green")
    checks.append({"item": "db_size_gb", "value": round(gb, 2), "level": level,
                   "threshold": f">{WARN_DB_GB}GB 黄 / >{CRIT_DB_GB}GB 红"})
    summary.append(f"库体积 {gb:.2f}GB [{level}]")
    wal_level = "yellow" if _mb(wal_size) > WARN_WAL_MB else "green"
    checks.append({"item": "wal_size_mb", "value": round(_mb(wal_size), 1), "level": wal_level,
                   "threshold": f">{WARN_WAL_MB}MB 黄"})
    summary.append(f"WAL {_mb(wal_size):.1f}MB [{wal_level}]")

    # 2) locked 计数（L1 钩子落盘）
    locked_total, locked_7d = 0, 0
    if HEALTH_JSON.exists():
        try:
            state = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
            locked_total = int(state.get("locked_total", 0))
            cutoff = time.time() - 7 * 86400
            locked_7d = sum(
                1 for e in state.get("locked_events", [])
                if _parse_ts(e.get("ts", "")) is not None and _parse_ts(e["ts"]) >= cutoff
            )
        except Exception:  # noqa: BLE001
            pass
    locked_level = "red" if locked_7d >= CRIT_LOCKED_7D else ("yellow" if locked_7d >= WARN_LOCKED_7D else "green")
    checks.append({"item": "locked_7d", "value": locked_7d, "level": locked_level,
                   "threshold": f"≥{WARN_LOCKED_7D} 黄 / ≥{CRIT_LOCKED_7D} 红"})
    summary.append(f"locked 近7天 {locked_7d} 次/累计 {locked_total} [{locked_level}]")

    # 3) 核心表行数（仅展示）
    top_rows: list[dict] = []
    if db_size > 0:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            names = set(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
            want = [t for t in CORE_TABLES if t in names]
            want += [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'odds500%'")]
            for t in want:
                n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]  # noqa: S608
                top_rows.append({"table": t, "rows": n})
        finally:
            conn.close()
        top_rows.sort(key=lambda x: -x["rows"])
        for r in top_rows[:5]:
            summary.append(f"{r['table']} {r['rows']:,} 行")
    checks.append({"item": "core_table_rows", "value": top_rows[:5], "level": "green"})

    # 4) 队名污染 tripwire（C-102，周频观察钩子：只报告，不修复、不阻断）
    try:
        import subprocess
        gate = Path(__file__).resolve().parent / "team_pollution_gate.py"
        p = subprocess.run([sys.executable, str(gate), "--quiet"],
                           capture_output=True, text=True, timeout=600,
                           cwd=str(DB_PATH.parent.parent))
        line = (p.stdout.strip().splitlines() or ["{}"])[-1]
        g = json.loads(line) if line.startswith("{") else {}
        if p.returncode == 0:
            g_level, g_txt = "green", f"干净（mode={g.get('mode')}）"
        elif p.returncode == 1:
            g_level = "red"
            g_txt = f"新增污染 {g.get('new_confirmed')} 行 → 走 C-099 同款修复流程，本门不自动修"
        else:
            g_level, g_txt = "yellow", f"门自身错误 exit={p.returncode}（不判污染）"
    except Exception as e:  # noqa: BLE001
        g_level, g_txt = "yellow", f"tripwire 调用失败: {type(e).__name__}"
    checks.append({"item": "team_pollution_tripwire", "value": g_txt, "level": g_level})
    summary.append(f"队名污染 tripwire [{g_level}] {g_txt}")

    # 5) 复盘数据覆盖门（C-106，周频观察钩子：只报告，不修复）
    try:
        gate2 = Path(__file__).resolve().parent / "stat_coverage_gate.py"
        p2 = subprocess.run([sys.executable, str(gate2), "--quiet"],
                            capture_output=True, text=True, timeout=120,
                            cwd=str(DB_PATH.parent.parent))
        line2 = (p2.stdout.strip().splitlines() or ["{}"])[-1]
        g2 = json.loads(line2) if line2.startswith("{") else {}
        if p2.returncode == 0 and g2.get("known_gap"):
            # C-20261022-193：已登记的持续性缺口 → 黄级观察（可见但不刷屏），
            # 不判红——防"狼来了"常态化；窗口 C 补采后摘除标签恢复红级告警。
            kg = g2["known_gap"]
            g2_level = "yellow"
            g2_txt = (f"known gap（{kg.get('label', '已登记')}）统计缺口 "
                      f"{g2.get('total_gaps')} 场（{kg.get('register_ref', '?')} 登记）")
        elif p2.returncode == 0:
            g2_level, g2_txt = "green", "30 天窗口零缺口"
        elif p2.returncode == 1:
            g2_level = "red"
            g2_txt = (f"统计缺口 {g2.get('total_gaps')} 场 "
                      f"{g2.get('by_date', [])[:3]} → C-105 runbook 修复")
        else:
            g2_level, g2_txt = "yellow", f"门自身错误 exit={p2.returncode}"
    except Exception as e:  # noqa: BLE001
        g2_level, g2_txt = "yellow", f"覆盖门调用失败: {type(e).__name__}"
    checks.append({"item": "stat_coverage_gate", "value": g2_txt, "level": g2_level})
    summary.append(f"复盘数据覆盖门 [{g2_level}] {g2_txt}")

    overall = "red" if any(c["level"] == "red" for c in checks) else (
        "yellow" if any(c["level"] == "yellow" for c in checks) else "green")

    print(f"[DB 体检] 总评: {overall.upper()}")
    for s in summary:
        print(f"  - {s}")
    if "--json" in sys.argv:
        print(json.dumps({"overall": overall, "checks": checks}, ensure_ascii=False))
    return 0 if overall == "green" else (1 if overall == "yellow" else 2)


def _parse_ts(ts: str):
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    sys.exit(main())
