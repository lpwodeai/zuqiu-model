import pandas as pd
import numpy as np
import json
import os
import yaml
import sys
import time
from datetime import datetime

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    print("Warning: xgboost not installed, will skip XGBoost training")

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    print("Warning: lightgbm not installed, will skip LightGBM training")

from sklearn.model_selection import train_test_split, TimeSeriesSplit, StratifiedKFold
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss, confusion_matrix, f1_score, roc_auc_score
from sklearn.preprocessing import StandardScaler, MinMaxScaler, RobustScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier, StackingClassifier as SklearnStackingClassifier
from sklearn.feature_selection import SelectKBest, mutual_info_classif, f_classif
from sklearn.decomposition import PCA
from sklearn.calibration import CalibratedClassifierCV
from sklearn.base import BaseEstimator, ClassifierMixin

OUTPUT_DIR = "g:/zuqiu/五大联赛专属模型/五大联赛专属模型/assets"
CONFIG_PATH = "g:/zuqiu/五大联赛专属模型/五大联赛专属模型/config.yaml"

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from feature_engineer import (
    load_config, filter_synthetic_data,
    detect_synthetic_data
)

from feature_engineering_pipeline import build_all_features, load_match_data
from feature_selection_optimization import optimize_feature_selection, get_selected_features
from auto_train import update_feedback_signal, calculate_prediction_error, calculate_model_odds_divergence

CONFIG = load_config()

def check_class_balance(y):
    counts = y.value_counts()
    total = len(y)
    print("\n类别分布检查:")
    for cls, count in counts.items():
        label = '客胜' if cls == 0 else ('平局' if cls == 1 else '主胜')
        print(f"  {label}: {count} ({count/total*100:.2f}%)")
    
    imbalance_ratio = counts.max() / counts.min()
    print(f"  不平衡比率: {imbalance_ratio:.2f}")
    
    return imbalance_ratio > 3

def apply_class_weight(X, y):
    class_counts = y.value_counts()
    class_weights = {cls: len(y) / (3 * count) for cls, count in class_counts.items()}
    print(f"\n应用类别权重: {class_weights}")
    return class_weights

def feature_selection(X, y, method='mutual_info', k=50):
    print(f"\n特征选择 ({method}, k={k})...")
    
    if method == 'mutual_info':
        selector = SelectKBest(score_func=mutual_info_classif, k=min(k, X.shape[1]))
    elif method == 'f_classif':
        selector = SelectKBest(score_func=f_classif, k=min(k, X.shape[1]))
    else:
        return X, []
    
    selector.fit(X, y)
    selected_indices = selector.get_support(indices=True)
    selected_features = X.columns[selected_indices]
    
    X_selected = X.iloc[:, selected_indices]
    
    print(f"  原始特征: {X.shape[1]}")
    print(f"  选择后特征: {X_selected.shape[1]}")
    print(f"  重要特征: {selected_features.tolist()[:10]}...")
    
    return X_selected, selected_features.tolist()

def train_xgboost(X_train, y_train, X_val, y_val, class_weights=None, params=None):
    if xgb is None:
        return None, None
    
    xgb_config = CONFIG.get('model', {}).get('xgboost', {})
    
    model = xgb.XGBClassifier(
        objective='multi:softprob',
        num_class=3,
        eval_metric='mlogloss',
        max_depth=xgb_config.get('max_depth', 4),
        learning_rate=xgb_config.get('learning_rate', 0.05),
        subsample=xgb_config.get('subsample', 0.8),
        colsample_bytree=xgb_config.get('colsample_bytree', 0.8),
        gamma=xgb_config.get('gamma', 0.1),
        min_child_weight=xgb_config.get('min_child_weight', 3),
        reg_alpha=xgb_config.get('reg_alpha', 0.1),
        reg_lambda=xgb_config.get('reg_lambda', 3.0),
        random_state=42,
        n_jobs=-1,
        n_estimators=xgb_config.get('num_boost_round', 150)
    )
    
    if class_weights:
        model.set_params(class_weight=class_weights)
    
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)])
    
    y_pred = model.predict_proba(X_val)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    f1 = f1_score(y_val, y_pred_class, average='weighted')
    
    y_pred_train = model.predict_proba(X_train)
    train_accuracy = accuracy_score(y_train, np.argmax(y_pred_train, axis=1))
    train_ll = log_loss(y_train, y_pred_train)
    
    print(f"XGBoost - Train Acc: {train_accuracy:.4f}, Train LogLoss: {train_ll:.4f}")
    print(f"XGBoost - Val Acc: {accuracy:.4f}, Val LogLoss: {ll:.4f}, Brier: {brier:.4f}, F1: {f1:.4f}")
    
    return model, {'accuracy': accuracy, 'log_loss': ll, 'brier': brier, 'f1': f1,
                   'train_accuracy': train_accuracy, 'train_log_loss': train_ll}

