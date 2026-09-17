# -*- coding: utf-8 -*-
"""P3: 阈值 factor 细化网格校准（全局 + 逐联赛）

背景:
  - 决策阈值调整 draw_threshold_factor 不修改概率，仅在分类时降低平局阈值，
    相比 class_weight 硬拉平局不破坏概率校准（ECE 恒不变）。
  - 本脚本将搜索网格细化到 1.02–1.10（步长 0.02），含 argmax 基线 (1.00)，
    并针对五大联赛分别校准，输出推荐的全局 factor 与联赛分档 factor。

数据/模型:
  - 使用最新 LightGBM 模型、selected_features、scaler（assets/ 内按 mtime 自动选最新）。
  - 特征对齐：缺失的 selected_features 列补 0，兼容历史 210 维（含未归一化
    league_西甲2026-2027赛季 列）模型，当前 pipeline 已归一化为 209 维。

运行:
  python scripts/calibrate_threshold_factor.py
"""
import os
import sys
import json
import warnings
from datetime import datetime

import numpy as np

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features  # noqa: E402
from sklearn.metrics import accuracy_score  # noqa: E402

MODEL_DIR = os.path.join(BASE_DIR, 'assets')

# 完整网格：
#   低区 [0.80~0.95]：反压低平局（抬高平局门槛，边界平局降级为主胜/客胜）
#   argmax 基线 1.00 + 细化上浮网格 [1.02~1.10]（压低平局门槛，更多平局）
# 注：factor <= 0 或 factor == 1.0 均等价 argmax；配置侧用 0.0 表示「不调整」。
LOW_GRID = [0.80, 0.85, 0.90, 0.95]
FULL_GRID = LOW_GRID + [1.00, 1.02, 1.04, 1.06, 1.08, 1.10]

LEAGUES = {
    'PL': '英超',
    'BL1': '德甲',
    'IT': '意甲',
    'LaLiga': '西甲',
    'FL1': '法甲',
}


def latest_asset(prefix):
    files = [f for f in os.listdir(MODEL_DIR)
             if f.startswith(prefix) and f.endswith('.pkl')]
    if not files:
        raise FileNotFoundError(f'未找到 {prefix}*.pkl 于 {MODEL_DIR}')
    latest = max(files, key=lambda f: os.path.getmtime(os.path.join(MODEL_DIR, f)))
    return os.path.join(MODEL_DIR, latest)


def apply_draw_threshold(probs, factor):
    """决策阈值：不修改概率，仅在分类时调整平局阈值。

    - factor <= 0: argmax（不做调整）
    - factor > 1:  压低平局门槛，更多平局（上浮平局召回）
    - 0 < factor < 1: 抬高平局门槛，边界平局降级为主胜/客胜（反压低平局）
    """
    if factor is None or factor <= 0:
        return np.argmax(probs, axis=1)
    draw_wins = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    non_draw = np.where(probs[:, 2] >= probs[:, 0], 2, 0)  # 非平局→主胜(2)/客胜(0)
    return np.where(draw_wins, 1, non_draw)


def align(X, selected_features):
    """将 X 对齐到 selected_features，缺失列补 0（兼容历史 210 维模型）。"""
    import pandas as pd
    X_aligned = pd.DataFrame(0.0, index=range(len(X)), columns=selected_features)
    for col in selected_features:
        if col in X.columns:
            X_aligned[col] = X[col].values
    return X_aligned


def evaluate(y, probs, factor):
    pred = apply_draw_threshold(probs, factor)
    acc = float(accuracy_score(y, pred))
    draw_mask = y == 1
    draw_recall = float((pred[draw_mask] == 1).mean()) if draw_mask.sum() > 0 else 0.0
    pred_draw = pred == 1
    draw_precision = float((y[pred_draw] == 1).mean()) if pred_draw.sum() > 0 else 0.0
    draw_rate = float(pred_draw.mean())
    return acc, draw_recall, draw_precision, draw_rate


def search(tag, y, probs):
    actual_draw_rate = float((y == 1).mean())
    print(f'\n  {tag}: n={len(y)}, 实际平局率={actual_draw_rate * 100:.1f}%')
    print(f'  {"factor":>8} {"准确率":>8} {"平局召回":>8} {"平局精确":>8} {"平局预测率":>10}')
    print('  ' + '-' * 48)
    rows = []
    for factor in FULL_GRID:
        acc, dr, dp, rr = evaluate(y, probs, factor)
        rows.append({'factor': factor, 'accuracy': acc, 'draw_recall': dr,
                     'draw_precision': dp, 'draw_rate': rr})
        print(f'  {factor:>8.2f} {acc * 100:>7.2f}% {dr * 100:>8.1f}% '
              f'{dp * 100:>8.1f}% {rr * 100:>10.1f}%')

    # 推荐规则1：准确率最高的 factor（平手取离 argmax(1.0) 最近者）
    best_acc = max(rows, key=lambda r: (r['accuracy'], -abs(r['factor'] - 1.0)))
    # 推荐规则2：平局预测率最接近实际平局率的 factor
    best_rate = min(rows, key=lambda r: abs(r['draw_rate'] - actual_draw_rate))
    return rows, best_acc, best_rate, actual_draw_rate


