import pandas as pd
import numpy as np
import json
import os
import sqlite3
import yaml
from pathlib import Path
from team_name_mapping import normalize_team_name

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR
OUTPUT_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.yaml"
DB_PATH = BASE_DIR / "data" / "five_leagues.db"

LEAGUE_CODE_MAP = {
    'EPL': 'PL',
    'BUNDESLIGA': 'BL1',
    'LALIGA': 'SA',
    'SERIEA': 'SerieA',
    'LIGUE1': 'FL1',
    # five_leagues.db 中 competitions.name 的映射（DB 改造后数据源）
    'Premier League': 'PL',
    'Bundesliga': 'BL1',
    'La Liga': 'SA',
    'Serie A': 'SerieA',
    'Ligue 1': 'FL1'
}

TEAM_KEY_MAP_EN = {
    'Liverpool': 'pl_liv', 'Aston Villa': 'pl_avl', 'Brighton': 'pl_bha',
    'Sunderland': 'pl_sun', 'West Ham': 'pl_whu', 'Man City': 'pl_mci',
    'Arsenal': 'pl_ars', 'Chelsea': 'pl_che', 'Man Utd': 'pl_mun',
    'Tottenham': 'pl_tot', 'Newcastle': 'pl_new', 'Fulham': 'pl_ful',
    'Bournemouth': 'pl_bou', 'Wolves': 'pl_wol', 'Crystal Palace': 'pl_cry',
    'Everton': 'pl_eve', 'Nottingham Forest': 'pl_nfo', 'Leicester': 'pl_lei',
    'Brentford': 'pl_bre', 'Southampton': 'pl_sou',
    'Barcelona': 'sa_bar', 'Real Madrid': 'sa_rma', 'Atletico Madrid': 'sa_atm',
    'Valencia': 'sa_val', 'Sevilla': 'sa_sev', 'Real Betis': 'sa_bet',
    'Villarreal': 'sa_vil', 'Getafe': 'sa_get', 'Espanyol': 'sa_esp',
    'Cadiz': 'sa_cad', 'Osasuna': 'sa_osa', 'Almeria': 'sa_alm',
    'Girona': 'sa_gir', 'Elche': 'sa_elc', 'Mallorca': 'sa_mll',
    'Celta': 'sa_civ', 'Real Sociedad': 'sa_rso', 'Rayo Vallecano': 'sa_rva',
    'Leganes': 'sa_leg', 'Zaragoza': 'sa_zar',
    'Bayern Munich': 'bl1_bay', 'Dortmund': 'bl1_dor', 'Leverkusen': 'bl1_lev',
    'Wolfsburg': 'bl1_wob', 'Frankfurt': 'bl1_fra', 'Stuttgart': 'bl1_scf',
    'Hoffenheim': 'bl1_tsg', 'Bochum': 'bl1_boc', 'Freiburg': 'bl1_fre',
    'Koln': 'bl1_koe', 'Mainz': 'bl1_mai', 'Augsburg': 'bl1_aug',
    'Hertha Berlin': 'bl1_hel', 'Union Berlin': 'bl1_sge', 'Gladbach': 'bl1_bmg',
    'RB Leipzig': 'bl1_rbl',
    'Juventus': 'seriea_juv', 'AC Milan': 'seriea_mil', 'Inter': 'seriea_int',
    'Roma': 'seriea_rom', 'Napoli': 'seriea_nap', 'Lazio': 'seriea_laz',
    'Fiorentina': 'seriea_flo', 'Atalanta': 'seriea_atl', 'Genoa': 'seriea_gen',
    'Bologna': 'seriea_bov', 'Salernitana': 'seriea_sal', 'Cremonese': 'seriea_cre',
    'Torino': 'seriea_tor', 'Udinese': 'seriea_udi', 'Empoli': 'seriea_emp',
    'Monza': 'seriea_mon', 'Lecce': 'seriea_lec', 'Sampdoria': 'seriea_sam',
    'Verona': 'seriea_ver', 'Spezia': 'seriea_spl',
    'Paris SG': 'fl1_psg', 'Marseille': 'fl1_mar', 'Lyon': 'fl1_lyo',
    'Monaco': 'fl1_mon', 'Rennes': 'fl1_ren', 'Strasbourg': 'fl1_str',
    'Bordeaux': 'fl1_bor', 'Nantes': 'fl1_nan', 'Reims': 'fl1_rei',
    'Toulouse': 'fl1_tou', 'Angers': 'fl1_ang', 'Metz': 'fl1_met',
    'Clermont': 'fl1_clo', 'Troyes': 'fl1_tro', 'Ajaccio': 'fl1_aja',
    'Brest': 'fl1_bre', 'Lorient': 'fl1_lor', 'Le Havre': 'fl1_hav',
    'Nice': 'fl1_nic'
}

