"""
T-005 v3 自动重训触发器执行引擎
==============================

读取 deploy_trigger.flag 配置，实现三重触发机制:
  1. 定时触发 (weekly) — 每周一早 8:00 检查
  2. 数据触发 (new_data) — 新比赛数据入库时检查
  3. 周期触发 (periodic) — 距离上次重训超过 7 天

性能门禁: 走水召回率 ≥ min_draw_recall 且预测率偏差 ≤ max_rate_deviation

用法:
  python retrain_trigger_runner.py check         # 检查是否需要重训
  python retrain_trigger_runner.py trigger       # 强制触发重训
  python retrain_trigger_runner.py daemon        # 守护模式（每小时检查一次）
  python retrain_trigger_runner.py status        # 查看触发器状态
  python retrain_trigger_runner.py schedule      # 安装 Windows 定时任务
"""

import sys
import os
import json
import subprocess
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger('trigger_runner')

BASE_DIR = Path(__file__).resolve().parent.parent
DEPLOYMENT_DIR = BASE_DIR / 'deployment'
ASSETS_DIR = BASE_DIR / 'assets'
REPORTS_DIR = BASE_DIR / 'reports'
SCRIPTS_DIR = BASE_DIR / 'scripts'
DATA_DIR = BASE_DIR / 'data'

TRIGGER_FILE = DEPLOYMENT_DIR / 'deploy_trigger.flag'
STATE_FILE = DEPLOYMENT_DIR / 'trigger_state.json'
LATEST_MODEL_FILE = DEPLOYMENT_DIR / 'latest_model.json'
DB_PATH = DATA_DIR / 'odds.db'

DEFAULT_CONFIG = {
    'auto_retrain': {
        'enabled': True,
        'schedule': 'weekly (every Monday)',
        'retrain_script': 'scripts/deploy_t005v3_final.py',
        'condition': 'new_data_available OR 7_days_elapsed',
        'performance_gate': {
            'min_draw_recall': 0.30,
            'max_rate_deviation': 0.02,
        }
    }
}


def load_trigger_config():
    """加载触发器配置"""
    if not TRIGGER_FILE.exists():
        log.warning(f"触发器配置文件不存在: {TRIGGER_FILE}")
        log.info("使用默认配置")
        return DEFAULT_CONFIG
    try:
        with open(TRIGGER_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        log.info(f"已加载触发器配置: 触发类型={config.get('trigger_type')}, 自动重训={config.get('auto_retrain', {}).get('enabled', False)}")
        return config
    except (json.JSONDecodeError, IOError) as e:
        log.error(f"触发器配置文件损坏: {e}")
        return DEFAULT_CONFIG


def load_state():
    """加载触发器状态"""
    if not STATE_FILE.exists():
        return {
            'last_check_time': None,
            'last_retrain_time': None,
            'last_retrain_result': None,
            'last_new_data_count': 0,
            'total_retrains': 0,
            'total_successes': 0,
            'total_failures': 0,
            'consecutive_failures': 0,
        }
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {'last_check_time': None}


def save_state(state):
    """保存触发器状态"""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2, default=str)


def get_db_row_count():
    """获取数据库中的比赛记录总数（新数据检测）
    
    检查 matches 表总记录数 + handicap_history 最新日期
    """
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()

        # 检查 matches 表
        try:
            cursor.execute("SELECT COUNT(*) FROM matches")
            match_count = cursor.fetchone()[0]
        except sqlite3.OperationalError:
            match_count = 0

        # 检查 handicap_history 最新日期
        latest_date = None
        try:
            cursor.execute("SELECT MAX(date) FROM handicap_history")
            row = cursor.fetchone()
            if row and row[0]:
                latest_date = str(row[0])
        except sqlite3.OperationalError:
            pass

        # 检查 wdl_history 最新日期
        try:
            cursor.execute("SELECT MAX(date) FROM wdl_history")
            row = cursor.fetchone()
            if row and row[0]:
                wdl_date = str(row[0])
                if latest_date is None or wdl_date > latest_date:
                    latest_date = wdl_date
        except sqlite3.OperationalError:
            pass

        conn.close()

        # 返回组合计数: match_count + 日期字符串
        # 用哈希确保变更检测
        count = match_count
        if latest_date:
            count = hash(f"{match_count}_{latest_date}")

        log.info(f"数据库状态: matches={match_count}, latest_date={latest_date}")
        return count

    except Exception as e:
        log.warning(f"无法读取数据库: {e}")
        return None


