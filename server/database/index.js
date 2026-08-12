import Database from 'better-sqlite3';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DB_PATH = path.join(__dirname, '../../data/five_leagues.db');

export class AppDatabase {
  constructor() {
    this.db = null;
    this.isInitialized = false;
    this.statements = new Map();
  }

  connect() {
    try {
      const dir = path.dirname(DB_PATH);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }

      this.db = new Database(DB_PATH, { fileMustExist: false });

      this.db.pragma('journal_mode = WAL');
      this.db.pragma('foreign_keys = ON');
      this.db.pragma('synchronous = NORMAL');
      this.db.pragma('cache_size = -64000');
      // Cluster 模式下多 worker 并发写：busy_timeout 让写操作排队等待而非立即报错
      this.db.pragma('busy_timeout = 5000');

      console.log('✅ 数据库连接成功 (better-sqlite3)');
      return this.db;
    } catch (err) {
      console.error('❌ 数据库连接失败:', err.message);
      throw err;
    }
  }

  prepare(sql) {
    return this.db.prepare(sql);
  }

  run(sql, params = []) {
    try {
      const stmt = this.db.prepare(sql);
      const result = stmt.run(...(Array.isArray(params) ? params : [params]));
      return Promise.resolve({ lastID: result.lastInsertRowid, changes: result.changes });
    } catch (err) {
      console.error('数据库执行错误:', err.message, sql);
      return Promise.reject(err);
    }
  }

  get(sql, params = []) {
    try {
      const stmt = this.db.prepare(sql);
      return Promise.resolve(stmt.get(...(Array.isArray(params) ? params : [params])));
    } catch (err) {
      console.error('数据库查询错误:', err.message, sql);
      return Promise.reject(err);
    }
  }

  all(sql, params = []) {
    try {
      const stmt = this.db.prepare(sql);
      return Promise.resolve(stmt.all(...(Array.isArray(params) ? params : [params])));
    } catch (err) {
      console.error('数据库查询错误:', err.message, sql);
      return Promise.reject(err);
    }
  }

  transaction(fn) {
    return this.db.transaction(fn);
  }

  exec(sql) {
    try {
      this.db.exec(sql);
    } catch (err) {
      console.error('SQL执行错误:', err.message);
      throw err;
    }
  }

  close() {
    if (this.db) {
      this.db.close();
      this.db = null;
      this.isInitialized = false;
      console.log('✅ 数据库连接已关闭');
    }
  }

  async init() {
    this.createCompetitionsTable();
    this.createTeamsTable();
    this.createMatchesTable();
    this.createPlayersTable();
    this.createPlayerStatsTable();
    this.createLogsTable();
    this.createMatchReviewsTable();
    this.createModelVersionsTable();
    this.createPredictionHistoryTable();
    this.createIndexes();
    this.isInitialized = true;
    console.log('✅ 数据库表初始化完成');
  }

  createCompetitionsTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS competitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        code TEXT UNIQUE,
        country TEXT,
        season TEXT,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
        updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  createTeamsTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS teams (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        shortName TEXT,
        country TEXT,
        league TEXT,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
        updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  createMatchesTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        homeTeamId INTEGER,
        awayTeamId INTEGER,
        homeGoals INTEGER DEFAULT 0,
        awayGoals INTEGER DEFAULT 0,
        competitionId INTEGER,
        homeXg REAL,
        awayXg REAL,
        homeXgot REAL,
        awayXgot REAL,
        homeBigChances INTEGER DEFAULT 0,
        awayBigChances INTEGER DEFAULT 0,
        homeXa REAL,
        awayXa REAL,
        homeSaves INTEGER DEFAULT 0,
        awaySaves INTEGER DEFAULT 0,
        homeTouchesBox INTEGER DEFAULT 0,
        awayTouchesBox INTEGER DEFAULT 0,
        homeHitsPost INTEGER DEFAULT 0,
        awayHitsPost INTEGER DEFAULT 0,
        homeShots INTEGER DEFAULT 0,
        homeShotsOnTarget INTEGER DEFAULT 0,
        awayShots INTEGER DEFAULT 0,
        awayShotsOnTarget INTEGER DEFAULT 0,
        homePossession INTEGER,
        homeCorners INTEGER DEFAULT 0,
        awayCorners INTEGER DEFAULT 0,
        homeFouls INTEGER DEFAULT 0,
        awayFouls INTEGER DEFAULT 0,
        homeYellowCards INTEGER DEFAULT 0,
        awayYellowCards INTEGER DEFAULT 0,
        matchHash TEXT UNIQUE,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
        updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  createPlayersTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        nameEn TEXT,
        teamId INTEGER,
        position TEXT NOT NULL,
        positionGroup TEXT,
        age INTEGER,
        height REAL,
        weight REAL,
        nationality TEXT,
        jerseyNumber INTEGER,
        marketValue REAL,
        foot TEXT,
        isKeyPlayer BOOLEAN DEFAULT 0,
        lineupRole TEXT DEFAULT 'backup',
        playerStatus TEXT DEFAULT 'available',
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
        updatedAt TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (teamId) REFERENCES teams(id)
      )
    `);
  }

  createPlayerStatsTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS player_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        playerId INTEGER NOT NULL,
        season TEXT NOT NULL,
        matches INTEGER DEFAULT 0,
        starts INTEGER DEFAULT 0,
        minutes INTEGER DEFAULT 0,
        goals INTEGER DEFAULT 0,
        assists INTEGER DEFAULT 0,
        xg REAL DEFAULT 0,
        xA REAL DEFAULT 0,
        shots INTEGER DEFAULT 0,
        shotsOnTarget INTEGER DEFAULT 0,
        bigChances INTEGER DEFAULT 0,
        bigChancesCreated INTEGER DEFAULT 0,
        tackles INTEGER DEFAULT 0,
        interceptions INTEGER DEFAULT 0,
        blocks INTEGER DEFAULT 0,
        duelsWon INTEGER DEFAULT 0,
        aerialWon INTEGER DEFAULT 0,
        dribblesCompleted INTEGER DEFAULT 0,
        dribblesAttempted INTEGER DEFAULT 0,
        passes INTEGER DEFAULT 0,
        passesCompleted INTEGER DEFAULT 0,
        keyPasses INTEGER DEFAULT 0,
        throughBalls INTEGER DEFAULT 0,
        crosses INTEGER DEFAULT 0,
        fouls INTEGER DEFAULT 0,
        yellowCards INTEGER DEFAULT 0,
        redCards INTEGER DEFAULT 0,
        penaltyGoals INTEGER DEFAULT 0,
        penaltyMissed INTEGER DEFAULT 0,
        rating REAL DEFAULT 0,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
        updatedAt TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (playerId) REFERENCES players(id),
        UNIQUE (playerId, season)
      )
    `);
  }

  createLogsTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        level TEXT NOT NULL DEFAULT 'info',
        category TEXT NOT NULL,
        message TEXT NOT NULL,
        details TEXT,
        source TEXT,
        userId INTEGER,
        requestId TEXT,
        ipAddress TEXT,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  createMatchReviewsTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS match_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        matchId TEXT NOT NULL UNIQUE,
        homeTeam TEXT NOT NULL,
        awayTeam TEXT NOT NULL,
        homeGoals INTEGER NOT NULL DEFAULT 0,
        awayGoals INTEGER NOT NULL DEFAULT 0,
        result TEXT NOT NULL,
        predictedWinA REAL,
        predictedDraw REAL,
        predictedWinB REAL,
        modelVersion TEXT,
        brierScore REAL,
        logLoss REAL,
        accuracy BOOLEAN DEFAULT 0,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
        updatedAt TEXT DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  createModelVersionsTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS model_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version TEXT NOT NULL UNIQUE,
        description TEXT,
        trainedAt TEXT DEFAULT CURRENT_TIMESTAMP,
        trainingDataCount INTEGER DEFAULT 0,
        brierScore REAL,
        accuracy REAL,
        status TEXT DEFAULT 'active',
        deployed BOOLEAN DEFAULT 0,
        deployedAt TEXT,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  createPredictionHistoryTable() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS prediction_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        userId TEXT,
        homeTeam TEXT,
        awayTeam TEXT,
        league TEXT,
        winProb REAL,
        drawProb REAL,
        loseProb REAL,
        confidence REAL,
        predictionJson TEXT NOT NULL,
        modelVersion TEXT,
        createdAt TEXT DEFAULT CURRENT_TIMESTAMP
      )
    `);
  }

  createIndexes() {
    const indexes = [
      'CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date)',
      'CREATE INDEX IF NOT EXISTS idx_matches_home_team ON matches(homeTeamId)',
      'CREATE INDEX IF NOT EXISTS idx_matches_away_team ON matches(awayTeamId)',
      'CREATE INDEX IF NOT EXISTS idx_matches_competition ON matches(competitionId)',
      'CREATE INDEX IF NOT EXISTS idx_teams_name ON teams(name)',
      'CREATE INDEX IF NOT EXISTS idx_competitions_name ON competitions(name)',
      'CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp)',
      'CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level)',
      'CREATE INDEX IF NOT EXISTS idx_logs_category ON logs(category)',
      'CREATE INDEX IF NOT EXISTS idx_reviews_match_id ON match_reviews(matchId)',
      'CREATE INDEX IF NOT EXISTS idx_reviews_home_team ON match_reviews(homeTeam)',
      'CREATE INDEX IF NOT EXISTS idx_reviews_away_team ON match_reviews(awayTeam)',
      'CREATE INDEX IF NOT EXISTS idx_model_versions_version ON model_versions(version)',
      'CREATE INDEX IF NOT EXISTS idx_prediction_history_user ON prediction_history(userId, createdAt)'
    ];

    for (const sql of indexes) {
      this.db.exec(sql);
    }
  }

  getDatabaseStats() {
    const tables = this.db.prepare(
      "SELECT name FROM sqlite_master WHERE type='table'"
    ).all();
    
    const stats = {};
    for (const { name } of tables) {
      try {
        const count = this.db.prepare(`SELECT COUNT(*) as count FROM "${name}"`).get();
        stats[name] = count.count;
      } catch (e) {
        stats[name] = 'error';
      }
    }
    return stats;
  }
}

export const db = new AppDatabase();