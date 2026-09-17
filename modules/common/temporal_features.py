"""通用赔率时序特征提取模块

负责从赔率时序数据中提取特征，支持模型训练。
与联赛无关，通过 data_loader 提供的统一接口工作。

特征清单：
- 开盘/收盘赔率：12个特征
- 赔率变化率：12个特征
- 趋势特征：24个特征
- 隐含概率：12个特征
- V型模式检测：12个特征
- 优先级评分：9个特征
- 总计：81个赔率时序特征

核心功能：
- detect_v_pattern: 检测V型反转模式
- calculate_time_weighted_trend: 计算时间加权趋势
- extract_wdl_features: 提取胜平负赔率特征
- extract_handicap_features: 提取让球赔率特征
- extract_total_goals_features: 提取总进球赔率特征
- extract_score_features: 提取比分赔率特征
- build_all_features: 构建单场比赛所有特征
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

# 导入通用数据加载函数
from .data_loader import (
    load_wdl_history,
    load_handicap_history,
    load_total_goals_history,
    load_score_history,
    get_db_connection
)


def detect_v_pattern(values: np.ndarray) -> Optional[Dict]:
    """
    检测V型反转模式

    Args:
        values: 赔率值序列

    Returns:
        Dict: V型模式信息（类型、位置、值），无则返回None
    """
    if len(values) < 3:
        return None

    max_idx = np.argmax(values)
    min_idx = np.argmin(values)

    # 检测倒V型（先降后升的反转）
    if min_idx > 0 and min_idx < len(values) - 1:
        before_min = values[min_idx - 1]
        after_min = values[min_idx + 1]
        if before_min > values[min_idx] and after_min > values[min_idx]:
            recovery = values[-1] - values[min_idx]
            if recovery > 0:
                return {
                    'type': 'reverse',
                    'min_index': min_idx,
                    'min_value': float(values[min_idx]),
                    'recovery': float(recovery)
                }

    # 检测V型（先升后降）
    if max_idx > 0 and max_idx < len(values) - 1:
        before_max = values[max_idx - 1]
        after_max = values[max_idx + 1]
        if before_max < values[max_idx] and after_max < values[max_idx]:
            return {
                'type': 'v_shape',
                'max_index': max_idx,
                'max_value': float(values[max_idx])
            }

    return None


def calculate_time_weighted_trend(odds_history: List[float]) -> Dict:
    """
    计算时间加权趋势

    Args:
        odds_history: 赔率历史序列

    Returns:
        Dict: 趋势分析结果
    """
    if len(odds_history) < 2:
        return {
            'consecutive_decreases': 0,
            'total_decrease_count': 0,
            'time_weighted_change': 0,
            'trend_strength': 0,
            'total_change': 0
        }

    consecutive_decreases = 0
    total_decrease_count = 0
    previous_odds = None
    total_change = 0
    weighted_sum = 0
    weight_sum = 0

    for i, odds in enumerate(odds_history):
        if previous_odds is not None:
            change = previous_odds - odds
            total_change += change

            # 时间权重：越靠近比赛时间权重越大
            weight = (i + 1) / len(odds_history)
            weighted_sum += change * weight
            weight_sum += weight

            if change > 0:
                consecutive_decreases += 1
                total_decrease_count += 1
            elif change < 0:
                consecutive_decreases = 0

        previous_odds = odds

    time_weighted_change = weighted_sum / weight_sum if weight_sum > 0 else 0
    total_periods = len(odds_history) - 1
    trend_strength = total_decrease_count / total_periods if total_periods > 0 else 0

    return {
        'consecutive_decreases': consecutive_decreases,
        'total_decrease_count': total_decrease_count,
        'time_weighted_change': time_weighted_change,
        'trend_strength': trend_strength,
        'total_change': total_change
    }


def calculate_priority_score(change: float, odds_level: float, v_pattern: Optional[Dict] = None) -> float:
    """
    计算赔率优先级评分

    Args:
        change: 赔率变化值（初盘-终盘）
        odds_level: 初始赔率水平
        v_pattern: V型模式信息

    Returns:
        float: 优先级评分
    """
    priority_score = 0

    if change > 0:
        priority_score = (change / odds_level) * (10 / odds_level) + (10 / odds_level)
        if v_pattern and v_pattern.get('type') == 'reverse':
            priority_score *= 1.3
    elif change == 0:
        priority_score = 10 / odds_level
    elif abs(change) < 0.2:
        priority_score = 10 / odds_level * 0.5 + (0.1 / odds_level)

    return priority_score


def calculate_implied_probability(win_odds: float, draw_odds: float, lose_odds: float) -> Tuple[float, float, float]:
    """
    计算归一化隐含概率

    Args:
        win_odds: 胜赔率
        draw_odds: 平赔率
        lose_odds: 负赔率

    Returns:
        Tuple[float, float, float]: 胜、平、负的归一化隐含概率
    """
    if win_odds > 0 and draw_odds > 0 and lose_odds > 0:
        total_implied = 1/win_odds + 1/draw_odds + 1/lose_odds
        if total_implied > 0:
            return (
                (1/win_odds) / total_implied,
                (1/draw_odds) / total_implied,
                (1/lose_odds) / total_implied
            )

    return 1/3, 1/3, 1/3


def extract_wdl_features(wdl_data: pd.DataFrame) -> Tuple[Dict, Dict]:
    """提取胜平负赔率特征"""
    features = {}
    feature_info = {}

    if len(wdl_data) < 2:
        return features, feature_info

    first = wdl_data.iloc[0]
    last = wdl_data.iloc[-1]

    for col in ['win_a', 'draw', 'win_b']:
        if col not in wdl_data.columns:
            continue

        values = wdl_data[col].values
        change = first[col] - last[col]
        percentage_change = (change / first[col]) * 100 if first[col] > 0 else 0
        v_pattern = detect_v_pattern(values)
        trend = calculate_time_weighted_trend(values)
        priority_score = calculate_priority_score(change, first[col], v_pattern)

        features[f'wdl_{col}_open'] = float(first[col])
        features[f'wdl_{col}_close'] = float(last[col])
        features[f'wdl_{col}_change'] = change
        features[f'wdl_{col}_percentage_change'] = percentage_change
        features[f'wdl_{col}_trend_strength'] = trend['trend_strength']
        features[f'wdl_{col}_consecutive_decreases'] = trend['consecutive_decreases']
        features[f'wdl_{col}_time_weighted_change'] = trend['time_weighted_change']
        features[f'wdl_{col}_priority_score'] = priority_score
        features[f'wdl_{col}_has_v_pattern'] = 1 if v_pattern else 0
        features[f'wdl_{col}_v_pattern_type'] = v_pattern['type'] if v_pattern else 'none'

        feature_info[f'wdl_{col}_open'] = {'description': f'{col}开盘赔率', 'source': 'wdl_history', 'calculation': '第一个时间点赔率'}
        feature_info[f'wdl_{col}_close'] = {'description': f'{col}收盘赔率', 'source': 'wdl_history', 'calculation': '最后一个时间点赔率'}
        feature_info[f'wdl_{col}_change'] = {'description': f'{col}赔率变化值', 'source': 'wdl_history', 'calculation': '初盘 - 终盘'}
        feature_info[f'wdl_{col}_percentage_change'] = {'description': f'{col}赔率变化百分比', 'source': 'wdl_history', 'calculation': '(初盘-终盘)/初盘*100'}
        feature_info[f'wdl_{col}_trend_strength'] = {'description': f'{col}赔率下降趋势强度', 'source': 'wdl_history', 'calculation': '下降周期占比'}
        feature_info[f'wdl_{col}_consecutive_decreases'] = {'description': f'{col}赔率连续下降次数', 'source': 'wdl_history', 'calculation': '连续下降计数'}
        feature_info[f'wdl_{col}_time_weighted_change'] = {'description': f'{col}赔率时间加权变化', 'source': 'wdl_history', 'calculation': '时间加权平均变化'}
        feature_info[f'wdl_{col}_priority_score'] = {'description': f'{col}赔率优先级分数', 'source': 'wdl_history', 'calculation': '变化率+赔率水平+走势模式'}
        feature_info[f'wdl_{col}_has_v_pattern'] = {'description': f'{col}赔率是否有V型走势', 'source': 'wdl_history', 'calculation': 'V型/倒V型检测'}

    # 隐含概率
    imp_home, imp_draw, imp_away = calculate_implied_probability(last['win_a'], last['draw'], last['win_b'])
    features['wdl_implied_prob_home'] = imp_home
    features['wdl_implied_prob_draw'] = imp_draw
    features['wdl_implied_prob_away'] = imp_away

    feature_info['wdl_implied_prob_home'] = {'description': '主胜隐含概率(归一化)', 'source': 'wdl_history', 'calculation': '1/主胜赔率 / (1/主胜+1/平局+1/客胜)'}
    feature_info['wdl_implied_prob_draw'] = {'description': '平局隐含概率(归一化)', 'source': 'wdl_history', 'calculation': '1/平局赔率 / (1/主胜+1/平局+1/客胜)'}
    feature_info['wdl_implied_prob_away'] = {'description': '客胜隐含概率(归一化)', 'source': 'wdl_history', 'calculation': '1/客胜赔率 / (1/主胜+1/平局+1/客胜)'}

    # 市场信号评分
    win_a_change = features.get('wdl_win_a_change', 0)
    win_b_change = features.get('wdl_win_b_change', 0)
    draw_change = features.get('wdl_draw_change', 0)

    prob_weight = 0.7
    momentum_weight = 0.3
    total_change = abs(win_a_change) + abs(win_b_change) + abs(draw_change) + 0.01
    home_score = imp_home * prob_weight + (1 + win_a_change / total_change) * momentum_weight
    draw_score = imp_draw * prob_weight + (1 + draw_change / total_change) * momentum_weight
    away_score = imp_away * prob_weight + (1 + win_b_change / total_change) * momentum_weight

    if win_a_change > 0:
        home_score += win_a_change * 0.1
    if win_b_change > 0:
        away_score += win_b_change * 0.1
    if draw_change > 0:
        draw_score += draw_change * 0.1

    scores = [('away', away_score, 0), ('draw', draw_score, 1), ('home', home_score, 2)]
    scores.sort(key=lambda x: x[1], reverse=True)
    features['wdl_market_signal'] = scores[0][2]
    features['wdl_home_score'] = home_score
    features['wdl_draw_score'] = draw_score
    features['wdl_away_score'] = away_score

    feature_info['wdl_market_signal'] = {'description': '市场预期信号(0=客胜,1=平局,2=主胜)', 'source': 'wdl_history', 'calculation': '融合隐含概率(70%)+赔率变化趋势(30%)的加权评分'}
    feature_info['wdl_home_score'] = {'description': '主胜综合评分', 'source': 'wdl_history', 'calculation': '隐含概率*0.7 + 趋势因子*0.3 + 赔率下降加成'}

    # 置信度
    max_change = max(abs(win_a_change), abs(win_b_change), abs(draw_change))
    positive_count = sum(1 for c in [win_a_change, win_b_change, draw_change] if c > 0)
    confidence = min(100, len(wdl_data) * 5 + max_change * 10 + positive_count * 5)
    max_prob = max(imp_home, imp_draw, imp_away)
    confidence += max_prob * 20
    features['wdl_odds_confidence'] = min(100, confidence)
    feature_info['wdl_odds_confidence'] = {'description': '赔率分析置信度', 'source': 'wdl_history', 'calculation': '数据点数*5 + 最大变化*10 + 正变化数量*5 + 最大隐含概率*20'}

    return features, feature_info


def extract_handicap_features(hcp_data: pd.DataFrame) -> Tuple[Dict, Dict]:
    """提取让球赔率特征"""
    features = {}
    feature_info = {}

    if len(hcp_data) < 2:
        return features, feature_info

    first = hcp_data.iloc[0]
    last = hcp_data.iloc[-1]

    for col in ['hcp_win', 'hcp_draw', 'hcp_lose']:
        if col not in hcp_data.columns:
            continue

        values = hcp_data[col].values
        change = first[col] - last[col]
        percentage_change = (change / first[col]) * 100 if first[col] > 0 else 0
        v_pattern = detect_v_pattern(values)
        trend = calculate_time_weighted_trend(values)
        priority_score = calculate_priority_score(change, first[col], v_pattern)

        features[f'hcp_{col}_open'] = float(first[col])
        features[f'hcp_{col}_close'] = float(last[col])
        features[f'hcp_{col}_change'] = change
        features[f'hcp_{col}_percentage_change'] = percentage_change
        features[f'hcp_{col}_trend_strength'] = trend['trend_strength']
        features[f'hcp_{col}_consecutive_decreases'] = trend['consecutive_decreases']
        features[f'hcp_{col}_priority_score'] = priority_score
        features[f'hcp_{col}_has_v_pattern'] = 1 if v_pattern else 0

        feature_info[f'hcp_{col}_open'] = {'description': f'{col}开盘赔率', 'source': 'handicap_history', 'calculation': '第一个时间点赔率'}
        feature_info[f'hcp_{col}_change'] = {'description': f'{col}让球赔率变化值', 'source': 'handicap_history', 'calculation': '初盘 - 终盘'}

    # 让球预测
    hcp_win_change = features.get('hcp_hcp_win_change', 0)
    hcp_draw_change = features.get('hcp_hcp_draw_change', 0)
    hcp_lose_change = features.get('hcp_hcp_lose_change', 0)

    positive_changes = []
    if hcp_win_change > 0:
        positive_changes.append(('hcp_win', hcp_win_change, 1))
    if hcp_draw_change > 0:
        positive_changes.append(('hcp_draw', hcp_draw_change, 0))
    if hcp_lose_change > 0:
        positive_changes.append(('hcp_lose', hcp_lose_change, -1))

    if positive_changes:
        positive_changes.sort(key=lambda x: x[1], reverse=True)
        features['hcp_prediction'] = positive_changes[0][2]
    else:
        features['hcp_prediction'] = 0

    feature_info['hcp_prediction'] = {'description': '让球预测(-1=让负,0=让平,1=让胜)', 'source': 'handicap_history', 'calculation': '让球赔率下降幅度最大项'}

    return features, feature_info


def extract_total_goals_features(tg_data: pd.DataFrame) -> Tuple[Dict, Dict]:
    """提取总进球赔率特征"""
    features = {}
    feature_info = {}

    if len(tg_data) < 2:
        return features, feature_info

    first = tg_data.iloc[0]
    last = tg_data.iloc[-1]

    goal_cols = ['0', '1', '2', '3', '4', '5', '6', '7+']
    changes = []

    for col in goal_cols:
        if col not in tg_data.columns:
            continue

        values = tg_data[col].values
        if np.any(np.isnan(values)):
            continue

        change = first[col] - last[col]
        percentage_change = (change / first[col]) * 100 if first[col] > 0 else 0
        v_pattern = detect_v_pattern(values)
        priority_score = calculate_priority_score(change, first[col], v_pattern)

        features[f'tg_{col}_open'] = float(first[col])
        features[f'tg_{col}_close'] = float(last[col])
        features[f'tg_{col}_change'] = change
        features[f'tg_{col}_percentage_change'] = percentage_change
        features[f'tg_{col}_priority_score'] = priority_score
        features[f'tg_{col}_has_v_pattern'] = 1 if v_pattern else 0

        feature_info[f'tg_{col}_change'] = {'description': f'{col}球赔率变化值', 'source': 'total_goals_history', 'calculation': '初盘 - 终盘'}

        changes.append({'goals': int(col) if col != '7+' else 7, 'change': change, 'priority_score': priority_score})

    # 目标进球数
    changes.sort(key=lambda x: x['priority_score'], reverse=True)
    target_goals = [c['goals'] for c in changes if c['priority_score'] > 0.3][:4]

    features['tg_target_goals_count'] = len(target_goals)
    for i, goals in enumerate(target_goals[:4]):
        features[f'tg_target_goals_{i+1}'] = goals

    feature_info['tg_target_goals_count'] = {'description': '目标进球数数量', 'source': 'total_goals_history', 'calculation': '优先级>0.3的进球数个数'}

    # 高低进球指示
    low_goals_decrease = any(c['goals'] <= 3 and c['change'] > 0.2 for c in changes)
    high_goals_decrease = any(c['goals'] >= 4 and c['change'] > 0.3 for c in changes)

    features['tg_low_goals_decrease'] = 1 if low_goals_decrease else 0
    features['tg_high_goals_decrease'] = 1 if high_goals_decrease else 0

    feature_info['tg_low_goals_decrease'] = {'description': '低进球数赔率是否下降', 'source': 'total_goals_history', 'calculation': '0-3球赔率下降>0.2'}
    feature_info['tg_high_goals_decrease'] = {'description': '高进球数赔率是否下降', 'source': 'total_goals_history', 'calculation': '4+球赔率下降>0.3'}

    return features, feature_info


def extract_score_features(score_data: pd.DataFrame, target_goals: List[int] = None) -> Tuple[Dict, Dict]:
    """提取比分赔率特征"""
    features = {}
    feature_info = {}

    if len(score_data) < 2:
        return features, feature_info

    if target_goals is None:
        target_goals = []

    timestamps = sorted(score_data['timestamp'].unique())
    if len(timestamps) < 2:
        return features, feature_info

    first_timestamp = timestamps[0]
    last_timestamp = timestamps[-1]

    first_scores = score_data[score_data['timestamp'] == first_timestamp]
    last_scores = score_data[score_data['timestamp'] == last_timestamp]

    first_odds_dict = dict(zip(first_scores['score'], first_scores['odds']))
    last_odds_dict = dict(zip(last_scores['score'], last_scores['odds']))

    all_scores = set(first_scores['score']) & set(last_scores['score'])
    score_changes = []

    for score in all_scores:
        if '其它' in score:
            continue

        first_odds = first_odds_dict.get(score)
        last_odds = last_odds_dict.get(score)

        if first_odds is None or last_odds is None:
            continue
        if np.isnan(first_odds) or np.isnan(last_odds):
            continue

        change = first_odds - last_odds
        percentage_change = (change / first_odds) * 100 if first_odds > 0 else 0

        score_parts = score.split(':')
        if len(score_parts) != 2:
            continue

        try:
            home, away = int(score_parts[0]), int(score_parts[1])
        except (ValueError, TypeError):
            continue
        total = home + away
        is_target = len(target_goals) == 0 or total in target_goals

        all_odds = []
        for ts in timestamps:
            ts_scores = score_data[score_data['timestamp'] == ts]
            score_row = ts_scores[ts_scores['score'] == score]
            if len(score_row) > 0:
                all_odds.append(score_row['odds'].values[0])

        trend = calculate_time_weighted_trend(all_odds)
        v_pattern = detect_v_pattern(np.array(all_odds))
        priority_score = calculate_priority_score(change, first_odds, v_pattern)

        score_changes.append({
            'score': score, 'total': total, 'home': home, 'away': away,
            'change': change, 'percentage_change': percentage_change,
            'priority_score': priority_score, 'is_target': is_target,
            'trend_strength': trend['trend_strength'],
            'consecutive_decreases': trend['consecutive_decreases']
        })

    if score_changes:
        score_changes.sort(key=lambda x: x['priority_score'], reverse=True)
        top_scores = score_changes[:5]

        features['score_target_count'] = len(top_scores)
        for i, sc in enumerate(top_scores):
            features[f'score_target_{i+1}_change'] = sc['change']
            features[f'score_target_{i+1}_priority'] = sc['priority_score']
            features[f'score_target_{i+1}_total'] = sc['total']
            features[f'score_target_{i+1}_diff'] = sc['home'] - sc['away']
            feature_info[f'score_target_{i+1}_change'] = {'description': f'目标比分{i+1}赔率变化', 'source': 'score_history', 'calculation': '初盘 - 终盘'}

        home_win_scores = [s for s in score_changes if s['home'] > s['away'] and s['change'] > 0]
        draw_scores = [s for s in score_changes if s['home'] == s['away']]
        away_win_scores = [s for s in score_changes if s['home'] < s['away'] and s['change'] > 0]

        features['score_home_win_decrease_count'] = len(home_win_scores)
        features['score_draw_count'] = len(draw_scores)
        features['score_away_win_decrease_count'] = len(away_win_scores)

        feature_info['score_home_win_decrease_count'] = {'description': '主胜比分赔率下降数量', 'source': 'score_history', 'calculation': '主胜比分中赔率下降的数量'}

    return features, feature_info


def build_all_features(match_id: str, conn=None) -> Tuple[Dict, Dict]:
    """构建单场比赛所有赔率时序特征"""
    match_features = {}
    all_feature_info = {}
    use_external_conn = conn is not None

    try:
        wdl_history = load_wdl_history(match_id, conn)
        if len(wdl_history) >= 2:
            wdl_features, wdl_info = extract_wdl_features(wdl_history)
            match_features.update(wdl_features)
            all_feature_info.update(wdl_info)

        handicap_history = load_handicap_history(match_id, conn)
        if len(handicap_history) >= 2:
            hcp_features, hcp_info = extract_handicap_features(handicap_history)
            match_features.update(hcp_features)
            all_feature_info.update(hcp_info)

        total_goals_history = load_total_goals_history(match_id, conn)
        target_goals = []
        if len(total_goals_history) >= 2:
            tg_features, tg_info = extract_total_goals_features(total_goals_history)
            match_features.update(tg_features)
            all_feature_info.update(tg_info)
            target_goals = [match_features.get(f'tg_target_goals_{i}') for i in range(1, 5)
                           if match_features.get(f'tg_target_goals_{i}') is not None]
            target_goals = [g for g in target_goals if g is not None]

        score_history = load_score_history(match_id, conn)
        if len(score_history) >= 2:
            score_features, score_info = extract_score_features(score_history, target_goals)
            match_features.update(score_features)
            all_feature_info.update(score_info)

    finally:
        if not use_external_conn and conn is not None:
            conn.close()

    return match_features, all_feature_info


def build_features_for_matches(match_ids: List[str]) -> Tuple[pd.DataFrame, Dict]:
    """批量构建多场比赛的特征"""
    from modules.common.data_loader import get_db_connection, ODDS_DB_PATH

    all_features = []
    all_feature_info = {}

    conn = get_db_connection(ODDS_DB_PATH)
    try:
        for i, match_id in enumerate(match_ids):
            features, info = build_all_features(match_id, conn)
            all_features.append(features)
            all_feature_info.update(info)
            if (i + 1) % 100 == 0:
                print(f"  已处理 {i+1}/{len(match_ids)} 场...")
    finally:
        conn.close()

    features_df = pd.DataFrame(all_features)

    # 丢弃非数值列（_type, _signal 等字符串分类特征）
    drop_cols = [c for c in features_df.columns
                 if c.endswith('_type') or c.endswith('_signal')]
    if drop_cols:
        features_df = features_df.drop(columns=drop_cols)

    # 填充缺失值为 0
    for col in features_df.columns:
        features_df[col] = pd.to_numeric(features_df[col], errors='coerce').fillna(0)

    return features_df, all_feature_info