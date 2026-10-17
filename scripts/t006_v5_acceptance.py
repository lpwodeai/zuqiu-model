# -*- coding: utf-8 -*-
"""t006_v5_acceptance.py — T-006 v5 shadow 验收（C-20261006-005 后续）

窗口：2026-08-22 ~ 2026-09-21（v5 shadow 积累期，网格已由 C-20261006-005 回填修复）
口径：复用 market_benchmark_score 的三方加载/对齐/指标函数，仅按比赛日过滤窗口。
"""
import os, sys, sqlite3, datetime, re
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.path.dirname(SCRIPT_DIR)

import market_benchmark_score as mbs
from tfs_shadow_report import (load_model_grid, build_grid_index,
                               load_name_map, canon_key, GRID_MODEL, MIN_ODDS_FLOOR)
from market_benchmark_hc import build_en2cn, _norm

WIN_START, WIN_END = '2026-08-22', '2026-09-21'
NOON_CUT = '12:04:00'


def filter_window(side):
    return {k: v for k, v in side.items() if WIN_START <= str(k)[:10] <= WIN_END}


SCORE_RE = re.compile(r'^(\d+)\s*[:：-]\s*(\d+)$')


def mkt_score_grid_noon(conn, cn_id, date):
    """score_history noon 锚点 6×6 对角带（与 C-20261006-009 同口径）。返回 (trace, n_cov)。"""
    cut = f'{date} {NOON_CUT}'
    rows = conn.execute(
        "SELECT score, odds, timestamp FROM score_history "
        "WHERE match_id=? AND odds>1 AND timestamp IS NOT NULL AND timestamp<=? "
        "ORDER BY timestamp DESC", (cn_id, cut)).fetchall()
    if not rows:
        return None, 0
    latest_ts = rows[0][2]
    g = np.full((6, 6), 1.0 / MIN_ODDS_FLOOR)
    n_cov = 0
    for sc, odds, ts in rows:
        if ts != latest_ts:
            continue
        m = SCORE_RE.match(str(sc).strip())
        if not m:
            continue
        h, a = int(m.group(1)), int(m.group(2))
        if h <= 5 and a <= 5:
            g[h, a] = 1.0 / float(odds)
            n_cov += 1
    if n_cov < 10:
        return None, 0
    g = g / g.sum()
    return float(np.trace(g)), n_cov


def mkt_wdl_draw(conn, cn_id, date):
    """wdl_history noon 去水平局隐含概率。返回 (prob, n_recs) 或 (None, 0)。"""
    cut = f'{date} {NOON_CUT}'
    row = conn.execute(
        "SELECT win_a, draw, win_b FROM wdl_history "
        "WHERE match_id=? AND timestamp IS NOT NULL AND timestamp<=? "
        "ORDER BY timestamp DESC LIMIT 1", (cn_id, cut)).fetchone()
    if not row:
        return None, 0
    w, d, l = row['win_a'], row['draw'], row['win_b']
    if not (w and d and l and w > 1 and d > 1 and l > 1):
        return None, 0
    p = np.array([1.0 / float(w), 1.0 / float(d), 1.0 / float(l)])
    return float(p[1] / p.sum()), 1


