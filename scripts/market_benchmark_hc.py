#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C-20261005-010: 市场基准扩展 T-005 让球 — 模型 上盘赢/走水/下盘赢 vs 竞彩让球胜平负共识。

背景：C-006/C-007 已确证模型 WDL 输给市场共识；C-008/C-009 已确证 T-004 总进球整体与市场
近平价、λ/xG 机制含独立信号。本脚本是市场基准「罗盘」的让球（HC）版，把模型 T-005 让球三向
输出与竞彩让球胜平负共识（handicap_history 的 hcp_win/hcp_draw/hcp_lose）配对，量化
「模型让球方向是否跑赢让球市场」。

口径（关键，按裁决两个设计注意）：
  - 比较器用 handicap_history（hcp_win/hcp_draw/hcp_lose = 竞彩让球胜平负整数线），
    **不用 matches.handicap**（C-20261002-014：曾受亚盘半盘口污染，且非权威）。
  - 标签用 matches.actual_handicap（竞彩官方赛果文本 "(-1)胜/平/负"），不经 matches.handicap
    推导；「胜→上盘赢 / 平→走水 / 负→下盘赢」。
  - 三方语义对齐：模型 HC_upper/draw/lower = 上盘赢/走水/下盘赢 ↔ 市场 hcp_win/draw/lose
    = 让球胜/平/负，同整数线域（不做亚盘半盘对照，留二期）。
  - 市场快照双口径（时间对齐，T-005 变盘比 WDL 更大）：
      t-24h：latest timestamp 日期 < match 日期（与 T-004/score 口径一致，最保守无泄漏）
      noon ：latest timestamp <= match 日期 12:04:00（对齐模型午间批处理预测时刻）
  - 桥接键：英 match_id → team_mapping(英→中) → handicap_history.match_id（中）。

指标（三向）：RPS（标签序 0=下盘赢 1=走水 2=上盘赢，概率列序 [下盘,走水,上盘]，越低越好）、
skill=1-RPS_model/RPS_market（>0 模型优）、argmax 命中、CLV(realized)、分联赛。

输出：reports/market_benchmark_hc_<ts>.md + 控制台。
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


def compute_rps(y_true, y_proba):
    """三向 RPS；y_true∈{0,1,2}，y_proba 列序 [下盘,走水,上盘]。"""
    y_true = np.asarray(y_true, dtype=int)
    y_proba = np.asarray(y_proba, dtype=float)
    n = len(y_true)
    o = np.zeros((n, 3))
    o[np.arange(n), y_true] = 1.0
    return float(np.mean(np.sum((np.cumsum(y_proba, axis=1) - np.cumsum(o, axis=1)) ** 2, axis=1)) / 2)


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
    """模型非回放 HC 三向预测 + matches actual_handicap（含英文队名供桥接）。"""
    rows = conn.execute("""
        SELECT m.match_id, m.match_date, m.home_team, m.away_team, m.league, m.actual_handicap
        FROM matches m
        WHERE EXISTS (
            SELECT 1 FROM model_predictions mp
            WHERE mp.match_id = m.match_id
              AND mp.model_name = ? AND mp.is_replay = 0
              AND mp.prediction_type = 'HC_upper'
        )
        ORDER BY m.match_date
    """, (MODEL_NAME,)).fetchall()

    preds = {}
    for r in conn.execute("""
        SELECT match_id, prediction_type, probability
        FROM model_predictions
        WHERE model_name = ? AND is_replay = 0
          AND prediction_type IN ('HC_upper','HC_draw','HC_lower')
    """, (MODEL_NAME,)):
        preds.setdefault(r['match_id'], {})[r['prediction_type']] = r['probability']

    out = []
    for r in rows:
        hc = preds.get(r['match_id'])
        if not hc:
            continue
        ah = str(r['actual_handicap'] or '').strip()
        if '胜' in ah:
            label = 2
        elif '平' in ah or '走' in ah:
            label = 1
        elif '负' in ah:
            label = 0
        else:
            continue  # 无官方让球赛果，跳过
        upper = float(hc.get('HC_upper') or 0.0)
        draw = float(hc.get('HC_draw') or 0.0)
        lower = float(hc.get('HC_lower') or 0.0)
        s = upper + draw + lower
        if s <= 0:
            continue
        out.append({
            'match_id': r['match_id'],
            'match_date': str(r['match_date'])[:10],
            'home_en': r['home_team'],
            'away_en': r['away_team'],
            'league': r['league'],
            'actual': label,
            # 模型侧概率（已归一）：[下盘, 走水, 上盘]
            'm_lower': lower / s, 'm_draw': draw / s, 'm_upper': upper / s,
        })
    return out


