"""西甲专属阈值校准 (C-20260816-206)"""
import os, sys, pickle, numpy as np, warnings, joblib, pandas as pd
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features
from sklearn.metrics import accuracy_score

print("加载数据...")
df = load_match_data_odds()
laliga = df[df['competition_name'].str.contains('西甲', na=False)]
print(f"西甲: {len(laliga)} 场")

X, y = build_all_features(laliga, include_odds=True)

# Load model + scaler + features
model_path = os.path.join(PROJECT_DIR, 'assets', 'lgb_model_20260815_215015.pkl')
model = pickle.load(open(model_path, 'rb'))
scaler_path = max([os.path.join(PROJECT_DIR, 'assets', f) for f in os.listdir(os.path.join(PROJECT_DIR, 'assets')) if f.startswith('scaler_') and f.endswith('.pkl')], key=os.path.getctime)
scaler = joblib.load(scaler_path)
sf_path = max([os.path.join(PROJECT_DIR, 'assets', f) for f in os.listdir(os.path.join(PROJECT_DIR, 'assets')) if f.startswith('selected_features_') and f.endswith('.pkl')], key=os.path.getctime)
sf = pickle.load(open(sf_path, 'rb'))

# Align features
X_aligned = pd.DataFrame(0, index=range(len(X)), columns=sf)
for col in sf:
    if col in X.columns:
        X_aligned[col] = X[col].values
X_s = scaler.transform(X_aligned)

# Predict
probs = model.predict(X_s).reshape(-1, 3)

def apply_draw_threshold(probs, factor):
    if factor <= 0:
        return np.argmax(probs, axis=1)
    pred = np.argmax(probs, axis=1)
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

actual_draw_rate = (y == 1).mean()
print(f"\n西甲 (LaLiga): 实际平局率 {actual_draw_rate:.1%}")
print(f"{'factor':>8}  {'准确率':>8}  {'平局召回':>10}  {'平局预测率':>10}  {'主胜预测率':>10}")
print(f"  {'-'*54}")

best_factor = 0.0
best_acc = 0

for factor in [0.0, 0.5, 0.7, 0.9, 1.0, 1.05, 1.1, 1.15, 1.2, 1.3, 1.5]:
    thresh_pred = apply_draw_threshold(probs, factor)
    acc = accuracy_score(y, thresh_pred)
    draw_recall = (thresh_pred[y == 1] == 1).mean()
    draw_pred_rate = (thresh_pred == 1).mean()
    home_pred_rate = (thresh_pred == 2).mean()

    marker = ""
    if factor == 1.1: marker = " <-- 全局"
    if factor == 0.0: marker = " <-- argmax"

    print(f"  {factor:>8.2f}  {acc*100:>7.2f}%  {draw_recall*100:>9.1f}%  {draw_pred_rate*100:>9.1f}%  {home_pred_rate*100:>9.1f}%{marker}")

    if acc > best_acc:
        best_acc = acc
        best_factor = factor

# Argmax baseline
argmax_pred = np.argmax(probs, axis=1)
argmax_acc = accuracy_score(y, argmax_pred)
argmax_draw_recall = (argmax_pred[y == 1] == 1).mean()
argmax_draw_rate = (argmax_pred == 1).mean()

print(f"\n  >>> 推荐 factor={best_factor}, 准确率={best_acc*100:.2f}%")
print(f"  >>> 全局 factor=1.1 准确率: 51.32%")
print(f"  >>> 预期改善: +{best_acc*100 - 51.32:.2f}pp")

# Error analysis
pred = np.argmax(probs, axis=1)
cm = np.zeros((3,3), dtype=int)
for a, p in zip(y, pred):
    cm[a, p] += 1

print(f"\n混淆矩阵 (argmax):")
print(f"                预测客胜  预测平局  预测主胜")
print(f"    实际客胜:      {cm[0,0]:4d}      {cm[0,1]:4d}      {cm[0,2]:4d}")
print(f"    实际平局:      {cm[1,0]:4d}      {cm[1,1]:4d}      {cm[1,2]:4d}")
print(f"    实际主胜:      {cm[2,0]:4d}      {cm[2,1]:4d}      {cm[2,2]:4d}")

# Distribution
print(f"\n分布对比:")
print(f"  实际: 客胜={(y==0).mean():.1%}, 平局={(y==1).mean():.1%}, 主胜={(y==2).mean():.1%}")
print(f"  argmax: 客胜={(pred==0).mean():.1%}, 平局={(pred==1).mean():.1%}, 主胜={(pred==2).mean():.1%}")

# High-confidence errors
errors = []
for i, (a, p) in enumerate(zip(y, pred)):
    if a != p:
        errors.append({
            'home': str(laliga.iloc[i].get('home_team', '?')),
            'away': str(laliga.iloc[i].get('away_team', '?')),
            'actual': int(a), 'predicted': int(p),
            'prob_max': float(probs[i].max()),
        })
high_conf = [e for e in errors if e['prob_max'] > 0.45]
print(f"\n高置信度错误 (prob>0.45): {len(high_conf)} 场 / {len(errors)} 场错误")
labels = ['客胜', '平局', '主胜']
for e in sorted(high_conf, key=lambda x: x['prob_max'], reverse=True)[:10]:
    print(f"  {e['home']} vs {e['away']} | 实际:{labels[e['actual']]} 预测:{labels[e['predicted']]} | prob={e['prob_max']:.3f}")