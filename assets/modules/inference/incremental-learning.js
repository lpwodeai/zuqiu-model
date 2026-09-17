var FiveLeagues_INCREMENTAL_LEARNING = (function() {
  var weightHistory = [];
  var maxHistorySize = 100;
  var learningRate = 0.1;
  var weightDecay = 0.95;

  function updateWeightsFromResult(matchId, predictions, actualResult) {
    var modelErrors = {};
    var models = ['poisson', 'dixonCole', 'xgboost', 'lightgbm', 'elo'];
    
    models.forEach(function(model) {
      if (predictions[model]) {
        var pred = predictions[model];
        var predOutcome = pred.winA >= pred.draw && pred.winA >= pred.winB ? 'winA' :
                         (pred.draw >= pred.winB ? 'draw' : 'winB');
        modelErrors[model] = predOutcome === actualResult ? 0 : 1;
      } else {
        modelErrors[model] = 0.5;
      }
    });
    
    var currentWeights = FiveLeagues_STACKING.getCurrentWeights();
    var newWeights = {};
    var totalWeight = 0;
    
    models.forEach(function(model) {
      var error = modelErrors[model];
      var currentWeight = currentWeights[model] || 0.15;
      
      newWeights[model] = currentWeight * (1 - learningRate * error) * weightDecay + 
                          (1 - error) * learningRate;
      totalWeight += newWeights[model];
    });
    
    models.forEach(function(model) {
      newWeights[model] /= totalWeight;
    });
    
    newWeights.ssm = newWeights.ssm || 0.15;
    newWeights.lightgbm = newWeights.lightgbm || 0.15;
    
    weightHistory.push({
      timestamp: Date.now(),
      matchId: matchId,
      weights: JSON.parse(JSON.stringify(newWeights)),
      errors: modelErrors
    });
    
    if (weightHistory.length > maxHistorySize) {
      weightHistory.shift();
    }
    
    FiveLeagues_STACKING.setDynamicWeights(newWeights);
    
    return {
      newWeights: newWeights,
      modelErrors: modelErrors,
      historyLength: weightHistory.length
    };
  }

  function getWeightHistory() {
    return weightHistory;
  }

  function rollbackWeights(steps) {
    steps = steps || 1;
    
    if (weightHistory.length <= steps) {
      FiveLeagues_STACKING.setDynamicWeights(null);
      return { success: true, rolledBack: weightHistory.length };
    }
    
    for (var i = 0; i < steps; i++) {
      weightHistory.pop();
    }
    
    var lastValid = weightHistory[weightHistory.length - 1];
    if (lastValid) {
      FiveLeagues_STACKING.setDynamicWeights(lastValid.weights);
    } else {
      FiveLeagues_STACKING.setDynamicWeights(null);
    }
    
    return { success: true, rolledBack: steps };
  }

  function computeWeightVersion() {
    var hash = 0;
    var weights = FiveLeagues_STACKING.getCurrentWeights();
    for (var key in weights) {
      hash = ((hash << 5) - hash) + weights[key];
      hash |= 0;
    }
    return 'v' + weightHistory.length + '-' + Math.abs(hash).toString(36);
  }

  function exportWeights() {
    return {
      version: computeWeightVersion(),
      timestamp: Date.now(),
      weights: FiveLeagues_STACKING.getCurrentWeights(),
      history: weightHistory,
      learningRate: learningRate,
      weightDecay: weightDecay
    };
  }

  function importWeights(data) {
    if (data.weights) {
      FiveLeagues_STACKING.setDynamicWeights(data.weights);
    }
    if (data.history) {
      weightHistory = data.history;
    }
    if (data.learningRate) {
      learningRate = data.learningRate;
    }
    if (data.weightDecay) {
      weightDecay = data.weightDecay;
    }
    return { success: true, version: data.version };
  }

  function getLearningStats() {
    var stats = {
      totalUpdates: weightHistory.length,
      currentWeights: FiveLeagues_STACKING.getCurrentWeights(),
      recentErrors: []
    };
    
    var recent = weightHistory.slice(-10);
    recent.forEach(function(entry) {
      stats.recentErrors.push({
        matchId: entry.matchId,
        errors: entry.errors
      });
    });
    
    return stats;
  }

  return {
    updateWeightsFromResult: updateWeightsFromResult,
    getWeightHistory: getWeightHistory,
    rollbackWeights: rollbackWeights,
    computeWeightVersion: computeWeightVersion,
    exportWeights: exportWeights,
    importWeights: importWeights,
    getLearningStats: getLearningStats
  };
})();