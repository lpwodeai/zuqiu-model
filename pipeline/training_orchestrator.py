"""
训练管线统一编排器（路线 A - ML 训练管线统一编排）
==================================================

唯一的训练决策与执行入口，收敛 auto_train / scheduled_learning /
retrain_trigger_runner 三套触发器，消除并发、口径漂移、无单一事实源。

核心能力：
  1. should_retrain(trigger) -> {full|incremental|skip}  统一决策表
  2. run_training(trigger) -> run_id                    统一执行入口
  3. get_recent_runs(limit)                              查询（仪表盘用）

状态表：five_leagues.db :: training_runs
去抖规则：同 trigger 类型 24h 内不重复
"""

import os
import sys
import json
import sqlite3
import logging
import subprocess
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

# ------------------------------------------------------------------
# 常量与路径
# ------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DB_PATH = DATA_DIR / "five_leagues.db"
LOG_PATH = PROJECT_ROOT / "logs" / "training_orchestrator.log"

# 去抖窗口
DEDUP_WINDOW_HOURS = 24

# 决策阈值（首版沿用现有各散件保守值）
THRESHOLDS = {
    "scheduled_full_interval_hours": 24 * 7,
    "new_data_full_games": 30,
    "new_data_incremental_games": 10,
    "accuracy_drop_pp": 3.0,
    "ece_delta_pp": 5.0,
}


class TriggerSource(str, Enum):
    SCHEDULED = "scheduled"
    DEGRADED = "degraded"
    NEW_DATA = "new_data"
    MANUAL = "manual"


class TrainingType(str, Enum):
    FULL = "full"
    INCREMENTAL = "incremental"
    BAYESIAN_SHADOW = "bayesian_shadow"


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ------------------------------------------------------------------
# 日志
# ------------------------------------------------------------------
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("training_orchestrator")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")
    _fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    logger.addHandler(_sh)


