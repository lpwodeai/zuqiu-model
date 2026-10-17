#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C-20261004-005: 市场基准「罗盘」— 模型 WDL 概率 vs 500.com 收盘共识线（降级版）。

背景：模型长期存在「平局系统性高估」（draw z=-2.86，价值投注亏损）。消融④臂已定位
根因为 Platt 校准跨赛季漂移，修复方案为分联赛+近赛季校准。本脚本提供独立的「市场基准」
校验视角：把生产模型 WDL 概率与 500.com 百家欧指收盘均价（odds500 共识线）在**同一批
已完赛场次**上配对，量化「模型是否跑赢市场」——若模型连共识收盘线都赢不了，那价值投注
亏损就不是校准问题而是 edge 不足；反之若模型 RPS/CLV 优于市场，则校准修复方向成立。

口径（降级版约定）：
  - 模型侧锚点：model_predictions.model_name='generate_unified_report_v2.0'、
    is_replay=0（生产非回放预测），取 WDL_home/draw/away 三行概率。
  - 市场侧锚点：odds500_ouzhi_summary.avg_live_win/draw/lose（收盘均赔），
    隐含概率去水（除以三向隐含概率总和，使 sum=1，公平对比模型 sum=1）。
  - 桥接键：match_date + 主队英文名 + 客队英文名（matches.home/away_team 与
    odds500_match.home/away_team_en 同口径，归一化空格/大小写）。
  - 标签序：0=客胜 1=平 2=主胜；概率列序 [客胜, 平, 主胜]。

指标：
  - RPS_model / RPS_market（越接近 0 越好），RPS_skill = 1 - RPS_model/RPS_market
    （>0 表示模型优于市场）。
  - CLV（realized closing-line value）= mean(p_model[实际赛果] - p_market[实际赛果])，
    正表示模型对「实际发生结果」赋权高于收盘市场 = 模型比市场更「聪明」。

输出：reports/market_benchmark_compass_<ts>.md + 控制台
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
# 实际赛果 → 标签（0=客胜 1=平 2=主胜）
ACTUAL_MAP = {
    '主胜': 2, '胜': 2, 'W': 2,
    '平局': 1, '平': 1, 'D': 1,
    '客胜': 0, '负': 0, 'L': 0,
}


def _norm(s):
    """队名归一化：去首尾空格、压缩多空格、小写。"""
    return ' '.join((str(s or '')).split()).strip().lower()


def compute_rps(y_true, y_proba):
    """RPS；y_true 为 0/1/2，y_proba 列序 [客胜,平,主胜]。"""
    y_true = np.asarray(y_true, dtype=int)
    y_proba = np.asarray(y_proba, dtype=float)
    n = len(y_true)
    o = np.zeros((n, 3))
    o[np.arange(n), y_true] = 1.0
    return float(np.mean(np.sum((np.cumsum(y_proba, axis=1) - np.cumsum(o, axis=1)) ** 2, axis=1)) / 2)


