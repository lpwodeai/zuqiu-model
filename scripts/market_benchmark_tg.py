#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C-20261004-008: 市场基准扩展 T-004 总进球 — 模型 over/under 2.5 vs 市场大小球共识。

背景：C-006/C-007 已确证模型 WDL 输给市场共识（w*=1 纯市场最优）。按裁决范围提醒，该结论
不得外推到 T-004 总进球——大小球市场效率通常低于 WDL，λ/xG 机制可能仍有独立价值。本脚本是
市场基准「罗盘」的 TG 版，把模型 T-004 总进球输出与市场大小球（over/under 2.5）共识配对，
量化「模型总进球概率是否跑赢大小球市场」。

市场口径（关键）：
  - 专用大小球赔率表 total_goals_history 的直接大小球盘（goals_0..goals_7_plus 八档），
    覆盖到 2026-09-20（C-20261004-009 排查确认：TG 采集未停更，此前"止于 05-25"为
    match_id_en 桥接列停更导致的误诊）。P(over 2.5) = Σ implied(goals_3..goals_7_plus)
    / Σ implied(全部八档)，隐含概率去水（1/odds 后归一）。
  - 无泄漏门禁：只取 timestamp 严格早于 match_date 的快照（pre-match 共识，避免盘中/赛后
    赔率泄漏）。注意该快照 ~t-24h，早于模型 12:04 午间批处理预测时刻 ~t-8h，故市场侧相对
    模型存在约 16~18h 的信息劣势（对模型结论偏保守，非泄漏）。
  - 桥接键：model_predictions.match_id（英 日期_英主_英客）→ team_mapping(英→中) →
    total_goals_history.match_id（中 日期_中文主_中文客）。

模型口径：model_predictions.model_name='generate_unified_report_v2.0'、is_replay=0，
取 TG_over_2_5 / TG_under_2_5（归一 sum=1）。

指标：Brier（二元，越低越好）、skill=1-Brier_model/Brier_market（>0 模型优）、
argmax（over/under 方向）命中率、CLV(realized)。

输出：reports/market_benchmark_tg_<ts>.md + 控制台。
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

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')

MODEL_NAME = 'generate_unified_report_v2.0'


def _norm(s):
    return ' '.join((str(s or '')).split()).strip().lower()


def compute_brier(y_true, p_over):
    """二元 Brier；y_true ∈ {0(under),1(over)}，p_over = P(over)。"""
    y_true = np.asarray(y_true, dtype=float)
    p_over = np.asarray(p_over, dtype=float)
    return float(np.mean((p_over - y_true) ** 2))


def build_en2cn(conn):
    """英文队名 → 中文队名（team_mapping 优先，odds500_match 兜底）。"""
    en2cn = {}
    for r in conn.execute("SELECT original_name, standard_name FROM team_mapping"):
        if r['standard_name']:
            en2cn.setdefault(_norm(r['original_name']), r['standard_name'])
    for r in conn.execute("SELECT home_team_en, home_team_cn, away_team_en, away_team_cn FROM odds500_match"):
        if r['home_team_cn']:
            en2cn.setdefault(_norm(r['home_team_en']), r['home_team_cn'])
        if r['away_team_cn']:
            en2cn.setdefault(_norm(r['away_team_en']), r['away_team_cn'])
    return en2cn


def load_model_side(conn):
    """模型非回放 TG 预测 + matches 实际总进球（含英文队名供桥接）。"""
    rows = conn.execute("""
        SELECT m.match_id, m.match_date, m.home_team, m.away_team, m.league,
               m.actual_total_goals, m.home_goals, m.away_goals
        FROM matches m
        WHERE EXISTS (
            SELECT 1 FROM model_predictions mp
            WHERE mp.match_id = m.match_id
              AND mp.model_name = ? AND mp.is_replay = 0
              AND mp.prediction_type = 'TG_over_2_5'
        )
        ORDER BY m.match_date
    """, (MODEL_NAME,)).fetchall()

    preds = {}
    for r in conn.execute("""
        SELECT match_id, prediction_type, probability
        FROM model_predictions
        WHERE model_name = ? AND is_replay = 0
          AND prediction_type IN ('TG_over_2_5','TG_under_2_5')
    """, (MODEL_NAME,)):
        preds.setdefault(r['match_id'], {})[r['prediction_type']] = r['probability']

    out = []
    for r in rows:
        tg = preds.get(r['match_id'])
        if not tg:
            continue
        tot = r['actual_total_goals']
        if tot is None:
            hg = r['home_goals']; ag = r['away_goals']
            if hg is None or ag is None:
                continue
            tot = int(hg) + int(ag)
        try:
            tot = int(tot)
        except (TypeError, ValueError):
            continue
        over = 1 if tot >= 3 else 0
        p_over = float(tg.get('TG_over_2_5') or 0.0)
        p_under = float(tg.get('TG_under_2_5') or 0.0)
        s = p_over + p_under
        if s <= 0:
            continue
        out.append({
            'match_id': r['match_id'],
            'match_date': str(r['match_date'])[:10],
            'home_en': r['home_team'],
            'away_en': r['away_team'],
            'league': r['league'],
            'actual_tg': tot,
            'over': over,
            'm_over': p_over / s,
        })
    return out


