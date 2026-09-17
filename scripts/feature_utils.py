import sqlite3
import pandas as pd
import numpy as np
import yaml
import os
import re
import sys

# 自动检测项目根目录，避免硬编码 h: 或 f: 盘符
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from db_utils import connect, read_sql, table_exists  # noqa: E402

DB_PATH = os.path.join(_PROJECT_DIR, "data", "five_leagues.db")
ODDS_DB_PATH = os.path.join(_PROJECT_DIR, "data", "odds.db")
ODDS_TIMING_DB_PATH = os.path.join(_PROJECT_DIR, "data", "odds_timing.db")
CONFIG_PATH = os.path.join(_PROJECT_DIR, "config.yaml")

TEAM_NAME_MAP = {
    # ========================
    # 英超 (Premier League)
    # 注意：中文名以 score_history 表实际使用的变体为准
    # ========================
    'Liverpool': '利物浦', 'Liverpool FC': '利物浦',
    'Manchester City': '曼彻斯特城', 'Manchester United': '曼彻斯特联',
    'Arsenal': '阿森纳', 'Tottenham Hotspur': '托特纳姆热刺', 'Chelsea': '切尔西',
    'Newcastle United': '纽卡斯尔联', 'Aston Villa': '阿斯顿维拉',
    'Brighton': '布赖顿', 'Brighton & Hove Albion': '布赖顿',
    'West Ham United': '西汉姆联', 'Brentford': '布伦特福德', 'Fulham': '富勒姆',
    'Crystal Palace': '水晶宫', 'Wolverhampton Wanderers': '狼队', 'Wolverhampton': '狼队',
    'Everton': '埃弗顿', 'Nottingham Forest': '诺丁汉森林',
    'Bournemouth': '伯恩茅斯', 'Burnley': '伯恩利',
    'Luton Town': '卢顿', 'Sheffield United': '谢菲尔德联',
    'Southampton': '南安普敦', 'Leeds United': '利兹联',
    'Leicester City': '莱切斯特城', 'Ipswich Town': '伊普斯维奇',
    'Sunderland': '桑德兰', 'Coventry City': '考文垂',
    # 旧标准名 → score_history 变体名
    '曼城': '曼彻斯特城', '曼联': '曼彻斯特联',
    '布莱顿': '布赖顿', '热刺': '托特纳姆热刺',
    '纽卡斯尔': '纽卡斯尔联', '西汉姆': '西汉姆联',
    '诺丁汉': '诺丁汉森林', '伊普斯': '伊普斯维奇',
    '布伦特': '布伦特福德',
    'Hull City': '赫尔城', 'Hull': '赫尔城',

    # ========================
    # 西甲 (La Liga)
    # 注意：中文名以 score_history 表实际使用的变体为准
    # ========================
    'Real Madrid': '皇家马德里', 'Barcelona': '巴塞罗那', 'FC Barcelona': '巴塞罗那',
    'Atlético Madrid': '马德里竞技', 'Sevilla': '塞维利亚',
    'Real Sociedad': '皇家社会', 'Villarreal': '比利亚雷亚尔',
    'Real Betis': '皇家贝蒂斯', 'Athletic Bilbao': '毕尔巴鄂竞技',
    'Athletic Club': '毕尔巴鄂竞技', 'Valencia': '巴伦西亚',
    'Getafe': '赫塔费', 'Osasuna': '奥萨苏纳',
    'Girona': '赫罗纳', 'Girona FC': '赫罗纳',
    'Mallorca': '马洛卡', 'Rayo Vallecano': '巴列卡诺',
    'Cadiz': '加的斯', 'Cádiz': '加的斯',
    'Alaves': '阿拉维斯', 'Deportivo Alavés': '阿拉维斯',
    'Real Valladolid': '瓦拉多利德', 'Las Palmas': '拉斯帕尔马斯',
    'UD Las Palmas': '拉斯帕尔马斯', 'Leganés': '莱加内斯',
    'Almería': '阿尔梅里亚', 'Celta Vigo': '维戈塞尔塔',
    'Granada': '格拉纳达CF', 'Elche': '埃尔切',
    'Espanyol': '西班牙人', 'Levante UD': '莱万特',
    'Real Oviedo': '奥维耶多',
    # 旧标准名 → score_history 变体名
    '瓦伦西亚': '巴伦西亚', '巴利亚多利德': '瓦拉多利德',
    '毕尔巴鄂': '毕尔巴鄂竞技', '马略卡': '马洛卡',
    '塞尔塔': '维戈塞尔塔', '格拉纳达': '格拉纳达CF',
    '皇马': '皇家马德里',
    'Espanol': '西班牙人', 'Levante': '莱万特',
    'Vallecano': '巴列卡诺', 'Deportivo': '拉科鲁尼亚',
    # 升班马/短名别名补全（2026-27 赛季 R3 短名 mapping）
    'Deportivo de A Coruña': '拉科鲁尼亚', 'Deportivo de A Coruna': '拉科鲁尼亚',
    'Deportivo La Coruña': '拉科鲁尼亚', 'Deportivo La Coruna': '拉科鲁尼亚',
    'La Coruña': '拉科鲁尼亚', 'La Coruna': '拉科鲁尼亚',
    'Málaga CF': '马拉加', 'Malaga CF': '马拉加', 'Málaga': '马拉加', 'Malaga': '马拉加',
    'Real Racing Club': '桑坦德竞技', 'Real Racing': '桑坦德竞技',

    # ========================
    # 意甲 (Serie A)
    # ========================
    'Juventus': '尤文图斯', 'Inter Milan': '国际米兰', 'Inter': '国际米兰',
    'AC Milan': 'AC米兰', 'Milan': 'AC米兰', 'AC': 'AC米兰',
    'Napoli': '那不勒斯', 'SSC Napoli': '那不勒斯',
    'Roma': '罗马', 'AS Roma': '罗马',
    'Lazio': '拉齐奥', 'Atalanta': '亚特兰大',
    'Fiorentina': '佛罗伦萨', 'Bologna': '博洛尼亚',
    'Torino': '都灵', 'Udinese': '乌迪内斯', 'Genoa': '热那亚',
    'Hellas Verona': '维罗纳', 'Verona': '维罗纳',
    'Lecce': '莱切', 'Cagliari': '卡利亚里',
    'Empoli': '恩波利', 'Monza': '蒙扎',
    'Como': '科莫', 'Cremonese': '克雷莫纳',
    'Parma': '帕尔马', 'Pisa': '比萨',
    'Sassuolo': '萨索洛', 'Frosinone': '弗洛西诺内',
    'Salernitana': '萨勒尼塔纳', 'Venezia': '威尼斯',
    # score_history 中文名变体
    '科莫': '科莫', '克雷莫纳': '克雷莫纳', '帕尔马': '帕尔马',
    '比萨': '比萨', '萨索洛': '萨索洛', '弗洛西诺内': '弗洛西诺内',
    '萨勒尼塔纳': '萨勒尼塔纳', '威尼斯': '威尼斯',
    '弗罗西诺内': '弗洛西诺内',

    # ========================
    # 德甲 (Bundesliga)
    # 注意：中文名以 score_history 表实际使用的变体为准
    # ========================
    'Bayern Munich': '拜仁慕尼黑', 'FC Bayern München': '拜仁慕尼黑',
    'Borussia Dortmund': '多特蒙德', 'Dortmund': '多特蒙德',
    'RB Leipzig': '莱比锡红牛', 'Bayer Leverkusen': '勒沃库森',
    'Bayer 04 Leverkusen': '勒沃库森', 'Leverkusen': '勒沃库森',
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
    'Holstein Kiel': '基尔', 'St. Pauli': '圣保利',
    'St Pauli': '圣保利', 'FC St. Pauli': '圣保利',
    'Hoffenheim': '霍芬海姆', 'TSG Hoffenheim': '霍芬海姆',
    'Hamburg': '汉堡', 'Hamburger SV': '汉堡',
    'Darmstadt 98': '达姆施塔特', 'VfL Bochum 1848': '波鸿',
    # 旧标准名 → score_history 变体名
    '云达不莱梅': '云达不来梅',
    'Schalke 04': '沙尔克04', 'FC Schalke 04': '沙尔克04', 'Schalke': '沙尔克04',

    # ========================
    # 法甲 (Ligue 1)
    # 注意：中文名以 score_history 表实际使用的变体为准
    # ========================
    'Paris Saint-Germain': '巴黎圣日尔曼', 'Paris SG': '巴黎圣日尔曼',
    'Olympique Marseille': '马赛', 'Olympique de Marseille': '马赛',
    'Marseille': '马赛', 'Monaco': '摩纳哥', 'AS Monaco': '摩纳哥',
    'Lyon': '里昂', 'Olympique Lyonnais': '里昂',
    'Lille': '里尔', 'Rennes': '雷恩', 'Stade Rennais': '雷恩',
    'Nice': '尼斯', 'Lens': '朗斯', 'RC Lens': '朗斯',
    'Reims': '兰斯', 'Stade de Reims': '兰斯',
    'Brest': '布雷斯特', 'Stade Brestois': '布雷斯特',
    'Angers': '昂热', 'Strasbourg': '斯特拉斯堡', 'RC Strasbourg': '斯特拉斯堡',
    'Toulouse': '图卢兹', 'Saint-Étienne': '圣埃蒂安',
    'Auxerre': '欧塞尔', 'Nantes': '南特', 'Le Havre': '勒阿弗尔',
    'Lorient': '洛里昂', 'Clermont': '克莱蒙', 'Clermont Foot': '克莱蒙',
    'Troyes': '特鲁瓦', 'ESTAC Troyes': '特鲁瓦',
    'Montpellier': '蒙彼利埃', 'Metz': '梅斯', 'Paris FC': '巴黎FC',
    # 旧标准名 → score_history 变体名
    '巴黎圣日耳曼': '巴黎圣日尔曼',
    '斯特拉斯': '斯特拉斯堡',
    'Le Mans': '勒芒', 'Le Mans FC': '勒芒',

    # ========================
    # 其他联赛球队（score_history中可能出现）
    # ========================
    '埃尔沃斯堡': '埃沃斯堡', '罗德兹': '罗德兹', '敦刻尔克': '敦刻尔克',
    '杜塞尔多夫': '杜塞尔多夫',
}

def normalize_team_name(name):
    # 标准化空格（处理 "St       Pauli" → "St Pauli" 等异常）
    if isinstance(name, str):
        name = ' '.join(name.split())
    if name in TEAM_NAME_MAP:
        return TEAM_NAME_MAP[name]
    if name in TEAM_NAME_MAP.values():
        return name
    name_lower = name.lower()
    for eng, ch in TEAM_NAME_MAP.items():
        if eng.lower() == name_lower:
            return ch
    return name


def canonical_team_name(match_id, field_value, is_home):
    """归一化队名（以 post_match_review 队名字段为权威，match_id 仅兜底）。

    home_team/away_team 来自实际赛果数据，队名身份最可靠；match_id 是生成的
    键，可能因共享前缀等被误匹配（如实际主队为「拉科鲁尼亚」却被记成
    "Deportivo Alavés"），故不作权威来源，只在字段名无法归一为中文时兜底。
    """
    def _has_cjk(s):
        return any('\u4e00' <= c <= '\u9fff' for c in str(s))

    primary = normalize_team_name(field_value)
    if primary and _has_cjk(primary):
        return primary
    # 字段名为英文/空 → 用 match_id 英文兜底
    if match_id:
        try:
            parts = str(match_id).split('_')
            if len(parts) >= 3:
                cn = normalize_team_name(parts[1] if is_home else parts[2])
                if cn and _has_cjk(cn):
                    return cn
        except Exception:
            pass
    return primary


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

def _resolve_feature_time_decay():
    """读取特征工程用时间衰减配置（优先feature_time_decay，向后兼容time_decay别名）"""
    t = CONFIG.get('training', {}) or {}
    ft = t.get('feature_time_decay')
    if isinstance(ft, dict) and ft:
        return ft
    # 向后兼容：老版本只有 training.time_decay
    return t.get('time_decay', {})


def calculate_time_decay_weights(dates, reference_date, config=None):
    if config is None:
        config = _resolve_feature_time_decay()
    
    decay_type = config.get('decay_type', 'exponential')
    half_life_days = config.get('half_life_days', 14)
    min_weight = config.get('min_weight', 0.01)
    max_history_days = config.get('max_history_days', 90)
    
    if len(dates) == 0:
        return np.array([])
    
    if hasattr(dates, 'dt'):
        days_diff = (reference_date - dates).dt.days
    else:
        days_diff = np.array([(reference_date - d).days for d in dates])
    
    days_diff = np.array(days_diff)
    mask = days_diff <= max_history_days
    weights = np.zeros(len(dates))
    
    if decay_type == 'exponential':
        weights[mask] = np.exp(-days_diff[mask] * np.log(2) / half_life_days)
    elif decay_type == 'linear':
        weights[mask] = np.maximum(0, 1 - days_diff[mask] / max_history_days)
    elif decay_type == 'custom':
        weights[mask] = np.exp(-days_diff[mask] / half_life_days)
    else:
        weights[mask] = 1.0
    
    weights[mask] = np.maximum(weights[mask], min_weight)
    
    if weights.sum() > 0:
        weights = weights / weights.sum()
    
    return weights

