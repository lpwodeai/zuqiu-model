/**
 * 预测API路由
 */

import express from 'express';
import PredictionService from '../services/prediction-service.js';
import { authenticateToken, optionalAuth } from '../middleware/auth.js';
import ApiResponse from '../../shared/api-response.js';

const router = express.Router();

/**
 * POST /api/predict
 * 执行比赛预测
 * 
 * 请求体:
 * {
 *   "homeTeam": "brazil",
 *   "awayTeam": "haiti",
 *   "options": {
 *     "venue": "sea_level",
 *     "neutral": false,
 *     "weather": "normal"
 *   }
 * }
 */
router.post('/', optionalAuth, async (req, res) => {
  try {
    const { homeTeam, awayTeam, options = {} } = req.body;

    if (!homeTeam || !awayTeam) {
      return res.status(400).json(ApiResponse.validationError('请提供主队和客队名称', 'MISSING_TEAMS'));
    }

    const prediction = await PredictionService.predict({
      homeTeam,
      awayTeam,
      ...options
    });

    res.json(ApiResponse.prediction(prediction));
  } catch (err) {
    console.error('预测错误:', err);
    res.status(500).json(ApiResponse.error(err.message, 'PREDICTION_ERROR'));
  }
});

/**
 * POST /api/predict/batch
 * 批量预测多场比赛
 */
router.post('/batch', authenticateToken, async (req, res) => {
  try {
    const { matches } = req.body;

    if (!matches || !Array.isArray(matches)) {
      return res.status(400).json(ApiResponse.validationError('请提供比赛数组', 'INVALID_MATCHES'));
    }

    const predictions = await Promise.all(
      matches.map(match => PredictionService.predict(match))
    );

    res.json(ApiResponse.predictions(predictions, predictions.length));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'BATCH_PREDICTION_ERROR'));
  }
});

/**
 * POST /api/predict/with-odds
 * 融合赔率数据的预测
 */
router.post('/with-odds', optionalAuth, async (req, res) => {
  try {
    const { homeTeam, awayTeam, odds, options = {} } = req.body;

    if (!homeTeam || !awayTeam) {
      return res.status(400).json(ApiResponse.validationError('请提供主队和客队名称', 'MISSING_TEAMS'));
    }

    const prediction = await PredictionService.predictWithOdds({
      homeTeam,
      awayTeam,
      odds,
      ...options
    });

    res.json(ApiResponse.prediction(prediction));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'PREDICTION_ERROR'));
  }
});

/**
 * POST /api/predict/with-lineup
 * 融合首发阵容的预测
 */
router.post('/with-lineup', optionalAuth, async (req, res) => {
  try {
    const { homeTeam, awayTeam, homeLineup, awayLineup, options = {} } = req.body;

    if (!homeTeam || !awayTeam) {
      return res.status(400).json(ApiResponse.validationError('请提供主队和客队名称', 'MISSING_TEAMS'));
    }

    const prediction = await PredictionService.predictWithLineup({
      homeTeam,
      awayTeam,
      homeLineup,
      awayLineup,
      ...options
    });

    res.json(ApiResponse.prediction(prediction));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'PREDICTION_ERROR'));
  }
});

/**
 * POST /api/predict/handicap
 * 让球胜平负独立预测
 *
 * 请求体:
 * {
 *   "homeTeam": "阿森纳",
 *   "awayTeam": "切尔西",
 *   "handicap": -1,  // 让球数: 正数=主队让球, 负数=客队让球, 0=平手
 *   "options": { "venue": "sea_level", "neutral": false }
 * }
 */
router.post('/handicap', optionalAuth, async (req, res) => {
  try {
    const { homeTeam, awayTeam, handicap = 0, options = {} } = req.body;

    if (!homeTeam || !awayTeam) {
      return res.status(400).json(ApiResponse.validationError('请提供主队和客队名称', 'MISSING_TEAMS'));
    }

    if (typeof handicap !== 'number') {
      return res.status(400).json(ApiResponse.validationError('handicap 必须为数字', 'INVALID_HANDICAP'));
    }

    const prediction = await PredictionService.predictHandicap({
      homeTeam,
      awayTeam,
      handicap,
      ...options
    });

    res.json(ApiResponse.prediction(prediction));
  } catch (err) {
    console.error('让球预测错误:', err);
    res.status(500).json(ApiResponse.error(err.message, 'HANDICAP_PREDICTION_ERROR'));
  }
});

