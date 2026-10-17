# -*- coding: utf-8 -*-
"""
WDL 平局校准 shadow 实验（D-20261006-053 方案 A / C-20261006-007 后续）
=====================================================================================
背景：生产 WDL Stacking 平局概率系统性偏高 +3.0pp（生产 p_draw 0.2531 vs 市场 0.2229），
经 IPF 原样传入 T-006 v5 网格（corr=1.000），是 v5 验收失败主因（94%）。
校准插入点 = LR meta-learner 融合后、冷门调整/落库前（shadow 双算，不落库）。

设计（与 t005_fix_experiment 同构的三窗严格时间切分）：
  fit  < 2026-03-01        校准器拟合（Platt/等温 × 三向联合/draw 定向）
  VAL  2026-03-01~08-21    变体选型（约束 |p_draw 偏差|≤2pp，按 RPS 选型）
  EVAL 2026-08-22+         双闸门：|p_draw 偏差|≤2pp 且 RPS 不退化；
                           双口径：重建管线 + 生产落库（99 场有赛果）

保真策略：直接调 prediction_core.WDLPredictor.predict（生产代码路径零改动）：
  - 特征：load_match_data_odds(dedup=True) + build_all_features(include_odds, ts_odds,
    consensus_odds 同生产)，尾槽覆写法（tail-slot）把目标场真实特征行复制到 df/X 尾部，
    使 predict 内部 mask 的 index[-1] 精确命中本场（解决同主客对多行歧义）；
  - 赔率：wdl_history/total_goals_history 按 noon 口径（≤比赛日 12:04 最后快照）重建
    odds_data；无 noon 快照时回退当日首条并打标 odds_mode=postnoon_first；
  - Elo 用 t005v2_final_elo_ratings.json 当前快照、贝叶斯用 08-28 资产（前视偏差，
    由 B0 门禁量化）。
B0 门禁：重建 EVAL vs 生产落库（is_replay=0）逐场对比，|mean Δp_draw|<0.5pp 达标，
不达标则本报告结论自动降级为「仅供参考」。

输出：reports/wdl_draw_calib_<ts>.md；全程 shadow 不落库、不改任何生产资产。
"""

import os
import sys
import io
import json
import pickle
import sqlite3
import logging
import contextlib
from datetime import datetime

import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
FEAT_CACHE = os.path.join(SCRIPT_DIR, '_feat_cache_wdl_calib.pkl')
PRED_CACHE = os.path.join(SCRIPT_DIR, '_pred_cache_wdl_calib.pkl')

FIT_END = '2026-03-01'      # 校准器拟合窗上界（不含）
EVAL_START = '2026-08-22'   # EVAL 窗首日
NOON_CUT = '12:04:00'       # 与市场基准 noon 口径一致
LEAGUES = ('英超', '西甲', '意甲', '德甲', '法甲')

BIAS_GATE = 0.02            # 双闸门1：|p_draw 偏差| ≤ 2pp
RPS_TOL_VAL = 0.002         # VAL 选型 RPS 容忍（相对 none）
RPS_TOL_EVAL = 0.001        # EVAL 闸门2：RPS 退化容忍
B0_GATE = 0.005             # B0：|mean Δp_draw| < 0.5pp

# 静音生产预测日志（7000+ 场 × 20+ 行）
logging.getLogger('prediction_core').setLevel(logging.CRITICAL)


# ============================================================
# 数据
# ============================================================
def parse_label(score, wdl_text):
    """actual_score '3:1' → 0主/1平/2客；缺失时回退 actual_wdl 文本。"""
    s = str(score or '').strip()
    if ':' in s:
        try:
            h, a = s.split(':')[:2]
            h, a = int(h), int(a)
            return 0 if h > a else (1 if h == a else 2)
        except ValueError:
            pass
    t = str(wdl_text or '').strip()
    if t in ('胜', '主胜'):
        return 0
    if t in ('平', '平局'):
        return 1
    if t in ('负', '客胜'):
        return 2
    return None


def load_targets(conn):
    """五联赛有赛果场，附时段标签。按 (date, home, away) 去重。"""
    ph = ','.join('?' * len(LEAGUES))
    rows = conn.execute(
        f"SELECT match_id, match_date, league, home_team, away_team, actual_score, actual_wdl "
        f"FROM matches WHERE league IN ({ph}) ORDER BY match_date", LEAGUES).fetchall()
    out, seen = [], set()
    for r in rows:
        y = parse_label(r['actual_score'], r['actual_wdl'])
        if y is None:
            continue
        date = str(r['match_date'])[:10]
        key = (date, r['home_team'], r['away_team'])
        if key in seen:
            continue
        seen.add(key)
        window = 'fit' if date < FIT_END else ('val' if date < EVAL_START else 'eval')
        out.append({
            'match_id': r['match_id'], 'date': date, 'league': r['league'],
            'home_en': r['home_team'], 'away_en': r['away_team'], 'y': y, 'window': window,
        })
    return out


