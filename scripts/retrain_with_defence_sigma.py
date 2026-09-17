# -*- coding: utf-8 -*-
"""
防守波动 σ 特征 全量重训对比脚本 (D-014)
=========================================
目的: 将新增的 3 个防守特征 (home_opp_goals_std/away_opp_goals_std/defence_stability_diff)
应用到全量历史数据，运行完整训练流程 (TimeSeriesSplit CV + 全量训练)，
并对比新增前后的模型效果。

用法: python scripts/retrain_with_defence_sigma.py
"""
import os, sys, warnings, json
import numpy as np
import pandas as pd
from datetime import datetime
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss, classification_report
from sklearn.preprocessing import StandardScaler
import lightgbm as lgb
import xgboost as xgb

from feature_utils import (load_match_data_odds, build_all_features, load_config)

CONFIG = load_config()
OUTPUT_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'assets')
NEW_DEFENCE_COLS = ['home_opp_goals_std', 'away_opp_goals_std', 'defence_stability_diff']

# ============================================================
# 训练配置
# ============================================================
LGB_PARAMS = dict(
    objective='multiclass', num_class=3, metric='multi_logloss',
    learning_rate=0.05, num_leaves=31, feature_fraction=0.8,
    bagging_fraction=0.8, bagging_freq=1, min_data_in_leaf=20,
    lambda_l2=1.0, verbose=-1, n_jobs=-1, random_state=42,
)
XGB_PARAMS = dict(
    objective='multi:softprob', num_class=3, eval_metric='mlogloss',
    max_depth=2, learning_rate=0.03, subsample=0.6, colsample_bytree=0.6,
    gamma=0.5, min_child_weight=10, reg_alpha=1.0, reg_lambda=10.0,
    seed=42, nthread=-1,
)

# ============================================================
# 工具函数
# ============================================================
def compute_ece(y_true, y_prob, n_bins=10):
    ece = 0.0
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        mask = (np.max(y_prob, axis=1) >= lo) & (np.max(y_prob, axis=1) < hi)
        if mask.sum() == 0:
            continue
        acc = (np.argmax(y_prob[mask], axis=1) == y_true[mask]).mean()
        conf = np.max(y_prob[mask], axis=1).mean()
        ece += (mask.sum() / len(y_true)) * abs(acc - conf)
    return ece


def train_lgb(X_tr, y_tr, X_va, y_va, sw=None):
    model = lgb.LGBMClassifier(**LGB_PARAMS)
    model.fit(X_tr, y_tr, sample_weight=sw,
              eval_set=[(X_va, y_va)],
              callbacks=[lgb.early_stopping(30, verbose=False)])
    p = model.predict_proba(X_va)
    pred = np.argmax(p, axis=1)
    return model, dict(
        accuracy=accuracy_score(y_va, pred),
        log_loss=log_loss(y_va, p),
        brier=brier_score_loss(y_va, p, pos_label=2),
        draw_recall=confusion_matrix_val(y_va, pred)[1,1] / confusion_matrix_val(y_va, pred)[1].sum()
    )


def train_xgb_model(X_tr, y_tr, X_va, y_va, sw=None):
    dtrain = xgb.DMatrix(X_tr, label=y_tr)
    dval = xgb.DMatrix(X_va, label=y_va)
    model = xgb.train(XGB_PARAMS, dtrain, num_boost_round=100,
                      evals=[(dval, 'val')], early_stopping_rounds=30,
                      verbose_eval=False)
    p = model.predict(dval)
    pred = np.argmax(p, axis=1)
    return model, dict(
        accuracy=accuracy_score(y_va, pred),
        log_loss=log_loss(y_va, p),
        brier=brier_score_loss(y_va, p, pos_label=2),
        draw_recall=confusion_matrix_val(y_va, pred)[1,1] / confusion_matrix_val(y_va, pred)[1].sum()
    )


def confusion_matrix_val(y_true, y_pred):
    from sklearn.metrics import confusion_matrix
    return confusion_matrix(y_true, y_pred, labels=[0, 1, 2])


