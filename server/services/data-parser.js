import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const EXPECTED_FIELDS = [
  'date', 'home_team', 'away_team', 'home_goals', 'away_goals', 'competition',
  'home_xg', 'away_xg', 'home_xgot', 'away_xgot', 'home_big_chances', 'away_big_chances',
  'home_xa', 'away_xa', 'home_saves', 'away_saves', 'home_touches_box', 'away_touches_box',
  'home_hits_post', 'away_hits_post', 'home_shots', 'home_shots_on_target',
  'away_shots', 'away_shots_on_target', 'home_possession', 'home_corners',
  'away_corners', 'home_fouls', 'away_fouls', 'home_yellow_cards', 'away_yellow_cards'
];

const NUMERIC_FIELDS = [
  'home_goals', 'away_goals', 'home_xg', 'away_xg', 'home_xgot', 'away_xgot',
  'home_big_chances', 'away_big_chances', 'home_xa', 'away_xa', 'home_saves', 'away_saves',
  'home_touches_box', 'away_touches_box', 'home_hits_post', 'away_hits_post',
  'home_shots', 'home_shots_on_target', 'away_shots', 'away_shots_on_target',
  'home_possession', 'home_corners', 'away_corners', 'home_fouls', 'away_fouls',
  'home_yellow_cards', 'away_yellow_cards'
];

const INTEGER_FIELDS = [
  'home_goals', 'away_goals', 'home_big_chances', 'away_big_chances',
  'home_saves', 'away_saves', 'home_touches_box', 'away_touches_box',
  'home_hits_post', 'away_hits_post', 'home_shots', 'home_shots_on_target',
  'away_shots', 'away_shots_on_target', 'home_possession', 'home_corners',
  'away_corners', 'home_fouls', 'away_fouls', 'home_yellow_cards', 'away_yellow_cards'
];

export class DataParser {
  constructor() {
    this.errors = [];
    this.warnings = [];
    this.stats = {
      totalRows: 0,
      validRows: 0,
      invalidRows: 0,
      missingValues: 0,
      correctedValues: 0
    };
  }

  parseFile(filePath) {
    this.errors = [];
    this.warnings = [];
    this.stats = {
      totalRows: 0,
      validRows: 0,
      invalidRows: 0,
      missingValues: 0,
      correctedValues: 0
    };

    const ext = path.extname(filePath).toLowerCase();
    
    switch (ext) {
      case '.csv':
        return this.parseCsv(filePath);
      default:
        throw new Error(`不支持的文件格式: ${ext}`);
    }
  }

  parseCsv(filePath) {
    const content = fs.readFileSync(filePath, 'utf8');
    const lines = content.split('\n').filter(line => line.trim());
    
    if (lines.length === 0) {
      throw new Error('CSV文件为空');
    }

    const headers = this.parseLine(lines[0]);
    const records = [];

    for (let i = 1; i < lines.length; i++) {
      const values = this.parseLine(lines[i]);
      const record = {};
      headers.forEach((header, index) => {
        record[header] = values[index] || '';
      });
      if (Object.keys(record).length > 0) {
        records.push(record);
      }
    }

    return this.processRecords(records);
  }

  parseLine(line) {
    const result = [];
    let current = '';
    let inQuotes = false;

    for (let i = 0; i < line.length; i++) {
      const char = line[i];

      if (char === '"') {
        inQuotes = !inQuotes;
      } else if (char === ',' && !inQuotes) {
        result.push(current.trim());
        current = '';
      } else {
        current += char;
      }
    }

    result.push(current.trim());
    return result;
  }

  processRecords(records) {
    this.stats.totalRows = records.length;
    
    this.validateHeaders(records[0]);

    const validRecords = [];
    
    for (let i = 0; i < records.length; i++) {
      const record = records[i];
      const rowNum = i + 2;
      
      const validated = this.validateRecord(record, rowNum);
      
      if (validated) {
        const cleaned = this.cleanRecord(validated);
        validRecords.push(cleaned);
        this.stats.validRows++;
      } else {
        this.stats.invalidRows++;
      }
    }

    return {
      data: validRecords,
      stats: this.stats,
      errors: this.errors,
      warnings: this.warnings
    };
  }

  validateHeaders(firstRow) {
    const actualFields = Object.keys(firstRow);
    const missingFields = EXPECTED_FIELDS.filter(f => !actualFields.includes(f));
    
    if (missingFields.length > 0) {
      this.errors.push({
        type: 'header_error',
        message: `缺少必要字段: ${missingFields.join(', ')}`,
        row: 1
      });
    }
  }

  validateRecord(record, rowNum) {
    const validated = {};
    let isValid = true;

    for (const field of EXPECTED_FIELDS) {
      const value = record[field];
      
      if (value === undefined || value === null || value === '') {
        if (['date', 'home_team', 'away_team'].includes(field)) {
          this.errors.push({
            type: 'missing_required',
            message: `第 ${rowNum} 行: 字段 ${field} 为必填项`,
            row: rowNum,
            field: field
          });
          isValid = false;
        } else {
          validated[field] = null;
          this.stats.missingValues++;
        }
      } else {
        validated[field] = value;
      }
    }

    const dateValid = this.validateDate(validated.date, rowNum);
    if (!dateValid) isValid = false;

    return isValid ? validated : null;
  }

  validateDate(dateStr, rowNum) {
    if (!dateStr) return false;

    const formats = [
      /^\d{4}\/\d{1,2}\/\d{1,2}$/,
      /^\d{4}-\d{2}-\d{2}$/
    ];

    if (!formats.some(f => f.test(dateStr))) {
      this.errors.push({
        type: 'invalid_date',
        message: `第 ${rowNum} 行: 日期格式无效: ${dateStr}`,
        row: rowNum
      });
      return false;
    }

    return true;
  }

  cleanRecord(record) {
    const cleaned = { ...record };

    for (const field of NUMERIC_FIELDS) {
      if (cleaned[field] !== null && cleaned[field] !== undefined) {
        const numValue = parseFloat(cleaned[field]);
        
        if (isNaN(numValue)) {
          cleaned[field] = null;
        } else {
          if (INTEGER_FIELDS.includes(field)) {
            cleaned[field] = Math.round(numValue);
          } else {
            cleaned[field] = Math.round(numValue * 100) / 100;
          }
        }
      }
    }

    if (cleaned.home_possession !== null) {
      if (cleaned.home_possession < 0 || cleaned.home_possession > 100) {
        cleaned.home_possession = Math.max(0, Math.min(100, cleaned.home_possession));
      }
    }

    cleaned.date = this.normalizeDate(cleaned.date);

    return cleaned;
  }

  normalizeDate(dateStr) {
    if (!dateStr) return null;

    if (dateStr.includes('/')) {
      const parts = dateStr.split('/');
      if (parts.length === 3) {
        const [year, month, day] = parts.map(p => p.padStart(2, '0'));
        return `${year}-${month}-${day}`;
      }
    }

    return dateStr;
  }

  generateMatchHash(record) {
    return `${record.date}_${record.home_team}_${record.away_team}`;
  }

  getReport() {
    return {
      summary: {
        totalRows: this.stats.totalRows,
        validRows: this.stats.validRows,
        invalidRows: this.stats.invalidRows,
        successRate: ((this.stats.validRows / this.stats.totalRows) * 100).toFixed(2) + '%',
        missingValues: this.stats.missingValues,
        correctedValues: this.stats.correctedValues
      },
      errors: this.errors,
      warnings: this.warnings
    };
  }
}

export const dataParser = new DataParser();