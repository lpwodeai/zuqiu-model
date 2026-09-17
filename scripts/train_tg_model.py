"""
T-004 总进球预测模型训练
========================

基于 total_goals_history 赔率数据，训练总进球数预测模型。

模型架构:
    1. 7类分类模型: 预测总进球数 (0球/1球/2球/3球/4球/5球/6+球)
    2. 大/小球二分类模型: 预测总进球 >2.5 或 ≤2.5

训练策略:
    - 5折 TimeSeriesSplit CV（按时间排序）
    - LightGBM + XGBoost 双模型对比
    - 按联赛统计 CV 准确率

评估指标:
    - 7类分类: Accuracy, Macro-F1, Weighted-F1, 混淆矩阵
    - 大/小球: Accuracy, Precision, Recall, F1
    - 按联赛分组准确率

运行: python scripts/train_tg_model.py
"""

import sys
import os
import logging
import json
import time
from datetime import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from tg_features import build_tg_features, GOAL_COLS
from sofascore_features import build_sofascore_features_for_tg
from match_level_features import build_match_features_for_tg
from match_level_lag_features import build_match_lag_features_for_tg
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    confusion_matrix, classification_report, log_loss
)
from sklearn.preprocessing import LabelEncoder
import warnings
warnings.filterwarnings('ignore')

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("[WARNING] XGBoost not available")

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False
    print("[WARNING] LightGBM not available")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('TrainTG')

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


def prepare_data(features: pd.DataFrame):
    """
    准备训练数据: 分离特征和目标变量。
    
    返回:
        X: 特征矩阵 (仅 tg_ 前缀列)
        y_multi: 7类分类目标
        y_binary: 大/小球二分类目标
        meta: metadata (league, date, actual_total_goals)
    """
    # 特征列
    feature_cols = [c for c in features.columns if c.startswith('tg_')]
    X = features[feature_cols].copy()
    
    # 填充缺失值
    if X.isnull().any().any():
        print(f"[DATA] 填充缺失值: {X.isnull().sum().sum()} 个")
        X = X.fillna(X.median())
    
    # 目标变量
    y_raw = features['actual_total_goals'].copy()
    
    # 7类分类目标
    y_multi = y_raw.apply(lambda x: min(int(x), 6))  # 6+球 → 6
    y_multi = y_multi.astype(int)
    
    # 大/小球二分类目标
    y_binary = (y_raw > 2).astype(int)
    
    # 元数据
    meta = features[['league', 'date', 'home_team', 'away_team']].copy()
    meta['actual_total_goals'] = y_raw
    
    # 过滤无标签样本
    valid_mask = y_raw.notna()
    X = X[valid_mask]
    y_multi = y_multi[valid_mask]
    y_binary = y_binary[valid_mask]
    meta = meta[valid_mask]
    
    print(f"\n[DATA] 准备数据完成:")
    print(f"    特征维度: {X.shape[1]}")
    print(f"    有效样本: {len(X)}")
    print(f"    7类分类分布: {dict(zip(*np.unique(y_multi, return_counts=True)))}")
    print(f"    大/小球分布: 大球={y_binary.sum()}, 小球={len(y_binary)-y_binary.sum()}")
    
    return X, y_multi, y_binary, meta


def train_lgb_multi(X_train, y_train, X_val, y_val, num_classes=7):
    """训练 LightGBM 多分类模型"""
    if not LGB_AVAILABLE:
        return None, None
    
    model = lgb.LGBMClassifier(
        objective='multiclass',
        num_class=num_classes,
        max_depth=6,
        learning_rate=0.05,
        n_estimators=200,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=0.1,
        random_state=42,
        verbose=-1,
        force_col_wise=True,
    )
    
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        eval_metric='multi_logloss',
        callbacks=[lgb.early_stopping(30, verbose=False), lgb.log_evaluation(0)]
    )
    
    y_pred = model.predict(X_val)
    acc = accuracy_score(y_val, y_pred)
    
    return model, acc


def train_xgb_multi(X_train, y_train, X_val, y_val, num_classes=7):
    """训练 XGBoost 多分类模型"""
    if not XGB_AVAILABLE:
        return None, None
    
    model = xgb.XGBClassifier(
        objective='multi:softmax',
        num_class=num_classes,
        max_depth=5,
        learning_rate=0.05,
        n_estimators=200,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        verbosity=0,
        use_label_encoder=False,
    )
    
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False
    )
    
    y_pred = model.predict(X_val)
    acc = accuracy_score(y_val, y_pred)
    
    return model, acc


