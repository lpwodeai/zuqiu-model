import pandas as pd
import sqlite3
import os
import hashlib
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'five_leagues.db')
CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'h2h_full_stats_sample(1).csv')

COMPETITION_MAP = {
    'Premier League': 'PL',
    'La Liga': 'SA',
    'Serie A': 'SerieA',
    'Bundesliga': 'BL1',
    'Ligue 1': 'FL1'
}

TEAM_NAME_MAP = {
    'Liverpool': '利物浦',
    'Bournemouth': '伯恩茅斯',
    'Aston Villa': '阿斯顿维拉',
    'Newcastle United': '纽卡斯尔',
    'Brighton & Hove Albion': '布莱顿',
    'Fulham': '富勒姆',
    'Sunderland': '桑德兰',
    'West Ham United': '西汉姆',
    'Tottenham Hotspur': '热刺',
    'Burnley': '伯恩利',
    'Wolverhampton Wanderers': '狼队',
    'Manchester City': '曼城',
    'Nottingham Forest': '诺丁汉森林',
    'Brentford': '布伦特福德',
    'Chelsea': '切尔西',
    'Crystal Palace': '水晶宫',
    'Manchester United': '曼联',
    'Arsenal': '阿森纳',
    'Leeds United': '利兹联',
    'Everton': '埃弗顿',
    'Barcelona': '巴塞罗那',
    'Real Madrid': '皇家马德里',
    'Atletico Madrid': '马德里竞技',
    'Valencia': '瓦伦西亚',
    'Sevilla': '塞维利亚',
    'Real Betis': '皇家贝蒂斯',
    'Villarreal': '比利亚雷亚尔',
    'Athletic Bilbao': '毕尔巴鄂',
    'Real Sociedad': '皇家社会',
    'Getafe': '赫塔费',
    'Espanyol': '西班牙人',
    'Celta Vigo': '塞尔塔',
    'Osasuna': '奥萨苏纳',
    'Rayo Vallecano': '巴列卡诺',
    'Mallorca': '马洛卡',
    'Girona': '赫罗纳',
    'Levante': '莱万特',
    'Alaves': '阿拉维斯',
    'Elche': '埃尔切',
    'Real Oviedo': '奥维耶多',
    'AC Milan': 'AC米兰',
    'Inter Milan': '国际米兰',
    'Juventus': '尤文图斯',
    'Napoli': '那不勒斯',
    'Roma': '罗马',
    'Lazio': '拉齐奥',
    'Atalanta': '亚特兰大',
    'Fiorentina': '佛罗伦萨',
    'Torino': '都灵',
    'Bologna': '博洛尼亚',
    'Sassuolo': '萨索洛',
    'Udinese': '乌迪内斯',
    'Verona': '维罗纳',
    'Genoa': '热那亚',
    'Cagliari': '卡利亚里',
    'Parma': '帕尔马',
    'Lecce': '莱切',
    'Cremonese': '克雷莫纳',
    'Como': '科莫',
    'Pisa': '比萨'
}

def get_team_id(conn, team_name):
    cursor = conn.cursor()
    chinese_name = TEAM_NAME_MAP.get(team_name, team_name)
    
    cursor.execute("SELECT id FROM teams WHERE name = ?", (chinese_name,))
    result = cursor.fetchone()
    if result:
        return result[0]
    
    cursor.execute("SELECT id FROM teams WHERE shortName LIKE ?", (f'%{chinese_name[:3]}%',))
    result = cursor.fetchone()
    if result:
        return result[0]
    
    cursor.execute("SELECT id FROM teams WHERE name LIKE ?", (f'%{chinese_name[:4]}%',))
    result = cursor.fetchone()
    if result:
        return result[0]
    
    return None

def get_competition_id(conn, competition_name):
    cursor = conn.cursor()
    code = COMPETITION_MAP.get(competition_name, competition_name)
    cursor.execute("SELECT id FROM competitions WHERE code = ?", (code,))
    result = cursor.fetchone()
    if result:
        return result[0]
    cursor.execute("SELECT id FROM competitions WHERE name = ?", (competition_name,))
    result = cursor.fetchone()
    if result:
        return result[0]
    return None

