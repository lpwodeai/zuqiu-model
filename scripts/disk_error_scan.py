# -*- coding: utf-8 -*-
"""磁盘控制器错误扫描（C-20261024-205 · 只读软模式）

背景：2026-10-23 硬盘线断开，System 日志出现 6 次 Disk 源 EventID 11
（\\Device\\Harddisk1\\DR5 控制器错误，13:42-18:14），页缓存被清 → 0a-3 特征重算
52min→155min。用户裁决：不迁 NVMe（西数专用盘已换线），若再发生以本告警替代。

规格（C-205 写死）：
  监控源：Windows System 日志，Provider=Disk，EventID 11
  判定：  近 24h 计数 >0 → 黄级；>=3 → 红级
  挂链位：chain_watchdog SCAN_SCRIPTS 非阻断位（C-20261022-192 机制）
  分工：  只管硬件层早期信号；0a-3 时长维度由设计稿 §7.1 + 超时降级覆盖

退出码：恒 0（软模式，非阻断；查询自身失败也打印错误行仍 exit 0——仪器失败不拖垮巡检链）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys

YELLOW_MIN = 1   # >0 即黄级
RED_MIN = 3      # >=3 红级（昨日事故实测 6 次）


def query_events(hours: int) -> tuple[int | None, list[str], str | None]:
    """返回 (计数, 时间戳列表, 错误信息)。计数 None = 查询失败。"""
    ps_cmd = (
        "$ev = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Disk'; Id=11; "
        f"StartTime=(Get-Date).AddHours(-{hours})}} -ErrorAction SilentlyContinue; "
        "if ($ev) { $ev | ForEach-Object { $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss') } }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, timeout=60, encoding="utf-8", errors="replace",
        )
    except Exception as exc:  # 超时/无 powershell 等
        return None, [], f"查询进程失败: {exc}"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, [], f"Get-WinEvent 失败: {err[-1] if err else 'unknown'}"
    times = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return len(times), times, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24, help="回看窗口（默认 24h）")
    ap.add_argument("--verbose", action="store_true", help="列出每次错误时间戳")
    args = ap.parse_args()

    count, times, err = query_events(args.hours)
    if count is None:
        print(f"[disk] 查询失败（{err}）——仪器异常，非磁盘结论")
        return 0

    if args.verbose and times:
        for t in times:
            print(f"  {t}")

    if count >= RED_MIN:
        level, icon = "红级", "[RED]"
    elif count >= YELLOW_MIN:
        level, icon = "黄级", "[YELLOW]"
    else:
        level, icon = "正常", "[OK]"

    # 结论行必须放最后：chain_watchdog 对非 scan_backfill_gap 脚本取末行
    print(f"{icon} Disk EventID 11 近{args.hours}h={count} 次 → {level}"
          f"（阈值 >0 黄 / >=3 红；2026-10-23 事故=6 次）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
