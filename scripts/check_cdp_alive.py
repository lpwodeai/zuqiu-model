# -*- coding: utf-8 -*-
"""CDP 9222 连通性检查（C-112-0d 无人值守可行性验证）。

每次调用向 http://localhost:9222/json/version 发一次请求，
结果追加到 reports/cdp_alive_log.md（时间戳 + OK/FAIL + 详情）。

用法：
  python scripts/check_cdp_alive.py              # 检查一次，追加日志
  python scripts/check_cdp_alive.py --tag xxx    # 日志行带来源标记

只读外发请求 + 追加日志文件，不碰任何生产链路。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "reports" / "cdp_alive_log.md"
CDP_URL = "http://localhost:9222/json/version"


def check_once(timeout: float = 5.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(CDP_URL, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        browser = data.get("Browser", "?")
        ws = "webSocketDebuggerUrl" in data
        return True, f"{browser} ws={'yes' if ws else 'no'}"
    except Exception as e:  # noqa: BLE001 - 任何异常都视为端口不通
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="manual", help="日志来源标记（manual / bat-restart / sched-1855）")
    args = ap.parse_args()

    ok, detail = check_once()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        LOG.write_text(
            "# CDP 9222 连通性日志（C-112-0d）\n\n"
            "| 时间 | 结果 | 来源 | 详情 |\n|---|---|---|---|\n",
            encoding="utf-8",
        )
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"| {ts} | {'✅ OK' if ok else '❌ FAIL'} | {args.tag} | {detail} |\n")

    print(f"[{ts}] {'OK' if ok else 'FAIL'} ({args.tag}) {detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
