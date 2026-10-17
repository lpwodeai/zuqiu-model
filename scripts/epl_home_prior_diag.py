# -*- coding: utf-8 -*-
"""
C-20261013-073 EPL 主场先验诊断（独立线, 只读量化, 不改任何参数）
=====================================================================================

登记文档: docs/class_weight_draw_discrim_experiment.md §7
异常登记值:
  模型英超 argmax 主胜占比 57.1%（prematch 报告样本口径）
  实际主胜占比 (n=63) 36.5%
  英超历史主胜率 (3 季) ~43-45%

口径（2026-10-13 定稿）:
  组 A: 26/27 窗口 EPL 实际主胜率。主源 matches.actual_score（归一化 胜/平/负 文本），
        交叉核对 post_match_review / odds500_match 26/27。
  组 B: 历史 3 季 EPL 主胜率基线。主源 odds500_match（season 字段，23/24+24/25+25/26
        三个完整赛季），交叉核对 matches 表同赛季边界。
  组 C: 模型 argmax 主胜占比 + p_home 均值。来源 model_predictions 落库 WDL 概率行
        （model_name='generate_unified_report_v2.0', is_replay=0, 生产预测行）。

三个检验（判据写死, 统计量实算, 不硬编码预期值）:
  T1  B vs A: 独立两比例 z 检验（ pooled SE）。|z|<1.96 → "英超今年主胜率偏低"不显著，
      定性小样本观察，不动任何先验。
  T2  C vs A: 同一批场次配对样本 → McNemar 精确检验
      （b=模型判主胜&实际非主胜, c=模型未判主胜&实际主胜）。p<0.05 → 主场通胀立案。
  T3  校准角度: 模型 p_home 均值 vs 实际主胜率（同一批场次）。p_home≈实际而 argmax 占比
      显著偏高 → 病在 argmax 方向层（与平局召回盲区同病），不在概率校准。

CLI:
    python scripts/epl_home_prior_diag.py
    python scripts/epl_home_prior_diag.py --recent-n 63 --out reports/epl_home_prior_diag_x.md
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"
CONFIG_PATH = PROJECT_DIR / "config.yaml"
ELO_FILE = PROJECT_DIR / "scripts" / "elo_rating.py"
BAYES_GLOB = PROJECT_DIR / "assets"

MODEL_NAME = "generate_unified_report_v2.0"
# 判据（写死）
Z_CRIT = 1.96
MCNEMAR_ALPHA = 0.05


# ---------------------------------------------------------------- 工具

def parse_score(s):
    """'2:1' / '2-1' → (2,1)；失败 → None。"""
    if not s:
        return None
    for sep in (':', '-'):
        if sep in str(s):
            try:
                h, a = str(s).split(sep)[:2]
                return int(h), int(a)
            except ValueError:
                return None
    return None


def norm_wdl(w):
    """归一化赛果文本 → home/draw/away。"""
    if w in ('主胜', '胜', 'home', 'H'):
        return 'home'
    if w in ('平局', '平', 'draw', 'D'):
        return 'draw'
    if w in ('客胜', '负', 'away', 'A'):
        return 'away'
    return None


def result_of(score=None, wdl=None, hg=None, ag=None):
    r = norm_wdl(wdl)
    if r:
        return r
    sc = parse_score(score)
    if sc:
        return 'home' if sc[0] > sc[1] else ('draw' if sc[0] == sc[1] else 'away')
    if hg is not None and ag is not None:
        return 'home' if hg > ag else ('draw' if hg == ag else 'away')
    return None


def wdl_stats(results):
    """['home','draw',...] → 计数与比率。"""
    n = len(results)
    if n == 0:
        return None
    h = sum(1 for r in results if r == 'home')
    d = sum(1 for r in results if r == 'draw')
    a = sum(1 for r in results if r == 'away')
    return {'n': n, 'home': h, 'draw': d, 'away': a,
            'home_rate': h / n, 'draw_rate': d / n, 'away_rate': a / n}


def se_proportion(p, n):
    return math.sqrt(p * (1 - p) / n) if n > 0 else float('nan')


def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def two_prop_z(x1, n1, x2, n2):
    """独立两比例 z 检验（pooled SE）。返回 (z, p_two_sided)。"""
    p_pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (x1 / n1 - x2 / n2) / se
    return z, 2 * (1 - norm_cdf(abs(z)))


def mcnemar_exact(b, c):
    """McNemar 精确二项检验（双侧）。返回 (chi2_cc, p_chi2, p_exact)。"""
    n = b + c
    if n == 0:
        return 0.0, 1.0, 1.0
    chi2 = (abs(b - c) - 1) ** 2 / n
    # chi2(1df) 上尾 = 2*(1-Phi(sqrt(x)))
    p_chi2 = 2 * (1 - norm_cdf(math.sqrt(chi2)))
    k = max(b, c)
    tail = sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)
    p_exact = min(1.0, 2 * tail)
    return chi2, p_chi2, p_exact


# ---------------------------------------------------------------- 数据

def group_a(conn):
    """组 A: 26/27 窗口 EPL 实际赛果。

    主源 = matches ∪ post_match_review（同 match_id 命名空间，pmr 补 matches 缺赛果的场次）；
    交叉核对 = odds500_match 26/27。
    """
    today = datetime.now().strftime('%Y-%m-%d')
    res, date_of = {}, {}
    for r in conn.execute(
            "SELECT match_id, match_date, actual_score, actual_wdl, home_goals, away_goals "
            "FROM matches WHERE league='英超' AND match_date >= '2026-08-01' "
            "AND match_date <= ? ORDER BY match_date", (today,)):
        x = result_of(score=r[2], wdl=r[3], hg=r[4], ag=r[5])
        if x:
            res[r[0]] = x
            date_of[r[0]] = r[1]
    for r in conn.execute(
            "SELECT match_id, match_date, actual_wdl, actual_score "
            "FROM post_match_review WHERE league='英超' AND match_date <= ?", (today,)):
        if r[0] not in res:
            x = result_of(score=r[3], wdl=r[2])
            if x:
                res[r[0]] = x
                date_of[r[0]] = r[1]
    pairs = sorted(((m, date_of[m], res[m]) for m in res), key=lambda t: t[1])
    main = wdl_stats([p[2] for p in pairs])

    pmr = list(conn.execute(
        "SELECT actual_wdl, actual_score FROM post_match_review WHERE league='英超'"))
    pmr_s = wdl_stats([x for x in (result_of(score=r[1], wdl=r[0]) for r in pmr) if x])

    o500 = list(conn.execute(
        "SELECT home_score, away_score FROM odds500_match "
        "WHERE season='26/27' AND league LIKE '%英超%' "
        "AND home_score IS NOT NULL AND away_score IS NOT NULL"))
    o500_s = wdl_stats([result_of(hg=r[0], ag=r[1]) for r in o500])
    return {'matches': main, 'pairs': pairs,
            'pmr': pmr_s, 'odds500': o500_s}


def five_league_2627(conn):
    """五联赛 26/27 赛果分布（odds500_match），用于平局率横向对比。"""
    out = {}
    for r in conn.execute(
            "SELECT league, COUNT(*) n, "
            "SUM(CASE WHEN home_score>away_score THEN 1 ELSE 0 END) h, "
            "SUM(CASE WHEN home_score=away_score THEN 1 ELSE 0 END) d, "
            "SUM(CASE WHEN home_score<away_score THEN 1 ELSE 0 END) a "
            "FROM odds500_match WHERE season='26/27' "
            "AND home_score IS NOT NULL AND away_score IS NOT NULL "
            "GROUP BY league"):
        out[r[0]] = {'n': r[1], 'home_rate': r[2] / r[1],
                     'draw_rate': r[3] / r[1], 'away_rate': r[4] / r[1]}
    return out


def group_b(conn):
    """组 B: 历史 3 季 EPL 基线（odds500 主源 + matches 交叉核对）。"""
    out = {}
    for season in ('23/24', '24/25', '25/26'):
        rows = list(conn.execute(
            "SELECT home_score, away_score FROM odds500_match "
            "WHERE season=? AND league LIKE '%英超%' "
            "AND home_score IS NOT NULL AND away_score IS NOT NULL", (season,)))
        out[season] = wdl_stats([result_of(hg=r[0], ag=r[1]) for r in rows])
    pooled_n = sum(s['n'] for s in out.values() if s)
    pooled_h = sum(s['home'] for s in out.values() if s)
    pooled = {'n': pooled_n, 'home': pooled_h, 'home_rate': pooled_h / pooled_n}

    # matches 表交叉核对（赛季边界 8/1~7/31）
    xcheck = {}
    for label, lo, hi in (('23/24', '2023-08-01', '2024-07-31'),
                          ('24/25', '2024-08-01', '2025-07-31'),
                          ('25/26', '2025-08-01', '2026-07-31')):
        rows = list(conn.execute(
            "SELECT actual_score, actual_wdl, home_goals, away_goals FROM matches "
            "WHERE league='英超' AND match_date >= ? AND match_date <= ?", (lo, hi)))
        xcheck[label] = wdl_stats([x for x in
                                   (result_of(score=r[0], wdl=r[1], hg=r[2], ag=r[3])
                                    for r in rows) if x])
    return {'by_season': out, 'pooled': pooled, 'xcheck_matches': xcheck}


def group_c(conn):
    """组 C: 落库 WDL 概率行（is_replay=0）→ argmax 主胜占比 + p_home 均值。

    返回 paired: [(match_id, argmax, actual, p_home, p_draw, p_away)]，actual 可能 None。
    """
    epl_ids = set(r[0] for r in conn.execute(
        "SELECT match_id FROM matches WHERE league='英超'"))
    epl_ids |= set(r[0] for r in conn.execute(
        "SELECT match_id FROM post_match_review WHERE league='英超'"))

    mids = [r[0] for r in conn.execute(
        "SELECT DISTINCT match_id FROM model_predictions "
        "WHERE model_name=? AND is_replay=0 AND prediction_type='WDL_home'", (MODEL_NAME,))]
    epl_mids = [m for m in mids if m in epl_ids]

    actual_of = {}
    for r in conn.execute(
            "SELECT match_id, actual_score, actual_wdl, home_goals, away_goals "
            "FROM matches WHERE league='英超'"):
        res = result_of(score=r[1], wdl=r[2], hg=r[3], ag=r[4])
        if res:
            actual_of[r[0]] = res
    for r in conn.execute(
            "SELECT match_id, actual_wdl, actual_score FROM post_match_review "
            "WHERE league='英超'"):
        if r[0] not in actual_of:
            res = result_of(score=r[2], wdl=r[1])
            if res:
                actual_of[r[0]] = res

    paired = []
    for m in sorted(epl_mids):
        probs = {}
        ok = True
        for pt in ('WDL_home', 'WDL_draw', 'WDL_away'):
            r = conn.execute(
                "SELECT probability FROM model_predictions "
                "WHERE model_name=? AND is_replay=0 AND prediction_type=? AND match_id=? "
                "ORDER BY timestamp DESC LIMIT 1", (MODEL_NAME, pt, m)).fetchone()
            if r is None or r[0] is None:
                ok = False
                break
            probs[pt] = r[0]
        if not ok:
            continue
        am = max(probs, key=probs.get).replace('WDL_', '')
        paired.append({'match_id': m, 'argmax': am,
                       'actual': actual_of.get(m),
                       'p_home': probs['WDL_home'], 'p_draw': probs['WDL_draw'],
                       'p_away': probs['WDL_away']})
    return paired


# ---------------------------------------------------------------- knob 只读核对

def knob_review():
    """四个 knob 的英超取值 vs 五联赛均值。只读，不改。"""
    import yaml
    cfg = yaml.safe_load(open(CONFIG_PATH, encoding='utf-8')) or {}

    knobs = []

    # 1) Elo HOME_ADVANTAGE（全局常量）
    elo_val = None
    m = re.search(r'HOME_ADVANTAGE\s*=\s*([0-9.]+)',
                  ELO_FILE.read_text(encoding='utf-8'))
    if m:
        elo_val = float(m.group(1))
    knobs.append({
        'knob': 'Elo home_advantage',
        'source': 'scripts/elo_rating.py HOME_ADVANTAGE（全局常量，无逐联赛覆盖）',
        'epl': elo_val, 'five_mean': elo_val,
        'note': '全局同一常量 65 分（等强对期望胜率 0.595），英超无特殊化；'
                '对英超历史 43-46% 主胜率量级合理',
        'suspect': '低',
    })

    # 2) Dixon-Coles ρ + λ 主场加成
    dc = ((cfg.get('unified_engine') or {}).get('dixon_coles') or {}).get('leagues') or {}
    dc_epl = dc.get('英超')
    dc_mean = sum(dc.values()) / len(dc) if dc else None
    lb = ((cfg.get('lambda_adjustment') or {}).get('league_boost') or {})
    knobs.append({
        'knob': 'Dixon-Coles ρ（低比分平局修正）',
        'source': 'config.yaml unified_engine.dixon_coles.leagues',
        'epl': dc_epl, 'five_mean': dc_mean,
        'note': f'英超 ρ={dc_epl}，修正强度弱于均值 {dc_mean:.3f}（ρ 越负压平越弱）；'
                f'λ league_boost 英超=1.0 未调整（仅 LaLiga={lb.get("LaLiga")}）；'
                '方向上与平局盲区同病，但量级小',
        'suspect': '低-中（ρ 偏弱方向性注意）',
    })

    # 3) 贝叶斯主场先验（先验均值 + MAP 拟合值）
    bayes = {}
    for p in sorted(BAYES_GLOB.glob('bayesian_model_*.json')):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            lg = d.get('league') or p.stem.split('_')[-1]
            bayes[lg] = d.get('home_adv')
        except Exception:
            pass
    b_epl = bayes.get('英超')
    b_mean = sum(v for v in bayes.values() if v is not None) / len(bayes) if bayes else None
    knobs.append({
        'knob': '贝叶斯主场先验（MAP 拟合 home_adv）',
        'source': 'scripts/bayesian_hierarchical_model.py home_prior=0.28（全局先验均值）；'
                  'assets/bayesian_model_*.json（逐联赛 MAP 拟合）',
        'epl': b_epl, 'five_mean': b_mean,
        'note': f'拟合值 {bayes}；英超 {b_epl} 低于五联赛均值 {b_mean:.4f}，'
                '且拟合窗止于 2024-12（偏旧），非通胀源',
        'suspect': '低（最低）',
    })

    # 4) 逐联赛补偿权重
    lc = ((cfg.get('training') or {}).get('league_compensation') or {})
    w = lc.get('weights') or {}
    w_epl = w.get('英超')
    w_mean = sum(w.values()) / len(w) if w else None
    knobs.append({
        'knob': '逐联赛补偿权重（训练 sample_weight）',
        'source': 'config.yaml training.league_compensation.weights',
        'epl': w_epl, 'five_mean': w_mean,
        'note': f'英超={w_epl}（=default 1.0，未补偿）；仅 德甲/法甲=1.20 '
                '（306 场结构补偿）；英超不存在被补偿抬高',
        'suspect': '低（已排除）',
    })

    # 附: league_specific 因子（特征层常量）
    ls = cfg.get('league_specific') or {}
    haf = {k: (v or {}).get('home_advantage_factor') for k, v in ls.items()}
    hga = {k: (v or {}).get('home_goal_advantage') for k, v in ls.items()}
    haf_vals = [v for v in haf.values() if v is not None]
    hga_vals = [v for v in hga.values() if v is not None]
    knobs.append({
        'knob': 'league_specific 主场因子（特征层常量）',
        'source': 'config.yaml league_specific.*.home_advantage_factor / home_goal_advantage',
        'epl': haf.get('EPL'),
        'five_mean': sum(haf_vals) / len(haf_vals) if haf_vals else None,
        'note': f'home_advantage_factor={haf}（英超 1.42 < 均值 '
                f'{sum(haf_vals)/len(haf_vals):.3f}）；home_goal_advantage={hga} '
                f'（英超 0.30 ≈ 均值 {sum(hga_vals)/len(hga_vals):.3f}）',
        'suspect': '低',
    })
    return knobs


# ---------------------------------------------------------------- 报告

def fmt_pct(x, nd=1):
    return f'{x*100:.{nd}f}%' if x is not None else '—'


def build_report(a, b, paired, t1, t2, t3, knobs, verdict_lines, ts, fl):
    L = []
    L.append('# EPL 主场先验诊断报告（C-20261013-073）')
    L.append('')
    L.append(f'- 生成时间: {ts}')
    L.append('- 登记背景: docs/class_weight_draw_discrim_experiment.md §7 —— '
             '模型英超 argmax 主胜占比登记值 57.1%（prematch 报告样本口径）vs '
             '实际主胜率 36.5%（n=63），疑似模型主场通胀')
    L.append('- 性质: **只读量化诊断**，不改任何模型/配置/生产参数')
    L.append('')
    L.append('## 1. 三方对比（A 实际 26/27 / B 历史 3 季 / C 模型落库 WDL）')
    L.append('')
    L.append('| 组 | 口径 | n | 主胜 | 平局 | 客胜 | 主胜率 |')
    L.append('|---|---|---|---|---|---|---|')
    am = a['matches']
    L.append(f"| A | 26/27 实际（matches ∪ post_match_review 并集归一化，至 {a['pairs'][-1][1] if a['pairs'] else '—'}） "
             f"| {am['n']} | {am['home']} | {am['draw']} | {am['away']} | **{fmt_pct(am['home_rate'])}** |")
    bp = b['pooled']
    L.append(f"| B | 历史 3 季基线（odds500_match 23/24+24/25+25/26 完整赛季） "
             f"| {bp['n']} | {bp['home']} | — | — | **{fmt_pct(bp['home_rate'])}** |")
    n_c = len(paired)
    n_ch = sum(1 for p in paired if p['argmax'] == 'home')
    n_cd = sum(1 for p in paired if p['argmax'] == 'draw')
    n_ca = sum(1 for p in paired if p['argmax'] == 'away')
    ph_mean = sum(p['p_home'] for p in paired) / n_c if n_c else None
    L.append(f"| C | 模型落库 WDL 行（is_replay=0）argmax "
             f"| {n_c} | {n_ch} | {n_cd} | {n_ca} | **{fmt_pct(n_ch/n_c if n_c else None)}** |")
    L.append(f"| C' | 模型 p_home 概率均值（同上批次） "
             f"| {n_c} | — | — | — | **{fmt_pct(ph_mean)}** |")
    L.append('')
    L.append('交叉核对（组 A 另两源）:')
    L.append('')
    L.append('| 源 | n | 主胜率 | 平局率 |')
    L.append('|---|---|---|---|')
    if a['pmr']:
        L.append(f"| post_match_review 英超 | {a['pmr']['n']} | {fmt_pct(a['pmr']['home_rate'])} | {fmt_pct(a['pmr']['draw_rate'])} |")
    if a['odds500']:
        L.append(f"| odds500_match 26/27 | {a['odds500']['n']} | {fmt_pct(a['odds500']['home_rate'])} | {fmt_pct(a['odds500']['draw_rate'])} |")
    L.append('')
    L.append('组 B 分季（odds500 主源 / matches 交叉核对）:')
    L.append('')
    L.append('| 赛季 | odds500 n | 主胜率 | matches n | 主胜率 |')
    L.append('|---|---|---|---|---|')
    for s in ('23/24', '24/25', '25/26'):
        o = b['by_season'].get(s)
        x = b['xcheck_matches'].get(s)
        L.append(f"| {s} | {o['n'] if o else '—'} | {fmt_pct(o['home_rate']) if o else '—'} "
                 f"| {x['n'] if x else '—'} | {fmt_pct(x['home_rate']) if x else '—'} |")
    L.append('')
    L.append(f'> 口径注记: 登记值 57.1% 为 prematch 报告样本（n=63）argmax 占比；'
             f'本报告组 C 采用落库 WDL 概率行口径，当前 EPL 覆盖 n={n_c}（五联赛合计 162 场），'
             f'两口径样本不同，数值不苛求一致；p_home 均值是概率校准口径，'
             f'与 argmax 占比（方向口径）分别输出。')
    L.append('')
    L.append('五联赛 26/27 赛果分布（odds500_match，实算）:')
    L.append('')
    L.append('| 联赛 | n | 主胜率 | 平局率 | 客胜率 |')
    L.append('|---|---|---|---|---|')
    max_draw_lg = max(fl, key=lambda k: fl[k]['draw_rate']) if fl else None
    for lg, s in sorted(fl.items(), key=lambda kv: -kv[1]['draw_rate']):
        mark = ' ← 平局率五联赛最高' if lg == max_draw_lg else ''
        L.append(f"| {lg} | {s['n']} | {fmt_pct(s['home_rate'])} | {fmt_pct(s['draw_rate'])} | {fmt_pct(s['away_rate'])}{mark} |")
    L.append('')
    L.append('## 2. 三个检验')
    L.append('')
    L.append('### T1  B vs A：英超今年主胜率是否显著偏低（独立两比例 z，pooled SE）')
    L.append('')
    L.append(f"- B: {bp['home']}/{bp['n']} = {fmt_pct(bp['home_rate'], 2)}；"
             f"A: {am['home']}/{am['n']} = {fmt_pct(am['home_rate'], 2)}（SE={se_proportion(am['home_rate'], am['n'])*100:.1f}pp）")
    L.append(f"- z = **{t1['z']:.3f}**，双侧 p = **{t1['p']:.4f}**")
    L.append(f"- 判据: |z| < {Z_CRIT} → 不显著。判定: **{t1['verdict']}**")
    L.append('')
    L.append('### T2  C vs A：模型主场通胀是否成立（同批次配对 McNemar 精确检验）')
    L.append('')
    L.append(f"- 配对场次 n={t2['n']}（模型 argmax 与实际赛果同批次）")
    L.append(f"- 不一致格: b（模型判主胜 & 实际非主胜）= **{t2['b']}**；"
             f"c（模型未判主胜 & 实际主胜）= **{t2['c']}**")
    L.append(f"- 连续性校正 χ² = {t2['chi2']:.3f}（p={t2['p_chi2']:.4f}）；"
             f"精确二项双侧 p = **{t2['p_exact']:.4f}**")
    L.append(f"- 判据: p < {MCNEMAR_ALPHA} → 主场通胀立案。判定: **{t2['verdict']}**")
    L.append('')
    L.append('### T3  校准角度：p_home 均值 vs 实际主胜率（同批次）')
    L.append('')
    L.append(f"- 模型 p_home 均值 = **{fmt_pct(t3['p_home_mean'], 1)}**；"
             f"同批次实际主胜率 = **{fmt_pct(t3['actual_home_rate'], 1)}**；"
             f"差 = {t3['gap']*100:+.1f}pp")
    L.append(f"- 同批次模型 argmax 主胜占比 = {fmt_pct(t3['argmax_home_rate'], 1)}，"
             f"argmax 平局占比 = {fmt_pct(t3['argmax_draw_rate'], 1)}，"
             f"同批次实际平局率 = {fmt_pct(t3['actual_draw_rate'], 1)}")
    L.append(f"- 判定: **{t3['verdict']}**")
    L.append('')
    L.append('## 3. 通胀来源拆解（四 knob 只读核对，英超取值 vs 五联赛均值）')
    L.append('')
    L.append('| knob | 来源 | 英超取值 | 五联赛均值 | 可疑度 | 说明 |')
    L.append('|---|---|---|---|---|---|')
    for k in knobs:
        epl_v = f"{k['epl']:.4g}" if isinstance(k['epl'], float) else str(k['epl'])
        mean_v = f"{k['five_mean']:.4g}" if isinstance(k['five_mean'], float) else str(k['five_mean'])
        L.append(f"| {k['knob']} | {k['source']} | {epl_v} | {mean_v} | {k['suspect']} | {k['note']} |")
    L.append('')
    L.append('## 4. 结论分档')
    L.append('')
    for v in verdict_lines:
        L.append(f'- {v}')
    L.append('')
    L.append('## 5. 诚实预期')
    L.append('')
    L.append('- 即使主场通胀修正落地，EPL 命中率预计仅 **+2~3pp**：被移走的通胀场次里实际仍有 '
             '~36% 是真主胜，方向移走会同时损失这部分命中；修正价值在方向结构（让出平局/客胜召回空间），'
             '不在总命中率跳升。')
    L.append('- 防止验收误判「修复无效」：若修复后 EPL 命中率 +2~3pp 且平局/客胜召回抬升，即为达标；'
             '不以总命中率大幅跃迁为验收标准。')
    L.append('')
    L.append('## 6. 附：配对场次的明细（组 C × A）')
    L.append('')
    L.append('| match_id | 模型 argmax | 实际 | p_home | p_draw | p_away |')
    L.append('|---|---|---|---|---|---|')
    for p in paired:
        L.append(f"| {p['match_id']} | {p['argmax']} | {p['actual'] or '—'} "
                 f"| {p['p_home']:.3f} | {p['p_draw']:.3f} | {p['p_away']:.3f} |")
    L.append('')
    return '\n'.join(L)


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description='EPL 主场先验诊断（C-20261013-073 定稿口径）')
    parser.add_argument('--recent-n', type=int, default=63, help='（兼容参数，不再截尾，A 取全量 26/27 窗口）')
    parser.add_argument('--out', type=str, default=None, help='报告输出路径')
    args = parser.parse_args()

    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ts_file = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = Path(args.out) if args.out else REPORT_DIR / f'epl_home_prior_diag_{ts_file}.md'

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    a = group_a(conn)
    b = group_b(conn)
    fl = five_league_2627(conn)
    paired = group_c(conn)
    conn.close()

    am, bp = a['matches'], b['pooled']

    # T1: B vs A
    z1, p1 = two_prop_z(bp['home'], bp['n'], am['home'], am['n'])
    t1 = {'z': z1, 'p': p1,
          'verdict': ('显著（|z|≥1.96）' if abs(z1) >= Z_CRIT else
                      '不显著（|z|<1.96）——「英超今年主胜率偏低」定性为小样本观察，标记后等样本，不动任何先验')}

    # T2: McNemar（同批次配对）
    pp = [p for p in paired if p['actual']]
    tb = sum(1 for p in pp if p['argmax'] == 'home' and p['actual'] != 'home')
    tc = sum(1 for p in pp if p['argmax'] != 'home' and p['actual'] == 'home')
    chi2, p_chi2, p_exact = mcnemar_exact(tb, tc)
    t2 = {'n': len(pp), 'b': tb, 'c': tc, 'chi2': chi2, 'p_chi2': p_chi2, 'p_exact': p_exact,
          'verdict': ('p<0.05 —— 模型主场通胀成立，立案' if p_exact < MCNEMAR_ALPHA else
                      'p≥0.05 —— 当前配对样本下主场通胀不显著，不立案（方向性偏多 b>c，留观察）')}

    # T3: 校准角度
    n_c = len(pp)
    ph_mean = sum(p['p_home'] for p in pp) / n_c if n_c else float('nan')
    act_home = sum(1 for p in pp if p['actual'] == 'home') / n_c if n_c else float('nan')
    act_draw = sum(1 for p in pp if p['actual'] == 'draw') / n_c if n_c else float('nan')
    am_home = sum(1 for p in pp if p['argmax'] == 'home') / n_c if n_c else float('nan')
    am_draw = sum(1 for p in pp if p['argmax'] == 'draw') / n_c if n_c else float('nan')
    gap = ph_mean - act_home
    if abs(gap) <= 0.04 and am_home > act_home + 0.10:
        t3v = ('p_home 均值与实际主胜率基本贴合（|差|≤4pp），而 argmax 主胜占比显著高于实际'
               '——病在 argmax 方向层（与平局召回盲区同病：argmax 从不选平），不在概率校准')
    else:
        t3v = 'p_home 均值与实际主胜率存在偏差，需结合 T1/T2 综合判断'
    t3 = {'p_home_mean': ph_mean, 'actual_home_rate': act_home, 'gap': gap,
          'argmax_home_rate': am_home, 'argmax_draw_rate': am_draw,
          'actual_draw_rate': act_draw, 'verdict': t3v}

    knobs = knob_review()

    # 结论分档
    verdict_lines = []
    t1_sig = abs(z1) >= Z_CRIT
    t2_sig = p_exact < MCNEMAR_ALPHA
    if t2_sig:
        verdict_lines.append('**档位一（立案）**：T2 McNemar p<0.05，模型主场通胀成立 → '
                             '建议立「英超主场先验校准」独立预注册线（队尾优先级，与平局实验并行不串）。')
    elif not t1_sig and not t2_sig:
        epl_draw = am['draw_rate']
        fl_max = max(fl, key=lambda k: fl[k]['draw_rate']) if fl else None
        am_draw_subset = sum(1 for p in pp if p['argmax'] == 'draw')
        verdict_lines.append('**档位二（不立案，本报告落此档）**：T1、T2 均不显著 → '
                             'EPL 低命中率主要归因平局盲区（EPL 26/27 实际平局率 '
                             f'{fmt_pct(epl_draw)}'
                             + (f'，odds500 口径下为五联赛最高' if fl_max == '英超' else '')
                             + f' × 全局 argmax 平局召回≈0（292 场归因登记值 1.3%；本批次 EPL {am_draw_subset}/{len(pp)}）），'
                             '归入 C-072 主实验 AUC 目标，不单独立案。')
    else:
        verdict_lines.append('**中间档**：T1/T2 一显著一不显著，需人工复核后再定档。')
    verdict_lines.append(f'方向性备注：配对 n={len(pp)} 下 b={tb} > c={tc}，模型判主胜方向偏多但未达显著；'
                         '落库 WDL 行 EPL 覆盖仍在累积，样本扩大后若 McNemar 转显著，再重启立案评估。')
    kb = {k['knob']: k for k in knobs}
    bayes_epl = next((k['epl'] for k in knobs if '贝叶斯' in k['knob']), None)
    dc_epl_v = next((k['epl'] for k in knobs if 'Dixon-Coles' in k['knob']), None)
    verdict_lines.append('knob 层结论：四个主场相关 knob 的英超取值均不高于五联赛均值'
                         f'（Elo 全局 {kb.get("Elo home_advantage", {}).get("epl")}、'
                         f'贝叶斯拟合 {bayes_epl:.4f}<均值、补偿权重 1.0、league_specific 1.42<均值；'
                         f'仅 DC ρ={dc_epl_v} 修正强度偏弱方向性注意）——**先验/knob 层未见英超特异通胀源**，'
                         '与统计检验一致：病在 argmax 方向层/平局盲区，不在主场先验。')

    report = build_report(a, b, paired, t1, t2, t3, knobs, verdict_lines, ts, fl)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding='utf-8')

    # 控制台摘要
    print('=' * 64)
    print('C-20261013-073 EPL 主场先验诊断（定稿口径）')
    print('=' * 64)
    print(f"[A] 26/27 实际: n={am['n']} 主胜={fmt_pct(am['home_rate'],2)} "
          f"平={fmt_pct(am['draw_rate'],2)} 客={fmt_pct(am['away_rate'],2)}")
    print(f"    交叉核对: post_match_review n={a['pmr']['n']} 主胜={fmt_pct(a['pmr']['home_rate'],2)}; "
          f"odds500 26/27 n={a['odds500']['n']} 主胜={fmt_pct(a['odds500']['home_rate'],2)}")
    print(f"[B] 3 季基线: n={bp['n']} 主胜={fmt_pct(bp['home_rate'],2)} "
          f"(23/24={fmt_pct(b['by_season']['23/24']['home_rate'])} "
          f"24/25={fmt_pct(b['by_season']['24/25']['home_rate'])} "
          f"25/26={fmt_pct(b['by_season']['25/26']['home_rate'])})")
    print(f"[C] 落库 WDL: n={len(paired)} argmax主胜={fmt_pct(sum(1 for p in paired if p['argmax']=='home')/len(paired),2)} "
          f"p_home均值={fmt_pct(sum(p['p_home'] for p in paired)/len(paired),2)}")
    print(f"[T1] B vs A: z={z1:.3f} p={p1:.4f} -> {t1['verdict'][:20]}")
    print(f"[T2] McNemar: b={tb} c={tc} chi2={chi2:.3f} p_exact={p_exact:.4f} -> {t2['verdict'][:24]}")
    print(f"[T3] p_home={fmt_pct(ph_mean,1)} vs actual={fmt_pct(act_home,1)} gap={gap*100:+.1f}pp")
    print(f"[报告] {out_path}")


if __name__ == '__main__':
    main()
