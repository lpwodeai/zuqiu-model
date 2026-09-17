import fetch from 'node-fetch';

const BASE_URL = 'http://localhost:3000';

let passed = 0;
let failed = 0;

function logResult(testName, success, message = '') {
  if (success) {
    console.log(`✅ ${testName}`);
    if (message) console.log(`   ${message}`);
    passed++;
  } else {
    console.log(`❌ ${testName}`);
    if (message) console.log(`   ${message}`);
    failed++;
  }
}

async function testHealthEndpoint() {
  console.log('\n' + '=' * 60);
  console.log('测试1: 健康检查端点');
  console.log('=' * 60);
  
  try {
    const response = await fetch(`${BASE_URL}/api/health`);
    const data = await response.json();
    
    if (data.status === 'healthy' && data.version === '7.5.0') {
      logResult('健康检查端点', true, `版本: ${data.version}, 运行时间: ${data.uptime.toFixed(2)}秒`);
    } else {
      logResult('健康检查端点', false, `状态异常: ${JSON.stringify(data)}`);
    }
  } catch (error) {
    logResult('健康检查端点', false, `请求失败: ${error.message}`);
  }
}

async function testTeamsEndpoint() {
  console.log('\n' + '=' * 60);
  console.log('测试2: 球队列表端点');
  console.log('=' * 60);
  
  try {
    const response = await fetch(`${BASE_URL}/api/predict/teams`);
    const data = await response.json();
    
    if (data.success && data.data && Array.isArray(data.data)) {
      const teamCount = data.data.length;
      const leagues = [...new Set(data.data.map(t => t.league))];
      logResult('球队列表端点', true, `加载 ${teamCount} 支球队, ${leagues.length} 个联赛: ${leagues.join(', ')}`);
      
      const bayern = data.data.find(t => t.key === 'bl1_bay');
      if (bayern && bayern.name === '拜仁慕尼黑') {
        logResult('拜仁慕尼黑数据验证', true, `攻击力: ${bayern.attack}, 防守力: ${bayern.defence}`);
      } else {
        logResult('拜仁慕尼黑数据验证', false, '未找到拜仁慕尼黑数据');
      }
    } else {
      logResult('球队列表端点', false, `响应格式异常: ${JSON.stringify(data)}`);
    }
  } catch (error) {
    logResult('球队列表端点', false, `请求失败: ${error.message}`);
  }
}

async function testPredictEndpoint() {
  console.log('\n' + '=' * 60);
  console.log('测试3: 预测端点');
  console.log('=' * 60);
  
  try {
    const response = await fetch(`${BASE_URL}/api/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        homeTeam: 'bl1_bay',
        awayTeam: 'fl1_psg',
        options: { venue: 'home', neutral: false }
      })
    });
    
    const data = await response.json();
    
    if (data.success && data.data) {
      const pred = data.data;
      
      if (pred.match && pred.match.homeTeam.name === '拜仁慕尼黑' && pred.match.awayTeam.name === '巴黎圣日耳曼') {
        logResult('比赛信息验证', true, `${pred.match.homeTeam.name} vs ${pred.match.awayTeam.name}`);
      } else {
        logResult('比赛信息验证', false, '比赛信息不匹配');
      }
      
      const wdl = pred.predictions.winDrawLose;
      const totalProb = wdl.win + wdl.draw + wdl.lose;
      if (Math.abs(totalProb - 1) < 0.01) {
        logResult('概率归一化验证', true, `胜: ${(wdl.win * 100).toFixed(1)}%, 平: ${(wdl.draw * 100).toFixed(1)}%, 负: ${(wdl.lose * 100).toFixed(1)}%`);
      } else {
        logResult('概率归一化验证', false, `概率和为 ${totalProb.toFixed(4)}, 应为1.0`);
      }
      
      if (pred.ensemble && pred.ensemble.model === 'Stacked-Ensemble-v6.0') {
        const components = Object.keys(pred.ensemble.components);
        logResult('集成模型验证', true, `模型: ${pred.ensemble.model}, 组件: ${components.join(', ')}`);
      } else {
        logResult('集成模型验证', false, '集成模型信息缺失');
      }
      
      if (pred.confidence && pred.confidence.level) {
        logResult('置信度验证', true, `置信度级别: ${pred.confidence.level}, 分数: ${pred.confidence.score.toFixed(4)}`);
      } else {
        logResult('置信度验证', false, '置信度信息缺失');
      }
      
      if (pred.timestamp) {
        logResult('时间戳验证', true, `预测时间: ${pred.timestamp}`);
      } else {
        logResult('时间戳验证', false, '时间戳缺失');
      }
    } else {
      logResult('预测端点', false, `响应格式异常: ${JSON.stringify(data)}`);
    }
  } catch (error) {
    logResult('预测端点', false, `请求失败: ${error.message}`);
  }
}

async function testWithOddsEndpoint() {
  console.log('\n' + '=' * 60);
  console.log('测试4: 融合赔率预测端点');
  console.log('=' * 60);
  
  try {
    const response = await fetch(`${BASE_URL}/api/predict/with-odds`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        homeTeam: 'bl1_bay',
        awayTeam: 'bl1_dor',
        odds: { win: 1.5, draw: 3.8, lose: 5.2 },
        options: { venue: 'home', neutral: false }
      })
    });
    
    const data = await response.json();
    
    if (data.success && data.data) {
      logResult('融合赔率预测端点', true, '预测成功');
      
      if (data.data.predictions && data.data.predictions.winDrawLose) {
        const wdl = data.data.predictions.winDrawLose;
        const totalProb = wdl.win + wdl.draw + wdl.lose;
        if (Math.abs(totalProb - 1) < 0.01) {
          logResult('赔率融合概率验证', true, `胜: ${(wdl.win * 100).toFixed(1)}%, 平: ${(wdl.draw * 100).toFixed(1)}%, 负: ${(wdl.lose * 100).toFixed(1)}%`);
        } else {
          logResult('赔率融合概率验证', false, `概率和为 ${totalProb.toFixed(4)}`);
        }
      }
    } else {
      logResult('融合赔率预测端点', false, `响应格式异常: ${JSON.stringify(data)}`);
    }
  } catch (error) {
    logResult('融合赔率预测端点', false, `请求失败: ${error.message}`);
  }
}

async function testDifferentLeagueMatchup() {
  console.log('\n' + '=' * 60);
  console.log('测试5: 跨联赛对决预测');
  console.log('=' * 60);
  
  try {
    const response = await fetch(`${BASE_URL}/api/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        homeTeam: 'sa_rma',
        awayTeam: 'pl_mci',
        options: { venue: 'neutral', neutral: true }
      })
    });
    
    const data = await response.json();
    
    if (data.success && data.data) {
      logResult('跨联赛对决预测', true, `${data.data.match.homeTeam.name} vs ${data.data.match.awayTeam.name} (中立场地)`);
      
      const lambdaRatio = data.data.lambda.ratio;
      logResult('Lambda比率验证', true, `进攻能力比率: ${lambdaRatio.toFixed(2)}`);
    } else {
      logResult('跨联赛对决预测', false, `响应格式异常: ${JSON.stringify(data)}`);
    }
  } catch (error) {
    logResult('跨联赛对决预测', false, `请求失败: ${error.message}`);
  }
}

