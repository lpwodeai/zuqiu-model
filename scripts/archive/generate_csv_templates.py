import sqlite3
import csv
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"
OUTPUT_DIR = BASE_DIR / "data" / "csv_templates"

TEAM_ABBREVIATIONS = {
    '阿森纳': 'ARS', '阿斯顿维拉': 'AVL', '布莱顿': 'BHA', '伯恩利': 'BUR',
    '切尔西': 'CHE', '水晶宫': 'CRY', '埃弗顿': 'EVE', '富勒姆': 'FUL',
    '利物浦': 'LIV', '曼城': 'MCI', '曼联': 'MUN', '纽卡斯尔': 'NEW',
    '诺丁汉森林': 'NFO', '谢菲尔德联': 'SHU', '南安普顿': 'SOU', '热刺': 'TOT',
    '西汉姆': 'WHU', '狼队': 'WOL', '伯恩茅斯': 'BOU', '莱斯特城': 'LEI',
    '利兹联': 'LEE', '沃特福德': 'WAT', '布伦特福德': 'BRE',
    '巴塞罗那': 'BAR', '皇家马德里': 'RMA', '马德里竞技': 'ATM', '瓦伦西亚': 'VAL',
    '塞维利亚': 'SEV', '比利亚雷亚尔': 'VIL', '毕尔巴鄂': 'BIL', '皇家社会': 'RSO',
    '贝蒂斯': 'BET', '赫塔费': 'GET', '西班牙人': 'ESP', '奥萨苏纳': 'OSA',
    '马洛卡': 'MLL', '格拉纳达': 'GRA', '巴列卡诺': 'RAY', '阿尔梅里亚': 'ALM',
    '加的斯': 'CAD', '埃尔切': 'ELC', '塞尔塔': 'CEL', '莱万特': 'LEV',
    '拜仁慕尼黑': 'BAY', '多特蒙德': 'DOR', '勒沃库森': 'LEV', '沃尔夫斯堡': 'WOB',
    '法兰克福': 'FRA', '斯图加特': 'STU', '霍芬海姆': 'TSG', '波鸿': 'BOC',
    '弗赖堡': 'FRE', '科隆': 'KOE', '美因茨': 'MAI', '奥格斯堡': 'AUG',
    '柏林赫塔': 'HEL', '柏林联合': 'UNB', '门兴格拉德巴赫': 'MGL', '莱比锡': 'RBL',
    '尤文图斯': 'JUV', 'AC米兰': 'MIL', '国际米兰': 'INT', '罗马': 'ROM',
    '那不勒斯': 'NAP', '拉齐奥': 'LAZ', '佛罗伦萨': 'FLO', '亚特兰大': 'ATL',
    '热那亚': 'GEN', '博洛尼亚': 'BOV', '都灵': 'TOR', '乌迪内斯': 'UDI',
    '桑普多利亚': 'SAM', '维罗纳': 'VER', '萨索洛': 'SAS',
    '巴黎圣日耳曼': 'PSG', '马赛': 'MAR', '里昂': 'LYO', '摩纳哥': 'MON',
    '雷恩': 'REN', '尼斯': 'NIC', '里尔': 'LIL', '兰斯': 'REI',
    '斯特拉斯堡': 'STR', '图卢兹': 'TOU', '南特': 'NAN'
}

EXACT_SCORES = [
    '1:0', '2:0', '2:1', '3:0', '3:1', '3:2', '4:0', '4:1', '4:2', '5:0', '5:1', '5:2',
    '0:0', '1:1', '2:2', '3:3', '4:4',
    '0:1', '0:2', '1:2', '0:3', '1:3', '2:3', '0:4', '1:4', '2:4', '0:5', '1:5', '2:5',
    '胜其它', '平其它', '负其它'
]

def get_team_abbr(team_name):
    return TEAM_ABBREVIATIONS.get(team_name, team_name[:3].upper())

def generate_match_id(date_str, home_team, away_team):
    date = date_str.replace('-', '')
    home_abbr = get_team_abbr(home_team)
    away_abbr = get_team_abbr(away_team)
    return f"{date}_{home_abbr}_vs_{away_abbr}"

