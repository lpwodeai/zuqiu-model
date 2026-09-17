# -*- coding: utf-8 -*-
"""
P2-13: Focal Loss 真实数据 A/B 验证（网格搜索版）
==================================================
在【同一数据、同一时间序列切分、同一 Optuna 参数】下：
    - baseline（交叉熵 + 反频率 class_weights，USE_FOCAL_LOSS=False）
    - Focal Loss 参数网格：gamma ∈ {0.5, 1.0, 1.5} × alpha_draw ∈ {1.4, 2.0, 3.0}

比较 XGBoost / LightGBM / 50:50 blend 的（argmax 无后处理）：
    Val Accuracy / Val LogLoss / Val RPS / 平局召回率

P2-13 验收：argmax 平局召回率 ≥ 0.30 不依赖后处理，且 RPS 不劣化（越小越好）。

运行：
    python -u scripts/ab_test_focal_loss.py

说明：
    - 纯对比实验，不写回任何生产模型/数据库/校准参数文件。
    - 复用 train_models 训练函数，与生产链路零漂移。
    - feature 构建只做一次，网格搜索多组参数复用同一份 X/y。
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


def _draw_recall(y_true, proba):
    pred = np.argmax(proba, axis=1)
    rec = recall_score(y_true, pred, labels=[0, 1, 2], average=None)
    return float(rec[1])


def _metrics(y_val, proba):
    pred = np.argmax(proba, axis=1)
    return {
        'accuracy': float(accuracy_score(y_val, pred)),
        'log_loss': float(log_loss(y_val, proba, labels=[0, 1, 2])),
        'rps': float(tm.compute_rps(np.asarray(y_val), np.asarray(proba))),
        'draw_recall': _draw_recall(y_val, proba),
    }


def run_one():
    """训练 XGB + LGB（按当前 tm.USE_FOCAL_LOSS/GAMMA/ALPHA 全局配置）。"""
    xgb_model, _ = tm.train_xgboost(
        X_train.values, y_train.values, X_val.values, y_val.values,
        sample_weights=sample_weights)
    lgb_model, _ = tm.train_lightgbm(
        X_train.values, y_train.values, X_val.values, y_val.values,
        sample_weights=sample_weights)

    out = {}
    blend = None
    if xgb_model is not None and lgb_model is not None:
        xgb_p = xgb_model.predict(xgb.DMatrix(X_val.values, label=y_val.values))
        lgb_p = tm._lgb_predict_proba(lgb_model, X_val.values)
        out['XGBoost'] = _metrics(y_val, xgb_p)
        out['LightGBM'] = _metrics(y_val, lgb_p)
        blend = _metrics(y_val, 0.5 * xgb_p + 0.5 * lgb_p)
    return out, blend


if __name__ == '__main__':
    print("=" * 70)
    print("P2-13 Focal Loss 真实数据 A/B 验证（网格搜索）")
    print("=" * 70)

    # 与 train_models.main() 一致的数据加载 + 特征构建 + 切分
    print("\n[1] 加载数据 + 构建特征（仅一次）...")
    df = tm.load_match_data_odds()
    print(f"    共 {len(df)} 场")

    include_odds = tm.CONFIG.get('training', {}).get('include_odds_features', True)
    X, y = tm.build_all_features(df, include_odds=include_odds, ts_odds=True, consensus_odds=True)
    print(f"    特征维度: {X.shape[1]}")

    sample_weights = tm.create_sample_weights(df)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    X_scaled_df = pd.DataFrame(X_scaled, columns=X.columns, index=X.index)

    validation_split = tm.CONFIG.get('training', {}).get('validation_split', 0.2)
    train_size = int(len(df) * (1 - validation_split))
    X_train, X_val = X_scaled_df.iloc[:train_size], X_scaled_df.iloc[train_size:]
    y_train, y_val = y.iloc[:train_size], y.iloc[train_size:]

    print(f"    训练集 {len(X_train)} / 验证集 {len(X_val)} 场")
    print(f"    验证集平局占比: {float((y_val == 1).mean()):.4f}")

    # baseline
    print("\n[2] Baseline（交叉熵 + class_weights）...")
    tm.USE_FOCAL_LOSS = False
    base_per, base_blend = run_one()

    # 网格搜索
    grid = []
    for gamma in [0.5, 1.0, 1.5]:
        for a_draw in [1.4, 2.0, 3.0]:
            grid.append((gamma, a_draw))

    results = {}
    print(f"\n[3] Focal Loss 网格搜索（{len(grid)} 组合）...")
    for i, (gamma, a_draw) in enumerate(grid, 1):
        tm.USE_FOCAL_LOSS = True
        tm.FOCAL_GAMMA = gamma
        tm.FOCAL_ALPHA = [1.0, a_draw, 1.0]
        print(f"\n{'='*70}\n[{i}/{len(grid)}] Focal gamma={gamma}, alpha_draw={a_draw}\n{'='*70}")
        per, blend = run_one()
        results[f"g{gamma}_a{a_draw}"] = {'per': per, 'blend': blend}

    # 汇总
    print("\n\n" + "=" * 90)
    print("汇总（blend 50:50，argmax 无后处理）")
    print("=" * 90)
    header = f"{'配置':<22}{'Acc':>8}{'LogLoss':>9}{'RPS':>8}{'平局召回':>9}  判定"
    print(header)
    print("-" * 90)

    def _row(label, m):
        return f"{label:<22}{m['accuracy']:>8.4f}{m['log_loss']:>9.4f}{m['rps']:>8.4f}{m['draw_recall']:>9.4f}"

    base_rps = base_blend['rps']
    print(_row('baseline', base_blend) + "   ← 基准")

    best = None  # (label, blend, 得分)
    for label in results:
        b = results[label]['blend']
        met = b['draw_recall'] >= 0.30 and b['rps'] <= base_rps + 0.005
        mark = '✅达标' if met else ''
        print(_row(label, b) + f"  {mark}")
        if met:
            # 达标中优先更低的 RPS，其次更高平局召回
            score = (b['rps'], -b['draw_recall'])
            if best is None or score < best[2]:
                best = (label, b, score)

    print("-" * 90)
    print("\n判定（P2-13 验收：blend 平局召回 ≥0.30 且 RPS 不劣化 vs baseline）:")
    if best:
        print(f"  ✅ 有达标组合: {best[0]}  RPS={best[1]['rps']:.4f} 平局召回={best[1]['draw_recall']:.4f}")
    else:
        print("  ❌ 无组合同时满足「平局召回 ≥0.30 且 RPS 不劣化」")

    # 落盘 JSON 供文档引用
    out_path = BASE_DIR / "assets" / f"ab_test_focal_loss_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'baseline': {'per': base_per, 'blend': base_blend},
        'grid': results,
        'val_size': int(len(X_val)),
        'draw_share': float((y_val == 1).mean()),
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")
    print("完成。")