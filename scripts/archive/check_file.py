import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
file_path = BASE_DIR / "data" / "batch_import.py"
print(f'文件大小: {os.path.getsize(file_path)} 字节')

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()
    print(f'总行数: {len(lines)}')
    print('\n最后50行内容:')
    for i, line in enumerate(lines[-50:], start=len(lines)-49):
        print(f'{i:4d}: {line.rstrip()}')
