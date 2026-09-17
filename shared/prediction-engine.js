/**
 * 共享预测引擎模块
 * 核心算法和特征工程，可同时用于浏览器端和服务器端
 */

const DEFAULT_STACKING_WEIGHTS = {
  dixonCole: 0.30,
  xgboost: 0.30,
  lightgbm: 0.25,
  elo: 0.15,
};

const WEATHER_IMPACT = {
  clear: { attack: 1.0, defence: 1.0 },
  cloudy: { attack: 0.98, defence: 1.02 },
  rain: { attack: 0.92, defence: 1.08 },
  heavy_rain: { attack: 0.85, defence: 1.15 },
  snow: { attack: 0.80, defence: 1.20 },
  fog: { attack: 0.85, defence: 1.10 },
  wind: { attack: 0.95, defence: 1.05 },
  extreme_wind: { attack: 0.88, defence: 1.12 }
};

const SURFACE_IMPACT = {
  grass: { attack: 1.0, defence: 1.0 },
  artificial: { attack: 1.05, defence: 0.98 },
  hybrid: { attack: 1.02, defence: 0.99 },
  frozen: { attack: 0.85, defence: 1.15 },
  waterlogged: { attack: 0.80, defence: 1.20 }
};

// P0-D: 训练-推理特征对齐 — JS 联赛代码/名称 → 训练端联赛 one-hot 中文名
// (训练端 competition_name 归一为: 英超/德甲/意甲/法甲/西甲)
const LEAGUE_NAME_MAP = {
  'PL': '英超', 'Premier League': '英超', 'English Premier League': '英超', '英超': '英超',
  'BL1': '德甲', 'Bundesliga': '德甲', '德甲': '德甲',
  'FL1': '法甲', 'Ligue 1': '法甲', '法甲': '法甲',
  'IT': '意甲', 'Serie A': '意甲', '意甲': '意甲',
  'SA': '西甲', 'La Liga': '西甲', 'Liga': '西甲', '西甲': '西甲'
};

class PredictionEngine {
  constructor() {
    this.stackingWeights = { ...DEFAULT_STACKING_WEIGHTS };
    this.featureScalerParams = null;
    this.xgbModel = {};
    this.lgbModel = {};
    this.t006LowgoalModel = null;  // C-20260819-005: T-006 低进球分类器(独立二分类)
    this.lambdaModel = null;       // 阶段 B (unified_engine_integration_plan §7): λ 回归头
    this.eloRatings = {};
    this.xgbCalibration = null;
    this.lgbCalibration = null;
    this.confidenceHistory = [];
    this.matchTypeWeights = {
      regular: { dixonCole: 0.30, xgboost: 0.30, lightgbm: 0.25, elo: 0.15 },
      derby: { dixonCole: 0.35, xgboost: 0.25, lightgbm: 0.25, elo: 0.15 },
      cup: { dixonCole: 0.25, xgboost: 0.30, lightgbm: 0.25, elo: 0.20 },
      promotion: { dixonCole: 0.30, xgboost: 0.35, lightgbm: 0.25, elo: 0.10 },
      relegation: { dixonCole: 0.30, xgboost: 0.30, lightgbm: 0.25, elo: 0.15 }
    };
  }

  setStackingWeights(weights) {
    this.stackingWeights = { ...DEFAULT_STACKING_WEIGHTS, ...weights };
  }

  setFeatureScalerParams(params) {
    this.featureScalerParams = params;
  }

  setModels(xgbModel, lgbModel) {
    this.xgbModel = xgbModel || {};
    this.lgbModel = lgbModel || {};
  }

  /**
   * 阶段 B (unified_engine_integration_plan §7): 设置 λ 回归头模型
   * @param {Object|null} model - LAMBDA_MODEL 结构
   *   { feature_cols: [...], models: { xgb_home/xgb_away/lgb_home/lgb_away: {base,lr,trees} },
   *     league_rho: {英超: -0.08, ...} }
   */
  setLambdaModel(model) {
    this.lambdaModel = model || null;
  }

  /**
   * C-20260819-005: 设置 T-006 低进球分类器模型
   * @param {Object|null} model - T006_LOWGOAL_MODEL 结构 (base/lr/trees/feature_cols/best_threshold/base_rate)
   */
  setT006LowgoalModel(model) {
    this.t006LowgoalModel = model || null;
  }

  setCalibrationParams(xgbCal, lgbCal) {
    this.xgbCalibration = xgbCal || null;
    this.lgbCalibration = lgbCal || null;
  }

  setEloRatings(eloRatings) {
    this.eloRatings = eloRatings || {};
  }

  addConfidenceRecord(confidence, wasCorrect) {
    this.confidenceHistory.push({ confidence, wasCorrect, timestamp: Date.now() });
    if (this.confidenceHistory.length > 100) {
      this.confidenceHistory.shift();
    }
  }

  poissonPMF(lambda, k) {
    if (k === 0) return Math.exp(-lambda);
    let prob = Math.exp(-lambda);
    for (let i = 1; i <= k; i++) {
      prob *= lambda / i;
    }
    return prob;
  }

  /**
   * C-20260823-P0: 数据驱动天气影响因子（修复硬编码默认值）
   * 
   * 优先使用 Python match_condition_features.py 生成的数据驱动因子，
   * 降级使用旧的硬编码 WEATHER_IMPACT 映射。
   * 
   * @param {Object} weather - 天气数据，支持两种格式：
   *   新格式: { attack_impact: 0.92, defence_impact: 1.08, type: 'rain', is_estimate: true }
   *   旧格式: { type: 'rain' } (降级到硬编码 WEATHER_IMPACT)
   * @returns {Object} { attack: number, defence: number }
   */
  calcWeatherImpact(weather) {
    // P0-修复: 优先使用数据驱动因子
    if (weather && typeof weather.attack_impact === 'number' && typeof weather.defence_impact === 'number') {
      return {
        attack: weather.attack_impact,
        defence: weather.defence_impact,
        _source: weather.is_estimate ? 'climate_estimate' : 'live_weather'
      };
    }
    // 降级: 旧硬编码映射
    const weatherType = weather?.type || 'clear';
    const impact = WEATHER_IMPACT[weatherType] || WEATHER_IMPACT.clear;
    return { ...impact, _source: 'hardcoded' };
  }

  calcSurfaceImpact(surface) {
    const surfaceType = surface || 'grass';
    const impact = SURFACE_IMPACT[surfaceType] || SURFACE_IMPACT.grass;
    return { ...impact, _source: 'hardcoded' };
  }

  /**
   * C-20260823-P0: 数据驱动 λ 计算（修复天气/伤病默认值）
   *
   * 优先级: options.matchConditions (Python生成) > teamA/teamB 直接属性 > 硬编码默认值
   */
  calcLambdaMatch(teamA, teamB, options) {
    const venue = options.venue || 'home';
    const neutral = options.neutral || false;

    // P0-修复: 优先使用 options.matchConditions 中的天气数据
    const mc = options.matchConditions || {};
    const weatherData = mc.weather || options.weather;
    
    const weatherImpact = this.calcWeatherImpact(weatherData);
    const surfaceImpact = this.calcSurfaceImpact(options.surface);
    
    const homeAdv = neutral ? 1.0 : 1.12;
    
    // P0-修复: 伤病因子优先级
    //   1. options.matchConditions.home_injury (Python 数据驱动)
    //   2. teamA.injury (上层直接传入)
    //   3. 1.0 (硬编码默认值 - 已废弃)
    const injuryA = mc.home_injury ?? teamA.injury ?? 1.0;
    const keyPlayerA = mc.home_key_player ?? teamA.keyPlayer ?? 1.0;
    const injuryB = mc.away_injury ?? teamB.injury ?? 1.0;
    const keyPlayerB = mc.away_key_player ?? teamB.keyPlayer ?? 1.0;
    const tempoA = teamA.tempo !== undefined ? teamA.tempo : 0.7;
    const tempoB = teamB.tempo !== undefined ? teamB.tempo : 0.7;

    const attackA = teamA.attack * injuryA * keyPlayerA * weatherImpact.attack * surfaceImpact.attack;
    const defenceA = teamA.defence * weatherImpact.defence * surfaceImpact.defence;
    const attackB = teamB.attack * injuryB * keyPlayerB * weatherImpact.attack * surfaceImpact.attack;
    const defenceB = teamB.defence * weatherImpact.defence * surfaceImpact.defence;

    const xGA = teamA.xGOT || teamA.attack * 1.0;
    const xGB = teamB.xGOT || teamB.attack * 1.0;
    // C-20260816-196: 修复 xGAA/xGAB fallback 方向错误
    const xGAA = teamA.xGA || (1 - teamA.defence) * 1.8;
    const xGAB = teamB.xGA || (1 - teamB.defence) * 1.8;

    const isHomeA = options.isHome !== undefined ? options.isHome : (venue === 'home');
    const homeFactorA = isHomeA ? homeAdv : 1.0;
    const homeFactorB = isHomeA ? 1.0 : homeAdv;

    // C-20260816-199: 修复 (1 - xGAB) 项方向
    let lambdaA = ((attackA * (1 - defenceB) * 0.3 + xGA * (1 - defenceB) * 0.3 + homeFactorA * 0.2 + xGAB * 0.2) * tempoA);
    let lambdaB = ((attackB * (1 - defenceA) * 0.3 + xGB * (1 - defenceA) * 0.3 + homeFactorB * 0.2 + xGAA * 0.2) * tempoB);

    // C-20260816-208: P0 赛季前3轮 λ 自动上浮 25%
    // 新赛季阵容磨合不确定性导致实际进球数偏高，两场西甲第1轮 λ 偏差 +0.64~+1.77
    const round = options.round || 1;
    const seasonBoost = (round <= 3) ? 1.25 : 1.0;
    lambdaA *= seasonBoost;
    lambdaB *= seasonBoost;

    // C-20260816-208: P1 赔率时序信号作为 λ 调整因子
    // 赔率变化方向反映市场资金流向，编码为 λ 微调
    if (options.oddsTrend) {
      const ot = options.oddsTrend;
      if (ot.homeDown) { lambdaA *= 1.05; }      // 主胜赔率↓ → 市场看多主队
      if (ot.drawDown) { lambdaA *= 1.03; lambdaB *= 1.03; } // 平局赔率↓ → 双方进球预期↑
      if (ot.awayUp) { lambdaB *= 0.95; }         // 客胜赔率↑ → 市场看空客队
    }

    // C-20260816-208: P1 西甲联赛 λ 基线上调 10%
    // 西甲 25/26 赛季场均进球 2.67，当前 λ 基准偏保守
    const league = options.league || '';
    const leagueBoost = (league === 'LaLiga') ? 1.10 : 1.0;
    lambdaA *= leagueBoost;
    lambdaB *= leagueBoost;

    return {
      lambdaA: Math.max(0.3, Math.min(3.5, lambdaA)),
      lambdaB: Math.max(0.3, Math.min(3.5, lambdaB)),
      factors: { weather: weatherImpact, surface: surfaceImpact, homeAdv: homeAdv }
    };
  }

  calcWinDrawLosePoisson(lambdaA, lambdaB) {
    let winA = 0, draw = 0, winB = 0;
    // C-20260816-208: P1 比分分布拓宽, maxGoals 7→10 覆盖长尾冷门比分 (3:0/4:0)
    const maxGoals = 10;

    for (let i = 0; i <= maxGoals; i++) {
      for (let j = 0; j <= maxGoals; j++) {
        const p = this.poissonPMF(lambdaA, i) * this.poissonPMF(lambdaB, j);
        if (i > j) winA += p;
        else if (i === j) draw += p;
        else winB += p;
      }
    }

    const total = winA + draw + winB;
    return {
      winA: winA / total,
      draw: draw / total,
      winB: winB / total
    };
  }

