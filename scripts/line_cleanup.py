import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FILE_PATH = BASE_DIR / "assets" / "model-engine.js"

def read_lines():
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        return f.readlines()

def write_lines(lines):
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.writelines(lines)

def get_line_numbers(lines, patterns):
    """查找匹配模式的行号"""
    result = []
    for i, line in enumerate(lines, 1):
        for pattern in patterns:
            if pattern in line:
                result.append(i)
                break
    return result

def cleanup_by_line_numbers():
    lines = read_lines()
    original_size = sum(len(line) for line in lines)
    print(f"📊 原始文件大小: {original_size:,} 字符")
    print(f"📊 原始行数: {len(lines):,}")
    
    # 1. 删除 GROUPS 变量 (行 1410-1423)
    print("\n🔍 删除 GROUPS 变量 (1410-1423)...")
    lines = lines[:1409] + ['  var GROUPS = {};\n'] + lines[1424:]
    print(f"✅ GROUPS 已清理，当前行数: {len(lines):,}")
    
    # 2. 删除 ROUND1_RESULTS 变量 (行 ~1426-1487，需要重新计算)
    print("\n🔍 删除 ROUND1_RESULTS 变量...")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'var ROUND1_RESULTS = {' in line:
            start_idx = i
        elif start_idx is not None and '};' in line and 'ROUND1_RESULTS' in ''.join(lines[start_idx:i+1]):
            end_idx = i
            break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + ['  var ROUND1_RESULTS = {};\n'] + lines[end_idx+1:]
        print(f"✅ ROUND1_RESULTS 已清理，当前行数: {len(lines):,}")
    
    # 3. 删除 ROUND2_RESULTS 变量
    print("\n🔍 删除 ROUND2_RESULTS 变量...")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'var ROUND2_RESULTS = {' in line:
            start_idx = i
        elif start_idx is not None and '};' in line and i > start_idx + 20:
            end_idx = i
            break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + ['  var ROUND2_RESULTS = {};\n'] + lines[end_idx+1:]
        print(f"✅ ROUND2_RESULTS 已清理，当前行数: {len(lines):,}")
    
    # 4. 删除 ROUND1_STANDINGS 变量
    print("\n🔍 删除 ROUND1_STANDINGS 变量...")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'var ROUND1_STANDINGS = {' in line:
            start_idx = i
        elif start_idx is not None and '};' in line and i > start_idx + 10:
            end_idx = i
            break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + ['  var ROUND1_STANDINGS = {};\n'] + lines[end_idx+1:]
        print(f"✅ ROUND1_STANDINGS 已清理，当前行数: {len(lines):,}")
    
    # 5. 删除 FIXTURES 变量
    print("\n🔍 删除 FIXTURES 变量...")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'var FIXTURES = {};' in line:
            start_idx = i
            end_idx = i + 11
            break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + ['  var FIXTURES = {};\n'] + lines[end_idx:]
        print(f"✅ FIXTURES 已清理，当前行数: {len(lines):,}")
    
    # 6. 删除 VENUES 变量
    print("\n🔍 删除 VENUES 变量...")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'var VENUES = {' in line:
            start_idx = i
        elif start_idx is not None and '};' in line:
            end_idx = i
            break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + ['  var VENUES = {};\n'] + lines[end_idx+1:]
        print(f"✅ VENUES 已清理，当前行数: {len(lines):,}")
    
    # 7. 删除 GROUP_VENUES 变量
    print("\n🔍 删除 GROUP_VENUES 变量...")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'var GROUP_VENUES = {' in line:
            start_idx = i
        elif start_idx is not None and '};' in line:
            end_idx = i
            break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + ['  var GROUP_VENUES = {};\n'] + lines[end_idx+1:]
        print(f"✅ GROUP_VENUES 已清理，当前行数: {len(lines):,}")
    
    # 8. 删除 WEATHER 变量
    print("\n🔍 删除 WEATHER 变量...")
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if 'var WEATHER = {' in line:
            start_idx = i
        elif start_idx is not None and '};' in line:
            end_idx = i
            break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + ['  var WEATHER = {};\n'] + lines[end_idx+1:]
        print(f"✅ WEATHER 已清理，当前行数: {len(lines):,}")
    
    # 9. 删除世界杯模拟函数块
    print("\n🔍 删除世界杯模拟函数块...")
    start_idx = None
    end_idx = None
    func_count = 0
    brace_count = 0
    
    for i, line in enumerate(lines):
        if '// ─── 蒙特卡洛模拟整届世界杯 ───' in line:
            start_idx = i
        
        if start_idx is not None:
            brace_count += line.count('{') - line.count('}')
            
            if 'function ' in line:
                func_count += 1
            
            if func_count >= 6 and brace_count <= 0:
                end_idx = i
                break
    
    if start_idx is not None and end_idx is not None:
        lines = lines[:start_idx] + lines[end_idx+1:]
        print(f"✅ 世界杯模拟函数块已清理，当前行数: {len(lines):,}")
    
    write_lines(lines)
    
    final_size = sum(len(line) for line in lines)
    removed_size = original_size - final_size
    print(f"\n📊 清理完成!")
    print(f"   - 原始大小: {original_size:,} 字符")
    print(f"   - 清理后大小: {final_size:,} 字符")
    print(f"   - 移除内容: {removed_size:,} 字符 ({removed_size/original_size*100:.1f}%)")
    
    return lines

