# -*- coding: utf-8 -*-
"""
从 fbref_match_mapping 填充 matches 表
将缺失的 24/25 和 25/26 赛季数据写入 matches 表
"""
import sqlite3
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

LEAGUE_SEASON_MAP = {
    ("英超", "23/24"): "英超2023-2024赛季",
    ("西甲", "23/24"): "西甲2023-2024赛季",
    ("意甲", "23/24"): "意甲2023-2024赛季",
    ("德甲", "23/24"): "德甲2023-2024赛季",
    ("法甲", "23/24"): "法甲2023-2024赛季",
    ("英超", "24/25"): "英超2024-2025赛季",
    ("西甲", "24/25"): "西甲2024-2025赛季",
    ("意甲", "24/25"): "意甲2024-2025赛季",
    ("德甲", "24/25"): "德甲2024-2025赛季",
    ("法甲", "24/25"): "法甲2024-2025赛季",
    ("英超", "25/26"): "英超2025-2026赛季",
    ("西甲", "25/26"): "西甲2025-2026赛季",
    ("意甲", "25/26"): "意甲2025-2026赛季",
    ("德甲", "25/26"): "德甲2025-2026赛季",
    ("法甲", "25/26"): "法甲2025-2026赛季",
}


def parse_score(score_str):
    if not score_str:
        return None, None, None
    m = re.match(r"(\d+)\s*[-–:]\s*(\d+)", score_str)
    if not m:
        return None, None, None
    hg, ag = int(m.group(1)), int(m.group(2))
    if hg > ag:
        wdl = "胜"
    elif hg < ag:
        wdl = "负"
    else:
        wdl = "平"
    return f"{hg}-{ag}", wdl, hg + ag


def populate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("=" * 70)
    print("📥 从 fbref_match_mapping 填充 matches 表")
    print("=" * 70)
    
    # 获取现有 match_id
    existing_ids = set()
    for row in cur.execute("SELECT match_id FROM matches"):
        existing_ids.add(row[0])
    print(f"\nmatches 表现有: {len(existing_ids)} 条")
    
    # 获取 fbref 数据
    fbref_rows = cur.execute("""
        SELECT odds_match_id, league, season, match_date, 
               home_team_cn, away_team_cn, fbref_score
        FROM fbref_match_mapping
        ORDER BY match_date, league
    """).fetchall()
    print(f"fbref_match_mapping: {len(fbref_rows)} 条")
    
    # 构建 match_id
    inserted = 0
    skipped = 0
    no_score = 0
    
    for row in fbref_rows:
        fbref_id, league, season, match_date, home_cn, away_cn, score = row
        
        # 构建 match_id
        match_id = f"{match_date}_{home_cn}_{away_cn}"
        
        if match_id in existing_ids:
            skipped += 1
            continue
        
        # 解析比分
        actual_score, actual_wdl, actual_total = parse_score(score)
        if actual_score is None:
            no_score += 1
            continue
        
        # 构建 match_type
        match_type = LEAGUE_SEASON_MAP.get((league, season), f"{league}{season.replace('/', '-')}赛季")
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            cur.execute("""
                INSERT INTO matches 
                    (match_id, home_team, away_team, match_date, match_type,
                     actual_score, actual_wdl, actual_total_goals, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (match_id, home_cn, away_cn, match_date, match_type,
                  actual_score, actual_wdl, actual_total, now, now))
            inserted += 1
            existing_ids.add(match_id)
        except Exception as e:
            if inserted < 5:
                print(f"  ⚠️ 插入失败: {match_id} - {e}")
    
    conn.commit()
    
    print(f"\n📊 结果:")
    print(f"  fbref 源数据: {len(fbref_rows)} 条")
    print(f"  新增插入: {inserted} 条")
    print(f"  跳过 (已存在): {skipped} 条")
    print(f"  跳过 (无比分): {no_score} 条")
    
    # 验证
    total = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"\n📊 matches 表最终: {total} 条")
    
    # 赛季分布
    for season_start in ["2023", "2024", "2025"]:
        cnt = cur.execute(
            "SELECT COUNT(*) FROM matches WHERE match_date >= ? AND match_date < ?",
            (f"{season_start}-08-01", f"{int(season_start)+1}-08-01")
        ).fetchone()[0]
        print(f"  {season_start}-{int(season_start)+1}: {cnt} 场")
    
    conn.close()
    print("\n✅ 填充完成!")


if __name__ == "__main__":
    populate()