def train_lightgbm(X_train, y_train, X_val, y_val, class_weights=None, params=None):
    if lgb is None:
        return None, None
    
    lgb_config = CONFIG.get('model', {}).get('lightgbm', {})
    
    model = lgb.LGBMClassifier(
        objective='multiclass',
        num_class=3,
        metric='multi_logloss',
        max_depth=lgb_config.get('max_depth', 4),
        learning_rate=lgb_config.get('learning_rate', 0.05),
        num_leaves=lgb_config.get('num_leaves', 31),
        subsample=lgb_config.get('subsample', 0.8),
        colsample_bytree=lgb_config.get('colsample_bytree', 0.8),
        reg_alpha=lgb_config.get('reg_alpha', 0.1),
        reg_lambda=lgb_config.get('reg_lambda', 3.0),
        min_child_weight=lgb_config.get('min_child_weight', 3),
        min_data_in_leaf=lgb_config.get('min_data_in_leaf', 20),
        random_state=42,
        verbose=0,
        n_estimators=lgb_config.get('num_boost_round', 150)
    )
    
    if class_weights:
        model.set_params(class_weight=class_weights)
    
    model.fit(X_train, y_train, eval_set=[(X_val, y_val)])
    
    y_pred = model.predict_proba(X_val)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    f1 = f1_score(y_val, y_pred_class, average='weighted')
    
    y_pred_train = model.predict_proba(X_train)
    train_accuracy = accuracy_score(y_train, np.argmax(y_pred_train, axis=1))
    train_ll = log_loss(y_train, y_pred_train)
    
    print(f"LightGBM - Train Acc: {train_accuracy:.4f}, Train LogLoss: {train_ll:.4f}")
    print(f"LightGBM - Val Acc: {accuracy:.4f}, Val LogLoss: {ll:.4f}, Brier: {brier:.4f}, F1: {f1:.4f}")
    
    return model, {'accuracy': accuracy, 'log_loss': ll, 'brier': brier, 'f1': f1,
                   'train_accuracy': train_accuracy, 'train_log_loss': train_ll}

class StackingClassifier(BaseEstimator, ClassifierMixin):
    def __init__(self, estimators, meta_classifier=None, use_meta_features=True, use_original_features=True, cv=5):
        self.estimators = estimators
        self.meta_classifier = meta_classifier if meta_classifier else LogisticRegression(max_iter=1000, random_state=42)
        self.use_meta_features = use_meta_features
        self.use_original_features = use_original_features
        self.cv = cv
        self.fitted_estimators_ = {}
        self.meta_classifier_ = None
    
    def _generate_meta_features(self, X):
        meta_features = []
        for name, estimator in self.fitted_estimators_.items():
            if estimator is not None:
                probs = estimator.predict_proba(X)
                meta_features.append(probs)
        
        if len(meta_features) == 0:
            return np.zeros((len(X), 0))
        
        return np.hstack(meta_features)
    
    def fit(self, X, y):
        print("  Stacking - 训练基学习器...")
        
        for name, estimator in self.estimators.items():
            if estimator is not None:
                print(f"    训练 {name}...")
                estimator.fit(X, y)
                self.fitted_estimators_[name] = estimator
        
        print("  Stacking - 生成meta-features...")
        meta_features = self._generate_meta_features(X)
        
        print("  Stacking - 合并特征...")
        if self.use_original_features and self.use_meta_features:
            if isinstance(X, pd.DataFrame):
                X_combined = np.hstack([X.values, meta_features])
            else:
                X_combined = np.hstack([X, meta_features])
        elif self.use_meta_features:
            X_combined = meta_features
        else:
            if isinstance(X, pd.DataFrame):
                X_combined = X.values
            else:
                X_combined = X
        
        print(f"  Stacking - 第二层输入维度: {X_combined.shape[1]}")
        
        print("  Stacking - 训练meta-classifier...")
        self.meta_classifier_ = self.meta_classifier.fit(X_combined, y)
        
        return self
    
    def predict_proba(self, X):
        meta_features = self._generate_meta_features(X)
        
        if self.use_original_features and self.use_meta_features:
            if isinstance(X, pd.DataFrame):
                X_combined = np.hstack([X.values, meta_features])
            else:
                X_combined = np.hstack([X, meta_features])
        elif self.use_meta_features:
            X_combined = meta_features
        else:
            if isinstance(X, pd.DataFrame):
                X_combined = X.values
            else:
                X_combined = X
        
        return self.meta_classifier_.predict_proba(X_combined)
    
    def predict(self, X):
        proba = self.predict_proba(X)
        return np.argmax(proba, axis=1)