def load_market_side(conn, align='t-24h'):
    """handicap_history 竞彩让球胜平负 → 去水 → [P(下盘),P(走水),P(上盘)]，键=中文 match_id。

    align='t-24h': latest timestamp 日期 < match 日期（最保守无泄漏）
    align='noon' : latest timestamp <= match 日期 12:04:00（对齐模型午间预测）
    """
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

    mkt = {}
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
        p_raw = np.array([1.0 / lose, 1.0 / draw, 1.0 / win])  # [下盘, 走水, 上盘]
        p = p_raw / p_raw.sum()
        mkt[mid] = {'k_lower': float(p[0]), 'k_draw': float(p[1]), 'k_upper': float(p[2])}
    return mkt


def run_align(model, market, en2cn, align):
    """按指定对齐口径桥接配对 + 计算指标，返回 (metrics_dict, per_league_rows, n_paired, miss_cn)。"""
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

    if not paired:
        return None, [], 0, miss_cn

    df = pd.DataFrame(paired)
    y = df['actual'].to_numpy(int)
    m_proba = df[['m_lower', 'm_draw', 'm_upper']].to_numpy(float)
    k_proba = df[['k_lower', 'k_draw', 'k_upper']].to_numpy(float)

    rps_model = compute_rps(y, m_proba)
    rps_market = compute_rps(y, k_proba)
    skill = 1 - rps_model / rps_market if rps_market > 0 else float('nan')

    mdl_real = m_proba[np.arange(len(y)), y]
    mkt_real = k_proba[np.arange(len(y)), y]
    clv = mdl_real - mkt_real

    mdl_hit = float((m_proba.argmax(axis=1) == y).mean())
    mkt_hit = float((k_proba.argmax(axis=1) == y).mean())

    # 走水命中（argmax 命中且 label==1）
    push_share = float((y == 1).mean())

    metrics = {
        'n': len(paired), 'miss_cn': miss_cn,
        'rps_model': rps_model, 'rps_market': rps_market, 'skill': skill,
        'mdl_hit': mdl_hit, 'mkt_hit': mkt_hit,
        'clv_mean': float(clv.mean()), 'clv_median': float(np.median(clv)),
        'clv_pos': float((clv > 0).mean()), 'push_share': push_share,
    }

    league_rows = []
    for lg, g in df.groupby('league'):
        if lg is None:
            continue
        yt = g['actual'].to_numpy(int)
        mp = g[['m_lower', 'm_draw', 'm_upper']].to_numpy(float)
        kp = g[['k_lower', 'k_draw', 'k_upper']].to_numpy(float)
        rm = compute_rps(yt, mp); rk = compute_rps(yt, kp)
        cl = mp[np.arange(len(yt)), yt] - kp[np.arange(len(yt)), yt]
        league_rows.append({
            'league': lg, 'n': len(g),
            'RPS_model': round(rm, 4), 'RPS_market': round(rk, 4),
            'skill': round(1 - rm / rk, 4) if rk > 0 else None,
            'CLV_mean': round(float(cl.mean()), 4),
        })
    return metrics, league_rows, len(paired), miss_cn


