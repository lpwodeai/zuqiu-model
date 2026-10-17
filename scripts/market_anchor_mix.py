#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""3a 市场锚定混合（C-20261004-007）— 模型 WDL 概率 vs 市场共识线的混合与分歧信任规则。

背景：market_benchmark_compass（C-005）已证明模型在同信息集下输给 500.com 共识线
约 0.020 RPS（RPS_skill=-0.129）。本脚本回答「模型输出与市场共识如何混合才最优」，
并显式化 V3 发现的「分歧区是模型亏钱区」结构（argmax 方向一致率 81.8%，分歧 14 场）。

三条设计修正（按用户裁决 V1-V4 后固化）：
  修正1（口径）：市场腿用「模型预测时刻的共识线」，默认 consensus@t-24h，而非 close。
     V1 已证该共识线 t-24h→close 几乎平坦（0.1919→0.1920），故 n=77 avg_live(≈close)
     为主扫描，n(t-24h) 严格 t-24h 腿做交叉校验，两者 w* 一致才下结论。
  修正2（防过拟合）：每个 w 的 RPS 给 bootstrap 95% CI；w* 前半段拟合/后半段检验
     （时间外推，同 TimeSeriesSplit 纪律）；报告 w=0/1/w*/0.5 四点。
  修正3（结构显式化）：加「分歧信任规则」面板（方向分歧规则 + δ 概率差规则扫描）。

标签序 / 概率列序（与 compass 一致）：0=客胜 1=平 2=主胜；向量列序 [客胜, 平, 主胜]。

输出：reports/market_anchor_mix_<ts>.md + 控制台。
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
ACTUAL_MAP = {
    '主胜': 2, '胜': 2, 'W': 2,
    '平局': 1, '平': 1, 'D': 1,
    '客胜': 0, '负': 0, 'L': 0,
}
N_BOOT = 2000
SEED = 42


def _norm(s):
    return ' '.join((str(s or '')).split()).strip().lower()


def compute_rps(y_true, y_proba):
    """RPS；y_true 0/1/2，y_proba 列序 [客胜,平,主胜]。"""
    y_true = np.asarray(y_true, dtype=int)
    y_proba = np.asarray(y_proba, dtype=float)
    n = len(y_true)
    o = np.zeros((n, 3))
    o[np.arange(n), y_true] = 1.0
    return float(np.mean(np.sum((np.cumsum(y_proba, axis=1) - np.cumsum(o, axis=1)) ** 2, axis=1) / 2))


def bootstrap_rps_ci(y_true, y_proba, n_iter=N_BOOT, seed=SEED):
    """重采样匹配对的 RPS 95% bootstrap CI，返回 (lo, mean, hi)。"""
    rng = np.random.default_rng(seed)
    n = len(y_true)
    idx = np.arange(n)
    vals = np.empty(n_iter)
    for i in range(n_iter):
        b = rng.choice(idx, size=n, replace=True)
        vals[i] = compute_rps(y_true[b], y_proba[b])
    return float(np.percentile(vals, 2.5)), float(vals.mean()), float(np.percentile(vals, 97.5))


def _blend(model_proba, market_proba, w):
    """final = w·market + (1-w)·model，均按 [客,平,主] 同列序。"""
    return w * market_proba + (1.0 - w) * model_proba


def load_model_side(conn):
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
            'm_home': home / s, 'm_draw': draw / s, 'm_away': away / s,
        })
    return out


