#!/usr/bin/env python3
"""
从 advanced_model_*.pkl (方案C训练产出) 导出 XGB/LGB 模型为 JS 格式，
供 prediction-service.js 加载使用。

用法:
  python scripts/export_models_to_js.py [--pkl <path>] [--backup]

默认 pkl: assets/advanced_model_20260812_175719.pkl (方案C)
输出:
  assets/xgb_model_export.js
  assets/lgb_model_export.js
  assets/feature_scaler_params.js
"""
import os
import sys
import json
import shutil
import argparse
from datetime import datetime

# 路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')


def convert_xgb_sklearn_to_js(model):
    """将 XGBClassifier (sklearn API) 转为 JS 格式"""
    if model is None:
        return None

    booster = model.get_booster()
    base_score = booster.attr('base_score')
    if base_score is None:
        base_score = 0.5
    else:
        base_score = float(base_score)

    # learning rate from sklearn params
    lr = model.get_params().get('learning_rate', 0.1)

    tree_dump = booster.get_dump()
    trees = []

    for tree_str in tree_dump:
        nodes = []
        for line in tree_str.split('\n'):
            line = line.strip()
            if not line:
                continue

            node_id_str = line.split(':')[0]
            node_id = int(node_id_str)

            if 'leaf=' in line:
                leaf_val = float(line.split('leaf=')[1].strip())
                nodes.append({'node_id': node_id, 'leaf': leaf_val})
            else:
                parts = line.split('[')
                cond_part = parts[1].split(']')[0]
                feature, rest = cond_part.split('<')
                split_info = {
                    'feature': feature.strip(),
                    'threshold': float(rest.strip())
                }
                goto_part = parts[1].split(']')[1]
                left = int(goto_part.split('yes=')[1].split(',')[0])
                right = int(goto_part.split('no=')[1].split(',')[0])
                split_info['left'] = left
                split_info['right'] = right
                nodes.append({'node_id': node_id, 'split': split_info})

        trees.append({'nodes': nodes})

    return {
        'base': base_score,
        'lr': lr,
        'trees': trees
    }


def convert_lgb_sklearn_to_js(model):
    """将 LGBMClassifier (sklearn API) 转为 JS 格式"""
    if model is None:
        return None

    booster = model.booster_
    tree_info = booster.dump_model()
    trees = []
    base_score = 0.5

    for tree in tree_info['tree_info']:
        nodes = []
        node_counter = [0]

        def parse_node(node):
            current_id = node_counter[0]
            node_counter[0] += 1

            if 'split_index' in node:
                feature_name = tree_info['feature_names'][node['split_index']]
                left_id = node_counter[0]
                right_id = node_counter[0] + 1

                nodes.append({
                    'node_id': current_id,
                    'split': {
                        'feature': feature_name,
                        'threshold': node['threshold'],
                        'left': left_id,
                        'right': right_id
                    }
                })

                parse_node(node['left_child'])
                parse_node(node['right_child'])
            else:
                leaf_value = node['leaf_value']
                if isinstance(leaf_value, (list, tuple)):
                    leaf_value = leaf_value[0]
                nodes.append({
                    'node_id': current_id,
                    'leaf': leaf_value
                })

        parse_node(tree['tree_structure'])
        trees.append({'nodes': nodes})

    return {
        'base': base_score,
        'lr': model.get_params().get('learning_rate', 0.1),
        'trees': trees
    }


def export_scaler_to_js(scaler, feature_names=None):
    """导出 StandardScaler 参数为 JS"""
    if scaler is None:
        return None

    params = {
        'mean': scaler.mean_.tolist() if hasattr(scaler, 'mean_') else [],
        'scale': scaler.scale_.tolist() if hasattr(scaler, 'scale_') else [],
    }
    if feature_names:
        params['feature_names'] = list(feature_names)
    else:
        # 生成占位特征名 f0, f1, ...
        n = len(params['mean'])
        params['feature_names'] = [f'f{i}' for i in range(n)]
    return params


