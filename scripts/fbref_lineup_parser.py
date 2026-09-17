"""
fbref.com 阵容解析器
====================
功能：解析比赛详情页 HTML，提取阵容信息
  - 球队阵型（如 4-2-3-1、4-4-2）
  - 首发 11 人（球衣号码 + 球员名 + fbref_player_id）
  - 替补球员（球衣号码 + 球员名 + fbref_player_id）
  - 换人记录（换入/换出球员 + 换人时间 + 原因）

HTML 结构：
  <div class="lineup">
    <table>
      <tr><th colspan="2">Liverpool (4-2-3-1)</th></tr>  ← 阵型在表头
      <tr><th>1</th><td><a href="/players/7a2e46a8/Alisson">Alisson</a></td></tr>
      ...（11 行首发）
      <tr class="spacer"><td colspan="2">Bench</td></tr>  ← Bench 分隔首发与替补
      <tr><th>62</th><td><a href="/players/...">Caoimhín Kelleher</a></td></tr>
      ...
    </table>
  </div>

换人记录 HTML 结构（在比赛事件区）：
  <div class="event">
    <div class="minute">66'</div>
    <a href="/players/{hash}/{slug}">Mikkel Damsgaard</a>
    for
    <a href="/players/{hash}/{slug}">Keane Lewis-Potter</a>
  </div>

使用方法：
  from fbref_lineup_parser import parse_lineups
  lineups = parse_lineups(html, match_info)
"""

import re
from typing import Optional
from bs4 import BeautifulSoup, Tag

# ============================================================
# 辅助函数
# ============================================================

def extract_player_id(url: str) -> Optional[str]:
    """从球员 URL 提取 8 位 fbref_player_id

    URL 格式: /players/{8char-hash}/{slug}
    """
    if not url:
        return None
    m = re.search(r'/players/([a-f0-9]{8})/', url)
    return m.group(1) if m else None


def extract_formation(text: str) -> Optional[str]:
    """从文本中提取阵型

    支持格式: 4-4-2, 4-3-3, 4-2-3-1, 3-5-2, 3-4-2-1, 5-3-2 等
    """
    if not text:
        return None
    # 匹配 N-N 或 N-N-N 或 N-N-N-N 等（2~5 段）
    m = re.search(r'\b(\d+(?:-\d+){1,5})\b', text)
    return m.group(1) if m else None


def extract_team_from_header(text: str) -> Optional[str]:
    """从表头文本提取球队名（去掉阵型部分）

    例如: "Liverpool (4-2-3-1)" → "Liverpool"
    """
    if not text:
        return None
    # 去掉括号及括号内的阵型
    name = re.sub(r'\s*\([^)]*\)\s*$', '', text).strip()
    return name if name else None


# ============================================================
# 阵容解析
# ============================================================

def parse_lineups(html: str, match_info: dict = None) -> dict:
    """解析比赛详情页，提取双方阵容

    Args:
        html: 比赛详情页 HTML
        match_info: 比赛元信息（含 home_team, away_team 等，可选）

    Returns:
        {
            "home": {
                "team": "Liverpool",
                "formation": "4-2-3-1",
                "starting_xi": [{player_name, fbref_player_id, jersey_number, position}, ...],
                "substitutes": [{player_name, fbref_player_id, jersey_number, position}, ...],
            },
            "away": { ... },
            "substitutions": [
                {team, player_in, player_in_id, player_out, player_out_id, minute, reason},
                ...
            ]
        }
    """
    soup = BeautifulSoup(html, 'lxml')
    match_info = match_info or {}

    # 解析两个阵容表
    lineup_tables = _find_lineup_tables(soup)

    home_lineup = None
    away_lineup = None

    if len(lineup_tables) >= 2:
        home_lineup = _parse_single_lineup(lineup_tables[0])
        away_lineup = _parse_single_lineup(lineup_tables[1])
    elif len(lineup_tables) == 1:
        # 只有一个阵容表（异常情况）
        home_lineup = _parse_single_lineup(lineup_tables[0])

    # 解析换人事件
    substitutions = parse_substitution_events(soup, home_lineup, away_lineup)

    # 如果阵容解析失败，尝试备用方案（从统计表推断首发/替补）
    if not home_lineup or not away_lineup:
        _fallback_from_stats(soup, home_lineup, away_lineup)

    return {
        "home": home_lineup or {"team": "", "formation": None, "starting_xi": [], "substitutes": []},
        "away": away_lineup or {"team": "", "formation": None, "starting_xi": [], "substitutes": []},
        "substitutions": substitutions,
    }


