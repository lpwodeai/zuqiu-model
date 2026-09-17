"""英超独立模型 v3.1 — 方案C: 英超赛季加权

v3.0 遗留问题:
- CV 45.85% 略低于 46% 目标 (-0.15pp)
- 平局召回 20.7% 偏低

v3.1 方案C修复:
1. 新增赛季权重: 25/26 ×1.5, 24/25 ×1.2, 23/24 ×1.0
2. 最终权重: time_weight × class_weight × season_weight
3. 目标: CV 46%+, 平局召回 25%+
"""
import os, sys, pickle, json, joblib, numpy as np, warnings
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, classification_report, f1_score
from sklearn.preprocessing import StandardScaler
from datetime import datetime

DRAW_THRESHOLD_FACTOR = 1.1
OUTPUT_DIR = os.path.join(PROJECT_DIR, 'assets', 'epl')
os.makedirs(OUTPUT_DIR, exist_ok=True)

print("=" * 60)
print("=== 英超独立模型 v3.1 — 方案C: 赛季加权 ===")
print("=" * 60)

# ============================================
# 1. 数据加载
# ============================================
df = load_match_data_odds()
epl_mask = df['competition_name'].str.contains('英超', na=False)
df_epl = df[epl_mask].copy()
print(f"\n英超数据: {len(df_epl)} 场")

# 标签分布
y_counts = df_epl['result'].value_counts()
print(f"  标签分布: {dict(y_counts)}")
print(f"  主胜率: {y_counts.get(1,0)/len(df_epl)*100:.1f}%")
print(f"  平局率: {y_counts.get(0,0)/len(df_epl)*100:.1f}%")
print(f"  客胜率: {y_counts.get(2,0)/len(df_epl)*100:.1f}%")

X_all, y_all = build_all_features(df_epl, include_odds=True)

# ============================================
# 2. 特征 (保留全部, Top-100 降维对验证集有害)
# ============================================
print(f"\n原始特征: {X_all.shape[1]} 维")

try:
    import lightgbm as lgb
    lgb_available = True
except ImportError:
    lgb_available = False
    print("  LightGBM 未安装，无法训练")

if not lgb_available:
    sys.exit(1)

# 保留全部特征 (v3.0 测试显示 Top-100 降维导致 Val 从 41.05%→38.86%)
X = X_all
print(f"  使用特征: {X.shape[1]} 维 (全部保留)")

# 变量初始化
X_arr = X.values
y_arr = y_all.values

# ============================================
# 3. 样本权重 (时间衰减 + 类别权重)
# ============================================
def create_time_weights(dates, half_life_months=18):
    """时间衰减权重: 半衰期 18 个月"""
    max_date = dates.max()
    age_months = (max_date - dates).dt.days / 30.0
    weights = np.exp(-np.log(2) * age_months / half_life_months)
    return weights / weights.mean()

time_weights = create_time_weights(df_epl['date'], half_life_months=18)
print(f"\n时间衰减 (半衰期=18月): 权重范围 {time_weights.min():.3f}~{time_weights.max():.3f}")

# 类别权重 (手动计算，避免与 sample_weight 冲突)
n_samples = len(y_all)
class_counts = np.bincount(y_all.astype(int))
class_weights = n_samples / (3 * class_counts)
print(f"  类别权重: 0={class_weights[0]:.3f}, 1={class_weights[1]:.3f}, 2={class_weights[2]:.3f}")

# 组合权重: time_weight × class_weight
class_weight_arr = np.array([class_weights[int(y)] for y in y_all])
combined_weights = time_weights.values * class_weight_arr
print(f"  组合权重 (time×class): {combined_weights.min():.3f}~{combined_weights.max():.3f}")

# === 方案C: 赛季权重（英超样本加权）===
# 近期赛季权重更高，强化模型对当前英超格局的学习
season_weights = np.ones(len(df_epl))
for i, d in enumerate(df_epl['date']):
    if d.year >= 2025 and d.month >= 8:       # 2025/26 赛季
        season_weights[i] = 1.5
    elif d.year >= 2024 and d.month >= 8:      # 2024/25 赛季
        season_weights[i] = 1.2
    else:                                       # 2023/24 及更早
        season_weights[i] = 1.0

