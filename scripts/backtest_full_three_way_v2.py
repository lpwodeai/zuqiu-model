# -*- coding: utf-8 -*-
"""
全量回测 v2：预计算 λ 查找表，极速版
覆盖 3021 场，五大联赛，23/24-26/27 赛季
"""
import json, sys, time, os
from pathlib import Path
from collections import defaultdict
import numpy as np
from scipy.stats import poisson
os.environ['PYTHONUNBUFFERED'] = '1'

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"
sys.path.insert(0, str(BASE))
from db_utils import connect  # noqa: E402

MAX_GOALS = 8
RHO_DEFAULT = -0.30
RHO_HIGH = -0.10

# ============================================================
# 1. 预计算 λ 查找表
# ============================================================
LAMBDA_STEP = 0.05
LAMBDA_MIN = 0.1
LAMBDA_MAX = 8.0
L_VALUES = [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0]

def build_lambda_lut():
    """预计算 λ→(WDL, HCP, TG) 的查找表"""
    lambdas = np.arange(LAMBDA_MIN, LAMBDA_MAX + LAMBDA_STEP, LAMBDA_STEP)
    n = len(lambdas)
    print(f"  构建 λ 查找表: {n}×{n} = {n*n} 个点...")
    sys.stdout.flush()
    
    # 存储: lut[lh_idx][la_idx] = (wdl[3], hcp[9][3], tg[17])
    wdl_lut = np.zeros((n, n, 3))
    hcp_lut = np.zeros((n, n, len(L_VALUES), 3))
    tg_lut = np.zeros((n, n, MAX_GOALS*2+1))
    
    for i, lh in enumerate(lambdas):
        for j, la in enumerate(lambdas):
            # 纯泊松比分矩阵
            grid = np.zeros((MAX_GOALS+1, MAX_GOALS+1))
            for h in range(MAX_GOALS+1):
                for a in range(MAX_GOALS+1):
                    grid[h, a] = poisson.pmf(h, lh) * poisson.pmf(a, la)
            grid /= grid.sum()
            
            # WDL
            wdl_lut[i, j, 0] = grid[np.tril_indices_from(grid, -1)].sum()  # home
            wdl_lut[i, j, 1] = np.trace(grid).sum()  # draw
            wdl_lut[i, j, 2] = grid[np.triu_indices_from(grid, 1)].sum()  # away
            
            # HCP for each L
            for k, L in enumerate(L_VALUES):
                home = draw = away = 0.0
                for h in range(MAX_GOALS+1):
                    for a in range(MAX_GOALS+1):
                        adj = h - L - a
                        if adj > 0: home += grid[h, a]
                        elif adj == 0: draw += grid[h, a]
                        else: away += grid[h, a]
                hcp_lut[i, j, k, 0] = home
                hcp_lut[i, j, k, 1] = draw
                hcp_lut[i, j, k, 2] = away
            
            # TG
            for h in range(MAX_GOALS+1):
                for a in range(MAX_GOALS+1):
                    tg_lut[i, j, h+a] += grid[h, a]
    
    print(f"  完成!")
    sys.stdout.flush()
    return lambdas, wdl_lut, hcp_lut, tg_lut

def lambda_to_idx(lh, la, lambdas):
    """λ 值 → 查找表索引"""
    i = int(round((lh - LAMBDA_MIN) / LAMBDA_STEP))
    j = int(round((la - LAMBDA_MIN) / LAMBDA_STEP))
    i = max(0, min(len(lambdas)-1, i))
    j = max(0, min(len(lambdas)-1, j))
    return i, j

# ============================================================
# 2. 赔率转概率
# ============================================================
def odds_to_prob(odds):
    imp = 1.0 / np.array(odds, dtype=float)
    return imp / imp.sum()

# ============================================================
# 3. 盘口线推断（查表）
# ============================================================
def infer_handicap_line(lh, la, hcp_probs, lambdas, hcp_lut):
    i, j = lambda_to_idx(lh, la, lambdas)
    best_L = 0.0
    best_loss = 1e9
    for k, L in enumerate(L_VALUES):
        hcp_m = hcp_lut[i, j, k]
        loss = ((hcp_m - hcp_probs)**2).sum()
        if loss < best_loss:
            best_loss = loss
            best_L = L
    return best_L

# ============================================================
# 4. λ 拟合（查表 + 局部搜索）
# ============================================================
def fit_wdl_lambda(wdl_probs, lambdas, wdl_lut):
    """WDL 反推 λ（查表）"""
    best_loss = 1e9
    best_i, best_j = 0, 0
    n = len(lambdas)
    for i in range(n):
        for j in range(n):
            wdl_m = wdl_lut[i, j]
            loss = ((wdl_m - wdl_probs)**2).sum()
            if loss < best_loss:
                best_loss = loss
                best_i, best_j = i, j
    return lambdas[best_i], lambdas[best_j]

