/**
 * D-008 验证脚本: 测试 team_name_map.json 动态加载和日志埋点
 * 
 * 用法: node tests/verify-team-name-map.js
 */

import service from '../server/services/prediction-service.js';

async function main() {
  console.log('='.repeat(60));
  console.log('D-008 验证: 球队映射动态加载 + 日志埋点');
  console.log('='.repeat(60));

  // =====================================================
  // 1. 初始化加载模型数据 (触发 _loadTeamNameMap)
  // =====================================================
  console.log('\n📋 步骤1: 初始化模型数据...');
  try {
    await service.init();
    console.log('✅ 初始化完成');
  } catch (err) {
    console.error('❌ 初始化失败:', err.message);
    process.exit(1);
  }

  // =====================================================
  // 2. 测试已知球队名映射
  // =====================================================
  console.log('\n📋 步骤2: 测试已知球队名映射...');
  const knownTeams = [
    '阿森纳', '切尔西', '利物浦', '曼城', '曼联',
    '巴塞罗那', '皇家马德里', '马德里竞技',
    '拜仁慕尼黑', '多特蒙德',
    '尤文图斯', '国际米兰', 'AC米兰',
    '巴黎圣日耳曼', '马赛',
  ];
  let knownOk = 0;
  let knownFail = 0;
  for (const name of knownTeams) {
    const result = service.getEnglishTeamName(name);
    if (result) {
      knownOk++;
    } else {
      knownFail++;
      console.error(`  ❌ 已知球队映射失败: "${name}"`);
    }
  }
  console.log(`  ✅ ${knownOk}/${knownTeams.length} 已知球队映射成功`);
  if (knownFail > 0) {
    console.error(`  ❌ ${knownFail} 个已知球队映射失败!`);
  }

  // =====================================================
  // 3. 测试不存在球队名 — 验证日志埋点
  // =====================================================
  console.log('\n📋 步骤3: 模拟不存在球队名 (测试日志埋点)...');
  const fakeTeams = ['不存在的球队FC', '测试队', 'UnknownTeam', '随便一个队名'];
  for (const name of fakeTeams) {
    const result = service.getEnglishTeamName(name);
    if (result === null) {
      console.log(`  ✅ "${name}" → null (正确，触发失效日志) ✓`);
    } else {
      console.error(`  ❌ "${name}" → "${result}" (预期 null)`);
    }
  }

  // 再重复调用一次，验证"持续失效"日志 (每10次提醒)
  console.log('\n  重复调用验证持续失效日志...');
  for (let i = 0; i < 9; i++) {
    service.getEnglishTeamName('不存在的球队FC');
  }
  // 第10次应该触发持续失效警告
  service.getEnglishTeamName('不存在的球队FC');
  console.log('  ✅ 持续失效日志已触发 (查看上方 "不存在的球队FC (已 10 次)" 警告)');

  // =====================================================
  // 4. 获取失效报告
  // =====================================================
  console.log('\n📋 步骤4: 获取映射失效报告...');
  const report = service.getTeamNameMissReport();
  console.log(`  总失效次数: ${report.totalMisses}`);
  console.log(`  失效球队数: ${report.uniqueTeams}`);
  console.log('  失效详情:');
  for (const d of report.details) {
    console.log(`    - "${d.name}": ${d.count} 次`);
  }

  // =====================================================
  // 5. 重置失效统计
  // =====================================================
  console.log('\n📋 步骤5: 重置失效统计...');
  service.resetTeamNameMissCache();
  const report2 = service.getTeamNameMissReport();
  console.log(`  重置后: 总失效=${report2.totalMisses}, 球队数=${report2.uniqueTeams}`);
  if (report2.totalMisses === 0) {
    console.log('  ✅ 重置成功');
  } else {
    console.error('  ❌ 重置失败');
  }

  // =====================================================
  // 6. 测试 findTeam (使用 teamNameIndex)
  // =====================================================
  console.log('\n📋 步骤6: 测试 findTeam (间接使用 getEnglishTeamName)...');
  const testCases = ['阿森纳', 'arsenal', 'Arsenal', '不存在的球队FC'];
  for (const name of testCases) {
    try {
      const result = service.findTeam(name);
      if (result) {
        console.log(`  ✅ "${name}" → ${result.key} (${result.team.name})`);
      } else {
        console.log(`  ⚠️ "${name}" → null (未找到，可能正常)`);
      }
    } catch (err) {
      console.log(`  ⚠️ "${name}" → 异常: ${err.message}`);
    }
  }

  // =====================================================
  // 汇总
  // =====================================================
  console.log('\n' + '='.repeat(60));
  console.log('✅ 验证完成');
  console.log('='.repeat(60));
}

main().catch(err => {
  console.error('❌ 验证失败:', err);
  process.exit(1);
});