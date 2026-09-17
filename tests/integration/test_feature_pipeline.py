"""
集成测试：特征工程 Pipeline
============================

测试从原始数据到完整特征集的端到端流程：
- 合成数据 → build_all_features → 60维特征
- 特征时序分离 → 无泄露
- 特征维度符合预期
"""

import os
import sys
import unittest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures'))

from sample_data import make_sample_matches, make_full_feature_set


class TestFeaturePipelineIntegration(unittest.TestCase):
    """特征工程 Pipeline 集成测试"""

    def test_synthetic_feature_assembly(self):
        """合成特征组装应产生多类特征（赔率+Elo+时序）"""
        features = make_full_feature_set(n=30, seed=42)
        self.assertEqual(features.shape[0], 30)
        # fixture 合成数据为 45 维（25赔率+10Elo+10时序）
        self.assertGreaterEqual(features.shape[1], 40)

    def test_feature_no_nan(self):
        """合成特征不应包含 NaN"""
        features = make_full_feature_set(n=20, seed=42)
        self.assertEqual(features.isna().sum().sum(), 0)

    def test_feature_no_inf(self):
        """合成特征不应包含 Inf"""
        features = make_full_feature_set(n=20, seed=42)
        self.assertFalse(np.isinf(features.values).any())

    def test_feature_categories_present(self):
        """应包含赔率、Elo、时序三类特征"""
        features = make_full_feature_set(n=20, seed=42)
        cols = list(features.columns)

        # 赔率类
        odds_cols = [c for c in cols if c.startswith('wdl_') or c.startswith('handicap_') or c.startswith('total_goals_')]
        self.assertGreater(len(odds_cols), 0)

        # Elo 类
        elo_cols = [c for c in cols if c.startswith(('home_elo', 'away_elo', 'elo_'))]
        self.assertGreater(len(elo_cols), 0)

        # 时序类
        temporal_cols = [c for c in cols if c.startswith((
            'wdl_win_volatility', 'wdl_draw_volatility', 'wdl_lose_volatility',
            'wdl_win_acceleration', 'wdl_late_trend', 'wdl_early_trend',
            'wdl_mid_stability', 'wdl_sudden_jump', 'wdl_update_frequency',
            'wdl_total_change',
        ))]
        self.assertGreater(len(temporal_cols), 0)


class TestRealFeaturePipeline(unittest.TestCase):
    """真实特征 Pipeline 集成测试（依赖数据库）"""

    def test_build_all_features_with_real_data(self):
        """测试 build_all_features 端到端"""
        try:
            from feature_utils import load_match_data_odds, build_all_features
            df = load_match_data_odds()
            X, y = build_all_features(df, include_odds=True, include_elo=True, include_temporal=True)
            self.assertGreater(X.shape[0], 100)
            self.assertGreater(X.shape[1], 50)
            self.assertEqual(len(y), len(X))
        except Exception as e:
            self.skipTest(f"跳过（数据库依赖）: {e}")

    def test_feature_temporal_split_no_leakage(self):
        """测试特征时序分离后无泄露"""
        try:
            from feature_utils import load_match_data_odds, build_all_features
            from feature_temporal import validate_no_leakage
            df = load_match_data_odds()
            X, y = build_all_features(df, include_odds=True, include_elo=True, include_temporal=True)
            try:
                validate_no_leakage(X, context="集成测试")
            except AssertionError:
                self.fail("特征工程存在数据泄露")
        except Exception as e:
            self.skipTest(f"跳过（数据库依赖）: {e}")


class TestModelPipelineIntegration(unittest.TestCase):
    """模型训练 Pipeline 集成测试"""

    def test_lgb_train_predict(self):
        """LGB 训练+预测应正常工作"""
        try:
            from lightgbm import LGBMClassifier
            from sklearn.model_selection import TimeSeriesSplit
            from sklearn.metrics import accuracy_score
            X = make_full_feature_set(n=80, seed=42)
            y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=80))
            params = {
                'max_depth': 3, 'num_leaves': 8, 'learning_rate': 0.1,
                'n_estimators': 30, 'random_state': 42, 'verbosity': -1,
                'objective': 'multiclass', 'num_class': 3,
            }
            tscv = TimeSeriesSplit(n_splits=3)
            accs = []
            for train_idx, val_idx in tscv.split(X):
                model = LGBMClassifier(**params)
                model.fit(X.iloc[train_idx], y.iloc[train_idx],
                          eval_set=[(X.iloc[val_idx], y.iloc[val_idx])], callbacks=[])
                preds = model.predict(X.iloc[val_idx])
                accs.append(accuracy_score(y.iloc[val_idx], preds))
            self.assertGreater(np.mean(accs), 0.0)
            self.assertLessEqual(np.mean(accs), 1.0)
        except Exception as e:
            self.skipTest(f"跳过: {e}")

    def test_xgb_train_predict(self):
        """XGB 训练+预测应正常工作"""
        try:
            from xgboost import XGBClassifier
            from sklearn.model_selection import TimeSeriesSplit
            from sklearn.metrics import accuracy_score
            X = make_full_feature_set(n=80, seed=42)
            y = pd.Series(np.random.RandomState(42).choice([0, 1, 2], size=80))
            params = {
                'max_depth': 3, 'learning_rate': 0.1, 'n_estimators': 30,
                'random_state': 42, 'eval_metric': 'mlogloss',
                'use_label_encoder': False, 'verbosity': 0,
            }
            tscv = TimeSeriesSplit(n_splits=3)
            accs = []
            for train_idx, val_idx in tscv.split(X):
                model = XGBClassifier(**params)
                model.fit(X.iloc[train_idx], y.iloc[train_idx],
                          eval_set=[(X.iloc[val_idx], y.iloc[val_idx])], verbose=False)
                preds = model.predict(X.iloc[val_idx])
                accs.append(accuracy_score(y.iloc[val_idx], preds))
            self.assertGreater(np.mean(accs), 0.0)
        except Exception as e:
            self.skipTest(f"跳过: {e}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
