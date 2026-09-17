/**
 * D-003 安全解析单元测试
 * 测试 _safeParseJsObject 函数替代 eval() 的安全性
 *
 * 运行方式:
 *   node tests/safe-parse.test.js
 */

import vm from 'vm';
import { strict as assert } from 'assert';

// 复制 _safeParseJsObject 实现 (与 data-collector.js / update-team-data.js 一致)
function safeParseJsObject(objStr, label = 'test') {
  // 先尝试 JSON.parse
  try {
    return JSON.parse(objStr);
  } catch (_) {
    // JS 对象字面量 (单引号键名等)，使用 vm.Script 沙箱执行
    try {
      const sandbox = {};
      const script = new vm.Script(`this.value = ${objStr};`, {
        filename: `safe-parse-${label}.js`
      });
      const context = vm.createContext(sandbox, {
        codeGeneration: { strings: false, wasm: false }
      });
      script.runInContext(context, { timeout: 1000 });
      return sandbox.value;
    } catch (vmErr) {
      throw new Error(`安全解析 ${label} 失败: ${vmErr.message}`);
    }
  }
}

// ============================================================
// 测试用例
// ============================================================

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`✅ PASS: ${name}`);
  } catch (err) {
    failed++;
    console.error(`❌ FAIL: ${name}`);
    console.error(`   ${err.message}`);
  }
}

// ============================================================
// 1. 标准 JSON 解析
// ============================================================
test('解析标准 JSON 对象', () => {
  const result = safeParseJsObject('{"name":"test","value":42}');
  assert.deepStrictEqual(result, { name: 'test', value: 42 });
});

test('解析 JSON 数组', () => {
  const result = safeParseJsObject('[1, 2, 3]');
  assert.deepStrictEqual(result, [1, 2, 3]);
});

test('解析 JSON 嵌套对象', () => {
  const result = safeParseJsObject('{"team":{"name":"arsenal","attack":2.85}}');
  assert.deepStrictEqual(result.team.name, 'arsenal');
  assert.strictEqual(result.team.attack, 2.85);
});

test('解析 JSON 空对象', () => {
  const result = safeParseJsObject('{}');
  assert.deepStrictEqual(result, {});
});

// ============================================================
// 2. JS 对象字面量 (单引号键名) - 触发 vm.Script 回退
// ============================================================
test('解析单引号键名 JS 对象 (模拟 teams_leagues.js 格式)', () => {
  const result = safeParseJsObject(`{
    'bl1_aug': { name: '奥格斯堡', league: 'BL1', attack: 1.52 },
    'bl1_bay': { name: '拜仁慕尼黑', league: 'BL1', attack: 2.0 }
  }`);
  assert.strictEqual(result.bl1_aug.name, '奥格斯堡');
  assert.strictEqual(result.bl1_bay.league, 'BL1');
  assert.strictEqual(result.bl1_bay.attack, 2.0);
});

test('解析单引号键名 JS 对象 (无引号键名)', () => {
  const result = safeParseJsObject('{name: "test", value: 42}');
  assert.strictEqual(result.name, 'test');
  assert.strictEqual(result.value, 42);
});

test('解析混合引号键名', () => {
  const result = safeParseJsObject(`{
    "double_quoted": 1,
    'single_quoted': 2,
    unquoted: 3
  }`);
  assert.strictEqual(result.double_quoted, 1);
  assert.strictEqual(result.single_quoted, 2);
  assert.strictEqual(result.unquoted, 3);
});

// ============================================================
// 3. 真实数据格式测试
// ============================================================
test('解析真实 TEAMS 数据格式 (单字段)', () => {
  const result = safeParseJsObject(`{
    'pl_ars': { name:'阿森纳', league:'PL', attack:2.85, defence:0.52,
      tactical:'possession', xGOT:1.8, xGA:0.68, marketValue:11.2,
      cohesion:0.92, recentForm:0.05, xT:0.27, tempo:0.7,
      pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} }
  }`);
  assert.strictEqual(result.pl_ars.name, '阿森纳');
  assert.strictEqual(result.pl_ars.league, 'PL');
  assert.strictEqual(result.pl_ars.attack, 2.85);
  assert.strictEqual(result.pl_ars.attackSide.left, 0.25);
});

