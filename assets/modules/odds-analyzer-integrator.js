import OddsAnalyzer from './odds-analyzer.js';

class OddsAnalyzerIntegrator {
  constructor() {
    this.analyzer = new OddsAnalyzer();
  }

  analyzeMatch(teamAKey, teamBKey, oddsData) {
    const result = this.analyzer.predictMatch(oddsData);
    
    return {
      analysisId: `${teamAKey}-${teamBKey}-${Date.now()}`,
      teamA: teamAKey,
      teamB: teamBKey,
      timestamp: new Date().toISOString(),
      totalGoals: result.totalGoals,
      score: result.score,
      handicap: result.handicap,
      winDrawLoss: result.winDrawLoss,
      combined: result.combinedPrediction,
      factors: this._generateFactors(result)
    };
  }

  _generateFactors(result) {
    const factors = {
      odds_trend_score: 0,
      odds_trend_total_goals: 0,
      odds_trend_handicap: 0,
      market_signal: '',
      odds_confidence: 0,
      expected_goal_diff: 0,
      predicted_scores: [],
      score_probabilities: {},
      total_goals_probabilities: {},
      handicap_advantage: 0,
      bookmaker_bias: 'none'
    };

    if (result.totalGoals.targetGoals.length > 0) {
      factors.odds_trend_total_goals = this._calculateTotalGoalsFactor(result.totalGoals);
      result.totalGoals.changes.forEach(c => {
        factors.total_goals_probabilities[c.goals] = c.percentageChange;
      });
    }

    if (result.score.targetScores.length > 0) {
      factors.odds_trend_score = this._calculateScoreFactor(result.score);
      factors.predicted_scores = result.score.targetScores.map(s => s.score);
      result.score.targetScores.forEach(s => {
        factors.score_probabilities[s.score] = s.percentageChange;
      });
    }

    if (result.handicap.prediction) {
      factors.odds_trend_handicap = this._calculateHandicapFactor(result.handicap);
      if (result.handicap.prediction === 'handicap_win') {
        factors.handicap_advantage = 1;
      } else if (result.handicap.prediction === 'handicap_lose') {
        factors.handicap_advantage = -1;
      }
    }

    if (result.winDrawLoss.prediction) {
      factors.market_signal = result.winDrawLoss.prediction;
    }

    const targetScores = result.score.targetScores;
    if (targetScores.length > 0) {
      const score = targetScores[0].score;
      const [home, away] = score.split(':').map(Number);
      factors.expected_goal_diff = home - away;
    }

    const signalCount = result.score.targetScores.length + result.totalGoals.targetGoals.length;
    const avgChange = result.score.targetScores.length > 0 
      ? result.score.targetScores.reduce((sum, s) => sum + Math.abs(s.change), 0) / result.score.targetScores.length
      : 0;
    factors.odds_confidence = Math.min(100, signalCount * 15 + avgChange * 20);

    const decreaseCount = result.handicap.decrease ? result.handicap.decrease.length : 0;
    if (decreaseCount === 1) {
      factors.bookmaker_bias = result.handicap.decrease[0].result;
    }

    return factors;
  }

  _calculateTotalGoalsFactor(totalGoalsResult) {
    const targetGoals = totalGoalsResult.targetGoals;
    if (targetGoals.length === 0) return 0;
    
    const avgGoals = targetGoals.reduce((sum, g) => sum + g, 0) / targetGoals.length;
    
    if (avgGoals >= 4) return 1;
    if (avgGoals >= 3) return 0.5;
    if (avgGoals >= 2) return 0.2;
    return -0.3;
  }

  _calculateScoreFactor(scoreResult) {
    const targetScores = scoreResult.targetScores;
    if (targetScores.length === 0) return 0;

    const homeWinCount = targetScores.filter(s => s.type === 'homeWins').length;
    const drawCount = targetScores.filter(s => s.type === 'draws').length;
    
    if (homeWinCount >= 2) return 0.8;
    if (homeWinCount === 1) return 0.3;
    if (drawCount >= 1) return -0.2;
    return -0.5;
  }

