"""
西甲联赛完整预测模型流水线
============================
基于现有模型架构，对西甲联赛进行：
1. 胜平负 (WDL) 预测 - LightGBM + XGBoost Ensemble
2. 让球胜平负 (Handicap) 预测 - T-005 v3 两阶段模型
3. 总进球数 (Total Goals) 预测 - T-004 多分类模型
4. 比分 (Score) 预测 - Poisson 回归 + 赔率推断

输出: 结构化预测结果 JSON + Markdown 报告
"""
import sys
import os
import json
import pickle
import sqlite3
import math
import warnings
import numpy as np
import pandas as pd
from datetime import datetime
from collections import defaultdict

warnings.filterwarnings('ignore')

# 路径配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(ROOT_DIR, 'assets')
DATA_DIR = os.path.join(ROOT_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'odds.db')

sys.path.insert(0, SCRIPT_DIR)

# ============================================================
# 第一部分: 胜平负 (WDL) 预测
# ============================================================

def load_laliga_data():
    """从 odds.db 加载西甲联赛比赛数据（复用 load_match_data_odds）"""
    from feature_utils import load_match_data_odds

    df = load_match_data_odds()

    # 过滤西甲
    df = df[df['competition_name'].str.contains('西甲', na=False)].copy()

    df['date'] = pd.to_datetime(df['date'])

    # 解析 actual_score
    def parse_score(s):
        if pd.isna(s) or s is None:
            return (None, None)
        parts = str(s).split('-')
        if len(parts) == 2:
            try:
                return (int(parts[0]), int(parts[1]))
            except:
                return (None, None)
        return (None, None)

    scores = df['actual_score'].apply(parse_score)
    df['home_goals'] = scores.apply(lambda x: x[0])
    df['away_goals'] = scores.apply(lambda x: x[1])
    df['total_goals'] = df['home_goals'] + df['away_goals']

    # 构建 actual_wdl 标签
    def get_wdl_label(row):
        h = row['home_goals']
        a = row['away_goals']
        if pd.isna(h) or pd.isna(a):
            return None
        if h > a:
            return 'home'
        elif h == a:
            return 'draw'
        else:
            return 'away'

    df['actual_wdl_label'] = df.apply(get_wdl_label, axis=1)

    # 过滤有比分的数据
    df = df[df['home_goals'].notna()].copy()

    print(f"西甲数据加载: {len(df)} 场比赛")
    print(f"  日期范围: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"  胜平负分布: H={len(df[df['actual_wdl_label']=='home'])} D={len(df[df['actual_wdl_label']=='draw'])} A={len(df[df['actual_wdl_label']=='away'])}")

    return df


def build_laliga_wdl_features(df):
    """为西甲构建 WDL 特征（复用现有特征工程）"""
    from feature_utils import build_all_features, load_config

    CONFIG = load_config()
    include_odds = CONFIG.get('training', {}).get('include_odds_features', True)

    X, y = build_all_features(df, include_odds=include_odds)

    print(f"WDL 特征维度: {X.shape[1]}")
    return X, y


def train_wdl_model_laliga(X, y):
    """在西甲数据上训练 WDL 模型"""
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import accuracy_score, log_loss, classification_report
    import lightgbm as lgb
    import xgboost as xgb

    # 时间序列划分
    train_size = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:train_size], X.iloc[train_size:]
    y_train, y_test = y.iloc[:train_size], y.iloc[train_size:]

    print(f"\n训练集: {len(X_train)} 场 (H={sum(y_train==2)} D={sum(y_train==1)} A={sum(y_train==0)})")
    print(f"测试集: {len(X_test)} 场 (H={sum(y_test==2)} D={sum(y_test==1)} A={sum(y_test==0)})")

    # 标准化
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    # LightGBM
    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (3 * class_counts)

    lgb_params = {
        'objective': 'multiclass', 'num_class': 3, 'metric': 'multi_logloss',
        'max_depth': 2, 'learning_rate': 0.03, 'num_leaves': 8,
        'subsample': 0.6, 'colsample_bytree': 0.6,
        'reg_alpha': 1.0, 'reg_lambda': 10.0,
        'min_child_weight': 10, 'min_data_in_leaf': 30,
        'seed': 42, 'verbose': -1
    }

    lgb_train = lgb.Dataset(X_train_scaled, y_train, weight=class_weights[y_train])
    lgb_val = lgb.Dataset(X_test_scaled, y_test, reference=lgb_train)

    lgb_model = lgb.train(lgb_params, lgb_train, num_boost_round=100,
                           valid_sets=[lgb_val],
                           callbacks=[lgb.early_stopping(15), lgb.log_evaluation(0)])

    lgb_pred = lgb_model.predict(X_test_scaled)
    lgb_acc = accuracy_score(y_test, np.argmax(lgb_pred, axis=1))
    lgb_ll = log_loss(y_test, lgb_pred)

    # XGBoost
    xgb_params = {
        'objective': 'multi:softprob', 'num_class': 3, 'eval_metric': 'mlogloss',
        'max_depth': 2, 'learning_rate': 0.03, 'subsample': 0.6,
        'colsample_bytree': 0.6, 'gamma': 0.5, 'min_child_weight': 10,
        'reg_alpha': 1.0, 'reg_lambda': 10.0, 'seed': 42, 'verbosity': 0
    }

    dtrain = xgb.DMatrix(X_train_scaled, label=y_train)
    dtrain.set_weight(class_weights[y_train])
    dtest = xgb.DMatrix(X_test_scaled, label=y_test)

    xgb_model = xgb.train(xgb_params, dtrain, num_boost_round=100,
                           evals=[(dtrain, 'train'), (dtest, 'test')],
                           early_stopping_rounds=15, verbose_eval=0)

    xgb_pred = xgb_model.predict(dtest)
    xgb_acc = accuracy_score(y_test, np.argmax(xgb_pred, axis=1))
    xgb_ll = log_loss(y_test, xgb_pred)

    # 集成预测
    ensemble_pred = (lgb_pred + xgb_pred) / 2
    ensemble_acc = accuracy_score(y_test, np.argmax(ensemble_pred, axis=1))
    ensemble_ll = log_loss(y_test, ensemble_pred)

    print(f"\n=== WDL 模型评估 ===")
    print(f"LightGBM:  Acc={lgb_acc:.4f}  LogLoss={lgb_ll:.4f}")
    print(f"XGBoost:   Acc={xgb_acc:.4f}  LogLoss={xgb_ll:.4f}")
    print(f"Ensemble:  Acc={ensemble_acc:.4f}  LogLoss={ensemble_ll:.4f}")

    results = {
        'lgb': {'accuracy': float(lgb_acc), 'log_loss': float(lgb_ll)},
        'xgb': {'accuracy': float(xgb_acc), 'log_loss': float(xgb_ll)},
        'ensemble': {'accuracy': float(ensemble_acc), 'log_loss': float(ensemble_ll)},
        'test_samples': len(y_test),
        'train_samples': len(y_train),
        'feature_dim': X.shape[1]
    }

    return lgb_model, xgb_model, scaler, results, X_test, y_test, ensemble_pred