def main():
    import pickle
    import joblib

    lgb_path = latest_asset('lgb_model_')
    scaler_path = latest_asset('scaler_')
    sf_path = latest_asset('selected_features_')
    print(f'模型: {os.path.basename(lgb_path)}')
    print(f'标准化器: {os.path.basename(scaler_path)}')
    print(f'特征集: {os.path.basename(sf_path)}')

    lgb = pickle.load(open(lgb_path, 'rb'))
    scaler = joblib.load(scaler_path)
    selected_features = pickle.load(open(sf_path, 'rb'))
    print(f'selected_features 维度: {len(selected_features)}')

    print('\n加载比赛数据 + 构建特征（全量）...')
    df = load_match_data_odds()
    X_all, y_all = build_all_features(df, include_odds=True)
    assert len(X_all) == len(df) == len(y_all), 'X/df/y 行数不一致，无法按联赛切分'

    # 特征对齐（兼容 210 维旧模型 vs 209 维当前 pipeline）
    missing = [c for c in selected_features if c not in X_all.columns]
    if missing:
        print(f'  [WARN] selected_features 中 {len(missing)} 列不在当前 X（历史未归一化联赛列），补 0 处理: {missing[:5]}')
    X_aligned = align(X_all, selected_features)
    X_scaled = scaler.transform(X_aligned)
    probs = lgb.predict(X_scaled)
    if probs.ndim == 1:
        probs = probs.reshape(-1, 3)
    print(f'  预测概率 shape: {probs.shape}')

    y_np = y_all.values.astype(int)
    league_arr = df['competition_name'].values

    results = {}

    # 全局
    print('\n' + '=' * 64)
    print('全局 draw_threshold_factor 细化校准')
    print('=' * 64)
    g_rows, g_best_acc, g_best_rate, g_actual = search('全局', y_np, probs)
    results['global'] = {
        'actual_draw_rate': g_actual, 'rows': g_rows,
        'best_accuracy_factor': g_best_acc['factor'],
        'best_rate_factor': g_best_rate['factor'],
    }

    # 逐联赛
    print('\n' + '=' * 64)
    print('联赛分档 draw_threshold_factor 校准')
    print('=' * 64)
    league_summary = {}
    for code, name in LEAGUES.items():
        mask = league_arr == name
        if mask.sum() == 0:
            print(f'\n  {name} ({code}): 无数据，跳过')
            league_summary[code] = {'n': 0, 'note': '无数据'}
            continue
        y_lg = y_np[mask]
        p_lg = probs[mask]
        rows, best_acc, best_rate, actual = search(f'{name} ({code})', y_lg, p_lg)
        results[code] = {
            'actual_draw_rate': actual, 'rows': rows,
            'best_accuracy_factor': best_acc['factor'],
            'best_rate_factor': best_rate['factor'],
        }
        league_summary[code] = {
            'n': int(mask.sum()),
            'actual_draw_rate': actual,
            'best_accuracy_factor': best_acc['factor'],
            'best_accuracy': best_acc['accuracy'],
            'best_rate_factor': best_rate['factor'],
        }

    # 汇总建议
    print('\n' + '=' * 64)
    print('推荐结果汇总')
    print('=' * 64)
    print(f'\n  全局最佳准确率 factor = {g_best_acc["factor"]} (acc={g_best_acc["accuracy"] * 100:.2f}%)')
    print(f'  全局平局预测率最接近实际 factor = {g_best_rate["factor"]}')
    print('\n  联赛分档（准确率最优）:')
    for code, name in LEAGUES.items():
        s = league_summary[code]
        if s.get('n', 0) == 0:
            print(f'    {code:<6} {name}: 无数据')
            continue
        print(f'    {code:<6} {name}: n={s["n"]}, 实际平局率={s["actual_draw_rate"] * 100:.1f}%, '
              f'最佳 factor={s["best_accuracy_factor"]} (acc={s["best_accuracy"] * 100:.2f}%)')

    out = {
        'experiment': 'draw_threshold_factor_refined_calibration',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'model': os.path.basename(lgb_path),
        'selected_features_dim': len(selected_features),
        'n_samples': int(len(y_np)),
        'grid': FULL_GRID,
        'results': results,
    }
    out_path = os.path.join(MODEL_DIR, f'draw_threshold_calibration_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'\n结果已保存: {out_path}')


if __name__ == '__main__':
    main()