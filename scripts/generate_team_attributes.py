import pandas as pd
import numpy as np
import json
import os
import yaml
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR
OUTPUT_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.yaml"

CSV_FILES = {
    'EPL': 'EPL_2025-26.csv',
    'BUNDESLIGA': 'BUNDESLIGA_2025-26.csv',
    'LALIGA': 'LALIGA_2025-26.csv',
    'SERIEA': 'SERIEA_2025-26.csv',
    'LIGUE1': 'LIGUE1_2025-26.csv'
}

# 新详细数据源映射（优先使用）
CSV_FILES_DETAILED = {
    'SERIEA': 'SERIEA_2025-26_DETAILED.csv',
}


def get_csv_file(league):
    """获取指定联赛的CSV文件路径（支持新数据源优先）"""
    data_path = os.path.join(DATA_DIR, 'data')
    
    # 检查是否有新详细数据源
    if league in CSV_FILES_DETAILED:
        detailed_path = os.path.join(data_path, CSV_FILES_DETAILED[league])
        if os.path.exists(detailed_path):
            print(f"  ✅ 使用新详细数据源: {CSV_FILES_DETAILED[league]}")
            return detailed_path
    
    # 使用旧数据源
    if league in CSV_FILES:
        old_path = os.path.join(data_path, CSV_FILES[league])
        if os.path.exists(old_path):
            return old_path
    
    print(f"  ⚠️ 数据源文件不存在: {league}")
    return None

LEAGUE_CODE_MAP = {
    'EPL': 'PL',
    'BUNDESLIGA': 'BL1',
    'LALIGA': 'SA',
    'SERIEA': 'SerieA',
    'LIGUE1': 'FL1'
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

def normalize_league_data(df, league):
    normalized = df.copy()
    
    if '日期' in df.columns:
        normalized['date'] = pd.to_datetime(df['日期'], format='%d/%m/%Y', errors='coerce')
        normalized['home_team_name'] = df['主队']
        normalized['away_team_name'] = df['客队']
        normalized['homeGoals'] = df['主队进球']
        normalized['awayGoals'] = df['客队进球']
        normalized['homeShots'] = df['主队射门'] if '主队射门' in df.columns else np.nan
        normalized['awayShots'] = df['客队射门'] if '客队射门' in df.columns else np.nan
        normalized['homeShotsOnTarget'] = df['主队射正'] if '主队射正' in df.columns else np.nan
        normalized['awayShotsOnTarget'] = df['客队射正'] if '客队射正' in df.columns else np.nan
        normalized['homeCorners'] = df['主队角球'] if '主队角球' in df.columns else np.nan
        normalized['awayCorners'] = df['客队角球'] if '客队角球' in df.columns else np.nan
        normalized['homeYellowCards'] = df['主队黄牌'] if '主队黄牌' in df.columns else np.nan
        normalized['awayYellowCards'] = df['客队黄牌'] if '客队黄牌' in df.columns else np.nan
        normalized['homeFouls'] = df['主队犯规'] if '主队犯规' in df.columns else np.nan
        normalized['awayFouls'] = df['客队犯规'] if '客队犯规' in df.columns else np.nan
        normalized['homePossession'] = df['主队控球率'] if '主队控球率' in df.columns else np.nan
    else:
        normalized['date'] = pd.to_datetime(df['Date'], format='%d/%m/%Y', errors='coerce')
        normalized['home_team_name'] = df['HomeTeam']
        normalized['away_team_name'] = df['AwayTeam']
        normalized['homeGoals'] = df['FTHG']
        normalized['awayGoals'] = df['FTAG']
        normalized['homeShots'] = df['HS'] if 'HS' in df.columns else np.nan
        normalized['awayShots'] = df['AS'] if 'AS' in df.columns else np.nan
        normalized['homeShotsOnTarget'] = df['HST'] if 'HST' in df.columns else np.nan
        normalized['awayShotsOnTarget'] = df['AST'] if 'AST' in df.columns else np.nan
        normalized['homeCorners'] = df['HC'] if 'HC' in df.columns else np.nan
        normalized['awayCorners'] = df['AC'] if 'AC' in df.columns else np.nan
        normalized['homeYellowCards'] = df['HY'] if 'HY' in df.columns else np.nan
        normalized['awayYellowCards'] = df['AY'] if 'AY' in df.columns else np.nan
        normalized['homeFouls'] = df['HF'] if 'HF' in df.columns else np.nan
        normalized['awayFouls'] = df['AF'] if 'AF' in df.columns else np.nan
        normalized['homePossession'] = np.nan
    
    normalized['competition_name'] = league
    return normalized

def load_csv_data():
    dfs = []
    for league, filename in CSV_FILES.items():
        filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            df = pd.read_csv(filepath, encoding='utf-8')
            df = normalize_league_data(df, league)
            dfs.append(df)
            print(f"Loaded {len(df)} matches from {filename}")
        else:
            print(f"Warning: {filepath} not found")
    
    if not dfs:
        raise ValueError("No CSV files loaded")
    
    df = pd.concat(dfs, ignore_index=True)
    df = df.dropna(subset=['date', 'home_team_name', 'away_team_name', 'homeGoals', 'awayGoals'])
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
        
        team_key = TEAM_KEY_MAP_EN.get(team_name, team_name.lower().replace(' ', '_'))
        
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
    
    print("\n1. 加载CSV数据...")
    df = load_csv_data()
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