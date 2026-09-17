"""
T-006 0-1球区间独立分类器（P1方向实现）

目标：构建 LightGBM 二分类器预测"小球先验" P(total_goals <= 1)
用于后续集成到 v2/v4 比分预测，提升 0-1 球区间命中率

特征工程依据（来自 t006_lowgoal_feature_analysis.py 分析结果）：
  - 一阶特征 Top-10 AUC: score_implied_total(0.6327), odds_00(0.6324),
    odds_11(0.6177), odds_10(0.6111), prob_draw(0.5951), draw(0.5938),
    avg_draw_score_odds(0.5759), odds_01(0.5731), margin(0.5538), lambda_asymmetry(0.5272)
  - 联赛强信号: 法甲/意甲小球率30%+, 英超/德甲14%以下
  - 关键发现: v2 的 lambda_total 恒=2.5 无方差，是 0-1球区间命中率仅13.2%的根因

特征集（约 22 维）:
  一阶(10): score_implied_total, odds_00/11/10/01, prob_draw, draw,
            avg_draw_score_odds, margin, lambda_asymmetry
  联赛(5): is_ligue1, is_serie_a, is_la_liga, is_premier_league, is_bundesliga
  二阶交互(7): draw/score_implied_total, prob_draw*score_implied_total,
              (1-prob_draw)*lambda_asymmetry, odds_draw_ratio,
              low_score_odds_sum, low_score_prob_sum, draw_minus_lambda_asym

评估:
  - 5折 TimeSeriesSplit（按 match_date 排序防泄露）
  - 指标: AUC, 小球召回率, 小球F1, 整体准确率
  - 阈值优化: 找最大化小球F1的概率阈值
"""
import sqlite3
import pandas as pd
import numpy as np
import os
import logging
import pickle
import warnings
from datetime import datetime
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    roc_auc_score, f1_score, recall_score, precision_score,
    accuracy_score, confusion_matrix, classification_report
)
import lightgbm as lgb

warnings.filterwarnings('ignore')
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
LOG_DIR = BASE_DIR / "logs"
MODEL_DIR = BASE_DIR / "assets"
MIN_CONFIDENCE = 0.85
LEAGUE_AVG_GOALS = 2.5
HOME_ADVANTAGE = 1.10
RANDOM_SEED = 42

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(MODEL_DIR, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_PATH = os.path.join(LOG_DIR, f't006_lowgoal_classifier_{ts}.log')
MODEL_PATH = os.path.join(MODEL_DIR, 't006_lowgoal_classifier_v1.pkl')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding='utf-8')
    ]
)
LOG = logging.getLogger('lowgoal_classifier')


def parse_score(score_str):
    if not score_str or ':' not in str(score_str):
        return None, None
    parts = str(score_str).split(':')
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None, None


