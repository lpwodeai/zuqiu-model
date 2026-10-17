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
  self_data_error  看门狗自身 DB 读取失败（不放行）                fatal + exit 2
  观察步骤失败 / 报告数与注册数偏差>0（两者均>0）                   仅看板
  0a-3 SKIP-* / 注册场次=0 空窗                                   正常态

退出码：0=全绿（可含观察项） 1=有致命告警 2=自身异常
"""
from __future__ import annotations

import argparse
import json
import sqlite3
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

    # 发告警（raise_alert 内置同键去重）
    fired = []
    for item in fatal:
        if chain_alert.raise_alert("fatal", item["key"], item["title"], item["body"],
                                   source="chain_watchdog"):
            fired.append(item["title"])

    footer = [
        f"最近巡检: {now.strftime('%Y-%m-%d %H:%M:%S')}（phase={phase}，链日 {date}）",
        f"- {CHAIN}: {chain_state}",
        f"- {REGEN}: {'成功（兜底已跑）' if regen_succeeded(regen_result) else '未跑/无成功证据'}",
    ]
    if artifact_note:
        footer.append(f"- {artifact_note}")
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
