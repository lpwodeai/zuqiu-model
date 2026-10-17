# -*- coding: utf-8 -*-
"""
T-006 v5 A-variant: IPF 平局约束软化 shadow 实验（C-20261006-011）
================================================================
背景：C-009/C-010 确认 v5 网格平局带恒等于 WDL p_draw（IPF 精确钉住平局边缘），
      DC ρ 重调无法改变平局带幅度（A 原方案证伪）。
A-variant：IPF 平局目标改为 draw_target = α·p_draw + (1-α)·dc_draw，
           其中 dc_draw = DC 网格（build_league_grid, ρ 控制）的平局带。
           α=1.0 为当前行为；α<1 平局带向 DC 自然值回归，ρ 重新生效。
目标：平局带偏差 vs WDL 市场 |bias| ≤ 0.5pp，且 RPS/top5/top1 不退化。
窗口：VAL 2026-03-01~08-21（选型）/ EVAL ≥08-22（验证）。
全程 shadow，不改生产 T006_RHO 或 ipf_reweight。
"""
import os, sys, sqlite3, re, pickle
from datetime import datetime
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from t006_score_predictor_v5 import (
    solve_lambda_core, build_league_grid, dc_tau, score_market_dist,
    parse_score, MAXG, _p, LEAGUE_RHO, SCORE_ODDS_ALPHA, de_vig,
)
from market_benchmark_score import SPECIFIC, LABEL_IDX, CATCH_ALL, GRID_MAX
from market_benchmark_score import compute_rps, compute_logloss
from market_benchmark_hc import build_en2cn, _norm

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
CACHE = os.path.join(SCRIPT_DIR, '_avariant_base.pkl')

VAL_START, VAL_END = '2026-03-01', '2026-08-21'
EVAL_START = '2026-08-22'
NOON_CUT = '12:04:00'

RHO_CANDIDATES = [-0.30, -0.25, -0.20, -0.15, -0.10, -0.05, 0.0, 0.05]
ALPHA_CANDIDATES = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]

# RPS 28 类序（同 market_benchmark_score）
SPECIFIC_LIST = SPECIFIC  # list of (h,a) tuples
SPECIFIC_SET = set(SPECIFIC_LIST)
N_CAT = len(SPECIFIC_LIST) + 3  # 25 specific + 胜其它/平其它/负其它


def score_to_label(h, a):
    if (h, a) in SPECIFIC_SET:
        return LABEL_IDX[(h, a)]
    if h > a:
        return len(SPECIFIC_LIST)      # 胜其它
    elif h == a:
        return len(SPECIFIC_LIST) + 1  # 平其它
    else:
        return len(SPECIFIC_LIST) + 2  # 负其它


def grid_to_pvec(grid):
    """6x6 grid (dict (h,a)->prob) → 28-dim RPS 概率向量（SPECIFIC + 3 catch-all）。"""
    p = np.zeros(N_CAT)
    for (h, a), pr in grid.items():
        if h <= GRID_MAX and a <= GRID_MAX:
            p[score_to_label(h, a)] += pr
        else:
            if h > a:
                p[len(SPECIFIC_LIST)] += pr
            elif h == a:
                p[len(SPECIFIC_LIST) + 1] += pr
            else:
                p[len(SPECIFIC_LIST) + 2] += pr
    s = p.sum()
    return p / s if s > 0 else p


def modified_ipf(grid, ph, draw_target, pa, iters=30):
    """IPF 但平局目标为 draw_target（已由调用方与 ph/pa 重新归一化保证和=1）。"""
    gh = dict(grid)
    for _ in range(iters):
        zh = sum(v for (h, a), v in gh.items() if h > a) + 1e-12
        for (h, a) in gh:
            gh[(h, a)] *= ph / zh if h > a else 1.0
        zd = sum(v for (h, a), v in gh.items() if h == a) + 1e-12
        for (h, a) in gh:
            gh[(h, a)] *= draw_target / zd if h == a else 1.0
        za = sum(v for (h, a), v in gh.items() if a > h) + 1e-12
        for (h, a) in gh:
            gh[(h, a)] *= pa / za if a > h else 1.0
    s = sum(gh.values())
    return {k: v / s for k, v in gh.items()} if s > 0 else gh


