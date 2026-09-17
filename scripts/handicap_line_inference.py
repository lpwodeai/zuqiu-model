"""
让球盘口线反推模型 (T-005 路径1)
================================

从 handicap_history 的 hcp_win/draw/lose 三个赔率反推盘口线（handicap_line），
再用 actual_score + 反推盘口线计算实际让球结果标签，将 T-005 训练样本从 242 场扩充至 ~3,908 场。

核心公式（已用 42 场验证样本 100% 验证通过）:
    adjusted_diff = (home_goals - away_goals) + handicap_line
        adjusted_diff > 0  → 胜（上盘赢, label=0）
        adjusted_diff = 0  → 平（走水,   label=1）
        adjusted_diff < 0  → 负（下盘赢, label=2）

数据现状:
    - handicap_history: 4,014 场比赛，有 hcp_win/draw/lose 但无盘口线
    - matches.handicap（盘口线）: 仅 42 场（0.8%）
    - matches.actual_score（比分）: 5,258 场（100%）
    - matches.actual_handicap（标签）: 仅 242 场（4.6%）
    - 可反推扩充: 3,672 场（有赔率+有正常比分+无标签）

反推策略（三层判定）:
    Step 1: 方向判断 — hcp_win vs hcp_lose 的大小关系
    Step 2: 大小判断 — 热门方赔率的高低（越低→让/受让越多）
    Step 3: 整数盘验证 — hcp_draw 是否存在且合理

验证方法:
    - 42 场有真实盘口线的样本作为验证集
    - Leave-One-Out CV（LOOCV），因样本量小
    - 指标：盘口线分类准确率 + 让球结果标签准确率

运行:
    python scripts/handicap_line_inference.py
"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Optional, Tuple, Dict, List
import sys
import os
import json
import time
from datetime import datetime

# 机器学习
from sklearn.tree import DecisionTreeClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.preprocessing import StandardScaler
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

# ==============================================================================
# 配置
# ==============================================================================

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(REPORT_DIR, exist_ok=True)

# 2026-07 时间戳过滤阈值（project_memory 规则）
MAX_VALID_TIMESTAMP = "2026-07-01 00:00:00"

# 让球结果映射
HCP_RESULT_MAP = {'胜': 0, '平': 1, '负': 2}
HCP_RESULT_NAMES = {0: '上盘赢', 1: '走水', 2: '下盘赢'}

# 盘口线类别（中国竞彩仅使用整数盘）
HANDICAP_LINE_CLASSES = [-2.0, -1.0, 0.0, 1.0, 2.0]
HANDICAP_LINE_NAMES = {
    -2.0: '主队让2球', -1.0: '主队让1球', 0.0: '平手盘',
    1.0: '主队受让1球', 2.0: '主队受让2球'
}


# ==============================================================================
# 数据加载
# ==============================================================================

def load_validation_data(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载42场验证样本：有真实盘口线 + 有比分 + 有让球结果的比赛。

    返回 DataFrame，包含:
        - match_id, actual_score, handicap_line, actual_handicap
        - hcp_win, hcp_draw, hcp_lose（最新快照赔率）
        - home_team, away_team, league, date
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    query = """
        SELECT
            mt.match_id,
            mt.actual_score,
            mt.handicap as handicap_line,
            mt.actual_handicap,
            h.hcp_win, h.hcp_draw, h.hcp_lose,
            mt.home_team, mt.away_team,
            mt.match_type as league,
            mt.match_date as date
        FROM matches mt
        INNER JOIN match_id_mapping mp ON mt.match_id = mp.matches_match_id
        INNER JOIN handicap_history h ON mp.sh_match_id = h.match_id
        WHERE mt.handicap IS NOT NULL
          AND mt.actual_score IS NOT NULL
          AND mt.actual_handicap IS NOT NULL
          AND h.timestamp = (
              SELECT MAX(h2.timestamp) FROM handicap_history h2
              WHERE h2.match_id = h.match_id AND h2.timestamp < ?
          )
        ORDER BY mt.match_date
    """

    df = pd.read_sql(query, conn, params=(MAX_VALID_TIMESTAMP,))

    if close_conn:
        conn.close()

    print(f"[INFER] 加载验证样本: {len(df)} 场")
    print(f"[INFER] 盘口线分布:")
    for line, count in df['handicap_line'].value_counts().sort_index().items():
        print(f"    {line:+.1f} ({HANDICAP_LINE_NAMES.get(line, '?')}): {count} 场")

    return df


def load_inference_target(conn: Optional[sqlite3.Connection] = None) -> pd.DataFrame:
    """
    加载需要反推盘口线的比赛：有赔率 + 有正常比分 + 无让球结果标签。

    返回 DataFrame，同 load_validation_data 结构（handicap_line/actual_handicap 为 NULL）。
    """
    close_conn = False
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        close_conn = True

    query = """
        SELECT
            mt.match_id,
            mt.actual_score,
            mt.handicap as handicap_line,
            mt.actual_handicap,
            h.hcp_win, h.hcp_draw, h.hcp_lose,
            mt.home_team, mt.away_team,
            mt.match_type as league,
            mt.match_date as date
        FROM matches mt
        INNER JOIN match_id_mapping mp ON mt.match_id = mp.matches_match_id
        INNER JOIN handicap_history h ON mp.sh_match_id = h.match_id
        WHERE mt.actual_score IS NOT NULL
          AND mt.actual_score NOT LIKE '%其它%'
          AND mt.actual_handicap IS NULL
          AND h.timestamp = (
              SELECT MAX(h2.timestamp) FROM handicap_history h2
              WHERE h2.match_id = h.match_id AND h2.timestamp < ?
          )
        ORDER BY mt.match_date
    """

    df = pd.read_sql(query, conn, params=(MAX_VALID_TIMESTAMP,))

    if close_conn:
        conn.close()

    print(f"[INFER] 加载反推目标: {len(df)} 场（有赔率+有比分+无标签）")

    return df


# ==============================================================================
# 特征工程
# ==============================================================================

def build_inference_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    从 hcp_win/draw/lose 构建反推盘口线的特征矩阵。

    特征列表（12维）:
        1. hcp_win, hcp_draw, hcp_lose（原始赔率）
        2. inv_prob_win, inv_prob_draw, inv_prob_lose（隐含概率）
        3. win_lose_ratio（赔率比值）
        4. win_lose_diff（赔率差值）
        5. is_home_favorite（方向指示：主队是否热门）
        6. favorite_odds（热门方赔率 = min(win, lose)）
        7. underdog_odds（冷门方赔率 = max(win, lose)）
        8. odds_spread（赔率价差 = underdog - favorite）
        9. draw_odds_level（走水赔率分段编码）
    """
    features = pd.DataFrame(index=df.index)

    eps = 1e-10

    # 原始赔率
    features['hcp_win'] = df['hcp_win'].fillna(3.0).astype(float)
    features['hcp_draw'] = df['hcp_draw'].fillna(3.5).astype(float)
    features['hcp_lose'] = df['hcp_lose'].fillna(3.0).astype(float)

    win = features['hcp_win'].values
    draw = features['hcp_draw'].values
    lose = features['hcp_lose'].values

    # 隐含概率（1/赔率 归一化）
    inv_win = 1.0 / np.clip(win, eps, None)
    inv_draw = 1.0 / np.clip(draw, eps, None)
    inv_lose = 1.0 / np.clip(lose, eps, None)
    inv_sum = inv_win + inv_draw + inv_lose

    features['inv_prob_win'] = inv_win / inv_sum
    features['inv_prob_draw'] = inv_draw / inv_sum
    features['inv_prob_lose'] = inv_lose / inv_sum

    # 赔率比值与差值
    features['win_lose_ratio'] = win / np.clip(lose, eps, None)
    features['win_lose_diff'] = win - lose

    # 方向指示
    features['is_home_favorite'] = (win < lose).astype(int)

    # 热门/冷门方赔率
    features['favorite_odds'] = np.minimum(win, lose)
    features['underdog_odds'] = np.maximum(win, lose)
    features['odds_spread'] = features['underdog_odds'] - features['favorite_odds']

    # 走水赔率分段编码（整数盘走水赔率通常 3.0~4.0）
    draw_vals = draw.copy()
    draw_level = np.zeros(len(draw_vals), dtype=int)
    draw_level[(draw_vals > 0) & (draw_vals < 3.0)] = 1      # 很低，走水可能性大
    draw_level[(draw_vals >= 3.0) & (draw_vals < 3.5)] = 2   # 正常整数盘
    draw_level[(draw_vals >= 3.5) & (draw_vals < 4.0)] = 3   # 正常整数盘
    draw_level[(draw_vals >= 4.0) & (draw_vals < 5.0)] = 4   # 偏高
    draw_level[(draw_vals >= 5.0)] = 5                        # 极高/半球盘
    draw_level[(draw_vals == 0) | np.isnan(draw_vals)] = 0    # 无走水赔率
    features['draw_odds_level'] = draw_level

    feature_cols = list(features.columns)
    print(f"[INFER] 构建反推特征: {len(feature_cols)} 维")
    print(f"    特征列: {feature_cols}")

    return features, feature_cols


