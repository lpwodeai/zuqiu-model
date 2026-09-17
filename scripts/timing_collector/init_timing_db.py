"""
赔率时序数据收集器 - 数据库初始化脚本
用于接收外部系统打卡的赔率时序数据，进行标准化存储

数据库：odds_timing.db
特点：
1. 独立于现有odds.db，不影响主系统
2. 支持增量数据接收
3. 包含数据验证和质量标记
4. 支持多来源数据
"""

import sqlite3
import os

DB_PATH = 'data/odds_timing.db'
SCHEMA_VERSION = '1.0'

def init_database():
    """初始化时序数据收集数据库"""
    
    # 如果已存在，先备份
    if os.path.exists(DB_PATH):
        backup_path = DB_PATH.replace('.db', f'_backup_{os.path.getmtime(DB_PATH):.0f}.db')
        os.rename(DB_PATH, backup_path)
        print(f'已备份旧数据库: {backup_path}')
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 启用外键约束
    cursor.execute('PRAGMA foreign_keys = ON')
    
    # 1. matches表 - 比赛基础信息
    cursor.execute("""
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            match_date TEXT NOT NULL,
            match_time TEXT,
            league TEXT,
            league_code TEXT,
            status TEXT DEFAULT 'pending',  -- pending/finished/processed
            source TEXT NOT NULL,           -- 数据来源标识
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 2. wdl_timing表 - 胜平负赔率时序
    cursor.execute("""
        CREATE TABLE wdl_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            win_a REAL NOT NULL,    -- 主胜赔率
            draw REAL NOT NULL,     -- 平局赔率
            win_b REAL NOT NULL,    -- 客胜赔率
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,  -- 数据质量评分
            is_valid INTEGER DEFAULT 1,      -- 是否有效
            notes TEXT,                      -- 备注
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
            UNIQUE(match_id, timestamp, source)
        )
    """)
    
    # 3. handicap_timing表 - 让球赔率时序
    cursor.execute("""
        CREATE TABLE handicap_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            handicap REAL NOT NULL,  -- 让球数
            hcp_win REAL NOT NULL,   -- 让球胜
            hcp_draw REAL,           -- 让球平（亚盘可为空）
            hcp_lose REAL NOT NULL,  -- 让球负
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,
            is_valid INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
            UNIQUE(match_id, timestamp, source)
        )
    """)
    
    # 4. total_goals_timing表 - 总进球赔率时序
    cursor.execute("""
        CREATE TABLE total_goals_timing (
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
            over_25 REAL,     -- 大2.5球
            under_25 REAL,    -- 小2.5球
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,
            is_valid INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
            UNIQUE(match_id, timestamp, source)
        )
    """)
    
    # 5. score_timing表 - 比分赔率时序
    cursor.execute("""
        CREATE TABLE score_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            score TEXT NOT NULL,    -- 比分格式: X:Y
            odds REAL NOT NULL,
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,
            is_valid INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
            UNIQUE(match_id, timestamp, score, source)
        )
    """)
    
    # 6. match_results表 - 比赛结果（赛后补充）
    cursor.execute("""
        CREATE TABLE match_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE NOT NULL,
            actual_score TEXT,       -- 实际比分
            actual_wdl TEXT,         -- 胜平负结果
            actual_handicap TEXT,    -- 让球结果
            actual_total_goals INTEGER,  -- 总进球数
            source TEXT NOT NULL,
            verified INTEGER DEFAULT 0,   -- 是否已验证
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE
        )
    """)
    
    # 7. data_sources表 - 数据源管理
    cursor.execute("""
        CREATE TABLE data_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_code TEXT UNIQUE NOT NULL,
            source_name TEXT NOT NULL,
            description TEXT,
            enabled INTEGER DEFAULT 1,
            last_sync_time TEXT,
            total_records INTEGER DEFAULT 0,
            quality_rating REAL DEFAULT 0.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 8. import_log表 - 导入日志
    cursor.execute("""
        CREATE TABLE import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_time TEXT DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL,
            total_matches INTEGER DEFAULT 0,
            wdl_records INTEGER DEFAULT 0,
            hcp_records INTEGER DEFAULT 0,
            tg_records INTEGER DEFAULT 0,
            score_records INTEGER DEFAULT 0,
            status TEXT DEFAULT 'processing',  -- processing/success/failed
            error_message TEXT,
            duration REAL
        )
    """)
    
    # 创建索引
    cursor.execute('CREATE INDEX idx_wdl_match_ts ON wdl_timing(match_id, timestamp)')
    cursor.execute('CREATE INDEX idx_hcp_match_ts ON handicap_timing(match_id, timestamp)')
    cursor.execute('CREATE INDEX idx_tg_match_ts ON total_goals_timing(match_id, timestamp)')
    cursor.execute('CREATE INDEX idx_score_match_ts ON score_timing(match_id, timestamp)')
    cursor.execute('CREATE INDEX idx_matches_status ON matches(status)')
    cursor.execute('CREATE INDEX idx_matches_source ON matches(source)')
    
    # 插入默认数据源
    default_sources = [
        ('EXTERNAL_SYSTEM', '外部数据收集系统', '通过HTTP API接收的外部系统打卡数据', 1, None, 0, 0.0),
        ('MANUAL_IMPORT', '手动导入', '通过CSV/Excel手动导入的数据', 1, None, 0, 0.0),
        ('SCRAPER', '爬虫采集', '通过爬虫采集的数据', 1, None, 0, 0.0),
    ]
    cursor.executemany("""
        INSERT INTO data_sources (source_code, source_name, description, enabled, last_sync_time, total_records, quality_rating)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, default_sources)
    
    conn.commit()
    conn.close()
    
    print(f'\n{"="*60}')
    print('数据库初始化完成!')
    print(f'数据库文件: {DB_PATH}')
    print(f'版本: {SCHEMA_VERSION}')
    print(f'表数量: 8个')
    print(f'{"="*60}')

if __name__ == '__main__':
    init_database()
