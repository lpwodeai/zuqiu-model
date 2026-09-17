import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.linear_model import LogisticRegression


class PlattScaledClassifier(BaseEstimator, ClassifierMixin):
    def __init__(self, base_model):
        self.base_model = base_model
        self.calibrators = {}
    
    def fit(self, X, y):
        probs = self.base_model.predict_proba(X)
        classes = np.unique(y)
        
        for i, cls in enumerate(classes):
            y_binary = (y == cls).astype(int)
            lr = LogisticRegression(max_iter=1000, solver='lbfgs')
            lr.fit(probs[:, i:i+1], y_binary)
            self.calibrators[cls] = lr
        
        self.classes_ = classes
        return self
    
    def predict_proba(self, X):
        probs = self.base_model.predict_proba(X)
        calibrated_probs = []
        
        for i, cls in enumerate(self.classes_):
            if cls in self.calibrators:
                calib_prob = self.calibrators[cls].predict_proba(probs[:, i:i+1])[:, 1]
                calibrated_probs.append(calib_prob)
        
        calibrated_probs = np.column_stack(calibrated_probs)
        row_sums = calibrated_probs.sum(axis=1, keepdims=True)
        calibrated_probs = calibrated_probs / row_sums
        
        return calibrated_probs
    
    def predict(self, X):
        probs = self.predict_proba(X)
        return self.classes_[np.argmax(probs, axis=1)]


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
        
        self.classes_ = self.meta_classifier_.classes_
        
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