# ==============================================================================
# 让球结果计算
# ==============================================================================

def parse_score(score_str: str) -> Tuple[int, int]:
    """
    解析比分字符串 → (home_goals, away_goals)。
    支持两种分隔符：
      - 冒号格式: '2:1'（2023-2024赛季为主）
      - 破折号格式: '2-1'（2024-2025及以后赛季为主）
    无法解析时返回 (None, None)。
    """
    if score_str is None:
        return None, None
    try:
        s = str(score_str).strip()
        if '其它' in s:
            return None, None
        # 优先尝试冒号分隔
        if ':' in s:
            parts = s.split(':')
            return int(parts[0]), int(parts[1])
        # 破折号分隔（如 '2-1'）
        if '-' in s:
            parts = s.split('-')
            return int(parts[0]), int(parts[1])
        return None, None
    except (ValueError, IndexError):
        return None, None


def compute_handicap_result(home_goals: int, away_goals: int, handicap_line: float) -> Optional[int]:
    """
    用比分 + 盘口线计算让球结果标签。

    adjusted_diff = (home_goals - away_goals) + handicap_line
        > 0 → 胜(0), = 0 → 平(1), < 0 → 负(2)
    """
    if home_goals is None or away_goals is None or handicap_line is None:
        return None
    adjusted_diff = (home_goals - away_goals) + handicap_line
    if adjusted_diff > 0:
        return 0  # 胜（上盘赢）
    elif adjusted_diff == 0:
        return 1  # 平（走水）
    else:
        return 2  # 负（下盘赢）


