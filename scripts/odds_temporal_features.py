import sqlite3
import pandas as pd
import numpy as np
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "five_leagues.db")
ODDS_DB_PATH = os.path.join(PROJECT_DIR, "data", "odds.db")


def load_odds_database():
    conn = sqlite3.connect(ODDS_DB_PATH)
    return conn


def load_wdl_history(match_id, conn):
    query = """
        SELECT timestamp, win_a, draw, win_b 
        FROM wdl_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = pd.read_sql(query, conn, params=(match_id,))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


def load_handicap_history(match_id, conn):
    query = """
        SELECT timestamp, hcp_win, hcp_draw, hcp_lose 
        FROM handicap_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = pd.read_sql(query, conn, params=(match_id,))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


def load_total_goals_history(match_id, conn):
    query = """
        SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus 
        FROM total_goals_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = pd.read_sql(query, conn, params=(match_id,))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.columns = ['timestamp', '0', '1', '2', '3', '4', '5', '6', '7+']
    return df


def load_score_history(match_id, conn):
    query = """
        SELECT timestamp, score, odds 
        FROM score_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = pd.read_sql(query, conn, params=(match_id,))
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


def detect_v_pattern(values):
    if len(values) < 3:
        return None

    max_idx = np.argmax(values)
    min_idx = np.argmin(values)

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


def calculate_time_weighted_trend(odds_history):
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


def calculate_priority_score(change, odds_level, v_pattern=None):
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


