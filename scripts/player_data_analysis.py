import sqlite3
import pandas as pd
import numpy as np
import os
import sys
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"
OUTPUT_DIR = BASE_DIR / "output"

sys.path.insert(0, str(BASE_DIR))
from db_utils import connect, write_dataframe  # noqa: E402

os.makedirs(OUTPUT_DIR, exist_ok=True)

def convert_numpy_types(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_numpy_types(item) for item in obj)
    else:
        return obj

def load_player_data():
    conn = sqlite3.connect(DB_PATH)
    
    players = pd.read_sql('SELECT * FROM players', conn)
    stats = pd.read_sql('SELECT * FROM player_stats', conn)
    
    conn.close()
    
    return players, stats

def generate_feature_catalog(players, stats):
    catalog = {}
    
    catalog['players'] = {
        'table': 'players',
        'description': '球员基础信息表',
        'total_records': len(players),
        'features': []
    }
    
    players_features = [
        {'name': 'id', 'type': 'INTEGER', 'unit': '-', 'range': (players['id'].min(), players['id'].max()), 'description': '球员唯一标识'},
        {'name': 'name', 'type': 'TEXT', 'unit': '-', 'range': '字符串', 'description': '球员中文名'},
        {'name': 'nameEn', 'type': 'TEXT', 'unit': '-', 'range': '字符串', 'description': '球员英文名'},
        {'name': 'teamId', 'type': 'INTEGER', 'unit': '-', 'range': (players['teamId'].min(), players['teamId'].max()), 'description': '所属球队ID'},
        {'name': 'position', 'type': 'TEXT', 'unit': '-', 'range': players['position'].unique().tolist(), 'description': '具体位置(如ST, GK, CB等)'},
        {'name': 'positionGroup', 'type': 'TEXT', 'unit': '-', 'range': players['positionGroup'].unique().tolist(), 'description': '位置分组(goalkeeper/defender/midfielder/forward)'},
        {'name': 'age', 'type': 'INTEGER', 'unit': '岁', 'range': (players['age'].min(), players['age'].max()), 'description': '年龄'},
        {'name': 'height', 'type': 'REAL', 'unit': 'cm', 'range': (players['height'].min(), players['height'].max()), 'description': '身高'},
        {'name': 'weight', 'type': 'REAL', 'unit': 'kg', 'range': (players['weight'].min(), players['weight'].max()), 'description': '体重'},
        {'name': 'nationality', 'type': 'TEXT', 'unit': '-', 'range': players['nationality'].unique().tolist(), 'description': '国籍'},
        {'name': 'jerseyNumber', 'type': 'INTEGER', 'unit': '-', 'range': (players['jerseyNumber'].min(), players['jerseyNumber'].max()), 'description': '球衣号码'},
        {'name': 'marketValue', 'type': 'REAL', 'unit': '亿欧元', 'range': (players['marketValue'].min(), players['marketValue'].max()), 'description': '市场价值'},
        {'name': 'foot', 'type': 'TEXT', 'unit': '-', 'range': players['foot'].unique().tolist(), 'description': '惯用脚(左脚/右脚/双脚)'},
        {'name': 'isKeyPlayer', 'type': 'BOOLEAN', 'unit': '-', 'range': [0, 1], 'description': '是否核心球员'},
        {'name': 'lineupRole', 'type': 'TEXT', 'unit': '-', 'range': players['lineupRole'].unique().tolist(), 'description': '阵容角色(starter/backup/rotation)'},
        {'name': 'playerStatus', 'type': 'TEXT', 'unit': '-', 'range': players['playerStatus'].unique().tolist(), 'description': '当前状态(available/injured/suspended/doubtful)'},
        {'name': 'createdAt', 'type': 'TEXT', 'unit': '-', 'range': '日期时间', 'description': '创建时间'},
        {'name': 'updatedAt', 'type': 'TEXT', 'unit': '-', 'range': '日期时间', 'description': '更新时间'},
    ]
    
    catalog['players']['features'] = players_features
    
    catalog['player_stats'] = {
        'table': 'player_stats',
        'description': '球员赛季统计表',
        'total_records': len(stats),
        'features': []
    }
    
    stats_features = [
        {'name': 'id', 'type': 'INTEGER', 'unit': '-', 'range': (stats['id'].min(), stats['id'].max()), 'description': '统计记录唯一标识'},
        {'name': 'playerId', 'type': 'INTEGER', 'unit': '-', 'range': (stats['playerId'].min(), stats['playerId'].max()), 'description': '关联球员ID'},
        {'name': 'season', 'type': 'TEXT', 'unit': '-', 'range': stats['season'].unique().tolist(), 'description': '赛季'},
        {'name': 'matches', 'type': 'INTEGER', 'unit': '场', 'range': (stats['matches'].min(), stats['matches'].max()), 'description': '出场次数'},
        {'name': 'starts', 'type': 'INTEGER', 'unit': '场', 'range': (stats['starts'].min(), stats['starts'].max()), 'description': '首发次数'},
        {'name': 'minutes', 'type': 'INTEGER', 'unit': '分钟', 'range': (stats['minutes'].min(), stats['minutes'].max()), 'description': '出场时间'},
        {'name': 'goals', 'type': 'INTEGER', 'unit': '个', 'range': (stats['goals'].min(), stats['goals'].max()), 'description': '进球数'},
        {'name': 'assists', 'type': 'INTEGER', 'unit': '次', 'range': (stats['assists'].min(), stats['assists'].max()), 'description': '助攻数'},
        {'name': 'xg', 'type': 'REAL', 'unit': '-', 'range': (stats['xg'].min(), stats['xg'].max()), 'description': '预期进球'},
        {'name': 'xA', 'type': 'REAL', 'unit': '-', 'range': (stats['xA'].min(), stats['xA'].max()), 'description': '预期助攻'},
        {'name': 'shots', 'type': 'INTEGER', 'unit': '次', 'range': (stats['shots'].min(), stats['shots'].max()), 'description': '射门次数'},
        {'name': 'shotsOnTarget', 'type': 'INTEGER', 'unit': '次', 'range': (stats['shotsOnTarget'].min(), stats['shotsOnTarget'].max()), 'description': '射正次数'},
        {'name': 'bigChances', 'type': 'INTEGER', 'unit': '次', 'range': (stats['bigChances'].min(), stats['bigChances'].max()), 'description': '大机会次数'},
        {'name': 'bigChancesCreated', 'type': 'INTEGER', 'unit': '次', 'range': (stats['bigChancesCreated'].min(), stats['bigChancesCreated'].max()), 'description': '创造大机会次数'},
        {'name': 'tackles', 'type': 'INTEGER', 'unit': '次', 'range': (stats['tackles'].min(), stats['tackles'].max()), 'description': '抢断次数'},
        {'name': 'interceptions', 'type': 'INTEGER', 'unit': '次', 'range': (stats['interceptions'].min(), stats['interceptions'].max()), 'description': '拦截次数'},
        {'name': 'blocks', 'type': 'INTEGER', 'unit': '次', 'range': (stats['blocks'].min(), stats['blocks'].max()), 'description': '封堵次数'},
        {'name': 'duelsWon', 'type': 'INTEGER', 'unit': '次', 'range': (stats['duelsWon'].min(), stats['duelsWon'].max()), 'description': '赢得对抗次数'},
        {'name': 'aerialWon', 'type': 'INTEGER', 'unit': '次', 'range': (stats['aerialWon'].min(), stats['aerialWon'].max()), 'description': '赢得空中对抗次数'},
        {'name': 'dribblesCompleted', 'type': 'INTEGER', 'unit': '次', 'range': (stats['dribblesCompleted'].min(), stats['dribblesCompleted'].max()), 'description': '成功过人次数'},
        {'name': 'dribblesAttempted', 'type': 'INTEGER', 'unit': '次', 'range': (stats['dribblesAttempted'].min(), stats['dribblesAttempted'].max()), 'description': '尝试过人次数'},
        {'name': 'passes', 'type': 'INTEGER', 'unit': '次', 'range': (stats['passes'].min(), stats['passes'].max()), 'description': '传球次数'},
        {'name': 'passesCompleted', 'type': 'INTEGER', 'unit': '次', 'range': (stats['passesCompleted'].min(), stats['passesCompleted'].max()), 'description': '成功传球次数'},
        {'name': 'keyPasses', 'type': 'INTEGER', 'unit': '次', 'range': (stats['keyPasses'].min(), stats['keyPasses'].max()), 'description': '关键传球次数'},
        {'name': 'throughBalls', 'type': 'INTEGER', 'unit': '次', 'range': (stats['throughBalls'].min(), stats['throughBalls'].max()), 'description': '直塞球次数'},
        {'name': 'crosses', 'type': 'INTEGER', 'unit': '次', 'range': (stats['crosses'].min(), stats['crosses'].max()), 'description': '传中次数'},
        {'name': 'fouls', 'type': 'INTEGER', 'unit': '次', 'range': (stats['fouls'].min(), stats['fouls'].max()), 'description': '犯规次数'},
        {'name': 'yellowCards', 'type': 'INTEGER', 'unit': '张', 'range': (stats['yellowCards'].min(), stats['yellowCards'].max()), 'description': '黄牌数'},
        {'name': 'redCards', 'type': 'INTEGER', 'unit': '张', 'range': (stats['redCards'].min(), stats['redCards'].max()), 'description': '红牌数'},
        {'name': 'penaltyGoals', 'type': 'INTEGER', 'unit': '个', 'range': (stats['penaltyGoals'].min(), stats['penaltyGoals'].max()), 'description': '点球进球数'},
        {'name': 'penaltyMissed', 'type': 'INTEGER', 'unit': '个', 'range': (stats['penaltyMissed'].min(), stats['penaltyMissed'].max()), 'description': '点球罚失数'},
        {'name': 'rating', 'type': 'REAL', 'unit': '-', 'range': (stats['rating'].min(), stats['rating'].max()), 'description': '平均评分(1-10)'},
        {'name': 'createdAt', 'type': 'TEXT', 'unit': '-', 'range': '日期时间', 'description': '创建时间'},
        {'name': 'updatedAt', 'type': 'TEXT', 'unit': '-', 'range': '日期时间', 'description': '更新时间'},
    ]
    
    catalog['player_stats']['features'] = stats_features
    
    return catalog

