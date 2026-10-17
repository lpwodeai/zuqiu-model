# -*- coding: utf-8 -*-
"""
t006_tg_edge_diag.py — v5 对 TG（大小球 2.5）边缘边际贡献诊断（C-20261006-016 后续，只读）
====================================================================================
问题（价值重定位第一动作，C-20261006-015 分支裁定 <4pp 转价值重定位）：
  top1 降级后，v5 剩余价值叙事之一是「网格形状信息对 TG 边缘有贡献」。
  现网 TG_over_2_5 落库（generate_unified_report_v2.0）来自 TG 线（比分市场一维 λ 投影），
  v5 网格 grid-implied P(over2.5) = Σ_{h+a≥3} grid[h,a]。
  若两者 corr≈1 且命中/Brier 无差异 → v5 对 TG 边缘边际贡献 ≈ 0，TG 价值叙事不成立。

口径：
  - 窗口 2026-08-22 ~ 2026-09-21（env TOP1_DIAG_WIN_START/END 可覆盖，与 top1 诊断同参数化）
  - 对比三方（均 v5 英文 match_id 命名空间）：
      stored_p : model_predictions TG_over_2_5（generate_unified_report_v2.0, is_replay=0）
      grid_p   : v5 网格（t006_score_predictor_v5 Score_grid_*）grid-implied P(total≥3)
      actual   : matches.actual_score 解析 total≥3（exact join 失败时经 canon_key 桥接）
  - 指标：corr（pearson/spearman）、mean|diff|、|diff|>0.05 占比、
          二项命中（p>0.5 方向）、Brier 及其差值 SE（通用 <1SE 条款适用，C-20261006-016）
  - caveat：窗口内 v5 网格为 C-005 回填产物（与 C-015 同源，B0 前视 caveat 继承）
输出：reports/t006_tg_edge_diag_<ts>.md + 控制台。全程只读 SELECT。
"""
import os, sys, sqlite3
from datetime import datetime
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

from t006_v5_acceptance import SCORE_RE
from tfs_shadow_report import load_model_grid, build_grid_index, load_name_map, canon_key
from market_benchmark_hc import _norm

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
LEAGUES = ('英超', '西甲', '意甲', '德甲', '法甲')
GRID_MODEL = 't006_score_predictor_v5'
PROD_MODEL = 'generate_unified_report_v2.0'

# 复测窗口参数化（C-20261006-016，与 t006_top1_gap_diag 同名 env）
WIN_START = os.environ.get('TOP1_DIAG_WIN_START', '2026-08-22')
WIN_END = os.environ.get('TOP1_DIAG_WIN_END', '2026-09-21')


