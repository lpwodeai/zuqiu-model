/* ============================================================
 * FiveLeagues_ROI - ROI 计算与投注分析模块
 * 从 model-engine.js 拆出，纯函数实现，无内部状态依赖
 * ============================================================ */
var FiveLeagues_ROI = (function() {

  // ─── 凯利公式 ───
  function kellyCriterion(modelProb, odds) {
    if (!modelProb || !odds || odds <= 1) return 0;
    var impliedProb = 1 / odds;
    var edge = modelProb - impliedProb;
    return edge / (odds - 1);
  }

  // ─── 按结果类型统计 ───
  function summarizeByOutcome(bets, outcomeType) {
    var filtered = bets.filter(function(b) { return b.outcome === outcomeType; });
    if (filtered.length === 0) return { count: 0, wins: 0, profit: 0 };
    var wins = filtered.filter(function(b) { return b.won; }).length;
    var profit = filtered.reduce(function(s, b) { return s + b.profit; }, 0);
    return {
      count: filtered.length,
      wins: wins,
      losses: filtered.length - wins,
      winRate: (wins / filtered.length * 100).toFixed(1) + '%',
      profit: profit.toFixed(2),
      avgOdds: (filtered.reduce(function(s, b) { return s + b.odds; }, 0) / filtered.length).toFixed(2)
    };
  }

  // ─── 按优势范围统计 ───
  function summarizeByEdge(bets, minEdge, maxEdge) {
    var filtered = bets.filter(function(b) {
      return parseFloat(b.edge) >= minEdge && parseFloat(b.edge) < maxEdge;
    });
    if (filtered.length === 0) return { count: 0, wins: 0, profit: 0 };
    var wins = filtered.filter(function(b) { return b.won; }).length;
    var profit = filtered.reduce(function(s, b) { return s + b.profit; }, 0);
    return {
      count: filtered.length,
      wins: wins,
      winRate: (wins / filtered.length * 100).toFixed(1) + '%',
      profit: profit.toFixed(2),
      avgEdge: (filtered.reduce(function(s, b) { return s + parseFloat(b.edge); }, 0) / filtered.length).toFixed(3)
    };
  }

  // ─── 计算夏普比率 ───
  function calculateSharpeRatio(bets) {
    if (bets.length < 2) return 0;
    var profits = bets.map(function(b) { return b.profit; });
    var mean = profits.reduce(function(s, p) { return s + p; }, 0) / profits.length;
    var variance = profits.map(function(p) { return Math.pow(p - mean, 2); })
      .reduce(function(s, v) { return s + v; }, 0) / (profits.length - 1);
    var std = Math.sqrt(variance);
    return std > 0 ? mean / std : 0;
  }

  // ─── 生成ROI建议 ───
  function generateROIRecommendations(ROI, winRate, maxDrawdownRatio, profitFactor) {
    var recs = [];
    if (winRate < 45) recs.push('提升预测准确率，当前胜率偏低');
    if (maxDrawdownRatio > 0.3) recs.push('降低最大仓位比例，控制回撤风险');
    if (profitFactor < 1.5) recs.push('优化赔率选择，提高盈利因子');
    if (ROI < 5) recs.push('增加价值投注阈值，减少低优势投注');
    if (ROI > 15 && winRate > 55) recs.push('当前策略盈利良好，可适度增加仓位');
    return recs.length > 0 ? recs : ['当前策略表现良好'];
  }

  // ─── 长期ROI计算 ───
  function computeLongTermROI(matchHistory, bankroll, minKellyFraction, options) {
    bankroll = bankroll || 1000;
    minKellyFraction = minKellyFraction || 0.05;
    options = options || {};

    var maxStakePercent = options.maxStakePercent || 0.25;
    var fractionalKelly = options.fractionalKelly || 0.5;

    var currentBankroll = bankroll;
    var bets = [];
    var totalInvested = 0;
    var peakBankroll = bankroll;
    var maxDrawdown = 0;

    for (var i = 0; i < matchHistory.length; i++) {
      var match = matchHistory[i];
      if (!match.predictions || !match.result || !match.marketOdds) continue;

      var pred = match.predictions.stacked || match.predictions.poisson;
      if (!pred) continue;

      var odds = match.marketOdds;

      var kellyA = kellyCriterion(pred.winA || 0.3, odds.winA || 2.0, fractionalKelly);
      var kellyD = kellyCriterion(pred.draw || 0.25, odds.draw || 3.5, fractionalKelly);
      var kellyB = kellyCriterion(pred.winB || 0.3, odds.winB || 4.0, fractionalKelly);

      var betsThisMatch = [];
      var impliedA = odds.winA ? 1 / odds.winA : 0.5;
      var impliedD = odds.draw ? 1 / odds.draw : 0.28;
      var impliedB = odds.winB ? 1 / odds.winB : 0.25;

      if (kellyA > minKellyFraction && (pred.winA || 0) > impliedA) {
        betsThisMatch.push({
          outcome: 'winA', kelly: kellyA, odds: odds.winA || 2.0,
          prob: pred.winA || 0.3, implied: impliedA,
          edge: ((pred.winA || 0.3) - impliedA).toFixed(3)
        });
      }
      if (kellyD > minKellyFraction && (pred.draw || 0) > impliedD) {
        betsThisMatch.push({
          outcome: 'draw', kelly: kellyD, odds: odds.draw || 3.5,
          prob: pred.draw || 0.25, implied: impliedD,
          edge: ((pred.draw || 0.25) - impliedD).toFixed(3)
        });
      }
      if (kellyB > minKellyFraction && (pred.winB || 0) > impliedB) {
        betsThisMatch.push({
          outcome: 'winB', kelly: kellyB, odds: odds.winB || 4.0,
          prob: pred.winB || 0.3, implied: impliedB,
          edge: ((pred.winB || 0.3) - impliedB).toFixed(3)
        });
      }

      for (var j = 0; j < betsThisMatch.length; j++) {
        var bet = betsThisMatch[j];
        var stake = currentBankroll * Math.min(bet.kelly, maxStakePercent);
        stake = Math.max(1, Math.floor(stake));

        var actualOutcome = match.result.goalsA > match.result.goalsB ? 'winA'
          : (match.result.goalsA < match.result.goalsB ? 'winB' : 'draw');

        var won = bet.outcome === actualOutcome;
        var profit = won ? stake * (bet.odds - 1) : -stake;
        currentBankroll += profit;
        totalInvested += stake;

        if (currentBankroll > peakBankroll) peakBankroll = currentBankroll;
        var drawdown = peakBankroll - currentBankroll;
        if (drawdown > maxDrawdown) maxDrawdown = drawdown;

        bets.push({
          matchId: match.matchId,
          date: match.timestamp,
          fixture: (match.teamA || 'A') + ' vs ' + (match.teamB || 'B'),
          outcome: bet.outcome, stake: stake, odds: bet.odds,
          prob: bet.prob.toFixed(3), implied: bet.implied.toFixed(3),
          edge: bet.edge, won: won, profit: profit,
          bankrollAfter: currentBankroll.toFixed(2)
        });
      }
    }

    var totalBets = bets.length;
    var wins = bets.filter(function(b) { return b.won; }).length;
    var losses = totalBets - wins;
    var totalProfit = bets.reduce(function(s, b) { return s + b.profit; }, 0);
    var avgStake = totalBets > 0 ? totalInvested / totalBets : 0;
    var avgOdds = totalBets > 0 ? bets.reduce(function(s, b) { return s + b.odds; }, 0) / totalBets : 0;

    var ROI = bankroll > 0 ? ((currentBankroll - bankroll) / bankroll * 100) : 0;
    var winRate = totalBets > 0 ? (wins / totalBets * 100) : 0;
    var avgProfitPerBet = totalBets > 0 ? totalProfit / totalBets : 0;
    var profitFactor = losses > 0
      ? (bets.filter(function(b) { return b.won; }).reduce(function(s, b) { return s + b.profit; }, 0) /
         Math.abs(bets.filter(function(b) { return !b.won; }).reduce(function(s, b) { return s + b.profit; }, 0)))
      : wins > 0 ? Infinity : 0;
    var sharpeRatio = totalBets > 10 ? calculateSharpeRatio(bets) : 0;

    return {
      summary: {
        initialBankroll: bankroll,
        finalBankroll: currentBankroll.toFixed(2),
        totalProfit: totalProfit.toFixed(2),
        ROI: ROI.toFixed(2) + '%',
        ROIAnnualized: ROI > 0 ? (ROI * 12 / (matchHistory.length || 1)).toFixed(2) + '%' : 'N/A'
      },
      betting: {
        totalBets: totalBets, wins: wins, losses: losses,
        winRate: winRate.toFixed(1) + '%',
        avgStake: avgStake.toFixed(2), avgOdds: avgOdds.toFixed(2),
        avgProfitPerBet: avgProfitPerBet.toFixed(2),
        profitFactor: profitFactor.toFixed(2),
        totalInvested: totalInvested.toFixed(2)
      },
      risk: {
        peakBankroll: peakBankroll.toFixed(2),
        maxDrawdown: maxDrawdown.toFixed(2),
        maxDrawdownPercent: bankroll > 0 ? (maxDrawdown / bankroll * 100).toFixed(2) + '%' : '0%',
        sharpeRatio: sharpeRatio.toFixed(3),
        riskRating: maxDrawdown / bankroll > 0.5 ? '高风险'
          : (maxDrawdown / bankroll > 0.3 ? '中等风险' : '低风险')
      },
      performance: {
        byOutcomeType: {
          winA: summarizeByOutcome(bets, 'winA'),
          draw: summarizeByOutcome(bets, 'draw'),
          winB: summarizeByOutcome(bets, 'winB')
        },
        byEdgeRange: {
          lowEdge: summarizeByEdge(bets, 0, 0.05),
          mediumEdge: summarizeByEdge(bets, 0.05, 0.10),
          highEdge: summarizeByEdge(bets, 0.10, 0.20),
          hugeEdge: summarizeByEdge(bets, 0.20, 1.0)
        }
      },
      conclusion: ROI > 20 ? '模型投注策略盈利能力优秀'
        : (ROI > 10 ? '模型投注策略盈利能力良好'
          : (ROI > 0 ? '模型投注策略小幅盈利'
            : '模型投注策略亏损，需优化价值投注识别')),
      recommendations: generateROIRecommendations(ROI, winRate, maxDrawdown / bankroll, profitFactor),
      betHistory: bets.slice(0, 20)
    };
  }

  return {
    kellyCriterion: kellyCriterion,
    summarizeByOutcome: summarizeByOutcome,
    summarizeByEdge: summarizeByEdge,
    calculateSharpeRatio: calculateSharpeRatio,
    generateROIRecommendations: generateROIRecommendations,
    computeLongTermROI: computeLongTermROI
  };
})();
