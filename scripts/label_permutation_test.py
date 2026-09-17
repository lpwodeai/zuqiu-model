"""
标签随机化测试 (Label Permutation Test)
验证数据泄露：如果模型在随机打乱标签后仍能取得高准确率，
说明特征中包含标签信息泄露（数据泄露）。

核心原理：
1. 训练基准模型，记录原始标签下的准确率
2. 随机打乱标签 N 次，每次重新训练模型，记录准确率
3. 如果随机标签准确率接近基准准确率 → 存在数据泄露
4. 如果随机标签准确率 ≈ 33%（三分类随机基线）→ 无泄露

判定标准（来自 analysis report）：
- 随机标签准确率 > 48% → 存在严重泄露，暂停所有优化
- 随机标签准确率 40%-48% → 可能存在主场优势泄露，需进一步排查
- 随机标签准确率 < 40% → 无泄露
"""
import os
import sys
import json
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, classification_report, confusion_matrix
import lightgbm as lgb

warnings.filterwarnings('ignore')

# 动态路径设置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from feature_utils import load_match_data_odds, build_all_features

# ============================================================
# 配置
# ============================================================
N_PERMUTATIONS = 30          # 随机化次数
RANDOM_SEED = 42
N_SPLITS = 5                 # TimeSeriesSplit 折数