def _find_lineup_tables(soup: BeautifulSoup) -> list:
    """查找阵容表

    fbref 阵容表位于 <div class="lineup"> 内的 <table>，
    或直接是 class="lineup" 的 <table>
    """
    tables = []

    # 方式1: <div class="lineup"> > <table>
    lineup_divs = soup.find_all('div', class_='lineup')
    for div in lineup_divs:
        table = div.find('table')
        if table:
            tables.append(table)

    # 方式2: <table class="lineup">
    if not tables:
        tables = soup.find_all('table', class_='lineup')

    # 方式3: 查找含 "(4-2-3-1)" 阵型模式的表
    if not tables:
        all_tables = soup.find_all('table')
        for t in all_tables:
            text = t.get_text()[:200]
            if re.search(r'\(\d+(?:-\d+)+\)', text):
                tables.append(t)

    return tables


def _parse_single_lineup(table: Tag) -> Optional[dict]:
    """解析单个阵容表

    Args:
        table: 阵容表 BeautifulSoup Tag

    Returns:
        {team, formation, starting_xi[], substitutes[]}
    """
    rows = table.find_all('tr')
    if not rows:
        return None

    team_name = None
    formation = None
    starting_xi = []
    substitutes = []
    in_bench = False

    for row in rows:
        cells = row.find_all(['th', 'td'])

        # 第一行通常是表头：球队名 (阵型)
        if not team_name:
            header_text = row.get_text(strip=True)
            if '(' in header_text and ')' in header_text:
                team_name = extract_team_from_header(header_text)
                formation = extract_formation(header_text)
                continue
            # 也可能表头只是球队名没有阵型
            if header_text and len(header_text) < 50 and not header_text.isdigit():
                team_name = header_text
                continue

        # 检查是否是 Bench 分隔行
        row_text = row.get_text(strip=True).lower()
        if row_text == 'bench' or 'bench' in row_text and len(cells) <= 2:
            in_bench = True
            continue

        # 跳过分隔行（spacer class）
        if row.get('class') and 'spacer' in row.get('class', []):
            row_text_check = row.get_text(strip=True).lower()
            if 'bench' in row_text_check:
                in_bench = True
            continue

        # 解析球员行：[球衣号码] [球员名]
        player = _parse_player_row(row)
        if player:
            if in_bench:
                substitutes.append(player)
            else:
                starting_xi.append(player)

    if not team_name and not starting_xi and not substitutes:
        return None

    return {
        "team": team_name or "",
        "formation": formation,
        "starting_xi": starting_xi,
        "substitutes": substitutes,
    }


