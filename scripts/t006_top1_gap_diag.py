# -*- coding: utf-8 -*-
"""
t006_top1_gap_diag.py — T-006 v5 top1 差距分解诊断（检查阶段，只读，C-20261006-015）
====================================================================================
纪律（用户冻结）：
  - 诊断线非修复线：只分解 8.5pp top1 差距来源，不产出修复方案、不碰任何参数
  - 沿用 C-010 验收同窗口（2026-08-22~09-21 五联赛）与锚点（t-24h / noon）
  - EVAL-peek 红线适用：本脚本无任何选型逻辑
  - 复用 C-010/市场基准实现：mkt_score_grid_noon/mkt_wdl_draw（import），
    本地 L0 网格构造器以「trace 逐场等价自检」对齐 C-010 口径，不一致即中止

四问分解链（同 n 交集上望远镜归因，各层 hit 差 = 该层损失）：
  L0 市场比分网格（score_history 最新快照 28 格去水，floor 补齐，信息天花板）
  L1 纯市场 λ 朴素泊松（wdl_history 去水 → solve_lambda_core，total 锁 L2 的 λh+λa）
  L2 v5 落库 λ 朴素泊松（t006_score_predictor_v5 Lambda_home/away）
  L3 v5 网格落库（Score_grid_h_a 36 格）

  Q② 归因:  hit(L2)-hit(L3)=形状损失(DC+IPF) | hit(L1)-hit(L2)=λ链损失(模型注入+时点)
            hit(L0)-hit(L1)=市场比分盘信息溢价（不可控）
  Q① 峰态:  各层 max/top5质量/熵 + L3/L0 top1 置信度-命中率分桶（过集中 vs 过分散）
  Q③ 基准:  L0/L1/L2/L3 四档 top1 命中并列
  Q④ 分布:  分联赛/分月/分比分族（主胜/平/客胜族）top1 差距

分支预冻结（用户裁定）：
  可控分量 = hit(L1) - hit(L3) ≥ 4pp → 开修复实验线（预注册协议方法论）
  可控分量 < 4pp → 不硬修，转价值重定位讨论
  注：链式归因非严格可加（路径依赖），4pp 判读为上限估计。

输出：reports/t006_top1_gap_diag_<ts>.md + 控制台。全程只读 SELECT。
"""
import os, sys, re, math, sqlite3
from datetime import datetime
from collections import defaultdict
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

from t006_v5_acceptance import mkt_score_grid_noon, mkt_wdl_draw, SCORE_RE, NOON_CUT

# 复测窗口参数化（C-20261006-016）：默认沿用 C-010/C-015 窗口，月度 live 复测可经环境变量覆盖
WIN_START = os.environ.get('TOP1_DIAG_WIN_START', '2026-08-22')
WIN_END = os.environ.get('TOP1_DIAG_WIN_END', '2026-09-21')
from tfs_shadow_report import MIN_ODDS_FLOOR, canon_key, load_name_map
from market_benchmark_hc import build_en2cn, _norm
from t006_score_predictor_v5 import solve_lambda_core

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
LEAGUES = ('英超', '西甲', '意甲', '德甲', '法甲')
GRID_MODEL = 't006_score_predictor_v5'
PROD_MODEL = 'generate_unified_report_v2.0'


# ---------- 数据加载 ----------

def load_window_matches(conn):
    """窗口内五联赛 + actual_score 可解析且双方 ≤5 球的场次。"""
    ph = ','.join('?' * len(LEAGUES))
    out = []
    for r in conn.execute(
            f"SELECT match_id, match_date, league, home_team, away_team, actual_score FROM matches "
            f"WHERE league IN ({ph}) AND match_date >= ? AND match_date <= ? AND actual_score IS NOT NULL",
            (*LEAGUES, WIN_START, WIN_END)):
        m = SCORE_RE.match(str(r['actual_score']).strip())
        if not m:
            continue
        h, a = int(m.group(1)), int(m.group(2))
        if h > 5 or a > 5:
            continue
        out.append({'match_id': r['match_id'], 'date': str(r['match_date'])[:10],
                    'league': r['league'], 'home_en': r['home_team'], 'away_en': r['away_team'],
                    'actual': (h, a)})
    return out


