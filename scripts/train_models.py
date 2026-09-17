import pandas as pd
import numpy as np
import json
import os
import yaml
import sqlite3
from datetime import datetime

try:
    import xgboost as xgb
except ImportError:
    xgb = None
    print("Warning: xgboost not installed, will skip XGBoost training")

try:
    import lightgbm as lgb
except ImportError:
    lgb = None
    print("Warning: lightgbm not installed, will skip LightGBM training")

from sklearn.model_selection import train_test_split, TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss
from sklearn.preprocessing import StandardScaler

# D1: MLflow 实验追踪（可选依赖；未安装时静默跳过，绝不因追踪失败影响训练流程）
try:
    import mlflow
    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False


# C-20260823-P0-2: RPS (Ranked Probability Score) 计算函数
def compute_rps(y_true, y_pred_proba, model_name: str = ""):
    """计算 RPS（足球预测黄金指标），世界级基准 0.19-0.21。
    
    RPS = (1/2) * mean(sum_k (cumsum(p_k) - cumsum(o_k))^2)
    其中 p_k 为预测概率，o_k 为实际结果（one-hot）。
    
    Args:
        y_true: 实际标签 (0=客胜, 1=平局, 2=主胜)
        y_pred_proba: 预测概率 (n_samples, 3)
    
    Returns:
        float: RPS 值
    """
    n = len(y_true)
    actual_hda = np.zeros((n, 3))
    for i, yv in enumerate(y_true):
        # C-20260823-020: 修复列顺序错位 bug。
        # y_pred_proba 列序 = [客胜(0), 平局(1), 主胜(2)] (label 升序)
        # 原实现 actual_hda 列序 = [主胜, 平局, 客胜]，导致 cumsum 方向相反、RPS 系统性偏大
        actual_hda[i, int(yv)] = 1  # 与 y_pred_proba 列序对齐
    cum_p = np.cumsum(y_pred_proba, axis=1)
    cum_o = np.cumsum(actual_hda, axis=1)
    rps_val = float(np.mean(np.sum((cum_p - cum_o) ** 2, axis=1)) / 2)

    # [RPS-Monitor] RPS 计算过程监控日志（列序已修复，与 y_pred_proba 对齐）
    _tag = f"[{model_name}]" if model_name else "[model]"
    _pred_mean = np.mean(y_pred_proba, axis=0)
    _actual_mean = np.mean(actual_hda, axis=0)
    _gap = rps_val - 0.21
    _grade = "达标" if rps_val <= 0.21 else ("偏高" if rps_val <= 0.24 else "明显偏高")
    print(f"  [RPS-Monitor] {_tag} 样本数={n} pred_shape={tuple(y_pred_proba.shape)}")
    print(f"  [RPS-Monitor] {_tag} 列均值(pred 客胜/平/主)=({_pred_mean[0]:.3f}/{_pred_mean[1]:.3f}/{_pred_mean[2]:.3f})")
    print(f"  [RPS-Monitor] {_tag} 列均值(actual 客胜/平/主)=({_actual_mean[0]:.3f}/{_actual_mean[1]:.3f}/{_actual_mean[2]:.3f})")
    print(f"  [RPS-Monitor] {_tag} RPS={rps_val:.4f} 基准[0.19-0.21] {_grade} (vs上限{_gap:+.4f})")
    return rps_val
from sklearn.linear_model import LogisticRegression
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "assets"
CONFIG_PATH = BASE_DIR / "config.yaml"
ANOMALY_DB_PATH = BASE_DIR / "data" / "anomaly_samples.db"

# 平局决策阈值因子：提升平局预测召回率（class=1）
# 不修改概率，仅在分类时降低平局阈值，保持概率校准
# factor=1.5 时等价于原 draw_boost=0.5 的分类效果，但不破坏概率校准
DRAW_THRESHOLD_FACTOR = 1.5

# P0-4: 统一预测引擎开关
# 设为 True 时启用 Dixon-Coles 统一架构（比分 ← λ_home, λ_away → WDL/让球/大小球）
# 设为 False 时使用旧 T-005/T-006 独立架构（向后兼容）
USE_UNIFIED_ENGINE = False  # 默认关闭，验证通过后设为 True

# === 平局召回率后处理校准 (2026-08-21 精细搜索最优参数) ===
# 方案A: 决策阈值法 apply_draw_threshold (不改概率, 用 DRAW_THRESHOLD_FACTOR)
# 方案B: 概率缩放法 DrawCalibrator (改平局概率后重归一化, 用 DRAW_CALIBRATOR_FACTOR)
# 精细搜索 (5折CV, 209维XGBoost, 约束 draw_recall>=0.28):
#   - 最优 DrawCalibrator factor=0.885: acc=0.4884, draw_recall=0.2813 (准确率损失最小)
#   - 保守配置 factor=0.850:  acc=0.4849, draw_recall=0.3036 (召回率储备更高)
# 最终选择: factor=0.885，平局召回率刚好达标(>0.28)，准确率损失仅 1.12pp
DRAW_CALIBRATOR_FACTOR = 0.885  # 推荐值 (draw_recall>=0.28, 准确率最高)
DRAW_CALIBRATOR_CONSERVATIVE = 0.850  # 保守值 (draw_recall>=0.30)

from feature_utils import (
    load_config, load_match_data, load_match_data_odds,
    build_features, build_team_features, build_all_features,
    build_odds_features, calc_h2h_stats, precompute_team_stats, get_global_league_stats
)

from odds_data_spec import TrainingLogger
from draw_calibrator import DrawCalibrator  # T-003.4 平局召回率后处理校准
from stratified_evaluation import compute_stratified_report, print_stratified_report  # P2-14 分层评估
from quality_gate import run_quality_gate, print_quality_gate  # P2-15 数据质量门禁
from focal_loss import focal_obj_xgb, focal_fobj_lgb, focal_feval_lgb, configure as configure_focal_loss  # P2-13 Focal Loss
from ev_loss import ev_obj_xgb, ev_fobj_lgb, ev_feval_lgb, configure as configure_ev_loss  # C-20260904-003 EV/ROI 目标
from mod_loss import mod_obj_xgb, mod_fobj_lgb, mod_feval_lgb, configure as configure_mod_loss  # C-20260904-004 市场赔率蒸馏
from penalty_loss import pen_obj_xgb, pen_fobj_lgb, pen_feval_lgb, configure as configure_pen_loss  # C-20260905-001 高赔率未命中惩罚

CONFIG = load_config()

# === P2-13: Focal Loss（平局模型层面解决） ===
# 开启后 XGBoost/LightGBM 使用多分类 Focal Loss 自定义目标（默认关闭，A/B 验证通过后启用）。
# 与后处理 DrawCalibrator/draw_threshold 互斥决策：focal loss 旨在不依赖后处理达成平局召回率>=30%。
USE_FOCAL_LOSS = False
FOCAL_GAMMA = 2.0                     # 聚焦参数，越大越聚焦难分样本（平局/爆冷）
FOCAL_ALPHA = [1.0, 1.4, 1.0]         # 类别权重[客胜,平局,主胜]，平局上调 1.4（平局-specific）

# === C-20260904-003: 训练端 EV/ROI 目标改造 ===
# 开启后 XGBoost/LightGBM 使用 EV-policy Loss 自定义目标（CE 锚点 + λ·EVL，见 ev_loss.py）。
# 概率层四连证伪后唯一剩路：让模型在训练端直接对 EV/ROI 求导，修复 edge 分桶非单调/系统性高估。
# 默认关闭（生产行为不变）；EV-loss OOF 实验通过 generate_oof_evloss.py 传入 odds_train 开启。
USE_EV_LOSS = False
EV_LOSS_LAMBDA_CE = 1.0               # EV 分量权重（CE 权重恒为 1.0）；扫描 {0.3, 1.0, 3.0}
EV_ODDS_CAP = 10.0                    # 真方向赔率截断上界（防极端冷门梯度爆炸）

# === C-20260904-004: 市场赔率蒸馏（Market-Odds Distillation, MOD）训练目标 ===
# 开启后 XGBoost/LightGBM 使用 MOD Loss 自定义目标（CE 锚点 + λ·KL(p‖q_market)，见 mod_loss.py）。
# 背景：C-20260904-003 证伪朴素 EV-policy Loss（对 EV 直接求导加剧高赔率过度自信 + LGB 负海森退化）。
# MOD 换向：把 p_model 拉向市场赔率去抽水隐含概率 q_soft（对抗高赔率方向过度自信），
# 梯度 p−q_soft、海森恒正 p(1-p)(1+λ)，无 EV-loss 的数值退化问题。与 USE_EV_LOSS 互斥。
USE_MOD_LOSS = False
MOD_LOSS_LAMBDA = 1.0                 # 蒸馏分量权重（CE 权重恒为 1.0）；扫描 {0.3, 1.0, 3.0}
MOD_LOSS_GAMMA = 1.0                  # 软目标温度软化指数（1.0=纯去抽水，<1 更扁平）

# === C-20260905-001: 高赔率未命中显式惩罚（High-odds Miss Penalty）训练目标 ===
# 开启后 XGBoost/LightGBM 使用 Penalty Loss 自定义目标（CE 锚点 + λ·P，见 penalty_loss.py）。
# 背景：EV 奖励（C-20260904-003）/ MOD 模仿（C-20260904-004）双路径证伪后唯一剩路——
# 模型对「高赔率（高 edge）方向」系统性过度自信（高赔率真结果实际频率 < 模型概率）。
# P 只惩罚「高赔率冷门胜方向（客胜/主胜 ≥ o_thresh，平局豁免）且未命中」的模型置信，
# 力度 ∝ p_k（越自信罚越狠）——对抗而非奖励/模仿高赔率方向。与 USE_EV_LOSS/USE_MOD_LOSS 互斥。
USE_PENALTY_LOSS = False
PENALTY_LOSS_LAMBDA = 1.0             # 惩罚分量权重（CE 权重恒为 1.0）；扫描 {0.3, 1.0, 3.0}
PENALTY_ODDS_THRESH = 3.5             # 高赔率胜方向阈值（>10pp 桶均赔 3.5；客胜 p50=3.33）


def _lgb_predict_proba(model, X):
    """LightGBM 自定义目标（Focal/EV/MOD/Penalty Loss）下 predict 返回原始 logit，需 softmax 还原概率。"""
    raw = np.asarray(model.predict(X), dtype=np.float64)
    if USE_FOCAL_LOSS or USE_EV_LOSS or USE_MOD_LOSS or USE_PENALTY_LOSS:
        raw = raw - raw.max(axis=1, keepdims=True)
        e = np.exp(raw)
        return e / e.sum(axis=1, keepdims=True)
    return raw

# === Optuna 调优最优参数 (2026-08-21, 30 trials × 2 models) ===
# 当 USE_OPTUNA_BEST_PARAMS=True 时，忽略 config.yaml 中的默认参数
USE_OPTUNA_BEST_PARAMS = True

XGB_OPTUNA_BEST = {
    'objective': 'multi:softprob',
    'num_class': 3,
    'eval_metric': 'mlogloss',
    'max_depth': 4,
    'learning_rate': 0.07,          # 文档推荐 0.07 不变
    'subsample': 0.785,
    'colsample_bytree': 0.827,
    'gamma': 5.0,                   # 进一步收紧: 4.5→5.0 (旧模型 4.956)
    'min_child_weight': 13,         # 对齐旧模型: 10→13 (旧模型 13)
    'max_delta_step': 0,
    'reg_alpha': 0.1,              # L1 正则
    'reg_lambda': 8.0,             # 对齐旧模型: 5.0→8.0 (旧模型 8.003)
    'scale_pos_weight': 2.108,
    'seed': 42,
    'nthread': -1,
}
XGB_OPTUNA_NUM_ROUNDS = 130  # 减少迭代: 178→130 (lr=0.07 下足够收敛)

LGB_OPTUNA_BEST = {
    'objective': 'multiclass',
    'num_class': 3,
    'metric': 'multi_logloss',
    'max_depth': 4,                 # 收紧: 6→4 (旧模型 3, 减少 50% 深度)
    'learning_rate': 0.12,
    'num_leaves': 128,              # 收紧: 212→128 (减少复杂度)
    'subsample': 0.680,
    'colsample_bytree': 0.540,
    'reg_alpha': 3.353,
    'reg_lambda': 13.904,
    'min_child_weight': 8,
    'min_data_in_leaf': 60,         # 收紧: 40→60 (增加叶节点最小样本)
    'feature_fraction': 0.566,
    'bagging_fraction': 0.783,
    'bagging_freq': 5,
    'seed': 42,
    'verbose': 0,
}
LGB_OPTUNA_NUM_ROUNDS = 80  # 减少迭代: 105→80 (lr=0.12 下足够收敛)

