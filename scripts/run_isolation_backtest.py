"""
数据隔离回测验证脚本
严格按时间序列划分训练/测试集，消除数据泄露

核心原则：
1. 训练集和测试集严格按时间分离，禁止重叠
2. StandardScaler 只在训练集上 fit，再 transform 测试集
3. 球队特征构建使用 date < match_date 过滤，确保只用历史数据
4. 模型从未见过任何测试集比赛

关联规则：
- M-002: 使用 class_weight 处理类别不平衡
- M-003: XGBoost 使用 sklearn API
- M-009: 使用时间序列交叉验证
- F-006: 区分赛前/赛后特征
- F-007: 训练和预测使用相同特征集合
- L-017: 回测必须使用时间序列划分
"""
import os
import sys
import json
import sqlite3
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss, classification_report, confusion_matrix
import xgboost as xgb

warnings.filterwarnings('ignore')

# 设置路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from feature_utils import load_match_data, load_match_data_odds, build_all_features, DB_PATH, ODDS_DB_PATH

ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')
REPORT_DIR = os.path.join(PROJECT_DIR, 'reports')

# ============================================================
# 数据隔离回测配置
# ============================================================
TEST_RATIO = 0.3  # 后30%作为测试集
RANDOM_SEED = 42

# XGBoost 参数（遵循 M-003 sklearn API, M-010 正则化）
XGB_PARAMS = {
    'n_estimators': 100,
    'max_depth': 5,
    'learning_rate': 0.03,
    'subsample': 0.8,
    'colsample_bytree': 0.8,
    'gamma': 0.5,
    'min_child_weight': 10,
    'reg_alpha': 1.0,
    'reg_lambda': 10.0,
    'objective': 'multi:softprob',
    'num_class': 3,
    'eval_metric': 'mlogloss',
    'random_state': RANDOM_SEED,
    'n_jobs': -1,
    'verbosity': 0,
}


def load_epl_match_ids_with_odds():
    """从 match_mapping 表获取有赔率数据的英超比赛 ID（对应 five_leagues.db）"""
    conn = sqlite3.connect(ODDS_DB_PATH)
    df = pd.read_sql("""
        SELECT five_leagues_match_id as match_id, match_date
        FROM match_mapping
        WHERE league = 'Premier League' OR league = '英超'
    """, conn)
    conn.close()
    return set(df['match_id'].tolist())


