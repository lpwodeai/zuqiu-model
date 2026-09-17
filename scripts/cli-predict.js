/**
 * Node.js CLI 入口 - 五大联赛预测模型
 * 用法: node cli-predict.js pl_ars pl_mun
 */

import { readFileSync } from 'fs';

// 动态加载 model-engine.js (浏览器环境下通过script标签加载，这里手动初始化)
const vm = await import('vm');
const fs = await import('fs');
const path = await import('path');

// 读取 model-engine.js 源码
const modelEnginePath = path.join(process.cwd(), 'assets', 'model-engine.js');
let modelCode = fs.readFileSync(modelEnginePath, 'utf-8');

// 创建模拟浏览器环境
const mockWindow = {
  document: { currentScript: null },
  console: console
};

const mockContext = vm.createContext(mockWindow);

// 执行 model-engine.js
try {
  vm.runInContext(modelCode, mockContext);
} catch (e) {
  console.error('加载模型失败:', e.message);
  process.exit(1);
}

const FiveLeagues = mockWindow.FiveLeagues;

// CLI 逻辑
const args = process.argv.slice(2);

function showHelp() {
  console.log(`
⚽ 五大联赛预测模型 - CLI工具

用法:
  node cli-predict.js <球队A> <球队B> [选项]

示例:
  node cli-predict.js pl_ars pl_mun
  node cli-predict.js bl1_bay bl1_dor --bayesian --samples=3000
  node cli-predict.js sa_bar sa_rea --nb --dynamic

选项:
  --bayesian      使用贝叶斯预测 (MCMC后验抽样)
  --nb             使用负二项分布 (替代泊松)
  --dynamic        使用动态评级
  --samples=N      MCMC采样次数 (默认2000)
  --odds=A/D/B     注入市场赔率

球队代码格式: <联赛>_<球队缩写>
  英超(PL): pl_ars(阿森纳), pl_mun(曼联), pl_liv(利物浦), pl_mci(曼城)...
  西甲(SA): sa_bar(巴塞罗那), sa_rea(皇马), sa_val(瓦伦西亚)...
  德甲(BL1): bl1_bay(拜仁), bl1_dor(多特), bl1_fre(弗赖堡)...
  意甲(IT): it_int(国米), it_rom(罗马), it_juv(尤文)...
  法甲(FL1): fl1_psg(巴黎), fl1_mon(摩纳哥)...
`);
}

async function main() {
  if (args.length < 2 || args[0] === '--help' || args[0] === '-h') {
    showHelp();
    return;
  }

  const teamA = args[0].toLowerCase();
  const teamB = args[1].toLowerCase();

  // 解析选项
  const options = {
    useNegativeBinomial: args.includes('--nb'),
    useDynamicRatings: args.includes('--dynamic'),
    useBayesian: args.includes('--bayesian')
  };

  // 解析采样次数
  const samplesArg = args.find(a => a.startsWith('--samples='));
  if (samplesArg) {
    options.samples = parseInt(samplesArg.split('=')[1]);
  }

  // 解析赔率
  const oddsArg = args.find(a => a.startsWith('--odds='));
  if (oddsArg) {
    const [a, d, b] = oddsArg.split('=')[1].split('/').map(Number);
    options.odds = { winA: a, draw: d, winB: b };
  }

  console.log('\n⚽ 五大联赛预测模型 - Bayesian');
  console.log('═══════════════════════════════════════\n');
  console.log(`📊 对阵: ${teamA.toUpperCase()} vs ${teamB.toUpperCase()}`);
  console.log(`⚙️  选项: ${JSON.stringify(options)}\n`);

  // 执行预测
  let result;
  if (options.useBayesian) {
    console.log('📈 使用贝叶斯预测 (MCMC)...');
    result = FiveLeagues.bayesianPredictMatch(teamA, teamB, {
      useNegativeBinomial: options.useNegativeBinomial,
      samples: options.samples || 2000,
      odds: options.odds
    });

    console.log('\n📊 贝叶斯预测结果:');
    console.log(`   ${teamA.toUpperCase()} 胜: ${(result.winA * 100).toFixed(1)}%`);
    console.log(`   平局: ${(result.draw * 100).toFixed(1)}%`);
    console.log(`   ${teamB.toUpperCase()} 胜: ${(result.winB * 100).toFixed(1)}%`);

    if (result.winAInterval) {
      console.log(`\n   主胜90%置信区间: [${(result.winAInterval.q5*100).toFixed(1)}%, ${(result.winAInterval.q95*100).toFixed(1)}%]`);
    }
  } else {
    console.log('📈 使用标准泊松/负二项预测...');
    result = FiveLeagues.predictMatch(teamA, teamB, {
      useNegativeBinomial: options.useNegativeBinomial,
      useDynamicRatings: options.useDynamicRatings
    });

    console.log('\n📊 预测结果:');
    console.log(`   ${teamA.toUpperCase()} 胜: ${(result.winA * 100).toFixed(1)}%`);
    console.log(`   平局: ${(result.draw * 100).toFixed(1)}%`);
    console.log(`   ${teamB.toUpperCase()} 胜: ${(result.winB * 100).toFixed(1)}%`);
  }

  // 显示比分预测
  console.log('\n📋 比分概率 (Top 5):');
  const topScores = result.topScores || result.scoreDistribution?.slice(0, 5);
  if (topScores) {
    topScores.forEach((s, i) => {
      const score = typeof s === 'string' ? s : s.score;
      const prob = typeof s === 'string' ? result.scoreDistribution[s] : s.prob;
      console.log(`   ${i + 1}. ${score}: ${(prob * 100).toFixed(1)}%`);
    });
  }

  // 市场差值分析 (如果有赔率)
  if (options.odds) {
    const market = FiveLeagues.analyzeMarketValue(result, options.odds);
    console.log('\n💰 市场差值分析:');
    if (market.topValue) {
      console.log(`   最佳价值: ${market.topValue.label}`);
      console.log(`   差值: ${(market.topValue.diff * 100).toFixed(1)}%`);
      console.log(`   凯利: ${market.topValue.kelly.toFixed(4)}`);
      console.log(`   置信度: ${market.confidence}`);
    } else {
      console.log('   无明显价值机会');
    }
  }

  // Lambda值
  if (result.lambdaA && result.lambdaB) {
    console.log(`\n⚡ 期望进球: ${teamA.toUpperCase()}=${result.lambdaA.toFixed(2)}, ${teamB.toUpperCase()}=${result.lambdaB.toFixed(2)}`);
  }

  console.log('\n═══════════════════════════════════════\n');
}

main().catch(console.error);
