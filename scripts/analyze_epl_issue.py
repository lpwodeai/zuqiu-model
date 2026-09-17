"""
英超表现异常分析脚本
对比英超与其他联赛的数据分布、特征分布、结果分布
找出英超准确率低（0.3886 vs 0.49-0.52）的根本原因
"""
import os
import sys
import warnings
import numpy as np
import pandas as pd
from collections import defaultdict

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from feature_utils import load_match_data_odds, build_all_features


def analyze_epl():
    print("=" * 70)
    print("英超表现异常分析")
    print("=" * 70)

    # 加载数据
    df = load_match_data_odds()
    X, y = build_all_features(df, include_odds=True)

    df = df.reset_index(drop=True)
    X = X.reset_index(drop=True)
    y = y.reset_index(drop=True)

    # 添加标签列
    df['label'] = y.values

    # 分离英超和其他联赛
    epl_df = df[df['competition_name'].str.contains('Premier|英超', case=False, na=False)]
    other_df = df[~df['competition_name'].str.contains('Premier|英超', case=False, na=False)]

    print(f"\n数据量对比:")
    print(f"  英超: {len(epl_df)} 场")
    print(f"  其他联赛: {len(other_df)} 场")
    print(f"  英超占比: {len(epl_df)/len(df)*100:.1f}%")

    # === 1. 结果分布对比 ===
    print(f"\n--- 结果分布对比 ---")
    for name, data in [('英超', epl_df), ('其他联赛', other_df), ('全联赛', df)]:
        dist = data['label'].value_counts().sort_index()
        total = len(data)
        print(f"  {name}: 客胜={dist.get(0,0)}({dist.get(0,0)/total*100:.1f}%), "
              f"平局={dist.get(1,0)}({dist.get(1,0)/total*100:.1f}%), "
              f"主胜={dist.get(2,0)}({dist.get(2,0)/total*100:.1f}%)")

    # === 2. 进球特征对比 ===
    print(f"\n--- 进球特征对比 ---")
    for name, data in [('英超', epl_df), ('其他联赛', other_df)]:
        print(f"  {name}:")
        print(f"    场均主队进球: {data['homeGoals'].mean():.2f}")
        print(f"    场均客队进球: {data['awayGoals'].mean():.2f}")
        print(f"    场均总进球:   {(data['homeGoals'] + data['awayGoals']).mean():.2f}")
        print(f"    主胜率:       {(data['homeGoals'] > data['awayGoals']).mean()*100:.1f}%")
        print(f"    平局率:       {(data['homeGoals'] == data['awayGoals']).mean()*100:.1f}%")

    # === 3. 关键特征分布对比 ===
    key_features = [
        'home_weighted_avg_xg', 'away_weighted_avg_xg',
        'home_avg_goals', 'away_avg_goals',
        'home_win_rate', 'away_win_rate',
        'home_recent_form_5', 'away_recent_form_5',
        'home_elo_rating', 'away_elo_rating',
    ]

    # 只取实际存在的特征
    existing_features = [f for f in key_features if f in X.columns]

    if existing_features:
        print(f"\n--- 关键特征均值对比 ---")
        print(f"  {'特征':<30} {'英超均值':<12} {'其他均值':<12} {'差异':<10}")
        print(f"  {'-'*65}")
        for feat in existing_features:
            epl_val = X.loc[epl_df.index, feat].mean()
            other_val = X.loc[other_df.index, feat].mean()
            diff = epl_val - other_val
            print(f"  {feat:<30} {epl_val:<12.4f} {other_val:<12.4f} {diff:<+10.4f}")

    # === 4. 特征方差对比 ===
    if existing_features:
        print(f"\n--- 特征标准差对比 ---")
        print(f"  {'特征':<30} {'英超Std':<12} {'其他Std':<12} {'比率':<10}")
        print(f"  {'-'*65}")
        for feat in existing_features:
            epl_std = X.loc[epl_df.index, feat].std()
            other_std = X.loc[other_df.index, feat].std()
            ratio = epl_std / other_std if other_std > 0 else float('inf')
            print(f"  {feat:<30} {epl_std:<12.4f} {other_std:<12.4f} {ratio:<10.2f}")

    # === 5. 时间序列分布 ===
    print(f"\n--- 时间分布 ---")
    for name, data in [('英超', epl_df), ('其他联赛', other_df)]:
        dates = pd.to_datetime(data['date'])
        print(f"  {name}: {dates.min().date()} ~ {dates.max().date()}, "
              f"共 {len(dates)} 场")

    # === 6. 预测偏差分析 ===
    print(f"\n--- 预测偏差分析（为什么英超更差）---")
    print(f"  可能原因:")
    print(f"  1. 英超竞争更均衡（主胜率低、平局率高）→ 更难预测")
    print(f"  2. 英超特征分布与其他联赛不同 → 模型泛化差")
    print(f"  3. 英超数据量不足 → 模型学习不充分")
    print(f"  4. 英超风格差异大（身体对抗强、节奏快）→ 特征不适用")

    epl_home_win_rate = (epl_df['homeGoals'] > epl_df['awayGoals']).mean()
    other_home_win_rate = (other_df['homeGoals'] > other_df['awayGoals']).mean()
    epl_draw_rate = (epl_df['homeGoals'] == epl_df['awayGoals']).mean()
    other_draw_rate = (other_df['homeGoals'] == other_df['awayGoals']).mean()

    print(f"\n  量化分析:")
    print(f"    英超主胜率: {epl_home_win_rate*100:.1f}% vs 其他: {other_home_win_rate*100:.1f}%")
    print(f"    英超平局率: {epl_draw_rate*100:.1f}% vs 其他: {other_draw_rate*100:.1f}%")

    if epl_home_win_rate < other_home_win_rate - 0.02:
        print(f"    ⚠️ 英超主胜率显著低于其他联赛，竞争更均衡，更难预测")
    if epl_draw_rate > other_draw_rate + 0.02:
        print(f"    ⚠️ 英超平局率显著高于其他联赛，平局多增加预测难度")

    return {
        'epl_matches': len(epl_df),
        'other_matches': len(other_df),
        'epl_home_win_rate': epl_home_win_rate,
        'epl_draw_rate': epl_draw_rate,
        'other_home_win_rate': other_home_win_rate,
        'other_draw_rate': other_draw_rate,
    }


if __name__ == '__main__':
    analyze_epl()