def generate_match_hash(row):
    hash_str = f"{row['date']}{row['home_team']}{row['away_team']}"
    return hashlib.md5(hash_str.encode()).hexdigest()

def import_data():
    print("=" * 60)
    print("数据导入脚本 - 第一阶段：数据清洗与质量提升")
    print("=" * 60)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("\n1. 读取CSV数据...")
    df = pd.read_csv(CSV_PATH)
    print(f"   CSV数据行数: {len(df)}")
    print(f"   CSV列: {list(df.columns)}")
    
    print("\n2. 清空现有合成数据...")
    cursor.execute("DELETE FROM matches")
    conn.commit()
    print("   matches表已清空")
    
    print("\n3. 数据预处理...")
    df['date'] = pd.to_datetime(df['date'], format='%Y/%m/%d').dt.strftime('%Y-%m-%d')
    
    print("\n4. 映射球队和联赛ID...")
    team_not_found = []
    comp_not_found = []
    df['homeTeamId'] = df['home_team'].apply(lambda x: get_team_id(conn, x))
    df['awayTeamId'] = df['away_team'].apply(lambda x: get_team_id(conn, x))
    df['competitionId'] = df['competition'].apply(lambda x: get_competition_id(conn, x))
    
    for idx, row in df.iterrows():
        if row['homeTeamId'] is None:
            team_not_found.append(row['home_team'])
        if row['awayTeamId'] is None:
            team_not_found.append(row['away_team'])
        if row['competitionId'] is None:
            comp_not_found.append(row['competition'])
    
    if team_not_found:
        print(f"   ⚠️ 未找到的球队: {set(team_not_found)}")
    if comp_not_found:
        print(f"   ⚠️ 未找到的联赛: {set(comp_not_found)}")
    
    original_count = len(df)
    df = df.dropna(subset=['homeTeamId', 'awayTeamId', 'competitionId'])
    print(f"   有效数据行数: {len(df)} (过滤掉 {original_count - len(df)} 条无效数据)")
    
    print("\n5. 字段重命名与映射...")
    column_mapping = {
        'home_goals': 'homeGoals',
        'away_goals': 'awayGoals',
        'home_xg': 'homeXg',
        'away_xg': 'awayXg',
        'home_xgot': 'homeXgot',
        'away_xgot': 'awayXgot',
        'home_big_chances': 'homeBigChances',
        'away_big_chances': 'awayBigChances',
        'home_xa': 'homeXa',
        'away_xa': 'awayXa',
        'home_saves': 'homeSaves',
        'away_saves': 'awaySaves',
        'home_touches_box': 'homeTouchesBox',
        'away_touches_box': 'awayTouchesBox',
        'home_hits_post': 'homeHitsPost',
        'away_hits_post': 'awayHitsPost',
        'home_shots': 'homeShots',
        'home_shots_on_target': 'homeShotsOnTarget',
        'away_shots': 'awayShots',
        'away_shots_on_target': 'awayShotsOnTarget',
        'home_possession': 'homePossession',
        'home_corners': 'homeCorners',
        'away_corners': 'awayCorners',
        'home_fouls': 'homeFouls',
        'away_fouls': 'awayFouls',
        'home_yellow_cards': 'homeYellowCards',
        'away_yellow_cards': 'awayYellowCards'
    }
    
    df = df.rename(columns=column_mapping)
    
    print("\n6. 处理缺失字段...")
    numeric_cols = ['homeXg', 'awayXg', 'homeXgot', 'awayXgot', 'homeXa', 'awayXa',
                    'homeSaves', 'awaySaves', 'homeBigChances', 'awayBigChances',
                    'homeShots', 'homeShotsOnTarget', 'awayShots', 'awayShotsOnTarget']
    
    for col in numeric_cols:
        if col in df.columns:
            missing_count = df[col].isna().sum()
            if missing_count > 0:
                mean_val = df[col].mean()
                df[col] = df[col].fillna(mean_val)
                print(f"   {col}: 填充 {missing_count} 个缺失值，均值={mean_val:.2f}")
            else:
                print(f"   {col}: 无缺失值")
    
    print("\n7. 生成matchHash...")
    df['matchHash'] = df.apply(generate_match_hash, axis=1)
    
    now = datetime.now().isoformat()
    df['createdAt'] = now
    df['updatedAt'] = now
    
    print("\n8. 构建插入数据...")
    db_columns = [
        'date', 'homeTeamId', 'awayTeamId', 'homeGoals', 'awayGoals', 'competitionId',
        'homeXg', 'awayXg', 'homeXgot', 'awayXgot', 'homeBigChances', 'awayBigChances',
        'homeXa', 'awayXa', 'homeSaves', 'awaySaves', 'homeTouchesBox', 'awayTouchesBox',
        'homeHitsPost', 'awayHitsPost', 'homeShots', 'homeShotsOnTarget', 'awayShots',
        'awayShotsOnTarget', 'homePossession', 'homeCorners', 'awayCorners', 'homeFouls',
        'awayFouls', 'homeYellowCards', 'awayYellowCards', 'matchHash', 'createdAt', 'updatedAt'
    ]
    
    insert_cols = [c for c in db_columns if c in df.columns]
    insert_data = df[insert_cols].values.tolist()
    
    placeholders = ', '.join(['?' for _ in insert_cols])
    insert_sql = f"INSERT INTO matches ({', '.join(insert_cols)}) VALUES ({placeholders})"
    
    print("\n9. 插入数据...")
    cursor.executemany(insert_sql, insert_data)
    conn.commit()
    print(f"   成功插入 {len(insert_data)} 条数据")
    
    print("\n10. 验证数据...")
    cursor.execute("SELECT COUNT(*) FROM matches")
    total = cursor.fetchone()[0]
    print(f"   matches表总数: {total}")
    
    cursor.execute("SELECT COUNT(DISTINCT competitionId) FROM matches")
    leagues = cursor.fetchone()[0]
    print(f"   涉及联赛数: {leagues}")
    
    cursor.execute("SELECT COUNT(DISTINCT homeTeamId) FROM matches")
    teams = cursor.fetchone()[0]
    print(f"   涉及球队数: {teams}")
    
    cursor.execute("SELECT MIN(date), MAX(date) FROM matches")
    date_range = cursor.fetchone()
    print(f"   日期范围: {date_range[0]} ~ {date_range[1]}")
    
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN homeGoals > awayGoals THEN 1 ELSE 0 END) as home_win,
            SUM(CASE WHEN homeGoals < awayGoals THEN 1 ELSE 0 END) as away_win,
            SUM(CASE WHEN homeGoals = awayGoals THEN 1 ELSE 0 END) as draw
        FROM matches
    """)
    results = cursor.fetchone()
    if None in results:
        print("   比赛结果分布: 无数据")
    else:
        total_matches = results[0] + results[1] + results[2]
        print(f"\n   比赛结果分布:")
        print(f"     主胜: {results[0]} ({results[0]/total_matches*100:.2f}%)")
        print(f"     客胜: {results[1]} ({results[1]/total_matches*100:.2f}%)")
        print(f"     平局: {results[2]} ({results[2]/total_matches*100:.2f}%)")
    
    cursor.execute("SELECT AVG(homeXg), AVG(awayXg), AVG(homeXgot), AVG(awayXgot), AVG(homeXa), AVG(awayXa) FROM matches")
    xg_stats = cursor.fetchone()
    if None in xg_stats:
        print("   xG/xGot/xA统计: 无数据")
    else:
        print(f"\n   xG/xGot/xA统计:")
        print(f"     homeXg: {xg_stats[0]:.2f}")
        print(f"     awayXg: {xg_stats[1]:.2f}")
        print(f"     homeXgot: {xg_stats[2]:.2f}")
        print(f"     awayXgot: {xg_stats[3]:.2f}")
        print(f"     homeXa: {xg_stats[4]:.2f}")
        print(f"     awayXa: {xg_stats[5]:.2f}")
    
    conn.close()
    
    print("\n" + "=" * 60)
    print("数据导入完成！")
    print("=" * 60)

if __name__ == "__main__":
    import_data()