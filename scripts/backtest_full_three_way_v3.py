# -*- coding: utf-8 -*-
"""
全量回测 v3：向量化比分矩阵 + scipy optimize，精简版
"""
import json, sys, time, os
from pathlib import Path
from collections import defaultdict
import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize
os.environ['PYTHONUNBUFFERED'] = '1'

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"
sys.path.insert(0, str(BASE))
from db_utils import connect  # noqa: E402

MAX_G = 8
RHO_DEFAULT = -0.30
RHO_HIGH = -0.10

# ============================================================
# 向量化比分矩阵
# ============================================================
def score_matrix(lh, la):
    """向量化：λ → 比分矩阵（纯泊松）"""
    hp = poisson.pmf(np.arange(MAX_G+1), lh)
    ap = poisson.pmf(np.arange(MAX_G+1), la)
    grid = np.outer(hp, ap)
    return grid / grid.sum()

def wdl_from_grid(grid):
    return np.array([
        grid[np.tril_indices_from(grid, -1)].sum(),
        np.trace(grid).sum(),
        grid[np.triu_indices_from(grid, 1)].sum()
    ])

def hcp_from_grid(grid, L):
    h = d = a = 0.0
    for hi in range(MAX_G+1):
        for ai in range(MAX_G+1):
            if hi - L > ai: h += grid[hi, ai]
            elif hi - L == ai: d += grid[hi, ai]
            else: a += grid[hi, ai]
    return np.array([h, d, a])

def tg_from_grid(grid):
    tg = np.zeros(MAX_G*2+1)
    for hi in range(MAX_G+1):
        for ai in range(MAX_G+1):
            tg[hi+ai] += grid[hi, ai]
    return tg

def dc_correction(h, a, lh, la, rho):
    if h == 0 and a == 0: return 1.0 - lh * la * rho
    elif h == 0 and a == 1: return 1.0 + lh * rho
    elif h == 1 and a == 0: return 1.0 + la * rho
    elif h == 1 and a == 1: return 1.0 - rho
    return 1.0

def score_matrix_dc(lh, la, rho):
    hp = poisson.pmf(np.arange(MAX_G+1), lh)
    ap = poisson.pmf(np.arange(MAX_G+1), la)
    grid = np.outer(hp, ap)
    for h in range(MAX_G+1):
        for a in range(MAX_G+1):
            grid[h, a] *= dc_correction(h, a, lh, la, rho)
    return grid / grid.sum()

# ============================================================
# 赔率
# ============================================================
def odds_to_prob(odds):
    imp = 1.0 / np.array(odds, dtype=float)
    return imp / imp.sum()

# ============================================================
# λ 拟合（向量化 + 两步网格）
# ============================================================
def fit_wdl_lambda(wdl_probs):
    """WDL→λ：scipy minimize"""
    def loss(lam):
        lh, la = lam[0], lam[1]
        if lh <= 0.01 or la <= 0.01: return 1e9
        grid = score_matrix(lh, la)
        wdl = wdl_from_grid(grid)
        return ((wdl - wdl_probs)**2).sum() * 100
    res = minimize(loss, [1.5, 1.0], bounds=[(0.05,8.0),(0.05,8.0)], method='Nelder-Mead',
                   options={'maxiter':50, 'xatol':1e-4, 'fatol':1e-8})
    return res.x[0], res.x[1]

def infer_L(lh, la, hcp_probs):
    """推断盘口线"""
    best_L, best_loss = 0.0, 1e9
    grid = score_matrix(lh, la)
    for L in [v/2 for v in range(-8, 9)]:
        hcp = hcp_from_grid(grid, L)
        loss = ((hcp - hcp_probs)**2).sum()
        if loss < best_loss:
            best_loss, best_L = loss, L
    return best_L

