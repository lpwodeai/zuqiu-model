import pandas as pd
import numpy as np
import json
import os
import yaml
import sqlite3
from datetime import datetime
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from sklearn.model_selection import TimeSeriesSplit

try:
    import xgboost as xgb
except ImportError:
    xgb = None

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.yaml"

from feature_utils import load_config, load_match_data, load_match_data_odds, build_features, build_team_features

CONFIG = load_config()

TOTAL_GOALS_BINS = [0, 1, 2, 3, 4, 5, 6, 7]
GOAL_DIFF_BINS = [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6]

class MultiTaskTrainer:
    def __init__(self):
        self.models = {}
        self.scaler = None
        self.feature_names = None
        self.task_config = {
            'wdl': {'num_classes': 3, 'objective': 'multi:softprob', 'weight': 0.4},
            'total_goals': {'num_classes': len(TOTAL_GOALS_BINS), 'objective': 'multi:softprob', 'weight': 0.3},
            'goal_diff': {'num_classes': len(GOAL_DIFF_BINS), 'objective': 'multi:softprob', 'weight': 0.3}
        }
    
    def _encode_total_goals(self, total_goals):
        encoded = np.digitize(total_goals, TOTAL_GOALS_BINS) - 1
        encoded = np.clip(encoded, 0, len(TOTAL_GOALS_BINS) - 1)
        return encoded
    
    def _encode_goal_diff(self, goal_diff, dummy=None):
        encoded = np.digitize(goal_diff, GOAL_DIFF_BINS) - 1
        encoded = np.clip(encoded, 0, len(GOAL_DIFF_BINS) - 1)
        return encoded
    
    def _decode_goal_diff(self, encoded):
        return GOAL_DIFF_BINS[encoded] + 0.5
    
    def prepare_multitask_targets(self, df):
        targets = {}
        
        targets['wdl'] = df['result'].values
        
        total_goals = df['total_goals'].fillna(0).astype(int)
        targets['total_goals'] = self._encode_total_goals(total_goals)
        
        goal_diff = df['goal_diff'].fillna(0).astype(int)
        targets['goal_diff'] = self._encode_goal_diff(goal_diff, np.zeros_like(goal_diff))
        
        return targets
    
    def build_shared_features(self, df):
        basic_features = build_features(df)
        team_features = build_team_features(df)
        
        team_features = team_features.drop(columns=[col for col in team_features.columns 
                                                    if col in basic_features.columns])
        
        X = pd.concat([basic_features, team_features], axis=1)
        
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        X_scaled_df = pd.DataFrame(X_scaled, columns=X.columns, index=X.index)
        
        self.feature_names = X.columns.tolist()
        
        return X_scaled_df
    
    def train_shared_encoder(self, X_train, y_train, task_name, params=None):
        if xgb is None:
            return None
        
        task_info = self.task_config[task_name]
        
        if params is None:
            params = {
                'objective': task_info['objective'],
                'num_class': task_info['num_classes'],
                'eval_metric': 'mlogloss',
                'max_depth': CONFIG.get('model', {}).get('xgboost', {}).get('max_depth', 2),
                'learning_rate': CONFIG.get('model', {}).get('xgboost', {}).get('learning_rate', 0.03),
                'subsample': CONFIG.get('model', {}).get('xgboost', {}).get('subsample', 0.6),
                'colsample_bytree': CONFIG.get('model', {}).get('xgboost', {}).get('colsample_bytree', 0.6),
                'gamma': CONFIG.get('model', {}).get('xgboost', {}).get('gamma', 0.5),
                'min_child_weight': CONFIG.get('model', {}).get('xgboost', {}).get('min_child_weight', 10),
                'reg_alpha': CONFIG.get('model', {}).get('xgboost', {}).get('reg_alpha', 1.0),
                'reg_lambda': CONFIG.get('model', {}).get('xgboost', {}).get('reg_lambda', 10.0),
                'seed': 42,
                'nthread': -1
            }
        
        dtrain = xgb.DMatrix(X_train.values, label=y_train)
        
        watchlist = [(dtrain, 'train')]
        model = xgb.train(params, dtrain, 
                          num_boost_round=CONFIG.get('model', {}).get('xgboost', {}).get('num_boost_round', 100),
                          evals=watchlist, verbose_eval=0)
        
        return model
    
    def train_multitask_model(self, X, targets):
        print("=" * 60)
        print("多任务学习模型训练")
        print("=" * 60)
        
        validation_split = CONFIG.get('training', {}).get('validation_split', 0.2)
        train_size = int(len(X) * (1 - validation_split))
        
        X_train, X_val = X.iloc[:train_size], X.iloc[train_size:]
        
        results = {}
        
        for task_name, task_info in self.task_config.items():
            y_train = targets[task_name][:train_size]
            y_val = targets[task_name][train_size:]
            
            print(f"\n训练 {task_name} 任务...")
            
            model = self.train_shared_encoder(X_train, y_train, task_name)
            
            if model:
                dval = xgb.DMatrix(X_val.values, label=y_val)
                y_pred = model.predict(dval)
                y_pred_class = np.argmax(y_pred, axis=1)
                
                accuracy = accuracy_score(y_val, y_pred_class)
                try:
                    ll = log_loss(y_val, y_pred, labels=np.arange(task_info['num_classes']))
                except:
                    ll = log_loss(y_val, y_pred)
                
                print(f"  验证准确率: {accuracy:.4f}, LogLoss: {ll:.4f}")
                
                self.models[task_name] = model
                results[task_name] = {
                    'model': model,
                    'accuracy': accuracy,
                    'log_loss': ll
                }
        
        return results
    
    def predict_multitask(self, X):
        predictions = {}
        
        for task_name, model in self.models.items():
            if isinstance(model, xgb.Booster):
                dmatrix = xgb.DMatrix(X.values if hasattr(X, 'values') else X)
                probs = model.predict(dmatrix)
                predictions[task_name] = {
                    'probabilities': probs,
                    'prediction': np.argmax(probs, axis=1)
                }
        
        predictions['wdl_probs'] = predictions.get('wdl', {}).get('probabilities', np.zeros((len(X), 3)))
        predictions['total_goals_probs'] = predictions.get('total_goals', {}).get('probabilities', np.zeros((len(X), len(TOTAL_GOALS_BINS))))
        predictions['goal_diff_probs'] = predictions.get('goal_diff', {}).get('probabilities', np.zeros((len(X), len(GOAL_DIFF_BINS))))
        
        predictions['score_predictions'] = self._generate_score_predictions(predictions)
        
        return predictions
    
    def _generate_score_predictions(self, predictions):
        wdl_probs = predictions.get('wdl_probs', [])
        tg_probs = predictions.get('total_goals_probs', [])
        gd_probs = predictions.get('goal_diff_probs', [])
        
        score_predictions = []
        
        for i in range(len(wdl_probs)):
            wdl_prob = wdl_probs[i]
            tg_prob = tg_probs[i] if i < len(tg_probs) else np.ones(len(TOTAL_GOALS_BINS)) / len(TOTAL_GOALS_BINS)
            gd_prob = gd_probs[i] if i < len(gd_probs) else np.ones(len(GOAL_DIFF_BINS)) / len(GOAL_DIFF_BINS)
            
            score_probs = {}
            
            for h in range(0, 7):
                for a in range(0, 7):
                    total_g = h + a
                    goal_diff = h - a
                    
                    tg_bin = min(self._encode_total_goals(np.array([total_g]))[0], len(TOTAL_GOALS_BINS) - 1)
                    gd_bin = min(self._encode_goal_diff(np.array([h]), np.array([a]))[0], len(GOAL_DIFF_BINS) - 1)
                    
                    wdl_idx = 2 if h > a else (0 if h < a else 1)
                    
                    combined_prob = wdl_prob[wdl_idx] * tg_prob[tg_bin] * gd_prob[gd_bin]
                    
                    score_probs[f"{h}:{a}"] = combined_prob
            
            total_prob = sum(score_probs.values())
            if total_prob > 0:
                score_probs = {k: v / total_prob for k, v in score_probs.items()}
            
            sorted_scores = sorted(score_probs.items(), key=lambda x: -x[1])[:5]
            score_predictions.append({
                'top_scores': sorted_scores,
                'score_probs': score_probs
            })
        
        return score_predictions
    
    def save_models(self, version=None):
        timestamp = version or datetime.now().strftime('%Y%m%d_%H%M%S')
        
        import joblib
        
        for task_name, model in self.models.items():
            model_path = os.path.join(OUTPUT_DIR, f'multitask_{task_name}_{timestamp}.pkl')
            joblib.dump(model, model_path)
            print(f"保存 {task_name} 模型: {model_path}")
        
        scaler_path = os.path.join(OUTPUT_DIR, f'multitask_scaler_{timestamp}.pkl')
        joblib.dump(self.scaler, scaler_path)
        print(f"保存标准化器: {scaler_path}")
        
        features_path = os.path.join(OUTPUT_DIR, f'multitask_features_{timestamp}.pkl')
        joblib.dump(self.feature_names, features_path)
        print(f"保存特征列表: {features_path}")
        
        config_path = os.path.join(OUTPUT_DIR, f'multitask_config_{timestamp}.json')
        config = {
            'task_config': self.task_config,
            'total_goals_bins': TOTAL_GOALS_BINS,
            'goal_diff_bins': GOAL_DIFF_BINS,
            'timestamp': timestamp
        }
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
        print(f"保存配置: {config_path}")
        
        return timestamp
    
    def load_models(self, version):
        import joblib
        
        self.models = {}
        
        for task_name in self.task_config.keys():
            model_path = os.path.join(OUTPUT_DIR, f'multitask_{task_name}_{version}.pkl')
            if os.path.exists(model_path):
                self.models[task_name] = joblib.load(model_path)
                print(f"加载 {task_name} 模型: {model_path}")
        
        scaler_path = os.path.join(OUTPUT_DIR, f'multitask_scaler_{version}.pkl')
        if os.path.exists(scaler_path):
            self.scaler = joblib.load(scaler_path)
            print(f"加载标准化器: {scaler_path}")
        
        features_path = os.path.join(OUTPUT_DIR, f'multitask_features_{version}.pkl')
        if os.path.exists(features_path):
            self.feature_names = joblib.load(features_path)
            print(f"加载特征列表: {features_path}")
        
        return len(self.models) > 0

def train_multitask_pipeline():
    print("=" * 60)
    print("多任务学习训练Pipeline")
    print("=" * 60)
    
    print("\n1. 加载比赛数据...")
    df = load_match_data_odds()
    print(f"   共加载 {len(df)} 场比赛")
    
    print("\n2. 构建共享特征...")
    trainer = MultiTaskTrainer()
    X = trainer.build_shared_features(df)
    print(f"   特征维度: {X.shape[1]}")
    
    print("\n3. 准备多任务目标...")
    targets = trainer.prepare_multitask_targets(df)
    for task, target in targets.items():
        print(f"   {task}: {len(target)} 个样本")
    
    print("\n4. 训练多任务模型...")
    results = trainer.train_multitask_model(X, targets)
    
    print("\n5. 保存模型...")
    version = trainer.save_models()
    
    print("\n" + "=" * 60)
    print("多任务学习训练完成!")
    print("=" * 60)
    
    for task, metrics in results.items():
        print(f"\n{task}:")
        print(f"  准确率: {metrics['accuracy']:.4f}")
        print(f"  LogLoss: {metrics['log_loss']:.4f}")
    
    return trainer, version

if __name__ == "__main__":
    train_multitask_pipeline()