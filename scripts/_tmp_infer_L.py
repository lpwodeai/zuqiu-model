# -*- coding: utf-8 -*-
"""法甲42场全量验证反推L准确率(整数步长)"""
import sqlite3, math
from pathlib import Path
from scipy.stats import poisson
import numpy as np

BASE = Path(__file__).resolve().parent.parent
c = sqlite3.connect(str(BASE / "data" / "odds.db"))
c.row_factory = sqlite3.Row
MAX_GOALS = 7; AVG_GOALS = 3.6

def implied(odds):
    inv=[1.0/max(o,1e-10) for o in odds]; t=sum(inv); return [x/t for x in inv]
def score_grid_pure(lh,la):
    g={}
    for h in range(MAX_GOALS+1):
        for a in range(MAX_GOALS+1): g[(h,a)]=poisson.pmf(h,lh)*poisson.pmf(a,la)
    t=sum(g.values()); return {k:v/t for k,v in g.items()}
def hcp_marginal(grid,L):
    out=[0.0,0.0,0.0]
    for (h,a),p in grid.items():
        d=h+L-a; out[0 if d>0 else (1 if d==0 else 2)]+=p
    return out
def ce(t,p): return -sum(x*math.log(max(y,1e-9)) for x,y in zip(t,p))
def lam_from_wdl(wdl):
    hp,dp,ap=implied(list(wdl))
    return max(0.3,min(3.5,AVG_GOALS*hp)), max(0.3,min(3.5,AVG_GOALS*ap))
def infer_L(wdl,hcp,lam,integer=True):
    lh,la=lam; grid=score_grid_pure(lh,la); t_hcp=implied(list(hcp))
    cand = list(range(-3,4)) if integer else list(np.arange(-3,3.01,0.25))
    best_L,best_ce=0,1e18
    for L in cand:
        e=ce(t_hcp,hcp_marginal(grid,L))
        if e<best_ce: best_ce,best_L=e,L
    return best_L,best_ce

rows = c.execute("""
    SELECT m.match_id, m.handicap FROM matches m
    WHERE m.match_type='法甲2025-2026赛季' AND m.actual_score IS NOT NULL AND m.actual_score!=''
      AND m.handicap IS NOT NULL
      AND EXISTS(SELECT 1 FROM wdl_history w WHERE w.match_id=m.match_id)
      AND EXISTS(SELECT 1 FROM handicap_history h WHERE h.match_id=m.match_id)
""").fetchall()
exact=0; within1=0; total=0; errs=[]
for r in rows:
    mid=r['match_id']
    w=c.execute("SELECT win_a,draw,win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1",(mid,)).fetchone()
    h=c.execute("SELECT hcp_win,hcp_draw,hcp_lose FROM handicap_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1",(mid,)).fetchone()
    if not(w and h): continue
    lam=lam_from_wdl((w['win_a'],w['draw'],w['win_b']))
    L,_=infer_L((w['win_a'],w['draw'],w['win_b']),(h['hcp_win'],h['hcp_draw'],h['hcp_lose']),lam,integer=True)
    real=r['handicap']; d=abs(L-real); errs.append(d); total+=1
    if d==0: exact+=1
    if d<=1: within1+=1
print(f"法甲全量验证: {total}场")
print(f"  完全准确(差0): {exact}/{total} = {exact/total*100:.1f}%")
print(f"  误差<=1档: {within1}/{total} = {within1/total*100:.1f}%")
print(f"  平均绝对误差: {sum(errs)/len(errs):.2f}")
import collections
print(f"  误差分布: {dict(collections.Counter(errs))}")
c.close()
