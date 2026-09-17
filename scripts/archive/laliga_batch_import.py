"""
批量导入脚本 - 西甲2025-2026赛季详细赔率时序数据

数据源: 西甲2025-2026完整时序赔率.txt
存储目标: odds_timing.db
参考: seriea_batch_import.py, reusable_import.py

数据格式规范：
=============
每场比赛数据结构：
{
    'match_id': str,           # 比赛ID，格式: YYYY-MM-DD_主队_客队（中文队名）
    'home_team': str,          # 主队名称（中文）
    'away_team': str,          # 客队名称（中文）
    'match_date': str,         # 比赛日期，格式: YYYY-MM-DD
    'match_time': str,         # 比赛时间，格式: HH:MM（可选）
    'league': str,             # 联赛名称: '西甲2025-2026赛季'
    'round': int,              # 轮次（1-38）
    'status': str,             # 比赛状态: 'completed'
    'source': str,             # 数据源: 'LALIGA_2025_26'
    
    'wdl_timing': list,        # 胜平负赔率时序 [(timestamp, win_a, draw, win_b), ...]
    'handicap_timing': list,   # 让球赔率时序 [(timestamp, handicap, hcp_win, hcp_draw, hcp_lose), ...]
    'score_timing': dict,      # 比分赔率时序 {'timestamp': [(score, odds), ...], ...}
    'total_goals_timing': list, # 总进球赔率时序 [(timestamp, g0, g1, g2, g3, g4, g5, g6, g7p), ...]
    
    'result': dict,            # 比赛结果
        {
            'actual_score': str,       # 实际比分，格式: X:Y
            'actual_wdl': str,         # 胜平负结果: '胜'/'平'/'负'（主队视角）
            'actual_handicap': str,    # 让球结果: '胜'/'平'/'负'
            'actual_total_goals': int, # 总进球数
            'verified': int            # 是否已验证: 0/1
        }
}
"""

import sqlite3
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# 添加路径以导入 reusable_import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reusable_import import import_match

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "odds_timing.db"
DATA_FILE = BASE_DIR / "data" / "西甲2025-2026完整时序赔率.txt"
LOG_FILE = BASE_DIR / "data" / "laliga_import_log.md"
SOURCE = 'LALIGA_2025_26'
LEAGUE_NAME = '西甲2025-2026赛季'

# 西甲球队英文->中文名称映射（与rebuild_team_mapping.py保持一致）
LALIGA_TEAM_MAP = {
    'Alaves': '阿拉维斯',
    'Ath Bilbao': '毕尔巴鄂',
    'Ath Madrid': '马德里竞技',
    'Barcelona': '巴塞罗那',
    'Betis': '皇家贝蒂斯',
    'Celta': '塞尔塔',
    'Elche': '埃尔切',
    'Espanol': '西班牙人',
    'Getafe': '赫塔费',
    'Girona': '赫罗纳',
    'Levante': '莱万特',
    'Mallorca': '马洛卡',
    'Osasuna': '奥萨苏纳',
    'Oviedo': '奥维耶多',
    'Real Madrid': '皇家马德里',
    'Sociedad': '皇家社会',
    'Sevilla': '塞维利亚',
    'Valencia': '瓦伦西亚',
    'Vallecano': '巴列卡诺',
    'Villarreal': '比利亚雷亚尔',
}


def normalize_team_name(eng_name):
    """将英文队名转换为中文标准名"""
    eng_name = eng_name.strip()
    # 精确匹配
    if eng_name in LALIGA_TEAM_MAP:
        return LALIGA_TEAM_MAP[eng_name]
    # 尝试模糊匹配（处理多余空格等）
    for key, value in LALIGA_TEAM_MAP.items():
        if key.lower() == eng_name.lower():
            return value
    # 尝试部分匹配
    for key, value in LALIGA_TEAM_MAP.items():
        if key.lower() in eng_name.lower() or eng_name.lower() in key.lower():
            return value
    print(f"  ⚠️ 无法映射球队名称: '{eng_name}'")
    return eng_name


def parse_score_result(score_str):
    """解析比分字符串 'X:Y' -> (int, int)"""
    parts = score_str.strip().split(':')
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return None, None


