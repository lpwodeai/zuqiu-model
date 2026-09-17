/**
 * C-20260820-039: prediction-service.js 运行时日志验证
 *
 * 目标: 真实调用 prediction-service.js 的 applyLeagueDrawThreshold 接口，
 *       传入不同联赛样本，验证 draw-threshold 日志是否输出正确的
 *       draw_threshold_factor 与决策模式 (argmax / 反压低平局 / 上浮平局)。
 *
 * 运行: node server/tests/test_draw_threshold_runtime_logging.js
 */
import { deepStrictEqual, strictEqual } from 'node:assert';
import service from '../services/prediction-service.js';
import { logger } from '../services/logger.js';

// 捕获 logger.info 输出（仅记录，不写入 DB，保持测试隔离）
const captured = [];
const originalInfo = logger.info.bind(logger);
logger.info = function (category, message, details) {
  captured.push({ category, message, details });
  console.log(`[CAPTURE] [${category}] ${message}`);
};

// 联赛样例 + 期望
// 期望因子对齐 config.yaml: FL1 0.0 / PL 0.0 / BL1 0.85 / IT 0.85 / LaLiga 0.80 / 未知 1.0
const SAMPLES = [
  { label: '法甲 FL1', leagueCode: 'FL1', home: '巴黎圣日耳曼', away: '马赛', wdl: { win: 0.50, draw: 0.25, lose: 0.25 }, expectFactor: 0.0, expectMode: 'argmax' },
  { label: '英超 PL', leagueCode: 'PL', home: '阿森纳', away: '切尔西', wdl: { win: 0.40, draw: 0.38, lose: 0.22 }, expectFactor: 0.0, expectMode: 'argmax' },
  { label: '德甲 BL1', leagueCode: 'BL1', home: '拜仁慕尼黑', away: '多特蒙德', wdl: { win: 0.34, draw: 0.35, lose: 0.31 }, expectFactor: 0.85, expectMode: '反压低平局' },
  { label: '意甲 IT', leagueCode: 'IT', home: '尤文图斯', away: '国际米兰', wdl: { win: 0.30, draw: 0.40, lose: 0.30 }, expectFactor: 0.85, expectMode: '反压低平局' },
  { label: '西甲 LaLiga', leagueCode: 'LaLiga', home: '皇家马德里', away: '巴塞罗那', wdl: { win: 0.36, draw: 0.37, lose: 0.27 }, expectFactor: 0.80, expectMode: '反压低平局' },
  { label: '未知联赛', leagueCode: 'UNKNOWN', home: '甲队', away: '乙队', wdl: { win: 0.33, draw: 0.34, lose: 0.33 }, expectFactor: 1.0, expectMode: 'argmax' },
];

let passed = 0, failed = 0;

function findLog(leagueCode) {
  return captured.filter(r => r.category === 'draw-threshold' && r.details && r.details.leagueCode === leagueCode);
}

console.log('\n=== prediction-service.js 运行时 draw_threshold_factor 日志验证 ===\n');

for (const s of SAMPLES) {
  const result = service.applyLeagueDrawThreshold(s.wdl, s.leagueCode, s.home, s.away);

  // 返回值校验
  strictEqual(result.factor, s.expectFactor, `${s.label}: 返回值 factor 期望 ${s.expectFactor}, 实际 ${result.factor}`);
  passed++; console.log(`  ✅ ${s.label}: 接口返回 factor=${result.factor}, 预测=${result.predictedLabel}`);

  // 日志校验
  const logs = findLog(s.leagueCode);
  if (logs.length === 0) {
    failed++; console.log(`  ❌ ${s.label}: 未捕获到 draw-threshold 日志 (leagueCode=${s.leagueCode})`);
    continue;
  }
  const log = logs[0];
  const d = log.details;

  let ok = true;
  if (d.factor !== s.expectFactor) { failed++; ok = false; console.log(`  ❌ ${s.label}: 日志 factor=${d.factor}, 期望 ${s.expectFactor}`); }
  if (d.mode !== s.expectMode) { failed++; ok = false; console.log(`  ❌ ${s.label}: 日志 mode=${d.mode}, 期望 ${s.expectMode}`); }
  if (ok) { passed++; console.log(`  ✅ ${s.label}: 日志 factor=${d.factor}, mode=${d.mode}, argmax=${d.argmaxLabel} → applied=${d.appliedLabel}, decision=${d.decision}`); }
}

// 校验「反压低平局降级」的关键日志内容
console.log('\n--- 决策模式细节校验 ---');

const bl1Log = findLog('BL1')[0];
if (bl1Log) {
  // 德甲案例: 平局 argmax 被压后降级为主胜
  const d = bl1Log.details;
  if (d.argmaxLabel === '平局' && d.appliedLabel === '主胜' && d.decision === '降级/推升' && d.triggered === false) {
    passed++; console.log('  ✅ 德甲: 边界平局正确降级为主胜 (反压低平局语义生效)');
  } else {
    failed++; console.log(`  ❌ 德甲: 边界平局降级异常 (argmax=${d.argmaxLabel}, applied=${d.appliedLabel}, decision=${d.decision})`);
  }
}

const itLog = findLog('IT')[0];
if (itLog) {
  // 意甲案例: 平局明显更高，反压后仍保持平局
  const d = itLog.details;
  if (d.argmaxLabel === '平局' && d.appliedLabel === '平局' && d.triggered === true) {
    passed++; console.log('  ✅ 意甲: 明显平局仍保持平局 (draw*0.85 仍胜出)');
  } else {
    failed++; console.log(`  ❌ 意甲: 明显平局未保持 (argmax=${d.argmaxLabel}, applied=${d.appliedLabel}, triggered=${d.triggered})`);
  }
}

// 恢复 logger.info
logger.info = originalInfo;

console.log(`\n${'='.repeat(56)}`);
console.log(`测试结果: ${passed} 通过, ${failed} 失败, ${passed + failed} 总计`);
console.log(`${'='.repeat(56)}`);

if (failed > 0) {
  process.exit(1);
}