  calcPoissonProbabilities(lambdaA, lambdaB) {
    const probabilities = {};
    // C-20260816-208: P1 比分分布拓宽, 上限 7→10 覆盖长尾冷门比分
    for (let a = 0; a <= 10; a++) {
      for (let b = 0; b <= 10; b++) {
        probabilities[`${a}-${b}`] = this.poissonPMF(lambdaA, a) * this.poissonPMF(lambdaB, b);
      }
    }
    return probabilities;
  }

  calcWinDrawLose(scoreProbabilities) {
    let win = 0, draw = 0, lose = 0;

    for (const [score, prob] of Object.entries(scoreProbabilities)) {
      const [a, b] = score.split('-').map(Number);
      if (a > b) win += prob;
      else if (a < b) lose += prob;
      else draw += prob;
    }

    return { win, draw, lose };
  }

  dixonColePMF(x, y, lambda1, lambda2, rho) {
    const p1 = this.poissonPMF(lambda1, x);
    const p2 = this.poissonPMF(lambda2, y);

    if (x === 0 && y === 0) {
      return p1 * p2 * (1 + rho);
    } else if (x === 0 && y === 1) {
      return p1 * p2 * (1 - rho);
    } else if (x === 1 && y === 0) {
      return p1 * p2 * (1 - rho);
    } else if (x === 1 && y === 1) {
      return p1 * p2 * (1 + rho);
    } else {
      return p1 * p2;
    }
  }

  calcHandicap(lambdaA, lambdaB) {
    let hWin = 0, hDraw = 0, hLose = 0;

    for (let a = 0; a <= 7; a++) {
      for (let b = 0; b <= 5; b++) {
        const prob = this.poissonPMF(lambdaA, a) * this.poissonPMF(lambdaB, b);
        const diff = a - b;

        if (diff >= 2) hWin += prob;
        else if (diff === 1) hDraw += prob;
        else hLose += prob;
      }
    }

    return { win: hWin, draw: hDraw, lose: hLose };
  }

  calcTotalGoals(lambdaA, lambdaB) {
    const tg = {};
    for (let g = 0; g <= 8; g++) {
      tg[g] = 0;
      for (let a = 0; a <= g; a++) {
        const b = g - a;
        tg[g] += this.poissonPMF(lambdaA, a) * this.poissonPMF(lambdaB, b);
      }
    }

    let over25 = 0, under25 = 0;
    for (let g = 0; g <= 8; g++) {
      if (g >= 3) over25 += tg[g];
      else under25 += tg[g];
    }

    return { distribution: tg, over25, under25 };
  }

  getTopScores(scoreProbabilities, limit) {
    const sorted = Object.entries(scoreProbabilities)
      .sort((a, b) => b[1] - a[1])
      .slice(0, limit);

    return sorted.map(([score, prob]) => ({ score, probability: prob }));
  }

  // ========== T-006 v4 核心算法 ==========

  /**
   * T-006 v4: Dixon-Coles 修正的 Poisson 比分概率
   * 移植自 t006_score_predictor_v4.py poisson_predict()
   *
   * @param {number} lambdaHome - 主队进球期望
   * @param {number} lambdaAway - 客队进球期望
   * @param {Object} options - 可选参数
   * @param {number} options.rho - 低比分 DC 修正参数 (默认 -0.30)
   * @param {number} options.rhoHigh - 高比分 DC 扩展参数 (默认 -0.10)
   * @param {number} options.highscoreMinGoals - 高比分阈值 (默认 5)
   * @param {number} options.maxGoals - 最大进球数 (默认 7)
   * @returns {Object} { scoreProbabilities, rawProbabilities, correctionStats }
   */
  predictScoreV4(lambdaHome, lambdaAway, options = {}) {
    const {
      rho = -0.30,
      rhoHigh = -0.10,
      highscoreMinGoals = 5,
      maxGoals = 7
    } = options;

    const size = maxGoals + 1;

    // 阶段1: 原始 Poisson PMF 矩阵
    const pmfHome = new Array(size);
    const pmfAway = new Array(size);
    for (let g = 0; g < size; g++) {
      pmfHome[g] = this.poissonPMF(lambdaHome, g);
      pmfAway[g] = this.poissonPMF(lambdaAway, g);
    }

    // 外积得到联合分布矩阵
    const rawMatrix = new Array(size);
    for (let h = 0; h < size; h++) {
      rawMatrix[h] = new Array(size);
      for (let a = 0; a < size; a++) {
        rawMatrix[h][a] = pmfHome[h] * pmfAway[a];
      }
    }

    // 阶段2: Dixon-Coles tau 修正矩阵
    const tauMatrix = new Array(size);
    for (let h = 0; h < size; h++) {
      tauMatrix[h] = new Array(size).fill(1.0);
    }

    // 低比分修正 (0-0, 0-1, 1-0, 1-1)
    tauMatrix[0][0] = 1.0 - lambdaHome * lambdaAway * rho;
    tauMatrix[0][1] = 1.0 + lambdaHome * rho;
    tauMatrix[1][0] = 1.0 + lambdaAway * rho;
    tauMatrix[1][1] = 1.0 - rho;

    // 高比分扩展 (总进球 >= highscoreMinGoals)
    if (rhoHigh !== 0) {
      const lambdaProd = lambdaHome * lambdaAway;
      let tauHighValue = 1.0 - rhoHigh * lambdaProd / 10.0;
      tauHighValue = Math.max(0.1, Math.min(3.0, tauHighValue));

      for (let h = 0; h < size; h++) {
        for (let a = 0; a < size; a++) {
          if (h + a >= highscoreMinGoals) {
            tauMatrix[h][a] = tauHighValue;
          }
        }
      }
    }

    // 裁剪 tau 矩阵
    for (let h = 0; h < size; h++) {
      for (let a = 0; a < size; a++) {
        tauMatrix[h][a] = Math.max(0.1, Math.min(3.0, tauMatrix[h][a]));
      }
    }

    // 应用修正
    const correctedMatrix = new Array(size);
    for (let h = 0; h < size; h++) {
      correctedMatrix[h] = new Array(size);
      for (let a = 0; a < size; a++) {
        correctedMatrix[h][a] = rawMatrix[h][a] * tauMatrix[h][a];
      }
    }

    // 归一化
    let totalRaw = 0, totalCorr = 0;
    for (let h = 0; h < size; h++) {
      for (let a = 0; a < size; a++) {
        totalRaw += rawMatrix[h][a];
        totalCorr += correctedMatrix[h][a];
      }
    }

    const scoreProbabilities = {};
    const rawProbabilities = {};
    for (let h = 0; h < size; h++) {
      for (let a = 0; a < size; a++) {
        const key = `${h}:${a}`;
        scoreProbabilities[key] = totalCorr > 0 ? correctedMatrix[h][a] / totalCorr : 0;
        rawProbabilities[key] = totalRaw > 0 ? rawMatrix[h][a] / totalRaw : 0;
      }
    }

    return { scoreProbabilities, rawProbabilities, correctionStats: { rho, rhoHigh } };
  }

  /**
   * T-006 v4: Monte Carlo 比分模拟
   * 移植自 t006_score_predictor_v4.py monte_carlo_simulate()
   *
   * @param {number} lambdaHome - 主队进球期望
   * @param {number} lambdaAway - 客队进球期望
   * @param {number} nSim - 模拟次数 (默认 500)
   * @param {number} maxGoals - 最大进球数 (默认 7)
   * @returns {Object} 比分概率字典 { "1:0": 0.12, "0:0": 0.08, ... }
   */
  monteCarloScoreSimulate(lambdaHome, lambdaAway, nSim = 500, maxGoals = 7) {
    const counts = {};
    for (let i = 0; i < nSim; i++) {
      const h = this.poissonRandom(lambdaHome, maxGoals);
      const a = this.poissonRandom(lambdaAway, maxGoals);
      const key = `${h}:${a}`;
      counts[key] = (counts[key] || 0) + 1;
    }

    const result = {};
    for (const key in counts) {
      result[key] = counts[key] / nSim;
    }
    return result;
  }

  /**
   * Poisson 随机数生成 (Knuth 算法)
   */
  poissonRandom(lambda, maxVal = 7) {
    const L = Math.exp(-lambda);
    let k = 0, p = 1;
    do {
      k++;
      p *= Math.random();
    } while (p > L && k < maxVal + 1);
    return k - 1;
  }

  /**
   * T-006 v4: 融合 Poisson + Monte Carlo + 比分赔率
   * 移植自 t006_score_predictor_v4.py fuse_predictions()
   *
   * @param {Object} poissonProbs - Poisson+DC 比分概率
   * @param {Object} mcProbs - Monte Carlo 比分概率
   * @param {Object} scoreOdds - 比分赔率字典 { "1:0": 8.5, ... }
   * @returns {Object} 融合后的比分概率
   */
  fuseScorePredictions(poissonProbs, mcProbs, scoreOdds = null) {
    const POISSON_WEIGHT = 0.85;
    const MC_WEIGHT = 0.15;
    const HIGHSCORE_ALPHA = 0.30;

    // 1. Poisson + MC 加权融合
    const allKeys = new Set([...Object.keys(poissonProbs), ...Object.keys(mcProbs)]);
    const fused = {};
    for (const key of allKeys) {
      const p = poissonProbs[key] || 0;
      const m = mcProbs[key] || 0;
      fused[key] = p * POISSON_WEIGHT + m * MC_WEIGHT;
    }

    // 2. 融合比分赔率（如果提供）
    if (scoreOdds && Object.keys(scoreOdds).length > 0) {
      const oddsProbs = {};
      let totalOddsProb = 0;
      for (const [score, odds] of Object.entries(scoreOdds)) {
        const p = 1.0 / odds;
        oddsProbs[score] = p;
        totalOddsProb += p;
      }
      // 归一化赔率概率
      if (totalOddsProb > 0) {
        for (const score in oddsProbs) {
          oddsProbs[score] /= totalOddsProb;
        }
      }

      // 加权融合：模型 (1-alpha) + 赔率 (alpha)
      for (const score in fused) {
        const oddsP = oddsProbs[score] || 0;
        fused[score] = fused[score] * (1 - HIGHSCORE_ALPHA) + oddsP * HIGHSCORE_ALPHA;
      }
    }

    // 3. 归一化
    let total = 0;
    for (const score in fused) total += fused[score];
    if (total > 0) {
      for (const score in fused) fused[score] /= total;
    }

    return fused;
  }

  /**
   * C-20260819-005: T-006 联赛代码 → one-hot 标志
   * 兼容三种格式:服务端代码(FL1/PL/BL1/LaLiga/IT)、中文(法甲/英超等)、英文(Ligue 1/Premier League 等)
   * 严格对齐 t006_lowgoal_classifier.py L205-L209 的训练时联赛编码
   * @param {string} league - 联赛代码或名称
   * @returns {Object} {is_ligue1, is_serie_a, is_la_liga, is_premier_league, is_bundesliga} (0/1 数值)
   */
  _resolveLeagueFlags(league) {
    const l = (league || '').toString().toLowerCase();
    return {
      is_ligue1:         l === 'fl1'    || l.includes('法甲')    || l.includes('ligue 1')    || l.includes('ligue1') ? 1 : 0,
      is_serie_a:        l === 'it'     || l.includes('意甲')    || l.includes('serie a')     || l.includes('seriea')  ? 1 : 0,
      is_la_liga:        l === 'laliga' || l.includes('西甲')    || l.includes('la liga')     || l.includes('laliga')  ? 1 : 0,
      is_premier_league: l === 'pl'     || l.includes('英超')    || l.includes('premier league')                       ? 1 : 0,
      is_bundesliga:     l === 'bl1'    || l.includes('德甲')    || l.includes('bundesliga')                            ? 1 : 0,
    };
  }

