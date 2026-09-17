"""批量回填脚本：用 SQL UPDATE 重算所有历史数据的 ground_duels 字段

公式:
  ground_duels_won  = MAX(0, duels_won        - COALESCE(aerials_won_total,  0))
  ground_duels_lost = MAX(0, duels_lost_sofa  - COALESCE(aerials_lost_sofa,  0))
  ground_duels_total = ground_duels_won + ground_duels_lost

仅更新有 duels 数据的行 (duels_won IS NOT NULL OR duels_lost_sofa IS NOT NULL)

用法: python backfill_ground_duels.py
"""
import sqlite3
import time

DB_PATH = "data/odds.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    print("=" * 70)
    print("批量回填: ground_duels 字段重算")
    print(f"数据库: {DB_PATH}")
    print("=" * 70)

    # ---------- 1. 统计回填前状态 ----------
    print("\n【Step 1】回填前统计...")
    cur.execute("SELECT COUNT(*) FROM match_player_stats")
    total = cur.fetchone()[0]
    print(f"  总行数: {total:,}")

    before = {}
    for col in ['ground_duels_won', 'ground_duels_lost', 'ground_duels_total']:
        cnt = cur.execute(
            f"SELECT COUNT(*) FROM match_player_stats WHERE {col} IS NOT NULL"
        ).fetchone()[0]
        before[col] = cnt
        pct = cnt / total * 100
        print(f"  {col}: {cnt:>7,} 行 ({pct:5.1f}%%)")

    # 可回填的行数
    can_fill = cur.execute("""
        SELECT COUNT(*) FROM match_player_stats
        WHERE duels_won IS NOT NULL OR duels_lost_sofa IS NOT NULL
    """).fetchone()[0]
    print(f"  可回填行数 (有 duels 数据): {can_fill:,}")

    # ---------- 2. 执行回填 ----------
    print("\n【Step 2】执行 SQL UPDATE 回填...")
    t0 = time.time()

    conn.execute("BEGIN IMMEDIATE")

    # ground_duels_won
    cur.execute("""
        UPDATE match_player_stats
        SET ground_duels_won = MAX(0,
            duels_won - COALESCE(aerials_won_total, 0)
        )
        WHERE duels_won IS NOT NULL
    """)
    rows_gdw = cur.rowcount
    print(f"  ground_duels_won: 更新 {rows_gdw:,} 行")

    # ground_duels_lost
    cur.execute("""
        UPDATE match_player_stats
        SET ground_duels_lost = MAX(0,
            duels_lost_sofa - COALESCE(aerials_lost_sofa, 0)
        )
        WHERE duels_lost_sofa IS NOT NULL
    """)
    rows_gdl = cur.rowcount
    print(f"  ground_duels_lost: 更新 {rows_gdl:,} 行")

    # ground_duels_total (依赖上面两个字段)
    cur.execute("""
        UPDATE match_player_stats
        SET ground_duels_total = COALESCE(ground_duels_won, 0)
                              + COALESCE(ground_duels_lost, 0)
        WHERE duels_won IS NOT NULL OR duels_lost_sofa IS NOT NULL
    """)
    rows_gdt = cur.rowcount
    print(f"  ground_duels_total: 更新 {rows_gdt:,} 行")

    conn.commit()
    elapsed = time.time() - t0
    print(f"\n  耗时: {elapsed:.1f}s")

    # ---------- 3. 回填后验证 ----------
    print("\n【Step 3】回填后验证...")
    after = {}
    for col in ['ground_duels_won', 'ground_duels_lost', 'ground_duels_total']:
        cnt = cur.execute(
            f"SELECT COUNT(*) FROM match_player_stats WHERE {col} IS NOT NULL"
        ).fetchone()[0]
        after[col] = cnt
        delta = cnt - before[col]
        pct2 = cnt / total * 100
        print(f"  {col}: {cnt:>7,} 行 ({pct2:5.1f}%%) | +{delta:,}")

    # ---------- 4. 抽样验证 ----------
    print("\n【Step 4】抽样验证 (前 15 行)...")
    cur.execute("""
        SELECT player_name, team,
               duels_won, aerials_won_total, ground_duels_won,
               duels_lost_sofa, aerials_lost_sofa, ground_duels_lost,
               ground_duels_total
        FROM match_player_stats
        WHERE ground_duels_won IS NOT NULL
        LIMIT 15
    """)
    rows = cur.fetchall()
    for r in rows:
        # 验证计算
        expected_won = max(0, (r[2] or 0) - (r[3] or 0))
        expected_lost = max(0, (r[5] or 0) - (r[6] or 0))
        expected_total = expected_won + expected_lost

        ok_won = r[4] == expected_won
        ok_lost = r[7] == expected_lost
        ok_total = r[8] == expected_total
        all_ok = ok_won and ok_lost and ok_total

        status = "✅" if all_ok else "❌"
        gw = r[4] if r[4] is not None else -1
        gl = r[7] if r[7] is not None else -1
        gt = r[8] if r[8] is not None else -1
        print(f"  {r[0]:20s} won={gw:>3}(exp={expected_won}) lost={gl:>3}(exp={expected_lost}) total={gt:>3}(exp={expected_total}) {status}")

    # ---------- 5. 与 duels_won 覆盖率对比 ----------
    print("\n【Step 5】覆盖率对比...")
    cur.execute("SELECT COUNT(*) FROM match_player_stats WHERE duels_won IS NOT NULL")
    duels_cnt = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM match_player_stats WHERE ground_duels_won IS NOT NULL")
    ground_cnt = cur.fetchone()[0]

    d_pct = duels_cnt / total * 100
    g_pct = ground_cnt / total * 100
    print(f"  duels_won 覆盖率:      {d_pct:5.1f}%% ({duels_cnt:,})")
    print(f"  ground_duels_won 覆盖率: {g_pct:5.1f}%% ({ground_cnt:,})")

    if duels_cnt > 0:
        ratio = ground_cnt / duels_cnt * 100
        print(f"  ground/duels 比例: {ratio:.1f}%")
        if ratio >= 95:
            print("  ✅ 回填成功！覆盖率与 duels_won 基本持平")
        elif ratio >= 80:
            print("  ⚠️ 回填部分成功，约 80% 覆盖")
        else:
            print("  ❌ 覆盖率偏低，需排查")

    conn.close()
    print("\n" + "=" * 70)
    print("回填完成！")
    print("=" * 70)

if __name__ == "__main__":
    main()
