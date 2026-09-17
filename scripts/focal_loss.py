# -*- coding: utf-8 -*-
"""
P2-13: 平局模型层面解决 — 多分类 Focal Loss 自定义目标（XGBoost / LightGBM）
=============================================================================
对 GBDT 三分类（客胜/平/主胜）提供 Focal Loss 自定义目标函数，让模型在训练层面
直接放大「难分样本」（尤其是少数类平局、爆冷）的梯度权重，从损失函数层面缓解
平局召回率依赖后处理补丁的问题。

公式（单样本，真实类别 t，softmax 输出 p，聚焦参数 gamma）:
    FL = -(1 - p_t)^gamma * log(p_t)
    （可选 alpha 类别权重，用于平局-specific 上浮，alpha_draw > 1）

与交叉熵的关系：
    当 gamma=0 时，FL = -log(p_t)，梯度/海森严格退化为 softmax 交叉熵
    grad_k = p_k - y_k, hess_k = p_k(1-p_k)。本模块以此作为自检锚点。

梯度/海森（对原始 logit z 求导，维度 (n, K) 展平为 (n*K,)）:
    a   = 1 - p_t
    A   = -a^gamma + gamma * p_t * a^(gamma-1) * log(p_t)
    grad_k        = A * (delta_{t,k} - p_k)
    dA/dp_t       = gamma * a^(gamma-2) * [ 2a + gamma*a*log(p_t) - (gamma-1)*log(p_t) ]
    对角海森 H_kk = dA/dp_t * p_t * (delta_{t,k} - p_k)^2  -  A * p_k * (1 - p_k)

验证：
    通过 `focal_gradcheck()` 用中心差分（finite difference）对比解析梯度/海森，
    gamma=0 时与交叉熵一致、gamma>0 时数值一致即证明推导正确。

约定：
    y 标签编码与生产一致：0=客胜, 1=平局, 2=主胜。
    proba 列序 = [客胜, 平, 主胜]。
    XGBoost predt 形状 (n, K)；LightGBM predt 形状 (n*K,) 扁平。
"""

import numpy as np

DEFAULT_GAMMA = 2.0
EPS = 1e-7


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _focal_loss_value(z, y, gamma=DEFAULT_GAMMA, alpha=None):
    """Focal Loss 总和（用于有限差分校验，与逐样本梯度保持同一缩放）。alpha: (3,) 类别权重，None 表示等权。"""
    p = _softmax(z)
    n = p.shape[0]
    p = np.clip(p, EPS, 1.0 - EPS)
    pt = p[np.arange(n), y]
    loss = -((1.0 - pt) ** gamma) * np.log(pt)
    if alpha is not None:
        loss = loss * alpha[y]
    return float(loss.sum())


def focal_grad_hess(z, y, gamma=DEFAULT_GAMMA, alpha=None):
    """计算 Focal Loss 的梯度与对角海森（对原始 logit z）。

    参数:
        z: (n, K) 原始 logit（未过 softmax）
        y: (n,)   真实标签 [0,1,2]
        gamma: 聚焦参数（默认 2.0）
        alpha: (K,) 类别权重（用于平局上调），None 表示等权

    返回:
        (grad, hess): 均为展平后的 (n*K,) 一维数组
    """
    z = np.asarray(z, dtype=np.float64)
    y = np.asarray(y, dtype=np.int64)
    n, K = z.shape

    p = _softmax(z)
    p = np.clip(p, EPS, 1.0 - EPS)

    pt = p[np.arange(n), y]               # (n,)
    a = np.clip(1.0 - pt, EPS, 1.0)       # (n,)
    log_pt = np.log(pt)

    # 标量 A（与类别 k 无关）
    A = -(a ** gamma) + gamma * pt * (a ** (gamma - 1.0)) * log_pt

    # 梯度: grad_k = A * (delta_{t,k} - p_k)
    onehot = np.zeros((n, K), dtype=np.float64)
    onehot[np.arange(n), y] = 1.0
    grad = A[:, None] * (onehot - p)      # (n, K)

    # dA/dp_t
    B = 2.0 * a + gamma * a * log_pt - (gamma - 1.0) * log_pt
    dAdpt = gamma * (a ** (gamma - 2.0)) * B

    # 对角海森: H_kk = dAdpt * pt * (delta_{t,k} - p_k)^2 - A * p_k * (1 - p_k)
    hess = np.empty_like(p)
    for k in range(K):
        pk = p[:, k]
        delta = (y == k).astype(np.float64)
        hess[:, k] = dAdpt * pt * ((delta - pk) ** 2) - A * pk * (1.0 - pk)

    # 可选类别权重（平局-specific 上浮）
    if alpha is not None:
        w = np.asarray(alpha, dtype=np.float64)[y]   # (n,)
        grad = grad * w[:, None]
        hess = hess * w[:, None]

    # 海森下限保护（P2-13 A/B 关键修复）：
    # Focal Loss 非凸，难分样本(pt→0)处海森可为负/近零，导致 LightGBM/XGBoost
    # 树分裂增益失效（"No further splits with positive gain, best gain: -inf"）。
    # 对负值/近零海森做下限截断，保证二阶分裂可用。
    hess = np.maximum(hess, 1e-6)

    return grad, hess  # (n, K)（调用方按需展平）


