# -*- coding: utf-8 -*-
"""传球成功率脏数据历史迁移脚本（根因修复）。

背景 / 根因：
  SofaScore 采集器 `final_sofascore_collector.py` 的字段映射表曾把 SofaScore 的
  `accuratePass`（准确传球**数**，整数）错误映射到 `pass_completion_pct` 列
  （该列语义为"传球成功率**百分比**"，且在 FBref 源里确实存 0-100 的百分比），
  导致：
    - `pass_completion_pct` 列被 SofaScore 的准确传球数污染（如 24，而非 88.9%）
    - `accurate_pass_sofa`（准确传球数）列在 SofaScore 源从未赋值，覆盖率仅 ~17%
    - 特征层 `sofascore_pre_match_features.py` 用 accurate_pass_sofa/total_pass_sofa
      计算传球成功率时，分子大量缺失 → 门禁拦截 → 报告误报"未采集"

修复动作：
  1. （已改）collector 映射：accuratePass → accurate_pass_sofa（根因）
  2. （本脚本）历史迁移：将 SofaScore 源 pass_completion_pct 中误存的准确传球数
     迁移到 accurate_pass_sofa，并清空污染的 pass_completion_pct

用法：
  python repair_pass_sr_mapping.py            # dry-run 预览（不写库）
  python repair_pass_sr_mapping.py --apply     # 执行迁移（事务原子）
"""
import sqlite3
import sys
import time

DB_PATH = "data/odds.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def stats_row(conn, label):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN stats_source='sofascore' AND accurate_pass_sofa IS NOT NULL
                     AND accurate_pass_sofa > 0 THEN 1 ELSE 0 END) AS acc_ok,
            SUM(CASE WHEN stats_source='sofascore' AND pass_completion_pct IS NOT NULL
                     THEN 1 ELSE 0 END) AS pct_of_sofa
        FROM match_player_stats
    """)
    r = c.fetchone()
    total = r["total"] or 0
    acc = r["acc_ok"] or 0
    pct = r["pct_of_sofa"] or 0
    print(f"[{label}] sofascore总={total:,}  "
          f"accurate_pass_sofa已有值={acc:,} ({acc/max(total,1)*100:.1f}%)  "
          f"pass_completion_pct污染={pct:,} ({pct/max(total,1)*100:.1f}%)")
    return total, acc, pct


def dry_run(conn):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    print("=" * 72)
    print("DRY-RUN：传球成功率脏数据迁移预览（不写库）")
    print("=" * 72)
    stats_row(conn, "迁移前")

    c.execute("""
        SELECT
            COUNT(*) AS to_migrate,
            SUM(CASE WHEN accurate_pass_sofa IS NOT NULL THEN 1 ELSE 0 END) AS only_clear,
            SUM(CASE WHEN accurate_pass_sofa IS NULL THEN 1 ELSE 0 END) AS actual_migrate
        FROM match_player_stats
        WHERE stats_source = 'sofascore'
          AND pass_completion_pct IS NOT NULL
    """)
    r = c.fetchone()
    print(f"\n  将处理的行总数（pass_completion_pct 非空的 sofascore 行）: {r['to_migrate'] or 0:,}")
    print(f"    ├─ 其中 accurate_pass_sofa 已有值（仅清空 pass_completion_pct）: {r['only_clear'] or 0:,}")
    print(f"    └─ 其中 accurate_pass_sofa 为空（实际迁移数值过去）: {r['actual_migrate'] or 0:,}")

    # 迁移后的预期覆盖
    c.execute("""
        SELECT COUNT(*) FROM match_player_stats
        WHERE stats_source='sofascore' AND accurate_pass_sofa IS NOT NULL
    """)
    cur_acc = c.fetchone()[0] or 0
    c.execute("SELECT COUNT(*) FROM match_player_stats WHERE stats_source='sofascore'")
    total = c.fetchone()[0] or 1
    after_acc = cur_acc + (r["actual_migrate"] or 0)
    print(f"\n  预期迁移后 accurate_pass_sofa 覆盖率: {cur_acc:,} → {after_acc:,} "
          f"({after_acc/total*100:.1f}%)")
    print("\n提示：确认无误后运行 `python repair_pass_sr_mapping.py --apply` 执行。")


def apply(conn):
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    print("=" * 72)
    print("执行迁移（事务原子）")
    print("=" * 72)
    t0 = time.time()

    # 单条 UPDATE 同时完成：迁移数值 + 清空污染列（幂等）
    c.execute("""
        UPDATE match_player_stats
        SET accurate_pass_sofa = COALESCE(accurate_pass_sofa, pass_completion_pct),
            pass_completion_pct = NULL
        WHERE stats_source = 'sofascore'
          AND pass_completion_pct IS NOT NULL
    """)
    affected = c.rowcount
    conn.commit()
    print(f"\n  已更新行数: {affected:,} | 耗时 {time.time()-t0:.1f}s")

    stats_row(conn, "迁移后")

    # 残余污染检查
    c.execute("""
        SELECT COUNT(*) FROM match_player_stats
        WHERE stats_source='sofascore' AND pass_completion_pct IS NOT NULL
    """)
    residual = c.fetchone()[0]
    if residual == 0:
        print("\n  ✅ sofascore 源 pass_completion_pct 已全部清空，无残余污染")
    else:
        print(f"\n  ⚠️ 仍有 {residual} 行 sofascore 源 pass_completion_pct 非空（需排查）")

    # FBref 源完整性确认（16 行正确百分比不应受影响）
    c.execute("""
        SELECT COUNT(*) AS n, ROUND(MIN(pass_completion_pct),1) AS mn,
               ROUND(MAX(pass_completion_pct),1) AS mx
        FROM match_player_stats WHERE stats_source='fbref'
    """)
    r = c.fetchone()
    print(f"  ✅ fbref 源未受影响: {r['n']} 行, pass_completion_pct 值域 {r['mn']}~{r['mx']}")


def main():
    apply_mode = "--apply" in sys.argv
    conn = _connect()
    try:
        if apply_mode:
            apply(conn)
        else:
            dry_run(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()