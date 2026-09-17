import sqlite3
conn = sqlite3.connect(r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db')
cur = conn.cursor()
cur.execute("SELECT sql FROM sqlite_master WHERE type='table'")
for r in cur.fetchall():
    print(r[0])
    print("---")
conn.close()