def fit_joint_lambda(wdl_probs, hcp_probs, tg_probs, L, lambdas, wdl_lut, hcp_lut, tg_lut):
    """三项联合 λ（查表）"""
    best_loss = 1e9
    best_i, best_j = 0, 0
    n = len(lambdas)
    
    # 找 L 对应的索引
    L_idx = None
    for k, lv in enumerate(L_VALUES):
        if abs(lv - L) < 0.01:
            L_idx = k
            break
    if L_idx is None:
        L_idx = 4  # default L=0
    
    for i in range(n):
        for j in range(n):
            wdl_m = wdl_lut[i, j]
            hcp_m = hcp_lut[i, j, L_idx]
            tg_m = tg_lut[i, j]
            loss = ((wdl_m - wdl_probs)**2).sum() * 3.0 + \
                   ((hcp_m - hcp_probs)**2).sum() * 2.0 + \
                   ((tg_m[:len(tg_probs)] - tg_probs[:len(tg_m)])**2).sum() * 1.0
            if loss < best_loss:
                best_loss = loss
                best_i, best_j = i, j
    return lambdas[best_i], lambdas[best_j]

# ============================================================
# 5. DC 修正 + 评估（原始计算，结果用）
# ============================================================
def dc_correction(h, a, lh, la, rho):
    if h == 0 and a == 0: return 1.0 - lh * la * rho
    elif h == 0 and a == 1: return 1.0 + lh * rho
    elif h == 1 and a == 0: return 1.0 + la * rho
    elif h == 1 and a == 1: return 1.0 - rho
    return 1.0

def score_grid_dc(lh, la, rho):
    grid = np.zeros((MAX_GOALS+1, MAX_GOALS+1))
    for h in range(MAX_GOALS+1):
        for a in range(MAX_GOALS+1):
            p = poisson.pmf(h, lh) * poisson.pmf(a, la)
            tau = dc_correction(h, a, lh, la, rho)
            grid[h, a] = p * tau
    grid /= grid.sum()
    return grid

def evaluate(actual_score, lh, la, rho):
    grid = score_grid_dc(lh, la, rho)
    h_goals, a_goals = actual_score
    actual_wdl = 0 if h_goals > a_goals else 1 if h_goals == a_goals else 2
    actual_tg = h_goals + a_goals
    
    # 聚合方向
    home = grid[np.tril_indices_from(grid, -1)].sum()
    draw = np.trace(grid).sum()
    away = grid[np.triu_indices_from(grid, 1)].sum()
    pred_wdl = np.argmax([home, draw, away])
    
    # TG
    tg = np.zeros(MAX_GOALS*2+1)
    for h in range(MAX_GOALS+1):
        for a in range(MAX_GOALS+1):
            tg[h+a] += grid[h, a]
    pred_over25 = 1 if sum(tg[3:]) >= 0.5 else 0
    actual_over25 = 1 if actual_tg >= 3 else 0
    pred_tg_range = 0 if sum(tg[:2]) >= 0.5 else 1 if sum(tg[2:4]) >= 0.5 else 2
    actual_tg_range = 0 if actual_tg <= 1 else 1 if actual_tg <= 3 else 2
    
    return {
        'dir': 1 if pred_wdl == actual_wdl else 0,
        'o25': 1 if pred_over25 == actual_over25 else 0,
        'tgr': 1 if pred_tg_range == actual_tg_range else 0,
    }

