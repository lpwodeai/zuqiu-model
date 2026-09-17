# -*- coding: utf-8 -*-
"""验证 build_match_alignment 修复后对齐率"""
import sqlite3, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts'))
from feature_utils import build_match_alignment

c = sqlite3.connect(r'data\odds.db')
for t in ['wdl_history', 'handicap_history', 'total_goals_history', 'score_history']:
    n_distinct = c.execute(f"SELECT COUNT(DISTINCT match_id) FROM {t}").fetchone()[0]
    align = build_match_alignment(c, t)
    print(f"{t}: 去重={n_distinct}, 对齐={len(align)} ({len(align)/max(n_distinct,1)*100:.2f}%)")
c.close()
print("\nOK")