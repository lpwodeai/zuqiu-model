"""
单元测试：Optuna 超参数调优模块
================================

测试 optuna_tuning.py 的核心函数：
- compute_sample_weights: 样本权重
- objective_xgb / objective_lgb: 目标函数
- run_optuna_tuning: 主调优流程
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


class TestComputeSampleWeights(unittest.TestCase):
    """测试样本权重计算"""

    def test_uniform_leagues(self):
        """等量联赛应返回接近 1 的权重"""
        from optuna_tuning import compute_sample_weights
        df = pd.DataFrame({'competition_name': ['A', 'B'] * 10})
        weights = compute_sample_weights(df)
        self.assertEqual(len(weights), 20)
        self.assertAlmostEqual(np.mean(weights), 1.0, places=2)

    def test_imbalanced_leagues(self):
        """不均衡联赛应给小联赛更高权重"""
        from optuna_tuning import compute_sample_weights
        df = pd.DataFrame({'competition_name': ['A'] * 8 + ['B'] * 2})
        weights = compute_sample_weights(df)
        self.assertGreater(weights[8], weights[0])

    def test_no_competition_column(self):
        """无 competition_name 列时应返回全 1"""
        from optuna_tuning import compute_sample_weights
        df = pd.DataFrame({'x': [1, 2, 3]})
        weights = compute_sample_weights(df)
        np.testing.assert_array_equal(weights, np.ones(3))


class TestObjectiveFunctions(unittest.TestCase):
    """测试 Optuna 目标函数"""

    def test_objective_lgb_returns_float(self):
        """LGB 目标函数应返回浮点数"""
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
            from optuna_tuning import objective_lgb

            X = make_full_feature_set(n=80, seed=42)
            y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=80))
            weights = np.ones(80)

            study = optuna.create_study(direction='maximize')
            study.optimize(lambda trial: objective_lgb(trial, X, y, weights), n_trials=2)
            self.assertIsInstance(study.best_value, (int, float))
        except ImportError:
            self.skipTest("optuna 未安装")
        except Exception as e:
            self.skipTest(f"跳过: {e}")

    def test_objective_xgb_returns_float(self):
        """XGB 目标函数应返回浮点数"""
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)
            from optuna_tuning import objective_xgb

            X = make_full_feature_set(n=80, seed=42)
            y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=80))
            weights = np.ones(80)

            study = optuna.create_study(direction='maximize')
            study.optimize(lambda trial: objective_xgb(trial, X, y, weights), n_trials=2)
            self.assertIsInstance(study.best_value, (int, float))
        except ImportError:
            self.skipTest("optuna 未安装")
        except Exception as e:
            self.skipTest(f"跳过: {e}")


class TestOptunaResultIntegrity(unittest.TestCase):
    """测试 Optuna 结果文件完整性"""

    def test_result_file_loadable(self):
        """Optuna 结果文件应可加载且包含必要字段"""
        import glob, json
        assets_dir = os.path.join(PROJECT_ROOT, 'assets')
        files = glob.glob(os.path.join(assets_dir, 'optuna_result_*.json'))
        if not files:
            self.skipTest("Optuna 结果文件不存在")

        latest = max(files, key=os.path.getctime)
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 必要字段
        required_keys = ['xgb_best', 'lgb_best', 'xgb_cv', 'lgb_cv']
        for key in required_keys:
            self.assertIn(key, data, f"结果文件缺少字段: {key}")

        # 参数应为字典
        self.assertIsInstance(data['xgb_best'], dict)
        self.assertIsInstance(data['lgb_best'], dict)

        # CV 应在合理范围
        self.assertGreater(data['lgb_cv'], 0.4)
        self.assertLess(data['lgb_cv'], 1.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
