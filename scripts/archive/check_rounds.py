import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
content = open(BASE_DIR / "data" / "batch_import.py", 'r', encoding='utf-8').read()
rounds = re.findall(r"'round': (\d+)", content)
round_counts = {}
for r in rounds:
    round_counts[int(r)] = round_counts.get(int(r), 0) + 1
print('batch_import.py中的轮次分布:')
for k, v in sorted(round_counts.items()):
    print(f'第{k}轮: {v}场')
print(f'\n第27轮场数: {round_counts.get(27, 0)}')
