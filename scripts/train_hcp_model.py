"""
T-005 让球胜平负预测模型训练
============================

基于 handicap_history 赔率数据，训练让球胜平负预测模型。

模型架构:
    3类分类模型: 预测让球结果 (上盘赢/走水/下盘赢)

训练策略:
    - 3折 TimeSeriesSplit CV（按时间排序，样本量242较小）
    - LightGBM + XGBoost 双模型对比
    - 按联赛统计 CV 准确率
    - Elo 特征增强（让球预测与球队实力强相关）

评估指标:
    - 3类分类: Accuracy, Macro-F1, Weighted-F1, 混淆矩阵
    - 按联赛分组准确率

运行: python scripts/train_hcp_model.py
"""

import sys
import os
import logging
import json
import time
from datetime import datetime
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from hcp_features import build_hcp_features, HCP_COLS, HCP_RESULT_NAMES
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    confusion_matrix, classification_report, log_loss
)
import warnings
warnings.filterwarnings('ignore')

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("[WARNING] XGBoost not available")

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False
    print("[WARNING] LightGBM not available")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('TrainHCP')

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'assets')
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'reports')
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


def prepare_data(features: pd.DataFrame):
    """
    准备训练数据: 分离特征和目标变量。

    返回:
        X: 特征矩阵 (仅 hcp_ 前缀列)
        y: 3类分类目标 (0=上盘赢, 1=走水, 2=下盘赢)
        meta: metadata (league, date, home_team, away_team)
    """
    # 特征列
    feature_cols = [c for c in features.columns if c.startswith('hcp_')]
    X = features[feature_cols].copy()

    # 填充缺失值
    if X.isnull().any().any():
        print(f"[DATA] 填充缺失值: {X.isnull().sum().sum()} 个")
        X = X.fillna(X.median())

    # 目标变量
    y_raw = features['actual_handicap'].copy()

    # 3类分类目标：支持 '胜'/'平'/'负' 字符串 和 0/1/2 数字两种格式
    label_map = {'胜': 0, '平': 1, '负': 2}
    y = y_raw.map(label_map)
    # 数字标签直接使用（反推标签已是 0/1/2）
    numeric_mask = y.isna() & y_raw.notna()
    if numeric_mask.any():
        y[numeric_mask] = pd.to_numeric(y_raw[numeric_mask], errors='coerce')
    y = y.fillna(-1).astype(int)

    # 元数据
    meta = features[['league', 'date', 'home_team', 'away_team']].copy()
    meta['actual_handicap'] = y_raw

    # 标签来源（如果有）
    if 'label_source' in features.columns:
        meta['label_source'] = features['label_source']
    else:
        meta['label_source'] = 'actual'

    # 过滤无标签样本
    valid_mask = y >= 0
    X = X[valid_mask]
    y = y[valid_mask]
    meta = meta[valid_mask]

    print(f"\n[DATA] 准备数据完成:")
    print(f"    特征维度: {X.shape[1]}")
    print(f"    有效样本: {len(X)}")
    if 'label_source' in meta.columns:
        actual_n = (meta['label_source'] == 'actual').sum()
        inferred_n = (meta['label_source'] == 'inferred').sum()
        print(f"    真标签: {actual_n} 场, 反推标签: {inferred_n} 场")
    print(f"    3类分类分布:")
    for label, name in HCP_RESULT_NAMES.items():
        count = (y == label).sum()
        print(f"      {name} ({label}): {count} ({count/len(y)*100:.1f}%)")

    return X, y, meta


def train_lgb_3class(X_train, y_train, X_val, y_val, num_classes=3, sample_weight=None):
    """训练 LightGBM 3分类模型"""
    if not LGB_AVAILABLE:
        return None, None

    model = lgb.LGBMClassifier(
        objective='multiclass',
        num_class=num_classes,
        max_depth=3,
        learning_rate=0.03,
        n_estimators=150,
        num_leaves=15,
        min_child_samples=10,
        subsample=0.7,
        colsample_bytree=0.7,
        reg_alpha=0.5,
        reg_lambda=0.5,
        random_state=42,
        verbose=-1,
        force_col_wise=True,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        eval_metric='multi_logloss',
        callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)]
    )

    y_pred = model.predict(X_val)
    acc = accuracy_score(y_val, y_pred)

    return model, acc


