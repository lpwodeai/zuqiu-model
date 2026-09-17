"""
Q5: 导出14场165维特征数据 + 重跑XGB修复后预测
"""
import json, os, sys, warnings, time, sqlite3, csv
import numpy as np
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from prediction_core import init_models, PredictionCore
from generate_unified_report import parse_odds_txt_auto
from feature_utils import normalize_team_name, build_all_features, load_match_data_odds
import pandas as pd

TARGET_MATCHES = [
    ('2026-08-22 22:00', '英超', '埃弗顿', '水晶宫', '英超'),
    ('2026-08-22 22:00', '英超', '诺丁汉森林', '利兹联', '英超'),
    ('2026-08-22 22:00', '英超', '伊普斯维奇', '桑德兰', '英超'),
    ('2026-08-22 23:00', '西甲', '毕尔巴鄂竞技', '塞维利亚', '西甲'),
    ('2026-08-22 23:15', '法甲', '朗斯', '欧塞尔', '法甲'),
    ('2026-08-23 00:30', '英超', '布伦特福德', '托特纳姆热刺', '英超'),
    ('2026-08-23 00:30', '意甲', '乌迪内斯', '科莫', '意甲'),
    ('2026-08-23 00:30', '意甲', '国际米兰', '蒙扎', '意甲'),
    ('2026-08-23 01:30', '西甲', '巴伦西亚', '维戈塞尔塔', '西甲'),
    ('2026-08-23 02:45', '意甲', '热那亚', '那不勒斯', '意甲'),
    ('2026-08-23 02:45', '意甲', '帕尔马', '卡利亚里', '意甲'),
    ('2026-08-23 02:45', '法甲', '尼斯', '洛里昂', '法甲'),
    ('2026-08-23 02:45', '法甲', '图卢兹', '里昂', '法甲'),
    ('2026-08-23 03:30', '西甲', '西班牙人', '皇家马德里', '西甲'),
]

txt_files = {
    '英超': 'data/英超2026-2027赛季完整时序赔率.txt',
    '西甲': 'data/西甲2026-2027赛季完整时序赔率.txt',
    '法甲': 'data/法甲2026-2027赛季完整时序赔率.txt',
    '意甲': 'data/意甲2026-2027赛季完整时序赔率.txt',
}

# 1. 加载 TXT 赔率
all_txt_matches = []
for league, fpath in txt_files.items():
    full_path = os.path.join(PROJECT_DIR, fpath)
    if os.path.exists(full_path):
        all_txt_matches.extend(parse_odds_txt_auto(full_path))

# 2. 加载模型 (含 XGB 修复)
print("加载模型...")
models = init_models()
core = PredictionCore(models)

# 3. 加载特征矩阵用于导出
print("加载特征矩阵...")
df = load_match_data_odds()
X, _ = build_all_features(df, include_odds=True)
features = models['wdl']['features']
scaler = models['wdl']['scaler']
lgb = models['wdl']['lgb_model']
xgb_model = models['wdl']['xgb_model']
print(f"特征矩阵: {X.shape}, 特征数: {len(features)}")

# 4. 逐场预测 + 导出特征
results = []
feature_rows = []
fieldnames = ['match', 'league', 'home', 'away', 'match_idx', 'features_available',
              'features_expected', 'match_count', 'lgb_win', 'lgb_draw', 'lgb_lose',
              'xgb_win', 'xgb_draw', 'xgb_lose', 'xgb_status']

