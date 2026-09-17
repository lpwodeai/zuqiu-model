import { db } from '../database/index.js';

export class MatchDAL {
  async getAll(filters = {}) {
    let sql = `
      SELECT m.*, 
        ht.name as home_team_name,
        at.name as away_team_name,
        c.name as competition_name
      FROM matches m
      LEFT JOIN teams ht ON m.homeTeamId = ht.id
      LEFT JOIN teams at ON m.awayTeamId = at.id
      LEFT JOIN competitions c ON m.competitionId = c.id
    `;
    
    const params = [];
    const conditions = [];

    if (filters.date) {
      conditions.push('m.date = ?');
      params.push(filters.date);
    }

    if (filters.competition) {
      conditions.push('c.name = ?');
      params.push(filters.competition);
    }

    if (filters.team) {
      conditions.push('(ht.name = ? OR at.name = ?)');
      params.push(filters.team, filters.team);
    }

    if (filters.season) {
      conditions.push('(m.date >= ? AND m.date <= ?)');
      params.push(`${filters.season}-07-01`, `${parseInt(filters.season) + 1}-06-30`);
    }

    if (conditions.length > 0) {
      sql += ' WHERE ' + conditions.join(' AND ');
    }

    sql += ' ORDER BY m.date DESC';

    const page = filters.page || 1;
    const limit = filters.limit || 50;
    const offset = (page - 1) * limit;
    
    sql += ' LIMIT ? OFFSET ?';
    params.push(limit, offset);

    const rows = await db.all(sql, params);
    
    const countSql = sql.replace(/SELECT[\s\S]*?FROM/i, 'SELECT COUNT(*) as total FROM').replace(/ORDER BY[\s\S]*/i, '').replace(/LIMIT[\s\S]*/i, '');
    const count = await db.get(countSql, params.slice(0, -2));

    return {
      data: rows,
      total: count?.total || 0,
      page,
      limit
    };
  }

  async getById(id) {
    const sql = `
      SELECT m.*, 
        ht.name as home_team_name,
        at.name as away_team_name,
        c.name as competition_name
      FROM matches m
      LEFT JOIN teams ht ON m.homeTeamId = ht.id
      LEFT JOIN teams at ON m.awayTeamId = at.id
      LEFT JOIN competitions c ON m.competitionId = c.id
      WHERE m.id = ?
    `;
    return await db.get(sql, [id]);
  }

  async create(matchData) {
    const {
      date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
      homeXg, awayXg, homeXgot, awayXgot, homeBigChances, awayBigChances,
      homeXa, awayXa, homeSaves, awaySaves, homeTouchesBox, awayTouchesBox,
      homeHitsPost, awayHitsPost, homeShots, homeShotsOnTarget,
      awayShots, awayShotsOnTarget, homePossession, homeCorners,
      awayCorners, homeFouls, awayFouls, homeYellowCards, awayYellowCards,
      matchHash
    } = matchData;

    const sql = `
      INSERT INTO matches (
        date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
        homeXg, awayXg, homeXgot, awayXgot, homeBigChances, awayBigChances,
        homeXa, awayXa, homeSaves, awaySaves, homeTouchesBox, awayTouchesBox,
        homeHitsPost, awayHitsPost, homeShots, homeShotsOnTarget,
        awayShots, awayShotsOnTarget, homePossession, homeCorners,
        awayCorners, homeFouls, awayFouls, homeYellowCards, awayYellowCards,
        matchHash, createdAt, updatedAt
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `;

    const now = new Date().toISOString();
    const result = await db.run(sql, [
      date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
      homeXg, awayXg, homeXgot, awayXgot, homeBigChances, awayBigChances,
      homeXa, awayXa, homeSaves, awaySaves, homeTouchesBox, awayTouchesBox,
      homeHitsPost, awayHitsPost, homeShots, homeShotsOnTarget,
      awayShots, awayShotsOnTarget, homePossession, homeCorners,
      awayCorners, homeFouls, awayFouls, homeYellowCards, awayYellowCards,
      matchHash, now, now
    ]);

    return await this.getById(result.lastID);
  }

  async update(id, matchData) {
    const updates = [];
    const params = [];

    const fields = [
      'date', 'homeTeamId', 'awayTeamId', 'homeGoals', 'awayGoals', 'competitionId',
      'homeXg', 'awayXg', 'homeXgot', 'awayXgot', 'homeBigChances', 'awayBigChances',
      'homeXa', 'awayXa', 'homeSaves', 'awaySaves', 'homeTouchesBox', 'awayTouchesBox',
      'homeHitsPost', 'awayHitsPost', 'homeShots', 'homeShotsOnTarget',
      'awayShots', 'awayShotsOnTarget', 'homePossession', 'homeCorners',
      'awayCorners', 'homeFouls', 'awayFouls', 'homeYellowCards', 'awayYellowCards'
    ];

    for (const field of fields) {
      if (matchData[field] !== undefined) {
        updates.push(`${field} = ?`);
        params.push(matchData[field]);
      }
    }

    updates.push('updatedAt = ?');
    params.push(new Date().toISOString());
    params.push(id);

    const sql = `UPDATE matches SET ${updates.join(', ')} WHERE id = ?`;
    await db.run(sql, params);

    return await this.getById(id);
  }