async function testTotalGoalsDistribution() {
  console.log('\n' + '=' * 60);
  console.log('测试6: 总进球分布验证');
  console.log('=' * 60);
  
  try {
    const response = await fetch(`${BASE_URL}/api/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        homeTeam: 'bl1_bay',
        awayTeam: 'bl1_aug',
        options: { venue: 'home', neutral: false }
      })
    });
    
    const data = await response.json();
    
    if (data.success && data.data && data.data.predictions && data.data.predictions.totalGoals) {
      const dist = data.data.predictions.totalGoals.distribution;
      const total = Object.values(dist).reduce((a, b) => a + b, 0);
      
      if (Math.abs(total - 1) < 0.01) {
        logResult('总进球分布归一化', true, `概率和: ${total.toFixed(4)}`);
      } else {
        logResult('总进球分布归一化', false, `概率和为 ${total.toFixed(4)}, 应为1.0`);
      }
      
      const over25 = data.data.predictions.totalGoals.over25;
      const under25 = data.data.predictions.totalGoals.under25;
      if (Math.abs(over25 + under25 - 1) < 0.01) {
        logResult('大/小2.5验证', true, `大球: ${(over25 * 100).toFixed(1)}%, 小球: ${(under25 * 100).toFixed(1)}%`);
      } else {
        logResult('大/小2.5验证', false, `大球+小球=${(over25 + under25).toFixed(4)}`);
      }
    } else {
      logResult('总进球分布验证', false, '总进球数据缺失');
    }
  } catch (error) {
    logResult('总进球分布验证', false, `请求失败: ${error.message}`);
  }
}

async function testNegativeCase() {
  console.log('\n' + '=' * 60);
  console.log('测试7: 无效球队请求(异常处理)');
  console.log('=' * 60);
  
  try {
    const response = await fetch(`${BASE_URL}/api/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        homeTeam: 'invalid_team',
        awayTeam: 'bl1_bay',
        options: { venue: 'home', neutral: false }
      })
    });
    
    const data = await response.json();
    
    if (!data.success && data.error) {
      logResult('无效球队异常处理', true, `错误信息: ${data.error}`);
    } else {
      logResult('无效球队异常处理', false, `预期失败但返回成功: ${JSON.stringify(data)}`);
    }
  } catch (error) {
    logResult('无效球队异常处理', false, `请求失败: ${error.message}`);
  }
}

async function main() {
  console.log('🚀 五大联赛预测系统 - 端到端API测试');
  console.log('=' * 60);
  
  await testHealthEndpoint();
  await testTeamsEndpoint();
  await testPredictEndpoint();
  await testWithOddsEndpoint();
  await testDifferentLeagueMatchup();
  await testTotalGoalsDistribution();
  await testNegativeCase();
  
  console.log('\n' + '=' * 60);
  console.log('测试结果汇总');
  console.log('=' * 60);
  console.log(`通过: ${passed}, 失败: ${failed}, 总计: ${passed + failed}`);
  
  if (failed === 0) {
    console.log('\n🎉 所有测试通过！系统运行正常。');
    process.exit(0);
  } else {
    console.log('\n⚠️ 部分测试失败，请检查上述错误信息。');
    process.exit(1);
  }
}

main().catch(error => {
  console.error('❌ 测试运行异常:', error.message);
  process.exit(1);
});