def main():
    parser = argparse.ArgumentParser(description='从 pkl 导出模型为 JS 格式')
    parser.add_argument('--pkl', default=os.path.join(ASSETS_DIR, 'advanced_model_20260812_175719.pkl'),
                        help='pkl 文件路径 (默认方案C)')
    parser.add_argument('--backup', action='store_true', default=True,
                        help='备份旧 JS 文件 (默认开启)')
    args = parser.parse_args()

    pkl_path = args.pkl
    if not os.path.exists(pkl_path):
        print(f"❌ pkl 文件不存在: {pkl_path}")
        sys.exit(1)

    print(f"📦 加载模型: {pkl_path}")
    import joblib
    data = joblib.load(pkl_path)

    models = data.get('models', {})
    scaler = data.get('scaler')
    ensemble_weights = data.get('ensemble_weights', {})

    xgb_info = models.get('xgb', {})
    lgb_info = models.get('lgb', {})
    xgb_model = xgb_info.get('model') if xgb_info else None
    lgb_model = lgb_info.get('model') if lgb_info else None

    if xgb_model is None:
        print("❌ pkl 中未找到 XGBoost 模型")
        sys.exit(1)
    if lgb_model is None:
        print("❌ pkl 中未找到 LightGBM 模型")
        sys.exit(1)

    print(f"   XGBoost: {type(xgb_model).__name__}, n_estimators={getattr(xgb_model, 'n_estimators', '?')}")
    print(f"   LightGBM: {type(lgb_model).__name__}, n_estimators={getattr(lgb_model, 'n_estimators', '?')}")
    print(f"   Scaler: {type(scaler).__name__ if scaler else 'None'}")
    print(f"   Ensemble weights: {ensemble_weights}")

    # 备份旧文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if args.backup:
        for fname in ['xgb_model_export.js', 'lgb_model_export.js', 'feature_scaler_params.js']:
            old_path = os.path.join(ASSETS_DIR, fname)
            if os.path.exists(old_path):
                bak_path = os.path.join(ASSETS_DIR, f'{fname}.bak_{timestamp}')
                shutil.copy2(old_path, bak_path)
                print(f"   📋 备份: {fname} → {os.path.basename(bak_path)}")

    # 导出 XGBoost
    print("\n🔄 导出 XGBoost → xgb_model_export.js ...")
    xgb_js = convert_xgb_sklearn_to_js(xgb_model)
    xgb_path = os.path.join(ASSETS_DIR, 'xgb_model_export.js')
    with open(xgb_path, 'w', encoding='utf-8') as f:
        f.write(f"var XGB_MODEL = {json.dumps(xgb_js, indent=2)};")
    xgb_size = os.path.getsize(xgb_path)
    print(f"   ✅ 已保存 ({xgb_size:,} bytes, {len(xgb_js['trees'])} 棵树)")

    # 导出 LightGBM
    print("\n🔄 导出 LightGBM → lgb_model_export.js ...")
    lgb_js = convert_lgb_sklearn_to_js(lgb_model)
    lgb_path = os.path.join(ASSETS_DIR, 'lgb_model_export.js')
    with open(lgb_path, 'w', encoding='utf-8') as f:
        f.write(f"var LGB_MODEL = {json.dumps(lgb_js, indent=2)};")
    lgb_size = os.path.getsize(lgb_path)
    print(f"   ✅ 已保存 ({lgb_size:,} bytes, {len(lgb_js['trees'])} 棵树)")

    # 导出 scaler（从 XGBoost booster 获取真实 feature_names）
    if scaler is not None:
        print("\n🔄 导出 Scaler → feature_scaler_params.js ...")
        # 从 XGBoost booster 获取特征名
        try:
            booster = xgb_model.get_booster()
            feature_names = booster.feature_names
        except Exception:
            feature_names = None
        scaler_params = export_scaler_to_js(scaler, feature_names)
        scaler_path = os.path.join(ASSETS_DIR, 'feature_scaler_params.js')
        with open(scaler_path, 'w', encoding='utf-8') as f:
            f.write(f"var FEATURE_SCALER_PARAMS = {json.dumps(scaler_params, indent=2)};")
        scaler_size = os.path.getsize(scaler_path)
        n_feat = len(scaler_params.get('feature_names', []))
        print(f"   ✅ 已保存 ({scaler_size:,} bytes, {n_feat} 个特征, {len(scaler_params['mean'])} 个均值)")

    # 保存 ensemble weights
    weights_path = os.path.join(ASSETS_DIR, 'stacking_weights.json')
    with open(weights_path, 'w', encoding='utf-8') as f:
        json.dump({
            'xgb': ensemble_weights.get('xgb', 0.5),
            'lgb': ensemble_weights.get('lgb', 0.5),
            '_source': f'方案C pkl: {os.path.basename(pkl_path)}',
            '_exported_at': timestamp
        }, f, indent=2)
    print(f"\n   ✅ Ensemble weights 已保存 → stacking_weights.json (xgb={ensemble_weights.get('xgb', 0.5)}, lgb={ensemble_weights.get('lgb', 0.5)})")

    print(f"\n{'='*60}")
    print(f"✅ 方案C模型导出完成！")
    print(f"   源 pkl: {os.path.basename(pkl_path)}")
    print(f"   XGB 树数: {len(xgb_js['trees'])}")
    print(f"   LGB 树数: {len(lgb_js['trees'])}")
    print(f"   集成权重: XGB={ensemble_weights.get('xgb', 0.5)}, LGB={ensemble_weights.get('lgb', 0.5)}")
    print(f"{'='*60}")
    print(f"\n💡 下一步: 重启 prediction-service 让它重新加载新模型")


if __name__ == '__main__':
    main()
