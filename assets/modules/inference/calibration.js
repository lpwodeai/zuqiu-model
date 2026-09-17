/* ============================================================
 * FiveLeagues_Calibration - 模型校准模块
 * 从 model-engine.js 拆出，config 和 state 通过参数传入
 * 包含: sigmoid, Platt Scaling, Isotonic Regression
 * ============================================================ */
var FiveLeagues_Calibration = (function() {

  // ─── 默认配置 ───
  var DEFAULT_CONFIG = {
    plattLearningRate: 0.01,
    plattIterations: 100,
    isotonicBins: 10,
    minCalibrationSamples: 20,
    calibrationUpdateInterval: 5
  };

  // ─── Sigmoid 函数 ───
  function sigmoid(x) {
    return 1 / (1 + Math.exp(-x));
  }

  // ─── Platt Scaling 训练 ───
  function trainPlattScaling(predictions, outcomes, config) {
    config = config || DEFAULT_CONFIG;

    if (!predictions || !outcomes || predictions.length < config.minCalibrationSamples) {
      return { A: 0, B: 0, error: '样本不足' };
    }

    var A = 0, B = 0;
    var lr = config.plattLearningRate;

    for (var iter = 0; iter < config.plattIterations; iter++) {
      var gradA = 0, gradB = 0;
      for (var i = 0; i < predictions.length; i++) {
        var p = predictions[i];
        var o = outcomes[i];
        var x = A * p + B;
        var s = sigmoid(x);
        gradA += (s - o) * p;
        gradB += (s - o);
      }
      A -= lr * gradA / predictions.length;
      B -= lr * gradB / predictions.length;
    }

    var calibratedProbs = predictions.map(function(p) { return sigmoid(A * p + B); });
    var brier = 0;
    for (var i = 0; i < calibratedProbs.length; i++) {
      brier += Math.pow(calibratedProbs[i] - outcomes[i], 2);
    }
    brier /= predictions.length;

    return { A: A, B: B, brier: brier, samples: predictions.length };
  }

  // ─── Isotonic Regression 训练 ───
  function trainIsotonicRegression(predictions, outcomes, config) {
    config = config || DEFAULT_CONFIG;

    if (!predictions || !outcomes || predictions.length < config.minCalibrationSamples) {
      return { bins: [], error: '样本不足' };
    }

    var data = [];
    for (var i = 0; i < predictions.length; i++) {
      data.push({ prob: predictions[i], outcome: outcomes[i] });
    }
    data.sort(function(a, b) { return a.prob - b.prob; });

    var bins = config.isotonicBins;
    var binSize = Math.ceil(data.length / bins);
    var binData = [];

    for (var b = 0; b < bins; b++) {
      var start = b * binSize;
      var end = Math.min((b + 1) * binSize, data.length);
      var bin = data.slice(start, end);

      if (bin.length > 0) {
        var avgProb = bin.reduce(function(s, d) { return s + d.prob; }, 0) / bin.length;
        var actualRate = bin.reduce(function(s, d) { return s + d.outcome; }, 0) / bin.length;
        binData.push({
          lower: bin[0].prob,
          upper: bin[bin.length - 1].prob,
          avgProb: avgProb,
          actualRate: actualRate,
          count: bin.length
        });
      }
    }

    // Isotonic: 强制单调递增
    for (var b = 1; b < binData.length; b++) {
      if (binData[b].actualRate < binData[b - 1].actualRate) {
        binData[b].actualRate = binData[b - 1].actualRate;
      }
    }

    return { bins: binData, samples: predictions.length };
  }

  // ─── 应用校准 ───
  function applyCalibration(prob, method, state) {
    if (!state || !state.lastUpdated) return prob;

    if (method === 'platt') {
      var A = state.plattParams.A;
      var B = state.plattParams.B;
      return sigmoid(A * prob + B);
    } else if (method === 'isotonic') {
      var bins = state.isotonicBins;
      for (var i = 0; i < bins.length; i++) {
        if (prob >= bins[i].lower && prob <= bins[i].upper) {
          return bins[i].actualRate;
        }
      }
      return prob;
    }

    return prob;
  }

  // ─── 更新校准模型（返回新状态，不修改原状态） ───
  function updateCalibrationModel(predictions, outcomes, config, state) {
    config = config || DEFAULT_CONFIG;
    state = state || { plattParams: { A: 0, B: 0 }, isotonicBins: [], lastUpdated: null, sampleCount: 0 };

    var plattResult = trainPlattScaling(predictions, outcomes, config);
    var isotonicResult = trainIsotonicRegression(predictions, outcomes, config);

    return {
      plattParams: { A: plattResult.A, B: plattResult.B },
      isotonicBins: isotonicResult.bins || [],
      lastUpdated: new Date().toISOString(),
      sampleCount: state.sampleCount + predictions.length,
      platt: plattResult,
      isotonic: isotonicResult,
      updated: new Date().toISOString()
    };
  }

  // ─── 生成校准报告 ───
  function generateCalibrationReport(state, config) {
    config = config || DEFAULT_CONFIG;
    state = state || { plattParams: { A: 0, B: 0 }, isotonicBins: [], lastUpdated: null, sampleCount: 0 };

    var report = {
      timestamp: new Date().toISOString(),
      status: state.lastUpdated ? '已校准' : '未校准',
      lastUpdated: state.lastUpdated,
      sampleCount: state.sampleCount,
      plattParams: state.plattParams,
      isotonicBins: state.isotonicBins,
      recommendations: []
    };

    if (state.sampleCount < config.minCalibrationSamples) {
      report.recommendations.push('样本不足，建议收集更多比赛数据');
    }

    if (!state.lastUpdated) {
      report.recommendations.push('尚未进行校准，建议执行updateCalibrationModel');
    }

    return report;
  }

  return {
    DEFAULT_CONFIG: DEFAULT_CONFIG,
    sigmoid: sigmoid,
    trainPlattScaling: trainPlattScaling,
    trainIsotonicRegression: trainIsotonicRegression,
    applyCalibration: applyCalibration,
    updateCalibrationModel: updateCalibrationModel,
    generateCalibrationReport: generateCalibrationReport
  };
})();
