const fs = require('fs');
const acorn = require('acorn');

// Start from the broken backup
let content = fs.readFileSync('assets/model-engine.broken.js', 'utf8');
let lines = content.split('\n');

console.log('Starting from broken backup, total lines: ' + lines.length);

// Strategy: Find ALL functions and check each for syntax validity.
// Invalid functions get replaced with stubs.
// Also fix object/array literals that fail syntax.

function checkCode(code) {
  try {
    acorn.parse(code, { ecmaVersion: 2020 });
    return { ok: true };
  } catch (e) {
    return { ok: false, line: e.loc.line, column: e.loc.column, message: e.message };
  }
}

// Find all top-level functions in the IIFE
let functions = [];
let inFunction = false;
let funcStart = -1;
let funcName = '';
let braceDepth = 0;
let inStringSingle = false;
let inStringDouble = false;

for (let i = 0; i < lines.length; i++) {
  let line = lines[i];
  
  for (let j = 0; j < line.length; j++) {
    let c = line[j];
    let prev = j > 0 ? line[j-1] : '';
    
    if (inStringSingle) {
      if (c === "'" && prev !== '\\') inStringSingle = false;
    } else if (inStringDouble) {
      if (c === '"' && prev !== '\\') inStringDouble = false;
    } else {
      if (c === "'") inStringSingle = true;
      else if (c === '"') inStringDouble = true;
      else if (c === '{') braceDepth++;
      else if (c === '}') {
        braceDepth--;
        if (inFunction && braceDepth === 1 && funcStart > 0) {
          // Function ends at this line (since we're inside IIFE which is depth 1)
          functions.push({ name: funcName, start: funcStart, end: i });
          inFunction = false;
          funcStart = -1;
        }
      }
    }
  }
  
  // Detect function start
  if (!inFunction && braceDepth >= 1) {
    let match = line.match(/function\s+(\w+)\s*\(/);
    if (match && line.trim().indexOf('function') < 10) {
      inFunction = true;
      funcStart = i;
      funcName = match[1];
      braceDepth = 1; // reset - we're at function body start
    }
  }
}

console.log('\nFound ' + functions.length + ' functions');

// Test each function
let invalidFuncs = [];
functions.forEach(f => {
  let funcCode = lines.slice(f.start, f.end + 1).join('\n');
  let testCode = 'var _test = ' + funcCode + ';';
  let result = checkCode(testCode);
  if (!result.ok) {
    invalidFuncs.push(f);
  }
});

console.log('Invalid functions: ' + invalidFuncs.length);
invalidFuncs.slice(0, 10).forEach(f => {
  console.log('  - ' + f.name + ' (lines ' + (f.start+1) + '-' + (f.end+1) + ')');
});

// Check overall syntax
let fullResult = checkCode(content);
console.log('\nOverall syntax: ' + (fullResult.ok ? 'PASS' : 'FAIL at line ' + fullResult.line));