def analyze_wdl_trend(wdl_history):
    features = {}
    feature_info = {}

    if len(wdl_history) < 2:
        return features, feature_info

    first = wdl_history.iloc[0]
    last = wdl_history.iloc[-1]

    for col in ['win_a', 'draw', 'win_b']:
        if col not in wdl_history.columns:
            continue

        values = wdl_history[col].values
        change = first[col] - last[col]
        percentage_change = (change / first[col]) * 100 if first[col] > 0 else 0
        v_pattern = detect_v_pattern(values)
        trend = calculate_time_weighted_trend(values)
        priority_score = calculate_priority_score(change, first[col], v_pattern)

        features[f'wdl_{col}_change'] = change
        features[f'wdl_{col}_percentage_change'] = percentage_change
        features[f'wdl_{col}_trend_strength'] = trend['trend_strength']
        features[f'wdl_{col}_consecutive_decreases'] = trend['consecutive_decreases']
        features[f'wdl_{col}_time_weighted_change'] = trend['time_weighted_change']
        features[f'wdl_{col}_priority_score'] = priority_score
        features[f'wdl_{col}_has_v_pattern'] = 1 if v_pattern else 0
        features[f'wdl_{col}_v_pattern_type'] = v_pattern['type'] if v_pattern else 'none'

        feature_info[f'wdl_{col}_change'] = {
            'description': f'{col}赔率变化值',
            'source': 'wdl_history',
            'calculation': '初盘 - 终盘'
        }
        feature_info[f'wdl_{col}_percentage_change'] = {
            'description': f'{col}赔率变化百分比',
            'source': 'wdl_history',
            'calculation': '(初盘-终盘)/初盘*100'
        }
        feature_info[f'wdl_{col}_trend_strength'] = {
            'description': f'{col}赔率下降趋势强度',
            'source': 'wdl_history',
            'calculation': '下降周期占比'
        }
        feature_info[f'wdl_{col}_consecutive_decreases'] = {
            'description': f'{col}赔率连续下降次数',
            'source': 'wdl_history',
            'calculation': '连续下降计数'
        }
        feature_info[f'wdl_{col}_time_weighted_change'] = {
            'description': f'{col}赔率时间加权变化',
            'source': 'wdl_history',
            'calculation': '时间加权平均变化'
        }
        feature_info[f'wdl_{col}_priority_score'] = {
            'description': f'{col}赔率优先级分数',
            'source': 'wdl_history',
            'calculation': '变化率+赔率水平+走势模式'
        }
        feature_info[f'wdl_{col}_has_v_pattern'] = {
            'description': f'{col}赔率是否有V型走势',
            'source': 'wdl_history',
            'calculation': 'V型/倒V型检测'
        }

    win_a_change = features.get('wdl_win_a_change', 0)
    win_b_change = features.get('wdl_win_b_change', 0)
    draw_change = features.get('wdl_draw_change', 0)

    if last['win_a'] > 0 and last['draw'] > 0 and last['win_b'] > 0:
        total_implied = 1/last['win_a'] + 1/last['draw'] + 1/last['win_b']
        if total_implied > 0:
            imp_home = (1/last['win_a']) / total_implied
            imp_draw = (1/last['draw']) / total_implied
            imp_away = (1/last['win_b']) / total_implied
        else:
            imp_home = imp_draw = imp_away = 1/3
    else:
        imp_home = imp_draw = imp_away = 1/3

    features['wdl_implied_prob_home'] = imp_home
    features['wdl_implied_prob_draw'] = imp_draw
    features['wdl_implied_prob_away'] = imp_away

    feature_info['wdl_implied_prob_home'] = {
        'description': '主胜隐含概率(归一化)',
        'source': 'wdl_history',
        'calculation': '1/主胜赔率 / (1/主胜+1/平局+1/客胜)'
    }
    feature_info['wdl_implied_prob_draw'] = {
        'description': '平局隐含概率(归一化)',
        'source': 'wdl_history',
        'calculation': '1/平局赔率 / (1/主胜+1/平局+1/客胜)'
    }
    feature_info['wdl_implied_prob_away'] = {
        'description': '客胜隐含概率(归一化)',
        'source': 'wdl_history',
        'calculation': '1/客胜赔率 / (1/主胜+1/平局+1/客胜)'
    }

    prob_weight = 0.7
    momentum_weight = 0.3

    total_change = abs(win_a_change) + abs(win_b_change) + abs(draw_change) + 0.01
    home_score = imp_home * prob_weight + (1 + win_a_change / total_change) * momentum_weight if total_change > 0 else imp_home * prob_weight
    draw_score = imp_draw * prob_weight + (1 + draw_change / total_change) * momentum_weight if total_change > 0 else imp_draw * prob_weight
    away_score = imp_away * prob_weight + (1 + win_b_change / total_change) * momentum_weight if total_change > 0 else imp_away * prob_weight

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

    feature_info['wdl_market_signal'] = {
        'description': '市场预期信号(0=客胜,1=平局,2=主胜)',
        'source': 'wdl_history',
        'calculation': '融合隐含概率(70%)+赔率变化趋势(30%)的加权评分'
    }
    feature_info['wdl_home_score'] = {
        'description': '主胜综合评分',
        'source': 'wdl_history',
        'calculation': '隐含概率*0.7 + 趋势因子*0.3 + 赔率下降加成'
    }
    feature_info['wdl_draw_score'] = {
        'description': '平局综合评分',
        'source': 'wdl_history',
        'calculation': '隐含概率*0.7 + 趋势因子*0.3 + 赔率下降加成'
    }
    feature_info['wdl_away_score'] = {
        'description': '客胜综合评分',
        'source': 'wdl_history',
        'calculation': '隐含概率*0.7 + 趋势因子*0.3 + 赔率下降加成'
    }

    max_change = max(abs(win_a_change), abs(win_b_change), abs(draw_change))
    positive_count = sum(1 for c in [win_a_change, win_b_change, draw_change] if c > 0)
    confidence = min(100, len(wdl_history) * 5 + max_change * 10 + positive_count * 5)
    max_prob = max(imp_home, imp_draw, imp_away)
    confidence += max_prob * 20
    features['wdl_odds_confidence'] = min(100, confidence)
    
    feature_info['wdl_odds_confidence'] = {
        'description': '赔率分析置信度',
        'source': 'wdl_history',
        'calculation': '数据点数*5 + 最大变化*10 + 正变化数量*5 + 最大隐含概率*20'
    }

    return features, feature_info


