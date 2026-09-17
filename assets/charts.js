(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var accent3 = style.getPropertyValue('--accent3').trim();
  var gold = style.getPropertyValue('--gold').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  // --- Chart 1: 五大联赛攻击力排行榜 ---
  var chart1 = echarts.init(document.getElementById('chart-power-rank'), null, { renderer: 'svg' });
  var attackTeams = ["Aston Villa", "Man United", "Bayern Munich", "Freiburg", "Union Berlin", "Stuttgart", "Dortmund", "Villarreal", "Roma", "Inter", "Monaco", "Auxerre"];
  var attackValues = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0];

  chart1.setOption({
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, appendToBody: true },
    legend: { data: ['攻击力'], textStyle: { color: muted }, top: 0 },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true },
    xAxis: { type: 'category', data: attackTeams, axisLabel: { color: muted, rotate: 30, fontSize: 11 }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', name: '攻击力评分', nameTextStyle: { color: muted }, axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '攻击力', type: 'bar', data: attackValues, itemStyle: { color: accent }, barWidth: '40%', label: { show: true, position: 'top', color: accent, fontSize: 10 } }
    ]
  });
  window.addEventListener('resize', function() { chart1.resize(); });

  // --- Chart 2: 攻防能力对比 ---
  var chart2 = echarts.init(document.getElementById('chart-group-xg'), null, { renderer: 'svg' });
  var compareTeams = ["FC Koln", "Monaco", "Pisa", "St Pauli", "RB Leipzig", "Lyon", "Crystal Palace", "Hoffenheim", "Ath Bilbao", "Metz", "Strasbourg", "Ein Frankfurt"];
  var compareAttack = [1.23, 2.0, 0.5, 0.61, 1.86, 1.24, 0.94, 1.06, 1.07, 0.51, 2.0, 1.77];
  var compareDefense = [0.7, 0.7, 0.69, 0.6799999999999999, 0.6799999999999999, 0.6799999999999999, 0.6599999999999999, 0.6599999999999999, 0.6599999999999999, 0.65, 0.65, 0.64];

  chart2.setOption({
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    legend: { data: ['攻击力', '防守稳定性'], textStyle: { color: muted }, top: 0 },
    grid: { left: '3%', right: '4%', bottom: '5%', top: '12%', containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'category', data: compareTeams.reverse(), axisLabel: { color: ink }, axisLine: { lineStyle: { color: rule } } },
    series: [
      { name: '攻击力', type: 'bar', data: compareAttack.reverse(), itemStyle: { color: accent, borderRadius: [0,4,4,0] }, barWidth: '35%' },
      { name: '防守稳定性', type: 'bar', data: compareDefense.reverse(), itemStyle: { color: accent3, borderRadius: [0,4,4,0] }, barWidth: '35%' }
    ]
  });
  window.addEventListener('resize', function() { chart2.resize(); });

  // --- Chart 3: 胜率分布 ---
  var chart3 = echarts.init(document.getElementById('chart-champion'), null, { renderer: 'svg' });
  var rateTeams = ["Aston Villa", "Man United", "Bayern Munich", "Freiburg", "Union Berlin", "Stuttgart", "Dortmund", "Villarreal", "Roma", "Inter"];
  var winRates = [50.0, 52.6, 82.39999999999999, 38.2, 29.4, 52.900000000000006, 64.7, 57.9, 60.5, 71.1];
  var drawRates = [21.099999999999998, 28.9, 14.7, 23.5, 26.5, 23.5, 20.599999999999998, 15.8, 10.5, 15.8];
  var lossRates = [28.9, 18.4, 2.9000000000000004, 38.2, 44.1, 23.5, 14.7, 26.3, 28.9, 13.200000000000001];

  chart3.setOption({
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    legend: { data: ['胜率', '平局率', '负率'], textStyle: { color: muted }, top: 0 },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true },
    xAxis: { type: 'category', data: rateTeams, axisLabel: { color: muted, rotate: 25 }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', name: '比率 %', nameTextStyle: { color: muted }, axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '胜率', type: 'bar', stack: 'total', data: winRates, itemStyle: { color: accent } },
      { name: '平局率', type: 'bar', stack: 'total', data: drawRates, itemStyle: { color: gold } },
      { name: '负率', type: 'bar', stack: 'total', data: lossRates, itemStyle: { color: accent2 } }
    ]
  });
  window.addEventListener('resize', function() { chart3.resize(); });

  // --- Chart 4: 预期进球与控球率 ---
  var chart4 = echarts.init(document.getElementById('chart-xg-breakdown'), null, { renderer: 'svg' });
  var xgTeams = ["Aston Villa", "Man United", "Bayern Munich", "Freiburg", "Union Berlin", "Stuttgart", "Dortmund", "Villarreal", "Roma", "Inter"];
  var xgf = [2.19, 2.25, 3.47, 2.13, 2.48, 2.35, 1.95, 2.43, 2.34, 2.4];
  var xga = [1.62, 0.87, 1.36, 1.81, 1.3, 1.84, 0.85, 1.49, 0.53, 1.46];

  chart4.setOption({
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    legend: { data: ['场均进球', '场均失球'], textStyle: { color: muted }, top: 0 },
    grid: { left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true },
    xAxis: { type: 'category', data: xgTeams, axisLabel: { color: muted, rotate: 30 }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', name: '进球数', nameTextStyle: { color: muted }, axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    series: [
      { name: '场均进球', type: 'bar', data: xgf, itemStyle: { color: accent }, barWidth: '35%', label: { show: true, position: 'top', color: accent, fontSize: 10 } },
      { name: '场均失球', type: 'bar', data: xga, itemStyle: { color: accent2 }, barWidth: '35%', label: { show: true, position: 'top', color: accent2, fontSize: 10 } }
    ]
  });
  window.addEventListener('resize', function() { chart4.resize(); });

})();
