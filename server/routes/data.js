/**
 * 数据管理API路由
 */

import express from 'express';
import DataService from '../services/data-service.js';
import { authenticateToken } from '../middleware/auth.js';

const router = express.Router();

/**
 * GET /api/data/matches
 * 获取比赛列表
 */
router.get('/matches', async (req, res) => {
  try {
    const { date, league, status, season, page = 1, limit = 20 } = req.query;
    const matches = await DataService.getMatches({ 
      date, league, status, season,
      page: parseInt(page),
      limit: parseInt(limit)
    });

    res.json({
      success: true,
      data: matches.data,
      total: matches.total,
      page: matches.page,
      limit: matches.limit,
      count: matches.data.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'MATCHES_ERROR' }
    });
  }
});

router.get('/matches/stats', async (req, res) => {
  try {
    const { league, season } = req.query;
    const stats = await DataService.getMatchStats({ league, season });

    res.json({
      success: true,
      data: stats,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'STATS_ERROR' }
    });
  }
});

router.get('/analysis', async (req, res) => {
  try {
    const { league } = req.query;
    const analysis = await DataService.getAnalysisData({ league });

    res.json({
      success: true,
      data: analysis,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'ANALYSIS_ERROR' }
    });
  }
});

/**
 * GET /api/data/matches/:matchId
 * 获取比赛详情
 */
router.get('/matches/:matchId', async (req, res) => {
  try {
    const { matchId } = req.params;
    const match = await DataService.getMatch(matchId);

    if (!match) {
      return res.status(404).json({
        error: { message: '比赛不存在', code: 'MATCH_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: match,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'MATCH_ERROR' }
    });
  }
});

/**
 * POST /api/data/matches
 * 创建新比赛（需要认证）
 */
router.post('/matches', authenticateToken, async (req, res) => {
  try {
    const matchData = req.body;
    const match = await DataService.createMatch(matchData);

    res.json({
      success: true,
      data: match,
      message: '比赛已创建',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'CREATE_MATCH_ERROR' }
    });
  }
});

/**
 * GET /api/data/history
 * 获取历史比赛数据
 */
router.get('/history', async (req, res) => {
  try {
    const { team1, team2, limit = 10 } = req.query;
    const history = await DataService.getHistoricalMatches(team1, team2, limit);

    res.json({
      success: true,
      data: history,
      count: history.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'HISTORY_ERROR' }
    });
  }
});

/**
 * POST /api/data/import
 * 导入数据（需要认证）
 */
router.post('/import', authenticateToken, async (req, res) => {
  try {
    const { type, data } = req.body;

    if (!type || !data) {
      return res.status(400).json({
        error: { message: '请提供数据类型和数据', code: 'MISSING_DATA' }
      });
    }

    const result = await DataService.importData(type, data);

    res.json({
      success: true,
      data: result,
      message: `成功导入 ${result.count} 条数据`,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'IMPORT_ERROR' }
    });
  }
});

/**
 * POST /api/data/collect
 * 触发数据采集（需要认证）
 */
router.post('/collect', authenticateToken, async (req, res) => {
  try {
    const { sources } = req.body;
    const result = await DataService.triggerCollection(sources);

    res.json({
      success: true,
      data: result,
      message: '数据采集任务已启动',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'COLLECT_ERROR' }
    });
  }
});

/**
 * GET /api/data/status
 * 获取数据状态
 */
router.get('/status', async (req, res) => {
  try {
    const status = await DataService.getDataStatus();

    res.json({
      success: true,
      data: status,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'STATUS_ERROR' }
    });
  }
});

export default router;