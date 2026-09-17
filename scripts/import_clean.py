import sqlite3
import json
import random
from datetime import datetime, timedelta
import os
import time

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

def load_team_attributes():
    with open(TEAM_ATTRS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def main():
    print("=== 清理旧数据库 ===")
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"已删除: {DB_PATH}")
    
    print("\n=== 加载球队数据 ===")
    teams_data = load_team_attributes()
    print(f"共加载 {len(teams_data)} 支球队")
    
    print("\n=== 创建新数据库 ===")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
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
    
    print("\n=== 插入联赛 ===")
    for code, name in LEAGUE_FULL_NAME_MAP.items():
        country = LEAGUE_COUNTRY_MAP.get(code, 'Unknown')
        cursor.execute('INSERT INTO competitions (name, code, country) VALUES (?, ?, ?)', 
                      (name, code, country))
    
    print("\n=== 插入球队 ===")
    team_name_to_id = {}
    for team_key, team_data in teams_data.items():
        name = team_data['name']
        league = team_data['league']
        country = LEAGUE_COUNTRY_MAP.get(league, 'Unknown')
        
        cursor.execute('''
            INSERT INTO teams (name, shortName, country, league, createdAt, updatedAt)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, name[:3], country, league, datetime.now().isoformat(), datetime.now().isoformat()))
        
        team_name_to_id[name] = cursor.lastrowid
    
    print(f"已插入 {len(team_name_to_id)} 支球队")
    
    print("\n=== 生成比赛数据 ===")
    league_teams = {}
    for team_key, team_data in teams_data.items():
        league = team_data['league']
        if league not in league_teams:
            league_teams[league] = []
        league_teams[league].append(team_data['name'])
    
    total_matches = 0
    
    for league_code, teams in league_teams.items():
        league_name = LEAGUE_FULL_NAME_MAP.get(league_code, league_code)
        
        cursor.execute('SELECT id FROM competitions WHERE code = ?', (league_code,))
        comp_result = cursor.fetchone()
        if not comp_result:
            print(f"跳过 {league_name}: 找不到联赛ID")
            continue
        competition_id = comp_result[0]
        
        all_matches = []
        rounds_per_team = 34
        
        for round_num in range(1, rounds_per_team + 1):
            start_date = datetime.strptime('2025-08-15', '%Y-%m-%d')
            match_date = start_date + timedelta(days=(round_num - 1) * 7)
            date_str = match_date.strftime('%Y-%m-%d')
            
            num_teams = len(teams)
            for i in range(num_teams):
                for j in range(num_teams):
                    if i >= j:
                        continue
                    
                    home_name = teams[i]
                    away_name = teams[j]
                    
                    home_id = team_name_to_id.get(home_name)
                    away_id = team_name_to_id.get(away_name)
                    
                    if not home_id or not away_id:
                        continue
                    
                    home_goals = random.randint(0, 5)
                    away_goals = random.randint(0, 5)
                    
                    home_xg = round(random.uniform(0.3, 3.0), 2)
                    away_xg = round(random.uniform(0.3, 3.0), 2)
                    
                    home_possession = random.randint(35, 65)
                    
                    match_hash = f"{date_str}_{home_name}_{away_name}"
                    
                    all_matches.append((
                        date_str, home_id, away_id, competition_id,
                        home_goals, away_goals, home_xg, away_xg,
                        home_possession, random.randint(5, 22), random.randint(5, 22),
                        random.randint(2, 10), random.randint(2, 10),
                        random.randint(0, 12), random.randint(0, 12),
                        random.randint(0, 4), random.randint(0, 4),
                        match_hash, datetime.now().isoformat(), datetime.now().isoformat()
                    ))
        
        cursor.executemany('''
            INSERT INTO matches (
                date, homeTeamId, awayTeamId, competitionId,
                homeGoals, awayGoals, homeXg, awayXg,
                homePossession, homeShots, awayShots,
                homeShotsOnTarget, awayShotsOnTarget,
                homeCorners, awayCorners,
                homeYellowCards, awayYellowCards,
                matchHash, createdAt, updatedAt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', all_matches)
        
        print(f"{league_name}: {len(all_matches)} 场比赛")
        total_matches += len(all_matches)
    
    conn.commit()
    conn.close()
    time.sleep(1)
    
    print("\n" + "="*60)
    print(f"导入完成: {total_matches} 场比赛")
    print("="*60)

if __name__ == "__main__":
    main()