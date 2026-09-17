const fs = require('fs');
const acorn = require('acorn');

let content = fs.readFileSync('assets/model-engine.js', 'utf8');

function checkSyntax(code) {
  try {
    acorn.parse(code, { ecmaVersion: 2020 });
    return { ok: true };
  } catch (e) {
    return { ok: false, line: e.loc.line, column: e.loc.column, message: e.message };
  }
}

// Strategy: Find each top-level var statement that declares an object/array,
// test if it's syntactically valid, and if not, replace with a placeholder.

let lines = content.split('\n');
let fixedCount = 0;
let maxAttempts = 50;

for (let attempt = 0; attempt < maxAttempts; attempt++) {
  let result = checkSyntax(lines.join('\n'));
  if (result.ok) {
    console.log('\nSyntax OK after ' + attempt + ' fixes!');
    break;
  }
  
  console.log('Fix attempt ' + (attempt + 1) + ': error at line ' + result.line);
  
  // Find the start of this statement (look backwards for var/function/)
  let errorLine = result.line - 1;
  let startLine = errorLine;
  
  // Go back to find the start of the current statement
  while (startLine > 0) {
    let prev = lines[startLine - 1].trim();
    if (prev.match(/^(var |function |\/\/|$)/) || prev.match(/^\s*var\s+\w+\s*=/) || prev.match(/^\s*function\s+\w+/)) {
      break;
    }
    startLine--;
  }
  
  // Find the end of this statement
  let endLine = errorLine;
  let braceDepth = 0;
  let inString = false;
  let stringChar = '';
  
  for (let i = startLine; i < lines.length && i < startLine + 500; i++) {
    let line = lines[i];
    for (let j = 0; j < line.length; j++) {
      let c = line[j];
      if (inString) {
        if (c === stringChar && (j === 0 || line[j-1] !== '\\')) inString = false;
      } else {
        if (c === "'" || c === '"') { inString = true; stringChar = c; }
        if (c === '{') braceDepth++;
        if (c === '}') {
          braceDepth--;
          if (braceDepth <= 0 && i > startLine) {
            endLine = i;
            i = lines.length; // break outer
            break;
          }
        }
      }
    }
    if (endLine > errorLine) break;
  }
  
  // If we couldn't find the end, use a heuristic
  if (endLine === errorLine) {
    endLine = Math.min(lines.length - 1, errorLine + 50);
  }
  
  // Try to find a safe end point (next top-level var/function or blank line)
  for (let i = errorLine + 1; i < Math.min(lines.length, errorLine + 100); i++) {
    let trimmed = lines[i].trim();
    if (trimmed.match(/^\s*var\s+\w+\s*=/) || trimmed.match(/^\s*function\s+\w+/) || trimmed === '') {
      endLine = i - 1;
      break;
    }
  }
  
  console.log('  Replacing lines ' + (startLine+1) + ' to ' + (endLine+1));
  
  // Get the variable name
  let firstLine = lines[startLine].trim();
  let varMatch = firstLine.match(/var\s+(\w+)\s*=/);
  let varName = varMatch ? varMatch[1] : 'unknown';
  
  // Replace with a placeholder
  lines[startLine] = '  var ' + varName + ' = {}; // FIXED: original data corrupted';
  for (let i = startLine + 1; i <= endLine; i++) {
    lines[i] = '';
  }
  
  fixedCount++;
}

if (fixedCount === 0) {
  console.log('No fixes applied');
} else {
  console.log('\nApplied ' + fixedCount + ' fixes');
}

let finalResult = checkSyntax(lines.join('\n'));
console.log('Final syntax check: ' + (finalResult.ok ? 'PASS' : 'FAIL at line ' + finalResult.line));

fs.writeFileSync('assets/model-engine.js', lines.join('\n'), 'utf8');
console.log('Wrote fixed model-engine.js');
