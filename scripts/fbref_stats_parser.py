"""
fbref.com 球员统计解析器（280+ 技术指标）
==========================================
功能：解析比赛详情页中的 7 类球员统计表，提取 280+ 项技术指标

统计表 ID 规则：
  stats_{team_hash}_summary         - 摘要（进球/助攻/xG/射门/卡牌等核心指标）
  stats_{team_hash}_passing         - 传球（总传球/短传/中传/长传/关键传球等）
  stats_{team_hash}_passing_types   - 传球类型（活球/死球/角球/界外球等）
  stats_{team_hash}_defense         - 防守（抢断/拦截/解围/封堵等）
  stats_{team_hash}_possession      - 控球（触球/盘带/带球推进/被断等）
  stats_{team_hash}_misc            - 其他（角球/点球/犯规/空中对抗/球权回收等）
  keeper_{team_hash}                - 门将（扑救/失球/PSxG等）

所有表都在 HTML 源码中（CSS 隐藏 tab），BeautifulSoup 可直接解析。

输出：
  - 60 个 typed 列映射到 match_player_stats 表的列
  - 全部 280+ 指标存入 stats_json (JSON 字符串)

使用方法：
  from fbref_stats_parser import parse_player_stats
  stats = parse_player_stats(html, match_info)
"""

import json
import re
from typing import Optional, Any
from bs4 import BeautifulSoup, Tag

# ============================================================
# data-stat → schema 列名映射
# ============================================================

# Summary 表 → schema 列映射
SUMMARY_MAP = {
    'goals': 'goals',
    'assists': 'assists',
    'pens_made': 'penalties_made',          # fbref 用 pens_made 或 penalties_made
    'penalties_made': 'penalties_made',
    'pens_won': 'penalties_won',
    'pens_att': 'penalties_attempted',
    'penalties_attempted': 'penalties_attempted',
    'shots': 'shots',
    'shots_on_target': 'shots_on_target',
    'shots_total': 'shots',
    'cards_yellow': 'yellow_cards',
    'yellow_cards': 'yellow_cards',
    'cards_red': 'red_cards',
    'red_cards': 'red_cards',
    'fouls': 'fouls_committed',
    'fouls_committed': 'fouls_committed',
    'fouled': 'fouls_drawn',
    'fouls_drawn': 'fouls_drawn',
    'offsides': 'offsides',
    'crosses': 'crosses',
    'tackles_won': 'tackles_won',
    'interceptions': 'interceptions',
    'blocks': 'blocks',
    'own_goals': 'own_goals',
    'own_g': 'own_goals',
    'xg': 'xg',
    'npxg': 'xg_npxg',
    'xag': 'xa',
    'sca': 'sca',
    'gca': 'gca',
    'touches': 'touches',
    'touches_live': 'live_ball_touches',
}

# Passing 表 → schema 列映射
PASSING_MAP = {
    'passes_completed': 'passes_completed',
    'passes_attempted': 'passes_attempted',
    'pass_pct': 'pass_completion_pct',
    'passes_total_distance': 'total_distance_passes',
    'passes_progressive_distance': 'progressive_distance_passes',
    'short_passes_completed': 'short_passes_completed',
    'short_passes_attempted': 'short_passes_attempted',
    'medium_passes_completed': 'medium_passes_completed',
    'medium_passes_attempted': 'medium_passes_attempted',
    'long_passes_completed': 'long_passes_completed',
    'long_passes_attempted': 'long_passes_attempted',
    'key_passes': 'key_passes',
    'passes_into_final_third': 'passes_into_final_third',
    'passes_into_penalty_area': 'passes_into_penalty_area',
    'crosses_into_penalty_area': 'crosses_into_penalty_area',
    'progressive_passes': 'progressive_passes',
}