  /**
   * C-20260819-005: T-006 从 matchData 构建 22 维低进球分类器特征
   * 严格对齐 scripts/t006_lowgoal_classifier.py build_features() L195-L257
   *
   * @param {Object} matchData - { scoreOdds, odds:{wdl:{close:{win,draw,lose}}}, leagueCode }
   * @param {Object} [wdlFallback] - { win, draw, lose } 当 matchData.odds 缺失时的回退概率(来自 stacked 预测)
   * @returns {Object|null} 22 维特征对象,key 与训练时 FEATURE_COLS 一致;关键数据缺失返回 null
   */
  buildT006Features(matchData, wdlFallback = null) {
    const LEAGUE_AVG_GOALS = 2.5;
    const HOME_ADVANTAGE = 1.10;

    // === 1. 提取 WDL 赔率 ===
    const odds = matchData?.odds || {};
    const wdlOdds = odds?.wdl?.close || odds.wdl?.open || null;
    let winA, drawOdds, winB;
    if (wdlOdds && wdlOdds.win > 0 && wdlOdds.draw > 0 && wdlOdds.lose > 0) {
      winA = wdlOdds.win; drawOdds = wdlOdds.draw; winB = wdlOdds.lose;
    } else if (wdlFallback && wdlFallback.win > 0 && wdlFallback.draw > 0 && wdlFallback.lose > 0) {
      // 回退:用 stacked 预测概率反推虚拟赔率(odds = totalImplied / prob,保持隐含概率一致)
      const ti = 1 / wdlFallback.win + 1 / wdlFallback.draw + 1 / wdlFallback.lose;
      winA = ti / wdlFallback.win;
      drawOdds = ti / wdlFallback.draw;
      winB = ti / wdlFallback.lose;
    } else {
      return null;  // 无法构建特征
    }

    // === 2. 基础派生特征(对齐训练脚本 L148-L162) ===
    const totalImplied = 1 / winA + 1 / drawOdds + 1 / winB;
    const probHome = (1 / winA) / totalImplied;
    const probDraw = (1 / drawOdds) / totalImplied;
    const probAway = (1 / winB) / totalImplied;
    const margin = (totalImplied - 1) * 100;

    // lambda 计算(对齐训练脚本 L153-L162)
    let lh = LEAGUE_AVG_GOALS * probHome * HOME_ADVANTAGE;
    let la = LEAGUE_AVG_GOALS * probAway;
    const tl = lh + la;
    if (tl > 0) { lh = lh / tl * LEAGUE_AVG_GOALS; la = la / tl * LEAGUE_AVG_GOALS; }
    const lambdaAsymmetry = (lh + la) > 0 ? Math.abs(lh - la) / (lh + la) : 0;

    // === 3. 比分赔率派生特征 ===
    const scoreOdds = matchData?.scoreOdds || {};
    const hasScoreOdds = scoreOdds && Object.keys(scoreOdds).length > 0;

    // score_implied_total(对齐训练脚本 L173-L181)
    let scoreImpliedTotal = LEAGUE_AVG_GOALS;  // 缺失回退
    if (hasScoreOdds) {
      let implTotal = 0, totalProb = 0;
      for (const [score, o] of Object.entries(scoreOdds)) {
        const parts = score.split(':');
        const h = parseInt(parts[0], 10);
        const a = parseInt(parts[1], 10);
        if (!isNaN(h) && !isNaN(a) && o > 0) {
          const p = 1 / o;
          implTotal += (h + a) * p;
          totalProb += p;
        }
      }
      if (totalProb > 0 && implTotal > 0) scoreImpliedTotal = implTotal / totalProb;
    }

    // avg_draw_score_odds(对齐训练脚本 L182-L183,缺失回退 draw*5)
    const drawScoreKeys = ['0:0', '1:1', '2:2', '3:3'];
    const drawOddsList = drawScoreKeys.map(k => scoreOdds[k]).filter(v => v != null && v > 0);
    const avgDrawScoreOdds = drawOddsList.length > 0
      ? drawOddsList.reduce((s, v) => s + v, 0) / drawOddsList.length
      : drawOdds * 5;

    // 低比分赔率(对齐训练脚本 L184-L187,缺失回退 50.0)
    const odds00 = scoreOdds['0:0'] ?? 50.0;
    const odds11 = scoreOdds['1:1'] ?? 50.0;
    const odds10 = scoreOdds['1:0'] ?? 50.0;
    const odds01 = scoreOdds['0:1'] ?? 50.0;

    // === 4. 联赛 one-hot ===
    const leagueCode = matchData?.leagueCode || matchData?.league || '';
    const flags = this._resolveLeagueFlags(leagueCode);

    // === 5. 二阶交互特征(对齐训练脚本 L212-L227) ===
    const drawOverImplied = scoreImpliedTotal !== 0
      ? drawOdds / scoreImpliedTotal
      : drawOdds / LEAGUE_AVG_GOALS;  // 缺失回退
    const probDrawXImplied = probDraw * scoreImpliedTotal;
    const probNondrawXAsym = (1 - probDraw) * lambdaAsymmetry;
    const oddsDrawRatio = (1 / drawOdds) / totalImplied;
    const lowScoreOddsSum = odds00 + odds11 + odds10 + odds01;
    const lowScoreProbSum = 1 / odds00 + 1 / odds11 + 1 / odds10 + 1 / odds01;
    const drawMinusAsym = drawOdds - lambdaAsymmetry * 10;

    // === 6. 组装 22 维特征对象(key 严格匹配训练 FEATURE_COLS) ===
    return {
      score_implied_total: scoreImpliedTotal,
      odds_00: odds00,
      odds_11: odds11,
      odds_10: odds10,
      odds_01: odds01,
      prob_draw: probDraw,
      draw: drawOdds,
      avg_draw_score_odds: avgDrawScoreOdds,
      margin: margin,
      lambda_asymmetry: lambdaAsymmetry,
      is_ligue1: flags.is_ligue1,
      is_serie_a: flags.is_serie_a,
      is_la_liga: flags.is_la_liga,
      is_premier_league: flags.is_premier_league,
      is_bundesliga: flags.is_bundesliga,
      draw_over_implied: drawOverImplied,
      prob_draw_x_implied: probDrawXImplied,
      prob_nondraw_x_asym: probNondrawXAsym,
      odds_draw_ratio: oddsDrawRatio,
      low_score_odds_sum: lowScoreOddsSum,
      low_score_prob_sum: lowScoreProbSum,
      draw_minus_asym: drawMinusAsym
    };
  }

  /**
   * C-20260819-005: T-006 二分类 LightGBM 推理(低进球概率)
   * 与 treeModelPredict 不同:不做 3 类 softmax,直接 sigmoid 输出
   * base 由导出脚本用零特征反推法计算,确保 JS sigmoid(base+lr*Σleaves) 与 Python predict_proba 严格一致
   *
   * @param {Object} features - buildT006Features 输出的 22 维特征
   * @returns {{probability, rawScore, isLowgoal, threshold, baseRate, skipped, reason?}}
   */
  predictT006Lowgoal(features) {
    if (!this.t006LowgoalModel || !this.t006LowgoalModel.trees || this.t006LowgoalModel.trees.length === 0) {
      return { probability: 0, rawScore: 0, isLowgoal: false, threshold: 0.5, baseRate: 0.22, skipped: true, reason: 'model_not_loaded' };
    }
    if (!features) {
      return {
        probability: 0, rawScore: 0, isLowgoal: false,
        threshold: this.t006LowgoalModel.best_threshold ?? 0.5,
        baseRate: this.t006LowgoalModel.base_rate ?? 0.22,
        skipped: true, reason: 'features_null'
      };
    }

    let rawScore = this.t006LowgoalModel.base || 0;
    const lr = this.t006LowgoalModel.lr || 0.05;
    for (const tree of this.t006LowgoalModel.trees) {
      rawScore += lr * this.predictTree(tree, features);  // 复用既有 predictTree 命名分支
    }
    // clamp 防数值溢出(Math.exp(50)≈5e21 仍可表达,但更大会溢出为 Infinity)
    rawScore = Math.max(-50, Math.min(50, rawScore));
    const probability = 1 / (1 + Math.exp(-rawScore));
    const threshold = this.t006LowgoalModel.best_threshold ?? 0.5;
    const baseRate = this.t006LowgoalModel.base_rate ?? 0.22;

    return {
      probability,
      rawScore,
      isLowgoal: probability >= threshold,
      threshold,
      baseRate,
      skipped: false
    };
  }

  /**
   * C-20260819-005: T-006 应用低比分权重调整
   * 严格匹配 scripts/t006_integration_compare.py adjust_lowgoal_weights_by_prior L280-L300
   * 公式: factor = 1 + alpha * (p_low - base_rate) / base_rate, clip [0.5, 2.0]
   * 仅对 ['0:0', '0:1', '1:0', '1:1'] 应用,全分布重新归一化
   *
   * @param {Object} fusedScores - fuseScorePredictions 输出的比分概率字典
   * @param {Object} t006Result - predictT006Lowgoal 返回值
   * @param {number} [alpha=0.5] - 集成系数(与 Python 默认一致)
   * @returns {Object} 调整后的比分概率字典
   */
  applyT006LowgoalAdjustment(fusedScores, t006Result, alpha = 0.5) {
    if (!fusedScores || typeof fusedScores !== 'object') return fusedScores;
    // 未加载模型 / 特征缺失 / 未达阈值 → 不调整(返回原分布)
    if (!t006Result || t006Result.skipped || !t006Result.isLowgoal) return fusedScores;

    const pLow = t006Result.probability;
    const baseRate = t006Result.baseRate || 0.22;
    if (baseRate <= 0) return fusedScores;

    // factor 公式严格对齐 Python L290
    let factor = 1.0 + alpha * (pLow - baseRate) / baseRate;
    factor = Math.max(0.5, Math.min(2.0, factor));  // clip [0.5, 2.0]
    // 防御:factor 接近 1 时跳过(避免无意义的浮点抖动)
    if (Math.abs(factor - 1.0) < 0.001) return fusedScores;

    const lowScores = ['0:0', '0:1', '1:0', '1:1'];
    const adjusted = { ...fusedScores };
    for (const s of lowScores) {
      if (adjusted[s] !== undefined) {
        adjusted[s] = adjusted[s] * factor;
      }
    }
    // 重新归一化(其他比分不动,但归一化使它们略缩)
    let total = 0;
    for (const k in adjusted) total += adjusted[k];
    if (total > 0) {
      for (const k in adjusted) adjusted[k] /= total;
    }
    return adjusted;
  }

  /**
   * 平局概率校准
   * 将模型预测的平局概率调整到目标值（默认 26%，接近实际足球比赛平局率）
   *
   * @param {Object} wdl - { win, draw, lose }
   * @param {number} targetDrawRate - 目标平局率 (默认 0.26)
   * @returns {Object} 校准后的 { win, draw, lose }
   */
  calibrateDrawProbability(wdl, targetDrawRate = 0.26) {
    const { win, draw, lose } = wdl;
    const currentDraw = draw;
    const diff = targetDrawRate - currentDraw;

    if (Math.abs(diff) < 0.001) return wdl;

    // 按比例从 win 和 lose 中调整
    const winLoseSum = win + lose;
    if (winLoseSum <= 0) return wdl;

    const newDraw = targetDrawRate;
    const remaining = 1 - newDraw;
    const newWin = win / winLoseSum * remaining;
    const newLose = lose / winLoseSum * remaining;

    return {
      win: newWin,
      draw: newDraw,
      lose: newLose
    };
  }

