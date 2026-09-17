import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FILE_PATH = BASE_DIR / "assets" / "predictor.js"

def read_file():
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(content):
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

def clean_predictor():
    content = read_file()
    original_size = len(content)
    print(f"📊 原始文件大小: {original_size:,} 字符")
    
    # 1. 修改文件标题
    print("\n🔍 修改文件标题...")
    content = content.replace(
        '/* ============================================================\n * 2026 World Cup Predictor UI - v3.6\n * 6模型集成(泊松+DC+SSM+XGBoost训练+LightGBM训练+Elo) + 正交因子 + 正则化钳制\n * 市场偏差分析 + 风险/组合/校准模块 + 动态权重显示\n * ============================================================ */',
        '/* ============================================================\n * Five Leagues Predictor UI - v3.6\n * 6模型集成(泊松+DC+SSM+XGBoost训练+LightGBM训练+Elo) + 正交因子 + 正则化钳制\n * 市场偏差分析 + 风险/组合/校准模块 + 动态权重显示\n * ============================================================ */'
    )
    print("✅ 文件标题已修改")
    
    # 2. 修改依赖从WC2026改为FiveLeagues
    print("\n🔍 修改依赖引用...")
    content = content.replace('var TEAMS = WC2026.TEAMS;', 'var TEAMS = FiveLeagues.TEAMS;')
    content = content.replace('var GROUPS = WC2026.GROUPS;', 'var GROUPS = FiveLeagues.GROUPS;')
    content = content.replace('var GROUP_VENUES = WC2026.GROUP_VENUES;', 'var GROUP_VENUES = FiveLeagues.GROUP_VENUES;')
    content = content.replace('var VENUES = WC2026.VENUES;', 'var VENUES = FiveLeagues.VENUES;')
    content = content.replace('var WEATHER = WC2026.WEATHER;', 'var WEATHER = FiveLeagues.WEATHER;')
    content = content.replace('var TACTICAL_MATCHUP = WC2026.TACTICAL_MATCHUP;', 'var TACTICAL_MATCHUP = FiveLeagues.TACTICAL_MATCHUP;')
    content = content.replace('var ELO_RATINGS = WC2026.ELO_RATINGS;', 'var ELO_RATINGS = FiveLeagues.ELO_RATINGS;')
    
    # 修改函数调用
    content = content.replace('WC2026.predictMatchDC(', 'FiveLeagues.predictMatchDC(')
    content = content.replace('WC2026.predictMatch(', 'FiveLeagues.predictMatch(')
    content = content.replace('WC2026.eloExpected(', 'FiveLeagues.eloExpected(')
    content = content.replace('WC2026.predictStacked(', 'FiveLeagues.predictStacked(')
    content = content.replace('WC2026.getModelPerformance(', 'FiveLeagues.getModelPerformance(')
    content = content.replace('WC2026.generatePreMatchReport(', 'FiveLeagues.generatePreMatchReport(')
    content = content.replace('WC2026.computeConfidenceInterval(', 'FiveLeagues.computeConfidenceInterval(')
    content = content.replace('WC2026.sensitivityAnalysis(', 'FiveLeagues.sensitivityAnalysis(')
    content = content.replace('WC2026.analyzeMarketEdge(', 'FiveLeagues.analyzeMarketEdge(')
    content = content.replace('WC2026.calibrateWithOdds(', 'FiveLeagues.calibrateWithOdds(')
    content = content.replace('WC2026.kellyCriterion(', 'FiveLeagues.kellyCriterion(')
    content = content.replace('WC2026.predictFullSlip(', 'FiveLeagues.predictFullSlip(')
    content = content.replace('WC2026.bivariatePoissonPMF(', 'FiveLeagues.bivariatePoissonPMF(')
    content = content.replace('WC2026.generateRiskReport(', 'FiveLeagues.generateRiskReport(')
    content = content.replace('WC2026.calculatePortfolioEdge(', 'FiveLeagues.calculatePortfolioEdge(')
    content = content.replace('WC2026.simulateWorldCup(', 'FiveLeagues.simulateWorldCup(')
    content = content.replace('WC2026.subscribeRealtime(', 'FiveLeagues.subscribeRealtime(')
    content = content.replace('WC2026.initRealtime(', 'FiveLeagues.initRealtime(')
    content = content.replace('WC2026.unsubscribeRealtime(', 'FiveLeagues.unsubscribeRealtime(')
    content = content.replace('WC2026.getMatch(', 'FiveLeagues.getMatch(')
    print("✅ 依赖引用已修改")
    
    # 3. 替换默认球队为五大联赛球队
    print("\n🔍 替换默认球队...")
    content = content.replace("buildTeamOptions('mexico')", "buildTeamOptions('pl_ars')")
    content = content.replace("buildTeamOptions('southafrica')", "buildTeamOptions('pl_mun')")
    print("✅ 默认球队已替换")
    
    # 4. 移除世界杯小组赛蒙特卡洛模拟功能
    print("\n🔍 移除世界杯小组赛蒙特卡洛模拟...")
    content = re.sub(r'// ─── 小组赛出线概率 \(蒙特卡洛\) ───[\s\S]*?document\.getElementById\(\'mcBtn\'\)\.addEventListener\(\'click\', runMonteCarlo\);\s*\n', '', content)
    print("✅ 蒙特卡洛模拟功能已移除")
    
    # 5. 移除初始化小组赛静态展示
    print("\n🔍 移除小组赛静态展示...")
    content = re.sub(r'// ─── 初始化小组赛静态展示 ───[\s\S]*?document\.getElementById\(\'groupGrid\'\)\.innerHTML = initGroupHtml;\s*\n', '', content)
    print("✅ 小组赛静态展示已移除")
    
    # 6. 清理实时数据引擎中的世界杯模拟数据
    print("\n🔍 清理实时数据引擎...")
    content = content.replace("simulateActiveMatch('AUS-TUR-20260614', '澳大利亚', '土耳其', '1-0', 75);", "")
    content = content.replace("simulateActiveMatch('CIV-ECU-20260615', '科特迪瓦', '厄瓜多尔', '1-0', 90);", "")
    print("✅ 实时数据引擎已清理")
    
    # 7. 移除世界杯相关注释和逻辑
    print("\n🔍 清理世界杯相关注释...")
    content = content.replace("// 世界杯小组赛除东道主外均为中立场地, sea_level视为中立", "// 联赛比赛中sea_level视为中立场地")
    content = re.sub(r'// v\d+\.\d+.*世界杯.*', '', content)
    print("✅ 世界杯相关注释已清理")
    
    # 8. 清理空行
    content = re.sub(r'\n{4,}', '\n\n\n', content)
    
    write_file(content)
    
    final_size = len(content)
    removed_size = original_size - final_size
    print(f"\n📊 清理完成!")
    print(f"   - 原始大小: {original_size:,} 字符")
    print(f"   - 清理后大小: {final_size:,} 字符")
    print(f"   - 移除内容: {removed_size:,} 字符 ({removed_size/original_size*100:.1f}%)")
    
    return content

def verify_cleanup(content):
    print("\n🔍 验证清理结果...")
    
    issues = []
    
    # 检查世界杯关键词
    worldcup_patterns = ['世界杯', 'World Cup', 'WorldCup', 'worldcup', 'WC2026', '2026世界杯']
    for pattern in worldcup_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            issues.append(f"❌ 仍存在 '{pattern}' 引用: {len(matches)} 处")
    
    # 检查WC2026引用
    if 'WC2026' in content:
        issues.append("❌ 仍存在 WC2026 引用")
    
    # 检查模拟函数
    if 'simulateWorldCup' in content:
        issues.append("❌ simulateWorldCup 调用仍存在")
    
    # 检查GROUPS和GROUP_VENUES使用
    if 'GROUPS' in content and 'groupGrid' in content:
        issues.append("❌ 小组赛相关代码仍存在")
    
    if issues:
        print("\n⚠️ 发现以下问题:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("✅ 所有检查通过!")
        return True

def main():
    print("🚀 开始清理 predictor.js 中的世界杯数据")
    
    content = clean_predictor()
    verify_cleanup(content)
    
    print("\n🎉 清理工作完成!")

if __name__ == '__main__':
    main()