import sys
import sqlite3
import pandas as pd
import numpy as np
import yaml
import os
import json
from datetime import datetime

from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, log_loss, f1_score, confusion_matrix
from sklearn.ensemble import RandomForestClassifier
import xgboost as xgb
import lightgbm as lgb

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from feature_engineering_pipeline import (
    load_match_data, build_all_features, feature_selection_mutual_info
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)

DB_PATH = os.path.join(PROJECT_DIR, "data", "five_leagues.db")
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.yaml")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

def train_and_evaluate(X_train, y_train, X_test, y_test, model_type='xgboost'):
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    if model_type == 'xgboost':
        params = CONFIG.get('model', {}).get('xgboost', {})
        params = {k: v for k, v in params.items() if k not in ['num_boost_round', 'early_stopping_rounds']}
        params['objective'] = 'multi:softprob'
        params['num_class'] = 3
        
        dtrain = xgb.DMatrix(X_train_scaled, label=y_train)
        dtest = xgb.DMatrix(X_test_scaled, label=y_test)
        
        model = xgb.train(
            params,
            dtrain,
            num_boost_round=100,
            evals=[(dtest, 'eval')],
            early_stopping_rounds=15,
            verbose_eval=False
        )
        y_proba = model.predict(dtest)
        y_pred_classes = np.argmax(y_proba, axis=1)
        feature_importance = model.get_score(importance_type='gain')
        
    elif model_type == 'lightgbm':
        params = CONFIG.get('model', {}).get('lightgbm', {})
        params = {k: v for k, v in params.items() if k not in ['num_boost_round', 'early_stopping_rounds']}
        params['objective'] = 'multiclass'
        params['num_class'] = 3
        params['verbosity'] = -1
        
        lgb_train = lgb.Dataset(X_train_scaled, label=y_train)
        lgb_test = lgb.Dataset(X_test_scaled, label=y_test, reference=lgb_train)
        
        model = lgb.train(
            params,
            lgb_train,
            num_boost_round=100,
            valid_sets=[lgb_test],
            callbacks=[lgb.early_stopping(stopping_rounds=15), lgb.log_evaluation(period=0)]
        )
        y_proba = model.predict(X_test_scaled)
        y_pred_classes = np.argmax(y_proba, axis=1)
        feature_importance = model.feature_importance(importance_type='gain')
        feature_importance = dict(zip(X_train.columns, feature_importance))
        
    else:
        model = RandomForestClassifier(
            n_estimators=100,
            random_state=42,
            class_weight='balanced',
            n_jobs=-1
        )
        model.fit(X_train_scaled, y_train)
        y_pred_classes = model.predict(X_test_scaled)
        y_proba = model.predict_proba(X_test_scaled)
        feature_importance = dict(zip(X_train.columns, model.feature_importances_))
    
    accuracy = accuracy_score(y_test, y_pred_classes)
    logloss = log_loss(y_test, y_proba)
    f1_macro = f1_score(y_test, y_pred_classes, average='macro')
    cm = confusion_matrix(y_test, y_pred_classes)
    
    per_class_accuracy = []
    for i in range(len(cm)):
        if cm[i].sum() > 0:
            per_class_accuracy.append(cm[i, i] / cm[i].sum())
        else:
            per_class_accuracy.append(0.0)
    
    return {
        'accuracy': accuracy,
        'log_loss': logloss,
        'f1_macro': f1_macro,
        'confusion_matrix': cm.tolist(),
        'per_class_accuracy': per_class_accuracy,
        'feature_importance': feature_importance,
        'sample_count': len(y_test)
    }

def leave_one_league_out_validation(df, X, y, model_types=['xgboost', 'lightgbm', 'random_forest']):
    leagues = df['competition_name'].unique()
    results = {}
    
    print(f"\n{'='*80}")
    print(f"Leave-One-League-Out 交叉验证")
    print(f"{'='*80}")
    print(f"\n参与验证的联赛: {leagues}")
    print(f"总样本数: {len(df)}")
    
    for test_league in sorted(leagues):
        print(f"\n{'='*80}")
        print(f"测试联赛: {test_league}")
        print(f"{'='*80}")
        
        train_mask = df['competition_name'] != test_league
        test_mask = df['competition_name'] == test_league
        
        X_train, X_test = X[train_mask], X[test_mask]
        y_train, y_test = y[train_mask], y[test_mask]
        
        print(f"  训练样本: {len(X_train)} ({df.loc[train_mask, 'competition_name'].value_counts().to_dict()})")
        print(f"  测试样本: {len(X_test)}")
        
        results[test_league] = {}
        
        for model_type in model_types:
            print(f"\n  训练 {model_type}...")
            try:
                result = train_and_evaluate(X_train, y_train, X_test, y_test, model_type)
                results[test_league][model_type] = result
                
                print(f"    准确率: {result['accuracy']:.4f}")
                print(f"    LogLoss: {result['log_loss']:.4f}")
                print(f"    F1(macro): {result['f1_macro']:.4f}")
                print(f"    分类准确率: 负={result['per_class_accuracy'][0]:.4f}, 平={result['per_class_accuracy'][1]:.4f}, 胜={result['per_class_accuracy'][2]:.4f}")
            except Exception as e:
                print(f"    训练失败: {str(e)}")
                results[test_league][model_type] = {'error': str(e)}
    
    return results