def get_anomaly_match_ids():
    try:
        conn = sqlite3.connect(ANOMALY_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT match_id FROM anomaly_samples")
        anomaly_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        return anomaly_ids
    except Exception as e:
        print(f"读取异常样本库失败: {e}")
        return []

def create_sample_weights(df, anomaly_weight=3.0, double_error_weight=4.0):
    anomaly_ids = get_anomaly_match_ids()
    if not anomaly_ids:
        print("未找到异常样本，使用等权重")
        return np.ones(len(df))
    
    print(f"\n加载到 {len(anomaly_ids)} 个异常样本")
    
    cn_to_en_mapping = {
        '利物浦': 'Liverpool',
        '切尔西': 'Chelsea',
        '阿森纳': 'Arsenal',
        '曼城': 'Manchester_City',
        '曼联': 'Manchester_United',
        '热刺': 'Tottenham_Hotspur',
        '纽卡斯尔': 'Newcastle_United',
        '布莱顿': 'Brighton_&_Hove_Albion',
        '伯恩茅斯': 'AFC_Bournemouth',
        '利兹联': 'Leeds_United',
        '埃弗顿': 'Everton',
        '阿斯顿维拉': 'Aston_Villa',
        '富勒姆': 'Fulham',
        '桑德兰': 'Sunderland',
        '西汉姆': 'West_Ham_United',
        '伯恩利': 'Burnley',
        '狼队': 'Wolverhampton_Wanderers',
        '诺丁汉森林': 'Nottingham_Forest',
        '布伦特福德': 'Brentford',
        '水晶宫': 'Crystal_Palace',
    }
    
    weights = np.ones(len(df))
    
    for idx, row in df.iterrows():
        date_str = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
        home_cn = str(row['home_team_name']).strip()
        away_cn = str(row['away_team_name']).strip()
        
        home_en = cn_to_en_mapping.get(home_cn, home_cn).replace(' ', '_')
        away_en = cn_to_en_mapping.get(away_cn, away_cn).replace(' ', '_')
        
        match_id_cn = f"{date_str}_{home_cn.replace(' ', '_')}_{away_cn.replace(' ', '_')}"
        match_id_en = f"{date_str}_{home_en}_{away_en}"
        
        matched_id = None
        if match_id_cn in anomaly_ids:
            matched_id = match_id_cn
        elif match_id_en in anomaly_ids:
            matched_id = match_id_en
        
        if matched_id:
            try:
                conn = sqlite3.connect(ANOMALY_DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT anomaly_type FROM anomaly_samples WHERE match_id = ?", (matched_id,))
                result = cursor.fetchone()
                conn.close()
                
                if result and result[0] == '双重错误':
                    weights[idx] = double_error_weight
                    print(f"  双重错误样本: {matched_id} -> 权重 {double_error_weight}")
                else:
                    weights[idx] = anomaly_weight
                    print(f"  异常样本: {matched_id} -> 权重 {anomaly_weight}")
            except Exception as e:
                weights[idx] = anomaly_weight
    
    print(f"\n样本权重统计:")
    print(f"  正常样本: {len(weights[weights == 1.0])}")
    print(f"  异常样本(权重{anomaly_weight}): {len(weights[weights == anomaly_weight])}")
    print(f"  双重错误样本(权重{double_error_weight}): {len(weights[weights == double_error_weight])}")
    print(f"  平均权重: {weights.mean():.2f}")
    
    return weights

# === B3: L3 样本权重加载器（C-20260908-010，knowledge_iteration.py --approve 闭环） ===
def apply_l3_sample_weights(df, base_weights=None, verbose=True):
    """读知识库 L3 sample_weights（status=active），按 source_matches 对样本加权。

    B3 闭环语义：knowledge_iteration.py 扫描复盘数据 → 写 pending 建议 →
    人工 --approve 转 active → 本函数在下轮重训时自动应用 suggested_weight。
    匹配键：df['match_id']（= matches.match_id，与 source_matches 完全同源，
    无需队名归一）。命中行权重 ×= suggested_weight（默认 1.2）。
    """
    weights = np.ones(len(df)) if base_weights is None else np.asarray(base_weights, dtype=float).copy()

    try:
        from knowledge_base_schema import load_entries
        active = []
        for _lg in ("英超", "西甲", "意甲", "德甲", "法甲", "global"):
            for e in load_entries(_lg, "sample_weights"):
                if e.get("status") == "active" and e.get("suggested_weight"):
                    active.append((_lg, e))
    except Exception as _e:
        print(f"  [L3-WARN] 知识库读取失败，跳过 L3 加权: {_e}")
        return weights

    if not active:
        if verbose:
            print("  [L3] 无 active 条目，样本权重保持基线")
        return weights

    df_ids = df["match_id"].astype(str).values
    for _lg, e in active:
        src = set(e.get("source_matches") or [])
        if not src:
            continue
        _w = float(e["suggested_weight"])
        mask = [str(m) in src for m in df_ids]
        n = int(sum(mask))
        if n:
            weights[mask] *= _w
            print(f"  [L3] {_lg} '{e.get('id')}': {n} 场命中 → 权重×{_w}")
    if verbose:
        nz = int((weights > 0).sum())
        print(f"  [L3] 加权完成: 命中样本 {nz} 场, 平均权重 {weights.mean():.3f}")
    return weights

# === B4: 人工修正场次加权（C-20260908-013，衔接 B4 中期样本权重调整） ===
def apply_human_correction_weights(df, base_weights=None, correction_weight=1.2, verbose=True):
    """读 human_corrections（status=open），按 match_id 对修正场次加权。

    B4 中期层：人工确认的修正场次（如『模型局限性归因』→『阵容异动归因』）
    在重训时以 correction_weight 加权；命中后 mark_correction_applied
    （applied_count+1 → status=applied），幂等降级安全。
    """
    weights = np.ones(len(df)) if base_weights is None else np.asarray(base_weights, dtype=float).copy()

    try:
        from knowledge_base_schema import load_human_corrections, mark_correction_applied
    except Exception as _e:
        print(f"  [CORR-WARN] 知识库读取失败，跳过人工修正加权: {_e}")
        return weights

    open_ids = [str(it.get("match_id")) for it in load_human_corrections()
                if it.get("status") == "open" and it.get("match_id")]
    if not open_ids:
        if verbose:
            print("  [CORR] 无 open 人工修正条目，跳过加权")
        return weights

    df_ids = df["match_id"].astype(str).values
    mask = [m in open_ids for m in df_ids]
    n = int(sum(mask))
    if n:
        weights[mask] *= correction_weight
        print(f"  [CORR] 人工修正场次命中: {n} 场 → 权重×{correction_weight}")
        for mid in df_ids:
            if mid in open_ids:
                mark_correction_applied(mid)
    if verbose:
        print(f"  [CORR] 加权完成: 平均权重 {weights.mean():.3f}")
    return weights

def train_xgboost(X_train, y_train, X_val, y_val, params=None, sample_weights=None,
                  num_boost_round=None, odds_train=None, odds_val=None):
    if xgb is None:
        return None, None
    
    xgb_config = CONFIG.get('model', {}).get('xgboost', {})
    
    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (3 * class_counts)
    print(f"XGBoost - 类别权重: {class_weights}")
    
    if params is None:
        if USE_OPTUNA_BEST_PARAMS:
            # 使用 Optuna 调优最优参数
            params = dict(XGB_OPTUNA_BEST)
            print(f"XGBoost - 使用 Optuna 最优参数 (lr={params['learning_rate']}, "
                  f"max_depth={params['max_depth']})")
        else:
            params = {
                'objective': 'multi:softprob',
                'num_class': 3,
                'eval_metric': 'mlogloss',
                'max_depth': xgb_config.get('max_depth', 2),
                'learning_rate': xgb_config.get('learning_rate', 0.03),
                'subsample': xgb_config.get('subsample', 0.6),
                'colsample_bytree': xgb_config.get('colsample_bytree', 0.6),
                'gamma': xgb_config.get('gamma', 0.5),
                'min_child_weight': xgb_config.get('min_child_weight', 10),
                'reg_alpha': xgb_config.get('reg_alpha', 1.0),
                'reg_lambda': xgb_config.get('reg_lambda', 10.0),
                'seed': 42,
                'nthread': -1
            }
    
    # scale_pos_weight 在多分类中由 sample_weight + class_weight 承担，从 params 移除
    if 'scale_pos_weight' in params:
        del params['scale_pos_weight']
    # nthread 新版 xgb 不接受
    if 'nthread' in params:
        del params['nthread']
    
    if num_boost_round is None:
        num_boost_round = (
            XGB_OPTUNA_NUM_ROUNDS if USE_OPTUNA_BEST_PARAMS
            else xgb_config.get('num_boost_round', 100)
        )
    
    dtrain = xgb.DMatrix(X_train, label=y_train)
    # class_weights（反频率）是平局召回主驱动力；Focal Loss 的 alpha 仅做「额外」平局上调，
    # 不替代 class_weights（P2-13 A/B 发现：删除 class_weights 导致平局召回 0.39→0.27）
    if sample_weights is not None:
        dtrain.set_weight(sample_weights[:len(y_train)] * class_weights[y_train])
    else:
        dtrain.set_weight(class_weights[y_train])
    
    dval = xgb.DMatrix(X_val, label=y_val)
    
    watchlist = [(dtrain, 'train'), (dval, 'val')]
    es_rounds = (5 if USE_OPTUNA_BEST_PARAMS
                 else xgb_config.get('early_stopping_rounds', 15))
    if USE_FOCAL_LOSS:
        configure_focal_loss(gamma=FOCAL_GAMMA, alpha=FOCAL_ALPHA)
        model = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist,
                          early_stopping_rounds=es_rounds, verbose_eval=0,
                          obj=focal_obj_xgb)
    elif USE_EV_LOSS and odds_train is not None:
        # C-20260904-003: EV-policy 目标（CE 锚点 + λ·EVL）。赔率矩阵与训练折行序 1:1 对齐。
        configure_ev_loss(odds_train, lambda_ce=EV_LOSS_LAMBDA_CE, odds_cap=EV_ODDS_CAP)
        model = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist,
                          early_stopping_rounds=es_rounds, verbose_eval=0,
                          obj=ev_obj_xgb)
    elif USE_MOD_LOSS and odds_train is not None:
        # C-20260904-004: 市场赔率蒸馏目标（CE 锚点 + λ·KL(p‖q_market)）。
        configure_mod_loss(odds_train, lambda_mod=MOD_LOSS_LAMBDA, gamma=MOD_LOSS_GAMMA)
        model = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist,
                          early_stopping_rounds=es_rounds, verbose_eval=0,
                          obj=mod_obj_xgb)
    elif USE_PENALTY_LOSS and odds_train is not None:
        # C-20260905-001: 高赔率未命中显式惩罚目标（CE 锚点 + λ·P，对抗高赔率方向过度自信）。
        configure_pen_loss(odds_train, lambda_pen=PENALTY_LOSS_LAMBDA, odds_thresh=PENALTY_ODDS_THRESH)
        model = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist,
                          early_stopping_rounds=es_rounds, verbose_eval=0,
                          obj=pen_obj_xgb)
    else:
        model = xgb.train(params, dtrain, num_boost_round=num_boost_round, evals=watchlist,
                          early_stopping_rounds=es_rounds, verbose_eval=0)

    y_pred = model.predict(dval)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    rps = compute_rps(y_val, y_pred, model_name="XGBoost")  # P0-2: RPS + 监控

    y_pred_train = model.predict(dtrain)
    train_accuracy = accuracy_score(y_train, np.argmax(y_pred_train, axis=1))
    train_ll = log_loss(y_train, y_pred_train)

    print(f"\nXGBoost - Train Accuracy: {train_accuracy:.4f}, Train LogLoss: {train_ll:.4f}")
    print(f"XGBoost - Val Accuracy: {accuracy:.4f}, Val LogLoss: {ll:.4f}, Brier: {brier:.4f}, RPS: {rps:.4f}")

    return model, {'accuracy': accuracy, 'log_loss': ll, 'brier': brier, 'rps': rps,
                   'train_accuracy': train_accuracy, 'train_log_loss': train_ll}

def train_lightgbm(X_train, y_train, X_val, y_val, params=None, sample_weights=None,
                   num_boost_round=None, odds_train=None, odds_val=None):
    if lgb is None:
        return None, None
    
    lgb_config = CONFIG.get('model', {}).get('lightgbm', {})
    
    class_counts = np.bincount(y_train)
    class_weights = len(y_train) / (3 * class_counts)
    print(f"LightGBM - 类别权重: {class_weights}")
    
    if params is None:
        if USE_OPTUNA_BEST_PARAMS:
            params = dict(LGB_OPTUNA_BEST)
            print(f"LightGBM - 使用 Optuna 最优参数 (lr={params['learning_rate']}, "
                  f"max_depth={params['max_depth']}, num_leaves={params['num_leaves']})")
        else:
            params = {
                'objective': 'multiclass',
                'num_class': 3,
                'metric': 'multi_logloss',
                'max_depth': lgb_config.get('max_depth', 2),
                'learning_rate': lgb_config.get('learning_rate', 0.03),
                'num_leaves': lgb_config.get('num_leaves', 8),
                'subsample': lgb_config.get('subsample', 0.6),
                'colsample_bytree': lgb_config.get('colsample_bytree', 0.6),
                'reg_alpha': lgb_config.get('reg_alpha', 1.0),
                'reg_lambda': lgb_config.get('reg_lambda', 10.0),
                'min_child_weight': lgb_config.get('min_child_weight', 10),
                'min_data_in_leaf': lgb_config.get('min_data_in_leaf', 30),
                'seed': 42,
                'verbose': 0
            }
    
    # class_weights（反频率）是平局召回主驱动力；Focal Loss 的 alpha 仅做「额外」平局上调，
    # 不替代 class_weights（P2-13 A/B 发现：删除 class_weights 导致平局召回 0.39→0.27）
    if sample_weights is not None:
        final_weights = sample_weights[:len(y_train)] * class_weights[y_train]
    else:
        final_weights = class_weights[y_train]
    
    lgb_train = lgb.Dataset(X_train, y_train, weight=final_weights)
    lgb_val = lgb.Dataset(X_val, y_val, reference=lgb_train)
    
    if num_boost_round is None:
        num_boost_round = (
            LGB_OPTUNA_NUM_ROUNDS if USE_OPTUNA_BEST_PARAMS
            else lgb_config.get('num_boost_round', 100)
        )
    es_rounds = (5 if USE_OPTUNA_BEST_PARAMS
                 else lgb_config.get('early_stopping_rounds', 15))
    
    callbacks = [lgb.early_stopping(stopping_rounds=es_rounds), lgb.log_evaluation(period=0)]
    
    if USE_FOCAL_LOSS:
        configure_focal_loss(gamma=FOCAL_GAMMA, alpha=FOCAL_ALPHA)
        params['objective'] = focal_fobj_lgb
        params['metric'] = 'None'  # 用 feval 提供的 softmax multi_logloss 作为早停指标
        model = lgb.train(params, lgb_train, num_boost_round=num_boost_round,
                          valid_sets=[lgb_val], callbacks=callbacks, feval=focal_feval_lgb)
    elif USE_EV_LOSS and odds_train is not None:
        # C-20260904-003: EV-policy 目标（CE 锚点 + λ·EVL）。赔率矩阵与训练折行序 1:1 对齐。
        configure_ev_loss(odds_train, lambda_ce=EV_LOSS_LAMBDA_CE, odds_cap=EV_ODDS_CAP)
        params['objective'] = ev_fobj_lgb
        params['metric'] = 'None'  # 用 feval 提供的 softmax multi_logloss 作为早停指标
        model = lgb.train(params, lgb_train, num_boost_round=num_boost_round,
                          valid_sets=[lgb_val], callbacks=callbacks, feval=ev_feval_lgb)
    elif USE_MOD_LOSS and odds_train is not None:
        # C-20260904-004: 市场赔率蒸馏目标（CE 锚点 + λ·KL(p‖q_market)）。
        configure_mod_loss(odds_train, lambda_mod=MOD_LOSS_LAMBDA, gamma=MOD_LOSS_GAMMA)
        params['objective'] = mod_fobj_lgb
        params['metric'] = 'None'  # 用 feval 提供的 softmax multi_logloss 作为早停指标
        model = lgb.train(params, lgb_train, num_boost_round=num_boost_round,
                          valid_sets=[lgb_val], callbacks=callbacks, feval=mod_feval_lgb)
    elif USE_PENALTY_LOSS and odds_train is not None:
        # C-20260905-001: 高赔率未命中显式惩罚目标（CE 锚点 + λ·P，对抗高赔率方向过度自信）。
        configure_pen_loss(odds_train, lambda_pen=PENALTY_LOSS_LAMBDA, odds_thresh=PENALTY_ODDS_THRESH)
        params['objective'] = pen_fobj_lgb
        params['metric'] = 'None'  # 用 feval 提供的 softmax multi_logloss 作为早停指标
        model = lgb.train(params, lgb_train, num_boost_round=num_boost_round,
                          valid_sets=[lgb_val], callbacks=callbacks, feval=pen_feval_lgb)
    else:
        model = lgb.train(params, lgb_train, num_boost_round=num_boost_round,
                          valid_sets=[lgb_val], callbacks=callbacks)
    
    y_pred = _lgb_predict_proba(model, X_val)
    y_pred_class = np.argmax(y_pred, axis=1)
    
    accuracy = accuracy_score(y_val, y_pred_class)
    ll = log_loss(y_val, y_pred)
    brier = brier_score_loss(y_val, y_pred, pos_label=2)
    rps = compute_rps(y_val, y_pred, model_name="LightGBM")  # P0-2: RPS + 监控

    y_pred_train = _lgb_predict_proba(model, X_train)
    train_accuracy = accuracy_score(y_train, np.argmax(y_pred_train, axis=1))
    train_ll = log_loss(y_train, y_pred_train)

    print(f"\nLightGBM - Train Accuracy: {train_accuracy:.4f}, Train LogLoss: {train_ll:.4f}")
    print(f"LightGBM - Val Accuracy: {accuracy:.4f}, Val LogLoss: {ll:.4f}, Brier: {brier:.4f}, RPS: {rps:.4f}")

    return model, {'accuracy': accuracy, 'log_loss': ll, 'brier': brier, 'rps': rps,
                   'train_accuracy': train_accuracy, 'train_log_loss': train_ll}