# ============================================================
# 第二部分: 让球胜平负 (Handicap) 预测 (T-005 v3)
# ============================================================

def predict_handicap_laliga():
    """使用 T-005 v3 模型对西甲进行让球预测"""
    print("\n" + "=" * 60)
    print("让球胜平负预测 (T-005 v3)")
    print("=" * 60)

    try:
        from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES
        from hcp_features import MAX_VALID_TIMESTAMP, HCP_RESULT_NAMES
        from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
        from deploy_t005v2_final import (
            two_stage_predict_final,
            OPTIMAL_CLASS_WEIGHT, OPTIMAL_TEMPERATURE,
            OPTIMAL_DYNAMIC_THRESHOLDS, DEFAULT_DRAW_THRESHOLD,
        )

        # 加载模型
        draw_path = os.path.join(ASSETS_DIR, 't005v3_draw_detector.pkl')
        dir_path = os.path.join(ASSETS_DIR, 't005v3_direction_predictor.pkl')
        elo_path = os.path.join(ASSETS_DIR, 't005v3_elo_ratings.json')

        with open(draw_path, 'rb') as f:
            draw_model = pickle.load(f)
        with open(dir_path, 'rb') as f:
            dir_model = pickle.load(f)
        with open(elo_path, 'r', encoding='utf-8') as f:
            elo_snapshot = json.load(f)

        print(f"T-005 v3 模型已加载 (Elo: {len(elo_snapshot.get('elo_ratings', {}))} 队)")

        # 构建特征
        features_df, labels_df = build_all_features_v2()
        features_df = merge_expanded_labels(features_df, labels_df)
        features_df = add_elo_features_hcp(features_df, elo_snapshot)

        # 过滤西甲
        conn = sqlite3.connect(DB_PATH)
        laliga_matches = pd.read_sql_query(
            "SELECT match_id FROM matches WHERE match_type LIKE '%西甲%'", conn
        )
        conn.close()

        laliga_ids = set(laliga_matches['match_id'].tolist())
        features_df = features_df[features_df['match_id'].isin(laliga_ids)].copy()

        print(f"西甲让球特征: {len(features_df)} 场比赛")

        if len(features_df) == 0:
            print("⚠️ 无西甲让球数据")
            return None

        # 预测
        X = features_df[V2_ALL_FEATURES].values
        draw_probs, dir_probs = two_stage_predict_final(
            draw_model, dir_model, X.T,
            OPTIMAL_CLASS_WEIGHT, OPTIMAL_TEMPERATURE,
            OPTIMAL_DYNAMIC_THRESHOLDS, DEFAULT_DRAW_THRESHOLD
        )

        LABEL_NAMES = ['上盘赢', '走水', '下盘赢']
        predictions = np.argmax(dir_probs, axis=1)
        results = []
        for i, idx in enumerate(features_df.index):
            results.append({
                'match_id': features_df.loc[idx, 'match_id'],
                'prediction': LABEL_NAMES[predictions[i]],
                'draw_prob': float(draw_probs[i]),
                'dir_probs': dir_probs[i].tolist(),
                'confidence': float(np.max(dir_probs[i]))
            })

        return results

    except Exception as e:
        print(f"让球预测出错: {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================
# 第三部分: 总进球数 (Total Goals) 预测 (T-004)
# ============================================================

def predict_total_goals_laliga():
    """使用 T-004 模型对西甲进行总进球预测"""
    print("\n" + "=" * 60)
    print("总进球数预测 (T-004)")
    print("=" * 60)

    try:
        from tg_features import TG_COLS, extract_tg_features
        from train_tg_model import add_elo_features_tg
        from elo_rating import EloRating

        # 从 odds.db 加载西甲数据
        conn = sqlite3.connect(DB_PATH)

        query = """
        SELECT m.match_id, m.home_team, m.away_team, m.match_date,
               m.actual_score, m.actual_total_goals
        FROM matches m
        WHERE m.match_type LIKE '%西甲%'
        ORDER BY m.match_date
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        df['match_date'] = pd.to_datetime(df['match_date'])
        df = df[df['actual_total_goals'].notna()].copy()

        print(f"西甲总进球数据: {len(df)} 场比赛")

        # 提取 TG 赔率特征
        conn = sqlite3.connect(DB_PATH)
        tg_features = extract_tg_features(df, conn)
        conn.close()

        if tg_features is None or len(tg_features) == 0:
            print("⚠️ 无法提取 TG 赔率特征")
            return None

        # 添加 Elo 特征
        elo = EloRating()
        elo.fit(df)
        tg_features = add_elo_features_tg(tg_features, elo)

        # 加载训练好的 TG 模型
        tg_model_path = os.path.join(ASSETS_DIR, 't004_tg_lgb_model.pkl')
        if os.path.exists(tg_model_path):
            with open(tg_model_path, 'rb') as f:
                tg_model = pickle.load(f)
            print("T-004 模型已加载")
        else:
            print("⚠️ T-004 模型文件不存在，使用启发式预测")
            return heuristic_tg_predict(df)

        # 预测
        feature_cols = [c for c in TG_COLS if c in tg_features.columns]
        X = tg_features[feature_cols].fillna(0).values
        probs = tg_model.predict(X)

        # 7类: 0,1,2,3,4,5,6+
        TG_CLASSES = ['0球', '1球', '2球', '3球', '4球', '5球', '6+球']
        exp_goals = np.sum(probs * np.arange(len(TG_CLASSES)), axis=1)

        # 大/小球
        over_25_probs = probs[:, 3:].sum(axis=1)  # >=3球

        results = []
        for i in range(len(df)):
            results.append({
                'match_id': df.iloc[i]['match_id'],
                'home_team': df.iloc[i]['home_team'],
                'away_team': df.iloc[i]['away_team'],
                'predicted_class': TG_CLASSES[np.argmax(probs[i])],
                'expected_goals': float(exp_goals[i]),
                'over_25_prob': float(over_25_probs[i]),
                'tg_probs': {TG_CLASSES[j]: float(probs[i][j]) for j in range(len(TG_CLASSES))}
            })

        return results

    except Exception as e:
        print(f"总进球预测出错: {e}")
        import traceback
        traceback.print_exc()
        return None


def heuristic_tg_predict(df):
    """启发式总进球预测（无模型时使用）"""
    results = []
    for _, row in df.iterrows():
        results.append({
            'match_id': row['match_id'],
            'home_team': row['home_team'],
            'away_team': row['away_team'],
            'predicted_class': '2球',
            'expected_goals': 2.5,
            'over_25_prob': 0.5,
            'tg_probs': {'0球': 0.08, '1球': 0.17, '2球': 0.25, '3球': 0.22, '4球': 0.14, '5球': 0.08, '6+球': 0.06}
        })
    return results


# ============================================================
# 第四部分: 比分 (Score) 预测 - Poisson 模型
# ============================================================

def predict_score_laliga(df):
    """Poisson 回归 + 赔率信息预测西甲比分"""
    print("\n" + "=" * 60)
    print("比分预测 (Poisson + 赔率)")
    print("=" * 60)

    from sklearn.linear_model import PoissonRegressor
    from sklearn.preprocessing import StandardScaler

    # 使用历史数据训练 Poisson 模型
    df_train = df[df['home_goals'].notna()].copy()

    # 构建简单特征
    # 使用球队历史场均进球作为特征
    home_avg_goals = df_train.groupby('home_team_name')['home_goals'].mean().to_dict()
    away_avg_goals = df_train.groupby('away_team_name')['away_goals'].mean().to_dict()
    home_avg_conceded = df_train.groupby('home_team_name')['away_goals'].mean().to_dict()
    away_avg_conceded = df_train.groupby('away_team_name')['home_goals'].mean().to_dict()

    global_home_avg = df_train['home_goals'].mean()
    global_away_avg = df_train['away_goals'].mean()

    # 从赔率历史中获取隐含概率
    conn = sqlite3.connect(DB_PATH)
    score_odds = pd.read_sql_query("""
        SELECT m.match_id, s.score, s.odds
        FROM matches m
        INNER JOIN match_id_mapping mm ON m.match_id = mm.matches_match_id
        INNER JOIN score_history s ON mm.sh_match_id = s.match_id
        WHERE m.match_type LIKE '%\u897f\u7532%'
    """, conn)
    conn.close()

    # 按 match_id 聚合比分概率
    score_probs = {}
    if len(score_odds) > 0:
        for mid, group in score_odds.groupby('match_id'):
            score_probs[mid] = dict(zip(group['score'], group['odds']))

    # 预测函数
    def predict_match_score(home_team, away_team, match_id=None):
        home_att = home_avg_goals.get(home_team, global_home_avg)
        away_att = away_avg_goals.get(away_team, global_away_avg)
        home_def = home_avg_conceded.get(home_team, global_away_avg)
        away_def = away_avg_conceded.get(away_team, global_home_avg)

        lambda_home = home_att * away_def / global_away_avg
        lambda_away = away_att * home_def / global_home_avg

        # 泊松概率
        max_goals = 6
        score_matrix = np.zeros((max_goals + 1, max_goals + 1))
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                score_matrix[i, j] = (
                    np.exp(-lambda_home) * lambda_home**i / math.factorial(i) *
                    np.exp(-lambda_away) * lambda_away**j / math.factorial(j)
                )

        # 归一化
        score_matrix /= score_matrix.sum()

        # 如果有赔率信息，与 Poisson 融合
        if match_id and match_id in score_probs:
            odds_weights = score_probs[match_id]
            for score_str, weight in odds_weights.items():
                try:
                    parts = score_str.split('-')
                    h, a = int(parts[0]), int(parts[1])
                    if h <= max_goals and a <= max_goals:
                        score_matrix[h, a] = 0.3 * score_matrix[h, a] + 0.7 * weight
                except:
                    continue
            score_matrix /= score_matrix.sum()

        # Top 5 比分
        flat = [(i, j, score_matrix[i, j]) for i in range(max_goals + 1) for j in range(max_goals + 1)]
        flat.sort(key=lambda x: x[2], reverse=True)
        top5 = [f"{i}-{j}" for i, j, _ in flat[:5]]

        # 胜平负概率
        home_win_prob = sum(score_matrix[i, j] for i in range(max_goals + 1) for j in range(max_goals + 1) if i > j)
        draw_prob = sum(score_matrix[i, j] for i in range(max_goals + 1) for j in range(max_goals + 1) if i == j)
        away_win_prob = sum(score_matrix[i, j] for i in range(max_goals + 1) for j in range(max_goals + 1) if i < j)

        # 最可能比分
        best_i, best_j, _ = max(flat, key=lambda x: x[2])

        return {
            'most_likely_score': f"{best_i}-{best_j}",
            'top5_scores': top5,
            'home_win_prob': float(home_win_prob),
            'draw_prob': float(draw_prob),
            'away_win_prob': float(away_win_prob),
            'expected_home_goals': float(lambda_home),
            'expected_away_goals': float(lambda_away),
            'score_matrix': [[float(score_matrix[i, j]) for j in range(5)] for i in range(5)]
        }

    # 对最后30场进行验证
    test_df = df_train.tail(30).copy()
    correct_top5 = 0
    correct_top1 = 0

    for _, row in test_df.iterrows():
        pred = predict_match_score(row['home_team_name'], row['away_team_name'], row['match_id'])
        actual = f"{int(row['home_goals'])}-{int(row['away_goals'])}"
        if actual in pred['top5_scores']:
            correct_top5 += 1
        if actual == pred['most_likely_score']:
            correct_top1 += 1

    print(f"比分预测验证 (最近30场):")
    print(f"  Top-1 准确率: {correct_top1/30:.2%}")
    print(f"  Top-5 准确率: {correct_top5/30:.2%}")

    return predict_match_score, {
        'top1_accuracy': correct_top1 / 30,
        'top5_accuracy': correct_top5 / 30,
        'home_avg_goals': float(global_home_avg),
        'away_avg_goals': float(global_away_avg)
    }


# ============================================================
# 第五部分: 综合预测与报告
# ============================================================

def generate_laliga_predictions(df, wdl_model, wdl_scaler, X_test, y_test, ensemble_pred):
    """生成西甲联赛综合预测结果"""
    print("\n" + "=" * 60)
    print("西甲联赛综合预测")
    print("=" * 60)

    WDL_LABELS = ['客胜', '平局', '主胜']

    # 找出西甲最后 N 场比赛
    recent = df.tail(20).copy()

    # WDL 预测 - 复用已有特征
    from feature_utils import build_all_features
    X_all, y_all = build_all_features(df, include_odds=True)

    # 对齐特征 - 使用训练时的特征列
    train_cols = X_test.columns.tolist()
    common_cols = [c for c in train_cols if c in X_all.columns]
    X_recent_aligned = X_all[common_cols].tail(20)

    # 标准化
    from sklearn.preprocessing import StandardScaler
    scaler2 = StandardScaler()
    X_recent_scaled = scaler2.fit_transform(X_recent_aligned)

    # 预测
    lgb_pred = wdl_model.predict(X_recent_scaled)

    predictions = []
    for i, (_, row) in enumerate(recent.iterrows()):
        probs = lgb_pred[i]
        pred_class = np.argmax(probs)
        predictions.append({
            'match_id': row['match_id'],
            'date': str(row['date'].date()),
            'home_team': row['home_team_name'],
            'away_team': row['away_team_name'],
            'prediction': WDL_LABELS[pred_class],
            'prob_home': float(probs[2]),
            'prob_draw': float(probs[1]),
            'prob_away': float(probs[0]),
            'confidence': float(np.max(probs)),
            'actual_score': row.get('actual_score', 'N/A'),
            'actual_wdl': row.get('actual_wdl_label', 'N/A'),
        })

    return predictions


def save_report(wdl_results, hcp_results, tg_results, score_results, output_path):
    """生成 Markdown 预测报告"""
    WDL_LABELS = ['客胜', '平局', '主胜']

    report = f"""# 西甲联赛预测模型报告

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 数据来源: odds.db (西甲 3 赛季 × 380 场)

---

## 一、模型架构概览

| 预测类型 | 模型 | 方法 | 特征维度 |
|---------|------|------|---------|
| 胜平负 (WDL) | LightGBM + XGBoost Ensemble | 多分类 (3类) | {wdl_results.get('feature_dim', 'N/A')} |
| 让球胜平负 | T-005 v3 两阶段 | 走水检测 + 方向预测 | 71 |
| 总进球数 | T-004 LightGBM | 多分类 (7类) | 62 |
| 比分 | Poisson + 赔率融合 | 泊松分布 + 市场赔率 | N/A |

---

## 二、WDL 胜平负模型评估

| 指标 | LightGBM | XGBoost | Ensemble |
|------|----------|---------|----------|
| 准确率 | {wdl_results['lgb']['accuracy']:.4f} | {wdl_results['xgb']['accuracy']:.4f} | {wdl_results['ensemble']['accuracy']:.4f} |
| LogLoss | {wdl_results['lgb']['log_loss']:.4f} | {wdl_results['xgb']['log_loss']:.4f} | {wdl_results['ensemble']['log_loss']:.4f} |

- 训练样本: {wdl_results['train_samples']} 场
- 测试样本: {wdl_results['test_samples']} 场
- 特征维度: {wdl_results['feature_dim']}

---

## 三、比分预测验证

| 指标 | 值 |
|------|-----|
| Top-1 准确率 | {score_results['top1_accuracy']:.2%} |
| Top-5 准确率 | {score_results['top5_accuracy']:.2%} |
| 西甲主场场均进球 | {score_results['home_avg_goals']:.2f} |
| 西甲客场场均进球 | {score_results['away_avg_goals']:.2f} |

---

## 四、结论与建议

1. **WDL 模型**: Ensemble 准确率 {wdl_results['ensemble']['accuracy']:.1%}，建议关注主胜概率 > 0.45 的比赛
2. **让球预测**: 走水预测率需控制在 20% 左右，偏离需调整阈值
3. **总进球**: 西甲大球率约 50%，需结合球队风格调整
4. **比分预测**: Top-5 准确率 {score_results['top5_accuracy']:.1%}，适合作为参考而非精确预测

---

*报告由 laliga_prediction_pipeline.py 自动生成*
"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存: {output_path}")


# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 60)
    print("西甲联赛预测模型流水线")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 加载西甲数据
    df = load_laliga_data()

    # 2. WDL 预测
    print("\n>>> 第一步: 胜平负 (WDL) 模型训练与预测")
    X, y = build_laliga_wdl_features(df)
    lgb_model, xgb_model, scaler, wdl_results, X_test, y_test, ensemble_pred = train_wdl_model_laliga(X, y)

    # 3. 比分预测
    print("\n>>> 第二步: 比分预测模型")
    predict_score_fn, score_results = predict_score_laliga(df)

    # 4. 让球预测 (T-005 v3)
    print("\n>>> 第三步: 让球胜平负预测")
    try:
        hcp_results = predict_handicap_laliga()
    except Exception as e:
        print(f"让球预测跳过: {e}")
        hcp_results = None

    # 5. 总进球预测 (T-004)
    print("\n>>> 第四步: 总进球数预测")
    try:
        tg_results = predict_total_goals_laliga()
    except Exception as e:
        print(f"总进球预测跳过: {e}")
        tg_results = None

    # 6. 生成报告
    report_path = os.path.join(ROOT_DIR, 'reports', f'laliga_prediction_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.md')
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    save_report(wdl_results, hcp_results, tg_results, score_results, report_path)

    # 7. 保存预测结果 JSON
    json_path = os.path.join(ROOT_DIR, 'reports', f'laliga_predictions_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    predictions = generate_laliga_predictions(df, lgb_model, scaler, X_test, y_test, ensemble_pred)

    output = {
        'generated_at': datetime.now().isoformat(),
        'league': '西甲',
        'wdl_model': wdl_results,
        'score_model': score_results,
        'predictions': predictions
    }

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n预测结果 JSON 已保存: {json_path}")

    print("\n" + "=" * 60)
    print("西甲预测模型流水线完成")
    print("=" * 60)


if __name__ == '__main__':
    main()