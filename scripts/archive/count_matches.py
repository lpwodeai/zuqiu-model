import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
content = open(BASE_DIR / "data" / "batch_import.py", 'r', encoding='utf-8').read()
count = len(re.findall(r"'match_id':", content))
print(f'Total match_id entries: {count}')
