"""快速验证：英超独立模型 _predict_epl() 修复后是否真正参与推理"""
import warnings; warnings.filterwarnings('ignore')
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prediction_core import init_models, WDLPredictor

# 加载模型
models = init_models()

epl = models.get('epl')
print('='*60)
print('英超模型状态检查')
print('='*60)
if epl:
    print(f'  状态: {epl.get("status")}')
    print(f'  LGB: {type(epl.get("lgb_model")).__name__ if epl.get("lgb_model") else None}')
    print(f'  Scaler: {epl.get("scaler").n_features_in_ if epl.get("scaler") else None} features')
    print(f'  特征数: {len(epl.get("features", []))}')
    print(f'  前5特征: {epl["features"][:5]}')
    print(f'  后5特征: {epl["features"][-5:]}')
else:
    print('  英超模型未加载!')

# 构造一场英超比赛的 odds
odds_data = {
    'wdl_odds': {
        'records': [{'win': 2.1, 'draw': 3.4, 'lose': 3.2}],
        'open': {'win': 2.2, 'draw': 3.3, 'lose': 3.1},
        'close': {'win': 2.1, 'draw': 3.4, 'lose': 3.2},
    },
    'handicap_odds': {
        'line': 0, 'records': [{'win': 2.0, 'draw': 3.3, 'lose': 3.0}],
        'open': {}, 'close': {'win': 2.0, 'draw': 3.3, 'lose': 3.0},
    },
    'score_odds': {'records': []},
    'tg_odds': {'records': []},
}

match = {
    'home_team': 'Liverpool', 'away_team': 'Manchester City',
    'home_team_cn': '利物浦', 'away_team_cn': '曼城',
    'league': '英超',
}

predictor = WDLPredictor(models['wdl'], epl_model=models.get('epl'))
predictor._cached_df = None
predictor._cached_X = None

print()
print('='*60)
print('英超模型推理测试 (非 mock, 真实 odds.db)')
print('='*60)
result = predictor._predict_epl(match, odds_data)
if result:
    print()
    print('='*60)
    print('验证通过! 英超模型输出:')
    print(f'  主胜: {result["win"]*100:.1f}%')
    print(f'  平局: {result["draw"]*100:.1f}%')
    print(f'  客胜: {result["lose"]*100:.1f}%')
    print('='*60)
else:
    print()
    print('='*60)
    print('[FAIL] _predict_epl() 返回 None，请检查上方日志')
    print('='*60)