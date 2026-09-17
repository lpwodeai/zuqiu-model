"""
P1-9 验证脚本（临时，验证完可删）
================================

验证内容:
    1. A 组基线: build_all_features(slim_odds=True, ts_odds=False) → 165 维无回归
    2. B 组实验: build_all_features(slim_odds=True, ts_odds=True) → +D-013(22) +T-003.1(8)
    3. 新特征非零率 / 比分赔率覆盖率（验收: ≥70%）
    4. 滚动时间切分 LightGBM A/B: Acc / LogLoss / RPS / DrawRecall

验收标准（model_gap_analysis_report_v2.0.md P1-9）:
    - 时序赔率对齐率 ≥98%（前置任务已达成 99.4%）
    - 滚动赛季切分 A/B: B 组 RPS/LogLoss 优于 A 基线
    - 比分赔率特征非零率 ≥70%
"""

import os
import sys
import time
import numpy as np
import pandas as pd

# 启用实时输出，避免重定向时缓冲
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from feature_utils import load_match_data_odds, build_all_features

# LightGBM 最优参数（project_memory 规则）
LGB_PARAMS = dict(
    objective='multiclass', num_class=3,
    max_depth=4, learning_rate=0.12, n_estimators=80,
    num_leaves=128, min_child_samples=60,
    random_state=42, n_jobs=-1, verbose=-1,
)

# result 编码: 2=主胜, 1=平, 0=客胜 → RPS 累积顺序 [主胜, 平, 客胜] = [2, 1, 0]
RPS_CLASS_ORDER = [2, 1, 0]


def rps_score(y_true: np.ndarray, proba: np.ndarray) -> float:
    """Ranked Probability Score（3类，K-1=2 归一化）"""
    order = RPS_CLASS_ORDER
    col_of = {c: i for i, c in enumerate(order)}
    idx = np.array([col_of[y] for y in y_true])
    P = proba[:, order]
    O = np.zeros_like(P)
    O[np.arange(len(y_true)), idx] = 1.0
    cumP = np.cumsum(P, axis=1)[:, :-1]
    cumO = np.cumsum(O, axis=1)[:, :-1]
    return float(np.mean(np.sum((cumP - cumO) ** 2, axis=1)) / 2.0)


def eval_split(X_train, y_train, X_test, y_test, tag):
    import lightgbm as lgb
    t0 = time.time()
    model = lgb.LGBMClassifier(**LGB_PARAMS)
    model.fit(X_train, y_train)
    proba = model.predict_proba(X_test)
    pred = np.argmax(proba, axis=1)

    # LGBM 类别顺序
    class_order = list(model.classes_)
    proba_ordered = proba[:, [class_order.index(c) for c in RPS_CLASS_ORDER]]
    y_test_arr = np.asarray(y_test)

    acc = float(np.mean(pred == y_test_arr))
    ll_rows = proba_ordered[np.arange(len(y_test_arr)),
                            [RPS_CLASS_ORDER.index(y) for y in y_test_arr]]
    ll = float(np.mean(-np.log(np.clip(ll_rows, 1e-9, 1.0))))
    rps = rps_score(y_test_arr, proba_ordered)
    draw_mask = y_test_arr == 1
    draw_recall = float((pred[draw_mask] == 1).mean()) if draw_mask.sum() else float('nan')
    print(f"   [{tag}] Acc={acc:.4f} | LogLoss={ll:.4f} | RPS={rps:.4f} "
          f"| DrawRecall={draw_recall:.4f} | 平局预测数={int((pred == 1).sum())} "
          f"| ({time.time() - t0:.1f}s)")
    return dict(acc=acc, ll=ll, rps=rps, draw_recall=draw_recall)


def main():
    print("=" * 70)
    print("[P1-9 验证] 时序赔率 + 比分赔率特征接入")
    print("=" * 70)

    df = load_match_data_odds()
    print(f"比赛数据: {len(df)} 场, 联赛分布: {dict(df['competition_name'].value_counts())}")

    # ---- 特征构建（带磁盘缓存，避免重复 20 分钟构建）----
    cache_path = os.path.join(_SCRIPT_DIR, '..', 'logs', '_tmp_p19_X_cache.pkl')
    if os.path.exists(cache_path):
        print("\n[缓存] 加载已构建的 X_A / X_B ...")
        import pickle
        with open(cache_path, 'rb') as f:
            cache = pickle.load(f)
        X_A, X_B, y = cache['X_A'], cache['X_B'], cache['y']
        print(f"   A 组维度: {X_A.shape[1]} | B 组维度: {X_B.shape[1]}")
    else:
        # ---- 1. A 组基线 ----
        print("\n[1] A 组基线: slim_odds=True, ts_odds=False")
        t0 = time.time()
        X_A, y = build_all_features(df, slim_odds=True, ts_odds=False)
        print(f"   A 组维度: {X_A.shape[1]} ({time.time() - t0:.1f}s)")

        # ---- 2. B 组实验 ----
        print("\n[2] B 组实验: slim_odds=True, ts_odds=True")
        t0 = time.time()
        X_B, _ = build_all_features(df, slim_odds=True, ts_odds=True)
        print(f"   B 组维度: {X_B.shape[1]} ({time.time() - t0:.1f}s)")

        import pickle
        with open(cache_path, 'wb') as f:
            pickle.dump({'X_A': X_A, 'X_B': X_B, 'y': y}, f)
        print(f"   [缓存] 已保存到 {cache_path}")

    new_cols = [c for c in X_B.columns if c not in X_A.columns]
    print(f"\n[3] 新增特征 {len(new_cols)} 维:")
    print(f"   {new_cols}")

    # 非零率
    print("\n[4] 新特征非零率（验收: score_* 非零率 ≥70%）:")
    for c in new_cols:
        nz = (X_B[c] != 0).mean() * 100
        flag = ' [达标]' if nz >= 70 else ''
        print(f"   {c}: {nz:.1f}%{flag}")

    # ---- 5. 滚动时间切分 A/B ----
    print("\n[5] 滚动时间切分 LightGBM A/B（无 shuffle，防泄漏）")
    y_arr = np.asarray(y)

    cutoffs = [(0.60, 0.80), (0.80, 1.00)]
    results = {'A': [], 'B': []}
    for tr_end, te_end in cutoffs:
        n = len(df)
        i1, i2 = int(n * tr_end), int(n * te_end)
        print(f"\n   Fold train[:{i1}] test[{i1}:{i2}]:")
        results['A'].append(eval_split(
            X_A.iloc[:i1], y_arr[:i1], X_A.iloc[i1:i2], y_arr[i1:i2], 'A基线'))
        results['B'].append(eval_split(
            X_B.iloc[:i1], y_arr[:i1], X_B.iloc[i1:i2], y_arr[i1:i2], 'B实验'))

    # ---- 6. 汇总 ----
    print("\n" + "=" * 70)
    print("[汇总] 滚动切分均值")
    print("=" * 70)
    for k, label in [('acc', 'Acc'), ('ll', 'LogLoss'), ('rps', 'RPS'),
                     ('draw_recall', 'DrawRecall')]:
        a = np.nanmean([r[k] for r in results['A']])
        b = np.nanmean([r[k] for r in results['B']])
        diff = b - a
        better = 'B更优' if ((k in ('ll', 'rps') and diff < 0) or
                            (k in ('acc', 'draw_recall') and diff > 0)) else 'A更优'
        print(f"   {label:12s}: A={a:.4f}  B={b:.4f}  diff={diff:+.4f} ({better})")


if __name__ == '__main__':
    main()