# LightGBM 参数（与 train_models.py 保持一致）
LGB_PARAMS = {
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

OUTPUT_DIR = os.path.join(PROJECT_DIR, "assets")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def train_and_evaluate(X_train, y_train, X_val, y_val):
    """训练 LightGBM 模型并评估"""
    dtrain = lgb.Dataset(X_train, label=y_train)
    dval = lgb.Dataset(X_val, label=y_val, reference=dtrain)

    model = lgb.train(
        LGB_PARAMS,
        dtrain,
        num_boost_round=200,
        valid_sets=[dval],
        callbacks=[
            lgb.early_stopping(stopping_rounds=20),
            lgb.log_evaluation(period=0)
        ]
    )

    y_pred_proba = model.predict(X_val)
    y_pred = np.argmax(y_pred_proba, axis=1)
    acc = accuracy_score(y_val, y_pred)
    ll = log_loss(y_val, y_pred_proba)

    return acc, ll, model


def run_label_permutation_test():
    """执行标签随机化测试"""
    print("=" * 70)
    print("标签随机化测试 (Label Permutation Test)")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # === 步骤1: 加载数据 ===
    print("\n[步骤1] 加载比赛数据...")
    df = load_match_data_odds()
    print(f"  共加载 {len(df)} 场比赛")
    print(f"  日期范围: {df['date'].min().date()} ~ {df['date'].max().date()}")
    label_dist = df['result'].value_counts().sort_index()
    print(f"  标签分布: 客胜(0)={label_dist.get(0,0)}, 平局(1)={label_dist.get(1,0)}, 主胜(2)={label_dist.get(2,0)}")

    # === 步骤2: 构建特征 ===
    print("\n[步骤2] 构建特征...")
    X, y = build_all_features(df, include_odds=True)
    print(f"  特征维度: {X.shape}")
    print(f"  标签维度: {y.shape}")

    # === 步骤3: 时间序列划分 ===
    print(f"\n[步骤3] 时间序列划分 (后20%为测试集)...")
    split_idx = int(len(df) * 0.8)
    X_train_full = X.iloc[:split_idx].copy()
    y_train_full = y.iloc[:split_idx].copy()
    X_test = X.iloc[split_idx:].copy()
    y_test = y.iloc[split_idx:].copy()

    print(f"  训练集: {len(X_train_full)} 场")
    print(f"  测试集: {len(X_test)} 场")
    print(f"  切分日期: {df.iloc[split_idx - 1]['date'].date()}")

    # === 步骤4: 基准模型训练（原始标签）===
    print("\n[步骤4] 训练基准模型（原始标签）...")

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train_full)
    X_test_scaled = scaler.transform(X_test)

    baseline_acc, baseline_ll, baseline_model = train_and_evaluate(
        X_train_scaled, y_train_full.values,
        X_test_scaled, y_test.values
    )

    print(f"  基准准确率: {baseline_acc*100:.2f}%")
    print(f"  基准 LogLoss: {baseline_ll:.4f}")

    # 基准模型详细报告
    y_pred_base = np.argmax(baseline_model.predict(X_test_scaled), axis=1)
    cm = confusion_matrix(y_test, y_pred_base, labels=[0, 1, 2])
    print(f"\n  基准混淆矩阵:")
    print(f"              预测客胜  预测平局  预测主胜")
    for i, name in enumerate(['实际客胜', '实际平局', '实际主胜']):
        print(f"    {name}      {cm[i][0]:5d}    {cm[i][1]:5d}    {cm[i][2]:5d}")

    # === 步骤5: 标签随机化测试 ===
    print(f"\n[步骤5] 标签随机化测试 ({N_PERMUTATIONS} 次)...")
    print(f"  {'次数':<6} {'准确率':<10} {'LogLoss':<10}")
    print(f"  {'-'*30}")

    permuted_results = []
    y_train_original = y_train_full.values.copy()

    for i in range(N_PERMUTATIONS):
        # 随机打乱训练标签
        y_train_shuffled = np.random.RandomState(RANDOM_SEED + i).permutation(y_train_original)

        # 重新训练
        perm_acc, perm_ll, _ = train_and_evaluate(
            X_train_scaled, y_train_shuffled,
            X_test_scaled, y_test.values
        )

        permuted_results.append({
            'iteration': i + 1,
            'accuracy': float(perm_acc),
            'log_loss': float(perm_ll),
        })

        print(f"  {i+1:<6} {perm_acc*100:>6.2f}%   {perm_ll:>8.4f}")

    # === 步骤6: 统计分析 ===
    perm_accuracies = [r['accuracy'] for r in permuted_results]
    perm_loglosses = [r['log_loss'] for r in permuted_results]

    mean_perm_acc = np.mean(perm_accuracies)
    std_perm_acc = np.std(perm_accuracies)
    max_perm_acc = np.max(perm_accuracies)
    min_perm_acc = np.min(perm_accuracies)

    # 计算 p-value: 随机标签准确率 >= 基准准确率的概率
    p_value = np.mean(np.array(perm_accuracies) >= baseline_acc)

    # 计算 z-score: 基准准确率偏离随机分布的程度
    if std_perm_acc > 0:
        z_score = (baseline_acc - mean_perm_acc) / std_perm_acc
    else:
        z_score = float('inf')

    print(f"\n{'='*70}")
    print(f"标签随机化测试结果")
    print(f"{'='*70}")
    print(f"  基准准确率 (原始标签):    {baseline_acc*100:.2f}%")
    print(f"  随机标签准确率均值:       {mean_perm_acc*100:.2f}%")
    print(f"  随机标签准确率标准差:     {std_perm_acc*100:.2f}%")
    print(f"  随机标签准确率范围:       [{min_perm_acc*100:.2f}%, {max_perm_acc*100:.2f}%]")
    print(f"  Z-Score:                  {z_score:.2f}")
    print(f"  P-Value:                  {p_value:.4f}")
    print(f"  基准 LogLoss:             {baseline_ll:.4f}")
    print(f"  随机标签 LogLoss 均值:    {np.mean(perm_loglosses):.4f}")

    # === 步骤7: 判定 ===
    print(f"\n{'='*70}")
    print(f"判定结果")
    print(f"{'='*70}")

    # 判定逻辑
    leakage_detected = False
    leakage_level = "无泄露"

    if baseline_acc - mean_perm_acc < 0.10:
        # 基准准确率与随机标签准确率差距小于 10pp
        if mean_perm_acc > 0.48:
            leakage_detected = True
            leakage_level = "严重泄露"
        elif mean_perm_acc > 0.40:
            leakage_detected = True
            leakage_level = "中等泄露（可能主场优势特征泄露）"
        else:
            leakage_level = "轻微泄露"
    else:
        # 差距大于 10pp，说明模型学到了真实信号
        if z_score > 3.0:
            leakage_level = "无泄露（模型学到了真实信号）"
        elif z_score > 2.0:
            leakage_level = "基本无泄露"
        else:
            leakage_level = "需进一步排查"

    if leakage_detected:
        print(f"\n  ⚠️  检测到{leakage_level}！")
        print(f"  随机标签准确率 ({mean_perm_acc*100:.2f}%) 显著高于随机基线 (33%)")
        print(f"  建议: 暂停预测功能，排查特征工程中的数据泄露源")
    else:
        print(f"\n  ✅ {leakage_level}")
        if z_score > 3.0:
            print(f"  基准准确率比随机标签准确率高出 {baseline_acc - mean_perm_acc:.2%}")
            print(f"  模型确实从特征中学到了真实预测信号，可信度较高")
        print(f"  建议: 可以继续使用当前模型进行预测")

    # === 步骤8: 保存报告 ===
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report = {
        'meta': {
            'test_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'n_permutations': N_PERMUTATIONS,
            'random_seed': RANDOM_SEED,
            'n_splits': N_SPLITS,
            'total_matches': len(df),
            'train_size': int(len(X_train_full)),
            'test_size': int(len(X_test)),
            'feature_dim': int(X.shape[1]),
            'date_range': f"{df['date'].min().date()} ~ {df['date'].max().date()}",
            'split_date': str(df.iloc[split_idx - 1]['date'].date()),
            'label_distribution': {int(k): int(v) for k, v in label_dist.items()},
        },
        'baseline': {
            'accuracy': float(baseline_acc),
            'log_loss': float(baseline_ll),
            'confusion_matrix': cm.tolist(),
        },
        'permutation_test': {
            'mean_accuracy': float(mean_perm_acc),
            'std_accuracy': float(std_perm_acc),
            'max_accuracy': float(max_perm_acc),
            'min_accuracy': float(min_perm_acc),
            'mean_log_loss': float(np.mean(perm_loglosses)),
            'z_score': float(z_score),
            'p_value': float(p_value),
            'accuracy_gap': float(baseline_acc - mean_perm_acc),
            'results': permuted_results,
        },
        'verdict': {
            'leakage_detected': leakage_detected,
            'leakage_level': leakage_level,
            'recommendation': '暂停预测，排查数据泄露' if leakage_detected else '可以继续使用当前模型',
        }
    }

    report_path = os.path.join(OUTPUT_DIR, f'label_permutation_test_{timestamp}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n  报告已保存: {report_path}")

    return report


if __name__ == '__main__':
    report = run_label_permutation_test()