def all_leagues_training(df, X, y, model_types=['xgboost', 'lightgbm', 'random_forest']):
    print(f"\n{'='*80}")
    print(f"全联赛训练验证")
    print(f"{'='*80}")
    
    # 时序切分：按比赛日期升序，前 80% 训练、后 20% 测试。
    # 禁止随机 shuffle / stratify，防止未来数据混入训练集导致时间泄露。
    has_date = 'date' in df.columns
    if has_date:
        dates = pd.to_datetime(df['date'], errors='coerce')
        sort_idx = dates.argsort(kind='stable').values
        sorted_dates = dates.iloc[sort_idx]
    else:
        sort_idx = np.arange(len(X))
        sorted_dates = None
    
    split_idx = int(len(sort_idx) * 0.8)
    train_pos, test_pos = sort_idx[:split_idx], sort_idx[split_idx:]
    
    X_train, X_test = X.iloc[train_pos], X.iloc[test_pos]
    y_train, y_test = y.iloc[train_pos], y.iloc[test_pos]
    
    print(f"--- 时序切分明细 ---")
    print(f"  总样本数: {len(X)}")
    if has_date:
        total_min, total_max = sorted_dates.min().date(), sorted_dates.max().date()
        split_point_date = sorted_dates.iloc[split_idx - 1].date()
        print(f"  总日期范围: {total_min} ~ {total_max}")
        print(f"  切分点索引: {split_idx} / {len(sort_idx)} (前 80%)")
        print(f"  切分点日期: {split_point_date} (训练集最后一场)")
        # 检查切分点是否落在同日多场比赛之间（同日是否被拆分）
        day_before = sorted_dates.iloc[split_idx - 1].date()
        day_after = sorted_dates.iloc[split_idx].date()
        if day_before == day_after:
            print(f"  ⚠️ 提示: 切分点同日 '{day_before}' 两侧均有样本，属同日多场比赛正常切分（非泄露）")
    else:
        print(f"  ⚠️ 数据无 date 列，退化为按原始顺序切分")
    
    train_dates = sorted_dates.iloc[:split_idx] if (has_date and sorted_dates is not None) else None
    test_dates = sorted_dates.iloc[split_idx:] if (has_date and sorted_dates is not None) else None
    
    if has_date and train_dates is not None:
        print(f"  训练集: {len(X_train)} 场 ({train_dates.min().date()} ~ {train_dates.max().date()})  占比 {len(X_train)/len(X):.1%}")
        print(f"  测试集: {len(X_test)} 场 ({test_dates.min().date()} ~ {test_dates.max().date()})  占比 {len(X_test)/len(X):.1%}")
        print(f"  训练集标签分布: {y_train.value_counts().to_dict()}")
        print(f"  测试集标签分布: {y_test.value_counts().to_dict()}")
        # 时间泄露校验
        if train_dates.max() > test_dates.min():
            print(f"  ❌ 时序泄露: 训练集最大日期 {train_dates.max().date()} > 测试集最小日期 {test_dates.min().date()}")
        else:
            print(f"  ✅ 无时序泄露: 训练集最大日期 {train_dates.max().date()} <= 测试集最小日期 {test_dates.min().date()}")
    else:
        print(f"  训练集: {len(X_train)} 场")
        print(f"  测试集: {len(X_test)} 场")
    print(f"--- 时序切分明细结束 ---")
    
    results = {}
    for model_type in model_types:
        print(f"\n  训练 {model_type}...")
        try:
            result = train_and_evaluate(X_train, y_train, X_test, y_test, model_type)
            results[model_type] = result
            
            print(f"    准确率: {result['accuracy']:.4f}")
            print(f"    LogLoss: {result['log_loss']:.4f}")
            print(f"    F1(macro): {result['f1_macro']:.4f}")
        except Exception as e:
            print(f"    训练失败: {str(e)}")
            results[model_type] = {'error': str(e)}
    
    return results

def analyze_feature_importance_consistency(results):
    feature_importance_dict = {}
    
    for test_league, models in results.items():
        for model_type, model_result in models.items():
            if 'feature_importance' in model_result:
                for feature, importance in model_result['feature_importance'].items():
                    if feature not in feature_importance_dict:
                        feature_importance_dict[feature] = []
                    feature_importance_dict[feature].append(importance)
    
    consistency_results = {}
    for feature, importances in feature_importance_dict.items():
        if len(importances) >= 3:
            mean_importance = np.mean(importances)
            std_importance = np.std(importances)
            cv_importance = std_importance / (mean_importance + 1e-8)
            consistency_results[feature] = {
                'mean_importance': mean_importance,
                'std_importance': std_importance,
                'cv_importance': cv_importance,
                'count': len(importances)
            }
    
    sorted_consistency = sorted(consistency_results.items(), key=lambda x: x[1]['mean_importance'], reverse=True)
    
    return sorted_consistency

