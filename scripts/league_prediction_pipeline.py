"""
通用联赛预测模型流水线
========================
一次开发，五大联赛通用。通过 league 参数区分联赛，所有模型逻辑联赛无关。

支持的预测类型:
1. 胜平负 (WDL) - LightGBM + XGBoost Ensemble（已有 train_models.py 跨联赛训练）
2. 让球胜平负 (Handicap) - T-005 v3 两阶段模型
3. 总进球数 (Total Goals) - T-004 多分类模型
4. 比分 (Score) - Poisson 回归 + 赔率信息融合

使用方式:
    python league_prediction_pipeline.py --league 西甲
    python league_prediction_pipeline.py --league 英超
    python league_prediction_pipeline.py --league 意甲
    python league_prediction_pipeline.py --league 德甲
    python league_prediction_pipeline.py --league 法甲
    python league_prediction_pipeline.py --league all   # 所有联赛
"""
import sys
import os
import math
import json
import argparse
import warnings
import numpy as np
import pandas as pd
from datetime import datetime

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(ROOT_DIR, 'assets')
DATA_DIR = os.path.join(ROOT_DIR, 'data')
REPORTS_DIR = os.path.join(ROOT_DIR, 'reports')
DB_PATH = os.path.join(DATA_DIR, 'odds.db')

sys.path.insert(0, SCRIPT_DIR)

LEAGUE_NAMES = {
    '西甲': '西甲', '英超': '英超', '意甲': '意甲',
    '德甲': '德甲', '法甲': '法甲',
    'laliga': '西甲', 'epl': '英超', 'seriea': '意甲',
    'bundesliga': '德甲', 'ligue1': '法甲'
}

WDL_LABELS = ['客胜', '平局', '主胜']


# ============================================================
# 通用数据加载
# ============================================================

def load_league_data(league: str):
    """加载指定联赛数据（复用 load_match_data_odds）"""
    from feature_utils import load_match_data_odds

    league_cn = LEAGUE_NAMES.get(league, league)
    df = load_match_data_odds()
    df = df[df['competition_name'].str.contains(league_cn, na=False)].copy()
    df['date'] = pd.to_datetime(df['date'])

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

    def get_wdl(row):
        h, a = row['home_goals'], row['away_goals']
        if pd.isna(h) or pd.isna(a):
            return None
        if h > a: return 'home'
        elif h == a: return 'draw'
        else: return 'away'

    df['actual_wdl_label'] = df.apply(get_wdl, axis=1)
    df = df[df['home_goals'].notna()].copy()

    print(f"[{league_cn}] 数据加载: {len(df)} 场")
    print(f"  日期: {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"  H={len(df[df['actual_wdl_label']=='home'])} D={len(df[df['actual_wdl_label']=='draw'])} A={len(df[df['actual_wdl_label']=='away'])}")

    return df


# ============================================================
# 1. 胜平负 (WDL) 预测 - 通用跨联赛模型
# ============================================================

def train_wdl_model(df, league: str):
    """训练 WDL 模型（跨联赛通用）"""
    from feature_utils import build_all_features, load_config
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import accuracy_score, log_loss
    import lightgbm as lgb
    import xgboost as xgb

    CONFIG = load_config()
    X, y = build_all_features(df, include_odds=CONFIG.get('training', {}).get('include_odds_features', True))

    train_size = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:train_size], X.iloc[train_size:]
    y_train, y_test = y.iloc[:train_size], y.iloc[train_size:]

    print(f"\n[{league}] WDL 训练: {len(X_train)}/{len(X_test)} 场, {X.shape[1]} 维特征")

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s = scaler.transform(X_test)

    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (3 * class_counts)

    lgb_model = lgb.LGBMClassifier(
        objective='multiclass', num_class=3, max_depth=2,
        learning_rate=0.03, num_leaves=8, subsample=0.6,
        colsample_bytree=0.6, reg_alpha=1.0, reg_lambda=10.0,
        min_child_weight=10, min_data_in_leaf=30,
        n_estimators=100, random_state=42, verbose=-1
    )
    lgb_model.fit(X_train_s, y_train, sample_weight=class_weights[y_train])
    lgb_pred = lgb_model.predict_proba(X_test_s)
    lgb_acc = accuracy_score(y_test, np.argmax(lgb_pred, axis=1))

    xgb_model = xgb.XGBClassifier(
        objective='multi:softprob', num_class=3, max_depth=2,
        learning_rate=0.03, subsample=0.6, colsample_bytree=0.6,
        gamma=0.5, min_child_weight=10, reg_alpha=1.0,
        reg_lambda=10.0, n_estimators=100, random_state=42, verbosity=0
    )
    xgb_model.fit(X_train_s, y_train, sample_weight=class_weights[y_train])
    xgb_pred = xgb_model.predict_proba(X_test_s)
    xgb_acc = accuracy_score(y_test, np.argmax(xgb_pred, axis=1))

    ensemble_pred = (lgb_pred + xgb_pred) / 2
    ensemble_acc = accuracy_score(y_test, np.argmax(ensemble_pred, axis=1))

    print(f"  LGB={lgb_acc:.4f}  XGB={xgb_acc:.4f}  Ensemble={ensemble_acc:.4f}")

    return {
        'lgb_model': lgb_model, 'xgb_model': xgb_model, 'scaler': scaler,
        'lgb_acc': float(lgb_acc), 'xgb_acc': float(xgb_acc),
        'ensemble_acc': float(ensemble_acc),
        'train_samples': len(y_train), 'test_samples': len(y_test),
        'feature_dim': X.shape[1], 'train_cols': X_train.columns.tolist(),
        'X_test': X_test, 'y_test': y_test
    }


