# -*- coding: utf-8 -*-
"""
P1-7 Stacking + LR meta-learner 训练脚本。

目标：从固定权重加权平均升级为真正的 Stacking + LR meta-learner。
基础模型池（5 个不同方法论）:
  1. Dixon-Coles    —— 联赛平均进球 + DC 低比分修正（纯赛果统计先验）
  2. Elo            —— 时序 Elo 评分 + 动量
  3. XGBoost        —— 树模型（含赔率/特征）
  4. LightGBM       —— 树模型（含赔率/特征）
  5. 贝叶斯层级     —— 分联赛 MAP + 层级先验（P1-5）

方法：用 TimeSeriesSplit 生成 5 个基础模型的 OOF（out-of-fold）预测，作为
15 维 meta 特征（5 模型 × [win, draw, lose]），再训练 LogisticRegression
（multinomial）meta-learner，输出最终 WDL 概率。

输出：
  assets/stacking_meta_learner.json
    {models, feature_names, classes, class_labels, coef_, intercept_,
     rps_oof_full, rps_oof_holdout, acc_oof_holdout, rps_fixed_full, n_train}
"""
import os
import sys
import json

import numpy as np
import pandas as pd
import xgboost as xgb

from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

from feature_utils import load_match_data_odds, build_all_features
from prediction_core import CalcEngine, STACKING_WEIGHTS
from bayesian_hierarchical_model import BayesianHierarchicalModel
from elo_rating import precompute_elo_ratings, get_team_elo_at_date
from train_models import train_xgboost, train_lightgbm  # 复用生产级超参

LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]
LEAGUE_RHO = {"英超": -0.08, "西甲": -0.12, "意甲": -0.15, "德甲": -0.05, "法甲": -0.10}
MODELS = ["dixonColes", "elo", "xgboost", "lightgbm", "bayesian"]
DEFAULT_PROB = {"win": 0.40, "draw": 0.27, "lose": 0.33}  # 基础模型缺失时的均匀先验


def _rps(y_true, y_pred_proba):
    """RPS（列序须为 [客胜(0), 平(1), 主胜(2)]）。"""
    y_true = np.asarray(y_true)
    y_pred_proba = np.asarray(y_pred_proba)
    n = len(y_true)
    actual = np.zeros((n, 3))
    for i, v in enumerate(y_true):
        actual[i, int(v)] = 1
    cp = np.cumsum(y_pred_proba, axis=1)
    co = np.cumsum(actual, axis=1)
    return float(np.mean(np.sum((cp - co) ** 2, axis=1)) / 2)


def _acc(y_true, y_pred_proba):
    y_true = np.asarray(y_true)
    y_pred_proba = np.asarray(y_pred_proba)
    return float(np.mean(np.argmax(y_pred_proba, axis=1) == y_true.astype(int)))


def _to_label_order(p):
    """{'win','draw','lose'} -> [客胜, 平, 主胜] = [lose, draw, win]"""
    return np.array([p["lose"], p["draw"], p["win"]])


