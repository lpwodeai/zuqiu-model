/**
 * 五大联赛规则配置与校验服务
 * 覆盖英超、西甲、意甲、德甲、法甲
 */

const LEAGUE_RULES = {
  premier_league: {
    id: 'premier_league',
    name: '英格兰超级联赛',
    nameEn: 'Premier League',
    country: 'England',
    teams: 20,
    matchesPerTeam: 38,
    totalMatches: 380,
    pointsSystem: {
      win: 3,
      draw: 1,
      loss: 0
    },
    promotionRelegation: {
      promotion: 3,
      relegation: 3,
      playoffTeams: 4,
      playoffWinners: 1
    },
    europeanQualification: {
      championsLeague: 4,
      europaLeague: 2,
      europaConferenceLeague: 1,
      leagueCupWinner: 1,
      faCupWinner: 1
    },
    specialRules: {
      varUsage: true,
      varReviewTime: 60,
      videoAssistantReferee: true,
      yellowCardAccumulation: {
        threshold: 5,
        banMatches: 1,
        resetAfter: 19
      },
      redCardBan: {
        directRed: 3,
        secondYellow: 1
      },
      goalLineTechnology: true,
      fifthSubstitute: {
        enabled: true,
        extraTimeOnly: false
      },
      waterBreak: {
        enabled: true,
        temperatureThreshold: 28
      }
    },
    seasonStructure: {
      startMonth: 8,
      endMonth: 5,
      midSeasonBreak: false,
      cupMatches: ['FA Cup', 'League Cup', 'Community Shield']
    },
    weatherImpact: {
      snowGames: true,
      rainImpact: 'moderate',
      fogDelay: true,
      temperatureRange: [-5, 35]
    },
    stadiums: {
      avgCapacity: 37000,
      allSeater: true,
      grassType: 'natural'
    }
  },

  la_liga: {
    id: 'la_liga',
    name: '西班牙甲级联赛',
    nameEn: 'La Liga',
    country: 'Spain',
    teams: 20,
    matchesPerTeam: 38,
    totalMatches: 380,
    pointsSystem: {
      win: 3,
      draw: 1,
      loss: 0
    },
    promotionRelegation: {
      promotion: 2,
      relegation: 3,
      playoffTeams: 2,
      playoffWinners: 1
    },
    europeanQualification: {
      championsLeague: 4,
      europaLeague: 2,
      europaConferenceLeague: 1,
      copaDelReyWinner: 1
    },
    specialRules: {
      varUsage: true,
      varReviewTime: 60,
      videoAssistantReferee: true,
      yellowCardAccumulation: {
        threshold: 5,
        banMatches: 1,
        resetAfter: 19
      },
      redCardBan: {
        directRed: 3,
        secondYellow: 1
      },
      goalLineTechnology: true,
      fifthSubstitute: {
        enabled: true,
        extraTimeOnly: false
      },
      waterBreak: {
        enabled: true,
        temperatureThreshold: 30
      },
      foreignPlayerLimit: {
        limit: 3,
        euExempt: true
      }
    },
    seasonStructure: {
      startMonth: 8,
      endMonth: 5,
      midSeasonBreak: true,
      breakWeeks: 2,
      cupMatches: ['Copa del Rey', 'Supercopa de Espana']
    },
    weatherImpact: {
      snowGames: false,
      rainImpact: 'low',
      fogDelay: false,
      temperatureRange: [5, 38]
    },
    stadiums: {
      avgCapacity: 35000,
      allSeater: true,
      grassType: 'natural'
    }
  },

  serie_a: {
    id: 'serie_a',
    name: '意大利甲级联赛',
    nameEn: 'Serie A',
    country: 'Italy',
    teams: 20,
    matchesPerTeam: 38,
    totalMatches: 380,
    pointsSystem: {
      win: 3,
      draw: 1,
      loss: 0
    },
    promotionRelegation: {
      promotion: 2,
      relegation: 3,
      playoffTeams: 2,
      playoffWinners: 1
    },
    europeanQualification: {
      championsLeague: 4,
      europaLeague: 2,
      europaConferenceLeague: 1,
      coppaItaliaWinner: 1
    },
    specialRules: {
      varUsage: true,
      varReviewTime: 60,
      videoAssistantReferee: true,
      yellowCardAccumulation: {
        threshold: 5,
        banMatches: 1,
        resetAfter: 19
      },
      redCardBan: {
        directRed: 3,
        secondYellow: 1
      },
      goalLineTechnology: true,
      fifthSubstitute: {
        enabled: true,
        extraTimeOnly: false
      },
      waterBreak: {
        enabled: true,
        temperatureThreshold: 28
      }
    },
    seasonStructure: {
      startMonth: 8,
      endMonth: 5,
      midSeasonBreak: true,
      breakWeeks: 2,
      cupMatches: ['Coppa Italia', 'Supercoppa Italiana']
    },
    weatherImpact: {
      snowGames: true,
      rainImpact: 'moderate',
      fogDelay: true,
      temperatureRange: [-2, 35]
    },
    stadiums: {
      avgCapacity: 32000,
      allSeater: true,
      grassType: 'natural'
    }
  },

  bundesliga: {
    id: 'bundesliga',
    name: '德国足球甲级联赛',
    nameEn: 'Bundesliga',
    country: 'Germany',
    teams: 18,
    matchesPerTeam: 34,
    totalMatches: 306,
    pointsSystem: {
      win: 3,
      draw: 1,
      loss: 0
    },
    promotionRelegation: {
      promotion: 2,
      relegation: 2,
      playoffTeams: 2,
      playoffWinners: 1
    },
    europeanQualification: {
      championsLeague: 4,
      europaLeague: 2,
      europaConferenceLeague: 1,
      dfbPokalWinner: 1
    },
    specialRules: {
      varUsage: true,
      varReviewTime: 60,
      videoAssistantReferee: true,
      yellowCardAccumulation: {
        threshold: 5,
        banMatches: 1,
        resetAfter: 17
      },
      redCardBan: {
        directRed: 3,
        secondYellow: 1
      },
      goalLineTechnology: true,
      fifthSubstitute: {
        enabled: true,
        extraTimeOnly: false
      },
      waterBreak: {
        enabled: true,
        temperatureThreshold: 26
      },
      '50Plus1Rule': true,
      licenseRequirements: {
        financialFairPlay: true,
        stadiumCapacity: 15000
      }
    },
    seasonStructure: {
      startMonth: 8,
      endMonth: 5,
      midSeasonBreak: true,
      breakWeeks: 6,
      cupMatches: ['DFB-Pokal', 'DFL-Supercup']
    },
    weatherImpact: {
      snowGames: true,
      rainImpact: 'high',
      fogDelay: true,
      temperatureRange: [-10, 32]
    },
    stadiums: {
      avgCapacity: 45000,
      allSeater: true,
      grassType: 'natural'
    }
  },

  ligue_1: {
    id: 'ligue_1',
    name: '法国足球甲级联赛',
    nameEn: 'Ligue 1',
    country: 'France',
    teams: 20,
    matchesPerTeam: 38,
    totalMatches: 380,
    pointsSystem: {
      win: 3,
      draw: 1,
      loss: 0
    },
    promotionRelegation: {
      promotion: 2,
      relegation: 2,
      playoffTeams: 2,
      playoffWinners: 1
    },
    europeanQualification: {
      championsLeague: 4,
      europaLeague: 2,
      europaConferenceLeague: 1,
      coupeDeFranceWinner: 1,
      coupeDeLaLigueWinner: 0
    },
    specialRules: {
      varUsage: true,
      varReviewTime: 60,
      videoAssistantReferee: true,
      yellowCardAccumulation: {
        threshold: 5,
        banMatches: 1,
        resetAfter: 19
      },
      redCardBan: {
        directRed: 3,
        secondYellow: 1
      },
      goalLineTechnology: true,
      fifthSubstitute: {
        enabled: true,
        extraTimeOnly: false
      },
      waterBreak: {
        enabled: true,
        temperatureThreshold: 28
      },
      financialFairPlay: {
        enabled: true,
        spendingLimit: null
      }
    },
    seasonStructure: {
      startMonth: 8,
      endMonth: 5,
      midSeasonBreak: true,
      breakWeeks: 2,
      cupMatches: ['Coupe de France', 'Trophee des Champions']
    },
    weatherImpact: {
      snowGames: true,
      rainImpact: 'moderate',
      fogDelay: false,
      temperatureRange: [-5, 35]
    },
    stadiums: {
      avgCapacity: 28000,
      allSeater: true,
      grassType: 'natural'
    }
  }
};

