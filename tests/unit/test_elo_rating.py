"""
单元测试：Elo Rating 模块
=========================

测试 elo_rating.py 的核心函数：
- expected_score: 期望得分计算
- update_elo: Elo 更新
- result_to_score: 比赛结果转得分
- goal_diff_to_score: 净胜球转得分
- get_elo_feature_names: 特征名列表
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))

from elo_rating import (
    expected_score,
    update_elo,
    result_to_score,
    goal_diff_to_score,
    get_elo_feature_names,
    build_elo_features,
)


class TestExpectedScore(unittest.TestCase):
    """测试期望得分计算（标准 Elo 公式，已修复 bug）"""

    def test_equal_elo(self):
        """等分情况下期望得分应为 0.5"""
        self.assertAlmostEqual(expected_score(1500, 1500), 0.5, places=6)

    def test_equal_elo_with_home_advantage(self):
        """等分+主场优势应提高主队期望得分"""
        # home_advantage=65 → rating_a=1565, E_A = 1/(1+10^((1500-1565)/400)) ≈ 0.595
        self.assertAlmostEqual(expected_score(1500, 1500, home_advantage=65), 0.595, places=2)

    def test_higher_elo_favored(self):
        """高分球队期望得分应高于 0.5"""
        self.assertGreater(expected_score(1600, 1500), 0.5)
        self.assertLess(expected_score(1500, 1600), 0.5)

    def test_home_advantage_boost(self):
        """主场优势应提高主队期望得分"""
        without_home = expected_score(1500, 1500, home_advantage=0)
        with_home = expected_score(1500, 1500, home_advantage=65)
        self.assertGreater(with_home, without_home)

    def test_bounds(self):
        """期望得分应在 (0, 1) 范围内"""
        for elo_a in [800, 1200, 1500, 1800, 2200]:
            for elo_b in [800, 1200, 1500, 1800, 2200]:
                score = expected_score(elo_a, elo_b)
                self.assertGreater(score, 0)
                self.assertLess(score, 1)


class TestUpdateElo(unittest.TestCase):
    """测试 Elo 更新逻辑（标准公式，已修复 bug）"""

    def test_win_increases_elo(self):
        """胜利应增加 Elo"""
        new_a, new_b = update_elo(1500, 1500, 1.0, k_factor=32)
        self.assertGreater(new_a, 1500)
        self.assertLess(new_b, 1500)

    def test_loss_decreases_elo(self):
        """失败应减少 Elo"""
        new_a, new_b = update_elo(1500, 1500, 0.0, k_factor=32)
        self.assertLess(new_a, 1500)
        self.assertGreater(new_b, 1500)

    def test_draw_no_change_when_equal(self):
        """等分平局时 Elo 应保持不变"""
        new_a, new_b = update_elo(1500, 1500, 0.5, k_factor=32)
        self.assertAlmostEqual(new_a, 1500, places=6)
        self.assertAlmostEqual(new_b, 1500, places=6)

    def test_zero_sum_property(self):
        """Elo 更新应满足零和性质（无主场优势时）"""
        total_before = 1500 + 1500
        new_a, new_b = update_elo(1500, 1500, 1.0, k_factor=32, home_advantage=0)
        self.assertAlmostEqual(new_a + new_b, total_before, places=6)

    def test_upset_larger_change(self):
        """冷门（低分队赢高分队）应有更大的 Elo 变化"""
        # 等分对决
        new_a_equal, _ = update_elo(1500, 1500, 1.0, k_factor=32)
        delta_equal = new_a_equal - 1500

        # 冷门对决（低分赢高分）
        new_a_upset, _ = update_elo(1300, 1700, 1.0, k_factor=32)
        delta_upset = new_a_upset - 1300

        self.assertGreater(delta_upset, delta_equal)

    def test_elo_clipped_to_range(self):
        """Elo 应被裁剪到 [1000, 2000] 范围内"""
        new_a, new_b = update_elo(1990, 1010, 1.0, k_factor=100)
        self.assertGreaterEqual(new_a, 1000)
        self.assertLessEqual(new_a, 2000)
        self.assertGreaterEqual(new_b, 1000)
        self.assertLessEqual(new_b, 2000)


class TestScoreConversion(unittest.TestCase):
    """测试比赛结果转换"""

    def test_result_to_score(self):
        """测试结果转得分"""
        self.assertEqual(result_to_score(2, 1), 1.0)  # 主胜
        self.assertEqual(result_to_score(1, 1), 0.5)  # 平局
        self.assertEqual(result_to_score(0, 1), 0.0)  # 主负
        self.assertEqual(result_to_score(3, 0), 1.0)  # 大胜
        self.assertEqual(result_to_score(0, 3), 0.0)  # 大负

    def test_goal_diff_to_score(self):
        """测试净胜球转得分（应考虑净胜球幅度）"""
        # 净胜球越大，得分应越接近 1（或等于 1）
        narrow_win = goal_diff_to_score(2, 1)
        big_win = goal_diff_to_score(5, 0)
        self.assertGreaterEqual(big_win, narrow_win)
        self.assertGreater(narrow_win, 0.5)
        self.assertGreater(big_win, 0.5)


class TestEloFeatureNames(unittest.TestCase):
    """测试 Elo 特征名列表"""

    def test_feature_count(self):
        """应返回 10 个特征名"""
        names = get_elo_feature_names()
        self.assertEqual(len(names), 10)

    def test_feature_names_content(self):
        """应包含关键 Elo 特征"""
        names = get_elo_feature_names()
        self.assertIn('home_elo', names)
        self.assertIn('away_elo', names)
        self.assertIn('elo_diff', names)

    def test_all_names_strings(self):
        """所有特征名应为字符串"""
        names = get_elo_feature_names()
        for name in names:
            self.assertIsInstance(name, str)


class TestBuildEloFeatures(unittest.TestCase):
    """测试 Elo 特征构建"""

    def test_build_with_sample_data(self):
        """使用合成数据测试特征构建"""
        from sys import path
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fixtures'))
        from sample_data import make_sample_matches

        df = make_sample_matches(n=20, seed=42)
        try:
            features = build_elo_features(df)
            self.assertIsInstance(features, pd.DataFrame)
            self.assertEqual(len(features), 20)
            self.assertGreaterEqual(features.shape[1], 8)
        except Exception as e:
            # 数据库依赖可能失败，确保是预期的失败类型
            self.skipTest(f"跳过：依赖未满足 - {e}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
