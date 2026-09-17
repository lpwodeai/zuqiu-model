var FiveLeagues_POISSON = (function() {
  var MATCH_TYPE_HIERARCHY = {
    friendly: { weight: 0.60, lambdaScale: 1.15, drawBoost: 0.05 },
    qualifier: { weight: 0.85, lambdaScale: 1.05, drawBoost: 0.02 },
    groupStage: { weight: 1.00, lambdaScale: 1.00, drawBoost: 0.00 },
    round32: { weight: 1.20, lambdaScale: 0.90, drawBoost: 0.05 },
    round16: { weight: 1.25, lambdaScale: 0.88, drawBoost: 0.05 },
    quarterFinal: { weight: 1.30, lambdaScale: 0.85, drawBoost: 0.05 },
    semiFinal: { weight: 1.35, lambdaScale: 0.82, drawBoost: 0.03 },
    final: { weight: 1.40, lambdaScale: 0.80, drawBoost: 0.02 }
  };

  function predictMatch(teamA, teamB, options) {
    options = options || {};
    var neutral = options.neutral || true;
    var matchType = options.matchType || 'groupStage';
    var temperature = options.temperature || 20;
    
    var tA = typeof teamA === 'string' ? FiveLeagues_TEAMS.getTeam(teamA) : teamA;
    var tB = typeof teamB === 'string' ? FiveLeagues_TEAMS.getTeam(teamB) : teamB;
    
    if (!tA || !tB) {
      return { winA: 0.33, draw: 0.34, winB: 0.33, lambdaA: 1.0, lambdaB: 1.0 };
    }

    var hierarchy = MATCH_TYPE_HIERARCHY[matchType] || MATCH_TYPE_HIERARCHY.groupStage;
    
    var homeAdv = neutral ? 1.0 : 1.12;
    
    var attackA = tA.attack * (tA.injury || 1.0) * (tA.keyPlayer || 1.0);
    var attackB = tB.attack * (tB.injury || 1.0) * (tB.keyPlayer || 1.0);
    var defenceA = tA.defence;
    var defenceB = tB.defence;

    var heatFactorA = tA.heatResistance || 0.95;
    var heatFactorB = tB.heatResistance || 0.95;
    
    if (temperature > 30) {
      heatFactorA *= 0.90;
      heatFactorB *= 0.85;
    }

    // defence = 防守强度 (0~1, 越高=防守越好=越难进球), 详见 team_attributes.json
    // 使用 (1 - defence) 转换为防守漏洞率, 使公式方向正确: 强进攻 × 弱防守 → 高进球期望
    var lambdaA = (attackA * (1 - defenceB) * homeAdv * hierarchy.lambdaScale) * heatFactorA;
    var lambdaB = (attackB * (1 - defenceA) * (neutral ? 1.0 : 0.95) * hierarchy.lambdaScale) * heatFactorB;

    lambdaA = Math.max(0.3, Math.min(3.5, lambdaA));
    lambdaB = Math.max(0.3, Math.min(3.5, lambdaB));

    var maxGoals = 10;
    var probA = [];
    var probB = [];
    
    for (var i = 0; i <= maxGoals; i++) {
      probA[i] = Math.exp(-lambdaA) * Math.pow(lambdaA, i) / factorial(i);
      probB[i] = Math.exp(-lambdaB) * Math.pow(lambdaB, i) / factorial(i);
    }

    var winA = 0, draw = 0, winB = 0;
    for (var ga = 0; ga <= maxGoals; ga++) {
      for (var gb = 0; gb <= maxGoals; gb++) {
        var p = probA[ga] * probB[gb];
        if (ga > gb) winA += p;
        else if (ga === gb) draw += p;
        else winB += p;
      }
    }

    var total = winA + draw + winB;
    winA /= total; draw /= total; winB /= total;
    
    var drawBonus = hierarchy.drawBoost;
    if (drawBonus > 0) {
      var reduce = drawBonus / 2;
      winA = Math.max(0.01, winA - reduce);
      winB = Math.max(0.01, winB - reduce);
      draw = Math.min(0.98, draw + drawBonus);
    }

    return {
      winA: winA,
      draw: draw,
      winB: winB,
      lambdaA: lambdaA,
      lambdaB: lambdaB,
      model: 'Poisson-v7.7'
    };
  }

  function factorial(n) {
    if (n <= 1) return 1;
    var result = 1;
    for (var i = 2; i <= n; i++) result *= i;
    return result;
  }

  function predictScoreProbabilities(lambdaA, lambdaB, maxGoals) {
    maxGoals = maxGoals || 5;
    var probs = {};
    
    for (var ga = 0; ga <= maxGoals; ga++) {
      for (var gb = 0; gb <= maxGoals; gb++) {
        var pa = Math.exp(-lambdaA) * Math.pow(lambdaA, ga) / factorial(ga);
        var pb = Math.exp(-lambdaB) * Math.pow(lambdaB, gb) / factorial(gb);
        probs[ga + '-' + gb] = pa * pb;
      }
    }
    
    return probs;
  }

  return {
    predictMatch: predictMatch,
    predictScoreProbabilities: predictScoreProbabilities,
    MATCH_TYPE_HIERARCHY: MATCH_TYPE_HIERARCHY
  };
})();