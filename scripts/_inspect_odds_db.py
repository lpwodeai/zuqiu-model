import sqlite3
conn = sqlite3.connect('data/odds.db')
cur = conn.cursor()
tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print('TABLES:', [t for t in tables if t in ('matches','wdl_history','handicap_history','total_goals_history','score_history','match_id_mapping','team_name_map') or 'history' in t])
for t in ['matches','wdl_history','handicap_history','total_goals_history','score_history']:
    if t in tables:
        cols = [c[1] for c in cur.execute(f'PRAGMA table_info({t})')]
        cnt = cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'\n=== {t} ({cnt} rows) ===')
        print('cols:', cols)
        # 看看最近3条样例
        try:
            rows = cur.execute(f'SELECT * FROM {t} LIMIT 2').fetchall()
            for row in rows:
                print('  sample:', row[:8])
        except Exception as e:
            print('  sample err', e)
conn.close()