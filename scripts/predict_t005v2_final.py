"""
T-005 v2 最终模型 — 最近5场真实比赛预测
=========================================

加载已部署的最终模型（三步优化后），对最近5场真实比赛做预测，
验证输出结果是否与评估报告一致。

使用最终优化参数:
    - Stage1 class_weight = {0: 1.0, 1: 1.5}  (ratio=1.5)
    - Temperature = 2.150
    - 动态阈值（按盘口线类别）

运行: python scripts/predict_t005v2_final.py
"""

import sys
import os
import json
import pickle
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES
from hcp_features import MAX_VALID_TIMESTAMP, HCP_RESULT_NAMES
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
from train_hcp_model_v2 import apply_rule_adjustments, generate_rule_warnings
from dynamic_draw_threshold import categorize_handicap_line, get_handicap_categories
from deploy_t005v2_final import (
    two_stage_predict_final,
    OPTIMAL_CLASS_WEIGHT, OPTIMAL_TEMPERATURE,
    OPTIMAL_DYNAMIC_THRESHOLDS, DEFAULT_DRAW_THRESHOLD,
)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DB_PATH = os.path.join(DATA_DIR, 'odds.db')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(REPORT_DIR, exist_ok=True)

LABEL_NAMES = ['上盘赢', '走水', '下盘赢']


