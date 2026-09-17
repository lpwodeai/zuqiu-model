import express from 'express';
import { matchDAL, teamDAL, competitionDAL } from '../services/data-dal.js';
import { db } from '../database/index.js';

const router = express.Router();

router.get('/matches', async (req, res) => {
  try {
    const filters = {
      date: req.query.date,
      competition: req.query.competition,
      team: req.query.team,
      season: req.query.season,
      page: parseInt(req.query.page) || 1,
      limit: parseInt(req.query.limit) || 50
    };
    
    const result = await matchDAL.getAll(filters);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/matches/:id', async (req, res) => {
  try {
    const match = await matchDAL.getById(req.params.id);
    if (match) {
      res.json(match);
    } else {
      res.status(404).json({ error: '比赛不存在' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/matches', async (req, res) => {
  try {
    const match = await matchDAL.create(req.body);
    res.status(201).json(match);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.put('/matches/:id', async (req, res) => {
  try {
    const match = await matchDAL.update(req.params.id, req.body);
    if (match) {
      res.json(match);
    } else {
      res.status(404).json({ error: '比赛不存在' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.delete('/matches/:id', async (req, res) => {
  try {
    const success = await matchDAL.delete(req.params.id);
    if (success) {
      res.json({ message: '删除成功' });
    } else {
      res.status(404).json({ error: '比赛不存在' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/matches/statistics/team/:teamId', async (req, res) => {
  try {
    const stats = await matchDAL.getTeamStatistics(req.params.teamId);
    res.json(stats);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/matches/statistics/competition/:competitionId', async (req, res) => {
  try {
    const stats = await matchDAL.getCompetitionStatistics(req.params.competitionId);
    res.json(stats);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/matches/head-to-head/:team1Id/:team2Id', async (req, res) => {
  try {
    const matches = await matchDAL.getHeadToHead(req.params.team1Id, req.params.team2Id);
    res.json(matches);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/matches/recent/:teamId', async (req, res) => {
  try {
    const limit = parseInt(req.query.limit) || 10;
    const matches = await matchDAL.getRecentMatches(req.params.teamId, limit);
    res.json(matches);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/matches/date-range', async (req, res) => {
  try {
    const range = await matchDAL.getDateRange();
    res.json(range);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/teams', async (req, res) => {
  try {
    const filters = {
      name: req.query.name,
      country: req.query.country,
      league: req.query.league,
      page: parseInt(req.query.page) || 1,
      limit: parseInt(req.query.limit) || 50
    };
    
    const result = await teamDAL.getAll(filters);
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/teams/:id', async (req, res) => {
  try {
    const team = await teamDAL.getById(req.params.id);
    if (team) {
      res.json(team);
    } else {
      res.status(404).json({ error: '球队不存在' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/teams', async (req, res) => {
  try {
    const team = await teamDAL.create(req.body);
    res.status(201).json(team);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.put('/teams/:id', async (req, res) => {
  try {
    const team = await teamDAL.update(req.params.id, req.body);
    if (team) {
      res.json(team);
    } else {
      res.status(404).json({ error: '球队不存在' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.delete('/teams/:id', async (req, res) => {
  try {
    const success = await teamDAL.delete(req.params.id);
    if (success) {
      res.json({ message: '删除成功' });
    } else {
      res.status(404).json({ error: '球队不存在' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/teams/search', async (req, res) => {
  try {
    const { q } = req.query;
    if (!q) {
      return res.status(400).json({ error: '缺少搜索关键词' });
    }
    
    const result = await teamDAL.getAll({ name: q, limit: 20 });
    res.json(result);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/competitions', async (req, res) => {
  try {
    const competitions = await competitionDAL.getAll();
    res.json(competitions);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/competitions/:id', async (req, res) => {
  try {
    const competition = await competitionDAL.getById(req.params.id);
    if (competition) {
      res.json(competition);
    } else {
      res.status(404).json({ error: '联赛不存在' });
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.post('/competitions', async (req, res) => {
  try {
    const competition = await competitionDAL.create(req.body);
    res.status(201).json(competition);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/export/matches', async (req, res) => {
  try {
    const filters = {
      date: req.query.date,
      competition: req.query.competition,
      team: req.query.team
    };
    
    const result = await matchDAL.getAll({ ...filters, page: 1, limit: 10000 });
    const format = req.query.format || 'json';
    
    if (format === 'csv') {
      res.setHeader('Content-Type', 'text/csv');
      res.setHeader('Content-Disposition', 'attachment; filename=matches.csv');
      
      const headers = ['date', 'home_team', 'away_team', 'home_goals', 'away_goals', 'competition',
        'home_xg', 'away_xg', 'home_xgot', 'away_xgot'];
      res.write(headers.join(',') + '\n');
      
      result.data.forEach(match => {
        const row = [
          match.date,
          match.home_team_name,
          match.away_team_name,
          match.homeGoals,
          match.awayGoals,
          match.competition_name,
          match.homeXg || '',
          match.awayXg || '',
          match.homeXgot || '',
          match.awayXgot || ''
        ];
        res.write(row.join(',') + '\n');
      });
      res.end();
    } else {
      res.json(result.data);
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

router.get('/stats', async (req, res) => {
  try {
    const teams = await db.get('SELECT COUNT(*) as count FROM teams');
    const matches = await db.get('SELECT COUNT(*) as count FROM matches');
    const competitions = await db.get('SELECT COUNT(*) as count FROM competitions');
    const dateRange = await matchDAL.getDateRange();
    
    res.json({
      teams: teams.count,
      matches: matches.count,
      competitions: competitions.count,
      dateRange
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

export default router;