def train_xgb_3class(X_train, y_train, X_val, y_val, num_classes=3):
    """训练 XGBoost 3分类模型"""
    if not XGB_AVAILABLE:
        return None, None

    model = xgb.XGBClassifier(
        objective='multi:softmax',
        num_class=num_classes,
        max_depth=3,
        learning_rate=0.03,
        n_estimators=150,
        subsample=0.7,
        colsample_bytree=0.7,
        reg_alpha=0.5,
        reg_lambda=1.0,
        random_state=42,
        verbosity=0,
        use_label_encoder=False,
    )

    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        verbose=False
    )

    y_pred = model.predict(X_val)
    acc = accuracy_score(y_val, y_pred)

    return model, acc


def add_elo_features_hcp(X, meta):
    """添加 Elo 评分特征（10维），让球预测与球队实力强相关"""
    try:
        from elo_rating import build_elo_features

        elo_input = pd.DataFrame({
            'home_team_name': meta['home_team'].values,
            'away_team_name': meta['away_team'].values,
            'date': meta['date'].values,
        })
        elo_input.index = X.index

        import sqlite3
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
        conn = sqlite3.connect(db_path)
        scores_df = pd.read_sql("SELECT match_id, actual_score FROM matches WHERE actual_score IS NOT NULL", conn)
        conn.close()

        def parse_score(score_str):
            try:
                s = str(score_str).strip()
                if '其它' in s:
                    return 0, 0
                if ':' in s:
                    parts = s.split(':')
                    return int(parts[0]), int(parts[1])
                if '-' in s:
                    parts = s.split('-')
                    return int(parts[0]), int(parts[1])
                return 0, 0
            except:
                return 0, 0

        score_map = {}
        for _, row in scores_df.iterrows():
            hg, ag = parse_score(row['actual_score'])
            score_map[row['match_id']] = (hg, ag)

        elo_input['homeGoals'] = [score_map.get(mid, (0, 0))[0] for mid in X.index]
        elo_input['awayGoals'] = [score_map.get(mid, (0, 0))[1] for mid in X.index]

        elo_df = build_elo_features(elo_input)
        elo_cols = [c for c in elo_df.columns]

        for col in elo_cols:
            X[col] = elo_df[col].values

        X[elo_cols] = X[elo_cols].fillna(X[elo_cols].median())
        print(f"[ELO] 添加 Elo 特征: {len(elo_cols)} 维")
        return X, elo_cols
    except Exception as e:
        print(f"[ELO] 无法添加 Elo 特征: {e}")
        import traceback; traceback.print_exc()
    return X, []


