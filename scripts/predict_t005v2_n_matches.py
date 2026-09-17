"""
T-005 v2 最终模型 — 最近 N 场真实比赛预测（稳定性验证）
=========================================================

目标：在最近 30 场真实比赛上验证走水预测占比是否稳定落在 20-22% 区间
（与测试集 783 场报告的 21.3% 对比）。

用法:
    python scripts/predict_t005v2_n_matches.py              # 默认 30 场
    python scripts/predict_t005v2_n_matches.py 50           # 最近 50 场

输出:
    - 每 10 场一个分块的走水预测率 / 实际走水率 / 准确率
    - 全量 N 场对比：预测分布 vs 实际分布 vs 评估报告基准
    - 走水预测占比稳定性结论
"""

import sys
import os
import json
import pickle
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime
import argparse
import warnings
warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES
from hcp_features import MAX_VALID_TIMESTAMP, HCP_RESULT_NAMES
from train_hcp_model import merge_expanded_labels, add_elo_features_hcp
from train_hcp_model_v2 import apply_rule_adjustments, generate_rule_warnings
from dynamic_draw_threshold import get_handicap_categories
from deploy_t005v2_final import (
    two_stage_predict_final,
    OPTIMAL_CLASS_WEIGHT, OPTIMAL_TEMPERATURE,
    OPTIMAL_DYNAMIC_THRESHOLDS, DEFAULT_DRAW_THRESHOLD,
)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
DB_PATH = os.path.join(DATA_DIR, 'odds.db')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(REPORT_DIR, exist_ok=True)

LABEL_NAMES = ['上盘赢', '走水', '下盘赢']

# 评估报告（测试集 783 场）基准
REPORT_BASELINE = {
    'n': 783,
    'accuracy': 0.4866,
    'draw_pred_rate': 0.2133,
    'draw_actual_rate': 0.2248,
    'home_pred_rate': 278 / 783,
    'draw_pred_count': 167,
    'away_pred_rate': 338 / 783,
}

TRAIN_VALID_MIN_DATE = '2025-11-30'   # 与部署脚本划分一致：训练到 2025-11-30, 测试集从这天开始


def load_models():
    with open(os.path.join(ASSETS_DIR, 't005v2_final_draw_detector.pkl'), 'rb') as f:
        draw_model = pickle.load(f)
    with open(os.path.join(ASSETS_DIR, 't005v2_final_direction_predictor.pkl'), 'rb') as f:
        dir_model = pickle.load(f)
    return draw_model, dir_model


def build_X_y_meta():
    features = build_all_features_v2()
    features = merge_expanded_labels(features)
    feature_cols = V2_ALL_FEATURES.copy()
    X = features[feature_cols].apply(pd.to_numeric, errors='coerce').fillna(
        lambda col: col.median() if pd.api.types.is_numeric_dtype(col) else col
    )
    if X.isnull().any().any():
        X = X.fillna(X.median())

    y_raw = features['actual_handicap'].copy()
    label_map = {'胜': 0, '平': 1, '负': 2}
    y = y_raw.map(label_map)
    nm = y.isna() & y_raw.notna()
    if nm.any():
        y[nm] = pd.to_numeric(y_raw[nm], errors='coerce')
    y = y.fillna(-1).astype(int)

    meta = features[['league', 'date', 'home_team', 'away_team']].copy()
    meta['actual_handicap'] = y_raw
    valid = y >= 0
    X = X[valid]
    y = y[valid]
    meta = meta[valid]
    X, _ = add_elo_features_hcp(X, meta)
    return X, y, meta


def get_recent(X, meta, n, only_test_set=True):
    md = meta.copy()
    md['_dp'] = pd.to_datetime(md['date'], errors='coerce')
    if only_test_set:
        md = md[md['_dp'] >= pd.Timestamp(TRAIN_VALID_MIN_DATE)]
    md = md.sort_values('_dp', ascending=False)
    idx = md.head(n).index
    return X.loc[idx], meta.loc[idx]