function validateLeagueRules() {
  const validationResults = {};
  
  for (const [leagueId, rules] of Object.entries(LEAGUE_RULES)) {
    validationResults[leagueId] = {
      league: rules.name,
      checks: [],
      issues: []
    };

    validationResults[leagueId].checks.push({
      category: '积分制度',
      check: '胜场积分',
      expected: 3,
      actual: rules.pointsSystem.win,
      passed: rules.pointsSystem.win === 3
    });

    validationResults[leagueId].checks.push({
      category: '积分制度',
      check: '平局积分',
      expected: 1,
      actual: rules.pointsSystem.draw,
      passed: rules.pointsSystem.draw === 1
    });

    validationResults[leagueId].checks.push({
      category: '积分制度',
      check: '负场积分',
      expected: 0,
      actual: rules.pointsSystem.loss,
      passed: rules.pointsSystem.loss === 0
    });

    validationResults[leagueId].checks.push({
      category: '赛制结构',
      check: '球队数量验证',
      expected: rules.teams,
      actual: rules.teams,
      passed: rules.teams > 0
    });

    validationResults[leagueId].checks.push({
      category: '赛制结构',
      check: '单队比赛场数',
      expected: rules.matchesPerTeam,
      actual: rules.matchesPerTeam,
      passed: rules.matchesPerTeam > 0
    });

    validationResults[leagueId].checks.push({
      category: '赛制结构',
      check: '总比赛场数计算',
      expected: rules.totalMatches,
      actual: rules.teams * rules.matchesPerTeam / 2,
      passed: rules.totalMatches === rules.teams * rules.matchesPerTeam / 2
    });

    validationResults[leagueId].checks.push({
      category: '升降级规则',
      check: '降级球队数',
      expected: rules.promotionRelegation.relegation,
      actual: rules.promotionRelegation.relegation,
      passed: rules.promotionRelegation.relegation >= 2
    });

    validationResults[leagueId].checks.push({
      category: 'VAR规则',
      check: 'VAR使用',
      expected: true,
      actual: rules.specialRules.varUsage,
      passed: rules.specialRules.varUsage === true
    });

    validationResults[leagueId].checks.push({
      category: '红黄牌规则',
      check: '黄牌累计阈值',
      expected: 5,
      actual: rules.specialRules.yellowCardAccumulation.threshold,
      passed: rules.specialRules.yellowCardAccumulation.threshold === 5
    });

    validationResults[leagueId].checks.push({
      category: '红黄牌规则',
      check: '直接红牌停赛场次',
      expected: 3,
      actual: rules.specialRules.redCardBan.directRed,
      passed: rules.specialRules.redCardBan.directRed >= 3
    });

    validationResults[leagueId].checks.push({
      category: '特殊规则',
      check: '第五换人',
      expected: true,
      actual: rules.specialRules.fifthSubstitute.enabled,
      passed: rules.specialRules.fifthSubstitute.enabled === true
    });

    validationResults[leagueId].checks.push({
      category: '特殊规则',
      check: '水停',
      expected: true,
      actual: rules.specialRules.waterBreak.enabled,
      passed: rules.specialRules.waterBreak.enabled === true
    });

    validationResults[leagueId].checks.push({
      category: '欧冠资格',
      check: '欧冠名额',
      expected: 4,
      actual: rules.europeanQualification.championsLeague,
      passed: rules.europeanQualification.championsLeague === 4
    });

    validationResults[leagueId].checks.push({
      category: '天气影响',
      check: '温度范围有效性',
      expected: '有效范围',
      actual: `${rules.weatherImpact.temperatureRange[0]}~${rules.weatherImpact.temperatureRange[1]}°C`,
      passed: rules.weatherImpact.temperatureRange[0] < rules.weatherImpact.temperatureRange[1]
    });

    validationResults[leagueId].checks.push({
      category: '球场设施',
      check: '全座席',
      expected: true,
      actual: rules.stadiums.allSeater,
      passed: rules.stadiums.allSeater === true
    });

    const failedChecks = validationResults[leagueId].checks.filter(c => !c.passed);
    validationResults[leagueId].issues = failedChecks.map(c => `${c.category} - ${c.check}: 期望${c.expected}, 实际${c.actual}`);
    validationResults[leagueId].passRate = ((validationResults[leagueId].checks.length - failedChecks.length) / validationResults[leagueId].checks.length * 100).toFixed(1);
  }

  return validationResults;
}

