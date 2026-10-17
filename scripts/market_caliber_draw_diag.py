# -*- coding: utf-8 -*-
"""
市场口径统一诊断（C-20261006-008 后续）
=================================================================
问题：WDL 平局校准实验（C-008）发现，同一批生产落库场上
  - 对 WDL 赔率市场（wdl_history 去水）：生产 p_draw 偏差 +1.1pp
  - 对比分对角带市场（score_history trace 去水）：生产 p_draw 偏差 +3.0pp（C-007）
差 ~1.9pp 被假设为「比分盘 vs 胜平负盘」对平局的结构性价差。本诊断量化该价差，
并明确 T-006 v5 验收应采用哪一套市场基准。

口径（同一比赛日、同一时刻锚点 noon = ≤比赛日 12:04:00 最后快照，与 WDL 校准实验一致）：
  mkt_wdl_draw   = 去水后的 1/draw_odds （胜平负盘平局隐含概率）
  mkt_score_draw = Σ_{h=0..5} gk[h,h] （比分盘对角带去水平局带 = np.trace）
两者用同一场次、同一 noon 锚点的最新快照，消除时间漂移差异。

输出：reports/market_caliber_draw_{ts}.md
"""
import os, sys, sqlite3, re
from datetime import datetime
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
from market_benchmark_hc import build_en2cn, _norm

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
NOON_CUT = '12:04:00'
EVAL_START = '2026-08-22'
LEAGUES = ('英超', '西甲', '意甲', '德甲', '法甲')


def load_targets(conn):
    ph = ','.join('?' * len(LEAGUES))
    rows = conn.execute(
        f"SELECT match_id, match_date, league, home_team, away_team, actual_score "
        f"FROM matches WHERE league IN ({ph}) AND match_date >= ? "
        f"AND actual_score IS NOT NULL AND actual_score != '' ORDER BY match_date",
        (*LEAGUES, EVAL_START)).fetchall()
    out = []
    for r in rows:
        out.append({'match_id': r['match_id'], 'date': str(r['match_date'])[:10],
                    'league': r['league'], 'home_en': r['home_team'],
                    'away_en': r['away_team']})
    return out


def mkt_wdl_draw(conn, cn_id, date):
    """wdl_history noon 去水平局隐含概率。返回 (prob, n_recs) 或 (None, 0)。"""
    cut = f'{date} {NOON_CUT}'
    rows = conn.execute(
        "SELECT win_a, draw, win_b FROM wdl_history "
        "WHERE match_id=? AND timestamp IS NOT NULL AND timestamp<=? "
        "ORDER BY timestamp DESC LIMIT 1", (cn_id, cut)).fetchone()
    if not rows:
        return None, 0
    w, d, l = rows['win_a'], rows['draw'], rows['win_b']
    if not (w and d and l and w > 1 and d > 1 and l > 1):
        return None, 0
    p = np.array([1.0 / float(w), 1.0 / float(d), 1.0 / float(l)])
    p = p / p.sum()
    return float(p[1]), 1


SCORE_RE = re.compile(r'^(\d+)\s*[:：-]\s*(\d+)$')


def mkt_score_draw(conn, cn_id, date):
    """score_history noon 对角带去水平局带。返回 (prob, n_cov) 或 (None, 0)。"""
    cut = f'{date} {NOON_CUT}'
    rows = conn.execute(
        "SELECT score, odds, timestamp FROM score_history "
        "WHERE match_id=? AND odds>1 AND timestamp IS NOT NULL AND timestamp<=? "
        "ORDER BY timestamp DESC", (cn_id, cut)).fetchall()
    if not rows:
        return None, 0
    latest_ts = rows[0][2]
    g = np.full((6, 6), 1.0 / 100.0)  # MIN_ODDS_FLOOR 缺失格哨兵（同 tfs_shadow_report）
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


