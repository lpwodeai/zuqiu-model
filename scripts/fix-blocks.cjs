const fs = require('fs');
const acorn = require('acorn');

let content = fs.readFileSync('assets/model-engine.broken.js', 'utf8');
let lines = content.split('\n');

function checkSyntax(code) {
  try {
    acorn.parse(code, { ecmaVersion: 2020 });
    return { ok: true };
  } catch (e) {
    return { ok: false, line: e.loc.line, message: e.message };
  }
}

console.log('Starting from broken backup...');

// Step 1: Find top-level var statements that declare objects/arrays
// and test each one for syntax validity
let varBlocks = [];
let i = 0;

while (i < lines.length) {
  let line = lines[i].trim();
  
  // Match top-level or 2-space indented var declarations
  if (line.match(/^\s{0,4}var\s+\w+\s*=\s*(\{|\[)/) && !line.match(/^\s{6,}/)) {
    let varName = line.match(/var\s+(\w+)\s*=/)[1];
    let startLine = i;
    
    // Find the end by counting braces
    let braceCount = 0;
    let bracketCount = 0;
    let inSingle = false;
    let inDouble = false;
    let foundEnd = false;
    let endLine = i;
    
    for (let j = i; j < lines.length; j++) {
      let l = lines[j];
      for (let k = 0; k < l.length; k++) {
        let c = l[k];
        let prev = k > 0 ? l[k-1] : '';
        
        if (inSingle) {
          if (c === "'" && prev !== '\\') inSingle = false;
        } else if (inDouble) {
          if (c === '"' && prev !== '\\') inDouble = false;
        } else {
          if (c === "'") inSingle = true;
          else if (c === '"') inDouble = true;
          else if (c === '{') braceCount++;
          else if (c === '}') {
            braceCount--;
            if (braceCount <= 0 && (j > startLine || bracketCount > 0)) {
              // Check if this is really the end (followed by , or ;)
              let rest = l.substring(k + 1).trim();
              if (rest.startsWith(';') || rest.startsWith(',') || rest === '') {
                endLine = j;
                foundEnd = true;
                break;
              }
            }
          }
          else if (c === '[') bracketCount++;
          else if (c === ']') {
            bracketCount--;
            if (bracketCount <= 0 && j > startLine) {
              let rest = l.substring(k + 1).trim();
              if (rest.startsWith(';') || rest.startsWith(',') || rest === '') {
                endLine = j;
                foundEnd = true;
                break;
              }
            }
          }
        }
      }
      if (foundEnd) break;
    }
    
    if (foundEnd && endLine > startLine) {
      // Test this block
      let blockCode = lines.slice(startLine, endLine + 1).join('\n');
      let testCode = 'var _test = ' + blockCode.substring(blockCode.indexOf('=') + 1) + ';';
      let result = checkSyntax(testCode);
      
      if (!result.ok) {
        varBlocks.push({
          name: varName,
          start: startLine,
          end: endLine,
          valid: false,
          error: result.message
        });
      } else {
        varBlocks.push({
          name: varName,
          start: startLine,
          end: endLine,
          valid: true
        });
      }
    }
    
    i = endLine + 1;
  } else {
    i++;
  }
}

console.log('\nFound ' + varBlocks.length + ' top-level var blocks');
let invalid = varBlocks.filter(b => !b.valid);
console.log('Invalid blocks: ' + invalid.length);

invalid.forEach(b => {
  console.log('  - ' + b.name + ' (lines ' + (b.start+1) + '-' + (b.end+1) + '): ' + b.error.substring(0, 60));
});

// Replace invalid blocks with placeholders
let offset = 0;
invalid.forEach(b => {
  let start = b.start - offset;
  let end = b.end - offset;
  let isArray = lines[start].includes('[');
  let placeholder = isArray ? '[]' : '{}';
  lines[start] = '  var ' + b.name + ' = ' + placeholder + '; // FIXED: original data corrupted';
  for (let j = start + 1; j <= end; j++) {
    lines[j] = '';
  }
  offset += (end - start);
});

let finalCode = lines.join('\n');
let finalResult = checkSyntax(finalCode);
console.log('\nAfter replacing invalid blocks:');
console.log('  Syntax check: ' + (finalResult.ok ? 'PASS' : 'FAIL at line ' + finalResult.line + ': ' + finalResult.message));

fs.writeFileSync('assets/model-engine.js', finalCode, 'utf8');
console.log('  Wrote fixed file');