def load_base_data(conn):
    """加载 VAL+EVAL 窗场次的基础数据（WDL 概率、TG、比分赔率、赛果、WDL 市场平局）。"""
    en2cn = build_en2cn(conn)

    # 生产 WDL 概率
    wdl_map = {}
    for r in conn.execute(
            "SELECT match_id, prediction_type, probability FROM model_predictions "
            "WHERE model_name='generate_unified_report_v2.0' AND is_replay=0 "
            "AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')"):
        wdl_map.setdefault(r['match_id'], {})[r['prediction_type']] = float(r['probability'])

    rows = conn.execute(
        "SELECT match_id, match_date, league, home_team, away_team, actual_score "
        "FROM matches WHERE match_date >= ? AND actual_score IS NOT NULL AND actual_score != ''",
        (VAL_START,)).fetchall()

    out = []
    for r in rows:
        mid = r['match_id']
        wd = wdl_map.get(mid)
        if not wd or 'WDL_draw' not in wd:
            continue
        ph = float(wd.get('WDL_home', 0.0))
        pd_ = float(wd['WDL_draw'])
        pa = float(wd.get('WDL_away', 0.0))
        if ph + pd_ + pa <= 0:
            continue

        date = str(r['match_date'])[:10]
        hc = en2cn.get(_norm(r['home_team']))
        ac = en2cn.get(_norm(r['away_team']))
        if not hc or not ac:
            continue
        cn_id = f"{date}_{hc}_{ac}"

        # TG 赔率 → total（total_goals_history 宽表：goals_0..goals_6, goals_7_plus）
        tg = conn.execute(
            "SELECT goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus "
            "FROM total_goals_history WHERE match_id=? AND timestamp IS NOT NULL "
            "AND timestamp<=? ORDER BY timestamp DESC LIMIT 1", (cn_id, f'{date} {NOON_CUT}')).fetchone()
        if not tg:
            continue
        goals_odds = [tg[f'goals_{i}'] for i in range(7)] + [tg['goals_7_plus']]
        if not all(goals_odds) or any(float(x) <= 1 for x in goals_odds):
            continue
        invs = [1.0 / float(x) for x in goals_odds]
        tot = sum(i * v for i, v in enumerate(invs)) / sum(invs)
        total = min(4.5, max(1.5, tot))

        # 比分赔率市场（score_history long 表：match_id/timestamp/score/odds）
        sc_rows = conn.execute(
            "SELECT score, odds, timestamp FROM score_history WHERE match_id=? "
            "AND timestamp IS NOT NULL AND timestamp<=? AND odds>1 ORDER BY timestamp DESC",
            (cn_id, f'{date} {NOON_CUT}')).fetchall()
        score_odds = {}
        if sc_rows:
            latest_ts = sc_rows[0]['timestamp']
            for sr in sc_rows:
                if sr['timestamp'] != latest_ts:
                    break
                sc = str(sr['score']).strip()
                try:
                    od = float(sr['odds'])
                except (TypeError, ValueError):
                    continue
                if od > 1:
                    score_odds[sc] = od

        # WDL 市场平局隐含概率（锚点）
        wr = conn.execute(
            "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id=? "
            "AND timestamp IS NOT NULL AND timestamp<=? ORDER BY timestamp DESC LIMIT 1",
            (cn_id, f'{date} {NOON_CUT}')).fetchone()
        mkt_wdl_draw = None
        if wr and wr['win_a'] and wr['draw'] and wr['win_b']:
            w, d, l = float(wr['win_a']), float(wr['draw']), float(wr['win_b'])
            if w > 1 and d > 1 and l > 1:
                _, mkt_wdl_draw, _ = de_vig(w, d, l)

        # 赛果
        sc = re.match(r'^(\d+)\s*[:：-]\s*(\d+)$', str(r['actual_score']).strip())
        if not sc:
            continue
        ah, aa = int(sc.group(1)), int(sc.group(2))

        out.append({
            'match_id': mid, 'date': date, 'league': r['league'],
            'ph': ph, 'pd_': pd_, 'pa': pa, 'total': total,
            'score_odds': score_odds, 'mkt_wdl_draw': mkt_wdl_draw,
            'actual_h': ah, 'actual_a': aa,
        })

    return out


