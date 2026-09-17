#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""严格时序 OOF（Out-of-Fold）预测生成脚本。

目的：用当前 208 维模型特征集 + 严格时间序列交叉验证，为全量有赛果比赛
生成无时序泄漏的 OOF 概率，供 EV 期望值引擎回测评估真实 edge。

背景：此前 ev_backtest 使用 all_matches_predictions_20260821_170550.csv
（全量训练 → 全量预测）回测得到 ROI +17~22%、平均 EV +27%，判定为时序泄漏
所致。本脚本改用 TimeSeriesSplit 滚动窗口，每条验证样本仅由其「之前时间
窗口」训练的模型预测。

实现要点：
1. 特征构建与 train_models.main() 生产管线完全一致：
     build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
2. 对齐到当前 208 维 selected_features（20260828_174103），排除死特征/漂移列。
3. 复用 train_models 的 Optuna 最优超参与 train_xgboost / train_lightgbm，
   确保与线上推理管线同源。
4. Platt 校准在「折内训练集」上拟合（fit on train），再应用到验证集，
   避免用验证集标签拟合校准系数造成的二次泄漏。

产出：
  assets/oof_predictions_<TS>.csv  逐场 OOF 概率（XGB/LGB raw + Platt 校准 + 实际赛果）

用法：
  python generate_oof_predictions.py
  python generate_oof_predictions.py --n-splits 5
"""

import os
import sys
import pickle
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

from feature_utils import load_match_data_odds, build_all_features  # noqa: E402
import train_models as TM  # 复用生产训练函数与 Optuna 最优超参  # noqa: E402

ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')

# 当前 208 维模型特征集（最新生产产物）
FEATURE_TS = '20260828_174103'
SF_PATH = os.path.join(ASSETS_DIR, f'selected_features_{FEATURE_TS}.pkl')

CLASS_NAMES = ['客胜', '平局', '主胜']
CLASS_MAP = {0: '客胜', 1: '平局', 2: '主胜'}


def align_features(X, selected_features):
    """对齐到训练时的特征列，缺失填 0（与 predict_all_matches.py 一致）。"""
    aligned = pd.DataFrame(0.0, index=range(len(X)), columns=selected_features)
    for col in selected_features:
        if col in X.columns:
            aligned[col] = X[col].values
    return aligned


def main(n_splits=5):
    print('=' * 60)
    print('严格时序 OOF 预测生成')
    print('=' * 60)

    print('\n1. 加载比赛数据...')
    df = load_match_data_odds()
    print(f'   共加载 {len(df)} 场比赛')

    print('\n2. 构建特征（与 train_models 生产管线一致）...')
    raw_X, y = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
    print(f'   原始特征维度: {raw_X.shape[1]}, 标签数: {len(y)}')

    print('\n3. 对齐到当前 208 维 selected_features...')
    selected_features = pickle.load(open(SF_PATH, 'rb'))
    print(f'   selected_features 维度: {len(selected_features)}')
    covered = sum(1 for c in selected_features if c in raw_X.columns)
    print(f'   特征覆盖: {covered}/{len(selected_features)}')
    if covered < len(selected_features):
        missing = [c for c in selected_features if c not in raw_X.columns]
        print(f'   [WARN] 缺失特征 {len(missing)} 个，将被填 0: {missing[:20]}...')
    X = align_features(raw_X, selected_features)

    print('\n4. 构建异常样本加权（与训练一致）...')
    sample_weights = TM.create_sample_weights(df)

    print(f'\n5. 严格时序交叉验证（TimeSeriesSplit, {n_splits} 折）...')
    tscv = TimeSeriesSplit(n_splits=n_splits)

    oof_folds = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        print(f'\n--- 第 {fold + 1}/{n_splits} 折 ---')
        X_train_fold = X.iloc[train_idx]
        X_val_fold = X.iloc[val_idx]
        y_train_fold = y.iloc[train_idx]
        y_val_fold = y.iloc[val_idx]
        fold_weights = sample_weights[train_idx]

        print(f'   训练集: {len(X_train_fold)} 场, 验证集(OOF): {len(X_val_fold)} 场')

        scaler_fold = StandardScaler()
        X_train_scaled = scaler_fold.fit_transform(X_train_fold)
        X_val_scaled = scaler_fold.transform(X_val_fold)

        # --- XGBoost ---
        xgb_model, _ = TM.train_xgboost(
            X_train_scaled, y_train_fold.values,
            X_val_scaled, y_val_fold.values,
            sample_weights=fold_weights)

        xgb_raw_val = xgb_model.predict(xgb.DMatrix(X_val_scaled))
        # Platt 校准系数在折内训练集上拟合，避免验证集标签泄漏
        xgb_raw_train = xgb_model.predict(xgb.DMatrix(X_train_scaled))
        xgb_platt = TM.fit_platt_scaling(y_train_fold.values, xgb_raw_train)
        xgb_probs_val = TM.apply_platt_scaling(xgb_raw_val, xgb_platt)

        # --- LightGBM ---
        lgb_model, _ = TM.train_lightgbm(
            X_train_scaled, y_train_fold.values,
            X_val_scaled, y_val_fold.values,
            sample_weights=fold_weights)

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

    print('\n6. 合并 OOF 预测并保存...')
    oof_all = pd.concat(oof_folds, ignore_index=True)
    oof_all = oof_all.sort_values('date').reset_index(drop=True) if 'date' in oof_all.columns else oof_all

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(ASSETS_DIR, f'oof_predictions_{ts}.csv')
    oof_all.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f'   OOF 预测已保存: {csv_path}')
    print(f'   总 OOF 场次: {len(oof_all)}')

    # --- OOF 汇总指标（快速 sanity check）---
    print('\n' + '=' * 60)
    print('OOF 汇总指标（无时序泄漏）')
    print('=' * 60)
    y_arr = oof_all['actual_result'].values.astype(int)
    for mname, prefix in [('XGBoost', 'xgb'), ('LightGBM', 'lgb')]:
        p = oof_all[[f'{prefix}_platt_away', f'{prefix}_platt_draw', f'{prefix}_platt_home']].values
        pred = np.argmax(p, axis=1)
        acc = (pred == y_arr).mean()
        # log_loss
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
    print('\n完成。下一步：python ev_backtest.py --csv ' + csv_path)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='严格时序 OOF 预测生成')
    parser.add_argument('--n-splits', type=int, default=5, help='时间序列交叉验证折数')
    args = parser.parse_args()
    main(n_splits=args.n_splits)