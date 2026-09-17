"""
从CSV导入五大联赛2025-2026赛季赔率数据到odds.db
支持中文/英文两种CSV格式
"""
import pandas as pd
import sqlite3
import os
from datetime import datetime
from dateutil import parser as date_parser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
FIVE_LEAGUES_DB = os.path.join(DATA_DIR, "five_leagues.db")
ODDS_DB = os.path.join(DATA_DIR, "odds.db")

# === 联赛名归一化：英文名 → 中文名映射 ===
# 修复：原 config['name'] 直接使用英文名（如 "Premier League"），
# 导致 matches 表中英混存，one-hot 编码维度膨胀。
# 现统一写入标准中文名 + 赛季后缀格式。
EN_TO_CN = {
    "Premier League": "英超",
    "Serie A":        "意甲",
    "La Liga":        "西甲",
    "Bundesliga":     "德甲",
    "Ligue 1":        "法甲",
}

def get_season(date_str):
    """根据日期返回赛季标签 (YYYY-YYYY+1)"""
    if not date_str:
        return "未知赛季"
    # 兼容多种日期格式：2023-08-12 / 2023-08-12 15:00:00 / 2023/08/12
    date_str = str(date_str).strip()
    year = int(date_str[:4])
    # 提取月份
    if len(date_str) >= 7:
        month = int(date_str[5:7])
    else:
        month = 1
    if month >= 8:
        return f"{year}-{year+1}"
    else:
        return f"{year-1}-{year}"

def build_match_type(en_name, date_str):
    """构建标准 match_type: 英超2023-2024赛季"""
    cn_name = EN_TO_CN.get(en_name, en_name)
    season = get_season(date_str)
    return f"{cn_name}{season}赛季"

