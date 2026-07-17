import os
import sys
import unittest
import numpy as np
import pandas as pd
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(PROJECT_ROOT, 'scripts'))

from auto_train import (
    calculate_prediction_error,
    calculate_model_odds_divergence,
    detect_anomaly,
    ANOMALY_THRESHOLD,
    DIVERGENCE_THRESHOLD,
    ERROR_THRESHOLD
)


class TestPredictionError(unittest.TestCase):
    
    def test_correct_prediction(self):
        """测试正确预测时误差为0"""
        model_probs = [0.1, 0.2, 0.7]
        self.assertAlmostEqual(calculate_prediction_error(model_probs, 2), 0.3, places=10)
        
        model_probs = [0.6, 0.2, 0.2]
        self.assertAlmostEqual(calculate_prediction_error(model_probs, 0), 0.4, places=10)
        
        model_probs = [0.2, 0.6, 0.2]
        self.assertAlmostEqual(calculate_prediction_error(model_probs, 1), 0.4, places=10)
    
    def test_wrong_prediction(self):
        """测试错误预测时误差较大"""
        model_probs = [0.1, 0.2, 0.7]
        self.assertEqual(calculate_prediction_error(model_probs, 0), 0.9)
        
        model_probs = [0.1, 0.2, 0.7]
        self.assertEqual(calculate_prediction_error(model_probs, 1), 0.8)
    
    def test_edge_cases(self):
        """测试边界情况"""
        model_probs = [1.0, 0.0, 0.0]
        self.assertEqual(calculate_prediction_error(model_probs, 0), 0.0)
        self.assertEqual(calculate_prediction_error(model_probs, 1), 1.0)
        self.assertEqual(calculate_prediction_error(model_probs, 2), 1.0)
        
        model_probs = [0.33, 0.34, 0.33]
        self.assertAlmostEqual(calculate_prediction_error(model_probs, 0), 0.67, places=2)
        
        model_probs = [0.33, 0.34, 0.33]
        self.assertAlmostEqual(calculate_prediction_error(model_probs, 1), 0.66, places=2)
        
        model_probs = [0.33, 0.34, 0.33]
        self.assertAlmostEqual(calculate_prediction_error(model_probs, 2), 0.67, places=2)


class TestDivergence(unittest.TestCase):
    
    def test_same_distribution(self):
        """测试相同分布时KL散度为0"""
        model_probs = [0.3, 0.4, 0.3]
        odds_probs = [0.3, 0.4, 0.3]
        self.assertAlmostEqual(calculate_model_odds_divergence(model_probs, odds_probs), 0.0, places=5)
    
    def test_different_distribution(self):
        """测试不同分布时有正的KL散度"""
        model_probs = [0.2, 0.3, 0.5]
        odds_probs = [0.5, 0.3, 0.2]
        divergence = calculate_model_odds_divergence(model_probs, odds_probs)
        self.assertTrue(divergence > 0)
    
    def test_extreme_divergence(self):
        """测试极端分歧情况"""
        model_probs = [0.01, 0.01, 0.98]
        odds_probs = [0.98, 0.01, 0.01]
        divergence = calculate_model_odds_divergence(model_probs, odds_probs)
        self.assertTrue(divergence > 1.0)


class TestAnomalyDetection(unittest.TestCase):
    
    def test_high_confidence_error(self):
        """测试高置信度错误"""
        model_probs = [0.05, 0.05, 0.90]
        odds_probs = [0.05, 0.05, 0.90]
        actual_result = 0
        prediction_error = calculate_prediction_error(model_probs, actual_result)
        
        anomaly_type, anomaly_score, divergence = detect_anomaly(
            model_probs, odds_probs, actual_result, prediction_error
        )
        
        self.assertEqual(anomaly_type, 'high_confidence_error')
        self.assertTrue(anomaly_score > ANOMALY_THRESHOLD)
    
    def test_model_odds_conflict(self):
        """测试模型与赔率冲突"""
        model_probs = [0.1, 0.2, 0.7]
        odds_probs = [0.7, 0.2, 0.1]
        actual_result = 0
        prediction_error = calculate_prediction_error(model_probs, actual_result)
        
        anomaly_type, anomaly_score, divergence = detect_anomaly(
            model_probs, odds_probs, actual_result, prediction_error
        )
        
        self.assertEqual(anomaly_type, 'model_odds_conflict')
        self.assertTrue(anomaly_score > ANOMALY_THRESHOLD)
    
    def test_no_anomaly(self):
        """测试无异常情况"""
        model_probs = [0.2, 0.3, 0.5]
        odds_probs = [0.25, 0.35, 0.40]
        actual_result = 2
        prediction_error = calculate_prediction_error(model_probs, actual_result)
        
        anomaly_type, anomaly_score, divergence = detect_anomaly(
            model_probs, odds_probs, actual_result, prediction_error
        )
        
        self.assertIsNone(anomaly_type)
        self.assertEqual(anomaly_score, 0.0)
    
    def test_market_disagreement(self):
        """测试市场分歧"""
        model_probs = [0.05, 0.05, 0.90]
        odds_probs = [0.40, 0.30, 0.30]
        actual_result = 2
        prediction_error = calculate_prediction_error(model_probs, actual_result)
        
        anomaly_type, anomaly_score, divergence = detect_anomaly(
            model_probs, odds_probs, actual_result, prediction_error
        )
        
        self.assertEqual(anomaly_type, 'market_disagreement')
        self.assertTrue(anomaly_score > ANOMALY_THRESHOLD)
    
    def test_probability_mismatch(self):
        """测试概率不匹配"""
        model_probs = [0.15, 0.35, 0.50]
        odds_probs = [0.25, 0.35, 0.40]
        actual_result = 2
        prediction_error = calculate_prediction_error(model_probs, actual_result)
        
        anomaly_type, anomaly_score, divergence = detect_anomaly(
            model_probs, odds_probs, actual_result, prediction_error
        )
        
        self.assertIsNone(anomaly_type)


class TestConstants(unittest.TestCase):
    
    def test_constants_defined(self):
        """测试常量定义正确"""
        self.assertEqual(ANOMALY_THRESHOLD, 0.5)
        self.assertEqual(DIVERGENCE_THRESHOLD, 0.5)
        self.assertEqual(ERROR_THRESHOLD, 0.8)
    
    def test_constants_ranges(self):
        """测试常量在合理范围内"""
        self.assertTrue(0 < ANOMALY_THRESHOLD < 1)
        self.assertTrue(0 < DIVERGENCE_THRESHOLD < 1)
        self.assertTrue(0 < ERROR_THRESHOLD < 1)


if __name__ == '__main__':
    unittest.main()
