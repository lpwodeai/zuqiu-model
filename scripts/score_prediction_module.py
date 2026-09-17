import numpy as np
import pandas as pd
import sqlite3
import math
from scipy.stats import poisson
from itertools import product
from pathlib import Path
from datetime import datetime
from t006_score_predictor_v5 import predict_score_distribution_v5

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

def calculate_lambda_from_odds(wdl_history, handicap_history=None, team_features=None):
    avg_goals_league = 2.8
    lambda_total = avg_goals_league
    
    if wdl_history:
        latest = wdl_history[-1]
        win_a, draw, win_b = latest['win_a'], latest['draw'], latest['win_b']
        
        total_implied = 1/win_a + 1/draw + 1/win_b
        prob_home = (1/win_a) / total_implied
        prob_draw = (1/draw) / total_implied
        prob_away = (1/win_b) / total_implied
        
        if prob_home > prob_away:
            home_advantage = prob_home / (prob_home + prob_away)
        else:
            home_advantage = 0.5
        
        lambda_home = lambda_total * home_advantage
        lambda_away = lambda_total * (1 - home_advantage)
        
        if handicap_history and len(handicap_history) >= 2:
            hcp_latest = handicap_history[-1]
            hcp_win, hcp_draw, hcp_lose = hcp_latest['hcp_win'], hcp_latest['hcp_draw'], hcp_latest['hcp_lose']
            
            hcp_total = 1/hcp_win + 1/hcp_draw + 1/hcp_lose
            hcp_prob_win = (1/hcp_win) / hcp_total
            
            if hcp_prob_win < 0.3:
                lambda_home *= 0.85
                lambda_away *= 1.15
    else:
        lambda_home = 1.8
        lambda_away = 1.3
    
    if team_features is not None:
        home_attack = team_features.get('home_attack_strength', 1.0)
        home_defence = team_features.get('home_defence_strength', 1.0)
        away_attack = team_features.get('away_attack_strength', 1.0)
        away_defence = team_features.get('away_defence_strength', 1.0)
        
        h2h_avg_home = team_features.get('h2h_avg_goals_home', 0)
        h2h_avg_away = team_features.get('h2h_avg_goals_away', 0)
        h2h_matches = team_features.get('h2h_matches', 0)
        
        h2h_factor = 0.3 if h2h_matches >= 3 else 0
        
        lambda_home = lambda_home * home_attack * away_defence
        lambda_away = lambda_away * away_attack * home_defence
        
        if h2h_matches >= 3:
            h2h_weight = h2h_factor
            h2h_total = h2h_avg_home + h2h_avg_away
            
            if h2h_total > 0:
                h2h_home_ratio = h2h_avg_home / h2h_total
                h2h_away_ratio = h2h_avg_away / h2h_total
                
                lambda_home = lambda_home * (1 - h2h_weight) + h2h_avg_home * h2h_weight
                lambda_away = lambda_away * (1 - h2h_weight) + h2h_avg_away * h2h_weight
    
    lambda_home = max(0.2, min(5.0, lambda_home))
    lambda_away = max(0.2, min(5.0, lambda_away))
    
    return lambda_home, lambda_away

def calculate_lambda_from_total_goals(tg_history):
    if not tg_history:
        return None, None
    
    latest = tg_history[-1]
    goals_probs = {}
    
    for i in range(8):
        key = f'goals_{i}' if i < 7 else 'goals_7_plus'
        if key in latest and latest[key] is not None and latest[key] > 0:
            goals_probs[i] = 1/latest[key]
    
    total = sum(goals_probs.values())
    if total == 0:
        return None, None
    
    for k in goals_probs:
        goals_probs[k] /= total
    
    expected_goals = sum(k * goals_probs[k] for k in goals_probs)
    
    return expected_goals, goals_probs

def dixon_coles_correction(h, a, rho):
    if (h == 0 and a == 0):
        return 1 - rho
    elif (h == 0 and a == 1):
        return 1 + rho
    elif (h == 1 and a == 0):
        return 1 + rho
    elif (h == 1 and a == 1):
        return 1 - rho
    else:
        return 1

