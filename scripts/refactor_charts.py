import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEAM_ATTRS_PATH = BASE_DIR / "assets" / "team_attributes.json"
CHARTS_PATH = BASE_DIR / "assets" / "charts.js"

def load_team_data():
    """加载五大联赛球队属性数据"""
    with open(TEAM_ATTRS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_top_teams(team_data, n=12):
    """获取攻击力最强的前N支球队"""
    sorted_teams = sorted(team_data.items(), key=lambda x: x[1]['attack'], reverse=True)
    return sorted_teams[:n]

def get_defense_top(team_data, n=12):
    """获取防守力最强的前N支球队（防守值越小越好）"""
    sorted_teams = sorted(team_data.items(), key=lambda x: x[1]['defence'])
    return sorted_teams[:n]

def generate_charts_js():
    """生成新的charts.js文件"""
    team_data = load_team_data()
    
    # 获取数据
    top_attack = get_top_teams(team_data, 12)
    top_defense = get_defense_top(team_data, 12)
    
    # 构建图表数据
    attack_teams = [t[1]['name'] for t in top_attack]
    attack_values = [t[1]['attack'] for t in top_attack]
    
    defense_teams = [t[1]['name'] for t in top_defense]
    defense_values = [t[1]['defence'] for t in top_defense]
    
    # xG数据（使用avgGoals代替）
    xg_teams = attack_teams[:10]
    xgf_values = [t[1]['avgGoals'] for t in top_attack[:10]]
    xga_values = [t[1]['avgOppGoals'] for t in top_attack[:10]]
    
    # 胜率数据
    winrate_teams = [t[1]['name'] for t in top_attack[:10]]
    winrate_values = [t[1]['winRate'] * 100 for t in top_attack[:10]]
    drawrate_values = [t[1]['drawRate'] * 100 for t in top_attack[:10]]
    lossrate_values = [t[1]['lossRate'] * 100 for t in top_attack[:10]]
    
    # 控球率数据
    possession_teams = attack_teams[:8]
    possession_values = [t[1]['avgPossession'] for t in top_attack[:8]]
    shots_values = [t[1]['avgShots'] for t in top_attack[:8]]
    
    js_content = f'''(function() {{
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
  var chart1 = echarts.init(document.getElementById('chart-power-rank'), null, {{ renderer: 'svg' }});
  var attackTeams = {json.dumps(attack_teams)};
  var attackValues = {attack_values};

  chart1.setOption({{
    animation: false,
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }}, appendToBody: true }},
    legend: {{ data: ['攻击力'], textStyle: {{ color: muted }}, top: 0 }},
    grid: {{ left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true }},
    xAxis: {{ type: 'category', data: attackTeams, axisLabel: {{ color: muted, rotate: 30, fontSize: 11 }}, axisLine: {{ lineStyle: {{ color: rule }} }} }},
    yAxis: {{ type: 'value', name: '攻击力评分', nameTextStyle: {{ color: muted }}, axisLabel: {{ color: muted }}, splitLine: {{ lineStyle: {{ color: rule }} }} }},
    series: [
      {{ name: '攻击力', type: 'bar', data: attackValues, itemStyle: {{ color: accent }}, barWidth: '40%', label: {{ show: true, position: 'top', color: accent, fontSize: 10 }} }}
    ]
  }});
  window.addEventListener('resize', function() {{ chart1.resize(); }});

  // --- Chart 2: 攻防能力对比 ---
  var chart2 = echarts.init(document.getElementById('chart-group-xg'), null, {{ renderer: 'svg' }});
  var compareTeams = {json.dumps(defense_teams)};
  var compareAttack = {[t[1]['attack'] for t in top_defense]};
  var compareDefense = {[1.0 - t[1]['defence'] for t in top_defense]};

  chart2.setOption({{
    animation: false,
    tooltip: {{ trigger: 'axis', appendToBody: true }},
    legend: {{ data: ['攻击力', '防守稳定性'], textStyle: {{ color: muted }}, top: 0 }},
    grid: {{ left: '3%', right: '4%', bottom: '5%', top: '12%', containLabel: true }},
    xAxis: {{ type: 'value', axisLabel: {{ color: muted }}, splitLine: {{ lineStyle: {{ color: rule }} }} }},
    yAxis: {{ type: 'category', data: compareTeams.reverse(), axisLabel: {{ color: ink }}, axisLine: {{ lineStyle: {{ color: rule }} }} }},
    series: [
      {{ name: '攻击力', type: 'bar', data: compareAttack.reverse(), itemStyle: {{ color: accent, borderRadius: [0,4,4,0] }}, barWidth: '35%' }},
      {{ name: '防守稳定性', type: 'bar', data: compareDefense.reverse(), itemStyle: {{ color: accent3, borderRadius: [0,4,4,0] }}, barWidth: '35%' }}
    ]
  }});
  window.addEventListener('resize', function() {{ chart2.resize(); }});

  // --- Chart 3: 胜率分布 ---
  var chart3 = echarts.init(document.getElementById('chart-champion'), null, {{ renderer: 'svg' }});
  var rateTeams = {json.dumps(winrate_teams)};
  var winRates = {winrate_values};
  var drawRates = {drawrate_values};
  var lossRates = {lossrate_values};

  chart3.setOption({{
    animation: false,
    tooltip: {{ trigger: 'axis', appendToBody: true }},
    legend: {{ data: ['胜率', '平局率', '负率'], textStyle: {{ color: muted }}, top: 0 }},
    grid: {{ left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true }},
    xAxis: {{ type: 'category', data: rateTeams, axisLabel: {{ color: muted, rotate: 25 }}, axisLine: {{ lineStyle: {{ color: rule }} }} }},
    yAxis: {{ type: 'value', name: '比率 %', nameTextStyle: {{ color: muted }}, axisLabel: {{ color: muted }}, splitLine: {{ lineStyle: {{ color: rule }} }} }},
    series: [
      {{ name: '胜率', type: 'bar', stack: 'total', data: winRates, itemStyle: {{ color: accent }} }},
      {{ name: '平局率', type: 'bar', stack: 'total', data: drawRates, itemStyle: {{ color: gold }} }},
      {{ name: '负率', type: 'bar', stack: 'total', data: lossRates, itemStyle: {{ color: accent2 }} }}
    ]
  }});
  window.addEventListener('resize', function() {{ chart3.resize(); }});

  // --- Chart 4: 预期进球与控球率 ---
  var chart4 = echarts.init(document.getElementById('chart-xg-breakdown'), null, {{ renderer: 'svg' }});
  var xgTeams = {json.dumps(xg_teams)};
  var xgf = {xgf_values};
  var xga = {xga_values};

  chart4.setOption({{
    animation: false,
    tooltip: {{ trigger: 'axis', appendToBody: true }},
    legend: {{ data: ['场均进球', '场均失球'], textStyle: {{ color: muted }}, top: 0 }},
    grid: {{ left: '3%', right: '4%', bottom: '10%', top: '12%', containLabel: true }},
    xAxis: {{ type: 'category', data: xgTeams, axisLabel: {{ color: muted, rotate: 30 }}, axisLine: {{ lineStyle: {{ color: rule }} }} }},
    yAxis: {{ type: 'value', name: '进球数', nameTextStyle: {{ color: muted }}, axisLabel: {{ color: muted }}, splitLine: {{ lineStyle: {{ color: rule }} }} }},
    series: [
      {{ name: '场均进球', type: 'bar', data: xgf, itemStyle: {{ color: accent }}, barWidth: '35%', label: {{ show: true, position: 'top', color: accent, fontSize: 10 }} }},
      {{ name: '场均失球', type: 'bar', data: xga, itemStyle: {{ color: accent2 }}, barWidth: '35%', label: {{ show: true, position: 'top', color: accent2, fontSize: 10 }} }}
    ]
  }});
  window.addEventListener('resize', function() {{ chart4.resize(); }});

}})();
'''
    
    with open(CHARTS_PATH, 'w', encoding='utf-8') as f:
        f.write(js_content)
    
    print("✅ charts.js 已成功重构")
    print(f"   - 使用 {len(team_data)} 支五大联赛球队数据")
    print(f"   - 图表1: 攻击力排行榜 (Top 12)")
    print(f"   - 图表2: 攻防能力对比 (Top 12)")
    print(f"   - 图表3: 胜率分布 (Top 10)")
    print(f"   - 图表4: 预期进球与控球率 (Top 10)")

def verify_charts_js():
    """验证charts.js内容"""
    with open(CHARTS_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    
    issues = []
    
    # 检查世界杯关键词
    worldcup_patterns = ['世界杯', 'World Cup', 'WorldCup', 'worldcup', 'WC2026', '2026世界杯']
    for pattern in worldcup_patterns:
        if pattern in content:
            issues.append(f"❌ 仍存在 '{pattern}' 引用")
    
    # 检查国家队名称
    national_teams = ['西班牙', '法国', '德国', '葡萄牙', '英格兰', '阿根廷', '巴西', '哥伦比亚', '比利时', '挪威', '克罗地亚', '瑞士', '奥地利', '土耳其', '摩洛哥', '日本', '美国', '墨西哥', '荷兰']
    for team in national_teams:
        if team in content:
            issues.append(f"❌ 仍存在国家队 '{team}'")
    
    # 检查世界杯术语
    wc_terms = ['小组赛', '夺冠', '第1轮', '第2轮', '第3轮']
    for term in wc_terms:
        if term in content:
            issues.append(f"❌ 仍存在世界杯术语 '{term}'")
    
    # 检查五大联赛俱乐部名称
    club_teams = ['Liverpool', 'Aston Villa', 'Brighton', 'Arsenal', 'Man United', 'Chelsea', 'Tottenham']
    found_clubs = sum(1 for club in club_teams if club in content)
    if found_clubs == 0:
        issues.append("❌ 未找到五大联赛俱乐部数据")
    
    if issues:
        print("\n⚠️ 发现以下问题:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("\n✅ 所有检查通过!")
        return True

def main():
    print("🚀 开始重构 charts.js")
    generate_charts_js()
    verify_charts_js()
    print("\n🎉 重构完成!")

if __name__ == '__main__':
    main()