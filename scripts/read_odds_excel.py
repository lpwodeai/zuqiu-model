import pandas as pd
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
excel_path = BASE_DIR / "2025-2026 英超 .xlsx"

print(f"读取Excel文件: {excel_path}")
print(f"文件存在: {os.path.exists(excel_path)}")

if os.path.exists(excel_path):
    xls = pd.ExcelFile(excel_path)
    print(f"\n工作表列表: {xls.sheet_names}")
    
    for sheet_name in xls.sheet_names:
        df = pd.read_excel(excel_path, sheet_name=sheet_name)
        print(f"\n=== 工作表: {sheet_name} ===")
        print(f"行数: {len(df)}, 列数: {len(df.columns)}")
        print(f"列名: {list(df.columns)}")
        print(f"\n数据预览:")
        print(df.head())
        print(f"\n数据类型:")
        print(df.dtypes)
