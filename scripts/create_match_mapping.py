"""创建两个数据库的match_id映射"""
import sqlite3
import os
import re
from datetime import datetime, timedelta

def get_team_name_mapping(five_leagues_path):
    """获取球队名称映射"""
    conn = sqlite3.connect(five_leagues_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, shortName FROM teams")
    teams = cursor.fetchall()
    conn.close()
    
    # 创建双向映射
    id_to_name = {}
    name_to_id = {}
    
    for team_id, name, short_name in teams:
        id_to_name[team_id] = name
        name_to_id[name] = team_id
        if short_name and short_name != name:
            name_to_id[short_name] = team_id
    
    return id_to_name, name_to_id

def normalize_date(date_str):
    """标准化日期格式"""
    # 处理格式 'YYYY-MM-DD' 或 'YYYY/MM/DD'
    date_str = date_str.replace('/', '-')
    # 处理单数字的月份和日期
    parts = date_str.split('-')
    if len(parts) == 3:
        year = parts[0].zfill(4)
        month = parts[1].zfill(2)
        day = parts[2].zfill(2)
        return f"{year}-{month}-{day}"
    return date_str

def find_matching_match(five_leagues_path, home_team, away_team, match_date, name_to_id):
    """在five_leagues.db中查找匹配的比赛"""
    conn = sqlite3.connect(five_leagues_path)
    cursor = conn.cursor()
    
    # 获取球队ID
    home_team_id = name_to_id.get(home_team)
    away_team_id = name_to_id.get(away_team)
    
    if not home_team_id or not away_team_id:
        conn.close()
        return None
    
    # 标准化日期
    normalized_date = normalize_date(match_date)
    
    # 尝试精确匹配
    cursor.execute("""
        SELECT id, date, homeTeamId, awayTeamId 
        FROM matches 
        WHERE homeTeamId = ? AND awayTeamId = ? AND date = ?
    """, (home_team_id, away_team_id, normalized_date))
    
    result = cursor.fetchone()
    if result:
        conn.close()
        return result[0]  # 返回匹配的match_id
    
    # 尝试邻近日期匹配（±1天）
    try:
        base_date = datetime.strptime(normalized_date, '%Y-%m-%d')
        for delta in [-1, 1]:
            nearby_date = (base_date + timedelta(days=delta)).strftime('%Y-%m-%d')
            cursor.execute("""
                SELECT id, date, homeTeamId, awayTeamId 
                FROM matches 
                WHERE homeTeamId = ? AND awayTeamId = ? AND date = ?
            """, (home_team_id, away_team_id, nearby_date))
            result = cursor.fetchone()
            if result:
                conn.close()
                return result[0]
    except:
        pass
    
    conn.close()
    return None

def create_mapping_table(odds_path):
    """在odds.db中创建映射表"""
    conn = sqlite3.connect(odds_path)
    cursor = conn.cursor()
    
    # 创建映射表（如果不存在）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS match_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odds_match_id TEXT UNIQUE,
            five_leagues_match_id INTEGER,
            home_team TEXT,
            away_team TEXT,
            match_date TEXT,
            league TEXT,
            mapped_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

def populate_mapping(five_leagues_path, odds_path):
    """填充映射表"""
    # 获取球队名称映射
    id_to_name, name_to_id = get_team_name_mapping(five_leagues_path)
    
    # 创建映射表
    create_mapping_table(odds_path)
    
    # 获取odds.db中的所有比赛
    conn_odds = sqlite3.connect(odds_path)
    cursor_odds = conn_odds.cursor()
    cursor_odds.execute("""
        SELECT match_id, home_team, away_team, match_date, match_type 
        FROM matches 
        WHERE match_type = 'Premier League'
    """)
    odds_matches = cursor_odds.fetchall()
    
    total = len(odds_matches)
    matched = 0
    unmatched = []
    
    for match_id, home_team, away_team, match_date, match_type in odds_matches:
        five_leagues_id = find_matching_match(five_leagues_path, home_team, away_team, match_date, name_to_id)
        
        if five_leagues_id:
            # 插入映射记录
            cursor_odds.execute("""
                INSERT OR IGNORE INTO match_mapping 
                (odds_match_id, five_leagues_match_id, home_team, away_team, match_date, league)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (match_id, five_leagues_id, home_team, away_team, match_date, match_type))
            matched += 1
        else:
            unmatched.append((match_id, home_team, away_team, match_date))
    
    conn_odds.commit()
    conn_odds.close()
    
    print(f"匹配结果：")
    print(f"  总比赛数：{total}")
    print(f"  成功匹配：{matched}")
    print(f"  匹配率：{matched/total*100:.2f}%")
    print(f"  未匹配：{len(unmatched)}")
    
    if unmatched:
        print("\n未匹配的比赛：")
        for item in unmatched[:10]:
            print(f"  {item}")
        if len(unmatched) > 10:
            print(f"  ... 还有 {len(unmatched)-10} 条未显示")
    
    return matched, len(unmatched)

def verify_mapping(odds_path):
    """验证映射结果"""
    conn = sqlite3.connect(odds_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM match_mapping")
    count = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT mm.odds_match_id, mm.five_leagues_match_id, 
               m.home_team, m.away_team, m.match_date
        FROM match_mapping mm
        JOIN matches m ON mm.odds_match_id = m.match_id
        LIMIT 5
    """)
    samples = cursor.fetchall()
    
    conn.close()
    
    print(f"\n映射表验证：")
    print(f"  映射记录数：{count}")
    print(f"  示例数据：")
    for sample in samples:
        print(f"    odds_id: {sample[0]}, five_leagues_id: {sample[1]}")
        print(f"    {sample[2]} vs {sample[3]} on {sample[4]}")

if __name__ == '__main__':
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(base_dir)
    
    five_leagues_path = os.path.join(project_dir, 'data', 'five_leagues.db')
    odds_path = os.path.join(project_dir, 'data', 'odds.db')
    
    print("=== 创建match_id映射 ===")
    print(f"five_leagues.db: {five_leagues_path}")
    print(f"odds.db: {odds_path}")
    
    matched, unmatched = populate_mapping(five_leagues_path, odds_path)
    verify_mapping(odds_path)
    
    print("\n=== 映射完成 ===")