def load_model_side(conn):
    """加载模型非回放 WDL 预测 + matches 赛果。返回 dict 列表（键 m_home/m_draw/m_away）。"""
    rows = conn.execute("""
        SELECT m.match_id, m.match_date, m.home_team, m.away_team, m.league, m.actual_wdl
        FROM matches m
        WHERE EXISTS (
            SELECT 1 FROM model_predictions mp
            WHERE mp.match_id = m.match_id
              AND mp.model_name = ? AND mp.is_replay = 0
              AND mp.prediction_type = 'WDL_home'
        )
          AND m.actual_wdl IS NOT NULL AND m.actual_wdl != ''
        ORDER BY m.match_date
    """, (MODEL_NAME,)).fetchall()

    preds = {}
    for r in conn.execute("""
        SELECT match_id, prediction_type, probability
        FROM model_predictions
        WHERE model_name = ? AND is_replay = 0
          AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')
    """, (MODEL_NAME,)):
        preds.setdefault(r['match_id'], {})[r['prediction_type']] = r['probability']

    out = []
    for r in rows:
        wdl = preds.get(r['match_id'])
        act = ACTUAL_MAP.get(str(r['actual_wdl']).strip())
        if not wdl or act is None:
            continue
        home = float(wdl.get('WDL_home') or 0.0)
        draw = float(wdl.get('WDL_draw') or 0.0)
        away = float(wdl.get('WDL_away') or 0.0)
        s = home + draw + away
        if s <= 0:
            continue
        out.append({
            'key': (str(r['match_date'])[:10], _norm(r['home_team']), _norm(r['away_team'])),
            'match_id': r['match_id'],
            'match_date': str(r['match_date'])[:10],
            'league': r['league'],
            'actual': act,
            # 模型侧概率（已归一）：[主胜, 平, 客胜]
            'm_home': home / s, 'm_draw': draw / s, 'm_away': away / s,
        })
    return out


def load_market_side(conn):
    """加载 500.com 收盘均赔 → 去水隐含概率。键为 (date, home_en, away_en)。"""
    rows = conn.execute("""
        SELECT s.match_id, s.avg_live_win, s.avg_live_draw, s.avg_live_lose,
               m.match_date, m.home_team_en, m.away_team_en, m.league
        FROM odds500_ouzhi_summary s
        LEFT JOIN odds500_match m ON s.match_id = m.match_id
    """).fetchall()

    mkt = {}
    for r in rows:
        try:
            win = float(r['avg_live_win']); draw = float(r['avg_live_draw']); lose = float(r['avg_live_lose'])
        except (TypeError, ValueError):
            continue
        if not (win > 1 and draw > 1 and lose > 1):
            continue
        p_raw = np.array([1 / win, 1 / draw, 1 / lose])  # [主胜, 平, 客胜]
        p = p_raw / p_raw.sum()  # 去水，sum=1
        key = (str(r['match_date'])[:10], _norm(r['home_team_en']), _norm(r['away_team_en']))
        mkt[key] = {'k_home': float(p[0]), 'k_draw': float(p[1]), 'k_away': float(p[2])}
    return mkt