def detect_duplicate_records(players, stats):
    duplicates = {}
    
    players_copy = players.copy()
    players_copy['name_normalized'] = players_copy['name'].str.strip().str.lower()
    
    duplicate_groups = players_copy.groupby(['name_normalized', 'teamId']).filter(lambda x: len(x) > 1)
    
    if not duplicate_groups.empty:
        duplicates['players_exact'] = []
        for (name_norm, team_id), group in duplicate_groups.groupby(['name_normalized', 'teamId']):
            original_names = group['name'].unique()
            if len(original_names) > 1:
                duplicates['players_exact'].append({
                    'normalized_name': name_norm,
                    'teamId': int(team_id),
                    'original_names': list(original_names),
                    'count': len(group),
                    'ids': group['id'].tolist(),
                    'details': group[['id', 'name', 'position', 'positionGroup', 'age', 'marketValue']].to_dict('records')
                })
    
    name_position_mismatch = []
    position_mapping = {
        'gk': 'goalkeeper', '门将': 'goalkeeper', '守门员': 'goalkeeper',
        'cb': 'defender', '中后卫': 'defender', '中卫': 'defender',
        'rb': 'defender', '右后卫': 'defender', '右卫': 'defender',
        'lb': 'defender', '左后卫': 'defender', '左卫': 'defender',
        'rdm': 'defender', 'ldm': 'defender', 'dm': 'defender', '后腰': 'defender',
        'cm': 'midfielder', '中场': 'midfielder', '中前卫': 'midfielder',
        'am': 'midfielder', '前腰': 'midfielder', '攻击型中场': 'midfielder',
        'rm': 'midfielder', '右中场': 'midfielder',
        'lm': 'midfielder', '左中场': 'midfielder',
        'st': 'forward', '前锋': 'forward', '中锋': 'forward',
        'cf': 'forward', '影锋': 'forward',
        'rw': 'forward', '右边锋': 'forward',
        'lw': 'forward', '左边锋': 'forward',
        'fw': 'forward', '边锋': 'forward',
    }
    
    for _, row in players.iterrows():
        pos_lower = str(row['position']).lower().strip()
        group_lower = str(row['positionGroup']).lower().strip()
        
        expected_group = position_mapping.get(pos_lower)
        
        if expected_group and expected_group != group_lower:
            name_position_mismatch.append({
                'id': row['id'],
                'name': row['name'],
                'position': row['position'],
                'positionGroup': row['positionGroup'],
                'expected_positionGroup': expected_group,
                'teamId': row['teamId']
            })
    
    duplicates['players_position_mismatch'] = name_position_mismatch
    
    return duplicates

