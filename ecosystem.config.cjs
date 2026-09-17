/**
 * PM2 生产环境配置文件 (CommonJS 格式)
 * 五大联赛足球预测模型 v8.0 — Cluster 模式
 *
 * 架构变更 (v2.0): Node.js 内置 cluster 模块实现水平扩展
 *   - PM2 管理 1 个主进程 (cluster.js)，fork N 个 worker (index.js)
 *   - exec_mode 保持 'fork' + instances: 1（PM2 管主进程，cluster 在代码内 fork worker）
 *   - worker 数量通过 WORKER_COUNT 环境变量控制（默认 CPU 核数，最多 8）
 *   - SQLite WAL + busy_timeout=5000ms 兼容多进程写
 *   - 定时任务仅在主进程运行，WebSocket 广播通过 IPC 中转
 *
 * 使用方法:
 *   开发环境:  pm2 start ecosystem.config.cjs --env development
 *   生产环境:  pm2 start ecosystem.config.cjs --env production
 *   一键启动:  pm2 start ecosystem.config.cjs
 *
 * 常用命令:
 *   pm2 start ecosystem.config.cjs        # 启动
 *   pm2 reload ecosystem.config.cjs       # 零停机重启
 *   pm2 restart ecosystem.config.cjs      # 完全重启
 *   pm2 stop ecosystem.config.cjs         # 停止
 *   pm2 delete ecosystem.config.cjs       # 删除
 *   pm2 monit                            # 实时监控
 *   pm2 logs five-leagues --lines 200     # 查看日志
 */

const path = require('path');
const os = require('os');

module.exports = {
  apps: [
    {
      name: 'five-leagues',

      // ===== 基础配置 =====
      // 入口改为 cluster.js（主进程），由它 fork worker 运行 index.js
      script: 'server/cluster.js',
      cwd: __dirname,
      interpreter: 'node',
      // Node 22+: VM Modules 已稳定，无需 --experimental-vm-modules
      // interpreter_args: '--experimental-vm-modules',
      // exec_mode 保持 fork + instances: 1：PM2 只管主进程，worker 由 cluster.js fork
      // 这是 SQLite WAL 兼容性要求（避免 PM2 cluster 模式在 Windows 上的端口冲突）
      exec_mode: 'fork',
      instances: 1,

      // ===== 环境变量 =====
      env: {
        NODE_ENV: 'development',
        PORT: 3000,
        CLIENT_URL: 'http://localhost:5173',
        // Worker 数量：默认 CPU 核数，开发环境限制为 2 避免资源浪费
        WORKER_COUNT: 2,
      },
      env_production: {
        NODE_ENV: 'production',
        PORT: 3000,
        CLIENT_URL: 'https://your-domain.com',
        // 生产环境：默认 CPU 核数（cluster.js 内最多 8），可通过环境变量覆盖
        // WORKER_COUNT: 4,
      },

      // ===== 进程管理 =====
      autorestart: true,
      watch: false,
      ignore_watch: [
        'node_modules',
        'logs',
        'data',
        '.git',
        '*.log',
        'assets',
      ],
      max_memory_restart: '1G',
      min_uptime: '10s',
      max_restarts: 10,
      restart_delay: 4000,

      // ===== 优雅关闭 =====
      kill_timeout: 5000,
      listen_timeout: 10000,

      // ===== 日志配置 =====
      error_file: path.join(__dirname, 'logs', 'error.log'),
      out_file: path.join(__dirname, 'logs', 'out.log'),
      log_file: path.join(__dirname, 'logs', 'combined.log'),
      log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
      merge_logs: true,
    },
  ],
};

/**
 * ═══════════════════════════════════════════════════════════
 * 部署完成后执行:
 * ═══════════════════════════════════════════════════════════
 *
 * # 1. 创建日志目录
 * sudo mkdir -p logs
 * sudo chown -R $USER:$USER logs
 *
 * # 2. 启动生产环境
 * pm2 start ecosystem.config.cjs --env production
 *
 * # 3. 保存进程列表（开机自启）
 * pm2 save
 *
 * # 4. 设置 PM2 系统服务（首次使用时）
 * pm2 startup systemd
 * # 按提示执行返回的命令
 *
 * # 5. 验证服务
 * curl http://localhost:3000/api/health
 *
 * # 6. 查看状态
 * pm2 status
 * pm2 logs five-leagues --lines 50
 */