# 联赛配置
LEAGUE_CONFIGS = [
    {
        'name': 'Premier League',
        'code': 'PL',
        'competition_id': 1,
        'csv_file': 'EPL_2025-26.csv',
        'lang': 'zh',  # 虽然球队名是英文，但列名是中文
        'team_col_home': '主队',
        'team_col_away': '客队',
        'date_col': '日期',
        'time_col': '时间',
        'home_goals_col': '主队进球',
        'away_goals_col': '客队进球',
        'result_col': '赛果',
        'wdl_home': 'bet365_主胜',
        'wdl_draw': 'bet365_平局',
        'wdl_away': 'bet365_客胜',
        'wdl_close_home': 'B365CH',
        'wdl_close_draw': 'B365CD',
        'wdl_close_away': 'B365CA',
        'tg_over': 'bet365_大2.5',
        'tg_under': 'bet365_小2.5',
        'tg_close_over': 'B365C>2.5',
        'tg_close_under': 'B365C<2.5',
        'hcp_line': '亚盘盘口',
        'hcp_home': 'bet365_亚盘主',
        'hcp_away': 'bet365_亚盘客',
        'hcp_close_home': 'B365CAHH',
        'hcp_close_away': 'B365CAHA',
    },
    {
        'name': 'Bundesliga',
        'code': 'BL1',
        'competition_id': 3,
        'csv_file': 'BUNDESLIGA_2025-26.csv',
        'lang': 'en',
        'team_col_home': 'HomeTeam',
        'team_col_away': 'AwayTeam',
        'date_col': 'Date',
        'time_col': 'Time',
        'home_goals_col': 'FTHG',
        'away_goals_col': 'FTAG',
        'result_col': 'FTR',
        'wdl_home': 'B365H',
        'wdl_draw': 'B365D',
        'wdl_away': 'B365A',
        'wdl_close_home': 'B365CH',
        'wdl_close_draw': 'B365CD',
        'wdl_close_away': 'B365CA',
        'tg_over': 'B365>2.5',
        'tg_under': 'B365<2.5',
        'tg_close_over': 'B365C>2.5',
        'tg_close_under': 'B365C<2.5',
        'hcp_line': 'AHh',
        'hcp_home': 'B365AHH',
        'hcp_away': 'B365AHA',
        'hcp_close_home': 'B365CAHH',
        'hcp_close_away': 'B365CAHA',
    },
    {
        'name': 'La Liga',
        'code': 'SA',
        'competition_id': 2,
        'csv_file': 'LALIGA_2025-26.csv',
        'lang': 'zh',
        'team_col_home': '主队',
        'team_col_away': '客队',
        'date_col': '日期',
        'time_col': '时间',
        'home_goals_col': '主队进球',
        'away_goals_col': '客队进球',
        'result_col': '赛果',
        'wdl_home': 'bet365_主胜',
        'wdl_draw': 'bet365_平局',
        'wdl_away': 'bet365_客胜',
        'wdl_close_home': 'B365CH',
        'wdl_close_draw': 'B365CD',
        'wdl_close_away': 'B365CA',
        'tg_over': 'bet365_大2.5',
        'tg_under': 'bet365_小2.5',
        'tg_close_over': 'B365C>2.5',
        'tg_close_under': 'B365C<2.5',
        'hcp_line': '亚盘盘口',
        'hcp_home': 'bet365_亚盘主',
        'hcp_away': 'bet365_亚盘客',
        'hcp_close_home': 'B365CAHH',
        'hcp_close_away': 'B365CAHA',
    },
    {
        'name': 'Serie A',
        'code': 'SerieA',
        'competition_id': 4,
        'csv_file': 'SERIEA_2025-26.csv',  # 旧数据源（仅初盘和尾盘）
        'csv_file_new': 'SERIEA_2025-26_DETAILED.csv',  # 新详细数据源（多时间节点）
        'lang': 'zh',
        'team_col_home': '主队',
        'team_col_away': '客队',
        'date_col': '日期',
        'time_col': '时间',
        'home_goals_col': '主队进球',
        'away_goals_col': '客队进球',
        'result_col': '赛果',
        'wdl_home': 'bet365_主胜',
        'wdl_draw': 'bet365_平局',
        'wdl_away': 'bet365_客胜',
        'wdl_close_home': 'B365CH',
        'wdl_close_draw': 'B365CD',
        'wdl_close_away': 'B365CA',
        'tg_over': 'bet365_大2.5',
        'tg_under': 'bet365_小2.5',
        'tg_close_over': 'B365C>2.5',
        'tg_close_under': 'B365C<2.5',
        'hcp_line': '亚盘盘口',
        'hcp_home': 'bet365_亚盘主',
        'hcp_away': 'bet365_亚盘客',
        'hcp_close_home': 'B365CAHH',
        'hcp_close_away': 'B365CAHA',
    },
    {
        'name': 'Ligue 1',
        'code': 'FL1',
        'competition_id': 5,
        'csv_file': 'LIGUE1_2025-26.csv',
        'lang': 'zh',
        'team_col_home': '主队',
        'team_col_away': '客队',
        'date_col': '日期',
        'time_col': '时间',
        'home_goals_col': '主队进球',
        'away_goals_col': '客队进球',
        'result_col': '赛果',
        'wdl_home': 'bet365_主胜',
        'wdl_draw': 'bet365_平局',
        'wdl_away': 'bet365_客胜',
        'wdl_close_home': 'B365CH',
        'wdl_close_draw': 'B365CD',
        'wdl_close_away': 'B365CA',
        'tg_over': 'bet365_大2.5',
        'tg_under': 'bet365_小2.5',
        'tg_close_over': 'B365C>2.5',
        'tg_close_under': 'B365C<2.5',
        'hcp_line': '亚盘盘口',
        'hcp_home': 'bet365_亚盘主',
        'hcp_away': 'bet365_亚盘客',
        'hcp_close_home': 'B365CAHH',
        'hcp_close_away': 'B365CAHA',
    },
]

