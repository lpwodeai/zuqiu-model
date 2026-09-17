import json
import re

NAME_MAPPING = {
    'Liverpool': '利物浦',
    'Aston Villa': '阿斯顿维拉',
    'Brighton': '布莱顿',
    'Sunderland': '桑德兰',
    'Tottenham': '热刺',
    'Wolves': '狼队',
    'Chelsea': '切尔西',
    "Nott'm Forest": '诺丁汉森林',
    'Man United': '曼联',
    'Leeds': '利兹联',
    'West Ham': '西汉姆',
    'Man City': '曼城',
    'Bournemouth': '伯恩茅斯',
    'Brentford': '布伦特福德',
    'Burnley': '伯恩利',
    'Arsenal': '阿森纳',
    'Crystal Palace': '水晶宫',
    'Everton': '埃弗顿',
    'Fulham': '富勒姆',
    'Newcastle': '纽卡斯尔',
    'Bayern Munich': '拜仁慕尼黑',
    'Ein Frankfurt': '法兰克福',
    'Freiburg': '弗赖堡',
    'Heidenheim': '海登海姆',
    'Leverkusen': '勒沃库森',
    'Union Berlin': '柏林联合',
    'St Pauli': '圣保利',
    'Mainz': '美因茨',
    "M'gladbach": '门兴格拉德巴赫',
    'Hamburg': '汉堡',
    'Hoffenheim': '霍芬海姆',
    'RB Leipzig': '莱比锡',
    'Stuttgart': '斯图加特',
    'Werder Bremen': '云达不莱梅',
    'Augsburg': '奥格斯堡',
    'Wolfsburg': '沃尔夫斯堡',
    'Dortmund': '多特蒙德',
    'FC Koln': '科隆',
    'Girona': '赫罗纳',
    'Villarreal': '比利亚雷亚尔',
    'Mallorca': '马洛卡',
    'Alaves': '阿拉维斯',
    'Valencia': '瓦伦西亚',
    'Celta': '塞尔塔',
    'Ath Bilbao': '毕尔巴鄂',
    'Espanol': '西班牙人',
    'Elche': '埃尔切',
    'Real Madrid': '皇家马德里',
    'Betis': '皇家贝蒂斯',
    'Ath Madrid': '马德里竞技',
    'Levante': '莱万特',
    'Osasuna': '奥萨苏纳',
    'Sociedad': '皇家社会',
    'Oviedo': '奥维耶多',
    'Sevilla': '塞维利亚',
    'Vallecano': '巴列卡诺',
    'Getafe': '赫塔费',
    'Barcelona': '巴塞罗那',
    'Genoa': '热那亚',
    'Sassuolo': '萨索洛',
    'Milan': 'AC米兰',
    'Roma': '罗马',
    'Cagliari': '卡利亚里',
    'Como': '科莫',
    'Atalanta': '亚特兰大',
    'Juventus': '尤文图斯',
    'Udinese': '乌迪内斯',
    'Inter': '国际米兰',
    'Cremonese': '克雷莫纳',
    'Lecce': '莱切',
    'Bologna': '博洛尼亚',
    'Parma': '帕尔马',
    'Napoli': '那不勒斯',
    'Pisa': '比萨',
    'Torino': '都灵',
    'Lazio': '拉齐奥',
    'Fiorentina': '佛罗伦萨',
    'Verona': '维罗纳',
    'Rennes': '雷恩',
    'Lens': '朗斯',
    'Monaco': '摩纳哥',
    'Nice': '尼斯',
    'Brest': '布雷斯特',
    'Angers': '昂热',
    'Auxerre': '欧塞尔',
    'Metz': '梅斯',
    'Nantes': '南特',
    'Paris SG': '巴黎圣日耳曼',
    'Marseille': '马赛',
    'Le Havre': '勒阿弗尔',
    'Lorient': '洛里昂',
    'Lyon': '里昂',
    'Strasbourg': '斯特拉斯堡',
    'Toulouse': '图卢兹',
    'Lille': '里尔',
    'Paris FC': '巴黎FC',
}

KEY_MAPPING = {
    "nott'm_forest": 'pl_nfo',
    'man_united': 'pl_mun',
    'leeds': 'pl_leeds',
    'burnley': 'pl_bur',
    'ein_frankfurt': 'bl1_fra',
    'heidenheim': 'bl1_hei',
    'st_pauli': 'bl1_stp',
    "m'gladbach": 'bl1_bmg',
    'hamburg': 'bl1_ham',
    'fc_koln': 'bl1_koe',
    'alaves': 'sa_alv',
    'ath_bilbao': 'sa_bil',
    'espanol': 'sa_esp',
    'betis': 'sa_bet',
    'ath_madrid': 'sa_atm',
    'levante': 'sa_lev',
    'sociedad': 'sa_rso',
    'oviedo': 'sa_ovi',
    'vallecano': 'sa_rva',
    'seriea_gen': 'it_gen',
    'sassuolo': 'it_sas',
    'milan': 'it_mil',
    'seriea_rom': 'it_rom',
    'cagliari': 'it_cag',
    'como': 'it_com',
    'seriea_atl': 'it_atl',
    'seriea_juv': 'it_juv',
    'seriea_udi': 'it_udi',
    'seriea_int': 'it_int',
    'seriea_cre': 'it_cre',
    'seriea_lec': 'it_lec',
    'seriea_bov': 'it_bov',
    'parma': 'it_par',
    'seriea_nap': 'it_nap',
    'pisa': 'it_pis',
    'seriea_tor': 'it_tor',
    'seriea_laz': 'it_laz',
    'seriea_flo': 'it_flo',
    'seriea_ver': 'it_ver',
    'lens': 'fl1_len',
    'auxerre': 'fl1_aux',
}

LEAGUE_MAPPING = {
    'SerieA': 'IT',
}

def main():
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    input_path = os.path.join(project_dir, 'assets', 'team_attributes.js')
    output_path = os.path.join(project_dir, 'assets', 'team_attributes.js')
    
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    match = re.search(r'var TEAM_ATTRIBUTES = (\{[\s\S]*\});', content)
    if not match:
        print('❌ 无法找到TEAM_ATTRIBUTES定义')
        return
    
    data = json.loads(match[1])
    
    new_data = {}
    for key, team in data.items():
        new_key = KEY_MAPPING.get(key, key)
        
        if 'name' in team and team['name'] in NAME_MAPPING:
            team['name'] = NAME_MAPPING[team['name']]
        
        if 'league' in team and team['league'] in LEAGUE_MAPPING:
            team['league'] = LEAGUE_MAPPING[team['league']]
        
        new_data[new_key] = team
    
    sorted_data = dict(sorted(new_data.items()))
    
    new_json = json.dumps(sorted_data, ensure_ascii=False, indent=2)
    new_content = f'var TEAM_ATTRIBUTES = {new_json};\n'
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f'✅ 已更新 {len(new_data)} 支球队')
    print(f'🔄 重命名了 {len(KEY_MAPPING)} 个键')

if __name__ == '__main__':
    main()