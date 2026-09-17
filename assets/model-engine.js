/* ============================================================
 * 五大联赛预测引擎 v7.5 - 前端UI模块
 * 保留功能: 数据加载、UI交互、赔率分析、实时数据流、数据质量验证
 * 核心算法已迁移至服务器端: shared/prediction-engine.js
 * ============================================================ */

var ROI = (typeof FiveLeagues_ROI !== 'undefined') ? FiveLeagues_ROI : null;
var Calibration = (typeof FiveLeagues_Calibration !== 'undefined') ? FiveLeagues_Calibration : null;
var QualityControl = (typeof FiveLeagues_QualityControl !== 'undefined') ? FiveLeagues_QualityControl : null;
var SquadParser = (typeof FiveLeagues_SquadParser !== 'undefined') ? FiveLeagues_SquadParser : null;
var TacticalAnalyzer = (typeof FiveLeagues_TacticalAnalyzer !== 'undefined') ? FiveLeagues_TacticalAnalyzer : null;
var PredictionUtils = (typeof FiveLeagues_PredictionUtils !== 'undefined') ? FiveLeagues_PredictionUtils : null;

var FiveLeaguesEngine = (function() {

  var TEAMS = {};

  var FiveLeaguesEngine_LIVE_STATS = {
    goalsScored: {},
    goalsConceded: {},
    assists: {},
    shots: {},
    shotsOnTarget: {},
    keyPasses: {},
    corners: {},
    aerialDuelsTotal: {},
    aerialDuelsWon: {},
    groundDuelsTotal: {},
    groundDuelsWon: {},
    tackles: {},
    blocks: {},
    goalCreatingChances: {},
    bigChancesMissed: {},
    saves: {}
  };

  var BEST_XI = {};

  function parseSquadDepth(teamKey, squadData) {
    if (!squadData) return { completeness: 1.0, keyPlayerImpact: 1.0, depthScore: 0.5, warning: '无阵容数据' };

    var team = TEAMS[teamKey];
    if (!team) return { completeness: 1.0, keyPlayerImpact: 1.0, depthScore: 0.5, warning: '未知球队' };

    var startingXI = squadData.startingXI || [];
    var absent = squadData.absent || [];
    var keyPlayers = squadData.keyPlayers || {};

    var completeness = Math.min(1.0, startingXI.length / 11);

    var keyPlayerImpact = 1.0;
    var missingKeyPlayers = [];
    for (var name in keyPlayers) {
      if (keyPlayers[name].status !== 'fit') {
        missingKeyPlayers.push(name);
        keyPlayerImpact *= 0.9;
      }
    }

    var depthScore = Math.min(1.0, 0.5 + (startingXI.length / 11) * 0.5);

    return {
      completeness: completeness,
      keyPlayerImpact: keyPlayerImpact,
      depthScore: depthScore,
      warning: missingKeyPlayers.length > 0 ? '关键球员缺阵: ' + missingKeyPlayers.join(', ') : null,
      missingKeyPlayers: missingKeyPlayers,
      startingXI: startingXI,
      absent: absent
    };
  }

  function applySquadDepthAdjustment(teamKey, squadDepth) {
    var team = TEAMS[teamKey];
    if (!team) return;
    team._originalAttack = team.attack;
    team._originalDefence = team.defence;
    team.attack *= squadDepth.keyPlayerImpact;
    team.defence *= squadDepth.completeness;
  }

  function restoreTeamOriginalStats(teamKey) {
    var team = TEAMS[teamKey];
    if (!team) return;
    if (team._originalAttack !== undefined) team.attack = team._originalAttack;
    if (team._originalDefence !== undefined) team.defence = team._originalDefence;
    delete team._originalAttack;
    delete team._originalDefence;
  }

  function parseRecentForm(teamKey, formData) {
    if (!formData) return { formScore: 0, goalsForAvg: 0, goalsAgainstAvg: 0, winRate: 0, warning: '无近期战绩数据' };

    var recentMatches = formData.matches || [];
    if (recentMatches.length === 0) return { formScore: 0, goalsForAvg: 0, goalsAgainstAvg: 0, winRate: 0, warning: '无近期战绩数据' };

    var totalGoalsFor = 0, totalGoalsAgainst = 0, wins = 0, draws = 0, losses = 0;

    for (var i = 0; i < recentMatches.length; i++) {
      var m = recentMatches[i];
      totalGoalsFor += m.goalsFor || 0;
      totalGoalsAgainst += m.goalsAgainst || 0;
      if (m.result === 'win') wins++;
      else if (m.result === 'draw') draws++;
      else losses++;
    }

    var n = recentMatches.length;
    var formScore = (wins * 2 + draws - losses) / (n * 2);
    var winRate = wins / n;

    return {
      formScore: formScore,
      goalsForAvg: totalGoalsFor / n,
      goalsAgainstAvg: totalGoalsAgainst / n,
      winRate: winRate,
      matches: recentMatches.length,
      wins: wins,
      draws: draws,
      losses: losses,
      warning: null
    };
  }

  function parseTacticalIntel(intelText) {
    if (!intelText) return { factors: [], overallImpact: 0 };

    var factors = [];
    var impact = 0;

    if (intelText.indexOf('控球') !== -1 || intelText.indexOf('possession') !== -1) {
      factors.push({ type: 'tactical', name: '控球战术', impact: 0.1, description: '球队擅长控球打法' });
      impact += 0.1;
    }
    if (intelText.indexOf('高压') !== -1 || intelText.indexOf('press') !== -1) {
      factors.push({ type: 'tactical', name: '高压逼抢', impact: 0.15, description: '球队实施高压战术' });
      impact += 0.15;
    }
    if (intelText.indexOf('反击') !== -1 || intelText.indexOf('counter') !== -1) {
      factors.push({ type: 'tactical', name: '防守反击', impact: 0.12, description: '球队擅长防守反击' });
      impact += 0.12;
    }
    if (intelText.indexOf('伤病') !== -1 || intelText.indexOf('injured') !== -1) {
      factors.push({ type: 'injury', name: '伤病影响', impact: -0.15, description: '关键球员受伤' });
      impact -= 0.15;
    }
    if (intelText.indexOf('停赛') !== -1 || intelText.indexOf('suspended') !== -1) {
      factors.push({ type: 'injury', name: '停赛影响', impact: -0.12, description: '关键球员停赛' });
      impact -= 0.12;
    }
    if (intelText.indexOf('状态') !== -1) {
      if (intelText.indexOf('好') !== -1 || intelText.indexOf('good') !== -1) {
        factors.push({ type: 'form', name: '状态良好', impact: 0.1, description: '球队近期状态良好' });
        impact += 0.1;
      } else if (intelText.indexOf('差') !== -1 || intelText.indexOf('poor') !== -1) {
        factors.push({ type: 'form', name: '状态低迷', impact: -0.1, description: '球队近期状态低迷' });
        impact -= 0.1;
      }
    }

    return { factors: factors, overallImpact: parseFloat(impact.toFixed(3)) };
  }

  function parseHistoricalH2H(teamAKey, teamBKey, h2hData) {
    if (!h2hData || h2hData.length === 0) return { h2hFactor: 0, winRateA: null, winRateB: null, lastWinner: null, warning: '无交锋记录' };

    var winsA = 0, winsB = 0, draws = 0;
    var lastWinner = null;

    for (var i = 0; i < h2hData.length; i++) {
      var match = h2hData[i];
      if (match.result === 'A') winsA++;
      else if (match.result === 'B') winsB++;
      else draws++;
      if (i === h2hData.length - 1) lastWinner = match.result;
    }

    var total = h2hData.length;
    var winRateA = winsA / total;
    var winRateB = winsB / total;

    var h2hFactor = (winRateA - winRateB) * 0.15;

    return {
      h2hFactor: h2hFactor,
      winRateA: winRateA,
      winRateB: winRateB,
      lastWinner: lastWinner,
      matches: total,
      winsA: winsA,
      winsB: winsB,
      draws: draws,
      warning: null
    };
  }

  function parsePlayerStats(teamKey, playerData) {
    if (!playerData) return { attackBoost: 0, defenceBoost: 0, overallBoost: 0, warning: '无球员数据' };

    var attackBoost = 0, defenceBoost = 0;
    var keyPlayers = playerData.keyPlayers || [];

    for (var i = 0; i < keyPlayers.length; i++) {
      var p = keyPlayers[i];
      if (p.position === 'forward' || p.position === 'midfielder') {
        attackBoost += p.status === 'fit' ? p.rating * 0.001 : -0.02;
      } else if (p.position === 'defender' || p.position === 'goalkeeper') {
        defenceBoost += p.status === 'fit' ? p.rating * 0.001 : -0.02;
      }
    }

    return {
      attackBoost: parseFloat(attackBoost.toFixed(3)),
      defenceBoost: parseFloat(defenceBoost.toFixed(3)),
      overallBoost: parseFloat((attackBoost + defenceBoost).toFixed(3)),
      keyPlayers: keyPlayers.length,
      warning: null
    };
  }

  function parseWeather(weatherData) {
    if (!weatherData) return { impact: 0, factors: [] };

    var factors = [];
    var impact = 0;

    if (weatherData.rain && weatherData.rain > 0.5) {
      factors.push({ name: '降雨', impact: -0.1, description: '降雨影响传球和射门' });
      impact -= 0.1;
    }
    if (weatherData.wind && weatherData.wind > 0.6) {
      factors.push({ name: '大风', impact: -0.08, description: '大风影响长传和射门' });
      impact -= 0.08;
    }
    if (weatherData.temperature && (weatherData.temperature < 5 || weatherData.temperature > 35)) {
      factors.push({ name: '极端温度', impact: -0.05, description: '极端温度影响球员体能' });
      impact -= 0.05;
    }

    return { impact: parseFloat(impact.toFixed(3)), factors: factors };
  }

  function injectOddsIntoModel(predictions, odds) {
    if (!odds) return { prediction: predictions, calibrated: false, msg: '无赔率数据' };

    var winOdds = odds.win || 2.0;
    var drawOdds = odds.draw || 3.0;
    var loseOdds = odds.lose || 2.0;

    var impliedWin = 1 / winOdds;
    var impliedDraw = 1 / drawOdds;
    var impliedLose = 1 / loseOdds;
    var total = impliedWin + impliedDraw + impliedLose;

    var normalizedWin = impliedWin / total;
    var normalizedDraw = impliedDraw / total;
    var normalizedLose = impliedLose / total;

    var mergedWin = predictions.winA * 0.7 + normalizedWin * 0.3;
    var mergedDraw = predictions.draw * 0.7 + normalizedDraw * 0.3;
    var mergedLose = predictions.winB * 0.7 + normalizedLose * 0.3;

    var mergeTotal = mergedWin + mergedDraw + mergedLose;

    return {
      prediction: {
        winA: mergedWin / mergeTotal,
        draw: mergedDraw / mergeTotal,
        winB: mergedLose / mergeTotal
      },
      calibrated: true,
      msg: '已融合市场赔率',
      marketImplied: { win: normalizedWin, draw: normalizedDraw, lose: normalizedLose },
      overround: total
    };
  }

  function getLiveStatsSummary(teamKey) {
    var stats = FiveLeaguesEngine_LIVE_STATS;
    return {
      goalsScored: stats.goalsScored[teamKey] || 0,
      goalsConceded: stats.goalsConceded[teamKey] || 0,
      shots: stats.shots[teamKey] || 0,
      shotsOnTarget: stats.shotsOnTarget[teamKey] || 0,
      corners: stats.corners[teamKey] || 0,
      tackles: stats.tackles[teamKey] || 0,
      saves: stats.saves[teamKey] || 0
    };
  }

  function calculateLiveStatsFactor(teamKey) {
    var summary = getLiveStatsSummary(teamKey);
    var team = TEAMS[teamKey];
    if (!team) return 1.0;

    var attackFactor = 1.0;
    if (summary.shotsOnTarget > 0) {
      attackFactor = summary.goalsScored / summary.shotsOnTarget;
    }

    var defenceFactor = 1.0;
    if (summary.shotsOnTarget > 0) {
      defenceFactor = summary.saves / summary.shotsOnTarget;
    }

    return {
      attackFactor: Math.max(0.5, Math.min(2.0, attackFactor)),
      defenceFactor: Math.max(0.5, Math.min(2.0, defenceFactor)),
      efficiencyFactor: Math.max(0.3, Math.min(1.5, attackFactor))
    };
  }

  function updateTeamAttributesFromLiveStats(teamKey, liveStats) {
    var team = TEAMS[teamKey];
    if (!team) return;

    FiveLeaguesEngine_LIVE_STATS.goalsScored[teamKey] = (FiveLeaguesEngine_LIVE_STATS.goalsScored[teamKey] || 0) + (liveStats.goalsScored || 0);
    FiveLeaguesEngine_LIVE_STATS.goalsConceded[teamKey] = (FiveLeaguesEngine_LIVE_STATS.goalsConceded[teamKey] || 0) + (liveStats.goalsConceded || 0);
    FiveLeaguesEngine_LIVE_STATS.shots[teamKey] = (FiveLeaguesEngine_LIVE_STATS.shots[teamKey] || 0) + (liveStats.shots || 0);
    FiveLeaguesEngine_LIVE_STATS.shotsOnTarget[teamKey] = (FiveLeaguesEngine_LIVE_STATS.shotsOnTarget[teamKey] || 0) + (liveStats.shotsOnTarget || 0);
    FiveLeaguesEngine_LIVE_STATS.corners[teamKey] = (FiveLeaguesEngine_LIVE_STATS.corners[teamKey] || 0) + (liveStats.corners || 0);
    FiveLeaguesEngine_LIVE_STATS.tackles[teamKey] = (FiveLeaguesEngine_LIVE_STATS.tackles[teamKey] || 0) + (liveStats.tackles || 0);
    FiveLeaguesEngine_LIVE_STATS.saves[teamKey] = (FiveLeaguesEngine_LIVE_STATS.saves[teamKey] || 0) + (liveStats.saves || 0);

    var factor = calculateLiveStatsFactor(teamKey);
    team.attack *= factor.efficiencyFactor;
    team.defence *= factor.defenceFactor;
  }

  function updateAllTeamsFromLiveStats(matchStats) {
    if (!matchStats || !matchStats.teamA || !matchStats.teamB) return;
    updateTeamAttributesFromLiveStats(matchStats.teamA, matchStats.statsA);
    updateTeamAttributesFromLiveStats(matchStats.teamB, matchStats.statsB);
  }

  var PLAYER_POSTMATCH_RATINGS = {};

  function calculatePlayerImpact(playerRating, minutesPlayed, position) {
    if (!playerRating || minutesPlayed < 10) return 0;
    var positionMultiplier = { forward: 1.5, midfielder: 1.2, defender: 1.0, goalkeeper: 0.8 };
    return playerRating * (minutesPlayed / 90) * (positionMultiplier[position] || 1.0);
  }

  function analyzeTeamAttackSide(teamKey) {
    var team = TEAMS[teamKey];
    if (!team || !team.attackSide) return { adjA: 0, adjB: 0 };

    var homeSide = team.attackSide;
    var leftAttack = homeSide.left || 0.33;
    var centerAttack = homeSide.center || 0.34;
    var rightAttack = homeSide.right || 0.33;

    return { adjA: adjA, adjB: adjB };
  }

  var MATCHES = [];
  var PREDICTION_LOG = [];
  var COMPLETED_MATCH_ARCHIVE = [];

  var WEATHER = {};

  function detectOddsModelConflict(prediction, odds) {
    if (!odds) return { hasConflict: false, level: 'none', msg: '无赔率数据' };

    var predWin = prediction.winA;
    var predDraw = prediction.draw;
    var predLose = prediction.winB;

    var impliedWin = 1 / odds.win;
    var impliedDraw = 1 / odds.draw;
    var impliedLose = 1 / odds.lose;
    var overround = impliedWin + impliedDraw + impliedLose;
    impliedWin /= overround;
    impliedDraw /= overround;
    impliedLose /= overround;

    var winDiff = Math.abs(predWin - impliedWin);
    var drawDiff = Math.abs(predDraw - impliedDraw);
    var loseDiff = Math.abs(predLose - impliedLose);

    var maxDiff = Math.max(winDiff, drawDiff, loseDiff);

    var predMax = Math.max(predWin, predDraw, predLose);
    var impMax = Math.max(impliedWin, impliedDraw, impliedLose);

    var predResult = predWin === predMax ? 'winA' : (predDraw === predMax ? 'draw' : 'winB');
    var impResult = impliedWin === impMax ? 'winA' : (impliedDraw === impMax ? 'draw' : 'winB');

    var directionConflict = predResult !== impResult;

    if (maxDiff > 0.15 && directionConflict) {
      return {
        hasConflict: true,
        level: 'high',
        msg: '模型与赔率方向相反且差异显著',
        details: { winDiff: winDiff, drawDiff: drawDiff, loseDiff: loseDiff, predResult: predResult, impResult: impResult }
      };
    } else if (maxDiff > 0.10) {
      return {
        hasConflict: true,
        level: 'medium',
        msg: '模型与赔率存在显著差异',
        details: { winDiff: winDiff, drawDiff: drawDiff, loseDiff: loseDiff }
      };
    } else if (maxDiff > 0.05) {
      return {
        hasConflict: true,
        level: 'low',
        msg: '模型与赔率存在轻微差异',
        details: { winDiff: winDiff, drawDiff: drawDiff, loseDiff: loseDiff }
      };
    }

    return { hasConflict: false, level: 'none', msg: '模型与赔率方向一致' };
  }

  function analyzeOddsTrajectory5D(trajectory) {
    var eu = trajectory.european || [];
    var hc = trajectory.handicap || [];
    var cs = trajectory.correctScore || [];
    var tg = trajectory.totalGoals || [];
    var htft = trajectory.halfTimeFullTime || [];

    if (eu.length < 2) return { error: '赔率数据不足(需至少2个时间节点)' };

    function impliedProbs(win, draw, lose) {
      var raw = { w: 1 / win, d: 1 / draw, l: 1 / lose };
      var total = raw.w + raw.d + raw.l;
      return { w: raw.w / total, d: raw.d / total, l: raw.l / total, overround: total };
    }
    function driftLabel(val) {
      if (val > 0.005) return '↑升';
      if (val < -0.005) return '↓降';
      return '→平';
    }
    function oddsDriftLabel(oddsOpen, oddsClose) {
      var diff = oddsClose - oddsOpen;
      if (diff > 0.05) return '↑升赔';
      if (diff < -0.05) return '↓降赔';
      return '→平';
    }
    function findOutcome(arr, key, keyField) {
      for (var i = 0; i < arr.length; i++) {
        if (arr[i][keyField || 'score'] === key) return arr[i];
      }
      return null;
    }

    var euFirst = eu[0], euLast = eu[eu.length - 1];
    var euFirstP = impliedProbs(euFirst.win, euFirst.draw, euFirst.lose);
    var euLastP = impliedProbs(euLast.win, euLast.draw, euLast.lose);
    var euDrift = { win: euLastP.w - euFirstP.w, draw: euLastP.d - euFirstP.d, lose: euLastP.l - euFirstP.l };

    var hcDrift = { win: 0, draw: 0, lose: 0 };
    var hcFirstP = null, hcLastP = null;
    if (hc.length >= 2) {
      hcFirstP = impliedProbs(hc[0].win, hc[0].draw, hc[0].lose);
      hcLastP = impliedProbs(hc[hc.length - 1].win, hc[hc.length - 1].draw, hc[hc.length - 1].lose);
      hcDrift.win = hcLastP.w - hcFirstP.w;
      hcDrift.draw = hcLastP.d - hcFirstP.d;
      hcDrift.lose = hcLastP.l - hcFirstP.l;
    }

    var scoreDrift = {};
    if (cs.length >= 2) {
      var csF = cs[0], csL = cs[cs.length - 1];
      for (var c = 0; c < csF.outcomes.length; c++) {
        var sc = csF.outcomes[c].score;
        var cL = findOutcome(csL.outcomes, sc, 'score');
        if (cL) {
          scoreDrift[sc] = {
            openOdds: csF.outcomes[c].odds,
            closeOdds: cL.odds,
            drift: cL.odds - csF.outcomes[c].odds,
            impliedDrift: (1 / cL.odds - 1 / csF.outcomes[c].odds) * 100,
            label: oddsDriftLabel(csF.outcomes[c].odds, cL.odds)
          };
        }
      }
    }

    var goalsDrift = {};
    if (tg.length >= 2) {
      var tgF = tg[0], tgL = tg[tg.length - 1];
      for (var g = 0; g < tgF.goals.length; g++) {
        var gn = tgF.goals[g].n;
        var gL = findOutcome(tgL.goals, gn, 'n');
        if (gL) {
          goalsDrift[gn + '球'] = {
            openOdds: tgF.goals[g].odds,
            closeOdds: gL.odds,
            drift: gL.odds - tgF.goals[g].odds,
            impliedDrift: (1 / gL.odds - 1 / tgF.goals[g].odds) * 100,
            label: oddsDriftLabel(tgF.goals[g].odds, gL.odds)
          };
        }
      }
    }

    var htftDrift = {};
    if (htft.length >= 2) {
      var hfF = htft[0], hfL = htft[htft.length - 1];
      for (var h = 0; h < hfF.outcomes.length; h++) {
        var combo = hfF.outcomes[h].combo;
        var hL = findOutcome(hfL.outcomes, combo, 'combo');
        if (hL) {
          htftDrift[combo] = {
            openOdds: hfF.outcomes[h].odds,
            closeOdds: hL.odds,
            drift: hL.odds - hfF.outcomes[h].odds,
            impliedDrift: (1 / hL.odds - 1 / hfF.outcomes[h].odds) * 100,
            label: oddsDriftLabel(hfF.outcomes[h].odds, hL.odds)
          };
        }
      }
    }

    var driftSummary = {
      european: {
        win: driftLabel(euDrift.win),
        draw: driftLabel(euDrift.draw),
        lose: driftLabel(euDrift.lose),
        detail: { win: (euDrift.win * 100).toFixed(1) + '%', draw: (euDrift.draw * 100).toFixed(1) + '%', lose: (euDrift.lose * 100).toFixed(1) + '%' }
      },
      handicap: {
        win: driftLabel(hcDrift.win),
        draw: driftLabel(hcDrift.draw),
        lose: driftLabel(hcDrift.lose),
        detail: { win: (hcDrift.win * 100).toFixed(1) + '%', draw: (hcDrift.draw * 100).toFixed(1) + '%', lose: (hcDrift.lose * 100).toFixed(1) + '%' }
      },
      keyScores: {},
      keyGoals: {},
      keyHtft: {}
    };

    var scoreEntries = Object.keys(scoreDrift).map(function(k) { return { key: k, v: scoreDrift[k] }; });
    scoreEntries.sort(function(a, b) { return Math.abs(b.v.impliedDrift) - Math.abs(a.v.impliedDrift); });
    for (var si = 0; si < Math.min(5, scoreEntries.length); si++) {
      driftSummary.keyScores[scoreEntries[si].key] = scoreEntries[si].v.label + '(' + scoreEntries[si].v.impliedDrift.toFixed(1) + '%)';
    }

    var goalsEntries = Object.keys(goalsDrift).map(function(k) { return { key: k, v: goalsDrift[k] }; });
    goalsEntries.sort(function(a, b) { return Math.abs(b.v.impliedDrift) - Math.abs(a.v.impliedDrift); });
    for (var gi = 0; gi < Math.min(5, goalsEntries.length); gi++) {
      driftSummary.keyGoals[goalsEntries[gi].key] = goalsEntries[gi].v.label + '(' + goalsEntries[gi].v.impliedDrift.toFixed(1) + '%)';
    }

    var htftEntries = Object.keys(htftDrift).map(function(k) { return { key: k, v: htftDrift[k] }; });
    htftEntries.sort(function(a, b) { return Math.abs(b.v.impliedDrift) - Math.abs(a.v.impliedDrift); });
    for (var hi = 0; hi < Math.min(5, htftEntries.length); hi++) {
      driftSummary.keyHtft[htftEntries[hi].key] = htftEntries[hi].v.label + '(' + htftEntries[hi].v.impliedDrift.toFixed(1) + '%)';
    }

    var singleSignals = [];

    if (euDrift.win < -0.03) singleSignals.push({ dim: '胜平负', msg: '主胜隐含概率大幅下降(' + (euDrift.win * 100).toFixed(1) + '%), 机构降低主胜预期', dir: 'down', strength: 'strong' });
    else if (euDrift.win < -0.015) singleSignals.push({ dim: '胜平负', msg: '主胜隐含概率下降(' + (euDrift.win * 100).toFixed(1) + '%)', dir: 'down', strength: 'medium' });
    else if (euDrift.win > 0.03) singleSignals.push({ dim: '胜平负', msg: '主胜隐含概率大幅上升(' + (euDrift.win * 100).toFixed(1) + '%), 机构看好主队', dir: 'up', strength: 'strong' });
    else if (euDrift.win > 0.015) singleSignals.push({ dim: '胜平负', msg: '主胜隐含概率上升(' + (euDrift.win * 100).toFixed(1) + '%)', dir: 'up', strength: 'medium' });

    if (euDrift.draw > 0.02) singleSignals.push({ dim: '胜平负', msg: '平局隐含概率上升(' + (euDrift.draw * 100).toFixed(1) + '%), 市场看好平局', dir: 'up', strength: 'medium' });
    if (euDrift.lose > 0.03) singleSignals.push({ dim: '胜平负', msg: '客胜隐含概率大幅上升(' + (euDrift.lose * 100).toFixed(1) + '%), 冷门升温', dir: 'up', strength: 'strong' });
    else if (euDrift.lose > 0.015) singleSignals.push({ dim: '胜平负', msg: '客胜隐含概率上升(' + (euDrift.lose * 100).toFixed(1) + '%)', dir: 'up', strength: 'medium' });

    if (hcDrift.lose > 0.03) singleSignals.push({ dim: '让球', msg: '让球客胜概率上升(' + (hcDrift.lose * 100).toFixed(1) + '%), 让球方信心减弱', dir: 'up', strength: 'strong' });
    if (hcDrift.win > 0.03) singleSignals.push({ dim: '让球', msg: '让球主胜概率上升(' + (hcDrift.win * 100).toFixed(1) + '%), 让球方看好主队', dir: 'up', strength: 'medium' });

    if (scoreDrift['1:1'] && scoreDrift['1:1'].impliedDrift > 0.5) singleSignals.push({ dim: '比分', msg: '1-1赔率走低(隐含概率+' + scoreDrift['1:1'].impliedDrift.toFixed(1) + '%), 平局比分预期升温', dir: 'down', strength: 'medium' });
    if (scoreDrift['0:0'] && scoreDrift['0:0'].impliedDrift > 0.5) singleSignals.push({ dim: '比分', msg: '0-0赔率走低, 零进球预期升温', dir: 'down', strength: 'medium' });
    if (scoreDrift['2:0'] && scoreDrift['2:0'].impliedDrift > 0.5) singleSignals.push({ dim: '比分', msg: '2-0赔率走低, 主队小胜预期升温', dir: 'down', strength: 'medium' });
    if (scoreDrift['0:1'] && scoreDrift['0:1'].impliedDrift > 0.5) singleSignals.push({ dim: '比分', msg: '0-1赔率走低, 客队小胜预期升温', dir: 'down', strength: 'medium' });

    if (goalsDrift['0球'] && goalsDrift['0球'].impliedDrift > 0.5) singleSignals.push({ dim: '进球数', msg: '0球赔率走低, 零进球预期升温', dir: 'down', strength: 'medium' });
    if (goalsDrift['1球'] && goalsDrift['1球'].impliedDrift > 0.5) singleSignals.push({ dim: '进球数', msg: '1球赔率走低, 低进球预期', dir: 'down', strength: 'medium' });
    if (goalsDrift['3球'] && goalsDrift['3球'].impliedDrift > 0.5) singleSignals.push({ dim: '进球数', msg: '3球赔率走低, 进球数预期偏高', dir: 'down', strength: 'medium' });
    if (goalsDrift['4球'] && goalsDrift['4球'].impliedDrift > 0.5) singleSignals.push({ dim: '进球数', msg: '4球赔率走低, 大比分预期升温', dir: 'down', strength: 'medium' });

    if (htftDrift['平平'] && htftDrift['平平'].impliedDrift > 0.5) singleSignals.push({ dim: '半全场', msg: '平平赔率走低, 全场平局预期升温', dir: 'down', strength: 'medium' });

    var crossSignals = [];
    if (euDrift.win > 0.02 && hcDrift.win > 0.02) crossSignals.push({ dim: '交叉验证', msg: '胜平负与让球同步看好主胜', strength: 'strong', confidence: 0.8 });
    if (euDrift.draw > 0.02 && scoreDrift['1:1'] && scoreDrift['1:1'].impliedDrift > 0.3) crossSignals.push({ dim: '交叉验证', msg: '胜平负与比分同步看好平局', strength: 'strong', confidence: 0.75 });
    if (euDrift.lose > 0.03 && goalsDrift['0球'] && goalsDrift['0球'].drift < -0.3) crossSignals.push({ dim: '交叉验证', msg: '客胜升温但进球预期下降, 警惕大比分客胜', strength: 'medium', confidence: 0.6 });

    var allSignals = singleSignals.concat(crossSignals);

    var steamMoves = [];
    if (euDrift.win < -0.05) steamMoves.push({ direction: '主胜', magnitude: Math.abs(euDrift.win), timestamp: euLast.timestamp || new Date().toISOString() });
    if (euDrift.lose < -0.05) steamMoves.push({ direction: '客胜', magnitude: Math.abs(euDrift.lose), timestamp: euLast.timestamp || new Date().toISOString() });
    if (euDrift.draw < -0.05) steamMoves.push({ direction: '平局', magnitude: Math.abs(euDrift.draw), timestamp: euLast.timestamp || new Date().toISOString() });

    var fusion = {
      winA: euDrift.win * 0.3 + (hcDrift.win || 0) * 0.2,
      draw: euDrift.draw * 0.3 + (hcDrift.draw || 0) * 0.2,
      winB: euDrift.lose * 0.3 + (hcDrift.lose || 0) * 0.2,
      confidence: Math.min(1.0, allSignals.length * 0.15),
      driftMagnitude: Math.abs(euDrift.win) + Math.abs(euDrift.draw) + Math.abs(euDrift.lose)
    };

    return {
      european: {
        open: { odds: euFirst, probs: euFirstP },
        close: { odds: euLast, probs: euLastP },
        drift: euDrift
      },
      handicap: { open: hcFirstP, close: hcLastP, drift: hcDrift },
      correctScore: scoreDrift,
      totalGoals: goalsDrift,
      halfTimeFullTime: htftDrift,
      driftSummary: driftSummary,
      singleSignals: singleSignals,
      crossSignals: crossSignals,
      allSignals: allSignals,
      steamMoves: steamMoves,
      fusion: fusion
    };
  }

  function analyzeBookmakerBehavior(trajectory, marketContext) {
    var eu = trajectory.european || [];
    if (eu.length < 2) return { error: '赔率数据不足' };

    var first = eu[0], last = eu[eu.length - 1];
    var firstP = { w: 1 / first.win, d: 1 / first.draw, l: 1 / first.lose };
    var lastP = { w: 1 / last.win, d: 1 / last.draw, l: 1 / last.lose };
    var firstOverround = firstP.w + firstP.d + firstP.l;
    var lastOverround = lastP.w + lastP.d + lastP.l;

    var behavior = {
      manipulation: {
        hardResistance: false,
        softResistance: false,
        aggressivePush: false,
        trapPattern: false
      },
      riskManagement: {
        overroundChange: lastOverround - firstOverround,
        initialOverround: firstOverround,
        finalOverround: lastOverround,
        isProtective: false,
        isAggressive: false
      },
      marketSentiment: {
        moneyFlow: { win: lastP.w - firstP.w, draw: lastP.d - firstP.d, lose: lastP.l - firstP.l },
        mostBet: null,
        leastBet: null,
        volatility: 0
      },
      confidence: 0
    };

    var winDrift = lastP.w - firstP.w;
    var loseDrift = lastP.l - firstP.l;

    if (Math.abs(winDrift) < 0.01 && first.win > 2.0) {
      behavior.manipulation.hardResistance = true;
    }

    if (Math.abs(winDrift) > 0.005 && Math.abs(winDrift) < 0.02) {
      behavior.manipulation.softResistance = true;
    }

    if (loseDrift > 0.03 && last.lose < first.lose) {
      behavior.manipulation.aggressivePush = true;
    }

    if (lastOverround > firstOverround + 0.05) {
      behavior.riskManagement.isProtective = true;
    } else if (lastOverround < firstOverround - 0.03) {
      behavior.riskManagement.isAggressive = true;
    }

    var maxFlow = Math.max(Math.abs(behavior.marketSentiment.moneyFlow.win),
                           Math.abs(behavior.marketSentiment.moneyFlow.draw),
                           Math.abs(behavior.marketSentiment.moneyFlow.lose));
    if (Math.abs(behavior.marketSentiment.moneyFlow.win) === maxFlow) {
      behavior.marketSentiment.mostBet = behavior.marketSentiment.moneyFlow.win > 0 ? '主胜' : '主胜(流出)';
    } else if (Math.abs(behavior.marketSentiment.moneyFlow.draw) === maxFlow) {
      behavior.marketSentiment.mostBet = behavior.marketSentiment.moneyFlow.draw > 0 ? '平局' : '平局(流出)';
    } else {
      behavior.marketSentiment.mostBet = behavior.marketSentiment.moneyFlow.lose > 0 ? '客胜' : '客胜(流出)';
    }

    var totalDrift = Math.abs(winDrift) + Math.abs(lastP.d - firstP.d) + Math.abs(loseDrift);
    behavior.marketSentiment.volatility = totalDrift;

    var signalCount = 0;
    if (behavior.manipulation.hardResistance) signalCount++;
    if (behavior.manipulation.softResistance) signalCount++;
    if (behavior.manipulation.aggressivePush) signalCount++;
    if (behavior.riskManagement.isProtective || behavior.riskManagement.isAggressive) signalCount++;
    behavior.confidence = Math.min(1.0, signalCount * 0.25 + totalDrift * 0.5);

    return behavior;
  }

  function detectOddsManipulation(trajectory, teamContext) {
    var eu = trajectory.european || [];
    if (eu.length < 2) return { error: '赔率数据不足' };

    var first = eu[0], last = eu[eu.length - 1];
    var firstP = { w: 1 / first.win, d: 1 / first.draw, l: 1 / first.lose };
    var lastP = { w: 1 / last.win, d: 1 / last.draw, l: 1 / last.lose };

    var manipulation = {
      type: 'none',
      signals: [],
      confidence: 0,
      recommendation: null,
      riskLevel: 'low'
    };

    var isHomeFavorite = first.win < first.lose;
    var homeOddsRise = last.win > first.win;

    if (isHomeFavorite && homeOddsRise && (last.win - first.win) > 0.15) {
      manipulation.type = 'block';
      manipulation.signals.push({
        code: 'BLOCK_HOME',
        desc: '主队热门但赔率上升 → 庄家阻盘, 不看好主队',
        strength: 'strong'
      });
    }

    if (isHomeFavorite && (first.win - last.win) > 0.1 && lastP.w > firstP.w + 0.05) {
      manipulation.type = 'trap';
      manipulation.signals.push({
        code: 'TRAP_HOME',
        desc: '主队热门且赔率下降 → 庄家诱盘, 吸引资金',
        strength: 'strong'
      });
    }

    var isAwayUnderdog = first.lose > first.win;
    var awayOddsDrop = last.lose < first.lose;

    if (isAwayUnderdog && awayOddsDrop && (first.lose - last.lose) > 0.2) {
      manipulation.type = 'reverse_trap';
      manipulation.signals.push({
        code: 'REVERSE_TRAP',
        desc: '客队冷门但赔率大幅下降 → 反向诱盘, 可能出冷门',
        strength: 'strong'
      });
    }

    if (Math.abs(first.win - 2.0) < 0.1 && Math.abs(last.win - 2.0) < 0.1) {
      manipulation.signals.push({
        code: 'PRICE_ANCHOR',
        desc: '主胜赔率锚定在2.0附近 → 价格锚定效应, 诱导均衡投注',
        strength: 'medium'
      });
    }

    var winDrift = lastP.w - firstP.w;
    var loseDrift = lastP.l - firstP.l;
    if (winDrift > 0.02 && loseDrift > 0.02) {
      manipulation.signals.push({
        code: 'CONFLICT_SIGNAL',
        desc: '主胜和客胜概率同时上升 → 矛盾信号, 平局可能性增加',
        strength: 'strong'
      });
      manipulation.riskLevel = 'high';
    }

    manipulation.confidence = Math.min(1.0, manipulation.signals.length * 0.3);

    if (manipulation.type === 'block') {
      manipulation.recommendation = '避开热门方, 考虑平局或冷门';
    } else if (manipulation.type === 'trap') {
      manipulation.recommendation = '谨慎投注热门方, 可能是诱盘';
    } else if (manipulation.type === 'reverse_trap') {
      manipulation.recommendation = '关注冷门方, 可能有机会';
    }

    return manipulation;
  }

  function advancedFeatureEngineering(trajectory, matchContext) {
    var eu = trajectory.european || [];
    if (eu.length < 2) return { error: '赔率数据不足' };

    var first = eu[0], last = eu[eu.length - 1];
    var firstP = { w: 1 / first.win, d: 1 / first.draw, l: 1 / first.lose };
    var lastP = { w: 1 / last.win, d: 1 / last.draw, l: 1 / last.lose };

    var features = {
      market: {
        moneyHeatIndex: 0,
        bookmakerConfidence: 0,
        anomalyScore: 0,
        consensusConcentration: 0,
        timeDecayFactor: 1
      },
      momentum: {
        winMomentum: 0,
        drawMomentum: 0,
        loseMomentum: 0,
        goalMomentum: 0
      },
      pattern: {
        isSteamMove: false,
        isSharpMoney: false,
        isLateMoney: false,
        patternStrength: 0
      },
      timing: {
        criticalTimeWindow: null,
        lastMinuteAction: false
      }
    };

    var maxProbChange = Math.max(Math.abs(lastP.w - firstP.w),
                                 Math.abs(lastP.d - firstP.d),
                                 Math.abs(lastP.l - firstP.l));
    features.market.moneyHeatIndex = Math.min(1.0, maxProbChange * 10);

    var firstOverround = firstP.w + firstP.d + firstP.l;
    var lastOverround = lastP.w + lastP.d + lastP.l;
    features.market.bookmakerConfidence = lastOverround < firstOverround ? 0.7 + (firstOverround - lastOverround) * 2 : 0.5;

    var volatility = 0;
    for (var i = 1; i < eu.length; i++) {
      var prev = { w: 1 / eu[i-1].win, d: 1 / eu[i-1].draw, l: 1 / eu[i-1].lose };
      var curr = { w: 1 / eu[i].win, d: 1 / eu[i].draw, l: 1 / eu[i].lose };
      volatility += Math.abs(curr.w - prev.w) + Math.abs(curr.d - prev.d) + Math.abs(curr.l - prev.l);
    }
    features.market.anomalyScore = Math.min(1.0, volatility);
    features.market.consensusConcentration = 0.6;

    var hoursToKickoff = matchContext ? matchContext.hoursToKickoff || 24 : 24;
    features.market.timeDecayFactor = hoursToKickoff < 6 ? 0.3 : hoursToKickoff < 12 ? 0.6 : 1.0;

    features.momentum.winMomentum = lastP.w - firstP.w;
    features.momentum.drawMomentum = lastP.d - firstP.d;
    features.momentum.loseMomentum = lastP.l - firstP.l;

    features.pattern.isSteamMove = maxProbChange > 0.05;

    if (eu.length >= 3) {
      var lateStart = eu.length - Math.ceil(eu.length * 0.2);
      var lateFirst = eu[lateStart];
      var latePFirst = { w: 1 / lateFirst.win, d: 1 / lateFirst.draw, l: 1 / lateFirst.lose };
      var lateChange = Math.abs(lastP.w - latePFirst.w) + Math.abs(lastP.d - latePFirst.d) + Math.abs(lastP.l - latePFirst.l);
      features.pattern.isLateMoney = lateChange > 0.03;
    }

    if (features.pattern.isLateMoney || features.market.anomalyScore > 0.5) {
      features.timing.criticalTimeWindow = 'late';
      features.timing.lastMinuteAction = true;
    }

    return features;
  }

  function dynamicOddsFusion(modelResult, oddsAnalysis, features) {
    if (!oddsAnalysis || !oddsAnalysis.fusion) return modelResult;

    var signalQuality = features ? features.market.bookmakerConfidence : 0.5;
    var volatility = features ? features.market.anomalyScore : 0;

    var baseWeight = 0.35;
    var dynamicWeight = baseWeight + signalQuality * 0.3;

    if (volatility > 0.6) {
      dynamicWeight *= 0.7;
    }

    var timeFactor = features ? features.market.timeDecayFactor : 1;
    dynamicWeight *= (0.7 + timeFactor * 0.3);

    dynamicWeight = Math.max(0.2, Math.min(0.6, dynamicWeight));

    var fused = {
      winA: modelResult.winA * (1 - dynamicWeight) + (modelResult.winA + oddsAnalysis.fusion.winA) * dynamicWeight,
      draw: modelResult.draw * (1 - dynamicWeight) + (modelResult.draw + oddsAnalysis.fusion.draw) * dynamicWeight,
      winB: modelResult.winB * (1 - dynamicWeight) + (modelResult.winB + oddsAnalysis.fusion.winB) * dynamicWeight
    };

    var total = fused.winA + fused.draw + fused.winB;
    fused.winA /= total;
    fused.draw /= total;
    fused.winB /= total;

    fused.original = { ...modelResult };
    fused.oddsWeight = dynamicWeight;
    fused.confidence = Math.min(1.0, (modelResult.confidence || 0.7) + signalQuality * 0.2);

    return fused;
  }

  var OddsStreamProcessor = {
    subscriptions: {},
    updateCallbacks: [],

    subscribe: function(matchId, callback) {
      if (!this.subscriptions[matchId]) {
        this.subscriptions[matchId] = [];
      }
      this.subscriptions[matchId].push(callback);
      return {
        unsubscribe: function() {
          var idx = OddsStreamProcessor.subscriptions[matchId].indexOf(callback);
          if (idx >= 0) {
            OddsStreamProcessor.subscriptions[matchId].splice(idx, 1);
          }
        }
      };
    },

    processUpdate: function(matchId, update) {
      var callbacks = this.subscriptions[matchId] || [];
      callbacks.forEach(function(cb) {
        try {
          cb(update);
        } catch (e) {
          console.error('Stream callback error:', e);
        }
      });

      this.updateCallbacks.forEach(function(cb) {
        try {
          cb(matchId, update);
        } catch (e) {
          console.error('Global callback error:', e);
        }
      });
    },

    registerGlobalCallback: function(callback) {
      this.updateCallbacks.push(callback);
      return {
        unregister: function() {
          var idx = OddsStreamProcessor.updateCallbacks.indexOf(callback);
          if (idx >= 0) {
            OddsStreamProcessor.updateCallbacks.splice(idx, 1);
          }
        }
      };
    },

    simulateUpdate: function(matchId, trajectoryUpdate) {
      this.processUpdate(matchId, {
        type: 'odds_update',
        timestamp: Date.now(),
        data: trajectoryUpdate
      });
    }
  };

  function evaluateOddsAnalysis(review, oddsAnalysis) {
    if (!review || !oddsAnalysis) return null;

    var evaluation = {
      accuracy: {
        winDrawWin: null,
        correctScore: null,
        totalGoals: null
      },
      brierScore: null,
      logLoss: null,
      signalQuality: {
        detectedSignals: oddsAnalysis.allSignals ? oddsAnalysis.allSignals.length : 0,
        correctSignals: 0,
        signalAccuracy: 0
      },
      recommendations: []
    };

    var actual = { winA: 0, draw: 0, winB: 0 };
    if (review.actual) {
      if (review.actual.goalsA > review.actual.goalsB) actual.winA = 1;
      else if (review.actual.goalsA < review.actual.goalsB) actual.winB = 1;
      else actual.draw = 1;
    }

    var finalProbs = oddsAnalysis.fusion ? {
      winA: 0.5 + oddsAnalysis.fusion.winA * 0.5,
      draw: 0.33 + oddsAnalysis.fusion.draw * 0.5,
      winB: 0.17 + oddsAnalysis.fusion.winB * 0.5
    } : { winA: 0.5, draw: 0.33, winB: 0.17 };

    evaluation.brierScore = Math.pow(finalProbs.winA - actual.winA, 2) +
                            Math.pow(finalProbs.draw - actual.draw, 2) +
                            Math.pow(finalProbs.winB - actual.winB, 2);

    var eps = 0.0001;
    evaluation.logLoss = -(actual.winA * Math.log(Math.max(eps, finalProbs.winA)) +
                          actual.draw * Math.log(Math.max(eps, finalProbs.draw)) +
                          actual.winB * Math.log(Math.max(eps, finalProbs.winB)));

    if (oddsAnalysis.allSignals) {
      oddsAnalysis.allSignals.forEach(function(sig) {
        var wasCorrect = false;
        if (sig.msg && actual.winA === 1) {
          wasCorrect = sig.msg.indexOf('主胜') >= 0 || sig.msg.indexOf('主队') >= 0;
        } else if (actual.draw === 1) {
          wasCorrect = sig.msg && sig.msg.indexOf('平局') >= 0;
        } else if (actual.winB === 1) {
          wasCorrect = sig.msg && (sig.msg.indexOf('客胜') >= 0 || sig.msg.indexOf('客队') >= 0);
        }
        if (wasCorrect) evaluation.signalQuality.correctSignals++;
      });
      evaluation.signalQuality.signalAccuracy = evaluation.signalQuality.detectedSignals > 0
        ? evaluation.signalQuality.correctSignals / evaluation.signalQuality.detectedSignals
        : 0;
    }

    if (evaluation.brierScore > 0.4) {
      evaluation.recommendations.push('赔率分析信号质量较低, 建议增加更多数据源');
    }
    if (evaluation.signalQuality.signalAccuracy < 0.4) {
      evaluation.recommendations.push('信号准确性不足, 需要调整信号检测阈值');
    }

    return evaluation;
  }

  function calcOddsTrajectoryCorrection(winDrift, drawDrift, loseDrift, hcDrift, steamMoves, scoreTrend, goalsTrend, htftTrend) {
    var corr = { winA: 0, draw: 0, winB: 0, goalLambda: 0, confidence: 0 };
    if (winDrift < -0.02) corr.winA += winDrift * 0.5;
    if (loseDrift > 0.02) corr.winB += loseDrift * 0.4;
    if (drawDrift > 0.015) corr.draw += drawDrift * 0.6;
    if (hcDrift.lose > 0.03) { corr.winB += 0.02; corr.draw += 0.01; corr.winA -= 0.03; }
    for (var i = 0; i < steamMoves.length; i++) {
      var sm = steamMoves[i];
      if (sm.direction === '客胜') { corr.winB += 0.03; corr.winA -= 0.02; }
      else if (sm.direction === '平局') { corr.draw += 0.04; corr.winA -= 0.02; corr.winB -= 0.02; }
      else if (sm.direction === '主胜') { corr.winA += 0.02; corr.winB -= 0.02; }
    }
    if (scoreTrend['1:1'] && scoreTrend['1:1'].implied > 0.3) corr.draw += 0.02;
    if (goalsTrend['0球'] && goalsTrend['0球'].drift < -0.5) corr.goalLambda -= 0.1;
    if (goalsTrend['1球'] && goalsTrend['1球'].drift < -0.3) corr.goalLambda -= 0.05;
    if (htftTrend['平平'] && htftTrend['平平'].drift < -0.3) corr.draw += 0.015;
    var total = corr.winA + corr.draw + corr.winB;
    corr.winA -= total / 3; corr.draw -= total / 3; corr.winB -= total / 3;
    corr.winA = Math.max(-0.08, Math.min(0.08, corr.winA));
    corr.draw = Math.max(-0.08, Math.min(0.08, corr.draw));
    corr.winB = Math.max(-0.08, Math.min(0.08, corr.winB));
    corr.goalLambda = Math.max(-0.3, Math.min(0.3, corr.goalLambda));
    corr.confidence = Math.min(1.0, (Math.abs(corr.winA) + Math.abs(corr.draw) + Math.abs(corr.winB)) / 0.15);
    return corr;
  }

  function applyOddsTrajectoryToPrediction(baseProb, correction) {
    var adjusted = { winA:baseProb.winA+correction.winA, draw:baseProb.draw+correction.draw, winB:baseProb.winB+correction.winB };
    var total = adjusted.winA+adjusted.draw+adjusted.winB;
    adjusted.winA/=total; adjusted.draw/=total; adjusted.winB/=total;
    adjusted.winA=Math.max(0.05,adjusted.winA); adjusted.draw=Math.max(0.05,adjusted.draw); adjusted.winB=Math.max(0.05,adjusted.winB);
    total=adjusted.winA+adjusted.draw+adjusted.winB;
    adjusted.winA/=total; adjusted.draw/=total; adjusted.winB/=total;
    return adjusted;
  }

  function evaluateOddsTrajectoryPostMatch(trajectoryAnalysis, actualResult) {
    if (!trajectoryAnalysis || trajectoryAnalysis.error) {
      return { error: '无赔率走势数据', conclusion: '无法评估' };
    }

    var lastProb = trajectoryAnalysis.european.close.probs;
    var outcomeMap = { winA: [1, 0, 0], draw: [0, 1, 0], winB: [0, 0, 1] };
    var actual = outcomeMap[actualResult];

    var mktPrediction = lastProb.w > lastProb.d && lastProb.w > lastProb.l ? 'winA' : (lastProb.l > lastProb.d ? 'winB' : 'draw');
    var mktHit = mktPrediction === actualResult;

    var driftDir = trajectoryAnalysis.european.drift;
    var trajectoryPredicted = null;
    if (driftDir.draw > 0.02 && driftDir.draw > driftDir.win && driftDir.draw > driftDir.lose) trajectoryPredicted = 'draw';
    else if (driftDir.lose > 0.02 && driftDir.lose > driftDir.win) trajectoryPredicted = 'winB';
    else if (driftDir.win > 0.02) trajectoryPredicted = 'winA';
    var trajectoryHit = trajectoryPredicted === actualResult;

    return {
      marketPrediction: mktPrediction,
      marketHit: mktHit,
      trajectoryPredicted: trajectoryPredicted,
      trajectoryHit: trajectoryHit,
      conclusion: mktHit ? '临场赔率预测正确' : (trajectoryHit ? '走势方向预测正确' : '赔率预测失败')
    };
  }

  function kellyCriterion(prob, odds) {
    if (prob >= 1 || prob <= 0 || odds <= 1) return 0;
    return (prob * odds - 1) / (odds - 1);
  }

  function calculatePortfolioEdge(bets) {
    if (!bets || bets.length === 0) return null;

    var n = bets.length;
    var totalStake = 0;
    var totalEV = 0;
    var evs = [];
    var returns = [];

    for (var i = 0; i < n; i++) {
      var b = bets[i];
      var ev = b.stake * (b.modelProb * b.odds - 1);
      evs.push(ev);
      totalEV += ev;
      totalStake += b.stake;
      returns.push(b.odds * b.stake);
    }

    var variance = 0;
    var covMatrix = [];
    for (var i = 0; i < n; i++) {
      covMatrix[i] = [];
      for (var j = 0; j < n; j++) {
        var bi = bets[i], bj = bets[j];
        var pi = bi.modelProb, pj = bj.modelProb;
        var si = bi.stake, sj = bj.stake;
        var oi = bi.odds, oj = bj.odds;

        if (i === j) {
          var winRet = oi - 1;
          var varI = pi * winRet * winRet + (1 - pi) * 1;
          covMatrix[i][j] = si * si * varI;
        } else {
          var corr = calcBetCorrelation(bi, bj);
          var cov = corr * Math.sqrt(covMatrix[i][i] || 0) * Math.sqrt(covMatrix[j][j] || 0);
          if (covMatrix[i][i] === undefined || covMatrix[j][j] === undefined) {
            var winRetI = oi - 1;
            var varI2 = pi * winRetI * winRetI + (1 - pi) * 1;
            var winRetJ = oj - 1;
            var varJ2 = pj * winRetJ * winRetJ + (1 - pj) * 1;
            cov = corr * si * sj * Math.sqrt(varI2 * varJ2);
          }
          covMatrix[i][j] = cov;
        }
      }
    }

    for (var i = 0; i < n; i++) {
      var bi = bets[i];
      var winRet = bi.odds - 1;
      var varI = bi.modelProb * winRet * winRet + (1 - bi.modelProb) * 1;
      covMatrix[i][i] = bi.stake * bi.stake * varI;
    }

    for (var i = 0; i < n; i++) {
      for (var j = 0; j < n; j++) {
        variance += covMatrix[i][j];
      }
    }

    var corrMatrix = [];
    for (var i = 0; i < n; i++) {
      corrMatrix[i] = [];
      for (var j = 0; j < n; j++) {
        if (i === j) {
          corrMatrix[i][j] = 1.0;
        } else {
          var di = covMatrix[i][i], dj = covMatrix[j][j];
          if (di > 0 && dj > 0) {
            corrMatrix[i][j] = covMatrix[i][j] / Math.sqrt(di * dj);
          } else {
            corrMatrix[i][j] = 0;
          }
        }
      }
    }

    var rebalanced = [];
    var sumKelly = 0;
    for (var i = 0; i < n; i++) {
      var kf = kellyCriterion(bets[i].modelProb, bets[i].odds);
      rebalanced.push({ bet: i, kelly: kf, recommendedStake: 0 });
      sumKelly += kf;
    }
    for (var i = 0; i < n; i++) {
      if (sumKelly > 0) {
        rebalanced[i].recommendedStake = totalStake * (rebalanced[i].kelly / sumKelly);
      }
      rebalanced[i].fractionalKelly = rebalanced[i].kelly * 0.25;
    }

    return {
      totalExpectedValue: totalEV,
      totalStake: totalStake,
      expectedValuePercent: totalStake > 0 ? (totalEV / totalStake) * 100 : 0,
      portfolioVariance: variance,
      portfolioStdDev: Math.sqrt(Math.max(0, variance)),
      sharpeRatio: variance > 0 ? totalEV / Math.sqrt(variance) : 0,
      correlationMatrix: corrMatrix,