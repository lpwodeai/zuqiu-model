"""
模型验证框架运行脚本
使用 model_validation_framework.py 的 generate_validation_report() 生成完整的分类报告
包括：时间序列CV、联赛性能、时间稳定性、概率校准
"""
import os
import sys
import warnings
import lightgbm as lgb

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from feature_utils import load_match_data_odds, build_all_features
from model_validation_framework import generate_validation_report

RANDOM_SEED = 42

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


def main():
    print("=" * 60)
    print("模型验证框架 - 完整分类报告生成")
    print("=" * 60)

    # 加载数据
    print("\n1. 加载比赛数据...")
    df = load_match_data_odds()
    print(f"   共加载 {len(df)} 场比赛")

    # 构建特征
    print("\n2. 构建特征...")
    X, y = build_all_features(df, include_odds=True)
    print(f"   特征维度: {X.shape}")

    # 确保 df 有必要的列
    df = df.reset_index(drop=True)
    X = X.reset_index(drop=True)
    y = y.reset_index(drop=True)

    # 创建 LightGBM 模型
    print("\n3. 创建 LightGBM 模型...")
    model = lgb.LGBMClassifier(**LGB_PARAMS)

    # 生成验证报告
    print("\n4. 生成验证报告...")
    report = generate_validation_report(model, X, y, df, 'LightGBM_WDL', n_splits=5)

    print("\n验证完成！")
    return report


if __name__ == '__main__':
    report = main()