const fs = require('fs');
const acorn = require('acorn');

let content = fs.readFileSync('assets/model-engine.js', 'utf8');

function findErrors(code, maxErrors = 20) {
  let errors = [];
  let lines = code.split('\n');
  
  for (let attempt = 0; attempt < maxErrors; attempt++) {
    try {
      acorn.parse(code, { ecmaVersion: 2020 });
      break; // No more errors
    } catch (e) {
      let errLine = e.loc ? e.loc.line : 0;
      errors.push({
        line: errLine,
        column: e.loc ? e.loc.column : 0,
        message: e.message,
        context: lines[errLine - 1] ? lines[errLine - 1].substring(0, 100) : ''
      });
      
      // Try to skip past this error by finding next statement
      // Find the next line that starts a new variable/function definition
      let skipTo = errLine;
      while (skipTo < lines.length) {
        let line = lines[skipTo].trim();
        if (line.match(/^(var |function |\/\/|$)/) || line.match(/^  var /) || line.match(/^  function /)) {
          break;
        }
        skipTo++;
      }
      
      // Replace the error block with a placeholder
      if (skipTo > errLine) {
        let startLine = errLine - 1;
        // Find the start of this statement (go back until we find a var/function/=)
        while (startLine > 0) {
          let prevLine = lines[startLine - 1].trim();
          if (prevLine.match(/(var |function |=.*\{|=.*\[)$/) || prevLine === '') {
            break;
          }
          startLine--;
        }
        
        // Replace with empty lines
        for (let i = startLine; i < skipTo; i++) {
          lines[i] = '';
        }
        code = lines.join('\n');
      } else {
        // Can't skip, just remove the line
        lines.splice(errLine - 1, 1);
        code = lines.join('\n');
      }
    }
  }
  
  return errors;
}

let errors = findErrors(content);
console.log('Found ' + errors.length + ' error locations:');
errors.forEach((e, i) => {
  console.log('\nError ' + (i+1) + ': line ' + e.line + ', col ' + e.column);
  console.log('  ' + e.message);
  console.log('  ' + e.context);
});

// Write the partially fixed version for testing
fs.writeFileSync('assets/model-engine-partial.js', content, 'utf8');
console.log('\nWrote model-engine-partial.js with attempted fixes');