def main():
    print("=" * 70)
    print("P1-7 Stacking + LR meta-learner 训练")
    print("=" * 70)

    # 1. 数据（时间有序，reshape 连续索引）
    print("\n[1/6] 加载比赛数据...")
    df = load_match_data_odds()
    df = df.sort_values("date").reset_index(drop=True)
    print(f"  比赛总数: {len(df)}")

    # 2. 特征（XGB/LGB 输入，与生产一致：include_odds=True, slim_odds=True）
    print("\n[2/6] 构建特征...")
    X, y = build_all_features(df, include_odds=True)
    y = np.asarray(y).astype(int)
    assert len(X) == len(df), f"特征行数 {len(X)} != 比赛数 {len(df)}"
    print(f"  特征维度: {X.shape}")

    tscv = TimeSeriesSplit(n_splits=5)

    # 3. OOF 生成
    print("\n[3/6] 生成 5 基础模型 OOF 预测 (TimeSeriesSplit)...")
    oof_preds = {m: [] for m in MODELS}   # 每模型 -> label-order probs (n_val, 3)
    oof_true = []
    oof_fold = []                          # 每条样本所属 fold（用于 meta holdout）

    for fold, (tr_idx, va_idx) in enumerate(tscv.split(X)):
        print(f"\n  --- Fold {fold + 1}/5 ---")
        train_df = df.iloc[tr_idx].reset_index(drop=True)
        val_df = df.iloc[va_idx].reset_index(drop=True)
        X_train = X.iloc[tr_idx]
        X_val = X.iloc[va_idx]
        y_train = y[tr_idx]
        y_val = y[va_idx]

        # --- XGBoost / LightGBM（复用生产超参，scaler 按 fold 内拟合防泄露）---
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_val_s = scaler.transform(X_val)

        try:
            xgb_model, _ = train_xgboost(X_train_s, y_train, X_val_s, y_val)
            xgb_val = xgb_model.predict(xgb.DMatrix(X_val_s))  # (n,3) [客胜,平,主胜]
        except Exception as e:
            print(f"  [Warn] XGBoost fold 失败，降级为先验: {e}")
            xgb_val = np.tile([0.33, 0.27, 0.40], (len(y_val), 1))

        try:
            lgb_model, _ = train_lightgbm(X_train_s, y_train, X_val_s, y_val)
            lgb_val = lgb_model.predict(X_val_s)
        except Exception as e:
            print(f"  [Warn] LightGBM fold 失败，降级为先验: {e}")
            lgb_val = np.tile([0.33, 0.27, 0.40], (len(y_val), 1))

        # --- Dixon-Coles（联赛平均进球 + DC 修正）---
        lg_stats = {L: {"home": g["homeGoals"].mean(), "away": g["awayGoals"].mean()}
                    for L, g in train_df.groupby("competition_name")}

        # --- Elo（train fold 内预计算）---
        team_elo_dfs = precompute_elo_ratings(train_df)

        # --- 贝叶斯层级（分联赛 MAP）---
        bayes_models = {}
        for L in LEAGUES:
            sub = train_df[train_df["competition_name"] == L]
            if len(sub) >= 10:
                try:
                    bayes_models[L] = BayesianHierarchicalModel().fit(sub, league=L)
                except Exception as e:
                    print(f"  [Bayes-Warn] {L} 拟合失败: {e}")
                    bayes_models[L] = None
            else:
                bayes_models[L] = None

        dc_val, elo_val, bay_val = [], [], []
        for _, row in val_df.iterrows():
            L = row["competition_name"]
            # DC
            if L in lg_stats:
                rho = LEAGUE_RHO.get(L, -0.10)
                dc = CalcEngine.calc_win_draw_lose_dixon_coles(
                    lg_stats[L]["home"], lg_stats[L]["away"], rho=rho)
            else:
                dc = DEFAULT_PROB
            dc_val.append(_to_label_order(dc))
            # Elo
            h = row["home_team_name"]; a = row["away_team_name"]; d = row["date"]
            hd = get_team_elo_at_date(team_elo_dfs, h, d)
            ad = get_team_elo_at_date(team_elo_dfs, a, d)
            elo = CalcEngine.calc_win_draw_lose_elo(
                h, a, {h: hd["elo"], a: ad["elo"]},
                {h: hd.get("momentum", 0.0), a: ad.get("momentum", 0.0)})
            elo_val.append(_to_label_order(elo))
            # Bayesian
            bm = bayes_models.get(L)
            bay = bm.predict_wdl(row["home_team_name"], row["away_team_name"]) if bm is not None else DEFAULT_PROB
            bay_val.append(_to_label_order(bay))

        preds = {
            "dixonColes": np.asarray(dc_val),
            "elo": np.asarray(elo_val),
            "xgboost": np.asarray(xgb_val),
            "lightgbm": np.asarray(lgb_val),
            "bayesian": np.asarray(bay_val),
        }
        for m in MODELS:
            oof_preds[m].append(preds[m])
        oof_true.append(y_val)
        oof_fold.append(np.full(len(y_val), fold, dtype=int))

    # 4. 汇总 OOF
    oof_preds = {m: np.vstack(oof_preds[m]) for m in MODELS}   # label-order (n_total, 3)
    y_all = np.concatenate(oof_true)
    fold_all = np.concatenate(oof_fold)

    meta_cols = [f"{m}__{c}" for m in MODELS for c in ["win", "draw", "lose"]]
    X_meta = np.hstack([
        np.hstack([oof_preds[m][:, 2][:, None],   # win
                   oof_preds[m][:, 1][:, None],   # draw
                   oof_preds[m][:, 0][:, None]])  # lose
        for m in MODELS
    ])  # (n_total, 15)，特征序 [win, draw, lose] per model

    # 固定权重 Stacking（当前生产配置，用于对比）
    fixed = np.zeros((len(y_all), 3))
    total_w = 0.0
    for m in MODELS:
        w = STACKING_WEIGHTS.get(m, 0)
        if w > 0:
            fixed += w * oof_preds[m]
            total_w += w
    fixed /= total_w

    # 5. 训练 meta-learner（holdout：前 4 折训练 / 第 5 折验证）
    print("\n[4/6] 训练 LR meta-learner...")
    clf = LogisticRegression(solver="lbfgs",
                             C=1.0, max_iter=2000, random_state=42)

    hold_train = fold_all < tscv.n_splits - 1
    hold_val = ~hold_train
    clf_hold = LogisticRegression(solver="lbfgs",
                                  C=1.0, max_iter=2000, random_state=42)
    clf_hold.fit(X_meta[hold_train], y_all[hold_train])
    meta_hold_proba = clf_hold.predict_proba(X_meta[hold_val])   # [客胜,平,主胜]

    clf.fit(X_meta, y_all)
    meta_proba = clf.predict_proba(X_meta)

    # 6. 评估
    print("\n[5/6] 评估（RPS 越低越好，世界级基准 0.19-0.21）")
    print(f"{'模型':<28}{'OOF-RPS':>10}{'OOF-Acc':>10}")
    for m in MODELS:
        print(f"{m:<28}{_rps(y_all, oof_preds[m]):>10.4f}{_acc(y_all, oof_preds[m]):>10.4f}")
    print(f"{'固定权重Stacking(5模型)':<28}{_rps(y_all, fixed):>10.4f}{_acc(y_all, fixed):>10.4f}")
    print(f"{'LR meta-learner(OOF全量)':<28}{_rps(y_all, meta_proba):>10.4f}{_acc(y_all, meta_proba):>10.4f}")
    rps_hold = _rps(y_all[hold_val], meta_hold_proba)
    acc_hold = _acc(y_all[hold_val], meta_hold_proba)
    print(f"{'LR meta-learner(holdout第5折)':<28}{rps_hold:>10.4f}{acc_hold:>10.4f}")

    rps_fixed_full = _rps(y_all, fixed)
    rps_meta_full = _rps(y_all, meta_proba)

    # 保存 meta-learner
    out = {
        "models": MODELS,
        "feature_names": meta_cols,
        "classes": [0, 1, 2],
        "class_labels": ["客胜", "平", "主胜"],
        "coef_": clf.coef_.tolist(),
        "intercept_": clf.intercept_.tolist(),
        "n_train": int(len(y_all)),
        "rps_oof_full": rps_meta_full,
        "acc_oof_full": _acc(y_all, meta_proba),
        "rps_oof_holdout": rps_hold,
        "acc_oof_holdout": acc_hold,
        "rps_fixed_full": rps_fixed_full,
    }
    os.makedirs(ASSETS_DIR, exist_ok=True)
    out_path = os.path.join(ASSETS_DIR, "stacking_meta_learner.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n[6/6] 已保存: {out_path}")
    print(f"  meta-learner vs 固定权重: RPS {rps_meta_full:.4f} vs {rps_fixed_full:.4f} "
          f"({rps_meta_full - rps_fixed_full:+.4f})")


if __name__ == "__main__":
    main()