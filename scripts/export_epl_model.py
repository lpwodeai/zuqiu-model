"""英超独立模型导出脚本 v3.0
将训练好的 LightGBM .pkl 模型转换为 JS 格式，供 prediction-service.js 加载
自动检测最新的 v3 模型文件
"""
import os, sys, pickle, json, glob, joblib, numpy as np
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')
EPL_DIR = os.path.join(ASSETS_DIR, 'epl')

# 自动检测最新模型
v3_pkls = sorted(glob.glob(os.path.join(EPL_DIR, 'lgb_model_epl_v3_*.pkl')))
if not v3_pkls:
    print("ERROR: 未找到 v3 模型文件")
    sys.exit(1)

model_path = v3_pkls[-1]
ts = model_path.split('lgb_model_epl_v3_')[1].replace('.pkl', '')
scaler_path = os.path.join(EPL_DIR, f'scaler_epl_v3_{ts}.pkl')
feat_path = os.path.join(EPL_DIR, f'selected_features_epl_v3_{ts}.pkl')

print(f"加载模型: {model_path}")
model = pickle.load(open(model_path, 'rb'))
print(f"  树数: {model.booster_.num_trees()}")
print(f"  学习率: {model.get_params().get('learning_rate', 'N/A')}")

# ============================================
# 转换 LightGBM 树为 JS 格式
# ============================================
def convert_lgb_to_js(model):
    booster = model.booster_
    dump = booster.dump_model()
    tree_info = dump['tree_info']
    
    trees = []
    for ti in tree_info:
        tree = ti['tree_structure']
        nodes = []
        
        def walk(node, parent_idx=-1):
            idx = len(nodes)
            if 'leaf_value' in node:
                nodes.append({
                    'idx': idx,
                    'leaf': node['leaf_value'],
                    'parent': parent_idx
                })
            else:
                nodes.append({
                    'idx': idx,
                    'feature': node.get('split_feature', 0),
                    'threshold': node.get('threshold', 0),
                    'left': 0,
                    'right': 0,
                    'parent': parent_idx
                })
                cur_idx = idx
                left_idx = walk(node['left_child'], cur_idx)
                right_idx = walk(node['right_child'], cur_idx)
                nodes[cur_idx]['left'] = left_idx
                nodes[cur_idx]['right'] = right_idx
            return idx
        
        walk(tree)
        trees.append({'nodes': nodes})
    
    return {
        'base': 0.0,
        'lr': model.get_params().get('learning_rate', 0.03),
        'trees': trees
    }

print("转换 LightGBM 模型为 JS 格式...")
js_model = convert_lgb_to_js(model)

# 保存 JS 模型
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
js_content = f"var LGB_EPL_MODEL = {json.dumps(js_model)};"

# assets/ 根目录 (prediction-service.js 引用路径)
stable_path = os.path.join(ASSETS_DIR, 'lgb_model_epl_export.js')
with open(stable_path, 'w', encoding='utf-8') as f:
    f.write(js_content)
print(f"JS 模型: {stable_path} ({len(js_content)} bytes)")

# 也保存到 epl/ 子目录
js_path = os.path.join(EPL_DIR, f'lgb_model_epl_export_{timestamp}.js')
with open(js_path, 'w', encoding='utf-8') as f:
    f.write(js_content)

# ============================================
# 导出 scaler
# ============================================
scaler = joblib.load(scaler_path)
scaler_js = {
    'mean': scaler.mean_.tolist(),
    'scale': scaler.scale_.tolist(),
    'feature_names': list(range(len(scaler.mean_)))
}
scaler_js_path = os.path.join(ASSETS_DIR, 'scaler_epl_export.js')
scaler_content = f"var SCALER_EPL_PARAMS = {json.dumps(scaler_js)};"
with open(scaler_js_path, 'w', encoding='utf-8') as f:
    f.write(scaler_content)
print(f"Scaler: {scaler_js_path}")

# ============================================
# 导出特征列表
# ============================================
sf = pickle.load(open(feat_path, 'rb'))
sf_js_path = os.path.join(ASSETS_DIR, 'selected_features_epl_export.js')
sf_content = f"var SELECTED_FEATURES_EPL = {json.dumps(sf)};"
with open(sf_js_path, 'w', encoding='utf-8') as f:
    f.write(sf_content)
print(f"特征列表: {sf_js_path} ({len(sf)} 维)")

# ============================================
# 元数据
# ============================================
meta = {
    'model_type': 'EPL-v3',
    'version': '3.0',
    'timestamp': timestamp,
    'source_timestamp': ts,
    'n_samples': 1141,
    'feature_dim': len(sf),
    'n_trees': len(js_model['trees']),
    'learning_rate': model.get_params().get('learning_rate', 0.03),
    'target_file': 'lgb_model_epl_export.js',
    'scaler_file': 'scaler_epl_export.js',
    'features_file': 'selected_features_epl_export.js'
}
meta_path = os.path.join(EPL_DIR, f'deploy_meta_v3_{timestamp}.json')
with open(meta_path, 'w', encoding='utf-8') as f:
    json.dump(meta, f, indent=2, ensure_ascii=False)
print(f"部署元数据: {meta_path}")

print(f"\n=== 导出完成 ===")
print(f"  版本: v3.0")
print(f"  特征维度: {len(sf)}")
print(f"  树数: {len(js_model['trees'])}")
print(f"  学习率: {meta['learning_rate']}")
print(f"  目标: prediction-service.js 已配置 LGB_EPL_MODEL_PATH")