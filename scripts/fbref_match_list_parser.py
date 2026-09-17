"""
fbref.com 赛程页解析器
======================
功能：解析 fbref 赛季赛程页 HTML，提取比赛列表（fbref_match_id、日期、主客队、比分、轮次）

赛程页 URL 格式：
  https://fbref.com/en/comps/{league_id}/{season}/schedule/{season}-{league_slug}-Scores-and-Fixtures

赛程表 HTML 结构：
  <table id="sched_{season}_{N}" class="stats_table">
    <thead>
      <tr><th data-stat="gameweek">Wk</th> ... <th data-stat="match_report">Match Report</th></tr>
    </thead>
    <tbody>
      <tr>
        <td>1</td>                                    <!-- 轮次 -->
        <td>Fri</td>                                   <!-- 星期 -->
        <td><a href="/matches/2024-08-16">2024-08-16</a></td>  <!-- 日期 -->
        <td>20:00</td>                                  <!-- 开赛时间 -->
        <td><a href="/squads/{hash}/...">Manchester Utd</a></td>  <!-- 主队 -->
        <td><a href="/matches/{8char-hash}/...">1–0</a></td>      <!-- 比分（含比赛URL） -->
        <td><a href="/squads/{hash}/...">Fulham</a></td>          <!-- 客队 -->
        <td>73,297</td>                                 <!-- 上座率 -->
        <td>Old Trafford</td>                           <!-- 场馆 -->
        <td>Robert Jones</td>                           <!-- 裁判 -->
        <td><a href="/matches/{hash}/...">Match Report</a></td>  <!-- 比赛报告链接 -->
        <td></td>                                       <!-- 备注 -->
      </tr>
    </tbody>
  </table>

使用方法：
  from fbref_match_list_parser import parse_match_list
  matches = parse_match_list(html, league_cfg, season)
"""

import re
from typing import Optional
from bs4 import BeautifulSoup

# ============================================================
# 联赛配置
# ============================================================

FBREF_LEAGUES = {
    "英超": {"id": 9,  "code": "PL", "slug": "Premier-League"},
    "意甲": {"id": 12, "code": "SA", "slug": "Serie-A"},
    "西甲": {"id": 12, "code": "LL", "slug": "La-Liga"},  # Note: actual id is 12 for Serie A; La Liga is 11
    "德甲": {"id": 20, "code": "BL", "slug": "Bundesliga"},
    "法甲": {"id": 13, "code": "FL", "slug": "Ligue-1"},
}

# fbref 实际联赛 ID（修正）
FBREF_LEAGUE_IDS = {
    "英超": 9,    # Premier League
    "西甲": 12,   # La Liga
    "意甲": 11,   # Serie A
    "德甲": 20,   # Bundesliga
    "法甲": 13,   # Ligue 1
}

FBREF_LEAGUE_SLUGS = {
    "英超": "Premier-League",
    "西甲": "La-Liga",
    "意甲": "Serie-A",
    "德甲": "Bundesliga",
    "法甲": "Ligue-1",
}


def build_schedule_url(league_cn: str, season: str) -> str:
    """构建赛程页 URL

    Args:
        league_cn: 中文联赛名（英超/西甲/意甲/德甲/法甲）
        season: 赛季（如 2024-2025）

    Returns:
        赛程页 URL
    """
    league_id = FBREF_LEAGUE_IDS[league_cn]
    slug = FBREF_LEAGUE_SLUGS[league_cn]
    return f"https://fbref.com/en/comps/{league_id}/{season}/schedule/{season}-{slug}-Scores-and-Fixtures"


def extract_fbref_match_id(url: str) -> Optional[str]:
    """从 fbref URL 中提取 8 位比赛 ID

    fbref URL 格式: /matches/{8char-hash}/{slug}
    例如: /matches/cc5b4244/Manchester-United-Fulham-August-16-2024-Premier-League

    Args:
        url: fbref 比赛链接

    Returns:
        8 位 hash 或 None
    """
    if not url:
        return None
    m = re.search(r'/matches/([a-f0-9]{8})/', url)
    return m.group(1) if m else None


def extract_team_hash(url: str) -> Optional[str]:
    """从球队 URL 中提取 8 位球队 hash

    fbref 球队 URL 格式: /squads/{8char-hash}/{season}/{slug}
    例如: /squads/19538871/2024-2025/Manchester-United-Stats

    Args:
        url: 球队链接

    Returns:
        8 位 hash 或 None
    """
    if not url:
        return None
    m = re.search(r'/squads/([a-f0-9]{8})/', url)
    return m.group(1) if m else None