def load_matches():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT m.date, ht.name as home_team_name, at.name as away_team_name, 
               m.homeGoals, m.awayGoals, c.name as competition_name
        FROM matches m
        JOIN teams ht ON m.homeTeamId = ht.id
        JOIN teams at ON m.awayTeamId = at.id
        JOIN competitions c ON m.competitionId = c.id
        WHERE m.homeGoals IS NOT NULL AND m.awayGoals IS NOT NULL
        ORDER BY m.date, c.name
    """)
    
    matches = []
    for row in cursor.fetchall():
        date, home_team, away_team, home_goals, away_goals, competition = row
        
        if home_goals > away_goals:
            result = '胜'
        elif home_goals < away_goals:
            result = '负'
        else:
            result = '平'
        
        exact_score = f"{home_goals}:{away_goals}"
        total_goals = home_goals + away_goals
        
        handicap_val = -1
        if home_goals - handicap_val > away_goals:
            handicap_result = f"({handicap_val})胜"
        elif home_goals - handicap_val < away_goals:
            handicap_result = f"({handicap_val})负"
        else:
            handicap_result = f"({handicap_val})平"
        
        matches.append({
            'match_id': generate_match_id(date, home_team, away_team),
            'date': date.replace('-', '/'),
            'league': competition,
            'home_team': home_team,
            'away_team': away_team,
            'result': result,
            'handicap_result': handicap_result,
            'exact_score': exact_score,
            'total_goals': total_goals
        })
    
    conn.close()
    return matches

def write_matches_main(matches):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, 'matches_main.csv')
    
    headers = [
        'match_id', 'event_date', 'league_name', 'home_team_name', 'away_team_name',
        'result', 'handicap_result', 'exact_score', 'total_goals'
    ]
    
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for match in matches:
            writer.writerow({
                'match_id': match['match_id'],
                'event_date': match['date'],
                'league_name': match['league'],
                'home_team_name': match['home_team'],
                'away_team_name': match['away_team'],
                'result': match['result'],
                'handicap_result': match['handicap_result'],
                'exact_score': match['exact_score'],
                'total_goals': match['total_goals']
            })
    
    print(f"Created matches_main.csv with {len(matches)} matches")

def write_odds_wdl(matches):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, 'odds_wdl.csv')
    
    headers = ['match_id', 'release_time', 'home_win_odds', 'draw_odds', 'away_win_odds']
    
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for match in matches:
            for i in range(3):
                if i == 0:
                    time_offset = '-03-00 10:00:00'
                elif i == 1:
                    time_offset = '-01-00 18:00:00'
                else:
                    time_offset = '-00-00 14:00:00'
                
                date_part = match['date'].replace('/', '-')
                release_time = date_part + time_offset
                
                writer.writerow({
                    'match_id': match['match_id'],
                    'release_time': release_time,
                    'home_win_odds': '',
                    'draw_odds': '',
                    'away_win_odds': ''
                })
    
    print(f"Created odds_wdl.csv template")

def write_odds_handicap(matches):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, 'odds_handicap.csv')
    
    headers = [
        'match_id', 'release_time', 'handicap_value', 
        'handicap_win_odds', 'handicap_draw_odds', 'handicap_lose_odds'
    ]
    
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for match in matches:
            for i in range(3):
                if i == 0:
                    time_offset = '-03-00 10:00:00'
                elif i == 1:
                    time_offset = '-01-00 18:00:00'
                else:
                    time_offset = '-00-00 14:00:00'
                
                date_part = match['date'].replace('/', '-')
                release_time = date_part + time_offset
                
                for hc in [-1, -0.5, -1.5]:
                    writer.writerow({
                        'match_id': match['match_id'],
                        'release_time': release_time,
                        'handicap_value': hc,
                        'handicap_win_odds': '',
                        'handicap_draw_odds': '',
                        'handicap_lose_odds': ''
                    })
    
    print(f"Created odds_handicap.csv template")

def write_odds_score(matches):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, 'odds_score.csv')
    
    headers = ['match_id', 'release_time'] + EXACT_SCORES
    
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for match in matches:
            for i in range(2):
                if i == 0:
                    time_offset = '-01-00 18:00:00'
                else:
                    time_offset = '-00-00 14:00:00'
                
                date_part = match['date'].replace('/', '-')
                release_time = date_part + time_offset
                
                row = {'match_id': match['match_id'], 'release_time': release_time}
                for score in EXACT_SCORES:
                    row[score] = ''
                
                writer.writerow(row)
    
    print(f"Created odds_score.csv template")

def write_odds_total_goals(matches):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, 'odds_total_goals.csv')
    
    headers = ['match_id', 'release_time', 'goals_0', 'goals_1', 'goals_2', 'goals_3', 
               'goals_4', 'goals_5', 'goals_6', 'goals_7+']
    
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        for match in matches:
            for i in range(3):
                if i == 0:
                    time_offset = '-03-00 10:00:00'
                elif i == 1:
                    time_offset = '-01-00 18:00:00'
                else:
                    time_offset = '-00-00 14:00:00'
                
                date_part = match['date'].replace('/', '-')
                release_time = date_part + time_offset
                
                row = {'match_id': match['match_id'], 'release_time': release_time}
                for g in ['goals_0', 'goals_1', 'goals_2', 'goals_3', 
                          'goals_4', 'goals_5', 'goals_6', 'goals_7+']:
                    row[g] = ''
                
                writer.writerow(row)
    
    print(f"Created odds_total_goals.csv template")

def write_readme():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, 'README.txt')
    
    content = """历史比赛结果与赔率数据CSV模板说明
