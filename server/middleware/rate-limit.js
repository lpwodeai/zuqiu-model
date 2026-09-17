import { cacheService } from '../services/cache-service.js';
import ApiResponse from '../../shared/api-response.js';

export async function rateLimit(req, res, next) {
  const ip = req.ip || req.connection.remoteAddress || 'unknown';

  const result = await cacheService.checkRateLimit(ip);

  res.setHeader('X-RateLimit-Limit', cacheService.rateLimitConfig?.maxRequests || 100);
  res.setHeader('X-RateLimit-Remaining', result.remaining);
  res.setHeader('X-RateLimit-Reset', Math.floor(result.resetTime / 1000));

  if (!result.allowed) {
    return res.status(429).json(ApiResponse.error('请求过于频繁，请稍后再试', 'RATE_LIMIT_EXCEEDED'));
  }

  next();
}

export function rateLimitByUser(req, res, next) {
  if (!req.user || !req.user.userId) {
    return rateLimit(req, res, next);
  }

  const userId = req.user.userId;
  const ip = req.ip || req.connection.remoteAddress || 'unknown';

  Promise.all([
    cacheService.checkRateLimit(`user:${userId}`),
    cacheService.checkRateLimit(ip)
  ]).then(([userResult, ipResult]) => {
    res.setHeader('X-RateLimit-Limit', cacheService.rateLimitConfig?.maxRequests || 100);
    res.setHeader('X-RateLimit-Remaining', Math.min(userResult.remaining, ipResult.remaining));
    res.setHeader('X-RateLimit-Reset', Math.max(userResult.resetTime, ipResult.resetTime));

    if (!userResult.allowed || !ipResult.allowed) {
      return res.status(429).json(ApiResponse.error('请求过于频繁，请稍后再试', 'RATE_LIMIT_EXCEEDED'));
    }

    next();
  }).catch(() => {
    next();
  });
}
