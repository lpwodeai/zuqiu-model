# -*- coding: utf-8 -*-
"""回填历史场次的 t006_score_predictor_v5 分数网格落库（幂等）。

用法：
  python backfill_t006_score_persistence.py                    # 全量（幂等）
  python backfill_t006_score_persistence.py 2026-09-08 2026-09-21  # 指定比赛日窗口（C-20261006-005 缺口回填）
"""
import sys
import time
import score_prediction_module as s

since = sys.argv[1] if len(sys.argv) > 1 else None
until = sys.argv[2] if len(sys.argv) > 2 else None

t0 = time.time()
results = s.run_full_score_backtest(persist=True, since=since, until=until)
elapsed = time.time() - t0

c = s.sqlite3.connect(str(s.DB_PATH))
cur = c.cursor()
cur.execute("SELECT COUNT(*) FROM model_predictions WHERE model_name='t006_score_predictor_v5'")
total_rows = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT match_id) FROM model_predictions WHERE model_name='t006_score_predictor_v5'")
total_matches = cur.fetchone()[0]
c.close()

print(f"窗口: {since or 'ALL'} ~ {until or 'ALL'}")
print(f"总场次处理: {len(results)}")
print(f"落库行数(全部): {total_rows}")
print(f"落库场次(DISTINCT): {total_matches}")
print(f"耗时: {elapsed:.1f}s")
print("BACKFILL DONE")
