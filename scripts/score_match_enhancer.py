"""
score_history 增强匹配模块 (T-006 准备工作)
解决 score_history(中文) 与 matches(英文) match_id 格式不一致问题
预期效果: 覆盖率从 4.6% 提升至 >= 70%

⚠️ 虚假值风险说明: 模糊匹配可能导致将A队的赔率映射到B队（假阳性）。
建议在生产环境中使用 confidence >= 0.85 的高置信度映射。
低置信度映射仅用于探索性分析。
"""

import sqlite3
import pandas as pd
import numpy as np
import re
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feature_utils import TEAM_NAME_MAP, normalize_team_name

DB_PATH = BASE_DIR / "data" / "odds.db"


def classify_match_confidence(match_method, similarity_score=0.0, day_diff=0):
    """
    根据匹配方法、相似度和日期偏差计算置信度等级。

    ⚠️ 虚假值风险说明: 模糊匹配可能导致将A队的赔率映射到B队（假阳性）。
    建议在生产环境中使用 confidence >= 0.85 的高置信度映射。
    低置信度映射仅用于探索性分析。

    置信度等级:
      1.00 - 直接精确匹配（英文match_id完全一致），无虚假值风险
      0.95 - 中文->英文精确球队名映射，极低风险
      0.85 - 日期容差 ±1天 + 精确球队映射，低风险
      0.80 - 精确子串匹配（一方名字包含另一方），较低风险
      0.75 - 模糊匹配相似度 >= 0.75，中等风险
      0.60 - 模糊匹配相似度 0.60-0.74，较高风险，需谨慎
      0.50 - 日期容差 ±2天匹配，高风险，仅供探索性分析
    """
    if match_method == 'direct':
        return 1.0
    elif match_method == 'cn_to_en':
        return 0.95
    elif match_method == 'date_tolerance':
        if day_diff <= 1:
            return 0.85
        else:
            return 0.50
    elif match_method == 'substring':
        return 0.80
    elif match_method == 'fuzzy':
        if similarity_score >= 0.75:
            return 0.75
        else:
            return 0.60
    elif match_method == 'fuzzy_date':
        if similarity_score >= 0.75:
            return 0.75 if day_diff <= 1 else 0.50
        else:
            return 0.60 if day_diff <= 1 else 0.50
    return 0.50



