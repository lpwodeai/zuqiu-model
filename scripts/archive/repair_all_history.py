"""一键全量历史数据修复脚本
================================================
功能:
  1. 回填 pass_completion_pct 字段（从 stats_json 中提取 accuratePass）
  2. 回填 ground_duels_won / ground_duels_lost / ground_duels_total
  3. 回填 duels_total / aerials_total

用法: python repair_all_history.py
"""
import sqlite3
import json
import time
import sys
from collections import defaultdict

DB_PATH = "data/odds.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    print("=" * 70)
    print("一键全量历史数据修复")
    print(f"数据库: {DB_PATH}")
    print("=" * 70)

    t0 = time.time()

    # ---------- Step 0: 统计 ----------
    print("\n【Step 0】数据概览...")
    cur.execute("SELECT COUNT(*) FROM match_player_stats")
    total = cur.fetchone()[0]
    print(f"  总行数: {total:,}")

    stats = {}
    for col in ['pass_completion_pct', 'ground_duels_won', 'ground_duels_lost',
                'ground_duels_total', 'duels_total', 'aerials_total',
                'accurate_pass_sofa']:
        try:
            cnt = cur.execute(
                f"SELECT COUNT(*) FROM match_player_stats WHERE {col} IS NOT NULL"
            ).fetchone()[0]
            stats[col] = cnt
            print(f"  {col:25s}: {cnt:>8,} 行 ({cnt/total*100:5.1f}%)")
        except sqlite3.OperationalError:
            print(f"  {col:25s}: 列不存在，跳过")

    # ---------- Step 1: 回填 pass_completion_pct ----------
    print("\n【Step 1】回填 pass_completion_pct（从 stats_json 提取 accuratePass）...")
    t1 = time.time()

    # 找到有 stats_json 且 pass_completion_pct 为 NULL 的行
    cur.execute("""
        SELECT id, stats_json FROM match_player_stats
        WHERE stats_json IS NOT NULL
          AND (pass_completion_pct IS NULL OR pass_completion_pct = 0)
    """)
    rows = cur.fetchall()
    print(f"  待处理行数: {len(rows):,}")

    updated_pass = 0
    batch = []
    batch_size = 500

    for i, (row_id, stats_json) in enumerate(rows):
        try:
            stats = json.loads(stats_json)
            accurate_pass = stats.get("accuratePass")
            if accurate_pass is not None and accurate_pass != 0:
                batch.append((accurate_pass, row_id))
        except (json.JSONDecodeError, TypeError):
            pass

        if len(batch) >= batch_size:
            cur.executemany(
                "UPDATE match_player_stats SET pass_completion_pct = ? WHERE id = ?",
                batch
            )
            updated_pass += len(batch)
            batch.clear()

        if (i + 1) % 5000 == 0:
            print(f"  进度: {i+1:,}/{len(rows):,} ({(i+1)/len(rows)*100:.1f}%) | 已更新 {updated_pass:,}")

    if batch:
        cur.executemany(
            "UPDATE match_player_stats SET pass_completion_pct = ? WHERE id = ?",
            batch
        )
        updated_pass += len(batch)

    conn.commit()
    elapsed = time.time() - t1
    print(f"  更新: {updated_pass:,} 行 | 耗时: {elapsed:.1f}s")

    # ---------- Step 2: 回填 ground_duels (SQL 批量) ----------
    print("\n【Step 2】回填 ground_duels 字段（SQL 批量计算）...")
    t2 = time.time()

    cur.execute("""
        UPDATE match_player_stats
        SET ground_duels_won = MAX(0,
            duels_won - COALESCE(aerials_won_total, 0)
        )
        WHERE duels_won IS NOT NULL
    """)
    gdw = cur.rowcount
    print(f"  ground_duels_won: {gdw:,} 行")

    cur.execute("""
        UPDATE match_player_stats
        SET ground_duels_lost = MAX(0,
            duels_lost_sofa - COALESCE(aerials_lost_sofa, 0)
        )
        WHERE duels_lost_sofa IS NOT NULL
    """)
    gdl = cur.rowcount
    print(f"  ground_duels_lost: {gdl:,} 行")

    cur.execute("""
        UPDATE match_player_stats
        SET ground_duels_total = COALESCE(ground_duels_won, 0)
                              + COALESCE(ground_duels_lost, 0)
        WHERE duels_won IS NOT NULL OR duels_lost_sofa IS NOT NULL
    """)
    gdt = cur.rowcount
    print(f"  ground_duels_total: {gdt:,} 行")

    conn.commit()
    elapsed2 = time.time() - t2
    print(f"  耗时: {elapsed2:.1f}s")

    # ---------- Step 3: 回填 duels_total / aerials_total ----------
    print("\n【Step 3】回填 duels_total / aerials_total...")
    cur.execute("""
        UPDATE match_player_stats
        SET duels_total = COALESCE(duels_won, 0) + COALESCE(duels_lost_sofa, 0)
        WHERE duels_total IS NULL
          AND (duels_won IS NOT NULL OR duels_lost_sofa IS NOT NULL)
    """)
    dt = cur.rowcount
    print(f"  duels_total: {dt:,} 行")

    cur.execute("""
        UPDATE match_player_stats
        SET aerials_total = COALESCE(aerials_won_total, 0) + COALESCE(aerials_lost_sofa, 0)
        WHERE aerials_total IS NULL
          AND (aerials_won_total IS NOT NULL OR aerials_lost_sofa IS NOT NULL)
    """)
    at = cur.rowcount
    print(f"  aerials_total: {at:,} 行")

    conn.commit()

    # ---------- Step 4: 最终验证 ----------
    print("\n【Step 4】最终验证...")
    total_after = cur.execute("SELECT COUNT(*) FROM match_player_stats").fetchone()[0]

    final_stats = {}
    for col in ['pass_completion_pct', 'ground_duels_won', 'ground_duels_lost',
                'ground_duels_total', 'duels_total', 'aerials_total']:
        try:
            cnt = cur.execute(
                f"SELECT COUNT(*) FROM match_player_stats WHERE {col} IS NOT NULL"
            ).fetchone()[0]
            final_stats[col] = cnt
            before = stats.get(col, 0)
            delta = cnt - before
            pct = cnt / total_after * 100
            delta_str = f"+{delta:,}" if delta >= 0 else f"{delta:,}"
            print(f"  {col:25s}: {cnt:>8,} 行 ({pct:5.1f}%) | {delta_str}")
        except sqlite3.OperationalError:
            pass

    # 一致性校验
    print("\n【一致性校验】")
    inconsistencies = 0
    if 'ground_duels_won' in final_stats:
        cur.execute("""
            SELECT COUNT(*) FROM match_player_stats
            WHERE ground_duels_won IS NOT NULL
              AND ground_duels_won != MAX(0,
                  COALESCE(duels_won,0) - COALESCE(aerials_won_total,0)
              )
        """)
        inconsistencies = cur.fetchone()[0]
        if inconsistencies == 0:
            print("  ✅ ground_duels_won 计算完全一致")
        else:
            print(f"  ⚠️  ground_duels_won 有 {inconsistencies} 行不一致")

    elapsed_total = time.time() - t0
    print(f"\n  总耗时: {elapsed_total:.1f}s")

    # 按联赛分布
    print("\n【按联赛分布 - ground_duels_won】")
    cur.execute("""
        SELECT fmm.league,
               COUNT(*) as total,
               SUM(CASE WHEN ps.ground_duels_won IS NOT NULL THEN 1 ELSE 0 END) as gd
        FROM match_player_stats ps
        JOIN fbref_match_mapping fmm ON ps.match_id = fmm.odds_match_id
        GROUP BY fmm.league
        ORDER BY total DESC
    """)
    print(f"  {'联赛':10s} {'总数':>8s} {'ground_duels':>12s} {'覆盖率':>8s}")
    for r in cur.fetchall():
        pct = r[2] / r[1] * 100 if r[1] > 0 else 0
        print(f"  {r[0]:10s} {r[1]:>8,} {r[2]:>12,} {pct:7.1f}%")

    conn.close()

    print("\n" + "=" * 70)
    print("✅ 全量历史数据修复完成！")
    print("=" * 70)

if __name__ == "__main__":
    main()