/**
 * POST /api/predict/score
 * 精确比分预测（使用 T-006 v4: Dixon-Coles + Monte Carlo + 赔率融合）
 *
 * 请求体:
 * {
 *   "homeTeam": "阿森纳",
 *   "awayTeam": "切尔西",
 *   "scoreOdds": { "1:0": 8.5, "0:0": 11.0, ... },  // 可选: 比分赔率
 *   "options": { "venue": "sea_level", "neutral": false }
 * }
 *
 * 响应:
 * {
 *   "scorePrediction": {
 *     "topScores": [{ "score": "1:1", "probability": 0.12 }, ...],
 *     "exactProbability": 0.12,
 *     "within1Probability": 0.65,
 *     "scoreModel": "T-006-v4"
 *   }
 * }
 */
router.post('/score', optionalAuth, async (req, res) => {
  try {
    const { homeTeam, awayTeam, scoreOdds = null, options = {} } = req.body;

    if (!homeTeam || !awayTeam) {
      return res.status(400).json(ApiResponse.validationError('请提供主队和客队名称', 'MISSING_TEAMS'));
    }

    const prediction = await PredictionService.predictScore({
      homeTeam,
      awayTeam,
      scoreOdds,
      ...options
    });

    res.json(ApiResponse.prediction(prediction));
  } catch (err) {
    console.error('比分预测错误:', err);
    res.status(500).json(ApiResponse.error(err.message, 'SCORE_PREDICTION_ERROR'));
  }
});

/**
 * GET /api/predict/history
 * 获取预测历史（本地个人使用：可选认证，无 token 时归属本地默认用户）
 */
router.get('/history', optionalAuth, async (req, res) => {
  try {
    const userId = req.user?.userId || 'local_user';
    const { limit = 20, offset = 0 } = req.query;

    const history = await PredictionService.getHistory(userId, limit, offset);

    res.json(ApiResponse.success(history, '获取预测历史成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'HISTORY_ERROR'));
  }
});

/**
 * POST /api/predict/history
 * 保存预测到历史（本地个人使用：可选认证，无 token 时归属本地默认用户）
 * 请求体: { prediction: {...}, league?: 'epl' }
 */
router.post('/history', optionalAuth, async (req, res) => {
  try {
    const userId = req.user?.userId || 'local_user';
    const { prediction, league } = req.body;

    if (!prediction) {
      return res.status(400).json(ApiResponse.validationError('请提供预测结果', 'MISSING_PREDICTION'));
    }

    const saved = await PredictionService.saveHistory(userId, prediction, league);

    res.json(ApiResponse.success(saved, '预测已保存到历史'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'HISTORY_SAVE_ERROR'));
  }
});

/**
 * DELETE /api/predict/history/:id
 * 删除单条预测历史（可选认证，归属校验）
 */
router.delete('/history/:id', optionalAuth, async (req, res) => {
  try {
    const userId = req.user?.userId || 'local_user';
    const { id } = req.params;

    const deleted = await PredictionService.deleteHistoryItem(userId, id);
    if (!deleted) {
      return res.status(404).json(ApiResponse.error('记录不存在或无权删除', 'NOT_FOUND'));
    }

    res.json(ApiResponse.success({ id }, '删除成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'HISTORY_DELETE_ERROR'));
  }
});

/**
 * DELETE /api/predict/history
 * 清空当前用户的预测历史（可选认证）
 */
router.delete('/history', optionalAuth, async (req, res) => {
  try {
    const userId = req.user?.userId || 'local_user';
    const count = await PredictionService.clearHistory(userId);

    res.json(ApiResponse.success({ deletedCount: count }, `已清空 ${count} 条历史记录`));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'HISTORY_CLEAR_ERROR'));
  }
});

/**
 * GET /api/predict/teams
 * 获取所有支持的球队列表
 */
router.get('/teams', async (req, res) => {
  try {
    const teams = PredictionService.getSupportedTeams();

    res.json(ApiResponse.teams(teams, teams.length));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'TEAMS_ERROR'));
  }
});

export default router;