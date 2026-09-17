class ModelMonitor {
  constructor(options = {}) {
    this.alertThresholds = options.alertThresholds || {
      accuracyDrop: 0.10,
      precisionDrop: 0.15,
      recallDrop: 0.15,
      f1Drop: 0.15,
      consecutiveErrors: 5,
      driftThreshold: 0.20
    };

    this.performanceHistory = [];
    this.consecutiveErrors = 0;
    this.lastAlert = null;
    this.modelVersion = 'v1.0';

    this.recentPredictions = [];
    this.maxRecentPredictions = options.maxRecentPredictions || 100;
  }

  recordPrediction(prediction) {
    this.recentPredictions.push(prediction);
    
    if (this.recentPredictions.length > this.maxRecentPredictions) {
      this.recentPredictions.shift();
    }

    if (!prediction.isCorrect) {
      this.consecutiveErrors++;
    } else {
      this.consecutiveErrors = 0;
    }

    return this.checkAlerts();
  }

  recordPerformance(metrics) {
    const record = {
      timestamp: new Date().toISOString(),
      ...metrics,
      modelVersion: this.modelVersion
    };

    this.performanceHistory.push(record);

    return this.checkAlerts();
  }

  checkAlerts() {
    const alerts = [];

    if (this.consecutiveErrors >= this.alertThresholds.consecutiveErrors) {
      alerts.push({
        type: 'consecutive_errors',
        severity: 'high',
        message: `连续错误${this.consecutiveErrors}次，超过阈值${this.alertThresholds.consecutiveErrors}`,
        timestamp: new Date().toISOString()
      });
    }

    if (this.performanceHistory.length >= 2) {
      const recent = this.performanceHistory[this.performanceHistory.length - 1];
      const previous = this.performanceHistory[this.performanceHistory.length - 2];

      const drops = [];
      if (recent.accuracy && previous.accuracy) {
        const drop = previous.accuracy - recent.accuracy;
        if (drop > this.alertThresholds.accuracyDrop) {
          drops.push(`准确率下降${(drop * 100).toFixed(1)}%`);
        }
      }
      if (recent.precision && previous.precision) {
        const drop = previous.precision - recent.precision;
        if (drop > this.alertThresholds.precisionDrop) {
          drops.push(`精确率下降${(drop * 100).toFixed(1)}%`);
        }
      }
      if (recent.recall && previous.recall) {
        const drop = previous.recall - recent.recall;
        if (drop > this.alertThresholds.recallDrop) {
          drops.push(`召回率下降${(drop * 100).toFixed(1)}%`);
        }
      }
      if (recent.f1 && previous.f1) {
        const drop = previous.f1 - recent.f1;
        if (drop > this.alertThresholds.f1Drop) {
          drops.push(`F1下降${(drop * 100).toFixed(1)}%`);
        }
      }

      if (drops.length > 0) {
        alerts.push({
          type: 'performance_drop',
          severity: 'medium',
          message: `性能下降: ${drops.join(', ')}`,
          timestamp: new Date().toISOString(),
          details: { recent, previous }
        });
      }
    }

    if (alerts.length > 0) {
      this.lastAlert = alerts[alerts.length - 1];
    }

    return alerts;
  }

  getPerformanceSummary() {
    if (this.performanceHistory.length === 0) {
      return { status: 'no_data', message: '暂无性能数据' };
    }

    const recent = this.performanceHistory[this.performanceHistory.length - 1];
    const last5 = this.performanceHistory.slice(-5);
    const last30 = this.performanceHistory.slice(-30);

    const metrics = ['accuracy', 'precision', 'recall', 'f1'];
    const summary = {
      status: 'healthy',
      current: {},
      last5: {},
      last30: {},
      trends: {}
    };

    metrics.forEach(metric => {
      if (recent[metric] !== undefined) {
        summary.current[metric] = recent[metric];
      }

      const last5Values = last5.map(h => h[metric]).filter(v => v !== undefined);
      if (last5Values.length > 0) {
        summary.last5[metric] = {
          mean: last5Values.reduce((a, b) => a + b, 0) / last5Values.length,
          std: this._computeStd(last5Values)
        };
      }

      const last30Values = last30.map(h => h[metric]).filter(v => v !== undefined);
      if (last30Values.length > 0) {
        summary.last30[metric] = {
          mean: last30Values.reduce((a, b) => a + b, 0) / last30Values.length,
          std: this._computeStd(last30Values)
        };
      }

      if (last5Values.length >= 2 && last30Values.length >= 2) {
        const trend = summary.last5[metric].mean - summary.last30[metric].mean;
        summary.trends[metric] = {
          direction: trend > 0.02 ? 'up' : trend < -0.02 ? 'down' : 'stable',
          magnitude: trend
        };
      }
    });

    if (this.consecutiveErrors >= this.alertThresholds.consecutiveErrors) {
      summary.status = 'critical';
    } else if (this.lastAlert && this.lastAlert.severity === 'high') {
      summary.status = 'warning';
    }

    return summary;
  }

  computeReliabilityDiagram(predictions) {
    const bins = Array(10).fill(null).map(() => ({ count: 0, correct: 0 }));

    predictions.forEach(pred => {
      if (pred.probabilities) {
        const maxProb = Math.max(...pred.probabilities);
        const binIdx = Math.min(9, Math.floor(maxProb * 10));
        
        bins[binIdx].count++;
        if (pred.isCorrect) {
          bins[binIdx].correct++;
        }
      }
    });

    const diagram = bins.map((bin, idx) => ({
      probabilityRange: [`${idx * 0.1}`, `${(idx + 1) * 0.1}`],
      expectedProbability: (idx + 0.5) * 0.1,
      observedFrequency: bin.count > 0 ? bin.correct / bin.count : 0,
      count: bin.count,
      correct: bin.correct
    }));

    const ece = diagram.reduce((sum, bin) => {
      if (bin.count > 0) {
        return sum + bin.count * Math.abs(bin.expectedProbability - bin.observedFrequency);
      }
      return sum;
    }, 0) / predictions.length;

    return { diagram, ece };
  }

  computeCalibrationError(predictions) {
    const reliability = this.computeReliabilityDiagram(predictions);
    return {
      ece: reliability.ece,
      mce: this._computeMCE(reliability.diagram),
      brierScore: this._computeBrierScore(predictions)
    };
  }

  _computeMCE(diagram) {
    return diagram.reduce((max, bin) => {
      if (bin.count > 0) {
        return Math.max(max, Math.abs(bin.expectedProbability - bin.observedFrequency));
      }
      return max;
    }, 0);
  }

  _computeBrierScore(predictions) {
    let total = 0;
    let count = 0;

    predictions.forEach(pred => {
      if (pred.probabilities) {
        const classIdx = pred.predictedResult === 'win' ? 0 : pred.predictedResult === 'draw' ? 1 : 2;
        const actual = pred.isCorrect ? 1 : 0;
        total += Math.pow(pred.probabilities[classIdx] - actual, 2);
        count++;
      }
    });

    return count > 0 ? total / count : 0;
  }

  detectDataDrift(recentFeatures, baselineFeatures) {
    const driftScores = {};
    
    Object.keys(baselineFeatures).forEach(key => {
      if (recentFeatures[key] !== undefined) {
        const baselineMean = baselineFeatures[key].mean || baselineFeatures[key];
        const baselineStd = baselineFeatures[key].std || 0;
        const recentValue = recentFeatures[key].mean || recentFeatures[key];

        if (baselineStd > 0) {
          const zScore = Math.abs((recentValue - baselineMean) / baselineStd);
          driftScores[key] = zScore;
        }
      }
    });

    const maxDrift = Math.max(...Object.values(driftScores), 0);
    const hasDrift = maxDrift > this.alertThresholds.driftThreshold;

    return {
      hasDrift,
      maxDrift,
      driftScores,
      driftedFeatures: Object.entries(driftScores)
        .filter(([_, score]) => score > this.alertThresholds.driftThreshold)
        .map(([name]) => name)
    };
  }

  generatePerformanceReport() {
    const summary = this.getPerformanceSummary();
    const calibration = this.computeCalibrationError(this.recentPredictions);
    const reliability = this.computeReliabilityDiagram(this.recentPredictions);

    let report = '='.repeat(80) + '\n';
    report += '模型性能监控报告\n';
    report += '='.repeat(80) + '\n\n';

    report += `模型版本: ${this.modelVersion}\n`;
    report += `报告时间: ${new Date().toISOString()}\n`;
    report += `状态: ${summary.status.toUpperCase()}\n\n`;

    report += '一、当前性能指标\n';
    Object.entries(summary.current).forEach(([metric, value]) => {
      report += `${metric}: ${(value * 100).toFixed(2)}%\n`;
    });
    report += '\n';

    report += '二、趋势分析\n';
    Object.entries(summary.trends).forEach(([metric, trend]) => {
      const direction = trend.direction === 'up' ? '↑' : trend.direction === 'down' ? '↓' : '→';
      report += `${metric}: ${direction} ${(trend.magnitude * 100).toFixed(2)}%\n`;
    });
    report += '\n';

    report += '三、校准误差\n';
    report += `期望校准误差(ECE): ${(calibration.ece * 100).toFixed(2)}%\n`;
    report += `最大校准误差(MCE): ${(calibration.mce * 100).toFixed(2)}%\n`;
    report += `Brier分数: ${calibration.brierScore.toFixed(4)}\n\n`;

    report += '四、可靠性图\n';
    reliability.diagram.forEach(bin => {
      report += `  ${bin.probabilityRange[0]}~${bin.probabilityRange[1]}: `;
      report += `期望=${(bin.expectedProbability * 100).toFixed(0)}%, `;
      report += `实际=${(bin.observedFrequency * 100).toFixed(0)}%, `;
      report += `样本数=${bin.count}\n`;
    });
    report += '\n';

    report += '五、最近警报\n';
    if (this.lastAlert) {
      report += `类型: ${this.lastAlert.type}\n`;
      report += `严重程度: ${this.lastAlert.severity}\n`;
      report += `消息: ${this.lastAlert.message}\n`;
      report += `时间: ${this.lastAlert.timestamp}\n`;
    } else {
      report += '暂无警报\n';
    }

    report += '='.repeat(80) + '\n';

    return report;
  }

  _computeStd(values) {
    if (values.length < 2) return 0;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (values.length - 1);
    return Math.sqrt(variance);
  }

  setModelVersion(version) {
    this.modelVersion = version;
  }

  reset() {
    this.performanceHistory = [];
    this.consecutiveErrors = 0;
    this.lastAlert = null;
    this.recentPredictions = [];
  }
}