# ------------------------------------------------------------------
# 数据库
# ------------------------------------------------------------------
def _get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table():
    """确保 training_runs 表存在（幂等）。"""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS training_runs (
                run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_source    TEXT NOT NULL,
                training_type     TEXT NOT NULL,
                feature_version   TEXT,
                model_version     TEXT,
                data_window_start TEXT,
                data_window_end   TEXT,
                val_rps           REAL,
                val_logloss       REAL,
                val_acc           REAL,
                val_draw_recall   REAL,
                started_at        TEXT NOT NULL,
                finished_at       TEXT,
                status            TEXT NOT NULL,
                error_msg         TEXT,
                artifacts_json    TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_training_runs_started
            ON training_runs(started_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_training_runs_status
            ON training_runs(status)
        """)
        conn.commit()
    logger.debug("training_runs 表就绪")


# ------------------------------------------------------------------
# 去抖
# ------------------------------------------------------------------
def _recent_run_within(trigger_source: str, hours: int) -> bool:
    """过去 N 小时内是否已有同 trigger 的成功/运行中训练。"""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT run_id FROM training_runs
            WHERE trigger_source = ?
              AND status IN ('running', 'success')
              AND started_at >= ?
            LIMIT 1
        """, (trigger_source, cutoff)).fetchone()
    return row is not None


# ------------------------------------------------------------------
# 统一决策表
# ------------------------------------------------------------------
def should_retrain(trigger_source: str, context: dict = None) -> dict:
    """
    统一决策函数：判断是否需要重训，以及重训类型。

    Args:
        trigger_source: scheduled / degraded / new_data / manual
        context: 可选上下文，如 {'new_match_count': 30, 'accuracy_drop': 4.2}

    Returns:
        {
            'should': bool,
            'training_type': 'full'|'incremental'|'bayesian_shadow',
            'reason': str,
            'dedup_hit': bool
        }
    """
    ensure_table()
    context = context or {}

    result = {"should": False, "training_type": None,
              "reason": "", "dedup_hit": False}

    # 1. 去抖检查
    if trigger_source != TriggerSource.MANUAL:
        if _recent_run_within(trigger_source, DEDUP_WINDOW_HOURS):
            result["dedup_hit"] = True
            result["reason"] = (f"{trigger_source} 在 {DEDUP_WINDOW_HOURS}h "
                                f"内已有运行，去抖跳过")
            logger.info(result["reason"])
            return result

    # 2. 按 trigger 走决策表
    if trigger_source == TriggerSource.SCHEDULED:
        with _get_conn() as conn:
            last_full = conn.execute("""
                SELECT started_at FROM training_runs
                WHERE training_type = 'full' AND status = 'success'
                ORDER BY started_at DESC LIMIT 1
            """).fetchone()
        if last_full is None:
            result.update(should=True, training_type=TrainingType.FULL,
                          reason="首次全量训练")
        else:
            last_dt = datetime.fromisoformat(last_full["started_at"])
            hours_since = (datetime.now() - last_dt).total_seconds() / 3600
            if hours_since >= THRESHOLDS["scheduled_full_interval_hours"]:
                result.update(should=True, training_type=TrainingType.FULL,
                              reason=f"距上次全量 {hours_since:.1f}h ≥ 阈值")
            else:
                result["reason"] = (f"距上次全量 {hours_since:.1f}h < 阈值")

    elif trigger_source == TriggerSource.DEGRADED:
        acc_drop = context.get("accuracy_drop", 0)
        ece_delta = context.get("ece_delta", 0)
        if (acc_drop >= THRESHOLDS["accuracy_drop_pp"] or
                ece_delta >= THRESHOLDS["ece_delta_pp"]):
            result.update(should=True, training_type=TrainingType.FULL,
                          reason=(f"性能衰退：acc↓{acc_drop:.1f}pp / "
                                  f"ECEΔ{ece_delta:.1f}pp"))
        else:
            result["reason"] = (f"衰退未达阈值：acc↓{acc_drop:.1f}pp / "
                                f"ECEΔ{ece_delta:.1f}pp")

    elif trigger_source == TriggerSource.NEW_DATA:
        new_count = context.get("new_match_count", 0)
        if new_count >= THRESHOLDS["new_data_full_games"]:
            result.update(should=True, training_type=TrainingType.FULL,
                          reason=(f"新增 {new_count} 场 ≥ "
                                  f"{THRESHOLDS['new_data_full_games']} 场"))
        elif new_count >= THRESHOLDS["new_data_incremental_games"]:
            result.update(should=True, training_type=TrainingType.INCREMENTAL,
                          reason=(f"新增 {new_count} 场 ≥ "
                                  f"{THRESHOLDS['new_data_incremental_games']} "
                                  f"场（增量）"))
        else:
            result["reason"] = f"新增 {new_count} 场 < 阈值"

    elif trigger_source == TriggerSource.MANUAL:
        ttype = context.get("training_type", TrainingType.FULL)
        result.update(should=True, training_type=ttype,
                      reason=f"人工触发 ({ttype})")

    else:
        result["reason"] = f"未知 trigger: {trigger_source}"
        logger.error(result["reason"])

    if result["should"]:
        # 确保是 string，方便 json 序列化
        if hasattr(result["training_type"], "value"):
            result["training_type"] = result["training_type"].value
        logger.info(f"决策 → {result['training_type']} | {result['reason']}")
    else:
        logger.info(f"决策 → skip | {result['reason']}")

    return result


# ------------------------------------------------------------------
# 统一执行入口
# ------------------------------------------------------------------
def _start_run(trigger_source, training_type) -> int:
    started_at = datetime.now().isoformat()
    with _get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO training_runs
            (trigger_source, training_type, started_at, status)
            VALUES (?, ?, ?, 'running')
        """, (trigger_source, training_type, started_at))
        conn.commit()
        run_id = cur.lastrowid
    logger.info(f"[{run_id}] 开始训练 trigger={trigger_source} "
                f"type={training_type}")
    return run_id


def _finish_run(run_id: int, status: str, metrics: dict = None,
                error_msg: str = None, artifacts: dict = None):
    finished_at = datetime.now().isoformat()
    metrics = metrics or {}
    artifacts = artifacts or {}
    with _get_conn() as conn:
        conn.execute("""
            UPDATE training_runs
               SET status = ?,
                   finished_at = ?,
                   val_rps = ?,
                   val_logloss = ?,
                   val_acc = ?,
                   val_draw_recall = ?,
                   error_msg = ?,
                   artifacts_json = ?
             WHERE run_id = ?
        """, (
            status, finished_at,
            metrics.get("rps"), metrics.get("logloss"),
            metrics.get("acc"), metrics.get("draw_recall"),
            error_msg,
            json.dumps(artifacts, ensure_ascii=False) if artifacts else None,
            run_id,
        ))
        conn.commit()
    logger.info(f"[{run_id}] 完成 status={status}" +
                (f" err={error_msg}" if error_msg else ""))


def run_training(trigger_source: str, context: dict = None,
                 force: bool = False) -> dict:
    """
    统一训练执行入口：决策 → 执行 → 写状态。

    Args:
        trigger_source: scheduled / degraded / new_data / manual
        context: 决策上下文 + 执行参数
        force: True 时跳过决策直接执行（用 manual 类型）

    Returns:
        {'run_id': int|None, 'decision': dict, 'status': str}
    """
    ensure_table()
    context = context or {}

    if force:
        decision = {"should": True,
                    "training_type": context.get("training_type",
                                                 TrainingType.FULL),
                    "reason": "force=True", "dedup_hit": False}
    else:
        decision = should_retrain(trigger_source, context)

    if not decision["should"]:
        return {"run_id": None, "decision": decision,
                "status": RunStatus.SKIPPED}

    training_type = decision["training_type"]
    run_id = _start_run(trigger_source, training_type)

    try:
        metrics, artifacts = _dispatch_training(training_type, context)
        _finish_run(run_id, RunStatus.SUCCESS, metrics=metrics,
                    artifacts=artifacts)
        return {"run_id": run_id, "decision": decision,
                "status": RunStatus.SUCCESS}
    except Exception as e:
        import traceback
        _finish_run(run_id, RunStatus.FAILED, error_msg=str(e))
        logger.error(f"[{run_id}] 训练失败: {e}")
        logger.error(traceback.format_exc())
        return {"run_id": run_id, "decision": decision,
                "status": RunStatus.FAILED, "error": str(e)}


def _dispatch_training(training_type: str, context: dict):
    """分发到具体训练脚本，返回 (metrics_dict, artifacts_dict)。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    if training_type == TrainingType.FULL:
        script = str(SCRIPTS_DIR / "train_models.py")
        logger.info(f"调用全量训练脚本: {script}")
        proc = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT),
            timeout=60 * 120,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"全量训练失败 (rc={proc.returncode}): "
                f"{proc.stderr[-500:]}")
        metrics = _parse_train_output(proc.stdout)
        artifacts = {"script": "train_models.py",
                     "stdout_tail": proc.stdout[-200:]}
        return metrics, artifacts

    elif training_type == TrainingType.INCREMENTAL:
        script = str(SCRIPTS_DIR / "online_learning_manager.py")
        logger.info(f"调用增量训练: {script}")
        proc = subprocess.run(
            [sys.executable, script, "--incremental"],
            capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT),
            timeout=60 * 30,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"增量训练失败 (rc={proc.returncode}): "
                f"{proc.stderr[-500:]}")
        return {}, {"script": "online_learning_manager.py"}

    elif training_type == TrainingType.BAYESIAN_SHADOW:
        script = str(SCRIPTS_DIR / "run_shadow_incremental.py")
        logger.info(f"调用贝叶斯 shadow 滚动: {script}")
        proc = subprocess.run(
            [sys.executable, script, "--roll"],
            capture_output=True, text=True, env=env, cwd=str(PROJECT_ROOT),
            timeout=60 * 60,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"贝叶斯 shadow 失败 (rc={proc.returncode}): "
                f"{proc.stderr[-500:]}")
        return {}, {"script": "run_shadow_incremental.py"}

    else:
        raise ValueError(f"未知训练类型: {training_type}")


