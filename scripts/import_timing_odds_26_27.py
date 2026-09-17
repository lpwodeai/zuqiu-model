# -*- coding: utf-8 -*-
"""导入4个联赛26/27赛季时序赔率txt到odds_timing.db"""
import re, sqlite3, os
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
ODDS_DB = str(DATA / "odds.db")
TIMING_DB = str(DATA / "odds_timing.db")

TXT_FILES = {
    "英超": "英超2026-2027赛季完整时序赔率.txt",
    "西甲": "西甲2026-2027赛季完整时序赔率.txt",
    "法甲": "法甲2026-2027赛季完整时序赔率.txt",
    "意甲": "意甲2026-2027赛季完整时序赔率.txt",
}

LEAGUE_PREFIX = {
    "英超": "英超2026-2027赛季",
    "西甲": "西甲2026-2027赛季",
    "法甲": "法甲2026-2027赛季",
    "意甲": "意甲2026-2027赛季",
}

def build_cn_to_en():
    """中文队名→英文队名（匹配 matches 表格式）"""
    # 直接映射: txt中文名 → matches表英文名
    return {
        # 英超
        "阿森纳": "Arsenal", "考文垂": "Coventry City",
        "埃弗顿": "Everton", "水晶宫": "Crystal Palace",
        "诺丁汉": "Nottingham Forest", "利兹联": "Leeds United",
        "伊普斯": "Ipswich Town", "桑德兰": "Sunderland",
        "布伦特": "Brentford", "热刺": "Tottenham Hotspur",
        "布莱顿": "Brighton & Hove Albion", "阿斯顿": "Aston Villa",
        "曼城": "Manchester City", "伯恩茅斯": "Bournemouth",
        "纽卡斯尔": "Newcastle United", "利物浦": "Liverpool FC",
        "富勒姆": "Fulham", "切尔西": "Chelsea",
        "赫尔城": "Hull City", "曼联": "Manchester United",
        # 西甲
        "贝蒂斯": "Real Betis", "皇家社会": "Real Sociedad",
        "毕尔巴鄂": "Athletic Club", "塞维利亚": "Sevilla",
        "巴伦西亚": "Valencia", "塞尔塔": "Celta Vigo",
        "西班牙人": "Espanyol", "皇马": "Real Madrid",
        "马竞": "Atletico Madrid", "比利亚雷": "Villarreal",
        "埃尔切": "Elche", "巴萨": "FC Barcelona",
        "赫塔费": "Getafe", "奥萨苏纳": "Osasuna",
        "莱万特": "Levante UD", "桑坦德": "Real Racing Club",
        "马拉加": "Malaga CF", "拉科鲁": "Deportivo de A Coruna",
        "巴列卡诺": "Rayo Vallecano", "阿拉维斯": "Deportivo Alaves",
        # 法甲
        "马赛": "Olympique de Marseille", "斯特拉斯": "RC Strasbourg",
        "朗斯": "RC Lens", "欧塞尔": "Auxerre",
        "尼斯": "Nice", "洛里昂": "Lorient",
        "图卢兹": "Toulouse", "里昂": "Olympique Lyonnais",
        "昂热": "Angers", "里尔": "Lille",
        "勒阿弗尔": "Le Havre", "摩纳哥": "AS Monaco",
        "勒芒": "Le Mans", "布雷斯特": "Stade Brestois",
        "特鲁瓦": "Troyes", "巴黎FC": "Paris FC",
        "雷恩": "Stade Rennais", "巴黎圣日": "Paris Saint-Germain",
        # 意甲
        "乌迪内斯": "Udinese", "科莫": "Como",
        "国际米兰": "Inter", "蒙扎": "Monza",
        "热那亚": "Genoa", "那不勒斯": "SSC Napoli",
        "帕尔马": "Parma", "卡利亚里": "Cagliari",
        "亚特兰大": "Atalanta", "萨索洛": "Sassuolo",
        "都灵": "Torino", "AC米兰": "AC Milan",
        "威尼斯": "Venezia", "莱切": "Lecce",
        "弗罗西诺": "Frosinone", "尤文图斯": "Juventus",
        "博洛尼亚": "Bologna", "拉齐奥": "Lazio",
        "罗马": "AS Roma", "佛罗伦萨": "Fiorentina",
    }


def safe_float(val):
    try:
        return float(val) if val and val != '--' and val != '-' else None
    except (ValueError, TypeError):
        return None


