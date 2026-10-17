# -*- coding: utf-8 -*-
"""EV 期望值引擎单元测试（对标 docs/EV期望值引擎设计文档_v1.0.md §9）。"""

import unittest

from ev_engine import (
    calc_implied_probabilities, remove_vig, calc_ev, calc_edge,
    calc_kelly, make_decision, analyze_match, ModelProbabilities, OddsData,
)


class TestEVEngine(unittest.TestCase):

    def test_calc_implied_probabilities(self):
        """测试隐含概率计算"""
        odds = OddsData(home=2.0, draw=3.0, away=4.0)
        h, d, a = calc_implied_probabilities(odds)
        self.assertAlmostEqual(h, 0.5, places=4)
        self.assertAlmostEqual(d, 1 / 3, places=4)
        self.assertAlmostEqual(a, 0.25, places=4)

    def test_remove_vig(self):
        """测试去抽水"""
        # 隐含概率 0.5 + 0.333 + 0.25 = 1.083（抽水 8.3%）
        h, d, a, total, vig = remove_vig(0.5, 1 / 3, 0.25)
        self.assertAlmostEqual(h + d + a, 1.0, places=4)  # 归一化后和为 1
        self.assertAlmostEqual(vig, 0.0833, places=3)      # 抽水率

    def test_calc_ev_positive(self):
        """测试正 EV"""
        ev = calc_ev(0.5, 2.20)
        self.assertAlmostEqual(ev, 0.1, places=4)

    def test_calc_ev_negative(self):
        """测试负 EV"""
        ev = calc_ev(0.4, 2.00)
        self.assertAlmostEqual(ev, -0.2, places=4)

    def test_calc_ev_break_even(self):
        """测试盈亏平衡"""
        ev = calc_ev(0.5, 2.00)
        self.assertAlmostEqual(ev, 0.0, places=4)

    def test_calc_edge(self):
        """测试价值空间"""
        edge = calc_edge(0.5, 0.45)
        self.assertAlmostEqual(edge, 0.05, places=4)

    def test_calc_kelly(self):
        """测试凯利计算"""
        full, quarter, clipped = calc_kelly(0.55, 2.10, strategy="quarter", cap=0.25)
        self.assertAlmostEqual(full, 0.1409, places=3)
        self.assertAlmostEqual(quarter, 0.0352, places=3)
        self.assertAlmostEqual(clipped, 0.0352, places=3)

    def test_kelly_cap(self):
        """测试凯利仓位上限"""
        full, quarter, clipped = calc_kelly(0.9, 1.50, strategy="full", cap=0.25)
        self.assertLessEqual(clipped, 0.25)

    def test_make_decision_value(self):
        """测试 VALUE 决策"""
        decision = make_decision(ev=0.05, edge=0.03, ev_threshold=0.02)
        self.assertEqual(decision, "VALUE")

    def test_make_decision_marginal(self):
        """测试 MARGINAL 决策"""
        decision = make_decision(ev=0.01, edge=0.02, ev_threshold=0.02)
        self.assertEqual(decision, "MARGINAL")

    def test_make_decision_avoid_negative_ev(self):
        """测试 AVOID 决策（负 EV）"""
        decision = make_decision(ev=-0.05, edge=0.03, ev_threshold=0.02)
        self.assertEqual(decision, "AVOID")

    def test_make_decision_avoid_negative_edge(self):
        """测试 AVOID 决策（负 edge）"""
        decision = make_decision(ev=0.05, edge=-0.01, ev_threshold=0.02)
        self.assertEqual(decision, "AVOID")

    def test_analyze_match_full(self):
        """测试完整比赛分析"""
        probs = ModelProbabilities(home=0.55, draw=0.25, away=0.20)
        odds = OddsData(home=2.00, draw=3.50, away=4.00)
        result = analyze_match(probs, odds, ev_threshold=0.02)

        self.assertEqual(result.overall_decision, "VALUE")
        self.assertEqual(result.best_direction, "home")
        self.assertGreater(result.best_ev, 0.02)
        self.assertGreater(result.recommended_stake_pct, 0)
        self.assertAlmostEqual(
            result.home_analysis.p_model + result.draw_analysis.p_model + result.away_analysis.p_model,
            1.0, places=4)

    def test_analyze_match_all_avoid(self):
        """测试三个方向都 AVOID 的情况"""
        probs = ModelProbabilities(home=0.45, draw=0.30, away=0.25)
        odds = OddsData(home=2.22, draw=3.33, away=4.00)
        result = analyze_match(probs, odds, ev_threshold=0.02)
        self.assertEqual(result.overall_decision, "AVOID")
        self.assertIsNone(result.best_direction)
        self.assertEqual(result.recommended_stake_pct, 0.0)

    def test_invalid_odds(self):
        """测试非法赔率"""
        odds = OddsData(home=0.5, draw=3.0, away=4.0)
        with self.assertRaises(ValueError):
            calc_implied_probabilities(odds)

    def test_invalid_prob(self):
        """测试非法概率"""
        with self.assertRaises(ValueError):
            calc_ev(p_model=1.5, odds=2.0)

    def test_probs_normalize(self):
        """测试概率归一化"""
        probs = ModelProbabilities(home=0.50, draw=0.30, away=0.21)  # 和为 1.01
        self.assertTrue(probs.validate())
        norm = probs.normalize()
        self.assertAlmostEqual(norm.home + norm.draw + norm.away, 1.0, places=4)


if __name__ == '__main__':
    unittest.main()