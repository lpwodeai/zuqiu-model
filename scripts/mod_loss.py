# -*- coding: utf-8 -*-
"""
C-20260904-004: 训练端系统性高估根治 — 市场赔率蒸馏（Market-Odds Distillation, MOD）损失
========================================================================================
背景（C-20260904-003 EV-policy Loss 三档 λ 扫描证伪后）：
  朴素「对 EV 直接求导」（L=CE+λ·(1−p_y·o_y)）因 `dEVL/dp_y=−o_y` 对高赔率真结果放大
  梯度，恰加剧「高赔率方向系统性过度自信」根因（>10pp 桶反更差），且真类海森可负导致
  LGB 数值退化。本模块换向：**把模型概率向市场赔率隐含概率（去抽水 + 可选温度软化）
  蒸馏**——让 p_model → p_market，对抗而非奖励高赔率过度自信。

损失（单样本，softmax 概率 p，三向市场赔率 o，去抽水隐含概率 q）:
    q'_k = 1/o_k                     （隐含概率）
    q_k  = q'_k / Σ_j q'_j           （比例去抽水，Σq=1）
    q_soft,k = q_k^γ / Σ_j q_j^γ     （γ∈(0,1] 温度软化；γ=1 为纯去抽水，γ↓ 更扁平）
    KL(q_soft || p) = Σ_k q_soft,k · log(q_soft,k / p_k)
    L   = CE(y,p) + λ·KL(q_soft || p) = −log(p_y) + λ·Σ_k q_soft,k·log(q_soft,k/p_k)

梯度/海森（对原始 logit z_k；Σq=1 时 KL 对 p 的梯度 = p−q，与 CE 同构）:
    grad_k = (p_k − δ_{k,y}) + λ·(p_k − q_soft,k)
    hess_k = p_k·(1−p_k) · (1+λ)      （恒正！无 EV-loss 的负海森退化问题）
    λ=0 严格退化为 softmax 交叉熵（自检锚点）。

与 EV-policy Loss 的本质区别：
  EV 分量奖励「高赔率真结果」（梯度 ∝ o_y）→ 加剧过度自信；
  MOD 分量惩罚「偏离市场隐含概率」（梯度 ∝ (p−q_soft)）→ 把容量拉回市场锚点，
  高赔率方向的 p 被 q_soft(低) 下拉，系统性高估从训练端被对抗。

约定（与生产一致）:
    y 编码: 0=客胜, 1=平局, 2=主胜
    odds 列序 = [客胜, 平局, 主胜]；NaN 行视为无赔率 → 仅 CE
    XGBoost predt 形状 (n, K)；LightGBM predt 形状 (n*K,) 扁平
    样本权重（class_weights/异常加权）由自定义目标内部对 get_weight() 施加

验证: `mod_gradcheck()` 用中心差分对比解析梯度/海森；λ=0 与交叉熵一致。
"""

import numpy as np

DEFAULT_LAMBDA_MOD = 1.0
DEFAULT_GAMMA = 1.0
EPS = 1e-7

# 模块级赔率状态（configure 绑定，与训练折行序 1:1 对齐，列序 [客胜,平局,主胜]）
_ODDS = None  # (n, 3) float；无赔率样本为 NaN


def _softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def _market_implied_probs(odds, gamma=DEFAULT_GAMMA):
    """从三向赔率构建去抽水 + 可选软化的软目标 q_soft，(n,K) 列序同 odds。

    q'_k = 1/o_k → q_k = q'_k/Σq'（比例去抽水）→ q_soft,k = q_k^γ/Σq_j^γ。
    无赔率行（任一 o≤1 或 NaN）返回 NaN 行（训练时退化为纯 CE）。
    """
    odds = np.asarray(odds, dtype=np.float64)
    n, K = odds.shape
    q = np.full_like(odds, np.nan)
    valid = np.all(np.isfinite(odds) & (odds > 1.0), axis=1)
    if valid.any():
        inv = 1.0 / odds[valid]
        qv = inv / inv.sum(axis=1, keepdims=True)
        if gamma != 1.0:
            qv = qv ** gamma
            qv = qv / qv.sum(axis=1, keepdims=True)
        q[valid] = qv
    q = np.clip(q, EPS, 1.0 - EPS)
    return q