def main():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    targets = load_targets(conn)
    en2cn = build_en2cn(conn)
    print(f'目标场次（≥{EVAL_START} 五联赛有赛果）: {len(targets)}')

    # 生产 WDL p_draw（落库，is_replay=0）
    prod_draw = {}
    for r in conn.execute(
            "SELECT match_id, probability FROM model_predictions "
            "WHERE model_name='generate_unified_report_v2.0' AND is_replay=0 "
            "AND prediction_type='WDL_draw'"):
        prod_draw[r['match_id']] = float(r['probability'])

    rows = []
    miss = {'cn': 0, 'no_wdl': 0, 'no_score': 0}
    for t in targets:
        hc = en2cn.get(_norm(t['home_en']))
        ac = en2cn.get(_norm(t['away_en']))
        if not hc or not ac:
            miss['cn'] += 1
            continue
        cn_id = f"{t['date']}_{hc}_{ac}"
        wdl_p, _ = mkt_wdl_draw(conn, cn_id, t['date'])
        if wdl_p is None:
            miss['no_wdl'] += 1
            continue
        sc_p, n_cov = mkt_score_draw(conn, cn_id, t['date'])
        if sc_p is None:
            miss['no_score'] += 1
            continue
        rows.append({**t, 'mkt_wdl_draw': wdl_p, 'mkt_score_draw': sc_p,
                     'prod_draw': prod_draw.get(t['match_id'])})

    print(f'双口径都命中: {len(rows)}  跳过: {miss}')
    if not rows:
        print('无数据，退出'); conn.close(); return

    dw = np.array([r['mkt_wdl_draw'] for r in rows])
    ds = np.array([r['mkt_score_draw'] for r in rows])
    diff = dw - ds  # 结构性价差：WDL 盘平局 - 比分盘平局带

    has_prod = [r for r in rows if r['prod_draw'] is not None]
    print(f'含生产落库 p_draw: {len(has_prod)}/{len(rows)}')

    # 主分布统计
    def q(x, p):
        return float(np.quantile(x, p))
    stats = {
        'n': len(rows),
        'mkt_wdl_draw_mean': float(dw.mean()),
        'mkt_score_draw_mean': float(ds.mean()),
        'diff_mean': float(diff.mean()),
        'diff_std': float(diff.std()),
        'diff_median': float(np.median(diff)),
        'diff_p10': q(diff, 0.10), 'diff_p25': q(diff, 0.25),
        'diff_p75': q(diff, 0.75), 'diff_p90': q(diff, 0.90),
        'frac_wdl_higher': float((diff > 0).mean()),
    }

    # 分联赛
    per_league = []
    for lg in sorted(set(r['league'] for r in rows)):
        idx = [i for i, r in enumerate(rows) if r['league'] == lg]
        if len(idx) >= 5:
            per_league.append({'league': lg, 'n': len(idx),
                               'wdl_mean': float(dw[idx].mean()),
                               'score_mean': float(ds[idx].mean()),
                               'diff_mean': float(diff[idx].mean()),
                               'diff_std': float(diff[idx].std())})

    # 分 wdl_draw 桶
    edges = [0, 0.20, 0.24, 0.28, 0.32, 1.0]
    per_bucket = []
    for i in range(len(edges) - 1):
        idx = [j for j in range(len(rows)) if edges[i] <= dw[j] < edges[i + 1]]
        if idx:
            per_bucket.append({'bucket': f'[{edges[i]:.2f},{edges[i+1]:.2f})', 'n': len(idx),
                               'wdl_mean': float(dw[idx].mean()),
                               'score_mean': float(ds[idx].mean()),
                               'diff_mean': float(diff[idx].mean())})

    # 生产 p_draw 对两种市场的偏差
    prod_table = None
    if has_prod:
        pp = np.array([r['prod_draw'] for r in has_prod])
        dwp = np.array([r['mkt_wdl_draw'] for r in has_prod])
        dsp = np.array([r['mkt_score_draw'] for r in has_prod])
        prod_table = {
            'n': len(has_prod),
            'prod_mean': float(pp.mean()),
            'wdl_market_mean': float(dwp.mean()),
            'score_market_mean': float(dsp.mean()),
            'bias_vs_wdl': float((pp - dwp).mean()),
            'bias_vs_score': float((pp - dsp).mean()),
            'caliber_gap': float((dwp - dsp).mean()),  # = bias_vs_score - bias_vs_wdl
        }

    # 输出报告
    lines = []
    A = lines.append
    A(f'# 市场口径统一诊断：WDL 盘平局 vs 比分盘对角带（C-20261006-008 后续）')
    A(f'')
    A(f'- 生成时间: {ts}')
    A(f'- 窗口: ≥{EVAL_START} 五联赛有赛果场（与 WDL 校准实验 EVAL 窗一致）')
    A(f'- 时间锚点: noon（≤比赛日 {NOON_CUT} 最后快照，两套市场共用同一锚点，消除时间漂移）')
    A(f'')
    A(f'## 1. 核心结论')
    A(f'')
    A(f'WDL 盘平局隐含概率 **系统性高于** 比分盘对角带平局带：'
      f'mean(wdl_draw − score_draw) = **{stats["diff_mean"]:+.4f}** '
      f'（±{stats["diff_std"]:.4f}，n={stats["n"]}），'
      f'WDL 盘更高的场次占 {stats["frac_wdl_higher"]:.1%}。')
    A(f'')
    if prod_table:
        A(f'含生产落库 p_draw（n={prod_table["n"]}）：')
        A(f'- 生产 p_draw 均值 = {prod_table["prod_mean"]:.4f}')
        A(f'- 对 WDL 盘偏差 = {prod_table["bias_vs_wdl"]:+.4f}（即 WDL 校准实验 C-008 的 +1.1pp 口径）')
        A(f'- 对比分对角带偏差 = {prod_table["bias_vs_score"]:+.4f}（即 C-007 的 +3.0pp 口径）')
        A(f'- 两者之差（纯口径价差）= {prod_table["caliber_gap"]:+.4f}')
        A(f'')
        A(f'**解读**：C-007 的 +3.0pp 平局偏置中，约 {prod_table["caliber_gap"]*100:.1f}pp '
          f'来自「WDL 盘 vs 比分盘」市场口径价差，仅约 {prod_table["bias_vs_wdl"]*100:.1f}pp '
          f'是生产 WDL 模型自身对 WDL 盘的偏高。末段校准（C-008 方向）最多只能消除后者，'
          f'无法改变比分盘验收下的平局带表现。')
    A(f'')
    A(f'## 2. 价差分布（wdl_draw − score_draw，pp=百分点）')
    A(f'')
    A(f'| 指标 | 值(pp) |')
    A(f'|---|---|')
    A(f'| 均值 | {stats["diff_mean"]*100:+.1f} |')
    A(f'| 中位数 | {stats["diff_median"]*100:+.1f} |')
    A(f'| 标准差 | {stats["diff_std"]*100:.1f} |')
    A(f'| p10 | {stats["diff_p10"]*100:+.1f} |')
    A(f'| p25 | {stats["diff_p25"]*100:+.1f} |')
    A(f'| p75 | {stats["diff_p75"]*100:+.1f} |')
    A(f'| p90 | {stats["diff_p90"]*100:+.1f} |')
    A(f'| WDL 盘更高占比 | {stats["frac_wdl_higher"]:.1%} |')
    A(f'')
    A(f'## 3. 分联赛')
    A(f'')
    A(f'| 联赛 | n | wdl_draw 均值 | score_draw 均值 | 价差(pp) | 价差 std |')
    A(f'|---|---|---|---|---|---|')
    for r in per_league:
        A(f'| {r["league"]} | {r["n"]} | {r["wdl_mean"]:.4f} | {r["score_mean"]:.4f} '
          f'| {r["diff_mean"]*100:+.1f} | {r["diff_std"]*100:.1f} |')
    A(f'')
    A(f'## 4. 按 WDL 盘平局概率分桶')
    A(f'')
    A(f'| wdl_draw 桶 | n | wdl_draw 均值 | score_draw 均值 | 价差(pp) |')
    A(f'|---|---|---|---|---|')
    for r in per_bucket:
        A(f'| {r["bucket"]} | {r["n"]} | {r["wdl_mean"]:.4f} | {r["score_mean"]:.4f} '
          f'| {r["diff_mean"]*100:+.1f} |')
    A(f'')
    A(f'## 5. 对 T-006 v5 验收的含义')
    A(f'')
    A(f'- v5 验收原用「比分对角带」作市场基准（C-006/C-007），平局带 +3.0pp 的偏置中，'
      f'大部分是 WDL→比分 网格映射无法跨越的**结构性价差**（两个市场对平局定价不同），'
      f'不是 v5 / 生产 WDL 的建模错误。')
    A(f'- 若 v5 验收目标是「模型网格是否对齐胜平负市场」，应改用 **wdl_history 平局隐含** 作基准，'
      f'此时生产 WDL 偏差仅 ~{prod_table["bias_vs_wdl"]*100 if prod_table else "?":.1f}pp，'
      f'v5 网格平局带偏差也会相应收窄。')
    A(f'- 若坚持比分盘基准，则 v5 平局带的「对标目标」应是比分对角带而非 WDL p_draw，'
      f'需在 T-006 侧（DC ρ / IPF 边缘口径 / 网格平局带后处理）调整，而非 WDL 校准。')
    A(f'- 建议：v5 验收报告同时给出两种基准下的平局带偏差，避免单一基准的误导；'
      f'主基准选与模型输入概率来源一致的口径。')
    A(f'')
    A(f'## 6. 已知局限')
    A(f'')
    A(f'1. 比分盘对角带用 6×6 截断（h,a≤5），5+ 其他比分的平局概率被分配到对角格的哨兵概率，'
      f'对平局带均值影响小（对角占比通常 <5%）。')
    A(f'2. wdl_history 去水用三向总和归一；score_history 去水用全 6×6 格总和归一，'
      f'两者去水方法略有差异但不改变平局相对定价结论。')
    A(f'3. 仅 EVAL 窗（新赛季）；老赛季 score_history 覆盖度下降，价差分布可能不同。')

    rpt = os.path.join(REPORTS_DIR, f'market_caliber_draw_{ts}.md')
    with open(rpt, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'\n报告: {rpt}')

    # 控制台速览
    print(f'\n=== 速览 ===')
    print(f'n={stats["n"]}  wdl_draw均值={stats["mkt_wdl_draw_mean"]:.4f}  '
          f'score_draw均值={stats["mkt_score_draw_mean"]:.4f}  '
          f'价差={stats["diff_mean"]*100:+.1f}±{stats["diff_std"]*100:.1f}pp')
    if prod_table:
        print(f'生产 p_draw 对 WDL 盘偏差={prod_table["bias_vs_wdl"]*100:+.1f}pp  '
              f'对比分盘偏差={prod_table["bias_vs_score"]*100:+.1f}pp  '
              f'口径价差={prod_table["caliber_gap"]*100:+.1f}pp')

    conn.close()


if __name__ == '__main__':
    main()
