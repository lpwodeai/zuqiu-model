import express from 'express';
// C-20260929-006：鉴权策略收敛——GET 端点保留 optionalAuth（公开只读，便于本地调试与 start_session.ps1 -Check 调用），
// POST 端点改 authenticateToken（强制 JWT），防止公网部署时 model-admin 敏感写操作（reload-all/watch-start 等）被无 token 触发
import { optionalAuth, authenticateToken } from '../middleware/auth.js';
import { asyncHandler, ValidationError } from '../middleware/error-handler.js';
import { modelHotReloader } from '../services/model-hot-reloader.js';
import PredictionService from '../services/prediction-service.js';
import { logger } from '../services/logger.js';

const router = express.Router();

// 本地个人部署：GET 只读端点使用 optionalAuth（无 token 放行），POST 写端点强制 authenticateToken
router.get('/reload/status', optionalAuth, asyncHandler(async (req, res) => {
  const stats = modelHotReloader.getStats();
  res.json({ success: true, data: stats });
}));

router.get('/reload/history', optionalAuth, asyncHandler(async (req, res) => {
  const { limit = 20 } = req.query;
  const history = modelHotReloader.getHistory(parseInt(limit));
  res.json({ success: true, data: history });
}));

router.get('/files', optionalAuth, asyncHandler(async (req, res) => {
  const files = modelHotReloader.getModelFileConfig();
  res.json({ success: true, data: files });
}));

router.post('/reload/:key', authenticateToken, asyncHandler(async (req, res) => {
  const { key } = req.params;
  logger.info('model-admin', `手动触发模型重载: ${key}`);

  try {
    const result = await modelHotReloader.triggerReload(key);
    res.json({
      success: true,
      message: `模型 ${key} 已热更新`,
      data: result
    });
  } catch (err) {
    throw new ValidationError(`重载失败: ${err.message}`);
  }
}));

router.post('/reload-all', authenticateToken, asyncHandler(async (req, res) => {
  logger.info('model-admin', '手动触发全量模型重载');

  const results = await modelHotReloader.reloadAll();

  const successCount = Object.values(results).filter(v => v === 'success').length;
  const failCount = Object.values(results).filter(v => v.startsWith('failed')).length;

  res.json({
    success: true,
    message: `重载完成: 成功${successCount}个, 失败${failCount}个`,
    data: results
  });
}));

router.post('/watch/start', authenticateToken, asyncHandler(async (req, res) => {
  modelHotReloader.startWatching();
  res.json({ success: true, message: '文件监控已启动' });
}));

router.post('/watch/stop', authenticateToken, asyncHandler(async (req, res) => {
  modelHotReloader.stopWatching();
  res.json({ success: true, message: '文件监控已停止' });
}));

router.get('/info', optionalAuth, asyncHandler(async (req, res) => {
  const info = {
    predictionService: {
      teamsCount: Object.keys(PredictionService.teams || {}).length,
      eloRatingsCount: Object.keys(PredictionService.eloRatings || {}).length,
      modelConfigured: !!PredictionService.xgbModel && !!PredictionService.lgbModel
    },
    hotReloader: modelHotReloader.getStats()
  };

  res.json({ success: true, data: info });
}));

export default router;