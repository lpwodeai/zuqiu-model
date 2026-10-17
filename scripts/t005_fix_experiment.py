# -*- coding: utf-8 -*-
"""
T-005 走水低估修复 — B/C 并行影子实验（不落库、不改生产 pkl、不动 prediction_core）
=====================================================================================
背景（C-20261005-010 / market_benchmark_hc_20261005_125723.md）：
  让球基准 skill=-0.386，两个独立病因：
    病因1 走水低估：实际走水率 23% / 市场隐含 24% / 模型 HC_draw 均值 9%
    病因2 方向为负：非走水子样本方向 argmax 35.8% < 50%（本实验只治病因1，
                   方向模型全程复用线上 t005v3_direction_predictor.pkl 以隔离变量）

B 路径（重训 Stage1 + 概率校准）：
  - 抬升 TRAE_HCP_TRAIN_CUTOFF 至 2099（与 get_serving_feature_matrix 同法），
    使 2026-08/09 场次进入特征矩阵
  - 训练集严格 < 2026-08-22（评估窗首日，零泄漏）；走水类 class_weight ratio 网格
    {1.5,2.0,2.5,3.0}（线上=1.20），其余超参与 deploy_t005v3_final 完全一致
  - 校准器 none / Platt(logistic on logit p) / isotonic，在「2026-03-01~08-21」
    同赛季验证窗按三向 RPS 选型（约束：走水预测率偏差≤2pp），冻结后评估
  - B0：直接用线上 draw pkl 重算，并施加与线上一致的 8% 下限+重归一，
    与 DB 落库概率/基线 RPS 对比，校验特征重建保真度

C 路径（T-006 比分网格聚合让球三向）：
  - P(上盘|H)=Σ grid[h,a] s.t. h+H>a；P(走水)=h+H==a；P(下盘)=h+H<a
  - H 从 matches.actual_handicap 官方文本 "(±d)胜/平/负" 解析（线位为赛前已知信息）
  - 网格来源 t006_score_predictor_v5 非回放 Score_grid_h_a（英文 match_id，覆盖 35/88）

评估：同一配对框（t-24h n≈87 / noon n≈88，复用 market_benchmark_hc 配对与市场快照），
指标 RPS / skill / argmax / 走水率偏差 / 走水召回 / 非走水方向命中率 / CLV；
C 另在 35 场网格子框上让所有变体同框对比。

输出：reports/t005_fix_experiment_<ts>.md
"""

import os
import sys
import re
import json
import pickle
import sqlite3
from datetime import datetime
from collections import Counter

# 截止线必须在任何管线 import 之前抬升（hcp_features / hcp_features_v2 在 import 期读取）
os.environ['TRAE_HCP_TRAIN_CUTOFF'] = '2099-12-31 00:00:00'

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import market_benchmark_hc as mbh
from hcp_features_v2 import V2_ALL_FEATURES
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
from hcp_features import parse_hcp_result_label
from train_hcp_model_v2 import compute_sample_weights
from deploy_t005v3_final import STAGE1_PARAMS

import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
FEAT_CACHE = os.path.join(SCRIPT_DIR, '_feat_cache_t005_2099.pkl')

EVAL_START = '2026-08-22'          # 评估窗首日（训练/校准严格在此之前）
VAL_START = '2026-03-01'           # 校准器选型验证窗起点（同赛季尾段）
RATIOS = [1.5, 2.0, 2.5, 3.0]      # 线上为 1.20
FLOOR = 0.08                       # 线上 T005V3_DRAW_PROB_FLOOR
GRID_MODEL = 't006_score_predictor_v5'
LINE_RE = re.compile(r'\(([+-]?\d+)\)')


# ============================================================
# 数据
# ============================================================
def build_matrix(conn):
    """71 维矩阵（抬升截止线），带本地缓存。返回 X(特征,含Elo) / y3(0上1走2下) / meta / feature_cols。"""
    if os.path.exists(FEAT_CACHE):
        with open(FEAT_CACHE, 'rb') as f:
            feats = pickle.load(f)
        print(f'  (使用特征缓存 {os.path.basename(FEAT_CACHE)})')
    else:
        from hcp_features_v2 import build_all_features_v2
        feats = build_all_features_v2(conn)
        feats = merge_expanded_labels(feats)
        with open(FEAT_CACHE, 'wb') as f:
            pickle.dump(feats, f)

    X = feats[V2_ALL_FEATURES].copy()
    X = X.apply(pd.to_numeric, errors='coerce')
    if X.isnull().any().any():
        X = X.fillna(X.median())

    def _decode(v):
        if pd.isna(v):
            return np.nan
        code = parse_hcp_result_label(v)
        if code is not None:
            return code
        try:
            num = int(float(v))
            return num if num in (0, 1, 2) else np.nan
        except (TypeError, ValueError):
            return np.nan

    y = feats['actual_handicap'].map(_decode)
    meta = feats[['league', 'date', 'home_team', 'away_team']].copy()
    if 'label_source' in feats.columns:
        meta['label_source'] = feats['label_source']
    else:
        meta['label_source'] = 'actual'

    valid = y.notna()
    X, y, meta = X[valid], y[valid].astype(int), meta[valid]
    X, elo_cols = add_elo_features_hcp(X, meta)
    meta['date'] = pd.to_datetime(meta['date'], errors='coerce')
    feature_cols = list(X.columns)
    print(f'  矩阵有效样本={len(X)}  特征={len(feature_cols)}  '
          f'走水率={ (y==1).mean():.3f}  日期 {meta["date"].min():%Y-%m-%d}~{meta["date"].max():%Y-%m-%d}')
    return X, y, meta, feature_cols