def determine_wdl_result(home_goals, away_goals):
    """根据比分判断胜平负结果（主队视角）"""
    if home_goals > away_goals:
        return '胜'
    elif home_goals < away_goals:
        return '负'
    else:
        return '平'


def determine_handicap_result(handicap_str, handicap_value, home_goals, away_goals):
    """根据让球盘口和比分判断让球结果"""
    # handicap_value: 正数表示客队让球(+X), 负数表示主队让球(-X)
    # 主队视角的让球后结果
    adjusted_home = home_goals + handicap_value
    if adjusted_home > away_goals:
        return '胜'
    elif adjusted_home < away_goals:
        return '负'
    else:
        return '平'


def parse_handicap_text(text):
    """解析让球结果文本，如 '(-1)胜'、'(+1)负'、'(-2)负'"""
    text = text.strip()
    match = re.match(r'\(([+-]\d+)\)([胜负平])', text)
    if match:
        handicap = int(match.group(1))
        result = match.group(2)
        return handicap, result
    # 尝试匹配格式如 "(-1)" 后面跟结果
    match2 = re.match(r'\((-\d+)\)\s*([胜负平])', text)
    if match2:
        handicap = int(match2.group(1))
        result = match2.group(2)
        return handicap, result
    match3 = re.match(r'\((\+\d+)\)\s*([胜负平])', text)
    if match3:
        handicap = int(match3.group(1))
        result = match3.group(2)
        return handicap, result
    return 0, '平'


def parse_data_file():
    """解析西甲数据文件，返回比赛数据列表"""
    print(f"正在读取数据文件: {DATA_FILE}")
    
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 按空行分割成比赛段落
    # 每场比赛以 "2025/2026 Regular Season" 开头
    matches = []
    blocks = re.split(r'\n(?=2025/2026 Regular Season)', content)
    
    for block in blocks:
        block = block.strip()
        if not block or not block.startswith('2025/2026 Regular Season'):
            continue
        
        match_data = parse_single_match(block)
        if match_data:
            matches.append(match_data)
    
    print(f"共解析 {len(matches)} 场比赛数据")
    return matches


def parse_single_match(block):
    """解析单场比赛数据块"""
    lines = block.split('\n')
    if len(lines) < 3:
        return None
    
    # 解析头部信息
    # 格式: "2025/2026 Regular Season 第X轮 YYYY-MM-DD HH:MM"
    header_match = re.match(
        r'2025/2026 Regular Season 第(\d+)轮\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})',
        lines[0].strip()
    )
    if not header_match:
        print(f"  ⚠️ 无法解析头部: {lines[0][:60]}")
        return None
    
    round_num = int(header_match.group(1))
    match_date = header_match.group(2)
    match_time = header_match.group(3)
    
    # 解析球队信息
    # 格式: "西甲 La Liga\tHomeTeam\tAwayTeam"
    team_line = lines[1].strip()
    team_parts = team_line.split('\t')
    if len(team_parts) >= 3:
        home_team_eng = team_parts[1].strip()
        away_team_eng = team_parts[2].strip()
    else:
        # 尝试用空格分割
        team_parts = team_line.split()
        if len(team_parts) >= 4:
            home_team_eng = team_parts[2].strip()
            away_team_eng = team_parts[3].strip()
        else:
            print(f"  ⚠️ 无法解析球队行: {team_line}")
            return None
    
    # 映射为中文队名
    home_team = normalize_team_name(home_team_eng)
    away_team = normalize_team_name(away_team_eng)
    
    # 构建match_id
    match_id = f"{match_date}_{home_team}_{away_team}"
    
    # 解析数据块
    wdl_timing = []
    handicap_timing = []
    score_timing = {}
    total_goals_timing = {}
    result = None
    
    # 查找开奖结果位置
    kaijiang_idx = -1
    for i, line in enumerate(lines):
        if '开奖结果' in line:
            kaijiang_idx = i
            break
    
    if kaijiang_idx == -1:
        print(f"  ⚠️ 找不到开奖结果: {match_id}")
        return None
    
    # 解析开奖结果
    result_lines = []
    for i in range(kaijiang_idx, min(kaijiang_idx + 10, len(lines))):
        result_lines.append(lines[i])
        if i > kaijiang_idx and '固定奖金' in lines[i]:
            break
    
    result = parse_result_section(result_lines, home_team, away_team)
    
    # 解析胜平负赔率
    wdl_section = extract_section(lines, '胜平负固定奖金', '让球胜平负固定奖金')
    if wdl_section:
        wdl_timing = parse_wdl_odds(wdl_section)
    
    # 解析让球赔率
    hcp_section = extract_section(lines, '让球胜平负固定奖金', '比分固定奖金')
    if hcp_section:
        handicap_timing = parse_handicap_odds(hcp_section)
    
    # 解析比分赔率
    score_section = extract_section(lines, '比分固定奖金', '总进球固定奖金')
    if score_section:
        score_timing = parse_score_odds(score_section)
    
    # 解析总进球赔率
    tg_section = extract_section(lines, '总进球固定奖金', None)
    if tg_section:
        total_goals_timing = parse_total_goals_odds(tg_section)
    
    match_data = {
        'match_id': match_id,
        'home_team': home_team,
        'away_team': away_team,
        'match_date': match_date,
        'match_time': match_time,
        'league': LEAGUE_NAME,
        'round': round_num,
        'status': 'completed',
        'source': SOURCE,
        'wdl_timing': wdl_timing,
        'handicap_timing': handicap_timing,
        'score_timing': score_timing,
        'total_goals_timing': total_goals_timing,
        'result': result,
    }
    
    return match_data


