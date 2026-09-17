import sqlite3
conn = sqlite3.connect('data/odds.db')

# Check actual_wdl values
r = conn.execute("SELECT actual_wdl, COUNT(*) FROM matches WHERE match_type LIKE '%英超%' GROUP BY actual_wdl").fetchall()
print('actual_wdl values:', r)

# Check wdl_history columns
r = conn.execute('PRAGMA table_info(wdl_history)').fetchall()
print('wdl_history columns:', [row[1] for row in r])

# Check actual_wdl encoding
r = conn.execute("SELECT actual_wdl, actual_score, COUNT(*) FROM matches WHERE match_type LIKE '%英超%' GROUP BY actual_wdl LIMIT 10").fetchall()
print('actual_wdl samples:', r[:5])

# Check match_id format
r = conn.execute("SELECT match_id FROM matches WHERE match_type LIKE '%英超%' LIMIT 3").fetchall()
print('match_id samples:', r)

r = conn.execute("SELECT match_id FROM wdl_history LIMIT 3").fetchall()
print('wdl_history match_id samples:', r)

# Check actual_wdl in match table (not match_type filter)
r = conn.execute("SELECT actual_wdl, COUNT(*) FROM matches GROUP BY actual_wdl").fetchall()
print('All actual_wdl values:', r)

conn.close()