def apply_draw_threshold(probs, factor=DRAW_THRESHOLD_FACTOR):
    """
    平局决策阈值调整：不修改概率，仅调整分类决策。
    
    保持概率分布不变（sum=1.0），仅在 argmax 时对平局类（class=1）
    应用阈值因子。如果 平局概率 × factor > max(客胜概率, 主胜概率)，
    则预测为平局。
    
    相比 draw_boost 的优势：
    - 概率保持校准（ECE不受影响）
    - 三分类概率和恒为 1.0（无需重归一化）
    - 价值投注 edge 计算不受影响
    """
    pred = np.argmax(probs, axis=1)
    # 平局概率 × factor 超过客胜和主胜时，覆盖为平局
    draw_mask = probs[:, 1] * factor > np.maximum(probs[:, 0], probs[:, 2])
    pred[draw_mask] = 1
    return pred

def fit_platt_scaling(y_true, y_proba):
    calibrated_proba = []
    for class_idx in range(y_proba.shape[1]):
        y_binary = (y_true == class_idx).astype(int)
        X_prob = y_proba[:, class_idx].reshape(-1, 1)
        
        clf = LogisticRegression(solver='lbfgs', max_iter=100)
        try:
            clf.fit(X_prob, y_binary)
            a = float(clf.coef_[0][0])
            b = float(clf.intercept_[0])
        except:
            a = 1.0
            b = 0.0
        
        calibrated_proba.append({'a': a, 'b': b})
    
    return calibrated_proba

def compute_ece(y_true, y_proba, n_bins=10):
    """Expected Calibration Error: 概率校准度量，越低越好"""
    ece = 0.0
    for class_idx in range(y_proba.shape[1]):
        y_binary = (y_true == class_idx).astype(int)
        prob = y_proba[:, class_idx]
        bin_edges = np.linspace(0, 1, n_bins + 1)
        for i in range(n_bins):
            mask = (prob >= bin_edges[i]) & (prob < bin_edges[i + 1])
            if mask.sum() > 0:
                acc = y_binary[mask].mean()
                conf = prob[mask].mean()
                ece += (mask.sum() / len(y_true)) * abs(acc - conf)
    return ece / y_proba.shape[1]

def compute_ece_per_class(y_true, y_proba, n_bins=10):
    """逐类别 ECE"""
    ece_list = []
    for class_idx in range(y_proba.shape[1]):
        y_binary = (y_true == class_idx).astype(int)
        prob = y_proba[:, class_idx]
        bin_edges = np.linspace(0, 1, n_bins + 1)
        ece = 0.0
        for i in range(n_bins):
            mask = (prob >= bin_edges[i]) & (prob < bin_edges[i + 1])
            if mask.sum() > 0:
                acc = y_binary[mask].mean()
                conf = prob[mask].mean()
                ece += (mask.sum() / len(y_true)) * abs(acc - conf)
        ece_list.append(ece)
    return ece_list

def apply_platt_scaling(probs, calibration_params):
    calibrated = np.zeros_like(probs)
    for class_idx in range(probs.shape[1]):
        a = calibration_params[class_idx]['a']
        b = calibration_params[class_idx]['b']
        logit = a * probs[:, class_idx] + b
        calibrated[:, class_idx] = 1 / (1 + np.exp(-logit))
    
    row_sums = calibrated.sum(axis=1, keepdims=True)
    calibrated = calibrated / np.maximum(row_sums, 1e-10)
    
    return calibrated

def convert_xgb_to_js(model):
    if model is None:
        return None
    
    trees = []
    base_score = model.attributes().get('base_score', 0.5)
    
    tree_dump = model.get_dump()
    for i in range(len(tree_dump)):
        tree_str = tree_dump[i]
        nodes = []
        current_node = {}
        
        for line in tree_str.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if line.startswith('0:'):
                if current_node:
                    nodes.append(current_node)
                current_node = {'node_id': 0}
                parts = line[2:].split('[')
                if len(parts) > 1:
                    cond_part = parts[1].split(']')[0]
                    feature, rest = cond_part.split('<')
                    current_node['split'] = {
                        'feature': feature.strip(),
                        'threshold': float(rest.strip())
                    }
                    goto_part = parts[1].split(']')[1]
                    left = int(goto_part.split('yes=')[1].split(',')[0])
                    right = int(goto_part.split('no=')[1].split(',')[0])
                    current_node['split']['left'] = left
                    current_node['split']['right'] = right
                else:
                    current_node['leaf'] = float(line.split(':')[1].split('leaf=')[1].strip())
            else:
                node_id = int(line.split(':')[0])
                if 'leaf=' in line:
                    leaf_val = float(line.split('leaf=')[1].strip())
                    nodes.append({'node_id': node_id, 'leaf': leaf_val})
                else:
                    parts = line.split('[')
                    cond_part = parts[1].split(']')[0]
                    feature, rest = cond_part.split('<')
                    split_info = {
                        'feature': feature.strip(),
                        'threshold': float(rest.strip())
                    }
                    goto_part = parts[1].split(']')[1]
                    left = int(goto_part.split('yes=')[1].split(',')[0])
                    right = int(goto_part.split('no=')[1].split(',')[0])
                    split_info['left'] = left
                    split_info['right'] = right
                    nodes.append({'node_id': node_id, 'split': split_info})
        
        trees.append({'nodes': nodes})
    
    js_model = {
        'base': base_score,
        'lr': 0.1,
        'trees': trees
    }
    
    return js_model

def convert_lgb_to_js(model):
    if model is None:
        return None
    
    trees = []
    base_score = 0.5
    
    tree_info = model.dump_model()
    for tree in tree_info['tree_info']:
        nodes = []
        node_counter = 0
        
        def parse_node(node):
            nonlocal node_counter
            current_id = node_counter
            node_counter += 1
            
            if 'split_index' in node:
                feature_name = tree_info['feature_names'][node['split_index']]
                left_id = node_counter
                right_id = node_counter + 1
                
                nodes.append({
                    'node_id': current_id,
                    'split': {
                        'feature': feature_name,
                        'threshold': node['threshold'],
                        'left': left_id,
                        'right': right_id
                    }
                })
                
                parse_node(node['left_child'])
                parse_node(node['right_child'])
            else:
                leaf_value = node['leaf_value']
                if isinstance(leaf_value, (list, tuple)):
                    leaf_value = leaf_value[0]
                nodes.append({
                    'node_id': current_id,
                    'leaf': leaf_value
                })
        
        parse_node(tree['tree_structure'])
        trees.append({'nodes': nodes})
    
    js_model = {
        'base': base_score,
        'lr': 0.1,
        'trees': trees
    }
    
    return js_model

def save_model_to_js(js_model, model_name):
    output_path = os.path.join(OUTPUT_DIR, f"{model_name.lower()}_model_export.js")
    
    js_content = f"var {model_name.upper()}_MODEL = {json.dumps(js_model, indent=2)};"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print(f"\nSaved {model_name} model to {output_path}")
    return output_path

def save_calibration_params(calibration_params, model_name):
    output_path = os.path.join(OUTPUT_DIR, f"{model_name.lower()}_calibration_params.js")
    
    js_content = f"var {model_name.upper()}_CALIBRATION = {json.dumps(calibration_params, indent=2)};"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print(f"\nSaved {model_name} calibration params to {output_path}")
    return output_path

def evaluate_with_time_series_split(X, y, feature_names, n_splits=5, sample_weights=None):
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    xgb_results = []
    lgb_results = []
    
    print(f"\n{'='*60}")
    print(f"滚动窗口验证 ({n_splits}折时间序列交叉验证)")
    print(f"{'='*60}")
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        print(f"\n--- 第 {fold+1}/{n_splits} 折 ---")
        
        X_train_fold, X_val_fold = X.iloc[train_idx], X.iloc[val_idx]
        y_train_fold, y_val_fold = y.iloc[train_idx], y.iloc[val_idx]
        
        fold_weights = sample_weights[train_idx] if sample_weights is not None else None
        
        print(f"  训练集: {len(X_train_fold)} 场, 验证集: {len(X_val_fold)} 场")
        
        scaler_fold = StandardScaler()
        X_train_scaled = scaler_fold.fit_transform(X_train_fold)
        X_val_scaled = scaler_fold.transform(X_val_fold)
        
        if xgb is not None:
            xgb_model_fold, xgb_metrics_fold = train_xgboost(X_train_scaled, y_train_fold.values,
                                                              X_val_scaled, y_val_fold.values,
                                                              sample_weights=fold_weights)
            if xgb_metrics_fold:
                xgb_results.append(xgb_metrics_fold)
        
        if lgb is not None:
            lgb_model_fold, lgb_metrics_fold = train_lightgbm(X_train_scaled, y_train_fold.values,
                                                               X_val_scaled, y_val_fold.values,
                                                               sample_weights=fold_weights)
            if lgb_metrics_fold:
                lgb_results.append(lgb_metrics_fold)
    
    print(f"\n{'='*60}")
    print("滚动窗口验证汇总")
    print(f"{'='*60}")
    
    if xgb_results:
        print("\nXGBoost 交叉验证结果:")
        acc_list = [r['accuracy'] for r in xgb_results]
        ll_list = [r['log_loss'] for r in xgb_results]
        train_acc_list = [r.get('train_accuracy', r['accuracy']) for r in xgb_results]
        train_ll_list = [r.get('train_log_loss', r['log_loss']) for r in xgb_results]
        print(f"  训练准确率: {np.mean(train_acc_list):.4f} ± {np.std(train_acc_list):.4f}")
        print(f"  训练LogLoss: {np.mean(train_ll_list):.4f} ± {np.std(train_ll_list):.4f}")
        print(f"  验证准确率: {np.mean(acc_list):.4f} ± {np.std(acc_list):.4f}")
        print(f"  验证LogLoss: {np.mean(ll_list):.4f} ± {np.std(ll_list):.4f}")
    
    if lgb_results:
        print("\nLightGBM 交叉验证结果:")
        acc_list = [r['accuracy'] for r in lgb_results]
        ll_list = [r['log_loss'] for r in lgb_results]
        train_acc_list = [r.get('train_accuracy', r['accuracy']) for r in lgb_results]
        train_ll_list = [r.get('train_log_loss', r['log_loss']) for r in lgb_results]
        print(f"  训练准确率: {np.mean(train_acc_list):.4f} ± {np.std(train_acc_list):.4f}")
        print(f"  训练LogLoss: {np.mean(train_ll_list):.4f} ± {np.std(train_ll_list):.4f}")
        print(f"  验证准确率: {np.mean(acc_list):.4f} ± {np.std(acc_list):.4f}")
        print(f"  验证LogLoss: {np.mean(ll_list):.4f} ± {np.std(ll_list):.4f}")
    
    return xgb_results, lgb_results


# ============================================================
# 阶段 A: 统一引擎一致性评估（unified_engine_integration_plan §7 L112-L145）
# λ 反推 + 验证集「引擎 WDL 边际 vs 分类器概率」RPS + 比分矩阵一致性
# ============================================================
def _unified_engine_eval_enabled():
    """阶段 A 评估开关（与 prediction_core._unified_engine_enabled 口径一致）。

    优先级: 环境变量 USE_UNIFIED_ENGINE(1/true/on|0/false/off) > config.yaml unified_engine.enabled。
    enabled=false 时 final_report['unified_engine'] 仅写 enabled:false，不执行引擎评估。
    """
    env = os.environ.get('USE_UNIFIED_ENGINE', '').strip().lower()
    if env in ('1', 'true', 'yes', 'on'):
        return True
    if env in ('0', 'false', 'no', 'off'):
        return False
    try:
        ue = CONFIG.get('unified_engine') or {}
        return bool(ue.get('enabled', False))
    except Exception:
        return False