# Pass Types 表 → schema 列映射
PASSING_TYPES_MAP = {
    'passes_live': 'live_ball_passes',
    'passes_dead': 'dead_ball_passes',
    'passes_free_kicks': 'free_kick_passes',
    'through_balls': 'through_balls',
    'passes_switches': 'switches',
    'crosses_st': 'crosses',  # 不同表中的crosses
    'corner_kicks': 'corner_kicks',
    'throw_ins': 'throw_ins_taken',
    'passes_inbound': 'inbound_passes',
    'passes_outbound': 'outbound_passes',
}

# Defense 表 → schema 列映射
DEFENSE_MAP = {
    'tackles': 'tackles',
    'tackles_won': 'tackles_won_def',
    'tackles_def_3rd': 'tackles_in_def_third',
    'tackles_mid_3rd': 'tackles_in_mid_third',
    'tackles_att_3rd': 'tackles_in_att_third',
    'challenge_tackles': 'dribblers_tackled',
    'challenges': 'dribblers_challenged',
    'challenges_lost': 'dribbled_past',
    'blocked_shots': 'blocked_shots',
    'blocked_passes': 'blocked_passes',
    'clearances': 'clearances',
    'errors': 'errors_leading_to_shot',
    'errors_leading_to_shot': 'errors_leading_to_shot',
}

# Possession 表 → schema 列映射
POSSESSION_MAP = {
    'touches': 'touches',
    'touches_def_pen_area': 'touches_def_pen_area',
    'touches_def_3rd': 'touches_def_third',
    'touches_mid_3rd': 'touches_mid_third',
    'touches_att_3rd': 'touches_att_third',
    'touches_att_pen_area': 'touches_att_pen_area',
    'dribbles_completed': 'dribbles_completed_pos',
    'dribbles_attempted': 'dribbles_attempted_pos',
    'dribble_pct': 'successful_dribble_pct',
    'players_beaten': 'players_beaten',
    'carries': 'carries',
    'carry_distance': 'carry_distance',
    'progressive_carries': 'progressive_carries',
    'carries_into_final_third': 'carries_into_final_third',
    'carries_into_penalty_area': 'carries_into_penalty_area',
    'miscontrols': 'miscontrols',
    'dispossessed': 'dispossessed',
    'passes_received': 'passes_received',
    'progressive_passes_received': 'progressive_passes_received',
}

# Misc 表 → schema 列映射
MISC_MAP = {
    'corner_kicks': 'corner_kicks',
    'corner_kicks_in': 'corner_kicks_in',
    'corner_kicks_out': 'corner_kicks_out',
    'corner_kicks_straight': 'corner_kicks_straight',
    'penalties_won': 'penalties_won',
    'pens_won': 'penalties_won',
    'penalties_conceded': 'penalties_conceded',
    'pens_conceded': 'penalties_conceded',
    'own_goals': 'own_goals',
    'own_g': 'own_goals',
    'ball_recoveries': 'ball_recoveries',
    'aerials_won': 'aerials_won',
    'aerials_lost': 'aerials_lost',
    'aerials_won_pct': 'aerial_win_pct',
}

# Keeper 表 → schema 列映射
KEEPER_MAP = {
    'gk_shots_on_target_against': 'gk_shots_on_target_against',
    'gk_goals_against': 'gk_goals_against',
    'gk_saves': 'gk_saves',
    'gk_save_pct': 'gk_save_pct',
    'gk_psxg': 'gk_psa',
}

# 所有映射合并（用于 typed 列提取）
ALL_TYPED_MAPS = {}
for m in [SUMMARY_MAP, PASSING_MAP, PASSING_TYPES_MAP, DEFENSE_MAP, POSSESSION_MAP, MISC_MAP, KEEPER_MAP]:
    ALL_TYPED_MAPS.update(m)

# 玩家信息字段（在每张表中都出现）
PLAYER_INFO_FIELDS = {
    'player': 'player_name',
    'shirt_number': 'jersey_number',
    'nation': 'nationality',
    'position': 'position',
    'age': 'age',
    'minutes': 'minutes_played',
}

