"""
fbref.com 五大联赛阵容与球员数据采集器
========================================
功能：从 fbref.com 采集单场比赛的阵容数据和球员详细统计（280+ 指标）
数据源：https://fbref.com/en/
写入目标：data/odds.db（match_lineups / match_player_stats / fbref_match_mapping / fbref_players 表）

采集流程：
  1. 获取赛程页 → 解析比赛列表（fbref_match_list_parser）
  2. 按日期过滤 → 确定 odds.db match_id 关联
  3. 逐场获取比赛详情页（fbref_anti_cloudflare.FbrefFetcher）
  4. 解析阵容（fbref_lineup_parser）+ 球员统计（fbref_stats_parser）
  5. 写入 odds.db（INSERT OR REPLACE / INSERT OR IGNORE）
  6. 记录采集映射和球员注册信息

反爬策略：
  - HTTP 优先 + Playwright 兜底（非 headless 通过 Cloudflare 验证）
  - 6 秒/场延迟 + 20 场批次 30 秒冷却 + 日 200 场上限

使用方法：
  # 试点：英超 2024-2025 赛季 8 月
  python scripts/fbref_collector.py --league 英超 --season 2024-2025 --start-date 2024-08-01 --end-date 2024-08-31

  # 指定使用 Playwright（非 headless）
  python scripts/fbref_collector.py --league 英超 --season 2024-2025 --use-playwright --no-headless

  # 全赛季
  python scripts/fbref_collector.py --league all --season 2024-2025
"""

import asyncio
import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# 添加脚本目录到 path
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

# 项目路径
PROJECT_ROOT = SCRIPTS_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "odds.db"

# 导入解析器和采集器模块
from fbref_match_list_parser import (
    parse_match_list, filter_completed_matches, filter_matches_by_date,
    build_schedule_url, FBREF_LEAGUE_IDS, FBREF_LEAGUE_SLUGS,
)
from fbref_lineup_parser import parse_lineups, enrich_positions_from_stats
from fbref_stats_parser import parse_player_stats, get_stats_summary
from fbref_anti_cloudflare import (
    FbrefFetcher, normalize_fbref_team,
    REQUEST_DELAY, BATCH_DELAY, BATCH_SIZE, DAILY_LIMIT,
)
from fbref_schema import init_fbref_schema

# ============================================================
# 数据库写入
# ============================================================

def build_odds_match_id(match_date: str, home_team_cn: str, away_team_cn: str) -> str:
    """构建 odds.db 格式的 match_id: {date}_{home}_{away}"""
    return f"{match_date}_{home_team_cn}_{away_team_cn}"


def find_odds_match_id(conn: sqlite3.Connection, match_date: str,
                       home_team_fbref: str, away_team_fbref: str) -> Optional[dict]:
    """在 odds.db 中查找对应的比赛，返回 match_id 和元信息

    尝试多种匹配策略：
    1. 精确日期 + 中文队名匹配
    2. 日期 + 模糊队名匹配
    3. 仅日期匹配（同日唯一比赛）

    Returns:
        {match_id, home_team, away_team, match_date, match_type, actual_score} 或 None
    """
    cursor = conn.cursor()

    # 将 fbref 英文队名转中文
    home_cn = normalize_fbref_team(home_team_fbref)
    away_cn = normalize_fbref_team(away_team_fbref)

    # 策略1: 精确匹配 date + home_team + away_team
    cursor.execute("""
        SELECT match_id, home_team, away_team, match_date, match_type, actual_score
        FROM matches
        WHERE match_date = ? AND (home_team = ? OR home_team = ?)
          AND (away_team = ? OR away_team = ?)
    """, (match_date, home_cn, home_team_fbref, away_cn, away_team_fbref))
    row = cursor.fetchone()
    if row:
        return _row_to_match_dict(row)

    # 策略2: 模糊匹配（LIKE %team%）
    cursor.execute("""
        SELECT match_id, home_team, away_team, match_date, match_type, actual_score
        FROM matches
        WHERE match_date = ? AND home_team LIKE ? AND away_team LIKE ?
    """, (match_date, f"%{home_cn[:2]}%", f"%{away_cn[:2]}%"))
    row = cursor.fetchone()
    if row:
        return _row_to_match_dict(row)

    # 策略3: 尝试更多队名变体
    # odds.db 中可能有不同中文翻译，尝试用日期+部分队名匹配
    cursor.execute("""
        SELECT match_id, home_team, away_team, match_date, match_type, actual_score
        FROM matches
        WHERE match_date = ?
        ORDER BY match_id
    """, (match_date,))
    rows = cursor.fetchall()
    for row in rows:
        db_home = row[1] or ''
        db_away = row[2] or ''
        # 检查是否有部分匹配
        if (home_cn and home_cn[:2] in db_home) or (away_cn and away_cn[:2] in db_away):
            return _row_to_match_dict(row)

    # 策略4: 如果该日期只有一场比赛，直接使用
    if len(rows) == 1:
        return _row_to_match_dict(rows[0])

    return None


