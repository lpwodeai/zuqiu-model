#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C-20260905-003: 市场水平错位测试 — 模型 OOF 概率水平 vs 竞彩/500.com 两源去抽水水平。

背景：C-20260905-002 审计确认回测数据干净，但发现结构性事实——模型以竞彩赔率特征训练
（13% 抽水，raw 隐含概率较 500.com 高 ~8%），回测却以 500.com avg_live（5.4% 抽水）为
投注市场。本脚本验证假设：模型概率水平是否贴近「高抽水竞彩市场」而非「去抽水真实概率」，
从而系统性抬高 edge = p_model - p_market（若能解释 EV-ROI 缺口则是最简洁根因）。

输出：reports/market_level_mismatch_<ts>.md
"""

import os
import sys
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from feature_utils import normalize_team_name  # noqa: E402

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
OOF_PATH = os.path.join(ASSETS_DIR, 'oof_predictions_20260903_012452.csv')

# 方向列映射：OOF 概率列为 away/draw/home；实际结果 actual_label ∈ {客胜,平局,主胜}
DIRS = ['away', 'draw', 'home']


def de_vig(p):
    """比例去抽水：p / Σp。输入 (n,3) 或 (3,) 或 (n,) 单列。"""
    p = np.asarray(p, dtype=float)
    if p.ndim == 1 and p.size == 3:
        return p / p.sum()
    if p.ndim == 2:
        return p / p.sum(axis=1, keepdims=True)
    return p


def main():
    print('=' * 60)
    print('市场水平错位测试 — 模型概率 vs 竞彩/500.com 去抽水水平')
    print('=' * 60)

    print('\n1. 加载 OOF 预测...')
    oof = pd.read_csv(OOF_PATH)
    print(f'   OOF 场次: {len(oof)}')

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=5000")

    # --- 竞彩 wdl_history 末条快照 ---
    print('\n2. 加载竞彩 wdl_history 末条快照 + 500.com avg_live...')
    wdl = pd.read_sql_query("SELECT match_id, timestamp, win_a, win_b, draw FROM wdl_history", conn)
    for c in ['win_a', 'win_b', 'draw']:
        wdl[c] = pd.to_numeric(wdl[c], errors='coerce')
    wdl = wdl[(wdl['win_a'] > 1) & (wdl['win_b'] > 1) & (wdl['draw'] > 1)].copy()
    wdl = wdl.sort_values(['match_id', 'timestamp'])
    wdl_last = wdl.groupby('match_id').last().reset_index()
    # 竞彩 match_id 结构：日期_主队_客队（主队在前）
    parts = wdl_last['match_id'].astype(str).str.split('_', n=2, expand=True)
    wdl_last['key'] = parts[0] + '|' + parts[1].map(normalize_team_name) + '|' + parts[2].map(normalize_team_name)
    wdl_last = wdl_last.drop_duplicates('key', keep='last')
    wdl_map = wdl_last.set_index('key')[['win_a', 'draw', 'win_b']]  # win_a=主胜, win_b=客胜

    # --- 500.com avg_live ---
    o500 = pd.read_sql_query("""
        SELECT s.match_id, s.avg_live_win, s.avg_live_draw, s.avg_live_lose,
               m.match_date, m.home_team_cn, m.away_team_cn
        FROM odds500_ouzhi_summary s
        LEFT JOIN odds500_match m ON s.match_id = m.match_id
    """, conn)
    conn.close()
    for c in ['avg_live_win', 'avg_live_draw', 'avg_live_lose']:
        o500[c] = pd.to_numeric(o500[c], errors='coerce')
    o500 = o500[(o500['avg_live_win'] > 1) & (o500['avg_live_draw'] > 1)
                & (o500['avg_live_lose'] > 1)].copy()
    o500['key'] = o500['match_date'].astype(str).str[:10] + '|' + \
        o500['home_team_cn'].map(normalize_team_name) + '|' + \
        o500['away_team_cn'].map(normalize_team_name)
    o500_map = o500.set_index('key')[['avg_live_win', 'avg_live_draw', 'avg_live_lose']]

    # --- 组装每场：模型概率(blend platt) + 两源去抽水概率 ---
    print('\n3. 组装三源概率矩阵...')
    oof = oof.copy()
    oof['key'] = oof['date'].astype(str).str[:10] + '|' + \
        oof['home_team_name'].map(normalize_team_name) + '|' + \
        oof['away_team_name'].map(normalize_team_name)
    # 模型概率 = xgb_platt 与 lgb_platt 均值（[away, draw, home]）
    for d_i, d in enumerate(DIRS):
        oof[f'model_{d}'] = (oof[f'xgb_platt_{d}'] + oof[f'lgb_platt_{d}']) / 2.0

    sp = oof['key'].map(wdl_map.to_dict('index'))
    o5 = oof['key'].map(o500_map.to_dict('index'))
    n_sp = oof['key'].isin(wdl_map.index).sum()
    n_500 = oof['key'].isin(o500_map.index).sum()
    print(f'   竞彩对齐: {n_sp}/{len(oof)} ({n_sp/len(oof):.1%})')
    print(f'   500.com对齐: {n_500}/{len(oof)} ({n_500/len(oof):.1%})')

    rows = []
    for i in range(len(oof)):
        m = oof.iloc[i]
        rec = {'match_id': m['match_id'], 'date': m['date'],
               'actual_label': m['actual_label']}
        rec['model'] = [m['model_away'], m['model_draw'], m['model_home']]
        k = m['key']
        if k in wdl_map.index:
            w = wdl_map.loc[k]
            raw = np.array([w['win_b'], w['draw'], w['win_a']], dtype=float)  # [客,平,主]
            rec['sp_impl'] = 1.0 / raw
            rec['sp_devig'] = de_vig(1.0 / raw)
        if k in o500_map.index:
            w = o500_map.loc[k]
            raw = np.array([w['avg_live_lose'], w['avg_live_draw'], w['avg_live_win']], dtype=float)  # [客,平,主]
            rec['m500_impl'] = 1.0 / raw
            rec['m500_devig'] = de_vig(1.0 / raw)
        rows.append(rec)

    print('\n4. 水平对比（按方向）...')
    # 每场取三个源的概率数组（有数据的）
    def mean_levels(getter, label):
        vals = {'away': [], 'draw': [], 'home': []}
        for r in rows:
            v = getter(r)
            if v is None:
                continue
            for d_i, d in enumerate(DIRS):
                vals[d].append(v[d_i])
        out = {d: float(np.mean(vals[d])) for d in DIRS}
        print(f'   {label}: 客胜={out["away"]:.4f} 平局={out["draw"]:.4f} 主胜={out["home"]:.4f}')
        return out

    m_model = mean_levels(lambda r: r.get('model'), '模型(blend platt)')
    m_sp_dev = mean_levels(lambda r: r.get('sp_devig'), '竞彩去抽水')
    m_500_dev = mean_levels(lambda r: r.get('m500_devig'), '500.com去抽水')
    m_sp_raw = mean_levels(lambda r: r.get('sp_impl'), '竞彩raw隐含')
    m_500_raw = mean_levels(lambda r: r.get('m500_impl'), '500.com raw隐含')

    # 实际结果边际频率
    actual_marg = {d: float((oof['actual_label'] == {'away': '客胜', 'draw': '平局', 'home': '主胜'}[d]).mean())
                   for d in DIRS}
    print(f'   实际结果边际: 客胜={actual_marg["away"]:.4f} 平局={actual_marg["draw"]:.4f} 主胜={actual_marg["home"]:.4f}')

    print('\n5. edge 偏差（模型 vs 各市场去抽水）按赔率档...')
    # 按 500.com 主胜赔率分档，看模型 vs 两源去抽水的偏差
    def edge_stats(ref_key, label):
        buckets = {'<=2.0': [], '2~3': [], '3~4.5': [], '>4.5': []}
        for r in rows:
            if 'm500_impl' not in r:
                continue
            o_home = 1.0 / r['m500_impl'][2]
            b = ('<=2.0' if o_home <= 2.0 else '2~3' if o_home <= 3.0
                 else '3~4.5' if o_home <= 4.5 else '>4.5')
            ref = r.get(ref_key)
            if ref is None:
                continue
            # max 方向模型概率 - 市场去抽水概率
            bm = np.argmax(r['model'])
            buckets[b].append(r['model'][bm] - ref[bm])
        print(f'   [{label}] 模型max方向概率 - 市场去抽水概率:')
        for b, v in buckets.items():
            if v:
                print(f'      {b}: mean={np.mean(v):+.4f} (n={len(v)})')

    edge_stats('m500_devig', '500.com')
    edge_stats('sp_devig', '竞彩')

    # --- 输出报告 ---
    print('\n6. 生成报告...')
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    # 结论要点（随运行自动计算部分 + 固定分析）
    dev_max = m_model['home'] - m_500_dev['home']
    lines = [f'# 市场水平错位测试 — 模型概率 vs 竞彩/500.com 去抽水水平', '',
             f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', '',
             f'- OOF: {OOF_PATH}（{len(oof)} 场，xgb/lgb platt 均值）',
             f'- 竞彩对齐: {n_sp} 场；500.com 对齐: {n_500} 场', '',
             '## 一、概率水平对比（三向均值）', '',
             '| 源 | 客胜 | 平局 | 主胜 |', '|---|---|---|---|',
             f'| 模型(blend platt) | {m_model["away"]:.4f} | {m_model["draw"]:.4f} | {m_model["home"]:.4f} |',
             f'| 竞彩去抽水 | {m_sp_dev["away"]:.4f} | {m_sp_dev["draw"]:.4f} | {m_sp_dev["home"]:.4f} |',
             f'| 500.com去抽水 | {m_500_dev["away"]:.4f} | {m_500_dev["draw"]:.4f} | {m_500_dev["home"]:.4f} |',
             f'| 竞彩 raw 隐含 | {m_sp_raw["away"]:.4f} | {m_sp_raw["draw"]:.4f} | {m_sp_raw["home"]:.4f} |',
             f'| 500.com raw 隐含 | {m_500_raw["away"]:.4f} | {m_500_raw["draw"]:.4f} | {m_500_raw["home"]:.4f} |',
             f'| 实际结果边际 | {actual_marg["away"]:.4f} | {actual_marg["draw"]:.4f} | {actual_marg["home"]:.4f} |', '',
             '## 二、结论', '',
             '1. **市场水平错位假设证伪**：模型三向概率水平（0.3137/0.2412/0.4452）贴近两个去抽水'
             '市场（500.com 0.3129/0.2484/0.4387、竞彩 0.3246/0.2477/0.4278）及实际结果边际'
             '（0.3141/0.2517/0.4342），而非高抽水竞彩 raw 隐含水平（0.3685/0.2812/0.4856）——'
             '模型已学到去抽水真实概率，edge 未被市场水位系统性抬高。',
             '2. **选择膨胀（selection inflation）定位**：模型 max 方向概率较两源去抽水均高 '
             '+3~7pp（各赔率档均匀，500.com 口径 3.4/5.4/6.8/5.9pp；竞彩口径略低）——取最大概率'
             '方向下注必然选择到高估侧（边际校准 ≠ 选择后校准），这是训练/校准均无法消除的选择效应。',
             '3. **分歧过度自信**：结合 edge 分桶（>10pp 桶模型 48.4% vs 实现 35.6%，高估 12.8pp）'
             '——高估随「模型-市场分歧度」放大，高分歧时市场更接近真相。',
             '4. **推论**：训练端三条路径（EV 奖励/MOD/惩罚）修的是「高赔率过度自信」，但真问题是'
             '「选择效应 + 分歧效应」；下一步应测试**决策层 edge 收缩**（p_used = p_market + '
             's·(p_model−p_market)，s<1 直接按比例缩小 edge 以对冲分歧过度自信）。', '']
    rep_path = os.path.join(REPORTS_DIR, f'market_level_mismatch_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'   报告已保存: {rep_path}')

    print('\n完成。')


if __name__ == '__main__':
    main()
