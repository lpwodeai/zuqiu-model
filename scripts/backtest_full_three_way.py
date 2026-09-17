# -*- coding: utf-8 -*-
"""
全量回测：三项联动 λ 优化 vs 旧基线
覆盖 3021 场，五大联赛，23/24-26/27 赛季
"""
import json, sys, math, time, os
from pathlib import Path
from collections import defaultdict
import numpy as np
from scipy.stats import poisson
os.environ['PYTHONUNBUFFERED'] = '1'

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "odds.db"
sys.path.insert(0, str(BASE))
from db_utils import connect  # noqa: E402

# ============================================================
# 1. Dixon-Coles 修正
# ============================================================
MAX_GOALS = 8
RHO_DEFAULT = -0.30
RHO_HIGH = -0.10  # 强盘口用

def dc_correction(h, a, lh, la, rho):
    """Dixon-Coles 低比分修正因子 τ"""
    if h == 0 and a == 0:
        return 1.0 - lh * la * rho
    elif h == 0 and a == 1:
        return 1.0 + lh * rho
    elif h == 1 and a == 0:
        return 1.0 + la * rho
    elif h == 1 and a == 1:
        return 1.0 - rho
    return 1.0

def score_grid(lh, la, rho, max_g=MAX_GOALS):
    """泊松+DC 比分矩阵"""
    grid = np.zeros((max_g+1, max_g+1))
    for h in range(max_g+1):
        for a in range(max_g+1):
            p = poisson.pmf(h, lh) * poisson.pmf(a, la)
            tau = dc_correction(h, a, lh, la, rho)
            grid[h, a] = p * tau
    grid /= grid.sum()
    return grid

def score_grid_pure(lh, la, max_g=MAX_GOALS):
    """纯泊松比分矩阵（无 DC 修正）"""
    grid = np.zeros((max_g+1, max_g+1))
    for h in range(max_g+1):
        for a in range(max_g+1):
            grid[h, a] = poisson.pmf(h, lh) * poisson.pmf(a, la)
    grid /= grid.sum()
    return grid

# ============================================================
# 2. 从比分矩阵计算聚合指标
# ============================================================
def aggregate_wdl(grid):
    """从比分矩阵聚合 WDL"""
    home = grid[np.tril_indices_from(grid, -1)].sum()   # h > a
    away = grid[np.triu_indices_from(grid, 1)].sum()     # a > h
    draw = np.trace(grid).sum()                          # h == a
    return np.array([home, draw, away])

def aggregate_tg(grid):
    """从比分矩阵聚合总进球概率"""
    tg = np.zeros(MAX_GOALS*2+1)
    for h in range(MAX_GOALS+1):
        for a in range(MAX_GOALS+1):
            tg[h+a] += grid[h, a]
    return tg

def aggregate_handicap(grid, L):
    """从比分矩阵计算让球结果概率（L：主队让球数，正=主队让球）"""
    home = 0.0  # 主队赢盘
    draw = 0.0  # 走水
    away = 0.0  # 客队赢盘
    for h in range(MAX_GOALS+1):
        for a in range(MAX_GOALS+1):
            adj = h - L - a
            if adj > 0:
                home += grid[h, a]
            elif adj == 0:
                draw += grid[h, a]
            else:
                away += grid[h, a]
    return np.array([home, draw, away])

# ============================================================
# 3. 赔率转概率
# ============================================================
def odds_to_prob(odds):
    """赔率转概率（去除 overround）"""
    imp = 1.0 / np.array(odds, dtype=float)
    return imp / imp.sum()

def wdl_lambda_from_fair_probs(fair_h, fair_d, fair_a):
    """从 WDL 公平概率反推 λ（网格搜索 + 局部优化）"""
    best_loss = 1e9
    best_lh, best_la = 1.5, 1.0
    
    # 粗网格搜索
    for lh in np.arange(0.3, 6.0, 0.5):
        for la in np.arange(0.3, 5.0, 0.5):
            grid = score_grid_pure(lh, la)
            wdl = aggregate_wdl(grid)
            loss = (wdl[0] - fair_h)**2 + (wdl[1] - fair_d)**2 + (wdl[2] - fair_a)**2
            if loss < best_loss:
                best_loss = loss
                best_lh, best_la = lh, la
    
    # 细网格精调
    for lh in np.arange(max(0.1, best_lh-0.4), best_lh+0.5, 0.05):
        for la in np.arange(max(0.1, best_la-0.4), best_la+0.5, 0.05):
            grid = score_grid_pure(lh, la)
            wdl = aggregate_wdl(grid)
            loss = (wdl[0] - fair_h)**2 + (wdl[1] - fair_d)**2 + (wdl[2] - fair_a)**2
            if loss < best_loss:
                best_loss = loss
                best_lh, best_la = lh, la
    
    return best_lh, best_la

