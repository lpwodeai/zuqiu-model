"""
CI 钩子：性能回归基线检查
==========================

阶段六 D-020: CI 集成

在 CI 流程中运行的轻量级检查脚本，确保模型性能不退化。
可集成到 Git pre-commit hook 或 CI pipeline。

用法：
    python tests/ci_check.py              # 完整检查
    python tests/ci_check.py --quick      # 快速检查（仅资产完整性）
    python tests/ci_check.py --baseline   # 更新基线
"""

import os
import sys
import json
import glob
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(PROJECT_ROOT, 'assets')
TESTS_DIR = os.path.join(PROJECT_ROOT, 'tests')

# ============================================
# 性能回归基线
# ============================================
# 这些基线基于阶段五最终成果，任何低于基线的变更都应触发警报
PERFORMANCE_BASELINE = {
    'lgb_cv_min': 0.50,        # LGB CV 最低 50%（阶段五 Optuna: 51.24%）
    'xgb_cv_min': 0.49,        # XGB CV 最低 49%（阶段五 Optuna: 51.14%）
    'd017_cv_min': 0.49,       # D-017 60维 CV 最低 49%（实际: 50.48%）
    'feature_dim_min': 55,     # 最终特征维度最低 55（实际: 60）
    'logloss_max': 1.10,       # LogLoss 最高 1.10（实际: 1.0177）
}

# 资产完整性清单
REQUIRED_ASSETS = {
    'optuna_result': 'optuna_result_*.json',
    'd017_features': 'd017_features_*.json',
    'd016_result': 'd016_result_*.json',
}


def find_latest_asset(pattern):
    """查找最新的资产文件"""
    files = glob.glob(os.path.join(ASSETS_DIR, pattern))
    if not files:
        return None
    return max(files, key=os.path.getctime)


def check_asset_integrity():
    """检查模型资产完整性"""
    print("=" * 60)
    print("📋 资产完整性检查")
    print("=" * 60)
    all_pass = True
    for name, pattern in REQUIRED_ASSETS.items():
        path = find_latest_asset(pattern)
        if path is None:
            print(f"  ❌ {name}: 缺失 (pattern: {pattern})")
            all_pass = False
        else:
            size = os.path.getsize(path)
            print(f"  ✅ {name}: {os.path.basename(path)} ({size} bytes)")
    return all_pass


def check_performance_baseline():
    """检查性能回归基线"""
    print("\n" + "=" * 60)
    print("📊 性能回归基线检查")
    print("=" * 60)
    all_pass = True

    # 检查 Optuna 结果
    optuna_path = find_latest_asset(REQUIRED_ASSETS['optuna_result'])
    if optuna_path:
        with open(optuna_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        lgb_cv = data.get('lgb_cv', 0)
        xgb_cv = data.get('xgb_cv', 0)

        status_lgb = "✅" if lgb_cv >= PERFORMANCE_BASELINE['lgb_cv_min'] else "❌"
        status_xgb = "✅" if xgb_cv >= PERFORMANCE_BASELINE['xgb_cv_min'] else "❌"
        print(f"  {status_lgb} LGB CV: {lgb_cv:.4f} (基线: {PERFORMANCE_BASELINE['lgb_cv_min']:.2f})")
        print(f"  {status_xgb} XGB CV: {xgb_cv:.4f} (基线: {PERFORMANCE_BASELINE['xgb_cv_min']:.2f})")

        if lgb_cv < PERFORMANCE_BASELINE['lgb_cv_min']:
            all_pass = False
        if xgb_cv < PERFORMANCE_BASELINE['xgb_cv_min']:
            all_pass = False
    else:
        print("  ❌ Optuna 结果文件缺失")
        all_pass = False

    # 检查 D-017 结果
    d017_path = find_latest_asset(REQUIRED_ASSETS['d017_features'])
    if d017_path:
        with open(d017_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        cv = data['final']['accuracy']
        n_features = len(data['final']['features'])
        logloss = data['final'].get('logloss', 1.0)

        status_cv = "✅" if cv >= PERFORMANCE_BASELINE['d017_cv_min'] else "❌"
        status_dim = "✅" if n_features >= PERFORMANCE_BASELINE['feature_dim_min'] else "❌"
        status_ll = "✅" if logloss <= PERFORMANCE_BASELINE['logloss_max'] else "❌"

        print(f"  {status_cv} D-017 CV: {cv:.4f} (基线: {PERFORMANCE_BASELINE['d017_cv_min']:.2f})")
        print(f"  {status_dim} 特征维度: {n_features} (最低: {PERFORMANCE_BASELINE['feature_dim_min']})")
        print(f"  {status_ll} LogLoss: {logloss:.4f} (最高: {PERFORMANCE_BASELINE['logloss_max']:.2f})")

        if cv < PERFORMANCE_BASELINE['d017_cv_min']:
            all_pass = False
        if n_features < PERFORMANCE_BASELINE['feature_dim_min']:
            all_pass = False
        if logloss > PERFORMANCE_BASELINE['logloss_max']:
            all_pass = False
    else:
        print("  ❌ D-017 结果文件缺失")
        all_pass = False

    return all_pass


def run_quick_tests():
    """运行快速测试（仅单元测试中的非依赖测试）"""
    print("\n" + "=" * 60)
    print("⚡ 快速单元测试")
    print("=" * 60)
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(TESTS_DIR, 'run_all_tests.py'), '--suite', 'unit'],
        capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    # 提取最后一行汇总
    lines = result.stdout.strip().split('\n')
    summary_found = False
    for line in lines[-20:]:
        if '运行测试数' in line or '成功' in line or '失败' in line:
            print(f"  {line.strip()}")
            summary_found = True
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description='CI 钩子：性能回归基线检查')
    parser.add_argument('--quick', action='store_true', help='仅快速检查（资产完整性）')
    parser.add_argument('--baseline', action='store_true', help='更新基线（暂未实现）')
    args = parser.parse_args()

    print("╔" + "═" * 58 + "╗")
    print("║  五大联赛专属模型 - CI 检查 " + " " * 28 + "║")
    print("║  阶段六 D-020 | " + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + " " * 22 + "║")
    print("╚" + "═" * 58 + "╝")

    results = []

    # 1. 资产完整性
    results.append(('资产完整性', check_asset_integrity()))

    if not args.quick:
        # 2. 性能回归基线
        results.append(('性能基线', check_performance_baseline()))

        # 3. 快速单元测试
        results.append(('单元测试', run_quick_tests()))

    # 汇总
    print("\n" + "=" * 60)
    print("📋 CI 检查汇总")
    print("=" * 60)
    all_pass = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    print("=" * 60)
    if all_pass:
        print("🎉 所有 CI 检查通过！")
        sys.exit(0)
    else:
        print("⚠️  CI 检查未通过，请修复后重试")
        sys.exit(1)


if __name__ == '__main__':
    main()