# ============================================================
# 2. 比分 (Score) 预测 - Poisson 通用模型
# ============================================================

def build_score_model(df, league: str):
    """
    构建联赛无关的 Poisson 比分预测模型。
    核心：球队历史场均进球/失球 + 赔率信息融合。
    换联赛只需换数据，模型逻辑不变。
    """
    from sklearn.linear_model import PoissonRegressor

    df_train = df[df['home_goals'].notna()].copy()

    # 球队级统计（数据驱动，联赛无关）
    home_avg_goals = df_train.groupby('home_team_name')['home_goals'].mean().to_dict()
    away_avg_goals = df_train.groupby('away_team_name')['away_goals'].mean().to_dict()
    home_avg_conceded = df_train.groupby('home_team_name')['away_goals'].mean().to_dict()
    away_avg_conceded = df_train.groupby('away_team_name')['home_goals'].mean().to_dict()

    global_home_avg = df_train['home_goals'].mean()
    global_away_avg = df_train['away_goals'].mean()

    # 加载比分赔率（联赛无关）
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    league_cn = LEAGUE_NAMES.get(league, league)
    score_odds = pd.read_sql_query(f"""
        SELECT m.match_id, s.score, s.odds
        FROM matches m
        INNER JOIN match_id_mapping mm ON m.match_id = mm.matches_match_id
        INNER JOIN score_history s ON mm.sh_match_id = s.match_id
        WHERE m.match_type LIKE '%{league_cn}%'
    """, conn)
    conn.close()

    score_probs = {}
    if len(score_odds) > 0:
        for mid, group in score_odds.groupby('match_id'):
            score_probs[mid] = dict(zip(group['score'], group['odds']))

    def predict_match(home_team, away_team, match_id=None):
        h_att = home_avg_goals.get(home_team, global_home_avg)
        a_att = away_avg_goals.get(away_team, global_away_avg)
        h_def = home_avg_conceded.get(home_team, global_away_avg)
        a_def = away_avg_conceded.get(away_team, global_home_avg)

        lambda_h = max(h_att * a_def / global_away_avg, 0.1)
        lambda_a = max(a_att * h_def / global_home_avg, 0.1)

        max_g = 6
        prob = np.zeros((max_g + 1, max_g + 1))
        for i in range(max_g + 1):
            for j in range(max_g + 1):
                prob[i, j] = (np.exp(-lambda_h) * lambda_h**i / math.factorial(i) *
                              np.exp(-lambda_a) * lambda_a**j / math.factorial(j))
        prob /= prob.sum()

        # 赔率融合
        if match_id and match_id in score_probs:
            for s, w in score_probs[match_id].items():
                try:
                    parts = s.split('-')
                    h, a = int(parts[0]), int(parts[1])
                    if h <= max_g and a <= max_g:
                        prob[h, a] = 0.3 * prob[h, a] + 0.7 * w
                except:
                    continue
            prob /= prob.sum()

        flat = [(i, j, prob[i, j]) for i in range(max_g + 1) for j in range(max_g + 1)]
        flat.sort(key=lambda x: x[2], reverse=True)
        top5 = [f"{i}-{j}" for i, j, _ in flat[:5]]
        best = max(flat, key=lambda x: x[2])

        home_win = sum(prob[i, j] for i in range(max_g + 1) for j in range(max_g + 1) if i > j)
        draw = sum(prob[i, j] for i in range(max_g + 1) for j in range(max_g + 1) if i == j)
        away_win = sum(prob[i, j] for i in range(max_g + 1) for j in range(max_g + 1) if i < j)

        return {
            'most_likely_score': f"{best[0]}-{best[1]}",
            'top5_scores': top5,
            'home_win_prob': float(home_win), 'draw_prob': float(draw),
            'away_win_prob': float(away_win),
            'expected_home_goals': float(lambda_h),
            'expected_away_goals': float(lambda_a),
        }

    # 验证
    test_n = min(30, len(df_train))
    test_df = df_train.tail(test_n)
    correct_top5 = 0
    correct_top1 = 0
    for _, row in test_df.iterrows():
        pred = predict_match(row['home_team_name'], row['away_team_name'], row['match_id'])
        actual = f"{int(row['home_goals'])}-{int(row['away_goals'])}"
        if actual in pred['top5_scores']:
            correct_top5 += 1
        if actual == pred['most_likely_score']:
            correct_top1 += 1

    print(f"[{league}] 比分验证 (n={test_n}): Top-1={correct_top1/test_n:.2%} Top-5={correct_top5/test_n:.2%}")

    return predict_match, {
        'top1_accuracy': correct_top1 / test_n,
        'top5_accuracy': correct_top5 / test_n,
        'home_avg_goals': float(global_home_avg),
        'away_avg_goals': float(global_away_avg),
        'teams_with_data': len(home_avg_goals),
    }


