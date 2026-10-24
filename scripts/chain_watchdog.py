# -*- coding: utf-8 -*-
"""
赛前链看门狗 (chain_watchdog.py) — C-20261017-126
====================================================================
独立失败域：不 import/调用任何链业务代码，只读状态文件/DB/报告目录，
判定失败后通过 chain_alert.raise_alert 走本地告警。

计划任务每日 23:55（evening）与 01:10（late）各触发一次；
链日锚点：当前小时 < 12 → 前一日（01:10 巡检前夜 20:30 链），否则当日。

判定矩阵（致命→告警，观察→只入 STATUS.md）：
  no_heartbeat     无心跳无结果（F1 启动即死 / F3 未运行）        fatal
  running_late     evening 仍运行=记录不告；late 仍无完成标记       fatal（仅 late）
  step_fail        致命白名单步骤 exit≠0（F2）                    fatal
  artifact_missing 注册场次>0 但明日报告无 _summary.md / 0 份报告（F4）fatal
  masked_by_regen  20:30 链失败而 23:30 兜底成功（陈旧数据掩盖）    fatal（单列）
  post_match_outage 赛后链连续 4 轮零新复盘+采集失败且 DB 有积压   fatal（C-20261017-132）
  self_data_error  看门狗自身 DB 读取失败（不放行）                fatal + exit 2
  观察步骤失败 / 报告数与注册数偏差>0（两者均>0）                   仅看板
  0a-3 SKIP-* / 注册场次=0 空窗                                   正常态

退出码：0=全绿（可含观察项） 1=有致命告警 2=自身异常
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chain_alert  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_STATUS_DIR = BASE_DIR / "logs" / "chain_status"
DEFAULT_REPORTS_DIR = BASE_DIR / "docs" / "prematch_reports"
DEFAULT_DB = BASE_DIR / "data" / "odds.db"
CHAIN = "prematch_2030"
REGEN = "report_regen_2330"
OBSERVE_STEPS = (
    "0a-4_understat补采",
    "0a-5_竞彩赛程采集",
    "2c_lambda滚动监控",
    "2d_回退计数器汇总",
    "2e_窗口B就绪检查",
)

# ---- C-20261017-132: post_match 断供检测 ----
# 心跳来源：run_post_match_pipeline.py --catch-up 每轮（每小时计划任务）追加
# logs/chain_status/post_match_runs.jsonl，含 total/new/existing/not_finished/fetch_fail。
POST_MATCH_HB_NAME = "post_match_runs.jsonl"
PM_N_CONSECUTIVE = 4        # 连续坏轮数门限（≈4 小时）
PM_RUN_SPAN_H = 30.0        # 最近 4 轮总跨度上限（StartWhenAvailable 跨关机容差）
PM_HEARTBEAT_FRESH_H = 6.0  # 最近一轮心跳新鲜度；白天超此数=调度静默
PM_SILENT_HOURS = range(9, 23)   # 调度静默只在白天判定（夜间关机为正常态）
PM_BACKLOG_MIN = 3          # DB 侧已完赛日未复盘积压场道门限
PM_BACKLOG_DAYS = 3         # 积压窗 [今天-3, 昨天]：昨日及更早=已给全天完赛宽限

# ---- C-20261022-192: 监控层接入（只读扫描，非阻断） ----
# 登记≠现状的教训：scan_orphans / scan_duplicate_keys 曾登记为"已挂链"实际无任何执行入口。
# 并入本看门狗每日巡检；非阻断——扫描自身失败只入 STATUS.md 观察行，绝不拖垮巡检链。
SCAN_SCRIPTS = (
    ("scan_orphans", "scan_orphans.py"),
    ("scan_duplicate_keys", "scan_duplicate_keys.py"),
    ("scan_backfill_gap", "scan_backfill_gap.py"),   # C-20261022-194：赛果/league 回填缺口
    ("disk_error_scan", "disk_error_scan.py"),       # C-20261024-205：Disk EventID 11 控制器错误（NVMe 不迁移的替代告警）
)
SCAN_TIMEOUT_S = 300   # scan_orphans 单次实测 ~91s（8 表全量），留足余量；超时仅记观察行


def chain_date_for_now(now: datetime, override: str | None) -> str:
    if override:
        return override
    if now.hour < 12:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def phase_for_now(now: datetime, override: str | None) -> str:
    if override:
        return override
    return "late" if now.hour < 12 else "evening"


def load_result(status_dir: Path, chain: str, date: str) -> dict | None:
    return chain_alert._load_json(chain_alert.result_path(chain, date))


def registered_matches(db_path: Path, target_date: str) -> int:
    """与 generate_unified_report.discover_matches 主源同口径：odds500_match status=1。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM odds500_match "
            "WHERE season='26/27' AND status=1 AND match_date=?",
            (target_date,),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def regen_succeeded(result: dict | None) -> bool:
    """23:30 兜底链成功证据：有完成标记且 evaluate 无致命项。"""
    if not result or not result.get("finished_at"):
        return False
    return not chain_alert.evaluate_result(result)