def train_lgb_binary(X_train, y_train, X_val, y_val):
    """训练 LightGBM 二分类模型（大/小球）"""
    if not LGB_AVAILABLE:
        return None, None
    
    model = lgb.LGBMClassifier(
        objective='binary',
        max_depth=5,
        learning_rate=0.05,
        n_estimators=150,
        num_leaves=31,
        min_child_samples=20,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=0.1,
        random_state=42,
        verbose=-1,
        force_col_wise=True,
    )
    
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        eval_metric='binary_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )
    
    y_pred = model.predict(X_val)
    acc = accuracy_score(y_val, y_pred)
    f1 = f1_score(y_val, y_pred)
    
    return model, {'accuracy': acc, 'f1': f1}


def train_xgb_binary(X_train, y_train, X_val, y_val):
    """训练 XGBoost 二分类模型（大/小球）"""
    if not XGB_AVAILABLE:
        return None, None
    
    model = xgb.XGBClassifier(
        objective='binary:logistic',
        max_depth=5,
        learning_rate=0.05,
        n_estimators=150,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=0.1,
        reg_lambda=1.0,
        random_state=42,
        verbosity=0,
        use_label_encoder=False,
    )
    
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False
    )
    
    y_pred = model.predict(X_val)
    acc = accuracy_score(y_val, y_pred)
    f1 = f1_score(y_val, y_pred)
    
    return model, {'accuracy': acc, 'f1': f1}