def _dc_wdl_from_lambda(lambda_home, lambda_away, rho=-0.30, max_goals=7):
    """向量化 Dixon-Coles WDL 边际（与 unified_prediction_engine.DixonColesGenerator 同口径）。

    仅供 λ 反演求解器内部使用：τ 公式/截断与引擎 generate() 完全一致，
    保证 infer_lambda_from_wdl 的「前向模型」与引擎边际一致（口径一致）。
    """
    from scipy.stats import poisson
    k = np.arange(max_goals + 1)
    ph = poisson.pmf(k, lambda_home).reshape(-1, 1)
    pa = poisson.pmf(k, lambda_away).reshape(1, -1)
    m = ph * pa
    # Dixon-Coles τ（论文标准版，[0.1, 3.0] 截断，与引擎 _dc_correction 一致）
    m[0, 0] *= min(3.0, max(0.1, 1.0 - lambda_home * lambda_away * rho))
    m[0, 1] *= min(3.0, max(0.1, 1.0 + lambda_home * rho))
    m[1, 0] *= min(3.0, max(0.1, 1.0 + lambda_away * rho))
    m[1, 1] *= min(3.0, max(0.1, 1.0 - rho))
    total = m.sum()
    if total > 0:
        m /= total
    i, j = np.meshgrid(k, k, indexing='ij')
    win = float(m[i > j].sum())
    draw = float(m[i == j].sum())
    lose = float(m[i < j].sum())
    return win, draw, lose


def infer_lambda_from_wdl(wdl_probs: dict, total_goals: float = 2.8, rho: float = -0.30):
    """从 WDL 概率 + 总进球约束数值反演 λ_home/λ_away。

    约束方程组（unified_engine_integration_plan §7 阶段 A）：
      ① total_goals ≈ λ_home + λ_away
      ② P(win)/P(lose) ≈ 由 λ_home/λ_away 决定的 Poisson 边际比（引擎 Dixon-Coles 口径）
    软约束 ③ P(draw) 拟合（辅助，帮助 RPS 达标）
    正则化: 极小 L2 项向 λ_home=λ_away=total_goals/2 拉拢（仅作多解/退化兜底），
            病态解主要由 least_squares 边界 [0.15, 6.5] 限制。
    求解器: scipy.optimize.least_squares(method='trf', bounds)。

    Args:
        wdl_probs: {'win': p, 'draw': p, 'lose': p}（和≈1）
        total_goals: 总进球约束（默认 2.8）
        rho: Dixon-Coles ρ（默认与引擎 T006_RHO=-0.30 一致；按联赛评估时传联赛 ρ）

    Returns:
        (lambda_home, lambda_away)
    """
    win = float(wdl_probs.get('win', 0.0))
    draw = float(wdl_probs.get('draw', 0.0))
    lose = float(wdl_probs.get('lose', 0.0))
    s = win + draw + lose
    if s <= 0:
        return total_goals / 2.0, total_goals / 2.0
    win, draw, lose = win / s, draw / s, lose / s
    draw = min(max(draw, 0.02), 0.60)
    win = min(max(win, 0.02), 0.90)
    lose = min(max(lose, 0.02), 0.90)
    wl_sum = win + lose
    if wl_sum <= 0.05:  # 退化：概率几乎全在平局 → 对称拆分
        return total_goals / 2.0, total_goals / 2.0

    from scipy.optimize import least_squares
    # 初值：按胜率比例拆分总进球
    frac = float(np.clip(0.5 + 0.5 * (win - lose) / wl_sum, 0.10, 0.90))
    x0 = np.clip(np.array([total_goals * frac, total_goals * (1.0 - frac)]), 0.15, 6.5)
    ratio_ref = win / lose
    draw_ref = draw
    reg = 1e-3

    def resid(x):
        lh, la = float(x[0]), float(x[1])
        w, d, lo = _dc_wdl_from_lambda(lh, la, rho=rho)
        return np.array([
            (lh + la - total_goals) / max(total_goals, 1e-6),          # 约束①
            np.log(max(w, 1e-9) / max(lo, 1e-9)) - np.log(ratio_ref),  # 约束②
            0.5 * (d - draw_ref),                                       # 软约束③
            reg * (lh - total_goals / 2.0) / max(total_goals, 1e-6),   # 正则化(多解兜底)
            reg * (la - total_goals / 2.0) / max(total_goals, 1e-6),
        ])

    try:
        sol = least_squares(resid, x0, bounds=([0.15, 0.15], [6.5, 6.5]),
                            method='trf', max_nfev=200, ftol=1e-10, xtol=1e-10)
        lh, la = float(sol.x[0]), float(sol.x[1])
    except Exception:
        lh, la = float(x0[0]), float(x0[1])
    return min(max(lh, 0.15), 6.5), min(max(la, 0.15), 6.5)


def evaluate_unified_engine(y_true_wdl, y_pred_wdl, df_meta, feature_names):
    """阶段 A: 验证集上量化统一引擎一致性，写入 final_report['unified_engine']。

    流程: ML WDL 概率 → infer_lambda_from_wdl → 引擎 generate → 引擎 wdl 边际，
    计算「引擎边际 vs 分类器概率」RPS（阶段 A 验收 ≤ 0.22）与
    「引擎比分矩阵 vs 现有 ScorePredictor 路径(CalcEngine.poisson_score_predict)」一致性。

    Args:
        y_true_wdl: 真实标签 (n,) 0=客胜/1=平局/2=主胜
        y_pred_wdl: 分类器概率 (n,3) 列序 [客胜, 平局, 主胜]
        df_meta: 验证集元数据 DataFrame（含 competition_name 或 league 列时按联赛取 ρ）
        feature_names: 特征名列表（用于日志/报告）

    Returns:
        dict（结构见计划 L134-141）: {enabled, rho_unified, lambda_source,
        wdl_marginal_vs_classifier_rps, score_consistency, n_evaluated}
        任何异常 → {'enabled': False, 'error': ...}
    """
    try:
        from prediction_core import get_unified_engine
    except Exception as e:
        return {'enabled': False, 'error': f'prediction_core 导入失败: {e}'}

    try:
        y_true = np.asarray(y_true_wdl)
        y_pred = np.asarray(y_pred_wdl, dtype=np.float64)
        if y_true.ndim != 1 or y_pred.ndim != 2 or y_pred.shape[1] != 3:
            return {'enabled': False, 'error': 'y_true/y_pred 形状不合法'}
        n = len(y_true)
        if n < 5:
            return {'enabled': False, 'error': '样本数过少'}
        y_pred = y_pred / np.maximum(y_pred.sum(axis=1, keepdims=True), 1e-9)  # 逐行归一化

        # 联赛 ρ 列（df_meta 含 competition_name/league 时按联赛；否则引擎默认 T006_RHO）
        league_col = None
        if df_meta is not None:
            for c in ('competition_name', 'league'):
                if c in df_meta.columns:
                    league_col = c
                    break

        eng = get_unified_engine()
        engine_marg = np.zeros((n, 3), dtype=np.float64)
        lambda_pairs = []
        n_failed = 0
        for i in range(n):
            league = df_meta[league_col].iloc[i] if league_col else None
            eng.set_league_rho(league)
            rho = eng.rho
            lh, la = infer_lambda_from_wdl(
                {'win': y_pred[i, 2], 'draw': y_pred[i, 1], 'lose': y_pred[i, 0]},
                total_goals=2.8, rho=rho)
            lambda_pairs.append((lh, la))
            try:
                res = eng.generate(lh, la, verbose=False)
                w = res['wdl']
                engine_marg[i] = [w['lose'], w['draw'], w['win']]
            except Exception:
                engine_marg[i] = y_pred[i]  # 引擎失败时退化为分类器概率
                n_failed += 1

        # 引擎边际 vs 分类器概率 RPS（阶段 A 验收指标 ≤ 0.22）
        cum_e = np.cumsum(engine_marg, axis=1)
        cum_c = np.cumsum(y_pred, axis=1)
        rps_vs_clf = float(np.mean(np.sum((cum_e - cum_c) ** 2, axis=1)) / 2.0)
        # 辅助: 引擎边际 vs 真实结果的 RPS
        actual = np.zeros((n, 3))
        for i, yv in enumerate(y_true):
            actual[i, int(yv)] = 1.0
        rps_vs_actual = float(np.mean(np.sum((cum_e - np.cumsum(actual, axis=1)) ** 2, axis=1)) / 2.0)

        # 比分矩阵一致性: 引擎 score_matrix vs 现有 ScorePredictor 路径
        # （CalcEngine.poisson_score_predict，同一 DC τ/max_goals/ρ 下应近似一致）
        # 抽样 seed=42 最多 300 场，度量 = 1 - 0.5*Σ|p_e - p_legacy|（TV 距离补）
        score_consistency = None
        try:
            from prediction_core import CalcEngine
            legacy_fn = CalcEngine.poisson_score_predict
        except Exception:
            legacy_fn = None
        if legacy_fn is not None:
            rng = np.random.default_rng(42)
            idx = rng.choice(n, size=min(n, 300), replace=False)
            cons = []
            for i in idx:
                lh, la = lambda_pairs[i]
                league = df_meta[league_col].iloc[i] if league_col else None
                eng.set_league_rho(league)
                rho = eng.rho
                res = eng.generate(lh, la, verbose=False)
                sm = res['score_matrix']  # (8,8)
                leg = legacy_fn(lh, la, max_goals=7, rho=rho)  # {'h:a': p}
                diff = 0.0
                for h in range(8):
                    for a in range(8):
                        diff += abs(float(sm[h, a]) - leg.get(f"{h}:{a}", 0.0))
                cons.append(1.0 - 0.5 * diff)
            score_consistency = float(np.mean(cons)) if cons else None

        _sc_str = f"{score_consistency:.4f}" if score_consistency is not None else "N/A"
        print(f"[UnifiedEngine-Monitor] 阶段A评估: n={n} "
              f"RPS(引擎边际vs分类器)={rps_vs_clf:.4f} "
              f"RPS(引擎边际vs真实)={rps_vs_actual:.4f} "
              f"score_consistency={_sc_str} 引擎失败={n_failed}")
        print(f"[UnifiedEngine-Monitor] 阶段A评估: 特征维度={len(feature_names) if feature_names is not None else 'N/A'}")

        return {
            'enabled': True,
            'rho_unified': True,
            'lambda_source': 'wdl_infer',
            'wdl_marginal_vs_classifier_rps': round(rps_vs_clf, 4),
            'engine_vs_actual_rps': round(rps_vs_actual, 4),
            'score_consistency': round(score_consistency, 4) if score_consistency is not None else None,
            'n_evaluated': int(n),
            'engine_failures': int(n_failed),
            'total_goals_constraint': 2.8,
            'feature_dim': len(feature_names) if feature_names is not None else None,
        }
    except Exception as e:
        return {'enabled': False, 'error': str(e)}


# ============================================================
# 阶段 B: λ 回归头（unified_engine_integration_plan §7 L148-L158）
# ML 特征 → λ_home/λ_away → Dixon-Coles 引擎 → 四维导出（完整闭环）
# ============================================================

# 阶段 B 超参（对齐项目 Optuna 约束与早停习惯，收紧深度/迭代缓解计划 §9 过拟合风险）
LAMBDA_LR = 0.05             # 学习率（XGB/LGB 对齐，防过拟合保守档）
LAMBDA_MAX_DEPTH = 3         # 收紧深度（WDL Optuna max_depth=4）
LAMBDA_NUM_LEAVES = 31       # LGB 叶子数（对齐 LGB_OPTUNA_BEST 收紧）
LAMBDA_NUM_BOOST_ROUND = 200  # 最大迭代（早停兜底）
LAMBDA_ES_ROUNDS = 20        # 早停轮数（对齐项目 early_stopping_rounds 习惯）
LAMBDA_MIN_GOALS_EPS = 1e-6  # λ 数值下限


def _log_factorial(goals):
    """log(goals!)，0! = 1 → 0。"""
    from scipy.special import gammaln
    return gammaln(np.asarray(goals, dtype=np.float64) + 1.0)


def _poisson_nll(goals, lam):
    """Poisson NLL: λ - goals·logλ + log(goals!)（统一口径）。

    计划 §7 阶段 B 损失定义；验证集指标与训练损失同口径。
    """
    lam = np.clip(np.asarray(lam, dtype=np.float64), LAMBDA_MIN_GOALS_EPS, None)
    goals = np.asarray(goals, dtype=np.float64)
    nll = lam - goals * np.log(lam) + _log_factorial(goals)
    return float(np.mean(nll))


def _odds_lambda_baseline(X, odds_cols=('wdl_implied_win', 'wdl_implied_lose')):
    """赔率 λ 基准（对齐 prediction_core.CalcEngine.calc_lambda_from_odds 同公式）。

    calc_lambda_from_odds: λ_home = NEW_SEASON_FIRST_ROUND_AVG_GOALS(3.6) × 去水隐含主胜概率，
    再 clamp [0.3, 3.5]。训练侧无 odds_data 结构，用特征矩阵中已去水的隐含概率列等价计算。

    Returns:
        (lambda_home_arr, lambda_away_arr)
    """
    avg_goals = 3.6  # NEW_SEASON_FIRST_ROUND_AVG_GOALS（prediction_core 同值）
    if odds_cols[0] in X.columns and odds_cols[1] in X.columns:
        lh = avg_goals * X[odds_cols[0]].to_numpy(dtype=np.float64)
        la = avg_goals * X[odds_cols[1]].to_numpy(dtype=np.float64)
    else:
        # 无赔率特征列时退化为对称基准（不影响「有赔率数据」场景验收）
        lh = np.full(len(X), avg_goals * 0.40)
        la = np.full(len(X), avg_goals * 0.30)
    return np.clip(lh, 0.3, 3.5), np.clip(la, 0.3, 3.5)


def _train_xgb_poisson(X_tr, y_tr, X_va, y_va):
    """单输出 XGBoost Poisson 回归（objective='reg:poisson' = Poisson NLL 原生实现）。

    Returns: (model, base_score, lr)
      base_score = log(mean(y)) 传入训练（JS 推理 λ = exp(base + lr·Σleaf) 对齐）。
    """
    params = {
        'objective': 'count:poisson',  # Poisson NLL 原生实现（config.yaml unified_engine.training.objective 同值）
        'eval_metric': 'poisson-nloglik',
        'max_depth': LAMBDA_MAX_DEPTH,
        'learning_rate': LAMBDA_LR,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'min_child_weight': 10,
        'gamma': 1.0,
        'reg_alpha': 0.1,
        'reg_lambda': 8.0,
        'seed': 42,
        'nthread': -1,
    }
    base_score = float(np.log(max(float(np.mean(y_tr)), 1e-6)))  # JS 侧 base（边际基准 = log(mean)）
    # XGB 3.x count:poisson：base_score 参数按「响应(λ)空间」解释 → 传 mean，内部取 log 得边际基准 log(mean)；
    # dump leaf 已烘焙学习率 → JS 推理 λ = exp(base + 1.0·Σleaf)，不再乘 lr（验收实证，6 位小数吻合）
    params['base_score'] = float(np.exp(base_score))
    dtrain = xgb.DMatrix(X_tr, label=y_tr)
    dval = xgb.DMatrix(X_va, label=y_va)
    model = xgb.train(params, dtrain, num_boost_round=LAMBDA_NUM_BOOST_ROUND,
                      evals=[(dval, 'val')], early_stopping_rounds=LAMBDA_ES_ROUNDS,
                      verbose_eval=0)
    return model, base_score, 1.0


