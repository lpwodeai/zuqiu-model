# -*- coding: utf-8 -*-
"""
平局加权回灌 LightGBM — 对照实验
================================
把 NN 实验中唯一正收益的「平局加权」以 class_weight 形式回灌到 LightGBM，
验证是否能在不引入 PyTorch 的前提下，提升平局召回率并尽量守住整体准确率。

对照维度（在同一 5 折 TimeSeriesSplit 上）:
  - baseline   : class_weight='balanced'（现有生产意图）
  - draw_boost : 反频率权重归一 + 平局(class=1) 乘以 boost，boost ∈ {1.2, 1.5, 2.0}

说明:
  - 纯对照实验脚本，不修改/不写回任何现有模型文件或主链。
  - 数据与 train_models.py 同源；切分一致 (sklearn TimeSeriesSplit)。
  - 联赛归一化已含 26/27 新赛季变体 (feature_utils.competition_map 已修复)。

运行:
  python scripts/draw_weight_lgbm_experiment.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))

import lightgbm as lgb
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit

from feature_utils import load_match_data_odds, build_all_features, load_config

SEED = 42
np.random.seed(SEED)

# 对照的平局权重倍数: None = baseline (balanced)
DRAW_BOOSTS = [None, 1.2, 1.5, 2.0]


# ============================================================
# 指标
# ============================================================

def compute_ece(y_true, probs, n_bins=10):
    y_true = np.asarray(y_true)
    probs = np.asarray(probs)
    conf = probs.max(axis=1)
    pred = probs.argmax(axis=1)
    acc = (pred == y_true).astype(np.float64)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (conf > edges[i]) & (conf <= edges[i + 1])
        if mask.sum() == 0:
            continue
        ece += (mask.sum() / len(y_true)) * abs(acc[mask].mean() - conf[mask].mean())
    return float(ece)


def compute_brier(y_true, probs):
    y_true = np.asarray(y_true)
    probs = np.asarray(probs)
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(y_true)), y_true] = 1.0
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def draw_recall(y_true, y_pred):
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    mask = y_true == 1
    if mask.sum() == 0:
        return float("nan")
    return float((y_pred[mask] == 1).mean())


def compute_metrics(y_true, probs):
    y_pred = probs.argmax(axis=1)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "log_loss": float(log_loss(y_true, probs, labels=[0, 1, 2])),
        "brier": compute_brier(y_true, probs),
        "ece": compute_ece(y_true, probs),
        "draw_recall": draw_recall(y_true, y_pred),
    }


def mean_metrics(list_of_dicts):
    keys = list_of_dicts[0].keys()
    return {k: float(np.nanmean([d[k] for d in list_of_dicts])) for k in keys}


# ============================================================
# 类别权重
# ============================================================

def class_weight_dict(y, draw_boost=1.0):
    """反频率权重归一 + 平局(class=1)上浮。返回 dict 供 LGBMClassifier 使用。"""
    counts = np.bincount(y, minlength=3).astype(np.float64)
    counts = np.where(counts == 0, 1, counts)
    w = counts.sum() / (3.0 * counts)
    w = w / w.mean()
    w[1] *= draw_boost
    return {0: w[0], 1: w[1], 2: w[2]}


# ============================================================
# LightGBM 单折
# ============================================================

def run_lgbm_fold(X_train, y_train, X_val, y_val, draw_boost=None):
    cfg = load_config().get("model", {}).get("lightgbm", {})
    params = dict(
        objective="multiclass", num_class=3,
        max_depth=cfg.get("max_depth", 3),
        learning_rate=cfg.get("learning_rate", 0.010611228531870624),
        num_leaves=cfg.get("num_leaves", 202),
        subsample=cfg.get("subsample", 0.9977564395215165),
        colsample_bytree=cfg.get("colsample_bytree", 0.7867816140734276),
        reg_alpha=cfg.get("reg_alpha", 0.15266573596777044),
        reg_lambda=cfg.get("reg_lambda", 1.4618378148431221),
        min_child_weight=cfg.get("min_child_weight", 6),
        min_data_in_leaf=cfg.get("min_data_in_leaf", 23),
        feature_fraction=cfg.get("feature_fraction", 0.9288484948848426),
        bagging_fraction=cfg.get("bagging_fraction", 0.6075695015110804),
        bagging_freq=cfg.get("bagging_freq", 8),
        verbose=-1, n_jobs=-1, seed=SEED,
    )
    n_est = int(cfg.get("num_boost_round", 164))
    cw = "balanced" if draw_boost is None else class_weight_dict(y_train, draw_boost)

    model = lgb.LGBMClassifier(
        **params, n_estimators=n_est, class_weight=cw, random_state=SEED,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)], eval_metric="multi_logloss",
        callbacks=[lgb.early_stopping(15, verbose=False)],
    )
    probs = model.predict_proba(X_val)
    return compute_metrics(y_val, probs)


# ============================================================
# 主流程
# ============================================================

def main():
    t0 = time.time()
    print("加载比赛数据 + 构建特征 (同 train_models.py)...")
    df = load_match_data_odds()
    X, y = build_all_features(df, include_odds=True)
    X = X.fillna(0.0)
    y = y.astype(int).values
    print(f"  X shape={X.shape}, y 分布={dict(zip(*np.unique(y, return_counts=True)))}")

    n_splits = 5
    tscv = TimeSeriesSplit(n_splits=n_splits)

    # results[boost_key] = [fold_metrics...]
    results = {}
    for fold, (tr_idx, va_idx) in enumerate(tscv.split(X)):
        X_tr, X_va = X.iloc[tr_idx].values, X.iloc[va_idx].values
        y_tr, y_va = y[tr_idx], y[va_idx]
        print(f"\n--- 第 {fold + 1}/{n_splits} 折: train={len(tr_idx)}, val={len(va_idx)} ---")
        for db in DRAW_BOOSTS:
            key = "baseline" if db is None else f"draw_boost_{db}"
            m = run_lgbm_fold(X_tr, y_tr, X_va, y_va, draw_boost=db)
            results.setdefault(key, []).append(m)
            print(f"  [{key:<14}] acc={m['accuracy']:.4f} ll={m['log_loss']:.4f} "
                  f"brier={m['brier']:.4f} ece={m['ece']:.4f} draw_r={m['draw_recall']:.4f}")

    summary = {k: mean_metrics(v) for k, v in results.items()}

    out = {
        "experiment": "draw_weight_lgbm_experiment",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_samples": len(y),
        "n_features": X.shape[1],
        "n_splits": n_splits,
        "draw_boosts": DRAW_BOOSTS,
        "folds": results,
        "summary": summary,
    }
    out_dir = BASE_DIR / "assets"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"draw_weight_lgbm_experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("平局加权回灌 LightGBM 对照结果 (各折均值)")
    print("-" * 70)
    keys = list(summary.keys())
    header = f"{'指标':<14}" + "".join(f"{k:>16}" for k in keys)
    print(header)
    for k, label in [("accuracy", "准确率"), ("log_loss", "LogLoss"),
                     ("brier", "Brier"), ("ece", "ECE"), ("draw_recall", "平局召回率")]:
        row = f"{label:<14}" + "".join(f"{summary[kk][k]:>16.4f}" for kk in keys)
        print(row)
    print("-" * 70)

    base = summary.get("baseline", {})
    if base:
        print("\n相对 baseline 增量 (Δ = 该配置 - baseline):")
        for label, k in [("准确率", "accuracy"), ("LogLoss", "log_loss"),
                         ("平局召回率", "draw_recall")]:
            parts = [f"{kk}={summary[kk][k] - base[k]:+.4f}" for kk in keys if kk != "baseline"]
            print(f"  {label:<12} " + "  ".join(parts))
        print("-" * 70)

    print(f"用时 {time.time() - t0:.1f}s")
    print(f"结果已保存: {out_path}")


if __name__ == "__main__":
    main()