  calcImpliedProbability(odds) {
    const { win, draw, lose } = odds;
    const total = 1/win + 1/draw + 1/lose;

    return {
      win: (1/win) / total,
      draw: (1/draw) / total,
      lose: (1/lose) / total
    };
  }

  calcValueAnalysis(modelProb, impliedProb) {
    return {
      win: {
        modelProb: modelProb.win,
        impliedProb: impliedProb.win,
        value: modelProb.win - impliedProb.win,
        recommendation: modelProb.win > impliedProb.win ? '有价值' : '无价值'
      },
      draw: {
        modelProb: modelProb.draw,
        impliedProb: impliedProb.draw,
        value: modelProb.draw - impliedProb.draw,
        recommendation: modelProb.draw > impliedProb.draw ? '有价值' : '无价值'
      },
      lose: {
        modelProb: modelProb.lose,
        impliedProb: impliedProb.lose,
        value: modelProb.lose - impliedProb.lose,
        recommendation: modelProb.lose > impliedProb.lose ? '有价值' : '无价值'
      }
    };
  }

  predictMatch(teamA, teamB, options) {
    const lambdas = this.calcLambdaMatch(teamA, teamB, options);
    const { winA, draw, winB } = this.calcWinDrawLosePoisson(lambdas.lambdaA, lambdas.lambdaB);
    return { winA, draw, winB, model: 'Poisson', factors: lambdas.factors };
  }

  predictMatchDC(teamA, teamB, options) {
    const lambdas = this.calcLambdaMatch(teamA, teamB, options);
    const rhoDC = options.rhoDC || 0.02;

    let winA = 0, draw = 0, winB = 0;
    const maxGoals = 7;

    for (let i = 0; i <= maxGoals; i++) {
      for (let j = 0; j <= maxGoals; j++) {
        const p = this.dixonColePMF(i, j, lambdas.lambdaA, lambdas.lambdaB, rhoDC);
        if (i > j) winA += p;
        else if (i === j) draw += p;
        else winB += p;
      }
    }

    const total = winA + draw + winB;
    return {
      winA: winA / total,
      draw: draw / total,
      winB: winB / total,
      model: 'Dixon-Cole'
    };
  }

  predictMatchSSM(teamA, teamB, options) {
    const lambdas = this.calcLambdaMatch(teamA, teamB, options);

    const attackA = teamA.attack || 0.8;
    const defenceA = teamA.defence || 0.7;
    const attackB = teamB.attack || 0.8;
    const defenceB = teamB.defence || 0.7;

    // SSM 修正: defence 方向已对齐 (强防守 → 低进球期望)
    const ssmLambdaA = lambdas.lambdaA * (attackA * 0.5 + 0.5) * ((1 - defenceB) * 0.5 + 0.5);
    const ssmLambdaB = lambdas.lambdaB * (attackB * 0.5 + 0.5) * ((1 - defenceA) * 0.5 + 0.5);

    const { winA, draw, winB } = this.calcWinDrawLosePoisson(ssmLambdaA, ssmLambdaB);
    return { winA, draw, winB, model: 'Bayesian-SSM' };
  }

  predictElo(teamAKey, teamBKey, options) {
    const eloA = this.eloRatings[teamAKey] || 1800;
    const eloB = this.eloRatings[teamBKey] || 1800;

    const isNeutral = options.neutral || false;
    const homeAdv = isNeutral ? 0 : 65;

    const eloDiff = (eloA + homeAdv) - eloB;
    const eloProbA = 1 / (1 + Math.pow(10, -eloDiff / 400));
    const eloProbB = 1 / (1 + Math.pow(10, eloDiff / 400));

    const eloDraw = 0.26 * Math.exp(-Math.abs(eloDiff) / 600);
    const eloNonDraw = 1 - eloDraw;

    const winA = eloProbA * eloNonDraw / Math.max(0.01, eloProbA + eloProbB);
    const winB = eloProbB * eloNonDraw / Math.max(0.01, eloProbA + eloProbB);
    const draw = eloDraw;

    return { winA, draw, winB, model: 'Elo' };
  }

  applyPlattCalibration(probs, calibrationParams) {
    if (!calibrationParams || !Array.isArray(calibrationParams) || calibrationParams.length !== 3) {
      return probs;
    }

    const calibrated = [];
    for (let i = 0; i < 3; i++) {
      const a = calibrationParams[i].a || 1.0;
      const b = calibrationParams[i].b || 0.0;
      const logit = a * probs[i] + b;
      calibrated.push(1 / (1 + Math.exp(-logit)));
    }

    const total = calibrated.reduce((sum, p) => sum + p, 0);
    if (total > 0) {
      return calibrated.map(p => p / total);
    }
    return probs;
  }

