"""检查odds_timing.db的表结构"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds_timing.db")
cursor = conn.cursor()

# 查看所有表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print('数据库表:', [t[0] for t in tables])

# 查看每个表的结构
for table in tables:
    table_name = table[0]
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    print(f'\n{table_name} 表结构:')
    for col in columns:
        print(f'  {col[1]} ({col[2]})')
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    count = cursor.fetchone()[0]
    print(f'  记录数: {count}')

conn.close()
