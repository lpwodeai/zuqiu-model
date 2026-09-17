"""
单元测试：D-016 概率校准模块
=============================

测试 d016_calibration.py 的辅助函数：
- compute_multiclass_brier: 多分类 Brier score
- compute_sample_weights: 样本权重
- evaluate_uncalibrated_cv / evaluate_calibrated_cv: CV 评估
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures'))

from sample_data import make_full_feature_set


class TestMulticlassBrier(unittest.TestCase):
    """测试多分类 Brier score 计算"""

    def test_perfect_prediction(self):
        """完美预测的 Brier score 应接近 0"""
        from d016_calibration import compute_multiclass_brier
        y_true = np.array([0, 1, 2])
        y_proba = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ])
        brier = compute_multiclass_brier(y_true, y_proba, n_classes=3)
        self.assertAlmostEqual(brier, 0.0, places=6)

    def test_uniform_prediction(self):
        """均匀预测的 Brier score 应较高"""
        from d016_calibration import compute_multiclass_brier
        y_true = np.array([0, 1, 2])
        y_proba = np.array([
            [1/3, 1/3, 1/3],
            [1/3, 1/3, 1/3],
            [1/3, 1/3, 1/3],
        ])
        brier = compute_multiclass_brier(y_true, y_proba, n_classes=3)
        self.assertGreater(brier, 0.1)

    def test_brier_range(self):
        """Brier score 应在 [0, 1] 范围内"""
        from d016_calibration import compute_multiclass_brier
        rng = np.random.RandomState(42)
        for _ in range(10):
            n = 20
            y_true = rng.choice([0, 1, 2], size=n)
            y_proba = rng.dirichlet([1, 1, 1], size=n)
            brier = compute_multiclass_brier(y_true, y_proba, n_classes=3)
            self.assertGreaterEqual(brier, 0.0)
            self.assertLessEqual(brier, 1.0)

    def test_non_negative(self):
        """Brier score 应非负"""
        from d016_calibration import compute_multiclass_brier
        rng = np.random.RandomState(42)
        y_true = rng.choice([0, 1, 2], size=30)
        y_proba = rng.dirichlet([2, 2, 2], size=30)
        self.assertGreaterEqual(compute_multiclass_brier(y_true, y_proba), 0)


class TestComputeSampleWeights(unittest.TestCase):
    """测试样本权重计算"""

    def test_uniform_weights(self):
        """等量联赛应返回接近 1 的权重"""
        from d016_calibration import compute_sample_weights
        df = pd.DataFrame({'competition_name': ['A', 'B'] * 10})
        weights = compute_sample_weights(df)
        self.assertEqual(len(weights), 20)
        self.assertAlmostEqual(np.mean(weights), 1.0, places=2)

    def test_missing_column(self):
        """无 competition_name 列时应返回全 1"""
        from d016_calibration import compute_sample_weights
        df = pd.DataFrame({'x': [1, 2, 3]})
        weights = compute_sample_weights(df)
        np.testing.assert_array_equal(weights, np.ones(3))


class TestEvaluateUncalibratedCV(unittest.TestCase):
    """测试未校准 CV 评估"""

    def test_returns_dict(self):
        """应返回字典结果"""
        from d016_calibration import evaluate_uncalibrated_cv
        X = make_full_feature_set(n=150, seed=42)
        y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=150))
        params = {
            'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
            'n_estimators': 30, 'random_state': 42, 'verbosity': -1,
            'objective': 'multiclass', 'num_class': 3,
        }
        weights = np.ones(150)
        result = evaluate_uncalibrated_cv(X, y, params, weights, 'lgb')
        self.assertIn('accuracy_mean', result)
        self.assertIn('logloss_mean', result)
        self.assertIn('brier_mean', result)

    def test_accuracy_in_range(self):
        """准确率应在 [0, 1]"""
        from d016_calibration import evaluate_uncalibrated_cv
        X = make_full_feature_set(n=150, seed=42)
        y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=150))
        params = {
            'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
            'n_estimators': 30, 'random_state': 42, 'verbosity': -1,
            'objective': 'multiclass', 'num_class': 3,
        }
        weights = np.ones(150)
        result = evaluate_uncalibrated_cv(X, y, params, weights, 'lgb')
        self.assertGreaterEqual(result['accuracy_mean'], 0.0)
        self.assertLessEqual(result['accuracy_mean'], 1.0)


class TestEvaluateCalibratedCV(unittest.TestCase):
    """测试校准 CV 评估（需较大样本量避免 CalibratedClassifierCV 内层 CV 样本不足）"""

    def test_platt_returns_dict(self):
        """Platt 校准应返回字典结果"""
        from d016_calibration import evaluate_calibrated_cv
        X = make_full_feature_set(n=150, seed=42)
        y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=150))
        params = {
            'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
            'n_estimators': 30, 'random_state': 42, 'verbosity': -1,
            'objective': 'multiclass', 'num_class': 3,
        }
        weights = np.ones(150)
        result = evaluate_calibrated_cv(X, y, params, weights, 'sigmoid', 'lgb')
        self.assertIn('accuracy_mean', result)
        self.assertIn('logloss_mean', result)
        self.assertIn('brier_mean', result)

    def test_isotonic_returns_dict(self):
        """Isotonic 校准应返回字典结果"""
        from d016_calibration import evaluate_calibrated_cv
        X = make_full_feature_set(n=150, seed=42)
        y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=150))
        params = {
            'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
            'n_estimators': 30, 'random_state': 42, 'verbosity': -1,
            'objective': 'multiclass', 'num_class': 3,
        }
        weights = np.ones(150)
        result = evaluate_calibrated_cv(X, y, params, weights, 'isotonic', 'lgb')
        self.assertIn('accuracy_mean', result)


if __name__ == '__main__':
    unittest.main(verbosity=2)