def weighted_mean(values, weights):
    if weights.sum() == 0:
        return values.mean() if len(values) > 0 else 0
    return (values * weights).sum() / weights.sum()

def load_match_data(db_path=None):
    if db_path is None:
        db_path = DB_PATH
    
    conn = sqlite3.connect(db_path)
    query = """
    SELECT 
        m.date,
        ht.name as home_team_name,
        at.name as away_team_name,
        m.homeGoals,
        m.awayGoals,
        c.name as competition_name
    FROM matches m
    LEFT JOIN teams ht ON m.homeTeamId = ht.id
    LEFT JOIN teams at ON m.awayTeamId = at.id
    LEFT JOIN competitions c ON m.competitionId = c.id
    WHERE m.homeGoals IS NOT NULL AND m.awayGoals IS NOT NULL
    ORDER BY m.date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    
    df['date'] = pd.to_datetime(df['date'])
    df['result'] = np.where(df['homeGoals'] > df['awayGoals'], 2,
                           np.where(df['homeGoals'] < df['awayGoals'], 0, 1))
    df['goal_diff'] = df['homeGoals'] - df['awayGoals']
    df['total_goals'] = df['homeGoals'] + df['awayGoals']
    
    return df

def load_match_data_odds(db_path=None):
    """
    从odds.db加载5大联赛完整比赛数据（含所有联赛）
    
    odds.db 包含：
    - 1288场比赛（英超/意甲/西甲/德甲/法甲全覆盖）
    - wdl_history: 8128条WDL赔率记录
    - handicap_history: 7723条让球赔率记录
    - total_goals_history: 5477条总进球赔率记录
    - score_history: 94578条比分赔率记录
    """
    if db_path is None:
        db_path = ODDS_DB_PATH
    
    if not os.path.exists(db_path):
        print(f"  [ERROR] odds.db 不存在: {db_path}")
        return load_match_data()
    
    conn = connect(db_path=db_path)
    
    query = """
    SELECT 
        m.match_date as date,
        m.home_team as home_team_name,
        m.away_team as away_team_name,
        m.actual_score,
        m.actual_wdl,
        m.league as competition_name,
        m.match_id
    FROM matches m
    WHERE m.actual_score IS NOT NULL
    ORDER BY m.match_date
    """
    df = read_sql(query, conn)
    conn.close()
    
    if len(df) == 0:
        print("  [WARN]  odds.db中无有效比赛数据，回退到five_leagues.db")
        return load_match_data()
    
    df['date'] = pd.to_datetime(df['date'], format='mixed')
    
    df['home_team_name'] = df['home_team_name'].apply(normalize_team_name)
    df['away_team_name'] = df['away_team_name'].apply(normalize_team_name)
    
    home_goals = []
    away_goals = []
    
    for score in df['actual_score']:
        if pd.isna(score):
            home_goals.append(np.nan)
            away_goals.append(np.nan)
        elif '-' in str(score):
            parts = str(score).split('-')
            home_goals.append(int(parts[0].strip()))
            away_goals.append(int(parts[1].strip()))
        elif ':' in str(score):
            parts = str(score).split(':')
            home_goals.append(int(parts[0].strip()))
            away_goals.append(int(parts[1].strip()))
        else:
            home_goals.append(np.nan)
            away_goals.append(np.nan)
    
    df['homeGoals'] = home_goals
    df['awayGoals'] = away_goals
    
    df = df.dropna(subset=['homeGoals', 'awayGoals'])
    
    df['result'] = np.where(df['homeGoals'] > df['awayGoals'], 2,
                           np.where(df['homeGoals'] < df['awayGoals'], 0, 1))
    df['goal_diff'] = df['homeGoals'] - df['awayGoals']
    df['total_goals'] = df['homeGoals'] + df['awayGoals']
    
    # 联赛名统一归一化为中文标准名（英超/西甲/意甲/德甲/法甲）
    # 覆盖四赛季（26/27、25/26、24/25、23/24）的中文赛季变体 + 国际数据源英文名（fbref/SofaScore 原始值）
    # 修复点：原映射只覆盖 25/26，导致 24/25、23/24 赛季保留 "英超2024-2025赛季" 等变体，
    # 训练时 league 字段出现 15 个唯一值（应 5 个），one-hot 列翻倍且验证集联赛字段在训练集未见，
    # 同时 league_weight 机制失效。统一后 one-hot 仅 5 列，联赛权重真正生效。
    competition_map = {
        # 26/27 赛季（新赛季，2026 更新）
        '英超2026-2027赛季': '英超',
        '意甲2026-2027赛季': '意甲',
        '西甲2026-2027赛季': '西甲',
        '德甲2026-2027赛季': '德甲',
        '法甲2026-2027赛季': '法甲',
        # 25/26 赛季
        '英超2025-2026赛季': '英超',
        '意甲2025-2026赛季': '意甲',
        '西甲2025-2026赛季': '西甲',
        '德甲2025-2026赛季': '德甲',
        '法甲2025-2026赛季': '法甲',
        # 24/25 赛季
        '英超2024-2025赛季': '英超',
        '意甲2024-2025赛季': '意甲',
        '西甲2024-2025赛季': '西甲',
        '德甲2024-2025赛季': '德甲',
        '法甲2024-2025赛季': '法甲',
        # 23/24 赛季
        '英超2023-2024赛季': '英超',
        '意甲2023-2024赛季': '意甲',
        '西甲2023-2024赛季': '西甲',
        '德甲2023-2024赛季': '德甲',
        '法甲2023-2024赛季': '法甲',
        # 国际数据源英文名（fbref/SofaScore 原始值）
        'Premier League': '英超',
        'Serie A': '意甲',
        'La Liga': '西甲',
        'Bundesliga': '德甲',
        'Ligue 1': '法甲',
    }
    df['competition_name'] = df['competition_name'].map(
        lambda x: competition_map.get(x, x)
    )
    
    print(f"  [OK] 从odds.db加载: {len(df)} 场比赛")
    print(f"     联赛分布:")
    for league, count in df['competition_name'].value_counts().items():
        print(f"       - {league}: {count} 场")
    
    return df

def check_data_quality(df):
    print("\n数据质量检查报告:")
    print(f"总记录数: {len(df)}")
    print(f"缺失值统计:")
    missing_stats = df.isnull().sum()
    for col, count in missing_stats.items():
        if count > 0:
            print(f"  {col}: {count} ({count/len(df)*100:.2f}%)")
    
    print(f"\n比赛结果分布:")
    result_counts = df['result'].value_counts()
    for result, count in result_counts.items():
        label = '客胜' if result == 0 else ('平局' if result == 1 else '主胜')
        print(f"  {label}: {count} ({count/len(df)*100:.2f}%)")
    
    print(f"\n联赛分布:")
    league_counts = df['competition_name'].value_counts()
    for league, count in league_counts.items():
        print(f"  {league}: {count}")
    
    print(f"\n日期范围: {df['date'].min().strftime('%Y-%m-%d')} 至 {df['date'].max().strftime('%Y-%m-%d')}")
    
    return {
        'total_records': len(df),
        'missing_stats': missing_stats,
        'result_distribution': result_counts,
        'league_distribution': league_counts,
        'date_range': (df['date'].min(), df['date'].max())
    }

def winsorize_series(series, lower_percentile=1, upper_percentile=99):
    if len(series.dropna()) == 0:
        return series
    lower = np.percentile(series.dropna(), lower_percentile)
    upper = np.percentile(series.dropna(), upper_percentile)
    return series.clip(lower=lower, upper=upper)

def build_features(df):
    features = pd.DataFrame()
    
    league_dummies = pd.get_dummies(df['competition_name'], prefix='league')
    features = pd.concat([features, league_dummies], axis=1)
    
    features['month'] = df['date'].dt.month
    features['day_of_week'] = df['date'].dt.dayofweek
    features['is_weekend'] = (df['date'].dt.dayofweek >= 5).astype(int)
    
    features['is_early_season'] = (df['date'].dt.month.isin([8, 9])).astype(int)
    features['is_mid_season'] = (df['date'].dt.month.isin([10, 11, 12, 1, 2])).astype(int)
    features['is_late_season'] = (df['date'].dt.month.isin([3, 4, 5])).astype(int)
    
    features = features.fillna(0)
    
    numeric_cols = features.select_dtypes(include=[np.number]).columns
    for col in numeric_cols:
        if col.startswith('league_') or col.startswith('is_'):
            continue
        features[col] = winsorize_series(features[col], lower_percentile=1, upper_percentile=99)
    
    return features

def get_global_league_stats(df):
    stats = {}
    for league in df['competition_name'].unique():
        league_df = df[df['competition_name'] == league]
        
        home_goals = league_df['homeGoals']
        away_goals = league_df['awayGoals']
        
        stats[league] = {
            'avg_home_goals': home_goals.mean(),
            'avg_away_goals': away_goals.mean(),
            'avg_total_goals': (home_goals + away_goals).mean(),
            'home_win_rate': (home_goals > away_goals).mean(),
            'draw_rate': (home_goals == away_goals).mean(),
            'away_win_rate': (home_goals < away_goals).mean(),
            'goals_std': (home_goals + away_goals).std(),
            'goal_diff_std': (home_goals - away_goals).std(),
            'home_goals_std': home_goals.std(),
            'away_goals_std': away_goals.std()
        }
    
    global_stats = {
        'avg_home_goals': df['homeGoals'].mean(),
        'avg_away_goals': df['awayGoals'].mean(),
        'avg_total_goals': df['total_goals'].mean(),
        'home_win_rate': (df['homeGoals'] > df['awayGoals']).mean(),
        'draw_rate': (df['homeGoals'] == df['awayGoals']).mean(),
        'away_win_rate': (df['homeGoals'] < df['awayGoals']).mean(),
        'goals_std': df['total_goals'].std(),
        'goal_diff_std': df['goal_diff'].std(),
        'home_goals_std': df['homeGoals'].std(),
        'away_goals_std': df['awayGoals'].std()
    }
    
    return stats, global_stats

def calc_h2h_stats(df, home_team, away_team, match_date):
    # 修复 L-012: 使用括号明确运算符优先级，确保日期过滤作用于所有条件
    h2h_hist = df[
        (
            ((df['home_team_name'] == home_team) & (df['away_team_name'] == away_team)) |
            ((df['home_team_name'] == away_team) & (df['away_team_name'] == home_team))
        ) &
        (df['date'] < match_date)
    ].sort_values('date').tail(10)
    
    if len(h2h_hist) == 0:
        return {
            'h2h_matches': 0,
            'h2h_home_win_rate': np.nan,
            'h2h_away_win_rate': np.nan,
            'h2h_draw_rate': np.nan,
            'h2h_avg_goals_home': np.nan,
            'h2h_avg_goals_away': np.nan,
            'h2h_avg_total_goals': np.nan,
            'h2h_goal_diff_avg': np.nan,
            'h2h_last_result': np.nan,
            'h2h_home_streak': 0,
            'h2h_away_streak': 0
        }
    
    home_wins = 0
    away_wins = 0
    draws = 0
    home_goals_total = 0
    away_goals_total = 0
    goal_diffs = []
    
    for _, r in h2h_hist.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        
        home_goals_total += hg
        away_goals_total += ag
        goal_diffs.append(hg - ag)
        
        if hg > ag:
            home_wins += 1
        elif hg < ag:
            away_wins += 1
        else:
            draws += 1
    
    total = len(h2h_hist)
    home_win_rate = home_wins / total
    away_win_rate = away_wins / total
    draw_rate = draws / total
    
    h2h_sorted_rev = h2h_hist.iloc[::-1]
    home_streak = 0
    for _, r in h2h_sorted_rev.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        if hg > ag:
            home_streak += 1
        else:
            break
    
    away_streak = 0
    for _, r in h2h_sorted_rev.iterrows():
        if r['home_team_name'] == home_team:
            hg, ag = r['homeGoals'], r['awayGoals']
        else:
            hg, ag = r['awayGoals'], r['homeGoals']
        if hg < ag:
            away_streak += 1
        else:
            break
    
    last_row = h2h_hist.iloc[-1]
    if last_row['home_team_name'] == home_team:
        hg, ag = last_row['homeGoals'], last_row['awayGoals']
    else:
        hg, ag = last_row['awayGoals'], last_row['homeGoals']
    if hg > ag:
        last_result = 2
    elif hg < ag:
        last_result = 0
    else:
        last_result = 1
    
    return {
        'h2h_matches': total,
        'h2h_home_win_rate': home_win_rate,
        'h2h_away_win_rate': away_win_rate,
        'h2h_draw_rate': draw_rate,
        'h2h_avg_goals_home': home_goals_total / total,
        'h2h_avg_goals_away': away_goals_total / total,
        'h2h_avg_total_goals': (home_goals_total + away_goals_total) / total,
        'h2h_goal_diff_avg': np.mean(goal_diffs),
        'h2h_last_result': last_result,
        'h2h_home_streak': home_streak,
        'h2h_away_streak': away_streak
    }

def _h2h_meetings_index(df):
    """预计算历史交锋索引: frozenset({队A, 队B}) -> 按日期升序的比赛记录列表。

    将 build_team_features 中「每场对全量 df 做布尔过滤 + sort」(O(n²)) 降为
    一次 O(n) 建索引 + 每场 O(1) 字典查找，是特征构建的主要性能瓶颈修复。
    """
    cols = ['date', 'home_team_name', 'away_team_name', 'homeGoals', 'awayGoals']
    idx = {}
    for rec in df[cols].sort_values('date').itertuples(index=False, name='H2HRec'):
        key = frozenset((rec.home_team_name, rec.away_team_name))
        idx.setdefault(key, []).append(rec)
    return idx

def _h2h_stats_from_records(records, home_team):
    """从已过滤（按日期升序、≤10 条）的交锋记录计算统计量，替代 calc_h2h_stats 的全量 df 过滤主体。"""
    total = len(records)
    if total == 0:
        return {
            'h2h_matches': 0,
            'h2h_home_win_rate': np.nan,
            'h2h_away_win_rate': np.nan,
            'h2h_draw_rate': np.nan,
            'h2h_avg_goals_home': np.nan,
            'h2h_avg_goals_away': np.nan,
            'h2h_avg_total_goals': np.nan,
            'h2h_goal_diff_avg': np.nan,
            'h2h_last_result': np.nan,
            'h2h_home_streak': 0,
            'h2h_away_streak': 0,
        }

    home_wins = 0
    away_wins = 0
    draws = 0
    home_goals_total = 0
    away_goals_total = 0
    goal_diffs = []

    for r in records:
        if r.home_team_name == home_team:
            hg, ag = r.homeGoals, r.awayGoals
        else:
            hg, ag = r.awayGoals, r.homeGoals
        home_goals_total += hg
        away_goals_total += ag
        goal_diffs.append(hg - ag)
        if hg > ag:
            home_wins += 1
        elif hg < ag:
            away_wins += 1
        else:
            draws += 1

    def _goal_pair(r):
        return (r.homeGoals, r.awayGoals) if r.home_team_name == home_team else (r.awayGoals, r.homeGoals)

    home_streak = 0
    for r in reversed(records):
        hg, ag = _goal_pair(r)
        if hg > ag:
            home_streak += 1
        else:
            break

    away_streak = 0
    for r in reversed(records):
        hg, ag = _goal_pair(r)
        if hg < ag:
            away_streak += 1
        else:
            break

    hg_last, ag_last = _goal_pair(records[-1])
    last_result = 2 if hg_last > ag_last else (0 if hg_last < ag_last else 1)

    return {
        'h2h_matches': total,
        'h2h_home_win_rate': home_wins / total,
        'h2h_away_win_rate': away_wins / total,
        'h2h_draw_rate': draws / total,
        'h2h_avg_goals_home': home_goals_total / total,
        'h2h_avg_goals_away': away_goals_total / total,
        'h2h_avg_total_goals': (home_goals_total + away_goals_total) / total,
        'h2h_goal_diff_avg': np.mean(goal_diffs),
        'h2h_last_result': last_result,
        'h2h_home_streak': home_streak,
        'h2h_away_streak': away_streak,
    }

def precompute_team_stats(df):
    all_teams = pd.concat([df['home_team_name'], df['away_team_name']]).unique()
    league_stats, global_stats = get_global_league_stats(df)
    
    team_stats_cache = {}
    
    for team in all_teams:
        team_matches = df[(df['home_team_name'] == team) | (df['away_team_name'] == team)].sort_values('date').copy()
        
        is_home = team_matches['home_team_name'] == team
        team_goals = np.where(is_home, team_matches['homeGoals'], team_matches['awayGoals'])
        opp_goals = np.where(is_home, team_matches['awayGoals'], team_matches['homeGoals'])
        
        team_matches['team_goals'] = team_goals
        team_matches['opp_goals'] = opp_goals
        team_matches['is_win'] = (team_goals > opp_goals).astype(int)
        team_matches['is_draw'] = (team_goals == opp_goals).astype(int)
        team_matches['is_loss'] = (team_goals < opp_goals).astype(int)
        
        team_matches['cum_wins'] = team_matches['is_win'].cumsum()
        team_matches['cum_draws'] = team_matches['is_draw'].cumsum()
        team_matches['cum_losses'] = team_matches['is_loss'].cumsum()
        team_matches['cum_goals'] = team_matches['team_goals'].cumsum()
        team_matches['cum_opp_goals'] = team_matches['opp_goals'].cumsum()
        team_matches['games_played'] = np.arange(1, len(team_matches) + 1)
        
        team_matches['win_rate'] = team_matches['cum_wins'] / team_matches['games_played']
        team_matches['draw_rate'] = team_matches['cum_draws'] / team_matches['games_played']
        team_matches['loss_rate'] = team_matches['cum_losses'] / team_matches['games_played']
        team_matches['avg_goals'] = team_matches['cum_goals'] / team_matches['games_played']
        team_matches['avg_opp_goals'] = team_matches['cum_opp_goals'] / team_matches['games_played']
        
        # 用 expanding().std() 向量化替代逐场 [:i+1].std() 的 O(m²) 循环（性能优化）
        team_matches['goals_std'] = team_matches['team_goals'].expanding(min_periods=1).std().fillna(0)
        
        team_matches['recent_form'] = team_matches['is_win'].rolling(window=5, min_periods=1).mean()
        team_matches['form_trend'] = team_matches['is_win'].rolling(window=6).mean() - team_matches['is_win'].rolling(window=6).mean().shift(6)
        
        consecutive_wins = []
        consecutive_losses = []
        consecutive_undefeated = []
        current_wins = 0
        current_losses = 0
        current_undefeated = 0
        for _, row in team_matches.iterrows():
            if row['is_win'] == 1:
                current_wins += 1
                current_losses = 0
                current_undefeated += 1
            elif row['is_loss'] == 1:
                current_wins = 0
                current_losses += 1
                current_undefeated = 0
            else:
                current_wins = 0
                current_losses = 0
                current_undefeated += 1
            consecutive_wins.append(current_wins)
            consecutive_losses.append(current_losses)
            consecutive_undefeated.append(current_undefeated)
        
        team_matches['consecutive_wins'] = consecutive_wins
        team_matches['consecutive_losses'] = consecutive_losses
        team_matches['consecutive_undefeated'] = consecutive_undefeated
        
        home_mask = team_matches['home_team_name'] == team
        home_subset = team_matches[home_mask].copy()
        away_subset = team_matches[~home_mask].copy()
        
        if len(home_subset) > 0:
            home_subset['home_win_rate'] = home_subset['is_win'].cumsum() / np.arange(1, len(home_subset) + 1)
            home_subset['home_avg_goals'] = home_subset['team_goals'].cumsum() / np.arange(1, len(home_subset) + 1)
        if len(away_subset) > 0:
            away_subset['away_win_rate'] = away_subset['is_win'].cumsum() / np.arange(1, len(away_subset) + 1)
            away_subset['away_avg_goals'] = away_subset['team_goals'].cumsum() / np.arange(1, len(away_subset) + 1)
        
        if len(home_subset) > 0:
            team_matches = team_matches.merge(
                home_subset[['date', 'home_win_rate', 'home_avg_goals']],
                on='date', how='left'
            )
        else:
            team_matches['home_win_rate'] = 0.0
            team_matches['home_avg_goals'] = 0.0
        
        if len(away_subset) > 0:
            team_matches = team_matches.merge(
                away_subset[['date', 'away_win_rate', 'away_avg_goals']],
                on='date', how='left'
            )
        else:
            team_matches['away_win_rate'] = 0.0
            team_matches['away_avg_goals'] = 0.0
        
        team_matches[['home_win_rate', 'away_win_rate', 'home_avg_goals', 'away_avg_goals']] = \
            team_matches[['home_win_rate', 'away_win_rate', 'home_avg_goals', 'away_avg_goals']].fillna(0)
        
        decay_cfg = _resolve_feature_time_decay()
        if decay_cfg.get('enabled', True):
            decay_type = decay_cfg.get('decay_type', 'exponential')
            half_life = decay_cfg.get('half_life_days', 14)
            min_weight = decay_cfg.get('min_weight', 0.01)
            max_hist = decay_cfg.get('max_history_days', 90)
            alpha = np.log(2) / half_life
            # 指数衰减且地权下限在窗口内永不生效（min_weight <= exp(-max_hist*α)）时，
            # 归一化权重 = exp(α·t_k) / Σ exp(α·t_j)，可用前缀和 O(m) 精确向量化；
            # 否则回退到逐场滑动窗口（结果一致，仅较慢）。
            floor_noop = (decay_type == 'exponential' and min_weight <= np.exp(-max_hist * alpha))
            if floor_noop:
                day = team_matches['date'].values.astype('datetime64[D]').astype(np.int64)
                t_rel = (day - day[-1]).astype(np.float64)  # <= 0，避免 exp 溢出/下溢
                E = np.exp(alpha * t_rel)  # <= 1，老比赛趋近 0
                win = team_matches['is_win'].values.astype(np.float64)
                goals = team_matches['team_goals'].values.astype(np.float64)
                PE = np.concatenate([[0.0], np.cumsum(E)])
                PEW = np.concatenate([[0.0], np.cumsum(E * win)])
                PEG = np.concatenate([[0.0], np.cumsum(E * goals)])
                weighted_win = np.empty(len(team_matches))
                weighted_goal = np.empty(len(team_matches))
                n = len(team_matches)
                start = 0
                for i in range(n):
                    while start < i and (day[i] - day[start]) > max_hist:
                        start += 1
                    if i >= 3 and start < i:
                        sumE = PE[i] - PE[start]
                        if sumE > 0:
                            weighted_win[i] = (PEW[i] - PEW[start]) / sumE
                            weighted_goal[i] = (PEG[i] - PEG[start]) / sumE
                            continue
                    weighted_win[i] = team_matches['win_rate'].iloc[i]
                    weighted_goal[i] = team_matches['avg_goals'].iloc[i]
                team_matches['weighted_win_rate'] = weighted_win
                team_matches['weighted_avg_goals'] = weighted_goal
            else:
                max_hist_ = max_hist
                dates_series = team_matches['date']
                win_mask_all = team_matches['is_win'].values
                goals_all = team_matches['team_goals'].values
                win_rate_all = team_matches['win_rate'].values
                avg_goals_all = team_matches['avg_goals'].values
                weighted_win_rates = []
                weighted_avg_goals = []
                start = 0
                n = len(team_matches)
                for i in range(n):
                    ref = dates_series.iloc[i]
                    while start < i and (ref - dates_series.iloc[start]).days > max_hist_:
                        start += 1
                    if i >= 3 and start < i:
                        weights = calculate_time_decay_weights(dates_series.iloc[start:i], ref, config=decay_cfg)
                        if weights.sum() > 0:
                            wm = win_mask_all[start:i]
                            g = goals_all[start:i]
                            weighted_win_rates.append(float((wm * weights).sum() / weights.sum()))
                            weighted_avg_goals.append(float((g * weights).sum() / weights.sum()))
                            continue
                    weighted_win_rates.append(win_rate_all[i])
                    weighted_avg_goals.append(avg_goals_all[i])
                team_matches['weighted_win_rate'] = weighted_win_rates
                team_matches['weighted_avg_goals'] = weighted_avg_goals
        else:
            team_matches['weighted_win_rate'] = team_matches['win_rate']
            team_matches['weighted_avg_goals'] = team_matches['avg_goals']
        
        team_matches['home_advantage'] = team_matches['home_win_rate'] - team_matches['away_win_rate']
        
        team_stats_cache[team] = team_matches
    
    return team_stats_cache, league_stats, global_stats

def build_team_features(df):
    team_stats_cache, league_stats, global_stats = precompute_team_stats(df)
    h2h_index = _h2h_meetings_index(df)  # 预计算交锋索引，O(n)，替代每场全量 df 过滤

    # 预提取每队「按日期升序」的日期(int64天)与统计 DataFrame，用 np.searchsorted
    # O(log m) 定位「该队 date<md 的最近一场」，替代逐场 home_hist[date<md] 全量布尔
    # 过滤 (O(n*m))。team_stats_cache[team] 经 merge 后为 RangeIndex 且按 date 升序，
    # 故布尔前缀过滤与 .iloc[:k] 完全等价。
    _EMPTY_D = np.array([], dtype=np.int64)
    lookup = {}
    for team, tm in team_stats_cache.items():
        di = tm['date'].values.astype('datetime64[D]').astype(np.int64)
        lookup[team] = (di, tm)

    def _last_before(di, tm, md):
        """返回该队 date<md 的最近一场 Series 行；无则 None。"""
        if tm is None:
            return None
        pos = int(np.searchsorted(di, md, side='left')) - 1
        return tm.iloc[pos] if pos >= 0 else None

    def _win_rate_before(di, tm, md):
        """返回 (该队 date<md 的场次数, 该队最近一场 win_rate)。"""
        if tm is None or len(di) == 0:
            return 0, None
        pos = int(np.searchsorted(di, md, side='left')) - 1
        if pos < 0:
            return 0, None
        return pos + 1, tm['win_rate'].iloc[pos]

    dates_all = df['date'].values.astype('datetime64[D]').astype(np.int64)
    home_names = df['home_team_name'].values
    away_names = df['away_team_name'].values
    league_names = df['competition_name'].values
    
    home_team_data = []
    away_team_data = []
    h2h_data = []
    opponent_data = []
    
    for i in range(len(df)):
        home_team = home_names[i]
        away_team = away_names[i]
        match_date = df['date'].iloc[i]
        md = dates_all[i]
        league = league_names[i]
        
        home_di, home_tm = lookup.get(home_team, (_EMPTY_D, None))
        away_di, away_tm = lookup.get(away_team, (_EMPTY_D, None))
        
        home_last = _last_before(home_di, home_tm, md)
        away_last = _last_before(away_di, away_tm, md)
        
        if home_last is not None:
            home_stats = {
                'home_avg_goals': home_last['avg_goals'],
                'home_avg_opp_goals': home_last['avg_opp_goals'],
                'home_win_rate': home_last['win_rate'],
                'home_draw_rate': home_last['draw_rate'],
                'home_loss_rate': home_last['loss_rate'],
                'home_goals_std': home_last['goals_std'],
                'home_recent_form': home_last['recent_form'],
                'home_form_trend': home_last['form_trend'],
                'home_consecutive_wins': home_last['consecutive_wins'],
                'home_consecutive_losses': home_last['consecutive_losses'],
                'home_consecutive_undefeated': home_last['consecutive_undefeated'],
                'home_games_played': home_last['games_played'],
                'home_weighted_win_rate': home_last['weighted_win_rate'],
                'home_weighted_avg_goals': home_last['weighted_avg_goals'],
                'home_home_win_rate': home_last['home_win_rate'],
                'home_away_win_rate': home_last['away_win_rate'],
                'home_home_goals': home_last['home_avg_goals'],
                'home_away_goals': home_last['away_avg_goals'],
                'home_home_advantage': home_last['home_advantage']
            }
        else:
            league_default = league_stats.get(league, global_stats)
            home_stats = {
                'home_avg_goals': league_default['avg_home_goals'],
                'home_avg_opp_goals': league_default['avg_away_goals'],
                'home_win_rate': league_default['home_win_rate'],
                'home_draw_rate': league_default['draw_rate'],
                'home_loss_rate': league_default['away_win_rate'],
                'home_goals_std': league_default['goals_std'],
                'home_recent_form': 1.0,
                'home_form_trend': 0.0,
                'home_consecutive_wins': 0,
                'home_consecutive_losses': 0,
                'home_consecutive_undefeated': 0,
                'home_games_played': 0,
                'home_weighted_win_rate': league_default['home_win_rate'],
                'home_weighted_avg_goals': league_default['avg_home_goals'],
                'home_home_win_rate': league_default['home_win_rate'],
                'home_away_win_rate': league_default['away_win_rate'],
                'home_home_goals': league_default['avg_home_goals'],
                'home_away_goals': league_default['avg_away_goals'],
                'home_home_advantage': league_default['home_win_rate'] - league_default['away_win_rate']
            }
        
        if away_last is not None:
            away_stats = {
                'away_avg_goals': away_last['avg_goals'],
                'away_avg_opp_goals': away_last['avg_opp_goals'],
                'away_win_rate': away_last['win_rate'],
                'away_draw_rate': away_last['draw_rate'],
                'away_loss_rate': away_last['loss_rate'],
                'away_goals_std': away_last['goals_std'],
                'away_recent_form': away_last['recent_form'],
                'away_form_trend': away_last['form_trend'],
                'away_consecutive_wins': away_last['consecutive_wins'],
                'away_consecutive_losses': away_last['consecutive_losses'],
                'away_consecutive_undefeated': away_last['consecutive_undefeated'],
                'away_games_played': away_last['games_played'],
                'away_weighted_win_rate': away_last['weighted_win_rate'],
                'away_weighted_avg_goals': away_last['weighted_avg_goals'],
                'away_home_win_rate': away_last['home_win_rate'],
                'away_away_win_rate': away_last['away_win_rate'],
                'away_home_goals': away_last['home_avg_goals'],
                'away_away_goals': away_last['away_avg_goals'],
                'away_home_advantage': away_last['home_advantage']
            }
        else:
            league_default = league_stats.get(league, global_stats)
            away_stats = {
                'away_avg_goals': league_default['avg_away_goals'],
                'away_avg_opp_goals': league_default['avg_home_goals'],
                'away_win_rate': league_default['away_win_rate'],
                'away_draw_rate': league_default['draw_rate'],
                'away_loss_rate': league_default['home_win_rate'],
                'away_goals_std': league_default['goals_std'],
                'away_recent_form': 1.0,
                'away_form_trend': 0.0,
                'away_consecutive_wins': 0,
                'away_consecutive_losses': 0,
                'away_consecutive_undefeated': 0,
                'away_games_played': 0,
                'away_weighted_win_rate': league_default['away_win_rate'],
                'away_weighted_avg_goals': league_default['avg_away_goals'],
                'away_home_win_rate': league_default['home_win_rate'],
                'away_away_win_rate': league_default['away_win_rate'],
                'away_home_goals': league_default['avg_home_goals'],
                'away_away_goals': league_default['avg_away_goals'],
                'away_home_advantage': league_default['home_win_rate'] - league_default['away_win_rate']
            }
        
        home_team_data.append(home_stats)
        away_team_data.append(away_stats)
        
        _h2h_records = [r for r in h2h_index.get(frozenset((home_team, away_team)), ()) if r.date < match_date][-10:]
        h2h = _h2h_stats_from_records(_h2h_records, home_team)
        h2h_data.append(h2h)
        
        home_opp_win_rates = []
        if home_tm is not None and len(home_di) > 0:
            pos_h = int(np.searchsorted(home_di, md, side='left')) - 1
            if pos_h >= 0:
                for opp in pd.unique(home_tm['away_team_name'].iloc[:pos_h + 1].iloc[:10]):
                    odi, otm = lookup.get(opp, (_EMPTY_D, None))
                    cnt, wr = _win_rate_before(odi, otm, md)
                    if cnt >= 5:
                        home_opp_win_rates.append(wr)
        
        away_opp_win_rates = []
        if away_tm is not None and len(away_di) > 0:
            pos_a = int(np.searchsorted(away_di, md, side='left')) - 1
            if pos_a >= 0:
                for opp in pd.unique(away_tm['away_team_name'].iloc[:pos_a + 1].iloc[:10]):
                    odi, otm = lookup.get(opp, (_EMPTY_D, None))
                    cnt, wr = _win_rate_before(odi, otm, md)
                    if cnt >= 5:
                        away_opp_win_rates.append(wr)
        
        opponent_data.append({
            'home_opponent_avg_win_rate': np.mean(home_opp_win_rates) if home_opp_win_rates else np.nan,
            'away_opponent_avg_win_rate': np.mean(away_opp_win_rates) if away_opp_win_rates else np.nan
        })
    
    home_df = pd.DataFrame(home_team_data, index=df.index)
    away_df = pd.DataFrame(away_team_data, index=df.index)
    h2h_df = pd.DataFrame(h2h_data, index=df.index)
    opp_df = pd.DataFrame(opponent_data, index=df.index)
    
    team_features = pd.concat([home_df, away_df, h2h_df, opp_df], axis=1)
    
    team_features['form_diff'] = team_features['home_win_rate'] - team_features['away_win_rate']
    team_features['goals_diff'] = team_features['home_avg_goals'] - team_features['away_avg_goals']
    team_features['defence_diff'] = team_features['away_avg_opp_goals'] - team_features['home_avg_opp_goals']
    team_features['recent_form_diff'] = team_features['home_recent_form'] - team_features['away_recent_form']
    team_features['form_trend_diff'] = team_features['home_form_trend'] - team_features['away_form_trend']
    
    team_features['streak_diff'] = (team_features['home_consecutive_wins'] - team_features['home_consecutive_losses']) - \
                                  (team_features['away_consecutive_wins'] - team_features['away_consecutive_losses'])
    team_features['undefeated_diff'] = team_features['home_consecutive_undefeated'] - team_features['away_consecutive_undefeated']
    
    team_features['weighted_form_diff'] = team_features['home_weighted_win_rate'] - team_features['away_weighted_win_rate']
    team_features['weighted_goals_diff'] = team_features['home_weighted_avg_goals'] - team_features['away_weighted_avg_goals']
    
    team_features['venue_diff'] = team_features['home_home_advantage'] - team_features['away_home_advantage']
    team_features['goals_stability_diff'] = team_features['away_goals_std'] - team_features['home_goals_std']
    team_features['opponent_strength_diff'] = team_features['home_opponent_avg_win_rate'] - team_features['away_opponent_avg_win_rate']
    
    league_medians = team_features.groupby(df['competition_name']).transform('median')
    team_features = team_features.fillna(league_medians)
    
    global_medians = team_features.median()
    team_features = team_features.fillna(global_medians)
    
    for col in team_features.columns:
        team_features[col] = winsorize_series(team_features[col], lower_percentile=1, upper_percentile=99)
    
    return team_features

def load_odds_database():
    """加载赔率数据库连接"""
    return connect(db_path=ODDS_DB_PATH)


def load_wdl_history(match_id, conn):
    """加载胜平负赔率历史"""
    query = """
        SELECT timestamp, win_a, draw, win_b 
        FROM wdl_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = read_sql(query, conn, params=(match_id,))
    if not df.empty:
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
        except Exception:
            pass
    return df