===================================

文件结构
--------
本模板包含5个CSV文件，用于记录历史比赛结果和赔率数据：

1. matches_main.csv     - 比赛基本信息和结果（每场比赛1行）
2. odds_wdl.csv         - 胜平负固定赔率（每场比赛多时间戳记录）
3. odds_handicap.csv    - 让球盘赔率（每场比赛多时间戳记录）
4. odds_score.csv       - 精确比分赔率（每场比赛多时间戳记录）
5. odds_total_goals.csv - 总进球赔率（每场比赛多时间戳记录）

数据关联
--------
所有文件通过 match_id 字段关联。match_id格式为：YYYYMMDD_HomeAbbr_vs_AwayAbbr
例如：20250815_MCI_vs_LIV

文件详细说明
------------

1. matches_main.csv（比赛主文件）
   - match_id: 比赛唯一标识
   - event_date: 比赛日期（格式: YYYY/MM/DD）
   - league_name: 联赛名称
   - home_team_name: 主队名称
   - away_team_name: 客队名称
   - result: 比赛结果（胜/平/负，以主队视角）
   - handicap_result: 让球结果（如 (-1)胜）
   - exact_score: 精确比分（格式: X:Y）
   - total_goals: 总进球数

2. odds_wdl.csv（胜平负赔率）
   - match_id: 比赛唯一标识
   - release_time: 赔率发布时间（格式: YYYY-MM-DD HH:MM:SS）
   - home_win_odds: 主胜赔率
   - draw_odds: 平局赔率
   - away_win_odds: 客胜赔率

3. odds_handicap.csv（让球盘赔率）
   - match_id: 比赛唯一标识
   - release_time: 赔率发布时间
   - handicap_value: 让球值（如 -1, -0.5, -1.5）
   - handicap_win_odds: 让球胜赔率
   - handicap_draw_odds: 让球平赔率
   - handicap_lose_odds: 让球负赔率

4. odds_score.csv（精确比分赔率）
   - match_id: 比赛唯一标识
   - release_time: 赔率发布时间
   - 1:0 ~ 2:5: 各精确比分赔率
   - 胜其它: 主队胜但比分不在以上列表
   - 平其它: 平局但比分不在以上列表
   - 负其它: 客队胜但比分不在以上列表

5. odds_total_goals.csv（总进球赔率）
   - match_id: 比赛唯一标识
   - release_time: 赔率发布时间
   - goals_0 ~ goals_6: 总进球数0-6的赔率
   - goals_7+: 总进球7个及以上的赔率

数据填写规范
------------
1. 赔率字段填写小数形式（如 1.85, 3.20, 4.50）
2. 空字段留空即可，系统会自动处理
3. 时间戳格式必须严格遵守：YYYY-MM-DD HH:MM:SS
4. 比赛日期格式：YYYY/MM/DD

使用流程
--------
1. 先填写 matches_main.csv 的比赛结果数据
2. 然后根据时间戳依次填写各赔率文件
3. 确保所有文件的 match_id 保持一致

注意事项
--------
- 模板已预填充875场英超、380场意甲、115场西甲比赛的基本信息
- 赔率数据需要手动填写
- 建议使用Excel或Google Sheets打开编辑
- 保存时请选择UTF-8编码
"""
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"Created README.txt")

def main():
    print("=" * 60)
    print("生成历史比赛结果与赔率数据CSV模板")
    print("=" * 60)
    
    print("\n1. 从数据库加载比赛数据...")
    matches = load_matches()
    print(f"   共加载 {len(matches)} 场比赛")
    
    print("\n2. 生成 matches_main.csv...")
    write_matches_main(matches)
    
    print("\n3. 生成 odds_wdl.csv...")
    write_odds_wdl(matches)
    
    print("\n4. 生成 odds_handicap.csv...")
    write_odds_handicap(matches)
    
    print("\n5. 生成 odds_score.csv...")
    write_odds_score(matches)
    
    print("\n6. 生成 odds_total_goals.csv...")
    write_odds_total_goals(matches)
    
    print("\n7. 生成 README.txt...")
    write_readme()
    
    print("\n" + "=" * 60)
    print("CSV模板生成完成!")
    print(f"文件位置: {OUTPUT_DIR}")
    print("=" * 60)
    print(f"\n文件清单:")
    print(f"  - matches_main.csv      ({len(matches)} 场比赛)")
    print(f"  - odds_wdl.csv          ({len(matches) * 3} 条记录)")
    print(f"  - odds_handicap.csv     ({len(matches) * 9} 条记录)")
    print(f"  - odds_score.csv        ({len(matches) * 2} 条记录)")
    print(f"  - odds_total_goals.csv  ({len(matches) * 3} 条记录)")
    print(f"  - README.txt            (说明文档)")

if __name__ == "__main__":
    main()