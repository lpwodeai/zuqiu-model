# -*- coding: utf-8 -*-
"""
修复 matches 表中英联赛名归一化
将 English names (Premier League, Serie A, La Liga, Bundesliga, Ligue 1)
统一为标准中文名 (英超, 西甲, 意甲, 德甲, 法甲)
根据 match_date 推算正确的赛季后缀
"""
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

# 英文名 → 中文名映射
EN_TO_CN = {
    "Premier League": "英超",
    "Serie A":        "意甲",
    "La Liga":        "西甲",
    "Bundesliga":     "德甲",
    "Ligue 1":        "法甲",
}

# 中文名变体归一化（去掉赛季后缀）
CN_NORMALIZE = {
    "英超": "英超",
    "西甲": "西甲",
    "意甲": "意甲",
    "德甲": "德甲",
    "法甲": "法甲",
}

def get_season(date_str):
    """根据日期返回赛季标签 (YYYY-YYYY+1)"""
    if not date_str:
        return "未知赛季"
    year = int(date_str[:4])
    month = int(date_str[5:7])
    if month >= 8:
        return f"{year}-{year+1}"
    else:
        return f"{year-1}-{year}"

def get_match_type(cn_name, season):
    """构建标准 match_type: 英超2023-2024赛季"""
    return f"{cn_name}{season}赛季"


def normalize():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("=" * 70)
    print("🔧 matches 表中英联赛名归一化")
    print("=" * 70)
    
    # 1. 统计当前状态
    print("\n📊 修复前 match_type 分布:")
    rows = cur.execute("""
        SELECT match_type, COUNT(*) FROM matches
        GROUP BY match_type
        ORDER BY COUNT(*) DESC
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:35s}: {r[1]:>5d} 场")
    print(f"  联赛种类: {len(rows)}")
    
    total = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"  总比赛数: {total}")
    
    # 2. 查找需要修复的行
    to_fix = []
    all_rows = cur.execute("""
        SELECT id, match_id, match_date, match_type, home_team, away_team 
        FROM matches
    """).fetchall()
    
    for row in all_rows:
        rid, match_id, match_date, match_type, home_team, away_team = row
        
        # 检查是否为英文名
        if match_type in EN_TO_CN:
            cn_name = EN_TO_CN[match_type]
            season = get_season(match_date)
            new_type = get_match_type(cn_name, season)
            to_fix.append((rid, match_type, new_type, match_date))
            continue
        
        # 检查中文名是否已有赛季后缀（应该已经是正确的）
        for cn_key in CN_NORMALIZE:
            if match_type and match_type.startswith(cn_key):
                # 已有正确的中文名+赛季后缀，跳过
                break
    
    print(f"\n📊 需要修复: {len(to_fix)} 条")
    
    if len(to_fix) == 0:
        print("✅ 无需修复!")
        conn.close()
        return
    
    # 3. 显示样例
    print(f"\n📋 修复样例 (前 10 条):")
    for i, (rid, old, new, md) in enumerate(to_fix[:10]):
        print(f"  {i+1}. [{md}] {old} → {new}")
    
    # 4. 执行修复
    print(f"\n🔧 执行修复...")
    updated = 0
    for rid, old, new, md in to_fix:
        cur.execute("UPDATE matches SET match_type = ?, updated_at = ? WHERE id = ?",
                    (new, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), rid))
        updated += 1
    
    conn.commit()
    
    # 5. 验证
    print(f"\n📊 修复后 match_type 分布:")
    rows = cur.execute("""
        SELECT match_type, COUNT(*) FROM matches
        GROUP BY match_type
        ORDER BY match_type
    """).fetchall()
    for r in rows:
        print(f"  {r[0]:35s}: {r[1]:>5d} 场")
    print(f"  联赛种类: {len(rows)}")
    
    total = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"  总比赛数: {total}")
    
    conn.close()
    print(f"\n✅ 归一化完成! 修复了 {updated} 条记录")


if __name__ == "__main__":
    normalize()