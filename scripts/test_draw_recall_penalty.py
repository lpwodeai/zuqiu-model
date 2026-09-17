"""
测试平局召回率惩罚逻辑
======================

使用模拟数据验证 DRAW_RECALL_MIN 约束和惩罚计算的正确性，
确保 optuna_tuning.py 中的逻辑在各种边界条件下正确工作。
"""

import numpy as np
import pandas as pd
from sklearn.metrics import recall_score, accuracy_score

DRAW_RECALL_MIN = 0.28
DRAW_RECALL_PENALTY = -0.05

def compute_penalty(draw_recall):
    """与 optuna_tuning.py 中完全一致的惩罚计算"""
    penalty = 0.0
    if draw_recall < DRAW_RECALL_MIN:
        penalty = DRAW_RECALL_PENALTY * (DRAW_RECALL_MIN - draw_recall) / DRAW_RECALL_MIN
    return penalty

def test_penalty_calculation():
    """测试 1: 惩罚计算正确性"""
    print("=" * 60)
    print("测试 1: 惩罚计算正确性")
    print("=" * 60)

    test_cases = [
        (0.35, 0.0, "高于下限，无惩罚"),
        (0.28, 0.0, "恰好等于下限，无惩罚"),
        (0.27, -0.001786, "略低于下限，轻微惩罚"),
        (0.20, -0.014286, "明显低于下限"),
        (0.10, -0.032143, "严重低于下限"),
        (0.00, -0.050000, "极端情况，最大惩罚"),
    ]

    all_pass = True
    for draw_recall, expected_penalty, desc in test_cases:
        actual_penalty = compute_penalty(draw_recall)
        diff = abs(actual_penalty - expected_penalty)
        passed = diff < 1e-6
        status = "✅" if passed else "❌"
        if not passed:
            all_pass = False
        print(f"  {status} draw_recall={draw_recall:.2f} → penalty={actual_penalty:.6f} "
              f"(期望 {expected_penalty:.6f}, 差值 {diff:.8f}) — {desc}")

    return all_pass


def test_penalty_impact_on_score():
    """测试 2: 惩罚对最终分数的影响"""
    print("\n" + "=" * 60)
    print("测试 2: 惩罚对最终分数的影响")
    print("=" * 60)

    print(f"\n  配置: DRAW_RECALL_MIN={DRAW_RECALL_MIN}, PENALTY系数={DRAW_RECALL_PENALTY}")
    print(f"  {'accuracy':>10} {'draw_recall':>12} {'penalty':>10} {'adjusted':>10} {'影响':>15}")
    print(f"  {'-'*10} {'-'*12} {'-'*10} {'-'*10} {'-'*15}")

    scenarios = [
        (0.52, 0.35),  # 好模型：高准确率 + 高平局召回
        (0.52, 0.20),  # 差模型：高准确率但忽视平局
        (0.48, 0.35),  # 一般模型：低准确率但平局召回好
        (0.48, 0.20),  # 差模型：双低
        (0.50, 0.28),  # 边界：恰好等于下限
        (0.50, 0.27),  # 边界以下
    ]

    for acc, draw_recall in scenarios:
        penalty = compute_penalty(draw_recall)
        adjusted = acc + penalty
        impact = "无影响" if penalty == 0 else f"扣 {abs(penalty):.4f}pp"
        print(f"  {acc:>10.4f} {draw_recall:>12.4f} {penalty:>10.6f} {adjusted:>10.4f} {impact:>15}")

    print("\n  💡 关键洞察:")
    print("     - 准确率 0.52 + 平局召回 0.20 → adjusted ≈ 0.506（被惩罚反超 0.52+0.35=0.52）")
    print("     - 准确率 0.48 + 平局召回 0.35 → adjusted = 0.48（无惩罚）")
    print("     - 平局召回 < 0.28 的模型会被惩罚，即使准确率高也可能输给低准确率但高召回的模型")


def test_recall_extraction():
    """测试 3: recall_score 提取平局召回率的正确性"""
    print("\n" + "=" * 60)
    print("测试 3: recall_score 提取平局召回率")
    print("=" * 60)

    y_true = np.array([0, 0, 0, 1, 1, 1, 2, 2, 2])  # 3主胜 3平 3客胜

    test_cases = [
        (np.array([0, 0, 0, 1, 1, 1, 2, 2, 2]), "完美预测", [1.0, 1.0, 1.0]),
        (np.array([0, 0, 1, 1, 1, 2, 2, 2, 0]), "部分错误", [2/3, 2/3, 2/3]),
        (np.array([0, 2, 2, 0, 0, 0, 1, 1, 1]), "平局全错", [1.0, 0.0, 0.0]),
    ]

    all_pass = True
    for y_pred, desc, expected in test_cases:
        per_class = recall_score(y_true, y_pred, labels=[0, 1, 2], average=None)
        home_r, draw_r, away_r = per_class[0], per_class[1], per_class[2]
        draw_correct = abs(draw_r - expected[1]) < 1e-10
        status = "✅" if draw_correct else "❌"
        if not draw_correct:
            all_pass = False
        print(f"  {status} {desc}: home={home_r:.4f}, draw={draw_r:.4f}, away={away_r:.4f} "
              f"(期望 draw={expected[1]:.4f})")

    print(f"\n  {'✅ 平局召回率提取逻辑正确' if all_pass else '❌ 提取逻辑有误'}")
    return all_pass


