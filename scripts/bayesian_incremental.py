# -*- coding: utf-8 -*-
"""
bayesian_incremental.py — P1-B 阶段2：attack/defense 后验的增量更新（扩展卡尔曼滤波）
================================================================================
背景（评估报告 v2.0 §P1-B + docs/bayesian_incremental_design.md）：
  现状贝叶斯层级模型（bayesian_hierarchical_model.py）每次刷新都「全量重训」——
  对所有历史比赛从头做 MAP 点估计，无「后验携带 + 增量更新」机制。
  本模块实现设计方案的选项 B（首选）：状态空间 + Kalman 滤波，把 attack/defense
  建模为一阶随机游走隐状态，用扩展卡尔曼滤波（EKF）逐场序贯更新后验。

状态方程（随机游走，逐场转移）:
    attack_i,t = attack_i,t-1 + ε_att,  ε_att ~ N(0, τ_att²)
    defense_i,t = defense_i,t-1 + ε_def, ε_def ~ N(0, τ_def²)

观测方程（Poisson log-link，Dixon-Coles 修正仅在预测端）:
    log(λ_home) = μ + home_adv + attack_home − defense_away
    log(λ_away) = μ + attack_away − defense_home
    goals_home ~ Poisson(λ_home),  goals_away ~ Poisson(λ_away)

EKF 线性化（直接对 λ=exp(η) 求 Jacobian，R 用 Poisson 方差≈均值）:
    h(x) = [λ_home, λ_away];  H = diag(λ) @ J_η（J_η 为线性预测子对 x 的 Jacobian）
    S = H P Hᵀ + diag(λ);  K = P Hᵀ S⁻¹;  x ← x + K(y−h);  P ← (I−KH)P

关键设计约束（设计文档 §3.4）:
  - μ / home_adv / ρ 为跨队「慢变量」，由 burn-in 全量 MAP 估计后在此保持**固定**，
    仅 attack/defense 随比赛增量更新（低频重估另议）。
  - 冷启动：burn-in 未见的新队（升班马）自动以 0 + σ_init² 大方差入状态（联赛均值收缩）。
  - 严格时序：update() 仅吸收「已完赛」场次；预测必须先于当场的 update。
  - Dixon-Coles ρ 仅用于 predict_wdl 的比分边际化，不进入 EKF 线性化（独立 Poisson 近似）。

与全量重训的隔离：本类只做「给定 μ/home_adv/ρ 初值后的 attack/defense 在线更新」，
对照（全量重训 vs 增量）在 bayesian_incremental_ab.py 完成。
只读状态，不写库、不写资产。
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bayesian_hierarchical_model import _tau_dixon_coles  # noqa: E402


def poisson_wdl(lam_home: float, lam_away: float, rho: float, max_goals: int = 10) -> Dict[str, float]:
    """由 λ 边际化得 WDL 概率（含 Dixon-Coles τ 修正），返回 {'win','draw','lose'}。

    与 BayesianHierarchicalModel.predict_wdl 同口径（客/平/主 = lose/draw/win）。
    """
    win = draw = lose = 0.0
    for i in range(max_goals + 1):
        pi = math.exp(-lam_home) * (lam_home ** i) / math.factorial(i)
        for j in range(max_goals + 1):
            pj = math.exp(-lam_away) * (lam_away ** j) / math.factorial(j)
            p = pi * pj
            tau = float(_tau_dixon_coles(
                np.array([i]), np.array([j]),
                np.array([lam_home]), np.array([lam_away]), rho,
            )[0])
            p *= tau
            if i > j:
                win += p
            elif i == j:
                draw += p
            else:
                lose += p
    total = win + draw + lose
    return {"win": win / total, "draw": draw / total, "lose": lose / total}


class BayesianIncrementalFilter:
    """attack/defense 后验的在线增量更新（扩展卡尔曼滤波）。

    状态布局: x = [attack(T), defense(T)]，team_i 的 attack 在索引 i、defense 在索引 T+i。
    协方差 P 为完整 (2T)×(2T) 矩阵（保留 attack/defense 之间的 off-diagonal 相关，
    相较 Laplace 对角近似更完整）。
    """

    def __init__(
        self,
        mu: float,
        home_adv: float,
        rho: float,
        tau_att: float = 0.03,
        tau_def: float = 0.03,
        sigma_init: float = 0.35,
        max_goals: int = 10,
        league: Optional[str] = None,
    ):
        self.mu = float(mu)
        self.home_adv = float(home_adv)
        self.rho = float(rho)
        self.tau_att = float(tau_att)
        self.tau_def = float(tau_def)
        self.sigma_init = float(sigma_init)
        self.max_goals = int(max_goals)

        self.team_index: Dict[str, int] = {}
        self._x = np.zeros(0)   # [attack(T), defense(T)]
        self._P = np.zeros((0, 0))
        self.league: Optional[str] = league
        self._last_absorbed_date: Optional[str] = None
        self._last_match_id: Optional[str] = None

    # ---------------- 状态初始化 / 冷启动 ----------------

    def init_teams(
        self,
        teams: List[str],
        attack: Optional[np.ndarray] = None,
        defense: Optional[np.ndarray] = None,
        attack_std: Optional[np.ndarray] = None,
        defense_std: Optional[np.ndarray] = None,
    ) -> None:
        """用 burn-in 全量 MAP 的攻防点估计 + Laplace std 初始化状态与协方差。"""
        self.team_index = {t: i for i, t in enumerate(teams)}
        T = len(teams)
        att = np.asarray(attack, dtype=float) if attack is not None else np.zeros(T)
        dfn = np.asarray(defense, dtype=float) if defense is not None else np.zeros(T)
        att_std = np.asarray(attack_std, dtype=float) if attack_std is not None else np.full(T, self.sigma_init)
        def_std = np.asarray(defense_std, dtype=float) if defense_std is not None else np.full(T, self.sigma_init)
        self._x = np.concatenate([att, dfn])
        var = np.concatenate([att_std ** 2, def_std ** 2])
        self._P = np.diag(var)

    def _ensure_team(self, team: str) -> None:
        """冷启动：burn-in 未见的新队加入状态（0 均值 + σ_init² 大方差，联赛均值收缩）。"""
        if team in self.team_index:
            return
        T = len(self.team_index)
        self.team_index[team] = T  # 新队索引 = T

        # 状态: [att(0..T-1), def(0..T-1)] -> [att(0..T), def(0..T)]
        att_old = self._x[:T]
        def_old = self._x[T:]
        self._x = np.concatenate([att_old, [0.0], def_old, [0.0]])

        # 协方差重映射：旧 att i→i；旧 def i(旧索引 T+i) → 新索引 (T+1)+i
        n_new = 2 * (T + 1)
        P = np.zeros((n_new, n_new))
        P[:T, :T] = self._P[:T, :T]
        P[:T, T + 1:T + 1 + T] = self._P[:T, T:]
        P[T + 1:T + 1 + T, :T] = self._P[T:, :T]
        P[T + 1:T + 1 + T, T + 1:T + 1 + T] = self._P[T:, T:]
        P[T, T] = self.sigma_init ** 2          # 新 attack
        P[2 * T + 1, 2 * T + 1] = self.sigma_init ** 2  # 新 defense
        self._P = P

    def _eta_and_lambda(self, home: str, away: str) -> Tuple[float, float, float, float]:
        self._ensure_team(home)
        self._ensure_team(away)
        T = len(self.team_index)
        hi = self.team_index[home]
        ai = self.team_index[away]
        att_h = self._x[hi]
        def_a = self._x[T + ai]
        att_a = self._x[ai]
        def_h = self._x[T + hi]
        eta_h = self.mu + self.home_adv + att_h - def_a
        eta_a = self.mu + att_a - def_h
        return eta_h, eta_a, math.exp(eta_h), math.exp(eta_a)

    # ---------------- 预测 ----------------

    def predict_lambda(self, home: str, away: str) -> Tuple[float, float]:
        _, _, lam_h, lam_a = self._eta_and_lambda(home, away)
        return lam_h, lam_a

    def predict_wdl(self, home: str, away: str) -> Dict[str, float]:
        lam_h, lam_a = self.predict_lambda(home, away)
        return poisson_wdl(lam_h, lam_a, self.rho, self.max_goals)

    # ---------------- EKF 增量更新 ----------------

    def update(self, home: str, away: str, goals_home: int, goals_away: int,
               date: Optional[str] = None, match_id: Optional[str] = None) -> None:
        """吸收一场已完赛比赛，序贯更新 attack/defense 后验。

        时序要求：对该场的预测必须在调用本方法**之前**通过 predict_wdl 完成
        （严格避免用本场赛果污染预测其特征）。本方法不返回预测值。
        """
        self._ensure_team(home)
        self._ensure_team(away)
        T = len(self.team_index)
        hi = self.team_index[home]
        ai = self.team_index[away]

        # 1) 预测步（随机游走转移：均值不变，协方差 + Q）
        Q = np.zeros((2 * T, 2 * T))
        Q[:T, :T] = np.eye(T) * (self.tau_att ** 2)
        Q[T:, T:] = np.eye(T) * (self.tau_def ** 2)
        P_pred = self._P + Q

        # 2) 线性预测子 η 与 λ
        eta_h = self.mu + self.home_adv + self._x[hi] - self._x[T + ai]
        eta_a = self.mu + self._x[ai] - self._x[T + hi]
        lam_h = max(math.exp(eta_h), 1e-3)
        lam_a = max(math.exp(eta_a), 1e-3)

        # 3) 观测 Jacobian：J_η (2×2T)，H = diag(λ) @ J_η
        J = np.zeros((2, 2 * T))
        J[0, hi] = 1.0
        J[0, T + ai] = -1.0
        J[1, ai] = 1.0
        J[1, T + hi] = -1.0
        H = np.diag([lam_h, lam_a]) @ J

        # 4) 观测：y=[gh, ga]，h=[λh, λa]，R≈diag(λ)（Poisson 方差≈均值）
        y = np.array([float(goals_home), float(goals_away)])
        z = np.array([lam_h, lam_a])
        R = np.diag([lam_h, lam_a])

        # 5) 增益与更新
        S = H @ P_pred @ H.T + R
        K = P_pred @ H.T @ np.linalg.inv(S)
        innov = y - z
        self._x = self._x + K @ innov
        P_upd = (np.eye(2 * T) - K @ H) @ P_pred
        self._P = 0.5 * (P_upd + P_upd.T)  # 数值对称化
        if date is not None:
            self._last_absorbed_date = str(date)
        if match_id is not None:
            self._last_match_id = str(match_id)

    @property
    def attack(self) -> np.ndarray:
        T = len(self.team_index)
        return self._x[:T]

    @property
    def defense(self) -> np.ndarray:
        T = len(self.team_index)
        return self._x[T:]

    # ---------------- 状态序列化（shadow 持久化） ----------------

    def state_dict(self) -> Dict:
        """导出完整状态（含协方差 P 与进度游标），供 shadow 滚动跨进程恢复。"""
        return {
            "schema_version": 1,
            "league": self.league,
            "mu": self.mu,
            "home_adv": self.home_adv,
            "rho": self.rho,
            "tau_att": self.tau_att,
            "tau_def": self.tau_def,
            "sigma_init": self.sigma_init,
            "max_goals": self.max_goals,
            "team_index": self.team_index,
            "x": self._x.tolist(),
            "P": self._P.tolist(),
            "cursor": {
                "last_absorbed_date": self._last_absorbed_date,
                "last_match_id": self._last_match_id,
            },
        }

    @classmethod
    def from_state_dict(cls, d: Dict) -> "BayesianIncrementalFilter":
        """从 state_dict 恢复（含协方差 P 与进度游标）。"""
        flt = cls(
            mu=d["mu"],
            home_adv=d["home_adv"],
            rho=d["rho"],
            tau_att=d.get("tau_att", 0.03),
            tau_def=d.get("tau_def", 0.03),
            sigma_init=d.get("sigma_init", 0.35),
            max_goals=int(d.get("max_goals", 10)),
            league=d.get("league"),
        )
        flt.team_index = dict(d["team_index"])
        flt._x = np.asarray(d["x"], dtype=float)
        flt._P = np.asarray(d["P"], dtype=float)
        cur = d.get("cursor") or {}
        flt._last_absorbed_date = cur.get("last_absorbed_date")
        flt._last_match_id = cur.get("last_match_id")
        return flt

    def save(self, path: str) -> str:
        """序列化到 JSON 文件（目录不存在时自动创建）。"""
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.state_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path: str) -> "BayesianIncrementalFilter":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_state_dict(json.load(f))