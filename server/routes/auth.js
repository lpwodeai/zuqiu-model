/**
 * 认证API路由
 */

import express from 'express';
import jwt from 'jsonwebtoken';
import bcrypt from 'bcryptjs';
import DataService from '../services/data-service.js';

const router = express.Router();

const JWT_SECRET = process.env.JWT_SECRET || 'top5-leagues-model-secret';
const JWT_EXPIRES_IN = '7d';

/**
 * POST /api/auth/register
 * 用户注册
 */
router.post('/register', async (req, res) => {
  try {
    const { username, email, password } = req.body;

    if (!username || !email || !password) {
      return res.status(400).json({
        error: { message: '请提供完整注册信息', code: 'MISSING_FIELDS' }
      });
    }

    // 检查用户是否已存在
    const existingUser = await DataService.findUser({ email });
    if (existingUser) {
      return res.status(409).json({
        error: { message: '用户已存在', code: 'USER_EXISTS' }
      });
    }

    // 加密密码
    const hashedPassword = await bcrypt.hash(password, 10);

    // 创建用户
    const user = await DataService.createUser({
      username,
      email,
      password: hashedPassword,
      createdAt: new Date()
    });

    // 生成JWT
    const token = jwt.sign(
      { userId: user.id, email: user.email },
      JWT_SECRET,
      { expiresIn: JWT_EXPIRES_IN }
    );

    res.json({
      success: true,
      data: {
        token,
        user: {
          id: user.id,
          username: user.username,
          email: user.email
        }
      },
      message: '注册成功',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'REGISTER_ERROR' }
    });
  }
});

/**
 * POST /api/auth/login
 * 用户登录
 */
router.post('/login', async (req, res) => {
  try {
    const { email, password } = req.body;

    if (!email || !password) {
      return res.status(400).json({
        error: { message: '请提供邮箱和密码', code: 'MISSING_FIELDS' }
      });
    }

    // 查找用户
    const user = await DataService.findUser({ email });
    if (!user) {
      return res.status(401).json({
        error: { message: '用户不存在', code: 'USER_NOT_FOUND' }
      });
    }

    // 验证密码
    const isValidPassword = await bcrypt.compare(password, user.password);
    if (!isValidPassword) {
      return res.status(401).json({
        error: { message: '密码错误', code: 'INVALID_PASSWORD' }
      });
    }

    // 生成JWT
    const token = jwt.sign(
      { userId: user.id, email: user.email },
      JWT_SECRET,
      { expiresIn: JWT_EXPIRES_IN }
    );

    res.json({
      success: true,
      data: {
        token,
        user: {
          id: user.id,
          username: user.username,
          email: user.email
        }
      },
      message: '登录成功',
      timestamp: new Date().toISOString()
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'LOGIN_ERROR' }
    });
  }
});

/**
 * GET /api/auth/verify
 * 验证Token有效性
 */
router.get('/verify', async (req, res) => {
  try {
    const authHeader = req.headers['authorization'];
    const token = authHeader && authHeader.split(' ')[1];

    if (!token) {
      return res.status(401).json({
        error: { message: '未提供Token', code: 'NO_TOKEN' }
      });
    }

    jwt.verify(token, JWT_SECRET, (err, decoded) => {
      if (err) {
        return res.status(403).json({
          error: { message: 'Token无效', code: 'INVALID_TOKEN' }
        });
      }

      res.json({
        success: true,
        data: {
          valid: true,
          user: decoded
        },
        timestamp: new Date().toISOString()
      });
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'VERIFY_ERROR' }
    });
  }
});

/**
 * POST /api/auth/refresh
 * 刷新Token
 */
router.post('/refresh', async (req, res) => {
  try {
    const authHeader = req.headers['authorization'];
    const token = authHeader && authHeader.split(' ')[1];

    if (!token) {
      return res.status(401).json({
        error: { message: '未提供Token', code: 'NO_TOKEN' }
      });
    }

    jwt.verify(token, JWT_SECRET, (err, decoded) => {
      if (err) {
        return res.status(403).json({
          error: { message: 'Token无效', code: 'INVALID_TOKEN' }
        });
      }

      const newToken = jwt.sign(
        { userId: decoded.userId, email: decoded.email },
        JWT_SECRET,
        { expiresIn: JWT_EXPIRES_IN }
      );

      res.json({
        success: true,
        data: { token: newToken },
        message: 'Token已刷新',
        timestamp: new Date().toISOString()
      });
    });
  } catch (err) {
    res.status(500).json({
      error: { message: err.message, code: 'REFRESH_ERROR' }
    });
  }
});

export default router;