class AutoRetrainer {
  constructor(options = {}) {
    this.retrainThreshold = options.retrainThreshold || 0.10;
    this.minSamplesBeforeRetrain = options.minSamplesBeforeRetrain || 10;
    this.maxModelsToKeep = options.maxModelsToKeep || 5;
    this.modelHistory = [];
  }

  shouldRetrain(currentMetrics, previousMetrics) {
    if (!previousMetrics) return false;

    const drops = [];
    if (currentMetrics.accuracy && previousMetrics.accuracy) {
      drops.push(previousMetrics.accuracy - currentMetrics.accuracy);
    }
    if (currentMetrics.f1 && previousMetrics.f1) {
      drops.push(previousMetrics.f1 - currentMetrics.f1);
    }

    const maxDrop = Math.max(...drops, 0);
    return maxDrop > this.retrainThreshold;
  }

  recordModel(model, metrics, timestamp = new Date()) {
    const modelRecord = {
      model,
      metrics,
      timestamp: timestamp.toISOString(),
      version: `v${this.modelHistory.length + 1}`
    };

    this.modelHistory.push(modelRecord);

    if (this.modelHistory.length > this.maxModelsToKeep) {
      this.modelHistory.shift();
    }

    return modelRecord;
  }

  getBestModel() {
    if (this.modelHistory.length === 0) return null;

    let bestModel = null;
    let bestScore = -Infinity;

    this.modelHistory.forEach(record => {
      const score = record.metrics.accuracy || 0;
      if (score > bestScore) {
        bestScore = score;
        bestModel = record;
      }
    });

    return bestModel;
  }

  getModelHistorySummary() {
    return this.modelHistory.map(record => ({
      version: record.version,
      timestamp: record.timestamp,
      accuracy: record.metrics.accuracy,
      f1: record.metrics.f1,
      sampleSize: record.metrics.sampleSize
    }));
  }
}

export {
  ModelMonitor,
  AutoRetrainer
};
