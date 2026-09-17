#!/usr/bin/env node
/**
 * 6文档一致性验证脚本
 * 检查 change_log.md、optimization_log.md、key_decisions.md、
 *        prompt_template.md、CONVERSATION_WORKFLOW_GUIDE.md、project_memory.md
 * 之间的数据一致性
 *
 * 用法: node scripts/verify_doc_consistency.mjs
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, '..');
const DOCS_DIR = path.join(PROJECT_ROOT, 'docs');

const DOC_FILES = {
  change_log: path.join(DOCS_DIR, 'change_log.md'),
  optimization_log: path.join(DOCS_DIR, 'optimization_log.md'),
  key_decisions: path.join(DOCS_DIR, 'key_decisions.md'),
  prompt_template: path.join(DOCS_DIR, 'prompt_template.md'),
  workflow_guide: path.join(DOCS_DIR, 'CONVERSATION_WORKFLOW_GUIDE.md'),
  project_memory: path.join(DOCS_DIR, 'project_memory.md'),
};

const results = {
  errors: [],
  warnings: [],
  info: [],
  stats: {
    changeLogCount: 0,
    optimizationIssueCount: 0,
    decisionCount: 0,
    crossRefValidations: 0,
  },
};

function log(level, message) {
  const prefix = level === 'ERROR' ? '❌' : level === 'WARN' ? '⚠️' : 'ℹ️';
  results[level === 'ERROR' ? 'errors' : level === 'WARN' ? 'warnings' : 'info'].push(message);
  console.log(`${prefix} ${message}`);
}

function readDoc(filePath) {
  if (!fs.existsSync(filePath)) {
    log('ERROR', `文件不存在: ${path.basename(filePath)}`);
    return '';
  }
  return fs.readFileSync(filePath, 'utf-8');
}

// ============================================================
// 1. 解析 change_log.md
// ============================================================
function parseChangeLog(content) {
  const entries = [];
  const lines = content.split('\n');
  let currentSection = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // 检测节标题
    const sectionMatch = line.match(/^###\s+[\d.]+\s+(.+)/);
    if (sectionMatch) {
      currentSection = sectionMatch[1].trim();
    }

    // 解析变更条目（表格行）
    // 格式: | C-{date}-{seq} | time | type | scope | content | before | after | reason | issueId | result | risk | author | status |
    if (line.match(/^\|\s*C-\d{8}-\d{3}\s*\|/)) {
      const columns = line.split('|').map(c => c.trim()).filter(c => c !== '');
      if (columns.length >= 13) {
        const entry = {
          id: columns[0],
          time: columns[1],
          type: columns[2],
          scope: columns[3],
          content: columns[4],
          before: columns[5],
          after: columns[6],
          reason: columns[7],
          issueId: columns[8],
          result: columns[9],
          risk: columns[10],
          author: columns[11],
          status: columns[12],
          section: currentSection,
        };
        entries.push(entry);
      }
    }
  }

  return entries;
}

// ============================================================
// 2. 解析 optimization_log.md
// ============================================================
function parseOptimizationLog(content) {
  const issues = { p0: [], p1: [], p2: [] };
  const taskLogs = [];
  const lines = content.split('\n');
  let currentSection = '';
  let currentPriority = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // 检测问题等级
    if (line.includes('P0级问题')) currentPriority = 'p0';
    else if (line.includes('P1级问题')) currentPriority = 'p1';
    else if (line.includes('P2级问题')) currentPriority = 'p2';

    // 检测任务日志节
    const taskSectionMatch = line.match(/^###\s+4\.\d+\s+(.+)/);
    if (taskSectionMatch) {
      currentSection = taskSectionMatch[1].trim();
    }

    // 解析问题条目
    // 格式: | P{level}-{num} | desc | source | status | planDate | actualDate? | notes? |
    const issueMatch = line.match(/^\|\s*(P[0-2]-\d+)\s*\|/);
    if (issueMatch) {
      const columns = line.split('|').map(c => c.trim()).filter(c => c !== '');
      if (columns.length >= 5) {
        const issueId = columns[0];
        const level = issueId.startsWith('P0') ? 'p0' : issueId.startsWith('P1') ? 'p1' : 'p2';
        const entry = {
          id: issueId,
          description: columns[1] || '',
          source: columns[2] || '',
          status: columns[3] || '',
          planDate: columns[4] || '',
          actualDate: columns[5] || '-',
          notes: columns[6] || '',
        };
        issues[level].push(entry);
      }
    }

    // 解析任务日志条目
    if (currentSection && line.match(/^\|\s*\d{2}:\d{2}:\d{2}\s*\|/) ||
        line.match(/^\|\s*\d{1,2}:\d{2}:\d{2}\s*\|/)) {
      const columns = line.split('|').map(c => c.trim()).filter(c => c !== '');
      if (columns.length >= 5) {
        taskLogs.push({
          time: columns[0],
          stage: columns[1],
          task: columns[2],
          owner: columns[3],
          status: columns[4],
          priority: columns[5] || '',
          notes: columns[6] || '',
          section: currentSection,
        });
      }
    }
  }

  return { issues, taskLogs };
}

// ============================================================
// 3. 解析 key_decisions.md
// ============================================================
function parseKeyDecisions(content) {
  const decisions = [];
  const lines = content.split('\n');
  let currentCategory = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // 检测决策类别
    if (line.includes('2.1 数据类决策')) currentCategory = '数据';
    else if (line.includes('2.2 模型类决策')) currentCategory = '模型';
    else if (line.includes('2.3 特征工程类决策')) currentCategory = '特征工程';
    else if (line.includes('2.4 代码质量类决策')) currentCategory = '代码质量';
    else if (line.includes('2.5 测试类决策')) currentCategory = '测试';
    else if (line.includes('2.6 服务类决策')) currentCategory = '服务';
    else if (line.includes('2.7 记忆系统类决策')) currentCategory = '记忆系统';

    // 解析决策条目
    // 格式: | D-{date}-{seq} | date | topic | content | status | owner |
    if (line.match(/^\|\s*D-\d{8}-\d{3}\s*\|/)) {
      const columns = line.split('|').map(c => c.trim()).filter(c => c !== '');
      if (columns.length >= 5) {
        decisions.push({
          id: columns[0],
          date: columns[1] || '',
          topic: columns[2] || '',
          content: columns[3] || '',
          status: columns[4] || '',
          owner: columns[5] || '',
          category: currentCategory,
        });
      }
    }
  }

  return decisions;
}

// ============================================================
// 4. 解析 prompt_template.md
// ============================================================
function parsePromptTemplate(content) {
  const result = { currentStage: '', performanceMetrics: {}, completedItems: [] };

  // 提取当前阶段
  const stageMatch = content.match(/当前阶段[：:]\s*(.+)/);
  if (stageMatch) {
    result.currentStage = stageMatch[1].trim();
  }

  // 提取性能指标
  const cvMatch = content.match(/CV准确率[：:]\s*([\d.]+)%/);
  if (cvMatch) result.performanceMetrics.cvAccuracy = parseFloat(cvMatch[1]);

  const llMatch = content.match(/LogLoss[：:]\s*([\d.]+)/);
  if (llMatch) result.performanceMetrics.logLoss = parseFloat(llMatch[1]);

  // 提取已完成优化项
  const completedSection = content.match(/已完成优化项[：:]*\n([\s\S]*?)(?=\n##|\n###|$)/);
  if (completedSection) {
    result.completedItems = completedSection[1]
      .split('\n')
      .filter(l => l.trim().startsWith('-'))
      .map(l => l.trim());
  }

  return result;
}

// ============================================================
// 5. 解析 CONVERSATION_WORKFLOW_GUIDE.md
// ============================================================
function parseWorkflowGuide(content) {
  const result = { lastUpdated: '', baselineMetrics: {}, modelConfig: {} };

  const updateMatch = content.match(/最后更新[：:]\s*([\d-]+)/);
  if (updateMatch) result.lastUpdated = updateMatch[1];

  // 提取基准性能
  const cvMatch = content.match(/CV准确率[：:]\s*([\d.]+)%/);
  if (cvMatch) result.baselineMetrics.cvAccuracy = parseFloat(cvMatch[1]);

  const llMatch = content.match(/LogLoss[：:]\s*([\d.]+)/);
  if (llMatch) result.baselineMetrics.logLoss = parseFloat(llMatch[1]);

  return result;
}

// ============================================================
// 6. 解析 project_memory.md
// ============================================================
function parseProjectMemory(content) {
  const result = { rules: [], learnings: [] };
  const lines = content.split('\n');
  let currentSection = '';

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.includes('硬性规则')) currentSection = 'rules';
    else if (line.includes('经验教训')) currentSection = 'learnings';

    const ruleMatch = line.match(/^\|\s*([A-Z]+-\d+)\s*\|/);
    if (ruleMatch && currentSection === 'rules') {
      result.rules.push({
        id: ruleMatch[1],
        content: line.split('|').map(c => c.trim()).filter(c => c !== '')[1] || '',
      });
    }
  }

  return result;
}

// ============================================================
// 一致性检查
// ============================================================

function checkCrossRefs(changeLog, optimizationLog, decisions) {
  log('INFO', '=== 交叉引用检查 ===\n');

  // 检查1: change_log 中的 B-XX 引用是否在 optimization_log 的问题清单中存在
  log('INFO', '--- 检查1: B-XX 引用一致性 ---');
  const bRefsInChangeLog = new Set();
  changeLog.forEach(entry => {
    const bMatch = entry.issueId.match(/B-\d+/g);
    if (bMatch) bMatch.forEach(b => bRefsInChangeLog.add(b));
  });

  // B-006, B-007, B-008 应存在引用
  ['B-006', 'B-007', 'B-008'].forEach(bId => {
    if (bRefsInChangeLog.has(bId)) {
      log('INFO', `  ✅ ${bId} 在 change_log.md 中有引用`);
    } else {
      log('ERROR', `  ❌ ${bId} 在 change_log.md 中无引用`);
    }
  });

  // 检查2: change_log 中的 C-ID 连续性
  log('INFO', '\n--- 检查2: C-ID 连续性 ---');
  const cIds = changeLog.map(e => e.id).sort();
  const gaps = [];
  for (let i = 0; i < cIds.length - 1; i++) {
    const current = cIds[i];
    const next = cIds[i + 1];
    const curSeq = parseInt(current.split('-')[2]);
    const nextSeq = parseInt(next.split('-')[2]);
    if (nextSeq - curSeq > 1) {
      gaps.push(`${current} → ${next} (缺失 ${nextSeq - curSeq - 1} 个)`);
    }
  }
  if (gaps.length > 0) {
    log('WARN', `  ⚠️ C-ID 可能不连续: ${gaps.join(', ')}`);
  } else {
    log('INFO', `  ✅ C-ID 连续 (共 ${cIds.length} 条记录)`);
  }

  // 检查3: optimization_log 中标记为已完成的问题应有对应的 change_log 记录
  log('INFO', '\n--- 检查3: 已完成问题 → 变更记录映射 ---');
  const completedIssues = [
    ...optimizationLog.issues.p0.filter(i => i.status === '已完成'),
    ...optimizationLog.issues.p1.filter(i => i.status === '已完成'),
  ];

  completedIssues.forEach(issue => {
    const hasRef = changeLog.some(entry => {
      const content = (entry.content + entry.reason + entry.after).toLowerCase();
      return content.includes(issue.id.toLowerCase().replace('-', '')) ||
             content.includes(issue.description.slice(0, 10));
    });
    if (hasRef) {
      log('INFO', `  ✅ ${issue.id} (${issue.description.slice(0, 20)}) 有对应变更记录`);
    } else {
      log('WARN', `  ⚠️ ${issue.id} 标记为已完成但未找到对应变更记录`);
    }
  });

  // 检查4: key_decisions 中已完成决策应有对应 change_log
  log('INFO', '\n--- 检查4: 已完成决策 → 变更记录映射 ---');
  const completedDecisions = decisions.filter(d => d.status === '已完成');
  let matchedDecisions = 0;

  completedDecisions.forEach(decision => {
    const hasRef = changeLog.some(entry => {
      const content = (entry.content + entry.reason + entry.after).toLowerCase();
      const decisionIdShort = decision.id.replace('D-', '').replace(/-/g, '');
      return content.includes(decisionIdShort) ||
             content.includes(decision.topic.slice(0, 8).toLowerCase());
    });
    if (hasRef) matchedDecisions++;
  });

  log('INFO', `  📊 已完成决策: ${completedDecisions.length} 个，有变更记录映射: ${matchedDecisions} 个`);
  if (completedDecisions.length - matchedDecisions > 0) {
    log('WARN', `  ⚠️ ${completedDecisions.length - matchedDecisions} 个已完成决策未在 change_log 中找到直接映射`);
  }

  // 检查5: change_log 状态一致性
  log('INFO', '\n--- 检查5: change_log 状态一致性 ---');
  const statusGroups = {};
  changeLog.forEach(entry => {
    const status = entry.status;
    if (!statusGroups[status]) statusGroups[status] = [];
    statusGroups[status].push(entry.id);
  });
  Object.entries(statusGroups).forEach(([status, ids]) => {
    log('INFO', `  📋 状态 "${status}": ${ids.length} 条记录 (${ids.join(', ')})`);
  });

  // 检查6: 日期一致性
  log('INFO', '\n--- 检查6: 日期一致性 ---');
  const datesInChangeLog = new Set();
  changeLog.forEach(entry => {
    const dateMatch = entry.id.match(/C-(\d{8})-/);
    if (dateMatch) datesInChangeLog.add(dateMatch[1]);
  });

  decisions.forEach(decision => {
    const decisionDate = decision.date.replace(/-/g, '');
    if (decisionDate && !datesInChangeLog.has(decisionDate)) {
      log('INFO', `  ℹ️ 决策 ${decision.id} 日期 ${decisionDate} 无同期变更记录 (可能为规划决策)`);
    }
  });

  log('INFO', `  📊 change_log 涉及日期: ${[...datesInChangeLog].join(', ')}`);

  // 更新统计
  results.stats.changeLogCount = changeLog.length;
  results.stats.optimizationIssueCount =
    optimizationLog.issues.p0.length + optimizationLog.issues.p1.length + optimizationLog.issues.p2.length;
  results.stats.decisionCount = decisions.length;
}

function checkPerformanceConsistency(promptData, workflowData) {
  log('INFO', '\n=== 性能指标一致性检查 ===\n');

  // CV 准确率一致性
  if (promptData.performanceMetrics.cvAccuracy && workflowData.baselineMetrics.cvAccuracy) {
    const diff = Math.abs(promptData.performanceMetrics.cvAccuracy - workflowData.baselineMetrics.cvAccuracy);
    if (diff < 0.01) {
      log('INFO', `  ✅ CV准确率一致: ${promptData.performanceMetrics.cvAccuracy}%`);
    } else {
      log('ERROR', `  ❌ CV准确率不一致: prompt_template=${promptData.performanceMetrics.cvAccuracy}%, workflow_guide=${workflowData.baselineMetrics.cvAccuracy}%`);
    }
  }

  // LogLoss 一致性
  if (promptData.performanceMetrics.logLoss && workflowData.baselineMetrics.logLoss) {
    const diff = Math.abs(promptData.performanceMetrics.logLoss - workflowData.baselineMetrics.logLoss);
    if (diff < 0.001) {
      log('INFO', `  ✅ LogLoss一致: ${promptData.performanceMetrics.logLoss}`);
    } else {
      log('ERROR', `  ❌ LogLoss不一致: prompt_template=${promptData.performanceMetrics.logLoss}, workflow_guide=${workflowData.baselineMetrics.logLoss}`);
    }
  }

  // 更新日期
  if (workflowData.lastUpdated) {
    log('INFO', `  📅 Workflow Guide 最后更新: ${workflowData.lastUpdated}`);
  }
}

function checkOptimizationStatus(optimizationLog) {
  log('INFO', '\n=== 优化状态检查 ===\n');

  const allIssues = [
    ...optimizationLog.issues.p0.map(i => ({ ...i, level: 'P0' })),
    ...optimizationLog.issues.p1.map(i => ({ ...i, level: 'P1' })),
    ...optimizationLog.issues.p2.map(i => ({ ...i, level: 'P2' })),
  ];

  const byStatus = {};
  allIssues.forEach(issue => {
    const status = issue.status;
    if (!byStatus[status]) byStatus[status] = [];
    byStatus[status].push(`${issue.level}-${issue.id.split('-')[1]}`);
  });

  Object.entries(byStatus).forEach(([status, ids]) => {
    log('INFO', `  📋 ${status}: ${ids.length} 个问题 (${ids.join(', ')})`);
  });

  // 检查已完成问题是否有实际解决日期
  const completedWithoutDate = allIssues.filter(
    i => i.status === '已完成' && (!i.actualDate || i.actualDate === '-')
  );
  if (completedWithoutDate.length > 0) {
    log('ERROR', `  ❌ ${completedWithoutDate.length} 个已完成问题缺少实际解决日期:`);
    completedWithoutDate.forEach(i => {
      log('ERROR', `     ${i.level}-${i.id.split('-')[1]}: ${i.description}`);
    });
  } else {
    log('INFO', `  ✅ 所有已完成问题都有实际解决日期`);
  }

  // 检查有备注的问题
  const withNotes = allIssues.filter(i => i.notes && i.notes.length > 5);
  log('INFO', `  📝 有详细备注的问题: ${withNotes.length} 个`);
}

function generateReport() {
  log('INFO', '\n' + '='.repeat(60));
  log('INFO', '验证报告汇总');
  log('INFO', '='.repeat(60));

  console.log(`\n📊 统计数据:`);
  console.log(`   change_log.md 变更记录: ${results.stats.changeLogCount} 条`);
  console.log(`   optimization_log.md 问题总数: ${results.stats.optimizationIssueCount} 个`);
  console.log(`   key_decisions.md 决策总数: ${results.stats.decisionCount} 个`);

  console.log(`\n📋 检查结果:`);
  console.log(`   ❌ 错误: ${results.errors.length} 个`);
  console.log(`   ⚠️  警告: ${results.warnings.length} 个`);
  console.log(`   ℹ️  信息: ${results.info.length} 个`);

  if (results.errors.length > 0) {
    console.log(`\n❌ 错误详情:`);
    results.errors.forEach(e => console.log(`   ${e}`));
  }

  if (results.warnings.length > 0) {
    console.log(`\n⚠️  警告详情:`);
    results.warnings.forEach(w => console.log(`   ${w}`));
  }

  const score = results.errors.length === 0 ?
    (results.warnings.length === 0 ? '💚 优秀' : '💛 良好') :
    '❤️ 需修复';

  console.log(`\n🏁 综合评分: ${score}`);

  return results.errors.length === 0;
}

// ============================================================
// 主流程
// ============================================================
function main() {
  console.log('🔍 6文档一致性验证脚本\n');

  // 读取所有文档
  const contents = {};
  for (const [name, filePath] of Object.entries(DOC_FILES)) {
    console.log(`📖 读取 ${path.basename(filePath)}...`);
    contents[name] = readDoc(filePath);
    if (contents[name]) {
      console.log(`   ✅ ${path.basename(filePath)} (${contents[name].length} 字符)`);
    }
  }

  console.log('');

  // 解析各文档
  const changeLog = parseChangeLog(contents.change_log);
  const { issues: optimizationLog, taskLogs } = parseOptimizationLog(contents.optimization_log);
  const decisions = parseKeyDecisions(contents.key_decisions);
  const promptData = parsePromptTemplate(contents.prompt_template);
  const workflowData = parseWorkflowGuide(contents.workflow_guide);
  const memoryData = parseProjectMemory(contents.project_memory);

  console.log(`\n📊 解析结果:`);
  console.log(`   change_log: ${changeLog.length} 条变更`);
  console.log(`   optimization_log: ${optimizationLog.p0.length + optimizationLog.p1.length + optimizationLog.p2.length} 个问题 (P0:${optimizationLog.p0.length}, P1:${optimizationLog.p1.length}, P2:${optimizationLog.p2.length})`);
  console.log(`   key_decisions: ${decisions.length} 个决策`);
  console.log(`   任务日志: ${taskLogs.length} 条`);
  console.log(`   project_memory: ${memoryData.rules.length} 条规则`);

  // 执行检查
  console.log('');
  checkCrossRefs(changeLog, { issues: optimizationLog, taskLogs }, decisions);
  checkPerformanceConsistency(promptData, workflowData);
  checkOptimizationStatus({ issues: optimizationLog, taskLogs });

  // 生成报告
  const allPassed = generateReport();

  process.exit(allPassed ? 0 : 1);
}

main();