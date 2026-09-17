"""
解析意甲2025-2026赔率时序txt文件并导入数据库

数据源: 意甲2025-2026完整时序赔率.txt
存储目标: odds_timing.db -> odds.db

数据来源格式: 体彩官方赔率数据
"""

import re
import sqlite3
import os
import sys
from datetime import datetime
from pathlib import Path

# 配置
BASE_DIR = Path(__file__).resolve().parent.parent.parent
INPUT_FILE = BASE_DIR / "data" / "意甲2025-2026完整时序赔率.txt"
ODDS_TIMING_DB = BASE_DIR / "data" / "odds_timing.db"
ODDS_DB = BASE_DIR / "data" / "odds.db"

# 意甲球队名称映射（统一格式）
TEAM_NAME_MAPPING = {
    'Inter Milan': 'Inter',
    'AC Milan': 'Milan',
    'Napoli': 'Napoli',
    'Roma': 'Roma',
    'Juventus': 'Juventus',
    'Lazio': 'Lazio',
    'Atalanta': 'Atalanta',
    'Fiorentina': 'Fiorentina',
    'Bologna': 'Bologna',
    'Torino': 'Torino',
    'Udinese': 'Udinese',
    'Genoa': 'Genoa',
    'Sampdoria': 'Sampdoria',
    'Cagliari': 'Cagliari',
    'Sassuolo': 'Sassuolo',
    'Lecce': 'Lecce',
    'Verona': 'Verona',
    'Monza': 'Monza',
    'Cremonese': 'Cremonese',
    'Pisa': 'Pisa',
    'Como': 'Como',
    'Parma': 'Parma',
    'Palermo': 'Palermo',
}


def normalize_team_name(name):
    """标准化球队名称"""
    name = name.strip()
    return TEAM_NAME_MAPPING.get(name, name)


def parse_match_header(line):
    """解析比赛头部信息
    格式: 2025/2026 Regular Season 第X轮 YYYY-MM-DD HH:MM
    """
    pattern = r'2025/2026 Regular Season 第(\d+)轮 (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})'
    match = re.match(pattern, line.strip())
    if match:
        round_num = int(match.group(1))
        match_date = match.group(2)
        match_time = match.group(3)
        return round_num, match_date, match_time
    return None, None, None


def parse_match_teams(lines, start_idx):
    """解析球队信息
    格式: 意甲 Serie A 主队 客队
    """
    for i in range(start_idx, min(start_idx + 5, len(lines))):
        line = lines[i].strip()
        if line.startswith('意甲') or line.startswith('Serie'):
            parts = line.split()
            if len(parts) >= 3:
                # 意甲 Serie A 主队 客队 或 意甲 主队 客队
                if 'Serie' in line:
                    home_team = parts[3] if len(parts) > 3 else parts[2]
                    away_team = parts[4] if len(parts) > 4 else parts[3]
                else:
                    home_team = parts[1]
                    away_team = parts[2]
                return normalize_team_name(home_team), normalize_team_name(away_team), i
    return None, None, start_idx


def safe_float(val, default=None):
    """安全转换为float，处理'--'等特殊值"""
    try:
        if val is None or val == '--' or val == '' or val == '-':
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_result_section(lines, start_idx):
    """解析开奖结果
    返回: {wdl_result, wdl_odds, handicap_result, handicap_line, handicap_odds, score, score_odds, total_goals, total_goals_odds}
    """
    result = {}
    
    for i in range(start_idx, min(start_idx + 10, len(lines))):
        line = lines[i].strip()
        
        # 胜平负结果
        if line.startswith('胜平负') and '开奖结果' not in line:
            parts = line.split()
            if len(parts) >= 2:
                result['wdl_result'] = parts[1]
                if len(parts) >= 3:
                    odds = safe_float(parts[2])
                    if odds is not None:
                        result['wdl_odds'] = odds
        
        # 让球结果
        elif line.startswith('让球胜平负'):
            parts = line.split()
            if len(parts) >= 2:
                hcp_info = parts[1]
                # 解析让球盘口和结果，如 (-1)负 或 (+1)胜
                hcp_match = re.match(r'[(]([+-]?\d+)[)]([胜负平])', hcp_info)
                if hcp_match:
                    result['handicap_line'] = int(hcp_match.group(1))
                    result['handicap_result'] = hcp_match.group(2)
                elif len(hcp_info) > 1 and hcp_info != '--':
                    result['handicap_result'] = hcp_info
                if len(parts) >= 3:
                    odds = safe_float(parts[2])
                    if odds is not None:
                        result['handicap_odds'] = odds
        
        # 比分结果
        elif line.startswith('比分') and '固定奖金' not in line:
            parts = line.split()
            if len(parts) >= 2:
                result['score'] = parts[1]
                if len(parts) >= 3:
                    odds = safe_float(parts[2])
                    if odds is not None:
                        result['score_odds'] = odds
        
        # 总进球结果
        elif line.startswith('总进球') and '固定奖金' not in line:
            parts = line.split()
            if len(parts) >= 2:
                goals_str = parts[1]
                # 处理 "7+" 格式
                if goals_str.endswith('+'):
                    result['total_goals'] = int(goals_str.replace('+', ''))
                else:
                    try:
                        result['total_goals'] = int(goals_str)
                    except ValueError:
                        result['total_goals'] = 7  # 默认值
                if len(parts) >= 3:
                    odds = safe_float(parts[2])
                    if odds is not None:
                        result['total_goals_odds'] = odds
    
    return result


