"""
sporttery 数据同步到 odds.db
=============================
将 odds_timing.db 中采集的 sporttery 数据同步到 odds.db，
匹配现有的表结构和数据格式。

使用方法：
  python scripts/sporttery_sync_to_odds.py
  python scripts/sporttery_sync_to_odds.py --dry-run  # 仅预览不写入
  python scripts/sporttery_sync_to_odds.py --league 英超  # 仅同步指定联赛
"""

import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ODDS_DB = DATA_DIR / "odds.db"
TIMING_DB = DATA_DIR / "odds_timing.db"

# === 联赛名归一化：赛季后缀 ===
# 修复：原 league_short 仅写入 "英超" 等短名，缺少赛季后缀，
# 导致 matches 表 match_type 格式不统一（部分有赛季，部分无）。
# 现统一追加赛季后缀，格式为 "英超2023-2024赛季"。
def get_season(date_str):
    """根据日期返回赛季标签 (YYYY-YYYY+1)"""
    if not date_str:
        return "未知赛季"
    date_str = str(date_str).strip()
    year = int(date_str[:4])
    if len(date_str) >= 7:
        month = int(date_str[5:7])
    else:
        month = 1
    if month >= 8:
        return f"{year}-{year+1}"
    else:
        return f"{year-1}-{year}"

# 球队名映射（sporttery.cn 名称 → odds.db 标准名称）
# 需要根据实际数据补充
TEAM_NAME_MAP = {
    # 英超
    "曼城": "曼彻斯特城", "曼彻斯特城": "曼彻斯特城",
    "曼联": "曼彻斯特联", "曼彻斯特联": "曼彻斯特联",
    "切尔西": "切尔西",
    "阿森纳": "阿森纳",
    "利物浦": "利物浦",
    "热刺": "托特纳姆热刺", "托特纳姆热刺": "托特纳姆热刺",
    "纽卡斯尔": "纽卡斯尔联", "纽卡斯尔联": "纽卡斯尔联",
    "布莱顿": "布赖顿", "布赖顿": "布赖顿",
    "维拉": "阿斯顿维拉", "阿斯顿维拉": "阿斯顿维拉",
    "西汉姆": "西汉姆联", "西汉姆联": "西汉姆联",
    "水晶宫": "水晶宫",
    "布伦特": "布伦特福德", "布伦特福德": "布伦特福德",
    "狼队": "狼队",
    "伯恩茅斯": "伯恩茅斯",
    "富勒姆": "富勒姆",
    "埃弗顿": "埃弗顿",
    "诺丁汉": "诺丁汉森林", "诺丁汉森林": "诺丁汉森林",
    "伊普斯": "伊普斯维奇", "伊普斯维奇": "伊普斯维奇",
    "南安普敦": "南安普敦", "南安普顿": "南安普敦",
    "莱切斯特": "莱斯特城", "莱斯特城": "莱斯特城",
    "利兹联": "利兹联",
    "伯恩利": "伯恩利",
    "谢菲联": "谢菲尔德联", "谢菲尔德联": "谢菲尔德联",
    "卢顿": "卢顿",

    # 意甲
    "国际米兰": "国际米兰", "国米": "国际米兰",
    "AC米兰": "AC米兰",
    "尤文图斯": "尤文图斯", "尤文": "尤文图斯",
    "那不勒斯": "那不勒斯",
    "罗马": "罗马",
    "拉齐奥": "拉齐奥",
    "亚特兰大": "亚特兰大",
    "佛罗伦萨": "佛罗伦萨",
    "博洛尼亚": "博洛尼亚",
    "都灵": "都灵",

    # 西甲
    "皇家马德里": "皇家马德里", "皇马": "皇家马德里",
    "巴塞罗那": "巴塞罗那", "巴萨": "巴塞罗那",
    "马德里竞技": "马德里竞技", "马竞": "马德里竞技",
    "塞维利亚": "塞维利亚",
    "比利亚雷": "比利亚雷亚尔", "比利亚雷亚尔": "比利亚雷亚尔",

    # 德甲
    "拜仁": "拜仁慕尼黑", "拜仁慕尼黑": "拜仁慕尼黑",
    "多特蒙德": "多特蒙德",
    "勒沃库森": "勒沃库森",
    "莱比锡": "莱比锡红牛", "莱比锡红牛": "莱比锡红牛",

    # 法甲
    "巴黎圣曼": "巴黎圣日耳曼", "巴黎圣日耳曼": "巴黎圣日耳曼",
    "马赛": "马赛",
    "里昂": "里昂",
    "摩纳哥": "摩纳哥",
}


def normalize_team_name(name: str) -> str:
    """标准化球队名称"""
    name = name.strip()
    return TEAM_NAME_MAP.get(name, name)


