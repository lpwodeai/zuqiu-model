# -*- coding: utf-8 -*-
import sqlite3, pandas as pd, warnings
warnings.filterwarnings('ignore')
c = sqlite3.connect(r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db')

print('=== wdl_history columns ===')
print([r[1] for r in c.execute('PRAGMA table_info(wdl_history)').fetchall()])
print('=== mapping tables ===')
print([r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%mapping%'").fetchall()])
print('=== matches older-season match_id ===')
print(pd.read_sql("SELECT match_id, match_date FROM matches WHERE match_date < '2023-08-01' ORDER BY match_date LIMIT 3", c).to_string())
print('=== distinct matches direct-align (Chinese wdl) ===')
print(pd.read_sql('SELECT COUNT(DISTINCT m.match_id) n FROM matches m JOIN wdl_history h ON h.match_id=m.match_id', c).to_string())
cols = [r[1] for r in c.execute('PRAGMA table_info(wdl_history)').fetchall()]
if 'match_id_en' in cols:
    print('=== wdl_history match_id_en samples ===')
    print(pd.read_sql("SELECT match_id, match_id_en FROM wdl_history WHERE match_id_en IS NOT NULL AND match_id_en!='' LIMIT 5", c).to_string())
    print('=== distinct via match_id_en ===')
    print(pd.read_sql('SELECT COUNT(DISTINCT m.match_id) n FROM matches m JOIN wdl_history h ON h.match_id_en=m.match_id', c).to_string())
else:
    print('NO match_id_en in wdl_history')
print('=== match_id_mapping samples ===')
try:
    print(pd.read_sql('SELECT * FROM match_id_mapping LIMIT 5', c).to_string())
    print('=== distinct via bridge ===')
    print(pd.read_sql('SELECT COUNT(DISTINCT m.match_id) n FROM matches m JOIN match_id_mapping mp ON mp.matches_match_id=m.match_id', c).to_string())
except Exception as e:
    print('match_id_mapping err', e)