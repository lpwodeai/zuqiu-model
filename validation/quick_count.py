import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
c = sqlite3.connect(BASE_DIR / "data" / "odds.db")
print(f"matches: {c.execute('SELECT COUNT(*) FROM matches').fetchone()[0]}")
print(f"fbref: {c.execute('SELECT COUNT(*) FROM fbref_match_mapping').fetchone()[0]}")
c.close()