def parse_wdl_odds(lines, start_idx):
    """解析胜平负赔率时序
    格式:
    胜平负固定奖金
    发布时间 胜 平 负
    2025-08-22 09:52:19 1.82 3.10 3.90
    """
    odds_data = []
    in_section = False
    
    for i in range(start_idx, min(start_idx + 30, len(lines))):
        line = lines[i].strip()
        
        if line.startswith('胜平负固定奖金'):
            in_section = True
            continue
        
        if in_section and line.startswith('发布时间'):
            # 这是表头，跳过
            continue
        
        if in_section and line:
            # 检查是否是数据行（以日期开头）
            if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', line):
                parts = line.split()
                if len(parts) >= 4:
                    timestamp = f"{parts[0]} {parts[1]}"
                    win = safe_float(parts[2])
                    draw = safe_float(parts[3])
                    lose = safe_float(parts[4]) if len(parts) > 4 else None
                    if win is not None and draw is not None:
                        odds_data.append((timestamp, win, draw, lose))
            elif not line.startswith('让球') and not line.startswith('比分') and not line.startswith('总进球'):
                # 继续收集数据
                pass
            else:
                # 遇到下一个 section 结束
                break
    
    return odds_data


def parse_handicap_odds(lines, start_idx):
    """解析让球赔率时序
    格式:
    让球胜平负固定奖金
    让球-1 或 让球+1
    发布时间 胜 平 负
    2025-08-22 09:52:18 3.90 3.30 1.76
    """
    odds_data = []
    handicap_line = 0
    in_section = False
    found_handicap = False
    
    for i in range(start_idx, min(start_idx + 30, len(lines))):
        line = lines[i].strip()
        
        if line.startswith('让球胜平负固定奖金'):
            in_section = True
            continue
        
        if in_section and line.startswith('让球'):
            # 让球-1 或 让球+1
            hcp_match = re.match(r'让球([+-])(\d+)', line)
            if hcp_match:
                sign = hcp_match.group(1)
                num = int(hcp_match.group(2))
                handicap_line = -num if sign == '-' else num
                found_handicap = True
            continue
        
        if in_section and line.startswith('发布时间'):
            continue
        
        if in_section and line:
            if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', line):
                parts = line.split()
                if len(parts) >= 4:
                    timestamp = f"{parts[0]} {parts[1]}"
                    win = safe_float(parts[2])
                    draw = safe_float(parts[3])
                    lose = safe_float(parts[4]) if len(parts) > 4 else None
                    if win is not None and draw is not None:
                        odds_data.append((timestamp, handicap_line, win, draw, lose))
            elif not line.startswith('比分') and not line.startswith('总进球'):
                pass
            else:
                break
    
    return odds_data, handicap_line


