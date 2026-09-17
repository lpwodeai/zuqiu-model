"""
T-005 模型部署脚本
==================

训练最终的 T-005 让球胜平负预测模型，持久化保存到 assets/ 目录。

部署产物:
    - t005_hcp_lgb_model.pkl: LightGBM 模型
    - t005_hcp_metadata.json: 特征列名、Elo 快照、版本信息
    - t005_elo_ratings.json: 全球队最新 Elo 评分（用于预测时构建 Elo 特征）

部署策略:
    - 使用全部 3,914 场数据训练最终模型（反推标签 3,672 + 真标签 242）
    - 保存特征列顺序，确保预测时特征对齐
    - 保存 Elo 评分快照（基于历史比赛结果计算到最新日期）

运行: python scripts/deploy_t005_model.py
"""

import sys
import os
import json
import pickle
import time
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features import build_hcp_features, HCP_RESULT_NAMES
from train_hcp_model import (
    prepare_data, add_elo_features_hcp, train_lgb_3class,
    merge_expanded_labels,
)
from elo_rating import (
    expected_score, update_elo, DEFAULT_ELO, K_FACTOR, HOME_ADVANTAGE
)

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DB_PATH = os.path.join(DATA_DIR, 'odds.db')


def build_team_elo_snapshot() -> dict:
    """
    基于历史比赛结果计算所有球队的最新 Elo 评分快照。
    按比赛日期顺序更新，返回 {team_name: elo_rating}。
    """
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT match_id, match_date, home_team, away_team, actual_score
        FROM matches
        WHERE actual_score IS NOT NULL
          AND actual_score != ''
        ORDER BY match_date
    """
    df = pd.read_sql(query, conn)
    conn.close()

    def parse_score(s):
        try:
            s = str(s).strip()
            if '其它' in s or s == '':
                return None, None
            for sep in [':', '-']:
                if sep in s:
                    parts = s.split(sep)
                    return int(parts[0]), int(parts[1])
            return None, None
        except:
            return None, None

    elo_ratings = {}  # team_name -> elo
    team_history = {}  # team_name -> list of (date, elo_after)

    for _, row in df.iterrows():
        hg, ag = parse_score(row['actual_score'])
        if hg is None:
            continue
        home = row['home_team']
        away = row['away_team']
        date = row['match_date']

        elo_h = elo_ratings.get(home, DEFAULT_ELO)
        elo_a = elo_ratings.get(away, DEFAULT_ELO)

        # 主队实际得分
        if hg > ag:
            actual_h = 1.0
        elif hg == ag:
            actual_h = 0.5
        else:
            actual_h = 0.0

        exp_h = expected_score(elo_h, elo_a, HOME_ADVANTAGE)
        new_elo_h, new_elo_a = update_elo(elo_h, elo_a, actual_h, K_FACTOR, HOME_ADVANTAGE)

        elo_ratings[home] = new_elo_h
        elo_ratings[away] = new_elo_a

        team_history.setdefault(home, []).append((date, new_elo_h))
        team_history.setdefault(away, []).append((date, new_elo_a))

    # 计算每支球队的近5场动量
    elo_momentum = {}
    for team, hist in team_history.items():
        if len(hist) >= 5:
            recent_5 = [h[1] for h in hist[-5:]]
            elo_momentum[team] = recent_5[-1] - recent_5[0]
        else:
            elo_momentum[team] = 0.0

    print(f"[ELO] 计算了 {len(elo_ratings)} 支球队的最新 Elo 评分")
    print(f"[ELO] Elo 评分范围: {min(elo_ratings.values()):.1f} ~ {max(elo_ratings.values()):.1f}")

    snapshot = {
        'elo_ratings': elo_ratings,
        'elo_momentum': elo_momentum,
        'default_elo': DEFAULT_ELO,
        'k_factor': K_FACTOR,
        'home_advantage': HOME_ADVANTAGE,
    }
    return snapshot


def build_elo_features_for_prediction(home_team, away_team, elo_snapshot):
    """
    使用 Elo 快照为单场比赛构建 10 维 Elo 特征。
    """
    elo_ratings = elo_snapshot['elo_ratings']
    elo_momentum = elo_snapshot['elo_momentum']
    home_adv = elo_snapshot['home_advantage']

    home_elo = elo_ratings.get(home_team, DEFAULT_ELO)
    away_elo = elo_ratings.get(away_team, DEFAULT_ELO)

    elo_diff = home_elo - away_elo + home_adv
    elo_ratio = home_elo / max(away_elo, 1.0)
    elo_home_expected = expected_score(home_elo, away_elo, home_adv)
    elo_away_expected = 1.0 - elo_home_expected
    # 平局概率估算（基于 Elo 差值的非线性映射）
    elo_draw_prob = 1.0 / (1.0 + np.exp(abs(elo_diff) / 100.0)) * 0.3 + 0.2
    home_momentum = elo_momentum.get(home_team, 0.0)
    away_momentum = elo_momentum.get(away_team, 0.0)
    elo_confidence = (home_elo + away_elo) / 3000.0

    return {
        'home_elo': home_elo,
        'away_elo': away_elo,
        'elo_diff': elo_diff,
        'elo_ratio': elo_ratio,
        'elo_home_expected': elo_home_expected,
        'elo_away_expected': elo_away_expected,
        'elo_draw_prob': elo_draw_prob,
        'home_elo_momentum': home_momentum,
        'away_elo_momentum': away_momentum,
        'elo_confidence': elo_confidence,
    }


def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    t_start = time.time()

    print("=" * 70)
    print("🚀 T-005 模型部署 — 训练最终模型并持久化")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  输出目录: {ASSETS_DIR}")
    print("=" * 70)

    if not LGB_AVAILABLE:
        print("❌ LightGBM 不可用，无法部署")
        return

    # Step 1: 构建特征 + 合并扩充标签
    print("\n📊 Step 1: 构建特征并合并扩充标签...")
    features = build_hcp_features()
    features = merge_expanded_labels(features)

    # Step 2: 准备数据 + Elo 特征
    print("\n🔧 Step 2: 准备数据 + Elo 特征...")
    X, y, meta = prepare_data(features)
    X, elo_cols = add_elo_features_hcp(X, meta)
    total_feature_dim = X.shape[1]

    print(f"\n  最终特征维度: {total_feature_dim} (HCP=15 + Elo={len(elo_cols)})")
    print(f"  训练样本: {len(X)} 场")

    # Step 3: 训练最终 LGB 模型（用全部数据）
    print("\n🎯 Step 3: 训练最终 LGB 模型（全量数据）...")
    # 使用 80% 训练 + 20% 评估（仅用于 early_stopping，最终模型用全部数据重训）
    split_idx = int(len(X) * 0.8)
    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)

    X_train_es = X_sorted.iloc[:split_idx]
    y_train_es = y_sorted.iloc[:split_idx]
    X_val_es = X_sorted.iloc[split_idx:]
    y_val_es = y_sorted.iloc[split_idx:]

    # 用 early stopping 确定最优 n_estimators
    es_model, es_acc = train_lgb_3class(X_train_es, y_train_es, X_val_es, y_val_es)
    best_n_estimators = es_model.best_iteration_ if hasattr(es_model, 'best_iteration_') else 150
    print(f"  Early stopping 确定最优 n_estimators: {best_n_estimators}")
    print(f"  评估集准确率: {es_acc*100:.1f}%")

    # 用全部数据 + 最优 n_estimators 重训最终模型
    print("\n  用全部数据重训最终模型...")
    final_model = lgb.LGBMClassifier(
        objective='multiclass',
        num_class=3,
        max_depth=3,
        learning_rate=0.03,
        n_estimators=best_n_estimators,
        num_leaves=15,
        min_child_samples=10,
        subsample=0.7,
        colsample_bytree=0.7,
        reg_alpha=0.5,
        reg_lambda=0.5,
        random_state=42,
        verbose=-1,
        force_col_wise=True,
    )
    final_model.fit(X, y)
    print(f"  ✅ 最终模型训练完成")

    # Step 4: 构建 Elo 快照
    print("\n📊 Step 4: 构建 Elo 评分快照...")
    elo_snapshot = build_team_elo_snapshot()

    # Step 5: 保存所有部署产物
    print("\n💾 Step 5: 保存部署产物...")

    # 5a: 保存 LGB 模型
    model_path = os.path.join(ASSETS_DIR, 't005_hcp_lgb_model.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump(final_model, f)
    print(f"  ✅ 模型已保存: {model_path}")

    # 5b: 保存元数据
    metadata = {
        'model_name': 'T-005 HCP LightGBM',
        'version': timestamp,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'task': '让球胜平负预测 (3类分类: 上盘赢/走水/下盘赢)',
        'feature_columns': list(X.columns),
        'feature_dim': total_feature_dim,
        'hcp_features': [c for c in X.columns if c.startswith('hcp_')],
        'elo_features': elo_cols,
        'training_samples': len(X),
        'class_distribution': {
            '上盘赢(0)': int((y == 0).sum()),
            '走水(1)': int((y == 1).sum()),
            '下盘赢(2)': int((y == 2).sum()),
        },
        'model_params': {
            'max_depth': 3,
            'learning_rate': 0.03,
            'n_estimators': best_n_estimators,
            'num_leaves': 15,
            'min_child_samples': 10,
            'subsample': 0.7,
            'colsample_bytree': 0.7,
            'reg_alpha': 0.5,
            'reg_lambda': 0.5,
        },
        'label_names': HCP_RESULT_NAMES,
        'eval_accuracy': float(es_acc),
        'data_source': 'odds.db handicap_history + 扩充数据集(反推标签)',
        'known_blindspots': [
            {'盲区': '走水预测', '召回率': '2.2%', '严重度': '高'},
            {'盲区': '冷门比赛', '准确率差距': '62.0pp', '严重度': '高'},
            {'盲区': '极端盘口(≥±2球)', '准确率差距': '5.7pp', '严重度': '中'},
        ],
    }
    metadata_path = os.path.join(ASSETS_DIR, 't005_hcp_metadata.json')
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 元数据已保存: {metadata_path}")

    # 5c: 保存 Elo 快照
    elo_path = os.path.join(ASSETS_DIR, 't005_elo_ratings.json')
    with open(elo_path, 'w', encoding='utf-8') as f:
        json.dump(elo_snapshot, f, ensure_ascii=False, indent=2)
    print(f"  ✅ Elo 快照已保存: {elo_path}")

    elapsed = time.time() - t_start
    print(f"\n{'='*70}")
    print(f"✅ T-005 模型部署完成! 耗时 {elapsed:.1f}s")
    print(f"{'='*70}")
    print(f"\n部署产物:")
    print(f"  1. {model_path}")
    print(f"  2. {metadata_path}")
    print(f"  3. {elo_path}")
    print(f"\n下一步: 运行 python scripts/predict_t005.py 进行预测")


if __name__ == "__main__":
    main()