def get_league_short_name(league_full: str) -> str:
    """获取联赛简称"""
    mapping = {
        "英格兰超级联赛": "英超",
        "意大利甲级联赛": "意甲",
        "西班牙甲级联赛": "西甲",
        "德国甲级联赛": "德甲",
        "法国甲级联赛": "法甲",
    }
    return mapping.get(league_full, league_full)


def sync_matches(odds_conn, timing_conn, league_filter: str = None, dry_run: bool = False):
    """同步比赛数据"""
    cursor_t = timing_conn.cursor()
    cursor_o = odds_conn.cursor()

    # 查询待同步的比赛
    query = "SELECT match_id, home_team, away_team, match_date, league, handicap, actual_wdl, actual_score, actual_total_goals, half_score FROM matches WHERE 1=1"
    params = []
    if league_filter:
        query += " AND league = ?"
        params.append(league_filter)

    cursor_t.execute(query, params)
    timing_matches = cursor_t.fetchall()

    print(f"📋 待同步比赛: {len(timing_matches)} 场")

    new_count = 0
    skip_count = 0
    error_count = 0

    for row in timing_matches:
        match_id, home_team, away_team, match_date, league, handicap, actual_wdl, actual_score, actual_total_goals, half_score = row

        # 标准化球队名
        home_std = normalize_team_name(home_team)
        away_std = normalize_team_name(away_team)
        league_short = get_league_short_name(league)
        # 修复：追加赛季后缀，统一 match_type 格式为 "英超2023-2024赛季"
        match_type = f"{league_short}{get_season(match_date)}赛季"

        # 检查是否已存在
        cursor_o.execute("SELECT id FROM matches WHERE match_id = ?", (match_id,))
        if cursor_o.fetchone():
            skip_count += 1
            continue

        if dry_run:
            print(f"  [DRY-RUN] {match_date} {home_std} vs {away_std} ({league_short})")
            new_count += 1
            continue

        try:
            cursor_o.execute("""
                INSERT INTO matches (match_id, home_team, away_team, match_date, match_type, 
                                     handicap, handicap_source, actual_wdl, actual_score, actual_total_goals)
                VALUES (?, ?, ?, ?, ?, ?, 'sporttery', ?, ?, ?)
            """, (match_id, home_std, away_std, match_date, match_type, 
                  handicap, actual_wdl, actual_score, actual_total_goals))
            new_count += 1
        except Exception as e:
            print(f"  ❌ 插入失败: {match_id} {home_team} vs {away_team}: {e}")
            error_count += 1

    if not dry_run:
        odds_conn.commit()

    print(f"✅ 比赛同步: 新增 {new_count}, 跳过 {skip_count}, 失败 {error_count}")
    return new_count


def sync_wdl_timing(odds_conn, timing_conn, league_filter: str = None, dry_run: bool = False):
    """同步WDL时序赔率"""
    cursor_t = timing_conn.cursor()
    cursor_o = odds_conn.cursor()

    # 获取需要同步的比赛ID
    if league_filter:
        cursor_t.execute("SELECT match_id FROM matches WHERE league = ?", (league_filter,))
    else:
        cursor_t.execute("SELECT match_id FROM matches")
    match_ids = [row[0] for row in cursor_t.fetchall()]

    new_count = 0
    skip_count = 0

    for match_id in match_ids:
        cursor_t.execute(
            "SELECT timestamp, win_a, draw, win_b FROM wdl_timing WHERE match_id = ? ORDER BY timestamp",
            (match_id,)
        )
        timing_rows = cursor_t.fetchall()

        for ts, win_a, draw, win_b in timing_rows:
            # 检查是否已存在
            cursor_o.execute(
                "SELECT id FROM wdl_history WHERE match_id = ? AND timestamp = ?",
                (match_id, ts)
            )
            if cursor_o.fetchone():
                skip_count += 1
                continue

            if dry_run:
                new_count += 1
                continue

            try:
                cursor_o.execute(
                    "INSERT INTO wdl_history (match_id, timestamp, win_a, draw, win_b) VALUES (?, ?, ?, ?, ?)",
                    (match_id, ts, win_a, draw, win_b)
                )
                new_count += 1
            except Exception as e:
                pass  # 忽略重复键错误

    if not dry_run:
        odds_conn.commit()

    print(f"✅ WDL时序: 新增 {new_count}, 跳过 {skip_count}")
    return new_count