def build_enhanced_cn_to_en():
    """构建增强的 中文->英文 映射表
    
    关键修复: TEAM_NAME_MAP 中存在中文->中文变体映射 (如 '曼城' -> '曼彻斯特城'),
    反转后会产生错误的中文->中文映射, 覆盖正确的中文->英文映射。
    修复方式: 跳过 en_name 包含中文字符的条目。
    """
    cn_to_en = {}

    for en_name, cn_name in TEAM_NAME_MAP.items():

        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', en_name))
        if has_chinese:
            continue
        cn_to_en[cn_name] = en_name
        norm_cn = normalize_team_name(cn_name)
        if norm_cn != cn_name and norm_cn not in cn_to_en:
            cn_to_en[norm_cn] = en_name

    supplements = {
        '切尔西': 'Chelsea',
        '马德里竞技': 'Atlético Madrid',
        '巴塞罗那': 'Barcelona',
        '加的斯': 'Cadiz',
        '莱万特': 'Levante UD',
        '格拉纳达CF': 'Granada',
        '埃尔切': 'Elche',
        '巴利亚多利德': 'Real Valladolid',
        '佛罗伦萨': 'Fiorentina',
        '博洛尼亚': 'Bologna',
        '蒙扎': 'Monza',
        '克雷莫纳': 'Cremonese',
        '恩波利': 'Empoli',
        '莱切': 'Lecce',
        '萨勒尼塔纳': 'Salernitana',
        '弗洛西诺内': 'Frosinone',
        '热那亚': 'Genoa',
        '科莫': 'Como',
        '帕尔马': 'Parma',
        '比萨': 'Pisa',
        '萨索洛': 'Sassuolo',
        '威尼斯': 'Venezia',
        '莱比锡红牛': 'RB Leipzig',
        '勒沃库森': 'Bayer Leverkusen',
        '法兰克福': 'Eintracht Frankfurt',
        '斯图加特': 'VfB Stuttgart',
        '门兴格拉德巴赫': 'Borussia Mönchengladbach',
        '奥格斯堡': 'Augsburg',
        '沃尔夫斯堡': 'VfL Wolfsburg',
        '美因茨': 'Mainz 05',
        '弗赖堡': 'SC Freiburg',
        '柏林联合': 'Union Berlin',
        '科隆': 'FC Köln',
        '海登海姆': 'Heidenheim',
        '霍芬海姆': 'Hoffenheim',
        '波鸿': 'VfL Bochum 1848',
        '达姆施塔特': 'Darmstadt 98',
        '基尔': 'Holstein Kiel',
        '圣保利': 'St. Pauli',
        '汉堡': 'Hamburger SV',
        '欧塞尔': 'Auxerre',
        '南特': 'Nantes',
        '勒阿弗尔': 'Le Havre',
        '蒙彼利埃': 'Montpellier',
        '梅斯': 'Metz',
        '巴黎FC': 'Paris FC',
        '朗斯': 'Lens',
        '兰斯': 'Reims',
        '布雷斯特': 'Brest',
        '斯特拉斯堡': 'Strasbourg',
        '图卢兹': 'Toulouse',
        '圣埃蒂安': 'Saint-Étienne',
        '昂热': 'Angers',
        '洛里昂': 'Lorient',
        '克莱蒙': 'Clermont',
        '摩纳哥': 'Monaco',
        '里昂': 'Lyon',
        '谢菲尔德联': 'Sheffield United',
        '卢顿': 'Luton Town',
        '伊普斯维奇': 'Ipswich Town',
        '南安普敦': 'Southampton',
        '利兹联': 'Leeds United',
        '莱切斯特城': 'Leicester City',
        '桑德兰': 'Sunderland',
        '敦刻尔克': 'Dunkerque',
        '罗德兹': 'Rodez',
        '杜塞尔多夫': 'Düsseldorf',
        '埃沃斯堡': 'Elversberg',
    }
    for cn, en in supplements.items():
        cn_to_en[cn] = en

    aliases = {
        '马略卡': 'Mallorca',
        '马洛卡': 'Mallorca',
        '曼城': 'Manchester City',
        '曼联': 'Manchester United',
        '热刺': 'Tottenham Hotspur',
        '纽卡斯尔': 'Newcastle United',
        '西汉姆': 'West Ham United',
        '布莱顿': 'Brighton & Hove Albion',
        '布赖顿': 'Brighton & Hove Albion',
        '水晶宫': 'Crystal Palace',
        '伯恩茅斯': 'Bournemouth',
        '伯恩利': 'Burnley',
        '狼队': 'Wolverhampton Wanderers',
        '埃弗顿': 'Everton',
        '诺丁汉森林': 'Nottingham Forest',
        '阿森纳': 'Arsenal',
        '利物浦': 'Liverpool',
        '布伦特福德': 'Brentford',
        '富勒姆': 'Fulham',
        '阿斯顿维拉': 'Aston Villa',
        '巴伦西亚': 'Valencia',
        '毕尔巴鄂': 'Athletic Club',
        '塞尔塔': 'Celta Vigo',
        '格拉纳达': 'Granada',
        '巴黎圣日耳曼': 'Paris Saint-Germain',
        '巴黎圣日尔曼': 'Paris Saint-Germain',
        '云达不莱梅': 'Werder Bremen',
        '云达不来梅': 'Werder Bremen',
        '多特蒙德': 'Borussia Dortmund',
        '拜仁慕尼黑': 'Bayern Munich',
        '国际米兰': 'Inter Milan',
        'AC米兰': 'AC Milan',
        '那不勒斯': 'Napoli',
        '罗马': 'Roma',
        '拉齐奥': 'Lazio',
        '亚特兰大': 'Atalanta',
        '都灵': 'Torino',
        '乌迪内斯': 'Udinese',
        '维罗纳': 'Hellas Verona',
        '卡利亚里': 'Cagliari',
    }
    for cn, en in aliases.items():
        cn_to_en[cn] = en

    new_aliases = {
        '维戈塞尔塔': 'Celta Vigo',
        '曼彻斯特城': 'Manchester City',
        '曼彻斯特联': 'Manchester United',
        '阿拉维斯': 'Deportivo Alavés',
        '巴列卡诺': 'Rayo Vallecano',
        '拉斯帕尔马斯': 'UD Las Palmas',
        '加的斯': 'Cádiz',
        '埃尔切': 'Elche',
        '巴利亚多利德': 'Real Valladolid',
        '莱万特': 'Levante UD',
        '伊普斯维奇': 'Ipswich Town',
        '南安普敦': 'Southampton',
        '利兹联': 'Leeds United',
        '莱切斯特城': 'Leicester City',
        '桑德兰': 'Sunderland',
        '敦刻尔克': 'Dunkerque',
        '罗德兹': 'Rodez',
        '杜塞尔多夫': 'Düsseldorf',
        '埃沃斯堡': 'Elversberg',
        '海登海姆': 'Heidenheim',
        '博洛尼亚': 'Bologna',
        '乌迪内斯': 'Udinese',
        '维罗纳': 'Hellas Verona',
        '卡利亚里': 'Cagliari',
        '热那亚': 'Genoa',
        '恩波利': 'Empoli',
        '莱切': 'Lecce',
        '萨勒尼塔纳': 'Salernitana',
        '威尼斯': 'Venezia',
        '克雷莫纳': 'Cremonese',
        '科莫': 'Como',
        '比萨': 'Pisa',
        '萨索洛': 'Sassuolo',
        '帕尔马': 'Parma',
        '弗洛西诺内': 'Frosinone',
        '斯图加特': 'VfB Stuttgart',
        '法兰克福': 'Eintracht Frankfurt',
        '门兴格拉德巴赫': 'Borussia Mönchengladbach',
        '奥格斯堡': 'Augsburg',
        '沃尔夫斯堡': 'VfL Wolfsburg',
        '美因茨': 'Mainz 05',
        '弗赖堡': 'SC Freiburg',
        '柏林联合': 'Union Berlin',
        '科隆': 'FC Köln',
        '波鸿': 'VfL Bochum 1848',
        '达姆施塔特': 'Darmstadt 98',
        '基尔': 'Holstein Kiel',
        '圣保利': 'St. Pauli',
        '汉堡': 'Hamburger SV',
        '霍芬海姆': 'Hoffenheim',
        '西汉姆联': 'West Ham United',
        '蒙扎': 'Monza',
        '布雷斯特': 'Brest',
        '斯特拉斯堡': 'Strasbourg',
        '雷恩': 'Rennes',
        '赫罗纳': 'Girona FC',
        '水晶宫': 'Crystal Palace',
        '埃弗顿': 'Everton',
        '富勒姆': 'Fulham',
        '伯恩茅斯': 'Bournemouth',
        '布伦特福德': 'Brentford',
        '托特纳姆热刺': 'Tottenham Hotspur',
        '阿斯顿维拉': 'Aston Villa',
        '皇家马德里': 'Real Madrid',
        '塞维利亚': 'Sevilla',
        '皇家社会': 'Real Sociedad',
        '比利亚雷亚尔': 'Villarreal',
        '皇家贝蒂斯': 'Real Betis',
        '马德里竞技': 'Atlético Madrid',
        '巴塞罗那': 'Barcelona',
        '毕尔巴鄂竞技': 'Athletic Club',
        '奥萨苏纳': 'Osasuna',
        '赫塔费': 'Getafe',
        '格拉纳达CF': 'Granada',
        '马洛卡': 'Mallorca',
        '瓦伦西亚': 'Valencia',
        '国际米兰': 'Inter Milan',
        'AC米兰': 'AC Milan',
        '那不勒斯': 'Napoli',
        '罗马': 'Roma',
        '拉齐奥': 'Lazio',
        '亚特兰大': 'Atalanta',
        '都灵': 'Torino',
        '拜仁慕尼黑': 'Bayern Munich',
        '多特蒙德': 'Borussia Dortmund',
        '勒沃库森': 'Bayer Leverkusen',
        '莱比锡红牛': 'RB Leipzig',
        '阿森纳': 'Arsenal',
        '利物浦': 'Liverpool',
        '切尔西': 'Chelsea',
        '布赖顿': 'Brighton & Hove Albion',
        '曼城': 'Manchester City',
        '曼联': 'Manchester United',
        '热刺': 'Tottenham Hotspur',
        '纽卡斯尔': 'Newcastle United',
        '水晶宫': 'Crystal Palace',
        '狼队': 'Wolverhampton Wanderers',
        '诺丁汉森林': 'Nottingham Forest',
        '巴黎圣日耳曼': 'Paris Saint-Germain',
        '巴黎圣日尔曼': 'Paris Saint-Germain',
        '云达不莱梅': 'Werder Bremen',
        '云达不来梅': 'Werder Bremen',
        '佛罗伦萨': 'Fiorentina',
        '海登海姆': 'Heidenheim',
        '摩纳哥': 'Monaco',
        '里昂': 'Lyon',
        '图卢兹': 'Toulouse',
        '南特': 'Nantes',
        '勒阿弗尔': 'Le Havre',
        '蒙彼利埃': 'Montpellier',
        '梅斯': 'Metz',
        '朗斯': 'Lens',
        '兰斯': 'Reims',
        '欧塞尔': 'Auxerre',
        '圣埃蒂安': 'Saint-Étienne',
        '昂热': 'Angers',
        '洛里昂': 'Lorient',
        '克莱蒙': 'Clermont',
        '巴黎FC': 'Paris FC',
    }
    for cn, en in new_aliases.items():
        if cn not in cn_to_en:
            cn_to_en[cn] = en

    # 标准化: 将通用英文名映射为 matches 表中的实际队名格式
    # 这是提升覆盖率的关键 - matches 表使用特定的队名格式
    _MATCHES_NAME_NORMALIZE = {
        'Liverpool': 'Liverpool FC',
        'Wolverhampton Wanderers': 'Wolverhampton',
        'Bayern Munich': 'FC Bayern München',
        'Inter Milan': 'Inter',
        'Napoli': 'SSC Napoli',
        'Roma': 'AS Roma',
        'Heidenheim': '1. FC Heidenheim',
        'FC Köln': '1. FC Köln',
        'Union Berlin': '1. FC Union Berlin',
        'Mainz 05': '1. FSV Mainz 05',
        'St. Pauli': 'FC St. Pauli',
        'Borussia Mönchengladbach': "Borussia M'gladbach",
        'Bayer Leverkusen': 'Bayer 04 Leverkusen',
        'Barcelona': 'FC Barcelona',
        'Lyon': 'Olympique Lyonnais',
        'Marseille': 'Olympique de Marseille',
        'Monaco': 'AS Monaco',
        'Rennes': 'Stade Rennais',
        'Lens': 'RC Lens',
        'Reims': 'Stade de Reims',
        'Brest': 'Stade Brestois',
        'Freiburg': 'SC Freiburg',
        'Frankfurt': 'Eintracht Frankfurt',
        'Wolfsburg': 'VfL Wolfsburg',
        'Bochum': 'VfL Bochum 1848',
        'Stuttgart': 'VfB Stuttgart',
        'Augsburg': 'FC Augsburg',
        'Hoffenheim': 'TSG Hoffenheim',
        'Leipzig': 'RB Leipzig',
        'Sevilla': 'Sevilla',
        'Alavés': 'Deportivo Alavés',
        'Vallecano': 'Rayo Vallecano',
        'Lille': 'Lille',
        'Werder Bremen': 'SV Werder Bremen',
        'Clermont': 'Clermont Foot',
        'Darmstadt 98': 'Darmstadt 98',
        'Köln': '1. FC Köln',
    }
    for cn_key, en_val in cn_to_en.items():
        if en_val in _MATCHES_NAME_NORMALIZE:
            cn_to_en[cn_key] = _MATCHES_NAME_NORMALIZE[en_val]

    return cn_to_en



