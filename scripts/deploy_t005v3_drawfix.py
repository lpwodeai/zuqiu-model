# -*- coding: utf-8 -*-
"""
T-005 病因1（走水低估）上线部署 — B_r2.5_iso（C-20261006-001）
=====================================================================================
训练/校准协议与 t005_fix_experiment.py **完全一致**（"验证过的配置即上线的配置"）：
  - TRAE_HCP_TRAIN_CUTOFF=2099 构建 71 维矩阵；训练严格 <2026-08-22（n=12440，零泄漏）
  - Stage1 走水模型 class_weight ratio=2.5，超参沿用 deploy_t005v3_final.STAGE1_PARAMS
    （ES 早停定轮数后全量重训，同实验 train_draw_model）
  - 等温回归校准器在 2026-03-01~08-21 同赛季验证窗（n=401）拟合并冻结
  - 方向 pkl 不动；8% 硬下限的移除在 prediction_core（校准后不再需要）

产物：
  - 覆盖 assets/t005v3_draw_detector.pkl（0920 旧文件已备份 assets/backup_20261006/）
  - 新增 assets/t005v3_draw_calibrator.pkl = {'kind':'iso','model':IsotonicRegression,...}
  - 更新 assets/t005v3_metadata.json（已备份）
  - reports/t005v3_drawfix_deploy_<ts>.json 部署记录

运行：python deploy_t005v3_drawfix.py
"""
import os
import sys
import json
import pickle
import sqlite3
from datetime import datetime

os.environ['TRAE_HCP_TRAIN_CUTOFF'] = '2099-12-31 00:00:00'

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

import numpy as np
import pandas as pd

import t005_fix_experiment as E

RATIO = 2.5
CAL_KIND = 'iso'


def main():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    print('=' * 80)
    print(f'T-005 走水修复部署 B_r{RATIO}_{CAL_KIND}（C-20261006-001）')
    print('=' * 80)

    conn = sqlite3.connect(E.DB_PATH)
    conn.row_factory = sqlite3.Row
    X, y3, meta, feature_cols = E.build_matrix(conn)

    dates = meta['date']
    tr_mask = dates < pd.Timestamp(E.EVAL_START)
    val_mask = (dates >= pd.Timestamp(E.VAL_START)) & (dates < pd.Timestamp(E.EVAL_START))
    X_tr, y_tr = X[tr_mask], y3[tr_mask]
    X_val, y_val = X[val_mask], y3[val_mask]
    from train_hcp_model_v2 import compute_sample_weights
    sw_tr = compute_sample_weights(y_tr.values, meta.loc[tr_mask, 'label_source'].values)
    es = int(len(X_tr) * 0.8)
    X_es, y_es, sw_es = X_tr.iloc[:es], y_tr.iloc[:es], sw_tr[:es]
    print(f'训练 n={len(X_tr)}（<{E.EVAL_START}）  验证 n={len(X_val)}（{E.VAL_START}~）')

    # ---- Stage1 重训（与实验同函数） ----
    dm = E.train_draw_model(X_tr, y_tr, X_es, y_es, sw_tr, sw_es, RATIO)

    # ---- 校准器冻结（与实验同函数） ----
    with open(os.path.join(E.ASSETS_DIR, 't005v3_direction_predictor.pkl'), 'rb') as f:
        prod_dir = pickle.load(f)
    p_val_raw = dm.predict_proba(X_val)[:, 1]
    yv = y_val.values
    cal = E.fit_calibrator(CAL_KIND, p_val_raw, (yv == 1).astype(int))
    p_val = E.apply_calibrator(cal, p_val_raw)
    val_dir = prod_dir.predict_proba(X_val)[:, 1]
    mv = E.metrics(yv, E.fuse(p_val, val_dir),
                   np.tile([1 / 3, 1 / 3, 1 / 3], (len(yv), 1)))
    print(f'验证窗冻结指标: RPS={mv["rps"]:.4f} 走水均值={mv["p_push_mean"]:.3f} '
          f'(实际{mv["push_actual_rate"]:.3f}) 走水召回={mv["push_recall"]:.3f}')
    assert abs(mv['p_push_mean'] - mv['push_actual_rate']) <= 0.02, '走水率偏差超出上线约束 2pp'

    # ---- 落盘 ----
    draw_path = os.path.join(E.ASSETS_DIR, 't005v3_draw_detector.pkl')
    cal_path = os.path.join(E.ASSETS_DIR, 't005v3_draw_calibrator.pkl')
    with open(draw_path, 'wb') as f:
        pickle.dump(dm, f)
    cal_blob = {
        'kind': 'iso',
        'model': cal[1],
        'version': ts,
        'change_id': 'C-20261006-001',
        'fitted_window': [E.VAL_START, E.EVAL_START],
        'n_val': int(len(yv)),
        'stage1_ratio': RATIO,
        'note': 'Stage1 predict_proba[:,1] 后、三向融合前施加；施后不再用 8% 硬下限',
    }
    with open(cal_path, 'wb') as f:
        pickle.dump(cal_blob, f)
    print(f'已写入: {os.path.basename(draw_path)} / {os.path.basename(cal_path)}')

    # ---- metadata ----
    mp = os.path.join(E.ASSETS_DIR, 't005v3_metadata.json')
    with open(mp, 'r', encoding='utf-8') as f:
        md = json.load(f)
    md['version'] = ts
    md['created_at'] = datetime.now().isoformat()
    md['calibration'] = {'temperature': 1.0, 'threshold': 0.5, 'class_weight_ratio': RATIO}
    md['draw_calibration'] = {
        'enabled': True, 'type': 'isotonic',
        'asset': 't005v3_draw_calibrator.pkl',
        'fitted_window': f'{E.VAL_START}~{E.EVAL_START}', 'n_val': int(len(yv)),
        'applied_at': 'after Stage1 predict_proba, before fuse',
        'legacy_draw_prob_floor': 'disabled when calibrator loaded (was 0.08)',
        'change_id': 'C-20261006-001',
    }
    md['training'] = {
        'train_max_date_exclusive': E.EVAL_START, 'n_train': int(len(X_tr)),
        'feature_env': 'TRAE_HCP_TRAIN_CUTOFF=2099-12-31',
        'n_features': len(feature_cols),
        'shadow_report': 'reports/t005_fix_experiment_20261006_100850.md',
    }
    with open(mp, 'w', encoding='utf-8') as f:
        json.dump(md, f, ensure_ascii=False, indent=2)
    print(f'已更新: t005v3_metadata.json version={ts}')

    deploy_rec = {
        'timestamp': ts, 'change_id': 'C-20261006-001',
        'deployment_type': 'production_drawfix',
        'stage1_ratio': RATIO, 'calibration': CAL_KIND,
        'n_train': int(len(X_tr)), 'n_val': int(len(yv)),
        'val_metrics': {k: (None if isinstance(v, float) and np.isnan(v) else v)
                        for k, v in mv.items() if isinstance(v, (int, float, np.floating))},
        'assets': ['t005v3_draw_detector.pkl', 't005v3_draw_calibrator.pkl'],
        'backup_dir': 'assets/backup_20261006/',
        'direction_predictor': 'unchanged (20260920_011218)',
    }
    rep_path = os.path.join(E.REPORTS_DIR, f't005v3_drawfix_deploy_{ts}.json')
    with open(rep_path, 'w', encoding='utf-8') as f:
        json.dump(deploy_rec, f, ensure_ascii=False, indent=2)
    conn.close()
    print(f'部署记录: {rep_path}')


if __name__ == '__main__':
    main()
