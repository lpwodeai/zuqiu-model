"""
训练运行仪表盘生成器
====================

从 training_runs 表读取最近 N 次运行，生成 Markdown 仪表盘报告。

用法：
    python -m pipeline.generate_training_dashboard [--limit 20] [--output reports/training_runs_dashboard.md]
"""

import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.training_orchestrator import get_recent_runs


def generate_dashboard(limit: int = 20) -> str:
    """生成 Markdown 格式的仪表盘报告。"""
    runs = get_recent_runs(limit)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append("# 训练运行仪表盘")
    lines.append("")
    lines.append(f"> 生成时间: {now}")
    lines.append(f"> 最近 {len(runs)} 次运行")
    lines.append("")

    # ---- 总览统计 ----
    total = len(runs)
    success = sum(1 for r in runs if r["status"] == "success")
    failed = sum(1 for r in runs if r["status"] == "failed")
    running = sum(1 for r in runs if r["status"] == "running")
    skipped = sum(1 for r in runs if r["status"] == "skipped")
    success_rate = (success / total * 100) if total else 0

    lines.append("## 总览")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| 总运行次数 | {total} |")
    lines.append(f"| 成功 | {success} |")
    lines.append(f"| 失败 | {failed} |")
    lines.append(f"| 运行中 | {running} |")
    lines.append(f"| 跳过 | {skipped} |")
    lines.append(f"| 成功率 | {success_rate:.1f}% |")
    lines.append("")

    # ---- Trigger 分布 ----
    trigger_counts = {}
    for r in runs:
        t = r["trigger_source"]
        trigger_counts[t] = trigger_counts.get(t, 0) + 1

    lines.append("## 触发原因分布")
    lines.append("")
    lines.append("| trigger | 次数 | 占比 |")
    lines.append("|---------|------|------|")
    for t, cnt in sorted(trigger_counts.items(), key=lambda x: -x[1]):
        pct = (cnt / total * 100) if total else 0
        lines.append(f"| {t} | {cnt} | {pct:.1f}% |")
    lines.append("")

    # ---- 训练类型分布 ----
    type_counts = {}
    for r in runs:
        t = r["training_type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    lines.append("## 训练类型分布")
    lines.append("")
    lines.append("| 类型 | 次数 |")
    lines.append("|------|------|")
    for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {t} | {cnt} |")
    lines.append("")

    # ---- 指标趋势（成功运行） ----
    success_runs = [r for r in runs if r["status"] == "success"
                    and r["val_rps"] is not None]
    if success_runs:
        lines.append("## 验证指标趋势（成功运行）")
        lines.append("")
        lines.append("| run_id | 日期 | trigger | type | RPS | LogLoss | Acc | DrawRecall |")
        lines.append("|--------|------|---------|------|-----|---------|-----|------------|")
        for r in reversed(success_runs[-10:]):  # 最早的在前
            date = str(r["started_at"])[:10]
            rps = f"{r['val_rps']:.4f}" if r["val_rps"] else "-"
            ll = f"{r['val_logloss']:.4f}" if r["val_logloss"] else "-"
            acc = f"{r['val_acc']:.4f}" if r["val_acc"] else "-"
            dr = f"{r['val_draw_recall']:.4f}" if r["val_draw_recall"] else "-"
            lines.append(f"| {r['run_id']} | {date} | {r['trigger_source']} | "
                         f"{r['training_type']} | {rps} | {ll} | {acc} | {dr} |")
        lines.append("")

    # ---- 最近运行明细 ----
    lines.append("## 最近运行明细")
    lines.append("")
    lines.append("| run_id | 开始时间 | trigger | type | 状态 | RPS | Acc |")
    lines.append("|--------|----------|---------|------|------|-----|-----|")
    for r in runs:
        started = str(r["started_at"])[:16]
        status_emoji = {
            "success": "✅",
            "failed": "❌",
            "running": "⏳",
            "skipped": "⏭️",
        }.get(r["status"], r["status"])
        rps = f"{r['val_rps']:.4f}" if r["val_rps"] else "-"
        acc = f"{r['val_acc']:.4f}" if r["val_acc"] else "-"
        lines.append(f"| {r['run_id']} | {started} | {r['trigger_source']} | "
                     f"{r['training_type']} | {status_emoji} {r['status']} | "
                     f"{rps} | {acc} |")
    lines.append("")

    # ---- 失败详情（如有） ----
    failed_runs = [r for r in runs if r["status"] == "failed"]
    if failed_runs:
        lines.append("## 失败详情")
        lines.append("")
        for r in failed_runs[:5]:
            lines.append(f"### run_id={r['run_id']}")
            lines.append(f"- 开始时间: {r['started_at']}")
            lines.append(f"- trigger: {r['trigger_source']}")
            lines.append(f"- type: {r['training_type']}")
            err = (r["error_msg"] or "(无)").replace("\n", " ")
            lines.append(f"- 错误: {err[:200]}")
            lines.append("")

    if not runs:
        lines.append("> ⚠️ 暂无训练运行记录")
        lines.append("")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="生成训练运行仪表盘")
    parser.add_argument("--limit", type=int, default=20,
                        help="最近 N 次运行")
    parser.add_argument("--output", type=str,
                        default=None,
                        help="输出文件路径（默认打印到 stdout）")
    args = parser.parse_args()

    md = generate_dashboard(args.limit)

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = PROJECT_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"✅ 仪表盘已生成: {out_path}")
    else:
        print(md)


if __name__ == "__main__":
    main()