def _edit_distance(s1, s2):
    """计算编辑距离"""
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i-1] == s2[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])
    return dp[m][n]


def _similarity(s1, s2):
    """计算字符串相似度"""
    if not s1 or not s2:
        return 0.0
    dist = _edit_distance(s1.lower(), s2.lower())
    return 1.0 - dist / max(len(s1), len(s2))


def fuzzy_match_team(query, candidates, threshold=0.60):
    """模糊匹配球队名 (阈值从0.55提高到0.60以减少假阳性)"""
    if not query or not candidates:
        return None
    best_match = None
    best_score = threshold
    for candidate in candidates:
        score = _similarity(query, candidate)
        if score > best_score:
            best_score = score
            best_match = candidate
    return best_match


def detect_match_id_format(match_id):
    """检测 match_id 格式（中文/英文）"""
    if not match_id or '_' not in match_id:
        return 'unknown'
    parts = match_id.split('_')
    if len(parts) < 3:
        return 'unknown'
    home, away = parts[1], parts[2]
    has_chinese = bool(re.search(r'[\u4e00-\u9fff]', home)) or bool(re.search(r'[\u4e00-\u9fff]', away))
    return 'chinese' if has_chinese else 'english'


def _exact_substring_match(home_raw, away_raw, all_match_teams):
    """
    精确子串匹配: 检查中文队名是否是英文队名的子串（或反之）。
    返回 (候选match_id, 相似度) 或 (None, 0.0)
    """
    if not home_raw or not away_raw:
        return None, 0.0
    home_lower = home_raw.lower()
    away_lower = away_raw.lower()
    for m_id, (m_home, m_away) in all_match_teams.items():
        mh_lower = m_home.lower()
        ma_lower = m_away.lower()
        if home_lower == mh_lower and away_lower == ma_lower:
            return m_id, 1.0
        if home_lower == ma_lower and away_lower == mh_lower:
            return m_id, 1.0
        if (mh_lower in home_lower or home_lower in mh_lower) and \
           (ma_lower in away_lower or away_lower in ma_lower):
            return m_id, 0.85
        if (mh_lower in away_lower or away_lower in mh_lower) and \
           (ma_lower in home_lower or home_lower in ma_lower):
            return m_id, 0.85
    return None, 0.0



