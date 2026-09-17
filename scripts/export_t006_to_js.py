#!/usr/bin/env python3
"""
T-006 低进球分类器 LightGBM → JS 导出
将 assets/t006_lowgoal_classifier_v1.pkl 转为 JS 树结构,供 prediction-service.js 加载。

树节点格式与 assets/lgb_model_export.js 一致:
  {node_id, split:{feature, threshold, left, right}} | {node_id, leaf}
以便复用 shared/prediction-engine.js 的 predictTree(L1093-1137)命名分支。

base / lr 计算说明:
  LightGBM 二分类 objective='binary sigmoid:1',dump_model() 返回的 leaf_value 已
  隐含学习率(是"贡献值"而非"梯度")。因此 Python 端 predict_proba 公式为:
      probability = sigmoid(Σ leaf_value)
  不需要额外的 base 或 lr 项。

  JS 端 predictT006Lowgoal 公式为 sigmoid(base + lr * Σleaf)(复用 lgb_model_export.js
  的 predictTree 命名分支)。为保持 JS/Python 严格一致,本脚本导出 base=0.0, lr=1.0,
  使 JS 公式退化为 sigmoid(Σleaf),与 Python 完全一致。

  零特征反推法仅作为元数据保留(诊断用),验证 sigmoid(L_zero) ≈ p_zero:
    1. p_zero = model.predict_proba(zero_features)[0, 1]   # Python 端 ground truth
    2. L_zero = Σ 所有树在零输入下到达的叶子值(用与 JS predictTree 一致的 `<` 比较)
    3. 校验: sigmoid(L_zero) ≈ p_zero  (LightGBM leaf_value 已含 lr,无需补偿)

输出:
  assets/t006_lowgoal_export.js   — 树 + base + lr + threshold + base_rate + metadata
  assets/t006_feature_spec.js     — 特征名 + 缺失值回退策略(JS 端构建特征时参考)

用法:
  python scripts/export_t006_to_js.py [--pkl <path>] [--backup]
"""
import os
import sys
import json
import math
import shutil
import argparse
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / 'assets'
DEFAULT_PKL = ASSETS_DIR / 't006_lowgoal_classifier_v1.pkl'


def walk_tree_zero(node):
    """递归遍历 LightGBM tree_structure,在零特征输入下到达叶子,返回叶子值。

    使用与 JS predictTree 一致的 `<` 比较:feature_val(=0) < threshold ? left : right
    leaf_value 可能是 list(多类)或 float(二分类),统一返回 float。
    """
    if 'leaf_value' in node:
        lv = node['leaf_value']
        if isinstance(lv, (list, tuple)):
            return float(lv[0])
        return float(lv)
    # internal node: feature_val = 0
    threshold = float(node['threshold'])
    # 0 < threshold ? left : right(与 JS predictTree L1130 一致)
    child = node['left_child'] if 0 < threshold else node['right_child']
    return walk_tree_zero(child)


def parse_node(node, feature_names, counter, nodes):
    """递归解析 tree_structure 为 {node_id, split|leaf} 格式。

    与 export_models_to_js.convert_lgb_sklearn_to_js 格式一致,但修正了 right_id 计算:
    原实现在递归处理左子树之前就设定 right_id=counter+1,仅当左子节点是叶子时正确;
    当左子节点是内部节点时,右子节点的实际 id 会因左子树大小而偏移,导致 predictTree 走错分支。
    本实现采用"先占位→处理左子树→处理右子树→回填"模式(与 export_epl_model.walk 一致)。

    节点类型判断:用 `'leaf_value' not in node` 反向判断内部节点。
    LightGBM dump_model() 返回的内部节点字段是 `split_feature`(特征索引),
    而非 `split_index`;若误用 `split_index in node` 判断会全部走 leaf 分支,
    导致树结构塌缩为根节点的 leaf_value,JS Σleaf 与 Python 严重不符。
    """
    cur_id = counter[0]
    counter[0] += 1

    if 'leaf_value' not in node:
        # 内部节点:用 split_feature 取特征索引(LightGBM dump 的标准字段)
        feat_idx = node.get('split_feature', node.get('split_index', 0))
        feat_name = feature_names[feat_idx]
        # 占位:先 append None,处理完子树后回填
        placeholder_idx = len(nodes)
        nodes.append(None)

        # 处理左子树:左子节点拿到当前 counter[0]
        left_id = counter[0]
        parse_node(node['left_child'], feature_names, counter, nodes)

        # 处理右子树:左子树处理完后,counter[0] 已前进到右子节点的 id
        right_id = counter[0]
        parse_node(node['right_child'], feature_names, counter, nodes)

        # 回填当前节点
        nodes[placeholder_idx] = {
            'node_id': cur_id,
            'split': {
                'feature': feat_name,
                'threshold': float(node['threshold']),
                'left': left_id,
                'right': right_id
            }
        }
    else:
        leaf_value = node['leaf_value']
        if isinstance(leaf_value, (list, tuple)):
            leaf_value = leaf_value[0]
        nodes.append({'node_id': cur_id, 'leaf': float(leaf_value)})