# 统计表类型 → table id 正则模式
TABLE_PATTERNS = {
    'summary': re.compile(r'stats_[a-f0-9]+_summary'),
    'passing': re.compile(r'stats_[a-f0-9]+_passing$'),
    'passing_types': re.compile(r'stats_[a-f0-9]+_passing_types'),
    'defense': re.compile(r'stats_[a-f0-9]+_defense'),
    'possession': re.compile(r'stats_[a-f0-9]+_possession'),
    'misc': re.compile(r'stats_[a-f0-9]+_misc'),
}

# table type → 对应映射
TABLE_MAPS = {
    'summary': SUMMARY_MAP,
    'passing': PASSING_MAP,
    'passing_types': PASSING_TYPES_MAP,
    'defense': DEFENSE_MAP,
    'possession': POSSESSION_MAP,
    'misc': MISC_MAP,
}


# ============================================================
# 数值解析
# ============================================================

def parse_numeric(value: str) -> Optional[Any]:
    """将 fbref 单元格文本解析为数值

    支持格式：
      "0" → 0 (int)
      "1.5" → 1.5 (float)
      "89.3" → 89.3 (float)
      "86.7%" → 86.7 (float, 去掉%)
      "" → None
      "-" → 0 (fbref 用 - 表示零)
    """
    if not value:
        return None
    text = value.strip()
    if text == '' or text == '-':
        return 0 if text == '-' else None
    # 去掉百分号
    is_pct = '%' in text
    text = text.replace('%', '').strip()
    try:
        num = float(text)
        if num == int(num) and not is_pct:
            return int(num)
        return num
    except ValueError:
        return None


def parse_age(age_str: str) -> Optional[str]:
    """解析年龄字段

    fbref 年龄格式: "27-265" 表示 27岁265天
    """
    if not age_str:
        return None
    return age_str.strip()


# ============================================================
# 统计表解析
# ============================================================

def parse_player_stats(html: str, match_info: dict = None) -> list:
    """解析比赛详情页中的球员统计数据

    Args:
        html: 比赛详情页 HTML
        match_info: 比赛元信息（含 fbref_match_id, home_team, away_team 等）

    Returns:
        球员统计字典列表，每条包含:
          - player_name, fbref_player_id, jersey_number, position, is_starter, minutes_played
          - 60 个 typed 列 (goals, assists, xg, xa, passes, tackles, ...)
          - stats_json: 全部 280+ 指标的 JSON 字符串
          - team, team_fbref_hash
    """
    soup = BeautifulSoup(html, 'lxml')
    match_info = match_info or {}

    # 查找所有统计表并按球队分组
    team_tables = _find_stat_tables_by_team(soup)

    if not team_tables:
        print("  ⚠️ 未找到球员统计表")
        return []

    all_players = []

    for team_hash, tables in team_tables.items():
        # 确定球队名称
        team_name = _find_team_name_for_hash(soup, team_hash) or team_hash

        # 合并该球队所有统计表的数据
        players = _merge_team_tables(tables, team_hash, team_name, match_info)
        all_players.extend(players)

    # 判断首发/替补（基于分钟数和 "for" 字段）
    _determine_starters(all_players)

    return all_players


def _find_stat_tables_by_team(soup: BeautifulSoup) -> dict:
    """查找所有统计表并按球队 hash 分组

    Returns:
        {team_hash: {table_type: table_tag, ...}, ...}
    """
    team_tables = {}

    # 查找所有 stats_ 和 keeper_ 表
    all_tables = soup.find_all('table', id=re.compile(r'^(stats_|keeper_)[a-f0-9]+'))

    for table in all_tables:
        table_id = table.get('id', '')
        if not table_id:
            continue

        # 提取球队 hash 和表类型
        for table_type, pattern in TABLE_PATTERNS.items():
            m = pattern.match(table_id)
            if m:
                # 提取 hash: stats_{hash}_{type}
                hash_match = re.search(r'(?:stats_|keeper_)([a-f0-9]+)', table_id)
                if hash_match:
                    team_hash = hash_match.group(1)
                    if team_hash not in team_tables:
                        team_tables[team_hash] = {}
                    team_tables[team_hash][table_type] = table
                break

        # 检查是否是 keeper 表
        keeper_match = re.match(r'keeper_([a-f0-9]+)', table_id)
        if keeper_match:
            team_hash = keeper_match.group(1)
            if team_hash not in team_tables:
                team_tables[team_hash] = {}
            team_tables[team_hash]['keeper'] = table

    return team_tables


