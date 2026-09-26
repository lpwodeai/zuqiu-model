# -*- coding: utf-8 -*-
"""C-20260921-038: TG 赔率 Shin 去水模块。

替代生产 TotalGoalsPredictor 中的简单 1/赔率归一化（multiplicative method），
Shin (1993) 法修正 favorite-longshot bias：博彩市场系统性高估冷门（高赔率低概率项）、
低估热门（低赔率高概率项），Shin 模型通过 insider rate z 参数校正该偏差。

数学推导：
    设 n 个档位，博彩隐含概率 q̃_i = (1/o_i) / Σ(1/o_j)
    Shin 模型：q̃_i = (1-z)·p_i + z/n  （z = insider rate）
    反解：p_i = (q̃_i - z/n) / (1-z)
    z 闭式解：z = (n·Σq̃² - 1) / (n-1)

    z=0 时退化为简单归一化（p_i = q̃_i）；z>0 时热门概率下调、冷门上调。
    z<0 时 clamp 到 0（无 Shin 校正）。

用法：
    from tg_shin_devig import shin_devig, simple_devig
    probs, z = shin_devig({'0': 12.0, '1': 4.5, '2': 3.2, ...})
"""

from __future__ import annotations
import numpy as np


def simple_devig(odds_dict: dict) -> dict:
    """简单 1/赔率归一化（multiplicative method，当前生产口径）。

    Args:
        odds_dict: {key: decimal_odds}，如 {'0': 12.0, '1': 4.5, '7+': 26.0}

    Returns:
        {key: probability}，和为 1.0
    """
    inv = {k: 1.0 / max(float(v), 1e-10) for k, v in odds_dict.items()}
    total = sum(inv.values())
    if total <= 0:
        n = len(odds_dict)
        return {k: 1.0 / n for k in odds_dict}
    return {k: v / total for k, v in inv.items()}


def shin_devig(odds_dict: dict) -> tuple[dict, float]:
    """Shin (1993) 去水法。

    Args:
        odds_dict: {key: decimal_odds}，如 {'0': 12.0, '1': 4.5, ..., '7+': 26.0}

    Returns:
        (probs_dict, z): probs_dict={key: probability}，z=Shin insider rate
    """
    n = len(odds_dict)
    if n < 2:
        return {k: 1.0 for k in odds_dict}, 0.0

    # 1. 归一化隐含概率
    inv = np.array([1.0 / max(float(v), 1e-10) for v in odds_dict.values()])
    pi = inv.sum()
    if pi <= 0:
        return {k: 1.0 / n for k in odds_dict}, 0.0
    q_tilde = inv / pi  # 归一化后隐含概率

    # 2. Shin z 闭式解
    sum_q2 = np.sum(q_tilde ** 2)
    z = (n * sum_q2 - 1.0) / (n - 1)

    # 3. clamp z 到 [0, n*min(q)-eps] 保证 p_i >= 0
    z = max(0.0, z)
    z_max = n * q_tilde.min() - 1e-8
    z = min(z, z_max)

    # 4. 反解真实概率
    if z < 1e-10:
        # z≈0 退化为简单归一化
        probs = q_tilde.copy()
    else:
        probs = (q_tilde - z / n) / (1.0 - z)
        # 数值安全归一化
        s = probs.sum()
        if s > 0:
            probs = probs / s

    keys = list(odds_dict.keys())
    return {keys[i]: float(probs[i]) for i in range(n)}, float(z)


def compute_vig(odds_dict: dict) -> float:
    """计算 vig（抽水率）= Σ(1/o) - 1。

    Returns:
        vig: 越高说明市场抽水越重
    """
    inv_sum = sum(1.0 / max(float(v), 1e-10) for v in odds_dict.values())
    return inv_sum - 1.0
