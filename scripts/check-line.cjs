const fs = require('fs');
let lines = fs.readFileSync('assets/model-engine.js', 'utf8').split('\n');

let lineNum = 239;
let line = lines[lineNum - 1];

console.log('Line ' + lineNum + ':');
console.log(line.substring(0, 200));
console.log('---');
console.log('Total lines');
for (let i = Math.max(0, lineNum-5); i < lineNum+5; i++) {
  console.log((i+1) + ': ' + lines[i].substring(0, 120));
}