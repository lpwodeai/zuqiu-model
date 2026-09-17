"""
批量导入脚本 - 意甲2025-2026赛季详细赔率时序数据

数据格式规范：
=============
每场比赛数据结构：
{
    'match_id': str,           # 比赛ID，格式: YYYY-MM-DD_主队_客队（中文队名）
    'home_team': str,          # 主队名称（中文）
    'away_team': str,          # 客队名称（中文）
    'match_date': str,         # 比赛日期，格式: YYYY-MM-DD
    'match_time': str,         # 比赛时间，格式: HH:MM（可选）
    'league': str,             # 联赛名称: '意甲2025-2026赛季'
    'round': int,              # 轮次（1-38）
    'status': str,             # 比赛状态: 'completed'/'pending'
    'source': str,             # 数据源: 'MANUAL_IMPORT'/'EXTERNAL_SYSTEM'/'SCRAPER'
    
    'wdl_timing': list,        # 胜平负赔率时序 [(timestamp, win_a, draw, win_b), ...]
                               # timestamp格式: 'YYYY-MM-DD HH:MM:SS'
                               # win_a: 主胜赔率, draw: 平局赔率, win_b: 客胜赔率
    
    'handicap_timing': list,   # 让球赔率时序 [(timestamp, handicap, hcp_win, hcp_draw, hcp_lose), ...]
                               # handicap: 让球盘口（负数为主队让球）
    
    'score_timing': dict,      # 比分赔率时序 {'timestamp': [(score, odds), ...], ...}
                               # score格式: 'X:Y'
    
    'total_goals_timing': list, # 总进球赔率时序
                               # [(timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus), ...]
    
    'result': dict,            # 比赛结果（已完赛必填）
        {
            'actual_score': str,       # 实际比分，格式: X:Y
            'actual_wdl': str,         # 胜平负结果: '胜'/'平'/'负'（主队视角）
            'actual_handicap': str,    # 让球结果: '胜'/'平'/'负'
            'actual_total_goals': int, # 总进球数
            'verified': int            # 是否已验证: 0/1
        }
}

数据质量标准：
=============
1. 胜平负/让球/总进球赔率至少3条记录（开盘+中间+收盘）
2. 比分赔率至少10个选项（含胜/平/负及其它）
3. 赔率值范围: 1.01-200.0
4. 时间顺序排列，最后记录在比赛前至少2小时
5. 球队名称使用中文，与SERIEA_2025-26.csv保持一致

意甲球队中文名称列表（必须使用以下名称）：
===========================================
Atalanta, Bologna, Cagliari, Como, Cremonese, Fiorentina, Genoa, Inter, Juventus,
Lazio, Lecce, Milan, Napoli, Parma, Pisa, Roma, Sassuolo, Torino, Udinese, Verona
"""

from reusable_import import import_match
import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "odds_timing.db"
LOG_FILE = BASE_DIR / "data" / "seriea_import_log.md"

# 意甲球队中文名称规范映射
SERIEA_TEAM_NAMES = {
    'Atalanta': 'Atalanta',
    'Bologna': 'Bologna',
    'Cagliari': 'Cagliari',
    'Como': 'Como',
    'Cremonese': 'Cremonese',
    'Fiorentina': 'Fiorentina',
    'Genoa': 'Genoa',
    'Inter': 'Inter',
    'Juventus': 'Juventus',
    'Lazio': 'Lazio',
    'Lecce': 'Lecce',
    'Milan': 'Milan',
    'Napoli': 'Napoli',
    'Parma': 'Parma',
    'Pisa': 'Pisa',
    'Roma': 'Roma',
    'Sassuolo': 'Sassuolo',
    'Torino': 'Torino',
    'Udinese': 'Udinese',
    'Verona': 'Verona',
}


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
    if match_data.get('home_team') not in SERIEA_TEAM_NAMES:
        errors.append(f"主队名称不规范: {match_data.get('home_team')}")
    if match_data.get('away_team') not in SERIEA_TEAM_NAMES:
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
            if not (1.01 <= draw <= 200.0):
                errors.append(f"平局赔率超出范围: {draw}")
            if not (1.01 <= win_b <= 200.0):
                errors.append(f"客胜赔率超出范围: {win_b}")
    
    hcp_timing = match_data.get('handicap_timing', [])
    if len(hcp_timing) > 0 and len(hcp_timing) < 3:
        errors.append(f"让球赔率记录不足（至少3条）: {len(hcp_timing)}条")
    
    score_timing = match_data.get('score_timing', {})
    total_scores = sum(len(scores) for scores in score_timing.values())
    if total_scores > 0 and total_scores < 10:
        errors.append(f"比分赔率选项不足（至少10个）: {total_scores}个")
    
    # 比赛结果验证（已完赛）
    if match_data.get('status') == 'completed':
        result = match_data.get('result')
        if not result:
            errors.append("已完赛比赛缺少结果数据")
        else:
            if result.get('actual_wdl') not in ['胜', '平', '负']:
                errors.append(f"胜平负结果错误: {result.get('actual_wdl')}")
            if result.get('actual_handicap') not in ['胜', '平', '负']:
                errors.append(f"让球结果错误: {result.get('actual_handicap')}")
    
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
        header = """# 意甲2025-2026赛季比赛数据导入日志

本文件记录所有通过批量导入脚本导入的比赛数据，用于模型训练和数据分析。

## 数据说明

- **联赛**: 意甲2025-2026赛季
- **数据类型**: 胜平负赔率、让球胜平负赔率、比分赔率、总进球赔率、比赛结果
- **数据源**: MANUAL_IMPORT（手动导入）
- **更新时间**: 自动更新

---

"""
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write(header + log_entry)


