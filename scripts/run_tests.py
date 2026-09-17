import os
import sys
import subprocess
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR = os.path.join(PROJECT_ROOT, 'tests')
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, 'scripts')


def run_unit_tests():
    print("=" * 60)
    print("运行单元测试")
    print("=" * 60)
    result = subprocess.run(
        [sys.executable, '-m', 'unittest', 'tests.test_auto_train', '-v'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    print(result.stdout)
    if result.stderr:
        print("错误输出:", result.stderr)
    return result.returncode == 0


def run_integration_tests():
    print("\n" + "=" * 60)
    print("运行集成测试")
    print("=" * 60)
    result = subprocess.run(
        [sys.executable, '-m', 'unittest', 'tests.test_integration', '-v'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    print(result.stdout)
    if result.stderr:
        print("错误输出:", result.stderr)
    return result.returncode == 0


def run_all_tests():
    print("\n" + "=" * 60)
    print("运行所有测试")
    print("=" * 60)
    result = subprocess.run(
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    print(result.stdout)
    if result.stderr:
        print("错误输出:", result.stderr)
    return result.returncode == 0


def run_test_for_file(file_name):
    print("\n" + "=" * 60)
    print(f"运行测试: {file_name}")
    print("=" * 60)
    
    test_module = f"tests.{os.path.splitext(file_name)[0]}"
    result = subprocess.run(
        [sys.executable, '-m', 'unittest', test_module, '-v'],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True
    )
    print(result.stdout)
    if result.stderr:
        print("错误输出:", result.stderr)
    return result.returncode == 0


def list_tests():
    print("可用测试文件:")
    test_files = sorted([f for f in os.listdir(TESTS_DIR) if f.startswith('test_') and f.endswith('.py')])
    for f in test_files:
        print(f"  - {f}")


def main():
    parser = argparse.ArgumentParser(description="运行项目测试")
    parser.add_argument('-u', '--unit', action='store_true', help='运行单元测试')
    parser.add_argument('-i', '--integration', action='store_true', help='运行集成测试')
    parser.add_argument('-a', '--all', action='store_true', help='运行所有测试')
    parser.add_argument('-f', '--file', type=str, help='运行指定测试文件')
    parser.add_argument('-l', '--list', action='store_true', help='列出可用测试')
    
    args = parser.parse_args()
    
    if args.list:
        list_tests()
        return
    
    success = True
    
    if args.unit:
        success = success and run_unit_tests()
    
    if args.integration:
        success = success and run_integration_tests()
    
    if args.all:
        success = run_all_tests()
    
    if args.file:
        success = run_test_for_file(args.file)
    
    if not (args.unit or args.integration or args.all or args.file):
        print("未指定测试类型，默认运行所有测试")
        success = run_all_tests()
    
    print("\n" + "=" * 60)
    if success:
        print("✓ 所有测试通过")
    else:
        print("✗ 部分测试失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