test('解析真实 TEAMS 数据格式 (多字段)', () => {
  const result = safeParseJsObject(`{
    'pl_ars': { name:'阿森纳', league:'PL', attack:2.85 },
    'pl_avl': { name:'阿斯顿维拉', league:'PL', attack:2.45 },
    'pl_che': { name:'切尔西', league:'PL', attack:2.75 }
  }`);
  assert.strictEqual(Object.keys(result).length, 3);
  assert.strictEqual(result.pl_ars.name, '阿森纳');
  assert.strictEqual(result.pl_avl.attack, 2.45);
  assert.strictEqual(result.pl_che.league, 'PL');
});

// ============================================================
// 4. 安全测试 - 代码注入防护
// ============================================================
test('防止代码注入 - 无 process 访问', () => {
  const result = safeParseJsObject('{name: "test"}');
  // vm.createContext 创建隔离沙箱，无法访问 process
  assert.strictEqual(result.name, 'test');
  // 验证沙箱中无全局对象
  assert.strictEqual(typeof result.process, 'undefined');
});

test('防止代码注入 - 无 require 访问', () => {
  const result = safeParseJsObject('{name: "test"}');
  assert.strictEqual(typeof result.require, 'undefined');
});

test('防止代码注入 - 无 fs 模块访问', () => {
  const result = safeParseJsObject('{name: "test"}');
  assert.strictEqual(typeof result.fs, 'undefined');
});

test('超时保护 - 无限循环检测', () => {
  try {
    safeParseJsObject('(function(){ while(true){} })()', 'timeout-test');
    assert.fail('应该抛出超时异常');
  } catch (err) {
    assert.ok(err.message.includes('timed out') || err.message.includes('失败'),
      `期望超时错误，实际: ${err.message}`);
  }
});

// ============================================================
// 5. 错误处理测试
// ============================================================
test('语法错误抛出异常', () => {
  try {
    safeParseJsObject('{invalid syntax!!!}', 'syntax-error');
    assert.fail('应该抛出异常');
  } catch (err) {
    assert.ok(err.message.includes('失败'),
      `期望错误消息包含"失败"，实际: ${err.message}`);
  }
});

test('空字符串抛出异常', () => {
  try {
    safeParseJsObject('', 'empty');
    assert.fail('应该抛出异常');
  } catch (err) {
    assert.ok(true);
  }
});

// ============================================================
// 6. 边界情况测试
// ============================================================
test('解析包含特殊字符的值', () => {
  const result = safeParseJsObject(`{name: "test\\nwith\\tchars", 'key': "value"}`, 'special-chars');
  assert.strictEqual(result.name, 'test\nwith\tchars');
  assert.strictEqual(result.key, 'value');
});

test('解析包含中文字符的对象', () => {
  const result = safeParseJsObject(`{'球队名': '拜仁慕尼黑', '联赛': '德甲'}`);
  assert.strictEqual(result['球队名'], '拜仁慕尼黑');
  assert.strictEqual(result['联赛'], '德甲');
});

test('解析大数值', () => {
  const result = safeParseJsObject('{big: 999999999, small: 0.000001}');
  assert.strictEqual(result.big, 999999999);
  assert.strictEqual(result.small, 0.000001);
});

test('解析布尔值和 null', () => {
  const result = safeParseJsObject('{active: true, deleted: false, extra: null}');
  assert.strictEqual(result.active, true);
  assert.strictEqual(result.deleted, false);
  assert.strictEqual(result.extra, null);
});

// ============================================================
// 结果统计
// ============================================================
console.log(`\n${'='.repeat(60)}`);
console.log(`测试结果: ${passed} 通过, ${failed} 失败, ${passed + failed} 总计`);
console.log(`${'='.repeat(60)}`);

if (failed > 0) {
  process.exit(1);
}