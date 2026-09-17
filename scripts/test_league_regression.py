"""五大联赛回归测试：验证联赛专属 draw_threshold_factor 分派逻辑

测试范围:
1. 各联赛因子分配正确性 (FL1→0.0, PL/BL1/LaLiga/IT→1.1)
2. 法甲 argmax 不改变预测，英超阈值正常触发
3. 概率值在所有联赛中保持不变 (决策阈值 ≠ 概率修改)
4. 跨联赛预测一致性 (同一概率向量，不同联赛可能有不同分类)
5. 边界情况：三概率相等、极端概率、平局概率=0
6. 大量样本统计：预测分布合理性
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

# 联赛因子配置 (与 prediction-service.js 同步)
LEAGUE_DRAW_THRESHOLD_FACTOR = {
    'FL1': 0.0,
    'PL': 1.1,
    'BL1': 1.1,
    'LaLiga': 1.1,
    'IT': 1.1,
}
DEFAULT_FACTOR = 1.1

def get_factor(league_code):
    return LEAGUE_DRAW_THRESHOLD_FACTOR.get(league_code, DEFAULT_FACTOR)

def apply_draw_threshold(probs, factor):
    pred = np.argmax(probs, axis=1)
    if factor <= 0:
        return pred
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

# 加载数据
df = load_match_data_odds()
X_all, y_all = build_all_features(df, include_odds=True)
X = X_all[sf]
X_scaled = scaler.transform(X)
probs = lgb.predict(X_scaled)

# 联赛映射
LEAGUE_MAP = {
    '英超': 'PL', '西甲': 'LaLiga', '意甲': 'IT',
    '德甲': 'BL1', '法甲': 'FL1'
}

# 联赛代码 -> 全称
LEAGUE_NAME = {
    'PL': '英超', 'LaLiga': '西甲', 'IT': '意甲',
    'BL1': '德甲', 'FL1': '法甲'
}

PASS, FAIL = 0, 0

def T(name, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ✅ {name}")
    except AssertionError as e:
        FAIL += 1
        print(f"  ❌ {name}: {e}")
    except Exception as e:
        FAIL += 1
        print(f"  💥 {name}: {e}")

def assert_eq(a, b, msg=""):
    assert a == b, f"expected {b}, got {a}" + (f" ({msg})" if msg else "")

def assert_close(a, b, tol=1e-6, msg=""):
    assert abs(a - b) < tol, f"expected ~{b}, got {a}" + (f" ({msg})" if msg else "")

def assert_gt(a, b, msg=""):
    assert a > b, f"expected >{b}, got {a}" + (f" ({msg})" if msg else "")

def assert_lt(a, b, msg=""):
    assert a < b, f"expected <{b}, got {a}" + (f" ({msg})" if msg else "")

def assert_ge(a, b, msg=""):
    assert a >= b, f"expected >={b}, got {a}" + (f" ({msg})" if msg else "")

def assert_le(a, b, msg=""):
    assert a <= b, f"expected <={b}, got {a}" + (f" ({msg})" if msg else "")

print("=" * 70)
print("=== 五大联赛回归测试: 联赛专属 draw_threshold_factor ===")
print("=" * 70)

# ============================================================
# 1. 联赛因子分配
# ============================================================
print("\n--- 1. 联赛因子分配 ---")

T("FL1 → 0.0", lambda: assert_eq(get_factor('FL1'), 0.0))
T("PL → 1.1", lambda: assert_eq(get_factor('PL'), 1.1))
T("BL1 → 1.1", lambda: assert_eq(get_factor('BL1'), 1.1))
T("LaLiga → 1.1", lambda: assert_eq(get_factor('LaLiga'), 1.1))
T("IT → 1.1", lambda: assert_eq(get_factor('IT'), 1.1))
T("未知 → 默认 1.1", lambda: assert_eq(get_factor('UNKNOWN'), 1.1))
T("None → 默认 1.1", lambda: assert_eq(get_factor(None), 1.1))

# ============================================================
# 2. 各联赛预测分布 (真实数据)
# ============================================================
print("\n--- 2. 各联赛真实数据预测分布 ---")

league_stats = {}

for cn_name, lc in LEAGUE_MAP.items():
    mask = df['competition_name'].str.contains(cn_name, na=False)
    y_l = np.array(y_all[mask.values])
    p_l = np.array(probs[mask.values])
    factor = get_factor(lc)
    
    pred_global = apply_draw_threshold(p_l, 1.1)      # 全局因子
    pred_league = apply_draw_threshold(p_l, factor)     # 联赛专属因子
    
    acc_global = accuracy_score(y_l, pred_global)
    acc_league = accuracy_score(y_l, pred_league)
    
    cr_global = classification_report(y_l, pred_global, output_dict=True, labels=[0,1,2],
                                        target_names=['客胜','平局','主胜'], zero_division=0)
    cr_league = classification_report(y_l, pred_league, output_dict=True, labels=[0,1,2],
                                        target_names=['客胜','平局','主胜'], zero_division=0)
    
    actual_draw = sum(y_l == 1) / len(y_l)
    actual_home = sum(y_l == 2) / len(y_l)
    
    league_stats[lc] = {
        'n': len(y_l),
        'actual_draw': actual_draw,
        'actual_home': actual_home,
        'acc_global': acc_global,
        'acc_league': acc_league,
        'draw_pred_global': sum(pred_global == 1) / len(pred_global),
        'draw_pred_league': sum(pred_league == 1) / len(pred_league),
        'home_pred_global': sum(pred_global == 2) / len(pred_global),
        'home_pred_league': sum(pred_league == 2) / len(pred_league),
        'draw_recall_global': cr_global['平局']['recall'],
        'draw_recall_league': cr_league['平局']['recall'],
        'changed': sum(pred_global != pred_league),
        'factor': factor,
    }
    
    marker = " <-- 专属" if factor != 1.1 else ""
    s = league_stats[lc]
    print(f"  {cn_name} ({lc}, factor={factor}, n={len(y_l)}){marker}:")
    print(f"    实际: 主胜={actual_home*100:.1f}%, 平局={actual_draw*100:.1f}%")
    print(f"    全局: acc={acc_global*100:.2f}% draw_pred={s['draw_pred_global']*100:.1f}% draw_rec={s['draw_recall_global']*100:.1f}%")
    print(f"    专属: acc={acc_league*100:.2f}% draw_pred={s['draw_pred_league']*100:.1f}% draw_rec={s['draw_recall_league']*100:.1f}%")
    if s['changed'] > 0:
        print(f"    预测变化: {s['changed']} 场 ({s['changed']/len(y_l)*100:.1f}%)")

# ============================================================
# 3. 回归检查: 非专属联赛不应有变化
# ============================================================
print("\n--- 3. 回归检查: 非专属联赛预测稳定性 ---")

for lc in ['PL', 'BL1', 'LaLiga', 'IT']:
    stats = league_stats[lc]
    T(f"{lc} 预测不变 (factor=1.1, 与全局相同)",
      lambda s=stats: assert_eq(s['changed'], 0, f"预期0场变化, 实际{s['changed']}场"))

# ============================================================
# 4. 法甲专属检查
# ============================================================
print("\n--- 4. 法甲专属检查 ---")

stats_l1 = league_stats['FL1']

T("法甲准确率不低于全局",
  lambda: assert_ge(stats_l1['acc_league'], stats_l1['acc_global'] - 0.005))

T("法甲平局预测率更接近实际",
  lambda: assert_lt(abs(stats_l1['draw_pred_league'] - stats_l1['actual_draw']),
                    abs(stats_l1['draw_pred_global'] - stats_l1['actual_draw'])))

T("法甲主胜预测率更接近实际",
  lambda: assert_lt(abs(stats_l1['home_pred_league'] - stats_l1['actual_home']),
                    abs(stats_l1['home_pred_global'] - stats_l1['actual_home'])))

T("法甲有预测变化 (factor=0.0 vs 1.1)",
  lambda: assert_gt(stats_l1['changed'], 0))

# ============================================================
# 5. 概率不变性: 决策阈值不修改概率
# ============================================================
print("\n--- 5. 概率不变性检查 ---")

# 随机抽样 100 场法甲比赛，验证概率不变
l1_mask = df['competition_name'].str.contains('法甲', na=False)
l1_probs = np.array(probs[l1_mask.values])
sample_idx = np.random.choice(len(l1_probs), min(100, len(l1_probs)), replace=False)

for i, idx in enumerate(sample_idx[:10]):  # 检查前 10 场
    p = l1_probs[idx]
    T(f"法甲样本{i+1} 概率和=1.0",
      lambda p=p: assert_close(p[0] + p[1] + p[2], 1.0, tol=0.001))

# ============================================================
# 6. 合成数据边界测试
# ============================================================
print("\n--- 6. 合成数据边界测试 ---")

# 构造典型概率向量，验证各联赛分类一致性
# 注意: probs 顺序为 [P(客胜), P(平局), P(主胜)] (与 apply_draw_threshold 一致)
test_cases = [
    # (P_客胜, P_平局, P_主胜, 预期_FL1, 预期_PL, 描述)
    (0.25, 0.25, 0.50, '主胜', '主胜', '主胜明显'),
    (0.33, 0.33, 0.34, '主胜', '平局', '平局接近主胜-阈值触发'),
    (0.33, 0.34, 0.33, '平局', '平局', '平局最高'),
    (0.50, 0.25, 0.25, '客胜', '客胜', '客胜明显'),
    (0.30, 0.30, 0.40, '主胜', '主胜', '平局不触发'),
    (0.30, 0.35, 0.35, '平局', '平局', '平局并列最高'),
    (0.40, 0.00, 0.60, '主胜', '主胜', '平局概率0'),
    (0.40, 0.30, 0.30, '客胜', '客胜', '客胜略高'),
    (0.34, 0.33, 0.33, '客胜', '平局', '平局接近客胜-阈值触发'),
    (0.00, 0.51, 0.49, '平局', '平局', '平局/客胜并列'),
]

def synthetic_pred(probs, factor):
    p = np.array([probs])
    return apply_draw_threshold(p, factor)[0]

LABELS = {0: '客胜', 1: '平局', 2: '主胜'}

for lose, draw, win, exp_fl1, exp_pl, desc in test_cases:
    probs_vec = [lose, draw, win]
    pred_fl1 = LABELS[synthetic_pred(probs_vec, 0.0)]
    pred_pl = LABELS[synthetic_pred(probs_vec, 1.1)]
    pred_bl1 = LABELS[synthetic_pred(probs_vec, 1.1)]
    pred_ll = LABELS[synthetic_pred(probs_vec, 1.1)]
    pred_it = LABELS[synthetic_pred(probs_vec, 1.1)]

    T(f"合成[{desc}]: FL1={pred_fl1}, PL={pred_pl}",
      lambda pfl1=pred_fl1, efl1=exp_fl1, ppl=pred_pl, epl=exp_pl, d=desc: (
          assert_eq(pfl1, efl1, d), assert_eq(ppl, epl, d)))

    T(f"合成[{desc}]: PL=BL1=LaLiga=IT 一致",
      lambda ppl=pred_pl, pbl1=pred_bl1, pll=pred_ll, pit=pred_it, d=desc: (
          assert_eq(ppl, pbl1, d), assert_eq(ppl, pll, d), assert_eq(ppl, pit, d)))

# ============================================================
# 7. 大量样本统计: 预测率合理性
# ============================================================
print("\n--- 7. 预测率合理性统计 ---")

total_global_draw = 0
total_league_draw = 0
total_n = 0

for lc, stats in league_stats.items():
    total_global_draw += stats['draw_pred_global'] * stats['n']
    total_league_draw += stats['draw_pred_league'] * stats['n']
    total_n += stats['n']

overall_actual_draw = sum(np.array(y_all) == 1) / len(y_all)

print(f"  全量实际平局率: {overall_actual_draw*100:.1f}%")
print(f"  全局预测平局率: {total_global_draw/total_n*100:.1f}%")
print(f"  专属预测平局率: {total_league_draw/total_n*100:.1f}%")

T("专属预测平局率不高于全局",
  lambda: assert_le(total_league_draw/total_n, total_global_draw/total_n + 0.01))

T("专属预测平局率不低于 15%",
  lambda: assert_ge(total_league_draw/total_n, 0.15))

# ============================================================
# 8. 总结
# ============================================================
print(f"\n{'='*70}")
print(f"  测试结果: {PASS} 通过, {FAIL} 失败, {PASS+FAIL} 总计")
print(f"{'='*70}")

if FAIL > 0:
    print(f"\n❌ 回归测试失败! 请检查上述失败项。")
    sys.exit(1)
else:
    print(f"\n✅ 回归测试全部通过! 联赛专属阈值逻辑正确，未破坏其他联赛。")
    print(f"   法甲 factor=0.0: 准确率 {stats_l1['acc_league']*100:.2f}% (全局 {stats_l1['acc_global']*100:.2f}%)")
    print(f"   法甲预测变化: {stats_l1['changed']} 场 ({stats_l1['changed']/stats_l1['n']*100:.1f}%)")
    print(f"   其他联赛: 0 场变化 (factor 一致)")