def _find_team_name_for_hash(soup: BeautifulSoup, team_hash: str) -> Optional[str]:
    """根据球队 hash 查找球队名

    通过查找包含该 hash 的球队链接来确定球队名
    """
    # 方式1: 查找 /squads/{hash}/ 链接
    link = soup.find('a', href=re.compile(rf'/squads/{team_hash}/'))
    if link:
        return link.get_text(strip=True)

    # 方式2: 查找统计表前的标题
    for table in soup.find_all('table', id=re.compile(rf'stats_{team_hash}_|keeper_{team_hash}')):
        prev = table.find_previous(['h2', 'h3'])
        if prev:
            text = prev.get_text(strip=True)
            # 标题通常是 "Liverpool Player Stats"
            m = re.match(r'(.+?)\s+Player\s+Stats', text)
            if m:
                return m.group(1).strip()
            return text

    return None


def _merge_team_tables(tables: dict, team_hash: str, team_name: str, match_info: dict) -> list:
    """合并一个球队的所有统计表数据

    Args:
        tables: {table_type: table_tag}
        team_hash: 球队 8 位 hash
        team_name: 球队名
        match_info: 比赛元信息

    Returns:
        球员统计列表
    """
    # 用 player_id 作为 key 合并数据
    players_by_id = {}  # {player_id: {field: value}}
    players_order = []  # 保持球员顺序

    # 按表类型顺序解析（summary 先解析以获取基础信息）
    table_order = ['summary', 'passing', 'passing_types', 'defense', 'possession', 'misc', 'keeper']

    for table_type in table_order:
        table = tables.get(table_type)
        if not table:
            continue

        col_map = TABLE_MAPS.get(table_type, {})
        if table_type == 'keeper':
            col_map = KEEPER_MAP

        # 解析表的列头
        headers = _parse_table_headers(table)

        # 解析每一行
        tbody = table.find('tbody')
        if not tbody:
            continue

        for row in tbody.find_all('tr'):
            # 跳过分隔行
            if row.get('class') and 'spacer' in row.get('class', []):
                continue
            if row.get('class') and 'thead' in row.get('class', []):
                continue

            player_data = _parse_stat_row(row, headers, table_type, col_map)
            if not player_data:
                continue

            player_id = player_data.get('fbref_player_id') or player_data.get('player_name')
            if not player_id:
                continue

            if player_id not in players_by_id:
                players_by_id[player_id] = {
                    'team': team_name,
                    'team_fbref': team_hash,
                    'fbref_match_id': match_info.get('fbref_match_id', ''),
                    'match_id': match_info.get('match_id', ''),
                }
                players_order.append(player_id)

            # 合并数据（后解析的表不覆盖已有数据，除非是 None）
            existing = players_by_id[player_id]
            for key, value in player_data.items():
                if key in ('fbref_player_id', 'player_name'):
                    if key not in existing:
                        existing[key] = value
                    continue
                # 球员信息字段只设置一次
                if key in PLAYER_INFO_FIELDS.values():
                    if key not in existing or existing[key] is None:
                        existing[key] = value
                else:
                    # 统计字段：用表类型前缀存储完整数据
                    full_key = f'{table_type}_{key}'
                    existing[full_key] = value
                    # 同时更新 typed 列
                    if key in ALL_TYPED_MAPS.values():
                        if key not in existing or existing[key] is None:
                            existing[key] = value

    # 构建最终球员列表
    result = []
    for player_id in players_order:
        player = players_by_id[player_id]

        # 构建 stats_json（全部 280+ 指标）
        stats_json = {}
        for key, value in player.items():
            if key not in ('team', 'team_fbref', 'fbref_match_id', 'match_id',
                           'fbref_player_id', 'player_name', 'jersey_number',
                           'position', 'nationality', 'age', 'minutes_played',
                           'is_starter'):
                stats_json[key] = value

        player['stats_json'] = json.dumps(stats_json, ensure_ascii=False, default=str)

        # 统计指标数量
        player['_metric_count'] = len(stats_json)

        result.append(player)

    return result


