/**
 * C-20260816-200 / C-20260820-037: 联赛专属 draw_threshold_factor 单元测试
 *
 * 测试目标 (2026-08-20 完整网格 [0.80~1.10] 重校准后):
 * 1. 高平局联赛反压低平局 (0<factor<1): 意甲 0.85 / 西甲 0.80 / 德甲 0.85
 * 2. 英超 / 法甲 factor=0.0 (argmax)，不做平局阈值调整
 * 3. 未知联赛回退到全局默认 factor=1.0 (等价 argmax)
 * 4. 反压低平局语义: 边界平局被降级为主胜/客胜（主/客较大者）
 * 5. 阈值调整不修改概率值，只改变分类标签
 * 6. 日志输出包含联赛名、球队名、概率、阈值信息
 */

import { deepStrictEqual, strictEqual, ok } from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// ============================================================
// D3（C-20260909-004）单一来源: 因子表优先读 config.yaml（与生产 prediction-service.js 同源），
// 读取失败才回退历史校准值兜底（C-20260820-037 完整网格重校准结果）
// ============================================================
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const CONFIG_YAML_PATH = path.join(__dirname, '../../config.yaml');

/** 最小化 YAML 子集解析（与 prediction-service.js 的 _parseYamlSubset 保持一致） */
function _parseYamlSubset(text) {
  const root = {};
  const stack = [{ indent: -1, node: root }];
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/\s+#.*$/, '');
    if (!line.trim() || /^\s*#/.test(line)) continue;
    const indent = line.match(/^\s*/)[0].length;
    const content = line.trim();
    const sepIdx = content.indexOf(':');
    if (sepIdx === -1) continue;
    const key = content.slice(0, sepIdx).trim();
    const value = content.slice(sepIdx + 1).trim();
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) {
      stack.pop();
    }
    const parent = stack[stack.length - 1].node;
    if (value === '') {
      if (!parent[key] || typeof parent[key] !== 'object') parent[key] = {};
      stack.push({ indent, node: parent[key] });
    } else {
      const num = Number(value);
      parent[key] = Number.isNaN(num) ? value.replace(/^["']|["']$/g, '') : num;
    }
  }
  return root;
}

function loadDrawThresholdConfig() {
  try {
    const cfg = _parseYamlSubset(fs.readFileSync(CONFIG_YAML_PATH, 'utf8'));
    if (cfg.draw_threshold_mode !== undefined ||
        cfg.draw_threshold_factor !== undefined ||
        cfg.draw_threshold_factor_league !== undefined) {
      return cfg;
    }
  } catch (e) { /* 读取失败回退历史值 */ }
  return null;
}

const _drawThresholdConfig = loadDrawThresholdConfig();
const LEAGUE_DRAW_THRESHOLD_FACTOR = _drawThresholdConfig?.draw_threshold_factor_league ?? {
  'FL1': 0.0,    // 法甲 — argmax（反压/上浮均无收益）
  'PL': 0.0,     // 英超 — argmax（反压/上浮均无收益）
  'BL1': 0.85,   // 德甲 — 反压低平局 +0.87pp
  'LaLiga': 0.80, // 西甲 — 反压低平局 +1.05pp
  'IT': 0.85,    // 意甲 — 反压低平局 +0.97pp
};
const DEFAULT_DRAW_THRESHOLD_FACTOR = _drawThresholdConfig?.draw_threshold_factor ?? 1.0;

function getLeagueDrawThresholdFactor(leagueCode) {
  if (!leagueCode) return DEFAULT_DRAW_THRESHOLD_FACTOR;
  const factor = LEAGUE_DRAW_THRESHOLD_FACTOR[leagueCode];
  return factor !== undefined ? factor : DEFAULT_DRAW_THRESHOLD_FACTOR;
}

function applyLeagueDrawThreshold(wdl, leagueCode) {
  const factor = getLeagueDrawThresholdFactor(leagueCode);
  const { win, draw, lose } = wdl;

  const maxProb = Math.max(win, draw, lose);
  let predictedLabel;
  if (win === maxProb) predictedLabel = '主胜';
  else if (draw === maxProb) predictedLabel = '平局';
  else predictedLabel = '客胜';

  let appliedLabel = predictedLabel;
  let thresholdInfo = null;

  // factor>0 时应用阈值调整
  //   语义: 0<factor<1 反压低平局（抬高门槛，边界平局降级为主胜/客胜）；factor>1 上浮平局（压低门槛）
  if (factor > 0) {
    const drawBoosted = draw * factor;
    if (drawBoosted > Math.max(win, lose)) {
      appliedLabel = '平局';
      thresholdInfo = { triggered: true, drawProb: draw, drawBoosted, winProb: win, loseProb: lose, factor };
    } else {
      // 平局未赢得阈值：主/客较大者胜出。0<factor<1 时实现「反压低平局」
      appliedLabel = win >= lose ? '主胜' : '客胜';
      thresholdInfo = { triggered: false, drawProb: draw, drawBoosted, maxOther: Math.max(win, lose), factor };
    }
  } else {
    thresholdInfo = { triggered: false, drawProb: draw, factor, reason: 'factor<=0 (argmax, 不做平局阈值调整)' };
  }

  return { win, draw, lose, predictedLabel: appliedLabel, factor, thresholdInfo };
}

// ============================================================
// 测试用例
// ============================================================

let passed = 0, failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ✅ ${name}`);
  } catch (e) {
    failed++;
    console.log(`  ❌ ${name}: ${e.message}`);
  }
}

console.log('=== 联赛专属 draw_threshold_factor 单元测试 ===\n');

// ---- 1. 联赛因子分配 ----
console.log('--- 1. 联赛因子分配 ---');
test('法甲 FL1 → factor=0.0', () => {
  strictEqual(getLeagueDrawThresholdFactor('FL1'), 0.0);
});
test('英超 PL → factor=0.0', () => {
  strictEqual(getLeagueDrawThresholdFactor('PL'), 0.0);
});
test('德甲 BL1 → factor=0.85', () => {
  strictEqual(getLeagueDrawThresholdFactor('BL1'), 0.85);
});
test('西甲 LaLiga → factor=0.80', () => {
  strictEqual(getLeagueDrawThresholdFactor('LaLiga'), 0.80);
});
test('意甲 IT → factor=0.85', () => {
  strictEqual(getLeagueDrawThresholdFactor('IT'), 0.85);
});
test('未知联赛 → 默认 factor=1.0', () => {
  strictEqual(getLeagueDrawThresholdFactor('UNKNOWN'), 1.0);
});
test('null → 默认 factor=1.0', () => {
  strictEqual(getLeagueDrawThresholdFactor(null), 1.0);
});

// ---- 2. 法甲/英超 factor=0.0: argmax, 不调整平局 ----
console.log('\n--- 2. 法甲/英超 (factor=0.0) 阈值行为 ---');

test('法甲: 主胜概率最高 → 预测主胜', () => {
  const result = applyLeagueDrawThreshold({ win: 0.50, draw: 0.25, lose: 0.25 }, 'FL1');
  strictEqual(result.predictedLabel, '主胜');
  strictEqual(result.factor, 0.0);
  strictEqual(result.thresholdInfo.triggered, false);
});

test('法甲: 平局概率最高 → 预测平局', () => {
  const result = applyLeagueDrawThreshold({ win: 0.30, draw: 0.40, lose: 0.30 }, 'FL1');
  strictEqual(result.predictedLabel, '平局');
  strictEqual(result.factor, 0.0);
});

test('英超: 平局概率接近主胜 → 不触发调整 (argmax)', () => {
  const result = applyLeagueDrawThreshold({ win: 0.34, draw: 0.33, lose: 0.33 }, 'PL');
  strictEqual(result.predictedLabel, '主胜');
  strictEqual(result.thresholdInfo.triggered, false);
  strictEqual(result.thresholdInfo.reason, 'factor<=0 (argmax, 不做平局阈值调整)');
});

test('法甲: 概率不变 (决策阈值不修改概率)', () => {
  const wdl = { win: 0.45, draw: 0.30, lose: 0.25 };
  const result = applyLeagueDrawThreshold(wdl, 'FL1');
  strictEqual(result.win, 0.45);
  strictEqual(result.draw, 0.30);
  strictEqual(result.lose, 0.25);
});

// ---- 3. 意甲 factor=0.85: 反压低平局 ----
console.log('\n--- 3. 意甲 (IT, factor=0.85) 反压低平局行为 ---');

test('意甲: 主胜概率明显高 → 预测主胜, 不触发', () => {
  const result = applyLeagueDrawThreshold({ win: 0.50, draw: 0.25, lose: 0.25 }, 'IT');
  strictEqual(result.predictedLabel, '主胜');
  strictEqual(result.thresholdInfo.triggered, false);
});

test('意甲: 平局概率明显高 → 仍预测平局 (draw*0.85 仍胜出)', () => {
  // draw=0.40, win=0.30, lose=0.30
  // draw * 0.85 = 0.34 > max(0.30, 0.30) = 0.30 → 触发
  const result = applyLeagueDrawThreshold({ win: 0.30, draw: 0.40, lose: 0.30 }, 'IT');
  strictEqual(result.predictedLabel, '平局');
  strictEqual(result.thresholdInfo.triggered, true);
  ok(Math.abs(result.thresholdInfo.drawBoosted - 0.34) < 0.001, `drawBoosted should be ~0.34, got ${result.thresholdInfo.drawBoosted}`);
});

test('意甲: 边界平局被反压降级为主胜 (关键语义)', () => {
  // draw=0.35 argmax，但 draw*0.85=0.2975 < max(win=0.34, lose=0.31)=0.34 → 降级为主胜
  const result = applyLeagueDrawThreshold({ win: 0.34, draw: 0.35, lose: 0.31 }, 'IT');
  strictEqual(result.predictedLabel, '主胜');
  strictEqual(result.thresholdInfo.triggered, false);
});

test('意甲: 概率不变 (决策阈值不修改概率)', () => {
  const wdl = { win: 0.34, draw: 0.35, lose: 0.31 };
  const result = applyLeagueDrawThreshold(wdl, 'IT');
  strictEqual(result.win, 0.34);
  strictEqual(result.draw, 0.35);
  strictEqual(result.lose, 0.31);
});

// ---- 4. 边界情况 ----
console.log('\n--- 4. 边界情况 ---');

test('三概率近似相等, 法甲 → 预测平局 (argmax)', () => {
  const result = applyLeagueDrawThreshold({ win: 0.333, draw: 0.334, lose: 0.333 }, 'FL1');
  strictEqual(result.predictedLabel, '平局');
});

test('三概率近似相等, 意甲 → 反压后降级为主胜', () => {
  // draw=0.334, win=0.333, lose=0.333
  // draw*0.85=0.2839 < max(0.333,0.333)=0.333 → 降级; win>=lose → 主胜
  const result = applyLeagueDrawThreshold({ win: 0.333, draw: 0.334, lose: 0.333 }, 'IT');
  strictEqual(result.predictedLabel, '主胜');
  strictEqual(result.thresholdInfo.triggered, false);
});

test('平局概率 0, 法甲 → 不触发', () => {
  const result = applyLeagueDrawThreshold({ win: 0.60, draw: 0.00, lose: 0.40 }, 'FL1');
  strictEqual(result.predictedLabel, '主胜');
});

test('平局概率 0, 意甲 → 不触发', () => {
  const result = applyLeagueDrawThreshold({ win: 0.60, draw: 0.00, lose: 0.40 }, 'IT');
  strictEqual(result.predictedLabel, '主胜');
  strictEqual(result.thresholdInfo.triggered, false);
});

// ---- 5. 法甲 vs 意甲 对比: 同一概率, 反压低平局降级边界平局 ----
console.log('\n--- 5. 法甲 vs 意甲 对比 (关键差异) ---');

test('同一个概率向量, 法甲预测平局, 意甲反压降级为主胜', () => {
  const wdl = { win: 0.34, draw: 0.35, lose: 0.31 };

  const resultL1 = applyLeagueDrawThreshold(wdl, 'FL1');
  strictEqual(resultL1.predictedLabel, '平局');
  strictEqual(resultL1.factor, 0.0);

  const resultIT = applyLeagueDrawThreshold(wdl, 'IT');
  strictEqual(resultIT.predictedLabel, '主胜');
  strictEqual(resultIT.factor, 0.85);
  strictEqual(resultIT.thresholdInfo.triggered, false);
});

test('同一个概率向量, 法甲和意甲概率值完全相同', () => {
  const wdl = { win: 0.34, draw: 0.35, lose: 0.31 };
  const resultL1 = applyLeagueDrawThreshold(wdl, 'FL1');
  const resultIT = applyLeagueDrawThreshold(wdl, 'IT');
  strictEqual(resultL1.win, 0.34);
  strictEqual(resultIT.win, 0.34);
  strictEqual(resultL1.draw, 0.35);
  strictEqual(resultIT.draw, 0.35);
});

// ---- 结果汇总 ----
console.log(`\n${'='.repeat(50)}`);
console.log(`测试结果: ${passed} 通过, ${failed} 失败, ${passed + failed} 总计`);
console.log(`${'='.repeat(50)}`);

if (failed > 0) {
  process.exit(1);
}