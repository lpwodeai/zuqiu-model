import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
from sklearn.metrics import (
    accuracy_score, log_loss, brier_score_loss, f1_score,
    confusion_matrix, classification_report, roc_auc_score
)
from sklearn.preprocessing import StandardScaler

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(PROJECT_DIR, "assets")

def evaluate_model(model, X_train, y_train, X_val, y_val, model_name):
    y_pred_train = model.predict(X_train)
    y_pred_train_proba = model.predict_proba(X_train)
    
    y_pred_val = model.predict(X_val)
    y_pred_val_proba = model.predict_proba(X_val)
    
    results = {
        'model': model_name,
        'train': {
            'accuracy': accuracy_score(y_train, y_pred_train),
            'log_loss': log_loss(y_train, y_pred_train_proba),
            'brier_score': brier_score_loss(y_train, y_pred_train_proba, pos_label=2),
            'f1_score': f1_score(y_train, y_pred_train, average='weighted'),
            'classification_report': classification_report(y_train, y_pred_train, output_dict=True)
        },
        'val': {
            'accuracy': accuracy_score(y_val, y_pred_val),
            'log_loss': log_loss(y_val, y_pred_val_proba),
            'brier_score': brier_score_loss(y_val, y_pred_val_proba, pos_label=2),
            'f1_score': f1_score(y_val, y_pred_val, average='weighted'),
            'classification_report': classification_report(y_val, y_pred_val, output_dict=True),
            'confusion_matrix': confusion_matrix(y_val, y_pred_val).tolist()
        },
        'overfitting': {
            'accuracy_gap': accuracy_score(y_train, y_pred_train) - accuracy_score(y_val, y_pred_val),
            'log_loss_gap': log_loss(y_val, y_pred_val_proba) - log_loss(y_train, y_pred_train_proba)
        }
    }
    
    return results

def run_time_series_cv(X, y, model, model_name, n_splits=5, scaler=None):
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    fold_results = []
    metrics = {
        'accuracy': [],
        'log_loss': [],
        'brier_score': [],
        'f1_score': []
    }
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_train_fold, X_val_fold = X.iloc[train_idx], X.iloc[val_idx]
        y_train_fold, y_val_fold = y.iloc[train_idx], y.iloc[val_idx]
        
        if scaler:
            X_train_scaled = scaler.fit_transform(X_train_fold)
            X_val_scaled = scaler.transform(X_val_fold)
        else:
            X_train_scaled = X_train_fold.values
            X_val_scaled = X_val_fold.values
        
        model.fit(X_train_scaled, y_train_fold.values)
        
        fold_result = evaluate_model(model, X_train_scaled, y_train_fold.values,
                                     X_val_scaled, y_val_fold.values, model_name)
        fold_result['fold'] = fold
        fold_results.append(fold_result)
        
        metrics['accuracy'].append(fold_result['val']['accuracy'])
        metrics['log_loss'].append(fold_result['val']['log_loss'])
        metrics['brier_score'].append(fold_result['val']['brier_score'])
        metrics['f1_score'].append(fold_result['val']['f1_score'])
    
    summary = {
        'model': model_name,
        'n_splits': n_splits,
        'mean_accuracy': np.mean(metrics['accuracy']),
        'std_accuracy': np.std(metrics['accuracy']),
        'mean_log_loss': np.mean(metrics['log_loss']),
        'std_log_loss': np.std(metrics['log_loss']),
        'mean_brier_score': np.mean(metrics['brier_score']),
        'std_brier_score': np.std(metrics['brier_score']),
        'mean_f1_score': np.mean(metrics['f1_score']),
        'std_f1_score': np.std(metrics['f1_score']),
        'worst_fold_accuracy': min(metrics['accuracy']),
        'best_fold_accuracy': max(metrics['accuracy']),
        'stability_score': 1 - (max(metrics['accuracy']) - min(metrics['accuracy'])) / np.mean(metrics['accuracy']),
        'fold_results': fold_results
    }
    
    return summary

