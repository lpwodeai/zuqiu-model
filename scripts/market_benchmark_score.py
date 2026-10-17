#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C-20261005-011: 市场基准扩展 T-006 比分 — 模型完整比分分布 vs 竞彩比分共识。

背景：C-006/C-007 已确证模型 WDL 输给市场；C-008/009（TG）、C-010（HC）已给出总进球与让球
的市场效率结论。本脚本是市场基准「罗盘」的比分（score）版，把模型 T-006 比分分布与竞彩比分
市场共识（score_history 各具体比分赔率）配对，量化「模型比分分布是否跑赢比分市场」。

关键口径：
  - 模型侧锚点：`t006_score_predictor_v5`、`is_replay=0`、`Score_grid_h_a`（6×6=36 格完整
    比分网格，h/a∈0..5），非生产报告 generate_unified_report_v2.0（后者仅 246 场且只存
    Lambda/top5，无完整网格）。14473 场覆盖 2016-08 ~ 2026-09。
  - 市场侧锚点：`score_history`（竞彩比分盘，long 表：match_id/timestamp/score/odds）。
  - 三种表 match_id 均为「中文 日期_中文主_中文客」同格式；队名全名/标准名差异经
    normalize_team_name 归一（'莱切'歧义另按 matches 权威键消歧为莱斯特城/意甲莱切）。
  - 评价格式（主口径，按裁决）：**28 具体比分条件分布**。
    竞彩比分市场原生 = 28 具体比分（0:0..5:2 等）+ 胜其它/平其它/负其它。但 (a) 模型网格
    封顶 5 球（6×6），catch-all（6+球/尾分）在模型侧会截断失真；(b) 仅 1030 场市场采集了
    3 个 catch-all，12015 场只有 28 具体。故对双方均「丢弃 catch-all、对 28 具体比分重归一
    到和=1」，即建模条件分布 P(score | score∈28具体)。样本最大且双方无截断。
  - 无泄漏门禁：市场快照 timestamp 严格早于 match_date（t-24h 最保守）或 <= match_date
    12:04（noon，对齐午间批处理预测时刻）；只取每场最新一条合格快照，且该快照须 28 具体比分
    齐全（否则跳过）。

指标（28 向 RPS，标签按「负→平→胜」序，越低越好）：
  RPS、skill=1-RPS_model/RPS_market（>0 模型优）、LogLoss（顺序无关稳健交叉验证）、
  argmax 精确比分命中、CLV(realized)=mean(p_model[实际]-p_market[实际])、分联赛。

