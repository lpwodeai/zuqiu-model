"""验证 Q1+Q2 修复效果"""
import json, os, sys, warnings, time
import numpy as np
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from prediction_core import init_models, PredictionCore
from generate_unified_report import parse_odds_txt_auto
from feature_utils import normalize_team_name

# 测试两场：一场典型比赛，一场极端比赛
test_cases = [
    ('埃弗顿', '水晶宫', '英超', '英超'),
    ('西班牙人', '皇家马德里', '西甲', '西甲'),
    ('毕尔巴鄂竞技', '塞维利亚', '西甲', '西甲'),
]

print("加载模型...")
models = init_models()
core = PredictionCore(models)

# 加载 TXT
all_odds = {}
for fname, league in [('英超2026-2027赛季完整时序赔率.txt', '英超'),
                        ('西甲2026-2027赛季完整时序赔率.txt', '西甲'),
                        ('法甲2026-2027赛季完整时序赔率.txt', '法甲'),
                        ('意甲2026-2027赛季完整时序赔率.txt', '意甲')]:
    fpath = os.path.join(PROJECT_DIR, 'data', fname)
    if os.path.exists(fpath):
        matches = parse_odds_txt_auto(fpath)
        for m in matches:
            key = f"{normalize_team_name(m['home_short'])}:{normalize_team_name(m['away_short'])}"
            all_odds[key] = m

for home_cn, away_cn, league, txt_league in test_cases:
    home_norm = normalize_team_name(home_cn)
    away_norm = normalize_team_name(away_cn)
    key = f"{home_norm}:{away_norm}"
    
    matched = all_odds.get(key)
    if not matched:
        # 尝试反向
        key_rev = f"{away_norm}:{home_norm}"
        matched = all_odds.get(key_rev)
    
    if not matched:
        print(f"\n{'='*60}")
        print(f"❌ {home_cn} vs {away_cn}: TXT无匹配")
        continue
    
    match = {
        'home_team': home_norm, 'away_team': away_norm,
        'home_team_cn': home_cn, 'away_team_cn': away_cn,
        'league': league, 'match_time': '2026-08-22 22:00',
    }
    
    print(f"\n{'='*60}")
    print(f"测试: {home_cn} vs {away_cn}")
    
    try:
        result = core.predict_unified(match, matched['odds_data'], is_mock=False)
        wdl = result['wdl']
        score = result['score']
        
        print(f"\n  WDL: {wdl['prediction']} ({wdl.get('confidence',0)*100:.1f}%)")
        print(f"  method: {wdl['method']}")
        
        # 验证 Q2: 比分是否与 WDL 一致
        wdl_pred = wdl['prediction']
        most_likely = score['most_likely']
        parts = most_likely.split(':')
        h_goals = int(parts[0])
        a_goals = int(parts[1])
        score_outcome = '主胜' if h_goals > a_goals else ('平局' if h_goals == a_goals else '客胜')
        
        top3_scores = [(s['score'], f"{s['prob']*100:.1f}%") for s in score['top5'][:3]]
        print(f"\n  比分: {most_likely} ({score['most_likely_prob']*100:.1f}%) [{score_outcome}]")
        print(f"  Top-3: {top3_scores}")
        
        if wdl_pred == score_outcome:
            print(f"  ✅ WDL({wdl_pred}) 与 比分({score_outcome}) 一致!")
        else:
            print(f"  ⚠️ WDL({wdl_pred}) vs 比分({score_outcome}) 不一致")
        
        # 显示 λ 值
        print(f"  λ: {score.get('lambda_home', 'N/A'):.2f} / {score.get('lambda_away', 'N/A'):.2f}")
        
    except Exception as e:
        print(f"  ❌ 预测失败: {e}")
        import traceback; traceback.print_exc()

print(f"\n{'='*60}")
print("验证完成")