# ============================================================
# 3. 让球胜平负 (Handicap) 预测 - T-005 v3
# ============================================================

def predict_handicap(league: str):
    """T-005 v3 让球预测（联赛无关）"""
    print(f"\n[{league}] 让球预测 (T-005 v3)...")

    try:
        import pickle
        from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES
        from hcp_features import HCP_RESULT_NAMES
        from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
        from deploy_t005v2_final import (
            two_stage_predict_final, OPTIMAL_CLASS_WEIGHT,
            OPTIMAL_TEMPERATURE, OPTIMAL_DYNAMIC_THRESHOLDS, DEFAULT_DRAW_THRESHOLD
        )

        draw_path = os.path.join(ASSETS_DIR, 't005v3_draw_detector.pkl')
        dir_path = os.path.join(ASSETS_DIR, 't005v3_direction_predictor.pkl')
        elo_path = os.path.join(ASSETS_DIR, 't005v3_elo_ratings.json')

        if not all(os.path.exists(p) for p in [draw_path, dir_path, elo_path]):
            print(f"  ⚠️ T-005 v3 模型文件不完整，跳过")
            return None

        with open(draw_path, 'rb') as f:
            draw_model = pickle.load(f)
        with open(dir_path, 'rb') as f:
            dir_model = pickle.load(f)
        with open(elo_path, 'r', encoding='utf-8') as f:
            elo_snapshot = json.load(f)

        features_df, labels_df = build_all_features_v2()
        features_df = merge_expanded_labels(features_df, labels_df)
        features_df = add_elo_features_hcp(features_df, elo_snapshot)

        # 过滤联赛
        league_cn = LEAGUE_NAMES.get(league, league)
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        league_matches = pd.read_sql_query(
            f"SELECT match_id FROM matches WHERE match_type LIKE '%{league_cn}%'", conn
        )
        conn.close()

        league_ids = set(league_matches['match_id'].tolist())
        features_df = features_df[features_df['match_id'].isin(league_ids)].copy()

        if len(features_df) == 0:
            print(f"  ⚠️ 无{league}让球数据")
            return None

        X = features_df[V2_ALL_FEATURES].values
        draw_probs, dir_probs = two_stage_predict_final(
            draw_model, dir_model, X.T, OPTIMAL_CLASS_WEIGHT,
            OPTIMAL_TEMPERATURE, OPTIMAL_DYNAMIC_THRESHOLDS, DEFAULT_DRAW_THRESHOLD
        )

        LABEL_NAMES = ['上盘赢', '走水', '下盘赢']
        predictions = np.argmax(dir_probs, axis=1)
        results = []
        for i, idx in enumerate(features_df.index):
            results.append({
                'match_id': features_df.loc[idx, 'match_id'],
                'prediction': LABEL_NAMES[predictions[i]],
                'draw_prob': float(draw_probs[i]),
                'confidence': float(np.max(dir_probs[i]))
            })
        print(f"  {len(results)} 场预测完成")
        return results

    except Exception as e:
        print(f"  ⚠️ 让球预测失败: {e}")
        return None


