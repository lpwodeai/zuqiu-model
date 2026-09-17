import sqlite3
import json
import random
from datetime import datetime, timedelta
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'five_leagues.db')
TEAM_ATTRS_PATH = os.path.join(BASE_DIR, 'assets', 'team_attributes.json')

LEAGUE_COUNTRY_MAP = {
    'PL': 'England',
    'SA': 'Spain',
    'BL1': 'Germany',
    'SerieA': 'Italy',
    'FL1': 'France'
}

LEAGUE_FULL_NAME_MAP = {
    'PL': 'Premier League',
    'SA': 'La Liga',
    'BL1': 'Bundesliga',
    'SerieA': 'Serie A',
    'FL1': 'Ligue 1'
}

POSITIONS = {
    'goalkeeper': ['门将', 'GK'],
    'defender': ['右后卫', 'RB', '中后卫', 'CB', '左后卫', 'LB'],
    'midfielder': ['右中场', 'RM', '中场', 'CM', '左中场', 'LM', '攻击中场', 'AM'],
    'forward': ['右边锋', 'RW', '前锋', 'ST', '左边锋', 'LW']
}

NATIONALITIES = {
    'PL': ['英格兰', '苏格兰', '威尔士', '巴西', '阿根廷'],
    'SA': ['西班牙', '阿根廷', '巴西', '法国', '乌拉圭'],
    'BL1': ['德国', '法国', '巴西', '阿根廷', '波兰'],
    'SerieA': ['意大利', '巴西', '阿根廷', '法国', '塞尔维亚'],
    'FL1': ['法国', '巴西', '阿根廷', '比利时', '塞内加尔']
}

PLAYER_NAMES = {
    'goalkeeper': [{'cn': '诺伊尔', 'en': 'Neuer'}, {'cn': '特尔施特根', 'en': 'Ter Stegen'},
                   {'cn': '库尔图瓦', 'en': 'Courtois'}, {'cn': '埃德森', 'en': 'Ederson'},
                   {'cn': '阿利森', 'en': 'Alisson'}, {'cn': '迈尼昂', 'en': 'Maignan'}],
    'defender': [{'cn': '范迪克', 'en': 'Van Dijk'}, {'cn': '拉莫斯', 'en': 'Ramos'},
                 {'cn': '德利赫特', 'en': 'De Ligt'}, {'cn': '坎塞洛', 'en': 'Cancelo'},
                 {'cn': '阿诺德', 'en': 'Arnold'}, {'cn': '罗伯逊', 'en': 'Robertson'}],
    'midfielder': [{'cn': '德布劳内', 'en': 'De Bruyne'}, {'cn': '莫德里奇', 'en': 'Modric'},
                   {'cn': '克罗斯', 'en': 'Kroos'}, {'cn': '坎特', 'en': 'Kante'},
                   {'cn': '基米希', 'en': 'Kimmich'}, {'cn': '贝林厄姆', 'en': 'Bellingham'}],
    'forward': [{'cn': '哈兰德', 'en': 'Haaland'}, {'cn': '姆巴佩', 'en': 'Mbappe'},
                {'cn': '莱万', 'en': 'Lewandowski'}, {'cn': '萨拉赫', 'en': 'Salah'},
                {'cn': '凯恩', 'en': 'Kane'}, {'cn': '维尼修斯', 'en': 'Vinicius'}]
}

FOOTS = ['左脚', '右脚', '双脚']