def build_match_id_mapping(score_history_ids, matches_ids, cn_to_en):
    """
    构建 score_history -> matches 的 match_id 映射。

    返回:
        mapping: {sh_id: m_id}
        confidence_map: {sh_id: confidence}
        method_map: {sh_id: method_name}
    """
    mapping = {}
    confidence_map = {}
    method_map = {}

    matches_by_date = defaultdict(list)
    for mid in matches_ids:
        parts = mid.split('_')
        if len(parts) >= 3:
            matches_by_date[parts[0]].append(mid)

    all_match_teams = {}
    for mid in matches_ids:
        parts = mid.split('_')
        if len(parts) >= 3:
            all_match_teams[mid] = (parts[1], parts[2])

    # 1. 直接匹配（英文格式）
    direct_count = 0
    for sh_id in score_history_ids:
        if sh_id in matches_ids:
            mapping[sh_id] = sh_id
            confidence_map[sh_id] = classify_match_confidence('direct')
            method_map[sh_id] = 'direct'
            direct_count += 1

    # 2. 中文->英文精确映射
    cn_count = 0
    for sh_id in score_history_ids:
        if sh_id in mapping:
            continue
        parts = sh_id.split('_')
        if len(parts) < 3:
            continue
        date, home_raw, away_raw = parts[0], parts[1], parts[2]
        fmt = detect_match_id_format(sh_id)
        if fmt == 'chinese':
            home_en = cn_to_en.get(home_raw)
            away_en = cn_to_en.get(away_raw)
            if not home_en or not away_en:
                home_norm = normalize_team_name(home_raw)
                away_norm = normalize_team_name(away_raw)
                home_en = home_en or cn_to_en.get(home_norm)
                away_en = away_en or cn_to_en.get(away_norm)
            if home_en and away_en:
                candidates = [
                    f"{date}_{home_en}_{away_en}",
                    f"{date}_{away_en}_{home_en}",
                ]
                for cand in candidates:
                    if cand in matches_ids:
                        mapping[sh_id] = cand
                        confidence_map[sh_id] = classify_match_confidence('cn_to_en')
                        method_map[sh_id] = 'cn_to_en'
                        cn_count += 1
                        break

    # 3. 日期容差精确匹配 (±2天)
    date_tolerance_count = 0
    for sh_id in score_history_ids:
        if sh_id in mapping:
            continue
        parts = sh_id.split('_')
        if len(parts) < 3:
            continue
        date, home_raw, away_raw = parts[0], parts[1], parts[2]
        home_en = cn_to_en.get(home_raw)
        away_en = cn_to_en.get(away_raw)

        try:
            dt = datetime.strptime(date, '%Y-%m-%d')
            date_candidates = []
            for delta in [0, 1, -1, 2, -2]:
                d = (dt + timedelta(days=delta)).strftime('%Y-%m-%d')
                date_candidates.append((d, abs(delta)))
        except Exception:
            date_candidates = [(date, 0)]

        if home_en and away_en:
            for d, day_diff in date_candidates:
                if sh_id in mapping:
                    break
                candidates = [
                    f"{d}_{home_en}_{away_en}",
                    f"{d}_{away_en}_{home_en}",
                ]
                for cand in candidates:
                    if cand in matches_ids and cand not in mapping.values():
                        mapping[sh_id] = cand
                        confidence_map[sh_id] = classify_match_confidence(
                            'date_tolerance', day_diff=day_diff)
                        method_map[sh_id] = 'date_tolerance'
                        date_tolerance_count += 1
                        break

    # 4. 精确子串匹配（针对无cn_to_en映射的记录）
    substring_count = 0
    for sh_id in score_history_ids:
        if sh_id in mapping:
            continue
        parts = sh_id.split('_')
        if len(parts) < 3:
            continue
        date, home_raw, away_raw = parts[0], parts[1], parts[2]
        home_en = cn_to_en.get(home_raw)
        away_en = cn_to_en.get(away_raw)

        if home_en and away_en:
            continue

        try:
            dt = datetime.strptime(date, '%Y-%m-%d')
            date_list = [(dt + timedelta(days=d)).strftime('%Y-%m-%d') for d in [0, 1, -1]]
        except Exception:
            date_list = [date]

        for d in date_list:
            if sh_id in mapping:
                break
            day_matches = matches_by_date.get(d, [])
            for m_id in day_matches:
                if m_id in mapping.values():
                    continue
                m_home, m_away = all_match_teams.get(m_id, ('', ''))
                found_id, score = _exact_substring_match(
                    home_raw, away_raw, {m_id: (m_home, m_away)})
                if found_id:
                    mapping[sh_id] = found_id
                    confidence_map[sh_id] = classify_match_confidence(
                        'substring', similarity_score=score)
                    method_map[sh_id] = 'substring'
                    substring_count += 1
                    break

    # 5. 模糊匹配 (阈值 0.60, 日期容差 ±1天)
    fuzzy_count = 0
    for sh_id in score_history_ids:
        if sh_id in mapping:
            continue
        parts = sh_id.split('_')
        if len(parts) < 3:
            continue
        date, home_raw, away_raw = parts[0], parts[1], parts[2]
        home_en = cn_to_en.get(home_raw)
        away_en = cn_to_en.get(away_raw)

        try:
            dt = datetime.strptime(date, '%Y-%m-%d')
            date_candidates = []
            for delta in [0, 1, -1]:
                d = (dt + timedelta(days=delta)).strftime('%Y-%m-%d')
                date_candidates.append((d, abs(delta)))
        except Exception:
            date_candidates = [(date, 0)]

        for d, day_diff in date_candidates:
            if sh_id in mapping:
                break
            day_matches = matches_by_date.get(d, [])
            for m_id in day_matches:
                if m_id in mapping.values():
                    continue
                m_parts = m_id.split('_')
                if len(m_parts) < 3:
                    continue
                m_home, m_away = m_parts[1], m_parts[2]

                if home_en and away_en:
                    if (m_home == home_en and m_away == away_en) or \
                       (m_home == away_en and m_away == home_en):
                        mapping[sh_id] = m_id
                        confidence_map[sh_id] = classify_match_confidence(
                            'date_tolerance', day_diff=day_diff)
                        method_map[sh_id] = 'date_tolerance'
                        date_tolerance_count += 1
                        break
                else:
                    home_score = _similarity(home_raw, m_home)
                    away_score = _similarity(away_raw, m_away)
                    home_score_rev = _similarity(home_raw, m_away)
                    away_score_rev = _similarity(away_raw, m_home)

                    if home_score >= 0.60 and away_score >= 0.60:
                        mapping[sh_id] = m_id
                        max_score = max(home_score, away_score)
                        confidence_map[sh_id] = classify_match_confidence(
                            'fuzzy_date', similarity_score=max_score, day_diff=day_diff)
                        method_map[sh_id] = 'fuzzy_date'
                        fuzzy_count += 1
                        break
                    elif home_score_rev >= 0.60 and away_score_rev >= 0.60:
                        mapping[sh_id] = m_id
                        max_score = max(home_score_rev, away_score_rev)
                        confidence_map[sh_id] = classify_match_confidence(
                            'fuzzy_date', similarity_score=max_score, day_diff=day_diff)
                        method_map[sh_id] = 'fuzzy_date'
                        fuzzy_count += 1
                        break

    total = len(score_history_ids)
    pct = len(mapping) / total * 100 if total > 0 else 0

    print(f"\n📊 匹配统计:")
    print(f"  直接匹配(英文): {direct_count}")
    print(f"  中文映射匹配: {cn_count}")
    print(f"  日期容差精确匹配: {date_tolerance_count}")
    print(f"  精确子串匹配: {substring_count}")
    print(f"  模糊匹配: {fuzzy_count}")
    print(f"  总计: {len(mapping)}/{total} ({pct:.1f}%)")

    return mapping, confidence_map, method_map