# ============================================================
# 4. 总进球数 (Total Goals) 预测 - T-004
# ============================================================

def predict_total_goals(league: str):
    """T-004 总进球预测（联赛无关）"""
    print(f"\n[{league}] 总进球预测 (T-004)...")

    try:
        import pickle
        import sqlite3
        from tg_features import extract_tg_features
        from train_tg_model import add_elo_features_tg
        from elo_rating import EloRating

        conn = sqlite3.connect(DB_PATH)
        league_cn = LEAGUE_NAMES.get(league, league)
        query = f"""
        SELECT m.match_id, m.home_team, m.away_team, m.match_date,
               m.actual_score, m.actual_total_goals
        FROM matches m
        WHERE m.match_type LIKE '%{league_cn}%'
        ORDER BY m.match_date
        """
        df = pd.read_sql_query(query, conn)
        df['match_date'] = pd.to_datetime(df['match_date'])
        df = df[df['actual_total_goals'].notna()].copy()

        tg_features = extract_tg_features(df, conn)
        conn.close()

        if tg_features is None or len(tg_features) == 0:
            print(f"  ⚠️ 无法提取 TG 赔率特征")
            return None

        elo = EloRating()
        elo.fit(df)
        tg_features = add_elo_features_tg(tg_features, elo)

        tg_model_path = os.path.join(ASSETS_DIR, 't004_tg_lgb_model.pkl')
        if os.path.exists(tg_model_path):
            with open(tg_model_path, 'rb') as f:
                tg_model = pickle.load(f)
        else:
            print(f"  ⚠️ T-004 模型文件不存在")
            return None

        from tg_features import TG_COLS
        feature_cols = [c for c in TG_COLS if c in tg_features.columns]
        X = tg_features[feature_cols].fillna(0).values
        probs = tg_model.predict(X)

        TG_CLASSES = ['0球', '1球', '2球', '3球', '4球', '5球', '6+球']
        exp_goals = np.sum(probs * np.arange(len(TG_CLASSES)), axis=1)
        over_25 = probs[:, 3:].sum(axis=1)

        results = []
        for i in range(len(df)):
            results.append({
                'match_id': df.iloc[i]['match_id'],
                'home_team': df.iloc[i]['home_team'],
                'away_team': df.iloc[i]['away_team'],
                'predicted_class': TG_CLASSES[np.argmax(probs[i])],
                'expected_goals': float(exp_goals[i]),
                'over_25_prob': float(over_25[i]),
            })
        print(f"  {len(results)} 场预测完成")
        return results

    except Exception as e:
        print(f"  ⚠️ 总进球预测失败: {e}")
        return None


# ============================================================
# 综合预测 + 报告
# ============================================================

