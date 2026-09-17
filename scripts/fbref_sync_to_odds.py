"""
fbref 数据验证脚本
==================
功能：验证 odds.db 中 fbref 相关表的数据完整性和一致性

验证项：
  1. 比赛映射（fbref_match_mapping）完整性
  2. 阵容数据（match_lineups）完整性：每场≥22人，首发11人
  3. 球员统计（match_player_stats）覆盖率：stats_json 非空率
  4. 比分一致性：fbref 比分 ↔ odds.db actual_score
  5. match_id 关联性：fbref 数据 ↔ matches 表正确关联
  6. 球员注册表（fbref_players）完整性

使用方法：
  python scripts/fbref_sync_to_odds.py                          # 全量验证
  python scripts/fbref_sync_to_odds.py --league 英超 --season 2024-2025  # 按联赛赛季
  python scripts/fbref_sync_to_odds.py --match-id 2024-08-25_利物浦_布伦特福德  # 单场验证
"""

import sqlite3
import argparse
import json
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"


def validate_all(conn, league: str = None, season: str = None, match_id: str = None):
    """执行全量验证"""
    results = {
        'mapping': validate_match_mapping(conn, league, season, match_id),
        'lineups': validate_lineups(conn, league, season, match_id),
        'player_stats': validate_player_stats(conn, league, season, match_id),
        'score_consistency': validate_score_consistency(conn, league, season, match_id),
        'match_linkage': validate_match_linkage(conn, league, season, match_id),
        'players_registry': validate_players_registry(conn),
    }
    return results


def validate_match_mapping(conn, league=None, season=None, match_id=None):
    """验证比赛映射表"""
    cursor = conn.cursor()
    query = "SELECT * FROM fbref_match_mapping WHERE 1=1"
    params = []
    if league:
        query += " AND league = ?"
        params.append(league)
    if season:
        query += " AND season = ?"
        params.append(season)
    if match_id:
        query += " AND odds_match_id = ?"
        params.append(match_id)

    cursor.execute(query, params)
    rows = cursor.fetchall()

    total = len(rows)
    issues = []

    for row in rows:
        odds_match_id = row[1]
        fbref_match_id = row[2]
        fbref_url = row[4]
        fbref_score = row[13] if len(row) > 13 else None

        if not fbref_match_id or len(fbref_match_id) != 8:
            issues.append(f"无效 fbref_match_id: {odds_match_id}")
        if not fbref_url or 'fbref.com' not in fbref_url:
            issues.append(f"无效 fbref URL: {odds_match_id}")

    return {
        'total': total,
        'issues': issues[:20],
        'issue_count': len(issues),
    }


def validate_lineups(conn, league=None, season=None, match_id=None):
    """验证阵容数据完整性"""
    cursor = conn.cursor()

    query = """
        SELECT match_id, team,
               SUM(CASE WHEN is_starter = 1 THEN 1 ELSE 0 END) as starters,
               SUM(CASE WHEN is_starter = 0 THEN 1 ELSE 0 END) as subs,
               COUNT(*) as total
        FROM match_lineups
        WHERE 1=1
    """
    params = []
    if match_id:
        query += " AND match_id = ?"
        params.append(match_id)
    if league or season:
        query += """ AND match_id IN (
            SELECT odds_match_id FROM fbref_match_mapping WHERE 1=1
        """
        if league:
            query += " AND league = ?"
            params.append(league)
        if season:
            query += " AND season = ?"
            params.append(season)
        query += ")"

    query += " GROUP BY match_id, team"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    total_matches = len(rows) // 2  # 每场2队
    issues = []
    incomplete_matches = set()

    for row in rows:
        odds_match_id, team, starters, subs, total_players = row
        if starters != 11:
            issues.append(f"{odds_match_id} {team}: 首发{starters}人 (应为11)")
            incomplete_matches.add(odds_match_id)
        if subs < 7:
            issues.append(f"{odds_match_id} {team}: 替补{subs}人 (应≥7)")
        if total_players < 18:
            issues.append(f"{odds_match_id} {team}: 总人数{total_players} (应≥18)")
            incomplete_matches.add(odds_match_id)

    return {
        'total_team_records': len(rows),
        'total_matches': total_matches,
        'incomplete_matches': len(incomplete_matches),
        'issues': issues[:20],
        'issue_count': len(issues),
    }