  async delete(id) {
    const sql = 'DELETE FROM matches WHERE id = ?';
    const result = await db.run(sql, [id]);
    return result.changes > 0;
  }

  async findByHash(matchHash) {
    const sql = 'SELECT * FROM matches WHERE matchHash = ?';
    return await db.get(sql, [matchHash]);
  }

  async getTeamStatistics(teamId) {
    const sql = `
      SELECT 
        COUNT(*) as total_matches,
        SUM(CASE WHEN homeTeamId = ? THEN homeGoals ELSE awayGoals END) as total_goals,
        SUM(CASE WHEN homeTeamId = ? THEN awayGoals ELSE homeGoals END) as total_conceded,
        SUM(CASE WHEN homeTeamId = ? AND homeGoals > awayGoals THEN 1 WHEN awayTeamId = ? AND awayGoals > homeGoals THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN homeGoals = awayGoals THEN 1 ELSE 0 END) as draws,
        SUM(CASE WHEN homeTeamId = ? AND homeGoals < awayGoals THEN 1 WHEN awayTeamId = ? AND awayGoals < homeGoals THEN 1 ELSE 0 END) as losses,
        AVG(CASE WHEN homeTeamId = ? THEN homeXg ELSE awayXg END) as avg_xg,
        AVG(CASE WHEN homeTeamId = ? THEN awayXg ELSE homeXg END) as avg_xga
      FROM matches
      WHERE homeTeamId = ? OR awayTeamId = ?
    `;
    
    const params = Array(10).fill(teamId);
    return await db.get(sql, params);
  }

  async getCompetitionStatistics(competitionId) {
    const sql = `
      SELECT 
        COUNT(*) as total_matches,
        AVG(homeGoals + awayGoals) as avg_goals_per_match,
        AVG(homeXg + awayXg) as avg_xg_per_match,
        SUM(CASE WHEN homeGoals > awayGoals THEN 1 ELSE 0 END) as home_wins,
        SUM(CASE WHEN homeGoals = awayGoals THEN 1 ELSE 0 END) as draws,
        SUM(CASE WHEN homeGoals < awayGoals THEN 1 ELSE 0 END) as away_wins
      FROM matches
      WHERE competitionId = ?
    `;
    return await db.get(sql, [competitionId]);
  }

  async getHeadToHead(team1Id, team2Id) {
    const sql = `
      SELECT m.*, 
        ht.name as home_team_name,
        at.name as away_team_name
      FROM matches m
      LEFT JOIN teams ht ON m.homeTeamId = ht.id
      LEFT JOIN teams at ON m.awayTeamId = at.id
      WHERE (homeTeamId = ? AND awayTeamId = ?) OR (homeTeamId = ? AND awayTeamId = ?)
      ORDER BY m.date DESC
    `;
    return await db.all(sql, [team1Id, team2Id, team2Id, team1Id]);
  }

  async getRecentMatches(teamId, limit = 10) {
    const sql = `
      SELECT m.*, 
        ht.name as home_team_name,
        at.name as away_team_name
      FROM matches m
      LEFT JOIN teams ht ON m.homeTeamId = ht.id
      LEFT JOIN teams at ON m.awayTeamId = at.id
      WHERE homeTeamId = ? OR awayTeamId = ?
      ORDER BY m.date DESC
      LIMIT ?
    `;
    return await db.all(sql, [teamId, teamId, limit]);
  }

  async getMatchCountByDate(date) {
    const sql = 'SELECT COUNT(*) as count FROM matches WHERE date = ?';
    const result = await db.get(sql, [date]);
    return result?.count || 0;
  }

  async getDateRange() {
    const sql = 'SELECT MIN(date) as min_date, MAX(date) as max_date FROM matches';
    return await db.get(sql);
  }

  async bulkInsert(matches) {
    let inserted = 0;
    let skipped = 0;
    let errors = [];

    for (const match of matches) {
      try {
        const existing = await this.findByHash(match.matchHash);
        if (existing) {
          skipped++;
          continue;
        }
        await this.create(match);
        inserted++;
      } catch (err) {
        errors.push({ match: match.matchHash, error: err.message });
      }
    }

    return { inserted, skipped, errors };
  }
}

