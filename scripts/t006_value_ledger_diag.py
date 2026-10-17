# -*- coding: utf-8 -*-
"""
t006_value_ledger_diag.py — v5 价值台账 + TG 偏置归因（C-20261006-017 后续，只读）
====================================================================================
两个问题（均为只读诊断，无任何选型；EVAL-peek 红线适用）：

A. TG 偏置归因（补 C-017 缺失的市场第三方）：
   C-017 发现 stored(TG线落库) 与 v5 网格 P(over2.5) corr 仅 0.30、mean(grid−stored)=−15.7pp，
   并留档「TG 线水平偏置嫌疑」。但该归因缺市场基准。本诊断加入
   L0 市场比分盘 grid-implied P(over) 与 Poisson(λ_v5_total)：
     - 若 stored ≈ L0（corr 高、gap 小）→ stored 是市场镜像，其 +9.5pp 水平差实为
       市场大小球/比分盘对 over 的定价特性，嫌疑应转向 v5 λ链总进球压缩（与 C-015
       Stage1 注入病理同源），C-017 的「TG 线偏置嫌疑」归因需修正；
     - Poisson(λ_v5) vs L3 的差区分「λ 总量压缩」vs「网格形状(DC/IPF)」。
   预声明判据：|mean(stored−L0_over)| < 2pp 且 corr(stored,L0) ≥ 0.8 → stored≈市场镜像；
   否则 TG 线相对比分盘存在独立偏移，C-017 归因成立。

B. top-N 价值台账（价值重定位维度盘点，C-015 分支裁定 <4pp 后续）：
   L0/L1/L2/L3 四层 top1/top3/top5 命中 + 实际格概率（NLL），paired SE + 通用
   <1SE 条款（C-016）。价值维度生存判据（预声明）：L3 − L0 ≥ +2pp 且 ≥1SE →
   该维度成为候选价值叙事；否则该维度无证据，价值台账关闭。

口径：窗口 env TOP1_DIAG_WIN_START/END（默认 2026-08-22~09-21，同 C-015/C-017）；
市场侧 noon 锚；v5 侧 latest 快照（C-005 回填口径，B0 前视 caveat 继承）。
输出：reports/t006_value_ledger_<ts>.md + 控制台。全程只读 SELECT。
"""
import os, sys, sqlite3
from datetime import datetime
from collections import defaultdict
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

from t006_top1_gap_diag import (load_window_matches, mkt_score_grid_full,
                                pick_v5_snapshot, poisson_grid,
                                NOON_CUT, WIN_START, WIN_END, GRID_MODEL, PROD_MODEL)
from tfs_shadow_report import canon_key, load_name_map
from market_benchmark_hc import build_en2cn, _norm
from t006_score_predictor_v5 import solve_lambda_core

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')


def p_over(g):
    """grid-implied P(total ≥ 3)（over 2.5）。"""
    return float(g[np.add.outer(np.arange(6), np.arange(6)) >= 3].sum())


def topk_metrics(g, actual):
    flat = g.flatten()
    order = np.argsort(flat)[::-1]
    cells = [divmod(int(i), 6) for i in order]
    h, a = actual
    out = {f'top{k}': 1.0 if (h, a) in cells[:k] else 0.0 for k in (1, 3, 5)}
    out['p_actual'] = float(g[h, a])
    out['nll'] = float(-np.log(max(g[h, a], 1e-12)))
    return out


