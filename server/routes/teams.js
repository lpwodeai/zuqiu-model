/**
 * 球队数据API路由
 */

import express from 'express';
import PredictionService from '../services/prediction-service.js';
import { authenticateToken, optionalAuth } from '../middleware/auth.js';

const router = express.Router();

/**
 * GET /api/teams
 * 获取所有球队列表
 */
router.get('/', async (req, res) => {
  try {
    const { league, search } = req.query;
    const teams = PredictionService.getSupportedTeams();

    let filteredTeams = teams;
    
    if (league) {
      filteredTeams = teams.filter(t => t.league === league);
    }
    
    if (search) {
      filteredTeams = teams.filter(t => 
        t.name.toLowerCase().includes(search.toLowerCase())
      );
    }

    res.json({
      success: true,
      data: filteredTeams,
      count: filteredTeams.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'TEAMS_ERROR' }
    });
  }
});

/**
 * GET /api/teams/groups
 * 获取所有联赛列表
 */
router.get('/groups', async (req, res) => {
  try {
    const teams = PredictionService.getSupportedTeams();
    const leagues = {};
    
    teams.forEach(team => {
      if (!leagues[team.league]) {
        leagues[team.league] = [];
      }
      leagues[team.league].push(team);
    });

    res.json({
      success: true,
      data: leagues,
      count: Object.keys(leagues).length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'LEAGUES_ERROR' }
    });
  }
});

/**
 * GET /api/teams/:teamKey
 * 获取单个球队详情
 */
router.get('/:teamKey', async (req, res) => {
  try {
    const { teamKey } = req.params;
    const team = PredictionService.getTeamData(teamKey);

    if (!team) {
      return res.status(404).json({
        error: { message: '球队不存在', code: 'TEAM_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: team,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'TEAM_ERROR' }
    });
  }
});

/**
 * GET /api/teams/:teamKey/players
 * 获取球队球员数据
 */
router.get('/:teamKey/players', async (req, res) => {
  try {
    const { teamKey } = req.params;
    const players = PredictionService.getTeamPlayers(teamKey);

    res.json({
      success: true,
      data: players,
      count: players ? players.length : 0,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'PLAYERS_ERROR' }
    });
  }
});

/**
 * PUT /api/teams/:teamKey
 * 更新球队数据（需要认证）
 */
router.put('/:teamKey', authenticateToken, async (req, res) => {
  try {
    const { teamKey } = req.params;
    const updates = req.body;

    const result = await PredictionService.updateTeamData(teamKey, updates);

    res.json({
      success: true,
      data: result,
      message: '球队数据已更新',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'UPDATE_ERROR' }
    });
  }
});

/**
 * POST /api/teams/compare
 * 比较两支球队
 */
router.post('/compare', async (req, res) => {
  try {
    const { team1, team2 } = req.body;

    if (!team1 || !team2) {
      return res.status(400).json({
        error: { message: '请提供两支球队', code: 'MISSING_TEAMS' }
      });
    }

    const comparison = PredictionService.compareTeams(team1, team2);

    res.json({
      success: true,
      data: comparison,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'COMPARE_ERROR' }
    });
  }
});

/**
 * GET /api/teams/mapping/misses
 * D-008: 获取球队名映射失效报告
 * 用于运维监控球队名映射是否完整
 */
router.get('/mapping/misses', async (req, res) => {
  try {
    const report = PredictionService.getTeamNameMissReport();
    res.json({
      success: true,
      data: report,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'MISS_REPORT_ERROR' }
    });
  }
});

/**
 * POST /api/teams/mapping/misses/reset
 * D-008: 重置球队名映射失效统计
 */
router.post('/mapping/misses/reset', async (req, res) => {
  try {
    PredictionService.resetTeamNameMissCache();
    res.json({
      success: true,
      message: '映射失效统计已重置',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'MISS_RESET_ERROR' }
    });
  }
});

export default router;