def parse_all_txt_files(cn_to_en):
    """解析所有4个联赛的txt文件"""
    all_matches = []

    for league, filename in TXT_FILES.items():
        filepath = DATA / filename
        if not filepath.exists():
            print(f"  [WARN] 文件不存在: {filepath}")
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            # 匹配赛季头部: 2026/2027 Regular Season 第X轮 YYYY-MM-DD HH:MM
            m = re.match(r'2026/2027 Regular Season 第(\d+)轮 (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})', line)
            if m:
                round_num = int(m.group(1))
                match_date = m.group(2)
                match_time = m.group(3)

                # 下一行是球队信息
                if i + 1 < len(lines):
                    team_line = lines[i + 1].strip()
                    # 格式: "英超     阿森纳VS考文垂" 或 "英超    阿森纳VS考文垂"
                    team_match = re.search(r'(\S+)\s*VS\s*(\S+)', team_line)
                    if team_match:
                        cn_home = team_match.group(1)
                        cn_away = team_match.group(2)

                        en_home = cn_to_en.get(cn_home)
                        en_away = cn_to_en.get(cn_away)

                        if not en_home or not en_away:
                            print(f"  [WARN] 队名映射失败: {cn_home}→{en_home}, {cn_away}→{en_away}")
                            i += 1
                            continue

                        match_id = f"{match_date}_{en_home}_{en_away}"

                        # 解析4个赔率section
                        sections = _parse_sections(lines, i + 2)

                        match_data = {
                            'match_id': match_id,
                            'home_team': en_home,
                            'away_team': en_away,
                            'cn_home': cn_home,
                            'cn_away': cn_away,
                            'match_date': match_date,
                            'match_time': match_time,
                            'round': round_num,
                            'league': LEAGUE_PREFIX[league],
                            'league_code': league,
                            'source': f'{league}_26_27',
                            **sections,
                        }
                        all_matches.append(match_data)

                # 跳到下一个比赛
                i += 2
                continue
            i += 1

    return all_matches


def _parse_sections(lines, start_idx):
    """解析4个赔率section"""
    sections = {'wdl': [], 'handicap': [], 'handicap_line': 0, 'score': [], 'total_goals': []}
    current_section = None
    current_ts = None

    i = start_idx
    while i < len(lines):
        line = lines[i].strip()

        # 检测下一个比赛开始
        if re.match(r'2026/2027 Regular Season', line):
            break

        # 检测section切换
        if line.startswith('胜平负固定奖金'):
            current_section = 'wdl'
            i += 1
            continue
        elif line.startswith('让球胜平负固定奖金'):
            current_section = 'handicap'
            i += 1
            continue
        elif line.startswith('比分固定奖金'):
            current_section = 'score'
            i += 1
            continue
        elif line.startswith('总进球固定奖金'):
            current_section = 'total_goals'
            i += 1
            continue

        if current_section == 'wdl':
            # 胜平负: 发布时间	胜	平	负
            m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', line)
            if m:
                sections['wdl'].append({
                    'ts': m.group(1), 'win': safe_float(m.group(2)),
                    'draw': safe_float(m.group(3)), 'lose': safe_float(m.group(4))
                })

        elif current_section == 'handicap':
            if line.startswith('让球'):
                m = re.match(r'让球([+-])(\d+)', line)
                if m:
                    sections['handicap_line'] = -int(m.group(2)) if m.group(1) == '-' else int(m.group(2))
            else:
                m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', line)
                if m:
                    sections['handicap'].append({
                        'ts': m.group(1), 'win': safe_float(m.group(2)),
                        'draw': safe_float(m.group(3)), 'lose': safe_float(m.group(4))
                    })

        elif current_section == 'score':
            ts_match = re.match(r'发布时间\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})', line)
            if ts_match:
                current_ts = f"{ts_match.group(1)} {ts_match.group(2)}"
                i += 1
                continue

            if current_ts and ' : ' in line:
                scores = [s.strip().replace(' ', '') for s in line.split('\t') if s.strip() and ' : ' in s]
                if i + 1 < len(lines):
                    odds_line = lines[i + 1].strip()
                    odds_vals = [o.strip() for o in odds_line.split('\t') if o.strip()]
                    for j, score in enumerate(scores):
                        if j < len(odds_vals):
                            od = safe_float(odds_vals[j])
                            if od:
                                sections['score'].append({'ts': current_ts, 'score': score, 'odds': od})
                    i += 1

        elif current_section == 'total_goals':
            m = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)', line)
            if m:
                sections['total_goals'].append({
                    'ts': m.group(1),
                    'goals': [safe_float(m.group(j)) for j in range(2, 10)]
                })

        i += 1

    return sections