def cross_validate_3class(X, y, meta, n_splits=3):
    """
    3类分类 TimeSeriesSplit CV（用3折因为样本量242较小）。
    """
    tscv = TimeSeriesSplit(n_splits=n_splits)

    # 按日期排序
    sort_idx = meta['date'].argsort()
    X_sorted = X.iloc[sort_idx].reset_index(drop=True)
    y_sorted = y.iloc[sort_idx].reset_index(drop=True)
    meta_sorted = meta.iloc[sort_idx].reset_index(drop=True)

    results = {
        'lgb': {'accuracy': [], 'f1_macro': [], 'f1_weighted': []},
        'xgb': {'accuracy': [], 'f1_macro': [], 'f1_weighted': []},
        'by_league': [],
    }

    all_y_true = []
    all_y_pred_lgb = []
    all_y_pred_xgb = []

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X_sorted)):
        X_train, X_val = X_sorted.iloc[train_idx], X_sorted.iloc[val_idx]
        y_train, y_val = y_sorted.iloc[train_idx], y_sorted.iloc[val_idx]
        meta_val = meta_sorted.iloc[val_idx]

        print(f"\n  Fold {fold+1}/{n_splits}: train={len(X_train)}, val={len(X_val)}")
        print(f"    训练集日期: {meta_sorted.iloc[train_idx]['date'].min()} ~ {meta_sorted.iloc[train_idx]['date'].max()}")
        print(f"    验证集日期: {meta_val['date'].min()} ~ {meta_val['date'].max()}")
        print(f"    验证集分布: 上盘赢={(y_val==0).sum()}, 走水={(y_val==1).sum()}, 下盘赢={(y_val==2).sum()}")

        # LGB
        lgb_model, lgb_acc = train_lgb_3class(X_train, y_train, X_val, y_val)
        if lgb_model is not None:
            lgb_pred = lgb_model.predict(X_val)
            results['lgb']['accuracy'].append(accuracy_score(y_val, lgb_pred))
            results['lgb']['f1_macro'].append(f1_score(y_val, lgb_pred, average='macro'))
            results['lgb']['f1_weighted'].append(f1_score(y_val, lgb_pred, average='weighted'))
            all_y_pred_lgb.extend(lgb_pred)
            print(f"    LGB: acc={results['lgb']['accuracy'][-1]:.4f}, f1_macro={results['lgb']['f1_macro'][-1]:.4f}")

        # XGB
        xgb_model, xgb_acc = train_xgb_3class(X_train, y_train, X_val, y_val)
        if xgb_model is not None:
            xgb_pred = xgb_model.predict(X_val)
            results['xgb']['accuracy'].append(accuracy_score(y_val, xgb_pred))
            results['xgb']['f1_macro'].append(f1_score(y_val, xgb_pred, average='macro'))
            results['xgb']['f1_weighted'].append(f1_score(y_val, xgb_pred, average='weighted'))
            all_y_pred_xgb.extend(xgb_pred)
            print(f"    XGB: acc={results['xgb']['accuracy'][-1]:.4f}, f1_macro={results['xgb']['f1_macro'][-1]:.4f}")

        all_y_true.extend(y_val)

        # 按联赛统计
        for league in meta_val['league'].unique():
            league_mask = meta_val['league'].values == league
            if league_mask.sum() >= 5:
                league_acc_lgb = accuracy_score(
                    y_val[league_mask],
                    lgb_model.predict(X_val.iloc[league_mask])
                ) if lgb_model is not None else None
                league_acc_xgb = accuracy_score(
                    y_val[league_mask],
                    xgb_model.predict(X_val.iloc[league_mask])
                ) if xgb_model is not None else None
                results['by_league'].append({
                    'fold': fold + 1,
                    'league': league,
                    'n': int(league_mask.sum()),
                    'lgb_acc': league_acc_lgb,
                    'xgb_acc': league_acc_xgb,
                })

    # 汇总
    print(f"\n{'='*60}")
    print(f"3类分类 CV 汇总 (上盘赢/走水/下盘赢, n_splits={n_splits})")
    print(f"{'='*60}")

    for model_name in ['lgb', 'xgb']:
        if results[model_name]['accuracy']:
            accs = results[model_name]['accuracy']
            f1s = results[model_name]['f1_macro']
            f1ws = results[model_name]['f1_weighted']
            print(f"  {model_name.upper()}:")
            print(f"    Accuracy:     {np.mean(accs):.4f} ± {np.std(accs):.4f}")
            print(f"    F1 Macro:     {np.mean(f1s):.4f} ± {np.std(f1s):.4f}")
            print(f"    F1 Weighted:  {np.mean(f1ws):.4f} ± {np.std(f1ws):.4f}")

    # 混淆矩阵（LGB）
    if all_y_pred_lgb:
        cm = confusion_matrix(all_y_true, all_y_pred_lgb)
        print(f"\n  LGB 混淆矩阵 (汇总):")
        labels = ['上盘赢', '走水', '下盘赢']
        header = '        ' + '  '.join([f'{l:>5s}' for l in labels])
        print(header)
        for i, row in enumerate(cm):
            print(f"  {labels[i]:>5s}  " + '  '.join([f'{v:5d}' for v in row]))

    return results


