# -*- coding: utf-8 -*-
"""P0-E 实盘 300 注 — 每日节奏编排（协议 §五每日运行节奏自动化）

每日自动执行：
  1. 结算昨日 pending（live_trial_away_favorite --settle，幂等，回填 W/L 与盈亏 + post_match_review 回写）
  2. 采集竞彩今日开盘赔率（sporttery_live_collector，当日官方日赛事）
  3. 生成未来窗口四维预测并回写 WDL_away（generate_unified_report --days N）
  4. 投注单 dry-run（live_trial_away_favorite --days LOOKAHEAD，生成 reports/live_trial_slip_*.md）
  5. （--auto-commit）候选 > 0 时自动写台账 pending（预注册记录；真实下注仍由人工在竞彩执行）
  6. 评估报告（live_trial_evaluation，若有 settled）

用法：
  python scripts/run_daily_p0e.py                    # 完整链路，不自动 commit
  python scripts/run_daily_p0e.py --auto-commit      # 候选自动写台账 pending
  python scripts/run_daily_p0e.py --skip-collect     # 调试：跳过竞彩采集
  python scripts/run_daily_p0e.py --skip-predict     # 调试：跳过模型预测

计划任务（每日 12:00）：
  schtasks /Create /TN "SoccerModel_P0ELiveTrial" /TR "C:\\Python314\\python.exe F:\\zuqiu\\五大联赛专属模型\\五大联赛专属模型\\scripts\\run_daily_p0e.py --auto-commit" /SC DAILY /ST 12:00 /F

日志：logs/run_daily_p0e_<YYYYMMDD>.log
"""
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

PY = sys.executable

# 子脚本（相对 scripts/）
COLLECT = "sporttery_live_collector.py"
PREDICT = "generate_unified_report.py"
SLIP = "live_trial_away_favorite.py"
EVAL = "live_trial_evaluation.py"


def run(cmd, label):
    """执行子进程，实时透传输出，失败抛异常。"""
    print(f"\n{'=' * 62}\n[{label}] {cmd}\n{'=' * 62}")
    proc = subprocess.run(cmd, cwd=str(PROJECT_DIR), text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"[{label}] 退出码 {proc.returncode}")
    return proc


def main():
    ap = argparse.ArgumentParser(description="P0-E 实盘 300 注每日节奏编排")
    ap.add_argument("--days", type=int, default=4, help="预测覆盖未来天数（默认 4，覆盖竞彩±1天错位）")
    ap.add_argument("--lookahead", type=int, default=7, help="投注窗口天数（传给 live_trial，默认 7）")
    ap.add_argument("--auto-commit", action="store_true", help="候选>0 自动写台账 pending")
    ap.add_argument("--skip-collect", action="store_true", help="跳过竞彩采集")
    ap.add_argument("--skip-predict", action="store_true", help="跳过模型预测")
    ap.add_argument("--no-eval", action="store_true", help="跳过评估报告")
    args = ap.parse_args()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_path = LOG_DIR / f"run_daily_p0e_{datetime.now():%Y%m%d}.log"
    print(f"[P0-E 每日节奏] {ts} | auto_commit={args.auto_commit} "
          f"days={args.days} lookahead={args.lookahead}")

    try:
        # 1) 结算昨日 pending（幂等；无 pending 亦安全）
        run([PY, str(SCRIPT_DIR / SLIP), "--settle", "--no-report"], "1/5 结算")

        # 2) 采集竞彩今日赔率
        if not args.skip_collect:
            run([PY, str(SCRIPT_DIR / COLLECT)], "2/5 竞彩采集")

        # 3) 生成未来预测（回写 WDL_away）
        if not args.skip_predict:
            run([PY, str(SCRIPT_DIR / PREDICT), "--days", str(args.days)], "3/5 模型预测")

        # 4) dry-run 投注单（始终生成报告供审计）
        run([PY, str(SCRIPT_DIR / SLIP), "--days", str(args.lookahead)], "4/5 投注单 dry-run")

        # 5) 候选自动提交（预注册记录）
        if args.auto_commit:
            run([PY, str(SCRIPT_DIR / SLIP), "--commit", "--no-report", "--days", str(args.lookahead)],
                "5/5 台账 commit")

        # 6) 评估报告
        if not args.no_eval:
            run([PY, str(SCRIPT_DIR / EVAL)], "6/6 评估")

        print(f"\n[P0-E] 每日节奏完成 ✓（日志 {log_path}）")
        return 0
    except RuntimeError as e:
        print(f"\n[P0-E] 每日节奏中断 ✗ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
