"""P0-2: 特征维度对齐验证（训练端 vs 推理端）"""
import pickle, os, json, glob, sys
import numpy as np

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 65)
print("=== P0-2: 特征维度对齐验证 ===")
print("=" * 65)

# ============================================================
# 1. 训练端特征维度
# ============================================================
print("\n--- 1. 训练端特征维度 ---")

lgb_files = sorted(glob.glob(os.path.join(BASE, 'lgb_model_*.pkl')))
xgb_files = sorted(glob.glob(os.path.join(BASE, 'xgb_model_*.pkl')))
sf_files = sorted(glob.glob(os.path.join(BASE, 'selected_features_*.pkl')))

lgb_path = lgb_files[-1] if lgb_files else None
xgb_path = xgb_files[-1] if xgb_files else None
sf_path = sf_files[-1] if sf_files else None

print(f"  LightGBM: {os.path.basename(lgb_path) if lgb_path else 'N/A'}")
print(f"  XGBoost:   {os.path.basename(xgb_path) if xgb_path else 'N/A'}")
print(f"  Features:  {os.path.basename(sf_path) if sf_path else 'N/A'}")

if lgb_path:
    lgb = pickle.load(open(lgb_path, 'rb'))
    lgb_feat_count = lgb.num_feature()
    print(f"  LightGBM num_feature(): {lgb_feat_count}")
    if hasattr(lgb, 'feature_name_'):
        lgb_names = list(lgb.feature_name_)
        print(f"  LightGBM feature_name_ 长度: {len(lgb_names)}")
        print(f"  前5: {lgb_names[:5]}")
        print(f"  后5: {lgb_names[-5:]}")

if xgb_path:
    xgb_model = pickle.load(open(xgb_path, 'rb'))
    try:
        xgb_feat_count = xgb_model.get_booster().num_features()
    except AttributeError:
        xgb_feat_count = xgb_model.n_features_in_ if hasattr(xgb_model, 'n_features_in_') else 'N/A'
    print(f"  XGBoost num_features(): {xgb_feat_count}")
    if hasattr(xgb_model, 'feature_names_in_'):
        xgb_names = list(xgb_model.feature_names_in_)
        print(f"  XGBoost feature_names_in_ 长度: {len(xgb_names)}")

if sf_path:
    sf = pickle.load(open(sf_path, 'rb'))
    sf_list = list(sf)
    print(f"  selected_features 数量: {len(sf_list)}")
    print(f"  前5: {sf_list[:5]}")
    print(f"  后5: {sf_list[-5:]}")

# Check metadata
md_path = os.path.join(BASE, 't005v3_metadata.json')
if os.path.exists(md_path):
    with open(md_path, 'r', encoding='utf-8') as f:
        md = json.load(f)
    print(f"\n  t005v3_metadata.json:")
    for k in ['feature_count', 'n_features', 'input_dim', 'model_type', 'num_features']:
        if k in md:
            print(f"    {k}: {md[k]}")
    if 'features' in md:
        print(f"    features 列表长度: {len(md['features'])}")

# ============================================================
# 2. 推理端特征维度（build_all_features 实际输出）
# ============================================================
print("\n--- 2. 推理端特征维度 ---")

try:
    from feature_utils import load_match_data_odds, build_all_features
    
    df = load_match_data_odds()
    print(f"  数据加载: {len(df)} 场比赛")
    
    X_all, y_all = build_all_features(df, include_odds=True)
    print(f"  build_all_features 输出维度: {X_all.shape[1]}")
    print(f"  前5个特征名: {list(X_all.columns[:5])}")
    print(f"  后5个特征名: {list(X_all.columns[-5:])}")
    
    inference_feat_count = X_all.shape[1]
    inference_feat_names = list(X_all.columns)
    
except Exception as e:
    print(f"  推理端特征构建失败: {e}")
    print(f"  尝试直接查询 selected_features 中的特征名...")
    inference_feat_count = None
    inference_feat_names = None

# ============================================================
# 3. 对比分析
# ============================================================
print("\n" + "=" * 65)
print("=== 3. 对比分析 ===")
print("=" * 65)

train_feat_count = len(sf_list) if sf_path else None

print(f"\n  训练端 selected_features: {train_feat_count} 维")
print(f"  推理端 build_all_features: {inference_feat_count} 维")

if train_feat_count and inference_feat_count:
    diff = inference_feat_count - train_feat_count
    print(f"  差异: {diff} 维")
    
    # 检查 selected_features 中的特征是否都在推理端输出中
    if inference_feat_names:
        missing = [f for f in sf_list if f not in inference_feat_names]
        extra = [f for f in inference_feat_names if f not in sf_list]
        
        if missing:
            print(f"\n  ⚠️ selected_features 中有 {len(missing)} 个特征不在推理端输出中:")
            for f in missing[:10]:
                print(f"    - {f}")
        else:
            print(f"\n  ✅ selected_features 中所有 {len(sf_list)} 个特征都在推理端输出中")
        
        if extra:
            print(f"\n  ℹ️ 推理端输出中有 {len(extra)} 个额外特征（未被 selected_features 选中）:")
            for f in extra[:10]:
                print(f"    - {f}")
            if len(extra) > 10:
                print(f"    ... 共 {len(extra)} 个")
        
        # 检查训练时实际使用的特征
        if lgb_path and hasattr(lgb, 'feature_name_'):
            lgb_names_set = set(lgb_names)
            sf_set = set(sf_list)
            
            lgb_not_in_sf = lgb_names_set - sf_set
            sf_not_in_lgb = sf_set - lgb_names_set
            
            if lgb_not_in_sf:
                print(f"\n  ⚠️ LightGBM feature_name_ 中有 {len(lgb_not_in_sf)} 个特征不在 selected_features 中")
            if sf_not_in_lgb:
                print(f"\n  ⚠️ selected_features 中有 {len(sf_not_in_lgb)} 个特征不在 LightGBM feature_name_ 中")
            if not lgb_not_in_sf and not sf_not_in_lgb:
                print(f"\n  ✅ LightGBM feature_name_ 与 selected_features 完全一致 ({len(lgb_names)} 维)")

# ============================================================
# 4. 结论
# ============================================================
print("\n" + "=" * 65)
print("=== 4. 结论 ===")
print("=" * 65)

if train_feat_count and inference_feat_count:
    if train_feat_count == inference_feat_count:
        print("\n  ✅ 训练端与推理端特征维度一致，无需修复")
    elif train_feat_count < inference_feat_count:
        print(f"\n  ℹ️ 推理端输出 {inference_feat_count} 维，训练时通过 selected_features 筛选为 {train_feat_count} 维")
        print(f"  ℹ️ 这是正常的工作流程：build_all_features → selected_features → 模型输入")
        if sf_path and inference_feat_names:
            if all(f in inference_feat_names for f in sf_list):
                print("  ✅ 筛选后的特征全部在推理端输出中，可以正常对齐")
            else:
                print("  ❌ 筛选后的特征有缺失，存在对齐问题！")
    else:
        print(f"  ❌ 训练端特征数 ({train_feat_count}) > 推理端特征数 ({inference_feat_count})，维度不匹配！")
else:
    print("\n  ⚠️ 无法完成完整对比，部分数据缺失")