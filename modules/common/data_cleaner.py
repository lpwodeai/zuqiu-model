"""通用赔率数据清洗模块

负责确保数据质量符合模型训练标准，包含：
- 日期格式统一
- 球队名称标准化（通过注入的 team_mappings 参数）
- 赔率值验证
- 数据完整性检查
- 缺失数据处理

数据质量标准：
- 胜平负/让球/总进球赔率至少2条记录，比分赔率至少10个选项
- 赔率值范围1.01-200.0，按时间顺序排列
- 时间格式统一为'YYYY-MM-DD HH:MM:SS'
- 比分格式保持为'X:Y'
"""

import re
import sqlite3
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from .data_loader import get_db_connection, ODDS_DB_PATH


def normalize_date(date_str: str) -> str:
    """
    统一日期格式为YYYY-MM-DD

    Args:
        date_str: 原始日期字符串

    Returns:
        str: 标准化后的日期字符串
    """
    if not date_str:
        return date_str

    date_str = date_str.strip()

    # 尝试匹配多种日期格式
    patterns = [
        r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$',           # YYYY-MM-DD 或 YYYY/MM/DD
        r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{2}):(\d{2})$',  # 带时间
        r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s+(\d{2}):(\d{2}):(\d{2})$',  # 带完整时间
    ]

    for pattern in patterns:
        match = re.match(pattern, date_str)
        if match:
            year = match.group(1)
            month = match.group(2).zfill(2)
            day = match.group(3).zfill(2)
            return f"{year}-{month}-{day}"

    return date_str


def normalize_team_name(team_name: str, team_mappings: Dict[str, str] = None) -> str:
    """
    统一球队名称为中文

    Args:
        team_name: 原始球队名称
        team_mappings: 球队名称映射表（英文→中文），可选

    Returns:
        str: 标准化后的中文球队名称
    """
    if not team_name:
        return team_name

    team_name = team_name.strip()

    # 检查是否已经是中文（包含非ASCII字符）
    if any(ord(char) > 127 for char in team_name):
        return team_name

    if not team_mappings:
        return team_name

    # 精确匹配映射表
    if team_name in team_mappings:
        return team_mappings[team_name]

    # 模糊匹配（忽略大小写）
    team_lower = team_name.lower()
    for eng, chi in team_mappings.items():
        if eng.lower() in team_lower or team_lower in eng.lower():
            return chi

    return team_name


def validate_odds_range(odds: Optional[float]) -> bool:
    """
    验证赔率值范围是否在1.01-200.0之间

    Args:
        odds: 赔率值

    Returns:
        bool: 是否在有效范围内
    """
    if odds is None:
        return True  # 允许NULL

    try:
        val = float(odds)
        return 1.01 <= val <= 200.0
    except (ValueError, TypeError):
        return False


def validate_score_format(score: str) -> bool:
    """
    验证比分格式是否为'X:Y'

    Args:
        score: 比分字符串

    Returns:
        bool: 是否符合格式
    """
    if not score:
        return True  # 允许空值

    pattern = r'^\d+:\d+$'
    return bool(re.match(pattern, score))


def validate_time_points(match_id: str, min_points: int = 5) -> Dict[str, bool]:
    """
    验证单场比赛的赔率时间点数量是否满足要求

    Args:
        match_id: 比赛ID
        min_points: 最小时间点数量要求

    Returns:
        Dict[str, bool]: 各类赔率的验证结果
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    result = {}

    # 胜平负赔率
    cursor.execute("SELECT COUNT(DISTINCT timestamp) FROM wdl_history WHERE match_id = ?", (match_id,))
    wdl_count = cursor.fetchone()[0]
    result['wdl'] = wdl_count >= min_points

    # 让球赔率
    cursor.execute("SELECT COUNT(DISTINCT timestamp) FROM handicap_history WHERE match_id = ?", (match_id,))
    hcp_count = cursor.fetchone()[0]
    result['handicap'] = hcp_count >= min_points

    # 总进球赔率
    cursor.execute("SELECT COUNT(DISTINCT timestamp) FROM total_goals_history WHERE match_id = ?", (match_id,))
    tg_count = cursor.fetchone()[0]
    result['total_goals'] = tg_count >= min_points

    # 比分赔率
    cursor.execute("SELECT COUNT(DISTINCT timestamp) FROM score_history WHERE match_id = ?", (match_id,))
    score_count = cursor.fetchone()[0]
    result['score'] = score_count >= min_points

    conn.close()

    return result


def detect_missing_data(match_id: str) -> Dict[str, List[str]]:
    """
    检测单场比赛的缺失数据

    Args:
        match_id: 比赛ID

    Returns:
        Dict[str, List[str]]: 缺失数据类型及说明
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    issues = {}

    # 检查比赛基本信息
    cursor.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,))
    match_row = cursor.fetchone()

    if not match_row:
        issues['match_info'] = ['比赛基本信息缺失']
        conn.close()
        return issues

    # 检查必要字段
    missing_fields = []
    if not match_row['home_team']:
        missing_fields.append('home_team')
    if not match_row['away_team']:
        missing_fields.append('away_team')
    if not match_row['match_date']:
        missing_fields.append('match_date')

    if missing_fields:
        issues['missing_fields'] = missing_fields

    # 检查赔率数据
    cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id = ?", (match_id,))
    if cursor.fetchone()[0] == 0:
        issues['wdl_history'] = ['胜平负赔率数据缺失']

    cursor.execute("SELECT COUNT(*) FROM handicap_history WHERE match_id = ?", (match_id,))
    if cursor.fetchone()[0] == 0:
        issues['handicap_history'] = ['让球赔率数据缺失']

    cursor.execute("SELECT COUNT(*) FROM total_goals_history WHERE match_id = ?", (match_id,))
    if cursor.fetchone()[0] == 0:
        issues['total_goals_history'] = ['总进球赔率数据缺失']

    cursor.execute("SELECT COUNT(*) FROM score_history WHERE match_id = ?", (match_id,))
    if cursor.fetchone()[0] == 0:
        issues['score_history'] = ['比分赔率数据缺失']

    # 检查赔率值有效性
    cursor.execute("SELECT timestamp, win_a, draw, win_b FROM wdl_history WHERE match_id = ?", (match_id,))
    wdl_rows = cursor.fetchall()
    invalid_wdl = []
    for row in wdl_rows:
        if not validate_odds_range(row['win_a']):
            invalid_wdl.append(f"win_a={row['win_a']} at {row['timestamp']}")
        if not validate_odds_range(row['draw']):
            invalid_wdl.append(f"draw={row['draw']} at {row['timestamp']}")
        if not validate_odds_range(row['win_b']):
            invalid_wdl.append(f"win_b={row['win_b']} at {row['timestamp']}")

    if invalid_wdl:
        issues['invalid_wdl_odds'] = invalid_wdl

    conn.close()

    return issues