for i, (match_time, league, home_cn, away_cn, txt_league) in enumerate(TARGET_MATCHES):
    home_norm = normalize_team_name(home_cn)
    away_norm = normalize_team_name(away_cn)

    matched_odds = None
    for tm in all_txt_matches:
        h = normalize_team_name(tm['home_short'])
        a = normalize_team_name(tm['away_short'])
        if h == home_norm and a == away_norm:
            matched_odds = tm
            break
        if h == away_norm and a == home_norm:
            matched_odds = tm
            break

    if not matched_odds:
        print(f"[{i+1}/14] {home_cn} vs {away_cn} - TXT无匹配, 跳过")
        continue

    odds_data = matched_odds['odds_data']
    match = {
        'home_team': home_norm, 'away_team': away_norm,
        'home_team_cn': home_cn, 'away_team_cn': away_cn,
        'league': league, 'match_time': match_time,
    }

    # 找特征矩阵中的匹配
    mask = (df['home_team_name'] == home_norm) | (df['away_team_name'] == away_norm)
    match_idx = df[mask].index[-1] if mask.sum() > 0 else -1
    available = []
    if match_idx >= 0 and match_idx < len(X):
        X_match = X.iloc[match_idx:match_idx+1]
        available = [f for f in features if f in X_match.columns]
    
    feat_row = {
        'match': f'{home_cn} vs {away_cn}',
        'league': league,
        'home': home_cn,
        'away': away_cn,
        'match_idx': match_idx,
        'features_available': len(available),
        'features_expected': len(features) if features else 0,
        'match_count': int(mask.sum()),
    }

    # LGB 推理
    lgb_win = lgb_draw = lgb_lose = None
    if lgb is not None and scaler is not None and len(available) == scaler.n_features_in_:
        X_vec = X_match[available].values
        X_scaled = scaler.transform(X_vec)
        lgb_raw = lgb.predict(X_scaled)[0]
        lgb_win, lgb_draw, lgb_lose = lgb_raw[2], lgb_raw[1], lgb_raw[0]
    
    feat_row['lgb_win'] = lgb_win
    feat_row['lgb_draw'] = lgb_draw
    feat_row['lgb_lose'] = lgb_lose

    # XGB 推理 (修复后)
    xgb_win = xgb_draw = xgb_lose = None
    xgb_status = 'skipped'
    if xgb_model is not None and scaler is not None and len(available) == scaler.n_features_in_:
        try:
            import xgboost as _xgb
            X_vec = X_match[available].values
            X_scaled = scaler.transform(X_vec)
            xgb_dmat = _xgb.DMatrix(X_scaled)
            xgb_raw = xgb_model.predict(xgb_dmat)[0]
            xgb_win, xgb_draw, xgb_lose = xgb_raw[2], xgb_raw[1], xgb_raw[0]
            xgb_status = 'ok'
        except Exception as e:
            xgb_status = f'error: {e}'
    
    feat_row['xgb_win'] = xgb_win
    feat_row['xgb_draw'] = xgb_draw
    feat_row['xgb_lose'] = xgb_lose
    feat_row['xgb_status'] = xgb_status

    feature_rows.append(feat_row)

    # 预测
    try:
        result = core.predict_unified(match, odds_data, is_mock=False)
        wdl = result.get('wdl', {})
        hcp = result.get('hcp', {})
        score = result.get('score', {})
        tg = result.get('tg', {})
        
        print(f"\n[{i+1}/14] {home_cn} vs {away_cn} ({league})")
        print(f"  WDL: {wdl.get('prediction')} ({wdl.get('confidence',0)*100:.1f}%) method={wdl.get('method','N/A')}")
        print(f"  LGB: {lgb_win*100:.1f}%/{lgb_draw*100:.1f}%/{lgb_lose*100:.1f}%" if lgb_win else "  LGB: N/A")
        print(f"  XGB: {xgb_win*100:.1f}%/{xgb_draw*100:.1f}%/{xgb_lose*100:.1f}% [{xgb_status}]" if xgb_win else f"  XGB: N/A [{xgb_status}]")
        print(f"  比分: {score.get('most_likely')} ({score.get('most_likely_prob',0)*100:.1f}%)")
        
        results.append({
            'home': home_cn, 'away': away_cn, 'league': league,
            'match_time': match_time,
            'wdl': wdl, 'handicap': hcp, 'score': score, 'total_goals': tg,
            'lgb': {'win': lgb_win, 'draw': lgb_draw, 'lose': lgb_lose},
            'xgb': {'win': xgb_win, 'draw': xgb_draw, 'lose': xgb_lose, 'status': xgb_status},
        })
    except Exception as e:
        print(f"  ❌ 预测失败: {e}")
        import traceback; traceback.print_exc()

# 5. 保存特征 CSV
csv_path = os.path.join(PROJECT_DIR, 'data', 'features_14_matches_20260823.csv')
with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(feature_rows)
print(f"\n特征数据已导出: {csv_path}")

# 6. 保存预测结果
output_path = os.path.join(PROJECT_DIR, 'data', 'predictions_14_matches_20260823_v4.json')
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump({
        'model_version': 'v4.0 (XGB fixed)',
        'prediction_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'predictions': results,
        'feature_summary': {r['match']: {k: r[k] for k in ['features_available', 'features_expected', 'match_count', 'lgb_win', 'lgb_draw', 'lgb_lose', 'xgb_win', 'xgb_draw', 'xgb_lose', 'xgb_status']} for r in feature_rows},
    }, f, ensure_ascii=False, indent=2, default=str)
print(f"预测结果已保存: {output_path}")

# 7. 汇总
print(f"\n{'='*60}")
print(f"完成: {len(results)}/14 场预测")
print("XGB 状态汇总:")
for r in feature_rows:
    print(f"  {r['match']:40s} LGB={r['lgb_win']*100:.1f}%/{r['lgb_draw']*100:.1f}%/{r['lgb_lose']*100:.1f}%  XGB={r['xgb_win']*100:.1f}%/{r['xgb_draw']*100:.1f}%/{r['xgb_lose']*100:.1f}% [{r['xgb_status']}]" if r['xgb_win'] else f"  {r['match']:40s} XGB=[{r['xgb_status']}]")