def load_handicap_history(match_id, conn):
    """加载让球赔率历史"""
    query = """
        SELECT timestamp, hcp_win, hcp_draw, hcp_lose 
        FROM handicap_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = read_sql(query, conn, params=(match_id,))
    if not df.empty:
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
        except Exception:
            pass
    return df


def load_total_goals_history(match_id, conn):
    """加载总进球赔率历史"""
    query = """
        SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus 
        FROM total_goals_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = read_sql(query, conn, params=(match_id,))
    if not df.empty:
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
        except Exception:
            pass
    return df


def calculate_implied_probability(odds):
    """从赔率计算隐含概率"""
    if odds <= 0:
        return 0.0
    return 1.0 / odds


def calculate_kelly_criterion(implied_prob, odds):
    """
    计算凯利指数（D-010 赔率衍生特征）

    公式: Kelly = (p × odds - 1) / (odds - 1)
    - p = 归一化隐含概率
    - odds = 收盘赔率
    - Kelly > 0: 赔率被高估，有价值投注信号
    - Kelly < 0: 赔率被低估，无价值

    返回值范围 [-1, 1]，clip防止极端值
    """
    if odds > 1.0 and implied_prob > 0:
        kelly = (implied_prob * odds - 1.0) / (odds - 1.0)
        return float(np.clip(kelly, -1.0, 1.0))
    return 0.0


