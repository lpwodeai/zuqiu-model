#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C-20261016-122 D3 接线回放 — 在 market_benchmark_hc 基准窗口上 monkeypatch 回放 T-005 v3 D3。

背景：market_benchmark_hc 的模型侧读 model_predictions 存量行——基准窗口（8/22~9/21）
存量仍是 D2 旧值，直接复跑 benchmark 测的不是 D3。本脚本按 C-002/C-003 回放同款思路：
不落库、不读存量概率，monkeypatch 掉特征构造与模型推理两个方法，让
HandicapPredictor.predict() 在基准同口径锚行（handicap_history t-24h / noon 快照）上
走真实 D3 主路径分支（市场三向去水），再与市场侧逐位对拍。

【重要】skill≈0 是构造性必然（D3 = 市场复制，模型侧数学 ≡ 市场侧数学）。
本回放的价值 = 接线证明（生产 predict() 真的会走 D3 分支、真的吃 handicap_history 锚行），
不是模型泛化能力证明。禁止把本报告读作「模型跑平市场」。

口径（与 market_benchmark_hc 完全一致）：
  - 窗口/标签：load_model_side（存量 HC 行存在 + matches.actual_handicap 官方文本）
  - 市场锚：handicap_history，t-24h / noon 双口径，行内 raw 赔率同源
  - 桥接：英 match_id → team_mapping/odds500_match → 中文 cn_id
  - 标签序 0=下盘赢 1=走水 2=上盘赢；概率列序 [下盘, 走水, 上盘]

验收锚点（全部应为「构造性通过」）：
  A1 每场 max|模型三向 − 市场三向| ≤ 1e-12（同数学，仅浮点次序差）
  A2 skill = 0（RPS_model == RPS_market）、argmax 命中逐位一致、CLV=0
  A3 每场 method = 'T-005 v3 D3 (market_replication, model_fallback)'、draw_source='market_hcp'
  A4 走水值连续分布，无 D2 isotonic 五台阶指纹（0.1%/13.5%/27.3%/29.5%/38.5%）
  A5 对照：D3 回放走水 vs D2 存量走水的逐场位移（台阶→连续的直接证据）

