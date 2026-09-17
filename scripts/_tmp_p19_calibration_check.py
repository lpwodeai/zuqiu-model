# -*- coding: utf-8 -*-
"""
P1-9 遗留② 完整校准链路复验（临时，验证完可删）
================================================

上一版 A/B（_tmp_p19_feature_check.py）仅训练「快速 LightGBM」且未走生产校准链路。
本脚本补齐：XGBoost + LightGBM 双 GBDT（生产最优参数）→ 50/50 融合 → 输出 RPS/Acc/LogLoss/DrawRecall。

说明：
  - 生产 WDL 链路 = 5 基础模型 Stacking（LR meta-learner），其中 Poisson/DC/Elo/贝叶斯
    与特征矩阵无关（ts_odds 不影响），只有 XGB/LGB 依赖特征。故双 GBDT 融合的 A/B 比较，
    已能忠实反映 ts_odds 在生产链路中的相对贡献（其余模型恒定、只做稀释）。
  - 滚动时间切分（无 shuffle，防泄漏）同上一版。

验收：B 组（ts_odds=True）RPS 优于 A 基线（ts_odds=False）则建议生产采用。
"""

import os
import sys
import time
import numpy as np

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

# result 编码: 2=主胜, 1=平, 0=客胜 → RPS 累积顺序 [主胜, 平, 客胜] = [2, 1, 0]
RPS_CLASS_ORDER = [2, 1, 0]

LGB_PARAMS = dict(
    objective='multiclass', num_class=3,
    max_depth=4, learning_rate=0.12, n_estimators=80,
    num_leaves=128, min_child_samples=60,
    random_state=42, n_jobs=-1, verbose=-1,
)

XGB_PARAMS = dict(
    objective='multi:softprob', num_class=3,
    max_depth=4, learning_rate=0.07, n_estimators=130,
    subsample=0.785, gamma=5.0, reg_alpha=0.1, reg_lambda=8.0,
    min_child_weight=13, tree_method='hist',
    random_state=42, n_jobs=-1, eval_metric='mlogloss',
)


def rps_score(y_true, proba):
    """Ranked Probability Score（3 类，K-1=2 归一化）。proba 列序=[主胜,平,客胜]。"""
    order = RPS_CLASS_ORDER
    col_of = {c: i for i, c in enumerate(order)}
    idx = np.array([col_of[y] for y in y_true])
    P = proba[:, order]
    O = np.zeros_like(P)
    O[np.arange(len(y_true)), idx] = 1.0
    cumP = np.cumsum(P, axis=1)[:, :-1]
    cumO = np.cumsum(O, axis=1)[:, :-1]
    return float(np.mean(np.sum((cumP - cumO) ** 2, axis=1)) / 2.0)


def metrics(y_true, proba):
    """proba 列序 = [主胜, 平, 客胜]（即 RPS_CLASS_ORDER）。"""
    y_true = np.asarray(y_true)
    pred = proba.argmax(axis=1)
    # argmax 结果 0=主胜,1=平,2=客胜 → 映射回 label: 0->2, 1->1, 2->0
    label_of_col = [2, 1, 0]
    pred_label = np.array([label_of_col[i] for i in pred])
    acc = float(np.mean(pred_label == y_true))
    idx = np.array([RPS_CLASS_ORDER.index(y) for y in y_true])
    p_rows = proba[np.arange(len(y_true)), idx]
    ll = float(np.mean(-np.log(np.clip(p_rows, 1e-9, 1.0))))
    rps = rps_score(y_true, proba)
    draw_mask = y_true == 1
    draw_recall = float((pred_label[draw_mask] == 1).mean()) if draw_mask.sum() else float('nan')
    return dict(acc=acc, ll=ll, rps=rps, draw_recall=draw_recall, pred_label=pred_label)