def extract_section(lines, start_marker, end_marker):
    """从行列表中提取指定段落在start_marker和end_marker之间的内容"""
    start_idx = -1
    end_idx = len(lines)
    
    for i, line in enumerate(lines):
        if start_marker in line:
            start_idx = i
            break
    
    if start_idx == -1:
        return None
    
    if end_marker:
        for i in range(start_idx + 1, len(lines)):
            if end_marker in lines[i]:
                end_idx = i
                break
    
    return lines[start_idx:end_idx]


def parse_result_section(lines, home_team, away_team):
    """解析开奖结果部分
    
    数据格式:
    开奖结果
    游戏	开奖结果	奖金
    胜平负	负	3.55
    让球胜平负	(-1)负	1.72
    比分	1:3	30.00
    总进球	4	5.65
    """
    result = {
        'actual_score': '',
        'actual_wdl': '平',
        'actual_handicap': '平',
        'actual_total_goals': 0,
        'verified': 1,
    }
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        parts = line.split('\t')
        
        # 让球胜平负（先检查，因为包含"胜平负"子串）
        if parts[0] == '让球胜平负' and len(parts) >= 2:
            hcp_text = parts[1].strip()
            hcp, hcp_result = parse_handicap_text(hcp_text)
            result['actual_handicap'] = hcp_result
            result['_handicap'] = hcp
        
        # 胜平负（排除"让球胜平负"）
        elif parts[0] == '胜平负' and len(parts) >= 2:
            wdl_result = parts[1].strip()
            if wdl_result in ['胜', '平', '负']:
                result['actual_wdl'] = wdl_result
        
        # 比分
        elif parts[0] == '比分' and len(parts) >= 2:
            score = parts[1].strip()
            if ':' in score:
                result['actual_score'] = score
                hg, ag = parse_score_result(score)
                if hg is not None:
                    result['actual_total_goals'] = hg + ag
        
        # 总进球
        elif parts[0] == '总进球' and len(parts) >= 2:
            try:
                tg = int(parts[1].strip())
                result['actual_total_goals'] = tg
            except ValueError:
                pass
    
    return result


def parse_wdl_odds(lines):
    """解析胜平负赔率时序数据"""
    wdl_timing = []
    
    # 跳过标题行（"胜平负固定奖金" 和 "发布时间\t胜\t平\t负"）
    data_start = 0
    for i, line in enumerate(lines):
        if '发布时间' in line and '胜' in line and '平' in line and '负' in line:
            data_start = i + 1
            break
    
    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line or '让球' in line or '比分' in line or '总进球' in line:
            break
        
        # 格式: "2025-08-14 13:50:32\t2.01\t3.13\t3.20"
        parts = line.split('\t')
        if len(parts) >= 4:
            try:
                ts = parts[0].strip()
                win_a = float(parts[1].strip())
                draw = float(parts[2].strip())
                win_b = float(parts[3].strip())
                wdl_timing.append((ts, win_a, draw, win_b))
            except (ValueError, IndexError):
                continue
    
    return wdl_timing