def generate_report(results, features, elo_dim=0, output_path=None):
    """生成 T-005 性能报告"""
    if output_path is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_path = os.path.join(REPORT_DIR, f'hcp_model_report_{timestamp}.md')

    lines = []
    lines.append(f"# T-005 让球胜平负预测模型 — 性能报告")
    lines.append(f"")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据源: odds.db → handicap_history → match_id_mapping → matches")
    lines.append(f"> 样本量: {len(features[features['actual_handicap'].notna()])} 场（有实际让球结果）")
    lines.append(f"")

    # 数据概览
    valid = features[features['actual_handicap'].notna()]
    lines.append(f"## 1. 数据概览")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|----|")
    lines.append(f"| 总比赛数（有让球赔率） | {len(features)} |")
    lines.append(f"| 有效样本（有实际让球结果） | {len(valid)} |")
    lines.append(f"| 特征维度 | {len([c for c in features.columns if c.startswith('hcp_')])} + Elo({elo_dim}) |")
    lines.append(f"| 上盘赢比例 | {(valid['actual_handicap']==0).sum()/len(valid)*100:.1f}% |")
    lines.append(f"| 走水比例 | {(valid['actual_handicap']==1).sum()/len(valid)*100:.1f}% |")
    lines.append(f"| 下盘赢比例 | {(valid['actual_handicap']==2).sum()/len(valid)*100:.1f}% |")
    lines.append(f"")

    # 联赛分布
    lines.append(f"### 1.1 联赛分布")
    lines.append(f"")
    lines.append(f"| 联赛 | 比赛数 |")
    lines.append(f"|------|--------|")
    for league, count in features['league'].value_counts().items():
        lines.append(f"| {league} | {count} |")
    lines.append(f"")

    # 3类分类结果
    lines.append(f"## 2. 3类分类模型（让球胜平负预测）")
    lines.append(f"")
    lines.append(f"| 模型 | Accuracy | F1 Macro | F1 Weighted |")
    lines.append(f"|------|----------|----------|-------------|")
    for model_name in ['lgb', 'xgb']:
        if results[model_name]['accuracy']:
            acc = np.mean(results[model_name]['accuracy'])
            acc_std = np.std(results[model_name]['accuracy'])
            f1m = np.mean(results[model_name]['f1_macro'])
            f1m_std = np.std(results[model_name]['f1_macro'])
            f1w = np.mean(results[model_name]['f1_weighted'])
            f1w_std = np.std(results[model_name]['f1_weighted'])
            lines.append(f"| {model_name.upper()} | {acc:.4f} ± {acc_std:.4f} | {f1m:.4f} ± {f1m_std:.4f} | {f1w:.4f} ± {f1w_std:.4f} |")
    lines.append(f"")

    # 按联赛结果
    if results.get('by_league'):
        lines.append(f"## 3. 按联赛 CV 准确率")
        lines.append(f"")
        lines.append(f"| 联赛 | LGB Acc | XGB Acc |")
        lines.append(f"|------|---------|---------|")
        league_stats = {}
        for entry in results['by_league']:
            league = entry['league']
            if league not in league_stats:
                league_stats[league] = {'lgb': [], 'xgb': [], 'n': entry['n']}
            if entry['lgb_acc'] is not None:
                league_stats[league]['lgb'].append(entry['lgb_acc'])
            if entry['xgb_acc'] is not None:
                league_stats[league]['xgb'].append(entry['xgb_acc'])

        for league, stats in sorted(league_stats.items()):
            lgb_str = f"{np.mean(stats['lgb']):.4f}" if stats['lgb'] else "N/A"
            xgb_str = f"{np.mean(stats['xgb']):.4f}" if stats['xgb'] else "N/A"
            lines.append(f"| {league} | {lgb_str} | {xgb_str} |")
    lines.append(f"")

    # 验收标准
    lines.append(f"## 4. 验收标准")
    lines.append(f"")
    lgb_acc = np.mean(results['lgb']['accuracy']) if results['lgb']['accuracy'] else 0

    lines.append(f"| 指标 | 目标 | 实际 | 状态 |")
    lines.append(f"|------|------|------|------|")
    lines.append(f"| 3类分类准确率 (LGB) | ≥ 45% | {lgb_acc*100:.1f}% | {'✅' if lgb_acc >= 0.45 else '❌'} |")
    lines.append(f"")

    # 特征重要性
    lines.append(f"## 5. 特征列表")
    lines.append(f"")
    hcp_cols = [c for c in features.columns if c.startswith('hcp_')]
    lines.append(f"| # | 特征名 | 描述 |")
    lines.append(f"|---|--------|------|")
    feature_descriptions = {
        'hcp_prob_win': '上盘赢概率', 'hcp_prob_draw': '走水概率', 'hcp_prob_lose': '下盘赢概率',
        'hcp_home_strength': '上盘优势（win-lose）', 'hcp_draw_risk': '走水风险',
        'hcp_confidence': '市场信心（max prob）', 'hcp_entropy': '分布熵',
        'hcp_expected_value': '隐含回报差', 'hcp_volatility': '概率标准差',
        'hcp_market_sentiment': '市场偏好', 'hcp_underdog_ratio': '冷门比',
        'hcp_favorite_margin': '热门边际', 'hcp_balance': '实力均衡度',
        'hcp_upset_risk': '冷门风险', 'hcp_odds_skew': '赔率偏度',
    }
    for i, col in enumerate(hcp_cols):
        desc = feature_descriptions.get(col, '')
        lines.append(f"| {i+1} | `{col}` | {desc} |")
    lines.append(f"")

    # 数据局限性
    lines.append(f"## 6. 数据局限性说明")
    lines.append(f"")
    lines.append(f"- 实际让球结果（actual_handicap）仅覆盖 242 场（4.6%），样本量较小")
    lines.append(f"- handicap_history 赔率数据覆盖 3,929 场，但缺少让球盘口线（handicap_line）用于计算实际结果")
    lines.append(f"- 后续可通过补充盘口线数据扩充训练样本")
    lines.append(f"")

    report = '\n'.join(lines)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n[REPORT] 报告已保存: {output_path}")
    return output_path


