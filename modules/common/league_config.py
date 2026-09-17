"""联赛配置模块

包含所有联赛特定的配置信息，包括：
- 球队名称映射表（英文→中文）
- 联赛名称
- 默认参数

新增联赛时只需在此文件中添加配置即可。
"""

from typing import Dict

# ============================================================
# 联赛名称（用于数据库 match_type LIKE 查询）
# ============================================================

LEAGUE_NAMES = {
    'epl': '英超',
    'seriea': '意甲',
    'bundesliga': '德甲',
    'laliga': '西甲',
    'ligue1': '法甲',
}

# ============================================================
# 球队名称映射表（英文→中文）
# 每个联赛独立维护，新增联赛时只需添加新的映射表
# ============================================================

# 英超球队映射（57条）
EPL_TEAM_MAPPINGS: Dict[str, str] = {
    'Arsenal': '阿森纳',
    'Aston Villa': '阿斯顿维拉',
    'Brighton': '布莱顿',
    'Brentford': '布伦特福德',
    'Burnley': '伯恩利',
    'Chelsea': '切尔西',
    'Crystal Palace': '水晶宫',
    'Everton': '埃弗顿',
    'Fulham': '富勒姆',
    'Leeds United': '利兹联',
    'Leicester': '莱斯特城',
    'Liverpool': '利物浦',
    'Manchester City': '曼城',
    'Manchester United': '曼联',
    'Newcastle United': '纽卡斯尔',
    'Nottingham Forest': '诺丁汉森林',
    'Southampton': '南安普顿',
    'Tottenham': '热刺',
    'West Ham': '西汉姆联',
    'Wolves': '狼队',
    'Sunderland': '桑德兰',
    'Arsenal FC': '阿森纳',
    'Liverpool FC': '利物浦',
    'Man City': '曼城',
    'Man Utd': '曼联',
    'Spurs': '热刺',
    'West Ham United': '西汉姆联',
    'Newcastle': '纽卡斯尔',
    'AFC Bournemouth': '伯恩茅斯',
    'Bournemouth': '伯恩茅斯',
    'Brighton & Hove Albion': '布莱顿',
    'Ipswich Town': '伊普斯维奇',
    'Ipswich': '伊普斯维奇',
    'Leicester City': '莱斯特城',
    'Nottingham Forest FC': '诺丁汉森林',
    'Wolverhampton Wanderers': '狼队',
    'Wolves FC': '狼队',
}

# 意甲球队映射（48条）
SERIEA_TEAM_MAPPINGS: Dict[str, str] = {
    'AC Milan': 'AC米兰',
    'Milan': 'AC米兰',
    'Inter Milan': '国际米兰',
    'Inter': '国际米兰',
    'Juventus': '尤文图斯',
    'Juventus FC': '尤文图斯',
    'Napoli': '那不勒斯',
    'SSC Napoli': '那不勒斯',
    'Roma': '罗马',
    'AS Roma': '罗马',
    'Lazio': '拉齐奥',
    'SS Lazio': '拉齐奥',
    'Atalanta': '亚特兰大',
    'Atalanta BC': '亚特兰大',
    'Fiorentina': '佛罗伦萨',
    'ACF Fiorentina': '佛罗伦萨',
    'Bologna': '博洛尼亚',
    'Bologna FC': '博洛尼亚',
    'Torino': '都灵',
    'Torino FC': '都灵',
    'Udinese': '乌迪内斯',
    'Udinese Calcio': '乌迪内斯',
    'Sampdoria': '桑普多利亚',
    'UC Sampdoria': '桑普多利亚',
    'Genoa': '热那亚',
    'Genoa CFC': '热那亚',
    'Palermo': '巴勒莫',
    'US Città di Palermo': '巴勒莫',
    'Hellas Verona': '维罗纳',
    'Verona': '维罗纳',
    'Cagliari': '卡利亚里',
    'Cagliari Calcio': '卡利亚里',
    'Empoli': '恩波利',
    'Empoli FC': '恩波利',
    'Parma': '帕尔马',
    'Parma Calcio': '帕尔马',
    'Como': '科莫',
    'Como 1907': '科莫',
    'Venezia': '威尼斯',
    'Venezia FC': '威尼斯',
    'Sassuolo': '萨索洛',
    'US Sassuolo': '萨索洛',
    'Lecce': '莱切',
    'US Lecce': '莱切',
    'Pisa': '比萨',
    'Pisa SC': '比萨',
    'Cremonese': '克雷莫纳',
    'US Cremonese': '克雷莫纳',
}