def cross_validate_multi(X, y, meta, n_splits=5):
    """
    7类分类 5折 TimeSeriesSplit CV。
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    # 按日期排序
    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)
    meta_sorted = meta.iloc[sort_idx].reset_index(drop=True)
    
    results = {
        'lgb': {'accuracy': [], 'f1_macro': [], 'f1_weighted': []},
        'xgb': {'accuracy': [], 'f1_macro': [], 'f1_weighted': []},
        'by_league': [],
    }
    
    all_y_true = []
    all_y_pred_lgb = []
    all_y_pred_xgb = []
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_sorted)):
        X_train, X_val = X_sorted.iloc[train_idx], X_sorted.iloc[val_idx]
        y_train, y_val = y_sorted.iloc[train_idx], y_sorted.iloc[val_idx]
        meta_val = meta_sorted.iloc[val_idx]
        
        print(f"\n  Fold {fold+1}/{n_splits}: train={len(X_train)}, val={len(X_val)}")
        print(f"    训练集日期: {meta_sorted.iloc[train_idx]['date'].min()} ~ {meta_sorted.iloc[train_idx]['date'].max()}")
        print(f"    验证集日期: {meta_val['date'].min()} ~ {meta_val['date'].max()}")
        
        # LGB
        lgb_model, lgb_acc = train_lgb_multi(X_train, y_train, X_val, y_val)
        if lgb_model is not None:
            lgb_pred = lgb_model.predict(X_val)
            results['lgb']['accuracy'].append(accuracy_score(y_val, lgb_pred))
            results['lgb']['f1_macro'].append(f1_score(y_val, lgb_pred, average='macro'))
            results['lgb']['f1_weighted'].append(f1_score(y_val, lgb_pred, average='weighted'))
            all_y_pred_lgb.extend(lgb_pred)
            print(f"    LGB: acc={results['lgb']['accuracy'][-1]:.4f}, f1_macro={results['lgb']['f1_macro'][-1]:.4f}")
        
        # XGB
        xgb_model, xgb_acc = train_xgb_multi(X_train, y_train, X_val, y_val)
        if xgb_model is not None:
            xgb_pred = xgb_model.predict(X_val)
            results['xgb']['accuracy'].append(accuracy_score(y_val, xgb_pred))
            results['xgb']['f1_macro'].append(f1_score(y_val, xgb_pred, average='macro'))
            results['xgb']['f1_weighted'].append(f1_score(y_val, xgb_pred, average='weighted'))
            all_y_pred_xgb.extend(xgb_pred)
            print(f"    XGB: acc={results['xgb']['accuracy'][-1]:.4f}, f1_macro={results['xgb']['f1_macro'][-1]:.4f}")
        
        all_y_true.extend(y_val)
        
        # 按联赛统计
        for league in meta_val['league'].unique():
            league_mask = meta_val['league'] == league
            if league_mask.sum() >= 5:
                league_acc_lgb = accuracy_score(
                    y_val[league_mask], 
                    lgb_model.predict(X_val[league_mask])
                ) if lgb_model is not None else None
                league_acc_xgb = accuracy_score(
                    y_val[league_mask],
                    xgb_model.predict(X_val[league_mask])
                ) if xgb_model is not None else None
                results['by_league'].append({
                    'fold': fold + 1,
                    'league': league,
                    'n': int(league_mask.sum()),
                    'lgb_acc': league_acc_lgb,
                    'xgb_acc': league_acc_xgb,
                })
    
    # 汇总
    print(f"\n{'='*60}")
    print(f"7类分类 CV 汇总 (n_splits={n_splits})")
    print(f"{'='*60}")
    
    for model_name in ['lgb', 'xgb']:
        if results[model_name]['accuracy']:
            accs = results[model_name]['accuracy']
            f1s = results[model_name]['f1_macro']
            print(f"  {model_name.upper()}:")
            print(f"    Accuracy:   {np.mean(accs):.4f} ± {np.std(accs):.4f}")
            print(f"    F1 Macro:   {np.mean(f1s):.4f} ± {np.std(f1s):.4f}")
    
    # 混淆矩阵（LGB）
    if all_y_pred_lgb:
        cm = confusion_matrix(all_y_true, all_y_pred_lgb)
        print(f"\n  LGB 混淆矩阵 (汇总):")
        labels = ['0球', '1球', '2球', '3球', '4球', '5球', '6+球']
        header = '        ' + '  '.join([f'{l:>5s}' for l in labels])
        print(header)
        for i, row in enumerate(cm):
            print(f"  {labels[i]:>5s}  " + '  '.join([f'{v:5d}' for v in row]))
    
    return results


def cross_validate_binary(X, y, meta, n_splits=5):
    """
    大/小球二分类 5折 TimeSeriesSplit CV。
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)
    meta_sorted = meta.iloc[sort_idx].reset_index(drop=True)
    
    results = {
        'lgb': {'accuracy': [], 'precision': [], 'recall': [], 'f1': []},
        'xgb': {'accuracy': [], 'precision': [], 'recall': [], 'f1': []},
    }
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_sorted)):
        X_train, X_val = X_sorted.iloc[train_idx], X_sorted.iloc[val_idx]
        y_train, y_val = y_sorted.iloc[train_idx], y_sorted.iloc[val_idx]
        
        # LGB
        lgb_model, lgb_metrics = train_lgb_binary(X_train, y_train, X_val, y_val)
        if lgb_model is not None:
            lgb_pred = lgb_model.predict(X_val)
            results['lgb']['accuracy'].append(accuracy_score(y_val, lgb_pred))
            results['lgb']['precision'].append(precision_score(y_val, lgb_pred))
            results['lgb']['recall'].append(recall_score(y_val, lgb_pred))
            results['lgb']['f1'].append(f1_score(y_val, lgb_pred))
        
        # XGB
        xgb_model, xgb_metrics = train_xgb_binary(X_train, y_train, X_val, y_val)
        if xgb_model is not None:
            xgb_pred = xgb_model.predict(X_val)
            results['xgb']['accuracy'].append(accuracy_score(y_val, xgb_pred))
            results['xgb']['precision'].append(precision_score(y_val, xgb_pred))
            results['xgb']['recall'].append(recall_score(y_val, xgb_pred))
            results['xgb']['f1'].append(f1_score(y_val, xgb_pred))
    
    # 汇总
    print(f"\n{'='*60}")
    print(f"大/小球二分类 CV 汇总 (n_splits={n_splits})")
    print(f"{'='*60}")
    
    for model_name in ['lgb', 'xgb']:
        if results[model_name]['accuracy']:
            print(f"  {model_name.upper()}:")
            for metric in ['accuracy', 'precision', 'recall', 'f1']:
                vals = results[model_name][metric]
                print(f"    {metric.capitalize():>10s}: {np.mean(vals):.4f} ± {np.std(vals):.4f}")
    
    return results


