const fs = require('fs');
let lines = fs.readFileSync('assets/model-engine.js', 'utf8').split('\n');

// Find loadTeamAttributes function
for (let i = 25; i < 60; i++) {
  console.log((i+1) + ': ' + lines[i].substring(0, 100));
}
