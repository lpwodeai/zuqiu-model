# -*- coding: utf-8 -*-
"""C-20261016-111：键错位回退计数器汇总（只读仪表，零模型影响）。

读 reports/resolve_matches_fallback.jsonl（resolve_matches_row 回退埋点），
按天聚合 level=1/2（回退命中）与 level=3（三层均无匹配=新死角）的 distinct match_id 数，
打印近 N 日表并幂等重写 reports/fallback_counter_log.md。

用途：① P2/P4（身份层统一）紧迫性量化；② 兜底层退出验收门（P2 生效后应归零）。

用法：python scripts/fallback_counter_summary.py [--days 14]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_JSONL = PROJECT_DIR / "reports" / "resolve_matches_fallback.jsonl"
OUT_MD = PROJECT_DIR / "reports" / "fallback_counter_log.md"


def load() -> dict:
    """{day: {1:set(mid), 2:set(mid), 3:set(mid)}}"""
    per_day: dict = defaultdict(lambda: {1: set(), 2: set(), 3: set()})
    if not LOG_JSONL.exists():
        return per_day
    for line in LOG_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        day = r.get("day") or (r.get("ts") or "")[:10]
        lvl = r.get("level")
        mid = r.get("match_id")
        if day and lvl in (1, 2, 3) and mid:
            per_day[day][lvl].add(mid)
    return per_day


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    per_day = load()
    today = datetime.now().date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(args.days - 1, -1, -1)]

    out = ["# 键错位回退计数器（C-20261016-111）\n"]
    out.append(f"> 最近运行：{datetime.now():%Y-%m-%d %H:%M} | 源：reports/resolve_matches_fallback.jsonl\n")
    out.append("> L1/L2 = resolve_matches_row 回退命中（review 键错位同族）｜L3 = 三层均无匹配（新死角告警）\n")
    out.append("> 用途：P2/P4 紧迫性仪表 + 兜底层退出验收门（P2 生效后应归零）\n")
    out.append("\n| 日期 | L1 review线索 | L2 event_id线索 | 回退命中合计 | L3 新死角 |\n")
    out.append("|------|:---:|:---:|:---:|:---:|\n")

    tot = {1: 0, 2: 0, 3: 0}
    for d in days:
        v = per_day.get(d)
        l1 = len(v[1]) if v else 0
        l2 = len(v[2]) if v else 0
        l3 = len(v[3]) if v else 0
        tot[1] += l1
        tot[2] += l2
        tot[3] += l3
        if l1 or l2 or l3:
            out.append(f"| {d} | {l1} | {l2} | {l1 + l2} | {l3} |\n")
    out.append(f"| **近{args.days}日合计** | **{tot[1]}** | **{tot[2]}** | **{tot[1] + tot[2]}** | **{tot[3]}** |\n")

    if tot[3]:
        out.append(f"\n⚠️ **L3={tot[3]}：存在 matches 三层均无匹配的场次——新死角，需人工核查**\n")
    else:
        out.append("\n✅ L3=0：无三层均失效的新死角。\n")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("".join(out), encoding="utf-8")
    print("".join(out))


if __name__ == "__main__":
    main()
