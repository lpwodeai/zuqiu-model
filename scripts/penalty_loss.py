# -*- coding: utf-8 -*-
"""
C-20260905-001: 训练端系统性高估根治 — 高赔率未命中显式惩罚（High-odds Miss Penalty）损失
==========================================================================================
背景（EV 奖励 / MOD 模仿双路径证伪后唯一剩路）：
  C-20260904-003 证伪「EV 奖励」（对高赔率真结果放大梯度 → 加剧过度自信）；
  C-20260904-004 证伪「MOD 模仿」（向市场蒸馏 = 押注市场正确 → 抹掉学「市场定价错误」能力）。
  两者根因同一：模型对**高赔率（高 edge）方向系统性过度自信**——高赔率真结果的实际
  发生频率 < 模型概率（>10pp 桶 ROI 最差）。本模块实现第三条路径：**对「高赔率+未命中」
  方向显式惩罚**——训练目标对抗（而非奖励/模仿）高赔率方向的过度自信。

损失（单样本，softmax 概率 p，三向市场赔率 o，真实标签 y）:
    H' = {k ∈ {客胜,主胜} : o_k ≥ o_thresh 且 k≠y}     （高赔率冷门胜方向，未命中；平局豁免）
    w_k = 1/|H'| if k∈H' else 0                          （归一化权重，|H'|≥1 时）
    P   = Σ_k w_k·p_k                                    （高赔率未命中方向的平均置信 = 过度自信量）
    L   = CE + λ·P = −log(p_y) + λ·Σ_k w_k·p_k

设计要点：
  1. **只惩罚「未命中」方向**——命中方向由 CE 负责正推，避免无差别打压冷门。
  2. **平局豁免**（H' 只含客胜/主胜）——平局赔率中位 3.6（≥3.0 占 97%），若纳入惩罚会
     压低 p_draw 导致平局召回崩溃（MOD 已踩坑）；平局过度自信非主要矛盾（>10pp 桶是冷门胜）。
  3. **惩罚力度 ∝ 置信 p_k**——模型对高赔率方向越自信，惩罚越重（对抗过度自信的本体）。
  4. **归一化** 使 P∈[0,1]，λ 语义清晰（每单位过度自信概率的惩罚强度）。
  5. 与 MOD 本质区别：不把 p 拉向市场 q，只在「高赔率未命中方向」砍掉过度自信的尾巴，
     命中方向与低赔率方向完全不受惩罚 → 保留模型「市场定价错误在哪」的学习能力。

梯度/海森（对原始 logit z_k；softmax 链式，w_k = 1/|H'| if k∈H' else 0，P=Σw·p）:
    grad_k = (p_k − δ_{k,y}) + λ·p_k·(w_k − P)
    hess_k = p_k·(1−p_k) + λ·p_k·(w_k−P)·(1−2p_k)      （可负 → floor 保护，同 EV-loss）
    λ=0 严格退化为 softmax 交叉熵（自检锚点）。

约定（与生产一致）:
    y 编码: 0=客胜, 1=平局, 2=主胜
    odds 列序 = [客胜, 平局, 主胜]；NaN 行视为无赔率 → 仅 CE
    XGBoost predt 形状 (n, K)；LightGBM predt 形状 (n*K,) 扁平（class-major, order='F'）
    样本权重（class_weights/异常加权）由自定义目标内部对 get_weight() 施加

验证: `pen_gradcheck()` 用中心差分对比解析梯度/海森；λ=0 与交叉熵一致。
"""

import numpy as np

DEFAULT_LAMBDA_PEN = 1.0
DEFAULT_ODDS_THRESH = 3.5          # 高赔率胜方向阈值（客胜 p50=3.33、主胜 p25=1.68；>10pp 桶均赔 3.5）
DEFAULT_HESS_FLOOR = 1e-6
EPS = 1e-7