def get_odds(index_list):
    conn = sqlite3.connect(DB_PATH)
    out = {}
    for mid in index_list:
        q = """
            SELECT h.hcp_win, h.hcp_draw, h.hcp_lose, mt.handicap
            FROM handicap_history h
            INNER JOIN match_id_mapping m ON h.match_id = m.sh_match_id
            INNER JOIN matches mt ON m.matches_match_id = mt.match_id
            WHERE m.matches_match_id = ? AND h.timestamp < ?
            ORDER BY h.timestamp DESC LIMIT 1
        """
        d = pd.read_sql(q, conn, params=(str(mid), MAX_VALID_TIMESTAMP))
        if len(d) > 0:
            out[mid] = {
                'hcp_win': float(d.iloc[0]['hcp_win']),
                'hcp_draw': float(d.iloc[0]['hcp_draw']),
                'hcp_lose': float(d.iloc[0]['hcp_lose']),
                'handicap_line': d.iloc[0]['handicap'],
            }
        else:
            out[mid] = {'hcp_win': 0, 'hcp_draw': 0, 'hcp_lose': 0, 'handicap_line': None}
    conn.close()
    return out


def actual_label(ah_val):
    if pd.isna(ah_val):
        return -1, '未进行'
    if ah_val in ['胜', '平', '负']:
        return {'胜': 0, '平': 1, '负': 2}[ah_val], {'胜': '上盘赢', '平': '走水', '负': '下盘赢'}[ah_val]
    try:
        i = int(ah_val)
        return i, LABEL_NAMES[i]
    except:
        return -1, '未进行'


