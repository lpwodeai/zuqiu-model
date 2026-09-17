"""查看CSV赔率详细信息"""
import pandas as pd
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
data_dir = BASE_DIR / "data"

csv_configs = [
    ('EPL', 'EPL_2025-26.csv', 'en'),
    ('Bundesliga', 'BUNDESLIGA_2025-26.csv', 'en'),
    ('LaLiga', 'LALIGA_2025-26.csv', 'zh'),
    ('Ligue1', 'LIGUE1_2025-26.csv', 'zh'),
    ('SerieA', 'SERIEA_2025-26.csv', 'zh'),
]

for league, fname, lang in csv_configs:
    fpath = os.path.join(data_dir, fname)
    if not os.path.exists(fpath):
        continue
    
    df = pd.read_csv(fpath, nrows=3)
    print(f'\n{"="*70}')
    print(f'{league} ({fname}) - {len(df.columns)} 列')
    print(f'{"="*70}')
    
    # 打印所有列名
    print('所有列名:')
    for i, c in enumerate(df.columns):
        print(f'  {i:3d}: {c}')
    
    # 找日期、球队、比分列
    print('\n关键字段:')
    if lang == 'en':
        date_col = 'Date'
        home_col = 'HomeTeam'
        away_col = 'AwayTeam'
        fthg_col = 'FTHG'
        ftag_col = 'FTAG'
        ftr_col = 'FTR'
    else:
        date_col = '日期'
        home_col = '主队'
        away_col = '客队'
        fthg_col = '主队进球'
        ftag_col = '客队进球'
        ftr_col = '赛果'
    
    print(f'  日期: {date_col}')
    print(f'  主队: {home_col}')
    print(f'  客队: {away_col}')
    print(f'  主队进球: {fthg_col}')
    print(f'  客队进球: {ftag_col}')
    print(f'  赛果: {ftr_col}')
    
    # 打印第一行样例
    print('\n第一行样例（前20列）:')
    row0 = df.iloc[0]
    for i, c in enumerate(df.columns[:20]):
        val = row0[c]
        print(f'  {c}: {val}')
