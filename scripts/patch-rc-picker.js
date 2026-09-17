import fs from 'fs';
import path from 'path';

const rcPickerPath = path.join(process.cwd(), 'node_modules', 'rc-picker');

if (!fs.existsSync(rcPickerPath)) {
  console.log('[patch-rc-picker] rc-picker not found, skipping');
  process.exit(0);
}

const miscUtilPath = path.join(rcPickerPath, 'es', 'utils', 'miscUtil.js');

if (!fs.existsSync(miscUtilPath)) {
  console.log('[patch-rc-picker] miscUtil.js not found, skipping');
} else {
  let miscUtilContent = fs.readFileSync(miscUtilPath, 'utf8');
  
  if (!miscUtilContent.includes('export function getValue')) {
    const getValueFunction = `\nexport function getValue(val, index) {
  if (!val) {
    return undefined;
  }
  return val[index];
}\n`;
    
    miscUtilContent += getValueFunction;
    fs.writeFileSync(miscUtilPath, miscUtilContent);
    console.log('[patch-rc-picker] Added getValue export to miscUtil.js');
  } else {
    console.log('[patch-rc-picker] getValue already exists in miscUtil.js');
  }
}

console.log('[patch-rc-picker] Done');