def main():
    print('=' * 70)
    print('市场基准「罗盘」— 模型 WDL vs 500.com 收盘共识线')
    print('=' * 70)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    print('\n1. 加载模型侧与市场侧...')
    model = load_model_side(conn)
    market = load_market_side(conn)
    conn.close()
    print(f'   模型侧（非回放+有赛果）: {len(model)} 场')
    print(f'   市场侧（去水隐含概率）: {len(market)} 场')

    print('\n2. 桥接配对（日期+主队英名+客队英名）...')
    paired = []
    for rec in model:
        m = market.get(rec['key'])
        if m is not None:
            paired.append({**rec, **m})
    print(f'   桥接命中: {len(paired)} 场 ({len(paired)/max(len(model),1):.1%})')
    if not paired:
        print('   无配对样本，退出。')
        return

    df = pd.DataFrame(paired)
    y_true = df['actual'].to_numpy(int)
    model_proba = df[['m_away', 'm_draw', 'm_home']].to_numpy(float)   # [客胜,平,主胜]
    market_proba = df[['k_away', 'k_draw', 'k_home']].to_numpy(float)

    rps_model = compute_rps(y_true, model_proba)
    rps_market = compute_rps(y_true, market_proba)
    rps_skill = 1 - rps_model / rps_market if rps_market > 0 else float('nan')

    # CLV（realized）：实际赛果上 模型概率 - 市场概率
    mdl_real = model_proba[np.arange(len(y_true)), y_true]
    mkt_real = market_proba[np.arange(len(y_true)), y_true]
    clv = mdl_real - mkt_real
    clv_pos_share = float((clv > 0).mean())

    # argmax 命中率对比
    mdl_hit = float((model_proba.argmax(axis=1) == y_true).mean())
    mkt_hit = float((market_proba.argmax(axis=1) == y_true).mean())

    print('\n3. 总体指标（配对样本 n=%d）' % len(paired))
    print(f'   RPS_model  = {rps_model:.4f}')
    print(f'   RPS_market = {rps_market:.4f}')
    print(f'   RPS_skill  = {rps_skill:+.4f}  (>0 模型优于市场)')
    print(f'   argmax命中  模型={mdl_hit:.3f}  市场={mkt_hit:.3f}')
    print(f'   CLV(realized) mean={clv.mean():+.4f}  median={np.median(clv):+.4f}  正值占比={clv_pos_share:.1%}')

    # 分联赛
    print('\n4. 分联赛对比...')
    league_rows = []
    for lg, g in df.groupby('league'):
        if lg is None:
            continue
        yt = g['actual'].to_numpy(int)
        mp = g[['m_away', 'm_draw', 'm_home']].to_numpy(float)
        kp = g[['k_away', 'k_draw', 'k_home']].to_numpy(float)
        rm = compute_rps(yt, mp); rk = compute_rps(yt, kp)
        cl = mp[np.arange(len(yt)), yt] - kp[np.arange(len(yt)), yt]
        league_rows.append({
            'league': lg, 'n': len(g),
            'RPS_model': round(rm, 4), 'RPS_market': round(rk, 4),
            'RPS_skill': round(1 - rm / rk, 4) if rk > 0 else None,
            'CLV_mean': round(float(cl.mean()), 4),
            'CLV_pos%': round(float((cl > 0).mean()) * 100, 1),
        })
    lg_df = pd.DataFrame(league_rows).sort_values('n', ascending=False)
    print(lg_df.to_string(index=False))

    # 报告
    print('\n5. 生成报告...')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    lines = ['# 市场基准「罗盘」— 模型 WDL vs 500.com 收盘共识线', '',
             f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', '',
             f'- 模型侧锚点: model_predictions.model_name=`{MODEL_NAME}`、is_replay=0',
             f'- 市场侧锚点: odds500_ouzhi_summary.avg_live（百家欧指收盘均赔，隐含概率去水）',
             f'- 桥接键: match_date + 主队英名 + 客队英名', '',
             f'## 一、总体指标（配对样本 n={len(paired)}）', '',
             f'| 指标 | 数值 |', f'| --- | --- |',
             f'| RPS_model | {rps_model:.4f} |',
             f'| RPS_market | {rps_market:.4f} |',
             f'| RPS_skill（>0=模型优） | {rps_skill:+.4f} |',
             f'| argmax命中 模型 | {mdl_hit:.3f} |',
             f'| argmax命中 市场 | {mkt_hit:.3f} |',
             f'| CLV mean（实际赛果） | {clv.mean():+.4f} |',
             f'| CLV median | {np.median(clv):+.4f} |',
             f'| CLV 正值占比 | {clv_pos_share:.1%} |', '',
             '## 二、分联赛', '',
             lg_df.to_markdown(index=False) if len(lg_df) else '（无分联赛数据）', '',
             '## 三、解读口径', '',
             '- RPS_skill = 1 - RPS_model/RPS_market；>0 表示模型概率序列优于市场收盘线。',
             '- CLV(realized) = mean(p_model[实际赛果] - p_market[实际赛果])；正表示模型对',
             '  实际发生结果的平均赋权高于市场，等价于「模型相对收盘线的 edge」。',
             '- 若 RPS_skill <= 0 且 CLV <= 0，说明模型未能跑赢共识线，价值投注亏损的根因',
             '  更可能在 edge 不足而非单一校准项；反之校准修复（分联赛景气化）方向成立。', '']
    rep_path = os.path.join(REPORTS_DIR, f'market_benchmark_compass_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'   报告已保存: {rep_path}')
    print('\n完成。')


if __name__ == '__main__':
    main()