def evaluate_league_performance(X, y, df, model, model_name, scaler=None):
    league_results = {}
    
    for league in df['competition_name'].unique():
        league_mask = df['competition_name'] == league
        X_league = X[league_mask]
        y_league = y[league_mask]
        
        if len(X_league) < 100:
            continue
        
        train_size = int(len(X_league) * 0.8)
        X_train_l, X_val_l = X_league.iloc[:train_size], X_league.iloc[train_size:]
        y_train_l, y_val_l = y_league.iloc[:train_size], y_league.iloc[train_size:]
        
        if scaler:
            X_train_scaled = scaler.fit_transform(X_train_l)
            X_val_scaled = scaler.transform(X_val_l)
        else:
            X_train_scaled = X_train_l.values
            X_val_scaled = X_val_l.values
        
        model.fit(X_train_scaled, y_train_l.values)
        y_pred = model.predict(X_val_scaled)
        y_pred_proba = model.predict_proba(X_val_scaled)
        
        league_results[league] = {
            'matches': len(X_league),
            'accuracy': accuracy_score(y_val_l, y_pred),
            'log_loss': log_loss(y_val_l, y_pred_proba),
            'brier_score': brier_score_loss(y_val_l, y_pred_proba, pos_label=2),
            'f1_score': f1_score(y_val_l, y_pred, average='weighted'),
            'confusion_matrix': confusion_matrix(y_val_l, y_pred).tolist()
        }
    
    return league_results

def evaluate_temporal_stability(X, y, df, model, model_name, time_window='M', scaler=None):
    df['period'] = df['date'].dt.to_period(time_window)
    period_results = {}
    
    periods = df['period'].unique()
    
    for period in periods:
        period_mask = df['period'] == period
        X_period = X[period_mask]
        y_period = y[period_mask]
        
        if len(X_period) < 50:
            continue
        
        X_train_all = X[df['period'] < period]
        y_train_all = y[df['period'] < period]
        
        if len(X_train_all) < 100:
            continue
        
        if scaler:
            X_train_scaled = scaler.fit_transform(X_train_all)
            X_period_scaled = scaler.transform(X_period)
        else:
            X_train_scaled = X_train_all.values
            X_period_scaled = X_period.values
        
        model.fit(X_train_scaled, y_train_all.values)
        y_pred = model.predict(X_period_scaled)
        y_pred_proba = model.predict_proba(X_period_scaled)
        
        period_results[str(period)] = {
            'matches': len(X_period),
            'accuracy': accuracy_score(y_period, y_pred),
            'log_loss': log_loss(y_period, y_pred_proba),
            'brier_score': brier_score_loss(y_period, y_pred_proba, pos_label=2),
            'training_data_size': len(X_train_all)
        }
    
    return period_results

def evaluate_probability_calibration(y_true, y_proba):
    calibration_results = {}
    
    for class_idx in range(3):
        class_probs = y_proba[:, class_idx]
        class_labels = (y_true == class_idx).astype(int)
        
        bins = np.linspace(0, 1, 11)
        bin_means = []
        bin_actual = []
        
        for i in range(len(bins) - 1):
            mask = (class_probs >= bins[i]) & (class_probs < bins[i+1])
            if mask.sum() > 0:
                bin_means.append(class_probs[mask].mean())
                bin_actual.append(class_labels[mask].mean())
        
        ece = 0
        total_samples = len(y_true)
        for i in range(len(bin_means)):
            mask = (class_probs >= bins[i]) & (class_probs < bins[i+1])
            ece += abs(bin_means[i] - bin_actual[i]) * (mask.sum() / total_samples)
        
        calibration_results[f'class_{class_idx}'] = {
            'expected_calibration_error': ece,
            'bin_means': bin_means,
            'bin_actual': bin_actual
        }
    
    return calibration_results

