# -*- coding: utf-8 -*-
"""
C-20260823-003: 精简特征 vs 全量特征 — 训练+推理性能对比
以今日 17 场五大联赛比赛为基准
"""
import sys, os, time, json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from feature_utils import build_all_features, load_match_data_odds

XGB_PARAMS = {
    'max_depth': 4, 'learning_rate': 0.07, 'n_estimators': 130,
    'subsample': 0.785, 'gamma': 5.0, 'reg_alpha': 0.1, 'reg_lambda': 8.0,
    'min_child_weight': 13, 'objective': 'multi:softprob', 'eval_metric': 'mlogloss',
    'random_state': 42, 'verbosity': 0, 'n_jobs': -1,
}

# 今日 17 场比赛 (team_name_cn 格式)
TODAY_MATCHES = [
    # 英超
    ("阿森纳", "考文垂"),
    ("埃弗顿", "水晶宫"),
    ("诺丁汉森林", "利兹联"),
    ("伊普斯维奇城", "桑德兰"),
    ("布伦特福德", "托特纳姆热刺"),
    # 西甲
    ("皇家贝蒂斯", "皇家社会"),
    ("毕尔巴鄂竞技", "塞维利亚"),
    ("瓦伦西亚", "维戈塞尔塔"),
    ("西班牙人", "皇家马德里"),
    # 法甲
    ("马赛", "斯特拉斯堡"),
    ("朗斯", "欧塞尔"),
    ("尼斯", "洛里昂"),
    ("图卢兹", "里昂"),
    # 意甲
    ("乌迪内斯", "科莫"),
    ("国际米兰", "蒙扎"),
    ("热那亚", "那不勒斯"),
    ("帕尔马", "卡利亚里"),
]

print("=" * 70)
print("精简特征 vs 全量特征 — 训练+推理性能对比")
print("基准: 5258 场历史 + 17 场今日比赛")
print("=" * 70)

# ============================================================
# 1. 加载数据
# ============================================================
print("\n[1/4] 加载数据...")
t0 = time.time()
df = load_match_data_odds()
df = df[df['result'].notna()].copy()
print(f"  历史数据: {len(df)} 场 ({time.time()-t0:.1f}s)")

# 找到今日比赛在df中的索引
today_indices = []
for home, away in TODAY_MATCHES:
    mask = (df['home_team_name'] == home) & (df['away_team_name'] == away)
    if mask.sum() > 0:
        idx = df[mask].index[0]
        today_indices.append(idx)
        print(f"  ✓ {home} vs {away} (idx={idx})")
    else:
        print(f"  ✗ {home} vs {away} — 未在历史数据中")

print(f"\n  匹配到 {len(today_indices)}/17 场")

# ============================================================
# 2. 特征构建性能对比
# ============================================================
print(f"\n[2/4] 特征构建性能对比...")

results = {}

for mode, label in [(False, "全量"), (True, "精简")]:
    t0 = time.time()
    X, y = build_all_features(df, slim_odds=mode)
    build_time = time.time() - t0
    results[label] = {'build_time': build_time, 'dim': X.shape[1], 'X': X, 'y': y}
    print(f"  {label}: {X.shape[1]} 维, {build_time:.1f}s")

full = results['全量']
slim = results['精简']

# ============================================================
# 3. 训练性能对比
# ============================================================
print(f"\n[3/4] 训练性能对比 (XGBoost, 130轮)...")

for label in ['全量', '精简']:
    r = results[label]
    X_data, y_data = r['X'], r['y']

    # 80/20 时间序列分割
    split = int(len(X_data) * 0.8)
    X_train, y_train = X_data.iloc[:split], y_data.iloc[:split]

    scaler = StandardScaler()
    t0 = time.time()
    X_train_scaled = scaler.fit_transform(X_train)
    scale_time = time.time() - t0

    model = xgb.XGBClassifier(**XGB_PARAMS)
    t0 = time.time()
    model.fit(X_train_scaled, y_train, verbose=False)
    train_time = time.time() - t0

    # 单场推理 (取最后一场)
    X_last = X_data.iloc[-1:].values
    X_last_scaled = scaler.transform(X_last)

    t0 = time.time()
    for _ in range(100):
        model.predict(X_last_scaled)
    single_infer = (time.time() - t0) / 100 * 1000

    # 批量推理 (17场)
    if today_indices:
        X_batch = X_data.iloc[today_indices]
        X_batch_scaled = scaler.transform(X_batch)
        t0 = time.time()
        model.predict(X_batch_scaled)
        batch_infer = time.time() - t0
    else:
        batch_infer = 0

    results[label]['scale_time'] = scale_time
    results[label]['train_time'] = train_time
    results[label]['single_infer_ms'] = single_infer
    results[label]['batch_infer_s'] = batch_infer

    print(f"  {label}: 缩放={scale_time:.2f}s, 训练={train_time:.1f}s, "
          f"单场推理={single_infer:.2f}ms, 17场批量={batch_infer:.2f}s")

# ============================================================
# 4. 汇总
# ============================================================
print(f"\n[4/4] 性能对比汇总")
print(f"\n{'='*70}")
print(f"{'指标':<20} {'全量特征':>15} {'精简特征':>15} {'提升':>15}")
print(f"{'='*70}")

for key, label, unit, lower_better in [
    ('build_time', '特征构建', 's', True),
    ('dim', '特征维度', '维', True),
    ('scale_time', '缩放耗时', 's', True),
    ('train_time', '训练耗时', 's', True),
    ('single_infer_ms', '单场推理', 'ms', True),
    ('batch_infer_s', '17场批量推理', 's', True),
]:
    fv = results['全量'][key]
    sv = results['精简'][key]
    if fv > 0:
        ratio = fv / sv if lower_better else sv / fv
        speedup = f"{ratio:.1f}x" if ratio > 1.1 else f"{ratio:.2f}x"
    else:
        speedup = "N/A"
    print(f"{label:<20} {fv:>15.2f} {sv:>15.2f} {speedup:>15}")

print(f"{'='*70}")

# 总分
total_full = results['全量']['build_time'] + results['全量']['train_time'] + results['全量']['batch_infer_s']
total_slim = results['精简']['build_time'] + results['精简']['train_time'] + results['精简']['batch_infer_s']
print(f"\n端到端总耗时: {total_full:.1f}s → {total_slim:.1f}s ({(total_full/total_slim if total_slim>0 else 0):.1f}x)")

# 保存
result_path = BASE / "logs" / f"perf_compare_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.json"
os.makedirs(BASE / "logs", exist_ok=True)
output = {
    'matches_found': len(today_indices),
    'total_matches': len(TODAY_MATCHES),
    'full': {k: float(v) if isinstance(v, (np.floating, float, int)) else v for k, v in results['全量'].items() if k != 'X' and k != 'y'},
    'slim': {k: float(v) if isinstance(v, (np.floating, float, int)) else v for k, v in results['精简'].items() if k != 'X' and k != 'y'},
}
with open(result_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, indent=2, ensure_ascii=False)
print(f"结果已保存: {result_path}")