import sqlite3
import pandas as pd
import numpy as np
import json
import os
import sys
import joblib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from odds_temporal_features import (
    build_odds_temporal_features, load_wdl_history, load_handicap_history,
    load_total_goals_history, load_score_history, analyze_wdl_trend,
    analyze_handicap_trend, analyze_total_goals_trend, analyze_score_trend
)
from score_prediction_module import analyze_match_score, predict_score_distribution
from feature_utils import build_features, build_team_features, build_odds_features, load_match_data, load_match_data_odds
from stacking_classifier import StackingClassifier
from team_name_mapping import normalize_team_name
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
MODEL_DIR = BASE_DIR / "assets"

class UnifiedBacktestFramework:
    def __init__(self, use_ml_model=True, use_odds_analysis=True, use_score_prediction=True, use_multitask=False):
        self.use_ml_model = use_ml_model
        self.use_odds_analysis = use_odds_analysis
        self.use_score_prediction = use_score_prediction
        self.use_multitask = use_multitask
        self.ml_model = None
        self.multitask_models = {}
        self.scaler = None
        self.selected_features = None
        self.model_report = None
        self.conn = None
        self.module_stats = {
            'ml': {'correct': 0, 'total': 0, 'accuracy': 0.55},
            'odds': {'correct': 0, 'total': 0, 'accuracy': 0.30},
            'score': {'correct': 0, 'total': 0, 'accuracy': 0.15}
        }
    
    def connect(self):
        self.conn = sqlite3.connect(DB_PATH)
    
    def disconnect(self):
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def _create_error_tables(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS error_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT,
                home_team TEXT,
                away_team TEXT,
                match_date TEXT,
                actual_wdl TEXT,
                actual_score TEXT,
                ml_prediction TEXT,
                ml_probabilities TEXT,
                ml_covered BOOLEAN,
                odds_prediction TEXT,
                odds_probabilities TEXT,
                odds_covered BOOLEAN,
                score_prediction TEXT,
                score_top1_score TEXT,
                score_top1_prob REAL,
                score_covered BOOLEAN,
                final_prediction TEXT,
                prediction_correct BOOLEAN,
                upset_risk_score REAL,
                error_type TEXT,
                scenario TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS module_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module_name TEXT,
                scenario TEXT,
                correct_count INTEGER DEFAULT 0,
                total_count INTEGER DEFAULT 0,
                accuracy REAL DEFAULT 0,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
    
    def _record_error_case(self, result):
        match_info = result.get('match_info', {})
        ml_pred = result.get('ml_prediction', {}) or {}
        odds_analysis = result.get('odds_analysis', {}) or {}
        score_pred = result.get('score_prediction', {}) or {}
        
        ml_prediction = ml_pred.get('prediction')
        ml_probabilities = json.dumps(ml_pred.get('probabilities', {}))
        ml_covered = ml_prediction is not None
        
        odds_prediction = odds_analysis.get('wdl', {}).get('signal_label')
        odds_probs = {
            '胜': odds_analysis.get('wdl', {}).get('features', {}).get('wdl_implied_prob_home', 0),
            '平': odds_analysis.get('wdl', {}).get('features', {}).get('wdl_implied_prob_draw', 0),
            '负': odds_analysis.get('wdl', {}).get('features', {}).get('wdl_implied_prob_away', 0)
        }
        odds_probabilities = json.dumps(odds_probs)
        odds_covered = odds_prediction in ['胜', '平', '负']
        
        score_prediction = score_pred.get('top1_wdl')
        score_top1_score = score_pred.get('top1_score')
        score_top1_prob = score_pred.get('top1_probability', 0)
        score_covered = score_top1_score is not None
        
        final_prediction = result.get('final_prediction')
        actual_wdl = match_info.get('actual_wdl')
        prediction_correct = final_prediction == actual_wdl if final_prediction and actual_wdl else None
        upset_risk_score = result.get('ml_prediction', {}).get('upset_risk_score', 0)
        
        error_type = 'CORRECT' if prediction_correct else 'ERROR'
        
        scenarios = []
        if ml_covered and odds_covered:
            if ml_prediction == odds_prediction:
                scenarios.append('一致预测')
            else:
                scenarios.append('分歧预测')
        if not ml_covered:
            scenarios.append('ML未覆盖')
        if not odds_covered:
            scenarios.append('赔率未覆盖')
        if upset_risk_score > 0.3:
            scenarios.append('高冷门风险')
        
        scenario = '|'.join(scenarios) if scenarios else '普通'
        
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO error_cases (
                match_id, home_team, away_team, match_date, actual_wdl, actual_score,
                ml_prediction, ml_probabilities, ml_covered,
                odds_prediction, odds_probabilities, odds_covered,
                score_prediction, score_top1_score, score_top1_prob, score_covered,
                final_prediction, prediction_correct, upset_risk_score,
                error_type, scenario
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.get('match_id'),
            match_info.get('home_team'),
            match_info.get('away_team'),
            match_info.get('match_date'),
            actual_wdl,
            match_info.get('actual_score'),
            ml_prediction,
            ml_probabilities,
            ml_covered,
            odds_prediction,
            odds_probabilities,
            odds_covered,
            score_prediction,
            score_top1_score,
            score_top1_prob,
            score_covered,
            final_prediction,
            prediction_correct,
            upset_risk_score,
            error_type,
            scenario
        ))
        self.conn.commit()
    
    def get_module_performance_by_scenario(self):
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT scenario, 
                   SUM(CASE WHEN ml_covered AND ml_prediction = actual_wdl THEN 1 ELSE 0 END) as ml_correct,
                   SUM(CASE WHEN ml_covered THEN 1 ELSE 0 END) as ml_total,
                   SUM(CASE WHEN odds_covered AND odds_prediction = actual_wdl THEN 1 ELSE 0 END) as odds_correct,
                   SUM(CASE WHEN odds_covered THEN 1 ELSE 0 END) as odds_total,
                   SUM(CASE WHEN score_covered AND score_prediction = actual_wdl THEN 1 ELSE 0 END) as score_correct,
                   SUM(CASE WHEN score_covered THEN 1 ELSE 0 END) as score_total,
                   COUNT(*) as total_matches
            FROM error_cases
            WHERE actual_wdl IS NOT NULL
            GROUP BY scenario
            ORDER BY total_matches DESC
        """)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        results = []
        for row in rows:
            results.append(dict(zip(columns, row)))
        return results
    
    def load_trained_model(self, model_path=None, features_path=None, scaler_path=None):
        if not self.use_ml_model:
            return False
        
        if self.use_multitask:
            return self._load_multitask_models()
        
        try:
            if model_path is None:
                pkl_files = [f for f in os.listdir(MODEL_DIR) if f.startswith('xgb_model_') and f.endswith('.pkl')]
                if pkl_files:
                    latest_model = sorted(pkl_files)[-1]
                    model_path = os.path.join(MODEL_DIR, latest_model)
                    print(f"✓ 找到最新pkl模型: {latest_model}")
                else:
                    model_files = [f for f in os.listdir(MODEL_DIR) if f.startswith('model_report')]
                    if not model_files:
                        print("警告: 未找到训练好的模型报告")
                        return False
                    latest_model = sorted(model_files)[-1]
                    model_path = os.path.join(MODEL_DIR, latest_model)
            
            if model_path.endswith('.pkl'):
                self.ml_model = joblib.load(model_path)
                print(f"✓ 加载模型文件: {os.path.basename(model_path)}")
                self.model_report = None
                
                filename = os.path.basename(model_path).replace('.pkl', '')
                timestamp = '_'.join(filename.split('_')[2:])
                scaler_path = os.path.join(MODEL_DIR, f'scaler_{timestamp}.pkl')
                features_path = os.path.join(MODEL_DIR, f'selected_features_{timestamp}.pkl')
                
                if os.path.exists(scaler_path):
                    self.scaler = joblib.load(scaler_path)
                    print(f"✓ 加载标准化器: {os.path.basename(scaler_path)}")
                
                if os.path.exists(features_path):
                    self.selected_features = joblib.load(features_path)
                    print(f"✓ 加载选中特征列表: {len(self.selected_features)} 个特征")
            else:
                with open(model_path, 'r', encoding='utf-8') as f:
                    self.model_report = json.load(f)
                print(f"✓ 加载模型报告: {model_path}")
                
                model_files_info = self.model_report.get('model_files', {})
                if model_files_info:
                    if 'ensemble' in model_files_info:
                        self.ml_model = joblib.load(model_files_info['ensemble'])
                        print(f"✓ 加载集成模型: {os.path.basename(model_files_info['ensemble'])}")
                    elif 'xgb' in model_files_info:
                        self.ml_model = joblib.load(model_files_info['xgb'])
                        print(f"✓ 加载XGBoost模型: {os.path.basename(model_files_info['xgb'])}")
            
            if scaler_path and self.scaler is None:
                self.scaler = joblib.load(scaler_path)
                print(f"✓ 加载标准化器: {os.path.basename(scaler_path)}")
            elif self.model_report and 'model_files' in self.model_report and self.scaler is None:
                if 'scaler' in self.model_report['model_files']:
                    self.scaler = joblib.load(self.model_report['model_files']['scaler'])
                    print(f"✓ 加载标准化器: {os.path.basename(self.model_report['model_files']['scaler'])}")
            
            if features_path and self.selected_features is None:
                self.selected_features = joblib.load(features_path)
                print(f"✓ 加载选中特征列表: {len(self.selected_features)} 个特征")
            elif self.model_report and self.selected_features is None:
                if 'model_files' in self.model_report and 'selected_features' in self.model_report['model_files']:
                    self.selected_features = joblib.load(self.model_report['model_files']['selected_features'])
                    print(f"✓ 加载选中特征列表: {len(self.selected_features)} 个特征")
                elif 'selected_features' in self.model_report:
                    self.selected_features = self.model_report['selected_features']
                    print(f"✓ 从模型报告加载选中特征列表: {len(self.selected_features)} 个特征")
            
            print(f"\n模型加载完成:")
            print(f"  - 模型对象: {'已加载' if self.ml_model else '未加载'}")
            print(f"  - 标准化器: {'已加载' if self.scaler else '未加载'}")
            print(f"  - 特征列表: {'已加载 (' + str(len(self.selected_features)) + '个)' if self.selected_features else '未加载'}")
            
            return True
        except Exception as e:
            print(f"✗ 加载模型失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _load_multitask_models(self):
        try:
            multitask_files = [f for f in os.listdir(MODEL_DIR) if f.startswith('multitask_') and f.endswith('.pkl')]
            if not multitask_files:
                print("警告: 未找到多任务学习模型")
                return False
            
            timestamps = []
            for f in multitask_files:
                parts = f.replace('.pkl', '').split('_')
                if len(parts) >= 3:
                    timestamps.append('_'.join(parts[-2:]))
            
            timestamps = sorted(set(timestamps))
            if not timestamps:
                print("警告: 无法提取多任务模型版本")
                return False
            
            latest_version = timestamps[-1]
            print(f"✓ 找到最新多任务模型版本: {latest_version}")
            
            task_names = ['wdl', 'total_goals', 'goal_diff']
            for task_name in task_names:
                model_path = os.path.join(MODEL_DIR, f'multitask_{task_name}_{latest_version}.pkl')
                if os.path.exists(model_path):
                    self.multitask_models[task_name] = joblib.load(model_path)
                    print(f"✓ 加载多任务 {task_name} 模型: {os.path.basename(model_path)}")
            
            scaler_path = os.path.join(MODEL_DIR, f'multitask_scaler_{latest_version}.pkl')
            if os.path.exists(scaler_path):
                self.scaler = joblib.load(scaler_path)
                print(f"✓ 加载多任务标准化器: {os.path.basename(scaler_path)}")
            
            features_path = os.path.join(MODEL_DIR, f'multitask_features_{latest_version}.pkl')
            if os.path.exists(features_path):
                self.selected_features = joblib.load(features_path)
                print(f"✓ 加载多任务特征列表: {len(self.selected_features)} 个特征")
            
            config_path = os.path.join(MODEL_DIR, f'multitask_config_{latest_version}.json')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.multitask_config = json.load(f)
                print(f"✓ 加载多任务配置")
            
            print(f"\n多任务模型加载完成:")
            print(f"  - 加载任务: {list(self.multitask_models.keys())}")
            print(f"  - 标准化器: {'已加载' if self.scaler else '未加载'}")
            print(f"  - 特征列表: {'已加载 (' + str(len(self.selected_features)) + '个)' if self.selected_features else '未加载'}")
            
            return len(self.multitask_models) > 0
        except Exception as e:
            print(f"✗ 加载多任务模型失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def run_single_match_analysis(self, match_id):
        if not self.conn:
            self.connect()
        
        result = {
            'match_id': match_id,
            'ml_prediction': None,
            'odds_analysis': {},
            'score_prediction': None,
            'final_prediction': None,
            'confidence': 0
        }
        
        try:
            cursor = self.conn.cursor()
            cursor.execute("PRAGMA table_info(matches)")
            columns = cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            cursor.execute('''
                SELECT home_team, away_team, actual_wdl, actual_handicap, 
                       actual_score, actual_total_goals, match_date
                FROM matches WHERE match_id = ?
            ''', (match_id,))
            match_info = cursor.fetchone()
            
            if not match_info:
                print(f"警告: matches表中未找到比赛 {match_id}，尝试从match_id中提取信息")
                actual_wdl = None
                actual_handicap = None
                actual_score = None
                actual_total_goals = None
                
                known_teams_en = {
                    'Liverpool', 'Chelsea', 'Arsenal', 'Manchester City', 'Manchester United',
                    'Tottenham Hotspur', 'Newcastle United', 'Brighton & Hove Albion',
                    'AFC Bournemouth', 'Leeds United', 'Everton', 'Aston Villa',
                    'Fulham', 'Sunderland', 'West Ham United', 'Burnley',
                    'Wolverhampton Wanderers', 'Nottingham Forest', 'Brentford', 'Crystal Palace'
                }
                
                parts = match_id.split('_')
                if len(parts) >= 3:
                    match_date = parts[0]
                    
                    candidate_away = parts[-1]
                    candidate_home_parts = parts[1:-1]
                    candidate_home = '_'.join(candidate_home_parts)
                    
                    if candidate_away in known_teams_en:
                        away_team = candidate_away
                        home_team = candidate_home
                    else:
                        found = False
                        for i in range(len(candidate_home_parts), 0, -1):
                            possible_home = '_'.join(candidate_home_parts[:i])
                            possible_away = '_'.join(candidate_home_parts[i:]) + '_' + candidate_away
                            if possible_home in known_teams_en and possible_away in known_teams_en:
                                home_team = possible_home
                                away_team = possible_away
                                found = True
                                break
                        if not found:
                            home_team = candidate_home
                            away_team = candidate_away
                else:
                    home_team = None
                    away_team = None
                    match_date = None
            else:
                home_team, away_team, actual_wdl, actual_handicap, actual_score, actual_total_goals, match_date = match_info
            
            result['match_info'] = {
                'home_team': home_team,
                'away_team': away_team,
                'match_date': match_date,
                'match_time': '',
                'actual_wdl': actual_wdl,
                'actual_handicap': actual_handicap,
                'actual_score': actual_score,
                'actual_total_goals': actual_total_goals
            }
            
            if self.use_odds_analysis:
                try:
                    wdl_history = load_wdl_history(match_id, self.conn)
                    handicap_history = load_handicap_history(match_id, self.conn)
                    tg_history = load_total_goals_history(match_id, self.conn)
                    score_history = load_score_history(match_id, self.conn)
                    
                    print(f"DEBUG: wdl_history shape={wdl_history.shape}, handicap={handicap_history.shape}, tg={tg_history.shape}, score={score_history.shape}")
                    
                    odds_result = self._analyze_with_odds_module(
                        wdl_history, handicap_history, tg_history, score_history
                    )
                    result['odds_analysis'] = odds_result if odds_result is not None else {}
                except Exception as e:
                    print(f"赔率分析模块错误: {e}")
                    import traceback
                    traceback.print_exc()
                    result['odds_analysis'] = {'error': str(e)}
            
            if self.use_score_prediction:
                score_result = analyze_match_score(match_id)
                result['score_prediction'] = score_result
            
            if self.use_ml_model:
                if not self.ml_model and not self.model_report:
                    self.load_trained_model()
                try:
                    ml_result = self._predict_with_ml_model(match_id, home_team, away_team, match_date)
                    print(f"DEBUG: ml_result={ml_result}")
                    result['ml_prediction'] = ml_result
                except Exception as e:
                    print(f"DEBUG: ML预测异常: {e}")
                    import traceback
                    traceback.print_exc()
                    result['ml_prediction'] = None
            
            result['final_prediction'] = self._ensemble_predictions(result)
            result['confidence'] = self._calculate_confidence(result)
            
            self._update_module_stats(result)
            
            self._create_error_tables()
            self._record_error_case(result)
            
            return result
        
        except Exception as e:
            result['error'] = str(e)
            return result
    
    def _analyze_with_odds_module(self, wdl_history, handicap_history, tg_history, score_history):
        result = {}
        
        try:
            if wdl_history is not None and len(wdl_history) >= 1:
                try:
                    if len(wdl_history) >= 2:
                        wdl_features, _ = analyze_wdl_trend(wdl_history)
                        signal_label = {0: '负', 1: '平', 2: '胜'}.get(wdl_features.get('wdl_market_signal'), '未知')
                    else:
                        last = wdl_history.iloc[-1]
                        if last['win_a'] > 0 and last['draw'] > 0 and last['win_b'] > 0:
                            total_implied = 1/last['win_a'] + 1/last['draw'] + 1/last['win_b']
                            if total_implied > 0:
                                imp_home = (1/last['win_a']) / total_implied
                                imp_draw = (1/last['draw']) / total_implied
                                imp_away = (1/last['win_b']) / total_implied
                            else:
                                imp_home = imp_draw = imp_away = 1/3
                        else:
                            imp_home = imp_draw = imp_away = 1/3
                        
                        wdl_features = {
                            'wdl_implied_prob_home': imp_home,
                            'wdl_implied_prob_draw': imp_draw,
                            'wdl_implied_prob_away': imp_away,
                            'wdl_market_signal': 2 if imp_home > imp_draw and imp_home > imp_away else (0 if imp_away > imp_draw else 1),
                            'wdl_odds_confidence': 50
                        }
                        signal_label = {0: '负', 1: '平', 2: '胜'}.get(wdl_features['wdl_market_signal'], '未知')
                    
                    result['wdl'] = {
                        'market_signal': wdl_features.get('wdl_market_signal'),
                        'confidence': wdl_features.get('wdl_odds_confidence', 0),
                        'signal_label': signal_label,
                        'features': wdl_features
                    }
                except Exception as e:
                    result['wdl_error'] = str(e)
        except Exception as e:
            result['error'] = f"odds module error: {str(e)}"
        
        if len(handicap_history) >= 1:
            try:
                if len(handicap_history) >= 2:
                    hcp_features, _ = analyze_handicap_trend(handicap_history)
                    hcp_pred = hcp_features.get('hcp_prediction')
                else:
                    last = handicap_history.iloc[-1]
                    if last['hcp_win'] > 0 and last['hcp_draw'] > 0 and last['hcp_lose'] > 0:
                        total_implied = 1/last['hcp_win'] + 1/last['hcp_draw'] + 1/last['hcp_lose']
                        if total_implied > 0:
                            imp_win = (1/last['hcp_win']) / total_implied
                            imp_draw = (1/last['hcp_draw']) / total_implied
                            imp_lose = (1/last['hcp_lose']) / total_implied
                        else:
                            imp_win = imp_draw = imp_lose = 1/3
                    else:
                        imp_win = imp_draw = imp_lose = 1/3
                    
                    hcp_pred = 2 if imp_win > imp_draw and imp_win > imp_lose else (0 if imp_lose > imp_draw else 1)
                    hcp_features = {
                        'hcp_prediction': hcp_pred,
                        'hcp_implied_prob_win': imp_win,
                        'hcp_implied_prob_draw': imp_draw,
                        'hcp_implied_prob_lose': imp_lose
                    }
                
                result['handicap'] = {
                    'prediction': hcp_features.get('hcp_prediction'),
                    'signal_label': {-1: '让负', 0: '让平', 1: '让胜'}.get(hcp_features.get('hcp_prediction'), '未知'),
                    'features': hcp_features
                }
            except Exception as e:
                result['handicap_error'] = str(e)
        
        if len(tg_history) >= 2:
            try:
                tg_features, _ = analyze_total_goals_trend(tg_history)
                target_goals = [tg_features.get(f'tg_target_goals_{i}') for i in range(1, 5) if tg_features.get(f'tg_target_goals_{i}') is not None]
                result['total_goals'] = {
                    'target_goals': [g for g in target_goals if g is not None],
                    'features': tg_features
                }
            except Exception as e:
                result['total_goals_error'] = str(e)
        
        if len(score_history) >= 2:
            try:
                target_goals = result.get('total_goals', {}).get('target_goals', [])
                score_features, _ = analyze_score_trend(score_history, target_goals)
                result['score'] = {
                    'features': score_features
                }
            except Exception as e:
                result['score_error'] = str(e)
        
        return result
    
    def _predict_with_ml_model(self, match_id, home_team, away_team, match_date):
        try:
            df = load_match_data_odds()
            
            target_date = pd.to_datetime(match_date).date()
            
            home_team_cn = normalize_team_name(home_team) or home_team
            away_team_cn = normalize_team_name(away_team) or away_team
            
            if home_team_cn != home_team:
                print(f"名称标准化: '{home_team}' -> '{home_team_cn}'")
            if away_team_cn != away_team:
                print(f"名称标准化: '{away_team}' -> '{away_team_cn}'")
            
            team_mask_en = (df['home_team_name'] == home_team) & (df['away_team_name'] == away_team)
            team_mask_cn = (df['home_team_name'] == home_team_cn) & (df['away_team_name'] == away_team_cn)
            team_mask = team_mask_en | team_mask_cn
            
            date_mask = df['date'].dt.date == target_date
            
            mask = team_mask & date_mask
            
            if not mask.any():
                date_diff = abs((df['date'] - pd.to_datetime(target_date)).dt.days)
                nearby_mask = team_mask & (date_diff <= 1)
                
                if nearby_mask.any():
                    nearby_matches = df[nearby_mask][['date', 'home_team_name', 'away_team_name']]
                    print(f"警告: 未找到精确日期匹配，找到{len(nearby_matches)}场邻近日期比赛")
                    for _, row in nearby_matches.iterrows():
                        print(f"  - {row['date'].date()} {row['home_team_name']} vs {row['away_team_name']}")
                    mask = nearby_mask
                else:
                    home_matches = df[df['home_team_name'] == home_team]['date'].dt.date.unique()[:5]
                    away_matches = df[df['away_team_name'] == away_team]['date'].dt.date.unique()[:5]
                    print(f"未找到匹配比赛:")
                    print(f"  目标: {target_date} {home_team} vs {away_team}")
                    print(f"  主队出现日期: {list(home_matches) if len(home_matches) > 0 else '无'}")
                    print(f"  客队出现日期: {list(away_matches) if len(away_matches) > 0 else '无'}")
                    return {'error': '未在训练数据中找到匹配比赛'}
            
            match_df = df[mask].copy()
            
            basic_features = build_features(match_df)
            team_features = build_team_features(match_df)
            
            team_features = team_features.drop(columns=[col for col in team_features.columns if col in basic_features.columns])
            
            X = pd.concat([basic_features, team_features], axis=1)
            
            # 集成赔率特征（与训练保持一致）
            try:
                odds_features = build_odds_features(match_df, self.conn)
                X = pd.concat([X, odds_features], axis=1)
                print(f"   赔率特征维度: {odds_features.shape[1]}")
            except Exception as e:
                print(f"   警告: 构建赔率特征失败 - {e}")
            
            upset_risk_score = 0
            if 'upset_risk_score' in X.columns:
                upset_risk_score = float(X['upset_risk_score'].iloc[0])
            
            if self.use_multitask and self.multitask_models:
                return self._predict_with_multitask_model(X, upset_risk_score)
            
            if self.ml_model and len(X) > 0:
                if self.selected_features:
                    missing_cols = set(self.selected_features) - set(X.columns)
                    if missing_cols:
                        print(f"警告: 特征不匹配，缺失特征: {missing_cols}")
                        for col in missing_cols:
                            X[col] = 0
                    
                    X = X[self.selected_features]
                
                X_data = X.values if hasattr(X, 'values') else X
                
                if self.scaler:
                    X_data = self.scaler.transform(X_data)
                
                if hasattr(self.ml_model, 'predict_proba'):
                    probs = self.ml_model.predict_proba(X_data)[0]
                else:
                    import xgboost as xgb
                    dmatrix = xgb.DMatrix(X_data)
                    probs = self.ml_model.predict(dmatrix)[0]
                
                class_mapping = {0: '负', 1: '平', 2: '胜'}
                pred_idx = np.argmax(probs)
                prediction = class_mapping.get(pred_idx, '未知')
                
                confidence = float(probs[pred_idx] * 100)
                
                return {
                    'model_type': 'Stacking Ensemble' if hasattr(self.ml_model, 'named_estimators_') else 'XGBoost',
                    'prediction': prediction,
                    'confidence': confidence,
                    'probabilities': {'胜': float(probs[2]), '平': float(probs[1]), '负': float(probs[0])},
                    'upset_risk_score': upset_risk_score
                }
            
            elif self.model_report and 'final_models' in self.model_report:
                ensemble_metrics = self.model_report['final_models'].get('ensemble')
                xgb_metrics = self.model_report['final_models'].get('xgboost')
                
                if ensemble_metrics:
                    return {
                        'model_type': 'Stacking Ensemble',
                        'accuracy': ensemble_metrics.get('accuracy'),
                        'prediction': '需要重新训练模型以生成预测',
                        'metrics': ensemble_metrics
                    }
                elif xgb_metrics:
                    return {
                        'model_type': 'XGBoost',
                        'accuracy': xgb_metrics.get('accuracy'),
                        'prediction': '需要重新训练模型以生成预测',
                        'metrics': xgb_metrics
                    }
            
            return {'error': '模型报告中没有可用的预测结果'}
        
        except Exception as e:
            return {'error': str(e)}
    
    def _predict_with_multitask_model(self, X, upset_risk_score):
        try:
            if self.selected_features:
                missing_cols = set(self.selected_features) - set(X.columns)
                if missing_cols:
                    print(f"警告: 特征不匹配，缺失特征: {missing_cols}")
                    for col in missing_cols:
                        X[col] = 0
                
                X = X[self.selected_features]
            
            X_data = X.values if hasattr(X, 'values') else X
            
            if self.scaler:
                X_data = self.scaler.transform(X_data)
            
            import xgboost as xgb
            
            predictions = {}
            
            if 'wdl' in self.multitask_models:
                dmatrix = xgb.DMatrix(X_data)
                wdl_probs = self.multitask_models['wdl'].predict(dmatrix)[0]
                class_mapping = {0: '负', 1: '平', 2: '胜'}
                wdl_pred_idx = np.argmax(wdl_probs)
                wdl_prediction = class_mapping.get(wdl_pred_idx, '未知')
                predictions['wdl'] = {
                    'prediction': wdl_prediction,
                    'probabilities': {'胜': float(wdl_probs[2]), '平': float(wdl_probs[1]), '负': float(wdl_probs[0])},
                    'confidence': float(wdl_probs[wdl_pred_idx] * 100)
                }
            
            total_goals_pred = None
            total_goals_probs = []
            if 'total_goals' in self.multitask_models:
                dmatrix = xgb.DMatrix(X_data)
                tg_probs = self.multitask_models['total_goals'].predict(dmatrix)[0]
                tg_bins = self.multitask_config.get('total_goals_bins', [0, 1, 2, 3, 4, 5, 6, 7])
                tg_pred_idx = np.argmax(tg_probs)
                total_goals_pred = tg_bins[tg_pred_idx] if tg_pred_idx < len(tg_bins) else tg_bins[-1]
                total_goals_probs = tg_probs.tolist()
                predictions['total_goals'] = {
                    'prediction': total_goals_pred,
                    'probabilities': total_goals_probs,
                    'bins': tg_bins
                }
            
            goal_diff_pred = None
            goal_diff_probs = []
            if 'goal_diff' in self.multitask_models:
                dmatrix = xgb.DMatrix(X_data)
                gd_probs = self.multitask_models['goal_diff'].predict(dmatrix)[0]
                gd_bins = self.multitask_config.get('goal_diff_bins', [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6])
                gd_pred_idx = np.argmax(gd_probs)
                goal_diff_pred = gd_bins[gd_pred_idx] if gd_pred_idx < len(gd_bins) else gd_bins[-1]
                goal_diff_probs = gd_probs.tolist()
                predictions['goal_diff'] = {
                    'prediction': goal_diff_pred,
                    'probabilities': goal_diff_probs,
                    'bins': gd_bins
                }
            
            score_predictions = self._generate_score_from_multitask(predictions)
            
            return {
                'model_type': 'Multitask Learning',
                'prediction': predictions['wdl']['prediction'] if 'wdl' in predictions else '未知',
                'confidence': predictions['wdl']['confidence'] if 'wdl' in predictions else 0,
                'probabilities': predictions['wdl']['probabilities'] if 'wdl' in predictions else {'胜': 0.33, '平': 0.33, '负': 0.33},
                'upset_risk_score': upset_risk_score,
                'multitask_results': predictions,
                'score_predictions': score_predictions
            }
        
        except Exception as e:
            print(f"多任务预测错误: {e}")
            import traceback
            traceback.print_exc()
            return {'error': str(e)}
    
    def _generate_score_from_multitask(self, predictions):
        try:
            wdl_probs = predictions.get('wdl', {}).get('probabilities', {'胜': 0.33, '平': 0.33, '负': 0.33})
            tg_probs = predictions.get('total_goals', {}).get('probabilities', [0.125] * 8)
            gd_probs = predictions.get('goal_diff', {}).get('probabilities', [1/13] * 13)
            tg_bins = predictions.get('total_goals', {}).get('bins', [0, 1, 2, 3, 4, 5, 6, 7])
            gd_bins = predictions.get('goal_diff', {}).get('bins', [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6])
            
            score_probs = {}
            
            for h in range(0, 7):
                for a in range(0, 7):
                    total_g = h + a
                    goal_diff = h - a
                    
                    tg_bin_idx = min(sum(1 for b in tg_bins if b <= total_g), len(tg_probs) - 1)
                    gd_bin_idx = min(sum(1 for b in gd_bins if b <= goal_diff), len(gd_probs) - 1)
                    
                    wdl_idx = 2 if h > a else (0 if h < a else 1)
                    wdl_prob = [wdl_probs['负'], wdl_probs['平'], wdl_probs['胜']][wdl_idx]
                    
                    combined_prob = wdl_prob * tg_probs[tg_bin_idx] * gd_probs[gd_bin_idx]
                    
                    score_probs[f"{h}:{a}"] = combined_prob
            
            total_prob = sum(score_probs.values())
            if total_prob > 0:
                score_probs = {k: v / total_prob for k, v in score_probs.items()}
            
            sorted_scores = sorted(score_probs.items(), key=lambda x: -x[1])[:5]
            
            top_scores = []
            for score, prob in sorted_scores:
                top_scores.append((score, prob))
            
            return {
                'top_scores': top_scores,
                'score_probs': score_probs
            }
        except Exception as e:
            print(f"生成比分预测错误: {e}")
            return {'top_scores': [], 'score_probs': {}}
    
    def _update_module_stats(self, result):
        if not hasattr(self, 'module_stats'):
            self.module_stats = {
                'ml': {'correct': 0, 'total': 0, 'accuracy': 0.55},
                'odds': {'correct': 0, 'total': 0, 'accuracy': 0.30},
                'score': {'correct': 0, 'total': 0, 'accuracy': 0.15,
                          'score_hit_top1': 0, 'score_hit_top3': 0, 
                          'score_total': 0, 'score_hit_rate_top1': 0, 'score_hit_rate_top3': 0}
            }
        
        actual_wdl = result.get('match_info', {}).get('actual_wdl')
        actual_score = result.get('match_info', {}).get('actual_score', '')
        if not actual_wdl:
            return
        
        ml_pred = result.get('ml_prediction', {}).get('prediction')
        if ml_pred in ['胜', '平', '负']:
            self.module_stats['ml']['total'] += 1
            if ml_pred == actual_wdl:
                self.module_stats['ml']['correct'] += 1
        
        odds_pred = result.get('odds_analysis', {}).get('wdl', {}).get('signal_label')
        if odds_pred in ['胜', '平', '负']:
            self.module_stats['odds']['total'] += 1
            if odds_pred == actual_wdl:
                self.module_stats['odds']['correct'] += 1
        
        score_pred = None
        top_scores = result.get('score_prediction', {}).get('top_scores', [])
        if top_scores:
            score = top_scores[0][0]
            if ':' in score:
                h, a = map(int, score.split(':'))
                if h > a:
                    score_pred = '胜'
                elif h == a:
                    score_pred = '平'
                else:
                    score_pred = '负'
        
        if score_pred in ['胜', '平', '负']:
            self.module_stats['score']['total'] += 1
            if score_pred == actual_wdl:
                self.module_stats['score']['correct'] += 1
        
        if actual_score and top_scores:
            self.module_stats['score']['score_total'] += 1
            top1_scores = [s[0] for s in top_scores[:1]]
            top3_scores = [s[0] for s in top_scores[:3]]
            
            if actual_score in top1_scores:
                self.module_stats['score']['score_hit_top1'] += 1
            if actual_score in top3_scores:
                self.module_stats['score']['score_hit_top3'] += 1
        
        for module in self.module_stats:
            if self.module_stats[module]['total'] > 0:
                self.module_stats[module]['accuracy'] = (
                    self.module_stats[module]['correct'] / 
                    self.module_stats[module]['total']
                )
        
        if self.module_stats['score']['score_total'] > 0:
            self.module_stats['score']['score_hit_rate_top1'] = (
                self.module_stats['score']['score_hit_top1'] / 
                self.module_stats['score']['score_total']
            )
            self.module_stats['score']['score_hit_rate_top3'] = (
                self.module_stats['score']['score_hit_top3'] / 
                self.module_stats['score']['score_total']
            )
    
    def _get_dynamic_weights(self):
        if not hasattr(self, 'module_stats'):
            self.module_stats = {
                'ml': {'correct': 0, 'total': 0, 'accuracy': 0.55},
                'odds': {'correct': 0, 'total': 0, 'accuracy': 0.30},
                'score': {'correct': 0, 'total': 0, 'accuracy': 0.15,
                          'score_hit_top1': 0, 'score_hit_top3': 0, 
                          'score_total': 0, 'score_hit_rate_top1': 0, 'score_hit_rate_top3': 0}
            }
        
        ml_acc = self.module_stats['ml']['accuracy']
        odds_acc = self.module_stats['odds']['accuracy']
        score_acc = self.module_stats['score']['accuracy']
        
        total_acc = ml_acc + odds_acc + score_acc
        if total_acc == 0:
            return {'ml': 0.5, 'odds': 0.35, 'score': 0.15}
        
        ml_weight = ml_acc / total_acc
        odds_weight = odds_acc / total_acc
        score_weight = score_acc / total_acc
        
        min_weight = 0.05
        ml_weight = max(min_weight, ml_weight)
        odds_weight = max(min_weight, odds_weight)
        score_weight = max(min_weight, score_weight)
        
        total = ml_weight + odds_weight + score_weight
        ml_weight /= total
        odds_weight /= total
        score_weight /= total
        
        return {'ml': ml_weight, 'odds': odds_weight, 'score': score_weight}
    
    def _ensemble_predictions(self, result):
        final_probs = {'胜': 0, '平': 0, '负': 0}
        
        weights = self._get_dynamic_weights()
        
        upset_risk_score = result.get('ml_prediction', {}).get('upset_risk_score', 0)
        
        ml_pred = None
        ml_probs = {}
        if result.get('ml_prediction'):
            ml_pred = result['ml_prediction'].get('prediction')
            ml_probs = result['ml_prediction'].get('probabilities', {'胜': 0.33, '平': 0.33, '负': 0.33})
        
        odds_pred = None
        odds_probs = {}
        wdl_features = {}
        if result.get('odds_analysis') and 'wdl' in result['odds_analysis']:
            wdl_features = result['odds_analysis']['wdl'].get('features', {})
            odds_pred = result['odds_analysis']['wdl'].get('signal_label')
            odds_probs = {
                '胜': wdl_features.get('wdl_implied_prob_home', 0.33),
                '平': wdl_features.get('wdl_implied_prob_draw', 0.33),
                '负': wdl_features.get('wdl_implied_prob_away', 0.33)
            }
            
            if odds_pred and odds_pred in ['胜', '平', '负'] and ml_pred is None:
                pred_prob = odds_probs.get(odds_pred, 0.33)
                max_prob_key = max(odds_probs, key=odds_probs.get)
                max_prob = odds_probs[max_prob_key]
                
                if max_prob_key != odds_pred and max_prob > pred_prob:
                    odds_probs['平'] = odds_probs.get('平', 0) + (max_prob - pred_prob) * 0.5
                    total = sum(odds_probs.values())
                    if total > 0:
                        for key in odds_probs:
                            odds_probs[key] /= total
                
                other_probs_sum = sum(v for k, v in odds_probs.items() if k != odds_pred)
                if other_probs_sum > 0 and pred_prob < 0.5:
                    target_pred_prob = 0.5
                    reduction_factor = (other_probs_sum * (1 - target_pred_prob)) / other_probs_sum
                    for key in odds_probs:
                        if key != odds_pred:
                            odds_probs[key] *= reduction_factor
                    odds_probs[odds_pred] = 1 - sum(v for k, v in odds_probs.items() if k != odds_pred)
        
        if not wdl_features and self.conn:
            match_id = result.get('match_id', '')
            if match_id:
                cursor = self.conn.cursor()
                cursor.execute('SELECT win_a, draw, win_b FROM wdl_history WHERE match_id = ? ORDER BY timestamp DESC LIMIT 1', (match_id,))
                row = cursor.fetchone()
                if row:
                    win_a, draw, win_b = row
                    if win_a > 0 and draw > 0 and win_b > 0:
                        inv_sum = 1/win_a + 1/draw + 1/win_b
                        odds_probs = {
                            '胜': (1/win_a) / inv_sum,
                            '平': (1/draw) / inv_sum,
                            '负': (1/win_b) / inv_sum
                        }
                        wdl_features = {
                            'wdl_implied_prob_home': odds_probs['胜'],
                            'wdl_implied_prob_draw': odds_probs['平'],
                            'wdl_implied_prob_away': odds_probs['负']
                        }
                        max_key = max(odds_probs, key=odds_probs.get)
                        odds_pred = max_key
        
        if ml_pred is None:
            weights = {'ml': 0.05, 'odds': 0.60, 'score': 0.35}
            
            if odds_pred and odds_probs:
                max_prob_key = max(odds_probs, key=odds_probs.get)
                max_prob = odds_probs[max_prob_key]
                pred_prob = odds_probs.get(odds_pred, 0)
                
                if max_prob_key != odds_pred and max_prob > pred_prob + 0.1:
                    draw_prob = odds_probs.get('平', 0)
                    if draw_prob < 0.35:
                        odds_probs['平'] = draw_prob + (max_prob - pred_prob) * 0.3
                        total = sum(odds_probs.values())
                        if total > 0:
                            for key in odds_probs:
                                odds_probs[key] /= total
        
        if ml_pred and odds_pred and ml_pred != odds_pred:
            max_odds_prob = max(odds_probs.get('胜', 0), odds_probs.get('平', 0), odds_probs.get('负', 0))
            ml_max_prob = ml_probs.get(ml_pred, 0.33)
            
            if max_odds_prob > 0.55:
                ml_weight = weights.get('ml', 0.5) * 0.5
                odds_weight = weights.get('odds', 0.35) + weights.get('ml', 0.5) * 0.3
                score_weight = weights.get('score', 0.15) + weights.get('ml', 0.5) * 0.2
                total = ml_weight + odds_weight + score_weight
                weights = {
                    'ml': ml_weight / total,
                    'odds': odds_weight / total,
                    'score': score_weight / total
                }
            elif max_odds_prob > 0.38 and ml_max_prob < 0.60:
                ml_weight = weights.get('ml', 0.5) * 0.4
                odds_weight = weights.get('odds', 0.35) + weights.get('ml', 0.5) * 0.4
                score_weight = weights.get('score', 0.15) + weights.get('ml', 0.5) * 0.2
                total = ml_weight + odds_weight + score_weight
                weights = {
                    'ml': ml_weight / total,
                    'odds': odds_weight / total,
                    'score': score_weight / total
                }
        
        if ml_pred and odds_pred and ml_pred != odds_pred:
            ml_max_prob = ml_probs.get(ml_pred, 0.33)
            odds_pred_prob = odds_probs.get(odds_pred, 0.33)
            
            prob_divergence = abs(ml_max_prob - odds_pred_prob)
            
            if prob_divergence > 0.1:
                new_upset_risk = min(prob_divergence * 3, 0.7)
                if new_upset_risk > upset_risk_score:
                    upset_risk_score = new_upset_risk
        
        # 让球分析信号融合：当让球方向与胜平负方向不一致时，提高冷门风险评分
        hcp_analysis = result.get('odds_analysis', {}).get('handicap', {})
        hcp_signal = hcp_analysis.get('signal_label', '')
        if hcp_signal in ['让胜', '让平', '让负'] and odds_pred in ['胜', '平', '负']:
            # 让球方向与胜平负方向不一致的情况
            # WDL预测"胜"时，Handicap应该预测"让胜"或"让平"（主队热门）
            # WDL预测"负"时，Handicap应该预测"让负"或"让平"（主队热门）
            wdl_to_hcp_mapping = {
                '胜': ['让胜', '让平'],
                '平': ['让胜', '让平', '让负'],
                '负': ['让负', '让平']
            }
            
            expected_hcp_signals = wdl_to_hcp_mapping.get(odds_pred, [])
            if hcp_signal not in expected_hcp_signals:
                hcp_conflict = True
                hcp_features = hcp_analysis.get('features', {})
                hcp_max_prob = max(hcp_features.get('hcp_implied_prob_win', 0), 
                                  hcp_features.get('hcp_implied_prob_draw', 0),
                                  hcp_features.get('hcp_implied_prob_lose', 0))
                
                hcp_confidence = hcp_max_prob if hcp_max_prob > 0 else 0.5
                upset_risk_increase = hcp_confidence * 0.25
                
                new_upset_risk = min(upset_risk_score + upset_risk_increase, 0.8)
                if new_upset_risk > upset_risk_score:
                    upset_risk_score = new_upset_risk
        
        if upset_risk_score == 0 and ml_pred is None and odds_pred and wdl_features:
            orig_probs = {
                '胜': wdl_features.get('wdl_implied_prob_home', 0.33),
                '平': wdl_features.get('wdl_implied_prob_draw', 0.33),
                '负': wdl_features.get('wdl_implied_prob_away', 0.33)
            }
            max_prob_key = max(orig_probs, key=orig_probs.get)
            max_prob = orig_probs[max_prob_key]
            pred_prob = orig_probs.get(odds_pred, 0)
            if max_prob_key != odds_pred and max_prob > pred_prob + 0.1:
                prob_divergence = max_prob - pred_prob
                upset_risk_score = min(prob_divergence * 2, 0.5)
        
        main_predictions = []
        if ml_pred:
            main_predictions.append(ml_pred)
        if odds_pred:
            main_predictions.append(odds_pred)
        
        all_unanimous = len(main_predictions) >= 2 and len(set(main_predictions)) == 1
        
        score_predicts_draw = False
        score_draw_prob = 0
        score_max_non_draw_prob = 0
        score_total_draw_prob = 0
        if result.get('score_prediction'):
            top_scores = result['score_prediction'].get('top_scores', [])
            if top_scores and len(top_scores) >= 1:
                top_score = top_scores[0][0]
                top_prob = top_scores[0][1] if len(top_scores[0]) > 1 else 0
                if ':' in top_score:
                    h, a = map(int, top_score.split(':'))
                    if h == a:
                        score_predicts_draw = True
                        score_draw_prob = top_prob
            
            for score, prob in top_scores[:5]:
                if ':' in score:
                    h, a = map(int, score.split(':'))
                    if h == a:
                        score_total_draw_prob += prob
                    else:
                        if prob > score_max_non_draw_prob:
                            score_max_non_draw_prob = prob
        
        draw_prob_diff = score_draw_prob - score_max_non_draw_prob
        
        if all_unanimous:
            if wdl_features and ml_probs:
                odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0.25)
                ml_draw_prob = ml_probs.get('平', 0.25)
                if ml_draw_prob > 0.27 and odds_draw_prob > 0.25:
                    if score_predicts_draw and score_draw_prob > 0.15:
                        if ml_draw_prob > 0.275 and odds_draw_prob < 0.275:
                            if draw_prob_diff > 0.02:
                                return '平'
            
            unanimous_pred = main_predictions[0]
            
            skip_unanimous_return = False
            if result.get('score_prediction') and unanimous_pred != '平':
                top_scores = result['score_prediction'].get('top_scores', [])
                upset_score_prob = 0
                max_upset_score_prob = 0
                for score, prob in top_scores[:5]:
                    if ':' in score:
                        h, a = map(int, score.split(':'))
                        if unanimous_pred == '胜':
                            if h < a:
                                upset_score_prob += prob
                                if prob > max_upset_score_prob:
                                    max_upset_score_prob = prob
                        elif unanimous_pred == '负':
                            if h > a:
                                upset_score_prob += prob
                                if prob > max_upset_score_prob:
                                    max_upset_score_prob = prob
                
                ml_max_prob = max(ml_probs.get('胜', 0), ml_probs.get('平', 0), ml_probs.get('负', 0))
                ml_max_key = max(ml_probs, key=ml_probs.get)
                if max_upset_score_prob > 0.10 and ml_max_prob < 0.50:
                    if upset_score_prob > 0.15 or max_upset_score_prob > 0.12:
                        odds_max_prob = max(odds_probs.get('胜', 0), odds_probs.get('平', 0), odds_probs.get('负', 0))
                        if odds_max_prob > 0.55:
                            if upset_score_prob > 0.20 and max_upset_score_prob > 0.15:
                                upset_risk_score = min(0.4 + upset_score_prob, 0.6)
                                skip_unanimous_return = True
                        else:
                            upset_risk_score = min(0.5 + upset_score_prob, 0.7)
                            if ml_max_key == unanimous_pred and ml_max_prob > 0.35:
                                skip_unanimous_return = False
                            else:
                                skip_unanimous_return = True
            
            if not skip_unanimous_return and unanimous_pred in ['胜', '平', '负']:
                if wdl_features:
                    max_odds_prob = max(wdl_features.get('wdl_implied_prob_home', 0), 
                                       wdl_features.get('wdl_implied_prob_draw', 0), 
                                       wdl_features.get('wdl_implied_prob_away', 0))
                    odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                    
                    if max_odds_prob > 0.65 and odds_draw_prob > 0.15:
                        if ml_probs and ml_probs.get('平', 0) > 0.20:
                            return '平'
                
                return unanimous_pred
        
        has_strong_favorite = False
        if wdl_features:
            max_odds_prob = max(wdl_features.get('wdl_implied_prob_home', 0), 
                               wdl_features.get('wdl_implied_prob_draw', 0), 
                               wdl_features.get('wdl_implied_prob_away', 0))
            if max_odds_prob > 0.55:
                has_strong_favorite = True
        
        if has_strong_favorite and wdl_features:
            odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0)
            max_odds_prob = max(wdl_features.get('wdl_implied_prob_home', 0), 
                               wdl_features.get('wdl_implied_prob_draw', 0), 
                               wdl_features.get('wdl_implied_prob_away', 0))
            
            if score_total_draw_prob > 0.25 and odds_draw_prob > 0.22:
                if score_total_draw_prob > score_max_non_draw_prob * 1.2:
                    return '平'
            if score_total_draw_prob == 0 and odds_draw_prob > 0.15:
                if max_odds_prob > 0.70 and ml_probs and ml_probs.get('平', 0) > 0.20:
                    return '平'
                if ml_probs and ml_probs.get('平', 0) > 0.25:
                    return '平'
                if not ml_pred and odds_draw_prob > 0.24:
                    return '平'
        

        
        if upset_risk_score > 0.33:
            if score_predicts_draw and score_draw_prob > 0.14 and draw_prob_diff > -0.05 and draw_prob_diff < 0.02:
                if ml_probs and ml_pred and wdl_features:
                    ml_max_prob = max(ml_probs.get('胜', 0), ml_probs.get('平', 0), ml_probs.get('负', 0))
                    ml_pred_prob = ml_probs.get(ml_pred, 0)
                    odds_pred_prob = 0
                    if ml_pred == '胜':
                        odds_pred_prob = wdl_features.get('wdl_implied_prob_home', 0)
                    elif ml_pred == '负':
                        odds_pred_prob = wdl_features.get('wdl_implied_prob_away', 0)
                    elif ml_pred == '平':
                        odds_pred_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                    odds_max_p = max(wdl_features.get('wdl_implied_prob_home', 0), 
                                     wdl_features.get('wdl_implied_prob_draw', 0), 
                                     wdl_features.get('wdl_implied_prob_away', 0))
                    ml_pred_odds_prob = 0
                    if ml_pred == '胜':
                        ml_pred_odds_prob = wdl_features.get('wdl_implied_prob_home', 0)
                    elif ml_pred == '负':
                        ml_pred_odds_prob = wdl_features.get('wdl_implied_prob_away', 0)
                    elif ml_pred == '平':
                        ml_pred_odds_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                    
                    if ml_max_prob > 0.50:
                        odds_max_prob = odds_max_p
                        if ml_pred == '胜' and wdl_features.get('wdl_implied_prob_home', 0) == odds_max_prob:
                            if upset_risk_score < 0.5:
                                return ml_pred
                        elif ml_pred == '负' and wdl_features.get('wdl_implied_prob_away', 0) == odds_max_prob:
                            if upset_risk_score < 0.5:
                                return ml_pred
                        elif ml_pred == '平' and wdl_features.get('wdl_implied_prob_draw', 0) == odds_max_prob:
                            if upset_risk_score < 0.5:
                                return ml_pred
                    else:
                        if ml_pred_odds_prob == odds_max_p:
                            if upset_risk_score < 0.5:
                                return ml_pred
                        if ml_pred_prob > 0.35 and ml_pred_prob > odds_pred_prob * 1.3 and ml_pred_prob != odds_max_p:
                            if odds_max_p <= 0.55:
                                return ml_pred
                
                if ml_pred is None and wdl_features:
                    odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                    if odds_draw_prob > 0.18 and score_draw_prob >= score_max_non_draw_prob * 0.9:
                        return '平'
                
                if wdl_features:
                    odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                    if not has_strong_favorite or (upset_risk_score > 0.35 and odds_draw_prob > 0.18):
                        if upset_risk_score < 0.5:
                            return '平'
                        elif upset_risk_score >= 0.5 and score_draw_prob >= score_max_non_draw_prob * 0.9:
                            if ml_probs:
                                ml_max_prob = max(ml_probs.get('胜', 0), ml_probs.get('平', 0), ml_probs.get('负', 0))
                                ml_max_key = max(ml_probs, key=ml_probs.get)
                                if ml_max_key != '平' and ml_max_prob > 0.35:
                                    return ml_max_key
                            return '平'
            if score_total_draw_prob > 0.25 and not score_predicts_draw:
                if wdl_features:
                    odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                    if odds_draw_prob > 0.22:
                        return '平'
        else:
            if score_predicts_draw and score_draw_prob > 0.17 and draw_prob_diff > 0.04:
                if not has_strong_favorite or score_draw_prob > 0.22:
                    return '平'
            if score_total_draw_prob > 0.30 and not score_predicts_draw:
                if wdl_features:
                    odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                    if odds_draw_prob > 0.24:
                        return '平'
            
            if not ml_pred and wdl_features:
                odds_draw_prob = wdl_features.get('wdl_implied_prob_draw', 0)
                if odds_draw_prob > 0.22:
                    if score_predicts_draw and score_draw_prob >= score_max_non_draw_prob:
                        return '平'
            

        
        favorite_prediction = None
        
        if odds_pred and odds_pred in ['胜', '负']:
            favorite_prediction = odds_pred
        elif wdl_features:
            home_prob = wdl_features.get('wdl_implied_prob_home', 0.33)
            away_prob = wdl_features.get('wdl_implied_prob_away', 0.33)
            draw_prob = wdl_features.get('wdl_implied_prob_draw', 0.33)
            
            if home_prob > away_prob and home_prob > draw_prob:
                favorite_prediction = '胜'
            elif away_prob > home_prob and away_prob > draw_prob:
                favorite_prediction = '负'
        
        if has_strong_favorite and ml_pred and odds_pred and ml_pred != odds_pred:
            if favorite_prediction == odds_pred:
                ml_max_prob = ml_probs.get(ml_pred, 0.33)
                if ml_max_prob < 0.45:
                    return favorite_prediction
        
        all_favorite = len(main_predictions) >= 2 and all(p == favorite_prediction for p in main_predictions)
        has_disagreement = len(main_predictions) >= 2 and len(set(main_predictions)) > 1
        
        if all_favorite:
            if upset_risk_score > 0.6:
                upset_factor = 0.5
            elif upset_risk_score > 0.5:
                upset_factor = 0.6
            elif upset_risk_score > 0.45:
                upset_factor = 0.7
            elif upset_risk_score > 0.4:
                upset_factor = 0.8
            elif upset_risk_score > 0.36:
                upset_factor = 0.85
            else:
                upset_factor = 1.0
        elif upset_risk_score > 0.45:
            upset_factor = 0.4
        elif upset_risk_score > 0.4:
            upset_factor = 0.5
        elif upset_risk_score > 0.36:
            upset_factor = 0.6
        elif upset_risk_score > 0.33:
            upset_factor = 0.75
        else:
            upset_factor = 1.0
        
        if result.get('ml_prediction') and 'prediction' in result['ml_prediction']:
            ml_probs = result['ml_prediction'].get('probabilities', {})
            ml_p_win = ml_probs.get('胜', 0)
            ml_p_draw = ml_probs.get('平', 0)
            ml_p_lose = ml_probs.get('负', 0)
            
            if favorite_prediction == '胜':
                ml_p_win *= upset_factor
                extra = ml_p_win * (1 - upset_factor) / 2
                ml_p_draw += extra
                ml_p_lose += extra
            elif favorite_prediction == '负':
                ml_p_lose *= upset_factor
                extra = ml_p_lose * (1 - upset_factor) / 2
                ml_p_draw += extra
                ml_p_win += extra
            
            total = ml_p_win + ml_p_draw + ml_p_lose
            if total > 0:
                ml_p_win /= total
                ml_p_draw /= total
                ml_p_lose /= total
            
            final_probs['胜'] += ml_p_win * weights['ml']
            final_probs['平'] += ml_p_draw * weights['ml']
            final_probs['负'] += ml_p_lose * weights['ml']
        
        if odds_probs:
            odds_p_win = odds_probs.get('胜', 0.33)
            odds_p_draw = odds_probs.get('平', 0.33)
            odds_p_lose = odds_probs.get('负', 0.33)
            
            if favorite_prediction == '胜':
                odds_p_win *= upset_factor
                extra = odds_p_win * (1 - upset_factor) / 2
                odds_p_draw += extra
                odds_p_lose += extra
            elif favorite_prediction == '负':
                odds_p_lose *= upset_factor
                extra = odds_p_lose * (1 - upset_factor) / 2
                odds_p_draw += extra
                odds_p_win += extra
            
            total = odds_p_win + odds_p_draw + odds_p_lose
            if total > 0:
                odds_p_win /= total
                odds_p_draw /= total
                odds_p_lose /= total
            
            final_probs['胜'] += odds_p_win * weights['odds']
            final_probs['平'] += odds_p_draw * weights['odds']
            final_probs['负'] += odds_p_lose * weights['odds']
            
            if odds_pred and odds_pred in ['胜', '平', '负']:
                max_prob_key = max(odds_probs, key=odds_probs.get)
                max_prob = odds_probs[max_prob_key]
                pred_prob = odds_probs.get(odds_pred, 0)
                
                if max_prob_key != odds_pred and max_prob > pred_prob + 0.1:
                    draw_boost = (max_prob - pred_prob) * 0.2
                    final_probs['平'] += draw_boost * weights['odds']
                    final_probs[max_prob_key] -= draw_boost * weights['odds'] * 0.5
                    final_probs[odds_pred] -= draw_boost * weights['odds'] * 0.5
                
                min_target_prob = 0.35
                current_prob = final_probs.get(odds_pred, 0)
                if current_prob < min_target_prob * (weights['odds'] + weights['score']):
                    boost_amount = min_target_prob * (weights['odds'] + weights['score']) - current_prob
                    final_probs[odds_pred] += boost_amount
                    reduce_amount = boost_amount / 2
                    if odds_pred != '胜':
                        final_probs['胜'] -= reduce_amount
                    if odds_pred != '平':
                        final_probs['平'] -= reduce_amount
                    if odds_pred != '负':
                        final_probs['负'] -= reduce_amount
        
        if result.get('score_prediction'):
            top_scores = result['score_prediction'].get('top_scores', [])
            if top_scores:
                score_probs = {}
                for score, prob in top_scores[:5]:
                    if ':' in score:
                        h, a = map(int, score.split(':'))
                        if h > a:
                            score_probs['胜'] = score_probs.get('胜', 0) + prob
                        elif h == a:
                            score_probs['平'] = score_probs.get('平', 0) + prob
                        else:
                            score_probs['负'] = score_probs.get('负', 0) + prob
                
                total = sum(score_probs.values())
                if total > 0:
                    score_probs['胜'] = score_probs.get('胜', 0) / total
                    score_probs['平'] = score_probs.get('平', 0) / total
                    score_probs['负'] = score_probs.get('负', 0) / total
                    
                    min_prob = 0.05
                    for key in ['胜', '平', '负']:
                        if score_probs.get(key, 0) < min_prob:
                            score_probs[key] = min_prob
                    
                    total = sum(score_probs.values())
                    if total > 0:
                        score_probs['胜'] /= total
                        score_probs['平'] /= total
                        score_probs['负'] /= total
                    
                    if favorite_prediction == '胜':
                        score_probs['胜'] *= upset_factor
                        extra = score_probs['胜'] * (1 - upset_factor) / 2
                        score_probs['平'] += extra
                        score_probs['负'] += extra
                    elif favorite_prediction == '负':
                        score_probs['负'] *= upset_factor
                        extra = score_probs['负'] * (1 - upset_factor) / 2
                        score_probs['平'] += extra
                        score_probs['胜'] += extra
                    
                    draw_prob = score_probs.get('平', 0)
                    max_non_draw_prob = max(score_probs.get('胜', 0), score_probs.get('负', 0))
                    if draw_prob > 0.14 and draw_prob >= max_non_draw_prob * 0.95:
                        score_probs['平'] = draw_prob * 1.3
                        total = sum(score_probs.values())
                        if total > 0:
                            score_probs['胜'] /= total
                            score_probs['平'] /= total
                            score_probs['负'] /= total
                    
                    total = sum(score_probs.values())
                    if total > 0:
                        score_probs['胜'] /= total
                        score_probs['平'] /= total
                        score_probs['负'] /= total
                
                # 总进球趋势与比分预测的一致性检查
                tg_score_consistency = 1.0
                tg_features = result.get('odds_analysis', {}).get('total_goals', {}).get('features', {})
                tg_target_goals = tg_features.get('tg_target_goals', [])
                if tg_target_goals and top_scores:
                    score_total_goals_dist = {}
                    for score, prob in top_scores[:5]:
                        if ':' in score:
                            h, a = map(int, score.split(':'))
                            total_g = h + a
                            score_total_goals_dist[total_g] = score_total_goals_dist.get(total_g, 0) + prob
                    
                    tg_target_set = set(tg_target_goals)
                    score_tg_prob = sum(score_total_goals_dist.get(g, 0) for g in tg_target_set)
                    
                    if score_tg_prob > 0.5:
                        tg_score_consistency = 1.3
                    elif score_tg_prob > 0.3:
                        tg_score_consistency = 1.15
                    elif score_tg_prob < 0.2:
                        tg_score_consistency = 0.8
                
                adjusted_score_weight = weights['score'] * tg_score_consistency
                
                final_probs['胜'] += score_probs.get('胜', 0.33) * adjusted_score_weight
                final_probs['平'] += score_probs.get('平', 0.33) * adjusted_score_weight
                final_probs['负'] += score_probs.get('负', 0.33) * adjusted_score_weight
        
        total = sum(final_probs.values())
        if total == 0:
            return None
        
        final_probs['胜'] /= total
        final_probs['平'] /= total
        final_probs['负'] /= total
        
        max_prob = max(final_probs.values())
        if max_prob < 0.33:
            return None
        
        is_extreme_favorite = False
        if result.get('odds_analysis') and 'wdl' in result['odds_analysis']:
            wdl_features = result['odds_analysis']['wdl'].get('features', {})
            if wdl_features:
                home_prob = wdl_features.get('wdl_implied_prob_home', 0.33)
                away_prob = wdl_features.get('wdl_implied_prob_away', 0.33)
                if home_prob > 0.75 or away_prob > 0.75:
                    is_extreme_favorite = True
        
        if all_favorite and not is_extreme_favorite and upset_risk_score > 0.36 and favorite_prediction:
            sorted_probs = sorted(final_probs.items(), key=lambda x: x[1], reverse=True)
            
            if len(sorted_probs) >= 2 and sorted_probs[0][0] == favorite_prediction:
                upset_options = [p for p in sorted_probs if p[0] != favorite_prediction]
                
                if upset_options:
                    if upset_risk_score > 0.45:
                        if favorite_prediction == '胜':
                            if '负' in [o[0] for o in upset_options]:
                                return '负'
                            return upset_options[-1][0]
                        else:
                            if '胜' in [o[0] for o in upset_options]:
                                return '胜'
                            return upset_options[-1][0]
                    elif upset_risk_score > 0.4:
                        return upset_options[-1][0]
                    elif upset_risk_score > 0.36:
                        if favorite_prediction == '胜':
                            if '负' in [o[0] for o in upset_options]:
                                return '负'
                            return max(upset_options, key=lambda x: x[1])[0]
                        else:
                            if '胜' in [o[0] for o in upset_options]:
                                return '胜'
                            return max(upset_options, key=lambda x: x[1])[0]
        
        if has_disagreement and favorite_prediction:
            ml_pred = result.get('ml_prediction', {}).get('prediction')
            odds_pred = result.get('odds_analysis', {}).get('wdl', {}).get('signal_label')
            
            if ml_pred != odds_pred:
                odds_p_win = wdl_features.get('wdl_implied_prob_home', 0) if wdl_features else 0
                odds_p_lose = wdl_features.get('wdl_implied_prob_away', 0) if wdl_features else 0
                
                odds_favorite = '胜' if odds_p_win > odds_p_lose else '负' if odds_p_lose > odds_p_win else None
                
                if odds_favorite:
                    if odds_pred != odds_favorite:
                        if upset_risk_score > 0.4:
                            if ml_pred == odds_favorite:
                                ml_pred_prob = ml_probs.get(ml_pred, 0)
                                if ml_pred_prob > 0.60:
                                    return ml_pred
                                else:
                                    return odds_pred if odds_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                        elif upset_risk_score > 0.33:
                            if ml_pred == odds_favorite:
                                prob_diff = abs(odds_p_win - odds_p_lose)
                                ml_pred_prob = ml_probs.get(ml_pred, 0)
                                if prob_diff > 0.20 and ml_pred_prob > 0.55:
                                    return ml_pred
                                else:
                                    return odds_pred if odds_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                    else:
                        if ml_pred != odds_favorite:
                            ml_pred_prob = ml_probs.get(ml_pred, 0)
                            odds_favorite_prob = odds_probs.get(odds_favorite, 0)
                            if ml_pred_prob < 0.60 and odds_favorite_prob > 0.35:
                                return odds_favorite
                
                if upset_risk_score > 0.45:
                    if ml_pred == favorite_prediction:
                        return odds_pred if odds_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                    elif odds_pred == favorite_prediction:
                        return ml_pred if ml_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                    else:
                        return odds_pred if odds_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                elif upset_risk_score > 0.4:
                    if ml_pred == favorite_prediction:
                        return odds_pred if odds_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                    elif odds_pred == favorite_prediction:
                        return ml_pred if ml_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                    else:
                        return max(final_probs, key=final_probs.get)
                elif upset_risk_score > 0.33:
                    if ml_pred == favorite_prediction and odds_pred != favorite_prediction:
                        return odds_pred if odds_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
                    elif odds_pred == favorite_prediction and ml_pred != favorite_prediction:
                        return ml_pred if ml_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
        
        if has_disagreement and ml_pred and odds_pred:
            ml_pred_prob = ml_probs.get(ml_pred, 0)
            odds_pred_prob = odds_probs.get(odds_pred, 0)
            
            if ml_pred_prob < 0.60 and odds_pred_prob > 0.35:
                if final_probs.get(ml_pred, 0) - final_probs.get(odds_pred, 0) < 0.10:
                    return odds_pred if odds_pred in ['胜', '平', '负'] else max(final_probs, key=final_probs.get)
        
        max_key = max(final_probs, key=final_probs.get)
        print("DEBUG: final_probs=", {k: round(v, 4) for k, v in final_probs.items()})
        print("DEBUG: weights=", {k: round(v, 4) for k, v in weights.items()})
        print(f"DEBUG: has_disagreement={has_disagreement}, ml_pred={ml_pred}, odds_pred={odds_pred}, favorite={favorite_prediction}, upset_risk={round(upset_risk_score, 4)}")
        return max_key
    
    def _calculate_confidence(self, result):
        confidence = 0
        components = []
        
        if result.get('odds_analysis') and 'wdl' in result['odds_analysis']:
            odds_conf = result['odds_analysis']['wdl'].get('confidence', 0) / 100
            components.append(odds_conf * 0.5)
        
        if result.get('score_prediction'):
            score_conf = result['score_prediction'].get('confidence', 0)
            components.append(score_conf * 0.3)
        
        if result.get('ml_prediction') and 'accuracy' in result['ml_prediction']:
            ml_acc = result['ml_prediction'].get('accuracy', 0)
            components.append(ml_acc * 0.2)
        
        if components:
            confidence = sum(components)
        
        return round(confidence * 100, 1)
    
    def run_full_backtest(self):
        if not self.conn:
            self.connect()
        
        cursor = self.conn.cursor()
        cursor.execute('SELECT match_id FROM matches ORDER BY match_date')
        match_ids = [row[0] for row in cursor.fetchall()]
        
        results = []
        correct_count = 0
        
        for i, match_id in enumerate(match_ids):
            if (i + 1) % 10 == 0:
                print(f"处理进度: {i+1}/{len(match_ids)}")
            
            analysis = self.run_single_match_analysis(match_id)
            results.append(analysis)
            
            if analysis.get('final_prediction') and analysis.get('match_info'):
                if analysis['final_prediction'] == analysis['match_info']['actual_wdl']:
                    correct_count += 1
        
        return results, correct_count, len(match_ids)
    
    def generate_report(self, results, correct_count, total_count):
        print("\n" + "=" * 90)
        print("统一回测报告 - 融合ML模型与赔率分析模块")
        print("=" * 90)
        
        print(f"\n总比赛数: {total_count}")
        print(f"融合预测正确: {correct_count}/{total_count} ({correct_count/total_count*100:.1f}%)")
        
        odds_correct = 0
        ml_correct = 0
        score_correct = 0
        odds_valid = 0
        ml_valid = 0
        score_valid = 0
        
        for r in results:
            match_info = r.get('match_info', {})
            actual_wdl = match_info.get('actual_wdl')
            
            odds_analysis = r.get('odds_analysis')
            if odds_analysis is not None and isinstance(odds_analysis, dict):
                wdl_info = odds_analysis.get('wdl')
                if wdl_info is not None and isinstance(wdl_info, dict):
                    odds_valid += 1
                    odds_pred = wdl_info.get('signal_label')
                    if odds_pred == actual_wdl:
                        odds_correct += 1
            
            ml_prediction = r.get('ml_prediction')
            if ml_prediction is not None and isinstance(ml_prediction, dict):
                if 'prediction' in ml_prediction:
                    ml_valid += 1
                    ml_pred = ml_prediction['prediction']
                    if ml_pred == actual_wdl:
                        ml_correct += 1
            
            score_prediction = r.get('score_prediction')
            if score_prediction is not None and isinstance(score_prediction, dict):
                if score_prediction.get('hit_top1'):
                    score_valid += 1
                    score_correct += 1
        
        print("\n各模块独立准确率:")
        print(f"  赔率分析模块: {odds_correct}/{odds_valid} ({odds_correct/odds_valid*100:.1f}%)" if odds_valid > 0 else "  赔率分析模块: N/A")
        print(f"  ML模型: {ml_correct}/{ml_valid} ({ml_correct/ml_valid*100:.1f}%)" if ml_valid > 0 else "  ML模型: N/A")
        print(f"  比分预测模块: {score_correct}/{score_valid} ({score_correct/score_valid*100:.1f}%)" if score_valid > 0 else "  比分预测模块: N/A")
        
        avg_confidence = sum(r.get('confidence', 0) for r in results) / total_count if total_count > 0 else 0
        print(f"\n平均置信度: {avg_confidence:.1f}%")
        
        print("\n详细结果:")
        print("-" * 90)
        print(f"{'序号':<4} | {'比赛':<40} | {'实际':<6} | {'赔率预测':<6} | {'比分预测':<12} | {'融合预测':<6} | {'正确':<6} | {'置信度':<6}")
        print("-" * 90)
        
        for i, r in enumerate(results, 1):
            match_info = r.get('match_info', {})
            team_display = f"{match_info.get('home_team', '')} vs {match_info.get('away_team', '')}"
            if len(team_display) > 40:
                team_display = team_display[:37] + '...'
            
            odds_analysis = r.get('odds_analysis')
            odds_pred = 'N/A'
            if odds_analysis is not None and isinstance(odds_analysis, dict):
                wdl_info = odds_analysis.get('wdl')
                if wdl_info is not None and isinstance(wdl_info, dict):
                    odds_pred = wdl_info.get('signal_label', 'N/A')
            
            score_prediction = r.get('score_prediction')
            score_display = 'N/A'
            if score_prediction is not None and isinstance(score_prediction, dict):
                top_scores = score_prediction.get('top_scores', [])
                if top_scores and len(top_scores) > 0:
                    first_score = top_scores[0]
                    if isinstance(first_score, (list, tuple)) and len(first_score) > 0:
                        score_display = str(first_score[0]) if first_score[0] is not None else 'N/A'
            
            final_pred = r.get('final_prediction')
            final_pred = str(final_pred) if final_pred is not None else 'N/A'
            actual = match_info.get('actual_wdl')
            actual = str(actual) if actual is not None else 'N/A'
            confidence = r.get('confidence', 0)
            
            is_correct = '✓' if final_pred == actual else '✗'
            
            print(f"{i:<4} | {team_display:<40} | {actual:<6} | {odds_pred:<6} | {score_display:<12} | {final_pred:<6} | {is_correct:<6} | {confidence:<6}%")
        
        summary = {
            'total_matches': total_count,
            'fusion_accuracy': correct_count/total_count*100 if total_count > 0 else 0,
            'odds_accuracy': odds_correct/odds_valid*100 if odds_valid > 0 else 0,
            'ml_accuracy': ml_correct/ml_valid*100 if ml_valid > 0 else 0,
            'score_accuracy': score_correct/score_valid*100 if score_valid > 0 else 0,
            'avg_confidence': avg_confidence
        }
        
        return summary

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='统一回测框架')
    parser.add_argument('--mode', type=str, default='full', help='回测模式')
    parser.add_argument('--model_path', type=str, default=None, help='模型文件路径')
    parser.add_argument('--features_path', type=str, default=None, help='选中特征文件路径')
    parser.add_argument('--scaler_path', type=str, default=None, help='标准化器文件路径')
    args = parser.parse_args()
    
    print("=" * 90)
    print("统一回测框架 - 融合ML模型与赔率分析模块")
    print("=" * 90)
    
    framework = UnifiedBacktestFramework(
        use_ml_model=True,
        use_odds_analysis=True,
        use_score_prediction=True
    )
    
    framework.connect()
    
    print("\n1. 加载训练模型...")
    framework.load_trained_model(model_path=args.model_path, features_path=args.features_path, scaler_path=args.scaler_path)
    
    print("\n2. 运行全库回测...")
    results, correct_count, total_count = framework.run_full_backtest()
    
    print("\n3. 生成报告...")
    summary = framework.generate_report(results, correct_count, total_count)
    
    framework.disconnect()
    
    print("\n" + "=" * 90)
    print("回测完成!")
    print("=" * 90)
    print(f"融合准确率: {summary['fusion_accuracy']:.1f}%")
    
    return summary