def parse_date(date_str, dayfirst=True):
    """解析日期字符串为统一格式 YYYY-MM-DD
    
    Args:
        date_str: 日期字符串
        dayfirst: 是否是日/月/年格式（欧洲格式）
    """
    try:
        date_str = str(date_str).strip()
        if not date_str or date_str == 'nan':
            return date_str
        
        # 尝试用dateutil解析
        from dateutil import parser as date_parser
        dt = date_parser.parse(date_str, dayfirst=dayfirst)
        return dt.strftime('%Y-%m-%d')
    except:
        # 备用：手动解析
        try:
            if '/' in date_str:
                parts = date_str.split('/')
                if len(parts) == 3:
                    if dayfirst:
                        # dd/mm/yyyy
                        day, month, year = parts
                        if len(year) == 2:
                            year = '20' + year
                        return f"{year}-{int(month):02d}-{int(day):02d}"
                    else:
                        # mm/dd/yyyy
                        month, day, year = parts
                        if len(year) == 2:
                            year = '20' + year
                        return f"{year}-{int(month):02d}-{int(day):02d}"
            elif '-' in date_str:
                parts = date_str.split('-')
                if len(parts) == 3:
                    if len(parts[0]) == 4:
                        return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
                    elif len(parts[2]) == 4:
                        return f"{parts[2]}-{int(parts[1]):02d}-{int(parts[0]):02d}"
            return date_str
        except:
            return date_str

def get_wdl_label(result, home_goals, away_goals):
    """获取胜平负标签（主队视角）"""
    if home_goals is not None and away_goals is not None:
        if home_goals > away_goals:
            return '胜'
        elif home_goals == away_goals:
            return '平'
        else:
            return '负'
    if result in ['H', 'h', '胜', '主胜']:
        return '胜'
    elif result in ['D', 'd', '平', '平局']:
        return '平'
    elif result in ['A', 'a', '负', '客胜']:
        return '负'
    return None

def safe_float(val):
    """安全转换为float"""
    try:
        if pd.isna(val) or val is None or val == '':
            return None
        return float(val)
    except:
        return None

def safe_int(val):
    """安全转换为int"""
    try:
        if pd.isna(val) or val is None or val == '':
            return None
        return int(float(val))
    except:
        return None

def load_team_mapping():
    """从team_mapping表加载球队名称映射"""
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    mapping = {}
    try:
        cursor.execute('SELECT original_name, standard_name, league FROM team_mapping')
        for row in cursor.fetchall():
            key = (row[0], row[2])
            mapping[key] = row[1]
    except:
        pass
    conn.close()
    return mapping

def find_five_leagues_match(home_team, away_team, match_date, competition_id):
    """在five_leagues.db中查找匹配的比赛（±1天容忍度）"""
    conn = sqlite3.connect(FIVE_LEAGUES_DB)
    cursor = conn.cursor()
    
    # 获取球队ID（尝试多种名称匹配）
    def find_team_id(name):
        # 精确匹配
        cursor.execute("SELECT id FROM teams WHERE name = ? OR shortName = ?", (name, name))
        row = cursor.fetchone()
        if row:
            return row[0]
        # 模糊匹配
        cursor.execute("SELECT id FROM teams WHERE name LIKE ? OR shortName LIKE ?", (f'%{name}%', f'%{name}%'))
        row = cursor.fetchone()
        if row:
            return row[0]
        return None
    
    home_id = find_team_id(home_team)
    away_id = find_team_id(away_team)
    
    if home_id and away_id:
        # 精确日期匹配
        cursor.execute("""
            SELECT id FROM matches 
            WHERE homeTeamId = ? AND awayTeamId = ? 
            AND competitionId = ?
            AND date LIKE ?
        """, (home_id, away_id, competition_id, f'{match_date}%'))
        row = cursor.fetchone()
        if row:
            conn.close()
            return row[0]
        
        # ±1天容忍度
        cursor.execute("""
            SELECT id, date FROM matches 
            WHERE homeTeamId = ? AND awayTeamId = ? 
            AND competitionId = ?
            ORDER BY ABS(julianday(date) - julianday(?))
            LIMIT 1
        """, (home_id, away_id, competition_id, match_date))
        row = cursor.fetchone()
        if row:
            # 检查日期差是否在1天内
            try:
                from datetime import datetime
                d1 = datetime.strptime(row[1][:10], '%Y-%m-%d')
                d2 = datetime.strptime(match_date, '%Y-%m-%d')
                if abs((d1 - d2).days) <= 1:
                    conn.close()
                    return row[0]
            except:
                pass
    
    conn.close()
    return None