# 模块级赔率状态（configure 绑定，与训练折行序 1:1 对齐，列序 [客胜,平局,主胜]）
_ODDS = None  # (n, 3) float；无赔率样本为 NaN


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _penalty_value(z, y, odds, lambda_pen=DEFAULT_LAMBDA_PEN, odds_thresh=DEFAULT_ODDS_THRESH):
    """总损失（用于有限差分校验，与逐样本梯度保持同一缩放）。"""
    p = _softmax(z)
    n = p.shape[0]
    p = np.clip(p, EPS, 1.0 - EPS)
    ce = -np.log(p[np.arange(n), y])
    o = np.asarray(odds, dtype=np.float64)
    has = np.all(np.isfinite(o) & (o > 1.0), axis=1)
    P = np.zeros(n, dtype=np.float64)
    if has.any():
        P = _penalty_term(p, y, o, odds_thresh)
    loss = ce + lambda_pen * P
    loss = np.where(has, loss, ce)   # 无赔率 → 纯 CE
    return float(loss.sum())


def _penalty_term(p, y, odds, odds_thresh=DEFAULT_ODDS_THRESH):
    """计算样本级惩罚 P = Σ_k w_k·p_k（w_k=1/|H'| if 高赔率未命中胜方向 else 0）。"""
    n = p.shape[0]
    o = np.asarray(odds, dtype=np.float64)
    # 高赔率胜方向（客胜=0, 主胜=2，平局=1 豁免）
    high = np.zeros((n, 3), dtype=np.float64)
    high[:, 0] = (o[:, 0] >= odds_thresh).astype(np.float64)
    high[:, 2] = (o[:, 2] >= odds_thresh).astype(np.float64)
    # 未命中方向剔除真实标签 y
    high[np.arange(n), y] = 0.0
    cnt = high.sum(axis=1)
    w = np.zeros_like(high)
    nz = cnt > 0
    w[nz] = high[nz] / cnt[nz, None]
    return (w * p).sum(axis=1)


def pen_grad_hess(z, y, odds, lambda_pen=DEFAULT_LAMBDA_PEN,
                  odds_thresh=DEFAULT_ODDS_THRESH, hess_floor=DEFAULT_HESS_FLOOR):
    """计算 Penalty Loss 的梯度与对角海森（对原始 logit z）。

    参数:
        z: (n, K) 原始 logit（未过 softmax），K=3，列序 [客胜,平局,主胜]
        y: (n,)   真实标签 [0,1,2]
        odds: (n, K) 三向市场赔率（列序同 z）；NaN 行视为无赔率 → 仅 CE
        lambda_pen: 惩罚分量权重（CE 权重恒为 1.0）
        odds_thresh: 高赔率胜方向阈值（≥ 该赔率视为高赔率冷门）
        hess_floor: 海森下限保护（P 分量非凸，可负/近零，防分裂增益失效）

    返回:
        (grad, hess): 均为 (n, K) 数组
    """
    z = np.asarray(z, dtype=np.float64)
    y = np.asarray(y, dtype=np.int64)
    odds = np.asarray(odds, dtype=np.float64)
    n, K = z.shape

    p = _softmax(z)
    p = np.clip(p, EPS, 1.0 - EPS)

    o = odds
    has = np.all(np.isfinite(o) & (o > 1.0), axis=1)

    # --- 惩罚项 w / P ---
    w = np.zeros((n, K), dtype=np.float64)
    P = np.zeros(n, dtype=np.float64)
    if has.any():
        high = np.zeros((n, K), dtype=np.float64)
        high[:, 0] = (o[:, 0] >= odds_thresh).astype(np.float64)
        high[:, 2] = (o[:, 2] >= odds_thresh).astype(np.float64)
        high[np.arange(n), y] = 0.0
        cnt = high.sum(axis=1)
        nz = cnt > 0
        w[nz] = high[nz] / cnt[nz, None]
        P = (w * p).sum(axis=1)

    # --- 梯度 ---
    onehot = np.zeros((n, K), dtype=np.float64)
    onehot[np.arange(n), y] = 1.0
    g_ce = p - onehot                        # CE 梯度
    g_pen = p * (w - P[:, None])             # 惩罚梯度: dP/dz_k = p_k(w_k - P)
    g_pen = np.where(has[:, None], g_pen, 0.0)
    grad = g_ce + lambda_pen * g_pen

    # --- 对角海森（P 分量可负 → 整体 floor 保护）---
    h_ce = p * (1.0 - p)
    h_pen = p * (w - P[:, None]) * (1.0 - 2.0 * p)
    h_pen = np.where(has[:, None], h_pen, 0.0)
    hess = h_ce + lambda_pen * h_pen
    hess = np.maximum(hess, hess_floor)

    return grad, hess