def parse_handicap_odds(lines):
    """解析让球赔率时序数据"""
    handicap_timing = []
    
    # 让球盘口信息在第一行数据之前
    handicap_value = 0
    for line in lines:
        line = line.strip()
        if line.startswith('让球') and ('+' in line or '-' in line):
            # 提取让球值
            match = re.search(r'让球([+-]\d+)', line)
            if match:
                handicap_value = int(match.group(1))
            break
    
    # 跳过标题行
    data_start = 0
    for i, line in enumerate(lines):
        if '发布时间' in line and '胜' in line and '平' in line and '负' in line:
            data_start = i + 1
            break
    
    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line or '比分' in line or '总进球' in line:
            break
        
        # 格式: "2025-08-14 13:50:32\t4.35\t3.65\t1.60"
        parts = line.split('\t')
        if len(parts) >= 4:
            try:
                ts = parts[0].strip()
                hcp_win = float(parts[1].strip())
                hcp_draw = float(parts[2].strip())
                hcp_lose = float(parts[3].strip())
                handicap_timing.append((ts, handicap_value, hcp_win, hcp_draw, hcp_lose))
            except (ValueError, IndexError):
                continue
    
    return handicap_timing


def parse_score_odds(lines):
    """解析比分赔率时序数据
    
    比分赔率格式比较特殊：
    - 每个时间点有多行（胜/平/负分区）
    - 第一行是标题行，包含所有比分
    - 后续行是对应赔率值
    """
    score_timing = {}
    
    # 找到所有发布时间标记
    timestamp_positions = []
    for i, line in enumerate(lines):
        if '发布时间' in line and re.search(r'\d{4}-\d{2}-\d{2}', line):
            ts_match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
            if ts_match:
                ts = ts_match.group(1)
                timestamp_positions.append((i, ts))
    
    if not timestamp_positions:
        return score_timing
    
    # 为每个时间点解析比分赔率
    for ts_idx, (start_line_idx, timestamp) in enumerate(timestamp_positions):
        # 找到下一个时间点或总进球开始的位置
        if ts_idx + 1 < len(timestamp_positions):
            end_line_idx = timestamp_positions[ts_idx + 1][0]
        else:
            end_line_idx = len(lines)
            # 查找总进球部分
            for i in range(start_line_idx + 1, len(lines)):
                if '总进球固定奖金' in lines[i]:
                    end_line_idx = i
                    break
        
        section_lines = lines[start_line_idx:end_line_idx]
        scores = parse_score_section(section_lines)
        
        if scores:
            score_timing[timestamp] = scores
    
    return score_timing


