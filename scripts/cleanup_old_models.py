#!/usr/bin/env python3
"""
清理 assets/ 中过期的历史模型 .pkl 文件。

删除策略:
  1. 白名单保护(绝不删除):
     - deployment/latest_model.json 指向的所有生产模型路径
     - 所有 t005v3_* 当前 T-005 生产系列
     - 最近 N 个 advanced_model_*.pkl(默认 5)
  2. 删除:
     - advanced_model_*.pkl 中超过 max_age_days(默认 30)且不在白名单的
  3. 可选(--include-legacy):同时清理 lgb_model_/xgb_model_/ensemble_model_/
     stacking_/multitask_/isolation_/d016_calibrated_ 等老实验系列中超期的

用法:
  python scripts/cleanup_old_models.py                       # dry-run,仅列出
  python scripts/cleanup_old_models.py --apply               # 真正执行删除
  python scripts/cleanup_old_models.py --keep 5 --days 30
  python scripts/cleanup_old_models.py --include-legacy --apply
"""
import os
import sys
import json
import re
import argparse
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(PROJECT_DIR, 'assets')
DEPLOY_DIR = os.path.join(PROJECT_DIR, 'deployment')
LATEST_MODEL_JSON = os.path.join(DEPLOY_DIR, 'latest_model.json')

ADVANCED_RE = re.compile(r'^advanced_model_(\d{8}_\d{6})\.pkl$')

# 老实验系列前缀(仅 --include-legacy 时清理)
LEGACY_PATTERNS = [
    'lgb_model_', 'xgb_model_', 'ensemble_model_', 'scaler_',
    'selected_features_', 'stacking_', 'multitask_', 'isolation_',
    'd016_calibrated_',
]


def parse_advanced_ts(fname):
    """从 advanced_model_YYYYMMDD_HHMMSS.pkl 提取时间戳,失败返回 None"""
    m = ADVANCED_RE.match(fname)
    if not m:
        return None
    return datetime.strptime(m.group(1), '%Y%m%d_%H%M%S')


def collect_whitelist(keep_recent):
    """收集受保护的生产模型文件名集合 + 返回 advanced 版本列表(新→旧)"""
    wl = set()

    # 1. latest_model.json 指向的路径(T-005 生产)
    if os.path.exists(LATEST_MODEL_JSON):
        with open(LATEST_MODEL_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for p in data.get('model_paths', {}).values():
            if p and os.path.exists(p):
                wl.add(os.path.basename(p))

    # 2. 所有 t005v3_* 当前 T-005 生产系列
    if os.path.isdir(ASSETS_DIR):
        for fname in os.listdir(ASSETS_DIR):
            if fname.startswith('t005v3_') and fname.endswith('.pkl'):
                wl.add(fname)

    # 3. 最近的 advanced_model_*.pkl
    advanced = []
    for fname in os.listdir(ASSETS_DIR):
        ts = parse_advanced_ts(fname)
        if ts:
            advanced.append((ts, fname))
    advanced.sort(reverse=True)  # 最新在前
    for ts, fname in advanced[:keep_recent]:
        wl.add(fname)

    return wl, advanced


def main():
    ap = argparse.ArgumentParser(description='清理过期历史模型 .pkl')
    ap.add_argument('--apply', action='store_true',
                    help='真正执行删除(默认 dry-run)')
    ap.add_argument('--keep', type=int, default=5,
                    help='保留最近 N 个 advanced 版本(默认 5)')
    ap.add_argument('--days', type=int, default=30,
                    help='删除超过 N 天的文件(默认 30)')
    ap.add_argument('--include-legacy', action='store_true',
                    help='同时清理 lgb/xgb/ensemble/stacking 等老实验系列')
    args = ap.parse_args()

    if not os.path.isdir(ASSETS_DIR):
        print(f'❌ assets 目录不存在: {ASSETS_DIR}')
        sys.exit(1)

    whitelist, advanced_list = collect_whitelist(args.keep)
    cutoff = datetime.now() - timedelta(days=args.days)

    to_delete = []
    kept_advanced = 0

    # advanced_model_*.pkl:超期且不在白名单 → 删除
    for ts, fname in advanced_list:
        path = os.path.join(ASSETS_DIR, fname)
        if fname in whitelist:
            kept_advanced += 1
            continue
        if ts < cutoff:
            size_mb = os.path.getsize(path) / 1024 / 1024
            to_delete.append((path, fname, ts, size_mb, 'advanced'))
        else:
            kept_advanced += 1

    # legacy 系列(可选)
    if args.include_legacy:
        for fname in os.listdir(ASSETS_DIR):
            if not fname.endswith('.pkl') or fname in whitelist:
                continue
            if ADVANCED_RE.match(fname):
                continue
            if not any(fname.startswith(p) for p in LEGACY_PATTERNS):
                continue
            path = os.path.join(ASSETS_DIR, fname)
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if mtime < cutoff:
                size_mb = os.path.getsize(path) / 1024 / 1024
                to_delete.append((path, fname, mtime, size_mb, 'legacy'))

    # 输出报告
    print('=' * 70)
    print(f'模型清理 {"[APPLY]" if args.apply else "[DRY-RUN]"}')
    print(f'  保留最近 advanced 版本数 : {args.keep}')
    print(f'  过期阈值              : {args.days} 天 (早于 {cutoff.strftime("%Y-%m-%d")})')
    print(f'  白名单保护文件数       : {len(whitelist)}')
    print(f'  advanced 总版本/保留   : {len(advanced_list)} / {kept_advanced}')
    print('=' * 70)

    if not to_delete:
        print('\n✅ 没有需要清理的文件。')
        return

    total_mb = sum(x[3] for x in to_delete)
    print(f'\n将删除 {len(to_delete)} 个文件,释放 {total_mb:.1f} MB:\n')
    for path, fname, ts, size_mb, cat in sorted(to_delete, key=lambda x: x[2]):
        print(f'  [{cat:8s}] {fname}  ({ts.strftime("%Y-%m-%d %H:%M")}, {size_mb:.1f} MB)')

    if not args.apply:
        print('\n💡 这是 DRY-RUN。确认无误后加 --apply 执行删除。')
        return

    # --apply 直接执行(--apply 本身即显式确认)
    deleted = 0
    for path, fname, ts, size_mb, cat in to_delete:
        try:
            os.remove(path)
            print(f'  🗑️ 已删除 {fname}')
            deleted += 1
        except OSError as e:
            print(f'  ❌ 删除失败 {fname}: {e}')
    print(f'\n✅ 清理完成:删除 {deleted}/{len(to_delete)} 个文件,释放 {total_mb:.1f} MB。')


if __name__ == '__main__':
    main()
