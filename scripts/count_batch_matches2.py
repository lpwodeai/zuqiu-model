"""统计batch_import.py中的英超比赛数量（更可靠的方式）"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def count_matches_in_batch_import(file_path):
    """统计batch_import.py中的比赛数量"""
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    match_count = 0
    round_stats = {}
    current_round = None
    
    for i, line in enumerate(lines):
        # 查找比赛定义的开始（以match_id开头的字典）
        if "'match_id':" in line and line.strip().startswith("'match_id':"):
            match_count += 1
        
        # 查找轮次信息
        if "'round':" in line:
            try:
                # 提取轮次数字
                parts = line.strip().split(':')
                if len(parts) >= 2:
                    rnd_val = parts[1].strip().rstrip(',')
                    current_round = int(rnd_val)
            except:
                pass
        
        # 统计轮次
        if "'match_id':" in line and current_round is not None:
            round_stats[current_round] = round_stats.get(current_round, 0) + 1
    
    return match_count, round_stats

if __name__ == '__main__':
    file_path = BASE_DIR / "data" / "batch_import.py"
    
    print(f'{"="*60}')
    print('【batch_import.py 英超比赛数量统计】')
    print(f'{"="*60}')
    
    count, round_stats = count_matches_in_batch_import(file_path)
    
    print(f'\n英超比赛总数: {count} 场')
    
    if round_stats:
        print('\n按轮次分布:')
        total_rounds = 0
        for rnd in sorted(round_stats.keys()):
            print(f'  第{rnd}轮: {round_stats[rnd]} 场')
            total_rounds += 1
        
        print(f'\n覆盖轮次数: {total_rounds} 轮')
        print(f'平均每轮比赛数: {count/total_rounds:.1f} 场')
    
    # 统计文件大小和行数
    import os
    file_size = os.path.getsize(file_path) / (1024 * 1024)
    
    print(f'\n文件信息:')
    print(f'  文件大小: {file_size:.2f} MB')
    print(f'  行数: {len(lines)} 行')
    
    print(f'\n{"="*60}')