def convert_t006_lgb_to_js(bundle):
    """将 t006 LightGBM 二分类器转为 JS 树结构 + base + metadata。"""
    model = bundle['model']  # lgb.LGBMClassifier
    booster = model.booster_
    dump = booster.dump_model()
    tree_info = dump['tree_info']

    # 特征名:优先用 dump 的 feature_names(已验证等于 bundle['feature_cols'])
    feature_names = dump.get('feature_names') or bundle['feature_cols']
    # 一致性校验
    if feature_names != bundle['feature_cols']:
        print(f'⚠️ 警告: dump feature_names 与 bundle.feature_cols 不一致,使用 bundle.feature_cols')
        feature_names = list(bundle['feature_cols'])

    lr = float(model.get_params().get('learning_rate', 0.05))

    # === base / lr 处理 ===
    # 关键:LightGBM dump_model() 返回的 leaf_value 已隐含学习率(是"贡献值"而非"梯度"),
    # 因此 Python 端 predict_proba 公式为 sigmoid(Σleaf_value),不需要额外的 base 或 lr。
    # JS 端 predictT006Lowgoal 公式为 sigmoid(base + lr * Σleaf),
    # 为保持一致,导出 base=0.0, lr=1.0,使 JS 公式退化为 sigmoid(Σleaf)。
    #
    # 零特征反推法仅作为元数据保留(诊断用),验证:
    #   p_zero = sigmoid(L_zero) ← 由 LightGBM 在零特征下遍历得到的 Σleaf
    n_feat = len(feature_names)
    X_zero = np.zeros((1, n_feat), dtype=np.float64)
    p_zero = float(model.predict_proba(X_zero)[0, 1])
    if not (0 < p_zero < 1):
        # 极端情况:predict_proba 给出 0 或 1,logit 无定义
        print(f'⚠️ 警告: p_zero={p_zero} 越界,base 将用对数变换防溢出')
        p_zero = max(1e-7, min(1 - 1e-7, p_zero))
    logit_zero = float(math.log(p_zero / (1 - p_zero)))

    # 遍历所有树取零输入叶子值之和(诊断用,验证 sigmoid(L_zero) ≈ p_zero)
    L_zero = 0.0
    for tree in tree_info:
        L_zero += walk_tree_zero(tree['tree_structure'])
    # 校验:零特征反推一致性(LightGBM leaf_value 已含 lr,故 sigmoid(L_zero) 应≈p_zero)
    p_zero_check = 1.0 / (1.0 + math.exp(-L_zero))
    if abs(p_zero_check - p_zero) > 1e-6:
        print(f'⚠️ 校验警告: sigmoid(L_zero)={p_zero_check:.6f} vs p_zero={p_zero:.6f}'
              f'(差异 {abs(p_zero_check - p_zero):.2e})')

    # 导出值:base=0, lr=1(JS 公式退化为 sigmoid(Σleaf),与 Python 严格一致)
    base = 0.0
    js_lr = 1.0

    # === 转换树结构 ===
    trees = []
    for tree in tree_info:
        nodes = []
        counter = [0]
        parse_node(tree['tree_structure'], feature_names, counter, nodes)
        trees.append({'nodes': nodes})

    # === 元数据 ===
    n_lowgoal = int(bundle.get('n_lowgoal', 0))
    n_samples = int(bundle.get('n_samples', 0))
    base_rate = n_lowgoal / n_samples if n_samples > 0 else 0.22

    return {
        'base': base,
        'lr': js_lr,
        'trees': trees,
        'feature_cols': list(bundle['feature_cols']),
        'best_threshold': float(bundle.get('best_threshold', 0.5)),
        'base_rate': float(base_rate),
        'metadata': {
            'auc': float(bundle.get('oof_auc', 0)),
            'f1': float(bundle.get('oof_f1', 0)),
            'recall': float(bundle.get('oof_recall', 0)),
            'precision': float(bundle.get('oof_precision', 0)),
            'n_samples': n_samples,
            'n_lowgoal': n_lowgoal,
            'base_rate': float(base_rate),
            'version': bundle.get('version', 'v1'),
            'trained_at': bundle.get('trained_at', ''),
            'objective': dump.get('objective', 'binary'),
            'sigmoid_required': True,  # 提示 JS 端必须做 sigmoid
            'base_computation': 'lightgbm_leaf_already_includes_lr',
            # LightGBM dump_model 的 leaf_value 已隐含学习率(贡献值),
            # 故 JS 公式 sigmoid(base + lr*Σleaf) 中 base=0, lr=1
            'real_learning_rate': lr,   # 真实学习率(参考,JS 推理不使用)
            'p_zero': p_zero,
            'L_zero': L_zero,
            'logit_zero': logit_zero,
            'lgb_params': bundle.get('lgb_params', {})
        }
    }