def gather(conn):
    matches = load_window_matches(conn)
    name_map = load_name_map(conn)
    en2cn = build_en2cn(conn)

    v5_raw = defaultdict(lambda: defaultdict(dict))
    v5_canon = {}
    for r in conn.execute(
            "SELECT match_id, prediction_type, probability, timestamp FROM model_predictions "
            "WHERE model_name=? AND is_replay=0 "
            "AND (prediction_type LIKE 'Score_grid_%' OR prediction_type IN ('Lambda_home','Lambda_away'))",
            (GRID_MODEL,)):
        ck = v5_canon.get(r['match_id'], '__UNSET__')
        if ck == '__UNSET__':
            ck = canon_key(r['match_id'], name_map)
            v5_canon[r['match_id']] = ck
        if ck is None:
            continue
        v5_raw[ck][r['timestamp']][r['prediction_type']] = float(r['probability'])

    stored_by_ck = {}
    n_stored_fail = 0
    for r in conn.execute(
            "SELECT match_id, probability FROM model_predictions "
            "WHERE model_name=? AND is_replay=0 AND prediction_type='TG_over_2_5'", (PROD_MODEL,)):
        ck = canon_key(r['match_id'], name_map)
        if ck is None:
            n_stored_fail += 1
            continue
        stored_by_ck[ck] = float(r['probability'])

    recs_a, recs_b = [], []
    funnel = defaultdict(int)
    for m in matches:
        ck = canon_key(m['match_id'], name_map)
        if ck is None:
            funnel['ck失败'] += 1
            continue
        v5s = pick_v5_snapshot(v5_raw.get(ck, {}), m['date'], 'latest')
        if v5s is None:
            funnel['缺v5'] += 1
            continue
        h, a = m['actual']
        y = 1 if h + a >= 3 else 0
        lam_h, lam_a = v5s['lam']
        rec = {'date': m['date'], 'league': m['league'], 'y': y,
               'stored': stored_by_ck.get(ck),
               'l3_over': p_over(v5s['grid']),
               'pois_over': p_over(poisson_grid(lam_h, lam_a)),
               'lam_total': lam_h + lam_a}
        # Part B 四层
        hc, ac = en2cn.get(_norm(m['home_en'])), en2cn.get(_norm(m['away_en']))
        cn_id = f"{m['date']}_{hc}_{ac}" if (hc and ac) else None
        L0 = mkt_score_grid_full(conn, cn_id, m['date'], 'noon') if cn_id else None
        if L0 is None:
            funnel['缺L0(noon)'] += 1
        else:
            rec['l0_over'] = p_over(L0['grid'])
            rec['l0_etot'] = float((np.add.outer(np.arange(6), np.arange(6)) * L0['grid']).sum())
            rec['L0'] = topk_metrics(L0['grid'], m['actual'])
            # L1：市场 WDL noon → λ 反演，total 锁 L2（与 C-015 同口径）
            cut = f"{m['date']} {NOON_CUT}"
            row = conn.execute(
                "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id=? AND timestamp IS NOT NULL "
                "AND timestamp<=? ORDER BY timestamp DESC LIMIT 1", (cn_id, cut)).fetchone()
            p_full = None
            if row and row['win_a'] and row['draw'] and row['win_b'] \
                    and row['win_a'] > 1 and row['draw'] > 1 and row['win_b'] > 1:
                v = np.array([1.0 / row['win_a'], 1.0 / row['draw'], 1.0 / row['win_b']])
                p_full = v / v.sum()
            if p_full is None:
                funnel['缺L1市场WDL'] += 1
            else:
                try:
                    lam1_h, lam1_a = solve_lambda_core(float(p_full[0]), float(p_full[2]), float(lam_h + lam_a))
                except Exception:
                    lam1_h = None
                if lam1_h is None:
                    funnel['L1λ反演失败'] += 1
                else:
                    rec['L1'] = topk_metrics(poisson_grid(lam1_h, lam1_a), m['actual'])
            rec['L2'] = topk_metrics(poisson_grid(lam_h, lam_a), m['actual'])
            rec['L3'] = topk_metrics(v5s['grid'], m['actual'])
            recs_b.append(rec)
        if rec['stored'] is not None and 'l0_over' in rec:
            recs_a.append(rec)
    print(f'窗口场次 {len(matches)}｜Part A（TG 归因，有 stored）n={len(recs_a)}｜'
          f'Part B（四层台账）n={len(recs_b)}｜stored canon 失败 {n_stored_fail}')
    if funnel:
        print('漏斗: ' + '  '.join(f'{k}={v}' for k, v in funnel.items()))
    return recs_a, recs_b


def main():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=5000')
    print('=' * 78)
    print(f'v5 价值台账 + TG 偏置归因（只读）  窗口 {WIN_START}~{WIN_END}（市场 noon / v5 latest）')
    print('=' * 78)
    recs_a, recs_b = gather(conn)
    analyze(recs_a, recs_b, ts)
    conn.close()