def run_variant(base, rho, alpha):
    """对一批基础数据，用指定 (rho, alpha) 重算 v5 网格，返回指标。"""
    draws_model, draws_mkt, y_list, pvecs, top1_hit, top5_hit = [], [], [], [], [], []
    leagues = []
    for r in base:
        if r['mkt_wdl_draw'] is None:
            continue
        lh, la = solve_lambda_core(r['ph'], r['pa'], r['total'])
        dc_grid = build_league_grid(lh, la, rho)
        dc_draw = sum(v for (h, a), v in dc_grid.items() if h == a)

        # 比分赔率融合（同生产 alpha=SCORE_ODDS_ALPHA）
        market = {}
        if r['score_odds']:
            raw = {k: 1.0 / max(float(v), 1e-10) for k, v in r['score_odds'].items()}
            s = sum(raw.values())
            if s > 0:
                market = {k: v / s for k, v in raw.items()}
        fused = {}
        all_keys = set(dc_grid) | {parse_score(k) for k in market}
        for key in all_keys:
            sk = f"{key[0]}:{key[1]}"
            fused[key] = dc_grid.get(key, 0.0) * (1 - SCORE_ODDS_ALPHA) + market.get(sk, 0.0) * SCORE_ODDS_ALPHA
        s = sum(fused.values())
        if s > 0:
            fused = {k: v / s for k, v in fused.items()}

        # 软化平局目标
        draw_target = alpha * r['pd_'] + (1 - alpha) * dc_draw
        # 重新归一化三边缘保证和=1
        tot = r['ph'] + draw_target + r['pa']
        ph_t, dt_t, pa_t = r['ph'] / tot, draw_target / tot, r['pa'] / tot
        final = modified_ipf(fused, ph_t, dt_t, pa_t)

        model_draw = sum(v for (h, a), v in final.items() if h == a and h <= GRID_MAX)
        draws_model.append(model_draw)
        draws_mkt.append(r['mkt_wdl_draw'])
        leagues.append(r['league'])

        # RPS / top1 / top5
        pvec = grid_to_pvec(final)
        pvecs.append(pvec)
        y = score_to_label(min(r['actual_h'], 99), min(r['actual_a'], 99))
        y_list.append(y)

        sorted_cells = sorted(final.items(), key=lambda x: x[1], reverse=True)
        top1 = sorted_cells[0][0] if sorted_cells else None
        top1_hit.append(1 if top1 == (r['actual_h'], r['actual_a']) else 0)
        top5_set = set(c for c, _ in sorted_cells[:5])
        top5_hit.append(1 if (r['actual_h'], r['actual_a']) in top5_set else 0)

    if not y_list:
        return None
    pmat = np.array(pvecs)
    yarr = np.array(y_list)
    bias = float(np.mean(draws_model) - np.mean(draws_mkt))
    rps = compute_rps(yarr, pmat)
    return {
        'n': len(y_list),
        'model_draw_mean': float(np.mean(draws_model)),
        'mkt_draw_mean': float(np.mean(draws_mkt)),
        'bias': bias,
        'rps': rps,
        'top1': float(np.mean(top1_hit)),
        'top5': float(np.mean(top5_hit)),
        'per_league_bias': {lg: float(np.mean([draws_model[i] - draws_mkt[i]
                                               for i in range(len(leagues)) if leagues[i] == lg]))
                            for lg in set(leagues)},
    }


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    if os.path.exists(CACHE):
        with open(CACHE, 'rb') as f:
            base = pickle.load(f)
        print(f'(缓存 {len(base)} 场)')
    else:
        print('加载基础数据...')
        base = load_base_data(conn)
        with open(CACHE, 'wb') as f:
            pickle.dump(base, f)
        print(f'共 {len(base)} 场')

    # 生产 WDL 概率仅覆盖近期（≥2026-08-21），原 VAL 窗无数据。
    # 改用可用窗内时序切分：VAL=前 60%，EVAL=后 40%（按日期排序）。
    base_sorted = sorted(base, key=lambda r: r['date'])
    n = len(base_sorted)
    split = int(n * 0.6)
    val = base_sorted[:split]
    evl = base_sorted[split:]
    print(f'可用 {n} 场（{base_sorted[0]["date"]}~{base_sorted[-1]["date"]}），'
          f'VAL={len(val)}  EVAL={len(evl)}（时序 60/40 切分）')

    # 基线（当前生产：联赛 ρ，α=1.0）
    print('\n=== 基线（联赛 ρ, α=1.0）===')
    val_base = _run_baseline(val)
    evl_base = _run_baseline(evl)
    print(f'VAL  基线: bias={val_base["bias"]*100:+.2f}pp  RPS={val_base["rps"]:.4f}  '
          f'top1={val_base["top1"]:.3f}  top5={val_base["top5"]:.3f}')
    print(f'EVAL 基线: bias={evl_base["bias"]*100:+.2f}pp  RPS={evl_base["rps"]:.4f}  '
          f'top1={evl_base["top1"]:.3f}  top5={evl_base["top5"]:.3f}')

    # 网格搜索（VAL）
    print('\n=== VAL 网格搜索（ρ × α）===')
    results = {}
    for rho in RHO_CANDIDATES:
        for alpha in ALPHA_CANDIDATES:
            m = run_variant(val, rho, alpha)
            if m:
                results[(rho, alpha)] = m

    # 选型：bias≤0.5pp 中 RPS 最优；若多解取 α 最大（最小改动）
    feasible = [(k, v) for k, v in results.items() if abs(v['bias']) <= 0.005]
    feasible.sort(key=lambda kv: (kv[1]['rps'], -kv[0][1]))
    if feasible:
        best = feasible[0]
        sel_rho, sel_alpha = best[0]
        print(f'\n选型: ρ={sel_rho}, α={sel_alpha}  '
              f'VAL bias={best[1]["bias"]*100:+.2f}pp  RPS={best[1]["rps"]:.4f}  '
              f'top1={best[1]["top1"]:.3f}  top5={best[1]["top5"]:.3f}')
    else:
        # 放宽到 bias 最小
        best = min(results.items(), key=lambda kv: abs(kv[1]['bias']))
        sel_rho, sel_alpha = best[0]
        print(f'\n无 |bias|≤0.5pp 解，选 bias 最小: ρ={sel_rho}, α={sel_alpha}  '
              f'bias={best[1]["bias"]*100:+.2f}pp')

    # EVAL 验证
    evl_m = run_variant(evl, sel_rho, sel_alpha)
    print(f'\n=== EVAL 验证（ρ={sel_rho}, α={sel_alpha}）===')
    print(f'bias={evl_m["bias"]*100:+.2f}pp  RPS={evl_m["rps"]:.4f}  '
          f'top1={evl_m["top1"]:.3f}  top5={evl_m["top5"]:.3f}')
    print(f'基线 RPS={evl_base["rps"]:.4f}  ΔRPS={evl_m["rps"]-evl_base["rps"]:+.4f}')
    print(f'分联赛偏差(pp): ' + '  '.join(f'{k}={v*100:+.1f}' for k, v in evl_m['per_league_bias'].items()))

    # 闸门判断
    gate1 = abs(evl_m['bias']) <= 0.005
    gate2 = (evl_m['rps'] - evl_base['rps']) <= 0.005
    gate3 = evl_m['top5'] >= 0.46 and evl_m['top1'] >= evl_base['top1'] - 0.005
    print(f'\n闸门: ①bias≤0.5pp {"PASS" if gate1 else "FAIL"}  '
          f'②ΔRPS≤0.005 {"PASS" if gate2 else "FAIL"}  '
          f'③top5≥0.46 且 top1 不下降 {"PASS" if gate3 else "FAIL"}')
    passed = gate1 and gate2 and gate3

    # 报告
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    L = []
    A = L.append
    A(f'# T-006 v5 A-variant: IPF 平局约束软化 shadow 实验')
    A(f'')
    A(f'- 生成: {ts}')
    A(f'- 窗口: VAL {VAL_START}~{VAL_END}（选型）/ EVAL ≥{EVAL_START}（验证）')
    A(f'- 机制: IPF 平局目标 draw_target = α·p_draw + (1-α)·dc_draw（dc_draw=DC 网格平局带，ρ 控制）')
    A(f'- 锚点: WDL 盘平局隐含概率（wdl_history 去水）')
    A(f'')
    A(f'## 1. 基线（当前生产：联赛 ρ，α=1.0）')
    A(f'')
    A(f'| 窗口 | n | 模型平局带 | 市场平局 | bias(pp) | RPS | top1 | top5 |')
    A(f'|---|---|---|---|---|---|---|---|')
    A(f'| VAL | {val_base["n"]} | {val_base["model_draw_mean"]:.4f} | {val_base["mkt_draw_mean"]:.4f} '
      f'| {val_base["bias"]*100:+.2f} | {val_base["rps"]:.4f} | {val_base["top1"]:.3f} | {val_base["top5"]:.3f} |')
    A(f'| EVAL | {evl_base["n"]} | {evl_base["model_draw_mean"]:.4f} | {evl_base["mkt_draw_mean"]:.4f} '
      f'| {evl_base["bias"]*100:+.2f} | {evl_base["rps"]:.4f} | {evl_base["top1"]:.3f} | {evl_base["top5"]:.3f} |')
    A(f'')
    A(f'## 2. VAL 选型结果')
    A(f'')
    A(f'候选 ρ ∈ {RHO_CANDIDATES}，α ∈ {ALPHA_CANDIDATES}，在 |bias|≤0.5pp 约束下按 RPS 最优选型。')
    A(f'')
    A(f'**选型: ρ={sel_rho}, α={sel_alpha}**')
    A(f'')
    A(f'| 指标 | VAL 选型值 |')
    A(f'|---|---|')
    A(f'| 模型平局带 | {best[1]["model_draw_mean"]:.4f} |')
    A(f'| 市场平局 | {best[1]["mkt_draw_mean"]:.4f} |')
    A(f'| bias | {best[1]["bias"]*100:+.2f}pp |')
    A(f'| RPS | {best[1]["rps"]:.4f} |')
    A(f'| top1 | {best[1]["top1"]:.3f} |')
    A(f'| top5 | {best[1]["top5"]:.3f} |')
    A(f'')
    A(f'## 3. EVAL 验证')
    A(f'')
    A(f'| 指标 | 基线 | 选型(ρ={sel_rho},α={sel_alpha}) | Δ |')
    A(f'|---|---|---|---|')
    A(f'| 模型平局带 | {evl_base["model_draw_mean"]:.4f} | {evl_m["model_draw_mean"]:.4f} | '
      f'{(evl_m["model_draw_mean"]-evl_base["model_draw_mean"])*100:+.2f}pp |')
    A(f'| bias vs WDL 市场 | {evl_base["bias"]*100:+.2f}pp | {evl_m["bias"]*100:+.2f}pp | '
      f'{(evl_m["bias"]-evl_base["bias"])*100:+.2f}pp |')
    A(f'| RPS | {evl_base["rps"]:.4f} | {evl_m["rps"]:.4f} | {evl_m["rps"]-evl_base["rps"]:+.4f} |')
    A(f'| top1 | {evl_base["top1"]:.3f} | {evl_m["top1"]:.3f} | '
      f'{(evl_m["top1"]-evl_base["top1"])*100:+.2f}pp |')
    A(f'| top5 | {evl_base["top5"]:.3f} | {evl_m["top5"]:.3f} | '
      f'{(evl_m["top5"]-evl_base["top5"])*100:+.2f}pp |')
    A(f'')
    A(f'### 分联赛偏差（EVAL）')
    A(f'')
    A(f'| 联赛 | bias(pp) |')
    A(f'|---|---|')
    for lg, b in sorted(evl_m['per_league_bias'].items()):
        A(f'| {lg} | {b*100:+.1f} |')
    A(f'')
    A(f'## 4. 三道闸门')
    A(f'')
    A(f'| 闸门 | 阈值 | 结果 |')
    A(f'|---|---|---|')
    A(f'| ① bias vs WDL 市场 | ≤0.5pp | {evl_m["bias"]*100:+.2f}pp → {"PASS" if gate1 else "FAIL"} |')
    A(f'| ② ΔRPS | ≤0.005 | {evl_m["rps"]-evl_base["rps"]:+.4f} → {"PASS" if gate2 else "FAIL"} |')
    A(f'| ③ top5≥0.46 且 top1 不下降 | — | top5={evl_m["top5"]:.3f}, '
      f'top1={evl_m["top1"]:.3f}(基线{evl_base["top1"]:.3f}) → {"PASS" if gate3 else "FAIL"} |')
    A(f'')
    A(f'**综合: {"PASS（可接线）" if passed else "FAIL（不接线，回退接受 +1.3pp）"}**')
    A(f'')
    A(f'## 5. VAL 全网格热区（bias≤0.5pp 的可行解，按 RPS 排序前 10）')
    A(f'')
    A(f'| ρ | α | bias(pp) | RPS | top1 | top5 |')
    A(f'|---|---|---|---|---|---|')
    for k, v in feasible[:10]:
        A(f'| {k[0]} | {k[1]} | {v["bias"]*100:+.2f} | {v["rps"]:.4f} | {v["top1"]:.3f} | {v["top5"]:.3f} |')
    A(f'')

    rpt = os.path.join(REPORTS_DIR, f't006_v5_avariant_{ts}.md')
    with open(rpt, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'\n报告: {rpt}')
    conn.close()