def _row_to_match_dict(row) -> dict:
    return {
        'match_id': row[0],
        'home_team': row[1],
        'away_team': row[2],
        'match_date': row[3],
        'match_type': row[4],
        'actual_score': row[5],
    }


def save_match_mapping(conn: sqlite3.Connection, match_data: dict):
    """保存 fbref → odds.db 比赛映射"""
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO fbref_match_mapping
        (odds_match_id, fbref_match_id, fbref_match_url, fbref_match_slug,
         league, season, match_date,
         home_team_fbref, away_team_fbref, home_team_cn, away_team_cn,
         fbref_week, fbref_score, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        match_data['odds_match_id'],
        match_data['fbref_match_id'],
        match_data['fbref_match_url'],
        match_data.get('fbref_match_slug', ''),
        match_data['league'],
        match_data['season'],
        match_data['match_date'],
        match_data['home_team'],
        match_data['away_team'],
        match_data.get('home_team_cn', ''),
        match_data.get('away_team_cn', ''),
        match_data.get('week'),
        match_data.get('score', ''),
    ))
    conn.commit()


def save_lineups(conn: sqlite3.Connection, odds_match_id: str, fbref_match_id: str,
                 lineups: dict):
    """保存阵容数据到 match_lineups 表"""
    cursor = conn.cursor()

    for side in ['home', 'away']:
        lineup = lineups.get(side, {})
        team_fbref = lineup.get('team', '')
        team_cn = normalize_fbref_team(team_fbref)
        formation = lineup.get('formation')

        # 首发球员
        for player in lineup.get('starting_xi', []):
            _insert_lineup_row(cursor, odds_match_id, fbref_match_id,
                               team_cn, team_fbref, formation, player, is_starter=1)

        # 替补球员
        for player in lineup.get('substitutes', []):
            _insert_lineup_row(cursor, odds_match_id, fbref_match_id,
                               team_cn, team_fbref, formation, player, is_starter=0)

    # 保存换人记录（更新已插入的阵容行）
    for sub in lineups.get('substitutions', []):
        team_fbref = sub.get('team', '')
        team_cn = normalize_fbref_team(team_fbref)

        # 更新换入球员的 sub_in_time 和 sub_in_for
        if sub.get('player_in'):
            cursor.execute("""
                UPDATE match_lineups
                SET sub_in_time = ?, sub_in_for = ?
                WHERE match_id = ? AND team = ? AND player_name = ?
            """, (
                sub.get('minute', ''),
                sub.get('player_out', ''),
                odds_match_id, team_cn, sub['player_in'],
            ))

        # 更新换出球员的 sub_out_time 和 sub_out_for
        if sub.get('player_out'):
            cursor.execute("""
                UPDATE match_lineups
                SET sub_out_time = ?, sub_out_for = ?
                WHERE match_id = ? AND team = ? AND player_name = ?
            """, (
                sub.get('minute', ''),
                sub.get('player_in', ''),
                odds_match_id, team_cn, sub['player_out'],
            ))

    conn.commit()


