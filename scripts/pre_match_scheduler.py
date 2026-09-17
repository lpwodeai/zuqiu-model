"""
P1-16 赛前双阶段报告调度器
阶段1(preliminary): 赛前 12h 生成预报告
阶段2(final):       赛前  2h 先拉最新赔率/伤情，再重跑最终报告

用法:
  python scripts/pre_match_scheduler.py --daemon
  python scripts/pre_match_scheduler.py --once
  python scripts/pre_match_scheduler.py --refresh-match MATCH_FID
"""

import argparse
import os
import sys
import time
import sqlite3
import subprocess
import logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds.db")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PHASE1_HOURS = 12
PHASE2_HOURS = 2
WINDOW_HOURS = 6
LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("scheduler")


def ensure_phase_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS match_phase_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_fid TEXT NOT NULL,
            phase TEXT NOT NULL,
            triggered_at TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            UNIQUE(match_fid, phase)
        )
    """)
    conn.commit()


def phase_done(conn, fid, phase):
    return conn.execute(
        "SELECT 1 FROM match_phase_log WHERE match_fid=? AND phase=? AND status='OK'",
        (fid, phase),
    ).fetchone() is not None


def mark_phase(conn, fid, phase, status, detail=None):
    conn.execute("""
        INSERT OR REPLACE INTO match_phase_log
        (match_fid, phase, triggered_at, status, detail)
        VALUES (?, ?, ?, ?, ?)
    """, (fid, phase, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status, detail))
    conn.commit()


def discover_upcoming(conn, hours_ahead=36):
    now = datetime.now()
    end = now + timedelta(hours=hours_ahead)
    start_date = now.strftime("%Y-%m-%d")
    end_date = end.strftime("%Y-%m-%d")
    ph = ",".join("?" * len(LEAGUES))
    rows = conn.execute(f"""
        SELECT fid, league, match_date, match_time, home_team_cn, away_team_cn
        FROM odds500_match
        WHERE status = 1 AND match_date >= ? AND match_date <= ? AND league IN ({ph})
        ORDER BY match_date, match_time
    """, [start_date, end_date] + LEAGUES).fetchall()

    results = []
    for r in rows:
        try:
            kickoff = datetime.strptime(f"{r['match_date']} {r['match_time']}", "%Y-%m-%d %H:%M")
        except Exception:
            continue
        hours_to = (kickoff - now).total_seconds() / 3600
        if -1 <= hours_to <= hours_ahead:
            d = dict(r)
            d["kickoff"] = kickoff
            d["hours_to_kickoff"] = hours_to
            results.append(d)
    return results


def run_realtime_collector():
    log.info("触发 realtime_data_collector --force ...")
    try:
        result = subprocess.run(
            [sys.executable, "scripts/realtime_data_collector.py", "--force"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=600,
        )
        log.info(f"  完成, code={result.returncode}")
        return result.returncode == 0
    except Exception as e:
        log.error(f"  失败: {e}")
        return False


def run_report_generator(target_date, league=None):
    cmd = [sys.executable, "scripts/generate_unified_report.py", "--date", target_date, "--days", "1"]
    if league:
        cmd += ["--league", league]
    log.info(f"生成报告: date={target_date} league={league or 'all'}")
    try:
        result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "")[-300:]
            log.warning(f"  报告返回码 {result.returncode}, 尾部: {tail}")
        return result.returncode == 0
    except Exception as e:
        log.error(f"  失败: {e}")
        return False


def scan_and_trigger(conn):
    matches = discover_upcoming(conn, hours_ahead=36)
    log.info(f"扫描到未来36h内 {len(matches)} 场比赛")

    p1_matches = [m for m in matches if PHASE1_HOURS - WINDOW_HOURS <= m["hours_to_kickoff"] <= PHASE1_HOURS]
    p1_pending = [m for m in p1_matches if not phase_done(conn, m["fid"], "preliminary")]

    p2_matches = [m for m in matches if PHASE2_HOURS - WINDOW_HOURS <= m["hours_to_kickoff"] <= PHASE2_HOURS]
    p2_pending = [m for m in p2_matches if not phase_done(conn, m["fid"], "final")]

    log.info(f"  阶段1(预报告) 窗口={len(p1_matches)}, 待触发={len(p1_pending)}")
    log.info(f"  阶段2(最终报告) 窗口={len(p2_matches)}, 待触发={len(p2_pending)}")

    # 阶段 1
    if p1_pending:
        _generate_by_date_league(conn, p1_pending, "preliminary", run_realtime=False)

    # 阶段 2
    if p2_pending:
        run_realtime_collector()
        _generate_by_date_league(conn, p2_pending, "final", run_realtime=True)

    log.info("本轮扫描完成。")


def _generate_by_date_league(conn, match_list, phase, run_realtime=False):
    dates = sorted(set(m["match_date"] for m in match_list))
    for d in dates:
        leagues_in_day = sorted(set(m["league"] for m in match_list if m["match_date"] == d))
        for lg in leagues_in_day:
            ok = run_report_generator(d, league=lg)
            for m in match_list:
                if m["match_date"] == d and m["league"] == lg:
                    mark_phase(conn, m["fid"], phase,
                               "OK" if ok else "FAIL",
                               f"league={lg} date={d}")
            time.sleep(2)


def run_once():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_phase_table(conn)
    scan_and_trigger(conn)
    conn.close()


def run_daemon(interval_min=15):
    log.info(f"双阶段调度器启动，每 {interval_min} 分钟扫描一次，Ctrl+C 退出")
    while True:
        try:
            run_once()
        except Exception as e:
            log.error(f"扫描异常: {e}")
            import traceback
            traceback.print_exc()
        log.info(f"休眠 {interval_min} 分钟...")
        time.sleep(interval_min * 60)


def refresh_match(fid):
    log.info(f"手动刷新 fid={fid} 的阶段2报告")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_phase_table(conn)
    m = conn.execute("SELECT fid, league, match_date FROM odds500_match WHERE fid=?", (fid,)).fetchone()
    if not m:
        log.error(f"未找到比赛 fid={fid}")
        conn.close()
        return
    run_realtime_collector()
    ok = run_report_generator(m["match_date"], league=m["league"])
    mark_phase(conn, fid, "final", "OK" if ok else "FAIL", "manual_refresh")
    conn.close()
    log.info(f"完成，结果={'OK' if ok else 'FAIL'}")


def main():
    parser = argparse.ArgumentParser(description="赛前双阶段报告调度器 (P1-16)")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--refresh-match", type=str, default=None)
    parser.add_argument("--interval", type=int, default=15, help="daemon 间隔分钟")
    args = parser.parse_args()

    if args.refresh_match:
        refresh_match(args.refresh_match)
    elif args.daemon:
        run_daemon(args.interval)
    elif args.once:
        run_once()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

