"""分析 Q3: 比分预测与WDL脱节根因"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from prediction_core import CalcEngine
import numpy as np

cases = [
    ('埃弗顿 vs 水晶宫', 1.80, 1.14, '主胜 45.3%'),
    ('诺丁汉 vs 利兹联', 1.58, 1.11, '主胜 43.1%'),
    ('伊普斯 vs 桑德兰', 1.11, 1.58, '客胜 39.1%'),
    ('毕尔巴鄂 vs 塞维利亚', 2.08, 1.22, '主胜 54.9%'),
    ('西班牙人 vs 皇马', 0.32, 3.99, '客胜 61.5%'),
]

for name, lh, la, wdl_pred in cases:
    np.random.seed(42)
    poisson_probs = CalcEngine.poisson_score_predict(lh, la, max_goals=7, rho=-0.30)
    mc_probs = CalcEngine.monte_carlo_score_predict(lh, la, n_sim=500, max_goals=7)
    fused = CalcEngine.fuse_score_predictions(poisson_probs, mc_probs)
    sorted_scores = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:5]
    
    wdl_home = sum(p for s, p in fused.items() if int(s.split(':')[0]) > int(s.split(':')[1]))
    wdl_draw = sum(p for s, p in fused.items() if int(s.split(':')[0]) == int(s.split(':')[1]))
    wdl_away = sum(p for s, p in fused.items() if int(s.split(':')[0]) < int(s.split(':')[1]))
    
    # 找出匹配WDL预测的比分
    if '主胜' in wdl_pred:
        target_scores = [s for s, p in sorted_scores if int(s.split(':')[0]) > int(s.split(':')[1])]
    elif '客胜' in wdl_pred:
        target_scores = [s for s, p in sorted_scores if int(s.split(':')[0]) < int(s.split(':')[1])]
    else:
        target_scores = [s for s, p in sorted_scores if int(s.split(':')[0]) == int(s.split(':')[1])]
    
    best_match_score = target_scores[0] if target_scores else 'N/A'
    best_match_prob = fused.get(best_match_score, 0) * 100
    
    print(f'{"="*60}')
    print(f'{name} (λ={lh:.2f}/{la:.2f})')
    print(f'  WDL预测: {wdl_pred}')
    print(f'  Poisson WDL: 主胜={wdl_home*100:.1f}% 平局={wdl_draw*100:.1f}% 客胜={wdl_away*100:.1f}%')
    print(f'  最可能比分: {sorted_scores[0][0]} ({sorted_scores[0][1]*100:.1f}%)')
    print(f'  匹配WDL的最佳比分: {best_match_score} ({best_match_prob:.1f}%)')
    print(f'  Top-5: {[(s, f"{p*100:.1f}%") for s, p in sorted_scores]}')