def get_db_statistics():
    """获取数据库统计信息"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE league LIKE '%意甲%'")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("SELECT round, COUNT(*) FROM matches WHERE league LIKE '%意甲%' GROUP BY round ORDER BY round")
    round_stats = {row[0]: row[1] for row in cursor.fetchall()}
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE league LIKE '%意甲%' AND status='completed'")
    completed_matches = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    wdl_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM handicap_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    hcp_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM score_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
    score_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_timing WHERE match_id IN (SELECT match_id FROM matches WHERE league LIKE '%意甲%')")
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
    
    print(f"开始批量导入意甲数据，共 {len(matches_data)} 场比赛...\n")
    
    for i, match_data in enumerate(matches_data, 1):
        match_id = match_data['match_id']
        print(f"[{i}/{len(matches_data)}] 处理: {match_data['home_team']} vs {match_data['away_team']}")
        
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
    
    print(f"\n批量导入完成！成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}")
    return success_count, fail_count


# ==============================================================================
# 比赛数据列表 - 在此添加新比赛数据
# ==============================================================================
MATCHES_DATA = [
    # 示例数据结构，实际数据将由用户提供
    # {
    #     'match_id': '2025-08-23_Genoa_Lecce',
    #     'home_team': 'Genoa',
    #     'away_team': 'Lecce',
    #     'match_date': '2025-08-23',
    #     'match_time': '17:30',
    #     'league': '意甲2025-2026赛季',
    #     'round': 1,
    #     'status': 'completed',
    #     'source': 'MANUAL_IMPORT',
    #     'wdl_timing': [
    #         ('2025-08-20 10:00:00', 1.90, 3.30, 4.50),
    #         ('2025-08-22 15:30:00', 1.85, 3.40, 4.60),
    #         ('2025-08-23 15:30:00', 1.95, 3.25, 4.20),
    #     ],
    #     'handicap_timing': [
    #         ('2025-08-20 10:00:00', -0.5, 1.90, None, 1.95),
    #         ('2025-08-22 15:30:00', -0.5, 1.85, None, 2.00),
    #         ('2025-08-23 15:30:00', -0.75, 2.03, None, 1.83),
    #     ],
    #     'score_timing': {
    #         '2025-08-23 15:30:00': [
    #             ('1:0', 7.00), ('2:0', 13.00), ('2:1', 8.50),
    #             ('0:0', 5.00), ('1:1', 4.20),
    #             ('0:1', 6.50), ('0:2', 11.00), ('1:2', 9.00),
    #             ('胜其它', 40.00), ('平其它', 50.00), ('负其它', 35.00),
    #         ],
    #     },
    #     'total_goals_timing': [
    #         ('2025-08-20 10:00:00', 15.00, 7.00, 4.20, 3.40, 5.00, 9.00, 18.00, 30.00),
    #         ('2025-08-23 15:30:00', 14.00, 6.50, 4.00, 3.50, 5.50, 10.00, 20.00, 35.00),
    #     ],
    #     'result': {
    #         'actual_score': '0:0',
    #         'actual_wdl': '平',
    #         'actual_handicap': '负',
    #         'actual_total_goals': 0,
    #         'verified': 1
    #     }
    # },
]


if __name__ == '__main__':
    import sys
    
    force = False
    if len(sys.argv) > 1 and sys.argv[1] == '--force':
        force = True
    
    if not MATCHES_DATA:
        print("⚠️ 没有可导入的比赛数据！")
        print("请在 MATCHES_DATA 列表中添加意甲详细赔率时序数据。")
        print("数据格式参考文件头部的说明。")
        sys.exit(0)
    
    batch_import_matches(MATCHES_DATA, force=force)