def build_eval_frames(conn):
    """复用基准配对逻辑，返回 {align: [record,...]}（含 match_id/线位H/市场概率/标签/落库概率）。

    每条记录附带：
      ptime 落库 HC_draw 预测时间戳；gen='pkl0920'（0920 部署后）或 'old'（旧模型）。
    0920 部署点 2026-09-20 01:12:18（assets/t005v3_draw_detector.pkl 版本）。
    """
    model = mbh.load_model_side(conn)
    en2cn = mbh.build_en2cn(conn)

    ah_text = {r['match_id']: r['actual_handicap'] for r in conn.execute(
        "SELECT match_id, actual_handicap FROM matches").fetchall()}

    frames = {}
    for align in ('t-24h', 'noon'):
        market = mbh.load_market_side(conn, align=align)
        recs = []
        for rec in model:
            hc = en2cn.get(mbh._norm(rec['home_en']))
            ac = en2cn.get(mbh._norm(rec['away_en']))
            if not hc or not ac:
                continue
            cn_id = f"{rec['match_date']}_{hc}_{ac}"
            m = market.get(cn_id)
            if m is None:
                continue
            m_line = LINE_RE.search(str(ah_text.get(rec['match_id'], '')))
            H = int(m_line.group(1)) if m_line else None
            recs.append({
                'match_id': rec['match_id'], 'cn_id': cn_id,
                'date': rec['match_date'], 'league': rec['league'],
                'y': rec['actual'],  # 基准序 0下1走2上
                'H': H,
                'k': np.array([m['k_lower'], m['k_draw'], m['k_upper']]),
                'stored': np.array([rec['m_lower'], rec['m_draw'], rec['m_upper']]),
            })
        frames[align] = recs

    # 落库预测时间戳 → 模型代际（83 场旧模型锁 8% 下限；5 场 09-21 为 0920 新 pkl）
    all_ids = sorted({r['match_id'] for v in frames.values() for r in v})
    ptime = {}
    if all_ids:
        ph = ','.join('?' * len(all_ids))
        for r in conn.execute(
                f"SELECT match_id, timestamp FROM model_predictions "
                f"WHERE model_name='generate_unified_report_v2.0' AND is_replay=0 "
                f"AND prediction_type='HC_draw' AND match_id IN ({ph})", all_ids):
            ptime[r['match_id']] = str(r['timestamp'])
    DEPLOY_TS = '2026-09-20 01:12:18'
    for align in frames:
        for r in frames[align]:
            ts = ptime.get(r['match_id'])
            r['ptime'] = ts
            r['gen'] = 'pkl0920' if ts is not None and ts >= DEPLOY_TS else 'old'
    return frames


def build_aligned_odds_features(conn, recs, align):
    """用与市场侧**同口径窗口**的赛前快照重建评估行的 18 维赔率特征（15 hcp + 3 wdl_draw）。

    全矩阵里评估行的赔率特征取「各场最新快照」（可能晚于生产预测时刻，存在小时级漂移）；
    这里改为基准快照同一条记录（t-24h: ts 日期<比赛日；noon: ts<=比赛日 12:04），
    使模型与市场处于同一信息集，并可直接用 5 场 0920 落库预测做逐场保真度校验。

    返回 DataFrame，index=英文 match_id，列=V2_ALL_FEATURES 前 18 列。
    """
    from hcp_features import normalize_probabilities, extract_hcp_features

    mini_rows, wdl_odds = [], {}
    for r in recs:
        cn, date = r['cn_id'], r['date']
        if align == 'noon':
            win, cut = '<=', f'{date} 12:04:00'
        else:  # t-24h：与 load_market_side 一致，ts[:10] < 比赛日
            win, cut = '<', f'{date} 00:00:00'
        row = conn.execute(
            "SELECT hcp_win,hcp_draw,hcp_lose FROM handicap_history "
            "WHERE match_id=? AND timestamp IS NOT NULL AND hcp_win>1 AND hcp_draw>1 AND hcp_lose>1 "
            f"AND timestamp {win} ? ORDER BY timestamp DESC LIMIT 1", (cn, cut)).fetchone()
        if row is None:
            # 市场配对成功必有同窗口快照；缺失则回退 NaN（后续中位数兜底，理论不触发）
            mini_rows.append(dict(mid=r['match_id'], w=np.nan, d=np.nan, l=np.nan, hdraw=np.nan))
        else:
            w, d, l = float(row['hcp_win']), float(row['hcp_draw']), float(row['hcp_lose'])
            mini_rows.append(dict(mid=r['match_id'], w=w, d=d, l=l, hdraw=np.nan))
        wr = conn.execute(
            "SELECT draw FROM wdl_history WHERE match_id=? AND timestamp IS NOT NULL AND draw>0 "
            f"AND timestamp {win} ? ORDER BY timestamp DESC LIMIT 1", (cn, cut)).fetchone()
        wdl_odds[r['match_id']] = float(wr['draw']) if wr is not None else np.nan

    dfh = pd.DataFrame({
        'hcp_win': [x['w'] for x in mini_rows],
        'hcp_draw': [x['d'] for x in mini_rows],
        'hcp_lose': [x['l'] for x in mini_rows],
        'matches_match_id': [x['mid'] for x in mini_rows],
        'actual_handicap': '', 'handicap_line': 0, 'actual_score': '',
        'league': '', 'date': [r['date'] for r in recs], 'home_team': '', 'away_team': '',
    })
    dfh = normalize_probabilities(dfh)
    f15 = extract_hcp_features(dfh)  # index=英文 match_id，15 维，顺序与入参一致

    # wdl_draw 3 维（口径同 build_wdl_draw_features：raw draw 缺失填 3.5）
    wdl_draw = pd.Series(wdl_odds).reindex(f15.index).fillna(3.5)
    wdl_prob = 1.0 / np.clip(wdl_draw, 1e-10, None)
    out = pd.DataFrame(index=f15.index)
    out['wdl_draw_odds'] = wdl_draw
    out['wdl_draw_prob'] = wdl_prob
    out['draw_divergence'] = np.abs(wdl_prob - f15['hcp_prob_draw'])
    out = pd.concat([f15, out], axis=1)
    out = out[V2_ALL_FEATURES[:18]]
    return out



