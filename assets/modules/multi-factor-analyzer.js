import OddsAnalyzer from './odds-analyzer.js';

class MultiFactorAnalyzer {
  constructor() {
    this.oddsAnalyzer = new OddsAnalyzer();
    this.factorWeights = {
      oddsChange: 0.45,
      teamStrength: 0.20,
      recentForm: 0.15,
      headToHead: 0.10,
      homeAdvantage: 0.10
    };
    this.maxFactorScore = 100;
  }

  setFactorWeights(weights) {
    this.factorWeights = { ...this.factorWeights, ...weights };
    const total = Object.values(this.factorWeights).reduce((a, b) => a + b, 0);
    Object.keys(this.factorWeights).forEach(key => {
      this.factorWeights[key] /= total;
    });
  }

  normalizeValue(value, min, max) {
    if (max === min) return 0.5;
    return Math.min(1, Math.max(0, (value - min) / (max - min)));
  }

  analyzeTeamStrength(factorData) {
    const homeStrength = factorData.homeTeamStrength || 0;
    const awayStrength = factorData.awayTeamStrength || 0;
    const strengthDiff = homeStrength - awayStrength;
    const normalizedDiff = this.normalizeValue(strengthDiff, -100, 100);
    
    return {
      score: normalizedDiff * this.maxFactorScore,
      normalizedScore: normalizedDiff,
      homeStrength,
      awayStrength,
      strengthDiff,
      analysis: strengthDiff > 10 ? '主队实力明显占优' : strengthDiff < -10 ? '客队实力明显占优' : '两队实力相当'
    };
  }

  analyzeRecentForm(factorData) {
    const homeRecent = factorData.homeRecentForm || { wins: 0, draws: 0, losses: 0 };
    const awayRecent = factorData.awayRecentForm || { wins: 0, draws: 0, losses: 0 };
    
    const homeMatches = homeRecent.wins + homeRecent.draws + homeRecent.losses;
    const awayMatches = awayRecent.wins + awayRecent.draws + awayRecent.losses;
    
    const homeFormScore = homeMatches > 0 ? (homeRecent.wins * 3 + homeRecent.draws) / (homeMatches * 3) : 0.5;
    const awayFormScore = awayMatches > 0 ? (awayRecent.wins * 3 + awayRecent.draws) / (awayMatches * 3) : 0.5;
    
    const formDiff = homeFormScore - awayFormScore;
    const normalizedDiff = this.normalizeValue(formDiff, -1, 1);
    
    return {
      score: normalizedDiff * this.maxFactorScore,
      normalizedScore: normalizedDiff,
      homeFormScore: (homeFormScore * 100).toFixed(1),
      awayFormScore: (awayFormScore * 100).toFixed(1),
      formDiff: (formDiff * 100).toFixed(1),
      analysis: formDiff > 0.15 ? '主队近期状态更好' : formDiff < -0.15 ? '客队近期状态更好' : '两队近期状态相当'
    };
  }

  analyzeHeadToHead(factorData) {
    const h2h = factorData.headToHead || { homeWins: 0, draws: 0, awayWins: 0 };
    const total = h2h.homeWins + h2h.draws + h2h.awayWins;
    
    if (total === 0) {
      return {
        score: 50,
        normalizedScore: 0.5,
        analysis: '无历史交锋记录'
      };
    }
    
    const homeWinRate = h2h.homeWins / total;
    const awayWinRate = h2h.awayWins / total;
    const diff = homeWinRate - awayWinRate;
    const normalizedDiff = this.normalizeValue(diff, -1, 1);
    
    return {
      score: (0.5 + normalizedDiff * 0.5) * this.maxFactorScore,
      normalizedScore: 0.5 + normalizedDiff * 0.5,
      homeWinRate: (homeWinRate * 100).toFixed(1),
      awayWinRate: (awayWinRate * 100).toFixed(1),
      analysis: diff > 0.2 ? '历史交锋主队占优' : diff < -0.2 ? '历史交锋客队占优' : '历史交锋战绩相当'
    };
  }

  analyzeHomeAdvantage(factorData) {
    const homeAdvantage = factorData.homeAdvantage || 0;
    const normalized = this.normalizeValue(homeAdvantage, -50, 50);
    
    return {
      score: normalized * this.maxFactorScore,
      normalizedScore: normalized,
      value: homeAdvantage,
      analysis: homeAdvantage > 10 ? '主队拥有主场优势' : homeAdvantage < -10 ? '主队主场表现不佳' : '主客场因素影响不大'
    };
  }

  analyzeOddsChange(oddsData) {
    const result = this.oddsAnalyzer.predictMatch(oddsData);
    
    let oddsScore = 0;
    const hcPred = result.handicap.prediction;
    
    if (hcPred === 'handicap_win') {
      oddsScore = 0.85;
    } else if (hcPred === 'handicap_draw') {
      oddsScore = 0.5;
    } else {
      oddsScore = 0.15;
    }
    
    return {
      score: oddsScore * this.maxFactorScore,
      normalizedScore: oddsScore,
      prediction: result,
      handicapPrediction: hcPred,
      analysis: result.handicap.analysis
    };
  }

