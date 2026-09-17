import sqlite3
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 配置
TXT_FILE = BASE_DIR / "data" / "法甲2025-2026完整时序赔率.txt"
ODDS_TIMING_DB = BASE_DIR / "data" / "odds_timing.db"
LEAGUE = '法甲2025-2026赛季'
LEAGUE_CODE = ''
MATCH_TYPE = '法甲2025-2026赛季'
# 法甲球队行前缀（区别于德甲的'D1'），格式: '法甲 Ligue 1\t主队\t客队'
TEAM_LINE_PREFIX = '法甲 Ligue 1'


def parse_txt_file(filepath):
    """解析法甲赔率txt文件"""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    matches = []
    current_match = None
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # 检查是否是新比赛的开始
        # 格式: 2025/2026 Regular Season 第X轮 日期 时间
        match_header = re.match(r'(\d{4}/\d{4})\s+Regular\s+Season\s+第(\d+)轮\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', line)
        if match_header:
            # 保存上一场比赛
            if current_match and current_match.get('home_team'):
                matches.append(current_match)

            # 开始新比赛
            season = match_header.group(1)
            round_num = int(match_header.group(2))
            match_date = match_header.group(3)
            match_time = match_header.group(4)

            current_match = {
                'season': season,
                'round': round_num,
                'match_date': match_date,
                'match_time': match_time,
                'home_team': '',
                'away_team': '',
                'result': {},
                'wdl_timing': [],
                'handicap_timing': [],
                'score_timing': [],
                'total_goals_timing': []
            }
            i += 1
            continue

        # 读取球队信息 - 法甲格式: '法甲 Ligue 1\t主队\t客队'
        if line.startswith(TEAM_LINE_PREFIX) and current_match and not current_match['home_team']:
            # 去掉前缀部分，按tab分割
            rest = line[len(TEAM_LINE_PREFIX):]
            parts = rest.split('\t')
            # 过滤空部分
            parts = [p for p in parts if p != '']
            if len(parts) >= 2:
                # 清理球队名中的多余空格
                current_match['home_team'] = re.sub(r'\s{2,}', ' ', parts[0].strip())
                current_match['away_team'] = re.sub(r'\s{2,}', ' ', parts[1].strip())
            elif len(parts) == 1:
                # 尝试用多个空格分割
                space_parts = re.split(r'\s{2,}', parts[0].strip())
                if len(space_parts) >= 2:
                    current_match['home_team'] = space_parts[0].strip()
                    current_match['away_team'] = space_parts[1].strip()
            i += 1
            continue

        # 读取开奖结果
        if line == '开奖结果' and current_match:
            i += 1
            if i < len(lines):
                # 跳过表头 "游戏	开奖结果	奖金"
                result_header = lines[i].strip()
                if '游戏' in result_header:
                    i += 1

                # 读取开奖结果行
                while i < len(lines):
                    result_line = lines[i].strip()
                    if not result_line or result_line == '':
                        break
                    if '固定奖金' in result_line:
                        break

                    parts = result_line.split()
                    if len(parts) >= 3:
                        game_type = parts[0]
                        result_value = parts[1]
                        try:
                            odds_value = float(parts[2])
                        except:
                            odds_value = None

                        if game_type == '胜平负':
                            current_match['result']['wdl_result'] = result_value
                            current_match['result']['wdl_odds'] = odds_value
                        elif game_type == '让球胜平负':
                            current_match['result']['handicap_result'] = result_value
                            current_match['result']['handicap_odds'] = odds_value
                        elif game_type == '比分':
                            current_match['result']['score_result'] = result_value
                            current_match['result']['score_odds'] = odds_value
                        elif game_type == '总进球':
                            # 处理 "7+" 这种值
                            goals_val = result_value.replace('+', '')
                            try:
                                current_match['result']['total_goals_result'] = int(goals_val)
                            except:
                                current_match['result']['total_goals_result'] = 7  # 7+ 统一为7
                            current_match['result']['total_goals_odds'] = odds_value
                    i += 1
                continue

        # 胜平负固定奖金
        if line == '胜平负固定奖金' and current_match:
            i += 1
            if i < len(lines) and '发布时间' in lines[i]:
                i += 1  # 跳过表头
            while i < len(lines):
                wdl_line = lines[i].strip()
                if not wdl_line or wdl_line == '':
                    break
                if '让球' in wdl_line or '比分' in wdl_line or '总进球' in wdl_line:
                    break
                parts = wdl_line.split()
                if len(parts) >= 4:
                    timestamp = parts[0] + ' ' + parts[1]
                    try:
                        win_a = float(parts[2])
                        draw = float(parts[3])
                        win_b = float(parts[4]) if len(parts) > 4 else None
                        if win_b:
                            current_match['wdl_timing'].append({
                                'timestamp': timestamp,
                                'win_a': win_a,
                                'draw': draw,
                                'win_b': win_b
                            })
                    except:
                        pass
                i += 1
            continue

        # 让球胜平负固定奖金
        if '让球胜平负固定奖金' in line and current_match:
            i += 1
            # 读取让球数
            handicap = 0
            if i < len(lines):
                hcp_line = lines[i].strip()
                if '让球' in hcp_line and '固定奖金' not in hcp_line:
                    hcp_match = re.match(r'让球([+-]?\d+)', hcp_line)
                    if hcp_match:
                        handicap = int(hcp_match.group(1))
                    i += 1

            # 跳过表头
            if i < len(lines) and '发布时间' in lines[i]:
                i += 1

            while i < len(lines):
                hcp_line = lines[i].strip()
                if not hcp_line or hcp_line == '':
                    break
                if '比分' in hcp_line or '总进球' in hcp_line or '胜平负' in hcp_line:
                    break
                parts = hcp_line.split()
                if len(parts) >= 4:
                    timestamp = parts[0] + ' ' + parts[1]
                    try:
                        hcp_win = float(parts[2])
                        hcp_draw = float(parts[3])
                        hcp_lose = float(parts[4]) if len(parts) > 4 else None
                        if hcp_lose:
                            current_match['handicap_timing'].append({
                                'timestamp': timestamp,
                                'handicap': handicap,
                                'hcp_win': hcp_win,
                                'hcp_draw': hcp_draw,
                                'hcp_lose': hcp_lose
                            })
                    except:
                        pass
                i += 1
            continue

        # 比分固定奖金
        if line == '比分固定奖金' and current_match:
            i += 1
            # 处理多个发布时间的比分数据
            while i < len(lines):
                score_line = lines[i].strip()
                if not score_line or score_line == '':
                    break
                if '总进球' in score_line or '胜平负' in score_line or score_line == '':
                    break

                # 检查是否是发布时间行
                if '发布时间' in score_line:
                    # 提取发布时间 (格式: '发布时间\t2025-08-14   13:50:32' 或 '发布时间 2025-08-15 10:12:54')
                    ts_match = re.match(r'发布时间\s*(\d{4}-\d{2}-\d{2})\s*(\d{2}:\d{2}:\d{2})', score_line)
                    if ts_match:
                        current_timestamp = ts_match.group(1) + ' ' + ts_match.group(2)
                    else:
                        i += 1
                        continue
                    i += 1

                    # 检查发布时间行是否同时包含比分数据（单行格式）
                    # 格式: '发布时间\t2025-08-15   18:08:44\t1 : 0\t2 : 0\t...'
                    remaining = score_line[ts_match.end():].strip()
                    # 用tab分割剩余部分，提取比分标签
                    inline_parts = [p.strip().replace(' ', '') for p in remaining.split('\t') if p.strip()]
                    # 过滤出有效的比分标签（包含:或中文如"胜其它"）
                    inline_scores = [p for p in inline_parts if ':' in p or '其它' in p]

                    if inline_scores:
                        # 单行格式：比分在发布时间行，赔率在下一行
                        if i < len(lines):
                            odds_tokens = [o.strip() for o in lines[i].strip().split('\t') if o.strip()]
                            if not odds_tokens or len(odds_tokens) < len(inline_scores):
                                odds_tokens = lines[i].strip().split()
                            i += 1
                            for j in range(min(len(inline_scores), len(odds_tokens))):
                                try:
                                    score_val = inline_scores[j]
                                    odds_val = float(odds_tokens[j])
                                    if score_val and odds_val:
                                        current_match['score_timing'].append({
                                            'timestamp': current_timestamp,
                                            'score': score_val,
                                            'odds': odds_val
                                        })
                                except:
                                    pass
                        continue
                    else:
                        # 多行格式：发布时间单独一行，后面跟着3组(表头+赔率)
                        # 读取胜场比分表头和赔率 - 用tab分割比分表头，正确处理 '1 : 0' 格式
                        win_score_headers = []
                        win_odds = []
                        if i < len(lines):
                            win_score_headers = [h.strip().replace(' ', '') for h in lines[i].strip().split('\t') if h.strip()]
                            i += 1
                        if i < len(lines):
                            win_odds = [o.strip() for o in lines[i].strip().split('\t') if o.strip()]
                            if not win_odds or len(win_odds) < len(win_score_headers):
                                win_odds = lines[i-1].strip().split()
                            i += 1

                        # 读取平局比分表头和赔率
                        draw_score_headers = []
                        draw_odds = []
                        if i < len(lines):
                            draw_score_headers = [h.strip().replace(' ', '') for h in lines[i].strip().split('\t') if h.strip()]
                            i += 1
                        if i < len(lines):
                            draw_odds = [o.strip() for o in lines[i].strip().split('\t') if o.strip()]
                            if not draw_odds or len(draw_odds) < len(draw_score_headers):
                                draw_odds = lines[i-1].strip().split()
                            i += 1

                        # 读取负场比分表头和赔率
                        lose_score_headers = []
                        lose_odds = []
                        if i < len(lines):
                            lose_score_headers = [h.strip().replace(' ', '') for h in lines[i].strip().split('\t') if h.strip()]
                            i += 1
                        if i < len(lines):
                            lose_odds = [o.strip() for o in lines[i].strip().split('\t') if o.strip()]
                            if not lose_odds or len(lose_odds) < len(lose_score_headers):
                                lose_odds = lines[i-1].strip().split()
                            i += 1

                        # 胜场部分
                        for j in range(min(len(win_score_headers), len(win_odds))):
                            try:
                                score_val = win_score_headers[j]
                                odds_val = float(win_odds[j])
                                if score_val and odds_val:
                                    current_match['score_timing'].append({
                                        'timestamp': current_timestamp,
                                        'score': score_val,
                                        'odds': odds_val
                                    })
                            except:
                                pass

                        # 平局部分
                        for j in range(min(len(draw_score_headers), len(draw_odds))):
                            try:
                                score_val = draw_score_headers[j]
                                odds_val = float(draw_odds[j])
                                if score_val and odds_val:
                                    current_match['score_timing'].append({
                                        'timestamp': current_timestamp,
                                        'score': score_val,
                                        'odds': odds_val
                                    })
                            except:
                                pass

                        # 负场部分
                        for j in range(min(len(lose_score_headers), len(lose_odds))):
                            try:
                                score_val = lose_score_headers[j]
                                odds_val = float(lose_odds[j])
                                if score_val and odds_val:
                                    current_match['score_timing'].append({
                                        'timestamp': current_timestamp,
                                        'score': score_val,
                                        'odds': odds_val
                                    })
                            except:
                                pass
                else:
                    i += 1
            continue

        # 总进球固定奖金
        if line == '总进球固定奖金' and current_match:
            i += 1
            # 读取表头
            if i < len(lines):
                tg_header = lines[i].strip()
                i += 1

            # 读取数据行
            while i < len(lines):
                tg_line = lines[i].strip()
                if not tg_line or tg_line == '':
                    break
                if '胜平负' in tg_line or '让球' in tg_line or '比分' in tg_line:
                    break
                parts = tg_line.split()
                if len(parts) >= 2:
                    timestamp = parts[0] + ' ' + parts[1]
                    try:
                        goals = [float(x) for x in parts[2:10]]  # 0,1,2,3,4,5,6,7+
                        current_match['total_goals_timing'].append({
                            'timestamp': timestamp,
                            'goals_0': goals[0] if len(goals) > 0 else None,
                            'goals_1': goals[1] if len(goals) > 1 else None,
                            'goals_2': goals[2] if len(goals) > 2 else None,
                            'goals_3': goals[3] if len(goals) > 3 else None,
                            'goals_4': goals[4] if len(goals) > 4 else None,
                            'goals_5': goals[5] if len(goals) > 5 else None,
                            'goals_6': goals[6] if len(goals) > 6 else None,
                            'goals_7_plus': goals[7] if len(goals) > 7 else None
                        })
                    except:
                        pass
                i += 1
            continue

        i += 1

    # 添加最后一场比赛
    if current_match and current_match.get('home_team'):
        matches.append(current_match)

    return matches


