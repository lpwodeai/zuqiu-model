# -*- coding: utf-8 -*-
"""逐场配对：生产 p_draw vs 市场隐含平局（TFS 已配对的 131 场子集）"""
import sqlite3, os, sys, re
import numpy as np

BASE = r'f:\zuqiu\五大联赛专属模型\五大联赛专属模型'
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import topo_tfs as T
from tfs_shadow_report import (load_model_grid, load_market_grid, load_name_map,
                               canon_key, DB_PATH, JSONL, GRID_MODEL)
from market_benchmark_hc import build_en2cn, _norm
import json

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
en2cn = build_en2cn(conn)
name_map = load_name_map(conn)

# 生产 WDL p_draw（英文 match_id）
wdl = {}
for r in conn.execute(
        "SELECT match_id, probability FROM model_predictions "
        "WHERE model_name='generate_unified_report_v2.0' AND is_replay=0 AND prediction_type='WDL_draw'"):
    wdl[r['match_id']] = r['probability']

# 网格索引（规范键 → 英文 grid id）
grid_index = {}
for (mid,) in conn.execute(
        "SELECT DISTINCT match_id FROM model_predictions WHERE model_name=? AND is_replay=0 "
        "AND prediction_type='Score_grid_0_0'", (GRID_MODEL,)).fetchall():
    ck = canon_key(mid, name_map)
    if ck:
        grid_index[ck] = mid

records = {}
with open(JSONL, encoding='utf-8') as f:
    for ln in f:
        ln = ln.strip()
        if ln:
            d = json.loads(ln); records[d['match_id']] = d

pairs = []
for mid, d in records.items():
    v5 = d.get('v5')
    if not (isinstance(v5, dict) and 'top5' in v5):
        continue
    ck = canon_key(mid, name_map)
    gid = grid_index.get(ck) if ck else None
    if not gid:
        continue
    gm = load_model_grid(conn, gid)
    if gm is None:
        continue
    std_names = set(name_map.values())
    hc = en2cn.get(_norm(d.get('home', ''))) or (d.get('home') if d.get('home') in std_names else None)
    ac = en2cn.get(_norm(d.get('away', ''))) or (d.get('away') if d.get('away') in std_names else None)
    if not hc or not ac:
        continue
    gk, n_cov = load_market_grid(conn, f"{d['match_date']}_{hc}_{ac}", d['match_date'])
    if gk is None:
        continue
    # 生产 p_draw：网格 id 与生产 id 同为英文键，直接查；否则经规范键找
    pd_prod = wdl.get(gid)
    if pd_prod is None:
        # 生产 WDL 的 match_id 可能略不同，用规范键反查
        for k, v in wdl.items():
            if canon_key(k, name_map) == ck:
                pd_prod = v; break
    if pd_prod is None:
        continue
    pairs.append({
        'mid': gid,
        'p_draw_prod': float(pd_prod),
        'v5_diag': float(np.trace(gm)),
        'mkt_diag': float(np.trace(gk)),
    })

n = len(pairs)
print(f'三方配对 n={n}')
a = np.array([p['p_draw_prod'] for p in pairs])
b = np.array([p['v5_diag'] for p in pairs])
c = np.array([p['mkt_diag'] for p in pairs])
print(f'生产 p_draw 均值: {a.mean():.4f}')
print(f'v5 平局带均值  : {b.mean():.4f}')
print(f'市场平局带均值 : {c.mean():.4f}')
print(f'偏差: 生产-市场 {a.mean()-c.mean():+.4f} | v5-市场 {b.mean()-c.mean():+.4f} | v5-生产 {b.mean()-a.mean():+.4f}')
print(f'相关: corr(生产偏差, v5偏差) = {np.corrcoef(a-c, b-c)[0,1]:.3f}')
# v5-生产 的残差来源（DC 网格结构项）
print(f'v5 相对生产 p_draw 的增量: {(b-a).mean():+.4f}（DC 网格结构贡献）')
