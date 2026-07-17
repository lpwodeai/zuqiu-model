import os
import sys
import unittest
import numpy as np
import pandas as pd
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(PROJECT_ROOT, 'scripts'))

from auto_train import update_feedback_signal, get_anomaly_stats


class TestIntegration(unittest.TestCase):
    
    def test_empty_inputs(self):
        """测试空输入时的处理"""
        result = update_feedback_signal(None, None)
        self.assertEqual(result['total_matches'], 0)
        self.assertEqual(result['anomaly_count'], 0)
        self.assertEqual(result['anomaly_rate'], 0.0)
        
        result = update_feedback_signal(pd.DataFrame(), np.array([]))
        self.assertEqual(result['total_matches'], 0)
        self.assertEqual(result['anomaly_count'], 0)
    
    def test_mismatched_lengths(self):
        """测试长度不匹配时的处理"""
        match_data = pd.DataFrame({'home_team_name': ['Team A'], 'away_team_name': ['Team B']})
        model_predictions = np.array([[0.3, 0.3, 0.4], [0.2, 0.4, 0.4]])
        
        result = update_feedback_signal(match_data, model_predictions)
        self.assertEqual(result['total_matches'], 0)
    
    def test_odds_data_mismatch(self):
        """测试odds_data长度不匹配时的处理"""
        match_data = pd.DataFrame({
            'home_team_name': ['Team A'],
            'away_team_name': ['Team B'],
            'result': [2]
        })
        model_predictions = np.array([[0.3, 0.3, 0.4]])
        odds_data = [[0.3, 0.3, 0.4], [0.2, 0.4, 0.4]]
        
        result = update_feedback_signal(match_data, model_predictions, odds_data)
        self.assertEqual(result['total_matches'], 1)
    
    def test_valid_input_with_anomaly(self):
        """测试包含异常样本的有效输入"""
        match_data = pd.DataFrame({
            'home_team_name': ['Team A', 'Team B', 'Team C'],
            'away_team_name': ['Team X', 'Team Y', 'Team Z'],
            'date': [datetime(2025, 8, 15), datetime(2025, 8, 16), datetime(2025, 8, 17)],
            'result': [0, 1, 2],
            'homeGoals': [1, 2, 3],
            'awayGoals': [2, 2, 1],
            'competition_name': ['EPL', 'EPL', 'EPL']
        })
        
        model_predictions = np.array([
            [0.05, 0.05, 0.90],
            [0.3, 0.3, 0.4],
            [0.2, 0.4, 0.4]
        ])
        
        odds_data = [
            [0.4, 0.3, 0.3],
            [0.3, 0.3, 0.4],
            [0.2, 0.4, 0.4]
        ]
        
        result = update_feedback_signal(match_data, model_predictions, odds_data)
        
        self.assertEqual(result['total_matches'], 3)
        self.assertGreaterEqual(result['anomaly_count'], 1)
        self.assertGreater(result['anomaly_rate'], 0.0)
    
    def test_valid_input_no_anomaly(self):
        """测试无异常样本的有效输入"""
        match_data = pd.DataFrame({
            'home_team_name': ['Team A', 'Team B'],
            'away_team_name': ['Team X', 'Team Y'],
            'date': [datetime(2025, 8, 15), datetime(2025, 8, 16)],
            'result': [2, 1],
            'homeGoals': [2, 1],
            'awayGoals': [1, 1],
            'competition_name': ['EPL', 'EPL']
        })
        
        model_predictions = np.array([
            [0.2, 0.3, 0.5],
            [0.25, 0.5, 0.25]
        ])
        
        odds_data = [
            [0.25, 0.35, 0.4],
            [0.25, 0.5, 0.25]
        ]
        
        result = update_feedback_signal(match_data, model_predictions, odds_data)
        
        self.assertEqual(result['total_matches'], 2)
        self.assertEqual(result['anomaly_count'], 0)
        self.assertEqual(result['anomaly_rate'], 0.0)
    
    def test_get_anomaly_stats(self):
        """测试获取异常统计"""
        stats = get_anomaly_stats()
        
        self.assertIn('total_anomalies', stats)
        self.assertIn('type_distribution', stats)
        self.assertIn('score_stats', stats)
        self.assertIn('latest_date', stats)
        
        self.assertIsInstance(stats['total_anomalies'], int)
        self.assertIsInstance(stats['type_distribution'], dict)
        self.assertIsInstance(stats['score_stats'], dict)


if __name__ == '__main__':
    unittest.main()
