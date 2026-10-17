# -*- coding: utf-8 -*-
"""
C-20261013-072: class_weight 平局判别力 Shadow 双算写入器
=====================================================================================

登记文档: docs/class_weight_draw_discrim_experiment.md
红线豁免: §15.3 红线 2 + §8.2/§13.4「重训禁平局加权」——有条件豁免

形态: **影子双算, 不影响生产预测/落库/展示**
  - 在 prediction_core 调用生产 draw_detector 后, 用同一份 71 维特征 X
    调 4 个候选 pkl (cw1.5/cw2.0/cw2.5/cw3.0) + iso 校准器
  - Stage2 方向预测器**复用生产** t005v3_direction_predictor.pkl (隔离变量, §1.2)
  - 写 reports/class_weight_shadow.jsonl, 按 match_id 去重覆盖
  - 赛后由 scripts/class_weight_shadow_eval.py JOIN post_match_review 做四门禁裁决

开关优先级:
  环境变量 TRAE_CLASS_WEIGHT_SHADOW > config.yaml > class_weight_experiment.shadow_enabled
  默认 false——只有显式开启才激活, 保证 production-safe

API:
  - is_shadow_enabled() -> bool
  - load_shadow_models() -> dict (4 个候选 pkl + calibrator, 懒加载 + 模块级缓存)
  - compute_shadow_predictions(X_71dim) -> dict (per ratio: p_draw_marginal + argmax + proba_3way)
  - append_class_weight_shadow(record) -> None (jsonl 追加, match_id 去重)
  - build_and_append_shadow(match_meta, X_71dim, control_record) -> None (一体化便捷入口)
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_DIR / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

ASSETS_DIR = PROJECT_DIR / "assets"
SHADOW_DIR = ASSETS_DIR / "shadow_class_weight"
REPORT_DIR = PROJECT_DIR / "reports"
JSONL_PATH = REPORT_DIR / "class_weight_shadow.jsonl"

# 候选 ratio 列表（与 class_weight_grid_train.py DEFAULT_RATIOS 一致）
SHADOW_RATIOS = [1.5, 2.0, 2.5, 3.0]

# 模块级缓存（懒加载, 首次调用后复用）
_SHADOW_CACHE: Optional[Dict[str, Any]] = None


# ============================================================
# 开关
# ============================================================
def is_shadow_enabled() -> bool:
    """检查影子双算是否启用。

    优先级: 环境变量 TRAE_CLASS_WEIGHT_SHADOW > config.yaml
    默认 false（production-safe, 显式开启才激活）。
    """
    env = os.environ.get('TRAE_CLASS_WEIGHT_SHADOW', '').lower()
    if env in ('1', 'true', 'yes', 'on'):
        return True
    if env in ('0', 'false', 'no', 'off'):
        return False
    # 环境变量未设置, 回退 config.yaml
    try:
        import yaml
        cfg_path = PROJECT_DIR / "config.yaml"
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        return bool(cfg.get('class_weight_experiment', {}).get('shadow_enabled', False))
    except Exception:
        return False


# ============================================================
# 模型加载（懒加载 + 模块级缓存）
# ============================================================
def load_shadow_models() -> Dict[str, Any]:
    """加载 4 个候选 draw_detector pkl + iso calibrator + 生产方向预测器。

    懒加载: 首次调用时加载, 之后复用模块级缓存。
    若任一候选 pkl 缺失, 返回 {'error': ...} 由调用方处理。
    """
    global _SHADOW_CACHE
    if _SHADOW_CACHE is not None:
        return _SHADOW_CACHE

    cache: Dict[str, Any] = {'models': {}, 'dir_model': None, 'errors': []}

    # 4 个候选 draw_detector + calibrator
    for ratio in SHADOW_RATIOS:
        suffix = f'cw{ratio}'
        model_path = SHADOW_DIR / f't005v3_draw_detector_{suffix}.pkl'
        cal_path = SHADOW_DIR / f't005v3_draw_calibrator_{suffix}.pkl'
        if not model_path.exists():
            cache['errors'].append(f'missing: {model_path.name} (run class_weight_grid_train.py first)')
            continue
        try:
            with open(model_path, 'rb') as f:
                cache['models'][ratio] = pickle.load(f)
            if cal_path.exists():
                with open(cal_path, 'rb') as f:
                    cache['models'][f'{ratio}_cal'] = pickle.load(f)
            else:
                cache['models'][f'{ratio}_cal'] = None
        except Exception as e:
            cache['errors'].append(f'load_fail {suffix}: {type(e).__name__}: {e}')

    # 生产方向预测器（Stage2 复用, 隔离变量）
    dir_path = ASSETS_DIR / 't005v3_direction_predictor.pkl'
    if dir_path.exists():
        try:
            with open(dir_path, 'rb') as f:
                cache['dir_model'] = pickle.load(f)
        except Exception as e:
            cache['errors'].append(f'dir_model load_fail: {type(e).__name__}: {e}')

    _SHADOW_CACHE = cache
    return cache


def clear_shadow_cache() -> None:
    """清理模块级缓存（测试用）。"""
    global _SHADOW_CACHE
    _SHADOW_CACHE = None


# ============================================================
# 影子预测
# ============================================================
def _apply_calibrator(cal, p_raw: np.ndarray) -> np.ndarray:
    """应用 iso/platt 校准器（与 t005_fix_experiment.apply_calibrator 一致）。"""
    if cal is None:
        return p_raw
    kind, obj = cal
    if kind == 'platt':
        z = np.log(np.clip(p_raw, 1e-6, 1 - 1e-6)).reshape(-1, 1)
        return obj.predict_proba(z)[:, 1]
    if kind == 'iso':
        return np.clip(obj.predict(p_raw), 0.0, 1.0)
    return p_raw


def _fuse_3way(p_draw: np.ndarray, p_away_nd: np.ndarray) -> np.ndarray:
    """三向融合: p_home = (1-p_draw)*(1-p_away_nd), p_away = (1-p_draw)*p_away_nd。

    与 t005_fix_experiment.fuse(floor=0.0) 一致（影子不施加硬下限）。
    返回 shape=(N, 3) 的 [p_home, p_draw, p_away]。
    """
    p_non_draw = 1.0 - p_draw
    p_home = p_non_draw * (1.0 - p_away_nd)
    p_away = p_non_draw * p_away_nd
    total = p_home + p_draw + p_away
    # 归一化（防数值漂移）
    out = np.stack([p_home, p_draw, p_away], axis=1)
    out = out / np.maximum(total.reshape(-1, 1), 1e-12)
    return out


def compute_shadow_predictions(X) -> Dict[str, Any]:
    """对单场或批量特征 X 计算 4 个候选 ratio 的影子预测。

    Args:
        X: pd.DataFrame 或 np.ndarray, 71 维（与生产 draw_detector 同口径）

    Returns:
        {
          'ratio_1.5': {'p_draw_marginal': float|list, 'argmax_class': int|list, 'proba_3way': [h,d,a]},
          ...
          'error': str|None
        }

    p_draw_marginal = iso 校准后的走水概率（与生产口径一致）
    argmax_class    = 0 上 / 1 走 / 2 下
    proba_3way      = [p_home, p_draw, p_away] 归一化后
    """
    cache = load_shadow_models()
    if cache.get('errors'):
        return {'error': '; '.join(cache['errors'][:3])}
    if not cache.get('models'):
        return {'error': 'no candidate models loaded (run class_weight_grid_train.py first)'}

    dir_model = cache.get('dir_model')
    if dir_model is None:
        return {'error': 'production direction_predictor missing (cannot fuse 3way)'}

    # 方向预测器调用一次（4 个候选共享, 隔离 Stage2 变量）
    try:
        p_away_nd = dir_model.predict_proba(X)[:, 1]
    except Exception as e:
        return {'error': f'dir_model predict fail: {type(e).__name__}: {e}'}

    out: Dict[str, Any] = {}
    for ratio in SHADOW_RATIOS:
        key = f'ratio_{ratio}'
        model = cache['models'].get(ratio)
        cal = cache['models'].get(f'{ratio}_cal')
        if model is None:
            out[key] = {'error': f'model cw{ratio} not loaded'}
            continue
        try:
            p_raw = model.predict_proba(X)[:, 1]
            p_draw = _apply_calibrator(cal, p_raw)
            proba_3way = _fuse_3way(p_draw, p_away_nd)
            argmax_cls = proba_3way.argmax(axis=1)

            # 单场 → 标量; 批量 → list
            if hasattr(X, '__len__') and len(X) == 1 if not hasattr(X, 'shape') else X.shape[0] == 1:
                out[key] = {
                    'p_draw_marginal': float(p_draw[0]),
                    'argmax_class': int(argmax_cls[0]),
                    'proba_3way': [float(proba_3way[0, 0]), float(proba_3way[0, 1]), float(proba_3way[0, 2])],
                }
            else:
                out[key] = {
                    'p_draw_marginal': [float(x) for x in p_draw],
                    'argmax_class': [int(x) for x in argmax_cls],
                    'proba_3way': [[float(proba_3way[i, 0]), float(proba_3way[i, 1]), float(proba_3way[i, 2])]
                                   for i in range(len(argmax_cls))],
                }
        except Exception as e:
            out[key] = {'error': f'{type(e).__name__}: {e}'}

    return out


# ============================================================
# JSONL 写入（match_id 去重覆盖, 与 append_tg_calib_shadow 同口径）
# ============================================================
def append_class_weight_shadow(record: dict) -> None:
    """追加 class_weight Shadow 记录到 reports/class_weight_shadow.jsonl。

    按 match_id 去重覆盖（与 append_tg_calib_shadow / append_tg_wodds_shadow 一致）。
    赛前生成、不含赛果；赛后由 class_weight_shadow_eval.py 按 match_id JOIN
    post_match_review.actual_handicap 做四门禁评测。
    """
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if JSONL_PATH.exists():
        for ln in JSONL_PATH.read_text(encoding='utf-8').splitlines():
            try:
                if json.loads(ln).get('match_id') != record['match_id']:
                    kept.append(ln)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps(record, ensure_ascii=False))
    JSONL_PATH.write_text('\n'.join(kept) + '\n', encoding='utf-8')


# ============================================================
# 一体化便捷入口
# ============================================================
def build_and_append_shadow(match_meta: dict, X, control_record: Optional[dict] = None) -> Optional[dict]:
    """一体化入口: 计算 4 个候选影子预测 + 写 jsonl。

    Args:
        match_meta: {match_id, gen_time, match_date, league, home, away}
        X: 71 维特征 (pd.DataFrame 单行 / np.ndarray (1,71) / list)
        control_record: 生产对照 (含 class_weight_version='baseline_2.5',
                        p_draw_marginal, argmax_class, proba_3way)。
                        若 None, control 字段写 null（评测时跳过门禁④）。

    Returns:
        写入的 record dict（用于调用方日志/调试）, 失败返回 None。
    """
    if not is_shadow_enabled():
        return None

    try:
        shadow = compute_shadow_predictions(X)
    except Exception as e:
        shadow = {'error': f'{type(e).__name__}: {e}'}

    record = {
        'match_id': match_meta.get('match_id'),
        'gen_time': match_meta.get('gen_time'),
        'match_date': match_meta.get('match_date'),
        'league': match_meta.get('league'),
        'home': match_meta.get('home'),
        'away': match_meta.get('away'),
        'control': control_record or {'class_weight_version': 'baseline_2.5',
                                       'p_draw_marginal': None,
                                       'argmax_class': None,
                                       'proba_3way': None},
        'shadow': shadow,
        'join_key': match_meta.get('match_id'),
    }
    append_class_weight_shadow(record)
    return record


# ============================================================
# CLI（手动回填单场 / 诊断用）
# ============================================================
def _cli():
    import argparse
    p = argparse.ArgumentParser(description='C-20261013-072 class_weight shadow writer (诊断/手动回填)')
    p.add_argument('--check', action='store_true', help='检查候选 pkl 加载状态')
    p.add_argument('--enabled', action='store_true', help='打印 is_shadow_enabled() 状态')
    args = p.parse_args()

    if args.enabled:
        print(f'is_shadow_enabled: {is_shadow_enabled()}')
        print(f'jsonl_path: {JSONL_PATH}')
        print(f'shadow_dir: {SHADOW_DIR}')
        return

    if args.check:
        cache = load_shadow_models()
        # 逐 ratio 输出（key 是 float ratio + '{ratio}_cal' str 混合，不能 sorted）
        for ratio in SHADOW_RATIOS:
            m = cache['models'].get(ratio)
            cal = cache['models'].get(f'{ratio}_cal')
            status = 'OK' if m is not None else 'MISSING'
            cal_status = 'OK' if cal is not None else ('MISSING' if not cal else 'None(no calibrator)')
            print(f'  cw{ratio}: detector={status}  calibrator={cal_status}')
        print(f'dir_model: {"OK" if cache.get("dir_model") is not None else "MISSING"}')
        if cache.get('errors'):
            print('errors:')
            for e in cache['errors']:
                print(f'  - {e}')
        return

    # 默认打印状态
    print(f'is_shadow_enabled: {is_shadow_enabled()}')
    print(f'jsonl_path: {JSONL_PATH}  exists: {JSONL_PATH.exists()}')
    print(f'shadow_dir: {SHADOW_DIR}  exists: {SHADOW_DIR.exists()}')


if __name__ == '__main__':
    _cli()