def pick_v5_snapshot(by_mid_ts, date, anchor):
    """从 {ts: {ptype: prob}} 里选锚点快照并装配。

    实况核查（2026-10-06）：窗口内 v5 网格 100% 为 C-005 回填产物（ts≥2026-09-09，
    每场单快照），满足赛前截止线的快照为 0 → v5 侧一律取「最新可用快照」，
    锚点纪律仅约束市场侧 L0/L1。此口径与 C-010 验收同源，B0 前视 caveat 继承。
    """
    if anchor == 'latest':
        ok = list(by_mid_ts.keys())
    elif anchor == 'noon':
        cut = f'{date} {NOON_CUT}'
        ok = [ts for ts in by_mid_ts if ts <= cut]
    else:
        ok = [ts for ts in by_mid_ts if ts < f'{date} 00:00:00']
    if not ok:
        return None
    ts = max(ok)
    d = by_mid_ts[ts]
    grid = np.full((6, 6), np.nan)
    for h in range(6):
        for a in range(6):
            v = d.get(f'Score_grid_{h}_{a}')
            if v is not None:
                grid[h, a] = v
    if np.isnan(grid).any():
        return None
    s = grid.sum()
    if s <= 0:
        return None
    lam_h, lam_a = d.get('Lambda_home'), d.get('Lambda_away')
    if not lam_h or not lam_a:
        return None
    return {'ts': ts, 'grid': grid / s, 'lam': (lam_h, lam_a), 'top1_prob': d.get('Score_top1')}


# ---------- 网格构造 ----------

def mkt_score_grid_full(conn, cn_id, date, anchor):
    """L0：score_history 锚点快照 → 6×6 去水网格（与 t006_v5_acceptance.mkt_score_grid_noon
    同构：floor 补齐未列格 + n_cov<10 拒绝；noon 锚经 trace 逐场等价自检）。"""
    if anchor == 'noon':
        cut, op = f'{date} {NOON_CUT}', '<='
    else:
        cut, op = f'{date} 00:00:00', '<'
    rows = conn.execute(
        f"SELECT score, odds, timestamp FROM score_history "
        f"WHERE match_id=? AND odds>1 AND timestamp IS NOT NULL AND timestamp{op}? "
        f"ORDER BY timestamp DESC", (cn_id, cut)).fetchall()
    if not rows:
        return None
    latest_ts = rows[0]['timestamp']
    g = np.full((6, 6), 1.0 / MIN_ODDS_FLOOR)
    n_cov = 0
    for r in rows:
        if r['timestamp'] != latest_ts:
            continue
        m = SCORE_RE.match(str(r['score']).strip())
        if not m:
            continue
        h, a = int(m.group(1)), int(m.group(2))
        if h <= 5 and a <= 5:
            g[h, a] = 1.0 / float(r['odds'])
            n_cov += 1
    if n_cov < 10:
        return None
    g = g / g.sum()
    return {'grid': g, 'ts': latest_ts, 'n_cov': n_cov}


def poisson_grid(lam_h, lam_a):
    """朴素独立泊松 6×6（截断重归一）。"""
    k = np.arange(6)
    ph = np.exp(-lam_h) * lam_h ** k / np.array([math.factorial(i) for i in range(6)])
    pa = np.exp(-lam_a) * lam_a ** k / np.array([math.factorial(i) for i in range(6)])
    g = np.outer(ph, pa)
    return g / g.sum()


# ---------- 指标 ----------

def grid_metrics(g, actual):
    h, a = actual
    idx = int(np.argmax(g))
    top1 = divmod(idx, 6)
    flat = g.flatten()
    order = np.argsort(flat)[::-1]
    top5 = [(divmod(int(i), 6), float(flat[i])) for i in order[:5]]
    entropy = float(-(g * np.log(np.clip(g, 1e-12, None))).sum())
    return {
        'top1_cell': top1,
        'top1_prob': float(g.max()),
        'top1_hit': 1.0 if (top1[0] == h and top1[1] == a) else 0.0,
        'top5_hit': 1.0 if (h, a) in [c for c, _ in top5] else 0.0,
        'max': float(g.max()), 'top5_mass': float(np.sort(flat)[::-1][:5].sum()),
        'entropy': entropy,
    }


def fmt_pct(x):
    return f'{x*100:.1f}%'


