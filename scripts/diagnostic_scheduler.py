"""
诊断脚本统一调度器
定期执行所有诊断/监控脚本，结果落表，超阈值输出告警。

用法:
  python scripts/diagnostic_scheduler.py --list
  python scripts/diagnostic_scheduler.py --run-all
  python scripts/diagnostic_scheduler.py --run calibration
  python scripts/diagnostic_scheduler.py --daemon --interval 360
  python scripts/diagnostic_scheduler.py --recent 20
"""

import argparse
import os
import sys
import time
import sqlite3
import subprocess
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds.db")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DIAGNOSTICS = {
    "calibration": {
        "script": "scripts/probability_calibration_monitor.py",
        "args": [], "description": "概率校准度监控", "timeout": 300,
    },
    "low_score": {
        "script": "scripts/low_score_diagnosis.py",
        "args": [], "description": "低比分系统性低估诊断", "timeout": 300,
    },
    "dual_track": {
        "script": "scripts/dual_track_backtest.py",
        "args": ["--league", "英超"], "description": "双轨回测 ROI", "timeout": 600,
    },
    "risk": {
        "script": "scripts/risk_monitor.py",
        "args": [], "description": "风险监控", "timeout": 300,
    },
    "concept_drift": {
        "script": "scripts/concept_drift_gate.py",
        "args": [], "description": "概念漂移检测", "timeout": 300,
    },
}

ALERT_KEYWORDS = ["WARNING", "ALERT", "告警", "严重", "超阈值", "FAIL", "ECE>0.05", "漂移>20%"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("diag")


def ensure_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS diagnostic_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_key TEXT NOT NULL,
            run_at TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_sec REAL,
            exit_code INTEGER,
            has_alert INTEGER DEFAULT 0,
            summary TEXT,
            output_tail TEXT
        )
    """)
    conn.commit()


def script_exists(key):
    info = DIAGNOSTICS.get(key)
    if not info:
        return False
    return os.path.exists(os.path.join(PROJECT_ROOT, info["script"]))


def run_diagnostic(key):
    info = DIAGNOSTICS[key]
    script_path = os.path.join(PROJECT_ROOT, info["script"])
    if not os.path.exists(script_path):
        return {"status": "SKIP", "has_alert": False, "summary": "脚本不存在"}

    cmd = [sys.executable, script_path] + info["args"]
    log.info(f"[{key}] 开始: {info['description']}")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=info["timeout"])
        duration = time.time() - t0
        output = (result.stdout or "") + (result.stderr or "")
        status = "OK" if result.returncode == 0 else "FAIL"
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        duration = time.time() - t0
        output = f"TIMEOUT after {info['timeout']}s"
        status = "TIMEOUT"
        exit_code = None
    except Exception as e:
        duration = time.time() - t0
        output = str(e)
        status = "ERROR"
        exit_code = None

    has_alert = any(kw in output for kw in ALERT_KEYWORDS)
    if len(output) > 400:
        summary = output[:200] + "\n...\n" + output[-200:]
    else:
        summary = output

    if has_alert:
        log.warning(f"[{key}] 检测到告警! 状态={status}, 用时={duration:.1f}s")
    else:
        log.info(f"[{key}] 完成, 状态={status}, 用时={duration:.1f}s")

    return {
        "status": status, "duration": round(duration, 2),
        "exit_code": exit_code, "has_alert": has_alert,
        "summary": summary[:500], "output_tail": output[-1000:],
    }


def run_and_log(conn, key):
    ensure_table(conn)
    result = run_diagnostic(key)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO diagnostic_runs
        (script_key, run_at, status, duration_sec, exit_code, has_alert, summary, output_tail)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        key, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        result["status"], result.get("duration"), result.get("exit_code"),
        1 if result.get("has_alert") else 0,
        result.get("summary", ""), result.get("output_tail", ""),
    ))
    conn.commit()
    return result


def run_all(conn):
    log.info(f"=== 开始全部诊断 ({len(DIAGNOSTICS)} 个) ===")
    summary = {}
    for key in DIAGNOSTICS:
        if not script_exists(key):
            log.info(f"[{key}] 脚本不存在，跳过")
            summary[key] = "SKIP"
            continue
        r = run_and_log(conn, key)
        summary[key] = r["status"] + (" [ALERT]" if r.get("has_alert") else "")
        time.sleep(2)
    log.info("=== 全部诊断完成 ===")
    for k, v in summary.items():
        log.info(f"  {k:20s} -> {v}")
    return summary


def run_daemon(conn, interval_min=360):
    log.info(f"诊断调度器启动，每 {interval_min} 分钟一轮，Ctrl+C 退出")
    while True:
        try:
            run_all(conn)
        except Exception as e:
            log.error(f"执行异常: {e}")
            import traceback
            traceback.print_exc()
        log.info(f"休眠 {interval_min} 分钟...")
        time.sleep(interval_min * 60)


def show_recent(conn, n=10):
    rows = conn.execute(
        "SELECT script_key, run_at, status, duration_sec, has_alert FROM diagnostic_runs "
        "ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    print(f"最近 {len(rows)} 次运行:")
    print(f"  {'脚本':20s} {'时间':20s} {'状态':10s} {'耗时':>8s} {'告警':>6s}")
    print("  " + "-" * 70)
    for r in rows:
        print(f"  {r['script_key']:20s} {r['run_at']:20s} {r['status']:10s} "
              f"{r['duration_sec'] or 0:7.1f}s {'YES' if r['has_alert'] else '-':>6s}")


def main():
    parser = argparse.ArgumentParser(description="诊断脚本统一调度器")
    parser.add_argument("--list", action="store_true", help="列出所有诊断脚本")
    parser.add_argument("--run-all", action="store_true", help="跑全部诊断")
    parser.add_argument("--run", type=str, default=None, help="跑单个诊断")
    parser.add_argument("--daemon", action="store_true", help="常驻模式")
    parser.add_argument("--interval", type=int, default=360, help="daemon 间隔分钟（默认 360=6h）")
    parser.add_argument("--recent", type=int, default=None, help="查看最近 N 次运行")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_table(conn)

    if args.list:
        print("可用诊断脚本:")
        for k, v in DIAGNOSTICS.items():
            exists = "OK" if script_exists(k) else "MISSING"
            print(f"  {k:20s} [{exists}] {v['description']}")
    elif args.recent is not None:
        show_recent(conn, args.recent)
    elif args.run_all:
        run_all(conn)
    elif args.run:
        if args.run not in DIAGNOSTICS:
            print(f"未知脚本: {args.run}")
            print(f"可用: {', '.join(DIAGNOSTICS.keys())}")
        else:
            run_and_log(conn, args.run)
    elif args.daemon:
        run_daemon(conn, args.interval)
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()