def validate_player_stats(conn, league=None, season=None, match_id=None):
    """验证球员统计覆盖率"""
    cursor = conn.cursor()

    query = """
        SELECT match_id, team,
               COUNT(*) as total_players,
               SUM(CASE WHEN stats_json IS NOT NULL AND stats_json != '' THEN 1 ELSE 0 END) as has_json,
               SUM(CASE WHEN minutes_played IS NOT NULL THEN 1 ELSE 0 END) as has_minutes,
               AVG(LENGTH(stats_json)) as avg_json_length
        FROM match_player_stats
        WHERE 1=1
    """
    params = []
    if match_id:
        query += " AND match_id = ?"
        params.append(match_id)
    if league or season:
        query += """ AND match_id IN (
            SELECT odds_match_id FROM fbref_match_mapping WHERE 1=1
        """
        if league:
            query += " AND league = ?"
            params.append(league)
        if season:
            query += " AND season = ?"
            params.append(season)
        query += ")"

    query += " GROUP BY match_id, team"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    total_players = 0
    total_has_json = 0
    issues = []

    for row in rows:
        odds_match_id, team, players, has_json, has_minutes, avg_len = row
        total_players += players
        total_has_json += has_json
        if has_json < players:
            issues.append(f"{odds_match_id} {team}: {players - has_json}人缺少 stats_json")
        if avg_len and avg_len < 100:
            issues.append(f"{odds_match_id} {team}: stats_json 平均长度 {avg_len:.0f} (可能数据不全)")

    json_coverage = (total_has_json / total_players * 100) if total_players > 0 else 0

    return {
        'total_players': total_players,
        'json_coverage_pct': round(json_coverage, 1),
        'issues': issues[:20],
        'issue_count': len(issues),
    }


def validate_score_consistency(conn, league=None, season=None, match_id=None):
    """验证比分一致性"""
    cursor = conn.cursor()

    query = """
        SELECT m.odds_match_id, m.fbref_score, matches.actual_score,
               m.home_team_fbref, m.away_team_fbref
        FROM fbref_match_mapping m
        LEFT JOIN matches ON m.odds_match_id = matches.match_id
        WHERE 1=1
    """
    params = []
    if league:
        query += " AND m.league = ?"
        params.append(league)
    if season:
        query += " AND m.season = ?"
        params.append(season)
    if match_id:
        query += " AND m.odds_match_id = ?"
        params.append(match_id)

    cursor.execute(query, params)
    rows = cursor.fetchall()

    total = len(rows)
    consistent = 0
    issues = []

    for row in rows:
        odds_match_id, fbref_score, odds_score, home, away = row

        if not fbref_score or not odds_score:
            continue

        # 转换 fbref 比分格式 (2–0) → (2:0)
        fbref_normalized = fbref_score.replace('–', ':').replace('—', ':').replace('-', ':')

        if fbref_normalized == odds_score:
            consistent += 1
        else:
            issues.append(f"{odds_match_id}: fbref={fbref_score} vs odds.db={odds_score}")

    return {
        'total': total,
        'consistent': consistent,
        'consistency_pct': round(consistent / total * 100, 1) if total > 0 else 0,
        'issues': issues[:20],
        'issue_count': len(issues),
    }


def validate_match_linkage(conn, league=None, season=None, match_id=None):
    """验证 fbref 数据与 matches 表的关联性"""
    cursor = conn.cursor()

    query = """
        SELECT m.odds_match_id, COUNT(l.id) as lineup_count, COUNT(ps.id) as stats_count
        FROM fbref_match_mapping m
        LEFT JOIN match_lineups l ON m.odds_match_id = l.match_id
        LEFT JOIN match_player_stats ps ON m.odds_match_id = ps.match_id
        WHERE 1=1
    """
    params = []
    if league:
        query += " AND m.league = ?"
        params.append(league)
    if season:
        query += " AND m.season = ?"
        params.append(season)
    if match_id:
        query += " AND m.odds_match_id = ?"
        params.append(match_id)

    query += " GROUP BY m.odds_match_id"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    total = len(rows)
    has_lineups = 0
    has_stats = 0
    issues = []

    for row in rows:
        odds_match_id, lineup_count, stats_count = row
        if lineup_count > 0:
            has_lineups += 1
        else:
            issues.append(f"{odds_match_id}: 无阵容数据")
        if stats_count > 0:
            has_stats += 1
        else:
            issues.append(f"{odds_match_id}: 无球员统计")

    return {
        'total': total,
        'has_lineups': has_lineups,
        'has_stats': has_stats,
        'lineup_pct': round(has_lineups / total * 100, 1) if total > 0 else 0,
        'stats_pct': round(has_stats / total * 100, 1) if total > 0 else 0,
        'issues': issues[:20],
        'issue_count': len(issues),
    }


