import { db } from '../database/index.js';

export class Logger {
  constructor() {
    this.levels = ['debug', 'info', 'warn', 'error'];
  }

  async log(level, category, message, details = null, source = null) {
    if (!this.levels.includes(level)) {
      level = 'info';
    }

    const sql = `
      INSERT INTO logs (level, category, message, details, source, timestamp)
      VALUES (?, ?, ?, ?, ?, ?)
    `;

    const params = [
      level,
      category,
      message,
      details ? JSON.stringify(details) : null,
      source,
      new Date().toISOString()
    ];

    try {
      if (db.db) {
        await db.run(sql, params);
      }
      console.log(`[${level.toUpperCase()}] [${category}] ${message}`);
    } catch (err) {
      console.error('日志写入失败:', err.message);
    }
  }

  async debug(category, message, details = null, source = null) {
    await this.log('debug', category, message, details, source);
  }

  async info(category, message, details = null, source = null) {
    await this.log('info', category, message, details, source);
  }

  async warn(category, message, details = null, source = null) {
    await this.log('warn', category, message, details, source);
  }

  async error(category, message, details = null, source = null) {
    await this.log('error', category, message, details, source);
  }

  async getLogs(filters = {}) {
    let sql = 'SELECT * FROM logs';
    const params = [];
    const conditions = [];

    if (filters.level) {
      conditions.push('level = ?');
      params.push(filters.level);
    }

    if (filters.category) {
      conditions.push('category = ?');
      params.push(filters.category);
    }

    if (filters.startTime) {
      conditions.push('timestamp >= ?');
      params.push(filters.startTime);
    }

    if (filters.endTime) {
      conditions.push('timestamp <= ?');
      params.push(filters.endTime);
    }

    if (filters.search) {
      conditions.push('(message LIKE ? OR details LIKE ?)');
      params.push(`%${filters.search}%`, `%${filters.search}%`);
    }

    if (conditions.length > 0) {
      sql += ' WHERE ' + conditions.join(' AND ');
    }

    sql += ' ORDER BY timestamp DESC';

    const page = filters.page || 1;
    const limit = filters.limit || 50;
    const offset = (page - 1) * limit;

    sql += ' LIMIT ? OFFSET ?';
    params.push(limit, offset);

    const rows = await db.all(sql, params);

    const countSql = sql.replace(/SELECT.*FROM/, 'SELECT COUNT(*) as total FROM').replace(/ORDER BY.*/, '').replace(/LIMIT.*/, '');
    const count = await db.get(countSql, params.slice(0, -2));

    return {
      data: rows.map(row => ({
        ...row,
        details: row.details ? JSON.parse(row.details) : null
      })),
      total: count?.total || 0,
      page,
      limit
    };
  }

  async getStats() {
    const stats = await db.all(`
      SELECT level, category, COUNT(*) as count
      FROM logs
      GROUP BY level, category
      ORDER BY count DESC
    `);

    const total = await db.get('SELECT COUNT(*) as total FROM logs');
    const today = await db.get(`
      SELECT COUNT(*) as count FROM logs 
      WHERE timestamp >= datetime('now', 'start of day')
    `);

    return {
      total: total?.total || 0,
      today: today?.count || 0,
      byLevel: stats.reduce((acc, row) => {
        acc[row.level] = (acc[row.level] || 0) + row.count;
        return acc;
      }, {}),
      byCategory: stats.reduce((acc, row) => {
        acc[row.category] = (acc[row.category] || 0) + row.count;
        return acc;
      }, {})
    };
  }

  async clearLogs(daysToKeep = 30) {
    const cutoffDate = new Date();
    cutoffDate.setDate(cutoffDate.getDate() - daysToKeep);

    const sql = 'DELETE FROM logs WHERE timestamp < ?';
    const result = await db.run(sql, [cutoffDate.toISOString()]);

    return { deleted: result.changes || 0 };
  }

  async exportLogs(format = 'json', filters = {}) {
    const { data } = await this.getLogs({ ...filters, limit: 10000 });

    if (format === 'csv') {
      const headers = ['id', 'timestamp', 'level', 'category', 'message', 'details', 'source'];
      const rows = data.map(row => [
        row.id,
        row.timestamp,
        row.level,
        row.category,
        `"${row.message.replace(/"/g, '""')}"`,
        row.details ? `"${JSON.stringify(row.details).replace(/"/g, '""')}"` : '',
        row.source || ''
      ]);

      return headers.join(',') + '\n' + rows.map(r => r.join(',')).join('\n');
    }

    return JSON.stringify(data, null, 2);
  }
}

export const logger = new Logger();

export default logger;