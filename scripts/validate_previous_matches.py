import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))
from unified_backtest_framework import UnifiedBacktestFramework

framework = UnifiedBacktestFramework(use_odds_analysis=True, use_score_prediction=True)
framework.connect()
framework.load_trained_model()

match_ids = [
    ('2025-11-08_Everton_Fulham', '胜'),
    ('2025-11-09_Sunderland_Arsenal', '平'),
    ('2025-11-09_Chelsea_Wolverhampton_Wanderers', '胜'),
    ('2025-11-09_Aston_Villa_Bournemouth', '胜'),
    ('2025-11-09_Brentford_Newcastle_United', '胜')
]

print("验证之前比赛的预测结果：")
print("-" * 70)

all_correct = True
for match_id, expected in match_ids:
    try:
        result = framework.run_single_match_analysis(match_id)
        final_pred = result.get('final_prediction', 'N/A')
        is_correct = final_pred == expected
        status = '✓' if is_correct else '✗'
        if match_id == '2025-11-09_Brentford_Newcastle_United':
            ml_probs = result.get('ml_model', {}).get('probabilities', {})
            odds_probs = result.get('odds_analysis', {}).get('wdl', {}).get('implied_probabilities', {})
            score_pred = result.get('score_prediction', {}).get('top_predictions', [])
            print(f"  ML概率: {ml_probs}")
            print(f"  赔率隐含概率: {odds_probs}")
            print(f"  比分预测Top-3: {score_pred[:3]}")
        if not is_correct:
            all_correct = False
        print(f"{status} {match_id}: 预测={final_pred}, 实际={expected}")
    except Exception as e:
        print(f"! {match_id}: 错误 - {e}")

print("-" * 70)
print(f"全部正确: {'是' if all_correct else '否'}")
framework.disconnect()
