const fs = require('fs');

let content = fs.readFileSync('assets/model-engine.js', 'utf8');

// Step 1: Rename namespace WC2026 → FiveLeaguesEngine
content = content.replace(/WC2026_TEAMS/g, 'FiveLeagues_TEAMS');
content = content.replace(/WC2026/g, 'FiveLeaguesEngine');

// Step 2: Update version comment header
content = content.replace(
  "2026 FIFA World Cup Prediction Engine v4.8",
  "Five Leagues Prediction Engine v8.0"
);
content = content.replace(
  /完整2026 FIFA赛制规则更新 — 小组同分排名规则.*?\n/,
  "五大联赛专属预测引擎 — 英超/德甲/法甲/意甲/西甲\n"
);

// Step 3: Add async init() function and isInitialized flag
// Find the beginning of finalAPI section and add init before it
// First, let's find where finalAPI definition
const finalApiIndex = content.indexOf('var finalAPI = {}');
if (finalApiIndex === -1) {
  console.log('ERROR: finalAPI not found');
  process.exit(1);
}

// Build the init function
const initCode = `
  // ─── 异步初始化 ───
  var _initialized = false;
  var _initPromise = null;
  
  function loadTeamAttributes() {
    if (typeof FiveLeagues_DataLoader !== 'undefined' && FiveLeagues_DataLoader.isInitialized()) {
      TEAMS = FiveLeagues_DataLoader.getAllTeams() || {};
      return Promise.resolve(TEAMS);
    }
    return new Promise(function(resolve, reject) {
      fetch('assets/team_attributes.json')
        .then(function(response) { return response.json(); })
        .then(function(data) {
          TEAMS = data;
          if (typeof FiveLeagues_DataLoader !== 'undefined') {
            FiveLeagues_DataLoader.setTeams(data);
          }
          resolve(data);
        })
        .catch(function(e) {
          console.error('Failed to load team attributes:', e.message);
          resolve({});
        });
    });
  }
  
  function loadModelConfig() {
    if (typeof FiveLeagues_DataLoader !== 'undefined' && FiveLeagues_DataLoader.isInitialized()) {
      CONFIG = FiveLeagues_DataLoader.getConfig() || {};
      return Promise.resolve(CONFIG);
    }
    return new Promise(function(resolve, reject) {
      fetch('assets/model_config.json')
        .then(function(response) { return response.json(); })
        .then(function(data) {
          CONFIG = data;
          if (typeof FiveLeagues_DataLoader !== 'undefined') {
            FiveLeagues_DataLoader.setConfig(data);
          }
          resolve(data);
        })
        .catch(function(e) {
          console.warn('Failed to load model config:', e.message);
          resolve({});
        });
    });
  }
  
  function init(callback) {
    if (_initPromise) {
      if (callback) _initPromise.then(callback);
      return _initPromise;
    }
    
    _initPromise = Promise.all([
      loadTeamAttributes(),
      loadModelConfig()
    ]).then(function() {
      _initialized = true;
      console.log('[FiveLeaguesEngine] 初始化完成，球队数:', Object.keys(TEAMS).length);
    });
    
    if (callback) _initPromise.then(callback);
    return _initPromise;
  }
  
  function isInitialized() {
    return _initialized;
  }
  
  var finalAPI = {};
`;

content = content.replace('var finalAPI = {};', initCode);

// Step 4: Add init and isInitialized to finalAPI exports
// Find the return finalAPI line and add exports before it
const returnFinalApiIndex = content.indexOf('return finalAPI;');
if (returnFinalApiIndex === -1) {
  console.log('ERROR: return finalAPI not found');
} else {
  const exportCode = `
  finalAPI.init = init;
  finalAPI.isInitialized = isInitialized;
  
  return finalAPI;`;
  content = content.replace(/\s*return finalAPI;/, exportCode);
}

// Step 5: Add MathUtils delegation for PMF functions
// Find the poissonPMF function and add delegation
const mathUtilsCode = `
  // ─── 数学工具委托 ───
  var MathUtils = (typeof FiveLeagues_MathUtils !== 'undefined') ? FiveLeagues_MathUtils : null;
`;

// Insert MathUtils after CONFIG variable
const configMatch = content.match(/var CONFIG = \{[^}]*\};/);
if (configMatch) {
  content = content.replace(configMatch[0], configMatch[0] + '\n' + mathUtilsCode);
}

// Wrap poissonPMF to delegate
content = content.replace(
  /function poissonPMF\(k, lambda\) \{/,
  'function poissonPMF(k, lambda) {\n    if (MathUtils) return MathUtils.poissonPMF(k, lambda);'
);

// Wrap factorial to delegate
content = content.replace(
  /function factorial\(n\) \{/,
  'function factorial(n) {\n    if (MathUtils) return MathUtils.factorial(n);'
);

// Wrap bivariatePoissonPMF to delegate
content = content.replace(
  /function bivariatePoissonPMF\(k, m, lambdaA, lambdaB, rho\) \{/,
  'function bivariatePoissonPMF(k, m, lambdaA, lambdaB, rho) {\n    if (MathUtils) return MathUtils.bivariatePoissonPMF(k, m, lambdaA, lambdaB, rho);'
);

fs.writeFileSync('assets/model-engine.js', content, 'utf8');
console.log('Rebuild complete');
console.log('Size:', fs.statSync('assets/model-engine.js').size);
