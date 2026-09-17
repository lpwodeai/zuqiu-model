"""法甲专属 draw_threshold_factor 校准
   
问题: 法甲平局实际率 23.8% < 全量 25.6%，全局 factor=1.1 导致
      法甲平局预测率 29.6% (过高)，平局误判 172 场 (38.7% of errors)
      
目标: 为法甲找到最优 draw_threshold_factor，降低平局误判
"""
import os, sys, pickle, joblib, numpy as np, warnings
warnings.filterwarnings('ignore')

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

lgb = pickle.load(open(os.path.join(BASE, 'lgb_model_20260815_215015.pkl'), 'rb'))
sf = pickle.load(open(os.path.join(BASE, 'selected_features_20260815_215015.pkl'), 'rb'))
scaler = joblib.load(os.path.join(BASE, 'scaler_20260815_215015.pkl'))

from feature_utils import load_match_data_odds, build_all_features
from sklearn.metrics import accuracy_score, classification_report

df = load_match_data_odds()
X_all, y_all = build_all_features(df, include_odds=True)
X = X_all[sf]
X_scaled = scaler.transform(X)
probs = lgb.predict(X_scaled)

def apply_draw_threshold(probs, factor):
    pred = np.argmax(probs, axis=1)
    if factor <= 0:
        return pred  # factor=0 等价于 argmax
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

# 法甲筛选
l1_mask = df['competition_name'].str.contains('法甲', na=False)
y_l1 = np.array(y_all[l1_mask.values])
probs_l1 = np.array(probs[l1_mask.values])
actual_draw_l1 = sum(y_l1==1) / len(y_l1)
actual_home_l1 = sum(y_l1==2) / len(y_l1)
actual_away_l1 = sum(y_l1==0) / len(y_l1)

print("=" * 75)
print("=== 法甲 (Ligue 1) 专属 draw_threshold_factor 校准 ===")
print("=" * 75)
print(f"\n法甲数据: {len(y_l1)} 场")
print(f"实际分布: 客胜={actual_away_l1*100:.1f}%, 平局={actual_draw_l1*100:.1f}%, 主胜={actual_home_l1*100:.1f}%")

# 原始 argmax 基线
orig_pred = np.argmax(probs_l1, axis=1)
orig_acc = accuracy_score(y_l1, orig_pred)
orig_cr = classification_report(y_l1, orig_pred, output_dict=True, labels=[0,1,2], target_names=['客胜','平局','主胜'])
print(f"\n原始 argmax: 准确率={orig_acc*100:.2f}%, 平局召回={orig_cr['平局']['recall']*100:.1f}%, "
      f"平局预测率={sum(orig_pred==1)/len(orig_pred)*100:.1f}%")

# 全量 factor=1.1 在法甲上的表现
global_pred = apply_draw_threshold(probs_l1, 1.1)
global_acc = accuracy_score(y_l1, global_pred)
global_cr = classification_report(y_l1, global_pred, output_dict=True, labels=[0,1,2], target_names=['客胜','平局','主胜'])
print(f"全量 factor=1.1: 准确率={global_acc*100:.2f}%, 平局召回={global_cr['平局']['recall']*100:.1f}%, "
      f"平局预测率={sum(global_pred==1)/len(global_pred)*100:.1f}%")

# === 搜索最优 factor ===
print(f"\n--- 网格搜索 (factor 0.0 ~ 2.0) ---")
print(f"{'factor':>8} {'准确率':>8} {'平局召回':>8} {'平局精确':>8} {'平局预测率':>10} {'主胜预测率':>10} {'客胜预测率':>10}")
print("-" * 80)

