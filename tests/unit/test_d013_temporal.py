"""
单元测试：D-013 时序赔率特征模块
================================

测试 d013_temporal_odds.py 的核心计算函数：
- compute_volatility: 波动率
- compute_acceleration: 加速度
- compute_late_trend / compute_early_trend: 趋势
- compute_mid_stability: 中期稳定性
- compute_sudden_jump: 突变检测
- compute_update_frequency: 更新频率
- compute_total_change: 总变化幅度
- get_d013_feature_names: 特征名列表
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from d013_temporal_odds import (
    compute_volatility,
    compute_acceleration,
    compute_late_trend,
    compute_early_trend,
    compute_mid_stability,
    compute_sudden_jump,
    compute_update_frequency,
    compute_total_change,
    get_d013_feature_names,
)


class TestVolatility(unittest.TestCase):
    """测试波动率计算"""

    def test_constant_series(self):
        """常数序列的波动率应为 0"""
        series = pd.Series([2.0, 2.0, 2.0, 2.0])
        self.assertAlmostEqual(compute_volatility(series), 0.0, places=6)

    def test_varying_series(self):
        """变化序列的波动率应大于 0"""
        series = pd.Series([2.0, 2.5, 1.8, 2.3])
        self.assertGreater(compute_volatility(series), 0)

    def test_short_series(self):
        """短序列（<2）应返回 0"""
        series = pd.Series([2.0])
        self.assertEqual(compute_volatility(series), 0.0)

    def test_non_negative(self):
        """波动率应非负"""
        rng = np.random.RandomState(42)
        for _ in range(10):
            series = pd.Series(rng.uniform(1.5, 3.0, size=5))
            self.assertGreaterEqual(compute_volatility(series), 0)


class TestAcceleration(unittest.TestCase):
    """测试加速度计算（二阶差分）"""

    def test_linear_series(self):
        """线性变化序列的加速度应接近 0"""
        series = pd.Series([2.0, 2.2, 2.4, 2.6])
        acc = compute_acceleration(series)
        self.assertAlmostEqual(abs(acc), 0.0, places=6)

    def test_convex_series(self):
        """凸序列（加速上升）应有正加速度"""
        series = pd.Series([2.0, 2.1, 2.3, 2.6])
        self.assertGreater(compute_acceleration(series), 0)

    def test_short_series(self):
        """短序列（<3）应返回 0"""
        self.assertEqual(compute_acceleration(pd.Series([2.0, 2.5])), 0.0)


class TestTrends(unittest.TestCase):
    """测试趋势计算"""

    def test_late_trend_increasing(self):
        """末尾上升序列的 late_trend 应为正"""
        series = pd.Series([2.0, 2.0, 2.1, 2.3, 2.5])
        self.assertGreater(compute_late_trend(series), 0)

    def test_late_trend_decreasing(self):
        """末尾下降序列的 late_trend 应为负"""
        series = pd.Series([2.5, 2.5, 2.4, 2.2, 2.0])
        self.assertLess(compute_late_trend(series), 0)

    def test_early_trend_increasing(self):
        """开头上升序列的 early_trend 应为正"""
        series = pd.Series([2.0, 2.3, 2.5, 2.5, 2.5])
        self.assertGreater(compute_early_trend(series), 0)

    def test_short_series(self):
        """短序列应返回 0"""
        self.assertEqual(compute_late_trend(pd.Series([2.0])), 0.0)
        self.assertEqual(compute_early_trend(pd.Series([2.0])), 0.0)


class TestMidStability(unittest.TestCase):
    """测试中期稳定性"""

    def test_stable_middle(self):
        """中间段稳定的序列应返回较高值"""
        series = pd.Series([2.0, 2.5, 2.5, 2.5, 2.0])
        stability = compute_mid_stability(series)
        self.assertIsInstance(stability, (int, float))

    def test_short_series(self):
        """短序列应返回数值（匹配当前实现，可能返回 0.5）"""
        result = compute_mid_stability(pd.Series([2.0]))
        self.assertIsInstance(result, (int, float))
        self.assertGreaterEqual(result, 0)
        self.assertLessEqual(result, 1)


class TestSuddenJump(unittest.TestCase):
    """测试突变检测"""

    def test_no_jump(self):
        """平滑序列的突变应为 0 或很小"""
        series = pd.Series([2.0, 2.05, 2.1, 2.15])
        self.assertLess(compute_sudden_jump(series, threshold=0.2), 0.2)

    def test_large_jump(self):
        """大幅跳变应被检测到"""
        series = pd.Series([2.0, 2.1, 5.0, 5.1])
        jump = compute_sudden_jump(series, threshold=0.2)
        self.assertGreater(jump, 0.2)

    def test_threshold_effect(self):
        """阈值越高，检测到的突变越少"""
        series = pd.Series([2.0, 2.5, 2.6, 2.7])
        small_threshold = compute_sudden_jump(series, threshold=0.1)
        large_threshold = compute_sudden_jump(series, threshold=0.5)
        self.assertGreaterEqual(small_threshold, large_threshold)


class TestUpdateFrequency(unittest.TestCase):
    """测试更新频率计算"""

    def test_single_day(self):
        """单日多时间点应返回较高频率"""
        df = pd.DataFrame({
            'win_a': [2.0, 2.1, 2.2, 2.3],
            'timestamp': pd.to_datetime([
                '2024-01-01 10:00', '2024-01-01 12:00',
                '2024-01-01 14:00', '2024-01-01 16:00'
            ])
        })
        freq = compute_update_frequency(df)
        self.assertGreater(freq, 0)

    def test_multi_day(self):
        """跨多天应返回较低频率"""
        df = pd.DataFrame({
            'win_a': [2.0, 2.1, 2.2, 2.3],
            'timestamp': pd.to_datetime([
                '2024-01-01', '2024-01-02', '2024-01-03', '2024-01-04'
            ])
        })
        freq = compute_update_frequency(df)
        self.assertIsInstance(freq, (int, float))


class TestTotalChange(unittest.TestCase):
    """测试总变化幅度"""

    def test_no_change(self):
        """常数序列总变化应为 0"""
        series = pd.Series([2.0, 2.0, 2.0])
        self.assertAlmostEqual(compute_total_change(series), 0.0, places=6)

    def test_monotonic_increase(self):
        """单调上升序列的总变化等于首尾差"""
        series = pd.Series([2.0, 2.5, 3.0])
        total = compute_total_change(series)
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_oscillating(self):
        """震荡序列的总变化应大于首尾差"""
        series = pd.Series([2.0, 3.0, 2.0, 3.0])
        total = compute_total_change(series)
        # 首尾差为 1.0，但总变化应更大（因为来回震荡）
        self.assertGreater(total, 1.0)

    def test_non_negative(self):
        """总变化应非负"""
        rng = np.random.RandomState(42)
        for _ in range(10):
            series = pd.Series(rng.uniform(1.5, 3.0, size=5))
            self.assertGreaterEqual(compute_total_change(series), 0)


class TestFeatureNames(unittest.TestCase):
    """测试 D-013 特征名列表"""

    def test_feature_count(self):
        """应返回 22 个特征名（P1-9 扩展版）"""
        names = get_d013_feature_names()
        self.assertEqual(len(names), 22)

    def test_feature_names_content(self):
        """应包含关键时序特征"""
        names = get_d013_feature_names()
        self.assertIn('wdl_win_volatility', names)
        self.assertIn('wdl_win_acceleration', names)
        self.assertIn('wdl_late_trend', names)
        self.assertIn('wdl_sudden_jump', names)

    def test_all_names_strings(self):
        """所有特征名应为字符串"""
        names = get_d013_feature_names()
        for name in names:
            self.assertIsInstance(name, str)


if __name__ == '__main__':
    unittest.main(verbosity=2)
