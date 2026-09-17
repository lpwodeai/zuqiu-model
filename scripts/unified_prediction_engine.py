# -*- coding: utf-8 -*-
"""
统一预测引擎 — Dixon-Coles 比分生成器（P0-4）

问题背景：
  T-005（让球胜平负）和 T-006（比分）是独立并行模型，T-006 v4 通过 WDL 重加权后处理
  缓解不一致，但根本架构未统一。

目标架构：
  ML 回归模型 → λ_home, λ_away → Dixon-Coles 比分矩阵 →
    ├─ 胜平负概率（边际分布）
    ├─ 让球概率（条件分布）
    ├─ 大小球概率（条件分布）
    └─ 精确比分（矩阵直接输出）

架构参考：
  Dixon & Coles (1997) "Modelling Association Football Scores and Inefficiencies
  in the Football Betting Market"
  ρ 参数: 低比分平局修正（0:0, 1:1, 0:1, 1:0）

用法：
  from unified_prediction_engine import DixonColesGenerator
  gen = DixonColesGenerator(rho=-0.10, max_goals=10)
  result = gen.generate(lambda_home=1.5, lambda_away=0.8)
  # result['wdl'] = {'win': 0.55, 'draw': 0.22, 'lose': 0.23}
  # result['score_matrix'] = 11x11 numpy array
  # result['handicap'] = {-2: 0.05, -1: 0.12, ...}
  # result['over_under'] = {0.5: 0.95, 1.5: 0.80, 2.5: 0.55, ...}
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np


class DixonColesGenerator:
    """Dixon-Coles 比分矩阵生成器。

    基于 λ_home, λ_away 和 ρ 参数，生成完整的比分矩阵，
    并从矩阵中导出所有预测任务（胜平负、让球、大小球、精确比分）。

    Attributes:
        rho: 低比分平局修正参数（-0.30 激进, -0.10 保守, 0.0 无修正）
        max_goals: 最大进球数截断
        eps: 数值稳定性
    """

    # 联赛经验 ρ 值
    LEAGUE_RHO = {
        "英超": -0.08, "西甲": -0.12, "意甲": -0.15,
        "德甲": -0.05, "法甲": -0.10,
    }

    # T-006 ρ 策略对齐（前置项 3，unified_engine_integration_plan §8）
    # 与 prediction_core.py 的 T006_RHO=-0.30 / T006_RHO_HIGH=-0.10 保持一致
    T006_RHO = -0.30
    T006_RHO_HIGH = -0.10

    def __init__(
        self,
        rho: float = -0.30,
        max_goals: int = 10,
        eps: float = 1e-15,
    ):
        self.rho = rho
        self.max_goals = max_goals
        self.eps = eps

        # 缓存: Poisson 概率
        self._poisson_cache: Dict[Tuple[float, int], float] = {}

    def set_league_rho(self, league: Optional[str] = None, strong_handicap: bool = False) -> None:
        """设置 ρ 参数（前置项 3：与 T-006 ρ 策略对齐）。

        优先级（等价 prediction_core 的 T006_RHO/T006_RHO_HIGH 策略）：
          1. strong_handicap=True → T006_RHO_HIGH=-0.10（strong handicap 保守修正）
          2. league 命中 LEAGUE_RHO → 联赛经验 ρ（阶段 A/B 按联赛启用）
          3. 默认 → T006_RHO=-0.30（与现有 T-006 比分预测一致）
        """
        if strong_handicap:
            self.rho = self.T006_RHO_HIGH
        elif league and league in self.LEAGUE_RHO:
            self.rho = self.LEAGUE_RHO[league]
        else:
            self.rho = self.T006_RHO

    # ==================== 核心生成 ====================

    def _poisson_pmf(self, lam: float, k: int) -> float:
        """Poisson PMF（带缓存）。"""
        cache_key = (round(lam, 4), k)
        if cache_key in self._poisson_cache:
            return self._poisson_cache[cache_key]
        if lam <= 0:
            val = 1.0 if k == 0 else 0.0
        else:
            val = math.exp(-lam) * (lam ** k) / math.factorial(k)
        self._poisson_cache[cache_key] = val
        return val

    def _dc_correction(
        self, home_goals: int, away_goals: int,
        lambda_home: float, lambda_away: float,
    ) -> float:
        """Dixon-Coles τ 修正（论文标准版，对齐 prediction_core.CalcEngine）。

        前置项 1（unified_engine_integration_plan §8）：原简化版 `1±ρ` 在 λ 偏离 1
        时产生偏差，改为 Dixon & Coles (1997) 标准形式：
          τ(0,0) = 1 - λ_home·λ_away·ρ
          τ(0,1) = 1 + λ_home·ρ
          τ(1,0) = 1 + λ_away·ρ
          τ(1,1) = 1 - ρ
        并对 τ 做 [0.1, 3.0] 截断（与 CalcEngine.poisson_score_predict 一致）。
        """
        if home_goals == 0 and away_goals == 0:
            tau = 1.0 - lambda_home * lambda_away * self.rho
        elif home_goals == 0 and away_goals == 1:
            tau = 1.0 + lambda_home * self.rho
        elif home_goals == 1 and away_goals == 0:
            tau = 1.0 + lambda_away * self.rho
        elif home_goals == 1 and away_goals == 1:
            tau = 1.0 - self.rho
        else:
            return 1.0
        return max(0.1, min(3.0, tau))

    def generate(
        self, lambda_home: float, lambda_away: float, verbose: bool = True
    ) -> Dict:
        """生成完整的 Dixon-Coles 比分矩阵及导出预测。

        Args:
            lambda_home: 主队期望进球数
            lambda_away: 客队期望进球数

        Returns:
            dict with keys:
                - score_matrix: (max_goals+1) x (max_goals+1) 概率矩阵
                - wdl: {'win': float, 'draw': float, 'lose': float}
                - handicap: {line: probability} 让球胜平负概率
                - over_under: {line: probability} 大小球概率
                - exact_score: [(score_str, prob)] 精确比分 Top10
                - total_goals: {0: prob, 1: prob, ...} 总进球分布
                - lambda: {'home': float, 'away': float}
        """
        if verbose:
            print(f"[UnifiedEngine-Monitor] generate() 调用: lambda_home={lambda_home:.4f}, "
                  f"lambda_away={lambda_away:.4f}, rho={self.rho}, max_goals={self.max_goals}")
        n = self.max_goals + 1

        # 1. 构建独立 Poisson 矩阵
        poisson_matrix = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                poisson_matrix[i, j] = (
                    self._poisson_pmf(lambda_home, i)
                    * self._poisson_pmf(lambda_away, j)
                )

        # 2. 应用 Dixon-Coles τ 修正
        dc_matrix = poisson_matrix.copy()
        for i in range(min(n, 3)):
            for j in range(min(n, 3)):
                dc_matrix[i, j] *= self._dc_correction(i, j, lambda_home, lambda_away)

        # 3. 归一化（截断修正）
        total = dc_matrix.sum()
        if total > 0:
            dc_matrix /= total

        # 4. 导出胜平负概率
        win = 0.0
        draw = 0.0
        lose = 0.0
        for i in range(n):
            for j in range(n):
                p = dc_matrix[i, j]
                if i > j:
                    win += p
                elif i == j:
                    draw += p
                else:
                    lose += p

        # 5. 导出让球概率（-3 到 +3）
        handicap = {}
        for line in range(-3, 4):
            h_win = 0.0
            h_draw = 0.0
            h_lose = 0.0
            for i in range(n):
                for j in range(n):
                    p = dc_matrix[i, j]
                    adj_home = i + line
                    if adj_home > j:
                        h_win += p
                    elif adj_home == j:
                        h_draw += p
                    else:
                        h_lose += p
            handicap[line] = {
                "win": round(h_win, 6),
                "draw": round(h_draw, 6),
                "lose": round(h_lose, 6),
            }

        # 6. 导出大小球概率
        total_goals_dist = {}
        for tg in range(n * 2):
            prob = 0.0
            for i in range(n):
                j = tg - i
                if 0 <= j < n:
                    prob += dc_matrix[i, j]
            total_goals_dist[tg] = prob

        over_under = {}
        for line in [0.5, 1.5, 2.5, 3.5, 4.5, 5.5]:
            over_prob = 0.0
            under_prob = 0.0
            for tg, prob in total_goals_dist.items():
                if tg > line:
                    over_prob += prob
                else:
                    under_prob += prob
            over_under[line] = {
                "over": round(over_prob, 6),
                "under": round(under_prob, 6),
            }

        # 7. 导出精确比分 Top10
        scores = []
        for i in range(n):
            for j in range(n):
                if dc_matrix[i, j] > 0.001:
                    scores.append((f"{i}:{j}", float(dc_matrix[i, j])))
        scores.sort(key=lambda x: x[1], reverse=True)
        exact_scores = scores[:10]

        if verbose:
            _wdl_sum = win + draw + lose
            _matrix_sum = float(dc_matrix.sum())
            _top1 = exact_scores[0] if exact_scores else ("N/A", 0.0)
            print(f"[UnifiedEngine-Monitor] generate() 完成: wdl(win/draw/lose)="
                  f"({win:.4f}/{draw:.4f}/{lose:.4f}) sum={_wdl_sum:.6f} "
                  f"valid={abs(_wdl_sum - 1.0) < 1e-6}")
            _h1 = handicap[-1]
            _ou25 = over_under[2.5]
            print(f"[UnifiedEngine-Monitor]   矩阵和={_matrix_sum:.6f} | top比分={_top1[0]}({_top1[1]:.3f}) "
                  f"| 让球-1=({_h1['win']:.3f}/{_h1['draw']:.3f}/{_h1['lose']:.3f}) "
                  f"大小球2.5=over/under={_ou25['over']:.3f}/{_ou25['under']:.3f}")
        return {
            "score_matrix": dc_matrix,
            "wdl": {
                "win": round(win, 6),
                "draw": round(draw, 6),
                "lose": round(lose, 6),
            },
            "handicap": handicap,
            "over_under": over_under,
            "exact_score": exact_scores,
            "total_goals": {k: round(v, 6) for k, v in total_goals_dist.items()},
            "lambda": {"home": lambda_home, "away": lambda_away},
        }

    # ==================== 批量预测 ====================

    def generate_batch(
        self,
        lambda_pairs: List[Tuple[float, float]],
        leagues: Optional[List[str]] = None,
    ) -> List[Dict]:
        """批量生成比分预测。

        Args:
            lambda_pairs: [(λ_home, λ_away), ...] 列表
            leagues: 联赛名称列表（用于自动设置 ρ），长度需与 lambda_pairs 一致

        Returns:
            List[Dict]: 每场比赛的预测结果
        """
        results = []
        for i, (lh, la) in enumerate(lambda_pairs):
            if leagues and i < len(leagues):
                self.set_league_rho(leagues[i])
            results.append(self.generate(lh, la, verbose=False))
        return results

    # ==================== 验证 ====================

    def validate_wdl_sum(self, result: Dict) -> bool:
        """验证胜平负概率和为 1.0。"""
        wdl = result["wdl"]
        total = wdl["win"] + wdl["draw"] + wdl["lose"]
        return abs(total - 1.0) < 1e-6

    def validate_score_matrix(self, result: Dict) -> bool:
        """验证比分矩阵概率和为 1.0。"""
        total = result["score_matrix"].sum()
        return abs(total - 1.0) < 1e-6


# ==================== 便捷函数 ====================

def create_generator(league: Optional[str] = None) -> DixonColesGenerator:
    """创建联赛特定的生成器（默认 ρ 对齐 T006_RHO=-0.30）。"""
    rho = DixonColesGenerator.LEAGUE_RHO.get(league, DixonColesGenerator.T006_RHO) if league else DixonColesGenerator.T006_RHO
    return DixonColesGenerator(rho=rho)


def quick_predict(
    lambda_home: float,
    lambda_away: float,
    league: Optional[str] = None,
) -> Dict:
    """快速预测（单次调用）。"""
    gen = create_generator(league)
    return gen.generate(lambda_home, lambda_away)


# ==================== CLI 入口 ====================

def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Dixon-Coles 统一预测引擎（P0-4）")
    parser.add_argument("--lambda-home", type=float, required=True, help="主队期望进球")
    parser.add_argument("--lambda-away", type=float, required=True, help="客队期望进球")
    parser.add_argument("--league", type=str, default=None, help="联赛名称")
    parser.add_argument("--rho", type=float, default=None, help="手动指定 ρ")
    parser.add_argument("--max-goals", type=int, default=10, help="最大进球截断")
    args = parser.parse_args()

    rho = args.rho if args.rho is not None else (
        DixonColesGenerator.LEAGUE_RHO.get(args.league, DixonColesGenerator.T006_RHO)
        if args.league else DixonColesGenerator.T006_RHO
    )
    gen = DixonColesGenerator(rho=rho, max_goals=args.max_goals)
    result = gen.generate(args.lambda_home, args.lambda_away)

    # 美化输出
    output = {
        "lambda": result["lambda"],
        "wdl": result["wdl"],
        "wdl_sum_valid": bool(gen.validate_wdl_sum(result)),
        "score_matrix_valid": bool(gen.validate_score_matrix(result)),
        "exact_score_top5": result["exact_score"][:5],
        "handicap": {str(k): v for k, v in result["handicap"].items()},
        "over_under": {str(k): v for k, v in result["over_under"].items()},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()