def distribution(y_pred_list, y_true_list):
    counts_pred = {LABEL_NAMES[i]: 0 for i in range(3)}
    counts_true = {LABEL_NAMES[i]: 0 for i in range(3)}
    for p in y_pred_list:
        counts_pred[LABEL_NAMES[p]] += 1
    for t in y_true_list:
        if 0 <= t <= 2:
            counts_true[LABEL_NAMES[t]] += 1
    n_pred = max(len(y_pred_list), 1)
    n_true = max(len(y_true_list), 1)
    return counts_pred, counts_true


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('n', type=int, nargs='?', default=30, help='最近比赛数（默认30）')
    parser.add_argument('--no-rules', action='store_true',
                        help='方案A：关闭 apply_rule_adjustments 规则引擎，仅保留核心模型+generate_rule_warnings 作为提示')
    args = parser.parse_args()
    N = args.n
    USE_RULES = not args.no_rules

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    mode_label = "核心模型 + 规则引擎" if USE_RULES else "方案A：仅核心模型（关闭规则引擎）"
    print("=" * 70)
    print(f"🎯 T-005 v2 最终模型 — 最近 {N} 场稳定性验证")
    print("=" * 70)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"模式: {mode_label}")
    print(f"参数: cw={OPTIMAL_CLASS_WEIGHT}, T={OPTIMAL_TEMPERATURE}, 动态阈值={OPTIMAL_DYNAMIC_THRESHOLDS}")
    print(f"样本范围: 只取测试集区间（≥{TRAIN_VALID_MIN_DATE}），避免训练数据泄漏")

    # 1. 加载
    draw, dire = load_models()
    X, y, meta = build_X_y_meta()

    # 2. 取最近 N 场
    X_n, m_n = get_recent(X, meta, N)
    if len(X_n) < N:
        print(f" ⚠️ 实际可用最近比赛只有 {len(X_n)} 场（已取测试集区间全部）")
        N = len(X_n)

    print(f"\n📋 数据范围: {m_n['date'].min()} ~ {m_n['date'].max()}, 共 {len(X_n)} 场")
    print(f"   联赛分布:")
    for lg, cnt in m_n['league'].value_counts().head(5).items():
        print(f"     - {lg}: {cnt} 场")

    # 3. 赔率 + 类别
    odds = get_odds(list(X_n.index))
    cats = get_handicap_categories(m_n)

    # 4. 预测
    y_pred_pre, y_proba_pre = two_stage_predict_final(
        draw, dire, X_n,
        handicap_categories=cats,
        temperature=OPTIMAL_TEMPERATURE,
        threshold_map=OPTIMAL_DYNAMIC_THRESHOLDS,
    )

    # 规则调整（方案A：可关闭）
    hw = pd.Series([odds[i]['hcp_win'] for i in X_n.index], index=X_n.index)
    hl = pd.Series([odds[i]['hcp_lose'] for i in X_n.index], index=X_n.index)
    hg = pd.Series([odds[i]['handicap_line'] for i in X_n.index], index=X_n.index)

    if USE_RULES:
        y_pred, y_proba = apply_rule_adjustments(y_pred_pre, y_proba_pre, X_n, hw, hl, hg)
    else:
        # 方案A：跳过规则调整，仅保留核心模型输出
        y_pred, y_proba = y_pred_pre.copy(), y_proba_pre.copy()

    # 打印规则调整前后走水预测数对比
    n_draw_pre = int((y_pred_pre == 1).sum())
    n_draw_post = int((y_pred == 1).sum())
    print(f"\n  🔀 规则调整前后走水预测数变化:")
    print(f"    前（仅T+动态阈值）: {n_draw_pre}/{N}  ({n_draw_pre/N*100:.1f}%)")
    if USE_RULES:
        print(f"    后（加规则调整）:    {n_draw_post}/{N}  ({n_draw_post/N*100:.1f}%)")
        if n_draw_post > n_draw_pre:
            added = [i for i in range(N) if y_pred_pre[i] != 1 and y_pred[i] == 1]
            print(f"    规则系统将 {len(added)} 场从非走水→走水")
    else:
        print(f"    后（方案A，跳过规则）: {n_draw_post}/{N}  ({n_draw_post/N*100:.1f}%)  ← 与核心模型一致")
        # 仍然输出 warnings 作为人工提示（不改变预测）
        n_with_warning = 0
        for i, idx in enumerate(X_n.index):
            wl = generate_rule_warnings(odds[idx])
            if wl:
                n_with_warning += 1
        print(f"    人工提示：{n_with_warning}/{N} 场触发规则警示（仅提示，不改变预测）")

    # 5. 收集真实标签
    y_true = []
    for i, (idx, m) in enumerate(m_n.iterrows()):
        lab, _ = actual_label(m.get('actual_handicap'))
        y_true.append(lab)

    valid_mask = np.array(y_true) >= 0
    n_valid = valid_mask.sum()
    y_pred_valid = y_pred[valid_mask]
    y_true_valid = np.array(y_true)[valid_mask]
    acc = float((y_pred_valid == y_true_valid).sum() / max(n_valid, 1))

    # 评估报告基准（在诊断之前定义）
    base_draw_pred = REPORT_BASELINE['draw_pred_rate']
    base_draw_actual = REPORT_BASELINE['draw_actual_rate']
    base_acc = REPORT_BASELINE['accuracy']
    base_home_pred = REPORT_BASELINE['home_pred_rate']
    base_away_pred = REPORT_BASELINE['away_pred_rate']

    print("\n" + "=" * 70)
    print("🔍 诊断：盘口线类别分布 & 走水概率分布")
    print("=" * 70)
    cat_vals = cats.values
    cat_counts = pd.Series(cat_vals).value_counts()
    print(f"\n  盘口线类别分布（{N}场）:")
    for c, cnt in cat_counts.items():
        thresh = OPTIMAL_DYNAMIC_THRESHOLDS.get(c, DEFAULT_DRAW_THRESHOLD)
        pct = cnt / N * 100
        print(f"    {c:>15s}: {cnt:3d} 场 ({pct:5.1f}%)  使用阈值={thresh}")
    unknown_cnt = int((cat_vals == 'unknown').sum())
    if unknown_cnt / N >= 0.5:
        print(f"    ⚠️  unknown 类别占比 {unknown_cnt/N*100:.0f}%，原因: matches.handicap 列缺失，反推盘口线（路径1）只产生整数类别")

    # 走水概率分布
    p_draw_vals = y_proba[:, 1]
    print(f"\n  走水概率 p_draw 分布:")
    print(f"    均值 ± 标准差: {p_draw_vals.mean():.3f} ± {p_draw_vals.std():.3f}")
    print(f"    百分位数: p10={np.percentile(p_draw_vals,10):.3f}, p25={np.percentile(p_draw_vals,25):.3f}, "
          f"p50={np.percentile(p_draw_vals,50):.3f}, p75={np.percentile(p_draw_vals,75):.3f}, p90={np.percentile(p_draw_vals,90):.3f}")
    thr = np.array([OPTIMAL_DYNAMIC_THRESHOLDS.get(c, DEFAULT_DRAW_THRESHOLD) for c in cat_vals])
    above_thr = (p_draw_vals > thr).sum()
    print(f"    p_draw > 各类别阈值场数: {above_thr}/{N} ({above_thr/N*100:.1f}%) → 对应走水预测场数")
    print(f"    p_draw > 0.5 的场数:   {(p_draw_vals>0.5).sum()}/{N}  ({(p_draw_vals>0.5).sum()/N*100:.1f}%)")
    print(f"    p_draw > 0.4 的场数:   {(p_draw_vals>0.4).sum()}/{N}  ({(p_draw_vals>0.4).sum()/N*100:.1f}%)")
    # 测试集基准
    print(f"\n  对比评估报告（测试集 783 场）走水概率基准:")
    print(f"    走水预测率: {base_draw_pred*100:.1f}%（p_draw > 各类别阈值的比例）")
    print(f"    p_draw > 0.5: ~19.6% (动态阈值后)")

    print("\n" + "=" * 70)
    print(f"📊 分块汇总（每 10 场一块）")
    print("=" * 70)
    block_size = 10
    n_blocks = (N + block_size - 1) // block_size
    rows = []
    for b in range(n_blocks):
        sl = slice(b * block_size, min((b + 1) * block_size, N))
        bp, bt = distribution(list(y_pred[sl]), y_true[sl])
        vm = np.array([y_true[i] for i in range(sl.start, sl.stop)]) >= 0
        n_vm = vm.sum()
        block_pred = y_pred[sl][vm] if n_vm > 0 else np.array([], dtype=int)
        block_true = (np.array(y_true[sl]))[vm]
        block_acc = float((block_pred == block_true).sum() / max(n_vm, 1))
        block_n = sl.stop - sl.start
        draw_pred_r = bp['走水'] / block_n
        draw_act_r = (bt['走水'] / max(sum(bt.values()), 1)) if sum(bt.values()) > 0 else float('nan')
        d_range_start = m_n['date'].iloc[sl.start]
        d_range_end = m_n['date'].iloc[sl.stop - 1]
        rows.append({
            'block': b + 1,
            'n': block_n,
            'date_range': f"{d_range_start}~{d_range_end}",
            'draw_pred_rate': round(draw_pred_r, 4),
            'draw_actual_rate': round(draw_act_r, 4) if not np.isnan(draw_act_r) else None,
            'accuracy': round(float(block_acc), 4),
            'pred_dist': {k: int(v) for k, v in bp.items()},
            'actual_dist': {k: int(v) for k, v in bt.items()},
            'n_valid': int(n_vm),
        })

    print(f"{'块':>3s}  {'场次':>4s}  {'日期范围':>26s}  {'走水预测率':>10s}  {'走水实际率':>10s}  {'准确率':>7s}  {'有效':>4s}")
    for r in rows:
        act_s = f"{r['draw_actual_rate']*100:.1f}%" if r['draw_actual_rate'] is not None else " — "
        print(f"  {r['block']:>2d}  {r['n']:>4d}  {r['date_range']:>26s}  {r['draw_pred_rate']*100:>9.1f}%  {act_s:>10s}  {r['accuracy']*100:>6.1f}%  {r['n_valid']:>4d}")

    print("\n" + "=" * 70)
    print(f"📊 全量 {N} 场汇总 vs 评估报告基准 (测试集 783 场)")
    print("=" * 70)

    pred_counts, actual_counts = distribution(list(y_pred), y_true)
    total_pred = max(N, 1)
    total_actual = max(sum(actual_counts.values()), 1)
    draw_pred_r = pred_counts['走水'] / total_pred
    draw_actual_r = actual_counts['走水'] / total_actual
    home_pred_r = pred_counts['上盘赢'] / total_pred
    away_pred_r = pred_counts['下盘赢'] / total_pred

    # 评估报告基准
    # (已在前面统一定义 base_draw_pred/base_draw_actual/base_acc/base_home_pred/base_away_pred)

    print(f"\n  {'指标':>14s}  {'评估报告(783场)':>16s}  {'实际比赛(N场)':>16s}  {'偏差':>8s}  {'状态':>6s}")
    print(f"  {'样本数':>14s}  {str(783):>16s}  {str(N):>16s}  {'':>8s}  {'':>6s}")
    def fmt(pct, ok):
        return "✅" if ok else "⚠️"
    draw_ok = 0.20 <= draw_pred_r <= 0.24
    print(f"  {'走水预测占比':>14s}  {base_draw_pred*100:>14.1f}%  {draw_pred_r*100:>14.1f}%  {abs(draw_pred_r-base_draw_pred)*100:>7.2f}pp  {fmt(draw_pred_r, draw_ok):>6s}")
    draw_act_ok = abs(draw_actual_r - base_draw_actual) <= 0.06
    print(f"  {'走水实际占比':>14s}  {base_draw_actual*100:>14.1f}%  {draw_actual_r*100:>14.1f}%  {abs(draw_actual_r-base_draw_actual)*100:>7.2f}pp  {fmt(draw_actual_r, draw_act_ok):>6s}")
    acc_ok = abs(acc - base_acc) <= 0.10
    print(f"  {'准确率':>14s}  {base_acc*100:>14.1f}%  {acc*100:>14.1f}%  {abs(acc-base_acc)*100:>7.2f}pp  {fmt(acc, acc_ok):>6s}")
    print(f"  {'上盘赢预测占比':>14s}  {base_home_pred*100:>14.1f}%  {home_pred_r*100:>14.1f}%  {abs(home_pred_r-base_home_pred)*100:>7.2f}pp")
    print(f"  {'下盘赢预测占比':>14s}  {base_away_pred*100:>14.1f}%  {away_pred_r*100:>14.1f}%  {abs(away_pred_r-base_away_pred)*100:>7.2f}pp")

    # 预测分布
    print(f"\n  📈 预测分布（{N} 场）:")
    print(f"    {'类别':>6s}  {'预测':>6s}  {'实际':>6s}  {'偏差':>6s}  {'占比':>7s}")
    for name in LABEL_NAMES:
        p = pred_counts[name]
        a = actual_counts[name]
        print(f"    {name:>6s}  {p:>6d}  {a:>6d}  {p-a:+6d}  {p/total_pred*100:>6.1f}%")

    # 走水稳定性结论
    print(f"\n" + "=" * 70)
    print("📋 走水预测率稳定性结论")
    print("=" * 70)

    block_draw_rates = [r['draw_pred_rate'] for r in rows]
    blocks_mean = np.mean(block_draw_rates)
    blocks_std = np.std(block_draw_rates) if len(block_draw_rates) > 1 else 0.0
    target_lo, target_hi = 0.20, 0.22

    print(f"\n  目标区间: 走水预测率 ∈ [20%, 22%]  (评估报告 21.3% ± 1%)")
    print(f"  全量 {N} 场: {draw_pred_r*100:.2f}% {'✅' if target_lo <= draw_pred_r <= target_hi else '⚠️'}")
    print(f"  分块均值 ± 标准差: {blocks_mean*100:.2f}% ± {blocks_std*100:.2f}%")
    print(f"  分块范围: [{min(block_draw_rates)*100:.1f}%, {max(block_draw_rates)*100:.1f}%]")
    print(f"  每块结果: " + " / ".join([f"块{r['block']}={r['draw_pred_rate']*100:.1f}%" for r in rows]))

    all_in = all(target_lo - 0.03 <= r <= target_hi + 0.05 for r in block_draw_rates)
    if draw_ok and all_in:
        print(f"\n  ✅✅✅ 稳定性验证通过：")
        print(f"     - 全量 {N} 场走水预测率 {draw_pred_r*100:.1f}% 落在目标区间 [20%, 22%] 附近")
        print(f"     - 各分块无极端偏离（≥30%或≤5%），分布均匀")
        print(f"     - 与评估报告 783 场基准的偏差 < 3pp，一致")
    elif draw_ok:
        print(f"\n  ✅ 部分通过：全量 {N} 场符合，但分块存在轻微波动（正常，N=30 样本仍小）")
    else:
        print(f"\n  ⚠️ 未通过：走水预测率偏离目标区间，需要检查阈值或 class_weight")

    # 保存
    report = {
        'timestamp': ts,
        'n_matches': N,
        'train_valid_min_date': TRAIN_VALID_MIN_DATE,
        'mode': 'with_rules' if USE_RULES else 'plan_a_no_rules',
        'date_range': [str(m_n['date'].min()), str(m_n['date'].max())],
        'optimization_params': {
            'class_weight': OPTIMAL_CLASS_WEIGHT,
            'temperature': OPTIMAL_TEMPERATURE,
            'dynamic_thresholds': OPTIMAL_DYNAMIC_THRESHOLDS,
            'use_rule_engine': USE_RULES,
        },
        'blocks': rows,
        'aggregate': {
            'predicted_counts': pred_counts,
            'actual_counts': actual_counts,
            'draw_pred_rate': float(draw_pred_r),
            'draw_actual_rate': float(draw_actual_r),
            'accuracy': float(acc),
            'n_valid': int(n_valid),
        },
        'baseline_comparison': {
            'baseline_n': REPORT_BASELINE['n'],
            'baseline_draw_pred_rate': base_draw_pred,
            'baseline_draw_actual_rate': base_draw_actual,
            'baseline_accuracy': base_acc,
            'abs_dev_draw_pred_rate': abs(float(draw_pred_r) - base_draw_pred),
            'abs_dev_accuracy': abs(float(acc) - base_acc),
        },
    }
    rp = os.path.join(REPORT_DIR, f't005v2_stability_{N}matches_{ts}.json')
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n💾 报告: {rp}")


if __name__ == '__main__':
    main()
