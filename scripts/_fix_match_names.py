"""修复 matches 表中意甲26/27赛季队名：英文 → 中文"""
import sqlite3
import os

# 队名映射 (SofaScore英文 → 中文)
NAME_MAP = {
    'Inter': '国际米兰', 'AC Milan': 'AC米兰', 'Juventus': '尤文图斯',
    'SSC Napoli': '那不勒斯', 'AS Roma': '罗马', 'Lazio': '拉齐奥',
    'Atalanta': '亚特兰大', 'Fiorentina': '佛罗伦萨', 'Bologna': '博洛尼亚',
    'Torino': '都灵', 'Udinese': '乌迪内斯', 'Genoa': '热那亚',
    'Hellas Verona': '维罗纳', 'Lecce': '莱切', 'Cagliari': '卡利亚里',
    'Empoli': '恩波利', 'Monza': '蒙扎', 'Sassuolo': '萨索洛',
    'Como': '科莫', 'Parma': '帕尔马', 'Frosinone': '弗罗西诺内',
    'Venezia': '威尼斯', 'Salernitana': '萨勒尼塔纳', 'Spezia': '斯佩齐亚',
    'Cremonese': '克雷莫纳', 'Pisa': '比萨',
}

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
conn = sqlite3.connect(DB)

updated = 0
for season in ['意甲2026-2027赛季', '英超2026-2027赛季', '西甲2026-2027赛季', '法甲2026-2027赛季', '德甲2026-2027赛季']:
    rows = conn.execute("SELECT id, match_id, home_team, away_team FROM matches WHERE match_type=?", (season,)).fetchall()
    for row_id, match_id, home, away in rows:
        home_cn = NAME_MAP.get(home, home)
        away_cn = NAME_MAP.get(away, away)
        if home_cn != home or away_cn != away:
            new_match_id = f"{match_id.split('_')[0]}_{home_cn}_{away_cn}"
            conn.execute("UPDATE matches SET home_team=?, away_team=?, match_id=? WHERE id=?",
                        (home_cn, away_cn, new_match_id, row_id))
            updated += 1
            print(f"  {home} → {home_cn}, {away} → {away_cn}")

conn.commit()
print(f"\n共更新 {updated} 条记录")
conn.close()