def _insert_lineup_row(cursor, match_id, fbref_match_id, team_cn, team_fbref,
                       formation, player, is_starter):
    """插入单个阵容行"""
    player_name = player.get('player_name', '')
    player_id = player.get('fbref_player_id')
    jersey = player.get('jersey_number')
    position = player.get('position')

    if not player_name:
        return

    cursor.execute("""
        INSERT OR REPLACE INTO match_lineups
        (match_id, fbref_match_id, team, team_fbref, formation,
         player_name, fbref_player_id, jersey_number, position,
         is_starter, collected_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        match_id, fbref_match_id, team_cn, team_fbref, formation,
        player_name, player_id, jersey, position, is_starter,
    ))


# match_player_stats 表的列定义（按顺序）
PLAYER_STATS_COLUMNS = [
    # 基础信息 (10)
    'match_id', 'fbref_match_id', 'team', 'team_fbref',
    'player_name', 'fbref_player_id', 'jersey_number', 'position',
    'is_starter', 'minutes_played',
    # Summary 表 (15)
    'goals', 'assists', 'penalties_made', 'penalties_attempted',
    'shots', 'shots_on_target', 'yellow_cards', 'red_cards',
    'fouls_committed', 'fouls_drawn', 'offsides', 'crosses',
    'tackles_won', 'interceptions', 'own_goals',
    # Passing 表 (16)
    'passes_completed', 'passes_attempted', 'pass_completion_pct',
    'total_distance_passes', 'progressive_distance_passes',
    'short_passes_completed', 'short_passes_attempted',
    'medium_passes_completed', 'medium_passes_attempted',
    'long_passes_completed', 'long_passes_attempted',
    'key_passes', 'passes_into_final_third', 'passes_into_penalty_area',
    'crosses_into_penalty_area', 'progressive_passes',
    # Defense 表 (12)
    'tackles', 'tackles_won_def', 'tackles_in_def_third',
    'tackles_in_mid_third', 'tackles_in_att_third',
    'dribblers_tackled', 'dribblers_challenged',
    'blocks', 'blocked_shots', 'blocked_passes',
    'clearances', 'errors_leading_to_shot',
    # Possession 表 (19)
    'touches', 'touches_def_pen_area', 'touches_def_third',
    'touches_mid_third', 'touches_att_third', 'touches_att_pen_area',
    'dribbles_completed_pos', 'dribbles_attempted_pos',
    'successful_dribble_pct', 'players_beaten',
    'carries', 'carry_distance', 'progressive_carries',
    'carries_into_final_third', 'carries_into_penalty_area',
    'miscontrols', 'dispossessed',
    'passes_received', 'progressive_passes_received',
    # Misc 表 (7)
    'corner_kicks', 'penalties_won', 'penalties_conceded',
    'ball_recoveries', 'aerials_won', 'aerials_lost', 'aerial_win_pct',
    # Keeper 表 (5)
    'gk_shots_on_target_against', 'gk_goals_against', 'gk_saves',
    'gk_save_pct', 'gk_psa',
    # Advanced (5)
    'xg', 'xg_npxg', 'xa', 'sca', 'gca',
    # JSON + 元数据 (4)
    'stats_json', 'stats_source', 'quality_flag', 'collected_at',
]
# 总列数: 10+15+16+12+19+7+5+5+3 = 92


def save_player_stats(conn: sqlite3.Connection, odds_match_id: str, fbref_match_id: str,
                      players: list, lineups: dict):
    """保存球员统计数据到 match_player_stats 表"""
    cursor = conn.cursor()

    # 构建球员→分钟数映射（从阵容数据获取）
    player_minutes = {}
    for side in ['home', 'away']:
        lineup = lineups.get(side, {})
        team_fbref = lineup.get('team', '')
        team_cn = normalize_fbref_team(team_fbref)

        for group in ['starting_xi', 'substitutes']:
            for p in lineup.get(group, []):
                pname = p.get('player_name', '')
                if pname:
                    key = (team_cn, pname)
                    player_minutes[key] = {
                        'team': team_cn,
                        'team_fbref': team_fbref,
                        'jersey': p.get('jersey_number'),
                        'position': p.get('position'),
                        'is_starter': 1 if group == 'starting_xi' else 0,
                    }

    # 构建 SQL（列名和占位符由列列表自动生成）
    col_list = ', '.join(PLAYER_STATS_COLUMNS)
    # stats_source 和 collected_at 用字面值，其余用 ?
    placeholders = []
    for col in PLAYER_STATS_COLUMNS:
        if col == 'stats_source':
            placeholders.append("'fbref'")
        elif col == 'collected_at':
            placeholders.append("datetime('now')")
        else:
            placeholders.append('?')
    ph_str = ', '.join(placeholders)

    sql = f"""INSERT OR REPLACE INTO match_player_stats ({col_list}) VALUES ({ph_str})"""

    for player in players:
        team_fbref = player.get('team_fbref', '') or player.get('team', '')
        team_cn = normalize_fbref_team(player.get('team', '')) or team_fbref
        player_name = player.get('player_name', '')
        player_id = player.get('fbref_player_id')

        if not player_name:
            continue

        # 从阵容映射获取额外信息
        key = (team_cn, player_name)
        lineup_info = player_minutes.get(key, {})

        # 确定首发/替补
        is_starter = player.get('is_starter', lineup_info.get('is_starter', 1))

        # 按列顺序构建值列表
        values = [
            # 基础信息
            odds_match_id, fbref_match_id, team_cn, team_fbref,
            player_name, player_id,
            player.get('jersey_number', lineup_info.get('jersey')),
            player.get('position', lineup_info.get('position')),
            is_starter, player.get('minutes_played'),
            # Summary
            player.get('goals'), player.get('assists'),
            player.get('penalties_made'), player.get('penalties_attempted'),
            player.get('shots'), player.get('shots_on_target'),
            player.get('yellow_cards'), player.get('red_cards'),
            player.get('fouls_committed'), player.get('fouls_drawn'),
            player.get('offsides'), player.get('crosses'),
            player.get('tackles_won'), player.get('interceptions'),
            player.get('own_goals'),
            # Passing
            player.get('passes_completed'), player.get('passes_attempted'),
            player.get('pass_completion_pct'),
            player.get('total_distance_passes'), player.get('progressive_distance_passes'),
            player.get('short_passes_completed'), player.get('short_passes_attempted'),
            player.get('medium_passes_completed'), player.get('medium_passes_attempted'),
            player.get('long_passes_completed'), player.get('long_passes_attempted'),
            player.get('key_passes'), player.get('passes_into_final_third'),
            player.get('passes_into_penalty_area'), player.get('crosses_into_penalty_area'),
            player.get('progressive_passes'),
            # Defense
            player.get('tackles'), player.get('tackles_won_def'),
            player.get('tackles_in_def_third'), player.get('tackles_in_mid_third'),
            player.get('tackles_in_att_third'),
            player.get('dribblers_tackled'), player.get('dribblers_challenged'),
            player.get('blocks'), player.get('blocked_shots'), player.get('blocked_passes'),
            player.get('clearances'), player.get('errors_leading_to_shot'),
            # Possession
            player.get('touches'), player.get('touches_def_pen_area'),
            player.get('touches_def_third'), player.get('touches_mid_third'),
            player.get('touches_att_third'), player.get('touches_att_pen_area'),
            player.get('dribbles_completed_pos'), player.get('dribbles_attempted_pos'),
            player.get('successful_dribble_pct'), player.get('players_beaten'),
            player.get('carries'), player.get('carry_distance'),
            player.get('progressive_carries'),
            player.get('carries_into_final_third'), player.get('carries_into_penalty_area'),
            player.get('miscontrols'), player.get('dispossessed'),
            player.get('passes_received'), player.get('progressive_passes_received'),
            # Misc
            player.get('corner_kicks'), player.get('penalties_won'),
            player.get('penalties_conceded'),
            player.get('ball_recoveries'),
            player.get('aerials_won'), player.get('aerials_lost'),
            player.get('aerial_win_pct'),
            # Keeper
            player.get('gk_shots_on_target_against'), player.get('gk_goals_against'),
            player.get('gk_saves'), player.get('gk_save_pct'), player.get('gk_psa'),
            # Advanced
            player.get('xg'), player.get('xg_npxg'), player.get('xa'),
            player.get('sca'), player.get('gca'),
            # JSON (stats_source 和 collected_at 由 SQL 字面值提供)
            player.get('stats_json'),
            # P1-15: 数据质量标记（minutes_played / is_starter 推断）
            (lambda mp, st: 'played_minutes' if (mp or 0) > 0
             else 'subbed_zero' if st else 'did_not_play'
             )(int(player.get('minutes_played') or 0), bool(is_starter)),
        ]

        cursor.execute(sql, values)

    conn.commit()


def save_player_registry(conn: sqlite3.Connection, players: list, match_id: str):
    """保存/更新球员注册表"""
    cursor = conn.cursor()
    for player in players:
        player_id = player.get('fbref_player_id')
        if not player_id:
            continue

        cursor.execute("""
            INSERT OR IGNORE INTO fbref_players
            (fbref_player_id, player_name_en, primary_team_cn, primary_team_fbref,
             primary_position, first_seen_match, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            player_id,
            player.get('player_name', ''),
            normalize_fbref_team(player.get('team', '')),
            player.get('team_fbref', ''),
            player.get('position'),
            match_id,
        ))

        # 更新 last_updated
        cursor.execute("""
            UPDATE fbref_players SET last_updated = datetime('now')
            WHERE fbref_player_id = ?
        """, (player_id,))

    conn.commit()