def observe_failures(result: dict) -> list[str]:
    out = []
    for s in result.get("steps") or []:
        if s.get("step") in OBSERVE_STEPS and s.get("exit_code") not in chain_alert._OK_EXIT_VALUES:
            out.append(f"{s['step']} exit={s.get('exit_code')}")
    return out


# ==================== C-20261017-132: post_match 断供检测 ====================

def post_match_backlog(db_path: Path, now: datetime) -> list[str]:
    """已到完赛日（match_id 日期 ∈ [今天-PM_BACKLOG_DAYS, 昨天]）的已预测未复盘场次。

    防误报设计：①只看昨日及更早，给当日比赛全天完赛宽限（晚间未完赛不误报）；
    ②matches 标 actual_score='无效场次' 的排除（C-20261016-105：无效场无复盘对象）；
    ③跨键别名经 fbref_match_mapping 同 event 判重（英文键已由中文键复盘的不算积压）。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        today = now.date()
        since = (today - timedelta(days=PM_BACKLOG_DAYS)).isoformat()
        yesterday = (today - timedelta(days=1)).isoformat()
        rows = conn.execute(
            """
            SELECT DISTINCT mp.match_id
            FROM (SELECT DISTINCT match_id FROM model_predictions
                  WHERE model_name='generate_unified_report_v2.0'
                    AND COALESCE(is_replay,0)=0) mp
            LEFT JOIN matches m ON mp.match_id=m.match_id
            WHERE substr(mp.match_id,1,10) BETWEEN ? AND ?
              AND COALESCE(m.actual_score,'') <> '无效场次'
              AND NOT EXISTS (SELECT 1 FROM post_match_review p
                              WHERE p.match_id=mp.match_id)
              AND NOT EXISTS (
                    SELECT 1 FROM fbref_match_mapping f1
                    JOIN fbref_match_mapping f2 ON f1.fbref_match_id=f2.fbref_match_id
                    JOIN post_match_review p ON p.match_id=f2.odds_match_id
                    WHERE f1.odds_match_id=mp.match_id)
            ORDER BY mp.match_id
            """,
            (since, yesterday),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def load_post_match_runs(status_dir: Path, n: int) -> list[dict]:
    """读心跳 JSONL 最后 n 行；坏行/缺文件返回空（不抛异常给调用方）。"""
    p = status_dir / POST_MATCH_HB_NAME
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines()[-n:]:
        try:
            rec = json.loads(line)
            if isinstance(rec, dict) and not rec.get("dry_run"):
                out.append(rec)
        except (ValueError, TypeError):
            continue
    return out


def check_post_match_outage(
    status_dir: Path, db_path: Path, now: datetime
) -> tuple[dict | None, str]:
    """赛后链断供判定。返回 (致命告警 dict 或 None, 看板说明行)。

    双信号必须同时成立才致命（falsify-first，宁漏报不误报）：
      管线侧：最近 N 轮 catch-up 连续「有目标(total≥1)+零新复盘(new=0)+采集失败(fetch_fail≥1)」，
              且 N 轮落在 PM_RUN_SPAN_H 内、最近一轮新鲜（另一个分支：白天调度静默）；
      DB 侧：已完赛日未复盘积压 ≥ PM_BACKLOG_MIN 场（无赛程/已追平/无效场均不满足）。
    """
    today_s = now.strftime("%Y-%m-%d")
    try:
        backlog = post_match_backlog(db_path, now)
    except Exception as e:  # DB 不可达不放行（同 self_data_error 语义）
        return (dict(
            key=f"{today_s}:post_match:self_data_error",
            title="post_match 断供检测数据源异常",
            body=f"积压场次查询失败：{type(e).__name__}: {e}",
        ), "post_match：检测自身数据源异常")
    nb = len(backlog)
    base = f"post_match catch-up：近 {PM_BACKLOG_DAYS} 天已完赛日待复盘积压 {nb} 场"

    runs = load_post_match_runs(status_dir, PM_N_CONSECUTIVE)
    if not runs:
        return None, base + "（暂无心跳，首轮 catch-up 后开始监测）"

    newest = datetime.fromisoformat(runs[-1]["ts"])
    age_h = (now - newest).total_seconds() / 3600.0
    last = runs[-1]
    note = (f"{base}；最近一轮 {newest.strftime('%m-%d %H:%M')} "
            f"total={last.get('total')} new={last.get('new')} "
            f"fetch_fail={last.get('fetch_fail')}")

    if nb < PM_BACKLOG_MIN:
        return None, note + "（积压低于门限，正常态）"

    # 分支一：白天调度静默（夜间关机靠 StartWhenAvailable 补跑，不判）
    if now.hour in PM_SILENT_HOURS and age_h > PM_HEARTBEAT_FRESH_H:
        return (dict(
            key=f"{today_s}:post_match:outage",
            title="post_match 赛后链调度静默（积压未消化）",
            body=f"{base}；最近 catch-up 心跳为 {newest.strftime('%Y-%m-%d %H:%M')}，"
                 f"已 {age_h:.1f}h 无运行（>{PM_HEARTBEAT_FRESH_H:.0f}h）。"
                 f"查计划任务 SoccerModel_PostMatchReview。积压样例："
                 + "；".join(backlog[:5]),
        ), note)

    # 分支二：连续 N 轮零产出+采集失败（JWT 过期/接口断供的 exit=0 空转指纹）
    if len(runs) >= PM_N_CONSECUTIVE:
        last_n = runs[-PM_N_CONSECUTIVE:]
        oldest = datetime.fromisoformat(last_n[0]["ts"])
        span_h = (newest - oldest).total_seconds() / 3600.0
        all_bad = all(r.get("total", 0) >= 1 and r.get("new", 0) == 0
                      and r.get("fetch_fail", 0) >= 1 for r in last_n)
        if age_h <= PM_HEARTBEAT_FRESH_H and span_h <= PM_RUN_SPAN_H and all_bad:
            return (dict(
                key=f"{today_s}:post_match:outage",
                title="post_match 赛后链采集断供（连续零复盘+采集失败）",
                body=f"最近 {PM_N_CONSECUTIVE} 轮 catch-up（{span_h:.1f}h 内）全部零新复盘且采集失败，"
                     f"任务 exit=0 空转；{base}。典型成因：SofaScore JWT(sofa_captcha) 过期"
                     f"（403 jwt:time-out-of-bounds），续期=start_edge_sofa.bat 人工过挑战重导 cookie；"
                     f"积压样例：" + "；".join(backlog[:5]),
            ), note + "（连续坏轮，已判断供）")

    return None, note + "（未达断供门限）"


def run_readonly_scans() -> list[str]:
    """C-20261022-192：每日只读扫描（非阻断）。

    scan_orphans / scan_duplicate_keys 各自有独立的 exit 语义（前者 FK 保护表非零即
    异常，后者默认恒 0），此处**只采集不裁决**——任何失败/超时/缺脚本都只产出一行
    观察记录，不进入 fatal、不改看门狗退出码，避免"仪器失败拖垮巡检链"。
    """
    notes = []
    for label, fname in SCAN_SCRIPTS:
        path = BASE_DIR / "scripts" / fname
        if not path.exists():
            notes.append(f"{label}: 脚本缺失")
            continue
        try:
            # C-20261023-196：子扫描 stdout 是管道时回落 GBK，打印 ⚠️ 类字符即
            # UnicodeEncodeError（scan_backfill_gap 挂链首日实发，哨兵静默变瞎）。
            # 强制子进程 UTF-8 IO + 显式解码，整个 SCAN_SCRIPTS 注册表一次性免疫。
            proc = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(BASE_DIR), capture_output=True,
                timeout=SCAN_TIMEOUT_S,
                encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            out_lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
            # scan_orphans 末行是耗时统计，无信息量 → 优先取结论/告警行
            out_lines = [ln for ln in out_lines if not ln.startswith("总耗时")]
            err_lines = [ln.strip() for ln in (proc.stderr or "").splitlines() if ln.strip()]
            # scan_backfill_gap 首行才是主信号（分桶计数+⚠️），末行是 league 计数 → 取首行
            tail = (out_lines[0] if label == "scan_backfill_gap" else out_lines[-1]) if out_lines else (err_lines[-1] if err_lines else "")
            notes.append(f"{label}: exit={proc.returncode} {tail[:160]}")
        except subprocess.TimeoutExpired:
            notes.append(f"{label}: 超时>{SCAN_TIMEOUT_S}s（非阻断）")
        except Exception as e:  # 扫描器自身异常绝不能冒泡到看门狗
            notes.append(f"{label}: 执行异常 {type(e).__name__}（非阻断）")
    return notes


def run(status_dir: Path, reports_dir: Path, db_path: Path,
        date: str, phase: str, now: datetime) -> int:
    # 注入告警器状态目录（生产=默认 logs/chain_status；固件测试=临时目录）
    chain_alert.STATUS_DIR = status_dir
    fatal: list[dict] = []   # {key,title,body}
    observes: list[str] = []
    target_report_date = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")

    result = load_result(status_dir, CHAIN, date)
    chain_failed = False
    still_running = False
    chain_state = "无心跳"

    if result is None:
        chain_state = "无心跳/无结果"
        fatal.append(dict(
            key=f"{date}:{CHAIN}:no_heartbeat",
            title=f"{CHAIN} {date} 无心跳（未启动或启动即死）",
            body="logs/chain_status/ 无当日状态文件。查计划任务 TraeCode_PrematchReport_2030 "
                 "Last Result；解析阶段失败发生在日志创建前（参见 C-125）。",
        ))
        chain_failed = True
    elif not result.get("finished_at"):
        if phase == "late":
            chain_state = "运行超时"
            fatal.append(dict(
                key=f"{date}:{CHAIN}:running_late",
                title=f"{CHAIN} {date} 超时未完成",
                body=f"01:10 巡检仍只有开始心跳（{result.get('started_at')}），无完成标记。",
            ))
            chain_failed = True
        else:
            chain_state = f"运行中（{result.get('started_at')}）"
            still_running = True
    else:
        reasons = chain_alert.evaluate_result(result)
        if reasons:
            chain_state = "致命步骤失败"
            fatal.append(dict(
                key=f"{date}:{CHAIN}:step_fail",
                title=f"{CHAIN} {date} 致命步骤失败",
                body="；".join(reasons),
            ))
            chain_failed = True
        else:
            chain_state = f"完成（{result.get('finished_at')}）"
            observes.extend(observe_failures(result))

    # 产物核对（独立于链自报；注册=0 为空窗正常态）
    artifact_note = ""
    try:
        registered = registered_matches(db_path,
                                        (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1))
                                        .strftime("%Y-%m-%d"))
    except Exception as e:  # DB 不可达不能静默放行
        fatal.append(dict(
            key=f"{date}:watchdog:self_data_error",
            title="看门狗自身数据源异常",
            body=f"odds.db odds500_match 查询失败：{type(e).__name__}: {e}",
        ))
        registered = None

    if registered is not None:
        rdir = reports_dir / target_report_date
        summary = rdir / "_summary.md"
        report_n = len(list(rdir.glob("*.md"))) - (1 if summary.exists() else 0)
        artifact_note = f"明日报告目录 {target_report_date}: 注册 {registered} 场 / 报告 {max(report_n, 0)} 份"
        if registered > 0 and not still_running:
            if not summary.exists() or report_n <= 0:
                fatal.append(dict(
                    key=f"{date}:{CHAIN}:artifact_missing",
                    title=f"{CHAIN} {date} 报告产物缺失",
                    body=f"{artifact_note}；_summary.md 存在={summary.exists()}。",
                ))
            elif report_n != registered:
                # 偏差但双方均>0：先观察校准，不弹致命（防机械误报，C-107 教训）
                observes.append(artifact_note + "（数量偏差，观察中）")

    # 掩盖检测：23:30 兜底成功而 20:30 失败
    regen_result = load_result(status_dir, REGEN, date)
    if chain_failed and regen_succeeded(regen_result):
        fatal.append(dict(
            key=f"{date}:{CHAIN}:masked_by_regen",
            title=f"{date} 20:30 链失败被 23:30 兜底掩盖",
            body="23:30 report_regen 用已采集数据重生成成功，报告看似齐全但采集/特征链未跑，"
                 "勿把兜底产物当正常链结果。",
        ))

    # post_match 断供检测（C-20261017-132）：独立于赛前链心跳，读赛后链心跳+DB 积压
    pm_fatal, pm_note = check_post_match_outage(status_dir, db_path, now)
    if pm_fatal:
        fatal.append(pm_fatal)

    # 发告警（raise_alert 内置同键去重）
    fired = []
    for item in fatal:
        if chain_alert.raise_alert("fatal", item["key"], item["title"], item["body"],
                                   source="chain_watchdog"):
            fired.append(item["title"])

    # 每日只读扫描（C-20261022-192）：仅记录，不影响 fatal / 退出码
    scan_notes = run_readonly_scans()

    footer = [
        f"最近巡检: {now.strftime('%Y-%m-%d %H:%M:%S')}（phase={phase}，链日 {date}）",
        f"- {CHAIN}: {chain_state}",
        f"- {REGEN}: {'成功（兜底已跑）' if regen_succeeded(regen_result) else '未跑/无成功证据'}",
    ]
    if artifact_note:
        footer.append(f"- {artifact_note}")
    footer.append(f"- {pm_note}")
    for n in scan_notes:
        footer.append(f"- 只读扫描 {n}")
    if observes:
        footer.append("- 观察项（不告警）: " + "；".join(observes))
    if fired:
        footer.append(f"- 本次新发出告警 {len(fired)} 条: " + "；".join(fired))
    chain_alert.render_status(extra_open=None, footer_lines=footer)

    print(f"[watchdog] phase={phase} chain_date={date} state={chain_state} "
          f"fatal={len(fatal)} new_fired={len(fired)} observe={len(observes)}")
    for f in fired:
        print(f"  [FATAL] {f}")
    if registered is None:
        return 2
    return 1 if fatal else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="赛前链看门狗 C-20261017-126")
    ap.add_argument("--chain-date", help="YYYY-MM-DD，默认按触发时刻自动锚定")
    ap.add_argument("--phase", choices=("evening", "late"), help="默认按小时自动判定")
    ap.add_argument("--status-dir", default=str(DEFAULT_STATUS_DIR))
    ap.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    now = datetime.now(CN_TZ)
    date = chain_date_for_now(now, args.chain_date)
    phase = phase_for_now(now, args.phase)
    try:
        return run(Path(args.status_dir), Path(args.reports_dir), Path(args.db),
                   date, phase, now)
    except Exception as e:
        # 看门狗自身未捕获异常：尽力落物证，不用弹窗（弹窗链路本身可能就是故障点）
        try:
            (DEFAULT_STATUS_DIR / "desktop").mkdir(parents=True, exist_ok=True)
            (DEFAULT_STATUS_DIR / "desktop" / "链告警_watchdog_crash.txt").write_text(
                f"看门狗自身崩溃 {now:%Y-%m-%d %H:%M:%S}\n{type(e).__name__}: {e}\n",
                encoding="utf-8")
        except OSError:
            pass
        print(f"[watchdog] CRASH {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
