/* ============================================================
 * FiveLeagues_TacticalAnalyzer - 战术情报分析模块
 * 从 model-engine.js 拆出，纯函数 + teams 参数传入
 * 包含: parseTacticalIntel, parseHistoricalH2H, parsePlayerStats, parseWeather, parseRecentForm
 * ============================================================ */
var FiveLeagues_TacticalAnalyzer = (function() {

  // ─── 时间加权近期状态追踪 ───
  function parseRecentForm(teamKey, recentMatches, teams) {
    if (!recentMatches || recentMatches.length === 0) {
      return { formScore: 0, goalsForAvg: 0, goalsAgainstAvg: 0, winRate: 0, warning: '无近期战绩数据' };
    }
    var wins = 0, draws = 0, losses = 0;
    var goalsFor = 0, goalsAgainst = 0;
    var bigWin = 0, cleanSheet = 0;
    var strongOpponentResults = [];

    for (var i = 0; i < recentMatches.length; i++) {
      var m = recentMatches[i];
      var scoreParts = m.score.split('-');
      var gf = parseInt(scoreParts[0]) || 0;
      var ga = parseInt(scoreParts[1]) || 0;
      goalsFor += gf;
      goalsAgainst += ga;

      if (gf > ga) { wins++; if (gf - ga >= 3) bigWin++; }
      else if (gf === ga) draws++;
      else losses++;

      if (ga === 0) cleanSheet++;

      // 检测对手强度
      var oppTeam = (teams && (teams[m.opponent] || teams[(m.opponent || '').toLowerCase().replace(/\s/g, '')])) || {};
      if (oppTeam.rank && oppTeam.rank <= 20) {
        strongOpponentResults.push({ result: gf > ga ? 'W' : (gf === ga ? 'D' : 'L'), gf: gf, ga: ga });
      }
    }

    var n = recentMatches.length;
    var winRate = wins / n;
    var gfAvg = goalsFor / n;
    var gaAvg = goalsAgainst / n;

    var goalDiff = (gfAvg - gaAvg) * 0.3;
    var csRate = (cleanSheet / n) * 0.2;
    var strongWinRate = strongOpponentResults.length > 0
      ? (strongOpponentResults.filter(function(r) { return r.result === 'W'; }).length / strongOpponentResults.length) * 0.1
      : 0;
    var formScore = winRate * 0.4 + Math.max(-0.3, Math.min(0.3, goalDiff)) + csRate + strongWinRate;

    return {
      formScore: parseFloat(formScore.toFixed(3)),
      winRate: parseFloat(winRate.toFixed(2)),
      goalsForAvg: parseFloat(gfAvg.toFixed(2)),
      goalsAgainstAvg: parseFloat(gaAvg.toFixed(2)),
      cleanSheetRate: parseFloat((cleanSheet / n).toFixed(2)),
      bigWinRate: parseFloat((bigWin / n).toFixed(2)),
      strongOpponentWinRate: strongOpponentResults.length > 0
        ? parseFloat((strongOpponentResults.filter(function(r) { return r.result === 'W'; }).length / strongOpponentResults.length).toFixed(2))
        : null,
      matchesAnalyzed: n,
      warning: formScore > 0.3 ? '状态火热' : (formScore < -0.1 ? '状态低迷' : '状态一般')
    };
  }

  // ─── 战术情报解析（纯函数） ───
  function parseTacticalIntel(intelText) {
    if (!intelText) return { factors: [], overallImpact: 0 };

    var factors = [];
    var impact = 0;
    var text = intelText.toLowerCase();

    // 战意因子
    if (text.indexOf('谢幕') >= 0 || text.indexOf('最后一场') >= 0 || text.indexOf('复仇') >= 0) {
      factors.push({ type: 'motivation', desc: '黄金一代谢幕/复仇战', value: 0.08 });
      impact += 0.08;
    }
    if (text.indexOf('首次参赛') >= 0 || text.indexOf('新军') >= 0) {
      factors.push({ type: 'motivation', desc: '赛事新军，无包袱', value: 0.03 });
      impact += 0.03;
    }

    // 战术风格因子
    if (text.indexOf('铁桶') >= 0 || text.indexOf('5后卫') >= 0 || text.indexOf('密集防守') >= 0) {
      factors.push({ type: 'tactical', desc: '密集防守体系', value: -0.05 });
      impact -= 0.05;
    }
    if (text.indexOf('高位逼抢') >= 0 || text.indexOf('pressing') >= 0) {
      factors.push({ type: 'tactical', desc: '高位逼抢战术', value: 0.04 });
      impact += 0.04;
    }
    if (text.indexOf('控球') >= 0 && text.indexOf('传控') >= 0) {
      factors.push({ type: 'tactical', desc: '传控体系', value: 0.03 });
      impact += 0.03;
    }

    // 教练因子
    if (text.indexOf('不败') >= 0 && text.indexOf('执教') >= 0) {
      factors.push({ type: 'coach', desc: '教练上任后不败', value: 0.05 });
      impact += 0.05;
    }

    // 赛程/体能因子
    if (text.indexOf('保留体力') >= 0 || text.indexOf('轮换') >= 0) {
      factors.push({ type: 'rotation', desc: '可能轮换保留体力', value: -0.04 });
      impact -= 0.04;
    }
    if (text.indexOf('远征') >= 0 || text.indexOf('2800公里') >= 0) {
      factors.push({ type: 'travel', desc: '长途旅行疲劳', value: -0.03 });
      impact -= 0.03;
    }

    // 心理因子
    if (text.indexOf('揭幕战魔咒') >= 0 || text.indexOf('从未赢过') >= 0) {
      factors.push({ type: 'psychology', desc: '历史心理阴影', value: -0.05 });
      impact -= 0.05;
    }

    return { factors: factors, overallImpact: parseFloat(impact.toFixed(3)) };
  }

  // ─── 历史交锋解析（纯函数） ───
  function parseHistoricalH2H(h2hRecords) {
    if (!h2hRecords || h2hRecords.length === 0) {
      return { h2hFactor: 0, winRateA: null, winRateB: null, lastWinner: null, warning: '无交锋记录' };
    }

    var aWins = 0, bWins = 0, draws = 0;
    var last3 = h2hRecords.slice(-3);

    for (var i = 0; i < h2hRecords.length; i++) {
      var r = h2hRecords[i];
      var parts = r.score.split('-');
      var aGoals = parseInt(parts[0]) || 0;
      var bGoals = parseInt(parts[1]) || 0;
      if (aGoals > bGoals) aWins++;
      else if (aGoals < bGoals) bWins++;
      else draws++;
    }

    var total = h2hRecords.length;
    var winRateA = aWins / total;
    var winRateB = bWins / total;

    var h2hFactor = 0;
    if (last3.length > 0) {
      var last3AWins = 0;
      for (var j = 0; j < last3.length; j++) {
        var lr = last3[j];
        var lp = lr.score.split('-');
        if (parseInt(lp[0]) > parseInt(lp[1])) last3AWins++;
      }
      h2hFactor = (last3AWins / last3.length - 0.5) * 0.1;
    }

    var last = h2hRecords[h2hRecords.length - 1];
    var lastParts = last.score.split('-');
    var lastWinner = parseInt(lastParts[0]) > parseInt(lastParts[1]) ? 'A' : (parseInt(lastParts[0]) < parseInt(lastParts[1]) ? 'B' : 'draw');

    return {
      h2hFactor: parseFloat(h2hFactor.toFixed(3)),
      totalMatches: total,
      winRateA: parseFloat(winRateA.toFixed(2)),
      winRateB: parseFloat(winRateB.toFixed(2)),
      drawRate: parseFloat((draws / total).toFixed(2)),
      lastWinner: lastWinner,
      last3Matches: last3.map(function(r) { return r.score; }),
      warning: h2hFactor > 0.05 ? '历史交锋主队占优' : (h2hFactor < -0.05 ? '历史交锋客队占优' : '交锋记录均衡')
    };
  }

  // ─── 球员联赛数据解析（纯函数） ───
  function parsePlayerStats(teamKey, playerStats) {
    if (!playerStats || Object.keys(playerStats).length === 0) {
      return { attackBoost: 0, defenceBoost: 0, overallBoost: 0, warning: '无球员数据' };
    }

    var totalRating = 0, count = 0;
    var totalGoals = 0, totalAssists = 0, totalKeyPass = 0;
    var topPlayers = [];

    for (var name in playerStats) {
      var p = playerStats[name];
      var rating = p.rating || 75;
      totalRating += rating;
      count++;
      totalGoals += p.goals || 0;
      totalAssists += p.assists || 0;
      totalKeyPass += p.keyPass || 0;
      if (rating >= 85) topPlayers.push({ name: name, rating: rating });
    }

    var avgRating = totalRating / count;
    var ratingBoost = (avgRating - 80) * 0.005;
    var attackBoost = (totalGoals + totalAssists) / count * 0.01;
    var creativityBoost = totalKeyPass / count * 0.02;
    var overallBoost = ratingBoost + attackBoost + creativityBoost;

    return {
      avgRating: parseFloat(avgRating.toFixed(1)),
      topPlayers: topPlayers,
      attackBoost: parseFloat(attackBoost.toFixed(3)),
      creativityBoost: parseFloat(creativityBoost.toFixed(3)),
      overallBoost: parseFloat(overallBoost.toFixed(3)),
      warning: topPlayers.length >= 3 ? '拥有' + topPlayers.length + '名顶级球星' : '缺乏顶级球星'
    };
  }

  // ─── 天气/场地解析（纯函数） ───
  function parseWeather(weatherData) {
    if (!weatherData) return { impact: 0, factors: [] };

    var factors = [];
    var impact = 0;

    var temp = weatherData.temperature || 20;
    if (temp > 30) {
      factors.push({ type: 'temperature', desc: '高温(>' + temp + '度)', value: -0.04 });
      impact -= 0.04;
    } else if (temp < 5) {
      factors.push({ type: 'temperature', desc: '低温(' + temp + '度)', value: -0.02 });
      impact -= 0.02;
    }

    var humidity = weatherData.humidity || 50;
    if (humidity > 80) {
      factors.push({ type: 'humidity', desc: '高湿度(' + humidity + '%)', value: -0.02 });
      impact -= 0.02;
    }

    var altitude = weatherData.altitude || 0;
    if (altitude > 1500) {
      factors.push({ type: 'altitude', desc: '高海拔(' + altitude + 'm)', value: -0.03 });
      impact -= 0.03;
    }

    var venue = weatherData.venue || '';
    if (venue.indexOf('artificial') >= 0 || venue.indexOf('人工') >= 0) {
      factors.push({ type: 'pitch', desc: '人工草皮', value: -0.02 });
      impact -= 0.02;
    }

    return { impact: parseFloat(impact.toFixed(3)), factors: factors };
  }

  return {
    parseRecentForm: parseRecentForm,
    parseTacticalIntel: parseTacticalIntel,
    parseHistoricalH2H: parseHistoricalH2H,
    parsePlayerStats: parsePlayerStats,
    parseWeather: parseWeather
  };
})();
