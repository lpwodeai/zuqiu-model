import { logger } from '../services/logger.js';

class AppError extends Error {
  constructor(message, code = 'INTERNAL_ERROR', statusCode = 500) {
    super(message);
    this.name = 'AppError';
    this.code = code;
    this.statusCode = statusCode;
    this.isOperational = true;
    Error.captureStackTrace(this, this.constructor);
  }
}

class NotFoundError extends AppError {
  constructor(message = '资源未找到') {
    super(message, 'NOT_FOUND', 404);
    this.name = 'NotFoundError';
  }
}

class ValidationError extends AppError {
  constructor(message = '数据验证失败') {
    super(message, 'VALIDATION_ERROR', 400);
    this.name = 'ValidationError';
  }
}

class AuthenticationError extends AppError {
  constructor(message = '认证失败') {
    super(message, 'AUTH_ERROR', 401);
    this.name = 'AuthenticationError';
  }
}

class AuthorizationError extends AppError {
  constructor(message = '权限不足') {
    super(message, 'FORBIDDEN', 403);
    this.name = 'AuthorizationError';
  }
}

class DatabaseError extends AppError {
  constructor(message = '数据库操作失败') {
    super(message, 'DATABASE_ERROR', 500);
    this.name = 'DatabaseError';
  }
}

class PredictionError extends AppError {
  constructor(message = '预测执行失败') {
    super(message, 'PREDICTION_ERROR', 500);
    this.name = 'PredictionError';
  }
}

class ModelLoadError extends AppError {
  constructor(message = '模型加载失败') {
    super(message, 'MODEL_LOAD_ERROR', 500);
    this.name = 'ModelLoadError';
  }
}

function asyncHandler(fn) {
  return (req, res, next) => {
    Promise.resolve(fn(req, res, next)).catch(next);
  };
}

function globalErrorHandler(err, req, res, next) {
  const requestId = req.headers['x-request-id'] || req.id || generateRequestId();
  const timestamp = new Date().toISOString();

  const errorResponse = {
    error: {
      message: err.isOperational ? err.message : '服务器内部错误',
      code: err.code || 'INTERNAL_ERROR',
      status: err.statusCode || 500,
      timestamp,
      requestId
    }
  };

  const logData = {
    requestId,
    method: req.method,
    url: req.originalUrl,
    ip: req.ip,
    userAgent: req.headers['user-agent'],
    statusCode: err.statusCode || 500,
    errorCode: err.code || 'INTERNAL_ERROR',
    errorType: err.name || 'Error',
    stack: err.stack,
    timestamp
  };

  if (err.statusCode >= 500 || !err.isOperational) {
    logger.error('api', err.message, logData, 'error-handler');
    console.error('[FATAL ERROR]', logData);
  } else {
    logger.warn('api', err.message, logData, 'error-handler');
    console.warn('[API ERROR]', err.message);
  }

  res.status(err.statusCode || 500).json(errorResponse);
}

function notFoundHandler(req, res, next) {
  const error = new NotFoundError(`路由未找到: ${req.method} ${req.originalUrl}`);
  next(error);
}

function requestLogger(req, res, next) {
  const start = Date.now();
  
  res.on('finish', () => {
    const duration = Date.now() - start;
    const logData = {
      method: req.method,
      url: req.originalUrl,
      statusCode: res.statusCode,
      duration: `${duration}ms`,
      ip: req.ip
    };

    if (res.statusCode >= 400) {
      logger.warn('request', `${req.method} ${req.originalUrl}`, logData);
    } else if (duration > 1000) {
      logger.info('request', `${req.method} ${req.originalUrl} (${duration}ms)`, logData);
    }
  });

  next();
}

function generateRequestId() {
  return `req_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
}

function setupUncaughtErrorHandlers(server) {
  process.on('uncaughtException', (err) => {
    console.error('未捕获异常:', err);
    logger.error('system', '未捕获异常', {
      message: err.message,
      stack: err.stack,
      type: err.name
    }, 'process');
  });

  process.on('unhandledRejection', (reason, promise) => {
    console.error('未处理的Promise拒绝:', reason);
    logger.error('system', '未处理的Promise拒绝', {
      reason: reason?.message || String(reason),
      stack: reason?.stack,
      promise: promise?.toString?.()
    }, 'process');
  });

  process.on('SIGTERM', () => {
    console.log('收到SIGTERM信号，正在优雅关闭...');
    logger.info('system', '收到SIGTERM信号，开始关闭', {}, 'process');
    
    server.close(() => {
      logger.info('system', '服务器已关闭', {}, 'process');
      process.exit(0);
    });

    setTimeout(() => {
      logger.error('system', '强制关闭超时', {}, 'process');
      process.exit(1);
    }, 30000);
  });

  process.on('SIGINT', () => {
    console.log('收到SIGINT信号，正在优雅关闭...');
    logger.info('system', '收到SIGINT信号，开始关闭', {}, 'process');
    
    server.close(() => {
      logger.info('system', '服务器已关闭', {}, 'process');
      process.exit(0);
    });

    setTimeout(() => {
      logger.error('system', '强制关闭超时', {}, 'process');
      process.exit(1);
    }, 30000);
  });
}

function SQLInjectionGuard(req, res, next) {
  const dangerousPatterns = [
    /;\s*DROP\s+/i,
    /;\s*DELETE\s+FROM/i,
    /;\s*INSERT\s+INTO/i,
    /;\s*UPDATE\s+\w+\s+SET/i,
    /--\s*$/m,
    /\/\*.*\*\//s,
    /;\s*ALTER\s+TABLE/i,
    /;\s*CREATE\s+TABLE/i,
    /UNION\s+SELECT/i
  ];

  const checkValue = (value) => {
    if (typeof value === 'string') {
      return dangerousPatterns.some(pattern => pattern.test(value));
    }
    return false;
  };

  const checkObject = (obj) => {
    if (!obj || typeof obj !== 'object') return false;
    for (const key in obj) {
      if (checkValue(obj[key])) return true;
      if (typeof obj[key] === 'object' && checkObject(obj[key])) return true;
    }
    return false;
  };

  if (checkObject(req.body) || checkObject(req.query) || checkObject(req.params)) {
    const err = new ValidationError('请求包含非法字符');
    return next(err);
  }

  next();
}

export {
  AppError,
  NotFoundError,
  ValidationError,
  AuthenticationError,
  AuthorizationError,
  DatabaseError,
  PredictionError,
  ModelLoadError,
  asyncHandler,
  globalErrorHandler,
  notFoundHandler,
  requestLogger,
  setupUncaughtErrorHandlers,
  SQLInjectionGuard
};

export default globalErrorHandler;