# ============================================================
# 4. 盘口线推断
# ============================================================
def infer_handicap_line(lh, la, hcp_probs):
    """从比分矩阵推断盘口线 L"""
    best_L = 0.0
    best_loss = 1e9
    for L in [v/2 for v in range(-8, 9)]:  # -4.0 to +4.0 in 0.5 steps
        grid = score_grid_pure(lh, la)
        hcp_model = aggregate_handicap(grid, L)
        loss = ((hcp_model - hcp_probs)**2).sum()
        if loss < best_loss:
            best_loss = loss
            best_L = L
    return best_L

# ============================================================
# 5. 三项联合 λ 拟合
# ============================================================
def joint_lambda_loss(lam, wdl_probs, hcp_probs, tg_probs, L):
    """三项联合损失函数"""
    lh, la = lam[0], lam[1]
    if lh <= 0.01 or la <= 0.01:
        return 1e9
    
    grid = score_grid_pure(lh, la)  # 纯泊松用于拟合
    
    wdl_model = aggregate_wdl(grid)
    hcp_model = aggregate_handicap(grid, L)
    tg_model = aggregate_tg(grid)
    
    loss_wdl = ((wdl_model - wdl_probs)**2).sum()
    loss_hcp = ((hcp_model - hcp_probs)**2).sum()
    
    # TG 损失：只比 0-7+ 项
    tg_len = min(len(tg_probs), len(tg_model))
    loss_tg = ((tg_model[:tg_len] - tg_probs[:tg_len])**2).sum()
    
    return loss_wdl * 3.0 + loss_hcp * 2.0 + loss_tg * 1.0

def fit_joint_lambda(wdl_probs, hcp_probs, tg_probs, L):
    """三项联合拟合 λ（网格搜索，快速）"""
    # 先用 WDL 估计初始值
    lh0, la0 = wdl_lambda_from_fair_probs(wdl_probs[0], wdl_probs[1], wdl_probs[2])
    
    best_loss = 1e9
    best_lh, best_la = lh0, la0
    
    # 在初始值附近搜索
    for lh in np.arange(max(0.1, lh0-1.0), lh0+1.1, 0.1):
        for la in np.arange(max(0.1, la0-0.8), la0+0.9, 0.1):
            if lh <= 0.01 or la <= 0.01:
                continue
            grid = score_grid_pure(lh, la)
            wdl_m = aggregate_wdl(grid)
            hcp_m = aggregate_handicap(grid, L)
            tg_m = aggregate_tg(grid)
            loss = ((wdl_m - wdl_probs)**2).sum() * 3.0 + ((hcp_m - hcp_probs)**2).sum() * 2.0 + ((tg_m[:len(tg_probs)] - tg_probs[:len(tg_m)])**2).sum() * 1.0
            if loss < best_loss:
                best_loss = loss
                best_lh, best_la = lh, la
    
    # 细网格精调
    for lh in np.arange(max(0.1, best_lh-0.15), best_lh+0.16, 0.02):
        for la in np.arange(max(0.1, best_la-0.15), best_la+0.16, 0.02):
            if lh <= 0.01 or la <= 0.01:
                continue
            grid = score_grid_pure(lh, la)
            wdl_m = aggregate_wdl(grid)
            hcp_m = aggregate_handicap(grid, L)
            tg_m = aggregate_tg(grid)
            loss = ((wdl_m - wdl_probs)**2).sum() * 3.0 + ((hcp_m - hcp_probs)**2).sum() * 2.0 + ((tg_m[:len(tg_probs)] - tg_probs[:len(tg_m)])**2).sum() * 1.0
            if loss < best_loss:
                best_loss = loss
                best_lh, best_la = lh, la
    
    return best_lh, best_la

