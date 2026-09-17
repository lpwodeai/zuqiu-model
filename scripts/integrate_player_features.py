import pandas as pd
import numpy as np
import os
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"

def load_existing_features():
    feature_files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith('features_') and f.endswith('.csv')]
    if not feature_files:
        print("未找到现有特征矩阵文件")
        return None
    
    latest_file = sorted(feature_files)[-1]
    print(f"加载现有特征矩阵: {latest_file}")
    return pd.read_csv(os.path.join(OUTPUT_DIR, latest_file))

def load_player_features():
    player_files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith('player_features_')]
    if not player_files:
        print("未找到球员特征文件")
        return None
    
    latest_file = sorted(player_files)[-1]
    print(f"加载球员特征矩阵: {latest_file}")
    return pd.read_csv(os.path.join(OUTPUT_DIR, latest_file))

def load_selected_player_features():
    selected_files = [f for f in os.listdir(OUTPUT_DIR) if f.startswith('selected_player_features_')]
    if not selected_files:
        print("未找到选择的球员特征文件")
        return None
    
    latest_file = sorted(selected_files)[-1]
    print(f"加载选择的球员特征: {latest_file}")
    df = pd.read_csv(os.path.join(OUTPUT_DIR, latest_file))
    return df['feature'].tolist()

def integrate_features(existing_features, player_features, selected_player_features=None):
    if selected_player_features:
        player_features = player_features[selected_player_features]
        print(f"使用选择的球员特征，维度: {len(selected_player_features)}")
    else:
        print(f"使用全部球员特征，维度: {player_features.shape[1]}")
    
    print(f"\n现有特征维度: {existing_features.shape[1]}")
    print(f"球员特征维度: {player_features.shape[1]}")
    
    common_cols = set(existing_features.columns) & set(player_features.columns)
    if common_cols:
        print(f"\n重叠列(将保留现有特征): {common_cols}")
        player_features = player_features.drop(columns=common_cols)
    
    integrated = pd.concat([existing_features, player_features], axis=1)
    integrated = integrated.loc[:, ~integrated.columns.duplicated()]
    
    print(f"\n整合后特征维度: {integrated.shape[1]}")
    
    return integrated

def analyze_integrated_features(X):
    print("\n整合特征分析:")
    print(f"总特征数: {X.shape[1]}")
    print(f"总样本数: {X.shape[0]}")
    
    missing_ratio = X.isnull().sum().mean() / X.shape[0] * 100
    print(f"平均缺失率: {missing_ratio:.2f}%")
    
    constant_cols = [col for col in X.columns if X[col].nunique() <= 1]
    print(f"常量特征数: {len(constant_cols)}")
    
    numeric_cols = X.select_dtypes(include=[np.number]).columns
    categorical_cols = X.select_dtypes(exclude=[np.number]).columns
    print(f"数值特征数: {len(numeric_cols)}")
    print(f"类别特征数: {len(categorical_cols)}")
    
    player_feature_count = sum(1 for col in X.columns if col.startswith('home_') or col.startswith('away_') or '_diff' in col)
    print(f"球员相关特征数: {player_feature_count}")
    
    return {
        'total_features': X.shape[1],
        'total_samples': X.shape[0],
        'avg_missing_ratio': missing_ratio,
        'constant_features': constant_cols,
        'numeric_features': len(numeric_cols),
        'categorical_features': len(categorical_cols),
        'player_features_count': player_feature_count
    }

def save_integrated_features(X):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'integrated_features_{timestamp}.csv'
    X.to_csv(os.path.join(OUTPUT_DIR, filename), index=False, encoding='utf-8')
    print(f"\n整合特征矩阵已保存: {filename}")
    return filename

def main():
    print("=" * 70)
    print("球员特征与现有回测数据整合验证")
    print("=" * 70)
    
    print("\n1. 加载现有特征矩阵...")
    existing_features = load_existing_features()
    if existing_features is None:
        print("警告: 未找到现有特征矩阵，将仅验证球员特征")
        existing_features = pd.DataFrame()
    
    print("\n2. 加载球员特征...")
    player_features = load_player_features()
    if player_features is None:
        print("错误: 未找到球员特征，退出")
        return
    
    print("\n3. 加载选择的球员特征...")
    selected_features = load_selected_player_features()
    
    if not existing_features.empty:
        print("\n4. 整合特征...")
        integrated = integrate_features(existing_features, player_features, selected_features)
        
        print("\n5. 分析整合结果...")
        analysis = analyze_integrated_features(integrated)
        
        print("\n6. 保存整合结果...")
        save_integrated_features(integrated)
        
        print("\n" + "=" * 70)
        print("整合验证完成!")
        print("=" * 70)
        print(f"\n整合特征总维度: {analysis['total_features']}")
        print(f"其中球员特征: {analysis['player_features_count']}")
        print(f"缺失率: {analysis['avg_missing_ratio']:.2f}%")
        print(f"常量特征: {len(analysis['constant_features'])}")
        
        return integrated
    else:
        print("\n仅验证球员特征...")
        analysis = analyze_integrated_features(player_features)
        
        print("\n" + "=" * 70)
        print("球员特征验证完成!")
        print("=" * 70)
        print(f"\n球员特征总维度: {analysis['total_features']}")
        print(f"缺失率: {analysis['avg_missing_ratio']:.2f}%")
        print(f"常量特征: {len(analysis['constant_features'])}")
        
        return player_features

if __name__ == "__main__":
    main()