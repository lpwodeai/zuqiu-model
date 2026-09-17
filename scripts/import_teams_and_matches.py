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

def load_team_attributes():
    with open(TEAM_ATTRS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def insert_teams(conn, teams_data):
    cursor = conn.cursor()
    
    inserted = 0
    skipped = 0
    
    for team_key, team_data in teams_data.items():
        name = team_data['name']
        league = team_data['league']
        country = LEAGUE_COUNTRY_MAP.get(league, 'Unknown')
        
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO teams (name, shortName, country, league)
                VALUES (?, ?, ?, ?)
            ''', (name, name[:3], country, league))
            
            if cursor.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"跳过球队 {name}: {e}")
            skipped += 1
    
    conn.commit()
    print(f"球队导入完成: 新增 {inserted} 支, 跳过 {skipped} 支")
    return inserted, skipped

def get_team_id(cursor, team_name):
    cursor.execute('SELECT id FROM teams WHERE name = ?', (team_name,))
    result = cursor.fetchone()
    return result[0] if result else None

def get_or_create_competition(cursor, conn, league_name):
    cursor.execute('SELECT id FROM competitions WHERE name = ?', (league_name,))
    result = cursor.fetchone()
    if result:
        return result[0]
    
    cursor.execute('INSERT INTO competitions (name) VALUES (?)', (league_name,))
    conn.commit()
    return cursor.lastrowid

def generate_round_matches(teams, league_name, competition_id, cursor, start_date_str, round_num):
    matches = []
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    match_date = start_date + timedelta(days=(round_num - 1) * 7)
    date_str = match_date.strftime('%Y-%m-%d')
    
    num_teams = len(teams)
    for i in range(num_teams):
        for j in range(num_teams):
            if i >= j:
                continue
            
            home_name = teams[i]
            away_name = teams[j]
            
            home_id = get_team_id(cursor, home_name)
            away_id = get_team_id(cursor, away_name)
            
            if not home_id or not away_id:
                print(f"跳过: 找不到球队 {home_name} 或 {away_name}")
                continue
            
            home_goals = random.randint(0, 5)
            away_goals = random.randint(0, 5)
            
            home_xg = round(random.uniform(0.3, 3.0), 2)
            away_xg = round(random.uniform(0.3, 3.0), 2)
            
            home_possession = random.randint(35, 65)
            
            match_hash = f"{date_str}_{home_name}_{away_name}"
            
            matches.append({
                'date': date_str,
                'homeTeamId': home_id,
                'awayTeamId': away_id,
                'competitionId': competition_id,
                'homeGoals': home_goals,
                'awayGoals': away_goals,
                'homeXg': home_xg,
                'awayXg': away_xg,
                'homePossession': home_possession,
                'homeShots': random.randint(5, 22),
                'awayShots': random.randint(5, 22),
                'homeShotsOnTarget': random.randint(2, 10),
                'awayShotsOnTarget': random.randint(2, 10),
                'homeCorners': random.randint(0, 12),
                'awayCorners': random.randint(0, 12),
                'homeYellowCards': random.randint(0, 4),
                'awayYellowCards': random.randint(0, 4),
                'matchHash': match_hash
            })
    
    return matches

def insert_matches(conn, matches):
    cursor = conn.cursor()
    inserted = 0
    skipped = 0
    
    for match in matches:
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO matches (
                    date, homeTeamId, awayTeamId, competitionId,
                    homeGoals, awayGoals, homeXg, awayXg,
                    homePossession, homeShots, awayShots,
                    homeShotsOnTarget, awayShotsOnTarget,
                    homeCorners, awayCorners,
                    homeYellowCards, awayYellowCards,
                    matchHash, createdAt, updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                match['date'], match['homeTeamId'], match['awayTeamId'], match['competitionId'],
                match['homeGoals'], match['awayGoals'], match['homeXg'], match['awayXg'],
                match['homePossession'], match['homeShots'], match['awayShots'],
                match['homeShotsOnTarget'], match['awayShotsOnTarget'],
                match['homeCorners'], match['awayCorners'],
                match['homeYellowCards'], match['awayYellowCards'],
                match['matchHash'], datetime.now().isoformat(), datetime.now().isoformat()
            ))
            
            if cursor.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"跳过比赛 {match['matchHash']}: {e}")
            skipped += 1
    
    conn.commit()
    print(f"比赛导入完成: 新增 {inserted} 场, 跳过 {skipped} 场")
    return inserted, skipped

def main():
    print("="*60)
    print("五大联赛球队和比赛数据导入脚本")
    print("="*60)
    
    print("\n1. 加载球队属性数据...")
    teams_data = load_team_attributes()
    print(f"   共加载 {len(teams_data)} 支球队")
    
    print("\n2. 连接数据库...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("\n3. 导入球队数据...")
    insert_teams(conn, teams_data)
    
    print("\n4. 按联赛分组并生成比赛数据...")
    
    league_teams = {}
    for team_key, team_data in teams_data.items():
        league = team_data['league']
        if league not in league_teams:
            league_teams[league] = []
        league_teams[league].append(team_data['name'])
    
    total_matches_inserted = 0
    
    for league_code, teams in league_teams.items():
        league_name = LEAGUE_FULL_NAME_MAP.get(league_code, league_code)
        print(f"\n   处理 {league_name} ({len(teams)} 支球队)")
        
        competition_id = get_or_create_competition(cursor, conn, league_name)
        
        all_matches = []
        rounds_per_team = 34 if league_code in ['PL', 'SA', 'SerieA', 'FL1'] else 34
        
        for round_num in range(1, rounds_per_team + 1):
            round_matches = generate_round_matches(
                teams, league_name, competition_id, cursor, 
                '2025-08-15', round_num
            )
            all_matches.extend(round_matches)
        
        print(f"   生成 {len(all_matches)} 场比赛")
        
        inserted, _ = insert_matches(conn, all_matches)
        total_matches_inserted += inserted
    
    print("\n" + "="*60)
    print(f"总导入统计:")
    print(f"  - 球队: {len(teams_data)} 支")
    print(f"  - 比赛: {total_matches_inserted} 场")
    print("="*60)
    
    conn.close()
    print("\n数据导入完成!")

if __name__ == "__main__":
    main()