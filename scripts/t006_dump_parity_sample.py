#!/usr/bin/env python3
"""
生成 T-006 parity 测试样本,供 tests/verify-t006-lowgoal.js 对比 JS vs Python 概率差异。

输出 tests/fixtures/t006_parity_sample.json,含:
  - features: 22 维特征字典(已知低进球场景:法甲 0:0)
  - feature_cols: 特征名顺序
  - expected_probability: Python model.predict_proba 期望概率
  - expected_raw_score: Python booster.predict 期望 raw_score(含 base)
  - base: dump 的 average_output(布尔,参考用)

JS 端通过 predictT006Lowgoal(features) 计算概率,与 expected_probability 对比,
差异应 < 0.01(浮点容差)。
"""
import json
import pickle
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PKL = BASE / 'assets' / 't006_lowgoal_classifier_v1.pkl'
OUT_DIR = BASE / 'tests' / 'fixtures'
OUT = OUT_DIR / 't006_parity_sample.json'


def main():
    if not PKL.exists():
        print(f'❌ pkl 不存在: {PKL}')
        return 1

    print(f'📦 加载: {PKL}')
    with open(PKL, 'rb') as f:
        bundle = pickle.load(f)

    model = bundle['model']
    feature_cols = bundle['feature_cols']

    # 构造已知低进球场景特征(法甲 0:0,小球高概率)
    # 数值参考 t006_lowgoal_classifier.py 训练数据的典型法甲低进球样本
    sample_features = {
        'score_implied_total': 1.8,
        'odds_00': 8.5,
        'odds_11': 6.5,
        'odds_10': 7.5,
        'odds_01': 7.5,
        'prob_draw': 0.31,
        'draw': 3.2,
        'avg_draw_score_odds': 7.5,
        'margin': 5.5,
        'lambda_asymmetry': 0.12,
        'is_ligue1': 1,
        'is_serie_a': 0,
        'is_la_liga': 0,
        'is_premier_league': 0,
        'is_bundesliga': 0,
        'draw_over_implied': 1.78,
        'prob_draw_x_implied': 0.558,
        'prob_nondraw_x_asym': 0.083,
        'odds_draw_ratio': 0.31,
        'low_score_odds_sum': 30.0,
        'low_score_prob_sum': 0.533,
        'draw_minus_asym': 2.0,
    }

    # 校验特征完整性
    missing = [k for k in feature_cols if k not in sample_features]
    if missing:
        print(f'❌ 特征缺失: {missing}')
        return 1

    # 按 feature_cols 顺序构造特征向量
    X = [[sample_features[k] for k in feature_cols]]

    # Python 端 ground truth
    p_expected = float(model.predict_proba(X)[0, 1])
    raw_expected = float(model.booster_.predict(X)[0])
    dump = model.booster_.dump_model()

    out = {
        'features': sample_features,
        'feature_cols': feature_cols,
        'expected_probability': p_expected,
        'expected_raw_score': raw_expected,
        'average_output': dump.get('average_output'),
        'objective': dump.get('objective'),
        'best_threshold': float(bundle['best_threshold']),
        'base_rate': bundle['n_lowgoal'] / bundle['n_samples'],
        'n_samples': bundle['n_samples'],
        'n_lowgoal': bundle['n_lowgoal'],
        'description': '法甲 0:0 低进球场景,用于 JS vs Python parity 验证'
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f'✅ parity 样本已生成: {OUT}')
    print(f'   expected_probability = {p_expected:.6f}')
    print(f'   expected_raw_score   = {raw_expected:.6f}')
    print(f'   best_threshold = {out["best_threshold"]:.4f}')
    print(f'   base_rate = {out["base_rate"]:.4f}')
    print(f'\n💡 JS 端 predictT006Lowgoal(features) 应给出 ~{p_expected:.4f},差异 < 0.01')


if __name__ == '__main__':
    sys_exit = main()
    import sys
    sys.exit(sys_exit or 0)