def main():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=5000')

    print('=' * 78)
    print(f'T-006 v5 top1 差距分解诊断（检查阶段，只读）  窗口 {WIN_START}~{WIN_END} 双锚')
    print('=' * 78)

    matches = load_window_matches(conn)
    print(f'\n0. 窗口场次（五联赛+actual≤5:5）: {len(matches)}')
    en2cn = build_en2cn(conn)

    # v5 侧按锚点装配（v5 英文 match_id 与 matches 主键队名不规范，经 canon_key 归一桥接）
    name_map = load_name_map(conn)
    v5_raw = defaultdict(lambda: defaultdict(dict))
    v5_canon = {}
    for r in conn.execute(
            f"SELECT match_id, prediction_type, probability, timestamp FROM model_predictions "
            f"WHERE model_name=? AND is_replay=0 "
            f"AND (prediction_type LIKE 'Score_grid_%' OR prediction_type IN ('Lambda_home','Lambda_away','Score_top1'))",
            (GRID_MODEL,)):
        ck = v5_canon.get(r['match_id'], '__UNSET__')
        if ck == '__UNSET__':
            ck = canon_key(r['match_id'], name_map)
            v5_canon[r['match_id']] = ck
        if ck is None:
            continue
        v5_raw[ck][r['timestamp']][r['prediction_type']] = float(r['probability'])

    funnel = defaultdict(int)
    records = []
    chk_fail = 0
    for m in matches:
        ck = canon_key(m['match_id'], name_map)
        hc, ac = en2cn.get(_norm(m['home_en'])), en2cn.get(_norm(m['away_en']))
        cn_id = f"{m['date']}_{hc}_{ac}" if (hc and ac) else None
        v5_s = pick_v5_snapshot(v5_raw.get(ck, {}), m['date'], 'latest')
        for anchor in ('noon', 't24'):
            v5s = v5_s
            if v5s is None:
                funnel[f'{anchor}:缺v5网格/λ'] += 1
                continue
            L0 = mkt_score_grid_full(conn, cn_id, m['date'], anchor) if cn_id else None
            if anchor == 'noon' and cn_id:
                trace_c010, _ = mkt_score_grid_noon(conn, cn_id, m['date'])
                if (L0 is None) != (trace_c010 is None):
                    chk_fail += 1
                elif L0 is not None and abs(float(np.trace(L0['grid'])) - trace_c010) > 1e-9:
                    chk_fail += 1
            if L0 is None:
                funnel[f'{anchor}:缺市场比分快照'] += 1
                continue
            lam_h, lam_a = v5s['lam']
            total = lam_h + lam_a
            p_full = None
            if cn_id:
                cut, op = (f"{m['date']} {NOON_CUT}", '<=') if anchor == 'noon' else (f"{m['date']} 00:00:00", '<')
                row = conn.execute(
                    f"SELECT win_a, draw, win_b FROM wdl_history WHERE match_id=? AND timestamp IS NOT NULL "
                    f"AND timestamp{op}? ORDER BY timestamp DESC LIMIT 1", (cn_id, cut)).fetchone()
                if row and row['win_a'] and row['draw'] and row['win_b'] \
                        and row['win_a'] > 1 and row['draw'] > 1 and row['win_b'] > 1:
                    v = np.array([1.0 / row['win_a'], 1.0 / row['draw'], 1.0 / row['win_b']])
                    p_full = v / v.sum()   # [home, draw, away]
            if p_full is None:
                funnel[f'{anchor}:缺市场WDL'] += 1
                continue
            try:
                lam1_h, lam1_a = solve_lambda_core(float(p_full[0]), float(p_full[2]), float(total))
            except Exception:
                funnel[f'{anchor}:λ反演失败'] += 1
                continue
            L1 = {'grid': poisson_grid(lam1_h, lam1_a), 'ts': 'mkt_wdl', 'n_cov': 3}
            L2 = {'grid': poisson_grid(lam_h, lam_a), 'ts': v5s['ts'], 'n_cov': 2}
            L3 = {'grid': v5s['grid'], 'ts': v5s['ts'], 'n_cov': 36}
            rec = {'mid': m['match_id'], 'date': m['date'], 'league': m['league'],
                   'anchor': anchor, 'actual': m['actual'], 'total': total}
            for name, layer in (('L0', L0), ('L1', L1), ('L2', L2), ('L3', L3)):
                rec[name] = grid_metrics(layer['grid'], m['actual'])
            # L3 top1 概率与落库 Score_top1 交叉验证
            if v5s['top1_prob'] is not None and abs(rec['L3']['top1_prob'] - v5s['top1_prob']) > 0.01:
                rec['top1_xcheck'] = round(abs(rec['L3']['top1_prob'] - v5s['top1_prob']), 4)
            records.append(rec)
    if chk_fail:
        print(f'[ABORT] L0 网格构造器与 C-010 trace 等价自检失败 {chk_fail} 场，口径漂移，禁止出报告')
        return
    print(f'等价自检 PASS（L0 构造器 vs C-010 mkt_score_grid_noon trace 逐场一致）')
    print(f'漏斗: ' + '  '.join(f'{k}={v}' for k, v in funnel.items()) if funnel else '漏斗: 无缺失')
    print(f'同 n 交集样本: {len(records)} 条（双锚合计）')

    analyze(records, ts)
    conn.close()