def analyze_handicap_trend(handicap_history):
    features = {}
    feature_info = {}

    if len(handicap_history) < 2:
        return features, feature_info

    first = handicap_history.iloc[0]
    last = handicap_history.iloc[-1]

    for col in ['hcp_win', 'hcp_draw', 'hcp_lose']:
        if col not in handicap_history.columns:
            continue

        values = handicap_history[col].values
        change = first[col] - last[col]
        percentage_change = (change / first[col]) * 100 if first[col] > 0 else 0
        v_pattern = detect_v_pattern(values)
        trend = calculate_time_weighted_trend(values)
        priority_score = calculate_priority_score(change, first[col], v_pattern)

        features[f'hcp_{col}_change'] = change
        features[f'hcp_{col}_percentage_change'] = percentage_change
        features[f'hcp_{col}_trend_strength'] = trend['trend_strength']
        features[f'hcp_{col}_consecutive_decreases'] = trend['consecutive_decreases']
        features[f'hcp_{col}_priority_score'] = priority_score
        features[f'hcp_{col}_has_v_pattern'] = 1 if v_pattern else 0

        feature_info[f'hcp_{col}_change'] = {
            'description': f'{col}让球赔率变化值',
            'source': 'handicap_history',
            'calculation': '初盘 - 终盘'
        }
        feature_info[f'hcp_{col}_percentage_change'] = {
            'description': f'{col}让球赔率变化百分比',
            'source': 'handicap_history',
            'calculation': '(初盘-终盘)/初盘*100'
        }
        feature_info[f'hcp_{col}_trend_strength'] = {
            'description': f'{col}让球赔率下降趋势强度',
            'source': 'handicap_history',
            'calculation': '下降周期占比'
        }
        feature_info[f'hcp_{col}_priority_score'] = {
            'description': f'{col}让球赔率优先级分数',
            'source': 'handicap_history',
            'calculation': '变化率+赔率水平+走势模式'
        }

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
        negative_changes = []
        if hcp_win_change < 0:
            negative_changes.append(('hcp_win', hcp_win_change, 1))
        if hcp_draw_change < 0:
            negative_changes.append(('hcp_draw', hcp_draw_change, 0))
        if hcp_lose_change < 0:
            negative_changes.append(('hcp_lose', hcp_lose_change, -1))

        if negative_changes:
            negative_changes.sort(key=lambda x: x[1])
            features['hcp_prediction'] = negative_changes[0][2]
        else:
            features['hcp_prediction'] = 0

    feature_info['hcp_prediction'] = {
        'description': '让球预测(-1=让负,0=让平,1=让胜)',
        'source': 'handicap_history',
        'calculation': '让球赔率下降幅度最大项(优先)或上升幅度最大项'
    }

    return features, feature_info