# ============================================================
# 6. 评估函数
# ============================================================
def evaluate_match(lh, la, rho, actual_score):
    """评估一场比赛"""
    grid = score_grid(lh, la, rho)
    wdl = aggregate_wdl(grid)
    tg = aggregate_tg(grid)
    
    # 实际
    h_goals, a_goals = actual_score
    actual_wdl = 0 if h_goals > a_goals else 1 if h_goals == a_goals else 2
    actual_tg = h_goals + a_goals
    actual_over25 = 1 if actual_tg >= 3 else 0
    
    # 预测
    pred_wdl = np.argmax(wdl)
    pred_over25 = 1 if sum(tg[3:]) >= 0.5 else 0
    pred_tg_range = 0 if sum(tg[:2]) >= 0.5 else 1 if sum(tg[2:4]) >= 0.5 else 2  # 0-1, 2-3, 4+
    actual_tg_range = 0 if actual_tg <= 1 else 1 if actual_tg <= 3 else 2
    
    return {
        'direction_hit': 1 if pred_wdl == actual_wdl else 0,
        'over25_hit': 1 if pred_over25 == actual_over25 else 0,
        'tg_range_hit': 1 if pred_tg_range == actual_tg_range else 0,
        'pred_wdl': pred_wdl,
        'actual_wdl': actual_wdl,
        'pred_over25': pred_over25,
        'actual_over25': actual_over25,
        'lh': lh, 'la': la, 'rho': rho,
    }