def _train_lgb_poisson(X_tr, y_tr, X_va, y_va):
    """单输出 LightGBM Poisson 回归（objective='poisson' = Poisson NLL 原生实现）。

    Returns: (model, base_score, lr)
      base_score = log(mean(y))（LGB poisson 内部 init_score 同值；JS 推理对齐用）。
      lr=1.0：LGB dump leaf 已含学习率缩放 → JS 推理 λ = exp(base + 1.0·Σleaf)。
    """
    params = {
        'objective': 'poisson',
        'metric': 'poisson',
        'max_depth': LAMBDA_MAX_DEPTH,
        'learning_rate': LAMBDA_LR,
        'num_leaves': LAMBDA_NUM_LEAVES,
        'min_data_in_leaf': 30,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 1,
        'reg_alpha': 0.1,
        'reg_lambda': 8.0,
        'seed': 42,
        'verbose': -1,
    }
    base_score = float(np.log(max(float(np.mean(y_tr)), 1e-6)))
    ltr = lgb.Dataset(X_tr, label=y_tr, init_score=np.full(len(y_tr), base_score))
    lva = lgb.Dataset(X_va, label=y_va, reference=ltr)
    model = lgb.train(params, ltr, num_boost_round=LAMBDA_NUM_BOOST_ROUND,
                      valid_sets=[lva], callbacks=[lgb.early_stopping(stopping_rounds=LAMBDA_ES_ROUNDS)])
    return model, base_score, 1.0


def _fit_lambda_pair(X_tr, yh_tr, ya_tr, X_va, yh_va, ya_va, use_xgb=True, use_lgb=True):
    """训练 (λ_home, λ_away) 的 XGB/LGB 双模型族（共 4 个单输出模型）。

    Returns: dict {'xgb_home': (m,base,lr), 'xgb_away': ..., 'lgb_home': ..., 'lgb_away': ...}
    """
    trained = {}
    if use_xgb and xgb is not None:
        trained['xgb_home'] = _train_xgb_poisson(X_tr, yh_tr, X_va, yh_va)
        trained['xgb_away'] = _train_xgb_poisson(X_tr, ya_tr, X_va, ya_va)
    if use_lgb and lgb is not None:
        trained['lgb_home'] = _train_lgb_poisson(X_tr, yh_tr, X_va, yh_va)
        trained['lgb_away'] = _train_lgb_poisson(X_tr, ya_tr, X_va, ya_va)
    return trained


def _lambda_pair_predict(trained, X_va):
    """多模型平均预测 (λ_home, λ_away)。XGB: λ=exp(base+lr·Σleaf)；LGB: λ=exp(base+Σleaf)。

    模型优先 XGB→LGB（与 WDL 主路径一致），同一族两目标分别平均。
    """
    lh_list, la_list = [], []
    for key in ('xgb_home', 'lgb_home'):
        if key in trained:
            model, base, lr = trained[key]
            if isinstance(model, xgb.core.Booster):
                pred = model.predict(xgb.DMatrix(X_va))
            else:
                pred = model.predict(X_va)
            lh_list.append(np.asarray(pred, dtype=np.float64))
    for key in ('xgb_away', 'lgb_away'):
        if key in trained:
            model, base, lr = trained[key]
            if isinstance(model, xgb.core.Booster):
                pred = model.predict(xgb.DMatrix(X_va))
            else:
                pred = model.predict(X_va)
            la_list.append(np.asarray(pred, dtype=np.float64))
    lh = np.mean(lh_list, axis=0) if lh_list else np.full(len(X_va), 1.5)
    la = np.mean(la_list, axis=0) if la_list else np.full(len(X_va), 1.3)
    return np.clip(lh, 0.15, 6.5), np.clip(la, 0.15, 6.5)


def train_lambda_head(X, df_meta, n_splits=5, train_final=True, validation_split=0.2):
    """阶段 B: λ 回归头训练（unified_engine_integration_plan §7 L148-L158）。

    结构: XGBoost + LightGBM 双模型族 × λ_home/λ_away（4 个单输出 Poisson 回归），
    损失 = Poisson NLL（λ - goals·logλ + log(goals!)，XGB reg:poisson / LGB poisson 原生实现），
    目标 = 每场真实进球数（load_match_data_odds 的 homeGoals/awayGoals）。

    严格时序切分（TimeSeriesSplit(n_splits) 对齐 WDL 5 折）；早停 + 收紧深度/迭代/正则
    （计划 §9 过拟合风险缓解）。

    Args:
        X: 特征 DataFrame（与 WDL 同集，建议传已标准化的 X_scaled_df 与 JS 推理对齐）
        df_meta: 元数据（含 homeGoals/awayGoals/competition_name）
        n_splits: 时序 CV 折数（默认 5，对齐 WDL）
        train_final: 是否在 80/20 时序切分上训练最终导出模型
        validation_split: final 验证集比例（对齐 CONFIG validation_split=0.2）

    Returns:
        dict: {'models': {...4 个最终模型...}, 'metrics': {...}}
    """
    print(f"\n{'='*60}")
    print(f"阶段 B: λ 回归头训练（Poisson NLL, TimeSeriesSplit({n_splits})）")
    print(f"{'='*60}")

    # ---- 输入对齐（X 行序 ↔ df_meta）----
    if X.index is not None and not (X.index == pd.RangeIndex(len(X))).all():
        try:
            meta = df_meta.loc[X.index].reset_index(drop=True)
        except Exception:
            meta = df_meta.iloc[:len(X)].reset_index(drop=True)
    else:
        meta = df_meta.iloc[:len(X)].reset_index(drop=True)
    Xr = X.reset_index(drop=True)
    meta = meta.reset_index(drop=True)

    y_home = meta['homeGoals'].to_numpy(dtype=np.float64)
    y_away = meta['awayGoals'].to_numpy(dtype=np.float64)
    if len(y_home) < n_splits * 10:
        print(f"[UnifiedEngine-Monitor] 阶段B: 样本数 {len(y_home)} 过少，跳过 λ 头训练")
        return {'models': {}, 'metrics': {'enabled': False, 'error': '样本数过少'}}

    # ---- 赔率 λ 基准（全样本，与 calc_lambda_from_odds 同公式）----
    odds_lh, odds_la = _odds_lambda_baseline(Xr)
    odds_nll_h = _poisson_nll(y_home, odds_lh)
    odds_nll_a = _poisson_nll(y_away, odds_la)
    odds_nll = 0.5 * (odds_nll_h + odds_nll_a)
    print(f"[UnifiedEngine-Monitor] 阶段B: 赔率λ基准 NLL(home/away/avg)="
          f"({odds_nll_h:.4f}/{odds_nll_a:.4f}/{odds_nll:.4f})")

    # ---- 时序 CV 评估（每折训练 4 个模型，早停）----
    tscv = TimeSeriesSplit(n_splits=n_splits)
    fold_metrics = []
    val_lh, val_la, val_yh, val_ya = [], [], [], []
    for fold, (tr_idx, va_idx) in enumerate(tscv.split(Xr)):
        X_tr, X_va = Xr.iloc[tr_idx], Xr.iloc[va_idx]
        yh_tr, ya_tr = y_home[tr_idx], y_away[tr_idx]
        yh_va, ya_va = y_home[va_idx], y_away[va_idx]
        if len(va_idx) < 5:
            continue
        trained = _fit_lambda_pair(X_tr.values, yh_tr, ya_tr, X_va.values, yh_va, ya_va)
        lh, la = _lambda_pair_predict(trained, X_va.values)
        val_lh.append(lh)
        val_la.append(la)
        val_yh.append(yh_va)
        val_ya.append(ya_va)
        f_nll_h = _poisson_nll(yh_va, lh)
        f_nll_a = _poisson_nll(ya_va, la)
        fold_metrics.append({
            'fold': fold + 1, 'n_train': len(tr_idx), 'n_val': len(va_idx),
            'poisson_nll_home': round(f_nll_h, 4), 'poisson_nll_away': round(f_nll_a, 4),
            'poisson_nll': round(0.5 * (f_nll_h + f_nll_a), 4),
        })
        print(f"  [UnifiedEngine-Monitor] 折{fold+1}: n_train={len(tr_idx)} n_val={len(va_idx)} "
              f"NLL(home/away)={f_nll_h:.4f}/{f_nll_a:.4f}")

    if not fold_metrics:
        return {'models': {}, 'metrics': {'enabled': False, 'error': '无有效折'}}

    lh_all = np.concatenate(val_lh)
    la_all = np.concatenate(val_la)
    yh_all = np.concatenate(val_yh)
    ya_all = np.concatenate(val_ya)
    nll_h = _poisson_nll(yh_all, lh_all)
    nll_a = _poisson_nll(ya_all, la_all)
    nll = 0.5 * (nll_h + nll_a)
    mae_h = float(np.mean(np.abs(lh_all - yh_all)))
    mae_a = float(np.mean(np.abs(la_all - ya_all)))
    print(f"[UnifiedEngine-Monitor] 阶段B: λ头验证集 NLL(home/away/avg)="
          f"({nll_h:.4f}/{nll_a:.4f}/{nll:.4f})  赔率基准={odds_nll:.4f}  "
          f"改善={odds_nll - nll:+.4f}  n_evaluated={len(yh_all)}")
    print(f"[UnifiedEngine-Monitor] 阶段B: MAE(home/away)={mae_h:.4f}/{mae_a:.4f}")

    # ---- 最终模型（80/20 时序切分，与 WDL 主流程同 split）----
    models = {}
    final_metrics = None
    if train_final:
        train_size = max(1, int(len(Xr) * (1 - validation_split)))
        trained_final = _fit_lambda_pair(
            Xr.iloc[:train_size].values, y_home[:train_size], y_away[:train_size],
            Xr.iloc[train_size:].values, y_home[train_size:], y_away[train_size:])
        models = trained_final
        flh, fla = _lambda_pair_predict(trained_final, Xr.iloc[train_size:].values)
        fnll_h = _poisson_nll(y_home[train_size:], flh)
        fnll_a = _poisson_nll(y_away[train_size:], fla)
        final_metrics = {
            'final_poisson_nll_home': round(fnll_h, 4),
            'final_poisson_nll_away': round(fnll_a, 4),
            'final_poisson_nll': round(0.5 * (fnll_h + fnll_a), 4),
            'final_n_train': int(train_size),
            'final_n_val': int(len(Xr) - train_size),
        }
        print(f"[UnifiedEngine-Monitor] 阶段B: 最终模型(80/20) 验证 NLL(home/away)="
              f"({fnll_h:.4f}/{fnll_a:.4f}) n_train={train_size}")

    metrics = {
        'enabled': True,
        'objective': 'poisson_nll',
        'lambda_source': 'ml_regression',
        'poisson_nll_home': round(nll_h, 4),
        'poisson_nll_away': round(nll_a, 4),
        'poisson_nll': round(nll, 4),
        'odds_baseline_nll_home': round(odds_nll_h, 4),
        'odds_baseline_nll_away': round(odds_nll_a, 4),
        'odds_baseline_nll': round(odds_nll, 4),
        'nll_improvement_vs_odds': round(odds_nll - nll, 4),
        'mae_home': round(mae_h, 4),
        'mae_away': round(mae_a, 4),
        'n_evaluated': int(len(yh_all)),
        'n_splits': int(n_splits),
        'fold_metrics': fold_metrics,
        'feature_dim': int(Xr.shape[1]),
        'train_final': bool(train_final),
        **({'final_model_metrics': final_metrics} if final_metrics else {}),
    }
    return {'models': models, 'metrics': metrics}


def _convert_xgb_poisson_to_js(model, n_trees=None):
    """λ 回归头专用 XGB 导出（修复 convert_xgb_to_js 节点落盘 bug）。

    背景: convert_xgb_to_js 的 root 节点（node_id=0）延迟到下一棵树开始才落盘，
    导致「最后一棵树 root 丢失」与「单叶树（0:leaf，count:poisson 回归常见）整体丢失」，
    JS 端这些树贡献 0（λ 系统性偏小）。此处逐行独立解析、每节点立即落盘。

    n_trees: 可选截断。实证（C-20260909-008）XGB 3.x predict() 默认用「全部树」
    （dump 全部树 == py 实际使用树数），不受 best_iteration 截断 → 导出不传。
    """
    if model is None:
        return None
    trees = []
    dump = model.get_dump()
    if n_trees is not None:
        dump = dump[:n_trees]
    for tree_str in dump:
        nodes = []
        for line in tree_str.split('\n'):
            line = line.strip()
            if not line:
                continue
            node_id = int(line.split(':')[0])
            if 'leaf=' in line:
                nodes.append({'node_id': node_id,
                              'leaf': float(line.split('leaf=')[1].strip())})
            else:
                parts = line.split('[')
                cond_part = parts[1].split(']')[0]
                feature, rest = cond_part.split('<')
                goto_part = parts[1].split(']')[1]
                nodes.append({'node_id': node_id, 'split': {
                    'feature': feature.strip(),
                    'threshold': float(rest.strip()),
                    'left': int(goto_part.split('yes=')[1].split(',')[0]),
                    'right': int(goto_part.split('no=')[1].split(',')[0]),
                }})
        trees.append({'nodes': nodes})
    return trees


