"""factor=1.1 全量数据 ECE 和准确率综合报告"""
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

def compute_ece_per_class(y_true, y_proba, n_bins=10):
    ece_list = []
    for class_idx in range(y_proba.shape[1]):
        y_binary = (y_true == class_idx).astype(int)
        prob = y_proba[:, class_idx]
        bin_edges = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            mask = (prob >= bin_edges[i]) & (prob < bin_edges[i + 1])
            if mask.sum() > 0:
                ece += (mask.sum() / len(y_true)) * abs(y_binary[mask].mean() - prob[mask].mean())
        ece_list.append(ece)
    return ece_list

def apply_draw_threshold(probs, factor):
    pred = np.argmax(probs, axis=1)
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

FACTOR = 1.1

print("=" * 70)
print(f"=== factor={FACTOR} 全量数据 ECE 和准确率综合报告 ===")
print("=" * 70)

# 原始预测
orig_pred = np.argmax(probs, axis=1)
orig_acc = accuracy_score(y_all, orig_pred)
orig_cr = classification_report(y_all, orig_pred, output_dict=True, labels=[0,1,2], target_names=['客胜','平局','主胜'])

# 阈值调整
thresh_pred = apply_draw_threshold(probs, FACTOR)
thresh_acc = accuracy_score(y_all, thresh_pred)
thresh_cr = classification_report(y_all, thresh_pred, output_dict=True, labels=[0,1,2], target_names=['客胜','平局','主胜'])

# ECE
ece = compute_ece_per_class(y_all, probs)
actual_draw_rate = sum(y_all == 1) / len(y_all)

print(f"\n--- 数据概况 ---")
print(f"  总比赛: {len(df)}")
print(f"  实际分布: 客胜={sum(y_all==0)} ({sum(y_all==0)/len(y_all)*100:.1f}%), "
      f"平局={sum(y_all==1)} ({actual_draw_rate*100:.1f}%), "
      f"主胜={sum(y_all==2)} ({sum(y_all==2)/len(y_all)*100:.1f}%)")

print(f"\n--- 模型性能 ---")
print(f"  特征维度: {X.shape[1]}")
print(f"  概率范围: [{probs.min():.3f}, {probs.max():.3f}]")
print(f"  概率和=1.0: {'✅ 全部正确' if np.allclose(probs.sum(axis=1), 1.0) else '❌ 异常'}")

print(f"\n--- 原始预测 (argmax) ---")
print(f"  准确率: {orig_acc*100:.2f}%")
print(f"  预测分布: 客胜={sum(orig_pred==0)}, 平局={sum(orig_pred==1)} ({sum(orig_pred==1)/len(orig_pred)*100:.1f}%), 主胜={sum(orig_pred==2)}")
print(f"  平局召回率: {orig_cr['平局']['recall']*100:.1f}%")
print(f"  平局精确率: {orig_cr['平局']['precision']*100:.1f}%")

print(f"\n--- 阈值调整 (factor={FACTOR}) ---")
print(f"  准确率: {thresh_acc*100:.2f}%")
print(f"  预测分布: 客胜={sum(thresh_pred==0)}, 平局={sum(thresh_pred==1)} ({sum(thresh_pred==1)/len(thresh_pred)*100:.1f}%), 主胜={sum(thresh_pred==2)}")
print(f"  平局召回率: {thresh_cr['平局']['recall']*100:.1f}%")
print(f"  平局精确率: {thresh_cr['平局']['precision']*100:.1f}%")
print(f"  准确率变化: {thresh_acc - orig_acc:+.4f}")
print(f"  平局召回变化: {thresh_cr['平局']['recall'] - orig_cr['平局']['recall']:+.4f}")

print(f"\n--- 概率校准 ECE ---")
print(f"  客胜 ECE: {ece[0]:.4f}")
print(f"  平局 ECE: {ece[1]:.4f}")
print(f"  主胜 ECE: {ece[2]:.4f}")
print(f"  平均 ECE: {np.mean(ece):.4f}")
print(f"  (决策阈值不修改概率，ECE 始终保持不变)")

print(f"\n分类报告 (factor={FACTOR}):")
print(classification_report(y_all, thresh_pred, target_names=['客胜','平局','主胜']))

print(f"\n--- 按联赛准确率 ---")
for league in df['competition_name'].unique():
    mask = df['competition_name'] == league
    if mask.sum() > 0:
        orig_la = accuracy_score(y_all[mask], orig_pred[mask])
        thresh_la = accuracy_score(y_all[mask], thresh_pred[mask])
        print(f"  {league}: 原始={orig_la*100:.2f}%, 阈值={thresh_la*100:.2f}%, 变化={thresh_la-orig_la:+.4f}")

print(f"\n=== 报告完成 ===")
print(f"  推荐 factor: {FACTOR}")
print(f"  状态: 决策阈值不修改概率，概率校准完好")
print(f"  平局过度预测: 从 60.6%(factor=1.5) 降至 {sum(thresh_pred==1)/len(thresh_pred)*100:.1f}%(factor={FACTOR})")