  buildFeatures(teamA, teamB, options, matchContext) {
    const isNeutral = options.neutral || false;
    matchContext = matchContext || {};

    const homeXg = teamA.xg ?? teamA.xGOT ?? teamA.attack ?? 1.0;
    const awayXg = teamB.xg ?? teamB.xGOT ?? teamB.attack ?? 1.0;
    const homeXgot = teamA.xGOT ?? homeXg * 0.8;
    const awayXgot = teamB.xGOT ?? awayXg * 0.8;
    const homeBigChances = teamA.bigChances ?? 0;
    const awayBigChances = teamB.bigChances ?? 0;
    const homeXa = teamA.xa ?? teamA.xGA ?? 0;
    const awayXa = teamB.xa ?? teamB.xGA ?? 0;
    const homeShots = teamA.shots ?? 0;
    const awayShots = teamB.shots ?? 0;
    const homeShotsOnTarget = teamA.shotsOnTarget ?? 0;
    const awayShotsOnTarget = teamB.shotsOnTarget ?? 0;
    const homeSaves = teamA.saves ?? 0;
    const awaySaves = teamB.saves ?? 0;
    const homePossession = teamA.possession ?? 50;
    const awayPossession = 100 - homePossession;
    const homeCorners = teamA.corners ?? 0;
    const awayCorners = teamB.corners ?? 0;
    // P0-D: 联赛统一到训练端中文名（英超/德甲/意甲/法甲/西甲）
    const league = LEAGUE_NAME_MAP[teamA.league ?? teamA.leagueName ?? ''] || LEAGUE_NAME_MAP[teamB.league ?? ''] || '其他';

    const features = {
      home_xg: homeXg,
      away_xg: awayXg,
      home_xgot: homeXgot,
      away_xgot: awayXgot,
      xg_diff: homeXg - awayXg,
      xgot_diff: homeXgot - awayXgot,
      xg_ratio: homeXg / (awayXg + 0.01),
      xgot_ratio: homeXgot / (awayXgot + 0.01),
      home_big_chances: homeBigChances,
      away_big_chances: awayBigChances,
      big_chances_diff: homeBigChances - awayBigChances,
      home_xa: homeXa,
      away_xa: awayXa,
      xa_diff: homeXa - awayXa,
      home_shots: homeShots,
      away_shots: awayShots,
      home_shots_on_target: homeShotsOnTarget,
      away_shots_on_target: awayShotsOnTarget,
      home_saves: homeSaves,
      away_saves: awaySaves,
      save_diff: homeSaves - awaySaves,
      save_rate_home: homeSaves / (awayShotsOnTarget + 1),
      save_rate_away: awaySaves / (homeShotsOnTarget + 1),
      home_possession: homePossession,
      away_possession: awayPossession,
      possession_diff: homePossession - awayPossession,
      home_corners: homeCorners,
      away_corners: awayCorners,
      corner_diff: homeCorners - awayCorners,
      shots_diff: homeShots - awayShots,
      shots_on_target_diff: homeShotsOnTarget - awayShotsOnTarget,
      home_attack_rate: homeShotsOnTarget / (homeShots + 1),
      away_attack_rate: awayShotsOnTarget / (awayShots + 1),
      attack_rate_diff: (homeShotsOnTarget / (homeShots + 1)) - (awayShotsOnTarget / (awayShots + 1)),
      home_xg_per_shot: homeXg / (homeShots + 1),
      away_xg_per_shot: awayXg / (awayShots + 1),
      xg_per_shot_diff: (homeXg / (homeShots + 1)) - (awayXg / (awayShots + 1)),
      total_xg: homeXg + awayXg,
      total_xgot: homeXgot + awayXgot,
      total_shots: homeShots + awayShots,
      total_shots_on_target: homeShotsOnTarget + awayShotsOnTarget,
      league_英超: league === '英超' ? 1 : 0,
      league_德甲: league === '德甲' ? 1 : 0,
      league_意甲: league === '意甲' ? 1 : 0,
      league_法甲: league === '法甲' ? 1 : 0,
      league_西甲: league === '西甲' ? 1 : 0
    };

    const homeAvgGoals = teamA.avgGoals ?? teamA.attack ?? 1.0;
    const homeAvgOppGoals = teamA.avgOppGoals ?? teamA.defence ?? 1.0;
    const awayAvgGoals = teamB.avgGoals ?? teamB.attack ?? 1.0;
    const awayAvgOppGoals = teamB.avgOppGoals ?? teamB.defence ?? 1.0;
    const homeWinRate = teamA.winRate ?? 0.33;
    const awayWinRate = teamB.winRate ?? 0.33;

    // B-006: 扩展特征 - 从 teamData 补全训练端有的球队特征
    const homeGamesPlayed = teamA.gamesPlayed ?? teamA.matches ?? 0;
    const awayGamesPlayed = teamB.gamesPlayed ?? teamB.matches ?? 0;
    const homeFormTrend = teamA.formTrend ?? 0;
    const awayFormTrend = teamB.formTrend ?? 0;
    const homeConsUndefeated = teamA.consecutiveUndefeated ?? 0;
    const awayConsUndefeated = teamB.consecutiveUndefeated ?? 0;
    const homeHomeGoals = teamA.homeGoals ?? teamA.homeGoalsScored ?? (homeAvgGoals * 1.1);
    const awayHomeGoals = teamB.homeGoals ?? teamB.homeGoalsScored ?? (awayAvgGoals * 1.1);
    const homeAwayGoals = teamA.awayGoals ?? teamA.awayGoalsScored ?? (homeAvgGoals * 0.9);
    const awayAwayGoals = teamB.awayGoals ?? teamB.awayGoalsScored ?? (awayAvgGoals * 0.9);
    const homeHomeAdv = (teamA.homeWinRate ?? homeWinRate) - (teamA.awayWinRate ?? 0.33);
    const awayHomeAdv = (teamB.homeWinRate ?? 0.33) - (teamB.awayWinRate ?? awayWinRate);
    const homeGoalsStability = teamA.goalsStability ?? (teamA.goalsStd !== undefined ? 1.0 / (teamA.goalsStd + 0.1) : 0);
    const awayGoalsStability = teamB.goalsStability ?? (teamB.goalsStd !== undefined ? 1.0 / (teamB.goalsStd + 0.1) : 0);
    const homeWeightedAvgGoals = teamA.weightedAvgGoals ?? homeAvgGoals;
    const awayWeightedAvgGoals = teamB.weightedAvgGoals ?? awayAvgGoals;

    const teamFeatures = {
      home_avg_goals: homeAvgGoals,
      home_avg_opp_goals: homeAvgOppGoals,
      home_win_rate: homeWinRate,
      home_draw_rate: teamA.drawRate ?? 0.34,
      home_loss_rate: teamA.lossRate ?? 0.33,
      home_goals_std: teamA.goalsStd ?? 0,
      home_recent_form: teamA.recentForm ?? 0,
      home_form_trend: homeFormTrend,
      home_consecutive_wins: teamA.consecutiveWins ?? 0,
      home_consecutive_losses: teamA.consecutiveLosses ?? 0,
      home_consecutive_undefeated: homeConsUndefeated,
      home_games_played: homeGamesPlayed,
      home_weighted_win_rate: teamA.weightedWinRate ?? homeWinRate,
      home_weighted_avg_goals: homeWeightedAvgGoals,
      home_home_win_rate: teamA.homeWinRate ?? homeWinRate,
      home_away_win_rate: teamA.awayWinRate ?? 0.33,
      home_home_goals: homeHomeGoals,
      home_away_goals: homeAwayGoals,
      home_home_advantage: homeHomeAdv,
      away_avg_goals: awayAvgGoals,
      away_avg_opp_goals: awayAvgOppGoals,
      away_win_rate: awayWinRate,
      away_draw_rate: teamB.drawRate ?? 0.34,
      away_loss_rate: teamB.lossRate ?? 0.33,
      away_goals_std: teamB.goalsStd ?? 0,
      away_recent_form: teamB.recentForm ?? 0,
      away_form_trend: awayFormTrend,
      away_consecutive_wins: teamB.consecutiveWins ?? 0,
      away_consecutive_losses: teamB.consecutiveLosses ?? 0,
      away_consecutive_undefeated: awayConsUndefeated,
      away_games_played: awayGamesPlayed,
      away_weighted_win_rate: teamB.weightedWinRate ?? awayWinRate,
      away_weighted_avg_goals: awayWeightedAvgGoals,
      away_home_win_rate: teamB.homeWinRate ?? 0.33,
      away_away_win_rate: teamB.awayWinRate ?? awayWinRate,
      away_home_goals: awayHomeGoals,
      away_away_goals: awayAwayGoals,
      away_home_advantage: awayHomeAdv,
      form_diff: homeWinRate - awayWinRate,
      goals_diff: homeAvgGoals - awayAvgGoals,
      defence_diff: awayAvgOppGoals - homeAvgOppGoals,
      recent_form_diff: (teamA.recentForm ?? 0) - (teamB.recentForm ?? 0),
      form_trend_diff: homeFormTrend - awayFormTrend,
      streak_diff: ((teamA.consecutiveWins ?? 0) - (teamA.consecutiveLosses ?? 0)) -
                   ((teamB.consecutiveWins ?? 0) - (teamB.consecutiveLosses ?? 0)),
      undefeated_diff: homeConsUndefeated - awayConsUndefeated,
      weighted_form_diff: (teamA.weightedWinRate ?? homeWinRate) - (teamB.weightedWinRate ?? awayWinRate),
      weighted_goals_diff: homeWeightedAvgGoals - awayWeightedAvgGoals,
      venue_diff: ((teamA.homeWinRate ?? homeWinRate) - (teamA.awayWinRate ?? 0.33)) -
                  ((teamB.homeWinRate ?? 0.33) - (teamB.awayWinRate ?? awayWinRate)),
      goals_stability_diff: homeGoalsStability - awayGoalsStability,
      opponent_strength_diff: (matchContext.homeOpponentWinRate ?? 0.33) - (matchContext.awayOpponentWinRate ?? 0.33),
      home_opponent_avg_win_rate: matchContext.homeOpponentWinRate ?? 0.33,
      away_opponent_avg_win_rate: matchContext.awayOpponentWinRate ?? 0.33
    };

    // B-006: Elo 特征
    if (matchContext.elo) {
      const e = matchContext.elo;
      Object.assign(features, {
        home_elo: e.homeElo ?? 1500,
        away_elo: e.awayElo ?? 1500,
        elo_diff: (e.homeElo ?? 1500) - (e.awayElo ?? 1500),
        elo_ratio: (e.homeElo ?? 1500) / ((e.awayElo ?? 1500) + 1),
        elo_home_expected: e.homeExpected ?? 0.5,
        elo_away_expected: e.awayExpected ?? 0.5,
        elo_draw_prob: e.drawProb ?? 0.25,
        home_elo_momentum: e.homeMomentum ?? 0,
        away_elo_momentum: e.awayMomentum ?? 0,
        elo_confidence: e.confidence ?? 0.5
      });
    }

    // B-006: H2H 特征
    if (matchContext.h2h) {
      const h = matchContext.h2h;
      Object.assign(features, {
        h2h_matches: h.matches ?? 0,
        h2h_home_win_rate: h.homeWinRate ?? 0,
        h2h_away_win_rate: h.awayWinRate ?? 0,
        h2h_draw_rate: h.drawRate ?? 0,
        h2h_avg_goals_home: h.avgGoalsHome ?? 0,
        h2h_avg_goals_away: h.avgGoalsAway ?? 0,
        h2h_avg_total_goals: h.avgTotalGoals ?? 0,
        h2h_goal_diff_avg: h.goalDiffAvg ?? 0,
        h2h_last_result: h.lastResult ?? 0,
        h2h_home_streak: h.homeStreak ?? 0,
        h2h_away_streak: h.awayStreak ?? 0
      });
    }

    // B-006: 赔率特征 (WDL / HCP / TG)
    if (matchContext.odds) {
      const o = matchContext.odds;
      // WDL 赔率特征 (16维)
      Object.assign(features, {
        wdl_open_win: o.wdlOpenWin ?? 0,
        wdl_open_draw: o.wdlOpenDraw ?? 0,
        wdl_open_lose: o.wdlOpenLose ?? 0,
        wdl_close_win: o.wdlCloseWin ?? o.wdlOpenWin ?? 0,
        wdl_close_draw: o.wdlCloseDraw ?? o.wdlOpenDraw ?? 0,
        wdl_close_lose: o.wdlCloseLose ?? o.wdlOpenLose ?? 0,
        wdl_win_trend: o.wdlWinTrend ?? 0,
        wdl_draw_trend: o.wdlDrawTrend ?? 0,
        wdl_lose_trend: o.wdlLoseTrend ?? 0,
        wdl_implied_win: o.wdlImpliedWin ?? 0,
        wdl_implied_draw: o.wdlImpliedDraw ?? 0,
        wdl_implied_lose: o.wdlImpliedLose ?? 0,
        wdl_overround: o.wdlOverround ?? 0,
        wdl_favorite: o.wdlFavorite ?? 0,
        wdl_favorite_prob: o.wdlFavoriteProb ?? 0,
        wdl_record_count: o.wdlRecordCount ?? 0,
        // WDL 凯利指数 (3维)
        wdl_kelly_win: o.wdlKellyWin ?? 0,
        wdl_kelly_draw: o.wdlKellyDraw ?? 0,
        wdl_kelly_lose: o.wdlKellyLose ?? 0,
        // WDL 变化率 (3维)
        wdl_win_change_rate: o.wdlWinChangeRate ?? 0,
        wdl_draw_change_rate: o.wdlDrawChangeRate ?? 0,
        wdl_lose_change_rate: o.wdlLoseChangeRate ?? 0
      });
      // HCP 赔率特征 (13维)
      Object.assign(features, {
        hcp_open_win: o.hcpOpenWin ?? 0,
        hcp_open_draw: o.hcpOpenDraw ?? 0,
        hcp_open_lose: o.hcpOpenLose ?? 0,
        hcp_close_win: o.hcpCloseWin ?? o.hcpOpenWin ?? 0,
        hcp_close_draw: o.hcpCloseDraw ?? o.hcpOpenDraw ?? 0,
        hcp_close_lose: o.hcpCloseLose ?? o.hcpOpenLose ?? 0,
        hcp_win_trend: o.hcpWinTrend ?? 0,
        hcp_draw_trend: o.hcpDrawTrend ?? 0,
        hcp_lose_trend: o.hcpLoseTrend ?? 0,
        hcp_implied_win: o.hcpImpliedWin ?? 0,
        hcp_implied_draw: o.hcpImpliedDraw ?? 0,
        hcp_implied_lose: o.hcpImpliedLose ?? 0,
        hcp_record_count: o.hcpRecordCount ?? 0,
        // HCP 凯利指数 (3维)
        hcp_kelly_win: o.hcpKellyWin ?? 0,
        hcp_kelly_draw: o.hcpKellyDraw ?? 0,
        hcp_kelly_lose: o.hcpKellyLose ?? 0,
        // HCP 变化率 (3维)
        hcp_win_change_rate: o.hcpWinChangeRate ?? 0,
        hcp_draw_change_rate: o.hcpDrawChangeRate ?? 0,
        hcp_lose_change_rate: o.hcpLoseChangeRate ?? 0
      });
      // TG 总进球特征 (6维)
      const tgOver25 = o.tgOver25 ?? 0;
      const tgUnder25 = o.tgUnder25 ?? 0;
      Object.assign(features, {
        tg_over_25_prob: tgOver25,
        tg_under_25_prob: tgUnder25,
        tg_most_likely: o.tgMostLikely ?? 0,
        tg_most_likely_prob: o.tgMostLikelyProb ?? 0,
        tg_expected: o.tgExpected ?? 2.5,
        tg_record_count: o.tgRecordCount ?? 0
      });
      // 赔率覆盖率 (4维)
      Object.assign(features, {
        has_wdl_odds: o.hasWdlOdds ? 1 : 0,
        has_hcp_odds: o.hasHcpOdds ? 1 : 0,
        has_tg_odds: o.hasTgOdds ? 1 : 0,
        odds_coverage: (o.hasWdlOdds ? 1 : 0) + (o.hasHcpOdds ? 1 : 0) + (o.hasTgOdds ? 1 : 0)
      });
      // 市场信心度 (5维)
      const wdlImp = [features.wdl_implied_win || 0.33, features.wdl_implied_draw || 0.34, features.wdl_implied_lose || 0.33];
      const wdlEntropy = -wdlImp.reduce((s, p) => s + (p > 0 ? p * Math.log(p) : 0), 0);
      Object.assign(features, {
        odds_confidence: Math.max(...wdlImp),
        odds_entropy: wdlEntropy,
        bookmaker_margin: o.wdlOverround ?? 0,
        value_bet_home: o.valueBetHome ?? 0,
        value_bet_away: o.valueBetAway ?? 0
      });
    }

    // B-006: 时序赔率波动特征 (10维)
    if (matchContext.oddsTiming) {
      const ot = matchContext.oddsTiming;
      Object.assign(features, {
        wdl_win_volatility: ot.winVolatility ?? 0,
        wdl_draw_volatility: ot.drawVolatility ?? 0,
        wdl_lose_volatility: ot.loseVolatility ?? 0,
        wdl_win_acceleration: ot.winAcceleration ?? 0,
        wdl_late_trend: ot.lateTrend ?? 0,
        wdl_early_trend: ot.earlyTrend ?? 0,
        wdl_mid_stability: ot.midStability ?? 0,
        wdl_sudden_jump: ot.suddenJump ?? 0,
        wdl_update_frequency: ot.updateFrequency ?? 0,
        wdl_total_change: ot.totalChange ?? 0
      });
    }

    // B-006: 比分赔率特征 (8维)
    if (matchContext.scoreOdds) {
      const so = matchContext.scoreOdds;
      Object.assign(features, {
        score_mode_prob: so.modeProb ?? 0,
        score_entropy: so.entropy ?? 0,
        score_home_win_prob: so.homeWinProb ?? 0.33,
        score_draw_prob: so.drawProb ?? 0.34,
        score_away_win_prob: so.awayWinProb ?? 0.33,
        score_over_25_prob: so.over25Prob ?? 0.5,
        score_expected_goals: so.expectedGoals ?? 2.5,
        score_top3_concentration: so.top3Concentration ?? 0
      });
    }

    // B-006: 非线性变换特征 NL (29维) - 从赔率特征衍生
    if (matchContext.odds) {
      const wdlIW = features.wdl_implied_win || 0.33;
      const wdlID = features.wdl_implied_draw || 0.34;
      const wdlIL = features.wdl_implied_lose || 0.33;
      const wdlCW = features.wdl_close_win || 0;
      const wdlCD = features.wdl_close_draw || 0;
      const wdlCL = features.wdl_close_lose || 0;
      const hcpCW = features.hcp_close_win || 0;
      const hcpCD = features.hcp_close_draw || 0;
      const hcpCL = features.hcp_close_lose || 0;
      const oc = features.odds_confidence || 0.33;
      const oe = features.odds_entropy || 1.0;
      const bm = features.bookmaker_margin || 0;
      const wdlKW = features.wdl_kelly_win || 0;
      const wdlKL = features.wdl_kelly_lose || 0;

      Object.assign(features, {
        wdl_winA_log: Math.log(Math.max(wdlIW, 0.001)),
        wdl_winA_sqrt: Math.sqrt(Math.max(wdlIW, 0)),
        wdl_winA_inv: wdlIW > 0 ? 1.0 / wdlIW : 0,
        wdl_drawA_log: Math.log(Math.max(wdlID, 0.001)),
        wdl_drawA_sqrt: Math.sqrt(Math.max(wdlID, 0)),
        wdl_drawA_inv: wdlID > 0 ? 1.0 / wdlID : 0,
        wdl_loseA_log: Math.log(Math.max(wdlIL, 0.001)),
        wdl_loseA_sqrt: Math.sqrt(Math.max(wdlIL, 0)),
        wdl_loseA_inv: wdlIL > 0 ? 1.0 / wdlIL : 0,
        wdl_imp_win_sq: wdlIW * wdlIW,
        wdl_imp_win_cu: wdlIW * wdlIW * wdlIW,
        wdl_imp_win_log: Math.log(Math.max(wdlIW, 0.001)),
        wdl_imp_draw_sq: wdlID * wdlID,
        wdl_imp_lose_sq: wdlIL * wdlIL,
        wdl_imp_lose_cu: wdlIL * wdlIL * wdlIL,
        wdl_imp_lose_log: Math.log(Math.max(wdlIL, 0.001)),
        wdl_implied_entropy: oe,
        wdl_implied_gini: 1 - (wdlIW*wdlIW + wdlID*wdlID + wdlIL*wdlIL),
        hcp_close_win_log: Math.log(Math.max(hcpCW, 0.001)),
        hcp_close_draw_log: Math.log(Math.max(hcpCD, 0.001)),
        hcp_close_lose_log: Math.log(Math.max(hcpCL, 0.001)),
        tg_over_25_odds: features.tg_over_25_prob > 0 ? 1.0 / features.tg_over_25_prob : 0,
        tg_under_25_odds: features.tg_under_25_prob > 0 ? 1.0 / features.tg_under_25_prob : 0,
        odds_confidence_sq: oc * oc,
        odds_entropy_log: Math.log(Math.max(oe, 0.001)),
        bookmaker_margin_sqrt: Math.sqrt(Math.max(bm, 0)),
        wdl_imp_win_x_kelly: wdlIW * wdlKW,
        wdl_imp_lose_x_kelly: wdlIL * wdlKL,
        wdl_imp_ratio: wdlIL > 0 ? wdlIW / wdlIL : 0
      });
    }

    // ===== P0-D: 训练-推理特征对齐补全（17 维可计算特征）=====
    // 时间特征（6维）— 与 feature_utils.build_features 一致（Python dayofweek: Monday=0）
    let matchDate = matchContext.date ?? options.date ?? null;
    if (!(matchDate instanceof Date)) {
      if (typeof matchDate === 'number') matchDate = new Date(matchDate);
      else if (typeof matchDate === 'string') matchDate = new Date(matchDate);
      else matchDate = new Date();
      if (isNaN(matchDate.getTime())) matchDate = new Date();
    }
    const matchMonth = matchDate.getMonth() + 1;
    const matchDayOfWeek = (matchDate.getDay() + 6) % 7; // JS getDay(): Sunday=0 → Python: Monday=0
    Object.assign(features, {
      month: matchMonth,
      day_of_week: matchDayOfWeek,
      is_weekend: matchDayOfWeek >= 5 ? 1 : 0,
      is_early_season: (matchMonth === 8 || matchMonth === 9) ? 1 : 0,
      is_mid_season: (matchMonth === 10 || matchMonth === 11 || matchMonth === 12 || matchMonth === 1 || matchMonth === 2) ? 1 : 0,
      is_late_season: (matchMonth === 3 || matchMonth === 4 || matchMonth === 5) ? 1 : 0
    });

    // 实力差距特征（3维）— 与 feature_utils.build_draw_enhanced_features A 组一致
    const strHomeElo = matchContext.elo?.homeElo ?? 1500;
    const strAwayElo = matchContext.elo?.awayElo ?? 1500;
    const eloGap = Math.abs(strHomeElo - strAwayElo);
    const eloMax = Math.max(strHomeElo, strAwayElo);
    Object.assign(features, {
      strength_closeness: Math.exp(-eloGap / 100.0),
      strength_gap_indicator: eloGap < 50 ? 1.0 : Math.max(0.0, 1.0 - (eloGap - 50) / 100.0),
      strength_balance: eloMax > 0 ? Math.min(strHomeElo, strAwayElo) / (eloMax + 1e-6) : 1.0
    });

    // 赔率波动相对特征（3维）— 与 feature_utils.build_draw_enhanced_features B 组一致
    // 无时序数据时 wdl_*_volatility=0 → stability=1.0, balance=0, relative=0（训练端无数据行同值）
    const vWin = features.wdl_win_volatility ?? 0;
    const vDraw = features.wdl_draw_volatility ?? 0;
    const vLose = features.wdl_lose_volatility ?? 0;
    const vMean = (vWin + vDraw + vLose) / 3;
    const vVar = ((vWin - vMean) ** 2 + (vDraw - vMean) ** 2 + (vLose - vMean) ** 2) / 3;
    Object.assign(features, {
      draw_odds_stability: 1.0 / (1.0 + vDraw),
      odds_volatility_balance: Math.sqrt(vVar),
      draw_vol_relative: vDraw / (vWin + vLose + 1e-6)
    });

    // P0-D: 合并预计算桥接特征（sofa_*/pa_*/mkt_/odds_ts_ 真实值），
    // 替换缺口特征的均值填充。桥接键均为缺口键，不覆盖上方已计算键。
    if (matchContext && matchContext.externalFeatures) {
      Object.assign(features, matchContext.externalFeatures);
    }

    return { ...features, ...teamFeatures };
  }

