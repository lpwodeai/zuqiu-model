/* ============================================================
 * CSV批量导入脚本
 * 功能: 将CSV格式的比赛数据转换为JSON格式并导入模型进行复盘
 * 支持: 比赛结果、赔率数据、统计数据的批量导入
 * ============================================================ */

var MatchDataImporter = {
  // CSV字段映射配置
  fieldMapping: {
    matchId: ['MatchID', '比赛ID', 'ID'],
    date: ['Date', '日期', '比赛日期'],
    teamA: ['TeamA', '主队', '球队A'],
    teamB: ['TeamB', '客队', '球队B'],
    goalsA: ['GoalsA', '主队进球', 'A队进球', 'HomeGoals'],
    goalsB: ['GoalsB', '客队进球', 'B队进球', 'AwayGoals'],
    halfTimeA: ['HTGoalsA', '半场主队', 'HT_A'],
    halfTimeB: ['HTGoalsB', '半场客队', 'HT_B'],
    venue: ['Venue', '场地', '球场'],
    round: ['Round', '轮次', '阶段'],
    group: ['Group', '小组'],
    oddsWinA: ['OddsWinA', '主胜赔', '胜赔率'],
    oddsDraw: ['OddsDraw', '平局赔', '平赔率'],
    oddsLose: ['OddsLose', '客胜赔', '负赔率'],
    possessionA: ['PossessionA', '主队控球', '控球率A'],
    shotsA: ['ShotsA', '主队射门', '射门A'],
    shotsOnTargetA: ['ShotsOnTargetA', '主队射正', '射正A'],
    xGA: ['xGA', '主队xG', 'xG_A'],
    xGB: ['xGB', '客队xG', 'xG_B']
  },

  // 解析CSV文件
  parseCSV: function(csvText) {
    var lines = csvText.trim().split('\n');
    if (lines.length < 2) return { error: 'CSV数据不足' };

    var headers = lines[0].split(',').map(function(h) { return h.trim(); });
    var rows = [];

    for (var i = 1; i < lines.length; i++) {
      var parts = this.parseCSVLine(lines[i]);
      if (parts.length === 0) continue;

      var row = {};
      for (var j = 0; j < headers.length; j++) {
        row[headers[j].trim()] = parts[j] ? parts[j].trim() : '';
      }
      rows.push(row);
    }

    return { headers: headers, rows: rows };
  },

  // 解析单行CSV（处理带引号的字段）
  parseCSVLine: function(line) {
    var result = [];
    var current = '';
    var inQuotes = false;

    for (var i = 0; i < line.length; i++) {
      var char = line[i];

      if (char === '"') {
        if (inQuotes && line[i + 1] === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (char === ',' && !inQuotes) {
        result.push(current);
        current = '';
      } else {
        current += char;
      }
    }
    result.push(current);

    return result;
  },

  // 将CSV行转换为比赛数据格式
  convertToMatchData: function(csvRow) {
    var matchData = {
      matchId: this.getFieldValue(csvRow, 'matchId') || 'UNKNOWN-' + Date.now(),
      date: this.getFieldValue(csvRow, 'date') || '',
      teamA: {
        name: this.getFieldValue(csvRow, 'teamA') || '',
        nameCN: this.getFieldValue(csvRow, 'teamA') || ''
      },
      teamB: {
        name: this.getFieldValue(csvRow, 'teamB') || '',
        nameCN: this.getFieldValue(csvRow, 'teamB') || ''
      },
      result: {
        goalsA: parseInt(this.getFieldValue(csvRow, 'goalsA') || '0'),
        goalsB: parseInt(this.getFieldValue(csvRow, 'goalsB') || '0'),
        halfTimeGoalsA: parseInt(this.getFieldValue(csvRow, 'halfTimeA') || '0'),
        halfTimeGoalsB: parseInt(this.getFieldValue(csvRow, 'halfTimeB') || '0'),
        extraTime: false,
        penaltyShootout: false
      },
      venue: this.getFieldValue(csvRow, 'venue') || '',
      round: this.getFieldValue(csvRow, 'round') || '',
      group: this.getFieldValue(csvRow, 'group') || '',
      odds: {
        opening: {},
        closing: {}
      },
      statistics: {}
    };

    // 处理赔率数据
    var winA = parseFloat(this.getFieldValue(csvRow, 'oddsWinA'));
    var draw = parseFloat(this.getFieldValue(csvRow, 'oddsDraw'));
    var lose = parseFloat(this.getFieldValue(csvRow, 'oddsLose'));

    if (!isNaN(winA) && !isNaN(draw) && !isNaN(lose)) {
      matchData.odds.opening.european = { win: winA, draw: draw, lose: lose };
      matchData.odds.closing.european = { win: winA, draw: draw, lose: lose };
    }

    // 处理统计数据
    var possessionA = parseInt(this.getFieldValue(csvRow, 'possessionA'));
    var shotsA = parseInt(this.getFieldValue(csvRow, 'shotsA'));
    var shotsOnTargetA = parseInt(this.getFieldValue(csvRow, 'shotsOnTargetA'));
    var xGA = parseFloat(this.getFieldValue(csvRow, 'xGA'));
    var xGB = parseFloat(this.getFieldValue(csvRow, 'xGB'));

    if (!isNaN(possessionA)) matchData.statistics.possessionA = possessionA;
    if (!isNaN(possessionA)) matchData.statistics.possessionB = 100 - possessionA;
    if (!isNaN(shotsA)) matchData.statistics.shotsA = shotsA;
    if (!isNaN(shotsOnTargetA)) matchData.statistics.shotsOnTargetA = shotsOnTargetA;
    if (!isNaN(xGA) || !isNaN(xGB)) {
      matchData.statistics.xG = {
        teamA: !isNaN(xGA) ? xGA : 0,
        teamB: !isNaN(xGB) ? xGB : 0
      };
    }

    return matchData;
  },

  // 获取字段值（支持多别名）
  getFieldValue: function(row, fieldName) {
    var aliases = this.fieldMapping[fieldName];
    if (!aliases) return null;

    for (var i = 0; i < aliases.length; i++) {
      if (row[aliases[i]] !== undefined) {
        return row[aliases[i]];
      }
    }
    return null;
  },

  // 批量导入CSV数据
  importFromCSV: function(csvText, options) {
    options = options || {};
    var result = {
      success: 0,
      failed: 0,
      imported: [],
      errors: []
    };

    var parsed = this.parseCSV(csvText);
    if (parsed.error) {
      return { error: parsed.error };
    }

    for (var i = 0; i < parsed.rows.length; i++) {
      try {
        var matchData = this.convertToMatchData(parsed.rows[i]);

        if (!matchData.teamA.name || !matchData.teamB.name) {
          result.failed++;
          result.errors.push({ row: i + 2, error: '缺少球队名称' });
          continue;
        }

        // 尝试获取球队代码
        var teamAKey = this.findTeamKey(matchData.teamA.name);
        var teamBKey = this.findTeamKey(matchData.teamB.name);

        if (teamAKey) matchData.teamA.code = teamAKey;
        if (teamBKey) matchData.teamB.code = teamBKey;

        result.imported.push(matchData);
        result.success++;

      } catch (e) {
        result.failed++;
        result.errors.push({ row: i + 2, error: e.message });
      }
    }

    return result;
  },

  // 根据球队名称查找球队代码
  findTeamKey: function(teamName) {
    if (!teamName || !window.FiveLeagues || !FiveLeagues.TEAMS) return null;

    var lowerName = teamName.toLowerCase().trim();
    for (var key in FiveLeagues.TEAMS) {
      var team = FiveLeagues.TEAMS[key];
      if (team.name && team.name.toLowerCase() === lowerName) return key;
      if (team.nameCN && team.nameCN === teamName) return key;
      if (team.code && team.code.toLowerCase() === lowerName) return key;
    }
    return null;
  },

  // 导入并执行复盘
  importAndReview: function(csvText) {
    var importResult = this.importFromCSV(csvText);
    if (importResult.error) {
      return { error: importResult.error };
    }

    var reviewResults = [];
    for (var i = 0; i < importResult.imported.length; i++) {
      var match = importResult.imported[i];
      try {
        var teamAKey = match.teamA.code || match.teamA.name.toLowerCase().replace(/\s+/g, '');
        var teamBKey = match.teamB.code || match.teamB.name.toLowerCase().replace(/\s+/g, '');

        // 创建预测数据（用于复盘对比）
        var prediction = {
          matchId: match.matchId,
          teamA: teamAKey,
          teamB: teamBKey,
          wdw: match.odds.closing.european || { win: 2.0, draw: 3.2, lose: 2.0 },
          score: [],
          htft: { top3: [] },
          total: {}
        };

        // 创建实际结果数据
        var actual = {
          matchId: match.matchId,
          teamA: teamAKey,
          teamB: teamBKey,
          goalsA: match.result.goalsA,
          goalsB: match.result.goalsB,
          halfTimeA: match.result.halfTimeGoalsA,
          halfTimeB: match.result.halfTimeGoalsB
        };

        // 执行复盘
        var review = FiveLeagues.reviewMatch(prediction, actual);
        review.matchData = match;
        reviewResults.push(review);

      } catch (e) {
        reviewResults.push({ error: e.message, matchId: match.matchId });
      }
    }

    return {
      importStats: importResult,
      reviewResults: reviewResults
    };
  },

  // 生成CSV模板
  generateCSVTemplate: function() {
    var headers = [
      'MatchID', 'Date', 'Round', 'Group', 'Venue',
      'TeamA', 'TeamB',
      'GoalsA', 'GoalsB', 'HTGoalsA', 'HTGoalsB',
      'OddsWinA', 'OddsDraw', 'OddsLose',
      'PossessionA', 'ShotsA', 'ShotsOnTargetA',
      'xGA', 'xGB'
    ];

    var template = headers.join(',') + '\n';
    template += 'PL-2024-001,2024-08-17,Matchday 1,PL,Emirates Stadium,Arsenal,Man United,2,1,1,0,1.75,3.60,4.50,58,14,6,1.8,1.2\n';
    template += 'BL1-2024-001,2024-08-16,Matchday 1,BL1,Allianz Arena,Bayern Munich,Freiburg,3,1,1,1,1.35,4.80,8.50,62,16,8,2.1,0.8\n';
    template += 'SA-2024-001,2024-08-18,Matchday 1,SA,Camp Nou,Barcelona,Valencia,2,0,1,0,1.40,4.50,7.50,65,15,7,1.9,0.5\n';

    return template;
  }
};

// 导出到全局
if (typeof module !== 'undefined' && module.exports) {
  module.exports = MatchDataImporter;
} else if (typeof window !== 'undefined') {
  window.MatchDataImporter = MatchDataImporter;
}

console.log('MatchDataImporter 已加载');