def _parse_player_row(row: Tag) -> Optional[dict]:
    """解析单个球员行

    行结构: <tr><th>20</th><td><a href="/players/178ae8f8/Diogo-Jota">Diogo Jota</a></td></tr>

    Returns:
        {player_name, fbref_player_id, jersey_number, position}
    """
    th = row.find('th')
    tds = row.find_all('td')

    # 球衣号码在 <th> 或第一个 <td>
    jersey_number = None
    player_name = None
    player_id = None
    player_url = None

    # 提取球衣号码
    if th:
        try:
            jersey_number = int(th.get_text(strip=True))
        except ValueError:
            pass

    # 提取球员名和链接
    # 球员名通常在 <td> 内的 <a> 标签
    for td in tds:
        a = td.find('a')
        if a and '/players/' in (a.get('href', '')):
            player_name = a.get_text(strip=True)
            player_url = a.get('href', '')
            player_id = extract_player_id(player_url)
            break

    # 如果没有 <a>，尝试从 <td> 文本提取
    if not player_name and tds:
        text = tds[0].get_text(strip=True)
        if text and text != 'Bench':
            player_name = text

    # 如果球衣号码不在 <th>，尝试从 <td> 提取
    if jersey_number is None and tds:
        for td in tds:
            try:
                val = int(td.get_text(strip=True))
                jersey_number = val
                break
            except ValueError:
                continue

    if not player_name:
        return None

    return {
        "player_name": player_name,
        "fbref_player_id": player_id,
        "fbref_player_url": player_url,
        "jersey_number": jersey_number,
        "position": None,  # 位置从统计表获取
    }


# ============================================================
# 换人事件解析
# ============================================================

def parse_substitution_events(soup: BeautifulSoup, home_lineup: dict = None, away_lineup: dict = None) -> list:
    """解析比赛事件中的换人记录

    fbref 换人事件 HTML 结构：
    <div class="event ...">
      <div class="minute">66'</div>
      <a href="/players/{hash}/{slug}">Mikkel Damsgaard</a> for
      <a href="/players/{hash}/{slug}">Keane Lewis-Potter</a>
    </div>

    或在事件时间线中：
    <div class="...">
      <a>Player In</a> for <a>Player Out</a> — Substitute
    </div>

    Args:
        soup: BeautifulSoup 对象
        home_lineup: 主队阵容（用于判断换人归属）
        away_lineup: 客队阵容（用于判断换人归属）

    Returns:
        换人记录列表: [{team, player_in, player_in_id, player_out, player_out_id, minute, reason}]
    """
    substitutions = []
    seen = set()  # 去重

    # 构建球员→球队映射（用于判断换人归属）
    player_to_team = {}
    if home_lineup:
        for p in home_lineup.get('starting_xi', []) + home_lineup.get('substitutes', []):
            if p.get('player_name'):
                player_to_team[p['player_name']] = home_lineup.get('team', 'home')
    if away_lineup:
        for p in away_lineup.get('starting_xi', []) + away_lineup.get('substitutes', []):
            if p.get('player_name'):
                player_to_team[p['player_name']] = away_lineup.get('team', 'away')

    # 方式1: 查找包含 "for" 的事件 div
    event_divs = soup.find_all('div', class_=re.compile(r'event'))
    for div in event_divs:
        text = div.get_text()
        if 'for' not in text.lower():
            continue
        # 跳过包含 "for" 但不是换人的（如 "Header" 之类）
        if 'substitute' not in text.lower() and 'for' not in text.lower():
            continue

        sub = _parse_substitution_div(div, player_to_team)
        if sub:
            key = (sub.get('player_in', ''), sub.get('player_out', ''), sub.get('minute', ''))
            if key not in seen:
                seen.add(key)
                substitutions.append(sub)

    # 方式2: 全文搜索换人模式（备用）
    if not substitutions:
        substitutions = _parse_substitutions_from_text(soup, player_to_team)

    # 按时间排序
    substitutions.sort(key=lambda s: _parse_minute(s.get('minute', '0')))

    return substitutions