def build_feature_spec(bundle):
    """特征规格:每个特征的缺失值回退策略,供 JS buildT006Features 参考对齐。

    严格对齐 t006_lowgoal_classifier.py L244-L252 的缺失值填充逻辑。
    JS 端不 require 此文件,值硬编码在 buildT006Features 中以保证一致性。
    """
    return {
        'feature_cols': list(bundle['feature_cols']),
        'fallbacks': {
            # 与 t006_lowgoal_classifier.py L244-L252 严格一致
            'score_implied_total': 2.5,            # LEAGUE_AVG_GOALS
            'avg_draw_score_odds': 'draw_x_5',    # JS 端解释为 draw * 5
            'odds_00': 50.0,
            'odds_11': 50.0,
            'odds_10': 50.0,
            'odds_01': 50.0,
            'draw_over_implied': 'draw_div_2_5',  # draw / LEAGUE_AVG_GOALS
            'prob_draw_x_implied': 'prob_draw_x_2_5',
            'low_score_odds_sum': 200.0,
            'low_score_prob_sum': 0.08,
            'draw_minus_asym': 'draw',            # 即用 draw 值
        },
        'low_score_keys': ['0:0', '0:1', '1:0', '1:1'],
        'draw_score_keys': ['0:0', '1:1', '2:2', '3:3'],
        'league_mapping': {
            'FL1': {'is_ligue1': 1, 'is_serie_a': 0, 'is_la_liga': 0, 'is_premier_league': 0, 'is_bundesliga': 0},
            'IT':  {'is_ligue1': 0, 'is_serie_a': 1, 'is_la_liga': 0, 'is_premier_league': 0, 'is_bundesliga': 0},
            'LaLiga': {'is_ligue1': 0, 'is_serie_a': 0, 'is_la_liga': 1, 'is_premier_league': 0, 'is_bundesliga': 0},
            'PL':  {'is_ligue1': 0, 'is_serie_a': 0, 'is_la_liga': 0, 'is_premier_league': 1, 'is_bundesliga': 0},
            'BL1': {'is_ligue1': 0, 'is_serie_a': 0, 'is_la_liga': 0, 'is_premier_league': 0, 'is_bundesliga': 1},
        }
    }


