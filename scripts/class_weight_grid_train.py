# -*- coding: utf-8 -*-
"""
C-20261013-072: class_weight 网格重训 → 平局判别力影子实验（训练阶段）
=====================================================================================

登记文档: docs/class_weight_draw_discrim_experiment.md
红线豁免: §15.3 红线 2 + §8.2/§13.4「重训禁平局加权」——有条件豁免
豁免范围: 仅限训练层 class_weight ∈ {1.5, 2.0, 2.5, 3.0}
豁免边界: 不含 focal loss / 不含输出层阈值调参 / 不覆盖生产 pkl
豁免收回: 实验失败或达标后自动收回

启动形态: **训练 + 影子接入，不含任何切换决策**
  - 训练窗 < 2026-08-22（C-002 冻结协议，无泄漏）
  - 校准窗 2026-03-01 ~ 2026-08-21（iso 拟合，与 drawfix C-002 一致）
  - **不读 08-22~09-21**（EVAL-peek 防护，详见登记文档 §2.3）
  - 候选 pkl 落盘 assets/shadow_class_weight/，不覆盖生产
  - 四门禁评估由 scripts/class_weight_shadow_eval.py 在 fresh 窗口 n≥300 时执行

复用协议:
  - E.build_matrix (t005_fix_experiment) —— 71 维特征矩阵
  - E.train_draw_model —— LightGBM Stage1 + ES 早停 + 全量重训
  - E.fit_calibrator('iso', ...) —— 与 drawfix C-002 逐字段一致
  - compute_sample_weights (train_hcp_model_v2) —— 样本权重（与 deploy_t005v3_final 一致）
  - STAGE1_PARAMS (deploy_t005v3_final) —— 其余超参，逐字段一致

CLI:
    python scripts/class_weight_grid_train.py
    python scripts/class_weight_grid_train.py --ratios 1.5,2.0,2.5,3.0
    python scripts/class_weight_grid_train.py --dry-run  # 只打印不落盘
"""
from __future__ import annotations