def _convert_lgb_poisson_to_js(model, n_trees=None):
    """λ 回归头专用 LGB 导出（修复 convert_lgb_to_js 的特征索引 bug）。

    背景: LGB 4.x 的 dump_model 树节点中 split_index 是节点 DFS 序号、split_feature 才是
    训练数据列索引（int）。convert_lgb_to_js 用 feature_names[split_index] 取特征名，
    导致导出 JS 的 split 全部映射到错误特征（验收实证 diff ~0.3-0.6）。此处直接用
    split_feature 落盘为 'f{idx}'（JS predictTree 经 featureScalerParams.feature_names[idx] 解析）。

    节点编号采用「前序 DFS 连续编号」: 父节点 id=n 时，左子树占 [n+1, n+len(L)]，
    右子树根 id = n+1+len(L)。⚠ 必须先遍历完左子树再取 right_id（node_counter），
    否则当左子树节点数 >1 时 right_id 会指向左子树内部节点（验收实证 diff ~0.2-0.5）。

    n_trees: 可选截断。LGB early stopping 后 booster 已截断为 best_iteration 棵树
    （实证 num_trees==dump 树数==117, predict() 用全部），dump 即 py 实际使用的树 → 不传。
    """
    if model is None:
        return None
    trees = []
    tree_info = model.dump_model()
    info = tree_info['tree_info']
    if n_trees is not None:
        info = info[:n_trees]
    for tree in info:
        nodes = []
        node_counter = 0

        def parse_node(node):
            nonlocal node_counter
            current_id = node_counter
            node_counter += 1
            if 'split_index' in node:
                fidx = int(node['split_feature'])  # 真实列索引（勿用 split_index）
                left_id = node_counter
                parse_node(node['left_child'])
                right_id = node_counter
                parse_node(node['right_child'])
                nodes.append({'node_id': current_id, 'split': {
                    'feature': f'f{fidx}',
                    'threshold': float(node['threshold']),
                    'left': left_id,
                    'right': right_id,
                }})
            else:
                leaf_value = node['leaf_value']
                if isinstance(leaf_value, (list, tuple)):
                    leaf_value = leaf_value[0]
                nodes.append({'node_id': current_id, 'leaf': float(leaf_value)})

        parse_node(tree['tree_structure'])
        trees.append({'nodes': nodes})
    return trees


def convert_lambda_to_js(lambda_models, feature_names):
    """阶段 B: λ 回归头导出为 JS（与 convert_xgb_to_js/convert_lgb_to_js 对齐）。

    产物结构（save_model_to_js → assets/lambda_model_export.js）:
      var LAMBDA_MODEL = {
        "feature_cols": [254 维特征名],   # 与 feature_scaler_params.js 顺序一致
        "models": {
          "xgb_home": {"base": log(mean), "lr": 1.0, "leq": false, "trees": [...]},
          "xgb_away": {...}, "lgb_home": {"base": 0.0, "lr": 1.0, "leq": true, ...}, ...
        },
        "league_rho": {"英超": -0.08, ...}   # set_league_rho 用
      }

    JS 推理约定（与 Python 输出对齐，验收抽样验证）:
      λ = exp(base + 1.0·Σleaf)
      - 树数对齐: XGB dump 全部树 == py predict() 实际使用树数（实证 predict() 用全部树，
        非 best_iteration+1）；LGB early stopping 后 booster 已截断，dump==py 实际树数。均不截断。
      - XGB 3.x count:poisson: base_score 参数按 λ(响应)空间解释（训练侧传 mean），
        dump leaf 已烘焙学习率 → JS 不再乘 lr；base = log(mean)；
        split 条件 = 严格小于（fval < threshold → 左），leq=false。
      - LGB poisson: C-20260909-006 实证 LGB 4.x predict() 不含 init_score
        （raw_score=Σleaf 精确相等）→ base 归零；dump leaf 已含学习率缩放 → JS 不乘 lr；
        split 决策为「<=」（dump decision_type "<="），leq=true。
      λ 统一 clamp [0.15, 6.5]（JS predictLambdaFromModel 与 Python _lambda_pair_predict 同口径）。
    """
    try:
        from unified_prediction_engine import DixonColesGenerator
        league_rho = dict(DixonColesGenerator.LEAGUE_RHO)
    except Exception:
        league_rho = {'英超': -0.08, '西甲': -0.12, '意甲': -0.15, '德甲': -0.05, '法甲': -0.10}

    js_models = {}
    for key, (model, base, lr) in lambda_models.items():
        if model is None:
            continue
        if key.startswith('xgb'):
            # 实证（C-20260909-008）: XGB 3.x predict() 默认用「全部树」（num_boost_round 内全部 dump 树），
            # 不受 best_iteration 截断（predict(best+1) ≠ predict()）。JS 导出全部树与 py 对齐。
            # 用 λ 专用转换（修复 convert_xgb_to_js 单叶树/末树 root 丢失，见 _convert_xgb_poisson_to_js）
            trees = _convert_xgb_poisson_to_js(model)
            # XGB: dump 条件为严格小于（fval < threshold → 左）；base = log(mean)（响应空间解释）
            leq = False
            base_out = float(base)
        else:
            # LGB: early stopping 后 booster 已截断为 best_iteration 棵树（实证 num_trees==dump 树数），
            # dump 即 py predict() 实际使用的全部树 → 不截断。
            # 用 λ 专用转换（修复 convert_lgb_to_js 的 split_index 特征映射 bug + 节点编号 bug，见 _convert_lgb_poisson_to_js）
            trees = _convert_lgb_poisson_to_js(model)
            # C-20260909-006: LGB 4.x 的 predict() 不含 init_score（验收实证 raw=Σleaf, 0.00e+00），
            # JS 侧 base 归零（λ = exp(0 + Σleaf) 与 py 一致）；split 决策为「<=」（dump decision_type "<="）
            leq = True
            base_out = 0.0
        js_models[key] = {'base': base_out, 'lr': 1.0, 'leq': leq, 'trees': trees}

    return {
        'feature_cols': list(feature_names),
        'models': js_models,
        'league_rho': league_rho,
    }


