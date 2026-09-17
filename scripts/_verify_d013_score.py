# -*- coding: utf-8 -*-
"""验证 d013 / score 向量化改写与旧实现输出一致，并计时。"""
import sys, time, warnings
warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_utils import read_sql
import feature_utils as F
import d013_temporal_odds as D
import score_features as S

df = F.load_match_data_odds()
print(f"df={df.shape}", flush=True)

# ==================== d013 旧版重建 ====================
conn = D.load_timing_conn()
_MAX = D.MAX_VALID_TIMESTAMP
_GOAL_COLS = D._GOAL_COLS
from feature_utils import build_match_alignment  # noqa: E402

wdl = read_sql("SELECT match_id, timestamp, win_a, draw, win_b FROM wdl_history WHERE timestamp < ?",
               conn, params=(_MAX,))
hcp = read_sql("SELECT match_id, timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE timestamp < ?",
               conn, params=(_MAX,))
tg = read_sql(f"SELECT match_id, timestamp, {', '.join(_GOAL_COLS)} FROM total_goals_history WHERE timestamp < ?",
              conn, params=(_MAX,))
wdl['mmid'] = wdl['match_id'].map(build_match_alignment(conn, 'wdl_history'))
hcp['mmid'] = hcp['match_id'].map(build_match_alignment(conn, 'handicap_history'))
tg['mmid'] = tg['match_id'].map(build_match_alignment(conn, 'total_goals_history'))
for t in (wdl, hcp, tg):
    t.dropna(subset=['mmid'], inplace=True)
wdl = D._valid_rows(wdl.sort_values('timestamp'), ['win_a', 'draw', 'win_b'])
hcp = D._valid_rows(hcp.sort_values('timestamp'), ['hcp_win', 'hcp_draw', 'hcp_lose'])
tg = D._valid_rows(tg.sort_values('timestamp'), _GOAL_COLS)

# 旧 step-4
wdl_feats_old = {mmid: D._compute_wdl_features(g) for mmid, g in wdl.groupby('mmid', sort=False)}
hcp_feats_old = {mmid: D._compute_hcp_features(g) for mmid, g in hcp.groupby('mmid', sort=False)}
ou_feats_old = {mmid: D._compute_ou_features(g) for mmid, g in tg.groupby('mmid', sort=False)}

# 新 step-4
wdl_feats_new = D._group_apply_np(wdl, 'mmid', D._wdl_feats_np, ['win_a', 'draw', 'win_b'])
hcp_feats_new = D._group_apply_np(hcp, 'mmid', D._hcp_feats_np, ['hcp_win', 'hcp_draw', 'hcp_lose'])
ou_feats_new = D._group_apply_np(tg, 'mmid', D._ou_feats_np, _GOAL_COLS, matrix=True)
conn.close()

def cmp_dicts(a, b, name):
    assert set(a) == set(b), f"{name}: 组集不一致 {len(a)} vs {len(b)}"
    n_bad = 0
    for k in a:
        da, db = a[k], b[k]
        if set(da) != set(db):
            n_bad += 1
            if n_bad <= 3:
                print(f"  {name} [{k}] 键不一致: {set(da)^set(db)}")
            continue
        for kk in da:
            va, vb = da[kk], db[kk]
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                if not np.allclose(va, vb, rtol=1e-9, atol=1e-9, equal_nan=True):
                    n_bad += 1
                    if n_bad <= 3:
                        print(f"  {name} [{k}][{kk}] {va} != {vb}")
            elif va != vb:
                n_bad += 1
    print(f"[d013] {name}: {len(a)} 组, 不一致组 {n_bad}", flush=True)

cmp_dicts(wdl_feats_old, wdl_feats_new, "wdl")
cmp_dicts(hcp_feats_old, hcp_feats_new, "hcp")
cmp_dicts(ou_feats_old, ou_feats_new, "ou")

# ==================== 完整函数级对比 + 计时 ====================
t0 = time.time(); d_new = D.build_d013_features(df); t_d = time.time() - t0
t0 = time.time(); s_new = S.build_score_features(df); t_s = time.time() - t0
print(f"[timing] d013新 {t_d:.2f}s, score新 {t_s:.2f}s", flush=True)

t0 = time.time(); s_old = S.build_score_features_legacy(df); t_s_old = time.time() - t0
print(f"[timing] score旧 {t_s_old:.2f}s", flush=True)

# score 对比
assert list(s_new.columns) == list(s_old.columns), "score columns differ"
assert list(s_new.index) == list(s_old.index), "score index differ"
diff = (s_new[s_old.columns].to_numpy(dtype=float) - s_old[s_old.columns].to_numpy(dtype=float))
max_abs = np.nanmax(np.abs(diff))
n_bad = int((np.abs(diff) > 1e-6).sum())
print(f"[score] 不一致元素 {n_bad}, 最大绝对差 {max_abs:.10f}", flush=True)

print("DONE", flush=True)