def find_latest_expanded_csv() -> str:
    """查找最新的扩充数据集 CSV 文件。"""
    import glob
    pattern = os.path.join(REPORT_DIR, 'hcp_expanded_dataset_*.csv')
    files = sorted(glob.glob(pattern))
    if files:
        return files[-1]
    return None


def merge_expanded_labels(features: pd.DataFrame) -> pd.DataFrame:
    """
    加载扩充数据集 CSV，将反推标签合并到 features 中。

    合并逻辑:
        - 有真标签(actual_handicap='胜'/'平'/'负')的样本 → 保持不变
        - 无真标签但有反推标签的样本 → 用反推标签(0/1/2)填充 actual_handicap
        - 标记 label_source（actual/inferred）
    """
    csv_path = find_latest_expanded_csv()
    if csv_path is None:
        print("[EXPAND] 未找到扩充数据集 CSV，使用原始242场真标签")
        features['label_source'] = 'actual'
        return features

    print(f"[EXPAND] 加载扩充数据集: {os.path.basename(csv_path)}")
    df_expanded = pd.read_csv(csv_path)

    # 去重（保留第一条，CSV 中可能因时序赔率快照导致重复）
    df_expanded = df_expanded.drop_duplicates(subset=['match_id'], keep='first')

    # CSV 的 match_id 对应 features 的 index (matches_match_id)
    expanded_dict = df_expanded.set_index('match_id')[
        ['actual_handicap_pred', 'label_source']
    ].to_dict('index')

    # 将 actual_handicap 列转为 object 类型，以支持混合类型（字符串+整数）
    features['actual_handicap'] = features['actual_handicap'].astype(object)

    # 确保 index 唯一（去重，保留第一条）
    dup_mask = features.index.duplicated(keep='first')
    if dup_mask.any():
        print(f"[EXPAND] 去重: 移除 {dup_mask.sum()} 条重复 index")
        features = features[~dup_mask]

    # 合并标签（使用 .at 访问器获取标量值）
    inferred_count = 0
    actual_count = 0
    label_source_list = []
    for idx in features.index:
        idx_str = str(idx)
        if idx_str in expanded_dict:
            info = expanded_dict[idx_str]
            current_val = features.at[idx, 'actual_handicap']
            # 如果已有真标签，优先使用
            if pd.notna(current_val):
                label_source_list.append('actual')
                actual_count += 1
            else:
                # 使用反推标签
                pred_label = info['actual_handicap_pred']
                if pd.notna(pred_label):
                    features.at[idx, 'actual_handicap'] = int(pred_label)
                    label_source_list.append('inferred')
                    inferred_count += 1
                else:
                    label_source_list.append('none')
        else:
            current_val = features.at[idx, 'actual_handicap']
            if pd.notna(current_val):
                label_source_list.append('actual')
                actual_count += 1
            else:
                label_source_list.append('none')

    features['label_source'] = label_source_list
    print(f"[EXPAND] 合并完成: 真标签={actual_count}, 反推标签={inferred_count}, 无标签={label_source_list.count('none')}")
    return features