def calculate_change_rate(close_odds, open_odds):
    """
    计算赔率变化率（D-010 动量指标）

    公式: change_rate = (close - open) / open
    - 正值: 赔率上升（该结果可能性下降，市场看淡）
    - 负值: 赔率下降（该结果可能性上升，市场看好）

    返回值clip到 [-1, 1] 防止极端值
    """
    if open_odds > 0:
        rate = (close_odds - open_odds) / open_odds
        return float(np.clip(rate, -1.0, 1.0))
    return 0.0


def _kelly_array(implied, odds):
    """向量化凯利指数（与 calculate_kelly_criterion 等价）。"""
    implied = np.asarray(implied, dtype=float)
    odds = np.asarray(odds, dtype=float)
    out = np.zeros_like(implied)
    m = (odds > 1.0) & (implied > 0)
    out[m] = np.clip((implied[m] * odds[m] - 1.0) / (odds[m] - 1.0), -1.0, 1.0)
    return out


def _change_rate_array(close, open_):
    """向量化赔率变化率（与 calculate_change_rate 等价）。"""
    close = np.asarray(close, dtype=float)
    open_ = np.asarray(open_, dtype=float)
    out = np.zeros_like(close)
    m = open_ > 0
    out[m] = np.clip((close[m] - open_[m]) / open_[m], -1.0, 1.0)
    return out


