import sqlite3
import pandas as pd
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'five_leagues.db')
PLAYERS_CSV_PATH = os.path.join(BASE_DIR, 'data', 'players_template.csv')
STATS_CSV_PATH = os.path.join(BASE_DIR, 'data', 'player_stats_template.csv')

def load_team_mapping():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, name FROM teams')
    teams = cursor.fetchall()
    
    conn.close()
    
    mapping = {row[1]: row[0] for row in teams}
    return mapping

def validate_csv(file_path, required_columns):
    if not os.path.exists(file_path):
        print(f"错误：文件不存在: {file_path}")
        return None
    
    df = pd.read_csv(file_path)
    
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        print(f"错误：缺少必填列: {missing_cols}")
        return None
    
    print(f"读取成功，行数: {len(df)}")
    return df

def import_players(df, team_mapping):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    inserted = 0
    skipped = 0
    errors = []
    
    for _, row in df.iterrows():
        team_name = row['teamName']
        
        if team_name not in team_mapping:
            errors.append(f"球队 '{team_name}' 不存在于数据库中，跳过球员 '{row['name']}'")
            skipped += 1
            continue
        
        team_id = team_mapping[team_name]
        
        try:
            cursor.execute('''
                INSERT INTO players (
                    name, nameEn, teamId, position, positionGroup,
                    age, height, weight, nationality, jerseyNumber,
                    marketValue, foot, isKeyPlayer, lineupRole, playerStatus,
                    injuryStatus, injuryReturnDate, createdAt, updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                row['name'],
                row.get('nameEn', ''),
                team_id,
                row['position'],
                row['positionGroup'],
                int(row['age']) if pd.notna(row['age']) else None,
                float(row['height']) if pd.notna(row['height']) else None,
                float(row['weight']) if pd.notna(row['weight']) else None,
                row.get('nationality', ''),
                int(row['jerseyNumber']) if pd.notna(row['jerseyNumber']) else None,
                float(row['marketValue']),
                row.get('foot', ''),
                int(row['isKeyPlayer']) if pd.notna(row['isKeyPlayer']) else 0,
                row['lineupRole'],
                row['playerStatus'],
                row.get('injuryStatus', ''),
                row.get('injuryReturnDate', ''),
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            
            inserted += 1
        except Exception as e:
            errors.append(f"导入球员 '{row['name']}' 失败: {str(e)}")
            skipped += 1
    
    conn.commit()
    conn.close()
    
    return inserted, skipped, errors

def import_player_stats(df, team_mapping):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    inserted = 0
    skipped = 0
    errors = []
    
    cursor.execute('SELECT id, name, teamId FROM players')
    player_rows = cursor.fetchall()
    
    player_map = {}
    for pid, name, tid in player_rows:
        if name not in player_map:
            player_map[name] = {}
        player_map[name][tid] = pid
    
    for _, row in df.iterrows():
        player_name = row['name']
        team_name = row['teamName']
        
        if team_name not in team_mapping:
            errors.append(f"球队 '{team_name}' 不存在于数据库中，跳过统计 '{player_name}'")
            skipped += 1
            continue
        
        team_id = team_mapping[team_name]
        
        if player_name not in player_map or team_id not in player_map[player_name]:
            errors.append(f"球员 '{player_name}' 在球队 '{team_name}' 中不存在，跳过")
            skipped += 1
            continue
        
        player_id = player_map[player_name][team_id]
        
        try:
            cursor.execute('''
                INSERT INTO player_stats (
                    playerId, season, matches, starts, minutes,
                    goals, assists, xg, xA, shots, shotsOnTarget,
                    bigChances, bigChancesCreated, tackles, interceptions,
                    blocks, duelsWon, aerialWon, dribblesCompleted,
                    dribblesAttempted, passes, passesCompleted, keyPasses,
                    throughBalls, crosses, fouls, yellowCards, redCards,
                    penaltyGoals, penaltyMissed, rating,
                    createdAt, updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                player_id,
                row['season'],
                int(row['matches']) if pd.notna(row['matches']) else 0,
                int(row['starts']) if pd.notna(row['starts']) else 0,
                int(row['minutes']) if pd.notna(row['minutes']) else 0,
                int(row['goals']) if pd.notna(row['goals']) else 0,
                int(row['assists']) if pd.notna(row['assists']) else 0,
                float(row['xg']) if pd.notna(row['xg']) else 0.0,
                float(row['xa']) if pd.notna(row['xa']) else 0.0,
                int(row['shots']) if pd.notna(row['shots']) else 0,
                int(row['shotsOnTarget']) if pd.notna(row['shotsOnTarget']) else 0,
                int(row['bigChances']) if pd.notna(row['bigChances']) else 0,
                int(row['bigChancesCreated']) if pd.notna(row['bigChancesCreated']) else 0,
                int(row['tackles']) if pd.notna(row['tackles']) else 0,
                int(row['interceptions']) if pd.notna(row['interceptions']) else 0,
                int(row['blocks']) if pd.notna(row['blocks']) else 0,
                int(row['duelsWon']) if pd.notna(row['duelsWon']) else 0,
                int(row['aerialWon']) if pd.notna(row['aerialWon']) else 0,
                int(row['dribblesCompleted']) if pd.notna(row['dribblesCompleted']) else 0,
                int(row['dribblesAttempted']) if pd.notna(row['dribblesAttempted']) else 0,
                int(row['passes']) if pd.notna(row['passes']) else 0,
                int(row['passesCompleted']) if pd.notna(row['passesCompleted']) else 0,
                int(row['keyPasses']) if pd.notna(row['keyPasses']) else 0,
                int(row['throughBalls']) if pd.notna(row['throughBalls']) else 0,
                int(row['crosses']) if pd.notna(row['crosses']) else 0,
                int(row['fouls']) if pd.notna(row['fouls']) else 0,
                int(row['yellowCards']) if pd.notna(row['yellowCards']) else 0,
                int(row['redCards']) if pd.notna(row['redCards']) else 0,
                int(row['penaltyGoals']) if pd.notna(row['penaltyGoals']) else 0,
                int(row['penaltyMissed']) if pd.notna(row['penaltyMissed']) else 0,
                float(row['rating']) if pd.notna(row['rating']) else 0.0,
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            
            inserted += 1
        except Exception as e:
            errors.append(f"导入统计 '{player_name}' 失败: {str(e)}")
            skipped += 1
    
    conn.commit()
    conn.close()
    
    return inserted, skipped, errors

def main():
    print("=== 导入球员数据 ===")
    
    print("\n1. 加载球队映射...")
    team_mapping = load_team_mapping()
    print(f"   数据库中球队数量: {len(team_mapping)}")
    print(f"   球队列表: {list(team_mapping.keys())[:5]}...")
    
    print("\n2. 验证并导入球员基础信息...")
    players_required = ['name', 'teamName', 'position', 'positionGroup', 'age', 'marketValue', 'isKeyPlayer', 'lineupRole', 'playerStatus']
    players_df = validate_csv(PLAYERS_CSV_PATH, players_required)
    
    if players_df is not None:
        inserted, skipped, errors = import_players(players_df, team_mapping)
        print(f"   成功: {inserted}, 跳过: {skipped}")
        if errors:
            print("\n   错误详情:")
            for err in errors[:5]:
                print(f"     - {err}")
            if len(errors) > 5:
                print(f"     ... 还有 {len(errors) - 5} 个错误")
    
    print("\n3. 验证并导入球员赛季统计...")
    stats_required = ['name', 'teamName', 'season', 'matches', 'starts', 'minutes', 'goals', 'assists', 'xg', 'xa', 'rating']
    stats_df = validate_csv(STATS_CSV_PATH, stats_required)
    
    if stats_df is not None:
        inserted, skipped, errors = import_player_stats(stats_df, team_mapping)
        print(f"   成功: {inserted}, 跳过: {skipped}")
        if errors:
            print("\n   错误详情:")
            for err in errors[:5]:
                print(f"     - {err}")
            if len(errors) > 5:
                print(f"     ... 还有 {len(errors) - 5} 个错误")
    
    print("\n=== 导入完成 ===")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM players')
    player_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM player_stats')
    stats_count = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"最终players表记录数: {player_count}")
    print(f"最终player_stats表记录数: {stats_count}")

if __name__ == "__main__":
    main()