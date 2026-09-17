var FiveLeagues_BACKTEST = (function() {
  function runBacktest(matchHistory, options) {
    options = options || {};
    
    var startDate = options.startDate || null;
    var endDate = options.endDate || null;
    var minConfidence = options.minConfidence || 0;
    
    var filteredMatches = matchHistory.filter(function(m) {
      if (startDate && m.date < startDate) return false;
      if (endDate && m.date > endDate) return false;
      if (!m.result) return false;
      return true;
    });
    
    var results = {
      totalMatches: filteredMatches.length,
      correctPredictions: 0,
      accuracy: 0,
      brierScore: 0,
      profit: 0,
      roi: 0,
      bestModel: '',
      modelAccuracies: {},
      scoreDistribution: {},
      overUnderResults: { over25: { correct: 0, total: 0 }, under25: { correct: 0, total: 0 } }
    };
    
    var modelCounts = {};
    var modelCorrect = {};
    
    filteredMatches.forEach(function(match) {
      if (!match.predictions || !match.result) return;
      
      var actualOutcome = match.result.actualOutcome;
      
      var bestModel = null;
      var bestAccuracy = 0;
      
      ['poisson', 'dixonCole', 'xgboost', 'lightgbm', 'stacked'].forEach(function(model) {
        if (!modelCounts[model]) modelCounts[model] = 0;
        if (!modelCorrect[model]) modelCorrect[model] = 0;
        
        if (match.predictions[model]) {
          modelCounts[model]++;
          var pred = match.predictions[model];
          var predOutcome = pred.winA >= pred.draw && pred.winA >= pred.winB ? 'winA' :
                           (pred.draw >= pred.winB ? 'draw' : 'winB');
          
          if (predOutcome === actualOutcome) {
            modelCorrect[model]++;
            if (model === 'stacked') {
              results.correctPredictions++;
            }
            
            var confidence = Math.max(pred.winA, pred.draw, pred.winB);
            if (confidence > bestAccuracy) {
              bestAccuracy = confidence;
              bestModel = model;
            }
          }
          
          var prob = pred[actualOutcome];
          results.brierScore += Math.pow(prob - 1, 2);
        }
      });
      
      if (match.result.xG_a !== undefined && match.result.xG_b !== undefined) {
        var totalXG = match.result.xG_a + match.result.xG_b;
        var totalGoals = match.result.goalsA + match.result.goalsB;
        var over25Prediction = totalXG > 2.5;
        var over25Actual = totalGoals > 2.5;
        
        if (over25Prediction) {
          results.overUnderResults.over25.total++;
          if (over25Prediction === over25Actual) {
            results.overUnderResults.over25.correct++;
          }
        } else {
          results.overUnderResults.under25.total++;
          if (over25Prediction === over25Actual) {
            results.overUnderResults.under25.correct++;
          }
        }
      }
      
      var scoreKey = match.result.goalsA + '-' + match.result.goalsB;
      results.scoreDistribution[scoreKey] = (results.scoreDistribution[scoreKey] || 0) + 1;
    });
    
    results.accuracy = results.totalMatches > 0 ? 
                      (results.correctPredictions / results.totalMatches) * 100 : 0;
    results.brierScore = results.totalMatches > 0 ? 
                        results.brierScore / results.totalMatches : 0;
    
    for (var model in modelCounts) {
      results.modelAccuracies[model] = modelCounts[model] > 0 ?
                                       (modelCorrect[model] / modelCounts[model]) * 100 : 0;
    }
    
    var maxAccuracy = 0;
    for (var model in results.modelAccuracies) {
      if (results.modelAccuracies[model] > maxAccuracy) {
        maxAccuracy = results.modelAccuracies[model];
        results.bestModel = model;
      }
    }
    
    return results;
  }

  function generateBacktestReport(results) {
    var report = '=== 2026世界杯预测模型回测报告 ===\n\n';
    report += '测试样本数: ' + results.totalMatches + '\n';
    report += '预测准确率: ' + results.accuracy.toFixed(1) + '%\n';
    report += 'Brier Score: ' + results.brierScore.toFixed(4) + '\n';
    report += '最佳模型: ' + results.bestModel + '\n\n';
    report += '各模型准确率:\n';
    
    for (var model in results.modelAccuracies) {
      report += '  ' + model + ': ' + results.modelAccuracies[model].toFixed(1) + '%\n';
    }
    
    report += '\n比分分布:\n';
    var sortedScores = Object.keys(results.scoreDistribution).sort(function(a, b) {
      return results.scoreDistribution[b] - results.scoreDistribution[a];
    });
    sortedScores.slice(0, 10).forEach(function(score) {
      report += '  ' + score + ': ' + results.scoreDistribution[score] + '场\n';
    });
    
    report += '\n大球/小球预测:\n';
    report += '  Over 2.5: ' + results.overUnderResults.over25.correct + '/' + 
              results.overUnderResults.over25.total + ' (' + 
              (results.overUnderResults.over25.total > 0 ? 
              (results.overUnderResults.over25.correct / results.overUnderResults.over25.total * 100).toFixed(1) : 0) + '%)\n';
    report += '  Under 2.5: ' + results.overUnderResults.under25.correct + '/' + 
              results.overUnderResults.under25.total + ' (' + 
              (results.overUnderResults.under25.total > 0 ? 
              (results.overUnderResults.under25.correct / results.overUnderResults.under25.total * 100).toFixed(1) : 0) + '%)\n';
    
    return report;
  }

  function analyzeConfidenceLevels(matchHistory) {
    var levels = {
      high: { correct: 0, total: 0 },
      medium: { correct: 0, total: 0 },
      low: { correct: 0, total: 0 }
    };
    
    matchHistory.forEach(function(m) {
      if (!m.predictions || !m.result) return;
      
      var pred = m.predictions.stacked || m.predictions.poisson;
      if (!pred) return;
      
      var maxProb = Math.max(pred.winA, pred.draw, pred.winB);
      var actual = m.result.actualOutcome;
      var predOutcome = pred.winA >= pred.draw && pred.winA >= pred.winB ? 'winA' :
                       (pred.draw >= pred.winB ? 'draw' : 'winB');
      
      var level;
      if (maxProb >= 0.7) level = 'high';
      else if (maxProb >= 0.5) level = 'medium';
      else level = 'low';
      
      levels[level].total++;
      if (predOutcome === actual) levels[level].correct++;
    });
    
    return {
      high: {
        count: levels.high.total,
        accuracy: levels.high.total > 0 ? (levels.high.correct / levels.high.total) * 100 : 0
      },
      medium: {
        count: levels.medium.total,
        accuracy: levels.medium.total > 0 ? (levels.medium.correct / levels.medium.total) * 100 : 0
      },
      low: {
        count: levels.low.total,
        accuracy: levels.low.total > 0 ? (levels.low.correct / levels.low.total) * 100 : 0
      }
    };
  }

  return {
    runBacktest: runBacktest,
    generateBacktestReport: generateBacktestReport,
    analyzeConfidenceLevels: analyzeConfidenceLevels
  };
})();