def generate_report(results, all_leagues_results, feature_importance_consistency):
    report = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'leave_one_league_out': results,
        'all_leagues_training': all_leagues_results,
        'feature_importance_consistency': feature_importance_consistency,
        'summary': {}
    }
    
    for model_type in ['xgboost', 'lightgbm', 'random_forest']:
        model_results = []
        for test_league, models in results.items():
            if model_type in models and 'accuracy' in models[model_type]:
                model_results.append(models[model_type]['accuracy'])
        
        if model_results:
            report['summary'][model_type] = {
                'mean_accuracy': np.mean(model_results),
                'std_accuracy': np.std(model_results),
                'min_accuracy': np.min(model_results),
                'max_accuracy': np.max(model_results),
                'cv_accuracy': np.std(model_results) / np.mean(model_results),
                'all_leagues_accuracy': all_leagues_results.get(model_type, {}).get('accuracy', 0)
            }
    
    report['summary']['feature_transferability'] = {
        'high_consistency_features': [
            feature for feature, stats in feature_importance_consistency[:20]
            if stats['cv_importance'] < 0.5
        ],
        'medium_consistency_features': [
            feature for feature, stats in feature_importance_consistency[20:40]
            if 0.5 <= stats['cv_importance'] < 1.0
        ],
        'low_consistency_features': [
            feature for feature, stats in feature_importance_consistency[40:]
            if stats['cv_importance'] >= 1.0
        ]
    }
    
    return report

def print_report(report):
    print(f"\n{'='*80}")
    print(f"跨联赛特征迁移验证报告")
    print(f"{'='*80}")
    print(f"\n生成时间: {report['timestamp']}")
    
    print(f"\n{'='*60}")
    print(f"模型性能汇总")
    print(f"{'='*60}")
    
    for model_type, stats in report['summary'].items():
        if model_type in ['xgboost', 'lightgbm', 'random_forest']:
            print(f"\n{model_type}:")
            print(f"  Leave-One-League-Out:")
            print(f"    平均准确率: {stats['mean_accuracy']:.4f}")
            print(f"    标准差: {stats['std_accuracy']:.4f}")
            print(f"    CV: {stats['cv_accuracy']:.4f}")
            print(f"    范围: [{stats['min_accuracy']:.4f}, {stats['max_accuracy']:.4f}]")
            print(f"  全联赛训练准确率: {stats['all_leagues_accuracy']:.4f}")
    
    print(f"\n{'='*60}")
    print(f"特征迁移性分析")
    print(f"{'='*60}")
    
    transfer = report['summary']['feature_transferability']
    print(f"\n高一致性特征 (CV < 0.5): {len(transfer['high_consistency_features'])}个")
    for feature in transfer['high_consistency_features'][:10]:
        print(f"  - {feature}")
    
    print(f"\n中一致性特征 (0.5 <= CV < 1.0): {len(transfer['medium_consistency_features'])}个")
    
    print(f"\n低一致性特征 (CV >= 1.0): {len(transfer['low_consistency_features'])}个")
    for feature in transfer['low_consistency_features'][:10]:
        print(f"  - {feature}")
    
    print(f"\n{'='*60}")
    print(f"特征重要性排名(前20)")
    print(f"{'='*60}")
    for i, (feature, stats) in enumerate(report['feature_importance_consistency'][:20]):
        print(f"{i+1:2d}. {feature:50s} 均值: {stats['mean_importance']:.4f}  CV: {stats['cv_importance']:.4f}")

def save_report(report):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = os.path.join(OUTPUT_DIR, f'cross_league_validation_report_{timestamp}.json')
    
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n报告已保存到: {report_path}")
    return report_path

def main():
    print("=" * 80)
    print("跨联赛特征迁移验证")
    print("=" * 80)
    
    print("\n1. 加载比赛数据...")
    df = load_match_data(DB_PATH)
    print(f"   加载记录数: {len(df)}")
    print(f"   联赛分布: {df['competition_name'].value_counts().to_dict()}")
    
    print("\n2. 构建特征...")
    X, y, feature_info = build_all_features(df)
    print(f"   特征维度: {X.shape[1]}")
    print(f"   样本数量: {X.shape[0]}")
    
    print("\n3. 特征选择...")
    mi_df, selected_features = feature_selection_mutual_info(X, y, top_n=80)
    X_selected = X[selected_features]
    print(f"   选择特征数量: {len(selected_features)}")
    
    print("\n4. Leave-One-League-Out 验证...")
    lol_results = leave_one_league_out_validation(df, X_selected, y)
    
    print("\n5. 全联赛训练验证...")
    all_leagues_results = all_leagues_training(df, X_selected, y)
    
    print("\n6. 特征重要性一致性分析...")
    feature_consistency = analyze_feature_importance_consistency(lol_results)
    
    print("\n7. 生成报告...")
    report = generate_report(lol_results, all_leagues_results, feature_consistency)
    
    print_report(report)
    
    save_report(report)
    
    print("\n" + "=" * 80)
    print("跨联赛验证完成")
    print("=" * 80)
    
    return report

if __name__ == '__main__':
    import sys
    report = main()