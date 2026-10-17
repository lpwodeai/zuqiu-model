# -*- coding: utf-8 -*-
"""
T-006 v5 B: 网格平局带后处理缩放 shadow 实验（C-20261006-012）
==============================================================
背景：A-variant（IPF 平局约束软化）FAIL —— dc_draw≈pd_，软化无效；根因是 WDL p_draw
      对市场平局定价漂移迟钝（VAL 市场平局 0.2550 → EVAL 0.2347，模型平局带恒 0.258）。
B 双臂（IPF 后对角格后处理，全程 shadow）：
  B-static  ：对角格 ×f 再归一（f<1 压平局带）。静态参数只能移水平位，跟不了窗间漂移。
  B-dynamic ：对角格重标定到 target = β·p_draw + (1-β)·mkt_wdl_draw（odds 保持比因子，
              精确命中 target）。β<1 时平局带自动跟踪市场重定价。
              β=0 语义 = 平局带完全复制 WDL 市场价（模型不再提供平局信息）。
选型：VAL（时序前 60%）|bias|≤0.5pp 中 RPS 最优，平手取参数最接近 1.0（最小改动）。
闸门：① EVAL |bias|≤0.5pp ② ΔRPS≤0.005 ③ top5≥0.46 且 top1 不下降（-0.005 容差）。
输出：reports/t006_v5_b_experiment_<ts>.md
"""
import os, sys, pickle, sqlite3
from datetime import datetime
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from t006_v5_avariant_experiment import (
    load_base_data, grid_to_pvec, modified_ipf,
    solve_lambda_core, build_league_grid, parse_score, SCORE_ODDS_ALPHA,
    LEAGUE_RHO, GRID_MAX, compute_rps, DB_PATH,
)

CACHE = os.path.join(SCRIPT_DIR, '_avariant_base.pkl')
SPLIT_RATIO = 0.6
BIAS_GATE = 0.005      # 0.5pp
RPS_GATE = 0.005
TOP1_TOL = 0.005
TOP5_GATE = 0.46

F_LIST = [1.0, 0.98, 0.96, 0.94, 0.92, 0.90, 0.88, 0.85, 0.80]          # B-static
BETA_LIST = [1.0, 0.8, 0.6, 0.4, 0.2, 0.0]                               # B-dynamic


def _pipeline_grid(r):
    """生产管线到 IPF 为止（联赛 ρ + 比分赔率融合 SCORE_ODDS_ALPHA + α=1.0 IPF）。与 f/β 无关。"""
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
    return modified_ipf(fused, r['ph'], r['pd_'], r['pa'])


def _post_scale(grid, mode, param, mkt_draw, p_draw):
    g = dict(grid)
    if mode == 'static':
        if param >= 1.0:
            return g
        for k in list(g):
            if k[0] == k[1]:
                g[k] *= param
    else:  # dynamic: 对角重标定到精确 target（odds 保持比因子）
        target = param * p_draw + (1 - param) * mkt_draw
        d = sum(v for k, v in g.items() if k[0] == k[1])
        if d <= 1e-9 or d >= 1 - 1e-9 or target <= 1e-9 or target >= 1 - 1e-9:
            return g
        factor = target * (1 - d) / (d * (1 - target))
        for k in list(g):
            if k[0] == k[1]:
                g[k] *= factor
    s = sum(g.values())
    return {k: v / s for k, v in g.items()} if s > 0 else g


def run_arm(base, grids, mode, param):
    draws_model, draws_mkt, y_list, pvecs, t1, t5, leagues = [], [], [], [], [], [], []
    for r in base:
        if r['mkt_wdl_draw'] is None or r['match_id'] not in grids:
            continue
        g = _post_scale(grids[r['match_id']], mode, param, r['mkt_wdl_draw'], r['pd_'])
        draws_model.append(sum(v for (h, a), v in g.items() if h == a and h <= GRID_MAX))
        draws_mkt.append(r['mkt_wdl_draw'])
        leagues.append(r['league'])
        pvecs.append(grid_to_pvec(g))
        y_list.append(score_to_label_public(r))
        cells = sorted(g.items(), key=lambda x: x[1], reverse=True)
        t1.append(1 if cells and cells[0][0] == (r['actual_h'], r['actual_a']) else 0)
        t5.append(1 if (r['actual_h'], r['actual_a']) in {c for c, _ in cells[:5]} else 0)
    if not y_list:
        return None
    return {
        'n': len(y_list),
        'model_draw_mean': float(np.mean(draws_model)),
        'mkt_draw_mean': float(np.mean(draws_mkt)),
        'bias': float(np.mean(draws_model) - np.mean(draws_mkt)),
        'rps': compute_rps(np.array(y_list), np.array(pvecs)),
        'top1': float(np.mean(t1)),
        'top5': float(np.mean(t5)),
        'per_league_bias': {lg: float(np.mean([draws_model[i] - draws_mkt[i]
                                               for i in range(len(leagues)) if leagues[i] == lg]))
                            for lg in sorted(set(leagues))},
    }