def analyze_total_goals_trend(total_goals_history):
    features = {}
    feature_info = {}

    if len(total_goals_history) < 2:
        return features, feature_info

    first = total_goals_history.iloc[0]
    last = total_goals_history.iloc[-1]

    goal_cols = ['0', '1', '2', '3', '4', '5', '6', '7+']

    target_goals = []
    changes = []

    for col in goal_cols:
        if col not in total_goals_history.columns:
            continue

        values = total_goals_history[col].values
        if np.any(np.isnan(values)):
            continue

        change = first[col] - last[col]
        percentage_change = (change / first[col]) * 100 if first[col] > 0 else 0
        v_pattern = detect_v_pattern(values)
        priority_score = calculate_priority_score(change, first[col], v_pattern)

        features[f'tg_{col}_change'] = change
        features[f'tg_{col}_percentage_change'] = percentage_change
        features[f'tg_{col}_priority_score'] = priority_score
        features[f'tg_{col}_has_v_pattern'] = 1 if v_pattern else 0

        feature_info[f'tg_{col}_change'] = {
            'description': f'{col}球赔率变化值',
            'source': 'total_goals_history',
            'calculation': '初盘 - 终盘'
        }
        feature_info[f'tg_{col}_priority_score'] = {
            'description': f'{col}球赔率优先级分数',
            'source': 'total_goals_history',
            'calculation': '变化率+赔率水平+走势模式'
        }

        changes.append({
            'goals': int(col) if col != '7+' else 7,
            'change': change,
            'priority_score': priority_score
        })

    changes.sort(key=lambda x: x['priority_score'], reverse=True)
    target_goals = [c['goals'] for c in changes if c['priority_score'] > 0.3][:4]

    features['tg_target_goals_count'] = len(target_goals)
    for i, goals in enumerate(target_goals[:4]):
        features[f'tg_target_goals_{i+1}'] = goals

    feature_info['tg_target_goals_count'] = {
        'description': '目标进球数数量',
        'source': 'total_goals_history',
        'calculation': '优先级>0.3的进球数个数'
    }

    low_goals_decrease = any(c['goals'] <= 3 and c['change'] > 0.2 for c in changes)
    high_goals_decrease = any(c['goals'] >= 4 and c['change'] > 0.3 for c in changes)

    features['tg_low_goals_decrease'] = 1 if low_goals_decrease else 0
    features['tg_high_goals_decrease'] = 1 if high_goals_decrease else 0

    feature_info['tg_low_goals_decrease'] = {
        'description': '低进球数赔率是否下降',
        'source': 'total_goals_history',
        'calculation': '0-3球赔率下降>0.2'
    }
    feature_info['tg_high_goals_decrease'] = {
        'description': '高进球数赔率是否下降',
        'source': 'total_goals_history',
        'calculation': '4+球赔率下降>0.3'
    }

    return features, feature_info


