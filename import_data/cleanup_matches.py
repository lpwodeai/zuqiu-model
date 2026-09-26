# -*- coding: utf-8 -*-
"""
清理 matches 表：只保留 fbref_match_mapping 中存在的比赛
将杯赛/非联赛比赛移到 matches_archive 表

匹配键：match_date + 归一化主队名 + 归一化客队名（使用 team_name_mapping.normalize_team_name）。
原脚本用 match_id 直接对比 fbref_match_mapping.odds_match_id，但前者为中文队名、后者为英文队名，
仅 35.3% 匹配，会误归档 64.7% 比赛。修复后归一化匹配率 98.4%。

默认 dry-run（仅打印不移除），加 --apply 才真正执行。
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
sys.path.insert(0, str(BASE_DIR / "scripts"))

from team_name_mapping import normalize_team_name  # noqa: E402


def _norm(name):
    """归一化队名，失败时返回原名（避免 None 破坏元组比较）。"""
    n = normalize_team_name(name)
    return n if n else (name if name else "")


def _build_fbref_keys(cur):
    """从 fbref_match_mapping 构建 (date, 归一化主队, 归一化客队) 集合。"""
    keys = set()
    for r in cur.execute("SELECT match_date, home_team_cn, away_team_cn FROM fbref_match_mapping"):
        keys.add((r[0], _norm(r[1]), _norm(r[2])))
    return keys


def cleanup(dry_run=True):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    mode = "DRY-RUN（仅预览，不修改）" if dry_run else "APPLY（实际执行）"
    print("=" * 70)
    print(f"🧹 matches 表清理：只保留 fbref_match_mapping 中的比赛  [{mode}]")
    print("=" * 70)

    total_matches = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"\n📊 清理前: matches 表 {total_matches} 场")

    fbref_keys = _build_fbref_keys(cur)
    print(f"   fbref_match_mapping 基准键: {len(fbref_keys)} 个")

    to_remove = []
    for row in cur.execute(
        "SELECT id, match_id, match_date, match_type, home_team, away_team FROM matches"
    ):
        mid, match_id, match_date, match_type, home_team, away_team = row
        key = (match_date, _norm(home_team), _norm(away_team))
        # 同时尝试主客对调（应对数据源主客标注互换的罕见情况）
        key_rev = (match_date, _norm(away_team), _norm(home_team))
        if key not in fbref_keys and key_rev not in fbref_keys:
            to_remove.append(row)

    print(f"   需要移除: {len(to_remove)} 场 ({len(to_remove)*100/total_matches:.1f}%)")

    if len(to_remove) == 0:
        print("\n✅ matches 表已干净，无需清理!")
        conn.close()
        return

    print(f"\n📋 移除样例 (前 10 场):")
    for i, row in enumerate(to_remove[:10]):
        mid, match_id, match_date, match_type, home_team, away_team = row
        print(f"  {i+1}. {match_date} | {match_type} | {home_team} vs {away_team}")

    print(f"\n📊 移除比赛按 match_type 分布:")
    type_counts = {}
    for row in to_remove:
        mt = row[3]
        for league in ["英超", "西甲", "意甲", "德甲", "法甲"]:
            if mt and mt.startswith(league):
                mt = league
                break
        type_counts[mt] = type_counts.get(mt, 0) + 1
    for mt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {mt:30s}: {cnt:>4d} 场")

    if dry_run:
        print(f"\n⏭️  DRY-RUN 模式，未执行移除。如需实际执行请加 --apply 参数。")
        conn.close()
        return

    print(f"\n🔧 执行清理...")

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

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ids_to_remove = [row[0] for row in to_remove]

    batch_size = 500
    for i in range(0, len(ids_to_remove), batch_size):
        batch = ids_to_remove[i:i + batch_size]
        placeholders = ",".join(["?"] * len(batch))

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

        cur.execute(f"DELETE FROM matches WHERE id IN ({placeholders})", batch)

    conn.commit()

    remaining = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    archived = cur.execute("SELECT COUNT(*) FROM matches_archive").fetchone()[0]

    print(f"\n{'='*70}")
    print(f"✅ 清理完成!")
    print(f"   matches 表: {remaining} 场 (保留)")
    print(f"   matches_archive: {archived} 场 (归档)")
    print(f"   fbref_match_mapping 基准键: {len(fbref_keys)} 个")
    print(f"{'='*70}")

    conn.close()


if __name__ == "__main__":
    apply_mode = "--apply" in sys.argv
    cleanup(dry_run=not apply_mode)