season_25_26 = (season_weights == 1.5).sum()
season_24_25 = (season_weights == 1.2).sum()
season_old = (season_weights == 1.0).sum()
print(f"  赛季权重: 25/26={season_25_26}场×1.5, 24/25={season_24_25}场×1.2, 更早={season_old}场×1.0")

# 最终权重: time × class × season
combined_weights = combined_weights * season_weights
print(f"  最终权重 (time×class×season): {combined_weights.min():.3f}~{combined_weights.max():.3f}")

# ============================================
# 4. 模型参数 (v3.0)
# ============================================
params = {
    'n_estimators': 200,
    'learning_rate': 0.03,        # 略微提升 (0.02→0.03)
    'max_depth': 4,
    'num_leaves': 15,
    'min_child_samples': 40,      # 50→40 (更多样本可用)
    'subsample': 0.7,             # 0.6→0.7 (更多样本可用)
    'colsample_bytree': 0.7,      # 0.6→0.7
    'reg_alpha': 0.8,             # 1.0→0.8
    'reg_lambda': 0.8,            # 1.0→0.8
    'min_split_gain': 0.03,       # 0.05→0.03
    'random_state': 42,
    'verbose': -1,
    # 移除 class_weight='balanced' — 手动叠加到 sample_weight
}

# ============================================
# 5. 3折 Cross-Validation
# ============================================
print(f"\n--- LightGBM 3折 CV (v3.1) ---")
tscv = TimeSeriesSplit(n_splits=3)
X_arr = X.values
y_arr = y_all.values

fold_metrics = []
for fold, (train_idx, val_idx) in enumerate(tscv.split(X_arr)):
    X_train, X_val = X_arr[train_idx], X_arr[val_idx]
    y_train, y_val = y_arr[train_idx], y_arr[val_idx]

    # 标准化
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)

    # 训练 (with early stopping + combined weights)
    model = lgb.LGBMClassifier(**params)
    model.fit(
        X_train_s, y_train,
        eval_set=[(X_val_s, y_val)],
        eval_metric='multi_logloss',
        sample_weight=combined_weights[train_idx],
        callbacks=[lgb.early_stopping(30, verbose=False)]
    )

    # 预测
    probs = model.predict_proba(X_val_s)
    acc = accuracy_score(y_val, np.argmax(probs, axis=1))
    ll = log_loss(y_val, probs)
    f1 = f1_score(y_val, np.argmax(probs, axis=1), average='macro')
    cr = classification_report(y_val, np.argmax(probs, axis=1), output_dict=True, labels=[0,1,2])
    print(f"  Fold {fold+1}: Acc={acc*100:.2f}%, LogLoss={ll:.4f}, "
          f"MacroF1={f1:.4f}, 平局召回={cr['1']['recall']*100:.1f}%, trees={model.best_iteration_}")
    fold_metrics.append({
        'accuracy': acc, 'logloss': ll, 'macro_f1': f1,
        'draw_recall': cr['1']['recall'],
        'n_trees': model.best_iteration_
    })

# ============================================
# 6. 全量训练
# ============================================
scaler_full = StandardScaler()
X_s = scaler_full.fit_transform(X_arr)

# 时间序列最终验证集 (最后 20%)
split_idx = int(len(X_s) * 0.8)
X_train_f, X_val_f = X_s[:split_idx], X_s[split_idx:]
y_train_f, y_val_f = y_arr[:split_idx], y_arr[split_idx:]

model_full = lgb.LGBMClassifier(**params)
model_full.fit(
    X_train_f, y_train_f,
    eval_set=[(X_val_f, y_val_f)],
    eval_metric='multi_logloss',
    sample_weight=combined_weights[:split_idx],
    callbacks=[lgb.early_stopping(30, verbose=False)]
)

# 评估
probs_val = model_full.predict_proba(X_val_f)
val_acc = accuracy_score(y_val_f, np.argmax(probs_val, axis=1))
val_ll = log_loss(y_val_f, probs_val)
val_f1 = f1_score(y_val_f, np.argmax(probs_val, axis=1), average='macro')

probs_full = model_full.predict_proba(X_s)
full_acc = accuracy_score(y_arr, np.argmax(probs_full, axis=1))

