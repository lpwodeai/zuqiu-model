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

from feature_utils import load_match_data_odds, build_all_features, ODDS_DB_PATH
from prediction_core import CalcEngine, STACKING_WEIGHTS, LEAGUE_RHO
from bayesian_hierarchical_model import BayesianHierarchicalModel
from elo_rating import precompute_elo_ratings, get_team_elo_at_date
from train_models import train_xgboost, train_lightgbm  # 复用生产级超参
from db_utils import connect

LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]
# LEAGUE_RHO 从 prediction_core 导入（train/serve 同源）
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


def _build_odds_data_from_db(conn, match_id):
    """从 odds.db 读取历史赔率，构建与 serving 同构的 odds_data。

    wdl_history: win_a=主胜, draw=平, win_b=客胜 → {'win','draw','lose'}
    total_goals_history: goals_0..goals_7_plus → {'0'..'7+'}
    返回 odds_data 或 None（无赔率）。
    """
    rows = conn.execute(
        "SELECT timestamp, win_a, draw, win_b FROM wdl_history "
        "WHERE match_id=? ORDER BY timestamp", (match_id,)).fetchall()
    if not rows:
        return None
    records = [{'time': r[0], 'win': float(r[1]), 'draw': float(r[2]), 'lose': float(r[3])}
               for r in rows]
    wdl_odds = {'close': records[-1], 'open': records[0], 'records': records}

    tg_rows = conn.execute(
        "SELECT timestamp, goals_0, goals_1, goals_2, goals_3, "
        "goals_4, goals_5, goals_6, goals_7_plus "
        "FROM total_goals_history WHERE match_id=? ORDER BY timestamp",
        (match_id,)).fetchall()
    tg_records = []
    for r in tg_rows:
        goals = {}
        for idx, k in enumerate(['0', '1', '2', '3', '4', '5', '6', '7+']):
            v = r[idx + 1]
            if v is not None:
                goals[k] = float(v)
        if goals:
            tg_records.append({'pub_time': r[0], 'goals': goals})

    return {'wdl_odds': wdl_odds, 'tg_odds': {'records': tg_records}}


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
    oof_dc_fallback = []                  # C-20260920-030: DC 兜底标志（16th meta feature）

    # C-20260920-030: 打开 odds.db 连接，OOF DC 用赔率λ（与 serving 同源）
    db_conn = connect(db_path=ODDS_DB_PATH)

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

        # --- Dixon-Coles（C-20260920-030: 与 serving 同源 — 赔率λ + A-002 + 联赛ρ）---
        # 缺赔率时回退联赛均值λ + 标记 fallback=1（让 meta 区分两分布）
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

        dc_val, dc_fallback_val, elo_val, bay_val = [], [], [], []
        for _, row in val_df.iterrows():
            L = row["competition_name"]
            rho = LEAGUE_RHO.get(L, -0.10)
            # DC — 优先赔率λ（与 serving 同链路），无赔率回退联赛均值
            is_fallback = True
            odds_data = _build_odds_data_from_db(db_conn, row.get("match_id", ""))
            if odds_data and odds_data.get('wdl_odds', {}).get('close'):
                try:
                    lh, la = CalcEngine.calc_lambda_from_odds(odds_data)
                    lh, la, _ = CalcEngine.adjust_lambda_for_mid_score(lh, la, odds_data)
                    dc = CalcEngine.calc_win_draw_lose_dixon_coles(lh, la, rho=rho)
                    is_fallback = False
                except Exception:
                    pass
            if is_fallback:
                if L in lg_stats:
                    dc = CalcEngine.calc_win_draw_lose_dixon_coles(
                        lg_stats[L]["home"], lg_stats[L]["away"], rho=rho)
                else:
                    dc = DEFAULT_PROB
            dc_val.append(_to_label_order(dc))
            dc_fallback_val.append(1.0 if is_fallback else 0.0)
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
        oof_dc_fallback.append(np.asarray(dc_fallback_val))

    db_conn.close()

    # 4. 汇总 OOF
    oof_preds = {m: np.vstack(oof_preds[m]) for m in MODELS}   # label-order (n_total, 3)
    y_all = np.concatenate(oof_true)
    fold_all = np.concatenate(oof_fold)
    dc_fallback_all = np.concatenate(oof_dc_fallback)

    meta_cols = [f"{m}__{c}" for m in MODELS for c in ["win", "draw", "lose"]]
    meta_cols.append("dc_odds_fallback")
    X_meta = np.hstack([
        np.hstack([oof_preds[m][:, 2][:, None],   # win
                   oof_preds[m][:, 1][:, None],   # draw
                   oof_preds[m][:, 0][:, None]])  # lose
        for m in MODELS
    ])  # (n_total, 15)
    X_meta = np.hstack([X_meta, dc_fallback_all[:, None]])  # (n_total, 16)

    n_odds = int((dc_fallback_all == 0).sum())
    n_fallback = int((dc_fallback_all == 1).sum())
    print(f"\n  DC λ 来源: 赔率={n_odds} 场, 兜底={n_fallback} 场")

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