输出：reports/market_benchmark_score_<ts>.md + 控制台。
"""

import os
import re
import sqlite3
import sys
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from team_name_mapping import normalize_team_name  # noqa: E402

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')

MODEL_NAME = 't006_score_predictor_v5'

# 28 具体比分（竞彩比分盘原生具体项），按 RPS 序「负→平→胜」排列
SPECIFIC = [
    (0, 5), (1, 5), (2, 5),
    (0, 4), (1, 4), (2, 4),
    (0, 3), (1, 3), (2, 3),
    (0, 2), (1, 2), (0, 1),
    (3, 3), (2, 2), (1, 1), (0, 0),
    (1, 0), (2, 0), (2, 1),
    (3, 0), (3, 1), (3, 2),
    (4, 0), (4, 1), (4, 2),
    (5, 0), (5, 1), (5, 2),
]
LABELS = [f"{h}:{a}" for (h, a) in SPECIFIC]
SPECIFIC_SET = set(SPECIFIC)
LABEL_IDX = {s: i for i, s in enumerate(SPECIFIC)}
LABEL_SET = set(LABELS)
CATCH_ALL = {'胜其它', '平其它', '负其它'}
GRID_MAX = 5  # 模型网格 0..5


def norm_key(match_id):
    """中文 match_id → 归一化键（日期_标准主队_标准客队）。

    matches/t006 用全名（曼彻斯特联/托特纳姆热刺/巴黎圣日尔曼），score_history 用标准名
    （曼联/热刺/巴黎圣日耳曼），统一经 normalize_team_name 归一到 TEAM_ALIASES 标准中文。
    """
    parts = str(match_id).split('_', 2)
    if len(parts) < 3:
        return None
    date, home, away = parts[0], parts[1], parts[2]
    h = normalize_team_name(home) or home
    a = normalize_team_name(away) or away
    return f"{date}_{h}_{a}"


# 竞彩比分盘把「莱斯特城」误记为「莱切」（与意甲莱切同名冲突）。market 侧消歧时需按 matches 权威键
# 决定 '莱切' 指莱斯特城（英超）还是莱切（意甲）。
_AMBIG = {'莱切': ('莱切', '莱斯特城')}


def _candidates(raw):
    n = normalize_team_name(raw) or raw
    return _AMBIG.get(n, (n,))


def resolve_key(match_id, valid):
    """score_history 专用：对歧义队名（'莱切'）按 matches 权威归一键消歧后返回 canonical key。"""
    parts = str(match_id).split('_', 2)
    if len(parts) < 3:
        return None
    date, home, away = parts[0], parts[1], parts[2]
    for h in _candidates(home):
        for a in _candidates(away):
            k = f"{date}_{h}_{a}"
            if k in valid:
                return k
    return f"{date}_{_candidates(home)[0]}_{_candidates(away)[0]}"


def build_valid_keys(conn):
    """matches 表所有归一化键（权威），供 market 侧消歧。"""
    keys = set()
    for r in conn.execute("SELECT match_id FROM matches"):
        nk = norm_key(r['match_id'])
        if nk:
            keys.add(nk)
    return keys


def compute_rps(y, P):
    """N 向 RPS；y∈[0,N)，P 为 (n,N) 概率矩阵（列序同 RPS 序），越低越好。"""
    y = np.asarray(y, dtype=int)
    P = np.asarray(P, dtype=float)
    n = len(y)
    O = np.zeros_like(P)
    O[np.arange(n), y] = 1.0
    return float(np.mean(np.sum((np.cumsum(P, axis=1) - np.cumsum(O, axis=1)) ** 2, axis=1)) / 2)


def compute_logloss(y, P):
    y = np.asarray(y, dtype=int)
    P = np.asarray(P, dtype=float)
    eps = 1e-12
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], eps, 1.0))))


def _norm_grid(probs):
    """36 格 → 28 具体比分 → 重归一。probs: dict[(h,a)] -> float（0..5）。"""
    vec = np.array([probs.get((h, a), 0.0) for (h, a) in SPECIFIC], dtype=float)
    s = vec.sum()
    if s <= 0:
        return None
    return vec / s


def load_model_side(conn):
    """t006_score_predictor_v5 非回放：36 格 → 28 具体比分条件分布。"""
    grid_rows = conn.execute("""
        SELECT match_id, prediction_type, probability
        FROM model_predictions
        WHERE model_name = ? AND is_replay = 0
          AND prediction_type LIKE 'Score_grid_%'
    """, (MODEL_NAME,)).fetchall()

    by_match = defaultdict(dict)
    for r in grid_rows:
        m = re.match(r'Score_grid_(\d+)_(\d+)', r['prediction_type'])
        if not m:
            continue
        h, a = int(m.group(1)), int(m.group(2))
        try:
            p = float(r['probability'])
        except (TypeError, ValueError):
            continue
        by_match[r['match_id']][(h, a)] = p

    out = {}
    for mid, grid in by_match.items():
        if len(grid) < 36:  # 网格不完整，跳过
            continue
        vec = _norm_grid(grid)
        if vec is None:
            continue
        nk = norm_key(mid)
        if nk is None:
            continue
        out[nk] = vec
    return out


def load_actual_side(conn):
    """matches 实际比分 → 28 具体比分 index（只保留实际∈28具体，供条件分布口径）。"""
    rows = conn.execute("""
        SELECT match_id, league, home_goals, away_goals
        FROM matches
        WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL
    """).fetchall()
    out = {}
    for r in rows:
        try:
            h, a = int(r['home_goals']), int(r['away_goals'])
        except (TypeError, ValueError):
            continue
        if (h, a) not in SPECIFIC_SET:
            continue  # 实际为「其它/6+球」，条件口径剔除
        nk = norm_key(r['match_id'])
        if nk is None:
            continue
        out[nk] = {'league': r['league'], 'label': LABEL_IDX[(h, a)]}
    return out


def load_market_side(conn, align='t-24h', valid=None):
    """score_history 竞彩比分盘 → 28 具体比分去水重归一，键=match_id（中文）。

    align='t-24h': latest timestamp 日期 < match 日期（最保守无泄漏）
    align='noon' : latest timestamp <= match 日期 12:04:00（对齐午间预测）
    仅接受该快照 28 具体比分齐全（n_specific==28）的场次。
    """
    rows = conn.execute("""
        SELECT match_id, timestamp, score, odds
        FROM score_history
        WHERE score IS NOT NULL AND odds IS NOT NULL
    """).fetchall()

    by_match = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        mid = str(r['match_id'])
        ts = str(r['timestamp'])
        date = mid.split('_')[0]
        if align == 'noon':
            if ts > f"{date} 12:04:00":
                continue
        else:
            if ts[:10] >= date:
                continue
        sc = str(r['score'])
        if sc not in LABEL_SET:
            continue  # 只看 28 具体比分
        try:
            od = float(r['odds'])
        except (TypeError, ValueError):
            continue
        if not (od > 1):
            continue
        by_match[mid][ts][sc] = od

    mkt = {}
    for mid, tsmap in by_match.items():
        if not tsmap:
            continue
        latest_ts = max(tsmap.keys())
        snap = tsmap[latest_ts]
        if len(snap) < 28:  # 28 具体比分不齐全，跳过
            continue
        raw = np.array([1.0 / snap.get(lb, np.nan) for lb in LABELS], dtype=float)
        if np.any(np.isnan(raw)) or raw.sum() <= 0:
            continue
        p = raw / raw.sum()
        nk = resolve_key(mid, valid) if valid is not None else norm_key(mid)
        if nk is None:
            continue
        mkt[nk] = p
    return mkt


def run_align(model, market, actual):
    """三方对齐（同 match_id）+ 计算指标。"""
    paired = []
    for mid in sorted(set(model) & set(market) & set(actual)):
        paired.append({
            'match_id': mid,
            'label': actual[mid]['label'],
            'league': actual[mid]['league'],
            'm': model[mid],
            'k': market[mid],
        })

    if not paired:
        return None, [], len(paired)

    y = np.array([p['label'] for p in paired], dtype=int)
    M = np.array([p['m'] for p in paired], dtype=float)
    K = np.array([p['k'] for p in paired], dtype=float)

    rps_m = compute_rps(y, M)
    rps_k = compute_rps(y, K)
    ll_m = compute_logloss(y, M)
    ll_k = compute_logloss(y, K)
    skill_rps = 1 - rps_m / rps_k if rps_k > 0 else float('nan')
    skill_ll = 1 - ll_m / ll_k if ll_k > 0 else float('nan')

    mdl_real = M[np.arange(len(y)), y]
    mkt_real = K[np.arange(len(y)), y]
    clv = mdl_real - mkt_real

    mdl_hit = float((M.argmax(axis=1) == y).mean())   # 精确比分命中（argmax）
    mkt_hit = float((K.argmax(axis=1) == y).mean())

    metrics = {
        'n': len(paired),
        'rps_model': rps_m, 'rps_market': rps_k, 'skill': skill_rps,
        'll_model': ll_m, 'll_market': ll_k, 'skill_ll': skill_ll,
        'mdl_hit': mdl_hit, 'mkt_hit': mkt_hit,
        'clv_mean': float(clv.mean()), 'clv_median': float(np.median(clv)),
        'clv_pos': float((clv > 0).mean()),
    }

    league_rows = []
    df = pd.DataFrame(paired)
    for lg, g in df.groupby('league'):
        if lg is None:
            continue
        yt = g['label'].to_numpy(int)
        Mp = np.array([x for x in g['m']], dtype=float)
        Kp = np.array([x for x in g['k']], dtype=float)
        rm = compute_rps(yt, Mp); rk = compute_rps(yt, Kp)
        cl = Mp[np.arange(len(yt)), yt] - Kp[np.arange(len(yt)), yt]
        league_rows.append({
            'league': lg, 'n': len(g),
            'RPS_model': round(rm, 4), 'RPS_market': round(rk, 4),
            'skill': round(1 - rm / rk, 4) if rk > 0 else None,
            'LL_model': round(compute_logloss(yt, Mp), 4),
            'LL_market': round(compute_logloss(yt, Kp), 4),
            'CLV_mean': round(float(cl.mean()), 4),
        })
    return metrics, league_rows, len(paired)


def main():
    print('=' * 78)
    print('市场基准扩展 T-006 比分 — 模型完整比分分布 vs 竞彩比分共识（28 具体条件分布）')
    print('=' * 78)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    print('\n1. 加载三方数据...')
    model = load_model_side(conn)
    actual = load_actual_side(conn)
    valid = build_valid_keys(conn)
    print(f'   模型侧（完整 36 格）：{len(model)} 场')
    print(f'   实际比分（∈28具体）：{len(actual)} 场')
    print(f'   matches 权威键（消歧用）：{len(valid)} 场')

    results = {}
    for align in ('t-24h', 'noon'):
        market = load_market_side(conn, align=align, valid=valid)
        m, lg_rows, n = run_align(model, market, actual)
        results[align] = (m, lg_rows, n)
        if m:
            print(f'\n--- {align} 口径（配对 n={m["n"]}）---')
            print(f'   RPS_model={m["rps_model"]:.4f}  RPS_market={m["rps_market"]:.4f}  skill={m["skill"]:+.4f}')
            print(f'   LogLoss_model={m["ll_model"]:.4f}  LogLoss_market={m["ll_market"]:.4f}  skill_ll={m["skill_ll"]:+.4f}')
            print(f'   精确比分命中 模型={m["mdl_hit"]:.3f}  市场={m["mkt_hit"]:.3f}')
            print(f'   CLV mean={m["clv_mean"]:+.4f}  median={m["clv_median"]:+.4f}  正值占比={m["clv_pos"]:.1%}')
        else:
            print(f'\n--- {align} 口径：无配对 ---')
    conn.close()

    print('\n2. 生成报告...')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    L = ['# 市场基准扩展 T-006 比分 — 模型完整比分分布 vs 竞彩比分共识', '',
         f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', '',
         f'- 模型侧锚点: `{MODEL_NAME}`、`is_replay=0`、`Score_grid_h_a`（6×6=36 格，h/a∈0..5）',
         f'- 市场侧锚点: `score_history` 竞彩比分盘（long 表）',
         f'- 标签: `matches.home_goals/away_goals` 实际比分，仅保留∈28具体比分',
         f'- 评价格式: **28 具体比分条件分布**（丢弃胜/平/负其它，双方对 28 具体比分重归一）',
         f'- 对齐键: 三表中文 match_id 同格式；队名经 normalize_team_name 归一（含莱切/莱斯特城消歧）',
         f'- 市场快照双口径: t-24h（timestamp 日期<比赛日）与 noon（<=比赛日 12:04）', '',
         '## 一、总体指标（28 向 RPS / LogLoss，越低越好）', '',
         '| 口径 | n | RPS_model | RPS_market | skill | LL_model | LL_market | skill_ll | 精确命中模型 | 精确命中市场 | CLV_mean | CLV正值% |',
         '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for align in ('t-24h', 'noon'):
        m, lg_rows, n = results[align]
        if not m:
            L.append(f'| {align} | 0 | - | - | - | - | - | - | - | - | - | - |')
            continue
        L.append(f'| {align} | {m["n"]} | {m["rps_model"]:.4f} | {m["rps_market"]:.4f} | {m["skill"]:+.4f} | '
                 f'{m["ll_model"]:.4f} | {m["ll_market"]:.4f} | {m["skill_ll"]:+.4f} | '
                 f'{m["mdl_hit"]:.3f} | {m["mkt_hit"]:.3f} | {m["clv_mean"]:+.4f} | {m["clv_pos"]:.1%} |')
    L += ['', '## 二、分联赛（各口径）', '']
    for align in ('t-24h', 'noon'):
        m, lg_rows, n = results[align]
        if not lg_rows:
            continue
        lg_df = pd.DataFrame(lg_rows).sort_values('n', ascending=False)
        L += [f'### {align} 口径', '', lg_df.to_markdown(index=False), '']
    L += ['## 三、口径与限制（重要）', '',
          '- 评价格式为 **28 具体比分条件分布**：竞彩比分市场原生含 28 具体 + 胜/平/负其它，',
          '  但模型网格封顶 5 球（catch-all 在模型侧截断失真），且仅 1/13 场次市场采集了 catch-all，',
          '  故双方统一丢弃 catch-all、对 28 具体比分重归一（条件分布 P(score|score∈具体)）。',
          '- RPS 对类别顺序敏感；本脚本按「负(客胜大比分→小)→平(大→小)→胜(小→大)」自然序排列，',
          '  双方共用同一顺序。LogLoss 为顺序无关稳健交叉验证。',
          '- 时间对齐：t-24h 最保守无泄漏，但市场相对模型约缺 16~28h 信息（偏保守于「模型输市场」）；',
          '  noon（<=比赛日 12:04）对齐午间批处理预测时刻更公平。',
          '- T-006 结论仅限比分分布，不回推 WDL/大小球/让球。', '']
    rep_path = os.path.join(REPORTS_DIR, f'market_benchmark_score_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'   报告已保存: {rep_path}')
    print('\n完成。')


if __name__ == '__main__':
    main()