def print_quality_report(confidence_map, method_map):
    """打印质量报告: 置信度分层统计、低置信度警告、生产适用性建议"""
    if not confidence_map:
        print("\n⚠️ 无映射数据可供质量评估")
        return

    tiers = defaultdict(list)
    for sh_id, conf in confidence_map.items():
        tiers[conf].append(sh_id)

    print(f"\n{'='*60}")
    print("📋 映射质量报告 (虚假值风险评估)")
    print(f"{'='*60}")

    sorted_tiers = sorted(tiers.items(), key=lambda x: x[0], reverse=True)
    for conf, ids in sorted_tiers:
        method_counts = defaultdict(int)
        for sh_id in ids:
            method_counts[method_map.get(sh_id, 'unknown')] += 1
        methods_str = ', '.join(f'{m}: {c}' for m, c in method_counts.items())
        risk_label = (
            "✅ 无风险" if conf >= 0.95 else
            "🟢 极低风险" if conf >= 0.85 else
            "🟡 中等风险" if conf >= 0.75 else
            "🟠 较高风险" if conf >= 0.60 else
            "🔴 高风险"
        )
        print(f" 置信度 {conf:.2f} | {risk_label} | {len(ids)} 条 | 方法: {methods_str}")

    total_mappings = len(confidence_map)
    high_conf = sum(1 for c in confidence_map.values() if c >= 0.85)
    medium_conf = sum(1 for c in confidence_map.values() if 0.75 <= c < 0.85)
    low_conf = sum(1 for c in confidence_map.values() if c < 0.75)
    warning_count = sum(1 for c in confidence_map.values() if c < 0.80)

    print(f"\n  📊 分层汇总:")
    print(f"    高置信度 (>=0.85): {high_conf} 条 ({high_conf/total_mappings*100:.1f}%)")
    print(f"    中置信度 (0.75-0.84): {medium_conf} 条 ({medium_conf/total_mappings*100:.1f}%)")
    print(f"    低置信度 (<0.75): {low_conf} 条 ({low_conf/total_mappings*100:.1f}%)")
    print(f"    ⚠️ 需关注 (<0.80, 潜在虚假值): {warning_count} 条")

    if warning_count > 0:
        print(f"\n  ⚠️ 警告: 有 {warning_count} 条映射的置信度低于 0.80，可能存在虚假值风险。")
        print(f"     建议: 生产环境仅使用 confidence >= 0.85 的映射。")

    safe_pct = high_conf / total_mappings * 100 if total_mappings > 0 else 0
    print(f"\n  🏭 生产适用性: {'✅ 可用' if safe_pct >= 80 else '⚠️ 部分可用' if safe_pct >= 50 else '❌ 不推荐'}")
    print(f"     安全映射比例 (>=0.85): {safe_pct:.1f}%")

    print(f"{'='*60}")


