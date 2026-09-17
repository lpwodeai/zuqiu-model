"""
高级模型训练器 - 阶段三模型修复实现

集成功能:
  D-004: 动态class_weight策略（联赛权重、德比战权重、历史错误率权重）
  D-005: 增强正则化（L1/L2惩罚、早停、dropout率、剪枝）
  D-006: Sklearn API封装的XGBoost/LightGBM训练器
  D-007: 动态权重集成（基于置信度、数据质量、比赛特征）
  D-008: 增强时间序列交叉验证 + 过拟合监控

使用:
  from advanced_model_trainer import AdvancedModelTrainer
  
  trainer = AdvancedModelTrainer()
  results = trainer.train(X_train, y_train, X_val, y_val)
  trainer.plot_overfitting_monitor()
"""

import numpy as np
import pandas as pd
import json
import os
import logging
import warnings
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Any

from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss, f1_score, precision_score, recall_score
from sklearn.preprocessing import StandardScaler

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

# D1: MLflow 实验追踪（可选依赖；未安装时静默跳过，绝不因追踪失败影响训练流程）
try:
    import mlflow
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False

warnings.filterwarnings('ignore')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('ModelTrainer')

# 联赛权重：按联赛前缀匹配（自动兼容多赛季的competition_name）
# 德甲/法甲每赛季仅306场（vs 英/西/意380场），结构性弱势×1.20补偿
# 配置来源：config.yaml.training.league_compensation.weights + league_tier_weight.json
LEAGUE_BASELINE_WEIGHTS = {
    '英超': 1.00,
    '西甲': 1.00,
    '意甲': 1.00,
    '德甲': 1.20,
    '法甲': 1.20,
}

# 旧字典（向后兼容，与新版逻辑取并集）
LEAGUER_WEIGHTS = {
    '英超2025-2026赛季': 1.00,
    '西甲2025-2026赛季': 1.00,
    '意甲2025-2026赛季': 1.00,
    '德甲2025-2026赛季': 1.20,
    '法甲2025-2026赛季': 1.20,
    '英超2024-2025赛季': 1.00,
    '西甲2024-2025赛季': 1.00,
    '意甲2024-2025赛季': 1.00,
    '德甲2024-2025赛季': 1.20,
    '法甲2024-2025赛季': 1.20,
    '英超2023-2024赛季': 1.00,
    '西甲2023-2024赛季': 1.00,
    '意甲2023-2024赛季': 1.00,
    '德甲2023-2024赛季': 1.20,
    '法甲2023-2024赛季': 1.20,
}


def _get_league_weight(league_name: str) -> float:
    """按联赛名前缀匹配权重，兼容任意赛季的中文联赛名"""
    if not league_name:
        return 1.0
    # 优先完整匹配（向后兼容LEAGUER_WEIGHTS字典）
    if league_name in LEAGUER_WEIGHTS:
        return LEAGUER_WEIGHTS[league_name]
    # 前缀匹配（支持跨赛季）
    for prefix, w in LEAGUE_BASELINE_WEIGHTS.items():
        if league_name.startswith(prefix):
            return w
    return 1.0

DERBY_PAIRS = [
    ('利物浦', '曼城'), ('曼城', '利物浦'),
    ('阿森纳', '热刺'), ('热刺', '阿森纳'),
    ('曼联', '利物浦'), ('利物浦', '曼联'),
    ('皇马', '巴萨'), ('巴萨', '皇马'),
    ('拜仁慕尼黑', '多特蒙德'), ('多特蒙德', '拜仁慕尼黑'),
    ('AC米兰', '国际米兰'), ('国际米兰', 'AC米兰'),
]