def check_time_trigger(state):
    """检查定时触发（每周一）"""
    now = datetime.now()
    day_of_week = now.weekday()  # 0=Monday
    hour = now.hour
    last_check = state.get('last_check_time')

    if last_check:
        last_check_dt = datetime.fromisoformat(last_check)
        last_check_date = last_check_dt.date()
        today = now.date()
        if last_check_date == today:
            log.info(f"今日已检查过定时触发，跳过")
            return {'triggered': False, 'reason': '今日已检查'}

    if day_of_week == 0 and hour >= 8:
        log.info(f"定时触发: 周一 {hour}:00 已到达检查时间")
        return {'triggered': True, 'reason': f'周一 {hour}:00 定时触发'}

    log.info(f"定时触发检查: 当前周{day_of_week+1}, {hour}:00, 非触发时间")
    return {'triggered': False, 'reason': f'非触发时间 (周{day_of_week+1} {hour}:00)'}


def check_data_trigger(state):
    """检查数据触发（新数据入库）"""
    current_count = get_db_row_count()
    last_count = state.get('last_new_data_count', 0)

    if current_count is None:
        return {'triggered': False, 'reason': '数据库不可用', 'current_count': 0}

    if last_count == 0:
        log.info(f"数据触发检查: 首次检查，记录基线 {current_count} 条")
        return {'triggered': False, 'reason': f'首次检查，记录基线', 'current_count': current_count}

    new_data = current_count - last_count
    if new_data > 0:
        log.info(f"数据触发: 检测到 {new_data} 条新数据 (当前={current_count}, 上次={last_count})")
        return {'triggered': True, 'reason': f'新数据入库: +{new_data} 条', 'current_count': current_count}

    log.info(f"数据触发检查: 无新数据 (当前={current_count}, 上次={last_count})")
    return {'triggered': False, 'reason': f'无新数据 (={current_count})', 'current_count': current_count}


def check_periodic_trigger(state):
    """检查周期触发（7天间隔）"""
    last_retrain = state.get('last_retrain_time')
    if not last_retrain:
        log.info("周期触发: 从未重训，需要立即重训")
        return {'triggered': True, 'reason': '从未重训'}

    last_retrain_dt = datetime.fromisoformat(last_retrain)
    days_elapsed = (datetime.now() - last_retrain_dt).days

    if days_elapsed >= 7:
        log.info(f"周期触发: 距上次重训 {days_elapsed} 天 >= 7 天阈值")
        return {'triggered': True, 'reason': f'周期触发: {days_elapsed} 天未重训'}

    log.info(f"周期触发检查: 距上次重训 {days_elapsed}/7 天")
    return {'triggered': False, 'reason': f'{days_elapsed}/7 天'}


def check_performance_gate(config, metrics):
    """检查性能门禁"""
    gate = config.get('auto_retrain', {}).get('performance_gate', {})
    min_recall = gate.get('min_draw_recall', 0.30)
    max_deviation = gate.get('max_rate_deviation', 0.02)

    recall = metrics.get('draw_recall', 0)
    deviation = metrics.get('rate_deviation', 1.0)

    recall_ok = recall >= min_recall
    deviation_ok = deviation <= max_deviation

    passed = recall_ok and deviation_ok
    reasons = []
    if not recall_ok:
        reasons.append(f"召回率 {recall:.4f} < {min_recall}")
    if not deviation_ok:
        reasons.append(f"偏差 {deviation:.4f} > {max_deviation}")

    return {
        'passed': passed,
        'recall_ok': recall_ok,
        'deviation_ok': deviation_ok,
        'recall': recall,
        'deviation': deviation,
        'reasons': reasons,
    }