def _parse_substitution_div(div: Tag, player_to_team: dict) -> Optional[dict]:
    """解析单个换人事件 div"""
    # 提取时间
    minute = None
    minute_div = div.find('div', class_='minute')
    if minute_div:
        minute = minute_div.get_text(strip=True)
    else:
        # 从文本中提取时间
        text = div.get_text()
        m = re.search(r"(\d+\+?\d*)'", text)
        if m:
            minute = m.group(1) + "'"

    # 提取所有球员链接
    player_links = div.find_all('a', href=re.compile(r'/players/'))
    if len(player_links) < 2:
        return None

    # 换人格式: PlayerIn for PlayerOut
    # 第一个链接是换入球员，第二个是换出球员
    player_in_name = player_links[0].get_text(strip=True)
    player_in_id = extract_player_id(player_links[0].get('href', ''))
    player_out_name = player_links[1].get_text(strip=True)
    player_out_id = extract_player_id(player_links[1].get('href', ''))

    # 判断换人归属
    team = player_to_team.get(player_in_name, player_to_team.get(player_out_name, ''))

    # 检查是否有伤病信息
    text = div.get_text().lower()
    reason = 'Tactical'
    if 'injur' in text:
        reason = 'Injury'
    elif 'concus' in text:
        reason = 'Concussion'

    if not player_in_name or not player_out_name:
        return None

    return {
        'team': team,
        'player_in': player_in_name,
        'player_in_id': player_in_id,
        'player_out': player_out_name,
        'player_out_id': player_out_id,
        'minute': minute or '',
        'reason': reason,
    }


def _parse_substitutions_from_text(soup: BeautifulSoup, player_to_team: dict) -> list:
    """备用方案：从全文解析换人模式

    匹配模式: PlayerName for PlayerName — Substitute
    """
    subs = []
    text = soup.get_text()

    # 匹配: [PlayerIn] for [PlayerOut]
    # 球员名可能含字母、空格、连字符、重音字符
    pattern = r"([A-Z][A-Za-zÀ-ÿ'’\-\s]+?)\s+for\s+([A-Z][A-Za-zÀ-ÿ'’\-\s]+?)(?:\s+[—–-]\s+Substitute|\s+\d+'|$)"
    matches = re.finditer(pattern, text)

    for m in matches:
        player_in = m.group(1).strip()
        player_out = m.group(2).strip()

        # 尝试提取附近的时间
        start = max(0, m.start() - 50)
        context = text[start:m.end() + 20]
        time_match = re.search(r"(\d+\+?\d*)'", context)
        minute = time_match.group(1) + "'" if time_match else ''

        team = player_to_team.get(player_in, player_to_team.get(player_out, ''))

        subs.append({
            'team': team,
            'player_in': player_in,
            'player_in_id': None,
            'player_out': player_out,
            'player_out_id': None,
            'minute': minute,
            'reason': 'Tactical',
        })

    return subs


def _parse_minute(minute_str: str) -> int:
    """将时间字符串解析为整数分钟，用于排序

    "66'" → 66
    "90+2'" → 92
    """
    if not minute_str:
        return 999
    m = re.match(r'(\d+)(?:\+(\d+))?', minute_str)
    if m:
        base = int(m.group(1))
        extra = int(m.group(2)) if m.group(2) else 0
        return base + extra
    return 999


def _fallback_from_stats(soup: BeautifulSoup, home_lineup: dict, away_lineup: dict):
    """备用方案：从球员统计表推断首发/替补

    当阵容表解析失败时，从统计表中的 minutes 和 position 推断
    """
    # 查找所有统计表
    stat_tables = soup.find_all('table', id=re.compile(r'stats_[a-f0-9]+_summary'))

    for table in stat_tables:
        # 从 table id 提取球队 hash
        m = re.match(r'stats_([a-f0-9]+)_summary', table.get('id', ''))
        if not m:
            continue
        team_hash = m.group(1)

        # 确定是主队还是客队
        # 通过表前的标题文字判断
        prev = table.find_previous(['h2', 'h3'])
        team_name = prev.get_text(strip=True) if prev else ''

        players = []
        for row in table.find('tbody').find_all('tr'):
            player = _parse_stats_row_for_lineup(row)
            if player:
                players.append(player)

        if not players:
            continue

        # 按 minutes 分：>0 且首发出场的为首发
        starters = [p for p in players if p.get('minutes_played', 0) > 0][:11]
        subs = [p for p in players if p not in starters]

        lineup_data = {
            "team": team_name,
            "formation": None,
            "starting_xi": starters,
            "substitutes": subs,
        }

        if not home_lineup:
            # 赋值给 home
            # 注意：这里直接修改字典内容
            home_lineup.clear()
            home_lineup.update(lineup_data)
        elif not away_lineup:
            away_lineup.clear()
            away_lineup.update(lineup_data)


