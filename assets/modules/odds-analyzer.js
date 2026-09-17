class OddsAnalyzer {
  constructor() {
    this.minChangeThreshold = 0.1;
    this.significantChangeThreshold = 0.5;
  }

  analyzeTotalGoalsOdds(oddsHistory, awayWinOddsLow = false, handicap = null, winDrawLossHistory = null) {
    if (!oddsHistory || oddsHistory.length < 2) {
      return { status: 'insufficient_data', targets: [], targetGoals: [] };
    }

    const first = oddsHistory[0];
    const last = oddsHistory[oddsHistory.length - 1];
    
    if (!first || !last) {
      return { status: 'insufficient_data', targets: [], targetGoals: [] };
    }
    
    let homeWinOddsDecreasing = false;
    if (winDrawLossHistory && winDrawLossHistory.length >= 2) {
      const wdlFirst = winDrawLossHistory[0].winA;
      const wdlLast = winDrawLossHistory[winDrawLossHistory.length - 1].winA;
      if (wdlFirst > wdlLast) {
        homeWinOddsDecreasing = true;
      }
    }
    
    const changes = [];
    Object.keys(first).forEach(key => {
      if (key !== 'time' && typeof first[key] === 'number' && typeof last[key] === 'number') {
        const change = first[key] - last[key];
        const percentageChange = (change / first[key]) * 100;
        
        const vPattern = this._detectVPatternForTotalGoals(oddsHistory, key);
        
        let priorityScore = 0;
        const oddsLevel = first[key];
        const goals = key === '7+' ? 7 : parseInt(key);
        
        if (change > 0) {
          priorityScore = (change / oddsLevel) * (10 / oddsLevel) + (10 / oddsLevel);
          if (vPattern && vPattern.type === 'reverse') {
            priorityScore *= 1.3;
          }
        } else if (homeWinOddsDecreasing && handicap && parseInt(handicap) === -1 && goals <= 2 && Math.abs(change) < 0.3 && oddsLevel < 6) {
          priorityScore = 10 / oddsLevel * 0.7;
        } else if (Math.abs(change) < 0.2) {
          priorityScore = 10 / oddsLevel * 0.5 + (0.1 / oddsLevel);
        } else if (awayWinOddsLow && goals <= 2 && change < 0 && Math.abs(change) > 0.5) {
          priorityScore = 10 / oddsLevel * 1.2;
        } else if (awayWinOddsLow && goals === 1 && change < 0 && Math.abs(change) < 0.5 && oddsLevel < 6) {
          priorityScore = 10 / oddsLevel * 0.9;
        } else if (handicap && parseInt(handicap) === -2 && goals >= 3 && change < 0 && oddsLevel < 8) {
          priorityScore = 10 / oddsLevel * 0.6;
        } else if (homeWinOddsDecreasing && handicap && parseInt(handicap) === -1 && goals <= 2 && change < 0 && Math.abs(change) < 1.0 && oddsLevel < 6) {
          priorityScore = 10 / oddsLevel * 0.8;
        } else if (awayWinOddsLow && handicap && parseInt(handicap) >= 2 && goals <= 2 && change < 0 && Math.abs(change) < 0.5 && oddsLevel < 8) {
          priorityScore = 10 / oddsLevel * 1.1;
        } else if (awayWinOddsLow && handicap && parseInt(handicap) >= 2 && goals === 1 && change >= 0 && Math.abs(change) < 1.0 && oddsLevel < 8) {
          priorityScore = 10 / oddsLevel * 1.3;
        } else if (awayWinOddsLow && handicap && parseInt(handicap) >= 2 && goals === 1 && change < 0 && Math.abs(change) < 0.5 && oddsLevel < 8) {
          priorityScore = 10 / oddsLevel * 1.2;
        }
        
        changes.push({
          goals: goals,
          initial: first[key],
          final: last[key],
          change: change,
          percentageChange: percentageChange,
          priorityScore: priorityScore,
          direction: change > 0 ? 'decrease' : change < 0 ? 'increase' : 'stable',
          vPattern: vPattern
        });
      }
    });

    const lowGoalsDecrease = changes.some(c => c.goals <= 3 && c.change > 0.2);
    const highGoalsDecrease = changes.some(c => c.goals >= 4 && c.change > 0.3);
    const highGoalsBigDrop = changes.some(c => c.goals >= 5 && c.change > 0.5);
    const allLowGoalsIncrease = changes.filter(c => c.goals <= 3).every(c => c.change < 0);
    const allHighGoalsDecrease = changes.filter(c => c.goals >= 5).every(c => c.change > 0);
    const mostLowGoalsIncrease = changes.filter(c => c.goals <= 2).every(c => c.change < 0);
    changes.forEach(c => {
      if (c.goals >= 4 && c.change < 0 && Math.abs(c.change) < 0.5 && c.priorityScore === 0) {
        if (lowGoalsDecrease && c.initial < 15) {
          c.priorityScore = 10 / c.initial * 0.6;
        }
      }
      if (c.goals <= 2 && c.change < 0 && Math.abs(c.change) > 0.15 && c.priorityScore === 0) {
        if (highGoalsDecrease && c.initial < 8) {
          c.priorityScore = 10 / c.initial * 0.7;
        }
      }
      if (c.goals >= 5 && c.change > 0.5 && c.priorityScore === 0) {
        c.priorityScore = 10 / c.initial * 0.8;
      }
      if (c.goals >= 5 && c.change > 1.0 && c.priorityScore === 0) {
        c.priorityScore = 10 / c.initial * 1.1;
      }
      if (c.goals >= 5 && c.change > 1.0 && mostLowGoalsIncrease && c.priorityScore > 0) {
        c.priorityScore *= 1.5;
      }
      if (c.goals === 4 && highGoalsBigDrop && c.priorityScore === 0) {
        c.priorityScore = 10 / c.initial * 0.6;
      }
      if (c.goals === 4 && allLowGoalsIncrease && allHighGoalsDecrease && c.priorityScore === 0) {
        c.priorityScore = 10 / c.initial * 0.9;
      }
      if (handicap && Math.abs(parseInt(handicap)) >= 3 && c.goals === Math.abs(parseInt(handicap)) && allLowGoalsIncrease && allHighGoalsDecrease && c.priorityScore === 0) {
        c.priorityScore = 10 / c.initial * 0.85;
      }
      if (c.goals <= 2 && allLowGoalsIncrease && allHighGoalsDecrease && c.priorityScore > 0) {
        c.priorityScore *= 0.3;
      }
      if (c.goals <= 3 && mostLowGoalsIncrease && c.change < 0.1 && c.priorityScore > 0) {
        c.priorityScore *= 0.5;
      }
      if (c.goals <= 2 && c.change < 0 && Math.abs(c.change) > 0.15 && c.priorityScore === 0) {
        if (handicap && parseInt(handicap) > 0 && winDrawLossHistory && winDrawLossHistory.length >= 2) {
          const lastWinB = winDrawLossHistory[winDrawLossHistory.length - 1].winB;
          if (lastWinB < 1.50) {
            c.priorityScore = 10 / c.initial * 0.6;
          }
        }
      }
      const isStrongFavorite = winDrawLossHistory && winDrawLossHistory.length > 0 && winDrawLossHistory[0].winA < 1.5;
      if (homeWinOddsDecreasing && handicap && parseInt(handicap) === -1 && c.goals >= 4 && c.priorityScore > 0 && isStrongFavorite) {
        c.priorityScore *= 0.5;
      }
    });

    changes.sort((a, b) => {
      const highGoalBonusA = a.goals >= 5 && a.change > 1.0 ? 2.5 : 1;
      const highGoalBonusB = b.goals >= 5 && b.change > 1.0 ? 2.5 : 1;
      return (b.priorityScore * highGoalBonusB) - (a.priorityScore * highGoalBonusA);
    });

    const targetGoals = changes
      .filter(c => c.priorityScore > 0.3)
      .slice(0, 4)
      .map(c => c.goals);

    return {
      status: 'success',
      changes: changes,
      topDecreases: changes.slice(0, 3),
      targetGoals: targetGoals,
      targets: targetGoals,
      initialOdds: first,
      finalOdds: last,
      analysis: this._generateTotalGoalsAnalysis(changes, targetGoals)
    };
  }

  _detectVPatternForTotalGoals(oddsHistory, key) {
    if (oddsHistory.length < 3) return null;
    
    const values = oddsHistory.map(o => o[key]);
    if (values.some(v => typeof v !== 'number')) return null;
    
    const first = values[0];
    const last = values[values.length - 1];
    
    const maxIndex = values.indexOf(Math.max(...values));
    const minIndex = values.indexOf(Math.min(...values));
    
    if (minIndex > 0 && minIndex < values.length - 1) {
      const beforeMin = values[minIndex - 1];
      const afterMin = values[minIndex + 1];
      
      if (beforeMin > values[minIndex] && afterMin > values[minIndex]) {
        const recovery = last - values[minIndex];
        if (recovery > 0) {
          return {
            type: 'reverse',
            description: `${key}球赔率出现倒V型反转`,
            minIndex: minIndex,
            minValue: values[minIndex],
            recovery: recovery
          };
        }
      }
    }
    
    if (maxIndex > 0 && maxIndex < values.length - 1) {
      const beforeMax = values[maxIndex - 1];
      const afterMax = values[maxIndex + 1];
      
      if (beforeMax < values[maxIndex] && afterMax < values[maxIndex]) {
        return {
          type: 'v_shape',
          description: `${key}球赔率出现V型走势`,
          maxIndex: maxIndex,
          maxValue: values[maxIndex]
        };
      }
    }
    
    return null;
  }

  _generateTotalGoalsAnalysis(changes, targetGoals) {
    if (targetGoals.length === 0) {
      return '总进球数赔率变化不明显，无法确定进球数方向';
    }
    if (targetGoals.length === 1) {
      return `总进球数赔率看，大概率进球总数为 ${targetGoals[0]} 球`;
    }
    return `总进球数赔率看，大概率进球总数为 ${targetGoals.join('球、')}球`;
  }

  analyzeScoreOdds(oddsHistory, targetGoals, awayWinOddsLow = false, handicapPrediction = null, handicap = null) {
    if (!oddsHistory || oddsHistory.length < 2) {
      return { status: 'insufficient_data', targets: [], targetScores: [], homeWinScores: [], allChanges: [], targetGoals: [] };
    }

    targetGoals = targetGoals || [];

    const first = oddsHistory[0];
    const last = oddsHistory[oddsHistory.length - 1];

    const scoreTypes = ['homeWins', 'draws', 'awayWins'];
    const allChanges = [];

    scoreTypes.forEach(type => {
      if (first[type] && last[type] && Array.isArray(first[type]) && Array.isArray(last[type])) {
        first[type].forEach(scoreEntry => {
          const lastEntry = last[type].find(s => s.score === scoreEntry.score);
          if (lastEntry && !scoreEntry.score.includes('其它')) {
            const change = scoreEntry.odds - lastEntry.odds;
            const percentageChange = (change / scoreEntry.odds) * 100;
            const [home, away] = scoreEntry.score.split(':').map(Number);
            const total = home + away;
            const isTarget = targetGoals.length === 0 || targetGoals.includes(total);
            
            const trendResult = this._calculateScoreTrend(oddsHistory, type, scoreEntry.score);
            const timeWeightedChange = trendResult.timeWeightedChange;
            const consecutiveDecreases = trendResult.consecutiveDecreases;
            const trendStrength = trendResult.trendStrength;
            
            let priorityScore = 0;
            const oddsLevel = scoreEntry.odds;
            
            if (change > 0) {
              priorityScore = (change / oddsLevel) * (10 / oddsLevel) + (10 / oddsLevel);
              priorityScore += consecutiveDecreases * 0.15;
              priorityScore *= (1 + trendStrength * 0.2);
            } else if (change === 0) {
              priorityScore = 10 / oddsLevel;
            } else if (awayWinOddsLow && type === 'awayWins' && total <= 2 && change < 0) {
              priorityScore = 10 / oddsLevel * 0.7;
            } else if (handicapPrediction === 'handicap_win' && type === 'homeWins' && total <= 3 && change < 0 && Math.abs(change) < 0.5) {
              priorityScore = 10 / oddsLevel * 0.5;
            } else if (handicapPrediction === 'handicap_win' && type === 'homeWins' && total <= 4 && Math.abs(change) > 0) {
              priorityScore = 10 / oddsLevel * 0.6;
            } else if (handicapPrediction === 'handicap_win' && type === 'homeWins' && home - away >= 2 && Math.abs(change) < 1.0 && oddsLevel < 20) {
              priorityScore = 10 / oddsLevel * 0.8;
            } else if (handicapPrediction === 'handicap_draw' && change < 0 && Math.abs(change) < 1.5 && oddsLevel < 15) {
              priorityScore = 10 / oddsLevel * 0.5;
            } else if (handicapPrediction === 'handicap_draw' && change > 0 && Math.abs(change) > 3 && oddsLevel > 15 && total >= 4) {
              priorityScore = 10 / oddsLevel * 0.9;
            } else if (handicapPrediction === 'handicap_draw' && type === 'awayWins' && handicap && parseInt(handicap) >= 2 && away - home === parseInt(handicap)) {
              priorityScore = 10 / oddsLevel * 0.7;
            } else if (handicapPrediction === 'handicap_draw' && type === 'homeWins' && handicap && parseInt(handicap) === -1 && home - away === 1) {
              priorityScore = 10 / oddsLevel * 1.2;
            } else if (handicapPrediction === 'handicap_draw' && type === 'awayWins' && handicap && parseInt(handicap) === 1 && away - home === 1) {
              priorityScore = 10 / oddsLevel * 1.2;
            } else if (handicapPrediction === 'handicap_lose' && type === 'awayWins' && away - home >= 2) {
              if (change > 0) {
                priorityScore = (change / oddsLevel) * (10 / oddsLevel) + (10 / oddsLevel) * 0.8;
              } else {
                priorityScore = 10 / oddsLevel + (0.2 / oddsLevel);
              }
            } else if (handicapPrediction === 'handicap_lose' && type === 'awayWins' && handicap && parseInt(handicap) === -1 && away - home === 1) {
              priorityScore = 10 / oddsLevel * 1.1;
            } else if (handicapPrediction === 'handicap_lose' && type === 'draws') {
              if (change > 0 && Math.abs(change) < 2.0 && oddsLevel < 15) {
                priorityScore = 10 / oddsLevel * 0.6;
              } else if (change <= 0 && Math.abs(change) < 1.0 && oddsLevel < 12) {
                priorityScore = 10 / oddsLevel * 0.4;
              }
            } else if (handicapPrediction === 'handicap_win' && type === 'awayWins' && handicap && parseInt(handicap) >= 2 && away === 1 && home === 0) {
              priorityScore = 10 / oddsLevel * 1.5;
            } else if (handicapPrediction === 'handicap_win' && type === 'awayWins' && handicap && parseInt(handicap) >= 2 && away - home === 1) {
              priorityScore = 10 / oddsLevel * 1.2;
            }

            allChanges.push({
              score: scoreEntry.score,
              type: type,
              totalGoals: total,
              initial: scoreEntry.odds,
              final: lastEntry.odds,
              change: change,
              percentageChange: percentageChange,
              priorityScore: priorityScore,
              direction: change > 0 ? 'decrease' : change < 0 ? 'increase' : 'stable',
              isTarget: isTarget,
              consecutiveDecreases: consecutiveDecreases,
              trendStrength: trendStrength,
              timeWeightedChange: timeWeightedChange
            });
          }
        });
      }
    });

    allChanges.sort((a, b) => b.priorityScore - a.priorityScore);

    const targetScores = allChanges
      .filter(c => {
        if (c.priorityScore > 0.5 && c.isTarget) return true;
        if (handicapPrediction === 'handicap_win' && handicap && parseInt(handicap) >= 2 && c.priorityScore > 0.5 && c.type === 'awayWins') {
          const [home, away] = c.score.split(':').map(Number);
          if (away - home === 1) return true;
        }
        return false;
      })
      .slice(0, 5);

    if (targetScores.length === 0 && targetGoals.length > 0) {
      const stableTargetScores = allChanges
        .filter(c => c.direction === 'stable' && c.isTarget && c.priorityScore > 0.5)
        .slice(0, 3);
      targetScores.push(...stableTargetScores);
    }

    if (targetGoals.length > 0 && targetScores.length > 0) {
      const drawScores = allChanges.filter(c => {
        const [home, away] = c.score.split(':').map(Number);
        return home === away && c.isTarget && !c.score.includes('其它');
      });
      drawScores.forEach(drawScore => {
        if (!targetScores.find(s => s.score === drawScore.score)) {
          targetScores.push(drawScore);
        }
      });
    }

    if (handicapPrediction === 'handicap_draw' && targetGoals.length > 0 && handicap) {
      const hcpNum = parseInt(handicap);
      const drawMatchScores = allChanges.filter(c => {
        const [home, away] = c.score.split(':').map(Number);
        const goalDiff = home - away;
        if (hcpNum < 0 && goalDiff === Math.abs(hcpNum)) {
          return c.isTarget && !c.score.includes('其它');
        }
        if (hcpNum > 0 && goalDiff === -hcpNum) {
          return c.isTarget && !c.score.includes('其它');
        }
        return false;
      });
      drawMatchScores.sort((a, b) => {
        const [ha, aa] = a.score.split(':').map(Number);
        const [hb, ab] = b.score.split(':').map(Number);
        const totalA = ha + aa;
        const totalB = hb + ab;
        return totalA - totalB;
      });
      drawMatchScores.forEach(score => {
        if (!targetScores.find(s => s.score === score.score)) {
          targetScores.unshift(score);
        }
      });
      targetScores.splice(5);
    }

    if (handicapPrediction === 'handicap_lose' && targetGoals.length > 0) {
      const awayWinScores = allChanges.filter(c => {
        const [home, away] = c.score.split(':').map(Number);
        const goalDiff = away - home;
        if (handicap && parseInt(handicap) === -1) {
          return c.type === 'awayWins' && goalDiff >= 1 && c.isTarget && !c.score.includes('其它');
        }
        return c.type === 'awayWins' && goalDiff >= 2 && c.isTarget && !c.score.includes('其它');
      });
      awayWinScores.sort((a, b) => {
        const [ha, aa] = a.score.split(':').map(Number);
        const [hb, ab] = b.score.split(':').map(Number);
        const diffA = aa - ha;
        const diffB = ab - hb;
        if (handicap && parseInt(handicap) === -1) {
          return diffA - diffB;
        }
        return diffB - diffA;
      });
      const nonAwayWinIndices = targetScores
        .map((s, i) => {
          const [h, a] = s.score.split(':').map(Number);
          if (s.type !== 'awayWins' || a - h < (handicap && parseInt(handicap) === -1 ? 1 : 2)) {
            return i;
          }
          return -1;
        })
        .filter(i => i >= 0);
      awayWinScores.forEach((score, i) => {
        if (!targetScores.find(s => s.score === score.score)) {
          if (i < nonAwayWinIndices.length) {
            targetScores.splice(nonAwayWinIndices[i], 1, score);
          } else {
            targetScores.push(score);
          }
        }
      });
      targetScores.splice(5);
    }

    if (handicapPrediction === 'handicap_win' && handicap && parseInt(handicap) === -1 && targetGoals.length > 0) {
      const homeBigWinScores = allChanges.filter(c => {
        const [home, away] = c.score.split(':').map(Number);
        return c.type === 'homeWins' && home - away >= 2 && !c.score.includes('其它') && c.isTarget && c.priorityScore > 0.5;
      });
      homeBigWinScores.forEach(score => {
        if (!targetScores.find(s => s.score === score.score)) {
          targetScores.unshift(score);
        }
      });
      targetScores.sort((a, b) => b.priorityScore - a.priorityScore);
      targetScores.splice(5);
    }

    if (targetScores.length === 0) {
      const allTop = allChanges.filter(c => c.priorityScore > 0.5).slice(0, 3);
      targetScores.push(...allTop);
    }

    const homeWinScores = allChanges
      .filter(c => c.type === 'homeWins' && c.direction === 'decrease')
      .slice(0, 5);

    return {
      status: 'success',
      allChanges: allChanges,
      topDecreases: allChanges.slice(0, 5),
      targetScores: targetScores,
      homeWinScores: homeWinScores,
      initialOdds: first,
      finalOdds: last,
      analysis: this._generateScoreAnalysis(allChanges, targetScores, targetGoals)
    };
  }

  _calculateScoreTrend(oddsHistory, type, score) {
    let consecutiveDecreases = 0;
    let totalDecreaseCount = 0;
    let previousOdds = null;
    let totalChange = 0;
    let weightedSum = 0;
    let weightSum = 0;
    
    oddsHistory.forEach((entry, index) => {
      const scoreEntry = entry[type]?.find(s => s.score === score);
      if (scoreEntry && previousOdds !== null) {
        const change = previousOdds - scoreEntry.odds;
        totalChange += change;
        
        const weight = (index + 1) / oddsHistory.length;
        weightedSum += change * weight;
        weightSum += weight;
        
        if (change > 0) {
          consecutiveDecreases++;
          totalDecreaseCount++;
        } else if (change < 0) {
          consecutiveDecreases = 0;
        }
      }
      if (scoreEntry) {
        previousOdds = scoreEntry.odds;
      }
    });
    
    const timeWeightedChange = weightSum > 0 ? weightedSum / weightSum : 0;
    const totalPeriods = oddsHistory.length > 1 ? oddsHistory.length - 1 : 1;
    const trendStrength = totalDecreaseCount / totalPeriods;
    
    return {
      consecutiveDecreases: consecutiveDecreases,
      totalDecreaseCount: totalDecreaseCount,
      timeWeightedChange: timeWeightedChange,
      trendStrength: trendStrength,
      totalChange: totalChange
    };
  }

  _generateScoreAnalysis(allChanges, targetScores, targetGoals) {
    if (targetScores.length === 0) {
      return '比分赔率变化不明显';
    }
    
    const scoreList = targetScores.map(s => s.score).join('、');
    return `从比分赔率下降幅度看，概率进球比分 ${scoreList}`;
  }

  analyzeHalfTimeFullTime(oddsHistory) {
    if (!oddsHistory || oddsHistory.length < 2) {
      return { status: 'insufficient_data', prediction: null, changes: [], decrease: [], increase: [] };
    }

    const first = oddsHistory[0];
    const last = oddsHistory[oddsHistory.length - 1];

    const resultTypes = ['winWin', 'winDraw', 'winLose', 'drawWin', 'drawDraw', 'drawLose', 'loseWin', 'loseDraw', 'loseLose'];
    const changes = [];

    resultTypes.forEach(type => {
      if (typeof first[type] === 'number' && typeof last[type] === 'number') {
        const change = first[type] - last[type];
        const percentageChange = (change / first[type]) * 100;
        
        let priorityScore = 0;
        const oddsLevel = first[type];
        
        if (change > 0) {
          priorityScore = (change / oddsLevel) * (10 / oddsLevel) + (10 / oddsLevel);
        } else if (change === 0) {
          priorityScore = 10 / oddsLevel;
        }
        
        changes.push({
          result: type,
          initial: first[type],
          final: last[type],
          change: change,
          percentageChange: percentageChange,
          priorityScore: priorityScore,
          direction: change > 0 ? 'decrease' : change < 0 ? 'increase' : 'stable'
        });
      }
    });

    const decrease = changes.filter(c => c.direction === 'decrease');
    const increase = changes.filter(c => c.direction === 'increase');
    
    decrease.sort((a, b) => b.priorityScore - a.priorityScore);
    
    let prediction = null;
    let analysis = '';
    
    if (decrease.length > 0) {
      const topDecrease = decrease[0];
      prediction = topDecrease.result;
      
      const resultMap = {
        'winWin': '胜胜',
        'winDraw': '胜平',
        'winLose': '胜负',
        'drawWin': '平胜',
        'drawDraw': '平平',
        'drawLose': '平负',
        'loseWin': '负胜',
        'loseDraw': '负平',
        'loseLose': '负负'
      };
      
      analysis = `半全场赔率看，${resultMap[topDecrease.result]}赔率从${topDecrease.initial}下降至${topDecrease.final}（↓${topDecrease.change.toFixed(2)}），概率最高`;
      
      if (topDecrease.result === 'winWin') {
        analysis += '，暗示主队上半场领先且最终获胜';
      } else if (topDecrease.result === 'drawWin') {
        analysis += '，暗示平局进入下半场后主队获胜';
      } else if (topDecrease.result === 'drawDraw') {
        analysis += '，暗示全场平局';
      }
    }

    return {
      status: 'success',
      changes: changes,
      decrease: decrease,
      increase: increase,
      prediction: prediction,
      analysis: analysis,
      initialOdds: first,
      finalOdds: last,
      topDecreases: decrease.slice(0, 3)
    };
  }

  analyzeHandicapOdds(oddsHistory, handicap = '-1', winDrawLossHistory = null, scoresHistory = null) {
    if (!oddsHistory || oddsHistory.length < 2) {
      return { status: 'insufficient_data', prediction: null, changes: [], decrease: [], increase: [] };
    }

    const first = oddsHistory[0];
    const last = oddsHistory[oddsHistory.length - 1];

    const resultTypes = ['win', 'draw', 'lose'];
    const changes = [];

    resultTypes.forEach(type => {
      if (typeof first[type] === 'number' && typeof last[type] === 'number') {
        const change = first[type] - last[type];
        const percentageChange = (change / first[type]) * 100;
        
        let priorityScore = 0;
        const oddsLevel = first[type];
        
        if (change > 0) {
          priorityScore = (change / oddsLevel) * (10 / oddsLevel) + (10 / oddsLevel);
        } else if (change === 0) {
          priorityScore = 10 / oddsLevel;
        }
        
        changes.push({
          result: type,
          handicap: handicap,
          initial: first[type],
          final: last[type],
          change: change,
          percentageChange: percentageChange,
          priorityScore: priorityScore,
          direction: change > 0 ? 'decrease' : change < 0 ? 'increase' : 'stable'
        });
      }
    });

    const decrease = changes.filter(c => c.direction === 'decrease');
    const increase = changes.filter(c => c.direction === 'increase');
    
    let prediction = null;
    let analysis = '';

    const handicapNum = parseInt(handicap);
    const isPositiveHandicap = handicapNum > 0;

    let winDrawLossReverse = false;
    let awayWinOddsLow = false;
    if (winDrawLossHistory && winDrawLossHistory.length >= 2) {
      const wdlFirst = winDrawLossHistory[0].winA;
      const wdlLast = winDrawLossHistory[winDrawLossHistory.length - 1].winA;
      const wdlMin = Math.min(...winDrawLossHistory.map(w => w.winA));
      const wdlMinIndex = winDrawLossHistory.findIndex(w => w.winA === wdlMin);
      
      if (wdlMinIndex > 0 && wdlMinIndex < winDrawLossHistory.length - 1) {
        const beforeMin = winDrawLossHistory[wdlMinIndex - 1].winA;
        const afterMin = winDrawLossHistory[wdlMinIndex + 1].winA;
        
        if (beforeMin > wdlMin && afterMin > wdlMin) {
          winDrawLossReverse = true;
        }
      }

      const wdlFirstB = winDrawLossHistory[0].winB;
      const wdlLastB = winDrawLossHistory[winDrawLossHistory.length - 1].winB;
      if (wdlFirstB < 1.50 || wdlLastB < 1.50) {
        awayWinOddsLow = true;
      }
    }

    const vPattern = this._detectVPattern(oddsHistory);

    if (decrease.length > 0) {
      decrease.sort((a, b) => b.priorityScore - a.priorityScore);
      const topDecrease = decrease[0];
      
      const minOdds = Math.min(first.win, first.draw, first.lose);
      const loseIsLowest = first.lose === minOdds;
      const loseChangeAbs = Math.abs(changes.find(c => c.result === 'lose').change);
      
      const winIncrease = increase.find(c => c.result === 'win');
      const loseIncrease = increase.find(c => c.result === 'lose');
      const winDecrease = decrease.find(c => c.result === 'win');
      const loseDecrease = decrease.find(c => c.result === 'lose');
      const drawDecrease = decrease.find(c => c.result === 'draw');
      const winChange = winDecrease ? winDecrease.change : 0;
      const loseChange = loseDecrease ? loseDecrease.change : 0;
      
      decrease.sort((a, b) => b.change - a.change);
      const maxDecrease = decrease[0];
      
      if (isPositiveHandicap && Math.abs(handicapNum) === 1 && awayWinOddsLow && loseDecrease && loseDecrease.change > 0.50) {
        let winPeakIndex = -1;
        let winPeakValue = first.win;
        for (let i = 1; i < oddsHistory.length - 1; i++) {
          if (oddsHistory[i].win > winPeakValue && oddsHistory[i].win > oddsHistory[i-1].win && oddsHistory[i].win > oddsHistory[i+1].win) {
            winPeakValue = oddsHistory[i].win;
            winPeakIndex = i;
          }
        }
        if (winPeakIndex > 0 && last.win < winPeakValue && last.win > first.win) {
          prediction = 'handicap_win';
          analysis = `让球(${handicap})为正向让球（主队受让1球），胜平负客胜赔率极低（<1.50）；让负赔率大幅下降${loseDecrease.change.toFixed(2)}看似客队大胜，但让胜赔率出现倒V型反转（从${first.win}升至${winPeakValue}再降至${last.win}），庄家可能通过降低让负赔率吸引投注，实际防范让胜打出`;
        } else {
          prediction = 'handicap_lose';
          analysis = `让球(${handicap})为正向让球（主队受让1球），胜平负客胜赔率极低（<1.50），市场明确看好客队获胜；让负赔率大幅下降${loseDecrease.change.toFixed(2)}，客队大胜概率增加，预测让负`;
        }
      } else if (isPositiveHandicap && awayWinOddsLow && maxDecrease.result === 'win' && winChange > 0.20) {
        prediction = 'handicap_draw';
        analysis = `让球(${handicap})为正向让球（主队受让），胜平负客胜赔率极低（<1.50），市场明确看好客队获胜；虽然让胜赔率下降${winChange.toFixed(2)}，但在客队强势场景下，让平（客队净胜1球）是最可能结果`;
      } else if (isPositiveHandicap && Math.abs(handicapNum) === 1 && awayWinOddsLow && winIncrease && Math.abs(winIncrease.change) > 0.30 && first.win < 2.5 && drawDecrease && drawDecrease.change < 0.5) {
        prediction = 'handicap_win';
        analysis = `让球(${handicap})为正向让球（主队受让1球），客胜赔率极低（<1.50）；让胜赔率从较低水平(${first.win})大幅上升${Math.abs(winIncrease.change).toFixed(2)}，且让平赔率下降幅度较小(${drawDecrease.change.toFixed(2)})，这是庄家典型的诱盘手法——通过抬高让胜赔率、降低让负赔率吸引投注到让负，实际防范让胜（主队获胜或小负）打出`;
      } else if (isPositiveHandicap && Math.abs(handicapNum) === 1 && awayWinOddsLow && winIncrease && Math.abs(winIncrease.change) > 0.25 && loseDecrease && loseDecrease.change > 0.15 && drawDecrease && drawDecrease.change > 0.05) {
        prediction = 'handicap_draw';
        analysis = `让球(${handicap})为正向让球（主队受让1球），客胜赔率极低（<1.50）；让胜赔率大幅上升${Math.abs(winIncrease.change).toFixed(2)}且让负赔率下降${loseDecrease.change.toFixed(2)}，两边赔率向相反方向移动，但让平赔率也小幅下降${drawDecrease.change.toFixed(2)}，庄家可能通过让胜和让负的赔率变化诱导投注，实际防范让平（客队净胜1球）打出`;
      } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && loseIsLowest && loseChangeAbs < 0.15 && this._hasHandicapDrawScoreDrop(scoresHistory, handicap)) {
        const winDecrease = decrease.find(c => c.result === 'win');
        const winChange = winDecrease ? winDecrease.change : 0;
        
        const hasWinDrop = this._hasHandicapWinScoreDrop(scoresHistory, handicap);
        const hasDrawDrop = this._hasHandicapDrawScoreDrop(scoresHistory, handicap);
        
        const isWeakFavorite = winDrawLossHistory && winDrawLossHistory.length > 0 && winDrawLossHistory[0].winA > 1.5;
        
        if (isWeakFavorite && hasDrawDrop && !hasWinDrop) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率且保持稳定（变化${changes.find(c => c.result === 'lose').change.toFixed(2)}）；主胜赔率较高（${winDrawLossHistory[0].winA}）表明主队是弱热门，且让平比分赔率（如2:1）大幅下降，庄家实际防范让平打出`;
        } else if (winChange > 0 && hasWinDrop && !hasDrawDrop) {
          prediction = 'handicap_win';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率且保持稳定（变化${changes.find(c => c.result === 'lose').change.toFixed(2)}），让胜赔率下降${winChange.toFixed(2)}且让胜比分赔率也大幅下降，庄家实际防范让胜打出`;
        } else if (hasDrawDrop && (!hasWinDrop || isWeakFavorite)) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率且保持稳定（变化${changes.find(c => c.result === 'lose').change.toFixed(2)}）；让平比分赔率（如2:1）大幅下降，且主胜赔率较高（${winDrawLossHistory?.[0]?.winA || 'N/A'}）表明主队优势不明显，庄家实际防范让平打出`;
        } else if (winChange > 0 && hasWinDrop) {
          prediction = 'handicap_win';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率且保持稳定（变化${changes.find(c => c.result === 'lose').change.toFixed(2)}），让胜赔率下降${winChange.toFixed(2)}且让胜比分赔率也大幅下降，庄家实际防范让胜打出`;
        } else {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率且保持稳定（变化${changes.find(c => c.result === 'lose').change.toFixed(2)}），但对应让平的比分赔率（如2:1）大幅下降，庄家实际防范让平打出`;
        }
      } else if (loseIsLowest && loseChangeAbs < 0.40 && maxDecrease.result === 'draw' && maxDecrease.change < 0.50) {
        prediction = 'handicap_lose';
        analysis = `让负赔率${first.lose}是最低赔率且保持稳定（变化${changes.find(c => c.result === 'lose').change.toFixed(2)}），市场本身看好让负，虽然让平赔率下降${maxDecrease.change.toFixed(2)}，但让负概率更高`;
      } else if (isPositiveHandicap && Math.abs(handicapNum) >= 2 && maxDecrease.result === 'draw' && maxDecrease.change > 0.30 && winIncrease && Math.abs(winIncrease.change) > 0.30 && loseDecrease && loseDecrease.change > 0.20) {
        prediction = 'handicap_win';
        analysis = `让球(${handicap})为正向让球且让球数较大（≥2），让平赔率下降${maxDecrease.change.toFixed(2)}但让胜赔率大幅上涨${Math.abs(winIncrease.change).toFixed(2)}且让负赔率也下降${loseDecrease.change.toFixed(2)}，庄家可能通过降低让平和让负赔率吸引投注，实际防范让胜打出`;
      } else if (isPositiveHandicap && Math.abs(handicapNum) >= 2 && awayWinOddsLow && maxDecrease.result === 'draw' && maxDecrease.change > 0.30 && winIncrease && Math.abs(winIncrease.change) < 0.50) {
        prediction = 'handicap_win';
        analysis = `让球(${handicap})为正向让球且让球数较大（≥2），客胜赔率极低（<1.50），市场明确看好客队获胜；让平赔率下降${maxDecrease.change.toFixed(2)}看似客队净胜等于让球数，但让胜赔率仅小幅上涨${Math.abs(winIncrease.change).toFixed(2)}，庄家可能通过降低让平赔率吸引投注，实际防范让胜（客队小胜）打出`;
      } else if (maxDecrease.result === 'draw' && maxDecrease.change > 0.30) {
        if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && this._isDrawScoreDropDominant(scoresHistory, handicap)) {
          let homeWinDropThenRise = false;
          if (winDrawLossHistory && winDrawLossHistory.length >= 3) {
            const midIndex = Math.floor(winDrawLossHistory.length / 2);
            const first = winDrawLossHistory[0].winA;
            const mid = winDrawLossHistory[midIndex].winA;
            const last = winDrawLossHistory[winDrawLossHistory.length - 1].winA;
            if (mid < first && last > mid && last > first) {
              homeWinDropThenRise = true;
            }
          }
          if (homeWinDropThenRise) {
            prediction = 'handicap_draw';
            analysis = `让球(${handicap})为负向让球（主队让球），让平赔率下降${maxDecrease.change.toFixed(2)}；主胜赔率先降后升（从${winDrawLossHistory[0].winA}降至${winDrawLossHistory[Math.floor(winDrawLossHistory.length/2)].winA}再升至${winDrawLossHistory[winDrawLossHistory.length-1].winA}），主队优势收窄，庄家实际防范让平（主队净胜1球）打出`;
          } else {
            prediction = 'handicap_lose';
            analysis = `让球(${handicap})为负向让球（主队让球），让平赔率下降${maxDecrease.change.toFixed(2)}，但平局比分（1:1）赔率大幅下降，庄家实际防范平局打出，对应让负`;
          }
        } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && loseIsLowest && loseChangeAbs < 0.15 && !this._isDrawScoreDropDominant(scoresHistory, handicap)) {
          prediction = 'handicap_lose';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}始终是最低赔率且保持稳定（变化${changes.find(c => c.result === 'lose').change.toFixed(2)}），虽然让平赔率下降${maxDecrease.change.toFixed(2)}，但平局比分（1:1）赔率变化不大，庄家可能通过降低让平赔率吸引投注，实际防范让负打出`;
        } else {
          prediction = 'handicap_draw';
          analysis = `让平赔率从初盘的${maxDecrease.initial}下降至${maxDecrease.final}（↓${maxDecrease.change.toFixed(2)}），下降幅度最大，庄家预测平局概率增加`;
        }
      } else if (loseIncrease && Math.abs(loseIncrease.change) > 0.05 && winDecrease) {
        if (!isPositiveHandicap && first.lose === minOdds && Math.abs(loseIncrease.change) < 0.40) {
          if (!isPositiveHandicap && Math.abs(handicapNum) >= 3 && winChange > 0.20 && this._hasHandicapDrawScoreDrop(scoresHistory, handicap)) {
            prediction = 'handicap_draw';
            analysis = `让球(${handicap})为负向让球且让球数较大（≥3），让负赔率${first.lose}是初始最低赔率，让负赔率上涨${Math.abs(loseIncrease.change).toFixed(2)}且让胜赔率下降${winChange.toFixed(2)}，但对应让平的比分赔率大幅下降，庄家通过降低让胜赔率吸引投注，实际防范让平（主队刚好净胜让球数）打出`;
          } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && winChange > 0.30 && drawDecrease && drawDecrease.change > 0.15 && this._isDrawScoreDropDominant(scoresHistory, handicap)) {
            prediction = 'handicap_lose';
            analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率且上涨${Math.abs(loseIncrease.change).toFixed(2)}，让胜赔率大幅下降${winChange.toFixed(2)}看似主胜机会大，但让平赔率也下降${drawDecrease.change.toFixed(2)}且平局比分赔率下降，庄家通过降低让胜赔率吸引投注，实际防范让负（平局）打出`;
          } else if (winChange > 0.30) {
            prediction = 'handicap_win';
            analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率，但让负赔率上涨${Math.abs(loseIncrease.change).toFixed(2)}且让胜赔率大幅下降${winChange.toFixed(2)}，市场倾向让胜`;
          } else {
            prediction = 'handicap_lose';
            analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率，虽然让负赔率上涨${Math.abs(loseIncrease.change).toFixed(2)}且让胜赔率下降${winChange.toFixed(2)}，但让负赔率仍然是最低赔率，市场仍然看好让负`;
          }
        } else if (winChange < 0.15 && increase.find(c => c.result === 'draw') && Math.abs(increase.find(c => c.result === 'draw').change) > 0.2) {
          prediction = 'handicap_draw';
          analysis = `让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），让负概率降低；但让胜赔率仅小幅下降${winChange.toFixed(2)}，让平赔率上涨${Math.abs(increase.find(c => c.result === 'draw').change).toFixed(2)}，庄家防范平局打出`;
        } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && increase.find(c => c.result === 'draw') && Math.abs(increase.find(c => c.result === 'draw').change) > 0.25 && this._hasHandicapDrawScoreDrop(scoresHistory, handicap)) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率上涨${Math.abs(loseIncrease.change).toFixed(2)}且让胜赔率下降${winChange.toFixed(2)}，但让平赔率大幅上升${Math.abs(increase.find(c => c.result === 'draw').change).toFixed(2)}，庄家通过降低让胜赔率吸引投注；对应让平的比分赔率（如2:1）大幅下降，庄家实际防范让平打出`;
        } else {
          prediction = 'handicap_win';
          analysis = `让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），让负概率降低，同时让胜赔率下降${winChange.toFixed(2)}，让胜概率增加`;
        }
      } else if (maxDecrease.result === 'draw' && maxDecrease.change > 0.15) {
        prediction = 'handicap_draw';
        analysis = `让平赔率从初盘的${maxDecrease.initial}下降至${maxDecrease.final}（↓${maxDecrease.change.toFixed(2)}），下降幅度最大，庄家预测平局概率增加`;
      }
    }

    if (!prediction && vPattern && vPattern.type === 'reverse') {
      const loseDecrease = decrease.find(c => c.result === 'lose');
      const drawIncrease = increase.find(c => c.result === 'draw');
      const drawDecrease = decrease.find(c => c.result === 'draw');
      const winDecrease = decrease.find(c => c.result === 'win');
      const winChange = winDecrease ? winDecrease.change : 0;
      
      if (vPattern.reversedResult === 'win') {
        if (winDrawLossReverse) {
          prediction = 'handicap_lose';
          analysis = `检测到${vPattern.description}，但主胜赔率出现倒V型反转（最终高于初始值），主胜概率下降，庄家可能诱盘让胜，实际看好让负`;
        } else {
          prediction = 'handicap_win';
          analysis = `检测到${vPattern.description}，庄家预测主队赢盘概率增加`;
        }
      } else if (vPattern.reversedResult === 'lose') {
        if (isPositiveHandicap && Math.abs(handicapNum) >= 2 && loseDecrease && loseDecrease.change > 0.30) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为正向让球且让球数较大（≥2），让负赔率出现谷底（倒V型反转）并大幅下降${loseDecrease.change.toFixed(2)}，但在大让球场景下，客队净胜正好等于让球数（让平）的概率更高，庄家可能通过降低让负赔率吸引投注`;
        } else if (isPositiveHandicap && Math.abs(handicapNum) === 1 && awayWinOddsLow && loseDecrease && loseDecrease.change > 0 && drawIncrease && Math.abs(drawIncrease.change) > 0.30) {
          if (loseDecrease.change > 0.50) {
            prediction = 'handicap_lose';
            analysis = `让球(${handicap})为正向让球（主队受让1球），客胜赔率极低（<1.50），市场明确看好客队获胜；让负赔率大幅下降${loseDecrease.change.toFixed(2)}，客队大胜概率增加，预测让负`;
          } else {
            prediction = 'handicap_draw';
            analysis = `让球(${handicap})为正向让球（主队受让1球），客胜赔率极低（<1.50），市场明确看好客队获胜；让负赔率出现V型反转并小幅下降${loseDecrease.change.toFixed(2)}但让平赔率大幅上升${Math.abs(drawIncrease.change).toFixed(2)}，庄家可能通过降低让负赔率吸引投注，实际防范让平（客队净胜1球）打出`;
          }
        } else if (isPositiveHandicap && Math.abs(handicapNum) === 1 && awayWinOddsLow && loseDecrease && loseDecrease.change > 0.30 && drawDecrease && drawDecrease.change > 0.10) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为正向让球（主队受让1球），客胜赔率极低（<1.50），市场明确看好客队获胜；让胜赔率出现V型反转，但让负赔率大幅下降${loseDecrease.change.toFixed(2)}且让平赔率也下降${drawDecrease.change.toFixed(2)}，庄家通过降低让负赔率吸引投注，实际防范让平（客队净胜1球）打出`;
        } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && first.lose === Math.min(first.win, first.draw, first.lose) && loseDecrease && loseDecrease.change < 0.15 && drawIncrease && Math.abs(drawIncrease.change) > 0.25) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率且小幅下降${loseDecrease.change.toFixed(2)}，但让平赔率大幅上涨${Math.abs(drawIncrease.change).toFixed(2)}，庄家可能通过降低让负赔率吸引投注，实际防范让平打出`;
        } else {
          prediction = 'handicap_lose';
          analysis = `检测到${vPattern.description}，庄家预测客队赢盘概率增加`;
        }
      }
    }

    if (!prediction && decrease.length > 0) {
      decrease.sort((a, b) => b.priorityScore - a.priorityScore);
      const top = decrease[0];
      
      const winIncrease = increase.find(c => c.result === 'win');
      const loseIncrease = increase.find(c => c.result === 'lose');
      const drawIncrease = increase.find(c => c.result === 'draw');
      const winDecrease = decrease.find(c => c.result === 'win');
      const drawDecrease = decrease.find(c => c.result === 'draw');
      const loseDecrease = decrease.find(c => c.result === 'lose');
      
      const winChange = winDecrease ? winDecrease.change : 0;
      const drawChange = drawDecrease ? drawDecrease.change : 0;
      const loseChange = loseDecrease ? loseDecrease.change : 0;

      if (isPositiveHandicap && top.result === 'lose' && loseChange > 0.20 && winIncrease && Math.abs(winIncrease.change) > 0.30) {
        if (drawDecrease && drawDecrease.change > 0.15) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为正向让球（主队受让），让负赔率下降${loseChange.toFixed(2)}但让平赔率也下降${drawDecrease.change.toFixed(2)}，让平概率增加`;
        } else if (awayWinOddsLow && loseChange > 0.30) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为正向让球（主队受让），客胜赔率极低（<1.50），市场明确看好客队获胜；让负赔率大幅下降${loseChange.toFixed(2)}，但在客队强势场景下，让平（客队净胜1球）是最可能结果`;
        } else {
          prediction = 'handicap_lose';
          analysis = `让球(${handicap})为正向让球（主队受让），让负赔率下降${loseChange.toFixed(2)}，是唯一大幅下降的赔率，庄家预测客队赢盘概率增加`;
        }
      } else if (isPositiveHandicap && Math.abs(handicapNum) === 1 && awayWinOddsLow && top.result === 'lose' && loseChange > 0.30 && drawDecrease && drawDecrease.change > 0.10) {
        prediction = 'handicap_draw';
        analysis = `让球(${handicap})为正向让球（主队受让1球），客胜赔率极低（<1.50），市场明确看好客队获胜；让负赔率大幅下降${loseChange.toFixed(2)}，让平赔率也下降${drawDecrease.change.toFixed(2)}，庄家通过降低让负赔率吸引投注，实际防范让平（客队净胜1球）打出`;
      }

      if (!prediction && isPositiveHandicap && awayWinOddsLow && top.result === 'win' && winChange > 0.20) {
        prediction = 'handicap_draw';
        analysis = `让球(${handicap})为正向让球（主队受让），胜平负客胜赔率极低（<1.50），市场明确看好客队获胜；虽然让胜赔率下降${winChange.toFixed(2)}，但在客队强势场景下，让平（客队净胜1球）是最可能结果`;
      }
      
      if (!prediction && isPositiveHandicap && Math.abs(handicapNum) === 1 && awayWinOddsLow && loseDecrease && loseChange > 0 && drawIncrease && Math.abs(drawIncrease.change) > 0.30) {
        prediction = 'handicap_draw';
        analysis = `让球(${handicap})为正向让球（主队受让1球），客胜赔率极低（<1.50），市场明确看好客队获胜；让负赔率小幅下降${loseChange.toFixed(2)}但让平赔率大幅上升${Math.abs(drawIncrease.change).toFixed(2)}，庄家可能通过降低让负赔率吸引投注，实际防范让平（客队净胜1球）打出`;
      }
      
      if (!prediction && winIncrease && Math.abs(winIncrease.change) > 0.30 && loseChange < 0.05 && !loseIncrease) {
        prediction = 'handicap_win';
        analysis = `让胜赔率从初盘的${winIncrease.initial}大幅上涨至${winIncrease.final}（↑${Math.abs(winIncrease.change).toFixed(2)}），但让负赔率几乎不变（↓${loseChange.toFixed(2)}），庄家抬高让胜赔率吸引投注，实际看好让胜`;
      } else if (!prediction && loseIncrease && Math.abs(loseIncrease.change) > 0.05 && winDecrease) {
        if (winChange < 0.15 && drawIncrease && Math.abs(drawIncrease.change) > 0.2) {
          prediction = 'handicap_draw';
          analysis = `让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），让负概率降低；但让胜赔率仅小幅下降${winChange.toFixed(2)}，让平赔率上涨${Math.abs(drawIncrease.change).toFixed(2)}，庄家防范平局打出`;
        } else if (winDrawLossReverse && Math.abs(loseIncrease.change) > 0.30) {
          prediction = 'handicap_lose';
          analysis = `主胜赔率出现倒V型反转（最终高于初始值），主胜概率下降；让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），但主胜赔率反转暗示庄家诱盘，实际看好让负`;
        } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && drawIncrease && Math.abs(drawIncrease.change) > 0.25 && this._hasHandicapDrawScoreDrop(scoresHistory, handicap)) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率上涨${Math.abs(loseIncrease.change).toFixed(2)}且让胜赔率下降${winChange.toFixed(2)}，但让平赔率大幅上升${Math.abs(drawIncrease.change).toFixed(2)}，庄家通过降低让胜赔率吸引投注；对应让平的比分赔率（如2:1）大幅下降，庄家实际防范让平打出`;
        } else {
          prediction = 'handicap_win';
          analysis = `让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），让负概率降低，同时让胜赔率下降，让胜概率增加`;
        }
      } else if (!prediction && top.result === 'draw' && top.change > 0.2) {
        if (isPositiveHandicap && Math.abs(handicapNum) >= 2 && winIncrease && Math.abs(winIncrease.change) > 0.30 && loseDecrease && loseDecrease.change > 0.20) {
          prediction = 'handicap_win';
          analysis = `让球(${handicap})为正向让球且让球数较大（≥2），让平赔率下降${top.change.toFixed(2)}但让胜赔率大幅上涨${Math.abs(winIncrease.change).toFixed(2)}且让负赔率也下降${loseDecrease.change.toFixed(2)}，庄家可能通过降低让平和让负赔率吸引投注，实际防范让胜打出`;
        } else {
          prediction = 'handicap_draw';
          analysis = `让平赔率从初盘的${top.initial}下降至${top.final}（↓${top.change.toFixed(2)}），下降幅度最大，庄家预测平局概率增加`;
        }
      } else if (!prediction && loseIncrease && Math.abs(loseIncrease.change) > 0.05 && !winIncrease) {
        if (winChange < 0.15 && drawIncrease && Math.abs(drawIncrease.change) > 0.2) {
          prediction = 'handicap_draw';
          analysis = `让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），让负概率降低；但让胜赔率仅小幅下降${winChange.toFixed(2)}，让平赔率上涨${Math.abs(drawIncrease.change).toFixed(2)}，庄家防范平局打出`;
        } else if (winDrawLossReverse && Math.abs(loseIncrease.change) > 0.30) {
          prediction = 'handicap_lose';
          analysis = `主胜赔率出现倒V型反转（最终高于初始值），主胜概率下降；让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），但主胜赔率反转暗示庄家诱盘，实际看好让负`;
        } else {
          prediction = 'handicap_win';
          analysis = `让负赔率从初盘的${loseIncrease.initial}上涨至${loseIncrease.final}（↑${Math.abs(loseIncrease.change).toFixed(2)}），让负概率降低，让胜概率增加`;
        }
      } else if (!prediction && top.result === 'win') {
        if (winChange < 0.15 && drawIncrease && Math.abs(drawIncrease.change) > 0.2) {
          prediction = 'handicap_draw';
          analysis = `让胜赔率仅小幅下降${winChange.toFixed(2)}，但让平赔率上涨${Math.abs(drawIncrease.change).toFixed(2)}，庄家防范平局打出`;
        } else if (winDrawLossReverse && loseIncrease && Math.abs(loseIncrease.change) > 0.30) {
          prediction = 'handicap_lose';
          analysis = `让胜赔率从初盘的${top.initial}下降至${top.final}（↓${top.change.toFixed(2)}），但主胜赔率出现倒V型反转（最终高于初始值），主胜概率下降，庄家可能诱盘让胜，实际看好让负`;
        } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && first.lose === Math.min(first.win, first.draw, first.lose) && loseIncrease && Math.abs(loseIncrease.change) > 0.10 && winChange > 0.50) {
          prediction = 'handicap_lose';
          analysis = `让球(${handicap})为负向让球（主队让球），让负赔率${first.lose}是初始最低赔率，让负赔率上涨${Math.abs(loseIncrease.change).toFixed(2)}但让胜赔率大幅下降${winChange.toFixed(2)}，庄家通过降低让胜赔率吸引投注，实际防范让负打出`;
        } else if (!isPositiveHandicap && Math.abs(handicapNum) === 1 && winDecrease && winChange > 0.15 && drawIncrease && Math.abs(drawIncrease.change) > 0.25 && this._hasHandicapDrawScoreDrop(scoresHistory, handicap)) {
          prediction = 'handicap_draw';
          analysis = `让球(${handicap})为负向让球（主队让球），让胜赔率下降${winChange.toFixed(2)}但让平赔率大幅上升${Math.abs(drawIncrease.change).toFixed(2)}，庄家通过降低让胜赔率吸引投注；但对应让平的比分赔率（如2:1）大幅下降，庄家实际防范让平打出`;
        } else {
          prediction = 'handicap_win';
          analysis = `让胜赔率从初盘的${top.initial}下降至${top.final}，下降幅度${top.change.toFixed(2)}，庄家预测主队大概率会赢盘`;
        }
      } else if (!prediction && top.result === 'draw') {
        prediction = 'handicap_draw';
        analysis = `让平赔率下降幅度最大，庄家预测平局概率增加`;
      } else if (!prediction && top.change < 0.05) {
        const drawDecrease2 = decrease.find(c => c.result === 'draw');
        if (drawDecrease2 && drawDecrease2.change > 0.15) {
          prediction = 'handicap_draw';
          analysis = `让负赔率下降幅度很小（↓${top.change.toFixed(2)}），让平赔率下降${drawDecrease2.change.toFixed(2)}，庄家防范平局打出`;
        } else {
          const minOdds = Math.min(first.win, first.draw, first.lose);
          if (first.win === minOdds) {
            prediction = 'handicap_win';
            analysis = `让胜赔率${first.win}是初始最低赔率，赔率变化幅度较小，市场看好让胜`;
          } else if (first.draw === minOdds) {
            prediction = 'handicap_draw';
            analysis = `让平赔率${first.draw}是初始最低赔率，赔率变化幅度较小，市场看好让平`;
          } else {
            prediction = 'handicap_lose';
            analysis = `让负赔率${first.lose}是初始最低赔率，赔率变化幅度较小，市场看好让负`;
          }
        }
      } else if (!prediction) {
        const minOdds = Math.min(first.win, first.draw, first.lose);
        if (first.win === minOdds) {
          prediction = 'handicap_win';
          analysis = `让胜赔率${first.win}是初始最低赔率，市场看好让胜`;
        } else if (first.draw === minOdds) {
          prediction = 'handicap_draw';
          analysis = `让平赔率${first.draw}是初始最低赔率，市场看好让平`;
        } else {
          prediction = 'handicap_lose';
          analysis = `让负赔率${first.lose}是初始最低赔率，市场看好让负`;
        }
      }
    }

    return {
      status: 'success',
      changes: changes,
      decrease: decrease,
      increase: increase,
      prediction: prediction,
      analysis: analysis,
      initialOdds: first,
      finalOdds: last,
      handicap: handicap,
      vPattern: vPattern
    };
  }

  _detectVPattern(oddsHistory) {
    if (oddsHistory.length < 3) return null;
    
    const first = oddsHistory[0];
    const last = oddsHistory[oddsHistory.length - 1];
    
    let maxLoseIndex = 0;
    let maxLoseValue = first.lose;
    let minLoseIndex = 0;
    let minLoseValue = first.lose;
    
    let maxWinIndex = 0;
    let maxWinValue = first.win;
    let minWinIndex = 0;
    let minWinValue = first.win;
    
    oddsHistory.forEach((item, index) => {
      if (item.lose > maxLoseValue) { maxLoseValue = item.lose; maxLoseIndex = index; }
      if (item.lose < minLoseValue) { minLoseValue = item.lose; minLoseIndex = index; }
      if (item.win > maxWinValue) { maxWinValue = item.win; maxWinIndex = index; }
      if (item.win < minWinValue) { minWinValue = item.win; minWinIndex = index; }
    });
    
    const MIN_CHANGE_THRESHOLD = 0.05;
    
    if (maxLoseIndex > 0 && maxLoseIndex < oddsHistory.length - 1) {
      const beforePeak = oddsHistory[maxLoseIndex - 1].lose;
      const afterPeak = oddsHistory[maxLoseIndex + 1].lose;
      if (beforePeak < maxLoseValue && afterPeak < maxLoseValue) {
        const change = Math.abs(last.lose - first.lose);
        if (change >= MIN_CHANGE_THRESHOLD) {
          if (last.lose < first.lose) {
            return { type: 'reverse', reversedResult: 'lose', description: '让负赔率先升后降（V型反转），最终让负赔率低于初始值，让负概率增加' };
          } else {
            return { type: 'reverse', reversedResult: 'win', description: '让负赔率出现峰值（V型反转），最终让负赔率高于初始值，让胜概率增加' };
          }
        }
      }
    }
    
    if (minLoseIndex > 0 && minLoseIndex < oddsHistory.length - 1) {
      const beforeValley = oddsHistory[minLoseIndex - 1].lose;
      const afterValley = oddsHistory[minLoseIndex + 1].lose;
      if (beforeValley > minLoseValue && afterValley > minLoseValue) {
        const change = Math.abs(last.lose - first.lose);
        if (change >= MIN_CHANGE_THRESHOLD) {
          if (last.lose > first.lose) {
            return { type: 'reverse', reversedResult: 'win', description: '让负赔率先降后升（倒V型反转），最终让负赔率高于初始值，让胜概率增加' };
          } else {
            return { type: 'reverse', reversedResult: 'lose', description: '让负赔率出现谷底（倒V型反转），最终让负赔率低于初始值，让负概率增加' };
          }
        }
      }
    }
    
    if (maxWinIndex > 0 && maxWinIndex < oddsHistory.length - 1) {
      const beforePeak = oddsHistory[maxWinIndex - 1].win;
      const afterPeak = oddsHistory[maxWinIndex + 1].win;
      if (beforePeak < maxWinValue && afterPeak < maxWinValue) {
        const change = Math.abs(last.win - first.win);
        if (change >= MIN_CHANGE_THRESHOLD) {
          if (last.win < first.win) {
            return { type: 'reverse', reversedResult: 'win', description: '让胜赔率先升后降（V型反转），最终让胜赔率低于初始值，让胜概率增加' };
          } else {
            return { type: 'reverse', reversedResult: 'lose', description: '让胜赔率出现峰值（V型反转），最终让胜赔率高于初始值，让负概率增加' };
          }
        }
      }
    }
    
    if (minWinIndex > 0 && minWinIndex < oddsHistory.length - 1) {
      const beforeValley = oddsHistory[minWinIndex - 1].win;
      const afterValley = oddsHistory[minWinIndex + 1].win;
      if (beforeValley > minWinValue && afterValley > minWinValue) {
        const change = Math.abs(last.win - first.win);
        if (change >= MIN_CHANGE_THRESHOLD) {
          if (last.win > first.win) {
            return { type: 'reverse', reversedResult: 'lose', description: '让胜赔率先降后升（倒V型反转），最终让胜赔率高于初始值，让负概率增加' };
          } else {
            return { type: 'reverse', reversedResult: 'win', description: '让胜赔率出现谷底（倒V型反转），最终让胜赔率低于初始值，让胜概率增加' };
          }
        }
      }
    }
    
    return null;
  }

  _hasHandicapDrawScoreDrop(scoresHistory, handicap) {
    if (!scoresHistory || scoresHistory.length < 2) return false;
    
    const handicapNum = parseInt(handicap);
    const first = scoresHistory[0];
    const last = scoresHistory[scoresHistory.length - 1];
    
    const drawScoreChanges = [];
    
    if (handicapNum === -1) {
      const targetScores = ['2:1', '3:2', '4:3'];
      targetScores.forEach(score => {
        const firstOdds = first.homeWins?.find(s => s.score === score)?.odds;
        const lastOdds = last.homeWins?.find(s => s.score === score)?.odds;
        if (firstOdds && lastOdds && firstOdds - lastOdds > 0.4) {
          drawScoreChanges.push({ score, change: firstOdds - lastOdds });
        }
      });
      
      const winScoreChanges = [];
      const winScores = ['2:0', '3:0', '3:1', '4:0', '4:1'];
      winScores.forEach(score => {
        const firstOdds = first.homeWins?.find(s => s.score === score)?.odds;
        const lastOdds = last.homeWins?.find(s => s.score === score)?.odds;
        if (firstOdds && lastOdds && firstOdds - lastOdds > 0.3) {
          winScoreChanges.push({ score, change: firstOdds - lastOdds });
        }
      });
      
      if (winScoreChanges.length >= 2 && drawScoreChanges.length > 0) {
        return false;
      }
    } else if (handicapNum === 1) {
      const targetScores = ['1:2', '2:3', '3:4'];
      targetScores.forEach(score => {
        const firstOdds = first.awayWins?.find(s => s.score === score)?.odds;
        const lastOdds = last.awayWins?.find(s => s.score === score)?.odds;
        if (firstOdds && lastOdds && firstOdds - lastOdds > 0.4) {
          drawScoreChanges.push({ score, change: firstOdds - lastOdds });
        }
      });
    } else if (handicapNum === -2) {
      const targetScores = ['3:1', '4:2', '5:3'];
      targetScores.forEach(score => {
        const firstOdds = first.homeWins?.find(s => s.score === score)?.odds;
        const lastOdds = last.homeWins?.find(s => s.score === score)?.odds;
        if (firstOdds && lastOdds && firstOdds - lastOdds > 1.0) {
          drawScoreChanges.push({ score, change: firstOdds - lastOdds });
        }
      });
    } else if (handicapNum === -3) {
      const targetScores = ['3:0', '4:1', '5:2'];
      targetScores.forEach(score => {
        const firstOdds = first.homeWins?.find(s => s.score === score)?.odds;
        const lastOdds = last.homeWins?.find(s => s.score === score)?.odds;
        if (firstOdds && lastOdds && firstOdds - lastOdds > 0.4) {
          drawScoreChanges.push({ score, change: firstOdds - lastOdds });
        }
      });
    } else if (handicapNum === 2) {
      const targetScores = ['1:3', '2:4', '3:5'];
      targetScores.forEach(score => {
        const firstOdds = first.awayWins?.find(s => s.score === score)?.odds;
        const lastOdds = last.awayWins?.find(s => s.score === score)?.odds;
        if (firstOdds && lastOdds && firstOdds - lastOdds > 1.0) {
          drawScoreChanges.push({ score, change: firstOdds - lastOdds });
        }
      });
    }
    
    return drawScoreChanges.length > 0;
  }

  _hasHandicapWinScoreDrop(scoresHistory, handicap) {
    if (!scoresHistory || scoresHistory.length < 2) return false;
    
    const handicapNum = parseInt(handicap);
    const first = scoresHistory[0];
    const last = scoresHistory[scoresHistory.length - 1];
    
    if (handicapNum === -1) {
      const winScores = ['2:0', '3:0', '3:1', '4:0', '4:1', '5:0', '5:1'];
      let bigDropCount = 0;
      let totalDropCount = 0;
      winScores.forEach(score => {
        const firstOdds = first.homeWins?.find(s => s.score === score)?.odds;
        const lastOdds = last.homeWins?.find(s => s.score === score)?.odds;
        if (firstOdds && lastOdds) {
          const change = firstOdds - lastOdds;
          if (change > 0) {
            totalDropCount++;
            if (change > 2.0) {
              bigDropCount++;
            }
          }
        }
      });
      
      return bigDropCount >= 1 || totalDropCount >= 2;
    }
    
    return false;
  }

  _isDrawScoreDropDominant(scoresHistory, handicap) {
    if (!scoresHistory || scoresHistory.length < 2) return false;
    
    const handicapNum = parseInt(handicap);
    const first = scoresHistory[0];
    const last = scoresHistory[scoresHistory.length - 1];
    
    if (handicapNum === -1) {
      const drawScore = first.draws?.find(s => s.score === '1:1');
      const drawScoreLast = last.draws?.find(s => s.score === '1:1');
      const drawScore00 = first.draws?.find(s => s.score === '0:0');
      const drawScore00Last = last.draws?.find(s => s.score === '0:0');
      const drawScore22 = first.draws?.find(s => s.score === '2:2');
      const drawScore22Last = last.draws?.find(s => s.score === '2:2');
      
      const drawScoreChange = drawScore && drawScoreLast ? drawScore.odds - drawScoreLast.odds : 0;
      const drawScore00Change = drawScore00 && drawScore00Last ? drawScore00.odds - drawScore00Last.odds : 0;
      const drawScore22Change = drawScore22 && drawScore22Last ? drawScore22.odds - drawScore22Last.odds : 0;
      
      const mainDrawsDown = drawScoreChange > 0.8 || drawScore00Change > 0.8;
      const otherDrawsDown = drawScore22Change > 0.8;
      const mainDrawsUp = drawScoreChange < -0.5 || drawScore00Change < -0.5;
      
      if (mainDrawsDown || (otherDrawsDown && !mainDrawsUp)) {
        return true;
      }
    } else if (handicapNum === 1) {
      const drawScore = first.draws?.find(s => s.score === '1:1');
      const drawScoreLast = last.draws?.find(s => s.score === '1:1');
      
      const drawScoreChange = drawScore && drawScoreLast ? drawScore.odds - drawScoreLast.odds : 0;
      
      if (drawScoreChange > 1.0) {
        return true;
      }
    }
    
    return false;
  }

  analyzeWinDrawLossOdds(oddsHistory) {
    if (!oddsHistory || oddsHistory.length < 2) {
      return { status: 'insufficient_data', prediction: null, changes: [] };
    }

    const first = oddsHistory[0];
    const last = oddsHistory[oddsHistory.length - 1];

    const changes = [];
    ['winA', 'draw', 'winB'].forEach(type => {
      if (typeof first[type] === 'number' && typeof last[type] === 'number') {
        const change = first[type] - last[type];
        const percentageChange = (change / first[type]) * 100;
        
        let priorityScore = 0;
        const oddsLevel = first[type];
        
        if (change > 0) {
          priorityScore = (change / oddsLevel) * (10 / oddsLevel) + (10 / oddsLevel);
        } else if (Math.abs(change) < 0.2) {
          priorityScore = 10 / oddsLevel + (0.1 / oddsLevel);
        }
        
        changes.push({
          result: type,
          initial: first[type],
          final: last[type],
          change: change,
          percentageChange: percentageChange,
          direction: change > 0 ? 'decrease' : change < 0 ? 'increase' : 'stable',
          priorityScore: priorityScore
        });
      }
    });

    changes.sort((a, b) => b.priorityScore - a.priorityScore);
    
    const topDecrease = changes.find(c => c.direction === 'decrease');
    const prediction = topDecrease ? topDecrease.result : null;

    return {
      status: 'success',
      changes: changes,
      prediction: prediction,
      analysis: this._generateWinDrawLossAnalysis(changes),
      initialOdds: first,
      finalOdds: last
    };
  }

  _generateWinDrawLossAnalysis(changes) {
    const decrease = changes.filter(c => c.direction === 'decrease');
    const increase = changes.filter(c => c.direction === 'increase');
    
    if (decrease.length === 0) return '胜平负赔率变化不明显';
    
    const resultNames = { winA: '主队胜', draw: '平局', winB: '客队胜' };
    const top = decrease[0];
    
    const analysis = [`${resultNames[top.result]}赔率下降幅度最大(${top.change.toFixed(2)})`];
    
    increase.forEach(c => {
      analysis.push(`${resultNames[c.result]}赔率上升(${c.change.toFixed(2)}绝对值)，不看好`);
    });
    
    return analysis.join('；');
  }

  predictMatch(oddsData) {
    let awayWinOddsLow = false;
    if (oddsData.winDrawLoss && oddsData.winDrawLoss.length >= 2) {
      const firstB = oddsData.winDrawLoss[0].winB;
      const lastB = oddsData.winDrawLoss[oddsData.winDrawLoss.length - 1].winB;
      if (firstB < 1.50 || lastB < 1.50) {
        awayWinOddsLow = true;
      }
    }
    
    const handicapResult = this.analyzeHandicapOdds(oddsData.handicap, oddsData.handicapValue || '-1', oddsData.winDrawLoss, oddsData.scores);
    const totalGoalsResult = this.analyzeTotalGoalsOdds(oddsData.totalGoals, awayWinOddsLow, oddsData.handicapValue, oddsData.winDrawLoss);
    const scoreResult = this.analyzeScoreOdds(oddsData.scores, totalGoalsResult.targetGoals, awayWinOddsLow, handicapResult.prediction, oddsData.handicapValue);
    const wdlResult = this.analyzeWinDrawLossOdds(oddsData.winDrawLoss);

    const combinedPrediction = this._combinePredictions({
      totalGoals: totalGoalsResult,
      score: scoreResult,
      handicap: handicapResult,
      winDrawLoss: wdlResult
    });

    return {
      totalGoals: totalGoalsResult,
      score: scoreResult,
      handicap: handicapResult,
      winDrawLoss: wdlResult,
      combinedPrediction: combinedPrediction,
      timestamp: new Date().toISOString()
    };
  }

  _combinePredictions(results) {
    const finalScores = [];
    const analysis = [];
    const handicapValue = results.handicap.handicap || '-1';
    const handicapNum = parseInt(handicapValue);
    const targetGoals = results.totalGoals.targetGoals;

    if (results.totalGoals.targetGoals.length > 0) {
      analysis.push(results.totalGoals.analysis);
    }

    const _validateScoreForHandicap = (score, prediction) => {
      const [home, away] = score.split(':').map(Number);
      const goalDiff = home - away;
      const total = home + away;
      
      const isDrawScore = home === away;
      
      if (targetGoals.length > 0 && !targetGoals.includes(total)) {
        if (prediction === 'handicap_lose' && isDrawScore) {
        } else if (prediction === 'handicap_win' && handicapNum >= 2 && away === 1 && home === 0) {
        } else if (prediction === 'handicap_win' && handicapNum >= 2 && total <= 2) {
        } else if (prediction === 'handicap_draw' && handicapNum === -1 && goalDiff === 1) {
        } else if (prediction === 'handicap_draw' && handicapNum === 1 && goalDiff === -1) {
        } else if (prediction === 'handicap_draw' && handicapNum === -2 && goalDiff === 2) {
        } else if (prediction === 'handicap_draw' && handicapNum === 2 && goalDiff === -2) {
        } else {
          return false;
        }
      }
      
      const absHandicap = Math.abs(handicapNum);
      
      if (prediction === 'handicap_win') {
        if (handicapNum < 0) {
          return goalDiff >= absHandicap + 1;
        } else {
          return goalDiff >= -absHandicap + 1;
        }
      } else if (prediction === 'handicap_draw') {
        if (handicapNum < 0) {
          return goalDiff === absHandicap;
        } else {
          return goalDiff === -handicapNum;
        }
      } else if (prediction === 'handicap_lose') {
        if (handicapNum < 0) {
          return goalDiff <= absHandicap - 1;
        } else {
          return goalDiff <= -absHandicap - 1;
        }
      }
      return true;
    };

    const _findValidScores = (allScores, prediction) => {
      const valid = [];
      allScores.forEach(s => {
        if (!s.score.includes('其它') && _validateScoreForHandicap(s.score, prediction)) {
          valid.push(s);
        }
      });
      valid.sort((a, b) => {
        const [homeA, awayA] = a.score.split(':').map(Number);
        const [homeB, awayB] = b.score.split(':').map(Number);
        const totalA = homeA + awayA;
        const totalB = homeB + awayB;
        
        const scoreAinTarget = targetGoals.includes(totalA);
        const scoreBinTarget = targetGoals.includes(totalB);
        
        if (scoreAinTarget && !scoreBinTarget) return -1;
        if (!scoreAinTarget && scoreBinTarget) return 1;
        
        if (scoreAinTarget && scoreBinTarget) {
          if (totalA !== totalB) return totalB - totalA;
        }
        
        return a.final - b.final;
      });
      return valid;
    };

    if (results.score.targetScores.length > 0) {
      analysis.push(results.score.analysis);
      
      const targetScores = results.score.targetScores;
      const handicapPrediction = results.handicap.prediction;
      
      if (handicapPrediction) {
        const validScores = targetScores.filter(s => _validateScoreForHandicap(s.score, handicapPrediction));
        
        if (validScores.length > 0) {
          validScores.sort((a, b) => {
            const [homeA, awayA] = a.score.split(':').map(Number);
            const [homeB, awayB] = b.score.split(':').map(Number);
            const totalA = homeA + awayA;
            const totalB = homeB + awayB;
            const goalDiffA = homeA - awayA;
            const goalDiffB = homeB - awayB;
            
            const scoreAinTarget = targetGoals.includes(totalA);
            const scoreBinTarget = targetGoals.includes(totalB);
            
            if (handicapNum >= 2 && handicapPrediction === 'handicap_win') {
              const isSmallWinA = awayA - homeA === 1;
              const isSmallWinB = awayB - homeB === 1;
              if (isSmallWinA && !isSmallWinB) return -1;
              if (!isSmallWinA && isSmallWinB) return 1;
              if (isSmallWinA && isSmallWinB) return totalA - totalB;
            }
            
            if (handicapNum === -1 && handicapPrediction === 'handicap_lose') {
              const isSmallAwayWinA = awayA - homeA === 1;
              const isSmallAwayWinB = awayB - homeB === 1;
              if (isSmallAwayWinA && !isSmallAwayWinB) return -1;
              if (!isSmallAwayWinA && isSmallAwayWinB) return 1;
              if (isSmallAwayWinA && isSmallAwayWinB) return totalA - totalB;
            }
            
            if (handicapNum === 1 && handicapPrediction === 'handicap_draw') {
              const isSmallAwayWinA = awayA - homeA === 1;
              const isSmallAwayWinB = awayB - homeB === 1;
              if (isSmallAwayWinA && !isSmallAwayWinB) return -1;
              if (!isSmallAwayWinA && isSmallAwayWinB) return 1;
              if (isSmallAwayWinA && isSmallAwayWinB) return totalB - totalA;
            }
            
            if (scoreAinTarget && !scoreBinTarget) return -1;
            if (!scoreAinTarget && scoreBinTarget) return 1;
            
            if (scoreAinTarget && scoreBinTarget) {
              if (handicapNum < 0 && handicapPrediction === 'handicap_win') {
                if (goalDiffA !== goalDiffB) return goalDiffA - goalDiffB;
              } else {
                if (totalA !== totalB) return totalB - totalA;
              }
            } else if (scoreAinTarget && !scoreBinTarget) {
              return -1;
            } else if (!scoreAinTarget && scoreBinTarget) {
              return 1;
            }
            
            if (handicapNum === 1 && handicapPrediction === 'handicap_draw') {
              const isSmallAwayWinA = awayA - homeA === 1;
              const isSmallAwayWinB = awayB - homeB === 1;
              if (isSmallAwayWinA && isSmallAwayWinB) {
                if (totalA !== totalB) return totalB - totalA;
              }
            }
            
            const priorityDiff = b.priorityScore - a.priorityScore;
            if (priorityDiff !== 0) return priorityDiff;
            
            return a.final - b.final;
          });
          finalScores.push(...validScores.map(s => s.score));
        } else {
          let fallbackScores = [];
          
          if (handicapPrediction === 'handicap_lose' && results.score.allChanges && results.score.allChanges.length > 0) {
            const drawScores = results.score.allChanges.filter(s => {
              const [home, away] = s.score.split(':').map(Number);
              return home === away && !s.score.includes('其它');
            });
            drawScores.sort((a, b) => {
              const [homeA, awayA] = a.score.split(':').map(Number);
              const [homeB, awayB] = b.score.split(':').map(Number);
              const totalA = homeA + awayA;
              const totalB = homeB + awayB;
              
              const scoreAinTarget = targetGoals.includes(totalA);
              const scoreBinTarget = targetGoals.includes(totalB);
              
              if (scoreAinTarget && !scoreBinTarget) return -1;
              if (!scoreAinTarget && scoreBinTarget) return 1;
              
              return a.final - b.final;
            });
            
            const validDraws = drawScores.filter(s => _validateScoreForHandicap(s.score, handicapPrediction));
            if (validDraws.length > 0) {
              fallbackScores.push(...validDraws.slice(0, 2));
            }
          }
          
          if (fallbackScores.length === 0 && results.score.allChanges && results.score.allChanges.length > 0) {
            fallbackScores = _findValidScores(results.score.allChanges, handicapPrediction);
          }
          
          if (fallbackScores.length > 0) {
            finalScores.push(...fallbackScores.slice(0, 3).map(s => s.score));
            analysis.push(`候选比分不满足让球约束，使用满足条件的备选比分：${finalScores.join('、')}`);
          } else {
            finalScores.push(...targetScores.map(s => s.score));
          }
        }
      } else {
        finalScores.push(...targetScores.map(s => s.score));
      }
    }

    if (results.handicap.analysis) {
      analysis.push(results.handicap.analysis);
    }

    let finalScore = null;
    if (finalScores.length > 0) {
      finalScore = finalScores[0];
      if (finalScores.length > 1) {
        finalScore = finalScores.slice(0, 2).join('、');
      }
      analysis.push(`综合分析，预测比分：${finalScore}`);
    }

    return {
      finalScores: finalScores.slice(0, 3),
      analysis: analysis.join('；'),
      finalScore: finalScore,
      handicapPrediction: results.handicap.prediction,
      winDrawLossPrediction: results.winDrawLoss.prediction
    };
  }

  calculateImpliedProbability(odds) {
    return 1 / odds;
  }

  calculateOverround(winOdds, drawOdds, loseOdds) {
    return (1 / winOdds) + (1 / drawOdds) + (1 / loseOdds);
  }

  normalizeProbabilities(winOdds, drawOdds, loseOdds) {
    const overround = this.calculateOverround(winOdds, drawOdds, loseOdds);
    return {
      win: (1 / winOdds) / overround,
      draw: (1 / drawOdds) / overround,
      lose: (1 / loseOdds) / overround
    };
  }

  getOddsSignal(initialOdds, finalOdds) {
    const change = initialOdds - finalOdds;
    const percentageChange = (change / initialOdds) * 100;
    
    if (percentageChange > 15) return 'strong_buy';
    if (percentageChange > 5) return 'buy';
    if (percentageChange > 0) return 'weak_buy';
    if (percentageChange > -5) return 'weak_sell';
    if (percentageChange > -15) return 'sell';
    return 'strong_sell';
  }

  generateFactors() {
    return {
      odds_trend_score: 0,
      odds_trend_total_goals: 0,
      odds_trend_handicap: 0,
      market_signal: '',
      odds_confidence: 0
    };
  }
}

export default OddsAnalyzer;