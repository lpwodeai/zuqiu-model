# -*- coding: utf-8 -*-
"""
P2-15: 自动化数据质量监控（Quality Gate）
==========================================
在训练 Pipeline 前置步骤运行，对加载的比赛数据做结构化质量检查，
输出 pass / warning / fail 三级状态并自动告警（打印醒目告警 + 写入训练日志）。

检查项:
  1. 总样本量（过少告警）
  2. 关键列缺失值率（date/home/away/competition_name/result/homeGoals/awayGoals）
  3. 结果分布（客胜/平/主胜比例，极端失衡告警）
  4. 联赛覆盖（缺失/分布）
  5. 重复比赛（home+away+date 三元组重复）
  6. 球队名称异常（空值/非中文）

状态判定（对齐 data_cleaner 口径）:
  任一 critical → fail；任一 minor → warning；否则 pass
"""

import numpy as np
import pandas as pd
from datetime import datetime

RESULT_LABELS = ['客胜', '平局', '主胜']


def run_quality_gate(df):
    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_matches': int(len(df)),
        'status': 'pass',
        'critical': [],
        'warning': [],
        'stats': {},
        'checks': {},
    }

    n = len(df)
    if n == 0:
        report['status'] = 'fail'
        report['critical'].append('数据集为空')
        return report

    # 1. 样本量
    if n < 3000:
        report['warning'].append(f'样本量偏少({n} 场)')
    report['stats']['total_matches'] = n

    # 2. 关键列缺失值率
    key_cols = ['date', 'home_team', 'away_team', 'competition_name', 'result',
                'homeGoals', 'awayGoals']
    missing = {}
    for c in key_cols:
        if c in df.columns:
            cnt = int(df[c].isnull().sum())
            missing[c] = cnt
        else:
            missing[c] = n
            report['critical'].append(f'缺少关键列 {c}')
    report['stats']['missing'] = missing
    for c, cnt in missing.items():
        rate = cnt / n
        if rate > 0.3:
            report['critical'].append(f'列 {c} 缺失率 {rate:.1%} > 30%')
        elif rate > 0.1:
            report['warning'].append(f'列 {c} 缺失率 {rate:.1%} > 10%')

    # 3. 结果分布
    if 'result' in df.columns:
        vc = df['result'].value_counts(normalize=True)
        dist = {int(k): round(float(v), 4) for k, v in vc.items()}
        report['stats']['result_distribution'] = dist
        for c in (0, 1, 2):
            if dist.get(c, 0.0) < 0.15:
                report['warning'].append(
                    f'{RESULT_LABELS[c]} 占比 {dist.get(c, 0):.1%} 偏低(<15%)')

    # 4. 联赛覆盖
    if 'competition_name' in df.columns:
        league_counts = {str(k): int(v) for k, v in df['competition_name'].value_counts().items()}
        report['stats']['league_distribution'] = league_counts
        if len(league_counts) < 5:
            report['warning'].append(
                f'联赛覆盖不足({len(league_counts)} 个联赛，预期 5)')

    # 5. 重复比赛（home+away+date）
    if all(c in df.columns for c in ['home_team', 'away_team', 'date']):
        dup_mask = df.duplicated(subset=['home_team', 'away_team', 'date'], keep=False)
        n_dup = int(dup_mask.sum())
        report['stats']['duplicate_matches'] = n_dup
        if n_dup > 0:
            dup_rate = n_dup / n
            if dup_rate > 0.05:
                report['critical'].append(f'重复比赛 {n_dup} 场({dup_rate:.1%})')
            else:
                report['warning'].append(f'重复比赛 {n_dup} 场')

    # 6. 球队名称异常（空/非中文）
    if all(c in df.columns for c in ['home_team', 'away_team']):
        def _valid_team(x):
            s = str(x).strip()
            return bool(s) and any('\u4e00' <= ch <= '\u9fff' for ch in s)
        bad = 0
        for c in ['home_team', 'away_team']:
            bad += int((~df[c].map(_valid_team)).sum())
        report['stats']['invalid_team_names'] = bad
        if bad / n > 0.1:
            report['warning'].append(f'球队名称异常 {bad} 场')

    # 状态判定
    if report['critical']:
        report['status'] = 'fail'
    elif report['warning']:
        report['status'] = 'warning'

    report['checks']['critical_count'] = len(report['critical'])
    report['checks']['warning_count'] = len(report['warning'])
    return report


def print_quality_gate(report):
    status = report['status']
    banner = {
        'pass': '[PASS] 数据质量门禁: PASS',
        'warning': '[WARN] 数据质量门禁: WARNING',
        'fail': '[FAIL] 数据质量门禁: FAIL',
    }[status]
    print("\n" + "=" * 60)
    print("[P2-15] 自动化数据质量监控")
    print(banner)
    print("-" * 60)
    print(f"  总样本量: {report['total_matches']}")
    print(f"  生成时间: {report['generated_at']}")
    stats = report['stats']
    print(f"  联赛分布: {stats.get('league_distribution', {})}")
    print(f"  结果分布(客胜/平/主胜): {stats.get('result_distribution', {})}")
    print(f"  重复比赛: {stats.get('duplicate_matches', 0)} 场")
    if report['critical']:
        print(f"  [CRITICAL] {len(report['critical'])} 项")
        for m in report['critical']:
            print(f"     - {m}")
    if report['warning']:
        print(f"  [WARNING] {len(report['warning'])} 项")
        for m in report['warning']:
            print(f"     - {m}")
    print("=" * 60)


if __name__ == '__main__':
    print("P2-15 质量门禁模块（供 train_models.py 调用，无独立运行逻辑）")