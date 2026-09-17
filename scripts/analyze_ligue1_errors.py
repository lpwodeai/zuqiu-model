"""法甲低准确率专项分析 — 错误比赛拆解 + 数据分布对比"""
import os, sys, pickle, joblib, numpy as np, pandas as pd, warnings
warnings.filterwarnings('ignore')

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

lgb = pickle.load(open(os.path.join(BASE, 'lgb_model_20260815_215015.pkl'), 'rb'))
sf = pickle.load(open(os.path.join(BASE, 'selected_features_20260815_215015.pkl'), 'rb'))
scaler = joblib.load(os.path.join(BASE, 'scaler_20260815_215015.pkl'))

from feature_utils import load_match_data_odds, build_all_features
from sklearn.metrics import accuracy_score, confusion_matrix, classification_report

df = load_match_data_odds()
X_all, y_all = build_all_features(df, include_odds=True)
X = X_all[sf]
X_scaled = scaler.transform(X)
probs = lgb.predict(X_scaled)

FACTOR = 1.1
pred = np.argmax(probs, axis=1)
draw_mask = probs[:, 1] * FACTOR > np.maximum(probs[:, 0], probs[:, 2])
thresh_pred = pred.copy()
thresh_pred[draw_mask] = 1

# ========== 法甲筛选 ==========
ligue1_mask = df['competition_name'].str.contains('法甲', na=False)
df_l1 = df[ligue1_mask].reset_index(drop=True)
y_l1 = np.array(y_all[ligue1_mask.values])
pred_l1 = np.array(thresh_pred[ligue1_mask.values])
probs_l1 = np.array(probs[ligue1_mask.values])

print("=" * 70)
print("=== 法甲 (Ligue 1) 错误分析 ===")
print("=" * 70)

# ========== 基础统计 ==========
print(f"\n--- 基本数据 ---")
print(f"  比赛数: {len(df_l1)}")
print(f"  实际分布: 客胜={sum(y_l1==0)} ({sum(y_l1==0)/len(y_l1)*100:.1f}%), "
      f"平局={sum(y_l1==1)} ({sum(y_l1==1)/len(y_l1)*100:.1f}%), "
      f"主胜={sum(y_l1==2)} ({sum(y_l1==2)/len(y_l1)*100:.1f}%)")
print(f"  预测分布: 客胜={sum(pred_l1==0)}, 平局={sum(pred_l1==1)} ({sum(pred_l1==1)/len(pred_l1)*100:.1f}%), 主胜={sum(pred_l1==2)}")
print(f"  准确率: {accuracy_score(y_l1, pred_l1)*100:.2f}%")

# ========== 混淆矩阵 ==========
cm = confusion_matrix(y_l1, pred_l1, labels=[0,1,2])
print(f"\n--- 混淆矩阵 ---")
print(f"               预测客胜  预测平局  预测主胜")
for i, label in enumerate(['实际客胜', '实际平局', '实际主胜']):
    print(f"  {label}:     {cm[i,0]:5d}     {cm[i,1]:5d}     {cm[i,2]:5d}")

# ========== 错误类型细分 ==========
err_mask = y_l1 != pred_l1
n_errors = err_mask.sum()
print(f"\n--- 错误总数: {n_errors} ({n_errors/len(df_l1)*100:.1f}%) ---")

# 错误分类
error_types = []
for i in range(len(df_l1)):
    if not err_mask[i]:
        continue
    a, p = y_l1[i], pred_l1[i]
    if a == 1 and p != 1:
        error_types.append('平局漏判(预测非平)')
    elif a != 1 and p == 1:
        error_types.append('平局误判(预测平局)')
    elif a == 0:
        error_types.append('客胜→主胜' if p == 2 else '客胜→平局(阈值)')
    else:
        error_types.append('主胜→客胜' if p == 0 else '主胜→平局(阈值)')

from collections import Counter
etype_counts = Counter(error_types)
print(f"\n--- 错误类型分布 ---")
for etype, cnt in etype_counts.most_common():
    print(f"  {etype}: {cnt} ({cnt/n_errors*100:.1f}%)")

# ========== 高置信度错误 ==========
error_indices = np.where(err_mask)[0]
high_conf = []
for i in error_indices:
    pmax = max(probs_l1[i])
    if pmax > 0.45:
        high_conf.append((i, pmax))
high_conf.sort(key=lambda x: -x[1])

