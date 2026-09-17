class WalkForwardBacktest {
  constructor(options = {}) {
    this.trainWindowSize = options.trainWindowSize || 20;
    this.testWindowSize = options.testWindowSize || 5;
    this.stride = options.stride || 5;
    this.evaluationMetrics = options.evaluationMetrics || ['accuracy', 'precision', 'recall', 'f1'];
    this.verbose = options.verbose || false;
  }

  run(data, modelFactory, featureExtractor) {
    const nSamples = data.length;
    if (nSamples < this.trainWindowSize + this.testWindowSize) {
      throw new Error(`数据量不足，至少需要${this.trainWindowSize + this.testWindowSize}个样本`);
    }

    const results = {
      folds: [],
      overallMetrics: {},
      detailedResults: [],
      trainingHistory: []
    };

    let trainStart = 0;
    let foldIndex = 0;

    while (trainStart + this.trainWindowSize + this.testWindowSize <= nSamples) {
      const trainEnd = trainStart + this.trainWindowSize;
      const testEnd = trainEnd + this.testWindowSize;

      const trainData = data.slice(trainStart, trainEnd);
      const testData = data.slice(trainEnd, testEnd);

      if (this.verbose) {
        console.log(`\n=== Fold ${foldIndex + 1} ==`);
        console.log(`训练集: ${trainStart + 1} - ${trainEnd}`);
        console.log(`测试集: ${trainEnd + 1} - ${testEnd}`);
        console.log(`训练样本数: ${trainData.length}, 测试样本数: ${testData.length}`);
      }

      const foldResult = this._runSingleFold(trainData, testData, modelFactory, featureExtractor);
      foldResult.foldIndex = foldIndex;
      foldResult.trainRange = { start: trainStart, end: trainEnd };
      foldResult.testRange = { start: trainEnd, end: testEnd };

      results.folds.push(foldResult);
      results.detailedResults.push(...foldResult.detailedResults);

      if (this.verbose) {
        console.log(`准确率: ${(foldResult.metrics.accuracy * 100).toFixed(2)}%`);
        console.log(`精确率: ${(foldResult.metrics.precision * 100).toFixed(2)}%`);
        console.log(`召回率: ${(foldResult.metrics.recall * 100).toFixed(2)}%`);
        console.log(`F1: ${foldResult.metrics.f1.toFixed(4)}`);
      }

      trainStart += this.stride;
      foldIndex++;
    }

    results.overallMetrics = this._computeOverallMetrics(results.folds);
    results.trainingHistory = this._extractTrainingHistory(results.folds);

    return results;
  }

  _runSingleFold(trainData, testData, modelFactory, featureExtractor) {
    const { X_train, y_train } = this._prepareData(trainData, featureExtractor);
    const { X_test, y_test, testMatches } = this._prepareData(testData, featureExtractor, true);

    const model = modelFactory();
    model.fit(X_train, y_train);

    const predictions = model.predict(X_test);
    const probabilities = model.predictProba ? model.predictProba(X_test) : null;

    const metrics = this._computeMetrics(y_test, predictions);
    const detailedResults = this._generateDetailedResults(testMatches, predictions, probabilities, y_test);

    return {
      metrics,
      detailedResults,
      modelInfo: {
        type: model.constructor.name,
        params: this._extractModelParams(model)
      }
    };
  }

  _prepareData(data, featureExtractor, includeMatches = false) {
    const X = [];
    const y = [];
    const matches = [];

    data.forEach(match => {
      const features = featureExtractor.extractAllFeatures(match);
      const featureArray = Object.values(features);

      if (!isNaN(featureArray[0])) {
        X.push(featureArray);
        y.push(match.actualResult?.handicap || match.actualResult?.wdl);
        
        if (includeMatches) {
          matches.push(match);
        }
      }
    });

    if (includeMatches) {
      return { X, y, matches };
    }
    return { X, y };
  }

  _computeMetrics(y_true, y_pred) {
    const n = y_true.length;
    
    const tp = {};
    const fp = {};
    const fn = {};
    const tn = {};
    const classes = [...new Set([...y_true, ...y_pred])];

    classes.forEach(cls => {
      tp[cls] = 0;
      fp[cls] = 0;
      fn[cls] = 0;
      tn[cls] = 0;
    });

    for (let i = 0; i < n; i++) {
      const trueCls = y_true[i];
      const predCls = y_pred[i];

      classes.forEach(cls => {
        if (predCls === cls && trueCls === cls) {
          tp[cls]++;
        } else if (predCls === cls && trueCls !== cls) {
          fp[cls]++;
        } else if (predCls !== cls && trueCls === cls) {
          fn[cls]++;
        } else {
          tn[cls]++;
        }
      });
    }

    let correct = 0;
    for (let i = 0; i < n; i++) {
      if (y_true[i] === y_pred[i]) correct++;
    }
    const accuracy = correct / n;

    let precisionSum = 0;
    let recallSum = 0;
    let f1Sum = 0;
    let nValidClasses = 0;

    classes.forEach(cls => {
      const p = tp[cls] + fp[cls];
      const r = tp[cls] + fn[cls];
      
      if (p > 0) {
        precisionSum += tp[cls] / p;
        nValidClasses++;
      }
      if (r > 0) {
        recallSum += tp[cls] / r;
      }
      if (p > 0 && r > 0) {
        const prec = tp[cls] / p;
        const rec = tp[cls] / r;
        f1Sum += 2 * prec * rec / (prec + rec);
      }
    });

    const precision = nValidClasses > 0 ? precisionSum / nValidClasses : 0;
    const recall = nValidClasses > 0 ? recallSum / nValidClasses : 0;
    const f1 = nValidClasses > 0 ? f1Sum / nValidClasses : 0;

    return {
      accuracy,
      precision,
      recall,
      f1,
      confusionMatrix: { tp, fp, fn, tn },
      classes,
      sampleSize: n
    };
  }

  _generateDetailedResults(matches, predictions, probabilities, y_true) {
    const results = [];

    matches.forEach((match, idx) => {
      const result = {
        matchId: match.matchId,
        homeTeam: match.homeTeam,
        awayTeam: match.awayTeam,
        matchDate: match.matchDate,
        handicap: match.handicap,
        actualResult: y_true[idx],
        predictedResult: predictions[idx],
        isCorrect: y_true[idx] === predictions[idx],
        probabilities: probabilities ? probabilities[idx] : null
      };

      results.push(result);
    });

    return results;
  }

  _computeOverallMetrics(folds) {
    if (folds.length === 0) return {};

    const metrics = {};
    const metricNames = ['accuracy', 'precision', 'recall', 'f1'];

    metricNames.forEach(name => {
      const values = folds.map(f => f.metrics[name]);
      metrics[name] = {
        mean: values.reduce((a, b) => a + b, 0) / values.length,
        std: this._computeStd(values),
        min: Math.min(...values),
        max: Math.max(...values),
        values
      };
    });

    const allDetailed = folds.flatMap(f => f.detailedResults);
    const totalCorrect = allDetailed.filter(r => r.isCorrect).length;
    metrics.overallAccuracy = totalCorrect / allDetailed.length;
    metrics.totalSamples = allDetailed.length;
    metrics.totalFolds = folds.length;

    return metrics;
  }

  _computeStd(values) {
    if (values.length < 2) return 0;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (values.length - 1);
    return Math.sqrt(variance);
  }

  _extractTrainingHistory(folds) {
    return folds.map(fold => ({
      foldIndex: fold.foldIndex,
      trainSize: fold.trainRange.end - fold.trainRange.start,
      testSize: fold.testRange.end - fold.testRange.start,
      accuracy: fold.metrics.accuracy,
      modelType: fold.modelInfo.type
    }));
  }

  _extractModelParams(model) {
    const params = {};
    Object.keys(model).forEach(key => {
      if (typeof model[key] === 'number' || typeof model[key] === 'boolean' || typeof model[key] === 'string') {
        params[key] = model[key];
      }
    });
    return params;
  }

  generateReport(results) {
    let report = '='.repeat(80) + '\n';
    report += 'Walk-Forward 回测报告\n';
    report += '='.repeat(80) + '\n\n';

    report += '一、测试配置\n';
    report += `训练窗口大小: ${this.trainWindowSize}\n`;
    report += `测试窗口大小: ${this.testWindowSize}\n`;
    report += `步长: ${this.stride}\n`;
    report += `总折叠数: ${results.totalFolds}\n`;
    report += `总样本数: ${results.totalSamples}\n\n`;

    report += '二、综合指标\n';
    report += `整体准确率: ${(results.overallMetrics.overallAccuracy * 100).toFixed(2)}%\n\n`;

    report += '三、各指标统计\n';
    ['accuracy', 'precision', 'recall', 'f1'].forEach(name => {
      const m = results.overallMetrics[name];
      if (m) {
        report += `${name.toUpperCase()}:\n`;
        report += `  均值: ${(m.mean * 100).toFixed(2)}%\n`;
        report += `  标准差: ${(m.std * 100).toFixed(2)}%\n`;
        report += `  最小值: ${(m.min * 100).toFixed(2)}%\n`;
        report += `  最大值: ${(m.max * 100).toFixed(2)}%\n\n`;
      }
    });

    report += '四、折叠详情\n';
    results.folds.forEach((fold, idx) => {
      report += `折叠 ${idx + 1}:\n`;
      report += `  训练范围: ${fold.trainRange.start + 1} - ${fold.trainRange.end}\n`;
      report += `  测试范围: ${fold.testRange.start + 1} - ${fold.testRange.end}\n`;
      report += `  准确率: ${(fold.metrics.accuracy * 100).toFixed(2)}%\n`;
      report += `  模型: ${fold.modelInfo.type}\n\n`;
    });

    report += '='.repeat(80) + '\n';

    return report;
  }
}

