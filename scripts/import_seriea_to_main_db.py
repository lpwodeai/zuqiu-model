"""
意甲赔率数据同步脚本
将odds_timing.db中的意甲数据导入到odds.db
"""

import sqlite3
import os
from datetime import datetime

# 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TIMING_DB_PATH = os.path.join(BASE_DIR, 'odds_timing.db')
ODDS_DB_PATH = os.path.join(BASE_DIR, 'odds.db')

SERIEA_LEAGUE = '意甲2025-2026赛季'

def get_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def import_seriea_matches():
    """导入意甲比赛数据"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    # 获取意甲比赛
    timing_cursor.execute("""
        SELECT * FROM matches 
        WHERE league LIKE '%意甲%'
        ORDER BY match_date
    """)
    
    imported = 0
    updated = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = row['match_id']
        
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
                  SERIEA_LEAGUE, match_id))
            updated += 1
        else:
            # 插入新记录
            odds_cursor.execute("""
                INSERT INTO matches 
                (match_id, home_team, away_team, match_date, match_type, 
                 actual_wdl, actual_handicap, actual_score, actual_total_goals)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (match_id, row['home_team'], row['away_team'], row['match_date'],
                  SERIEA_LEAGUE, None, None, None, None))
            imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, updated, skipped

def import_seriea_wdl():
    """导入意甲胜平负赔率"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT t.* FROM wdl_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE m.league LIKE '%意甲%'
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = row['match_id']
        timestamp = row['timestamp']
        
        # 检查是否已存在
        odds_cursor.execute("""
            SELECT COUNT(*) FROM wdl_history 
            WHERE match_id = ? AND timestamp = ?
        """, (match_id, timestamp))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        odds_cursor.execute("""
            INSERT INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
            VALUES (?, ?, ?, ?, ?)
        """, (match_id, timestamp, row['win_a'], row['draw'], row['win_b']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_seriea_handicap():
    """导入意甲让球赔率"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT t.* FROM handicap_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE m.league LIKE '%意甲%'
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = row['match_id']
        timestamp = row['timestamp']
        
        odds_cursor.execute("""
            SELECT COUNT(*) FROM handicap_history 
            WHERE match_id = ? AND timestamp = ?
        """, (match_id, timestamp))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        odds_cursor.execute("""
            INSERT INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
            VALUES (?, ?, ?, ?, ?)
        """, (match_id, timestamp, row['hcp_win'], row['hcp_draw'], row['hcp_lose']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_seriea_total_goals():
    """导入意甲总进球赔率"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT t.* FROM total_goals_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE m.league LIKE '%意甲%'
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = row['match_id']
        timestamp = row['timestamp']
        
        odds_cursor.execute("""
            SELECT COUNT(*) FROM total_goals_history 
            WHERE match_id = ? AND timestamp = ?
        """, (match_id, timestamp))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        odds_cursor.execute("""
            INSERT INTO total_goals_history 
            (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, 
             goals_5, goals_6, goals_7_plus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (match_id, timestamp,
              row['goals_0'], row['goals_1'], row['goals_2'], row['goals_3'], row['goals_4'],
              row['goals_5'], row['goals_6'], row['goals_7_plus']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_seriea_score():
    """导入意甲比分赔率"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT t.* FROM score_timing t
        JOIN matches m ON t.match_id = m.match_id
        WHERE m.league LIKE '%意甲%'
    """)
    
    imported = 0
    skipped = 0
    
    for row in timing_cursor.fetchall():
        match_id = row['match_id']
        timestamp = row['timestamp']
        score = row['score']
        
        odds_cursor.execute("""
            SELECT COUNT(*) FROM score_history 
            WHERE match_id = ? AND timestamp = ? AND score = ?
        """, (match_id, timestamp, score))
        
        if odds_cursor.fetchone()[0] > 0:
            skipped += 1
            continue
        
        odds_cursor.execute("""
            INSERT INTO score_history (match_id, timestamp, score, odds)
            VALUES (?, ?, ?, ?)
        """, (match_id, timestamp, score, row['odds']))
        imported += 1
    
    timing_conn.close()
    odds_conn.commit()
    odds_conn.close()
    
    return imported, skipped

def import_seriea_results():
    """导入意甲比赛结果"""
    timing_conn = get_conn(TIMING_DB_PATH)
    odds_conn = get_conn(ODDS_DB_PATH)
    
    timing_cursor = timing_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    timing_cursor.execute("""
        SELECT r.* FROM match_results r
        JOIN matches m ON r.match_id = m.match_id
        WHERE m.league LIKE '%意甲%' AND r.verified = 1
    """)
    
    updated = 0
    
    for row in timing_cursor.fetchall():
        match_id = row['match_id']
        
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
    
    return updated

def main():
    print('=' * 60)
    print('意甲赔率数据同步到主数据库 odds.db')
    print(f'开始时间: {datetime.now().isoformat()}')
    print('=' * 60)
    
    # 1. 导入比赛数据
    print('\n--- 1. 导入意甲比赛数据 ---')
    imported, updated, skipped = import_seriea_matches()
    print(f'  新增: {imported} 场')
    print(f'  更新: {updated} 场')
    
    # 2. 导入胜平负赔率
    print('\n--- 2. 导入胜平负赔率数据 ---')
    imported, skipped = import_seriea_wdl()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 3. 导入让球赔率
    print('\n--- 3. 导入让球赔率数据 ---')
    imported, skipped = import_seriea_handicap()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 4. 导入总进球赔率
    print('\n--- 4. 导入总进球赔率数据 ---')
    imported, skipped = import_seriea_total_goals()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 5. 导入比分赔率
    print('\n--- 5. 导入比分赔率数据 ---')
    imported, skipped = import_seriea_score()
    print(f'  导入: {imported} 条')
    print(f'  跳过(重复): {skipped} 条')
    
    # 6. 导入比赛结果
    print('\n--- 6. 导入意甲比赛结果 ---')
    updated = import_seriea_results()
    print(f'  更新结果: {updated} 场')
    
    # 统计odds.db中的意甲数据
    print('\n--- 7. odds.db数据统计 ---')
    conn = sqlite3.connect(ODDS_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE match_type LIKE '%意甲%'")
    total = cursor.fetchone()[0]
    print(f'  意甲比赛数: {total}')
    
    cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_type LIKE '%意甲%')")
    wdl_count = cursor.fetchone()[0]
    print(f'  胜平负赔率: {wdl_count} 条')
    
    cursor.execute("SELECT COUNT(*) FROM handicap_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_type LIKE '%意甲%')")
    hcp_count = cursor.fetchone()[0]
    print(f'  让球赔率: {hcp_count} 条')
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_type LIKE '%意甲%')")
    tg_count = cursor.fetchone()[0]
    print(f'  总进球赔率: {tg_count} 条')
    
    cursor.execute("SELECT COUNT(*) FROM score_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_type LIKE '%意甲%')")
    score_count = cursor.fetchone()[0]
    print(f'  比分赔率: {score_count} 条')
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE match_type LIKE '%意甲%' AND actual_score IS NOT NULL")
    result_count = cursor.fetchone()[0]
    print(f'  已有结果: {result_count} 场')
    
    conn.close()
    
    print('\n' + '=' * 60)
    print(f'同步完成! 结束时间: {datetime.now().isoformat()}')
    print('=' * 60)

if __name__ == '__main__':
    main()
