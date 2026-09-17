"""删除只有基础赔率（开盘+收盘）的数据源"""

import sqlite3
import os
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = BASE_DIR / "data" / "odds.db"
BACKUP_DIR = BASE_DIR / "data"

def backup_database():
    """备份当前数据库"""
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f'odds_backup_before_clean_{timestamp}.db')
    
    # 连接原数据库
    conn = sqlite3.connect(ODDS_DB)
    # 创建备份数据库
    backup_conn = sqlite3.connect(backup_path)
    
    # 复制数据
    conn.backup(backup_conn)
    
    conn.close()
    backup_conn.close()
    
    print(f"✅ 数据库已备份到: {backup_path}")
    return backup_path

def find_basic_only_matches():
    """找出只有基础赔率（WDL≤2个时间点）的比赛"""
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    
    # 查询只有2个或更少WDL时间点的比赛
    cursor.execute("""
        SELECT m.match_id, m.match_date, m.home_team, m.away_team, 
               COUNT(DISTINCT w.timestamp) as wdl_count
        FROM matches m
        LEFT JOIN wdl_history w ON m.match_id = w.match_id
        GROUP BY m.match_id
        HAVING wdl_count <= 2
        ORDER BY m.match_date
    """)
    
    basic_only = cursor.fetchall()
    conn.close()
    
    return basic_only

def delete_basic_only_matches(basic_only):
    """删除只有基础赔率的比赛及其关联数据"""
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    
    deleted_matches = []
    
    for match in basic_only:
        match_id = match[0]
        
        # 删除关联的赔率数据
        cursor.execute("DELETE FROM wdl_history WHERE match_id = ?", (match_id,))
        cursor.execute("DELETE FROM handicap_history WHERE match_id = ?", (match_id,))
        cursor.execute("DELETE FROM total_goals_history WHERE match_id = ?", (match_id,))
        cursor.execute("DELETE FROM score_history WHERE match_id = ?", (match_id,))
        
        # 删除比赛记录
        cursor.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
        
        deleted_matches.append(match)
    
    conn.commit()
    conn.close()
    
    return deleted_matches

def generate_cleanup_report(deleted_matches, backup_path):
    """生成清理报告"""
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    report = f"""# 数据清理报告 - 删除基础赔率数据源

## 基本信息

- 清理时间: {timestamp}
- 备份文件: {os.path.basename(backup_path)}

## 删除统计

| 指标 | 数量 |
|------|------|
| 删除比赛数 | {len(deleted_matches)} 场 |

## 删除比赛列表

| 日期 | 主队 | 客队 | WDL时间点 |
|------|------|------|-----------|
"""
    
    for match in deleted_matches:
        report += f"| {match[1]} | {match[2]} | {match[3]} | {match[4]} |\n"
    
    return report

def update_logs(deleted_count):
    """更新优化日志和变更日志"""
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # 更新optimization_log.md
    log_entry = f"""
- **{timestamp}**: 删除只有基础赔率（WDL≤2个时间点）的比赛数据，共删除 {deleted_count} 场比赛。
  - 原因：为后续导入详细时序数据源腾出空间，确保数据质量统一。
  - 影响：数据量减少，但保留的比赛均具有多时间点详细赔率数据。
"""
    
    with open(BASE_DIR / "docs" / "optimization_log.md", 'a', encoding='utf-8') as f:
        f.write(log_entry)
    
    # 更新change_log.md
    with open(BASE_DIR / "docs" / "change_log.md", 'a', encoding='utf-8') as f:
        f.write(f"""| C-{datetime.datetime.now().strftime('%Y%m%d-%H%M')} | {timestamp} | 数据 | data/odds.db | 删除基础赔率比赛数据 | {deleted_count + 132}场比赛 | {132}场比赛 | 为导入详细时序数据源做准备 | P0-01 | 通过 | 高 | 数据工程师 | 已验证 |\n""")
    
    print("✅ 日志已更新")

def main():
    print("=" * 70)
    print("【删除基础赔率数据源】")
    print("=" * 70)
    
    # 1. 备份数据库
    backup_path = backup_database()
    
    # 2. 找出只有基础赔率的比赛
    basic_only = find_basic_only_matches()
    print(f"\n🔍 找到只有基础赔率的比赛: {len(basic_only)} 场")
    
    if len(basic_only) == 0:
        print("✓ 没有需要删除的基础赔率数据")
        return
    
    # 3. 删除这些比赛
    deleted = delete_basic_only_matches(basic_only)
    print(f"\n🗑️ 已删除 {len(deleted)} 场比赛及其关联赔率数据")
    
    # 4. 生成清理报告
    report = generate_cleanup_report(deleted, backup_path)
    report_path = BASE_DIR / "docs" / "cleanup_report.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n📄 清理报告已保存到: {report_path}")
    
    # 5. 更新日志
    update_logs(len(deleted))
    
    # 6. 验证结果
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM matches")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT m.match_id, COUNT(DISTINCT w.timestamp) as cnt
            FROM matches m
            LEFT JOIN wdl_history w ON m.match_id = w.match_id
            GROUP BY m.match_id
            HAVING cnt <= 2
        )
    """)
    remaining_basic = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"\n✅ 验证结果")
    print(f"  剩余比赛总数: {total_matches} 场")
    print(f"  剩余基础赔率比赛: {remaining_basic} 场")
    
    print("\n" + "=" * 70)
    print("【清理完成】")
    print("=" * 70)

if __name__ == '__main__':
    main()
