/**
 * feature-bridge.js — P0-D 114 维数据源缺口 JS 读取侧
 * ================================================================
 * 读取 Python `scripts/build_feature_bridge.py --export` 生成的
 * `assets/feature_bridge.json`，按「日期|主队中文|客队中文」键返回
 * sofa_ / pa_ / mkt_共识 / odds_ts_ 共 110 维真实值（mkt_dev_ 4 维由调用方
 * 用实时竞彩隐含概率计算，见 prediction-service.buildMatchContext）。
 *
 * 命中失败（赛程未收录/队名不一致）返回 null，上层维持原均值填充（归一化 0），
 * 不破坏既有 serving。
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const BRIDGE_PATH = path.join(__dirname, '../../assets/feature_bridge.json');

class FeatureBridge {
  constructor() {
    this._records = null;
    this._loaded = false;
    this._warned = false;
  }

  /**
   * 加载桥接文件（幂等，仅首次真正读盘）。
   * @returns {boolean} 是否加载成功
   */
  load() {
    if (this._loaded) return this._records !== null;
    this._loaded = true;
    try {
      if (!fs.existsSync(BRIDGE_PATH)) {
        if (!this._warned) {
          console.warn(`[feature-bridge] 桥接文件不存在: ${BRIDGE_PATH}（请先运行 build_feature_bridge.py --export）`);
          this._warned = true;
        }
        return false;
      }
      const raw = JSON.parse(fs.readFileSync(BRIDGE_PATH, 'utf8'));
      this._records = raw.records || {};
      return true;
    } catch (err) {
      console.error('[feature-bridge] 加载失败:', err.message);
      this._records = null;
      return false;
    }
  }

  /**
   * 按比赛查特征记录，返回 {feature_name: number} 或 null。
   * @param {string} date  YYYY-MM-DD
   * @param {string} home 主队中文名（canonical）
   * @param {string} away 客队中文名（canonical）
   */
  lookup(date, home, away) {
    if (!this.load() || !date || !home || !away) return null;
    const rec = this._records[`${date}|${home}|${away}`]
      || this._records[`${date}|${away}|${home}`];
    return rec ? { ...rec } : null;
  }

  /** 当前缓存记录数（调试/健康检查用） */
  get size() {
    if (!this.load()) return 0;
    return Object.keys(this._records).length;
  }
}

// 单例
export const featureBridge = new FeatureBridge();