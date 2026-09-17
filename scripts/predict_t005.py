"""
T-005 模型预测脚本
==================

加载已部署的 T-005 模型，对最近 N 场真实比赛进行让球胜平负预测。

预测流程:
    1. 从 odds.db 加载最近 N 场有让球赔率的比赛
    2. 构建赛前可用特征（15维 HCP + 10维 Elo = 25维）
    3. 用已部署的 LightGBM 模型预测让球结果（上盘赢/走水/下盘赢）
    4. 输出预测结果 + 概率分布 + 置信度分析

使用方式:
    python scripts/predict_t005.py              # 默认预测最近 5 场
    python scripts/predict_t005.py --n 10       # 预测最近 10 场
    python scripts/predict_t005.py --date 2026-08-10  # 预测指定日期附近的比赛
"""

import sys
import os
import json
import pickle
import argparse
import numpy as np
import pandas as pd
import sqlite3
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features import HCP_COLS, HCP_RESULT_NAMES, MAX_VALID_TIMESTAMP

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DB_PATH = os.path.join(DATA_DIR, 'odds.db')

MODEL_PATH = os.path.join(ASSETS_DIR, 't005_hcp_lgb_model.pkl')
METADATA_PATH = os.path.join(ASSETS_DIR, 't005_hcp_metadata.json')
ELO_PATH = os.path.join(ASSETS_DIR, 't005_elo_ratings.json')


def load_model_artifacts():
    """加载模型和元数据"""
    print("📦 加载模型产物...")

    with open(MODEL_PATH, 'rb') as f:
        model = pickle.load(f)

    with open(METADATA_PATH, 'r', encoding='utf-8') as f:
        metadata = json.load(f)

    with open(ELO_PATH, 'r', encoding='utf-8') as f:
        elo_snapshot = json.load(f)

    print(f"  ✅ 模型: {metadata['model_name']} v{metadata['version']}")
    print(f"  ✅ 特征维度: {metadata['feature_dim']} (HCP={len(metadata['hcp_features'])} + Elo={len(metadata['elo_features'])})")
    print(f"  ✅ 训练样本: {metadata['training_samples']} 场")
    print(f"  ✅ 评估准确率: {metadata['eval_accuracy']*100:.1f}%")

    return model, metadata, elo_snapshot


def load_recent_matches(n=5, target_date=None):
    """
    从 odds.db 加载最近 N 场有让球赔率的比赛。

    返回 DataFrame，每行一场比赛，含最新让球赔率快照。
    """
    conn = sqlite3.connect(DB_PATH)

    # 查询最近有让球赔率的比赛
    if target_date:
        date_filter = f"AND mt.match_date <= '{target_date}'"
    else:
        date_filter = ""

    query = f"""
        SELECT
            m.matches_match_id as match_id,
            mt.match_date as date,
            mt.home_team,
            mt.away_team,
            mt.match_type as league,
            mt.actual_score,
            mt.actual_handicap,
            mt.handicap as handicap_line,
            h.hcp_win,
            h.hcp_draw,
            h.hcp_lose,
            h.timestamp as odds_timestamp
        FROM handicap_history h
        INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
        INNER JOIN matches mt ON m.matches_match_id = mt.match_id
        WHERE h.timestamp < '{MAX_VALID_TIMESTAMP}'
            {date_filter}
        ORDER BY mt.match_date DESC, h.timestamp DESC
    """

    df = pd.read_sql(query, conn)
    conn.close()

    print(f"\n📊 加载让球赔率数据: {len(df)} 条记录")

    # 取每场比赛的最新赔率快照
    df = df.sort_values(['date', 'odds_timestamp'], ascending=[False, False])
    df = df.drop_duplicates(subset=['match_id'], keep='first')

    # 取最近 N 场
    df = df.head(n)

    print(f"  选取最近 {len(df)} 场比赛（日期范围: {df['date'].min()} ~ {df['date'].max()}）")

    return df