def verify_cleanup(lines):
    content = ''.join(lines)
    print("\n🔍 验证清理结果...")
    
    issues = []
    
    # 检查世界杯相关关键词
    worldcup_patterns = ['世界杯', 'World Cup', 'WorldCup', 'worldcup', 'WC2026', '2026世界杯']
    for pattern in worldcup_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            issues.append(f"❌ 仍存在 '{pattern}' 引用: {len(matches)} 处")
    
    # 检查变量是否为空
    empty_vars = ['GROUPS', 'ROUND1_RESULTS', 'ROUND2_RESULTS', 'ROUND1_STANDINGS', 
                  'FIXTURES', 'VENUES', 'GROUP_VENUES', 'WEATHER']
    for var in empty_vars:
        match = re.search(r'var ' + var + r' = (\{[^}]*\})', content)
        if match and len(match.group(1)) > 5:
            issues.append(f"❌ {var} 变量未完全清理")
    
    # 检查模拟函数是否存在
    sim_funcs = ['simulateWorldCup', 'simulateGroup', 'simulateKnockout2026', 'simulatePenaltyShootout']
    for func in sim_funcs:
        if func in content:
            issues.append(f"❌ {func} 函数仍存在")
    
    if issues:
        print("\n⚠️ 发现以下问题:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("✅ 所有检查通过!")
        return True

def post_cleanup(lines):
    """后期清理：替换FIFA系列赛等引用"""
    print("\n🔍 执行后期清理...")
    
    for i, line in enumerate(lines):
        # 替换FIFA系列赛
        line = line.replace("'FIFA系列赛'", "'国际友谊赛'")
        line = line.replace('"FIFA系列赛"', '"国际友谊赛"')
        
        # 替换版本号
        line = line.replace("'v4.7-wc2026-expanded'", "'v4.7'")
        
        # 替换伤病状态
        line = line.replace("'确认缺席本届世界杯'", "'确认缺席'")
        
        lines[i] = line
    
    # 删除DATA_QUALITY_CONFIG中的FIFA数据源
    for i, line in enumerate(lines):
        if "dataSources:" in line:
            lines[i] = line.replace("'FIFA', ", "")
    
    # 删除TACTICAL_DATA中的国家队数据
    content = ''.join(lines)
    
    # 删除国家队数据
    national_teams = ['mexico', 'southafrica', 'southkorea', 'czechia', 'canada', 
                      'bosnia', 'usa', 'paraguay']
    for team in national_teams:
        pattern = r',\s*' + team + r': \{[^}]+\}'
        content = re.sub(pattern, '', content)
    
    # 删除v4.3补充注释和后面的国家队数据
    content = re.sub(r"// ─── v4\.3补充: 2026世界杯48队技战术数据 ───[\s\S]*?\}\s*\];", 
                     "}];", content)
    
    # 删除MATCH_HISTORY中的世界杯比赛记录
    content = re.sub(r'// v7\.7: 融合巴西小组赛三场比赛xG数据.*?\n', '', content)
    content = re.sub(r'// 巴西 vs 摩洛.*?\n', '', content)
    content = re.sub(r'// 巴西 vs 海地.*?\n', '', content)
    
    # 删除simulateWorldCup导出
    content = content.replace(',\n    simulateWorldCup: simulateWorldCup', '')
    
    # 清理空行
    content = re.sub(r'\n{4,}', '\n\n\n', content)
    
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✅ 后期清理完成!")
    return content

def main():
    print("🚀 开始精确行号清理 model-engine.js 中的世界杯数据")
    
    lines = cleanup_by_line_numbers()
    post_cleanup(lines)
    
    # 重新读取验证
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    
    verify_cleanup([content])
    
    print("\n🎉 清理工作完成!")

if __name__ == '__main__':
    main()