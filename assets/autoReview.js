/* ============================================================
 * 自动复盘模块 (v1.0)
 * 解决数据流断点问题：赛前预测5维度 vs 赛后验证1维度
 * 自动验证：胜平负/比分/半全场/总进球/让球
 * ============================================================ */

var AUTO_REVIEW = (function() {
  // 验证结果状态
  var STATUS = {
    CORRECT: 'correct',
    INCORRECT: 'incorrect',
    PARTIAL: 'partial',
    UNVERIFIED: 'unverified'
  };

  /**
   * 验证胜平负预测
   */
  function verifyWinDrawWin(prediction, actual) {
    var predResult = prediction.winA > prediction.winB ? 'winA' : 
                     (prediction.winB > prediction.winA ? 'winB' : 'draw');
    
    var actualResult = actual.goalsA > actual.goalsB ? 'winA' : 
                      (actual.goalsB > actual.goalsA ? 'winB' : 'draw');
    
    return {
      type: 'winDrawWin',
      predicted: predResult,
      actual: actualResult,
      correct: predResult === actualResult,
      status: predResult === actualResult ? STATUS.CORRECT : STATUS.INCORRECT,
      confidence: Math.max(prediction.winA, prediction.winB, prediction.draw)
    };
  }

  /**
   * 验证比分预测
   */
  function verifyCorrectScore(prediction, actual) {
    var actualScore = actual.goalsA + '-' + actual.goalsB;
    var topScores = prediction.slice(0, 5); // 取TOP5预测
    
    var exactMatch = topScores.find(function(s) {
      return s.score === actualScore;
    });
    
    var goalDiffMatch = topScores.find(function(s) {
      var parts = s.score.split('-');
      var predA = parseInt(parts[0]), predB = parseInt(parts[1]);
      return (predA - predB) === (actual.goalsA - actual.goalsB);
    });
    
    return {
      type: 'correctScore',
      predicted: topScores.map(function(s) { return s.score + '(' + (s.prob * 100).toFixed(1) + '%)'; }).join(', '),
      actual: actualScore,
      exactCorrect: !!exactMatch,
      goalDiffCorrect: !!goalDiffMatch,
      status: exactMatch ? STATUS.CORRECT : (goalDiffMatch ? STATUS.PARTIAL : STATUS.INCORRECT),
      top1Prob: topScores[0] ? topScores[0].prob : 0
    };
  }

  /**
   * 验证半全场预测
   */
  function verifyHalfTimeFullTime(prediction, actual) {
    var actualHalf = actual.halfTimeA > actual.halfTimeB ? 'H' : 
                    (actual.halfTimeB > actual.halfTimeA ? 'A' : 'D');
    var actualFull = actual.goalsA > actual.goalsB ? 'H' : 
                    (actual.goalsB > actual.goalsA ? 'A' : 'D');
    var actualResult = actualHalf + actualFull;
    
    var top3 = prediction.top3 || [];
    var matched = top3.find(function(hf) {
      return hf.result === actualResult;
    });
    
    var halfCorrect = top3.some(function(hf) {
      return hf.result.charAt(0) === actualHalf;
    });
    
    var fullCorrect = top3.some(function(hf) {
      return hf.result.charAt(1) === actualFull;
    });
    
    return {
      type: 'halfTimeFullTime',
      predicted: top3.map(function(hf) { return hf.result + '(' + (hf.prob * 100).toFixed(1) + '%)'; }).join(', '),
      actual: actualResult,
      correct: !!matched,
      halfCorrect: halfCorrect,
      fullCorrect: fullCorrect,
      status: matched ? STATUS.CORRECT : (halfCorrect || fullCorrect ? STATUS.PARTIAL : STATUS.INCORRECT)
    };
  }

  /**
   * 验证总进球预测
   */
  function verifyTotalGoals(prediction, actual) {
    var total = actual.goalsA + actual.goalsB;
    var over25 = total > 2.5;
    var under25 = total < 2.5;
    var exactly25 = total === 2.5; // 实际不可能，但保留
    
    var predOver = prediction.over25 || 0;
    var predUnder = prediction.under25 || 0;
    
    var predicted = predOver > predUnder ? 'over25' : 'under25';
    var actualResult = over25 ? 'over25' : (under25 ? 'under25' : 'exact25');
    
    return {
      type: 'totalGoals',
      predicted: predicted,
      actual: actualResult,
      correct: predicted === actualResult,
      status: predicted === actualResult ? STATUS.CORRECT : STATUS.INCORRECT,
      expected: prediction.expected || 0,
      actualTotal: total,
      overProb: predOver,
      underProb: predUnder
    };
  }

  /**
   * 验证让球预测
   */
  function verifyHandicap(prediction, actual) {
    if (!prediction || !prediction.handicap) {
      return {
        type: 'handicap',
        predicted: null,
        actual: null,
        correct: null,
        status: STATUS.UNVERIFIED,
        reason: 'No handicap prediction'
      };
    }
    
    var hc = prediction.handicap;
    var line = hc.line || 0;
    
    // 计算让球后结果
    var adjustedA = actual.goalsA - line;
    var adjustedB = actual.goalsB;
    
    var predResult = hc.winA > hc.winB ? 'winA' : 
                     (hc.winB > hc.winA ? 'winB' : 'draw');
    
    var actualResult = adjustedA > adjustedB ? 'winA' : 
                      (adjustedB > adjustedA ? 'winB' : 'draw');
    
    return {
      type: 'handicap',
      line: line,
      predicted: predResult,
      actual: actualResult,
      correct: predResult === actualResult,
      status: predResult === actualResult ? STATUS.CORRECT : STATUS.INCORRECT,
      confidence: Math.max(hc.winA, hc.winB, hc.draw || 0)
    };
  }

  /**
   * 完整复盘一场比赛
   */
  function reviewMatch(prediction, actual) {
    var review = {
      matchId: actual.matchId || prediction.matchId,
      teamA: actual.teamA || prediction.teamA,
      teamB: actual.teamB || prediction.teamB,
      date: actual.date || new Date().toISOString(),
      actualGoals: {
        goalsA: actual.goalsA || 0,
        goalsB: actual.goalsB || 0,
        halfTimeA: actual.halfTimeA || 0,
        halfTimeB: actual.halfTimeB || 0
      },
      dimensions: [],
      summary: {
        totalCorrect: 0,
        totalPartial: 0,
        totalIncorrect: 0,
        totalUnverified: 0,
        overallAccuracy: 0
      }
    };

    // 验证5个维度
    if (prediction.winDrawWin) {
      review.dimensions.push(verifyWinDrawWin(prediction.winDrawWin, actual));
    }
    if (prediction.correctScore) {
      review.dimensions.push(verifyCorrectScore(prediction.correctScore, actual));
    }
    if (prediction.halfTimeFullTime) {
      review.dimensions.push(verifyHalfTimeFullTime(prediction.halfTimeFullTime, actual));
    }
    if (prediction.totalGoals) {
      review.dimensions.push(verifyTotalGoals(prediction.totalGoals, actual));
    }
    if (prediction.handicap) {
      review.dimensions.push(verifyHandicap(prediction.handicap, actual));
    }

    // 计算汇总统计
    review.dimensions.forEach(function(d) {
      switch (d.status) {
        case STATUS.CORRECT:
          review.summary.totalCorrect++;
          break;
        case STATUS.PARTIAL:
          review.summary.totalPartial++;
          break;
        case STATUS.INCORRECT:
          review.summary.totalIncorrect++;
          break;
        case STATUS.UNVERIFIED:
          review.summary.totalUnverified++;
          break;
      }
    });

    // 计算总体准确率 (仅计算已验证的维度)
    var verified = review.summary.totalCorrect + review.summary.totalPartial + review.summary.totalIncorrect;
    review.summary.overallAccuracy = verified > 0 ? 
      (review.summary.totalCorrect + review.summary.totalPartial * 0.5) / verified : 0;

    return review;
  }

  /**
   * 批量复盘多场比赛
   */
  function batchReview(predictions, actualResults) {
    var reviews = [];
    var overall = {
      totalMatches: 0,
      avgAccuracy: 0,
      dimensionStats: {
        winDrawWin: { correct: 0, incorrect: 0, partial: 0, unverified: 0 },
        correctScore: { correct: 0, incorrect: 0, partial: 0, unverified: 0 },
        halfTimeFullTime: { correct: 0, incorrect: 0, partial: 0, unverified: 0 },
        totalGoals: { correct: 0, incorrect: 0, partial: 0, unverified: 0 },
        handicap: { correct: 0, incorrect: 0, partial: 0, unverified: 0 }
      }
    };

    actualResults.forEach(function(actual) {
      var pred = predictions.find(function(p) {
        return p.matchId === actual.matchId || 
               (p.teamA === actual.teamA && p.teamB === actual.teamB);
      });

      if (pred) {
        var review = reviewMatch(pred, actual);
        reviews.push(review);
        
        // 更新总体统计
        overall.totalMatches++;
        overall.avgAccuracy += review.summary.overallAccuracy;
        
        review.dimensions.forEach(function(d) {
          if (overall.dimensionStats[d.type]) {
            overall.dimensionStats[d.type][d.status]++;
          }
        });
      }
    });

    // 计算平均准确率
    overall.avgAccuracy = overall.totalMatches > 0 ? overall.avgAccuracy / overall.totalMatches : 0;

    return {
      reviews: reviews,
      overall: overall,
      timestamp: new Date().toISOString()
    };
  }

  return {
    STATUS: STATUS,
    verifyWinDrawWin: verifyWinDrawWin,
    verifyCorrectScore: verifyCorrectScore,
    verifyHalfTimeFullTime: verifyHalfTimeFullTime,
    verifyTotalGoals: verifyTotalGoals,
    verifyHandicap: verifyHandicap,
    reviewMatch: reviewMatch,
    batchReview: batchReview
  };
})();
