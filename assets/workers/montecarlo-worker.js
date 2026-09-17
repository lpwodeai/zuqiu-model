var isRunning = false;
var shouldStop = false;

self.addEventListener('message', function(e) {
  var data = e.data;
  
  if (data.type === 'start') {
    isRunning = true;
    shouldStop = false;
    runMonteCarlo(data.params);
  } else if (data.type === 'stop') {
    shouldStop = true;
    isRunning = false;
  }
});

function runMonteCarlo(params) {
  var lambdaA = params.lambdaA;
  var lambdaB = params.lambdaB;
  var iterations = params.iterations || 10000;
  var reportInterval = params.reportInterval || 1000;
  
  var results = {
    winsA: 0,
    draws: 0,
    winsB: 0,
    scoreMatrix: {},
    totalGoals: {},
    halfTimeResults: {}
  };
  
  for (var i = 0; i < iterations; i++) {
    if (shouldStop) {
      self.postMessage({ type: 'stopped' });
      return;
    }
    
    var goalsA = poissonRandom(lambdaA);
    var goalsB = poissonRandom(lambdaB);
    
    var htGoalsA = Math.floor(goalsA * (0.4 + Math.random() * 0.2));
    var htGoalsB = Math.floor(goalsB * (0.4 + Math.random() * 0.2));
    
    var scoreKey = goalsA + '-' + goalsB;
    var htScoreKey = htGoalsA + '-' + htGoalsB;
    var totalG = goalsA + goalsB;
    
    results.scoreMatrix[scoreKey] = (results.scoreMatrix[scoreKey] || 0) + 1;
    results.halfTimeResults[htScoreKey] = (results.halfTimeResults[htScoreKey] || 0) + 1;
    results.totalGoals[totalG] = (results.totalGoals[totalG] || 0) + 1;
    
    if (goalsA > goalsB) results.winsA++;
    else if (goalsA === goalsB) results.draws++;
    else results.winsB++;
    
    if ((i + 1) % reportInterval === 0) {
      var progress = ((i + 1) / iterations) * 100;
      self.postMessage({
        type: 'progress',
        progress: progress.toFixed(1),
        partialResults: {
          winsA: results.winsA,
          draws: results.draws,
          winsB: results.winsB,
          iterations: i + 1
        }
      });
    }
  }
  
  for (var key in results.scoreMatrix) {
    results.scoreMatrix[key] /= iterations;
  }
  for (var key in results.halfTimeResults) {
    results.halfTimeResults[key] /= iterations;
  }
  for (var key in results.totalGoals) {
    results.totalGoals[key] /= iterations;
  }
  
  results.winAProb = results.winsA / iterations;
  results.drawProb = results.draws / iterations;
  results.winBProb = results.winsB / iterations;
  
  self.postMessage({
    type: 'complete',
    results: results,
    iterations: iterations
  });
  
  isRunning = false;
}

function poissonRandom(lambda) {
  var L = Math.exp(-lambda);
  var k = 0;
  var p = 1;
  
  do {
    k++;
    p *= Math.random();
  } while (p > L);
  
  return k - 1;
}