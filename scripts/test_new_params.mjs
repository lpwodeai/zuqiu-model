import PredictionEngine from '../shared/prediction-engine.js';

// 阿拉维斯 vs 赫塔费 球队数据
const alaves = {
  name: '阿拉维斯', attack: 1.42, defence: 0.36, xGOT: 0.8, xGA: 1.24, tempo: 0.86, league: 'LaLiga'
};
const getafe = {
  name: '赫塔费', attack: 1.22, defence: 0.43, xGOT: 0.8, xGA: 1.01, tempo: 0.87, league: 'LaLiga'
};

const engine = new PredictionEngine();

// 赔率趋势 (实际数据: 主胜 2.23→2.24, 平局 2.60→2.50, 客胜 3.38→3.55)
const oddsTrend = { homeDown: false, drawDown: true, awayUp: true };

console.log('=== 阿拉维斯 vs 赫塔费 新参数预测效果 ===\n');

// 旧参数
const oldOpts = { venue: 'home', neutral: false, isHome: true, round: 99, league: 'XXX' };
const oldHome = engine.calcLambdaMatch(alaves, getafe, oldOpts);
const oldAway = engine.calcLambdaMatch(getafe, alaves, { ...oldOpts, isHome: false });

// 新参数
const newOpts = { venue: 'home', neutral: false, isHome: true, round: 1, oddsTrend, league: 'LaLiga' };
const newHome = engine.calcLambdaMatch(alaves, getafe, newOpts);
const newAway = engine.calcLambdaMatch(getafe, alaves, { ...newOpts, isHome: false });

// 实际 xG: 1.93 vs 0.24

console.log('## λ 参数对比');
console.log('| 参数 | 主队 λ | 客队 λ | 期望总进球 |');
console.log('|------|:---:|:---:|:---:|');
console.log(`| 旧参数 | ${oldHome.lambdaA.toFixed(3)} | ${oldAway.lambdaA.toFixed(3)} | ${(oldHome.lambdaA+oldAway.lambdaA).toFixed(3)} |`);
console.log(`| 新参数 | ${newHome.lambdaA.toFixed(3)} | ${newAway.lambdaA.toFixed(3)} | ${(newHome.lambdaA+newAway.lambdaA).toFixed(3)} |`);
console.log(`| 实际 xG | 1.93 | 0.24 | 2.17 |`);
console.log(`| 旧 λ 偏差 | ${(oldHome.lambdaA-1.93).toFixed(3)} | ${(oldAway.lambdaA-0.24).toFixed(3)} | — |`);
console.log(`| 新 λ 偏差 | ${(newHome.lambdaA-1.93).toFixed(3)} | ${(newAway.lambdaA-0.24).toFixed(3)} | — |`);

// 乘数分解
const seasonBoost = 1.25;
const oddsBoost = 1.03 * 0.95; // drawDown + awayUp
const leagueBoost = 1.10;
const totalBoost = seasonBoost * oddsBoost * leagueBoost;
console.log('\n## 乘数分解');
console.log('| 因子 | 值 | 说明 |');
console.log('|------|:---:|------|');
console.log(`| 赛季第1轮 | ×1.25 | 前3轮上浮 |`);
console.log(`| 平局赔率↓ (2.60→2.50) | ×1.03 | 双方进球预期↑ |`);
console.log(`| 客胜赔率↑ (3.38→3.55) | ×0.95 | 客队看空 |`);
console.log(`| 主胜赔率→ (2.23→2.24) | ×1.00 | 基本不变 |`);
console.log(`| 西甲联赛 | ×1.10 | 场均2.67球 |`);
console.log(`| 累计乘数 | ×${totalBoost.toFixed(3)} | |`);

// WDL 对比
const oldWdl = engine.calcWinDrawLosePoisson(oldHome.lambdaA, oldAway.lambdaA);
const newWdl = engine.calcWinDrawLosePoisson(newHome.lambdaA, newAway.lambdaA);

