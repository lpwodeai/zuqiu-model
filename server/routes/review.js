import express from 'express';
import { reviewService } from '../services/review-service.js';
import { trainScheduler } from '../services/train-scheduler.js';
import { authenticateToken } from '../middleware/auth.js';
import ApiResponse from '../../shared/api-response.js';

const router = express.Router();

router.post('/', authenticateToken, async (req, res) => {
  try {
    const result = await reviewService.saveReview(req.body);
    res.json(ApiResponse.success(result, '复盘数据已保存'));
  } catch (err) {
    res.status(400).json(ApiResponse.error(err.message, 'REVIEW_SAVE_ERROR'));
  }
});

router.post('/batch', authenticateToken, async (req, res) => {
  try {
    const { reviews } = req.body;
    if (!reviews || !Array.isArray(reviews)) {
      return res.status(400).json(ApiResponse.validationError('请提供复盘数据数组', 'INVALID_REVIEWS'));
    }
    
    const result = await reviewService.saveBatchReviews(reviews);
    res.json(ApiResponse.success(result, '批量保存完成'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'REVIEW_BATCH_ERROR'));
  }
});

router.get('/:matchId', async (req, res) => {
  try {
    const { matchId } = req.params;
    const review = await reviewService.getReview(matchId);
    
    if (!review) {
      return res.status(404).json(ApiResponse.notFound('复盘数据不存在', 'REVIEW_NOT_FOUND'));
    }
    
    res.json(ApiResponse.success(review, '获取成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'REVIEW_GET_ERROR'));
  }
});

router.get('/team/:teamName', async (req, res) => {
  try {
    const { teamName } = req.params;
    const { limit = 20, offset = 0 } = req.query;
    const reviews = await reviewService.getReviewsByTeam(teamName, limit, offset);
    res.json(ApiResponse.success(reviews, '获取成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'REVIEW_GET_ERROR'));
  }
});

router.get('/recent/list', async (req, res) => {
  try {
    const { limit = 50 } = req.query;
    const reviews = await reviewService.getRecentReviews(limit);
    res.json(ApiResponse.success(reviews, '获取成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'REVIEW_GET_ERROR'));
  }
});

router.get('/stats', async (req, res) => {
  try {
    const stats = await reviewService.getReviewStats();
    res.json(ApiResponse.success(stats, '获取成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'REVIEW_STATS_ERROR'));
  }
});

router.get('/model/versions', async (req, res) => {
  try {
    const { limit = 10 } = req.query;
    const versions = await reviewService.getModelVersions(limit);
    res.json(ApiResponse.success(versions, '获取成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'MODEL_VERSION_ERROR'));
  }
});

router.get('/model/latest', async (req, res) => {
  try {
    const version = await reviewService.getLatestModelVersion();
    res.json(ApiResponse.success(version, '获取成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'MODEL_VERSION_ERROR'));
  }
});

router.post('/model/versions', authenticateToken, async (req, res) => {
  try {
    const result = await reviewService.saveModelVersion(req.body);
    res.json(ApiResponse.success(result, '模型版本已保存'));
  } catch (err) {
    res.status(400).json(ApiResponse.error(err.message, 'MODEL_VERSION_SAVE_ERROR'));
  }
});

router.patch('/model/versions/:version/deploy', authenticateToken, async (req, res) => {
  try {
    const { version } = req.params;
    const { deployed } = req.body;
    
    const result = await reviewService.setModelDeployed(version, deployed);
    res.json(ApiResponse.success(result, '部署状态已更新'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'MODEL_DEPLOY_ERROR'));
  }
});

router.post('/train/trigger', authenticateToken, async (req, res) => {
  try {
    const result = await trainScheduler.triggerTraining();
    
    if (result.success) {
      res.json(ApiResponse.success(result, '训练任务已触发'));
    } else {
      res.status(503).json(ApiResponse.error(result.message || '训练失败', 'TRAIN_ERROR'));
    }
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'TRAIN_ERROR'));
  }
});

router.get('/train/status', async (req, res) => {
  try {
    const status = await trainScheduler.getStatus();
    res.json(ApiResponse.success(status, '获取成功'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'TRAIN_STATUS_ERROR'));
  }
});

router.post('/train/check', async (req, res) => {
  try {
    const shouldTrain = await trainScheduler.shouldTrain();
    res.json(ApiResponse.success({ shouldTrain }, '检查完成'));
  } catch (err) {
    res.status(500).json(ApiResponse.error(err.message, 'TRAIN_CHECK_ERROR'));
  }
});

export default router;
