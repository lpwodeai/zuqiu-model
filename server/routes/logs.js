import express from 'express';
import { logger } from '../services/logger.js';

const router = express.Router();

router.get('/', async (req, res) => {
  try {
    const { level, category, startTime, endTime, search, page = 1, limit = 50 } = req.query;
    
    const filters = {};
    if (level) filters.level = level;
    if (category) filters.category = category;
    if (startTime) filters.startTime = startTime;
    if (endTime) filters.endTime = endTime;
    if (search) filters.search = search;
    filters.page = parseInt(page);
    filters.limit = parseInt(limit);

    const result = await logger.getLogs(filters);
    
    res.json({
      success: true,
      ...result,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      error: { message: err.message, code: 'LOGS_ERROR' }
    });
  }
});

router.get('/stats', async (req, res) => {
  try {
    const stats = await logger.getStats();
    
    res.json({
      success: true,
      data: stats,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      error: { message: err.message, code: 'STATS_ERROR' }
    });
  }
});

router.post('/log', async (req, res) => {
  try {
    const { level = 'info', category, message, details, source } = req.body;

    if (!category || !message) {
      return res.status(400).json({
        success: false,
        error: { message: '请提供日志分类和消息', code: 'MISSING_PARAMS' }
      });
    }

    await logger.log(level, category, message, details, source);

    res.json({
      success: true,
      message: '日志记录成功',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      error: { message: err.message, code: 'LOG_ERROR' }
    });
  }
});

router.get('/export', async (req, res) => {
  try {
    const { format = 'json', level, category } = req.query;

    const filters = {};
    if (level) filters.level = level;
    if (category) filters.category = category;

    const data = await logger.exportLogs(format, filters);

    if (format === 'csv') {
      res.setHeader('Content-Type', 'text/csv');
      res.setHeader('Content-Disposition', 'attachment; filename=logs.csv');
    } else {
      res.setHeader('Content-Type', 'application/json');
      res.setHeader('Content-Disposition', 'attachment; filename=logs.json');
    }

    res.send(data);
  } catch (err) {
    res.status(500).json({
      success: false,
      error: { message: err.message, code: 'EXPORT_ERROR' }
    });
  }
});

router.delete('/clear', async (req, res) => {
  try {
    const { daysToKeep = 30 } = req.query;
    
    const result = await logger.clearLogs(parseInt(daysToKeep));

    await logger.info('system', `日志清理完成，删除 ${result.deleted} 条记录`, { daysToKeep });

    res.json({
      success: true,
      message: `成功删除 ${result.deleted} 条旧日志`,
      deleted: result.deleted,
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      error: { message: err.message, code: 'CLEAR_ERROR' }
    });
  }
});

router.post('/test', async (req, res) => {
  try {
    await logger.debug('test', '调试日志测试');
    await logger.info('test', '信息日志测试');
    await logger.warn('test', '警告日志测试');
    await logger.error('test', '错误日志测试', { errorCode: 123, details: '测试详情' });

    res.json({
      success: true,
      message: '日志测试完成，已记录4条测试日志',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      success: false,
      error: { message: err.message, code: 'TEST_ERROR' }
    });
  }
});

export default router;