results = []
for factor in [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 1.7, 2.0]:
    pred = apply_draw_threshold(probs_l1, factor)
    acc = accuracy_score(y_l1, pred)
    cr = classification_report(y_l1, pred, output_dict=True, labels=[0,1,2], target_names=['客胜','平局','主胜'])
    draw_recall = cr['平局']['recall']
    draw_precision = cr['平局']['precision']
    draw_rate = sum(pred == 1) / len(pred)
    home_rate = sum(pred == 2) / len(pred)
    away_rate = sum(pred == 0) / len(pred)
    
    marker = " <-- 全局" if factor == 1.1 else ""
    print(f"{factor:>8.1f} {acc*100:>8.2f}% {draw_recall*100:>8.1f}% {draw_precision*100:>8.1f}% "
          f"{draw_rate*100:>10.1f}% {home_rate*100:>10.1f}% {away_rate*100:>10.1f}%{marker}")
    
    results.append({
        'factor': factor, 'accuracy': acc, 'draw_recall': draw_recall,
        'draw_precision': draw_precision, 'draw_rate': draw_rate,
        'home_rate': home_rate, 'away_rate': away_rate
    })

# === 推荐策略 ===
print(f"\n--- 推荐策略 ---")

# 策略1: 平局预测率最接近实际平局率
closest = min(results, key=lambda r: abs(r['draw_rate'] - actual_draw_l1))
print(f"策略1 (平局率匹配): factor={closest['factor']:.1f}, "
      f"平局预测率={closest['draw_rate']*100:.1f}% (实际{actual_draw_l1*100:.1f}%), "
      f"准确率={closest['accuracy']*100:.2f}%")

# 策略2: 平衡准确率+平局召回
best = max(results, key=lambda r: (r['accuracy'] + r['draw_recall']) / 2)
print(f"策略2 (准确率+平局召回): factor={best['factor']:.1f}, "
      f"准确率={best['accuracy']*100:.2f}%, 平局召回={best['draw_recall']*100:.1f}%")

# 策略3: 最高准确率（忽略平局）
best_acc = max(results, key=lambda r: r['accuracy'])
print(f"策略3 (最高准确率): factor={best_acc['factor']:.1f}, "
      f"准确率={best_acc['accuracy']*100:.2f}%, 平局预测率={best_acc['draw_rate']*100:.1f}%")

# === 对比：法甲 factor 对全量联赛的影响 ===
print(f"\n--- 假如法甲用专属 factor，对全量准确率的影响 ---")
for league_name in ['英超', '西甲', '意甲', '德甲', '法甲']:
    mask = df['competition_name'].str.contains(league_name, na=False)
    y_l = np.array(y_all[mask.values])
    p_l = np.array(probs[mask.values])
    
    # 全量 factor=1.1
    pred_global = apply_draw_threshold(p_l, 1.1)
    acc_global = accuracy_score(y_l, pred_global)
    
    # 法甲用专属 factor，其他用 1.1
    if league_name == '法甲':
        pred_local = apply_draw_threshold(p_l, closest['factor'])
    else:
        pred_local = apply_draw_threshold(p_l, 1.1)
    acc_local = accuracy_score(y_l, pred_local)
    
    print(f"  {league_name} ({mask.sum()}场): 全量factor=1.1 → {acc_global*100:.2f}%, "
          f"专属factor → {acc_local*100:.2f}% "
          f"({'法甲用' + str(closest['factor']) if league_name == '法甲' else '用1.1'})")

# === 结论 ===
print(f"\n{'='*75}")
print(f"=== 结论 ===")
print(f"  法甲平局实际率: {actual_draw_l1*100:.1f}% (全量 {sum(y_all==1)/len(y_all)*100:.1f}%)")
print(f"  全局 factor=1.1 在法甲: 平局预测率 {sum(global_pred==1)/len(global_pred)*100:.1f}%, 准确率 {global_acc*100:.2f}%")
print(f"  推荐法甲专属 factor: {closest['factor']:.1f}")
print(f"    平局预测率: {closest['draw_rate']*100:.1f}% → 接近实际 {actual_draw_l1*100:.1f}%")
print(f"    准确率: {closest['accuracy']*100:.2f}% (vs 全局 {global_acc*100:.2f}%)")
print(f"    平局召回: {closest['draw_recall']*100:.1f}%")
print(f"    主胜预测率: {closest['home_rate']*100:.1f}% (实际 {actual_home_l1*100:.1f}%)")
print(f"\n  实施方式: 在 prediction-service.js 中按联赛应用不同 factor")
print(f"    其他联赛: draw_threshold_factor = 1.1")
print(f"    法甲:     draw_threshold_factor = {closest['factor']:.1f}")
print(f"=" * 75)