def run_full_training(X, y, label):
    """运行完整训练流程: 5折TS CV + 全量训练"""
    print(f'\n{"="*60}')
    print(f'  {label}')
    print(f'{"="*60}')
    print(f'  特征维度: {X.shape[1]}, 样本数: {len(X)}')

    # --- 5折 TimeSeriesSplit CV ---
    tscv = TimeSeriesSplit(n_splits=5)
    lgb_accs, lgb_lls, lgb_draws = [], [], []
    xgb_accs, xgb_lls, xgb_draws = [], [], []
    for fold, (tr, va) in enumerate(tscv.split(X)):
        Xtr, ytr = X.iloc[tr], y.iloc[tr]
        Xva, yva = X.iloc[va], y.iloc[va]
        scaler = StandardScaler()
        Xtr_sc = scaler.fit_transform(Xtr)
        Xva_sc = scaler.transform(Xva)

        _, m = train_lgb(Xtr_sc, ytr, Xva_sc, yva)
        lgb_accs.append(m['accuracy']); lgb_lls.append(m['log_loss']); lgb_draws.append(m['draw_recall'])
        _, m = train_xgb_model(Xtr_sc, ytr, Xva_sc, yva)
        xgb_accs.append(m['accuracy']); xgb_lls.append(m['log_loss']); xgb_draws.append(m['draw_recall'])

        print(f'   Fold{fold+1} LGB acc={lgb_accs[-1]*100:.1f}% ll={lgb_lls[-1]:.4f} draw={lgb_draws[-1]*100:.1f}%'
              f' | XGB acc={xgb_accs[-1]*100:.1f}% ll={xgb_lls[-1]:.4f} draw={xgb_draws[-1]*100:.1f}%')

    print(f'   --- CV 汇总 ---')
    print(f'   LGB: acc={np.mean(lgb_accs)*100:.2f}%±{np.std(lgb_accs)*100:.2f}  '
          f'll={np.mean(lgb_lls):.4f}±{np.std(lgb_lls):.4f}  '
          f'draw={np.mean(lgb_draws)*100:.1f}%±{np.std(lgb_draws)*100:.1f}')
    print(f'   XGB: acc={np.mean(xgb_accs)*100:.2f}%±{np.std(xgb_accs)*100:.2f}  '
          f'll={np.mean(xgb_lls):.4f}±{np.std(xgb_lls):.4f}  '
          f'draw={np.mean(xgb_draws)*100:.1f}%±{np.std(xgb_draws)*100:.1f}')

    # --- 全量训练 (80/20 时间序列划分) ---
    split = int(len(X) * 0.8)
    Xtr, ytr = X.iloc[:split], y.iloc[:split]
    Xva, yva = X.iloc[split:], y.iloc[split:]
    scaler = StandardScaler()
    Xtr_sc = scaler.fit_transform(Xtr)
    Xva_sc = scaler.transform(Xva)

    lgb_model, lgb_m = train_lgb(Xtr_sc, ytr, Xva_sc, yva)
    xgb_model, xgb_m = train_xgb_model(Xtr_sc, ytr, Xva_sc, yva)

    # ECE
    lgb_p = lgb_model.predict_proba(Xva_sc)
    ece_lgb = compute_ece(yva, lgb_p)
    xgb_p = xgb_model.predict(xgb.DMatrix(Xva_sc))
    ece_xgb = compute_ece(yva, xgb_p)

    # 完整分类报告 (per-class precision/recall/f1)
    lgb_pred = np.argmax(lgb_p, axis=1)
    xgb_pred = np.argmax(xgb_p, axis=1)
    lgb_report = classification_report(yva, lgb_pred, labels=[0, 1, 2], output_dict=True, zero_division=0)
    xgb_report = classification_report(yva, xgb_pred, labels=[0, 1, 2], output_dict=True, zero_division=0)

    print(f'   --- 全量训练 (80/20) ---')
    print(f'   LGB: acc={lgb_m["accuracy"]*100:.2f}% ll={lgb_m["log_loss"]:.4f} '
          f'draw={lgb_m["draw_recall"]*100:.1f}% ECE={ece_lgb:.4f}')
    print(f'   XGB: acc={xgb_m["accuracy"]*100:.2f}% ll={xgb_m["log_loss"]:.4f} '
          f'draw={xgb_m["draw_recall"]*100:.1f}% ECE={ece_xgb:.4f}')

    return dict(
        cv_lgb_acc=np.mean(lgb_accs), cv_lgb_ll=np.mean(lgb_lls), cv_lgb_draw=np.mean(lgb_draws),
        cv_xgb_acc=np.mean(xgb_accs), cv_xgb_ll=np.mean(xgb_lls), cv_xgb_draw=np.mean(xgb_draws),
        full_lgb_acc=lgb_m['accuracy'], full_lgb_ll=lgb_m['log_loss'], full_lgb_draw=lgb_m['draw_recall'],
        full_xgb_acc=xgb_m['accuracy'], full_xgb_ll=xgb_m['log_loss'], full_xgb_draw=xgb_m['draw_recall'],
        ece_lgb=ece_lgb, ece_xgb=ece_xgb,
        lgb_report=lgb_report, xgb_report=xgb_report,
    )