def run_retrain(config, state, force=False):
    """执行重训"""
    retrain_script = config.get('auto_retrain', {}).get('retrain_script', 'scripts/deploy_t005v3_final.py')
    script_path = BASE_DIR / retrain_script

    if not script_path.exists():
        log.error(f"重训脚本不存在: {script_path}")
        return {'success': False, 'error': f'脚本不存在: {script_path}'}

    log.info(f"开始重训: {script_path}")
    log.info(f"  工作目录: {BASE_DIR}")
    log.info(f"  强制模式: {force}")

    start_time = time.time()

    try:
        cmd = [sys.executable, str(script_path)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
            timeout=600,
            encoding='utf-8',
        )

        elapsed = time.time() - start_time

        if result.returncode == 0:
            log.info(f"重训成功! 耗时 {elapsed:.1f}s")
            log.info(f"STDOUT:\n{result.stdout[-500:]}")

            metrics = parse_metrics_from_output(result.stdout)
            gate_result = check_performance_gate(config, metrics)

            if gate_result['passed'] or force:
                log.info(f"性能门禁检查: {'通过' if gate_result['passed'] else '强制通过（force=True）'}")
                if not gate_result['passed'] and force:
                    log.warning(f"性能门禁未通过: {'; '.join(gate_result['reasons'])}")
                return {
                    'success': True,
                    'elapsed': elapsed,
                    'metrics': metrics,
                    'gate': gate_result,
                }
            else:
                log.error(f"性能门禁未通过: {'; '.join(gate_result['reasons'])}")
                return {
                    'success': False,
                    'error': f"性能门禁未通过: {'; '.join(gate_result['reasons'])}",
                    'elapsed': elapsed,
                    'metrics': metrics,
                    'gate': gate_result,
                }
        else:
            log.error(f"重训失败! 耗时 {elapsed:.1f}s")
            log.error(f"STDERR:\n{result.stderr[-500:]}")
            return {
                'success': False,
                'error': result.stderr[-200:],
                'elapsed': elapsed,
            }

    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        log.error(f"重训超时! 耗时 {elapsed:.1f}s")
        return {'success': False, 'error': '执行超时 (> 600s)', 'elapsed': elapsed}

    except Exception as e:
        elapsed = time.time() - start_time
        log.error(f"重训异常: {e}")
        return {'success': False, 'error': str(e), 'elapsed': elapsed}


def parse_metrics_from_output(output):
    """从重训脚本输出中解析关键指标"""
    metrics = {
        'draw_recall': None,
        'draw_precision': None,
        'rate_deviation': None,
        'accuracy': None,
        'f1_macro': None,
    }

    for line in output.split('\n'):
        line = line.strip()
        if '走水召回率:' in line:
            try:
                val = float(line.split(':')[1].strip().split()[0])
                metrics['draw_recall'] = val
            except (ValueError, IndexError):
                pass
        elif '走水精确率:' in line:
            try:
                val = float(line.split(':')[1].strip().split()[0])
                metrics['draw_precision'] = val
            except (ValueError, IndexError):
                pass
        elif '预测率偏差:' in line:
            try:
                val = float(line.split(':')[1].strip().split()[0].replace('+', ''))
                metrics['rate_deviation'] = abs(val)
            except (ValueError, IndexError):
                pass
        elif 'Accuracy:' in line:
            try:
                val = float(line.split(':')[1].strip().split()[0])
                metrics['accuracy'] = val
            except (ValueError, IndexError):
                pass
        elif 'F1 Macro:' in line:
            try:
                val = float(line.split(':')[1].strip().split()[0])
                metrics['f1_macro'] = val
            except (ValueError, IndexError):
                pass

    return metrics


