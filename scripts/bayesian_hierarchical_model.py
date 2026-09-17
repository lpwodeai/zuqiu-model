# -*- coding: utf-8 -*-
"""
贝叶斯层级足球预测模型（P1-5）
==============================

实现 Baio & Blangiardo (2010) 风格贝叶斯层级模型 + Dixon-Coles 修正。

核心公式:
    log(λ_home) = μ + home_adv + attack_home - def_away
    log(λ_away) = μ + attack_away - def_home

    attack_i ~ N(0, σ_attack²)     # 层级先验：向联赛均值收缩（升班马自动收缩到 0=联赛平均）
    def_i    ~ N(0, σ_def²)
    ρ        ~ N(0, σ_ρ²)          # Dixon-Coles 低比分修正
    home_adv ~ N(home_prior, σ_home²)

特点:
    - MAP 优化（scipy L-BFGS-B），等价于后验众数估计
    - Dixon-Coles τ 修正（原始论文形式，与 prediction_core 一致）
    - 时间衰减权重（近似赛季内随机游走，近期比赛权重更高）
    - 升班马/新球队 attack/def 初始化为 0（=联赛均值），层级先验自动收缩
    - Laplace 对角近似提供 attack/def 不确定性（attack_std / def_std）
    - 分联赛训练，输出 bayesian_model_{league}.json

设计原则:
    - 严格只用赛果（进球数），不依赖赔率/特征，作为统计模型进入 Stacking 基础模型集
    - 与 prediction_core.stack_wdl_probabilities 的 {'win','draw','lose'} 输出格式兼容，
      便于 P1-7 集成
    - 时间序列切分（按日期 80/20），无随机 shuffle，防数据泄露
"""

from __future__ import annotations

import json
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from scipy.optimize import minimize
    from scipy.special import gammaln
except Exception:  # pragma: no cover
    minimize = None
    gammaln = None

# 自动定位项目目录，避免硬编码盘符
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
_ASSETS_DIR = os.path.join(_PROJECT_DIR, "assets")

# 五大联赛（与 feature_utils.load_match_data_odds 归一化后一致）
LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]

# 绝对最小值，防止 log(0) / λ 为 0
_EPS = 1e-9


def _log_poisson(k: np.ndarray, lam: np.ndarray) -> np.ndarray:
    """向量化 Poisson log-pmf: log(e^-λ λ^k / k!) = -λ + k log λ - log(k!)"""
    lam = np.clip(lam, _EPS, None)
    return -lam + k * np.log(lam) - gammaln(k + 1.0)


def _tau_dixon_coles(home_goals, away_goals, lam_home, lam_away, rho):
    """Dixon-Coles τ 修正（原始论文形式，逐元素向量化）。

    与 prediction_core.CalcEngine.dixon_coles_correction 一致:
        (0,0) -> 1 - λ_h·λ_a·ρ
        (0,1) -> 1 + λ_h·ρ
        (1,0) -> 1 + λ_a·ρ
        (1,1) -> 1 - ρ
        其它  -> 1
    """
    tau = np.ones_like(lam_home, dtype=float)
    m00 = (home_goals == 0) & (away_goals == 0)
    m01 = (home_goals == 0) & (away_goals == 1)
    m10 = (home_goals == 1) & (away_goals == 0)
    m11 = (home_goals == 1) & (away_goals == 1)
    tau[m00] = 1.0 - lam_home[m00] * lam_away[m00] * rho
    tau[m01] = 1.0 + lam_home[m01] * rho
    tau[m10] = 1.0 + lam_away[m10] * rho
    tau[m11] = 1.0 - rho
    return np.clip(tau, 0.1, 3.0)