def build_hcp_features_for_match(row):
    """
    为单场比赛构建 15 维 HCP 特征。
    复用 hcp_features.py 中的逻辑。
    """
    hcp_cols = HCP_COLS
    probs_raw = np.array([row['hcp_win'], row['hcp_draw'], row['hcp_lose']], dtype=float)

    # 填充 NaN
    probs_raw = np.nan_to_num(probs_raw, nan=0.0)

    # 归一化
    row_sum = probs_raw.sum()
    if row_sum == 0:
        probs = np.array([1/3, 1/3, 1/3])
    else:
        probs = probs_raw / row_sum

    eps = 1e-10
    features = {}

    # 基础概率 (3维)
    features['hcp_prob_win'] = probs[0]
    features['hcp_prob_draw'] = probs[1]
    features['hcp_prob_lose'] = probs[2]

    # 衍生特征 (12维)
    features['hcp_home_strength'] = probs[0] - probs[2]
    features['hcp_draw_risk'] = probs[1]
    features['hcp_confidence'] = np.max(probs)
    features['hcp_entropy'] = -np.sum(probs * np.log(np.clip(probs, eps, 1.0)))
    features['hcp_expected_value'] = (1.0 / np.clip(probs_raw[0], eps, None)) - (1.0 / np.clip(probs_raw[2], eps, None))
    features['hcp_volatility'] = np.std(probs)

    inv_probs = 1.0 / np.clip(probs_raw, eps, None)
    inv_sum = inv_probs.sum()
    features['hcp_market_sentiment'] = inv_probs[0] / inv_sum
    features['hcp_underdog_ratio'] = probs[2] / np.clip(probs[0], eps, None)
    features['hcp_favorite_margin'] = 1.0 - features['hcp_confidence']
    features['hcp_balance'] = np.abs(probs[0] - probs[2])
    features['hcp_upset_risk'] = probs[1] + probs[2]

    raw_sum = probs_raw.sum()
    features['hcp_odds_skew'] = (probs_raw[0] - probs_raw[2]) / np.clip(raw_sum, eps, None)

    return features


def build_elo_features_for_match(home_team, away_team, elo_snapshot):
    """
    使用 Elo 快照为单场比赛构建 10 维 Elo 特征。
    """
    from elo_rating import expected_score, DEFAULT_ELO, HOME_ADVANTAGE

    elo_ratings = elo_snapshot['elo_ratings']
    elo_momentum = elo_snapshot['elo_momentum']
    home_adv = elo_snapshot.get('home_advantage', HOME_ADVANTAGE)

    home_elo = elo_ratings.get(home_team, DEFAULT_ELO)
    away_elo = elo_ratings.get(away_team, DEFAULT_ELO)

    elo_diff = home_elo - away_elo + home_adv
    elo_ratio = home_elo / max(away_elo, 1.0)
    elo_home_expected = expected_score(home_elo, away_elo, home_adv)
    elo_away_expected = 1.0 - elo_home_expected
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


def build_feature_matrix(matches_df, metadata, elo_snapshot):
    """
    为所有比赛构建特征矩阵，确保列顺序与训练时一致。
    """
    feature_cols = metadata['feature_columns']
    rows = []

    for _, match in matches_df.iterrows():
        # HCP 特征
        hcp_feats = build_hcp_features_for_match(match)
        # Elo 特征
        elo_feats = build_elo_features_for_match(
            match['home_team'], match['away_team'], elo_snapshot
        )

        all_feats = {**hcp_feats, **elo_feats}
        rows.append(all_feats)

    X = pd.DataFrame(rows)
    # 确保列顺序与训练时一致
    X = X.reindex(columns=feature_cols, fill_value=0.0)

    # 填充缺失值
    if X.isnull().any().any():
        X = X.fillna(X.median())

    return X


