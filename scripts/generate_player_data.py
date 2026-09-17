import sqlite3
import random
import json
from datetime import datetime
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'five_leagues.db')

POSITIONS = {
    'goalkeeper': ['门将', 'GK'],
    'defender': ['右后卫', 'RB', '中后卫', 'CB', '左后卫', 'LB', '中卫', 'DC'],
    'midfielder': ['右中场', 'RM', '中场', 'CM', '左中场', 'LM', '攻击中场', 'AM', '防守中场', 'DM'],
    'forward': ['右边锋', 'RW', '前锋', 'ST', '左边锋', 'LW', '中锋', 'CF']
}

NATIONALITIES = {
    'PL': ['英格兰', '苏格兰', '威尔士', '北爱尔兰', '西班牙', '法国', '德国', '巴西', '阿根廷'],
    'SA': ['西班牙', '阿根廷', '巴西', '法国', '乌拉圭', '哥伦比亚', '葡萄牙', '荷兰'],
    'BL1': ['德国', '法国', '巴西', '阿根廷', '波兰', '奥地利', '克罗地亚', '英格兰'],
    'SerieA': ['意大利', '巴西', '阿根廷', '法国', '塞尔维亚', '波兰', '荷兰', '西班牙'],
    'FL1': ['法国', '巴西', '阿根廷', '比利时', '塞内加尔', '摩洛哥', '葡萄牙', '德国']
}

PLAYER_NAMES = {
    'goalkeeper': [
        {'cn': '诺伊尔', 'en': 'Neuer'}, {'cn': '特尔施特根', 'en': 'Ter Stegen'},
        {'cn': '库尔图瓦', 'en': 'Courtois'}, {'cn': '埃德森', 'en': 'Ederson'},
        {'cn': '阿利森', 'en': 'Alisson'}, {'cn': '迈尼昂', 'en': 'Maignan'},
        {'cn': '多纳鲁马', 'en': 'Donnarumma'}, {'cn': '奥布拉克', 'en': 'Oblak'},
        {'cn': '萨维奇', 'en': 'Szczesny'}, {'cn': '拉姆斯代尔', 'en': 'Ramsdale'},
        {'cn': '马丁内斯', 'en': 'Martinez'}, {'cn': '贝尼特斯', 'en': 'Benitez'},
        {'cn': '特拉普', 'en': 'Trapp'}, {'cn': '莱诺', 'en': 'Leno'},
        {'cn': '帕特里西奥', 'en': 'Patricio'}, {'cn': '鲁利', 'en': 'Rulli'}
    ],
    'defender': [
        {'cn': '范迪克', 'en': 'Van Dijk'}, {'cn': '拉莫斯', 'en': 'Ramos'},
        {'cn': '德利赫特', 'en': 'De Ligt'}, {'cn': '阿拉巴', 'en': 'Alaba'},
        {'cn': '坎塞洛', 'en': 'Cancelo'}, {'cn': '阿诺德', 'en': 'Arnold'},
        {'cn': '罗伯逊', 'en': 'Robertson'}, {'cn': '卢卡斯', 'en': 'Lucas'},
        {'cn': '马奎尔', 'en': 'Maguire'}, {'cn': '斯通斯', 'en': 'Stones'},
        {'cn': '瓦拉内', 'en': 'Varane'}, {'cn': '乌帕梅卡诺', 'en': 'Upamecano'},
        {'cn': '金彭贝', 'en': 'Kimpembe'}, {'cn': '孔德', 'en': 'Conde'},
        {'cn': '托莫里', 'en': 'Tomori'}, {'cn': '巴斯托尼', 'en': 'Bastoni'},
        {'cn': '迪洛伦佐', 'en': 'Di Lorenzo'}, {'cn': '夸德拉多', 'en': 'Cuadrado'},
        {'cn': '戈森斯', 'en': 'Gosens'}, {'cn': '劳姆', 'en': 'Raum'}
    ],
    'midfielder': [
        {'cn': '德布劳内', 'en': 'De Bruyne'}, {'cn': '莫德里奇', 'en': 'Modric'},
        {'cn': '克罗斯', 'en': 'Kroos'}, {'cn': '坎特', 'en': 'Kante'},
        {'cn': '博格巴', 'en': 'Pogba'}, {'cn': 'B费', 'en': 'Fernandes'},
        {'cn': '亨德森', 'en': 'Henderson'}, {'cn': '蒂亚戈', 'en': 'Thiago'},
        {'cn': '基米希', 'en': 'Kimmich'}, {'cn': '穆西亚拉', 'en': 'Musiala'},
        {'cn': '贝林厄姆', 'en': 'Bellingham'}, {'cn': '楚阿梅尼', 'en': 'Tchouameni'},
        {'cn': '恩佐', 'en': 'Enzo'}, {'cn': '麦卡利斯特', 'en': 'Mac Allister'},
        {'cn': '凯西', 'en': 'Kessié'}, {'cn': '若日尼奥', 'en': 'Jorginho'},
        {'cn': '米林科维奇', 'en': 'Milinkovic-Savic'}, {'cn': '托纳利', 'en': 'Tonali'},
        {'cn': '维拉蒂', 'en': 'Verratti'}, {'cn': '贡多齐', 'en': 'Guendouzi'}
    ],
    'forward': [
        {'cn': '梅西', 'en': 'Messi'}, {'cn': 'C罗', 'en': 'Ronaldo'},
        {'cn': '哈兰德', 'en': 'Haaland'}, {'cn': '姆巴佩', 'en': 'Mbappe'},
        {'cn': '莱万', 'en': 'Lewandowski'}, {'cn': '本泽马', 'en': 'Benzema'},
        {'cn': '萨拉赫', 'en': 'Salah'}, {'cn': '马内', 'en': 'Mane'},
        {'cn': '凯恩', 'en': 'Kane'}, {'cn': '斯特林', 'en': 'Sterling'},
        {'cn': '福登', 'en': 'Foden'}, {'cn': '萨卡', 'en': 'Saka'},
        {'cn': '维尼修斯', 'en': 'Vinicius'}, {'cn': '罗德里戈', 'en': 'Rodrygo'},
        {'cn': '劳塔罗', 'en': 'Lautaro'}, {'cn': '哲科', 'en': 'Dzeko'},
        {'cn': '穆阿尼', 'en': 'Muani'}, {'cn': '登贝莱', 'en': 'Dembele'},
        {'cn': '维尔茨', 'en': 'Wirtz'}, {'cn': '阿德耶米', 'en': 'Ademi'}
    ]
}