# 中文队名 / DB 变体 -> team_key 的反向映射。
# 1) 先用 normalize_team_name(英文) 把 TEAM_KEY_MAP_EN 反向成 {中文: team_key}；
# 2) 再补充 DB 中存在但 TEAM_KEY_MAP_EN 未覆盖的队（2025-26 赛季升班马/降级队等）
#    以及 DB 里未归一化的英/西文变体（Ath Madrid / Vallecano）和中文同义变体（赫塔菲）。
CN_TO_TEAM_KEY = {}
for _en_name, _team_key in TEAM_KEY_MAP_EN.items():
    _cn_name = normalize_team_name(_en_name)
    if _cn_name:
        CN_TO_TEAM_KEY[_cn_name] = _team_key

CN_TO_TEAM_KEY.update({
    # DB 存在但 TEAM_KEY_MAP_EN 未覆盖的球队（中文标准名 -> team_key）
    '云达不莱梅': 'bl1_wer', '伯恩利': 'pl_bur', '利兹联': 'pl_lee',
    '卡利亚里': 'seriea_cag', '圣保利': 'bl1_stp', '奥维耶多': 'sa_ovi',
    '巴黎FC': 'fl1_pfc', '帕尔马': 'seriea_par', '朗斯': 'fl1_len',
    '桑坦德竞技': 'sa_rac', '欧塞尔': 'fl1_aux', '比萨': 'seriea_pis',
    '毕尔巴鄂': 'sa_ath', '汉堡': 'bl1_hsv', '海登海姆': 'bl1_hei',
    '科莫': 'seriea_com', '考文垂': 'pl_cov', '莱万特': 'sa_lev',
    '萨索洛': 'seriea_sas', '里尔': 'fl1_lil', '阿拉维斯': 'sa_ala',
    # DB 中未归一化的英/西文变体（normalize_team_name 返回 None，显式映射到既有 team_key）
    'Ath Madrid': 'sa_atm', 'Vallecano': 'sa_rva',
    # 中文同义变体（normalize 后可命中，这里兜底）
    '赫塔菲': 'sa_get',
})

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

def calculate_time_decay_weights(dates, reference_date, config=None):
    if config is None:
        config = CONFIG.get('training', {}).get('time_decay', {})
    
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