console.log('\n## 胜平负概率对比');
console.log('| 结果 | 旧参数 | 新参数 | 变化 |');
console.log('|------|:---:|:---:|:---:|');
console.log(`| 主胜 | ${(oldWdl.winA*100).toFixed(1)}% | ${(newWdl.winA*100).toFixed(1)}% | +${((newWdl.winA-oldWdl.winA)*100).toFixed(1)}pp |`);
console.log(`| 平局 | ${(oldWdl.draw*100).toFixed(1)}% | ${(newWdl.draw*100).toFixed(1)}% | ${((newWdl.draw-oldWdl.draw)*100).toFixed(1)}pp |`);
console.log(`| 客胜 | ${(oldWdl.winB*100).toFixed(1)}% | ${(newWdl.winB*100).toFixed(1)}% | ${((newWdl.winB-oldWdl.winB)*100).toFixed(1)}pp |`);

// 比分 Top-10
const topScores = ['1:0','0:0','1:1','0:1','2:0','2:1','3:0','0:2','2:2','3:1'];
console.log('\n## 比分概率对比 (Top-10)');
console.log('| 比分 | 旧概率 | 新概率 | 变化 | 排名变化 |');
console.log('|:----:|:---:|:---:|:---:|:---:|');

// 计算排名
const allScores = [];
for (let a = 0; a <= 10; a++) {
  for (let b = 0; b <= 10; b++) {
    const oldP = engine.poissonPMF(oldHome.lambdaA, a) * engine.poissonPMF(oldAway.lambdaA, b);
    const newP = engine.poissonPMF(newHome.lambdaA, a) * engine.poissonPMF(newAway.lambdaA, b);
    allScores.push({ score: `${a}:${b}`, oldP, newP });
  }
}
allScores.sort((a, b) => b.newP - a.newP);
const newRank = {};
allScores.forEach((s, i) => newRank[s.score] = i + 1);
allScores.sort((a, b) => b.oldP - a.oldP);
const oldRank = {};
allScores.forEach((s, i) => oldRank[s.score] = i + 1);

for (const s of topScores) {
  const [a, b] = s.split(':').map(Number);
  const oldP = (engine.poissonPMF(oldHome.lambdaA, a) * engine.poissonPMF(oldAway.lambdaA, b) * 100);
  const newP = (engine.poissonPMF(newHome.lambdaA, a) * engine.poissonPMF(newAway.lambdaA, b) * 100);
  const rankChange = (oldRank[s] || '?') + '→' + (newRank[s] || '?');
  const marker = (s === '3:0') ? ' ⚡实际赛果!' : '';
  console.log(`| ${s} | ${oldP.toFixed(2)}% | ${newP.toFixed(2)}% | ${(newP-oldP>=0?'+':'')+(newP-oldP).toFixed(2)}pp | ${rankChange}${marker} |`);
}

// 总进球
console.log('\n## 总进球概率对比');
console.log('| 进球数 | 旧参数 | 新参数 | 变化 |');
console.log('|:---:|:---:|:---:|:---:|');
for (let g = 0; g <= 5; g++) {
  let oldP = 0, newP = 0;
  for (let a = 0; a <= g; a++) {
    oldP += engine.poissonPMF(oldHome.lambdaA, a) * engine.poissonPMF(oldAway.lambdaA, g-a);
    newP += engine.poissonPMF(newHome.lambdaA, a) * engine.poissonPMF(newAway.lambdaA, g-a);
  }
  const marker = (g === 3) ? ' ⚡实际!' : '';
  console.log(`| ${g}球 | ${(oldP*100).toFixed(1)}% | ${(newP*100).toFixed(1)}% | ${(newP-oldP>=0?'+':'')+((newP-oldP)*100).toFixed(1)}pp${marker} |`);
}
let oldOver25 = 0, newOver25 = 0;
for (let g = 0; g <= 10; g++) {
  for (let a = 0; a <= g; a++) {
    if (g >= 3) {
      oldOver25 += engine.poissonPMF(oldHome.lambdaA, a) * engine.poissonPMF(oldAway.lambdaA, g-a);
      newOver25 += engine.poissonPMF(newHome.lambdaA, a) * engine.poissonPMF(newAway.lambdaA, g-a);
    }
  }
}
console.log(`| 大球(>2.5) | ${(oldOver25*100).toFixed(1)}% | ${(newOver25*100).toFixed(1)}% | ${(newOver25-oldOver25>=0?'+':'')+((newOver25-oldOver25)*100).toFixed(1)}pp |`);
console.log(`| 小球(<2.5) | ${(100-oldOver25*100).toFixed(1)}% | ${(100-newOver25*100).toFixed(1)}% | ${(oldOver25-newOver25>=0?'+':'')+((oldOver25-newOver25)*100).toFixed(1)}pp |`);

