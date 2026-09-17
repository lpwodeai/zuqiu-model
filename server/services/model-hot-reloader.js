import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';
import { logger } from './logger.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const MODEL_FILES = {
  xgbModel: {
    path: path.join(__dirname, '../../assets/xgb_model_export.js'),
    pattern: /var XGB_MODEL = (\{[\s\S]*\});/,
    type: 'model'
  },
  lgbModel: {
    path: path.join(__dirname, '../../assets/lgb_model_export.js'),
    pattern: /var LGB_MODEL = (\{[\s\S]*\});/,
    type: 'model'
  },
  teamAttributes: {
    path: path.join(__dirname, '../../assets/team_attributes.json'),
    pattern: null,
    type: 'json'
  },
  stackingWeights: {
    path: path.join(__dirname, '../../assets/stacking_weights.json'),
    pattern: null,
    type: 'json'
  },
  leagueTier: {
    path: path.join(__dirname, '../../assets/league_tier.json'),
    pattern: null,
    type: 'json'
  },
  leagueTierWeight: {
    path: path.join(__dirname, '../../assets/league_tier_weight.json'),
    pattern: null,
    type: 'json'
  },
  featureScaler: {
    path: path.join(__dirname, '../../assets/feature_scaler_params.js'),
    pattern: /var FEATURE_SCALER_PARAMS = (\{[\s\S]*\});/,
    type: 'model'
  }
};

class ModelHotReloader {
  constructor() {
    this.watchers = new Map();
    this.lastModified = new Map();
    this.reloadCallbacks = new Set();
    this.reloadHistory = [];
    this.maxHistorySize = 100;
    this.isWatching = false;
    this.pendingReloads = new Map();
    this.stats = {
      totalReloads: 0,
      successfulReloads: 0,
      failedReloads: 0,
      lastReloadTime: null
    };
  }

  startWatching() {
    if (this.isWatching) {
      logger.info('hot-reload', '文件监控已在运行中');
      return;
    }

    this.isWatching = true;

    for (const [key, config] of Object.entries(MODEL_FILES)) {
      if (fs.existsSync(config.path)) {
        this.watchFile(key, config);
        this.lastModified.set(key, fs.statSync(config.path).mtimeMs);
      }
    }

    logger.info('hot-reload', `开始监控 ${this.watchers.size} 个模型文件`);
  }

  stopWatching() {
    for (const [key, watcher] of this.watchers.entries()) {
      try {
        watcher.close();
      } catch (e) {
        // Ignore
      }
    }
    this.watchers.clear();
    this.isWatching = false;
    logger.info('hot-reload', '已停止所有文件监控');
  }

  watchFile(key, config) {
    try {
      const watcher = fs.watch(config.path, (eventType, filename) => {
        if (eventType === 'change') {
          const currentMtime = fs.statSync(config.path).mtimeMs;
          const lastMtime = this.lastModified.get(key);

          if (currentMtime !== lastMtime) {
            this.lastModified.set(key, currentMtime);
            this.scheduleReload(key, config);
          }
        }
      });

      this.watchers.set(key, watcher);
    } catch (err) {
      logger.error('hot-reload', `监控文件失败: ${key}`, {
        path: config.path,
        error: err.message
      });
    }
  }

  scheduleReload(key, config) {
    if (this.pendingReloads.has(key)) {
      clearTimeout(this.pendingReloads.get(key));
    }

    const timer = setTimeout(() => {
      this.pendingReloads.delete(key);
      this.handleFileChange(key, config);
    }, 300);

    this.pendingReloads.set(key, timer);
  }

  handleFileChange(key, config) {
    logger.info('hot-reload', `检测到文件变更: ${key}`, {
      path: config.path,
      time: new Date().toISOString()
    });

    const maxRetries = 3;
    let attempt = 0;

    const tryReload = () => {
      attempt++;
      try {
        const data = this.loadFileData(key, config);
        
        this.stats.totalReloads++;
        this.stats.successfulReloads++;
        this.stats.lastReloadTime = new Date().toISOString();

        this.addToHistory(key, 'success', data);
        this.notifyCallbacks(key, data);

        logger.info('hot-reload', `模型热更新成功: ${key} (第${attempt}次尝试)`);
        return true;
      } catch (err) {
        if (attempt < maxRetries) {
          logger.warn('hot-reload', `热更新重试中: ${key} (第${attempt}次失败: ${err.message})`);
          setTimeout(tryReload, 200 * attempt);
          return false;
        }

        this.stats.totalReloads++;
        this.stats.failedReloads++;
        this.addToHistory(key, 'failed', { error: err.message });
        logger.error('hot-reload', `模型热更新失败: ${key}`, {
          error: err.message,
          stack: err.stack,
          attempts: attempt
        });
        return false;
      }
    };

    tryReload();
  }