def predict_score_distribution(wdl_history, handicap_history=None, tg_history=None, max_goals=5, team_features=None, rho=-0.1, production_wdl_probs=None):
    """C2 集成：升级为 T-006 v5（去水 WDL 数值求解 λ + IPF 乘性重加权对齐 WDL 边际）。

    相比 v4（λ=2.8×胜率 + tg 0.3 混合破坏 WDL 边际）：
      - λ 由数值求解强度差使泊松胜负倾向对齐 WDL
      - 总进球期望(tg_history)融入 total 先验，兼顾大小球且不破坏 WDL 边缘
      - IPF 重加权保证比分网格 WDL 三边缘精确对准概率（不劣化 WDL 栈）
    production WDL 锚定：优先用生产 WDL 概率（model_predictions WDL_home/draw/away），
      仅当缺失/不可用时回退去水赔率。生产 WDL 来自统一报告模型 ML 输出，比去水赔率更贴近模型信念。
    返回 (score_probs, lambda_home, lambda_away)，与 v4 签名/返回值一致，下游无需改动。
    """
    # 优先生产 WDL 概率锚定；否则用去水 WDL（末条）
    wdl_probs = None
    if production_wdl_probs is not None:
        try:
            vals = [float(x) for x in production_wdl_probs]
            if len(vals) == 3 and all(v > 0 for v in vals) and abs(sum(vals) - 1.0) < 0.2:
                s_ = sum(vals)
                wdl_probs = [v / s_ for v in vals]
        except Exception:
            wdl_probs = None
    if wdl_probs is None and wdl_history:
        latest = wdl_history[-1]
        try:
            ia, id_, ib = 1.0 / latest['win_a'], 1.0 / latest['draw'], 1.0 / latest['win_b']
            s_ = ia + id_ + ib
            wdl_probs = [ia / s_, id_ / s_, ib / s_]
        except Exception:
            wdl_probs = None

    # 总进球期望（tg_history 均值，可选）
    tg_expected = None
    if tg_history:
        latest_tg = tg_history[-1]
        keys = ['goals_0', 'goals_1', 'goals_2', 'goals_3', 'goals_4', 'goals_5', 'goals_6', 'goals_7_plus']
        vals = []
        for i, k in enumerate(keys):
            v = latest_tg.get(k)
            if v is not None:
                g = 7 if k == 'goals_7_plus' else i
                vals.append(g * v)
        if vals:
            tg_expected = sum(vals)

    if wdl_probs is None:
        # 无赔率时回退：保持均线 λ（近似 v4 无赔率路径）
        lambda_home = 2.8 * 0.55
        lambda_away = 2.8 * 0.45
        if tg_expected:
            sc = tg_expected / 2.8 if 2.8 > 0 else 1.0
            lambda_home *= sc; lambda_away *= sc
        import numpy as _np
        from scipy.stats import poisson as _poiss
        s = {}
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                s[f"{h}:{a}"] = _poiss.pmf(h, lambda_home) * _poiss.pmf(a, lambda_away)
        tot = sum(s.values())
        return {k: v / tot for k, v in s.items()}, lambda_home, lambda_away

    score_probs, lambda_home, lambda_away = predict_score_distribution_v5(
        wdl_probs, total_goals_expected=tg_expected, max_goals=max_goals)
    return score_probs, lambda_home, lambda_away

def get_top_scores(score_probs, top_n=5):
    sorted_scores = sorted(score_probs.items(), key=lambda x: x[1], reverse=True)
    return sorted_scores[:top_n]

def evaluate_score_prediction(actual_score, score_probs, top_n=3):
    top_scores = get_top_scores(score_probs, top_n)
    
    actual_score_str = str(actual_score)
    
    hit = False
    rank = None
    hit_confidence = 0
    
    for i, (score, prob) in enumerate(top_scores):
        if score == actual_score_str:
            hit = True
            rank = i + 1
            hit_confidence = prob
            break
    
    top1_confidence = top_scores[0][1] if top_scores else 0
    
    confidence = hit_confidence if hit else top1_confidence
    
    return {
        'hit': hit,
        'rank': rank,
        'confidence': confidence,
        'top1_confidence': top1_confidence,
        'top_n': top_n,
        'top_scores': top_scores
    }