def analyze_score_trend(score_history, target_goals=None):
    features = {}
    feature_info = {}

    if len(score_history) < 2:
        return features, feature_info

    if target_goals is None:
        target_goals = []

    timestamps = sorted(score_history['timestamp'].unique())
    if len(timestamps) < 2:
        return features, feature_info

    first_timestamp = timestamps[0]
    last_timestamp = timestamps[-1]

    first_scores = score_history[score_history['timestamp'] == first_timestamp]
    last_scores = score_history[score_history['timestamp'] == last_timestamp]

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

        home, away = int(score_parts[0]), int(score_parts[1])
        total = home + away
        is_target = len(target_goals) == 0 or total in target_goals

        all_odds = []
        for ts in timestamps:
            ts_scores = score_history[score_history['timestamp'] == ts]
            score_row = ts_scores[ts_scores['score'] == score]
            if len(score_row) > 0:
                all_odds.append(score_row['odds'].values[0])

        trend = calculate_time_weighted_trend(all_odds)
        v_pattern = detect_v_pattern(np.array(all_odds))
        priority_score = calculate_priority_score(change, first_odds, v_pattern)

        score_changes.append({
            'score': score,
            'total': total,
            'home': home,
            'away': away,
            'change': change,
            'percentage_change': percentage_change,
            'priority_score': priority_score,
            'is_target': is_target,
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

            feature_info[f'score_target_{i+1}_change'] = {
                'description': f'目标比分{i+1}赔率变化',
                'source': 'score_history',
                'calculation': '初盘 - 终盘'
            }
            feature_info[f'score_target_{i+1}_priority'] = {
                'description': f'目标比分{i+1}优先级',
                'source': 'score_history',
                'calculation': '变化率+赔率水平'
            }

        home_win_scores = [s for s in score_changes if s['home'] > s['away'] and s['change'] > 0]
        draw_scores = [s for s in score_changes if s['home'] == s['away']]
        away_win_scores = [s for s in score_changes if s['home'] < s['away'] and s['change'] > 0]

        features['score_home_win_decrease_count'] = len(home_win_scores)
        features['score_draw_count'] = len(draw_scores)
        features['score_away_win_decrease_count'] = len(away_win_scores)

        feature_info['score_home_win_decrease_count'] = {
            'description': '主胜比分赔率下降数量',
            'source': 'score_history',
            'calculation': '主胜比分中赔率下降的数量'
        }
        feature_info['score_draw_count'] = {
            'description': '平局比分数量',
            'source': 'score_history',
            'calculation': '平局比分记录数'
        }
        feature_info['score_away_win_decrease_count'] = {
            'description': '客胜比分赔率下降数量',
            'source': 'score_history',
            'calculation': '客胜比分中赔率下降的数量'
        }

    return features, feature_info


def build_odds_temporal_features(match_ids, conn=None):
    all_features = []
    all_feature_info = {}
    has_data = False

    if conn is None:
        conn = load_odds_database()

    try:
        for match_id in match_ids:
            match_features = {}

            try:
                wdl_history = load_wdl_history(match_id, conn)
                if len(wdl_history) >= 2:
                    has_data = True
                    wdl_features, wdl_info = analyze_wdl_trend(wdl_history)
                    match_features.update(wdl_features)
                    all_feature_info.update(wdl_info)
            except Exception:
                pass

            try:
                handicap_history = load_handicap_history(match_id, conn)
                if len(handicap_history) >= 2:
                    has_data = True
                    hcp_features, hcp_info = analyze_handicap_trend(handicap_history)
                    match_features.update(hcp_features)
                    all_feature_info.update(hcp_info)
            except Exception:
                pass

            try:
                total_goals_history = load_total_goals_history(match_id, conn)
                target_goals = []
                if len(total_goals_history) >= 2:
                    has_data = True
                    tg_features, tg_info = analyze_total_goals_trend(total_goals_history)
                    match_features.update(tg_features)
                    all_feature_info.update(tg_info)
                    target_goals = [match_features.get(f'tg_target_goals_{i}') for i in range(1, 5) if match_features.get(f'tg_target_goals_{i}') is not None]
                    target_goals = [g for g in target_goals if g is not None]
            except Exception:
                pass

            try:
                score_history = load_score_history(match_id, conn)
                if len(score_history) >= 2:
                    has_data = True
                    score_features, score_info = analyze_score_trend(score_history, target_goals)
                    match_features.update(score_features)
                    all_feature_info.update(score_info)
            except Exception:
                pass

            all_features.append(match_features)

        features_df = pd.DataFrame(all_features)

        for col in features_df.columns:
            if col.endswith('_type') or col.endswith('_signal'):
                continue
            features_df[col] = features_df[col].fillna(0)

        if not has_data:
            print("警告: 未找到赔率时序数据，返回空特征集")

        return features_df, all_feature_info

    finally:
        if conn is not None:
            conn.close()


def build_odds_temporal_features_from_df(match_df):
    def build_match_id(row):
        date_str = row['date'].strftime('%Y-%m-%d')
        home = str(row['home_team_name']).replace(' ', '_')
        away = str(row['away_team_name']).replace(' ', '_')
        return f"{date_str}_{home}_{away}"
    
    match_ids = match_df.apply(build_match_id, axis=1).tolist()

    features_df, feature_info = build_odds_temporal_features(match_ids)

    features_df = features_df.reindex(match_df.index)

    return features_df, feature_info


if __name__ == "__main__":
    test_match_ids = ['2025-08-16_曼城_阿森纳']

    print("测试赔率时序特征提取...")
    features, info = build_odds_temporal_features(test_match_ids)
    print(f"生成特征数量: {len(features.columns)}")
    print(f"特征列表: {list(features.columns)}")
    print(f"\n特征信息:")
    for name, details in info.items():
        print(f"  {name}: {details['description']}")

    if not features.empty:
        print(f"\n示例数据:")
        print(features.head())