def focal_obj_xgb(predt, dtrain):
    """XGBoost 自定义目标入口（xgb.train(obj=...)）。

    XGBoost 2.1+ 要求 grad/hess 形状为 (n_samples, n_classes)，predt 形状 (n, K)。
    """
    y = dtrain.get_label().astype(np.int64)
    gamma = getattr(focal_obj_xgb, 'gamma', DEFAULT_GAMMA)
    alpha = getattr(focal_obj_xgb, 'alpha', None)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1)
    grad, hess = focal_grad_hess(predt, y, gamma=gamma, alpha=alpha)
    return grad, hess


def focal_fobj_lgb(predt, train_data):
    """LightGBM 自定义目标入口（传入 params['objective']，LightGBM 4.x 写法）。

    LightGBM 4.x 的 predt / grad / hess 均为展平的 (n*K,) 一维数组。
    """
    y = train_data.get_label().astype(np.int64)
    gamma = getattr(focal_fobj_lgb, 'gamma', DEFAULT_GAMMA)
    alpha = getattr(focal_fobj_lgb, 'alpha', None)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1)
    grad, hess = focal_grad_hess(predt, y, gamma=gamma, alpha=alpha)
    return grad.ravel(), hess.ravel()


def focal_feval_lgb(preds, train_data):
    """LightGBM feval：softmax 后计算 multi_logloss，修正自定义目标下内置指标不套 softmax 的问题。

    LightGBM 4.x 在 callable objective 下，内置 multi_logloss 直接对原始 logit 求对数值误导早停，
    故此 feval 显式做 softmax + clip 后返回正确的 multi_logloss（越低越好）。
    """
    y = train_data.get_label().astype(np.int64)
    z = np.asarray(preds, dtype=np.float64).reshape(len(y), -1)
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    p = e / e.sum(axis=1, keepdims=True)
    p = np.clip(p, 1e-9, 1.0)
    ll = float(np.mean(-np.log(p[np.arange(len(y)), y])))
    return 'multi_logloss', ll, False


def configure(gamma=DEFAULT_GAMMA, alpha=None):
    """把 gamma / alpha 绑定到两个入口函数（供训练脚本在调用前设置）。"""
    focal_obj_xgb.gamma = float(gamma)
    focal_obj_xgb.alpha = None if alpha is None else np.asarray(alpha, dtype=np.float64)
    focal_fobj_lgb.gamma = float(gamma)
    focal_fobj_lgb.alpha = None if alpha is None else np.asarray(alpha, dtype=np.float64)


def focal_gradcheck(n=16, K=3, gamma=DEFAULT_GAMMA, h=1e-5, tol=1e-4):
    """中心差分校验解析梯度与对角海森，返回最大绝对误差。

    用梯度中心差分（对解析梯度做差分）校验海森，避免二阶差分 h^2 带来的
    数值噪声。用法: python scripts/focal_loss.py
    """
    rng = np.random.default_rng(42)
    z = rng.normal(size=(n, K))
    y = rng.integers(0, K, size=n)
    alpha = np.array([1.0, 1.4, 1.0])  # 平局上调，用于校验 alpha 分支

    grad_ana, hess_ana = focal_grad_hess(z, y, gamma=gamma, alpha=alpha)
    grad_ana = grad_ana.reshape(n, K)
    hess_ana = hess_ana.reshape(n, K)

    grad_num = np.zeros_like(grad_ana)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            lp = _focal_loss_value(zp, y, gamma=gamma, alpha=alpha)
            lm = _focal_loss_value(zm, y, gamma=gamma, alpha=alpha)
            grad_num[i, k] = (lp - lm) / (2 * h)

    # 海森用「解析梯度的中心差分」校验，精度更高（O(h^2) 而非 O(h^2) 的四阶导数）
    hess_num = np.zeros_like(hess_ana)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            gp, _ = focal_grad_hess(zp, y, gamma=gamma, alpha=alpha)
            gm, _ = focal_grad_hess(zm, y, gamma=gamma, alpha=alpha)
            hess_num[i, k] = (gp.reshape(n, K)[i, k] - gm.reshape(n, K)[i, k]) / (2 * h)

    g_err = float(np.abs(grad_ana - grad_num).max())
    hess_err = float(np.abs(hess_ana - hess_num).max())

    print("\n[P2-13 focal_gradcheck] 有限差分校验（gamma=%.1f, h=%.1e）" % (gamma, h))
    print(f"  梯度最大绝对误差: {g_err:.3e}   (tol={tol})")
    print(f"  海森最大绝对误差: {hess_err:.3e}   (tol={tol})")
    print(f"  {'' if (g_err < tol and hess_err < tol) else '!! '}结论: "
          f"{'校验通过' if (g_err < tol and hess_err < tol) else '校验失败'}")

    # gamma=0 应退化为交叉熵
    if abs(gamma - 0.0) < 1e-12:
        g0, h0 = focal_grad_hess(z, y, gamma=0.0, alpha=None)
        p = _softmax(z)
        onehot = np.zeros((n, K)); onehot[np.arange(n), y] = 1.0
        ce_g = (p - onehot).ravel()
        ce_h = (p * (1.0 - p)).ravel()
        print(f"  gamma=0 退化校验: grad误差={np.abs(g0.ravel()-ce_g).max():.3e} "
              f"hess误差={np.abs(h0.ravel()-ce_h).max():.3e}")

    return g_err, hess_err


if __name__ == '__main__':
    focal_gradcheck(n=16, K=3, gamma=2.0)
    focal_gradcheck(n=16, K=3, gamma=0.0)