import argparse
import os
import sys
import pickle
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# C-002 冻结协议：截止线必须在任何管线 import 之前抬升
# （hcp_features / hcp_features_v2 在 import 期读取 TRAE_HCP_TRAIN_CUTOFF）
os.environ.setdefault('TRAE_HCP_TRAIN_CUTOFF', '2099-12-31 00:00:00')
# 防御性: 阻止本脚本误被生产 prediction_core 加载为「生产 draw_detector」
os.environ.setdefault('TRAE_CLASS_WEIGHT_EXPERIMENT', '1')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# 复用 t005_fix_experiment 的训练/校准/特征矩阵协议（C-002 冻结）
import t005_fix_experiment as E  # noqa: E402
from train_hcp_model_v2 import compute_sample_weights  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s][%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('cw_grid_train')

# ============================================================
# 路径
# ============================================================
ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')
SHADOW_DIR = os.path.join(ASSETS_DIR, 'shadow_class_weight')
REPORTS_DIR = os.path.join(PROJECT_DIR, 'reports')
os.makedirs(SHADOW_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# 网格（生产 baseline=2.5 与 C-069 部署一致；2.5 复训做基线 sanity check）
DEFAULT_RATIOS = [1.5, 2.0, 2.5, 3.0]
CAL_KIND = 'iso'  # 与 deploy_t005v3_final / drawfix C-002 一致


# ============================================================
# 数据加载（C-002 冻结协议照抄 deploy_t005v3_final.load_data）
# ============================================================
def load_data():
    """加载 V3 特征数据 + 时序切分（C-002 冻结协议）。

    切分协议:
      - 训练严格 < EVAL_START (2026-08-22)
      - 校准窗 VAL_START~EVAL_START (2026-03-01~08-21, n≈401)
      - **不读评估窗**（EVAL-peek 防护，详见登记文档 §2.3）

    本函数与 deploy_t005v3_final.load_data 的唯一差异:
      返回时不带 X_test/y_test/meta_test/cat_test——本实验不做评估窗选型。
    """
    logger.info("开始加载 V3 特征数据 (E.build_matrix + 时序切分, C-002 冻结)...")
    conn = sqlite3.connect(E.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        X, y3, meta, feature_cols = E.build_matrix(conn)
    finally:
        conn.close()

    dates = meta['date']
    tr_mask = dates < pd.Timestamp(E.EVAL_START)
    val_mask = (dates >= pd.Timestamp(E.VAL_START)) & (dates < pd.Timestamp(E.EVAL_START))

    X_tr, y_tr = X[tr_mask].reset_index(drop=True), y3[tr_mask].reset_index(drop=True)
    X_val, y_val = X[val_mask].reset_index(drop=True), y3[val_mask].reset_index(drop=True)

    # ES 早停窗（训练集内部 80/20，照抄 drawfix E.train_draw_model 期望）
    es = int(len(X_tr) * 0.8)
    X_train_es = X_tr.iloc[:es].reset_index(drop=True)
    y_train_es = y_tr.iloc[:es].reset_index(drop=True)
    X_val_es = X_tr.iloc[es:].reset_index(drop=True)
    y_val_es = y_tr.iloc[es:].reset_index(drop=True)

    # 样本权重（照抄 drawfix: 用 meta.label_source）
    label_sources_tr = meta.loc[tr_mask, 'label_source'].values
    sw_train = compute_sample_weights(y_tr.values, label_sources_tr)
    sw_es = compute_sample_weights(y_train_es.values, label_sources_tr[:es])

    logger.info(
        f"数据加载完成: 训练集={len(X_tr)}(<{E.EVAL_START}), "
        f"校准窗={len(X_val)}({E.VAL_START}~{E.EVAL_START}), "
        f"特征={X.shape[1]}维  (评估窗不读, EVAL-peek 防护)"
    )
    logger.info(f"训练集走水率: {(y_tr == 1).mean():.4f}  校准窗走水率: {(y_val == 1).mean():.4f}")

    return {
        'X_train': X_tr, 'y_train': y_tr,
        'X_val': X_val, 'y_val': y_val,
        'X_train_es': X_train_es, 'y_train_es': y_train_es,
        'X_val_es': X_val_es, 'y_val_es': y_val_es,
        'sw_train': sw_train, 'sw_es': sw_es,
        'feature_cols': feature_cols,
    }


# ============================================================
# 网格重训
# ============================================================
def train_one_ratio(data, ratio):
    """单 ratio 训练 Stage1 + iso 校准器，返回 (model, calibrator, sanity_metrics)。

    Sanity metrics 仅在「校准窗」计算——不是评估窗，不是选型决策。
    用途: 训练日志诊断（确认 ratio 越大 → p_draw 均值越高 的方向性正确）。
    """
    logger.info(f"  [ratio={ratio}] 训练 Stage1 走水检测器 (E.train_draw_model)...")
    draw_model = E.train_draw_model(
        data['X_train'], data['y_train'],
        data['X_train_es'], data['y_train_es'],
        data['sw_train'], data['sw_es'],
        ratio,
    )

    logger.info(f"  [ratio={ratio}] 拟合 iso 校准器 (窗={E.VAL_START}~{E.EVAL_START}, n={len(data['y_val'])})")
    p_val_raw = draw_model.predict_proba(data['X_val'])[:, 1]
    yv = data['y_val'].values
    cal = E.fit_calibrator(CAL_KIND, p_val_raw, (yv == 1).astype(int))
    p_val = E.apply_calibrator(cal, p_val_raw)

    # 校准窗 sanity 指标（非选型，仅日志诊断）
    push_actual = float((yv == 1).mean())
    p_push_mean = float(p_val.mean())
    push_recall = float(((p_val >= 0.5) & (yv == 1)).sum() / max((yv == 1).sum(), 1))
    try:
        from sklearn.metrics import roc_auc_score
        auc = float(roc_auc_score((yv == 1).astype(int), p_val))
    except Exception:
        auc = float('nan')

    sanity = {
        'ratio': ratio,
        'n_val': len(yv),
        'push_actual_rate': push_actual,
        'p_push_mean': p_push_mean,
        'p_push_bias': p_push_mean - push_actual,
        'push_recall': push_recall,
        'auc_val': auc,
    }
    logger.info(
        f"  [ratio={ratio}] 校准窗 sanity: "
        f"AUC={auc:.4f}  p_push_mean={p_push_mean:.3f} "
        f"(actual={push_actual:.3f}, bias={p_push_mean - push_actual:+.3f})  "
        f"push_recall={push_recall:.3f}"
    )
    return draw_model, cal, sanity


def save_candidate(draw_model, calibrator, ratio, feature_cols):
    """落盘候选 pkl 到 assets/shadow_class_weight/（不覆盖生产）。"""
    suffix = f'cw{ratio}'
    model_path = os.path.join(SHADOW_DIR, f't005v3_draw_detector_{suffix}.pkl')
    cal_path = os.path.join(SHADOW_DIR, f't005v3_draw_calibrator_{suffix}.pkl')
    meta_path = os.path.join(SHADOW_DIR, f't005v3_draw_meta_{suffix}.json')

    with open(model_path, 'wb') as f:
        pickle.dump(draw_model, f)
    with open(cal_path, 'wb') as f:
        pickle.dump(calibrator, f)

    # 元数据（含红线豁免标识，便于 prediction_core 防御性加载校验）
    import json
    meta = {
        'class_weight_version': f'cw{ratio}',
        'ratio': ratio,
        'experiment': 'C-20261013-072',
        'red_line_exempted': '§15.3 红线 2 + §8.2/§13.4 重训禁平局加权',
        'exemption_scope': 'training_layer_class_weight_only',
        'excludes': ['focal_loss', 'output_threshold_tuning'],
        'auto_recover_on': ['experiment_fail', 'experiment_pass', 'protocol_violation', 'shared_window_pollution'],
        'training_window': f'<{E.EVAL_START}',
        'calibration_window': f'{E.VAL_START}~{E.EVAL_START}',
        'cal_kind': CAL_KIND,
        'eval_window_policy': 'fresh_only_no_eval_peek',
        'feature_cols': feature_cols,
        'n_features': len(feature_cols),
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'production_safe': 'never_load_in_prediction_core',
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(f"  落盘: {os.path.basename(model_path)} / {os.path.basename(cal_path)} / {os.path.basename(meta_path)}")
    return model_path, cal_path, meta_path


def write_summary_report(sanity_results, paths, ratios):
    """落盘训练摘要到 reports/class_weight_grid_train_<ts>.md。"""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = os.path.join(REPORTS_DIR, f'class_weight_grid_train_{ts}.md')
    lines = [
        '# C-20261013-072 class_weight 网格重训摘要',
        '',
        f'生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
        f'实验代号: P0-DrawDiscrim-ClassWeight',
        f'登记文档: docs/class_weight_draw_discrim_experiment.md',
        '',
        '## 红线豁免状态',
        '',
        '- 豁免: §15.3 红线 2 + §8.2/§13.4 重训禁平局加权（仅训练层 class_weight）',
        '- 不含: focal loss / 输出层阈值调参（t=0.30 已证伪净收益 -1.3pp）',
        '- 自动收回: 实验失败/达标/协议违反/共窗口污染',
        '',
        '## 训练协议 (C-002 冻结)',
        '',
        f'- 训练窗: <{E.EVAL_START} (无泄漏)',
        f'- 校准窗: {E.VAL_START}~{E.EVAL_START} (iso 拟合, n≈401)',
        f'- 校准器: {CAL_KIND}',
        '- 评估窗: **不读** (EVAL-peek 防护, fresh 窗口由 shadow_eval 处理)',
        '',
        '## 候选 pkl 落盘',
        '',
        '| ratio | model | calibrator | meta |',
        '|---|---|---|---|',
    ]
    for ratio in ratios:
        p = paths.get(ratio, {})
        lines.append(
            f'| {ratio} | `{os.path.basename(p.get("model", ""))}` | '
            f'`{os.path.basename(p.get("calibrator", ""))}` | '
            f'`{os.path.basename(p.get("meta", ""))}` |'
        )

    lines += ['', '## 校准窗 sanity 指标（非选型，仅日志诊断）', '',
              '| ratio | n_val | AUC | p_push_mean | push_actual | bias | push_recall |',
              '|---|---|---|---|---|---|---|']
    for s in sanity_results:
        lines.append(
            f"| {s['ratio']} | {s['n_val']} | {s['auc_val']:.4f} | "
            f"{s['p_push_mean']:.3f} | {s['push_actual_rate']:.3f} | "
            f"{s['p_push_bias']:+.3f} | {s['push_recall']:.3f} |"
        )

    lines += [
        '',
        '## 方向性预期',
        '',
        '- ratio 越大 → Stage1 对走水样本加权越重 → p_push_mean 越高',
        '- 若方向不符合预期（ratio↑ 但 p_push_mean↓），训练层可能存在 sample_weight 主导覆盖',
        '- 真正的选型裁决在 fresh 窗口 n≥300 时由四门禁执行（见登记文档 §3）',
        '',
        '## 下一步',
        '',
        '1. 影子双算写入: scripts/class_weight_shadow_writer.py 在 generate_unified_report.py 接入',
        '2. fresh 窗口积累: 2026-10-10 起 reports/class_weight_shadow.jsonl 自然增长',
        '3. 四门禁裁决: fresh n≥300 时 scripts/class_weight_shadow_eval.py 执行',
        '',
    ]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    logger.info(f"摘要报告: {path}")
    return path


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description='C-20261013-072 class_weight 网格重训')
    parser.add_argument('--ratios', type=str, default=','.join(map(str, DEFAULT_RATIOS)),
                        help='逗号分隔的 ratio 列表，默认 1.5,2.0,2.5,3.0')
    parser.add_argument('--dry-run', action='store_true',
                        help='只训练不落盘（用于方向性预检）')
    args = parser.parse_args()

    ratios = [float(x.strip()) for x in args.ratios.split(',') if x.strip()]
    logger.info(f"=== C-20261013-072 class_weight 网格重训启动 ===")
    logger.info(f"ratios={ratios}  dry_run={args.dry_run}")
    logger.info(f"红线豁免: §15.3 红线 2 + §8.2/§13.4 (仅训练层, 不含 focal/threshold)")
    logger.info(f"启动形态: 训练 + 影子接入, 不含切换决策")

    # 红线守门: 防御性检查 config.yaml 必须已开启豁免
    try:
        import yaml
        cfg_path = os.path.join(PROJECT_DIR, 'config.yaml')
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        cw_exp = cfg.get('class_weight_experiment', {})
        if not cw_exp.get('active', False):
            logger.error("config.yaml > class_weight_experiment.active = false")
            logger.error("红线豁免未启用，拒绝训练。请先在 config.yaml 显式开启豁免。")
            logger.error("登记文档: docs/class_weight_draw_discrim_experiment.md §1")
            sys.exit(2)
        exempted_from = cw_exp.get('exempted_from', [])
        if 'red_line_2_draw_weight_retrain_ban' not in exempted_from:
            logger.error(f"config.yaml > class_weight_experiment.exempted_from 缺少 "
                         f"'red_line_2_draw_weight_retrain_ban'")
            sys.exit(2)
        logger.info(f"红线豁免已显式开启: exempted_from={exempted_from}")
        logger.info(f"豁免范围: {cw_exp.get('exemption_scope', '<未指定>')}")
        logger.info(f"自动收回条件: {cw_exp.get('auto_recover_on', [])}")
    except Exception as e:
        logger.error(f"读取 config.yaml 红线豁免段失败: {e}")
        logger.error("拒绝训练——红线豁免必须显式拍板（见登记文档 §1）")
        sys.exit(2)

    # 数据加载
    data = load_data()

    # 网格重训
    sanity_results = []
    paths = {}
    for ratio in ratios:
        logger.info(f"--- ratio={ratio} ---")
        draw_model, cal, sanity = train_one_ratio(data, ratio)
        sanity_results.append(sanity)
        if not args.dry_run:
            mp, cp, mtp = save_candidate(draw_model, cal, ratio, data['feature_cols'])
            paths[ratio] = {'model': mp, 'calibrator': cp, 'meta': mtp}
        else:
            logger.info(f"  [ratio={ratio}] dry-run, 不落盘")

    # 方向性 sanity check
    logger.info("=== 方向性 sanity check ===")
    sorted_s = sorted(sanity_results, key=lambda x: x['ratio'])
    for i in range(1, len(sorted_s)):
        prev, curr = sorted_s[i - 1], sorted_s[i]
        delta = curr['p_push_mean'] - prev['p_push_mean']
        direction = '↑' if delta > 0 else '↓'
        logger.info(f"  ratio {prev['ratio']}→{curr['ratio']}: "
                    f"p_push_mean {prev['p_push_mean']:.3f}→{curr['p_push_mean']:.3f} "
                    f"({direction}{delta:+.3f})")

    # 摘要报告
    if not args.dry_run:
        write_summary_report(sanity_results, paths, ratios)

    logger.info("=== 训练阶段完成 ===")
    logger.info("下一步: 接入影子双算 (scripts/class_weight_shadow_writer.py)")
    logger.info("裁决: fresh n≥300 时四门禁 (scripts/class_weight_shadow_eval.py)")


if __name__ == '__main__':
    main()