  _calculateHandicapFactor(handicapResult) {
    if (!handicapResult.prediction) return 0;
    
    if (handicapResult.prediction === 'handicap_win') return 0.6;
    if (handicapResult.prediction === 'handicap_draw') return -0.1;
    if (handicapResult.prediction === 'handicap_lose') return -0.6;
    return 0;
  }

  applyFactorsToPrediction(prediction, factors) {
    const adjusted = { ...prediction };

    if (factors.odds_trend_handicap !== 0) {
      const adjustment = factors.odds_trend_handicap * 0.05;
      if (factors.handicap_advantage === 1) {
        adjusted.winA = Math.min(0.95, Math.max(0.05, adjusted.winA + adjustment));
        adjusted.winB = Math.max(0.05, adjusted.winB - adjustment * 0.5);
      } else if (factors.handicap_advantage === -1) {
        adjusted.winB = Math.min(0.95, Math.max(0.05, adjusted.winB + adjustment));
        adjusted.winA = Math.max(0.05, adjusted.winA - adjustment * 0.5);
      }
    }

    if (factors.odds_trend_total_goals > 0) {
      const goalAdjustment = factors.odds_trend_total_goals * 0.1;
      if (adjusted.lambdaA) adjusted.lambdaA = Math.max(0.1, adjusted.lambdaA + goalAdjustment);
      if (adjusted.lambdaB) adjusted.lambdaB = Math.max(0.1, adjusted.lambdaB + goalAdjustment * 0.5);
    }

    if (factors.expected_goal_diff !== 0) {
      const diffAdjustment = factors.expected_goal_diff * 0.02;
      if (adjusted.lambdaA) adjusted.lambdaA = Math.max(0.1, adjusted.lambdaA + diffAdjustment);
      if (adjusted.lambdaB) adjusted.lambdaB = Math.max(0.1, adjusted.lambdaB - diffAdjustment * 0.3);
    }

    const total = adjusted.winA + adjusted.draw + adjusted.winB;
    if (total > 0) {
      adjusted.winA /= total;
      adjusted.draw /= total;
      adjusted.winB /= total;
    }

    adjusted.oddsFactors = factors;
    adjusted.oddsCalibrated = true;

    return adjusted;
  }

  generateAnalysisReport(analysisResult) {
    const { totalGoals, score, handicap, combined, factors } = analysisResult;
    
    return {
      summary: {
        match: `${analysisResult.teamA} vs ${analysisResult.teamB}`,
        timestamp: analysisResult.timestamp,
        predictedScore: combined.finalScore,
        handicapPrediction: handicap.prediction,
        winDrawLossPrediction: combined.winDrawLossPrediction,
        confidence: factors.odds_confidence
      },
      analysis: {
        totalGoals: {
          targetGoals: totalGoals.targetGoals,
          changes: totalGoals.changes.map(c => ({
            goals: c.goals,
            initial: c.initial,
            final: c.final,
            change: c.change,
            direction: c.direction
          })),
          analysis: totalGoals.analysis
        },
        score: {
          targetScores: score.targetScores.map(s => ({
            score: s.score,
            initial: s.initial,
            final: s.final,
            change: s.change,
            direction: s.direction
          })),
          analysis: score.analysis
        },
        handicap: {
          prediction: handicap.prediction,
          changes: handicap.changes.map(c => ({
            result: c.result,
            initial: c.initial,
            final: c.final,
            change: c.change,
            direction: c.direction
          })),
          analysis: handicap.analysis
        },
        combined: {
          finalScores: combined.finalScores,
          analysis: combined.analysis,
          finalScore: combined.finalScore
        }
      },
      factors: factors
    };
  }
}

export default OddsAnalyzerIntegrator;