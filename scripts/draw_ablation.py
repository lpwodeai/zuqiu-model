# -*- coding: utf-8 -*-
"""draw_ablation.py — 平局根因消融诊断（4 组对照，C-20261004-003）

===============================================
依据《对标世界级精算模型差距分析 v1.0》§2.2「平局高估根因假设链」：
  平局 z=-2.86 系统性高估的三条假设——
    H1 异常样本加权（3.0/4.0/5.0）间接在做「平局加权」（平局错误场占比极高）；
    H2 竞彩高抽水制造「假价值投注」→ 平局隐含概率偏高 → 特征/λ 锚污染；
    H3 校准跨赛季漂移（Platt 全量历史拟合）。

4 组对照（不动架构、不违反 §15.3 红线）：
  ① 基线          = np.maximum(异常加权, L3, B4)      ——生产现行
  ② 去异常加权     = np.maximum(L3, B4)                ——隔离 H1
  ③ 去L3/B4加权    = 异常加权 only                     ——隔离 L3/B4
  ④ 分联赛逆赛季校准 = ①的权重 + 每折内按联赛拟合 Platt  ——隔离 H3

对比指标（按文档要求，不看 acc，避免 argmax 诱导反平局）：
  - 总 RPS（全三分类概率）
  - ECE_draw（逐类 ECE 的平局桶，越低越好）
  - 平局系统性偏差 gap = pred_draw_mean − actual_draw_rate（>0 即高估）

复用 ab_test_l3_weights.py 收敛的结构：5 折 TimeSeriesSplit（无 shuffle）+ XGBoost
单模型 + 生产同源数据/特征（load_match_data_odds + build_all_features）。

用法：
  python scripts/draw_ablation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

import train_models as tm  # noqa: E402
from feature_utils import build_all_features, load_match_data_odds  # noqa: E402
from train_models import (  # noqa: E402
    apply_human_correction_weights,
    apply_l3_sample_weights,
    apply_platt_scaling,
    compute_ece_per_class,
    compute_rps,
    create_sample_weights,
    fit_platt_scaling,
)

N_SPLITS = 5
# 单联赛 fold 内样本不足此数时不拟合该联赛 Platt，回退全局
CALIB_MIN_LEAGUE = 20


# ==================== 4 组权重构造 ====================
def weights_baseline(df) -> np.ndarray:
    """① 基线 = 生产现行合成：max(异常加权, L3, B4)。"""
    a = create_sample_weights(df, verbose=False)
    l3 = apply_l3_sample_weights(df, None, verbose=False)
    b4 = apply_human_correction_weights(df, None, verbose=False)
    return np.maximum(np.maximum(a, l3), b4)


def weights_no_anomaly(df) -> np.ndarray:
    """② 去异常加权 = max(L3, B4)。"""
    l3 = apply_l3_sample_weights(df, None, verbose=False)
    b4 = apply_human_correction_weights(df, None, verbose=False)
    return np.maximum(l3, b4)


def weights_anomaly_only(df) -> np.ndarray:
    """③ 去 L3/B4 = 仅异常加权。"""
    return create_sample_weights(df, verbose=False)


# ==================== 分联赛 Platt 校准（H3） ====================
def _fit_per_league(ytr: np.ndarray, Ptr: np.ndarray, lg_tr: np.ndarray) -> dict:
    """每联赛拟合 Platt（fold 内样本 < CALIB_MIN_LEAGUE 则跳过，回退全局）。"""
    params = {}
    for lg in np.unique(lg_tr):
        m = lg_tr == lg
        if int(m.sum()) < CALIB_MIN_LEAGUE:
            continue
        params[lg] = fit_platt_scaling(ytr[m], Ptr[m])
    return params


def _apply_per_league(P, leagues, params, default_params):
    """按联赛套用对应 Platt；无对应联赛参数则用全局参数。"""
    out = np.zeros_like(P)
    for lg in np.unique(leagues):
        m = leagues == lg
        p = params.get(lg, default_params)
        out[m] = apply_platt_scaling(P[m], p) if p is not None else P[m]
    return out


# ==================== 单臂训练（5 折时序池化 OOF） ====================
def run_arm(X, y, weights, leagues, tag: str, per_league_calib: bool = False) -> dict:
    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
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
            print(f"  [{tag}] fold{fold} 训练失败，跳过")
            continue
        Pva = model.predict(xgb.DMatrix(Xva_s))
        if per_league_calib:
            # 每折内：train 折 OOF → 分联赛 Platt（近赛季=训练窗内最近数据，时序天然保证）
            Ptr = model.predict(xgb.DMatrix(Xtr_s))
            lg_tr = leagues[tr]
            global_params = fit_platt_scaling(ytr.values, Ptr)
            lg_params = _fit_per_league(ytr.values, Ptr, lg_tr)
            Pva = _apply_per_league(Pva, leagues[va], lg_params, global_params)
        preds.append(Pva)
        trues.append(yva.values)

    P = np.concatenate(preds)
    Y = np.concatenate(trues)
    rps = compute_rps(Y, P, tag)
    ece_per_class = compute_ece_per_class(Y, P, n_bins=10)  # [客胜, 平局, 主胜]
    ece_draw = float(ece_per_class[1])
    pred_draw_mean = float(P[:, 1].mean())
    draw_rate = float((Y == 1).mean())
    gap = pred_draw_mean - draw_rate
    print(f"[{tag}] RPS={rps:.4f} ECE_draw={ece_draw:.4f} "
          f"pred_draw={pred_draw_mean:.4f} actual_draw={draw_rate:.4f} "
          f"gap={gap:+.4f} (n={len(Y)})")
    return {
        "tag": tag, "rps": rps, "ece_draw": ece_draw,
        "ece_per_class": ece_per_class,
        "pred_draw_mean": pred_draw_mean, "draw_rate": draw_rate,
        "gap": gap, "n": len(Y),
    }


def main() -> None:
    print("加载数据 + 构建特征（生产同源）...")
    df = load_match_data_odds()
    print(f"  df 行数: {len(df)}, 日期 {df['date'].min()} ~ {df['date'].max()}")
    X, y = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
    leagues = df["competition_name"].values
    print(f"  特征维度: {X.shape[1]}, 联赛数: {np.unique(leagues)}")

    results = []
    results.append(run_arm(X, y, weights_baseline(df), leagues, "①基线"))
    results.append(run_arm(X, y, weights_no_anomaly(df), leagues, "②去异常加权"))
    results.append(run_arm(X, y, weights_anomaly_only(df), leagues, "③去L3B4"))
    results.append(run_arm(X, y, weights_baseline(df), leagues, "④分联赛校准", per_league_calib=True))

    print("\n=== 汇总 ===")
    base = results[0]
    hdr = f"{'臂':<14}{'RPS':>8}{'ECE_draw':>10}{'pred_draw':>11}{'actual':>9}{'gap':>9}"
    print(hdr)
    for r in results:
        print(f"{r['tag']:<14}{r['rps']:>8.4f}{r['ece_draw']:>10.4f}"
              f"{r['pred_draw_mean']:>11.4f}{r['draw_rate']:>9.4f}{r['gap']:>+9.4f}")

    print("\n=== 对照（相对①基线） ===")
    for r in results[1:]:
        print(f"  {r['tag']}: ΔRPS={r['rps']-base['rps']:+.4f} "
              f"ΔECE_draw={r['ece_draw']-base['ece_draw']:+.4f} "
              f"Δgap={r['gap']-base['gap']:+.4f}")


if __name__ == "__main__":
    main()