# -*- coding: utf-8 -*-
"""全量回填历史场次的 t006_score_predictor_v5 分数网格落库（幂等）。"""
import time
import score_prediction_module as s

t0 = time.time()
results = s.run_full_score_backtest(persist=True)
elapsed = time.time() - t0

c = s.sqlite3.connect(str(s.DB_PATH))
cur = c.cursor()
cur.execute("SELECT COUNT(*) FROM model_predictions WHERE model_name='t006_score_predictor_v5'")
total_rows = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT match_id) FROM model_predictions WHERE model_name='t006_score_predictor_v5'")
total_matches = cur.fetchone()[0]
c.close()

print(f"总场次处理: {len(results)}")
print(f"落库行数(全部): {total_rows}")
print(f"落库场次(DISTINCT): {total_matches}")
print(f"耗时: {elapsed:.1f}s")
print("BACKFILL DONE")