def load_noon_odds(conn, mid_cn, date):
    """wdl/tg 的 noon 口径重建。返回 (odds_data, market[kh,kd,ka], odds_mode, n_wdl_recs)。"""
    cut = f'{date} {NOON_CUT}'
    rows = conn.execute(
        "SELECT timestamp, win_a, draw, win_b FROM wdl_history WHERE match_id=? "
        "ORDER BY timestamp", (mid_cn,)).fetchall()
    recs = [(str(r['timestamp']), float(r['win_a']), float(r['draw']), float(r['win_b']))
            for r in rows
            if r['win_a'] and r['draw'] and r['win_b'] and r['win_a'] > 1 and r['draw'] > 1 and r['win_b'] > 1]
    if not recs:
        return None, None, 'no_wdl', 0
    noon = [x for x in recs if x[0] <= cut]
    if noon:
        use, mode = noon, 'noon'
    else:
        use, mode = [recs[0]], 'postnoon_first'
    wdl_recs = [{'time': t, 'win': w, 'draw': d, 'lose': l} for t, w, d, l in use]
    odds_data = {
        'wdl_odds': {'records': wdl_recs, 'open': wdl_recs[0], 'close': wdl_recs[-1]},
        'handicap_odds': {'line': None, 'records': [], 'open': {}, 'close': {}},
        'score_odds': {'records': []},
        'tg_odds': {'records': []},
    }
    # 总进球（noon 口径，同 build_sporttery_odds 结构）
    tg_rows = conn.execute(
        "SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus "
        "FROM total_goals_history WHERE match_id=? AND timestamp<=? ORDER BY timestamp",
        (mid_cn, cut)).fetchall()
    if tg_rows:
        labels = ['0', '1', '2', '3', '4', '5', '6', '7+']
        tg_recs = []
        for r in tg_rows:
            # 老赛季 total_goals_history 存在 NULL 单元格：过滤 None 键，
            # 全空则丢整条（生产 build_sporttery_odds 不过滤但近期数据无 NULL 从未触发）
            goals = {labels[i]: float(r[i + 1]) for i in range(8)
                     if r[i + 1] is not None and float(r[i + 1]) > 1}
            if goals:
                tg_recs.append({'pub_time': str(r[0]), 'goals': goals})
        odds_data['tg_odds']['records'] = tg_recs
    lw, ld, ll = use[-1][1], use[-1][2], use[-1][3]
    p = np.array([1.0 / lw, 1.0 / ld, 1.0 / ll])
    market = p / p.sum()
    return odds_data, market, mode, len(use)


# ============================================================
# 特征缓存（生产同参数，一次性构建）
# ============================================================
def build_feature_cache(predictor):
    if os.path.exists(FEAT_CACHE):
        with open(FEAT_CACHE, 'rb') as f:
            df, X = pickle.load(f)
        print(f'  (使用特征缓存 {os.path.basename(FEAT_CACHE)}, df={df.shape}, X={X.shape})')
    else:
        from feature_utils import load_match_data_odds, build_all_features
        df = load_match_data_odds(dedup=True).reset_index(drop=True)
        print(f'  全量特征构建中（约4-5分钟）...')
        X, _ = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
        X = X.reset_index(drop=True)
        assert len(X) == len(df), f'X/df 行数不一致 {len(X)}/{len(df)}'
        with open(FEAT_CACHE, 'wb') as f:
            pickle.dump((df, X), f)
    predictor._cached_df = df
    predictor._cached_X = X
    return df, X