def generate_report(multi_results, binary_results, features, X=None, output_path=None):
    """生成 T-004 性能报告"""
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(REPORT_DIR, f'tg_model_report_{timestamp}.md')
    
    lines = []
    lines.append(f"# T-004 总进球预测模型 — 性能报告")
    lines.append(f"")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据源: odds.db → total_goals_history → match_id_mapping → matches")
    lines.append(f"> 增强特征: Elo + 历史进球均值 + WDL概率 + SofaScore球队级 + Lag比赛级(防泄露)")
    lines.append(f"")
    
    # 数据概览
    valid = features[features['actual_total_goals'].notna()]
    tg_dim = len([c for c in features.columns if c.startswith('tg_')])
    total_dim = X.shape[1] if X is not None else tg_dim
    
    lines.append(f"## 1. 数据概览")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| 总比赛数 | {len(features)} |")
    lines.append(f"| 有效样本（有实际总进球） | {len(valid)} |")
    lines.append(f"| 特征维度 | {total_dim} (TG赔率={tg_dim} + 增强={total_dim-tg_dim}) |")
    lines.append(f"| 大球比例 (>2.5) | {(valid['actual_total_goals'] > 2).sum()/len(valid)*100:.1f}% |")
    lines.append(f"| 小球比例 (≤2.5) | {(valid['actual_total_goals'] <= 2).sum()/len(valid)*100:.1f}% |")
    lines.append(f"")
    
    # 联赛分布
    lines.append(f"### 1.1 联赛分布")
    lines.append(f"")
    lines.append(f"| 联赛 | 比赛数 | 有效样本 |")
    lines.append(f"|------|--------|----------|")
    for league, count in features['league'].value_counts().items():
        valid_count = features[features['league'] == league]['actual_total_goals'].notna().sum()
        lines.append(f"| {league} | {count} | {valid_count} |")
    lines.append(f"")
    
    # 7类分类结果
    lines.append(f"## 2. 7类分类模型（总进球数预测）")
    lines.append(f"")
    lines.append(f"| 模型 | Accuracy | F1 Macro | F1 Weighted |")
    lines.append(f"|------|----------|----------|-------------|")
    for model_name in ['lgb', 'xgb']:
        if multi_results[model_name]['accuracy']:
            acc = np.mean(multi_results[model_name]['accuracy'])
            acc_std = np.std(multi_results[model_name]['accuracy'])
            f1m = np.mean(multi_results[model_name]['f1_macro'])
            f1m_std = np.std(multi_results[model_name]['f1_macro'])
            f1w = np.mean(multi_results[model_name]['f1_weighted'])
            f1w_std = np.std(multi_results[model_name]['f1_weighted'])
            lines.append(f"| {model_name.upper()} | {acc:.4f} ± {acc_std:.4f} | {f1m:.4f} ± {f1m_std:.4f} | {f1w:.4f} ± {f1w_std:.4f} |")
    lines.append(f"")
    
    # 大/小球结果
    lines.append(f"## 3. 大/小球二分类模型")
    lines.append(f"")
    lines.append(f"| 模型 | Accuracy | Precision | Recall | F1 |")
    lines.append(f"|------|----------|-----------|--------|----|")
    for model_name in ['lgb', 'xgb']:
        if binary_results[model_name]['accuracy']:
            lines.append(f"| {model_name.upper()} | " + 
                        f"{np.mean(binary_results[model_name]['accuracy']):.4f} ± {np.std(binary_results[model_name]['accuracy']):.4f} | " +
                        f"{np.mean(binary_results[model_name]['precision']):.4f} ± {np.std(binary_results[model_name]['precision']):.4f} | " +
                        f"{np.mean(binary_results[model_name]['recall']):.4f} ± {np.std(binary_results[model_name]['recall']):.4f} | " +
                        f"{np.mean(binary_results[model_name]['f1']):.4f} ± {np.std(binary_results[model_name]['f1']):.4f} |")
    lines.append(f"")
    
    # 按联赛结果
    if multi_results.get('by_league'):
        lines.append(f"## 4. 按联赛 CV 准确率")
        lines.append(f"")
        lines.append(f"| 联赛 | LGB Acc | XGB Acc |")
        lines.append(f"|------|---------|---------|")
        league_stats = {}
        for entry in multi_results['by_league']:
            league = entry['league']
            if league not in league_stats:
                league_stats[league] = {'lgb': [], 'xgb': [], 'n': entry['n']}
            if entry['lgb_acc'] is not None:
                league_stats[league]['lgb'].append(entry['lgb_acc'])
            if entry['xgb_acc'] is not None:
                league_stats[league]['xgb'].append(entry['xgb_acc'])
        
        for league, stats in sorted(league_stats.items()):
            lgb_str = f"{np.mean(stats['lgb']):.4f}" if stats['lgb'] else "N/A"
            xgb_str = f"{np.mean(stats['xgb']):.4f}" if stats['xgb'] else "N/A"
            lines.append(f"| {league} | {lgb_str} | {xgb_str} |")
    lines.append(f"")
    
    # 验收标准
    lines.append(f"## 5. 验收标准")
    lines.append(f"")
    lgb_acc = np.mean(multi_results['lgb']['accuracy']) if multi_results['lgb']['accuracy'] else 0
    lgb_binary_acc = np.mean(binary_results['lgb']['accuracy']) if binary_results['lgb']['accuracy'] else 0
    
    lines.append(f"| 指标 | 目标 | 实际 | 状态 |")
    lines.append(f"|------|------|------|------|")
    lines.append(f"| 7类分类准确率 (LGB) | ≥ 40% | {lgb_acc*100:.1f}% | {'✅' if lgb_acc >= 0.40 else '❌'} |")
    lines.append(f"| 大/小球方向准确率 (LGB) | ≥ 55% | {lgb_binary_acc*100:.1f}% | {'✅' if lgb_binary_acc >= 0.55 else '❌'} |")
    lines.append(f"")
    
    # 特征重要性
    lines.append(f"## 6. 特征列表")
    lines.append(f"")
    tg_cols = [c for c in features.columns if c.startswith('tg_')]
    lines.append(f"| # | 特征名 | 描述 |")
    lines.append(f"|---|--------|------|")
    feature_descriptions = {
        'tg_prob_0': '0球概率', 'tg_prob_1': '1球概率', 'tg_prob_2': '2球概率',
        'tg_prob_3': '3球概率', 'tg_prob_4': '4球概率', 'tg_prob_5': '5球概率',
        'tg_prob_6': '6球概率', 'tg_prob_7': '7+球概率',
        'tg_over_25_prob': '大球(>2.5)概率', 'tg_under_25_prob': '小球(≤2.5)概率',
        'tg_over_15_prob': '>1.5球概率', 'tg_over_35_prob': '>3.5球概率',
        'tg_expected_goals': '期望总进球', 'tg_most_likely_goals': '最可能进球数',
        'tg_most_likely_prob': '最可能进球概率', 'tg_entropy': '分布熵',
        'tg_top3_concentration': '前3集中度', 'tg_variance': '方差',
        'tg_median_goals': '中位数进球', 'tg_low_score_prob': '低进球(0-1)概率',
    }
    for i, col in enumerate(tg_cols):
        desc = feature_descriptions.get(col, '')
        lines.append(f"| {i+1} | `{col}` | {desc} |")
    lines.append(f"")
    
    report = '\n'.join(lines)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n[REPORT] 报告已保存: {output_path}")
    return output_path