# ==============================================================================
# 模型训练与验证
# ==============================================================================

def validate_loocv(X: np.ndarray, y: np.ndarray, model_factory, model_name: str) -> Dict:
    """
    Leave-One-Out 交叉验证（适用于小样本）。

    返回:
        - accuracy: 盘口线分类准确率
        - predictions: 每个样本的预测值
        - y_true: 真实值
    """
    loo = LeaveOneOut()
    predictions = np.zeros(len(y), dtype=float)
    true_labels = y.copy()

    for train_idx, test_idx in loo.split(X):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        model = model_factory()
        model.fit(X_train, y_train)
        predictions[test_idx] = model.predict(X_test)

    accuracy = accuracy_score(true_labels, predictions)

    print(f"\n[INFER] {model_name} LOOCV 验证结果:")
    print(f"    盘口线分类准确率: {accuracy:.4f} ({accuracy*100:.1f}%)")

    # 混淆矩阵
    cm = confusion_matrix(true_labels, predictions, labels=HANDICAP_LINE_CLASSES)
    print(f"    混淆矩阵 (行=真实, 列=预测):")
    header = "          " + "  ".join([f'{int(l):>+5d}' for l in HANDICAP_LINE_CLASSES])
    print(header)
    for i, line in enumerate(HANDICAP_LINE_CLASSES):
        print(f"    {int(line):>+5d}  " + "  ".join([f'{v:5d}' for v in cm[i]]))

    return {
        'accuracy': accuracy,
        'predictions': predictions,
        'y_true': true_labels,
        'confusion_matrix': cm,
    }


def validate_label_accuracy(df: pd.DataFrame, predicted_lines: np.ndarray) -> Dict:
    """
    验证让球结果标签准确率：
    用反推盘口线 + actual_score 计算标签，与实际 actual_handicap 对比。

    这是最关键的指标——反推的最终目的是生成正确的训练标签。
    """
    correct = 0
    total = 0
    label_results = {'correct': 0, 'total': 0, 'by_class': {0: [0, 0], 1: [0, 0], 2: [0, 0]}}

    for i, (_, row) in enumerate(df.iterrows()):
        home_goals, away_goals = parse_score(row['actual_score'])
        if home_goals is None:
            continue

        pred_line = predicted_lines[i]
        pred_label = compute_handicap_result(home_goals, away_goals, pred_line)

        true_label = HCP_RESULT_MAP.get(row['actual_handicap'])

        if pred_label is not None and true_label is not None:
            total += 1
            if pred_label == true_label:
                correct += 1
                label_results['by_class'][true_label][0] += 1
            label_results['by_class'][true_label][1] += 1

    accuracy = correct / total if total > 0 else 0

    print(f"\n[INFER] 让球结果标签准确率:")
    print(f"    总样本: {total}")
    print(f"    正确: {correct}")
    print(f"    准确率: {accuracy:.4f} ({accuracy*100:.1f}%)")

    print(f"    按类别:")
    for label, name in HCP_RESULT_NAMES.items():
        correct_cls, total_cls = label_results['by_class'][label]
        acc_cls = correct_cls / total_cls if total_cls > 0 else 0
        print(f"      {name}({label}): {correct_cls}/{total_cls} = {acc_cls*100:.1f}%")

    return {
        'accuracy': accuracy,
        'correct': correct,
        'total': total,
        'by_class': label_results['by_class'],
    }