  loadFileData(key, config) {
    const content = fs.readFileSync(config.path, 'utf8');

    if (config.type === 'json') {
      return JSON.parse(content);
    }

    if (config.pattern) {
      const match = content.match(config.pattern);
      if (match) {
        try {
          return JSON.parse(match[1]);
        } catch (parseErr) {
          const sandbox = {};
          const script = new vm.Script(`this.value = ${match[1]};`, { filename: config.path });
          const context = vm.createContext(sandbox, {
            codeGeneration: { strings: false, wasm: false }
          });
          script.runInContext(context, { timeout: 1000 });
          return sandbox.value;
        }
      }
      throw new Error(`无法解析文件内容: ${key}`);
    }

    return content;
  }

  addToHistory(key, status, data) {
    const entry = {
      key,
      status,
      timestamp: new Date().toISOString(),
      data: status === 'success' ? { keys: Object.keys(data || {}).slice(0, 5) } : null,
      error: status === 'failed' ? data?.error : null
    };

    this.reloadHistory.unshift(entry);
    
    if (this.reloadHistory.length > this.maxHistorySize) {
      this.reloadHistory.pop();
    }
  }

  onReload(callback) {
    this.reloadCallbacks.add(callback);
    return () => this.reloadCallbacks.delete(callback);
  }

  notifyCallbacks(key, data) {
    for (const callback of this.reloadCallbacks) {
      try {
        callback(key, data);
      } catch (err) {
        logger.error('hot-reload', '回调函数执行失败', { error: err.message });
      }
    }
  }

  getStats() {
    // 构建 6 个模型的加载状态
    const modelStatus = {
      xgb: fs.existsSync(MODEL_FILES.xgbModel.path) ? 'active' : 'inactive',
      lgb: fs.existsSync(MODEL_FILES.lgbModel.path) ? 'active' : 'inactive',
      elo: fs.existsSync(MODEL_FILES.teamAttributes.path) ? 'active' : 'inactive',
      poisson: fs.existsSync(MODEL_FILES.teamAttributes.path) ? 'active' : 'inactive',
      dixonCole: fs.existsSync(MODEL_FILES.teamAttributes.path) ? 'active' : 'inactive',
      ssm: fs.existsSync(MODEL_FILES.teamAttributes.path) ? 'active' : 'inactive'
    };

    return {
      ...this.stats,
      modelStatus,
      watchedFiles: Array.from(this.watchers.keys()),
      historySize: this.reloadHistory.length,
      isWatching: this.isWatching
    };
  }

  getHistory(limit = 20) {
    return this.reloadHistory.slice(0, limit);
  }

  async triggerReload(fileKey) {
    const config = MODEL_FILES[fileKey];
    if (!config) {
      throw new Error(`未知的模型文件: ${fileKey}`);
    }

    if (!fs.existsSync(config.path)) {
      throw new Error(`文件不存在: ${config.path}`);
    }

    this.handleFileChange(fileKey, config);
    return { success: true, key: fileKey };
  }

  async reloadAll() {
    const results = {};
    
    for (const [key, config] of Object.entries(MODEL_FILES)) {
      if (fs.existsSync(config.path)) {
        try {
          this.handleFileChange(key, config);
          results[key] = 'success';
        } catch (err) {
          results[key] = `failed: ${err.message}`;
        }
      } else {
        results[key] = 'skipped (file not found)';
      }
    }

    return results;
  }

  getModelFileConfig() {
    return Object.entries(MODEL_FILES).map(([key, config]) => ({
      key,
      path: config.path,
      exists: fs.existsSync(config.path),
      type: config.type
    }));
  }
}

export const modelHotReloader = new ModelHotReloader();
export default modelHotReloader;