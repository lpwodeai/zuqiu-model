# -*- coding: utf-8 -*-
"""临时profiling：拆分 d013 与 score 模块内部各阶段耗时。"""
import sys, time
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db_utils import connect, read_sql
from feature_utils import build_match_alignment, load_match_data_odds

_MAX = "2026-07-01 00:00:00"
_GOAL_COLS = ['goals_0', 'goals_1', 'goals_2', 'goals_3',
              'goals_4', 'goals_5', 'goals_6', 'goals_7_plus']

def t(label, fn):
    t0 = time.time()
    r = fn()
    dt = time.time() - t0
    print(f"[{dt:7.2f}s] {label}", flush=True)
    return r

df = load_match_data_odds()
print(f"df={df.shape}", flush=True)
import d013_temporal_odds as D
conn = D.load_timing_conn()
print(f"conn ok", flush=True)

# ----- d013 阶段拆分 -----
wdl = t("d013 read wdl_history", lambda: read_sql(
    "SELECT match_id, timestamp, win_a, draw, win_b FROM wdl_history WHERE timestamp < ?",
    conn, params=(_MAX,)))
hcp = t("d013 read handicap_history", lambda: read_sql(
    "SELECT match_id, timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE timestamp < ?",
    conn, params=(_MAX,)))
tg = t("d013 read total_goals_history", lambda: read_sql(
    f"SELECT match_id, timestamp, {', '.join(_GOAL_COLS)} FROM total_goals_history WHERE timestamp < ?",
    conn, params=(_MAX,)))

m1 = t("d013 align wdl_history", lambda: build_match_alignment(conn, 'wdl_history'))
m2 = t("d013 align handicap_history", lambda: build_match_alignment(conn, 'handicap_history'))
m3 = t("d013 align total_goals_history", lambda: build_match_alignment(conn, 'total_goals_history'))

wdl['mmid'] = wdl['match_id'].map(m1)
hcp['mmid'] = hcp['match_id'].map(m2)
tg['mmid'] = tg['match_id'].map(m3)

for x in (wdl, hcp, tg):
    x.dropna(subset=['mmid'], inplace=True)

wdl_s = t("d013 wdl sort+valid", lambda: wdl.sort_values('timestamp'))
hcp_s = t("d013 hcp sort+valid", lambda: hcp.sort_values('timestamp'))
tg_s = t("d013 tg sort+valid", lambda: tg.sort_values('timestamp'))

import d013_temporal_odds as D
def bench_groups():
    wdl_feats = {mmid: D._compute_wdl_features(g) for mmid, g in wdl_s.groupby('mmid', sort=False)}
    hcp_feats = {mmid: D._compute_hcp_features(g) for mmid, g in hcp_s.groupby('mmid', sort=False)}
    ou_feats = {mmid: D._compute_ou_features(g) for mmid, g in tg_s.groupby('mmid', sort=False)}
    return len(wdl_feats), len(hcp_feats), len(ou_feats)

n = t("d013 groupby compute (3 tables)", bench_groups)
print(f"   groups: {n}", flush=True)

# ----- score 阶段拆分 -----
raw = t("score read score_history", lambda: read_sql(
    "SELECT match_id, timestamp, score, odds FROM score_history WHERE timestamp < ?",
    conn, params=(_MAX,)))
m4 = t("score align score_history", lambda: build_match_alignment(conn, "score_history"))
raw['mmid'] = raw['match_id'].map(m4)
n_hist = raw['match_id'].nunique()
raw = raw.dropna(subset=['mmid'])
raw['timestamp'] = pd.to_datetime(raw['timestamp'], format='mixed', errors='coerce')
raw = raw.dropna(subset=['timestamp']).sort_values('timestamp')

import score_features as S
def bench_score():
    records = {}
    for mmid, g in raw.groupby('mmid', sort=False):
        records[mmid] = S.compute_score_features_from_df(g)
    return len(records)

nscore = t("score groupby compute", bench_score)
print(f"   score groups: {nscore}", flush=True)

conn.close()
print("DONE", flush=True)