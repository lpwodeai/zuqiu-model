"""
赔率时序数据质量验证脚本

功能：
1. 验证数据完整性
2. 检查赔率值范围
3. 验证时间点分布
4. 检测数据异常
5. 生成质量报告
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/odds_timing.db')

def get_conn():
    return sqlite3.connect(DB_PATH)

def validate_data():
    """执行数据验证"""
    conn = get_conn()
    cursor = conn.cursor()
    
    print(f'\n{"="*70}')
    print('赔率时序数据质量验证报告')
    print(f'生成时间: {datetime.now().isoformat()}')
    print(f'{"="*70}')
    
    issues = []
    warnings = []
    
    # 1. 比赛数据验证
    print('\n--- 1. 比赛数据验证 ---')
    cursor.execute("SELECT COUNT(*) FROM matches")
    match_count = cursor.fetchone()[0]
    print(f'比赛总数: {match_count}')
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE home_team IS NULL OR home_team = ''")
    empty_home = cursor.fetchone()[0]
    if empty_home > 0:
        issues.append(f'发现 {empty_home} 场比赛主队为空')
        print(f'❌ 主队为空: {empty_home} 场')
    else:
        print('✅ 主队数据完整')
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE away_team IS NULL OR away_team = ''")
    empty_away = cursor.fetchone()[0]
    if empty_away > 0:
        issues.append(f'发现 {empty_away} 场比赛客队为空')
        print(f'❌ 客队为空: {empty_away} 场')
    else:
        print('✅ 客队数据完整')
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE match_date IS NULL OR match_date = ''")
    empty_date = cursor.fetchone()[0]
    if empty_date > 0:
        issues.append(f'发现 {empty_date} 场比赛日期为空')
        print(f'❌ 比赛日期为空: {empty_date} 场')
    else:
        print('✅ 比赛日期完整')
    
    # 2. WDL赔率验证
    print('\n--- 2. 胜平负赔率数据验证 ---')
    cursor.execute("SELECT COUNT(*) FROM wdl_timing")
    wdl_count = cursor.fetchone()[0]
    print(f'WDL记录总数: {wdl_count}')
    
    # 检查赔率值范围
    cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE win_a < 1.01 OR win_a > 200.0")
    bad_win_a = cursor.fetchone()[0]
    if bad_win_a > 0:
        issues.append(f'发现 {bad_win_a} 条主胜赔率值超出范围(1.01-200)')
        print(f'❌ 主胜赔率值异常: {bad_win_a} 条')
    else:
        print('✅ 主胜赔率值范围正确')
    
    cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE draw < 1.01 OR draw > 200.0")
    bad_draw = cursor.fetchone()[0]
    if bad_draw > 0:
        issues.append(f'发现 {bad_draw} 条平局赔率值超出范围')
        print(f'❌ 平局赔率值异常: {bad_draw} 条')
    else:
        print('✅ 平局赔率值范围正确')
    
    cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE win_b < 1.01 OR win_b > 200.0")
    bad_win_b = cursor.fetchone()[0]
    if bad_win_b > 0:
        issues.append(f'发现 {bad_win_b} 条客胜赔率值超出范围')
        print(f'❌ 客胜赔率值异常: {bad_win_b} 条')
    else:
        print('✅ 客胜赔率值范围正确')
    
    # 检查时间点分布
    cursor.execute("""
        SELECT match_id, COUNT(*) as cnt 
        FROM wdl_timing 
        GROUP BY match_id 
        HAVING cnt < 2
    """)
    low_time_points = cursor.fetchall()
    if len(low_time_points) > 0:
        warnings.append(f'有 {len(low_time_points)} 场比赛时间点少于2个')
        print(f'⚠️ 时间点少于2个: {len(low_time_points)} 场')
    else:
        print('✅ 所有比赛至少有2个时间点')
    
    # 3. 让球赔率验证
    print('\n--- 3. 让球赔率数据验证 ---')
    cursor.execute("SELECT COUNT(*) FROM handicap_timing")
    hcp_count = cursor.fetchone()[0]
    print(f'让球赔率记录总数: {hcp_count}')
    
    cursor.execute("SELECT COUNT(*) FROM handicap_timing WHERE hcp_win < 1.01 OR hcp_win > 200.0")
    bad_hcp_win = cursor.fetchone()[0]
    if bad_hcp_win > 0:
        issues.append(f'发现 {bad_hcp_win} 条让球胜赔率值异常')
        print(f'❌ 让球胜赔率值异常: {bad_hcp_win} 条')
    else:
        print('✅ 让球胜赔率值范围正确')
    
    # 4. 总进球赔率验证
    print('\n--- 4. 总进球赔率数据验证 ---')
    cursor.execute("SELECT COUNT(*) FROM total_goals_timing")
    tg_count = cursor.fetchone()[0]
    print(f'总进球赔率记录总数: {tg_count}')
    
    # 5. 比分赔率验证
    print('\n--- 5. 比分赔率数据验证 ---')
    cursor.execute("SELECT COUNT(*) FROM score_timing")
    score_count = cursor.fetchone()[0]
    print(f'比分赔率记录总数: {score_count}')
    
    cursor.execute("SELECT COUNT(*) FROM score_timing WHERE odds < 1.01 OR odds > 200.0")
    bad_score_odds = cursor.fetchone()[0]
    if bad_score_odds > 0:
        issues.append(f'发现 {bad_score_odds} 条比分赔率值异常')
        print(f'❌ 比分赔率值异常: {bad_score_odds} 条')
    else:
        print('✅ 比分赔率值范围正确')
    
    # 6. 时间点分布统计
    print('\n--- 6. 时间点分布统计 ---')
    cursor.execute("""
        SELECT cnt, COUNT(*) as match_count
        FROM (SELECT match_id, COUNT(*) as cnt FROM wdl_timing GROUP BY match_id)
        GROUP BY cnt
        ORDER BY cnt
    """)
    total_matches_with_wdl = 0
    for row in cursor.fetchall():
        total_matches_with_wdl += row[1]
        print(f'  {row[0]}个时间点: {row[1]}场')
    
    # 7. 数据来源统计
    print('\n--- 7. 数据来源统计 ---')
    cursor.execute("""
        SELECT source, COUNT(*) as count 
        FROM matches 
        GROUP BY source 
        ORDER BY count DESC
    """)
    for row in cursor.fetchall():
        print(f'  {row[0]}: {row[1]}场')
    
    conn.close()
    
    # 8. 综合评估
    print(f'\n{"="*70}')
    print('综合评估')
    print(f'{"="*70}')
    
    print(f'\n数据总量:')
    print(f'  比赛数: {match_count}')
    print(f'  WDL记录: {wdl_count}')
    print(f'  让球记录: {hcp_count}')
    print(f'  总进球记录: {tg_count}')
    print(f'  比分记录: {score_count}')
    
    print(f'\n问题列表 ({len(issues)}个):')
    if issues:
        for i, issue in enumerate(issues, 1):
            print(f'  {i}. {issue}')
    else:
        print('  无')
    
    print(f'\n警告列表 ({len(warnings)}个):')
    if warnings:
        for i, warning in enumerate(warnings, 1):
            print(f'  {i}. {warning}')
    else:
        print('  无')
    
    # 评分
    total_checks = match_count + wdl_count + hcp_count + tg_count + score_count
    issue_score = max(0, 100 - len(issues) * 10 - len(warnings) * 5)
    print(f'\n数据质量评分: {issue_score}/100')
    
    if issue_score >= 90:
        print('评估结果: 🟢 优秀')
    elif issue_score >= 70:
        print('评估结果: 🟡 良好')
    elif issue_score >= 50:
        print('评估结果: 🟠 一般')
    else:
        print('评估结果: 🔴 较差')
    
    print(f'\n{"="*70}')
    
    return issues, warnings

if __name__ == '__main__':
    validate_data()
