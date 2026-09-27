# ⚠️ 状态说明（C-20260927-009 实测）：本脚本直连 data/odds_timing.db。该库是
# data_store.ingest_odds_timing_txt 的按需 TXT 导入通道，当前为 0 字节空库（2026-09-25 清理后未再导入），
# 因此本脚本会显示 Tables (0) 且 wdl_timing/match_results/match_mapping 报 no such table——
# 这不是数据丢失；时序赔率现网存量位于 odds.db 的 *_history 表（wdl_history 等，12881 场实测）。
# 核验时序数据请改用 validation/check_timing_schema.py（实测有效）或直接查 odds.db。
import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
db_path = BASE_DIR / "data" / "odds_timing.db"
print(f"File exists: {os.path.exists(db_path)}")
print(f"File size: {os.path.getsize(db_path) / 1024 / 1024:.1f} MB")

db = sqlite3.connect(db_path)
c = db.cursor()

# List all tables
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
print(f"\nTables ({len(tables)}):")
for t in tables:
    c2 = db.cursor()
    cnt = c2.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
    print(f"  {t[0]}: {cnt} rows")

# Check wdl_timing specifically
try:
    cnt = c.execute("SELECT COUNT(*) FROM wdl_timing").fetchone()[0]
    print(f"\nwdl_timing rows: {cnt}")
except Exception as e:
    print(f"\nwdl_timing: {e}")

# Check match_results for timing data
try:
    cnt = c.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
    print(f"match_results rows: {cnt}")
except Exception as e:
    print(f"match_results: {e}")

# Check match_mapping
try:
    cnt = c.execute("SELECT COUNT(*) FROM match_mapping").fetchone()[0]
    print(f"match_mapping rows: {cnt}")
except Exception as e:
    print(f"match_mapping: {e}")

db.close()