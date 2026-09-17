/**
 * 数据采集管道
 * 自动化采集赔率数据、球队数据、比赛数据
 */

import axios from 'axios';
import cheerio from 'cheerio';
import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// 数据存储路径
const DATA_DIR = path.join(__dirname, '../data');

class DataCollector {
  constructor() {
    this.dataDir = DATA_DIR;
    this.ensureDirectories();
    
    // 采集配置
    this.config = {
      oddsInterval: 300000,      // 赔率采集间隔：5分钟
      matchInterval: 3600000,    // 比赛采集间隔：1小时
      teamInterval: 86400000,    // 球队数据更新间隔：24小时
      retryAttempts: 3,
      retryDelay: 1000
    };
  }

  /**
   * 安全解析 JS 对象字面量 (替代 eval，解决 C-003/D-003 安全漏洞)
   */
  _safeParseJsObject(objStr, label) {
    try {
      return JSON.parse(objStr);
    } catch (_) {
      try {
        const sandbox = {};
        const script = new vm.Script(`this.value = ${objStr};`, {
          filename: `safe-parse-${label || 'unknown'}.js`
        });
        const context = vm.createContext(sandbox, {
          codeGeneration: { strings: false, wasm: false }
        });
        script.runInContext(context, { timeout: 1000 });
        return sandbox.value;
      } catch (vmErr) {
        throw new Error(`安全解析 ${label || 'JS对象'} 失败: ${vmErr.message}`);
      }
    }
  }

  /**
   * 确保数据目录存在
   */
  ensureDirectories() {
    const dirs = ['odds', 'teams', 'matches', 'cache'];
    dirs.forEach(dir => {
      const fullPath = path.join(this.dataDir, dir);
      if (!fs.existsSync(fullPath)) {
        fs.mkdirSync(fullPath, { recursive: true });
      }
    });
  }

  /**
   * 启动定时采集任务
   */
  startScheduledCollection() {
    console.log('🚀 数据采集管道启动');
    
    // 赔率采集
    this.scheduleTask('odds', this.config.oddsInterval, this.collectOdds.bind(this));
    
    // 比赛数据采集
    this.scheduleTask('matches', this.config.matchInterval, this.collectMatches.bind(this));
    
    // 球队数据更新
    this.scheduleTask('teams', this.config.teamInterval, this.updateTeamData.bind(this));
    
    console.log('✅ 定时采集任务已启动');
  }

  /**
   * 定时任务调度
   */
  scheduleTask(name, interval, task) {
    console.log(`📅 ${name}采集任务: 每${interval/1000}秒执行一次`);
    
    // 立即执行一次
    task().catch(err => console.error(`${name}采集错误:`, err));
    
    // 定时执行
    setInterval(async () => {
      try {
        await task();
      } catch (err) {
        console.error(`${name}采集错误:`, err);
      }
    }, interval);
  }

  /**
   * 采集赔率数据
   */
  async collectOdds() {
    console.log('📊 开始采集赔率数据...');
    
    try {
      // 模拟数据采集（实际需对接真实数据源）
      const oddsData = await this.fetchOddsFromSource();
      
      // 保存数据
      const saved = await this.saveOddsData(oddsData);
      
      console.log(`✅ 赔率数据采集完成: ${saved.count}场比赛`);
      return saved;
    } catch (err) {
      console.error('❌ 赔率采集失败:', err.message);
      throw err;
    }
  }

  /**
   * 从数据源获取赔率（模拟实现）
   */
  async fetchOddsFromSource() {
    // 实际实现需要对接真实赔率API
    // 示例：对接彩票网站、体育数据API等
    
    // 这里返回模拟数据作为框架示例
    const mockOdds = this.generateMockOddsData();
    return mockOdds;
  }

  /**
   * 生成模拟赔率数据（用于测试）
   */
  generateMockOddsData() {
    // 从现有预测JSON文件读取真实赔率数据
    const predictionsDir = path.join(__dirname, '..');
    const files = fs.readdirSync(predictionsDir).filter(f => f.startsWith('predict_') && f.endsWith('.json'));
    
    const oddsData = [];
    files.forEach(file => {
      try {
        const data = JSON.parse(fs.readFileSync(path.join(predictionsDir, file), 'utf8'));
        if (data.odds) {
          oddsData.push({
            matchId: file.replace('predict_', '').replace('.json', ''),
            homeTeam: data.homeTeam,
            awayTeam: data.awayTeam,
            odds: data.odds,
            timestamp: new Date().toISOString()
          });
        }
      } catch (err) {
        // 忽略解析错误
      }
    });
    
    return oddsData;
  }

  /**
   * 保存赔率数据
   */
  async saveOddsData(oddsData) {
    const oddsDir = path.join(this.dataDir, 'odds');
    let count = 0;

    oddsData.forEach(odds => {
      const filePath = path.join(oddsDir, `${odds.matchId}.json`);
      
      let existingData = {};
      if (fs.existsSync(filePath)) {
        existingData = JSON.parse(fs.readFileSync(filePath, 'utf8'));
      }

      // 添加历史记录
      if (!existingData.history) existingData.history = [];
      existingData.history.push({
        timestamp: odds.timestamp,
        win: odds.odds.win,
        draw: odds.odds.draw,
        lose: odds.odds.lose
      });

      // 更新当前赔率
      existingData.current = odds.odds;
      existingData.updatedAt = odds.timestamp;

      fs.writeFileSync(filePath, JSON.stringify(existingData, null, 2));
      count++;
    });

    return { count, savedAt: new Date().toISOString() };
  }