def detect_identical_stats(stats):
    stats_no_id = stats.drop(['id', 'createdAt', 'updatedAt'], axis=1)
    
    duplicated_stats = stats_no_id[stats_no_id.duplicated(keep=False)]
    
    identical_groups = []
    if not duplicated_stats.empty:
        for _, group in duplicated_stats.groupby(list(stats_no_id.columns)):
            if len(group) > 1:
                identical_groups.append({
                    'playerIds': group['playerId'].tolist(),
                    'count': len(group),
                    'stats': group.iloc[0].to_dict()
                })
    
    return identical_groups

def generate_cleaning_report(before, after, duplicates_removed, fixes_applied):
    report = {
        'timestamp': datetime.now().isoformat(),
        'cleaning_summary': {
            'players_before': before['players'],
            'players_after': after['players'],
            'players_removed': before['players'] - after['players'],
            'stats_before': before['stats'],
            'stats_after': after['stats'],
            'stats_removed': before['stats'] - after['stats'],
            'duplicates_removed': duplicates_removed,
            'fixes_applied': fixes_applied
        },
        'rules': {
            'rule1': {
                'name': '完全重复记录清理',
                'description': '删除姓名和球队ID完全相同的重复记录',
                'action': '保留最早创建的记录，删除其余重复记录',
                'count': duplicates_removed.get('exact_duplicates', 0)
            },
            'rule2': {
                'name': '位置映射修正',
                'description': '修复position与positionGroup不一致的记录',
                'action': '根据标准位置映射表修正positionGroup字段',
                'count': fixes_applied.get('position_fixes', 0)
            },
            'rule3': {
                'name': '格式规范化',
                'description': '统一位置字段的格式和命名',
                'action': '将中文位置名称转换为标准英文缩写',
                'count': fixes_applied.get('format_fixes', 0)
            },
            'rule4': {
                'name': '冗余统计清理',
                'description': '删除完全相同的球员统计记录',
                'action': '保留playerId最小的记录，删除其余',
                'count': duplicates_removed.get('stats_duplicates', 0)
            }
        }
    }
    
    return report