def parse_score_odds(lines, start_idx):
    """解析比分赔率时序
    
    比分赔率格式复杂，有多个发布时间，每个时间点有一组赔率
    """
    odds_data = []
    in_section = False
    current_timestamp = None
    
    i = start_idx
    while i < min(start_idx + 60, len(lines)):
        line = lines[i].strip()
        
        if line.startswith('比分固定奖金'):
            in_section = True
            i += 1
            continue
        
        if not in_section:
            i += 1
            continue
        
        # 检查是否遇到下一个 section
        if line.startswith('总进球'):
            break
        
        # 发布时间行
        timestamp_match = re.match(r'发布时间\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', line)
        if timestamp_match:
            date_part = timestamp_match.group(1)
            time_part = timestamp_match.group(2)
            current_timestamp = f"{date_part} {time_part}"
            i += 1
            continue
        
        if current_timestamp and line:
            # 检查是否是比分行
            # 1 : 0	2 : 0	2 : 1	...
            if ' : ' in line:
                headers = line.split('\t')
                # 读取下一行的赔率
                if i + 1 < len(lines):
                    odds_line = lines[i + 1].strip()
                    odds_values = odds_line.split('\t')
                    
                    for j, header in enumerate(headers):
                        header = header.strip()
                        if header and j < len(odds_values):
                            try:
                                odds = float(odds_values[j].strip())
                                score_key = header.replace(' ', '')  # "1:0"
                                odds_data.append((current_timestamp, score_key, odds))
                            except (ValueError, IndexError):
                                pass
                    i += 2
                    continue
            elif line.startswith('0 : ') or line.startswith('1 : ') or line.startswith('2 : '):
                # 平局或客胜部分
                headers = line.split('\t')
                if i + 1 < len(lines):
                    odds_line = lines[i + 1].strip()
                    odds_values = odds_line.split('\t')
                    
                    for j, header in enumerate(headers):
                        header = header.strip()
                        if header and j < len(odds_values):
                            try:
                                odds = float(odds_values[j].strip())
                                score_key = header.replace(' ', '')
                                odds_data.append((current_timestamp, score_key, odds))
                            except (ValueError, IndexError):
                                pass
                    i += 2
                    continue
        
        i += 1
    
    return odds_data