def get_safe_mappings(confidence_map, method_map, min_confidence=0.85):
    """安全过滤器: 仅返回高置信度映射，供生产环境使用"""
    safe_ids = set()
    for sh_id, conf in confidence_map.items():
        if conf >= min_confidence:
            safe_ids.add(sh_id)
    return safe_ids


def create_match_id_mapping_table(conn):
    """在数据库中创建/更新 match_id_mapping 桥接表"""
    try:
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS match_id_mapping")
        cursor.execute("""
            CREATE TABLE match_id_mapping (
                sh_match_id TEXT PRIMARY KEY,
                matches_match_id TEXT NOT NULL,
                match_method TEXT,
                confidence REAL DEFAULT 1.0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)

        cn_to_en = build_enhanced_cn_to_en()

        cursor.execute("SELECT DISTINCT match_id FROM score_history")
        sh_ids = set(row[0] for row in cursor.fetchall())

        cursor.execute("SELECT match_id FROM matches")
        m_ids = set(row[0] for row in cursor.fetchall())

        print(f"\n📋 数据概览:")
        print(f"  score_history unique match_id: {len(sh_ids)}")
        print(f"  matches unique match_id: {len(m_ids)}")

        mapping, confidence_map, method_map = build_match_id_mapping(sh_ids, m_ids, cn_to_en)

        cursor.execute("DELETE FROM match_id_mapping")
        for sh_id, m_id in mapping.items():
            method = method_map.get(sh_id, 'unknown')
            confidence = confidence_map.get(sh_id, 0.50)
            cursor.execute(
                "INSERT OR IGNORE INTO match_id_mapping (sh_match_id, matches_match_id, match_method, confidence) VALUES (?, ?, ?, ?)",
                (sh_id, m_id, method, confidence)
            )
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM match_id_mapping")
        count = cursor.fetchone()[0]
        print(f"\n💾 match_id_mapping 表更新完成: {count} 条记录")

        print_quality_report(confidence_map, method_map)

        safe_ids = get_safe_mappings(confidence_map, method_map, min_confidence=0.85)
        print(f"\n🛡️  生产安全映射 (confidence >= 0.85): {len(safe_ids)} 条")

        return True
    except Exception as e:
        print(f"❌ 创建映射表失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print("=" * 70)
    print("🔧 score_history 增强匹配模块 (T-006 准备)")
    print("=" * 70)

    conn = sqlite3.connect(DB_PATH)
    success = create_match_id_mapping_table(conn)

    if success:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM match_id_mapping")
        mapped = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(DISTINCT match_id) FROM score_history")
        total = cursor.fetchone()[0]
        print(f"\n✅ 最终覆盖率: {mapped}/{total} ({mapped/total*100:.1f}%)")

        print(f"\n📊 置信度分布:")
        cursor.execute("""
            SELECT
                CASE
                    WHEN confidence >= 0.95 THEN '1.00-0.95 (高置信度)'
                    WHEN confidence >= 0.85 THEN '0.94-0.85 (生产可用)'
                    WHEN confidence >= 0.75 THEN '0.84-0.75 (中等风险)'
                    WHEN confidence >= 0.60 THEN '0.74-0.60 (较高风险)'
                    ELSE '<0.60 (高风险, 慎用)'
                END as tier,
                COUNT(*) as cnt
            FROM match_id_mapping
            GROUP BY tier
            ORDER BY tier
        """)
        for tier, cnt in cursor.fetchall():
            print(f"  {tier}: {cnt}")

        cursor.execute("SELECT AVG(confidence) FROM match_id_mapping")
        avg_conf = cursor.fetchone()[0]
        print(f"\n  平均置信度: {avg_conf:.3f}")

        cursor.execute("SELECT COUNT(*) FROM match_id_mapping WHERE confidence < 0.80")
        low_conf = cursor.fetchone()[0]
        print(f"  ⚠️ 低置信度 (<0.80) 映射数: {low_conf}")

    conn.close()
    print("\n🏁 完成!")
