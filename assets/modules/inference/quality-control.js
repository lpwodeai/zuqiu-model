/* ============================================================
 * FiveLeagues_QualityControl - 预测质量评估与告警模块
 * 从 model-engine.js 拆出，config 通过参数传入，无内部状态依赖
 * ============================================================ */
var FiveLeagues_QualityControl = (function() {

  // ─── 默认配置 ───
  var DEFAULT_CONFIG = {
    thresholds: {
      brier: { excellent: 0.20, good: 0.35, medium: 0.50, poor: 0.80 },
      accuracy: { excellent: 0.75, good: 0.65, medium: 0.55, poor: 0.45 },
      calibrationError: { excellent: 0.05, good: 0.10, medium: 0.15, poor: 0.20 },
      dataCompleteness: { excellent: 0.98, good: 0.95, medium: 0.90, poor: 0.85 }
    },
    alertRules: {
      brierConsecutive: { threshold: 0.50, consecutive: 3, severity: 'warning' },
      accuracyConsecutive: { threshold: 0.50, consecutive: 5, severity: 'critical' },
      dataCompleteness: { threshold: 0.85, severity: 'warning' },
      calibrationDrift: { threshold: 0.15, severity: 'warning' }
    }
  };

  // ─── 评估预测质量 ───
  function evaluatePredictionQuality(predictions, actualOutcomes, config) {
    if (!predictions || !actualOutcomes || predictions.length !== actualOutcomes.length) {
      return null;
    }

    config = config || DEFAULT_CONFIG;
    var brierSum = 0;
    var correct = 0;
    var calibrationErrors = [];

    for (var i = 0; i < predictions.length; i++) {
      var p = predictions[i];
      var o = actualOutcomes[i];

      brierSum += Math.pow(p.winA - o.winA, 2) + Math.pow(p.draw - o.draw, 2) + Math.pow(p.winB - o.winB, 2);

      var predMax = Math.max(p.winA, p.draw, p.winB);
      var actualMax = Math.max(o.winA, o.draw, o.winB);

      if ((p.winA === predMax && o.winA === actualMax) ||
          (p.draw === predMax && o.draw === actualMax) ||
          (p.winB === predMax && o.winB === actualMax)) {
        correct++;
      }

      if (o.winA === 1) calibrationErrors.push(p.winA - 1);
      else if (o.draw === 1) calibrationErrors.push(p.draw - 1);
      else if (o.winB === 1) calibrationErrors.push(p.winB - 1);
    }

    var brier = brierSum / predictions.length;
    var accuracy = correct / predictions.length;
    var calibrationError = calibrationErrors.length > 0
      ? calibrationErrors.reduce(function(s, e) { return s + Math.abs(e); }, 0) / calibrationErrors.length
      : 0;

    var brierLevel = brier < config.thresholds.brier.excellent ? 'excellent' :
                     brier < config.thresholds.brier.good ? 'good' :
                     brier < config.thresholds.brier.medium ? 'medium' :
                     brier < config.thresholds.brier.poor ? 'poor' : 'critical';

    var accuracyLevel = accuracy >= config.thresholds.accuracy.excellent ? 'excellent' :
                       accuracy >= config.thresholds.accuracy.good ? 'good' :
                       accuracy >= config.thresholds.accuracy.medium ? 'medium' :
                       accuracy >= config.thresholds.accuracy.poor ? 'poor' : 'critical';

    return {
      brier: brier,
      brierLevel: brierLevel,
      accuracy: accuracy,
      accuracyLevel: accuracyLevel,
      calibrationError: calibrationError,
      sampleCount: predictions.length
    };
  }

  // ─── 检查告警条件 ───
  function checkAlerts(qualityReport, config) {
    if (!qualityReport) return [];

    config = config || DEFAULT_CONFIG;
    var alerts = [];

    if (qualityReport.brier > config.alertRules.brierConsecutive.threshold) {
      alerts.push({
        type: 'brier_high',
        message: 'Brier评分过高: ' + qualityReport.brier.toFixed(3),
        severity: 'warning',
        threshold: config.alertRules.brierConsecutive.threshold,
        actual: qualityReport.brier
      });
    }

    if (qualityReport.accuracy < config.alertRules.accuracyConsecutive.threshold) {
      alerts.push({
        type: 'accuracy_low',
        message: '命中率过低: ' + (qualityReport.accuracy * 100).toFixed(1) + '%',
        severity: 'critical',
        threshold: config.alertRules.accuracyConsecutive.threshold,
        actual: qualityReport.accuracy
      });
    }

    if (qualityReport.calibrationError > config.alertRules.calibrationDrift.threshold) {
      alerts.push({
        type: 'calibration_drift',
        message: '校准误差过大: ' + qualityReport.calibrationError.toFixed(3),
        severity: 'warning',
        threshold: config.alertRules.calibrationDrift.threshold,
        actual: qualityReport.calibrationError
      });
    }

    return alerts;
  }

  return {
    DEFAULT_CONFIG: DEFAULT_CONFIG,
    evaluatePredictionQuality: evaluatePredictionQuality,
    checkAlerts: checkAlerts
  };
})();
