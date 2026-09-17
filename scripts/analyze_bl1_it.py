"""德甲(BL1)与意甲(IT)调优分析脚本 (C-20260816-205)
1. 逐联赛错误分析
2. 专属阈值校准
3. 基于英超优化经验的调优建议
"""
import os, sys, json, pickle, numpy as np, warnings, joblib, pandas as pd
warnings.filterwarnings('ignore')

# Path setup
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from collections import Counter

# Load data
print("=" * 60)
print("加载全量数据...")
df = load_match_data_odds()
print(f"全量: {len(df)} 场")

# League mapping
LEAGUE_IDS = {
    'BL1': '德甲',
    'IT': '意甲',
    'PL': '英超',
    'LaLiga': '西甲',
    'FL1': '法甲',
}

# Load pretrained model
model_path = os.path.join(PROJECT_DIR, 'assets', 'lgb_model_20260815_215015.pkl')
if not os.path.exists(model_path):
    model_path = max(
        [os.path.join(PROJECT_DIR, 'assets', f) for f in os.listdir(os.path.join(PROJECT_DIR, 'assets'))
         if f.startswith('lgb_model_') and f.endswith('.pkl')],
        key=os.path.getctime
    )
print(f"模型: {os.path.basename(model_path)}")
model = pickle.load(open(model_path, 'rb'))

# Load scaler
scaler_path = max(
    [os.path.join(PROJECT_DIR, 'assets', f) for f in os.listdir(os.path.join(PROJECT_DIR, 'assets'))
     if f.startswith('scaler_') and f.endswith('.pkl')],
    key=os.path.getctime
)
scaler = joblib.load(scaler_path)

# Load selected features
sf_path = max(
    [os.path.join(PROJECT_DIR, 'assets', f) for f in os.listdir(os.path.join(PROJECT_DIR, 'assets'))
     if f.startswith('selected_features_') and f.endswith('.pkl')],
    key=os.path.getctime
)
selected_features = pickle.load(open(sf_path, 'rb'))

# ============================================================
# 1. Per-league performance analysis
# ============================================================
print("\n" + "=" * 60)
print("一、逐联赛预测性能")
print("=" * 60)

results = {}
for league_code in ['BL1', 'IT', 'PL', 'LaLiga', 'FL1']:
    league_name = LEAGUE_IDS[league_code]
    league_df = df[df['competition_name'].str.contains(league_name, na=False)].copy()
    if len(league_df) == 0:
        print(f"  {league_name}: 无数据")
        continue
    
    X, y = build_all_features(league_df, include_odds=True)
    # Align to selected_features: fill missing with 0
    X_aligned = pd.DataFrame(0, index=range(len(X)), columns=selected_features)
    for col in selected_features:
        if col in X.columns:
            X_aligned[col] = X[col].values
    X_s = scaler.transform(X_aligned)
    probs = model.predict(X_s)  # Booster.predict returns probabilities for softmax objective
    probs = probs.reshape(-1, 3)  # reshape from (n*3,) to (n, 3)
    pred = np.argmax(probs, axis=1)
    
    acc = accuracy_score(y, pred)
    cm = confusion_matrix(y, pred, labels=[0, 1, 2])
    report = classification_report(y, pred, output_dict=True, labels=[0, 1, 2], target_names=['客胜', '平局', '主胜'])
    
    # Analyze error types
    errors = []
    for i, (actual, predicted) in enumerate(zip(y, pred)):
        if actual != predicted:
            row = league_df.iloc[i]
            errors.append({
                'date': str(row.get('date', '')),
                'home': str(row.get('home_team', '')),
                'away': str(row.get('away_team', '')),
                'actual': int(actual),
                'predicted': int(predicted),
                'prob_max': float(probs[i].max()),
                'prob_home': float(probs[i][2]),
                'prob_draw': float(probs[i][1]),
                'prob_away': float(probs[i][0]),
            })
    
    results[league_code] = {
        'n': len(league_df),
        'accuracy': acc,
        'cm': cm.tolist(),
        'report': report,
        'errors': errors,
        'actual_dist': Counter(y),
        'pred_dist': Counter(pred),
    }
    
    print(f"\n{'='*40}")
    print(f"  {league_name} ({league_code}): {len(league_df)} 场, 准确率 {acc*100:.2f}%")
    print(f"  混淆矩阵:")
    print(f"                预测客胜  预测平局  预测主胜")
    print(f"    实际客胜:      {cm[0,0]:4d}      {cm[0,1]:4d}      {cm[0,2]:4d}")
    print(f"    实际平局:      {cm[1,0]:4d}      {cm[1,1]:4d}      {cm[1,2]:4d}")
    print(f"    实际主胜:      {cm[2,0]:4d}      {cm[2,1]:4d}      {cm[2,2]:4d}")
    print(f"  实际分布: 客胜={results[league_code]['actual_dist'][0]:.1%}, 平局={results[league_code]['actual_dist'][1]:.1%}, 主胜={results[league_code]['actual_dist'][2]:.1%}")
    print(f"  预测分布: 客胜={results[league_code]['pred_dist'][0]:.1%}, 平局={results[league_code]['pred_dist'][1]:.1%}, 主胜={results[league_code]['pred_dist'][2]:.1%}")