def _to_serializable(obj):
    if isinstance(obj, dict):
        return {str(k): _to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_to_serializable(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(_to_serializable(v) for v in obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    return obj


class OverfittingMonitor:
    """过拟合监控器 - 追踪训练/验证指标变化"""
    
    def __init__(self):
        self.history = {
            'epochs': [],
            'train_accuracy': [],
            'val_accuracy': [],
            'train_logloss': [],
            'val_logloss': [],
            'train_f1': [],
            'val_f1': [],
            'gap_accuracy': [],
            'gap_logloss': [],
        }
        self.warning_thresholds = {
            'accuracy_gap': 0.15,
            'logloss_gap': 0.30,
            'val_acc_decline': 0.02,
        }
    
    def record(self, epoch, train_metrics, val_metrics):
        self.history['epochs'].append(epoch)
        self.history['train_accuracy'].append(train_metrics['accuracy'])
        self.history['val_accuracy'].append(val_metrics['accuracy'])
        self.history['train_logloss'].append(train_metrics['log_loss'])
        self.history['val_logloss'].append(val_metrics['log_loss'])
        self.history['train_f1'].append(train_metrics.get('f1', 0))
        self.history['val_f1'].append(val_metrics.get('f1', 0))
        
        acc_gap = train_metrics['accuracy'] - val_metrics['accuracy']
        ll_gap = val_metrics['log_loss'] - train_metrics['log_loss']
        
        self.history['gap_accuracy'].append(acc_gap)
        self.history['gap_logloss'].append(ll_gap)
        
        warnings = self._check_overfitting(acc_gap, ll_gap)
        if warnings:
            for w in warnings:
                logger.warning(f"[Epoch {epoch}] 过拟合警告: {w}")
        
        return warnings
    
    def _check_overfitting(self, acc_gap, ll_gap):
        warnings = []
        if acc_gap > self.warning_thresholds['accuracy_gap']:
            warnings.append(f"训练/验证准确率差距过大 ({acc_gap:.4f} > {self.warning_thresholds['accuracy_gap']})")
        if ll_gap > self.warning_thresholds['logloss_gap']:
            warnings.append(f"训练/验证LogLoss差距过大 ({ll_gap:.4f} > {self.warning_thresholds['logloss_gap']})")
        return warnings
    
    def get_summary(self):
        if not self.history['epochs']:
            return {}
        return {
            'total_epochs': len(self.history['epochs']),
            'final_train_acc': self.history['train_accuracy'][-1],
            'final_val_acc': self.history['val_accuracy'][-1],
            'final_train_ll': self.history['train_logloss'][-1],
            'final_val_ll': self.history['val_logloss'][-1],
            'max_acc_gap': max(self.history['gap_accuracy']) if self.history['gap_accuracy'] else 0,
            'avg_acc_gap': np.mean(self.history['gap_accuracy']) if self.history['gap_accuracy'] else 0,
            'overfitting_detected': any(
                g > self.warning_thresholds['accuracy_gap'] 
                for g in self.history['gap_accuracy']
            ),
        }
    
    def generate_report(self):
        summary = self.get_summary()
        if not summary:
            return "无训练记录"
        
        report = []
        report.append("=" * 60)
        report.append("📊 过拟合监控报告")
        report.append("=" * 60)
        report.append(f"  总训练轮数: {summary['total_epochs']}")
        report.append(f"  最终训练准确率: {summary['final_train_acc']:.4f}")
        report.append(f"  最终验证准确率: {summary['final_val_acc']:.4f}")
        report.append(f"  最终训练LogLoss: {summary['final_train_ll']:.4f}")
        report.append(f"  最终验证LogLoss: {summary['final_val_ll']:.4f}")
        report.append(f"  最大准确率差距: {summary['max_acc_gap']:.4f}")
        report.append(f"  平均准确率差距: {summary['avg_acc_gap']:.4f}")
        
        if summary['overfitting_detected']:
            report.append("  ⚠️  检测到过拟合！")
            report.append("  建议: 增加正则化参数、减少max_depth、增加训练数据")
        elif summary['avg_acc_gap'] < 0.05:
            report.append("  ✅ 模型泛化良好")
        else:
            report.append("  ⚠️  存在轻微过拟合倾向")
        
        return "\n".join(report)


class DynamicClassWeightStrategy:
    """动态class_weight调整策略"""
    
    @staticmethod
    def compute_weights(
        y_train: np.ndarray,
        df_train: pd.DataFrame = None,
        league_column: str = 'league',
        home_team_column: str = 'home_team_name',
        away_team_column: str = 'away_team_name',
        anomaly_weight: float = 3.0,
        derby_weight: float = 1.5,
        min_weight: float = 0.5,
        max_weight: float = 5.0,
    ) -> np.ndarray:
        n_classes = 3
        class_counts = np.bincount(y_train, minlength=n_classes)
        total = len(y_train)
        
        base_weights = total / (n_classes * class_counts)
        base_weights = np.clip(base_weights, min_weight, max_weight)
        
        logger.info(f"基础类别权重: {dict(enumerate(base_weights))}")
        logger.info(f"类别分布: {dict(enumerate(class_counts))}")
        
        final_weights = base_weights[y_train].copy()
        
        if df_train is not None and len(df_train) == len(y_train):
            for idx in range(len(y_train)):
                multiplier = 1.0
                
                league = df_train.iloc[idx].get(league_column, '')
                league_w = _get_league_weight(league)
                if league_w != 1.0 or league in LEAGUER_WEIGHTS:
                    multiplier *= league_w
                
                home = str(df_train.iloc[idx].get(home_team_column, ''))
                away = str(df_train.iloc[idx].get(away_team_column, ''))
                for pair in DERBY_PAIRS:
                    if (home in pair[0] and away in pair[1]) or (home in pair[1] and away in pair[0]):
                        multiplier *= derby_weight
                        break
                
                final_weights[idx] = np.clip(
                    final_weights[idx] * multiplier,
                    min_weight,
                    max_weight
                )
            
            logger.info(f"动态权重统计: mean={final_weights.mean():.3f}, std={final_weights.std():.3f}")
            logger.info(f"  范围: [{final_weights.min():.3f}, {final_weights.max():.3f}]")
        
        return final_weights
    
    @staticmethod
    def compute_class_weights_only(y_train: np.ndarray) -> Dict[int, float]:
        n_classes = 3
        class_counts = np.bincount(y_train, minlength=n_classes)
        total = len(y_train)
        weights = total / (n_classes * class_counts)
        return {i: float(w) for i, w in enumerate(weights)}


class SklearnXGBoostTrainer:
    """Sklearn API封装的XGBoost训练器"""
    
    def __init__(self, params: Dict = None):
        self.params = params or self._default_params()
        self.model = None
        self.best_iteration = 0
        self.training_history = []
        
    def _default_params(self):
        return {
            'n_estimators': 200,
            'max_depth': 4,
            'learning_rate': 0.05,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'reg_alpha': 1.0,
            'reg_lambda': 5.0,
            'min_child_weight': 5,
            'gamma': 0.1,
            'random_state': 42,
            'n_jobs': -1,
            'tree_method': 'hist',
            'early_stopping_rounds': 20,
            'eval_metric': 'mlogloss',
            'verbosity': 0,
        }
    
    def fit(self, X_train, y_train, X_val=None, y_val=None, 
            sample_weight=None, class_weight=None, monitor: OverfittingMonitor = None):
        if not XGB_AVAILABLE:
            raise ImportError("XGBoost is not installed")
        
        self.model = xgb.XGBClassifier(**self.params)
        
        fit_params = {}
        if class_weight:
            fit_params['sample_weight'] = self._build_weight_vector(y_train, sample_weight, class_weight)
        elif sample_weight is not None:
            fit_params['sample_weight'] = sample_weight
        
        if X_val is not None and y_val is not None:
            fit_params['eval_set'] = [(X_val, y_val)]
            fit_params['verbose'] = False
        
        self.model.fit(X_train, y_train, **fit_params)
        
        if hasattr(self.model, 'best_iteration'):
            self.best_iteration = self.model.best_iteration
        
        if monitor and X_val is not None:
            self._record_monitor(X_train, y_train, X_val, y_val, monitor)
        
        logger.info(f"XGBoost训练完成: best_iteration={self.best_iteration}")
        logger.info(f"  参数: max_depth={self.params['max_depth']}, lr={self.params['learning_rate']}")
        
        return self
    
    def _build_weight_vector(self, y_train, sample_weight, class_weight):
        weights = np.ones(len(y_train))
        if class_weight:
            for cls, w in class_weight.items():
                weights[y_train == cls] *= w
        if sample_weight is not None:
            weights *= sample_weight
        return weights
    
    def _record_monitor(self, X_train, y_train, X_val, y_val, monitor):
        train_pred = self.model.predict_proba(X_train)
        val_pred = self.model.predict_proba(X_val)
        
        train_metrics = {
            'accuracy': accuracy_score(y_train, np.argmax(train_pred, axis=1)),
            'log_loss': log_loss(y_train, train_pred),
            'f1': f1_score(y_train, np.argmax(train_pred, axis=1), average='macro'),
        }
        val_metrics = {
            'accuracy': accuracy_score(y_val, np.argmax(val_pred, axis=1)),
            'log_loss': log_loss(y_val, val_pred),
            'f1': f1_score(y_val, np.argmax(val_pred, axis=1), average='macro'),
        }
        
        monitor.record(self.best_iteration, train_metrics, val_metrics)
    
    def predict_proba(self, X):
        return self.model.predict_proba(X)
    
    def predict(self, X):
        return self.model.predict(X)
    
    def get_feature_importance(self, top_n=20):
        if self.model is None:
            return {}
        importance = self.model.feature_importances_
        indices = np.argsort(importance)[::-1][:top_n]
        return {f"feature_{i}": importance[i] for i in indices}


class SklearnLightGBMTrainer:
    """Sklearn API封装的LightGBM训练器"""
    
    def __init__(self, params: Dict = None):
        self.params = params or self._default_params()
        self.model = None
        self.best_iteration = 0
        
    def _default_params(self):
        return {
            'n_estimators': 200,
            'max_depth': 5,
            'learning_rate': 0.05,
            'num_leaves': 16,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'reg_alpha': 1.0,
            'reg_lambda': 3.0,
            'min_child_weight': 3,
            'min_data_in_leaf': 20,
            'random_state': 42,
            'n_jobs': -1,
            'boosting_type': 'gbdt',
            'objective': 'multiclass',
            'num_class': 3,
            'metric': 'multi_logloss',
            'early_stopping_round': 20,
            'verbosity': -1,
        }
    
    def fit(self, X_train, y_train, X_val=None, y_val=None,
            sample_weight=None, class_weight=None, monitor: OverfittingMonitor = None):
        if not LGB_AVAILABLE:
            raise ImportError("LightGBM is not installed")
        
        self.model = lgb.LGBMClassifier(**self.params)
        
        fit_params = {}
        if class_weight:
            fit_params['sample_weight'] = self._build_weight_vector(y_train, sample_weight, class_weight)
        elif sample_weight is not None:
            fit_params['sample_weight'] = sample_weight
        
        if X_val is not None and y_val is not None:
            fit_params['eval_set'] = [(X_val, y_val)]
            fit_params['callbacks'] = [
                lgb.early_stopping(stopping_rounds=self.params['early_stopping_round']),
                lgb.log_evaluation(period=0)
            ]
        
        self.model.fit(X_train, y_train, **fit_params)
        
        if hasattr(self.model, 'best_iteration_'):
            self.best_iteration = self.model.best_iteration_
        
        if monitor and X_val is not None:
            self._record_monitor(X_train, y_train, X_val, y_val, monitor)
        
        logger.info(f"LightGBM训练完成: best_iteration={self.best_iteration}")
        logger.info(f"  参数: max_depth={self.params['max_depth']}, lr={self.params['learning_rate']}")
        
        return self
    
    def _build_weight_vector(self, y_train, sample_weight, class_weight):
        weights = np.ones(len(y_train))
        if class_weight:
            for cls, w in class_weight.items():
                weights[y_train == cls] *= w
        if sample_weight is not None:
            weights *= sample_weight
        return weights
    
    def _record_monitor(self, X_train, y_train, X_val, y_val, monitor):
        train_pred = self.model.predict_proba(X_train)
        val_pred = self.model.predict_proba(X_val)
        
        train_metrics = {
            'accuracy': accuracy_score(y_train, np.argmax(train_pred, axis=1)),
            'log_loss': log_loss(y_train, train_pred),
            'f1': f1_score(y_train, np.argmax(train_pred, axis=1), average='macro'),
        }
        val_metrics = {
            'accuracy': accuracy_score(y_val, np.argmax(val_pred, axis=1)),
            'log_loss': log_loss(y_val, val_pred),
            'f1': f1_score(y_val, np.argmax(val_pred, axis=1), average='macro'),
        }
        
        monitor.record(self.best_iteration, train_metrics, val_metrics)
    
    def predict_proba(self, X):
        return self.model.predict_proba(X)
    
    def predict(self, X):
        return self.model.predict(X)
    
    def get_feature_importance(self, top_n=20):
        if self.model is None:
            return {}
        importance = self.model.feature_importances_
        indices = np.argsort(importance)[::-1][:top_n]
        return {f"feature_{i}": importance[i] for i in indices}


class DynamicWeightEnsemble:
    """动态权重集成策略"""
    
    def __init__(self):
        self.model_weights = {'xgb': 0.5, 'lgb': 0.5}
        self.confidence_threshold = 0.6
        self.min_weight = 0.1
        self.max_weight = 0.9
        self.history_accuracies = {'xgb': [], 'lgb': []}
    
    def compute_weights(
        self,
        xgb_proba: np.ndarray,
        lgb_proba: np.ndarray,
        xgb_acc: float = None,
        lgb_acc: float = None,
        match_features: Dict = None,
    ) -> Dict[str, float]:
        if xgb_acc is not None:
            self.history_accuracies['xgb'].append(xgb_acc)
        if lgb_acc is not None:
            self.history_accuracies['lgb'].append(lgb_acc)
        
        if len(self.history_accuracies['xgb']) >= 5:
            recent_xgb = np.mean(self.history_accuracies['xgb'][-5:])
            recent_lgb = np.mean(self.history_accuracies['lgb'][-5:])
            
            total = recent_xgb + recent_lgb
            w_xgb = np.clip(recent_xgb / total, self.min_weight, self.max_weight)
            w_lgb = np.clip(recent_lgb / total, self.min_weight, self.max_weight)
            
            self.model_weights = {'xgb': w_xgb, 'lgb': w_lgb}
            logger.info(f"动态权重更新: XGB={w_xgb:.3f}, LGB={w_lgb:.3f}")
        
        final_weights = self.model_weights.copy()
        
        if match_features:
            confidence_xgb = np.max(xgb_proba, axis=1).mean()
            confidence_lgb = np.max(lgb_proba, axis=1).mean()
            
            if confidence_xgb > self.confidence_threshold * 1.1:
                boost = min(confidence_xgb - 0.6, 0.3)
                final_weights['xgb'] = np.clip(final_weights['xgb'] + boost, self.min_weight, self.max_weight)
                final_weights['lgb'] = 1.0 - final_weights['xgb']
        
        total = sum(final_weights.values())
        final_weights = {k: v / total for k, v in final_weights.items()}
        
        return final_weights
    
    def predict(self, xgb_proba: np.ndarray, lgb_proba: np.ndarray,
                weights: Dict[str, float] = None) -> np.ndarray:
        if weights is None:
            weights = self.model_weights
        
        w_xgb = weights.get('xgb', 0.5)
        w_lgb = weights.get('lgb', 0.5)
        
        return w_xgb * xgb_proba + w_lgb * lgb_proba
    
    def get_weights(self) -> Dict[str, float]:
        return self.model_weights.copy()


class EnhancedTimeSeriesCV:
    """增强型时间序列交叉验证"""
    
    def __init__(self, n_splits: int = 5, min_train_size: int = 50):
        self.n_splits = n_splits
        self.min_train_size = min_train_size
        self.results = []
        self.monitor = OverfittingMonitor()
    
    def run(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        df_meta: pd.DataFrame = None,
        train_fn_xgb=None,
        train_fn_lgb=None,
        feature_names: List[str] = None,
    ) -> Dict[str, Any]:
        tscv = TimeSeriesSplit(n_splits=self.n_splits)
        
        xgb_fold_metrics = []
        lgb_fold_metrics = []
        fold_details = []
        
        logger.info(f"开始 {self.n_splits} 折时间序列交叉验证...")
        logger.info(f"  总样本数: {len(X)}, 最小训练集: {self.min_train_size}")
        
        for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
            y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
            
            logger.info(f"\n--- 第 {fold+1}/{self.n_splits} 折 ---")
            logger.info(f"  训练: {len(X_train)} 样本, 验证: {len(X_val)} 样本")
            
            if len(X_train) < self.min_train_size:
                logger.warning(f"  训练集过小 ({len(X_train)}), 跳过此折")
                continue
            
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_val_scaled = scaler.transform(X_val)
            
            fold_result = {
                'fold': fold + 1,
                'train_size': len(X_train),
                'val_size': len(X_val),
                'train_distribution': dict(zip(*np.unique(y_train, return_counts=True))),
                'val_distribution': dict(zip(*np.unique(y_val, return_counts=True))),
            }
            
            if train_fn_xgb:
                try:
                    xgb_trainer = train_fn_xgb(X_train_scaled, y_train.values, 
                                                X_val_scaled, y_val.values,
                                                fold_index=fold, monitor=self.monitor)
                    if xgb_trainer:
                        xgb_fold_metrics.append(xgb_trainer)
                        fold_result['xgb'] = xgb_trainer
                except Exception as e:
                    logger.error(f"  XGBoost训练失败: {e}")
            
            if train_fn_lgb:
                try:
                    lgb_trainer = train_fn_lgb(X_train_scaled, y_train.values,
                                                X_val_scaled, y_val.values,
                                                fold_index=fold, monitor=self.monitor)
                    if lgb_trainer:
                        lgb_fold_metrics.append(lgb_trainer)
                        fold_result['lgb'] = lgb_trainer
                except Exception as e:
                    logger.error(f"  LightGBM训练失败: {e}")
            
            fold_details.append(fold_result)
        
        summary = self._compute_summary(xgb_fold_metrics, lgb_fold_metrics)
        summary['fold_details'] = fold_details
        summary['overfitting_report'] = self.monitor.generate_report()
        
        logger.info(f"\n{'='*60}")
        logger.info("📊 交叉验证汇总")
        logger.info(f"{'='*60}")
        
        return summary
    
    def _compute_summary(self, xgb_metrics, lgb_metrics):
        summary = {}
        
        if xgb_metrics:
            accs = [m['val_accuracy'] for m in xgb_metrics]
            lls = [m['val_log_loss'] for m in xgb_metrics]
            train_accs = [m['train_accuracy'] for m in xgb_metrics]
            f1s = [m.get('val_f1', 0) for m in xgb_metrics]
            
            summary['xgb'] = {
                'val_accuracy_mean': float(np.mean(accs)),
                'val_accuracy_std': float(np.std(accs)),
                'val_logloss_mean': float(np.mean(lls)),
                'train_accuracy_mean': float(np.mean(train_accs)),
                'overfitting_gap': float(np.mean(train_accs) - np.mean(accs)),
                'val_f1_mean': float(np.mean(f1s)),
                'n_folds': len(accs),
            }
        
        if lgb_metrics:
            accs = [m['val_accuracy'] for m in lgb_metrics]
            lls = [m['val_log_loss'] for m in lgb_metrics]
            train_accs = [m['train_accuracy'] for m in lgb_metrics]
            f1s = [m.get('val_f1', 0) for m in lgb_metrics]
            
            summary['lgb'] = {
                'val_accuracy_mean': float(np.mean(accs)),
                'val_accuracy_std': float(np.std(accs)),
                'val_logloss_mean': float(np.mean(lls)),
                'train_accuracy_mean': float(np.mean(train_accs)),
                'overfitting_gap': float(np.mean(train_accs) - np.mean(accs)),
                'val_f1_mean': float(np.mean(f1s)),
                'n_folds': len(accs),
            }
        
        return summary


class AdvancedModelTrainer:
    """高级模型训练器 - 统一入口"""
    
    def __init__(self, config: Dict = None):
        self.config = config or self._default_config()
        self.overfitting_monitor = OverfittingMonitor()
        self.cv = EnhancedTimeSeriesCV(n_splits=self.config.get('cv_folds', 5))
        self.weight_strategy = DynamicClassWeightStrategy()
        self.ensemble = DynamicWeightEnsemble()
        self.scaler = None
        self.models = {}
        self.training_log = []
        
    def _default_config(self):
        return {
            'cv_folds': 5,
            'validation_split': 0.2,
            'xgboost': {
                'n_estimators': 200,
                'max_depth': 4,
                'learning_rate': 0.05,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
                'reg_alpha': 1.0,
                'reg_lambda': 5.0,
                'gamma': 0.1,
                'min_child_weight': 5,
                'early_stopping_rounds': 20,
            },
            'lightgbm': {
                'n_estimators': 200,
                'max_depth': 5,
                'learning_rate': 0.05,
                'num_leaves': 16,
                'subsample': 0.8,
                'colsample_bytree': 0.8,
                'reg_alpha': 1.0,
                'reg_lambda': 3.0,
                'min_data_in_leaf': 20,
                'early_stopping_round': 20,
            },
            'class_weight': {
                'league_weight': True,
                'derby_weight': True,
                'anomaly_weight': 3.0,
                'derby_multiplier': 1.5,
                'min_weight': 0.5,
                'max_weight': 5.0,
            },
            'ensemble': {
                'method': 'dynamic',
                'confidence_threshold': 0.6,
            },
        }
    
    def _log(self, event: str, data: Dict):
        entry = {
            'timestamp': datetime.now().isoformat(),
            'event': event,
            'data': data,
        }
        self.training_log.append(entry)
        
        try:
            safe_data = _to_serializable(data)
            logger.info(f"[{event}] {json.dumps(safe_data, ensure_ascii=False)}")
        except Exception:
            logger.info(f"[{event}] {event} logged (serialization skipped)")
    
    def train(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        df_meta: pd.DataFrame = None,
    ) -> Dict[str, Any]:
        self._log('TRAINING_START', {
            'n_samples': len(X),
            'n_features': X.shape[1],
            'n_classes': len(np.unique(y)),
            'class_distribution': dict(zip(*np.unique(y, return_counts=True))),
        })
        
        train_size = int(len(X) * (1 - self.config['validation_split']))
        X_train_df = X.iloc[:train_size]
        X_val_df = X.iloc[train_size:]
        y_train = y.iloc[:train_size]
        y_val = y.iloc[train_size:]

        df_train_meta = df_meta.iloc[:train_size] if df_meta is not None else None

        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train_df)
        X_val_scaled = self.scaler.transform(X_val_df)

        X_train = pd.DataFrame(X_train_scaled, columns=X.columns, index=X_train_df.index)
        X_val = pd.DataFrame(X_val_scaled, columns=X.columns, index=X_val_df.index)

        # === [D2 方案] 验证集分布漂移检测 + 分联赛独立分层补偿权重（TV 距离版）===
        # D2 改进：将漂移度量从 KL 散度改为 TV 距离（Total Variation = Σ|p_train - p_val|）。
        # 原因：KL 散度对分布差异的数值偏小（英超单独 KL=0.0264，远低于 0.05 阈值），
        # 而 TV 距离能直观反映分布差异（英超 TV=0.194，即 19.4% 的分布差异）。
        # 阈值 0.10：> 0.10 表示分布差异 ≥ 10% 才触发补偿（英超/意甲/西甲触发，德甲/法甲不触发）。
        DRIFT_TV_THRESHOLD = 0.10  # TV 距离触发阈值
        drift_severity = 'low'
        overall_drift_tv = 0.0
        drift_weights = np.ones(len(y_train), dtype=float)
        train_league_dist = {}
        val_league_dist = {}
        per_league_tv = {}  # 各联赛独立 TV 距离
        triggered_leagues = []  # 触发补偿的联赛列表

        if df_meta is not None and 'league' in df_meta.columns:
            train_league = df_meta.iloc[:train_size]['league'].values
            val_league = df_meta.iloc[train_size:]['league'].values
            y_train_arr = y_train.values
            y_val_arr = y_val.values

            # 联赛单维分布（用于日志可读性）
            train_league_dist = pd.Series(train_league).value_counts(normalize=True).to_dict()
            val_league_dist = pd.Series(val_league).value_counts(normalize=True).to_dict()

            # === 整体 [联赛, y] 联合分布 TV 距离 ===
            train_joint = pd.Series(
                [f"{lg}_{yy}" for lg, yy in zip(train_league, y_train_arr)]
            ).value_counts(normalize=True)
            val_joint = pd.Series(
                [f"{lg}_{yy}" for lg, yy in zip(val_league, y_val_arr)]
            ).value_counts(normalize=True)
            overall_drift_tv = 0.0
            all_keys = sorted(set(train_joint.index) | set(val_joint.index))
            for k in all_keys:
                overall_drift_tv += abs(float(train_joint.get(k, 0)) - float(val_joint.get(k, 0)))

            # === 分联赛独立 [y] 分布 TV 距离（D2 方案核心）===
            unique_leagues = sorted(set(train_league) | set(val_league))
            for lg in unique_leagues:
                tr_mask = train_league == lg
                vl_mask = val_league == lg
                if tr_mask.sum() == 0 or vl_mask.sum() == 0:
                    per_league_tv[lg] = 0.0
                    continue
                tr_dist = pd.Series(y_train_arr[tr_mask]).value_counts(normalize=True)
                vl_dist = pd.Series(y_val_arr[vl_mask]).value_counts(normalize=True)
                tv = 0.0
                for c in sorted(set(tr_dist.index) | set(vl_dist.index)):
                    tv += abs(float(tr_dist.get(c, 0)) - float(vl_dist.get(c, 0)))
                per_league_tv[lg] = float(tv)
                if tv > DRIFT_TV_THRESHOLD:
                    triggered_leagues.append(lg)

            # 严重度按"最严重的联赛"判定
            worst_tv = max(per_league_tv.values()) if per_league_tv else 0.0
            drift_severity = 'high' if worst_tv > 0.15 else ('medium' if worst_tv > DRIFT_TV_THRESHOLD else 'low')

            logger.info(f"[Drift-D2] 整体TV={overall_drift_tv:.4f} | 分联赛TV={per_league_tv}")
            logger.info(f"[Drift-D2] 最严重联赛TV={worst_tv:.4f} 严重度={drift_severity} 阈值={DRIFT_TV_THRESHOLD}")
            logger.info(f"[Drift-D2] 触发补偿的联赛={triggered_leagues}")

            # === 分联赛独立补偿权重 ===
            # 对每个触发联赛，给训练集里"验证集占比偏高"的 y 类别加权
            if triggered_leagues:
                for lg in triggered_leagues:
                    tr_mask = train_league == lg
                    vl_mask = val_league == lg
                    tr_dist = pd.Series(y_train_arr[tr_mask]).value_counts(normalize=True)
                    vl_dist = pd.Series(y_val_arr[vl_mask]).value_counts(normalize=True)
                    # 对该联赛的训练样本逐个加权
                    for i in np.where(tr_mask)[0]:
                        yy = y_train_arr[i]
                        p_val = float(vl_dist.get(yy, 0))
                        p_train = float(tr_dist.get(yy, 0))
                        if p_train > 0:
                            # 比值越大说明该 (联赛, y) 组合在验证集中占比偏高，训练时加权
                            w = min(2.0, 0.5 + (p_val / max(p_train, 1e-6)) * 0.5)
                            drift_weights[i] = drift_weights[i] * w
                logger.info(f"[Drift-D2] 已启用分联赛补偿，drift_weights 范围 "
                            f"[{drift_weights.min():.3f}, {drift_weights.max():.3f}] "
                            f"非1.0样本数={int((drift_weights != 1.0).sum())}")
            else:
                logger.info(f"[Drift-D2] 无联赛触发补偿(阈值{DRIFT_TV_THRESHOLD})，drift_weights 全为 1.0")

        self._log('DATA_SPLIT', {
            'train_size': len(X_train),
            'val_size': len(X_val),
            'train_distribution': dict(zip(*np.unique(y_train, return_counts=True))),
            'val_distribution': dict(zip(*np.unique(y_val, return_counts=True))),
            'train_league_dist': train_league_dist,
            'val_league_dist': val_league_dist,
            'drift_tv_divergence': float(overall_drift_tv),
            'drift_severity': drift_severity,
            'per_league_tv': per_league_tv,
            'triggered_leagues': triggered_leagues,
            'worst_league_tv': float(worst_tv) if (df_meta is not None and 'league' in df_meta.columns) else 0.0,
            'drift_metric': 'TV_distance',
            'drift_threshold': DRIFT_TV_THRESHOLD,
        })

        # ============================================================
        # [sample_time_decay] 方案C/方案D 样本时间衰减权重
        # 与 class_weight / drift_weights 相乘，不破坏现有权重分布
        # ============================================================
        time_decay_weights = np.ones(len(y_train), dtype=float)
        sample_decay_info = None
        sample_decay_cfg = self.config.get('sample_time_decay')
        if sample_decay_cfg and sample_decay_cfg.get('enabled', False) and df_meta is not None and 'date' in df_meta.columns:
            try:
                train_dates = df_train_meta['date'].values if df_train_meta is not None else None
                if train_dates is not None and len(train_dates) == len(y_train):
                    pd_avail = 'pd' in globals() or 'pandas' in [n.split('.')[0] for n in globals()]
                    ref_date = max(train_dates)
                    all_dates_for_count = df_meta['date'].values
                    recent_1yr_count = int(np.sum(
                        np.array([(ref_date - d).days if hasattr(d, 'days') else (pd.Timestamp(ref_date) - pd.Timestamp(d)).days
                                  for d in all_dates_for_count]) <= 365
                    )) if len(all_dates_for_count) > 0 else 0
                    # 自动模式：在方案C和方案D之间切换
                    # 赛中切换逻辑：
                    #   方案C_mild  → 休赛期 / 赛季上半段 (8月-11月)，或新赛季样本不足
                    #   方案D_balanced → 赛季下半段 (12月-次年5月) **且** 近赛季样本 ≥ 阈值
                    # 用户要求：必须两个条件同时满足才切D，不能仅靠样本量单条件
                    auto_mode = sample_decay_cfg.get('auto_mode', False)
                    threshold = int(sample_decay_cfg.get('mid_season_sample_threshold', 1000))
                    manual = sample_decay_cfg.get('manual', {})
                    # 日历赛季阶段判定：以【脚本执行当天】所在月份判断
                    #   → 因为训练数据永远滞后于真实当下（如8月初训练，训练集最新数据只到5月赛季结束）
                    #   → 用【执行日月份】判断是否已进入赛季下半段，才符合用户的休赛/开赛节奏
                    import datetime as _dt
                    try:
                        ref_month = int(_dt.date.today().month)
                    except Exception:
                        try:
                            ref_month = pd.Timestamp.now().month
                        except Exception:
                            ref_month = 8  # 默认8月=新赛季上半段
                    # 五大联赛赛季：8月开赛 → 12月进入冬歇/下半段 → 次年5月结束
                    # 赛季下半段：month in 12,1,2,3,4,5
                    in_mid_season_half = ref_month >= 12 or ref_month <= 5
                    active_cfg = None
                    active_profile = 'manual'
                    if manual.get('enabled', False):
                        active_cfg = manual
                        hl_ = int(manual.get('half_life_days', 365))
                        if hl_ <= 200:
                            active_profile = 'D_balanced'
                        else:
                            active_profile = 'C_mild'
                    elif auto_mode:
                        sample_ready = recent_1yr_count >= threshold
                        calendar_ready = in_mid_season_half
                        if sample_ready and calendar_ready and sample_decay_cfg.get('profile_d', {}).get('enabled', True):
                            active_cfg = sample_decay_cfg['profile_d']
                            active_profile = 'D_balanced'
                        elif sample_decay_cfg.get('profile_c', {}).get('enabled', True):
                            active_cfg = sample_decay_cfg['profile_c']
                            active_profile = 'C_mild'
                    else:
                        if sample_decay_cfg.get('profile_c', {}).get('enabled', True):
                            active_cfg = sample_decay_cfg['profile_c']
                            active_profile = 'C_mild'
                    if active_cfg is None:
                        active_cfg = {'decay_type': 'exponential', 'half_life_days': 365,
                                       'min_weight': 0.01, 'max_history_days': 1095}
                    hl = int(active_cfg.get('half_life_days', 365))
                    mh = int(active_cfg.get('max_history_days', 1095))
                    minw = float(active_cfg.get('min_weight', 0.01))
                    dtype_ = active_cfg.get('decay_type', 'exponential')
                    for i in range(len(train_dates)):
                        d = train_dates[i]
                        try:
                            if hasattr(d, 'days'):
                                diff = (ref_date - d).days
                            else:
                                diff = (pd.Timestamp(ref_date) - pd.Timestamp(d)).days
                        except Exception:
                            diff = 0
                        if diff < 0:
                            diff = 0
                        if diff > mh:
                            w = 0.0
                        else:
                            if dtype_ == 'exponential':
                                w = float(np.exp(-max(diff, 0.001) * np.log(2) / hl))
                            elif dtype_ == 'linear':
                                w = max(0.0, 1.0 - diff / mh)
                            else:
                                w = 1.0
                            w = max(w, minw)
                        time_decay_weights[i] = w
                    # 归一化：保持平均权重≈1.0，避免时间衰减稀释全局权重
                    nonzero_mask = time_decay_weights > 0
                    if nonzero_mask.any():
                        avg_w = float(np.mean(time_decay_weights[nonzero_mask]))
                        if avg_w > 1e-6:
                            time_decay_weights = time_decay_weights / avg_w
                    # Kish有效样本量统计
                    pos = time_decay_weights > 0
                    if pos.any():
                        w_sum = float(np.sum(time_decay_weights[pos]))
                        w2_sum = float(np.sum(time_decay_weights[pos] ** 2))
                        kish_n = w_sum * w_sum / w2_sum if w2_sum > 1e-9 else 0.0
                    else:
                        kish_n = 0.0
                    sample_decay_info = {
                        'profile': active_profile,
                        'half_life_days': hl,
                        'max_history_days': mh,
                        'decay_type': dtype_,
                        'min_weight': minw,
                        'recent_1yr_sample_count': recent_1yr_count,
                        'auto_mode': bool(auto_mode),
                        'mid_season_threshold': threshold,
                        'ref_month': int(ref_month) if 'ref_month' in locals() else None,
                        'in_mid_season_half': bool(in_mid_season_half) if 'in_mid_season_half' in locals() else None,
                        'switch_decision': (
                            f"ref_date={pd.Timestamp(ref_date).strftime('%Y-%m-%d')} (月={ref_month if 'ref_month' in locals() else '?'}, "
                            f"下半段={in_mid_season_half if 'in_mid_season_half' in locals() else '?'}) + "
                            f"近赛季样本{recent_1yr_count} vs 阈值{threshold} => "
                            f"{'已切方案D' if active_profile == 'D_balanced' else '维持/切至方案C'}"
                        ),
                        'kish_effective_n': float(kish_n),
                        'weight_mean': float(np.mean(time_decay_weights)),
                        'weight_std': float(np.std(time_decay_weights)),
                        'weight_min': float(np.min(time_decay_weights)),
                        'weight_max': float(np.max(time_decay_weights)),
                        'zero_weight_count': int(np.sum(time_decay_weights == 0)),
                    }
                    logger.info(f"[SampleTimeDecay] profile={active_profile} hl={hl}d mh={mh}d "
                                f"ref_month={ref_month if 'ref_month' in locals() else '?'} "
                                f"mid_season_half={in_mid_season_half if 'in_mid_season_half' in locals() else '?'} "
                                f"近赛季样本={recent_1yr_count} 阈值={threshold} Kish有效n={kish_n:.0f}")
            except Exception as exc:
                logger.warning(f"[SampleTimeDecay] 计算失败，回退至全量等权: {exc}")
                time_decay_weights = np.ones(len(y_train), dtype=float)
                sample_decay_info = {'error': str(exc)}

        class_weight_config = self.config['class_weight']
        sample_weights = self.weight_strategy.compute_weights(
            y_train.values,
            df_train_meta,
            anomaly_weight=class_weight_config['anomaly_weight'],
            derby_weight=class_weight_config['derby_multiplier'],
            min_weight=class_weight_config['min_weight'],
            max_weight=class_weight_config['max_weight'],
        )
        # 应用漂移补偿权重（与 league/derby/anomaly 权重相乘）
        if df_meta is not None and 'league' in df_meta.columns:
            sample_weights = sample_weights * drift_weights
        # 应用样本时间衰减权重（方案C/方案D）
        sample_weights = sample_weights * time_decay_weights
        class_weights_dict = self.weight_strategy.compute_class_weights_only(y_train.values)

        class_weights_log = {
            'class_weights': class_weights_dict,
            'sample_weight_stats': {
                'mean': float(np.mean(sample_weights)),
                'std': float(np.std(sample_weights)),
                'min': float(np.min(sample_weights)),
                'max': float(np.max(sample_weights)),
            }
        }
        if sample_decay_info is not None:
            class_weights_log['sample_time_decay'] = sample_decay_info
        self._log('CLASS_WEIGHTS', class_weights_log)
        
        cv_results = self._run_cv(X, y, df_meta)
        
        xgb_trainer, xgb_metrics = self._train_xgboost(
            X_train, y_train, X_val, y_val,
            sample_weights, class_weights_dict,
            is_final=True
        )
        lgb_trainer, lgb_metrics = self._train_lightgbm(
            X_train, y_train, X_val, y_val,
            sample_weights, class_weights_dict,
            is_final=True
        )
        
        self.models['xgb'] = xgb_trainer
        self.models['lgb'] = lgb_trainer
        
        ensemble_weights = self.ensemble.compute_weights(
            xgb_proba=xgb_trainer.predict_proba(X_val.values) if xgb_trainer else None,
            lgb_proba=lgb_trainer.predict_proba(X_val.values) if lgb_trainer else None,
            xgb_acc=xgb_metrics['val_accuracy'] if xgb_metrics else 0,
            lgb_acc=lgb_metrics['val_accuracy'] if lgb_metrics else 0,
        )
        
        final_proba = self.ensemble.predict(
            xgb_trainer.predict_proba(X_val.values) if xgb_trainer else np.zeros((len(X_val), 3)),
            lgb_trainer.predict_proba(X_val.values) if lgb_trainer else np.zeros((len(X_val), 3)),
        )
        final_pred = np.argmax(final_proba, axis=1)
        final_acc = accuracy_score(y_val, final_pred)
        
        self._log('ENSEMBLE_RESULT', {
            'weights': ensemble_weights,
            'final_val_accuracy': float(final_acc),
        })

        # ============================================================
        # [Score Evaluation] 精确比分评估 + Proper Scoring Rules
        # 论文策略 1+6: Top-1/Top-3 命中率 + RPS/Brier/LogLoss
        # ============================================================
        score_eval = self._evaluate_score_metrics(
            final_proba, y_val, df_train_meta, df_meta, train_size
        )
        self._log('SCORE_EVALUATION', score_eval)

        result = {
            'models': self.models,
            'scaler': self.scaler,
            'cv_results': cv_results,
            'xgb_metrics': xgb_metrics,
            'lgb_metrics': lgb_metrics,
            'ensemble_weights': ensemble_weights,
            'final_val_accuracy': float(final_acc),
            'score_evaluation': score_eval,
            'overfitting_report': self.overfitting_monitor.generate_report(),
            'training_log': self.training_log,
            'class_weights': class_weights_dict,
        }
        
        self._log('TRAINING_COMPLETE', {
            'final_val_accuracy': float(final_acc),
            'ensemble_weights': ensemble_weights,
            'score_evaluation': score_eval,
        })

        # === D1: MLflow 实验追踪（可选）===
        # mlflow 未安装或记录失败时静默跳过，绝不因追踪失败中断训练流程。
        if MLFLOW_AVAILABLE:
            try:
                run_name = f"train_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                with mlflow.start_run(run_name=run_name):
                    # ---- 参数记录（关键超参数，取自 self.config）----
                    mlflow.log_param('experiment', 'D1_MLflow_Tracking')
                    # P1-E: 可复现快照绑定（git commit / 数据源版本 / 特征集版本 hash）
                    from pathlib import Path
                    from mlflow_repro import log_repro_snapshot
                    log_repro_snapshot(Path(__file__).resolve().parent.parent, list(X.columns))
                    mlflow.log_param('n_samples', int(len(X)))
                    mlflow.log_param('feature_dim', int(X.shape[1]))
                    mlflow.log_param('n_classes', int(len(np.unique(y))))
                    mlflow.log_param('validation_split', float(self.config['validation_split']))
                    mlflow.log_param('cv_folds', int(self.config.get('cv_folds', 5)))
                    for _model_key in ('xgboost', 'lightgbm'):
                        for _k, _v in self.config.get(_model_key, {}).items():
                            if isinstance(_v, (int, float, str, bool)):
                                mlflow.log_param(f'{_model_key}_{_k}', _v)
                    # ---- 指标记录（accuracy / log_loss / RPS）----
                    if xgb_metrics:
                        mlflow.log_metric('xgb_val_accuracy', float(xgb_metrics.get('val_accuracy', 0)))
                        mlflow.log_metric('xgb_val_log_loss', float(xgb_metrics.get('val_log_loss', 0)))
                    if lgb_metrics:
                        mlflow.log_metric('lgb_val_accuracy', float(lgb_metrics.get('val_accuracy', 0)))
                        mlflow.log_metric('lgb_val_log_loss', float(lgb_metrics.get('val_log_loss', 0)))
                    mlflow.log_metric('ensemble_val_accuracy', float(final_acc))
                    psr = (score_eval or {}).get('proper_scoring_rules', {})
                    if psr:
                        mlflow.log_metric('ensemble_rps', float(psr.get('rps', 0)))
                        mlflow.log_metric('ensemble_log_loss', float(psr.get('log_loss', 0)))
                        mlflow.log_metric('ensemble_brier_score', float(psr.get('brier_score', 0)))
                    print(f"   [MLflow] 实验追踪完成: run={run_name}")
            except Exception as e:
                logger.warning(f"[MLflow] 实验追踪失败（不影响训练）: {e}")
        
        return result

    @staticmethod
    def _calculate_xg_features(df_meta, val_meta):
        """
        xG 特征工程 V2: 球队历史 + 赔率隐含 + 大球概率增强
        
        融合策略 (保守版):
        1. 球队历史 xG: 主队主场得分 + 客队客场失分
        2. 赔率隐含 xG: tg_expected 总进球预期 (仅 20% 权重)
        3. 相对大球信号: 当 tg_over_25_prob 高于中位数时适度增强
        4. 胜负比例微调: 仅在极端胜负概率时调整主客队比例
        
        核心改进 (方案C - 修复版):
        - 使用相对阈值而非绝对阈值
        - 限制最大放大幅度 (20%)
        - 保证 xG 值在合理范围
        """
        import numpy as np
        
        if df_meta is None or val_meta is None:
            return None, None
        
        n_val = len(val_meta)
        xg_home = np.ones(n_val) * 1.4
        xg_away = np.ones(n_val) * 1.1
        
        try:
            # 获取训练集统计信息
            if 'homeGoals' in df_meta.columns and 'awayGoals' in df_meta.columns:
                home_avg = float(df_meta['homeGoals'].mean())
                away_avg = float(df_meta['awayGoals'].mean())
            else:
                home_avg, away_avg = 1.4, 1.1
            
            # 获取验证集的球队信息（如果可用）
            if 'home_team_name' in val_meta.columns and 'away_team_name' in val_meta.columns:
                # 计算球队级别的历史统计
                home_teams = val_meta['home_team_name'].values
                away_teams = val_meta['away_team_name'].values
                
                if 'home_team_name' in df_meta.columns and 'away_team_name' in df_meta.columns:
                    home_goals_by_team = df_meta.groupby('home_team_name')['homeGoals'].mean().to_dict()
                    away_goals_by_team = df_meta.groupby('away_team_name')['awayGoals'].mean().to_dict()
                    home_concede_by_team = df_meta.groupby('home_team_name')['awayGoals'].mean().to_dict()
                    away_concede_by_team = df_meta.groupby('away_team_name')['homeGoals'].mean().to_dict()
                    
                    for i in range(n_val):
                        ht = home_teams[i]
                        at = away_teams[i]
                        
                        # 主队 xG: 主队主场得分能力 vs 客队客场失分能力
                        h_home_attack = home_goals_by_team.get(ht, home_avg)
                        a_away_concede = away_concede_by_team.get(at, away_avg)
                        
                        # 客队 xG: 客队客场得分能力 vs 主队主场失分能力
                        a_away_attack = away_goals_by_team.get(at, away_avg)
                        h_home_concede = home_concede_by_team.get(ht, home_avg)
                        
                        # 融合: 使用 Team-specific 统计
                        xg_home[i] = 0.5 * h_home_attack + 0.5 * a_away_concede
                        xg_away[i] = 0.5 * a_away_attack + 0.5 * h_home_concede
            
            # ========== 方案C (修复版): 赔率特征增强 ==========
            
            # 1. 使用 tg_expected (赔率隐含总进球) 调整 xG 总量
            if 'tg_expected' in val_meta.columns:
                tg_expected = val_meta['tg_expected'].values
                for i in range(n_val):
                    if pd.notna(tg_expected[i]) and tg_expected[i] > 0:
                        total_xg_hist = xg_home[i] + xg_away[i]
                        if total_xg_hist > 0:
                            ratio_home = xg_home[i] / total_xg_hist
                            # 80% 历史 + 20% 赔率隐含 (保守融合)
                            xg_home[i] = 0.8 * xg_home[i] + 0.2 * tg_expected[i] * ratio_home
                            xg_away[i] = 0.8 * xg_away[i] + 0.2 * tg_expected[i] * (1 - ratio_home)
                        else:
                            xg_home[i] = tg_expected[i] * 0.5
                            xg_away[i] = tg_expected[i] * 0.5
            
            # 2. 使用相对大球信号增强 xG (仅增强高于中位数的场次)
            if 'tg_over_25_prob' in val_meta.columns:
                tg_over_25 = val_meta['tg_over_25_prob'].values
                valid_mask = pd.notna(tg_over_25)
                if valid_mask.any():
                    # 计算中位数和 75% 分位数作为相对基准
                    median_val = np.median(tg_over_25[valid_mask])
                    p75_val = np.percentile(tg_over_25[valid_mask], 75)
                    logger.info(f"[xG] tg_over_25_prob 统计: median={median_val:.3f}, p75={p75_val:.3f}")
                    
                    for i in range(n_val):
                        if pd.notna(tg_over_25[i]):
                            # 仅当高于 75% 分位数时才增强
                            if tg_over_25[i] > p75_val and p75_val > median_val:
                                # 归一化: 0 表示刚好超过 p75, 1 表示最大值
                                signal = (tg_over_25[i] - p75_val) / max(tg_over_25.max() - p75_val, 0.01)
                                signal = np.clip(signal, 0, 1)
                                # 最大增强 20%
                                boost_factor = 1.0 + 0.2 * signal
                                xg_home[i] *= boost_factor
                                xg_away[i] *= boost_factor
            
            # 3. 使用胜负隐含比例微调主客队分配 (仅极端情况)
            if 'wdl_implied_win' in val_meta.columns and 'wdl_implied_lose' in val_meta.columns:
                wdl_win = val_meta['wdl_implied_win'].values
                wdl_lose = val_meta['wdl_implied_lose'].values
                for i in range(n_val):
                    if pd.notna(wdl_win[i]) and pd.notna(wdl_lose[i]):
                        total_prob = wdl_win[i] + wdl_lose[i] + 1e-6
                        win_ratio = wdl_win[i] / total_prob
                        # 仅当一方胜率 > 0.6 或 < 0.2 时才调整
                        if win_ratio > 0.6 or win_ratio < 0.2:
                            total_xg = xg_home[i] + xg_away[i]
                            if total_xg > 0:
                                hist_ratio = xg_home[i] / total_xg
                                # 极端情况下，30% 依据赔率信号调整
                                new_ratio = (1 - 0.3) * hist_ratio + 0.3 * win_ratio
                                xg_home[i] = total_xg * new_ratio
                                xg_away[i] = total_xg * (1 - new_ratio)
            
            # 限制合理范围 (保守范围: 0.2-5.0)
            xg_home = np.clip(xg_home, 0.2, 5.0)
            xg_away = np.clip(xg_away, 0.2, 5.0)
            
            # 记录增强效果
            logger.info(f"[xG] 增强后统计: xg_home_mean={xg_home.mean():.3f}, xg_away_mean={xg_away.mean():.3f}")
            
        except Exception as e:
            logger.warning(f"[xG] 特征计算异常: {e}, 使用默认值")
            import traceback
            traceback.print_exc()
        
        return xg_home, xg_away
    
    @staticmethod
    def _calculate_team_style_features(df_meta, val_meta):
        """
        球队攻防风格特征 V5: 区分 0-0 和 2-1 等中比分场景
        
        核心思路:
        - 0-0 多发生在双方防守强、进攻弱的比赛
        - 2-1/1-2 多发生在双方进攻强或有攻强守弱球队的比赛
        - 通过球队历史场均进球/失球构建风格信号
        
        返回:
            defensive_solidity: 防守稳固度 (越低越防守 → 更可能 0-0)
            attacking_intensity: 进攻强度 (越高越进攻 → 更可能中高比分)
            style_00_affinity: 0-0 倾向度 (越高越可能 0-0)
        """
        import numpy as np
        
        if df_meta is None or val_meta is None:
            return None, None, None
        
        n_val = len(val_meta)
        defensive_solidity = np.ones(n_val) * 1.3  # 默认中等防守
        attacking_intensity = np.ones(n_val) * 1.3  # 默认中等进攻
        style_00_affinity = np.ones(n_val) * 0.5   # 默认中等 0-0 倾向
        
        try:
            if 'home_team_name' not in val_meta.columns or 'away_team_name' not in val_meta.columns:
                return None, None, None
            if 'home_team_name' not in df_meta.columns or 'away_team_name' not in df_meta.columns:
                return None, None, None
            
            # 球队级别历史统计
            home_attack = df_meta.groupby('home_team_name')['homeGoals'].agg(['mean', 'std']).to_dict('index')
            away_attack = df_meta.groupby('away_team_name')['awayGoals'].agg(['mean', 'std']).to_dict('index')
            home_defense = df_meta.groupby('home_team_name')['awayGoals'].agg(['mean', 'std']).to_dict('index')
            away_defense = df_meta.groupby('away_team_name')['homeGoals'].agg(['mean', 'std']).to_dict('index')
            
            home_teams = val_meta['home_team_name'].values
            away_teams = val_meta['away_team_name'].values
            
            global_home_attack = float(df_meta['homeGoals'].mean())
            global_away_attack = float(df_meta['awayGoals'].mean())
            global_home_defense = global_away_attack  # 主队失球 = 客队进球均值
            global_away_defense = global_home_attack  # 客队失球 = 主队进球均值
            
            for i in range(n_val):
                ht = home_teams[i]
                at = away_teams[i]
                
                # 主队: 主场进攻能力 + 主场防守能力
                h_attack = home_attack.get(ht, {}).get('mean', global_home_attack)
                h_defense = home_defense.get(ht, {}).get('mean', global_home_defense)
                
                # 客队: 客场进攻能力 + 客场防守能力
                a_attack = away_attack.get(at, {}).get('mean', global_away_attack)
                a_defense = away_defense.get(at, {}).get('mean', global_away_defense)
                
                # 防守稳固度: 双方失球均值 (越低越防守)
                defensive_solidity[i] = (h_defense + a_defense) / 2.0
                
                # 进攻强度: 双方进球均值 (越高越进攻)
                attacking_intensity[i] = (h_attack + a_attack) / 2.0
                
                # 0-0 倾向度: 防守越强 + 进攻越弱 → 越可能 0-0
                # 归一化到 0-1 范围: defensive_solidity 越低, attacking_intensity 越低 → affinity 越高
                # 使用反比: affinity = 1 - (defensive + attacking) / scale
                total_style = defensive_solidity[i] + attacking_intensity[i]
                # total_style 范围约 1.0-4.0, 越低越可能 0-0
                style_00_affinity[i] = max(0.0, min(1.0, 1.0 - (total_style - 2.0) / 2.0))
            
            logger.info(f"[TeamStyle] 防守稳固度: mean={defensive_solidity.mean():.3f}, "
                        f"std={defensive_solidity.std():.3f}")
            logger.info(f"[TeamStyle] 进攻强度: mean={attacking_intensity.mean():.3f}, "
                        f"std={attacking_intensity.std():.3f}")
            logger.info(f"[TeamStyle] 0-0倾向度: mean={style_00_affinity.mean():.3f}, "
                        f"Q25={np.percentile(style_00_affinity, 25):.3f}, "
                        f"Q75={np.percentile(style_00_affinity, 75):.3f}")
            
        except Exception as e:
            logger.warning(f"[TeamStyle] 特征计算异常: {e}")
            import traceback
            traceback.print_exc()
        
        return defensive_solidity, attacking_intensity, style_00_affinity
    
    @staticmethod
    def _get_scenario_params(lambda_sum, probs_hda_i):
        """
        分场景参数回退机制 V2: 根据预测的进球量级选择最优参数
        
        场景划分 (方案A 实施):
        - 极低进球场景 (λ_sum < 1.5): 0-0 等 — 使用极保守参数
        - 低进球场景 (1.5 ≤ λ_sum < 2.0): 1-0, 0-1 等 — 使用保守参数
        - 中进球场景 (2.0 ≤ λ_sum < 3.0): 2-1, 1-2, 2-2 等 — 使用平衡参数
        - 高进球场景 (λ_sum ≥ 3.0): 3-2, 4-3 等 — 使用激进参数
        
        返回: (rho, boost) 元组
        """
        # 获取胜负概率
        p_home = probs_hda_i[0]  # 主胜概率
        p_away = probs_hda_i[2]  # 客胜概率
        
        if lambda_sum < 1.5:
            # 极低进球场景: 极保守参数，专门优化 0-0 预测
            # 针对 0-0 场景: 极小 boost 避免过度放大，极负 ρ 修正倾向
            rho = -0.40
            boost = 1.0
        elif lambda_sum < 2.0:
            # 低进球场景: 保守参数
            rho = -0.35
            boost = 1.5
        elif lambda_sum < 3.0:
            # 中进球场景: 平衡参数
            rho = -0.30
            boost = 2.0
        else:
            # 高进球场景: 激进参数，覆盖更多比分
            rho = -0.25
            boost = 2.6
        
        return rho, boost

    @staticmethod
    def _build_score_matrix(lambda_h, lambda_a, rho, max_goals=7):
        """构建 Dixon-Coles 比分矩阵 (静态辅助方法，网格搜索和评估共用)"""
        import math as _math

        def _poisson_pmf(k, lam):
            if lam <= 0:
                return 1.0 if k == 0 else 0.0
            return (_math.exp(-lam) * lam**k) / _math.factorial(k)

        matrix = np.zeros((max_goals + 1, max_goals + 1))
        for x in range(max_goals + 1):
            for y in range(max_goals + 1):
                p_x = _poisson_pmf(x, lambda_h)
                p_y = _poisson_pmf(y, lambda_a)
                dc_correction = 1.0
                if x == 0 and y == 0:
                    dc_correction = 1 - lambda_h * lambda_a * rho
                elif x == 0 and y == 1:
                    dc_correction = 1 + lambda_h * rho
                elif x == 1 and y == 0:
                    dc_correction = 1 + lambda_a * rho
                elif x == 1 and y == 1:
                    dc_correction = 1 - rho
                matrix[x, y] = p_x * p_y * dc_correction

        total = matrix.sum()
        if total > 0:
            matrix /= total
        return matrix

    @staticmethod
    def _select_top_candidates(score_matrix, top_n=10):
        """从比分矩阵排序获取 Top-N 候选"""
        flat_indices = np.dstack(np.unravel_index(
            np.argsort(score_matrix.ravel())[::-1], score_matrix.shape
        ))[0]
        return [(int(idx[0]), int(idx[1])) for idx in flat_indices[:top_n]]

    @staticmethod
    def _inject_tail_anchors(top_candidates, lambda_h, lambda_a, max_goals=7):
        """
        论文策略3增强版 v2: 尾部候选增强 — 高比分/大分差锚点注入 Top-3
        核心改进:
        1. 激进高比分锚点: 覆盖 (3,3), (4,2), (2,4), (4,3), (3,4) 等 5+ 球比分
        2. 概率比较注入: 只有当锚点概率 > 当前 Top-3 末位时才替换
        3. 阈值放宽: λ_sum > 2.2 即触发高比分锚点 (覆盖更多场景)
        4. 自适应 max_goals: λ_sum > 3.5 时使用 8, 否则 7
        """
        enhanced = list(top_candidates)
        lambda_sum = lambda_h + lambda_a
        lambda_diff = abs(lambda_h - lambda_a)

        anchor_set = set(top_candidates)
        injected = []

        lh_rounded = min(max(int(round(lambda_h)), 0), max_goals)
        la_rounded = min(max(int(round(lambda_a)), 0), max_goals)

        # 预构建评分矩阵用于概率比较
        rho_ref = -0.35
        sm = AdvancedModelTrainer._build_score_matrix(lambda_h, lambda_a, rho_ref, max_goals)

        # 1. 高比分锚点: 当 λ 总和 > 2.2 时触发 (放宽阈值)
        if lambda_sum > 2.2:
            # 扩展高比分覆盖: 包含 5+ 球的各种组合
            high_score_anchors = [
                (2, 3), (3, 2), (3, 3), (2, 4), (4, 2),
                (3, 4), (4, 3), (4, 4), (1, 4), (4, 1),
                (2, 2), (1, 3), (3, 1),
            ]
            for anchor in high_score_anchors:
                if anchor[0] <= max_goals and anchor[1] <= max_goals:
                    if anchor not in anchor_set:
                        injected.append(anchor)
                        anchor_set.add(anchor)

        # 2. 大分差锚点: 当 λ 差 > 0.5 时触发 (进一步放宽)
        if lambda_diff > 0.5:
            if lambda_h > lambda_a:
                diff_anchors = [
                    (3, 0), (4, 0), (3, 1), (4, 1),
                    (2, 0), (2, 1), (1, 0),
                ]
            else:
                diff_anchors = [
                    (0, 3), (0, 4), (1, 3), (1, 4),
                    (0, 2), (1, 2), (0, 1),
                ]
            for anchor in diff_anchors:
                if anchor[0] <= max_goals and anchor[1] <= max_goals:
                    if anchor not in anchor_set:
                        injected.append(anchor)
                        anchor_set.add(anchor)

        # 3. 自适应锚点 (基于 λ 四舍五入)
        adaptive_anchors = [
            (lh_rounded, min(la_rounded + 1, max_goals)),
            (min(lh_rounded + 1, max_goals), la_rounded),
            (min(lh_rounded + 1, max_goals), min(la_rounded + 1, max_goals)),
            (max(0, lh_rounded - 1), min(la_rounded + 1, max_goals)),
            (min(lh_rounded + 1, max_goals), max(0, la_rounded - 1)),
        ]
        if lambda_sum > 2.0:
            for anchor in adaptive_anchors:
                if anchor not in anchor_set and anchor[0] <= max_goals and anchor[1] <= max_goals:
                    injected.append(anchor)
                    anchor_set.add(anchor)

        # 将注入的锚点按概率排序并尝试替换 Top-3 末位
        anchor_probs = []
        for anchor in injected:
            prob = sm[anchor[0], anchor[1]]
            anchor_probs.append((anchor, prob))

        # 按概率降序排列
        anchor_probs.sort(key=lambda x: x[1], reverse=True)

        # 替换策略: 从概率最高的锚点开始，尝试替换 Top-3 中概率最低的
        for anchor, prob in anchor_probs:
            if len(enhanced) >= 3:
                # 找到 Top-3 中概率最低的位置
                min_idx = min(range(3), key=lambda idx: sm[enhanced[idx][0], enhanced[idx][1]])
                min_prob = sm[enhanced[min_idx][0], enhanced[min_idx][1]]
                if prob > min_prob:
                    enhanced[min_idx] = anchor
            elif len(enhanced) < 10:
                enhanced.append(anchor)

        # 去重 (保留首次出现顺序)
        seen = set()
        unique_enhanced = []
        for c in enhanced:
            if c not in seen:
                seen.add(c)
                unique_enhanced.append(c)

        # 补充到至少10个候选
        result = unique_enhanced[:max(len(unique_enhanced), 10)]
        return result

    @staticmethod
    def _conditional_rerank(top_candidates, score_matrix, lambda_h, lambda_a,
                             p_home, p_draw, p_away, style_00_affinity,
                             style_q25, style_q75, rho_used):
        """
        论文策略4 V5.1: 条件化重排 — 根据比赛上下文对 Top-10 候选进行重新排序
        
        V5.1 最终配置 (基于 A/B 测试的最优参数):
        - p_draw 分级加成: 0-0 最高 (0.15), 1-1 中等 (0.08), 2-2 不加成
        - 防守风格: 低分加成 0.12, 高分惩罚 0.08 (原始 V5 权重, 降权后效果下降)
        - 进攻风格: 中分加成 0.10, 0-0 惩罚 0.06
        - 胜负偏向: 强主队/客队 +0.10
        - λ_sum: 低 λ → 0-0 +0.08, 1球 +0.04; 高 λ → 中分 +0.05
        
        核心信号:
           - p_draw 信号: 高平局概率 → 提升平局比分 (0-0 最高, 1-1 中等)
           - 球队风格信号: 高防守风格 → 提升低分; 高进攻风格 → 提升中高比分
           - 胜负偏向信号: 强主队 → 提升主胜比分; 强客队同理
           - 低分保护: 防止 1-0/0-1 被平局挤占 Top-3
           - λ_sum 信号: 低 λ_sum → 确保 0-0 和 1-0/0-1 在 Top-3
        
        参数:
            top_candidates: Top-10 候选列表 [(h, a), ...]
            score_matrix: 比分概率矩阵
            lambda_h, lambda_a: 主客队 λ 值
            p_home, p_draw, p_away: 胜平负概率
            style_00_affinity: 球队 0-0 倾向度 (0-1)
            style_q25, style_q75: style_00 分位数阈值
            rho_used: 当前使用的 ρ 参数
            
        返回:
            重排后的 Top-10 候选列表
        """
        if not top_candidates:
            return top_candidates
        
        max_goals = score_matrix.shape[0] - 1
        lambda_sum = lambda_h + lambda_a
        
        # 计算每个候选的调整后概率
        adjusted = []
        for h, a in top_candidates:
            base_prob = float(score_matrix[h, a])
            adjustment = 0.0
            reasons = []
            
            is_draw = (h == a)
            is_low_score = (h + a <= 1)
            is_mid_score = (3 <= h + a <= 4)
            is_high_score = (h + a >= 5)
            is_home_win = (h > a)
            is_away_win = (a > h)
            
            # 信号1: p_draw 信号 — 高平局概率提升平局比分 (V5.1 最终配置)
            # 0-0 获得最高加成, 1-1 中等加成, 2-2 不加成
            # 阈值 0.28: A/B 测试最优值 (0.32 会导致 0-0 下降 2.9pp)
            if is_draw and p_draw > 0.28:
                if h + a == 0:
                    # 0-0: 最高加成
                    draw_boost = min(0.15, (p_draw - 0.28) * 0.8)
                    adjustment += draw_boost
                    reasons.append(f'p_draw={p_draw:.2f}→0-0+{draw_boost:.3f}')
                elif h + a == 2:
                    # 1-1: 中等加成
                    draw_boost = min(0.08, (p_draw - 0.28) * 0.4)
                    adjustment += draw_boost
                    reasons.append(f'p_draw={p_draw:.2f}→1-1+{draw_boost:.3f}')
                # 2-2 及以上平局: 不额外加成
            
            # 信号2: 球队防守风格 — 高 style_00 提升低分
            if style_00_affinity is not None and style_00_affinity > style_q75:
                if is_low_score:
                    # 恢复 V5 原始权重: 0.12 (V5.1降至0.08导致整体效果下降)
                    style_boost = 0.12 * min(1.0, (style_00_affinity - style_q75) / max(1.0 - style_q75, 0.01))
                    adjustment += style_boost
                    reasons.append(f'防守风格→低分+{style_boost:.3f}')
                elif is_high_score:
                    # 恢复 V5 原始惩罚: 0.08 (V5.1降至0.04导致高分竞争加剧)
                    style_penalty = 0.08
                    adjustment -= style_penalty
                    reasons.append(f'防守风格→高分-{style_penalty:.3f}')
            
            # 信号3: 球队进攻风格 — 低 style_00 提升中高比分
            if style_00_affinity is not None and style_00_affinity < style_q25:
                if is_mid_score:
                    style_boost = 0.10 * min(1.0, (style_q25 - style_00_affinity) / max(style_q25, 0.01))
                    adjustment += style_boost
                    reasons.append(f'进攻风格→中分+{style_boost:.3f}')
                elif is_low_score and h + a == 0:
                    # 进攻风格降低 0-0 概率
                    style_penalty = 0.06
                    adjustment -= style_penalty
                    reasons.append(f'进攻风格→0-0-{style_penalty:.3f}')
            
            # 信号4: 胜负偏向 — 强主队提升主胜比分
            if p_home > 0.55 and is_home_win:
                home_boost = min(0.10, (p_home - 0.55) * 0.5)
                adjustment += home_boost
                reasons.append(f'强主队→主胜+{home_boost:.3f}')
            elif p_away > 0.45 and is_away_win:
                away_boost = min(0.10, (p_away - 0.45) * 0.5)
                adjustment += away_boost
                reasons.append(f'强客队→客胜+{away_boost:.3f}')
            
            # 信号5: λ_sum 信号 — 低 λ_sum 确保 0-0 和 1-0/0-1 在前列
            if lambda_sum < 2.3:
                if (h, a) == (0, 0):
                    adjustment += 0.08
                    reasons.append('低λ→0-0+0.08')
                elif h + a == 1:
                    adjustment += 0.04
                    reasons.append('低λ→1球+0.04')
            
            # 信号6: 高 λ_sum 确保中高比分在列
            if lambda_sum > 2.8 and is_mid_score:
                adjustment += 0.05
                reasons.append('高λ→中分+0.05')

            adjusted_prob = base_prob * (1.0 + adjustment)
            adjusted.append({
                'score': (h, a),
                'base_prob': base_prob,
                'adjusted_prob': adjusted_prob,
                'adjustment': adjustment,
                'reasons': '; '.join(reasons) if reasons else '',
            })
        
        # 按调整后概率降序排列
        adjusted.sort(key=lambda x: x['adjusted_prob'], reverse=True)
        
        # 返回重排后的候选列表
        return [item['score'] for item in adjusted]

    def _evaluate_score_metrics(self, final_proba, y_val, df_train_meta, df_meta, train_size):
        """
        论文策略 1+2+3+6:
        - ρ + lambda_boost 联合网格搜索 (Top-3 为核心目标)
        - 尾部候选增强 (高比分锚点注入)
        - Proper Scoring Rules + 精确比分命中率
        """
        n_val = len(y_val)
        probs_hda = np.zeros((n_val, 3))
        probs_hda[:, 0] = final_proba[:, 2]
        probs_hda[:, 1] = final_proba[:, 1]
        probs_hda[:, 2] = final_proba[:, 0]

        actual_hda = np.zeros((n_val, 3))
        for i, yv in enumerate(y_val):
            if yv == 2:    actual_hda[i, 0] = 1
            elif yv == 1:  actual_hda[i, 1] = 1
            else:          actual_hda[i, 2] = 1

        eps = 1e-15
        log_loss_val = -np.mean(np.sum(actual_hda * np.log(probs_hda + eps), axis=1))
        brier_val = np.mean(np.sum((probs_hda - actual_hda) ** 2, axis=1)) / 3.0
        cum_p = np.cumsum(probs_hda, axis=1)
        cum_o = np.cumsum(actual_hda, axis=1)
        rps_val = np.mean(np.sum((cum_p - cum_o) ** 2, axis=1)) / 2

        val_meta = df_meta.iloc[train_size:] if df_meta is not None else None
        actual_scores = []
        has_actual_goals = (val_meta is not None and
                            'homeGoals' in val_meta.columns and
                            'awayGoals' in val_meta.columns)
        if has_actual_goals:
            for _, row in val_meta.iterrows():
                actual_scores.append((int(row['homeGoals']), int(row['awayGoals'])))
        else:
            logger.warning("[ScoreEval] df_meta 缺少 homeGoals/awayGoals")

        if not (has_actual_goals and len(actual_scores) == n_val):
            return self._build_eval_result_skeleton(log_loss_val, brier_val, rps_val,
                                                   None, None, None, None, 0, 0, 0, 0, 0, 0)

        if df_train_meta is not None and 'homeGoals' in df_train_meta.columns:
            base_lh = float(df_train_meta['homeGoals'].mean())
            base_la = float(df_train_meta['awayGoals'].mean())
        else:
            base_lh, base_la = 1.4, 1.1

        # ============== 策略4: xG 特征工程 (预期进球融合) ==============
        logger.info("[ScoreEval] 计算 xG 特征 (球队历史 + 赔率隐含)...")
        xg_home_arr, xg_away_arr = self._calculate_xg_features(df_train_meta, val_meta)
        if xg_home_arr is not None:
            logger.info(f"[ScoreEval] xG 统计: home_mean={xg_home_arr.mean():.3f}, "
                        f"away_mean={xg_away_arr.mean():.3f}")
            # 融合 xG 值与全局基准 (70% xG + 30% 全局基准)
            xg_weight = 0.7
            effective_base_lh_arr = xg_weight * xg_home_arr + (1 - xg_weight) * base_lh
            effective_base_la_arr = xg_weight * xg_away_arr + (1 - xg_weight) * base_la
        else:
            effective_base_lh_arr = np.ones(n_val) * base_lh
            effective_base_la_arr = np.ones(n_val) * base_la
            logger.info("[ScoreEval] xG 特征不可用, 使用全局基准值")

        # ============== 策略5 V5: 球队攻防风格特征 (区分 0-0 和 2-1) ==============
        logger.info("[ScoreEval] 计算球队攻防风格特征 (V5)...")
        defensive_arr, attacking_arr, style_00_arr = self._calculate_team_style_features(df_train_meta, val_meta)
        if style_00_arr is not None:
            style_q75 = np.percentile(style_00_arr, 75)
            style_q25 = np.percentile(style_00_arr, 25)
            logger.info(f"[ScoreEval] 球队风格: style_00 Q25={style_q25:.3f}, Q75={style_q75:.3f}")
        else:
            style_q75 = 0.7
            style_q25 = 0.3
            logger.info("[ScoreEval] 球队风格特征不可用, 使用默认阈值")

        # ============== 策略2: ρ + lambda_boost 联合网格搜索 (综合评分目标) ==============
        rho_grid = [-0.25, -0.30, -0.35, -0.40, -0.45]
        # 方案C扩展: 添加 boost=3.5, 4.0 以测试高比分场景
        boost_grid = [1.0, 1.2, 1.5, 1.8, 2.2, 2.6, 3.0, 3.5, 4.0]

        # 统计中比分/高比分场次
        n_mid = max(1, sum(1 for s in actual_scores if 3 <= s[0]+s[1] <= 4))
        n_high = max(1, sum(1 for s in actual_scores if s[0]+s[1] >= 5))

        logger.info("[ScoreEval] 开始 ρ × lambda_boost 网格搜索 (综合评分目标)...")
        logger.info(f"[ScoreEval] rho_grid={rho_grid}, boost_grid={boost_grid}")
        logger.info(f"[ScoreEval] n_mid={n_mid}, n_high={n_high}")

        best_composite = -1
        best_rho = -0.13
        best_boost = 1.5
        best_params_scores = {}
        search_results = []

        for rho_test in rho_grid:
            for boost in boost_grid:
                t1 = t3 = t10_cov = 0
                sc1x2 = 0
                mid_t1 = mid_t3 = 0
                high_t3 = 0
                for i in range(n_val):
                    ph, pa = probs_hda[i, 0], probs_hda[i, 2]
                    # 使用 xG 增强的基准值
                    base_lh_i = effective_base_lh_arr[i]
                    base_la_i = effective_base_la_arr[i]
                    lh = max(0.2, base_lh_i * (0.5 + ph * boost))
                    la = max(0.2, base_la_i * (0.5 + pa * boost))
                    # 高比分场景: 扩展 max_goals 到 8 以覆盖更多比分
                    max_g = 8 if (lh + la) > 3.5 else 7
                    sm = self._build_score_matrix(lh, la, rho_test, max_g)
                    tc = self._select_top_candidates(sm, 10)
                    tc = self._inject_tail_anchors(tc, lh, la, max_g)
                    a = actual_scores[i]
                    if a == tc[0]: t1 += 1
                    if a in tc[:3]: t3 += 1
                    if a in tc[:10]: t10_cov += 1
                    ts = tc[0]
                    if ts[0] > ts[1]: p1x2 = 0
                    elif ts[0] < ts[1]: p1x2 = 2
                    else: p1x2 = 1
                    if p1x2 == int(np.argmax(actual_hda[i])): sc1x2 += 1
                    tg = a[0] + a[1]
                    if 3 <= tg <= 4:
                        if a == tc[0]: mid_t1 += 1
                        if a in tc[:3]: mid_t3 += 1
                    if tg >= 5:
                        if a in tc[:3]: high_t3 += 1
                n = max(n_val, 1)
                r3 = t3 / n
                mid_r3 = mid_t3 / n_mid
                high_r3 = high_t3 / n_high
                # 综合评分: 40% 总体 Top-3 + 30% 中比分 Top-3 + 30% 高比分 Top-3
                composite = 0.4 * r3 + 0.3 * mid_r3 + 0.3 * high_r3
                search_results.append({
                    'rho': rho_test, 'boost': boost,
                    'top1': t1, 'top1_rate': t1 / n,
                    'top3': t3, 'top3_rate': r3,
                    'mid_top1': mid_t1, 'mid_top1_rate': mid_t1 / n_mid,
                    'mid_top3': mid_t3, 'mid_top3_rate': mid_r3,
                    'high_top3': high_t3, 'high_top3_rate': high_r3,
                    'composite_score': composite,
                    'score_1x2': sc1x2,
                })
                if composite > best_composite:
                    best_composite = composite
                    best_rho = rho_test
                    best_boost = boost
                    best_params_scores = search_results[-1]

        logger.info(f"[ScoreEval] 网格搜索完成: best_rho={best_rho}, best_boost={best_boost}, "
                     f"best_composite={best_composite*100:.2f}%, "
                     f"overall_top3={best_params_scores['top3_rate']*100:.2f}%, "
                     f"mid_top3={best_params_scores['mid_top3_rate']*100:.2f}%, "
                     f"high_top3={best_params_scores['high_top3_rate']*100:.2f}%")

        # 搜索结果 Top-5 (按综合评分排序)
        top5_results = sorted(search_results, key=lambda x: x['composite_score'], reverse=True)[:5]
        for r in top5_results:
            logger.info(f"  ρ={r['rho']:.2f} boost={r['boost']:.1f} "
                         f"Comp={r['composite_score']*100:.1f}% "
                         f"T1={r['top1_rate']*100:.1f}% T3={r['top3_rate']*100:.1f}% "
                         f"Mid_T1={r['mid_top1_rate']*100:.1f}% Mid_T3={r['mid_top3_rate']*100:.1f}% "
                         f"High_T3={r['high_top3_rate']*100:.1f}%")

        # ============== 用最优参数重新评估 (自适应 max_goals + 分场景回退) ==============
        rho = best_rho
        lambda_boost = best_boost

        # 方案A V4.1: 5级分级场景 (平衡 0-0 和中比分)
        # V3问题: Q25+boost=1.0 → 0-0=58.6% 但中比分=27.2%
        # V4问题: Q15+boost=1.2 → 中比分=32.0% 但 0-0=45.7%
        # V4.1方案: Q20+boost=1.0 保留0-0优势, mid boost=2.2 保留中比分优势
        raw_lambda_sums = np.zeros(n_val)
        for i in range(n_val):
            ph_i, pa_i = probs_hda[i, 0], probs_hda[i, 2]
            base_lh_i = effective_base_lh_arr[i]
            base_la_i = effective_base_la_arr[i]
            raw_lh_i = base_lh_i * (0.5 + ph_i)
            raw_la_i = base_la_i * (0.5 + pa_i)
            raw_lambda_sums[i] = raw_lh_i + raw_la_i
        
        # V4.1: 5级分位数 (Q20/Q40/Q60/Q75)
        q20 = np.percentile(raw_lambda_sums, 20)   # 极低/低分界
        q40 = np.percentile(raw_lambda_sums, 40)   # 低/中低分界
        q60 = np.percentile(raw_lambda_sums, 60)   # 中低/中高分界
        q75 = np.percentile(raw_lambda_sums, 75)   # 中高/高分界
        logger.info(f"[ScoreEval] λ_sum 分位数 (V4.1): Q20={q20:.2f}, Q40={q40:.2f}, Q60={q60:.2f}, Q75={q75:.2f}")
        logger.info(f"[ScoreEval] λ_sum 范围: min={raw_lambda_sums.min():.2f}, max={raw_lambda_sums.max():.2f}, mean={raw_lambda_sums.mean():.2f}")

        # 分场景参数统计 (方案A V4.1: 5级)
        scenario_stats = {'very_low': 0, 'low': 0, 'mid_low': 0, 'mid_high': 0, 'high': 0}
        # 交叉统计: 每个场景内实际比分类型分布 (诊断用)
        cross_stats = {
            'very_low': {'0-0': 0, 'low': 0, 'mid': 0, 'high': 0},
            'low': {'0-0': 0, 'low': 0, 'mid': 0, 'high': 0},
            'mid_low': {'0-0': 0, 'low': 0, 'mid': 0, 'high': 0},
            'mid_high': {'0-0': 0, 'low': 0, 'mid': 0, 'high': 0},
            'high': {'0-0': 0, 'low': 0, 'mid': 0, 'high': 0},
        }
        zero_zero_logs = []  # 0-0 场次详细日志
        mid_score_logs = []  # 中比分场次详细日志

        top1_hits = top3_hits = top10_coverage = score_1x2_hits = 0
        # 策略4重排效果统计
        rerank_stats = {'total': 0, 'top3_changed': 0, 'helped': 0, 'hurt': 0, 'neutral': 0}
        rerank_hurt_logs = []   # 负向收益场次详细日志
        rerank_help_logs = []   # 正向收益场次详细日志
        score_type_stats = {
            '0-0': {'total': 0, 'top1': 0, 'top3': 0},
            'low_score': {'total': 0, 'top1': 0, 'top3': 0},
            'mid_score': {'total': 0, 'top1': 0, 'top3': 0},
            'high_score': {'total': 0, 'top1': 0, 'top3': 0},
        }

        for i in range(n_val):
            ph, pa = probs_hda[i, 0], probs_hda[i, 2]
            # 使用 xG 增强的基准值
            base_lh_i = effective_base_lh_arr[i]
            base_la_i = effective_base_la_arr[i]
            
            # 使用预计算的 raw_lambda_sum
            raw_lambda_sum = raw_lambda_sums[i]
            p_draw_i = probs_hda[i, 1]  # 平局概率
            
            # 策略5 V4.2: 5级分级场景 + p_draw二级信号
            # 0-0 是平局, 高 p_draw 的比赛需要限制 boost 以保留 0-0 预测能力
            if raw_lambda_sum <= q20:
                # 极低进球场景 (最低20%): 保守参数
                scenario_rho, scenario_boost = -0.40, 1.0
                scenario_name = 'very_low'
                scenario_stats['very_low'] += 1
            elif raw_lambda_sum <= q40:
                # 低进球场景 (20%-40%)
                scenario_rho, scenario_boost = -0.35, 1.5
                scenario_name = 'low'
                scenario_stats['low'] += 1
            elif raw_lambda_sum <= q60:
                # 中低进球场景 (40%-60%)
                scenario_rho, scenario_boost = -0.32, 2.0
                scenario_name = 'mid_low'
                scenario_stats['mid_low'] += 1
            elif raw_lambda_sum <= q75:
                # 中高进球场景 (60%-75%)
                scenario_rho, scenario_boost = -0.28, 2.4
                scenario_name = 'mid_high'
                scenario_stats['mid_high'] += 1
            else:
                # 高进球场景 (最高25%)
                scenario_rho, scenario_boost = -0.25, 2.6
                scenario_name = 'high'
                scenario_stats['high'] += 1
            
            # V4.2 关键优化: p_draw 二级信号 — 高平局概率比赛限制 boost
            # 0-0 是平局, 高 p_draw 的比赛需要限制 boost 以保留 0-0 预测能力
            # 阈值 0.30: 平衡 0-0 恢复和中比分保护的最优点
            draw_boost_applied = False
            if p_draw_i > 0.30 and scenario_name not in ('very_low', 'low'):
                # 高平局概率: 限制 boost, 使用更保守的 ρ
                scenario_boost = min(scenario_boost, 1.8)
                scenario_rho = min(scenario_rho, -0.35)  # 更负的 ρ → 更保守
                draw_boost_applied = True
            
            # V5 三级信号: 球队攻防风格 (style_00_affinity)
            # 高 style_00 (双方防守强、进攻弱) → 更可能 0-0, 进一步限制 boost
            # 低 style_00 (双方进攻强) → 更可能中高比分, 取消保守限制
            style_override_applied = False
            style_reason = ''
            if style_00_arr is not None:
                style_00_i = style_00_arr[i]
                if style_00_i > style_q75 and scenario_name not in ('very_low', 'low'):
                    # 高 0-0 倾向: 进一步限制 boost
                    scenario_boost = min(scenario_boost, 1.5)
                    scenario_rho = min(scenario_rho, -0.38)
                    style_override_applied = True
                    style_reason = f'高防守风格(style={style_00_i:.2f}>Q75={style_q75:.2f})→boost≤1.5'
                elif style_00_i < style_q25 and draw_boost_applied:
                    # 低 0-0 倾向但 p_draw 高: 可能是 2-2 等中比分平局, 取消保守限制
                    scenario_boost = min(scenario_boost + 0.4, 2.2)  # 恢复部分 boost
                    style_override_applied = True
                    style_reason = f'高进攻风格(style={style_00_i:.2f}<Q25={style_q25:.2f})→恢复boost'
            
            # 使用场景参数重新计算 λ
            lh = max(0.2, base_lh_i * (0.5 + ph * scenario_boost))
            la = max(0.2, base_la_i * (0.5 + pa * scenario_boost))
            
            # 自适应 max_goals: λ 总和大时扩展到 8
            max_goals = 8 if (lh + la) > 3.5 else 7
            
            # 使用场景特定的 rho 参数
            sm = self._build_score_matrix(lh, la, scenario_rho, max_goals)
            tc = self._select_top_candidates(sm, 10)
            tc = self._inject_tail_anchors(tc, lh, la, max_goals)
            
            # 策略4: 条件化重排 — 根据比赛上下文重新排序 Top-10
            style_00_i = float(style_00_arr[i]) if style_00_arr is not None else None
            tc_pre_rerank = list(tc)  # 保存重排前的顺序用于日志
            tc = self._conditional_rerank(
                tc, sm, lh, la, ph, p_draw_i, pa,
                style_00_i, style_q25, style_q75, scenario_rho
            )

            actual = actual_scores[i]
            actual_total_goals = actual[0] + actual[1]
            
            # 策略4重排效果追踪
            rerank_stats['total'] += 1
            pre_top3 = set(tc_pre_rerank[:3])
            post_top3 = set(tc[:3])
            if pre_top3 != post_top3:
                rerank_stats['top3_changed'] += 1
                actual_in_pre = actual in pre_top3
                actual_in_post = actual in post_top3
                if actual_in_post and not actual_in_pre:
                    rerank_stats['helped'] += 1
                    rerank_help_logs.append({
                        'index': i, 'actual': actual,
                        'pre_top3': str(tc_pre_rerank[:3]),
                        'post_top3': str(tc[:3]),
                        'scenario': scenario_name,
                        'p_draw': p_draw_i,
                        'style_00': style_00_i if style_00_i is not None else -1.0,
                        'lambda_sum': raw_lambda_sum,
                        'boost': scenario_boost,
                    })
                elif actual_in_pre and not actual_in_post:
                    rerank_stats['hurt'] += 1
                    # 找出被挤出的比分和被换入的比分
                    removed = pre_top3 - post_top3
                    added = post_top3 - pre_top3
                    rerank_hurt_logs.append({
                        'index': i, 'actual': actual,
                        'pre_top3': str(tc_pre_rerank[:3]),
                        'post_top3': str(tc[:3]),
                        'removed': str(removed),
                        'added': str(added),
                        'scenario': scenario_name,
                        'p_draw': p_draw_i,
                        'style_00': style_00_i if style_00_i is not None else -1.0,
                        'lambda_sum': raw_lambda_sum,
                        'boost': scenario_boost,
                        'actual_total_goals': actual_total_goals,
                    })
                else:
                    rerank_stats['neutral'] += 1
            
            # 交叉统计: 记录每个场景内实际比分类型分布
            if actual_total_goals == 0:
                cross_stats[scenario_name]['0-0'] += 1
            elif actual_total_goals <= 2:
                cross_stats[scenario_name]['low'] += 1
            elif actual_total_goals <= 4:
                cross_stats[scenario_name]['mid'] += 1
            else:
                cross_stats[scenario_name]['high'] += 1
            
            # 记录 0-0 场次的详细日志 (方案A V5调试 — 含球队风格诊断)
            if actual_total_goals == 0:
                top3_preds = tc[:3]
                zero_zero_in_top3 = (0, 0) in top3_preds
                zero_zero_prob = float(sm[0, 0])
                style_val = float(style_00_arr[i]) if style_00_arr is not None else -1.0
                # 诊断误分类原因
                if not zero_zero_in_top3:
                    # 分析为什么 0-0 没进 Top-3
                    miss_reasons = []
                    if scenario_name in ('mid_low', 'mid_high', 'high'):
                        miss_reasons.append(f'场景={scenario_name}(boost={scenario_boost}过高)')
                    if not draw_boost_applied and p_draw_i <= 0.30:
                        miss_reasons.append(f'p_draw={p_draw_i:.2f}≤0.30未触发保守限制')
                    if style_00_arr is not None and style_val <= style_q75:
                        miss_reasons.append(f'style_00={style_val:.2f}≤Q75未触发防守风格')
                    miss_reason = '; '.join(miss_reasons) if miss_reasons else '未知原因'
                else:
                    miss_reason = ''
                zero_zero_logs.append({
                    'index': i,
                    'actual': actual,
                    'raw_lambda_sum': raw_lambda_sum,
                    'p_draw': p_draw_i,
                    'style_00': style_val,
                    'scenario': scenario_name,
                    'rho': scenario_rho,
                    'boost': scenario_boost,
                    'draw_boost': draw_boost_applied,
                    'style_override': style_override_applied,
                    'style_reason': style_reason,
                    'lh': lh,
                    'la': la,
                    'zero_zero_prob': zero_zero_prob,
                    'zero_zero_in_top3': zero_zero_in_top3,
                    'miss_reason': miss_reason,
                    'top3_preds': str(top3_preds),
                    'top1_pred': tc[0],
                })
            
            # 记录中比分(3-4球)场次日志 (V5: 含球队风格诊断)
            if 3 <= actual_total_goals <= 4:
                top3_preds = tc[:3]
                mid_in_top3 = actual in top3_preds
                style_val = float(style_00_arr[i]) if style_00_arr is not None else -1.0
                # 诊断误分类原因
                if not mid_in_top3:
                    miss_reasons = []
                    if scenario_boost < 2.0:
                        miss_reasons.append(f'boost={scenario_boost}被限制(保守场景)')
                    if draw_boost_applied:
                        miss_reasons.append(f'p_draw={p_draw_i:.2f}>0.30触发boost限制')
                    if style_override_applied and '恢复' not in style_reason:
                        miss_reasons.append(f'style_00={style_val:.2f}触发防守风格覆盖')
                    if lh + la < 2.5:
                        miss_reasons.append(f'λ_sum={lh+la:.2f}偏低(实际{actual_total_goals}球)')
                    miss_reason = '; '.join(miss_reasons) if miss_reasons else 'Top-3预测偏差'
                else:
                    miss_reason = ''
                mid_score_logs.append({
                    'index': i,
                    'actual': actual,
                    'raw_lambda_sum': raw_lambda_sum,
                    'p_draw': p_draw_i,
                    'style_00': style_val,
                    'scenario': scenario_name,
                    'rho': scenario_rho,
                    'boost': scenario_boost,
                    'draw_boost': draw_boost_applied,
                    'style_override': style_override_applied,
                    'lh': lh,
                    'la': la,
                    'in_top3': mid_in_top3,
                    'miss_reason': miss_reason,
                    'top3_preds': str(top3_preds),
                })
            
            if actual == tc[0]: top1_hits += 1
            if actual in tc[:3]: top3_hits += 1
            if actual in tc[:10]: top10_coverage += 1
            ts = tc[0]
            if ts[0] > ts[1]: p1x2 = 0
            elif ts[0] < ts[1]: p1x2 = 2
            else: p1x2 = 1
            if p1x2 == int(np.argmax(actual_hda[i])): score_1x2_hits += 1

            tg = actual[0] + actual[1]
            if tg == 0: st_key = '0-0'
            elif tg <= 2: st_key = 'low_score'
            elif tg <= 4: st_key = 'mid_score'
            else: st_key = 'high_score'
            score_type_stats[st_key]['total'] += 1
            if actual == tc[0]: score_type_stats[st_key]['top1'] += 1
            if actual in tc[:3]: score_type_stats[st_key]['top3'] += 1
        
        # 策略4重排效果统计
        logger.info(f"[ScoreEval] 策略4条件化重排效果: "
                    f"总场次={rerank_stats['total']}, "
                    f"Top-3改变={rerank_stats['top3_changed']}({rerank_stats['top3_changed']/max(rerank_stats['total'],1)*100:.1f}%), "
                    f"正向={rerank_stats['helped']}, 负向={rerank_stats['hurt']}, "
                    f"中性={rerank_stats['neutral']}, "
                    f"净收益={rerank_stats['helped']-rerank_stats['hurt']:+d}")
        
        # 输出负向收益场次详细日志 (诊断重排伤害来源)
        if rerank_hurt_logs:
            logger.info(f"[ScoreEval] 重排负向收益分析 (共{len(rerank_hurt_logs)}场, 展示全部):")
            # 按实际比分类型分组统计
            hurt_by_type = {'0-0': 0, 'low': 0, 'mid': 0, 'high': 0}
            for h in rerank_hurt_logs:
                tg = h['actual_total_goals']
                if tg == 0: hurt_by_type['0-0'] += 1
                elif tg <= 2: hurt_by_type['low'] += 1
                elif tg <= 4: hurt_by_type['mid'] += 1
                else: hurt_by_type['high'] += 1
            logger.info(f"  负向按比分类型: 0-0={hurt_by_type['0-0']}, 低比分={hurt_by_type['low']}, "
                        f"中比分={hurt_by_type['mid']}, 高比分={hurt_by_type['high']}")
            for h in rerank_hurt_logs:
                logger.info(f"  [HURT#{h['index']}] actual={h['actual']}, scenario={h['scenario']}, "
                           f"λ_sum={h['lambda_sum']:.3f}, p_draw={h['p_draw']:.3f}, style_00={h['style_00']:.3f}, "
                           f"boost={h['boost']}, 挤出={h['removed']}, 换入={h['added']}, "
                           f"pre_top3={h['pre_top3']}, post_top3={h['post_top3']}")
        
        # 输出正向收益场次 (对比分析)
        if rerank_help_logs:
            logger.info(f"[ScoreEval] 重排正向收益分析 (共{len(rerank_help_logs)}场, 展示前10条):")
            for h in rerank_help_logs[:10]:
                logger.info(f"  [HELP#{h['index']}] actual={h['actual']}, scenario={h['scenario']}, "
                           f"λ_sum={h['lambda_sum']:.3f}, p_draw={h['p_draw']:.3f}, style_00={h['style_00']:.3f}, "
                           f"pre_top3={h['pre_top3']}, post_top3={h['post_top3']}")
        
        logger.info(f"[ScoreEval] 分场景统计 (V5): 极低={scenario_stats['very_low']}, "
                    f"低={scenario_stats['low']}, 中低={scenario_stats['mid_low']}, "
                    f"中高={scenario_stats['mid_high']}, 高={scenario_stats['high']}")
        
        # 输出交叉统计: 场景 × 实际比分类型 (V5诊断)
        scenario_names_5 = ['very_low', 'low', 'mid_low', 'mid_high', 'high']
        logger.info(f"[ScoreEval] 场景×比分交叉统计:")
        for sn in scenario_names_5:
            cs = cross_stats[sn]
            total = sum(cs.values())
            logger.info(f"  {sn}({total}场): 0-0={cs['0-0']}, 低比分={cs['low']}, 中比分={cs['mid']}, 高比分={cs['high']}")
        
        # 输出 0-0 场次详细日志 (V5: 含球队风格和误分类原因)
        if zero_zero_logs:
            logger.info(f"[ScoreEval] 0-0 场次分析: 共 {len(zero_zero_logs)} 场")
            zero_zero_hit = sum(1 for z in zero_zero_logs if z['zero_zero_in_top3'])
            logger.info(f"[ScoreEval] 0-0 在Top-3命中率: {zero_zero_hit}/{len(zero_zero_logs)} = {zero_zero_hit/len(zero_zero_logs)*100:.1f}%")
            # 按场景分组统计 0-0 命中率
            for sn in scenario_names_5:
                sn_logs = [z for z in zero_zero_logs if z['scenario'] == sn]
                if sn_logs:
                    sn_hit = sum(1 for z in sn_logs if z['zero_zero_in_top3'])
                    logger.info(f"  0-0 in {sn}: {sn_hit}/{len(sn_logs)} = {sn_hit/len(sn_logs)*100:.1f}%")
            # 统计 style_override 对 0-0 的影响
            style_helped = sum(1 for z in zero_zero_logs if z.get('style_override') and z['zero_zero_in_top3'])
            style_total = sum(1 for z in zero_zero_logs if z.get('style_override'))
            if style_total > 0:
                logger.info(f"  0-0 风格信号触发: {style_total}场, 命中={style_helped}/{style_total}={style_helped/style_total*100:.1f}%")
            # 输出误分类的 0-0 场次 (前10条, 含误分类原因)
            missed_00 = [z for z in zero_zero_logs if not z['zero_zero_in_top3']]
            if missed_00:
                logger.info(f"[ScoreEval] 0-0 误分类分析 (共{len(missed_00)}场, 展示前10条):")
                for z in missed_00[:10]:
                    logger.info(f"  [0-0 MISS#{z['index']}] scenario={z['scenario']}, "
                               f"λ_sum={z['raw_lambda_sum']:.3f}, p_draw={z['p_draw']:.3f}, "
                               f"style_00={z['style_00']:.3f}, boost={z['boost']}, ρ={z['rho']}, "
                               f"0-0概率={z['zero_zero_prob']:.4f}, "
                               f"draw_boost={z['draw_boost']}, style_override={z.get('style_override', False)}, "
                               f"原因: {z['miss_reason']}, top3={z['top3_preds']}")
        
        # 输出中比分场次分析 (V5: 含球队风格和误分类原因)
        if mid_score_logs:
            mid_hit = sum(1 for m in mid_score_logs if m['in_top3'])
            logger.info(f"[ScoreEval] 中比分(3-4球)场次分析: 共 {len(mid_score_logs)} 场, "
                        f"Top-3命中={mid_hit}/{len(mid_score_logs)} = {mid_hit/len(mid_score_logs)*100:.1f}%")
            # 按场景分组统计中比分命中率
            for sn in scenario_names_5:
                sn_logs = [m for m in mid_score_logs if m['scenario'] == sn]
                if sn_logs:
                    sn_hit = sum(1 for m in sn_logs if m['in_top3'])
                    logger.info(f"  中比分 in {sn}: {sn_hit}/{len(sn_logs)} = {sn_hit/len(sn_logs)*100:.1f}%")
            # 统计 style_override 对中比分的影响
            style_mid_total = sum(1 for m in mid_score_logs if m.get('style_override'))
            style_mid_hit = sum(1 for m in mid_score_logs if m.get('style_override') and m['in_top3'])
            if style_mid_total > 0:
                logger.info(f"  中比分 风格信号触发: {style_mid_total}场, 命中={style_mid_hit}/{style_mid_total}={style_mid_hit/style_mid_total*100:.1f}%")
            # 统计 draw_boost 对中比分的影响
            draw_mid_total = sum(1 for m in mid_score_logs if m.get('draw_boost'))
            draw_mid_hit = sum(1 for m in mid_score_logs if m.get('draw_boost') and m['in_top3'])
            if draw_mid_total > 0:
                logger.info(f"  中比分 draw_boost触发: {draw_mid_total}场, 命中={draw_mid_hit}/{draw_mid_total}={draw_mid_hit/draw_mid_total*100:.1f}%")
            # 输出误分类的中比分场次 (前10条, 含误分类原因)
            missed_mid = [m for m in mid_score_logs if not m['in_top3']]
            if missed_mid:
                logger.info(f"[ScoreEval] 中比分误分类分析 (共{len(missed_mid)}场, 展示前10条):")
                for m in missed_mid[:10]:
                    logger.info(f"  [中比分 MISS#{m['index']}] actual={m['actual']}, "
                               f"scenario={m['scenario']}, λ_sum={m['raw_lambda_sum']:.3f}, "
                               f"p_draw={m['p_draw']:.3f}, style_00={m['style_00']:.3f}, "
                               f"boost={m['boost']}, ρ={m['rho']}, "
                               f"draw_boost={m.get('draw_boost', False)}, style_override={m.get('style_override', False)}, "
                               f"原因: {m['miss_reason']}, top3={m['top3_preds']}")

        score_evaluated = n_val
        n = max(score_evaluated, 1)

        result = {
            'proper_scoring_rules': {
                'log_loss': float(log_loss_val),
                'brier_score': float(brier_val),
                'rps': float(rps_val),
            },
            'grid_search': {
                'best_rho': best_rho,
                'best_lambda_boost': lambda_boost,
                'best_composite_score': float(best_composite),
                'best_top3_rate': float(best_params_scores.get('top3_rate', 0)),
                'best_mid_top3_rate': float(best_params_scores.get('mid_top3_rate', 0)),
                'best_high_top3_rate': float(best_params_scores.get('high_top3_rate', 0)),
                'best_params_detail': best_params_scores,
                'top5_results': top5_results,
                'search_space': {'rho': rho_grid, 'lambda_boost': boost_grid},
                'optimization_target': 'composite: 0.4*overall_top3 + 0.3*mid_top3 + 0.3*high_top3',
            },
            'exact_score_accuracy': {
                'top1_hits': top1_hits,
                'top1_rate': float(top1_hits / n),
                'top3_hits': top3_hits,
                'top3_rate': float(top3_hits / n),
                'top10_coverage': top10_coverage,
                'top10_coverage_rate': float(top10_coverage / n),
                'score_derived_1x2_hits': score_1x2_hits,
                'score_derived_1x2_rate': float(score_1x2_hits / n),
                'evaluated_matches': score_evaluated,
            },
            'score_type_breakdown': {
                k: {
                    'total': v['total'],
                    'top1': v['top1'],
                    'top3': v['top3'],
                    'top1_rate': float(v['top1'] / max(v['total'], 1)),
                    'top3_rate': float(v['top3'] / max(v['total'], 1)),
                } for k, v in score_type_stats.items()
            },
            'lambda_estimation': {
                'base_lambda_home': float(base_lh),
                'base_lambda_away': float(base_la),
                'xg_enhanced': xg_home_arr is not None,
                'xg_weight': 0.7 if xg_home_arr is not None else 0.0,
                'xg_home_mean': float(xg_home_arr.mean()) if xg_home_arr is not None else None,
                'xg_away_mean': float(xg_away_arr.mean()) if xg_away_arr is not None else None,
                'rho': rho,
                'lambda_boost': lambda_boost,
                'max_goals': max_goals,
                'formula': f'lambda = base * (0.5 + p_win * {lambda_boost})',
            },
            'scenario_params': {
                'enabled': True,
                'version': 'V5.1',
                'percentiles': {'q20': float(q20), 'q40': float(q40), 'q60': float(q60), 'q75': float(q75)},
                'very_low_goals': {'rho': -0.40, 'boost': 1.0, 'percentile': 'Q0-Q20', 'count': scenario_stats['very_low']},
                'low_goals': {'rho': -0.35, 'boost': 1.5, 'percentile': 'Q20-Q40', 'count': scenario_stats['low']},
                'mid_low_goals': {'rho': -0.32, 'boost': 2.0, 'percentile': 'Q40-Q60', 'count': scenario_stats['mid_low']},
                'mid_high_goals': {'rho': -0.28, 'boost': 2.4, 'percentile': 'Q60-Q75', 'count': scenario_stats['mid_high']},
                'high_goals': {'rho': -0.25, 'boost': 2.6, 'percentile': 'Q75+', 'count': scenario_stats['high']},
                'p_draw_threshold': 0.30,
                'p_draw_boost_cap': 1.8,
                'style_00_q25': float(style_q25) if style_00_arr is not None else None,
                'style_00_q75': float(style_q75) if style_00_arr is not None else None,
                'style_high_boost_cap': 1.5,
                'style_low_boost_recovery': 0.4,
            },
            'cross_stats': {
                sn: dict(cross_stats[sn]) for sn in scenario_names_5
            },
            'zero_zero_analysis': {
                'total_zero_zero': len(zero_zero_logs),
                'in_top3': sum(1 for z in zero_zero_logs if z['zero_zero_in_top3']),
                'hit_rate': float(sum(1 for z in zero_zero_logs if z['zero_zero_in_top3']) / max(len(zero_zero_logs), 1)),
            },
            'mid_score_analysis': {
                'total_mid_score': len(mid_score_logs),
                'in_top3': sum(1 for m in mid_score_logs if m['in_top3']),
                'hit_rate': float(sum(1 for m in mid_score_logs if m['in_top3']) / max(len(mid_score_logs), 1)),
            },
            'rerank_analysis': {
                'strategy': 'conditional_rerank',
                'total': rerank_stats['total'],
                'top3_changed': rerank_stats['top3_changed'],
                'top3_changed_rate': float(rerank_stats['top3_changed'] / max(rerank_stats['total'], 1)),
                'helped': rerank_stats['helped'],
                'hurt': rerank_stats['hurt'],
                'neutral': rerank_stats['neutral'],
                'net_gain': rerank_stats['helped'] - rerank_stats['hurt'],
            },
            'reference': {
                'paper_v1_top1': 0.100,
                'paper_v1_top3': 0.267,
                'paper_v4_top1': 0.147,
                'paper_v4_top3': 0.307,
                'paper_v1_rps': 0.2095,
            },
            'optimization_delta': {
                'baseline_top1': 0.1227,
                'baseline_top3': 0.3149,
                'delta_top1': float(top1_hits / n - 0.1227),
                'delta_top3': float(top3_hits / n - 0.3149),
            },
        }

        logger.info(f"[ScoreEval] ρ={rho} boost={lambda_boost} composite={best_composite*100:.2f}%")
        logger.info(f"[ScoreEval] Top-1={top1_hits}/{n} ({top1_hits/n*100:.1f}%) "
                     f"Top-3={top3_hits}/{n} ({top3_hits/n*100:.1f}%) "
                     f"Coverage={top10_coverage}/{n} ({top10_coverage/n*100:.1f}%)")
        # 输出各比分类型详情
        for st_key, st_name in [('0-0', '0-0'), ('low_score', '低比分(1-2球)'),
                                 ('mid_score', '中比分(3-4球)'), ('high_score', '高比分(≥5球)')]:
            sd = result['score_type_breakdown'].get(st_key, {})
            if sd.get('total', 0) > 0:
                logger.info(f"[ScoreEval] {st_name}: {sd['total']}场 "
                             f"Top-1={sd['top1_rate']*100:.1f}% Top-3={sd['top3_rate']*100:.1f}%")
        logger.info(f"[ScoreEval] 对比基线: Top-1 Δ={result['optimization_delta']['delta_top1']*100:+.1f}pp "
                     f"Top-3 Δ={result['optimization_delta']['delta_top3']*100:+.1f}pp")

        return result

    def _build_eval_result_skeleton(self, log_loss, brier, rps, base_lh, base_la, rho, boost,
                                     t1, t3, t10, sc1x2, n_eval):
        n = max(n_eval, 1)
        return {
            'proper_scoring_rules': {
                'log_loss': float(log_loss), 'brier_score': float(brier), 'rps': float(rps),
            },
            'grid_search': {'skipped': True, 'reason': 'no actual goals'},
            'exact_score_accuracy': {
                'top1_hits': t1, 'top1_rate': float(t1 / n),
                'top3_hits': t3, 'top3_rate': float(t3 / n),
                'top10_coverage': t10, 'top10_coverage_rate': float(t10 / n),
                'score_derived_1x2_hits': sc1x2, 'score_derived_1x2_rate': float(sc1x2 / n),
                'evaluated_matches': n_eval,
            },
            'score_type_breakdown': {},
            'lambda_estimation': {
                'base_lambda_home': base_lh, 'base_lambda_away': base_la,
                'rho': rho, 'lambda_boost': boost,
            },
            'reference': {'paper_v1_top1': 0.100, 'paper_v1_top3': 0.267},
            'optimization_delta': {'baseline_top1': 0.1227, 'baseline_top3': 0.3149,
                                    'delta_top1': 0.0, 'delta_top3': 0.0},
        }

    def _preprocess(self, X: pd.DataFrame) -> Tuple[pd.DataFrame, StandardScaler]:
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        return pd.DataFrame(X_scaled, columns=X.columns, index=X.index), scaler
    
    def _run_cv(self, X, y, df_meta):
        cv_config = self.cv
        
        def train_xgb_fold(X_tr, y_tr, X_va, y_va, fold_index=0, monitor=None):
            try:
                trainer = SklearnXGBoostTrainer(self.config['xgboost'])
                class_weights = self.weight_strategy.compute_class_weights_only(y_tr)
                trainer.fit(X_tr, y_tr, X_va, y_va, class_weight=class_weights, monitor=monitor)
                
                y_pred = trainer.predict(X_va)
                y_proba = trainer.predict_proba(X_va)
                
                return {
                    'model': trainer,
                    'val_accuracy': accuracy_score(y_va, y_pred),
                    'val_log_loss': log_loss(y_va, y_proba),
                    'val_f1': f1_score(y_va, y_pred, average='macro'),
                    'train_accuracy': accuracy_score(y_tr, trainer.predict(X_tr)),
                    'train_log_loss': log_loss(y_tr, trainer.predict_proba(X_tr)),
                    'best_iteration': trainer.best_iteration,
                }
            except Exception as e:
                logger.error(f"XGBoost fold {fold_index} 失败: {e}")
                return None
        
        def train_lgb_fold(X_tr, y_tr, X_va, y_va, fold_index=0, monitor=None):
            try:
                trainer = SklearnLightGBMTrainer(self.config['lightgbm'])
                class_weights = self.weight_strategy.compute_class_weights_only(y_tr)
                trainer.fit(X_tr, y_tr, X_va, y_va, class_weight=class_weights, monitor=monitor)
                
                y_pred = trainer.predict(X_va)
                y_proba = trainer.predict_proba(X_va)
                
                return {
                    'model': trainer,
                    'val_accuracy': accuracy_score(y_va, y_pred),
                    'val_log_loss': log_loss(y_va, y_proba),
                    'val_f1': f1_score(y_va, y_pred, average='macro'),
                    'train_accuracy': accuracy_score(y_tr, trainer.predict(X_tr)),
                    'train_log_loss': log_loss(y_tr, trainer.predict_proba(X_tr)),
                    'best_iteration': trainer.best_iteration,
                }
            except Exception as e:
                logger.error(f"LightGBM fold {fold_index} 失败: {e}")
                return None
        
        return cv_config.run(X, y, df_meta, train_xgb_fold, train_lgb_fold)
    
    def _train_xgboost(self, X_train, y_train, X_val, y_val, 
                       sample_weights, class_weights, is_final=False):
        if not XGB_AVAILABLE:
            return None, None
        
        trainer = SklearnXGBoostTrainer(self.config['xgboost'])
        trainer.fit(
            X_train.values, y_train.values,
            X_val.values, y_val.values,
            sample_weight=sample_weights,
            class_weight=class_weights,
            monitor=self.overfitting_monitor if is_final else None
        )
        
        y_val_pred = trainer.predict(X_val.values)
        y_val_proba = trainer.predict_proba(X_val.values)
        
        metrics = {
            'val_accuracy': float(accuracy_score(y_val, y_val_pred)),
            'val_log_loss': float(log_loss(y_val, y_val_proba)),
            'val_f1': float(f1_score(y_val, y_val_pred, average='macro')),
            'train_accuracy': float(accuracy_score(y_train, trainer.predict(X_train.values))),
            'train_log_loss': float(log_loss(y_train, trainer.predict_proba(X_train.values))),
            'best_iteration': trainer.best_iteration,
        }
        
        self._log('XGBOOST_TRAINED', {
            'best_iteration': trainer.best_iteration,
            'val_accuracy': metrics['val_accuracy'],
            'val_log_loss': metrics['val_log_loss'],
            'overfitting_gap': metrics['train_accuracy'] - metrics['val_accuracy'],
        })
        
        return trainer, metrics
    
    def _train_lightgbm(self, X_train, y_train, X_val, y_val,
                        sample_weights, class_weights, is_final=False):
        if not LGB_AVAILABLE:
            return None, None
        
        trainer = SklearnLightGBMTrainer(self.config['lightgbm'])
        trainer.fit(
            X_train.values, y_train.values,
            X_val.values, y_val.values,
            sample_weight=sample_weights,
            class_weight=class_weights,
            monitor=self.overfitting_monitor if is_final else None
        )
        
        y_val_pred = trainer.predict(X_val.values)
        y_val_proba = trainer.predict_proba(X_val.values)
        
        metrics = {
            'val_accuracy': float(accuracy_score(y_val, y_val_pred)),
            'val_log_loss': float(log_loss(y_val, y_val_proba)),
            'val_f1': float(f1_score(y_val, y_val_pred, average='macro')),
            'train_accuracy': float(accuracy_score(y_train, trainer.predict(X_train.values))),
            'train_log_loss': float(log_loss(y_train, trainer.predict_proba(X_train.values))),
            'best_iteration': trainer.best_iteration,
        }
        
        self._log('LIGHTGBM_TRAINED', {
            'best_iteration': trainer.best_iteration,
            'val_accuracy': metrics['val_accuracy'],
            'val_log_loss': metrics['val_log_loss'],
            'overfitting_gap': metrics['train_accuracy'] - metrics['val_accuracy'],
        })
        
        return trainer, metrics
    
    def predict(self, X: pd.DataFrame) -> Dict[str, Any]:
        if not self.models:
            raise ValueError("模型未训练")
        
        X_scaled = self.scaler.transform(X)
        
        results = {}
        for name, trainer in self.models.items():
            if trainer:
                proba = trainer.predict_proba(X_scaled)
                results[name] = {
                    'proba': proba,
                    'pred': np.argmax(proba, axis=1),
                }
        
        if len(results) >= 2:
            xgb_proba = results['xgb']['proba']
            lgb_proba = results['lgb']['proba']
            weights = self.ensemble.get_weights()
            final_proba = self.ensemble.predict(xgb_proba, lgb_proba, weights)
            results['ensemble'] = {
                'proba': final_proba,
                'pred': np.argmax(final_proba, axis=1),
                'weights': weights,
            }
        
        return results
    
    def save(self, path: str):
        import joblib
        save_data = {
            'models': {name: {'model': t.model, 'config': t.params} for name, t in self.models.items() if t},
            'scaler': self.scaler,
            'ensemble_weights': self.ensemble.get_weights(),
            'config': self.config,
            'training_log': self.training_log,
        }
        joblib.dump(save_data, path)
        logger.info(f"模型已保存到: {path}")
    
    def load(self, path: str):
        import joblib
        data = joblib.load(path)
        self.scaler = data['scaler']
        self.config = data['config']
        self.ensemble.model_weights = data['ensemble_weights']
        
        if 'xgb' in data['models'] and data['models']['xgb']:
            xgb_trainer = SklearnXGBoostTrainer(data['models']['xgb']['config'])
            xgb_trainer.model = data['models']['xgb']['model']
            self.models['xgb'] = xgb_trainer
        
        if 'lgb' in data['models'] and data['models']['lgb']:
            lgb_trainer = SklearnLightGBMTrainer(data['models']['lgb']['config'])
            lgb_trainer.model = data['models']['lgb']['model']
            self.models['lgb'] = lgb_trainer
        
        logger.info(f"模型已从 {path} 加载")


if __name__ == '__main__':
    print("=" * 60)
    print("高级模型训练器 - 模块测试")
    print("=" * 60)
    
    np.random.seed(42)
    n_samples = 500
    n_features = 20
    
    X = pd.DataFrame(np.random.randn(n_samples, n_features), columns=[f'f{i}' for i in range(n_features)])
    y = pd.Series(np.random.choice([0, 1, 2], n_samples, p=[0.5, 0.3, 0.2]))
    df_meta = pd.DataFrame({
        'league': ['英超2025-2026赛季'] * n_samples,
        'home_team_name': ['利物浦'] * n_samples,
        'away_team_name': ['曼城'] * n_samples,
    })
    
    trainer = AdvancedModelTrainer()
    result = trainer.train(X, y, df_meta)
    
    print("\n" + "=" * 60)
    print("📊 训练结果")
    print("=" * 60)
    print(f"  XGBoost Val Accuracy: {result['xgb_metrics']['val_accuracy']:.4f}")
    print(f"  LightGBM Val Accuracy: {result['lgb_metrics']['val_accuracy']:.4f}")
    print(f"  集成 Val Accuracy: {result['final_val_accuracy']:.4f}")
    print(f"  集成权重: {result['ensemble_weights']}")
    print(f"\n{result['overfitting_report']}")