def load_match_data_from_db():
    """从 five_leagues.db 读取比赛数据，返回与原 load_csv_data 兼容的 DataFrame。"""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT
        m.date,
        ht.name AS home_team_name,
        at.name AS away_team_name,
        m.homeGoals,
        m.awayGoals,
        m.homeShots,
        m.awayShots,
        m.homeShotsOnTarget,
        m.awayShotsOnTarget,
        m.homeCorners,
        m.awayCorners,
        m.homeFouls,
        m.awayFouls,
        m.homeYellowCards,
        m.awayYellowCards,
        m.homePossession,
        c.name AS competition_name
    FROM matches m
    LEFT JOIN teams ht ON m.homeTeamId = ht.id
    LEFT JOIN teams at ON m.awayTeamId = at.id
    LEFT JOIN competitions c ON m.competitionId = c.id
    WHERE m.homeGoals IS NOT NULL
    ORDER BY m.date
    """
    df = pd.read_sql(query, conn)
    conn.close()

    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date', 'home_team_name', 'away_team_name', 'homeGoals', 'awayGoals'])
    print(f"Loaded {len(df)} matches from five_leagues.db")
    return df

def compute_team_attributes(df):
    team_attributes = {}
    all_teams = pd.concat([df['home_team_name'], df['away_team_name']]).unique()
    
    global_avg_goals = df['homeGoals'].mean()
    global_avg_opp_goals = df['awayGoals'].mean()
    global_avg_shots = df['homeShots'].fillna(df['awayShots']).mean()
    global_avg_shots_on_target = df['homeShotsOnTarget'].fillna(df['awayShotsOnTarget']).mean()
    global_avg_possession = df['homePossession'].mean()
    
    decay_config = CONFIG.get('training', {}).get('time_decay', {})
    
    for team_name in all_teams:
        home_matches = df[df['home_team_name'] == team_name].sort_values('date')
        away_matches = df[df['away_team_name'] == team_name].sort_values('date')
        all_matches = pd.concat([home_matches, away_matches]).sort_values('date')
        
        if len(all_matches) == 0:
            continue
        
        latest_date = all_matches['date'].max()
        weights = calculate_time_decay_weights(all_matches['date'], latest_date, decay_config)
        
        home_goals = all_matches['homeGoals'].where(all_matches['home_team_name'] == team_name)
        away_goals = all_matches['awayGoals'].where(all_matches['away_team_name'] == team_name)
        team_goals = home_goals.fillna(away_goals)
        
        home_opp_goals = all_matches['awayGoals'].where(all_matches['home_team_name'] == team_name)
        away_opp_goals = all_matches['homeGoals'].where(all_matches['away_team_name'] == team_name)
        team_opp_goals = home_opp_goals.fillna(away_opp_goals)
        
        home_shots = all_matches['homeShots'].where(all_matches['home_team_name'] == team_name)
        away_shots = all_matches['awayShots'].where(all_matches['away_team_name'] == team_name)
        team_shots = home_shots.fillna(away_shots)
        
        home_shots_on_target = all_matches['homeShotsOnTarget'].where(all_matches['home_team_name'] == team_name)
        away_shots_on_target = all_matches['awayShotsOnTarget'].where(all_matches['away_team_name'] == team_name)
        team_shots_on_target = home_shots_on_target.fillna(away_shots_on_target)
        
        home_possession = all_matches['homePossession'].where(all_matches['home_team_name'] == team_name)
        away_possession = (100 - all_matches['homePossession']).where(all_matches['away_team_name'] == team_name)
        team_possession = home_possession.fillna(away_possession)
        
        home_corners = all_matches['homeCorners'].where(all_matches['home_team_name'] == team_name)
        away_corners = all_matches['awayCorners'].where(all_matches['away_team_name'] == team_name)
        team_corners = home_corners.fillna(away_corners)
        
        valid_mask = weights > 0
        if valid_mask.sum() > 0:
            w_valid = weights[valid_mask]
            
            avg_goals = weighted_mean(team_goals[valid_mask].fillna(team_goals.mean()), w_valid)
            avg_opp_goals = weighted_mean(team_opp_goals[valid_mask].fillna(team_opp_goals.mean()), w_valid)
            avg_shots = weighted_mean(team_shots[valid_mask].fillna(team_shots.mean()), w_valid)
            avg_shots_on_target = weighted_mean(team_shots_on_target[valid_mask].fillna(team_shots_on_target.mean()), w_valid)
            avg_possession = weighted_mean(team_possession[valid_mask].fillna(50), w_valid)
            avg_corners = weighted_mean(team_corners[valid_mask].fillna(team_corners.mean()), w_valid)
        else:
            avg_goals = team_goals.mean()
            avg_opp_goals = team_opp_goals.mean()
            avg_shots = team_shots.mean()
            avg_shots_on_target = team_shots_on_target.mean()
            avg_possession = team_possession.mean() if not np.isnan(team_possession.mean()) else 50
            avg_corners = team_corners.mean()
        
        league = all_matches.iloc[0]['competition_name']
        league_code = LEAGUE_CODE_MAP.get(league, league)
        
        # team_name 来自 DB（中文为主）。优先用 CN_TO_TEAM_KEY 解析；
        # 先 normalize_team_name 处理中文同义变体（如 赫塔菲->赫塔费），
        # 再 fallback 到 TEAM_KEY_MAP_EN / 小写化。
        norm_name = normalize_team_name(team_name) or team_name
        team_key = (CN_TO_TEAM_KEY.get(norm_name)
                    or CN_TO_TEAM_KEY.get(team_name)
                    or TEAM_KEY_MAP_EN.get(team_name)
                    or team_name.lower().replace(' ', '_'))
        
        win_rate = (team_goals > team_opp_goals).mean()
        draw_rate = (team_goals == team_opp_goals).mean()
        loss_rate = (team_goals < team_opp_goals).mean()
        
        shots_on_target_rate = avg_shots_on_target / (avg_shots + 0.01)
        
        attack_score = (avg_goals / global_avg_goals) * (shots_on_target_rate / 0.25) * (avg_possession / 50)
        attack_score = np.clip(attack_score, 0.5, 2.0)
        
        defence_score = (global_avg_opp_goals / (avg_opp_goals + 0.01)) * 0.6
        defence_score = np.clip(defence_score, 0.3, 0.95)
        
        xgot_score = avg_shots * shots_on_target_rate * 0.15
        xgot_score = np.clip(xgot_score, 0.8, 2.5)
        
        xga_score = avg_opp_goals * 0.6
        xga_score = np.clip(xga_score, 0.4, 1.5)
        
        market_value = attack_score * 8
        market_value = np.clip(market_value, 1.5, 18.0)
        
        cohesion = 0.6 + win_rate * 0.4
        
        recent_form = (win_rate - 0.33) * 2
        
        tempo = 0.6 + (avg_shots / 15) * 0.4
        
        press_intensity = 0.6 + (avg_possession / 70) * 0.4
        
        tactical = 'possession' if avg_possession > 55 else 'pressing' if shots_on_target_rate > 0.3 else 'direct'
        
        team_attributes[team_key] = {
            'name': team_name,
            'league': league_code,
            'attack': float(round(attack_score, 2)),
            'defence': float(round(defence_score, 2)),
            'xGOT': float(round(xgot_score, 2)),
            'xGA': float(round(xga_score, 2)),
            'marketValue': float(round(market_value, 1)),
            'cohesion': float(round(cohesion, 2)),
            'recentForm': float(round(recent_form, 2)),
            'xT': float(round(xgot_score * 0.15, 2)),
            'tempo': float(round(tempo, 2)),
            'pressIntensity': float(round(press_intensity, 2)),
            'avgGoals': float(round(avg_goals, 2)),
            'avgOppGoals': float(round(avg_opp_goals, 2)),
            'avgPossession': float(round(avg_possession, 1)) if not np.isnan(avg_possession) else 50.0,
            'avgShots': float(round(avg_shots, 1)),
            'winRate': float(round(win_rate, 3)),
            'drawRate': float(round(draw_rate, 3)),
            'lossRate': float(round(loss_rate, 3)),
            'matchesPlayed': int(len(all_matches)),
            'tactical': tactical,
            'attackSide': {'left': 0.25, 'center': 0.50, 'right': 0.25}
        }
    
    return team_attributes

def main():
    print("=" * 60)
    print("生成数据驱动的球队属性")
    print("=" * 60)
    
    print("\n1. 从 five_leagues.db 加载比赛数据...")
    df = load_match_data_from_db()
    print(f"   有效数据: {len(df)} 场比赛")
    
    print("\n2. 计算球队属性...")
    team_attributes = compute_team_attributes(df)
    print(f"   生成 {len(team_attributes)} 支球队的属性")
    
    print("\n3. 保存球队属性到JSON...")
    output_path = os.path.join(OUTPUT_DIR, 'team_attributes.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(team_attributes, f, ensure_ascii=False, indent=2)
    print(f"   已保存到 {output_path}")
    
    print("\n4. 保存为JavaScript格式...")
    js_output_path = os.path.join(OUTPUT_DIR, 'team_attributes.js')
    with open(js_output_path, 'w', encoding='utf-8') as f:
        f.write(f"var TEAM_ATTRIBUTES = {json.dumps(team_attributes, ensure_ascii=False, indent=2)};")
    print(f"   已保存到 {js_output_path}")
    
    print("\n" + "=" * 60)
    print("生成完成!")
    print("=" * 60)
    
    print("\n示例球队属性:")
    sample_teams = list(team_attributes.keys())[:5]
    for key in sample_teams:
        attrs = team_attributes[key]
        print(f"  {key}: {attrs['name']}")
        print(f"    attack={attrs['attack']}, defence={attrs['defence']}, xGOT={attrs['xGOT']}")
        print(f"    winRate={attrs['winRate']}, matches={attrs['matchesPlayed']}")

if __name__ == "__main__":
    main()
