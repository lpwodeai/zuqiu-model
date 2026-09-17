# -*- coding: utf-8 -*-
"""统计 odds.db 中各联赛完整可用于回测的样本量"""
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
c = sqlite3.connect(str(BASE / "data" / "odds.db"))
c.row_factory = sqlite3.Row

# 1. 获取所有有 WDL+HCP+TG 三项的 match_id
wdl_ids = set(r[0] for r in c.execute("SELECT DISTINCT match_id FROM wdl_history").fetchall())
hcp_ids = set(r[0] for r in c.execute("SELECT DISTINCT match_id FROM handicap_history").fetchall())
tg_ids = set(r[0] for r in c.execute("SELECT DISTINCT match_id FROM total_goals_history").fetchall())
all3 = wdl_ids & hcp_ids & tg_ids
print(f"三项齐全 match_id: {len(all3)} 个")

# 2. 按赛季分组
from collections import defaultdict
seasons = defaultdict(list)
for mid in all3:
    parts = mid.split('_')
    if len(parts) >= 3:
        date = parts[0]
        if date.startswith('2023'):
            seasons['23/24'].append(mid)
        elif date.startswith('2024'):
            seasons['24/25'].append(mid)
        elif date.startswith('2025'):
            seasons['25/26'].append(mid)
        elif date.startswith('2026'):
            seasons['26/27'].append(mid)

for s, ids in sorted(seasons.items()):
    print(f"  {s}: {len(ids)} 场")

# 3. 按联赛按赛季分组（用 matches 表的 match_type）
print("\n=== 按联赛/赛季统计（matches 表 match_type）===")
# 先建立 match_id → match_type 映射
mid_to_type = {}
for r in c.execute("SELECT match_id, match_type FROM matches WHERE match_id IS NOT NULL").fetchall():
    mid_to_type[r[0]] = r[1]

# 对三项齐全的 match_id，检查 match_type
league_stats = defaultdict(lambda: defaultdict(int))
for mid in all3:
    mt = mid_to_type.get(mid, 'UNKNOWN')
    # 简化 match_type
    for lg in ['英超', '西甲', '意甲', '德甲', '法甲']:
        if lg in mt:
            mt = mt.split('赛季')[0] if '赛季' in mt else mt
            # 提取赛季
            yr = '23/24' if '2023' in mt else '24/25' if '2024' in mt else '25/26' if '2025' in mt else '26/27' if '2026' in mt else '?'
            league_stats[lg][yr] += 1
            break
    else:
        # 没有 match_type，尝试从 match_id 推断联赛
        league_stats['UNKNOWN'][mid[:20]] += 1

for lg in ['英超', '西甲', '意甲', '德甲', '法甲', 'UNKNOWN']:
    if lg in league_stats:
        years = league_stats[lg]
        if all(isinstance(k, str) and '/' in k for k in years.keys()):
            total = sum(years.values())
            ystr = ' '.join(f'{k}:{v}' for k, v in sorted(years.items()))
            print(f"  {lg}: {total} 场 ({ystr})")
        else:
            print(f"  {lg}: {sum(years.values())} 场 (UNKNOWN match_type)")

# 4. 重点：英超 25/26 三项齐全 + 完赛
print("\n=== 英超 25/26 三项齐全 + 完赛 ===")
# 获取英超25/26所有 match_id
epl_matches = set()
for r in c.execute("SELECT match_id FROM matches WHERE match_type='英超2025-2026赛季' AND actual_score IS NOT NULL AND actual_score!=''").fetchall():
    epl_matches.add(r[0])
print(f"  matches 表英超 25/26 完赛: {len(epl_matches)} 场")

# 三项齐全且 match_id 在 matches 表中
epl_3 = all3 & epl_matches
print(f"  三项齐全 + 在 matches 表: {len(epl_3)} 场")

# 但也检查直接在 wdl_history 中的 match_id（不经过 matches 表映射）
epl_wdl = set()
for r in c.execute("SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE '%阿森纳%' OR match_id LIKE '%利物浦%' OR match_id LIKE '%曼城%' OR match_id LIKE '%切尔西%' OR match_id LIKE '%热刺%' OR match_id LIKE '%曼联%'").fetchall():
    epl_wdl.add(r[0])
epl_25_26 = {m for m in epl_wdl if m.startswith('2025')}
epl_3_direct = epl_25_26 & hcp_ids & tg_ids
print(f"  英超 25/26 三项齐全 (直接 match_id): {len(epl_3_direct)} 场")

# 5. 检查西甲
print("\n=== 西甲 ===")
laliga_wdl = set()
laliga_teams = ['皇马','巴塞罗那','马竞','赫罗纳','皇家社会','毕尔巴鄂','贝蒂斯','比利亚雷亚尔','塞维利亚','瓦伦西亚','西班牙人','奥萨苏纳','赫塔费','阿拉维斯','巴列卡诺','塞尔塔','马洛卡','莱加内斯','巴拉多利德','拉斯帕尔马斯']
for t in laliga_teams:
    rows = c.execute("SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE ?", (f"%{t}%",)).fetchall()
    laliga_wdl.update(r[0] for r in rows)
laliga_25_26 = {m for m in laliga_wdl if m.startswith('2025')}
laliga_3 = laliga_25_26 & hcp_ids & tg_ids
print(f"  西甲 25/26 三项齐全: {len(laliga_3)} 场")

c.close()