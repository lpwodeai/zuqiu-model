const fs = require('fs');
let content = fs.readFileSync('assets/model-engine.js', 'utf8');
let lines = content.split('\n');

// Find LEAGUE_TIER object
let startLine = -1;
for (let i = 0; i < lines.length; i++) {
  if (lines[i].includes('var LEAGUE_TIER')) {
    startLine = i;
    break;
  }
}

console.log('LEAGUE_TIER starts at line ' + (startLine + 1));

// Find where LEAGUE_TIER ends (next variable or function)
let endLine = -1;
let braceCount = 0;
for (let i = startLine; i < lines.length; i++) {
  for (let j = 0; j < lines[i].length; j++) {
    if (lines[i][j] === '{') braceCount++;
    if (lines[i][j] === '}') {
      braceCount--;
      if (braceCount === 0) {
        endLine = i;
        break;
      }
    }
  }
  if (endLine > 0) break;
}

console.log('LEAGUE_TIER ends at line ' + (endLine + 1));
console.log('Total lines: ' + (endLine - startLine + 1));
console.log('');
console.log('--- Lines around start ---');
for (let i = startLine; i < Math.min(startLine + 15, lines.length); i++) {
  console.log((i+1) + ': ' + lines[i].substring(0, 100));
}
console.log('');
console.log('--- Lines around end ---');
for (let i = Math.max(0, endLine - 5); i < Math.min(endLine + 5, lines.length); i++) {
  console.log((i+1) + ': ' + lines[i].substring(0, 100));
}
