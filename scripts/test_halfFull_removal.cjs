/**
 * 半全场移除验证测试脚本
 * ============================
 * 验证目标:
 *   1. prediction-engine.js 中无 calcHalfFull / halfFull 残留
 *   2. prediction-service.js 中无 calcHalfFull / halfFull 残留
 *   3. PredictionEngine 可正常加载和实例化
 *   4. createPredictionResult() 返回结果中不含 halfFull 字段
 *   5. 其他预测功能 (WDL/Handicap/TotalGoals/Score) 正常输出
 *
 * 用法: node scripts/test_halfFull_removal.js
 */

const fs = require('fs');
const path = require('path');

const PROJECT_DIR = path.resolve(__dirname, '..');
const ENGINE_PATH = path.join(PROJECT_DIR, 'shared', 'prediction-engine.js');
const SERVICE_PATH = path.join(PROJECT_DIR, 'server', 'services', 'prediction-service.js');

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  [PASS] ${name}`);
    passed++;
  } catch (e) {
    console.log(`  [FAIL] ${name}: ${e.message}`);
    failed++;
  }
}

function assert(condition, msg) {
  if (!condition) throw new Error(msg || 'assertion failed');
}

// ============================================================
// 阶段 1: 源码静态检查
// ============================================================
console.log('\n=== 阶段 1: 源码静态检查 ===');

test('prediction-engine.js 不含 calcHalfFull', () => {
  const content = fs.readFileSync(ENGINE_PATH, 'utf-8');
  assert(!content.includes('calcHalfFull'), '发现 calcHalfFull 残留');
});

test('prediction-engine.js 不含 halfFull 字段', () => {
  const content = fs.readFileSync(ENGINE_PATH, 'utf-8');
  assert(!content.includes('halfFull'), '发现 halfFull 残留');
});

test('prediction-service.js 不含 calcHalfFull', () => {
  const content = fs.readFileSync(SERVICE_PATH, 'utf-8');
  assert(!content.includes('calcHalfFull'), '发现 calcHalfFull 残留');
});

test('prediction-service.js 不含 halfFull', () => {
  const content = fs.readFileSync(SERVICE_PATH, 'utf-8');
  assert(!content.includes('halfFull'), '发现 halfFull 残留');
});

// ============================================================
// 阶段 2: 引擎加载测试
// ============================================================
console.log('\n=== 阶段 2: 引擎加载测试 ===');

let PredictionEngine;
test('PredictionEngine 模块可加载', () => {
  // 清除缓存确保重新加载
  delete require.cache[require.resolve(ENGINE_PATH)];
  const mod = require(ENGINE_PATH);
  PredictionEngine = mod.default || mod;
  assert(typeof PredictionEngine === 'function', 'PredictionEngine 不是构造函数');
});

let engine;
test('PredictionEngine 可实例化', () => {
  engine = new PredictionEngine();
  assert(engine instanceof PredictionEngine, '实例化失败');
});

test('实例上不存在 calcHalfFull 方法', () => {
  assert(typeof engine.calcHalfFull === 'undefined', 'calcHalfFull 方法仍然存在');
});

// ============================================================
// 阶段 3: 核心功能可用性检查
// ============================================================
console.log('\n=== 阶段 3: 核心功能可用性检查 ===');

test('poissonPMF 方法可用', () => {
  assert(typeof engine.poissonPMF === 'function', 'poissonPMF 不可用');
});

test('calcLambdaMatch 方法可用', () => {
  assert(typeof engine.calcLambdaMatch === 'function', 'calcLambdaMatch 不可用');
});

test('dixonColePMF 方法可用', () => {
  assert(typeof engine.dixonColePMF === 'function', 'dixonColePMF 不可用');
});

test('calcWinDrawLosePoisson 方法可用', () => {
  assert(typeof engine.calcWinDrawLosePoisson === 'function', 'calcWinDrawLosePoisson 不可用');
});

test('calcHandicap 方法可用', () => {
  assert(typeof engine.calcHandicap === 'function', 'calcHandicap 不可用');
});

test('calcTotalGoals 方法可用', () => {
  assert(typeof engine.calcTotalGoals === 'function', 'calcTotalGoals 不可用');
});

test('predictScoreV4 方法可用', () => {
  assert(typeof engine.predictScoreV4 === 'function', 'predictScoreV4 不可用');
});

test('predictStacked 方法可用', () => {
  assert(typeof engine.predictStacked === 'function', 'predictStacked 不可用');
});

test('createPredictionResult 方法可用', () => {
  assert(typeof engine.createPredictionResult === 'function', 'createPredictionResult 不可用');
});

// ============================================================
// 阶段 4: 预测结果结构验证
// ============================================================
console.log('\n=== 阶段 4: 预测结果结构验证 ===');

// 构造模拟数据
const mockTeamA = { key: 'home', name: 'Real Madrid', league: '西甲' };
const mockTeamB = { key: 'away', name: 'FC Barcelona', league: '西甲' };
const mockStackedResult = {
  winA: 0.45, winB: 0.25, draw: 0.30,
  confidence: 0.45, uncertainty: 0.12,
  model: 'stacking'
};

test('createPredictionResult 可执行', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(result !== null && result !== undefined, '返回 null/undefined');
});

test('结果中不含 halfFull 字段', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(!('halfFull' in (result.predictions || {})), 'predictions 中仍有 halfFull');
  // 递归检查整个结果对象
  const json = JSON.stringify(result);
  assert(!json.includes('halfFull'), 'JSON 序列化结果中包含 halfFull');
});

test('expectations 中不含半全场', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  const json = JSON.stringify(result);
  assert(!json.includes('半全场'), '结果中包含"半全场"文字');
});

test('必需字段: predictions.winDrawLose 存在', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(result.predictions && result.predictions.winDrawLose, 'winDrawLose 缺失');
  const wdl = result.predictions.winDrawLose;
  assert(typeof wdl.win === 'number', 'wdl.win 不是数字');
  assert(typeof wdl.draw === 'number', 'wdl.draw 不是数字');
  assert(typeof wdl.lose === 'number', 'wdl.lose 不是数字');
});

test('必需字段: predictions.handicap 存在', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(result.predictions && result.predictions.handicap, 'handicap 缺失');
});

test('必需字段: predictions.totalGoals 存在', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(result.predictions && result.predictions.totalGoals, 'totalGoals 缺失');
});

test('必需字段: predictions.topScores 存在', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(result.predictions && Array.isArray(result.predictions.topScores), 'topScores 缺失或非数组');
});

test('必需字段: lambda 存在且类型正确', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(result.lambda, 'lambda 缺失');
  assert(typeof result.lambda.home === 'number', 'lambda.home 不是数字');
  assert(typeof result.lambda.away === 'number', 'lambda.away 不是数字');
});

test('必需字段: confidence 存在', () => {
  const result = engine.createPredictionResult(
    'home', 'away', mockTeamA, mockTeamB,
    mockStackedResult, 1.5, 1.0, {}
  );
  assert(result.confidence !== undefined, 'confidence 缺失');
});

// ============================================================
// 阶段 5: generate_unified_report.py 检查
// ============================================================
console.log('\n=== 阶段 5: generate_unified_report.py 检查 ===');

const REPORT_PATH = path.join(PROJECT_DIR, 'scripts', 'generate_unified_report.py');

test('generate_unified_report.py 不含 halfFull', () => {
  const content = fs.readFileSync(REPORT_PATH, 'utf-8');
  assert(!content.includes('halfFull'), 'generate_unified_report.py 中发现 halfFull');
});

test('generate_unified_report.py 不含 calcHalfFull', () => {
  const content = fs.readFileSync(REPORT_PATH, 'utf-8');
  assert(!content.includes('calcHalfFull'), 'generate_unified_report.py 中发现 calcHalfFull');
});

test('generate_unified_report.py 四维度入口正常', () => {
  const content = fs.readFileSync(REPORT_PATH, 'utf-8');
  assert(content.includes("'wdl'"), "缺少 WDL 维度");
  assert(content.includes("'hcp'"), "缺少 T-005 v3 维度");
  assert(content.includes("'score'"), "缺少 比分维度");
  assert(content.includes("'tg'"), "缺少 总进球维度");
  // 确认没有第5个维度，只匹配 predict_unified 函数内的 result['xxx'] 赋值
  const predictUnifiedMatch = content.match(/def predict_unified\([\s\S]*?(?=\ndef |\n# ==={10,}|\Z)/);
  if (predictUnifiedMatch) {
    const fnBody = predictUnifiedMatch[0];
    const dimMatches = fnBody.match(/result\[['"](\w+)['"]\]\s*=/g) || [];
    const dimNames = [...new Set(dimMatches.map(m => m.replace(/result\[['"]|['"]\]\s*=/g, '')))];
    console.log(`    检测到维度: [${dimNames.join(', ')}]`);
    assert(dimNames.length === 4, `预期 4 个维度，实际 ${dimNames.length} 个: ${dimNames.join(', ')}`);
    assert(dimNames.includes('wdl'), '缺少 wdl 维度');
    assert(dimNames.includes('hcp'), '缺少 hcp 维度');
    assert(dimNames.includes('score'), '缺少 score 维度');
    assert(dimNames.includes('tg'), '缺少 tg 维度');
  }
});

// ============================================================
// 汇总
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`测试完成: ${passed} 通过, ${failed} 失败, ${passed + failed} 总计`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  console.log('\n结论: 存在问题，请检查失败项');
  process.exit(1);
} else {
  console.log('\n结论: 半全场模块已完全移除，其他预测功能正常');
  process.exit(0);
}