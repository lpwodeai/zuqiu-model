#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""C-20260904-003: 训练端 EV/ROI 目标改造 — OOF 重训 harness。

目的：用 EV-policy Loss（CE 锚点 + λ·EVL，见 ev_loss.py）重训 XGBoost/LightGBM，
在严格时序 OOF 下验证「训练端对 EV/ROI 直接求导」能否修复概率系统性高估
（edge 分桶非单调、>10pp 桶仍负 ROI），并与基线 OOF（generate_oof_predictions.py）
同口径对比。

与基线 OOF 的差异：
  1. 打开 train_models 的 USE_EV_LOSS 开关，并把当前折训练样本的三向市场赔率
     （odds500_ouzhi_summary 收盘均价）按 [客胜, 平局, 主胜] 列序对齐到训练行序，
     经 configure_ev_loss() 绑定到自定义目标。
  2. XGBoost 自定义 obj 下 model.predict() 返回原始 logit，需手动 softmax 还原概率
     （LightGBM 由 train_models._lgb_predict_proba 内部处理）。
  3. 无赔率样本（桥接失败）赔率置 NaN → 该样本退化为纯 CE，模型行为与基线一致。

输出列 schema 与基线 OOF 完全一致（xgb/lgb × raw/platt × away/draw/home），
可直接喂给 edge_monotonic_fix.py --oof <csv> 做同一套 edge 分桶单调性评估。

用法：
  python generate_oof_evloss.py                  # B1: λ=1.0（EV-policy）
  python generate_oof_evloss.py --lambda-ce 0.3  # B2 扫描
  python generate_oof_evloss.py --loss mod       # D1: 市场赔率蒸馏 λ=1.0 γ=1.0
  python generate_oof_evloss.py --loss mod --lambda-mod 3.0 --gamma 0.5 --tag D3
  python generate_oof_evloss.py --loss penalty --lambda-pen 1.0 --odds-thresh 3.5 --tag E1  # 高赔率未命中惩罚
"""

import os
import sys
import pickle
import sqlite3
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features, normalize_team_name  # noqa: E402
import train_models as TM  # 复用生产训练函数与 Optuna 最优超参  # noqa: E402

ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')

# 当前 208 维模型特征集（最新生产产物，与基线 OOF 一致）
FEATURE_TS = '20260828_174103'
SF_PATH = os.path.join(ASSETS_DIR, f'selected_features_{FEATURE_TS}.pkl')

CLASS_NAMES = ['客胜', '平局', '主胜']
CLASS_MAP = {0: '客胜', 1: '平局', 2: '主胜'}

# 赔率列序 [客胜(0), 平局(1), 主胜(2)]，与 ev_loss._ODDS / y 编码一致
AWAY, DRAW, HOME = 0, 1, 2


def build_odds_matrix(df):
    """按 matches 行序构建 (N, 3) 三向收盘赔率矩阵，列序 [客胜, 平局, 主胜]。

    odds500_match.match_id 与 matches.match_id 同构（YYYY-MM-DD_Home_Away），
    直接命中；miss 时用「日期+中文队名」桥回退（与 edge_monotonic_fix 同构）。
    命中: odds_matrix[i] = (avg_live_lose, avg_live_draw, avg_live_win)
    未命中: 全 NaN → 训练时退化为纯 CE。
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()

    odds_map = {}
    cur.execute("SELECT match_id, avg_live_win, avg_live_draw, avg_live_lose FROM odds500_ouzhi_summary")
    for mid, w, d, l in cur.fetchall():
        try:
            hw, dd, aw = float(w), float(d), float(l)
            if hw > 1 and dd > 1 and aw > 1:
                odds_map[mid] = (hw, dd, aw)  # (主, 平, 客)
        except (TypeError, ValueError):
            continue

    bridge = {}
    cur.execute("SELECT match_id, match_date, home_team_cn, away_team_cn FROM odds500_match")
    for mid, md, h, a in cur.fetchall():
        if not mid:
            continue
        bridge[(md or "")[:10], normalize_team_name(h), normalize_team_name(a)] = mid
    conn.close()

    n = len(df)
    odds = np.full((n, 3), np.nan, dtype=np.float64)
    hit = 0
    for i, row in enumerate(df.itertuples(index=False)):
        key = getattr(row, 'match_id', None)
        if key not in odds_map:
            alt = bridge.get((str(getattr(row, 'date', ''))[:10],
                              normalize_team_name(getattr(row, 'home_team_name', '') or ''),
                              normalize_team_name(getattr(row, 'away_team_name', '') or '')))
            key = alt
        if key and key in odds_map:
            hw, dd, aw = odds_map[key]
            odds[i] = (aw, dd, hw)  # 转 [客胜, 平局, 主胜]
            hit += 1
    print(f"   赔率矩阵: {n} 行, 命中 {hit} ({hit / n:.1%}), 未命中 {n - hit} → 纯 CE")
    return odds


