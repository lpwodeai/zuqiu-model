/**
 * P1-D: 线上 A/B 测试框架 — 流量分流端点
 *
 * 提供稳定哈希分流，与 scripts/ab_test_framework.py 的 stable_assign 同算法：
 *   bucket = parseInt(sha256(`${seed}:${unitKey}`)[0..8], 16) % 100
 * 保证同一 unit_key 永远落到同一 variant（不随进程重启漂移）。
 *
 * 记录/分析（并行 shadow + 未来样本显著性）由 scripts/ab_test_framework.py 完成。
 */

import express from 'express';
import crypto from 'crypto';
import ApiResponse from '../../shared/api-response.js';

const router = express.Router();

function hashBucket(unitKey, seed = '') {
  const digest = crypto.createHash('sha256').update(`${seed}:${unitKey}`, 'utf8').digest('hex');
  return parseInt(digest.slice(0, 8), 16) % 100;
}

function stableAssign(unitKey, variants, weights = null, seed = '') {
  const n = variants.length;
  if (n === 0) throw new Error('variants 不能为空');
  const w = (weights && weights.length === n) ? weights : new Array(n).fill(1);
  const total = w.reduce((s, x) => s + (Number(x) || 0), 0);
  if (total <= 0) throw new Error('weights 之和必须 > 0');
  const bucket = hashBucket(unitKey, seed);
  let acc = 0;
  for (let i = 0; i < n; i++) {
    acc += Number(w[i]) || 0;
    if (bucket < (acc / total) * 100) return { variant: variants[i], bucket };
  }
  return { variant: variants[n - 1], bucket };
}

/**
 * POST /api/abtest/assign
 * 请求体: { unitKey, variants: ["control","treatment"], weights?: [1,1], seed?: "" }
 * 响应:   { ok, unitKey, variant, bucket, variants }
 */
router.post('/assign', (req, res) => {
  try {
    const body = req.body || {};
    const { unitKey } = body;
    const variants = Array.isArray(body.variants) ? body.variants : ['control', 'treatment'];
    if (!unitKey) {
      return res.status(400).json(ApiResponse.validationError('请提供 unitKey', 'MISSING_UNIT_KEY'));
    }
    if (!Array.isArray(variants) || variants.length < 2) {
      return res.status(400).json(ApiResponse.validationError('variants 至少需要 2 个', 'INVALID_VARIANTS'));
    }
    const names = variants.map(String);
    const { variant, bucket } = stableAssign(String(unitKey), names, body.weights, body.seed || '');
    res.json({ ok: true, unitKey: String(unitKey), variant, bucket, variants: names });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

export default router;