# -*- coding: utf-8 -*-
import sqlite3, pandas as pd, warnings, numpy as np
warnings.filterwarnings('ignore')
c = sqlite3.connect(r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db')
for t, cols in [
    ('wdl_history', ['win_a','draw','win_b']),
    ('handicap_history', ['hcp_win','hcp_draw','hcp_lose']),
    ('total_goals_history', ['goals_0','goals_1','goals_2','goals_3','goals_4','goals_5','goals_6','goals_7_plus']),
]:
    df = pd.read_sql(f"SELECT {', '.join(cols)} FROM {t}", c)
    print(f"\n=== {t} ({len(df)} rows) ===")
    for col in cols:
        s = pd.to_numeric(df[col], errors='coerce')
        print(f"  {col}: null={s.isna().sum()} <=0={(s<=0).sum()} min={s.min()} max={s.max()}")
print('\n=== matches completed (actual_score not null) count ===')
print(pd.read_sql('SELECT COUNT(*) n FROM matches WHERE actual_score IS NOT NULL', c).to_string())
print('match_id null among completed:', pd.read_sql('SELECT SUM(CASE WHEN match_id IS NULL THEN 1 ELSE 0 END) n FROM matches WHERE actual_score IS NOT NULL', c).to_string())