def fit_joint_lambda(wdl_probs, hcp_probs, tg_probs, L):
    """三项联合λ：scipy minimize"""
    lh0, la0 = fit_wdl_lambda(wdl_probs)
    def loss(lam):
        lh, la = lam[0], lam[1]
        if lh <= 0.01 or la <= 0.01: return 1e9
        grid = score_matrix(lh, la)
        wdl = wdl_from_grid(grid)
        hcp = hcp_from_grid(grid, L)
        tg = tg_from_grid(grid)
        tl = min(len(tg_probs), len(tg))
        return ((wdl-wdl_probs)**2).sum()*3 + ((hcp-hcp_probs)**2).sum()*2 + ((tg[:tl]-tg_probs[:tl])**2).sum()
    res = minimize(loss, [lh0, la0], bounds=[(0.05,8.0),(0.05,8.0)], method='Nelder-Mead',
                   options={'maxiter':50, 'xatol':1e-4, 'fatol':1e-8})
    return res.x[0], res.x[1]

# ============================================================
# 评估
# ============================================================
def evaluate(actual_score, lh, la, rho):
    grid = score_matrix_dc(lh, la, rho)
    h_goals, a_goals = actual_score
    actual_wdl = 0 if h_goals > a_goals else 1 if h_goals == a_goals else 2
    actual_tg = h_goals + a_goals
    
    home = grid[np.tril_indices_from(grid, -1)].sum()
    draw = np.trace(grid).sum()
    away = grid[np.triu_indices_from(grid, 1)].sum()
    pred_wdl = np.argmax([home, draw, away])
    
    tg = tg_from_grid(grid)
    pred_over25 = 1 if sum(tg[3:]) >= 0.5 else 0
    actual_over25 = 1 if actual_tg >= 3 else 0
    pred_tg_range = 0 if sum(tg[:2]) >= 0.5 else 1 if sum(tg[2:4]) >= 0.5 else 2
    actual_tg_range = 0 if actual_tg <= 1 else 1 if actual_tg <= 3 else 2
    
    return {'dir': int(pred_wdl == actual_wdl), 'o25': int(pred_over25 == actual_over25),
            'tgr': int(pred_tg_range == actual_tg_range)}

# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 70)
    print("全量回测 v3：向量化+两步网格")
    print("=" * 70)
    sys.stdout.flush()
    
    c = connect(db_path=DB)
    
    # 获取可回测 match_id
    wdl_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM wdl_history WHERE match_id_en IS NOT NULL").fetchall())
    hcp_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM handicap_history WHERE match_id_en IS NOT NULL").fetchall())
    tg_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM total_goals_history WHERE match_id_en IS NOT NULL").fetchall())
    score_mids = set(r[0] for r in c.execute("SELECT match_id FROM matches WHERE actual_score IS NOT NULL AND actual_score != ''").fetchall())
    
    all_mids = sorted(wdl_en & hcp_en & tg_en & score_mids)
    print(f"可回测样本: {len(all_mids)} 场")
    sys.stdout.flush()
    
    # 批量加载
    print("加载数据...")
    sys.stdout.flush()
    wdl_data, hcp_data, tg_data, match_info = {}, {}, {}, {}
    for mid in all_mids:
        r = c.execute("SELECT win_a, draw, win_b FROM wdl_history WHERE match_id_en=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
        if r: wdl_data[mid] = (r[0], r[1], r[2])
        r = c.execute("SELECT hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE match_id_en=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
        if r: hcp_data[mid] = (r[0], r[1], r[2])
        r = c.execute("SELECT goals_0,goals_1,goals_2,goals_3,goals_4,goals_5,goals_6,goals_7_plus FROM total_goals_history WHERE match_id_en=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
        if r: tg_data[mid] = tuple(r)
        r = c.execute("SELECT actual_score, match_type FROM matches WHERE match_id=?", (mid,)).fetchone()
        if r and r[0]:
            parts = r[0].replace(' ', '').split('-')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                match_info[mid] = {'score': (int(parts[0]), int(parts[1])), 'league': r[1]}
    
    valid_mids = [m for m in all_mids if m in wdl_data and m in hcp_data and m in tg_data and m in match_info]
    print(f"完整数据: {len(valid_mids)} 场")
    
    league_counts = defaultdict(int)
    season_counts = defaultdict(int)
    for mid in valid_mids:
        lg = match_info[mid]['league']
        for l in ['英超','西甲','意甲','德甲','法甲']:
            if l in lg: league_counts[l] += 1; break
        if mid.startswith('2023'): season_counts['23/24'] += 1
        elif mid.startswith('2024'): season_counts['24/25'] += 1
        elif mid.startswith('2025'): season_counts['25/26'] += 1
        elif mid.startswith('2026'): season_counts['26/27'] += 1
    print(f"  联赛:", dict(league_counts))
    print(f"  赛季:", dict(season_counts))
    sys.stdout.flush()
    
    # 回测
    print(f"\n回测 {len(valid_mids)} 场...")
    sys.stdout.flush()
    
    results = {'A':{'dir':0,'o25':0,'tgr':0}, 'B':{'dir':0,'o25':0,'tgr':0}, 'C':{'dir':0,'o25':0,'tgr':0}}
    lr = defaultdict(lambda: {'A':{'dir':0,'o25':0,'tgr':0,'n':0},'B':{'dir':0,'o25':0,'tgr':0,'n':0},'C':{'dir':0,'o25':0,'tgr':0,'n':0}})
    errors = 0
    t1 = time.time()
    
    for i, mid in enumerate(valid_mids):
        if (i+1) % 500 == 0:
            print(f"  {i+1}/{len(valid_mids)} ({time.time()-t1:.0f}s)")
            sys.stdout.flush()
        
        try:
            info = match_info[mid]
            lg = info['league']
            for l in ['英超','西甲','意甲','德甲','法甲']:
                if l in lg: lg_key = l; break
            else: lg_key = '其他'
            
            wdl_p = odds_to_prob(wdl_data[mid])
            hcp_p = odds_to_prob(hcp_data[mid])
            tg_p = odds_to_prob(tg_data[mid])
            
            lh_a, la_a = fit_wdl_lambda(wdl_p)
            L = infer_L(lh_a, la_a, hcp_p)
            lh_b, la_b = fit_joint_lambda(wdl_p, hcp_p, tg_p, L)
            
            r_a = evaluate(info['score'], lh_a, la_a, RHO_DEFAULT)
            r_b = evaluate(info['score'], lh_b, la_b, RHO_DEFAULT)
            rho_c = RHO_HIGH if abs(L) >= 1.0 else RHO_DEFAULT
            r_c = evaluate(info['score'], lh_b, la_b, rho_c)
            
            for var, r in [('A',r_a),('B',r_b),('C',r_c)]:
                results[var]['dir'] += r['dir']; results[var]['o25'] += r['o25']; results[var]['tgr'] += r['tgr']
                lr[lg_key][var]['dir'] += r['dir']; lr[lg_key][var]['o25'] += r['o25']; lr[lg_key][var]['tgr'] += r['tgr']
                lr[lg_key][var]['n'] += 1
        except Exception as e:
            errors += 1
    
    elapsed = time.time() - t1
    n = len(valid_mids)
    print(f"\n完成 ({n}场, {elapsed:.0f}s, {errors}错, 场均{elapsed/n*1000:.0f}ms)")
    print(f"{'='*70}")
    
    print(f"{'指标':<20} {'A 旧基线':>12} {'B 三项联动':>12} {'C +ρ切换':>12}")
    print("-"*58)
    for key, label in [('dir','方向命中率'),('o25','大2.5命中率'),('tgr','TG区间')]:
        a,b,c = results['A'][key], results['B'][key], results['C'][key]
        print(f"{label:<20} {a/n:>11.1%} {b/n:>11.1%} {c/n:>11.1%}")
    
    print(f"\n{'='*70}\n按联赛:")
    for lg in ['英超','西甲','意甲','德甲','法甲']:
        if lg in lr:
            x = lr[lg]; nl = x['A']['n']
            print(f"  {lg}({nl}场): 方向 A={x['A']['dir']/nl:.1%} B={x['B']['dir']/nl:.1%} C={x['C']['dir']/nl:.1%} | TG A={x['A']['tgr']/nl:.1%} B={x['B']['tgr']/nl:.1%} C={x['C']['tgr']/nl:.1%}")
    
    c.close()

if __name__ == "__main__":
    main()