# -*- coding: utf-8 -*-
"""
优化版：将 odds.db 中赔率时序表的中文 match_id 映射到 matches 表的英文 match_id
策略：1. 中文队名→精确英文名  2. 英文队名→标准化  3. 日期+联赛匹配
"""
import sqlite3, json, re
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"

# 1. 从 matches 表建立所有英文队名和 match_id 索引
c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row

# 提取 matches 表的所有 match_id 和元数据
matches_data = {}  # match_id → {home, away, date, match_type}
matches_by_date_home = defaultdict(list)  # (date, home) → [match_id]
matches_by_date_away = defaultdict(list)

for r in c.execute("SELECT match_id, match_type, home_team, away_team, match_date FROM matches WHERE match_id GLOB '*-*-*_*_*'").fetchall():
    mid = r[0]
    parts = mid.split("_")
    if len(parts) >= 3:
        date = parts[0]
        home = parts[1]
        away = parts[2] if len(parts) == 3 else "_".join(parts[2:])
        hlower = home.lower().strip()
        alower = away.lower().strip()
        matches_data[mid] = {"date": date, "home": home, "away": away, "match_type": r[1],
                             "home_lower": hlower, "away_lower": alower}
        matches_by_date_home[(date, hlower)].append(mid)
        matches_by_date_away[(date, alower)].append(mid)

# 提取所有英文队名
en_teams = set()
for d in matches_data.values():
    en_teams.add(d["home"])
    en_teams.add(d["home_lower"])
    en_teams.add(d["away"])
    en_teams.add(d["away_lower"])

print(f"matches 表: {len(matches_data)} 个 match_id, {len(en_teams)} 个队名")

# 2. 加载并修正 name_map
with open(BASE / "assets" / "team_name_map.json", "r", encoding="utf-8") as f:
    name_map = json.load(f)

# 修正：确保 name_map 的值精确匹配 matches 表中的队名
corrected_map = {}
for cn, en_lower in name_map.items():
    en_lower = en_lower.lower().strip()
    # 在 matches 表中找匹配
    found = None
    for en in en_teams:
        if en.lower().strip() == en_lower:
            found = en
            break
    if not found:
        # 模糊匹配
        for en in en_teams:
            el = en.lower().strip()
            if en_lower in el or el in en_lower:
                found = en
                break
    if found:
        corrected_map[cn] = found

print(f"name_map: {len(name_map)} 条 → 修正后 {len(corrected_map)} 条可匹配")

# 3. 建立英文简写→标准英文名映射（处理 history 表中已有的英文名）
en_short_to_full = {}
# 手动常见简写
manual_en = {
    "AC": "AC Milan",
    "Augsburg": "FC Augsburg",
    "Bayern Munich": "FC Bayern München",
    "Brest": "Stade Brestois",
    "Dortmund": "Borussia Dortmund",
    "Ein Frankfurt": "Eintracht Frankfurt",
    "FC Koln": "1. FC Köln",
    "Freiburg": "SC Freiburg",
    "Hamburg": "Hamburger SV",
    "Heidenheim": "1. FC Heidenheim",
    "Hoffenheim": "TSG Hoffenheim",
    "Lens": "RC Lens",
    "Leverkusen": "Bayer 04 Leverkusen",
    "Lyon": "Olympique Lyonnais",
    "M'gladbach": "Borussia M'gladbach",
    "Mainz": "1. FSV Mainz 05",
    "Marseille": "Olympique de Marseille",
    "Milan": "AC Milan",
    "Monaco": "AS Monaco",
    "Napoli": "SSC Napoli",
    "Paris SG": "Paris Saint-Germain",
    "Rennes": "Stade Rennais",
    "Roma": "AS Roma",
    "St Pauli": "FC St. Pauli",
    "Strasbourg": "RC Strasbourg",
    "Stuttgart": "VfB Stuttgart",
    "Union Berlin": "1. FC Union Berlin",
    "Verona": "Hellas Verona",
    "Werder Bremen": "SV Werder Bremen",
    "Wolfsburg": "VfL Wolfsburg",
    "Köln": "1. FC Köln",
    "Bochum": "VfL Bochum 1848",
    "Reims": "Stade de Reims",
}

for short, full in manual_en.items():
    if full in en_teams:
        en_short_to_full[short] = full
        en_short_to_full[short.lower()] = full

# 也加入 matches 表中已有的映射
for en in en_teams:
    en_lower = en.lower().strip()
    # 移除常见前缀
    for prefix in ["fc ", "ac ", "as ", "ssc ", "sc ", "rc ", "sv ", "vfb ", "vfl ", "tsg ", "ud ", "rb ", "1. fc ", "1. fsv ", "bayer 04 ", "borussia ", "olympique ", "stade ", "real ", "deportivo ", "sporting "]:
        if en_lower.startswith(prefix):
            short = en_lower[len(prefix):].strip()
            en_short_to_full[short] = en
            en_short_to_full[short.lower()] = en

