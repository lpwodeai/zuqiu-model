import { createClient } from 'redis';
import { logger } from './logger.js';
import { memoryCache } from './memory-cache.js';

const REDIS_CONFIG = {
  host: process.env.REDIS_HOST || 'localhost',
  port: process.env.REDIS_PORT || 6379,
  password: process.env.REDIS_PASSWORD || undefined,
  db: process.env.REDIS_DB || 0
};

const CACHE_TTL = {
  prediction: process.env.CACHE_TTL_PREDICTION ? parseInt(process.env.CACHE_TTL_PREDICTION) : 7200,
  teams: process.env.CACHE_TTL_TEAMS ? parseInt(process.env.CACHE_TTL_TEAMS) : 86400,
  odds: process.env.CACHE_TTL_ODDS ? parseInt(process.env.CACHE_TTL_ODDS) : 3600,
  rateLimit: process.env.CACHE_TTL_RATE_LIMIT ? parseInt(process.env.CACHE_TTL_RATE_LIMIT) : 60
};

const RATE_LIMIT_CONFIG = {
  maxRequests: process.env.RATE_LIMIT_MAX ? parseInt(process.env.RATE_LIMIT_MAX) : 100,
  windowSeconds: process.env.RATE_LIMIT_WINDOW ? parseInt(process.env.RATE_LIMIT_WINDOW) : 60,
  burstLimit: process.env.RATE_LIMIT_BURST ? parseInt(process.env.RATE_LIMIT_BURST) : 10
};

export class CacheService {
  constructor() {
    this.client = null;
    this.isConnected = false;
    this.cacheType = 'memory';
    this.rateLimitConfig = RATE_LIMIT_CONFIG;
    this.cacheTtl = CACHE_TTL;
  }

  async connect() {
    const connectStart = Date.now();
    try {
      this.client = createClient({
        url: `redis://${REDIS_CONFIG.password ? `${REDIS_CONFIG.password}@` : ''}${REDIS_CONFIG.host}:${REDIS_CONFIG.port}/${REDIS_CONFIG.db}`,
        socket: {
          connectTimeout: 3000,
          reconnectStrategy: (retries) => {
            if (retries > 5) {
              this.switchToMemoryCache();
              return new Error('Redis重连次数过多，切换到内存缓存');
            }
            return Math.min(retries * 1000, 5000);
          }
        }
      });

      let errorLogged = false;
      let reconnectCount = 0;
      const maxReconnectAttempts = 5;

      this.client.on('error', (err) => {
        if (!errorLogged) {
          console.error('Redis连接错误:', err.message);
          logger.error('cache', 'Redis连接错误', { error: err.message });
          errorLogged = true;
        }
        this.isConnected = false;
      });

      this.client.on('connect', () => {
        console.log('✅ Redis连接成功');
        logger.info('cache', 'Redis连接成功');
        this.isConnected = true;
        this.cacheType = 'redis';
        errorLogged = false;
        reconnectCount = 0;
      });

      this.client.on('reconnecting', () => {
        reconnectCount++;
        if (reconnectCount <= maxReconnectAttempts) {
          console.log(`Redis正在重连... (第${reconnectCount}次)`);
          logger.info('cache', 'Redis正在重连', { attempt: reconnectCount });
        }
      });

      this.client.on('end', () => {
        console.log('Redis连接已断开，切换到内存缓存');
        logger.warn('cache', 'Redis连接已断开，切换到内存缓存');
        this.isConnected = false;
        this.switchToMemoryCache();
      });

      await this.client.connect();
      const connectTime = Date.now() - connectStart;
      console.log(`✅ Redis连接成功 (耗时: ${connectTime}ms)`);
      logger.info('cache', `[Redis连接] 耗时 ${connectTime}ms`, { connectTimeMs: connectTime });
      return this.client;
    } catch (err) {
      console.warn('Redis连接失败，使用内存缓存:', err.message);
      logger.warn('cache', 'Redis连接失败，使用内存缓存', { error: err.message });
      this.switchToMemoryCache();
      return null;
    }
  }

