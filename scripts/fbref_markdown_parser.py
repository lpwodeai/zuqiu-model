"""
fbref.com Markdown 解析器
==========================
功能：解析 WebFetch 工具返回的 Markdown 格式页面，提取阵容和球员统计数据

WebFetch 成功绕过 Cloudflare 获取 fbref.com 页面，返回 Markdown 格式。
本模块解析该 Markdown 输出，提取：
  1. 比赛信息（球队、比分、日期、场馆）
  2. 阵容数据（阵型、首发11人、替补名单）
  3. 换人记录（换入/换出球员、时间）
  4. 球员统计（280+ 指标，来自7类统计表）

使用方法：
  with open('match_page.md', 'r', encoding='utf-8') as f:
      markdown = f.read()
  result = parse_match_from_markdown(markdown, '英超', '2024-2025')
"""

import re
import json
from typing import Optional


# ============================================================
# 比赛信息解析
# ============================================================

def parse_match_info(markdown: str) -> dict:
    """从 Markdown 提取比赛基本信息

    Returns:
        {home_team, away_team, home_score, away_score, date, venue, attendance, ...}
    """
    info = {
        'home_team': '', 'away_team': '',
        'home_score': None, 'away_score': None,
        'date': '', 'venue': '', 'attendance': '',
        'matchweek': '',
    }

    # 提取标题："# Manchester United vs. Fulham Match Report – Friday August 16, 2024"
    title_match = re.search(r'^#\s+(.+?)\s+vs\.?\s+(.+?)\s+Match Report\s*[–-]\s*(.+)$', markdown, re.MULTILINE)
    if title_match:
        info['home_team'] = title_match.group(1).strip()
        info['away_team'] = title_match.group(2).strip()

    # --- 日期提取 ---
    # 方案1: 从日期链接 URL 提取 ISO 日期（最可靠）
    # 格式: [**Friday August 16, 2024**](https://fbref.com/en/matches/2024-08-16)
    date_url_match = re.search(
        r'https://fbref\.com/en/matches/(\d{4}-\d{2}-\d{2})', markdown
    )
    if date_url_match:
        info['date'] = date_url_match.group(1)
    else:
        # 方案2: 从标题解析 "Friday August 16, 2024"
        if title_match:
            date_str = title_match.group(3).strip()
            parsed_date = _parse_date_string(date_str)
            if parsed_date:
                info['date'] = parsed_date
        # 方案3: 从内容中找 [YYYY-MM-DD] 格式
        if not info['date']:
            date_match = re.search(r'\[(\d{4}-\d{2}-\d{2})\]', markdown)
            if date_match:
                info['date'] = date_match.group(1)

    # --- 比分提取 ---
    # 格式: [**Manchester United**](url)\n\n1\n  → home_score=1
    #        [**Fulham**](url)\n\n0\n           → away_score=0
    if info['home_team']:
        home_score_pattern = re.compile(
            r'\[\*\*' + re.escape(info['home_team']) + r'\*\*\]\([^)]+\)\s*\n\s*\n\s*(\d+)\s*\n'
        )
        hs_match = home_score_pattern.search(markdown)
        if hs_match:
            info['home_score'] = int(hs_match.group(1))

    if info['away_team']:
        away_score_pattern = re.compile(
            r'\[\*\*' + re.escape(info['away_team']) + r'\*\*\]\([^)]+\)\s*\n\s*\n\s*(\d+)\s*\n'
        )
        as_match = away_score_pattern.search(markdown)
        if as_match:
            info['away_score'] = int(as_match.group(1))

    # 提取场馆
    venue_match = re.search(r'\*\*Venue\*\*\s*:\s*(.+?)(?:\n|$)', markdown)
    if venue_match:
        info['venue'] = venue_match.group(1).strip()

    # 提取上座率
    att_match = re.search(r'\*\*Attendance\*\*\s*:\s*([\d,]+)', markdown)
    if att_match:
        info['attendance'] = att_match.group(1).strip()

    # 提取轮次
    mw_match = re.search(r'Matchweek\s+(\d+)', markdown, re.IGNORECASE)
    if mw_match:
        info['matchweek'] = mw_match.group(1)

    return info


