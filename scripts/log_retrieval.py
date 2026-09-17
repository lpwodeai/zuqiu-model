#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日志检索脚本 - 每日对话前快速检索项目日志

使用方法:
    python scripts/log_retrieval.py          # 显示检索摘要
    python scripts/log_retrieval.py --verbose  # 详细模式，显示完整决策和变更
    python scripts/log_retrieval.py --save     # 保存检索结果到文件
    python scripts/log_retrieval.py --startup  # 启动模式，输出适合加载的上下文

输出:
    格式化的日志摘要，包含:
    1. 项目状态总览
    2. 关键规则提醒
    3. 最新任务完成情况
    4. 待办事项列表
    5. 最近变更记录
    6. 关键决策状态
    7. 检索过程日志
"""

import os
import re
import sys
import json
import time
from datetime import datetime, timedelta
from collections import defaultdict

# 配置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(BASE_DIR, 'docs')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')

LOG_FILES = {
    'project_memory': {
        'path': os.path.join(DOCS_DIR, 'project_memory.md'),
        'name': '项目记忆',
        'desc': '核心规则、工程约定、经验教训',
    },
    'optimization_log': {
        'path': os.path.join(DOCS_DIR, 'optimization_log.md'),
        'name': '优化日志',
        'desc': '每日任务日志、问题追踪',
    },
    'change_log': {
        'path': os.path.join(DOCS_DIR, 'change_log.md'),
        'name': '变更日志',
        'desc': '代码/参数/配置变更记录',
    },
    'key_decisions': {
        'path': os.path.join(DOCS_DIR, 'key_decisions.md'),
        'name': '决策日志',
        'desc': '关键决策及其状态',
    },
    'prompt_template': {
        'path': os.path.join(DOCS_DIR, 'prompt_template.md'),
        'name': '上下文模板',
        'desc': '当前阶段标识、待办事项',
    },
}

# 全局日志记录
retrieval_log = []


def log_step(step, message, level='info'):
    """记录检索过程日志"""
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    log_entry = {
        'timestamp': timestamp,
        'step': step,
        'message': message,
        'level': level,
    }
    retrieval_log.append(log_entry)
    
    level_icons = {
        'info': 'ℹ️',
        'success': '✅',
        'warning': '⚠️',
        'error': '❌',
        'start': '▶️',
        'end': '⏹️',
    }
    icon = level_icons.get(level, '📝')
    print(f"  [{timestamp}] {icon} [{step}] {message}")


def read_file(filepath):
    """读取文件内容"""
    if not os.path.exists(filepath):
        log_step('READ', f"文件不存在: {filepath}", 'warning')
        return None
    try:
        file_size = os.path.getsize(filepath)
        log_step('READ', f"读取文件: {os.path.basename(filepath)} ({file_size} bytes)", 'start')
        
        start_time = time.time()
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        elapsed = (time.time() - start_time) * 1000
        
        line_count = content.count('\n') + 1
        char_count = len(content)
        log_step('READ', f"读取成功: {line_count}行, {char_count}字符, 耗时{elapsed:.1f}ms", 'success')
        return content
    except Exception as e:
        log_step('READ', f"读取失败: {e}", 'error')
        return None


def extract_section(content, section_header):
    """提取指定章节的内容"""
    if not content:
        return None
    pattern = f'## {re.escape(section_header)}.*?(?=\\n## |\\n### |$)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(0)
    return None


def extract_today_logs(content, days=7):
    """提取最近N天的日志"""
    if not content:
        return []
    
    recent_logs = []
    today = datetime.now()
    date_pattern = r'(\d{4}-\d{2}-\d{2})'
    lines = content.split('\n')
    
    current_date = None
    current_section = []
    
    for line in lines:
        date_match = re.search(date_pattern, line)
        if date_match:
            if current_date and current_section:
                log_date = datetime.strptime(current_date, '%Y-%m-%d')
                if (today - log_date).days <= days:
                    recent_logs.append({
                        'date': current_date,
                        'content': '\n'.join(current_section).strip()
                    })
            current_date = date_match.group(1)
            current_section = [line]
        elif current_date:
            current_section.append(line)
    
    if current_date and current_section:
        log_date = datetime.strptime(current_date, '%Y-%m-%d')
        if (today - log_date).days <= days:
            recent_logs.append({
                'date': current_date,
                'content': '\n'.join(current_section).strip()
            })
    
    return recent_logs


def extract_pending_tasks(content):
    """提取待办事项"""
    if not content:
        return []
    
    tasks = []
    pending_patterns = [
        r'⬜\s*(.+?)(?:\n|$)',
        r'待开始\s*\|(.+?)\|',
        r'待实施\s*\|(.+?)\|',
    ]
    
    for pattern in pending_patterns:
        matches = re.findall(pattern, content)
        tasks.extend([m.strip() for m in matches if m.strip()])
    
    return list(set(tasks))  # 去重


def extract_rules(content, keyword=None):
    """提取规则"""
    if not content:
        return []
    
    rules = []
    rule_pattern = r'\|\s*(DATA-\d+|FEAT-\d+|MODEL-\d+|SECURITY-\d+)\s*\|\s*(.+?)\s*\|'
    matches = re.findall(rule_pattern, content)
    
    for rule_id, rule_content in matches:
        if keyword is None or keyword in rule_content or keyword in rule_id:
            rules.append({
                'id': rule_id,
                'content': rule_content.strip()
            })
    
    return rules


def extract_issues(content, status=None):
    """提取问题"""
    if not content:
        return []
    
    issues = []
    issue_pattern = r'\|\s*(P\d-\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|'
    matches = re.findall(issue_pattern, content)
    
    for issue_id, description, source, status_val, plan_date, actual_date in matches:
        if status is None or status in status_val:
            issues.append({
                'id': issue_id,
                'description': description.strip(),
                'status': status_val,
                'source': source,
                'plan_date': plan_date,
                'actual_date': actual_date,
            })
    
    return issues


def extract_decisions(content, status=None):
    """提取决策"""
    if not content:
        return []
    
    decisions = []
    decision_pattern = r'\|\s*(D-\d{8}-\d+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\S+)\s*\|'
    matches = re.findall(decision_pattern, content)
    
    for dec_id, date, topic, content_val, status_val in matches:
        if status is None or status in status_val:
            decisions.append({
                'id': dec_id,
                'date': date,
                'topic': topic.strip(),
                'content': content_val.strip(),
                'status': status_val,
            })
    
    return decisions


def extract_changes(content, days=7):
    """提取变更详情"""
    if not content:
        return []
    
    changes = []
    recent_logs = extract_today_logs(content, days)
    
    for log in recent_logs:
        change_pattern = r'\|\s*(C-\d{8}-\d+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|'
        matches = re.findall(change_pattern, log['content'])
        
        for chg_id, date, type_val, desc_val, related, operator in matches:
            changes.append({
                'id': chg_id,
                'date': date,
                'type': type_val,
                'description': desc_val.strip(),
                'related': related,
                'operator': operator,
            })
    
    return changes


def extract_learning_points(content):
    """提取经验教训"""
    if not content:
        return []
    
    learnings = []
    learning_pattern = r'\|\s*(EXP-\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|'
    matches = re.findall(learning_pattern, content)
    
    for exp_id, desc, date, impact in matches:
        learnings.append({
            'id': exp_id,
            'description': desc.strip(),
            'date': date,
            'impact': impact.strip(),
        })
    
    return learnings


def load_all_logs(verbose=False):
    """加载所有日志文件并返回结构化数据"""
    log_step('START', '开始加载所有日志文件...', 'start')
    start_time = time.time()
    
    result = {
        'project_memory': {},
        'optimization_log': {},
        'change_log': {},
        'key_decisions': {},
        'prompt_template': {},
    }
    
    # 1. 加载项目记忆
    log_step('LOAD', '加载 project_memory.md ...', 'info')
    memory_content = read_file(LOG_FILES['project_memory']['path'])
    if memory_content:
        result['project_memory'] = {
            'exists': True,
            'content': memory_content,
            'rules': {
                'data': extract_rules(memory_content, 'DATA-'),
                'feature': extract_rules(memory_content, 'FEAT-'),
                'model': extract_rules(memory_content, 'MODEL-'),
                'security': extract_rules(memory_content, 'SECURITY-'),
            },
            'learnings': extract_learning_points(memory_content),
        }
        log_step('LOAD', f"项目记忆加载完成: {len(result['project_memory']['rules']['data'])}条数据规则, "
                        f"{len(result['project_memory']['rules']['feature'])}条特征规则, "
                        f"{len(result['project_memory']['learnings'])}条经验", 'success')
    else:
        result['project_memory'] = {'exists': False}
        log_step('LOAD', '项目记忆文件不存在', 'warning')
    
    # 2. 加载优化日志
    log_step('LOAD', '加载 optimization_log.md ...', 'info')
    opt_content = read_file(LOG_FILES['optimization_log']['path'])
    if opt_content:
        recent_logs = extract_today_logs(opt_content, days=7)
        result['optimization_log'] = {
            'exists': True,
            'content': opt_content,
            'recent_logs': recent_logs,
            'pending_tasks': extract_pending_tasks(opt_content),
            'issues': extract_issues(opt_content),
        }
        log_step('LOAD', f"优化日志加载完成: {len(recent_logs)}条近期日志, "
                        f"{len(result['optimization_log']['pending_tasks'])}项待办, "
                        f"{len(result['optimization_log']['issues'])}个问题", 'success')
    else:
        result['optimization_log'] = {'exists': False}
        log_step('LOAD', '优化日志文件不存在', 'warning')
    
    # 3. 加载变更日志
    log_step('LOAD', '加载 change_log.md ...', 'info')
    change_content = read_file(LOG_FILES['change_log']['path'])
    if change_content:
        changes = extract_changes(change_content, days=30)
        result['change_log'] = {
            'exists': True,
            'content': change_content,
            'recent_changes': changes,
            'change_count_total': len(change_content.split('|')[2::6]) if '|' in change_content else 0,
        }
        log_step('LOAD', f"变更日志加载完成: {len(changes)}条近30天变更", 'success')
    else:
        result['change_log'] = {'exists': False}
        log_step('LOAD', '变更日志文件不存在', 'warning')
    
    # 4. 加载决策日志
    log_step('LOAD', '加载 key_decisions.md ...', 'info')
    decision_content = read_file(LOG_FILES['key_decisions']['path'])
    if decision_content:
        all_decisions = extract_decisions(decision_content)
        pending_decisions = extract_decisions(decision_content, '待实施')
        in_progress_decisions = extract_decisions(decision_content, '实施中')
        completed_decisions = extract_decisions(decision_content, '已实施')
        result['key_decisions'] = {
            'exists': True,
            'content': decision_content,
            'all_decisions': all_decisions,
            'pending': pending_decisions,
            'in_progress': in_progress_decisions,
            'completed': completed_decisions,
        }
        log_step('LOAD', f"决策日志加载完成: {len(all_decisions)}项总决策, "
                        f"{len(pending_decisions)}项待实施, "
                        f"{len(in_progress_decisions)}项实施中", 'success')
    else:
        result['key_decisions'] = {'exists': False}
        log_step('LOAD', '决策日志文件不存在', 'warning')
    
    # 5. 加载上下文模板
    log_step('LOAD', '加载 prompt_template.md ...', 'info')
    template_content = read_file(LOG_FILES['prompt_template']['path'])
    if template_content:
        # 提取当前阶段
        stage_match = re.search(r'当前阶段[：:]\s*(.+)', template_content)
        current_stage = stage_match.group(1).strip() if stage_match else '未知'
        
        result['prompt_template'] = {
            'exists': True,
            'content': template_content,
            'current_stage': current_stage,
        }
        log_step('LOAD', f"上下文模板加载完成, 当前阶段: {current_stage}", 'success')
    else:
        result['prompt_template'] = {'exists': False}
        log_step('LOAD', '上下文模板文件不存在', 'warning')
    
    elapsed = time.time() - start_time
    total_files = sum(1 for v in result.values() if v.get('exists'))
    log_step('END', f"加载完成: {total_files}/5 文件, 总耗时 {elapsed*1000:.1f}ms", 'end')
    
    return result


def format_output(verbose=False):
    """格式化输出日志摘要"""
    start_time = time.time()
    
    print("=" * 70)
    print("📋 足球预测模型 - 日志检索摘要")
    print(f"   检索时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if verbose:
        print(f"   详细模式: 开启")
    print("=" * 70)
    
    # 加载所有日志
    log_step('INIT', "开始检索流程", 'start')
    all_data = load_all_logs(verbose)
    
    # ========== 1. 项目状态总览 ==========
    print("\n" + "=" * 70)
    print("📖 【1. 项目状态总览】")
    print("=" * 70)
    
    # 当前阶段
    template = all_data.get('prompt_template', {})
    current_stage = template.get('current_stage', '未知')
    print(f"\n  🎯 当前阶段: {current_stage}")
    
    # 数据统计
    memory = all_data.get('project_memory', {})
    rules = memory.get('rules', {})
    total_rules = sum(len(v) for v in rules.values())
    learnings = memory.get('learnings', [])
    print(f"  📚 规则总数: {total_rules}条 (数据/特征/模型/安全)")
    print(f"  💡 经验教训: {len(learnings)}条")
    
    # ========== 2. 关键规则提醒 ==========
    print("\n" + "=" * 70)
    print("🔒 【2. 关键规则提醒】")
    print("=" * 70)
    
    if rules.get('data'):
        print(f"\n  📊 数据规则 ({len(rules['data'])}条):")
        for r in rules['data'][:5]:
            print(f"    • [{r['id']}] {r['content']}")
        if len(rules['data']) > 5:
            print(f"    ... 还有 {len(rules['data'])-5} 条规则")
    
    if rules.get('feature'):
        print(f"\n  🎯 特征规则 ({len(rules['feature'])}条):")
        for r in rules['feature'][:3]:
            print(f"    • [{r['id']}] {r['content']}")
    
    if rules.get('model'):
        print(f"\n  🤖 模型规则 ({len(rules['model'])}条):")
        for r in rules['model'][:3]:
            print(f"    • [{r['id']}] {r['content']}")
    
    if rules.get('security'):
        print(f"\n  🔒 安全规则 ({len(rules['security'])}条):")
        for r in rules['security'][:2]:
            print(f"    • [{r['id']}] {r['content']}")
    
    # ========== 3. 最新任务完成情况 ==========
    print("\n" + "=" * 70)
    print("📝 【3. 最新任务完成情况】")
    print("=" * 70)
    
    opt_log = all_data.get('optimization_log', {})
    recent_logs = opt_log.get('recent_logs', [])
    
    if recent_logs:
        print(f"\n  📅 最近7天日志 ({len(recent_logs)}条):")
        for log in recent_logs[-5:]:
            print(f"    📆 {log['date']}:")
            log_lines = log['content'].split('\n')[:5]
            for line in log_lines:
                if line.strip() and '|' not in line and '---' not in line:
                    print(f"      {line.strip()[:80]}")
    else:
        print("\n  📅 最近7天无新日志")
    
    # ========== 4. 待办事项列表 ==========
    print("\n" + "=" * 70)
    print("📋 【4. 待办事项列表】")
    print("=" * 70)
    
    pending_tasks = opt_log.get('pending_tasks', [])
    issues = opt_log.get('issues', [])
    
    if pending_tasks:
        print(f"\n  ⬜ 待办事项 ({len(pending_tasks)}项):")
        for task in pending_tasks[:10]:
            print(f"    ⬜ {task[:60]}")
        if len(pending_tasks) > 10:
            print(f"    ... 还有 {len(pending_tasks)-10} 项")
    
    if issues:
        print(f"\n  ⚠️ 待解决问题 ({len(issues)}项):")
        for issue in issues[:8]:
            print(f"    • [{issue['id']}] [{issue['status']}] {issue['description'][:50]}...")
        if len(issues) > 8:
            print(f"    ... 还有 {len(issues)-8} 个问题")
    
    # ========== 5. 最近变更记录 ==========
    print("\n" + "=" * 70)
    print("🔧 【5. 最近变更记录】")
    print("=" * 70)
    
    change_log = all_data.get('change_log', {})
    recent_changes = change_log.get('recent_changes', [])
    
    if recent_changes:
        # 按变更类型分组
        changes_by_type = defaultdict(list)
        for chg in recent_changes:
            changes_by_type[chg['type']].append(chg)
        
        print(f"\n  📊 近30天变更统计:")
        for type_name, changes in changes_by_type.items():
            print(f"    • {type_name}: {len(changes)}次")
        
        print(f"\n  📝 最近变更详情 (共{len(recent_changes)}条):")
        for chg in recent_changes[-5:]:
            print(f"    📅 {chg['date']} [{chg['type']}] {chg['description'][:60]}")
            if chg.get('related'):
                print(f"       关联: {chg['related']}")
    else:
        print("\n  📅 近30天无新变更")
    
    # ========== 6. 关键决策状态 ==========
    print("\n" + "=" * 70)
    print("💡 【6. 关键决策状态】")
    print("=" * 70)
    
    dec_log = all_data.get('key_decisions', {})
    pending_dec = dec_log.get('pending', [])
    in_progress_dec = dec_log.get('in_progress', [])
    completed_dec = dec_log.get('completed', [])
    
    if pending_dec:
        print(f"\n  ⏳ 待实施决策 ({len(pending_dec)}项):")
        for dec in pending_dec[:5]:
            print(f"    • [{dec['id']}] {dec['topic']}")
            print(f"      {dec['content'][:70]}...")
        if len(pending_dec) > 5:
            print(f"    ... 还有 {len(pending_dec)-5} 项待实施决策")
    
    if in_progress_dec:
        print(f"\n  🔄 实施中决策 ({len(in_progress_dec)}项):")
        for dec in in_progress_dec[:3]:
            print(f"    • [{dec['id']}] {dec['topic']}")
    
    if completed_dec:
        print(f"\n  ✅ 已完成决策 ({len(completed_dec)}项):")
        if verbose:
            for dec in completed_dec[:3]:
                print(f"    • [{dec['id']}] {dec['topic']}")
        else:
            print(f"    最近完成: [{completed_dec[-1]['id']}] {completed_dec[-1]['topic']}")
    
    # ========== 7. 经验教训检索 ==========
    if verbose and learnings:
        print("\n" + "=" * 70)
        print("💡 【7. 经验教训检索】")
        print("=" * 70)
        
        print(f"\n  📚 所有经验教训 ({len(learnings)}条):")
        for exp in learnings:
            print(f"    • [{exp['id']}] {exp['description'][:60]}...")
            print(f"      日期: {exp['date']}, 影响: {exp['impact']}")
    
    # ========== 8. 检索过程日志 ==========
    print("\n" + "=" * 70)
    print("📊 【检索过程日志】")
    print("=" * 70)
    
    for log in retrieval_log:
        icon = {'start': '▶️', 'end': '⏹️', 'info': 'ℹ️', 'success': '✅', 
                'warning': '⚠️', 'error': '❌'}
        print(f"    [{log['timestamp']}] {icon.get(log['level'], '📝')} [{log['step']}] {log['message']}")
    
    # ========== 9. 汇总 ==========
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 70)
    print("📊 【检索汇总】")
    print("=" * 70)
    print(f"  • 规则总数: {total_rules}条")
    print(f"  • 经验教训: {len(learnings)}条")
    print(f"  • 近期任务: {len(recent_logs)}条日志")
    print(f"  • 待办事项: {len(pending_tasks)}项")
    print(f"  • 待解决问题: {len(issues)}个")
    print(f"  • 近30天变更: {len(recent_changes)}条")
    print(f"  • 待实施决策: {len(pending_dec)}项")
    print(f"  • 实施中决策: {len(in_progress_dec)}项")
    print(f"  • 总耗时: {elapsed*1000:.1f}ms")
    
    print("\n" + "=" * 70)
    print("✅ 日志检索完成！")
    print("=" * 70)
    
    return all_data


def save_results(all_data):
    """保存检索结果到文件"""
    if not os.path.exists(LOGS_DIR):
        os.makedirs(LOGS_DIR)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(LOGS_DIR, f'retrieval_{timestamp}.json')
    
    # 简化输出，去除原始内容
    save_data = {
        'timestamp': timestamp,
        'project_memory': {},
        'optimization_log': {},
        'change_log': {},
        'key_decisions': {},
    }
    
    # 保存项目记忆摘要
    mem = all_data.get('project_memory', {})
    if mem.get('exists'):
        save_data['project_memory'] = {
            'rules': mem.get('rules', {}),
            'learnings_count': len(mem.get('learnings', [])),
        }
    
    # 保存优化日志摘要
    opt = all_data.get('optimization_log', {})
    if opt.get('exists'):
        save_data['optimization_log'] = {
            'recent_logs_count': len(opt.get('recent_logs', [])),
            'pending_tasks_count': len(opt.get('pending_tasks', [])),
            'issues_count': len(opt.get('issues', [])),
            'pending_tasks': opt.get('pending_tasks', [])[:10],
        }
    
    # 保存变更日志摘要
    chg = all_data.get('change_log', {})
    if chg.get('exists'):
        save_data['change_log'] = {
            'recent_changes_count': len(chg.get('recent_changes', [])),
            'recent_changes': chg.get('recent_changes', [])[:10],
        }
    
    # 保存决策日志摘要
    dec = all_data.get('key_decisions', {})
    if dec.get('exists'):
        save_data['key_decisions'] = {
            'pending_count': len(dec.get('pending', [])),
            'in_progress_count': len(dec.get('in_progress', [])),
            'completed_count': len(dec.get('completed', [])),
            'pending': dec.get('pending', [])[:10],
            'in_progress': dec.get('in_progress', [])[:5],
        }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    
    log_step('SAVE', f"检索结果已保存: {output_file}", 'success')
    return output_file


def generate_startup_context(all_data):
    """生成适合加载的启动上下文"""
    template = []
    template.append("# 足球预测模型 - 启动上下文")
    template.append(f"\n## 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    template.append("\n---\n")
    
    # 当前阶段
    stage = all_data.get('prompt_template', {}).get('current_stage', '未知')
    template.append(f"## 当前阶段: {stage}")
    
    # 关键规则
    rules = all_data.get('project_memory', {}).get('rules', {})
    if rules:
        template.append("\n## 关键规则")
        for rule_type, rule_list in rules.items():
            if rule_list:
                type_map = {'data': '数据规则', 'feature': '特征规则', 'model': '模型规则', 'security': '安全规则'}
                template.append(f"\n### {type_map.get(rule_type, rule_type)}")
                for r in rule_list[:3]:
                    template.append(f"- [{r['id']}] {r['content']}")
    
    # 待办事项
    pending = all_data.get('optimization_log', {}).get('pending_tasks', [])
    if pending:
        template.append(f"\n## 待办事项 ({len(pending)}项)")
        for task in pending[:10]:
            template.append(f"- [ ] {task}")
    
    # 关键决策
    dec = all_data.get('key_decisions', {})
    pending_dec = dec.get('pending', [])
    if pending_dec:
        template.append(f"\n## 待实施决策 ({len(pending_dec)}项)")
        for d in pending_dec[:5]:
            template.append(f"- [{d['id']}] {d['topic']}")
    
    # 近期变更
    changes = all_data.get('change_log', {}).get('recent_changes', [])
    if changes:
        template.append(f"\n## 近期变更 (近30天, {len(changes)}条)")
        for c in changes[-3:]:
            template.append(f"- [{c['date']}] [{c['type']}] {c['description'][:50]}")
    
    return '\n'.join(template)


if __name__ == '__main__':
    verbose = '--verbose' in sys.argv
    save = '--save' in sys.argv
    startup = '--startup' in sys.argv
    
    all_data = format_output(verbose=verbose)
    
    if save:
        save_results(all_data)
    
    if startup:
        context = generate_startup_context(all_data)
        print("\n" + "=" * 70)
        print("🚀 【启动上下文】")
        print("=" * 70)
        print(context)
