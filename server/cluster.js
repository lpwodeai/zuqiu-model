/**
 * 五大联赛足球预测模型 - Cluster 主进程入口
 *
 * 架构：
 *   主进程 (cluster.js)
 *     ├── fork N workers（默认 CPU 核数，最多 8）
 *     ├── 定时任务（trainScheduler 仅主进程运行）
 *     ├── WebSocket 跨进程广播中转
 *     └── worker 退出自动重启
 *
 *   Worker 进程 (index.js)
 *     ├── Express HTTP 服务
 *     ├── WebSocket 连接
 *     ├── better-sqlite3 (WAL + busy_timeout)
 *     └── Redis 缓存
 *
 * 设计依据：负载测试 v2.0 显示 fork 单进程在并发 200 后吞吐饱和（4144 QPS），
 * cluster 模式可水平扩展至 N × 单进程容量。
 *
 * SQLite WAL 兼容：多进程读并发安全，写通过 busy_timeout=5000ms 排队。
 */

import cluster from 'cluster';
import os from 'os';
import dotenv from 'dotenv';
import { fileURLToPath } from 'url';
import path from 'path';

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = process.env.PORT || 3000;
const WORKER_COUNT = Math.min(
  parseInt(process.env.WORKER_COUNT) || os.cpus().length,
  8
);

// ============ 主进程 ============
if (cluster.isPrimary) {
  console.log('═══════════════════════════════════════════════════════════');
  console.log(`  五大联赛预测模型 v8.0 - Cluster 模式`);
  console.log(`  主进程 PID: ${process.pid}`);
  console.log(`  Worker 数量: ${WORKER_COUNT}`);
  console.log(`  端口: ${PORT}`);
  console.log(`  平台: ${process.platform} | Node: ${process.version}`);
  console.log('═══════════════════════════════════════════════════════════\n');

  // 动态导入主进程专用服务（避免 worker 无谓加载）
  const { db } = await import('./database/index.js');
  const { trainScheduler } = await import('./services/train-scheduler.js');
  const { logger } = await import('./services/logger.js');
  const { reviewService } = await import('./services/review-service.js');

  // 主进程数据库连接（定时任务/统计用）
  try {
    db.connect();
    await db.init();
    console.log('✅ [主进程] 数据库连接成功');
  } catch (err) {
    console.error('❌ [主进程] 数据库连接失败:', err.message);
  }

  // 定时任务只在主进程运行
  reviewService.init().then(() => {
    console.log('✅ [主进程] 复盘服务已初始化');
  }).catch(err => console.error('❌ [主进程] 复盘服务初始化失败:', err.message));

  trainScheduler.init().then(() => {
    return trainScheduler.scheduleTraining();
  }).then(() => {
    console.log('✅ [主进程] 训练调度服务已启动（仅主进程）');
  }).catch(err => {
    console.warn('⚠️ [主进程] 训练调度服务启动失败:', err.message);
  });

  // Fork workers
  const workers = new Map();

  for (let i = 0; i < WORKER_COUNT; i++) {
    const worker = cluster.fork({
      CLUSTER_WORKER: 'true',
      WORKER_ID: String(i + 1),
    });
    workers.set(worker.process.pid, worker);
    console.log(`  → Worker #${i + 1} 已启动 (PID: ${worker.process.pid})`);
  }

  // WebSocket 跨进程广播中转
  // worker 发送 { type: 'broadcast', channel, message } → 主进程转发给所有 worker
  cluster.on('message', (worker, msg) => {
    if (!msg || typeof msg !== 'object') return;

    if (msg.type === 'broadcast') {
      // 转发给所有其他 worker
      for (const [pid, w] of workers) {
        if (pid !== worker.process.pid && w.isConnected()) {
          w.send({ type: 'broadcast', channel: msg.channel, message: msg.message });
        }
      }
    } else if (msg.type === 'worker_ready') {
      console.log(`✅ Worker #${msg.workerId} (PID: ${worker.process.pid}) 已就绪`);
    }
  });

  // worker 退出自动重启
  cluster.on('exit', (worker, code, signal) => {
    const pid = worker.process.pid;
    const workerId = worker.id;
    console.error(`⚠️ Worker (PID: ${pid}) 退出 (code=${code}, signal=${signal})`);

    workers.delete(pid);

    // 延迟 2 秒重启，避免快速崩溃循环
    setTimeout(() => {
      const newWorker = cluster.fork({
        CLUSTER_WORKER: 'true',
        WORKER_ID: String(workerId),
      });
      workers.set(newWorker.process.pid, newWorker);
      console.log(`  → Worker 已重启 (PID: ${newWorker.process.pid})`);
    }, 2000);
  });

  // 优雅关闭
  let shuttingDown = false;
  const shutdown = (signal) => {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`\n📡 收到 ${signal}，开始优雅关闭...`);

    for (const [pid, worker] of workers) {
      if (worker.isConnected()) {
        worker.send({ type: 'shutdown' });
      }
    }

    // 5 秒后强制退出
    setTimeout(() => {
      console.log('⚡ 强制关闭剩余 worker');
      for (const [pid, worker] of workers) {
        worker.process.kill('SIGTERM');
      }
      db.close();
      process.exit(0);
    }, 5000);
  };

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));

  console.log(`\n🚀 Cluster 已启动: ${WORKER_COUNT} workers 监听 :${PORT}\n`);

} else {
  // ============ Worker 进程 ============
  // 加载实际的 Express 服务
  await import('./index.js');
}