def _mod_loss_value(z, y, odds, lambda_mod=DEFAULT_LAMBDA_MOD, gamma=DEFAULT_GAMMA):
    """总损失（用于有限差分校验，与逐样本梯度保持同一缩放）。"""
    p = _softmax(z)
    n = p.shape[0]
    p = np.clip(p, EPS, 1.0 - EPS)
    ce = -np.log(p[np.arange(n), y])
    q = _market_implied_probs(odds, gamma)
    has = ~np.isnan(q[:, 0])
    loss = ce.copy()
    if has.any():
        kl = (q[has] * (np.log(q[has]) - np.log(p[has]))).sum(axis=1)
        loss[has] = ce[has] + lambda_mod * kl
    return float(loss.sum())


def mod_grad_hess(z, y, odds, lambda_mod=DEFAULT_LAMBDA_MOD,
                  gamma=DEFAULT_GAMMA, hess_floor=1e-6):
    """计算 MOD Loss 的梯度与对角海森（对原始 logit z）。

    参数:
        z: (n, K) 原始 logit（未过 softmax），K=3，列序 [客胜,平局,主胜]
        y: (n,)   真实标签 [0,1,2]
        odds: (n, K) 三向市场赔率（列序同 z）；NaN 行视为无赔率 → 仅 CE
        lambda_mod: 蒸馏分量权重（CE 权重恒为 1.0）
        gamma: 软目标温度软化指数（1.0=纯去抽水，<1 更扁平）
        hess_floor: 海森下限保护（p 近 0/1 时 p(1-p) 近零，防分裂增益失效）

    返回:
        (grad, hess): 均为 (n, K) 数组
    """
    z = np.asarray(z, dtype=np.float64)
    y = np.asarray(y, dtype=np.int64)
    odds = np.asarray(odds, dtype=np.float64)
    n, K = z.shape

    p = _softmax(z)
    p = np.clip(p, EPS, 1.0 - EPS)

    q = _market_implied_probs(odds, gamma)
    has = ~np.isnan(q[:, 0])

    # --- 梯度 ---
    onehot = np.zeros((n, K), dtype=np.float64)
    onehot[np.arange(n), y] = 1.0
    grad = p - onehot                        # CE 梯度
    if has.any():
        grad[has] += lambda_mod * (p[has] - q[has])   # KL 梯度 p−q_soft

    # --- 对角海森（恒正：p(1-p)·(1+λ)）---
    hess = p * (1.0 - p)
    hess = np.where(has[:, None], hess * (1.0 + lambda_mod), hess)
    hess = np.maximum(hess, hess_floor)

    return grad, hess


def mod_obj_xgb(predt, dtrain):
    """XGBoost 自定义目标入口（xgb.train(obj=...)）。

    XGBoost 2.1+ 要求 grad/hess 形状为 (n_samples, n_classes)，predt 形状 (n, K)。
    样本权重（class_weights 等）在此显式施加（自定义目标不会自动乘）。
    """
    y = dtrain.get_label().astype(np.int64)
    lambda_mod = getattr(mod_obj_xgb, 'lambda_mod', DEFAULT_LAMBDA_MOD)
    gamma = getattr(mod_obj_xgb, 'gamma', DEFAULT_GAMMA)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1)
    grad, hess = mod_grad_hess(predt, y, _ODDS, lambda_mod=lambda_mod, gamma=gamma)
    if _ODDS is not None and _ODDS.shape[0] == len(y):
        w = dtrain.get_weight()
        if w is not None and w.size:
            grad = grad * w[:, None]
            hess = hess * w[:, None]
    return grad, hess


def mod_fobj_lgb(predt, train_data):
    """LightGBM 自定义目标入口（传入 params['objective']，LightGBM 4.x 写法）。

    LightGBM 4.x 的 predt / grad / hess 均为展平的 (n*K,) 一维数组，且为
    **class-major 布局**（basic.py 用 `order='F'` reshape 还原）：
    predt[c*n + i] = 样本 i 类 c 的 logit。必须用 order='F' reshape/ravel，
    否则梯度布局错乱导致模型学不动（"No further splits"、概率扁平化）。
    """
    y = train_data.get_label().astype(np.int64)
    lambda_mod = getattr(mod_fobj_lgb, 'lambda_mod', DEFAULT_LAMBDA_MOD)
    gamma = getattr(mod_fobj_lgb, 'gamma', DEFAULT_GAMMA)
    predt = np.asarray(predt, dtype=np.float64).reshape(len(y), -1, order='F')
    grad, hess = mod_grad_hess(predt, y, _ODDS, lambda_mod=lambda_mod, gamma=gamma)
    if _ODDS is not None and _ODDS.shape[0] == len(y):
        w = train_data.get_weight()
        if w is not None and w.size:
            grad = grad * w[:, None]
            hess = hess * w[:, None]
    return grad.ravel(order='F'), hess.ravel(order='F')