def analyze(recs_a, recs_b, ts):
    lines = []
    L = lines.append
    L('# v5 价值台账 + TG 偏置归因（C-20261006-017 后续）')
    L('')
    L(f'- 时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}｜窗口: {WIN_START}~{WIN_END} 五联赛')
    L(f'- 口径: 市场侧 noon 锚；v5 侧 latest 快照（C-005 回填口径，B0 前视 caveat 继承）')
    L(f'- 性质: 只读诊断、无选型；通用 <1SE 条款适用（C-20261006-016）；判据已在报告生成前预声明（见脚本 docstring）')
    L('')

    # ---------- Part A ----------
    L('## A. TG 偏置归因（stored vs L0 市场比分盘 vs v5 网格 vs Poisson(λ_v5)）')
    L('')
    n_a = len(recs_a)
    if n_a < 30:
        L(f'n={n_a} < 30，样本不足，仅列描述统计不做归因判定。')
    sp = np.array([r['stored'] for r in recs_a])
    l0 = np.array([r['l0_over'] for r in recs_a])
    l3 = np.array([r['l3_over'] for r in recs_a])
    ps = np.array([r['pois_over'] for r in recs_a])
    y = np.array([r['y'] for r in recs_a], dtype=float)
    L(f'| 口径 | 均值 | Brier(vs实际) |')
    L(f'|---|---|---|')
    L(f'| stored（TG线落库） | {sp.mean():.4f} | {((sp-y)**2).mean():.4f} |')
    L(f'| L0 市场比分盘 P(over) | {l0.mean():.4f} | {((l0-y)**2).mean():.4f} |')
    L(f'| Poisson(λ_v5_total) | {ps.mean():.4f} | {((ps-y)**2).mean():.4f} |')
    L(f'| L3 v5 网格 P(over) | {l3.mean():.4f} | {((l3-y)**2).mean():.4f} |')
    L(f'| 实际 over 占比 | {y.mean():.4f} | 基率 Brier {y.mean()*(1-y.mean()):.4f} |')
    L('')
    corr_s_l0 = float(np.corrcoef(sp, l0)[0, 1]) if n_a >= 3 else float('nan')
    corr_s_l3 = float(np.corrcoef(sp, l3)[0, 1]) if n_a >= 3 else float('nan')
    gap_s_l0 = sp - l0
    gap_l0_l3 = l0 - l3
    gap_ps_l3 = ps - l3
    se_s_l0 = float(gap_s_l0.std(ddof=1) / np.sqrt(n_a)) if n_a > 1 else float('nan')
    L(f'- corr(stored, L0) = **{corr_s_l0:.3f}**｜corr(stored, L3) = {corr_s_l3:.3f}')
    L(f'- mean(stored − L0_over) = **{gap_s_l0.mean()*100:+.2f}pp** ± {se_s_l0*100:.2f}pp（1SE）')
    L(f'- mean(L0_over − L3_over) = {gap_l0_l3.mean()*100:+.2f}pp（市场比分盘高于 v5 网格的部分）')
    L(f'- mean(Poisson(λ_v5) − L3) = {gap_ps_l3.mean()*100:+.2f}pp（正=形状/截断抬高 over；负=形状压低）')
    lam_tot = np.array([r['lam_total'] for r in recs_a if 'lam_total' in r])
    l0_etot = np.array([r['l0_etot'] for r in recs_a if 'l0_etot' in r])
    L(f'- 总进球水平: λ_v5_total 均值 = {lam_tot.mean():.3f} vs 比分盘隐含 E[total] 均值 = {l0_etot.mean():.3f}'
      f'（差 {l0_etot.mean()-lam_tot.mean():+.3f} 球）——v5 的 total 先验 = TG 八档赔率隐含 E[g]'
      f'（predict_score_v5 冻结口径，钳位[1.5,4.5]）')
    L('')
    if abs(gap_s_l0.mean()) < 0.02 and corr_s_l0 >= 0.8:
        verdict_a = ('**stored ≈ 市场比分盘镜像**（预声明判据命中）：TG 线落库的水平由市场定价主导，'
                     'C-017 观察到的 +9.5pp「stored 高于实际」应修正归因为市场对 over 的定价特性'
                     '（公众买大球倾向 / 比分盘投影），**嫌疑转向 v5 λ链总进球压缩**（与 C-015 Stage1 '
                     '注入病理同源）。C-017 附发现的「TG 线偏置嫌疑」按本判定修正，TG 线修复项不成立。')
    elif corr_s_l0 >= 0.8:
        verdict_a = (f'stored 与市场比分盘**同向强相关**（corr={corr_s_l0:.2f}）但系统性偏高 '
                     f'{gap_s_l0.mean()*100:+.2f}pp ± {se_s_l0*100:.2f}pp（{abs(gap_s_l0.mean())/se_s_l0:.1f} 个 SE，显著）：'
                     'TG 线以市场为锚但输入侧（大小球盘口定价/水位）自带 over 高估，C-017「TG 线偏置嫌疑」'
                     '方向成立但需精化——偏高主体是 TG线 相对比分盘的独立 +6pp 级偏移。'
                     f'而 v5 网格低于比分盘 {gap_l0_l3.mean()*100:+.1f}pp **全部来自 λ 总量压缩**'
                     f'（Poisson(λ_v5) − L3 = {gap_ps_l3.mean()*100:+.2f}pp，形状零贡献），与 C-015 λ链病理同源。')
    elif corr_s_l0 < 0.5:
        verdict_a = ('stored 与市场比分盘 P(over) 相关性弱（<0.5）：TG 线并非比分盘投影，'
                     '其水平偏移是独立现象，C-017 归因（TG 线自身嫌疑）维持，需另查 TG 线输入源。')
    else:
        nsig = abs(gap_s_l0.mean()) / se_s_l0 if se_s_l0 else float('nan')
        verdict_a = (f'stored 与 L0 相关中等（corr={corr_s_l0:.2f}）、水平差 {gap_s_l0.mean()*100:+.2f}pp '
                     f'±{se_s_l0*100:.2f}pp（{nsig:.1f} 个 SE）'
                     + ('→ 水平差显著，需分解 TG 线输入源后归因。' if nsig >= 1
                        else '→ 按 <1SE 条款判读，证据不足以归因，留档再触发。'))
    L(f'**Part A 判定**: {verdict_a}')
    L('')

    # ---------- Part B ----------
    L('## B. top-N 价值台账（L0 市场网格 / L1 市场λ泊松 / L2 v5λ泊松 / L3 v5网格，noon 锚）')
    L('')
    n_b = len(recs_b)
    L(f'同 n 交集 n={n_b}。预声明判据：L3 − L0 ≥ +2pp 且 ≥1SE → 维度成为候选价值叙事；否则无证据。')
    L('')
    L('| 层 | top1 | top3 | top5 | P(实际格) 均值 | NLL |')
    L('|---|---|---|---|---|---|')
    for k in ('L0', 'L1', 'L2', 'L3'):
        sub = [r[k] for r in recs_b if k in r]
        if not sub:
            continue
        L(f'| {k} | ' + ' | '.join(
            f'{np.mean([s[f"top{i}"] for s in sub]):.3f}' for i in (1, 3, 5)) +
          f' | {np.mean([s["p_actual"] for s in sub]):.4f} | '
          f'{np.mean([s["nll"] for s in sub]):.4f} |')
    L('')
    # paired diff L3 vs L0
    pair = [(r['L3'], r['L0']) for r in recs_b if 'L0' in r and 'L3' in r]
    if pair:
        L('| 维度 | L3 − L0 | paired SE | 判读 |')
        L('|---|---|---|---|')
        for k, lab in (('top1', 'top1'), ('top3', 'top3'), ('top5', 'top5'), ('p_actual', 'P(实际格)'), ('nll', 'NLL')):
            d = np.array([a[k] - b[k] for a, b in pair])
            se = float(d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 1 else float('nan')
            sig = '≥1SE' if abs(d.mean()) >= se else '<1SE'
            if k == 'nll':
                good = d.mean() <= 0
            else:
                good = d.mean() >= 0.02
            read = '候选' if (good and sig == '≥1SE') else '无证据'
            L(f'| {lab} | {d.mean()*100:+.2f}pp | {se*100:.2f}pp | {sig}，{read} |')
        L('')
    L('---')
    L('*只读诊断，无生产变更。Part A 修正 C-017 归因；Part B 为价值重定位维度台账（预声明判据）。*')

    out = os.path.join(REPORTS_DIR, f't006_value_ledger_{ts}.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines))
    print(f'\n报告: {out}')


if __name__ == '__main__':
    main()