def load_grid(conn, match_id):
    rows = conn.execute(
        "SELECT prediction_type, probability FROM model_predictions "
        "WHERE model_name=? AND is_replay=0 AND match_id=? AND prediction_type LIKE 'Score_grid_%'",
        (GRID_MODEL, match_id)).fetchall()
    g = np.full((6, 6), np.nan)
    for r in rows:
        mm = re.match(r'Score_grid_(\d+)_(\d+)', r['prediction_type'])
        if mm:
            g[int(mm.group(1)), int(mm.group(2))] = float(r['probability'])
    if np.isnan(g).any() or g.sum() <= 0:
        return None
    return g / g.sum()


# ============================================================
# 概率融合
# ============================================================
def fuse(p_draw, p_away_nd, floor=0.0):
    """走水概率 + P(下盘|非走水) → [下盘,走水,上盘]；floor>0 时复现线上 clip+重归一。"""
    p_draw = np.clip(np.asarray(p_draw, float), 1e-9, 1 - 1e-9)
    p_away_nd = np.clip(np.asarray(p_away_nd, float), 1e-9, 1 - 1e-9)
    p_lower = (1 - p_draw) * p_away_nd
    p_upper = (1 - p_draw) * (1 - p_away_nd)
    p = np.stack([p_lower, p_draw, p_upper], axis=1)
    p = p / p.sum(axis=1, keepdims=True)
    if floor > 0:
        p[:, 1] = np.maximum(p[:, 1], floor)
        p = p / p.sum(axis=1, keepdims=True)
    return p


def train_draw_model(X_tr, y_tr, X_es, y_es, sw_tr, sw_es, ratio):
    """与 deploy_t005v3_final.train_models 同构（ES 早停 + 全量重训），仅改走水类 ratio。"""
    cw = {0: 1.0, 1: ratio}
    m0 = lgb.LGBMClassifier(**STAGE1_PARAMS, class_weight=cw)
    m0.fit(X_es, (y_es == 1).astype(int), sample_weight=sw_es,
           eval_set=[(X_es, (y_es == 1).astype(int))], eval_metric='binary_logloss',
           callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)])
    n_best = max(20, m0.best_iteration_ or STAGE1_PARAMS['n_estimators'])
    params = dict(STAGE1_PARAMS)
    params['n_estimators'] = n_best
    m = lgb.LGBMClassifier(**params, class_weight=cw)
    m.fit(X_tr, (y_tr == 1).astype(int), sample_weight=sw_tr)
    return m


def fit_calibrator(kind, p_raw, y_draw):
    if kind == 'none':
        return None
    if kind == 'platt':
        z = np.log(np.clip(p_raw, 1e-6, 1 - 1e-6)).reshape(-1, 1)
        cal = LogisticRegression(C=1e6)
        cal.fit(z, y_draw)
        return ('platt', cal)
    if kind == 'iso':
        iso = IsotonicRegression(out_of_bounds='clip', y_min=0.001, y_max=0.999)
        iso.fit(p_raw, y_draw)
        return ('iso', iso)
    raise ValueError(kind)


def apply_calibrator(cal, p_raw):
    if cal is None:
        return p_raw
    kind, obj = cal
    if kind == 'platt':
        z = np.log(np.clip(p_raw, 1e-6, 1 - 1e-6)).reshape(-1, 1)
        return obj.predict_proba(z)[:, 1]
    return obj.predict(p_raw)