  analyze(factorData, oddsData) {
    const oddsResult = this.analyzeOddsChange(oddsData);
    const strengthResult = this.analyzeTeamStrength(factorData);
    const formResult = this.analyzeRecentForm(factorData);
    const h2hResult = this.analyzeHeadToHead(factorData);
    const homeAdvResult = this.analyzeHomeAdvantage(factorData);

    const otherFactors = [
      { score: strengthResult.normalizedScore, weight: this.factorWeights.teamStrength },
      { score: formResult.normalizedScore, weight: this.factorWeights.recentForm },
      { score: h2hResult.normalizedScore, weight: this.factorWeights.headToHead },
      { score: homeAdvResult.normalizedScore, weight: this.factorWeights.homeAdvantage }
    ];

    const otherScore = otherFactors.reduce((sum, f) => sum + f.score * f.weight, 0);
    const otherWeight = otherFactors.reduce((sum, f) => sum + f.weight, 0);
    const normalizedOtherScore = otherScore / otherWeight;

    let compositeScore = oddsResult.normalizedScore;
    
    const oddsDirection = oddsResult.normalizedScore > 0.6 ? 'win' : oddsResult.normalizedScore < 0.4 ? 'lose' : 'draw';
    const otherDirection = normalizedOtherScore > 0.6 ? 'win' : normalizedOtherScore < 0.4 ? 'lose' : 'draw';
    
    if (oddsDirection === otherDirection) {
      compositeScore = oddsResult.normalizedScore * 0.7 + normalizedOtherScore * 0.3;
    } else if (oddsDirection === 'draw' && otherDirection !== 'draw') {
      compositeScore = normalizedOtherScore;
    } else if (otherDirection === 'draw') {
      compositeScore = oddsResult.normalizedScore;
    } else {
      const oddsConfidence = Math.abs(oddsResult.normalizedScore - 0.5) * 2;
      const otherConfidence = Math.abs(normalizedOtherScore - 0.5) * 2;
      if (oddsConfidence > otherConfidence * 1.5) {
        compositeScore = oddsResult.normalizedScore;
      } else if (otherConfidence > oddsConfidence * 1.5) {
        compositeScore = normalizedOtherScore;
      } else {
        compositeScore = 0.5;
      }
    }

    let prediction = 'handicap_lose';
    if (compositeScore > 0.55) {
      prediction = 'handicap_win';
    } else if (compositeScore > 0.45) {
      prediction = 'handicap_draw';
    }

    const factorBreakdown = [
      { name: '赔率变化', weight: this.factorWeights.oddsChange, score: oddsResult.normalizedScore, analysis: oddsResult.analysis },
      { name: '球队实力', weight: this.factorWeights.teamStrength, score: strengthResult.normalizedScore, analysis: strengthResult.analysis },
      { name: '近期状态', weight: this.factorWeights.recentForm, score: formResult.normalizedScore, analysis: formResult.analysis },
      { name: '历史交锋', weight: this.factorWeights.headToHead, score: h2hResult.normalizedScore, analysis: h2hResult.analysis },
      { name: '主场优势', weight: this.factorWeights.homeAdvantage, score: homeAdvResult.normalizedScore, analysis: homeAdvResult.analysis }
    ];

    return {
      compositeScore: compositeScore,
      prediction: prediction,
      factorBreakdown: factorBreakdown,
      oddsAnalysis: oddsResult,
      strengthAnalysis: strengthResult,
      formAnalysis: formResult,
      h2hAnalysis: h2hResult,
      homeAdvAnalysis: homeAdvResult,
      crossValidation: this._crossValidate(factorBreakdown),
      confidence: this._calculateConfidence(factorBreakdown)
    };
  }

  _crossValidate(factorBreakdown) {
    const winningFactors = factorBreakdown.filter(f => f.score > 0.6);
    const losingFactors = factorBreakdown.filter(f => f.score < 0.4);
    
    if (winningFactors.length >= 3 && losingFactors.length === 0) {
      return { status: 'strong_confirm', message: '多因子交叉验证通过，预测结论高度一致' };
    } else if (winningFactors.length >= 2 && losingFactors.length <= 1) {
      return { status: 'moderate_confirm', message: '多因子交叉验证基本通过，部分因子支持预测结论' };
    } else if (winningFactors.length === losingFactors.length) {
      return { status: 'conflict', message: '多因子存在冲突，建议谨慎决策' };
    } else {
      return { status: 'weak', message: '因子支持度不足，建议结合更多信息' };
    }
  }

  _calculateConfidence(factorBreakdown) {
    const scores = factorBreakdown.map(f => f.score);
    const avgScore = scores.reduce((a, b) => a + b, 0) / scores.length;
    const variance = scores.reduce((sum, s) => sum + Math.pow(s - avgScore, 2), 0) / scores.length;
    const consistency = 1 - Math.sqrt(variance);
    
    return (avgScore * 0.6 + consistency * 0.4) * 100;
  }
}

export default MultiFactorAnalyzer;