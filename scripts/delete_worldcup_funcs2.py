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

def delete_worldcup_functions():
    content = read_file()
    
    # 找到 simulateWorldCup 函数开始位置
    start_pattern = r'// ─── 蒙特卡洛模拟整届世界.*?───\s*function simulateWorldCup'
    start_match = re.search(start_pattern, content)
    
    if start_match:
        start_idx = start_match.start()
        print(f"🔍 找到开始位置: {start_idx}")
    else:
        print("❌ 未找到开始位置")
        return
    
    # 找到 incCount 函数开始位置
    end_pattern = r'\s*function incCount\(obj, key\)'
    end_match = re.search(end_pattern, content[start_idx:])
    
    if end_match:
        end_idx = start_idx + end_match.start()
        print(f"🔍 找到结束位置: {end_idx}")
    else:
        print("❌ 未找到结束位置")
        return
    
    # 删除这段代码
    new_content = content[:start_idx] + content[end_idx:]
    write_file(new_content)
    
    # 验证
    sim_funcs = ['simulateWorldCup', 'runSingleSimulation', 'simulateKnockout2026', 
                 'simulateKnockoutPairwise', 'simulateGroup', 'simulatePenaltyShootout']
    
    remaining = []
    for func in sim_funcs:
        if func in new_content:
            remaining.append(func)
    
    if remaining:
        print(f"⚠️ 仍存在以下函数: {remaining}")
    else:
        print("✅ 所有世界杯模拟函数已删除")
    
    return new_content

def main():
    print("🚀 删除世界杯模拟函数")
    delete_worldcup_functions()
    print("🎉 删除完成!")

if __name__ == '__main__':
    main()