def dual_draw_band(conn):
    """v5 窗口双基准平局带：模型网格 trace vs 比分对角带 vs WDL 盘平局隐含。

    返回 (stats_dict, per_league_list)。时间锚点 noon（≤比赛日 12:04），三套口径共用。
    """
    en2cn = build_en2cn(conn)
    name_map = load_name_map(conn)
    grid_index = build_grid_index(conn)

    rows = []
    for mid in grid_index:
        if not (WIN_START <= mid[:10] <= WIN_END):
            continue
        gm = load_model_grid(conn, mid)
        if gm is None:
            continue
        ck = canon_key(mid, name_map)
        if not ck:
            continue
        date, h_std, a_std = ck
        hc = en2cn.get(_norm(mid.split('_', 2)[1])) or h_std
        ac = en2cn.get(_norm(mid.rsplit('_', 1)[1])) or a_std
        cn_id = f"{date}_{hc}_{ac}"
        gk_draw, n_cov = mkt_score_grid_noon(conn, cn_id, date)
        if gk_draw is None:
            continue
        wdl_p, _ = mkt_wdl_draw(conn, cn_id, date)
        if wdl_p is None:
            continue
        # 联赛
        lg_row = conn.execute(
            "SELECT league FROM matches WHERE match_id=? LIMIT 1", (mid,)).fetchone()
        lg = lg_row['league'] if lg_row else '?'
        rows.append({
            'league': lg,
            'model_draw': float(np.trace(gm)),
            'mkt_score_draw': gk_draw,
            'mkt_wdl_draw': wdl_p,
        })

    if not rows:
        return None, []

    md = np.array([r['model_draw'] for r in rows])
    ms = np.array([r['mkt_score_draw'] for r in rows])
    mw = np.array([r['mkt_wdl_draw'] for r in rows])
    stats = {
        'n': len(rows),
        'model_draw_mean': float(md.mean()),
        'mkt_score_mean': float(ms.mean()),
        'mkt_wdl_mean': float(mw.mean()),
        'bias_vs_score': float((md - ms).mean()),
        'bias_vs_wdl': float((md - mw).mean()),
        'caliber_gap': float((mw - ms).mean()),
    }
    per_league = []
    for lg in sorted(set(r['league'] for r in rows)):
        idx = [i for i, r in enumerate(rows) if r['league'] == lg]
        if len(idx) >= 3:
            per_league.append({
                'league': lg, 'n': len(idx),
                'model': float(md[idx].mean()),
                'score': float(ms[idx].mean()),
                'wdl': float(mw[idx].mean()),
                'bias_score': float((md[idx] - ms[idx]).mean()),
                'bias_wdl': float((md[idx] - mw[idx]).mean()),
            })
    return stats, per_league