def pen_obj_xgb(predt, dtrain):
    """XGBoost 自定义目标入口（xgb.train(obj=...)）。

    XGBoost 2.1+ 要求 grad/hess 形状为 (n_samples, n_classes)，predt 形状 (n, K)。
    样本权重（class_weights 等）在此显式施加（自定义目标不会自动乘）。
    """
    y = dtrain.get_label().astype(np.int64)
    lambda_pen = getattr(pen_obj_xgb, 'lambda_pen', DEFAULT_LAMBDA_PEN)
    odds_thresh = getattr(pen_obj_xgb, 'odds_thresh', DEFAULT_ODDS_THRESH)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1)
    grad, hess = pen_grad_hess(predt, y, _ODDS, lambda_pen=lambda_pen, odds_thresh=odds_thresh)
    if _ODDS is not None and _ODDS.shape[0] == len(y):
        w = dtrain.get_weight()
        if w is not None and w.size:
            grad = grad * w[:, None]
            hess = hess * w[:, None]
    return grad, hess


def pen_fobj_lgb(predt, train_data):
    """LightGBM 自定义目标入口（传入 params['objective']，LightGBM 4.x 写法）。

    LightGBM 4.x 的 predt / grad / hess 均为展平的 (n*K,) 一维数组，且为
    **class-major 布局**（basic.py 用 `order='F'` reshape 还原）：
    predt[c*n + i] = 样本 i 类 c 的 logit。必须用 order='F' reshape/ravel，
    否则梯度布局错乱导致模型学不动（"No further splits"、概率扁平化）。
    """
    y = train_data.get_label().astype(np.int64)
    lambda_pen = getattr(pen_fobj_lgb, 'lambda_pen', DEFAULT_LAMBDA_PEN)
    odds_thresh = getattr(pen_fobj_lgb, 'odds_thresh', DEFAULT_ODDS_THRESH)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1, order='F')
    grad, hess = pen_grad_hess(predt, y, _ODDS, lambda_pen=lambda_pen, odds_thresh=odds_thresh)
    if _ODDS is not None and _ODDS.shape[0] == len(y):
        w = train_data.get_weight()
        if w is not None and w.size:
            grad = grad * w[:, None]
            hess = hess * w[:, None]
    return grad.ravel(order='F'), hess.ravel(order='F')


def pen_feval_lgb(preds, train_data):
    """LightGBM feval：softmax 后计算 multi_logloss，修正自定义目标下内置指标不套 softmax 的问题。

    preds 为 class-major 布局（同 objective），须 order='F' reshape。
    """
    y = train_data.get_label().astype(np.int64)
    z = np.asarray(preds, dtype=np.float64).reshape(len(y), -1, order='F')
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    p = e / e.sum(axis=1, keepdims=True)
    p = np.clip(p, 1e-9, 1.0)
    ll = float(np.mean(-np.log(p[np.arange(len(y)), y])))
    return 'multi_logloss', ll, False


def configure(odds, lambda_pen=DEFAULT_LAMBDA_PEN, odds_thresh=DEFAULT_ODDS_THRESH):
    """把赔率矩阵 / λ / 阈值绑定到两个入口函数（供训练脚本在调用前设置）。

    odds: (n, 3) 三向赔率，列序 [客胜, 平局, 主胜]，与训练折行序 1:1 对齐。
    """
    global _ODDS
    _ODDS = np.asarray(odds, dtype=np.float64).reshape(-1, 3)
    pen_obj_xgb.lambda_pen = float(lambda_pen)
    pen_obj_xgb.odds_thresh = float(odds_thresh)
    pen_fobj_lgb.lambda_pen = float(lambda_pen)
    pen_fobj_lgb.odds_thresh = float(odds_thresh)