def parse_score(s):
    """解析比分字符串"""
    try:
        s = str(s).strip()
        if s == '' or s == 'None' or '其它' in s:
            return None, None
        for sep in [':', '-']:
            if sep in s:
                parts = s.split(sep)
                return int(parts[0]), int(parts[1])
        return None, None
    except:
        return None, None


def calculate_actual_handicap(home_goals, away_goals, handicap_line):
    """
    根据实际比分和盘口线计算实际让球结果。
    handicap_line 为负表示主队让球，为正表示主队受让。

    返回: 0=上盘赢, 1=走水, 2=下盘赢
    """
    if handicap_line is None or pd.isna(handicap_line):
        return None
    try:
        line = float(handicap_line)
    except:
        return None

    # 让球后的净胜球 = 主队进球 - 客队进球 + 盘口线
    # 注意：盘口线为负(主队让球)时，主队净胜球减少
    # 实际上是: 主队让 line 球，所以 home_adjusted = home_goals + line
    # 如果 line = -1, home 让1球，home_adjusted = home_goals - 1
    adjusted_diff = (home_goals - away_goals) + line

    if adjusted_diff > 0:
        return 0  # 上盘赢（主队方向赢）
    elif adjusted_diff == 0:
        return 1  # 走水
    else:
        return 2  # 下盘赢（客队方向赢）