def add_elo_features(X, meta):
    """添加 Elo 评分特征（10维）"""
    try:
        from elo_rating import build_elo_features
        
        # 构建Elo所需的DataFrame（需要 homeGoals, awayGoals 列）
        elo_input = pd.DataFrame({
            'home_team_name': meta['home_team'].values,
            'away_team_name': meta['away_team'].values,
            'date': meta['date'].values,
        })
        elo_input.index = X.index
        
        # 从matches获取比分数据来计算homeGoals/awayGoals
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
        conn = sqlite3.connect(db_path)
        scores_df = pd.read_sql("SELECT match_id, actual_score FROM matches WHERE actual_score IS NOT NULL", conn)
        conn.close()
        
        # 解析比分
        def parse_score(score_str):
            try:
                parts = str(score_str).split('-')
                return int(parts[0]), int(parts[1])
            except:
                return 0, 0
        
        score_map = {}
        for _, row in scores_df.iterrows():
            hg, ag = parse_score(row['actual_score'])
            score_map[row['match_id']] = (hg, ag)
        
        elo_input['homeGoals'] = [score_map.get(mid, (0, 0))[0] for mid in X.index]
        elo_input['awayGoals'] = [score_map.get(mid, (0, 0))[1] for mid in X.index]
        
        # 计算Elo
        elo_df = build_elo_features(elo_input)
        elo_cols = [c for c in elo_df.columns]
        
        for col in elo_cols:
            X[col] = elo_df[col].values
        
        X[elo_cols] = X[elo_cols].fillna(X[elo_cols].median())
        print(f"[ELO] 添加 Elo 特征: {len(elo_cols)} 维")
        return X, elo_cols
    except Exception as e:
        print(f"[ELO] 无法添加 Elo 特征: {e}")
        import traceback; traceback.print_exc()
    return X, []


