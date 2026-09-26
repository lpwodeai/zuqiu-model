# -*- coding: utf-8 -*-
"""
通用竞彩网时序赔率进度查询脚本
================================
按赛季参数查询 matches 写入进度（按联赛）与四张时序表的场次数，
用于后台采集时监控实时进度（DB 每场 commit，比 stdout 日志更即时）。

用法：
  python logs/_check_sporttery_progress.py --season 2016-2017
  python logs/_check_sporttery_progress.py --season 16/17
"""
import argparse
import sqlite3
from pathlib import Path

# 动态定位项目根（logs 的上一级），避免硬编码盘符
DB = Path(__file__).resolve().parent.parent / "data" / "odds.db"
TIMING_TABLES = ["wdl_history", "handicap_history", "total_goals_history", "score_history"]


def parse_season(s: str) -> tuple:
    """解析赛季写法，返回 (起始年, 结束年, 标准赛季字符串如 2016-2017)

    支持：2016-2017 / 16/17 / 2016/2017 / 16-17
    """
    s = s.strip().replace("/", "-")
    parts = s.split("-")
    if len(parts) != 2:
        raise ValueError(f"无法解析赛季「{s}」，应为 2016-2017 或 16/17")
    years = []
    for p in parts:
        y = int(p)
        if y < 100:
            y += 2000
        years.append(y)
    start, end = years
    if end != start + 1:
        raise ValueError(f"赛季跨年不连续：{start} -> {end}")
    return start, end, f"{start}-{end}"


def main():
    ap = argparse.ArgumentParser(description="查询竞彩网时序赔率采集进度")
    ap.add_argument("--season", type=str, required=True, help="赛季，如 2016-2017 或 16/17")
    args = ap.parse_args()

    start, end, season = parse_season(args.season)
    # 采集日期范围与 SEASON_RANGES 一致：起始年 8/1 ~ 结束年 5/31
    date_start = f"{start}-08-01"
    date_end = f"{end}-05-31"

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    print(f"===== 竞彩网时序赔率进度 | 赛季 {season}（{date_start} ~ {date_end}） =====")
    print()

    # 1. matches 表（match_type = {联赛}{season}赛季）
    print("--- matches 已写入（按联赛） ---")
    total_m = 0
    for r in cur.execute(
        "SELECT match_type, COUNT(DISTINCT match_id) FROM matches "
        "WHERE match_type LIKE ? GROUP BY match_type ORDER BY match_type",
        (f"%{season}赛季%",),
    ):
        print(f"  {r[0]}: {r[1]} 场")
        total_m += r[1]
    print(f"  合计: {total_m} 场" if total_m else "  （暂无数据）")
    print()

    # 2. 四张时序表（match_id 以 YYYY-MM-DD 开头，按日期范围统计）
    print("--- 四张时序表（DISTINCT match_id） ---")
    for tbl in TIMING_TABLES:
        n = cur.execute(
            f"SELECT COUNT(DISTINCT match_id) FROM {tbl} "
            "WHERE substr(match_id, 1, 10) BETWEEN ? AND ?",
            (date_start, date_end),
        ).fetchone()[0]
        print(f"  {tbl}: {n} 场")

    conn.close()
    print()
    print("提示：total_goals/score 若无场次可能是该玩法尚未开售或已跳过；以 wdl_history 为准判断整体进度。")


if __name__ == "__main__":
    main()