# -*- coding: utf-8 -*-
"""
P2-14: 分层评估体系（Stratified Evaluation）
=============================================
对验证集预测结果按 6 个维度分层，输出每层样本量 / Acc / LogLoss / RPS。
供训练报告 `final_training_report_*.json` 与训练日志记录使用。

6 个维度:
  1. 按联赛         competition_name（英超/西甲/意甲/德甲/法甲）
  2. 按赛季阶段     早(8-9月)/中(10-2月)/晚(3-5月)
  3. 按时间滚动     val 集按时间均分 3 段（前/中/后）
  4. 按主客场       实际结果：客胜(0)/平局(1)/主胜(2)
  5. 按赔率区间     市场最看好方隐含概率(wdl_favorite_prob)分桶
  6. 按投注模拟     价值投注：模型概率 > 隐含概率 + edge 才下注，模拟 ROI

口径:
  - result 编码与生产一致: 0=客胜, 1=平局, 2=主胜
  - proba 列序 = [客胜(0), 平(1), 主胜(2)]，与 train_models.compute_rps 完全一致
  - 隐含概率映射: wdl_implied_lose=客胜, wdl_implied_draw=平, wdl_implied_win=主胜
"""

import numpy as np
import pandas as pd

RESULT_LABELS = ['客胜', '平局', '主胜']

# 市场隐含概率列序 [客胜, 平, 主胜]
_IMPLIED_COLS = ['wdl_implied_lose', 'wdl_implied_draw', 'wdl_implied_win']

# 赔率区间分桶（市场最看好方隐含概率）
_FAVORITE_BINS = [-np.inf, 0.40, 0.50, 0.60, np.inf]
_FAVORITE_LABELS = ['<0.40', '0.40-0.50', '0.50-0.60', '>=0.60']

# 价值投注 edge 阈值
EDGE_THRESHOLD = 0.05


def _rps(y_true, proba):
    n = len(y_true)
    actual = np.zeros((n, 3))
    for i, yv in enumerate(y_true):
        actual[i, int(yv)] = 1.0
    cumP = np.cumsum(proba, axis=1)[:, :-1]
    cumO = np.cumsum(actual, axis=1)[:, :-1]
    return float(np.mean(np.sum((cumP - cumO) ** 2, axis=1)) / 2.0)


def _slice_metrics(y, p):
    y = np.asarray(y)
    p = np.asarray(p)
    pred = p.argmax(axis=1)
    acc = float((pred == y).mean())
    rows = p[np.arange(len(y)), y]
    ll = float(np.mean(-np.log(np.clip(rows, 1e-9, 1.0))))
    rps = _rps(y, p)
    return {'n': int(len(y)), 'acc': acc, 'logloss': ll, 'rps': rps}


def _group(y, p, mask):
    idx = np.where(np.asarray(mask))[0]
    if len(idx) == 0:
        return None
    return _slice_metrics(y[idx], p[idx])


def _season_stage(dates):
    stages = []
    for d in dates:
        m = pd.Timestamp(d).month
        if m in (8, 9):
            stages.append('early')
        elif m in (10, 11, 12, 1, 2):
            stages.append('mid')
        else:
            stages.append('late')
    return np.array(stages)


