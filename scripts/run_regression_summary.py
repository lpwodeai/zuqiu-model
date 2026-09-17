import sys
import io
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from unified_backtest_framework import UnifiedBacktestFramework
from regression_test_all_matches import BACKTEST_MATCHES

framework = UnifiedBacktestFramework(use_odds_analysis=True, use_score_prediction=True)
framework.connect()
framework.load_trained_model()

correct_count = 0
ml_coverage = 0
odds_coverage = 0
score_total = 0
score_hit_top1 = 0
score_hit_top3 = 0

total_matches = len(BACKTEST_MATCHES)

for i, match in enumerate(BACKTEST_MATCHES):
    match_id = match['match_id']
    actual_result = match['actual_result']
    
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    
    try:
        result = framework.run_single_match_analysis(match_id)
    finally:
        sys.stdout = old_stdout
    
    final_pred = result.get('final_prediction')
    ml_pred = result.get('ml_prediction', {}) or {}
    odds_analysis = result.get('odds_analysis', {}) or {}
    odds_pred = odds_analysis.get('wdl', {}).get('signal_label')
    score_pred = result.get('score_prediction', {}) or {}
    
    if ml_pred and isinstance(ml_pred, dict) and 'error' not in ml_pred:
        ml_coverage += 1
    if odds_pred in ['胜', '平', '负']:
        odds_coverage += 1
    
    is_correct = final_pred == actual_result
    if is_correct:
        correct_count += 1
    
    top_scores = score_pred.get('top_scores', [])
    top3_scores = score_pred.get('top3_scores', [])
    actual_score = result.get('match_info', {}).get('actual_score', '')
    
    if top_scores and actual_score:
        score_total += 1
        top1_scores_list = [s[0] for s in top_scores[:1]]
        top3_scores_list = [s[0] for s in top3_scores] if top3_scores else [s[0] for s in top_scores[:3]]
        
        if actual_score in top1_scores_list:
            score_hit_top1 += 1
        if actual_score in top3_scores_list:
            score_hit_top3 += 1
    
    if (i + 1) % 20 == 0:
        print(f"进度: {i + 1}/{total_matches}")

framework.disconnect()

accuracy = correct_count / total_matches * 100
ml_coverage_rate = ml_coverage / total_matches * 100
odds_coverage_rate = odds_coverage / total_matches * 100
score_hit_rate_top1 = score_hit_top1 / score_total * 100 if score_total > 0 else 0
score_hit_rate_top3 = score_hit_top3 / score_total * 100 if score_total > 0 else 0

print("=" * 80)
print("回归测试总结")
print("=" * 80)
print(f"总比赛数: {total_matches}")
print(f"正确预测: {correct_count}")
print(f"准确率: {accuracy:.1f}%")
print(f"ML模型覆盖率: {ml_coverage_rate:.1f}% ({ml_coverage}/{total_matches})")
print(f"赔率分析覆盖率: {odds_coverage_rate:.1f}% ({odds_coverage}/{total_matches})")
print(f"比分预测Top-1命中率: {score_hit_rate_top1:.1f}% ({score_hit_top1}/{score_total})")
print(f"比分预测Top-3命中率: {score_hit_rate_top3:.1f}% ({score_hit_top3}/{score_total})")