def align_features(X, selected_features):
    """对齐到训练时的特征列，缺失填 0（与基线 OOF 一致）。"""
    aligned = pd.DataFrame(0.0, index=range(len(X)), columns=selected_features)
    for col in selected_features:
        if col in X.columns:
            aligned[col] = X[col].values
    return aligned


def _xgb_predict_proba(model, X, ev_loss_on):
    """XGBoost 自定义 obj（EV/Focal）下 predict 返回原始 logit，需 softmax 还原概率。"""
    raw = np.asarray(model.predict(xgb.DMatrix(X)), dtype=np.float64)
    if ev_loss_on:
        raw = raw - raw.max(axis=1, keepdims=True)
        e = np.exp(raw)
        return e / e.sum(axis=1, keepdims=True)
    return raw


def main(n_splits=5, loss='ev', lambda_ce=1.0, lambda_mod=1.0, gamma=1.0,
         lambda_pen=1.0, odds_thresh=3.5, tag=''):
    tag_s = f"_{tag}" if tag else ""
    loss_name = {'ev': 'EV-policy', 'mod': '市场赔率蒸馏(MOD)',
                 'penalty': '高赔率未命中惩罚(Penalty)'}[loss]
    print('=' * 60)
    print(f'{loss_name} OOF 重训（{tag_s}）')
    print('=' * 60)

    # 打开训练端自定义目标开关（生产默认 False，仅本 harness 生效）
    TM.USE_EV_LOSS = (loss == 'ev')
    TM.USE_MOD_LOSS = (loss == 'mod')
    TM.USE_PENALTY_LOSS = (loss == 'penalty')
    if loss == 'ev':
        TM.EV_LOSS_LAMBDA_CE = float(lambda_ce)
        TM.EV_ODDS_CAP = 10.0
        print(f'   USE_EV_LOSS=True, lambda_ce={TM.EV_LOSS_LAMBDA_CE}, odds_cap={TM.EV_ODDS_CAP}')
    elif loss == 'mod':
        TM.MOD_LOSS_LAMBDA = float(lambda_mod)
        TM.MOD_LOSS_GAMMA = float(gamma)
        print(f'   USE_MOD_LOSS=True, lambda_mod={TM.MOD_LOSS_LAMBDA}, gamma={TM.MOD_LOSS_GAMMA}')
    elif loss == 'penalty':
        TM.PENALTY_LOSS_LAMBDA = float(lambda_pen)
        TM.PENALTY_ODDS_THRESH = float(odds_thresh)
        print(f'   USE_PENALTY_LOSS=True, lambda_pen={TM.PENALTY_LOSS_LAMBDA}, '
              f'odds_thresh={TM.PENALTY_ODDS_THRESH}')

    print('\n1. 加载比赛数据...')
    df = load_match_data_odds()
    df = df.sort_values('date').reset_index(drop=True)
    print(f'   共加载 {len(df)} 场比赛')

    print('\n2. 构建特征（与基线 OOF / 生产管线一致）...')
    raw_X, y = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
    print(f'   原始特征维度: {raw_X.shape[1]}, 标签数: {len(y)}')

    print('\n3. 对齐到当前 208 维 selected_features...')
    selected_features = pickle.load(open(SF_PATH, 'rb'))
    covered = sum(1 for c in selected_features if c in raw_X.columns)
    print(f'   特征覆盖: {covered}/{len(selected_features)}')
    if covered < len(selected_features):
        missing = [c for c in selected_features if c not in raw_X.columns]
        print(f'   [WARN] 缺失特征 {len(missing)} 个，将被填 0: {missing[:20]}...')
    X = align_features(raw_X, selected_features)

    print('\n4. 构建三向市场赔率矩阵（对齐 df 行序）...')
    odds_matrix = build_odds_matrix(df)

    print('\n5. 构建异常样本加权（与训练一致）...')
    sample_weights = TM.create_sample_weights(df)

    print(f'\n6. 严格时序交叉验证（TimeSeriesSplit, {n_splits} 折）...')
    tscv = TimeSeriesSplit(n_splits=n_splits)

    oof_folds = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        print(f'\n--- 第 {fold + 1}/{n_splits} 折 ---')
        X_train_fold = X.iloc[train_idx]
        X_val_fold = X.iloc[val_idx]
        y_train_fold = y.iloc[train_idx]
        y_val_fold = y.iloc[val_idx]
        fold_weights = sample_weights[train_idx]
        odds_train_fold = odds_matrix[train_idx]  # (n_tr, 3) 与训练行序 1:1 对齐

        print(f'   训练集: {len(X_train_fold)} 场, 验证集(OOF): {len(X_val_fold)} 场')

        scaler_fold = StandardScaler()
        X_train_scaled = scaler_fold.fit_transform(X_train_fold)
        X_val_scaled = scaler_fold.transform(X_val_fold)

        # --- XGBoost（EV-policy 目标）---
        xgb_model, _ = TM.train_xgboost(
            X_train_scaled, y_train_fold.values,
            X_val_scaled, y_val_fold.values,
            sample_weights=fold_weights, odds_train=odds_train_fold)

        xgb_raw_val = _xgb_predict_proba(xgb_model, X_val_scaled, ev_loss_on=True)
        xgb_raw_train = _xgb_predict_proba(xgb_model, X_train_scaled, ev_loss_on=True)
        xgb_platt = TM.fit_platt_scaling(y_train_fold.values, xgb_raw_train)
        xgb_probs_val = TM.apply_platt_scaling(xgb_raw_val, xgb_platt)

        # --- LightGBM（EV-policy 目标）---
        lgb_model, _ = TM.train_lightgbm(
            X_train_scaled, y_train_fold.values,
            X_val_scaled, y_val_fold.values,
            sample_weights=fold_weights, odds_train=odds_train_fold)

        lgb_raw_val = TM._lgb_predict_proba(lgb_model, X_val_scaled)
        lgb_raw_train = TM._lgb_predict_proba(lgb_model, X_train_scaled)
        lgb_platt = TM.fit_platt_scaling(y_train_fold.values, lgb_raw_train)
        lgb_probs_val = TM.apply_platt_scaling(lgb_raw_val, lgb_platt)

        # --- 收集 OOF 明细 ---
        val_df = df.iloc[val_idx].reset_index(drop=True)
        n = len(val_df)
        oof_rows = pd.DataFrame(index=range(n))

        for col in ['match_id', 'competition_name', 'date',
                    'home_team_name', 'away_team_name']:
            if col in val_df.columns:
                oof_rows[col] = val_df[col].values

        oof_rows['actual_result'] = y_val_fold.values
        oof_rows['actual_label'] = [CLASS_MAP.get(int(v), str(v)) for v in y_val_fold.values]

        # XGBoost 概率列序 = [客胜(0), 平局(1), 主胜(2)]
        oof_rows['xgb_raw_away'] = xgb_raw_val[:, 0]
        oof_rows['xgb_raw_draw'] = xgb_raw_val[:, 1]
        oof_rows['xgb_raw_home'] = xgb_raw_val[:, 2]
        oof_rows['xgb_platt_away'] = xgb_probs_val[:, 0]
        oof_rows['xgb_platt_draw'] = xgb_probs_val[:, 1]
        oof_rows['xgb_platt_home'] = xgb_probs_val[:, 2]

        # LightGBM 概率列序 = [客胜(0), 平局(1), 主胜(2)]
        oof_rows['lgb_raw_away'] = lgb_raw_val[:, 0]
        oof_rows['lgb_raw_draw'] = lgb_raw_val[:, 1]
        oof_rows['lgb_raw_home'] = lgb_raw_val[:, 2]
        oof_rows['lgb_platt_away'] = lgb_probs_val[:, 0]
        oof_rows['lgb_platt_draw'] = lgb_probs_val[:, 1]
        oof_rows['lgb_platt_home'] = lgb_probs_val[:, 2]

        oof_folds.append(oof_rows)

    print('\n7. 合并 OOF 预测并保存...')
    oof_all = pd.concat(oof_folds, ignore_index=True)
    oof_all = oof_all.sort_values('date').reset_index(drop=True) if 'date' in oof_all.columns else oof_all

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    if loss == 'ev':
        loss_code, param_s = 'evloss', f'_l{lambda_ce}'
    elif loss == 'mod':
        loss_code, param_s = 'modloss', f'_l{lambda_mod}_g{gamma}'
    else:
        loss_code, param_s = 'penloss', f'_l{lambda_pen}_t{odds_thresh}'
    csv_path = os.path.join(ASSETS_DIR, f'oof_{loss_code}{param_s}{tag_s}_{ts}.csv')
    oof_all.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'   OOF 预测已保存: {csv_path}')
    print(f'   总 OOF 场次: {len(oof_all)}')

    # --- OOF 汇总指标（快速 sanity check，与基线同口径）---
    print('\n' + '=' * 60)
    print(f'OOF 汇总指标（{loss_name} {param_s}{tag_s}, 无时序泄漏）')
    print('=' * 60)
    y_arr = oof_all['actual_result'].values.astype(int)
    for mname, prefix in [('XGBoost', 'xgb'), ('LightGBM', 'lgb')]:
        p = oof_all[[f'{prefix}_platt_away', f'{prefix}_platt_draw', f'{prefix}_platt_home']].values
        pred = np.argmax(p, axis=1)
        acc = (pred == y_arr).mean()
        eps = 1e-12
        p_clip = np.clip(p, eps, 1 - eps)
        p_clip = p_clip / p_clip.sum(axis=1, keepdims=True)
        ll = -np.mean(np.log(p_clip[np.arange(len(y_arr)), y_arr] + eps))
        draw_recall = ((pred[y_arr == 1] == 1).sum() / max(1, (y_arr == 1).sum()))
        print(f'  {mname:>10}: 准确率 {acc:.4f} | LogLoss {ll:.4f} | 平局召回 {draw_recall:.4f}')

    # 50/50 blend
    xgb_p = oof_all[['xgb_platt_away', 'xgb_platt_draw', 'xgb_platt_home']].values
    lgb_p = oof_all[['lgb_platt_away', 'lgb_platt_draw', 'lgb_platt_home']].values
    blend_p = (xgb_p + lgb_p) / 2
    blend_pred = np.argmax(blend_p, axis=1)
    blend_acc = (blend_pred == y_arr).mean()
    print(f'  {"blend":>10}: 准确率 {blend_acc:.4f}')

    print(f'\n  实际结果分布: ' + ', '.join(
        f'{CLASS_MAP[k]}={int((y_arr == k).sum())}' for k in (0, 1, 2)))
    print('\n完成。下一步: python edge_monotonic_fix.py --oof ' + csv_path)

    return csv_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='训练端自定义目标 OOF 重训（EV-policy / MOD / 高赔率惩罚）')
    parser.add_argument('--n-splits', type=int, default=5, help='时间序列交叉验证折数')
    parser.add_argument('--loss', type=str, default='ev', choices=['ev', 'mod', 'penalty'],
                        help="损失类型：ev=EV-policy（C-20260904-003），mod=市场赔率蒸馏（C-20260904-004），"
                             "penalty=高赔率未命中惩罚（C-20260905-001）")
    parser.add_argument('--lambda-ce', type=float, default=1.0,
                        help='EV-policy 的 EV 分量权重（CE 权重恒 1.0）；B1=1.0, B2/B3 扫描 0.3/3.0')
    parser.add_argument('--lambda-mod', type=float, default=1.0,
                        help='MOD 蒸馏分量权重（CE 权重恒 1.0）；D1=1.0')
    parser.add_argument('--gamma', type=float, default=1.0,
                        help='MOD 软目标温度软化指数（1.0=纯去抽水，<1 更扁平）')
    parser.add_argument('--lambda-pen', type=float, default=1.0,
                        help='高赔率惩罚分量权重（CE 权重恒 1.0）；E1=1.0，E2/E3 扫描 0.3/3.0')
    parser.add_argument('--odds-thresh', type=float, default=3.5,
                        help='高赔率胜方向阈值（客胜/主胜 ≥ 该赔率视为高赔率冷门；>10pp 桶均赔 3.5）')
    parser.add_argument('--tag', type=str, default='', help='输出文件标签（如 B1/B2/B3/D1/E1）')
    args = parser.parse_args()
    main(n_splits=args.n_splits, loss=args.loss, lambda_ce=args.lambda_ce,
         lambda_mod=args.lambda_mod, gamma=args.gamma, lambda_pen=args.lambda_pen,
         odds_thresh=args.odds_thresh, tag=args.tag)
