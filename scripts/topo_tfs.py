# -*- coding: utf-8 -*-
"""topo_tfs.py — 拓扑保真度（TFS）评估核心模块（D-20261006-052 轨 A）

用途：衡量"预测比分概率矩阵"与"市场隐含比分矩阵"的结构相似性，
作为 RPS/LogLoss（逐点误差）之外的结构维度评估门禁。

方法（纯 numpy/scipy 实现，不依赖 gudhi）：
  - 把 6x6 比分网格视为 2D 标量场；
  - superlevel H0 持久图：连通峰（众数）的出生/死亡（并查集 + elder rule）；
  - sublevel  H0 持久图：谷（=超水平的洞，Alexander 对偶近似）；
  - 两组持久图合并后与对方算 Wasserstein-1 距离（匈牙利匹配，含对角投影）；
  - TFS = 1 / (1 + W)，∈(0,1]，1 = 结构完全一致。

辅助结构量（业务可读）：
  - n_modes        : persistence > MODE_PERSIST_THRESH 的峰个数（"概率质量分成几团"）
  - top4_mass      : top-4 比分格子质量占比（集中度）
  - diagonal_mass  : 平局带（h==a）质量占比
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment

MODE_PERSIST_THRESH = 0.02   # 峰持久度阈值（概率质量单位）
INF_DEATH = 0.0              # 最老峰的死亡值（网格值域下界）


# ---------------------------------------------------------------------------
# 并查集
# ---------------------------------------------------------------------------
class _DSU:
    def __init__(self, n):
        self.parent = list(range(n))
        self.birth = [None] * n   # 各分量（根）的出生值

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b, value, bars):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        # elder rule：出生值更大的分量更"老"
        if self.birth[ra] < self.birth[rb]:
            ra, rb = rb, ra
        # rb 分量在 value 处死亡
        bars.append((self.birth[rb], value))
        self.parent[rb] = ra
        return ra


def _level_h0_diagram(grid: np.ndarray, superlevel: bool = True) -> list:
    """2D 网格的 superlevel（或 sublevel）H0 持久图。

    superlevel: 按值降序加格子，新生分量=局部峰，合并=峰死亡。
    sublevel  : 等价于对 -grid 做 superlevel。
    返回 [(birth, death), ...]，值域为概率质量（grid 已归一化）。
    """
    g = np.asarray(grid, float)
    if not superlevel:
        g = -g
    h, w = g.shape
    flat = g.ravel()
    order = np.argsort(-flat)          # 降序
    dsu = _DSU(flat.size)
    active = np.zeros(flat.size, bool)
    bars = []
    # 4-邻接
    nbr = lambda i: [i - 1 if i % w else -1,
                     i + 1 if (i + 1) % w else -1,
                     i - w if i >= w else -1,
                     i + w if i < flat.size - w else -1]
    for i in order:
        active[i] = True
        dsu.birth[i] = flat[i]
        root = i
        for j in nbr(i):
            if j >= 0 and active[j]:
                root = dsu.union(root, j, flat[i], bars)
    # 收尾：仍存活的分量死亡于值域下界
    lo = flat.min()
    for i in range(flat.size):
        if active[i] and dsu.find(i) == i and dsu.birth[i] is not None:
            bars.append((dsu.birth[i], lo))
    # 规范化 birth>=death
    return [(max(b, d), min(b, d)) for b, d in bars if b > d]


def grid_diagrams(grid: np.ndarray) -> list:
    """superlevel + sublevel 合并持久图。"""
    return _level_h0_diagram(grid, True) + _level_h0_diagram(grid, False)


def wasserstein1(dgm1: list, dgm2: list) -> float:
    """两个 1D 持久图（birth,death 列表）的 Wasserstein-1 距离（L∞ 成本 + 对角投影）。"""
    if not dgm1 and not dgm2:
        return 0.0
    P = np.array(dgm1) if dgm1 else np.zeros((0, 2))
    Q = np.array(dgm2) if dgm2 else np.zeros((0, 2))
    n, m = len(P), len(Q)
    # 对角投影成本：L∞ 意义下 = persistence/2
    def diag_cost(x):
        return (x[0] - x[1]) / 2.0
    C = np.zeros((n + m, n + m))
    for i in range(n):
        for j in range(m):
            C[i, j] = max(abs(P[i, 0] - Q[j, 0]), abs(P[i, 1] - Q[j, 1]))
    # P 点 → 任意 Q 对角槽：成本=自身对角投影成本（整块填充，否则出现 0 成本逃配）
    for i in range(n):
        C[i, m:] = diag_cost(P[i])
    for j in range(m):
        C[n:, j] = diag_cost(Q[j])
    # 对角-对角零成本
    r, c = linear_sum_assignment(C)
    return float(C[r, c].sum())


def compute_tfs(grid_model: np.ndarray, grid_ref: np.ndarray) -> dict:
    """核心入口：模型网格 vs 参考（市场）网格 → TFS 与结构量。

    输入 grid 不要求已归一化（内部归一）。
    返回 {tfs, wasserstein, n_modes_model, n_modes_ref,
          top4_mass_model, top4_mass_ref, diag_mass_model, diag_mass_ref}
    """
    gm = np.asarray(grid_model, float)
    gr = np.asarray(grid_ref, float)
    gm = np.clip(gm, 0, None); gm = gm / gm.sum()
    gr = np.clip(gr, 0, None); gr = gr / gr.sum()
    W = wasserstein1(grid_diagrams(gm), grid_diagrams(gr))

    def stats(g):
        sup = _level_h0_diagram(g, True)
        n_modes = sum(1 for b, d in sup if b - d > MODE_PERSIST_THRESH)
        srt = np.sort(g.ravel())[::-1]
        top4 = float(srt[:4].sum())
        diag = float(np.trace(g))
        return n_modes, top4, diag

    nm_m, t4_m, dg_m = stats(gm)
    nm_r, t4_r, dg_r = stats(gr)
    return {
        'tfs': 1.0 / (1.0 + W),
        'wasserstein': W,
        'n_modes_model': nm_m, 'n_modes_ref': nm_r,
        'top4_mass_model': t4_m, 'top4_mass_ref': t4_r,
        'diag_mass_model': dg_m, 'diag_mass_ref': dg_r,
    }