# 德甲球队映射（30条）
BUNDESLIGA_TEAM_MAPPINGS: Dict[str, str] = {
    'Bayern Munich': '拜仁慕尼黑', 'FC Bayern München': '拜仁慕尼黑',
    'Borussia Dortmund': '多特蒙德', 'Dortmund': '多特蒙德',
    'RB Leipzig': '莱比锡红牛',
    'Bayer Leverkusen': '勒沃库森', 'Bayer 04 Leverkusen': '勒沃库森', 'Leverkusen': '勒沃库森',
    'Eintracht Frankfurt': '法兰克福', 'Ein Frankfurt': '法兰克福',
    'VfB Stuttgart': '斯图加特', 'Stuttgart': '斯图加特',
    'Borussia Mönchengladbach': '门兴格拉德巴赫',
    "Borussia M'gladbach": '门兴格拉德巴赫', "M'gladbach": '门兴格拉德巴赫',
    'Werder Bremen': '云达不来梅', 'SV Werder Bremen': '云达不来梅',
    'Augsburg': '奥格斯堡', 'FC Augsburg': '奥格斯堡',
    'Wolfsburg': '沃尔夫斯堡', 'VfL Wolfsburg': '沃尔夫斯堡',
    'Mainz 05': '美因茨', 'Mainz': '美因茨', '1. FSV Mainz 05': '美因茨',
    'SC Freiburg': '弗赖堡', 'Freiburg': '弗赖堡',
    'Union Berlin': '柏林联合', '1. FC Union Berlin': '柏林联合',
    'FC Köln': '科隆', 'FC Koln': '科隆', '1. FC Köln': '科隆',
    'Heidenheim': '海登海姆', '1. FC Heidenheim': '海登海姆',
    'Holstein Kiel': '基尔',
    'St. Pauli': '圣保利', 'St Pauli': '圣保利', 'FC St. Pauli': '圣保利',
    'Hoffenheim': '霍芬海姆', 'TSG Hoffenheim': '霍芬海姆',
    'Hamburg': '汉堡', 'Hamburger SV': '汉堡',
    'Darmstadt 98': '达姆施塔特', 'VfL Bochum 1848': '波鸿',
}

# 西甲球队映射（35条）
LALIGA_TEAM_MAPPINGS: Dict[str, str] = {
    'Real Madrid': '皇家马德里',
    'Barcelona': '巴塞罗那', 'FC Barcelona': '巴塞罗那',
    'Atlético Madrid': '马德里竞技',
    'Sevilla': '塞维利亚',
    'Real Sociedad': '皇家社会',
    'Villarreal': '比利亚雷亚尔',
    'Real Betis': '皇家贝蒂斯',
    'Athletic Bilbao': '毕尔巴鄂竞技', 'Athletic Club': '毕尔巴鄂竞技',
    'Valencia': '巴伦西亚',
    'Getafe': '赫塔费',
    'Osasuna': '奥萨苏纳',
    'Girona': '赫罗纳', 'Girona FC': '赫罗纳',
    'Mallorca': '马洛卡',
    'Rayo Vallecano': '巴列卡诺',
    'Cadiz': '加的斯', 'Cádiz': '加的斯',
    'Alaves': '阿拉维斯', 'Deportivo Alavés': '阿拉维斯',
    'Real Valladolid': '瓦拉多利德',
    'Las Palmas': '拉斯帕尔马斯', 'UD Las Palmas': '拉斯帕尔马斯',
    'Leganés': '莱加内斯',
    'Almería': '阿尔梅里亚',
    'Celta Vigo': '维戈塞尔塔',
    'Granada': '格拉纳达CF',
    'Elche': '埃尔切',
    'Espanyol': '西班牙人',
    'Levante UD': '莱万特',
    'Real Oviedo': '奥维耶多',
}

# 法甲球队映射（30条）
LIGUE1_TEAM_MAPPINGS: Dict[str, str] = {
    'Paris Saint-Germain': '巴黎圣日尔曼', 'Paris SG': '巴黎圣日尔曼',
    'Olympique Marseille': '马赛', 'Olympique de Marseille': '马赛', 'Marseille': '马赛',
    'Monaco': '摩纳哥', 'AS Monaco': '摩纳哥',
    'Lyon': '里昂', 'Olympique Lyonnais': '里昂',
    'Lille': '里尔',
    'Rennes': '雷恩', 'Stade Rennais': '雷恩',
    'Nice': '尼斯',
    'Lens': '朗斯', 'RC Lens': '朗斯',
    'Reims': '兰斯', 'Stade de Reims': '兰斯',
    'Brest': '布雷斯特', 'Stade Brestois': '布雷斯特',
    'Angers': '昂热',
    'Strasbourg': '斯特拉斯堡', 'RC Strasbourg': '斯特拉斯堡',
    'Toulouse': '图卢兹',
    'Saint-Étienne': '圣埃蒂安',
    'Auxerre': '欧塞尔',
    'Nantes': '南特',
    'Le Havre': '勒阿弗尔',
    'Lorient': '洛里昂',
    'Clermont': '克莱蒙', 'Clermont Foot': '克莱蒙',
    'Montpellier': '蒙彼利埃',
    'Metz': '梅斯',
    'Paris FC': '巴黎FC',
}

# 联赛键 → 球队映射表
LEAGUE_TEAM_MAPPINGS = {
    'epl': EPL_TEAM_MAPPINGS,
    'seriea': SERIEA_TEAM_MAPPINGS,
    'bundesliga': BUNDESLIGA_TEAM_MAPPINGS,
    'laliga': LALIGA_TEAM_MAPPINGS,
    'ligue1': LIGUE1_TEAM_MAPPINGS,
}

# 联赛键 → 默认联赛名称
LEAGUE_DEFAULTS = {
    'epl': '英超',
    'seriea': '意甲',
    'bundesliga': '德甲',
    'laliga': '西甲',
    'ligue1': '法甲',
}


def get_team_mappings(league: str) -> Dict[str, str]:
    """根据联赛键获取球队映射表"""
    return LEAGUE_TEAM_MAPPINGS.get(league, {})


def get_league_name(league: str) -> str:
    """根据联赛键获取联赛中文名"""
    return LEAGUE_DEFAULTS.get(league, league)