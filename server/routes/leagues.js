/**
 * 五大联赛规则API路由
 */

import express from 'express';
import { 
  getAllLeagues, 
  getLeagueRules, 
  generateValidationReport,
  getLeaguePointsSystem,
  getLeaguePromotionRelegation,
  getLeagueSpecialRules,
  getWeatherImpact
} from '../services/league-rules.js';

const router = express.Router();

/**
 * GET /api/leagues
 * 获取所有五大联赛列表
 */
router.get('/', async (req, res) => {
  try {
    const leagues = getAllLeagues();
    res.json({
      success: true,
      data: leagues,
      count: leagues.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'LEAGUES_ERROR' }
    });
  }
});

/**
 * GET /api/leagues/:leagueId
 * 获取指定联赛规则详情
 */
router.get('/:leagueId', async (req, res) => {
  try {
    const { leagueId } = req.params;
    const rules = getLeagueRules(leagueId);

    if (!rules) {
      return res.status(404).json({
        error: { message: '联赛不存在', code: 'LEAGUE_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: rules,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'LEAGUE_ERROR' }
    });
  }
});

/**
 * GET /api/leagues/:leagueId/points
 * 获取指定联赛积分制度
 */
router.get('/:leagueId/points', async (req, res) => {
  try {
    const { leagueId } = req.params;
    const pointsSystem = getLeaguePointsSystem(leagueId);

    if (!pointsSystem) {
      return res.status(404).json({
        error: { message: '联赛不存在', code: 'LEAGUE_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: pointsSystem,
      league: getLeagueRules(leagueId)?.name,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'POINTS_ERROR' }
    });
  }
});

/**
 * GET /api/leagues/:leagueId/promotion
 * 获取指定联赛升降级规则
 */
router.get('/:leagueId/promotion', async (req, res) => {
  try {
    const { leagueId } = req.params;
    const promotion = getLeaguePromotionRelegation(leagueId);

    if (!promotion) {
      return res.status(404).json({
        error: { message: '联赛不存在', code: 'LEAGUE_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: promotion,
      league: getLeagueRules(leagueId)?.name,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'PROMOTION_ERROR' }
    });
  }
});

/**
 * GET /api/leagues/:leagueId/special-rules
 * 获取指定联赛特殊规则
 */
router.get('/:leagueId/special-rules', async (req, res) => {
  try {
    const { leagueId } = req.params;
    const specialRules = getLeagueSpecialRules(leagueId);

    if (!specialRules) {
      return res.status(404).json({
        error: { message: '联赛不存在', code: 'LEAGUE_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: specialRules,
      league: getLeagueRules(leagueId)?.name,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'SPECIAL_RULES_ERROR' }
    });
  }
});

/**
 * GET /api/leagues/:leagueId/weather
 * 获取指定联赛天气影响因素
 */
router.get('/:leagueId/weather', async (req, res) => {
  try {
    const { leagueId } = req.params;
    const weather = getWeatherImpact(leagueId);

    if (!weather) {
      return res.status(404).json({
        error: { message: '联赛不存在', code: 'LEAGUE_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: weather,
      league: getLeagueRules(leagueId)?.name,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'WEATHER_ERROR' }
    });
  }
});

/**
 * GET /api/leagues/validation/report
 * 获取联赛规则校验报告
 */
router.get('/validation/report', async (req, res) => {
  try {
    const report = generateValidationReport();
    
    res.json({
      success: true,
      data: report,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'VALIDATION_ERROR' }
    });
  }
});

export default router;