def score_to_label_public(r):
    from t006_v5_avariant_experiment import score_to_label
    return score_to_label(r['actual_h'], r['actual_a'])


def select(feasible_list):
    """feasible_list=[(param, m)]，已筛 |bias|≤阈值。选 RPS 最优，平手取参数最接近 1.0。"""
    if not feasible_list:
        return None
    feasible_list = sorted(feasible_list, key=lambda kv: (kv[1]['rps'], -abs(kv[0] - 1.0)))
    return feasible_list[0]


def gates(ev, ev_base):
    g1 = abs(ev['bias']) <= BIAS_GATE
    g2 = (ev['rps'] - ev_base['rps']) <= RPS_GATE
    g3 = ev['top5'] >= TOP5_GATE and ev['top1'] >= ev_base['top1'] - TOP1_TOL
    return g1, g2, g3


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

    base_sorted = sorted(base, key=lambda r: r['date'])
    split = int(len(base_sorted) * SPLIT_RATIO)
    val, evl = base_sorted[:split], base_sorted[split:]
    print(f'共 {len(base_sorted)} 场（{base_sorted[0]["date"]}~{base_sorted[-1]["date"]}），'
          f'VAL={len(val)} EVAL={len(evl)}（时序 {int(SPLIT_RATIO*100)}/{100-int(SPLIT_RATIO*100)}）')

    print('预计算每场生产管线网格（IPF 止）...')
    grids = {r['match_id']: _pipeline_grid(r) for r in base_sorted if r['mkt_wdl_draw'] is not None}
    print(f'网格 {len(grids)} 场')

    # 基线 = param=1.0（两臂同管线，post 无操作）
    val_base = run_arm(val, grids, 'static', 1.0)
    evl_base = run_arm(evl, grids, 'static', 1.0)
    print(f'\n基线 VAL : bias={val_base["bias"]*100:+.2f}pp RPS={val_base["rps"]:.4f} '
          f'top1={val_base["top1"]:.3f} top5={val_base["top5"]:.3f} n={val_base["n"]}')
    print(f'基线 EVAL: bias={evl_base["bias"]*100:+.2f}pp RPS={evl_base["rps"]:.4f} '
          f'top1={evl_base["top1"]:.3f} top5={evl_base["top5"]:.3f} n={evl_base["n"]}')

    arms = {}
    for name, plist, mode in (('B-static', F_LIST, 'static'), ('B-dynamic', BETA_LIST, 'dynamic')):
        print(f'\n=== {name} ===')
        rows_val, rows_ev = [], []
        for p in plist:
            mv = run_arm(val, grids, mode, p)
            me = run_arm(evl, grids, mode, p)
            rows_val.append((p, mv))
            rows_ev.append((p, me))
            print(f'  p={p:<4} VAL bias={mv["bias"]*100:+.2f}pp RPS={mv["rps"]:.4f} '
                  f'top1={mv["top1"]:.3f} | EVAL bias={me["bias"]*100:+.2f}pp RPS={me["rps"]:.4f} '
                  f'top1={me["top1"]:.3f} top5={me["top5"]:.3f}')
        feas = [(p, m) for p, m in rows_val if abs(m['bias']) <= BIAS_GATE]
        sel = select(feas)
        arms[name] = {'mode': mode, 'rows_val': rows_val, 'rows_ev': rows_ev, 'sel': sel}

    # 报告
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    L = []
    A = L.append
    A('# T-006 v5 B: 网格平局带后处理缩放 shadow 实验')
    A('')
    A(f'- 生成: {ts}（change_log C-20261006-012）')
    A('- 机制: IPF 后对角格后处理。B-static: 对角×f；B-dynamic: 对角重标定到 '
      'target=β·p_draw+(1-β)·mkt_wdl_draw（odds 保持比因子，精确命中）')
    A('- 背景: A-variant FAIL（dc_draw≈pd_，软化无效）；根因=WDL p_draw 对市场平局定价漂移迟钝')
    A('- 锚点: WDL 盘平局隐含概率（wdl_history 去水，noon 口径）')
    A(f'- 数据: {len(base_sorted)} 场（{base_sorted[0]["date"]}~{base_sorted[-1]["date"]}），'
      f'VAL={val_base["n"]} / EVAL={evl_base["n"]}（时序 {int(SPLIT_RATIO*100)}/{100-int(SPLIT_RATIO*100)} 切分）')
    A('')
    A('## 1. 基线（生产行为，param=1.0）')
    A('')
    A('| 窗口 | n | 模型平局带 | 市场平局 | bias(pp) | RPS | top1 | top5 |')
    A('|---|---|---|---|---|---|---|---|')
    A(f'| VAL | {val_base["n"]} | {val_base["model_draw_mean"]:.4f} | {val_base["mkt_draw_mean"]:.4f} '
      f'| {val_base["bias"]*100:+.2f} | {val_base["rps"]:.4f} | {val_base["top1"]:.3f} | {val_base["top5"]:.3f} |')
    A(f'| EVAL | {evl_base["n"]} | {evl_base["model_draw_mean"]:.4f} | {evl_base["mkt_draw_mean"]:.4f} '
      f'| {evl_base["bias"]*100:+.2f} | {evl_base["rps"]:.4f} | {evl_base["top1"]:.3f} | {evl_base["top5"]:.3f} |')
    A('')
    A('> 漂移现象（A-variant 已发现）：市场平局 VAL→EVAL 由 %.4f 降至 %.4f，模型平局带恒 ≈%.4f。'
      % (val_base['mkt_draw_mean'], evl_base['mkt_draw_mean'], evl_base['model_draw_mean']))
    A('')
    for name in ('B-static', 'B-dynamic'):
        arm = arms[name]
        A(f'## 2. {name} 全参数诊断表')
        A('')
        if name == 'B-dynamic':
            A('> β=0 语义：平局带完全复制 WDL 市场价（模型不提供平局信息，仅保留网格结构信息）。')
            A('')
        A('| 参数 | VAL bias(pp) | VAL RPS | VAL top1 | EVAL bias(pp) | EVAL RPS | EVAL ΔRPS | EVAL top1 | EVAL top5 |')
        A('|---|---|---|---|---|---|---|---|---|')
        for (pv, mv), (pe, me) in zip(arm['rows_val'], arm['rows_ev']):
            A(f'| {pv} | {mv["bias"]*100:+.2f} | {mv["rps"]:.4f} | {mv["top1"]:.3f} '
              f'| {me["bias"]*100:+.2f} | {me["rps"]:.4f} | {me["rps"]-evl_base["rps"]:+.4f} '
              f'| {me["top1"]:.3f} | {me["top5"]:.3f} |')
        A('')
        sel = arm['sel']
        if sel:
            p, mv = sel
            me = run_arm(evl, grids, arm['mode'], p)
            g1, g2, g3 = gates(me, evl_base)
            passed = g1 and g2 and g3
            A(f'## 3. {name} 选型与闸门')
            A('')
            A(f'**选型: param={p}**（VAL |bias|≤0.5pp 中 RPS 最优，平手取最接近 1.0）')
            A('')
            A('| 闸门 | 阈值 | EVAL 结果 |')
            A('|---|---|---|')
            A(f'| ① bias vs WDL 市场 | ≤0.5pp | {me["bias"]*100:+.2f}pp → {"PASS" if g1 else "FAIL"} |')
            A(f'| ② ΔRPS | ≤0.005 | {me["rps"]-evl_base["rps"]:+.4f} → {"PASS" if g2 else "FAIL"} |')
            A(f'| ③ top5≥0.46 且 top1 不下降 | — | top5={me["top5"]:.3f}, top1={me["top1"]:.3f}'
              f'(基线 {evl_base["top1"]:.3f}) → {"PASS" if g3 else "FAIL"} |')
            A('')
            A(f'**{name} 综合: {"PASS（可接线）" if passed else "FAIL"}**')
            A('')
            A(f'### 选型参数 EVAL 分联赛偏差(pp)')
            A('')
            A('| 联赛 | bias(pp) |')
            A('|---|---|')
            for lg, b in me['per_league_bias'].items():
                A(f'| {lg} | {b*100:+.1f} |')
        else:
            A(f'## 3. {name} 选型与闸门')
            A('')
            A('VAL 无 |bias|≤0.5pp 可行解 → FAIL')
        A('')
    A('## 4. 结论要点')
    A('')
    A('- B-static 静态参数只能移水平位：VAL 选出的 f 对 EVAL 窗间市场重定价（0.2550→0.2347）无跟踪能力，'
      '预期闸门① FAIL 或临界。')
    A('- B-dynamic β<1 通过赛前可得的 WDL 市场平局自动跟踪重定价，是唯一能对冲漂移的机制；'
      '代价是 β→0 时平局带完全市场复制（模型平局信息归零）。')
    A('- 最终裁决以本报告闸门结果为准；接线需用户确认。')
    A('')

    rpt = os.path.join(os.path.dirname(SCRIPT_DIR), 'reports', f't006_v5_b_experiment_{ts}.md')
    with open(rpt, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'\n报告: {rpt}')
    conn.close()


if __name__ == '__main__':
    main()
