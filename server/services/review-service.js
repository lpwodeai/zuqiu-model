import { db } from '../database/index.js';
import { logger } from './logger.js';
import { cacheService } from './cache-service.js';

export class ReviewService {
  constructor() {
    this.db = db;
  }

  async init() {
    await this.createReviewTable();
    await this.createModelVersionTable();
    console.log('✅ 复盘服务初始化完成');
  }

  async createReviewTable() {
    const sql = `
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
    `;
    await this.db.run(sql);
  }

  async createModelVersionTable() {
    const sql = `
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
    `;
    await this.db.run(sql);
  }

  validateReviewData(data) {
    const errors = [];
    
    if (!data.matchId || typeof data.matchId !== 'string') {
      errors.push('matchId必须是字符串');
    }
    
    if (!data.homeTeam || typeof data.homeTeam !== 'string') {
      errors.push('homeTeam必须是字符串');
    }
    
    if (!data.awayTeam || typeof data.awayTeam !== 'string') {
      errors.push('awayTeam必须是字符串');
    }
    
    if (typeof data.homeGoals !== 'number' || data.homeGoals < 0) {
      errors.push('homeGoals必须是非负整数');
    }
    
    if (typeof data.awayGoals !== 'number' || data.awayGoals < 0) {
      errors.push('awayGoals必须是非负整数');
    }
    
    if (data.predictedWinA !== undefined) {
      if (typeof data.predictedWinA !== 'number' || data.predictedWinA < 0 || data.predictedWinA > 1) {
        errors.push('predictedWinA必须在0到1之间');
      }
    }
    
    if (data.predictedDraw !== undefined) {
      if (typeof data.predictedDraw !== 'number' || data.predictedDraw < 0 || data.predictedDraw > 1) {
        errors.push('predictedDraw必须在0到1之间');
      }
    }
    
    if (data.predictedWinB !== undefined) {
      if (typeof data.predictedWinB !== 'number' || data.predictedWinB < 0 || data.predictedWinB > 1) {
        errors.push('predictedWinB必须在0到1之间');
      }
    }
    
    return {
      isValid: errors.length === 0,
      errors: errors
    };
  }

  async saveReview(reviewData) {
    const validation = this.validateReviewData(reviewData);
    
    if (!validation.isValid) {
      throw new Error(`数据验证失败: ${validation.errors.join(', ')}`);
    }

    const { matchId, homeTeam, awayTeam, homeGoals, awayGoals, 
            predictedWinA, predictedDraw, predictedWinB, modelVersion } = reviewData;

    let result;
    if (homeGoals > awayGoals) result = 'winA';
    else if (homeGoals < awayGoals) result = 'winB';
    else result = 'draw';

    let brierScore = null;
    let logLoss = null;
    let accuracy = false;

    if (predictedWinA !== undefined && predictedDraw !== undefined && predictedWinB !== undefined) {
      const actualWinA = result === 'winA' ? 1 : 0;
      const actualDraw = result === 'draw' ? 1 : 0;
      const actualWinB = result === 'winB' ? 1 : 0;

      brierScore = Math.pow(predictedWinA - actualWinA, 2) +
                   Math.pow(predictedDraw - actualDraw, 2) +
                   Math.pow(predictedWinB - actualWinB, 2);

      const eps = 0.0001;
      logLoss = -(actualWinA * Math.log(Math.max(eps, predictedWinA)) +
                  actualDraw * Math.log(Math.max(eps, predictedDraw)) +
                  actualWinB * Math.log(Math.max(eps, predictedWinB)));

      const maxProb = Math.max(predictedWinA, predictedDraw, predictedWinB);
      let predictedResult;
      if (predictedWinA === maxProb) predictedResult = 'winA';
      else if (predictedDraw === maxProb) predictedResult = 'draw';
      else predictedResult = 'winB';
      accuracy = predictedResult === result;
    }

    const sql = `
      INSERT OR REPLACE INTO match_reviews (
        matchId, homeTeam, awayTeam, homeGoals, awayGoals, result,
        predictedWinA, predictedDraw, predictedWinB, modelVersion,
        brierScore, logLoss, accuracy, updatedAt
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `;

    const params = [
      matchId, homeTeam, awayTeam, homeGoals, awayGoals, result,
      predictedWinA, predictedDraw, predictedWinB, modelVersion,
      brierScore, logLoss, accuracy, new Date().toISOString()
    ];

    try {
      const dbResult = await this.db.run(sql, params);
      logger.info('review', '复盘数据已保存', { matchId, homeTeam, awayTeam });
      
      await cacheService.invalidatePrediction(homeTeam, awayTeam);
      
      return { success: true, id: dbResult.lastID };
    } catch (err) {
      logger.error('review', '保存复盘数据失败', { matchId, error: err.message });
      throw err;
    }
  }