def parse_total_goals_odds(lines, start_idx):
    """解析总进球赔率时序
    格式:
    总进球固定奖金
    发布时间 0 1 2 3 4 5 6 7+
    2025-08-22 09:52:19 8.50 3.90 3.05 3.80 6.50 14.50 25.00 40.00
    """
    odds_data = []
    in_section = False
    
    for i in range(start_idx, min(start_idx + 20, len(lines))):
        line = lines[i].strip()
        
        if line.startswith('总进球固定奖金'):
            in_section = True
            continue
        
        if in_section and line.startswith('发布时间'):
            continue
        
        if in_section and line:
            if re.match(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', line):
                parts = line.split()
                if len(parts) >= 3:
                    timestamp = f"{parts[0]} {parts[1]}"
                    goals = []
                    for j in range(2, min(10, len(parts))):
                        try:
                            val = float(parts[j])
                        except (ValueError, IndexError):
                            val = None
                        goals.append(val)
                    # goals: [0球, 1球, 2球, 3球, 4球, 5球, 6球, 7+球]
                    odds_data.append((timestamp,) + tuple(goals))
            elif not line.startswith('2025') and not line.startswith('2026'):
                # 继续收集数据
                pass
    
    return odds_data


def parse_seriea_file(filepath):
    """解析意甲赔率txt文件
    
    Returns:
        list of dict: 每场比赛的完整数据
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    matches = []
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # 检查是否是比赛开始
        if re.match(r'2025/2026 Regular Season', line):
            # 解析比赛头部
            round_num, match_date, match_time = parse_match_header(line)
            
            # 解析球队
            home_team, away_team, team_idx = parse_match_teams(lines, i + 1)
            
            if home_team and away_team:
                # 初始化比赛数据
                match_data = {
                    'round': round_num,
                    'match_date': match_date,
                    'match_time': match_time,
                    'home_team': home_team,
                    'away_team': away_team,
                    'league': '意甲2025-2026赛季',
                    'status': 'completed',
                    'source': 'SCRAPER',
                }
                
                # 生成match_id
                match_data['match_id'] = f"{match_date}_{home_team}_{away_team}"
                
                # 解析开奖结果和赔率
                wdl_odds = []
                handicap_odds = []
                score_odds = []
                total_goals_odds = []
                result_info = {}
                
                # 从team_idx+1开始查找结果和赔率
                j = team_idx + 1
                
                # 解析开奖结果
                result_info = parse_result_section(lines, j)
                
                # 解析胜平负赔率
                wdl_start = j
                while wdl_start < len(lines) and not lines[wdl_start].strip().startswith('胜平负固定奖金'):
                    wdl_start += 1
                wdl_odds = parse_wdl_odds(lines, wdl_start)
                
                # 解析让球赔率
                hcp_start = wdl_start + 1
                while hcp_start < len(lines) and not lines[hcp_start].strip().startswith('让球胜平负固定奖金'):
                    hcp_start += 1
                handicap_odds, handicap_line_val = parse_handicap_odds(lines, hcp_start)
                
                # 解析比分赔率
                score_start = hcp_start + 1
                while score_start < len(lines) and not lines[score_start].strip().startswith('比分固定奖金'):
                    score_start += 1
                score_odds = parse_score_odds(lines, score_start)
                
                # 解析总进球赔率
                tg_start = score_start + 1
                while tg_start < len(lines) and not lines[tg_start].strip().startswith('总进球固定奖金'):
                    tg_start += 1
                total_goals_odds = parse_total_goals_odds(lines, tg_start)
                
                # 构建结果
                if 'wdl_result' in result_info:
                    match_data['result'] = {
                        'actual_score': result_info.get('score', ''),
                        'actual_wdl': result_info.get('wdl_result', ''),
                        'actual_handicap': result_info.get('handicap_result', ''),
                        'actual_total_goals': result_info.get('total_goals', 0),
                        'verified': 1
                    }
                else:
                    match_data['result'] = None
                    match_data['status'] = 'pending'
                
                # 存储赔率数据
                match_data['wdl_timing'] = wdl_odds
                match_data['handicap_timing'] = handicap_odds
                match_data['score_timing'] = score_odds
                match_data['total_goals_timing'] = total_goals_odds
                
                matches.append(match_data)
                print(f"  解析: {match_date} {home_team} vs {away_team} (第{round_num}轮)")
            
            # 移动到下一个比赛块
            i = team_idx + 1
            while i < len(lines) and not re.match(r'2025/2026 Regular Season', lines[i].strip()):
                i += 1
        else:
            i += 1
    
    return matches


def import_to_odds_timing_db(matches):
    """将解析的数据导入odds_timing.db"""
    conn = sqlite3.connect(ODDS_TIMING_DB)
    cursor = conn.cursor()
    
    success_count = 0
    skip_count = 0
    update_count = 0
    
    for match in matches:
        match_id = match['match_id']
        
        try:
            # 检查比赛是否已存在
            cursor.execute("SELECT COUNT(*) FROM matches WHERE match_id=?", (match_id,))
            match_exists = cursor.fetchone()[0] > 0
            
            # 检查赔率数据是否已存在
            cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id=?", (match_id,))
            existing_wdl = cursor.fetchone()[0]
            
            if match_exists and existing_wdl > 0:
                # 比赛和赔率都已存在，检查是否需要补充结果
                cursor.execute("SELECT COUNT(*) FROM match_results WHERE match_id=?", (match_id,))
                existing_result = cursor.fetchone()[0]
                if existing_result > 0:
                    skip_count += 1
                    continue
                # 赔率存在但结果缺失，继续执行以补充结果
                print(f"  🔄 补充结果: {match_id}")
            
            # 插入/更新比赛基本信息
            if not match_exists:
                cursor.execute("""
                    INSERT INTO matches (match_id, home_team, away_team, match_date, league, round, status, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    match_id,
                    match['home_team'],
                    match['away_team'],
                    match['match_date'],
                    match['league'],
                    match['round'],
                    match['status'],
                    match['source']
                ))
            
            # 插入胜平负赔率（使用INSERT OR IGNORE避免重复）
            wdl_inserted = 0
            for wdl in match.get('wdl_timing', []):
                timestamp, win, draw, lose = wdl
                # 检查是否已存在
                cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id=? AND timestamp=?", (match_id, timestamp))
                if cursor.fetchone()[0] == 0:
                    cursor.execute("""
                        INSERT INTO wdl_timing (match_id, timestamp, win_a, draw, win_b, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (match_id, timestamp, win, draw, lose, 'SERIEA_2025_26'))
                    wdl_inserted += 1
            
            # 插入让球赔率
            for hcp in match.get('handicap_timing', []):
                if len(hcp) >= 5:
                    timestamp, handicap, win, draw, lose = hcp
                    cursor.execute("SELECT COUNT(*) FROM handicap_timing WHERE match_id=? AND timestamp=?", (match_id, timestamp))
                    if cursor.fetchone()[0] == 0:
                        cursor.execute("""
                            INSERT INTO handicap_timing (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose, source)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (match_id, timestamp, handicap, win, draw, lose, 'SERIEA_2025_26'))
            
            # 插入比分赔率
            for score_entry in match.get('score_timing', []):
                if len(score_entry) >= 3:
                    timestamp, score, odds = score_entry
                    cursor.execute("SELECT COUNT(*) FROM score_timing WHERE match_id=? AND timestamp=? AND score=?", (match_id, timestamp, score))
                    if cursor.fetchone()[0] == 0:
                        cursor.execute("""
                            INSERT INTO score_timing (match_id, timestamp, score, odds, source)
                            VALUES (?, ?, ?, ?, ?)
                        """, (match_id, timestamp, score, odds, 'SERIEA_2025_26'))
            
            # 插入总进球赔率
            for tg in match.get('total_goals_timing', []):
                timestamp = tg[0]
                goals = tg[1:]  # [0球, 1球, 2球, ...]
                cursor.execute("SELECT COUNT(*) FROM total_goals_timing WHERE match_id=? AND timestamp=?", (match_id, timestamp))
                if cursor.fetchone()[0] == 0:
                    # 填充到对应字段
                    padded_goals = list(goals) + [None] * (8 - len(goals))
                    cursor.execute("""
                        INSERT INTO total_goals_timing 
                        (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, 
                         goals_4, goals_5, goals_6, goals_7_plus, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (match_id, timestamp) + tuple(padded_goals[:8]) + ('SERIEA_2025_26',))
            
            # 插入结果
            if match.get('result'):
                result = match['result']
                cursor.execute("SELECT COUNT(*) FROM match_results WHERE match_id=?", (match_id,))
                if cursor.fetchone()[0] == 0:
                    cursor.execute("""
                        INSERT INTO match_results 
                        (match_id, actual_score, actual_wdl, actual_handicap, actual_total_goals, verified, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        match_id,
                        result.get('actual_score', ''),
                        result.get('actual_wdl', ''),
                        result.get('actual_handicap', ''),
                        result.get('actual_total_goals', 0),
                        result.get('verified', 1),
                        'SERIEA_2025_26'
                    ))
            
            if match_exists:
                update_count += 1
                print(f"  🔄 更新赔率: {match_id} (新增{wdl_inserted}条WDL)")
            else:
                success_count += 1
                print(f"  ✅ 导入: {match_id} (WDL={wdl_inserted}条)")
            
        except Exception as e:
            print(f"  ❌ 失败: {match_id} - {e}")
            import traceback
            traceback.print_exc()
    
    conn.commit()
    conn.close()
    
    return success_count, update_count, skip_count