def load_production_wdl_probs(conn, match_id):
    """从 model_predictions 读取生产 WDL 概率（WDL_home/WDL_draw/WDL_away，统一报告模型 ML 输出）。

    三者齐全且均为有效概率时返回 [home, draw, away]，否则返回 None（由调用方回退去水赔率）。
    """
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT prediction_type, probability FROM model_predictions
            WHERE match_id = ? AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')
        """, (match_id,))
        probs = {}
        for ptype, prob in cursor.fetchall():
            if prob is not None and ptype in ('WDL_home', 'WDL_draw', 'WDL_away'):
                probs[ptype] = float(prob)
        if len(probs) == 3:
            return [probs['WDL_home'], probs['WDL_draw'], probs['WDL_away']]
    except Exception:
        pass
    return None


def analyze_match_score(match_id):
    conn = sqlite3.connect(DB_PATH)
    
    cursor = conn.cursor()
    cursor.execute('SELECT home_team, away_team, actual_score, actual_total_goals FROM matches WHERE match_id = ?', (match_id,))
    match = cursor.fetchone()
    
    if not match:
        conn.close()
        return None, None
    
    home_team, away_team, actual_score, actual_total_goals = match
    
    wdl_history = []
    cursor.execute('SELECT timestamp, win_a, draw, win_b FROM wdl_history WHERE match_id = ? ORDER BY timestamp', (match_id,))
    for row in cursor.fetchall():
        wdl_history.append({'timestamp': row[0], 'win_a': row[1], 'draw': row[2], 'win_b': row[3]})
    
    handicap_history = []
    cursor.execute('SELECT timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE match_id = ? ORDER BY timestamp', (match_id,))
    for row in cursor.fetchall():
        handicap_history.append({'timestamp': row[0], 'hcp_win': row[1], 'hcp_draw': row[2], 'hcp_lose': row[3]})
    
    tg_history = []
    cursor.execute('SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus FROM total_goals_history WHERE match_id = ? ORDER BY timestamp', (match_id,))
    for row in cursor.fetchall():
        tg_history.append({
            'timestamp': row[0],
            'goals_0': row[1], 'goals_1': row[2], 'goals_2': row[3],
            'goals_3': row[4], 'goals_4': row[5], 'goals_5': row[6],
            'goals_6': row[7], 'goals_7_plus': row[8]
        })

    # C2 集成：production WDL 概率锚定（ML 输出优先，缺失则内部回退去水赔率）
    production_wdl_probs = load_production_wdl_probs(conn, match_id)

    conn.close()
    
    score_probs, lambda_home, lambda_away = predict_score_distribution(
        wdl_history, handicap_history, tg_history, production_wdl_probs=production_wdl_probs)
    
    evaluation = evaluate_score_prediction(actual_score, score_probs, top_n=3)
    
    top_scores = evaluation['top_scores']
    top3_scores = top_scores[:3]
    
    top1_prob = top_scores[0][1] if top_scores else 0
    top3_prob_sum = sum(p for s, p in top3_scores)
    
    if top_scores and len(top_scores) >= 2:
        top1_prob = top_scores[0][1]
        top2_prob = top_scores[1][1]
        confidence_gap = top1_prob - top2_prob
    else:
        confidence_gap = 0
    
    entropy = -sum(p * math.log(p) for p in score_probs.values() if p > 0) if score_probs else 0
    max_entropy = math.log(len(score_probs)) if len(score_probs) > 0 else 1
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0
    confidence_score = (1 - normalized_entropy) * 0.5 + confidence_gap * 0.5
    
    top1_score = top_scores[0][0] if top_scores else None
    if top1_score and ':' in top1_score:
        h, a = map(int, top1_score.split(':'))
        top1_wdl = '胜' if h > a else ('平' if h == a else '负')
    else:
        top1_wdl = None
    
    return {
        'match_id': match_id,
        'home_team': home_team,
        'away_team': away_team,
        'actual_score': actual_score,
        'actual_total_goals': actual_total_goals,
        'lambda_home': round(lambda_home, 2),
        'lambda_away': round(lambda_away, 2),
        'expected_total_goals': round(lambda_home + lambda_away, 2),
        'score_probs': score_probs,
        'top_scores': [(s, round(p, 4)) for s, p in top_scores],
        'top3_scores': [(s, round(p, 4)) for s, p in top3_scores],
        'top1_score': top1_score,
        'top1_wdl': top1_wdl,
        'top1_probability': round(top1_prob, 4),
        'top3_probability_sum': round(top3_prob_sum, 4),
        'confidence_gap': round(confidence_gap, 4),
        'confidence_score': round(confidence_score, 4),
        'entropy': round(entropy, 4),
        'hit': evaluation['hit'],
        'rank': evaluation['rank'],
        'confidence': round(evaluation['confidence'], 4)
    }

SCORE_MODEL_NAME = "t006_score_predictor_v5"

def save_score_prediction(conn, match_id, analysis, model_name=SCORE_MODEL_NAME):
    """将 score 预测产物幂等落库到 model_predictions（C2 遗留项③ 接入方案）。

    写入口径（对齐 prediction_db_writer.py）：
      - score grid: Score_grid_{h}_{a}，prediction='h:a'，probability=该比分概率（36 行，max_goals=5）
      - 摘要: Score_top1/Score_top3/Score_top5
      - 可信度: Score_entropy / Score_confidence
      - λ: Lambda_home / Lambda_away
    幂等：INSERT OR IGNORE + UNIQUE(match_id, model_name, prediction_type)（已建索引）。
    WDL 产物（WDL_home 等）由统一报告生成器写入，此处只管比分维度。
    """
    insert = ("INSERT OR IGNORE INTO model_predictions "
              "(match_id, model_name, prediction_type, prediction, probability, confidence, timestamp) "
              "VALUES (?, ?, ?, ?, ?, ?, ?)")
    now = datetime.now().isoformat()

    def _write(ptype, prediction, prob):
        conn.execute(insert, (match_id, model_name, ptype, prediction, prob, prob, now))

    # 1. score grid（全网格，max_goals=5 → 36 行）
    score_probs = analysis.get('score_probs') or {}
    for score, prob in score_probs.items():
        if ':' not in score:
            continue
        h, a = score.split(':', 1)
        _write(f"Score_grid_{h}_{a}", score, float(prob))

    # 2. 摘要行（top1/3/5，从完整 score_probs 排序取，避免依赖调用方截断的 top_scores）
    score_probs = analysis.get('score_probs') or {}
    ranked = [(s, p) for s, p in score_probs.items() if ':' in s]
    ranked.sort(key=lambda x: -x[1])
    for n, key_name in ((1, "Score_top1"), (3, "Score_top3"), (5, "Score_top5")):
        top_n = ranked[:n]
        if not top_n:
            continue
        joined = ",".join(s for s, _ in top_n)
        prob_sum = sum(p for _, p in top_n)
        _write(key_name, joined, round(prob_sum, 6))

    # 3. 可信度
    if analysis.get('entropy') is not None:
        _write("Score_entropy", str(round(analysis['entropy'], 4)),
               analysis.get('confidence_score'))
    if analysis.get('confidence_score') is not None:
        _write("Score_confidence", str(round(analysis['confidence_score'], 4)),
               analysis['confidence_score'])

    # 4. λ（lambda 已在现值里，复用同一 model_name 使回测可单模型取全量）
    if analysis.get('lambda_home') is not None:
        _write("Lambda_home", str(analysis['lambda_home']), analysis['lambda_home'])
    if analysis.get('lambda_away') is not None:
        _write("Lambda_away", str(analysis['lambda_away']), analysis['lambda_away'])

    conn.commit()


def run_full_score_backtest(persist=False):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    
    cursor = conn.cursor()
    cursor.execute('SELECT match_id, home_team, away_team, actual_score, actual_total_goals FROM matches ORDER BY match_id')
    matches = cursor.fetchall()
    
    results = []
    
    for match in matches:
        match_id, home_team, away_team, actual_score, actual_total_goals = match
        
        analysis = analyze_match_score(match_id)
        
        if analysis:
            if persist:
                save_score_prediction(conn, match_id, analysis)
            results.append({
                'match_id': match_id,
                'home_team': home_team,
                'away_team': away_team,
                'actual_score': actual_score,
                'actual_total_goals': actual_total_goals,
                'lambda_home': analysis['lambda_home'],
                'lambda_away': analysis['lambda_away'],
                'hit_top1': analysis['rank'] == 1,
                'hit_top3': analysis['hit'],
                'rank': analysis['rank'],
                'confidence': analysis['confidence'],
                'top_scores': [(s, round(p, 4)) for s, p in analysis['top_scores'][:3]]
            })
    
    conn.close()
    return results

def generate_score_backtest_report(results):
    total = len(results)
    
    hit_top1 = sum(1 for r in results if r['hit_top1'])
    hit_top3 = sum(1 for r in results if r['hit_top3'])
    
    avg_confidence = sum(r['confidence'] for r in results) / total if total > 0 else 0
    
    avg_lambda_home = sum(r['lambda_home'] for r in results) / total if total > 0 else 0
    avg_lambda_away = sum(r['lambda_away'] for r in results) / total if total > 0 else 0
    
    print('=' * 90)
    print('比分预测回测报告 - Poisson融合赔率模型')
    print('=' * 90)
    print(f'\n总比赛数: {total}')
    print(f'\n比分预测准确率:')
    print(f'  Top-1 命中: {hit_top1}/{total} ({hit_top1/total*100:.1f}%)')
    print(f'  Top-3 命中: {hit_top3}/{total} ({hit_top3/total*100:.1f}%)')
    print(f'\n平均置信度: {avg_confidence:.4f}')
    print(f'\n平均Lambda值:')
    print(f'  主队预期进球: {avg_lambda_home:.2f}')
    print(f'  客队预期进球: {avg_lambda_away:.2f}')
    print(f'  总预期进球: {avg_lambda_home + avg_lambda_away:.2f}')
    
    print('\n' + '=' * 90)
    print('详细结果:')
    print('=' * 90)
    print(f'{"序号":<4} | {"主客队":<30} | {"实际比分":<10} | {"Top-1预测":<10} | {"Top-1概率":<10} | {"命中":<6}')
    print('-' * 90)
    
    for i, r in enumerate(results, 1):
        team_display = f'{r["home_team"]} vs {r["away_team"]}'
        if len(team_display) > 30:
            team_display = team_display[:27] + '...'
        
        top1_score = r['top_scores'][0][0] if r['top_scores'] else 'N/A'
        top1_prob = r['top_scores'][0][1] if r['top_scores'] else 0
        
        print(f'{i:<4} | {team_display:<30} | {r["actual_score"]:<10} | {top1_score:<10} | {top1_prob:<10.4f} | {"✓" if r["hit_top1"] else "✗":<6}')
    
    return {
        'total_matches': total,
        'top1_accuracy': hit_top1/total*100 if total > 0 else 0,
        'top3_accuracy': hit_top3/total*100 if total > 0 else 0,
        'avg_confidence': avg_confidence,
        'avg_lambda_home': avg_lambda_home,
        'avg_lambda_away': avg_lambda_away
    }

def predict_single_match(wdl_history, handicap_history=None, tg_history=None):
    score_probs, lambda_home, lambda_away = predict_score_distribution(wdl_history, handicap_history, tg_history)
    
    top_scores = get_top_scores(score_probs, 5)
    top3_scores = top_scores[:3]
    
    top1_prob = top_scores[0][1] if top_scores else 0
    top3_prob_sum = sum(p for s, p in top3_scores)
    
    if top_scores and len(top_scores) >= 2:
        top1_prob = top_scores[0][1]
        top2_prob = top_scores[1][1]
        confidence_gap = top1_prob - top2_prob
    else:
        confidence_gap = 0
    
    entropy = -sum(p * math.log(p) for p in score_probs.values() if p > 0) if score_probs else 0
    max_entropy = math.log(len(score_probs)) if len(score_probs) > 0 else 1
    normalized_entropy = entropy / max_entropy if max_entropy > 0 else 0
    confidence_score = (1 - normalized_entropy) * 0.5 + confidence_gap * 0.5
    
    top1_score = top_scores[0][0] if top_scores else None
    if top1_score and ':' in top1_score:
        h, a = map(int, top1_score.split(':'))
        top1_wdl = '胜' if h > a else ('平' if h == a else '负')
    else:
        top1_wdl = None
    
    return {
        'lambda_home': round(lambda_home, 2),
        'lambda_away': round(lambda_away, 2),
        'expected_total_goals': round(lambda_home + lambda_away, 2),
        'top_scores': [(s, round(p, 4)) for s, p in top_scores],
        'top3_scores': [(s, round(p, 4)) for s, p in top3_scores],
        'top1_score': top1_score,
        'top1_wdl': top1_wdl,
        'top1_probability': round(top1_prob, 4),
        'top3_probability_sum': round(top3_prob_sum, 4),
        'confidence_gap': round(confidence_gap, 4),
        'confidence_score': round(confidence_score, 4),
        'entropy': round(entropy, 4),
        'score_probs': {k: round(v, 4) for k, v in sorted(score_probs.items(), key=lambda x: -x[1])[:20]}
    }

if __name__ == "__main__":
    results = run_full_score_backtest()
    summary = generate_score_backtest_report(results)