def get_match_id(match):
    """生成比赛ID"""
    date_str = match['match_date']
    home = match['home_team']
    away = match['away_team']
    return f"{date_str}_{home}_{away}"


def parse_handicap_result(result_str):
    """解析让球结果，返回 (handicap, result)"""
    if not result_str:
        return 0, ''
    # 格式: (-2)胜, (+1)负, (-1)平 等
    pattern = r'\(([+-]?\d+)\)([胜负平])'
    match = re.match(pattern, result_str)
    if match:
        handicap = int(match.group(1))
        result = match.group(2)
        return handicap, result
    return 0, result_str


def parse_score_result(result_str):
    """解析比分结果，格式化为 'X:Y'"""
    if not result_str:
        return ''
    return result_str.replace(' ', '')


def sync_to_odds_timing_db(matches):
    """同步数据到odds_timing.db"""
    print(f"\n正在同步到 odds_timing.db...")
    conn = sqlite3.connect(ODDS_TIMING_DB)
    cursor = conn.cursor()

    imported_count = 0
    wdl_count = 0
    hcp_count = 0
    tg_count = 0
    score_count = 0

    for match in matches:
        match_id = get_match_id(match)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 检查是否已存在
        cursor.execute("SELECT id FROM matches WHERE match_id = ?", (match_id,))
        existing = cursor.fetchone()

        if existing:
            match_db_id = existing[0]
            cursor.execute("""
                UPDATE matches 
                SET home_team=?, away_team=?, match_date=?, match_time=?,
                    league=?, league_code=?, status=?, source=?, updated_at=?, round=?
                WHERE id=?
            """, (
                match['home_team'],
                match['away_team'],
                match['match_date'],
                match['match_time'],
                LEAGUE,
                LEAGUE_CODE,
                'imported',
                'MANUAL_IMPORT',
                now,
                match['round'],
                match_db_id
            ))
        else:
            cursor.execute("""
                INSERT INTO matches (match_id, home_team, away_team, match_date, match_time,
                                   league, league_code, status, source, created_at, updated_at, round)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'imported', 'MANUAL_IMPORT', ?, ?, ?)
            """, (
                match_id,
                match['home_team'],
                match['away_team'],
                match['match_date'],
                match['match_time'],
                LEAGUE,
                LEAGUE_CODE,
                now,
                now,
                match['round']
            ))
            match_db_id = cursor.lastrowid

        # 插入/更新开奖结果
        result = match['result']
        actual_score = parse_score_result(result.get('score_result', ''))
        actual_wdl = result.get('wdl_result', '')
        handicap_val, actual_handicap = parse_handicap_result(result.get('handicap_result', ''))
        actual_total_goals = result.get('total_goals_result', 0)

        cursor.execute("SELECT id FROM match_results WHERE match_id = ?", (match_id,))
        existing_result = cursor.fetchone()

        if existing_result:
            cursor.execute("""
                UPDATE match_results 
                SET actual_score=?, actual_wdl=?, actual_handicap=?, actual_total_goals=?, updated_at=?
                WHERE id=?
            """, (actual_score, actual_wdl, actual_handicap, actual_total_goals, now, existing_result[0]))
        else:
            cursor.execute("""
                INSERT INTO match_results (match_id, actual_score, actual_wdl, actual_handicap,
                                         actual_total_goals, source, verified, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'MANUAL_IMPORT', 1, ?, ?)
            """, (match_id, actual_score, actual_wdl, actual_handicap, actual_total_goals, now, now))

        # 清除旧的时序数据
        cursor.execute("DELETE FROM wdl_timing WHERE match_id = ?", (match_id,))
        cursor.execute("DELETE FROM handicap_timing WHERE match_id = ?", (match_id,))
        cursor.execute("DELETE FROM total_goals_timing WHERE match_id = ?", (match_id,))
        cursor.execute("DELETE FROM score_timing WHERE match_id = ?", (match_id,))

        # 插入胜平负时序数据
        for wdl in match['wdl_timing']:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO wdl_timing (match_id, timestamp, win_a, draw, win_b, source, quality_score, is_valid, created_at)
                    VALUES (?, ?, ?, ?, ?, 'MANUAL_IMPORT', 1.0, 1, ?)
                """, (match_id, wdl['timestamp'], wdl['win_a'], wdl['draw'], wdl['win_b'], now))
                wdl_count += 1
            except:
                pass

        # 插入让球时序数据
        for hcp in match['handicap_timing']:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO handicap_timing (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose,
                                                 source, quality_score, is_valid, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'MANUAL_IMPORT', 1.0, 1, ?)
                """, (match_id, hcp['timestamp'], hcp['handicap'], hcp['hcp_win'], hcp['hcp_draw'], hcp['hcp_lose'], now))
                hcp_count += 1
            except:
                pass

        # 插入总进球时序数据
        for tg in match['total_goals_timing']:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO total_goals_timing (match_id, timestamp, goals_0, goals_1, goals_2, goals_3,
                                                    goals_4, goals_5, goals_6, goals_7_plus,
                                                    source, quality_score, is_valid, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL_IMPORT', 1.0, 1, ?)
                """, (match_id, tg['timestamp'], tg['goals_0'], tg['goals_1'], tg['goals_2'], tg['goals_3'],
                      tg['goals_4'], tg['goals_5'], tg['goals_6'], tg['goals_7_plus'], now))
                tg_count += 1
            except:
                pass

        # 插入比分时序数据
        for score in match['score_timing']:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO score_timing (match_id, timestamp, score, odds, source, quality_score, is_valid, created_at)
                    VALUES (?, ?, ?, ?, 'MANUAL_IMPORT', 1.0, 1, ?)
                """, (match_id, score['timestamp'], score['score'], score['odds'], now))
                score_count += 1
            except:
                pass

        imported_count += 1

    # 记录导入日志
    cursor.execute("""
        INSERT INTO import_log (import_time, source, total_matches, wdl_records, hcp_records,
                               tg_records, score_records, status, duration)
        VALUES (?, 'MANUAL_IMPORT', ?, ?, ?, ?, ?, 'success', 0)
    """, (now, imported_count, wdl_count, hcp_count, tg_count, score_count))

    conn.commit()
    conn.close()

    print(f"  成功导入 {imported_count} 场比赛")
    print(f"  胜平负记录: {wdl_count}")
    print(f"  让球记录: {hcp_count}")
    print(f"  总进球记录: {tg_count}")
    print(f"  比分记录: {score_count}")


