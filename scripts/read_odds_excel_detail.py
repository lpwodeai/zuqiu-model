import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
excel_path = BASE_DIR / "2025-2026 英超 .xlsx"

df = pd.read_excel(excel_path, sheet_name=0)
print(f"完整数据（第1场比赛）:")
print(df.to_string())