def _parse_stats_row_for_lineup(row: Tag) -> Optional[dict]:
    """从统计表行提取球员基本信息（备用方案）"""
    cells = {c.get('data-stat', ''): c for c in row.find_all(['td', 'th'])}

    player_cell = cells.get('player')
    if not player_cell:
        return None

    a = player_cell.find('a')
    player_name = a.get_text(strip=True) if a else player_cell.get_text(strip=True)
    player_id = extract_player_id(a.get('href', '')) if a else None

    jersey = None
    if 'shirt_number' in cells:
        try:
            jersey = int(cells['shirt_number'].get_text(strip=True))
        except ValueError:
            pass

    position = cells.get('position', None)
    pos_text = position.get_text(strip=True) if position else None

    minutes = 0
    if 'minutes' in cells:
        try:
            minutes = int(cells['minutes'].get_text(strip=True))
        except ValueError:
            pass

    if not player_name:
        return None

    return {
        "player_name": player_name,
        "fbref_player_id": player_id,
        "fbref_player_url": a.get('href', '') if a else None,
        "jersey_number": jersey,
        "position": pos_text,
        "minutes_played": minutes,
    }


# ============================================================
# 位置信息补充
# ============================================================

def enrich_positions_from_stats(lineups: dict, stats: list):
    """用球员统计数据补充阵容中缺失的位置信息

    Args:
        lineups: parse_lineups 返回的阵容数据
        stats: parse_player_stats 返回的球员统计列表
    """
    # 构建 player_id → position 映射
    id_to_pos = {}
    name_to_pos = {}
    for s in stats:
        pos = s.get('position')
        if pos:
            if s.get('fbref_player_id'):
                id_to_pos[s['fbref_player_id']] = pos
            if s.get('player_name'):
                name_to_pos[s['player_name']] = pos

    # 补充位置
    for side in ['home', 'away']:
        lineup = lineups.get(side, {})
        for group in ['starting_xi', 'substitutes']:
            for player in lineup.get(group, []):
                if not player.get('position'):
                    pid = player.get('fbref_player_id')
                    pname = player.get('player_name')
                    if pid and pid in id_to_pos:
                        player['position'] = id_to_pos[pid]
                    elif pname and pname in name_to_pos:
                        player['position'] = name_to_pos[pname]


if __name__ == '__main__':
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("用法: python fbref_lineup_parser.py <match.html>")
        sys.exit(1)

    html = Path(sys.argv[1]).read_text(encoding='utf-8')
    result = parse_lineups(html)

    print(f"\n✅ 阵容解析完成")
    for side in ['home', 'away']:
        lineup = result[side]
        print(f"\n{'='*50}")
        print(f"{side.upper()}: {lineup['team']} ({lineup['formation']})")
        print(f"  首发 ({len(lineup['starting_xi'])}人):")
        for p in lineup['starting_xi']:
            print(f"    #{p['jersey_number']:>2} {p['player_name']} ({p.get('fbref_player_id', '?')})")
        print(f"  替补 ({len(lineup['substitutes'])}人):")
        for p in lineup['substitutes']:
            print(f"    #{p['jersey_number']:>2} {p['player_name']}")

    print(f"\n{'='*50}")
    print(f"换人记录 ({len(result['substitutions'])}次):")
    for s in result['substitutions']:
        print(f"  {s['minute']:>5} {s['player_in']} ↔ {s['player_out']} [{s['team']}] ({s['reason']})")