def mod_feval_lgb(preds, train_data):
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


def configure(odds, lambda_mod=DEFAULT_LAMBDA_MOD, gamma=DEFAULT_GAMMA):
    """把赔率矩阵 / λ / γ 绑定到两个入口函数（供训练脚本在调用前设置）。

    odds: (n, 3) 三向赔率，列序 [客胜, 平局, 主胜]，与训练折行序 1:1 对齐。
    """
    global _ODDS
    _ODDS = np.asarray(odds, dtype=np.float64).reshape(-1, 3)
    mod_obj_xgb.lambda_mod = float(lambda_mod)
    mod_obj_xgb.gamma = float(gamma)
    mod_fobj_lgb.lambda_mod = float(lambda_mod)
    mod_fobj_lgb.gamma = float(gamma)


def mod_gradcheck(n=16, K=3, lambda_mod=DEFAULT_LAMBDA_MOD, gamma=DEFAULT_GAMMA,
                  h=1e-5, tol=1e-4):
    """中心差分校验解析梯度与对角海森，返回最大绝对误差。"""
    rng = np.random.default_rng(42)
    z = rng.normal(size=(n, K))
    y = rng.integers(0, K, size=n)
    odds = np.round(rng.uniform(1.5, 8.0, size=(n, K)), 2)
    # 注入若干无赔率行验证 NaN 退化分支
    odds[0, :] = np.nan
    odds[1, :] = 1.0

    grad_ana, _ = mod_grad_hess(z, y, odds, lambda_mod=lambda_mod, gamma=gamma)
    _, hess_raw = mod_grad_hess(z, y, odds, lambda_mod=lambda_mod, gamma=gamma, hess_floor=0.0)

    grad_num = np.zeros_like(grad_ana)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            lp = _mod_loss_value(zp, y, odds, lambda_mod=lambda_mod, gamma=gamma)
            lm = _mod_loss_value(zm, y, odds, lambda_mod=lambda_mod, gamma=gamma)
            grad_num[i, k] = (lp - lm) / (2 * h)

    hess_num = np.zeros_like(hess_raw)
    for i in range(n):
        for k in range(K):
            zp = z.copy(); zp[i, k] += h
            zm = z.copy(); zm[i, k] -= h
            gp, _ = mod_grad_hess(zp, y, odds, lambda_mod=lambda_mod, gamma=gamma, hess_floor=0.0)
            gm, _ = mod_grad_hess(zm, y, odds, lambda_mod=lambda_mod, gamma=gamma, hess_floor=0.0)
            hess_num[i, k] = (gp[i, k] - gm[i, k]) / (2 * h)

    g_err = float(np.abs(grad_ana - grad_num).max())
    hess_err = float(np.abs(hess_raw - hess_num).max())

    print("\n[MOD-Loss mod_gradcheck] 有限差分校验（lambda=%.1f, gamma=%.1f, h=%.1e）"
          % (lambda_mod, gamma, h))
    print(f"  梯度最大绝对误差: {g_err:.3e}   (tol={tol})")
    print(f"  海森最大绝对误差: {hess_err:.3e}   (tol={tol})")
    print(f"  {'' if (g_err < tol and hess_err < tol) else '!! '}结论: "
          f"{'校验通过' if (g_err < tol and hess_err < tol) else '校验失败'}")

    # λ=0 应退化为交叉熵
    g0, h0 = mod_grad_hess(z, y, odds, lambda_mod=0.0, gamma=gamma)
    p = _softmax(z)
    onehot = np.zeros((n, K)); onehot[np.arange(n), y] = 1.0
    ce_g = p - onehot
    ce_h = p * (1.0 - p)
    print(f"  lambda=0 退化校验: grad误差={np.abs(g0 - ce_g).max():.3e} "
          f"hess误差={np.abs(h0 - ce_h).max():.3e}")

    return g_err, hess_err


if __name__ == '__main__':
    mod_gradcheck(n=16, K=3, lambda_mod=1.0, gamma=1.0)
    mod_gradcheck(n=16, K=3, lambda_mod=0.3, gamma=0.5)
    mod_gradcheck(n=16, K=3, lambda_mod=0.0, gamma=1.0)
