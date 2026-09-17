# -*- coding: utf-8 -*-
"""
P1-10 / P1-11 联合 A/B 验证（临时，验证完可删）
================================================

基线:  build_all_features(slim_odds=True, ts_odds=True, xg_deep=False,
                          ctx_features=False, consensus_odds=False)  → 233 维（当前生产基线）
实验:
  P1-10  B_ctx = 基线 + 情境化特征 14 维（赛程密度/休息天数/连续作战/积分压力/德比/新军）
  P1-11  B_cons = 基线 + 赔率一致性特征 10 维（竞彩 vs 百家欧指共识偏离度）

验收:
  P1-10: A/B（XGB+LGB 融合）RPS 不劣化、Acc 不降
  P1-11: A/B RPS 改善 或 校准（ECE/LogLoss）改善

方法: 滚动时间切分（无 shuffle，防泄漏），XGBoost + LightGBM 双 GBDT 最优参数 → 50/50 融合。
"""

import os
import sys
import time
import pickle

import numpy as np
import pandas as pd

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features
from contextual_features import build_contextual_features
from odds_consensus_features import build_odds_consensus_features

# result 编码与生产一致: 0=客胜, 1=平局, 2=主胜（label 升序）
# proba 列序 = [客胜(0), 平(1), 主胜(2)]，与 train_models.compute_rps 口径完全对齐

LGB_PARAMS = dict(
    objective='multiclass', num_class=3,
    max_depth=4, learning_rate=0.12, n_estimators=80,
    num_leaves=128, min_child_samples=60,
    random_state=42, n_jobs=-1, verbose=-1,
)

XGB_PARAMS = dict(
    objective='multi:softprob', num_class=3,
    max_depth=4, learning_rate=0.07, n_estimators=130,
    subsample=0.785, gamma=5.0, reg_alpha=0.1, reg_lambda=8.0,
    min_child_weight=13, tree_method='hist',
    random_state=42, n_jobs=-1, eval_metric='mlogloss',
)


def rps_score(y_true, proba):
    # 与 train_models.compute_rps 口径一致：proba 列序 = [客胜, 平, 主胜]，累积顺序 [客,平,主]
    n = len(y_true)
    actual = np.zeros((n, 3))
    for i, yv in enumerate(y_true):
        actual[i, int(yv)] = 1.0
    cumP = np.cumsum(proba, axis=1)[:, :-1]
    cumO = np.cumsum(actual, axis=1)[:, :-1]
    return float(np.mean(np.sum((cumP - cumO) ** 2, axis=1)) / 2.0)