# ============================================================
# 7. 主流程
# ============================================================
def main():
    print("=" * 70)
    print("全量回测：三项联动 λ 优化 vs 旧基线")
    print("=" * 70)
    
    c = connect(db_path=DB)
    
    # 获取所有可回测 match_id
    wdl_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM wdl_history WHERE match_id_en IS NOT NULL").fetchall())
    hcp_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM handicap_history WHERE match_id_en IS NOT NULL").fetchall())
    tg_en = set(r[0] for r in c.execute("SELECT DISTINCT match_id_en FROM total_goals_history WHERE match_id_en IS NOT NULL").fetchall())
    score_mids = set(r[0] for r in c.execute("SELECT match_id FROM matches WHERE actual_score IS NOT NULL AND actual_score != ''").fetchall())
    
    all_mids = sorted(wdl_en & hcp_en & tg_en & score_mids)
    print(f"可回测样本: {len(all_mids)} 场")
    
    # 批量获取数据
    print("加载赔率数据...")
    
    # 获取每场比赛的最后一条 WDL 赔率
    wdl_data = {}
    for mid in all_mids:
        r = c.execute(
            "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id_en=? ORDER BY timestamp DESC LIMIT 1",
            (mid,)
        ).fetchone()
        if r:
            wdl_data[mid] = (r[0], r[1], r[2])
    
    # 获取每场比赛的最后一条让球赔率
    hcp_data = {}
    for mid in all_mids:
        r = c.execute(
            "SELECT hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE match_id_en=? ORDER BY timestamp DESC LIMIT 1",
            (mid,)
        ).fetchone()
        if r:
            hcp_data[mid] = (r[0], r[1], r[2])
    
    # 获取每场比赛的最后一条总进球赔率
    tg_data = {}
    for mid in all_mids:
        r = c.execute(
            "SELECT goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus FROM total_goals_history WHERE match_id_en=? ORDER BY timestamp DESC LIMIT 1",
            (mid,)
        ).fetchone()
        if r:
            tg_data[mid] = tuple(r)
    
    # 获取实际比分和联赛
    match_info = {}
    for mid in all_mids:
        r = c.execute("SELECT actual_score, match_type FROM matches WHERE match_id=?", (mid,)).fetchone()
        if r and r[0]:
            parts = r[0].replace(' ', '').split('-')
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                match_info[mid] = {
                    'score': (int(parts[0]), int(parts[1])),
                    'league': r[1]
                }
    
    # 过滤：只保留有完整数据的
    valid_mids = [m for m in all_mids if m in wdl_data and m in hcp_data and m in tg_data and m in match_info]
    print(f"完整数据: {len(valid_mids)} 场")
    
    # 按联赛统计
    league_counts = defaultdict(int)
    season_counts = defaultdict(int)
    for mid in valid_mids:
        lg = match_info[mid]['league']
        for l in ['英超', '西甲', '意甲', '德甲', '法甲']:
            if l in lg:
                league_counts[l] += 1
                break
        if mid.startswith('2023'): season_counts['23/24'] += 1
        elif mid.startswith('2024'): season_counts['24/25'] += 1
        elif mid.startswith('2025'): season_counts['25/26'] += 1
        elif mid.startswith('2026'): season_counts['26/27'] += 1
    
    print("  联赛:", dict(league_counts))
    print("  赛季:", dict(season_counts))
    
    # 开始回测
    print(f"\n开始回测 {len(valid_mids)} 场...")
    t0 = time.time()
    
    results = {
        'A_baseline': {'direction': 0, 'over25': 0, 'tg_range': 0},
        'B_joint': {'direction': 0, 'over25': 0, 'tg_range': 0},
        'C_joint_rho': {'direction': 0, 'over25': 0, 'tg_range': 0},
    }
    
    details = []
    errors = 0
    
    for i, mid in enumerate(valid_mids):
        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{len(valid_mids)}")
            sys.stdout.flush()
        
        try:
            info = match_info[mid]
            actual_score = info['score']
            league = info['league']
            
            # 赔率→概率
            wdl_probs = odds_to_prob(wdl_data[mid])
            hcp_probs = odds_to_prob(hcp_data[mid])
            tg_probs = odds_to_prob(tg_data[mid])
            
            # A: 旧基线 λ（WDL 纯泊松反推）
            lh_a, la_a = wdl_lambda_from_fair_probs(wdl_probs[0], wdl_probs[1], wdl_probs[2])
            
            # 推断盘口线 L（用旧 λ）
            L = infer_handicap_line(lh_a, la_a, hcp_probs)
            
            # B: 新三项联动 λ
            lh_b, la_b = fit_joint_lambda(wdl_probs, hcp_probs, tg_probs, L)
            
            # 评估
            # A: ρ=-0.30
            r_a = evaluate_match(lh_a, la_a, RHO_DEFAULT, actual_score)
            # B: ρ=-0.30
            r_b = evaluate_match(lh_b, la_b, RHO_DEFAULT, actual_score)
            # C: ρ 切换
            rho_c = RHO_HIGH if abs(L) >= 1.0 else RHO_DEFAULT
            r_c = evaluate_match(lh_b, la_b, rho_c, actual_score)
            
            results['A_baseline']['direction'] += r_a['direction_hit']
            results['A_baseline']['over25'] += r_a['over25_hit']
            results['A_baseline']['tg_range'] += r_a['tg_range_hit']
            results['B_joint']['direction'] += r_b['direction_hit']
            results['B_joint']['over25'] += r_b['over25_hit']
            results['B_joint']['tg_range'] += r_b['tg_range_hit']
            results['C_joint_rho']['direction'] += r_c['direction_hit']
            results['C_joint_rho']['over25'] += r_c['over25_hit']
            results['C_joint_rho']['tg_range'] += r_c['tg_range_hit']
            
            details.append({
                'mid': mid, 'league': league, 'L': L,
                'lh_a': lh_a, 'la_a': la_a, 'lh_b': lh_b, 'la_b': la_b,
                'a_dir': r_a['direction_hit'], 'a_o25': r_a['over25_hit'], 'a_tgr': r_a['tg_range_hit'],
                'b_dir': r_b['direction_hit'], 'b_o25': r_b['over25_hit'], 'b_tgr': r_b['tg_range_hit'],
                'c_dir': r_c['direction_hit'], 'c_o25': r_c['over25_hit'], 'c_tgr': r_c['tg_range_hit'],
            })
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  错误 {mid}: {e}")
    
    elapsed = time.time() - t0
    n = len(valid_mids)
    
    # 输出结果
    print(f"\n{'='*70}")
    print(f"回测完成 ({n} 场, {elapsed:.1f}s, {errors} 错误)")
    print(f"{'='*70}")
    
    header = f"{'指标':<20} {'A 旧基线':>12} {'B 三项联动':>12} {'C 三项+ρ切换':>14}"
    print(header)
    print("-" * 60)
    
    for key, label in [('direction', '方向命中率'), ('over25', '大2.5命中率'), ('tg_range', 'TG区间(0-1/2-3/4+)')]:
        a = results['A_baseline'][key]
        b = results['B_joint'][key]
        c = results['C_joint_rho'][key]
        print(f"{label:<20} {a/n:>11.1%} {b/n:>11.1%} {c/n:>13.1%}")
        print(f"  (命中数)          {a:>6}/{n}     {b:>6}/{n}     {c:>6}/{n}")
    
    # 按联赛分组
    print(f"\n{'='*70}")
    print("按联赛分组")
    print(f"{'='*70}")
    
    league_results = defaultdict(lambda: {
        'A': {'dir': 0, 'o25': 0, 'tgr': 0, 'n': 0},
        'B': {'dir': 0, 'o25': 0, 'tgr': 0, 'n': 0},
        'C': {'dir': 0, 'o25': 0, 'tgr': 0, 'n': 0},
    })
    
    for d in details:
        lg = d['league']
        for l in ['英超', '西甲', '意甲', '德甲', '法甲']:
            if l in lg:
                lg_key = l
                break
        else:
            lg_key = '其他'
        
        for var, key in [('A', 'a'), ('B', 'b'), ('C', 'c')]:
            league_results[lg_key][var]['dir'] += d[f'{key}_dir']
            league_results[lg_key][var]['o25'] += d[f'{key}_o25']
            league_results[lg_key][var]['tgr'] += d[f'{key}_tgr']
            league_results[lg_key][var]['n'] += 1
    
    for lg in ['英超', '西甲', '意甲', '德甲', '法甲']:
        if lg in league_results:
            lr = league_results[lg]
            n_lg = lr['A']['n']
            print(f"\n  {lg} ({n_lg}场):")
            print(f"    {'':<18} {'A旧基线':>10} {'B三项联动':>10} {'C+ρ切换':>10}")
            print(f"    {'方向命中率':<18} {lr['A']['dir']/n_lg:>9.1%} {lr['B']['dir']/n_lg:>9.1%} {lr['C']['dir']/n_lg:>9.1%}")
            print(f"    {'大2.5命中率':<18} {lr['A']['o25']/n_lg:>9.1%} {lr['B']['o25']/n_lg:>9.1%} {lr['C']['o25']/n_lg:>9.1%}")
            print(f"    {'TG区间命中率':<18} {lr['A']['tgr']/n_lg:>9.1%} {lr['B']['tgr']/n_lg:>9.1%} {lr['C']['tgr']/n_lg:>9.1%}")
    
    # 按赛季分组
    print(f"\n{'='*70}")
    print("按赛季分组")
    print(f"{'='*70}")
    
    season_results = defaultdict(lambda: {
        'A': {'dir': 0, 'o25': 0, 'tgr': 0, 'n': 0},
        'B': {'dir': 0, 'o25': 0, 'tgr': 0, 'n': 0},
        'C': {'dir': 0, 'o25': 0, 'tgr': 0, 'n': 0},
    })
    
    for d in details:
        mid = d['mid']
        if mid.startswith('2023'): sz = '23/24'
        elif mid.startswith('2024'): sz = '24/25'
        elif mid.startswith('2025'): sz = '25/26'
        elif mid.startswith('2026'): sz = '26/27'
        else: sz = '?'
        
        for var, key in [('A', 'a'), ('B', 'b'), ('C', 'c')]:
            season_results[sz][var]['dir'] += d[f'{key}_dir']
            season_results[sz][var]['o25'] += d[f'{key}_o25']
            season_results[sz][var]['tgr'] += d[f'{key}_tgr']
            season_results[sz][var]['n'] += 1
    
    for sz in ['23/24', '24/25', '25/26', '26/27']:
        if sz in season_results:
            sr = season_results[sz]
            n_sz = sr['A']['n']
            print(f"\n  {sz} ({n_sz}场):")
            print(f"    {'':<18} {'A旧基线':>10} {'B三项联动':>10} {'C+ρ切换':>10}")
            print(f"    {'方向命中率':<18} {sr['A']['dir']/n_sz:>9.1%} {sr['B']['dir']/n_sz:>9.1%} {sr['C']['dir']/n_sz:>9.1%}")
            print(f"    {'大2.5命中率':<18} {sr['A']['o25']/n_sz:>9.1%} {sr['B']['o25']/n_sz:>9.1%} {sr['C']['o25']/n_sz:>9.1%}")
            print(f"    {'TG区间命中率':<18} {sr['A']['tgr']/n_sz:>9.1%} {sr['B']['tgr']/n_sz:>9.1%} {sr['C']['tgr']/n_sz:>9.1%}")
    
    # 保存详细结果
    out_path = BASE / "reports" / "backtest_three_way_full.json"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'summary': {k: {kk: vv for kk, vv in v.items()} for k, v in results.items()},
            'total': n,
            'by_league': {k: {kk: {kkk: vvv for kkk, vvv in vv.items()} for kk, vv in v.items()} for k, v in league_results.items()},
            'by_season': {k: {kk: {kkk: vvv for kkk, vvv in vv.items()} for kk, vv in v.items()} for k, v in season_results.items()},
            'details': details[:100],  # 只保存前100条详情
        }, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {out_path}")
    
    c.close()

if __name__ == "__main__":
    main()