def parse_score_section(lines):
    """解析单个时间点的比分赔率
    
    格式示例:
    发布时间	2025-08-14   13:50:32	
    1 : 0	2 : 0	2 : 1	3 : 0	3 : 1	3 : 2	4 : 0	4 : 1	4 : 2	5 : 0	5 : 1	5 : 2	胜其它
    6.50	9.00	7.00	18.00	16.00	28.00	50.00	50.00	90.00	150.0	150.0	250.0	90.00
    0 : 0	1 : 1	2 : 2	3 : 3	平其它	
    10.00	5.90	13.00	65.00	400.0
    0 : 1	0 : 2	1 : 2	0 : 3	1 : 3	2 : 3	0 : 4	1 : 4	2 : 4	0 : 5	1 : 5	2 : 5	负其它
    8.50	15.00	10.00	50.00	30.00	35.00	150.0	100.0	150.0	400.0	400.0	600.0	150.0
    """
    scores = []
    
    # 跳过发布时间行
    data_lines = []
    for line in lines:
        line = line.strip()
        if not line or '发布时间' in line:
            continue
        data_lines.append(line)
    
    if not data_lines:
        return scores
    
    # 比分赔率按三行一组排列（标题行、赔率行可能交叉）
    # 结构: 标题行(比分) -> 赔率行 -> 标题行(比分) -> 赔率行 -> ...
    # 以第一个 "胜其它" 结束第一组，"平其它" 结束第二组，"负其它" 结束第三组
    
    i = 0
    while i < len(data_lines):
        line = data_lines[i].strip()
        
        # 检查是否是标题行（包含 "胜其它"、"平其它" 或 "负其它"）
        if '胜其它' in line or '平其它' in line or '负其它' in line:
            # 这是标题行，下一行是赔率
            score_labels = line.split('\t')
            
            # 清洗标签 - 去除空格
            cleaned_labels = []
            for label in score_labels:
                label = label.strip()
                if label and ':' in label:
                    # 标准化比分格式 X : Y -> X:Y
                    label = re.sub(r'\s*:\s*', ':', label)
                elif label in ['胜其它', '平其它', '负其它']:
                    # 保持原样
                    pass
                if label:
                    cleaned_labels.append(label)
            
            # 获取赔率行
            if i + 1 < len(data_lines):
                odds_line = data_lines[i + 1].strip()
                odds_values = odds_line.split('\t')
                
                # 配对比分和赔率
                for j in range(min(len(cleaned_labels), len(odds_values))):
                    try:
                        odds = float(odds_values[j].strip())
                        label = cleaned_labels[j]
                        if label and odds > 0:
                            scores.append((label, odds))
                    except (ValueError, IndexError):
                        continue
                i += 2
            else:
                i += 1
        else:
            i += 1
    
    return scores


def parse_total_goals_odds(lines):
    """解析总进球赔率时序数据
    
    格式:
    发布时间	0	1	2	3	4	5	6	7+
    2025-08-14 13:50:32	10.00	4.20	3.10	3.60	6.00	12.00	25.00	40.00
    """
    tg_timing = []
    
    # 跳过标题行
    data_start = 0
    for i, line in enumerate(lines):
        if '发布时间' in line and '0' in line and '7+' in line:
            data_start = i + 1
            break
    
    for i in range(data_start, len(lines)):
        line = lines[i].strip()
        if not line:
            break
        
        # 格式: "2025-08-14 13:50:32\t10.00\t4.20\t3.10\t3.60\t6.00\t12.00\t25.00\t40.00"
        parts = line.split('\t')
        if len(parts) >= 9:
            try:
                ts = parts[0].strip()
                g0 = float(parts[1].strip())
                g1 = float(parts[2].strip())
                g2 = float(parts[3].strip())
                g3 = float(parts[4].strip())
                g4 = float(parts[5].strip())
                g5 = float(parts[6].strip())
                g6 = float(parts[7].strip())
                g7p = float(parts[8].strip())
                tg_timing.append((ts, g0, g1, g2, g3, g4, g5, g6, g7p))
            except (ValueError, IndexError):
                continue
    
    return tg_timing


def check_match_exists(match_id):
    """检查比赛是否已存在于数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM matches WHERE match_id=?", (match_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def validate_match_data(match_data):
    """验证比赛数据格式和质量"""
    errors = []
    
    # 必需字段检查
    required_fields = ['match_id', 'home_team', 'away_team', 'match_date', 
                      'league', 'status', 'source', 'wdl_timing']
    for field in required_fields:
        if field not in match_data or not match_data[field]:
            errors.append(f"缺少必需字段: {field}")
    
    # 球队名称验证
    if match_data.get('home_team') not in LALIGA_TEAM_MAP.values():
        errors.append(f"主队名称不规范: {match_data.get('home_team')}")
    if match_data.get('away_team') not in LALIGA_TEAM_MAP.values():
        errors.append(f"客队名称不规范: {match_data.get('away_team')}")
    
    # 日期格式验证
    if 'match_date' in match_data:
        date_str = match_data['match_date']
        if len(date_str) != 10 or date_str[4] != '-' or date_str[7] != '-':
            errors.append(f"日期格式错误，应为YYYY-MM-DD: {date_str}")
    
    # 赔率数据质量验证
    wdl_timing = match_data.get('wdl_timing', [])
    if len(wdl_timing) < 3:
        errors.append(f"胜平负赔率记录不足（至少3条）: {len(wdl_timing)}条")
    else:
        for ts, win_a, draw, win_b in wdl_timing:
            if not (1.01 <= win_a <= 200.0):
                errors.append(f"主胜赔率超出范围: {win_a}")
                break
            if not (1.01 <= draw <= 200.0):
                errors.append(f"平局赔率超出范围: {draw}")
                break
            if not (1.01 <= win_b <= 200.0):
                errors.append(f"客胜赔率超出范围: {win_b}")
                break
    
    hcp_timing = match_data.get('handicap_timing', [])
    if len(hcp_timing) > 0 and len(hcp_timing) < 3:
        errors.append(f"让球赔率记录不足（至少3条）: {len(hcp_timing)}条")
    
    score_timing = match_data.get('score_timing', {})
    total_scores = sum(len(scores) for scores in score_timing.values())
    if total_scores > 0 and total_scores < 10:
        errors.append(f"比分赔率选项不足（至少10个）: {total_scores}个")
    
    return errors


def append_to_log(match_data, success=True, error_msg=None):
    """追加导入日志"""
    match_id = match_data['match_id']
    home_team = match_data['home_team']
    away_team = match_data['away_team']
    match_date = match_data['match_date']
    match_round = match_data.get('round', '未知轮次')
    
    log_entry = f"""### {match_id}

