/**
 * prediction-writer 双库写入幂等性单元测试
 * =============================================
 * 验证内容:
 *   1. makeMatchId / makeMatchHash 纯函数
 *   2. savePreMatchPrediction 幂等性: 重复调用不产生重复行
 *   3. batchSavePredictions 幂等性: 重复批量调用不产生重复行
 *   4. updatePostMatchResult 回填逻辑
 *
 * 隔离策略:
 *   通过环境变量 PREDICTION_WRITER_ODDS_DB 指向临时数据库，
 *   不污染真实 data/odds.db。
 *
 * 运行: node tests/prediction-writer.test.js
 */
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';
import { strict as assert } from 'node:assert';
import Database from 'better-sqlite3';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ============ 1. 创建隔离临时库 + 建表（真实 schema） ============
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pw-test-'));
const tmpDbPath = path.join(tmpDir, 'test_odds.db');

const setupDb = new Database(tmpDbPath);
setupDb.exec(`
  CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT UNIQUE NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    match_date TEXT NOT NULL,
    match_type TEXT NOT NULL,
    handicap REAL,
    actual_wdl TEXT,
    actual_handicap TEXT,
    actual_score TEXT,
    actual_total_goals INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE fbref_match_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    odds_match_id TEXT NOT NULL,
    fbref_match_id TEXT NOT NULL,
    fbref_match_url TEXT NOT NULL,
    fbref_match_slug TEXT,
    league TEXT NOT NULL,
    season TEXT NOT NULL,
    match_date TEXT NOT NULL,
    home_team_fbref TEXT,
    away_team_fbref TEXT,
    home_team_cn TEXT,
    away_team_cn TEXT,
    fbref_week INTEGER,
    fbref_score TEXT,
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(fbref_match_id),
    UNIQUE(odds_match_id, season)
  );

  CREATE TABLE model_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    prediction_type TEXT NOT NULL,
    prediction TEXT,
    probability REAL,
    confidence REAL,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE,
    UNIQUE(match_id, model_name, prediction_type)
  );
`);
setupDb.close();

// ============ 2. 设置环境变量（须在 import 模块前） ============
process.env.PREDICTION_WRITER_ODDS_DB = tmpDbPath;

// ============ 3. 动态导入被测模块 ============
const writer = await import('../shared/prediction-writer.js');

// ============ 4. 测试辅助 ============
let passed = 0;
let failed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`✅ PASS: ${name}`);
  } catch (err) {
    failed++;
    console.error(`❌ FAIL: ${name}`);
    console.error(`   ${err.message}`);
  }
}

function countPreds(db, matchId = null) {
  if (matchId) {
    return db.prepare('SELECT COUNT(*) AS c FROM model_predictions WHERE match_id = ?').get(matchId).c;
  }
  return db.prepare('SELECT COUNT(*) AS c FROM model_predictions').get().c;
}

function countDupGroups(db) {
  return db.prepare(`
    SELECT COUNT(*) AS c FROM (
      SELECT match_id, model_name, prediction_type, COUNT(*) AS cnt
      FROM model_predictions
      GROUP BY match_id, model_name, prediction_type
      HAVING COUNT(*) > 1
    )
  `).get().c;
}

const samplePrediction = {
  matchDate: '2026-08-20',
  homeTeam: 'TestHome FC',
  awayTeam: 'TestAway FC',
  homeTeamCn: '测试主队',
  awayTeamCn: '测试客队',
  league: '测试联赛',
  season: '2026-2027',
  handicap: -0.5,
  wdl: { home: 0.45, draw: 0.28, away: 0.27 },
  handicapProb: { upper: 0.50, draw: 0.08, lower: 0.42 },
  scoreTop5: [{ score: '1:1', prob: 0.14 }],
  totalGoalsProbOver: 0.55,
  totalGoalsProbUnder: 0.45,
  lambdaHome: 1.32,
  lambdaAway: 1.10,
  modelVersion: 'v7.5-test'
};

// ============ 5. 测试用例 ============

// --- 纯函数 ---
test('makeMatchId 生成日期_主队_客队 格式', () => {
  assert.strictEqual(writer.makeMatchId('2026-08-20', 'A', 'B'), '2026-08-20_A_B');
});

test('makeMatchId 清理多余空格', () => {
  assert.strictEqual(writer.makeMatchId('2026-08-20', '  A  ', ' B '), '2026-08-20_A_B');
});

