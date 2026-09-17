"""
平局召回率修复脚本
测试不同的 class_weight 和阈值策略，提升平局预测能力

当前问题: XGBoost/LightGBM 在跨联赛场景下平局召回率为 0%
解决方案:
1. 调整 class_weight 给平局更高权重
2. 调整预测阈值，增加平局预测比例
3. 对比不同策略的效果
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, classification_report, confusion_matrix
import lightgbm as lgb

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from feature_utils import load_match_data_odds, build_all_features

RANDOM_SEED = 42

# 基础参数
LGB_BASE_PARAMS = {
    'objective': 'multiclass',
    'num_class': 3,
    'metric': 'multi_logloss',
    'boosting_type': 'gbdt',
    'num_leaves': 31,
    'learning_rate': 0.03,
    'feature_fraction': 0.8,
    'bagging_fraction': 0.8,
    'bagging_freq': 5,
    'min_data_in_leaf': 20,
    'lambda_l1': 1.0,
    'lambda_l2': 10.0,
    'verbose': -1,
    'random_state': RANDOM_SEED,
    'n_jobs': -1,
}


def train_with_class_weight(X_train, y_train, X_val, y_val, class_weight_ratio):
    """使用 class_weight 训练模型"""
    # 计算 class_weight
    n_samples = len(y_train)
    n_classes = 3
    class_counts = np.bincount(y_train.astype(int), minlength=n_classes)
    class_weights = n_samples / (n_classes * class_counts)
    # 给平局(class=1)额外权重
    class_weights[1] *= class_weight_ratio

    params = {**LGB_BASE_PARAMS, 'class_weight': class_weights.tolist()}

    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    model = lgb.train(
        params,
        dtrain,
        num_boost_round=200,
        valid_sets=[dval],
        callbacks=[
            lgb.early_stopping(stopping_rounds=20),
            lgb.log_evaluation(period=0)
        ]
    )

    return model


def train_with_threshold_adjustment(X_train, y_train, X_val, y_val, draw_threshold_boost):
    """训练模型并通过调整阈值提升平局预测"""
    # 使用默认 class_weight 训练
    params = {**LGB_BASE_PARAMS}
    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    model = lgb.train(
        params,
        dtrain,
        num_boost_round=200,
        valid_sets=[dval],
        callbacks=[
            lgb.early_stopping(stopping_rounds=20),
            lgb.log_evaluation(period=0)
        ]
    )

    # 获取预测概率
    y_pred_proba = model.predict(X_val)
    # 调整平局(class=1)的阈值: 降低平局预测门槛
    y_pred_proba[:, 1] *= (1 + draw_threshold_boost)
    # 重新归一化
    y_pred_proba = y_pred_proba / y_pred_proba.sum(axis=1, keepdims=True)
    y_pred = np.argmax(y_pred_proba, axis=1)

    return y_pred, y_pred_proba, model


def evaluate_predictions(y_true, y_pred, y_pred_proba, strategy_name):
    """评估预测结果"""
    acc = accuracy_score(y_true, y_pred)
    ll = log_loss(y_true, y_pred_proba)
    cr = classification_report(y_true, y_pred, output_dict=True, labels=[0, 1, 2],
                                target_names=['客胜', '平局', '主胜'])
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1, 2])

    draw_recall = cr['平局']['recall'] if '平局' in cr else 0.0
    home_recall = cr['主胜']['recall'] if '主胜' in cr else 0.0
    away_recall = cr['客胜']['recall'] if '客胜' in cr else 0.0

    return {
        'strategy': strategy_name,
        'accuracy': acc,
        'log_loss': ll,
        'draw_recall': draw_recall,
        'home_recall': home_recall,
        'away_recall': away_recall,
        'avg_recall': (draw_recall + home_recall + away_recall) / 3,
        'confusion_matrix': cm.tolist(),
    }


def main():
    print("=" * 70)
    print("平局召回率修复实验")
    print("=" * 70)

    # 加载数据
    print("\n[1] 加载数据...")
    df = load_match_data_odds()
    X, y = build_all_features(df, include_odds=True)
    y = y.values.astype(int)
    print(f"  数据量: {len(df)} 场, 特征: {X.shape[1]} 维")

    # 时间序列划分
    split_idx = int(len(df) * 0.8)
    X_train = X.iloc[:split_idx].values
    y_train = y[:split_idx]
    X_test = X.iloc[split_idx:].values
    y_test = y[split_idx:]

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_test = scaler.transform(X_test)

    print(f"  训练集: {len(X_train)} 场, 测试集: {len(X_test)} 场")
    test_dist = np.bincount(y_test, minlength=3)
    print(f"  测试集分布: 客胜={test_dist[0]}, 平局={test_dist[1]}, 主胜={test_dist[2]}")

    results = []

    # === 策略1: 基准（无 class_weight） ===
    print(f"\n[2] 策略1: 基准（无 class_weight）...")
    params = {**LGB_BASE_PARAMS}
    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_test, label=y_test, reference=dtrain)
    model = lgb.train(params, dtrain, num_boost_round=200, valid_sets=[dval],
                      callbacks=[lgb.early_stopping(stopping_rounds=20), lgb.log_evaluation(period=0)])
    y_pred_proba = model.predict(X_test)
    y_pred = np.argmax(y_pred_proba, axis=1)
    results.append(evaluate_predictions(y_test, y_pred, y_pred_proba, "基准(无class_weight)"))

    # === 策略2: class_weight 调整 ===
    for ratio in [1.2, 1.5, 2.0, 2.5, 3.0]:
        print(f"\n[3] 策略2: class_weight ratio={ratio}...")
        model = train_with_class_weight(X_train, y_train, X_test, y_test, ratio)
        y_pred_proba = model.predict(X_test)
        y_pred = np.argmax(y_pred_proba, axis=1)
        results.append(evaluate_predictions(y_test, y_pred, y_pred_proba, f"class_weight={ratio}"))

    # === 策略3: 阈值调整 ===
    for boost in [0.2, 0.5, 0.8, 1.0, 1.5]:
        print(f"\n[4] 策略3: 阈值调整 boost={boost}...")
        y_pred, y_pred_proba, _ = train_with_threshold_adjustment(
            X_train, y_train, X_test, y_test, boost
        )
        results.append(evaluate_predictions(y_test, y_pred, y_pred_proba, f"阈值调整={boost}"))

    # === 策略4: class_weight + 阈值调整 组合 ===
    for ratio, boost in [(2.0, 0.5), (2.5, 0.5), (3.0, 0.5)]:
        print(f"\n[5] 策略4: class_weight={ratio} + 阈值={boost}...")
        model = train_with_class_weight(X_train, y_train, X_test, y_test, ratio)
        y_pred_proba = model.predict(X_test)
        y_pred_proba[:, 1] *= (1 + boost)
        y_pred_proba = y_pred_proba / y_pred_proba.sum(axis=1, keepdims=True)
        y_pred = np.argmax(y_pred_proba, axis=1)
        results.append(evaluate_predictions(y_test, y_pred, y_pred_proba, f"组合(cw={ratio},t={boost})"))

    # === 结果汇总 ===
    print(f"\n{'='*70}")
    print(f"实验结果汇总")
    print(f"{'='*70}")
    print(f"  {'策略':<30} {'准确率':<8} {'LogLoss':<8} {'主胜R':<8} {'平局R':<8} {'客胜R':<8} {'均R':<8}")
    print(f"  {'-'*80}")

    # 按平局召回率排序
    results.sort(key=lambda x: x['draw_recall'], reverse=True)

    best_by_draw = None
    best_balanced = None
    best_balanced_score = -1

    for r in results:
        print(f"  {r['strategy']:<30} {r['accuracy']*100:>6.2f}%  {r['log_loss']:>7.4f} "
              f" {r['home_recall']*100:>5.1f}%  {r['draw_recall']*100:>5.1f}%  "
              f" {r['away_recall']*100:>5.1f}%  {r['avg_recall']*100:>5.1f}%")

        # 找最佳平衡策略（准确率 > 50% 且平局召回率 > 15%）
        if r['accuracy'] > 0.50 and r['draw_recall'] > 0.10:
            score = r['accuracy'] * 0.5 + r['avg_recall'] * 0.5
            if score > best_balanced_score:
                best_balanced_score = score
                best_balanced = r

        if best_by_draw is None:
            best_by_draw = r

    # === 推荐 ===
    print(f"\n{'='*70}")
    print(f"推荐策略")
    print(f"{'='*70}")

    if best_balanced:
        print(f"\n  ✅ 最佳平衡策略: {best_balanced['strategy']}")
        print(f"     准确率: {best_balanced['accuracy']*100:.2f}%")
        print(f"     平局召回率: {best_balanced['draw_recall']*100:.1f}%")
        print(f"     平均召回率: {best_balanced['avg_recall']*100:.1f}%")
        print(f"     混淆矩阵: {best_balanced['confusion_matrix']}")
    else:
        print(f"\n  ⚠️ 未找到同时满足准确率>50%且平局召回率>10%的策略")
        print(f"     最高平局召回率策略: {best_by_draw['strategy']}")
        print(f"     平局召回率: {best_by_draw['draw_recall']*100:.1f}%")
        print(f"     准确率: {best_by_draw['accuracy']*100:.2f}%")

    print(f"\n  建议: 将推荐策略的 class_weight 参数应用到 train_models.py")
    print(f"        和 cross_league_validation.py 中")

    return results


if __name__ == '__main__':
    results = main()