// 让球(-1)
let oldUp = 0, oldDraw_hcp = 0, oldDown = 0;
let newUp = 0, newDraw_hcp = 0, newDown = 0;
for (let a = 0; a <= 10; a++) {
  for (let b = 0; b <= 10; b++) {
    const oldP = engine.poissonPMF(oldHome.lambdaA, a) * engine.poissonPMF(oldAway.lambdaA, b);
    const newP = engine.poissonPMF(newHome.lambdaA, a) * engine.poissonPMF(newAway.lambdaA, b);
    const adj = a - 1 - b;
    if (adj > 0) { oldUp += oldP; newUp += newP; }
    else if (adj === 0) { oldDraw_hcp += oldP; newDraw_hcp += newP; }
    else { oldDown += oldP; newDown += newP; }
  }
}
console.log('\n## 让球(-1) 概率对比');
console.log('| 结果 | 旧参数 | 新参数 | 变化 |');
console.log('|------|:---:|:---:|:---:|');
console.log(`| 上盘赢 | ${(oldUp*100).toFixed(1)}% | ${(newUp*100).toFixed(1)}% | ${(newUp-oldUp>=0?'+':'')+((newUp-oldUp)*100).toFixed(1)}pp |`);
console.log(`| 走水 | ${(oldDraw_hcp*100).toFixed(1)}% | ${(newDraw_hcp*100).toFixed(1)}% | ${(newDraw_hcp-oldDraw_hcp>=0?'+':'')+((newDraw_hcp-oldDraw_hcp)*100).toFixed(1)}pp |`);
console.log(`| 下盘赢 | ${(oldDown*100).toFixed(1)}% | ${(newDown*100).toFixed(1)}% | ${(newDown-oldDown>=0?'+':'')+((newDown-oldDown)*100).toFixed(1)}pp |`);

console.log('\n## 综合评估');
console.log('| 维度 | 旧预测 | 新预测 | 实际 | 改善? |');
console.log('|------|:---:|:---:|:---:|:---:|');
console.log(`| WDL | 主胜 ${(oldWdl.winA*100).toFixed(1)}% | 主胜 ${(newWdl.winA*100).toFixed(1)}% | 主胜 | ${newWdl.winA > oldWdl.winA ? '✅ 置信度提升' : '→'} |`);
console.log(`| 总进球 | 小球 ${(100-oldOver25*100).toFixed(1)}% | 小球 ${(100-newOver25*100).toFixed(1)}% | 大球(3) | ${newOver25 > oldOver25 ? '✅ 大球概率↑' : '→'} |`);
console.log(`| 3:0 比分 | ${(engine.poissonPMF(oldHome.lambdaA,3)*engine.poissonPMF(oldAway.lambdaA,0)*100).toFixed(2)}% (排名${oldRank['3:0']}) | ${(engine.poissonPMF(newHome.lambdaA,3)*engine.poissonPMF(newAway.lambdaA,0)*100).toFixed(2)}% (排名${newRank['3:0']}) | 3:0 | ${newRank['3:0'] < oldRank['3:0'] ? '✅ 排名上升' : '→'} |`);
console.log(`| 让球(-1) | 下盘 ${(oldDown*100).toFixed(1)}% | 下盘 ${(newDown*100).toFixed(1)}% | 上盘 | ${newUp > oldUp ? '✅ 上盘概率↑' : '→'} |`);