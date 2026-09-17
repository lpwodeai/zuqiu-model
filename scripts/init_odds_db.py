import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

def init_odds_database():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    sql = """
        CREATE TABLE IF NOT EXISTS matches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id TEXT UNIQUE NOT NULL,
          home_team TEXT NOT NULL,
          away_team TEXT NOT NULL,
          match_date TEXT NOT NULL,
          match_type TEXT NOT NULL,
          handicap REAL,
          actual_wdl TEXT,
          actual_handicap TEXT,
          actual_score TEXT,
          actual_total_goals INTEGER,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS wdl_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          win_a REAL NOT NULL,
          draw REAL NOT NULL,
          win_b REAL NOT NULL,
          FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
          UNIQUE(match_id, timestamp)
        );

        CREATE TABLE IF NOT EXISTS handicap_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          hcp_win REAL NOT NULL,
          hcp_draw REAL NOT NULL,
          hcp_lose REAL NOT NULL,
          FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
          UNIQUE(match_id, timestamp)
        );

        CREATE TABLE IF NOT EXISTS total_goals_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          goals_0 REAL,
          goals_1 REAL,
          goals_2 REAL,
          goals_3 REAL,
          goals_4 REAL,
          goals_5 REAL,
          goals_6 REAL,
          goals_7_plus REAL,
          FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
          UNIQUE(match_id, timestamp)
        );

        CREATE TABLE IF NOT EXISTS score_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id TEXT NOT NULL,
          timestamp TEXT NOT NULL,
          score TEXT NOT NULL,
          odds REAL NOT NULL,
          FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
          UNIQUE(match_id, timestamp, score)
        );

        CREATE TABLE IF NOT EXISTS features (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id TEXT NOT NULL,
          feature_name TEXT NOT NULL,
          feature_value REAL NOT NULL,
          FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
          UNIQUE(match_id, feature_name)
        );

        CREATE TABLE IF NOT EXISTS model_predictions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          match_id TEXT NOT NULL,
          model_name TEXT NOT NULL,
          prediction_type TEXT NOT NULL,
          prediction TEXT NOT NULL,
          probability REAL,
          confidence REAL,
          timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
          input_snapshot_json TEXT,
          feature_version TEXT,
          config_version TEXT,
          model_version TEXT,
          FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
          UNIQUE(match_id, model_name, prediction_type)
        );

        CREATE TABLE IF NOT EXISTS model_performance (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          model_name TEXT NOT NULL,
          evaluation_date TEXT NOT NULL,
          metric_name TEXT NOT NULL,
          metric_value REAL NOT NULL,
          sample_size INTEGER,
          notes TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_wdl_match_id ON wdl_history(match_id);
        CREATE INDEX IF NOT EXISTS idx_hcp_match_id ON handicap_history(match_id);
        CREATE INDEX IF NOT EXISTS idx_tg_match_id ON total_goals_history(match_id);
        CREATE INDEX IF NOT EXISTS idx_score_match_id ON score_history(match_id);
    """
    
    cursor.executescript(sql)
    conn.commit()

    # P0-06: 旧库迁移——为 model_predictions 补充溯源字段（幂等，已有则跳过）
    cols = {r[1] for r in cursor.execute("PRAGMA table_info(model_predictions)")}
    new_cols = [
        ("input_snapshot_json", "TEXT"),
        ("feature_version", "TEXT"),
        ("config_version", "TEXT"),
        ("model_version", "TEXT"),
    ]
    for name, typ in new_cols:
        if name not in cols:
            cursor.execute(f"ALTER TABLE model_predictions ADD COLUMN {name} {typ}")
            print(f"  + 迁移新增列: model_predictions.{name}")
    conn.commit()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    print(f"创建成功! 表数量: {len(tables)}")
    for table in tables:
        print(f"  - {table[0]}")
    
    conn.close()

if __name__ == "__main__":
    init_odds_database()