FOOTS = ['左脚', '右脚', '双脚']

def get_random_player_name(position_group):
    names = PLAYER_NAMES[position_group]
    return random.choice(names)

def generate_player_stats(player_id, season='2025', position_group='forward'):
    matches = random.randint(15, 40)
    starts = random.randint(5, matches)
    minutes = starts * random.randint(70, 95) + (matches - starts) * random.randint(10, 30)
    
    if position_group == 'goalkeeper':
        return {
            'playerId': player_id,
            'season': season,
            'matches': matches,
            'starts': starts,
            'minutes': minutes,
            'goals': 0,
            'assists': random.randint(0, 2),
            'xg': 0,
            'xA': round(random.uniform(0, 0.5), 2),
            'shots': 0,
            'shotsOnTarget': 0,
            'bigChances': 0,
            'bigChancesCreated': 0,
            'tackles': random.randint(5, 25),
            'interceptions': random.randint(10, 35),
            'blocks': random.randint(20, 50),
            'duelsWon': random.randint(30, 80),
            'aerialWon': random.randint(40, 100),
            'dribblesCompleted': 0,
            'dribblesAttempted': 0,
            'passes': random.randint(500, 1500),
            'passesCompleted': random.randint(450, 1400),
            'keyPasses': random.randint(5, 25),
            'throughBalls': 0,
            'crosses': 0,
            'fouls': random.randint(5, 25),
            'yellowCards': random.randint(0, 5),
            'redCards': random.randint(0, 1),
            'penaltyGoals': 0,
            'penaltyMissed': 0,
            'rating': round(random.uniform(6.5, 8.5), 2)
        }
    
    elif position_group == 'defender':
        return {
            'playerId': player_id,
            'season': season,
            'matches': matches,
            'starts': starts,
            'minutes': minutes,
            'goals': random.randint(0, 8),
            'assists': random.randint(0, 6),
            'xg': round(random.uniform(0, 2.5), 2),
            'xA': round(random.uniform(0, 2), 2),
            'shots': random.randint(10, 50),
            'shotsOnTarget': random.randint(3, 20),
            'bigChances': random.randint(0, 5),
            'bigChancesCreated': random.randint(0, 8),
            'tackles': random.randint(50, 150),
            'interceptions': random.randint(40, 100),
            'blocks': random.randint(30, 80),
            'duelsWon': random.randint(80, 200),
            'aerialWon': random.randint(50, 150),
            'dribblesCompleted': random.randint(10, 50),
            'dribblesAttempted': random.randint(15, 70),
            'passes': random.randint(1500, 3500),
            'passesCompleted': random.randint(1300, 3200),
            'keyPasses': random.randint(10, 40),
            'throughBalls': random.randint(0, 10),
            'crosses': random.randint(20, 100),
            'fouls': random.randint(20, 60),
            'yellowCards': random.randint(2, 10),
            'redCards': random.randint(0, 2),
            'penaltyGoals': 0,
            'penaltyMissed': 0,
            'rating': round(random.uniform(6.5, 8.2), 2)
        }
    
    elif position_group == 'midfielder':
        return {
            'playerId': player_id,
            'season': season,
            'matches': matches,
            'starts': starts,
            'minutes': minutes,
            'goals': random.randint(2, 15),
            'assists': random.randint(3, 18),
            'xg': round(random.uniform(2, 8), 2),
            'xA': round(random.uniform(3, 10), 2),
            'shots': random.randint(30, 100),
            'shotsOnTarget': random.randint(10, 40),
            'bigChances': random.randint(2, 12),
            'bigChancesCreated': random.randint(5, 20),
            'tackles': random.randint(30, 100),
            'interceptions': random.randint(20, 60),
            'blocks': random.randint(5, 30),
            'duelsWon': random.randint(50, 150),
            'aerialWon': random.randint(20, 80),
            'dribblesCompleted': random.randint(20, 80),
            'dribblesAttempted': random.randint(30, 120),
            'passes': random.randint(2000, 4500),
            'passesCompleted': random.randint(1700, 4000),
            'keyPasses': random.randint(30, 100),
            'throughBalls': random.randint(5, 30),
            'crosses': random.randint(10, 60),
            'fouls': random.randint(15, 50),
            'yellowCards': random.randint(3, 12),
            'redCards': random.randint(0, 2),
            'penaltyGoals': random.randint(0, 3),
            'penaltyMissed': random.randint(0, 1),
            'rating': round(random.uniform(6.8, 8.8), 2)
        }
    
    else:
        return {
            'playerId': player_id,
            'season': season,
            'matches': matches,
            'starts': starts,
            'minutes': minutes,
            'goals': random.randint(5, 35),
            'assists': random.randint(3, 15),
            'xg': round(random.uniform(8, 25), 2),
            'xA': round(random.uniform(3, 10), 2),
            'shots': random.randint(60, 180),
            'shotsOnTarget': random.randint(25, 80),
            'bigChances': random.randint(8, 25),
            'bigChancesCreated': random.randint(3, 15),
            'tackles': random.randint(10, 50),
            'interceptions': random.randint(5, 25),
            'blocks': random.randint(2, 15),
            'duelsWon': random.randint(40, 120),
            'aerialWon': random.randint(20, 80),
            'dribblesCompleted': random.randint(30, 100),
            'dribblesAttempted': random.randint(40, 150),
            'passes': random.randint(500, 1500),
            'passesCompleted': random.randint(400, 1300),
            'keyPasses': random.randint(15, 50),
            'throughBalls': random.randint(2, 15),
            'crosses': random.randint(10, 50),
            'fouls': random.randint(10, 40),
            'yellowCards': random.randint(1, 8),
            'redCards': random.randint(0, 1),
            'penaltyGoals': random.randint(2, 10),
            'penaltyMissed': random.randint(0, 3),
            'rating': round(random.uniform(7.0, 9.0), 2)
        }