# ============================================================
# 主采集器
# ============================================================

class FbrefCollector:
    """fbref.com 数据采集器"""

    def __init__(self, headless: bool = True, force_playwright: bool = False):
        self.fetcher = FbrefFetcher(headless=headless, force_playwright=force_playwright)
        self.conn = sqlite3.connect(str(DB_PATH))
        self.stats = {
            'total': 0, 'success': 0, 'failed': 0, 'skipped': 0,
            'lineups': 0, 'player_stats': 0, 'errors': [],
        }

    async def close(self):
        await self.fetcher.close()
        self.conn.close()

    async def collect_league(self, league_cn: str, season: str,
                             start_date: str = None, end_date: str = None,
                             dry_run: bool = False):
        """采集一个联赛一个赛季的数据

        Args:
            league_cn: 中文联赛名（英超/西甲/意甲/德甲/法甲）
            season: 赛季（如 2024-2025）
            start_date: 起始日期 (YYYY-MM-DD)，可选
            end_date: 结束日期 (YYYY-MM-DD)，可选
            dry_run: 试运行（只解析不写库）
        """
        print(f"\n{'='*60}")
        print(f"🏆 fbref 数据采集: {league_cn} {season}")
        if start_date or end_date:
            print(f"   日期范围: {start_date or '开始'} ~ {end_date or '结束'}")
        print(f"   写入目标: {DB_PATH}")
        print(f"{'='*60}")

        # 确保 fbref 表结构存在
        print("\n📋 检查 fbref 表结构...")
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='match_lineups'")
            if not cursor.fetchone():
                print("   ⚠️ fbref 表不存在，正在初始化...")
                init_fbref_schema()
        except Exception as e:
            print(f"   ⚠️ 表结构检查失败: {e}，尝试初始化...")
            init_fbref_schema()

        # Step 1: 获取赛程页
        sched_url = build_schedule_url(league_cn, season)
        print(f"\n📅 获取赛程页: {sched_url}")
        html = await self.fetcher.fetch(sched_url)
        if not html:
            print(f"❌ 无法获取赛程页")
            return

        # Step 2: 解析比赛列表
        matches = parse_match_list(html, league_cn, season)
        print(f"✅ 解析到 {len(matches)} 场比赛")

        # 过滤已完赛
        matches = filter_completed_matches(matches)
        print(f"   已完赛: {len(matches)} 场")

        # 按日期过滤
        if start_date or end_date:
            sd = start_date or '1900-01-01'
            ed = end_date or '2099-12-31'
            matches = filter_matches_by_date(matches, sd, ed)
            print(f"   日期范围内: {len(matches)} 场")

        if not matches:
            print("⚠️ 没有符合条件的比赛")
            return

        # Step 3: 逐场采集
        print(f"\n🏃 开始采集（{len(matches)} 场，预计 {len(matches) * REQUEST_DELAY / 60:.1f} 分钟）")
        print(f"   反爬配置: {REQUEST_DELAY}s/场, {BATCH_SIZE}场/批, {BATCH_DELAY}s批次冷却")

        for i, match in enumerate(matches, 1):
            self.stats['total'] += 1

            # 检查日限额
            if self.stats['success'] + self.stats['failed'] >= DAILY_LIMIT:
                print(f"\n⚠️ 达到日限额 {DAILY_LIMIT} 场，停止采集")
                break

            print(f"\n[{i}/{len(matches)}] {match['match_date']} {match['home_team']} vs {match['away_team']}")
            print(f"   fbref_match_id: {match['fbref_match_id']}")

            try:
                success = await self._collect_single_match(match, league_cn, season, dry_run)
                if success:
                    self.stats['success'] += 1
                else:
                    self.stats['failed'] += 1
            except Exception as e:
                print(f"   ❌ 采集异常: {e}")
                self.stats['failed'] += 1
                self.stats['errors'].append({
                    'match': f"{match['match_date']} {match['home_team']} vs {match['away_team']}",
                    'error': str(e),
                })

            # 反爬延迟
            if i < len(matches):
                self.fetcher.throttle()

        # 打印统计
        self._print_stats()

    async def _collect_single_match(self, match: dict, league_cn: str,
                                    season: str, dry_run: bool) -> bool:
        """采集单场比赛"""
        fbref_match_id = match['fbref_match_id']
        fbref_match_url = match['fbref_match_url']

        # 在 odds.db 中查找对应比赛
        odds_match = find_odds_match_id(
            self.conn, match['match_date'],
            match['home_team'], match['away_team']
        )

        if odds_match:
            odds_match_id = odds_match['match_id']
            print(f"   ✅ 关联 odds.db: {odds_match_id}")
            match['odds_match_id'] = odds_match_id
            match['home_team_cn'] = odds_match['home_team']
            match['away_team_cn'] = odds_match['away_team']
        else:
            # 构建新的 match_id
            home_cn = normalize_fbref_team(match['home_team'])
            away_cn = normalize_fbref_team(match['away_team'])
            odds_match_id = build_odds_match_id(match['match_date'], home_cn, away_cn)
            match['odds_match_id'] = odds_match_id
            match['home_team_cn'] = home_cn
            match['away_team_cn'] = away_cn
            print(f"   ⚠️ 未在 odds.db 找到对应比赛，使用构建的 match_id: {odds_match_id}")

        # 检查是否已采集过
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT 1 FROM fbref_match_mapping WHERE fbref_match_id = ?",
            (fbref_match_id,)
        )
        if cursor.fetchone() and not dry_run:
            print(f"   ⏭️ 已采集过，跳过")
            self.stats['skipped'] += 1
            return True

        # 获取比赛详情页
        html = await self.fetcher.fetch(fbref_match_url)
        if not html:
            print(f"   ❌ 无法获取比赛详情页")
            return False

        # 解析阵容
        lineups = parse_lineups(html, match)
        home_count = len(lineups['home']['starting_xi'])
        away_count = len(lineups['away']['starting_xi'])
        print(f"   📋 阵容: {lineups['home']['team']} ({lineups['home']['formation']}) "
              f"{home_count}首发 + {len(lineups['home']['substitutes'])}替补 | "
              f"{lineups['away']['team']} ({lineups['away']['formation']}) "
              f"{away_count}首发 + {len(lineups['away']['substitutes'])}替补")
        print(f"   🔄 换人: {len(lineups['substitutions'])} 次")

        # 解析球员统计
        players = parse_player_stats(html, {
            'fbref_match_id': fbref_match_id,
            'match_id': odds_match_id,
        })

        # 用统计数据补充阵容位置信息
        enrich_positions_from_stats(lineups, players)

        stats_summary = get_stats_summary(players)
        print(f"   📊 球员统计: {stats_summary['total_players']}人, "
              f"{stats_summary['total_metrics']}指标, "
              f"表: {','.join(stats_summary['tables_covered'])}")

        if dry_run:
            print(f"   🧪 试运行模式，不写库")
            return True

        # 写入数据库
        # 1. 比赛映射
        save_match_mapping(self.conn, match)

        # 2. 阵容
        save_lineups(self.conn, odds_match_id, fbref_match_id, lineups)
        self.stats['lineups'] += sum(
            len(lineups[s]['starting_xi']) + len(lineups[s]['substitutes'])
            for s in ['home', 'away']
        )

        # 3. 球员统计
        save_player_stats(self.conn, odds_match_id, fbref_match_id, players, lineups)
        self.stats['player_stats'] += len(players)

        # 4. 球员注册表
        save_player_registry(self.conn, players, odds_match_id)

        print(f"   ✅ 已写入数据库")
        return True

    def _print_stats(self):
        """打印采集统计"""
        print(f"\n{'='*60}")
        print(f"📊 采集统计")
        print(f"{'='*60}")
        print(f"  总比赛数:   {self.stats['total']}")
        print(f"  成功:       {self.stats['success']}")
        print(f"  失败:       {self.stats['failed']}")
        print(f"  跳过(已采集): {self.stats['skipped']}")
        print(f"  阵容记录:   {self.stats['lineups']}")
        print(f"  球员统计:   {self.stats['player_stats']}")
        if self.stats['errors']:
            print(f"\n  ❌ 错误列表:")
            for err in self.stats['errors'][:10]:
                print(f"    - {err['match']}: {err['error']}")
        print(f"{'='*60}")


