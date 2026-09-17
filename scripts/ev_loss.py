# -*- coding: utf-8 -*-
"""
C-20260904-003: 训练端 EV/ROI 目标改造 — 多分类 EV-policy Loss 自定义目标（XGBoost / LightGBM）
================================================================================================
背景（概率层四连证伪后唯一剩路）：
  纯 WDL 交叉熵只优化「对错」不优化「盈利」，模型在极端赔率 / 高 edge 区域系统性高估
  （edge 分桶非单调、>10pp 桶仍负 ROI）。本模块把训练目标改为 CE 锚点 + λ·EV 分量，
  使梯度直接对 EV 求导，让模型容量集中到「高赔率真结果」等高价值样本上。

损失（单样本，真实类别 y，softmax 概率 p，市场赔率 o，o_y=真方向赔率）:
    EV  = p_y * o_y - 1                          （对真实方向下注 1 元的期望盈利）
    EVL = -EV = 1 - p_y * o_y                    （EV 目标：直接对 EV 求负）
    L   = CE + λ * EVL = -log(p_y) + λ*(1 - p_y*o_y)

梯度（对 logit z_k，REINFORCE 策略梯度形式）:
    g_CE,k   = p_k - δ_{k,y}
    g_EV,y   = -p_y * o_y * (1 - p_y)            （真类别，向上推，力度 ∝ o_y）
    g_EV,k≠y =  p_k * p_y * o_y                  （错类别，向下推）
    总 g_k   = g_CE,k + λ * g_EV,k

对角海森:
    H_CE,k   = p_k * (1 - p_k)
    H_EV,k   = p_k * (EV - r_k) * (1 - 2*p_k)    （r_k = (o_k-1) if k==y else -1）
    总 H_k   = H_CE,k + λ * max(H_EV,k, floor)   （海森下限保护，同 focal）

与样本级利润加权 CE 的区别：真类梯度多一个 p_y 因子 —— 即策略梯度（REINFORCE），
λ=0 时严格退化为 softmax 交叉熵（自检锚点）。

约定（与生产一致）:
    y 编码: 0=客胜, 1=平局, 2=主胜
    odds 列序 = [客胜, 平局, 主胜]（wdl_history 收盘: win_b=客胜, draw=平, win_a=主胜）
    XGBoost predt 形状 (n, K)；LightGBM predt 形状 (n*K,) 扁平
    样本权重（class_weights/异常加权）由自定义目标内部对 get_weight() 施加
    （XGBoost/LightGBM 自定义目标下权重不会自动乘到 grad/hess）

验证: `ev_gradcheck()` 用中心差分对比解析梯度/海森；λ=0 与交叉熵一致。
"""

import numpy as np

DEFAULT_LAMBDA_CE = 1.0
DEFAULT_ODDS_CAP = 10.0
EPS = 1e-7
HESS_FLOOR = 1e-6

# 模块级赔率状态（configure 绑定，与训练折行序 1:1 对齐，列序 [客胜,平局,主胜]）
_ODDS = None  # (n, 3) float；无赔率样本为 NaN


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _ev_loss_value(z, y, odds, lambda_ce=DEFAULT_LAMBDA_CE, odds_cap=DEFAULT_ODDS_CAP):
    """总损失（用于有限差分校验，与逐样本梯度保持同一缩放）。"""
    p = _softmax(z)
    n = p.shape[0]
    p = np.clip(p, EPS, 1.0 - EPS)
    py = p[np.arange(n), y]
    ce = -np.log(py)
    oy = odds[np.arange(n), y]
    oy = np.clip(oy, 1.3, odds_cap)
    evl = 1.0 - py * oy
    loss = ce + lambda_ce * evl
    # 无赔率样本（NaN）仅保留 CE 分量
    loss = np.where(np.isnan(oy), ce, loss)
    return float(loss.sum())


