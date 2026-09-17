class FeatureExtractor {
  constructor() {
    this.timeWindows = {
      short: 1,
      medium: 6,
      long: 24
    };
  }

  extractAllFeatures(matchData) {
    const features = {};

    Object.assign(features, this.extractMarketSentimentFeatures(matchData));
    Object.assign(features, this.extractVolatilityFeatures(matchData));
    Object.assign(features, this.extractCrossMarketFeatures(matchData));
    Object.assign(features, this.extractTimeBasedFeatures(matchData));
    Object.assign(features, this.extractInteractionFeatures(matchData));
    Object.assign(features, this.extractNonlinearFeatures(matchData));
    Object.assign(features, this.extractDomainSpecificFeatures(matchData));

    return features;
  }

  extractMarketSentimentFeatures(matchData) {
    const features = {};

    if (matchData.wdlHistory && matchData.wdlHistory.length >= 2) {
      const wdlChanges = this._computeOddsChanges(matchData.wdlHistory);
      Object.assign(features, this._computeChangeRates(wdlChanges, 'wdl'));
      Object.assign(features, this._computeImpliedProbabilityFeatures(matchData.wdlHistory));
      Object.assign(features, this._computeKellyFeatures(matchData.wdlHistory));
    }

    if (matchData.handicapHistory && matchData.handicapHistory.length >= 2) {
      const hcpChanges = this._computeOddsChanges(matchData.handicapHistory);
      Object.assign(features, this._computeChangeRates(hcpChanges, 'hcp'));
    }

    if (matchData.totalGoalsHistory && matchData.totalGoalsHistory.length >= 2) {
      const tgChanges = this._computeTotalGoalsChanges(matchData.totalGoalsHistory);
      Object.assign(features, this._computeChangeRates(tgChanges, 'tg'));
    }

    return features;
  }

  extractVolatilityFeatures(matchData) {
    const features = {};

    if (matchData.wdlHistory && matchData.wdlHistory.length >= 3) {
      Object.assign(features, this._computeVolatilityFeatures(matchData.wdlHistory, 'wdl'));
    }

    if (matchData.handicapHistory && matchData.handicapHistory.length >= 3) {
      Object.assign(features, this._computeVolatilityFeatures(matchData.handicapHistory, 'hcp'));
    }

    if (matchData.totalGoalsHistory && matchData.totalGoalsHistory.length >= 3) {
      Object.assign(features, this._computeTotalGoalsVolatility(matchData.totalGoalsHistory));
    }

    return features;
  }

  extractCrossMarketFeatures(matchData) {
    const features = {};

    if (matchData.wdlHistory && matchData.handicapHistory) {
      features.wdl_hcp_consistency = this._computeWdlHcpConsistency(
        matchData.wdlHistory, 
        matchData.handicapHistory,
        matchData.handicap || -1
      );
    }

    if (matchData.totalGoalsHistory && matchData.scoreHistory) {
      features.tg_score_consistency = this._computeTotalGoalsScoreConsistency(
        matchData.totalGoalsHistory, 
        matchData.scoreHistory
      );
    }

    if (matchData.wdlHistory && matchData.totalGoalsHistory) {
      features.wdl_over_under_bias = this._computeWdlOverUnderBias(
        matchData.wdlHistory, 
        matchData.totalGoalsHistory
      );
    }

    return features;
  }

  extractTimeBasedFeatures(matchData) {
    const features = {};

    if (matchData.matchDate && matchData.wdlHistory && matchData.wdlHistory.length > 0) {
      const firstTimestamp = new Date(matchData.wdlHistory[0].timestamp);
      const matchDate = new Date(matchData.matchDate);
      const hoursUntilMatch = (matchDate - firstTimestamp) / (1000 * 60 * 60);
      
      features.hours_until_match = hoursUntilMatch;
      features.history_duration_hours = hoursUntilMatch;
      
      const lastTimestamp = new Date(matchData.wdlHistory[matchData.wdlHistory.length - 1].timestamp);
      features.last_update_hours_before = (matchDate - lastTimestamp) / (1000 * 60 * 60);
      
      const updatesPerHour = matchData.wdlHistory.length / hoursUntilMatch;
      features.update_frequency = updatesPerHour;
    }

    if (matchData.wdlHistory && matchData.wdlHistory.length >= 2) {
      const earlyWdl = matchData.wdlHistory[0];
      const lateWdl = matchData.wdlHistory[matchData.wdlHistory.length - 1];
      
      features.winA_late_to_early_ratio = lateWdl.winA / earlyWdl.winA;
      features.draw_late_to_early_ratio = lateWdl.draw / earlyWdl.draw;
      features.winB_late_to_early_ratio = lateWdl.winB / earlyWdl.winB;
    }

    return features;
  }

  _computeOddsChanges(history) {
    const changes = [];
    for (let i = 1; i < history.length; i++) {
      const prev = history[i - 1];
      const curr = history[i];
      const change = {};
      if (prev.winA !== undefined && curr.winA !== undefined) {
        change.winA = curr.winA - prev.winA;
        change.winA_pct = (change.winA / prev.winA) * 100;
      }
      if (prev.draw !== undefined && curr.draw !== undefined) {
        change.draw = curr.draw - prev.draw;
        change.draw_pct = (change.draw / prev.draw) * 100;
      }
      if (prev.winB !== undefined && curr.winB !== undefined) {
        change.winB = curr.winB - prev.winB;
        change.winB_pct = (change.winB / prev.winB) * 100;
      }
      if (prev.hcp_win !== undefined && curr.hcp_win !== undefined) {
        change.hcp_win = curr.hcp_win - prev.hcp_win;
        change.hcp_win_pct = (change.hcp_win / prev.hcp_win) * 100;
      }
      if (prev.hcp_draw !== undefined && curr.hcp_draw !== undefined) {
        change.hcp_draw = curr.hcp_draw - prev.hcp_draw;
        change.hcp_draw_pct = (change.hcp_draw / prev.hcp_draw) * 100;
      }
      if (prev.hcp_lose !== undefined && curr.hcp_lose !== undefined) {
        change.hcp_lose = curr.hcp_lose - prev.hcp_lose;
        change.hcp_lose_pct = (change.hcp_lose / prev.hcp_lose) * 100;
      }
      change.timestamp = curr.timestamp;
      changes.push(change);
    }
    return changes;
  }

  _computeTotalGoalsChanges(history) {
    const changes = [];
    const goalKeys = ['0', '1', '2', '3', '4', '5', '6', '7+'];
    
    for (let i = 1; i < history.length; i++) {
      const prev = history[i - 1];
      const curr = history[i];
      const change = { timestamp: curr.timestamp };
      
      goalKeys.forEach(key => {
        if (prev[key] !== undefined && curr[key] !== undefined) {
          change[key] = curr[key] - prev[key];
          change[`${key}_pct`] = (change[key] / prev[key]) * 100;
        }
      });
      changes.push(change);
    }
    return changes;
  }

  _computeChangeRates(changes, prefix) {
    const features = {};
    
    if (changes.length === 0) return features;

    const fields = [];
    if (changes[0].winA !== undefined) fields.push('winA', 'draw', 'winB');
    if (changes[0].hcp_win !== undefined) fields.push('hcp_win', 'hcp_draw', 'hcp_lose');
    if (changes[0]['0'] !== undefined) fields.push('0', '1', '2', '3', '4', '5', '6', '7+');

    fields.forEach(field => {
      const values = changes.map(c => c[field]).filter(v => v !== undefined);
      const pctValues = changes.map(c => c[`${field}_pct`]).filter(v => v !== undefined);
      
      if (values.length > 0) {
        features[`${prefix}_${field}_avg_change`] = values.reduce((a, b) => a + b, 0) / values.length;
        features[`${prefix}_${field}_max_change`] = Math.max(...values);
        features[`${prefix}_${field}_min_change`] = Math.min(...values);
        features[`${prefix}_${field}_total_change`] = values.reduce((a, b) => a + b, 0);
      }
      
      if (pctValues.length > 0) {
        features[`${prefix}_${field}_avg_pct_change`] = pctValues.reduce((a, b) => a + b, 0) / pctValues.length;
        features[`${prefix}_${field}_volatility`] = this._computeStd(pctValues);
      }
    });

    return features;
  }

  _computeImpliedProbabilityFeatures(history) {
    const features = {};
    
    const lastEntry = history[history.length - 1];
    const firstEntry = history[0];
    
    const lastImplied = this._convertToImpliedProbability(lastEntry);
    const firstImplied = this._convertToImpliedProbability(firstEntry);
    
    features.implied_winA_last = lastImplied.winA;
    features.implied_draw_last = lastImplied.draw;
    features.implied_winB_last = lastImplied.winB;
    features.implied_overround_last = lastImplied.overround;
    
    features.implied_winA_change = lastImplied.winA - firstImplied.winA;
    features.implied_draw_change = lastImplied.draw - firstImplied.draw;
    features.implied_winB_change = lastImplied.winB - firstImplied.winB;
    features.implied_overround_change = lastImplied.overround - firstImplied.overround;
    
    return features;
  }

  _convertToImpliedProbability(odds) {
    const winA = 1 / odds.winA;
    const draw = 1 / odds.draw;
    const winB = 1 / odds.winB;
    const sum = winA + draw + winB;
    
    return {
      winA: winA / sum,
      draw: draw / sum,
      winB: winB / sum,
      overround: sum
    };
  }

  _computeKellyFeatures(history) {
    const features = {};
    
    const lastEntry = history[history.length - 1];
    const implied = this._convertToImpliedProbability(lastEntry);
    
    const fairWinA = 1 / implied.winA;
    const fairDraw = 1 / implied.draw;
    const fairWinB = 1 / implied.winB;
    
    features.kelly_winA = Math.max(0, (lastEntry.winA / fairWinA) - 1);
    features.kelly_draw = Math.max(0, (lastEntry.draw / fairDraw) - 1);
    features.kelly_winB = Math.max(0, (lastEntry.winB / fairWinB) - 1);
    
    features.kelly_max = Math.max(features.kelly_winA, features.kelly_draw, features.kelly_winB);
    features.kelly_best_option = ['winA', 'draw', 'winB'][
      [features.kelly_winA, features.kelly_draw, features.kelly_winB].indexOf(features.kelly_max)
    ];
    
    return features;
  }

  _computeVolatilityFeatures(history, prefix) {
    const features = {};
    const fields = [];
    
    if (history[0].winA !== undefined) fields.push('winA', 'draw', 'winB');
    if (history[0].hcp_win !== undefined) fields.push('hcp_win', 'hcp_draw', 'hcp_lose');

    fields.forEach(field => {
      const values = history.map(h => h[field]).filter(v => v !== undefined);
      
      if (values.length >= 3) {
        const mean = values.reduce((a, b) => a + b, 0) / values.length;
        const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (values.length - 1);
        const std = Math.sqrt(variance);
        
        features[`${prefix}_${field}_std`] = std;
        features[`${prefix}_${field}_cv`] = std / mean;
        features[`${prefix}_${field}_range`] = Math.max(...values) - Math.min(...values);
        features[`${prefix}_${field}_iqr`] = this._computeIQR(values);
        
        const rollingStd = this._computeRollingStd(values, 3);
        features[`${prefix}_${field}_rolling_std`] = rollingStd[rollingStd.length - 1] || std;
      }
    });

    return features;
  }

  _computeTotalGoalsVolatility(history) {
    const features = {};
    const goalKeys = ['0', '1', '2', '3', '4', '5', '6', '7+'];

    goalKeys.forEach(key => {
      const values = history.map(h => h[key]).filter(v => v !== undefined && v !== null);
      
      if (values.length >= 3) {
        const mean = values.reduce((a, b) => a + b, 0) / values.length;
        const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (values.length - 1);
        const std = Math.sqrt(variance);
        
        features[`tg_${key}_std`] = std;
        features[`tg_${key}_cv`] = std / mean;
      }
    });

    return features;
  }

  _computeStd(values) {
    if (values.length < 2) return 0;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const variance = values.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (values.length - 1);
    return Math.sqrt(variance);
  }

  _computeRollingStd(values, window) {
    const results = [];
    for (let i = window - 1; i < values.length; i++) {
      const windowValues = values.slice(i - window + 1, i + 1);
      results.push(this._computeStd(windowValues));
    }
    return results;
  }

  _computeIQR(values) {
    const sorted = [...values].sort((a, b) => a - b);
    const q1 = sorted[Math.floor(sorted.length * 0.25)];
    const q3 = sorted[Math.floor(sorted.length * 0.75)];
    return q3 - q1;
  }

  _computeWdlHcpConsistency(wdlHistory, hcpHistory, handicap) {
    if (wdlHistory.length === 0 || hcpHistory.length === 0) return 0;

    const wdlLast = wdlHistory[wdlHistory.length - 1];
    const hcpLast = hcpHistory[hcpHistory.length - 1];

    const wdlImplied = this._convertToImpliedProbability(wdlLast);
    const hcpImplied = this._convertToImpliedProbability({
      winA: hcpLast.hcp_win || hcpLast.win,
      draw: hcpLast.hcp_draw || hcpLast.draw,
      winB: hcpLast.hcp_lose || hcpLast.lose
    });

    let consistency = 0;
    
    if (handicap < 0) {
      const homeWinConsistency = Math.abs(wdlImplied.winA - hcpImplied.winA);
      const drawConsistency = Math.abs(wdlImplied.draw - hcpImplied.draw);
      const awayWinConsistency = Math.abs(wdlImplied.winB - hcpImplied.winB);
      consistency = 1 - (homeWinConsistency + drawConsistency + awayWinConsistency) / 3;
    } else {
      const awayWinConsistency = Math.abs(wdlImplied.winB - hcpImplied.winA);
      const drawConsistency = Math.abs(wdlImplied.draw - hcpImplied.draw);
      const homeWinConsistency = Math.abs(wdlImplied.winA - hcpImplied.winB);
      consistency = 1 - (homeWinConsistency + drawConsistency + awayWinConsistency) / 3;
    }

    return Math.max(0, Math.min(1, consistency));
  }

  _computeTotalGoalsScoreConsistency(tgHistory, scoreHistory) {
    if (tgHistory.length === 0 || scoreHistory.length === 0) return 0;

    const tgLast = tgHistory[tgHistory.length - 1];
    const scoreLast = scoreHistory[scoreHistory.length - 1];

    const tgDist = this._computeTotalGoalsDistribution(tgLast);
    const scoreDist = this._computeScoreBasedDistribution(scoreLast.scores);

    let klDivergence = 0;
    for (let i = 0; i <= 7; i++) {
      const key = i === 7 ? '7+' : String(i);
      const tgProb = tgDist[key] || 0;
      const scoreProb = scoreDist[key] || 0;
      
      if (tgProb > 0 && scoreProb > 0) {
        klDivergence += tgProb * Math.log(tgProb / scoreProb);
      }
    }

    return Math.max(0, Math.min(1, 1 - klDivergence));
  }

  _computeTotalGoalsDistribution(tgEntry) {
    const goalKeys = ['0', '1', '2', '3', '4', '5', '6', '7+'];
    let sum = 0;
    
    goalKeys.forEach(key => {
      if (tgEntry[key] !== undefined && tgEntry[key] !== null) {
        sum += 1 / tgEntry[key];
      }
    });
    
    const dist = {};
    goalKeys.forEach(key => {
      if (tgEntry[key] !== undefined && tgEntry[key] !== null) {
        dist[key] = (1 / tgEntry[key]) / sum;
      } else {
        dist[key] = 0;
      }
    });
    
    return dist;
  }

  _computeScoreBasedDistribution(scores) {
    const dist = { '0': 0, '1': 0, '2': 0, '3': 0, '4': 0, '5': 0, '6': 0, '7+': 0 };
    let sum = 0;

    scores.forEach(s => {
      const [home, away] = s.score.split(':').map(Number);
      const total = home + away;
      const key = total >= 7 ? '7+' : String(total);
      dist[key] += 1 / s.odds;
      sum += 1 / s.odds;
    });

    Object.keys(dist).forEach(key => {
      dist[key] = sum > 0 ? dist[key] / sum : 0;
    });

    return dist;
  }

  _computeWdlOverUnderBias(wdlHistory, tgHistory) {
    if (wdlHistory.length === 0 || tgHistory.length === 0) return 0;

    const wdlLast = wdlHistory[wdlHistory.length - 1];
    const tgLast = tgHistory[tgHistory.length - 1];

    const wdlImplied = this._convertToImpliedProbability(wdlLast);
    
    let over25Prob = 0;
    const goalKeys = ['3', '4', '5', '6', '7+'];
    let tgSum = 0;
    goalKeys.forEach(key => {
      if (tgLast[key] !== undefined && tgLast[key] !== null) {
        tgSum += 1 / tgLast[key];
      }
    });
    goalKeys.forEach(key => {
      if (tgLast[key] !== undefined && tgLast[key] !== null) {
        over25Prob += (1 / tgLast[key]) / tgSum;
      }
    });

    const homeWinProb = wdlImplied.winA;
    const bias = homeWinProb > 0.5 ? over25Prob - 0.5 : 0.5 - over25Prob;
    
    return bias;
  }

  extractInteractionFeatures(matchData) {
    const features = {};

    if (matchData.wdlHistory && matchData.wdlHistory.length >= 2) {
      const wdlLast = matchData.wdlHistory[matchData.wdlHistory.length - 1];
      const wdlImplied = this._convertToImpliedProbability(wdlLast);
      
      features.wdl_winA_draw_interaction = wdlImplied.winA * wdlImplied.draw;
      features.wdl_winA_winB_interaction = wdlImplied.winA * wdlImplied.winB;
      features.wdl_draw_winB_interaction = wdlImplied.draw * wdlImplied.winB;
      
      const wdlChanges = this._computeOddsChanges(matchData.wdlHistory);
      if (wdlChanges.length > 0) {
        const lastChange = wdlChanges[wdlChanges.length - 1];
        features.wdl_change_interaction = (lastChange.winA || 0) * (lastChange.draw || 0);
        features.wdl_pct_change_interaction = (lastChange.winA_pct || 0) * (lastChange.draw_pct || 0);
      }
    }

    if (matchData.wdlHistory && matchData.handicapHistory && matchData.handicapHistory.length >= 2) {
      const wdlLast = matchData.wdlHistory[matchData.wdlHistory.length - 1];
      const hcpLast = matchData.handicapHistory[matchData.handicapHistory.length - 1];
      
      const wdlImplied = this._convertToImpliedProbability(wdlLast);
      const hcpImplied = this._convertToImpliedProbability({
        winA: hcpLast.hcp_win || hcpLast.win,
        draw: hcpLast.hcp_draw || hcpLast.draw,
        winB: hcpLast.hcp_lose || hcpLast.lose
      });

      features.wdl_hcp_implied_interaction = wdlImplied.winA * hcpImplied.winA;
      features.wdl_hcp_overround_interaction = wdlImplied.overround * hcpImplied.overround;
    }

    if (matchData.wdlHistory && matchData.totalGoalsHistory && matchData.totalGoalsHistory.length >= 2) {
      const wdlLast = matchData.wdlHistory[matchData.wdlHistory.length - 1];
      const tgLast = matchData.totalGoalsHistory[matchData.totalGoalsHistory.length - 1];
      
      const wdlImplied = this._convertToImpliedProbability(wdlLast);
      const tgDist = this._computeTotalGoalsDistribution(tgLast);
      
      let over25Prob = 0;
      ['3', '4', '5', '6', '7+'].forEach(key => over25Prob += tgDist[key] || 0);
      
      features.wdl_tg_interaction = wdlImplied.winA * over25Prob;
      features.wdl_draw_tg_interaction = wdlImplied.draw * over25Prob;
    }

    return features;
  }

  extractNonlinearFeatures(matchData) {
    const features = {};

    if (matchData.wdlHistory && matchData.wdlHistory.length >= 2) {
      const wdlLast = matchData.wdlHistory[matchData.wdlHistory.length - 1];
      const wdlImplied = this._convertToImpliedProbability(wdlLast);
      
      features.wdl_winA_log = Math.log(wdlLast.winA);
      features.wdl_draw_log = Math.log(wdlLast.draw);
      features.wdl_winB_log = Math.log(wdlLast.winB);
      
      features.wdl_winA_sqrt = Math.sqrt(wdlLast.winA);
      features.wdl_draw_sqrt = Math.sqrt(wdlLast.draw);
      features.wdl_winB_sqrt = Math.sqrt(wdlLast.winB);
      
      features.wdl_winA_squared = Math.pow(wdlLast.winA, 2);
      features.wdl_draw_squared = Math.pow(wdlLast.draw, 2);
      features.wdl_winB_squared = Math.pow(wdlLast.winB, 2);
      
      features.wdl_implied_winA_log = Math.log(wdlImplied.winA + 0.001);
      features.wdl_implied_draw_log = Math.log(wdlImplied.draw + 0.001);
      features.wdl_implied_winB_log = Math.log(wdlImplied.winB + 0.001);
      
      features.wdl_implied_entropy = -wdlImplied.winA * Math.log(wdlImplied.winA + 0.001) -
                                     wdlImplied.draw * Math.log(wdlImplied.draw + 0.001) -
                                     wdlImplied.winB * Math.log(wdlImplied.winB + 0.001);
      
      const giniNumerator = wdlImplied.winA * wdlImplied.draw + 
                            wdlImplied.winA * wdlImplied.winB + 
                            wdlImplied.draw * wdlImplied.winB;
      features.wdl_implied_gini = 1 - (wdlImplied.winA * wdlImplied.winA + 
                                       wdlImplied.draw * wdlImplied.draw + 
                                       wdlImplied.winB * wdlImplied.winB);
      
      features.wdl_overround_squared = Math.pow(wdlImplied.overround, 2);
      features.wdl_overround_log = Math.log(wdlImplied.overround);
    }

    if (matchData.handicapHistory && matchData.handicapHistory.length >= 2) {
      const hcpLast = matchData.handicapHistory[matchData.handicapHistory.length - 1];
      const hcpWin = hcpLast.hcp_win || hcpLast.win;
      const hcpDraw = hcpLast.hcp_draw || hcpLast.draw;
      const hcpLose = hcpLast.hcp_lose || hcpLast.lose;
      
      if (hcpWin && hcpDraw && hcpLose) {
        features.hcp_win_log = Math.log(hcpWin);
        features.hcp_draw_log = Math.log(hcpDraw);
        features.hcp_lose_log = Math.log(hcpLose);
        
        features.hcp_win_sqrt = Math.sqrt(hcpWin);
        features.hcp_draw_sqrt = Math.sqrt(hcpDraw);
        features.hcp_lose_sqrt = Math.sqrt(hcpLose);
      }
    }

    if (matchData.totalGoalsHistory && matchData.totalGoalsHistory.length >= 2) {
      const tgLast = matchData.totalGoalsHistory[matchData.totalGoalsHistory.length - 1];
      const tgDist = this._computeTotalGoalsDistribution(tgLast);
      
      let tgEntropy = 0;
      Object.values(tgDist).forEach(prob => {
        if (prob > 0) tgEntropy -= prob * Math.log(prob);
      });
      features.tg_entropy = tgEntropy;
      
      const tgMean = ['0', '1', '2', '3', '4', '5', '6', '7+'].reduce((sum, key) => {
        const goals = key === '7+' ? 7 : parseInt(key);
        return sum + goals * (tgDist[key] || 0);
      }, 0);
      features.tg_mean_squared = Math.pow(tgMean, 2);
      features.tg_mean_cubed = Math.pow(tgMean, 3);
      features.tg_mean_log = Math.log(tgMean + 1);
    }

    return features;
  }

  extractDomainSpecificFeatures(matchData) {
    const features = {};

    if (matchData.wdlHistory && matchData.wdlHistory.length >= 2) {
      const wdlLast = matchData.wdlHistory[matchData.wdlHistory.length - 1];
      const wdlFirst = matchData.wdlHistory[0];
      const wdlImplied = this._convertToImpliedProbability(wdlLast);
      
      const favoriteProb = Math.max(wdlImplied.winA, wdlImplied.winB);
      const underdogProb = Math.min(wdlImplied.winA, wdlImplied.winB);
      features.favorite_strength_index = favoriteProb - underdogProb;
      
      features.draw_compression_ratio = (wdlLast.winA + wdlLast.winB) / (2 * wdlLast.draw);
      
      features.market_efficiency_score = 1 - (wdlImplied.overround - 1);
      
      features.wdl_liquidity_score = 1 / wdlImplied.overround;
      
      const wdlChanges = this._computeOddsChanges(matchData.wdlHistory);
      if (wdlChanges.length > 0) {
        const totalAbsChange = wdlChanges.reduce((sum, c) => {
          return sum + Math.abs(c.winA || 0) + Math.abs(c.draw || 0) + Math.abs(c.winB || 0);
        }, 0);
        features.wdl_total_abs_change = totalAbsChange;
        
        const avgAbsChange = totalAbsChange / (wdlChanges.length * 3);
        features.wdl_avg_abs_change = avgAbsChange;
        
        const lastChange = wdlChanges[wdlChanges.length - 1];
        const recentMomentum = (lastChange.winA_pct || 0) + (lastChange.draw_pct || 0) + (lastChange.winB_pct || 0);
        features.wdl_recent_momentum = recentMomentum;
        
        const trendScore = wdlChanges.reduce((sum, c, i) => {
          const weight = (i + 1) / wdlChanges.length;
          return sum + weight * (c.winA_pct || 0);
        }, 0);
        features.wdl_weighted_trend = trendScore;
      }
      
      features.winA_late_to_early_ratio_squared = Math.pow(wdlLast.winA / wdlFirst.winA, 2);
      features.winB_late_to_early_ratio_squared = Math.pow(wdlLast.winB / wdlFirst.winB, 2);
      
      const oddsRange = Math.max(wdlLast.winA, wdlLast.winB, wdlLast.draw) - 
                        Math.min(wdlLast.winA, wdlLast.winB, wdlLast.draw);
      features.wdl_odds_range = oddsRange;
      
      features.wdl_odds_spread = (wdlLast.winA - wdlLast.winB);
      
      const wdlSum = wdlLast.winA + wdlLast.draw + wdlLast.winB;
      features.wdl_odds_sum = wdlSum;
      features.wdl_odds_mean = wdlSum / 3;
      features.wdl_odds_std = Math.sqrt(
        ((wdlLast.winA - wdlSum/3)**2 + (wdlLast.draw - wdlSum/3)**2 + (wdlLast.winB - wdlSum/3)**2) / 3
      );
    }

    if (matchData.handicapHistory && matchData.handicapHistory.length >= 2) {
      const hcpLast = matchData.handicapHistory[matchData.handicapHistory.length - 1];
      const hcpWin = hcpLast.hcp_win || hcpLast.win;
      const hcpDraw = hcpLast.hcp_draw || hcpLast.draw;
      const hcpLose = hcpLast.hcp_lose || hcpLast.lose;
      
      if (hcpWin && hcpDraw && hcpLose) {
        const hcpImplied = this._convertToImpliedProbability({
          winA: hcpWin, draw: hcpDraw, winB: hcpLose
        });
        
        features.hcp_overround = hcpImplied.overround;
        
        const hcpRange = Math.max(hcpWin, hcpDraw, hcpLose) - Math.min(hcpWin, hcpDraw, hcpLose);
        features.hcp_odds_range = hcpRange;
        
        features.hcp_odds_spread = hcpWin - hcpLose;
      }
    }

    if (matchData.totalGoalsHistory && matchData.totalGoalsHistory.length >= 2) {
      const tgLast = matchData.totalGoalsHistory[matchData.totalGoalsHistory.length - 1];
      const tgDist = this._computeTotalGoalsDistribution(tgLast);
      
      let tgMean = 0;
      let tgVariance = 0;
      ['0', '1', '2', '3', '4', '5', '6', '7+'].forEach(key => {
        const goals = key === '7+' ? 7 : parseInt(key);
        tgMean += goals * (tgDist[key] || 0);
      });
      ['0', '1', '2', '3', '4', '5', '6', '7+'].forEach(key => {
        const goals = key === '7+' ? 7 : parseInt(key);
        tgVariance += Math.pow(goals - tgMean, 2) * (tgDist[key] || 0);
      });
      
      features.tg_variance = tgVariance;
      features.tg_std = Math.sqrt(tgVariance);
      
      const overOdds = tgLast['0'] && tgLast['1'] && tgLast['2'] ? 
        1 / (1/tgLast['0'] + 1/tgLast['1'] + 1/tgLast['2']) : null;
      const underOdds = tgLast['3'] && tgLast['4'] && tgLast['5'] && tgLast['6'] && tgLast['7+'] ?
        1 / (1/tgLast['3'] + 1/tgLast['4'] + 1/tgLast['5'] + 1/tgLast['6'] + 1/tgLast['7+']) : null;
      
      if (overOdds && underOdds) {
        features.tg_over_under_ratio = overOdds / underOdds;
        features.tg_over_under_spread = overOdds - underOdds;
      }
      
      const highProbKey = Object.keys(tgDist).reduce((a, b) => tgDist[a] > tgDist[b] ? a : b);
      features.tg_most_likely_goals = parseInt(highProbKey === '7+' ? '7' : highProbKey);
      
      const secondHighProbKey = Object.keys(tgDist).reduce((a, b) => {
        if (a === highProbKey) return b;
        if (b === highProbKey) return a;
        return tgDist[a] > tgDist[b] ? a : b;
      });
      features.tg_second_most_likely = parseInt(secondHighProbKey === '7+' ? '7' : secondHighProbKey);
      
      const probDiff = tgDist[highProbKey] - tgDist[secondHighProbKey];
      features.tg_mode_confidence = probDiff;
      
      let skewness = 0;
      ['0', '1', '2', '3', '4', '5', '6', '7+'].forEach(key => {
        const goals = key === '7+' ? 7 : parseInt(key);
        skewness += Math.pow(goals - tgMean, 3) * (tgDist[key] || 0);
      });
      features.tg_skewness = tgVariance > 0 ? skewness / Math.pow(tgVariance, 1.5) : 0;
      
      let kurtosis = 0;
      ['0', '1', '2', '3', '4', '5', '6', '7+'].forEach(key => {
        const goals = key === '7+' ? 7 : parseInt(key);
        kurtosis += Math.pow(goals - tgMean, 4) * (tgDist[key] || 0);
      });
      features.tg_kurtosis = tgVariance > 0 ? kurtosis / Math.pow(tgVariance, 2) : 0;
    }

    if (matchData.scoreHistory && matchData.scoreHistory.length > 0) {
      const scoreLast = matchData.scoreHistory[matchData.scoreHistory.length - 1];
      
      let maxScoreProb = 0;
      let mostLikelyScore = '';
      scoreLast.scores.forEach(s => {
        const prob = 1 / s.odds;
        if (prob > maxScoreProb) {
          maxScoreProb = prob;
          mostLikelyScore = s.score;
        }
      });
      
      features.score_mode_prob = maxScoreProb;
      
      const [home, away] = mostLikelyScore.split(':').map(Number);
      features.score_mode_home = home || 0;
      features.score_mode_away = away || 0;
      
      let sumProb = 0;
      scoreLast.scores.forEach(s => sumProb += 1 / s.odds);
      features.score_market_overround = sumProb;
      
      let scoreEntropy = 0;
      scoreLast.scores.forEach(s => {
        const prob = (1 / s.odds) / sumProb;
        if (prob > 0) scoreEntropy -= prob * Math.log(prob);
      });
      features.score_entropy = scoreEntropy;
      
      let highScoreProb = 0;
      scoreLast.scores.forEach(s => {
        const [h, a] = s.score.split(':').map(Number);
        if (h + a >= 3) {
          highScoreProb += 1 / s.odds;
        }
      });
      features.score_high_prob = highScoreProb / sumProb;
      
      let drawScoreProb = 0;
      scoreLast.scores.forEach(s => {
        const [h, a] = s.score.split(':').map(Number);
        if (h === a) {
          drawScoreProb += 1 / s.odds;
        }
      });
      features.score_draw_prob = drawScoreProb / sumProb;
      
      let homeWinScoreProb = 0;
      scoreLast.scores.forEach(s => {
        const [h, a] = s.score.split(':').map(Number);
        if (h > a) {
          homeWinScoreProb += 1 / s.odds;
        }
      });
      features.score_home_win_prob = homeWinScoreProb / sumProb;
      
      let awayWinScoreProb = 0;
      scoreLast.scores.forEach(s => {
        const [h, a] = s.score.split(':').map(Number);
        if (h < a) {
          awayWinScoreProb += 1 / s.odds;
        }
      });
      features.score_away_win_prob = awayWinScoreProb / sumProb;
    }

    if (matchData.matchType) {
      features.match_type_group = matchData.matchType === 'group' ? 1 : 0;
      features.match_type_knockout = matchData.matchType === 'knockout' ? 1 : 0;
    }

    return features;
  }
}

export default FeatureExtractor;