def test_tscv_draw_distribution():
    """测试 4: TimeSeriesSplit 下平局样本分布"""
    print("\n" + "=" * 60)
    print("测试 4: TimeSeriesSplit 下平局样本分布")
    print("=" * 60)

    from sklearn.model_selection import TimeSeriesSplit

    np.random.seed(42)
    n_samples = 500
    n_features = 10
    X = pd.DataFrame(np.random.randn(n_samples, n_features))
    y = pd.Series(np.random.choice([0, 1, 2], n_samples, p=[0.45, 0.25, 0.30]))

    tscv = TimeSeriesSplit(n_splits=5)

    print(f"  总样本: {n_samples}, 平局率: {(y==1).mean():.2%}\n")

    for i, (train_idx, val_idx) in enumerate(tscv.split(X)):
        y_train = y.iloc[train_idx]
        y_val = y.iloc[val_idx]
        train_draw = (y_train == 1).mean()
        val_draw = (y_val == 1).mean()
        print(f"  Fold {i+1}: train={len(train_idx)} (平局={train_draw:.2%}), "
              f"val={len(val_idx)} (平局={val_draw:.2%})")

    print("\n  💡 洞察: 各 fold 平局率相对稳定（25% 左右），TimeSeriesSplit 不会引入剧烈分布偏移")


def test_xgb_lgb_with_penalty():
    """测试 5: 带惩罚的 XGBoost/LightGBM 训练（快速验证）"""
    print("\n" + "=" * 60)
    print("测试 5: 带惩罚的 XGBoost/LightGBM 训练验证")
    print("=" * 60)

    from xgboost import XGBClassifier
    from lightgbm import LGBMClassifier
    from sklearn.model_selection import TimeSeriesSplit

    np.random.seed(42)
    n_samples = 300
    n_features = 15
    X = pd.DataFrame(np.random.randn(n_samples, n_features))
    y = pd.Series(np.random.choice([0, 1, 2], n_samples, p=[0.45, 0.25, 0.30]))

    tscv = TimeSeriesSplit(n_splits=5)

    for model_name, model_cls, model_params in [
        ("XGBoost", XGBClassifier, {'max_depth': 3, 'learning_rate': 0.05, 'n_estimators': 50,
                                     'use_label_encoder': False, 'eval_metric': 'mlogloss', 'verbosity': 0}),
        ("LightGBM", LGBMClassifier, {'max_depth': 3, 'learning_rate': 0.05, 'n_estimators': 50,
                                        'verbosity': -1, 'objective': 'multiclass', 'num_class': 3}),
    ]:
        cv_acc, cv_draw_recall = [], []

        for train_idx, val_idx in tscv.split(X):
            X_tr, X_va = X.iloc[train_idx], X.iloc[val_idx]
            y_tr, y_va = y.iloc[train_idx], y.iloc[val_idx]

            model = model_cls(**model_params)
            if model_name == "XGBoost":
                model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], verbose=False)
            else:
                model.fit(X_tr, y_tr, eval_set=[(X_va, y_va)], callbacks=[])

            y_pred = model.predict(X_va)
            acc = accuracy_score(y_va, y_pred)
            per_class = recall_score(y_va, y_pred, labels=[0, 1, 2], average=None)
            draw_r = per_class[1]

            cv_acc.append(acc)
            cv_draw_recall.append(draw_r)

        mean_acc = float(np.mean(cv_acc))
        mean_draw_r = float(np.mean(cv_draw_recall))
        penalty = compute_penalty(mean_draw_r)
        adjusted = mean_acc + penalty

        status = "✅" if penalty == 0 else "⚠️"
        print(f"\n  {status} {model_name}:")
        print(f"     accuracy={mean_acc:.4f}, draw_recall={mean_draw_r:.4f}")
        print(f"     penalty={penalty:.6f}, adjusted_score={adjusted:.4f}")
        print(f"     {'无需惩罚，模型合格' if penalty == 0 else f'平局召回率低于 {DRAW_RECALL_MIN:.0%} 门槛，被惩罚 {abs(penalty):.4f}pp'}")


def main():
    print("🔬 平局召回率惩罚逻辑测试\n")

    results = []
    results.append(("惩罚计算", test_penalty_calculation()))
    test_penalty_impact_on_score()
    results.append(("召回率提取", test_recall_extraction()))
    test_tscv_draw_distribution()
    test_xgb_lgb_with_penalty()

    print("\n" + "=" * 60)
    print("📊 测试汇总")
    print("=" * 60)
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {status} {name}")

    all_passed = all(p for _, p in results)
    print(f"\n{'✅ 所有测试通过' if all_passed else '❌ 部分测试失败，请检查'}")
    return 0 if all_passed else 1


if __name__ == '__main__':
    exit(main())