def add_historical_goal_features(X, meta):
    """添加历史进球均值特征（6维正交特征）"""
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
        conn = sqlite3.connect(db_path)
        
        # 加载所有比赛结果，按日期排序
        all_matches = pd.read_sql("""
            SELECT match_date, home_team, away_team, actual_total_goals
            FROM matches
            WHERE actual_total_goals IS NOT NULL
            ORDER BY match_date
        """, conn)
        conn.close()
        
        all_matches['match_date_str'] = all_matches['match_date'].astype(str).str[:10]
        
        # 为每场比赛计算历史累计均值
        results = []
        for idx in X.index:
            row_meta = meta.loc[idx]
            # 使用 .values 转换为 numpy 数组避免 pandas Series 索引对齐问题
            match_date = str(row_meta['date'])[:10]
            home_team = str(row_meta['home_team'])
            away_team = str(row_meta['away_team'])
            
            # 主队历史：该日期之前的所有比赛（用字符串比较避免datetime类型问题）
            before_mask = all_matches['match_date_str'].values < match_date
            home_mask = (all_matches['home_team'].values == home_team) | (all_matches['away_team'].values == home_team)
            away_mask = (all_matches['home_team'].values == away_team) | (all_matches['away_team'].values == away_team)
            
            home_history = all_matches[before_mask & home_mask]
            away_history = all_matches[before_mask & away_mask]
            
            # 主队历史进球均值
            home_avg = home_history['actual_total_goals'].mean() if len(home_history) > 0 else 2.5
            
            # 客队历史进球均值
            away_avg = away_history['actual_total_goals'].mean() if len(away_history) > 0 else 2.5
            
            # 近期5场均值
            home_recent = home_history.tail(5)
            away_recent = away_history.tail(5)
            
            home_recent_avg = home_recent['actual_total_goals'].mean() if len(home_recent) > 0 else 2.5
            away_recent_avg = away_recent['actual_total_goals'].mean() if len(away_recent) > 0 else 2.5
            
            results.append({
                'hist_home_avg_goals': home_avg,
                'hist_away_avg_goals': away_avg,
                'hist_avg_goals_diff': home_avg - away_avg,
                'hist_home_recent_avg': home_recent_avg,
                'hist_away_recent_avg': away_recent_avg,
                'hist_combined_avg': (home_avg + away_avg) / 2,
            })
        
        hist_df = pd.DataFrame(results, index=X.index)
        hist_cols = [c for c in hist_df.columns]
        
        for col in hist_cols:
            X[col] = hist_df[col].values
        
        X[hist_cols] = X[hist_cols].fillna(2.5)
        print(f"[HIST] 添加历史进球均值特征: {len(hist_cols)} 维")
        return X, hist_cols
    except Exception as e:
        print(f"[HIST] 无法添加历史进球特征: {e}")
        import traceback; traceback.print_exc()
    return X, []


def add_wdl_features(X, features_df):
    """添加 WDL 隐含概率特征"""
    try:
        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
        conn = sqlite3.connect(db_path)
        
        # wdl_history表列名: win_a (主胜), draw, win_b (客胜)
        wdl_df = pd.read_sql("""
            SELECT h.match_id as history_match_id, m.matches_match_id,
                   h.win_a, h.draw, h.win_b, h.timestamp
            FROM wdl_history h
            INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
            WHERE h.timestamp < '2026-07-01'
            ORDER BY h.match_id, h.timestamp
        """, conn)
        conn.close()
        
        if len(wdl_df) > 0:
            wdl_latest = wdl_df.sort_values('timestamp').groupby('history_match_id').last()
            wdl_latest = wdl_latest.set_index('matches_match_id')
            
            for col in ['win_a', 'draw', 'win_b']:
                wdl_latest[col] = wdl_latest[col].fillna(0)
            row_sums = wdl_latest[['win_a', 'draw', 'win_b']].sum(axis=1)
            for col in ['win_a', 'draw', 'win_b']:
                wdl_latest[col] = wdl_latest[col] / row_sums.replace(0, 1)
            
            common_idx = X.index.intersection(wdl_latest.index)
            if len(common_idx) > 0:
                for col in ['win_a', 'draw', 'win_b']:
                    X.loc[common_idx, f'wdl_{col}'] = wdl_latest.loc[common_idx, col].values
                wdl_feat_cols = ['wdl_win_a', 'wdl_draw', 'wdl_win_b']
                X[wdl_feat_cols] = X[wdl_feat_cols].fillna(X[wdl_feat_cols].median())
                print(f"[WDL] 添加 WDL 概率特征: {len(wdl_feat_cols)} 维, 匹配 {len(common_idx)} 场")
                return X, wdl_feat_cols
    except Exception as e:
        print(f"[WDL] 无法添加 WDL 特征: {e}")
        import traceback; traceback.print_exc()
    return X, []


def add_sofascore_features(X, tg_features_df):
    """添加 SofaScore 球队级特征（23维差值特征，正交于赔率）"""
    try:
        sofa_df = build_sofascore_features_for_tg(tg_features_df)
        
        sofa_cols = [c for c in sofa_df.columns if c.startswith('sofa_')]
        common_idx = X.index.intersection(sofa_df.index)
        
        if len(common_idx) == 0:
            print("[SOFA] 无匹配的 SofaScore 特征")
            return X, []
        
        for col in sofa_cols:
            X.loc[common_idx, col] = sofa_df.loc[common_idx, col].values
        
        # 填充缺失
        X[sofa_cols] = X[sofa_cols].fillna(X[sofa_cols].median())
        
        print(f"[SOFA] 添加 SofaScore 特征: {len(sofa_cols)} 维, 匹配 {len(common_idx)}/{len(X)} 场 ({len(common_idx)/len(X)*100:.1f}%)")
        return X, sofa_cols
    except Exception as e:
        print(f"[SOFA] 无法添加 SofaScore 特征: {e}")
        import traceback; traceback.print_exc()
    return X, []


