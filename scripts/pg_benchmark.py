# -*- coding: utf-8 -*-
"""P2-11: SQLite vs PostgreSQL 性能基准测试

模拟实际工作负载的 5 类典型查询, 各跑 N 轮取中位数:
  Q1 赛程过滤: matches 按 league + 日期范围 (特征工程入口)
  Q2 球员近况: match_player_stats 按球队取最近 N 场 (PA 特征核心查询)
  Q3 赔率时序: score_history 按 match_id (盘口变化轨迹)
  Q4 分区裁剪 JOIN: mps JOIN fbref_match_mapping (物化 match_date 优势)
  Q5 多公司聚合: odds500_ouzhi_company 按 match_id 汇总
"""
from __future__ import annotations

import sqlite3
import statistics
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "pylibs"))

from pg_migrate import pg_connect, SQLITE_PATH  # noqa: E402

ROUNDS = 20

# 每个查询: (名称, sqlite_sql+params, pg_sql+params)
# 日期参数用 fbref_match_mapping 实际日期范围
BENCHMARKS = [
    ("Q1 赛程过滤(league+日期范围)",
     "SELECT * FROM matches WHERE league = ? AND match_date BETWEEN ? AND ?",
     ("英超", "2024-01-01", "2025-06-30"),
     "SELECT * FROM matches WHERE league = %s AND match_date BETWEEN %s AND %s",
     ("英超", "2024-01-01", "2025-06-30")),

    ("Q2 球员近况(球队最近20场, PA特征)",
     """SELECT mps.* FROM match_player_stats mps
        JOIN fbref_match_mapping fmm ON mps.fbref_match_id = fmm.fbref_match_id
        WHERE mps.team = ? AND fmm.match_date <= ?
        ORDER BY fmm.match_date DESC LIMIT 500""",
     ("Liverpool FC", "2025-06-30"),
     """SELECT * FROM match_player_stats
        WHERE team = %s AND match_date <= %s
        ORDER BY match_date DESC LIMIT 500""",
     ("Liverpool FC", "2025-06-30")),

    ("Q3 赔率时序(score_history by match)",
     "SELECT * FROM score_history WHERE match_id = ? ORDER BY \"timestamp\"",
     ("2025-01-04_利物浦_曼联",),
     'SELECT * FROM score_history WHERE match_id = %s ORDER BY "timestamp"',
     ("2025-01-04_利物浦_曼联",)),

    ("Q4 聚合统计(球员场均数据 GROUP BY)",
     """SELECT mps.player_name, COUNT(*), AVG(mps.minutes_played)
        FROM match_player_stats mps
        JOIN fbref_match_mapping fmm ON mps.fbref_match_id = fmm.fbref_match_id
        WHERE mps.team = ? AND fmm.match_date >= ?
        GROUP BY mps.player_name ORDER BY COUNT(*) DESC LIMIT 30""",
     ("Arsenal", "2024-08-01"),
     """SELECT player_name, COUNT(*), AVG(minutes_played)
        FROM match_player_stats
        WHERE team = %s AND match_date >= %s
        GROUP BY player_name ORDER BY COUNT(*) DESC LIMIT 30""",
     ("Arsenal", "2024-08-01")),

    ("Q5 多公司赔率聚合(ouzhi)",
     "SELECT company, COUNT(*), AVG(init_win) FROM odds500_ouzhi_company WHERE match_id = ? GROUP BY company",
     ("2024-08-17_曼联_富勒姆",),
     "SELECT company, COUNT(*), AVG(init_win) FROM odds500_ouzhi_company WHERE match_id = %s GROUP BY company",
     ("2024-08-17_曼联_富勒姆",)),

    ("Q6 全表分析(全员赛季汇总, PA批处理)",
     """SELECT mps.team, COUNT(*), SUM(mps.goals), SUM(mps.assists),
               AVG(mps.xg), AVG(mps.xa), AVG(mps.minutes_played)
        FROM match_player_stats mps
        JOIN fbref_match_mapping fmm ON mps.fbref_match_id = fmm.fbref_match_id
        WHERE fmm.match_date >= ?
        GROUP BY mps.team ORDER BY COUNT(*) DESC""",
     ("2023-08-01",),
     """SELECT team, COUNT(*), SUM(goals), SUM(assists),
               AVG(xg), AVG(xa), AVG(minutes_played)
        FROM match_player_stats
        WHERE match_date >= %s
        GROUP BY team ORDER BY COUNT(*) DESC""",
     ("2023-08-01",)),
]


def bench_sqlite(sql: str, params: tuple) -> tuple[float, int]:
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.execute("PRAGMA cache_size = -64000")  # 64MB cache, 给 SQLite 最优条件
    best, n = 1e9, 0
    times = []
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        rows = conn.execute(sql, params).fetchall()
        dt = time.perf_counter() - t0
        times.append(dt)
        n = len(rows)
    conn.close()
    return statistics.median(times), n


def bench_pg(sql: str, params: tuple) -> tuple[float, int]:
    conn = pg_connect()
    cur = conn.cursor()
    times, n = [], 0
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        cur.execute(sql, params)
        rows = cur.fetchall()
        dt = time.perf_counter() - t0
        times.append(dt)
        n = len(rows)
    conn.close()
    return statistics.median(times), n


def main():
    # 先校准参数中的真实 match_id, 避免 Q3/Q5 空结果失真
    sq = sqlite3.connect(str(SQLITE_PATH))
    mid_score = sq.execute(
        "SELECT match_id FROM score_history GROUP BY match_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
    mid_ouzhi = sq.execute(
        "SELECT match_id FROM odds500_ouzhi_company GROUP BY match_id ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
    sq.close()
    BENCHMARKS[2] = (BENCHMARKS[2][0], BENCHMARKS[2][1], (mid_score,),
                     BENCHMARKS[2][3], (mid_score,))
    BENCHMARKS[4] = (BENCHMARKS[4][0], BENCHMARKS[4][1], (mid_ouzhi,),
                     BENCHMARKS[4][3], (mid_ouzhi,))

    print(f"{'查询':44s} {'SQLite(ms)':>10s} {'PG(ms)':>9s} {'加速比':>7s} {'行数':>7s}")
    print("-" * 84)
    total_speedup = []
    for name, sq_sql, sq_p, pg_sql, pg_p in BENCHMARKS:
        t_sq, n_sq = bench_sqlite(sq_sql, sq_p)
        t_pg, n_pg = bench_pg(pg_sql, pg_p)
        speedup = t_sq / t_pg if t_pg > 0 else float("inf")
        total_speedup.append(speedup)
        flag = "[OK]" if speedup >= 5 else ("[~5x]" if speedup >= 1 else "[SLOW]")
        print(f"{name:44s} {t_sq * 1000:>10.1f} {t_pg * 1000:>9.1f} {speedup:>6.1f}x {n_sq:>7} {flag}")
    print("-" * 84)
    geo = statistics.geometric_mean([s for s in total_speedup if s > 0])
    print(f"几何平均加速比: {geo:.1f}x  (目标 ≥5x)")


if __name__ == "__main__":
    main()
