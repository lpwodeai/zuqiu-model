import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import poisson
import math
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

def parse_score(score_str):
    if not score_str or ':' not in str(score_str):
        return None, None
    parts = str(score_str).split(':')
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None, None

def load_match_data(conn):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT m.match_id, m.home_team, m.away_team, m.actual_score, m.actual_total_goals,
               mm.sh_match_id
        FROM matches m
        LEFT JOIN match_id_mapping mm ON mm.matches_match_id = m.match_id
        WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
        GROUP BY m.match_id
    """)
    rows = cursor.fetchall()
    data = []
    for r in rows:
        home_goals, away_goals = parse_score(r[3])
        data.append({
            'match_id': r[0],
            'home_team': r[1],
            'away_team': r[2],
            'actual_score': r[3],
            'home_goals': home_goals,
            'away_goals': away_goals,
            'actual_total_goals': r[4],
            'sh_match_id': r[5]
        })
    return data

def load_wdl_odds(conn, sh_match_id):
    if not sh_match_id:
        return None
    cursor = conn.cursor()
    cursor.execute("""
        SELECT win_a, draw, win_b FROM wdl_history
        WHERE match_id = ? ORDER BY timestamp DESC LIMIT 1
    """, (sh_match_id,))
    row = cursor.fetchone()
    if row:
        return {'win_a': row[0], 'draw': row[1], 'win_b': row[2]}
    return None

def load_score_odds(conn, sh_match_id):
    if not sh_match_id:
        return {}
    cursor = conn.cursor()
    cursor.execute("""
        SELECT score, odds FROM score_history
        WHERE match_id = ?
    """, (sh_match_id,))
    rows = cursor.fetchall()
    score_odds = {}
    for score, odds in rows:
        if score not in score_odds:
            score_odds[score] = []
        score_odds[score].append(odds)
    result = {}
    for score, odds_list in score_odds.items():
        result[score] = np.mean(odds_list)
    return result

def calculate_lambda(odds, score_odds=None, home_advantage=0.5):
    avg_goals = 2.8
    
    if odds:
        win_a, draw, win_b = odds['win_a'], odds['draw'], odds['win_b']
        total_implied = 1/win_a + 1/draw + 1/win_b
        prob_home = (1/win_a) / total_implied
        prob_away = (1/win_b) / total_implied
        
        lambda_home = avg_goals * prob_home
        lambda_away = avg_goals * prob_away
    else:
        lambda_home = avg_goals * home_advantage
        lambda_away = avg_goals * (1 - home_advantage)
    
    if score_odds:
        implied_total = 0
        total_prob = 0
        for score, odds_val in score_odds.items():
            h, a = parse_score(score)
            if h is not None:
                p = 1 / odds_val
                implied_total += (h + a) * p
                total_prob += p
        if total_prob > 0:
            implied_total /= total_prob
            if implied_total > 0:
                scale = implied_total / (lambda_home + lambda_away) if (lambda_home + lambda_away) > 0 else 1
                lambda_home *= scale
                lambda_away *= scale
    
    lambda_home = max(0.1, min(6.0, lambda_home))
    lambda_away = max(0.1, min(6.0, lambda_away))
    
    return lambda_home, lambda_away

def dixon_coles_correction(h, a, lambda_home, lambda_away, rho=-0.15):
    if h <= 1 and a <= 1:
        if h == 0 and a == 0:
            return 1.0 - lambda_home * lambda_away * rho
        elif h == 0 and a == 1:
            return 1.0 + lambda_home * rho
        elif h == 1 and a == 0:
            return 1.0 + lambda_away * rho
        elif h == 1 and a == 1:
            return 1.0 - rho
    return 1.0

def poisson_predict(lambda_home, lambda_away, max_goals=7):
    score_probs = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            prob_h = poisson.pmf(h, lambda_home)
            prob_a = poisson.pmf(a, lambda_away)
            tau = dixon_coles_correction(h, a, lambda_home, lambda_away)
            score_probs[f"{h}:{a}"] = prob_h * prob_a * tau
    
    total = sum(score_probs.values())
    if total > 0:
        for k in score_probs:
            score_probs[k] /= total
    
    return score_probs

def monte_carlo_predict(lambda_home, lambda_away, n_sim=10000, max_goals=7):
    home_goals = np.random.poisson(lambda_home, n_sim)
    away_goals = np.random.poisson(lambda_away, n_sim)
    
    home_goals = np.clip(home_goals, 0, max_goals)
    away_goals = np.clip(away_goals, 0, max_goals)
    
    scores = [f"{h}:{a}" for h, a in zip(home_goals, away_goals)]
    
    score_counts = {}
    for s in scores:
        score_counts[s] = score_counts.get(s, 0) + 1
    
    total = len(scores)
    score_probs = {k: v / total for k, v in score_counts.items()}
    
    return score_probs

def fuse_predictions(poisson_probs, mc_probs, poisson_weight=0.65, mc_weight=0.35):
    all_scores = set(list(poisson_probs.keys()) + list(mc_probs.keys()))
    
    fused = {}
    for score in all_scores:
        p = poisson_probs.get(score, 0)
        m = mc_probs.get(score, 0)
        fused[score] = p * poisson_weight + m * mc_weight
    
    total = sum(fused.values())
    if total > 0:
        for k in fused:
            fused[k] /= total
    
    return fused

def evaluate_prediction(actual_score, fused_probs, top_n=5):
    sorted_scores = sorted(fused_probs.items(), key=lambda x: x[1], reverse=True)
    actual = str(actual_score)
    
    exact_hit = 0
    within1_hit = 0
    within2_hit = 0
    
    actual_h, actual_a = parse_score(actual_score)
    
    for score, prob in sorted_scores[:top_n]:
        h, a = parse_score(score)
        if h is not None and actual_h is not None:
            diff = abs(h - actual_h) + abs(a - actual_a)
            if score == actual:
                exact_hit = 1
            if diff <= 2:
                within2_hit = 1
            if diff <= 1:
                within1_hit = 1
    
    top1_score = sorted_scores[0][0] if sorted_scores else None
    top1_prob = sorted_scores[0][1] if sorted_scores else 0
    
    top3_probs = sum(p for s, p in sorted_scores[:3])
    
    return {
        'exact_hit': exact_hit,
        'within1_hit': within1_hit,
        'within2_hit': within2_hit,
        'top1_score': top1_score,
        'top1_prob': top1_prob,
        'top3_probs': top3_probs,
        'sorted_scores': sorted_scores[:top_n]
    }

def run_backtest():
    np.random.seed(42)
    
    conn = sqlite3.connect(DB_PATH)
    
    print("=" * 80)
    print("T-006 比分预测原型 - Poisson(Dixon-Coles) + 蒙特卡洛融合回测")
    print("=" * 80)
    
    matches = load_match_data(conn)
    print(f"\n加载比赛数: {len(matches)}")
    
    mapped = sum(1 for m in matches if m['sh_match_id'] is not None)
    print(f"有映射的比赛: {mapped}")
    print(f"无映射的比赛: {len(matches) - mapped}")
    
    results = []
    total = len(matches)
    
    for i, match in enumerate(matches):
        if (i + 1) % 500 == 0:
            print(f"  处理进度: {i + 1}/{total} ({(i+1)/total*100:.1f}%)")
        
        odds = load_wdl_odds(conn, match['sh_match_id'])
        score_odds = load_score_odds(conn, match['sh_match_id'])
        
        lambda_home, lambda_away = calculate_lambda(odds, score_odds)
        
        poisson_probs = poisson_predict(lambda_home, lambda_away)
        
        mc_probs = monte_carlo_predict(lambda_home, lambda_away, n_sim=10000)
        
        fused_probs = fuse_predictions(poisson_probs, mc_probs, 0.65, 0.35)
        
        ev = evaluate_prediction(match['actual_score'], fused_probs)
        
        results.append({
            'match_id': match['match_id'],
            'home_team': match['home_team'],
            'away_team': match['away_team'],
            'actual_score': match['actual_score'],
            'lambda_home': lambda_home,
            'lambda_away': lambda_away,
            'expected_total': lambda_home + lambda_away,
            'exact_hit': ev['exact_hit'],
            'within1_hit': ev['within1_hit'],
            'within2_hit': ev['within2_hit'],
            'top1_score': ev['top1_score'],
            'top1_prob': ev['top1_prob'],
            'top3_probs': ev['top3_probs'],
            'has_odds': odds is not None,
            'has_score_odds': len(score_odds) > 0
        })
    
    conn.close()
    
    return results

def print_summary(results):
    total = len(results)
    if total == 0:
        print("无回测数据")
        return
    
    exact_hits = sum(1 for r in results if r['exact_hit'])
    within1_hits = sum(1 for r in results if r['within1_hit'])
    within2_hits = sum(1 for r in results if r['within2_hit'])
    
    has_odds = [r for r in results if r['has_odds']]
    no_odds = [r for r in results if not r['has_odds']]
    
    has_both = [r for r in results if r['has_odds'] and r['has_score_odds']]
    
    avg_lambda_home = np.mean([r['lambda_home'] for r in results])
    avg_lambda_away = np.mean([r['lambda_away'] for r in results])
    avg_expected = np.mean([r['expected_total'] for r in results])
    
    avg_top1_prob = np.mean([r['top1_prob'] for r in results])
    avg_top3_probs = np.mean([r['top3_probs'] for r in results])
    
    print("\n" + "=" * 80)
    print("回测结果汇总")
    print("=" * 80)
    
    print(f"\n总比赛数: {total}")
    print(f"有赔率数据: {len(has_odds)} ({len(has_odds)/total*100:.1f}%)")
    print(f"无赔率数据: {len(no_odds)} ({len(no_odds)/total*100:.1f}%)")
    print(f"有赔率+比分赔率: {len(has_both)} ({len(has_both)/total*100:.1f}%)")
    
    print(f"\n{'='*80}")
    print("预测准确率 (Top-5 范围内)")
    print(f"{'='*80}")
    print(f"  精确比分命中: {exact_hits}/{total} ({exact_hits/total*100:.2f}%)")
    print(f"  1球内命中:   {within1_hits}/{total} ({within1_hits/total*100:.2f}%)")
    print(f"  2球内命中:   {within2_hits}/{total} ({within2_hits/total*100:.2f}%)")
    
    print(f"\n{'='*80}")
    print("Lambda 统计")
    print(f"{'='*80}")
    print(f"  平均主队 Lambda: {avg_lambda_home:.3f}")
    print(f"  平均客队 Lambda: {avg_lambda_away:.3f}")
    print(f"  平均总进球期望:  {avg_expected:.3f}")
    
    print(f"\n{'='*80}")
    print("预测置信度")
    print(f"{'='*80}")
    print(f"  平均 Top-1 概率:  {avg_top1_prob:.4f}")
    print(f"  平均 Top-3 概率:  {avg_top3_probs:.4f}")
    
    if has_odds:
        exact_with = sum(1 for r in has_odds if r['exact_hit'])
        w1_with = sum(1 for r in has_odds if r['within1_hit'])
        w2_with = sum(1 for r in has_odds if r['within2_hit'])
        
        print(f"\n{'='*80}")
        print("有赔率数据子集 (n={})".format(len(has_odds)))
        print(f"{'='*80}")
        print(f"  精确比分命中: {exact_with}/{len(has_odds)} ({exact_with/len(has_odds)*100:.2f}%)")
        print(f"  1球内命中:   {w1_with}/{len(has_odds)} ({w1_with/len(has_odds)*100:.2f}%)")
        print(f"  2球内命中:   {w2_with}/{len(has_odds)} ({w2_with/len(has_odds)*100:.2f}%)")
    
    if no_odds:
        exact_without = sum(1 for r in no_odds if r['exact_hit'])
        w1_without = sum(1 for r in no_odds if r['within1_hit'])
        w2_without = sum(1 for r in no_odds if r['within2_hit'])
        
        print(f"\n{'='*80}")
        print("无赔率数据子集 (n={})".format(len(no_odds)))
        print(f"{'='*80}")
        print(f"  精确比分命中: {exact_without}/{len(no_odds)} ({exact_without/len(no_odds)*100:.2f}%)")
        print(f"  1球内命中:   {w1_without}/{len(no_odds)} ({w1_without/len(no_odds)*100:.2f}%)")
        print(f"  2球内命中:   {w2_without}/{len(no_odds)} ({w2_without/len(no_odds)*100:.2f}%)")
    
    print(f"\n{'='*80}")
    print("Top-10 预测 vs 实际")
    print(f"{'='*80}")
    print(f"{'#':<4} | {'比赛':<30} | {'实际比分':<10} | {'Top-1预测':<10} | {'概率':<8} | {'命中':<6}")
    print("-" * 100)
    
    for i, r in enumerate(results[:10], 1):
        team_str = f"{r['home_team']} vs {r['away_team']}"
        if len(team_str) > 30:
            team_str = team_str[:27] + "..."
        
        hit_str = "✓" if r['exact_hit'] else ("△" if r['within1_hit'] else ("◇" if r['within2_hit'] else "✗"))
        
        print(f"{i:<4} | {team_str:<30} | {r['actual_score']:<10} | {r['top1_score']:<10} | {r['top1_prob']:<8.4f} | {hit_str:<6}")
    
    print(f"\n{'='*80}")
    print("命中率按联赛/赔率可用性分组")
    print(f"{'='*80}")
    
    results_df = pd.DataFrame(results)
    if len(results_df) > 0:
        results_df['total_goals'] = results_df['actual_score'].apply(
            lambda x: sum(parse_score(x)) if parse_score(x)[0] is not None else 0
        )
        
        bins = [0, 2, 4, 6, 8, 15]
        labels = ['0-1球', '2-3球', '4-5球', '6-7球', '8+球']
        results_df['goal_bin'] = pd.cut(results_df['total_goals'], bins=bins, labels=labels, right=False)
        
        print("\n按总进球数分布:")
        for label in labels:
            subset = results_df[results_df['goal_bin'] == label]
            if len(subset) > 0:
                exact = subset['exact_hit'].sum()
                w1 = subset['within1_hit'].sum()
                w2 = subset['within2_hit'].sum()
                print(f"  {label:>8}: n={len(subset):>4} | 精确={exact/len(subset)*100:5.1f}% | 1球内={w1/len(subset)*100:5.1f}% | 2球内={w2/len(subset)*100:5.1f}%")
    
    return {
        'total': total,
        'exact_rate': exact_hits / total * 100,
        'within1_rate': within1_hits / total * 100,
        'within2_rate': within2_hits / total * 100,
        'avg_lambda_home': avg_lambda_home,
        'avg_lambda_away': avg_lambda_away,
        'avg_expected': avg_expected,
        'has_odds_count': len(has_odds),
        'no_odds_count': len(no_odds)
    }

if __name__ == "__main__":
    print("=" * 80)
    print("T-006 比分预测 Benchmark - Dixon-Coles + Poisson + Monte Carlo")
    print("=" * 80)
    
    results = run_backtest()
    summary = print_summary(results)
    
    print("\n" + "=" * 80)
    print("BENCHMARK 最终指标")
    print("=" * 80)
    print(f"  总比赛数:         {summary['total']}")
    print(f"  精确比分命中率:   {summary['exact_rate']:.2f}%")
    print(f"  1球内命中率:      {summary['within1_rate']:.2f}%")
    print(f"  2球内命中率:      {summary['within2_rate']:.2f}%")
    print(f"  平均主队 Lambda:  {summary['avg_lambda_home']:.3f}")
    print(f"  平均客队 Lambda:  {summary['avg_lambda_away']:.3f}")
    print(f"  平均总进球期望:   {summary['avg_expected']:.3f}")
    
    within1_target = 40.0
    print(f"\n  within1_rate 目标检查:")
    print(f"    目标 within1_rate >= {within1_target:.1f}%")
    if summary['within1_rate'] >= within1_target:
        print(f"    ✅ 达标! ({summary['within1_rate']:.2f}% >= {within1_target:.1f}%)")
    else:
        gap = within1_target - summary['within1_rate']
        print(f"    ❌ 未达标 ({summary['within1_rate']:.2f}% < {within1_target:.1f}%), 差距 {gap:.2f}%")
    
    print("\n  配置: Dixon-Coles ρ=-0.15, Poisson权重=0.65, MC权重=0.35, 去重=GROUP BY match_id")
    print("  完成!")