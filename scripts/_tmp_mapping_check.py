# -*- coding: utf-8 -*-
import sqlite3
c = sqlite3.connect('data/odds.db')
c.row_factory = sqlite3.Row
OLD = '2026-09-07_Deportivo Alavés_Osasuna'

print("=== match_mapping* 相关 ===")
for t in ('match_mapping','match_mapping_old','match_mapping_sync','fbref_match_mapping','match_id_mapping'):
    try:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
        rows = []
        for col in cols:
            try:
                rr = c.execute(f"SELECT * FROM {t} WHERE CAST({col} AS TEXT) LIKE ?", ('%'+OLD+'%',)).fetchall()
                if rr:
                    rows.append((col, [dict(x) for x in rr]))
            except Exception:
                pass
        print(f"  {t}: {cols}")
        for col, rr in rows:
            print(f"    [{col}] {len(rr)} 行 -> {rr[:2]}")
    except Exception as e:
        print(f"  {t}: {e}")

print("\n=== post_match_review.attribution_json 中嵌入的 match_id ===")
r = c.execute("SELECT attribution_json FROM post_match_review WHERE match_id=?", (OLD,)).fetchone()
print("  ", r['attribution_json'][:200] if r else "无")