# CV 统计
cv_acc = np.mean([m['accuracy'] for m in fold_metrics])
cv_ll = np.mean([m['logloss'] for m in fold_metrics])
cv_draw = np.mean([m['draw_recall'] for m in fold_metrics])
cv_f1 = np.mean([m['macro_f1'] for m in fold_metrics])
cv_trees = int(np.mean([m['n_trees'] for m in fold_metrics]))

print(f"\n  CV 平均: Acc={cv_acc*100:.2f}%, LogLoss={cv_ll:.4f}, "
      f"MacroF1={cv_f1:.4f}, 平局召回={cv_draw*100:.1f}%, 平均树数={cv_trees}")
print(f"  验证集 (最后20%): Acc={val_acc*100:.2f}%, LogLoss={val_ll:.4f}, MacroF1={val_f1:.4f}")
print(f"  全量: Acc={full_acc*100:.2f}%")
print(f"  过拟合差距: {full_acc - val_acc:+.4f}")

# ============================================
# 7. 阈值调整
# ============================================
def apply_draw_threshold(probs, factor):
    pred = np.argmax(probs, axis=1)
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

thresh_pred = apply_draw_threshold(probs_val, DRAW_THRESHOLD_FACTOR)
thresh_acc = accuracy_score(y_val_f, thresh_pred)
thresh_cr = classification_report(y_val_f, thresh_pred, output_dict=True, labels=[0,1,2], target_names=['客胜','平局','主胜'])
print(f"\n  阈值调整 (factor={DRAW_THRESHOLD_FACTOR}): Acc={thresh_acc*100:.2f}%, "
      f"平局召回={thresh_cr['平局']['recall']*100:.1f}%, "
      f"平局预测率={thresh_cr['平局']['support']/len(y_val_f)*100:.1f}%")

# ============================================
# 8. 保存模型
# ============================================
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
lgb_path = os.path.join(OUTPUT_DIR, f'lgb_model_epl_v3_{timestamp}.pkl')
pickle.dump(model_full, open(lgb_path, 'wb'))
scaler_path = os.path.join(OUTPUT_DIR, f'scaler_epl_v3_{timestamp}.pkl')
joblib.dump(scaler_full, scaler_path)

# 保存特征列表 (全量特征)
feat_path = os.path.join(OUTPUT_DIR, f'selected_features_epl_v3_{timestamp}.pkl')
pickle.dump(X_all.columns.tolist(), open(feat_path, 'wb'))

metadata = {
    'model_type': 'EPL-v3.1',
    'version': '3.1',
    'timestamp': timestamp,
    'n_samples': len(df_epl),
    'feature_dim': X.shape[1],
    'half_life_months': 18,
    'season_weights': {'25/26': 1.5, '24/25': 1.2, 'older': 1.0},
    'params': params,
    'cv_accuracy': float(cv_acc),
    'val_accuracy': float(val_acc),
    'full_accuracy': float(full_acc),
    'cv_macro_f1': float(cv_f1),
    'val_macro_f1': float(val_f1),
    'cv_draw_recall': float(cv_draw),
    'best_trees': cv_trees
}
json.dump(metadata, open(os.path.join(OUTPUT_DIR, f'metadata_epl_v3_{timestamp}.json'), 'w', encoding='utf-8'), indent=2, ensure_ascii=False)

print(f"\n  模型已保存: {lgb_path}")
print(f"  特征列表: {feat_path}")

# ============================================
# 9. 总结
# ============================================
print(f"\n{'='*60}")
print(f"=== 英超独立模型 v3.1 训练总结 ===")
print(f"{'='*60}")
print(f"  特征维度: {X_all.shape[1]} (全部保留)")
print(f"  半衰期: 18月")
print(f"  赛季权重: 25/26×1.5, 24/25×1.2, 更早×1.0")
print(f"  类别权重: 手动叠加 ({class_weights[0]:.2f}/{class_weights[1]:.2f}/{class_weights[2]:.2f})")
print(f"  CV 折数: 3")
print(f"  CV 准确率: {cv_acc*100:.2f}%")
print(f"  Val 准确率: {val_acc*100:.2f}%")
print(f"  过拟合: {full_acc - val_acc:+.4f}")
print(f"  对比 v3.0: CV=45.85%, Val=41.48%, 平局召回=20.7%")