def _parse_date_string(date_str: str) -> str:
    """解析 'Friday August 16, 2024' → '2024-08-16'"""
    import datetime
    # 常见格式: "Friday August 16, 2024" / "Saturday Aug 17, 2024"
    for fmt in ('%A %B %d, %Y', '%a %B %d, %Y', '%A %b %d, %Y', '%a %b %d, %Y'):
        try:
            dt = datetime.datetime.strptime(date_str, fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    return ''


# ============================================================
# 阵容解析
# ============================================================

def parse_lineups_from_markdown(markdown: str) -> dict:
    """从 Markdown 提取阵容数据

    阵容表格式：
        | Manchester Utd (4-2-3-1) | |
        |---|---|
        | 24 | [André Onana](url) |
        ...
        | Bench | |
        | 1 | [Altay Bayındır](url) |
        ...

    Returns:
        {home: {team, formation, starting_xi[], substitutes[]}, away: {...}}
    """
    lineups = {'home': {}, 'away': {}}

    # 找所有阵容表（以 "Team Name (formation)" 开头的表格）
    # 格式: | Team Name (4-2-3-1) | |
    lineup_pattern = re.compile(
        r'\|\s*(.+?)\s*\((\d+(?:-\d+)+)\)\s*\|\s*\|\s*\n'  # 表头行
        r'\|[-|]+\|\s*\n'  # 分隔行
        r'((?:\|[^\n]+\n)+)',  # 数据行
        re.MULTILINE
    )

    matches = list(lineup_pattern.finditer(markdown))

    for i, match in enumerate(matches):
        team_name = match.group(1).strip()
        formation = match.group(2).strip()
        data_rows = match.group(3)

        side = 'home' if i == 0 else 'away'

        starting_xi = []
        substitutes = []
        in_bench = False

        for row in data_rows.strip().split('\n'):
            row = row.strip()
            if not row or not row.startswith('|'):
                continue

            # "Bench" 行标记替补开始
            if 'Bench' in row:
                in_bench = True
                continue

            # 解析球员行: | 24 | [André Onana](url) |
            player = _parse_player_row_md(row)
            if player:
                if in_bench:
                    substitutes.append(player)
                else:
                    starting_xi.append(player)

        lineups[side] = {
            'team': team_name,
            'formation': formation,
            'starting_xi': starting_xi,
            'substitutes': substitutes,
        }

    return lineups


def _parse_player_row_md(row: str) -> Optional[dict]:
    """解析 Markdown 表格中的球员行

    格式: | 24 | [André Onana](https://fbref.com/en/players/e9c0c1b2/Andre-Onana) |
    """
    # 提取球衣号码（第一列）
    parts = row.split('|')
    if len(parts) < 3:
        return None

    jersey_str = parts[1].strip()
    if not jersey_str.isdigit():
        return None

    jersey = int(jersey_str)

    # 提取球员名和ID：[Player Name](url)
    link_match = re.search(r'\[([^\]]+)\]\(https://fbref\.com/en/players/([a-f0-9]{8})/', row)
    if link_match:
        player_name = link_match.group(1).strip()
        player_id = link_match.group(2)
    else:
        # 无链接的球员
        name_match = re.search(r'\[([^\]]+)\]', row)
        if name_match:
            player_name = name_match.group(1).strip()
            player_id = None
        else:
            return None

    return {
        'player_name': player_name,
        'fbref_player_id': player_id,
        'jersey_number': jersey,
        'position': None,  # 位置从统计表补充
    }


# ============================================================
# 换人记录解析
# ============================================================

def parse_substitutions_from_markdown(markdown: str, lineups: dict = None) -> list:
    """从 Markdown 提取换人记录

    换人格式（在 Match Summary 中）：
        61' 0:0
        [Alejandro Garnacho](url)
        for [Amad Diallo](url)
         — Substitute

    Returns:
        [{team, player_in, player_out, minute, score}]
    """
    substitutions = []

    # 匹配换人模式（允许元素间有空行）
    # 实际 Markdown 格式:
    #   61' 0:0
    #   <blank>
    #   [Alejandro Garnacho](url)
    #   <blank>
    #   for [Amad Diallo](url)
    #   <blank>
    #    — Substitute
    sub_pattern = re.compile(
        r"(\d+(?:\+\d+)?)['’]\s*(\d+:\d+)\s+"  # 分钟 + 比分 + 空白(含换行和空行)
        r"\[([^\]]+)\]\(https://fbref\.com/en/players/([a-f0-9]{8})/[^\)]*\)\s+"  # 换入球员
        r"for\s+\[([^\]]+)\]\(https://fbref\.com/en/players/([a-f0-9]{8})/[^\)]*\)\s+"  # 换出球员
        r"[—-]\s*Substitute",
        re.DOTALL
    )

    for match in sub_pattern.finditer(markdown):
        minute = match.group(1)
        score = match.group(2)
        player_in = match.group(3)
        player_in_id = match.group(4)
        player_out = match.group(5)
        player_out_id = match.group(6)

        # 确定球队（通过球员名匹配阵容）
        team = _determine_team(player_in, lineups) if lineups else ''

        substitutions.append({
            'team': team,
            'player_in': player_in,
            'player_in_id': player_in_id,
            'player_out': player_out,
            'player_out_id': player_out_id,
            'minute': minute,
            'score': score,
        })

    return substitutions


def _determine_team(player_name: str, lineups: dict) -> str:
    """根据球员名确定所属球队"""
    for side in ['home', 'away']:
        lineup = lineups.get(side, {})
        for group in ['starting_xi', 'substitutes']:
            for p in lineup.get(group, []):
                if p.get('player_name') == player_name:
                    return lineup.get('team', '')
    return ''


# ============================================================
# 球员统计解析
# ============================================================

# 统计表类型识别关键词
TABLE_TYPE_MARKERS = {
    'summary': ['Performance', 'Expected', 'SCA', 'Passes', 'Carries', 'Take-Ons'],
    'passing': ['Total', 'Short', 'Medium', 'Long', 'TotDist', 'PrgDist', 'CrsPA'],
    'pass_types': ['Pass Types', 'Corner Kicks', 'Outcomes'],
    'defense': ['Tackles', 'Challenges', 'Blocks', 'Interceptions'],
    'possession': ['Touches', 'Dribbles', 'Carries', 'Receiving'],
    'misc': ['Miscellaneous', 'Fouls', 'Aerials', 'Crosses'],
}

# Summary 表列映射
SUMMARY_COLS = [
    'player_name', 'jersey_number', 'nation', 'position', 'age', 'minutes_played',
    'goals', 'assists', 'penalties_made', 'penalties_attempted',
    'shots', 'shots_on_target', 'yellow_cards', 'red_cards',
    'touches', 'tackles', 'interceptions', 'blocks',
    'xg', 'xg_npxg', 'xa', 'sca', 'gca',
    'passes_completed', 'passes_attempted', 'pass_completion_pct',
    'progressive_passes', 'carries', 'progressive_carries',
    'dribbles_attempted_pos', 'successful_dribble_pct',
]

# Passing 表列映射（含短/中/长传的 Cmp% 列）
PASSING_COLS = [
    'player_name', 'jersey_number', 'nation', 'position', 'age', 'minutes_played',
    'passes_completed', 'passes_attempted', 'pass_completion_pct',
    'total_distance_passes', 'progressive_distance_passes',
    'short_passes_completed', 'short_passes_attempted', 'short_passes_pct',
    'medium_passes_completed', 'medium_passes_attempted', 'medium_passes_pct',
    'long_passes_completed', 'long_passes_attempted', 'long_passes_pct',
    'assists', 'xa', 'xg_xa', 'key_passes',
    'passes_into_final_third', 'passes_into_penalty_area',
    'crosses_into_penalty_area', 'progressive_passes',
]


def parse_player_stats_from_markdown(markdown: str) -> list:
    """从 Markdown 提取球员统计数据

    WebFetch 返回的 Markdown 包含多个球员统计表（Summary/Passing/Pass Types/Defense/Possession/Misc）。
    本函数解析这些表，按球员合并数据。

    Returns:
        [{player_name, fbref_player_id, team, jersey_number, position, minutes_played, ...stats, stats_json}]
    """
    players_by_id = {}

    # --- 球队上下文追踪 ---
    # Markdown 结构: "## Manchester Utd Player Stats" 标记球队统计区
    # 记录所有球队标题位置，用于为每个统计表确定所属球队
    team_headers = []
    for m in re.finditer(r'^##\s+(.+?)\s+Player Stats\s*$', markdown, re.MULTILINE):
        team_headers.append((m.start(), m.group(1).strip()))

    def _find_team_for_position(pos: int) -> str:
        """根据统计表在 Markdown 中的位置，找最近的球队标题"""
        current_team = ''
        for header_pos, team_name in team_headers:
            if header_pos < pos:
                current_team = team_name
            else:
                break
        return current_team

    # 找到所有球员统计表
    # Markdown 结构：
    #   | Category1 | Category2 | ... |   (分类行，可选)
    #   |---|---|...|                  (分隔行)
    #   | Player | # | Nation | Pos | Age | Min | Gls | ... |  (列头行)
    #   | [Player Name](url) | 8 | ... |  (数据行)
    # 数据行紧跟列头行，无额外分隔
    table_pattern = re.compile(
        r'\|\s*Player\s*\|\s*#\s*\|\s*Nation\s*\|\s*Pos\s*\|\s*Age\s*\|\s*Min\s*\|([^\n]+)\n'  # 列头行
        r'((?:\|[^\n]+\n)+)',  # 数据行（紧跟列头）
        re.MULTILINE
    )

    for table_match in table_pattern.finditer(markdown):
        header_extra = table_match.group(1)
        data_rows = table_match.group(2)

        # 根据统计表位置确定球队
        current_team = _find_team_for_position(table_match.start())

        # 识别表类型
        table_type = _identify_table_type(header_extra)

        # 解析表头列名
        col_names = _parse_header_columns(header_extra, table_type)

        # 解析数据行
        for row in data_rows.strip().split('\n'):
            row = row.strip()
            if not row.startswith('|'):
                continue

            player = _parse_stats_row(row, col_names, table_type)
            if not player:
                continue

            player_id = player.get('fbref_player_id') or player['player_name']

            if player_id not in players_by_id:
                players_by_id[player_id] = {
                    'player_name': player['player_name'],
                    'fbref_player_id': player.get('fbref_player_id'),
                    'jersey_number': player.get('jersey_number'),
                    'position': player.get('position'),
                    'minutes_played': player.get('minutes_played'),
                    'nation': player.get('nation'),
                    'team': current_team,  # 从球队上下文赋值
                }

            # 合并统计数据
            for key, value in player.items():
                if key in ('player_name', 'fbref_player_id', 'jersey_number', 'position',
                           'minutes_played', 'nation'):
                    continue
                if value is not None and value != '':
                    players_by_id[player_id][key] = value

    # 构建 stats_json
    players = list(players_by_id.values())
    basic_keys = {'player_name', 'fbref_player_id', 'jersey_number', 'position',
                  'minutes_played', 'nation', 'team'}

    for player in players:
        stats_json = {k: v for k, v in player.items() if k not in basic_keys}
        player['stats_json'] = json.dumps(stats_json, ensure_ascii=False)

    return players


def _identify_table_type(header_extra: str) -> str:
    """根据表头额外列识别表类型"""
    header_lower = header_extra.lower()
    for ttype, markers in TABLE_TYPE_MARKERS.items():
        for marker in markers:
            if marker.lower() in header_lower:
                return ttype
    return 'unknown'


def _parse_header_columns(header_extra: str, table_type: str) -> list:
    """解析表头列名"""
    # 从 header_extra 提取列名
    cols = [c.strip() for c in header_extra.split('|') if c.strip()]

    # 根据表类型映射
    if table_type == 'summary':
        return SUMMARY_COLS[6:]  # 跳过前6个基础列
    elif table_type == 'passing':
        return PASSING_COLS[6:]
    else:
        return cols  # 返回原始列名


def _parse_stats_row(row: str, col_names: list, table_type: str) -> Optional[dict]:
    """解析统计表数据行"""
    parts = [p.strip() for p in row.split('|')]
    if len(parts) < 5:
        return None

    player = {}

    # 第一列：球员名 + 链接 [Name](url)
    player_cell = parts[1]
    link_match = re.search(r'\[([^\]]+)\]\(https://fbref\.com/en/players/([a-f0-9]{8})/', player_cell)
    if link_match:
        player['player_name'] = link_match.group(1).strip()
        player['fbref_player_id'] = link_match.group(2)
    else:
        # 跳过非球员行（如 "16 Players" 汇总行）
        name_match = re.search(r'\[([^\]]+)\]', player_cell)
        if name_match:
            player['player_name'] = name_match.group(1).strip()
            player['fbref_player_id'] = None
        else:
            return None

    # 跳过汇总行
    if 'Players' in player_cell or 'Squad' in player_cell:
        return None

    # 第二列：球衣号
    if parts[2].isdigit():
        player['jersey_number'] = int(parts[2])

    # 第三列：国籍 [xx Country](url)
    nation_match = re.search(r'\[([^\]]+)\]', parts[3]) if len(parts) > 3 else None
    if nation_match:
        player['nation'] = nation_match.group(1).strip()

    # 第四列：位置
    if len(parts) > 4:
        player['position'] = parts[4].strip()

    # 第五列：年龄 (格式: 29-343)
    if len(parts) > 5:
        age_str = parts[5].strip()
        age_match = re.match(r'(\d+)-(\d+)', age_str)
        if age_match:
            player['age'] = int(age_match.group(1))
            player['age_days'] = int(age_match.group(2))

    # 第六列：分钟数
    if len(parts) > 6:
        min_str = parts[6].strip()
        if min_str.isdigit():
            player['minutes_played'] = int(min_str)

    # 后续列：统计数据
    stat_values = parts[7:]  # 跳过前6个基础列 + 空的第一元素
    for i, value in enumerate(stat_values):
        if i >= len(col_names):
            break
        col_name = col_names[i]
        value = value.strip()
        if value and value != '':
            # 尝试转换为数值
            try:
                if '.' in value:
                    player[col_name] = float(value)
                else:
                    player[col_name] = int(value)
            except ValueError:
                player[col_name] = value

    return player


# ============================================================
# 球队归属推断
# ============================================================

def assign_teams_to_players(players: list, lineups: dict):
    """根据阵容信息为球员分配球队

    Markdown 中球员统计表按球队分组，但表头可能不含球队名。
    通过阵容中的球员名单匹配来推断球队。
    """
    # 构建球员→球队映射
    player_to_team = {}
    for side in ['home', 'away']:
        lineup = lineups.get(side, {})
        team_name = lineup.get('team', '')
        for group in ['starting_xi', 'substitutes']:
            for p in lineup.get(group, []):
                player_to_team[p['player_name']] = team_name

    # 为每个球员分配球队
    for player in players:
        name = player.get('player_name', '')
        if name in player_to_team:
            player['team'] = player_to_team[name]


# ============================================================
# 主解析函数
# ============================================================

def parse_match_from_markdown(markdown: str, league: str = '', season: str = '',
                              fbref_match_id: str = '') -> dict:
    """解析 WebFetch 返回的比赛详情页 Markdown

    Args:
        markdown: WebFetch 返回的 Markdown 文本
        league: 联赛名称（中文）
        season: 赛季（如 2024-2025）
        fbref_match_id: fbref 比赛 ID（8位哈希）

    Returns:
        {match_info, lineups, substitutions, players}
    """
    # 1. 解析比赛信息
    match_info = parse_match_info(markdown)
    match_info['league'] = league
    match_info['season'] = season
    match_info['fbref_match_id'] = fbref_match_id

    # 2. 解析阵容
    lineups = parse_lineups_from_markdown(markdown)

    # 3. 解析换人
    substitutions = parse_substitutions_from_markdown(markdown, lineups)

    # 4. 解析球员统计
    players = parse_player_stats_from_markdown(markdown)

    # 5. 为球员分配球队
    assign_teams_to_players(players, lineups)

    # 6. 用统计表数据补充阵容中的位置信息
    _enrich_lineup_positions(lineups, players)

    return {
        'match_info': match_info,
        'lineups': lineups,
        'substitutions': substitutions,
        'players': players,
    }


def _enrich_lineup_positions(lineups: dict, players: list):
    """用球员统计表的位置数据补充阵容中的位置信息"""
    player_positions = {}
    for p in players:
        name = p.get('player_name', '')
        pos = p.get('position')
        if name and pos:
            player_positions[name] = pos

    for side in ['home', 'away']:
        lineup = lineups.get(side, {})
        for group in ['starting_xi', 'substitutes']:
            for p in lineup.get(group, []):
                if not p.get('position') and p['player_name'] in player_positions:
                    p['position'] = player_positions[p['player_name']]


# ============================================================
# 赛程页解析
# ============================================================

def parse_schedule_from_markdown(markdown: str, league: str = '', season: str = '') -> list:
    """解析 WebFetch 返回的赛程页 Markdown，提取比赛列表

    赛程表格式：
        | Wk | Day | Date | Time | Home | Score | Away | ... | Match Report | ... |
        | 1 | Fri | [2024-08-16](url) | 20:00 | [Manchester Utd](url) | [1–0](match_url) | [Fulham](url) | ... |

    Returns:
        [{fbref_match_id, fbref_match_url, match_date, home_team, away_team, score, week}]
    """
    matches = []

    # 找赛程表行（包含 Match Report 链接的行）
    # 格式: | 1 | Fri | [2024-08-16](url) | ... | [1–0](match_url) | ... |
    row_pattern = re.compile(
        r'\|\s*(\d+)\s*\|'  # Wk
        r'\s*(\w+)\s*\|'  # Day
        r'\s*\[(\d{4}-\d{2}-\d{2})\]\([^\)]*\)\s*\|'  # Date
        r'\s*([\d:]+)\s*\|'  # Time
        r'\s*\[([^\]]+)\]\([^\)]*\)\s*\|'  # Home
        r'\s*\[([^\]]+)\]\(https://fbref\.com/en/matches/([a-f0-9]{8})/[^\)]*\)\s*\|'  # Score + match URL
        r'\s*\[([^\]]+)\]\([^\)]*\)\s*\|',  # Away
        re.MULTILINE
    )

    for match in row_pattern.finditer(markdown):
        week = int(match.group(1))
        day = match.group(2)
        date = match.group(3)
        time = match.group(4)
        home_team = match.group(5)
        score = match.group(6)
        fbref_match_id = match.group(7)
        away_team = match.group(8)

        # 构建比赛 URL
        fbref_match_url = f"https://fbref.com/en/matches/{fbref_match_id}"

        # 解析比分 "1–0" → home_goals, away_goals
        score_match = re.match(r'(\d+)[–—-](\d+)', score)
        home_goals = int(score_match.group(1)) if score_match else None
        away_goals = int(score_match.group(2)) if score_match else None

        matches.append({
            'fbref_match_id': fbref_match_id,
            'fbref_match_url': fbref_match_url,
            'match_date': date,
            'home_team': home_team,
            'away_team': away_team,
            'score': score,
            'home_goals': home_goals,
            'away_goals': away_goals,
            'week': week,
            'league': league,
            'season': season,
        })

    return matches


# ============================================================
# 测试入口
# ============================================================

if __name__ == '__main__':
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("用法: python fbref_markdown_parser.py <markdown_file> [--schedule]")
        print("  解析 WebFetch 返回的 fbref 页面 Markdown")
        sys.exit(1)

    md_file = Path(sys.argv[1])
    if not md_file.exists():
        print(f"❌ 文件不存在: {md_file}")
        sys.exit(1)

    markdown = md_file.read_text(encoding='utf-8')

    if '--schedule' in sys.argv:
        # 解析赛程页
        matches = parse_schedule_from_markdown(markdown, '英超', '2024-2025')
        print(f"\n✅ 解析到 {len(matches)} 场比赛")
        for m in matches[:5]:
            print(f"  {m['match_date']} {m['home_team']} {m['score']} {m['away_team']} (ID: {m['fbref_match_id']})")
        if len(matches) > 5:
            print(f"  ... 共 {len(matches)} 场")
    else:
        # 解析比赛详情页
        result = parse_match_from_markdown(markdown, '英超', '2024-2025')

        mi = result['match_info']
        print(f"\n✅ 比赛信息:")
        print(f"  {mi['home_team']} vs {mi['away_team']}")
        print(f"  日期: {mi['date']}, 场馆: {mi['venue']}")

        for side in ['home', 'away']:
            lineup = result['lineups'].get(side, {})
            print(f"\n📋 {side.upper()} 阵容: {lineup.get('team', '')} ({lineup.get('formation', '')})")
            print(f"  首发: {len(lineup.get('starting_xi', []))}人")
            for p in lineup.get('starting_xi', [])[:3]:
                print(f"    #{p['jersey_number']} {p['player_name']} ({p.get('position', '?')})")
            print(f"  替补: {len(lineup.get('substitutes', []))}人")

        print(f"\n🔄 换人: {len(result['substitutions'])} 次")
        for sub in result['substitutions'][:3]:
            print(f"  {sub['minute']}' {sub['player_in']} ← {sub['player_out']}")

        players = result['players']
        print(f"\n📊 球员统计: {len(players)}人")
        if players:
            p = players[0]
            stat_count = len([k for k in p.keys() if k not in
                            ('player_name', 'fbref_player_id', 'jersey_number', 'position',
                             'minutes_played', 'nation', 'team', 'stats_json')])
            print(f"  示例: {p['player_name']} - {stat_count}项统计指标")
            print(f"  进球: {p.get('goals', '?')}, 助攻: {p.get('assists', '?')}, "
                  f"xG: {p.get('xg', '?')}, 射门: {p.get('shots', '?')}")