def validate_rule_based(df: pd.DataFrame) -> Dict:
    """
    规则反推法（基线对比）：
    基于赔率规律的简单规则，用于与 ML 模型对比。

    规则:
        - hcp_win < hcp_lose → 主队受让（正盘）
            - hcp_win < 1.5 → +2
            - 1.5 ≤ hcp_win < 2.2 → +1
            - hcp_win ≥ 2.2 → 0
        - hcp_win > hcp_lose → 主队让球（负盘）
            - hcp_lose < 1.5 → -2
            - 1.5 ≤ hcp_lose < 2.2 → -1
            - hcp_lose ≥ 2.2 → 0
        - hcp_win ≈ hcp_lose → 0
    """
    predictions = []

    for _, row in df.iterrows():
        win = float(row['hcp_win']) if pd.notna(row['hcp_win']) else 3.0
        lose = float(row['hcp_lose']) if pd.notna(row['hcp_lose']) else 3.0

        if abs(win - lose) < 0.15:
            # 平手盘
            pred = 0.0
        elif win < lose:
            # 主队热门 → 受让（正盘）
            if win < 1.5:
                pred = 2.0
            elif win < 2.2:
                pred = 1.0
            else:
                pred = 0.0
        else:
            # 客队热门 → 主队让球（负盘）
            if lose < 1.5:
                pred = -2.0
            elif lose < 2.2:
                pred = -1.0
            else:
                pred = 0.0

        predictions.append(pred)

    predictions = np.array(predictions)
    y_true = df['handicap_line'].values
    accuracy = accuracy_score(y_true, predictions)

    print(f"\n[INFER] 规则反推法（基线）:")
    print(f"    盘口线分类准确率: {accuracy:.4f} ({accuracy*100:.1f}%)")

    # 标签准确率
    label_acc = validate_label_accuracy(df, predictions)

    return {
        'accuracy': accuracy,
        'predictions': predictions,
        'label_accuracy': label_acc,
    }


# ==============================================================================
# 主流程
# ==============================================================================