def build_odds_features(df, odds_conn=None):
    """
    构建赔率特征（向量化重写版，C-20260830-007）

    与原逐行实现完全等价，但将「逐行 iterrows + 逐行 DB 查询」替换为：
    1. 一次性批量加载 wdl_history / handicap_history / total_goals_history 三张表；
    2. 按 match_id 聚合开盘(首条)/收盘(末条)记录；
    3. 用 numpy 向量化计算全部派生特征。

    行为保持一致：
    - 仅按 matches.match_id == 历史表.match_id 直接对齐（与旧逻辑相同）；
    - 未对齐场次的所有赔率特征为 NaN（record_count/has_/coverage 为 0）。
    """
    if odds_conn is None:
        odds_conn = load_odds_database()

    if 'match_id' not in df.columns:
        # 无 match_id 时回退旧逐行逻辑（按日期+队名构造候选 match_id）
        return _build_odds_features_legacy(df, odds_conn)

    def _load_and_agg(table, cols):
        """批量加载单张赔率历史表，返回 (开盘值, 收盘值, 记录数) 三个矩阵/数组。"""
        try:
            raw = read_sql(
                f"SELECT match_id, timestamp, {', '.join(cols)} FROM {table}", odds_conn
            )
        except Exception:
            return None, None, None
        if raw.empty:
            return None, None, None
        # 按 (match_id, timestamp字符串) 排序，等价于 SQL ORDER BY timestamp 后的取首/取末
        raw = raw.sort_values(['match_id', 'timestamp'], kind='mergesort')
        first = raw.drop_duplicates(subset='match_id', keep='first').set_index('match_id')[cols]
        last = raw.drop_duplicates(subset='match_id', keep='last').set_index('match_id')[cols]
        cnt = raw.groupby('match_id', sort=False).size()
        return first, last, cnt

    def _align(first, last, cnt):
        """把按历史表 match_id 聚合的结果对齐到 df 行序（直接 match_id 相等）。"""
        if first is None:
            return None, None, None, pd.Series(False, index=df.index)
        # 统一为 object 字符串，避免 pandas StringDtype 与 numpy object 索引不匹配
        keys = first.index.astype(object)
        first = first.copy(); first.index = keys
        last = last.copy(); last.index = keys
        cnt = cnt.copy(); cnt.index = keys
        mid = pd.Index(df['match_id'].astype(object))
        f = first.reindex(mid).to_numpy(dtype=float)
        l = last.reindex(mid).to_numpy(dtype=float)
        c = cnt.reindex(mid).to_numpy(dtype=float)
        matched = np.asarray(mid.isin(keys), dtype=bool)
        return f, l, c, matched

    wdl_cols = ['win_a', 'draw', 'win_b']
    hcp_cols = ['hcp_win', 'hcp_draw', 'hcp_lose']
    tg_cols = ['goals_0', 'goals_1', 'goals_2', 'goals_3',
               'goals_4', 'goals_5', 'goals_6', 'goals_7_plus']

    wdl_first, wdl_last, wdl_cnt = _load_and_agg('wdl_history', wdl_cols)
    hcp_first, hcp_last, hcp_cnt = _load_and_agg('handicap_history', hcp_cols)
    tg_first, tg_last, tg_cnt = _load_and_agg('total_goals_history', tg_cols)

    wdl_f, wdl_l, wdl_c, wdl_matched = _align(wdl_first, wdl_last, wdl_cnt)
    hcp_f, hcp_l, hcp_c, hcp_matched = _align(hcp_first, hcp_last, hcp_cnt)
    _, tg_l, tg_c, tg_matched = _align(tg_first, tg_last, tg_cnt)

    def _three_way(fst, lst, cnt, matched, pfx, with_fav):
        """构建胜平负/让球三路赔率特征（开盘/收盘/趋势/隐含概率/凯利/变化率）。"""
        fst = np.asarray(fst, dtype=float)
        lst = np.asarray(lst, dtype=float)
        cnt = np.asarray(cnt, dtype=float)
        matched = np.asarray(matched, dtype=bool)

        ow, od, ol = fst[:, 0], fst[:, 1], fst[:, 2]
        cw, cd, cl = lst[:, 0], lst[:, 1], lst[:, 2]

        with np.errstate(divide='ignore', invalid='ignore'):
            ih = np.where(cw > 0, 1.0 / cw, np.nan)
            id_ = np.where(cd > 0, 1.0 / cd, np.nan)
            ia = np.where(cl > 0, 1.0 / cl, np.nan)

        total = ih + id_ + ia
        valid = total > 0

        imp_win = np.where(matched & valid, ih / total, np.where(matched, 1.0 / 3.0, np.nan))
        imp_draw = np.where(matched & valid, id_ / total, np.where(matched, 1.0 / 3.0, np.nan))
        imp_lose = np.where(matched & valid, ia / total, np.where(matched, 1.0 / 3.0, np.nan))

        out = {
            f'{pfx}_open_win': ow, f'{pfx}_open_draw': od, f'{pfx}_open_lose': ol,
            f'{pfx}_close_win': cw, f'{pfx}_close_draw': cd, f'{pfx}_close_lose': cl,
            f'{pfx}_win_trend': cw - ow, f'{pfx}_draw_trend': cd - od, f'{pfx}_lose_trend': cl - ol,
            f'{pfx}_implied_win': imp_win, f'{pfx}_implied_draw': imp_draw, f'{pfx}_implied_lose': imp_lose,
            f'{pfx}_record_count': np.where(matched, cnt, 0.0),
            f'{pfx}_kelly_win': _kelly_array(imp_win, cw),
            f'{pfx}_kelly_draw': _kelly_array(imp_draw, cd),
            f'{pfx}_kelly_lose': _kelly_array(imp_lose, cl),
            f'{pfx}_win_change_rate': _change_rate_array(cw, ow),
            f'{pfx}_draw_change_rate': _change_rate_array(cd, od),
            f'{pfx}_lose_change_rate': _change_rate_array(cl, ol),
        }

        if with_fav:
            imp_stack = np.column_stack([ih, id_, ia])
            fav = np.argmax(imp_stack, axis=1).astype(float)
            imp_fill = np.where(np.isnan(imp_stack), -np.inf, imp_stack)
            raw_max = imp_fill.max(axis=1)
            out[f'{pfx}_favorite'] = np.where(matched, fav, np.nan)
            out[f'{pfx}_favorite_prob'] = (
                np.where(matched & valid, raw_max / total, np.where(matched, 0.0, np.nan))
            )
            out[f'{pfx}_overround'] = np.where(matched & valid, total, np.where(matched, 1.0, np.nan))

        # 未对齐行：除 record_count 外全部置 NaN（与原 else 分支一致）
        for k in out:
            if k == f'{pfx}_record_count':
                continue
            out[k] = np.where(matched, out[k], np.nan)
        return out

    def _tg_feat(lst, cnt, matched):
        """构建总进球赔率特征（大小球概率/最可能/期望）。"""
        lst = np.asarray(lst, dtype=float)
        cnt = np.asarray(cnt, dtype=float)
        matched = np.asarray(matched, dtype=bool)

        total = lst.sum(axis=1)
        valid = total > 0
        with np.errstate(divide='ignore', invalid='ignore'):
            norm = lst / total[:, None]

        over_idx = np.array([3, 4, 5, 6, 7])
        under_idx = np.array([0, 1, 2])
        glabels = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        norm_fill = np.where(np.isnan(norm), -np.inf, norm)

        return {
            'tg_over_25_prob': np.where(matched & valid, norm[:, over_idx].sum(axis=1), np.nan),
            'tg_under_25_prob': np.where(matched & valid, norm[:, under_idx].sum(axis=1), np.nan),
            'tg_most_likely': np.where(matched & valid, np.argmax(norm, axis=1).astype(float), np.nan),
            'tg_most_likely_prob': np.where(matched & valid, norm_fill.max(axis=1), np.nan),
            'tg_expected': np.where(matched & valid, (norm * glabels).sum(axis=1), np.nan),
            'tg_record_count': np.where(matched, cnt, 0.0),
        }

    data = {}
    data.update(_three_way(wdl_f, wdl_l, wdl_c, wdl_matched, 'wdl', True))
    data.update(_three_way(hcp_f, hcp_l, hcp_c, hcp_matched, 'hcp', False))
    data.update(_tg_feat(tg_l, tg_c, tg_matched))

    has_wdl = wdl_matched.astype(int)
    has_hcp = hcp_matched.astype(int)
    has_tg = tg_matched.astype(int)
    data['has_wdl_odds'] = has_wdl
    data['has_hcp_odds'] = has_hcp
    data['has_tg_odds'] = has_tg
    data['odds_coverage'] = has_wdl + has_hcp + has_tg

    # D-010 市场置信度/熵/庄家利润率（基于 WDL 归一化隐含概率）
    probs = np.column_stack([data['wdl_implied_win'], data['wdl_implied_draw'], data['wdl_implied_lose']])
    probs_fill = np.where(np.isnan(probs), -np.inf, probs)
    data['odds_confidence'] = np.where(has_wdl.astype(bool), probs_fill.max(axis=1), np.nan)
    with np.errstate(divide='ignore', invalid='ignore'):
        logp = np.log(probs + 1e-10)
        ent_terms = np.where(probs > 0, probs * logp, 0.0)
    data['odds_entropy'] = np.clip(-ent_terms.sum(axis=1), 0.0, 1.1)
    data['bookmaker_margin'] = data['wdl_overround'] - 1.0

    odds_df = pd.DataFrame(data, index=df.index)

    # 确保所有预期的列都存在
    expected_columns = [
        'wdl_open_win', 'wdl_open_draw', 'wdl_open_lose',
        'wdl_close_win', 'wdl_close_draw', 'wdl_close_lose',
        'wdl_win_trend', 'wdl_draw_trend', 'wdl_lose_trend',
        'wdl_implied_win', 'wdl_implied_draw', 'wdl_implied_lose',
        'wdl_overround', 'wdl_favorite', 'wdl_favorite_prob',
        'wdl_record_count',
        'wdl_kelly_win', 'wdl_kelly_draw', 'wdl_kelly_lose',
        'wdl_win_change_rate', 'wdl_draw_change_rate', 'wdl_lose_change_rate',
        'hcp_open_win', 'hcp_open_draw', 'hcp_open_lose',
        'hcp_close_win', 'hcp_close_draw', 'hcp_close_lose',
        'hcp_win_trend', 'hcp_draw_trend', 'hcp_lose_trend',
        'hcp_implied_win', 'hcp_implied_draw', 'hcp_implied_lose',
        'hcp_record_count',
        'hcp_kelly_win', 'hcp_kelly_draw', 'hcp_kelly_lose',
        'hcp_win_change_rate', 'hcp_draw_change_rate', 'hcp_lose_change_rate',
        'tg_over_25_prob', 'tg_under_25_prob',
        'tg_most_likely', 'tg_most_likely_prob',
        'tg_expected', 'tg_record_count',
        'has_wdl_odds', 'has_hcp_odds', 'has_tg_odds', 'odds_coverage',
        'odds_confidence', 'odds_entropy', 'bookmaker_margin',
    ]

    for col in expected_columns:
        if col not in odds_df.columns:
            odds_df[col] = np.nan

    # 保持与旧实现一致的列顺序
    odds_df = odds_df.reindex(columns=expected_columns)

    # 缺失值处理：使用全局中位数填充（简化处理，避免groupby问题）
    for col in odds_df.columns:
        if col.startswith('has_') or col == 'odds_coverage':
            continue
        col_median = odds_df[col].median()
        odds_df[col] = odds_df[col].fillna(col_median)

    # 二元特征和计数特征使用0填充
    for col in ['has_wdl_odds', 'has_hcp_odds', 'has_tg_odds', 'odds_coverage',
                'wdl_record_count', 'hcp_record_count', 'tg_record_count']:
        if col in odds_df.columns:
            odds_df[col] = odds_df[col].fillna(0).astype(int)

    # 异常值处理
    for col in odds_df.columns:
        if col.startswith('has_') or col == 'odds_coverage':
            continue
        odds_df[col] = winsorize_series(odds_df[col], lower_percentile=1, upper_percentile=99)

    return odds_df