def pen_gradcheck(n=16, K=3, lambda_pen=DEFAULT_LAMBDA_PEN, odds_thresh=DEFAULT_ODDS_THRESH,
                  h=1e-5, tol=1e-4):
    """中心差分校验解析梯度与对角海森，返回最大绝对误差。

    用法: python scripts/penalty_loss.py
    """
    rng = np.random.default_rng(42)
    z = rng.normal(size=(n, K))
    y = rng.integers(0, K, size=n)
    odds = np.round(rng.uniform(1.5, 8.0, size=(n, K)), 2)  # 合理赔率区间
    # 注入若干无赔率行验证 NaN 退化分支
    odds[0, :] = np.nan
    odds[1, :] = 1.0

    grad_ana, _ = pen_grad_hess(z, y, odds, lambda_pen=lambda_pen, odds_thresh=odds_thresh)
    _, hess_raw = pen_grad_hess(z, y, odds, lambda_pen=lambda_pen, odds_thresh=odds_thresh,
                                hess_floor=-np.inf)   # 负无穷取消 clamp，校验原始公式

    grad_num = np.zeros_like(grad_ana)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            lp = _penalty_value(zp, y, odds, lambda_pen=lambda_pen, odds_thresh=odds_thresh)
            lm = _penalty_value(zm, y, odds, lambda_pen=lambda_pen, odds_thresh=odds_thresh)
            grad_num[i, k] = (lp - lm) / (2 * h)

    hess_num = np.zeros_like(hess_raw)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            gp, _ = pen_grad_hess(zp, y, odds, lambda_pen=lambda_pen, odds_thresh=odds_thresh,
                                  hess_floor=-np.inf)
            gm, _ = pen_grad_hess(zm, y, odds, lambda_pen=lambda_pen, odds_thresh=odds_thresh,
                                  hess_floor=-np.inf)
            hess_num[i, k] = (gp[i, k] - gm[i, k]) / (2 * h)

    g_err = float(np.abs(grad_ana - grad_num).max())
    hess_err = float(np.abs(hess_raw - hess_num).max())

    print("\n[Penalty-Loss pen_gradcheck] 有限差分校验（lambda=%.1f, thresh=%.1f, h=%.1e）"
          % (lambda_pen, odds_thresh, h))
    print(f"  梯度最大绝对误差: {g_err:.3e}   (tol={tol})")
    print(f"  海森最大绝对误差: {hess_err:.3e}   (tol={tol})")
    print(f"  {'' if (g_err < tol and hess_err < tol) else '!! '}结论: "
          f"{'校验通过' if (g_err < tol and hess_err < tol) else '校验失败'}")

    # λ=0 应退化为交叉熵
    g0, h0 = pen_grad_hess(z, y, odds, lambda_pen=0.0, odds_thresh=odds_thresh)
    p = _softmax(z)
    onehot = np.zeros((n, K)); onehot[np.arange(n), y] = 1.0
    ce_g = p - onehot
    ce_h = p * (1.0 - p)
    print(f"  lambda=0 退化校验: grad误差={np.abs(g0 - ce_g).max():.3e} "
          f"hess误差={np.abs(h0 - ce_h).max():.3e}")

    # 方向性校验（构造明确场景）：y=主胜(2)，客胜=5.0 高赔率未命中，平局=3.0 豁免，主胜=1.5 真实
    # H'={客胜} → 惩罚梯度对客胜应为正（优化压低 logit）、对主胜应为负（抬高 logit）
    zz = np.zeros((1, K))
    yy = np.array([2])
    oo = np.array([[5.0, 3.0, 1.5]])
    gp, _ = pen_grad_hess(zz, yy, oo, lambda_pen=1.0, odds_thresh=3.5, hess_floor=-np.inf)
    pz = _softmax(zz)
    pen_only = gp - (pz - np.eye(K)[yy])   # 扣除 CE 梯度分量（基于 zz 的 softmax）
    print(f"  方向性校验: 惩罚梯度 客胜={pen_only[0, 0]:+.4f} (期望 >0) | "
          f"平局={pen_only[0, 1]:+.4f} (豁免≈0) | 主胜={pen_only[0, 2]:+.4f} (期望 <0)")
    ok_dir = pen_only[0, 0] > 0 and pen_only[0, 2] < 0
    print(f"  {'✅' if ok_dir else '❌'} 惩罚方向正确（高赔率未命中方向被压低，真实方向被抬高）")

    return g_err, hess_err


if __name__ == '__main__':
    pen_gradcheck(n=16, K=3, lambda_pen=1.0, odds_thresh=3.5)
    pen_gradcheck(n=16, K=3, lambda_pen=3.0, odds_thresh=3.5)
    pen_gradcheck(n=16, K=3, lambda_pen=0.0, odds_thresh=3.5)