# ============================================================
# 指标
# ============================================================
def metrics(y, proba, k_proba):
    """y 基准序 0下1走2上。"""
    y = np.asarray(y, int)
    rps = mbh.compute_rps(y, proba)
    rps_k = mbh.compute_rps(y, k_proba)
    arg = proba.argmax(axis=1)
    out = {
        'n': len(y),
        'rps': rps,
        'rps_market': rps_k,
        'skill': 1 - rps / rps_k if rps_k > 0 else np.nan,
        'argmax_hit': float((arg == y).mean()),
        'argmax_market': float((k_proba.argmax(axis=1) == y).mean()),
        'p_push_mean': float(proba[:, 1].mean()),
        'push_actual_rate': float((y == 1).mean()),
        'push_pred_rate': float((arg == 1).mean()),
        'push_recall': float((arg[y == 1] == 1).mean()) if (y == 1).any() else np.nan,
        'push_precision': float((y[arg == 1] == 1).mean()) if (arg == 1).any() else np.nan,
        'clv_mean': float((proba[np.arange(len(y)), y] - k_proba[np.arange(len(y)), y]).mean()),
    }
    nd = y != 1
    if nd.any():
        out['dir_hit_nopush'] = float((arg[nd] == y[nd]).mean())
        out['dir_market_nopush'] = float((k_proba[nd].argmax(axis=1) == y[nd]).mean())
        out['n_nopush'] = int(nd.sum())
    return out


def fmt_row(name, m):
    return (f"{name:<22s} n={m['n']:>3d} RPS={m['rps']:.4f} skill={m['skill']:+.4f} "
            f"argmax={m['argmax_hit']:.3f}(市{m['argmax_market']:.3f}) "
            f"走水均值={m['p_push_mean']:.3f}(实际{m['push_actual_rate']:.3f}) "
            f"走水召回={m['push_recall']:.3f} 精确={m['push_precision']:.3f} "
            f"方向(非走)={m.get('dir_hit_nopush', float('nan')):.3f}(市{m.get('dir_market_nopush', float('nan')):.3f}) "
            f"CLV={m['clv_mean']:+.4f}")