# ============================================================
# 2. Draw threshold factor calibration per league
# ============================================================
print("\n" + "=" * 60)
print("二、联赛专属 draw_threshold_factor 校准")
print("=" * 60)

def apply_draw_threshold(probs, factor):
    if factor <= 0:
        return np.argmax(probs, axis=1)
    pred = np.argmax(probs, axis=1)
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

for league_code in ['BL1', 'IT']:
    if league_code not in results:
        continue
    league_name = LEAGUE_IDS[league_code]
    league_df = df[df['competition_name'].str.contains(league_name, na=False)]
    X, y = build_all_features(league_df, include_odds=True)
    # Align to selected_features: fill missing with 0
    X_aligned = pd.DataFrame(0, index=range(len(X)), columns=selected_features)
    for col in selected_features:
        if col in X.columns:
            X_aligned[col] = X[col].values
    X_s = scaler.transform(X_aligned)
    probs = model.predict(X_s)  # Booster.predict returns probabilities for softmax objective
    probs = probs.reshape(-1, 3)  # reshape from (n*3,) to (n, 3)
    
    actual_draw_rate = (y == 1).mean()
    
    print(f"\n  {league_name} ({league_code}): 实际平局率 {actual_draw_rate:.1%}")
    print(f"  {'factor':>8}  {'准确率':>8}  {'平局召回':>10}  {'平局预测率':>10}  {'主胜预测率':>10}")
    print(f"  {'-'*54}")
    
    best_factor = 0.0
    best_acc = results[league_code]['accuracy']
    
    for factor in [0.0, 0.5, 0.7, 0.9, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.5]:
        thresh_pred = apply_draw_threshold(probs, factor)
        acc = accuracy_score(y, thresh_pred)
        draw_recall = (thresh_pred[y == 1] == 1).mean() if (y == 1).sum() > 0 else 0
        draw_pred_rate = (thresh_pred == 1).mean()
        home_pred_rate = (thresh_pred == 2).mean()
        
        marker = " <-- 全局" if factor == 1.1 else ""
        if factor == 0.0:
            marker = " <-- argmax"
        
        print(f"  {factor:>8.2f}  {acc*100:>7.2f}%  {draw_recall*100:>9.1f}%  {draw_pred_rate*100:>9.1f}%  {home_pred_rate*100:>9.1f}%{marker}")
        
        if acc > best_acc:
            best_acc = acc
            best_factor = factor
    
    results[league_code]['best_factor'] = best_factor
    results[league_code]['best_acc'] = best_acc
    print(f"\n  >>> 推荐 factor={best_factor}, 准确率={best_acc*100:.2f}% (基线={results[league_code]['accuracy']*100:.2f}%)")

# ============================================================
# 3. High-confidence errors analysis
# ============================================================
print("\n" + "=" * 60)
print("三、高置信度错误 (prob_max > 0.45)")
print("=" * 60)

for league_code in ['BL1', 'IT']:
    if league_code not in results:
        continue
    league_name = LEAGUE_IDS[league_code]
    errs = results[league_code]['errors']
    high_conf = [e for e in errs if e['prob_max'] > 0.45]
    print(f"\n  {league_name}: {len(high_conf)} 场高置信度错误 (共 {len(errs)} 场错误)")
    
    if high_conf:
        # Top 10
        high_conf.sort(key=lambda x: x['prob_max'], reverse=True)
        for e in high_conf[:10]:
            labels = ['客胜', '平局', '主胜']
            print(f"    {e['date']} {e['home']} vs {e['away']} | "
                  f"实际:{labels[e['actual']]} 预测:{labels[e['predicted']]} | "
                  f"prob={e['prob_max']:.3f} (客{e['prob_away']:.3f}/平{e['prob_draw']:.3f}/主{e['prob_home']:.3f})")

# ============================================================
# 4. Feature distribution comparison
# ============================================================
print("\n" + "=" * 60)
print("四、特征分布差异 (联赛 vs 全量)")
print("=" * 60)

all_df = df.copy()
X_all, y_all = build_all_features(all_df, include_odds=True)
X_all = X_all[selected_features] if hasattr(X_all, 'columns') else X_all

key_features = [
    'score_expected_goals', 'score_entropy', 'score_over_25_prob',
    'home_games_played', 'home_recent_form', 'away_recent_form',
    'h2h_home_win_rate', 'h2h_away_win_rate', 'h2h_draw_rate',
    'odds_home_win_implied', 'odds_draw_implied', 'odds_away_win_implied',
]

available_features = [f for f in key_features if f in X_all.columns]