def calculate_rps(y_true, y_pred):
    if len(y_pred.shape) == 1:
        y_pred = np.column_stack([1 - y_pred, y_pred])
    
    n_classes = y_pred.shape[1]
    rps = 0.0
    
    for i in range(len(y_true)):
        cum_true = np.zeros(n_classes)
        cum_pred = np.zeros(n_classes)
        
        for c in range(n_classes):
            if c > 0:
                cum_true[c] = cum_true[c-1]
                cum_pred[c] = cum_pred[c-1]
            if y_true.iloc[i] == c if isinstance(y_true, pd.Series) else y_true[i] == c:
                cum_true[c] += 1
            cum_pred[c] += y_pred[i, c]
        
        rps += np.sum((cum_true - cum_pred) ** 2) / (n_classes - 1)
    
    return rps / len(y_true)

def calculate_brier_decomposition(y_true, y_pred):
    n_classes = y_pred.shape[1]
    y_onehot = np.zeros((len(y_true), n_classes))
    
    for i in range(len(y_true)):
        cls = y_true.iloc[i] if isinstance(y_true, pd.Series) else y_true[i]
        y_onehot[i, cls] = 1
    
    overall_brier = brier_score_loss(y_onehot, y_pred)
    
    reliability = 0.0
    resolution = 0.0
    uncertainty = 0.0
    
    for c in range(n_classes):
        for i in range(len(y_true)):
            reliability += (y_pred[i, c] - y_onehot[i, c]) ** 2
        
        p_mean = np.mean(y_pred[:, c])
        p_var = np.var(y_pred[:, c])
        resolution += p_var
        
        p_true = np.mean(y_onehot[:, c])
        uncertainty += p_true * (1 - p_true)
    
    reliability = reliability / len(y_true) / n_classes
    
    return {
        'overall_brier': overall_brier,
        'reliability': reliability,
        'resolution': resolution,
        'uncertainty': uncertainty,
        'decomposition_check': reliability - resolution + uncertainty
    }

def calculate_calibration_error(y_true, y_pred):
    n_classes = y_pred.shape[1]
    calibration_errors = []
    
    for c in range(n_classes):
        mask = (y_true == c).values if isinstance(y_true, pd.Series) else (y_true == c)
        avg_pred = np.mean(y_pred[mask, c]) if np.any(mask) else 0.5
        avg_true = np.mean(mask) if np.any(mask) else 0.5
        calibration_errors.append(abs(avg_pred - avg_true))
    
    return {
        'per_class_error': calibration_errors,
        'mean_calibration_error': np.mean(calibration_errors)
    }