def analyze(records, ts):
    lines = []
    L = lines.append
    L(f'# T-006 v5 top1 差距分解诊断（C-20261006-015）')
    L(f'')
    L(f'- 时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}｜窗口: {WIN_START}~{WIN_END} 五联赛｜锚点: t-24h / noon（C-010 同口径）')
    L(f'- 性质: **检查阶段只读诊断**，无修复方案、无选型；EVAL-peek 红线适用')
    L(f'- 分解链: L0 市场比分网格（信息天花板）→ L1 纯市场λ朴素泊松（total 锁 L2）→ L2 v5落库λ朴素泊松 → L3 v5网格')
    L(f'- 同 n 交集: {len(records)} 条（双锚合计）；链式归因非严格可加，判读为上限估计')
    L('')
    # 主表（双锚合并 + 分锚）
    L('## 1. 四层基准并列（Q③）')
    L('')
    L('| 锚点 | n | L0 市场比分网格 | L1 市场λ泊松 | L2 v5λ泊松 | L3 v5网格 | total gap(L0-L3) |')
    L('|---|---|---|---|---|---|---|')
    gap_by_anchor = {}
    for anchor in ('noon', 't24'):
        sub = [r for r in records if r['anchor'] == anchor]
        if not sub:
            continue
        hits = [np.mean([r[k]['top1_hit'] for r in sub]) for k in ('L0', 'L1', 'L2', 'L3')]
        gap_by_anchor[anchor] = (len(sub), hits)
        L(f'| {anchor} | {len(sub)} | ' + ' | '.join(fmt_pct(x) for x in hits) + f' | {fmt_pct(hits[0]-hits[3])} |')
    allhits = [np.mean([r[k]['top1_hit'] for r in records]) for k in ('L0', 'L1', 'L2', 'L3')]
    L(f'| 合计 | {len(records)} | ' + ' | '.join(fmt_pct(x) for x in allhits) + f' | {fmt_pct(allhits[0]-allhits[3])} |')
    L('')
    # 望远镜归因
    L('## 2. 望远镜归因（Q②）')
    L('')
    loss_shape = allhits[2] - allhits[3]
    loss_lam = allhits[1] - allhits[2]
    loss_info = allhits[0] - allhits[1]
    controllable = allhits[1] - allhits[3]
    L(f'| 分段 | top1 损失 | 定性 |')
    L(f'|---|---|---|')
    L(f'| L2→L3 形状损失（DC ρ + IPF） | **{loss_shape*100:+.1f}pp** | 模型侧可控 |')
    L(f'| L1→L2 λ链损失（Stage1 模型WDL注入 + 时点 + TG缩放） | **{loss_lam*100:+.1f}pp** | 模型侧可控 |')
    L(f'| L0→L1 市场比分盘信息溢价 | {loss_info*100:+.1f}pp | **不可控**（信息天花板） |')
    L(f'| **可控分量 = hit(L1) − hit(L3)** | **{controllable*100:+.1f}pp** | 分支阈值 4pp |')
    L('')
    verdict = '≥4pp → 满足开修复实验线条件（预注册方法论）' if controllable >= 0.04 else '<4pp → 不硬修，转价值重定位讨论'
    L(f'**分支判定（预冻结）: 可控分量 {fmt_pct(controllable)} → {verdict}**')
    L('')
    # 峰态
    L('## 3. 峰态/集中度（Q①）')
    L('')
    L('| 层 | max prob | top5 质量 | 熵 | top1 置信度均值 | top1 命中（同置信下） |')
    L('|---|---|---|---|---|---|')
    for k in ('L0', 'L1', 'L2', 'L3'):
        mx = np.mean([r[k]['max'] for r in records])
        t5 = np.mean([r[k]['top5_mass'] for r in records])
        ent = np.mean([r[k]['entropy'] for r in records])
        conf = np.mean([r[k]['top1_prob'] for r in records])
        L(f'| {k} | {mx:.3f} | {t5:.3f} | {ent:.3f} | {conf:.3f} | {fmt_pct(np.mean([r[k]["top1_hit"] for r in records]))} |')
    L('')
    # top1 可靠性分桶（L3 vs L0）
    L('### 3.1 top1 置信度-命中率分桶（L3 vs L0）')
    L('')
    qs = np.quantile([r['L3']['top1_prob'] for r in records], [0, .25, .5, .75, 1.0])
    L('| L3 置信度桶 | n | L3 命中率 | L3 置信度均值 | L0 命中率（同场） |')
    L('|---|---|---|---|---|')
    for i in range(4):
        lo, hi = qs[i], qs[i + 1]
        if i == 3:
            sub = [r for r in records if lo <= r['L3']['top1_prob'] <= hi]
        else:
            sub = [r for r in records if lo <= r['L3']['top1_prob'] < hi]
        if not sub:
            continue
        L(f'| [{lo:.3f}, {hi:.3f}{"]" if i == 3 else ")"} | {len(sub)} | '
          f'{fmt_pct(np.mean([r["L3"]["top1_hit"] for r in sub]))} | '
          f'{np.mean([r["L3"]["top1_prob"] for r in sub]):.3f} | '
          f'{fmt_pct(np.mean([r["L0"]["top1_hit"] for r in sub]))} |')
    L('')
    # 分布
    L('## 4. 分布均匀性（Q④）')
    L('')
    L('### 4.1 分联赛 top1（noon 锚）')
    L('')
    sub_noon = [r for r in records if r['anchor'] == 'noon']
    L('| 联赛 | n | L0 | L3 | gap |')
    L('|---|---|---|---|---|')
    for lg in sorted({r['league'] for r in sub_noon}):
        s = [r for r in sub_noon if r['league'] == lg]
        h0 = np.mean([r['L0']['top1_hit'] for r in s])
        h3 = np.mean([r['L3']['top1_hit'] for r in s])
        L(f'| {lg} | {len(s)} | {fmt_pct(h0)} | {fmt_pct(h3)} | {(h0-h3)*100:+.1f}pp |')
    L('')
    L('### 4.2 分比分族（实际赛果族，双锚合并）')
    L('')
    L('| 实际族 | n | L0 | L3 | gap |')
    L('|---|---|---|---|---|')
    for fam, cond in (('主胜族', lambda r: r['actual'][0] > r['actual'][1]),
                      ('平局族', lambda r: r['actual'][0] == r['actual'][1]),
                      ('客胜族', lambda r: r['actual'][0] < r['actual'][1])):
        s = [r for r in records if cond(r)]
        if not s:
            continue
        h0 = np.mean([r['L0']['top1_hit'] for r in s])
        h3 = np.mean([r['L3']['top1_hit'] for r in s])
        L(f'| {fam} | {len(s)} | {fmt_pct(h0)} | {fmt_pct(h3)} | {(h0-h3)*100:+.1f}pp |')
    L('')
    L('### 4.3 分月')
    L('')
    L('| 月 | n | L0 | L3 | gap |')
    L('|---|---|---|---|---|')
    for mo in sorted({r['date'][:7] for r in records}):
        s = [r for r in records if r['date'][:7] == mo]
        h0 = np.mean([r['L0']['top1_hit'] for r in s])
        h3 = np.mean([r['L3']['top1_hit'] for r in s])
        L(f'| {mo} | {len(s)} | {fmt_pct(h0)} | {fmt_pct(h3)} | {(h0-h3)*100:+.1f}pp |')
    L('')
    # top5 附注
    L('## 5. 附注')
    L('')
    t5g = [np.mean([r[k]['top5_hit'] for r in records]) for k in ('L0', 'L3')]
    L(f'- top5 命中: L0 {fmt_pct(t5g[0])} vs L3 {fmt_pct(t5g[1])}（gap {fmt_pct(t5g[0]-t5g[1])}）')
    xc = [r['top1_xcheck'] for r in records if 'top1_xcheck' in r]
    L(f'- L3 网格 argmax 与落库 Score_top1 交叉验证: {len(records)-len(xc)}/{len(records)} 一致（>0.01 偏差 {len(xc)} 条，最大 {max(xc) if xc else 0}）')
    se_l3 = float(np.std([r['L3']['top1_hit'] for r in records], ddof=1) / np.sqrt(len(records))) if len(records) > 1 else float('nan')
    L(f'- 小样本警示: n={len(records)}，L3 top1 命中率 SE≈{se_l3*100:.1f}pp，分段结论（分联赛/分月/分族）谨慎')
    L('')
    L('---')
    L('*诊断线产出，无修复方案。分支判定见 §2；如走修复线，须按 WDL 市场混合协议方法论预注册。*')

    out = os.path.join(REPORTS_DIR, f't006_top1_gap_diag_{ts}.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines))
    print(f'\n报告: {out}')


if __name__ == '__main__':
    main()