def ev_grad_hess(z, y, odds, lambda_ce=DEFAULT_LAMBDA_CE, odds_cap=DEFAULT_ODDS_CAP):
    """计算 EV-policy Loss 的梯度与对角海森（对原始 logit z）。

    参数:
        z: (n, K) 原始 logit（未过 softmax），K=3，列序 [客胜,平局,主胜]
        y: (n,)   真实标签 [0,1,2]
        odds: (n, K) 三向市场赔率（列序同 z）；NaN 行视为无赔率 → 仅 CE
        lambda_ce: EV 分量权重（CE 权重恒为 1.0）
        odds_cap: 真方向赔率截断上界（防极端冷门梯度爆炸）

    返回:
        (grad, hess): 均为 (n, K) 数组
    """
    grad, hess_raw = _ev_grad_hess_raw(z, y, odds, lambda_ce, odds_cap)
    # 海森下限保护（EV 分量非凸，难分样本处可负/近零，防分裂增益失效）
    hess = np.maximum(hess_raw, HESS_FLOOR)
    return grad, hess


def _ev_grad_hess_raw(z, y, odds, lambda_ce, odds_cap):
    """未加海森下限保护的原始梯度/海森（供 gradcheck 校验，避免 floor 掩盖公式误差）。"""
    z = np.asarray(z, dtype=np.float64)
    y = np.asarray(y, dtype=np.int64)
    odds = np.asarray(odds, dtype=np.float64)
    n, K = z.shape

    p = _softmax(z)
    p = np.clip(p, EPS, 1.0 - EPS)

    oy = odds[np.arange(n), y]
    oy = np.clip(oy, 1.3, odds_cap)
    has_odds = ~np.isnan(oy)

    py = p[np.arange(n), y]
    ev = py * oy - 1.0  # 真方向 EV（p_y * o_y - 1）

    # --- 梯度 ---
    onehot = np.zeros((n, K), dtype=np.float64)
    onehot[np.arange(n), y] = 1.0
    g_ce = p - onehot                                  # (n, K)

    g_ev = np.empty_like(p)
    for k in range(K):
        # 通用式: g_EV,k = p_k * (EV - r_k)，r_k = (o_k-1) if k==y else -1
        rk = np.where(y == k, oy - 1.0, -1.0)
        g_ev[:, k] = p[:, k] * (ev - rk)
    g_ev = np.where(has_odds[:, None], g_ev, 0.0)      # 无赔率 → 纯 CE

    grad = g_ce + lambda_ce * g_ev

    # --- 对角海森（原始，不加下限保护）---
    h_ce = p * (1.0 - p)                               # (n, K)
    h_ev = np.empty_like(p)
    for k in range(K):
        rk = np.where(y == k, oy - 1.0, -1.0)
        h_ev[:, k] = p[:, k] * (ev - rk) * (1.0 - 2.0 * p[:, k])
    h_ev = np.where(has_odds[:, None], h_ev, 0.0)
    hess_raw = h_ce + lambda_ce * h_ev

    return grad, hess_raw


def ev_obj_xgb(predt, dtrain):
    """XGBoost 自定义目标入口（xgb.train(obj=...)）。

    XGBoost 2.1+ 要求 grad/hess 形状为 (n_samples, n_classes)，predt 形状 (n, K)。
    样本权重（class_weights 等）在此显式施加（自定义目标不会自动乘）。
    """
    y = dtrain.get_label().astype(np.int64)
    lambda_ce = getattr(ev_obj_xgb, 'lambda_ce', DEFAULT_LAMBDA_CE)
    odds_cap = getattr(ev_obj_xgb, 'odds_cap', DEFAULT_ODDS_CAP)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1)
    grad, hess = ev_grad_hess(predt, y, _ODDS, lambda_ce=lambda_ce, odds_cap=odds_cap)
    if _ODDS is not None and _ODDS.shape[0] == len(y):
        w = dtrain.get_weight()
        if w is not None and w.size:
            grad = grad * w[:, None]
            hess = hess * w[:, None]
    return grad, hess


def ev_fobj_lgb(predt, train_data):
    """LightGBM 自定义目标入口（传入 params['objective']，LightGBM 4.x 写法）。

    LightGBM 4.x 的 predt / grad / hess 均为展平的 (n*K,) 一维数组，且为
    **class-major 布局**（basic.py 用 `order='F'` reshape 还原）：
    predt[c*n + i] = 样本 i 类 c 的 logit。必须用 order='F' reshape/ravel，
    否则梯度布局错乱导致模型学不动（"No further splits"、概率扁平化）。
    """
    y = train_data.get_label().astype(np.int64)
    lambda_ce = getattr(ev_fobj_lgb, 'lambda_ce', DEFAULT_LAMBDA_CE)
    odds_cap = getattr(ev_fobj_lgb, 'odds_cap', DEFAULT_ODDS_CAP)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1, order='F')
    grad, hess = ev_grad_hess(predt, y, _ODDS, lambda_ce=lambda_ce, odds_cap=odds_cap)
    if _ODDS is not None and _ODDS.shape[0] == len(y):
        w = train_data.get_weight()
        if w is not None and w.size:
            grad = grad * w[:, None]
            hess = hess * w[:, None]
    return grad.ravel(order='F'), hess.ravel(order='F')


