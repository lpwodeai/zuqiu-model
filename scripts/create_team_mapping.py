"""创建球队名称映射表，解决中英文名称匹配问题"""
import sqlite3
import os

# 英超球队中英文名称映射
PREMIER_LEAGUE_TEAMS = {
    'Arsenal': '阿森纳',
    'Aston Villa': '阿斯顿维拉',
    'Bournemouth': '伯恩茅斯',
    'Brentford': '布伦特福德',
    'Brighton & Hove Albion': '布莱顿',
    'Burnley': '伯恩利',
    'Chelsea': '切尔西',
    'Crystal Palace': '水晶宫',
    'Everton': '埃弗顿',
    'Fulham': '富勒姆',
    'Leeds United': '利兹联',
    'Liverpool': '利物浦',
    'Manchester City': '曼城',
    'Manchester United': '曼联',
    'Newcastle United': '纽卡斯尔',
    'Nottingham Forest': '诺丁汉森林',
    'Sunderland': '桑德兰',
    'Tottenham Hotspur': '热刺',
    'West Ham United': '西汉姆',
    'Wolverhampton Wanderers': '狼队',
    # 中文变体
    '纽卡斯尔联': '纽卡斯尔',
    '西汉姆联': '西汉姆',
}

def create_team_mapping_table(db_path):
    """在数据库中创建球队名称映射表"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS team_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT UNIQUE,
            standard_name TEXT,
            league TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

def populate_team_mapping(db_path):
    """填充球队名称映射表"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    for original_name, standard_name in PREMIER_LEAGUE_TEAMS.items():
        cursor.execute("""
            INSERT OR IGNORE INTO team_mapping 
            (original_name, standard_name, league)
            VALUES (?, ?, 'PL')
        """, (original_name, standard_name))
    
    conn.commit()
    conn.close()

def get_standard_name(db_path, team_name):
    """获取球队的标准名称"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 首先检查是否有直接映射
    cursor.execute("""
        SELECT standard_name FROM team_mapping 
        WHERE original_name = ?
    """, (team_name,))
    result = cursor.fetchone()
    if result:
        conn.close()
        return result[0]
    
    # 如果没有直接映射，检查是否已经是标准名称
    cursor.execute("""
        SELECT standard_name FROM team_mapping 
        WHERE standard_name = ?
    """, (team_name,))
    result = cursor.fetchone()
    if result:
        conn.close()
        return result[0]
    
    conn.close()
    return team_name

def update_match_mapping_with_team_mapping(five_leagues_path, odds_path):
    """使用球队名称映射更新比赛映射"""
    conn_odds = sqlite3.connect(odds_path)
    cursor_odds = conn_odds.cursor()
    
    # 获取所有未匹配的比赛
    cursor_odds.execute("""
        SELECT m.match_id, m.home_team, m.away_team, m.match_date, m.match_type
        FROM matches m
        LEFT JOIN match_mapping mm ON m.match_id = mm.odds_match_id
        WHERE mm.odds_match_id IS NULL AND m.match_type = 'Premier League'
    """)
    unmatched_matches = cursor_odds.fetchall()
    
    # 获取five_leagues.db中的球队映射
    conn_fl = sqlite3.connect(five_leagues_path)
    cursor_fl = conn_fl.cursor()
    cursor_fl.execute("SELECT id, name, shortName FROM teams WHERE league = 'PL'")
    teams = cursor_fl.fetchall()
    
    name_to_id = {}
    for team_id, name, short_name in teams:
        name_to_id[name] = team_id
        if short_name:
            name_to_id[short_name] = team_id
    
    total = len(unmatched_matches)
    newly_matched = 0
    
    for match_id, home_team, away_team, match_date, match_type in unmatched_matches:
        # 获取标准名称
        std_home = get_standard_name(odds_path, home_team)
        std_away = get_standard_name(odds_path, away_team)
        
        # 查找匹配的球队ID
        home_team_id = name_to_id.get(std_home)
        away_team_id = name_to_id.get(std_away)
        
        if home_team_id and away_team_id:
            # 尝试查找匹配的比赛
            cursor_fl.execute("""
                SELECT id FROM matches 
                WHERE homeTeamId = ? AND awayTeamId = ? AND date = ?
            """, (home_team_id, away_team_id, match_date.replace('/', '-')))
            
            result = cursor_fl.fetchone()
            if result:
                cursor_odds.execute("""
                    INSERT OR IGNORE INTO match_mapping 
                    (odds_match_id, five_leagues_match_id, home_team, away_team, match_date, league)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (match_id, result[0], home_team, away_team, match_date, match_type))
                newly_matched += 1
    
    conn_odds.commit()
    conn_fl.close()
    conn_odds.close()
    
    print(f"使用球队名称映射后：")
    print(f"  未匹配比赛数：{total}")
    print(f"  新匹配成功：{newly_matched}")
    
    return newly_matched

def verify_final_mapping(odds_path):
    """验证最终映射结果"""
    conn = sqlite3.connect(odds_path)
    cursor = conn.cursor()
    
    # 统计映射情况
    cursor.execute("SELECT COUNT(*) FROM matches WHERE match_type = 'Premier League'")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM match_mapping")
    mapped_count = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT m.match_id, m.home_team, m.away_team, mm.five_leagues_match_id
        FROM matches m
        LEFT JOIN match_mapping mm ON m.match_id = mm.odds_match_id
        WHERE mm.odds_match_id IS NULL AND m.match_type = 'Premier League'
    """)
    still_unmatched = cursor.fetchall()
    
    conn.close()
    
    print(f"\n=== 最终映射结果 ===")
    print(f"英超比赛总数：{total_matches}")
    print(f"成功映射：{mapped_count}")
    print(f"映射率：{mapped_count/total_matches*100:.2f}%")
    print(f"仍未匹配：{len(still_unmatched)}")
    
    if still_unmatched:
        print("\n仍未匹配的比赛：")
        for item in still_unmatched[:10]:
            print(f"  {item[0]}: {item[1]} vs {item[2]}")
        if len(still_unmatched) > 10:
            print(f"  ... 还有 {len(still_unmatched)-10} 条未显示")

if __name__ == '__main__':
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(base_dir)
    
    five_leagues_path = os.path.join(project_dir, 'data', 'five_leagues.db')
    odds_path = os.path.join(project_dir, 'data', 'odds.db')
    
    print("=== 创建球队名称映射 ===")
    
    # 创建球队映射表
    create_team_mapping_table(odds_path)
    populate_team_mapping(odds_path)
    print("球队名称映射表创建完成")
    
    # 使用球队映射更新比赛映射
    newly_matched = update_match_mapping_with_team_mapping(five_leagues_path, odds_path)
    
    # 验证最终结果
    verify_final_mapping(odds_path)
    
    print("\n=== 球队名称映射完成 ===")