def _parse_score(score_text: str) -> tuple:
    """解析比分文本，返回 (主队进球, 客队进球)

    fbref 比分格式: "1–0" (使用 en-dash) 或 "1-0" (使用 hyphen)
    未进行的比赛: 空字符串

    Args:
        score_text: 比分文本

    Returns:
        (home_goals, away_goals) 元组，未解析则 (None, None)
    """
    if not score_text or score_text.strip() == '':
        return None, None
    # fbref 使用 en-dash (–) 和 em-dash (—)，也可能用普通 hyphen (-)
    m = re.match(r'^(\d+)\s*[–—-]\s*(\d+)$', score_text.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _normalize_score_for_odds_db(score_text: str) -> str:
    """将 fbref 比分格式转为 odds.db 格式

    fbref: "1–0" (en-dash) → odds.db: "1:0" (colon)
    """
    if not score_text:
        return ""
    m = re.match(r'^(\d+)\s*[–—-]\s*(\d+)$', score_text.strip())
    if m:
        return f"{m.group(1)}:{m.group(2)}"
    return score_text.strip()


def parse_match_list(html: str, league_cn: str, season: str) -> list:
    """解析 fbref 赛程页 HTML，返回比赛列表

    Args:
        html: 赛程页 HTML 源码
        league_cn: 中文联赛名（英超/西甲/意甲/德甲/法甲）
        season: 赛季（如 2024-2025）

    Returns:
        比赛字典列表，每条包含:
          - fbref_match_id: 8位比赛hash
          - fbref_match_url: 完整比赛URL
          - match_date: 日期 (YYYY-MM-DD)
          - match_time: 开赛时间 (HH:MM)
          - home_team: 主队名(fbref英文)
          - home_team_hash: 主队8位hash
          - away_team: 客队名(fbref英文)
          - away_team_hash: 客队8位hash
          - score: 比分文本 (如 "2–0")
          - home_goals: 主队进球 (int 或 None)
          - away_goals: 客队进球 (int 或 None)
          - score_normalized: odds.db格式比分 (如 "2:0")
          - week: 轮次 (int 或 None)
          - venue: 场馆
          - attendance: 上座率
          - referee: 裁判
          - league: 联赛中文名
          - season: 赛季
    """
    soup = BeautifulSoup(html, 'lxml')
    matches = []

    # 查找赛程表：id 以 sched_ 开头
    # fbref 赛程表 id 格式: sched_{season}_{N} 或 sched_{season}_{N}_1 (分月时)
    sched_tables = soup.find_all('table', id=re.compile(r'^sched_'))

    if not sched_tables:
        # 备用：查找 class 包含 stats_table 且有 sched 相关 id 的表
        sched_tables = soup.find_all('table', class_='stats_table')
        sched_tables = [t for t in sched_tables if t.get('id', '').startswith('sched_')]

    if not sched_tables:
        print(f"  ⚠️ 未找到赛程表 (sched_*)")
        return matches

    for table in sched_tables:
        tbody = table.find('tbody')
        if not tbody:
            continue

        for row in tbody.find_all('tr'):
            # 跳过分组标题行（如月份分隔）
            if row.get('class') and 'spacer' in row.get('class', []):
                continue

            cells = row.find_all(['td', 'th'])
            if len(cells) < 7:
                continue

            # 按 data-stat 属性提取（更可靠）
            stat_cells = {}
            for cell in cells:
                stat = cell.get('data-stat', '')
                if stat:
                    stat_cells[stat] = cell

            # 提取各字段
            week_text = _get_cell_text(stat_cells.get('gameweek'))
            match_date = _get_cell_text(stat_cells.get('date'))
            match_time = _get_cell_text(stat_cells.get('time'))
            venue = _get_cell_text(stat_cells.get('venue'))
            attendance = _get_cell_text(stat_cells.get('attendance'))
            referee = _get_cell_text(stat_cells.get('referee'))

            # 主队
            home_cell = stat_cells.get('home')
            home_team = _get_cell_text(home_cell) if home_cell else ''
            home_team_hash = None
            if home_cell:
                a = home_cell.find('a')
                if a:
                    home_team_hash = extract_team_hash(a.get('href', ''))

            # 客队
            away_cell = stat_cells.get('away')
            away_team = _get_cell_text(away_cell) if away_cell else ''
            away_team_hash = None
            if away_cell:
                a = away_cell.find('a')
                if a:
                    away_team_hash = extract_team_hash(a.get('href', ''))

            # 比分 + 比赛URL（比分单元格内的链接包含 fbref_match_id）
            score_cell = stat_cells.get('score')
            score_text = _get_cell_text(score_cell) if score_cell else ''
            fbref_match_id = None
            fbref_match_url = None

            if score_cell:
                a = score_cell.find('a')
                if a:
                    href = a.get('href', '')
                    fbref_match_id = extract_fbref_match_id(href)
                    if fbref_match_id:
                        fbref_match_url = f"https://fbref.com{href}" if href.startswith('/') else href

            # 备用：从 match_report 单元格提取 fbref_match_id
            if not fbref_match_id:
                mr_cell = stat_cells.get('match_report')
                if mr_cell:
                    a = mr_cell.find('a')
                    if a:
                        href = a.get('href', '')
                        fbref_match_id = extract_fbref_match_id(href)
                        if fbref_match_id:
                            fbref_match_url = f"https://fbref.com{href}" if href.startswith('/') else href

            # 跳过没有比赛ID的行（未安排或无效）
            if not fbref_match_id:
                continue

            # 解析比分
            home_goals, away_goals = _parse_score(score_text)
            score_normalized = _normalize_score_for_odds_db(score_text)

            # 解析轮次
            week = None
            try:
                week = int(week_text) if week_text else None
            except ValueError:
                pass

            # 解析上座率（去除逗号）
            attendance_clean = None
            if attendance:
                try:
                    attendance_clean = int(attendance.replace(',', '').replace('.', ''))
                except ValueError:
                    attendance_clean = None

            matches.append({
                'fbref_match_id': fbref_match_id,
                'fbref_match_url': fbref_match_url,
                'fbref_match_slug': fbref_match_url.split('/matches/')[1] if '/matches/' in (fbref_match_url or '') else '',
                'match_date': match_date,
                'match_time': match_time,
                'home_team': home_team,
                'home_team_hash': home_team_hash,
                'away_team': away_team,
                'away_team_hash': away_team_hash,
                'score': score_text,
                'home_goals': home_goals,
                'away_goals': away_goals,
                'score_normalized': score_normalized,
                'week': week,
                'venue': venue,
                'attendance': attendance_clean,
                'referee': referee,
                'league': league_cn,
                'season': season,
            })

    return matches


def _get_cell_text(cell) -> str:
    """安全获取单元格文本，去除空白"""
    if cell is None:
        return ''
    return cell.get_text(strip=True)


def filter_completed_matches(matches: list) -> list:
    """过滤出已完成的比赛（有比分的）

    Args:
        matches: parse_match_list 返回的比赛列表

    Returns:
        仅包含已完场比赛的列表
    """
    return [m for m in matches if m['home_goals'] is not None and m['away_goals'] is not None]


def filter_matches_by_date(matches: list, start_date: str, end_date: str) -> list:
    """按日期范围过滤比赛

    Args:
        matches: 比赛列表
        start_date: 起始日期 (YYYY-MM-DD)
        end_date: 结束日期 (YYYY-MM-DD)

    Returns:
        日期范围内的比赛列表
    """
    return [
        m for m in matches
        if m['match_date'] and start_date <= m['match_date'] <= end_date
    ]


if __name__ == '__main__':
    # 自测：从本地 HTML 文件解析
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        print("用法: python fbref_match_list_parser.py <schedule.html> [联赛] [赛季]")
        print("示例: python fbref_match_list_parser.py sched.html 英超 2024-2025")
        sys.exit(1)

    html_file = sys.argv[1]
    league = sys.argv[2] if len(sys.argv) > 2 else "英超"
    season = sys.argv[3] if len(sys.argv) > 3 else "2024-2025"

    html = Path(html_file).read_text(encoding='utf-8')
    matches = parse_match_list(html, league, season)

    print(f"\n✅ 解析完成: 共 {len(matches)} 场比赛")
    completed = filter_completed_matches(matches)
    print(f"   已完成: {len(completed)} 场")

    for m in matches[:5]:
        print(f"  [{m['week']}] {m['match_date']} {m['home_team']} {m['score']} {m['away_team']} → {m['fbref_match_id']}")