def _build_odds_features_legacy(df, odds_conn=None):
    """
    构建赔率特征（修复版）
    
    特征类型：
    1. 开盘赔率（最早记录）
    2. 收盘赔率（最新记录，比赛前）
    3. 赔率变化趋势（开盘vs收盘）
    4. 隐含概率
    5. 市场倾向指标
    
    修复: 
    - 支持中英文球队名匹配
    - 支持odds.db直接传入match_id
    - 修复日期格式解析
    """
    if odds_conn is None:
        odds_conn = load_odds_database()
    
    use_odds_match_id = 'match_id' in df.columns
    
    odds_features = []
    
    team_mapping = {}
    try:
        cursor = odds_conn.cursor()
        cursor.execute("SELECT original_name, standard_name FROM team_mapping")
        for orig, std in cursor.fetchall():
            team_mapping[orig] = std
    except Exception:
        pass
    
    for idx, row in df.iterrows():
        match_date = row['date']
        home_team = row['home_team_name']
        away_team = row['away_team_name']
        
        feature_dict = {}
        
        if use_odds_match_id and pd.notna(row.get('match_id')):
            match_ids_to_try = [row['match_id']]
        else:
            date_str = match_date.strftime('%Y-%m-%d')
            
            home_english = TEAM_NAME_MAP.get(home_team, home_team)
            away_english = TEAM_NAME_MAP.get(away_team, away_team)
            
            match_ids_to_try = [
                f"{date_str}_{home_team}_{away_team}",
                f"{date_str}_{home_team}_{away_team.replace(' ', '_')}",
                f"{date_str}_{home_team.replace(' ', '_')}_{away_team}",
                f"{date_str}_{home_english}_{away_english}",
                f"{date_str}_{home_english.replace(' ', '_')}_{away_english.replace(' ', '_')}",
            ]
        
        wdl_df = pd.DataFrame()
        for mid in match_ids_to_try:
            wdl_df = load_wdl_history(mid, odds_conn)
            if not wdl_df.empty:
                break
        
        if not wdl_df.empty:
            try:
                wdl_df['timestamp'] = pd.to_datetime(wdl_df['timestamp'], format='mixed')
            except Exception:
                pass
        
        if not wdl_df.empty and len(wdl_df) >= 1:
            first_row = wdl_df.iloc[0]
            last_row = wdl_df.iloc[-1]
            
            feature_dict['wdl_open_win'] = float(first_row['win_a'])
            feature_dict['wdl_open_draw'] = float(first_row['draw'])
            feature_dict['wdl_open_lose'] = float(first_row['win_b'])
            
            feature_dict['wdl_close_win'] = float(last_row['win_a'])
            feature_dict['wdl_close_draw'] = float(last_row['draw'])
            feature_dict['wdl_close_lose'] = float(last_row['win_b'])
            
            feature_dict['wdl_win_trend'] = float(last_row['win_a'] - first_row['win_a'])
            feature_dict['wdl_draw_trend'] = float(last_row['draw'] - first_row['draw'])
            feature_dict['wdl_lose_trend'] = float(last_row['win_b'] - first_row['win_b'])
            
            imp_home = calculate_implied_probability(last_row['win_a'])
            imp_draw = calculate_implied_probability(last_row['draw'])
            imp_away = calculate_implied_probability(last_row['win_b'])
            total_imp = imp_home + imp_draw + imp_away
            
            if total_imp > 0:
                feature_dict['wdl_implied_win'] = imp_home / total_imp
                feature_dict['wdl_implied_draw'] = imp_draw / total_imp
                feature_dict['wdl_implied_lose'] = imp_away / total_imp
                feature_dict['wdl_overround'] = total_imp
            else:
                feature_dict['wdl_implied_win'] = 1/3
                feature_dict['wdl_implied_draw'] = 1/3
                feature_dict['wdl_implied_lose'] = 1/3
                feature_dict['wdl_overround'] = 1.0
            
            feature_dict['wdl_favorite'] = float(np.argmax([imp_home, imp_draw, imp_away]))
            feature_dict['wdl_favorite_prob'] = float(max([imp_home, imp_draw, imp_away]) / total_imp if total_imp > 0 else 0)

            feature_dict['wdl_record_count'] = len(wdl_df)

            # === D-010: WDL 凯利指数（价值投注信号） ===
            feature_dict['wdl_kelly_win'] = calculate_kelly_criterion(
                feature_dict['wdl_implied_win'], float(last_row['win_a']))
            feature_dict['wdl_kelly_draw'] = calculate_kelly_criterion(
                feature_dict['wdl_implied_draw'], float(last_row['draw']))
            feature_dict['wdl_kelly_lose'] = calculate_kelly_criterion(
                feature_dict['wdl_implied_lose'], float(last_row['win_b']))

            # === D-010: WDL 赔率变化率（动量指标） ===
            feature_dict['wdl_win_change_rate'] = calculate_change_rate(
                float(last_row['win_a']), float(first_row['win_a']))
            feature_dict['wdl_draw_change_rate'] = calculate_change_rate(
                float(last_row['draw']), float(first_row['draw']))
            feature_dict['wdl_lose_change_rate'] = calculate_change_rate(
                float(last_row['win_b']), float(first_row['win_b']))
        else:
            wdl_base_features = [
                'wdl_open_win', 'wdl_open_draw', 'wdl_open_lose',
                'wdl_close_win', 'wdl_close_draw', 'wdl_close_lose',
                'wdl_win_trend', 'wdl_draw_trend', 'wdl_lose_trend',
                'wdl_implied_win', 'wdl_implied_draw', 'wdl_implied_lose',
                'wdl_overround', 'wdl_favorite', 'wdl_favorite_prob',
                'wdl_record_count',
                'wdl_kelly_win', 'wdl_kelly_draw', 'wdl_kelly_lose',
                'wdl_win_change_rate', 'wdl_draw_change_rate', 'wdl_lose_change_rate',
            ]
            for f in wdl_base_features:
                feature_dict[f] = np.nan if f != 'wdl_record_count' else 0
        
        if use_odds_match_id and pd.notna(row.get('match_id')):
            hcp_df = load_handicap_history(row['match_id'], odds_conn)
            tg_df = load_total_goals_history(row['match_id'], odds_conn)
        else:
            hcp_df = pd.DataFrame()
            tg_df = pd.DataFrame()
            for mid in match_ids_to_try:
                hcp_df = load_handicap_history(mid, odds_conn)
                if not hcp_df.empty:
                    break
            for mid in match_ids_to_try:
                tg_df = load_total_goals_history(mid, odds_conn)
                if not tg_df.empty:
                    break
        
        if not hcp_df.empty and len(hcp_df) >= 1:
            try:
                hcp_df['timestamp'] = pd.to_datetime(hcp_df['timestamp'], format='mixed')
            except Exception:
                pass
            first_hcp = hcp_df.iloc[0]
            last_hcp = hcp_df.iloc[-1]
            
            feature_dict['hcp_open_win'] = float(first_hcp['hcp_win'])
            feature_dict['hcp_open_draw'] = float(first_hcp['hcp_draw'])
            feature_dict['hcp_open_lose'] = float(first_hcp['hcp_lose'])
            
            feature_dict['hcp_close_win'] = float(last_hcp['hcp_win'])
            feature_dict['hcp_close_draw'] = float(last_hcp['hcp_draw'])
            feature_dict['hcp_close_lose'] = float(last_hcp['hcp_lose'])
            
            feature_dict['hcp_win_trend'] = float(last_hcp['hcp_win'] - first_hcp['hcp_win'])
            feature_dict['hcp_draw_trend'] = float(last_hcp['hcp_draw'] - first_hcp['hcp_draw'])
            feature_dict['hcp_lose_trend'] = float(last_hcp['hcp_lose'] - first_hcp['hcp_lose'])
            
            imp_hcp_home = calculate_implied_probability(last_hcp['hcp_win'])
            imp_hcp_draw = calculate_implied_probability(last_hcp['hcp_draw'])
            imp_hcp_away = calculate_implied_probability(last_hcp['hcp_lose'])
            total_hcp = imp_hcp_home + imp_hcp_draw + imp_hcp_away
            
            if total_hcp > 0:
                feature_dict['hcp_implied_win'] = imp_hcp_home / total_hcp
                feature_dict['hcp_implied_draw'] = imp_hcp_draw / total_hcp
                feature_dict['hcp_implied_lose'] = imp_hcp_away / total_hcp
            else:
                feature_dict['hcp_implied_win'] = 1/3
                feature_dict['hcp_implied_draw'] = 1/3
                feature_dict['hcp_implied_lose'] = 1/3
            
            feature_dict['hcp_record_count'] = len(hcp_df)

            # === D-010: 让球凯利指数（价值投注信号） ===
            feature_dict['hcp_kelly_win'] = calculate_kelly_criterion(
                feature_dict['hcp_implied_win'], float(last_hcp['hcp_win']))
            feature_dict['hcp_kelly_draw'] = calculate_kelly_criterion(
                feature_dict['hcp_implied_draw'], float(last_hcp['hcp_draw']))
            feature_dict['hcp_kelly_lose'] = calculate_kelly_criterion(
                feature_dict['hcp_implied_lose'], float(last_hcp['hcp_lose']))

            # === D-010: 让球赔率变化率（动量指标） ===
            feature_dict['hcp_win_change_rate'] = calculate_change_rate(
                float(last_hcp['hcp_win']), float(first_hcp['hcp_win']))
            feature_dict['hcp_draw_change_rate'] = calculate_change_rate(
                float(last_hcp['hcp_draw']), float(first_hcp['hcp_draw']))
            feature_dict['hcp_lose_change_rate'] = calculate_change_rate(
                float(last_hcp['hcp_lose']), float(first_hcp['hcp_lose']))
        else:
            hcp_base_features = [
                'hcp_open_win', 'hcp_open_draw', 'hcp_open_lose',
                'hcp_close_win', 'hcp_close_draw', 'hcp_close_lose',
                'hcp_win_trend', 'hcp_draw_trend', 'hcp_lose_trend',
                'hcp_implied_win', 'hcp_implied_draw', 'hcp_implied_lose',
                'hcp_record_count',
                'hcp_kelly_win', 'hcp_kelly_draw', 'hcp_kelly_lose',
                'hcp_win_change_rate', 'hcp_draw_change_rate', 'hcp_lose_change_rate',
            ]
            for f in hcp_base_features:
                feature_dict[f] = np.nan if f != 'hcp_record_count' else 0
        
        if not tg_df.empty and len(tg_df) >= 1:
            try:
                tg_df['timestamp'] = pd.to_datetime(tg_df['timestamp'], format='mixed')
            except Exception:
                pass
            last_tg = tg_df.iloc[-1]
            
            goal_probs = {}
            for g in range(8):
                col = f'goals_{g}' if g < 7 else 'goals_7_plus'
                if col in last_tg.index:
                    goal_probs[g] = float(last_tg[col])
            
            if goal_probs:
                total_prob = sum(goal_probs.values())
                if total_prob > 0:
                    for g in goal_probs:
                        goal_probs[g] /= total_prob
                
                over_25 = sum(p for g, p in goal_probs.items() if g > 2)
                under_25 = sum(p for g, p in goal_probs.items() if g <= 2)
                
                feature_dict['tg_over_25_prob'] = over_25
                feature_dict['tg_under_25_prob'] = under_25
                feature_dict['tg_most_likely'] = float(max(goal_probs, key=goal_probs.get))
                feature_dict['tg_most_likely_prob'] = float(max(goal_probs.values()))
                
                expected_goals = sum(g * p for g, p in goal_probs.items())
                feature_dict['tg_expected'] = expected_goals
            
            feature_dict['tg_record_count'] = len(tg_df)
        else:
            tg_base_features = [
                'tg_over_25_prob', 'tg_under_25_prob',
                'tg_most_likely', 'tg_most_likely_prob',
                'tg_expected', 'tg_record_count',
            ]
            for f in tg_base_features:
                feature_dict[f] = np.nan if f != 'tg_record_count' else 0
        
        feature_dict['has_wdl_odds'] = 1 if not wdl_df.empty else 0
        feature_dict['has_hcp_odds'] = 1 if not hcp_df.empty else 0
        feature_dict['has_tg_odds'] = 1 if not tg_df.empty else 0
        feature_dict['odds_coverage'] = sum([
            feature_dict['has_wdl_odds'],
            feature_dict['has_hcp_odds'],
            feature_dict['has_tg_odds']
        ])

        # === D-010: 市场信心度/确定性特征（3维） ===
        wdl_probs = [
            feature_dict.get('wdl_implied_win', 1/3),
            feature_dict.get('wdl_implied_draw', 1/3),
            feature_dict.get('wdl_implied_lose', 1/3),
        ]
        # 市场确信度：最大隐含概率（值越高市场越确信某个结果）
        feature_dict['odds_confidence'] = float(max(wdl_probs))
        # 赔率熵：市场不确定性（越高越不确定，范围0~1.099）
        entropy = -sum(p * np.log(p + 1e-10) for p in wdl_probs if p > 0)
        feature_dict['odds_entropy'] = float(np.clip(entropy, 0, 1.1))
        # 庄家利润率：赔付率-1（反映庄家抽水比例）
        feature_dict['bookmaker_margin'] = float(feature_dict.get('wdl_overround', 1.0) - 1.0)

        odds_features.append(feature_dict)
    
    # 转换为DataFrame
    odds_df = pd.DataFrame(odds_features, index=df.index)
    
    # 确保所有预期的列都存在
    expected_columns = [
        'wdl_open_win', 'wdl_open_draw', 'wdl_open_lose',
        'wdl_close_win', 'wdl_close_draw', 'wdl_close_lose',
        'wdl_win_trend', 'wdl_draw_trend', 'wdl_lose_trend',
        'wdl_implied_win', 'wdl_implied_draw', 'wdl_implied_lose',
        'wdl_overround', 'wdl_favorite', 'wdl_favorite_prob',
        'wdl_record_count',
        'wdl_kelly_win', 'wdl_kelly_draw', 'wdl_kelly_lose',
        'wdl_win_change_rate', 'wdl_draw_change_rate', 'wdl_lose_change_rate',
        'hcp_open_win', 'hcp_open_draw', 'hcp_open_lose',
        'hcp_close_win', 'hcp_close_draw', 'hcp_close_lose',
        'hcp_win_trend', 'hcp_draw_trend', 'hcp_lose_trend',
        'hcp_implied_win', 'hcp_implied_draw', 'hcp_implied_lose',
        'hcp_record_count',
        'hcp_kelly_win', 'hcp_kelly_draw', 'hcp_kelly_lose',
        'hcp_win_change_rate', 'hcp_draw_change_rate', 'hcp_lose_change_rate',
        'tg_over_25_prob', 'tg_under_25_prob',
        'tg_most_likely', 'tg_most_likely_prob',
        'tg_expected', 'tg_record_count',
        'has_wdl_odds', 'has_hcp_odds', 'has_tg_odds', 'odds_coverage',
        'odds_confidence', 'odds_entropy', 'bookmaker_margin',
    ]
    
    for col in expected_columns:
        if col not in odds_df.columns:
            odds_df[col] = np.nan

    # 缺失值处理：使用全局中位数填充（简化处理，避免groupby问题）
    for col in odds_df.columns:
        if col.startswith('has_') or col == 'odds_coverage':
            continue
        col_median = odds_df[col].median()
        odds_df[col] = odds_df[col].fillna(col_median)
    
    # 二元特征和计数特征使用0填充
    for col in ['has_wdl_odds', 'has_hcp_odds', 'has_tg_odds', 'odds_coverage',
                'wdl_record_count', 'hcp_record_count', 'tg_record_count']:
        if col in odds_df.columns:
            odds_df[col] = odds_df[col].fillna(0).astype(int)
    
    # 异常值处理
    for col in odds_df.columns:
        if col.startswith('has_') or col == 'odds_coverage':
            continue
        odds_df[col] = winsorize_series(odds_df[col], lower_percentile=1, upper_percentile=99)
    
    return odds_df