def load_market_side(conn):
    """total_goals_history 专用大小球盘（goals_0..goals_7_plus）→ 去水 → P(over 2.5)。

    无泄漏门禁：只取 timestamp 严格早于 match_date 的快照（pre-match 共识）。
    P(over 2.5) = Σ implied(goals_3..goals_7_plus) / Σ implied(全部八档)。
    """
    buckets = ['goals_0', 'goals_1', 'goals_2', 'goals_3',
               'goals_4', 'goals_5', 'goals_6', 'goals_7_plus']
    rows = conn.execute("""
        SELECT match_id, timestamp, goals_0, goals_1, goals_2, goals_3,
               goals_4, goals_5, goals_6, goals_7_plus
        FROM total_goals_history
        WHERE match_id IS NOT NULL AND timestamp IS NOT NULL
    """).fetchall()

    by_match = {}
    for r in rows:
        mid = str(r['match_id'])
        ts = str(r['timestamp'])
        date = mid.split('_')[0]
        # 只保留严格早于 match_date 的快照（pre-match，防盘中/赛后泄漏）
        if ts[:10] >= date:
            continue
        by_match.setdefault(mid, {})[ts] = r

    mkt = {}
    for mid, tsmap in by_match.items():
        if not tsmap:
            continue
        latest_ts = max(tsmap.keys())  # 最近一次 pre-match 快照
        r = tsmap[latest_ts]
        implied = {}
        for i, b in enumerate(buckets):
            try:
                o = float(r[b])
            except (TypeError, ValueError):
                continue
            if o is None or o <= 1.0:
                continue
            implied[i] = 1.0 / o
        tot_implied = sum(implied.values())
        if tot_implied <= 0:
            continue
        # over 2.5 = 总进球 ≥ 3 = goals_3..goals_7_plus（下标 i>=3）
        p_over = sum(ip for i, ip in implied.items() if i >= 3) / tot_implied
        mkt[mid] = {'k_over': p_over}
    return mkt


