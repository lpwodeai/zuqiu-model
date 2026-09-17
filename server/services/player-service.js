import { db } from '../database/index.js';

export class PlayerDAL {
  async getAll(filters = {}) {
    let sql = `
      SELECT p.*, 
        t.name as team_name,
        t.league as team_league
      FROM players p
      LEFT JOIN teams t ON p.teamId = t.id
    `;
    
    const params = [];
    const conditions = [];

    if (filters.teamId) {
      conditions.push('p.teamId = ?');
      params.push(filters.teamId);
    }

    if (filters.league) {
      conditions.push('t.league = ?');
      params.push(filters.league);
    }

    if (filters.position) {
      conditions.push('p.position = ?');
      params.push(filters.position);
    }

    if (filters.positionGroup) {
      conditions.push('p.positionGroup = ?');
      params.push(filters.positionGroup);
    }

    if (filters.isKeyPlayer !== undefined) {
      conditions.push('p.isKeyPlayer = ?');
      params.push(filters.isKeyPlayer ? 1 : 0);
    }

    if (filters.search) {
      conditions.push('(p.name LIKE ? OR p.nameEn LIKE ?)');
      params.push(`%${filters.search}%`, `%${filters.search}%`);
    }

    if (conditions.length > 0) {
      sql += ' WHERE ' + conditions.join(' AND ');
    }

    sql += ' ORDER BY p.isKeyPlayer DESC, p.name ASC';

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
      SELECT p.*, 
        t.name as team_name,
        t.league as team_league
      FROM players p
      LEFT JOIN teams t ON p.teamId = t.id
      WHERE p.id = ?
    `;
    return await db.get(sql, [id]);
  }

  async getByTeam(teamId) {
    const sql = `
      SELECT p.*, 
        t.name as team_name,
        t.league as team_league
      FROM players p
      LEFT JOIN teams t ON p.teamId = t.id
      WHERE p.teamId = ?
      ORDER BY p.positionGroup, p.isKeyPlayer DESC, p.name ASC
    `;
    return await db.all(sql, [teamId]);
  }

  async getStats(playerId, season = null) {
    let sql = `
      SELECT ps.*
      FROM player_stats ps
      WHERE ps.playerId = ?
    `;
    const params = [playerId];

    if (season) {
      sql += ' AND ps.season = ?';
      params.push(season);
    }

    sql += ' ORDER BY ps.season DESC';

    return await db.all(sql, params);
  }

  async create(playerData) {
    const {
      name, nameEn, teamId, position, positionGroup,
      age, height, weight, nationality, jerseyNumber,
      marketValue, foot, isKeyPlayer, lineupRole, playerStatus
    } = playerData;

    const sql = `
      INSERT INTO players (
        name, nameEn, teamId, position, positionGroup,
        age, height, weight, nationality, jerseyNumber,
        marketValue, foot, isKeyPlayer, lineupRole, playerStatus,
        createdAt, updatedAt
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `;

    const result = await db.run(sql, [
      name, nameEn, teamId, position, positionGroup,
      age, height, weight, nationality, jerseyNumber,
      marketValue, foot, isKeyPlayer ? 1 : 0, lineupRole || 'backup', playerStatus || 'available',
      new Date().toISOString(), new Date().toISOString()
    ]);

    return result.lastID;
  }

  async createStats(statsData) {
    const {
      playerId, season, matches, starts, minutes,
      goals, assists, xg, xA, shots, shotsOnTarget,
      bigChances, bigChancesCreated, tackles, interceptions,
      blocks, duelsWon, aerialWon, dribblesCompleted,
      dribblesAttempted, passes, passesCompleted, keyPasses,
      throughBalls, crosses, fouls, yellowCards, redCards,
      penaltyGoals, penaltyMissed, rating
    } = statsData;

    const sql = `
      INSERT OR REPLACE INTO player_stats (
        playerId, season, matches, starts, minutes,
        goals, assists, xg, xA, shots, shotsOnTarget,
        bigChances, bigChancesCreated, tackles, interceptions,
        blocks, duelsWon, aerialWon, dribblesCompleted,
        dribblesAttempted, passes, passesCompleted, keyPasses,
        throughBalls, crosses, fouls, yellowCards, redCards,
        penaltyGoals, penaltyMissed, rating,
        createdAt, updatedAt
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `;

    const result = await db.run(sql, [
      playerId, season, matches || 0, starts || 0, minutes || 0,
      goals || 0, assists || 0, xg || 0, xA || 0, shots || 0, shotsOnTarget || 0,
      bigChances || 0, bigChancesCreated || 0, tackles || 0, interceptions || 0,
      blocks || 0, duelsWon || 0, aerialWon || 0, dribblesCompleted || 0,
      dribblesAttempted || 0, passes || 0, passesCompleted || 0, keyPasses || 0,
      throughBalls || 0, crosses || 0, fouls || 0, yellowCards || 0, redCards || 0,
      penaltyGoals || 0, penaltyMissed || 0, rating || 0,
      new Date().toISOString(), new Date().toISOString()
    ]);

    return result.lastID;
  }
}

class PlayerService {
  constructor() {
    this.dal = new PlayerDAL();
  }

  async getPlayers(filters = {}) {
    try {
      const result = await this.dal.getAll(filters);
      
      const players = result.data.map(player => ({
        id: player.id,
        name: player.name,
        nameEn: player.nameEn,
        teamId: player.teamId,
        teamName: player.team_name,
        teamLeague: player.team_league,
        position: player.position,
        positionGroup: player.positionGroup,
        age: player.age,
        height: player.height,
        weight: player.weight,
        nationality: player.nationality,
        jerseyNumber: player.jerseyNumber,
        marketValue: player.marketValue,
        foot: player.foot,
        isKeyPlayer: player.isKeyPlayer === 1,
        lineupRole: player.lineupRole,
        playerStatus: player.playerStatus,
        createdAt: player.createdAt,
        updatedAt: player.updatedAt
      }));

      return {
        data: players,
        total: result.total,
        page: result.page,
        limit: result.limit
      };
    } catch (err) {
      console.error('获取球员列表失败:', err.message);
      return { data: [], total: 0, page: filters.page || 1, limit: filters.limit || 20 };
    }
  }

  async getPlayerById(playerId) {
    try {
      const player = await this.dal.getById(playerId);
      if (!player) return null;

      const stats = await this.dal.getStats(playerId);
      
      return {
        id: player.id,
        name: player.name,
        nameEn: player.nameEn,
        teamId: player.teamId,
        teamName: player.team_name,
        teamLeague: player.team_league,
        position: player.position,
        positionGroup: player.positionGroup,
        age: player.age,
        height: player.height,
        weight: player.weight,
        nationality: player.nationality,
        jerseyNumber: player.jerseyNumber,
        marketValue: player.marketValue,
        foot: player.foot,
        isKeyPlayer: player.isKeyPlayer === 1,
        lineupRole: player.lineupRole,
        playerStatus: player.playerStatus,
        stats: stats.map(s => this.formatPlayerStats(s)),
        createdAt: player.createdAt,
        updatedAt: player.updatedAt
      };
    } catch (err) {
      console.error('获取球员详情失败:', err.message);
      return null;
    }
  }

  async getPlayerStats(playerId, season = null) {
    try {
      const stats = await this.dal.getStats(playerId, season);
      return stats.map(s => this.formatPlayerStats(s));
    } catch (err) {
      console.error('获取球员统计失败:', err.message);
      return [];
    }
  }

  async getPlayersByTeam(teamId) {
    try {
      const players = await this.dal.getByTeam(teamId);
      return players.map(player => ({
        id: player.id,
        name: player.name,
        nameEn: player.nameEn,
        teamId: player.teamId,
        teamName: player.team_name,
        teamLeague: player.team_league,
        position: player.position,
        positionGroup: player.positionGroup,
        age: player.age,
        jerseyNumber: player.jerseyNumber,
        marketValue: player.marketValue,
        isKeyPlayer: player.isKeyPlayer === 1,
        lineupRole: player.lineupRole,
        playerStatus: player.playerStatus
      }));
    } catch (err) {
      console.error('获取球队球员失败:', err.message);
      return [];
    }
  }

  async getStatsLeaders({ league, positionGroup, statType = 'goals', limit = 10, season = '2025' }) {
    try {
      const statFieldMap = {
        goals: 'ps.goals',
        assists: 'ps.assists',
        xg: 'ps.xg',
        xA: 'ps.xA',
        shots: 'ps.shots',
        tackles: 'ps.tackles',
        interceptions: 'ps.interceptions',
        rating: 'ps.rating',
        minutes: 'ps.minutes',
        matches: 'ps.matches'
      };

      const statField = statFieldMap[statType] || 'ps.goals';

      let sql = `
        SELECT p.name, p.nameEn, p.position, p.positionGroup,
               t.name as team_name, t.league as team_league,
               ${statField} as value, ps.matches, ps.minutes, ps.goals, ps.assists, ps.rating
        FROM player_stats ps
        JOIN players p ON ps.playerId = p.id
        JOIN teams t ON p.teamId = t.id
        WHERE ps.season = ?
      `;

      const params = [season];

      if (league) {
        sql += ' AND t.league = ?';
        params.push(league);
      }

      if (positionGroup) {
        sql += ' AND p.positionGroup = ?';
        params.push(positionGroup);
      }

      sql += ` ORDER BY ${statField} DESC LIMIT ?`;
      params.push(limit);

      const rows = await db.all(sql, params);

      return rows.map(row => ({
        name: row.name,
        nameEn: row.nameEn,
        position: row.position,
        positionGroup: row.positionGroup,
        teamName: row.team_name,
        teamLeague: row.team_league,
        value: row.value,
        matches: row.matches,
        minutes: row.minutes,
        goals: row.goals,
        assists: row.assists,
        rating: row.rating
      }));
    } catch (err) {
      console.error('获取统计榜单失败:', err.message);
      return [];
    }
  }

  async getStatsOverview({ league, teamId }) {
    try {
      let sql = `
        SELECT 
          COUNT(DISTINCT p.id) as totalPlayers,
          AVG(p.age) as avgAge,
          AVG(p.marketValue) as avgMarketValue,
          SUM(ps.goals) as totalGoals,
          SUM(ps.assists) as totalAssists,
          AVG(ps.rating) as avgRating,
          SUM(ps.matches) as totalMatches,
          SUM(ps.minutes) as totalMinutes
        FROM players p
        LEFT JOIN player_stats ps ON p.id = ps.playerId
        JOIN teams t ON p.teamId = t.id
        WHERE ps.season = '2025'
      `;

      const params = [];

      if (league) {
        sql += ' AND t.league = ?';
        params.push(league);
      }

      if (teamId) {
        sql += ' AND p.teamId = ?';
        params.push(teamId);
      }

      const result = await db.get(sql, params);

      return {
        totalPlayers: result?.totalPlayers || 0,
        avgAge: result?.avgAge ? Math.round(result.avgAge * 10) / 10 : 0,
        avgMarketValue: result?.avgMarketValue ? Math.round(result.avgMarketValue * 100) / 100 : 0,
        totalGoals: result?.totalGoals || 0,
        totalAssists: result?.totalAssists || 0,
        avgRating: result?.avgRating ? Math.round(result.avgRating * 100) / 100 : 0,
        totalMatches: result?.totalMatches || 0,
        totalMinutes: result?.totalMinutes || 0,
        league,
        teamId
      };
    } catch (err) {
      console.error('获取统计概览失败:', err.message);
      return {
        totalPlayers: 0,
        avgAge: 0,
        avgMarketValue: 0,
        totalGoals: 0,
        totalAssists: 0,
        avgRating: 0,
        totalMatches: 0,
        totalMinutes: 0,
        league,
        teamId
      };
    }
  }

  formatPlayerStats(stats) {
    return {
      id: stats.id,
      playerId: stats.playerId,
      season: stats.season,
      matches: stats.matches,
      starts: stats.starts,
      minutes: stats.minutes,
      goals: stats.goals,
      assists: stats.assists,
      xg: stats.xg,
      xA: stats.xA,
      shots: stats.shots,
      shotsOnTarget: stats.shotsOnTarget,
      bigChances: stats.bigChances,
      bigChancesCreated: stats.bigChancesCreated,
      tackles: stats.tackles,
      interceptions: stats.interceptions,
      blocks: stats.blocks,
      duelsWon: stats.duelsWon,
      aerialWon: stats.aerialWon,
      dribblesCompleted: stats.dribblesCompleted,
      dribblesAttempted: stats.dribblesAttempted,
      passes: stats.passes,
      passesCompleted: stats.passesCompleted,
      keyPasses: stats.keyPasses,
      throughBalls: stats.throughBalls,
      crosses: stats.crosses,
      fouls: stats.fouls,
      yellowCards: stats.yellowCards,
      redCards: stats.redCards,
      penaltyGoals: stats.penaltyGoals,
      penaltyMissed: stats.penaltyMissed,
      rating: stats.rating,
      goalsPerGame: stats.matches > 0 ? Math.round(stats.goals / stats.matches * 100) / 100 : 0,
      assistsPerGame: stats.matches > 0 ? Math.round(stats.assists / stats.matches * 100) / 100 : 0,
      shotAccuracy: stats.shots > 0 ? Math.round(stats.shotsOnTarget / stats.shots * 100) : 0,
      passAccuracy: stats.passes > 0 ? Math.round(stats.passesCompleted / stats.passes * 100) : 0,
      dribbleSuccess: stats.dribblesAttempted > 0 ? Math.round(stats.dribblesCompleted / stats.dribblesAttempted * 100) : 0
    };
  }

  async createPlayer(playerData) {
    return await this.dal.create(playerData);
  }

  async createPlayerStats(statsData) {
    return await this.dal.createStats(statsData);
  }
}

export default new PlayerService();