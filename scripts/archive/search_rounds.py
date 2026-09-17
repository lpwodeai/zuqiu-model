import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
data_dir = BASE_DIR / "data"
round_counts = {}

for filename in os.listdir(data_dir):
    if filename.startswith('import_') and filename.endswith('.py'):
        filepath = os.path.join(data_dir, filename)
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                rounds = re.findall(r"'round': (\d+)", content)
                for r in rounds:
                    round_counts[int(r)] = round_counts.get(int(r), 0) + 1
        except Exception as e:
            print(f'Error reading {filename}: {e}')

print('各导入脚本中的轮次分布:')
for k, v in sorted(round_counts.items()):
    print(f'第{k}轮: {v}场')
print(f'\n第27轮场数: {round_counts.get(27, 0)}')