def main():
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🏆 T-005 路径1：让球盘口线反推模型")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  目标: 从 hcp_win/draw/lose 反推盘口线，验证42场样本精度")
    print(f"  验证方法: LOOCV + 让球结果标签准确率")
    print("=" * 70)

    conn = sqlite3.connect(DB_PATH)

    # ---- Step 1: 加载验证数据 ----
    print("\n📊 Step 1: 加载42场验证样本...")
    df_val = load_validation_data(conn)

    # ---- Step 2: 构建特征 ----
    print("\n🔧 Step 2: 构建反推特征...")
    X_df, feature_cols = build_inference_features(df_val)
    X = X_df.values
    y = df_val['handicap_line'].values

    print(f"    X shape: {X.shape}")
    print(f"    y 分布: {dict(zip(*np.unique(y, return_counts=True)))}")

    # ---- Step 3: 规则反推法（基线）----
    print("\n📏 Step 3: 规则反推法（基线对比）...")
    rule_results = validate_rule_based(df_val)

    # ---- Step 4: 决策树 LOOCV ----
    print("\n🌳 Step 4: 决策树模型 LOOCV 验证...")
    dt_factory = lambda: DecisionTreeClassifier(
        max_depth=4,
        min_samples_leaf=2,
        random_state=42
    )
    dt_results = validate_loocv(X, y, dt_factory, "DecisionTree(max_depth=4)")

    # 决策树标签准确率
    dt_label_acc = validate_label_accuracy(df_val, dt_results['predictions'])

    # ---- Step 5: 逻辑回归 LOOCV ----
    print("\n📐 Step 5: 逻辑回归模型 LOOCV 验证...")
    lr_factory = lambda: LogisticRegression(
        multi_class='multinomial',
        solver='lbfgs',
        max_iter=1000,
        C=1.0,
        random_state=42
    )
    lr_pipe_factory = lambda: _LogRegPipe()
    lr_results = validate_loocv(X, y, lr_pipe_factory, "LogisticRegression(标准化)")

    # 逻辑回归标签准确率
    lr_label_acc = validate_label_accuracy(df_val, lr_results['predictions'])

    # ---- Step 6: 选择最优模型 ----
    print("\n" + "=" * 70)
    print("📋 Step 6: 模型对比汇总")
    print("=" * 70)

    comparison = pd.DataFrame({
        '模型': ['规则反推法', '决策树(DT)', '逻辑回归(LR)'],
        '盘口线准确率': [
            rule_results['accuracy'],
            dt_results['accuracy'],
            lr_results['accuracy'],
        ],
        '标签准确率': [
            rule_results['label_accuracy']['accuracy'],
            dt_label_acc['accuracy'],
            lr_label_acc['accuracy'],
        ],
    })
    comparison['盘口线准确率'] = comparison['盘口线准确率'].apply(lambda x: f"{x*100:.1f}%")
    comparison['标签准确率'] = comparison['标签准确率'].apply(lambda x: f"{x*100:.1f}%")
    print(comparison.to_string(index=False))

    # 选择最优
    models = {
        '规则反推法': (rule_results['accuracy'], rule_results['label_accuracy']['accuracy']),
        '决策树(DT)': (dt_results['accuracy'], dt_label_acc['accuracy']),
        '逻辑回归(LR)': (lr_results['accuracy'], lr_label_acc['accuracy']),
    }
    best_model_name = max(models, key=lambda k: models[k][1])  # 按标签准确率选最优
    best_line_acc, best_label_acc = models[best_model_name]
    print(f"\n    🏆 最优模型: {best_model_name}")
    print(f"       盘口线准确率: {best_line_acc*100:.1f}%")
    print(f"       标签准确率: {best_label_acc*100:.1f}%")

    # 达标判断
    print(f"\n    达标标准: 盘口线准确率≥80%, 标签准确率≥85%")
    line_ok = best_line_acc >= 0.80
    label_ok = best_label_acc >= 0.85
    print(f"    盘口线准确率 {'✅ 达标' if line_ok else '❌ 未达标'} ({best_line_acc*100:.1f}% vs 80%)")
    print(f"    标签准确率 {'✅ 达标' if label_ok else '❌ 未达标'} ({best_label_acc*100:.1f}% vs 85%)")

    # ---- Step 7: 保存验证结果 ----
    print("\n💾 Step 7: 保存验证结果...")

    results_json = {
        'timestamp': timestamp_str,
        'task': 'T-005-路径1-盘口线反推验证',
        'validation_samples': len(df_val),
        'feature_dim': len(feature_cols),
        'feature_cols': feature_cols,
        'handicap_line_distribution': {
            str(k): int(v) for k, v in df_val['handicap_line'].value_counts().to_dict().items()
        },
        'models': {
            'rule_based': {
                'line_accuracy': float(rule_results['accuracy']),
                'label_accuracy': float(rule_results['label_accuracy']['accuracy']),
                'label_by_class': {
                    str(k): {'correct': v[0], 'total': v[1]}
                    for k, v in rule_results['label_accuracy']['by_class'].items()
                },
            },
            'decision_tree': {
                'line_accuracy': float(dt_results['accuracy']),
                'label_accuracy': float(dt_label_acc['accuracy']),
                'label_by_class': {
                    str(k): {'correct': v[0], 'total': v[1]}
                    for k, v in dt_label_acc['by_class'].items()
                },
            },
            'logistic_regression': {
                'line_accuracy': float(lr_results['accuracy']),
                'label_accuracy': float(lr_label_acc['accuracy']),
                'label_by_class': {
                    str(k): {'correct': v[0], 'total': v[1]}
                    for k, v in lr_label_acc['by_class'].items()
                },
            },
        },
        'best_model': best_model_name,
        'best_line_accuracy': float(best_line_acc),
        'best_label_accuracy': float(best_label_acc),
        'meets_standard': {
            'line_acc_ge_80pct': bool(line_ok),
            'label_acc_ge_85pct': bool(label_ok),
        },
        'elapsed_seconds': round(time.time() - t_start, 2),
    }

    json_path = os.path.join(REPORT_DIR, f'hcp_line_inference_validation_{timestamp_str}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results_json, f, ensure_ascii=False, indent=2)
    print(f"    结果已保存: {json_path}")

    conn.close()

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ 盘口线反推模型验证完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")

    return results_json


class _LogRegPipe:
    """逻辑回归 + 标准化的 Pipeline 封装（用于 LOOCV）。"""

    def __init__(self):
        self.scaler = StandardScaler()
        # sklearn 新版本已弃用 multi_class 参数，默认使用 multinomial
        self.model = LogisticRegression(
            solver='lbfgs',
            max_iter=1000,
            C=1.0,
            random_state=42,
        )

    def fit(self, X, y):
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)
        return self

    def predict(self, X):
        X_scaled = self.scaler.transform(X)
        return self.model.predict(X_scaled)


if __name__ == "__main__":
    main()