def main():
    print("=" * 60)
    print("法甲2025-2026完整时序赔率数据导入工具")
    print("=" * 60)

    # 1. 解析txt文件
    print(f"\n正在解析文件: {TXT_FILE}")
    matches = parse_txt_file(TXT_FILE)
    print(f"共解析 {len(matches)} 场比赛")

    # 显示解析统计
    total_wdl = sum(len(m['wdl_timing']) for m in matches)
    total_hcp = sum(len(m['handicap_timing']) for m in matches)
    total_tg = sum(len(m['total_goals_timing']) for m in matches)
    total_score = sum(len(m['score_timing']) for m in matches)
    print(f"  胜平负赔率记录: {total_wdl}")
    print(f"  让球赔率记录: {total_hcp}")
    print(f"  总进球赔率记录: {total_tg}")
    print(f"  比分赔率记录: {total_score}")

    # 检查解析完整性
    no_team = [i for i, m in enumerate(matches) if not m['home_team'] or not m['away_team']]
    if no_team:
        print(f"\n  ⚠️ 有 {len(no_team)} 场比赛缺少球队信息")
        for idx in no_team[:5]:
            print(f"    比赛{idx+1}: date={matches[idx]['match_date']}, home='{matches[idx]['home_team']}', away='{matches[idx]['away_team']}'")

    # 显示前几场比赛信息
    if matches:
        print(f"\n示例 - 前3场比赛:")
        for i, m in enumerate(matches[:3]):
            print(f"\n  比赛{i+1}:")
            print(f"    轮次: 第{m['round']}轮")
            print(f"    时间: {m['match_date']} {m['match_time']}")
            print(f"    对阵: {m['home_team']} vs {m['away_team']}")
            print(f"    结果: {m['result']}")

    # 2. 同步到odds_timing.db
    sync_to_odds_timing_db(matches)

    # 已移除 odds.db 同步：matches 基础数据统一由 populate_matches_from_fbref.py 负责（本脚本只写 odds_timing.db）

    print("\n" + "=" * 60)
    print("数据同步完成!")
    print("=" * 60)


if __name__ == '__main__':
    main()