def add_match_level_features(X, tg_features_df):
    """添加比赛级 xG/射门特征（⚠️ 赛后统计，仅用于基准对比/特征重要性分析）"""
    try:
        match_df = build_match_features_for_tg(tg_features_df)
        
        match_cols = [c for c in match_df.columns if c.startswith('match_')]
        common_idx = X.index.intersection(match_df.index)
        
        if len(common_idx) == 0:
            print("[MATCH] 无匹配的比赛级特征")
            return X, []
        
        for col in match_cols:
            X.loc[common_idx, col] = match_df.loc[common_idx, col].values
        
        # 确保数值类型并填充
        for col in match_cols:
            if X[col].dtype == object:
                X[col] = pd.to_numeric(X[col], errors='coerce')
        X[match_cols] = X[match_cols].fillna(X[match_cols].median())
        
        print(f"[MATCH] 添加比赛级特征: {len(match_cols)} 维, 匹配 {len(common_idx)}/{len(X)} 场 ({len(common_idx)/len(X)*100:.1f}%)")
        print(f"[MATCH] ⚠️ 注意: 这些是赛后统计特征，在真实预测中属于数据泄露！仅用于基准对比。")
        return X, match_cols
    except Exception as e:
        print(f"[MATCH] 无法添加比赛级特征: {e}")
        import traceback; traceback.print_exc()
    return X, []


def add_match_lag_features(X, tg_features_df):
    """添加 Lag 版本比赛级特征（✅ 无数据泄露：仅使用历史数据）"""
    try:
        lag_df = build_match_lag_features_for_tg(tg_features_df)
        
        lag_cols = [c for c in lag_df.columns]
        common_idx = X.index.intersection(lag_df.index)
        
        if len(common_idx) == 0:
            print("[LAG] 无匹配的 lag 特征")
            return X, []
        
        for col in lag_cols:
            X.loc[common_idx, col] = lag_df.loc[common_idx, col].values
        
        # 确保数值类型并填充
        for col in lag_cols:
            if X[col].dtype == object:
                X[col] = pd.to_numeric(X[col], errors='coerce')
        X[lag_cols] = X[lag_cols].fillna(X[lag_cols].median())
        
        print(f"[LAG] 添加 Lag 比赛级特征: {len(lag_cols)} 维, 匹配 {len(common_idx)}/{len(X)} 场 ({len(common_idx)/len(X)*100:.1f}%)")
        print(f"[LAG] ✅ 防泄露: 仅使用球队历史数据（shift+rolling），无未来信息")
        return X, lag_cols
    except Exception as e:
        print(f"[LAG] 无法添加 lag 特征: {e}")
        import traceback; traceback.print_exc()
    return X, []


def cross_validate_3class(X, y, meta, n_splits=5):
    """
    3类分类: ≤1球 / 2-3球 / ≥4球
    """
    # 构建3类目标
    y_3class = y.copy()
    y_3class = y_3class.apply(lambda x: 0 if x <= 1 else (1 if x <= 3 else 2))
    
    tscv = TimeSeriesSplit(n_splits=n_splits)
    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y_3class.iloc[sort_idx].reset_index(drop=True)
    
    results = {'lgb': {'accuracy': [], 'f1_macro': []}, 'xgb': {'accuracy': [], 'f1_macro': []}}
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_sorted)):
        X_train, X_val = X_sorted.iloc[train_idx], X_sorted.iloc[val_idx]
        y_train, y_val = y_sorted.iloc[train_idx], y_sorted.iloc[val_idx]
        
        # LGB
        lgb_model, _ = train_lgb_multi(X_train, y_train, X_val, y_val, num_classes=3)
        if lgb_model is not None:
            lgb_pred = lgb_model.predict(X_val)
            results['lgb']['accuracy'].append(accuracy_score(y_val, lgb_pred))
            results['lgb']['f1_macro'].append(f1_score(y_val, lgb_pred, average='macro'))
        
        # XGB
        xgb_model, _ = train_xgb_multi(X_train, y_train, X_val, y_val, num_classes=3)
        if xgb_model is not None:
            xgb_pred = xgb_model.predict(X_val)
            results['xgb']['accuracy'].append(accuracy_score(y_val, xgb_pred))
            results['xgb']['f1_macro'].append(f1_score(y_val, xgb_pred, average='macro'))
    
    print(f"\n{'='*60}")
    print(f"3类分类 CV 汇总 (≤1球/2-3球/≥4球, n_splits={n_splits})")
    print(f"{'='*60}")
    for model_name in ['lgb', 'xgb']:
        if results[model_name]['accuracy']:
            print(f"  {model_name.upper()}:")
            print(f"    Accuracy: {np.mean(results[model_name]['accuracy']):.4f} ± {np.std(results[model_name]['accuracy']):.4f}")
            print(f"    F1 Macro: {np.mean(results[model_name]['f1_macro']):.4f} ± {np.std(results[model_name]['f1_macro']):.4f}")
    
    return results


