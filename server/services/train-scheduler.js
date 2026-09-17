import { logger } from './logger.js';
import { reviewService } from './review-service.js';
import { cacheService } from './cache-service.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { spawn } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export class TrainScheduler {
  constructor() {
    this.schedule = null;
    this.isTraining = false;
    this.lastTrainingTime = null;
    this.scheduleModule = null;
    this.config = {
      schedule: process.env.TRAIN_SCHEDULE || '0 2 * * 1',
      minDaysBetweenTraining: process.env.MIN_DAYS_BETWEEN_TRAINING ? parseInt(process.env.MIN_DAYS_BETWEEN_TRAINING) : 7,
      minNewMatches: process.env.MIN_NEW_MATCHES ? parseInt(process.env.MIN_NEW_MATCHES) : 10
    };
  }

  async init() {
    console.log('✅ 训练调度服务初始化完成');
  }

  async shouldTrain() {
    if (this.isTraining) {
      return false;
    }

    const stats = await reviewService.getReviewStats();
    
    if (this.lastTrainingTime) {
      const daysSinceLastTraining = (Date.now() - this.lastTrainingTime) / (1000 * 60 * 60 * 24);
      if (daysSinceLastTraining < this.config.minDaysBetweenTraining) {
        logger.info('train', '距离上次训练时间不足', { daysSinceLastTraining, minRequired: this.config.minDaysBetweenTraining });
        return false;
      }
    }

    if (stats.total < this.config.minNewMatches) {
      logger.info('train', '新比赛数据不足', { total: stats.total, minRequired: this.config.minNewMatches });
      return false;
    }

    return true;
  }

  async runTraining() {
    if (this.isTraining) {
      logger.warn('train', '训练正在进行中，跳过本次训练');
      return { success: false, message: '训练正在进行中' };
    }

    this.isTraining = true;
    const startTime = Date.now();
    
    logger.info('train', '开始模型训练');
    
    try {
      const stats = await reviewService.getReviewStats();
      
      const version = `v${new Date().toISOString().slice(0, 10).replace(/-/g, '')}`;
      
      const trainingResult = await this.executeTraining(version);
      
      await reviewService.saveModelVersion({
        version: version,
        description: `自动训练 - 基于${stats.total}场比赛数据`,
        trainingDataCount: stats.total,
        brierScore: trainingResult.brierScore || null,
        accuracy: trainingResult.accuracy || null,
        deployed: true
      });
      
      this.lastTrainingTime = Date.now();
      const duration = ((Date.now() - startTime) / 1000).toFixed(2);
      
      logger.info('train', '模型训练完成', { version, duration: `${duration}s`, dataCount: stats.total });
      
      await cacheService.invalidateAllPredictions();
      
      return {
        success: true,
        version: version,
        duration: `${duration}s`,
        dataCount: stats.total,
        brierScore: trainingResult.brierScore,
        accuracy: trainingResult.accuracy
      };
    } catch (err) {
      logger.error('train', '模型训练失败', { error: err.message });
      return { success: false, message: err.message };
    } finally {
      this.isTraining = false;
    }
  }

  async executeTraining(version) {
    const trainingScript = path.join(__dirname, '../../scripts/train_models.py');
    
    return new Promise((resolve) => {
      try {
        const pythonProcess = spawn('python', [trainingScript, '--version', version]);
        
        let output = '';
        let error = '';
        
        pythonProcess.stdout.on('data', (data) => {
          output += data.toString();
        });
        
        pythonProcess.stderr.on('data', (data) => {
          error += data.toString();
        });
        
        pythonProcess.on('close', (code) => {
          if (code === 0) {
            logger.info('train', '训练脚本执行成功', { output: output.slice(0, 500) });
            
            const brierMatch = output.match(/brier_score:\s*([\d.]+)/);
            const accuracyMatch = output.match(/accuracy:\s*([\d.]+)/);
            
            resolve({
              brierScore: brierMatch ? parseFloat(brierMatch[1]) : null,
              accuracy: accuracyMatch ? parseFloat(accuracyMatch[1]) : null,
              output: output
            });
          } else {
            logger.error('train', '训练脚本执行失败', { code, error });
            resolve({
              brierScore: null,
              accuracy: null,
              output: output,
              error: error
            });
          }
        });
        
        pythonProcess.on('error', (err) => {
          logger.error('train', '训练脚本启动失败', { error: err.message });
          resolve({
            brierScore: null,
            accuracy: null,
            error: err.message
          });
        });
      } catch (err) {
        logger.error('train', '执行训练脚本出错', { error: err.message });
        resolve({
          brierScore: null,
          accuracy: null,
          error: err.message
        });
      }
    });
  }

  async scheduleTraining() {
    try {
      this.scheduleModule = await import('node-schedule');
      
      this.schedule = this.scheduleModule.scheduleJob(this.config.schedule, async () => {
        logger.info('train', '定时训练任务触发');
        
        if (await this.shouldTrain()) {
          await this.runTraining();
        } else {
          logger.info('train', '条件不满足，跳过训练');
        }
      });
      
      console.log(`✅ 定时训练任务已设置: ${this.config.schedule}`);
      logger.info('train', '定时训练任务已设置', { schedule: this.config.schedule });
      
      return true;
    } catch (err) {
      logger.warn('train', 'node-schedule未安装，定时训练功能不可用', { error: err.message });
      console.warn('⚠️ node-schedule未安装，定时训练功能不可用');
      return false;
    }
  }

  async triggerTraining() {
    return await this.runTraining();
  }

  async getStatus() {
    return {
      isTraining: this.isTraining,
      lastTrainingTime: this.lastTrainingTime,
      schedule: this.config.schedule,
      minDaysBetweenTraining: this.config.minDaysBetweenTraining,
      minNewMatches: this.config.minNewMatches
    };
  }

  async stop() {
    if (this.schedule) {
      this.schedule.cancel();
      this.schedule = null;
      logger.info('train', '定时训练任务已停止');
    }
  }
}

export const trainScheduler = new TrainScheduler();