def train_ensemble(X_train, y_train, X_val, y_val, models):
    print("\n训练集成模型 (Stacking)...")
    
    estimators = {}
    for name, model in models.items():
        if model is not None:
            estimators[name] = model
    
    if len(estimators) < 2:
        print("  集成模型需要至少2个基模型")
        return None, None
    
    stacking_clf = StackingClassifier(
        estimators=estimators,
        meta_classifier=LogisticRegression(max_iter=1000, random_state=42, class_weight='balanced'),
        use_meta_features=True,
        use_original_features=True,
        cv=5
    )
    stacking_clf.fit(X_train, y_train)
    
    y_pred = stacking_clf.predict_proba(X_val)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    f1 = f1_score(y_val, y_pred_class, average='weighted')
    
    rps = calculate_rps(y_val, y_pred)
    brier_decomp = calculate_brier_decomposition(y_val, y_pred)
    calib_error = calculate_calibration_error(y_val, y_pred)
    
    y_pred_train = stacking_clf.predict_proba(X_train)
    train_accuracy = accuracy_score(y_train, np.argmax(y_pred_train, axis=1))
    
    print(f"Stacking - Train Acc: {train_accuracy:.4f}")
    print(f"Stacking - Val Acc: {accuracy:.4f}, Val LogLoss: {ll:.4f}, Brier: {brier:.4f}, F1: {f1:.4f}")
    print(f"Stacking - RPS: {rps:.4f}")
    print(f"Stacking - Brier分解: Reliability={brier_decomp['reliability']:.4f}, Resolution={brier_decomp['resolution']:.4f}, Uncertainty={brier_decomp['uncertainty']:.4f}")
    print(f"Stacking - 校准误差: Mean={calib_error['mean_calibration_error']:.4f}")
    
    return stacking_clf, {'accuracy': accuracy, 'log_loss': ll, 'brier': brier, 'f1': f1,
                          'train_accuracy': train_accuracy, 'rps': rps,
                          'brier_reliability': brier_decomp['reliability'],
                          'brier_resolution': brier_decomp['resolution'],
                          'brier_uncertainty': brier_decomp['uncertainty'],
                          'mean_calibration_error': calib_error['mean_calibration_error']}

def apply_probability_calibration(model, X_val, y_val, method='isotonic'):
    print(f"\n概率校准 ({method})...")
    
    calibrated_model = CalibratedClassifierCV(model, method=method, cv=5)
    calibrated_model.fit(X_val, y_val)
    
    y_pred = calibrated_model.predict_proba(X_val)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    
    print(f"  校准后 - Accuracy: {accuracy:.4f}, Brier: {brier:.4f}")
    
    return calibrated_model, {'accuracy': accuracy, 'brier': brier}