def clean_match_data(match_data: Dict, team_mappings: Dict[str, str] = None) -> Dict:
    """
    清洗单场比赛数据

    Args:
        match_data: 原始比赛数据字典
        team_mappings: 球队名称映射表（英文→中文），可选

    Returns:
        Dict: 清洗后的比赛数据
    """
    cleaned = match_data.copy()

    # 标准化日期
    if 'match_date' in cleaned and cleaned['match_date']:
        cleaned['match_date'] = normalize_date(cleaned['match_date'])

    # 标准化球队名称
    if 'home_team' in cleaned:
        cleaned['home_team'] = normalize_team_name(cleaned['home_team'], team_mappings)
    if 'away_team' in cleaned:
        cleaned['away_team'] = normalize_team_name(cleaned['away_team'], team_mappings)

    # 验证比分格式
    if 'actual_score' in cleaned and cleaned['actual_score']:
        if not validate_score_format(cleaned['actual_score']):
            cleaned['actual_score'] = None

    return cleaned


def clean_odds_data(df: pd.DataFrame, odds_type: str) -> pd.DataFrame:
    """
    清洗赔率数据

    Args:
        df: 原始赔率数据DataFrame
        odds_type: 赔率类型（'wdl', 'handicap', 'total_goals', 'score'）

    Returns:
        pd.DataFrame: 清洗后的赔率数据
    """
    cleaned = df.copy()

    # 移除无效赔率值
    if odds_type == 'wdl':
        for col in ['win_a', 'draw', 'win_b']:
            if col in cleaned.columns:
                cleaned[col] = cleaned[col].apply(
                    lambda x: x if validate_odds_range(x) else np.nan
                )

    elif odds_type == 'handicap':
        for col in ['hcp_win', 'hcp_draw', 'hcp_lose']:
            if col in cleaned.columns:
                cleaned[col] = cleaned[col].apply(
                    lambda x: x if validate_odds_range(x) else np.nan
                )

    elif odds_type == 'total_goals':
        for col in ['0', '1', '2', '3', '4', '5', '6', '7+']:
            if col in cleaned.columns:
                cleaned[col] = cleaned[col].apply(
                    lambda x: x if validate_odds_range(x) else np.nan
                )

    elif odds_type == 'score':
        if 'odds' in cleaned.columns:
            cleaned['odds'] = cleaned['odds'].apply(
                lambda x: x if validate_odds_range(x) else np.nan
            )

    # 按时间排序
    if 'timestamp' in cleaned.columns:
        cleaned['timestamp'] = pd.to_datetime(cleaned['timestamp'])
        cleaned = cleaned.sort_values('timestamp')

    # 移除重复时间点（保留最后一条）
    if 'timestamp' in cleaned.columns:
        cleaned = cleaned.drop_duplicates(subset='timestamp', keep='last')

    return cleaned