def main():
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    print("=" * 70)
    print("🏆 T-005 让球胜平负预测模型训练（路径1 扩充版）")
    print("=" * 70)
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据源: handicap_history → match_id_mapping → matches + 扩充数据集CSV")
    print(f"  路径1: 从赔率反推盘口线，扩充训练样本至 ~3,915 场")
    print("=" * 70)

    # Step 1: 构建特征
    print("\n📊 Step 1: 构建让球赔率特征...")
    t0 = time.time()
    features = build_hcp_features()
    print(f"   耗时: {time.time() - t0:.1f}s")

    # Step 1b: 合并扩充数据集（反推标签）
    print("\n📊 Step 1b: 合并扩充数据集（反推盘口线标签）...")
    features = merge_expanded_labels(features)

    # Step 2: 准备数据
    print("\n🔧 Step 2: 准备训练数据...")
    X, y, meta = prepare_data(features)

    # Step 2.5: 添加 Elo 特征（让球预测与球队实力强相关）
    print("\n🔧 Step 2.5: 添加 Elo 特征增强...")
    X, elo_cols = add_elo_features_hcp(X, meta)
    total_feature_dim = X.shape[1]
    print(f"   增强后总特征维度: {total_feature_dim} (HCP基础=15 + Elo={len(elo_cols)})")

    # Step 3: 3类分类 CV（样本量充足，升级为5折）
    n_splits = 5 if len(X) > 500 else 3
    print(f"\n🎯 Step 3: 3类分类 {n_splits}折 TimeSeriesSplit CV (上盘赢/走水/下盘赢)...")
    t0 = time.time()
    results = cross_validate_3class(X, y, meta, n_splits=n_splits)
    print(f"   耗时: {time.time() - t0:.1f}s")

    # Step 4: 生成报告
    print("\n📝 Step 4: 生成性能报告...")
    report_path = generate_report(results, features, elo_dim=len(elo_cols))

    # Step 5: 保存结果 JSON
    results_json = {
        'timestamp': timestamp,
        'task': 'T-005',
        'data': {
            'total_matches': len(features),
            'valid_samples': len(X),
            'feature_dim': total_feature_dim,
            'hcp_features': sum(1 for c in X.columns if c.startswith('hcp_')),
            'elo_features': len(elo_cols),
        },
        'class_distribution': {
            '上盘赢': int((y == 0).sum()),
            '走水': int((y == 1).sum()),
            '下盘赢': int((y == 2).sum()),
        },
        'classification_3class': {
            'lgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))}
                    for k, v in results['lgb'].items() if v},
            'xgb': {k: {'mean': float(np.mean(v)), 'std': float(np.std(v))}
                    for k, v in results['xgb'].items() if v},
        },
        'acceptance': {
            'accuracy_45pct': float(np.mean(results['lgb']['accuracy'])) >= 0.45 if results['lgb']['accuracy'] else False,
        }
    }

    json_path = os.path.join(REPORT_DIR, f'hcp_model_results_{timestamp}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(results_json, f, ensure_ascii=False, indent=2)
    print(f"[JSON] 结果已保存: {json_path}")

    print(f"\n{'='*70}")
    print(f"✅ T-005 训练完成!")
    print(f"{'='*70}")

    return results


if __name__ == "__main__":
    main()