# -*- coding: utf-8 -*-
"""赛果/联赛回填缺口扫描（C-20261022-194 · 只读软模式）

盲区 C 哨兵：matches 表 actual_wdl/actual_score 未回填、league 空值——此前无任何门禁，
只能靠复盘报告"结果源"标注被动发现。本扫描每日产出一行观察行，挂 chain_watchdog 非阻断。

"已完赛"双信号（取并集，不用单一来源）：
  信号 1：post_match_review 已有该场（复盘都出了必然完赛）但 matches.actual_wdl 空
          = C 缺口的实锤形态（复盘用了 SofaScore 结果、canonical 未回填）
  信号 2：matches.match_date 距今 ≥2 天（48h 缓冲，回填滞后是常态）且 actual_wdl 空
          = 抓"完赛了但复盘都还没生成"的漏场

输出：滞后天数分桶 [0-1d / 2-3d / 4-7d / >7d]，>7d 桶突出显示；
      第二行 league 空值计数（26/27 赛季 + 全库，基线 128）。

边界（C-191 未修前）：哑扫描——只用 match_id/match_date 计数，不做任何队名对拍 join。
范围：仅 26/27 赛季（match_date >= 2026-08-01）——更早赛季本就无 actual_wdl 回填实践，
      扫进去会让 >7d 桶全是远古历史 = 又一个"狼来了"（首版实测 501 场里 490+ 是 2016-2022）。
窗口 C 验收仪器：L3a/b 回填完成后 >7d 桶应归零。

退出码：恒 0（软模式，非阻断；失败打印错误仍 exit 0）。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ODDS_DB = BASE / "data" / "odds.db"

BUCKETS = ((0, 1, "0-1d"), (2, 3, "2-3d"), (4, 7, "4-7d"), (8, 10**9, ">7d"))
SEASON_START = "2026-08-01"  # 仅 26/27 赛季：更早赛季无 actual_wdl 回填实践，扫入即误报


def _ro_conn():
    return sqlite3.connect(f"file:{ODDS_DB.as_posix()}?mode=ro", uri=True)


def collect_gaps(conn):
    """返回 [(match_id, match_date, lag_days, signals), ...]，按 lag 降序。"""
    today = date.today()
    rows = {}

    # 信号 1：复盘已出但 canonical 未回填
    for mid, mdate in conn.execute(
        "SELECT p.match_id, p.match_date FROM post_match_review p "
        "LEFT JOIN matches m ON m.match_id = p.match_id "
        "WHERE p.match_date >= ? "
        "AND (m.match_id IS NULL OR m.actual_wdl IS NULL OR TRIM(m.actual_wdl) = '')",
        (SEASON_START,),
    ):
        rows[mid] = (mdate, "pmr")

    # 信号 2：开赛 ≥48h 且 actual_wdl 空
    for mid, mdate in conn.execute(
        "SELECT match_id, match_date FROM matches "
        "WHERE match_date >= ? AND (actual_wdl IS NULL OR TRIM(actual_wdl) = '')",
        (SEASON_START,),
    ):
        if mid in rows:
            rows[mid] = (rows[mid][0], rows[mid][1] + "+date")
        else:
            rows[mid] = (mdate, "date")

    out = []
    for mid, (mdate, sig) in rows.items():
        try:
            lag = (today - date.fromisoformat(str(mdate)[:10])).days
        except (TypeError, ValueError):
            continue
        if lag < 2 and "pmr" not in sig:
            continue  # 48h 缓冲内且非复盘实锤 → 正常滞后，不计
        out.append((mid, mdate, lag, sig))
    out.sort(key=lambda r: -r[2])
    return out


def league_null_counts(conn):
    """返回 (近季 26/27 league 空值场数, 全库 league 空值场数)。"""
    season = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE (league IS NULL OR TRIM(league)='') "
        "AND match_date >= '2026-08-01'"
    ).fetchone()[0]
    total = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE league IS NULL OR TRIM(league)=''"
    ).fetchone()[0]
    return season, total


def main() -> int:
    # C-20261023-196：stdout 为 GBK 管道/控制台时打印 ⚠️ 会 UnicodeEncodeError
    # （chain_watchdog subprocess 捕获场景实发）。降级替换字符而非崩溃；编码
    # 保持环境默认，挂链时由看门狗 PYTHONIOENCODING=utf-8 保全 ⚠️ 原样输出。
    sys.stdout.reconfigure(errors="replace")
    try:
        conn = _ro_conn()
        try:
            gaps = collect_gaps(conn)
            season_null, total_null = league_null_counts(conn)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[backfill-gap] 扫描自身错误（非阻断）: {type(e).__name__}: {e}")
        return 0

    counts = {label: 0 for _, _, label in BUCKETS}
    for _, _, lag, _ in gaps:
        for lo, hi, label in BUCKETS:
            if lo <= lag <= hi:
                counts[label] += 1
                break

    line = (f"赛果回填缺口: 共 {len(gaps)} 场 "
            f"[0-1d:{counts['0-1d']} | 2-3d:{counts['2-3d']} | "
            f"4-7d:{counts['4-7d']} | >7d:{counts['>7d']}]")
    if counts[">7d"]:
        line += f"  ⚠️ >7d 桶 {counts['>7d']} 场（真断供候选）"
    print(line)
    print(f"league 空值: 26/27 赛季 {season_null} 场（全库 {total_null} 场；条目 6 基线 128）")

    if "--verbose" in sys.argv:
        for mid, mdate, lag, sig in gaps[:20]:
            print(f"  {mdate} lag={lag}d [{sig}] {mid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