def compute_stratified_report(df_val, y_true, proba, odds_val=None):
    """计算分层评估报告。

    参数:
      df_val: DataFrame，至少含 competition_name / date / result，行序与 y_true 对齐
      y_true: (n,) 真实标签 [0,1,2]
      proba:  (n,3) 预测概率，列序 [客胜, 平, 主胜]
      odds_val: 可选 DataFrame，含 wdl_implied_win/draw/lose、wdl_favorite_prob（用于维度5/6）
    """
    y = np.asarray(y_true)
    p = np.asarray(proba)

    report = {
        'overall': _slice_metrics(y, p),
        'dimensions': {},
    }

    # 维度1: 按联赛
    dim_league = {}
    for lg, sub in df_val.groupby('competition_name'):
        mask = (df_val['competition_name'] == lg).values
        dim_league[str(lg)] = _group(y, p, mask)
    report['dimensions']['by_league'] = dim_league

    # 维度2: 按赛季阶段
    stages = _season_stage(df_val['date'].values)
    dim_stage = {}
    for label, st in [('early', 'early'), ('mid', 'mid'), ('late', 'late')]:
        dim_stage[label] = _group(y, p, stages == st)
    report['dimensions']['by_season_stage'] = dim_stage

    # 维度3: 按时间滚动（val 集均分 3 段）
    n = len(y)
    third = n // 3
    boundaries = [(0, third), (third, 2 * third), (2 * third, n)]
    dim_time = {}
    for i, (a, b) in enumerate(boundaries):
        mask = np.zeros(n, dtype=bool)
        mask[a:b] = True
        dim_time[f'chunk_{i+1}'] = _group(y, p, mask)
    report['dimensions']['by_time_roll'] = dim_time

    # 维度4: 按主客场（实际结果）
    dim_ha = {}
    for c, label in [(0, 'away_win'), (1, 'draw'), (2, 'home_win')]:
        dim_ha[label] = _group(y, p, y == c)
    report['dimensions']['by_home_away'] = dim_ha

    # 维度5/6 依赖赔率
    if odds_val is not None and 'wdl_favorite_prob' in odds_val.columns:
        fav = odds_val['wdl_favorite_prob'].values
        dim_odds = {}
        for i in range(len(_FAVORITE_LABELS)):
            mask = (fav > _FAVORITE_BINS[i]) & (fav <= _FAVORITE_BINS[i + 1])
            dim_odds[_FAVORITE_LABELS[i]] = _group(y, p, mask)
        report['dimensions']['by_odds_interval'] = dim_odds

        # 维度6: 投注模拟（价值投注 ROI）
        if all(c in odds_val.columns for c in _IMPLIED_COLS):
            implied = np.zeros((n, 3))
            implied[:, 0] = odds_val['wdl_implied_lose'].values
            implied[:, 1] = odds_val['wdl_implied_draw'].values
            implied[:, 2] = odds_val['wdl_implied_win'].values
            edge = p - implied
            bet_class = p.argmax(axis=1)
            edge_sel = edge[np.arange(n), bet_class]
            bet_mask = edge_sel > EDGE_THRESHOLD
            n_bets = int(bet_mask.sum())
            if n_bets > 0:
                odds_bet = 1.0 / np.clip(implied[np.arange(n), bet_class][bet_mask], 1e-6, None)
                wins = (bet_class[bet_mask] == y[bet_mask])
                profits = np.where(wins, odds_bet - 1.0, -1.0)
                roi = float(profits.mean())
                hit_rate = float(wins.mean())
                mean_edge = float(edge_sel[bet_mask].mean())
            else:
                roi = hit_rate = mean_edge = None
            report['dimensions']['by_value_betting'] = {
                'bets': n_bets,
                'hit_rate': hit_rate,
                'mean_edge': mean_edge,
                'roi': roi,
            }

    return report


def print_stratified_report(report):
    """以紧凑表格打印分层评估结果。"""
    print("\n" + "=" * 70)
    print("P2-14 分层评估（blend 50/50 验证集）")
    print("=" * 70)
    ov = report['overall']
    print(f"  整体: n={ov['n']} Acc={ov['acc']:.4f} LogLoss={ov['logloss']:.4f} RPS={ov['rps']:.4f}")

    for dim_name, dim_data in report['dimensions'].items():
        print(f"\n  >>> {dim_name}")
        if dim_name == 'by_value_betting':
            vb = dim_data
            print(f"      下注 {vb['bets']} 场 | 命中率 "
                  f"{vb['hit_rate'] if vb['hit_rate'] is not None else 'N/A'} | "
                  f"平均edge {vb['mean_edge'] if vb['mean_edge'] is not None else 'N/A'} | "
                  f"ROI {vb['roi'] if vb['roi'] is not None else 'N/A'}")
            continue
        for grp, m in dim_data.items():
            if m is None:
                print(f"    {grp:16s} n=0（无样本）")
            else:
                print(f"    {grp:16s} n={m['n']:5d} Acc={m['acc']:.4f} "
                      f"LogLoss={m['logloss']:.4f} RPS={m['rps']:.4f}")


if __name__ == '__main__':
    print("P2-14 分层评估模块（供 train_models.py 调用，无独立运行逻辑）")