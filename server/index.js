/**
 * 五大联赛足球预测模型 - 后端API服务器
 * Express.js + WebSocket + JWT认证 + better-sqlite3 + 模型热更新
 */

import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import { WebSocketServer } from 'ws';
import http from 'http';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

import predictionRoutes from './routes/predictions.js';
import teamRoutes from './routes/teams.js';
import oddsRoutes from './routes/odds.js';
import authRoutes from './routes/auth.js';
import dataRoutes from './routes/data.js';
import leagueRoutes from './routes/leagues.js';
import databaseRoutes from './routes/database.js';
import logsRoutes from './routes/logs.js';
import reviewRoutes from './routes/review.js';
import playerRoutes from './routes/players.js';
import modelAdminRoutes from './routes/model-admin.js';
import abTestRoutes from './routes/ab-test.js';

import PredictionService from './services/prediction-service.js';
import DataService from './services/data-service.js';
import { generateValidationReport } from './services/league-rules.js';
import { db } from './database/index.js';
import { logger } from './services/logger.js';
import { cacheService } from './services/cache-service.js';
import { rateLimit } from './middleware/rate-limit.js';
import { reviewService } from './services/review-service.js';
import { trainScheduler } from './services/train-scheduler.js';
import {
  globalErrorHandler,
  notFoundHandler,
  requestLogger,
  setupUncaughtErrorHandlers,
  SQLInjectionGuard
} from './middleware/error-handler.js';
import ApiResponse from '../shared/api-response.js';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

app.use(cors({
  origin: process.env.CLIENT_URL || 'http://localhost:5173',
  credentials: true
}));
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));
app.use(requestLogger);
app.use(SQLInjectionGuard);

app.use('/api/predict', rateLimit);
app.use('/api/odds', rateLimit);

app.use('/api/auth', authRoutes);
app.use('/api/predict', predictionRoutes);
app.use('/api/teams', teamRoutes);
app.use('/api/odds', oddsRoutes);
app.use('/api/data', dataRoutes);
app.use('/api/leagues', leagueRoutes);
app.use('/api/db', databaseRoutes);
app.use('/api/logs', logsRoutes);
app.use('/api/review', reviewRoutes);
app.use('/api/players', playerRoutes);
app.use('/api/model', modelAdminRoutes);
app.use('/api/abtest', abTestRoutes);

app.get('/api/health', (req, res) => {
  const dbStats = db.getDatabaseStats();
  const hotReloadStats = {
    isWatching: PredictionService.engine ? true : false
  };
  
  res.json({
    status: 'healthy',
    version: '7.5.0',
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
    database: {
      connected: db.db !== null,
      tables: Object.keys(dbStats).length,
      totalRecords: Object.values(dbStats).reduce((a, b) => a + b, 0)
    },
    models: {
      xgbLoaded: !!PredictionService.xgbModel && !!PredictionService.xgbModel.trees,
      lgbLoaded: !!PredictionService.lgbModel && !!PredictionService.lgbModel.trees,
      teamsCount: Object.keys(PredictionService.teams || {}).length
    }
  });
});

app.get('/api/cache/stats', async (req, res) => {
  const stats = await cacheService.getStats();
  res.json(ApiResponse.success(stats, '缓存统计'));
});

app.delete('/api/cache/predictions', async (req, res) => {
  await cacheService.invalidateAllPredictions();
  res.json(ApiResponse.success(null, '所有预测缓存已失效'));
});

if (process.env.NODE_ENV === 'production') {
  const distPath = path.join(__dirname, '../client/dist');
  if (fs.existsSync(distPath)) {
    app.use(express.static(distPath));
    app.get('*', (req, res, next) => {
      if (req.path.startsWith('/api/')) {
        return next();
      }
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }
}

app.use(notFoundHandler);
app.use(globalErrorHandler);

const clients = new Set();

wss.on('connection', (ws) => {
  console.log('WebSocket客户端已连接');
  clients.add(ws);

  const heartbeatInterval = setInterval(() => {
    if (ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'heartbeat', timestamp: Date.now() }));
    }
  }, 30000);

  ws.on('message', async (message) => {
    try {
      const data = JSON.parse(message);
      
      switch (data.type) {
        case 'predict':
          const prediction = await PredictionService.predict(data.match);
          ws.send(JSON.stringify({ type: 'prediction', data: prediction }));
          break;
        
        case 'subscribe':
          ws.subscription = data.matchId;
          break;
        
        case 'unsubscribe':
          ws.subscription = null;
          break;
        
        case 'heartbeat':
          break;
      }
    } catch (err) {
      ws.send(JSON.stringify({ type: 'error', message: err.message }));
    }
  });

  ws.on('close', () => {
    clearInterval(heartbeatInterval);
    clients.delete(ws);
    console.log('WebSocket客户端已断开');
  });
});