def sync_handicap_timing(odds_conn, timing_conn, league_filter: str = None, dry_run: bool = False):
    """同步让球时序赔率"""
    cursor_t = timing_conn.cursor()
    cursor_o = odds_conn.cursor()

    if league_filter:
        cursor_t.execute("SELECT match_id FROM matches WHERE league = ?", (league_filter,))
    else:
        cursor_t.execute("SELECT match_id FROM matches")
    match_ids = [row[0] for row in cursor_t.fetchall()]

    new_count = 0
    skip_count = 0

    for match_id in match_ids:
        cursor_t.execute(
            "SELECT timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_timing WHERE match_id = ? ORDER BY timestamp",
            (match_id,)
        )
        timing_rows = cursor_t.fetchall()

        for ts, hcp_win, hcp_draw, hcp_lose in timing_rows:
            cursor_o.execute(
                "SELECT id FROM handicap_history WHERE match_id = ? AND timestamp = ?",
                (match_id, ts)
            )
            if cursor_o.fetchone():
                skip_count += 1
                continue

            if dry_run:
                new_count += 1
                continue

            try:
                cursor_o.execute(
                    "INSERT INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose) VALUES (?, ?, ?, ?, ?)",
                    (match_id, ts, hcp_win, hcp_draw, hcp_lose)
                )
                new_count += 1
            except Exception:
                pass

    if not dry_run:
        odds_conn.commit()

    print(f"✅ 让球时序: 新增 {new_count}, 跳过 {skip_count}")
    return new_count


def sync_total_goals_timing(odds_conn, timing_conn, league_filter: str = None, dry_run: bool = False):
    """同步总进球时序赔率"""
    cursor_t = timing_conn.cursor()
    cursor_o = odds_conn.cursor()

    if league_filter:
        cursor_t.execute("SELECT match_id FROM matches WHERE league = ?", (league_filter,))
    else:
        cursor_t.execute("SELECT match_id FROM matches")
    match_ids = [row[0] for row in cursor_t.fetchall()]

    new_count = 0
    skip_count = 0

    for match_id in match_ids:
        cursor_t.execute(
            """SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus 
               FROM total_goals_timing WHERE match_id = ? ORDER BY timestamp""",
            (match_id,)
        )
        timing_rows = cursor_t.fetchall()

        for row in timing_rows:
            ts = row[0]
            cursor_o.execute(
                "SELECT id FROM total_goals_history WHERE match_id = ? AND timestamp = ?",
                (match_id, ts)
            )
            if cursor_o.fetchone():
                skip_count += 1
                continue

            if dry_run:
                new_count += 1
                continue

            try:
                cursor_o.execute(
                    """INSERT INTO total_goals_history 
                       (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (match_id, ts, row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8])
                )
                new_count += 1
            except Exception:
                pass

    if not dry_run:
        odds_conn.commit()

    print(f"✅ 总进球时序: 新增 {new_count}, 跳过 {skip_count}")
    return new_count


def print_stats(odds_conn):
    """打印统计信息"""
    cursor = odds_conn.cursor()

    cursor.execute("SELECT match_type, COUNT(*) FROM matches GROUP BY match_type ORDER BY COUNT(*) DESC")
    leagues = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM matches")
    total_matches = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM wdl_history")
    total_wdl = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM handicap_history")
    total_hcp = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM total_goals_history")
    total_tg = cursor.fetchone()[0]

    print(f"\n{'='*60}")
    print(f"📊 odds.db 当前状态")
    print(f"{'='*60}")
    print(f"总比赛数: {total_matches}")
    print(f"WDL时序数据点: {total_wdl}")
    print(f"让球时序数据点: {total_hcp}")
    print(f"总进球时序数据点: {total_tg}")
    print(f"\n联赛分布:")
    for league, count in leagues:
        print(f"  {league}: {count} 场")


def main():
    parser = argparse.ArgumentParser(description="sporttery数据同步到odds.db")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    parser.add_argument("--league", type=str, default=None,
                       choices=["英超", "意甲", "西甲", "德甲", "法甲"],
                       help="仅同步指定联赛")
    parser.add_argument("--stats-only", action="store_true", help="仅显示统计信息")
    args = parser.parse_args()

    if not ODDS_DB.exists():
        print(f"❌ odds.db 不存在: {ODDS_DB}")
        return

    if not TIMING_DB.exists():
        print(f"❌ odds_timing.db 不存在: {TIMING_DB}")
        print("   请先运行 sporttery_collector.py 采集数据")
        return

    odds_conn = sqlite3.connect(str(ODDS_DB))
    timing_conn = sqlite3.connect(str(TIMING_DB))

    try:
        if args.stats_only:
            print_stats(odds_conn)
            return

        league_filter = None
        if args.league:
            league_full = {
                "英超": "英格兰超级联赛",
                "意甲": "意大利甲级联赛",
                "西甲": "西班牙甲级联赛",
                "德甲": "德国甲级联赛",
                "法甲": "法国甲级联赛",
            }[args.league]
            league_filter = league_full

        mode = "[DRY-RUN] " if args.dry_run else ""
        print(f"{mode}开始同步数据...")

        sync_matches(odds_conn, timing_conn, league_filter, args.dry_run)
        sync_wdl_timing(odds_conn, timing_conn, league_filter, args.dry_run)
        sync_handicap_timing(odds_conn, timing_conn, league_filter, args.dry_run)
        sync_total_goals_timing(odds_conn, timing_conn, league_filter, args.dry_run)

        print_stats(odds_conn)

    finally:
        odds_conn.close()
        timing_conn.close()


if __name__ == "__main__":
    main()