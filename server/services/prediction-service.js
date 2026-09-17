/**
 * 预测服务模块
 * 封装model-engine.js核心预测功能
 */

import fs from 'fs';
import path from 'path';
import vm from 'vm';
import { fileURLToPath } from 'url';
import { createRequire } from 'module';
import PredictionEngine from '../../shared/prediction-engine.js';
import { cacheService } from './cache-service.js';
import { modelHotReloader } from './model-hot-reloader.js';
import { logger } from './logger.js';
import { db } from '../database/index.js';
import { featureBridge } from './feature-bridge.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const MODEL_PATH = path.join(__dirname, '../../assets/model-engine.js');
const TEAM_ATTRIBUTES_PATH = path.join(__dirname, '../../assets/team_attributes.json');
const STACKING_WEIGHTS_PATH = path.join(__dirname, '../../assets/stacking_weights.json');
const LEAGUE_TIER_PATH = path.join(__dirname, '../../assets/league_tier.json');
const LEAGUE_TIER_WEIGHT_PATH = path.join(__dirname, '../../assets/league_tier_weight.json');
const XGB_MODEL_PATH = path.join(__dirname, '../../assets/xgb_model_export.js');
const LGB_MODEL_PATH = path.join(__dirname, '../../assets/lgb_model_export.js');
const SCALER_PATH = path.join(__dirname, '../../assets/feature_scaler_params.js');
const DB_PATH = path.join(__dirname, '../../data/five_leagues.db');

// C-20260816-204: 英超独立模型路径
const LGB_EPL_MODEL_PATH = path.join(__dirname, '../../assets/lgb_model_epl_export.js');
const SCALER_EPL_PATH = path.join(__dirname, '../../assets/scaler_epl_export.js');

// C-20260819-006: T-006 低进球分类器路径
const T006_LOWGOAL_MODEL_PATH = path.join(__dirname, '../../assets/t006_lowgoal_export.js');
const T006_FEATURE_SPEC_PATH = path.join(__dirname, '../../assets/t006_feature_spec.js');

// 阶段 B (unified_engine_integration_plan §7): λ 回归头路径（非阻塞，缺失/失败不影响主预测）
const LAMBDA_MODEL_PATH = path.join(__dirname, '../../assets/lambda_model_export.js');

// C-20260816-200: 联赛专属 draw_threshold_factor（D3: 单一来源迁至 config.yaml，JS 不再硬编码阈值）
// C-20260823-001: 联赛分档阈值模式开关
//   "argmax"          — 全局 argmax，默认（消除多重比较过拟合风险）
//   "league_specific" — 逐联赛因子（西甲 0.80 / 意甲 0.85 / 德甲 0.85 / 英超·法甲 argmax）
//   切换方式: 修改 config.yaml 的 draw_threshold_mode 即可，无需改其他代码
const CONFIG_YAML_PATH = path.join(__dirname, '../../config.yaml');

/**
 * 兜底：js-yaml 不可用时的最小化 YAML 子集解析
 * 仅支持本项目该节子集：顶层 `key: value`、缩进嵌套 map、注释 # 行/行尾注释
 * @param {string} text YAML 文本
 * @returns {Object} 解析后的 JS 对象
 */
function _parseYamlSubset(text) {
  const root = {};
  const stack = [{ indent: -1, node: root }]; // 缩进栈，栈顶为当前父节点
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/\s+#.*$/, ''); // 去除行尾注释
    if (!line.trim() || /^\s*#/.test(line)) continue; // 空行/整行注释
    const indent = line.match(/^\s*/)[0].length;
    const content = line.trim();
    const sepIdx = content.indexOf(':');
    if (sepIdx === -1) continue; // 非 key: value 行忽略
    const key = content.slice(0, sepIdx).trim();
    const value = content.slice(sepIdx + 1).trim();
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) {
      stack.pop(); // 缩进回退到父级
    }
    const parent = stack[stack.length - 1].node;
    if (value === '') {
      // 嵌套 map
      if (!parent[key] || typeof parent[key] !== 'object') parent[key] = {};
      stack.push({ indent, node: parent[key] });
    } else {
      parent[key] = _parseYamlScalar(value);
    }
  }
  return root;
}

