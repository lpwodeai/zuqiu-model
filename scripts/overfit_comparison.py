#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
过拟合修复前后对比脚本
- 训练两个 XGBoost 模型（退化参数 vs 修复后参数）
- 生成预测分布对比图、过拟合 gap 对比图、概率分布对比图
- 输出 JSON 格式指标对比
"""

import os
import sys
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, classification_report, log_loss
from sklearn.preprocessing import StandardScaler

# 中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'server'))

OUTPUT_DIR = os.path.join(PROJECT_DIR, 'assets')
LOG_DIR = os.path.join(PROJECT_DIR, 'logs')

# 三轮参数配置
CONFIGS = {
    'degraded': {
        'label': '退化时 (08-21a)',
        'params': {
            'objective': 'multi:softprob', 'num_class': 3, 'eval_metric': 'mlogloss',
            'max_depth': 4, 'learning_rate': 0.07,
            'subsample': 0.785, 'colsample_bytree': 0.827,
            'gamma': 3.61, 'min_child_weight': 3,
            'reg_alpha': 0.0101, 'reg_lambda': 0.0282,
            'scale_pos_weight': 2.108, 'seed': 42, 'nthread': -1,
        },
        'n_rounds': 178,
        'color': '#e74c3c',
    },
    'fix_r1': {
        'label': '第一轮修复',
        'params': {
            'objective': 'multi:softprob', 'num_class': 3, 'eval_metric': 'mlogloss',
            'max_depth': 4, 'learning_rate': 0.07,
            'subsample': 0.785, 'colsample_bytree': 0.827,
            'gamma': 4.5, 'min_child_weight': 10,
            'reg_alpha': 0.1, 'reg_lambda': 5.0,
            'scale_pos_weight': 2.108, 'seed': 42, 'nthread': -1,
        },
        'n_rounds': 178,
        'color': '#f39c12',
    },
    'fix_r2': {
        'label': '第二轮修复 (最终)',
        'params': {
            'objective': 'multi:softprob', 'num_class': 3, 'eval_metric': 'mlogloss',
            'max_depth': 4, 'learning_rate': 0.07,
            'subsample': 0.785, 'colsample_bytree': 0.827,
            'gamma': 5.0, 'min_child_weight': 13,
            'reg_alpha': 0.1, 'reg_lambda': 8.0,
            'scale_pos_weight': 2.108, 'seed': 42, 'nthread': -1,
        },
        'n_rounds': 130,
        'color': '#27ae60',
    },
    'baseline_v28': {
        'label': '旧模型 v2.8',
        'params': {
            'objective': 'multi:softprob', 'num_class': 3, 'eval_metric': 'mlogloss',
            'max_depth': 9, 'learning_rate': 0.0492,
            'subsample': 0.665, 'colsample_bytree': 0.501,
            'gamma': 4.956, 'min_child_weight': 13,
            'reg_alpha': 0.064, 'reg_lambda': 8.003,
            'scale_pos_weight': 1.97, 'seed': 42, 'nthread': -1,
        },
        'n_rounds': 181,
        'color': '#3498db',
    },
}


def load_data():
    """加载数据并构建特征"""
    from feature_utils import load_match_data_odds, build_all_features

    print("加载数据...")
    df = load_match_data_odds()
    print(f"  比赛数: {len(df)}")

    print("构建特征 (215维)...")
    X, y = build_all_features(
        df, include_draw_enhanced=True
    )
    print(f"  特征维度: {X.shape[1]}")
    return X, y, list(X.columns)


def train_model(params, n_rounds, X_train, y_train, X_val):
    """训练 XGBoost 模型"""
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dval = xgb.DMatrix(X_val)
    model = xgb.train(params, dtrain, num_boost_round=n_rounds)
    train_probs = model.predict(dtrain)
    val_probs = model.predict(dval)
    return model, train_probs, val_probs


def apply_draw_threshold(probs, factor=1.5):
    """平局阈值调整"""
    modified = probs.copy()
    modified[:, 1] = modified[:, 1] ** (1.0 / factor)
    return modified.argmax(axis=1)


def compute_ece(y_true, y_proba, n_bins=10):
    """Expected Calibration Error"""
    ece = 0.0
    for cls in range(y_proba.shape[1]):
        cls_probs = y_proba[:, cls]
        cls_labels = (y_true == cls).astype(int)
        bin_edges = np.linspace(0, 1, n_bins + 1)
        for b in range(n_bins):
            mask = (cls_probs >= bin_edges[b]) & (cls_probs < bin_edges[b + 1])
            if mask.sum() == 0:
                continue
            avg_conf = cls_probs[mask].mean()
            avg_acc = cls_labels[mask].mean()
            ece += mask.sum() / len(y_true) * abs(avg_conf - avg_acc)
    return ece / y_proba.shape[1]


def plot_prediction_distribution(results, save_path):
    """图1: 预测分布对比（训练集 vs 验证集）"""
    fig, axes = plt.subplots(2, 4, figsize=(24, 10))
    class_names = ['客胜', '平局', '主胜']
    class_colors = ['#3498db', '#95a5a6', '#e74c3c']

    for col, (config_key, config) in enumerate(CONFIGS.items()):
        r = results[config_key]
        ax_top = axes[0, col]
        ax_bot = axes[1, col]

        # 训练集分布
        train_dist = np.bincount(r['train_pred'], minlength=3) / len(r['train_pred'])
        val_dist = np.bincount(r['val_pred'], minlength=3) / len(r['val_pred'])
        true_dist = np.bincount(r['y_train'], minlength=3) / len(r['y_train'])
        true_val_dist = np.bincount(r['y_val'], minlength=3) / len(r['y_val'])

        x = np.arange(3)
        w = 0.25

        ax_top.bar(x - w, true_dist, w, label='真实分布', color='#2c3e50', alpha=0.7)
        ax_top.bar(x, train_dist, w, label='预测分布(训练)', color=config['color'], alpha=0.8)
        ax_top.set_title(f'{config["label"]}\n训练集 (Acc={r["train_acc"]:.4f})', fontsize=11)
        ax_top.set_xticks(x)
        ax_top.set_xticklabels(class_names)
        ax_top.set_ylabel('占比')
        ax_top.set_ylim(0, 0.75)
        ax_top.legend(fontsize=8)

        ax_bot.bar(x - w, true_val_dist, w, label='真实分布', color='#2c3e50', alpha=0.7)
        ax_bot.bar(x, val_dist, w, label='预测分布(验证)', color=config['color'], alpha=0.8)
        ax_bot.set_title(f'验证集 (Acc={r["val_acc"]:.4f}, Gap={r["gap"]:.2f}pp)', fontsize=11)
        ax_bot.set_xticks(x)
        ax_bot.set_xticklabels(class_names)
        ax_bot.set_ylabel('占比')
        ax_bot.set_ylim(0, 0.75)
        ax_bot.legend(fontsize=8)

    plt.suptitle('过拟合修复前后 — 预测分布对比 (训练集 vs 验证集)', fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  预测分布图: {save_path}")


def plot_overfit_gap(results, save_path):
    """图2: 过拟合 gap 对比柱状图"""
    fig, ax = plt.subplots(figsize=(10, 6))

    labels = [CONFIGS[k]['label'] for k in CONFIGS]
    train_accs = [results[k]['train_acc'] for k in CONFIGS]
    val_accs = [results[k]['val_acc'] for k in CONFIGS]
    gaps = [results[k]['gap'] for k in CONFIGS]
    colors = [CONFIGS[k]['color'] for k in CONFIGS]

    x = np.arange(len(labels))
    w = 0.35

    bars1 = ax.bar(x - w/2, [a*100 for a in train_accs], w, label='训练准确率', color=colors, alpha=0.7, edgecolor='black')
    bars2 = ax.bar(x + w/2, [a*100 for a in val_accs], w, label='验证准确率', color=colors, alpha=1.0, edgecolor='black', hatch='//')

    # gap 标注
    for i, (gap, ta, va) in enumerate(zip(gaps, train_accs, val_accs)):
        ax.annotate(f'Gap={gap:.1f}pp', xy=(i, max(ta, va)*100 + 1),
                    ha='center', fontsize=9, fontweight='bold',
                    color='red' if gap > 8 else 'orange' if gap > 6 else 'green')

    ax.set_ylabel('准确率 (%)', fontsize=12)
    ax.set_title('过拟合 Gap 对比 — 训练 vs 验证准确率', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.legend(fontsize=10)
    ax.set_ylim(40, 70)

    # 6% 警戒线
    ax.axhline(y=0, color='black', linewidth=0.5)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  过拟合 gap 对比图: {save_path}")


def plot_probability_distribution(results, save_path):
    """图3: 平局概率分布对比"""
    fig, axes = plt.subplots(1, 4, figsize=(24, 5))

    for i, (config_key, config) in enumerate(CONFIGS.items()):
        r = results[config_key]
        ax = axes[i]

        draw_mask = r['y_val'] == 1
        non_draw_mask = ~draw_mask

        ax.hist(r['val_probs'][non_draw_mask, 1], bins=30, alpha=0.5,
                label='非平局样本', color='#3498db', density=True)
        ax.hist(r['val_probs'][draw_mask, 1], bins=30, alpha=0.6,
                label='平局样本', color='#e74c3c', density=True)

        ax.set_title(f'{config["label"]}\nECE={r["ece"]:.4f}', fontsize=11)
        ax.set_xlabel('平局概率')
        ax.set_ylabel('密度')
        ax.legend(fontsize=8)
        ax.axvline(x=0.33, color='gray', linestyle='--', alpha=0.5, label='1/3')

    plt.suptitle('验证集 — 平局概率分布对比 (红=真实平局, 蓝=非平局)', fontsize=13, fontweight='bold')
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  概率分布对比图: {save_path}")


def plot_calibration_curve(results, save_path):
    """图4: 概率校准曲线 (Reliability Diagram)"""
    fig, ax = plt.subplots(figsize=(8, 8))

    for config_key, config in CONFIGS.items():
        r = results[config_key]
        probs = r['val_probs']

        # 对平局类别画校准曲线
        draw_probs = probs[:, 1]
        draw_labels = (r['y_val'] == 1).astype(int)

        n_bins = 10
        bin_edges = np.linspace(0, 1, n_bins + 1)
        bin_centers = []
        bin_accs = []
        for b in range(n_bins):
            mask = (draw_probs >= bin_edges[b]) & (draw_probs < bin_edges[b + 1])
            if mask.sum() < 5:
                continue
            bin_centers.append(draw_probs[mask].mean())
            bin_accs.append(draw_labels[mask].mean())

        ax.plot(bin_centers, bin_accs, 'o-', color=config['color'],
                label=f'{config["label"]} (ECE={r["ece"]:.4f})', markersize=6)

    ax.plot([0, 1], [0, 1], 'k--', alpha=0.3, label='完美校准')
    ax.set_xlabel('预测概率 (平局)', fontsize=12)
    ax.set_ylabel('实际频率 (平局)', fontsize=12)
    ax.set_title('概率校准曲线 — 平局类别', fontsize=14, fontweight='bold')
    ax.legend(fontsize=9)
    ax.set_xlim(0, 0.8)
    ax.set_ylim(0, 0.6)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  校准曲线图: {save_path}")


def main():
    print("=" * 60)
    print("过拟合修复前后对比分析")
    print("=" * 60)

    # 加载数据
    X, y, feature_names = load_data()
    y = y.values if hasattr(y, 'values') else y
    X_arr = X.values if hasattr(X, 'values') else np.array(X)

    # 时间序列分割 (与 train_models.py 一致)
    split_idx = int(len(X_arr) * 0.8)
    X_train, X_val = X_arr[:split_idx], X_arr[split_idx:]
    y_train, y_val = y[:split_idx], y[split_idx:]

    print(f"\n训练集: {len(y_train)} 场, 验证集: {len(y_val)} 场")
    print(f"训练集分布: 客胜={np.sum(y_train==0)}, 平局={np.sum(y_train==1)}, 主胜={np.sum(y_train==2)}")
    print(f"验证集分布: 客胜={np.sum(y_val==0)}, 平局={np.sum(y_val==1)}, 主胜={np.sum(y_val==2)}")

    # 标准化
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)

    results = {}

    for config_key, config in CONFIGS.items():
        print(f"\n训练 {config['label']}...")
        model, train_probs, val_probs = train_model(
            config['params'], config['n_rounds'],
            X_train_scaled, y_train, X_val_scaled
        )

        train_pred = train_probs.argmax(axis=1)
        val_pred = val_probs.argmax(axis=1)

        # 阈值调整后
        val_pred_thr = apply_draw_threshold(val_probs, 1.5)

        train_acc = accuracy_score(y_train, train_pred)
        val_acc = accuracy_score(y_val, val_pred)
        val_acc_thr = accuracy_score(y_val, val_pred_thr)
        gap = (train_acc - val_acc) * 100
        ece = compute_ece(y_val, val_probs)
        ll = log_loss(y_val, val_probs, labels=[0, 1, 2])
        cr_thr = classification_report(y_val, val_pred_thr, labels=[0,1,2],
                                       target_names=['客胜','平局','主胜'],
                                       output_dict=True, zero_division=0)

        results[config_key] = {
            'train_acc': train_acc,
            'val_acc': val_acc,
            'val_acc_thr': val_acc_thr,
            'gap': gap,
            'ece': ece,
            'logloss': ll,
            'draw_recall_thr': cr_thr['平局']['recall'],
            'draw_precision_thr': cr_thr['平局']['precision'],
            'train_pred': train_pred,
            'val_pred': val_pred,
            'val_pred_thr': val_pred_thr,
            'val_probs': val_probs,
            'y_train': y_train,
            'y_val': y_val,
        }

        print(f"  训练: {train_acc:.4f} | 验证: {val_acc:.4f} | Gap: {gap:.2f}pp | ECE: {ece:.4f}")
        print(f"  阈值后: Acc={val_acc_thr:.4f}, 平局召回={cr_thr['平局']['recall']:.4f}")

    # 生成图表
    print("\n生成对比图表...")
    charts_dir = os.path.join(LOG_DIR, 'overfit_comparison')
    os.makedirs(charts_dir, exist_ok=True)

    plot_prediction_distribution(
        results,
        os.path.join(charts_dir, 'prediction_distribution_comparison.png')
    )
    plot_overfit_gap(
        results,
        os.path.join(charts_dir, 'overfit_gap_comparison.png')
    )
    plot_probability_distribution(
        results,
        os.path.join(charts_dir, 'probability_distribution_comparison.png')
    )
    plot_calibration_curve(
        results,
        os.path.join(charts_dir, 'calibration_curve_comparison.png')
    )

    # 输出 JSON 对比指标
    comparison = {}
    for config_key, config in CONFIGS.items():
        r = results[config_key]
        comparison[config_key] = {
            'label': config['label'],
            'params': {
                'learning_rate': config['params']['learning_rate'],
                'max_depth': config['params']['max_depth'],
                'reg_lambda': config['params']['reg_lambda'],
                'reg_alpha': config['params']['reg_alpha'],
                'min_child_weight': config['params']['min_child_weight'],
                'gamma': config['params']['gamma'],
                'n_estimators': config['n_rounds'],
            },
            'train_accuracy': float(r['train_acc']),
            'val_accuracy_original': float(r['val_acc']),
            'val_accuracy_threshold': float(r['val_acc_thr']),
            'overfit_gap_pp': float(r['gap']),
            'ece': float(r['ece']),
            'logloss': float(r['logloss']),
            'draw_recall_threshold': float(r['draw_recall_thr']),
            'draw_precision_threshold': float(r['draw_precision_thr']),
        }

    json_path = os.path.join(charts_dir, 'overfit_comparison_metrics.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2)
    print(f"\n  JSON 指标: {json_path}")

    print("\n" + "=" * 60)
    print("对比分析完成!")
    print("=" * 60)
    print(f"\n图表保存目录: {charts_dir}")


if __name__ == '__main__':
    main()