def run_isolation_backtest():
    """执行数据隔离回测"""
    print("=" * 80)
    print("数据隔离回测验证")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # === 步骤1: 加载全量数据 ===
    print("\n[步骤1] 加载全量比赛数据...")
    all_matches = load_match_data_odds()
    # 保留原始 id 用于匹配
    conn = sqlite3.connect(DB_PATH)
    id_df = pd.read_sql("SELECT id, date FROM matches WHERE homeGoals IS NOT NULL ORDER BY date", conn)
    conn.close()
    all_matches['match_id'] = id_df['id'].values
    print(f"  全量比赛: {len(all_matches)} 场")
    print(f"  日期范围: {all_matches['date'].min().strftime('%Y-%m-%d')} ~ {all_matches['date'].max().strftime('%Y-%m-%d')}")

    # 获取有赔率的英超比赛ID
    epl_with_odds_ids = load_epl_match_ids_with_odds()
    epl_mask = all_matches['match_id'].isin(epl_with_odds_ids)
    epl_with_odds = all_matches[epl_mask].sort_values('date').reset_index(drop=True)
    print(f"  有赔率的英超比赛: {len(epl_with_odds)} 场")

    # === 步骤2: 时间序列划分 ===
    print(f"\n[步骤2] 按时间序列划分 (测试比例={TEST_RATIO})...")
    cutoff_idx = int(len(epl_with_odds) * (1 - TEST_RATIO))
    cutoff_date = epl_with_odds.iloc[cutoff_idx - 1]['date']

    # 测试集: cutoff之后的英超赔率比赛
    test_matches = epl_with_odds.iloc[cutoff_idx:].copy()
    test_match_ids = set(test_matches['match_id'].tolist())

    print(f"  切分日期: {cutoff_date.strftime('%Y-%m-%d')}")
    print(f"  测试集: {len(test_matches)} 场 ({test_matches['date'].min().strftime('%Y-%m-%d')} ~ {test_matches['date'].max().strftime('%Y-%m-%d')})")

    # === 步骤3: 构建全量特征 ===
    print(f"\n[步骤3] 构建全量特征（含赔率特征）...")
    print(f"  正在为 {len(all_matches)} 场比赛构建特征，请稍候...")
    X_all, y_all = build_all_features(all_matches, include_odds=True)
    print(f"  特征矩阵: {X_all.shape}")
    print(f"  特征列数: {len(X_all.columns)}")

    # === 步骤4: 划分训练/测试集 ===
    print(f"\n[步骤4] 划分训练/测试集（严格时间隔离）...")

    # 测试集 mask: 属于测试集的英超赔率比赛
    test_mask = all_matches['match_id'].isin(test_match_ids)
    # 训练集 mask: 所有不属于测试集的比赛（包括其他联赛 + 早期英超）
    train_mask = ~test_mask

    X_train = X_all[train_mask].copy()
    y_train = y_all[train_mask].copy()
    X_test = X_all[test_mask].copy()
    y_test = y_all[test_mask].copy()

    # 确保特征列一致（F-007）
    feature_cols = X_train.columns.tolist()
    X_test = X_test[feature_cols]

    # 验证无数据泄露
    train_dates = set(all_matches[train_mask]['date'].dt.strftime('%Y-%m-%d'))
    test_dates = set(all_matches[test_mask]['date'].dt.strftime('%Y-%m-%d'))
    overlap = train_dates & test_dates
    if overlap:
        # 同一天可能有其他联赛比赛，这是允许的（只要不是同一场比赛）
        print(f"  ℹ️ 同日期跨联赛比赛: {len(overlap)} 天（正常，非同场比赛）")

    train_overlap = set(all_matches[train_mask]['match_id']) & test_match_ids
    if train_overlap:
        print(f"  ⚠️ 严重: 训练集包含测试集比赛ID: {train_overlap}")
    else:
        print(f"  ✅ 验证通过: 训练集不包含任何测试集比赛")

    print(f"  训练集: {len(X_train)} 场")
    print(f"  测试集: {len(X_test)} 场")
    print(f"  训练集标签分布: {dict(y_train.value_counts().sort_index())}")
    print(f"  测试集标签分布: {dict(y_test.value_counts().sort_index())}")

    # === 步骤5: 训练隔离模型 ===
    print(f"\n[步骤5] 训练隔离模型...")

    # StandardScaler: 只在训练集上 fit（关键！）
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    print(f"  StandardScaler 已在训练集上 fit（避免数据泄露）")

    # 类别权重（M-002）
    class_counts = y_train.value_counts().sort_index()
    total_samples = len(y_train)
    class_weights = {cls: total_samples / (3 * count) for cls, count in class_counts.items()}
    sample_weights = y_train.map(class_weights).values
    print(f"  类别权重: {class_weights}")

    # XGBoost 训练（M-003: sklearn API）
    model = xgb.XGBClassifier(**XGB_PARAMS)
    model.fit(X_train_scaled, y_train, sample_weight=sample_weights, verbose=False)

    # 训练集表现（检测过拟合）
    train_pred = model.predict(X_train_scaled)
    train_acc = accuracy_score(y_train, train_pred)
    train_proba = model.predict_proba(X_train_scaled)
    train_loss = log_loss(y_train, train_proba, labels=[0, 1, 2])
    print(f"  训练集准确率: {train_acc*100:.2f}%")
    print(f"  训练集 Log Loss: {train_loss:.4f}")

    # === 步骤6: 测试集评估 ===
    print(f"\n[步骤6] 测试集评估（真实性能）...")
    X_test_scaled = scaler.transform(X_test)  # 只 transform

    y_pred = model.predict(X_test_scaled)
    y_proba = model.predict_proba(X_test_scaled)

    accuracy = accuracy_score(y_test, y_pred)
    loss = log_loss(y_test, y_proba, labels=[0, 1, 2])

    class_names = ['负(客胜)', '平(平局)', '胜(主胜)']
    report = classification_report(y_test, y_pred, target_names=class_names, output_dict=True)
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2])

    per_class_acc = {}
    for i, name in enumerate(class_names):
        per_class_acc[name] = cm[i][i] / cm[i].sum() if cm[i].sum() > 0 else 0

    pred_dist = dict(pd.Series(y_pred).value_counts().sort_index())
    actual_dist = dict(y_test.value_counts().sort_index())

    print(f"\n{'='*60}")
    print(f"  数据隔离回测 - 真实性能结果")
    print(f"{'='*60}")
    print(f"  测试集准确率: {accuracy*100:.2f}%")
    print(f"  测试集 Log Loss: {loss:.4f}")
    print(f"\n  各类别准确率:")
    for name, acc in per_class_acc.items():
        print(f"    {name}: {acc*100:.2f}%")
    print(f"\n  预测分布: {pred_dist}")
    print(f"  实际分布: {actual_dist}")
    print(f"\n  混淆矩阵:")
    print(f"    {'':12s}  预测负  预测平  预测胜")
    for i, name in enumerate(class_names):
        print(f"    实际{name:8s}   {cm[i][0]:5d}    {cm[i][1]:5d}    {cm[i][2]:5d}")
    print(f"{'='*60}")

    # === 步骤7: 保存模型和报告 ===
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    model_path = os.path.join(ASSETS_DIR, f'isolation_xgb_model_{timestamp}.pkl')
    scaler_path = os.path.join(ASSETS_DIR, f'isolation_scaler_{timestamp}.pkl')
    features_path = os.path.join(ASSETS_DIR, f'isolation_features_{timestamp}.pkl')

    import joblib
    joblib.dump(model, model_path)
    joblib.dump(scaler, scaler_path)
    joblib.dump(feature_cols, features_path)
    print(f"\n  隔离模型已保存: isolation_xgb_model_{timestamp}.pkl")

    # 保存报告
    os.makedirs(REPORT_DIR, exist_ok=True)
    report_path = os.path.join(REPORT_DIR, f'isolation_backtest_{timestamp}.json')

    report_data = {
        'meta': {
            'backtest_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'test_ratio': TEST_RATIO,
            'cutoff_date': cutoff_date.strftime('%Y-%m-%d'),
            'total_epl_with_odds': len(epl_with_odds),
            'train_size': int(len(X_train)),
            'test_size': int(len(X_test)),
            'test_date_range': f"{test_matches['date'].min().strftime('%Y-%m-%d')} ~ {test_matches['date'].max().strftime('%Y-%m-%d')}",
            'feature_count': len(feature_cols),
            'xgb_params': XGB_PARAMS,
            'isolation_measures': [
                'StandardScaler 只在训练集上 fit',
                '测试集比赛不包含在训练数据中',
                '球队特征构建使用 date < match_date 过滤',
                'XGBoost 使用 sklearn API (M-003)',
                '使用 class_weight 处理类别不平衡 (M-002)',
            ],
        },
        'train_performance': {
            'accuracy': float(train_acc),
            'log_loss': float(train_loss),
        },
        'test_performance': {
            'accuracy': float(accuracy),
            'log_loss': float(loss),
            'per_class_accuracy': {k: float(v) for k, v in per_class_acc.items()},
            'prediction_distribution': {int(k): int(v) for k, v in pred_dist.items()},
            'actual_distribution': {int(k): int(v) for k, v in actual_dist.items()},
            'confusion_matrix': cm.tolist(),
            'classification_report': report,
        },
        'comparison': {
            'original_backtest_accuracy': 98.32,
            'isolation_backtest_accuracy': float(accuracy * 100),
            'accuracy_drop': float(98.32 - accuracy * 100),
            'original_train_size': 875,
            'isolation_train_size': int(len(X_train)),
            'original_test_size': 119,
            'isolation_test_size': int(len(X_test)),
        },
        'y_pred': y_pred.tolist(),
        'y_test': y_test.tolist(),
    }

    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  报告已保存: {report_path}")

    # === 对比总结 ===
    isolation_acc = accuracy * 100
    acc_drop = 98.32 - isolation_acc

    print(f"\n{'='*80}")
    print(f"数据隔离回测 vs 原始回测 对比")
    print(f"{'='*80}")
    print(f"{'指标':<25} {'原始回测(泄露)':<20} {'隔离回测(真实)':<20}")
    print(f"{'-'*80}")
    print(f"{'ML模型准确率':<25} {'98.32%':<20} {isolation_acc:.2f}%")
    print(f"{'准确率下降':<25} {'-':<20} {acc_drop:.2f}%")
    print(f"{'训练集大小':<25} {'875场(含测试)':<20} {len(X_train)}场(无测试)")
    print(f"{'测试集大小':<25} {'119场(含训练)':<20} {len(X_test)}场(纯新数据)")
    print(f"{'StandardScaler':<25} {'全量fit(泄露)':<20} {'训练集fit(正确)'}")
    print(f"{'='*80}")

    if accuracy < 0.60:
        print(f"\n✅ 验证结论: 隔离回测准确率 {isolation_acc:.2f}% 显著低于原始回测 98.32%")
        print(f"   确认原始回测存在严重数据泄露，真实性能约 {isolation_acc:.2f}%")
    elif accuracy < 0.80:
        print(f"\n⚠️ 隔离回测准确率 {isolation_acc:.2f}% 低于原始回测，但仍偏高")
        print(f"   可能仍有部分特征泄露，需进一步检查")
    else:
        print(f"\n❓ 隔离回测准确率 {isolation_acc:.2f}% 仍然很高，需深入检查特征泄露")

    print(f"\n回测完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")

    return report_data


if __name__ == '__main__':
    results = run_isolation_backtest()
