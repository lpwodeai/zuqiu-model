#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
足球预测模型 - 项目启动脚本

功能:
    1. 自动加载项目上下文（通过日志检索）
    2. 显示当前状态摘要
    3. 生成启动上下文文件供 AI 助手使用
    4. 检查数据库和数据完整性
    5. 提供常用操作入口

使用方法:
    python run_startup.py              # 标准启动
    python run_startup.py --full       # 完整启动（含数据检查）
    python run_startup.py --context    # 仅生成上下文

输出:
    - 控制台状态摘要
    - logs/startup_context_YYYYMMDD_HHMMSS.md 启动上下文文件
    - logs/retrieval_YYYYMMDD_HHMMSS.json 检索结果
"""

import os
import sys
import time
import json
import logging
import traceback
import sqlite3
from datetime import datetime

# 配置日志
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_file = os.path.join(LOG_DIR, f'startup_{datetime.now().strftime("%Y%m%d")}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('startup')

# 获取脚本所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, 'scripts')
DOCS_DIR = os.path.join(BASE_DIR, 'docs')
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')

# 添加 scripts 目录到路径
sys.path.insert(0, SCRIPTS_DIR)


def print_banner():
    """打印启动横幅"""
    print("\n" + "=" * 70)
    print("🚀 足球预测模型 - 项目启动")
    print("=" * 70)
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  项目路径: {BASE_DIR}")
    print(f"  Python版本: {sys.version.split()[0]}")
    print("=" * 70)


def load_project_context():
    """加载项目上下文"""
    print("\n📖 正在加载项目上下文...")
    print("-" * 70)
    
    try:
        from log_retrieval import load_all_logs, log_step, save_results, generate_startup_context, LOG_FILES
        import time as time_module
        
        start_time = time_module.time()
        
        # 加载所有日志
        all_data = load_all_logs(verbose=False)
        
        elapsed = (time_module.time() - start_time) * 1000
        print(f"\n  ✅ 上下文加载完成 (耗时 {elapsed:.0f}ms)")
        
        return all_data
        
    except ImportError as e:
        print(f"  ❌ 无法导入 log_retrieval 模块: {e}")
        print(f"  💡 请确保 scripts/log_retrieval.py 存在且路径正确")
        return None
    except Exception as e:
        print(f"  ❌ 加载上下文失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def display_status_summary(all_data):
    """显示状态摘要"""
    print("\n📊 【项目状态摘要】")
    print("-" * 70)
    
    if not all_data:
        print("  ⚠️ 无可用数据")
        return
    
    # 当前阶段
    template = all_data.get('prompt_template', {})
    stage = template.get('current_stage', '未知')
    print(f"\n  🎯 当前阶段: {stage}")
    
    # 规则统计
    memory = all_data.get('project_memory', {})
    rules = memory.get('rules', {})
    total_rules = sum(len(v) for v in rules.values())
    learnings = memory.get('learnings', [])
    print(f"  📚 规则: {total_rules}条 | 经验: {len(learnings)}条")
    
    # 任务统计
    opt = all_data.get('optimization_log', {})
    pending = opt.get('pending_tasks', [])
    issues = opt.get('issues', [])
    recent_logs = opt.get('recent_logs', [])
    print(f"  📝 近期日志: {len(recent_logs)}条 | 待办: {len(pending)}项 | 问题: {len(issues)}个")
    
    # 变更统计
    change = all_data.get('change_log', {})
    recent_changes = change.get('recent_changes', [])
    print(f"  🔧 近30天变更: {len(recent_changes)}条")
    
    # 决策统计
    dec = all_data.get('key_decisions', {})
    pending_dec = dec.get('pending', [])
    in_progress_dec = dec.get('in_progress', [])
    completed_dec = dec.get('completed', [])
    print(f"  💡 决策: {len(pending_dec)}待实施 | {len(in_progress_dec)}实施中 | {len(completed_dec)}已完成")
    
    # 快速行动建议
    print("\n  ⚡ 快速操作:")
    if pending:
        print(f"    • 最优先待办: {pending[0][:50]}")
    if issues:
        print(f"    • 关键问题: [{issues[0]['id']}] {issues[0]['description'][:40]}...")
    if pending_dec:
        print(f"    • 待实施决策: [{pending_dec[0]['id']}] {pending_dec[0]['topic']}")


def check_database(max_retries=3, retry_delay=1.0):
    """
    检查数据库完整性
    
    Args:
        max_retries: 最大重试次数
        retry_delay: 初始重试间隔（秒），后续指数递增
    """
    print("\n🗄️ 【数据库检查】")
    print("-" * 70)
    
    databases = {
        'odds.db': {
            'path': os.path.join(DATA_DIR, 'odds.db'),
            'tables': ['matches', 'wdl_history', 'handicap_history', 'total_goals_history', 'score_history'],
            'league_column': 'match_type',  # odds.db 使用 match_type
        },
        'odds_timing.db': {
            'path': os.path.join(DATA_DIR, 'odds_timing.db'),
            'tables': ['matches', 'wdl_timing', 'handicap_timing', 'total_goals_timing', 'score_timing', 'match_results'],
            'league_column': 'league',  # odds_timing.db 使用 league
        },
    }
    
    db_check_results = []
    
    for db_name, db_info in databases.items():
        db_path = db_info['path']
        print(f"\n  📂 {db_name}:")
        logger.info(f"开始检查数据库: {db_name}")
        
        db_result = {
            'name': db_name,
            'path': db_path,
            'exists': False,
            'size_mb': 0,
            'tables': {},
            'league_distribution': [],
            'success': False,
            'error': None,
            'retries': 0,
        }
        
        # 检查文件是否存在
        if not os.path.exists(db_path):
            error_msg = f"数据库文件不存在: {db_path}"
            print(f"    ❌ {error_msg}")
            logger.error(error_msg)
            db_result['error'] = error_msg
            db_check_results.append(db_result)
            continue
        
        db_result['exists'] = True
        size_mb = os.path.getsize(db_path) / (1024 * 1024)
        db_result['size_mb'] = size_mb
        print(f"    ✅ 存在 ({size_mb:.1f}MB)")
        logger.info(f"数据库文件存在，大小: {size_mb:.1f}MB")
        
        # 带重试的数据库检查
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"数据库检查尝试 {attempt}/{max_retries}")
                
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # 检查表是否存在并获取记录数
                tables_result = {}
                for table in db_info['tables']:
                    try:
                        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                        exists = cursor.fetchone() is not None
                        
                        if exists:
                            cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
                            count = cursor.fetchone()[0]
                            tables_result[table] = {'exists': True, 'count': count}
                            print(f"      • {table}: {count:,} 条记录")
                            logger.info(f"  表 {table}: 存在, {count} 条记录")
                        else:
                            tables_result[table] = {'exists': False, 'count': 0}
                            print(f"      • {table}: ❌ 表不存在")
                            logger.warning(f"  表 {table}: 不存在")
                    except sqlite3.OperationalError as e:
                        tables_result[table] = {'exists': 'error', 'count': 0, 'error': str(e)}
                        error_detail = f"表 {table} 检查失败: {e}"
                        print(f"      • {table}: ❌ 错误 - {e}")
                        logger.error(error_detail)
                        last_error = str(e)
                
                db_result['tables'] = tables_result
                
                # 获取联赛分布
                league_col = db_info.get('league_column', 'league')
                try:
                    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='matches'")
                    if cursor.fetchone():
                        cursor.execute(f"SELECT [{league_col}], COUNT(*) FROM matches GROUP BY [{league_col}] ORDER BY COUNT(*) DESC")
                        leagues = cursor.fetchall()
                        if leagues:
                            db_result['league_distribution'] = [(l, c) for l, c in leagues]
                            print(f"\n      📊 联赛分布:")
                            for league, count in leagues:
                                print(f"        • {league}: {count} 场")
                                logger.info(f"    联赛 {league}: {count} 场")
                except Exception as e:
                    logger.warning(f"获取联赛分布失败: {e}")
                    last_error = str(e)
                
                conn.close()
                
                # 如果到达这里，说明成功
                db_result['success'] = True
                db_result['retries'] = attempt - 1
                logger.info(f"数据库 {db_name} 检查成功 (第{attempt}次尝试)")
                break  # 成功则跳出重试循环
                
            except sqlite3.Error as e:
                last_error = f"SQLite错误: {e}"
                logger.error(f"数据库 {db_name} 检查失败 (第{attempt}次尝试): {e}")
                logger.error(f"错误堆栈:\n{traceback.format_exc()}")
                
                if attempt < max_retries:
                    delay = retry_delay * (2 ** (attempt - 1))  # 指数退避
                    print(f"    ⚠️ 检查失败，{delay:.1f}秒后重试... (第{attempt}/{max_retries}次)")
                    logger.info(f"等待 {delay:.1f} 秒后重试...")
                    time.sleep(delay)
                else:
                    print(f"    ❌ 检查失败，已达最大重试次数")
                    logger.error(f"数据库 {db_name} 检查最终失败，已重试 {max_retries} 次")
                    
            except Exception as e:
                last_error = f"未知错误: {e}"
                logger.error(f"数据库 {db_name} 检查出现未知错误: {e}")
                logger.error(f"错误堆栈:\n{traceback.format_exc()}")
                
                if attempt < max_retries:
                    delay = retry_delay * (2 ** (attempt - 1))
                    print(f"    ⚠️ 未知错误，{delay:.1f}秒后重试... (第{attempt}/{max_retries}次)")
                    time.sleep(delay)
                else:
                    print(f"    ❌ 未知错误，已达最大重试次数")
                    logger.error(f"数据库 {db_name} 检查最终失败")
        
        if not db_result['success']:
            db_result['error'] = last_error
            print(f"    ❌ 最终错误: {last_error}")
            logger.error(f"数据库 {db_name} 最终错误: {last_error}")
        
        db_check_results.append(db_result)
    
    # 汇总报告
    print("\n" + "=" * 70)
    print("📊 数据库检查汇总")
    print("=" * 70)
    
    total_tables_checked = 0
    total_tables_ok = 0
    total_tables_error = 0
    total_records = 0
    failed_databases = []
    
    for result in db_check_results:
        status = "✅ 成功" if result['success'] else "❌ 失败"
        retries_info = f" (重试{result['retries']}次)" if result['retries'] > 0 else ""
        print(f"\n  {status} {result['name']}{retries_info}:")
        
        if result['error']:
            print(f"    错误: {result['error'][:100]}")
        
        for table_name, table_info in result.get('tables', {}).items():
            total_tables_checked += 1
            if table_info['exists'] is True:
                total_tables_ok += 1
                total_records += table_info['count']
            elif table_info['exists'] == 'error':
                total_tables_error += 1
        
        if not result['success']:
            failed_databases.append(result['name'])
    
    print(f"\n  📈 统计:")
    print(f"    • 检查数据库: {len(db_check_results)} 个")
    print(f"    • 成功: {len(db_check_results) - len(failed_databases)} 个")
    print(f"    • 失败: {len(failed_databases)} 个 {failed_databases if failed_databases else ''}")
    print(f"    • 检查表: {total_tables_checked} 个")
    print(f"    • 表正常: {total_tables_ok} 个")
    print(f"    • 表异常: {total_tables_error} 个")
    print(f"    • 总记录数: {total_records:,}")
    
    logger.info(f"数据库检查汇总: {len(db_check_results)}个数据库, {total_tables_checked}张表, {total_records:,}条记录")
    
    if failed_databases:
        logger.error(f"失败的数据库: {failed_databases}")
        print(f"\n  ⚠️ 部分数据库检查失败，请查看日志: {log_file}")
    else:
        print(f"\n  ✅ 所有数据库检查通过！")
    
    return db_check_results


def generate_context_file(all_data):
    """生成启动上下文文件"""
    print("\n📝 生成启动上下文文件...")
    print("-" * 70)
    
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    context_file = os.path.join(LOGS_DIR, f'startup_context_{timestamp}.md')
    
    try:
        # 生成 Markdown 上下文
        lines = []
        lines.append("# 足球预测模型 - 启动上下文")
        lines.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"> 启动脚本: run_startup.py")
        lines.append("")
        
        # 当前阶段
        template = all_data.get('prompt_template', {})
        stage = template.get('current_stage', '未知')
        lines.append(f"\n## 🎯 当前阶段: {stage}")
        
        # 关键规则（精简版）
        memory = all_data.get('project_memory', {})
        rules = memory.get('rules', {})
        if rules:
            lines.append("\n## 🔒 关键规则")
            type_map = {'data': '数据', 'feature': '特征', 'model': '模型', 'security': '安全'}
            for rule_type, rule_list in rules.items():
                if rule_list:
                    lines.append(f"\n### {type_map.get(rule_type, rule_type)}规则")
                    for r in rule_list[:2]:
                        lines.append(f"- [{r['id']}] {r['content']}")
        
        # 待办事项
        opt = all_data.get('optimization_log', {})
        pending = opt.get('pending_tasks', [])
        if pending:
            lines.append(f"\n## 📋 待办事项 ({len(pending)}项)")
            for task in pending[:15]:
                lines.append(f"- [ ] {task}")
        
        # 关键决策
        dec = all_data.get('key_decisions', {})
        pending_dec = dec.get('pending', [])
        if pending_dec:
            lines.append(f"\n## 💡 待实施决策 ({len(pending_dec)}项)")
            for d in pending_dec[:8]:
                lines.append(f"- [{d['id']}] {d['topic']}")
                lines.append(f"  - {d['content'][:80]}")
        
        # 近期变更
        change = all_data.get('change_log', {})
        recent_changes = change.get('recent_changes', [])
        if recent_changes:
            lines.append(f"\n## 🔧 近期变更 (近30天, {len(recent_changes)}条)")
            for c in recent_changes[-5:]:
                lines.append(f"- [{c['date']}] [{c['type']}] {c['description'][:60]}")
        
        # 问题清单
        issues = opt.get('issues', [])
        if issues:
            lines.append(f"\n## ⚠️ 待解决问题 ({len(issues)}项)")
            for issue in issues[:10]:
                lines.append(f"- [{issue['id']}] [{issue['status']}] {issue['description'][:50]}")
        
        # 经验教训
        learnings = memory.get('learnings', [])
        if learnings:
            lines.append(f"\n## 💡 经验教训 ({len(learnings)}条)")
            for exp in learnings:
                lines.append(f"- [{exp['id']}] {exp['description'][:60]}")
                lines.append(f"  - 日期: {exp['date']}, 影响: {exp['impact']}")
        
        lines.append("\n---")
        lines.append(f"\n*此文件由 run_startup.py 自动生成，每日对话前请执行 `python scripts/log_retrieval.py` 获取最新上下文*")
        
        # 写入文件
        with open(context_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        
        print(f"  ✅ 上下文文件已生成: {context_file}")
        return context_file
        
    except Exception as e:
        print(f"  ❌ 生成上下文文件失败: {e}")
        return None


def save_retrieval_data(all_data):
    """保存检索数据"""
    print("\n💾 保存检索数据...")
    print("-" * 70)
    
    try:
        from log_retrieval import save_results
        
        output_file = save_results(all_data)
        if output_file:
            print(f"  ✅ 检索数据已保存: {output_file}")
        return output_file
    except Exception as e:
        print(f"  ❌ 保存检索数据失败: {e}")
        return None


def print_help():
    """打印帮助信息"""
    print("\n📖 【使用说明】")
    print("-" * 70)
    print("""
  常用命令:
    python run_startup.py              # 标准启动（加载上下文+状态摘要）
    python run_startup.py --full       # 完整启动（含数据库检查）
    python run_startup.py --context    # 仅生成上下文文件
    python scripts/log_retrieval.py   # 日志检索（详细模式）
    python scripts/log_retrieval.py --verbose --save  # 详细检索并保存
  
  关键文件:
    docs/project_memory.md    # 项目记忆（规则、经验）
    docs/optimization_log.md   # 优化日志（任务、问题）
    docs/change_log.md        # 变更日志（代码变更）
    docs/key_decisions.md     # 决策日志（关键决策）
    docs/prompt_template.md   # 上下文模板
  
  生成文件:
    logs/startup_context_*.md # 启动上下文
    logs/retrieval_*.json     # 检索结果
""")


def main():
    """主启动函数"""
    # 解析命令行参数
    full_mode = '--full' in sys.argv
    context_only = '--context' in sys.argv
    help_mode = '--help' in sys.argv or '-h' in sys.argv
    
    if help_mode:
        print_help()
        return
    
    # 打印横幅
    print_banner()
    
    # 1. 加载项目上下文
    all_data = load_project_context()
    
    if all_data:
        # 2. 显示状态摘要
        display_status_summary(all_data)
        
        # 3. 生成上下文文件
        context_file = generate_context_file(all_data)
        
        # 4. 保存检索数据
        if not context_only:
            save_retrieval_data(all_data)
    
    # 5. 完整模式 - 检查数据库
    if full_mode:
        check_database()
    
    # 完成
    print("\n" + "=" * 70)
    print("✅ 项目启动完成！")
    print("=" * 70)
    
    if all_data:
        print("\n💡 下一步:")
        print("  • 查看启动上下文文件")
        print("  • 执行具体任务")
        print("  • 任务完成后更新日志文件")
    
    print()


if __name__ == '__main__':
    main()
