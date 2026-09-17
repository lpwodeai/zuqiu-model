# -*- coding: utf-8 -*-
"""
ab_test_l3_weights.py — B3 L3 样本权重 A/B 门禁（C-20260908-010）
=================================================================
比较 baseline（异常样本加权） vs +L3 加权（法甲『模型局限性归因』×1.2），
严格 5 折 TimeSeriesSplit（无 shuffle），XGBoost 单模型，四指标门禁：
  RPS 不劣化 / LogLoss 不劣化 / Acc 稳定 / DrawRecall 不降
数据/特征与生产链路同源（load_match_data_odds + build_all_features ts_odds/consensus_odds）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

import train_models as tm  # noqa: E402
from feature_utils import build_all_features, load_match_data_odds  # noqa: E402
from train_models import apply_l3_sample_weights, compute_rps, create_sample_weights  # noqa: E402


def run_arm(X, y, weights, tag: str) -> dict:
    """5 折严格时序训练 + 验证集预测池化，输出四指标。"""
    tscv = TimeSeriesSplit(n_splits=5)
    preds, trues = [], []
    for fold, (tr, va) in enumerate(tscv.split(X)):
        Xtr, Xva = X.iloc[tr], X.iloc[va]
        ytr, yva = y.iloc[tr], y.iloc[va]
        scaler = StandardScaler()
        Xtr_s = scaler.fit_transform(Xtr)
        Xva_s = scaler.transform(Xva)
        fw = weights[tr]
        model, _ = tm.train_xgboost(Xtr_s, ytr.values, Xva_s, yva.values, sample_weights=fw)
        if model is None:
            continue
        preds.append(model.predict(xgb.DMatrix(Xva_s)))
        trues.append(yva.values)
    P = np.concatenate(preds)
    Y = np.concatenate(trues)
    rps = compute_rps(Y, P, tag)
    ll = float(log_loss(Y, P))
    pred = P.argmax(1)
    acc = float(accuracy_score(Y, pred))
    draw_tp = int(((pred == 1) & (Y == 1)).sum())
    draw_fn = int(((pred != 1) & (Y == 1)).sum())
    draw_recall = draw_tp / (draw_tp + draw_fn) if (draw_tp + draw_fn) else float("nan")
    print(f"[{tag}] RPS={rps:.4f} LogLoss={ll:.4f} Acc={acc:.4f} DrawRecall={draw_recall:.4f} (n={len(Y)})")
    return {"rps": rps, "logloss": ll, "acc": acc, "draw_recall": draw_recall, "n": len(Y)}


def main() -> None:
    print("加载数据 + 构建特征（生产同源）...")
    df = load_match_data_odds()
    print(f"  df 行数: {len(df)}, 日期 {df['date'].min()} ~ {df['date'].max()}")
    X, y = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
    print(f"  特征维度: {X.shape[1]}")

    print("\n构建基线权重（异常样本加权）...")
    base = create_sample_weights(df)
    print("\n构建 +L3 权重（知识库 active 条目）...")
    l3 = apply_l3_sample_weights(df, base)

    print("\n=== A/B: baseline vs +L3 加权（XGBoost, 5 折严格时序）===")
    r_base = run_arm(X, y, base, "baseline")
    r_l3 = run_arm(X, y, l3, "l3_weighted")

    gate = {
        "rps": "PASS" if r_l3["rps"] <= r_base["rps"] + 1e-6 else "FAIL",
        "logloss": "PASS" if r_l3["logloss"] <= r_base["logloss"] + 1e-6 else "FAIL",
        "acc": "PASS" if r_l3["acc"] >= r_base["acc"] - 1e-4 else "FAIL",
        "draw_recall": "PASS" if r_l3["draw_recall"] >= r_base["draw_recall"] - 1e-6 else "FAIL",
    }
    print("\n=== 门禁结果 ===")
    for k in ("rps", "logloss", "acc", "draw_recall"):
        d = r_l3[k] - r_base[k]
        print(f"  {k:>11}: {r_base[k]:.4f} → {r_l3[k]:.4f} (Δ{d:+.4f}) {gate[k]}")
    print("\n门禁结论:", "全部 PASS（可投产）" if all(v == "PASS" for v in gate.values())
          else "存在 FAIL（不投产）")


if __name__ == "__main__":
    main()