for league_code in ['BL1', 'IT']:
    if league_code not in results:
        continue
    league_name = LEAGUE_IDS[league_code]
    league_df = df[df['competition_name'].str.contains(league_name, na=False)]
    X_lg, _ = build_all_features(league_df, include_odds=True)
    X_lg = X_lg[selected_features] if hasattr(X_lg, 'columns') else X_lg
    
    print(f"\n  {league_name}:")
    print(f"  {'特征':<28} {'联赛均值':>10} {'全量均值':>10} {'差异':>10}")
    print(f"  {'-'*60}")
    
    for feat in available_features:
        if feat in X_lg.columns:
            lg_mean = X_lg[feat].mean()
            all_mean = X_all[feat].mean()
            diff = lg_mean - all_mean
            marker = " ***" if abs(diff) > all_mean * 0.15 else ""
            print(f"  {feat:<28} {lg_mean:>10.4f} {all_mean:>10.4f} {diff:>+10.4f}{marker}")

# ============================================================
# 5. Recommendations
# ============================================================
print("\n" + "=" * 60)
print("五、调优建议")
print("=" * 60)

for league_code in ['BL1', 'IT']:
    if league_code not in results:
        continue
    league_name = LEAGUE_IDS[league_code]
    r = results[league_code]
    cm = r['cm']
    errors = r['errors']
    
    # Error type breakdown
    draw_overpredicted = cm[0,1] + cm[2,1]  # 非平局被预测为平局
    draw_missed = cm[1,0] + cm[1,2]  # 平局未被预测
    home_away_swap = cm[0,2] + cm[2,0]  # 主客胜互换
    total_errors = len(errors)
    
    # Actual vs predicted distribution
    actual_home = r['actual_dist'][2] / sum(r['actual_dist'].values()) if sum(r['actual_dist'].values()) > 0 else 0
    pred_home = r['pred_dist'][2] / sum(r['pred_dist'].values()) if sum(r['pred_dist'].values()) > 0 else 0
    actual_draw = r['actual_dist'][1] / sum(r['actual_dist'].values()) if sum(r['actual_dist'].values()) > 0 else 0
    pred_draw = r['pred_dist'][1] / sum(r['pred_dist'].values()) if sum(r['pred_dist'].values()) > 0 else 0
    
    print(f"\n  {'='*50}")
    print(f"  {league_name} ({league_code}) 调优建议")
    print(f"  {'='*50}")
    print(f"  当前准确率: {r['accuracy']*100:.2f}% (全量: 53.50%)")
    print(f"  推荐 factor: {r.get('best_factor', 'N/A')} → 准确率: {r.get('best_acc', r['accuracy'])*100:.2f}%")
    print(f"")
    print(f"  错误分布 ({total_errors} 场):")
    print(f"    平局误判 (预测平局但实际非平局): {draw_overpredicted} 场 ({draw_overpredicted/total_errors*100:.1f}%)")
    print(f"    平局漏判 (实际平局但预测非平局): {draw_missed} 场 ({draw_missed/total_errors*100:.1f}%)")
    print(f"    主客胜互换: {home_away_swap} 场 ({home_away_swap/total_errors*100:.1f}%)")
    print(f"")
    print(f"  分布偏差:")
    print(f"    主胜: 预测 {pred_home:.1%} vs 实际 {actual_home:.1%} (偏差 {pred_home-actual_home:+.1%})")
    print(f"    平局: 预测 {pred_draw:.1%} vs 实际 {actual_draw:.1%} (偏差 {pred_draw-actual_draw:+.1%})")
    
    # Specific recommendations based on patterns
    print(f"")
    print(f"  建议:")
    
    if league_code == 'BL1':
        # BL1 is best performing, minimal changes needed
        print(f"    1. [P3-低优先级] 德甲已是表现最好的联赛 (55.66%)，无需独立模型")
        print(f"    2. factor={r.get('best_factor', 1.1)} 阈值即可，无需大幅调整")
        print(f"    3. 重点关注高置信度错误的球队特征，优化球员级特征")
        print(f"    4. 德甲数据充足 (3赛季)，可作为全局模型的核心训练集")
        
    elif league_code == 'IT':
        # IT is mid-tier, could benefit from minor adjustments
        print(f"    1. [P2] 意甲 factor 校准: 推荐 factor={r.get('best_factor', 1.1)}")
        print(f"    2. 平局预测偏差: 当前平局预测率 {pred_draw:.1%} vs 实际 {actual_draw:.1%}")
        if draw_overpredicted > draw_missed:
            print(f"       → 降低平局阈值 (下降 factor)，减少平局误判")
        else:
            print(f"       → 维持或提高平局阈值")
        print(f"    3. 意甲 CV 0.4868，与全局模型差距不大，无需独立模型")
        print(f"    4. 建议: 专属 factor + 联赛权重微调 (当前 league_weight=1.20)")
        print(f"    5. 如需独立模型，数据量 {r['n']} 场为英超的 {r['n']/1141*100:.0f}%，正则化参数参考英超 v2.0")

# Save results
out_path = os.path.join(PROJECT_DIR, 'docs', 'bl1_it_optimization_analysis.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != 'errors'} for k, v in results.items()}, f, indent=2, ensure_ascii=False)
print(f"\n\n分析结果已保存: {out_path}")