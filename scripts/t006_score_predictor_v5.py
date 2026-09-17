# -*- coding: utf-8 -*-
"""T-006 v5 比分预测原型（与 v4 同源回测对比，严格时序门禁）

改进点（方案 §五/C2 v5 方向之一：贝叶斯多层λ + WDL 边缘一致，降级聚焦可落地增量）：
  1. λ 推导：不再用 v4 的 λ=2.8×胜率，改为从**去水 WDL 赔率**数值求解强度差 d，
     使泊松边际的胜负倾向与去水 WDL 一致（含球队近期进球形态调制总进球）。
  2. 总进球先验：用 score_history 隐含总进球（v4 保留该信号）叠加球队近期进球率。
  3. WDL 边缘重加权：对比分网格做**乘性 IPF**，精确对准去水 WDL 三概率
     ——从构造上保证「不劣化 WDL 栈」门禁（v4 不保证、v5 强制）。

严格时序门禁：
  - TimeSeriesSplit(n_splits=5, 无 shuffle) 逐折回测
  - 球队近期形态只取该场 date 之前场次（防泄漏）
  - 对比 v4/v5 在同一"有赔率"子集：Top-1/3/5 精确命中 + WDL 方向一致率

用法： python scripts/t006_score_predictor_v5.py
产物： reports/t006_v5_ablation.json + console 摘要
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson
from sklearn.model_selection import TimeSeriesSplit

from t006_score_predictor import parse_score, load_match_data

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

MAXG = 7

# 预计算 λ∈[0.15,6.0] 步长0.05 的泊松 PMF 查表，避免内层 scipy 标量调用
_LAM_GRID = np.arange(0.15, 6.01, 0.05)
_PG = np.arange(MAXG + 1)
_PMF_TBL = {int(g): poisson.pmf(g, _LAM_GRID) for g in _PG}


def _p(g, lam):
    idx = int(np.clip(np.round((lam - 0.15) / 0.05), 0, len(_LAM_GRID) - 1))
    return float(_PMF_TBL[int(round(g))][idx])


def de_vig(w_a, w_d, w_b):
    ia, id_, ib = 1.0 / w_a, 1.0 / w_d, 1.0 / w_b
    s = ia + id_ + ib
    return ia / s, id_ / s, ib / s


def dc_tau(h, a, lh, la, rho=-0.15):
    if h <= 1 and a <= 1:
        if h == 0 and a == 0:
            return 1.0 - lh * la * rho
        if h == 0 and a == 1:
            return 1.0 + lh * rho
        if h == 1 and a == 0:
            return 1.0 + la * rho
        if h == 1 and a == 1:
            return 1.0 - rho
    return 1.0


def build_score_grid(lh, la):
    grid = {}
    for h in range(MAXG + 1):
        for a in range(MAXG + 1):
            grid[(h, a)] = _p(h, lh) * _p(a, la) * dc_tau(h, a, lh, la)
    s = sum(grid.values())
    if s > 0:
        for k in grid:
            grid[k] /= s
    return grid


def solve_lambda_core(p_home, p_away, total):
    target = max(-0.999, min(0.999, (p_home - p_away)))
    lo, hi = -2.0, 2.0

    def ex_win_diff(d):
        lh = max(0.15, (total + d) / 2.0)
        la = max(0.15, (total - d) / 2.0)
        ph = sum((h > a) * _p(h, lh) * _p(a, la)
                 for h in range(MAXG + 1) for a in range(MAXG + 1))
        return ph - (1 - ph)  # Pwin_home - Pwin_away
    for _ in range(45):
        mid = (lo + hi) / 2.0
        if ex_win_diff(mid) > target:
            hi = mid
        else:
            lo = mid
    d = (lo + hi) / 2.0
    return max(0.15, min(6.0, (total + d) / 2.0)), max(0.15, min(6.0, (total - d) / 2.0))


def implied_total(score_odds):
    est = 0.0
    for score, odds_val in score_odds.items():
        h, a = parse_score(score)
        if h is not None:
            est += (1.0 / odds_val) * (h + a)
    return est


def ipf_reweight(grid, p_home, p_draw, p_away, iters=30):
    """乘性 IPF：把比分网格三边缘精确对准 (p_home, p_draw, p_away)。"""
    gh = {(h, a): v for (h, a), v in grid.items()}
    for _ in range(iters):
        # home marginal
        zh = sum(v for (h, a), v in gh.items() if h > a) + 1e-12
        for (h, a) in gh:
            gh[(h, a)] *= p_home / zh if h > a else 1.0
        zd = sum(v for (h, a), v in gh.items() if h == a) + 1e-12
        for (h, a) in gh:
            gh[(h, a)] *= p_draw / zd if h == a else 1.0
        za = sum(v for (h, a), v in gh.items() if a > h) + 1e-12
        for (h, a) in gh:
            gh[(h, a)] *= p_away / za if a > h else 1.0
    s = sum(gh.values())
    return {k: v / s for k, v in gh.items()} if s > 0 else gh


def recency_form(matches):
    """每队(严格 date 之前)滚动进球形态：GF/(GF+GA)、场均进球。字典覆盖用。"""
    ev = {}
    for m in matches:
        hg, ag = m['home_goals'], m['away_goals']
        for team, gf, ga, dt in (
                (m['home_team'], hg, ag, m['match_date'] if 'match_date' in m else None),
                (m['away_team'], ag, hg, m['match_date'] if 'match_date' in m else None)):
            if gf is None:
                continue
            ev.setdefault(team, []).append((gf, ga, dt))
    form = {}
    for team, arr in ev.items():
        arr.sort(key=lambda x: x[2] if x[2] is not None else '')
        form[team] = arr
    return form


def predict_score_distribution_v5(wdl_probs, total_goals_expected=None, max_goals=5):
    """C2 集成公共 API：给定去水 WDL 三概率 + 可选总进球期望，返回 (score_probs, lambda_home, lambda_away)。

    - λ：数值求解强度差使泊松胜负倾向对齐 WDL（v5 核心，替代 λ=2.8×胜率）
    - IPF 乘性重加权使比分网格 WDL 三边缘精确对准 wdl_probs（构造保证不劣化 WDL 栈）
    - 总进球期望(来自 tg_history/score_history)融入 total 先验，兼顾大小球信号且不破坏 WDL 边缘
    - 返回 dict 键为 'h:a' 字符串，与 score_prediction_module 兼容
    """
    ph, pd_, pa = float(wdl_probs[0]), float(wdl_probs[1]), float(wdl_probs[2])
    total = 2.6
    if total_goals_expected and total_goals_expected > 0.5:
        total = 0.5 * 2.6 + 0.5 * min(4.5, max(2.0, float(total_goals_expected)))
    lh, la = solve_lambda_core(ph, pa, total)
    grid = build_score_grid(lh, la)
    grid = ipf_reweight(grid, ph, pd_, pa)
    # 仅保留 max_goals 内比分即可（>max_goals 概率极低，不影响 WDL 边际保证由内部 MAXG=7 已满足）
    out = {f"{h}:{a}": v for (h, a), v in grid.items() if h <= max_goals and a <= max_goals}
    total_p = sum(out.values())
    if total_p > 0:
        out = {k: v / total_p for k, v in out.items()}
    return out, lh, la


# 预计算 λ∈[0.15,6.0] 步长0.05 的泊松 PMF 查表，避免内层 scipy 标量调用（模块级初始化于底部）


def _load_production_wdl_map(conn):
    """match_id -> [home, draw, away] 生产 WDL 概率（model_predictions WDL_home/draw/away）。"""
    try:
        rows = conn.execute(
            "SELECT match_id, prediction_type, probability FROM model_predictions "
            "WHERE prediction_type IN ('WDL_home','WDL_draw','WDL_away')"
        ).fetchall()
    except Exception:
        return {}
    m = {}
    for mid, ptype, prob in rows:
        if prob is None:
            continue
        m.setdefault(mid, {})[ptype] = float(prob)
    out = {}
    for mid, d in m.items():
        if len(d) == 3:
            out[mid] = [d['WDL_home'], d['WDL_draw'], d['WDL_away']]
    return out


def run_production_anchor_compare(report_path=None):
    """生产 WDL 概率锚定对比回测（严格时序 5 折，全量实际比分场次）。

    对比两种 v5 变体：
      - v5_de_vig: 有赔率→去水 WDL 锚定；无赔率→默认均线回退（生产现状）
      - v5_prod:   优先用 model_predictions 生产 WDL 概率锚定（无赔率场次亦生效），缺省回退 de_vig
    逐折统计 Top-1/3/5 + WDL 方向一致率，输出到 reports/t006_v5_production_anchor.json。
    """
    conn = sqlite3.connect(str(DB_PATH))
    matches = load_match_data(conn)
    try:
        dmap = dict(conn.execute("SELECT match_id, match_date FROM matches").fetchall())
    except Exception:
        dmap = {}
    prod_map = _load_production_wdl_map(conn)
    odds_cache, score_cache = {}, {}
    from t006_score_predictor import load_wdl_odds, load_score_odds
    for m in matches:
        sh = m['sh_match_id']
        odds_cache[m['match_id']] = load_wdl_odds(conn, sh)
        score_cache[m['match_id']] = load_score_odds(conn, sh)
    conn.close()

    def mdate(m):
        d = dmap.get(m['match_id'])
        if not d:
            return pd.Timestamp('2016-08-01')
        try:
            return pd.Timestamp(d)
        except Exception:
            return pd.Timestamp('2016-08-01')

    for m in matches:
        m['_dt'] = mdate(m)
    matches = sorted(matches, key=lambda x: x['_dt'])
    df = pd.DataFrame({k: [m[k] for m in matches] for k in
                       ['match_id', 'home_team', 'away_team', 'actual_score', 'sh_match_id', '_dt']})
    df['home_goals'] = [m['home_goals'] for m in matches]
    df['away_goals'] = [m['away_goals'] for m in matches]

    tscv = TimeSeriesSplit(n_splits=5)
    stats = {'v5_de_vig': {'top1': [], 'top3': [], 'top5': [], 'wdl': []},
             'v5_prod': {'top1': [], 'top3': [], 'top5': [], 'wdl': []},
             'anchored': {'vig': {'top1': [], 'top3': [], 'top5': [], 'wdl': []},
                          'prod': {'top1': [], 'top3': [], 'top5': [], 'wdl': []}}}

    for tr_idx, te_idx in tscv.split(df):
        tr = df.iloc[tr_idx]; te = df.iloc[te_idx]
        form = recency_form(
            [{'home_team': r['home_team'], 'away_team': r['away_team'],
              'home_goals': r['home_goals'], 'away_goals': r['away_goals'],
              'match_date': r['_dt']} for _, r in tr.iterrows()])

        def team_attack(team):
            arr = form.get(team, [])
            if not arr:
                return None
            gfs = [g for g, ga, d in arr if g is not None]
            if not gfs:
                return None
            arr8 = arr[-8:]
            scored = sum(gf for gf, ga, d in arr8 if gf is not None)
            conc = sum(ga for gf, ga, d in arr8 if gf is not None)
            return scored, conc, len(arr8)

        for _, r in te.iterrows():
            parsed = parse_score(r['actual_score'])
            if parsed[0] is None:
                continue
            ah, ag = parsed
            odds = odds_cache[r['match_id']]
            so = score_cache[r['match_id']]

            # 默认均线回退（生产现状，无赔率路径）
            lh_def, la_def = 2.8 * 0.55, 2.8 * 0.45
            if odds:
                ph, pd_, pa = de_vig(odds['win_a'], odds['draw'], odds['win_b'])
            else:
                ph, pd_, pa = None, None, None

            def compute(total, ph_, pa_):
                if ph_ is None:
                    return build_score_grid(lh_def, la_def)
                lh, la = solve_lambda_core(ph_, pa_, total)
                return build_score_grid(lh, la)

            total = 2.6
            if so:
                it = implied_total(so)
                if it > 0.5:
                    total = min(4.5, max(2.0, it))
            ha = team_attack(r['home_team']); aa = team_attack(r['away_team'])
            if ha and aa:
                h_sc, h_cn, h_n = ha; a_sc, a_cn, a_n = aa
                base = (h_sc / max(h_n, 1) + a_cn / max(a_n, 1)) * 0.5
                base += (a_sc / max(a_n, 1) + h_cn / max(h_n, 1)) * 0.5
                total = 0.7 * total + 0.3 * min(4.5, max(1.5, base))

            # v5_de_vig（有赔率→去水锚定；无赔率→默认均线回退）
            g_vig = compute(total, ph, pa)
            if ph is not None:
                g_vig = ipf_reweight(g_vig, ph, pd_, pa)

            # v5_prod：生产锚定优先（无赔率场次亦可用）
            prod = prod_map.get(r['match_id'])
            if prod:
                ph2, pd2, pa2 = prod[0], prod[1], prod[2]
                s2 = ph2 if ph2 > 0 else (ph if ph else 0.55)
                d2 = pd2 if pd2 > 0 else (pd_ if pd_ else 0.2)
                a2 = pa2 if pa2 > 0 else (pa if pa else 0.45)
                t2 = 0.5 * 2.6 + 0.5 * min(4.5, max(2.0, total))
                lh2, la2 = solve_lambda_core(s2, a2, t2)
                g_prod = build_score_grid(lh2, la2)
                g_prod = ipf_reweight(g_prod, s2, d2, a2)
            else:
                g_prod = g_vig

            def evals(g):
                ss = sorted(g.items(), key=lambda x: x[1], reverse=True)
                top1 = ss[0][0]; top3 = [s for s, p in ss[:3]]; top5 = [s for s, p in ss[:5]]
                m_home = sum(p for (h, a), p in g.items() if h > a)
                m_away = sum(p for (h, a), p in g.items() if a > h)
                pred = 0 if m_home >= m_away else 2
                actual = 0 if ah > ag else (2 if ag > ah else 1)
                return (top1 == (ah, ag), (ah, ag) in top3, (ah, ag) in top5,
                        int(pred == (0 if actual == 0 else 2 if actual == 2 else (pred == 1))))

            e_vig = evals(g_vig); e_prod = evals(g_prod)
            stats['v5_de_vig']['top1'].append(e_vig[0]); stats['v5_de_vig']['top3'].append(e_vig[1]); stats['v5_de_vig']['top5'].append(e_vig[2])
            stats['v5_prod']['top1'].append(e_prod[0]); stats['v5_prod']['top3'].append(e_prod[1]); stats['v5_prod']['top5'].append(e_prod[2])
            if ah != ag:
                stats['v5_de_vig']['wdl'].append(e_vig[3]); stats['v5_prod']['wdl'].append(e_prod[3])
            # 锚定生效子集：记录同场 de_vig vs prod 命中，避免全量稀释
            if prod:
                st = stats['anchored']
                st['vig']['top1'].append(e_vig[0]); st['vig']['top3'].append(e_vig[1]); st['vig']['top5'].append(e_vig[2])
                st['prod']['top1'].append(e_prod[0]); st['prod']['top3'].append(e_prod[1]); st['prod']['top5'].append(e_prod[2])
                if ah != ag:
                    st['vig']['wdl'].append(e_vig[3]); st['prod']['wdl'].append(e_prod[3])

    def summarize(k):
        s = stats[k]; n = len(s['top5']); nw = len(s['wdl'])
        return {'n': n,
                'top1': round(sum(s['top1']) / n * 100, 2) if n else None,
                'top3': round(sum(s['top3']) / n * 100, 2) if n else None,
                'top5': round(sum(s['top5']) / n * 100, 2) if n else None,
                'wdl_acc': round(sum(s['wdl']) / nw * 100, 2) if nw else None,
                'wdl_n': nw}

    def summarize_anchored():
        a = stats['anchored']; out = {}
        for key, sub in (('vig', a['vig']), ('prod', a['prod'])):
            n = len(sub['top5']); nw = len(sub['wdl'])
            out[key] = {'n': n,
                        'top1': round(sum(sub['top1']) / n * 100, 2) if n else None,
                        'top3': round(sum(sub['top3']) / n * 100, 2) if n else None,
                        'top5': round(sum(sub['top5']) / n * 100, 2) if n else None,
                        'wdl_acc': round(sum(sub['wdl']) / nw * 100, 2) if nw else None,
                        'wdl_n': nw}
        return out

    res = {'v5_de_vig': summarize('v5_de_vig'), 'v5_prod': summarize('v5_prod'),
           'anchored_subset': summarize_anchored(),
           'n_production_anchored': len(stats['anchored']['prod']['top5']),
           'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    path = Path(report_path) if report_path else (REPORT_DIR / 't006_v5_production_anchor.json')
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    print("=== T-006 v5 生产 WDL 锚定对比（严格时序 5 折，全量实际比分场次） ===")
    print("v5_de_vig:", json.dumps(res['v5_de_vig'], ensure_ascii=False))
    print("v5_prod:  ", json.dumps(res['v5_prod'], ensure_ascii=False))
    for met in ['top1', 'top3', 'top5', 'wdl_acc']:
        if res['v5_prod'][met] is not None and res['v5_de_vig'][met] is not None:
            print(f"  Δ{met:>8}: {res['v5_prod'][met]-res['v5_de_vig'][met]:+.2f}pp")
    print("锚定生效子集对比:")
    a = res['anchored_subset']
    for met in ['top1', 'top3', 'top5', 'wdl_acc']:
        if a['vig'][met] is not None and a['prod'][met] is not None:
            print(f"  {met:>8}: de_vig {a['vig'][met]}%  vs  prod {a['prod'][met]}%  (Δ {a['prod'][met]-a['vig'][met]:+.2f}pp, n={a['vig']['n']})")
    print("生产锚定生效场次数:", res['n_production_anchored'])
    print("保存:", path)
    return res


def main():
    results = {'v4': [], 'v5': []}
    conn = sqlite3.connect(str(DB_PATH))
    matches = load_match_data(conn)

    # 补充 match_date（用于近期形态防泄漏）
    try:
        dmap = dict(conn.execute("SELECT match_id, match_date FROM matches").fetchall())
    except Exception:
        dmap = {}

    # 预取 odds / score_odds
    odds_cache, score_cache = {}, {}
    from t006_score_predictor import load_wdl_odds, load_score_odds
    for m in matches:
        sh = m['sh_match_id']
        odds_cache[m['match_id']] = load_wdl_odds(conn, sh)
        score_cache[m['match_id']] = load_score_odds(conn, sh)
    conn.close()

    def mdate(m):
        d = dmap.get(m['match_id'])
        if d is None:
            return pd.Timestamp('2016-08-01')
        try:
            return pd.Timestamp(d)
        except Exception:
            return pd.Timestamp('2016-08-01')

    for m in matches:
        m['_dt'] = mdate(m)

    # 严格时序切分
    matches = sorted(matches, key=lambda x: x['_dt'])
    df = pd.DataFrame({k: [m[k] for m in matches] for k in
                       ['match_id', 'home_team', 'away_team', 'actual_score', 'sh_match_id', '_dt']})
    df['home_goals'] = [m['home_goals'] for m in matches]
    df['away_goals'] = [m['away_goals'] for m in matches]

    tscv = TimeSeriesSplit(n_splits=5)
    stats = {'v4': {'top1': [], 'top3': [], 'top5': [], 'wdl': []},
             'v5': {'top1': [], 'top3': [], 'top5': [], 'wdl': []}}

    for tr_idx, te_idx in tscv.split(df):
        tr = df.iloc[tr_idx]
        te = df.iloc[te_idx]
        # 近期形态：train 全量历史（防泄漏，训练段含更早形态）
        form = recency_form(
            [{'home_team': r['home_team'], 'away_team': r['away_team'],
              'home_goals': r['home_goals'], 'away_goals': r['away_goals'],
              'match_date': r['_dt']} for _, r in tr.iterrows()])

        def team_attack(team):
            arr = form.get(team, [])
            if not arr:
                return None
            gfs = [g for g, ga, d in arr if g is not None]
            if not gfs:
                return None
            gfs = gfs[-8:]
            scored = sum(gf for gf, ga, d in arr[-8:] if gf is not None)
            conc = sum(ga for gf, ga, d in arr[-8:] if gf is not None)
            n = len(arr[-8:])
            return scored, conc, n

        for _, r in te.iterrows():
            odds = odds_cache[r['match_id']]
            if not odds:
                continue
            parsed = parse_score(r['actual_score'])
            if parsed[0] is None:
                continue
            ah, ag = parsed

            # ----- v4 -----
            lh4, la4 = 2.8 * de_vig(odds['win_a'], odds['draw'], odds['win_b'])[0], \
                       2.8 * de_vig(odds['win_a'], odds['draw'], odds['win_b'])[2]
            so4 = score_cache[r['match_id']]
            if so4:
                it = implied_total(so4)
                if it > 0.5:
                    sc = it / (lh4 + la4) if lh4 + la4 > 0 else 1.0
                    lh4, la4 = lh4 * sc, la4 * sc
            g4 = build_score_grid(lh4, la4)

            # ----- v5 -----
            ph, pd_, pa = de_vig(odds['win_a'], odds['draw'], odds['win_b'])
            total = 2.6
            if so4:
                it = implied_total(so4)
                if it > 0.5:
                    total = min(4.5, max(2.0, it))
            ha = team_attack(r['home_team']); aa = team_attack(r['away_team'])
            if ha and aa:
                h_sc, h_cn, h_n = ha; a_sc, a_cn, a_n = aa
                # 近8场比率调制总进球（轻权重避免噪声）
                base = (h_sc / max(h_n, 1) + a_cn / max(a_n, 1)) * 0.5   # 主队进球+客队失球
                base += (a_sc / max(a_n, 1) + h_cn / max(h_n, 1)) * 0.5  # 客队进球+主队失球
                total = 0.7 * total + 0.3 * min(4.5, max(1.5, base))
            lh5, la5 = solve_lambda_core(ph, pa, total)
            g5_raw = build_score_grid(lh5, la5)
            g5 = ipf_reweight(g5_raw, ph, pd_, pa)

            def scores_sorted(g):
                return sorted(g.items(), key=lambda x: x[1], reverse=True)

            def evals(g):
                ss = scores_sorted(g)
                top1 = ss[0][0]; top3 = [s for s, p in ss[:3]]; top5 = [s for s, p in ss[:5]]
                t1 = (top1 == (ah, ag))
                t3 = (ah, ag) in top3
                t5 = (ah, ag) in top5
                # WDL 方向一致
                m_home = sum(p for (h, a), p in g.items() if h > a)
                m_away = sum(p for (h, a), p in g.items() if a > h)
                pred = 0 if m_home >= m_away else 2
                actual = 0 if ah > ag else (2 if ag > ah else 1)
                return t1, t3, t5, int(pred == (0 if actual == 0 else 2 if actual == 2 else (pred == 1)))

            # WDL 一致性仅统计非平局
            e4 = evals(g4); e5 = evals(g5)
            stats['v4']['top1'].append(e4[0]); stats['v4']['top3'].append(e4[1]); stats['v4']['top5'].append(e4[2])
            stats['v5']['top1'].append(e5[0]); stats['v5']['top3'].append(e5[1]); stats['v5']['top5'].append(e5[2])
            if ah != ag:
                stats['v4']['wdl'].append(e4[3]); stats['v5']['wdl'].append(e5[3])

    def summarize(k):
        s = stats[k]
        n = len(s['top5'])
        nw = len(s['wdl'])
        return {'n': n,
                'top1': round(sum(s['top1']) / n * 100, 2),
                'top3': round(sum(s['top3']) / n * 100, 2),
                'top5': round(sum(s['top5']) / n * 100, 2),
                'wdl_acc': round(sum(s['wdl']) / nw * 100, 2) if nw else None,
                'wdl_n': nw}

    res = {'v4': summarize('v4'), 'v5': summarize('v5'), 'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    path = REPORT_DIR / 't006_v5_ablation.json'
    path.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    print("=== T-006 v5 比分消融（严格时序 5 折，有赔率子集） ===")
    print("v4:", json.dumps(res['v4'], ensure_ascii=False))
    print("v5:", json.dumps(res['v5'], ensure_ascii=False))
    for met in ['top1', 'top3', 'top5', 'wdl_acc']:
        if res['v5'][met] is not None and res['v4'][met] is not None:
            d = res['v5'][met] - res['v4'][met]
            print(f"  Δ{met:>7}: {d:+.2f}pp")
    print("保存:", path)


if __name__ == '__main__':
    main()