# ============================================================
# 逐场预测（tail-slot 特征注入）
# ============================================================
def predict_all(predictor, t005_models, df, X, targets, conn, en2cn):
    import prediction_core as pc

    # 尾槽：追加一行占位，之后每场覆写该槽 → predict 内 mask 的 index[-1] 必中本槽
    slot = len(df)
    df.loc[slot] = df.loc[0]
    X.loc[slot] = X.loc[0]

    # match_id → df 行号（df.match_id == matches.match_id）；后备 名称+日期
    mid2idx = {}
    for idx, v in df['match_id'].items():
        if idx < slot and isinstance(v, str) and v not in mid2idx:
            mid2idx[v] = idx

    results, skips = [], {'no_df_row': 0, 'no_bridge': 0, 'no_wdl': 0, 'predict_err': 0}
    n = len(targets)
    for i, t in enumerate(targets):
        if (i + 1) % 500 == 0 or i == 0:
            print(f'  预测进度 {i + 1}/{n} ({t["window"]}) ...', flush=True)
        # 1) df 行定位
        src = mid2idx.get(t['match_id'])
        if src is None:
            skips['no_df_row'] += 1
            continue
        # 2) 中文桥接（odds 查询键 + match 字典）
        home_cn = str(df.at[src, 'home_team_name'])
        away_cn = str(df.at[src, 'away_team_name'])
        mid_cn = t['match_id'] if any('一' <= ch <= '鿿' for ch in t['match_id']) \
            else f"{t['date']}_{en2cn.get(' '.join(t['home_en'].split()).strip().lower())}_" \
                 f"{en2cn.get(' '.join(t['away_en'].split()).strip().lower())}"
        if mid_cn.endswith('_None') or '_None' in mid_cn:
            skips['no_bridge'] += 1
            continue
        # 3) noon 赔率
        odds_data, market, mode, n_recs = load_noon_odds(conn, mid_cn, t['date'])
        if odds_data is None:
            skips['no_wdl'] += 1
            continue
        # 4) 尾槽覆写
        df.loc[slot] = df.loc[src]
        X.loc[slot] = X.loc[src]
        # 5) 生产路径预测
        match = {
            'home_team': t['home_en'], 'away_team': t['away_en'],
            'home_team_cn': home_cn, 'away_team_cn': away_cn,
            'league': t['league'], 'match_date': t['date'],
        }
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = predictor.predict(match, odds_data, t005_models=t005_models, is_mock=False)
        except Exception:
            skips['predict_err'] += 1
            continue
        # 6) pre-meta 重建（校准插入点；复用生产融合函数）
        sub = res.get('sub_models') or {}
        avail = [m for m in pc.STACKING_META_MODELS if sub.get(m) is not None]
        p_pre = None
        if len(avail) >= 2:
            meta = pc.apply_stacking_meta_learner(sub, extra_features={'dc_odds_fallback': 0.0})
            if meta is not None:
                p_pre = np.array([meta['win'], meta['draw'], meta['lose']])
            else:
                st = pc.CalcEngine.stack_wdl_probabilities(sub, pc.STACKING_WEIGHTS)
                p_pre = np.array([st['win'], st['draw'], st['lose']])
        elif len(avail) == 1:
            s0 = sub[avail[0]]
            p_pre = np.array([s0['win'], s0['draw'], s0['lose']])
        if p_pre is None or not np.all(np.isfinite(p_pre)) or p_pre.sum() <= 0:
            hp, dp, ap = pc.odds_to_implied_prob(
                odds_data['wdl_odds']['close']['win'], odds_data['wdl_odds']['close']['draw'],
                odds_data['wdl_odds']['close']['lose'])
            p_pre = np.array([hp, dp, ap])
        p_pre = p_pre / p_pre.sum()

        results.append({
            'match_id': t['match_id'], 'date': t['date'], 'league': t['league'],
            'window': t['window'], 'y': t['y'],
            'p_pre': p_pre,
            'p_final': np.array([res['home_prob'], res['draw_prob'], res['away_prob']]),
            'market': market, 'odds_mode': mode, 'n_wdl_recs': n_recs,
            'method': res.get('model_used', ''), 'n_sub': len(avail),
            'upset_applied': bool(res.get('upset_risk_score', 0) >= 0.3
                                  and not res.get('upset_data_missing', 1)),
        })
    # 撤掉尾槽
    predictor._cached_df = df.iloc[:slot].copy()
    predictor._cached_X = X.iloc[:slot].copy()
    return results, skips


# ============================================================
# 校准器（输入/输出概率列序 [home, draw, away]）
# ============================================================
def _clip(p):
    return np.clip(p, 1e-6, 1 - 1e-6)


class NoneCalib:
    name = 'none'
    def fit(self, P, y): return self
    def transform(self, P): return P


class PlattJoint:
    """三向联合：multinomial LR on log(p)（log-linear 校准）。"""
    name = 'platt_joint'
    def fit(self, P, y):
        from sklearn.linear_model import LogisticRegression
        self.lr = LogisticRegression(max_iter=2000)
        self.lr.fit(np.log(_clip(P)), y)
        return self
    def transform(self, P):
        Q = self.lr.predict_proba(np.log(_clip(P)))
        out = np.zeros_like(P)
        for j, c in enumerate(self.lr.classes_):
            out[:, int(c)] = Q[:, j]
        return out


class IsoJoint:
    """三向联合：逐类 one-vs-rest 等温 → 归一。"""
    name = 'iso_joint'
    def fit(self, P, y):
        from sklearn.isotonic import IsotonicRegression
        self.isos = []
        for k in range(3):
            iso = IsotonicRegression(out_of_bounds='clip')
            iso.fit(P[:, k], (y == k).astype(int))
            self.isos.append(iso)
        return self
    def transform(self, P):
        Q = np.column_stack([iso.predict(P[:, k]) for k, iso in enumerate(self.isos)])
        Q = np.clip(Q, 1e-9, None)
        return Q / Q.sum(axis=1, keepdims=True)