# ============================================================
# 主流程
# ============================================================
def main():
    print('=' * 60)
    print('防守波动 Sigma 特征 全量重训对比 (D-014)')
    print('=' * 60)

    print('\n[1/4] 加载数据...')
    df = load_match_data_odds()
    print(f'  比赛样本: {len(df)}')

    print('\n[2/4] 构建特征 (含新防守特征)...')
    X, y = build_all_features(df, include_odds=True, include_elo=True,
                              include_temporal=True, include_score=True, include_nonlinear=True)
    print(f'  完整特征维度: {X.shape[1]}')

    # 确认新特征已存在
    present = [c for c in NEW_DEFENCE_COLS if c in X.columns]
    missing = [c for c in NEW_DEFENCE_COLS if c not in X.columns]
    print(f'  新特征: 已构建={present}, 缺失={missing}')
    if missing:
        print('  [ERROR] 部分新特征未构建成功，请检查 feature_utils.py 修改是否正确')
        sys.exit(1)

    print('\n[3/4] 基线训练 (不含防守 σ 特征)...')
    X_no = X.drop(columns=NEW_DEFENCE_COLS)
    r_no = run_full_training(X_no, y, '基线 (不含防守 σ 特征)')

    print('\n[4/4] 新特征训练 (含防守 σ 特征)...')
    r_new = run_full_training(X, y, '新增 (含防守 σ 特征)')

    # ============================================================
    # 对比报告
    # ============================================================
    print(f'\n{"="*60}')
    print('  对比报告')
    print(f'{"="*60}')
    items = [
        ('CV LGB Acc', 'cv_lgb_acc', '%'),
        ('CV LGB LogLoss', 'cv_lgb_ll', ''),
        ('CV LGB Draw Recall', 'cv_lgb_draw', '%'),
        ('CV XGB Acc', 'cv_xgb_acc', '%'),
        ('CV XGB LogLoss', 'cv_xgb_ll', ''),
        ('CV XGB Draw Recall', 'cv_xgb_draw', '%'),
        ('Full LGB Acc', 'full_lgb_acc', '%'),
        ('Full LGB LogLoss', 'full_lgb_ll', ''),
        ('Full LGB Draw Recall', 'full_lgb_draw', '%'),
        ('Full XGB Acc', 'full_xgb_acc', '%'),
        ('Full XGB LogLoss', 'full_xgb_ll', ''),
        ('Full XGB Draw Recall', 'full_xgb_draw', '%'),
        ('LGB ECE', 'ece_lgb', ''),
        ('XGB ECE', 'ece_xgb', ''),
    ]
    for name, key, unit in items:
        v_no = r_no[key]
        v_new = r_new[key]
        if unit == '%':
            delta = v_new - v_no
            direction = '+' if delta > 0 else ''
            print(f'  {name:20s}: {v_no*100:6.2f}% -> {v_new*100:6.2f}%  '
                  f'(Δ {direction}{delta*100:+.2f}pp)')
        else:
            delta = v_new - v_no
            direction = '+' if delta > 0 else ''
            print(f'  {name:20s}: {v_no:7.4f} -> {v_new:7.4f}  '
                  f'(Δ {direction}{delta:+.4f})')

    # 保存结果
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    result = dict(timestamp=ts, baseline=r_no, new=r_new)
    result_path = os.path.join(OUTPUT_DIR, f'defence_sigma_compare_{ts}.json')
    # 转换 numpy 类型
    def convert(obj):
        if isinstance(obj, (np.integer,)): return int(obj)
        if isinstance(obj, (np.floating,)): return float(obj)
        if isinstance(obj, dict): return {k: convert(v) for k, v in obj.items()}
        return obj
    with open(result_path, 'w') as f:
        json.dump(convert(result), f, indent=2, ensure_ascii=False)
    print(f'\n结果已保存: {result_path}')

    # ============================================================
    # 生成 Markdown 对比报告
    # ============================================================
    md_path = os.path.join(OUTPUT_DIR, f'defence_sigma_report_{ts}.md')
    wdl = {0: '客胜', 1: '平局', 2: '主胜'}
    L = []
    L.append('# 防守波动 σ 特征 (D-014) 全量重训对比报告\n')
    L.append(f'> 生成时间: {ts}')
    L.append(f'> 数据规模: {len(df)} 场比赛 (5大联赛)')
    L.append(f'> 特征维度: 基线 {X_no.shape[1]} 维 → 新增 {X.shape[1]} 维\n')

    L.append('## 一、核心指标对比\n')
    L.append('| 指标 | 基线 | 新增 | 变化 |')
    L.append('|------|------|------|------|')
    for name, key, unit in items:
        v_no = r_no[key]
        v_new = r_new[key]
        if unit == '%':
            d = (v_new - v_no) * 100
            L.append(f'| {name} | {v_no*100:.2f}% | {v_new*100:.2f}% | {d:+.2f}pp |')
        else:
            d = v_new - v_no
            L.append(f'| {name} | {v_no:.4f} | {v_new:.4f} | {d:+.4f} |')

    for mdl in ['lgb', 'xgb']:
        L.append(f'\n## {"二" if mdl == "lgb" else "三"}、各类别指标 ({mdl.upper()} 全量训练)\n')
        L.append('| 类别 | 指标 | 基线 | 新增 |')
        L.append('|------|------|------|------|')
        rep_no = r_no[f'{mdl}_report']
        rep_new = r_new[f'{mdl}_report']
        for cls in ['0', '1', '2', 'macro avg', 'weighted avg']:
            label = wdl.get(int(cls), cls) if cls.isdigit() else cls
            for metric in ['precision', 'recall', 'f1-score']:
                b = rep_no[cls][metric]
                n = rep_new[cls][metric]
                L.append(f'| {label} | {metric} | {b*100:.2f}% | {n*100:.2f}% |')

    L.append('\n## 四、结论\n')
    draw_delta = (r_new['cv_lgb_draw'] - r_no['cv_lgb_draw']) * 100
    ece_delta = r_new['ece_lgb'] - r_no['ece_lgb']
    acc_delta = (r_new['cv_lgb_acc'] - r_no['cv_lgb_acc']) * 100
    L.append(f'- 平局召回率 (CV LGB): {draw_delta:+.2f}pp')
    L.append(f'- 准确率 (CV LGB): {acc_delta:+.2f}pp')
    L.append(f'- ECE 校准误差 (LGB): {ece_delta:+.4f}')
    if draw_delta >= 0.5:
        verdict = '防守 σ 特征显著改善平局识别，建议并入生产'
    elif draw_delta <= -0.5:
        verdict = '防守 σ 特征为负收益（与 D-013/比分波动特征信息重叠），不建议并入生产'
    else:
        verdict = '防守 σ 特征无显著影响，需结合更多指标判断'
    L.append(f'- 结论: {verdict}\n')

    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'\nMarkdown 报告已保存: {md_path}')


if __name__ == '__main__':
    main()