test('makeMatchHash 对相同输入产生相同 MD5', () => {
  const h1 = writer.makeMatchHash('2026-08-20', 'A', 'B', '1-0');
  const h2 = writer.makeMatchHash('2026-08-20', 'A', 'B', '1-0');
  assert.strictEqual(h1, h2);
  assert.strictEqual(h1.length, 32);
});

// --- savePreMatchPrediction 幂等性 ---
test('savePreMatchPrediction 首次写入 10 条预测 + 1 场 matches', () => {
  const db = new Database(tmpDbPath);
  writer.savePreMatchPrediction(samplePrediction);
  const matchCount = db.prepare('SELECT COUNT(*) AS c FROM matches').get().c;
  const predCount = countPreds(db, '2026-08-20_TestHome FC_TestAway FC');
  db.close();
  assert.strictEqual(matchCount, 1, `matches 应为 1，实际 ${matchCount}`);
  // 3 WDL + 3 HC + 2 TG + 2 Lambda = 10
  assert.strictEqual(predCount, 10, `预测应为 10，实际 ${predCount}`);
});

test('savePreMatchPrediction 重复调用不产生重复行（幂等）', () => {
  const db = new Database(tmpDbPath);
  // 重复 3 次
  writer.savePreMatchPrediction(samplePrediction);
  writer.savePreMatchPrediction(samplePrediction);
  writer.savePreMatchPrediction(samplePrediction);
  const predCount = countPreds(db, '2026-08-20_TestHome FC_TestAway FC');
  const dup = countDupGroups(db);
  const matchCount = db.prepare('SELECT COUNT(*) AS c FROM matches').get().c;
  db.close();
  assert.strictEqual(predCount, 10, `重复调用后仍应为 10，实际 ${predCount}`);
  assert.strictEqual(dup, 0, `不应存在重复组，实际 ${dup} 组`);
  assert.strictEqual(matchCount, 1, `matches 应仍为 1，实际 ${matchCount}`);
});

// --- batchSavePredictions 幂等性 ---
test('batchSavePredictions 重复调用不产生重复行', () => {
  const batch = [
    samplePrediction,
    {
      ...samplePrediction,
      matchDate: '2026-08-21',
      homeTeam: 'BatchHome',
      awayTeam: 'BatchAway',
      homeTeamCn: '批量主队',
      awayTeamCn: '批量客队',
    },
  ];
  const db = new Database(tmpDbPath);
  writer.batchSavePredictions(batch);
  writer.batchSavePredictions(batch); // 重复
  const dup = countDupGroups(db);
  const total = countPreds(db);
  db.close();
  assert.strictEqual(dup, 0, `批量重复后不应有重复组，实际 ${dup} 组`);
  // 两场比赛 × 10 条 = 20 条，重复不增加
  assert.strictEqual(total, 20, `批量后总数应为 20，实际 ${total}`);
});

// --- updatePostMatchResult ---
test('updatePostMatchResult 正确回填实际结果', () => {
  const db = new Database(tmpDbPath);
  writer.updatePostMatchResult({
    matchDate: '2026-08-20',
    homeTeam: 'TestHome FC',
    awayTeam: 'TestAway FC',
    homeScore: 2,
    awayScore: 1,
    actualWdl: '主胜',
    handicap: -0.5,
    actualHandicapResult: '胜',
  });
  const row = db.prepare(
    "SELECT * FROM matches WHERE match_id = '2026-08-20_TestHome FC_TestAway FC'"
  ).get();
  db.close();
  assert.strictEqual(row.actual_score, '2-1');
  assert.strictEqual(row.actual_wdl, '主胜');
  assert.strictEqual(row.actual_total_goals, 3);
});

test('updatePostMatchResult 对不存在比赛返回 null', () => {
  const r = writer.updatePostMatchResult({
    matchDate: '1999-01-01',
    homeTeam: 'Nope',
    awayTeam: 'Nope2',
    homeScore: 0,
    awayScore: 0,
    actualWdl: '平',
  });
  assert.strictEqual(r, null);
});

// ============ 6. 结果统计 + 清理 ============
console.log(`\n${'='.repeat(60)}`);
console.log(`测试结果: ${passed} 通过, ${failed} 失败, ${passed + failed} 总计`);
console.log(`${'='.repeat(60)}`);

writer.close();
try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}

if (failed > 0) {
  process.exit(1);
}