def main():
    parser = argparse.ArgumentParser(description='T-006 低进球分类器 LightGBM → JS 导出')
    parser.add_argument('--pkl', default=str(DEFAULT_PKL),
                        help=f'pkl 文件路径 (默认: {DEFAULT_PKL})')
    parser.add_argument('--backup', action='store_true', default=True,
                        help='备份旧 JS 文件 (默认开启)')
    args = parser.parse_args()

    pkl_path = Path(args.pkl)
    if not pkl_path.exists():
        print(f'❌ pkl 文件不存在: {pkl_path}')
        sys.exit(1)

    print(f'📦 加载模型: {pkl_path}')
    with open(pkl_path, 'rb') as f:
        bundle = pickle.load(f)

    print(f'  模型类型: {type(bundle["model"]).__name__}')
    print(f'  特征维度: {len(bundle["feature_cols"])}')
    print(f'  best_threshold: {bundle["best_threshold"]:.6f}')
    print(f'  n_samples/n_lowgoal: {bundle["n_samples"]}/{bundle["n_lowgoal"]} '
          f'(base_rate={bundle["n_lowgoal"]/bundle["n_samples"]:.4f})')
    print(f'  oof_auc: {bundle.get("oof_auc", 0):.4f}')

    # 备份旧文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if args.backup:
        for fname in ['t006_lowgoal_export.js', 't006_feature_spec.js']:
            old_path = ASSETS_DIR / fname
            if old_path.exists():
                bak_path = ASSETS_DIR / f'{fname}.bak_{timestamp}'
                shutil.copy2(old_path, bak_path)
                print(f'  📋 备份: {fname} → {bak_path.name}')

    # 导出主模型
    print('\n🔄 导出 t006 模型 → t006_lowgoal_export.js ...')
    js_model = convert_t006_lgb_to_js(bundle)
    main_path = ASSETS_DIR / 't006_lowgoal_export.js'
    with open(main_path, 'w', encoding='utf-8') as f:
        f.write(f'var T006_LOWGOAL_MODEL = {json.dumps(js_model, indent=2, ensure_ascii=False)};')
    main_size = main_path.stat().st_size
    print(f'  ✅ 已保存 ({main_size:,} bytes, {len(js_model["trees"])} 棵树)')
    print(f'     base = {js_model["base"]:.6f}  (LightGBM leaf 已含 lr,base=0)')
    print(f'     lr = {js_model["lr"]}  (JS 公式退化用 1.0,真实 lr={js_model["metadata"]["real_learning_rate"]})')
    print(f'     best_threshold = {js_model["best_threshold"]}')
    print(f'     base_rate = {js_model["base_rate"]:.6f}')
    print(f'     p_zero = {js_model["metadata"]["p_zero"]:.6f}')
    print(f'     L_zero = {js_model["metadata"]["L_zero"]:.6f}')

    # 导出特征规格
    print('\n🔄 导出特征规格 → t006_feature_spec.js ...')
    spec = build_feature_spec(bundle)
    spec_path = ASSETS_DIR / 't006_feature_spec.js'
    with open(spec_path, 'w', encoding='utf-8') as f:
        f.write(f'var T006_FEATURE_SPEC = {json.dumps(spec, indent=2, ensure_ascii=False)};')
    print(f'  ✅ 已保存 ({spec_path.stat().st_size:,} bytes)')

    print(f'\n{"="*60}')
    print(f'✅ t006 导出完成!')
    print(f'   源 pkl: {pkl_path.name}')
    print(f'   树数: {len(js_model["trees"])}')
    print(f'   特征维度: {len(js_model["feature_cols"])}')
    print(f'   base: {js_model["base"]:.6f} (LightGBM leaf 已含 lr,base=0)')
    print(f'   lr: {js_model["lr"]} (JS 退化用 1.0,真实 lr={js_model["metadata"]["real_learning_rate"]})')
    print(f'   best_threshold: {js_model["best_threshold"]}')
    print(f'   base_rate: {js_model["base_rate"]:.6f}')
    print(f'{"="*60}')
    print(f'\n💡 下一步: 重启 prediction-service 让它加载 t006_lowgoal_export.js')


if __name__ == '__main__':
    main()