def import_to_timing_db(matches):
    """导入到odds_timing.db"""
    conn = sqlite3.connect(TIMING_DB)
    c = conn.cursor()

    new_matches = 0
    new_wdl = 0
    new_hcp = 0
    new_score = 0
    new_tg = 0

    for m in matches:
        mid = m['match_id']

        # 插入比赛
        c.execute("SELECT COUNT(*) FROM matches WHERE match_id=?", (mid,))
        if c.fetchone()[0] == 0:
            try:
                c.execute("""
                    INSERT INTO matches (match_id, home_team, away_team, match_date, match_time, league, league_code, source, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (mid, m['home_team'], m['away_team'], m['match_date'], m['match_time'],
                      m['league'], m['league_code'], m['source']))
                new_matches += 1
            except Exception as e:
                print(f"  [ERROR] matches: {mid} - {e}")

        # WDL
        for w in m['wdl']:
            c.execute("SELECT COUNT(*) FROM wdl_timing WHERE match_id=? AND timestamp=?", (mid, w['ts']))
            if c.fetchone()[0] == 0:
                try:
                    c.execute("""
                        INSERT INTO wdl_timing (match_id, timestamp, win_a, draw, win_b, source, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (mid, w['ts'], w['win'], w['draw'], w['lose'], m['source']))
                    new_wdl += 1
                except Exception as e:
                    print(f"  [ERROR] wdl: {mid} - {e}")

        # Handicap
        for h in m['handicap']:
            c.execute("SELECT COUNT(*) FROM handicap_timing WHERE match_id=? AND timestamp=?", (mid, h['ts']))
            if c.fetchone()[0] == 0:
                try:
                    c.execute("""
                        INSERT INTO handicap_timing (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose, source, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (mid, h['ts'], m['handicap_line'], h['win'], h['draw'], h['lose'], m['source']))
                    new_hcp += 1
                except Exception as e:
                    print(f"  [ERROR] hcp: {mid} - {e}")

        # Score
        for s in m['score']:
            c.execute("SELECT COUNT(*) FROM score_timing WHERE match_id=? AND timestamp=? AND score=?", (mid, s['ts'], s['score']))
            if c.fetchone()[0] == 0:
                try:
                    c.execute("""
                        INSERT INTO score_timing (match_id, timestamp, score, odds, source, created_at)
                        VALUES (?, ?, ?, ?, ?, datetime('now'))
                    """, (mid, s['ts'], s['score'], s['odds'], m['source']))
                    new_score += 1
                except Exception as e:
                    print(f"  [ERROR] score: {mid} - {e}")

        # Total Goals
        for tg in m['total_goals']:
            c.execute("SELECT COUNT(*) FROM total_goals_timing WHERE match_id=? AND timestamp=?", (mid, tg['ts']))
            if c.fetchone()[0] == 0:
                try:
                    g = tg['goals'] + [None] * (8 - len(tg['goals']))
                    c.execute("""
                        INSERT INTO total_goals_timing (match_id, timestamp, goals_0, goals_1, goals_2, goals_3,
                            goals_4, goals_5, goals_6, goals_7_plus, source, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """, (mid, tg['ts'], g[0], g[1], g[2], g[3], g[4], g[5], g[6], g[7], m['source']))
                    new_tg += 1
                except Exception as e:
                    print(f"  [ERROR] tg: {mid} - {e}")

    conn.commit()
    conn.close()

    print(f"\n导入完成: matches={new_matches}, WDL={new_wdl}, HCP={new_hcp}, Score={new_score}, TG={new_tg}")


def main():
    print("=" * 60)
    print("导入 26/27 赛季五大联赛时序赔率 → odds_timing.db")
    print("=" * 60)

    print("\n1. 构建中文→英文队名映射...")
    cn_to_en = build_cn_to_en()

    print("\n2. 解析4个txt文件...")
    all_matches = parse_all_txt_files(cn_to_en)
    print(f"   共解析 {len(all_matches)} 场比赛")

    for m in all_matches:
        print(f"   {m['match_id']} | {m['league_code']} | WDL={len(m['wdl'])} HCP={len(m['handicap'])} Score={len(m['score'])} TG={len(m['total_goals'])}")

    print("\n3. 导入 odds_timing.db...")
    import_to_timing_db(all_matches)

    print("\n完成!")


if __name__ == '__main__':
    main()