def _parse_table_headers(table: Tag) -> list:
    """解析表头，返回 [(data_stat, header_text), ...]"""
    headers = []
    thead = table.find('thead')
    if not thead:
        return headers

    # 最后一行表头通常是实际列名（fbref 有时有多行表头）
    header_rows = thead.find_all('tr')
    if not header_rows:
        return headers

    # 取最后一行（或包含 data-stat 的行）
    for row in header_rows:
        row_headers = []
        for th in row.find_all(['th', 'td']):
            data_stat = th.get('data-stat', '')
            text = th.get_text(strip=True)
            row_headers.append((data_stat, text))
        if row_headers:
            headers = row_headers  # 保留最后一行有效表头

    return headers


def _parse_stat_row(row: Tag, headers: list, table_type: str, col_map: dict) -> Optional[dict]:
    """解析单个球员统计行

    Args:
        row: 表格行 Tag
        headers: 表头列表 [(data_stat, header_text), ...]
        table_type: 表类型 (summary/passing/...)
        col_map: data-stat → schema 列名映射

    Returns:
        球员数据字典
    """
    cells = row.find_all(['td', 'th'])
    if not cells:
        return None

    # 构建 data-stat → cell 文本 映射
    cell_values = {}
    for cell in cells:
        data_stat = cell.get('data-stat', '')
        if data_stat:
            cell_values[data_stat] = cell

    # 提取球员信息
    player_name = None
    fbref_player_id = None
    player_url = None

    player_cell = cell_values.get('player')
    if player_cell:
        a = player_cell.find('a')
        if a:
            player_name = a.get_text(strip=True)
            player_url = a.get('href', '')
            fbref_player_id = _extract_player_id(player_url)
        else:
            player_name = player_cell.get_text(strip=True)

    if not player_name:
        return None

    # 构建数据字典
    result = {
        'player_name': player_name,
        'fbref_player_id': fbref_player_id,
    }

    # 球员信息字段
    for stat_field, schema_field in PLAYER_INFO_FIELDS.items():
        if stat_field in cell_values:
            text = cell_values[stat_field].get_text(strip=True)
            if schema_field == 'jersey_number':
                try:
                    result[schema_field] = int(text) if text else None
                except ValueError:
                    result[schema_field] = None
            elif schema_field == 'minutes_played':
                result[schema_field] = parse_numeric(text)
            else:
                result[schema_field] = text if text else None

    # 统计字段
    for stat_field, cell in cell_values.items():
        if stat_field in PLAYER_INFO_FIELDS:
            continue
        if stat_field == 'player':
            continue

        text = cell.get_text(strip=True)
        value = parse_numeric(text)

        # 映射到 schema 列名
        schema_field = col_map.get(stat_field, stat_field)

        # 存储（用 schema 列名作为 key）
        result[schema_field] = value

    # 提取 "for" 字段（替补替换的球员）
    if 'for' in cell_values:
        for_cell = cell_values['for']
        a = for_cell.find('a')
        if a:
            result['sub_in_for'] = a.get_text(strip=True)
            result['sub_in_for_id'] = _extract_player_id(a.get('href', ''))
        else:
            text = for_cell.get_text(strip=True)
            if text:
                result['sub_in_for'] = text

    return result


def _extract_player_id(url: str) -> Optional[str]:
    """从球员 URL 提取 8 位 ID"""
    if not url:
        return None
    m = re.search(r'/players/([a-f0-9]{8})/', url)
    return m.group(1) if m else None