def _parse_train_output(stdout: str) -> dict:
    """从训练输出中粗粒度提取验证指标。解析不到返回空。"""
    import re
    metrics = {}
    patterns = {
        "rps": r"RPS[=:\s]+([\d.]+)",
        "logloss": r"(?:logloss|log_loss|LogLoss)[=:\s]+([\d.]+)",
        "acc": r"(?:accuracy|acc|准确率)[=:\s]+([\d.]+)",
        "draw_recall": r"(?:draw_recall|平局召回)[=:\s]+([\d.]+)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, stdout, re.IGNORECASE)
        if m:
            try:
                metrics[key] = float(m.group(1))
            except ValueError:
                pass
    return metrics


# ------------------------------------------------------------------
# 查询（仪表盘 / 诊断）
# ------------------------------------------------------------------
def get_recent_runs(limit: int = 20) -> list:
    """返回最近 N 条训练运行记录（dict 列表）。"""
    ensure_table()
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM training_runs
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_run(run_id: int) -> dict:
    """按 run_id 查询单条记录。"""
    ensure_table()
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM training_runs WHERE run_id = ?
        """, (run_id,)).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="训练管线统一编排器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="检查是否需要重训")
    p_check.add_argument("--trigger", default="scheduled",
                         choices=[t.value for t in TriggerSource])
    p_check.add_argument("--new-matches", type=int, default=0)
    p_check.add_argument("--acc-drop", type=float, default=0.0)
    p_check.add_argument("--ece-delta", type=float, default=0.0)

    p_run = sub.add_parser("run", help="执行训练（走决策）")
    p_run.add_argument("--trigger", default="scheduled",
                       choices=[t.value for t in TriggerSource])
    p_run.add_argument("--new-matches", type=int, default=0)
    p_run.add_argument("--acc-drop", type=float, default=0.0)
    p_run.add_argument("--ece-delta", type=float, default=0.0)

    p_force = sub.add_parser("force", help="强制执行（跳过决策）")
    p_force.add_argument("--type", default="full",
                         choices=["full", "incremental", "bayesian_shadow"])

    p_list = sub.add_parser("list", help="列出最近的训练运行")
    p_list.add_argument("--limit", type=int, default=10)

    p_show = sub.add_parser("show", help="查看某次运行详情")
    p_show.add_argument("run_id", type=int)

    p_daily = sub.add_parser("daily-check",
                             help="每日综合检查（资源+数据+决策）")
    p_daily.add_argument("--skip-resource-check", action="store_true",
                         help="跳过资源检查")
    p_daily.add_argument("--execute", action="store_true",
                         help="真的执行训练（默认 dry-run 只检查）")

    p_init = sub.add_parser("init-table", help="建表（幂等）")

    args = parser.parse_args()
    ensure_table()

    if args.cmd == "init-table":
        print("training_runs 表已就绪")

    elif args.cmd == "daily-check":
        res = run_daily_check(
            skip_resource_check=args.skip_resource_check,
            dry_run=not args.execute)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))

    elif args.cmd == "check":
        ctx = {"new_match_count": args.new_matches,
               "accuracy_drop": args.acc_drop,
               "ece_delta": args.ece_delta}
        res = should_retrain(args.trigger, ctx)
        print(json.dumps(res, ensure_ascii=False, indent=2))

    elif args.cmd == "run":
        ctx = {"new_match_count": args.new_matches,
               "accuracy_drop": args.acc_drop,
               "ece_delta": args.ece_delta}
        res = run_training(args.trigger, ctx)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))

    elif args.cmd == "force":
        ctx = {"training_type": args.type}
        res = run_training("manual", ctx, force=True)
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))

    elif args.cmd == "list":
        runs = get_recent_runs(args.limit)
        if not runs:
            print("(无记录)")
            return
        print(f"{'run_id':>6} {'trigger':<10} {'type':<18} "
              f"{'status':<8} {'started_at':<20} rps     acc")
        print("-" * 90)
        for r in runs:
            print(f"{r['run_id']:>6} {r['trigger_source']:<10} "
                  f"{r['training_type']:<18} {r['status']:<8} "
                  f"{str(r['started_at'])[:19]:<20} "
                  f"{(r['val_rps'] or '-'):>6}  "
                  f"{(r['val_acc'] or '-'):>5}")

    elif args.cmd == "show":
        r = get_run(args.run_id)
        if not r:
            print(f"run_id={args.run_id} 不存在")
            return
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


# ------------------------------------------------------------------
# 数据检查（new_data 触发的数据源）
# ------------------------------------------------------------------
def get_new_match_count_since(since_iso: str = None) -> dict:
    """
    查询 five_leagues.db 中指定时间之后的新完赛场数。

    Args:
        since_iso: ISO 格式时间字符串，None 时用上次全量成功时间

    Returns:
        {'new_matches': int, 'total_matches': int, 'latest_date': str}
    """
    ensure_table()
    # 没指定的话，用上次全量成功的时间做起点
    if since_iso is None:
        with _get_conn() as conn:
            last = conn.execute("""
                SELECT started_at FROM training_runs
                WHERE training_type = 'full' AND status = 'success'
                ORDER BY started_at DESC LIMIT 1
            """).fetchone()
        if last is None:
            since_date = "2000-01-01"
        else:
            since_date = last["started_at"][:10]
    else:
        since_date = since_iso[:10]

    # 读 matches 表
    try:
        conn = sqlite3.connect(str(DATA_DIR / "five_leagues.db"))
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) FROM matches
            WHERE date >= ? AND homeGoals IS NOT NULL AND awayGoals IS NOT NULL
        """, (since_date,))
        new_count = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM matches
            WHERE homeGoals IS NOT NULL AND awayGoals IS NOT NULL
        """)
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT MAX(date) FROM matches
            WHERE homeGoals IS NOT NULL AND awayGoals IS NOT NULL
        """)
        latest = cur.fetchone()[0]
        conn.close()

        return {"new_matches": new_count, "total_matches": total,
                "latest_date": latest}
    except Exception as e:
        logger.warning(f"读取 matches 表失败: {e}")
        return {"new_matches": 0, "total_matches": 0, "latest_date": None}


