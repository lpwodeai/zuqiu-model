import sqlite3
import random
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"

BUNDESLIGA_TEAMS = [
    '拜仁慕尼黑', '多特蒙德', '勒沃库森', '莱比锡红牛', '沃尔夫斯堡',
    '门兴格拉德巴赫', '法兰克福', '柏林联合', '弗赖堡', '科隆',
    '奥格斯堡', '霍芬海姆', '美因茨', '波鸿', '斯图加特',
    '柏林赫塔', '比勒菲尔德', '杜塞尔多夫'
]

LIGUE_1_TEAMS = [
    '巴黎圣日耳曼', '马赛', '里昂', '摩纳哥', '里尔',
    '尼斯', '雷恩', '斯特拉斯堡', '兰斯', '波尔多',
    '蒙彼利埃', '南特', '布雷斯特', '图卢兹', '昂热',
    '梅斯', '第戎', '洛里昂', '尼姆', '亚眠'
]

def generate_matches(teams, league_name, start_date_str, matches_per_team=34):
    matches = []
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    num_teams = len(teams)
    total_matches = num_teams * matches_per_team // 2
    
    match_dates = []
    current_date = start_date
    while len(match_dates) < 38:
        if current_date.weekday() in [5, 6]:
            match_dates.append(current_date.strftime('%Y-%m-%d'))
        current_date += timedelta(days=1)
    
    used_pairs = set()
    match_id = 1
    
    for i in range(num_teams):
        for j in range(num_teams):
            if i == j:
                continue
            pair = tuple(sorted([i, j]))
            if pair in used_pairs:
                continue
            used_pairs.add(pair)
            
            round_num = random.randint(1, 34)
            date_str = match_dates[min(round_num - 1, len(match_dates) - 1)]
            
            home_goals = random.randint(0, 6)
            away_goals = random.randint(0, 6)
            
            home_xg = round(random.uniform(0.5, 3.5), 2)
            away_xg = round(random.uniform(0.5, 3.5), 2)
            
            home_possession = random.randint(35, 65)
            
            matches.append({
                'id': f'{league_name[:3].lower()}_{match_id}',
                'date': date_str,
                'home_team_name': teams[i],
                'away_team_name': teams[j],
                'competition_name': league_name,
                'homeGoals': home_goals,
                'awayGoals': away_goals,
                'homeXg': home_xg,
                'awayXg': away_xg,
                'homePossession': home_possession,
                'homeShots': random.randint(5, 25),
                'awayShots': random.randint(5, 25),
                'homeShotsOnTarget': random.randint(2, 12),
                'awayShotsOnTarget': random.randint(2, 12),
                'homeCorners': random.randint(0, 15),
                'awayCorners': random.randint(0, 15),
                'homeYellowCards': random.randint(0, 5),
                'awayYellowCards': random.randint(0, 5),
                'round': round_num
            })
            
            match_id += 1
    
    return matches

def insert_matches(conn, matches):
    cursor = conn.cursor()
    
    cursor.execute("SELECT id FROM competitions WHERE name = ?", ('Bundesliga',))
    bundesliga_id = cursor.fetchone()
    if not bundesliga_id:
        cursor.execute("INSERT INTO competitions (name) VALUES (?)", ('Bundesliga',))
        bundesliga_id = cursor.lastrowid
    else:
        bundesliga_id = bundesliga_id[0]
    
    cursor.execute("SELECT id FROM competitions WHERE name = ?", ('Ligue 1',))
    ligue1_id = cursor.fetchone()
    if not ligue1_id:
        cursor.execute("INSERT INTO competitions (name) VALUES (?)", ('Ligue 1',))
        ligue1_id = cursor.lastrowid
    else:
        ligue1_id = ligue1_id[0]
    
    team_map = {}
    cursor.execute("SELECT id, name FROM teams")
    for row in cursor.fetchall():
        team_map[row[1]] = row[0]
    
    for match in matches:
        league_id = bundesliga_id if match['competition_name'] == 'Bundesliga' else ligue1_id
        
        home_team_id = team_map.get(match['home_team_name'])
        away_team_id = team_map.get(match['away_team_name'])
        
        if not home_team_id or not away_team_id:
            print(f"跳过: 找不到球队 {match['home_team_name']} 或 {match['away_team_name']}")
            continue
        
        try:
            cursor.execute('''
                INSERT INTO matches (
                    date, homeTeamId, awayTeamId, competitionId,
                    homeGoals, awayGoals, homeXg, awayXg,
                    homePossession, homeShots, awayShots,
                    homeShotsOnTarget, awayShotsOnTarget,
                    homeCorners, awayCorners,
                    homeYellowCards, awayYellowCards,
                    createdAt, updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                match['date'], home_team_id, away_team_id, league_id,
                match['homeGoals'], match['awayGoals'], match['homeXg'], match['awayXg'],
                match['homePossession'], match['homeShots'], match['awayShots'],
                match['homeShotsOnTarget'], match['awayShotsOnTarget'],
                match['homeCorners'], match['awayCorners'],
                match['homeYellowCards'], match['awayYellowCards'],
                datetime.now().isoformat(), datetime.now().isoformat()
            ))
        except sqlite3.IntegrityError:
            pass
    
    conn.commit()
    print(f"成功插入 {cursor.rowcount} 场比赛")

def main():
    print("生成德甲和法甲比赛数据...")
    
    bundesliga_matches = generate_matches(BUNDESLIGA_TEAMS, 'Bundesliga', '2025-08-15')
    ligue1_matches = generate_matches(LIGUE_1_TEAMS, 'Ligue 1', '2025-08-15')
    
    print(f"德甲: {len(bundesliga_matches)} 场")
    print(f"法甲: {len(ligue1_matches)} 场")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        insert_matches(conn, bundesliga_matches + ligue1_matches)
        conn.close()
        print("数据插入完成!")
    except Exception as e:
        print(f"错误: {e}")

if __name__ == "__main__":
    main()