print(f"\n--- 高置信度错误 (prob_max > 0.45): {len(high_conf)} 场 ---")
print(f"{'日期':<12} {'主队':<20} {'客队':<20} {'实际':<6} {'预测':<6} {'客/平/主概率':<28} {'误差类型'}")
print("-" * 130)
for idx, pmax in high_conf[:20]:
    row = df_l1.iloc[idx]
    a, p = y_l1[idx], pred_l1[idx]
    act = ['客胜','平局','主胜'][a]
    pred_label = ['客胜','平局','主胜'][p]
    probs_str = f"{probs_l1[idx,0]:.3f}/{probs_l1[idx,1]:.3f}/{probs_l1[idx,2]:.3f}"
    date_str = str(row.get('date',''))[:10]
    ht = str(row.get('home_team_name','?'))[:20]
    at = str(row.get('away_team_name','?'))[:20]
    if a == 1 and p != 1: etype = '平局漏判(预测非平)'
    elif a != 1 and p == 1: etype = '平局误判(预测平局)'
    elif a == 0: etype = '客胜→主胜' if p == 2 else '客胜→平局(阈值)'
    else: etype = '主胜→客胜' if p == 0 else '主胜→平局(阈值)'
    print(f"{date_str:<12} {ht:<20} {at:<20} {act:<6} {pred_label:<6} {probs_str:<28} {etype}")

# ========== 概率分布分析 ==========
print(f"\n--- 法甲 vs 全量 概率分布对比 ---")
for label, idx in [('客胜',0), ('平局',1), ('主胜',2)]:
    l1_mean = probs_l1[:, idx].mean()
    all_mean = probs[:, idx].mean()
    print(f"  {label}概率: 法甲={l1_mean:.3f}±{probs_l1[:,idx].std():.3f}, 全量={all_mean:.3f}±{probs[:,idx].std():.3f}")

# 模型偏好
print(f"\n--- 模型预测偏好 (法甲 vs 全量) ---")
for label, idx in [('客胜',0), ('平局',1), ('主胜',2)]:
    l1_rate = sum(pred_l1 == idx) / len(pred_l1)
    actual_rate = sum(y_l1 == idx) / len(y_l1)
    all_pred_rate = sum(thresh_pred == idx) / len(thresh_pred)
    all_actual = sum(y_all == idx) / len(y_all)
    print(f"  {label}: 法甲预测={l1_rate*100:.1f}% | 法甲实际={actual_rate*100:.1f}% | 全量预测={all_pred_rate*100:.1f}% | 全量实际={all_actual*100:.1f}%")

# ========== 特征分布对比 ==========
X_l1 = X_all[sf].iloc[ligue1_mask.values].values
l1_feat_mean = np.mean(X_l1, axis=0)
all_feat_mean = np.mean(X_all[sf].values, axis=0)
feat_names = list(sf)
diff_series = pd.Series(np.abs(l1_feat_mean - all_feat_mean), index=feat_names)
diff = diff_series.sort_values(ascending=False)
print(f"\n--- 特征均值差异 TOP 10 (法甲 vs 全量) ---")
for feat in diff.head(10).index:
    idx = feat_names.index(feat)
    print(f"  {feat}: 法甲={l1_feat_mean[idx]:.4f}, 全量={all_feat_mean[idx]:.4f}, 差异={diff[feat]:.4f}")

# ========== 按赛季 ==========
df_l1['season'] = df_l1['date'].apply(lambda d: f"{d.year}-{d.year+1}" if d.month >= 8 else f"{d.year-1}-{d.year}")
print(f"\n--- 按赛季准确率 ---")
for season in sorted(df_l1['season'].unique()):
    mask = df_l1['season'] == season
    idxs = np.where(mask.values)[0]
    acc = accuracy_score(y_l1[idxs], pred_l1[idxs])
    n = len(idxs)
    print(f"  {season}: {n}场, 准确率={acc*100:.2f}%")

# ========== 总结 ==========
l1_acc = accuracy_score(y_l1, pred_l1)
all_acc = accuracy_score(y_all, thresh_pred)
print(f"\n{'='*70}")
print(f"=== 总结 ===")
print(f"法甲准确率 {l1_acc*100:.2f}% vs 全量 {all_acc*100:.2f}% (差距 {all_acc*100 - l1_acc*100:.2f}pp)")
print(f"核心发现:")
print(f"  1. 平局相关错误占 {etype_counts.get('平局误判(预测平局)',0) + etype_counts.get('平局漏判(预测非平)',0)}/{n_errors} ({100*(etype_counts.get('平局误判(预测平局)',0)+etype_counts.get('平局漏判(预测非平)',0))/n_errors:.0f}%)")
print(f"  2. 法甲平局实际 {sum(y_l1==1)/len(y_l1)*100:.1f}% < 全量 {sum(y_all==1)/len(y_all)*100:.1f}% - 法甲平局率偏低")
print(f"  3. 法甲主胜实际 {sum(y_l1==2)/len(y_l1)*100:.1f}% > 全量 {sum(y_all==2)/len(y_all)*100:.1f}% - 主胜更常见")
print(f"  4. 模型对法甲主胜预测不足 (-{sum(y_l1==2)/len(y_l1)*100 - sum(pred_l1==2)/len(pred_l1)*100:.1f}pp)")
print(f"建议: 考虑法甲专属 league_factor 或法甲独立模型 (类似英超)")
print(f"=" * 70)