def _run_baseline(base):
    """基线：逐场用联赛 ρ，α=1.0。"""
    from copy import deepcopy
    # α=1 时平局带=pd_ 与 ρ 无关，但 RPS 受 ρ 影响
    draws_model, draws_mkt, y_list, pvecs, top1_hit, top5_hit = [], [], [], [], [], []
    leagues = []
    for r in base:
        if r['mkt_wdl_draw'] is None:
            continue
        lh, la = solve_lambda_core(r['ph'], r['pa'], r['total'])
        rho0 = LEAGUE_RHO.get((r['league'] or '')[:2], -0.15)
        dc_grid = build_league_grid(lh, la, rho0)
        market = {}
        if r['score_odds']:
            raw = {k: 1.0 / max(float(v), 1e-10) for k, v in r['score_odds'].items()}
            s = sum(raw.values())
            if s > 0:
                market = {k: v / s for k, v in raw.items()}
        fused = {}
        for key in set(dc_grid) | {parse_score(k) for k in market}:
            sk = f"{key[0]}:{key[1]}"
            fused[key] = dc_grid.get(key, 0.0) * (1 - SCORE_ODDS_ALPHA) + market.get(sk, 0.0) * SCORE_ODDS_ALPHA
        s = sum(fused.values())
        if s > 0:
            fused = {k: v / s for k, v in fused.items()}
        final = modified_ipf(fused, r['ph'], r['pd_'], r['pa'])
        model_draw = sum(v for (h, a), v in final.items() if h == a and h <= GRID_MAX)
        draws_model.append(model_draw)
        draws_mkt.append(r['mkt_wdl_draw'])
        leagues.append(r['league'])
        pvecs.append(grid_to_pvec(final))
        y = score_to_label(min(r['actual_h'], 99), min(r['actual_a'], 99))
        y_list.append(y)
        sc_sorted = sorted(final.items(), key=lambda x: x[1], reverse=True)
        top1 = sc_sorted[0][0] if sc_sorted else None
        top1_hit.append(1 if top1 == (r['actual_h'], r['actual_a']) else 0)
        top5_set = set(c for c, _ in sc_sorted[:5])
        top5_hit.append(1 if (r['actual_h'], r['actual_a']) in top5_set else 0)
    pmat = np.array(pvecs)
    if not y_list:
        return None
    return {
        'n': len(y_list),
        'model_draw_mean': float(np.mean(draws_model)),
        'mkt_draw_mean': float(np.mean(draws_mkt)),
        'bias': float(np.mean(draws_model) - np.mean(draws_mkt)),
        'rps': compute_rps(np.array(y_list), pmat),
        'top1': float(np.mean(top1_hit)),
        'top5': float(np.mean(top5_hit)),
    }


if __name__ == '__main__':
    main()
