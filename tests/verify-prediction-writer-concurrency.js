/**
 * 高并发写入幂等性验证脚本
 * =============================================
 * 验证 prediction-writer.js 在多个进程并发写同一个 SQLite 库时，
 * UNIQUE(match_id, model_name, prediction_type) 唯一索引 + INSERT OR IGNORE
 * 是否真正确保不产生重复行。
 *
 * 机制:
 *   主进程创建隔离临时库 + 建表(含唯一索引)，
 *   然后 fork N 个子进程，每个子进程用独立连接并发调用 savePreMatchPrediction，
 *   对同一批比赛多次重复写入。最后主进程统计验证。
 *
 * 隔离: 通过环境变量 PREDICTION_WRITER_ODDS_DB 指向临时库，不污染真实 odds.db。
 *
 * 运行: node tests/verify-prediction-writer-concurrency.js [并发进程数] [每进程重复次数]
 */
import { fork } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';

const __filename = fileURLToPath(import.meta.url);

const WORKER_COUNT = Number(process.argv[2] || 8);
const REPEAT_PER_WORKER = Number(process.argv[3] || 10);

// 同一批比赛（5 场），供所有 worker 重复写入以制造并发争抢
const MATCHES = [
  { matchDate: '2026-08-20', homeTeam: 'ConcTeamA', awayTeam: 'ConcTeamB', homeTeamCn: '并发A队', awayTeamCn: '并发B队', league: '测试联赛', season: '2026-2027', handicap: -0.5, wdl: { home: 0.40, draw: 0.30, away: 0.30 }, handicapProb: { upper: 0.5, draw: 0.1, lower: 0.4 }, totalGoalsProbOver: 0.55, totalGoalsProbUnder: 0.45, lambdaHome: 1.2, lambdaAway: 1.0, modelVersion: 'conc-test' },
  { matchDate: '2026-08-21', homeTeam: 'ConcTeamC', awayTeam: 'ConcTeamD', homeTeamCn: '并发C队', awayTeamCn: '并发D队', league: '测试联赛', season: '2026-2027', handicap: 0.0, wdl: { home: 0.35, draw: 0.30, away: 0.35 }, handicapProb: { upper: 0.45, draw: 0.1, lower: 0.45 }, totalGoalsProbOver: 0.50, totalGoalsProbUnder: 0.50, lambdaHome: 1.1, lambdaAway: 1.1, modelVersion: 'conc-test' },
  { matchDate: '2026-08-22', homeTeam: 'ConcTeamE', awayTeam: 'ConcTeamF', homeTeamCn: '并发E队', awayTeamCn: '并发F队', league: '测试联赛', season: '2026-2027', handicap: 0.5, wdl: { home: 0.30, draw: 0.30, away: 0.40 }, handicapProb: { upper: 0.4, draw: 0.1, lower: 0.5 }, totalGoalsProbOver: 0.45, totalGoalsProbUnder: 0.55, lambdaHome: 1.0, lambdaAway: 1.2, modelVersion: 'conc-test' },
  { matchDate: '2026-08-23', homeTeam: 'ConcTeamG', awayTeam: 'ConcTeamH', homeTeamCn: '并发G队', awayTeamCn: '并发H队', league: '测试联赛', season: '2026-2027', handicap: -1.0, wdl: { home: 0.50, draw: 0.25, away: 0.25 }, handicapProb: { upper: 0.55, draw: 0.08, lower: 0.37 }, totalGoalsProbOver: 0.60, totalGoalsProbUnder: 0.40, lambdaHome: 1.5, lambdaAway: 0.9, modelVersion: 'conc-test' },
  { matchDate: '2026-08-24', homeTeam: 'ConcTeamI', awayTeam: 'ConcTeamJ', homeTeamCn: '并发I队', awayTeamCn: '并发J队', league: '测试联赛', season: '2026-2027', handicap: 0.25, wdl: { home: 0.38, draw: 0.28, away: 0.34 }, handicapProb: { upper: 0.48, draw: 0.09, lower: 0.43 }, totalGoalsProbOver: 0.52, totalGoalsProbUnder: 0.48, lambdaHome: 1.25, lambdaAway: 1.15, modelVersion: 'conc-test' },
];

const EXPECTED_PREDS_PER_MATCH = 10; // 3 WDL + 3 HC + 2 TG + 2 Lambda