class _DrawBase:
    """draw 定向：只校准 p_draw，home/away 按比例缩放。"""
    def _draw_cal(self, pd_raw):
        raise NotImplementedError
    def transform(self, P):
        pd_new = self._draw_cal(P[:, 1])
        rest = _clip(P[:, 0]) + _clip(P[:, 2])
        scale = (1.0 - pd_new) / np.clip(1.0 - P[:, 1], 1e-6, None)
        Q = P.copy()
        Q[:, 0] = P[:, 0] * scale
        Q[:, 2] = P[:, 2] * scale
        Q[:, 1] = pd_new
        Q = np.clip(Q, 1e-9, None)
        return Q / Q.sum(axis=1, keepdims=True)


class PlattDraw(_DrawBase):
    name = 'platt_draw'
    def fit(self, P, y):
        from sklearn.linear_model import LogisticRegression
        z = np.log(_clip(P[:, 1]) / _clip(1 - P[:, 1])).reshape(-1, 1)
        self.lr = LogisticRegression(max_iter=2000)
        self.lr.fit(z, (y == 1).astype(int))
        return self
    def _draw_cal(self, pd_raw):
        z = np.log(_clip(pd_raw) / _clip(1 - pd_raw)).reshape(-1, 1)
        return self.lr.predict_proba(z)[:, list(self.lr.classes_).index(1)]


class IsoDraw(_DrawBase):
    name = 'iso_draw'
    def fit(self, P, y):
        from sklearn.isotonic import IsotonicRegression
        self.iso = IsotonicRegression(out_of_bounds='clip')
        self.iso.fit(P[:, 1], (y == 1).astype(int))
        return self
    def _draw_cal(self, pd_raw):
        return self.iso.predict(pd_raw)


VARIANTS = [NoneCalib, PlattJoint, IsoJoint, PlattDraw, IsoDraw]


# ============================================================
# 指标
# ============================================================
def compute_rps(y, P):
    """三向 RPS，列序 [home, draw, away]（draw 居中，序数正确）。"""
    y = np.asarray(y, dtype=int)
    P = np.asarray(P, dtype=float)
    O = np.zeros((len(y), 3))
    O[np.arange(len(y)), y] = 1.0
    return float(np.mean(np.sum((np.cumsum(P, axis=1) - np.cumsum(O, axis=1)) ** 2, axis=1)) / 2)


def metrics_block(y, P, market):
    y = np.asarray(y, dtype=int)
    P = np.asarray(P, dtype=float)
    M = np.asarray(market, dtype=float)
    out = {
        'n': len(y),
        'rps': compute_rps(y, P),
        'rps_market': compute_rps(y, M),
        'p_draw_mean': float(P[:, 1].mean()),
        'mkt_draw_mean': float(M[:, 1].mean()),
        'draw_bias': float(P[:, 1].mean() - M[:, 1].mean()),
        'acc': float((P.argmax(axis=1) == y).mean()),
        'draw_pred_rate': float((P.argmax(axis=1) == 1).mean()),
        'draw_actual': float((y == 1).mean()),
    }
    out['draw_recall'] = float(((P.argmax(axis=1) == 1) & (y == 1)).sum() / max((y == 1).sum(), 1))
    ll = 0.0
    for i, k in enumerate(y):
        ll += -np.log(max(P[i, k], 1e-9))
    out['logloss'] = float(ll / len(y))
    return out


def draw_calibration_bins(y, P, n_bins=5):
    """p_draw 分箱：预测均值 vs 实际平局率。"""
    y = np.asarray(y, dtype=int)
    edges = np.quantile(P[:, 1], np.linspace(0, 1, n_bins + 1))
    edges[0], edges[-1] = 0.0, 1.0
    rows = []
    for b in range(n_bins):
        m = (P[:, 1] >= edges[b]) & (P[:, 1] <= edges[b + 1])
        if m.sum() > 0:
            rows.append({'bin': f'[{edges[b]:.3f},{edges[b + 1]:.3f}]', 'n': int(m.sum()),
                         'pred': float(P[m, 1].mean()), 'actual': float((y[m] == 1).mean())})
    return rows