def build_odds_features_slim(df, odds_conn=None):
    """
    C-20260823-003: 精简赔率特征（~30维核心特征，砍掉~75维衍生特征）
    
    保留特征:
    - WDL/HCP 隐含概率 (6维) — 核心信号
    - 总进球概率 (2维) — tg_over_25_prob, tg_under_25_prob
    - 凯利指数 (6维) — 价值评估
    - 赔率变化率 (6维) — 市场情绪动量
    - 市场置信度 (3维) — odds_confidence, odds_entropy, bookmaker_margin
    - 覆盖度 (4维) — has_wdl/hcp/tg_odds, odds_coverage
    - 偏好 (2维) — wdl_favorite, wdl_favorite_prob
    - 总进球期望 (1维) — tg_expected
    
    删除特征 (由 build_odds_features 生成但此处不保留):
    - 原始开盘/收盘赔率 (12维) — 与隐含概率冗余
    - 趋势值 (6维) — 与变化率冗余
    - 记录数 (3维) — 无预测价值
    """
    full_odds = build_odds_features(df, odds_conn)
    
    SLIM_COLUMNS = [
        # WDL 隐含概率 (3维)
        'wdl_implied_win', 'wdl_implied_draw', 'wdl_implied_lose',
        # HCP 隐含概率 (3维)
        'hcp_implied_win', 'hcp_implied_draw', 'hcp_implied_lose',
        # 总进球概率 (2维)
        'tg_over_25_prob', 'tg_under_25_prob',
        # 凯利指数 (6维)
        'wdl_kelly_win', 'wdl_kelly_draw', 'wdl_kelly_lose',
        'hcp_kelly_win', 'hcp_kelly_draw', 'hcp_kelly_lose',
        # 赔率变化率 (6维)
        'wdl_win_change_rate', 'wdl_draw_change_rate', 'wdl_lose_change_rate',
        'hcp_win_change_rate', 'hcp_draw_change_rate', 'hcp_lose_change_rate',
        # 市场置信度 (3维)
        'odds_confidence', 'odds_entropy', 'bookmaker_margin',
        # 覆盖度 (4维)
        'has_wdl_odds', 'has_hcp_odds', 'has_tg_odds', 'odds_coverage',
        # 偏好 (2维)
        'wdl_favorite', 'wdl_favorite_prob',
        # 总进球期望 (1维)
        'tg_expected',
    ]
    
    available = [c for c in SLIM_COLUMNS if c in full_odds.columns]
    return full_odds[available]


def build_nonlinear_features(X: pd.DataFrame) -> pd.DataFrame:
    """
    生成非线性增强特征 (T-003.2)
    
    仅保留对树模型有信息增益的特征（5维）:
        B. 隐含概率熵和基尼系数 (2维): 多特征组合 → 非单调变换，有增量
        F. 跨特征交互 (3维): 隐含概率×凯利指数、主客概率比 → 非单调变换，有增量
    
    已移除的纯单调变换 (24维, A/B/C/D/E组):
        log/sqrt/inv/sq/cu 等对树模型完全等价，零信息增益，增加过拟合风险
    
    参数:
        X: 包含赔率特征的特征矩阵
    
    返回:
        pd.DataFrame: 5维非线性增强特征矩阵
    """
    EPS = 1e-6
    nl_features = pd.DataFrame(index=X.index)
    
    # === B组: 隐含概率熵和基尼系数（2维）===
    # 多特征组合 → 非单调变换，有增量信息
    if all(c in X.columns for c in ['wdl_implied_win', 'wdl_implied_draw', 'wdl_implied_lose']):
        probs = np.column_stack([
            X['wdl_implied_win'].clip(lower=EPS, upper=1),
            X['wdl_implied_draw'].clip(lower=EPS, upper=1),
            X['wdl_implied_lose'].clip(lower=EPS, upper=1),
        ])
        # 归一化确保和为1
        probs = probs / probs.sum(axis=1, keepdims=True)
        nl_features['wdl_implied_entropy'] = -np.sum(probs * np.log(probs + EPS), axis=1)
        nl_features['wdl_implied_gini'] = 1.0 - np.sum(probs ** 2, axis=1)
    
    # === F组: 跨特征交互（3维）===
    # 跨特征组合 → 非单调变换，有增量信息
    if 'wdl_implied_win' in X.columns and 'wdl_kelly_win' in X.columns:
        nl_features['wdl_imp_win_x_kelly'] = X['wdl_implied_win'] * X['wdl_kelly_win']
    if 'wdl_implied_lose' in X.columns and 'wdl_kelly_lose' in X.columns:
        nl_features['wdl_imp_lose_x_kelly'] = X['wdl_implied_lose'] * X['wdl_kelly_lose']
    if 'wdl_implied_win' in X.columns and 'wdl_implied_lose' in X.columns:
        nl_features['wdl_imp_ratio'] = X['wdl_implied_win'] / (X['wdl_implied_lose'].clip(lower=EPS))
    
    # 缺失值填充和异常值处理
    for col in nl_features.columns:
        col_median = nl_features[col].median()
        nl_features[col] = nl_features[col].fillna(col_median)
        nl_features[col] = nl_features[col].replace([np.inf, -np.inf], col_median)
        nl_features[col] = winsorize_series(nl_features[col], lower_percentile=1, upper_percentile=99)
    
    return nl_features


def build_draw_enhanced_features(X: pd.DataFrame) -> pd.DataFrame:
    """
    构建平局增强特征 (T-003.3)

    专门针对平局检测设计的特征，分为两组:

    A. 双方实力差距特征 (3维):
        - strength_closeness: 实力接近度 = exp(-|elo_diff| / 100)
          当 elo_diff=0 时为 1.0 (实力完全对等)，差距越大越接近 0
        - strength_gap_indicator: 实力接近二元指示器
          |elo_diff| < 50 时为 1.0，否则按距离线性递减到 0
        - strength_balance: 实力平衡指数 = min(elo) / max(elo)
          衡量双方 Elo 比率的平衡程度

    B. 赔率波动系数特征 (3维):
        - draw_odds_stability: 平局赔率稳定性 = 1/(1+draw_volatility)
          平局赔率波动越小，比赛越可能以平局收场
        - odds_volatility_balance: 三向波动率均衡度
          = std([win_vol, draw_vol, lose_vol])，越低表示市场对结果越不确定
        - draw_vol_relative: 平局相对波动率
          = draw_vol / (win_vol + lose_vol + EPS)，比例越低平局信号越强
    """
    EPS = 1e-6
    de_features = pd.DataFrame(index=X.index)

    # === A组: 双方实力差距特征 (3维) ===
    if 'elo_diff' in X.columns:
        abs_gap = X['elo_diff'].abs()
        de_features['strength_closeness'] = np.exp(-abs_gap / 100.0)

        de_features['strength_gap_indicator'] = np.where(
            abs_gap < 50,
            1.0,
            np.maximum(0.0, 1.0 - (abs_gap - 50) / 100.0)
        )

    if 'home_elo' in X.columns and 'away_elo' in X.columns:
        elo_min = X[['home_elo', 'away_elo']].min(axis=1)
        elo_max = X[['home_elo', 'away_elo']].max(axis=1)
        de_features['strength_balance'] = elo_min / (elo_max + EPS)

    # === B组: 赔率波动系数特征 (3维) ===
    if 'wdl_draw_volatility' in X.columns:
        de_features['draw_odds_stability'] = 1.0 / (1.0 + X['wdl_draw_volatility'])

    if all(c in X.columns for c in ['wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility']):
        vol_data = X[['wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility']]
        de_features['odds_volatility_balance'] = vol_data.std(axis=1)

        win_lose_vol = X['wdl_win_volatility'] + X['wdl_lose_volatility']
        de_features['draw_vol_relative'] = X['wdl_draw_volatility'] / (win_lose_vol + EPS)

    # 缺失值和异常值处理
    for col in de_features.columns:
        col_median = de_features[col].median()
        de_features[col] = de_features[col].fillna(col_median)
        de_features[col] = de_features[col].replace([np.inf, -np.inf], col_median)
        de_features[col] = winsorize_series(de_features[col], lower_percentile=1, upper_percentile=99)

    return de_features


def _league_zscore(feat: pd.DataFrame, leagues: pd.Series,
                   matched_mask: pd.Series) -> pd.DataFrame:
    """按联赛对特征做 z-score 归一（P1-9 联赛条件化）。

    仅对有数据（matched_mask=True）的行计算各联赛均值/标准差并归一，
    无数据行统一置 0（明确的"无数据"信号，与默认填充行为一致）。

    Args:
        feat: 特征 DataFrame（索引与 df 对齐）
        leagues: df['competition_name'] 联赛列
        matched_mask: 布尔 Series，True=该行有时序数据

    Returns:
        归一化后的特征 DataFrame（副本）
    """
    out = feat.copy()
    leagues = leagues.reindex(out.index)
    mask = matched_mask.reindex(out.index).fillna(False)
    if mask.sum() < 10:
        # 有效样本过少，跳过归一，保持原值
        return out
    lg_masked = leagues[mask]
    for c in out.columns:
        sub = out.loc[mask, c]
        grp = sub.groupby(lg_masked)
        mu = grp.transform('mean')
        sd = grp.transform('std')
        z = (sub - mu) / sd.replace(0.0, np.nan)
        out.loc[mask, c] = z.fillna(0.0).values
        out.loc[~mask, c] = 0.0
    return out