def validate_players_registry(conn):
    """验证球员注册表"""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM fbref_players")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT fbref_player_id) FROM fbref_players")
    unique = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM fbref_players
        WHERE primary_team_cn IS NULL OR primary_team_cn = ''
    """)
    missing_team = cursor.fetchone()[0]

    return {
        'total': total,
        'unique_ids': unique,
        'missing_team': missing_team,
        'issues': [] if missing_team == 0 else [f"{missing_team} 球员缺少球队信息"],
        'issue_count': missing_team,
    }


def print_results(results):
    """打印验证结果"""
    print(f"\n{'='*60}")
    print(f"📊 fbref 数据验证报告")
    print(f"{'='*60}")

    # 比赛映射
    r = results['mapping']
    status = '✅' if r['issue_count'] == 0 else '⚠️'
    print(f"\n{status} 比赛映射 (fbref_match_mapping)")
    print(f"   总记录: {r['total']}")
    if r['issues']:
        for issue in r['issues'][:5]:
            print(f"   ⚠️ {issue}")

    # 阵容
    r = results['lineups']
    status = '✅' if r['issue_count'] == 0 else '⚠️'
    print(f"\n{status} 阵容数据 (match_lineups)")
    print(f"   总比赛: {r['total_matches']}")
    print(f"   球队记录: {r['total_team_records']}")
    print(f"   不完整比赛: {r['incomplete_matches']}")
    if r['issues']:
        for issue in r['issues'][:5]:
            print(f"   ⚠️ {issue}")

    # 球员统计
    r = results['player_stats']
    status = '✅' if r['issue_count'] == 0 and r['json_coverage_pct'] >= 95 else '⚠️'
    print(f"\n{status} 球员统计 (match_player_stats)")
    print(f"   总球员: {r['total_players']}")
    print(f"   JSON覆盖率: {r['json_coverage_pct']}%")
    if r['issues']:
        for issue in r['issues'][:5]:
            print(f"   ⚠️ {issue}")

    # 比分一致性
    r = results['score_consistency']
    status = '✅' if r['consistency_pct'] >= 95 else '⚠️'
    print(f"\n{status} 比分一致性")
    print(f"   总比对: {r['total']}")
    print(f"   一致: {r['consistent']} ({r['consistency_pct']}%)")
    if r['issues']:
        for issue in r['issues'][:5]:
            print(f"   ⚠️ {issue}")

    # 关联性
    r = results['match_linkage']
    status = '✅' if r['lineup_pct'] >= 95 and r['stats_pct'] >= 95 else '⚠️'
    print(f"\n{status} 数据关联性")
    print(f"   总比赛: {r['total']}")
    print(f"   有阵容: {r['has_lineups']} ({r['lineup_pct']}%)")
    print(f"   有统计: {r['has_stats']} ({r['stats_pct']}%)")
    if r['issues']:
        for issue in r['issues'][:5]:
            print(f"   ⚠️ {issue}")

    # 球员注册表
    r = results['players_registry']
    status = '✅' if r['issue_count'] == 0 else '⚠️'
    print(f"\n{status} 球员注册表 (fbref_players)")
    print(f"   总球员: {r['total']}")
    print(f"   唯一ID: {r['unique_ids']}")
    if r['issues']:
        for issue in r['issues']:
            print(f"   ⚠️ {issue}")

    # 总结
    total_issues = sum(r.get('issue_count', 0) for r in results.values())
    print(f"\n{'='*60}")
    if total_issues == 0:
        print(f"✅ 全部验证通过，无问题")
    else:
        print(f"⚠️ 共发现 {total_issues} 个问题")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="fbref 数据验证脚本")
    parser.add_argument('--league', default=None, help='联赛名称')
    parser.add_argument('--season', default=None, help='赛季')
    parser.add_argument('--match-id', default=None, help='指定 match_id 验证单场')
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB_PATH))

    # 检查表是否存在
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fbref_match_mapping'")
    if not cursor.fetchone():
        print("❌ fbref 表不存在，请先运行 fbref_schema.py 初始化")
        print("   python scripts/fbref_schema.py")
        return

    results = validate_all(conn, args.league, args.season, args.match_id)
    print_results(results)
    conn.close()


if __name__ == '__main__':
    main()
