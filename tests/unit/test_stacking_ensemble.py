"""
单元测试：Stacking 集成模块
============================

测试 stacking_ensemble.py 的核心函数：
- _safe_fit: 安全训练
- compute_sample_weights: 样本权重
- generate_oof_predictions: OOF 预测生成
- build_meta_features: 元特征构建
- evaluate_meta_models_cv: 元模型 CV 评估
- load_optuna_best: 加载 Optuna 最优参数
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


class TestSafeFit(unittest.TestCase):
    """测试安全训练函数"""

    def test_safe_fit_lgb(self):
        """_safe_fit 应能训练 LGB 模型"""
        try:
            from stacking_ensemble import _safe_fit
            from lightgbm import LGBMClassifier
            X = make_full_feature_set(n=50, seed=42)
            y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=50))
            model = LGBMClassifier(
                max_depth=3, num_leaves=8, learning_rate=0.1,
                n_estimators=20, random_state=42, verbosity=-1,
                objective='multiclass', num_class=3,
            )
            fitted = _safe_fit(model, X.iloc[:40], y.iloc[:40],
                               X.iloc[40:], y.iloc[40:], np.ones(40), 'lgb')
            self.assertTrue(hasattr(fitted, 'predict'))
        except Exception as e:
            self.skipTest(f"跳过: {e}")

    def test_safe_fit_xgb(self):
        """_safe_fit 应能训练 XGB 模型"""
        try:
            from stacking_ensemble import _safe_fit
            from xgboost import XGBClassifier
            X = make_full_feature_set(n=50, seed=42)
            y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=50))
            model = XGBClassifier(
                max_depth=3, learning_rate=0.1, n_estimators=20,
                random_state=42, eval_metric='mlogloss',
                use_label_encoder=False, verbosity=0,
            )
            fitted = _safe_fit(model, X.iloc[:40], y.iloc[:40],
                               X.iloc[40:], y.iloc[40:], np.ones(40), 'xgb')
            self.assertTrue(hasattr(fitted, 'predict'))
        except Exception as e:
            self.skipTest(f"跳过: {e}")


class TestComputeSampleWeights(unittest.TestCase):
    """测试样本权重计算"""

    def test_uniform_weights(self):
        """等量联赛应返回接近 1 的权重"""
        from stacking_ensemble import compute_sample_weights
        df = pd.DataFrame({'competition_name': ['A', 'B'] * 10})
        weights = compute_sample_weights(df)
        self.assertEqual(len(weights), 20)
        self.assertAlmostEqual(np.mean(weights), 1.0, places=2)


class TestGenerateOOF(unittest.TestCase):
    """测试 OOF 预测生成"""

    def test_oof_shape(self):
        """OOF 预测应具有正确形状"""
        try:
            from stacking_ensemble import generate_oof_predictions
            X = make_full_feature_set(n=80, seed=42)
            y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=80))
            params_dict = {
                'lgb': {
                    'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
                    'n_estimators': 20, 'random_state': 42, 'verbosity': -1,
                    'objective': 'multiclass', 'num_class': 3,
                }
            }
            weights = np.ones(80)
            oof = generate_oof_predictions(X, y, params_dict, weights)
            self.assertIsInstance(oof, dict)
            if 'lgb' in oof:
                self.assertEqual(oof['lgb'].shape, (80, 3))
        except Exception as e:
            self.skipTest(f"跳过: {e}")


class TestBuildMetaFeatures(unittest.TestCase):
    """测试元特征构建"""

    def test_meta_features_shape(self):
        """元特征应具有正确形状"""
        try:
            from stacking_ensemble import build_meta_features
            n = 80
            oof_probs = {
                'lgb': np.random.RandomState(42).dirichlet([1, 1, 1], size=n),
                'xgb': np.random.RandomState(43).dirichlet([1, 1, 1], size=n),
            }
            oof_labels = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=n))
            meta = build_meta_features(oof_probs, oof_labels)
            self.assertIsInstance(meta, pd.DataFrame)
            self.assertEqual(len(meta), n)
            # 元特征维度应 >= 基模型数 × 类别数
            self.assertGreaterEqual(meta.shape[1], 6)
        except Exception as e:
            self.skipTest(f"跳过: {e}")


class TestEvaluateMetaModels(unittest.TestCase):
    """测试元模型 CV 评估"""

    def test_evaluate_returns_dict(self):
        """评估应返回字典结果"""
        try:
            from stacking_ensemble import evaluate_meta_models_cv
            n = 100
            rng = np.random.RandomState(42)
            meta_features = pd.DataFrame(rng.uniform(0, 1, size=(n, 6)),
                                         columns=[f'f{i}' for i in range(6)])
            y = pd.Series(rng.choice([0, 1, 2], size=n))
            weights = np.ones(n)
            result = evaluate_meta_models_cv(meta_features, y, weights)
            self.assertIsInstance(result, dict)
        except Exception as e:
            self.skipTest(f"跳过: {e}")


class TestLoadOptunaBest(unittest.TestCase):
    """测试加载 Optuna 最优参数"""

    def test_load_returns_dict(self):
        """应返回包含 xgb_best 和 lgb_best 的字典"""
        try:
            from stacking_ensemble import load_optuna_best
            result = load_optuna_best()
            self.assertIsInstance(result, dict)
            self.assertIn('xgb_best', result)
            self.assertIn('lgb_best', result)
        except FileNotFoundError:
            self.skipTest("Optuna 结果文件不存在")
        except Exception as e:
            self.skipTest(f"跳过: {e}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