# ============================================================
# 主流程
# ============================================================
def patch_degraded(predictor, t005_models, df, X, conn, en2cn):
    """补丁：重跑 PRED_CACHE 中因 tg NULL 崩溃而降级为纯赔率的场次（n_sub==0）。"""
    with open(PRED_CACHE, 'rb') as f:
        results, skips = pickle.load(f)
    bad_idx = [i for i, r in enumerate(results)
               if r['n_sub'] == 0 and '赔率' in r.get('method', '')]
    print(f'   降级场识别: {len(bad_idx)}/{len(results)}')
    if not bad_idx:
        return
    # 重建 targets 索引以回填
    targets = {(t['match_id'], t['date']): t for t in load_targets(conn)}
    slot = len(df)
    df.loc[slot] = df.loc[0]
    X.loc[slot] = X.loc[0]
    mid2idx = {}
    for idx, v in df['match_id'].items():
        if idx < slot and isinstance(v, str) and v not in mid2idx:
            mid2idx[v] = idx
    import prediction_core as pc
    fixed = 0
    for i in bad_idx:
        r = results[i]
        t = targets.get((r['match_id'], r['date']))
        if t is None:
            continue
        src = mid2idx.get(t['match_id'])
        if src is None:
            continue
        home_cn = str(df.at[src, 'home_team_name'])
        away_cn = str(df.at[src, 'away_team_name'])
        mid_cn = t['match_id'] if any('一' <= ch <= '鿿' for ch in t['match_id']) \
            else f"{t['date']}_{en2cn.get(' '.join(t['home_en'].split()).strip().lower())}_" \
                 f"{en2cn.get(' '.join(t['away_en'].split()).strip().lower())}"
        odds_data, market, mode, n_recs = load_noon_odds(conn, mid_cn, t['date'])
        if odds_data is None:
            continue
        df.loc[slot] = df.loc[src]
        X.loc[slot] = X.loc[src]
        match = {'home_team': t['home_en'], 'away_team': t['away_en'],
                 'home_team_cn': home_cn, 'away_team_cn': away_cn,
                 'league': t['league'], 'match_date': t['date']}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = predictor.predict(match, odds_data, t005_models=t005_models, is_mock=False)
        except Exception:
            continue
        sub = res.get('sub_models') or {}
        avail = [m for m in pc.STACKING_META_MODELS if sub.get(m) is not None]
        if len(avail) < 2:
            continue  # 仍降级，保留原值
        meta = pc.apply_stacking_meta_learner(sub, extra_features={'dc_odds_fallback': 0.0})
        if meta is not None:
            p_pre = np.array([meta['win'], meta['draw'], meta['lose']])
        else:
            st = pc.CalcEngine.stack_wdl_probabilities(sub, pc.STACKING_WEIGHTS)
            p_pre = np.array([st['win'], st['draw'], st['lose']])
        p_pre = p_pre / p_pre.sum()
        results[i] = {**r, 'p_pre': p_pre,
                      'p_final': np.array([res['home_prob'], res['draw_prob'], res['away_prob']]),
                      'market': market, 'odds_mode': mode, 'n_wdl_recs': n_recs,
                      'method': res.get('model_used', ''), 'n_sub': len(avail),
                      'upset_applied': bool(res.get('upset_risk_score', 0) >= 0.3
                                            and not res.get('upset_data_missing', 1))}
        fixed += 1
    print(f'   补丁修复: {fixed}/{len(bad_idx)}')
    with open(PRED_CACHE, 'wb') as f:
        pickle.dump((results, skips), f)


