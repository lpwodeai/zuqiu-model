# -*- coding: utf-8 -*-
"""
NN vs 树模型 — WDL 分类对照实验
================================
借鉴「Embedding + 多任务共享表示 + Neural Poisson 比分头 + 冷门加权」四样，
用 PyTorch 神经网络与现有 LightGBM 在【同一 time-series split】上做 WDL 三分类对照。

核心设计:
  1. Embedding : team_id(16维) + league_id(8维)，与 187+ 维数值特征拼接
  2. 共享表示   : 2 层 MLP backbone (128 -> 64)
  3. Neural Poisson 头: 输出 λ_home / λ_away，经 Poisson PMF 折算 P(客胜/平/主胜)
  4. 冷门加权   : Focal Loss(γ=2) + 平局 class weight 上浮

说明:
  - 纯对照实验脚本，不修改/不写回任何现有模型文件或数据库主链。
  - 数据与 train_models.py 同源 (feature_utils.load_match_data_odds + build_all_features)。
  - 切分与 train_models.py 一致: sklearn TimeSeriesSplit (按日期时序，无随机打乱)。

运行:
  python scripts/nn_vs_tree_wdl_experiment.py
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 确保可导入 scripts 目录下的 feature_utils / elo_rating 等模块
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "scripts"))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import torch
import torch.nn as nn
import torch.nn.functional as F

import lightgbm as lgb
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler

from feature_utils import load_match_data_odds, build_all_features, load_config

SEED = 42
np.random.seed(SEED)
torch.manual_seed(SEED)

# ============================================================
# 1. 模型定义
# ============================================================

def lambda_to_wdl_probs(lam_home, lam_away, max_goals=10):
    """Neural Poisson 头: 由 λ_home/λ_away 经泊松 PMF 折算 WDL 概率。

    返回形状 (N, 3)，列顺序与 y 标签一致: [客胜, 平, 主胜] = [0, 1, 2]。
    """
    device = lam_home.device
    n = lam_home.shape[0]
    ks = torch.arange(0, max_goals + 1, dtype=torch.float32, device=device)
    log_fact = torch.lgamma(ks + 1.0)

    log_p_home = ks[None, :] * torch.log(lam_home[:, None] + 1e-9) - lam_home[:, None] - log_fact[None, :]
    log_p_away = ks[None, :] * torch.log(lam_away[:, None] + 1e-9) - lam_away[:, None] - log_fact[None, :]
    p_home = torch.exp(log_p_home)
    p_away = torch.exp(log_p_away)

    # P(客胜) = Σ_j P(away=j) * P(home < j)
    c_home = torch.cumsum(p_home, dim=1)
    p_home_less = torch.cat([torch.zeros(n, 1, device=device), c_home[:, :-1]], dim=1)
    p_away_win = (p_away * p_home_less).sum(dim=1)

    # P(平) = Σ_k P(home=k) P(away=k)
    p_draw = (p_home * p_away).sum(dim=1)

    # P(主胜) = 1 - 平 - 客胜（含尾部分量修正后归一）
    p_home_win = 1.0 - p_away_win - p_draw
    probs = torch.stack([p_away_win, p_draw, p_home_win], dim=1)
    probs = probs / probs.sum(dim=1, keepdim=True).clamp(min=1e-9)
    return probs


class NeuralPoissonWDL(nn.Module):
    def __init__(self, n_features, n_teams, n_leagues,
                 team_dim=16, league_dim=8, hidden=128, dropout=0.3):
        super().__init__()
        self.team_emb = nn.Embedding(n_teams, team_dim)
        self.league_emb = nn.Embedding(n_leagues, league_dim)
        in_dim = n_features + 2 * team_dim + league_dim
        self.backbone = nn.Sequential(
            nn.Linear(in_dim, hidden), nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden, hidden // 2), nn.ReLU(), nn.Dropout(dropout),
        )
        self.lam_head = nn.Linear(hidden // 2, 2)

    def forward(self, x_num, home_team, away_team, league):
        he = self.team_emb(home_team)
        ae = self.team_emb(away_team)
        le = self.league_emb(league)
        z = torch.cat([x_num, he, ae, le], dim=1)
        z = self.backbone(z)
        lam = F.softplus(self.lam_head(z)) + 1e-2
        return lam[:, 0], lam[:, 1]


# ============================================================
# 2. 损失与指标
# ============================================================

def focal_loss(probs, target, gamma=2.0, class_weight=None):
    """Focal Loss — 对置信但预测错的样本（如爆冷）自动加大梯度权重。"""
    pt = probs.gather(1, target.view(-1, 1)).clamp(min=1e-9).squeeze(1)
    ce = -torch.log(pt)
    focal = ((1.0 - pt) ** gamma) * ce
    if class_weight is not None:
        focal = focal * class_weight[target]
    return focal.mean()


def balanced_class_weight(y, draw_boost=1.2):
    """类别权重（反频率归一） + 平局上浮，对应「冷门/平局加权」。"""
    counts = np.bincount(y, minlength=3).astype(np.float64)
    counts = np.where(counts == 0, 1, counts)
    w = counts.sum() / (3.0 * counts)
    w = w / w.mean()
    w[1] *= draw_boost
    return torch.tensor(w, dtype=torch.float32)


def balanced_sample_weight(y):
    counts = np.bincount(y, minlength=3).astype(np.float64)
    counts = np.where(counts == 0, 1, counts)
    w = 1.0 / counts[y]
    return w / w.mean()


def compute_ece(y_true, probs, n_bins=10):
    """置信度口径的多分类 ECE（预测置信度 vs 实际准确率）。"""
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
# 3. 数据集构建（与 train_models.py 同源）
# ============================================================

def build_dataset():
    print("加载比赛数据 (同 train_models.py)...")
    df = load_match_data_odds()
    print(f"  共 {len(df)} 场比赛")

    print("构建特征 build_all_features(include_odds=True)...")
    X, y = build_all_features(df, include_odds=True)
    X = X.fillna(0.0)
    y = y.astype(int).values
    print(f"  X shape={X.shape}, y 分布={dict(zip(*np.unique(y, return_counts=True)))}")

    # 类别特征: team_id (主/客共用同一份词典) + league_id
    teams = sorted(set(df["home_team_name"]).union(set(df["away_team_name"])))
    team2id = {t: i for i, t in enumerate(teams)}
    leagues = sorted(df["competition_name"].unique())
    league2id = {l: i for i, l in enumerate(leagues)}

    home_team = df["home_team_name"].map(team2id).astype(int).values
    away_team = df["away_team_name"].map(team2id).astype(int).values
    league = df["competition_name"].map(league2id).astype(int).values

    print(f"  球队数={len(teams)}, 联赛数={len(leagues)}")
    return X, y, home_team, away_team, league, len(teams), len(leagues)


# ============================================================
# 4. 两个模型在各折上的训练与评估
# ============================================================

def run_lightgbm_fold(X_train, y_train, X_val, y_val):
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
    model = lgb.LGBMClassifier(
        **params, n_estimators=n_est, class_weight="balanced", random_state=SEED,
    )
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)], eval_metric="multi_logloss",
        callbacks=[lgb.early_stopping(15, verbose=False)],
    )
    probs = model.predict_proba(X_val)
    return compute_metrics(y_val, probs)


def run_nn_fold(X_train, y_train, h_train, a_train, l_train,
                X_val, y_val, h_val, a_val, l_val,
                n_teams, n_leagues):
    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train).astype(np.float32)
    X_val_s = scaler.transform(X_val).astype(np.float32)

    class_w = balanced_class_weight(y_train, draw_boost=1.2)

    model = NeuralPoissonWDL(
        n_features=X_train.shape[1], n_teams=n_teams, n_leagues=n_leagues,
    )
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)

    def to_torch(s):
        return torch.from_numpy(s)

    x_tr = to_torch(X_train_s)
    h_tr = torch.from_numpy(h_train.astype(np.int64))
    a_tr = torch.from_numpy(a_train.astype(np.int64))
    l_tr = torch.from_numpy(l_train.astype(np.int64))
    y_tr = torch.from_numpy(y_train.astype(np.int64))

    x_va = to_torch(X_val_s)
    h_va = torch.from_numpy(h_val.astype(np.int64))
    a_va = torch.from_numpy(a_val.astype(np.int64))
    l_va = torch.from_numpy(l_val.astype(np.int64))

    n = len(y_train)
    batch_size = 64
    best_val_loss = float("inf")
    best_probs = None
    patience = 20
    bad = 0

    for epoch in range(150):
        model.train()
        perm = torch.randperm(n)
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            opt.zero_grad()
            lam_h, lam_a = model(x_tr[idx], h_tr[idx], a_tr[idx], l_tr[idx])
            probs = lambda_to_wdl_probs(lam_h, lam_a)
            loss = focal_loss(probs, y_tr[idx], gamma=2.0, class_weight=class_w)
            loss.backward()
            opt.step()

        model.eval()
        with torch.no_grad():
            lam_h, lam_a = model(x_va, h_va, a_va, l_va)
            val_probs = lambda_to_wdl_probs(lam_h, lam_a)
            val_loss = log_loss(y_val, val_probs.numpy(), labels=[0, 1, 2])

        if val_loss < best_val_loss - 1e-5:
            best_val_loss = val_loss
            best_probs = val_probs.numpy()
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break

    if best_probs is None:
        best_probs = val_probs.numpy()
    return compute_metrics(y_val, best_probs)


# ============================================================
# 5. 主流程
# ============================================================

def main():
    t0 = time.time()
    X, y, home_team, away_team, league, n_teams, n_leagues = build_dataset()

    n_splits = 5
    tscv = TimeSeriesSplit(n_splits=n_splits)
    lgb_folds, nn_folds = [], []

    print(f"\n滚动窗口对照 ({n_splits} 折 TimeSeriesSplit)")
    print("=" * 70)

    for fold, (tr_idx, va_idx) in enumerate(tscv.split(X)):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y[tr_idx], y[va_idx]
        print(f"\n--- 第 {fold + 1}/{n_splits} 折: train={len(tr_idx)}, val={len(va_idx)} ---")

        lgb_m = run_lightgbm_fold(X_tr.values, y_tr, X_va.values, y_va)
        lgb_folds.append(lgb_m)
        print(f"  [LightGBM] acc={lgb_m['accuracy']:.4f} ll={lgb_m['log_loss']:.4f} "
              f"ece={lgb_m['ece']:.4f} draw_r={lgb_m['draw_recall']:.4f}")

        nn_m = run_nn_fold(
            X_tr.values, y_tr, home_team[tr_idx], away_team[tr_idx], league[tr_idx],
            X_va.values, y_va, home_team[va_idx], away_team[va_idx], league[va_idx],
            n_teams, n_leagues,
        )
        nn_folds.append(nn_m)
        print(f"  [NN]        acc={nn_m['accuracy']:.4f} ll={nn_m['log_loss']:.4f} "
              f"ece={nn_m['ece']:.4f} draw_r={nn_m['draw_recall']:.4f}")

    lgb_mean = mean_metrics(lgb_folds)
    nn_mean = mean_metrics(nn_folds)

    # 结果保存与汇总
    result = {
        "experiment": "nn_vs_tree_wdl_experiment",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_samples": len(y),
        "n_features": X.shape[1],
        "n_teams": n_teams,
        "n_leagues": n_leagues,
        "n_splits": n_splits,
        "lightgbm_folds": lgb_folds,
        "nn_folds": nn_folds,
        "lightgbm_mean": lgb_mean,
        "nn_mean": nn_mean,
    }
    out_dir = BASE_DIR / "assets"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"nn_wdl_experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print("对照结果汇总 (各折均值)")
    print("-" * 70)
    header = f"{'指标':<14}{'LightGBM':>14}{'NN':>14}{'Δ(NN-LGBM)':>14}"
    print(header)
    for k, label in [("accuracy", "准确率"), ("log_loss", "LogLoss"),
                     ("brier", "Brier"), ("ece", "ECE"), ("draw_recall", "平局召回率")]:
        delta = nn_mean[k] - lgb_mean[k]
        print(f"{label:<14}{lgb_mean[k]:>14.4f}{nn_mean[k]:>14.4f}{delta:>+14.4f}")
    print("-" * 70)
    print(f"用时 {time.time() - t0:.1f}s")
    print(f"结果已保存: {out_path}")


if __name__ == "__main__":
    main()