class TimeSeriesSplit {
  constructor(nSplits = 5) {
    this.nSplits = nSplits;
  }

  split(data) {
    const nSamples = data.length;
    const splits = [];
    
    const trainSize = Math.floor(nSamples * 0.6);
    const testSize = Math.floor((nSamples - trainSize) / this.nSplits);

    for (let i = 0; i < this.nSplits; i++) {
      const testStart = trainSize + i * testSize;
      const testEnd = i === this.nSplits - 1 ? nSamples : testStart + testSize;

      splits.push({
        trainIndices: Array.from({ length: testStart }, (_, i) => i),
        testIndices: Array.from({ length: testEnd - testStart }, (_, i) => testStart + i)
      });
    }

    return splits;
  }
}

class RollingWindowSplit {
  constructor(windowSize = 30, stepSize = 5) {
    this.windowSize = windowSize;
    this.stepSize = stepSize;
  }

  split(data) {
    const nSamples = data.length;
    const splits = [];

    for (let i = 0; i + this.windowSize <= nSamples; i += this.stepSize) {
      splits.push({
        trainIndices: Array.from({ length: this.windowSize }, (_, j) => i + j),
        testIndices: i + this.windowSize < nSamples ? [i + this.windowSize] : []
      });
    }

    return splits;
  }
}

export {
  WalkForwardBacktest,
  TimeSeriesSplit,
  RollingWindowSplit
};
