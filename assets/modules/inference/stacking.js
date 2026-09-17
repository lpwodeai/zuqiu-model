var FiveLeagues_STACKING = (function() {
  var STACKING_WEIGHTS = {
    poisson: 0.25,
    dixonCole: 0.15,
    ssm: 0.15,
    xgboost: 0.20,
    lightgbm: 0.15,
    elo: 0.10
  };

  var dynamicWeights = null;

  function setDynamicWeights(weights) {
    dynamicWeights = weights;
  }

  function getCurrentWeights() {
    return dynamicWeights || STACKING_WEIGHTS;
  }

  function getDelegateFunction(funcName) {
    if (typeof window !== 'undefined' && window.FiveLeaguesEngine && typeof window.FiveLeaguesEngine[funcName] === 'function') {
      return window.FiveLeaguesEngine[funcName].bind(window.FiveLeaguesEngine);
    }
    if (typeof global !== 'undefined' && global.FiveLeaguesEngine && typeof global.FiveLeaguesEngine[funcName] === 'function') {
      return global.FiveLeaguesEngine[funcName].bind(global.FiveLeaguesEngine);
    }
    return null;
  }

  function predictStacked(teamAKey, teamBKey, options) {
    options = options || {};

    var poisson = FiveLeagues_POISSON ? FiveLeagues_POISSON.predictMatch(teamAKey, teamBKey, options) :
                  { winA: 0.33, draw: 0.34, winB: 0.33 };
    
    var predictMatchDC = getDelegateFunction('predictMatchDC');
    var dc = predictMatchDC ? predictMatchDC(teamAKey, teamBKey, options) :
             { winA: 0.33, draw: 0.34, winB: 0.33 };
    
    var predictMatchSSMFull = getDelegateFunction('predictMatchSSMFull');
    var ssmFull = predictMatchSSMFull ? predictMatchSSMFull(teamAKey, teamBKey, options) :
                  { winA: 0.33, draw: 0.34, winB: 0.33 };
    
    var predictXGB = getDelegateFunction('predictXGB');
    var xgb = predictXGB ? predictXGB(teamAKey, teamBKey, options) :
              { winA: 0.33, draw: 0.34, winB: 0.33 };
    
    var predictLightGBM = getDelegateFunction('predictLightGBM');
    var lgb = predictLightGBM ? predictLightGBM(teamAKey, teamBKey, options) :
              { winA: 0.33, draw: 0.34, winB: 0.33 };

    var tA = FiveLeagues_TEAMS ? FiveLeagues_TEAMS.getTeam(teamAKey) : null;
    var tB = FiveLeagues_TEAMS ? FiveLeagues_TEAMS.getTeam(teamBKey) : null;
    
    var getEloRating = getDelegateFunction('getEloRating');
    var eloA = getEloRating ? getEloRating(teamAKey) : 1800;
    var eloB = getEloRating ? getEloRating(teamBKey) : 1800;
    
    var recentFormA = tA ? tA.recentForm || 0 : 0;
    var recentFormB = tB ? tB.recentForm || 0 : 0;
    var injuryA = tA ? tA.injury || 1.0 : 1.0;
    var injuryB = tB ? tB.injury || 1.0 : 1.0;
    
    var dynEloA = eloA + recentFormA * 30 + (1 - injuryA) * -50;
    var dynEloB = eloB + recentFormB * 30 + (1 - injuryB) * -50;
    
    var eloDiff = dynEloA - dynEloB;
    var isNeutral = options.neutral || false;
    var homeAdvElo = isNeutral ? 0 : 65;
    
    var eloProbA_raw = eloExpected(dynEloA + homeAdvElo, dynEloB);
    var eloProbB_raw = 1 - eloExpected(dynEloA, dynEloB + homeAdvElo);
    var eloDrawEst = 0.26 * Math.exp(-Math.abs(eloDiff) / 600);
    
    var eloNonDraw = 1 - eloDrawEst;
    var eloProbA = eloProbA_raw * eloNonDraw / Math.max(0.01, eloProbA_raw + eloProbB_raw);
    var eloProbB = eloProbB_raw * eloNonDraw / Math.max(0.01, eloProbA_raw + eloProbB_raw);
    var eloDraw = eloDrawEst;

    var w = getCurrentWeights();
    
    var winA = w.poisson * poisson.winA + w.dixonCole * dc.winA + 
               w.ssm * ssmFull.winA + w.xgboost * xgb.winA + 
               w.lightgbm * lgb.winA + w.elo * eloProbA;
    var draw = w.poisson * poisson.draw + w.dixonCole * dc.draw + 
               w.ssm * ssmFull.draw + w.xgboost * xgb.draw + 
               w.lightgbm * lgb.draw + w.elo * eloDraw;
    var winB = w.poisson * poisson.winB + w.dixonCole * dc.winB + 
               w.ssm * ssmFull.winB + w.xgboost * xgb.winB + 
               w.lightgbm * lgb.winB + w.elo * eloProbB;

    var total = winA + draw + winB;
    winA /= total; draw /= total; winB /= total;

    return {
      winA: winA,
      draw: draw,
      winB: winB,
      components: {
        poisson:  { winA: poisson.winA,  draw: poisson.draw,  winB: poisson.winB,  weight: w.poisson },
        dixonCole:{ winA: dc.winA,       draw: dc.draw,       winB: dc.winB,       weight: w.dixonCole },
        ssm:      { winA: ssmFull.winA,  draw: ssmFull.draw,  winB: ssmFull.winB,  weight: w.ssm },
        xgboost:  { winA: xgb.winA,     draw: xgb.draw,     winB: xgb.winB,     weight: w.xgboost },
        lightgbm: { winA: lgb.winA,     draw: lgb.draw,     winB: lgb.winB,     weight: w.lightgbm },
        elo:      { winA: eloProbA,     draw: eloDraw,      winB: eloProbB,     weight: w.elo }
      },
      dynamicWeights: !!dynamicWeights,
      model: 'Stacked-Ensemble-v7.7'
    };
  }

  function computeDynamicWeightsFromHistory(matchHistory) {
    if (!matchHistory || matchHistory.length < 5) return null;
    
    var recentMatches = matchHistory.slice(-10);
    
    var errors = {
      poisson: 0, dixonCole: 0, ssm: 0, xgboost: 0, lightgbm: 0, elo: 0
    };
    var counts = {
      poisson: 0, dixonCole: 0, ssm: 0, xgboost: 0, lightgbm: 0, elo: 0
    };
    
    for (var i = 0; i < recentMatches.length; i++) {
      var m = recentMatches[i];
      if (!m.predictions || !m.result) continue;
      
      var actual = m.result.actualOutcome;
      
      ['poisson', 'dixonCole', 'xgboost', 'lightgbm'].forEach(function(model) {
        if (m.predictions[model]) {
          var pred = m.predictions[model];
          var predOutcome = pred.winA >= pred.draw && pred.winA >= pred.winB ? 'winA' :
                           (pred.draw >= pred.winB ? 'draw' : 'winB');
          errors[model] += predOutcome === actual ? 0 : 1;
          counts[model]++;
        }
      });
    }
    
    var accuracies = {};
    var totalAccuracy = 0;
    ['poisson', 'dixonCole', 'ssm', 'xgboost', 'lightgbm', 'elo'].forEach(function(model) {
      accuracies[model] = counts[model] > 0 ? 1 - (errors[model] / counts[model]) : 0.5;
      totalAccuracy += accuracies[model];
    });
    
    var weights = {};
    ['poisson', 'dixonCole', 'ssm', 'xgboost', 'lightgbm', 'elo'].forEach(function(model) {
      weights[model] = totalAccuracy > 0 ? accuracies[model] / totalAccuracy : STACKING_WEIGHTS[model];
    });
    
    return weights;
  }

  function eloExpected(rA, rB) {
    return 1 / (1 + Math.pow(10, (rB - rA) / 400));
  }

  return {
    predictStacked: predictStacked,
    setDynamicWeights: setDynamicWeights,
    getCurrentWeights: getCurrentWeights,
    computeDynamicWeightsFromHistory: computeDynamicWeightsFromHistory,
    STACKING_WEIGHTS: STACKING_WEIGHTS
  };
})();