def main(incremental=False, version=None):
    logger = TrainingLogger(f"training_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    
    print("=" * 60)
    print("足球比赛预测模型训练Pipeline" + (" (增量训练)" if incremental else "") + (f" [版本: {version}]" if version else ""))
    print("=" * 60)
    
    # [UnifiedEngine-Monitor] 统一比分引擎(Dixon-Coles)接入状态检查
    print("\n[UnifiedEngine-Monitor] 统一比分引擎(Dixon-Coles)接入状态:")
    print(f"[UnifiedEngine-Monitor]   USE_UNIFIED_ENGINE = {USE_UNIFIED_ENGINE}")
    try:
        from unified_prediction_engine import DixonColesGenerator
        print(f"[UnifiedEngine-Monitor]   引擎模块导入: OK  DixonColesGenerator 可用, 联赛rho={DixonColesGenerator.LEAGUE_RHO}")
    except Exception as e:
        print(f"[UnifiedEngine-Monitor]   引擎模块导入: FAIL  {e}")
    _integrated = "是" if USE_UNIFIED_ENGINE else "否 (开关关闭, 比分预测走 T-005/T-006 独立架构)"
    print(f"[UnifiedEngine-Monitor]   训练流程接入: {_integrated}")

    print("\n1. 加载比赛数据...")
    df = load_match_data_odds()
    print(f"   共加载 {len(df)} 场比赛")
    
    logger.log_data_loading({
        'total_matches': len(df),
        'incremental': incremental,
        'data_source': 'odds.db',
        'date_range': f"{df['date'].min().date()} to {df['date'].max().date()}" if len(df) > 0 else 'N/A'
    })
    
    # P2-15: 自动化数据质量门禁（训练Pipeline前置步骤，pass/warning/fail + 自动告警）
    quality_report = run_quality_gate(df)
    print_quality_gate(quality_report)
    logger.log(
        stage="QUALITY_GATE",
        action="数据质量门禁",
        details={
            'status': quality_report['status'],
            'total_matches': quality_report['total_matches'],
            'critical': quality_report['critical'],
            'warning': quality_report['warning'],
            'stats': quality_report['stats'],
        }
    )
    
    if incremental:
        batch_size = CONFIG.get('training', {}).get('incremental', {}).get('batch_size', 100)
        df = df.tail(batch_size)
        print(f"   增量训练模式，使用最近 {len(df)} 场比赛")
    
    print("\n2. 构建特征...")
    include_odds = CONFIG.get('training', {}).get('include_odds_features', True)
    print(f"   赔率特征集成: {'开启' if include_odds else '关闭'}")
    
    X, y = build_all_features(df, include_odds=include_odds, ts_odds=True, consensus_odds=True)
    
    # 分离特征类型用于日志记录
    basic_features = build_features(df)
    team_features = build_team_features(df)
    team_features = team_features.drop(columns=[col for col in team_features.columns if col in basic_features.columns])
    
    odds_feature_count = X.shape[1] - basic_features.shape[1] - team_features.shape[1]
    
    print(f"   基础特征维度: {basic_features.shape[1]}")
    print(f"   球队特征维度: {team_features.shape[1]}")
    print(f"   赔率特征维度: {odds_feature_count}")
    print(f"   总特征维度: {X.shape[1]}")
    
    logger.log_feature_engineering({
        'basic_features': basic_features.shape[1],
        'team_features': team_features.shape[1],
        'odds_features': odds_feature_count,
        'total_features': X.shape[1],
        'include_odds_features': include_odds,
        'feature_names': X.columns.tolist()[:10] + ['...'] if X.shape[1] > 10 else X.columns.tolist()
    })
    
    print("\n3. 构建异常样本加权...")
    sample_weights = create_sample_weights(df)
    
    print("\n3.5 应用 L3 样本权重（知识库 active 条目，B3 闭环）...")
    sample_weights = apply_l3_sample_weights(df, sample_weights)

    print("\n3.6 应用人工修正场次加权（B4 中期样本权重调整）...")
    sample_weights = apply_human_correction_weights(df, sample_weights)
    
    print("\n4. 滚动窗口验证...")
    xgb_cv_results, lgb_cv_results = evaluate_with_time_series_split(X, y, X.columns.tolist(), n_splits=5, sample_weights=sample_weights)
    
    if xgb_cv_results:
        cv_acc = np.mean([r['accuracy'] for r in xgb_cv_results])
        cv_ll = np.mean([r['log_loss'] for r in xgb_cv_results])
        logger.log_evaluation('滚动窗口验证(XGBoost)', {
            'mean_accuracy': cv_acc,
            'mean_log_loss': cv_ll,
            'n_splits': 5,
            'fold_results': [{k: v for k, v in r.items() if k != 'model'} for r in xgb_cv_results]
        })
    
    print("\n5. 特征标准化...")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    X_scaled_df = pd.DataFrame(X_scaled, columns=X.columns, index=X.index)
    
    print("\n6. 划分训练/测试集 (时间序列分割)...")
    validation_split = CONFIG.get('training', {}).get('validation_split', 0.2)
    train_size = int(len(df) * (1 - validation_split))
    
    if train_size < 10:
        train_size = max(1, int(len(df) * 0.5))
    
    X_train, X_val = X_scaled_df.iloc[:train_size], X_scaled_df.iloc[train_size:]
    y_train, y_val = y.iloc[:train_size], y.iloc[train_size:]
    
    print(f"   训练集: {len(X_train)} 场")
    print(f"   验证集: {len(X_val)} 场")
    print(f"   训练集结果分布: {y_train.value_counts().to_dict()}")
    print(f"   验证集结果分布: {y_val.value_counts().to_dict()}")
    
    logger.log('DATA_SPLIT', '数据集划分完成', details={
        'train_size': len(X_train),
        'val_size': len(X_val),
        'train_distribution': y_train.value_counts().to_dict(),
        'val_distribution': y_val.value_counts().to_dict(),
        'validation_split': validation_split
    })
    
    print("\n7. 训练XGBoost模型...")
    xgb_model, xgb_metrics = train_xgboost(X_train.values, y_train.values, 
                                           X_val.values, y_val.values,
                                           sample_weights=sample_weights)
    
    if xgb_metrics:
        logger.log_model_training(
            model_name='XGBoost',
            params={
                'max_depth': CONFIG.get('model', {}).get('xgboost', {}).get('max_depth', 2),
                'learning_rate': CONFIG.get('model', {}).get('xgboost', {}).get('learning_rate', 0.03),
                'n_estimators': CONFIG.get('model', {}).get('xgboost', {}).get('num_boost_round', 100)
            },
            iteration=CONFIG.get('model', {}).get('xgboost', {}).get('num_boost_round', 100),
            train_loss=xgb_metrics.get('train_log_loss', 0),
            val_loss=xgb_metrics.get('log_loss', 0),
            metrics=xgb_metrics
        )
    
    print("\n8. 训练LightGBM模型...")
    lgb_model, lgb_metrics = train_lightgbm(X_train.values, y_train.values,
                                            X_val.values, y_val.values,
                                            sample_weights=sample_weights)
    
    if lgb_metrics:
        logger.log_model_training(
            model_name='LightGBM',
            params={
                'max_depth': CONFIG.get('model', {}).get('lightgbm', {}).get('max_depth', 2),
                'learning_rate': CONFIG.get('model', {}).get('lightgbm', {}).get('learning_rate', 0.03),
                'n_estimators': CONFIG.get('model', {}).get('lightgbm', {}).get('num_boost_round', 100)
            },
            iteration=CONFIG.get('model', {}).get('lightgbm', {}).get('num_boost_round', 100),
            train_loss=lgb_metrics.get('train_log_loss', 0),
            val_loss=lgb_metrics.get('log_loss', 0),
            metrics=lgb_metrics
        )
    
    print("\n9. 概率校准 (Platt Scaling)...")
    calibration_enabled = CONFIG.get('calibration', {}).get('enabled', True)
    
    xgb_calibration = None
    lgb_calibration = None
    
    if calibration_enabled and xgb_model:
        dtrain = xgb.DMatrix(X_val.values, label=y_val.values)
        xgb_val_probs = xgb_model.predict(dtrain)
        xgb_calibration = fit_platt_scaling(y_val.values, xgb_val_probs)
        calibrated_probs = apply_platt_scaling(xgb_val_probs, xgb_calibration)
        calibrated_acc = accuracy_score(y_val, np.argmax(calibrated_probs, axis=1))
        calibrated_brier = brier_score_loss(y_val, calibrated_probs, pos_label=2)
        print(f"  XGBoost 校准后 - Val Accuracy: {calibrated_acc:.4f}, Brier: {calibrated_brier:.4f}")
        
        logger.log_evaluation('XGBoost校准', {
            'calibrated_accuracy': calibrated_acc,
            'calibrated_brier': calibrated_brier,
            'calibration_enabled': calibration_enabled
        })
    
    if calibration_enabled and lgb_model:
        lgb_val_probs = _lgb_predict_proba(lgb_model, X_val.values)
        lgb_calibration = fit_platt_scaling(y_val.values, lgb_val_probs)
        calibrated_probs = apply_platt_scaling(lgb_val_probs, lgb_calibration)
        calibrated_acc = accuracy_score(y_val, np.argmax(calibrated_probs, axis=1))
        calibrated_brier = brier_score_loss(y_val, calibrated_probs, pos_label=2)
        print(f"  LightGBM 校准后 - Val Accuracy: {calibrated_acc:.4f}, Brier: {calibrated_brier:.4f}")
        
        logger.log_evaluation('LightGBM校准', {
            'calibrated_accuracy': calibrated_acc,
            'calibrated_brier': calibrated_brier,
            'calibration_enabled': calibration_enabled
        })
    
    print(f"\n9.5 平局后处理校准 (自适应精细搜索 + dr>=0.28 约束下最小化准确率损失)...")

    final_report = {
        'timestamp': datetime.now().isoformat(),
        'optuna_params_used': USE_OPTUNA_BEST_PARAMS,
        'xgb_optuna_best': {
            **{k: v for k, v in XGB_OPTUNA_BEST.items()
               if k not in ['nthread', 'scale_pos_weight']},
            'n_estimators': XGB_OPTUNA_NUM_ROUNDS,
        } if USE_OPTUNA_BEST_PARAMS else None,
        'lgb_optuna_best': {
            **LGB_OPTUNA_BEST,
            'n_estimators': LGB_OPTUNA_NUM_ROUNDS,
        } if USE_OPTUNA_BEST_PARAMS else None,
        'draw_calibrator_factor': DRAW_CALIBRATOR_FACTOR,
        'draw_threshold_factor': DRAW_THRESHOLD_FACTOR,
        'validation_split': validation_split,
        'feature_dim': X.shape[1],
        'total_matches': len(df),
        'models': {}
    }

    from sklearn.metrics import classification_report, f1_score, precision_score, recall_score

    # 自适应精细搜索: 在 dr>=DRAW_RECALL_TARGET(0.28) 约束下最大化准确率
    DRAW_RECALL_TARGET = 0.28

    def search_best_draw_calibrator(probs, y_true,
                                    factor_min=0.50, factor_max=1.00, factor_step=0.01,
                                    recall_target=DRAW_RECALL_TARGET):
        """精细搜索 DrawCalibrator factor，优先满足平局召回，其次最大化准确率"""
        best_valid = None  # (factor, accuracy, draw_recall)
        best_any = None    # 兜底：dr 最高的
        for factor in np.arange(factor_min, factor_max + 1e-9, factor_step):
            cal = DrawCalibrator(factor=factor)
            p_cal = cal.calibrate(probs)
            pred = np.argmax(p_cal, axis=1)
            cr = classification_report(y_true, pred, labels=[0, 1, 2],
                                       target_names=['客胜', '平局', '主胜'],
                                       output_dict=True, zero_division=0)
            acc = accuracy_score(y_true, pred)
            dr = cr['平局']['recall']
            if dr >= recall_target:
                if best_valid is None or acc > best_valid[1]:
                    best_valid = (factor, acc, dr)
            if best_any is None or dr > best_any[2]:
                best_any = (factor, acc, dr)
        return best_valid if best_valid is not None else best_any

    def search_best_draw_threshold(probs, y_true,
                                   factor_min=1.00, factor_max=2.50, factor_step=0.05,
                                   recall_target=DRAW_RECALL_TARGET):
        """精细搜索 Threshold factor，优先满足平局召回，其次最大化准确率"""
        best_valid = None
        best_any = None
        for factor in np.arange(factor_min, factor_max + 1e-9, factor_step):
            pred = apply_draw_threshold(probs, factor)
            cr = classification_report(y_true, pred, labels=[0, 1, 2],
                                       target_names=['客胜', '平局', '主胜'],
                                       output_dict=True, zero_division=0)
            acc = accuracy_score(y_true, pred)
            dr = cr['平局']['recall']
            if dr >= recall_target:
                if best_valid is None or acc > best_valid[1]:
                    best_valid = (factor, acc, dr)
            if best_any is None or dr > best_any[2]:
                best_any = (factor, acc, dr)
        return best_valid if best_valid is not None else best_any

    blend_probs = {}
    for model_name, model, val_probs_fn, calibration_data, train_metrics in [
        ("XGBoost", xgb_model, (lambda m, xv: m.predict(xgb.DMatrix(xv)) if m else None),
         (xgb_calibration, 'XGBoost'), xgb_metrics),
        ("LightGBM", lgb_model, (lambda m, xv: _lgb_predict_proba(m, xv) if m else None),
         (lgb_calibration, 'LightGBM'), lgb_metrics),
    ]:
        if model is None:
            continue

        val_probs = val_probs_fn(model, X_val.values)
        calib_data, mname = calibration_data
        if calib_data:
            val_probs = apply_platt_scaling(val_probs, calib_data)
        # P2-14: 保存各模型校准后概率，用于 50/50 blend 分层评估
        blend_probs[model_name] = np.asarray(val_probs).copy()

        # === 自适应精细搜索：Threshold 和 DrawCalibrator 各自最优 ===
        print(f"\n  >> {mname} 自适应搜索：在平局召回率≥{DRAW_RECALL_TARGET}前提下最小化准确率损失...")
        thr_best = search_best_draw_threshold(val_probs, y_val)
        cal_best = search_best_draw_calibrator(val_probs, y_val)

        # Original 基准
        orig_pred = np.argmax(val_probs, axis=1)
        acc_orig = accuracy_score(y_val, orig_pred)
        cr_orig = classification_report(y_val, orig_pred, output_dict=True,
                                        labels=[0, 1, 2],
                                        target_names=['客胜', '平局', '主胜'],
                                        zero_division=0)
        ece_orig = compute_ece(y_val, val_probs, n_bins=10)

        # Threshold (自适应最优)
        thr_factor_opt, acc_thr_opt, dr_thr_opt = thr_best
        thr_pred_opt = apply_draw_threshold(val_probs, thr_factor_opt)
        cr_thr_opt = classification_report(y_val, thr_pred_opt, output_dict=True,
                                           labels=[0, 1, 2],
                                           target_names=['客胜', '平局', '主胜'],
                                           zero_division=0)

        # DrawCalibrator (自适应最优)
        cal_factor_opt, acc_cal_opt, dr_cal_opt = cal_best
        calibrator_opt = DrawCalibrator(factor=cal_factor_opt)
        cal_probs_opt = calibrator_opt.calibrate(val_probs)
        cal_pred_opt = np.argmax(cal_probs_opt, axis=1)
        cr_cal_opt = classification_report(y_val, cal_pred_opt, output_dict=True,
                                           labels=[0, 1, 2],
                                           target_names=['客胜', '平局', '主胜'],
                                           zero_division=0)
        ece_cal_opt = compute_ece(y_val, cal_probs_opt, n_bins=10)
        ll_cal_opt = log_loss(y_val, cal_probs_opt, labels=[0, 1, 2])

        # 最终推荐：draw_recall >= 0.28 约束下选最优
        # 策略：优先 Threshold（不破坏概率 ECE），仅当 DrawCalibrator 准确率
        #       高出 Threshold >= 0.5pp 时才选 DrawCalibrator（补偿 ECE 恶化代价）
        candidates = [
            ('Original', acc_orig, cr_orig['平局']['recall'], orig_pred, 'original', None, None),
            (f'Threshold(F={thr_factor_opt:.2f})*', acc_thr_opt, dr_thr_opt, thr_pred_opt, 'threshold',
             thr_factor_opt, None),
            (f'DrawCalibrator(F={cal_factor_opt:.3f})*', acc_cal_opt, dr_cal_opt, cal_pred_opt, 'calibrator',
             None, cal_factor_opt),
        ]
        valid_cands = [c for c in candidates if c[2] >= DRAW_RECALL_TARGET]
        if valid_cands:
            # 在达标候选中：优先 Threshold，仅当 DrawCal 准确率高出 >=0.5pp 才选它
            valid_threshold = [c for c in valid_cands if c[4] == 'threshold']
            valid_calibrator = [c for c in valid_cands if c[4] == 'calibrator']
            if valid_threshold:
                best = max(valid_threshold, key=lambda x: x[1])
                if valid_calibrator:
                    cal_best = max(valid_calibrator, key=lambda x: x[1])
                    if cal_best[1] - best[1] >= 0.005:  # DrawCal 需高出 >=0.5pp 才选
                        best = cal_best
                best_name, best_acc, best_dr, best_pred, best_method, best_thr_f, best_cal_f = best
            else:
                best_name, best_acc, best_dr, best_pred, best_method, best_thr_f, best_cal_f = max(
                    valid_cands, key=lambda x: x[1])
        else:
            best_name, best_acc, best_dr, best_pred, best_method, best_thr_f, best_cal_f = max(
                candidates, key=lambda x: x[2])

        per_class_final = recall_score(y_val, best_pred, labels=[0, 1, 2], average=None)
        f1_final = f1_score(y_val, best_pred, average='macro')
        best_cr = classification_report(y_val, best_pred, output_dict=True,
                                        labels=[0, 1, 2],
                                        target_names=['客胜', '平局', '主胜'],
                                        zero_division=0)

        train_val_gap = (train_metrics.get('train_accuracy', 0) - best_acc) if train_metrics else 0.0

        overfit_flag = 'X' if train_val_gap > 0.06 else '!' if train_val_gap > 0.03 else 'OK'
        draw_meet_flag = 'OK' if best_dr >= 0.28 else ('!' if best_dr >= 0.20 else 'X')

        print(f"\n  ┌──────────────────────────────────────────────────────────────────────┐")
        print(f"  │  {mname:^71}  │")
        print(f"  ├──────────────────────────────────────────────────────────────────────┤")
        print(f"  │  训练准确率: {train_metrics.get('train_accuracy', 0):.4f}   │ 验证准确率(原始): {acc_orig:.4f}   │")
        print(f"  │  过拟合程度: {train_val_gap:+.4f}   {overfit_flag:<4} (gap>6%=严重)              │")
        print(f"  ├────────── 平局召回率对比 (自适应搜索最优) ─────────────────────────────────┤")
        print(f"  │  {'方法':<42} {'准确率':>8}  {'平局召回':>8}  {'达标':<4}   │")
        for (name, a, dr, pred, method, tf, cf) in candidates:
            mflag = 'OK' if dr >= 0.28 else ('  ' if dr >= 0.20 else 'X')
            sel = '←选' if name == best_name else '   '
            print(f"  │  {name:<42} {a:>8.4f}  {dr:>8.4f}  {mflag:<4} {sel} │")
        print(f"  ├──────────────────────────────────────────────────────────────────────┤")
        print(f"  │  最终推荐: {best_name}")
        print(f"  │  最终准确率: {best_acc:.4f}   │ 平局召回率: {best_dr:.4f} {draw_meet_flag}    │")
        print(f"  │  准确率损失(vs原始): {best_acc - acc_orig:+.4f}   (目标: dr≥0.28下最小)       │")
        print(f"  │  F1(macro):  {f1_final:.4f}   │ 平局精确率: {best_cr['平局']['precision']:.4f}     │")
        print(f"  │  概率校准 ECE: 原始={ece_orig:.4f} │ DrawCal={ece_cal_opt:.4f}                 │")
        print(f"  └──────────────────────────────────────────────────────────────────────┘")
        logger.log_evaluation(f'{mname}_阈值搜索对比', {
            'threshold': {'factor': float(thr_factor_opt), 'accuracy': float(acc_thr_opt), 'draw_recall': float(dr_thr_opt)},
            'calibrator': {'factor': float(cal_factor_opt), 'accuracy': float(acc_cal_opt), 'draw_recall': float(dr_cal_opt)},
            'original': {'accuracy': float(acc_orig), 'draw_recall': float(cr_orig['平局']['recall'])},
            'best': {'label': best_name, 'method': best_method, 'accuracy': float(best_acc), 'draw_recall': float(best_dr)},
        })

        final_report['models'][mname] = {
            'training': {
                'train_accuracy': float(train_metrics.get('train_accuracy', 0)),
                'train_logloss': float(train_metrics.get('train_log_loss', 0)),
                'val_accuracy_original': float(acc_orig),
                'val_logloss_original': float(train_metrics.get('log_loss', 0)),
                'val_rps_original': float(train_metrics.get('rps', 0)),  # P0-2: RPS
                'train_val_accuracy_gap': float(train_val_gap),
                'overfit_level': 'severe' if train_val_gap > 0.06
                else ('moderate' if train_val_gap > 0.03 else 'mild'),
            },
            'adaptive_search': {
                'draw_recall_target': DRAW_RECALL_TARGET,
                'threshold_search': {
                    'factor_min': 1.00, 'factor_max': 2.50, 'factor_step': 0.05,
                    'best_factor': float(thr_factor_opt),
                    'best_accuracy': float(acc_thr_opt),
                    'best_draw_recall': float(dr_thr_opt),
                    'best_draw_precision': float(cr_thr_opt['平局']['precision']),
                },
                'draw_calibrator_search': {
                    'factor_min': 0.50, 'factor_max': 1.00, 'factor_step': 0.01,
                    'best_factor': float(cal_factor_opt),
                    'best_accuracy': float(acc_cal_opt),
                    'best_draw_recall': float(dr_cal_opt),
                    'best_draw_precision': float(cr_cal_opt['平局']['precision']),
                },
            },
            'candidates': {
                method_key: {
                    'label': name,
                    'accuracy': float(a),
                    'draw_recall': float(dr),
                    'draw_precision': float(
                        (classification_report(y_val, pred, output_dict=True,
                                                labels=[0, 1, 2],
                                                target_names=['客胜', '平局', '主胜'],
                                                zero_division=0))['平局']['precision']),
                    'f1_macro': float(f1_score(y_val, pred, average='macro')),
                    'draw_target_028_met': bool(dr >= 0.28),
                    'classification_report': classification_report(
                        y_val, pred, output_dict=True,
                        labels=[0, 1, 2],
                        target_names=['客胜', '平局', '主胜'],
                        zero_division=0),
                    'threshold_factor': float(tf) if tf is not None else None,
                    'calibrator_factor': float(cf) if cf is not None else None,
                }
                for (name, a, dr, pred, method_key, tf, cf) in candidates
            },
            'recommended': {
                'method': best_method,
                'label': best_name,
                'draw_calibrator_factor': float(best_cal_f) if best_cal_f is not None else None,
                'draw_threshold_factor': float(best_thr_f) if best_thr_f is not None else None,
                'accuracy': float(best_acc),
                'draw_recall': float(best_dr),
                'draw_recall_target_028_met': bool(best_dr >= 0.28),
                'draw_precision': float(best_cr['平局']['precision']),
                'f1_macro': float(f1_final),
                'rps': float(train_metrics.get('rps', 0)),  # P0-2: RPS
                'ece_original': float(ece_orig),
                'ece_after_calibration': float(ece_cal_opt),
                'logloss_after_calibration': float(ll_cal_opt),
                'home_recall': float(per_class_final[2]),
                'away_recall': float(per_class_final[0]),
                'accuracy_loss_vs_original_pp': float((best_acc - acc_orig) * 100),
            },
        }
        logger.log_evaluation(f'{mname}平局校准汇总', final_report['models'][mname]['recommended'])

    # P2-14: 分层评估（XGB+LGB 50/50 blend 验证集，6 个维度）
    if len(blend_probs) == 2:
        blend = 0.5 * blend_probs['XGBoost'] + 0.5 * blend_probs['LightGBM']
        df_val = df.iloc[train_size:].reset_index(drop=True)
        odds_cols = [c for c in ['wdl_implied_win', 'wdl_implied_draw', 'wdl_implied_lose',
                                 'wdl_favorite_prob'] if c in X.columns]
        odds_val = X.iloc[train_size:][odds_cols].reset_index(drop=True) if odds_cols else None
        strat_report = compute_stratified_report(df_val, y_val.values, blend, odds_val)
        final_report['stratified_evaluation'] = strat_report
        logger.log_evaluation('分层评估(blend)', {'overall': strat_report['overall']})
        print_stratified_report(strat_report)

    # === 阶段 A: 统一引擎一致性评估（unified_engine_integration_plan §7 L112-L145）===
    # config.yaml unified_engine.enabled=false → final_report 仅写 enabled:false，不执行引擎评估
    if not _unified_engine_eval_enabled():
        final_report['unified_engine'] = {
            'enabled': False,
            'reason': 'config.yaml unified_engine.enabled=false（或环境变量 USE_UNIFIED_ENGINE=0）',
        }
    elif not blend_probs:
        final_report['unified_engine'] = {'enabled': False, 'reason': '验证集无模型概率'}
    else:
        if len(blend_probs) == 1:
            _ue_pred = next(iter(blend_probs.values()))
        else:
            _ue_pred = 0.5 * blend_probs['XGBoost'] + 0.5 * blend_probs['LightGBM']
        _df_val = df.iloc[train_size:].reset_index(drop=True)
        print(f"\n[UnifiedEngine-Monitor] 阶段A: 统一引擎一致性评估（验证集 {len(_df_val)} 场）...")
        final_report['unified_engine'] = evaluate_unified_engine(
            y_val.values, _ue_pred, _df_val, X.columns.tolist())
        _ue = final_report['unified_engine']
        if _ue.get('enabled'):
            print(f"[UnifiedEngine-Monitor] 阶段A完成: RPS(边际vs分类器)="
                  f"{_ue['wdl_marginal_vs_classifier_rps']}, "
                  f"score_consistency={_ue.get('score_consistency')}, "
                  f"n_evaluated={_ue['n_evaluated']}")

    import joblib
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    print(f"\n10. 保存校准器参数与评估报告...")
    calibrator_params_path = os.path.join(OUTPUT_DIR, f'draw_calibrator_params_{timestamp}.json')
    per_model_calibration = {}
    for mname, mdata in final_report['models'].items():
        rec = mdata['recommended']
        per_model_calibration[mname] = {
            'method': rec['method'],
            'label': rec['label'],
            'draw_threshold_factor': rec.get('draw_threshold_factor'),
            'draw_calibrator_factor': rec.get('draw_calibrator_factor'),
            'accuracy': rec['accuracy'],
            'draw_recall': rec['draw_recall'],
            'draw_recall_target_028_met': rec['draw_recall_target_028_met'],
            'accuracy_loss_vs_original_pp': rec.get('accuracy_loss_vs_original_pp'),
        }
    calibrator_params = {
        'method': 'Adaptive hybrid search (Threshold + DrawCalibrator, constrained optimization)',
        'strategy': '优先满足平局召回率≥0.28，再最大化准确率（最小化准确率损失）',
        'draw_recall_target': DRAW_RECALL_TARGET,
        'original_defaults': {
            'draw_threshold_factor_default': DRAW_THRESHOLD_FACTOR,
            'draw_calibrator_factor_default': DRAW_CALIBRATOR_FACTOR,
            'draw_calibrator_factor_conservative': DRAW_CALIBRATOR_CONSERVATIVE,
        },
        'per_model': per_model_calibration,
        'search_details': {
            mname: mdata.get('adaptive_search', {})
            for mname, mdata in final_report['models'].items()
        },
    }
    with open(calibrator_params_path, 'w', encoding='utf-8') as f:
        json.dump(calibrator_params, f, ensure_ascii=False, indent=2)
    print(f"   校准器参数: {calibrator_params_path}")

    report_path = os.path.join(OUTPUT_DIR, f'final_training_report_{timestamp}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(final_report, f, ensure_ascii=False, indent=2)
    print(f"   最终评估报告: {report_path}")

    # === D1: MLflow 实验追踪（可选）===
    # 每个 C- 编号实验对应一条 run：记录 params/metrics/artifacts。
    # mlflow 未安装或记录失败时静默跳过，绝不因追踪失败中断训练流程。
    if MLFLOW_AVAILABLE:
        try:
            run_name = f"train_{timestamp}"
            with mlflow.start_run(run_name=run_name):
                # ---- 参数记录（关键超参数，取自 final_report / 现有变量）----
                mlflow.log_param('experiment', 'D1_MLflow_Tracking')
                # P1-E: 可复现快照绑定（git commit / 数据源版本 / 特征集版本 hash）
                # 复用共享工具 mlflow_repro.py，避免双训练入口口径不一致。
                from mlflow_repro import log_repro_snapshot
                log_repro_snapshot(BASE_DIR, X.columns.tolist(),
                                   artifact_dir=OUTPUT_DIR, artifact_suffix=timestamp)
                mlflow.log_param('version', version if version else 'None')
                mlflow.log_param('incremental', bool(incremental))
                mlflow.log_param('total_matches', int(len(df)))
                mlflow.log_param('feature_dim', int(X.shape[1]))
                mlflow.log_param('validation_split', float(validation_split))
                mlflow.log_param('optuna_params_used', bool(USE_OPTUNA_BEST_PARAMS))
                mlflow.log_param('draw_threshold_factor', float(DRAW_THRESHOLD_FACTOR))
                mlflow.log_param('draw_calibrator_factor', float(DRAW_CALIBRATOR_FACTOR))
                # XGBoost / LightGBM 超参数（final_report 中的 optuna 最优参数）
                for _tag in ('xgb_optuna_best', 'lgb_optuna_best'):
                    _best = final_report.get(_tag)
                    if _best:
                        for _k, _v in _best.items():
                            if isinstance(_v, (int, float, str, bool)):
                                mlflow.log_param(f'{_tag}_{_k}', _v)
                # ---- 指标记录（final_report.recommended：准确率/RPS/LogLoss/平局召回等）----
                for mname, mdata in final_report.get('models', {}).items():
                    rec = mdata.get('recommended', {})
                    for _k in ('accuracy', 'rps', 'draw_recall', 'logloss_after_calibration',
                               'draw_precision', 'f1_macro', 'home_recall', 'away_recall',
                               'ece_original', 'ece_after_calibration'):
                        if rec.get(_k) is not None:
                            mlflow.log_metric(f'{mname}_{_k}', float(rec[_k]))
                # 原始（未校准）验证集指标
                for _tag, _m in (('xgb', xgb_metrics), ('lgb', lgb_metrics)):
                    if _m:
                        mlflow.log_metric(f'{_tag}_val_accuracy', float(_m.get('accuracy', 0)))
                        mlflow.log_metric(f'{_tag}_val_log_loss', float(_m.get('log_loss', 0)))
                        mlflow.log_metric(f'{_tag}_val_brier', float(_m.get('brier', 0)))
                        mlflow.log_metric(f'{_tag}_val_rps', float(_m.get('rps', 0)))
                # ---- 产物记录：最终训练报告 JSON ----
                mlflow.log_artifact(report_path)
                mlflow.set_tag('mlflow.note.content', 'D1: MLflow 实验追踪 - final_training_report')
                print(f"   [MLflow] 实验追踪完成: run={run_name} artifact={report_path}")
        except Exception as e:
            print(f"[MLflow] 实验追踪失败（不影响训练）: {e}")
    
    print("\n10. 保存模型为pkl格式...")
    saved_models = []
    if xgb_model:
        xgb_path = os.path.join(OUTPUT_DIR, f'xgb_model_{timestamp}.pkl')
        joblib.dump(xgb_model, xgb_path)
        print(f"   XGBoost模型: {xgb_path}")
        saved_models.append('XGBoost')
    
    if lgb_model:
        lgb_path = os.path.join(OUTPUT_DIR, f'lgb_model_{timestamp}.pkl')
        joblib.dump(lgb_model, lgb_path)
        print(f"   LightGBM模型: {lgb_path}")
    
    scaler_pkl_path = os.path.join(OUTPUT_DIR, f'scaler_{timestamp}.pkl')
    joblib.dump(scaler, scaler_pkl_path)
    print(f"   标准化器: {scaler_pkl_path}")
    
    features_pkl_path = os.path.join(OUTPUT_DIR, f'selected_features_{timestamp}.pkl')
    joblib.dump(X.columns.tolist(), features_pkl_path)
    print(f"   特征列表: {features_pkl_path}")
    
    print("\n11. 保存特征标准化参数...")
    scaler_params = {
        'mean': scaler.mean_.tolist(),
        'scale': scaler.scale_.tolist(),
        'feature_names': X.columns.tolist()
    }
    scaler_path = os.path.join(OUTPUT_DIR, 'feature_scaler_params.js')
    with open(scaler_path, 'w', encoding='utf-8') as f:
        f.write(f"var FEATURE_SCALER_PARAMS = {json.dumps(scaler_params, indent=2)};")
    print(f"   已保存到 {scaler_path}")
    
    print("\n11. 转换模型为JavaScript格式...")
    if xgb_model:
        xgb_js = convert_xgb_to_js(xgb_model)
        save_model_to_js(xgb_js, 'XGB')
    
    if lgb_model:
        lgb_js = convert_lgb_to_js(lgb_model)
        save_model_to_js(lgb_js, 'LGB')

    # === 阶段 B: λ 回归头训练 + JS 导出（unified_engine_integration_plan §7 L148-L158）===
    # 仅 USE_UNIFIED_ENGINE 开启时执行（避免默认全量训练引入额外耗时/资产）
    if _unified_engine_eval_enabled():
        print("\n[UnifiedEngine-Monitor] 阶段B: λ 回归头训练 + JS 导出...")
        try:
            lambda_result = train_lambda_head(X_scaled_df, df, n_splits=5, train_final=True,
                                              validation_split=validation_split)
            _lm = lambda_result['metrics']
            if _lm.get('enabled'):
                lambda_js = convert_lambda_to_js(lambda_result['models'], X.columns.tolist())
                save_model_to_js(lambda_js, 'LAMBDA')
                final_report['lambda_head'] = _lm
                logger.log_evaluation('λ回归头(阶段B)', _lm)
                print(f"[UnifiedEngine-Monitor] 阶段B完成: λ头NLL={_lm['poisson_nll']} "
                      f"vs 赔率基准={_lm['odds_baseline_nll']} 改善="
                      f"{_lm['nll_improvement_vs_odds']:+.4f} n={_lm['n_evaluated']}")
            else:
                final_report['lambda_head'] = _lm
        except Exception as e:
            import traceback
            traceback.print_exc()
            final_report['lambda_head'] = {'enabled': False, 'error': str(e)}
    
    print("\n12. 保存概率校准参数...")
    if xgb_calibration:
        save_calibration_params(xgb_calibration, 'XGB')
    
    if lgb_calibration:
        save_calibration_params(lgb_calibration, 'LGB')
    
    print("\n" + "=" * 60)
    print("训练完成!")
    print("=" * 60)
    
    if xgb_metrics:
        print(f"\nXGBoost 指标:")
        print(f"  准确率: {xgb_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {xgb_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {xgb_metrics['brier']:.4f}")
    
    if lgb_metrics:
        print(f"\nLightGBM 指标:")
        print(f"  准确率: {lgb_metrics['accuracy']:.4f}")
        print(f"  LogLoss: {lgb_metrics['log_loss']:.4f}")
        print(f"  Brier Score: {lgb_metrics['brier']:.4f}")
    
    print("\n特征重要性:")
    if xgb_model:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 8))
        xgb.plot_importance(xgb_model, ax=ax, max_num_features=15)
        plt.savefig(os.path.join(OUTPUT_DIR, 'xgb_importance.png'))
        plt.close()
        print("  XGBoost: 已保存到 xgb_importance.png")
    
    if lgb_model:
        fig, ax = plt.subplots(figsize=(12, 8))
        lgb.plot_importance(lgb_model, ax=ax, max_num_features=15)
        plt.savefig(os.path.join(OUTPUT_DIR, 'lgb_importance.png'))
        plt.close()
        print("  LightGBM: 已保存到 lgb_importance.png")
    
    # 记录训练完成
    logger.log_training_completion({
        'models_trained': saved_models,
        'best_model': 'XGBoost' if xgb_metrics and (not lgb_metrics or xgb_metrics.get('accuracy', 0) > lgb_metrics.get('accuracy', 0)) else 'LightGBM',
        'best_accuracy': xgb_metrics.get('accuracy', 0) if xgb_metrics else (lgb_metrics.get('accuracy', 0) if lgb_metrics else 0),
        'output_dir': str(OUTPUT_DIR),
        'timestamp': timestamp,
        'total_features': X.shape[1],
        'total_matches': len(df),
        'config': {
            'validation_split': validation_split,
            'incremental': incremental,
            'calibration_enabled': calibration_enabled
        }
    })
    
    print(f"\n训练日志已保存到: {logger.log_file}")
    print("日志摘要:")
    print(logger.get_log_summary())
    
    final_brier = None
    final_accuracy = None
    if xgb_metrics:
        final_brier = xgb_metrics['brier']
        final_accuracy = xgb_metrics['accuracy']
    elif lgb_metrics:
        final_brier = lgb_metrics['brier']
        final_accuracy = lgb_metrics['accuracy']
    
    print(f"\nbrier_score: {final_brier:.4f}" if final_brier else "\nbrier_score: null")
    print(f"accuracy: {final_accuracy:.4f}" if final_accuracy else "accuracy: null")
    print(f"version: {version}" if version else "version: null")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='足球比赛预测模型训练')
    parser.add_argument('--incremental', action='store_true', help='增量训练模式')
    parser.add_argument('--version', type=str, help='模型版本号')
    args = parser.parse_args()
    main(incremental=args.incremental, version=args.version)