def cmd_check(config, state):
    """检查所有触发条件"""
    print("=" * 60)
    print("🔍 T-005 v3 自动重训触发器检查")
    print("=" * 60)

    triggers = {}

    triggers['time'] = check_time_trigger(state)
    triggers['data'] = check_data_trigger(state)
    triggers['periodic'] = check_periodic_trigger(state)

    should_trigger = any(t['triggered'] for t in triggers.values())

    for name, result in triggers.items():
        icon = '✅' if result['triggered'] else '⏸️'
        name_map = {'time': '定时触发', 'data': '数据触发', 'periodic': '周期触发'}
        print(f"  {icon} {name_map.get(name, name)}: {'需要重训' if result['triggered'] else '无需重训'}")
        print(f"     原因: {result.get('reason', 'N/A')}")

    state['last_check_time'] = datetime.now().isoformat()
    if triggers['data'].get('current_count'):
        state['last_new_data_count'] = triggers['data']['current_count']
    save_state(state)

    print(f"\n  📋 结论: {'需要立即重训' if should_trigger else '暂不需重训'}")
    return should_trigger, triggers


def cmd_trigger(config, state):
    """强制触发重训"""
    print("=" * 60)
    print("🚀 强制触发 T-005 v3 重训")
    print("=" * 60)

    result = run_retrain(config, state, force=True)

    state['last_retrain_time'] = datetime.now().isoformat()
    state['last_retrain_result'] = 'success' if result['success'] else 'failed'
    state['last_retrain_metrics'] = result.get('metrics')
    state['total_retrains'] = state.get('total_retrains', 0) + 1
    if result['success']:
        state['total_successes'] = state.get('total_successes', 0) + 1
        state['consecutive_failures'] = 0
    else:
        state['total_failures'] = state.get('total_failures', 0) + 1
        state['consecutive_failures'] = state.get('consecutive_failures', 0) + 1

    save_state(state)

    if result['success']:
        gate = result.get('gate', {})
        print(f"\n  ✅ 重训完成!")
        print(f"     耗时: {result.get('elapsed', 0):.1f}s")
        m = result.get('metrics', {})
        if m:
            print(f"     走水召回率: {m.get('draw_recall', 'N/A')}")
            print(f"     走水精确率: {m.get('draw_precision', 'N/A')}")
            print(f"     预测率偏差: {m.get('rate_deviation', 'N/A')}")
        print(f"     性能门禁: {'通过' if gate.get('passed') else '强制通过'}")
    else:
        print(f"\n  ❌ 重训失败: {result.get('error', '未知错误')}")

    return result


def cmd_daemon(config, state):
    """守护模式：每小时检查一次"""
    print("=" * 60)
    print("🌀 T-005 v3 重训触发器守护模式")
    print("=" * 60)
    print("  检查间隔: 60 分钟")
    print("  按 Ctrl+C 退出")
    print("-" * 60)

    try:
        while True:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            should_trigger, triggers = cmd_check(config, state)

            if should_trigger:
                print(f"\n  [{now}] 检测到触发条件，开始重训...")
                cmd_trigger(config, state)
            else:
                print(f"  [{now}] 暂不需重训")

            print(f"  下次检查: 60 分钟后")
            time.sleep(3600)

    except KeyboardInterrupt:
        print("\n\n守护模式已退出")