def build_all_features(df, include_odds=True, include_elo=True, include_temporal=True,
                       include_score=True, include_nonlinear=True, include_draw_enhanced=True,
                       slim_odds=True, ts_odds=False, xg_deep=False, ctx_features=False,
                       consensus_odds=False):
    """
    构建所有特征（基础特征 + 球队特征 + 赔率特征 + D-010衍生特征 + D-012 Elo特征
    + D-013时序赔率特征 + 比分赔率特征 + 非线性变换特征 + 平局增强特征）

    参数:
        df: 比赛数据DataFrame
        include_odds: 是否包含赔率特征
        slim_odds: 是否使用精简赔率特征 (C-20260823-003, ~30维 vs 全量~54维)
        include_elo: 是否包含Elo评分特征 (D-012)
        include_temporal: 是否包含时序赔率变化速率特征 (D-013)
        include_score: 是否包含比分赔率特征 (T-003.1)
        include_nonlinear: 是否包含非线性变换特征 (T-003.2)
        include_draw_enhanced: 是否包含平局增强特征 (T-003.3)
        ts_odds: P1-9 时序赔率开关（默认 False 保证可回退 A/B）。True 时在
            slim_odds 精简模式下仍接入 D-013 时序赔率（22维，含跨盘口一致性+
            去水概率漂移）与 T-003.1 比分赔率（8维），并对时序特征做联赛
            z-score 归一（联赛条件化，不拆 5 套模型）
        xg_deep: P1-8 xG 深度特征开关（默认 False 保证可回退 A/B）。True 时接入
            xg_diff_recent / xg_diff_trend / xg_diff_pct 主客各 3 维（共 6 维）
        ctx_features: P1-10 情境化特征开关（默认 False 保证可回退 A/B）。True 时接入
            情境特征（赛程密度/休息天数/主客连续作战/积分排名压力/德比/新军经验，
            共 14 维）
        consensus_odds: P1-11 多博彩公司赔率一致性开关（默认 False 保证可回退 A/B）。
            True 时接入百家欧指共识概率（3维）+ 竞彩-共识偏离度（3维）+ 总偏离度+
            庄家分歧度+覆盖庄家数+市场抽水（共 10 维）

    返回:
        X: 特征矩阵
        y: 标签（胜/平/负）
    """
    basic_features = build_features(df)
    team_features = build_team_features(df)

    team_features = team_features.drop(columns=[col for col in team_features.columns if col in basic_features.columns])

    X = pd.concat([basic_features, team_features], axis=1)

    # === D-012: Elo Rating 特征（10维）===
    if include_elo:
        try:
            from elo_rating import build_elo_features
            print(f"   构建 D-012 Elo Rating 特征...")
            elo_feature = build_elo_features(df)
            print(f"   [OK] D-012 Elo特征维度: {elo_feature.shape[1]} (shape={elo_feature.shape})")
            if elo_feature.shape[1] > 0:
                X = pd.concat([X, elo_feature], axis=1)
            else:
                print(f"   [WARN]  Elo特征为0维")
        except Exception as e:
            import traceback
            print(f"   警告: 构建Elo特征失败 - {e}")
            traceback.print_exc()

    # 集成赔率特征
    if include_odds:
        try:
            if slim_odds:
                odds_feature = build_odds_features_slim(df)
                print(f"   赔率特征维度 (slim): {odds_feature.shape[1]} (shape={odds_feature.shape})")
            else:
                odds_feature = build_odds_features(df)
                print(f"   赔率特征维度: {odds_feature.shape[1]} (shape={odds_feature.shape})")
            if odds_feature.shape[1] > 0:
                X = pd.concat([X, odds_feature], axis=1)
            else:
                print(f"   [WARN] 赔率特征为0维，检查match_id匹配...")
                if 'match_id' in df.columns:
                    print(f"   match_id样本: {df['match_id'].iloc[:3].tolist()}")
        except Exception as e:
            import traceback
            print(f"   警告: 构建赔率特征失败 - {e}")
            traceback.print_exc()

        # === D-010: 价值投注信号（2维）===
        # 隐含概率 vs 球队历史胜率的差异，正值表示赔率被高估（有价值）
        # 使用 pd.concat 一次性添加，避免 DataFrame 碎片化
        bet_cols = {}
        if 'wdl_implied_win' in X.columns and 'home_win_rate' in X.columns:
            bet_cols['value_bet_home'] = X['wdl_implied_win'] - X['home_win_rate']
        if 'wdl_implied_lose' in X.columns and 'away_win_rate' in X.columns:
            bet_cols['value_bet_away'] = X['wdl_implied_lose'] - X['away_win_rate']
        if bet_cols:
            X = pd.concat([X, pd.DataFrame(bet_cols, index=X.index)], axis=1)
            print(f"   [OK] D-010 价值投注信号: +2维 (value_bet_home, value_bet_away)")

    # === D-013: 时序赔率特征（22维，P1-9 扩展）===
    # slim_odds 模式下默认跳过；ts_odds=True 时接入（含跨盘口一致性+去水概率漂移）
    if include_temporal and (not slim_odds or ts_odds):
        try:
            from d013_temporal_odds import build_d013_features
            print(f"   构建 D-013 时序赔率特征 (ts_odds={ts_odds})...")
            temporal_feature = build_d013_features(df)
            print(f"   [OK] D-013 时序赔率特征维度: {temporal_feature.shape[1]} (shape={temporal_feature.shape})")
            if temporal_feature.shape[1] > 0:
                # 内部指标列：联赛 z-score 时用于屏蔽无数据行
                if '_ts_matched' in temporal_feature.columns:
                    ts_matched_mask = temporal_feature.pop('_ts_matched') > 0
                else:
                    ts_matched_mask = None
                # 联赛条件化（P1-9 步骤5）：时序特征按联赛 z-score 归一
                if ts_odds and ts_matched_mask is not None:
                    leagues = df['competition_name']
                    temporal_feature = _league_zscore(temporal_feature, leagues, ts_matched_mask)
                    print(f"   [OK] D-013 时序特征已按联赛 z-score 归一")
                X = pd.concat([X, temporal_feature], axis=1)
            else:
                print(f"   [WARN]  D-013时序赔率特征为0维")
        except Exception as e:
            import traceback
            print(f"   警告: 构建D-013时序赔率特征失败 - {e}")
            traceback.print_exc()

    # === T-003.1: 比分赔率特征（8维）===
    # slim_odds 模式下默认跳过；ts_odds=True 时接入（P1-9）
    if include_score and (not slim_odds or ts_odds):
        try:
            from score_features import build_score_features
            print(f"   构建比分赔率特征 (T-003.1)...")
            score_feature = build_score_features(df)
            print(f"   [OK] 比分赔率特征维度: {score_feature.shape[1]} (shape={score_feature.shape})")
            if score_feature.shape[1] > 0:
                X = pd.concat([X, score_feature], axis=1)
            else:
                print(f"   [WARN]  比分赔率特征为0维")
        except Exception as e:
            import traceback
            print(f"   警告: 构建比分赔率特征失败 - {e}")
            traceback.print_exc()

    # === T-003.2: 非线性变换特征（29维）===
    # slim_odds 模式下跳过（赔率衍生特征，拟合噪声）
    if include_nonlinear and not slim_odds:
        try:
            print(f"   构建非线性变换特征 (T-003.2)...")
            nonlinear_feature = build_nonlinear_features(X)
            print(f"   [OK] 非线性变换特征维度: {nonlinear_feature.shape[1]} (shape={nonlinear_feature.shape})")
            if nonlinear_feature.shape[1] > 0:
                X = pd.concat([X, nonlinear_feature], axis=1)
            else:
                print(f"   [WARN]  非线性变换特征为0维")
        except Exception as e:
            import traceback
            print(f"   警告: 构建非线性变换特征失败 - {e}")
            traceback.print_exc()

    # === T-003.3: 平局增强特征（6维）===
    # 依赖 Elo 特征和时序赔率特征，必须在它们之后构建
    if include_draw_enhanced:
        try:
            print(f"   构建平局增强特征 (T-003.3)...")
            draw_enhanced = build_draw_enhanced_features(X)
            print(f"   [OK] 平局增强特征维度: {draw_enhanced.shape[1]} (shape={draw_enhanced.shape})")
            if draw_enhanced.shape[1] > 0:
                X = pd.concat([X, draw_enhanced], axis=1)
        except Exception as e:
            import traceback
            print(f"   警告: 构建平局增强特征失败 - {e}")
            traceback.print_exc()

    # === T-007: SofaScore 球员级特征（44维）===
    # 从 sofascore_team_features 表加载，按 date + 球队名合并
    try:
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
        conn = connect(db_path=db_path)
        if table_exists(conn, 'sofascore_team_features'):
            sofa_df = read_sql("SELECT * FROM sofascore_team_features", conn)
            conn.close()
            print(f"   [OK] T-007 球员特征: {sofa_df.shape[1]} 维 (shape={sofa_df.shape})")
            
            # 只保留特征列（去掉 ID/元数据列），P0-3: 含 pa_ 前缀球员可用性特征
            sofa_feature_cols = [c for c in sofa_df.columns 
                                 if c.startswith('sofa_') or c.startswith('pa_')]
            
            # 标准化 sofa 的球队名（与 df 保持一致）
            sofa_df['home_norm'] = sofa_df['home_team_cn'].apply(normalize_team_name)
            sofa_df['away_norm'] = sofa_df['away_team_cn'].apply(normalize_team_name)
            sofa_df['match_date_dt'] = pd.to_datetime(sofa_df['match_date'], format='mixed')
            
            # 按 date + home_team + away_team 合并
            # 构建 merge key
            df_merge = df[['date', 'home_team_name', 'away_team_name']].copy()
            df_merge['_idx'] = range(len(df_merge))
            
            sofa_merge = sofa_df[['match_date_dt', 'home_norm', 'away_norm'] + sofa_feature_cols].copy()
            sofa_merge = sofa_merge.rename(columns={
                'match_date_dt': 'date',
                'home_norm': 'home_team_name',
                'away_norm': 'away_team_name'
            })
            
            merged = df_merge.merge(sofa_merge, on=['date', 'home_team_name', 'away_team_name'], how='left')
            merged = merged.sort_values('_idx').reset_index(drop=True)
            
            # 统计覆盖率
            non_null_mask = merged[sofa_feature_cols[0]].notna() if sofa_feature_cols else pd.Series([False]*len(merged))
            coverage = non_null_mask.sum() / len(merged) * 100 if len(merged) > 0 else 0
            print(f"   球员特征覆盖率: {coverage:.1f}% ({non_null_mask.sum()}/{len(merged)})")
            
            # 将特征列加入 X（使用 pd.concat 一次性添加，避免 DataFrame 碎片化）
            sofa_extra = merged[sofa_feature_cols].fillna(-1.0)
            sofa_extra.index = X.index
            X = pd.concat([X, sofa_extra], axis=1)
            
            print(f"   合并后特征维度: {X.shape[1]}")
        else:
            conn.close()
            print(f"   [WARN]  sofascore_team_features 表不存在，跳过球员特征")
    except Exception as e:
        import traceback
        print(f"   警告: 加载球员特征失败 - {e}")
        traceback.print_exc()

    # === P1-8: xG 差值趋势 + 联赛分位特征（6维）===
    if xg_deep:
        try:
            from xg_deep_features import build_xg_deep_features
            print(f"   构建 xG 深度特征 (xg_deep={xg_deep})...")
            xg_feat = build_xg_deep_features(df)
            print(f"   [OK] xG 深度特征维度: {xg_feat.shape[1]} (shape={xg_feat.shape})")
            if xg_feat.shape[1] > 0:
                X = pd.concat([X, xg_feat], axis=1)
            else:
                print(f"   [WARN]  xG 深度特征为0维")
        except Exception as e:
            import traceback
            print(f"   警告: 构建 xG 深度特征失败 - {e}")
            traceback.print_exc()

    # === P1-10: 情境化特征（14维，赛程密度/休息天数/连续作战/积分压力/德比/新军经验）===
    if ctx_features:
        try:
            from contextual_features import build_contextual_features
            print(f"   构建 P1-10 情境化特征 (ctx_features={ctx_features})...")
            ctx_feat = build_contextual_features(df)
            print(f"   [OK] 情境化特征维度: {ctx_feat.shape[1]} (shape={ctx_feat.shape})")
            if ctx_feat.shape[1] > 0:
                X = pd.concat([X, ctx_feat], axis=1)
            else:
                print(f"   [WARN]  情境化特征为0维")
        except Exception as e:
            import traceback
            print(f"   警告: 构建情境化特征失败 - {e}")
            traceback.print_exc()

    # === P1-11: 多博彩公司赔率一致性（10维，竞彩 vs 百家欧指共识偏离度）===
    if consensus_odds:
        try:
            from odds_consensus_features import build_odds_consensus_features
            print(f"   构建 P1-11 赔率一致性特征 (consensus_odds={consensus_odds})...")
            cons_feat = build_odds_consensus_features(df, X)
            print(f"   [OK] 赔率一致性特征维度: {cons_feat.shape[1]} (shape={cons_feat.shape})")
            if cons_feat.shape[1] > 0:
                X = pd.concat([X, cons_feat], axis=1)
            else:
                print(f"   [WARN]  赔率一致性特征为0维")
        except Exception as e:
            import traceback
            print(f"   警告: 构建赔率一致性特征失败 - {e}")
            traceback.print_exc()

    y = df['result']

    return X, y


def build_match_alignment(conn, history_table: str) -> dict:
    """构建赔率历史表 → matches 表的三通道对齐映射。

    赔率历史表（wdl_history/handicap_history/total_goals_history/score_history）
    的 match_id 主要为中文格式（date_中文主_中文客）。matches 表的 match_id
    在 16/17~22/23 赛季同为中文格式、23/24 起转为英文格式（date_英文主_英文客），
    单一通道无法全覆盖。本函数按优先级用三条通道解析对齐：

      通道0（直接，最高优先）: history.match_id == matches.match_id（覆盖中文赛季，占 ~71%）
      通道1（桥表）: match_id_mapping (sh_match_id → matches_match_id)
      通道2（兜底）: history 表自带 match_id_en 直接列（补齐英文赛季桥表未覆盖场次）

    Args:
        conn: sqlite3 连接
        history_table: 赔率历史表名

    Returns:
        dict: {history_match_id: matches_match_id}，未对齐项不出现
    """
    valid_tables = ("wdl_history", "handicap_history",
                    "total_goals_history", "score_history")
    if history_table not in valid_tables:
        raise ValueError(f"不支持的赔率历史表: {history_table}")

    # ---- 通道0 备选集合：一次性加载 matches.match_id（避免对历史大表做 JOIN）----
    matches_ids = set()
    for (mid,) in conn.execute("SELECT match_id FROM matches").fetchall():
        if mid is not None:
            matches_ids.add(mid)

    # ---- 通道1 备选：match_id_mapping 桥表全量加载（小表）----
    bridge = {}
    try:
        for sh, en in conn.execute(
            "SELECT sh_match_id, matches_match_id FROM match_id_mapping"
        ).fetchall():
            if sh is not None and en is not None:
                bridge[sh] = en
    except Exception:  # noqa: BLE001
        bridge = {}

    # ---- 通道2 探测：history 表是否带 match_id_en 列（SQLite/PG 双后端一致）----
    desc = conn.execute(f"SELECT * FROM {history_table} LIMIT 0").description or []
    has_en = any(d[0] == "match_id_en" for d in desc)

    mapping = {}
    if has_en:
        rows = conn.execute(
            f"SELECT DISTINCT match_id, match_id_en FROM {history_table}"
        ).fetchall()
        for sh, en in rows:
            if sh is None:
                continue
            if sh in matches_ids:
                mapping[sh] = sh          # 通道0 直接对齐（最高优先）
                continue
            en1 = bridge.get(sh)
            if en1 is not None and en1 in matches_ids:
                mapping[sh] = en1         # 通道1 桥表（次优先）
                continue
            if en is not None and en in matches_ids:
                mapping[sh] = en          # 通道2 match_id_en 兜底（最低优先）
    else:
        rows = conn.execute(
            f"SELECT DISTINCT match_id FROM {history_table}"
        ).fetchall()
        for (sh,) in rows:
            if sh is None:
                continue
            if sh in matches_ids:
                mapping[sh] = sh
                continue
            en1 = bridge.get(sh)
            if en1 is not None and en1 in matches_ids:
                mapping[sh] = en1

    return mapping
