# -*- coding: utf-8 -*-
"""
P2-10: 位置-specific 球员特征 真实数据 A/B 验证
================================================
在【同一数据、同一时间序列切分、同一训练函数】下，对比：
    - baseline（不含位置特征：原 23 维全队 sofa 特征 + pa_ 可用性特征）
    - full     （含位置特征：额外 11 维 D/M/F 三条线 sofa 特征，主客共 +22 列）

比较 XGBoost / LightGBM / 50:50 blend 的（argmax 无后处理）：
    Val Accuracy / Val LogLoss / Val RPS / 平局召回率

运行：
    python -u scripts/ab_test_position_features.py

说明：
    - feature 构建（build_all_features）只做一次，再用列筛选得到 baseline/full 两组。
    - 复用 train_models 训练函数，与生产链路零漂移。
    - 不写回任何生产模型/数据库。
"""

import sys
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss, recall_score

import train_models as tm
import xgboost as xgb

POS_PREFIXES = ("sofa_fw_", "sofa_mf_", "sofa_df_")


def _draw_recall(y_true, proba):
    pred = np.argmax(proba, axis=1)
    return float(recall_score(y_true, pred, labels=[0, 1, 2], average=None)[1])


def _metrics(y_val, proba):
    pred = np.argmax(proba, axis=1)
    return {
        'accuracy': float(accuracy_score(y_val, pred)),
        'log_loss': float(log_loss(y_val, proba, labels=[0, 1, 2])),
        'rps': float(tm.compute_rps(np.asarray(y_val), np.asarray(proba))),
        'draw_recall': _draw_recall(y_val, proba),
    }


def run_training(X_tr, y_tr, X_va, y_va, sw):
    """训练 XGB + LGB，返回 blend 指标。"""
    # 关闭 focal loss（用生产默认）
    xgb_model, _ = tm.train_xgboost(X_tr, y_tr, X_va, y_va, sample_weights=sw)
    lgb_model, _ = tm.train_lightgbm(X_tr, y_tr, X_va, y_va, sample_weights=sw)
    if xgb_model is None or lgb_model is None:
        return None, None
    xgb_p = xgb_model.predict(xgb.DMatrix(X_va, label=y_va))
    lgb_p = tm._lgb_predict_proba(lgb_model, X_va)
    per = {'XGBoost': _metrics(y_va, xgb_p), 'LightGBM': _metrics(y_va, lgb_p)}
    blend = _metrics(y_va, 0.5 * xgb_p + 0.5 * lgb_p)
    return per, blend


if __name__ == '__main__':
    print("=" * 70)
    print("P2-10 位置-specific 球员特征 A/B 验证")
    print("=" * 70)

    tm.USE_FOCAL_LOSS = False  # 确保用生产默认（交叉熵 + class_weights）

    print("\n[1] 加载数据 + 构建特征（仅一次）...")
    df = tm.load_match_data_odds()
    print(f"    共 {len(df)} 场")
    include_odds = tm.CONFIG.get('training', {}).get('include_odds_features', True)
    X, y = tm.build_all_features(df, include_odds=include_odds, ts_odds=True, consensus_odds=True)
    print(f"    完整特征维度: {X.shape[1]}")

    # 识别位置-specific 特征列
    pos_cols = [c for c in X.columns if c.startswith(POS_PREFIXES)]
    print(f"    位置-specific 特征列数: {len(pos_cols)}")
    if len(pos_cols) == 0:
        print("    ❌ 未检测到位置特征列，请先运行 features/sofascore_pre_match_features.py --output db")
        sys.exit(1)

    sample_weights = tm.create_sample_weights(df)
    validation_split = tm.CONFIG.get('training', {}).get('validation_split', 0.2)
    train_size = int(len(df) * (1 - validation_split))

    results = {}
    for mode, X_mode in [('baseline', X.drop(columns=pos_cols)), ('full', X)]:
        print(f"\n[2] {mode} 特征集（{X_mode.shape[1]} 维）训练...")
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_mode)
        X_scaled_df = pd.DataFrame(X_scaled, columns=X_mode.columns, index=X_mode.index)
        X_tr = X_scaled_df.iloc[:train_size].values
        X_va = X_scaled_df.iloc[train_size:].values
        y_tr = y.iloc[:train_size].values
        y_va = y.iloc[train_size:].values
        per, blend = run_training(X_tr, y_tr, X_va, y_va, sample_weights)
        results[mode] = {'per': per, 'blend': blend}

    # 汇总
    print("\n\n" + "=" * 70)
    print("A/B 结果汇总（argmax 无后处理）")
    print("=" * 70)
    b = results['baseline']
    f = results['full']

    for key in ['XGBoost', 'LightGBM', 'blend']:
        bm = b['per'].get(key) if key != 'blend' else b['blend']
        fm = f['per'].get(key) if key != 'blend' else f['blend']
        if bm is None or fm is None:
            continue
        print(f"\n--- {key} ---")
        print(f"  {'指标':<14}{'baseline':>12}{'full':>12}{'Δ(F-B)':>12}")
        for metric in ['accuracy', 'log_loss', 'rps', 'draw_recall']:
            d = fm[metric] - bm[metric]
            tag = ''
            if metric == 'draw_recall':
                tag = ' ← 提升' if d > 0.001 else (' ← 下降' if d < -0.001 else '')
            elif metric in ('log_loss', 'rps'):
                tag = ' ← 改善' if d < -0.0005 else (' ← 劣化' if d > 0.0005 else '')
            else:
                tag = ' ← 提升' if d > 0.001 else (' ← 下降' if d < -0.001 else '')
            print(f"  {metric:<14}{bm[metric]:>12.4f}{fm[metric]:>12.4f}{d:>+12.4f}{tag}")

    blend_b = b['blend']
    blend_f = f['blend']
    print("\n判定（blend 50:50）:")
    print(f"  RPS:        {blend_b['rps']:.4f} → {blend_f['rps']:.4f} ({'+' if blend_f['rps']>blend_b['rps'] else ''}{blend_f['rps']-blend_b['rps']:.4f})")
    print(f"  平局召回:   {blend_b['draw_recall']:.4f} → {blend_f['draw_recall']:.4f} ({'+' if blend_f['draw_recall']>blend_b['draw_recall'] else ''}{blend_f['draw_recall']-blend_b['draw_recall']:.4f})")
    print(f"  Accuracy:   {blend_b['accuracy']:.4f} → {blend_f['accuracy']:.4f}")

    # 落盘
    out_path = BASE_DIR / "assets" / f"ab_test_position_features_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    payload = {
        'baseline': results['baseline'],
        'full': results['full'],
        'pos_feature_cols': pos_cols,
        'val_size': int(len(X_va)),
        'draw_share': float((pd.Series(y_va) == 1).mean()),
    }
    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
    print("完成。")