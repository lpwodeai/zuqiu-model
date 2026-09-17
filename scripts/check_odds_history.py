"""检查odds.db的当前数据状态并查询相关日志"""

import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = BASE_DIR / "data" / "odds.db"

def check_odds_db():
    """检查odds.db的当前状态"""
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    
    print("=" * 70)
    print("【odds.db 当前状态】")
    print("=" * 70)
    
    # 获取各表记录数
    tables = ['matches', 'wdl_history', 'handicap_history', 'total_goals_history', 'score_history']
    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        print(f"  {table}: {count} 条记录")
    
    # 获取比赛按联赛分布
    print("\n【比赛按联赛分布】")
    cursor.execute("SELECT league_name, COUNT(*) FROM matches GROUP BY league_name")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]} 场")
    
    # 获取英超比赛数量
    cursor.execute("SELECT COUNT(*) FROM matches WHERE league_name='英超'")
    epl_count = cursor.fetchone()[0]
    print(f"\n英超比赛总数: {epl_count} 场")
    
    # 获取英超比赛的时间范围
    cursor.execute("SELECT MIN(match_date), MAX(match_date) FROM matches WHERE league_name='英超'")
    dates = cursor.fetchone()
    print(f"英超比赛日期范围: {dates[0]} ~ {dates[1]}")
    
    # 获取英超比赛的轮次分布（如果有round字段）
    cursor.execute("PRAGMA table_info(matches)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'round' in columns:
        print("\n【英超比赛轮次分布】")
        cursor.execute("SELECT round, COUNT(*) FROM matches WHERE league_name='英超' GROUP BY round ORDER BY round")
        for row in cursor.fetchall():
            print(f"  第{row[0]}轮: {row[1]} 场")
    
    conn.close()

def check_import_logs():
    """检查导入日志"""
    log_files = [
        BASE_DIR / "data" / "import_log.md",
        BASE_DIR / "docs" / "optimization_log.md",
        BASE_DIR / "docs" / "change_log.md"
    ]
    
    print("\n" + "=" * 70)
    print("【导入日志检查】")
    print("=" * 70)
    
    for log_file in log_files:
        if os.path.exists(log_file):
            print(f"\n📄 {os.path.basename(log_file)}")
            with open(log_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # 搜索与132场相关的记录
                if '132' in content:
                    print("  找到包含'132'的内容:")
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if '132' in line:
                            start = max(0, i-2)
                            end = min(len(lines), i+3)
                            for j in range(start, end):
                                print(f"    {lines[j]}")
                            print()
                
                # 搜索与英超相关的记录
                if '英超' in content and '场' in content:
                    print("  找到英超比赛数量相关内容:")
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if '英超' in line and '场' in line and ('赔率' in line or '比赛' in line):
                            print(f"    {line}")
    
    # 检查备份文件
    print("\n" + "=" * 70)
    print("【备份文件检查】")
    print("=" * 70)
    
    backup_dir = BASE_DIR / "data"
    backup_files = [f for f in os.listdir(backup_dir) if 'backup' in f.lower() and f.endswith('.db')]
    
    if backup_files:
        for backup_file in sorted(backup_files):
            backup_path = os.path.join(backup_dir, backup_file)
            size = os.path.getsize(backup_path) / (1024 * 1024)
            print(f"  {backup_file}: {size:.2f} MB")
            
            # 检查备份文件中的数据
            try:
                conn = sqlite3.connect(backup_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM matches")
                count = cursor.fetchone()[0]
                print(f"    matches表记录数: {count}")
                cursor.execute("SELECT COUNT(*) FROM wdl_history")
                wdl_count = cursor.fetchone()[0]
                print(f"    wdl_history表记录数: {wdl_count}")
                conn.close()
            except Exception as e:
                print(f"    读取失败: {e}")
    else:
        print("  未找到备份文件")

if __name__ == '__main__':
    check_odds_db()
    check_import_logs()