function generateValidationReport() {
  const results = validateLeagueRules();
  const report = {
    generatedAt: new Date().toISOString(),
    leagues: Object.keys(LEAGUE_RULES).length,
    totalChecks: 0,
    passedChecks: 0,
    failedChecks: 0,
    summary: {},
    details: {}
  };

  for (const [leagueId, result] of Object.entries(results)) {
    report.totalChecks += result.checks.length;
    report.passedChecks += result.checks.filter(c => c.passed).length;
    report.failedChecks += result.checks.filter(c => !c.passed).length;

    report.summary[leagueId] = {
      name: result.league,
      passRate: result.passRate,
      issuesCount: result.issues.length,
      status: result.issues.length === 0 ? '通过' : result.issues.length <= 2 ? '警告' : '需调整'
    };

    report.details[leagueId] = {
      league: result.league,
      passRate: result.passRate,
      issues: result.issues,
      checks: result.checks
    };
  }

  report.overallPassRate = ((report.passedChecks / report.totalChecks) * 100).toFixed(1);

  return report;
}

function getLeagueRules(leagueId) {
  return LEAGUE_RULES[leagueId] || null;
}

function getAllLeagues() {
  return Object.values(LEAGUE_RULES);
}

function getLeagueIds() {
  return Object.keys(LEAGUE_RULES);
}

function getLeaguePointsSystem(leagueId) {
  const rules = LEAGUE_RULES[leagueId];
  return rules ? rules.pointsSystem : null;
}

function getLeaguePromotionRelegation(leagueId) {
  const rules = LEAGUE_RULES[leagueId];
  return rules ? rules.promotionRelegation : null;
}

function getLeagueSpecialRules(leagueId) {
  const rules = LEAGUE_RULES[leagueId];
  return rules ? rules.specialRules : null;
}

function getWeatherImpact(leagueId) {
  const rules = LEAGUE_RULES[leagueId];
  return rules ? rules.weatherImpact : null;
}

export {
  LEAGUE_RULES,
  validateLeagueRules,
  generateValidationReport,
  getLeagueRules,
  getAllLeagues,
  getLeagueIds,
  getLeaguePointsSystem,
  getLeaguePromotionRelegation,
  getLeagueSpecialRules,
  getWeatherImpact
};