def main():
    print("=" * 60)
    print("意甲2025-2026赔率时序数据解析与导入")
    print("=" * 60)
    
    # 1. 解析文件
    print("\n📖 解析赔率数据文件...")
    matches = parse_seriea_file(INPUT_FILE)
    print(f"✅ 解析完成: {len(matches)} 场比赛")
    
    # 2. 导入odds_timing.db
    print("\n💾 导入odds_timing.db...")
    success_count, update_count, skip_count = import_to_odds_timing_db(matches)
    print(f"✅ 导入完成: 新增 {success_count}, 更新 {update_count}, 跳过 {skip_count}")
    
    # 3. 统计
    print("\n📊 数据统计:")
    conn = sqlite3.connect(ODDS_TIMING_DB)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE league LIKE '%意甲%'")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    wdl_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM handicap_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    hcp_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM score_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    score_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    tg_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM match_results WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    result_count = cursor.fetchone()[0]
    
    print(f"  总比赛数: {total}")
    print(f"  胜平负赔率: {wdl_count} 条 (场均 {wdl_count/max(total,1):.1f} 条)")
    print(f"  让球赔率: {hcp_count} 条 (场均 {hcp_count/max(total,1):.1f} 条)")
    print(f"  比分赔率: {score_count} 条 (场均 {score_count/max(total,1):.1f} 条)")
    print(f"  总进球赔率: {tg_count} 条 (场均 {tg_count/max(total,1):.1f} 条)")
    print(f"  比赛结果: {result_count} 条")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("📝 下一步: 执行 import_to_main_db.py 同步到 odds.db")
    print("=" * 60)


if __name__ == '__main__':
    main()