class BayesianHierarchicalModel:
    """Baio & Blangiardo 风格贝叶斯层级足球模型（MAP 拟合）。"""

    def __init__(
        self,
        sigma_att: float = 0.35,
        sigma_def: float = 0.35,
        sigma_rho: float = 0.20,
        sigma_home: float = 0.50,
        home_prior: float = 0.28,
        mu_prior_sigma: float = 1.0,
        time_decay_half_life: Optional[float] = 300.0,
        min_weight: float = 0.15,
    ):
        self.sigma_att = sigma_att
        self.sigma_def = sigma_def
        self.sigma_rho = sigma_rho
        self.sigma_home = sigma_home
        self.home_prior = home_prior
        self.mu_prior_sigma = mu_prior_sigma
        self.time_decay_half_life = time_decay_half_life
        self.min_weight = min_weight

        # 拟合后填充
        self.league: Optional[str] = None
        self.teams: List[str] = []
        self.mu: float = 0.0
        self.home_adv: float = 0.0
        self.rho: float = 0.0
        self.attack: np.ndarray = np.array([])
        self.defense: np.ndarray = np.array([])
        self.attack_std: np.ndarray = np.array([])
        self.defense_std: np.ndarray = np.array([])
        self.num_matches: int = 0
        self.date_min: Optional[str] = None
        self.date_max: Optional[str] = None

    # ---------------- 数据准备 ----------------

    @staticmethod
    def _prepare(df) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str], np.ndarray]:
        """将 (home_team, away_team, home_goals, away_goals, date) 转为索引矩阵。

        返回: (home_idx, away_idx, home_goals, away_goals, teams, weights)
        """
        df = df.copy()
        teams = sorted(set(df["home_team_name"]).union(set(df["away_team_name"])))
        team_index = {t: i for i, t in enumerate(teams)}

        home_idx = df["home_team_name"].map(team_index).to_numpy(dtype=int)
        away_idx = df["away_team_name"].map(team_index).to_numpy(dtype=int)
        home_goals = df["homeGoals"].astype(int).to_numpy()
        away_goals = df["awayGoals"].astype(int).to_numpy()

        return home_idx, away_idx, home_goals, away_goals, teams

    @staticmethod
    def _time_weights(dates: np.ndarray, half_life: Optional[float], min_weight: float) -> np.ndarray:
        """指数时间衰减权重（近似赛季内随机游走）。"""
        n = len(dates)
        if half_life is None or n == 0:
            return np.ones(n)
        dts = pd.to_datetime(dates)
        ref = dts.max()
        days = np.array([(ref - d).days for d in dts], dtype=float)
        weights = np.power(2.0, -days / half_life)
        weights = np.clip(weights, min_weight, 1.0)
        weights = weights / weights.mean()  # 归一化，保持似然尺度稳定
        return weights

    # ---------------- 目标函数 ----------------

    def _build_linear_predictor(self, params):
        mu, home_adv, rho = params[0], params[1], params[2]
        T = len(self.teams)
        att = params[3:3 + T]
        dfn = params[3 + T:3 + 2 * T]
        lam_home = np.exp(mu + home_adv + att[self._home_idx] - dfn[self._away_idx])
        lam_away = np.exp(mu + att[self._away_idx] - dfn[self._home_idx])
        return mu, home_adv, rho, att, dfn, lam_home, lam_away

    def _neg_log_posterior(self, params) -> float:
        mu, home_adv, rho, att, dfn, lam_home, lam_away = self._build_linear_predictor(params)

        # 对数似然（含时间衰减权重 + Dixon-Coles τ 修正）
        ll = (
            _log_poisson(self._home_goals, lam_home)
            + _log_poisson(self._away_goals, lam_away)
            + np.log(_tau_dixon_coles(self._home_goals, self._away_goals, lam_home, lam_away, rho))
        )
        ll_weighted = np.sum(self._weights * ll)

        # 层级/弱先验（-log prior）
        lp = (
            - 0.5 * (mu / self.mu_prior_sigma) ** 2
            - 0.5 * ((home_adv - self.home_prior) / self.sigma_home) ** 2
            - 0.5 * (rho / self.sigma_rho) ** 2
            - 0.5 * np.sum((att / self.sigma_att) ** 2)
            - 0.5 * np.sum((dfn / self.sigma_def) ** 2)
        )
        return -(ll_weighted + lp)

    # ---------------- 拟合 ----------------

    def fit(self, df: pd.DataFrame, league: Optional[str] = None) -> "BayesianHierarchicalModel":
        if minimize is None:
            raise RuntimeError("scipy.optimize 不可用，无法做 MAP 优化")

        self.league = league
        home_idx, away_idx, home_goals, away_goals, teams = self._prepare(df)
        self._home_idx = home_idx
        self._away_idx = away_idx
        self._home_goals = home_goals.astype(float)
        self._away_goals = away_goals.astype(float)
        self._teams = teams
        self.teams = teams
        self.num_matches = len(home_idx)

        dates = df["date"].to_numpy()
        self._weights = self._time_weights(dates, self.time_decay_half_life, self.min_weight)
        self.date_min = str(df["date"].min().date())
        self.date_max = str(df["date"].max().date())

        T = len(teams)
        n_params = 3 + 2 * T

        # 初始值：μ=联赛平均 log 进球/2，home_adv=0.28，ρ=-0.10，攻防=0（升班马/新队先验=联赛均值）
        avg_goals = max((home_goals.mean() + away_goals.mean()) / 2.0, 1.0)
        x0 = np.zeros(n_params)
        x0[0] = np.log(avg_goals / 2.0)  # μ：每队 log 期望进球基线
        x0[1] = self.home_prior
        x0[2] = -0.10

        bounds = [(-2.0, 2.0)] * 3 + [(-3.0, 3.0)] * (2 * T)

        result = minimize(
            self._neg_log_posterior,
            x0,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 2000, "ftol": 1e-10, "gtol": 1e-6, "maxls": 40},
        )

        if not result.success:
            # 不中断，记录收敛警告（MAP 优化收敛风险为已知点）
            print(f"  [Bayes-Warn] L-BFGS-B 未完全收敛: {result.message} (status={result.status})")

        p = result.x
        self.mu, self.home_adv, self.rho = p[0], p[1], p[2]
        self.attack = p[3:3 + T]
        self.defense = p[3 + T:3 + 2 * T]
        self._fit_success = bool(result.success)

        self._laplace_std(p)
        return self

    def _laplace_std(self, params: np.ndarray) -> None:
        """Laplace 对角近似：std = sqrt(1 / H_ii)，H 为 -log posterior 的海森对角。"""
        try:
            f0 = self._neg_log_posterior(params)
            eps = 1e-4
            T = len(self.teams)
            att_std = np.zeros(T)
            def_std = np.zeros(T)
            # 仅对 attack/def 参数求海森对角（攻防不确定性是核心输出）
            base_offset = 3
            for i in range(2 * T):
                p_plus = params.copy()
                p_minus = params.copy()
                delta = eps * max(1.0, abs(params[base_offset + i]))
                p_plus[base_offset + i] += delta
                p_minus[base_offset + i] -= delta
                f_plus = self._neg_log_posterior(p_plus)
                f_minus = self._neg_log_posterior(p_minus)
                h_ii = (f_plus - 2.0 * f0 + f_minus) / (delta ** 2)
                h_ii = max(h_ii, 1e-6)
                std = float(np.sqrt(1.0 / h_ii))
                if i < T:
                    att_std[i] = std
                else:
                    def_std[i - T] = std
            self.attack_std = np.clip(att_std, 0.01, 2.0)
            self.defense_std = np.clip(def_std, 0.01, 2.0)
        except Exception as e:  # pragma: no cover
            print(f"  [Bayes-Warn] Laplace 不确定度计算失败，回退默认值: {e}")
            T = len(self.teams)
            self.attack_std = np.full(T, self.sigma_att)
            self.defense_std = np.full(T, self.sigma_def)

    # ---------------- 预测 ----------------

    def predict_lambda(self, home_team: str, away_team: str) -> Tuple[float, float]:
        hi = self._team_index(home_team)
        ai = self._team_index(away_team)
        att_h = self.attack[hi] if hi is not None else 0.0
        def_h = self.defense[hi] if hi is not None else 0.0
        att_a = self.attack[ai] if ai is not None else 0.0
        def_a = self.defense[ai] if ai is not None else 0.0
        lam_home = float(np.exp(self.mu + self.home_adv + att_h - def_a))
        lam_away = float(np.exp(self.mu + att_a - def_h))
        return lam_home, lam_away

    def predict_wdl(self, home_team: str, away_team: str, max_goals: int = 10) -> Dict[str, float]:
        """返回 {'win': 主胜, 'draw': 平, 'lose': 客胜}（与 stack_wdl_probabilities 兼容）。"""
        lam_home, lam_away = self.predict_lambda(home_team, away_team)
        win = draw = lose = 0.0
        for i in range(max_goals + 1):
            p_i = math.exp(-lam_home) * (lam_home ** i) / math.factorial(i)
            for j in range(max_goals + 1):
                p_j = math.exp(-lam_away) * (lam_away ** j) / math.factorial(j)
                p = p_i * p_j
                tau = float(_tau_dixon_coles(
                    np.array([i]), np.array([j]),
                    np.array([lam_home]), np.array([lam_away]), self.rho,
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

    def _team_index(self, team: str) -> Optional[int]:
        return self.teams.index(team) if team in self.teams else None

    # ---------------- 序列化 ----------------

    def to_dict(self) -> Dict:
        return {
            "model_type": "bayesian_hierarchical",
            "version": "P1-5-v1",
            "league": self.league,
            "num_matches": self.num_matches,
            "date_min": self.date_min,
            "date_max": self.date_max,
            "mu": float(self.mu),
            "home_adv": float(self.home_adv),
            "rho": float(self.rho),
            "time_decay_half_life": self.time_decay_half_life,
            "teams": self.teams,
            "attack": [float(a) for a in self.attack],
            "defense": [float(d) for d in self.defense],
            "attack_std": [float(s) for s in self.attack_std],
            "defense_std": [float(s) for s in self.defense_std],
        }

    def save(self, path: Optional[str] = None) -> str:
        if path is None:
            league_key = self.league if self.league else "all"
            path = os.path.join(_ASSETS_DIR, f"bayesian_model_{league_key}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path: str) -> "BayesianHierarchicalModel":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        m = cls(
            sigma_att=data.get("sigma_att", 0.35),
            sigma_def=data.get("sigma_def", 0.35),
            time_decay_half_life=data.get("time_decay_half_life", 300.0),
        )
        m.league = data.get("league")
        m.teams = data["teams"]
        m.mu = data["mu"]
        m.home_adv = data["home_adv"]
        m.rho = data["rho"]
        m.attack = np.array(data["attack"])
        m.defense = np.array(data["defense"])
        m.attack_std = np.array(data.get("attack_std", [0.35] * len(m.teams)))
        m.defense_std = np.array(data.get("defense_std", [0.35] * len(m.teams)))
        m.num_matches = data.get("num_matches", 0)
        m.date_min = data.get("date_min")
        m.date_max = data.get("date_max")
        return m


# ---------------- 评估辅助 ----------------

def compute_rps(y_true: np.ndarray, y_proba: np.ndarray) -> float:
    """RPS（标签 0=客胜,1=平,2=主胜；y_proba 列序 [客胜,平,主胜]）。"""
    n = len(y_true)
    o = np.zeros((n, 3))
    o[np.arange(n), y_true.astype(int)] = 1.0
    return float(np.mean(np.sum((np.cumsum(y_proba, axis=1) - np.cumsum(o, axis=1)) ** 2, axis=1)) / 2)


def evaluate_model(model: BayesianHierarchicalModel, df: pd.DataFrame) -> Dict:
    """对给定比赛集评估 WDL 预测质量。"""
    y_true = []
    y_proba = []
    for _, row in df.iterrows():
        h, a = row["home_team_name"], row["away_team_name"]
        r = int(row["result"])
        wdl = model.predict_wdl(h, a)
        y_true.append(r)
        y_proba.append([wdl["lose"], wdl["draw"], wdl["win"]])
    y_true = np.array(y_true)
    y_proba = np.array(y_proba)
    pred = np.argmax(y_proba, axis=1)
    acc = float(np.mean(pred == y_true))
    rps = compute_rps(y_true, y_proba)
    # logloss（带裁剪）
    y_proba_clip = np.clip(y_proba, 1e-7, 1.0)
    o = np.zeros_like(y_proba_clip)
    o[np.arange(len(y_true)), y_true.astype(int)] = 1.0
    ll = float(-np.mean(np.sum(o * np.log(y_proba_clip), axis=1)))
    return {"n": len(y_true), "acc": acc, "rps": rps, "logloss": ll, "y_true": y_true, "y_proba": y_proba}


def train_league_models(df: pd.DataFrame, all_leagues=None) -> Dict[str, Dict]:
    """分联赛训练并保存模型，返回 {league: 训练摘要}。"""
    leagues = all_leagues or LEAGUES
    summary = {}
    for lg in leagues:
        lg_df = df[df["competition_name"] == lg].sort_values("date").reset_index(drop=True)
        if len(lg_df) < 100:
            print(f"  [Skip] {lg} 样本不足 ({len(lg_df)})")
            continue
        # 时间序列切分：80% 训练 / 20% 测试
        split = int(len(lg_df) * 0.8)
        train_df = lg_df.iloc[:split]
        test_df = lg_df.iloc[split:]

        model = BayesianHierarchicalModel()
        model.fit(train_df, league=lg)
        path = model.save()
        eval_res = evaluate_model(model, test_df)
        summary[lg] = {
            "path": path,
            "train_n": len(train_df),
            "test_n": len(test_df),
            "num_teams": len(model.teams),
            "mu": round(model.mu, 4),
            "home_adv": round(model.home_adv, 4),
            "rho": round(model.rho, 4),
            "converged": getattr(model, "_fit_success", False),
            "test_acc": round(eval_res["acc"], 4),
            "test_rps": round(eval_res["rps"], 4),
            "test_logloss": round(eval_res["logloss"], 4),
        }
        print(f"  [Bayes] {lg}: 训练{len(train_df)} 测试{len(test_df)} 队{len(model.teams)} "
              f"μ={model.mu:.3f} home={model.home_adv:.3f} ρ={model.rho:.3f} | "
              f"RPS={eval_res['rps']:.4f} Acc={eval_res['acc']:.3f} LogLoss={eval_res['logloss']:.3f}")
    return summary


def load_match_data():
    """从 odds.db 加载 5 大联赛完整比赛结果（用干净 league 列 + actual_score 解析）。

    注意：feature_utils.load_match_data_odds 的 competition_map 仅覆盖 23/24 起，
    16/17~22/23 会保留 "英超2016-2017赛季" 等变体名。此处改用 matches.league
    （P1-9 已用 match_type 回填为 5 个干净联赛名），覆盖 16/17~26/27 全季。
    """
    import sqlite3
    import sys
    sys.path.insert(0, _SCRIPT_DIR)
    from feature_utils import normalize_team_name, ODDS_DB_PATH

    conn = sqlite3.connect(ODDS_DB_PATH)
    query = """
        SELECT match_date AS date, home_team, away_team, actual_score, league
        FROM matches
        WHERE league IS NOT NULL AND actual_score IS NOT NULL
        ORDER BY match_date
    """
    df = pd.read_sql(query, conn)
    conn.close()

    df["date"] = pd.to_datetime(df["date"], format="mixed")
    df["home_team_name"] = df["home_team"].apply(normalize_team_name)
    df["away_team_name"] = df["away_team"].apply(normalize_team_name)

    home_goals, away_goals = [], []
    for score in df["actual_score"]:
        if pd.isna(score):
            home_goals.append(np.nan)
            away_goals.append(np.nan)
        else:
            s = str(score)
            sep = "-" if "-" in s else (":" if ":" in s else None)
            if sep:
                parts = s.split(sep)
                try:
                    home_goals.append(int(parts[0].strip()))
                    away_goals.append(int(parts[1].strip()))
                    continue
                except (ValueError, IndexError):
                    pass
            home_goals.append(np.nan)
            away_goals.append(np.nan)

    df["homeGoals"] = home_goals
    df["awayGoals"] = away_goals
    df = df.dropna(subset=["homeGoals", "awayGoals"])
    df["homeGoals"] = df["homeGoals"].astype(int)
    df["awayGoals"] = df["awayGoals"].astype(int)
    df["competition_name"] = df["league"]
    df["result"] = np.where(df["homeGoals"] > df["awayGoals"], 2,
                            np.where(df["homeGoals"] < df["awayGoals"], 0, 1))
    df = df.sort_values("date").reset_index(drop=True)

    print(f"  [OK] Bayesian 数据加载: {len(df)} 场")
    print(f"     联赛分布:")
    for lg, count in df["competition_name"].value_counts().items():
        print(f"       - {lg}: {count} 场")
    return df


# ---------------- CLI ----------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="贝叶斯层级足球模型（P1-5）")
    parser.add_argument("--train-all", action="store_true", help="分联赛训练全部 5 大联赛")
    parser.add_argument("--league", type=str, default=None, help="仅训练/评估指定联赛")
    parser.add_argument("--evaluate", action="store_true", help="训练后评估测试集 RPS")
    parser.add_argument("--json-summary", type=str, default=None, help="汇总输出到 JSON 文件")
    args = parser.parse_args()

    df = load_match_data()
    if args.league:
        leagues = [args.league]
    else:
        leagues = LEAGUES

    if args.train_all or args.league:
        summary = train_league_models(df, all_leagues=leagues)
    else:
        summary = {}

    if args.evaluate or args.json_summary:
        # 评估模式下也确保已训练
        if not summary:
            summary = train_league_models(df, all_leagues=leagues)

    if args.json_summary:
        with open(args.json_summary, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"  汇总已写入: {args.json_summary}")

    for lg, s in summary.items():
        print(f"  {lg}: {s}")


if __name__ == "__main__":
    main()