def load_data(conn):
    """加载所有高置信比赛 + WDL赔率 + Score赔率 + 日期"""
    LOG.info('[load_data] 加载高置信比赛 + WDL + Score赔率 + match_date...')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.match_id, m.home_team, m.away_team, m.actual_score, m.match_type,
               m.match_date, mm.sh_match_id, mm.confidence
        FROM matches m
        INNER JOIN match_id_mapping mm ON mm.matches_match_id = m.match_id
            AND mm.confidence >= ?
        WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
        GROUP BY m.match_id
    ''', (MIN_CONFIDENCE,))
    matches = []
    for r in cursor.fetchall():
        h, a = parse_score(r[3])
        if h is None:
            continue
        matches.append({
            'match_id': r[0], 'home_team': r[1], 'away_team': r[2],
            'actual_score': r[3], 'league': r[4] or 'Unknown',
            'match_date': r[5], 'sh_match_id': r[6], 'confidence': r[7],
            'home_goals': h, 'away_goals': a, 'total_goals': h + a
        })
    LOG.info(f'[load_data] 加载 {len(matches)} 场')

    # 批量预加载WDL（取最新timestamp）
    sh_ids = [m['sh_match_id'] for m in matches if m['sh_match_id']]
    LOG.info(f'[load_data] 批量加载WDL赔率 {len(sh_ids)} 条...')
    wdl_dict = {}
    if sh_ids:
        placeholders = ','.join('?' * len(sh_ids))
        cursor.execute(f'''
            SELECT match_id, win_a, draw, win_b, timestamp
            FROM wdl_history WHERE match_id IN ({placeholders})
        ''', sh_ids)
        for row in cursor.fetchall():
            mid, wa, d, wb, t = row
            if mid not in wdl_dict or t > wdl_dict[mid]['timestamp']:
                wdl_dict[mid] = {'win_a': wa, 'draw': d, 'win_b': wb, 'timestamp': t}

    # 批量预加载Score赔率（取均值）
    LOG.info(f'[load_data] 批量加载Score赔率...')
    score_dict = {}
    if sh_ids:
        cursor.execute(f'''
            SELECT match_id, score, odds FROM score_history WHERE match_id IN ({placeholders})
        ''', sh_ids)
        tmp = {}
        for mid, score, odds in cursor.fetchall():
            if mid not in tmp:
                tmp[mid] = {}
            if score not in tmp[mid]:
                tmp[mid][score] = []
            tmp[mid][score].append(odds)
        for mid, sd in tmp.items():
            score_dict[mid] = {s: np.mean(o) for s, o in sd.items()}

    # 合并到matches
    for m in matches:
        wdl = wdl_dict.get(m['sh_match_id'])
        m['has_wdl'] = wdl is not None
        if wdl:
            m['win_a'] = wdl['win_a']
            m['draw'] = wdl['draw']
            m['win_b'] = wdl['win_b']
            total_implied = 1.0 / wdl['win_a'] + 1.0 / wdl['draw'] + 1.0 / wdl['win_b']
            m['prob_home'] = (1.0 / wdl['win_a']) / total_implied
            m['prob_draw'] = (1.0 / wdl['draw']) / total_implied
            m['prob_away'] = (1.0 / wdl['win_b']) / total_implied
            m['margin'] = (total_implied - 1.0) * 100
            lh = LEAGUE_AVG_GOALS * m['prob_home'] * HOME_ADVANTAGE
            la = LEAGUE_AVG_GOALS * m['prob_away']
            tl = lh + la
            if tl > 0:
                lh = lh / tl * LEAGUE_AVG_GOALS
                la = la / tl * LEAGUE_AVG_GOALS
            m['lambda_home'] = lh
            m['lambda_away'] = la
            m['lambda_total'] = lh + la
            m['lambda_asymmetry'] = abs(lh - la) / (lh + la) if (lh + la) > 0 else 0
        else:
            m['win_a'] = m['draw'] = m['win_b'] = np.nan
            m['prob_home'] = m['prob_draw'] = m['prob_away'] = np.nan
            m['margin'] = np.nan
            m['lambda_home'] = m['lambda_away'] = m['lambda_total'] = np.nan
            m['lambda_asymmetry'] = np.nan

        sd = score_dict.get(m['sh_match_id'], {})
        m['has_score_odds'] = len(sd) > 0
        if sd:
            implied_total = 0.0
            total_prob = 0.0
            for score, odds_val in sd.items():
                h_s, a_s = parse_score(score)
                if h_s is not None:
                    p = 1.0 / odds_val
                    implied_total += (h_s + a_s) * p
                    total_prob += p
            m['score_implied_total'] = implied_total / total_prob if total_prob > 0 else np.nan
            draw_odds_list = [sd[s] for s in ['0:0', '1:1', '2:2', '3:3'] if s in sd]
            m['avg_draw_score_odds'] = np.mean(draw_odds_list) if draw_odds_list else np.nan
            m['odds_00'] = sd.get('0:0', np.nan)
            m['odds_11'] = sd.get('1:1', np.nan)
            m['odds_10'] = sd.get('1:0', np.nan)
            m['odds_01'] = sd.get('0:1', np.nan)
        else:
            m['score_implied_total'] = np.nan
            m['avg_draw_score_odds'] = np.nan
            m['odds_00'] = m['odds_11'] = m['odds_10'] = m['odds_01'] = np.nan
    return pd.DataFrame(matches)


def build_features(df):
    """构建特征矩阵 + 标签"""
    LOG.info('[build_features] 构建特征矩阵...')
    df = df[df['has_wdl']].copy()
    df = df.dropna(subset=['draw', 'prob_draw']).reset_index(drop=True)
    df['is_lowgoal'] = (df['total_goals'] <= 1).astype(int)
    df['match_date'] = pd.to_datetime(df['match_date'], errors='coerce')
    df = df.dropna(subset=['match_date']).sort_values('match_date').reset_index(drop=True)

    # 联赛 one-hot（按小球率强信号编码）
    df['is_ligue1'] = df['league'].str.contains('法甲', na=False).astype(int)
    df['is_serie_a'] = df['league'].str.contains('意甲', na=False).astype(int)
    df['is_la_liga'] = df['league'].str.contains('西甲', na=False).astype(int)
    df['is_premier_league'] = df['league'].str.contains('英超', na=False).astype(int)
    df['is_bundesliga'] = df['league'].str.contains('德甲', na=False).astype(int)

    # 二阶交互特征
    df['draw_over_implied'] = df['draw'] / df['score_implied_total'].replace(0, np.nan)
    df['prob_draw_x_implied'] = df['prob_draw'] * df['score_implied_total']
    df['prob_nondraw_x_asym'] = (1 - df['prob_draw']) * df['lambda_asymmetry']
    # 归一化平局赔率概率（在三大赔率中的占比）
    df['odds_draw_ratio'] = (1.0 / df['draw']) / (
        1.0 / df['win_a'] + 1.0 / df['draw'] + 1.0 / df['win_b']
    )
    # 低比分赔率聚合（4个核心低比分）
    df['low_score_odds_sum'] = df[['odds_00', 'odds_11', 'odds_10', 'odds_01']].sum(axis=1, min_count=1)
    # 低比分概率聚合（赔率倒数和，反映市场对低比分的总信心）
    df['low_score_prob_sum'] = (
        1.0 / df['odds_00'] + 1.0 / df['odds_11'] +
        1.0 / df['odds_10'] + 1.0 / df['odds_01']
    )
    # 平局赔率 - lambda不对称性（两者都是小球信号，差值放大）
    df['draw_minus_asym'] = df['draw'] - df['lambda_asymmetry'] * 10  # 缩放使量级匹配

    FEATURE_COLS = [
        # 一阶特征 (10)
        'score_implied_total', 'odds_00', 'odds_11', 'odds_10', 'odds_01',
        'prob_draw', 'draw', 'avg_draw_score_odds', 'margin', 'lambda_asymmetry',
        # 联赛 one-hot (5)
        'is_ligue1', 'is_serie_a', 'is_la_liga', 'is_premier_league', 'is_bundesliga',
        # 二阶交互 (7)
        'draw_over_implied', 'prob_draw_x_implied', 'prob_nondraw_x_asym',
        'odds_draw_ratio', 'low_score_odds_sum', 'low_score_prob_sum', 'draw_minus_asym'
    ]
    X = df[FEATURE_COLS].copy()
    y = df['is_lowgoal'].copy()
    dates = df['match_date'].copy()

    # 缺失值处理（score_implied_total 缺失时用 lambda_total 回退，低比分赔率缺失用大值填充）
    X['score_implied_total'] = X['score_implied_total'].fillna(LEAGUE_AVG_GOALS)
    X['avg_draw_score_odds'] = X['avg_draw_score_odds'].fillna(X['draw'] * 5)  # 保守估计
    for col in ['odds_00', 'odds_11', 'odds_10', 'odds_01']:
        X[col] = X[col].fillna(50.0)  # 大赔率=低概率
    X['draw_over_implied'] = X['draw_over_implied'].fillna(X['draw'] / LEAGUE_AVG_GOALS)
    X['prob_draw_x_implied'] = X['prob_draw_x_implied'].fillna(X['prob_draw'] * LEAGUE_AVG_GOALS)
    X['low_score_odds_sum'] = X['low_score_odds_sum'].fillna(200.0)
    X['low_score_prob_sum'] = X['low_score_prob_sum'].fillna(0.08)
    X['draw_minus_asym'] = X['draw_minus_asym'].fillna(X['draw'])

    LOG.info(f'[build_features] 特征矩阵: {X.shape}, 小球样本: {y.sum()}/{len(y)} ({y.mean()*100:.1f}%)')
    LOG.info(f'[build_features] 日期范围: {dates.min().date()} → {dates.max().date()}')
    LOG.info(f'[build_features] 特征列表 ({len(FEATURE_COLS)}维): {FEATURE_COLS}')
    return X, y, dates, FEATURE_COLS, df


def detect_leakage(X, feature_cols):
    """泄露检测：检查是否含赛后统计特征"""
    POST_MATCH_KEYWORDS = [
        'actual_', 'goals', 'xg', 'shots', 'possession', 'corner',
        'yellow', 'red', 'foul', 'save', 'card', 'total_goals'
    ]
    suspicious = []
    for col in feature_cols:
        col_lower = col.lower()
        for kw in POST_MATCH_KEYWORDS:
            if kw in col_lower and 'implied' not in col_lower and 'lambda' not in col_lower:
                suspicious.append((col, kw))
    if suspicious:
        LOG.error(f'❌ 泄露检测失败! 可疑赛后特征: {suspicious}')
        return False
    LOG.info('✅ 泄露检测通过: 所有特征均为赔率/赛前派生特征')
    return True


def train_and_evaluate(X, y, dates, feature_cols):
    """5折 TimeSeriesSplit 训练 + 评估"""
    LOG.info('\n' + '=' * 80)
    LOG.info('【模型训练】LightGBM + 5折 TimeSeriesSplit')
    LOG.info('=' * 80)

    # 泄露检测
    if not detect_leakage(X, feature_cols):
        return None

    # 按日期排序确保 TimeSeriesSplit 时序性
    sort_idx = np.argsort(dates.values)
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)

    tscv = TimeSeriesSplit(n_splits=5)
    oof_preds = np.zeros(len(y_sorted))
    fold_metrics = []
    all_feature_importance = []

    lgb_params = {
        'objective': 'binary',
        'metric': 'auc',
        'boosting_type': 'gbdt',
        'num_leaves': 15,
        'max_depth': 4,
        'learning_rate': 0.05,
        'n_estimators': 200,
        'min_child_samples': 20,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'reg_alpha': 0.1,
        'reg_lambda': 0.5,
        'random_state': RANDOM_SEED,
        'verbose': -1,
        'class_weight': 'balanced',  # 处理类别不平衡（小球22%）
    }

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_sorted), 1):
        X_tr, X_val = X_sorted.iloc[train_idx], X_sorted.iloc[val_idx]
        y_tr, y_val = y_sorted.iloc[train_idx], y_sorted.iloc[val_idx]
        n_low_tr = y_tr.sum()
        n_low_val = y_val.sum()

        model = lgb.LGBMClassifier(**lgb_params)
        model.fit(
            X_tr, y_tr,
            eval_set=[(X_val, y_val)],
            callbacks=[lgb.early_stopping(20, verbose=False)]
        )
        oof_preds[val_idx] = model.predict_proba(X_val)[:, 1]
        fold_auc = roc_auc_score(y_val, oof_preds[val_idx])
        fold_metrics.append({
            'fold': fold,
            'n_train': len(train_idx), 'n_val': len(val_idx),
            'n_low_train': n_low_tr, 'n_low_val': n_low_val,
            'low_rate_train': n_low_tr / len(train_idx),
            'low_rate_val': n_low_val / len(val_idx),
            'auc': fold_auc,
            'best_iteration': model.best_iteration_ or lgb_params['n_estimators']
        })
        all_feature_importance.append(
            pd.Series(model.feature_importances_, index=feature_cols)
        )
        LOG.info(f'  Fold {fold}: train={len(train_idx)}(小球{n_low_tr}, {n_low_tr/len(train_idx)*100:.1f}%) '
                 f'val={len(val_idx)}(小球{n_low_val}, {n_low_val/len(val_idx)*100:.1f}%) '
                 f'AUC={fold_auc:.4f} best_iter={model.best_iteration_}')

    # === 整体 OOF 评估 ===
    LOG.info('\n' + '-' * 80)
    LOG.info('【OOF 整体评估】')
    LOG.info('-' * 80)
    overall_auc = roc_auc_score(y_sorted, oof_preds)
    LOG.info(f'  Overall AUC: {overall_auc:.4f}')

    # 阈值优化：找最大化小球F1的阈值
    LOG.info('\n【阈值优化】寻找最大化小球F1的概率阈值')
    LOG.info(f'{"阈值":<8} | {"准确率":<8} | {"小球P":<8} | {"小球R":<8} | {"小球F1":<8} | {"大球R":<8}')
    LOG.info('-' * 70)
    best_f1 = 0
    best_threshold = 0.5
    for thresh in np.arange(0.20, 0.70, 0.05):
        y_pred = (oof_preds >= thresh).astype(int)
        acc = accuracy_score(y_sorted, y_pred)
        p_low = precision_score(y_sorted, y_pred, pos_label=1, zero_division=0)
        r_low = recall_score(y_sorted, y_pred, pos_label=1, zero_division=0)
        f1_low = f1_score(y_sorted, y_pred, pos_label=1, zero_division=0)
        r_high = recall_score(y_sorted, y_pred, pos_label=0, zero_division=0)
        LOG.info(f'{thresh:<8.2f} | {acc*100:<7.2f}% | {p_low*100:<7.2f}% | {r_low*100:<7.2f}% | {f1_low*100:<7.2f}% | {r_high*100:<7.2f}%')
        if f1_low > best_f1:
            best_f1 = f1_low
            best_threshold = thresh

    LOG.info(f'\n  ✅ 最佳阈值: {best_threshold:.2f} (小球F1={best_f1*100:.2f}%)')

    # 用最佳阈值输出最终评估
    y_pred_best = (oof_preds >= best_threshold).astype(int)
    final_acc = accuracy_score(y_sorted, y_pred_best)
    final_p = precision_score(y_sorted, y_pred_best, pos_label=1, zero_division=0)
    final_r = recall_score(y_sorted, y_pred_best, pos_label=1, zero_division=0)
    final_f1 = f1_score(y_sorted, y_pred_best, pos_label=1, zero_division=0)
    cm = confusion_matrix(y_sorted, y_pred_best)

    LOG.info('\n' + '-' * 80)
    LOG.info(f'【最终评估 @ 阈值={best_threshold:.2f}】')
    LOG.info('-' * 80)
    LOG.info(f'  Overall AUC:        {overall_auc:.4f}')
    LOG.info(f'  整体准确率:          {final_acc*100:.2f}%')
    LOG.info(f'  小球 Precision:     {final_p*100:.2f}%')
    LOG.info(f'  小球 Recall:        {final_r*100:.2f}%')
    LOG.info(f'  小球 F1:            {final_f1*100:.2f}%')
    LOG.info(f'  基线准确率(全猜大球): {(1-y_sorted.mean())*100:.2f}%')
    LOG.info(f'  基线准确率(全猜小球): {y_sorted.mean()*100:.2f}%')
    LOG.info(f'\n  混淆矩阵:')
    LOG.info(f'                预测大球    预测小球')
    LOG.info(f'  实际大球    {cm[0,0]:<10}  {cm[0,1]:<10}')
    LOG.info(f'  实际小球    {cm[1,0]:<10}  {cm[1,1]:<10}')
    LOG.info(f'\n  分类报告:')
    for line in classification_report(y_sorted, y_pred_best, target_names=['大球', '小球']).split('\n'):
        if line.strip():
            LOG.info(f'    {line}')

    # === 特征重要性 ===
    LOG.info('\n' + '-' * 80)
    LOG.info('【特征重要性】(5折平均 gain)')
    LOG.info('-' * 80)
    importance_df = pd.DataFrame(all_feature_importance).mean().sort_values(ascending=False)
    for i, (feat, imp) in enumerate(importance_df.items(), 1):
        LOG.info(f'  {i:<3} {feat:<24} {imp:<10.1f}')

    # === 概率分布分析 ===
    LOG.info('\n' + '-' * 80)
    LOG.info('【概率分箱分析】OOF预测概率 → 实际小球率（校准检查）')
    LOG.info('-' * 80)
    prob_bins = pd.cut(oof_preds, bins=[0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
                       labels=['<0.1', '0.1-0.2', '0.2-0.3', '0.3-0.4', '0.4-0.5',
                               '0.5-0.6', '0.6-0.8', '>=0.8'])
    bin_stats = pd.DataFrame({'prob_bin': prob_bins, 'actual': y_sorted.values})
    bin_agg = bin_stats.groupby('prob_bin').agg(
        n=('actual', 'count'),
        actual_low_rate=('actual', 'mean'),
        avg_pred_prob=('actual', lambda x: np.nan  # placeholder
                      )
    )
    # 重新计算 avg_pred_prob
    bin_pred = pd.DataFrame({'prob_bin': prob_bins, 'pred': oof_preds})
    bin_agg['avg_pred_prob'] = bin_pred.groupby('prob_bin')['pred'].mean()
    LOG.info(f'{"概率区间":<12} | {"场数":<6} | {"实际小球率":<12} | {"平均预测概率":<12} | {"校准差":<10}')
    LOG.info('-' * 70)
    for b, row in bin_agg.iterrows():
        if row['n'] > 0:
            calib_diff = abs(row['actual_low_rate'] - row['avg_pred_prob'])
            LOG.info(f'{str(b):<12} | {int(row["n"]):<6} | {row["actual_low_rate"]*100:<11.2f}% | {row["avg_pred_prob"]*100:<11.2f}% | {calib_diff*100:<9.2f}%')

    # === 保存最终模型（用全量数据训练）===
    LOG.info('\n' + '-' * 80)
    LOG.info('【保存模型】用全量数据训练最终模型')
    LOG.info('-' * 80)
    final_model = lgb.LGBMClassifier(**lgb_params)
    final_model.fit(X_sorted, y_sorted)
    model_bundle = {
        'model': final_model,
        'feature_cols': feature_cols,
        'best_threshold': float(best_threshold),
        'oof_auc': float(overall_auc),
        'oof_f1': float(final_f1),
        'oof_recall': float(final_r),
        'oof_precision': float(final_p),
        'n_samples': int(len(y_sorted)),
        'n_lowgoal': int(y_sorted.sum()),
        'trained_at': datetime.now().isoformat(),
        'version': 'v1',
        'lgb_params': lgb_params
    }
    with open(MODEL_PATH, 'wb') as f:
        pickle.dump(model_bundle, f)
    LOG.info(f'  模型已保存: {MODEL_PATH}')
    LOG.info(f'  最佳阈值: {best_threshold:.4f}')
    LOG.info(f'  OOF AUC: {overall_auc:.4f}')
    LOG.info(f'  OOF 小球F1: {final_f1:.4f}')
    LOG.info(f'  OOF 小球Recall: {final_r:.4f}')

    return {
        'auc': overall_auc, 'f1': final_f1, 'recall': final_r,
        'precision': final_p, 'accuracy': final_acc,
        'threshold': best_threshold, 'fold_metrics': fold_metrics,
        'importance': importance_df, 'oof_preds': oof_preds,
        'y_sorted': y_sorted, 'model_path': MODEL_PATH
    }


def main():
    LOG.info('=' * 80)
    LOG.info('T-006 0-1球区间独立分类器 (P1方向)')
    LOG.info('目标: 构建 LightGBM 二分类器预测 P(total_goals <= 1)')
    LOG.info('=' * 80)
    LOG.info(f'日志路径: {LOG_PATH}')
    LOG.info(f'模型保存路径: {MODEL_PATH}')

    conn = sqlite3.connect(DB_PATH)
    df = load_data(conn)
    conn.close()

    X, y, dates, feature_cols, df_full = build_features(df)
    results = train_and_evaluate(X, y, dates, feature_cols)

    if results:
        LOG.info('\n' + '=' * 80)
        LOG.info('【最终汇总】')
        LOG.info('=' * 80)
        LOG.info(f'  样本量: {len(y)} (小球 {y.sum()}, {y.mean()*100:.1f}%)')
        LOG.info(f'  特征维度: {len(feature_cols)}')
        LOG.info(f'  OOF AUC:        {results["auc"]:.4f}')
        LOG.info(f'  最佳阈值:        {results["threshold"]:.2f}')
        LOG.info(f'  小球 F1:        {results["f1"]*100:.2f}%')
        LOG.info(f'  小球 Recall:    {results["recall"]*100:.2f}%')
        LOG.info(f'  小球 Precision: {results["precision"]*100:.2f}%')
        LOG.info(f'  整体准确率:      {results["accuracy"]*100:.2f}%')
        LOG.info(f'  基线(全猜大球):  {(1-y.mean())*100:.2f}%')
        improvement = results['accuracy'] - (1 - y.mean())
        LOG.info(f'  vs 基线提升:     {improvement*100:+.2f}pp')
        LOG.info(f'\n  模型路径: {results["model_path"]}')
        LOG.info(f'  完整日志: {LOG_PATH}')

        # 集成建议
        LOG.info('\n' + '=' * 80)
        LOG.info('【集成到 v2/v4 的建议】')
        LOG.info('=' * 80)
        LOG.info(f'  1. 加载模型: pickle.load(open("{MODEL_PATH}", "rb"))')
        LOG.info(f'  2. 提取特征: {feature_cols}')
        LOG.info(f'  3. 预测: P(小球) = model.predict_proba(X)[1]')
        LOG.info(f'  4. 集成策略:')
        LOG.info(f'     - 若 P(小球) >= {results["threshold"]:.2f}: 提升低比分(0:0/1:0/0:1/1:1)权重')
        LOG.info(f'     - 否则: 维持 v2 原预测')
        LOG.info(f'  5. 权重调整公式: P_调整(score) = P_v2(score) * (1 + α * (P_小球 - 阈值))')
        LOG.info(f'     其中 α 为集成系数（建议从 0.5 开始调优）')

    LOG.info(f'\n完成! 完整日志: {LOG_PATH}')


if __name__ == '__main__':
    main()
