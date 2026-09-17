"""
单元测试：特征工程工具模块
==========================

测试 feature_utils.py 的核心工具函数：
- normalize_team_name: 球队名规范化
- calculate_implied_probability: 隐含概率计算
- calculate_kelly_criterion: 凯利指数计算
- calculate_change_rate: 变化率计算
- weighted_mean: 加权均值
- winsorize_series: 缩尾处理
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from feature_utils import (
    normalize_team_name,
    calculate_implied_probability,
    calculate_kelly_criterion,
    calculate_change_rate,
    weighted_mean,
    winsorize_series,
)


class TestNormalizeTeamName(unittest.TestCase):
    """测试球队名规范化"""

    def test_basic_normalization(self):
        """基本规范化测试"""
        result = normalize_team_name("Arsenal FC")
        self.assertIsInstance(result, str)

    def test_empty_input(self):
        """空输入应返回空或原样"""
        result = normalize_team_name("")
        self.assertIsInstance(result, str)

    def test_none_input(self):
        """None 输入应优雅处理"""
        try:
            result = normalize_team_name(None)
            self.assertIsNone(result)  # 实现可能返回 None 或抛异常
        except (TypeError, AttributeError):
            pass  # 可接受的异常

    def test_consistency(self):
        """相同输入应返回相同输出"""
        name = "Manchester United"
        r1 = normalize_team_name(name)
        r2 = normalize_team_name(name)
        self.assertEqual(r1, r2)


class TestImpliedProbability(unittest.TestCase):
    """测试隐含概率计算"""

    def test_single_outcome(self):
        """单一结果隐含概率"""
        prob = calculate_implied_probability(2.0)
        self.assertAlmostEqual(prob, 0.5, places=4)

    def test_high_odds_low_prob(self):
        """高赔率应有低概率"""
        prob_high = calculate_implied_probability(1.5)
        prob_low = calculate_implied_probability(5.0)
        self.assertGreater(prob_high, prob_low)

    def test_range(self):
        """概率应在 (0, 1] 范围内"""
        for odds in [1.01, 1.5, 2.0, 3.0, 5.0, 10.0]:
            prob = calculate_implied_probability(odds)
            self.assertGreater(prob, 0)
            self.assertLessEqual(prob, 1)


class TestKellyCriterion(unittest.TestCase):
    """测试凯利指数计算"""

    def test_fair_odds_zero_kelly(self):
        """公平赔率下 Kelly 应为 0 或接近 0"""
        # 赔率 2.0 对应隐含概率 0.5，若真实概率也是 0.5，Kelly 应为 0
        kelly = calculate_kelly_criterion(0.5, 2.0)
        self.assertAlmostEqual(kelly, 0.0, places=4)

    def test_value_bet_positive_kelly(self):
        """价值投注（真实概率 > 隐含概率）应有正 Kelly"""
        # 赔率 3.0 隐含概率 0.333，真实概率 0.5 → 价值投注
        kelly = calculate_kelly_criterion(0.5, 3.0)
        self.assertGreater(kelly, 0)

    def test_no_value_negative_kelly(self):
        """无价值投注应有负 Kelly"""
        # 赔率 1.5 隐含概率 0.667，真实概率 0.5 → 无价值
        kelly = calculate_kelly_criterion(0.5, 1.5)
        self.assertLess(kelly, 0)


class TestChangeRate(unittest.TestCase):
    """测试变化率计算"""

    def test_no_change(self):
        """无变化时变化率应为 0"""
        rate = calculate_change_rate(2.0, 2.0)
        self.assertAlmostEqual(rate, 0.0, places=6)

    def test_increase(self):
        """上升时变化率应为正"""
        rate = calculate_change_rate(2.5, 2.0)
        self.assertGreater(rate, 0)

    def test_decrease(self):
        """下降时变化率应为负"""
        rate = calculate_change_rate(1.5, 2.0)
        self.assertLess(rate, 0)


class TestWeightedMean(unittest.TestCase):
    """测试加权均值"""

    def test_equal_weights(self):
        """等权重时应等于简单均值"""
        values = np.array([1.0, 2.0, 3.0, 4.0])
        weights = np.array([1.0, 1.0, 1.0, 1.0])
        result = weighted_mean(values, weights)
        self.assertAlmostEqual(result, 2.5, places=6)

    def test_weighted_towards_high(self):
        """高权重应使结果偏向高值"""
        values = np.array([1.0, 10.0])
        weights = np.array([0.1, 0.9])
        result = weighted_mean(values, weights)
        self.assertGreater(result, 5.0)  # 偏向 10

    def test_single_value(self):
        """单值应返回该值"""
        result = weighted_mean(np.array([5.0]), np.array([1.0]))
        self.assertAlmostEqual(result, 5.0, places=6)


class TestWinsorize(unittest.TestCase):
    """测试缩尾处理"""

    def test_no_outliers(self):
        """无异常值时数据应不变"""
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = winsorize_series(series, lower_percentile=0, upper_percentile=100)
        np.testing.assert_array_almost_equal(result.values, series.values)

    def test_extreme_outliers_clipped(self):
        """极端值应被裁剪"""
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
        result = winsorize_series(series, lower_percentile=1, upper_percentile=99)
        # 极端值 100 应被缩小
        self.assertLess(result.iloc[-1], 100.0)

    def test_preserves_length(self):
        """缩尾后长度应不变"""
        series = pd.Series(np.random.RandomState(42).normal(0, 1, 100))
        result = winsorize_series(series)
        self.assertEqual(len(result), len(series))


if __name__ == '__main__':
    unittest.main(verbosity=2)