# ============================================================
# 6. 主流程
# ============================================================
def main():
    print("=" * 70)
    print("全量回测 v2：预计算查表，极速版")
    print("=" * 70)
    
    t0 = time.time()
    
    # 构建查找表
    lambdas, wdl_lut, hcp_lut, tg_lut = build_lambda_lut()
    print(f"  查找表构建耗时: {time.time()-t0:.1f}s")
    sys.stdout.flush()
    
    # 加载数据
    c = connect(db_path=DB)
    
    wdl_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM wdl_history WHERE match_id_en IS NOT NULL").fetchall())
    hcp_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM handicap_history WHERE match_id_en IS NOT NULL").fetchall())
    tg_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM total_goals_history WHERE match_id_en IS NOT NULL").fetchall())
    score_mids = set(r[0] for r in c.execute("SELECT match_id FROM matches WHERE actual_score IS NOT NULL AND actual_score != ''").fetchall())
    
    all_mids = sorted(wdl_en & hcp_en & tg_en & score_mids)
    print(f"可回测样本: {len(all_mids)} 场")
    
    # 批量加载赔率和比分
    print("加载赔率数据...")
    sys.stdout.flush()
    
    wdl_data = {}
    hcp_data = {}
    tg_data = {}
    match_info = {}
    
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
        for l in ['英超', '西甲', '意甲', '德甲', '法甲']:
            if l in lg: league_counts[l] += 1; break
        if mid.startswith('2023'): season_counts['23/24'] += 1
        elif mid.startswith('2024'): season_counts['24/25'] += 1
        elif mid.startswith('2025'): season_counts['25/26'] += 1
        elif mid.startswith('2026'): season_counts['26/27'] += 1
    print(f"  联赛:", dict(league_counts))
    print(f"  赛季:", dict(season_counts))
    sys.stdout.flush()
    
    # 回测
    print(f"\n开始回测 {len(valid_mids)} 场...")
    sys.stdout.flush()
    
    results = {'A': {'dir':0,'o25':0,'tgr':0}, 'B': {'dir':0,'o25':0,'tgr':0}, 'C': {'dir':0,'o25':0,'tgr':0}}
    league_r = defaultdict(lambda: {'A':{'dir':0,'o25':0,'tgr':0,'n':0}, 'B':{'dir':0,'o25':0,'tgr':0,'n':0}, 'C':{'dir':0,'o25':0,'tgr':0,'n':0}})
    errors = 0
    
    t1 = time.time()
    for i, mid in enumerate(valid_mids):
        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{len(valid_mids)} ({time.time()-t1:.1f}s)")
            sys.stdout.flush()
        
        try:
            info = match_info[mid]
            actual_score = info['score']
            lg = info['league']
            for l in ['英超', '西甲', '意甲', '德甲', '法甲']:
                if l in lg: lg_key = l; break
            else: lg_key = '其他'
            
            wdl_p = odds_to_prob(wdl_data[mid])
            hcp_p = odds_to_prob(hcp_data[mid])
            tg_p = odds_to_prob(tg_data[mid])
            
            # A: 旧基线
            lh_a, la_a = fit_wdl_lambda(wdl_p, lambdas, wdl_lut)
            
            # 推断 L
            L = infer_handicap_line(lh_a, la_a, hcp_p, lambdas, hcp_lut)
            
            # B: 新三项联动
            lh_b, la_b = fit_joint_lambda(wdl_p, hcp_p, tg_p, L, lambdas, wdl_lut, hcp_lut, tg_lut)
            
            # 评估
            r_a = evaluate(actual_score, lh_a, la_a, RHO_DEFAULT)
            r_b = evaluate(actual_score, lh_b, la_b, RHO_DEFAULT)
            rho_c = RHO_HIGH if abs(L) >= 1.0 else RHO_DEFAULT
            r_c = evaluate(actual_score, lh_b, la_b, rho_c)
            
            for var, r in [('A', r_a), ('B', r_b), ('C', r_c)]:
                results[var]['dir'] += r['dir']
                results[var]['o25'] += r['o25']
                results[var]['tgr'] += r['tgr']
                league_r[lg_key][var]['dir'] += r['dir']
                league_r[lg_key][var]['o25'] += r['o25']
                league_r[lg_key][var]['tgr'] += r['tgr']
                league_r[lg_key][var]['n'] += 1
        except Exception as e:
            errors += 1
    
    elapsed = time.time() - t1
    n = len(valid_mids)
    
    print(f"\n回测完成 ({n} 场, {elapsed:.1f}s, {errors} 错误)")
    print(f"场均: {elapsed/n*1000:.1f}ms")
    print(f"{'='*70}")
    
    header = f"{'指标':<20} {'A 旧基线':>12} {'B 三项联动':>12} {'C 三项+ρ切换':>14}"
    print(header)
    print("-" * 60)
    for key, label in [('dir', '方向命中率'), ('o25', '大2.5命中率'), ('tgr', 'TG区间(0-1/2-3/4+)')]:
        a = results['A'][key]; b = results['B'][key]; c = results['C'][key]
        print(f"{label:<20} {a/n:>11.1%} {b/n:>11.1%} {c/n:>13.1%}")
        print(f"  (命中数)          {a:>6}/{n}     {b:>6}/{n}     {c:>6}/{n}")
    
    # 按联赛
    print(f"\n{'='*70}")
    print("按联赛分组")
    print(f"{'='*70}")
    for lg in ['英超', '西甲', '意甲', '德甲', '法甲']:
        if lg in league_r:
            lr = league_r[lg]; n_lg = lr['A']['n']
            print(f"\n  {lg} ({n_lg}场):")
            print(f"    {'':<18} {'A旧基线':>10} {'B三项联动':>10} {'C+ρ切换':>10}")
            for key, label in [('dir', '方向'), ('o25', '大2.5'), ('tgr', 'TG区间')]:
                print(f"    {label:<18} {lr['A'][key]/n_lg:>9.1%} {lr['B'][key]/n_lg:>9.1%} {lr['C'][key]/n_lg:>9.1%}")
    
    c.close()
    print(f"\n总耗时: {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()