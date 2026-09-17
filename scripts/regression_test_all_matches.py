import sqlite3
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unified_backtest_framework import UnifiedBacktestFramework

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

def load_all_matches_from_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT match_id, home_team, away_team, match_date, actual_wdl
        FROM matches 
        WHERE actual_wdl IS NOT NULL AND actual_wdl IN ('胜', '平', '负')
        ORDER BY match_date
    """)
    matches = cursor.fetchall()
    
    conn.close()
    
    return [
        {
            'match_id': match[0],
            'home_team': match[1],
            'away_team': match[2],
            'date': match[3],
            'actual_result': match[4]
        }
        for match in matches
    ]

BACKTEST_MATCHES = load_all_matches_from_db()

def run_regression_test():
    framework = UnifiedBacktestFramework(use_ml_model=True, use_odds_analysis=True, use_score_prediction=True)
    framework.connect()
    framework.load_trained_model()
    
    results = []
    correct_count = 0
    ml_coverage = 0
    odds_coverage = 0
    score_total = 0
    score_hit_top1 = 0
    score_hit_top3 = 0
    
    print("=" * 100)
    print(f"英超比赛回归测试 - 共 {len(BACKTEST_MATCHES)} 场")
    print("=" * 100)
    
    for match in BACKTEST_MATCHES:
        match_id = match['match_id']
        actual_result = match['actual_result']
        
        print(f"\n--- {match['home_team']} vs {match['away_team']} ---")
        print(f"比赛日期: {match['date']}")
        print(f"实际结果: {actual_result}")
        
        result = framework.run_single_match_analysis(match_id)
        
        final_pred = result.get('final_prediction')
        ml_pred = result.get('ml_prediction', {}) or {}
        odds_analysis = result.get('odds_analysis', {}) or {}
        odds_pred = odds_analysis.get('wdl', {}).get('signal_label')
        score_pred = result.get('score_prediction', {}) or {}
        
        ml_covered = '✓' if ml_pred and isinstance(ml_pred, dict) and 'error' not in ml_pred else '✗'
        odds_covered = '✓' if odds_pred in ['胜', '平', '负'] else '✗'
        
        if ml_pred and isinstance(ml_pred, dict) and 'error' not in ml_pred:
            ml_coverage += 1
        if odds_pred in ['胜', '平', '负']:
            odds_coverage += 1
        
        is_correct = final_pred == actual_result
        if is_correct:
            correct_count += 1
        
        status = '✓ 正确' if is_correct else '✗ 错误'
        
        print(f"ML预测: {ml_pred.get('prediction', 'N/A')} ({ml_covered})")
        print(f"赔率预测: {odds_pred} ({odds_covered})")
        print(f"融合预测: {final_pred} {status}")
        
        top_scores = score_pred.get('top_scores', [])
        top3_scores = score_pred.get('top3_scores', [])
        actual_score = result.get('match_info', {}).get('actual_score', '')
        if top_scores and actual_score:
            score_total += 1
            top1_score = top_scores[0][0] if top_scores else None
            top1_scores_list = [s[0] for s in top_scores[:1]]
            top3_scores_list = [s[0] for s in top3_scores] if top3_scores else [s[0] for s in top_scores[:3]]
            
            hit_top1 = actual_score in top1_scores_list
            hit_top3 = actual_score in top3_scores_list
            
            if hit_top1:
                score_hit_top1 += 1
            if hit_top3:
                score_hit_top3 += 1
            
            conf_score = score_pred.get('confidence_score', 0)
            print(f"比分预测: Top-1={top1_score}, 置信度={conf_score:.4f}, Top-1命中={'✓' if hit_top1 else '✗'}, Top-3命中={'✓' if hit_top3 else '✗'}")
        
        results.append({
            'match_id': match_id,
            'home_team': match['home_team'],
            'away_team': match['away_team'],
            'date': match['date'],
            'actual_result': actual_result,
            'actual_score': actual_score,
            'ml_prediction': ml_pred.get('prediction', None),
            'ml_covered': ml_covered == '✓',
            'odds_prediction': odds_pred,
            'odds_covered': odds_covered == '✓',
            'score_prediction': score_pred.get('top1_score', None),
            'score_confidence': score_pred.get('confidence_score', 0),
            'score_hit_top1': hit_top1 if 'hit_top1' in dir() else False,
            'score_hit_top3': hit_top3 if 'hit_top3' in dir() else False,
            'final_prediction': final_pred,
            'is_correct': is_correct
        })
    
    framework.disconnect()
    
    total_matches = len(BACKTEST_MATCHES)
    accuracy = correct_count / total_matches * 100
    ml_coverage_rate = ml_coverage / total_matches * 100
    odds_coverage_rate = odds_coverage / total_matches * 100
    score_hit_rate_top1 = score_hit_top1 / score_total * 100 if score_total > 0 else 0
    score_hit_rate_top3 = score_hit_top3 / score_total * 100 if score_total > 0 else 0
    
    print("\n" + "=" * 100)
    print("回归测试总结")
    print("=" * 100)
    print(f"总比赛数: {total_matches}")
    print(f"正确预测: {correct_count}")
    print(f"准确率: {accuracy:.1f}%")
    print(f"ML模型覆盖率: {ml_coverage_rate:.1f}% ({ml_coverage}/{total_matches})")
    print(f"赔率分析覆盖率: {odds_coverage_rate:.1f}% ({odds_coverage}/{total_matches})")
    print(f"比分预测Top-1命中率: {score_hit_rate_top1:.1f}% ({score_hit_top1}/{score_total})")
    print(f"比分预测Top-3命中率: {score_hit_rate_top3:.1f}% ({score_hit_top3}/{score_total})")
    
    failed_matches = [r for r in results if not r['is_correct']]
    if failed_matches:
        print("\n预测错误的比赛:")
        for r in failed_matches:
            print(f"  - {r['home_team']} vs {r['away_team']}: 实际={r['actual_result']}, 预测={r['final_prediction']}")
    
    return results, {
        'total_matches': total_matches,
        'correct_count': correct_count,
        'accuracy': accuracy,
        'ml_coverage': ml_coverage,
        'ml_coverage_rate': ml_coverage_rate,
        'odds_coverage': odds_coverage,
        'odds_coverage_rate': odds_coverage_rate,
        'score_total': score_total,
        'score_hit_top1': score_hit_top1,
        'score_hit_top3': score_hit_top3,
        'score_hit_rate_top1': score_hit_rate_top1,
        'score_hit_rate_top3': score_hit_rate_top3
    }

if __name__ == "__main__":
    results, summary = run_regression_test()
    
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'output')
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, 'regression_test_results.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump({
            'summary': summary,
            'results': results
        }, f, ensure_ascii=False, indent=2)
    
    print(f"\n详细结果已保存到: {output_path}")