def evaluate_with_time_series_split(X, y, df, n_splits=5):
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    results = {
        'xgboost': [],
        'lightgbm': [],
        'ensemble': []
    }
    
    print(f"\n{'='*70}")
    print(f"时间序列交叉验证 ({n_splits}折)")
    print(f"{'='*70}")
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        print(f"\n--- 第 {fold+1}/{n_splits} 折 ---")
        
        df_train_fold = df.iloc[train_idx].copy()
        df_val_fold = df.iloc[val_idx].copy()
        
        print(f"  训练集: {len(df_train_fold)} 场, 验证集: {len(df_val_fold)} 场")
        
        print("  重新计算训练集特征...")
        X_train_fold, y_train_fold, _ = build_all_features(df_train_fold)
        
        print("  重新计算验证集特征...")
        df_val_with_train = pd.concat([df_train_fold, df_val_fold]).sort_values('date').reset_index(drop=True)
        X_all_fold, y_all_fold, _ = build_all_features(df_val_with_train)
        X_val_fold = X_all_fold.iloc[-len(df_val_fold):]
        y_val_fold = y_all_fold.iloc[-len(df_val_fold):]
        
        feature_selection_enabled = CONFIG.get('feature_selection', {}).get('enabled', True)
        if feature_selection_enabled:
            k_features = CONFIG.get('feature_selection', {}).get('k', 50)
            method = CONFIG.get('feature_selection', {}).get('method', 'mutual_info')
            X_train_fold, selected_features = feature_selection(X_train_fold, y_train_fold, method=method, k=k_features)
            X_val_fold = X_val_fold[selected_features]
        
        scaler_fold = StandardScaler()
        X_train_scaled = scaler_fold.fit_transform(X_train_fold)
        X_val_scaled = scaler_fold.transform(X_val_fold)
        
        has_class_imbalance = check_class_balance(y_train_fold)
        class_weights = apply_class_weight(X_train_fold, y_train_fold) if has_class_imbalance else None
        
        models_fold = {}
        
        if xgb is not None:
            xgb_model_fold, xgb_metrics_fold = train_xgboost(X_train_scaled, y_train_fold.values,
                                                              X_val_scaled, y_val_fold.values,
                                                              class_weights=class_weights)
            models_fold['xgb'] = xgb_model_fold
            if xgb_metrics_fold:
                results['xgboost'].append(xgb_metrics_fold)
        
        if lgb is not None:
            lgb_model_fold, lgb_metrics_fold = train_lightgbm(X_train_scaled, y_train_fold.values,
                                                               X_val_scaled, y_val_fold.values,
                                                               class_weights=class_weights)
            models_fold['lgb'] = lgb_model_fold
            if lgb_metrics_fold:
                results['lightgbm'].append(lgb_metrics_fold)
        
        if len([m for m in models_fold.values() if m is not None]) >= 2:
            ensemble_model, ensemble_metrics = train_ensemble(X_train_scaled, y_train_fold.values,
                                                              X_val_scaled, y_val_fold.values, models_fold)
            if ensemble_metrics:
                results['ensemble'].append(ensemble_metrics)
    
    print(f"\n{'='*70}")
    print("时间序列交叉验证汇总")
    print(f"{'='*70}")
    
    for model_name, model_results in results.items():
        if model_results:
            print(f"\n{model_name.upper()} 交叉验证结果:")
            acc_list = [r['accuracy'] for r in model_results]
            ll_list = [r['log_loss'] for r in model_results]
            brier_list = [r['brier'] for r in model_results]
            f1_list = [r['f1'] for r in model_results]
            train_acc_list = [r.get('train_accuracy', r['accuracy']) for r in model_results]
            
            print(f"  训练准确率: {np.mean(train_acc_list):.4f} ± {np.std(train_acc_list):.4f}")
            print(f"  验证准确率: {np.mean(acc_list):.4f} ± {np.std(acc_list):.4f}")
            print(f"  验证LogLoss: {np.mean(ll_list):.4f} ± {np.std(ll_list):.4f}")
            print(f"  验证Brier: {np.mean(brier_list):.4f} ± {np.std(brier_list):.4f}")
            print(f"  验证F1: {np.mean(f1_list):.4f} ± {np.std(f1_list):.4f}")
            
            if 'rps' in model_results[0]:
                rps_list = [r['rps'] for r in model_results]
                print(f"  验证RPS: {np.mean(rps_list):.4f} ± {np.std(rps_list):.4f}")
            
            if 'brier_reliability' in model_results[0]:
                rel_list = [r['brier_reliability'] for r in model_results]
                res_list = [r['brier_resolution'] for r in model_results]
                unc_list = [r['brier_uncertainty'] for r in model_results]
                print(f"  Brier分解 - Reliability: {np.mean(rel_list):.4f} ± {np.std(rel_list):.4f}")
                print(f"               Resolution: {np.mean(res_list):.4f} ± {np.std(res_list):.4f}")
                print(f"               Uncertainty: {np.mean(unc_list):.4f} ± {np.std(unc_list):.4f}")
            
            if 'mean_calibration_error' in model_results[0]:
                calib_list = [r['mean_calibration_error'] for r in model_results]
                print(f"  平均校准误差: {np.mean(calib_list):.4f} ± {np.std(calib_list):.4f}")
    
    return results

def analyze_confusion_matrix(model, X, y, model_name):
    y_pred = model.predict(X)
    cm = confusion_matrix(y, y_pred)
    
    print(f"\n{model_name} 混淆矩阵:")
    print(f"            预测客胜  预测平局  预测主胜")
    print(f"实际客胜      {cm[0,0]:<8} {cm[0,1]:<8} {cm[0,2]:<8}")
    print(f"实际平局      {cm[1,0]:<8} {cm[1,1]:<8} {cm[1,2]:<8}")
    print(f"实际主胜      {cm[2,0]:<8} {cm[2,1]:<8} {cm[2,2]:<8}")
    
    class_acc = [cm[i,i]/cm[i].sum() if cm[i].sum() > 0 else 0 for i in range(3)]
    print(f"\n  各类别准确率: 客胜={class_acc[0]:.4f}, 平局={class_acc[1]:.4f}, 主胜={class_acc[2]:.4f}")
    
    return cm.tolist(), class_acc