// ============ worker 角色 ============
if (process.env.PW_ROLE === 'worker') {
  const writer = await import('../shared/prediction-writer.js');
  const repeat = Number(process.env.PW_REPEAT || 10);
  let saved = 0;
  try {
    for (let i = 0; i < repeat; i++) {
      for (const m of JSON.parse(process.env.PW_MATCHES || '[]')) {
        writer.savePreMatchPrediction(m);
        saved++;
      }
    }
    process.exit(0);
  } catch (err) {
    console.error(`[worker ${process.pid}] 写入失败: ${err.message}`);
    process.exit(1);
  }
}

// ============ 主进程角色 ============
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pw-conc-'));
const tmpDbPath = path.join(tmpDir, 'conc_odds.db');

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

console.log('='.repeat(70));
console.log('高并发写入幂等性验证');
console.log('='.repeat(70));
console.log(`并发进程数: ${WORKER_COUNT}`);
console.log(`每进程重复写入次数: ${REPEAT_PER_WORKER}`);
console.log(`比赛场次: ${MATCHES.length}`);
console.log(`总写入调用次数: ${WORKER_COUNT * REPEAT_PER_WORKER * MATCHES.length}`);
console.log(`临时库: ${tmpDbPath}`);
console.log('-'.repeat(70));

const workerEnv = {
  ...process.env,
  PREDICTION_WRITER_ODDS_DB: tmpDbPath,
  PW_ROLE: 'worker',
  PW_REPEAT: String(REPEAT_PER_WORKER),
  PW_MATCHES: JSON.stringify(MATCHES),
};

const workerPromises = [];
const startTime = Date.now();

for (let i = 0; i < WORKER_COUNT; i++) {
  workerPromises.push(new Promise((resolve) => {
    const child = fork(__filename, [], { env: workerEnv, silent: false });
    child.on('exit', (code) => resolve({ pid: child.pid, code }));
    child.on('error', (err) => resolve({ pid: child.pid, code: -1, err: err.message }));
  }));
}

const results = await Promise.all(workerPromises);
const elapsedMs = Date.now() - startTime;

const failedWorkers = results.filter((r) => r.code !== 0);
console.log(`\n[1] Worker 执行情况:`);
console.log(`    成功: ${results.length - failedWorkers.length}/${results.length}`);
for (const r of failedWorkers) {
  console.log(`    ❌ worker ${r.pid} 退出码 ${r.code}${r.err ? ` (${r.err})` : ''}`);
}

// 最终统计
const checkDb = new Database(tmpDbPath);
const totalMatches = checkDb.prepare('SELECT COUNT(*) AS c FROM matches').get().c;
const totalPreds = checkDb.prepare('SELECT COUNT(*) AS c FROM model_predictions').get().c;
const dupGroups = checkDb.prepare(`
  SELECT COUNT(*) AS c FROM (
    SELECT match_id, model_name, prediction_type, COUNT(*) AS cnt
    FROM model_predictions
    GROUP BY match_id, model_name, prediction_type
    HAVING COUNT(*) > 1
  )
`).get().c;
checkDb.close();

const expectedPreds = MATCHES.length * EXPECTED_PREDS_PER_MATCH;

console.log(`\n[2] 最终数据校验:`);
console.log(`    matches 行数: ${totalMatches} (期望 ${MATCHES.length})`);
console.log(`    model_predictions 行数: ${totalPreds} (期望 ${expectedPreds})`);
console.log(`    重复组数: ${dupGroups} (期望 0)`);
console.log(`    耗时: ${elapsedMs}ms`);

console.log('\n' + '='.repeat(70));
let ok = true;
if (totalMatches !== MATCHES.length) { console.log('❌ matches 行数不符'); ok = false; }
if (totalPreds !== expectedPreds) { console.log('❌ model_predictions 行数不符（存在重复或丢失）'); ok = false; }
if (dupGroups !== 0) { console.log(`❌ 存在 ${dupGroups} 组重复`); ok = false; }
if (failedWorkers.length > 0) { console.log('❌ 有 worker 写入失败'); ok = false; }
if (ok) {
  console.log('✅ 高并发下唯一索引 + INSERT OR IGNORE 幂等逻辑验证通过');
  console.log('   ' + WORKER_COUNT + ' 个进程并发 ' + (WORKER_COUNT * REPEAT_PER_WORKER * MATCHES.length) + ' 次写入，最终无任何重复行');
} else {
  console.log('❌ 验证未通过，请检查上方输出');
}
console.log('='.repeat(70));

// 清理
fs.rmSync(tmpDir, { recursive: true, force: true });
process.exit(ok ? 0 : 1);