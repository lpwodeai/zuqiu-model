"""
端到端测试：预测 Pipeline
==========================

测试从原始数据到最终预测的完整流程：
- 数据加载 → 特征构建 → 模型训练 → 预测输出
- 验证预测结果的格式和范围
- 验证模型资产的完整性
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


class TestPredictPipelineE2E(unittest.TestCase):
    """预测 Pipeline 端到端测试"""

    def test_full_pipeline_synthetic(self):
        """合成数据完整 Pipeline 测试"""
        from lightgbm import LGBMClassifier
        X = make_full_feature_set(n=100, seed=42)
        y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=100))

        # 训练
        params = {
            'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
            'n_estimators': 50, 'random_state': 42, 'verbosity': -1,
            'objective': 'multiclass', 'num_class': 3,
        }
        model = LGBMClassifier(**params)
        model.fit(X.iloc[:80], y.iloc[:80])

        # 预测
        X_test = X.iloc[80:]
        preds = model.predict(X_test)
        proba = model.predict_proba(X_test)

        # 验证预测格式
        self.assertEqual(len(preds), 20)
        self.assertTrue(all(p in [0, 1, 2] for p in preds))

        # 验证概率格式
        self.assertEqual(proba.shape, (20, 3))
        np.testing.assert_array_almost_equal(proba.sum(axis=1), np.ones(20), decimal=5)

        # 概率应在 [0, 1]
        self.assertTrue(np.all(proba >= 0))
        self.assertTrue(np.all(proba <= 1))


class TestModelAssetsE2E(unittest.TestCase):
    """模型资产完整性测试"""

    def test_optuna_result_exists(self):
        """Optuna 结果文件应存在"""
        assets_dir = os.path.join(PROJECT_ROOT, 'assets')
        import glob
        files = glob.glob(os.path.join(assets_dir, 'optuna_result_*.json'))
        if not files:
            self.skipTest("Optuna 结果文件不存在")
        # 验证最新文件可读
        latest = max(files, key=os.path.getctime)
        import json
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertIn('xgb_best', data)
        self.assertIn('lgb_best', data)
        self.assertIn('xgb_cv', data)
        self.assertIn('lgb_cv', data)

    def test_d017_features_exist(self):
        """D-017 特征精简结果应存在"""
        assets_dir = os.path.join(PROJECT_ROOT, 'assets')
        import glob
        files = glob.glob(os.path.join(assets_dir, 'd017_features_*.json'))
        if not files:
            self.skipTest("D-017 结果文件不存在")
        latest = max(files, key=os.path.getctime)
        import json
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertIn('final', data)
        self.assertIn('features', data['final'])
        self.assertIn('accuracy', data['final'])

    def test_d016_result_exists(self):
        """D-016 概率校准结果应存在"""
        assets_dir = os.path.join(PROJECT_ROOT, 'assets')
        import glob
        files = glob.glob(os.path.join(assets_dir, 'd016_result_*.json'))
        if not files:
            self.skipTest("D-016 结果文件不存在")
        latest = max(files, key=os.path.getctime)
        import json
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.assertIn('baseline_lgb', data)
        self.assertIn('best_name', data)


class TestRegressionE2E(unittest.TestCase):
    """回归测试：确保模型性能不退化"""

    def test_lgb_cv_above_baseline(self):
        """LGB CV 应高于 47% 基线"""
        assets_dir = os.path.join(PROJECT_ROOT, 'assets')
        import glob, json
        files = glob.glob(os.path.join(assets_dir, 'optuna_result_*.json'))
        if not files:
            self.skipTest("Optuna 结果不存在")
        latest = max(files, key=os.path.getctime)
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        lgb_cv = data.get('lgb_cv', 0)
        # 基线为 47.22%，CV 应高于此
        self.assertGreater(lgb_cv, 0.47,
                           f"LGB CV {lgb_cv:.4f} 低于基线 0.4722，性能退化")

    def test_d017_cv_above_baseline(self):
        """D-017 60维 CV 应高于 48%"""
        assets_dir = os.path.join(PROJECT_ROOT, 'assets')
        import glob, json
        files = glob.glob(os.path.join(assets_dir, 'd017_features_*.json'))
        if not files:
            self.skipTest("D-017 结果不存在")
        latest = max(files, key=os.path.getctime)
        with open(latest, 'r', encoding='utf-8') as f:
            data = json.load(f)
        cv = data['final']['accuracy']
        self.assertGreater(cv, 0.48,
                           f"D-017 CV {cv:.4f} 低于 0.48，性能退化")


if __name__ == '__main__':
    unittest.main(verbosity=2)