export class TeamDAL {
  async getAll(filters = {}) {
    let sql = 'SELECT * FROM teams';
    const params = [];
    const conditions = [];

    if (filters.name) {
      conditions.push('name LIKE ?');
      params.push(`%${filters.name}%`);
    }

    if (filters.country) {
      conditions.push('country LIKE ?');
      params.push(`%${filters.country}%`);
    }

    if (filters.league) {
      conditions.push('league LIKE ?');
      params.push(`%${filters.league}%`);
    }

    if (conditions.length > 0) {
      sql += ' WHERE ' + conditions.join(' AND ');
    }

    sql += ' ORDER BY name';

    const page = filters.page || 1;
    const limit = filters.limit || 50;
    const offset = (page - 1) * limit;
    
    sql += ' LIMIT ? OFFSET ?';
    params.push(limit, offset);

    const rows = await db.all(sql, params);
    
    const countSql = sql.replace(/SELECT[\s\S]*?FROM/i, 'SELECT COUNT(*) as total FROM').replace(/ORDER BY[\s\S]*/i, '').replace(/LIMIT[\s\S]*/i, '');
    const count = await db.get(countSql, params.slice(0, -2));

    return {
      data: rows,
      total: count?.total || 0,
      page,
      limit
    };
  }

  async getById(id) {
    const sql = 'SELECT * FROM teams WHERE id = ?';
    return await db.get(sql, [id]);
  }

  async getByName(name) {
    const sql = 'SELECT * FROM teams WHERE name = ?';
    return await db.get(sql, [name]);
  }

  async create(teamData) {
    const { name, shortName, country, league } = teamData;
    const sql = `
      INSERT INTO teams (name, shortName, country, league, createdAt, updatedAt)
      VALUES (?, ?, ?, ?, ?, ?)
    `;
    const now = new Date().toISOString();
    const result = await db.run(sql, [name, shortName, country, league, now, now]);
    return await this.getById(result.lastID);
  }

  async update(id, teamData) {
    const updates = [];
    const params = [];

    if (teamData.name !== undefined) { updates.push('name = ?'); params.push(teamData.name); }
    if (teamData.shortName !== undefined) { updates.push('shortName = ?'); params.push(teamData.shortName); }
    if (teamData.country !== undefined) { updates.push('country = ?'); params.push(teamData.country); }
    if (teamData.league !== undefined) { updates.push('league = ?'); params.push(teamData.league); }

    updates.push('updatedAt = ?');
    params.push(new Date().toISOString());
    params.push(id);

    const sql = `UPDATE teams SET ${updates.join(', ')} WHERE id = ?`;
    await db.run(sql, params);
    return await this.getById(id);
  }

  async delete(id) {
    const sql = 'DELETE FROM teams WHERE id = ?';
    const result = await db.run(sql, [id]);
    return result.changes > 0;
  }

  async findOrCreate(name, options = {}) {
    let team = await this.getByName(name);
    if (!team) {
      team = await this.create({
        name,
        shortName: options.shortName || name,
        country: options.country || null,
        league: options.league || null
      });
    }
    return team;
  }

  async bulkInsert(teams) {
    let inserted = 0;
    let skipped = 0;

    for (const team of teams) {
      try {
        const existing = await this.getByName(team.name);
        if (existing) {
          skipped++;
          continue;
        }
        await this.create(team);
        inserted++;
      } catch (err) {
        console.error('插入球队失败:', team.name, err.message);
      }
    }

    return { inserted, skipped };
  }
}

export class CompetitionDAL {
  async getAll() {
    const sql = 'SELECT * FROM competitions ORDER BY name';
    return await db.all(sql);
  }

  async getById(id) {
    const sql = 'SELECT * FROM competitions WHERE id = ?';
    return await db.get(sql, [id]);
  }

  async getByName(name) {
    const sql = 'SELECT * FROM competitions WHERE name = ?';
    return await db.get(sql, [name]);
  }

  async create(competitionData) {
    const { name, code, country, season } = competitionData;
    const sql = `
      INSERT INTO competitions (name, code, country, season, createdAt, updatedAt)
      VALUES (?, ?, ?, ?, ?, ?)
    `;
    const now = new Date().toISOString();
    const result = await db.run(sql, [name, code, country, season, now, now]);
    return await this.getById(result.lastID);
  }

  async update(id, competitionData) {
    const updates = [];
    const params = [];

    if (competitionData.name !== undefined) { updates.push('name = ?'); params.push(competitionData.name); }
    if (competitionData.code !== undefined) { updates.push('code = ?'); params.push(competitionData.code); }
    if (competitionData.country !== undefined) { updates.push('country = ?'); params.push(competitionData.country); }
    if (competitionData.season !== undefined) { updates.push('season = ?'); params.push(competitionData.season); }

    updates.push('updatedAt = ?');
    params.push(new Date().toISOString());
    params.push(id);

    const sql = `UPDATE competitions SET ${updates.join(', ')} WHERE id = ?`;
    await db.run(sql, params);
    return await this.getById(id);
  }

  async delete(id) {
    const sql = 'DELETE FROM competitions WHERE id = ?';
    const result = await db.run(sql, [id]);
    return result.changes > 0;
  }

  async findOrCreate(name, options = {}) {
    let competition = await this.getByName(name);
    if (!competition) {
      competition = await this.create({
        name,
        code: options.code || null,
        country: options.country || null,
        season: options.season || null
      });
    }
    return competition;
  }
}

export const matchDAL = new MatchDAL();
export const teamDAL = new TeamDAL();
export const competitionDAL = new CompetitionDAL();