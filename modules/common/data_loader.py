"""通用赔率数据加载模块

负责从odds.db加载比赛数据和赔率时序数据，提供统一的数据访问接口。
支持所有联赛，通过 league 参数区分。

数据库说明：
- odds.db: 存储五大联赛赔率数据（5,252场比赛，3赛季）
  - 胜平负赔率、让球赔率、总进球赔率、比分赔率

数据模型：
- MatchData: 比赛基本信息
- OddsTemporalData: 赔率时序数据

核心功能：
- load_league_matches: 加载指定联赛所有比赛基本信息
- load_wdl_history: 加载单场比赛胜平负赔率时序
- load_handicap_history: 加载单场比赛让球赔率时序
- load_total_goals_history: 加载单场比赛总进球赔率时序
- load_score_history: 加载单场比赛比分赔率时序
- load_all_match_data: 加载指定联赛所有数据（含赔率）
"""

import sys
import sqlite3
import pandas as pd
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

# 数据库路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from db_utils import connect, read_sql  # noqa: E402

ODDS_DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'odds.db')
FIVE_LEAGUES_DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'five_leagues.db')


@dataclass
class MatchData:
    """比赛基本信息数据模型"""
    match_id: str           # YYYY-MM-DD_主队_客队
    home_team: str          # 中文队名
    away_team: str          # 中文队名
    match_date: str         # YYYY-MM-DD
    match_type: str         # 比赛类型/联赛
    handicap: Optional[float] = None      # 让球
    actual_wdl: Optional[str] = None      # 胜/平/负
    actual_score: Optional[str] = None    # X:Y
    actual_total_goals: Optional[int] = None  # 总进球数


@dataclass
class OddsTemporalData:
    """赔率时序数据模型"""
    match_id: str
    timestamps: List[str]          # 时间点列表（按时间排序）
    wdl_data: pd.DataFrame         # 胜平负赔率
    handicap_data: pd.DataFrame    # 让球赔率
    total_goals_data: pd.DataFrame # 总进球赔率
    score_data: pd.DataFrame       # 比分赔率


def get_db_connection(db_path: str = ODDS_DB_PATH):
    """获取数据库连接（DB_BACKEND=pg 时忽略 db_path 连 PG, 默认 SQLite）。"""
    return connect(db_path=db_path)


# match_id 映射缓存: {english_match_id: chinese_sh_match_id}
_match_id_cache: Dict[str, str] = {}


def _resolve_match_id_for_history(match_id: str, conn: sqlite3.Connection) -> str:
    """将英文 match_id 映射为 *_history 表使用的中文 sh_match_id。

    matches 表使用英文球队名（如 Arsenal），而 wdl_history/handicap_history/
    total_goals_history/score_history 表使用中文球队名（如 阿森纳）。
    match_id_mapping 桥接表存储 sh_match_id（中文）→ matches_match_id（英文）。

    Args:
        match_id: matches 表中的英文 match_id
        conn: 数据库连接

    Returns:
        str: *_history 表中对应的 match_id（可能是中文或原值）
    """
    if match_id in _match_id_cache:
        return _match_id_cache[match_id]

    cursor = conn.cursor()
    cursor.execute(
        "SELECT sh_match_id FROM match_id_mapping WHERE matches_match_id = ?",
        (match_id,)
    )
    row = cursor.fetchone()
    if row:
        resolved = row['sh_match_id']
    else:
        # No mapping found — return original (may be a direct match)
        resolved = match_id

    _match_id_cache[match_id] = resolved
    return resolved


def load_league_matches(league: str) -> List[MatchData]:
    """
    加载指定联赛所有比赛基本信息

    Args:
        league: 联赛名称（如 '英超', '意甲', '德甲', '西甲', '法甲'）

    Returns:
        List[MatchData]: 比赛数据列表
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT match_id, home_team, away_team, match_date, match_type,
               handicap, actual_wdl, actual_score, actual_total_goals
        FROM matches
        WHERE match_type LIKE ?
        ORDER BY match_date
    """

    cursor.execute(query, (f'%{league}%',))
    rows = cursor.fetchall()
    conn.close()

    matches = []
    for row in rows:
        matches.append(MatchData(
            match_id=row['match_id'],
            home_team=row['home_team'],
            away_team=row['away_team'],
            match_date=row['match_date'],
            match_type=row['match_type'],
            handicap=row['handicap'],
            actual_wdl=row['actual_wdl'],
            actual_score=row['actual_score'],
            actual_total_goals=row['actual_total_goals']
        ))

    return matches


