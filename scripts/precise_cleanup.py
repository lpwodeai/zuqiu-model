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

def precise_cleanup():
    content = read_file()
    original_size = len(content)
    print(f"📊 原始文件大小: {original_size:,} 字符")
    
    # 1. 删除GROUPS变量 (第1410-1423行)
    print("\n🔍 清理 GROUPS 变量...")
    content = re.sub(r'var GROUPS = \{[\s\S]*?\n  \};', 'var GROUPS = {};', content)
    print("✅ GROUPS 已清理")
    
    # 2. 删除ROUND1_RESULTS变量
    print("\n🔍 清理 ROUND1_RESULTS 变量...")
    content = re.sub(r'var ROUND1_RESULTS = \{[\s\S]*?\n  \};', 'var ROUND1_RESULTS = {};', content)
    print("✅ ROUND1_RESULTS 已清理")
    
    # 3. 删除ROUND2_RESULTS变量 (更大的范围)
    print("\n🔍 清理 ROUND2_RESULTS 变量...")
    content = re.sub(r'// ─── 第二轮比赛结果 \(Round 2 Results\) ───\s*var ROUND2_RESULTS = \{[\s\S]*?^\s*\};', 
                     'var ROUND2_RESULTS = {};', content, flags=re.MULTILINE)
    print("✅ ROUND2_RESULTS 已清理")
    
    # 4. 删除GROUP_VENUES变量
    print("\n🔍 清理 GROUP_VENUES 变量...")
    content = re.sub(r'var GROUP_VENUES = \{[^}]+\};', 'var GROUP_VENUES = {};', content)
    print("✅ GROUP_VENUES 已清理")
    
    # 5. 删除VENUES变量
    print("\n🔍 清理 VENUES 变量...")
    content = re.sub(r'var VENUES = \{[\s\S]*?\n  \};', 'var VENUES = {};', content)
    print("✅ VENUES 已清理")
    
    # 6. 删除WEATHER变量
    print("\n🔍 清理 WEATHER 变量...")
    content = re.sub(r'var WEATHER = \{[^}]+\};', 'var WEATHER = {};', content)
    print("✅ WEATHER 已清理")
    
    # 7. 删除世界杯模拟函数块 (从"蒙特卡洛模拟整届世界杯"注释开始)
    print("\n🔍 清理世界杯模拟函数块...")
    content = re.sub(r'// ─── 蒙特卡洛模拟整届世界杯 ───[\s\S]*?function incCount\(obj, key\)', 
                     'function incCount(obj, key)', content)
    print("✅ 世界杯模拟函数块已清理")
    
    # 8. 删除toProbability函数前的空白
    content = re.sub(r'\n{3,}function toProbability', '\n\nfunction toProbability', content)
    
    # 9. 删除simulateWorldCup导出
    print("\n🔍 清理 simulateWorldCup 导出...")
    content = content.replace(',\n    simulateWorldCup: simulateWorldCup', '')
    print("✅ simulateWorldCup 导出已清理")
    
    # 10. 替换FIFA系列赛引用
    print("\n🔍 清理 FIFA系列赛引用...")
    content = content.replace("'FIFA系列赛'", "'国际友谊赛'")
    content = content.replace('"FIFA系列赛"', '"国际友谊赛"')
    print("✅ FIFA系列赛引用已替换")
    
    # 11. 删除v4.7-wc2026-expanded引用
    content = content.replace("'v4.7-wc2026-expanded'", "'v4.7'")
    
    # 12. 删除确认缺席本届世界杯引用
    content = content.replace("'确认缺席本届世界杯'", "'确认缺席'")
    
    # 13. 删除TACTICAL_DATA中的非五大联赛国家队数据
    print("\n🔍 清理 TACTICAL_DATA 中的国家队数据...")
    national_teams_pattern = r"(mexico: \{[^}]+\},|southafrica: \{[^}]+\},|southkorea: \{[^}]+\},|czechia: \{[^}]+\},|canada: \{[^}]+\},|bosnia: \{[^}]+\},|usa: \{[^}]+\},|paraguay: \{[^}]+\},)"
    content = re.sub(national_teams_pattern, '', content)
    
    # 删除v4.3补充注释和后面的国家队数据
    content = re.sub(r"// ─── v4\.3补充: 2026世界杯48队技战术数据 ───[\s\S]*?\}\s*\];", 
                     "}];", content)
    
    print("✅ TACTICAL_DATA 已清理")
    
    # 14. 删除MATCH_HISTORY中的世界杯比赛记录
    print("\n🔍 清理 MATCH_HISTORY 中的世界杯比赛...")
    content = re.sub(r'// v7\.7: 融合巴西小组赛三场比赛xG数据.*?\n', '', content)
    content = re.sub(r'// 巴西 vs 摩洛.*?\n', '', content)
    content = re.sub(r'// 巴西 vs 海地.*?\n', '', content)
    
    # 删除MATCH_HISTORY中的世界杯比赛对象
    content = re.sub(r'\{\s*matchId:\s*\'brazil-morocco-20260614\'[^}]+\},\s*\{\s*matchId:\s*\'brazil-haiti-20260620\'[^}]+\},', '', content)
    
    print("✅ MATCH_HISTORY 已清理")
    
    # 15. 删除DATA_QUALITY_CONFIG中的FIFA数据源
    content = content.replace("'FIFA', ", "")
    
    # 16. 清理空行
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
    
    # 检查世界杯相关关键词
    worldcup_patterns = ['世界杯', 'World Cup', 'WorldCup', 'worldcup', 'WC2026', '2026世界杯']
    for pattern in worldcup_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            issues.append(f"❌ 仍存在 '{pattern}' 引用: {len(matches)} 处")
    
    # 检查 GROUPS 是否为空
    groups_match = re.search(r'var GROUPS = (\{[^}]*\})', content)
    if groups_match and len(groups_match.group(1)) > 5:
        issues.append("❌ GROUPS 变量未完全清理")
    
    # 检查 ROUND1_RESULTS 是否为空
    round1_match = re.search(r'var ROUND1_RESULTS = (\{[^}]*\})', content)
    if round1_match and len(round1_match.group(1)) > 5:
        issues.append("❌ ROUND1_RESULTS 变量未完全清理")
    
    # 检查 ROUND2_RESULTS 是否为空
    round2_match = re.search(r'var ROUND2_RESULTS = (\{[^}]*\})', content)
    if round2_match and len(round2_match.group(1)) > 5:
        issues.append("❌ ROUND2_RESULTS 变量未完全清理")
    
    # 检查 GROUP_VENUES 是否为空
    venues_match = re.search(r'var GROUP_VENUES = (\{[^}]*\})', content)
    if venues_match and len(venues_match.group(1)) > 5:
        issues.append("❌ GROUP_VENUES 变量未完全清理")
    
    # 检查 WEATHER 是否为空
    weather_match = re.search(r'var WEATHER = (\{[^}]*\})', content)
    if weather_match and len(weather_match.group(1)) > 5:
        issues.append("❌ WEATHER 变量未完全清理")
    
    # 检查模拟函数是否存在
    sim_funcs = ['simulateWorldCup', 'simulateGroup', 'simulateKnockout2026', 'simulatePenaltyShootout']
    for func in sim_funcs:
        if func in content:
            issues.append(f"❌ {func} 函数仍存在")
    
    # 检查导出中的世界杯相关内容
    if 'simulateWorldCup' in content:
        issues.append("❌ simulateWorldCup 仍在代码中")
    
    if issues:
        print("\n⚠️ 发现以下问题:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("✅ 所有检查通过!")
        return True

def main():
    print("🚀 开始精确清理 model-engine.js 中的世界杯数据")
    
    content = precise_cleanup()
    verify_cleanup(content)
    
    print("\n🎉 清理工作完成!")

if __name__ == '__main__':
    main()