**基本信息**
- **比赛**: {home_team} vs {away_team}
- **日期**: {match_date} {match_data.get('match_time', '')}
- **轮次**: 第{match_round}轮
- **数据源**: {match_data['source']}

**赔率数据统计**
| 数据类型 | 记录数 |
|---------|--------|
| 胜平负赔率 | {len(match_data.get('wdl_timing', []))} 条 |
| 让球赔率 | {len(match_data.get('handicap_timing', []))} 条 |
| 比分赔率 | {sum(len(scores) for scores in match_data.get('score_timing', {}).values())} 条 |
| 总进球赔率 | {len(match_data.get('total_goals_timing', []))} 条 |

"""
    
    if success:
        result = match_data.get('result')
        if result:
            log_entry += f"""**比赛结果**
- **比分**: {result['actual_score']}
- **胜平负**: {result['actual_wdl']}
- **让球结果**: {result['actual_handicap']}
- **总进球**: {result['actual_total_goals']}

"""
        log_entry += f"**导入状态**: ✅ 成功\n\n---\n\n"
    else:
        log_entry += f"**导入状态**: ❌ 失败\n**错误信息**: {error_msg}\n\n---\n\n"
    
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            existing_content = f.read()
        if f"### {match_id}" in existing_content:
            start_idx = existing_content.find(f"### {match_id}")
            end_idx = existing_content.find("\n---\n\n", start_idx)
            if end_idx != -1:
                end_idx += len("\n---\n\n")
                new_content = existing_content[:start_idx] + log_entry + existing_content[end_idx:]
            else:
                new_content = existing_content[:start_idx] + log_entry
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.write(new_content)
        else:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(log_entry)
    else:
        header = """# 西甲2025-2026赛季比赛数据导入日志

本文件记录所有通过批量导入脚本导入的比赛数据，用于模型训练和数据分析。

## 数据说明

- **联赛**: 西甲2025-2026赛季
- **数据类型**: 胜平负赔率、让球胜平负赔率、比分赔率、总进球赔率、比赛结果
- **数据源**: LALIGA_2025_26（西甲赔率数据）
- **更新时间**: 自动更新

---

"""
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(header + log_entry)


def get_db_statistics():
    """获取数据库统计信息"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE league LIKE '%西甲%'")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("SELECT round, COUNT(*) FROM matches WHERE league LIKE '%西甲%' GROUP BY round ORDER BY round")
    round_stats = {row[0]: row[1] for row in cursor.fetchall()}
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE league LIKE '%西甲%' AND status='completed'")
    completed_matches = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%西甲%')")
    wdl_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM handicap_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%西甲%')")
    hcp_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM score_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%西甲%')")
    score_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%西甲%')")
    tg_count = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'total_matches': total_matches,
        'round_stats': round_stats,
        'completed_matches': completed_matches,
        'wdl_count': wdl_count,
        'hcp_count': hcp_count,
        'score_count': score_count,
        'tg_count': tg_count,
    }


