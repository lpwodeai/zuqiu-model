"""P0/P1: 快速推理验证 — 加载现有模型，预测样本比赛"""
import os, sys, pickle, joblib, numpy as np, warnings
warnings.filterwarnings('ignore')

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载模型
lgb = pickle.load(open(os.path.join(BASE, 'lgb_model_20260815_215015.pkl'), 'rb'))
sf = pickle.load(open(os.path.join(BASE, 'selected_features_20260815_215015.pkl'), 'rb'))
scaler = joblib.load(os.path.join(BASE, 'scaler_20260815_215015.pkl'))

print(f"模型加载: LightGBM {lgb.num_feature()}维, 特征 {len(sf)}维")

# 加载数据
from feature_utils import load_match_data_odds, build_all_features

df = load_match_data_odds()
print(f"数据加载: {len(df)} 场")

X_all, y_all = build_all_features(df, include_odds=True)
print(f"特征构建: {X_all.shape[1]}维, 标签: {len(y_all)}")

# 对齐特征
X = X_all[sf]
print(f"特征对齐: {X.shape[1]}维 (匹配 selected_features)")

# 标准化
X_scaled = scaler.transform(X)
print(f"标准化完成: mean={X_scaled.mean():.4f}, std={X_scaled.std():.4f}")

# 预测
probs = lgb.predict(X_scaled)
print(f"预测概率: shape={probs.shape}, 范围=[{probs.min():.3f}, {probs.max():.3f}]")
print(f"概率和: min={probs.sum(axis=1).min():.4f}, max={probs.sum(axis=1).max():.4f}")

# 决策阈值调整
DRAW_THRESHOLD_FACTOR = 1.5
pred_raw = np.argmax(probs, axis=1)
draw_mask = probs[:, 1] * DRAW_THRESHOLD_FACTOR > np.maximum(probs[:, 0], probs[:, 2])
pred_thresh = pred_raw.copy()
pred_thresh[draw_mask] = 1

# 统计
from sklearn.metrics import accuracy_score, classification_report

print(f"\n=== 预测结果 ===")
print(f"原始预测: 主胜={sum(pred_raw==2)}, 平局={sum(pred_raw==1)}, 客胜={sum(pred_raw==0)}")
print(f"阈值调整: 主胜={sum(pred_thresh==2)}, 平局={sum(pred_thresh==1)}, 客胜={sum(pred_thresh==0)}")
print(f"平局变化: {sum(pred_raw==1)} -> {sum(pred_thresh==1)} (阈值调整激活 {sum(draw_mask)} 场)")

print(f"\n原始准确率: {accuracy_score(y_all, pred_raw)*100:.2f}%")
print(f"阈值准确率: {accuracy_score(y_all, pred_thresh)*100:.2f}%")

cr = classification_report(y_all, pred_thresh, target_names=['客胜','平局','主胜'])
print(f"\n阈值调整后分类报告:")
print(cr)

# ECE
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

ece = compute_ece_per_class(y_all, probs)
print(f"ECE: 客胜={ece[0]:.4f}, 平局={ece[1]:.4f}, 主胜={ece[2]:.4f}, 平均={np.mean(ece):.4f}")

# 按联赛统计
print(f"\n=== 按联赛准确率 ===")
for league in df['competition_name'].unique():
    mask = df['competition_name'] == league
    if mask.sum() > 0:
        league_acc = accuracy_score(y_all[mask], pred_thresh[mask])
        print(f"  {league}: {league_acc*100:.2f}% ({mask.sum()}场)")

print(f"\n✅ 推理流程正常，所有组件工作正常")