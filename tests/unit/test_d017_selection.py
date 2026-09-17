"""
单元测试：D-017 特征精简模块
=============================

测试 d017_feature_selection.py 的辅助函数和逻辑：
- get_forced_features: 强制保留特征识别
- compute_sample_weights: 样本权重计算
- evaluate_cv: CV 评估逻辑
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures'))

from sample_data import make_full_feature_set, make_sample_matches


class TestForcedFeatures(unittest.TestCase):
    """测试强制保留特征识别"""

    def test_forced_features_include_elo(self):
        """强制特征应包含 Elo"""
        from d017_feature_selection import get_forced_features
        X = make_full_feature_set(n=20, seed=42)
        forced = get_forced_features(X)
        elo_in_forced = [f for f in forced if f.startswith(('home_elo', 'away_elo', 'elo_'))]
        self.assertGreater(len(elo_in_forced), 0)

    def test_forced_features_include_temporal(self):
        """强制特征应包含 D-013 时序"""
        from d017_feature_selection import get_forced_features
        X = make_full_feature_set(n=20, seed=42)
        forced = get_forced_features(X)
        temporal_in_forced = [f for f in forced if f.startswith((
            'wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility',
            'wdl_win_acceleration', 'wdl_late_trend', 'wdl_early_trend',
            'wdl_mid_stability', 'wdl_sudden_jump', 'wdl_update_frequency',
            'wdl_total_change',
        ))]
        self.assertGreater(len(temporal_in_forced), 0)

    def test_forced_count(self):
        """强制特征应为 20 个（Elo 10 + Temporal 10）"""
        from d017_feature_selection import get_forced_features
        X = make_full_feature_set(n=20, seed=42)
        forced = get_forced_features(X)
        self.assertEqual(len(forced), 20)


class TestComputeSampleWeights(unittest.TestCase):
    """测试样本权重计算"""

    def test_uniform_leagues(self):
        """等量联赛应返回接近 1 的权重"""
        from d017_feature_selection import compute_sample_weights
        df = pd.DataFrame({
            'competition_name': ['A', 'B', 'A', 'B', 'A', 'B']
        })
        weights = compute_sample_weights(df)
        self.assertEqual(len(weights), 6)
        # 等量分配，权重应接近 1
        self.assertAlmostEqual(np.mean(weights), 1.0, places=2)

    def test_imbalanced_leagues(self):
        """不均衡联赛应给小联赛更高权重"""
        from d017_feature_selection import compute_sample_weights
        df = pd.DataFrame({
            'competition_name': ['A'] * 8 + ['B'] * 2
        })
        weights = compute_sample_weights(df)
        # 小联赛（B）应获得更高权重
        self.assertGreater(weights[8], weights[0])

    def test_no_competition_column(self):
        """无 competition_name 列时应返回全 1 权重"""
        from d017_feature_selection import compute_sample_weights
        df = pd.DataFrame({'other': [1, 2, 3]})
        weights = compute_sample_weights(df)
        np.testing.assert_array_equal(weights, np.ones(3))


class TestEvaluateCV(unittest.TestCase):
    """测试 CV 评估函数"""

    def test_evaluate_returns_dict(self):
        """evaluate_cv 应返回字典结果"""
        from d017_feature_selection import evaluate_cv
        X = make_full_feature_set(n=50, seed=42)
        y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=50))
        params = {
            'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
            'n_estimators': 50, 'random_state': 42, 'verbosity': -1,
            'objective': 'multiclass', 'num_class': 3,
        }
        weights = np.ones(50)
        result = evaluate_cv(X, y, params, weights, "test")
        self.assertIsInstance(result, dict)
        self.assertIn('accuracy_mean', result)
        self.assertIn('logloss_mean', result)
        self.assertIn('cv_accuracies', result)

    def test_evaluate_accuracy_range(self):
        """CV 准确率应在 [0, 1] 范围内"""
        from d017_feature_selection import evaluate_cv
        X = make_full_feature_set(n=50, seed=42)
        y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=50))
        params = {
            'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
            'n_estimators': 50, 'random_state': 42, 'verbosity': -1,
            'objective': 'multiclass', 'num_class': 3,
        }
        weights = np.ones(50)
        result = evaluate_cv(X, y, params, weights, "test")
        self.assertGreaterEqual(result['accuracy_mean'], 0.0)
        self.assertLessEqual(result['accuracy_mean'], 1.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