def ece_score(y_true, proba, n_bins=10):
    y_true = np.asarray(y_true)
    conf = proba.max(axis=1)
    pred_col = proba.argmax(axis=1)
    correct = (pred_col == y_true).astype(float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (conf > bins[i]) & (conf <= bins[i + 1])
        if mask.sum() > 0:
            ece += (mask.sum() / len(y_true)) * abs(conf[mask].mean() - correct[mask].mean())
    return float(ece)


def metrics(y_true, proba):
    y_true = np.asarray(y_true)
    pred_col = proba.argmax(axis=1)
    acc = float(np.mean(pred_col == y_true))
    p_rows = proba[np.arange(len(y_true)), y_true]
    ll = float(np.mean(-np.log(np.clip(p_rows, 1e-9, 1.0))))
    rps = rps_score(y_true, proba)
    ece = ece_score(y_true, proba)
    draw_mask = y_true == 1
    draw_recall = float((pred_col[draw_mask] == 1).mean()) if draw_mask.sum() else float('nan')
    return dict(acc=acc, ll=ll, rps=rps, ece=ece, draw_recall=draw_recall)


def train_and_proba(X_tr, y_tr, X_te):
    import lightgbm as lgb
    import xgboost as xgb

    lgb_m = lgb.LGBMClassifier(**LGB_PARAMS)
    lgb_m.fit(X_tr, y_tr)
    lgb_p = lgb_m.predict_proba(X_te)  # 升序 [客胜(0), 平(1), 主胜(2)]

    xgb_m = xgb.XGBClassifier(**XGB_PARAMS)
    xgb_m.fit(X_tr, y_tr)
    xgb_p = xgb_m.predict_proba(X_te)

    return lgb_p, xgb_p


def fmt(m):
    return (f"Acc={m['acc']:.4f} | LogLoss={m['ll']:.4f} | RPS={m['rps']:.4f} "
            f"| ECE={m['ece']:.4f} | DrawRecall={m['draw_recall']:.4f}")


def run_ab(name, X_A, X_B, y):
    print("\n" + "=" * 76)
    print(f"[{name}] A/B（XGB+LGB 融合）")
    print("=" * 76)
    y_arr = np.asarray(y)
    cutoffs = [(0.60, 0.80), (0.80, 1.00)]
    summary = {'A': [], 'B': []}

    for tr_end, te_end in cutoffs:
        n = len(y_arr)
        i1, i2 = int(n * tr_end), int(n * te_end)
        print(f"\n   Fold train[:{i1}] test[{i1}:{i2}]:")
        for grp, X in [('A', X_A), ('B', X_B)]:
            X_tr = X.iloc[:i1].reset_index(drop=True)
            X_te = X.iloc[i1:i2].reset_index(drop=True)
            y_tr = y_arr[:i1]
            y_te = y_arr[i1:i2]
            t0 = time.time()
            lgb_p, xgb_p = train_and_proba(X_tr, y_tr, X_te)
            blend_p = 0.5 * lgb_p + 0.5 * xgb_p
            m = metrics(y_te, blend_p)
            print(f"   [{grp}] {fmt(m)} ({time.time()-t0:.1f}s)")
            summary[grp].append(m)

    print(f"\n   -- 汇总均（A=基线 / B=实验）--")
    for k, label in [('acc', 'Acc'), ('ll', 'LogLoss'), ('rps', 'RPS'),
                     ('ece', 'ECE'), ('draw_recall', 'DrawRecall')]:
        a = np.nanmean([r[k] for r in summary['A']])
        b = np.nanmean([r[k] for r in summary['B']])
        diff = b - a
        better = ('B更优' if ((k in ('ll', 'rps', 'ece') and diff < 0) or
                              (k in ('acc', 'draw_recall') and diff > 0)) else 'A更优')
        print(f"   {label:12s}: A={a:.4f}  B={b:.4f}  diff={diff:+.4f} ({better})")
    return summary


def main():
    print("=" * 76)
    print("[P1-10 / P1-11] 情境化特征 + 赔率一致性特征 A/B 验证")
    print("=" * 76)

    df = load_match_data_odds()
    print(f"比赛数据: {len(df)} 场")

    cache_path = os.path.join(_SCRIPT_DIR, '..', 'logs', '_tmp_p110_p111_base.pkl')
    if os.path.exists(cache_path):
        print("\n[缓存] 加载已构建基线 X_base ...")
        with open(cache_path, 'rb') as f:
            X_base, y = pickle.load(f)
        print(f"   基线维度: {X_base.shape[1]}")
    else:
        print("\n[1] 构建基线特征（slim_odds=True, ts_odds=True，其余开关关闭）...")
        t0 = time.time()
        X_base, y = build_all_features(df, slim_odds=True, ts_odds=True)
        print(f"   基线维度: {X_base.shape[1]} ({time.time()-t0:.1f}s)")
        with open(cache_path, 'wb') as f:
            pickle.dump((X_base, y), f)
        print(f"   [缓存] 已保存 {cache_path}")

    # ---- P1-10: + 情境化特征 ----
    print("\n[2] P1-10 实验: 基线 + 情境化特征")
    t0 = time.time()
    ctx_feat = build_contextual_features(df)
    X_ctx = pd.concat([X_base, ctx_feat], axis=1)
    print(f"   新增 {ctx_feat.shape[1]} 维, 实验维度 {X_ctx.shape[1]} ({time.time()-t0:.1f}s)")

    # ---- P1-11: + 赔率一致性特征 ----
    print("\n[3] P1-11 实验: 基线 + 赔率一致性特征")
    t0 = time.time()
    cons_feat = build_odds_consensus_features(df, X_base)
    X_cons = pd.concat([X_base, cons_feat], axis=1)
    print(f"   新增 {cons_feat.shape[1]} 维, 实验维度 {X_cons.shape[1]} ({time.time()-t0:.1f}s)")

    run_ab("P1-10 情境化特征", X_base, X_ctx, y)
    run_ab("P1-11 赔率一致性", X_base, X_cons, y)


if __name__ == '__main__':
    main()