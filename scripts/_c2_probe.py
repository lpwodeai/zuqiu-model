import sqlite3
c = sqlite3.connect('data/odds.db')
print("模型预测类型:")
for r in c.execute("SELECT prediction_type, COUNT(*) FROM model_predictions GROUP BY prediction_type LIMIT 40"):
    print("  ", r)
print("\n含 actual_score 的 matches 数:")
m = c.execute("SELECT COUNT(*) FROM matches WHERE actual_score IS NOT NULL AND actual_score!=''").fetchone()[0]
print("  ", m)
print("\nmodel_predictions WDL_* match_id 样例:")
for r in c.execute("SELECT match_id, prediction_type, prediction, probability FROM model_predictions WHERE prediction_type LIKE 'WDL%' LIMIT 6"):
    print("  ", r)
print("\nmatches.match_id 样例（前3含 actual_score）:")
for r in c.execute("SELECT match_id, home_team, away_team, actual_score FROM matches WHERE actual_score IS NOT NULL AND actual_score!='' LIMIT 3"):
    print("  ", r)
# 覆盖：matches(有赛果) 中有多少能匹配到 model_predictions 的 WDL_tot 或 WDL_away
cnt = c.execute("""
 SELECT COUNT(DISTINCT m.match_id) FROM matches m
 WHERE m.actual_score IS NOT NULL AND m.actual_score!=''
 AND EXISTS (SELECT 1 FROM model_predictions mp WHERE mp.match_id=m.match_id AND mp.prediction_type='WDL_tot')
""").fetchone()[0]
print("\n有 WDL_tot 预测覆盖的赛果场次:", cnt, "其中概率列样本:")
for r in c.execute("SELECT prediction_type, MIN(probability), MAX(probability), COUNT(*) FROM model_predictions GROUP BY prediction_type"):
    if 'WDL' in (r[0] or '') or 'wdl' in (r[0] or ''):
        print("  ", r)