def generate_data_quality_report(league: str) -> Dict:
    """
    生成联赛数据质量报告

    Args:
        league: 联赛名称

    Returns:
        Dict: 数据质量报告
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    report = {
        'league': league,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'overall_status': 'pass',
        'issues': [],
        'statistics': {}
    }

    # 获取比赛列表
    cursor.execute("SELECT match_id, home_team, away_team, match_date FROM matches WHERE match_type LIKE ?", (f'%{league}%',))
    matches = cursor.fetchall()

    total_matches = len(matches)
    report['statistics']['total_matches'] = total_matches

    # 统计各类问题
    issues_by_type = {
        'missing_info': 0,
        'invalid_date': 0,
        'invalid_team_name': 0,
        'missing_wdl': 0,
        'missing_handicap': 0,
        'missing_total_goals': 0,
        'missing_score': 0,
        'insufficient_time_points': 0,
        'invalid_odds': 0
    }

    for match in matches:
        match_id = match['match_id']

        # 检查日期格式
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', match['match_date']):
            issues_by_type['invalid_date'] += 1

        # 检查球队名称是否为中文
        if not any(ord(char) > 127 for char in match['home_team']):
            issues_by_type['invalid_team_name'] += 1
        if not any(ord(char) > 127 for char in match['away_team']):
            issues_by_type['invalid_team_name'] += 1

        # 检查赔率数据存在性
        cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id = ?", (match_id,))
        if cursor.fetchone()[0] == 0:
            issues_by_type['missing_wdl'] += 1

        cursor.execute("SELECT COUNT(*) FROM handicap_history WHERE match_id = ?", (match_id,))
        if cursor.fetchone()[0] == 0:
            issues_by_type['missing_handicap'] += 1

        cursor.execute("SELECT COUNT(*) FROM total_goals_history WHERE match_id = ?", (match_id,))
        if cursor.fetchone()[0] == 0:
            issues_by_type['missing_total_goals'] += 1

        cursor.execute("SELECT COUNT(*) FROM score_history WHERE match_id = ?", (match_id,))
        if cursor.fetchone()[0] == 0:
            issues_by_type['missing_score'] += 1

    report['statistics']['issues_by_type'] = issues_by_type

    # 判断整体状态
    total_issues = sum(issues_by_type.values())
    if total_issues > total_matches * 0.1:
        report['overall_status'] = 'warning'
    elif total_issues > total_matches * 0.3:
        report['overall_status'] = 'fail'

    # 添加问题描述
    if issues_by_type['invalid_date'] > 0:
        report['issues'].append(f"{issues_by_type['invalid_date']}场比赛日期格式不正确")
    if issues_by_type['invalid_team_name'] > 0:
        report['issues'].append(f"{issues_by_type['invalid_team_name']}个球队名称不是中文")
    if issues_by_type['missing_wdl'] > 0:
        report['issues'].append(f"{issues_by_type['missing_wdl']}场比赛缺失胜平负赔率")
    if issues_by_type['missing_handicap'] > 0:
        report['issues'].append(f"{issues_by_type['missing_handicap']}场比赛缺失让球赔率")
    if issues_by_type['missing_total_goals'] > 0:
        report['issues'].append(f"{issues_by_type['missing_total_goals']}场比赛缺失总进球赔率")
    if issues_by_type['missing_score'] > 0:
        report['issues'].append(f"{issues_by_type['missing_score']}场比赛缺失比分赔率")

    conn.close()

    return report


def clean_all_data(league: str, team_mappings: Dict[str, str] = None, dry_run: bool = True) -> Dict:
    """
    执行完整的数据清洗流程

    Args:
        league: 联赛名称
        team_mappings: 球队名称映射表（英文→中文），可选
        dry_run: 是否为模拟运行（不实际修改数据）

    Returns:
        Dict: 清洗结果报告
    """
    report = {
        'league': league,
        'dry_run': dry_run,
        'cleaned_matches': 0,
        'cleaned_wdl': 0,
        'cleaned_handicap': 0,
        'cleaned_total_goals': 0,
        'cleaned_score': 0,
        'changes_made': []
    }

    if dry_run:
        return report

    conn = get_db_connection()
    cursor = conn.cursor()

    # 获取需要清洗的比赛
    cursor.execute("SELECT match_id, home_team, away_team, match_date FROM matches WHERE match_type LIKE ?", (f'%{league}%',))
    matches = cursor.fetchall()

    for match in matches:
        match_id = match['match_id']
        changes = []

        # 标准化日期
        new_date = normalize_date(match['match_date'])
        if new_date != match['match_date']:
            cursor.execute("UPDATE matches SET match_date = ? WHERE match_id = ?", (new_date, match_id))
            changes.append(f"日期: {match['match_date']} -> {new_date}")

        # 标准化球队名称
        if team_mappings:
            new_home = normalize_team_name(match['home_team'], team_mappings)
            if new_home != match['home_team']:
                cursor.execute("UPDATE matches SET home_team = ? WHERE match_id = ?", (new_home, match_id))
                changes.append(f"主队: {match['home_team']} -> {new_home}")

            new_away = normalize_team_name(match['away_team'], team_mappings)
            if new_away != match['away_team']:
                cursor.execute("UPDATE matches SET away_team = ? WHERE match_id = ?", (new_away, match_id))
                changes.append(f"客队: {match['away_team']} -> {new_away}")

        if changes:
            report['cleaned_matches'] += 1
            report['changes_made'].append({match_id: changes})

    conn.commit()
    conn.close()

    return report