# ------------------------------------------------------------------
# 资源门禁（低资源时段跳过训练，避免影响生产）
# ------------------------------------------------------------------
def check_resource_availability() -> dict:
    """
    检查系统资源是否足够启动训练。

    Returns:
        {'ok': bool, 'cpu_pct': float, 'mem_pct': float,
         'disk_pct': float, 'reason': str}
    """
    try:
        import psutil
    except ImportError:
        return {"ok": True, "reason": "psutil 未安装，跳过资源检查"}

    max_cpu = 80
    max_mem = 85
    max_disk = 90

    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage(str(PROJECT_ROOT)).percent

    ok = cpu <= max_cpu and mem <= max_mem and disk <= max_disk
    reason = ("资源正常" if ok else
              f"CPU={cpu}% MEM={mem}% DISK={disk}%")

    if not ok:
        logger.warning(f"资源不足，跳过训练: {reason}")

    return {"ok": ok, "cpu_pct": cpu, "mem_pct": mem,
            "disk_pct": disk, "reason": reason}


# ------------------------------------------------------------------
# 每日综合检查（替代 scheduled_learning.run_daily_check 的入口）
# ------------------------------------------------------------------
def run_daily_check(skip_resource_check: bool = False,
                    dry_run: bool = True) -> dict:
    """
    每日综合检查入口：资源 → 新数据 → 衰退 → 综合决策 → 执行。

    覆盖三个散件的核心调度逻辑：
      - auto_train: 资源检查 + 新比赛数
      - scheduled_learning: 每日检查 + 周训
      - retrain_trigger: 三重触发（周期/数据/性能门禁）

    Args:
        skip_resource_check: 跳过资源门禁（默认不跳）
        dry_run: True=只检查不执行（默认！安全第一），False=真跑

    Returns:
        {'resource': dict, 'data': dict, 'decision': dict,
         'trigger_source': str|None, 'training_result': dict|None,
         'dry_run': bool}
    """
    logger.info("=" * 60)
    logger.info(f"每日综合检查（dry_run={dry_run}）")
    logger.info("=" * 60)

    # 1. 资源检查
    if not skip_resource_check:
        res = check_resource_availability()
        if not res["ok"]:
            logger.info(f"资源不足跳过: {res['reason']}")
            return {"resource": res, "data": None,
                    "decision": {"should": False, "reason": "资源不足"},
                    "training_result": None}
    else:
        res = {"ok": True, "reason": "已跳过"}

    # 2. 新数据检测
    data = get_new_match_count_since()
    logger.info(f"数据检查: 新增 {data['new_matches']} 场，"
                f"总计 {data['total_matches']} 场，"
                f"最新 {data['latest_date']}")

    # 3. 周期触发（scheduled）
    sched_result = should_retrain("scheduled")
    if sched_result["should"]:
        # 周期触发优先级最高（定期重训）
        decision = sched_result
        trigger = "scheduled"
    elif data["new_matches"] >= THRESHOLDS["new_data_incremental_games"]:
        # 有新数据，走 new_data 决策
        decision = should_retrain(
            "new_data", {"new_match_count": data["new_matches"]})
        trigger = "new_data"
    else:
        decision = {"should": False, "training_type": None,
                    "reason": "无周期触发且新增数据不足", "dedup_hit": False}
        trigger = None

    # 4. 执行（dry_run=True 时只检查不跑训练）
    if decision["should"]:
        if dry_run:
            logger.info(f"[DRY-RUN] 综合决策 → 将触发训练: "
                        f"{decision['reason']}（未实际执行）")
            result = {"status": "dry_run",
                      "would_trigger": True,
                      "would_type": decision["training_type"]}
        else:
            logger.info(f"综合决策 → 触发训练: {decision['reason']}")
            ctx = {"new_match_count": data["new_matches"]}
            result = run_training(trigger, ctx)
    else:
        logger.info(f"综合决策 → 跳过: {decision['reason']}")
        result = None

    return {
        "resource": res,
        "data": data,
        "decision": decision,
        "trigger_source": trigger,
        "training_result": result,
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    main()
