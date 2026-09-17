const fs = require('fs');
let lines = fs.readFileSync('assets/model-engine.js', 'utf8').split('\n');

for (let i = 35; i < 55; i++) {
  console.log((i+1) + ': ' + lines[i].substring(0, 120));
}