def generate_validation_report(model, X, y, df, model_name, n_splits=5):
    print(f"\n{'='*70}")
    print(f"模型验证报告 - {model_name}")
    print(f"{'='*70}")
    
    scaler = StandardScaler()
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'model_name': model_name,
        'data_summary': {
            'total_matches': len(df),
            'feature_dimensions': X.shape[1],
            'date_range': f"{df['date'].min().strftime('%Y-%m-%d')} to {df['date'].max().strftime('%Y-%m-%d')}"
        }
    }
    
    print("\n1. 时间序列交叉验证...")
    cv_summary = run_time_series_cv(X, y, model, model_name, n_splits=n_splits, scaler=scaler)
    report['time_series_cv'] = cv_summary
    
    print(f"   平均准确率: {cv_summary['mean_accuracy']:.4f} ± {cv_summary['std_accuracy']:.4f}")
    print(f"   平均LogLoss: {cv_summary['mean_log_loss']:.4f}")
    print(f"   稳定性得分: {cv_summary['stability_score']:.4f}")
    
    print("\n2. 联赛性能评估...")
    league_performance = evaluate_league_performance(X, y, df, model, model_name, scaler=scaler)
    report['league_performance'] = league_performance
    
    for league, stats in league_performance.items():
        print(f"   {league}: 准确率={stats['accuracy']:.4f}, LogLoss={stats['log_loss']:.4f}")
    
    print("\n3. 时间稳定性评估...")
    temporal_stability = evaluate_temporal_stability(X, y, df, model, model_name, scaler=scaler)
    report['temporal_stability'] = temporal_stability
    
    if temporal_stability:
        accuracies = [v['accuracy'] for v in temporal_stability.values()]
        print(f"   时间窗口准确率范围: {min(accuracies):.4f} ~ {max(accuracies):.4f}")
        print(f"   时间稳定性: {1 - (max(accuracies) - min(accuracies)) / np.mean(accuracies):.4f}")
    
    print("\n4. 概率校准评估...")
    X_scaled = scaler.fit_transform(X)
    model.fit(X_scaled, y.values)
    y_proba = model.predict_proba(X_scaled)
    calibration_results = evaluate_probability_calibration(y.values, y_proba)
    report['probability_calibration'] = calibration_results
    
    for cls, results in calibration_results.items():
        print(f"   {cls}: ECE={results['expected_calibration_error']:.4f}")
    
    print("\n5. 综合评估...")
    overall_score = calculate_overall_score(cv_summary, league_performance, temporal_stability, calibration_results)
    report['overall_score'] = overall_score
    
    print(f"   综合评分: {overall_score:.2f}/100")
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = os.path.join(OUTPUT_DIR, f"validation_report_{model_name}_{timestamp}.json")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n验证报告已保存到: {output_path}")
    
    return report

def calculate_overall_score(cv_summary, league_performance, temporal_stability, calibration_results):
    score = 0
    
    accuracy_weight = 0.3
    stability_weight = 0.2
    calibration_weight = 0.2
    league_consistency_weight = 0.3
    
    accuracy_score = min(100, cv_summary['mean_accuracy'] * 200)
    score += accuracy_score * accuracy_weight
    
    stability_score = cv_summary['stability_score'] * 100
    score += stability_score * stability_weight
    
    avg_ece = np.mean([r['expected_calibration_error'] for r in calibration_results.values()])
    calibration_score = max(0, 100 - avg_ece * 500)
    score += calibration_score * calibration_weight
    
    if league_performance:
        league_accuracies = [v['accuracy'] for v in league_performance.values()]
        league_consistency = 1 - np.std(league_accuracies) / np.mean(league_accuracies) if np.mean(league_accuracies) > 0 else 0
        league_consistency_score = league_consistency * 100
        score += league_consistency_score * league_consistency_weight
    
    return score

def compare_models(models, X, y, df, n_splits=5):
    print("\n" + "=" * 70)
    print("模型对比分析")
    print("=" * 70)
    
    scaler = StandardScaler()
    comparison_results = []
    
    for model_name, model in models.items():
        print(f"\n评估模型: {model_name}")
        
        cv_summary = run_time_series_cv(X, y, model, model_name, n_splits=n_splits, scaler=scaler)
        
        comparison_results.append({
            'model': model_name,
            'mean_accuracy': cv_summary['mean_accuracy'],
            'std_accuracy': cv_summary['std_accuracy'],
            'mean_log_loss': cv_summary['mean_log_loss'],
            'mean_brier_score': cv_summary['mean_brier_score'],
            'stability_score': cv_summary['stability_score'],
            'worst_fold': cv_summary['worst_fold_accuracy']
        })
        
        print(f"  平均准确率: {cv_summary['mean_accuracy']:.4f} ± {cv_summary['std_accuracy']:.4f}")
        print(f"  平均LogLoss: {cv_summary['mean_log_loss']:.4f}")
        print(f"  稳定性得分: {cv_summary['stability_score']:.4f}")
    
    df_results = pd.DataFrame(comparison_results)
    df_results = df_results.sort_values('mean_accuracy', ascending=False)
    
    print("\n模型排名:")
    for idx, row in df_results.iterrows():
        print(f"  {idx+1}. {row['model']}: 准确率={row['mean_accuracy']:.4f}, 稳定性={row['stability_score']:.4f}")
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'comparison': comparison_results,
        'ranking': df_results.to_dict('records')
    }
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = os.path.join(OUTPUT_DIR, f"model_comparison_{timestamp}.json")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    return report, df_results