def import_league(config):
    """导入单个联赛的数据（支持新数据源优先）"""
    # 优先使用新的详细数据源（如果存在）
    csv_file = config['csv_file']
    if 'csv_file_new' in config:
        new_csv_path = os.path.join(DATA_DIR, config['csv_file_new'])
        if os.path.exists(new_csv_path):
            csv_file = config['csv_file_new']
            print(f"  ✅ 使用新详细数据源: {csv_file}")
        elif not os.path.exists(os.path.join(DATA_DIR, config['csv_file'])):
            print(f"  ⚠️ 新旧数据源都不存在: {config['csv_file']} / {config['csv_file_new']}")
            print(f"  ⚠️ 请确保至少存在一个数据源文件")
            return 0, 0, 0, 0, 0
    
    csv_path = os.path.join(DATA_DIR, csv_file)
    if not os.path.exists(csv_path):
        print(f"  ⚠️ 文件不存在: {csv_path}")
        return 0, 0, 0, 0, 0
    
    df = pd.read_csv(csv_path)
    print(f"\n  读取 {len(df)} 场比赛")
    
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    
    match_count = 0
    wdl_count = 0
    hcp_count = 0
    tg_count = 0
    mapped_count = 0
    
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for idx, row in df.iterrows():
        try:
            home_team = str(row[config['team_col_home']]).strip()
            away_team = str(row[config['team_col_away']]).strip()
            match_date = parse_date(str(row[config['date_col']]).strip())
            match_time = str(row[config['time_col']]).strip() if config['time_col'] in df.columns else ''
            
            home_goals = safe_int(row[config['home_goals_col']])
            away_goals = safe_int(row[config['away_goals_col']])
            result = row[config['result_col']] if config['result_col'] in df.columns else None
            actual_wdl = get_wdl_label(result, home_goals, away_goals)
            actual_score = f"{home_goals}:{away_goals}" if home_goals is not None and away_goals is not None else None
            actual_total_goals = home_goals + away_goals if home_goals is not None and away_goals is not None else None
            
            # 生成match_id
            match_id = f"{match_date}_{home_team}_{away_team}"
            
            # 让球盘口：此 CSV 为 bet365 亚盘（半整数盘口，无让球平），
            # 与竞彩 goalLine（整数盘口，含让球平/走水）口径不同。
            # matches.handicap 统一仅由竞彩 goalLine 数据源写入，这里不覆盖，避免污染。
            handicap = safe_float(row[config['hcp_line']]) if config['hcp_line'] in df.columns else None

            # 1. 插入/更新matches表（不写 handicap / actual_handicap，保留竞彩 goalLine 数据）
            cursor.execute("""
                INSERT INTO matches 
                (match_id, home_team, away_team, match_date, match_type, 
                 actual_wdl, actual_score, actual_total_goals, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    home_team=excluded.home_team,
                    away_team=excluded.away_team,
                    match_date=excluded.match_date,
                    match_type=excluded.match_type,
                    actual_wdl=excluded.actual_wdl,
                    actual_score=excluded.actual_score,
                    actual_total_goals=excluded.actual_total_goals,
                    updated_at=excluded.updated_at
            """, (
                match_id, home_team, away_team, match_date, 
                build_match_type(config['name'], match_date),
                actual_wdl, actual_score, actual_total_goals,
                now, now
            ))
            match_count += 1
            
            # 2. 胜平负赔率（开盘）
            wdl_h = safe_float(row.get(config['wdl_home']))
            wdl_d = safe_float(row.get(config['wdl_draw']))
            wdl_a = safe_float(row.get(config['wdl_away']))
            if wdl_h and wdl_d and wdl_a:
                cursor.execute("""
                    INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                    VALUES (?, ?, ?, ?, ?)
                """, (match_id, match_date, wdl_h, wdl_d, wdl_a))
                wdl_count += 1
            
            # 胜平负赔率（收盘）
            wdl_ch = safe_float(row.get(config['wdl_close_home']))
            wdl_cd = safe_float(row.get(config['wdl_close_draw']))
            wdl_ca = safe_float(row.get(config['wdl_close_away']))
            if wdl_ch and wdl_cd and wdl_ca:
                cursor.execute("""
                    INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                    VALUES (?, ?, ?, ?, ?)
                """, (match_id, f"{match_date}_close", wdl_ch, wdl_cd, wdl_ca))
                wdl_count += 1
            
            # 3. 让球赔率
            hcp_h = safe_float(row.get(config['hcp_home']))
            hcp_a = safe_float(row.get(config['hcp_away']))
            if hcp_h and hcp_a and handicap is not None:
                # 让球平局赔率可能没有，用默认值
                cursor.execute("""
                    INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                    VALUES (?, ?, ?, ?, ?)
                """, (match_id, match_date, hcp_h, None, hcp_a))
                hcp_count += 1
            
            # 让球收盘赔率
            hcp_ch = safe_float(row.get(config['hcp_close_home']))
            hcp_ca = safe_float(row.get(config['hcp_close_away']))
            if hcp_ch and hcp_ca and handicap is not None:
                cursor.execute("""
                    INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                    VALUES (?, ?, ?, ?, ?)
                """, (match_id, f"{match_date}_close", hcp_ch, None, hcp_ca))
                hcp_count += 1
            
            # 4. 总进球赔率（大小球）
            tg_over = safe_float(row.get(config['tg_over']))
            tg_under = safe_float(row.get(config['tg_under']))
            if tg_over and tg_under:
                # 总进球表结构是goals_0到goals_7_plus，大小球只有2.5的，我们存到对应位置
                # 这里只存储大小球作为参考，用goals_2(=大)和goals_3(=小)的方式不太合适
                # 更简单的方式：不填具体进球数赔率，只记录有数据
                cursor.execute("""
                    INSERT OR IGNORE INTO total_goals_history 
                    (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, 
                     goals_4, goals_5, goals_6, goals_7_plus)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (match_id, match_date, None, None, tg_over, tg_under, None, None, None, None))
                tg_count += 1
            
            # 总进球收盘赔率
            tg_c_over = safe_float(row.get(config['tg_close_over']))
            tg_c_under = safe_float(row.get(config['tg_close_under']))
            if tg_c_over and tg_c_under:
                cursor.execute("""
                    INSERT OR IGNORE INTO total_goals_history 
                    (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, 
                     goals_4, goals_5, goals_6, goals_7_plus)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (match_id, f"{match_date}_close", None, None, tg_c_over, tg_c_under, None, None, None, None))
                tg_count += 1
            
            # 5. 尝试匹配five_leagues.db
            fl_match_id = find_five_leagues_match(home_team, away_team, match_date, config['competition_id'])
            if fl_match_id:
                cursor.execute("""
                    INSERT OR IGNORE INTO match_mapping 
                    (odds_match_id, five_leagues_match_id, home_team, away_team, match_date, league, mapped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (match_id, fl_match_id, home_team, away_team, match_date, build_match_type(config['name'], match_date), now))
                mapped_count += 1
            
        except Exception as e:
            print(f"  ⚠️ 第{idx+1}行导入失败: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    return match_count, wdl_count, hcp_count, tg_count, mapped_count

def main():
    print("=" * 70)
    print("五大联赛2025-2026赛季赔率数据批量导入")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    total_matches = 0
    total_wdl = 0
    total_hcp = 0
    total_tg = 0
    total_mapped = 0
    
    for config in LEAGUE_CONFIGS:
        print(f"\n{'='*50}")
        print(f"导入 {config['name']} ({config['csv_file']})")
        print(f"{'='*50}")
        
        m, w, h, t, mp = import_league(config)
        print(f"  结果: {m}场比赛, {w}条WDL, {h}条让球, {t}条总进球, {mp}条映射")
        
        total_matches += m
        total_wdl += w
        total_hcp += h
        total_tg += t
        total_mapped += mp
    
    print("\n" + "=" * 70)
    print("导入完成!")
    print(f"  总比赛数: {total_matches}")
    print(f"  WDL赔率记录: {total_wdl}")
    print(f"  让球赔率记录: {total_hcp}")
    print(f"  总进球赔率记录: {total_tg}")
    print(f"  已映射到five_leagues: {total_mapped}")
    print(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

if __name__ == '__main__':
    main()