def clean_player_data(players, stats):
    players_clean = players.copy()
    stats_clean = stats.copy()
    
    position_mapping = {
        'gk': 'goalkeeper', '门将': 'goalkeeper', '守门员': 'goalkeeper',
        'cb': 'defender', '中后卫': 'defender', '中卫': 'defender',
        'rb': 'defender', '右后卫': 'defender', '右卫': 'defender',
        'lb': 'defender', '左后卫': 'defender', '左卫': 'defender',
        'rdm': 'defender', 'ldm': 'defender', 'dm': 'defender', '后腰': 'defender',
        'cm': 'midfielder', '中场': 'midfielder', '中前卫': 'midfielder',
        'am': 'midfielder', '前腰': 'midfielder', '攻击型中场': 'midfielder',
        'rm': 'midfielder', '右中场': 'midfielder',
        'lm': 'midfielder', '左中场': 'midfielder',
        'st': 'forward', '前锋': 'forward', '中锋': 'forward',
        'cf': 'forward', '影锋': 'forward',
        'rw': 'forward', '右边锋': 'forward',
        'lw': 'forward', '左边锋': 'forward',
        'fw': 'forward', '边锋': 'forward',
    }
    
    standard_position_map = {
        '门将': 'GK', '守门员': 'GK',
        '中后卫': 'CB', '中卫': 'CB',
        '右后卫': 'RB', '右卫': 'RB',
        '左后卫': 'LB', '左卫': 'LB',
        '后腰': 'DM',
        '中场': 'CM', '中前卫': 'CM',
        '前腰': 'AM', '攻击型中场': 'AM',
        '右中场': 'RM',
        '左中场': 'LM',
        '前锋': 'ST', '中锋': 'ST',
        '影锋': 'CF',
        '右边锋': 'RW',
        '左边锋': 'LW',
        '边锋': 'RW',
    }
    
    fixes_applied = {
        'position_fixes': 0,
        'format_fixes': 0
    }
    
    for i, row in players_clean.iterrows():
        pos = str(row['position']).strip()
        pos_lower = pos.lower()
        
        if pos in standard_position_map:
            players_clean.at[i, 'position'] = standard_position_map[pos]
            fixes_applied['format_fixes'] += 1
        
        expected_group = position_mapping.get(pos_lower)
        if expected_group and str(row['positionGroup']) != expected_group:
            players_clean.at[i, 'positionGroup'] = expected_group
            fixes_applied['position_fixes'] += 1
    
    players_clean['name_normalized'] = players_clean['name'].str.strip().str.lower()
    
    duplicates_mask = players_clean.duplicated(subset=['name_normalized', 'teamId'], keep='first')
    duplicates_removed = {'exact_duplicates': int(duplicates_mask.sum())}
    
    players_clean = players_clean[~duplicates_mask]
    players_clean = players_clean.drop('name_normalized', axis=1)
    
    valid_player_ids = set(players_clean['id'])
    stats_clean = stats_clean[stats_clean['playerId'].isin(valid_player_ids)]
    
    stats_no_id = stats_clean.drop(['id', 'createdAt', 'updatedAt'], axis=1)
    stats_duplicates_mask = stats_no_id.duplicated(subset=['playerId', 'season'], keep='first')
    duplicates_removed['stats_duplicates'] = int(stats_duplicates_mask.sum())
    
    stats_clean = stats_clean[~stats_duplicates_mask]
    
    return players_clean, stats_clean, duplicates_removed, fixes_applied