def load_market_close(conn):
    """avg_live 收盘均赔 → 去水隐含概率，键 (date, home_en, away_en)。"""
    rows = conn.execute("""
        SELECT s.match_id, s.avg_live_win, s.avg_live_draw, s.avg_live_lose,
               m.match_date, m.home_team_en, m.away_team_en
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
        p_raw = np.array([1 / win, 1 / draw, 1 / lose])  # [主,平,客]
        p = p_raw / p_raw.sum()
        key = (str(r['match_date'])[:10], _norm(r['home_team_en']), _norm(r['away_team_en']))
        mkt[key] = {'k_home': float(p[0]), 'k_draw': float(p[1]), 'k_away': float(p[2])}
    return mkt


def load_market_t24h(conn):
    """consensus@t-24h 网格赔率 → 去水隐含概率，键 (date, home_en, away_en)。

    桥接链：odds500_match(home/away_en + home/away_cn + match_date) → 中文 match_id
    = f"{date}_{home_cn}_{away_cn}" → odds_grid_snapshot(grid_key='t-24h')。
    val1/val2/val3 = [主胜, 平, 客胜]（odds_grid.py labels_map）。
    """
    # en 键 → 中文 match_id
    en2cn = {}
    for r in conn.execute("""
        SELECT match_date, home_team_en, away_team_en, home_team_cn, away_team_cn
        FROM odds500_match
    """):
        key = (str(r['match_date'])[:10], _norm(r['home_team_en']), _norm(r['away_team_en']))
        en2cn[key] = f"{str(r['match_date'])[:10]}_{r['home_team_cn']}_{r['away_team_cn']}"

    # 中文 match_id → t-24h 赔率
    cn2odds = {}
    for r in conn.execute("""
        SELECT match_id, val1, val2, val3 FROM odds_grid_snapshot
        WHERE odds_type='wdl' AND grid_key='t-24h'
          AND val1 IS NOT NULL AND val2 IS NOT NULL AND val3 IS NOT NULL
    """):
        try:
            v1, v2, v3 = float(r['val1']), float(r['val2']), float(r['val3'])
        except (TypeError, ValueError):
            continue
        if not (v1 > 1 and v2 > 1 and v3 > 1):
            continue
        cn2odds[r['match_id']] = (v1, v2, v3)

    mkt = {}
    for key, cn_id in en2cn.items():
        odds = cn2odds.get(cn_id)
        if odds is None:
            continue
        p_raw = np.array([1 / odds[0], 1 / odds[1], 1 / odds[2]])  # [主,平,客]
        p = p_raw / p_raw.sum()
        mkt[key] = {'k_home': float(p[0]), 'k_draw': float(p[1]), 'k_away': float(p[2])}
    return mkt


def _argmax_agree(model_proba, market_proba):
    """方向一致掩码：argmax(model)==argmax(market)。"""
    return model_proba.argmax(axis=1) == market_proba.argmax(axis=1)


def direction_rule_probs(model_proba, market_proba):
    """方向分歧规则：argmax 一致用模型，分歧用市场。返回 (n,3) 概率。"""
    agree = _argmax_agree(model_proba, market_proba)[:, None]
    return np.where(agree, model_proba, market_proba)


def delta_rule_probs(model_proba, market_proba, delta):
    """δ 概率差规则：以市场 argmax 为焦点，|p_model−p_market| < δ 用模型，否则用市场。"""
    focal = market_proba.argmax(axis=1)
    d_model = model_proba[np.arange(len(model_proba)), focal]
    d_market = market_proba[np.arange(len(market_proba)), focal]
    use_model = (np.abs(d_model - d_market) < delta)[:, None]
    return np.where(use_model, model_proba, market_proba)


def _fmt(lo, mid, hi):
    return f"{mid:.4f} [{lo:.4f}, {hi:.4f}]"


def main():
    print('=' * 78)
    print('3a 市场锚定混合 — 模型 WDL vs 市场共识线（w 扫描 + 分歧信任规则）')
    print('=' * 78)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    print('\n[加载] 模型侧 / 市场 close / 市场 t-24h ...')
    model = load_model_side(conn)
    close = load_market_close(conn)
    t24h = load_market_t24h(conn)
    conn.close()

    n_close = sum(1 for rec in model if rec['key'] in close)
    n_t24h = sum(1 for rec in model if rec['key'] in t24h)
    print(f'  模型侧（非回放+有赛果）: {len(model)} 场')
    print(f'  配对 close  (n)      : {n_close} 场')
    print(f'  配对 t-24h  (n)      : {n_t24h} 场（严格 t-24h 交叉校验腿）')

    # 主面板：close 腿（n 最大）
    paired = [{**rec, **close[rec['key']]} for rec in model if rec['key'] in close]
    if not paired:
        print('  无 close 配对样本，退出。')
        return
    df = pd.DataFrame(paired).sort_values('match_date').reset_index(drop=True)
    y = df['actual'].to_numpy(int)
    P_model = df[['m_away', 'm_draw', 'm_home']].to_numpy(float)      # [客,平,主]
    P_mkt = df[['k_away', 'k_draw', 'k_home']].to_numpy(float)
    n = len(df)

    print(f'\n[主面板] close 腿 n={n}，w 扫描（w·市场 + (1-w)·模型，bootstrap CI）')
    w_grid = np.arange(0.0, 1.05, 0.05)
    w_rows = []
    for w in w_grid:
        Pb = _blend(P_model, P_mkt, w)
        lo, mid, hi = bootstrap_rps_ci(y, Pb)
        w_rows.append({'w': round(float(w), 2), 'rps': mid, 'lo': lo, 'hi': hi})
    w_df = pd.DataFrame(w_rows)
    best_idx = w_df['rps'].idxmin()
    w_star = float(w_df.loc[best_idx, 'w'])
    rps_wstar = float(w_df.loc[best_idx, 'rps'])

    rps_w0 = float(w_df.loc[w_df['w'] == 0.0, 'rps'].iloc[0])
    rps_w1 = float(w_df.loc[w_df['w'] == 1.0, 'rps'].iloc[0])
    rps_w05 = float(w_df.loc[w_df['w'] == 0.5, 'rps'].iloc[0])
    # 四点 CI
    def ci_at(w):
        row = w_df[w_df['w'] == w].iloc[0]
        return row['lo'], row['rps'], row['hi']

    print('   ' + ' | '.join(f"w={w:.2f}: {r:.4f}" for w, r in zip(w_df['w'], w_df['rps'])))
    print(f'   w* = {w_star:.2f}  (RPS={rps_wstar:.4f})')
    print(f'   四点: w=0 {rps_w0:.4f} | w=0.5 {rps_w05:.4f} | w* {rps_wstar:.4f} | w=1 {rps_w1:.4f}')

    # 时间外推：前半段拟合 w*，后半段检验
    print(f'\n[修正2] 时间外推（前半段拟合 w*，后半段检验）')
    half = n // 2
    y_fit, Pm_fit, Pmk_fit = y[:half], P_model[:half], P_mkt[:half]
    y_ho, Pm_ho, Pmk_ho = y[half:], P_model[half:], P_mkt[half:]
    fit_rps = {}
    for w in w_grid:
        fit_rps[float(w)] = compute_rps(y_fit, _blend(Pm_fit, Pmk_fit, w))
    w_star_fit = min(fit_rps, key=fit_rps.get)
    rps_ho_model = compute_rps(y_ho, Pm_ho)
    rps_ho_mkt = compute_rps(y_ho, Pmk_ho)
    rps_ho_wstar = compute_rps(y_ho, _blend(Pm_ho, Pmk_ho, w_star_fit))
    rps_ho_w05 = compute_rps(y_ho, _blend(Pm_ho, Pmk_ho, 0.5))
    print(f'   前半段拟合 w*_fit = {w_star_fit:.2f}')
    print(f'   后半段 RPS: 模型(w=0)={rps_ho_model:.4f} | 混合(w*_fit)={rps_ho_wstar:.4f} | 0.5={rps_ho_w05:.4f} | 市场(w=1)={rps_ho_mkt:.4f}')

    # 交叉校验：t-24h 腿 n_t24h 上的 w* 是否一致
    print(f'\n[修正1] t-24h 交叉校验（n={n_t24h}）')
    t_paired = [{**rec, **t24h[rec['key']]} for rec in model if rec['key'] in t24h]
    consistency = None
    if len(t_paired) >= 15:
        td = pd.DataFrame(t_paired).sort_values('match_date')
        ty = td['actual'].to_numpy(int)
        tPm = td[['m_away', 'm_draw', 'm_home']].to_numpy(float)
        tPk = td[['k_away', 'k_draw', 'k_home']].to_numpy(float)
        t_rps = {}
        for w in w_grid:
            t_rps[float(w)] = compute_rps(ty, _blend(tPm, tPk, w))
        t_wstar = min(t_rps, key=t_rps.get)
        t_rps_w0 = t_rps[0.0]
        t_rps_w1 = t_rps[1.0]
        print(f'   t-24h 腿 w* = {t_wstar:.2f} (RPS w0={t_rps_w0:.4f}, w*={t_rps[t_wstar]:.4f}, w1={t_rps_w1:.4f})')
        consistency = f"close w*={w_star:.2f} vs t-24h w*={t_wstar:.2f} 一致" if abs(w_star - t_wstar) < 0.15 else f"不一致（close {w_star:.2f} / t-24h {t_wstar:.2f}）"
        print(f'   -> {consistency}')
    else:
        print(f'   t-24h 配对不足 15 场，跳过交叉校验。')

    # 分歧信任规则面板
    print(f'\n[修正3] 分歧信任规则面板（close 腿 n={n}）')
    agree_rate = float(_argmax_agree(P_model, P_mkt).mean())
    P_dir = direction_rule_probs(P_model, P_mkt)
    rps_dir = compute_rps(y, P_dir)
    n_disagree = int((~_argmax_agree(P_model, P_mkt)).sum())
    print(f'   argmax 方向一致率 = {agree_rate:.1%}（分歧 {n_disagree} 场）')
    print(f'   方向规则 RPS = {rps_dir:.4f}（< w0={rps_w0:.4f} 才算跑赢纯模型）')

    # δ 扫描
    d_grid = np.arange(0.0, 0.31, 0.02)
    d_rows = []
    for d in d_grid:
        Pd = delta_rule_probs(P_model, P_mkt, float(d))
        d_rows.append({'delta': round(float(d), 2), 'rps': compute_rps(y, Pd)})
    d_df = pd.DataFrame(d_rows)
    d_best = d_df.loc[d_df['rps'].idxmin()]
    print('   δ 扫描: ' + ' | '.join(f"δ={r['delta']:.2f}:{r['rps']:.4f}" for _, r in d_df.iterrows()))
    print(f'   δ* = {d_best["delta"]:.2f} (RPS={d_best["rps"]:.4f})')

    # argmax 命中率作为辅助（面板非主指标）
    mdl_hit = float((P_model.argmax(axis=1) == y).mean())
    mkt_hit = float((P_mkt.argmax(axis=1) == y).mean())
    dir_hit = float((P_dir.argmax(axis=1) == y).mean())

    # ------- 报告 -------
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    L = []
    L.append('# 3a 市场锚定混合 — 模型 WDL vs 市场共识线（含三修正）')
    L.append('')
    L.append(f'**生成时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    L.append('')
    L.append(f'- 模型侧锚点: `{MODEL_NAME}`、`is_replay=0`')
    L.append(f'- 市场腿主扫描: `odds500_ouzhi_summary.avg_live`（收盘≈t-24h，V1 已证平坦）')
    L.append(f'- 市场腿交叉校验: `odds_grid_snapshot.grid_key="t-24h"`（严格同信息集）')
    L.append(f'- 桥接: model → odds500_match(en) → odds_grid_snapshot(中文)')
    L.append('- 概率列序 [客胜, 平, 主胜]；RPS 越低越好')
    L.append('')
    L.append(f'## 一、总体与四点（close 腿 n={n}）')
    L.append('')
    L.append('| 锚点 | w | RPS (bootstrap 95% CI) |')
    L.append('| --- | --- | --- |')
    for w in [0.0, 0.5, w_star, 1.0]:
        lo, mid, hi = ci_at(w)
        L.append(f'| {"纯模型" if w==0 else ("朴素对半" if w==0.5 else ("最优w*" if abs(w-w_star)<1e-9 else "纯市场"))} | {w:.2f} | {_fmt(lo,mid,hi)} |')
    L.append('')
    L.append(f'- **最优混合权重 w = {w_star:.2f}**（全样本最小 RPS 点）')
    L.append(f'- argmax 命中率: 模型 {mdl_hit:.1%} / 市场 {mkt_hit:.1%}')
    L.append('')
    L.append('## 二、w 扫描曲线（bootstrap 95% CI）')
    L.append('')
    L.append('| w | RPS | 95% CI |')
    L.append('| --- | --- | --- |')
    for _, r in w_df.iterrows():
        L.append(f'| {r["w"]:.2f} | {r["rps"]:.4f} | [{r["lo"]:.4f}, {r["hi"]:.4f}] |')
    L.append('')
    L.append('## 三、时间外推（前半段拟合 / 后半段检验）')
    L.append('')
    L.append(f'- 前半段拟合 w*_fit = {w_star_fit:.2f}（n={half}）')
    L.append(f'- 后半段 RPS: 模型={rps_ho_model:.4f} | 混合(w*_fit)={rps_ho_wstar:.4f} | 0.5={rps_ho_w05:.4f} | 市场={rps_ho_mkt:.4f}（n={n-half}）')
    L.append('')
    L.append('## 四、t-24h 交叉校验（修正1）')
    L.append('')
    if consistency:
        L.append(f'- {consistency}')
    else:
        L.append('- t-24h 配对不足 15 场，未校验。')
    L.append('')
    L.append('## 五、分歧信任规则面板（修正3）')
    L.append('')
    L.append(f'- argmax 方向一致率 = {agree_rate:.1%}（分歧 {n_disagree} 场）')
    L.append(f'- 方向分歧规则 RPS = {rps_dir:.4f}（argmax 一致用模型/分歧用市场）')
    L.append(f'- δ* = {d_best["delta"]:.2f} 概率差规则 RPS = {d_best["rps"]:.4f}')
    L.append('')
    L.append('| δ | RPS |')
    L.append('| --- | --- |')
    for _, r in d_df.iterrows():
        L.append(f'| {r["delta"]:.2f} | {r["rps"]:.4f} |')
    L.append('')
    L.append('## 六、解读预案落位')
    L.append('')
    # 自动判定
    if rps_wstar >= rps_w1 - 1e-4 and w_star > 0.9:
        verdict = ('**w=1 单调升到纯市场最优**：模型在 WDL 上零边际价值，输出≈市场+噪声。'
                   '架构结论成立 —— WDL 主线改为「市场复制+校准」，模型精力转向 3d 竞彩错价泳道 + 比分/TG/让球任务。')
    elif rps_wstar < min(rps_w0, rps_w1) - 1e-4 and 0.5 < w_star < 0.8:
        verdict = ('**混合优于两端（w∈(0.5,0.8)）**：模型含互补信号但被噪声拖累。'
                   '混合是正确架构方向，接 3b 特征瘦身（提纯互补信号）。')
    elif rps_dir < rps_w1 - 1e-4:
        verdict = ('**分歧信任规则显著优于纯市场**：模型价值集中在「与市场一致区的微调」。'
                   '规则入 serving 候选，走 shadow + 三闸门。')
    else:
        verdict = ('**三条都输给纯市场**：模型 WDL 独立价值≈0。'
                   '架构结论成立，且 P0-E 实盘 300 注建议提前收束。')
    L.append(f'- {verdict}')
    L.append('- 范围提醒：以上仅测 WDL，不外推 T-004 总进球 / T-005 让球 / T-006 比分。')
    L.append('')

    rep_path = os.path.join(REPORTS_DIR, f'market_anchor_mix_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))

    print(f'\n[报告] 已保存: {rep_path}')
    print('\n================ 解读预案落位 ================')
    print(verdict)
    print('==============================================')
    print('\n完成。')


if __name__ == '__main__':
    main()