def generate_team_players(team_id, league):
    players = []
    nationality_list = NATIONALITIES.get(league, NATIONALITIES['PL'])
    
    pos_distribution = {
        'goalkeeper': 3,
        'defender': 8,
        'midfielder': 8,
        'forward': 6
    }
    
    jersey_numbers = list(range(1, 30))
    random.shuffle(jersey_numbers)
    jersey_idx = 0
    
    for position_group, count in pos_distribution.items():
        for i in range(count):
            name_data = get_random_player_name(position_group)
            pos_detail = random.choice(POSITIONS[position_group])
            
            player = {
                'name': name_data['cn'],
                'nameEn': name_data['en'] + f'_{team_id}_{position_group}_{i}',
                'teamId': team_id,
                'position': pos_detail,
                'positionGroup': position_group,
                'age': random.randint(18, 35),
                'height': random.randint(170, 195),
                'weight': random.randint(65, 90),
                'nationality': random.choice(nationality_list),
                'jerseyNumber': jersey_numbers[jersey_idx],
                'marketValue': round(random.uniform(0.5, 15), 2),
                'foot': random.choice(FOOTS),
                'isKeyPlayer': i == 0 and random.random() > 0.3,
                'lineupRole': 'starter' if i < count // 2 else 'backup',
                'playerStatus': 'available' if random.random() > 0.1 else random.choice(['injured', 'suspended'])
            }
            
            jersey_idx += 1
            players.append(player)
    
    return players

