import { logger } from './logger.js';

// 内存缓存优化 v2.0：LRU 淘汰 + 主动过期清理
// 变更: C-20260817-221 (方案1+2: LRU淘汰 + maxSize + 主动过期清理)

const DEFAULT_MAX_SIZE = 1000;
const CLEANUP_INTERVAL_MS = 60000; // 60秒主动清理

export class MemoryCache {
  constructor(maxSize = DEFAULT_MAX_SIZE) {
    this.cache = new Map();
    this.maxSize = maxSize;
    this.accessOrder = []; // LRU 队列（最近使用的在末尾）
    this.evictionCount = 0;
    this.cleanupCount = 0;

    this.stats = {
      hits: 0,
      misses: 0,
      sets: 0,
      invalidations: 0
    };

    // 启动主动过期清理定时器
    this._startCleanupTimer();
  }

  getKey(type, ...parts) {
    const prefix = `five_leagues:${type}`;
    const keyParts = parts.map(p => String(p).toLowerCase().replace(/\s+/g, '_'));
    return `${prefix}:${keyParts.join(':')}`;
  }

  async get(key) {
    const item = this.cache.get(key);
    if (item) {
      // 惰性过期检查
      if (item.expiresAt && Date.now() > item.expiresAt) {
        this.cache.delete(key);
        this._removeFromLRU(key);
        this.stats.misses++;
        return null;
      }
      // 更新 LRU 访问顺序
      this._touchLRU(key);
      this.stats.hits++;
      return JSON.parse(item.value);
    }
    this.stats.misses++;
    return null;
  }

  async set(key, value, ttl) {
    // LRU 淘汰：达到上限时移除最久未使用的条目
    if (this.cache.size >= this.maxSize) {
      const oldest = this.accessOrder.shift();
      if (oldest && this.cache.has(oldest)) {
        this.cache.delete(oldest);
        this.evictionCount++;
        logger.debug('memory-cache', 'LRU淘汰', { key: oldest, evictionCount: this.evictionCount });
      }
    }

    this.cache.set(key, {
      value: JSON.stringify(value),
      expiresAt: ttl ? Date.now() + ttl * 1000 : null
    });

    // 更新 LRU 访问顺序
    this._removeFromLRU(key);
    this.accessOrder.push(key);
    this.stats.sets++;
  }

  async del(key) {
    this.cache.delete(key);
    this._removeFromLRU(key);
    this.stats.invalidations++;
  }

  async keys(pattern) {
    const regex = new RegExp(pattern.replace(/\*/g, '.*'));
    return Array.from(this.cache.keys()).filter(key => regex.test(key));
  }

  async flushAll() {
    this.cache.clear();
    this.accessOrder = [];
    this.evictionCount = 0;
    this.cleanupCount = 0;
    this.stats = { hits: 0, misses: 0, sets: 0, invalidations: 0 };
  }

  async getStats() {
    const total = this.stats.hits + this.stats.misses;
    const hitRate = total > 0 ? (this.stats.hits / total * 100).toFixed(2) : 0;
    return {
      ...this.stats,
      hitRate: `${hitRate}%`,
      total: total,
      size: this.cache.size,
      maxSize: this.maxSize,
      evictionCount: this.evictionCount,
      cleanupCount: this.cleanupCount,
      type: 'memory'
    };
  }

  // ── 内部方法 ──

  /** 更新 LRU 访问顺序（将 key 移到末尾） */
  _touchLRU(key) {
    this._removeFromLRU(key);
    this.accessOrder.push(key);
  }

  /** 从 LRU 队列中移除 key */
  _removeFromLRU(key) {
    const idx = this.accessOrder.indexOf(key);
    if (idx !== -1) {
      this.accessOrder.splice(idx, 1);
    }
  }

  /** 启动主动过期清理定时器 */
  _startCleanupTimer() {
    if (this._cleanupTimer) return;
    this._cleanupTimer = setInterval(() => {
      const now = Date.now();
      let cleaned = 0;
      for (const [key, item] of this.cache) {
        if (item.expiresAt && now > item.expiresAt) {
          this.cache.delete(key);
          this._removeFromLRU(key);
          cleaned++;
        }
      }
      if (cleaned > 0) {
        this.cleanupCount += cleaned;
        logger.debug('memory-cache', '主动过期清理', { cleaned, total: this.cache.size, cleanupCount: this.cleanupCount });
      }
    }, CLEANUP_INTERVAL_MS);

    // 防止定时器阻止进程退出
    if (this._cleanupTimer.unref) {
      this._cleanupTimer.unref();
    }
  }

  /** 停止清理定时器（用于测试或优雅关闭） */
  stopCleanup() {
    if (this._cleanupTimer) {
      clearInterval(this._cleanupTimer);
      this._cleanupTimer = null;
    }
  }
}

export const memoryCache = new MemoryCache();