# 4. 翻译函数
def translate_match_id(mid_cn):
    """将中文或简写英文 match_id 转换为 matches 表的标准 match_id"""
    parts = mid_cn.split("_")
    if len(parts) < 3:
        return None
    
    date = parts[0]
    home = parts[1]
    away = parts[2] if len(parts) == 3 else "_".join(parts[2:])
    
    # 判断是中/英文
    home_is_cn = any('\u4e00' <= c <= '\u9fff' for c in home)
    away_is_cn = any('\u4e00' <= c <= '\u9fff' for c in away)
    
    if home_is_cn:
        home_en = corrected_map.get(home)
    else:
        home_en = en_short_to_full.get(home) or en_short_to_full.get(home.lower())
        if not home_en and home in en_teams:
            home_en = home
    
    if away_is_cn:
        away_en = corrected_map.get(away)
    else:
        away_en = en_short_to_full.get(away) or en_short_to_full.get(away.lower())
        if not away_en and away in en_teams:
            away_en = away
    
    if not home_en or not away_en:
        return None
    
    mid_en = f"{date}_{home_en}_{away_en}"
    
    # 验证是否在 matches 表中
    if mid_en in matches_data:
        return mid_en
    
    return None

# 5. 执行映射
print("\n执行映射...")
all_cn_mids = set()
for tbl in ['wdl_history', 'handicap_history', 'total_goals_history']:
    for r in c.execute(f"SELECT DISTINCT match_id FROM {tbl}").fetchall():
        all_cn_mids.add(r[0])

print(f"总计: {len(all_cn_mids)} 个中文 match_id")

mid_map = {}
failures = []
for mid in all_cn_mids:
    mid_en = translate_match_id(mid)
    if mid_en:
        mid_map[mid] = mid_en
    else:
        failures.append(mid)

print(f"成功映射: {len(mid_map)}/{len(all_cn_mids)} ({len(mid_map)/len(all_cn_mids)*100:.1f}%)")

# 分析失败原因
if failures:
    print(f"\n失败 ({len(failures)}):")
    # 按球队名分组
    fail_teams = defaultdict(int)
    for mid in failures[:100]:
        parts = mid.split("_")
        if len(parts) >= 3:
            home = parts[1]
            away = parts[2] if len(parts) == 3 else "_".join(parts[2:])
            fail_teams[home] += 1
            fail_teams[away] += 1
    for t, n in sorted(fail_teams.items(), key=lambda x: -x[1])[:20]:
        print(f"  {t}: {n}次")

# 6. 写入数据库
print("\n写入数据库...")
for tbl in ['wdl_history', 'handicap_history', 'total_goals_history']:
    updated = 0
    for mid_cn, mid_en in mid_map.items():
        cur = c.execute(f"UPDATE {tbl} SET match_id_en = ? WHERE match_id = ? AND match_id_en IS NULL", (mid_en, mid_cn))
        updated += cur.rowcount
    
    total = c.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    mapped_n = c.execute(f"SELECT COUNT(*) FROM {tbl} WHERE match_id_en IS NOT NULL").fetchone()[0]
    print(f"  {tbl}: {mapped_n}/{total} 行已映射 ({mapped_n/total*100:.1f}%)")

c.commit()

# 7. 验证
print("\n验证映射...")
matches_mids = set(matches_data.keys())
for tbl in ['wdl_history', 'handicap_history', 'total_goals_history']:
    mapped_mids = set(r[0] for r in c.execute(f"SELECT DISTINCT match_id_en FROM {tbl} WHERE match_id_en IS NOT NULL").fetchall())
    found = mapped_mids & matches_mids
    # 统计 distinct 中文 match_id
    cn_distinct = c.execute(f"SELECT COUNT(DISTINCT match_id) FROM {tbl} WHERE match_id_en IS NOT NULL").fetchone()[0]
    print(f"  {tbl}: {cn_distinct} distinct 中文 match_id → {len(mapped_mids)} distinct 英文, {len(found)} 在 matches 表中")

# 8. 统计三项齐全且可回测的场次
print("\n=== 可回测样本统计 ===")
wdl_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM wdl_history WHERE match_id_en IS NOT NULL").fetchall())
hcp_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM handicap_history WHERE match_id_en IS NOT NULL").fetchall())
tg_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM total_goals_history WHERE match_id_en IS NOT NULL").fetchall())
all3_en = wdl_en & hcp_en & tg_en & matches_mids
print(f"三项齐全 + 在 matches 表: {len(all3_en)} 场")

# 按赛季分组
from collections import Counter
sz = Counter()
for mid in all3_en:
    if mid.startswith('2023'): sz['23/24'] += 1
    elif mid.startswith('2024'): sz['24/25'] += 1
    elif mid.startswith('2025'): sz['25/26'] += 1
    elif mid.startswith('2026'): sz['26/27'] += 1
for s, n in sorted(sz.items()):
    print(f"  {s}: {n} 场")

# 按联赛分组
lg = Counter()
for mid in all3_en:
    mt = matches_data[mid]['match_type']
    for l in ['英超', '西甲', '意甲', '德甲', '法甲']:
        if l in mt:
            lg[l] += 1
            break
    else:
        lg['其他'] += 1
for l, n in sorted(lg.items()):
    print(f"  {l}: {n} 场")

c.close()
print("\n完成!")