def ev_feval_lgb(preds, train_data):
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


def configure(odds, lambda_ce=DEFAULT_LAMBDA_CE, odds_cap=DEFAULT_ODDS_CAP):
    """把赔率矩阵 / λ / 截断绑定到两个入口函数（供训练脚本在调用前设置）。

    odds: (n, 3) 三向赔率，列序 [客胜, 平局, 主胜]，与训练折行序 1:1 对齐。
    """
    global _ODDS
    _ODDS = np.asarray(odds, dtype=np.float64).reshape(-1, 3)
    ev_obj_xgb.lambda_ce = float(lambda_ce)
    ev_obj_xgb.odds_cap = float(odds_cap)
    ev_fobj_lgb.lambda_ce = float(lambda_ce)
    ev_fobj_lgb.odds_cap = float(odds_cap)


def ev_gradcheck(n=16, K=3, lambda_ce=DEFAULT_LAMBDA_CE, h=1e-5, tol=1e-4):
    """中心差分校验解析梯度与对角海森，返回最大绝对误差。

    用「解析梯度」的中心差分校验海森，避免二阶差分 h^2 数值噪声（同 focal_gradcheck）。
    用法: python scripts/ev_loss.py
    """
    rng = np.random.default_rng(42)
    z = rng.normal(size=(n, K))
    y = rng.integers(0, K, size=n)
    odds = np.round(rng.uniform(1.5, 8.0, size=(n, K)), 2)  # 合理赔率区间

    grad_ana, _ = ev_grad_hess(z, y, odds, lambda_ce=lambda_ce)
    _, hess_ana = _ev_grad_hess_raw(z, y, odds, lambda_ce, DEFAULT_ODDS_CAP)  # 校验原始公式（无 floor）

    grad_num = np.zeros_like(grad_ana)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            lp = _ev_loss_value(zp, y, odds, lambda_ce=lambda_ce)
            lm = _ev_loss_value(zm, y, odds, lambda_ce=lambda_ce)
            grad_num[i, k] = (lp - lm) / (2 * h)

    hess_num = np.zeros_like(hess_ana)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            gp, _ = ev_grad_hess(zp, y, odds, lambda_ce=lambda_ce)
            gm, _ = ev_grad_hess(zm, y, odds, lambda_ce=lambda_ce)
            hess_num[i, k] = (gp[i, k] - gm[i, k]) / (2 * h)

    g_err = float(np.abs(grad_ana - grad_num).max())
    hess_err = float(np.abs(hess_ana - hess_num).max())

    print("\n[EV-Loss ev_gradcheck] 有限差分校验（lambda_ce=%.1f, h=%.1e）" % (lambda_ce, h))
    print(f"  梯度最大绝对误差: {g_err:.3e}   (tol={tol})")
    print(f"  海森最大绝对误差: {hess_err:.3e}   (tol={tol})")
    print(f"  {'' if (g_err < tol and hess_err < tol) else '!! '}结论: "
          f"{'校验通过' if (g_err < tol and hess_err < tol) else '校验失败'}")

    # λ=0 应退化为交叉熵
    g0, h0 = ev_grad_hess(z, y, odds, lambda_ce=0.0)
    p = _softmax(z)
    onehot = np.zeros((n, K)); onehot[np.arange(n), y] = 1.0
    ce_g = p - onehot
    ce_h = p * (1.0 - p)
    print(f"  lambda=0 退化校验: grad误差={np.abs(g0 - ce_g).max():.3e} "
          f"hess误差={np.abs(h0 - ce_h).max():.3e}")

    return g_err, hess_err


if __name__ == '__main__':
    ev_gradcheck(n=16, K=3, lambda_ce=1.0)
    ev_gradcheck(n=16, K=3, lambda_ce=3.0)
    ev_gradcheck(n=16, K=3, lambda_ce=0.0)
