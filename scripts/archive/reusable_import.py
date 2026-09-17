import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "odds_timing.db"

def import_match(match_data):
    """
    可复用的比赛数据导入函数
    
    match_data 结构:
    {
        'match_id': str,           # 比赛ID，格式: YYYY-MM-DD_主队_客队
        'home_team': str,          # 主队名称（中文）
        'away_team': str,          # 客队名称（中文）
        'match_date': str,         # 比赛日期，格式: YYYY-MM-DD
        'match_time': str,         # 比赛时间，格式: HH:MM（可选）
        'league': str,             # 联赛名称
        'round': int,              # 轮次（可选）
        'status': str,             # 比赛状态: completed/pending
        'source': str,             # 数据源: MANUAL_IMPORT/EXTERNAL_SYSTEM/SCRAPER
        
        'wdl_timing': list,        # 胜平负赔率时序
            # [(timestamp, win_a, draw, win_b), ...]
        
        'handicap_timing': list,   # 让球赔率时序（可选）
            # [(timestamp, handicap, hcp_win, hcp_draw, hcp_lose), ...]
        
        'score_timing': dict,      # 比分赔率时序（可选）
            # {'timestamp': [(score, odds), ...], ...}
        
        'total_goals_timing': list, # 总进球赔率时序（可选）
            # [(timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus), ...]
        
        'result': dict,            # 比赛结果（可选）
            {
                'actual_score': str,       # 实际比分，格式: X:Y
                'actual_wdl': str,         # 胜平负结果: win/draw/lose
                'actual_handicap': str,    # 让球结果: win/draw/lose
                'actual_total_goals': int, # 总进球数
                'verified': int            # 是否已验证: 0/1
            }
    }
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # 先删除已存在的同名比赛数据（支持重复导入）
        cursor.execute('DELETE FROM match_results WHERE match_id=?', (match_data['match_id'],))
        cursor.execute('DELETE FROM total_goals_timing WHERE match_id=?', (match_data['match_id'],))
        cursor.execute('DELETE FROM score_timing WHERE match_id=?', (match_data['match_id'],))
        cursor.execute('DELETE FROM handicap_timing WHERE match_id=?', (match_data['match_id'],))
        cursor.execute('DELETE FROM wdl_timing WHERE match_id=?', (match_data['match_id'],))
        cursor.execute('DELETE FROM matches WHERE match_id=?', (match_data['match_id'],))
        
        # 插入比赛信息（包含轮次）
        cursor.execute('''
            INSERT INTO matches (match_id, home_team, away_team, match_date, match_time,
                                league, round, league_code, status, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, datetime('now'), datetime('now'))
        ''', (match_data['match_id'], match_data['home_team'], match_data['away_team'],
              match_data['match_date'], match_data.get('match_time', ''), match_data['league'],
              match_data.get('round', None), match_data['status'], match_data['source']))
        print(f"已插入比赛: {match_data['home_team']} vs {match_data['away_team']}")
        
        # 插入胜平负赔率时序
        wdl_count = 0
        for ts, win_a, draw, win_b in match_data.get('wdl_timing', []):
            cursor.execute('''
                INSERT INTO wdl_timing (match_id, timestamp, win_a, draw, win_b, source,
                                       quality_score, is_valid, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1.0, 1, datetime('now'))
            ''', (match_data['match_id'], ts, win_a, draw, win_b, match_data['source']))
            wdl_count += 1
        print(f"  胜平负赔率: {wdl_count} 条")
        
        # 插入让球赔率时序
        hcp_count = 0
        for ts, hcp, h_win, h_draw, h_lose in match_data.get('handicap_timing', []):
            cursor.execute('''
                INSERT INTO handicap_timing (match_id, timestamp, handicap, hcp_win, hcp_draw,
                                             hcp_lose, source, quality_score, is_valid, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1.0, 1, datetime('now'))
            ''', (match_data['match_id'], ts, hcp, h_win, h_draw, h_lose, match_data['source']))
            hcp_count += 1
        print(f"  让球赔率: {hcp_count} 条")
        
        # 插入比分赔率时序（处理重复时间戳）
        score_count = 0
        seen_timestamps = {}
        for ts, scores in match_data.get('score_timing', {}).items():
            # 检测重复时间戳并添加后缀
            if ts in seen_timestamps:
                seen_timestamps[ts] += 1
                final_ts = f"{ts}_{seen_timestamps[ts]}"
                print(f"    ⚠️ 检测到重复时间戳: {ts} -> {final_ts}")
            else:
                seen_timestamps[ts] = 1
                final_ts = ts
            
            for score, odds in scores:
                cursor.execute('''
                    INSERT INTO score_timing (match_id, timestamp, score, odds, source,
                                              quality_score, is_valid, created_at)
                    VALUES (?, ?, ?, ?, ?, 1.0, 1, datetime('now'))
                ''', (match_data['match_id'], final_ts, score, odds, match_data['source']))
                score_count += 1
        print(f"  比分赔率: {score_count} 条")
        
        # 插入总进球赔率时序
        tg_count = 0
        for ts, g0, g1, g2, g3, g4, g5, g6, g7p in match_data.get('total_goals_timing', []):
            cursor.execute('''
                INSERT INTO total_goals_timing (match_id, timestamp, goals_0, goals_1, goals_2,
                                                 goals_3, goals_4, goals_5, goals_6, goals_7_plus,
                                                 over_25, under_25, source, quality_score, is_valid, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 1, datetime('now'))
            ''', (match_data['match_id'], ts, g0, g1, g2, g3, g4, g5, g6, g7p, None, None, match_data['source']))
            tg_count += 1
        print(f"  总进球赔率: {tg_count} 条")
        
        # 插入比赛结果
        result = match_data.get('result')
        if result:
            cursor.execute('''
                INSERT INTO match_results (match_id, actual_score, actual_wdl, actual_handicap,
                                           actual_total_goals, source, verified, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ''', (match_data['match_id'], result['actual_score'], result['actual_wdl'],
                  result['actual_handicap'], result['actual_total_goals'],
                  match_data['source'], result['verified']))
            print(f"  比赛结果: {result['actual_score']}")
        
        # 插入导入日志
        cursor.execute('''
            INSERT INTO import_log (import_time, source, total_matches, wdl_records, hcp_records,
                                    tg_records, score_records, status, error_message, duration)
            VALUES (datetime('now'), ?, 1, ?, ?, ?, ?, 'success', NULL, 0.1)
        ''', (match_data['source'], wdl_count, hcp_count, tg_count, score_count))
        
        # 更新 data_sources 统计
        cursor.execute('UPDATE data_sources SET total_records=total_records+1 WHERE source_code=?', 
                      (match_data['source'],))
        
        conn.commit()
        
        # 验证导入结果
        verify_import(cursor, match_data['match_id'], wdl_count, hcp_count, score_count, tg_count)
        
        print("\n导入成功！")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"导入失败: {e}")
        raise
    finally:
        conn.close()

def verify_import(cursor, match_id, expected_wdl, expected_hcp, expected_score, expected_tg):
    """验证导入数据的完整性"""
    cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id=?", (match_id,))
    actual_wdl = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM handicap_timing WHERE match_id=?", (match_id,))
    actual_hcp = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM score_timing WHERE match_id=?", (match_id,))
    actual_score = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_timing WHERE match_id=?", (match_id,))
    actual_tg = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM match_results WHERE match_id=?", (match_id,))
    actual_result = cursor.fetchone()[0]
    
    print("\n=== 验证结果 ===")
    print(f"胜平负赔率: {actual_wdl}/{expected_wdl} {'✓' if actual_wdl == expected_wdl else '✗'}")
    print(f"让球赔率: {actual_hcp}/{expected_hcp} {'✓' if actual_hcp == expected_hcp else '✗'}")
    print(f"比分赔率: {actual_score}/{expected_score} {'✓' if actual_score == expected_score else '✗'}")
    print(f"总进球赔率: {actual_tg}/{expected_tg} {'✓' if actual_tg == expected_tg else '✗'}")
    print(f"比赛结果: {actual_result}/1 {'✓' if actual_result == 1 else '✗ (可能未提供)'}")

# 示例用法
if __name__ == '__main__':
    # 在此添加新比赛数据并调用 import_match()
    pass
