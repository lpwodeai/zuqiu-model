const fs = require('fs');
const acorn = require('acorn');

let content = fs.readFileSync('assets/model-engine.js', 'utf8');
let lines = content.split('\n');

function checkSyntax(code) {
  try {
    acorn.parse(code, { ecmaVersion: 2020 });
    return { ok: true, error: null };
  } catch (e) {
    return { ok: false, error: e, line: e.loc ? e.loc.line : null, column: e.loc ? e.loc.column : null };
  }
}

// Step 1: Find the first syntax error line
let result = checkSyntax(content);
if (result.ok) {
  console.log('File is syntactically correct!');
  process.exit(0);
}

console.log('First error at line ' + result.line + ', column ' + result.column);
console.log('Error: ' + result.error.message);

// Show the problematic area
for (let i = Math.max(0, result.line - 3); i < Math.min(lines.length, result.line + 3); i++) {
  console.log((i+1) + ': ' + lines[i].substring(0, 150));
}

// Count total errors by trying to fix and reparse
console.log('\n--- Analyzing errors... ---');
