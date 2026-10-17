# -*- coding: utf-8 -*-
"""tfs_shadow_report.py — T-006 v5 shadow 比分矩阵 TFS 回算报告（D-20261006-052 轨 A）

数据源：
  - 模型网格：model_predictions（model_name=t006_score_predictor_v5, is_replay=0,
    Score_grid_h_a，英文 match_id，与 t006_shadow_v5.jsonl 同键）
  - 市场网格：score_history（中文 id，经 market_benchmark_hc.build_en2cn 桥接；
    取比赛日前最后一个快照，1/odds 去水归一）
  - 赛果：post_match_review / matches

口径纪律：jsonl 中 v5 error / 网格缺失 / 市场缺失 / 无赛果 的场次明确计数，不静默丢弃。
输出：reports/tfs_score_shadow_YYYYMMDD_HHMMSS.md
"""
import os, re, sys, json, sqlite3, datetime
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(BASE_DIR, 'data', 'odds.db')
JSONL = os.path.join(BASE_DIR, 'reports', 't006_shadow_v5.jsonl')

import topo_tfs as T
from market_benchmark_hc import build_en2cn, _norm

GRID_MODEL = 't006_score_predictor_v5'
MIN_ODDS_FLOOR = 500.0   # 市场缺失格子的赔率下限代理（隐含概率 ~0.002）


def load_model_grid(conn, match_id):
    rows = conn.execute(
        "SELECT prediction_type, probability FROM model_predictions "
        "WHERE model_name=? AND is_replay=0 AND match_id=? AND prediction_type LIKE 'Score_grid_%'",
        (GRID_MODEL, match_id)).fetchall()
    g = np.full((6, 6), np.nan)
    for ptype, prob in rows:
        m = re.match(r'Score_grid_(\d+)_(\d+)', ptype)
        if m:
            g[int(m.group(1)), int(m.group(2))] = float(prob)
    if np.isnan(g).any() or g.sum() <= 0:
        return None
    return g / g.sum()


def build_grid_index(conn):
    """全部 v5 网格，键=规范化键（date_stdhome_stdaway，经 team_mapping 归一）。"""
    index = {}
    for (mid,) in conn.execute(
            "SELECT DISTINCT match_id FROM model_predictions "
            "WHERE model_name=? AND is_replay=0 AND prediction_type='Score_grid_0_0'",
            (GRID_MODEL,)).fetchall():
        index[mid] = None   # 先登记键，网格按需加载
    return index


def load_name_map(conn):
    """任意队名 → 统一规范名（中文优先）。

    基于 market_benchmark_hc.build_en2cn（team_mapping 优先，odds500_match 兜底），
    追加：① 中文名 identity；② 二次解析（standard_name 本身若是英文键则透传到中文）。
    """
    m = build_en2cn(conn)
    for v in list(m.values()):
        m.setdefault(_norm(v), v)          # 中文标准名 → 自身
    # 二次解析：value 的 norm 若仍是键，说明 standard_name 不统一，透传一层
    for k, v in list(m.items()):
        v2 = m.get(_norm(v))
        if v2 and v2 != v:
            m[k] = v2
    return m


def canon_key(match_id, name_map):
    """match_id(date_home_away) → (date, std_home, std_away)；无法解析返回 None。"""
    parts = str(match_id).split('_')
    if len(parts) < 3:
        return None
    date, home, away = parts[0], '_'.join(parts[1:-1]), parts[-1]
    h = name_map.get(_norm(home), _norm(home))
    a = name_map.get(_norm(away), _norm(away))
    return (date, h, a)


def load_market_grid(conn, cn_id, match_date):
    """市场隐含比分网格：比赛日前最后快照，1/odds 去水，缺失格 1/MIN_ODDS_FLOOR。"""
    rows = conn.execute(
        "SELECT score, odds, timestamp FROM score_history "
        "WHERE match_id=? AND odds>1 AND timestamp<? ORDER BY timestamp DESC",
        (cn_id, f'{match_date} 00:00:00')).fetchall()
    if not rows:
        return None, 0
    latest_ts = rows[0][2]
    g = np.full((6, 6), 1.0 / MIN_ODDS_FLOOR)
    n_cov = 0
    for sc, odds, ts in rows:
        if ts != latest_ts:
            continue
        m = re.match(r'^(\d+)\s*[:：-]\s*(\d+)$', str(sc).strip())
        if not m:
            continue  # '胜其他'等不入格
        h, a = int(m.group(1)), int(m.group(2))
        if h <= 5 and a <= 5:
            g[h, a] = 1.0 / float(odds)
            n_cov += 1
    if n_cov < 10:   # 覆盖不足视为不可用
        return None, n_cov
    return g / g.sum(), n_cov


