# -*- coding: utf-8 -*-
"""
P1-10 情境化特征 14 维诊断（gain 重要性 + 逐维 Leave-One-Out ablation）
========================================================================
目标: 区分「高价值情境信号」与「噪声/冗余」特征，判断是否存在值得启用的子集。
      通过 ① XGB/LGB gain 重要性 ② 逐维 LoO 的 RPS 边际 ③ 分组 ablation
      ④ 最终 2-fold A/B（base / full / 赛程体能 / 形势身份）给出子集结论。

数据/口径: 与生产一致
  - 基线 build_all_features(slim_odds=True, ts_odds=True)（缓存复用 _tmp_p110_p111_base.pkl）
  - + 14 维情境化特征 contextual_features.build_contextual_features
  - 切分: 滚动时间切分（无 shuffle，防泄漏）
  - 模型: XGBoost + LightGBM 生产参数 → 50/50 融合
  - RPS 列序 = [客胜(0), 平(1), 主胜(2)]，与 train_models.compute_rps 完全一致

特征分组（14 维 = 5 组）:
  A. 休息天数(3):   h_rest_days, a_rest_days, rest_days_diff
  B. 赛程密度(4):   h_games_7d, a_games_7d, h_games_14d, a_games_14d
  C. 连续作战(2):   h_away_streak, a_away_streak
  D. 积分压力(3):   h_ppg_last5, a_ppg_last5, ppg_last5_diff
  E. 德比/新军(2):  is_derby, league_exp_diff
  复合: 赛程体能(A+B+C, 9) / 形势身份(D+E, 5)

用法: python scripts/_tmp_p110_shap_ablation.py
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
from contextual_features import build_contextual_features, FEATURE_COLS

# ---------- 与生产一致的参数 ----------
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

# ---------- 特征分组（依据 P1-10 14 维来源） ----------
GROUP_REST = ["h_rest_days", "a_rest_days", "rest_days_diff"]                 # A 休息天数
GROUP_GAMES = ["h_games_7d", "a_games_7d", "h_games_14d", "a_games_14d"]     # B 赛程密度
GROUP_STREAK = ["h_away_streak", "a_away_streak"]                             # C 连续作战
GROUP_PPG = ["h_ppg_last5", "a_ppg_last5", "ppg_last5_diff"]                  # D 积分/排名压力
GROUP_ID = ["is_derby", "league_exp_diff"]                                    # E 德比/新军身份

GROUP_PHYSICAL = GROUP_REST + GROUP_GAMES + GROUP_STREAK   # 赛程体能 9 维
GROUP_FORM = GROUP_PPG + GROUP_ID                          # 形势身份 5 维


def rps_score(y_true, proba):
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


def fit_two(X_tr, y_tr):
    import lightgbm as lgb
    import xgboost as xgb
    lgb_m = lgb.LGBMClassifier(**LGB_PARAMS)
    lgb_m.fit(X_tr, y_tr)
    xgb_m = xgb.XGBClassifier(**XGB_PARAMS)
    xgb_m.fit(X_tr, y_tr)
    return lgb_m, xgb_m


def blend_proba(lgb_m, xgb_m, X_te):
    return 0.5 * lgb_m.predict_proba(X_te) + 0.5 * xgb_m.predict_proba(X_te)


def gain_table(lgb_m, xgb_m, train_cols, target_cols):
    """XGB/LGB gain 重要性，按训练列序对齐 target_cols 的真实位置，归一化为百分比。"""
    xgb_g = np.asarray(xgb_m.feature_importances_, dtype=float)
    lgb_g = np.asarray(lgb_m.booster_.feature_importance(importance_type='gain'), dtype=float)
    col_list = list(train_cols)
    rows = {}
    for c in target_cols:
        i = col_list.index(c) if c in col_list else -1
        rows[c] = dict(
            xgb=float(xgb_g[i]) if 0 <= i < len(xgb_g) else 0.0,
            lgb=float(lgb_g[i]) if 0 <= i < len(lgb_g) else 0.0,
        )
    return rows


def main():
    print("=" * 80)
    print("[P1-10] 情境化特征 14 维诊断（gain 重要性 + 逐维 LoO + 分组 ablation + 2-fold A/B）")
    print("=" * 80)

    df = load_match_data_odds()
    print(f"比赛数据: {len(df)} 场")

    cache_path = os.path.join(_SCRIPT_DIR, '..', 'logs', '_tmp_p110_p111_base.pkl')
    if os.path.exists(cache_path):
        print("\n[缓存] 加载已构建基线 X_base ...")
        with open(cache_path, 'rb') as f:
            X_base, y = pickle.load(f)
        print(f"   基线维度: {X_base.shape[1]}")
    else:
        print("\n[1] 构建基线特征（slim_odds=True, ts_odds=True）...")
        t0 = time.time()
        X_base, y = build_all_features(df, slim_odds=True, ts_odds=True)
        print(f"   基线维度: {X_base.shape[1]} ({time.time()-t0:.1f}s)")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'wb') as f:
            pickle.dump((X_base, y), f)

    base_dim = X_base.shape[1]

    print("\n[2] 构建情境化特征（14 维）...")
    t0 = time.time()
    ctx_feat = build_contextual_features(df)
    print(f"   情境特征维度: {ctx_feat.shape[1]} ({time.time()-t0:.1f}s)")

    print("\n   特征非零率（数据覆盖/变异度）:")
    for c in FEATURE_COLS:
        nz = (ctx_feat[c] != 0).mean() * 100
        print(f"     {c:18s} 非零 {nz:5.1f}%")

    X_full = pd.concat([X_base.reset_index(drop=True), ctx_feat.reset_index(drop=True)], axis=1)
    y_arr = np.asarray(y)

    # 主诊断 fold: train[:0.8], test[0.8:]（与生产滚动切分一致）
    n = len(y_arr)
    i_tr = int(n * 0.8)
    X_all_tr = X_full.iloc[:i_tr].reset_index(drop=True)
    X_all_te = X_full.iloc[i_tr:].reset_index(drop=True)
    y_tr = y_arr[:i_tr]
    y_te = y_arr[i_tr:]
    print(f"\n主诊断 fold: train[:{i_tr}] ({i_tr} 场) / test[{i_tr}:] ({n - i_tr} 场)")

    # ---------- 阶段 1: full 模型 gain 重要性 ----------
    print("\n" + "=" * 80)
    print(f"[阶段1] full 模型({base_dim}+14 维) 上 14 维的 gain 重要性")
    print("=" * 80)
    t0 = time.time()
    lgb_full, xgb_full = fit_two(X_all_tr, y_tr)
    print(f"   full 模型训练完成 ({time.time()-t0:.1f}s)")
    p_full = blend_proba(lgb_full, xgb_full, X_all_te)
    m_full = metrics(y_te, p_full)
    print(f"   [full 基准] Acc={m_full['acc']:.4f} LogLoss={m_full['ll']:.4f} "
          f"RPS={m_full['rps']:.4f} ECE={m_full['ece']:.4f} DrawRecall={m_full['draw_recall']:.4f}")

    gain = gain_table(lgb_full, xgb_full, X_all_tr.columns, FEATURE_COLS)
    print("\n   {:18s} {:>12s} {:>12s}".format("特征", "XGB gain%", "LGB gain"))
    print("   " + "-" * 46)
    for c in FEATURE_COLS:
        print(f"   {c:18s} {gain[c]['xgb']*100:12.3f} {gain[c]['lgb']:12.3f}")

    # ---------- 阶段 2: 逐维 Leave-One-Out RPS 边际 ----------
    print("\n" + "=" * 80)
    print("[阶段2] 逐维 Leave-One-Out（剔除该维后 RPS 变化）")
    print("         delta = RPS_剔除后 - RPS_full；delta<0 表示剔除后 RPS 变好 → 该维为拖累项")
    print("=" * 80)
    loo = {}
    for drop_c in FEATURE_COLS:
        keep = [c for c in X_full.columns if c != drop_c]
        X_tr_k = X_full.iloc[:i_tr][keep].reset_index(drop=True)
        X_te_k = X_full.iloc[i_tr:][keep].reset_index(drop=True)
        t0 = time.time()
        lgb_m, xgb_m = fit_two(X_tr_k, y_tr)
        p = blend_proba(lgb_m, xgb_m, X_te_k)
        m = metrics(y_te, p)
        delta = m['rps'] - m_full['rps']
        verdict = "拖累项(剔除更优)" if delta < -1e-4 else ("价值项(保留)" if delta > 1e-4 else "中性")
        loo[drop_c] = dict(rps=m['rps'], delta=delta, ll=m['ll'], acc=m['acc'],
                           draw_recall=m['draw_recall'], verdict=verdict)
        print(f"   -{drop_c:18s} RPS={m['rps']:.4f} delta={delta:+.4f} "
              f"Acc={m['acc']:.4f} LL={m['ll']:.4f} DR={m['draw_recall']:.4f}  [{verdict}] "
              f"({time.time()-t0:.1f}s)")

    # ---------- 阶段 3: 分组 ablation ----------
    print("\n" + "=" * 80)
    print("[阶段3] 分组 ablation（单 fold，剔除整组）")
    print("=" * 80)
    groups = {
        "休息天数(rest*3)": GROUP_REST,
        "赛程密度(games*4)": GROUP_GAMES,
        "连续作战(streak*2)": GROUP_STREAK,
        "积分压力(ppg*3)": GROUP_PPG,
        "德比/新军(id*2)": GROUP_ID,
    }
    group_res = {}
    for gname, gcols in groups.items():
        keep = [c for c in X_full.columns if c not in gcols]
        X_tr_k = X_full.iloc[:i_tr][keep].reset_index(drop=True)
        X_te_k = X_full.iloc[i_tr:][keep].reset_index(drop=True)
        t0 = time.time()
        lgb_m, xgb_m = fit_two(X_tr_k, y_tr)
        p = blend_proba(lgb_m, xgb_m, X_te_k)
        m = metrics(y_te, p)
        delta = m['rps'] - m_full['rps']
        group_res[gname] = dict(rps=m['rps'], delta=delta, acc=m['acc'], ll=m['ll'])
        print(f"   剔除 {gname:22s} → RPS={m['rps']:.4f} delta={delta:+.4f} "
              f"Acc={m['acc']:.4f} LL={m['ll']:.4f} ({time.time()-t0:.1f}s)")

    # ---------- 阶段 4: 最终 2-fold 滚动 A/B ----------
    print("\n" + "=" * 80)
    print("[阶段4] 最终 2-fold 滚动 A/B：基线 / full(+14) / 赛程体能(+9) / 形势身份(+5)")
    print("=" * 80)
    subsets = {
        f"base({base_dim})": [],
        "full(+14)": FEATURE_COLS,
        "physical(+9)": GROUP_PHYSICAL,
        "form(+5)": GROUP_FORM,
    }
    cutoffs = [(0.60, 0.80), (0.80, 1.00)]
    final = {k: [] for k in subsets}
    for tr_end, te_end in cutoffs:
        i1, i2 = int(n * tr_end), int(n * te_end)
        print(f"\n   Fold train[:{i1}] test[{i1}:{i2}]:")
        for sname, sfeat in subsets.items():
            cols = [c for c in X_base.columns] + sfeat
            X_s = X_full[cols]
            X_tr_s = X_s.iloc[:i1].reset_index(drop=True)
            X_te_s = X_s.iloc[i1:i2].reset_index(drop=True)
            t0 = time.time()
            lgb_m, xgb_m = fit_two(X_tr_s, y_arr[:i1])
            p = blend_proba(lgb_m, xgb_m, X_te_s)
            m = metrics(y_arr[i1:i2], p)
            final[sname].append(m)
            print(f"   [{sname:14s}] Acc={m['acc']:.4f} LL={m['ll']:.4f} "
                  f"RPS={m['rps']:.4f} ECE={m['ece']:.4f} DR={m['draw_recall']:.4f} "
                  f"({time.time()-t0:.1f}s)")

    print("\n   -- 2-fold 汇总（均值，相对 full） --")
    full_mean = {k: np.nanmean([r[k] for r in final['full(+14)']]) for k in
                 ['acc', 'll', 'rps', 'ece', 'draw_recall']}
    print(f"   {'配置':16s} {'Acc':>8s} {'LogLoss':>9s} {'RPS':>8s} {'ECE':>8s} {'DrawRecall':>10s}")
    for sname in subsets:
        agg = {k: np.nanmean([r[k] for r in final[sname]]) for k in
               ['acc', 'll', 'rps', 'ece', 'draw_recall']}
        d_rps = agg['rps'] - full_mean['rps']
        print(f"   {sname:16s} {agg['acc']:8.4f} {agg['ll']:9.4f} {agg['rps']:8.4f} "
              f"{agg['ece']:8.4f} {agg['draw_recall']:10.4f}  (RPS相对full {d_rps:+.4f})")

    # ---------- 结论汇总 ----------
    print("\n" + "=" * 80)
    print("[结论] 逐维 LoO 判定")
    print("=" * 80)
    keep, drop = [], []
    for c in FEATURE_COLS:
        v = loo[c]
        tag = "拖累" if v['delta'] < -1e-4 else ("价值" if v['delta'] > 1e-4 else "中性")
        (drop if v['delta'] < -1e-4 else keep).append(c)
        print(f"   {c:18s} delta_RPS={v['delta']:+.4f}  → {tag}")
    print(f"\n   建议剔除({len(drop)}): {drop}")
    print(f"   建议保留({len(keep)}): {keep}")

    # 分组汇总
    print("\n[结论] 分组 ablation 汇总（delta = RPS_剔除后 - full，<0 表示该组拖累）")
    for gname, g in group_res.items():
        print(f"   {gname:22s} delta_RPS={g['delta']:+.4f}")

    print("\n[Done] P1-10 诊断完成")


if __name__ == '__main__':
    main()