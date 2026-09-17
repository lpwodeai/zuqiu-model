import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
FILE_PATH = BASE_DIR / "assets" / "main.js"

def read_file():
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        return f.read()

def write_file(content):
    with open(FILE_PATH, 'w', encoding='utf-8') as f:
        f.write(content)

def replace_worldcup_content():
    content = read_file()
    original_size = len(content)
    
    print("📊 原始文件大小: {} 字符".format(original_size))
    
    # 1. 替换模块名称 WC2026 -> FiveLeagues
    print("\n🔍 替换模块名称...")
    content = content.replace('var WC2026 = (function() {', 'var FiveLeagues = (function() {')
    content = content.replace('WC2026_MODULE_LOADER', 'FiveLeagues_MODULE_LOADER')
    content = content.replace('WC2026_CACHE', 'FiveLeagues_CACHE')
    content = content.replace('WC2026_STACKING', 'FiveLeagues_STACKING')
    content = content.replace('WC2026_INCREMENTAL_LEARNING', 'FiveLeagues_INCREMENTAL_LEARNING')
    content = content.replace('WC2026_BACKTEST', 'FiveLeagues_BACKTEST')
    content = content.replace('WC2026_TEAMS', 'FiveLeagues_TEAMS')
    content = content.replace('WC2026_SOCIAL', 'FiveLeagues_SOCIAL')
    print("✅ 模块名称已替换")
    
    # 2. 替换错误消息
    print("\n🔍 替换错误消息...")
    content = content.replace(
        "throw new Error('WC2026 not initialized. Call init() first.');",
        "throw new Error('FiveLeagues not initialized. Call init() first.');"
    )
    print("✅ 错误消息已替换")
    
    write_file(content)
    
    final_size = len(content)
    print(f"\n📊 替换完成!")
    print(f"   - 原始大小: {original_size:,} 字符")
    print(f"   - 替换后大小: {final_size:,} 字符")
    
    return content

def verify_replacement(content):
    print("\n🔍 验证替换结果...")
    
    issues = []
    
    # 检查是否还有WC2026引用
    if 'WC2026' in content:
        issues.append("❌ 仍存在 WC2026 引用")
    
    # 检查世界杯关键词
    worldcup_patterns = ['世界杯', 'World Cup', 'WorldCup', 'worldcup', 'WC2026', '2026世界杯']
    for pattern in worldcup_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            issues.append(f"❌ 仍存在 '{pattern}' 引用: {len(matches)} 处")
    
    # 检查新的模块名称是否正确
    expected_refs = ['FiveLeagues', 'FiveLeagues_MODULE_LOADER', 'FiveLeagues_CACHE', 
                     'FiveLeagues_STACKING', 'FiveLeagues_INCREMENTAL_LEARNING', 
                     'FiveLeagues_BACKTEST', 'FiveLeagues_TEAMS', 'FiveLeagues_SOCIAL']
    for ref in expected_refs:
        if ref not in content:
            issues.append(f"❌ 缺少预期的引用: {ref}")
    
    if issues:
        print("\n⚠️ 发现以下问题:")
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("\n✅ 所有检查通过!")
        return True

def main():
    print("🚀 开始替换 main.js 中的世界杯内容")
    
    content = replace_worldcup_content()
    verify_replacement(content)
    
    print("\n🎉 替换完成!")

if __name__ == '__main__':
    main()