def load_wdl_history(match_id: str, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载单场比赛胜平负赔率时序数据

    Args:
        match_id: 比赛ID（matches 表的英文 ID，内部自动桥接为中文 ID）
        conn: 数据库连接（可选，传入可复用连接）

    Returns:
        pd.DataFrame: 胜平负赔率时序数据，包含timestamp、win_a、draw、win_b列
    """
    use_external_conn = conn is not None
    if not use_external_conn:
        conn = get_db_connection()

    query = """
        SELECT timestamp, win_a, draw, win_b
        FROM wdl_history
        WHERE match_id = ?
        ORDER BY timestamp
    """

    df = read_sql(query, conn, params=(match_id,))

    # 桥接：英文 match_id 未命中时，通过 match_id_mapping 查中文 sh_match_id
    if df.empty:
        resolved_id = _resolve_match_id_for_history(match_id, conn)
        if resolved_id != match_id:
            df = read_sql(query, conn, params=(resolved_id,))

    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', errors='coerce')

    if not use_external_conn:
        conn.close()

    return df


def load_handicap_history(match_id: str, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载单场比赛让球赔率时序数据

    Args:
        match_id: 比赛ID（matches 表的英文 ID，内部自动桥接为中文 ID）
        conn: 数据库连接（可选）

    Returns:
        pd.DataFrame: 让球赔率时序数据，包含timestamp、hcp_win、hcp_draw、hcp_lose列
    """
    use_external_conn = conn is not None
    if not use_external_conn:
        conn = get_db_connection()

    query = """
        SELECT timestamp, hcp_win, hcp_draw, hcp_lose
        FROM handicap_history
        WHERE match_id = ?
        ORDER BY timestamp
    """

    df = read_sql(query, conn, params=(match_id,))

    if df.empty:
        resolved_id = _resolve_match_id_for_history(match_id, conn)
        if resolved_id != match_id:
            df = read_sql(query, conn, params=(resolved_id,))

    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', errors='coerce')

    if not use_external_conn:
        conn.close()

    return df


def load_total_goals_history(match_id: str, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载单场比赛总进球赔率时序数据

    Args:
        match_id: 比赛ID（matches 表的英文 ID，内部自动桥接为中文 ID）
        conn: 数据库连接（可选）

    Returns:
        pd.DataFrame: 总进球赔率时序数据，列名为timestamp、0、1、2、3、4、5、6、7+
    """
    use_external_conn = conn is not None
    if not use_external_conn:
        conn = get_db_connection()

    query = """
        SELECT timestamp, goals_0, goals_1, goals_2, goals_3,
               goals_4, goals_5, goals_6, goals_7_plus
        FROM total_goals_history
        WHERE match_id = ?
        ORDER BY timestamp
    """

    df = read_sql(query, conn, params=(match_id,))

    if df.empty:
        resolved_id = _resolve_match_id_for_history(match_id, conn)
        if resolved_id != match_id:
            df = read_sql(query, conn, params=(resolved_id,))

    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', errors='coerce')
    df.columns = ['timestamp', '0', '1', '2', '3', '4', '5', '6', '7+']

    if not use_external_conn:
        conn.close()

    return df


def load_score_history(match_id: str, conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载单场比赛比分赔率时序数据

    Args:
        match_id: 比赛ID（matches 表的英文 ID，内部自动桥接为中文 ID）
        conn: 数据库连接（可选）

    Returns:
        pd.DataFrame: 比分赔率时序数据，包含timestamp、score、odds列
    """
    use_external_conn = conn is not None
    if not use_external_conn:
        conn = get_db_connection()

    query = """
        SELECT timestamp, score, odds
        FROM score_history
        WHERE match_id = ?
        ORDER BY timestamp
    """

    df = read_sql(query, conn, params=(match_id,))

    if df.empty:
        resolved_id = _resolve_match_id_for_history(match_id, conn)
        if resolved_id != match_id:
            df = read_sql(query, conn, params=(resolved_id,))

    df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed', errors='coerce')

    if not use_external_conn:
        conn.close()

    return df


def load_all_match_data(match_id: str) -> Tuple[Optional[MatchData], Optional[OddsTemporalData]]:
    """
    加载单场比赛的完整数据（基本信息+赔率时序）

    Args:
        match_id: 比赛ID

    Returns:
        Tuple[MatchData, OddsTemporalData]: 比赛数据和赔率数据
    """
    conn = get_db_connection()

    # 加载比赛基本信息
    cursor = conn.cursor()
    cursor.execute("""
        SELECT match_id, home_team, away_team, match_date, match_type,
               handicap, actual_wdl, actual_score, actual_total_goals
        FROM matches
        WHERE match_id = ?
    """, (match_id,))

    row = cursor.fetchone()
    if not row:
        conn.close()
        return None, None

    match_data = MatchData(
        match_id=row['match_id'],
        home_team=row['home_team'],
        away_team=row['away_team'],
        match_date=row['match_date'],
        match_type=row['match_type'],
        handicap=row['handicap'],
        actual_wdl=row['actual_wdl'],
        actual_score=row['actual_score'],
        actual_total_goals=row['actual_total_goals']
    )

    # 加载赔率时序数据
    wdl_df = load_wdl_history(match_id, conn)
    hcp_df = load_handicap_history(match_id, conn)
    tg_df = load_total_goals_history(match_id, conn)
    score_df = load_score_history(match_id, conn)

    # 获取所有时间点
    all_timestamps = set()
    if not wdl_df.empty:
        all_timestamps.update(wdl_df['timestamp'].tolist())
    if not hcp_df.empty:
        all_timestamps.update(hcp_df['timestamp'].tolist())
    if not tg_df.empty:
        all_timestamps.update(tg_df['timestamp'].tolist())
    if not score_df.empty:
        all_timestamps.update(score_df['timestamp'].tolist())

    sorted_timestamps = sorted(all_timestamps)

    conn.close()

    odds_data = OddsTemporalData(
        match_id=match_id,
        timestamps=[ts.strftime('%Y-%m-%d %H:%M:%S') for ts in sorted_timestamps],
        wdl_data=wdl_df,
        handicap_data=hcp_df,
        total_goals_data=tg_df,
        score_data=score_df
    )

    return match_data, odds_data


def load_league_statistics(league: str) -> Dict:
    """
    加载联赛统计信息

    Args:
        league: 联赛名称

    Returns:
        Dict: 统计信息字典
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    stats = {}

    # 比赛数量
    cursor.execute("""
        SELECT COUNT(*) FROM matches WHERE match_type LIKE ?
    """, (f'%{league}%',))
    stats['match_count'] = cursor.fetchone()[0]

    # 日期范围
    cursor.execute("""
        SELECT MIN(match_date), MAX(match_date) FROM matches WHERE match_type LIKE ?
    """, (f'%{league}%',))
    dates = cursor.fetchone()
    stats['date_range'] = f"{dates[0]} ~ {dates[1]}"

    # 涉及球队
    cursor.execute("""
        SELECT COUNT(DISTINCT home_team) FROM matches WHERE match_type LIKE ?
    """, (f'%{league}%',))
    stats['team_count'] = cursor.fetchone()[0]

    # 赔率统计
    cursor.execute("""
        SELECT COUNT(*) FROM wdl_history WHERE match_id IN
        (SELECT match_id FROM matches WHERE match_type LIKE ?)
    """, (f'%{league}%',))
    stats['wdl_record_count'] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM handicap_history WHERE match_id IN
        (SELECT match_id FROM matches WHERE match_type LIKE ?)
    """, (f'%{league}%',))
    stats['handicap_record_count'] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM total_goals_history WHERE match_id IN
        (SELECT match_id FROM matches WHERE match_type LIKE ?)
    """, (f'%{league}%',))
    stats['total_goals_record_count'] = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM score_history WHERE match_id IN
        (SELECT match_id FROM matches WHERE match_type LIKE ?)
    """, (f'%{league}%',))
    stats['score_record_count'] = cursor.fetchone()[0]

    conn.close()

    return stats


