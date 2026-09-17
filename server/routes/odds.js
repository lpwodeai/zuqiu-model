/**
 * 赔率数据API路由
 */

import express from 'express';
import DataService from '../services/data-service.js';
import { authenticateToken, optionalAuth } from '../middleware/auth.js';

const router = express.Router();

/**
 * GET /api/odds/:matchId
 * 获取比赛赔率数据
 */
router.get('/:matchId', async (req, res) => {
  try {
    const { matchId } = req.params;
    const odds = await DataService.getOdds(matchId);

    if (!odds) {
      return res.status(404).json({
        error: { message: '赔率数据不存在', code: 'ODDS_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: odds,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'ODDS_ERROR' }
    });
  }
});

/**
 * GET /api/odds/:matchId/history
 * 获取赔率历史变化
 */
router.get('/:matchId/history', async (req, res) => {
  try {
    const { matchId } = req.params;
    const { limit = 50 } = req.query;
    const history = await DataService.getOddsHistory(matchId, limit);

    res.json({
      success: true,
      data: history,
      count: history.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'ODDS_HISTORY_ERROR' }
    });
  }
});

/**
 * POST /api/odds/:matchId
 * 更新赔率数据（需要认证）
 */
router.post('/:matchId', authenticateToken, async (req, res) => {
  try {
    const { matchId } = req.params;
    const oddsData = req.body;

    const result = await DataService.updateOdds(matchId, oddsData);

    res.json({
      success: true,
      data: result,
      message: '赔率数据已更新',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'ODDS_UPDATE_ERROR' }
    });
  }
});

/**
 * GET /api/odds/live
 * 获取实时赔率数据
 */
router.get('/live', async (req, res) => {
  try {
    const liveOdds = await DataService.getLiveOdds();

    res.json({
      success: true,
      data: liveOdds,
      count: liveOdds.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'LIVE_ODDS_ERROR' }
    });
  }
});

/**
 * POST /api/odds/analyze
 * 分析赔率变化趋势
 */
router.post('/analyze', async (req, res) => {
  try {
    const { matchId, oddsHistory } = req.body;
    const analysis = await DataService.analyzeOddsTrend(matchId, oddsHistory);

    res.json({
      success: true,
      data: analysis,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'ODDS_ANALYSIS_ERROR' }
    });
  }
});

export default router;