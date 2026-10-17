# -*- coding: utf-8 -*-
"""feature_gate_audit_report.py — 特征准入闸年度体检（D-20261006-052 轨 A）

对当前 WDL 生产特征集（build_all_features 生产同源参数）跑准入闸三层检查：
  1. 注册校验（PRE_MATCH_FEATURE_CATALOG，D-009）
  2. 内部相关性聚类（特征两两 |r|>0.8 的冗余簇，供精简参考）
  3. LGBM gain importance 占比（<1% 建议下线候选）

输出: reports/feature_gate_audit_YYYYMMDD_HHMMSS.md
"""
import os, sys, datetime
import numpy as np
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
BASE_DIR = os.path.dirname(SCRIPT_DIR)

import feature_utils as fu
import lightgbm as lgb


def main():
    print('加载数据 + 构建特征（生产同源: slim_odds+ts_odds+consensus_odds）...')
    df = fu.load_match_data_odds()
    X, y = fu.build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
    feats = list(X.columns)
    n_feat = len(feats)
    print(f'  样本 {len(X)}，特征 {n_feat} 维')

    # --- 层 1: 注册校验 ---
    reg = fu.feature_gate_register_check(feats)

    # --- 层 2: 内部相关性冗余簇（现有特征集两两） ---
    print('  相关性矩阵计算...')
    C = X.astype(float).corr().abs()
    C_arr = C.to_numpy(copy=True)
    np.fill_diagonal(C_arr, 0.0)
    redundant_pairs = []
    cols = list(C.columns)
    for i in range(n_feat):
        for j in range(i + 1, n_feat):
            if C_arr[i, j] > fu.FEATURE_GATE_CORR_THRESH:
                redundant_pairs.append((cols[i], cols[j], float(C_arr[i, j])))
    redundant_pairs.sort(key=lambda t: -t[2])

    # --- 层 3: LGBM gain importance（80/20 时间切分，快速体检非正式训练） ---
    print('  LGBM 快速训练（gain 重要性）...')
    Xv = X.fillna(X.median(numeric_only=True)).fillna(0.0)
    split = int(len(Xv) * 0.8)
    dtrain = lgb.Dataset(Xv.iloc[:split], y.iloc[:split])
    params = dict(objective='multiclass', num_class=3, max_depth=6, num_leaves=31,
                  learning_rate=0.05, verbose=-1, seed=42)
    booster = lgb.train(params, dtrain, num_boost_round=300)
    imp = fu.feature_gate_importance(booster.feature_importance(importance_type='gain'), feats)

    # --- 汇总报告 ---
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(BASE_DIR, 'reports', f'feature_gate_audit_{ts}.md')
    L = []
    L.append('# 特征准入闸年度体检报告（WDL 生产特征集）\n')
    L.append(f'> 生成: {ts} ｜ D-20261006-052 轨 A ｜ 特征构建: build_all_features(slim_odds, ts_odds, consensus_odds) ｜ 样本 n={len(X)} ｜ 维度 {n_feat}\n')

    L.append('## 层 1：注册校验（D-009）\n')
    L.append(f'- 已注册: {len(reg["registered"])}/{n_feat}')
    unreg = reg['unregistered']
    L.append(f'- 未注册: **{len(unreg)}**' + ('（清单如下）' if unreg else '') + '\n')
    if unreg:
        L.append('| 未注册特征 |')
        L.append('|---|')
        for f in unreg:
            L.append(f'| {f} |')
        L.append('')

    L.append(f'## 层 2：内部冗余簇（|r|>{fu.FEATURE_GATE_CORR_THRESH}）\n')
    L.append(f'- 高相关对数: **{len(redundant_pairs)}**\n')
    if redundant_pairs:
        L.append('| 特征 A | 特征 B | \\|r\\| |')
        L.append('|---|---|---|')
        for a, b, r in redundant_pairs[:30]:
            L.append(f'| {a} | {b} | {r:.3f} |')
        if len(redundant_pairs) > 30:
            L.append(f'| ... | 共 {len(redundant_pairs)} 对，仅列前 30 | |')
        L.append('')

    L.append(f'## 层 3：importance 淘汰候选（gain 占比 <{fu.FEATURE_GATE_MIN_IMPORTANCE_SHARE:.0%}）\n')
    drop = imp['drop']
    L.append(f'- 低于阈值: **{len(drop)}/{n_feat}** 维（合计 gain 占比 '
             f"{sum(imp['share'][f] for f in drop):.1%}）\n")
    if drop:
        L.append('| 特征 | gain 占比 |')
        L.append('|---|---|')
        for f in sorted(drop, key=lambda f: imp['share'][f]):
            L.append(f"| {f} | {imp['share'][f]:.2%} |")
        L.append('')
    top10 = sorted(imp['share'].items(), key=lambda kv: -kv[1])[:10]
    L.append('### Top10 重要性\n')
    L.append('| 特征 | gain 占比 |')
    L.append('|---|---|')
    for f, s in top10:
        L.append(f'| {f} | {s:.2%} |')
    L.append('')
    L.append('## 解读\n')
    L.append('- 未注册特征：违反 D-009，需补登记或从特征集移除。')
    L.append('- 高相关对：每对保留业务含义更直接的一个，另一个可进下线候选（参考 D-017 流程）。')
    L.append('- 低重要性特征：不立即删除——先在 shadow 环境验证移除后 RPS 无回归（小样本窗内 importance 有噪声）。')
    L.append('- 本体检建议每年或每轮大特征变更后重跑一次。')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print(f'报告: {out_path}')
    print(f'未注册 {len(unreg)}，高相关对 {len(redundant_pairs)}，低重要性 {len(drop)}/{n_feat}')


if __name__ == '__main__':
    main()
