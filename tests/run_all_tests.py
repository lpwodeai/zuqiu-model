"""
自动化测试主运行器
==================

阶段六：测试扩展

用法：
    # 运行所有测试
    python tests/run_all_tests.py

    # 仅运行单元测试
    python tests/run_all_tests.py --suite unit

    # 仅运行集成测试
    python tests/run_all_tests.py --suite integration

    # 仅运行端到端测试
    python tests/run_all_tests.py --suite e2e

    # 详细输出
    python tests/run_all_tests.py -v

也可使用 pytest：
    python -m pytest tests/ -v
    python -m pytest tests/unit/ -v
"""

import os
import sys
import time
import argparse
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, 'scripts')
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(TESTS_DIR, 'fixtures'))


def discover_tests(suite_name):
    """发现指定测试套件的测试用例"""
    if suite_name == 'all':
        start_dir = TESTS_DIR
    else:
        start_dir = os.path.join(TESTS_DIR, suite_name)
        if not os.path.isdir(start_dir):
            print(f"错误：测试套件目录不存在 - {start_dir}")
            return None

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir, pattern='test_*.py', top_level_dir=TESTS_DIR)
    return suite


def run_suite(suite, verbosity=2):
    """运行测试套件并返回结果"""
    runner = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stdout)
    start_time = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_time
    return result, elapsed


def print_summary(result, elapsed, suite_name):
    """打印测试汇总"""
    print("\n" + "=" * 60)
    print(f"测试汇总 - {suite_name.upper()}")
    print("=" * 60)
    print(f"  运行测试数:  {result.testsRun}")
    print(f"  成功:        {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"  失败:        {len(result.failures)}")
    print(f"  错误:        {len(result.errors)}")
    print(f"  跳过:        {len(result.skipped)}")
    print(f"  耗时:        {elapsed:.2f}s")
    if result.failures:
        print(f"\n  失败用例:")
        for test, _ in result.failures:
            print(f"    - {test}")
    if result.errors:
        print(f"\n  错误用例:")
        for test, _ in result.errors:
            print(f"    - {test}")
    print("=" * 60)
    return result.wasSuccessful()


def main():
    parser = argparse.ArgumentParser(description='五大联赛专属模型 - 自动化测试运行器')
    parser.add_argument('--suite', choices=['all', 'unit', 'integration', 'e2e'],
                        default='all', help='测试套件 (默认: all)')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    args = parser.parse_args()

    verbosity = 2 if args.verbose else 1

    print("=" * 60)
    print("五大联赛专属模型 - 自动化测试框架")
    print("阶段六：测试扩展")
    print("=" * 60)
    print(f"  测试套件: {args.suite}")
    print(f"  详细输出: {args.verbose}")
    print(f"  项目根: {PROJECT_ROOT}")
    print(f"  测试目录: {TESTS_DIR}")

    suite = discover_tests(args.suite)
    if suite is None:
        sys.exit(1)

    result, elapsed = run_suite(suite, verbosity)
    success = print_summary(result, elapsed, args.suite)

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
