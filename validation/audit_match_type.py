# -*- coding: utf-8 -*-
"""
统计 matches 表中 match_type 标签错误分布
赛季定义: 当年8月 ~ 次年5月
  2023-2024: 2023-08-01 ~ 2024-06-30
  2024-2025: 2024-08-01 ~ 2025-06-30
  2025-2026: 2025-08-01 ~ 2026-06-30
"""
import sqlite3
from pathlib import Path
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

def get_correct_season(date_str):
    """根据日期返回正确的赛季标签"""
    if not date_str or not date_str.startswith("20"):
        return "未知"
    year = int(date_str[:4])
    month = int(date_str[5:7])
    if month >= 8:
        return f"{year}-{year+1}"
    else:
        return f"{year-1}-{year}"

def get_correct_match_type(date_str, league):
    """根据日期和联赛返回正确的 match_type"""
    season = get_correct_season(date_str)
    return f"{league}{season}赛季"

# 联赛名简称映射
LEAGUE_ABBR = {
    "英超": "英超", "西甲": "西甲", "意甲": "意甲",
    "德甲": "德甲", "法甲": "法甲"
}

print("=" * 70)
print("📊 matches 表 match_type 错误标签分布")
print("=" * 70)

# 查询所有比赛
rows = conn.execute("""
    SELECT match_id, match_date, match_type, home_team, away_team
    FROM matches
    ORDER BY match_date
""").fetchall()

print(f"总比赛数: {len(rows)}")

# 统计错误
errors = defaultdict(int)  # {old_match_type: {correct_match_type: count}}
correct_by_season = defaultdict(int)
wrong_by_pattern = defaultdict(int)

for row in rows:
    match_id, match_date, match_type, home_team, away_team = row
    
    # 提取联赛名
    league = None
    for abbr in LEAGUE_ABBR:
        if match_type and match_type.startswith(abbr):
            league = abbr
            break
    
    if not league or not match_date:
        continue
    
    correct_type = get_correct_match_type(match_date, league)
    
    if match_type == correct_type:
        correct_by_season[get_correct_season(match_date)] += 1
    else:
        key = f"{match_type} → {correct_type}"
        errors[key] += 1
        wrong_by_pattern[f"{match_type[:10]}* → {correct_type[:10]}*"] += 1

# 输出正确分布
print(f"\n✅ 标签正确的比赛:")
for s in sorted(correct_by_season.keys()):
    print(f"  {s}: {correct_by_season[s]:>5d} 场")

# 输出错误 Top 20
print(f"\n❌ 标签错误的比赛 (按错误次数排序，Top 20):")
sorted_errors = sorted(errors.items(), key=lambda x: -x[1])
for i, (key, cnt) in enumerate(sorted_errors[:20]):
    bar = "█" * min(cnt // 10, 40)
    print(f"  {i+1:2d}. {key:50s}  {cnt:>5d} 场 {bar}")

# 按模式汇总
print(f"\n📊 错误模式汇总:")
sorted_patterns = sorted(wrong_by_pattern.items(), key=lambda x: -x[1])
for pattern, cnt in sorted_patterns[:15]:
    print(f"  {pattern:50s}  {cnt:>5d} 场")

# 总览
total_wrong = sum(errors.values())
total_correct = sum(correct_by_season.values())
print(f"\n{'='*70}")
print(f"  标签正确: {total_correct} 场 ({total_correct*100/len(rows):.1f}%)")
print(f"  标签错误: {total_wrong} 场 ({total_wrong*100/len(rows):.1f}%)")
print(f"{'='*70}")

conn.close()