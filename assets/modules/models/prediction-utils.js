/* ============================================================
 * FiveLeagues_PredictionUtils - 预测算法工具函数模块
 * 从 model-engine.js 拆出的纯数学函数，无内部状态依赖
 *
 * 注意：核心预测算法（calcLambda, predictMatch, predictStacked 等）
 * 因与 model-engine.js 内部状态（TEAMS, CONFIG, ELO_RATINGS,
 * SSM_STATE, TACTICAL_MATCHUP, VENUES, WEATHER 等）深度耦合，
 * 暂保留在 model-engine.js 中，待未来整体重构时迁移。
 *
 * 包含: calculateImpliedProbabilities, sampleNormal, poissonWinProb
 * ============================================================ */
var FiveLeagues_PredictionUtils = (function() {

  // ─── 计算赔率隐含概率（去除抽水） ───
  function calculateImpliedProbabilities(odds) {
    if (!odds || !odds.winA || !odds.draw || !odds.winB) {
      return null;
    }

    var pA = 1 / odds.winA;
    var pD = 1 / odds.draw;
    var pB = 1 / odds.winB;

    // 市场抽水
    var overround = pA + pD + pB;

    // 移除抽水后的隐含概率
    var impliedA = pA / overround;
    var impliedD = pD / overround;
    var impliedB = pB / overround;

    return {
      winA: impliedA,
      draw: impliedD,
      winB: impliedB,
      overround: overround,
      margin: (overround - 1) * 100 // 抽水比例(%)
    };
  }

  // ─── Box-Muller 正态分布采样 ───
  function sampleNormal(mean, std) {
    var u1 = Math.random();
    var u2 = Math.random();
    var z0 = Math.sqrt(-2.0 * Math.log(u1)) * Math.cos(2.0 * Math.PI * u2);
    return z0 * std + mean;
  }

  // ─── 简化 Poisson 胜率估算（基于 lambda 比值） ───
  function poissonWinProb(lambdaA, lambdaB) {
    var ratio = lambdaA / lambdaB;
    if (ratio > 2.0) return 0.65;
    if (ratio > 1.5) return 0.55;
    if (ratio > 1.2) return 0.45;
    if (ratio > 1.0) return 0.40;
    return 0.35;
  }

  return {
    calculateImpliedProbabilities: calculateImpliedProbabilities,
    sampleNormal: sampleNormal,
    poissonWinProb: poissonWinProb
  };
})();