def main():
    print('=' * 78)
    print('市场基准扩展 T-005 让球 — 模型 上盘/走水/下盘 vs 竞彩让球胜平负共识')
    print('=' * 78)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    print('\n1. 加载模型侧 / 市场侧 / 桥接映射...')
    model = load_model_side(conn)
    en2cn = build_en2cn(conn)
    print(f'   模型侧（非回放+有官方让球赛果）: {len(model)} 场')
    print(f'   队名桥接映射: {len(en2cn)} 个英文名→中文名')

    results = {}
    for align in ('t-24h', 'noon'):
        market = load_market_side(conn, align=align)
        m, lg_rows, n, miss_cn = run_align(model, market, en2cn, align)
        results[align] = (m, lg_rows, n, miss_cn)
        if m:
            print(f'\n--- {align} 口径（配对 n={m["n"]}，中文名缺映射 {miss_cn}）---')
            print(f'   RPS_model={m["rps_model"]:.4f}  RPS_market={m["rps_market"]:.4f}  skill={m["skill"]:+.4f}')
            print(f'   argmax命中 模型={m["mdl_hit"]:.3f}  市场={m["mkt_hit"]:.3f}  走水占比={m["push_share"]:.1%}')
            print(f'   CLV mean={m["clv_mean"]:+.4f}  median={m["clv_median"]:+.4f}  正值占比={m["clv_pos"]:.1%}')
    conn.close()

    # 报告
    print('\n2. 生成报告...')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    L = ['# 市场基准扩展 T-005 让球 — 模型 上盘/走水/下盘 vs 竞彩让球胜平负共识', '',
         f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', '',
         f'- 模型侧锚点: `{MODEL_NAME}`、`is_replay=0`、`HC_upper/draw/lower`（上盘赢/走水/下盘赢）',
         f'- 市场侧锚点: `handicap_history` 的 `hcp_win/hcp_draw/hcp_lose`（竞彩让球胜平负，整数线）',
         f'- 标签: `matches.actual_handicap` 官方赛果文本（胜→上盘/平→走水/负→下盘，不用 matches.handicap）',
         f'- 桥接键: 英 match_id → `team_mapping` → 中 match_id',
         f'- 市场快照双口径: t-24h（timestamp 日期<比赛日）与 noon（<=比赛日 12:04 对齐模型午间预测）', '',
         '## 一、总体指标（三向 RPS，越低越好）', '',
         '| 口径 | n | RPS_model | RPS_market | skill | argmax模型 | argmax市场 | 走水占比 | CLV_mean | CLV正值% |',
         '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for align in ('t-24h', 'noon'):
        m, lg_rows, n, miss_cn = results[align]
        if not m:
            L.append(f'| {align} | 0 | 无配对 | - | - | - | - | - | - | - |')
            continue
        L.append(f'| {align} | {m["n"]} | {m["rps_model"]:.4f} | {m["rps_market"]:.4f} | {m["skill"]:+.4f} | '
                 f'{m["mdl_hit"]:.3f} | {m["mkt_hit"]:.3f} | {m["push_share"]:.1%} | {m["clv_mean"]:+.4f} | {m["clv_pos"]:.1%} |')
    L += ['',
          '## 二、分联赛（各口径）', '']
    for align in ('t-24h', 'noon'):
        m, lg_rows, n, miss_cn = results[align]
        if not lg_rows:
            continue
        lg_df = pd.DataFrame(lg_rows).sort_values('n', ascending=False)
        L += [f'### {align} 口径', '', lg_df.to_markdown(index=False), '']
    L += ['## 三、口径与限制（重要）', '',
          '- 比较器用 `handicap_history`（竞彩让球胜平负，整数线），**不用 `matches.handicap`**',
          '  （C-20261002-014：曾受亚盘半盘口污染且非权威）；标签用 `actual_handicap` 官方文本。',
          '- 三方语义对齐：模型 HC_upper/draw/lower = 上盘赢/走水/下盘赢 ↔ 市场 hcp_win/draw/lose',
          '  = 让球胜/平/负。竞彩让球为整数线（走水=home+handicap==away 是真实结果），',
          '  与亚盘半盘线不同线、不同走水语义，本脚本不做亚盘对照（留二期）。',
          '- 时间对齐（比 WDL 更重要，让球盘临场变盘更大）：提供双口径。',
          '  · t-24h：最保守无泄漏，但市场相对模型约缺 16~28h 信息（偏保守于「模型输市场」）。',
          '  · noon（<=比赛日 12:04）：对齐模型午间批处理预测时刻，同信息集更公平；',
          '    依赖「欧洲联赛中国时间晚间开球、12:04 必为赛前」假设。',
          '- T-005 结论仅限让球三向，不外推 T-006 比分分布。', '']
    rep_path = os.path.join(REPORTS_DIR, f'market_benchmark_hc_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'   报告已保存: {rep_path}')
    print('\n完成。')


if __name__ == '__main__':
    main()