def run_league_pipeline(league: str):
    """对单个联赛执行完整预测流水线"""
    league_cn = LEAGUE_NAMES.get(league, league)
    print(f"\n{'='*60}")
    print(f"{league_cn} 预测流水线")
    print(f"{'='*60}")

    df = load_league_data(league)

    if len(df) < 50:
        print(f"⚠️ {league_cn} 数据不足 (<50场)，跳过")
        return None

    results = {'league': league_cn, 'timestamp': datetime.now().isoformat()}

    # 1. WDL
    print(f"\n>>> WDL 胜平负")
    wdl = train_wdl_model(df, league_cn)
    results['wdl'] = {k: v for k, v in wdl.items() if k not in ('lgb_model', 'xgb_model', 'scaler', 'X_test', 'y_test', 'train_cols')}

    # 2. 比分
    print(f"\n>>> 比分预测")
    predict_fn, score_metrics = build_score_model(df, league_cn)
    results['score'] = score_metrics

    # 3. 让球
    print(f"\n>>> 让球胜平负")
    hcp = predict_handicap(league)
    if hcp:
        results['handicap_count'] = len(hcp)

    # 4. 总进球
    print(f"\n>>> 总进球数")
    tg = predict_total_goals(league)
    if tg:
        results['tg_count'] = len(tg)

    return results


def generate_report(all_results: list, output_path: str):
    """生成通用联赛预测报告"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    report = f"""# 五大联赛预测模型报告

> 生成时间: {timestamp}
> 数据来源: odds.db

---

## 模型架构

| 预测类型 | 方法 | 联赛无关性 |
|---------|------|-----------|
| 胜平负 (WDL) | LightGBM + XGBoost Ensemble (3类) | 跨联赛训练，联赛权重补偿 |
| 比分 (Score) | Poisson 回归 + 赔率融合 | 数据驱动，球队级统计 |
| 让球 (Handicap) | T-005 v3 两阶段 | 跨联赛训练 |
| 总进球 (TG) | T-004 LightGBM (7类) | 跨联赛训练 |

---

## 各联赛 WDL 模型评估

| 联赛 | 训练场次 | 测试场次 | 特征维度 | LGB Acc | XGB Acc | Ensemble Acc |
|------|---------|---------|---------|---------|---------|-------------|
"""
    for r in all_results:
        if r and 'wdl' in r:
            wdl = r['wdl']
            report += f"| {r['league']} | {wdl['train_samples']} | {wdl['test_samples']} | {wdl['feature_dim']} | {wdl['lgb_acc']:.4f} | {wdl['xgb_acc']:.4f} | {wdl['ensemble_acc']:.4f} |\n"

    report += "\n## 各联赛比分预测验证\n\n"
    report += "| 联赛 | Top-1 | Top-5 | 主场场均进球 | 客场场均进球 | 球队数 |\n"
    report += "|------|-------|-------|-------------|-------------|--------|\n"
    for r in all_results:
        if r and 'score' in r:
            s = r['score']
            report += f"| {r['league']} | {s['top1_accuracy']:.2%} | {s['top5_accuracy']:.2%} | {s['home_avg_goals']:.2f} | {s['away_avg_goals']:.2f} | {s['teams_with_data']} |\n"

    report += f"""
---

## 结论

1. **WDL 模型**: 所有联赛使用同一套特征工程和模型架构，无需单独开发
2. **比分模型**: Poisson 参数由各联赛数据自动计算，换联赛即切换
3. **让球/总进球**: T-005 v3 / T-004 跨联赛训练，league 参数仅用于数据过滤

*报告由 league_prediction_pipeline.py 自动生成*
"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存: {output_path}")


# ============================================================
# 主程序
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='五大联赛通用预测模型流水线')
    parser.add_argument('--league', type=str, default='all',
                        choices=['all', '西甲', '英超', '意甲', '德甲', '法甲',
                                 'laliga', 'epl', 'seriea', 'bundesliga', 'ligue1'],
                        help='目标联赛 (默认: all)')
    args = parser.parse_args()

    print("=" * 60)
    print("五大联赛通用预测模型流水线")
    print(f"启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    leagues = ['西甲', '英超', '意甲', '德甲', '法甲'] if args.league == 'all' else [args.league]
    all_results = []

    for league in leagues:
        result = run_league_pipeline(league)
        all_results.append(result)

    # 生成报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(REPORTS_DIR, f'league_prediction_report_{timestamp}.md')
    os.makedirs(REPORTS_DIR, exist_ok=True)
    generate_report(all_results, report_path)

    print("\n" + "=" * 60)
    print("全部联赛预测完成")
    print("=" * 60)


if __name__ == '__main__':
    main()