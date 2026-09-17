/* ============================================================
 * FiveLeagues_SquadParser - 阵容深度解析模块
 * 从 model-engine.js 拆出，teams 和 config 通过参数传入
 * 包含: parseSquadDepth, applySquadDepthAdjustment, restoreTeamOriginalStats, restoreOriginalTeamData
 * ============================================================ */
var FiveLeagues_SquadParser = (function() {

  var DEFAULT_CONFIG = {
    squadDepth: {
      completenessWeight: 0.3,
      keyPlayerImpactWeight: 0.5,
      depthScoreWeight: 0.2,
      maxDeltaRatio: 0.35,
      minKeyPlayerImpact: 0.5
    }
  };

  // ─── 解析阵容数据，计算阵容完整度和关键球员影响 ───
  function parseSquadDepth(teamKey, squadData, teams) {
    if (!squadData) return { completeness: 1.0, keyPlayerImpact: 1.0, depthScore: 0.5, warning: '无阵容数据' };

    var team = teams ? teams[teamKey] : null;
    if (!team) return { completeness: 1.0, keyPlayerImpact: 1.0, depthScore: 0.5, warning: '未知球队' };

    var startingXI = squadData.startingXI || [];
    var absent = squadData.absent || [];
    var keyPlayers = squadData.keyPlayers || {};

    // 1. 阵容完整度: 首发11人
    var completeness = Math.min(1.0, startingXI.length / 11);

    // 2. 关键球员影响
    var keyPlayerImpact = 1.0;
    var missingKeyPlayers = [];
    for (var name in keyPlayers) {
      var kp = keyPlayers[name];
      if (kp.status === 'injured' || kp.status === 'suspended') {
        var penalty = (kp.rating / 100) * 0.15;
        keyPlayerImpact -= penalty;
        missingKeyPlayers.push(name + '(' + kp.rating + ')');
      }
    }
    keyPlayerImpact = Math.max(0.5, keyPlayerImpact);

    // 3. 阵容深度: 替补质量评估
    var bench = squadData.bench || [];
    var depthScore = Math.min(1.0, bench.length / 7);

    // 4. 综合评分
    var overall = completeness * 0.3 + keyPlayerImpact * 0.5 + depthScore * 0.2;

    // 5. 生成警告信息
    var warning = '';
    if (missingKeyPlayers.length > 0) {
      warning += '核心缺阵: ' + missingKeyPlayers.join(', ');
    }
    if (absent.length > 3) {
      warning += '; 大面积缺阵 ' + absent.length + '人';
    }
    if (completeness < 0.9) {
      warning += '; 首发不完整';
    }

    return {
      completeness: parseFloat(completeness.toFixed(2)),
      keyPlayerImpact: parseFloat(keyPlayerImpact.toFixed(2)),
      depthScore: parseFloat(depthScore.toFixed(2)),
      overall: parseFloat(overall.toFixed(2)),
      missingKeyPlayers: missingKeyPlayers,
      warning: warning || '阵容完整'
    };
  }

  // ─── 应用阵容深度修正到球队 attack/defence ───
  function applySquadDepthAdjustment(teamKey, squadData, teams, options) {
    options = options || {};
    var config = options.config || DEFAULT_CONFIG.squadDepth;
    var depth = parseSquadDepth(teamKey, squadData, teams);
    var team = teams ? teams[teamKey] : null;
    if (!team) return depth;

    // 保存原始值（仅在首次调用时保存）
    if (!team._originalAttack) team._originalAttack = team.attack;
    if (!team._originalDefence) team._originalDefence = team.defence;
    if (!team._originalInjury) team._originalInjury = team.injury;

    // delta上限控制
    var maxDeltaRatio = options.maxDeltaRatio || config.maxDeltaRatio || 0.35;
    var baseAttack = team._originalAttack;
    var baseDefence = team._originalDefence;

    var adjustFactor = depth.overall;
    var currentAttack = baseAttack * adjustFactor;
    var currentDefence = baseDefence * adjustFactor;
    var attackDelta = (currentAttack - baseAttack) / baseAttack;
    var defenceDelta = (currentDefence - baseDefence) / baseDefence;

    var maxDelta = maxDeltaRatio;
    var attackDeltaCapped = Math.max(-maxDelta, Math.min(maxDelta, attackDelta));
    var defenceDeltaCapped = Math.max(-maxDelta, Math.min(maxDelta, defenceDelta));

    team.attack = baseAttack * (1 + attackDeltaCapped);
    team.defence = baseDefence * (1 + defenceDeltaCapped);

    var injuryDelta = attackDeltaCapped;
    team.injury = team._originalInjury * (1 + injuryDelta);

    depth.deltaCapApplied = (attackDelta !== attackDeltaCapped || defenceDelta !== defenceDeltaCapped);
    depth.attackDelta = parseFloat(attackDelta.toFixed(4));
    depth.defenceDelta = parseFloat(defenceDelta.toFixed(4));
    depth.attackDeltaCapped = parseFloat(attackDeltaCapped.toFixed(4));
    depth.defenceDeltaCapped = parseFloat(defenceDeltaCapped.toFixed(4));
    depth.maxDeltaRatio = maxDeltaRatio;

    return depth;
  }

  // ─── 恢复球队原始 attack/defence ───
  function restoreTeamOriginalStats(teamKey, teams) {
    var team = teams ? teams[teamKey] : null;
    if (!team) return;
    if (team._originalAttack) team.attack = team._originalAttack;
    if (team._originalDefence) team.defence = team._originalDefence;
  }

  // ─── 完整恢复球队原始数据 ───
  function restoreOriginalTeamData(teamKey, teams) {
    var team = teams ? teams[teamKey] : null;
    if (!team) return;
    if (team._originalAttack) team.attack = team._originalAttack;
    if (team._originalDefence) team.defence = team._originalDefence;
    if (team._originalInjury) team.injury = team._originalInjury;
  }

  return {
    parseSquadDepth: parseSquadDepth,
    applySquadDepthAdjustment: applySquadDepthAdjustment,
    restoreTeamOriginalStats: restoreTeamOriginalStats,
    restoreOriginalTeamData: restoreOriginalTeamData
  };
})();
