"""
T-004 总进球预测模块 — 数据探索
===============================
分析 total_goals_history 数据结构和可用性
"""
import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
conn = sqlite3.connect(DB_PATH)

# 1. 表结构
print("=" * 60)
print("1. total_goals_history 表结构")
print("=" * 60)
schema = pd.read_sql("PRAGMA table_info(total_goals_history)", conn)
print(schema.to_string())

# 2. 数据量统计
print("\n" + "=" * 60)
print("2. 数据量统计")
print("=" * 60)
total_rows = pd.read_sql("SELECT COUNT(*) as cnt FROM total_goals_history", conn)['cnt'].iloc[0]
unique_matches = pd.read_sql("SELECT COUNT(DISTINCT match_id) as cnt FROM total_goals_history", conn)['cnt'].iloc[0]
print(f"总记录数: {total_rows}")
print(f"唯一 match_id: {unique_matches}")
print(f"平均每场记录数: {total_rows/unique_matches:.1f}")

# 3. 通过映射表关联后的覆盖率
print("\n" + "=" * 60)
print("3. 通过 match_id_mapping 关联后的覆盖率")
print("=" * 60)
df = pd.read_sql("""
    SELECT 
        COUNT(DISTINCT h.match_id) as total_hist,
        COUNT(DISTINCT m.matches_match_id) as mapped,
        COUNT(DISTINCT CASE WHEN m.matches_match_id IS NOT NULL THEN h.match_id END) as matched_hist
    FROM total_goals_history h
    LEFT JOIN match_id_mapping m ON h.match_id = m.sh_match_id
""", conn)
row = df.iloc[0]
print(f"history 唯一 match_id: {row['total_hist']}")
print(f"映射成功: {row['matched_hist']} ({row['matched_hist']/row['total_hist']*100:.1f}%)")
print(f"对应 matches match_id: {row['mapped']}")

# 4. 按联赛统计可用的比赛数
print("\n" + "=" * 60)
print("4. 按联赛统计可用比赛数（含实际总进球）")
print("=" * 60)
df = pd.read_sql("""
    SELECT 
        mt.match_type as league,
        COUNT(DISTINCT h.match_id) as tg_matches,
        COUNT(DISTINCT CASE WHEN mt.actual_total_goals IS NOT NULL THEN h.match_id END) as with_result
    FROM total_goals_history h
    INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
    INNER JOIN matches mt ON m.matches_match_id = mt.match_id
    GROUP BY mt.match_type
    ORDER BY mt.match_type
""", conn)
print(df.to_string())

# 5. 总进球分布
print("\n" + "=" * 60)
print("5. 总进球分布（所有可用比赛）")
print("=" * 60)
df = pd.read_sql("""
    SELECT 
        mt.actual_total_goals,
        COUNT(*) as cnt
    FROM total_goals_history h
    INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
    INNER JOIN matches mt ON m.matches_match_id = mt.match_id
    WHERE mt.actual_total_goals IS NOT NULL
    GROUP BY mt.actual_total_goals
    ORDER BY mt.actual_total_goals
""", conn)
total_with_result = df['cnt'].sum()
print(f"有实际总进球的比赛: {total_with_result}")
print(df.to_string())
print(f"\n大/小球分布:")
over25 = df[df['actual_total_goals'] > 2]['cnt'].sum()
under25 = df[df['actual_total_goals'] <= 2]['cnt'].sum()
print(f"  大球 (>2.5): {over25} ({over25/total_with_result*100:.1f}%)")
print(f"  小球 (≤2.5): {under25} ({under25/total_with_result*100:.1f}%)")

# 6. 时间戳分布
print("\n" + "=" * 60)
print("6. 时间戳分析（过滤前）")
print("=" * 60)
df = pd.read_sql("""
    SELECT 
        MIN(timestamp) as min_ts,
        MAX(timestamp) as max_ts,
        COUNT(*) as total,
        SUM(CASE WHEN timestamp < '2026-07-01' THEN 1 ELSE 0 END) as valid_cnt,
        SUM(CASE WHEN timestamp >= '2026-07-01' THEN 1 ELSE 0 END) as invalid_cnt
    FROM total_goals_history
""", conn)
row = df.iloc[0]
print(f"时间范围: {row['min_ts']} ~ {row['max_ts']}")
print(f"有效记录 (<2026-07): {row['valid_cnt']} ({row['valid_cnt']/row['total']*100:.1f}%)")
print(f"需过滤 (>=2026-07): {row['invalid_cnt']} ({row['invalid_cnt']/row['total']*100:.1f}%)")

# 7. 样例数据
print("\n" + "=" * 60)
print("7. 样例数据（3条）")
print("=" * 60)
df = pd.read_sql("""
    SELECT h.match_id, h.timestamp, h.goals_0, h.goals_1, h.goals_2, h.goals_3, 
           h.goals_4, h.goals_5, h.goals_6, h.goals_7_plus,
           mt.actual_total_goals
    FROM total_goals_history h
    INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
    INNER JOIN matches mt ON m.matches_match_id = mt.match_id
    WHERE mt.actual_total_goals IS NOT NULL
    LIMIT 3
""", conn)
print(df.to_string())

# 8. 总进球类别分布（用于分类模型）
print("\n" + "=" * 60)
print("8. 总进球类别分布")
print("=" * 60)
df = pd.read_sql("""
    SELECT 
        CASE 
            WHEN mt.actual_total_goals = 0 THEN '0球'
            WHEN mt.actual_total_goals = 1 THEN '1球'
            WHEN mt.actual_total_goals = 2 THEN '2球'
            WHEN mt.actual_total_goals = 3 THEN '3球'
            WHEN mt.actual_total_goals = 4 THEN '4球'
            WHEN mt.actual_total_goals = 5 THEN '5球'
            ELSE '6+球'
        END as goals_category,
        COUNT(*) as cnt
    FROM total_goals_history h
    INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
    INNER JOIN matches mt ON m.matches_match_id = mt.match_id
    WHERE mt.actual_total_goals IS NOT NULL
    GROUP BY mt.actual_total_goals
    ORDER BY mt.actual_total_goals
""", conn)
print(df.to_string())

conn.close()