def main():
    print("=== 生成球员数据 ===")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT id, league FROM teams')
    teams = cursor.fetchall()
    
    total_players = 0
    total_stats = 0
    
    for team_id, league in teams:
        print(f"处理球队 {team_id} ({league})...")
        
        players = generate_team_players(team_id, league)
        
        for player in players:
            cursor.execute('''
                INSERT INTO players (
                    name, nameEn, teamId, position, positionGroup,
                    age, height, weight, nationality, jerseyNumber,
                    marketValue, foot, isKeyPlayer, lineupRole, playerStatus,
                    createdAt, updatedAt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                player['name'], player['nameEn'], player['teamId'],
                player['position'], player['positionGroup'],
                player['age'], player['height'], player['weight'],
                player['nationality'], player['jerseyNumber'],
                player['marketValue'], player['foot'],
                1 if player['isKeyPlayer'] else 0,
                player['lineupRole'], player['playerStatus'],
                datetime.now().isoformat(), datetime.now().isoformat()
            ))
            
            player_id = cursor.lastrowid
            stats = generate_player_stats(player_id, '2025', player['positionGroup'])
            
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
                stats['playerId'], stats['season'], stats['matches'], stats['starts'], stats['minutes'],
                stats['goals'], stats['assists'], stats['xg'], stats['xA'], stats['shots'], stats['shotsOnTarget'],
                stats['bigChances'], stats['bigChancesCreated'], stats['tackles'], stats['interceptions'],
                stats['blocks'], stats['duelsWon'], stats['aerialWon'], stats['dribblesCompleted'],
                stats['dribblesAttempted'], stats['passes'], stats['passesCompleted'], stats['keyPasses'],
                stats['throughBalls'], stats['crosses'], stats['fouls'], stats['yellowCards'], stats['redCards'],
                stats['penaltyGoals'], stats['penaltyMissed'], stats['rating'],
                datetime.now().isoformat(), datetime.now().isoformat()
            ))
            
            total_players += 1
            total_stats += 1
        
        conn.commit()
    
    conn.close()
    
    print(f"\n=== 完成 ===")
    print(f"生成球员: {total_players} 名")
    print(f"生成统计: {total_stats} 条")

if __name__ == "__main__":
    main()