# -*- coding: utf-8 -*-
"""
setup_reliability_scheduler.py — P0-B 可靠性图门禁每日计划任务配置脚本
=========================================================================
用途：注册 / 更新 / 删除 Windows 计划任务「SoccerModel_ReliabilityGate」，
      每日 09:00 调用 scripts/reliability_gate.py，
      自动重算 ECE / Reliability Diagram / 全维度分层，超阈值 exit 1 阻断投产，
      产物按日归档 docs/reliability_gate/{YYYYMMDD}/。

对齐已有约定：
  - P0-C 每日任务 SoccerModel_ConceptDrift（每日 08:30，concept_drift_gate.py）
  - P0-E 每日任务 SoccerModel_P0ELiveTrial（每日 12:00，run_daily_p0e.py）
  - P1-B 每日任务 SoccerModel_P1BShadow（每日 13:00，setup_shadow_scheduler.py 管理）
  - 运行脚本 reliability_gate.py 自身路径均基于 __file__ 解析，与工作目录无关

门禁语义：reliability_gate.py 在平局高估缺陷（待办②）闭环前会持续 FAIL（exit 1），
          该非零退出码即「阻断投产」信号，属预期行为——每日报告仍会正常落盘。

默认为「无论用户是否登录都运行」（以 SYSTEM 账户运行，无需密码），需以管理员身份运行本脚本。

用法：
  python scripts/setup_reliability_scheduler.py            # 注册任务（幂等：先删后建，需管理员）
  python scripts/setup_reliability_scheduler.py --delete   # 仅删除任务（需管理员）
  python scripts/setup_reliability_scheduler.py --query    # 查询任务详情（只读，无需管理员）
  python scripts/setup_reliability_scheduler.py --st 18:30 # 自定义每日触发时间
  python scripts/setup_reliability_scheduler.py --ru "DOMAIN\\User" --password "xxx"  # 改用指定账户+密码（无论是否登录）

注：以 SYSTEM 运行时，若 Python 依赖装在用户 site-packages（pip --user），
    SYSTEM 可能读不到，需改用系统级安装或通过 --ru 指定有依赖的账户。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GATE_SCRIPT = SCRIPT_DIR / "reliability_gate.py"

TASK_NAME = "SoccerModel_ReliabilityGate"
DEFAULT_TIME = "09:00"
DEFAULT_RUN_AS = "SYSTEM"
DESCRIPTION = "P0-B 可靠性图自动化门禁：每日重算 ECE/Reliability Diagram/全维度分层，超阈值 exit 1 阻断投产（无论是否登录）"


def build_command(python_exec: str) -> str:
    """构造 schtasks /TR 要执行的命令（含引号，防止路径空格）。"""
    return f'"{python_exec}" "{GATE_SCRIPT}"'


def create_task(python_exec: str, at_time: str, run_as: str, password: str | None = None) -> int:
    """注册每日计划任务（先删后建保证幂等），以 /ru 指定账户实现「无论是否登录都运行」。

    - run_as=SYSTEM：无需密码，加 /rl HIGHEST（需管理员运行本脚本）。
    - run_as=其它账户：需提供 /rp 密码以存储凭据。
    返回 schtasks 退出码。
    """
    command = build_command(python_exec)
    create_cmd = [
        "schtasks", "/create",
        "/tn", TASK_NAME,
        "/tr", command,
        "/sc", "daily",
        "/st", at_time,
        "/ru", run_as,
    ]
    if run_as.upper() == "SYSTEM":
        create_cmd += ["/rl", "HIGHEST"]
    elif password:
        create_cmd += ["/rp", password]
    create_cmd += ["/f"]

    print(f"[setup] 创建计划任务:")
    print(f"        任务名  : {TASK_NAME}")
    print(f"        触发    : 每日 {at_time}")
    print(f"        运行身份: {run_as}{'（无需密码）' if run_as.upper() == 'SYSTEM' else ''}")
    print(f"        执行    : {command}")
    print(f"        原始命令: {' '.join(create_cmd)}\n")
    result = subprocess.run(create_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print("✅ 计划任务创建成功\n")
        print(result.stdout)
    else:
        print("❌ 计划任务创建失败\n")
        print(result.stderr or result.stdout)
    return result.returncode


def delete_task() -> int:
    """删除计划任务（不存在时忽略）。返回 schtasks 退出码。"""
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"[setup] 已删除旧任务 {TASK_NAME}\n")
    else:
        print(f"[setup] 未发现旧任务 {TASK_NAME}（或删除失败）：{result.stderr.strip()}\n")
    return result.returncode


def query_task() -> int:
    """查询任务详情。"""
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_NAME, "/v", "/fo", "LIST"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"❌ 未查询到任务 {TASK_NAME}\n{result.stderr}")
    return result.returncode


def main() -> None:
    ap = argparse.ArgumentParser(description="P0-B 可靠性门禁每日计划任务配置（无论是否登录都运行）")
    ap.add_argument("--delete", action="store_true", help="仅删除任务（需管理员）")
    ap.add_argument("--query", action="store_true", help="查询任务详情（只读）")
    ap.add_argument("--st", default=DEFAULT_TIME, help=f"每日触发时间 HH:MM（默认 {DEFAULT_TIME}）")
    ap.add_argument("--python", default=sys.executable, help="Python 解释器路径（默认当前 sys.executable）")
    ap.add_argument("--ru", default=DEFAULT_RUN_AS, help=f"运行账户（默认 {DEFAULT_RUN_AS}=无需密码；其它账户需搭配 --password）")
    ap.add_argument("--password", default=None, help="运行账户密码（仅当 --ru 非 SYSTEM 时需提供）")
    args = ap.parse_args()

    if args.query:
        query_task()
        return
    if args.delete:
        delete_task()
        return

    if args.ru.upper() != "SYSTEM" and not args.password:
        print(f"❌ 运行账户 {args.ru} 非 SYSTEM，必须提供 --password 才能「无论是否登录都运行」")
        sys.exit(2)

    print("========== 配置 P0-B 可靠性门禁每日计划任务 ==========")
    print(f"门禁脚本 : {GATE_SCRIPT}")
    print(f"Python   : {args.python}")
    print(f"运行身份 : {args.ru}{'（无需密码）' if args.ru.upper() == 'SYSTEM' else ''}\n")

    delete_task()
    rc = create_task(args.python, args.st, args.ru, args.password)

    print("========== 配置完成 ==========")
    print(f"任务名 : {TASK_NAME}")
    print(f"周期   : 每日 {args.st}（重算 ECE/Reliability Diagram/分层 → 超阈值 exit 1），无论是否登录都运行")
    print(f"产物   : docs/reliability_gate/{{YYYYMMDD}}/reliability_gate.md / .json / 图")
    print(f"查阅   : python scripts/setup_reliability_scheduler.py --query  # 看任务详情")
    sys.exit(rc)


if __name__ == "__main__":
    main()