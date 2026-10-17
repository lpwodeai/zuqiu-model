# -*- coding: utf-8 -*-
"""drift_indicative 月度统计面板（C-110 落地 / C-20261016-109）

立项规格（六条，冻结）：
1. 口径同源：hit/rev/neutral 直接读 post_match_review.attribution_json 中归因引擎
   （C-20261014-085）写入的 drift_indicative，不重算、不解析 md。
2. 数据源走 DB。
3. 统计纪律：每月行带 n + Wilson 95% CI；禁单月结论；观察句仅当累计偏离 >2σ
   且同向连续 ≥2 月。
4. 零假设预注册：漂移无方向信息（H0: P(hit)=P(rev)=0.5，C-20261004-006 七网格
   平坦先验）。面板只积累证据、永不自动接入模型。
5. 交付：reports/drift_indicative_log.md 幂等重写（重算全量防重复行）+ 每月一行，
   手动月度运行（同频 wdl_bias_monitor），无报警阈值。
6. 分桶：漂移幅度（<1pp / 1-3pp / >3pp，按 drift_delta）+ 分联赛。

用途：裁决复盘报告 §六 反复出现的命题「增强时序赔率特征权重」——若反指率不低，
该命题由证据否决并永久结案。
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DB = SCRIPTS.parent / "data" / "odds.db"
LOG = SCRIPTS.parent / "reports" / "drift_indicative_log.md"


def wilson_ci(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    center = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, center - half), min(1.0, center + half))


def z_stat(k: int, n: int, p0: float = 0.5) -> float:
    if n == 0:
        return 0.0
    return (k / n - p0) / math.sqrt(p0 * (1 - p0) / n)


def bucket(delta):
    if delta is None:
        return "未知"
    d = abs(float(delta))
    if d < 0.01:
        return "<1pp"
    if d <= 0.03:
        return "1-3pp"
    return ">3pp"


def main() -> int:
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT match_date, league, attribution_json FROM post_match_review "
        "WHERE attribution_json LIKE '%drift_indicative%' ORDER BY match_date").fetchall()
    conn.close()

    # 聚合：月 × (hit/rev/neutral)；分桶；分联赛
    monthly = defaultdict(lambda: [0, 0, 0])       # month -> [hit, rev, neutral]
    by_bucket = defaultdict(lambda: [0, 0, 0])
    by_league = defaultdict(lambda: [0, 0, 0])
    tot = [0, 0, 0]
    for mdate, league, aj in rows:
        try:
            d = json.loads(aj)
        except Exception:
            continue
        month = (mdate or "")[:7]
        for a in d.get("attributions", []):
            ev = a.get("evidence") or {}
            ind = ev.get("drift_indicative")
            if ind not in ("hit", "rev", "neutral"):
                continue
            i = {"hit": 0, "rev": 1, "neutral": 2}[ind]
            monthly[month][i] += 1
            by_bucket[bucket(ev.get("drift_delta"))][i] += 1
            by_league[league or "未知"][i] += 1
            tot[i] += 1

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [
        "# drift_indicative 月度面板（C-110）",
        "",
        f"> 最近运行：{now} | 数据源：post_match_review.attribution_json（归因引擎 C-20261014-085 写入）",
        "> 零假设 H0：漂移无方向信息，P(hit)=P(rev)=0.5（C-20261004-006 七网格平坦先验）。",
        "> 纪律：禁单月结论；观察句仅当累计 |z|>2 且同向连续 ≥2 月。本面板只积累证据，永不自动接入模型。",
        "",
        "## 月度明细（hit=漂移推向实际赢家 / rev=推向输家 / neutral=无明确方向）",
        "",
        "| 月份 | n(方向性) | hit | rev | neutral | hit率 [95% CI] | z |",
        "|------|:--------:|:---:|:---:|:-------:|:--------------:|:--:|",
    ]
    cum_k = cum_n = 0
    consec_dir = 0   # 同向连续月数（>0 偏 hit，<0 偏 rev）
    prev_sign = 0
    months = sorted(monthly)
    for m in months:
        h, r, nu = monthly[m]
        n_dir = h + r
        cum_k += h
        cum_n += n_dir
        lo, hi = wilson_ci(h, n_dir)
        z = z_stat(h, n_dir)
        sign = 1 if (n_dir and h / n_dir > 0.5) else (-1 if n_dir and h / n_dir < 0.5 else 0)
        consec_dir = consec_dir + 1 if sign == prev_sign and sign != 0 else (1 if sign != 0 else 0)
        prev_sign = sign if sign != 0 else prev_sign
        L.append(f"| {m} | {n_dir} | {h} | {r} | {nu} | "
                 f"{(h/n_dir*100 if n_dir else 0):.1f}% [{lo*100:.1f},{hi*100:.1f}] | {z:+.2f} |")

    # 累计行
    lo, hi = wilson_ci(cum_k, cum_n)
    zc = z_stat(cum_k, cum_n)
    L += [
        f"| **累计** | **{cum_n}** | **{cum_k}** | **{cum_n-cum_k}** | — | "
        f"**{(cum_k/cum_n*100 if cum_n else 0):.1f}% [{lo*100:.1f},{hi*100:.1f}]** | **{zc:+.2f}** |",
        "",
        "## 漂移幅度分桶（累计）",
        "",
        "| 幅度 | n(方向性) | hit | rev | hit率 |",
        "|------|:--------:|:---:|:---:|:-----:|",
    ]
    for b in ("<1pp", "1-3pp", ">3pp", "未知"):
        h, r, nu = by_bucket.get(b, [0, 0, 0])
        n_dir = h + r
        L.append(f"| {b} | {n_dir} | {h} | {r} | {(h/n_dir*100 if n_dir else 0):.1f}% |")
    L += ["", "## 分联赛（累计）", "", "| 联赛 | n(方向性) | hit | rev | hit率 |", "|------|:--------:|:---:|:---:|:-----:|"]
    for lg in sorted(by_league):
        h, r, nu = by_league[lg]
        n_dir = h + r
        L.append(f"| {lg} | {n_dir} | {h} | {r} | {(h/n_dir*100 if n_dir else 0):.1f}% |")

    # 观察句（纪律：累计 |z|>2 且同向连续 ≥2 月）
    L += ["", "## 观察（满足统计纪律才写）", ""]
    if cum_n == 0:
        L.append("- 暂无 drift 标记样本。")
    elif abs(zc) > 2 and abs(consec_dir) >= 2:
        direction = "顺指（hit 显著多于 rev）" if zc > 0 else "**反指（rev 显著多于 hit）**"
        L.append(f"- 累计证据达到观察门槛：{direction}，n={cum_n}，z={zc:+.2f}，同向连续 {abs(consec_dir)} 月。")
        if zc < 0:
            L.append("- 裁决建议：命题「增强时序赔率特征权重」被证据否决——drift 信号方向不稳定，禁止接入模型；命题可永久结案。")
    else:
        L.append(f"- 未达观察门槛（累计 |z|={abs(zc):.2f}≤2 或同向连续 {abs(consec_dir)}<2 月）：证据不足，继续积累，不做结论。")
    L.append("")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(L), encoding="utf-8")
    print(f"面板已写入 {LOG}（方向性样本 n={cum_n}，月份 {len(months)} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