def save_results(catalog, duplicates, report):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    catalog_converted = convert_numpy_types(catalog)
    duplicates_converted = convert_numpy_types(duplicates)
    report_converted = convert_numpy_types(report)
    
    with open(os.path.join(OUTPUT_DIR, f'player_feature_catalog_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump(catalog_converted, f, ensure_ascii=False, indent=2)
    print(f"\n特征目录已保存: player_feature_catalog_{timestamp}.json")
    
    with open(os.path.join(OUTPUT_DIR, f'duplicate_detection_report_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump(duplicates_converted, f, ensure_ascii=False, indent=2)
    print(f"雷同数据检测报告已保存: duplicate_detection_report_{timestamp}.json")
    
    with open(os.path.join(OUTPUT_DIR, f'data_cleaning_report_{timestamp}.json'), 'w', encoding='utf-8') as f:
        json.dump(report_converted, f, ensure_ascii=False, indent=2)
    print(f"数据清理报告已保存: data_cleaning_report_{timestamp}.json")
    
    return timestamp

def apply_cleaning_to_db(players_clean, stats_clean):
    conn = connect(db_path=DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM player_stats')
    cursor.execute('DELETE FROM players')
    
    write_dataframe(conn, players_clean, 'players')
    write_dataframe(conn, stats_clean, 'player_stats')
    
    conn.commit()
    conn.close()
    
    print(f"\n数据库已更新:")
    print(f"  players表: {len(players_clean)} 条记录")
    print(f"  player_stats表: {len(stats_clean)} 条记录")

def main():
    print("=" * 80)
    print("球员数据特征分析与雷同数据清理")
    print("=" * 80)
    
    print("\n1. 加载球员数据...")
    players, stats = load_player_data()
    print(f"   players表: {len(players)} 条记录")
    print(f"   player_stats表: {len(stats)} 条记录")
    
    print("\n2. 生成特征目录...")
    catalog = generate_feature_catalog(players, stats)
    print(f"   players表特征数: {len(catalog['players']['features'])}")
    print(f"   player_stats表特征数: {len(catalog['player_stats']['features'])}")
    
    print("\n3. 检测雷同数据...")
    duplicates = detect_duplicate_records(players, stats)
    print(f"   完全重复球员记录组数: {len(duplicates.get('players_exact', []))}")
    print(f"   位置映射不一致记录数: {len(duplicates.get('players_position_mismatch', []))}")
    
    identical_stats = detect_identical_stats(stats)
    print(f"   完全相同统计记录组数: {len(identical_stats)}")
    
    print("\n4. 执行数据清理...")
    before_counts = {'players': len(players), 'stats': len(stats)}
    players_clean, stats_clean, duplicates_removed, fixes_applied = clean_player_data(players, stats)
    after_counts = {'players': len(players_clean), 'stats': len(stats_clean)}
    
    print(f"   清理前 - players: {before_counts['players']}, stats: {before_counts['stats']}")
    print(f"   清理后 - players: {after_counts['players']}, stats: {after_counts['stats']}")
    print(f"   删除完全重复记录: {duplicates_removed.get('exact_duplicates', 0)}")
    print(f"   删除重复统计记录: {duplicates_removed.get('stats_duplicates', 0)}")
    print(f"   修正位置映射: {fixes_applied.get('position_fixes', 0)}")
    print(f"   规范化位置格式: {fixes_applied.get('format_fixes', 0)}")
    
    print("\n5. 生成清理报告...")
    report = generate_cleaning_report(before_counts, after_counts, duplicates_removed, fixes_applied)
    
    timestamp = save_results(catalog, duplicates, report)
    
    print("\n6. 应用清理到数据库...")
    apply_cleaning_to_db(players_clean, stats_clean)
    
    print("\n" + "=" * 80)
    print("分析与清理完成!")
    print("=" * 80)
    print(f"\n输出文件目录: {OUTPUT_DIR}")
    print(f"时间戳: {timestamp}")
    
    return catalog, duplicates, report

if __name__ == "__main__":
    main()