def get_match_ids_by_date_range(start_date: str, end_date: str, league: str) -> List[str]:
    """
    根据日期范围获取比赛ID列表

    Args:
        start_date: 开始日期（YYYY-MM-DD）
        end_date: 结束日期（YYYY-MM-DD）
        league: 联赛名称

    Returns:
        List[str]: 比赛ID列表
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT match_id FROM matches
        WHERE match_date BETWEEN ? AND ?
        AND match_type LIKE ?
        ORDER BY match_date
    """

    cursor.execute(query, (start_date, end_date, f'%{league}%'))
    match_ids = [row['match_id'] for row in cursor.fetchall()]

    conn.close()

    return match_ids


def get_match_ids_with_complete_odds(league: str) -> List[str]:
    """
    获取具有完整赔率数据的比赛ID列表（胜平负+让球+总进球+比分）

    Args:
        league: 联赛名称

    Returns:
        List[str]: 比赛ID列表
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT m.match_id
        FROM matches m
        LEFT JOIN match_id_mapping mim ON m.match_id = mim.matches_match_id
        JOIN wdl_history w ON COALESCE(mim.sh_match_id, m.match_id) = w.match_id
        JOIN handicap_history h ON COALESCE(mim.sh_match_id, m.match_id) = h.match_id
        JOIN total_goals_history t ON COALESCE(mim.sh_match_id, m.match_id) = t.match_id
        JOIN score_history s ON COALESCE(mim.sh_match_id, m.match_id) = s.match_id
        WHERE m.match_type LIKE ?
        GROUP BY m.match_id
        HAVING COUNT(DISTINCT w.timestamp) >= 2
        AND COUNT(DISTINCT h.timestamp) >= 2
        AND COUNT(DISTINCT t.timestamp) >= 2
        AND COUNT(DISTINCT s.timestamp) >= 2
    """

    cursor.execute(query, (f'%{league}%',))
    match_ids = [row['match_id'] for row in cursor.fetchall()]

    conn.close()

    return match_ids