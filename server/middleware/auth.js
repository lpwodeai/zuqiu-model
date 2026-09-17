/**
 * JWT认证中间件
 */

import jwt from 'jsonwebtoken';

const JWT_SECRET = process.env.JWT_SECRET || 'top5-leagues-model-secret';

/**
 * 必须认证
 */
export function authenticateToken(req, res, next) {
  const authHeader = req.headers['authorization'];
  const token = authHeader && authHeader.split(' ')[1];

  if (!token) {
    return res.status(401).json({
      error: { message: '未提供认证Token', code: 'NO_TOKEN' }
    });
  }

  jwt.verify(token, JWT_SECRET, (err, decoded) => {
    if (err) {
      return res.status(403).json({
        error: { message: 'Token无效或已过期', code: 'INVALID_TOKEN' }
      });
    }

    req.user = decoded;
    next();
  });
}

/**
 * 可选认证（公开API使用）
 */
export function optionalAuth(req, res, next) {
  const authHeader = req.headers['authorization'];
  const token = authHeader && authHeader.split(' ')[1];

  if (token) {
    jwt.verify(token, JWT_SECRET, (err, decoded) => {
      if (!err) {
        req.user = decoded;
      }
      next();
    });
  } else {
    next();
  }
}

/**
 * 角色检查
 */
export function requireRole(role) {
  return (req, res, next) => {
    if (!req.user || req.user.role !== role) {
      return res.status(403).json({
        error: { message: '权限不足', code: 'INSUFFICIENT_PERMISSION' }
      });
    }
    next();
  };
}

/**
 * 管理员权限检查
 */
export function requireAdmin(req, res, next) {
  return requireRole('admin')(req, res, next);
}