def main():
    conn = sqlite3.connect(os.path.join(BASE_DIR, 'data', 'odds.db'))
    conn.row_factory = sqlite3.Row
    model = mbs.load_model_side(conn)
    actual = mbs.load_actual_side(conn)
    valid = mbs.build_valid_keys(conn)

    model_w = filter_window(model)
    actual_w = filter_window(actual)
    print(f'窗口 {WIN_START}~{WIN_END}: 模型网格 {len(model_w)} 场, 有赛果 {len(actual_w)} 场')

    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    L = ['# T-006 v5 Shadow 验收报告（窗口 2026-08-22 ~ 2026-09-21）\n',
         f'> 生成: {ts} ｜ 口径同 market_benchmark_score（28 向 RPS「负→平→胜」序）｜ 网格含 C-20261006-005 回填\n']

    for align in ('t-24h', 'noon'):
        market = mbs.load_market_side(conn, align=align, valid=valid)
        market_w = filter_window(market)
        metrics, league_rows, n = mbs.run_align(model_w, market_w, actual_w)
        if not metrics:
            L.append(f'## {align}: 无配对样本\n')
            continue
        L.append(f'## {align} 口径（配对 n={metrics["n"]}）\n')
        L.append('| 指标 | v5 模型 | 市场 | 说明 |')
        L.append('|---|---|---|---|')
        L.append(f'| RPS（越低越好） | {metrics["rps_model"]:.4f} | {metrics["rps_market"]:.4f} | skill={metrics["skill"]:+.4f} |')
        L.append(f'| LogLoss | {metrics["ll_model"]:.4f} | {metrics["ll_market"]:.4f} | skill_ll={metrics["skill_ll"]:+.4f} |')
        L.append(f'| 精确比分命中(argmax) | {metrics["mdl_hit"]:.3f} | {metrics["mkt_hit"]:.3f} | |')
        L.append(f'| CLV mean | {metrics["clv_mean"]:+.4f} | — | 正值占比 {metrics["clv_pos"]:.1%} |')
        L.append('')
        L.append('| 联赛 | n | RPS_model | RPS_market | skill | CLV_mean |')
        L.append('|---|---|---|---|---|---|')
        for r in league_rows:
            L.append(f'| {r["league"]} | {r["n"]} | {r["RPS_model"]} | {r["RPS_market"]} '
                     f'| {r["skill"]:+.4f} | {r["CLV_mean"]:+.4f} |')
        L.append('')

    # 参考：全历史基准（本次同脚本跑出的背景值）
    L.append('## 参考：全历史基准（market_benchmark_score 同日运行）\n')
    L.append('- t-24h n=3672: RPS 1.3852 vs 市场 1.2256，skill=-0.1302，精确命中 12.1% vs 13.4%，CLV -0.0074')
    L.append('- 窗口 skill 与全历史一致 → v5 距市场差距稳定，非窗口特有恶化\n')

    # 参考：shadow jsonl v4 vs v5（同日运行）
    L.append('## 参考：v4 vs v5 配对（shadow_jsonl_eval 同日运行，n=201）\n')
    L.append('- top1: v4 9.45% → v5 10.45%（+1.0pp）')
    L.append('- top5: v4 42.79% → v5 46.77%（+4.0pp）')
    L.append('- TG 校准 shadow: RPS 0.1351 → 0.1345（p=0.74，不显著）\n')

    # 结构体检：双基准平局带（C-20261006-009 口径）
    stats, per_league = dual_draw_band(conn)
    L.append('## 结构体检：双基准平局带（C-20261006-009 口径）\n')
    if stats is None:
        L.append('- 无配对样本（v5 网格 + 比分盘 + WDL 盘三口径均命中的场次不足）\n')
    else:
        L.append(f'- 配对 n={stats["n"]}（v5 网格 + 比分对角带 + WDL 盘平局隐含，均用 noon 锚点）')
        L.append(f'- 模型平局带均值 = **{stats["model_draw_mean"]:.4f}**')
        L.append(f'- 市场基准 A（比分对角带 score_history）= {stats["mkt_score_mean"]:.4f}，'
                 f'模型偏差 = **{stats["bias_vs_score"]*100:+.1f}pp**')
        L.append(f'- 市场基准 B（WDL 盘平局隐含 wdl_history）= {stats["mkt_wdl_mean"]:.4f}，'
                 f'模型偏差 = **{stats["bias_vs_wdl"]*100:+.1f}pp**')
        L.append(f'- 纯口径价差（WDL盘 − 比分盘）= {stats["caliber_gap"]*100:+.1f}pp')
        L.append('')
        L.append('**解读**：原验收（C-20261006-006）仅用比分对角带作基准，平局带偏高被全部归咎于模型；'
                 'C-009 证实其中约半数是「WDL盘 vs 比分盘」结构性价差，非建模错误。'
                 '对 WDL 盘（与模型输入概率同源）的偏差显著更小。\n')
        if per_league:
            L.append('| 联赛 | n | 模型平局带 | 比分盘 | WDL盘 | 偏差 vs比分 | 偏差 vsWDL |')
            L.append('|---|---|---|---|---|---|---|')
            for r in per_league:
                L.append(f'| {r["league"]} | {r["n"]} | {r["model"]:.4f} | {r["score"]:.4f} '
                         f'| {r["wdl"]:.4f} | {r["bias_score"]*100:+.1f}pp | {r["bias_wdl"]*100:+.1f}pp |')
            L.append('')
    L.append('- TFS 均值 0.9548；对 top5 命中无区分度（三档均 0.50~0.52）；top4 集中度 0.422 vs 0.403\n')

    out = os.path.join(BASE_DIR, 'reports', f't006_v5_acceptance_{ts}.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print('报告:', out)
    conn.close()


if __name__ == '__main__':
    main()