def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    print("=" * 70)
    print("🏆 T-004 总进球预测模型训练")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据源: total_goals_history → match_id_mapping → matches")
    print("=" * 70)
    
    # Step 1: 构建特征
    print("\n📊 Step 1: 构建总进球赔率特征...")
    t0 = time.time()
    features = build_tg_features()
    print(f"   耗时: {time.time() - t0:.1f}s")
    
    # Step 2: 准备数据
    print("\n🔧 Step 2: 准备训练数据...")
    X, y_multi, y_binary, meta = prepare_data(features)
    
    # Step 2.5: 添加增强特征
    print("\n🔧 Step 2.5: 添加增强特征 (Elo + 历史进球均值 + WDL概率 + SofaScore + Lag比赛级)...")
    X, elo_cols = add_elo_features(X, meta)
    X, hist_cols = add_historical_goal_features(X, meta)
    X, wdl_cols = add_wdl_features(X, features)
    X, sofa_cols = add_sofascore_features(X, features)
    X, lag_cols = add_match_lag_features(X, features)  # ✅ Lag 版本：无数据泄露
    total_feature_dim = X.shape[1]
    print(f"   增强后总特征维度: {total_feature_dim} (TG基础=20 + Elo={len(elo_cols)} + 历史={len(hist_cols)} + WDL={len(wdl_cols)} + SofaScore={len(sofa_cols)} + Lag比赛级={len(lag_cols)})")
    
    # Step 3: 7类分类 CV
    print("\n🎯 Step 3: 7类分类 5折 CV...")
    t0 = time.time()
    multi_results = cross_validate_multi(X, y_multi, meta, n_splits=5)
    print(f"   耗时: {time.time() - t0:.1f}s")
    
    # Step 3.5: 3类分类 CV
    print("\n🎯 Step 3.5: 3类分类 5折 CV (≤1球/2-3球/≥4球)...")
    t0 = time.time()
    three_class_results = cross_validate_3class(X, y_multi, meta, n_splits=5)
    print(f"   耗时: {time.time() - t0:.1f}s")
    
    # Step 4: 大/小球二分类 CV
    print("\n🎯 Step 4: 大/小球二分类 5折 CV...")
    t0 = time.time()
    binary_results = cross_validate_binary(X, y_binary, meta, n_splits=5)
    print(f"   耗时: {time.time() - t0:.1f}s")
    
    # Step 5: 生成报告
    print("\n📝 Step 5: 生成性能报告...")
    report_path = generate_report(multi_results, binary_results, features, X)
    
    # Step 6: 保存结果 JSON
    results_json = {
        'timestamp': timestamp,
        'task': 'T-004',
        'data': {
            'total_matches': len(features),
            'valid_samples': len(X),
            'feature_dim': total_feature_dim,
            'tg_features': 20,
            'elo_features': len(elo_cols),
            'hist_features': len(hist_cols),
            'wdl_features': len(wdl_cols),
            'sofa_features': len(sofa_cols),
            'lag_features': len(lag_cols),
        },
        'multi_class_7': {
            'lgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} 
                    for k, v in multi_results['lgb'].items() if v},
            'xgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} 
                    for k, v in multi_results['xgb'].items() if v},
        },
        'multi_class_3': {
            'lgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} 
                    for k, v in three_class_results['lgb'].items() if v},
            'xgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} 
                    for k, v in three_class_results['xgb'].items() if v},
        },
        'binary': {
            'lgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} 
                    for k, v in binary_results['lgb'].items() if v},
            'xgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))} 
                    for k, v in binary_results['xgb'].items() if v},
        },
        'acceptance': {
            'multi_7class_accuracy_40pct': float(np.mean(multi_results['lgb']['accuracy'])) >= 0.40 if multi_results['lgb']['accuracy'] else False,
            'binary_accuracy_55pct': float(np.mean(binary_results['lgb']['accuracy'])) >= 0.55 if binary_results['lgb']['accuracy'] else False,
        }
    }
    
    json_path = os.path.join(REPORT_DIR, f'tg_model_results_{timestamp}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results_json, f, ensure_ascii=False, indent=2)
    print(f"[JSON] 结果已保存: {json_path}")
    
    print(f"\n{'='*70}")
    print(f"✅ T-004 训练完成!")
    print(f"{'='*70}")
    
    return multi_results, binary_results


if __name__ == "__main__":
    main()