def cmd_status(config, state):
    """显示触发器状态"""
    print("=" * 60)
    print("📊 T-005 v3 触发器状态")
    print("=" * 60)

    print(f"\n  触发器配置:")
    ar = config.get('auto_retrain', {})
    print(f"    自动重训: {'启用' if ar.get('enabled') else '禁用'}")
    print(f"    重训脚本: {ar.get('retrain_script', 'N/A')}")
    print(f"    触发条件: {ar.get('condition', 'N/A')}")

    gate = ar.get('performance_gate', {})
    print(f"    性能门禁: 召回率 ≥ {gate.get('min_draw_recall', 'N/A')}, 偏差 ≤ {gate.get('max_rate_deviation', 'N/A')}")

    print(f"\n  运行统计:")
    print(f"    上次检查: {state.get('last_check_time', '从未')}")
    print(f"    上次重训: {state.get('last_retrain_time', '从未')}")
    print(f"    上次结果: {state.get('last_retrain_result', 'N/A')}")
    print(f"    累计重训: {state.get('total_retrains', 0)} 次")
    print(f"    成功: {state.get('total_successes', 0)} | 失败: {state.get('total_failures', 0)}")
    print(f"    连续失败: {state.get('consecutive_failures', 0)} 次")

    last_metrics = state.get('last_retrain_metrics')
    if last_metrics:
        print(f"\n  上次重训指标:")
        print(f"    走水召回率: {last_metrics.get('draw_recall', 'N/A')}")
        print(f"    走水精确率: {last_metrics.get('draw_precision', 'N/A')}")
        print(f"    预测率偏差: {last_metrics.get('rate_deviation', 'N/A')}")

    # 检查各触发条件
    print(f"\n  当前触发条件状态:")
    triggers = {}
    triggers['time'] = check_time_trigger(state)
    triggers['data'] = check_data_trigger(state)
    triggers['periodic'] = check_periodic_trigger(state)

    for name, result in triggers.items():
        icon = '🔴' if result['triggered'] else '🟢'
        name_map = {'time': '定时触发', 'data': '数据触发', 'periodic': '周期触发'}
        print(f"    {icon} {name_map.get(name, name)}: {result.get('reason', 'N/A')}")

    # 最新模型
    if LATEST_MODEL_FILE.exists():
        try:
            with open(LATEST_MODEL_FILE, 'r', encoding='utf-8') as f:
                latest = json.load(f)
            print(f"\n  当前线上模型:")
            print(f"    版本: {latest.get('latest_version', 'N/A')}")
            print(f"    温度: {latest.get('calibration', {}).get('temperature', 'N/A')}")
            print(f"    阈值: {latest.get('calibration', {}).get('threshold', 'N/A')}")
            print(f"    更新时间: {latest.get('updated_at', 'N/A')}")
        except Exception:
            pass


def cmd_schedule(config, state):
    """安装 Windows 定时任务"""
    print("=" * 60)
    print("⏰ 安装 Windows 定时重训任务")
    print("=" * 60)

    python_exe = sys.executable
    runner_script = str(Path(__file__).resolve())
    task_name = "T005v3_AutoRetrain"

    cmd = [
        'schtasks', '/create',
        '/TN', task_name,
        '/TR', f'"{python_exe}" "{runner_script}" daemon',
        '/SC', 'DAILY',
        '/ST', '08:00',
        '/F',
    ]

    print(f"  将创建 Windows 定时任务:")
    print(f"    任务名: {task_name}")
    print(f"    执行时间: 每日 08:00")
    print(f"    执行脚本: {runner_script}")
    print(f"    Python: {python_exe}")

    print(f"\n  执行命令: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"\n  ✅ 定时任务创建成功!")
            print(f"     你可以通过 '任务计划程序' 查看和管理此任务")
        else:
            print(f"\n  ❌ 创建失败: {result.stderr.strip()}")
            print(f"     可能需要以管理员权限运行")
    except FileNotFoundError:
        print(f"\n  ⚠️ schtasks 命令不可用")
        print(f"     请手动在 '任务计划程序' 中创建每日 08:00 触发的任务")
        print(f"     执行: {python_exe} {runner_script} daemon")
    except Exception as e:
        print(f"\n  ❌ 执行异常: {e}")

    print(f"\n  备选方案：手动创建")
    print(f"    1. 打开 '任务计划程序' (taskschd.msc)")
    print(f"    2. 创建基本任务 → 每日 08:00")
    print(f"    3. 操作: 启动程序 → {python_exe}")
    print(f"       参数: \"{runner_script}\" daemon")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()
    config = load_trigger_config()
    state = load_state()

    commands = {
        'check': lambda: cmd_check(config, state),
        'trigger': lambda: cmd_trigger(config, state),
        'daemon': lambda: cmd_daemon(config, state),
        'status': lambda: cmd_status(config, state),
        'schedule': lambda: cmd_schedule(config, state),
    }

    if command in commands:
        commands[command]()
    else:
        print(f"未知命令: {command}")
        print(__doc__)


if __name__ == '__main__':
    main()
