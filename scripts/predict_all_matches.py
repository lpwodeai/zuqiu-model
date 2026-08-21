#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
全量历史数据预测脚本 (正式上线版本)
================================================
基于修复后的模型 (第二轮修复, 时间戳 20260821_170550)，
对全部历史比赛执行预测，完整复现线上推理管线：

  1. 加载 Booster 模型 + scaler + selected_features
  2. XGBoost:   softmax 概率 → Platt 校准 → Threshold(F=1.50)
  3. LightGBM:  softmax 概率 → Platt 校准 → Threshold(F=1.45)

产出:
  - assets/all_matches_predictions_<TS>.csv   逐场预测明细
  - assets/all_matches_predictions_<TS>.json  汇总指标
"""

import os
import sys
import json
import pickle
import warnings

import numpy as np
import pandas as pd
import joblib
import xgboost as xgb
import lightgbm as lgb

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, log_loss

# ============================================================
# 模型版本配置
# ============================================================
TS = '20260821_170550'
ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')

XGB_MODEL_PATH = os.path.join(ASSETS_DIR, f'xgb_model_{TS}.pkl')
LGB_MODEL_PATH = os.path.join(ASSETS_DIR, f'lgb_model_{TS}.pkl')
SCALER_PATH = os.path.join(ASSETS_DIR, f'scaler_{TS}.pkl')
SF_PATH = os.path.join(ASSETS_DIR, f'selected_features_{TS}.pkl')

# Platt 校准参数 (与 train_models.py 保存的 js 一致)
XGB_PLATT = [
    {'a': 4.178814888000488,  'b': -2.236797571182251},
    {'a': 1.296308159828186,  'b': -1.4241137504577637},
    {'a': 3.9587998390197754, 'b': -1.7363663911819458},
]
LGB_PLATT = [
    {'a': 3.7939040118956955, 'b': -2.1015033621539003},
    {'a': 1.128928756967238,  'b': -1.366585863964088},
    {'a': 3.6224614950283605, 'b': -1.6347367083173783},
]

# 平局决策阈值因子
XGB_THRESHOLD_FACTOR = 1.50
LGB_THRESHOLD_FACTOR = 1.45

CLASS_NAMES = ['客胜', '平局', '主胜']
CLASS_MAP = {0: '客胜', 1: '平局', 2: '主胜'}


def apply_platt_scaling(probs, params):
    """Platt 校准，返回归一化后的概率"""
    calibrated = np.zeros_like(probs)
    for cls in range(probs.shape[1]):
        a = params[cls]['a']
        b = params[cls]['b']
        logit = a * probs[:, cls] + b
        calibrated[:, cls] = 1.0 / (1.0 + np.exp(-logit))
    row_sums = calibrated.sum(axis=1, keepdims=True)
    return calibrated / np.maximum(row_sums, 1e-10)


def apply_draw_threshold(probs, factor):
    """平局决策阈值：不修改概率，仅调整 argmax 决策"""
    pred = np.argmax(probs, axis=1)
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred


def load_artifacts():
    print("加载模型产物...")
    xgb_model = pickle.load(open(XGB_MODEL_PATH, 'rb'))
    lgb_model = pickle.load(open(LGB_MODEL_PATH, 'rb'))
    scaler = joblib.load(SCALER_PATH)
    selected_features = pickle.load(open(SF_PATH, 'rb'))
    print(f"  XGBoost 模型: {os.path.basename(XGB_MODEL_PATH)}")
    print(f"  LightGBM 模型: {os.path.basename(LGB_MODEL_PATH)}")
    print(f"  特征维度: {len(selected_features)}")
    return xgb_model, lgb_model, scaler, selected_features


def align_features(X, selected_features):
    """对齐到训练时的特征列，缺失填 0"""
    aligned = pd.DataFrame(0.0, index=range(len(X)), columns=selected_features)
    for col in selected_features:
        if col in X.columns:
            aligned[col] = X[col].values
    return aligned


def main():
    print("=" * 60)
    print("全量历史数据预测 (修复后模型)")
    print("=" * 60)

    xgb_model, lgb_model, scaler, selected_features = load_artifacts()

    print("\n加载全量比赛数据...")
    df = load_match_data_odds()
    print(f"  共加载 {len(df)} 场比赛")

    print("\n构建特征 (215 维)...")
    X, y = build_all_features(df, include_odds=True)
    print(f"  特征维度: {X.shape[1]}, 标签数: {len(y)}")

    # 特征对齐 + 标准化
    print("\n特征对齐与标准化...")
    X_aligned = align_features(X, selected_features)
    X_scaled = scaler.transform(X_aligned)

    # ============ XGBoost 预测 ============
    print("\nXGBoost 预测 + Platt + Threshold(F=1.50)...")
    dmat = xgb.DMatrix(X_scaled)
    xgb_raw = xgb_model.predict(dmat).reshape(-1, 3)
    xgb_probs = apply_platt_scaling(xgb_raw, XGB_PLATT)
    xgb_pred = apply_draw_threshold(xgb_probs, XGB_THRESHOLD_FACTOR)

    # ============ LightGBM 预测 ============
    print("LightGBM 预测 + Platt + Threshold(F=1.45)...")
    lgb_raw = lgb_model.predict(X_scaled).reshape(-1, 3)
    lgb_probs = apply_platt_scaling(lgb_raw, LGB_PLATT)
    lgb_pred = apply_draw_threshold(lgb_probs, LGB_THRESHOLD_FACTOR)

    # ============ 构建明细 DataFrame ============
    y_arr = y.values if hasattr(y, 'values') else np.array(y)

    # 尝试提取展示列
    result_df = pd.DataFrame(index=range(len(df)))
    for col in ['date', 'home_team', 'away_team', 'home_team_name', 'away_team_name',
                'competition_name', 'match_id']:
        if col in df.columns:
            result_df[col] = df[col].values

    result_df['actual_result'] = y_arr
    result_df['actual_label'] = [CLASS_MAP.get(int(v), str(v)) for v in y_arr]

    # XGBoost
    result_df['xgb_raw_away'] = xgb_raw[:, 0]
    result_df['xgb_raw_draw'] = xgb_raw[:, 1]
    result_df['xgb_raw_home'] = xgb_raw[:, 2]
    result_df['xgb_platt_away'] = xgb_probs[:, 0]
    result_df['xgb_platt_draw'] = xgb_probs[:, 1]
    result_df['xgb_platt_home'] = xgb_probs[:, 2]
    result_df['xgb_pred'] = xgb_pred
    result_df['xgb_pred_label'] = [CLASS_MAP.get(int(v), str(v)) for v in xgb_pred]
    result_df['xgb_correct'] = (xgb_pred == y_arr).astype(int)

    # LightGBM
    result_df['lgb_raw_away'] = lgb_raw[:, 0]
    result_df['lgb_raw_draw'] = lgb_raw[:, 1]
    result_df['lgb_raw_home'] = lgb_raw[:, 2]
    result_df['lgb_platt_away'] = lgb_probs[:, 0]
    result_df['lgb_platt_draw'] = lgb_probs[:, 1]
    result_df['lgb_platt_home'] = lgb_probs[:, 2]
    result_df['lgb_pred'] = lgb_pred
    result_df['lgb_pred_label'] = [CLASS_MAP.get(int(v), str(v)) for v in lgb_pred]
    result_df['lgb_correct'] = (lgb_pred == y_arr).astype(int)

    # 保存明细 CSV
    csv_path = os.path.join(ASSETS_DIR, f'all_matches_predictions_{TS}.csv')
    result_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"\n  预测明细已保存: {csv_path}")

    # ============ 汇总指标 ============
    print("\n" + "=" * 60)
    print("全量预测汇总")
    print("=" * 60)

    summary = {
        'model_version': TS,
        'total_matches': int(len(df)),
        'feature_dim': int(X.shape[1]),
        'pipeline': 'XGB: softmax -> Platt -> Threshold(F=1.50); LGB: softmax -> Platt -> Threshold(F=1.45)',
        'models': {},
    }

    per_model = {
        'XGBoost': (xgb_pred, xgb_probs, XGB_THRESHOLD_FACTOR),
        'LightGBM': (lgb_pred, lgb_probs, LGB_THRESHOLD_FACTOR),
    }

    for mname, (pred, probs, thr) in per_model.items():
        acc = accuracy_score(y_arr, pred)
        ll = log_loss(y_arr, probs, labels=[0, 1, 2])
        cm = confusion_matrix(y_arr, pred, labels=[0, 1, 2])
        report = classification_report(y_arr, pred, labels=[0, 1, 2],
                                       target_names=CLASS_NAMES, output_dict=True,
                                       zero_division=0)
        draw_recall = report['平局']['recall']

        print(f"\n  【{mname}】 (Threshold F={thr})")
        print(f"    全量准确率: {acc:.4f}")
        print(f"    LogLoss:    {ll:.4f}")
        print(f"    平局召回率: {draw_recall:.4f}")
        print(f"    混淆矩阵:")
        print(f"      预测客胜  预测平局  预测主胜")
        print(f"    客胜 {cm[0,0]:4d}   {cm[0,1]:4d}    {cm[0,2]:4d}")
        print(f"    平局 {cm[1,0]:4d}   {cm[1,1]:4d}    {cm[1,2]:4d}")
        print(f"    主胜 {cm[2,0]:4d}   {cm[2,1]:4d}    {cm[2,2]:4d}")

        summary['models'][mname] = {
            'threshold_factor': thr,
            'accuracy': float(acc),
            'logloss': float(ll),
            'draw_recall': float(draw_recall),
            'draw_precision': float(report['平局']['precision']),
            'f1_macro': float(report['macro avg']['f1-score']),
            'confusion_matrix': cm.tolist(),
            'classification_report': report,
        }

    # 逐联赛表现
    summary['by_league'] = {}
    if 'competition_name' in df.columns:
        print("\n  【逐联赛表现 (XGBoost)】")
        league_names = []
        for cname in df['competition_name'].dropna().unique():
            if isinstance(cname, str) and cname.strip():
                league_names.append(cname)
        # 去重保序
        seen = set()
        league_names = [l for l in league_names if not (l in seen or seen.add(l))]

        for league in league_names:
            mask = (df['competition_name'] == league).values
            if mask.sum() < 1:
                continue
            l_acc = accuracy_score(y_arr[mask], xgb_pred[mask])
            l_dr = (xgb_pred[mask][y_arr[mask] == 1] == 1).mean() if (y_arr[mask] == 1).sum() > 0 else 0.0
            summary['by_league'][str(league)] = {
                'n': int(mask.sum()),
                'xgb_accuracy': float(l_acc),
                'xgb_draw_recall': float(l_dr),
            }
            print(f"    {league}: {mask.sum()} 场, 准确率 {l_acc:.4f}, 平局召回 {l_dr:.4f}")

    # 保存汇总 JSON
    json_path = os.path.join(ASSETS_DIR, f'all_matches_predictions_{TS}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n  汇总指标已保存: {json_path}")

    print("\n" + "=" * 60)
    print("全量预测完成!")
    print("=" * 60)


if __name__ == '__main__':
    main()