def load_outcomes(conn):
    out = {}
    for mid, sc in conn.execute(
            "SELECT match_id, actual_score FROM post_match_review WHERE actual_score IS NOT NULL"):
        out[mid] = sc
    return out


def parse_score(s):
    m = re.match(r'(\d+)\s*[:：-]\s*(\d+)', str(s or ''))
    return (int(m.group(1)), int(m.group(2))) if m else None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    en2cn = build_en2cn(conn)
    outcomes = load_outcomes(conn)
    name_map = load_name_map(conn)
    # 网格索引：规范化键 → 实际 match_id（jsonl 含中文名/异体名，需桥接）
    grid_index = {}
    for (mid,) in conn.execute(
            "SELECT DISTINCT match_id FROM model_predictions "
            "WHERE model_name=? AND is_replay=0 AND prediction_type='Score_grid_0_0'",
            (GRID_MODEL,)).fetchall():
        ck = canon_key(mid, name_map)
        if ck:
            grid_index[ck] = mid

    # jsonl 去重（按 match_id 取最后一条，与 append_shadow_v5 覆盖语义一致）
    records = {}
    with open(JSONL, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                d = json.loads(ln)
                records[d['match_id']] = d

    excluded = {'v5_error': 0, 'model_grid_missing': 0, 'cn_map_missing': 0,
                'market_grid_missing': 0, 'no_outcome': 0, 'bad_score': 0}
    rows = []
    for mid, d in records.items():
        v5 = d.get('v5')
        if not (isinstance(v5, dict) and 'top5' in v5):
            excluded['v5_error'] += 1
            continue
        ck = canon_key(mid, name_map)
        grid_mid = grid_index.get(ck) if ck else None
        gm = load_model_grid(conn, grid_mid) if grid_mid else None
        if gm is None:
            excluded['model_grid_missing'] += 1
            continue
        date, home_en, away_en = d['match_date'], d.get('home', ''), d.get('away', '')
        std_names = set(name_map.values())
        hc = en2cn.get(_norm(home_en)) or (home_en if home_en in std_names else None)
        ac = en2cn.get(_norm(away_en)) or (away_en if away_en in std_names else None)
        if not hc or not ac:
            excluded['cn_map_missing'] += 1
            continue
        gk, n_cov = load_market_grid(conn, f'{date}_{hc}_{ac}', date)
        if gk is None:
            excluded['market_grid_missing'] += 1
            continue
        sc = parse_score(outcomes.get(mid))
        if sc is None:
            excluded['no_outcome'] += 1
            continue
        r = T.compute_tfs(gm, gk)
        top5_scores = [x['score'] for x in v5['top5']]
        top5_hit = f'{sc[0]}:{sc[1]}' in [s.replace('-', ':').replace('：', ':') for s in top5_scores]
        rows.append({'match_id': mid, 'league': d.get('league', '?'),
                     'tfs': r['tfs'], 'wasserstein': r['wasserstein'],
                     'n_modes_model': r['n_modes_model'], 'n_modes_mkt': r['n_modes_ref'],
                     'top4_model': r['top4_mass_model'], 'top4_mkt': r['top4_mass_ref'],
                     'diag_model': r['diag_mass_model'], 'diag_mkt': r['diag_mass_ref'],
                     'top5_hit': bool(top5_hit), 'mkt_cov': n_cov})

    n = len(rows)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(BASE_DIR, 'reports', f'tfs_score_shadow_{ts}.md')

    L = []
    L.append('# T-006 v5 Shadow 比分矩阵 TFS（拓扑保真度）回算报告\n')
    L.append(f'> 生成: {ts} ｜ D-20261006-052 轨 A ｜ 口径: 模型网格(model_predictions v5 非回放) vs 市场隐含网格(score_history 赛前最后快照去水)\n')
    L.append('## 覆盖\n')
    L.append(f'- jsonl 记录: {len(records)} 场（去重后）')
    L.append(f'- 纳入计算: **{n}** 场')
    L.append(f'- 剔除: ' + '，'.join(f'{k}={v}' for k, v in excluded.items()) + '\n')
    if n:
        tfs = np.array([r['tfs'] for r in rows])
        W = np.array([r['wasserstein'] for r in rows])
        L.append('## TFS 分布\n')
        L.append(f'- 均值 {tfs.mean():.4f} ｜ 中位 {np.median(tfs):.4f} ｜ P10 {np.percentile(tfs,10):.4f} ｜ P90 {np.percentile(tfs,90):.4f}')
        L.append(f'- Wasserstein 均值 {W.mean():.4f}\n')
        # TFS 三分位 vs top5 命中
        L.append('## TFS 三分位 × top5 命中率（结构相似是否对应预测命中）\n')
        q1, q2 = np.percentile(tfs, [33.3, 66.7])
        L.append('| TFS 档 | n | top5 命中率 | 平均 W | 模型众数均值 | 市场众数均值 |')
        L.append('|---|---|---|---|---|---|')
        for name, lo, hi in [('低', -1, q1), ('中', q1, q2), ('高', q2, 2)]:
            sel = [r for r in rows if lo < r['tfs'] <= hi]
            if sel:
                L.append(f"| {name} ({lo:.2f}~{hi:.2f}) | {len(sel)} | {np.mean([r['top5_hit'] for r in sel]):.3f} "
                         f"| {np.mean([r['wasserstein'] for r in sel]):.4f} "
                         f"| {np.mean([r['n_modes_model'] for r in sel]):.2f} | {np.mean([r['n_modes_mkt'] for r in sel]):.2f} |")
        # 按联赛
        L.append('\n## 分联赛\n')
        L.append('| 联赛 | n | TFS 均值 | top5 命中 |')
        L.append('|---|---|---|---|')
        for lg in sorted({r['league'] for r in rows}):
            sel = [r for r in rows if r['league'] == lg]
            L.append(f"| {lg} | {len(sel)} | {np.mean([r['tfs'] for r in sel]):.4f} | {np.mean([r['top5_hit'] for r in sel]):.3f} |")
        # 结构离群 top10
        L.append('\n## 结构离群（TFS 最低 10 场，建议人工抽查）\n')
        L.append('| match_id | TFS | W | 众数(模型/市场) | top4质量(模型/市场) |')
        L.append('|---|---|---|---|---|')
        for r in sorted(rows, key=lambda x: x['tfs'])[:10]:
            L.append(f"| {r['match_id']} | {r['tfs']:.3f} | {r['wasserstein']:.3f} "
                     f"| {r['n_modes_model']}/{r['n_modes_mkt']} | {r['top4_model']:.2f}/{r['top4_mkt']:.2f} |")
        # 集中度对比
        L.append('\n## 结构量对比（模型 vs 市场）\n')
        L.append(f"- top4 质量: 模型 {np.mean([r['top4_model'] for r in rows]):.3f} vs 市场 {np.mean([r['top4_mkt'] for r in rows]):.3f}")
        L.append(f"- 平局带质量: 模型 {np.mean([r['diag_model'] for r in rows]):.3f} vs 市场 {np.mean([r['diag_mkt'] for r in rows]):.3f}")
        L.append(f"- 众数个数: 模型 {np.mean([r['n_modes_model'] for r in rows]):.2f} vs 市场 {np.mean([r['n_modes_mkt'] for r in rows]):.2f}")
        L.append('\n## 解读指南\n')
        L.append('- TFS=1 结构完全一致；<0.8 视为结构偏离，建议人工复核该场预测。')
        L.append('- 高 TFS 档 top5 命中率应不低于低 TFS 档；若无差异，说明结构相似性对命中无区分度（TFS 仅作体检指标）。')
        L.append('- 模型众数/集中度系统性偏离市场 → 提示 λ 或 DC 结构存在偏置。')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'报告: {out_path}')
    print(f'纳入 {n} 场, 剔除 {excluded}')
    if n:
        print(f"TFS 均值 {np.mean([r['tfs'] for r in rows]):.4f}")
    conn.close()


if __name__ == '__main__':
    main()