# ============================================================
# CLI 入口
# ============================================================

async def main():
    parser = argparse.ArgumentParser(
        description="fbref.com 五大联赛阵容与球员数据采集器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 试点：英超 2024-2025 赛季 8 月（试运行）
  python scripts/fbref_collector.py --league 英超 --season 2024-2025 --start-date 2024-08-01 --end-date 2024-08-31 --dry-run

  # 正式采集：英超 2024-2025 赛季 8 月
  python scripts/fbref_collector.py --league 英超 --season 2024-2025 --start-date 2024-08-01 --end-date 2024-08-31

  # 使用 Playwright 非 headless（Cloudflare 严格时）
  python scripts/fbref_collector.py --league 英超 --season 2024-2025 --use-playwright --no-headless

  # 全联赛全赛季
  python scripts/fbref_collector.py --league all --season 2024-2025
        """,
    )
    parser.add_argument('--league', required=True,
                        choices=['英超', '西甲', '意甲', '德甲', '法甲', 'all'],
                        help='联赛名称')
    parser.add_argument('--season', required=True,
                        help='赛季 (如 2024-2025)')
    parser.add_argument('--start-date', default=None,
                        help='起始日期 (YYYY-MM-DD)')
    parser.add_argument('--end-date', default=None,
                        help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--dry-run', action='store_true',
                        help='试运行（只解析不写库）')
    parser.add_argument('--use-playwright', action='store_true',
                        help='强制使用 Playwright（跳过 HTTP）')
    parser.add_argument('--no-headless', action='store_true',
                        help='Playwright 使用非 headless 模式（显示浏览器窗口）')

    args = parser.parse_args()

    # 确定联赛列表
    if args.league == 'all':
        leagues = ['英超', '西甲', '意甲', '德甲', '法甲']
    else:
        leagues = [args.league]

    # 创建采集器
    collector = FbrefCollector(
        headless=not args.no_headless,
        force_playwright=args.use_playwright,
    )

    try:
        for league in leagues:
            await collector.collect_league(
                league, args.season,
                start_date=args.start_date,
                end_date=args.end_date,
                dry_run=args.dry_run,
            )
    finally:
        await collector.close()


if __name__ == '__main__':
    asyncio.run(main())