输出：reports/replay_d3_benchmark_<ts>.md + 控制台。
"""

import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import sqlite3

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')

import market_benchmark_hc as mbh
import prediction_core as pc

# D2 isotonic 五台阶指纹（旧值，来自 19:28 版落库/历史报告；用于 A4 消失证明）
OLD_STEPS = [0.001, 0.135, 0.273, 0.295, 0.385]
STEP_TOL = 0.002


def load_anchor_odds(conn, align):
    """与 mbh.load_market_side 同口径的锚行选取，但返回 raw 赔率（供 predict() 消费）。"""
    rows = conn.execute("""
        SELECT match_id, timestamp, hcp_win, hcp_draw, hcp_lose
        FROM handicap_history
        WHERE match_id IS NOT NULL AND timestamp IS NOT NULL
    """).fetchall()
    by_match = {}
    for r in rows:
        mid = str(r['match_id'])
        ts = str(r['timestamp'])
        date = mid.split('_')[0]
        if align == 'noon':
            if ts > f"{date} 12:04:00":
                continue
        else:  # t-24h
            if ts[:10] >= date:
                continue
        by_match.setdefault(mid, {})[ts] = r
    anchors = {}
    for mid, tsmap in by_match.items():
        if not tsmap:
            continue
        latest_ts = max(tsmap.keys())
        r = tsmap[latest_ts]
        try:
            win = float(r['hcp_win']); draw = float(r['hcp_draw']); lose = float(r['hcp_lose'])
        except (TypeError, ValueError):
            continue
        if not (win > 1 and draw > 1 and lose > 1):
            continue
        anchors[mid] = {'win': win, 'draw': draw, 'lose': lose, 'ts': latest_ts}
    return anchors


def market_threeway(a):
    """mbh.load_market_side 同数学：[P(下盘), P(走水), P(上盘)]。"""
    p_raw = np.array([1.0 / a['lose'], 1.0 / a['draw'], 1.0 / a['win']])
    return p_raw / p_raw.sum()


def make_replay_predictor():
    """monkeypatch 桩：draw_model 非 None 进 try 块；特征/推理替换为锚行快照桩。"""
    predictor = pc.HandicapPredictor({'draw_model': object(), 'dir_model': None})
    predictor._build_t005v3_feature_vector = lambda match, odds_data, is_mock=False: (
        None, 'replay: benchmark anchor snapshot (monkeypatch)')
    predictor._predict_t005v3_handicap = lambda X: {
        'probabilities': {'上盘赢': 0.0, '走水': 0.0, '下盘赢': 0.0},
        'prediction': 'REPLAY_STUB', 'confidence': 0.0,
        'raw_p_draw': 0.0, 'calib_p_draw': None,
    }
    return predictor


def replay_one(predictor, rec, anchor):
    """对单场回放 D3 predict()，返回 (model_threeway, method, draw_source, prediction)。"""
    match = {
        'home_team': rec['home_en'], 'away_team': rec['away_en'],
        'home_team_cn': rec['home_cn'], 'away_team_cn': rec['away_cn'],
    }
    h = {'win': anchor['win'], 'draw': anchor['draw'], 'lose': anchor['lose']}
    odds_data = {
        'handicap_odds': {'line': -1.0, 'close': h, 'open': h, 'records': [h]},
        'wdl_odds': {'records': [], 'open': {}, 'close': {}},
        'score_odds': {'records': []},
        'tg_odds': {'records': []},
    }
    res = predictor.predict(match, odds_data)
    # D3 返回体：home_win_prob/draw_prob/away_win_prob = 上盘/走水/下盘
    m = np.array([res['away_win_prob'], res['draw_prob'], res['home_win_prob']], dtype=float)
    return m, res.get('method', ''), res.get('draw_source', ''), res.get('prediction', '')


def run_align(conn, en2cn, model, align, predictor, log):
    anchors = load_anchor_odds(conn, align)
    paired, miss_anchor, miss_cn = [], 0, 0
    for rec in model:
        home_cn = en2cn.get(mbh._norm(rec['home_en']))
        away_cn = en2cn.get(mbh._norm(rec['away_en']))
        if not home_cn or not away_cn:
            miss_cn += 1
            continue
        rec = {**rec, 'home_cn': home_cn, 'away_cn': away_cn}
        cn_id = f"{rec['match_date']}_{home_cn}_{away_cn}"
        a = anchors.get(cn_id)
        if a is None:
            miss_anchor += 1
            continue
        m, method, draw_source, pred_label = replay_one(predictor, rec, a)
        k = market_threeway(a)
        paired.append({
            **rec, 'cn_id': cn_id, 'anchor_ts': a['ts'], 'anchor_hcp': f"{a['win']}/{a['draw']}/{a['lose']}",
            'm_lower': m[0], 'm_draw': m[1], 'm_upper': m[2],
            'k_lower': k[0], 'k_draw': k[1], 'k_upper': k[2],
            'max_diff': float(np.max(np.abs(m - k))),
            'method': method, 'draw_source': draw_source, 'pred': pred_label,
            'd2_lower': rec['m_lower'], 'd2_draw': rec['m_draw'], 'd2_upper': rec['m_upper'],
        })

    if not paired:
        return None, [], 0, miss_cn, miss_anchor

    df = pd.DataFrame(paired)
    y = df['actual'].to_numpy(int)
    m_proba = df[['m_lower', 'm_draw', 'm_upper']].to_numpy(float)
    k_proba = df[['k_lower', 'k_draw', 'k_upper']].to_numpy(float)
    # 同窗口 D2 存量对照（窗口内存量 HC 行未被今日 --write 触碰，仍是 D2 值）
    d2_proba = df[['d2_lower', 'd2_draw', 'd2_upper']].to_numpy(float)

    rps_model = mbh.compute_rps(y, m_proba)
    rps_market = mbh.compute_rps(y, k_proba)
    skill = 1 - rps_model / rps_market if rps_market > 0 else float('nan')
    rps_d2 = mbh.compute_rps(y, d2_proba)
    skill_d2 = 1 - rps_d2 / rps_market if rps_market > 0 else float('nan')
    mdl_hit = float((m_proba.argmax(axis=1) == y).mean())
    mkt_hit = float((k_proba.argmax(axis=1) == y).mean())
    d2_hit = float((d2_proba.argmax(axis=1) == y).mean())
    clv = m_proba[np.arange(len(y)), y] - k_proba[np.arange(len(y)), y]

    metrics = {
        'n': len(paired), 'miss_cn': miss_cn, 'miss_anchor': miss_anchor,
        'rps_model': rps_model, 'rps_market': rps_market, 'skill': skill,
        'rps_d2': rps_d2, 'skill_d2': skill_d2, 'd2_hit': d2_hit,
        'mdl_hit': mdl_hit, 'mkt_hit': mkt_hit,
        'max_diff': float(df['max_diff'].max()),
        'method_ok': float((df['method'] == 'T-005 v3 D3 (market_replication, model_fallback)').mean()),
        'drawsrc_ok': float((df['draw_source'] == 'market_hcp').mean()),
        'argmax_consistent': float((m_proba.argmax(axis=1) == k_proba.argmax(axis=1)).mean()),
        'clv_mean': float(clv.mean()),
        'push_share': float((y == 1).mean()),
        # A4: 走水指纹
        'draw_min': float(df['m_draw'].min()), 'draw_max': float(df['m_draw'].max()),
        'draw_mean': float(df['m_draw'].mean()), 'draw_nunique': int(df['m_draw'].nunique()),
        'step_hits': int(sum(((df['m_draw'] - s).abs() < STEP_TOL).sum() for s in OLD_STEPS)),
        # A5: D2→D3 走水位移
        'shift_gt5pp': int((abs(df['m_draw'] - df['d2_draw']) > 0.05).sum()),
        'shift_mean': float((df['m_draw'] - df['d2_draw']).mean()),
    }
    log(f'   [{align}] n={metrics["n"]} max|Δ|={metrics["max_diff"]:.2e} '
        f'skill={skill:+.6f} method_ok={metrics["method_ok"]:.0%} 台阶命中={metrics["step_hits"]}')
    return metrics, df, len(paired), miss_cn, miss_anchor


def main():
    print('=' * 78)
    print('C-20261016-122 D3 接线回放 — 基准窗口 monkeypatch 回放（不落库）')
    print('=' * 78)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    print('\n1. 加载基准窗口（存量 HC 行 + 官方让球赛果）与桥接映射...')
    model = mbh.load_model_side(conn)
    en2cn = mbh.build_en2cn(conn)
    print(f'   窗口场次: {len(model)}  桥接映射: {len(en2cn)}')

    predictor = make_replay_predictor()
    print('   monkeypatch 桩就绪（特征构造/模型推理 → 锚行快照桩；draw_model=非None占位）')

    results = {}
    for align in ('t-24h', 'noon'):
        print(f'\n2. 回放 {align} 口径...')
        m, df, n, miss_cn, miss_anchor = run_align(conn, en2cn, model, align, predictor, print)
        results[align] = (m, df, n, miss_cn, miss_anchor)

    conn.close()

    # 报告
    print('\n3. 生成报告...')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    L = ['# C-20261016-122 D3 接线回放 — 基准窗口 monkeypatch 回放（不落库）', '',
         f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', '',
         '## 〇、本报告怎么读（必读）', '',
         '- **skill≈0 是构造性必然**：D3 = 市场复制，模型侧数学 ≡ 竞彩让球三向去水。',
         '  本回放的价值 = **接线证明**（生产 predict() 真的走 D3 分支、真的吃 handicap_history 锚行），',
         '  **不是**模型泛化能力证明。禁止把本报告读作「模型跑平市场」。',
         '- 回放方式：monkeypatch 特征构造/模型推理（C-002/C-003 同款思路），',
         '  predict() 在基准同口径锚行上走真实 D3 主路径分支；不落库、不读存量概率。',
         '- 基准对照（D2 存量口径）: `reports/market_benchmark_hc_20261016_200213.md`',
         '  （skill=-0.33、argmax 32.8% vs 市场 54.7%、RPS 0.2999 vs 0.2256）。', '',
         '## 一、验收锚点', '',
         '| 锚点 | 口径 | 判据 | 结果 |',
         '| --- | --- | --- | --- |']
    for align in ('t-24h', 'noon'):
        m, df, n, miss_cn, miss_anchor = results[align]
        if not m:
            L.append(f'| 全部 | {align} | - | 无配对场次 |')
            continue
        a1 = m['max_diff'] <= 1e-12
        a2 = abs(m['skill']) < 1e-9 and m['argmax_consistent'] == 1.0
        a3 = m['method_ok'] == 1.0 and m['drawsrc_ok'] == 1.0
        a4 = m['step_hits'] == 0 and m['draw_nunique'] >= m['n'] - 2
        L.append(f'| A1 逐位一致 | {align} | max|Δ|≤1e-12 | {m["max_diff"]:.2e} {"✅" if a1 else "❌"} |')
        L.append(f'| A2 skill≈0 | {align} | skill=0 且 argmax 一致 | {m["skill"]:+.2e} / {m["argmax_consistent"]:.0%} {"✅" if a2 else "❌"} |')
        L.append(f'| A3 method/标记 | {align} | 全部 D3 串 + market_hcp | {m["method_ok"]:.0%}/{m["drawsrc_ok"]:.0%} {"✅" if a3 else "❌"} |')
        L.append(f'| A4 台阶消失 | {align} | 五台阶命中=0 且走水近连续 | 命中{m["step_hits"]} 去重值{m["draw_nunique"]}/{m["n"]} {"✅" if a4 else "❌"} |')
    L += ['',
          '## 二、总体指标（同窗口三方对照：D3 回放 vs D2 存量 vs 市场）', '',
          '| 口径 | n | RPS_D3回放 | RPS_D2存量 | RPS_市场 | skill_D3 | skill_D2 | argmax_D3 | argmax_D2 | argmax_市场 | 走水占比 | CLV_D3 |',
          '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for align in ('t-24h', 'noon'):
        m, df, n, miss_cn, miss_anchor = results[align]
        if not m:
            L.append(f'| {align} | 0 | - | - | - | - | - | - | - | - | - | - |')
            continue
        L.append(f'| {align} | {m["n"]} | {m["rps_model"]:.6f} | {m["rps_d2"]:.4f} | {m["rps_market"]:.6f} | '
                 f'{m["skill"]:+.2e} | {m["skill_d2"]:+.4f} | {m["mdl_hit"]:.3f} | {m["d2_hit"]:.3f} | {m["mkt_hit"]:.3f} | '
                 f'{m["push_share"]:.1%} | {m["clv_mean"]:+.2e} |')
    L += ['',
          '- n 与 20:02 基准报告（64/65）的差值来自窗口内赛后标签回填（同口径查询的当前库状态）。',
          '- skill_D2 用同一配对集从存量 D2 行现算（窗口存量行未被今日 --write 触碰），',
          '  数值与 20:02 报告（-0.33）同量级即可，逐位不必一致（配对集略不同）。']
    L += ['',
          '## 三、走水分布（A4/A5 证据）', '',
          '| 口径 | 走水min | 走水mean | 走水max | 去重值数 | 五台阶命中(±0.2pp) | D2→D3位移>5pp场数 | 平均位移 |',
          '| --- | --- | --- | --- | --- | --- | --- | --- |']
    for align in ('t-24h', 'noon'):
        m, df, n, miss_cn, miss_anchor = results[align]
        if not m:
            continue
        L.append(f'| {align} | {m["draw_min"]:.3f} | {m["draw_mean"]:.3f} | {m["draw_max"]:.3f} | '
                 f'{m["draw_nunique"]}/{m["n"]} | {m["step_hits"]} | {m["shift_gt5pp"]} | {m["shift_mean"]:+.3f} |')
    L += ['', f'- 旧五台阶: {", ".join(f"{s:.1%}" for s in OLD_STEPS)}（D2 isotonic 指纹，±{STEP_TOL:.1%} 容差）。',
          '- D3 走水 = 市场让平去水，应随赔率连续变化（~20-28% 区间为主）。', '',
          '## 四、逐场明细', '']
    for align in ('t-24h', 'noon'):
        m, df, n, miss_cn, miss_anchor = results[align]
        if df is None or len(df) == 0:
            continue
        d = df[['cn_id', 'anchor_ts', 'anchor_hcp', 'actual',
                'd2_draw', 'm_draw', 'k_draw', 'max_diff', 'pred', 'method']].copy()
        d.columns = ['cn_id', '锚行ts', '锚hcp(胜/平/负)', '标签', 'D2走水', 'D3走水', '市场走水', 'max|Δ|', 'D3预测', 'method']
        d['D2走水'] = d['D2走水'].map(lambda v: f'{v:.4f}')
        d['D3走水'] = d['D3走水'].map(lambda v: f'{v:.4f}')
        d['市场走水'] = d['市场走水'].map(lambda v: f'{v:.4f}')
        d['max|Δ|'] = d['max|Δ|'].map(lambda v: f'{v:.1e}')
        d['method'] = d['method'].map(lambda s: 'D3' if 'D3' in str(s) else str(s)[:30])
        L += [f'### {align} 口径（n={len(d)}）', '', d.to_markdown(index=False), '']
    L += ['## 五、口径与限制', '',
          '- 回放窗口/标签/桥接/市场锚口径与 `market_benchmark_hc` 完全一致（load_model_side + t-24h/noon）。',
          '- 锚行 raw 赔率直接喂给 predict()（hcp close=锚行），line 占位 -1.0（D3 分支不消费 line）。',
          '- 桥接用 matches 表英文名 → team_mapping/odds500_match → 中文 cn_id，与管线 best-mid 择优',
          '  在极少数 mid 歧义场（如 crawl 日期前缀错位）可能取到不同锚行——爬虫层既有问题，非 D3 引入。',
          '- 本回放不落库（is_replay 行也不写），对 model_predictions 零写入。', '']

    rep_path = os.path.join(REPORTS_DIR, f'replay_d3_benchmark_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'   报告已保存: {rep_path}')

    # 控制台总结
    all_ok = True
    for align in ('t-24h', 'noon'):
        m = results[align][0]
        if not m:
            all_ok = False
            continue
        ok = (m['max_diff'] <= 1e-12 and abs(m['skill']) < 1e-9 and m['argmax_consistent'] == 1.0
              and m['method_ok'] == 1.0 and m['drawsrc_ok'] == 1.0 and m['step_hits'] == 0)
        all_ok = all_ok and ok
    print('\n' + ('✅ 全部验收锚点通过（构造性）— D3 接线证明成立' if all_ok else '❌ 存在未通过锚点，见报告'))


if __name__ == '__main__':
    main()