def main():
    print('=' * 78)
    print('市场基准扩展 T-004 总进球 — 模型 over/under 2.5 vs 专用大小球盘共识')
    print('=' * 78)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    print('\n1. 加载模型侧 / 市场侧 / 桥接映射...')
    model = load_model_side(conn)
    market = load_market_side(conn)
    en2cn = build_en2cn(conn)
    conn.close()
    print(f'   模型侧（非回放+有总进球）: {len(model)} 场')
    print(f'   市场侧（pre-match 反推 P(over)）: {len(market)} 场')
    print(f'   队名桥接映射: {len(en2cn)} 个英文名→中文名')

    print('\n2. 桥接配对（英 match_id → team_mapping → 中 match_id）...')
    paired = []
    miss_cn = 0
    for rec in model:
        home_cn = en2cn.get(_norm(rec['home_en']))
        away_cn = en2cn.get(_norm(rec['away_en']))
        if not home_cn or not away_cn:
            miss_cn += 1
            continue
        cn_id = f"{rec['match_date']}_{home_cn}_{away_cn}"
        m = market.get(cn_id)
        if m is not None:
            paired.append({**rec, **m})
    print(f'   中文名缺映射: {miss_cn} 场；桥接命中: {len(paired)} 场 ({len(paired)/max(len(model),1):.1%})')
    if not paired:
        print('   无配对样本。可能原因：①total_goals_history 桥接缺口 ②team_mapping 缺中文名映射。')
        print('   请检查数据源后重试。')
        return

    df = pd.DataFrame(paired)
    y_over = df['over'].to_numpy(float)
    m_over = df['m_over'].to_numpy(float)
    k_over = df['k_over'].to_numpy(float)

    brier_model = compute_brier(y_over, m_over)
    brier_market = compute_brier(y_over, k_over)
    skill = 1 - brier_model / brier_market if brier_market > 0 else float('nan')

    clv = np.where(y_over == 1, m_over, 1 - m_over) - np.where(y_over == 1, k_over, 1 - k_over)
    mdl_hit = float(((m_over > 0.5) == (y_over == 1)).mean())
    mkt_hit = float(((k_over > 0.5) == (y_over == 1)).mean())

    print('\n3. 总体指标（配对样本 n=%d）' % len(paired))
    print(f'   Brier_model  = {brier_model:.4f}')
    print(f'   Brier_market = {brier_market:.4f}')
    print(f'   RPS_skill    = {skill:+.4f}  (>0 模型优于市场)')
    print(f'   argmax命中   模型={mdl_hit:.3f}  市场={mkt_hit:.3f}')
    print(f'   CLV(realized) mean={clv.mean():+.4f}  median={np.median(clv):+.4f}')

    print('\n4. 分联赛对比...')
    league_rows = []
    for lg, g in df.groupby('league'):
        if lg is None:
            continue
        yo = g['over'].to_numpy(float)
        mo = g['m_over'].to_numpy(float)
        ko = g['k_over'].to_numpy(float)
        bm = compute_brier(yo, mo); bk = compute_brier(yo, ko)
        league_rows.append({
            'league': lg, 'n': len(g),
            'Brier_model': round(bm, 4), 'Brier_market': round(bk, 4),
            'skill': round(1 - bm / bk, 4) if bk > 0 else None,
        })
    lg_df = pd.DataFrame(league_rows).sort_values('n', ascending=False)
    print(lg_df.to_string(index=False))

    print('\n5. 生成报告...')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    L = ['# 市场基准扩展 T-004 总进球 — 模型 over/under 2.5 vs 大小球共识', '',
         f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', '',
         f'- 模型侧锚点: `{MODEL_NAME}`、`is_replay=0`、`TG_over_2_5`/`TG_under_2_5`',
         f'- 市场侧锚点: `total_goals_history` 专用大小球盘（goals_0..goals_7_plus）直接反推 P(over 2.5)',
         f'- 无泄漏门禁: 仅取 timestamp < match_date 的 pre-match 快照（防盘中/赛后泄漏）',
         f'- 桥接键: 英 match_id → `team_mapping` → 中 match_id（缺失 {miss_cn} 场中文名映射）', '',
         f'## 一、总体指标（配对样本 n={len(paired)}）', '',
         f'| 指标 | 数值 |', f'| --- | --- |',
         f'| Brier_model | {brier_model:.4f} |',
         f'| Brier_market | {brier_market:.4f} |',
         f'| RPS_skill（>0=模型优） | {skill:+.4f} |',
         f'| argmax命中 模型 | {mdl_hit:.3f} |',
         f'| argmax命中 市场 | {mkt_hit:.3f} |',
         f'| CLV mean（实际方向） | {clv.mean():+.4f} |',
         f'| CLV median | {np.median(clv):+.4f} |', '',
         '## 二、分联赛', '',
         lg_df.to_markdown(index=False) if len(lg_df) else '（无分联赛数据）', '',
         '## 三、口径与限制（重要）', '',
         '- 本脚本直接用 `total_goals_history` 专用大小球盘（goals_0..goals_7_plus）反推 P(over 2.5)，',
         '  比此前 `score_history`（正确比分盘）间接反推更直接、更接近真实大小球市场共识。',
         '- C-20261004-009 排查已确认：`total_goals_history` 采集未停更（覆盖至 2026-09-20），',
         '  此前"止于 05-25"为 `match_id_en` 桥接列停更导致的误诊；本脚本桥接不依赖该列。',
         '- 市场侧快照为 pre-match（timestamp < match_date，约 t-24h），早于模型 12:04 午间',
         '  批处理预测时刻（约 t-8h），故**市场侧相对模型存在约 16~18h 信息劣势**——对「模型',
         '  跑赢市场」的判读偏保守（若模型仍输，则是更强的 edge 不足信号；若模型赢，需扣除该',
         '  信息优势）。',
         '- T-004 结论仅限 over/under 2.5 一条线，不外推 T-006 比分完整分布。', '']
    rep_path = os.path.join(REPORTS_DIR, f'market_benchmark_tg_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'   报告已保存: {rep_path}')
    print('\n完成。')


if __name__ == '__main__':
    main()