def _determine_starters(players: list):
    """判断球员是首发还是替补

    规则：
    1. 有 "sub_in_for" 字段的为替补
    2. minutes_played == 0 的为替补（未上场）
    3. 其他为首发
    """
    # 按球队分组
    teams = {}
    for p in players:
        team = p.get('team', '')
        if team not in teams:
            teams[team] = []
        teams[team].append(p)

    for team, team_players in teams.items():
        # 有 sub_in_for 的是替补
        subs = [p for p in team_players if p.get('sub_in_for')]
        starters = [p for p in team_players if not p.get('sub_in_for')]

        # 如果替补数 + 首发数 != 总数，用 minutes 判断
        for p in team_players:
            if 'is_starter' not in p:
                mins = p.get('minutes_played', 0)
                if mins is not None and mins == 0 and not p.get('sub_in_for'):
                    # 可能是未上场的替补
                    p['is_starter'] = 0
                else:
                    p['is_starter'] = 1

        # 确保有 sub_in_for 的是替补
        for p in subs:
            p['is_starter'] = 0

        # 如果首发超过 11 人，取 minutes 最多的 11 个
        if len(starters) > 11:
            starters.sort(key=lambda p: p.get('minutes_played', 0) or 0, reverse=True)
            for p in starters[11:]:
                p['is_starter'] = 0


# ============================================================
# 统计信息
# ============================================================

def get_stats_summary(players: list) -> dict:
    """获取球员统计的汇总信息

    Returns:
        {
            total_players: 总球员数,
            total_metrics: 总指标数,
            tables_covered: 覆盖的统计表类型,
            by_team: {team: {players, starters, subs}},
        }
    """
    if not players:
        return {'total_players': 0, 'total_metrics': 0, 'tables_covered': [], 'by_team': {}}

    # 统计指标数（取第一个球员的 stats_json 长度）
    max_metrics = max(p.get('_metric_count', 0) for p in players)

    # 覆盖的表类型
    table_types = set()
    for p in players:
        for key in p.keys():
            if '_' in key:
                prefix = key.split('_')[0]
                if prefix in ('summary', 'passing', 'defense', 'possession', 'misc', 'keeper'):
                    table_types.add(prefix)

    # 按球队统计
    by_team = {}
    for p in players:
        team = p.get('team', 'Unknown')
        if team not in by_team:
            by_team[team] = {'players': 0, 'starters': 0, 'subs': 0}
        by_team[team]['players'] += 1
        if p.get('is_starter', 1) == 1:
            by_team[team]['starters'] += 1
        else:
            by_team[team]['subs'] += 1

    return {
        'total_players': len(players),
        'total_metrics': max_metrics,
        'tables_covered': sorted(table_types),
        'by_team': by_team,
    }


if __name__ == '__main__':
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("用法: python fbref_stats_parser.py <match.html>")
        sys.exit(1)

    html = Path(sys.argv[1]).read_text(encoding='utf-8')
    players = parse_player_stats(html)

    summary = get_stats_summary(players)
    print(f"\n✅ 球员统计解析完成")
    print(f"   总球员数: {summary['total_players']}")
    print(f"   最大指标数: {summary['total_metrics']}")
    print(f"   覆盖统计表: {summary['tables_covered']}")

    for team, info in summary['by_team'].items():
        print(f"\n  {team}: {info['players']}人 (首发{info['starters']}, 替补{info['subs']})")

    print(f"\n{'='*60}")
    for p in players[:3]:
        print(f"\n  {p.get('player_name')} (#{p.get('jersey_number')}, {p.get('position')})")
        print(f"    进球={p.get('goals')}, 助攻={p.get('assists')}, xG={p.get('xg')}, xA={p.get('xa')}")
        print(f"    传球={p.get('passes_completed')}/{p.get('passes_attempted')}, 抢断={p.get('tackles')}")
        print(f"    触球={p.get('touches')}, 分钟={p.get('minutes_played')}, 首发={p.get('is_starter')}")
        print(f"    指标数: {p.get('_metric_count')}")