  normalizeFeatures(features) {
    if (!this.featureScalerParams) return features;

    const { mean, scale, feature_names } = this.featureScalerParams;
    const normalized = {};

    feature_names.forEach((name, index) => {
      // B-006: 缺失特征使用均值作为默认值，归一化后为 0 (中性)，而非 (0-mean)/scale 的负偏移
      const value = features[name] !== undefined ? features[name] : mean[index];
      normalized[name] = scale[index] !== 0 ? (value - mean[index]) / scale[index] : 0;
    });

    return normalized;
  }

  getFeatureVector(features) {
    if (!this.featureScalerParams) return [];

    const { feature_names } = this.featureScalerParams;
    return feature_names.map(name => features[name] !== undefined ? features[name] : 0);
  }

  predictXGB(teamA, teamB, options, matchContext) {
    const features = this.buildFeatures(teamA, teamB, options, matchContext);
    const normalizedFeatures = this.normalizeFeatures(features);
    const prob = this.treeModelPredict(this.xgbModel, normalizedFeatures);

    const calibrated = this.applyPlattCalibration([prob.winA, prob.draw, prob.winB], this.xgbCalibration);

    return {
      winA: calibrated[2],
      draw: calibrated[1],
      winB: calibrated[0],
      model: 'XGBoost',
      calibrated: this.xgbCalibration !== null
    };
  }

  predictLightGBM(teamA, teamB, options, matchContext) {
    const features = this.buildFeatures(teamA, teamB, options, matchContext);
    const normalizedFeatures = this.normalizeFeatures(features);
    const prob = this.treeModelPredict(this.lgbModel, normalizedFeatures);

    const calibrated = this.applyPlattCalibration([prob.winA, prob.draw, prob.winB], this.lgbCalibration);

    return {
      winA: calibrated[2],
      draw: calibrated[1],
      winB: calibrated[0],
      model: 'LightGBM',
      calibrated: this.lgbCalibration !== null
    };
  }

  treeModelPredict(model, features) {
    if (!model || !model.trees || model.trees.length === 0) {
      const avgAttack = ((features.home_xg || 1.0) + (features.away_xg || 1.0)) / 2;
      const attackDiff = features.xg_diff || 0;

      let winA = 0.5 + attackDiff * 0.3;
      let winB = 0.5 - attackDiff * 0.3;
      let draw = 0.25 + Math.exp(-Math.abs(attackDiff) * 5) * 0.15;

      const total = winA + draw + winB;
      return {
        winA: Math.min(0.75, Math.max(0.05, winA / total)),
        draw: Math.min(0.5, Math.max(0.1, draw / total)),
        winB: Math.min(0.75, Math.max(0.05, winB / total))
      };
    }

    let base = model.base || 0.5;
    let lr = model.lr || 0.1;
    let logits = [0, 0, 0];

    for (let t = 0; t < model.trees.length; t++) {
      const treeVal = this.predictTree(model.trees[t], features);
      const classIdx = t % 3;
      logits[classIdx] = Math.max(-20, Math.min(20, logits[classIdx] + lr * treeVal));
    }

    for (let i = 0; i < 3; i++) {
      logits[i] += base;
    }

    const maxLogit = Math.max(...logits);
    const expLogits = logits.map(l => Math.exp(l - maxLogit));
    const sumExp = expLogits.reduce((a, b) => a + b, 0);
    const probs = expLogits.map(e => e / sumExp);

    return {
      winA: probs[2],
      draw: probs[1],
      winB: probs[0]
    };
  }