def generate_feature_importance(model, feature_names, model_name, top_n=20):
    if model_name.lower().startswith('xgb'):
        importances = model.feature_importances_
        importance_df = pd.DataFrame({'feature': feature_names, 'importance': importances})
    elif model_name.lower().startswith('lgb'):
        importances = model.feature_importances_
        importance_df = pd.DataFrame({'feature': feature_names, 'importance': importances})
    else:
        return None
    
    importance_df = importance_df.sort_values('importance', ascending=False).head(top_n)
    
    print(f"\n{model_name} 特征重要性 (Top {top_n}):")
    for _, row in importance_df.iterrows():
        print(f"  {row['feature']}: {row['importance']:.4f}")
    
    return importance_df.to_dict('records')

def save_model_report(report, version=None):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"model_report_{timestamp}" + (f"_v{version}" if version else "") + ".json"
    output_path = os.path.join(OUTPUT_DIR, filename)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n模型报告已保存到: {output_path}")
    return output_path

def main(version=None):
    start_time = time.time()
    
    print("=" * 70)
    print("足球比赛预测模型训练 Pipeline v2.0")
    print(f"版本: {version}" if version else "")
    print("=" * 70)
    
    print("\n1. 加载比赛数据...")
    df = load_match_data(include_csv_odds=True)
    print(f"   共加载 {len(df)} 场比赛")
    print(f"   日期范围: {df['date'].min().strftime('%Y-%m-%d')} 至 {df['date'].max().strftime('%Y-%m-%d')}")
    
    print("\n2. 数据质量检查与过滤...")
    flags = detect_synthetic_data(df)
    df = filter_synthetic_data(df)
    
    print("\n3. 构建特征...")
    X, y, feature_info = build_all_features(df)
    print(f"   总特征维度: {X.shape[1]}")
    
    print("\n4. 特征选择...")
    feature_selection_enabled = CONFIG.get('feature_selection', {}).get('enabled', True)
    if feature_selection_enabled:
        k_features = CONFIG.get('feature_selection', {}).get('k', 50)
        method = CONFIG.get('feature_selection', {}).get('method', 'mutual_info')
        X, selected_features = feature_selection(X, y, method=method, k=k_features)
    
    print("\n5. 类别分布检查...")
    has_class_imbalance = check_class_balance(y)
    
    print("\n6. 时间序列交叉验证...")
    cv_results = evaluate_with_time_series_split(X, y, df, n_splits=5)
    
    print("\n7. 训练最终模型...")
    validation_split = CONFIG.get('training', {}).get('validation_split', 0.2)
    train_size = int(len(df) * (1 - validation_split))
    
    X_train, X_val = X.iloc[:train_size], X.iloc[train_size:]
    y_train, y_val = y.iloc[:train_size], y.iloc[train_size:]
    
    print(f"   训练集: {len(X_train)} 场")
    print(f"   验证集: {len(X_val)} 场")
    
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_val_scaled = scaler.transform(X_val)
    
    class_weights = apply_class_weight(X_train, y_train) if has_class_imbalance else None
    
    final_models = {}
    
    print("\n   训练XGBoost...")
    xgb_model, xgb_metrics = train_xgboost(X_train_scaled, y_train.values, X_val_scaled, y_val.values,
                                           class_weights=class_weights)
    final_models['xgb'] = xgb_model
    
    print("\n   训练LightGBM...")
    lgb_model, lgb_metrics = train_lightgbm(X_train_scaled, y_train.values, X_val_scaled, y_val.values,
                                            class_weights=class_weights)
    final_models['lgb'] = lgb_model
    
    print("\n   训练集成模型...")
    ensemble_model, ensemble_metrics = train_ensemble(X_train_scaled, y_train.values,
                                                      X_val_scaled, y_val.values, final_models)
    
    print("\n8. 概率校准...")
    calibration_enabled = CONFIG.get('calibration', {}).get('enabled', True)
    if calibration_enabled and xgb_model:
        xgb_calibrated, xgb_calib_metrics = apply_probability_calibration(xgb_model, X_val_scaled, y_val.values)
    
    print("\n9. 特征重要性分析...")
    xgb_importance = generate_feature_importance(xgb_model, X.columns.tolist(), 'XGBoost') if xgb_model else None
    lgb_importance = generate_feature_importance(lgb_model, X.columns.tolist(), 'LightGBM') if lgb_model else None
    
    print("\n9.5. 特征重要性动态更新...")
    models_for_importance = []
    if xgb_model:
        models_for_importance.append({'model': xgb_model, 'type': 'xgboost'})
    if lgb_model:
        models_for_importance.append({'model': lgb_model, 'type': 'lightgbm'})
    
    if models_for_importance:
        optimize_feature_selection(X, y, models=models_for_importance, feature_names=X.columns.tolist())
    
    print("\n10. 混淆矩阵分析...")
    if xgb_model:
        xgb_cm, xgb_class_acc = analyze_confusion_matrix(xgb_model, X_val_scaled, y_val.values, 'XGBoost')
    if lgb_model:
        lgb_cm, lgb_class_acc = analyze_confusion_matrix(lgb_model, X_val_scaled, y_val.values, 'LightGBM')
    
    print("\n11. 更新反馈信号...")
    if ensemble_model:
        model_probs = ensemble_model.predict_proba(X_val_scaled)
        predictions = model_probs
    elif xgb_model:
        model_probs = xgb_model.predict_proba(X_val_scaled)
        predictions = model_probs
    else:
        predictions = None
    
    if predictions is not None:
        val_df = df.iloc[train_size:]
        feedback_result = update_feedback_signal(val_df, predictions)
        print(f"   异常样本数: {feedback_result['anomaly_count']}")
        print(f"   异常率: {feedback_result['anomaly_rate']:.2%}")
        print(f"   平均预测误差: {feedback_result['avg_prediction_error']:.4f}")
    
    print("\n12. 保存模型报告...")
    report = {
        'timestamp': datetime.now().isoformat(),
        'version': version,
        'data_summary': {
            'total_matches': len(df),
            'date_range': f"{df['date'].min().strftime('%Y-%m-%d')} to {df['date'].max().strftime('%Y-%m-%d')}",
            'feature_dimensions': X.shape[1],
            'class_distribution': y.value_counts().to_dict()
        },
        'cross_validation': cv_results,
        'final_models': {
            'xgboost': xgb_metrics,
            'lightgbm': lgb_metrics,
            'ensemble': ensemble_metrics
        },
        'feature_importance': {
            'xgboost': xgb_importance,
            'lightgbm': lgb_importance
        },
        'training_time_seconds': round(time.time() - start_time, 2)
    }
    
    report_path = save_model_report(report, version)
    
    print("\n" + "=" * 70)
    print("训练完成!")
    print("=" * 70)
    
    if ensemble_metrics:
        print(f"\n最佳模型 (Stacking Ensemble):")
        print(f"  准确率: {ensemble_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {ensemble_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {ensemble_metrics['brier']:.4f}")
        print(f"  F1 Score: {ensemble_metrics['f1']:.4f}")
        if 'rps' in ensemble_metrics:
            print(f"  RPS: {ensemble_metrics['rps']:.4f}")
        if 'mean_calibration_error' in ensemble_metrics:
            print(f"  平均校准误差: {ensemble_metrics['mean_calibration_error']:.4f}")
    elif xgb_metrics:
        print(f"\n最佳模型 (XGBoost):")
        print(f"  准确率: {xgb_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {xgb_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {xgb_metrics['brier']:.4f}")
        print(f"  F1 Score: {xgb_metrics['f1']:.4f}")
    
    print(f"\n训练耗时: {round(time.time() - start_time, 2)} 秒")
    
    return report

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='足球比赛预测模型训练 v2.0')
    parser.add_argument('--version', type=str, help='模型版本号')
    args = parser.parse_args()
    main(version=args.version)