def train_and_proba(X_tr, y_tr, X_te):
    import lightgbm as lgb
    import xgboost as xgb

    # LightGBM
    lgb_m = lgb.LGBMClassifier(**LGB_PARAMS)
    lgb_m.fit(X_tr, y_tr)
    lgb_p = lgb_m.predict_proba(X_te)          # 列序 = classes_ (0,1,2)
    lgb_p = lgb_p[:, lgb_m.classes_ == np.array(lgb_m.classes_)]
    # 重排为 [主胜(2), 平(1), 客胜(0)]
    lgb_p = lgb_p[:, [np.where(lgb_m.classes_ == c)[0][0] for c in RPS_CLASS_ORDER]]

    # XGBoost
    xgb_m = xgb.XGBClassifier(**XGB_PARAMS)
    xgb_m.fit(X_tr, y_tr)
    xgb_p = xgb_m.predict_proba(X_te)          # 列序 = classes_ (0,1,2)
    xgb_p = xgb_p[:, [np.where(xgb_m.classes_ == c)[0][0] for c in RPS_CLASS_ORDER]]

    return lgb_p, xgb_p


def format_row(m):
    return (f"Acc={m['acc']:.4f} | LogLoss={m['ll']:.4f} | RPS={m['rps']:.4f} "
            f"| DrawRecall={m['draw_recall']:.4f}")


def main():
    print("=" * 74)
    print("[P1-9 遗留②] ts_odds 生产链路复验（XGB+LGB 双 GBDT 融合）")
    print("=" * 74)

    cache_path = os.path.join(_SCRIPT_DIR, '..', 'logs', '_tmp_p19_X_cache.pkl')
    import pickle
    with open(cache_path, 'rb') as f:
        cache = pickle.load(f)
    X_A, X_B, y = cache['X_A'], cache['X_B'], cache['y']
    print(f"特征缓存加载: A={X_A.shape[1]}维 | B={X_B.shape[1]}维 | 样本={len(y)}")
    y_arr = np.asarray(y)

    cutoffs = [(0.60, 0.80), (0.80, 1.00)]
    summary = {'A': {'lgb': [], 'xgb': [], 'blend': []},
               'B': {'lgb': [], 'xgb': [], 'blend': []}}

    for tr_end, te_end in cutoffs:
        n = len(y_arr)
        i1, i2 = int(n * tr_end), int(n * te_end)
        print(f"\n   Fold train[:{i1}] test[{i1}:{i2}]:")

        for grp, X in [('A', X_A), ('B', X_B)]:
            X_tr = X.iloc[:i1].reset_index(drop=True)
            X_te = X.iloc[i1:i2].reset_index(drop=True)
            y_tr = y_arr[:i1]
            y_te = y_arr[i1:i2]
            t0 = time.time()
            lgb_p, xgb_p = train_and_proba(X_tr, y_tr, X_te)
            blend_p = 0.5 * lgb_p + 0.5 * xgb_p
            m_lgb = metrics(y_te, lgb_p)
            m_xgb = metrics(y_te, xgb_p)
            m_blend = metrics(y_te, blend_p)
            print(f"   [{grp}·LGB ] {format_row(m_lgb)} ({time.time()-t0:.1f}s)")
            print(f"   [{grp}·XGB ] {format_row(m_xgb)}")
            print(f"   [{grp}·Blend] {format_row(m_blend)}")
            summary[grp]['lgb'].append(m_lgb)
            summary[grp]['xgb'].append(m_xgb)
            summary[grp]['blend'].append(m_blend)

    print("\n" + "=" * 74)
    print("[汇总] 滚动切分均值（A=ts_odds关闭 / B=ts_odds开启）")
    print("=" * 74)
    for model in ('lgb', 'xgb', 'blend'):
        print(f"\n  --- {model.upper()} ---")
        for k, label in [('acc', 'Acc'), ('ll', 'LogLoss'), ('rps', 'RPS'),
                         ('draw_recall', 'DrawRecall')]:
            a = np.nanmean([r[k] for r in summary['A'][model]])
            b = np.nanmean([r[k] for r in summary['B'][model]])
            diff = b - a
            better = ('B更优' if ((k in ('ll', 'rps') and diff < 0) or
                                  (k in ('acc', 'draw_recall') and diff > 0)) else 'A更优')
            print(f"   {label:12s}: A={a:.4f}  B={b:.4f}  diff={diff:+.4f} ({better})")


if __name__ == '__main__':
    main()