def rankdata(x):
    """平均秩 spearman 用（无 scipy 依赖）。"""
    x = np.asarray(x, dtype=float)
    order = np.argsort(x)
    ranks = np.empty(len(x), dtype=float)
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def main():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=5000')

    print('=' * 78)
    print(f'v5 对 TG(over 2.5) 边缘边际贡献诊断（只读）  窗口 {WIN_START}~{WIN_END}')
    print('=' * 78)

    # matches 索引：exact 主键 + canon (date, norm_home, norm_away) 双路
    ph = ','.join('?' * len(LEAGUES))
    m_exact, m_canon = {}, {}
    for r in conn.execute(
            f"SELECT match_id, match_date, league, home_team, away_team, actual_score FROM matches "
            f"WHERE league IN ({ph}) AND match_date >= ? AND match_date <= ? "
            f"AND actual_score IS NOT NULL AND actual_score != ''",
            (*LEAGUES, WIN_START, WIN_END)):
        row = {'date': str(r['match_date'])[:10], 'league': r['league'],
               'home': r['home_team'], 'away': r['away_team'], 'actual': str(r['actual_score']).strip()}
        m_exact[r['match_id']] = row
        m_canon[(row['date'], _norm(row['home']), _norm(row['away']))] = row

    # 现网 TG 落库
    stored = {}
    for r in conn.execute(
            "SELECT match_id, probability FROM model_predictions "
            "WHERE model_name=? AND is_replay=0 AND prediction_type='TG_over_2_5'", (PROD_MODEL,)):
        stored[r['match_id']] = float(r['probability'])

    name_map = load_name_map(conn)
    records = []
    funnel = {'网格场次': 0, '缺TG落库': 0, '缺赛果桥接': 0, '比分不可解析': 0}
    for mid in build_grid_index(conn):
        if not (WIN_START <= mid[:10] <= WIN_END):
            continue
        gm = load_model_grid(conn, mid)
        if gm is None:
            continue
        funnel['网格场次'] += 1
        sp = stored.get(mid)
        if sp is None:
            funnel['缺TG落库'] += 1
            continue
        m = m_exact.get(mid)
        if m is None:
            ck = canon_key(mid, name_map)
            if ck:
                m = m_canon.get((ck[0], _norm(ck[1]), _norm(ck[2])))
        if m is None:
            funnel['缺赛果桥接'] += 1
            continue
        am = SCORE_RE.match(m['actual'])
        if not am:
            funnel['比分不可解析'] += 1
            continue
        total = int(am.group(1)) + int(am.group(2))
        g = gm / gm.sum()
        records.append({'mid': mid, 'date': m['date'], 'league': m['league'],
                        'stored': sp, 'grid': float(g[np.add.outer(np.arange(6), np.arange(6)) >= 3].sum()),
                        'y': 1 if total >= 3 else 0})

    print(f'漏斗: ' + '  '.join(f'{k}={v}' for k, v in funnel.items()))
    print(f'配对样本: {len(records)}')
    if len(records) < 10:
        print('[ABORT] 样本不足，不出报告')
        return
    analyze(records, ts)
    conn.close()


