"""
赔率时序数据导入脚本
将odds_timing.db中的数据导入到现有odds.db

功能：
1. 数据标准化处理
2. match_id映射和关联
3. 去重处理
4. 增量导入支持
5. 导入日志记录
"""

import sqlite3
import os
from datetime import datetime

# 配置
TIMING_DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/odds_timing.db')
ODDS_DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/odds.db')

def get_conn(db_path):
    """获取数据库连接"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def normalize_match_id(match_id):
    """标准化match_id格式"""
    return match_id.replace(' ', '_').replace('/', '-').strip()

def import_matches():
    """导入比赛数据"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    # 获取未处理的比赛
    timing_cursor.execute("""
        SELECT * FROM matches 
        WHERE status = 'pending' 
        ORDER BY match_date
    """)
    
    imported = 0
    updated = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = normalize_match_id(row['match_id'])
        
        # 检查是否已存在
        odds_cursor.execute("SELECT COUNT(*) FROM matches WHERE match_id = ?", (match_id,))
        exists = odds_cursor.fetchone()[0] > 0
        
        if exists:
            # 更新现有记录
            odds_cursor.execute("""
                UPDATE matches 
                SET home_team = ?, away_team = ?, match_date = ?, match_type = ?, 
                    updated_at = CURRENT_TIMESTAMP
                WHERE match_id = ?
            """, (row['home_team'], row['away_team'], row['match_date'], 
                  row['league'] or 'Unknown', match_id))
            updated += 1
        else:
            # 插入新记录
            odds_cursor.execute("""
                INSERT INTO matches 
                (match_id, home_team, away_team, match_date, match_type, 
                 actual_wdl, actual_handicap, actual_score, actual_total_goals)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (match_id, row['home_team'], row['away_team'], row['match_date'],
                  row['league'] or 'Unknown', None, None, None, None))
            imported += 1
        
        # 更新状态
        timing_cursor.execute("UPDATE matches SET status = 'processed' WHERE id = ?", (row['id'],))
    
    timing_conn.commit()
    odds_conn.commit()
    
    timing_conn.close()
    odds_conn.close()
    
    return imported, updated, skipped

def import_wdl_timing():
    """导入胜平负赔率时序数据"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    # 获取有效数据
    timing_cursor.execute("""
        SELECT t.*, m.match_id as normalized_match_id
        FROM wdl_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE t.is_valid = 1
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = normalize_match_id(row['normalized_match_id'])
        
        # 检查是否已存在（防止重复）
        odds_cursor.execute("""
            SELECT COUNT(*) FROM wdl_history 
            WHERE match_id = ? AND timestamp = ?
        """, (match_id, row['timestamp']))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        # 插入数据
        odds_cursor.execute("""
            INSERT INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
            VALUES (?, ?, ?, ?, ?)
        """, (match_id, row['timestamp'], row['win_a'], row['draw'], row['win_b']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_handicap_timing():
    """导入让球赔率时序数据"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT t.*, m.match_id as normalized_match_id
        FROM handicap_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE t.is_valid = 1
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = normalize_match_id(row['normalized_match_id'])
        
        odds_cursor.execute("""
            SELECT COUNT(*) FROM handicap_history 
            WHERE match_id = ? AND timestamp = ?
        """, (match_id, row['timestamp']))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        odds_cursor.execute("""
            INSERT INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
            VALUES (?, ?, ?, ?, ?)
        """, (match_id, row['timestamp'], row['hcp_win'], row['hcp_draw'], row['hcp_lose']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_total_goals_timing():
    """导入总进球赔率时序数据"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT t.*, m.match_id as normalized_match_id
        FROM total_goals_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE t.is_valid = 1
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = normalize_match_id(row['normalized_match_id'])
        
        odds_cursor.execute("""
            SELECT COUNT(*) FROM total_goals_history 
            WHERE match_id = ? AND timestamp = ?
        """, (match_id, row['timestamp']))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        odds_cursor.execute("""
            INSERT INTO total_goals_history 
            (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, 
             goals_5, goals_6, goals_7_plus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (match_id, row['timestamp'],
              row['goals_0'], row['goals_1'], row['goals_2'], row['goals_3'], row['goals_4'],
              row['goals_5'], row['goals_6'], row['goals_7_plus']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_score_timing():
    """导入比分赔率时序数据"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT t.*, m.match_id as normalized_match_id
        FROM score_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE t.is_valid = 1
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = normalize_match_id(row['normalized_match_id'])
        
        odds_cursor.execute("""
            SELECT COUNT(*) FROM score_history 
            WHERE match_id = ? AND timestamp = ? AND score = ?
        """, (match_id, row['timestamp'], row['score']))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        odds_cursor.execute("""
            INSERT INTO score_history (match_id, timestamp, score, odds)
            VALUES (?, ?, ?, ?)
        """, (match_id, row['timestamp'], row['score'], row['odds']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_match_results():
    """导入比赛结果"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT r.*, m.match_id as normalized_match_id
        FROM match_results r
        JOIN matches m ON r.match_id = m.match_id
        WHERE r.verified = 1
    """)
    
    imported = 0
    updated = 0
    
    for row in timing_cursor.fetchall():
        match_id = normalize_match_id(row['normalized_match_id'])
        
        odds_cursor.execute("SELECT COUNT(*) FROM matches WHERE match_id = ?", (match_id,))
        exists = odds_cursor.fetchone()[0] > 0
        
        if exists:
            odds_cursor.execute("""
                UPDATE matches 
                SET actual_wdl = ?, actual_handicap = ?, actual_score = ?, 
                    actual_total_goals = ?
                WHERE match_id = ?
            """, (row['actual_wdl'], row['actual_handicap'], row['actual_score'],
                  row['actual_total_goals'], match_id))
            updated += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, updated

def run_import():
    """执行完整导入流程"""
    print(f'\n{"="*60}')
    print('赔率时序数据导入脚本')
    print(f'开始时间: {datetime.now().isoformat()}')
    print(f'{"="*60}')
    
    # 1. 导入比赛数据
    print('\n--- 1. 导入比赛数据 ---')
    imported, updated, skipped = import_matches()
    print(f'  新增: {imported} 场')
    print(f'  更新: {updated} 场')
    print(f'  跳过: {skipped} 场')
    
    # 2. 导入胜平负赔率
    print('\n--- 2. 导入胜平负赔率数据 ---')
    imported, skipped = import_wdl_timing()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 3. 导入让球赔率
    print('\n--- 3. 导入让球赔率数据 ---')
    imported, skipped = import_handicap_timing()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 4. 导入总进球赔率
    print('\n--- 4. 导入总进球赔率数据 ---')
    imported, skipped = import_total_goals_timing()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 5. 导入比分赔率
    print('\n--- 5. 导入比分赔率数据 ---')
    imported, skipped = import_score_timing()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 6. 导入比赛结果
    print('\n--- 6. 导入比赛结果 ---')
    imported, updated = import_match_results()
    print(f'  更新结果: {updated} 场')
    
    # 更新导入日志
    timing_conn = get_conn(TIMING_DB_PATH)
    timing_cursor = timing_conn.cursor()
    timing_cursor.execute("""
        INSERT INTO import_log 
        (source, total_matches, wdl_records, hcp_records, tg_records, score_records, status)
        VALUES ('TIMING_IMPORT', 0, 0, 0, 0, 0, 'success')
    """)
    timing_conn.commit()
    timing_conn.close()
    
    print(f'\n{"="*60}')
    print('导入完成!')
    print(f'结束时间: {datetime.now().isoformat()}')
    print(f'{"="*60}')

if __name__ == '__main__':
    run_import()