  /**
   * 采集比赛数据
   */
  async collectMatches() {
    console.log('🏆 开始采集比赛数据...');
    
    try {
      const matchesData = await this.fetchMatchesFromSource();
      const saved = await this.saveMatchesData(matchesData);
      
      console.log(`✅ 比赛数据采集完成: ${saved.count}场比赛`);
      return saved;
    } catch (err) {
      console.error('❌ 比赛采集失败:', err.message);
      throw err;
    }
  }

  /**
   * 从数据源获取比赛数据（模拟实现）
   */
  async fetchMatchesFromSource() {
    // 实际实现需要对接FIFA API、体育媒体API等
    // 返回模拟数据作为框架示例
    return [];
  }

  /**
   * 保存比赛数据
   */
  async saveMatchesData(matchesData) {
    const matchesDir = path.join(this.dataDir, 'matches');
    let count = 0;

    matchesData.forEach(match => {
      const filePath = path.join(matchesDir, `${match.id}.json`);
      fs.writeFileSync(filePath, JSON.stringify(match, null, 2));
      count++;
    });

    return { count, savedAt: new Date().toISOString() };
  }

  /**
   * 更新球队数据
   */
  async updateTeamData() {
    console.log('⚽ 开始更新球队数据...');
    
    try {
      // 从model-engine.js读取球队数据
      const modelPath = path.join(__dirname, '../assets/model-engine.js');
      const content = fs.readFileSync(modelPath, 'utf8');
      
      // 解析TEAMS数据 (安全解析: 使用 vm.Script 替代 eval)
      const teamsMatch = content.match(/var TEAMS = (\{[\s\S]*?\});/);
      if (teamsMatch) {
        const teams = this._safeParseJsObject(teamsMatch[1], 'TEAMS');
        
        const filePath = path.join(this.dataDir, 'teams', 'all_teams.json');
        fs.writeFileSync(filePath, JSON.stringify(teams, null, 2));
        
        console.log(`✅ 球队数据更新完成: ${Object.keys(teams).length}支球队`);
        return { count: Object.keys(teams).length };
      }
      
      return { count: 0 };
    } catch (err) {
      console.error('❌ 球队数据更新失败:', err.message);
      throw err;
    }
  }

  /**
   * 手动触发采集
   */
  async manualCollect(types = ['odds', 'matches', 'teams']) {
    const results = {};
    
    for (const type of types) {
      try {
        switch (type) {
          case 'odds':
            results.odds = await this.collectOdds();
            break;
          case 'matches':
            results.matches = await this.collectMatches();
            break;
          case 'teams':
            results.teams = await this.updateTeamData();
            break;
        }
      } catch (err) {
        results[type] = { error: err.message };
      }
    }
    
    return results;
  }

  /**
   * 获取采集状态
   */
  getStatus() {
    return {
      config: this.config,
      directories: {
        odds: fs.existsSync(path.join(this.dataDir, 'odds')),
        matches: fs.existsSync(path.join(this.dataDir, 'matches')),
        teams: fs.existsSync(path.join(this.dataDir, 'teams'))
      },
      lastCollection: this.getLastCollectionTime()
    };
  }

  /**
   * 获取最后采集时间
   */
  getLastCollectionTime() {
    const cacheDir = path.join(this.dataDir, 'cache');
    const logPath = path.join(cacheDir, 'collection_log.json');
    
    if (fs.existsSync(logPath)) {
      return JSON.parse(fs.readFileSync(logPath, 'utf8'));
    }
    
    return null;
  }

  /**
   * 记录采集日志
   */
  logCollection(type, result) {
    const cacheDir = path.join(this.dataDir, 'cache');
    const logPath = path.join(cacheDir, 'collection_log.json');
    
    let log = {};
    if (fs.existsSync(logPath)) {
      log = JSON.parse(fs.readFileSync(logPath, 'utf8'));
    }
    
    log[type] = {
      timestamp: new Date().toISOString(),
      result
    };
    
    fs.writeFileSync(logPath, JSON.stringify(log, null, 2));
  }

  /**
   * 停止所有采集任务
   */
  stop() {
    console.log('🛑 数据采集管道停止');
    // 清除所有定时任务（需要保存interval引用）
  }
}

// 主执行入口
const collector = new DataCollector();

// 命令行参数处理
const args = process.argv.slice(2);

if (args.includes('--start')) {
  collector.startScheduledCollection();
} else if (args.includes('--collect')) {
  const types = args.filter(a => ['odds', 'matches', 'teams'].includes(a));
  collector.manualCollect(types.length > 0 ? types : ['odds', 'matches', 'teams'])
    .then(results => console.log('采集结果:', results))
    .catch(err => console.error('采集失败:', err));
} else if (args.includes('--status')) {
  console.log('采集状态:', collector.getStatus());
} else {
  console.log(`
数据采集管道使用说明:

  node scripts/data-collector.js --start       启动定时采集
  node scripts/data-collector.js --collect     手动采集所有数据
  node scripts/data-collector.js --collect odds matches teams  指定采集类型
  node scripts/data-collector.js --status      查看采集状态
`);
}

export default collector;