async function broadcastOddsUpdate(matchId, oddsData) {
  const message = JSON.stringify({ type: 'odds_update', matchId, data: oddsData });

  // Cluster 模式：先向本 worker 的客户端广播，再通过 IPC 转发给其他 worker
  clients.forEach(client => {
    if (client.subscription === matchId && client.readyState === 1) {
      client.send(message);
    }
  });

  // 如果是 cluster worker，通过主进程中转给其他 worker
  if (process.env.CLUSTER_WORKER && typeof process.send === 'function') {
    process.send({ type: 'broadcast', channel: 'odds_update', message: { matchId, data: oddsData } });
  }
}

// Cluster 模式：接收主进程转发的广播消息，向本 worker 的 WebSocket 客户端发送
if (process.env.CLUSTER_WORKER && typeof process.on === 'function') {
  process.on('message', (msg) => {
    if (!msg || typeof msg !== 'object') return;

    if (msg.type === 'broadcast' && msg.channel === 'odds_update') {
      const { matchId, data } = msg.message;
      const wsMessage = JSON.stringify({ type: 'odds_update', matchId, data });
      clients.forEach(client => {
        if (client.subscription === matchId && client.readyState === 1) {
          client.send(wsMessage);
        }
      });
    } else if (msg.type === 'shutdown') {
      console.log(`📡 Worker #${process.env.WORKER_ID} 收到关闭信号，优雅退出...`);
      server.close(() => {
        db.close();
        process.exit(0);
      });
      // 5 秒超时强制退出
      setTimeout(() => process.exit(1), 5000);
    }
  });
}

setupUncaughtErrorHandlers(server);

const PORT = process.env.PORT || 3000;

async function startServer() {
  try {
    db.connect();
    await db.init();
    console.log('✅ 数据库连接成功 (better-sqlite3)');
    await logger.info('system', '数据库连接成功 (better-sqlite3)');
  } catch (err) {
    console.error('❌ 数据库连接失败:', err.message);
    await logger.error('system', '数据库连接失败', { error: err.message });
  }

  // 初始化预测服务（异步加载模型 + 引擎配置 + 热更新）
  try {
    await PredictionService.init();
  } catch (err) {
    console.error('❌ 预测服务初始化失败:', err.message);
  }

  cacheService.connect().then(() => {
    console.log('✅ Redis缓存服务已启动');
  }).catch((err) => {
    console.warn('⚠️ Redis缓存服务连接失败:', err.message);
  });

  reviewService.init().then(() => {
    console.log('✅ 复盘服务已初始化');
  }).catch((err) => {
    console.error('❌ 复盘服务初始化失败:', err.message);
  });

  // 定时任务只在主进程运行（cluster 模式下 worker 跳过）
  if (!process.env.CLUSTER_WORKER) {
    trainScheduler.init().then(() => {
      return trainScheduler.scheduleTraining();
    }).then(() => {
      console.log('✅ 训练调度服务已启动');
    }).catch((err) => {
      console.warn('⚠️ 训练调度服务启动失败:', err.message);
    });
  }

  server.listen(PORT, () => {
    const workerTag = process.env.CLUSTER_WORKER ? ` [Worker #${process.env.WORKER_ID}]` : '';
    console.log(`🚀 五大联赛足球预测模型 v8.0 已启动${workerTag}`);
    console.log(`📍 API服务器: http://localhost:${PORT}`);
    console.log(`📍 WebSocket: ws://localhost:${PORT}`);
    console.log(`📍 健康检查: http://localhost:${PORT}/api/health`);
    console.log(`📍 模型管理: http://localhost:${PORT}/api/model/reload/status`);
    console.log(`📍 数据库API: http://localhost:${PORT}/api/db`);
    console.log(`📍 日志API: http://localhost:${PORT}/api/logs`);
    console.log(`📍 全局错误处理: 已启用`);
    console.log(`📍 模型热更新: 已启用`);

    logger.info('system', '服务器启动成功', { port: PORT, version: '7.5.0', worker: process.env.WORKER_ID || 'primary' });

    // Cluster worker 通知主进程已就绪
    if (process.env.CLUSTER_WORKER && typeof process.send === 'function') {
      process.send({ type: 'worker_ready', workerId: process.env.WORKER_ID });
    }

    if (!process.env.CLUSTER_WORKER) {
      const validationReport = generateValidationReport();
      console.log(`\n📊 联赛规则校验报告:`);
      console.log(`   - 总检查项: ${validationReport.totalChecks}`);
      console.log(`   - 通过: ${validationReport.passedChecks}`);
      console.log(`   - 失败: ${validationReport.failedChecks}`);
      console.log(`   - 总体通过率: ${validationReport.overallPassRate}%`);

      for (const [leagueId, summary] of Object.entries(validationReport.summary)) {
        console.log(`   - ${summary.name}: ${summary.passRate}% (${summary.status})`);
      }
    }
  });
}

startServer();

export { app, wss, broadcastOddsUpdate };