# -*- coding: utf-8 -*-
"""
清理 matches 表：只保留 fbref_match_mapping 中存在的比赛
将杯赛/非联赛比赛移到 matches_archive 表
"""
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

def cleanup():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("=" * 70)
    print("🧹 matches 表清理：只保留 fbref_match_mapping 中的比赛")
    print("=" * 70)
    
    # 1. 统计
    total_matches = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"\n📊 清理前: matches 表 {total_matches} 场")
    
    # 2. 获取 fbref_match_mapping 中的 odds_match_id
    fbref_ids = set()
    for row in cur.execute("SELECT odds_match_id FROM fbref_match_mapping"):
        fbref_ids.add(row[0])
    print(f"   fbref_match_mapping 基准: {len(fbref_ids)} 场")
    
    # 3. 查找 matches 中不在 fbref 的比赛
    to_remove = []
    for row in cur.execute("SELECT id, match_id, match_date, match_type, home_team, away_team FROM matches"):
        mid, match_id, match_date, match_type, home_team, away_team = row
        if match_id not in fbref_ids:
            to_remove.append(row)
    
    print(f"   需要移除: {len(to_remove)} 场")
    
    if len(to_remove) == 0:
        print("\n✅ matches 表已干净，无需清理!")
        conn.close()
        return
    
    # 4. 显示移除样例
    print(f"\n📋 移除样例 (前 10 场):")
    for i, row in enumerate(to_remove[:10]):
        mid, match_id, match_date, match_type, home_team, away_team = row
        print(f"  {i+1}. {match_date} | {match_type} | {home_team} vs {away_team}")
    
    # 5. 按 match_type 统计移除分布
    print(f"\n📊 移除比赛按 match_type 分布:")
    type_counts = {}
    for row in to_remove:
        mt = row[3]
        # 简化标签
        for league in ["英超", "西甲", "意甲", "德甲", "法甲"]:
            if mt and mt.startswith(league):
                mt = league
                break
        type_counts[mt] = type_counts.get(mt, 0) + 1
    for mt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {mt:30s}: {cnt:>4d} 场")
    
    # 6. 创建归档表并移动数据
    print(f"\n🔧 执行清理...")
    
    # 创建归档表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS matches_archive (
            id INTEGER PRIMARY KEY,
            match_id TEXT,
            home_team TEXT,
            away_team TEXT,
            match_date TEXT,
            match_type TEXT,
            handicap REAL,
            actual_wdl TEXT,
            actual_handicap TEXT,
            actual_score TEXT,
            actual_total_goals INTEGER,
            created_at TEXT,
            updated_at TEXT,
            archived_at TEXT,
            archive_reason TEXT DEFAULT 'not_in_fbref_match_mapping'
        )
    """)
    
    # 移动数据
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ids_to_remove = [str(row[0]) for row in to_remove]
    
    # 分批处理（SQLite 参数限制）
    batch_size = 500
    for i in range(0, len(ids_to_remove), batch_size):
        batch = ids_to_remove[i:i+batch_size]
        placeholders = ",".join(["?"] * len(batch))
        
        # 复制到归档表
        cur.execute(f"""
            INSERT INTO matches_archive 
                (id, match_id, home_team, away_team, match_date, match_type,
                 handicap, actual_wdl, actual_handicap, actual_score, 
                 actual_total_goals, created_at, updated_at, archived_at)
            SELECT id, match_id, home_team, away_team, match_date, match_type,
                   handicap, actual_wdl, actual_handicap, actual_score,
                   actual_total_goals, created_at, updated_at, ?
            FROM matches
            WHERE id IN ({placeholders})
        """, [now] + batch)
        
        # 从 matches 删除
        cur.execute(f"DELETE FROM matches WHERE id IN ({placeholders})", batch)
    
    conn.commit()
    
    # 7. 验证
    remaining = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    archived = cur.execute("SELECT COUNT(*) FROM matches_archive").fetchone()[0]
    
    print(f"\n{'='*70}")
    print(f"✅ 清理完成!")
    print(f"   matches 表: {remaining} 场 (保留)")
    print(f"   matches_archive: {archived} 场 (归档)")
    print(f"   fbref_match_mapping: {len(fbref_ids)} 场 (基准)")
    print(f"   匹配率: {remaining}/{len(fbref_ids)} ({remaining*100/len(fbref_ids):.1f}%)")
    print(f"{'='*70}")
    
    conn.close()

if __name__ == "__main__":
    cleanup()