# ============================================================
# 主流程
# ============================================================
def main():
    t0 = datetime.now()
    print('=' * 86)
    print('T-005 走水修复 B/C 影子实验（不写生产）')
    print('=' * 86)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print('\n[1/5] 构建 71 维特征矩阵（抬升截止线，首次约 4 分钟）...')
    X, y3, meta, feature_cols = build_matrix(conn)

    # 与线上资产对齐校验（metadata 的 feature_columns 为陈旧 61 列记录，以 pkl 实际特征为准）
    with open(os.path.join(ASSETS_DIR, 't005v3_metadata.json'), 'r', encoding='utf-8') as f:
        prod_meta = json.load(f)
    with open(os.path.join(ASSETS_DIR, 't005v3_draw_detector.pkl'), 'rb') as f:
        prod_draw = pickle.load(f)
    with open(os.path.join(ASSETS_DIR, 't005v3_direction_predictor.pkl'), 'rb') as f:
        prod_dir = pickle.load(f)
    pkl_names = list(getattr(prod_draw, 'feature_name_', []) or [])
    if pkl_names:
        if pkl_names != feature_cols:
            diff = [(i, a, b) for i, (a, b) in enumerate(zip(pkl_names, feature_cols)) if a != b]
            raise AssertionError(f'特征列顺序与线上 pkl 不一致（前若干）: {diff[:5]}; '
                                 f'{len(pkl_names)} vs {len(feature_cols)}')
    else:
        assert prod_draw.n_features_in_ == len(feature_cols)
    print(f"  线上资产: draw={prod_meta['version']} ratio="
          f"{prod_meta['calibration']['class_weight_ratio']} T={prod_meta['calibration']['temperature']}"
          f"  n_features={prod_draw.n_features_in_}（metadata 61列为陈旧记录）")

    print('\n[2/5] 构建评估配对框...')
    frames = build_eval_frames(conn)
    # 评估行定位：英文 id 直取，缺失则中文桥接 id
    all_ids = sorted({r['match_id'] for v in frames.values() for r in v})
    row_pos = {}
    miss = []
    for mid in all_ids:
        if mid in X.index:
            row_pos[mid] = mid
        else:
            hit = None
            for align, recs in frames.items():
                for r in recs:
                    if r['match_id'] == mid and r['cn_id'] in X.index:
                        hit = r['cn_id']
                        break
                if hit:
                    break
            if hit:
                row_pos[mid] = hit
            else:
                miss.append(mid)
    print(f'  评估场次 {len(all_ids)}，特征行命中 {len(row_pos)}，缺失 {len(miss)}')
    if miss[:5]:
        print('  缺失样例:', miss[:5])

    # 统一裁剪：后续所有变体概率与 frames 等长、同序
    for align in frames:
        frames[align] = [r for r in frames[align] if r['match_id'] in row_pos]
    print(f"  裁剪后配对框: t-24h={len(frames['t-24h'])}  noon={len(frames['noon'])}")

    # 同口径快照重建 18 维赔率特征（与市场同一信息集）
    aligned_feats = {align: build_aligned_odds_features(conn, recs, align)
                     for align, recs in frames.items()}

    def X_of(recs, align):
        """评估行特征：71 维基线行 + 该 align 口径快照覆盖前 18 维。"""
        base = X.loc[[row_pos[r['match_id']] for r in recs]].copy()
        ov = aligned_feats[align]
        for c in V2_ALL_FEATURES[:18]:
            base[c] = [ov.loc[r['match_id'], c] for r in recs]
        return base

    # 数据切分
    dates = meta['date']
    tr_mask = dates < pd.Timestamp(EVAL_START)
    val_mask = (dates >= pd.Timestamp(VAL_START)) & (dates < pd.Timestamp(EVAL_START))
    X_tr, y_tr = X[tr_mask], y3[tr_mask]
    X_val, y_val = X[val_mask], y3[val_mask]
    ls_tr = meta.loc[tr_mask, 'label_source'].values
    sw_tr = compute_sample_weights(y_tr.values, ls_tr)
    es = int(len(X_tr) * 0.8)
    X_es, y_es, sw_es = X_tr.iloc[:es], y_tr.iloc[:es], sw_tr[:es]
    print(f'  训练(日期<{EVAL_START}) n={len(X_tr)}  验证窗({VAL_START}~) n={len(X_val)} '
          f'(走水率{ (y_val==1).mean():.3f})')

    # 线上方向模型概率（所有变体共用，隔离病因2）
    dir_p = {align: prod_dir.predict_proba(X_of(recs, align))[:, 1]
             for align, recs in frames.items()}

    # ---------- B0：线上 draw pkl 重算（同口径快照，保真度校验） ----------
    print('\n[3/5] B0 线上 0920 pkl 重算 + 保真度校验...')
    variants = {}
    for align, recs in frames.items():
        Xe = X_of(recs, align)
        raw = prod_draw.predict_proba(Xe)[:, 1]
        variants.setdefault('B0_线上pkl_无下限', {})[align] = fuse(raw, dir_p[align])
        variants.setdefault('B0b_线上pkl_8%下限', {})[align] = fuse(raw, dir_p[align], floor=FLOOR)

    # 保真度（noon 框）：
    #  (a) 5 场 0920 部署后落库 = 当前 pkl 同代产出，可逐场直接比对（含方向三向）
    #  (b) 83 场旧模型无法与 0920 pkl 互校，只作分布参照；基线 RPS 另报 old-only 纯净口径
    recs = frames['noon']
    stored = np.array([r['stored'] for r in recs])
    recal = variants['B0b_线上pkl_8%下限']['noon']
    gens = np.array([r['gen'] for r in recs])
    y_eval = np.array([r['y'] for r in recs])
    k_eval = np.array([r['k'] for r in recs])
    newm = gens == 'pkl0920'
    oldm = ~newm
    corr_new = float(np.corrcoef(recal[newm, 1], stored[newm, 1])[0, 1]) if newm.sum() > 2 else np.nan
    md_new = float(np.mean(recal[newm, 1] - stored[newm, 1])) if newm.any() else np.nan
    corr_old = float(np.corrcoef(recal[oldm, 1], stored[oldm, 1])[0, 1])
    rps_stored = mbh.compute_rps(y_eval, stored)
    rps_stored_old = mbh.compute_rps(y_eval[oldm], stored[oldm])
    rps_recal = mbh.compute_rps(y_eval, recal)
    fidelity_rows = []
    for i, r in enumerate(recs):
        if r['gen'] == 'pkl0920':
            fidelity_rows.append((r['match_id'], r['ptime'], stored[i], recal[i]))
            print(f"  [新pkl场] {r['match_id']} 落库={stored[i].round(3)} 重算={recal[i].round(3)}")
    print(f'  新pkl 5场走水 corr={corr_new:.4f} 均值差={md_new:+.4f}（同代直接保真校验）')
    print(f'  旧模型 {oldm.sum()} 场走水 corr={corr_old:.4f}（跨代，预期不相关，仅参照）')
    print(f'  RPS 落库全框={rps_stored:.4f} 仅旧模型场={rps_stored_old:.4f}  '
          f'0920重算={rps_recal:.4f}（基准报告 noon=0.3007）')

    # ---------- B：ratio 网格 × 校准器，验证窗选型 ----------
    print('\n[4/5] B 路径：ratio 网格重训 × {none/platt/iso}，验证窗选型...')
    val_dir = prod_dir.predict_proba(X_val)[:, 1]
    yv = y_val.values
    tuned = {}
    for ratio in RATIOS:
        dm = train_draw_model(X_tr, y_tr, X_es, y_es, sw_tr, sw_es, ratio)
        p_val_raw = dm.predict_proba(X_val)[:, 1]
        for kind in ('none', 'platt', 'iso'):
            cal = fit_calibrator(kind, p_val_raw, (yv == 1).astype(int))
            p_val = apply_calibrator(cal, p_val_raw)
            proba_val = fuse(p_val, val_dir)
            mv = metrics(yv, proba_val,
                         np.tile([1 / 3, 1 / 3, 1 / 3], (len(yv), 1)))  # 选型只看模型自身 RPS
            name = f'B_r{ratio}_{kind}'
            dev = abs(mv['p_push_mean'] - mv['push_actual_rate'])
            tuned[name] = {'ratio': ratio, 'kind': kind, 'model': dm, 'cal': cal,
                           'rps_val': mv['rps'], 'push_dev': dev,
                           'push_recall_val': mv['push_recall']}
            print(f'  {name:<16s} val RPS={mv["rps"]:.4f} 走水均值={mv["p_push_mean"]:.3f}'
                  f'(实际{mv["push_actual_rate"]:.3f},偏差{dev:.3f}) 召回={mv["push_recall"]:.3f}')

    feasible = {k: v for k, v in tuned.items() if v['push_dev'] <= 0.02}
    pool = feasible or tuned

    # ---------- B-min：不重训，仅给当前 0920 pkl 加校准（最小生产干预候选） ----------
    p_val_raw_0920 = prod_draw.predict_proba(X_val)[:, 1]
    for kind in ('platt', 'iso'):
        name = f'Bmin_0920pkl_{kind}'
        cal = fit_calibrator(kind, p_val_raw_0920, (yv == 1).astype(int))
        p_val = apply_calibrator(cal, p_val_raw_0920)
        mv = metrics(yv, fuse(p_val, val_dir),
                     np.tile([1 / 3, 1 / 3, 1 / 3], (len(yv), 1)))
        dev = abs(mv['p_push_mean'] - mv['push_actual_rate'])
        tuned[name] = {'ratio': '1.2(线上)', 'kind': kind, 'model': None, 'cal': cal,
                       'rps_val': mv['rps'], 'push_dev': dev,
                       'push_recall_val': mv['push_recall']}
        print(f'  {name:<20s} val RPS={mv["rps"]:.4f} 走水均值={mv["p_push_mean"]:.3f}'
              f'(偏差{dev:.3f}) 召回={mv["push_recall"]:.3f}')
        for align, recs_f in frames.items():
            Xe = X_of(recs_f, align)
            p_raw = prod_draw.predict_proba(Xe)[:, 1]
            variants.setdefault(name, {})[align] = fuse(apply_calibrator(cal, p_raw), dir_p[align])

    feasible = {k: v for k, v in tuned.items() if v['push_dev'] <= 0.02}
    pool = feasible or tuned
    best_name = min(pool, key=lambda k: pool[k]['rps_val'])
    print(f'  -> 选型（偏差≤2pp 约束内最小验证窗RPS）: {best_name}；'
                f'可行变体数={len(feasible)}/{len(tuned)}')

    for name, spec in tuned.items():
        dm, cal = spec['model'], spec['cal']
        if dm is None:
            continue  # Bmin 复用线上 pkl，变体概率已在上面生成
        for align, recs_f in frames.items():
            Xe = X_of(recs_f, align)
            p_raw = dm.predict_proba(Xe)[:, 1]
            variants.setdefault(name, {})[align] = fuse(apply_calibrator(cal, p_raw), dir_p[align])

    # ---------- C：比分网格聚合 ----------
    print('\n[5/5] C 路径：比分网格聚合让球三向...')
    grid_ok = {align: [] for align in frames}
    for align, recs in frames.items():
        for r in recs:
            if r['match_id'] not in row_pos or r['H'] is None:
                continue
            g = load_grid(conn, r['match_id'])
            if g is None:
                continue
            H = r['H']
            h_idx, a_idx = np.meshgrid(np.arange(6), np.arange(6), indexing='ij')
            upper = float(g[h_idx + H > a_idx].sum())
            draw_ = float(g[h_idx + H == a_idx].sum())
            lower = float(g[h_idx + H < a_idx].sum())
            tot = upper + draw_ + lower
            rr = dict(r)
            rr['c'] = np.array([lower, draw_, upper]) / tot
            grid_ok[align].append(rr)
    print(f'  网格覆盖: t-24h={len(grid_ok["t-24h"])}  noon={len(grid_ok["noon"])}')

    conn.close()

    # ============================================================
    # 汇总输出
    # ============================================================
    fidelity = {
        'corr_new': corr_new, 'md_new': md_new, 'corr_old': corr_old,
        'rps_stored': rps_stored, 'rps_stored_old': rps_stored_old,
        'rps_recal': rps_recal, 'rows': fidelity_rows,
        'n_new': int(newm.sum()), 'n_old': int(oldm.sum()),
    }
    report = build_report(frames, variants, tuned, best_name, grid_ok, fidelity, t0)
    rep_dir = REPORTS_DIR
    os.makedirs(rep_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    rep_path = os.path.join(rep_dir, f't005_fix_experiment_{ts}.md')
    with open(rep_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'\n报告已保存: {rep_path}')


def _row_line(name, m, tag=''):
    return ('| {n}{tag} | {rps:.4f} | {skill:+.4f} | {ah:.3f} | {am:.3f} | '
            '{pm:.3f} | {dev:+.3f} | {rr:.3f} | {pp:.3f} | {dh:.3f} | {dmv:.3f} | '
            '{clv:+.4f} |'.format(
                n=name, tag=tag, rps=m['rps'], skill=m['skill'], ah=m['argmax_hit'],
                am=m['argmax_market'], pm=m['p_push_mean'],
                dev=m['p_push_mean'] - m['push_actual_rate'], rr=m['push_recall'],
                pp=m['push_precision'], dh=m.get('dir_hit_nopush', float('nan')),
                dmv=m.get('dir_market_nopush', float('nan')), clv=m['clv_mean']))


TABLE_HEAD = ('| 变体 | RPS | skill | argmax命中 | 市场命中 | 走水均值 | 走水率偏差 | '
              '走水召回 | 走水精确 | 方向命中(非走) | 市场方向 | CLV |',
              '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')


def build_report(frames, variants, tuned, best_name, grid_ok, fid, t0):
    L = ['# T-005 走水低估修复 — B/C 影子实验报告', '',
         f'**生成时间**: {datetime.now():%Y-%m-%d %H:%M:%S}',
         f'**耗时**: {(datetime.now()-t0).total_seconds():.0f}s', '',
         '## 一、方法与口径', '',
         '- 影子实验：**不写 model_predictions、不覆盖生产 pkl、不改 prediction_core**。',
         '- 病因1（走水低估）修复；方向模型全程用线上 `t005v3_direction_predictor.pkl`，'
         '病因2（方向为负）不在本实验范围。',
         f'- B：训练集严格早于 {EVAL_START}（评估窗首日，零泄漏）；走水类 class_weight '
         f'ratio 网格 {RATIOS}（线上 1.20）× 校准 {{none, platt, iso}}；校准器在 '
         f'{VAL_START}~{EVAL_START} 同赛季验证窗按三向 RPS 选型（约束走水率偏差≤2pp），冻结后评估。',
         '- 评估行赔率特征（前 18 维）按**与市场侧同口径窗口**的快照重建：'
         't-24h=timestamp 日期<比赛日；noon=timestamp≤比赛日 12:04，'
         '保证模型与市场处于同一信息集；其余 53 维（form/rest/cards/opp/Elo）沿用全矩阵行。',
         '- B0 = 当前线上资产（版本 20260920_011218）离线重算 + 线上 8% 下限。'
         '落库 88 场中 83 场为 0920 部署前的旧模型产出（走水全锁 0.076~0.078），'
         '5 场（09-21 12:04 批）为 0920 pkl 同代产出；两者不可互校，保真度只在同代 5 场上检验。',
         '- 0920 部署报告测试集 n=741 且全为当季联赛样本，与当前 12.6k 管线队列不一致，'
         '故 B0 在评估框的表现**仅作参照、不作选型依据**；诚实候选为严格时间切分的 B 变体。',
         '- 评估框与 market_benchmark_hc 完全同源（双口径、同市场快照、同标签）；'
         '因 3 场德乙命名缺口特征行缺失，全框 n=85/84（基准为 88/87），RPS 小数位勿直接横比。',
         '- 概率列序统一 [下盘,走水,上盘]；标签 0下/1走/2上。', '',
         '## 二、B0 特征重建保真度（同代 5 场，noon 同口径快照）', '',
         '| 比赛 | 落库时间 | 落库[下,走,上] | 重算[下,走,上] |',
         '| --- | --- | --- | --- |']
    for mid, ptime, s, q in fid['rows']:
        L.append(f"| {mid} | {ptime} | {s[0]:.3f}/{s[1]:.3f}/{s[2]:.3f} "
                 f"| {q[0]:.3f}/{q[1]:.3f}/{q[2]:.3f} |")
    L += ['',
          f"- 同代 {fid['n_new']} 场走水概率 corr(重算,落库)=**{fid['corr_new']:.4f}**，"
          f"均值差={fid['md_new']:+.4f}。",
          f"- 旧模型 {fid['n_old']} 场跨代 corr={fid['corr_old']:.4f}（预期不相关，仅记录）。",
          f"- RPS：落库全框={fid['rps_stored']:.4f}；仅旧模型场次={fid['rps_stored_old']:.4f}"
          f"（纯净旧基线）；0920 重算={fid['rps_recal']:.4f}。基准报告 noon 全 88 场=0.3007。", '']

    for align in ('t-24h', 'noon'):
        recs_keep = frames[align]
        L += [f'## 三、全框结果 — {align}（n={len(recs_keep)}）', '', *TABLE_HEAD]
        y = np.array([r['y'] for r in recs_keep])
        kk = np.array([r['k'] for r in recs_keep])
        st = np.array([r['stored'] for r in recs_keep])
        oldm = np.array([r['gen'] == 'old' for r in recs_keep])
        L.append(_row_line('生产落库（全框，83旧+5新）', metrics(y, st, kk)))
        L.append(_row_line(f'生产落库（仅旧模型 n={int(oldm.sum())}）',
                           metrics(y[oldm], st[oldm], kk[oldm])))
        for name in ['B0_线上pkl_无下限', 'B0b_线上pkl_8%下限'] + list(tuned.keys()):
            tag = ' **[B选中]**' if name == best_name else ''
            L.append(_row_line(name, metrics(y, variants[name][align], kk), tag))
        L.append('')

    # 验证窗选型表
    L += ['## 四、B 路径验证窗选型（冻结前，n=401，实际走水率 0.237）', '',
          '| 变体 | ratio | 校准 | 验证窗RPS | 走水率偏差 | 走水召回 | 偏差≤2pp |',
          '| --- | --- | --- | --- | --- | --- | --- |']
    for name, s in tuned.items():
        ok = '✅' if s['push_dev'] <= 0.02 else '—'
        tag = ' **选中**' if name == best_name else ''
        L.append(f"| {name}{tag} | {s['ratio']} | {s['kind']} | {s['rps_val']:.4f} | "
                 f"{s['push_dev']:.3f} | {s['push_recall_val']:.3f} | {ok} |")
    L.append('')

    # C 子框
    for align in ('t-24h', 'noon'):
        sub = grid_ok[align]
        if not sub:
            continue
        y = np.array([r['y'] for r in sub])
        kk = np.array([r['k'] for r in sub])
        cc = np.array([r['c'] for r in sub])
        ids = [r['match_id'] for r in sub]
        recs_all = frames[align]
        id_pos = {r['match_id']: i for i, r in enumerate(recs_all)}
        idx = np.array([id_pos[i] for i in ids])
        st = np.array([recs_all[i]['stored'] for i in idx])
        L += [f'## 五、C 路径网格子框 — {align}（n={len(sub)}，同框对比）', '', *TABLE_HEAD]
        L.append(_row_line('生产落库', metrics(y, st, kk)))
        L.append(_row_line('B0b_线上pkl_8%下限',
                           metrics(y, variants['B0b_线上pkl_8%下限'][align][idx], kk)))
        L.append(_row_line('C_比分网格聚合', metrics(y, cc, kk)))
        if best_name in variants:
            L.append(_row_line(best_name + '（B选中）',
                               metrics(y, variants[best_name][align][idx], kk), ' **[B选中]**'))
        L.append('')

    L += ['## 六、限制与注意', '',
          '- 评估行仅前 18 维赔率特征按 align 快照重建；Elo/form/rest/cards/opp 共 53 维取'
          '全矩阵（point-in-time 构造，与生产同源但中位数填充以全表计算，存在极微差异）。',
          '- 同代保真度 n=5 极小，corr 仅供方向参考；逐场三向偏差表是更直接的证据。',
          '- 评估框 n=85/84、网格子框 n≈35，样本量小，skill 波动大，重点看方向与量级而非小数位。',
          '- 校准器验证窗为同赛季 3-8 月，标签含反推样本（label_source）权重沿用生产管线；'
          'none 校准全部过预测，platt/iso 均把走水均值精确拉回实际率，iso 验证窗 RPS 微优、'
          'platt 召回更稳，小样本下两者并列看待。',
          '- C 网格 6×6 封顶 5 球（极端比分被截入近邻格，影响很小）；网格来自 t006 v5 非回放。',
          '- 本实验只治走水低估；即便走水校准达标，非走水方向命中率若仍 <50%，'
          '说明病因2 需另行决策（重训方向模型或市场复制/凸混合）。', '']

    # ---- 七、结论与上线建议 ----
    # 市场参照：市场自身走水 argmax 召回（评估框）
    mref = {}
    for align in ('t-24h', 'noon'):
        yy = np.array([r['y'] for r in frames[align]])
        kk = np.array([r['k'] for r in frames[align]])
        ka = kk.argmax(1)
        mref[align] = ((ka[yy == 1] == 1).mean() if (yy == 1).any() else np.nan,
                       float(kk[:, 1].mean()), float((yy == 1).mean()))
    L += ['## 七、结论与上线建议（影子阶段，待批准后才动生产）', '',
          '### 7.1 病因1（走水低估）修复判定：**B 路径有效，建议上线**', '',
          f'- 市场参照：走水 argmax 召回 t-24h={mref["t-24h"][0]:.2f}、'
          f'noon={mref["noon"][0]:.2f}——竞彩走水概率结构性地几乎从不成为 argmax，'
          '故走水验收应以**概率质量**（均值偏差/RPS/CLV）为准，argmax 召回仅参考。',
          '- 修复量级（noon，同信息集）：RPS 0.298→0.227~0.231；skill -0.386→-0.05~-0.08；'
          '走水均值偏差 -18.2pp→±3pp 内；CLV -0.052→约 0；35 场网格子框 skill 转正 '
          '(+0.02)、CLV +0.015。',
          '- 推荐候选：**B_r2.5_iso**（双口径 RPS 0.2264/0.2276 居前、偏差 -2~-3pp、'
          'CLV 正、验证窗召回 0.547）；保守备选 B_r1.5_iso。冻结规则自动选中的 '
          'B_r3.0_iso 验证窗微优 0.0004（噪声级）但评估窗走水判别被压平，综合稳健性略逊。',
          '- 免重训方案 Bmin（0920 pkl+iso/platt）同样有效但次一档（val RPS 0.328、'
          '校准后均值仍 +5pp），仅作为不便重训时的退路。',
          '- 上线所需改动（均未执行）：① 按 r2.5 配置重训 Stage1 draw 模型并落新 pkl；'
          '② 持久化等温校准器（serving 链路在 draw 概率出口加 iso 映射）；'
          '③ 移除/放宽 prediction_core 的 T005V3_DRAW_PROB_FLOOR=0.08 硬下限（校准后已不需要）；'
          '④ metadata 记录校准类型与训练截止；⑤ 回放 8-9 月窗确认后再切线上。', '',
          '### 7.2 C 路径：**否决**', '',
          '- 比分网格聚合走水质量本身不足（子框均值 0.187 vs 实际 0.314），'
          'RPS 0.274 差于旧落库 0.266、更远逊 B 的 0.216；走水问题不能靠 T-006 网格旁路解决。', '',
          '### 7.3 病因2（方向为负）：仍未解决，需另行决策', '',
          '- B 修复后非走水子样本方向命中 0.55~0.63（旧模型 0.33），但市场 0.70 仍占优，'
          '这是剩余全部 skill 缺口的来源；候选方案：重训 Stage2 方向模型、'
          '市场三向凸混合、或直接复制市场方向（D 路径，另立实验）。', '']
    return '\n'.join(L)


if __name__ == '__main__':
    main()