def load_final_models():
    """加载已部署的最终模型和 Elo 快照。"""
    print("📦 加载已部署的最终模型...")

    draw_path = os.path.join(ASSETS_DIR, 't005v2_final_draw_detector.pkl')
    dir_path = os.path.join(ASSETS_DIR, 't005v2_final_direction_predictor.pkl')
    elo_path = os.path.join(ASSETS_DIR, 't005v2_final_elo_ratings.json')
    meta_path = os.path.join(ASSETS_DIR, 't005v2_final_metadata.json')

    with open(draw_path, 'rb') as f:
        draw_model = pickle.load(f)
    with open(dir_path, 'rb') as f:
        dir_model = pickle.load(f)
    with open(elo_path, 'r', encoding='utf-8') as f:
        elo_snapshot = json.load(f)
    with open(meta_path, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    print(f"  ✅ Stage1 走水检测器: {draw_path}")
    print(f"  ✅ Stage2 方向预测器: {dir_path}")
    print(f"  ✅ Elo 快照: {elo_path} ({len(elo_snapshot['elo_ratings'])} 支球队)")
    print(f"  ✅ 元数据: {meta_path}")
    print(f"  模型版本: {metadata['version']}")
    print(f"  优化参数: cw={OPTIMAL_CLASS_WEIGHT}, T={OPTIMAL_TEMPERATURE}, 阈值={OPTIMAL_DYNAMIC_THRESHOLDS}")

    return draw_model, dir_model, elo_snapshot, metadata


def build_features_for_prediction():
    """构建完整特征矩阵（47维 + 10维 Elo = 57维）。"""
    print("\n📊 构建特征矩阵 (47维 + 10维 Elo)...")

    features = build_all_features_v2()
    features = merge_expanded_labels(features)

    feature_cols = V2_ALL_FEATURES.copy()
    X = features[feature_cols].copy()
    X = X.apply(pd.to_numeric, errors='coerce')
    if X.isnull().any().any():
        X = X.fillna(X.median())

    y_raw = features['actual_handicap'].copy()
    label_map = {'胜': 0, '平': 1, '负': 2}
    y = y_raw.map(label_map)
    numeric_mask = y.isna() & y_raw.notna()
    if numeric_mask.any():
        y[numeric_mask] = pd.to_numeric(y_raw[numeric_mask], errors='coerce')
    y = y.fillna(-1).astype(int)

    meta = features[['league', 'date', 'home_team', 'away_team']].copy()
    meta['actual_handicap'] = y_raw
    meta['label_source'] = features.get('label_source', 'actual')

    valid_mask = y >= 0
    X = X[valid_mask]
    y = y[valid_mask]
    meta = meta[valid_mask]

    X, elo_cols = add_elo_features_hcp(X, meta)
    print(f"  特征: {X.shape[1]}维, 样本: {len(X)}场")

    return X, y, meta


def get_recent_matches(X, meta, n=5):
    """取最近 n 场比赛（按日期排序）。"""
    meta_with_date = meta.copy()
    meta_with_date['date_parsed'] = pd.to_datetime(meta_with_date['date'], errors='coerce')
    meta_with_date = meta_with_date.sort_values('date_parsed', ascending=False)

    recent_indices = meta_with_date.head(n).index
    X_recent = X.loc[recent_indices]
    meta_recent = meta.loc[recent_indices]

    return X_recent, meta_recent


def get_odds_info(meta_recent):
    """从数据库获取最近5场比赛的原始赔率信息。"""
    print("\n📊 获取原始赔率信息...")
    conn = sqlite3.connect(DB_PATH)
    odds_info = {}
    for mid in meta_recent.index:
        query = """
            SELECT h.hcp_win, h.hcp_draw, h.hcp_lose, mt.handicap
            FROM handicap_history h
            INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
            INNER JOIN matches mt ON m.matches_match_id = mt.match_id
            WHERE m.matches_match_id = ?
              AND h.timestamp < ?
            ORDER BY h.timestamp DESC LIMIT 1
        """
        df_odds = pd.read_sql(query, conn, params=(str(mid), MAX_VALID_TIMESTAMP))
        if len(df_odds) > 0:
            odds_info[mid] = {
                'hcp_win': float(df_odds.iloc[0]['hcp_win']),
                'hcp_draw': float(df_odds.iloc[0]['hcp_draw']),
                'hcp_lose': float(df_odds.iloc[0]['hcp_lose']),
                'handicap_line': df_odds.iloc[0]['handicap'],
            }
        else:
            odds_info[mid] = {'hcp_win': 0, 'hcp_draw': 0, 'hcp_lose': 0, 'handicap_line': None}
    conn.close()
    return odds_info


def predict_recent_5():
    """主预测流程。"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    print("=" * 70)
    print("🎯 T-005 v2 最终模型 — 最近5场真实比赛预测")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. 加载模型
    draw_model, dir_model, elo_snapshot, metadata = load_final_models()

    # 2. 构建特征
    X, y, meta = build_features_for_prediction()

    # 3. 取最近5场
    X_recent, meta_recent = get_recent_matches(X, meta, n=5)
    print(f"\n📋 最近5场比赛:")
    for i, (idx, m) in enumerate(meta_recent.iterrows()):
        print(f"  {i+1}. {m['date']} | {m['league']} | {m['home_team']} vs {m['away_team']}")

    # 4. 获取赔率信息
    odds_info = get_odds_info(meta_recent)

    # 5. 获取盘口线类别（用于动态阈值）
    handicap_categories = get_handicap_categories(meta_recent)
    print(f"\n📊 盘口线类别:")
    for idx in meta_recent.index:
        cat = handicap_categories.loc[idx]
        line = odds_info[idx]['handicap_line']
        threshold = OPTIMAL_DYNAMIC_THRESHOLDS.get(cat, DEFAULT_DRAW_THRESHOLD)
        print(f"  {meta_recent.loc[idx, 'home_team']} vs {meta_recent.loc[idx, 'away_team']}: "
              f"盘口线={line}, 类别={cat}, 阈值={threshold}")

    # 6. 预测（集成 T=2.150 + 动态阈值）
    print(f"\n🎯 最终模型预测 (T={OPTIMAL_TEMPERATURE} + 动态阈值)...")
    y_pred, y_proba = two_stage_predict_final(
        draw_model, dir_model, X_recent,
        handicap_categories=handicap_categories,
        temperature=OPTIMAL_TEMPERATURE,
        threshold_map=OPTIMAL_DYNAMIC_THRESHOLDS,
    )

    # 7. 应用规则调整
    hcp_win_series = pd.Series([odds_info[mid]['hcp_win'] for mid in X_recent.index], index=X_recent.index)
    hcp_lose_series = pd.Series([odds_info[mid]['hcp_lose'] for mid in X_recent.index], index=X_recent.index)
    handicap_series = pd.Series([odds_info[mid]['handicap_line'] for mid in X_recent.index], index=X_recent.index)

    y_pred_adj, y_proba_adj = apply_rule_adjustments(
        y_pred, y_proba, X_recent, hcp_win_series, hcp_lose_series, handicap_series
    )

    # 8. 展示结果
    print("\n" + "=" * 70)
    print("📊 预测结果")
    print("=" * 70)

    results = []
    n_correct = 0
    n_valid = 0

    for i, (idx, match) in enumerate(meta_recent.iterrows()):
        odds = odds_info[idx]
        pred = int(y_pred_adj[i])
        proba = y_proba_adj[i]
        conf = float(np.max(proba))

        # 实际结果
        ah = match.get('actual_handicap')
        actual_name = '未进行'
        actual_label = -1
        if pd.notna(ah) and ah in ['胜', '平', '负']:
            actual_name = {'胜': '上盘赢', '平': '走水', '负': '下盘赢'}[ah]
            actual_label = {'胜': 0, '平': 1, '负': 2}[ah]
        elif pd.notna(ah):
            try:
                actual_label = int(ah)
                actual_name = LABEL_NAMES[actual_label]
            except:
                pass

        if actual_label >= 0:
            n_valid += 1
            if pred == actual_label:
                n_correct += 1
                correct_flag = '✅'
            else:
                correct_flag = '❌'
        else:
            correct_flag = '—'

        print(f"\n  ┌─ 比赛 {i+1}: {match['home_team']} vs {match['away_team']}")
        print(f"  │  日期: {match['date']}  |  联赛: {match['league']}")
        print(f"  │  让球赔率: 上盘={odds['hcp_win']:.2f}  走水={odds['hcp_draw']:.2f}  下盘={odds['hcp_lose']:.2f}")
        print(f"  │  盘口线: {odds['handicap_line']}  |  类别: {handicap_categories.loc[idx]}  |  阈值: {OPTIMAL_DYNAMIC_THRESHOLDS.get(handicap_categories.loc[idx], DEFAULT_DRAW_THRESHOLD)}")
        print(f"  │  ───────────────────────────────────────")
        print(f"  │  🎯 预测: {LABEL_NAMES[pred]} ({conf*100:.1f}%)")
        print(f"  │  概率: 上盘={proba[0]*100:.1f}%  走水={proba[1]*100:.1f}%  下盘={proba[2]*100:.1f}%")
        print(f"  │  实际: {actual_name}  {correct_flag}")

        # 规则警示
        warnings_list = generate_rule_warnings(odds)
        if warnings_list:
            print(f"  │  ⚠️ 规则警示:")
            for w in warnings_list:
                print(f"  │    {w}")
        print(f"  └─")

        results.append({
            'match': f"{match['home_team']} vs {match['away_team']}",
            'date': str(match['date']),
            'league': match['league'],
            'odds': odds,
            'handicap_category': handicap_categories.loc[idx],
            'draw_threshold_used': OPTIMAL_DYNAMIC_THRESHOLDS.get(handicap_categories.loc[idx], DEFAULT_DRAW_THRESHOLD),
            'prediction': LABEL_NAMES[pred],
            'probability': {
                '上盘赢': float(proba[0]),
                '走水': float(proba[1]),
                '下盘赢': float(proba[2]),
            },
            'actual_result': actual_name,
            'correct': correct_flag,
            'warnings': warnings_list,
        })

    # 9. 汇总
    print("\n" + "=" * 70)
    print("📊 预测汇总")
    print("=" * 70)

    pred_dist = {name: 0 for name in LABEL_NAMES}
    for r in results:
        pred_dist[r['prediction']] += 1

    print(f"\n  预测分布:")
    for name in LABEL_NAMES:
        count = pred_dist[name]
        print(f"    {name}: {count}/5 ({count/5*100:.0f}%)")

    if n_valid > 0:
        print(f"\n  准确率: {n_correct}/{n_valid} ({n_correct/n_valid*100:.0f}%)")
    else:
        print(f"\n  准确率: 暂无已完赛比赛")

    # 10. 与评估报告一致性对比
    draw_pct = pred_dist['走水'] / 5 * 100
    home_pct = pred_dist['上盘赢'] / 5 * 100
    away_pct = pred_dist['下盘赢'] / 5 * 100
    draw_str = f"{draw_pct:.0f}% ({pred_dist['走水']}/5)"
    home_str = f"{home_pct:.0f}% ({pred_dist['上盘赢']}/5)"
    away_str = f"{away_pct:.0f}% ({pred_dist['下盘赢']}/5)"

    print(f"\n  📊 与评估报告一致性对比:")
    print(f"    {'指标':>12s}  {'评估报告(测试集783场)':>22s}  {'本次预测(5场)':>14s}")
    print(f"    {'走水预测占比':>12s}  {'21.3% (167/783)':>22s}  {draw_str:>14s}")
    print(f"    {'上盘赢占比':>12s}  {'35.5% (278/783)':>22s}  {home_str:>14s}")
    print(f"    {'下盘赢占比':>12s}  {'43.2% (338/783)':>22s}  {away_str:>14s}")

    # 11. 保存结果
    report = {
        'timestamp': timestamp,
        'model_version': metadata['version'],
        'optimization_params': {
            'class_weight': OPTIMAL_CLASS_WEIGHT,
            'temperature': OPTIMAL_TEMPERATURE,
            'dynamic_thresholds': OPTIMAL_DYNAMIC_THRESHOLDS,
        },
        'n_matches': len(results),
        'predictions': results,
        'prediction_distribution': pred_dist,
        'accuracy': f"{n_correct}/{n_valid}" if n_valid > 0 else "N/A",
    }
    report_path = os.path.join(REPORT_DIR, f't005v2_final_predict5_{timestamp}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n💾 预测结果已保存: {report_path}")


if __name__ == "__main__":
    predict_recent_5()