def main():
    import prediction_core as pc
    import market_benchmark_hc as mbh

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    print('=' * 78)
    print('WDL 平局校准 shadow 实验（D-20261006-053 方案 A）')
    print('=' * 78)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    print('\n1. 加载生产模型资产（pkl 0918 + Platt + meta 0920 + Elo + 贝叶斯0828）...')
    models = pc.init_models()
    predictor = pc.WDLPredictor(models['wdl'], epl_model=None)
    t005_models = models['t005']
    print(f"   WDL: {models['wdl']['status']}, features={len(models['wdl']['features'])}, "
          f"scaler_n={models['wdl']['scaler'].n_features_in_ if models['wdl']['scaler'] is not None else None}")

    print('\n2. 目标场次（五联赛有赛果）...')
    targets = load_targets(conn)
    n_w = {w: sum(1 for t in targets if t['window'] == w) for w in ('fit', 'val', 'eval')}
    print(f"   fit={n_w['fit']}  val={n_w['val']}  eval={n_w['eval']}")

    print('\n3. 特征矩阵（生产同参数 build_all_features）...')
    df, X = build_feature_cache(predictor)
    print(f'   df={df.shape}  X={X.shape}')

    print('\n4. 逐场重建预测（生产 predict 路径，tail-slot 注入）...')
    en2cn = mbh.build_en2cn(conn)
    if os.path.exists(PRED_CACHE):
        with open(PRED_CACHE, 'rb') as f:
            results, skips = pickle.load(f)
        print(f'   (使用预测缓存 {os.path.basename(PRED_CACHE)}, n={len(results)})')
        if '--patch-degraded' in sys.argv:
            patch_degraded(predictor, t005_models, df, X, conn, en2cn)
            with open(PRED_CACHE, 'rb') as f:
                results, skips = pickle.load(f)
    else:
        results, skips = predict_all(predictor, t005_models, df, X, targets, conn, en2cn)
        with open(PRED_CACHE, 'wb') as f:
            pickle.dump((results, skips), f)
    print(f'   成功 {len(results)} 场；跳过: {skips}')
    for w in ('fit', 'val', 'eval'):
        rw = [r for r in results if r['window'] == w]
        if rw:
            print(f"   {w}: n={len(rw)}, 平局率={np.mean([r['y'] == 1 for r in rw]):.3f}, "
                  f"meta融合率={np.mean(['meta' in r['method'].lower() for r in rw]):.2%}, "
                  f"noon赔率率={np.mean([r['odds_mode'] == 'noon' for r in rw]):.2%}")

    print('\n5. B0 保真门禁（重建 EVAL vs 生产落库）...')
    stored = {}
    for r in conn.execute(
            "SELECT match_id, prediction_type, probability FROM model_predictions "
            "WHERE model_name='generate_unified_report_v2.0' AND is_replay=0 "
            "AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')"):
        stored.setdefault(r['match_id'], {})[r['prediction_type']] = float(r['probability'])
    eval_res = [r for r in results if r['window'] == 'eval']
    b0_pairs = [(r, stored[r['match_id']]) for r in eval_res if r['match_id'] in stored
                and 'WDL_draw' in stored[r['match_id']]]
    b0_pass, b0_bias = None, None
    if b0_pairs:
        d = np.array([r['p_final'][1] - s['WDL_draw'] for r, s in b0_pairs])
        b0_bias = float(d.mean())
        b0_mad = float(np.abs(d).mean())
        b0_pass = abs(b0_bias) < B0_GATE
        print(f'   B0: n={len(b0_pairs)}, mean Δp_draw={b0_bias:+.4f}, mean|Δ|={b0_mad:.4f} '
              f'→ {"PASS" if b0_pass else "FAIL（报告降级为参考）"}')
    else:
        print('   B0: 无配对场（跳过，降级）')
        b0_pass = False

    print('\n6. 校准器拟合（fit 窗 pre-meta 概率）...')
    fit_res = [r for r in results if r['window'] == 'fit']
    val_res = [r for r in results if r['window'] == 'val']
    P_fit = np.array([r['p_pre'] for r in fit_res])
    y_fit = np.array([r['y'] for r in fit_res])
    P_val = np.array([r['p_pre'] for r in val_res])
    y_val = np.array([r['y'] for r in val_res])
    M_val = np.array([r['market'] for r in val_res])
    print(f'   fit n={len(fit_res)}  val n={len(val_res)}')

    calibs, val_table = {}, []
    for cls in VARIANTS:
        c = cls().fit(P_fit, y_fit)
        calibs[c.name] = c
        m = metrics_block(y_val, c.transform(P_val), M_val)
        val_table.append({'variant': c.name, **m})
        print(f"   {c.name:<12s} VAL: RPS={m['rps']:.4f} bias={m['draw_bias']:+.4f} "
              f"draw_recall={m['draw_recall']:.3f} acc={m['acc']:.3f}")

    # 选型：|bias|≤2pp 中 RPS 最低；无人过闸则取 RPS 最低并标记
    none_rps = next(v['rps'] for v in val_table if v['variant'] == 'none')
    eligible = [v for v in val_table if abs(v['draw_bias']) <= BIAS_GATE]
    pool = eligible if eligible else val_table
    chosen = min(pool, key=lambda v: v['rps'])
    chosen_name = chosen['variant']
    print(f'   选型: {chosen_name}（约束内候选 {len(eligible)}/{len(val_table)}）')

    print('\n7. EVAL 双口径验证...')
    P_eval = np.array([r['p_pre'] for r in eval_res])
    y_eval = np.array([r['y'] for r in eval_res])
    M_eval = np.array([r['market'] for r in eval_res])
    cal = calibs[chosen_name]
    eval_table = []
    for name, c in calibs.items():
        m = metrics_block(y_eval, c.transform(P_eval), M_eval)
        eval_table.append({'track': 'rebuild', 'variant': name, **m})
    # 生产落库口径（stored 是 post-upset final，与 pre-meta 输入存在接口差异，报告中注明）
    st_rows = [r for r in eval_res if r['match_id'] in stored and 'WDL_draw' in stored[r['match_id']]]
    if st_rows:
        P_st = np.array([[stored[r['match_id']]['WDL_home'], stored[r['match_id']]['WDL_draw'],
                          stored[r['match_id']]['WDL_away']] for r in st_rows])
        P_st = P_st / P_st.sum(axis=1, keepdims=True)
        y_st = np.array([r['y'] for r in st_rows])
        M_st = np.array([r['market'] for r in st_rows])
        for name in ('none', chosen_name):
            c = calibs[name]
            m = metrics_block(y_st, c.transform(P_st), M_st)
            eval_table.append({'track': 'stored', 'variant': name, **m})
    for row in eval_table:
        print(f"   [{row['track']:>7s}] {row['variant']:<12s} n={row['n']} RPS={row['rps']:.4f} "
              f"bias={row['draw_bias']:+.4f} draw_recall={row['draw_recall']:.3f}")

    # 双闸门判定（rebuild 口径为主，stored 口径为决策参考）
    g = next(r for r in eval_table if r['track'] == 'rebuild' and r['variant'] == chosen_name)
    gate1 = abs(g['draw_bias']) <= BIAS_GATE
    gate2 = g['rps'] <= next(r for r in eval_table
                             if r['track'] == 'rebuild' and r['variant'] == 'none')['rps'] + RPS_TOL_EVAL
    verdict = 'PASS' if (gate1 and gate2 and b0_pass) else 'FAIL'
    print(f'\n   双闸门: bias {abs(g["draw_bias"]):.4f}≤{BIAS_GATE} → {gate1}; '
          f'RPS 退化≤{RPS_TOL_EVAL} → {gate2}; B0 → {b0_pass}')
    print(f'   结论: {verdict}')

    # 分联赛（chosen, rebuild EVAL）
    per_league = []
    for lg in sorted({r['league'] for r in eval_res}):
        idx = [i for i, r in enumerate(eval_res) if r['league'] == lg]
        if len(idx) >= 5:
            m = metrics_block(y_eval[idx], cal.transform(P_eval[idx]), M_eval[idx])
            m0 = metrics_block(y_eval[idx], P_eval[idx], M_eval[idx])
            per_league.append({'league': lg, 'n': len(idx),
                               'rps_none': m0['rps'], 'rps_cal': m['rps'],
                               'bias_none': m0['draw_bias'], 'bias_cal': m['draw_bias']})

    bins = draw_calibration_bins(y_eval, cal.transform(P_eval))

    # ============================================================
    # 报告
    # ============================================================
    lines = []
    A = lines.append
    A(f'# WDL 平局校准 shadow 实验报告（D-20261006-053 方案 A）')
    A(f'')
    A(f'- 生成时间: {ts}')
    A(f'- 结论: **{verdict}**' + ('' if b0_pass else '（B0 保真门禁未过，结论降级为参考）'))
    A(f'- 校准插入点: LR meta-learner 融合后、冷门调整/落库前（shadow 双算，不落库）')
    A(f'')
    A(f'## 1. 背景与根因')
    A(f'')
    A(f'生产 WDL Stacking p_draw 系统性偏高（C-20261006-007：生产 0.2531 vs 市场 0.2229，+3.0pp），')
    A(f'经 IPF 原样传入 T-006 v5 网格（corr=1.000），为 v5 验收失败主因（94%）。')
    A(f'本实验在严格时间切分下训练末段概率校准器，shadow 验证双闸门。')
    A(f'')
    A(f'## 2. 数据窗与管线')
    A(f'')
    A(f'| 窗口 | 范围 | n 目标 | n 成功 | 平局率 | noon赔率率 | meta融合率 |')
    A(f'|---|---|---|---|---|---|---|')
    for w, tag in [('fit', f'<{FIT_END}'), ('val', f'{FIT_END}~2026-08-21'), ('eval', f'≥{EVAL_START}')]:
        rw = [r for r in results if r['window'] == w]
        if rw:
            A(f'| {w} | {tag} | {n_w[w]} | {len(rw)} | {np.mean([r["y"] == 1 for r in rw]):.3f} '
              f'| {np.mean([r["odds_mode"] == "noon" for r in rw]):.1%} '
              f'| {np.mean(["meta" in r["method"].lower() for r in rw]):.1%} |')
    A(f'')
    A(f'- 跳过统计: {skips}')
    A(f'- 赔率口径: noon（≤比赛日 {NOON_CUT} 最后快照）；无 noon 快照时回退当日首条并打标')
    A(f'- 特征: 生产同参 build_all_features(include_odds, ts_odds, consensus_odds)，'
      f'tail-slot 精确注入本场特征行')
    A(f'')
    A(f'## 3. B0 保真门禁（重建 EVAL vs 生产落库）')
    A(f'')
    if b0_pairs:
        A(f'- 配对 n={len(b0_pairs)}，mean Δp_draw = **{b0_bias:+.4f}**（门限 ±{B0_GATE}），'
          f'mean|Δ| = {b0_mad:.4f} → **{"PASS" if b0_pass else "FAIL"}**')
    else:
        A(f'- 无配对场，B0 无法执行 → FAIL（降级）')
    A(f'')
    A(f'## 4. VAL 选型（fit 拟合 / VAL {FIT_END}~08-21 选型）')
    A(f'')
    A(f'| 变体 | RPS | p_draw 偏差 | p_draw 均值 | 市场 | draw_recall | acc | logloss |')
    A(f'|---|---|---|---|---|---|---|---|')
    for v in val_table:
        star = ' **★**' if v['variant'] == chosen_name else ''
        A(f"| {v['variant']}{star} | {v['rps']:.4f} | {v['draw_bias']:+.4f} | {v['p_draw_mean']:.4f} "
          f"| {v['mkt_draw_mean']:.4f} | {v['draw_recall']:.3f} | {v['acc']:.3f} | {v['logloss']:.4f} |")
    A(f'')
    A(f'选型规则: |bias|≤{BIAS_GATE} 候选中 RPS 最低（命中候选 {len(eligible)}/{len(val_table)}）'
      f'→ **{chosen_name}**')
    A(f'')
    A(f'## 5. EVAL 双口径（≥{EVAL_START}）')
    A(f'')
    A(f'| 口径 | 变体 | n | RPS | RPS市场 | p_draw 偏差 | p_draw 均值 | 市场 | draw_recall | acc |')
    A(f'|---|---|---|---|---|---|---|---|---|---|')
    for r in eval_table:
        A(f"| {r['track']} | {r['variant']} | {r['n']} | {r['rps']:.4f} | {r['rps_market']:.4f} "
          f"| {r['draw_bias']:+.4f} | {r['p_draw_mean']:.4f} | {r['mkt_draw_mean']:.4f} "
          f"| {r['draw_recall']:.3f} | {r['acc']:.3f} |")
    A(f'')
    A(f'- stored 口径注: 落库值为冷门调整后 final，与校准器训练输入（pre-meta）存在接口差异；'
      f'EVAL 窗冷门调整触发率低，影响小。')
    A(f'')
    A(f'## 6. 双闸门判定')
    A(f'')
    A(f'| 闸门 | 实测 | 门限 | 结果 |')
    A(f'|---|---|---|---|')
    A(f"| B0 保真 | {b0_bias:+.4f} | <±{B0_GATE} | {'PASS' if b0_pass else 'FAIL'} |")
    A(f"| ① p_draw 偏差收敛 | {abs(g['draw_bias']):.4f} | ≤{BIAS_GATE} | {'PASS' if gate1 else 'FAIL'} |")
    A(f"| ② RPS 不退化 | {g['rps']:.4f} vs none {next(r for r in eval_table if r['track'] == 'rebuild' and r['variant'] == 'none')['rps']:.4f} | 退化≤{RPS_TOL_EVAL} | {'PASS' if gate2 else 'FAIL'} |")
    A(f'')
    A(f'**总结论: {verdict}**' + ('' if b0_pass else '（B0 未过，降级为参考）'))
    A(f'')
    A(f'## 7. 分联赛（chosen={chosen_name}, rebuild EVAL）')
    A(f'')
    A(f'| 联赛 | n | RPS none | RPS 校准 | 偏差 none | 偏差 校准 |')
    A(f'|---|---|---|---|---|---|')
    for r in per_league:
        A(f"| {r['league']} | {r['n']} | {r['rps_none']:.4f} | {r['rps_cal']:.4f} "
          f"| {r['bias_none']:+.4f} | {r['bias_cal']:+.4f} |")
    A(f'')
    A(f'## 8. p_draw 分箱校准度（chosen, rebuild EVAL）')
    A(f'')
    A(f'| 箱 | n | 预测均值 | 实际平局率 |')
    A(f'|---|---|---|---|')
    for b in bins:
        A(f"| {b['bin']} | {b['n']} | {b['pred']:.3f} | {b['actual']:.3f} |")
    A(f'')
    A(f'## 9. 已知局限')
    A(f'')
    A(f'1. Elo 用当前快照（前视）、贝叶斯为 08-28 资产、特征 z-score 归一窗与生产当日不同 —— 均由 B0 量化兜底。')
    A(f'2. 特征矩阵中赔率特征取 DB 现有快照（近似终盘），与生产预测时刻 noon 快照存在小时级漂移。')
    A(f'3. 校准器输入为 pre-meta 融合概率；生产接线时须在 apply_stacking_meta_learner 之后、冷门调整之前插入。')
    A(f'4. fit 窗横跨 2016-2026 多赛季，市场/模型分布漂移可能稀释校准参数时效性（VAL 同赛季选型已部分对冲）。')
    A(f'')
    if verdict == 'PASS':
        A(f'## 10. 后续动作建议')
        A(f'')
        A(f'- 冻结校准器为 assets/wdl_draw_calibrator.pkl + metadata（参照 t005v3_draw_calibrator 模式）')
        A(f'- shadow 接线 prediction_core：双算 result[\'_shadow_wdl_calib\'] + generate_unified_report 落 jsonl')
        A(f'  （参照 _shadow_tg_calib 模式），观察 1-2 周后再决策是否切主路')
        A(f'- T-006 v5 验收报告在校准切主路后重新生成（平局偏差是 v5 失败主因）')

    rpt = os.path.join(REPORTS_DIR, f'wdl_draw_calib_{ts}.md')
    with open(rpt, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'\n报告已写: {rpt}')

    # 机器可读摘要（供 change_log / 后续冻结）
    summary = {
        'ts': ts, 'verdict': verdict, 'chosen': chosen_name,
        'b0': {'n': len(b0_pairs), 'bias': b0_bias, 'pass': b0_pass},
        'gates': {'bias': gate1, 'rps': gate2},
        'val_table': [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
                      for r in val_table],
        'eval_chosen': {k: (round(v, 4) if isinstance(v, float) else v) for k, v in g.items()},
    }
    with open(os.path.join(REPORTS_DIR, f'wdl_draw_calib_{ts}.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    conn.close()
    return verdict, chosen_name


if __name__ == '__main__':
    main()