  predictTree(tree, features, leq = false, f32 = false) {
    if (!tree || !tree.nodes) return 0;

    const nodeMap = {};
    // C-20260819-007: 修复 rootNodeId 初始化 bug
    // 原实现 `let rootNodeId = 0; if (rootNodeId === 0 || ...)` 中 `rootNodeId === 0`
    // 永真(当 rootNodeId=0 时),导致遍历到 node_id>0 的第一个节点时错误更新,
    // root 被误设为 node_id=1(根的左子节点)而非 node_id=0。
    // t006 parity 测试发现:200 棵树累积 Σleaf 偏差 0.5335,概率差异 0.132。
    // 修复:用 null 初始值 + `=== null` 判断,正确找到最小 node_id(即 root)。
    let rootNodeId = null;

    tree.nodes.forEach(node => {
      nodeMap[node.node_id] = node;
      if (rootNodeId === null || node.node_id < rootNodeId) {
        rootNodeId = node.node_id;
      }
    });

    let node = nodeMap[rootNodeId];
    if (!node) return 0;

    while (node && node.split !== undefined) {
      const feature = node.split.feature;
      const threshold = node.split.threshold;
      let featureVal = 0;

      if (feature.startsWith('f')) {
        const idx = parseInt(feature.substring(1));
        if (!isNaN(idx) && this.featureScalerParams) {
          const featureName = this.featureScalerParams.feature_names[idx];
          featureVal = features[featureName] !== undefined ? features[featureName] : 0;
        }
      } else if (feature.startsWith('Column_')) {
        const idx = parseInt(feature.substring(8));
        if (!isNaN(idx) && this.featureScalerParams) {
          const featureName = this.featureScalerParams.feature_names[idx];
          featureVal = features[featureName] !== undefined ? features[featureName] : 0;
        }
      } else {
        featureVal = features[feature] !== undefined ? features[feature] : 0;
      }

      // C-20260909-007: 区分 split 决策「<」(XGB) 与「<=」(LGB)
      // LGB dump decision_type="<=" → leq=true 时特征值恰等于阈值走左枝，与 py 一致
      // C-20260909-008: XGB 内部 float32 比较（特征+阈值均 fround 后再比，边界行对齐 py；
      //   只 fround 特征是混合模式，实证偏差反而更大 3.88e-02）；LGB 为 float64 比较（f32=false）
      const cmpVal = f32 ? Math.fround(featureVal) : featureVal;
      const cmpThr = f32 ? Math.fround(threshold) : threshold;
      const nextNodeId = leq
        ? (cmpVal <= cmpThr ? node.split.left : node.split.right)
        : (cmpVal < cmpThr ? node.split.left : node.split.right);
      node = nodeMap[nextNodeId];

      if (!node) break;
    }

    return node ? (node.leaf || 0) : 0;
  }

  /**
   * 阶段 B (unified_engine_integration_plan §7): λ 回归头单树推理（Poisson link）
   * λ = exp(base + lr · Σleaf)
   *   - XGB (reg:poisson): base = log(mean)，split 严格小于（leq=false），
   *     内部 float32 比较（f32=true，C-20260909-008 边界行实证）
   *   - LGB (poisson):     base = 0（C-20260909-006: predict() 不含 init_score），
   *     split <=（leq=true），float64 比较（f32=false）
   * 与 train_models.convert_lambda_to_js 导出约定一致。
   */
  predictLambdaTree(model, features) {
    if (!model || !model.trees || model.trees.length === 0) return 0;
    let raw = model.base || 0;
    const lr = model.lr || 1.0;
    const leq = model.leq === true;
    const f32 = model.leq !== true;  // XGB: float32 比较；LGB: float64
    for (let t = 0; t < model.trees.length; t++) {
      raw += lr * this.predictTree(model.trees[t], features, leq, f32);
    }
    raw = Math.max(-20, Math.min(20, raw));
    return Math.exp(raw);
  }

  /**
   * 阶段 B: λ 回归头推理（XGB+LGB 四模型平均 → λ_home/λ_away）
   * @param {Object} features - normalizeFeatures(features) 后的特征对象
   * @returns {{lambdaHome:number, lambdaAway:number}|null} 模型缺失/特征缺失时返回 null
   */
  predictLambdaFromModel(features) {
    if (!this.lambdaModel || !this.lambdaModel.models) return null;
    const models = this.lambdaModel.models;
    let lhSum = 0, laSum = 0, lhCnt = 0, laCnt = 0;
    for (const key of ['xgb_home', 'lgb_home']) {
      if (models[key]) {
        lhSum += this.predictLambdaTree(models[key], features);
        lhCnt++;
      }
    }
    for (const key of ['xgb_away', 'lgb_away']) {
      if (models[key]) {
        laSum += this.predictLambdaTree(models[key], features);
        laCnt++;
      }
    }
    if (lhCnt === 0 || laCnt === 0) return null;
    const lambdaHome = Math.max(0.15, Math.min(6.5, lhSum / lhCnt));
    const lambdaAway = Math.max(0.15, Math.min(6.5, laSum / laCnt));
    return { lambdaHome, lambdaAway };
  }

  calcModelDisagreement(modelProbs) {
    if (modelProbs.length < 2) return 0;

    const meanWinA = modelProbs.reduce((sum, p) => sum + p.winA, 0) / modelProbs.length;
    const meanDraw = modelProbs.reduce((sum, p) => sum + p.draw, 0) / modelProbs.length;
    const meanWinB = modelProbs.reduce((sum, p) => sum + p.winB, 0) / modelProbs.length;

    let variance = 0;
    modelProbs.forEach(p => {
      variance += Math.pow(p.winA - meanWinA, 2);
      variance += Math.pow(p.draw - meanDraw, 2);
      variance += Math.pow(p.winB - meanWinB, 2);
    });

    const stdDev = Math.sqrt(variance / (modelProbs.length * 3));
    return Math.min(1, stdDev * 3);
  }

  calcDynamicConfidence(winA, draw, winB, disagreement) {
    const maxProb = Math.max(winA, draw, winB);
    const entropy = -winA * Math.log(winA + 0.0001) - draw * Math.log(draw + 0.0001) - winB * Math.log(winB + 0.0001);
    const normalizedEntropy = entropy / Math.log(3);

    const probScore = maxProb * 2 - 0.5;
    const entropyScore = 1 - normalizedEntropy;
    const disagreementScore = 1 - disagreement;

    let rawConfidence = probScore * 0.4 + entropyScore * 0.3 + disagreementScore * 0.3;

    if (this.confidenceHistory.length >= 20) {
      const recentHistory = this.confidenceHistory.slice(-20);
      const avgConfidence = recentHistory.reduce((sum, r) => sum + r.confidence, 0) / recentHistory.length;
      const correctRate = recentHistory.filter(r => r.wasCorrect).length / recentHistory.length;

      const calibrationFactor = correctRate / (avgConfidence + 0.0001);
      rawConfidence = rawConfidence * Math.min(1.5, Math.max(0.5, calibrationFactor));
    }

    const clampedConfidence = Math.max(0.1, Math.min(0.95, rawConfidence));

    let level;
    if (clampedConfidence >= 0.8) level = 'high';
    else if (clampedConfidence >= 0.6) level = 'medium';
    else if (clampedConfidence >= 0.4) level = 'low';
    else level = 'very_low';

    return {
      score: clampedConfidence,
      level,
      breakdown: {
        probability: probScore,
        entropy: entropyScore,
        disagreement: disagreementScore
      },
      calibrated: this.confidenceHistory.length >= 20
    };
  }

  determineMatchType(teamA, teamB, options) {
    const league = teamA.league || teamB.league || '';
    const competitionType = options?.competitionType || 'regular';

    if (competitionType === 'cup') return 'cup';
    
    const derbyPairs = [
      ['Manchester United', 'Manchester City'],
      ['Liverpool', 'Everton'],
      ['Arsenal', 'Tottenham'],
      ['Real Madrid', 'Barcelona'],
      ['AC Milan', 'Inter Milan'],
      ['Bayern Munich', 'Dortmund'],
      ['Paris Saint-Germain', 'Marseille']
    ];

    for (const [t1, t2] of derbyPairs) {
      if ((teamA.name === t1 && teamB.name === t2) || (teamA.name === t2 && teamB.name === t1)) {
        return 'derby';
      }
    }

    if (options?.isPromotionRace) return 'promotion';
    if (options?.isRelegationBattle) return 'relegation';

    return 'regular';
  }

  getDynamicWeights(teamA, teamB, options) {
    const matchType = this.determineMatchType(teamA, teamB, options);
    const baseWeights = this.matchTypeWeights[matchType] || this.matchTypeWeights.regular;

    const modelConfidences = {
      poisson: 0.6,
      dixonCole: 0.5,
      ssm: 0.55,
      xgboost: 0.7,
      lightgbm: 0.65,
      elo: 0.5
    };

    let totalWeight = 0;
    const adjustedWeights = {};

    for (const [model, baseWeight] of Object.entries(baseWeights)) {
      const confidence = modelConfidences[model] || 0.5;
      adjustedWeights[model] = baseWeight * confidence;
      totalWeight += adjustedWeights[model];
    }

    for (const [model, weight] of Object.entries(adjustedWeights)) {
      adjustedWeights[model] = weight / totalWeight;
    }

    return { weights: adjustedWeights, matchType, dynamic: true };
  }

  monteCarloSimulation(teamA, teamB, options, nSimulations = 1000) {
    const results = [];
    
    for (let i = 0; i < nSimulations; i++) {
      const lambdas = this.calcLambdaMatch(teamA, teamB, options);
      
      const homeGoals = this.poissonRandom(lambdas.lambdaA);
      const awayGoals = this.poissonRandom(lambdas.lambdaB);
      
      let result;
      if (homeGoals > awayGoals) result = 2;
      else if (homeGoals < awayGoals) result = 0;
      else result = 1;
      
      results.push({ homeGoals, awayGoals, result });
    }

    const winCount = results.filter(r => r.result === 2).length;
    const drawCount = results.filter(r => r.result === 1).length;
    const loseCount = results.filter(r => r.result === 0).length;

    const goalDiffDist = {};
    results.forEach(r => {
      const diff = r.homeGoals - r.awayGoals;
      goalDiffDist[diff] = (goalDiffDist[diff] || 0) + 1;
    });

    const goalDist = {};
    results.forEach(r => {
      const total = r.homeGoals + r.awayGoals;
      goalDist[total] = (goalDist[total] || 0) + 1;
    });

    const winPercentiles = this.calculatePercentiles(results.filter(r => r.result === 2).map(r => r.homeGoals - r.awayGoals));
    const drawPercentiles = this.calculatePercentiles(results.filter(r => r.result === 1).map(r => r.homeGoals));
    const losePercentiles = this.calculatePercentiles(results.filter(r => r.result === 0).map(r => r.awayGoals - r.homeGoals));

    return {
      simulations: nSimulations,
      winProb: winCount / nSimulations,
      drawProb: drawCount / nSimulations,
      loseProb: loseCount / nSimulations,
      goalDiffDistribution: goalDiffDist,
      totalGoalsDistribution: goalDist,
      percentiles: {
        win: winPercentiles,
        draw: drawPercentiles,
        lose: losePercentiles
      },
      variance: {
        win: this.calculateVariance(results.map(r => r.result === 2 ? 1 : 0)),
        draw: this.calculateVariance(results.map(r => r.result === 1 ? 1 : 0)),
        lose: this.calculateVariance(results.map(r => r.result === 0 ? 1 : 0))
      }
    };
  }

  poissonRandom(lambda) {
    let L = Math.exp(-lambda);
    let p = 1.0;
    let k = 0;

    do {
      k++;
      p *= Math.random();
    } while (p > L);

    return k - 1;
  }

  calculatePercentiles(data) {
    if (data.length === 0) return { p5: 0, p25: 0, p50: 0, p75: 0, p95: 0 };
    
    data.sort((a, b) => a - b);
    const n = data.length;
    
    return {
      p5: data[Math.floor(n * 0.05)] || 0,
      p25: data[Math.floor(n * 0.25)] || 0,
      p50: data[Math.floor(n * 0.5)] || 0,
      p75: data[Math.floor(n * 0.75)] || 0,
      p95: data[Math.floor(n * 0.95)] || 0
    };
  }

  calculateVariance(data) {
    if (data.length === 0) return 0;
    const mean = data.reduce((sum, val) => sum + val, 0) / data.length;
    const squaredDiffs = data.map(val => Math.pow(val - mean, 2));
    return squaredDiffs.reduce((sum, val) => sum + val, 0) / data.length;
  }

