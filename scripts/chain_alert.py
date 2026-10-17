# -*- coding: utf-8 -*-
"""
链失败本地告警器 (chain_alert.py) — C-20261017-126
====================================================================
仅本地通道（不外发，例行邮件保持停用）：
  1. alerts_YYYYMM.jsonl   全量告警历史（append-only）
  2. alerts_state.json     「链日+事件键」去重状态（同键跨夜两次触发只告一次）
  3. STATUS.md             当前看板（未消致命项 + 最近巡检/链结果）
  4. desktop/<键安全名>.txt 致命级桌面物证（弹窗在非交互会话可能失败，文件是 SSOT）
  5. notify_local.ps1      NotifyIcon 气泡（best-effort，失败静默）

两种调用：
  # 链结束自判（ps1 调用；读链结果 json，致命步骤失败/exit≠0 才告警）
  python chain_alert.py inspect-chain --result logs/chain_status/prematch_2030_20261017.json

  # 通用直发（看门狗 import 本模块 API，或命令行 --raise）
  python chain_alert.py raise --level fatal --key 2026-10-17:prematch_2030:no_heartbeat \
      --title "..." --body "..." --source chain_watchdog

退出码：永不因通知失败返回非 0（告警器自身故障不得反噬调用方，F5 隔离）；
仅参数/文件硬错误返回 2。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# 模块级可变：生产用默认路径；看门狗可注入（固件对拍用临时目录，不污染生产）
STATUS_DIR = BASE_DIR / "logs" / "chain_status"
NOTIFY_PS1 = Path(__file__).resolve().parent / "notify_local.ps1"


def _desktop_dir() -> Path:
    return STATUS_DIR / "desktop"
CN_TZ = timezone(timedelta(hours=8))

# 致命步骤白名单（与 run_prematch_2030.ps1 / chain_watchdog.py 共用同一定义）
# 观察类（0a-4/0a-5/2c/2d/2e）失败不告警；0a-3 的 SKIP-* 语义视同成功。
FATAL_STEPS = (
    "0a_赛前数据补采",
    "0a-1_赛前官方首发伤停采集",
    "0a-2_伤停同步入库",
    "0a-3_赛前特征重算",
    "0b_竞彩时序赔率回踩",
    "0c_Elo快照重建",
    "1_生成预测报告",
)

_OK_EXIT_VALUES = {0, "0", "SKIP-FLAG", "SKIP-0", "SKIP", None}


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def result_path(chain: str, chain_date: str) -> Path:
    """chain_date: YYYY-MM-DD 或 YYYYMMDD"""
    d = chain_date.replace("-", "")
    return STATUS_DIR / f"{chain}_{d}.json"


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _key_filename(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-:一-鿿]", "_", key).replace(":", "_")


def evaluate_result(result: dict) -> list[str]:
    """读链结果 json，返回致命失败原因列表（空=全绿/仅观察级失败/SKIP 正常态）。"""
    fatal: list[str] = []
    chain = result.get("chain", "unknown")
    if not result.get("finished_at"):
        fatal.append(f"{chain} 心跳存在但无完成标记（中断/超时被杀）")
    steps = result.get("steps") or []
    seen = {s.get("step"): s.get("exit_code") for s in steps}
    for name in FATAL_STEPS:
        code = seen.get(name)
        if code not in _OK_EXIT_VALUES:
            fatal.append(f"致命步骤 {name} exit={code}")
    # report_regen_2330 兜底链步骤名为单一步骤
    for s in steps:
        nm = s.get("step", "")
        if chain == "report_regen_2330" and nm.startswith("regen"):
            if s.get("exit_code") not in _OK_EXIT_VALUES:
                fatal.append(f"{nm} exit={s.get('exit_code')}")
    ec = result.get("exit_code")
    if ec not in _OK_EXIT_VALUES and not fatal:
        fatal.append(f"链整体 exit_code={ec}")
    return fatal


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_state() -> dict:
    p = STATUS_DIR / "alerts_state.json"
    data = _load_json(p)
    return data if isinstance(data, dict) else {"fired": {}}


def _save_state(state: dict) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    (STATUS_DIR / "alerts_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_evidence(key: str, title: str, body: str, ts: str) -> Path:
    ddir = _desktop_dir()
    ddir.mkdir(parents=True, exist_ok=True)
    p = ddir / f"链告警_{_key_filename(key)}.txt"
    p.write_text(
        f"【赛前链致命告警】\n时间: {ts}\n事件键: {key}\n\n{title}\n\n{body}\n",
        encoding="utf-8",
    )
    return p


def _popup(title: str, message: str) -> None:
    """NotifyIcon 气泡，best-effort：非交互会话/无桌面时静默失败。"""
    try:
        subprocess.run(
            [
                r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(NOTIFY_PS1), "-Title", title, "-Message", message[:220],
            ],
            timeout=30,
            capture_output=True,
        )
    except Exception:
        pass


def render_status(extra_open: list[dict] | None = None,
                  footer_lines: list[str] | None = None) -> None:
    """覆盖写 STATUS.md 看板：未消致命项（当日 jsonl 取 fatal）+ 页脚。"""
    today = now_cn().strftime("%Y%m%d")
    month = now_cn().strftime("%Y%m")
    open_items: dict[str, dict] = {}
    jl = STATUS_DIR / f"alerts_{month}.jsonl"
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("level") != "fatal":
                continue
            ts_day = (rec.get("ts") or "")[:10].replace("-", "")
            # 只展示当天/前一天（链在 23:55~01:10 跨夜）的未消项
            if ts_day not in (today, (now_cn() - timedelta(days=1)).strftime("%Y%m%d")):
                continue
            open_items[rec.get("key")] = rec
    for it in extra_open or []:
        open_items.setdefault(it["key"], it)

    lines = [
        "# 赛前链运行看板（chain_watchdog / C-20261017-126）",
        "",
        f"更新时间: {now_cn().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if open_items:
        lines.append(f"## 🔴 未消致命告警（{len(open_items)}）")
        lines.append("")
        for rec in open_items.values():
            lines.append(f"- **{rec.get('title', rec.get('key'))}**  ")
            lines.append(f"  {rec.get('ts', '')}｜键 `{rec.get('key')}`｜源 {rec.get('source', '?')}")
            if rec.get("body"):
                lines.append(f"  <br>{rec['body']}")
        lines.append("")
    else:
        lines += ["## 🟢 当前无未消致命告警", ""]
    lines.append("---")
    lines.append("全量历史见 alerts_YYYYMM.jsonl；致命告警物证见 desktop/ 目录。")
    for fl in footer_lines or []:
        lines.append("")
        lines.append(fl)
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    (STATUS_DIR / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def raise_alert(level: str, key: str, title: str, body: str, source: str) -> bool:
    """发起告警。返回 True=新发出，False=去重命中。任何外显失败不抛异常。"""
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    fired: dict = state.setdefault("fired", {})
    ts = now_cn().strftime("%Y-%m-%d %H:%M:%S")
    record = {"ts": ts, "level": level, "key": key, "title": title,
              "body": body, "source": source}
    if key in fired:
        return False
    try:
        _append_jsonl(STATUS_DIR / f"alerts_{now_cn().strftime('%Y%m')}.jsonl", record)
    except OSError:
        return False
    fired[key] = ts
    _save_state(state)
    if level == "fatal":
        try:
            ev = _write_evidence(key, title, body, ts)
            record["evidence"] = str(ev)
        except OSError:
            pass
        _popup(f"[链告警] {title}", body)
    render_status()
    return True


def cmd_inspect_chain(result_file: str) -> int:
    result = _load_json(Path(result_file))
    if result is None:
        raise SystemExit(2)
    chain = result.get("chain", "unknown")
    date = result.get("chain_date", "")
    reasons = evaluate_result(result)
    if reasons:
        body = "；".join(reasons)
        raise_alert(
            "fatal", f"{date}:{chain}:chain_fail",
            f"{chain} {date} 链失败", body, source=chain,
        )
    render_status(footer_lines=[
        f"最近链自报: {chain} {date} {'❌ ' + '；'.join(reasons) if reasons else '✅ 全致命步骤通过'}",
    ])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="赛前链本地告警器 C-20261017-126")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("inspect-chain")
    p1.add_argument("--result", required=True)
    p2 = sub.add_parser("raise")
    p2.add_argument("--level", choices=("fatal", "info"), default="fatal")
    p2.add_argument("--key", required=True)
    p2.add_argument("--title", required=True)
    p2.add_argument("--body", default="")
    p2.add_argument("--source", default="cli")
    args = ap.parse_args()
    if args.cmd == "inspect-chain":
        return cmd_inspect_chain(args.result)
    raise_alert(args.level, args.key, args.title, args.body, args.source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
