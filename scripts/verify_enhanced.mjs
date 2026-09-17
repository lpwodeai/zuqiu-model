// 五大联赛预测模型增强功能验证测试
import { readFileSync } from 'fs';
import vm from 'vm';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const modelEnginePath = path.join(__dirname, 'assets', 'model-engine.js');
const modelCode = readFileSync(modelEnginePath, 'utf-8');

const mockWindow = { document: { currentScript: null }, console: console };
const mockContext = vm.createContext(mockWindow);
vm.runInContext(modelCode, mockContext);

const FiveLeagues = mockWindow.FiveLeagues;

// 测试增强分析 - 使用五大联赛球队代码
const result = FiveLeagues.predictMatch('pl_ars', 'pl_mun', { useNegativeBinomial: true });

console.log('=== 五大联赛比分预测增强功能验证 ===\n');

console.log('基础预测:', (result.winA * 100).toFixed(1) + '%', 'vs', (result.winB * 100).toFixed(1) + '%');

if (result.enhanced) {
  console.log('\n【首选/次选】');
  console.log('  首选:', result.enhanced.firstChoice?.score, '-', (result.enhanced.firstChoice?.prob * 100).toFixed(1) + '%');
  console.log('  次选:', result.enhanced.secondChoice?.score, '-', (result.enhanced.secondChoice?.prob * 100).toFixed(1) + '%');
  
  console.log('\n【大比分首选/次选】');
  console.log('  大比分首选:', result.enhanced.bigGoalFirst?.score, '-', (result.enhanced.bigGoalFirst?.prob * 100).toFixed(1) + '%');
  console.log('  大比分次选:', result.enhanced.bigGoalSecond?.score, '-', (result.enhanced.bigGoalSecond?.prob * 100).toFixed(1) + '%');
  
  console.log('\n【大小球】');
  console.log('  大球(>=3球):', (result.enhanced.over25 * 100).toFixed(1) + '%');
  console.log('  小球(<3球):', (result.enhanced.under25 * 100).toFixed(1) + '%');
  
  console.log('\n【爆冷概率】:', (result.enhanced.upsetProbability * 100).toFixed(1) + '%');
  
  console.log('\n【比分排名 Top 5】');
  result.enhanced.rankedScores.slice(0, 5).forEach(s => {
    console.log('  ' + s.rank + '. ' + s.score + ': ' + (s.prob * 100).toFixed(1) + '%');
  });
}

if (result.upsetRisk) {
  console.log('\n【防爆冷风险】');
  console.log('  风险等级:', result.upsetRisk.riskLevel);
  console.log('  风险值:', (result.upsetRisk.totalRisk * 100).toFixed(1) + '%');
  if (result.upsetRisk.riskFactors.length > 0) {
    result.upsetRisk.riskFactors.forEach(f => console.log('  -', f.detail));
  }
  if (result.upsetRisk.suggestions.length > 0) {
    console.log('  建议:', result.upsetRisk.suggestions.join(', '));
  }
}

console.log('\n✅ 比分预测增强功能验证完成!');