def load_team_attributes():
    with open(TEAM_ATTRS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def recreate_database(conn):
    cursor = conn.cursor()
    cursor.execute('DROP TABLE IF EXISTS player_stats')
    cursor.execute('DROP TABLE IF EXISTS players')
    cursor.execute('DROP TABLE IF EXISTS matches')
    cursor.execute('DROP TABLE IF EXISTS teams')
    cursor.execute('DROP TABLE IF EXISTS competitions')
    
    cursor.execute('''
        CREATE TABLE competitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            code TEXT,
            country TEXT,
            createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
            updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            shortName TEXT,
            country TEXT,
            league TEXT,
            createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
            updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            homeTeamId INTEGER,
            awayTeamId INTEGER,
            homeGoals INTEGER DEFAULT 0,
            awayGoals INTEGER DEFAULT 0,
            competitionId INTEGER,
            homeXg REAL,
            awayXg REAL,
            homeXgot REAL,
            awayXgot REAL,
            homeBigChances INTEGER DEFAULT 0,
            awayBigChances INTEGER DEFAULT 0,
            homeXa REAL,
            awayXa REAL,
            homeSaves INTEGER DEFAULT 0,
            awaySaves INTEGER DEFAULT 0,
            homeTouchesBox INTEGER DEFAULT 0,
            awayTouchesBox INTEGER DEFAULT 0,
            homeHitsPost INTEGER DEFAULT 0,
            awayHitsPost INTEGER DEFAULT 0,
            homeShots INTEGER DEFAULT 0,
            homeShotsOnTarget INTEGER DEFAULT 0,
            awayShots INTEGER DEFAULT 0,
            awayShotsOnTarget INTEGER DEFAULT 0,
            homePossession INTEGER,
            homeCorners INTEGER DEFAULT 0,
            awayCorners INTEGER DEFAULT 0,
            homeFouls INTEGER DEFAULT 0,
            awayFouls INTEGER DEFAULT 0,
            homeYellowCards INTEGER DEFAULT 0,
            awayYellowCards INTEGER DEFAULT 0,
            matchHash TEXT UNIQUE,
            createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
            updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            nameEn TEXT,
            teamId INTEGER,
            position TEXT NOT NULL,
            positionGroup TEXT,
            age INTEGER,
            height REAL,
            weight REAL,
            nationality TEXT,
            jerseyNumber INTEGER,
            marketValue REAL,
            foot TEXT,
            isKeyPlayer BOOLEAN DEFAULT 0,
            lineupRole TEXT DEFAULT 'backup',
            playerStatus TEXT DEFAULT 'available',
            createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
            updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playerId INTEGER NOT NULL,
            season TEXT NOT NULL,
            matches INTEGER DEFAULT 0,
            starts INTEGER DEFAULT 0,
            minutes INTEGER DEFAULT 0,
            goals INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            xg REAL DEFAULT 0,
            xA REAL DEFAULT 0,
            shots INTEGER DEFAULT 0,
            shotsOnTarget INTEGER DEFAULT 0,
            bigChances INTEGER DEFAULT 0,
            bigChancesCreated INTEGER DEFAULT 0,
            tackles INTEGER DEFAULT 0,
            interceptions INTEGER DEFAULT 0,
            blocks INTEGER DEFAULT 0,
            duelsWon INTEGER DEFAULT 0,
            aerialWon INTEGER DEFAULT 0,
            dribblesCompleted INTEGER DEFAULT 0,
            dribblesAttempted INTEGER DEFAULT 0,
            passes INTEGER DEFAULT 0,
            passesCompleted INTEGER DEFAULT 0,
            keyPasses INTEGER DEFAULT 0,
            throughBalls INTEGER DEFAULT 0,
            crosses INTEGER DEFAULT 0,
            fouls INTEGER DEFAULT 0,
            yellowCards INTEGER DEFAULT 0,
            redCards INTEGER DEFAULT 0,
            penaltyGoals INTEGER DEFAULT 0,
            penaltyMissed INTEGER DEFAULT 0,
            rating REAL DEFAULT 0,
            createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
            updatedAt TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (playerId, season)
        )
    ''')
    
    conn.commit()
    print("数据库表已重建")

def insert_competitions(conn):
    cursor = conn.cursor()
    for code, name in LEAGUE_FULL_NAME_MAP.items():
        country = LEAGUE_COUNTRY_MAP.get(code, 'Unknown')
        cursor.execute('INSERT INTO competitions (name, code, country) VALUES (?, ?, ?)', (name, code, country))
    conn.commit()
    print(f"已插入 {len(LEAGUE_FULL_NAME_MAP)} 个联赛")

def insert_teams(conn, teams_data):
    cursor = conn.cursor()
    team_name_to_id = {}
    for team_key, team_data in teams_data.items():
        name = team_data['name']
        league = team_data['league']
        country = LEAGUE_COUNTRY_MAP.get(league, 'Unknown')
        cursor.execute('INSERT INTO teams (name, shortName, country, league) VALUES (?, ?, ?, ?)', 
                      (name, name[:3], country, league))
        team_name_to_id[name] = cursor.lastrowid
    conn.commit()
    print(f"已插入 {len(team_name_to_id)} 支球队")
    return team_name_to_id

def insert_matches(conn, teams_data, team_name_to_id):
    cursor = conn.cursor()
    league_teams = {}
    for team_key, team_data in teams_data.items():
        league = team_data['league']
        if league not in league_teams:
            league_teams[league] = []
        league_teams[league].append(team_data['name'])
    
    total_matches = 0
    for league_code, teams in league_teams.items():
        cursor.execute('SELECT id FROM competitions WHERE code = ?', (league_code,))
        comp_result = cursor.fetchone()
        if not comp_result:
            continue
        competition_id = comp_result[0]
        
        rounds_per_team = 34
        for round_num in range(1, rounds_per_team + 1):
            match_date = datetime(2025, 8, 15) + timedelta(days=(round_num - 1) * 7)
            date_str = match_date.strftime('%Y-%m-%d')
            
            num_teams = len(teams)
            for i in range(num_teams):
                for j in range(num_teams):
                    if i >= j:
                        continue
                    home_id = team_name_to_id.get(teams[i])
                    away_id = team_name_to_id.get(teams[j])
                    if not home_id or not away_id:
                        continue
                    
                    match_hash = f"{date_str}_{teams[i]}_{teams[j]}"
                    cursor.execute('''
                        INSERT INTO matches (date, homeTeamId, awayTeamId, competitionId,
                            homeGoals, awayGoals, homeXg, awayXg, homePossession,
                            homeShots, awayShots, homeShotsOnTarget, awayShotsOnTarget,
                            homeCorners, awayCorners, homeYellowCards, awayYellowCards, matchHash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (date_str, home_id, away_id, competition_id,
                          random.randint(0, 5), random.randint(0, 5),
                          round(random.uniform(0.3, 3.0), 2), round(random.uniform(0.3, 3.0), 2),
                          random.randint(35, 65), random.randint(5, 22), random.randint(5, 22),
                          random.randint(2, 10), random.randint(2, 10),
                          random.randint(0, 12), random.randint(0, 12),
                          random.randint(0, 4), random.randint(0, 4), match_hash))
                    total_matches += 1
    
    conn.commit()
    print(f"已插入 {total_matches} 场比赛")

def insert_players(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT id, league FROM teams')
    teams = cursor.fetchall()
    
    total_players = 0
    for team_id, league in teams:
        nationality_list = NATIONALITIES.get(league, NATIONALITIES['PL'])
        
        pos_distribution = {'goalkeeper': 3, 'defender': 6, 'midfielder': 6, 'forward': 5}
        jersey_numbers = list(range(1, 25))
        random.shuffle(jersey_numbers)
        jersey_idx = 0
        
        for position_group, count in pos_distribution.items():
            for i in range(count):
                name_data = random.choice(PLAYER_NAMES[position_group])
                pos_detail = random.choice(POSITIONS[position_group])
                
                cursor.execute('''
                    INSERT INTO players (name, nameEn, teamId, position, positionGroup,
                        age, height, weight, nationality, jerseyNumber,
                        marketValue, foot, isKeyPlayer, lineupRole, playerStatus)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (name_data['cn'], f"{name_data['en']}_{team_id}_{i}", team_id,
                      pos_detail, position_group, random.randint(18, 35),
                      random.randint(170, 195), random.randint(65, 90),
                      random.choice(nationality_list), jersey_numbers[jersey_idx],
                      round(random.uniform(0.5, 10), 2), random.choice(FOOTS),
                      1 if i == 0 and random.random() > 0.3 else 0,
                      'starter' if i < count // 2 else 'backup',
                      'available' if random.random() > 0.1 else 'injured'))
                jersey_idx += 1
                total_players += 1
    
    conn.commit()
    print(f"已插入 {total_players} 名球员")

def insert_player_stats(conn):
    cursor = conn.cursor()
    cursor.execute('SELECT id, positionGroup FROM players')
    players = cursor.fetchall()
    
    total_stats = 0
    for player_id, position_group in players:
        matches = random.randint(15, 35)
        starts = random.randint(5, matches)
        minutes = starts * random.randint(70, 95) + (matches - starts) * random.randint(10, 30)
        
        if position_group == 'goalkeeper':
            goals, assists, xg, shots = 0, random.randint(0, 2), 0, 0
            tackles, interceptions, blocks = random.randint(5, 25), random.randint(10, 35), random.randint(20, 50)
            passes = random.randint(500, 1500)
        elif position_group == 'defender':
            goals, assists, xg, shots = random.randint(0, 5), random.randint(0, 4), round(random.uniform(0, 1.5), 2), random.randint(10, 35)
            tackles, interceptions, blocks = random.randint(40, 120), random.randint(30, 80), random.randint(20, 60)
            passes = random.randint(1000, 2500)
        elif position_group == 'midfielder':
            goals, assists, xg, shots = random.randint(3, 12), random.randint(5, 15), round(random.uniform(3, 8), 2), random.randint(30, 80)
            tackles, interceptions, blocks = random.randint(20, 80), random.randint(15, 50), random.randint(5, 25)
            passes = random.randint(1500, 3500)
        else:
            goals, assists, xg, shots = random.randint(8, 30), random.randint(3, 12), round(random.uniform(10, 22), 2), random.randint(50, 150)
            tackles, interceptions, blocks = random.randint(5, 30), random.randint(3, 20), random.randint(2, 10)
            passes = random.randint(300, 1000)
        
        shots_on_target = random.randint(0, shots) if shots > 0 else 0
        cursor.execute('''
            INSERT INTO player_stats (playerId, season, matches, starts, minutes,
                goals, assists, xg, xA, shots, shotsOnTarget,
                tackles, interceptions, blocks, passes, passesCompleted,
                keyPasses, dribblesCompleted, dribblesAttempted, aerialWon,
                fouls, yellowCards, redCards, rating)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (player_id, '2025', matches, starts, minutes,
              goals, assists, xg, round(random.uniform(0, 5), 2), shots, shots_on_target,
              tackles, interceptions, blocks, passes, random.randint(passes - 200, passes),
              random.randint(10, 50), random.randint(10, 50), random.randint(15, 70), random.randint(20, 80),
              random.randint(5, 35), random.randint(1, 8), random.randint(0, 1), round(random.uniform(6.5, 8.8), 2)))
        total_stats += 1
    
    conn.commit()
    print(f"已插入 {total_stats} 条球员统计")

def main():
    print("=== 清理旧数据库 ===")
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    
    print("\n=== 创建新数据库 ===")
    conn = sqlite3.connect(DB_PATH)
    recreate_database(conn)
    
    print("\n=== 插入联赛 ===")
    insert_competitions(conn)
    
    print("\n=== 加载并插入球队 ===")
    teams_data = load_team_attributes()
    team_name_to_id = insert_teams(conn, teams_data)
    
    print("\n=== 插入比赛 ===")
    insert_matches(conn, teams_data, team_name_to_id)
    
    print("\n=== 插入球员 ===")
    insert_players(conn)
    
    print("\n=== 插入球员统计 ===")
    insert_player_stats(conn)
    
    conn.close()
    
    print("\n=== 验证数据 ===")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM teams')
    teams_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM matches')
    matches_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM players')
    players_count = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM player_stats')
    stats_count = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"\n数据导入完成:")
    print(f"- 球队: {teams_count} 支")
    print(f"- 比赛: {matches_count} 场")
    print(f"- 球员: {players_count} 名")
    print(f"- 统计: {stats_count} 条")

if __name__ == "__main__":
    main()