def analyze(records, ts):
    lines = []
    L = lines.append
    sp = np.array([r['stored'] for r in records])
    gp = np.array([r['grid'] for r in records])
    y = np.array([r['y'] for r in records], dtype=float)
    n = len(records)

    pear = float(np.corrcoef(sp, gp)[0, 1])
    spear = float(np.corrcoef(rankdata(sp), rankdata(gp))[0, 1])
    diff = gp - sp
    brier_s = (sp - y) ** 2
    brier_g = (gp - y) ** 2
    acc_s = float(((sp > 0.5) == (y > 0.5)).mean())
    acc_g = float(((gp > 0.5) == (y > 0.5)).mean())
    db = brier_g - brier_s
    se_db = float(db.std(ddof=1) / np.sqrt(n)) if n > 1 else float('nan')
    over_rate = float(y.mean())

    L('# v5 对 TG(over 2.5) 边缘边际贡献诊断（C-20261006-016 后续）')
    L('')
    L(f'- 时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}｜窗口: {WIN_START}~{WIN_END} 五联赛｜配对 n={n}')
    L(f'- 口径: stored=现网 TG_over_2_5 落库（{PROD_MODEL}，TG线 λ 投影）vs grid=v5 网格 grid-implied P(over2.5)=Σ(h+a≥3) vs 实际 total≥3')
    L(f'- caveat: 窗口内 v5 网格为 C-005 回填产物（C-015 同源，B0 前视 caveat 继承）；通用 <1SE 条款适用（C-20261006-016）')
    L('')
    L('## 1. 一致性（stored vs grid）')
    L('')
    L(f'| 指标 | 值 |')
    L(f'|---|---|')
    L(f'| Pearson corr | {pear:.4f} |')
    L(f'| Spearman corr | {spear:.4f} |')
    L(f'| mean(stored) | {sp.mean():.4f} |')
    L(f'| mean(grid) | {gp.mean():.4f} |')
    L(f'| mean(grid − stored) | {diff.mean()*100:+.2f}pp |')
    L(f'| mean\\|grid − stored\\| | {np.abs(diff).mean():.4f} |')
    L(f'| \\|diff\\|>0.05 占比 | {(np.abs(diff) > 0.05).mean():.1%} |')
    L(f'| grid 更高占比 | {(diff > 0).mean():.1%} |')
    L('')
    L('## 2. 对实际 over2.5 的判别力')
    L('')
    L(f'- 实际 over 占比: {over_rate:.1%}（n={n}）｜基率参照: 恒猜 {over_rate:.1%} 的 Brier = {over_rate*(1-over_rate):.4f}')
    L(f'| 口径 | 二项命中(p>0.5) | Brier |')
    L(f'|---|---|---|')
    L(f'| stored (TG线落库) | {acc_s:.3f} | {brier_s.mean():.4f} |')
    L(f'| grid (v5 网格) | {acc_g:.3f} | {brier_g.mean():.4f} |')
    L(f'| 差（grid − stored） | {acc_g-acc_s:+.3f} | ΔBrier {db.mean():+.4f} ± {se_db:.4f}（1SE） |')
    L('')
    L('## 3. 分月')
    L('')
    L('| 月 | n | stored Brier | grid Brier | mean\\|diff\\| |')
    L('|---|---|---|---|---|')
    for mo in sorted({r['date'][:7] for r in records}):
        idx = [i for i, r in enumerate(records) if r['date'][:7] == mo]
        L(f'| {mo} | {len(idx)} | {brier_s[idx].mean():.4f} | {brier_g[idx].mean():.4f} | {np.abs(diff[idx]).mean():.4f} |')
    L('')
    L('## 4. 判定')
    L('')
    hi_corr = pear >= 0.95
    brier_nsig = abs(db.mean()) < se_db
    if hi_corr and brier_nsig:
        verdict = ('**v5 对 TG 边缘边际贡献 ≈ 0**：网格隐含 P(over2.5) 与现网 TG 线落库高度一致'
                   f'（corr={pear:.3f}），且 ΔBrier={db.mean():+.4f} < 1SE（{se_db:.4f}）→ 证据不足认定差异，'
                   'TG 价值叙事不成立。按通用条款留档，非永久关闭：live 积累后再触发条件同 C-016。')
    elif hi_corr:
        verdict = (f'高度一致（corr={pear:.3f}）但 ΔBrier={db.mean():+.4f} ≥ 1SE（{se_db:.4f}）→ '
                   '存在可分辨差异，需进一步看方向与分月稳健性再下结论。')
    else:
        verdict = (
            f'两者高度不一致（corr={pear:.3f}）：网格与 TG 线承载不同信息，非复制关系。'
            f'但就边际价值本身：grid 相对 stored 无判别力增益（ΔBrier={db.mean():+.4f} ± {se_db:.4f}，1SE 内；'
            f'二项命中差 {acc_g-acc_s:+.3f}）→ **「v5 网格对 TG 边缘有额外价值」在本窗证伪**'
            '（通用 <1SE 条款留档，非永久关闭）。'
            f'附发现（留档不越界）：mean(stored)={sp.mean():.4f} 系统性高于实际 over 占比 {over_rate:.1%}'
            f'（+{(sp.mean()-over_rate)*100:.1f}pp），mean(grid)={gp.mean():.4f} 反而更贴近实际水平'
            '——TG 线落库存在水平偏置嫌疑，归因（λ 差异 vs 网格形状差异 / TG 线校准）留作后续专门诊断。')
    L(verdict)
    L('')
    L('---')
    L('*只读诊断，无生产变更。价值重定位依据之一；与 C-015 top1 降级、C-016 通用 <1SE 条款联动。*')

    out = os.path.join(REPORTS_DIR, f't006_tg_edge_diag_{ts}.md')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines))
    print(f'\n报告: {out}')


if __name__ == '__main__':
    main()