  switchToMemoryCache() {
    this.cacheType = 'memory';
    this.isConnected = false;
    this.client = null;
    console.log('📦 缓存模式已切换为内存缓存');
    logger.info('cache', '缓存模式已切换为内存缓存');
  }

  async disconnect() {
    if (this.client) {
      await this.client.quit();
    }
    this.isConnected = false;
    this.cacheType = 'memory';
  }

  getKey(type, ...parts) {
    const prefix = `five_leagues:${type}`;
    const keyParts = parts.map(p => String(p).toLowerCase().replace(/\s+/g, '_'));
    return `${prefix}:${keyParts.join(':')}`;
  }

  async getPrediction(homeTeam, awayTeam, options = {}) {
    const key = this.getKey('prediction', homeTeam, awayTeam, JSON.stringify(options));
    const getStart = Date.now();
    let result = null;
    let usedCacheType = this.cacheType;
    
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        const cached = await this.client.get(key);
        if (cached) {
          result = JSON.parse(cached);
        }
      } catch (err) {
        logger.error('cache', '获取预测缓存失败', { key, error: err.message });
        result = await memoryCache.get(key);
        usedCacheType = 'memory(fallback)';
      }
    } else {
      result = await memoryCache.get(key);
    }
    
    const elapsed = Date.now() - getStart;
    const hit = result !== null;
    logger.info('cache', `[缓存-读取] ${homeTeam} vs ${awayTeam} | ${hit ? 'HIT' : 'MISS'} | type=${usedCacheType} | 耗时 ${elapsed}ms`);
    
    return result;
  }

  async setPrediction(homeTeam, awayTeam, options, prediction) {
    const key = this.getKey('prediction', homeTeam, awayTeam, JSON.stringify(options));
    const setStart = Date.now();
    let usedCacheType = this.cacheType;
    
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        await this.client.set(key, JSON.stringify(prediction), {
          EX: CACHE_TTL.prediction
        });
      } catch (err) {
        logger.error('cache', '设置预测缓存失败', { key, error: err.message });
        await memoryCache.set(key, prediction, CACHE_TTL.prediction);
        usedCacheType = 'memory(fallback)';
      }
    } else {
      await memoryCache.set(key, prediction, CACHE_TTL.prediction);
    }
    
    const elapsed = Date.now() - setStart;
    logger.info('cache', `[缓存-写入] ${homeTeam} vs ${awayTeam} | type=${usedCacheType} | TTL=${CACHE_TTL.prediction}s | 耗时 ${elapsed}ms`);
    
    // 每 10 次写入输出一次缓存统计
    if (memoryCache.stats.sets % 10 === 0) {
      const stats = await this.getStats();
      logger.info('cache', `[缓存-统计] hitRate=${stats.hitRate} | 命中=${stats.hits} | 未命中=${stats.misses} | 淘汰=${memoryCache.evictionCount} | 清理=${memoryCache.cleanupCount} | 条目数=${memoryCache.cache.size}`);
    }
  }

  async invalidatePrediction(homeTeam, awayTeam) {
    const pattern = this.getKey('prediction', homeTeam, awayTeam) + '*';
    
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        const keys = await this.client.keys(pattern);
        if (keys.length > 0) {
          await this.client.del(keys);
          logger.debug('cache', '预测缓存已失效', { keys, type: 'redis' });
        }
      } catch (err) {
        logger.error('cache', '失效预测缓存失败', { homeTeam, awayTeam, error: err.message });
      }
    }
    
    const memKeys = await memoryCache.keys(pattern);
    for (const key of memKeys) {
      await memoryCache.del(key);
    }
  }

  async invalidateAllPredictions() {
    const pattern = this.getKey('prediction', '*');
    
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        const keys = await this.client.keys(pattern);
        if (keys.length > 0) {
          await this.client.del(keys);
          logger.debug('cache', '所有预测缓存已失效', { count: keys.length, type: 'redis' });
        }
      } catch (err) {
        logger.error('cache', '失效所有预测缓存失败', { error: err.message });
      }
    }
    
    const memKeys = await memoryCache.keys(pattern);
    for (const key of memKeys) {
      await memoryCache.del(key);
    }
  }

  async getTeams() {
    const key = this.getKey('teams');
    
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        const cached = await this.client.get(key);
        if (cached) {
          return JSON.parse(cached);
        }
        return null;
      } catch (err) {
        logger.error('cache', '获取球队缓存失败', { key, error: err.message });
        return await memoryCache.get(key);
      }
    }
    
    return await memoryCache.get(key);
  }

  async setTeams(teams) {
    const key = this.getKey('teams');
    
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        await this.client.set(key, JSON.stringify(teams), {
          EX: CACHE_TTL.teams
        });
      } catch (err) {
        logger.error('cache', '设置球队缓存失败', { key, error: err.message });
        await memoryCache.set(key, teams, CACHE_TTL.teams);
      }
    } else {
      await memoryCache.set(key, teams, CACHE_TTL.teams);
    }
  }

  async checkRateLimit(ip) {
    const key = this.getKey('rate_limit', ip);
    
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        const current = await this.client.get(key);
        const count = current ? parseInt(current) : 0;
        
        if (count >= RATE_LIMIT_CONFIG.maxRequests) {
          return { allowed: false, remaining: 0, resetTime: Date.now() + RATE_LIMIT_CONFIG.windowSeconds * 1000 };
        }

        await this.client.incr(key);
        await this.client.expire(key, CACHE_TTL.rateLimit);
        
        return {
          allowed: true,
          remaining: RATE_LIMIT_CONFIG.maxRequests - (count + 1),
          resetTime: Date.now() + RATE_LIMIT_CONFIG.windowSeconds * 1000
        };
      } catch (err) {
        logger.error('cache', '检查频率限制失败', { ip, error: err.message });
        return this.checkRateLimitMemory(ip);
      }
    }
    
    return this.checkRateLimitMemory(ip);
  }

  async checkRateLimitMemory(ip) {
    const key = this.getKey('rate_limit', ip);
    const current = await memoryCache.get(key);
    const count = current || 0;
    
    if (count >= RATE_LIMIT_CONFIG.maxRequests) {
      return { allowed: false, remaining: 0, resetTime: Date.now() + RATE_LIMIT_CONFIG.windowSeconds * 1000 };
    }

    await memoryCache.set(key, count + 1, CACHE_TTL.rateLimit);
    
    return {
      allowed: true,
      remaining: RATE_LIMIT_CONFIG.maxRequests - (count + 1),
      resetTime: Date.now() + RATE_LIMIT_CONFIG.windowSeconds * 1000
    };
  }

  async getStats() {
    const memStats = await memoryCache.getStats();
    const totalHits = memStats.hits;
    const totalMisses = memStats.misses;
    const totalSets = memStats.sets;
    const totalInvalidations = memStats.invalidations;
    
    const total = totalHits + totalMisses;
    const hitRate = total > 0 ? (totalHits / total * 100).toFixed(2) : 0;
    
    return {
      hits: totalHits,
      misses: totalMisses,
      sets: totalSets,
      invalidations: totalInvalidations,
      hitRate: `${hitRate}%`,
      total: total,
      cacheType: this.cacheType,
      ttlConfig: CACHE_TTL,
      rateLimitConfig: RATE_LIMIT_CONFIG
    };
  }

  async flushAll() {
    if (this.cacheType === 'redis' && this.isConnected && this.client) {
      try {
        await this.client.flushDb();
        logger.info('cache', 'Redis缓存已清空');
      } catch (err) {
        logger.error('cache', '清空Redis缓存失败', { error: err.message });
      }
    }
    
    await memoryCache.flushAll();
    logger.info('cache', '内存缓存已清空');
  }
}

export const cacheService = new CacheService();