  async predictStacked(teamAKey, teamBKey, teamA, teamB, options, matchContext) {
    options = options || {};

    const poisson = this.predictMatch(teamA, teamB, options);
    const dc = this.predictMatchDC(teamA, teamB, options);
    const ssm = this.predictMatchSSM(teamA, teamB, options);
    const xgb = this.predictXGB(teamA, teamB, options, matchContext);
    const lgb = this.predictLightGBM(teamA, teamB, options, matchContext);
    const elo = this.predictElo(teamAKey, teamBKey, options);

    const { weights, matchType, dynamic } = this.getDynamicWeights(teamA, teamB, options);

    const models = [
      { name: 'poisson', data: poisson, weight: weights.poisson },
      { name: 'dixonCole', data: dc, weight: weights.dixonCole },
      { name: 'ssm', data: ssm, weight: weights.ssm },
      { name: 'xgboost', data: xgb, weight: weights.xgboost },
      { name: 'lightgbm', data: lgb, weight: weights.lightgbm },
      { name: 'elo', data: elo, weight: weights.elo }
    ];

    let winA = 0, draw = 0, winB = 0;
    let totalWeight = 0;

    models.forEach(m => {
      if (typeof m.data.winA === 'number' && typeof m.data.draw === 'number' && typeof m.data.winB === 'number') {
        winA += m.weight * m.data.winA;
        draw += m.weight * m.data.draw;
        winB += m.weight * m.data.winB;
        totalWeight += m.weight;
      }
    });

    if (totalWeight > 0) {
      winA /= totalWeight;
      draw /= totalWeight;
      winB /= totalWeight;
    } else {
      winA = 0.33;
      draw = 0.34;
      winB = 0.33;
    }

    const total = winA + draw + winB;
    if (total > 0) {
      winA /= total;
      draw /= total;
      winB /= total;
    }

    const components = {};
    const modelProbs = [];
    models.forEach(m => {
      const w = typeof m.data.winA === 'number' ? m.data.winA : 0.33;
      const d = typeof m.data.draw === 'number' ? m.data.draw : 0.34;
      const l = typeof m.data.winB === 'number' ? m.data.winB : 0.33;
      components[m.name] = { winA: w, draw: d, winB: l, weight: m.weight };
      modelProbs.push({ winA: w, draw: d, winB: l });
    });

    const disagreement = this.calcModelDisagreement(modelProbs);
    const confidence = this.calcDynamicConfidence(winA, draw, winB, disagreement);

    const mcSimulation = this.monteCarloSimulation(teamA, teamB, options, 500);

    return {
      winA,
      draw,
      winB,
      components,
      dynamicWeights: dynamic,
      matchType,
      model: 'Stacked-Ensemble-v7.0',
      confidence,
      uncertainty: mcSimulation
    };
  }

  calcPlayerImpact(lineup, leagueTier, leagueTierWeight) {
    if (!lineup || !Array.isArray(lineup)) return { total: 0 };

    let totalImpact = 0;
    const playerImpacts = [];

    lineup.forEach(player => {
      const { rating, keyContribution, league } = player;

      const ratingImpact = (rating - 7.0) * 0.1;
      const contributionImpact = keyContribution || 0;

      let tierImpact = 0;
      if (leagueTierWeight) {
        for (const [level, leagues] of Object.entries(leagueTier)) {
          if (leagues.includes(league)) {
            tierImpact = (leagueTierWeight[level] || 0.3) - 0.5;
            break;
          }
        }
      }

      const total = ratingImpact + contributionImpact + tierImpact;
      playerImpacts.push({ ...player, impact: total });
      totalImpact += total;
    });

    return { total: totalImpact / lineup.length, players: playerImpacts };
  }

  /**
   * A-002: 中比分 λ 调整
   * 基于 WDL 预测概率和赔率隐含总进球数对 lambda 进行第二因子调整
   *
   * 问题: 原始 lambda 仅由球队属性计算，过于保守，导致中比分(3-4球) Top-1=0%
   * 公式: λ_adjusted = λ × wdlScale × tgScale
   *   - wdlScale = 0.5 + P(win) × 1.5  (clamp to [0.7, 1.8])
   *   - tgScale = tgExpected / (λH + λA)  (clamp to [0.85, 1.4], 仅当有赔率数据时)
   *
   * @param {number} lambdaHome - 原始主队 lambda
   * @param {number} lambdaAway - 原始客队 lambda
   * @param {Object} stackedResult - Stacking 集成结果 { winA, winB }
   * @param {Object} oddsContext - 可选: 赔率上下文 { tgExpected }
   * @returns {{ lambdaHome: number, lambdaAway: number, wdlScaleHome: number, wdlScaleAway: number, tgScale: number }}
   */
  adjustLambdaForMidScore(lambdaHome, lambdaAway, stackedResult, oddsContext = null) {
    // 第一因子: WDL 概率缩放
    // P1 修复 (C-20260816-193): 优先使用赔率隐含概率，解除与 WDL 模型的耦合
    // 旧逻辑: winA/winB 来自 WDL 模型预测 → 模型误差传递到 λ 调整
    // 新逻辑: 优先使用赔率隐含 P(win)，仅当无赔率时回退到模型预测
    let winA, winB;
    if (oddsContext?.impliedWinA !== undefined && oddsContext?.impliedWinB !== undefined) {
      // 使用赔率隐含概率（市场信息，独立于模型）
      winA = oddsContext.impliedWinA;
      winB = oddsContext.impliedWinB;
    } else {
      // 回退: 模型预测（无赔率数据时）
      winA = stackedResult?.winA ?? 0.33;
      winB = stackedResult?.winB ?? 0.33;
    }
    const wdlScaleHome = Math.max(0.7, Math.min(1.8, 0.5 + winA * 1.5));
    const wdlScaleAway = Math.max(0.7, Math.min(1.8, 0.5 + winB * 1.5));

    // 第二因子: 赔率隐含总进球数缩放 (仅当有赔率数据时)
    let tgScale = 1.0;
    if (oddsContext?.tgExpected && oddsContext.tgExpected > 0) {
      const baseTotal = lambdaHome + lambdaAway;
      if (baseTotal > 0.1) {
        tgScale = oddsContext.tgExpected / baseTotal;
        tgScale = Math.max(0.85, Math.min(1.4, tgScale));
      }
    }

    return {
      lambdaHome: lambdaHome * wdlScaleHome * tgScale,
      lambdaAway: lambdaAway * wdlScaleAway * tgScale,
      wdlScaleHome,
      wdlScaleAway,
      tgScale
    };
  }

  createPredictionResult(homeTeamKey, awayTeamKey, teamA, teamB, stackedResult, lambdaA, lambdaB, options = {}) {
    // 阶段 B (unified_engine_integration_plan §7): λ 回归头优先（WDL 分类保持主路径）
    // λ 模型已加载且特征可构建时，用 ML 回归 λ 替换 calcLambdaMatch 的 λ 作为 raw 输入；
    // 后续 A-002 adjustLambdaForMidScore（赔率隐含 P(win) + tg 缩放）与 WDL 重加权仍保留，
    // 比分/让球/大小球从同一 λ 导出，保证四维一致。模型缺失/失败 → 回退调用方传入 λ。
    if (this.lambdaModel) {
      try {
        const features = this.buildFeatures(teamA, teamB, options, undefined);
        const normalizedFeatures = this.normalizeFeatures(features);
        const mlLambda = this.predictLambdaFromModel(normalizedFeatures);
        if (mlLambda) {
          lambdaA = mlLambda.lambdaHome;
          lambdaB = mlLambda.lambdaAway;
        }
      } catch (_) { /* λ 头失败 → 保持 calcLambdaMatch 的 λ */ }
    }

    // 平局概率校准（先做校准，再用于 lambda 调整）
    const rawWDL = {
      win: stackedResult.winA,
      draw: stackedResult.draw,
      lose: stackedResult.winB
    };
    const calibratedWDL = options.calibrateDraw !== false
      ? this.calibrateDrawProbability(rawWDL, options.targetDrawRate || 0.26)
      : rawWDL;

    // A-002: 中比分 λ 调整 — 基于校准后 WDL 概率 + 赔率隐含总进球数
    // P1 修复 (C-20260816-193): 优先使用赔率隐含 P(win) 替代模型预测，解除耦合
    const wdlForLambda = { winA: calibratedWDL.win, winB: calibratedWDL.lose };
    const oddsCtx = options.oddsContext || null;
    // 如果赔率上下文中有胜平负赔率，计算隐含概率并注入
    if (oddsCtx && !oddsCtx.impliedWinA && oddsCtx.winOdds) {
      const invSum = (1/oddsCtx.winOdds) + (1/oddsCtx.drawOdds || 3.5) + (1/oddsCtx.loseOdds || 3.5);
      oddsCtx.impliedWinA = (1/oddsCtx.winOdds) / invSum;
      oddsCtx.impliedWinB = (1/oddsCtx.loseOdds || 3.5) / invSum;
    }
    const adjusted = this.adjustLambdaForMidScore(lambdaA, lambdaB, wdlForLambda, oddsCtx);
    const adjLambdaA = adjusted.lambdaHome;
    const adjLambdaB = adjusted.lambdaAway;

    // T-006 v4: 使用 Dixon-Coles + Monte Carlo + 赔率融合替代纯 Poisson
    const v4Result = this.predictScoreV4(adjLambdaA, adjLambdaB, {
      rho: options.rho || -0.30,
      rhoHigh: options.rhoHigh || -0.10,
      highscoreMinGoals: 5,
      maxGoals: 7
    });

    const mcProbs = this.monteCarloScoreSimulate(adjLambdaA, adjLambdaB, 3000, 7);
    const fusedScores = this.fuseScorePredictions(
      v4Result.scoreProbabilities,
      mcProbs,
      options.scoreOdds || null
    );

    const handicap = this.calcHandicap(adjLambdaA, adjLambdaB);
    const totalGoals = this.calcTotalGoals(adjLambdaA, adjLambdaB);

    return {
      match: {
        homeTeam: { key: homeTeamKey, name: teamA.name },
        awayTeam: { key: awayTeamKey, name: teamB.name }
      },
      lambda: {
        home: adjLambdaA,
        away: adjLambdaB,
        ratio: adjLambdaA / adjLambdaB,
        raw: { home: lambdaA, away: lambdaB },
        adjustment: {
          wdlScaleHome: adjusted.wdlScaleHome,
          wdlScaleAway: adjusted.wdlScaleAway,
          tgScale: adjusted.tgScale,
          method: 'A-002-mid-score'
        }
      },
      predictions: {
        winDrawLose: calibratedWDL,
        winDrawLoseRaw: rawWDL,
        handicap,
        totalGoals,
        topScores: this.getTopScores(fusedScores, 10),
        scoreDistribution: fusedScores,
        scoreModel: 'T-006-v4',
        scoreCorrection: v4Result.correctionStats
      },
      confidence: stackedResult.confidence,
      uncertainty: stackedResult.uncertainty,
      teams: {
        home: teamA,
        away: teamB
      },
      ensemble: {
        model: stackedResult.model,
        components: stackedResult.components,
        weights: stackedResult.components ?
          Object.fromEntries(Object.entries(stackedResult.components).map(([k, v]) => [k, v.weight])) : {},
        dynamicWeights: stackedResult.dynamicWeights,
        matchType: stackedResult.matchType
      },
      timestamp: new Date().toISOString()
    };
  }
}

export { PredictionEngine, DEFAULT_STACKING_WEIGHTS };
export default PredictionEngine;
