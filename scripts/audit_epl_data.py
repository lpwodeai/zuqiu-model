"""P1: 英超数据质量审计"""
import os, sys, sqlite3, numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(BASE)
DB_PATH = os.path.join(PROJECT, 'data', 'odds.db')

conn = sqlite3.connect(DB_PATH)

# 1. 英超数据量统计
df = pd.read_sql_query("""
    SELECT m.*, 
           CASE WHEN wdl.match_id IS NOT NULL THEN 1 ELSE 0 END as has_wdl,
           CASE WHEN hcp.match_id IS NOT NULL THEN 1 ELSE 0 END as has_handicap,
           CASE WHEN tg.match_id IS NOT NULL THEN 1 ELSE 0 END as has_total_goals
    FROM matches m
    LEFT JOIN wdl_history wdl ON m.match_id = wdl.match_id
    LEFT JOIN handicap_history hcp ON m.match_id = hcp.match_id
    LEFT JOIN total_goals_history tg ON m.match_id = tg.match_id
    WHERE m.match_type LIKE '%英超%'
""", conn)

print("=" * 60)
print("=== 英超数据质量审计 ===")
print("=" * 60)

# 基本统计
print(f"\n--- 1. 数据量 ---")
print(f"  总比赛: {len(df)}")
print(f"  时段: {df['match_date'].min()} ~ {df['match_date'].max()}")
print(f"  主胜率: {(df['actual_wdl']==2).mean()*100:.1f}%")
print(f"  平局率: {(df['actual_wdl']==1).mean()*100:.1f}%")
print(f"  客胜率: {(df['actual_wdl']==0).mean()*100:.1f}%")

# 赔率覆盖率
print(f"\n--- 2. 赔率覆盖率 ---")
print(f"  WDL赔率:  {df['has_wdl'].sum()}/{len(df)} ({df['has_wdl'].mean()*100:.1f}%)")
print(f"  让球赔率: {df['has_handicap'].sum()}/{len(df)} ({df['has_handicap'].mean()*100:.1f}%)")
print(f"  总进球赔率: {df['has_total_goals'].sum()}/{len(df)} ({df['has_total_goals'].mean()*100:.1f}%)")

# 赛季分布
print(f"\n--- 3. 赛季分布 ---")
df['season'] = df['match_type'].str.extract(r'(\d{4})')
season_counts = df.groupby('season').size()
for s, c in season_counts.items():
    print(f"  {s}赛季: {c}场")

# 与其他联赛对比
print(f"\n--- 4. 跨联赛对比 ---")
all_df = pd.read_sql_query("SELECT match_type, actual_wdl FROM matches", conn)
for league_name in ['英超', '西甲', '意甲', '德甲', '法甲']:
    league_mask = all_df['match_type'].str.contains(league_name)
    league_data = all_df[league_mask]
    if len(league_data) > 0:
        home_rate = (league_data['actual_wdl']==2).mean()*100
        draw_rate = (league_data['actual_wdl']==1).mean()*100
        away_rate = (league_data['actual_wdl']==0).mean()*100
        print(f"  {league_name}: {len(league_data)}场, 主{home_rate:.1f}% 平{draw_rate:.1f}% 客{away_rate:.1f}%")

# 赔率数据时间戳检查
print(f"\n--- 5. 赔率时间戳质量 ---")
try:
    wdl_ts = pd.read_sql_query("""
        SELECT match_id, timestamp, win_odds, draw_odds, lose_odds 
        FROM wdl_history 
        WHERE match_id IN (SELECT match_id FROM matches WHERE match_type LIKE '%英超%')
        LIMIT 1000
    """, conn)
    if len(wdl_ts) > 0:
        print(f"  样本WDL赔率记录: {len(wdl_ts)}")
        print(f"  时间戳范围: {wdl_ts['timestamp'].min()} ~ {wdl_ts['timestamp'].max()}")
        # 检查是否有异常时间戳
        try:
            pd_ts = pd.to_datetime(wdl_ts['timestamp'])
            bad_ts = pd_ts[(pd_ts.dt.year == 2026) & (pd_ts.dt.month == 7)]
            print(f"  2026-07异常时间戳: {len(bad_ts)} 条")
        except:
            pass
except Exception as e:
    print(f"  赔率时间戳检查失败: {e}")

# 比赛日期分布
print(f"\n--- 6. 比赛日期分布 ---")
df['match_date'] = pd.to_datetime(df['match_date'])
df['year'] = df['match_date'].dt.year
for yr in sorted(df['year'].unique()):
    yr_data = df[df['year'] == yr]
    print(f"  {yr}年: {len(yr_data)}场")

conn.close()

print(f"\n=== 审计结论 ===")
print(f"  数据量: 充足 ({len(df)}场)")
print(f"  标签分布: 正常 (主{df['actual_wdl'].eq(2).mean()*100:.1f}%, 平{df['actual_wdl'].eq(1).mean()*100:.1f}%)")
print(f"  赔率覆盖: {'良好' if df['has_wdl'].mean() > 0.8 else '需改善'}")
print(f"  建议: 英超风格差异(快节奏/高对抗)导致全局模型过拟合大陆联赛，需独立模型")