/** 最小化 YAML 标量解析：数字/布尔/null/去引号字符串 */
function _parseYamlScalar(value) {
  if ((value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  if (value === 'null' || value === '~') return null;
  if (value === 'true') return true;
  if (value === 'false') return false;
  const num = Number(value);
  return (value !== '' && !Number.isNaN(num)) ? num : value;
}

/**
 * 模块加载时从 config.yaml 读取平局阈值配置（D3: 单一来源，消除 JS 双源硬编码）
 * @returns {Object|null} 含 draw_threshold_mode/factor/factor_league 的配置；读取失败或缺该节返回 null
 */
function _loadDrawThresholdConfig() {
  try {
    if (!fs.existsSync(CONFIG_YAML_PATH)) return null;
    const raw = fs.readFileSync(CONFIG_YAML_PATH, 'utf8');
    let cfg = null;
    try {
      const yaml = require('js-yaml'); // js-yaml 已在 package.json 依赖中（^4.3.0）
      cfg = yaml.load(raw);
    } catch (_) {
      cfg = _parseYamlSubset(raw); // js-yaml 不可用时最小化解析兜底
    }
    if (cfg && typeof cfg === 'object' &&
        (cfg.draw_threshold_mode !== undefined ||
         cfg.draw_threshold_factor !== undefined ||
         cfg.draw_threshold_factor_league !== undefined)) {
      return cfg;
    }
  } catch (_) { /* 读取失败时走下方硬编码回退 */ }
  return null;
}

// 兼容回退: config.yaml 缺失/解析失败/缺该节时，回退到既有硬编码常量，保证服务不崩
const _drawThresholdConfig = _loadDrawThresholdConfig();

const DRAW_THRESHOLD_MODE = _drawThresholdConfig?.draw_threshold_mode ?? "argmax";

// C-20260820-037: 重训 209 维模型 + 完整网格 [0.80~1.10] 重校准——高平局联赛「反压低平局」提升准确率
//   因子语义: <=0 或 =1.0 等价 argmax；0<factor<1 反压低平局（抬高门槛，边界平局降级）；>1 上浮平局
//   准确率最优: 西甲 0.80(+1.05pp) / 意甲 0.85(+0.97pp) / 德甲 0.85(+0.87pp) / 英超·法甲 argmax
//   D3（C-20260909-004）单源: 因子表一律读 config.yaml；config 缺失时回退空表 {}，
//   league_specific 模式退化为全部走 DEFAULT 1.0（等价 argmax），JS 不再硬编码任何联赛因子
const LEAGUE_DRAW_THRESHOLD_FACTOR = _drawThresholdConfig?.draw_threshold_factor_league ?? {};
const DEFAULT_DRAW_THRESHOLD_FACTOR = _drawThresholdConfig?.draw_threshold_factor ?? 1.0;

class PredictionService {
  constructor() {
    this.teams = {};
    this.teamNameIndex = {};
    this.playerData = {};
    this.leagueTier = {};
    this.leagueTierWeight = {};
    this.playerPostmatchRatings = {};
    this.eloRatings = {};
    this.xgbModel = {};
    this.lgbModel = {};
    this.lgbEplModel = null;  // C-20260816-204: 英超独立模型
    this.t006LowgoalModel = null;  // C-20260819-006: T-006 低进球分类器
    this.t006FeatureSpec = null;   // C-20260819-006: T-006 特征规格(参考)
    this.stackingWeights = {};
    this.modelEngineLoaded = false;
    this.featureScalerParams = null;
    this.predictionConfig = {};
    this.engine = new PredictionEngine();
    this._initPromise = null;
    this._initCompleted = false;
  }

  async init() {
    if (this._initPromise) return this._initPromise;
    this._initPromise = this._initialize();
    return this._initPromise;
  }

  async _initialize() {
    const initStart = Date.now();
    logger.info('prediction-service', '开始初始化...');

    this.loadConfig();
    this.loadModelData();

    const loadResult = await this.loadTrainedModels();

    this.configureEngine();
    this.setupHotReload();

    const elapsed = Date.now() - initStart;
    this._initCompleted = true;
    logger.info('prediction-service', `初始化完成: ${loadResult.loadedCount}/3 模型, 耗时 ${elapsed}ms`);
    console.log(`🚀 预测服务初始化完成 (${elapsed}ms)`);
    return loadResult;
  }

  setupHotReload() {
    modelHotReloader.onReload((key, data) => {
      logger.info('prediction-service', `收到热更新通知: ${key}`);
      
      switch (key) {
        case 'xgbModel':
          this.xgbModel = data;
          this.configureEngine();
          break;
        case 'lgbModel':
          this.lgbModel = data;
          this.configureEngine();
          break;
        case 'teamAttributes':
          this.teams = data;
          this.buildTeamEloRatings();
          this.buildTeamNameIndex();
          this.configureEngine();
          break;
        case 'stackingWeights':
          this.stackingWeights = data;
          this.configureEngine();
          break;
        case 'leagueTier':
          this.leagueTier = data;
          break;
        case 'leagueTierWeight':
          this.leagueTierWeight = data;
          break;
        case 'featureScaler':
          this.featureScalerParams = data;
          this.configureEngine();
          break;
        case 't006LowgoalModel':  // C-20260819-006
          this.t006LowgoalModel = data;
          this.configureEngine();
          break;
      }
      
      logger.info('prediction-service', `热更新完成: ${key}`);
    });
    
    modelHotReloader.startWatching();
    logger.info('prediction-service', '模型热更新监控已启动');
  }

  configureEngine() {
    this.engine.setStackingWeights(this.stackingWeights);
    this.engine.setFeatureScalerParams(this.featureScalerParams);
    this.engine.setModels(this.xgbModel, this.lgbModel);
    this.engine.setEloRatings(this.eloRatings);
    this.engine.setT006LowgoalModel(this.t006LowgoalModel);  // C-20260819-006
    this.engine.setLambdaModel(this.lambdaModel);  // 阶段 B: λ 回归头（null 时引擎回退 calcLambdaMatch）
    console.log('✅ 共享预测引擎已配置');
  }

  /**
   * 加载配置文件
   */
  loadConfig() {
    try {
      const configPath = path.join(__dirname, '../../config.yaml');
      if (fs.existsSync(configPath)) {
        const content = fs.readFileSync(configPath, 'utf8');
        const config = content.split('\n').reduce((acc, line) => {
          if (line.trim().startsWith('prediction:')) {
            acc.inPrediction = true;
          } else if (acc.inPrediction && line.trim().startsWith('base_prob:')) {
            acc.config.baseProb = parseFloat(line.trim().split(':')[1].trim());
          } else if (acc.inPrediction && line.trim().startsWith('certainty:')) {
            acc.config.certainty = parseFloat(line.trim().split(':')[1].trim());
          } else if (line.trim() && !line.trim().startsWith('-') && !line.trim().startsWith('#')) {
            acc.inPrediction = false;
          }
          return acc;
        }, { inPrediction: false, config: {} }).config;
        
        this.predictionConfig = config;
        console.log('✅ 预测配置已加载:', this.predictionConfig);
      }
    } catch (err) {
      console.error('❌ 配置加载失败:', err.message);
    }
  }

  /**
   * 加载模型数据
   */
  loadModelData() {
    let teamsLoaded = false;
    
    if (fs.existsSync(TEAM_ATTRIBUTES_PATH)) {
      try {
        const teamContent = fs.readFileSync(TEAM_ATTRIBUTES_PATH, 'utf8');
        this.teams = JSON.parse(teamContent);
        teamsLoaded = true;
        console.log('✅ 从team_attributes.json加载球队数据:', Object.keys(this.teams).length, '支球队');
      } catch (parseErr) {
        console.warn('⚠️ JSON解析失败:', parseErr.message);
      }
    }
    
    if (!teamsLoaded && fs.existsSync(MODEL_PATH)) {
      try {
        const content = fs.readFileSync(MODEL_PATH, 'utf8');
        const teamsMatch = content.match(/var TEAMS = (\{[\s\S]*\});/);
        if (teamsMatch) {
          this.teams = JSON.parse(teamsMatch[1]);
          console.log('⚠️ 从model-engine.js加载球队数据(备用):', Object.keys(this.teams).length, '支球队');
        }
      } catch (e) {
        console.warn('⚠️ 备用加载失败:', e.message);
      }
    }

    if (fs.existsSync(STACKING_WEIGHTS_PATH)) {
      try {
        const content = fs.readFileSync(STACKING_WEIGHTS_PATH, 'utf8');
        this.stackingWeights = JSON.parse(content);
        console.log('✅ 从stacking_weights.json加载堆叠权重:', Object.keys(this.stackingWeights).length, '个模型');
      } catch (e) {
        console.warn('⚠️ STACKING_WEIGHTS加载失败:', e.message);
      }
    }

    if (fs.existsSync(LEAGUE_TIER_PATH)) {
      try {
        const content = fs.readFileSync(LEAGUE_TIER_PATH, 'utf8');
        this.leagueTier = JSON.parse(content);
        console.log('✅ 从league_tier.json加载联赛层级:', Object.keys(this.leagueTier).length, '个层级');
      } catch (e) {
        console.warn('⚠️ LEAGUE_TIER加载失败:', e.message);
      }
    }

    if (fs.existsSync(LEAGUE_TIER_WEIGHT_PATH)) {
      try {
        const content = fs.readFileSync(LEAGUE_TIER_WEIGHT_PATH, 'utf8');
        this.leagueTierWeight = JSON.parse(content);
        console.log('✅ 从league_tier_weight.json加载联赛权重:', Object.keys(this.leagueTierWeight).length, '个权重');
      } catch (e) {
        console.warn('⚠️ LEAGUE_TIER_WEIGHT加载失败:', e.message);
      }
    }

    this.buildTeamEloRatings();
    this.buildTeamNameIndex();
    this.modelEngineLoaded = true;
    console.log('✅ 模型数据已加载:', Object.keys(this.teams).length, '支球队, ELO:', Object.keys(this.eloRatings).length, '个评分');
  }

  buildTeamEloRatings() {
    const leagueBaseElo = {
      BL1: 1850,
      FL1: 1780,
      PL: 1830,
      LaLiga: 1820,
      IT: 1790,
      SA: 1770
    };

    for (const [key, team] of Object.entries(this.teams)) {
      if (!this.eloRatings[key]) {
        const league = team.league || '';
        const baseElo = leagueBaseElo[league] || 1800;
        const attackBonus = (team.attack || 1.0 - 1.0) * 50;
        const formBonus = (team.recentForm || 0) * 20;
        this.eloRatings[key] = Math.round(baseElo + attackBonus + formBonus);
      }
    }
  }

  _safeParseModel(content, variableName, filePath) {
    const match = content.match(new RegExp(`var ${variableName}\\s*=\\s*(\\{[\\s\\S]*\\});`));
    if (!match) {
      throw new Error(`模型文件格式错误: ${filePath} 未找到 var ${variableName}`);
    }

    try {
      const obj = JSON.parse(match[1]);
      return obj;
    } catch (parseErr) {
      const sandbox = {};
      const script = new vm.Script(`this.value = ${match[1]};`, { filename: filePath });
      const context = vm.createContext(sandbox, {
        codeGeneration: { strings: false, wasm: false }
      });
      script.runInContext(context, { timeout: 1000 });
      return sandbox.value;
    }
  }

  async loadTrainedModels() {
    const startTime = Date.now();
    const results = await Promise.allSettled([
      this._loadModelFile(XGB_MODEL_PATH, 'XGB_MODEL', 'xgbModel'),
      this._loadModelFile(LGB_MODEL_PATH, 'LGB_MODEL', 'lgbModel'),
      this._loadModelFile(SCALER_PATH, 'FEATURE_SCALER_PARAMS', 'featureScalerParams')
    ]);

    let loadedCount = 0;
    for (const result of results) {
      if (result.status === 'fulfilled') {
        loadedCount++;
        const { key, data, stats } = result.value;
        this[key] = data;
        if (key === 'featureScalerParams') {
          this.featureScalerParams = data;
        }
        console.log(`✅ 模型已加载: ${key} - ${stats}`);
      } else {
        console.error('❌ 模型加载失败:', result.reason.message);
      }
    }

    // C-20260816-204: 加载英超独立模型 (非阻塞，失败不影响全局模型)
    try {
      const eplResult = await this._loadModelFile(LGB_EPL_MODEL_PATH, 'LGB_EPL_MODEL', 'lgbEplModel');
      this.lgbEplModel = eplResult.data;
      // C-20260817-001: 同时加载特征列表和scaler获取维度信息
      let eplFeatureCount = '?';
      try {
        const featContent = await fs.promises.readFile(
          path.join(__dirname, '../../assets/selected_features_epl_export.js'), 'utf8'
        );
        const featData = this._safeParseModel(featContent, 'SELECTED_FEATURES_EPL', 'selectedFeaturesEpl');
        eplFeatureCount = featData.features ? featData.features.length : featData.length || '?';
      } catch (_) {}
      console.log(`✅ 英超独立模型已加载: ${eplResult.stats}, ${eplFeatureCount}维特征`);
      logger.info('prediction-service', `英超独立模型加载完成: ${eplResult.stats}, ${eplFeatureCount}维特征`);
    } catch (err) {
      console.warn('⚠️ 英超独立模型加载失败，将使用全局模型:', err.message);
      logger.warn('prediction-service', `英超独立模型加载失败: ${err.message}`);
      this.lgbEplModel = null;
    }

    // C-20260819-006: 加载 T-006 低进球分类器(非阻塞,失败不影响主预测)
    try {
      const t006Result = await this._loadModelFile(T006_LOWGOAL_MODEL_PATH, 'T006_LOWGOAL_MODEL', 't006LowgoalModel');
      this.t006LowgoalModel = t006Result.data;
      // 同时加载特征规格(可选,主要用于校验特征名完整性)
      try {
        const specContent = await fs.promises.readFile(T006_FEATURE_SPEC_PATH, 'utf8');
        this.t006FeatureSpec = this._safeParseModel(specContent, 'T006_FEATURE_SPEC', 't006FeatureSpec');
      } catch (_) { /* spec 缺失不致命 */ }
      const nTrees = this.t006LowgoalModel.trees?.length || 0;
      const nFeat = this.t006LowgoalModel.feature_cols?.length || 0;
      const threshold = this.t006LowgoalModel.best_threshold;
      const baseRate = this.t006LowgoalModel.base_rate;
      console.log(`✅ T-006 低进球分类器已加载: ${nTrees} 棵树, ${nFeat} 维特征, threshold=${threshold}, base_rate=${baseRate}`);
      logger.info('prediction-service', `T-006 加载完成: ${nTrees} 棵树, ${nFeat} 维, threshold=${threshold}, base_rate=${baseRate}`);
    } catch (err) {
      console.warn('⚠️ T-006 低进球分类器加载失败,低比分调整将被跳过:', err.message);
      logger.warn('prediction-service', `T-006 加载失败: ${err.message}`);
      this.t006LowgoalModel = null;
    }

    // 阶段 B (unified_engine_integration_plan §7): 加载 λ 回归头(非阻塞,失败不影响主预测)
    // 缺失时 engine.lambdaModel=null → createPredictionResult 回退 calcLambdaMatch 路径
    try {
      const lambdaResult = await this._loadModelFile(LAMBDA_MODEL_PATH, 'LAMBDA_MODEL', 'lambdaModel');
      this.lambdaModel = lambdaResult.data;
      const nModels = Object.keys(this.lambdaModel.models || {}).length;
      console.log(`✅ λ 回归头已加载: ${nModels} 个模型, ${(this.lambdaModel.feature_cols || []).length} 维特征`);
      logger.info('prediction-service', `λ回归头加载完成: ${nModels} 模型, ${(this.lambdaModel.feature_cols || []).length} 维`);
    } catch (err) {
      console.warn('⚠️ λ 回归头加载失败,将回退 calcLambdaMatch:', err.message);
      logger.warn('prediction-service', `λ回归头加载失败: ${err.message}`);
      this.lambdaModel = null;
    }

    const elapsed = Date.now() - startTime;
    console.log(`📊 模型加载完成: ${loadedCount}/3 成功, 耗时 ${elapsed}ms`);

    if (loadedCount > 0) {
      this.configureEngine();
    }

    return { loadedCount, elapsed };
  }

  async _loadModelFile(filePath, varName, targetKey) {
    const content = await fs.promises.readFile(filePath, 'utf8');
    const data = this._safeParseModel(content, varName, filePath);

    let stats = '';
    if (varName === 'XGB_MODEL' || varName === 'LGB_MODEL' || varName === 'LGB_EPL_MODEL') {
      stats = `${data.trees ? data.trees.length : 0} 棵树`;
    } else if (varName === 'FEATURE_SCALER_PARAMS') {
      stats = `${data.feature_names ? data.feature_names.length : 0} 个特征`;
    }

    return { key: targetKey, data, stats };
  }

  buildTeamNameIndex() {
    this.teamNameIndex = {};
    
    for (const [key, team] of Object.entries(this.teams)) {
      const name = team.name || '';
      
      if (name) {
        this.teamNameIndex[name.toLowerCase()] = key;
      }

      const englishName = this.getEnglishTeamName(name);
      if (englishName) {
        this.teamNameIndex[englishName.toLowerCase()] = key;
      }

      this.teamNameIndex[key.toLowerCase()] = key;
    }
  }

  /**
   * C-20260816-200: 按联赛获取 draw_threshold_factor（D3: 单一来源读 config.yaml）
   * @param {string} leagueCode - 联赛代码 (FL1/PL/BL1/LaLiga/IT)
   * @returns {number|null} 该联赛的 draw_threshold_factor；argmax 模式返回 null（不做阈值调整）
   */
  getLeagueDrawThresholdFactor(leagueCode) {
    // D3: 单一来源已迁至 config.yaml（模块加载时解析）
    //   argmax 模式: 不做任何平局阈值调整，返回 null（保持 argmax 行为不变）
    //   league_specific 模式: 返回 config 中该联赛因子，未配置联赛回退默认因子
    if (DRAW_THRESHOLD_MODE === "argmax") return null;
    return LEAGUE_DRAW_THRESHOLD_FACTOR[leagueCode] ?? DEFAULT_DRAW_THRESHOLD_FACTOR;
  }

  /**
   * C-20260816-200: 应用联赛专属 draw_threshold_factor 调整 WDL 预测
   * 决策阈值调整：不修改概率值，只改变分类决策
   * @param {Object} wdl - { win, draw, lose } 概率
   * @param {string} leagueCode - 联赛代码
   * @param {string} homeTeamName - 主队名 (用于日志)
   * @param {string} awayTeamName - 客队名 (用于日志)
   * @returns {Object} { win, draw, lose, predictedLabel, factor, thresholdInfo }
   */
  applyLeagueDrawThreshold(wdl, leagueCode, homeTeamName = '', awayTeamName = '') {
    const factor = this.getLeagueDrawThresholdFactor(leagueCode);
    const { win, draw, lose } = wdl;

    // argmax 基线
    const maxProb = Math.max(win, draw, lose);
    let predictedLabel;
    if (win === maxProb) predictedLabel = '主胜';
    else if (draw === maxProb) predictedLabel = '平局';
    else predictedLabel = '客胜';

    let thresholdInfo = null;
    let appliedLabel = predictedLabel;

    // factor > 0 时应用阈值调整
    //   语义: 0<factor<1 反压低平局（抬高门槛，边界平局降级为主胜/客胜）；factor>1 上浮平局（压低门槛）
    if (factor > 0) {
      const drawBoosted = draw * factor;
      if (drawBoosted > Math.max(win, lose)) {
        appliedLabel = '平局';
        thresholdInfo = {
          triggered: true,
          drawProb: draw,
          drawBoosted,
          winProb: win,
          loseProb: lose,
          factor
        };
      } else {
        // 平局未赢得阈值：主/客较大者胜出。0<factor<1 时实现「反压低平局」——边界平局降级为主胜/客胜
        appliedLabel = win >= lose ? '主胜' : '客胜';
        thresholdInfo = {
          triggered: false,
          drawProb: draw,
          drawBoosted,
          maxOther: Math.max(win, lose),
          factor
        };
      }
    } else {
      // factor<=0: argmax, 不做任何调整
      thresholdInfo = {
        triggered: false,
        drawProb: draw,
        factor,
        reason: 'factor<=0 (argmax, 不做平局阈值调整)'
      };
    }

    // C-20260816-205 + C-20260820-039: 详细运行时日志埋点（每个联赛，含未知联赛回退）
    //   记录每个联赛「实际应用」的 draw_threshold_factor 值及决策模式
    const LEAGUE_NAMES = {
      'FL1': '法甲', 'PL': '英超', 'BL1': '德甲', 'IT': '意甲', 'LaLiga': '西甲'
    };
    const leagueName = LEAGUE_NAMES[leagueCode] || (leagueCode ? `未知联赛:${leagueCode}` : '未知联赛');
    const mode = (factor <= 0 || factor === 1.0) ? 'argmax' : (factor < 1.0 ? '反压低平局' : '上浮平局');
    const matchTag = `${homeTeamName} vs ${awayTeamName}`;
    const probStr = `[主${win.toFixed(3)}/平${draw.toFixed(3)}/客${lose.toFixed(3)}]`;
    const decision = thresholdInfo.triggered
      ? '平局触发'
      : (predictedLabel === appliedLabel ? 'argmax(不变)' : '降级/推升');

    logger.info('draw-threshold',
      `[${leagueName}] ${matchTag} | factor=${factor} (${mode}) | ${probStr} | ` +
      `argmax=${predictedLabel} → applied=${appliedLabel} | ${decision}`,
      {
        leagueCode: leagueCode || null,
        leagueName,
        factor,
        mode,
        win: +win.toFixed(4),
        draw: +draw.toFixed(4),
        lose: +lose.toFixed(4),
        argmaxLabel: predictedLabel,
        appliedLabel,
        triggered: !!thresholdInfo.triggered,
        decision
      }
    );

    return {
      win,
      draw,
      lose,
      predictedLabel: appliedLabel,
      factor,
      thresholdInfo
    };
  }

  /**
   * 加载共享 TEAM_NAME_MAP (从 Python feature_utils.py 导出)
   * 解决 C-004: 双源映射统一
   * 解决 D-008: 硬编码球队映射消除 — 仅从 JSON 加载，无硬编码回退
   */
  _loadTeamNameMap() {
    if (this._teamNameMap) return this._teamNameMap;

    const mapPath = path.join(
      path.dirname(fileURLToPath(import.meta.url)),
      '../../assets/team_name_map.json'
    );

    try {
      if (fs.existsSync(mapPath)) {
        this._teamNameMap = JSON.parse(fs.readFileSync(mapPath, 'utf8'));
        const count = Object.keys(this._teamNameMap).length;
        if (count < 50) {
          console.warn(`⚠️ 共享球队名映射条目过少: ${count} 条 (预期 ≥100)，请重新运行 python scripts/export_team_name_map.py`);
        } else {
          console.log(`✅ 共享球队名映射已加载: ${count} 条`);
        }
        return this._teamNameMap;
      }
    } catch (err) {
      console.error(`❌ 共享球队名映射加载失败: ${err.message}`);
    }

    // 紧急回退: 仅保留最常用的球队名映射 (保证基本可用性)
    // 此回退不完整，请尽快修复 team_name_map.json
    console.error('❌❌❌ 严重: 球队名映射 JSON 缺失，使用最小回退映射 (仅 12 条) ❌❌❌');
    console.error('   请运行: python scripts/export_team_name_map.py');
    this._teamNameMap = {
      '阿森纳': 'arsenal', '切尔西': 'chelsea', '利物浦': 'liverpool',
      '曼城': 'manchester city', '曼联': 'manchester united',
      '热刺': 'tottenham hotspur', '巴塞罗那': 'barcelona',
      '皇家马德里': 'real madrid', '拜仁慕尼黑': 'bayern munich',
      '多特蒙德': 'borussia dortmund', '尤文图斯': 'juventus',
      '巴黎圣日耳曼': 'paris saint-germain',
    };
    return this._teamNameMap;
  }

  /**
   * 球队名映射查找 (带失效日志)
   * 解决 D-008: 每次查找失败自动记录，方便排查
   */
  getEnglishTeamName(chineseName) {
    if (!chineseName) return null;
    const nameMap = this._loadTeamNameMap();
    const result = nameMap[chineseName];
    if (!result) {
      this._logTeamNameMiss(chineseName);
    }
    return result || null;
  }

  /**
   * 记录球队名映射失效 (日志埋点)
   */
  _logTeamNameMiss(chineseName) {
    if (!this._teamNameMissStats) {
      this._teamNameMissStats = new Map();
    }
    const count = (this._teamNameMissStats.get(chineseName) || 0) + 1;
    this._teamNameMissStats.set(chineseName, count);

    // 首次失效时输出警告日志
    if (count === 1) {
      console.warn(`⚠️ 球队名映射失效: "${chineseName}" 未找到英文名，请更新 team_name_map.json`);
    } else if (count % 10 === 0) {
      // 每 10 次失效提醒一次
      console.warn(`⚠️ 球队名映射持续失效: "${chineseName}" (已 ${count} 次)`);
    }
  }

  /**
   * 获取球队名映射失效报告
   * 返回: { totalMisses, uniqueTeams, details: [{name, count}] }
   */
  getTeamNameMissReport() {
    if (!this._teamNameMissStats || this._teamNameMissStats.size === 0) {
      return { totalMisses: 0, uniqueTeams: 0, details: [] };
    }
    const details = Array.from(this._teamNameMissStats.entries())
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => b.count - a.count);
    const totalMisses = details.reduce((sum, d) => sum + d.count, 0);
    return { totalMisses, uniqueTeams: details.length, details };
  }

  /**
   * 重置失效统计
   */
  resetTeamNameMissCache() {
    this._teamNameMissStats = new Map();
  }

  findTeam(searchName) {
    if (!searchName) return null;
    
    const normalized = searchName.toLowerCase().trim();
    
    if (this.teamNameIndex[normalized]) {
      const key = this.teamNameIndex[normalized];
      return { key, team: this.teams[key] };
    }

    for (const [key, team] of Object.entries(this.teams)) {
      const name = team.name || '';
      if (name.toLowerCase().includes(normalized)) {
        return { key, team };
      }
    }

    for (const [key, team] of Object.entries(this.teams)) {
      const englishName = this.getEnglishTeamName(team.name);
      if (englishName && englishName.toLowerCase().includes(normalized)) {
        return { key, team };
      }
    }

    return null;
  }

  /**
   * 执行比赛预测 (6模型Stacking集成)
   */
  async predict(matchData) {
    const { homeTeam, awayTeam, venue = 'neutral', neutral = false, weather = 'normal' } = matchData;

    const homeResult = this.findTeam(homeTeam);
    const awayResult = this.findTeam(awayTeam);

    if (!homeResult || !awayResult) {
      const missing = [];
      if (!homeResult) missing.push(homeTeam);
      if (!awayResult) missing.push(awayTeam);
      throw new Error(`球队不存在: ${missing.join(' 或 ')}`);
    }

    const teamA = homeResult.team;
    const teamB = awayResult.team;
    const homeTeamKey = homeResult.key;
    const awayTeamKey = awayResult.key;

    // C-20260816-200: 检测联赛（从球队 league 字段）
    const leagueCode = teamA.league || teamB.league || null;

    // C-20260816-204: 英超独立模型分派 — 临时替换 LightGBM 模型
    const savedLgbModel = this.lgbModel;
    let eplModelUsed = false;
    if (leagueCode === 'PL' && this.lgbEplModel) {
      this.lgbModel = this.lgbEplModel;
      this.engine.setModels(this.xgbModel, this.lgbEplModel);
      eplModelUsed = true;
      logger.info('prediction-service', `[EPL模型] ${teamA.name} vs ${teamB.name} — 使用英超独立模型`);
    }

    const options = { venue, neutral, weather };
    
    // C-20260817-223: 缓存入口日志 — 记录预测请求开始
    const predictStart = Date.now();
    logger.info('prediction-service', `[预测-入口] ${teamA.name} vs ${teamB.name} | league=${leagueCode || 'N/A'} | epl=${eplModelUsed}`);
    
    const cachedPrediction = await cacheService.getPrediction(homeTeamKey, awayTeamKey, options);
    if (cachedPrediction) {
      const cacheHitTime = Date.now() - predictStart;
      logger.info('prediction-service', `[预测-缓存命中] ${teamA.name} vs ${teamB.name} | 总耗时 ${cacheHitTime}ms`);
      return cachedPrediction;
    }

    // B-006: 构建 matchContext 用于特征对齐
    const matchContext = this.buildMatchContext(homeTeamKey, awayTeamKey, teamA, teamB, matchData);

    const stackedResult = await this.engine.predictStacked(homeTeamKey, awayTeamKey, teamA, teamB, options, matchContext);

    // C-20260817-001: 预测关键节点日志 — 原始模型输出
    logger.info('prediction-service',
      `[预测-原始] ${teamA.name} vs ${teamB.name} | ` +
      `WDL=[${(stackedResult.wdl?.win||0).toFixed(4)}, ${(stackedResult.wdl?.draw||0).toFixed(4)}, ${(stackedResult.wdl?.lose||0).toFixed(4)}] | ` +
      `EPL=${eplModelUsed} | league=${leagueCode || 'N/A'}`
    );

    // C-20260816-204: 恢复全局模型（英超预测完成后）
    if (eplModelUsed) {
      this.lgbModel = savedLgbModel;
      this.engine.setModels(this.xgbModel, savedLgbModel);
    }

    // C-20260816-208: 构建赛季轮次、赔率趋势、联赛信息用于 λ 调整
    const matchRound = matchData.round || 1;

    // C-20260816-208: P1 赔率时序信号 — 从开/收盘赔率变化方向推断市场资金流向
    let oddsTrend = null;
    if (matchData.odds && matchData.odds.wdl) {
      const wdl = matchData.odds.wdl;
      const openWin = wdl.open?.win || 0;
      const openDraw = wdl.open?.draw || 0;
      const openLose = wdl.open?.lose || 0;
      const closeWin = wdl.close?.win || 0;
      const closeDraw = wdl.close?.draw || 0;
      const closeLose = wdl.close?.lose || 0;
      if (openWin > 0 && closeWin > 0) {
        oddsTrend = {
          homeDown: closeWin < openWin,     // 主胜赔率↓ → 市场看多主队
          drawDown: closeDraw < openDraw,   // 平局赔率↓ → 双方进球预期↑
          awayUp: closeLose > openLose,     // 客胜赔率↑ → 市场看空客队
        };
        logger.info('prediction-service',
          `[λ-trend] ${teamA.name} vs ${teamB.name} | ` +
          `主胜 ${openWin.toFixed(2)}→${closeWin.toFixed(2)} ${oddsTrend.homeDown ? '↓' : '→'} | ` +
          `平局 ${openDraw.toFixed(2)}→${closeDraw.toFixed(2)} ${oddsTrend.drawDown ? '↓' : '→'} | ` +
          `客胜 ${openLose.toFixed(2)}→${closeLose.toFixed(2)} ${oddsTrend.awayUp ? '↑' : '→'}`
        );
      }
    }

    const homeLambda = this.engine.calcLambdaMatch(teamA, teamB, {
      venue, neutral, isHome: true,
      round: matchRound,
      oddsTrend,
      league: leagueCode
    });
    const awayLambda = this.engine.calcLambdaMatch(teamB, teamA, {
      venue, neutral, isHome: false,
      round: matchRound,
      oddsTrend,
      league: leagueCode
    });

    // A-002: 构建赔率上下文，传递隐含总进球数 + 胜平负赔率用于 lambda 调整
    // P1 修复 (C-20260816-193): 传递 winOdds/drawOdds/loseOdds 用于计算赔率隐含 P(win)
    const oddsContext = matchData.odds ? {
      tgExpected: matchData.odds.tg?.expected ?? matchData.odds.tgExpected ?? 0,
      winOdds: matchData.odds.wdl?.close?.win ?? matchData.odds.wdl?.open?.win ?? 0,
      drawOdds: matchData.odds.wdl?.close?.draw ?? matchData.odds.wdl?.open?.draw ?? 0,
      loseOdds: matchData.odds.wdl?.close?.lose ?? matchData.odds.wdl?.open?.lose ?? 0
    } : null;

    const result = this.engine.createPredictionResult(
      homeTeamKey, awayTeamKey, teamA, teamB, stackedResult,
      homeLambda.lambdaA, awayLambda.lambdaA,
      { oddsContext }
    );

    // C-20260816-200: 应用联赛专属 draw_threshold_factor
    const wdl = result.predictions.winDrawLose;
    const thresholdResult = this.applyLeagueDrawThreshold(
      wdl, leagueCode, teamA.name, teamB.name
    );

    result.predictions.winDrawLoseThreshold = {
      ...thresholdResult,
      leagueCode,
      leagueFactor: thresholdResult.factor
    };
    result.predictions.predictedLabel = thresholdResult.predictedLabel;
    result.predictions.eplModelUsed = eplModelUsed;  // C-20260816-204

    // C-20260817-001 + C-20260820-039: 预测关键节点日志 — 最终预测结果（含联赛实际应用的 factor）
    const lambdaInfo = result.predictions.lambda ? 
      `λ=[${result.predictions.lambda.homeLambda?.toFixed(2)}, ${result.predictions.lambda.awayLambda?.toFixed(2)}]` : 'λ=N/A';
    logger.info('prediction-service',
      `[预测-最终] ${teamA.name} vs ${teamB.name} | ` +
      `预测=${thresholdResult.predictedLabel} | ` +
      `WDL=[${wdl.win.toFixed(4)}, ${wdl.draw.toFixed(4)}, ${wdl.lose.toFixed(4)}] | ` +
      `阈值触发=${thresholdResult.thresholdInfo ? (thresholdResult.thresholdInfo.triggered || false) : false} | ` +
      `factor=${thresholdResult.factor} | ${lambdaInfo}`,
      {
        leagueCode: leagueCode || null,
        leagueFactor: thresholdResult.factor,
        predictedLabel: thresholdResult.predictedLabel,
        thresholdTriggered: thresholdResult.thresholdInfo ? (thresholdResult.thresholdInfo.triggered || false) : false
      }
    );

    await cacheService.setPrediction(homeTeamKey, awayTeamKey, options, result);

    const totalTime = Date.now() - predictStart;
    logger.info('prediction-service', `[预测-出口] ${teamA.name} vs ${teamB.name} | 总耗时 ${totalTime}ms | 缓存已写入`);

    return result;
  }

  /**
   * B-006: 构建 matchContext，包含 Elo、H2H、赔率等特征数据
   */
  buildMatchContext(homeTeamKey, awayTeamKey, teamA, teamB, matchData) {
    const ctx = {};

    // P0-D: 传递比赛日期（时间特征 month/day_of_week/is_*_season 依赖）
    ctx.date = matchData.date ?? matchData.matchDate ?? null;

    // Elo 特征
    const homeElo = this.eloRatings[homeTeamKey] ?? 1500;
    const awayElo = this.eloRatings[awayTeamKey] ?? 1500;
    ctx.elo = {
      homeElo,
      awayElo,
      homeExpected: 1.0 / (1 + Math.pow(10, (awayElo - homeElo) / 400)),
      awayExpected: 1.0 / (1 + Math.pow(10, (homeElo - awayElo) / 400)),
      drawProb: 0.25,
      homeMomentum: (teamA?.eloMomentum ?? 0),
      awayMomentum: (teamB?.eloMomentum ?? 0),
      confidence: Math.min(1.0, Math.abs(homeElo - awayElo) / 400)
    };

    // 赔率特征 (从 matchData.odds 传入)
    if (matchData.odds) {
      const o = matchData.odds;
      ctx.odds = {
        wdlOpenWin: o.wdl?.open?.win ?? 0,
        wdlOpenDraw: o.wdl?.open?.draw ?? 0,
        wdlOpenLose: o.wdl?.open?.lose ?? 0,
        wdlCloseWin: o.wdl?.close?.win ?? o.wdl?.open?.win ?? 0,
        wdlCloseDraw: o.wdl?.close?.draw ?? o.wdl?.open?.draw ?? 0,
        wdlCloseLose: o.wdl?.close?.lose ?? o.wdl?.open?.lose ?? 0,
        wdlWinTrend: o.wdl?.winTrend ?? 0,
        wdlDrawTrend: o.wdl?.drawTrend ?? 0,
        wdlLoseTrend: o.wdl?.loseTrend ?? 0,
        wdlImpliedWin: o.wdl?.impliedWin ?? 0,
        wdlImpliedDraw: o.wdl?.impliedDraw ?? 0,
        wdlImpliedLose: o.wdl?.impliedLose ?? 0,
        wdlOverround: o.wdl?.overround ?? 0,
        wdlFavorite: o.wdl?.favorite ?? 0,
        wdlFavoriteProb: o.wdl?.favoriteProb ?? 0,
        wdlRecordCount: o.wdl?.recordCount ?? 0,
        wdlKellyWin: o.wdl?.kellyWin ?? 0,
        wdlKellyDraw: o.wdl?.kellyDraw ?? 0,
        wdlKellyLose: o.wdl?.kellyLose ?? 0,
        wdlWinChangeRate: o.wdl?.winChangeRate ?? 0,
        wdlDrawChangeRate: o.wdl?.drawChangeRate ?? 0,
        wdlLoseChangeRate: o.wdl?.loseChangeRate ?? 0,
        hcpOpenWin: o.hcp?.open?.win ?? 0,
        hcpOpenDraw: o.hcp?.open?.draw ?? 0,
        hcpOpenLose: o.hcp?.open?.lose ?? 0,
        hcpCloseWin: o.hcp?.close?.win ?? o.hcp?.open?.win ?? 0,
        hcpCloseDraw: o.hcp?.close?.draw ?? o.hcp?.open?.draw ?? 0,
        hcpCloseLose: o.hcp?.close?.lose ?? o.hcp?.open?.lose ?? 0,
        hcpWinTrend: o.hcp?.winTrend ?? 0,
        hcpDrawTrend: o.hcp?.drawTrend ?? 0,
        hcpLoseTrend: o.hcp?.loseTrend ?? 0,
        hcpImpliedWin: o.hcp?.impliedWin ?? 0,
        hcpImpliedDraw: o.hcp?.impliedDraw ?? 0,
        hcpImpliedLose: o.hcp?.impliedLose ?? 0,
        hcpRecordCount: o.hcp?.recordCount ?? 0,
        hcpKellyWin: o.hcp?.kellyWin ?? 0,
        hcpKellyDraw: o.hcp?.kellyDraw ?? 0,
        hcpKellyLose: o.hcp?.kellyLose ?? 0,
        hcpWinChangeRate: o.hcp?.winChangeRate ?? 0,
        hcpDrawChangeRate: o.hcp?.drawChangeRate ?? 0,
        hcpLoseChangeRate: o.hcp?.loseChangeRate ?? 0,
        tgOver25: o.tg?.over25 ?? 0,
        tgUnder25: o.tg?.under25 ?? 0,
        tgMostLikely: o.tg?.mostLikely ?? 0,
        tgMostLikelyProb: o.tg?.mostLikelyProb ?? 0,
        tgExpected: o.tg?.expected ?? 2.5,
        tgRecordCount: o.tg?.recordCount ?? 0,
        hasWdlOdds: !!(o.wdl?.open?.win || o.wdl?.close?.win),
        hasHcpOdds: !!(o.hcp?.open?.win || o.hcp?.close?.win),
        hasTgOdds: !!(o.tg?.over25 || o.tg?.under25),
        valueBetHome: o.valueBetHome ?? 0,
        valueBetAway: o.valueBetAway ?? 0
      };
    }

    // P0-D: 接入条件输出上下文（h2h / oddsTiming / scoreOdds，29 维）。
    // 数据层（data-service / routes）需在 matchData 上提供对应结构；
    // 字段名与 shared/prediction-engine.js buildFeatures 消费端一致。
    if (matchData.h2h) {
      ctx.h2h = matchData.h2h;
    }
    if (matchData.oddsTiming) {
      ctx.oddsTiming = matchData.oddsTiming;
    }
    if (matchData.scoreOdds) {
      // 既可能是派生态 {modeProb, entropy, ...}，也可能是原始比分赔率 {score: odds}
      if (matchData.scoreOdds.modeProb !== undefined) {
        ctx.scoreOdds = matchData.scoreOdds;
      } else {
        ctx.scoreOdds = this.deriveScoreOddsFeatures(matchData.scoreOdds);
      }
    }

    // P0-D: 接入预计算桥接特征（sofa_*/pa_*/mkt_共识/odds_ts_ 共 110 维真实值）。
    // mkt_dev_* 4 维依赖实时竞彩隐含概率，在此即时计算。
    const bridged = featureBridge.lookup(ctx.date, teamA?.name, teamB?.name);
    if (bridged) {
      if (ctx.odds?.hasWdlOdds && bridged.mkt_imp_win !== undefined) {
        const iw = ctx.odds.wdlImpliedWin ?? 0;
        const id = ctx.odds.wdlImpliedDraw ?? 0;
        const il = ctx.odds.wdlImpliedLose ?? 0;
        bridged.mkt_dev_win = iw - bridged.mkt_imp_win;
        bridged.mkt_dev_draw = id - bridged.mkt_imp_draw;
        bridged.mkt_dev_lose = il - bridged.mkt_imp_lose;
        bridged.mkt_dev_abs = Math.abs(bridged.mkt_dev_win)
          + Math.abs(bridged.mkt_dev_draw) + Math.abs(bridged.mkt_dev_lose);
      }
      ctx.externalFeatures = bridged;
    }

    return ctx;
  }

  /**
   * P0-D: 从原始比分赔率 {score: odds} 派生 8 维 score_* 特征，
   * 与 feature_utils.py score_* 公式对齐（去抽水隐含概率 → modeProb/entropy/WDL/over25/expectedGoals/top3）。
   */
  deriveScoreOddsFeatures(scoreOdds) {
    const feats = {
      modeProb: 0, entropy: 0, homeWinProb: 0.33, drawProb: 0.34, awayWinProb: 0.33,
      over25Prob: 0.5, expectedGoals: 2.5, top3Concentration: 0
    };
    if (!scoreOdds || typeof scoreOdds !== 'object' || Object.keys(scoreOdds).length === 0) {
      return feats;
    }

    // 去抽水隐含概率
    let total = 0;
    const probs = {};
    for (const [score, odds] of Object.entries(scoreOdds)) {
      const o = Number(odds);
      if (!o || o <= 0) continue;
      probs[score] = 1 / o;
      total += probs[score];
    }
    if (total <= 0) return feats;
    for (const k of Object.keys(probs)) probs[k] /= total;

    // WDL / over25 / expectedGoals / modeProb / entropy
    let homeWin = 0, draw = 0, awayWin = 0, over25 = 0, expGoals = 0;
    let modeP = 0, entropy = 0;
    for (const [score, p] of Object.entries(probs)) {
      const [h, a] = score.split(':').map(Number);
      if (Number.isNaN(h) || Number.isNaN(a)) continue;
      if (h > a) homeWin += p;
      else if (h === a) draw += p;
      else awayWin += p;
      if (h + a > 2.5) over25 += p;
      expGoals += (h + a) * p;
      if (p > modeP) { modeP = p; }
      entropy -= p > 0 ? p * Math.log(p) : 0;
    }
    const sorted = Object.values(probs).sort((a, b) => b - a);
    const top3 = sorted.slice(0, 3).reduce((s, v) => s + v, 0);

    feats.modeProb = modeP;
    feats.entropy = entropy;
    feats.homeWinProb = homeWin || 0.33;
    feats.drawProb = draw || 0.34;
    feats.awayWinProb = awayWin || 0.33;
    feats.over25Prob = over25 || 0.5;
    feats.expectedGoals = expGoals || 2.5;
    feats.top3Concentration = top3;
    return feats;
  }

  /**
   * 融合赔率数据的预测
   */
  async predictWithOdds(matchData) {
    const basePrediction = await this.predict(matchData);
    const { odds } = matchData;

    if (!odds) return basePrediction;

    const impliedProb = this.engine.calcImpliedProbability(odds);
    const valueAnalysis = this.engine.calcValueAnalysis(basePrediction.predictions.winDrawLose, impliedProb);

    return {
      ...basePrediction,
      odds: {
        provided: odds,
        implied: impliedProb
      },
      valueAnalysis
    };
  }

  /**
   * 融合首发阵容的预测
   */
  async predictWithLineup(matchData) {
    const basePrediction = await this.predict(matchData);
    const { homeLineup, awayLineup } = matchData;

    if (!homeLineup || !awayLineup) return basePrediction;

    const homeImpact = this.engine.calcPlayerImpact(homeLineup, this.leagueTier, this.leagueTierWeight);
    const awayImpact = this.engine.calcPlayerImpact(awayLineup, this.leagueTier, this.leagueTierWeight);

    const adjustedLambdaA = basePrediction.lambda.home * (1 + homeImpact.total * 0.1);
    const adjustedLambdaB = basePrediction.lambda.away * (1 + awayImpact.total * 0.1);

    const scoreProbabilities = this.engine.calcPoissonProbabilities(adjustedLambdaA, adjustedLambdaB);
    const winDrawLose = this.engine.calcWinDrawLose(scoreProbabilities);

    return {
      ...basePrediction,
      lineup: {
        home: homeLineup,
        away: awayLineup
      },
      playerImpact: {
        home: homeImpact,
        away: awayImpact
      },
      adjustedLambda: { home: adjustedLambdaA, away: adjustedLambdaB },
      adjustedPredictions: {
        winDrawLose,
        topScores: this.engine.getTopScores(scoreProbabilities, 10)
      }
    };
  }

  /**
   * 让球胜平负预测（独立端点）
   * @param {Object} matchData - { homeTeam, awayTeam, handicap, options }
   * @returns {Object} 让球预测结果
   */
  async predictHandicap(matchData) {
    const { homeTeam, awayTeam, handicap = 0, ...options } = matchData;

    const basePrediction = await this.predict({ homeTeam, awayTeam, ...options });
    const { lambda } = basePrediction;
    const { home: lambdaHome, away: lambdaAway } = lambda;

    // 根据让球盘口计算让球胜平负
    // handicap > 0: 主队让球（如 -1 表示主队让 1 球）
    // handicap < 0: 客队让球
    const adjustedHome = lambdaHome;
    const adjustedAway = lambdaAway;

    let hWin = 0, hDraw = 0, hLose = 0;
    const maxGoals = 7;

    for (let a = 0; a <= maxGoals; a++) {
      for (let b = 0; b <= maxGoals; b++) {
        const prob = this.engine.poissonPMF(adjustedHome, a) * this.engine.poissonPMF(adjustedAway, b);
        const adjustedDiff = a - b + handicap; // 让球调整

        if (adjustedDiff > 0) hWin += prob;
        else if (adjustedDiff === 0) hDraw += prob;
        else hLose += prob;
      }
    }

    // 归一化
    const total = hWin + hDraw + hLose;
    if (total > 0) {
      hWin /= total;
      hDraw /= total;
      hLose /= total;
    }

    return {
      ...basePrediction,
      handicap: {
        value: handicap,
        prediction: { win: hWin, draw: hDraw, lose: hLose },
        description: handicap > 0 ? `主队让 ${handicap} 球` : handicap < 0 ? `客队让 ${Math.abs(handicap)} 球` : '平手'
      }
    };
  }

  /**
   * 精确比分预测（独立端点，使用 T-006 v4 算法 + T-006 低进球分类器后验调整）
   * C-20260819-006: 在 fuseScorePredictions 之后,若 t006 分类器预测 P(小球) ≥ threshold,
   *                 对低比分(0:0/0:1/1:0/1:1)做后验重加权(匹配 Python adjust_lowgoal_weights_by_prior)
   * @param {Object} matchData - { homeTeam, awayTeam, scoreOdds, odds:{wdl:{close:{win,draw,lose}}}, options }
   * @returns {Object} 比分预测结果
   */
  async predictScore(matchData) {
    const { homeTeam, awayTeam, scoreOdds = null, ...options } = matchData;

    const basePrediction = await this.predict({ homeTeam, awayTeam, ...options });
    const { lambda } = basePrediction;
    const { home: lambdaHome, away: lambdaAway } = lambda;

    // T-006 v4 完整流程
    const v4Result = this.engine.predictScoreV4(lambdaHome, lambdaAway, {
      rho: options.rho || -0.30,
      rhoHigh: options.rhoHigh || -0.10
    });

    const mcProbs = this.engine.monteCarloScoreSimulate(lambdaHome, lambdaAway, 3000, 7);
    let fusedScores = this.engine.fuseScorePredictions(
      v4Result.scoreProbabilities,
      mcProbs,
      scoreOdds
    );

    // === C-20260819-006: T-006 低进球分类器后验调整 ===
    let t006Info = { applied: false, skipped: true, reason: 'not_run' };
    if (this.t006LowgoalModel) {
      const leagueCode = basePrediction?.teams?.home?.league
                      || basePrediction?.teams?.away?.league
                      || null;
      // WDL 回退:优先用 matchData.odds.wdl,缺失时用 stacked 预测概率反推虚拟赔率
      const wdlPrediction = basePrediction?.predictions?.winDrawLose;
      const wdlFallback = wdlPrediction
        ? { win: wdlPrediction.win, draw: wdlPrediction.draw, lose: wdlPrediction.lose }
        : null;

      const t006Features = this.engine.buildT006Features(
        { scoreOdds, odds: matchData.odds, leagueCode },
        wdlFallback
      );
      const t006Result = this.engine.predictT006Lowgoal(t006Features);

      if (!t006Result.skipped) {
        const preLowSum = (fusedScores['0:0'] || 0) + (fusedScores['0:1'] || 0)
                        + (fusedScores['1:0'] || 0) + (fusedScores['1:1'] || 0);
        fusedScores = this.engine.applyT006LowgoalAdjustment(fusedScores, t006Result, 0.5);
        const postLowSum = (fusedScores['0:0'] || 0) + (fusedScores['0:1'] || 0)
                         + (fusedScores['1:0'] || 0) + (fusedScores['1:1'] || 0);
        const factor = t006Result.isLowgoal
          ? Math.max(0.5, Math.min(2.0, 1 + 0.5 * (t006Result.probability - t006Result.baseRate) / t006Result.baseRate))
          : 1.0;
        t006Info = {
          applied: t006Result.isLowgoal,
          skipped: false,
          pLow: t006Result.probability,
          threshold: t006Result.threshold,
          baseRate: t006Result.baseRate,
          rawScore: t006Result.rawScore,
          preLowSum,
          postLowSum,
          factor
        };
        logger.info('t006-adjust',
          `[T006] ${homeTeam} vs ${awayTeam} | league=${leagueCode || 'N/A'} | ` +
          `pLow=${t006Result.probability.toFixed(4)} | threshold=${t006Result.threshold} | ` +
          `isLow=${t006Result.isLowgoal} | factor=${factor.toFixed(3)} | ` +
          `lowSum ${preLowSum.toFixed(4)}→${postLowSum.toFixed(4)}`
        );
      } else {
        t006Info = { applied: false, skipped: true, reason: t006Result.reason };
        logger.info('t006-adjust', `[T006] ${homeTeam} vs ${awayTeam} | skipped: ${t006Result.reason}`);
      }
    } else {
      logger.info('t006-adjust', `[T006] ${homeTeam} vs ${awayTeam} | model not loaded, skipped`);
    }

    const topScores = this.engine.getTopScores(fusedScores, 10);

    // 计算 1 球内命中率
    let within1Prob = 0;
    for (const { score, probability } of topScores) {
      const [h, a] = score.split(':').map(Number);
      if (Math.abs(h - a) <= 1) within1Prob += probability;
    }

    // 计算精确命中概率
    const exactProb = topScores[0] ? topScores[0].probability : 0;

    return {
      match: basePrediction.match,
      lambda: basePrediction.lambda,
      scorePrediction: {
        topScores,
        exactProbability: exactProb,
        within1Probability: within1Prob,
        scoreModel: 'T-006-v4',
        correction: v4Result.correctionStats,
        scoreOddsUsed: !!scoreOdds,
        totalScores: Object.keys(fusedScores).length,
        t006: t006Info  // C-20260819-006: t006 调整元数据
      },
      fullDistribution: fusedScores,
      confidence: basePrediction.confidence,
      timestamp: new Date().toISOString()
    };
  }

  calcLambda(teamA, teamB, options) {
    return this.engine.calcLambda(teamA, teamB, options);
  }

  calcPoissonProbabilities(lambdaA, lambdaB) {
    return this.engine.calcPoissonProbabilities(lambdaA, lambdaB);
  }

  calcWinDrawLose(scoreProbabilities) {
    return this.engine.calcWinDrawLose(scoreProbabilities);
  }

  calcHandicap(lambdaA, lambdaB) {
    return this.engine.calcHandicap(lambdaA, lambdaB);
  }

  calcTotalGoals(lambdaA, lambdaB) {
    return this.engine.calcTotalGoals(lambdaA, lambdaB);
  }

  getTopScores(scoreProbabilities, limit) {
    return this.engine.getTopScores(scoreProbabilities, limit);
  }

  calcImpliedProbability(odds) {
    return this.engine.calcImpliedProbability(odds);
  }

  calcValueAnalysis(modelProb, impliedProb) {
    return this.engine.calcValueAnalysis(modelProb, impliedProb);
  }

  calcPlayerImpact(lineup) {
    return this.engine.calcPlayerImpact(lineup, this.leagueTier, this.leagueTierWeight);
  }

  /**
   * 获取支持的球队列表
   */
  getSupportedTeams() {
    return Object.entries(this.teams).map(([key, team]) => ({
      key,
      name: team.name,
      league: team.league || team.group,
      attack: team.attack,
      defence: team.defence,
      xGOT: team.xGOT,
      xGA: team.xGA,
      marketValue: team.marketValue,
      tactical: team.tactical,
      cohesion: team.cohesion,
      recentForm: team.recentForm
    }));
  }

  /**
   * 获取球队数据
   */
  getTeamData(teamKey) {
    const result = this.findTeam(teamKey);
    return result ? result.team : null;
  }

  /**
   * 获取球队球员数据
   */
  getTeamPlayers(teamKey) {
    return this.playerPostmatchRatings[teamKey];
  }

  /**
   * 比较两支球队
   */
  compareTeams(team1Key, team2Key) {
    const result1 = this.findTeam(team1Key);
    const result2 = this.findTeam(team2Key);

    if (!result1 || !result2) {
      throw new Error('球队不存在');
    }

    const team1 = result1.team;
    const team2 = result2.team;
    team1Key = result1.key;
    team2Key = result2.key;

    const dimensions = [
      { key: 'attack', name: '进攻' },
      { key: 'defence', name: '防守' },
      { key: 'fifaRank', name: 'FIFA排名' },
      { key: 'xGOT', name: '预期进球' },
      { key: 'xGA', name: '预期失球' },
      { key: 'marketValue', name: '身价' },
      { key: 'tempo', name: '节奏' },
      { key: 'pressIntensity', name: '逼抢强度' }
    ];

    const comparison = dimensions.map(d => ({
      dimension: d.name,
      team1: team1[d.key],
      team2: team2[d.key],
      diff: team1[d.key] - team2[d.key],
      winner: team1[d.key] > team2[d.key] ? team1Key : team2Key
    }));

    return {
      team1: { key: team1Key, name: team1.name },
      team2: { key: team2Key, name: team2.name },
      comparison
    };
  }

  /**
   * 更新球队数据
   */
  async updateTeamData(teamKey, updates) {
    const result = this.findTeam(teamKey);
    if (!result) {
      throw new Error('球队不存在');
    }

    teamKey = result.key;
    Object.assign(this.teams[teamKey], updates);

    // 保存到文件（可选，需要实现持久化）
    return this.teams[teamKey];
  }

  /**
   * 保存预测到历史
   * @param {string} userId - 用户ID
   * @param {object} prediction - 完整预测结果
   * @param {string} [league] - 联赛
   * @returns {Promise<object>} 保存后的记录（含 id, savedAt）
   */
  async saveHistory(userId, prediction, league = null) {
    if (!prediction) {
      throw new Error('预测结果不能为空');
    }

    const homeTeam = prediction?.match?.homeTeam?.name || prediction?.teams?.home?.name || '';
    const awayTeam = prediction?.match?.awayTeam?.name || prediction?.teams?.away?.name || '';
    const wdl = prediction?.predictions?.winDrawLose || {};
    const winProb = wdl.win != null ? Number(wdl.win) : null;
    const drawProb = wdl.draw != null ? Number(wdl.draw) : null;
    const loseProb = wdl.lose != null ? Number(wdl.lose) : null;
    const confidence = prediction?.confidence != null ? Number(prediction.confidence) : null;
    const modelVersion = prediction?.modelVersion || prediction?.ensemble?.modelVersion || null;

    const result = db.run(
      `INSERT INTO prediction_history
        (userId, homeTeam, awayTeam, league, winProb, drawProb, loseProb, confidence, predictionJson, modelVersion)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        userId || null,
        homeTeam,
        awayTeam,
        league,
        winProb,
        drawProb,
        loseProb,
        confidence,
        JSON.stringify(prediction),
        modelVersion
      ]
    );

    const id = (await result).lastID;
    logger.info('prediction-service', `预测历史已保存: id=${id}, ${homeTeam} vs ${awayTeam}`);

    return {
      id,
      savedAt: Date.now(),
      ...prediction
    };
  }

  /**
   * 获取预测历史
   * @param {string} userId - 用户ID
   * @param {number} limit - 返回条数
   * @param {number} offset - 偏移量
   * @returns {Promise<Array>} 历史记录数组（每条包含完整 prediction 字段和 savedAt）
   */
  async getHistory(userId, limit = 20, offset = 0) {
    const rows = await db.all(
      `SELECT id, userId, homeTeam, awayTeam, league, winProb, drawProb, loseProb,
              confidence, predictionJson, modelVersion, createdAt
       FROM prediction_history
       WHERE userId = ?
       ORDER BY id DESC
       LIMIT ? OFFSET ?`,
      [userId, Number(limit) || 20, Number(offset) || 0]
    );

    return rows.map(row => {
      let prediction = {};
      try {
        prediction = JSON.parse(row.predictionJson || '{}');
      } catch (e) {
        logger.warn('prediction-service', `解析预测历史JSON失败: id=${row.id}`);
      }
      return {
        id: row.id,
        savedAt: new Date(row.createdAt + 'Z').getTime() || Date.now(),
        ...prediction
      };
    });
  }

  /**
   * 删除单条预测历史
   * @param {string} userId - 用户ID（权限校验）
   * @param {number} id - 记录ID
   * @returns {Promise<boolean>} 是否删除成功
   */
  async deleteHistoryItem(userId, id) {
    const result = await db.run(
      'DELETE FROM prediction_history WHERE id = ? AND userId = ?',
      [id, userId]
    );
    return result.changes > 0;
  }

  /**
   * 清空指定用户的预测历史
   * @param {string} userId - 用户ID
   * @returns {Promise<number>} 删除的条数
   */
  async clearHistory(userId) {
    const result = await db.run(
      'DELETE FROM prediction_history WHERE userId = ?',
      [userId]
    );
    logger.info('prediction-service', `清空预测历史: userId=${userId}, 删除${result.changes}条`);
    return result.changes;
  }

  predictMatch(teamAKey, teamBKey, options) {
    const teamA = this.teams[teamAKey];
    const teamB = this.teams[teamBKey];
    return teamA && teamB ? this.engine.predictMatch(teamA, teamB, options) : { winA: 0.33, draw: 0.34, winB: 0.33 };
  }

  predictMatchDC(teamAKey, teamBKey, options) {
    const teamA = this.teams[teamAKey];
    const teamB = this.teams[teamBKey];
    return teamA && teamB ? this.engine.predictMatchDC(teamA, teamB, options) : { winA: 0.33, draw: 0.34, winB: 0.33 };
  }

  predictMatchSSM(teamAKey, teamBKey, options) {
    const teamA = this.teams[teamAKey];
    const teamB = this.teams[teamBKey];
    return teamA && teamB ? this.engine.predictMatchSSM(teamA, teamB, options) : { winA: 0.33, draw: 0.34, winB: 0.33 };
  }

  predictXGB(teamAKey, teamBKey, options) {
    const teamA = this.teams[teamAKey];
    const teamB = this.teams[teamBKey];
    return teamA && teamB ? this.engine.predictXGB(teamA, teamB, options) : { winA: 0.33, draw: 0.34, winB: 0.33 };
  }

  predictLightGBM(teamAKey, teamBKey, options) {
    const teamA = this.teams[teamAKey];
    const teamB = this.teams[teamBKey];
    return teamA && teamB ? this.engine.predictLightGBM(teamA, teamB, options) : { winA: 0.33, draw: 0.34, winB: 0.33 };
  }

  predictElo(teamAKey, teamBKey, options) {
    return this.engine.predictElo(teamAKey, teamBKey, options);
  }

  async predictStacked(teamAKey, teamBKey, options) {
    const teamA = this.teams[teamAKey];
    const teamB = this.teams[teamBKey];
    return teamA && teamB ? this.engine.predictStacked(teamAKey, teamBKey, teamA, teamB, options) : { winA: 0.33, draw: 0.34, winB: 0.33 };
  }

  calcModelDisagreement(modelProbs) {
    return this.engine.calcModelDisagreement(modelProbs);
  }

  calcConfidence(winA, draw, winB, disagreement) {
    return this.engine.calcConfidence(winA, draw, winB, disagreement);
  }

  calcLambdaMatch(teamA, teamB, options) {
    return this.engine.calcLambdaMatch(teamA, teamB, options);
  }

  calcWinDrawLosePoisson(lambdaA, lambdaB) {
    return this.engine.calcWinDrawLosePoisson(lambdaA, lambdaB);
  }

  dixonColePMF(x, y, lambda1, lambda2, rho) {
    return this.engine.dixonColePMF(x, y, lambda1, lambda2, rho);
  }

  buildFeatures(teamA, teamB, options) {
    return this.engine.buildFeatures(teamA, teamB, options);
  }

  normalizeFeatures(features) {
    return this.engine.normalizeFeatures(features);
  }

  getFeatureVector(features) {
    return this.engine.getFeatureVector(features);
  }

  treeModelPredict(model, features) {
    return this.engine.treeModelPredict(model, features);
  }

  predictTree(tree, features) {
    return this.engine.predictTree(tree, features);
  }
}

// 单例模式导出
const service = new PredictionService();
export default service;