def refresh_summary():
    """刷新日志摘要"""
    stats = get_db_statistics()
    
    round_lines = []
    for round_num in sorted(stats['round_stats'].keys()):
        count = stats['round_stats'][round_num]
        round_lines.append(f"| 第{round_num}轮 | {count} 场 |")
    
    summary = f"""## 统计概览

| 统计项 | 数量 |
|-------|------|
| 总比赛数 | {stats['total_matches']} 场 |
{chr(10).join(round_lines)}
| 已完成比赛 | {stats['completed_matches']} 场 |
| 胜平负赔率记录 | {stats['wdl_count']} 条 |
| 让球赔率记录 | {stats['hcp_count']} 条 |
| 比分赔率记录 | {stats['score_count']} 条 |
| 总进球赔率记录 | {stats['tg_count']} 条 |

"""
    
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            content = f.read()
        
        start_marker = "## 统计概览"
        end_marker = "\n---\n"
        
        start_idx = content.find(start_marker)
        end_idx = content.find(end_marker, start_idx)
        
        if start_idx != -1 and end_idx != -1:
            new_content = content[:start_idx] + summary + content[end_idx:]
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.write(new_content)


def batch_import_matches(matches_data, force=False):
    """批量导入比赛数据"""
    success_count = 0
    fail_count = 0
    skip_count = 0
    skip_existing = 0
    
    print(f"\n开始批量导入西甲数据，共 {len(matches_data)} 场比赛...\n")
    
    for i, match_data in enumerate(matches_data, 1):
        match_id = match_data['match_id']
        print(f"[{i}/{len(matches_data)}] 处理: {match_data['home_team']} vs {match_data['away_team']} (第{match_data.get('round')}轮)")
        
        try:
            # 验证数据格式
            errors = validate_match_data(match_data)
            if errors:
                print(f"  ❌ 数据验证失败: {', '.join(errors)}")
                append_to_log(match_data, success=False, error_msg=', '.join(errors))
                fail_count += 1
                continue
            
            # 检查是否已存在
            if check_match_exists(match_id):
                skip_existing += 1
                if force:
                    print(f"  ⚠️ 比赛已存在，强制覆盖更新...")
                else:
                    print(f"  ⏭️ 比赛已存在，跳过（使用 --force 强制更新）\n")
                    skip_count += 1
                    continue
            
            # 执行导入
            import_match(match_data)
            append_to_log(match_data, success=True)
            success_count += 1
            print(f"  ✅ 导入成功\n")
            
        except Exception as e:
            print(f"  ❌ 导入失败: {e}\n")
            append_to_log(match_data, success=False, error_msg=str(e))
            fail_count += 1
    
    refresh_summary()
    
    print(f"\n{'='*60}")
    print(f"批量导入完成！")
    print(f"  成功: {success_count}")
    print(f"  失败: {fail_count}")
    print(f"  跳过: {skip_count} (已存在)")
    print(f"  总计: {len(matches_data)}")
    print(f"{'='*60}")
    
    # 显示统计信息
    stats = get_db_statistics()
    print(f"\n数据库统计:")
    print(f"  总比赛数: {stats['total_matches']}")
    print(f"  胜平负赔率记录: {stats['wdl_count']} 条")
    print(f"  让球赔率记录: {stats['hcp_count']} 条")
    print(f"  比分赔率记录: {stats['score_count']} 条")
    print(f"  总进球赔率记录: {stats['tg_count']} 条")
    
    return success_count, fail_count


if __name__ == '__main__':
    import sys
    
    force = False
    if len(sys.argv) > 1 and sys.argv[1] == '--force':
        force = True
    
    # 第一步：解析数据文件
    print("=" * 60)
    print("西甲2025-2026赛季赔率数据批量导入")
    print("=" * 60)
    
    matches_data = parse_data_file()
    
    if not matches_data:
        print("⚠️ 没有解析到任何比赛数据！")
        sys.exit(1)
    
    # 第二步：批量导入
    batch_import_matches(matches_data, force=force)
    
    print("\n✅ 第一步完成！数据已导入 odds_timing.db")
    print("下一步: 运行 import_to_main_db.py 同步到 odds.db")