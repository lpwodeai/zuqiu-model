import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FILE_PATH = BASE_DIR / "assets" / "model-engine.js"

def read_file():
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(content):
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

def final_cleanup():
    content = read_file()
    original_size = len(content)
    
    print("📊 开始最终清理...")
    
    # 1. 替换FIFA系列赛为国际友谊赛
    print("\n🔍 清理 FIFA系列赛引用...")
    content = content.replace("'FIFA系列赛'", "'国际友谊赛'")
    content = content.replace('"FIFA系列赛"', '"国际友谊赛"')
    print("✅ FIFA系列赛引用已替换")
    
    # 2. 删除DATA_QUALITY_CONFIG中的FIFA数据源
    content = content.replace("'FIFA', ", "")
    
    # 3. 删除TACTICAL_DATA中的国家队数据
    print("\n🔍 清理 TACTICAL_DATA 中的国家队数据...")
    national_teams_pattern = r",\s*(switzerland|qatar|costa_rica|panama|jamaica|canada|bosnia|mexico|southafrica|southkorea|czechia|usa|paraguay|australia|turkey|iran|saudiarabia|serbia|denmark|wales|poland|ecuador|tunisia|morocco|cameroon|ghana|senegal|peru|newzealand|ukraine|china|uzbekistan|honduras|venezuela|bolivia|iceland|nigeria|egypt|algeria): \{[^}]+\}"
    content = re.sub(national_teams_pattern, '', content)
    print("✅ TACTICAL_DATA 已清理")
    
    # 4. 删除ELO_RATINGS中的国家队数据
    print("\n🔍 清理 ELO_RATINGS 中的国家队数据...")
    elo_pattern = r",\s*(switzerland|qatar|costa_rica|panama|jamaica|canada|bosnia|mexico|southafrica|southkorea|czechia|usa|paraguay|australia|turkey|iran|saudiarabia|serbia|denmark|wales|poland|ecuador|tunisia|morocco|cameroon|ghana|senegal|peru|newzealand|ukraine|china|uzbekistan|honduras|venezuela|bolivia|iceland|nigeria|egypt|algeria|jordan|curacao|capeverde|iraq|haiti):\s*\d+"
    content = re.sub(elo_pattern, '', content)
    print("✅ ELO_RATINGS 已清理")
    
    # 5. 删除VENUE_ENVIRONMENT中的世界杯场地数据
    print("\n🔍 清理 VENUE_ENVIRONMENT 中的世界杯场地...")
    venue_pattern = r",\s*(azteca|akron|canada|usa_east|usa_west): \{[^}]+\}"
    content = re.sub(venue_pattern, '', content)
    print("✅ VENUE_ENVIRONMENT 已清理")
    
    # 6. 删除COMPLETED_MATCH_ARCHIVE中的国家队比赛
    print("\n🔍 清理 COMPLETED_MATCH_ARCHIVE 中的国家队比赛...")
    # 匹配国家队比赛对象
    match_archive_pattern = r"\{\s*matchId:\s*'[^']*canada[^']*'[^}]+\},\s*"
    content = re.sub(match_archive_pattern, '', content)
    match_archive_pattern = r"\{\s*matchId:\s*'[^']*qatar[^']*'[^}]+\},\s*"
    content = re.sub(match_archive_pattern, '', content)
    match_archive_pattern = r"\{\s*matchId:\s*'[^']*switzerland[^']*'[^}]+\},\s*"
    content = re.sub(match_archive_pattern, '', content)
    match_archive_pattern = r"\{\s*matchId:\s*'[^']*bosnia[^']*'[^}]+\},\s*"
    content = re.sub(match_archive_pattern, '', content)
    match_archive_pattern = r"\{\s*matchId:\s*'[^']*mexico[^']*'[^}]+\},\s*"
    content = re.sub(match_archive_pattern, '', content)
    match_archive_pattern = r"\{\s*matchId:\s*'[^']*southafrica[^']*'[^}]+\},\s*"
    content = re.sub(match_archive_pattern, '', content)
    match_archive_pattern = r"\{\s*matchId:\s*'[^']*czechia[^']*'[^}]+\},\s*"
    content = re.sub(match_archive_pattern, '', content)
    print("✅ COMPLETED_MATCH_ARCHIVE 已清理")
    
    # 7. 清理空行
    content = re.sub(r'\n{4,}', '\n\n\n', content)
    
    write_file(content)
    
    final_size = len(content)
    removed_size = original_size - final_size
    print(f"\n📊 最终清理完成!")
    print(f"   - 原始大小: {original_size:,} 字符")
    print(f"   - 清理后大小: {final_size:,} 字符")
    print(f"   - 移除内容: {removed_size:,} 字符 ({removed_size/original_size*100:.1f}%)")
    
    return content

def verify_cleanup(content):
    print("\n🔍 最终验证...")
    
    issues = []
    
    # 检查世界杯关键词
    worldcup_patterns = ['世界杯', 'World Cup', 'WorldCup', 'worldcup', 'WC2026', '2026世界杯']
    for pattern in worldcup_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            issues.append(f"❌ 仍存在 '{pattern}' 引用: {len(matches)} 处")
    
    # 检查模拟函数
    sim_funcs = ['simulateWorldCup', 'simulateGroup', 'simulateKnockout2026', 'simulatePenaltyShootout']
    for func in sim_funcs:
        if func in content:
            issues.append(f"❌ {func} 函数仍存在")
    
    # 检查空变量
    empty_vars = ['GROUPS', 'ROUND1_RESULTS', 'ROUND2_RESULTS', 'ROUND1_STANDINGS', 
                  'FIXTURES', 'VENUES', 'GROUP_VENUES', 'WEATHER']
    for var in empty_vars:
        match = re.search(r'var ' + var + r' = (\{[^}]*\})', content)
        if match and len(match.group(1)) > 5:
            issues.append(f"❌ {var} 变量未完全清理")
    
    if issues:
        print("\n⚠️ 发现以下问题:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("✅ 所有检查通过!")
        return True

def main():
    print("🚀 开始最终清理")
    content = final_cleanup()
    verify_cleanup(content)
    print("\n🎉 最终清理完成!")

if __name__ == '__main__':
    main()