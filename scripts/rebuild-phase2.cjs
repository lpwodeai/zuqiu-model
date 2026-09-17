const fs = require('fs');

let content = fs.readFileSync('assets/model-engine.js', 'utf8');
let lines = content.split('\n');

// Find where TEAMS definition starts and ends
let teamsStart = -1;
let teamsEnd = -1;
for (let i = 0; i < lines.length; i++) {
  if (teamsStart === -1 && lines[i].match(/^\s*var TEAMS = \{/)) {
    teamsStart = i;
  }
  if (teamsStart !== -1 && teamsEnd === -1) {
    // Count braces to find end
    let braceCount = 0;
    for (let j = i; j < lines.length; j++) {
      for (let k = 0; k < lines[j].length; k++) {
        if (lines[j][k] === '{') braceCount++;
        if (lines[j][k] === '}') {
          braceCount--;
          if (braceCount === 0 && j > teamsStart) {
            teamsEnd = j;
            break;
          }
        }
      }
      if (teamsEnd > 0) break;
    }
    break;
  }
}

console.log('TEAMS block: lines ' + (teamsStart+1) + ' to ' + (teamsEnd+1));

// Replace TEAMS with empty object that will be populated by init()
// But keep it as is for now - we'll use init to load from JSON
// Actually, let's keep the existing TEAMS as fallback but they are national teams
// Better: replace with empty and rely on init() + fallback loading

// For now, let's keep TEAMS as fallback (they'll still work for testing)
// and add the delegation for other modules

// Step 1: Find where to add module delegation (near MathUtils)
let mathUtilsLine = -1;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].includes('FiveLeagues_MathUtils')) {
    mathUtilsLine = i;
    break;
  }
}

console.log('MathUtils at line ' + (mathUtilsLine+1));

// Add other module delegations after MathUtils
const moduleDelegations = `
  var ROI = (typeof FiveLeagues_ROI !== 'undefined') ? FiveLeagues_ROI : null;
  var Calibration = (typeof FiveLeagues_Calibration !== 'undefined') ? FiveLeagues_Calibration : null;
  var QualityControl = (typeof FiveLeagues_QualityControl !== 'undefined') ? FiveLeagues_QualityControl : null;
  var SquadParser = (typeof FiveLeagues_SquadParser !== 'undefined') ? FiveLeagues_SquadParser : null;
  var TacticalAnalyzer = (typeof FiveLeagues_TacticalAnalyzer !== 'undefined') ? FiveLeagues_TacticalAnalyzer : null;
  var PredictionUtils = (typeof FiveLeagues_PredictionUtils !== 'undefined') ? FiveLeagues_PredictionUtils : null;
`;

lines.splice(mathUtilsLine + 1, 0, moduleDelegations);

// Step 2: Add ELO_RATINGS and SSM_STATE exports (dashboard/mobile may need them)
// Find ELO_RATINGS definition
let eloStart = -1;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*var ELO_RATINGS = \{/)) {
    eloStart = i;
    break;
  }
}

console.log('ELO_RATINGS at line ' + (eloStart+1));

// Step 3: Find the finalAPI section and add delegated functions
// Find where finalAPI exports are
let finalApiExports = -1;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].includes('finalAPI.init = init;')) {
    finalApiExports = i;
    break;
  }
}

console.log('finalAPI exports at line ' + (finalApiExports+1));

// Add ELO_RATINGS and SSM_STATE to finalAPI
const extraExports = `  finalAPI.ELO_RATINGS = ELO_RATINGS;
  finalAPI.SSM_STATE = SSM_STATE;
  finalAPI.TEAMS = TEAMS;
  
  // Math delegations
  finalAPI.poissonPMF = poissonPMF;
  finalAPI.factorial = factorial;
  finalAPI.bivariatePoissonPMF = bivariatePoissonPMF;
  
  // Share card (stub for compatibility)
  finalAPI.generateShareCard = function(teamAName, teamBName, prediction) {
    return {
      title: teamAName + ' vs ' + teamBName,
      prediction: prediction,
      timestamp: new Date().toISOString()
    };
  };
`;

lines.splice(finalApiExports + 2, 0, extraExports);

// Step 4: Wrap some functions to delegate to modules
// For squad parser functions
// We'll add delegation at the top of parseSquadDepth
for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function parseSquadDepth\(/)) {
    lines[i] = lines[i] + '\n    if (SquadParser) return SquadParser.parseSquadDepth(homeSquad, awaySquad, TEAMS);';
    break;
  }
}

for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function applySquadDepthAdjustment\(/)) {
    lines[i] = lines[i] + '\n    if (SquadParser) return SquadParser.applySquadDepthAdjustment(teamAKey, teamBKey, homeSquad, awaySquad, TEAMS, options);';
    break;
  }
}

for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function restoreTeamOriginalStats\(/)) {
    lines[i] = lines[i] + '\n    if (SquadParser) return SquadParser.restoreTeamOriginalStats(teamAKey, teamBKey, TEAMS);';
    break;
  }
}

// Tactical analyzer delegations
for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function parseRecentForm\(/)) {
    lines[i] = lines[i] + '\n    if (TacticalAnalyzer) return TacticalAnalyzer.parseRecentForm(recentFormData, TEAMS);';
    break;
  }
}

for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function parseTacticalIntel\(/)) {
    lines[i] = lines[i] + '\n    if (TacticalAnalyzer) return TacticalAnalyzer.parseTacticalIntel(tacticalData);';
    break;
  }
}

for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function parseHistoricalH2H\(/)) {
    lines[i] = lines[i] + '\n    if (TacticalAnalyzer) return TacticalAnalyzer.parseHistoricalH2H(h2hData);';
    break;
  }
}

for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function parsePlayerStats\(/)) {
    lines[i] = lines[i] + '\n    if (TacticalAnalyzer) return TacticalAnalyzer.parsePlayerStats(playerStats);';
    break;
  }
}

for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function parseWeather\(/)) {
    lines[i] = lines[i] + '\n    if (TacticalAnalyzer) return TacticalAnalyzer.parseWeather(weatherData);';
    break;
  }
}

// kellyCriterion delegation
for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function kellyCriterion\(/)) {
    lines[i] = lines[i] + '\n    if (ROI) return ROI.kellyCriterion(probability, odds, bankroll, fraction);';
    break;
  }
}

// calculateImpliedProbabilities delegation
for (let i = 0; i < lines.length; i++) {
  if (lines[i].match(/^\s*function calculateImpliedProbabilities\(/)) {
    lines[i] = lines[i] + '\n    if (PredictionUtils) return PredictionUtils.calculateImpliedProbabilities(odds);';
    break;
  }
}

content = lines.join('\n');
fs.writeFileSync('assets/model-engine.js', content, 'utf8');

console.log('Done. Total lines:', lines.length);