  async saveBatchReviews(reviews) {
    const results = [];
    
    for (const review of reviews) {
      try {
        const result = await this.saveReview(review);
        results.push({ ...result, matchId: review.matchId });
      } catch (err) {
        results.push({ success: false, matchId: review.matchId, error: err.message });
      }
    }
    
    const successCount = results.filter(r => r.success).length;
    const failCount = results.filter(r => !r.success).length;
    
    logger.info('review', '批量保存复盘数据完成', { total: reviews.length, success: successCount, fail: failCount });
    
    return {
      total: reviews.length,
      success: successCount,
      fail: failCount,
      results: results
    };
  }

  async getReview(matchId) {
    const sql = 'SELECT * FROM match_reviews WHERE matchId = ?';
    return await this.db.get(sql, [matchId]);
  }

  async getReviewsByTeam(teamName, limit = 20, offset = 0) {
    const sql = `
      SELECT * FROM match_reviews 
      WHERE homeTeam LIKE ? OR awayTeam LIKE ?
      ORDER BY createdAt DESC
      LIMIT ? OFFSET ?
    `;
    return await this.db.all(sql, [`%${teamName}%`, `%${teamName}%`, limit, offset]);
  }

  async getRecentReviews(limit = 50) {
    const sql = `
      SELECT * FROM match_reviews 
      ORDER BY createdAt DESC
      LIMIT ?
    `;
    return await this.db.all(sql, [limit]);
  }

  async getReviewStats() {
    const sql = `
      SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN accuracy = 1 THEN 1 ELSE 0 END) as correct,
        AVG(brierScore) as avgBrierScore,
        AVG(logLoss) as avgLogLoss,
        MIN(createdAt) as earliestDate,
        MAX(createdAt) as latestDate
      FROM match_reviews
      WHERE brierScore IS NOT NULL
    `;
    const stats = await this.db.get(sql);
    
    return {
      total: stats.total || 0,
      correct: stats.correct || 0,
      accuracy: stats.total > 0 ? ((stats.correct / stats.total) * 100).toFixed(2) : 0,
      avgBrierScore: stats.avgBrierScore ? stats.avgBrierScore.toFixed(4) : null,
      avgLogLoss: stats.avgLogLoss ? stats.avgLogLoss.toFixed(4) : null,
      earliestDate: stats.earliestDate,
      latestDate: stats.latestDate
    };
  }

  async saveModelVersion(versionData) {
    const { version, description, trainingDataCount, brierScore, accuracy, deployed = false } = versionData;
    
    const sql = `
      INSERT OR REPLACE INTO model_versions (
        version, description, trainingDataCount, brierScore, accuracy,
        status, deployed, deployedAt, updatedAt
      ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
    `;

    const params = [
      version, description, trainingDataCount, brierScore, accuracy,
      deployed ? 1 : 0, deployed ? new Date().toISOString() : null, new Date().toISOString()
    ];

    try {
      const result = await this.db.run(sql, params);
      logger.info('model', '模型版本已保存', { version, deployed });
      return { success: true, id: result.lastID };
    } catch (err) {
      logger.error('model', '保存模型版本失败', { version, error: err.message });
      throw err;
    }
  }

  async getModelVersions(limit = 10) {
    const sql = `
      SELECT * FROM model_versions
      ORDER BY trainedAt DESC
      LIMIT ?
    `;
    return await this.db.all(sql, [limit]);
  }

  async getLatestModelVersion() {
    const sql = `
      SELECT * FROM model_versions
      ORDER BY trainedAt DESC
      LIMIT 1
    `;
    return await this.db.get(sql);
  }

  async setModelDeployed(version, deployed) {
    const sql = `
      UPDATE model_versions 
      SET deployed = ?, deployedAt = ?, updatedAt = ?
      WHERE version = ?
    `;
    
    const params = [deployed ? 1 : 0, deployed ? new Date().toISOString() : null, new Date().toISOString(), version];
    
    try {
      await this.db.run(sql, params);
      logger.info('model', '模型部署状态已更新', { version, deployed });
      
      if (deployed) {
        await cacheService.invalidateAllPredictions();
      }
      
      return { success: true };
    } catch (err) {
      logger.error('model', '更新模型部署状态失败', { version, error: err.message });
      throw err;
    }
  }
}

export const reviewService = new ReviewService();
