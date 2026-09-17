"""
fbref WebFetch 管道 - 保存单场比赛数据
======================================
功能：解析 WebFetch 返回的比赛详情页 Markdown，保存阵容和球员统计到 odds.db

使用方法：
  # 1. 用 WebFetch 获取比赛页面（输出保存到 temp 文件）
  # 2. 运行本脚本解析并保存
  python scripts/fbref_save_match.py <markdown_file> --league 英超 --season 2024-2025

  # 批量处理目录下所有 .md 文件
  python scripts/fbref_save_match.py --batch data/fbref_md/ --league 英超 --season 2024-2025
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# 项目路径
SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"

sys.path.insert(0, str(SCRIPTS_DIR))
from fbref_markdown_parser import parse_match_from_markdown, parse_schedule_from_markdown
from fbref_anti_cloudflare import normalize_fbref_team
from fbref_schema import init_fbref_schema
from fbref_collector import (
    find_odds_match_id, save_match_mapping, save_lineups, save_player_stats,
    save_player_registry, build_odds_match_id,
)


def process_match_markdown(md_path: Path, league: str, season: str,
                           fbref_match_id: str = None, dry_run: bool = False) -> dict:
    """解析单个 Markdown 文件并保存到数据库

    Args:
        md_path: Markdown 文件路径
        league: 联赛名称
        season: 赛季
        fbref_match_id: fbref 比赛 ID（可选，从文件名推断）
        dry_run: 试运行（不写库）

    Returns:
        {success, match_info, lineups_count, players_count, errors}
    """
    if not md_path.exists():
        return {'success': False, 'error': f'文件不存在: {md_path}'}

    markdown = md_path.read_text(encoding='utf-8')

    # 从文件名推断 fbref_match_id（如 cc5b4244.md）
    if not fbref_match_id:
        name = md_path.stem
        if len(name) == 8 and all(c in '0123456789abcdef' for c in name):
            fbref_match_id = name

    # 解析 Markdown
    result = parse_match_from_markdown(markdown, league, season, fbref_match_id or '')

    match_info = result['match_info']
    lineups = result['lineups']
    substitutions = result['substitutions']
    players = result['players']

    # 检查解析结果
    home_lineup = lineups.get('home', {})
    away_lineup = lineups.get('away', {})

    if not home_lineup or not away_lineup:
        return {'success': False, 'error': '阵容解析失败', 'match_info': match_info}

    if len(home_lineup.get('starting_xi', [])) == 0:
        return {'success': False, 'error': '首发阵容为空', 'match_info': match_info}

    if not players:
        return {'success': False, 'error': '球员统计为空', 'match_info': match_info}

    if dry_run:
        print(f"  🧪 试运行: {match_info['home_team']} vs {match_info['away_team']}")
        print(f"     首发: {len(home_lineup.get('starting_xi', []))}+{len(away_lineup.get('starting_xi', []))}")
        print(f"     球员统计: {len(players)}人")
        return {
            'success': True, 'dry_run': True,
            'match_info': match_info,
            'lineups_count': len(home_lineup.get('starting_xi', [])) + len(away_lineup.get('starting_xi', [])),
            'players_count': len(players),
        }

    # 写入数据库
    conn = sqlite3.connect(str(DB_PATH))

    try:
        # 查找 odds.db 中的对应比赛
        home_team = match_info['home_team']
        away_team = match_info['away_team']
        match_date = match_info.get('date', '')

        odds_match = None
        if match_date:
            odds_match = find_odds_match_id(conn, match_date, home_team, away_team)

        if odds_match:
            odds_match_id = odds_match['match_id']
            home_team_cn = odds_match['home_team']
            away_team_cn = odds_match['away_team']
        else:
            home_team_cn = normalize_fbref_team(home_team)
            away_team_cn = normalize_fbref_team(away_team)
            odds_match_id = build_odds_match_id(match_date or 'unknown', home_team_cn, away_team_cn)

        # 构建比赛数据
        match_data = {
            'odds_match_id': odds_match_id,
            'fbref_match_id': fbref_match_id or '',
            'fbref_match_url': f"https://fbref.com/en/matches/{fbref_match_id}" if fbref_match_id else '',
            'fbref_match_slug': '',
            'league': league,
            'season': season,
            'match_date': match_date,
            'home_team': home_team,
            'away_team': away_team,
            'home_team_cn': home_team_cn,
            'away_team_cn': away_team_cn,
            'week': match_info.get('matchweek', ''),
            'score': f"{match_info.get('home_score', '')}-{match_info.get('away_score', '')}",
        }

        # 检查是否已采集
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM fbref_match_mapping WHERE fbref_match_id = ?",
            (fbref_match_id,)
        )
        if cursor.fetchone():
            print(f"  ⏭️ 已采集过，跳过: {fbref_match_id}")
            return {'success': True, 'skipped': True, 'match_info': match_info}

        # 1. 保存比赛映射
        save_match_mapping(conn, match_data)

        # 2. 保存阵容（含换人记录）
        # 将换人数据注入 lineups 字典，供 save_lineups 使用
        lineups['substitutions'] = substitutions
        save_lineups(conn, odds_match_id, fbref_match_id or '', lineups)

        # 3. 保存球员统计
        save_player_stats(conn, odds_match_id, fbref_match_id or '', players, lineups)

        # 4. 保存球员注册表
        save_player_registry(conn, players, odds_match_id)

        lineups_count = sum(
            len(lineups[s].get('starting_xi', [])) + len(lineups[s].get('substitutes', []))
            for s in ['home', 'away']
        )

        print(f"  ✅ 保存成功: {home_team} vs {away_team} "
              f"({lineups_count}阵容, {len(players)}球员统计)")

        return {
            'success': True,
            'match_info': match_info,
            'lineups_count': lineups_count,
            'players_count': len(players),
            'substitutions_count': len(substitutions),
        }

    except Exception as e:
        return {'success': False, 'error': str(e), 'match_info': match_info}
    finally:
        conn.close()


def process_batch(md_dir: Path, league: str, season: str, dry_run: bool = False) -> dict:
    """批量处理目录下所有 Markdown 文件

    文件命名：{fbref_match_id}.md（如 cc5b4244.md）
    """
    if not md_dir.exists():
        print(f"❌ 目录不存在: {md_dir}")
        return {'total': 0, 'success': 0, 'failed': 0}

    md_files = sorted(md_dir.glob('*.md')) + sorted(md_dir.glob('*.txt'))
    print(f"\n📂 批量处理: {md_dir}")
    print(f"   找到 {len(md_files)} 个文件")

    # 确保表结构存在
    if not dry_run:
        init_fbref_schema()

    stats = {'total': len(md_files), 'success': 0, 'failed': 0, 'skipped': 0, 'errors': []}

    for i, md_file in enumerate(md_files, 1):
        print(f"\n[{i}/{len(md_files)}] {md_file.name}")
        result = process_match_markdown(md_file, league, season, dry_run=dry_run)

        if result.get('success'):
            if result.get('skipped'):
                stats['skipped'] += 1
            else:
                stats['success'] += 1
        else:
            stats['failed'] += 1
            stats['errors'].append({
                'file': md_file.name,
                'error': result.get('error', '未知错误'),
            })

    # 打印统计
    print(f"\n{'='*60}")
    print(f"📊 批量处理统计")
    print(f"{'='*60}")
    print(f"  总文件: {stats['total']}")
    print(f"  成功:   {stats['success']}")
    print(f"  跳过:   {stats['skipped']}")
    print(f"  失败:   {stats['failed']}")
    if stats['errors']:
        print(f"\n  ❌ 错误列表:")
        for err in stats['errors'][:10]:
            print(f"    - {err['file']}: {err['error']}")
    print(f"{'='*60}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="fbref WebFetch 管道 - 保存比赛数据到数据库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 保存单个 Markdown 文件
  python scripts/fbref_save_match.py match_page.md --league 英超 --season 2024-2025

  # 试运行（不写库）
  python scripts/fbref_save_match.py match_page.md --league 英超 --season 2024-2025 --dry-run

  # 批量处理目录
  python scripts/fbref_save_match.py --batch data/fbref_md/ --league 英超 --season 2024-2025
        """,
    )
    parser.add_argument('md_file', nargs='?', help='Markdown 文件路径')
    parser.add_argument('--batch', metavar='DIR', help='批量处理目录')
    parser.add_argument('--league', default='英超', help='联赛名称')
    parser.add_argument('--season', default='2024-2025', help='赛季')
    parser.add_argument('--fbref-match-id', default=None, help='fbref 比赛 ID')
    parser.add_argument('--dry-run', action='store_true', help='试运行（不写库）')
    args = parser.parse_args()

    if args.batch:
        process_batch(Path(args.batch), args.league, args.season, args.dry_run)
    elif args.md_file:
        if not args.dry_run:
            init_fbref_schema()
        result = process_match_markdown(
            Path(args.md_file), args.league, args.season,
            args.fbref_match_id, args.dry_run
        )
        if not result.get('success'):
            print(f"❌ 失败: {result.get('error', '未知错误')}")
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
