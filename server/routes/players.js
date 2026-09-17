import express from 'express';
import PlayerService from '../services/player-service.js';

const router = express.Router();

router.get('/', async (req, res) => {
  try {
    const { teamId, league, position, positionGroup, isKeyPlayer, page = 1, limit = 20, search } = req.query;
    
    const filters = {};
    if (teamId) filters.teamId = parseInt(teamId);
    if (league) filters.league = league;
    if (position) filters.position = position;
    if (positionGroup) filters.positionGroup = positionGroup;
    if (isKeyPlayer !== undefined) filters.isKeyPlayer = isKeyPlayer === 'true';
    if (search) filters.search = search;
    
    const result = await PlayerService.getPlayers({
      ...filters,
      page: parseInt(page),
      limit: parseInt(limit)
    });

    res.json({
      success: true,
      data: result.data,
      total: result.total,
      page: result.page,
      limit: result.limit,
      count: result.data.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'PLAYERS_ERROR' }
    });
  }
});

router.get('/:playerId', async (req, res) => {
  try {
    const { playerId } = req.params;
    const player = await PlayerService.getPlayerById(parseInt(playerId));

    if (!player) {
      return res.status(404).json({
        error: { message: '球员不存在', code: 'PLAYER_NOT_FOUND' }
      });
    }

    res.json({
      success: true,
      data: player,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'PLAYER_ERROR' }
    });
  }
});

router.get('/:playerId/stats', async (req, res) => {
  try {
    const { playerId } = req.params;
    const { season } = req.query;
    
    const stats = await PlayerService.getPlayerStats(parseInt(playerId), season);

    res.json({
      success: true,
      data: stats,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'PLAYER_STATS_ERROR' }
    });
  }
});

router.get('/team/:teamId', async (req, res) => {
  try {
    const { teamId } = req.params;
    const players = await PlayerService.getPlayersByTeam(parseInt(teamId));

    res.json({
      success: true,
      data: players,
      count: players.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'TEAM_PLAYERS_ERROR' }
    });
  }
});

router.get('/stats/leaders', async (req, res) => {
  try {
    const { league, positionGroup, statType, limit = 10, season } = req.query;
    
    const leaders = await PlayerService.getStatsLeaders({
      league,
      positionGroup,
      statType,
      limit: parseInt(limit),
      season
    });

    res.json({
      success: true,
      data: leaders,
      count: leaders.length,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'LEADERS_ERROR' }
    });
  }
});

router.get('/stats/overview', async (req, res) => {
  try {
    const { league, teamId } = req.query;
    
    const overview = await PlayerService.getStatsOverview({ league, teamId: teamId ? parseInt(teamId) : null });

    res.json({
      success: true,
      data: overview,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'STATS_OVERVIEW_ERROR' }
    });
  }
});

export default router;