def main():
    parser = argparse.ArgumentParser(description='T-005 让球胜平负预测')
    parser.add_argument('--n', type=int, default=5, help='预测最近 N 场比赛')
    parser.add_argument('--date', type=str, default=None, help='目标日期 (YYYY-MM-DD)')
    args = parser.parse_args()

    print("=" * 70)
    print("🔮 T-005 让球胜平负预测")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  预测场数: 最近 {args.n} 场")
    if args.date:
        print(f"  目标日期: {args.date}")
    print("=" * 70)

    # Step 1: 加载模型
    model, metadata, elo_snapshot = load_model_artifacts()

    # Step 2: 加载最近比赛
    matches_df = load_recent_matches(n=args.n, target_date=args.date)

    if len(matches_df) == 0:
        print("\n❌ 未找到符合条件的比赛")
        return

    # Step 3: 构建特征矩阵
    print("\n🔧 构建特征矩阵...")
    X = build_feature_matrix(matches_df, metadata, elo_snapshot)
    print(f"  特征矩阵: {X.shape[0]} 场 × {X.shape[1]} 维")

    # Step 4: 预测
    print("\n🎯 运行预测...")
    predictions = model.predict(X)
    probabilities = model.predict_proba(X)

    # Step 5: 展示结果
    print("\n" + "=" * 70)
    print("📋 预测结果")
    print("=" * 70)

    label_names = ['上盘赢', '走水', '下盘赢']
    results = []

    for i, (_, match) in enumerate(matches_df.iterrows()):
        pred_label = int(predictions[i])
        pred_name = label_names[pred_label]
        proba = probabilities[i]

        # 置信度 = 最大预测概率
        confidence = float(np.max(proba))

        # 市场热门方向
        market_favorite = '上盘' if match['hcp_win'] < match['hcp_lose'] else '下盘'

        # 实际结果（优先用 actual_handicap 字段，其次从比分+盘口线计算）
        actual_result = None
        actual_name = '未进行'

        # 方式1: 直接使用 DB 中的 actual_handicap 字段（'胜'/'平'/'负'）
        if pd.notna(match.get('actual_handicap')) and match['actual_handicap'] in ['胜', '平', '负']:
            ah_map = {'胜': 0, '平': 1, '负': 2}
            actual_result = ah_map[match['actual_handicap']]
            actual_name = label_names[actual_result]
        else:
            # 方式2: 从比分 + 盘口线计算
            hg, ag = parse_score(match['actual_score'])
            if hg is not None and ag is not None:
                actual_result = calculate_actual_handicap(hg, ag, match['handicap_line'])
                if actual_result is not None:
                    actual_name = label_names[actual_result]

        # 盘口线描述
        line = match['handicap_line']
        if pd.notna(line):
            try:
                line_val = float(line)
                if line_val < 0:
                    line_desc = f"主让{-line_val:.1f}球"
                elif line_val > 0:
                    line_desc = f"主受让{line_val:.1f}球"
                else:
                    line_desc = "平手"
            except:
                line_desc = str(line)
        else:
            line_desc = "未知"

        correct = '—'
        if actual_result is not None:
            correct = '✅' if actual_result == pred_label else '❌'

        result = {
            'date': match['date'],
            'league': match['league'],
            'home_team': match['home_team'],
            'away_team': match['away_team'],
            'handicap_line': line_desc,
            'hcp_win': match['hcp_win'],
            'hcp_draw': match['hcp_draw'],
            'hcp_lose': match['hcp_lose'],
            'market_favorite': market_favorite,
            'pred_label': pred_label,
            'pred_name': pred_name,
            'confidence': confidence,
            'proba_win': float(proba[0]),
            'proba_draw': float(proba[1]),
            'proba_lose': float(proba[2]),
            'actual_score': match['actual_score'] if pd.notna(match['actual_score']) else '未进行',
            'actual_result': actual_name,
            'correct': correct,
        }
        results.append(result)

        # 打印单场预测
        print(f"\n  ┌─ 比赛 {i+1}: {match['home_team']} vs {match['away_team']}")
        print(f"  │  日期: {match['date']}  |  联赛: {match['league']}")
        print(f"  │  盘口: {line_desc}  |  市场热门: {market_favorite}")
        print(f"  │  让球赔率: 上盘={match['hcp_win']:.2f}  走水={match['hcp_draw']:.2f}  下盘={match['hcp_lose']:.2f}")
        print(f"  │  ───────────────────────────────────────")
        print(f"  │  🎯 模型预测: {pred_name}  (置信度: {confidence*100:.1f}%)")
        print(f"  │  📊 概率分布: 上盘赢={proba[0]*100:.1f}%  走水={proba[1]*100:.1f}%  下盘赢={proba[2]*100:.1f}%")
        if actual_result is not None:
            print(f"  │  ✅ 实际结果: {actual_name}  |  比分: {match['actual_score']}  |  预测{correct}")
        else:
            print(f"  │  ⏳ 实际结果: 比赛未进行或无比分数据")
        print(f"  └─")

    # 汇总
    print("\n" + "=" * 70)
    print("📊 预测汇总")
    print("=" * 70)

    # 预测分布
    pred_counts = {label_names[k]: 0 for k in range(3)}
    for r in results:
        pred_counts[r['pred_name']] += 1
    print(f"\n  预测分布:")
    for name, count in pred_counts.items():
        print(f"    {name}: {count} 场 ({count/len(results)*100:.0f}%)")

    # 平均置信度
    avg_conf = np.mean([r['confidence'] for r in results])
    print(f"\n  平均置信度: {avg_conf*100:.1f}%")

    # 已完成比赛准确率
    completed = [r for r in results if r['correct'] != '—']
    if completed:
        correct_count = sum(1 for r in completed if r['correct'] == '✅')
        print(f"\n  已完成比赛: {len(completed)} 场")
        print(f"  预测正确: {correct_count} 场 ({correct_count/len(completed)*100:.0f}%)")
    else:
        print(f"\n  已完成比赛: 0 场（所有比赛均未进行或无比分数据）")

    # 风险提示
    print(f"\n  ⚠️ 已知盲区提醒:")
    for bs in metadata.get('known_blindspots', []):
        print(f"    - {bs['盲区']} ({bs['严重度']}): {bs.get('召回率', bs.get('准确率差距', ''))}")

    # 保存预测结果
    report_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports',
        f't005_prediction_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    )
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'model_version': metadata['version'],
            'n_matches': len(results),
            'predictions': results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n💾 预测结果已保存: {report_path}")


if __name__ == "__main__":
    main()
