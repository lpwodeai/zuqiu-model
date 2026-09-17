const Database = require('better-sqlite3');
const path = require('path');

const ODDS_DB = path.join(__dirname, '..', 'data', 'odds.db');
const TIMING_DB = path.join(__dirname, '..', 'data', 'odds_timing.db');

console.log('=== 1. odds.db matches 表结构 ===');
const oddsDb = new Database(ODDS_DB, { readonly: true });

const matchesCols = oddsDb.prepare("PRAGMA table_info(matches)").all();
console.log('matches columns:', matchesCols.map(c => c.name).join(', '));

const totalMatches = oddsDb.prepare("SELECT COUNT(*) as cnt FROM matches").get();
console.log(`\n总比赛数: ${totalMatches.cnt}`);

// 按联赛统计（match_type 列）
const leagueStats = oddsDb.prepare("SELECT match_type, COUNT(*) as cnt FROM matches GROUP BY match_type ORDER BY cnt DESC").all();
console.log('\n按联赛分布:');
leagueStats.forEach(r => console.log(`  ${r.match_type}: ${r.cnt} 场`));

// 时间分布（match_date 列）
const dateRange = oddsDb.prepare("SELECT MIN(match_date) as min, MAX(match_date) as max FROM matches").get();
console.log(`\nmatch_date 范围: ${dateRange.min} ~ ${dateRange.max}`);

// 按年份分组
const yearStats = oddsDb.prepare(`
  SELECT substr(match_date, 1, 4) as year, COUNT(*) as cnt
  FROM matches
  WHERE match_date IS NOT NULL
  GROUP BY year ORDER BY year
`).all();
console.log('\n按年份分布:');
yearStats.forEach(r => console.log(`  ${r.year}: ${r.cnt} 场`));

// 按年月分组（最近24个月）
const monthStats = oddsDb.prepare(`
  SELECT substr(match_date, 1, 7) as ym, COUNT(*) as cnt
  FROM matches
  WHERE match_date IS NOT NULL
  GROUP BY ym ORDER BY ym DESC
  LIMIT 30
`).all();
console.log('\n按年月分布（最近30个月）:');
monthStats.forEach(r => console.log(`  ${r.ym}: ${r.cnt} 场`));

// 数据时效性分析：按赛季统计
const seasonStats = oddsDb.prepare(`
  SELECT
    CASE
      WHEN match_date >= '2025-08-01' THEN '2025-2026赛季'
      WHEN match_date >= '2024-08-01' THEN '2024-2025赛季'
      WHEN match_date >= '2023-08-01' THEN '2023-2024赛季'
      WHEN match_date >= '2022-08-01' THEN '2022-2023赛季'
      WHEN match_date >= '2021-08-01' THEN '2021-2022赛季'
      ELSE '2021年7月前'
    END as season,
    COUNT(*) as cnt,
    MIN(match_date) as min_date,
    MAX(match_date) as max_date
  FROM matches
  WHERE match_date IS NOT NULL
  GROUP BY season
  ORDER BY min_date DESC
`).all();
console.log('\n按赛季分布:');
seasonStats.forEach(r => console.log(`  ${r.season}: ${r.cnt} 场 (${r.min_date} ~ ${r.max_date})`));

// 检查是否有缺失日期的比赛
const nullDateCnt = oddsDb.prepare("SELECT COUNT(*) as cnt FROM matches WHERE match_date IS NULL").get();
console.log(`\n缺失 match_date 的比赛数: ${nullDateCnt.cnt}`);

// 检查实际结果缺失情况
const nullWdl = oddsDb.prepare("SELECT COUNT(*) as cnt FROM matches WHERE actual_wdl IS NULL OR actual_wdl = ''").get();
const nullScore = oddsDb.prepare("SELECT COUNT(*) as cnt FROM matches WHERE actual_score IS NULL OR actual_score = ''").get();
console.log(`缺失 actual_wdl: ${nullWdl.cnt} 场`);
console.log(`缺失 actual_score: ${nullScore.cnt} 场`);

console.log('\n=== 2. wdl_history 表 ===');
const wdlCols = oddsDb.prepare("PRAGMA table_info(wdl_history)").all();
console.log('wdl_history columns:', wdlCols.map(c => c.name).join(', '));

const wdlTotal = oddsDb.prepare("SELECT COUNT(*) as cnt FROM wdl_history").get();
console.log(`总记录: ${wdlTotal.cnt}`);

const wdlTsRange = oddsDb.prepare("SELECT MIN(timestamp) as min, MAX(timestamp) as max FROM wdl_history").get();
console.log(`timestamp 范围: ${wdlTsRange.min} ~ ${wdlTsRange.max}`);

// wdl_history 按年份统计
const wdlYearStats = oddsDb.prepare(`
  SELECT substr(timestamp, 1, 4) as year, COUNT(*) as cnt
  FROM wdl_history
  WHERE timestamp IS NOT NULL
  GROUP BY year ORDER BY year
`).all();
console.log('\nwdl_history 按年份:');
wdlYearStats.forEach(r => console.log(`  ${r.year}: ${r.cnt} 条`));

console.log('\n=== 3. handicap_history 表 ===');
const hcpCols = oddsDb.prepare("PRAGMA table_info(handicap_history)").all();
console.log('handicap_history columns:', hcpCols.map(c => c.name).join(', '));

const hcpTotal = oddsDb.prepare("SELECT COUNT(*) as cnt FROM handicap_history").get();
console.log(`总记录: ${hcpTotal.cnt}`);

const hcpTsRange = oddsDb.prepare("SELECT MIN(timestamp) as min, MAX(timestamp) as max FROM handicap_history").get();
console.log(`timestamp 范围: ${hcpTsRange.min} ~ ${hcpTsRange.max}`);

console.log('\n=== 4. score_history 表 ===');
const scoreTotal = oddsDb.prepare("SELECT COUNT(*) as cnt FROM score_history").get();
console.log(`总记录: ${scoreTotal.cnt}`);

console.log('\n=== 5. total_goals_history 表 ===');
try {
  const tgTotal = oddsDb.prepare("SELECT COUNT(*) as cnt FROM total_goals_history").get();
  console.log(`总记录: ${tgTotal.cnt}`);
} catch (e) {
  console.log('total_goals_history 表不存在或查询失败:', e.message);
}

console.log('\n=== 6. match_id_mapping 表 ===');
try {
  const mappingTotal = oddsDb.prepare("SELECT COUNT(*) as cnt FROM match_id_mapping").get();
  console.log(`总记录: ${mappingTotal.cnt}`);
} catch (e) {
  console.log('match_id_mapping 表不存在或查询失败:', e.message);
}

console.log('\n=== 7. odds_timing.db 时序赔率 ===');
try {
  const timingDb = new Database(TIMING_DB, { readonly: true });
  const timingTables = timingDb.prepare("SELECT name FROM sqlite_master WHERE type='table'").all();
  console.log('Tables:', timingTables.map(t => t.name).join(', '));

  const timingCols = timingDb.prepare("PRAGMA table_info(odds_timing)").all();
  console.log('odds_timing columns:', timingCols.map(c => c.name).join(', '));

  const timingTotal = timingDb.prepare("SELECT COUNT(*) as cnt FROM odds_timing").get();
  console.log(`总记录: ${timingTotal.cnt}`);

  // 时间分布
  const tsCol = timingCols.map(c => c.name).find(n => /time|date/i.test(n));
  if (tsCol) {
    const timingRange = timingDb.prepare(`SELECT MIN(${tsCol}) as min, MAX(${tsCol}) as max FROM odds_timing`).get();
    console.log(`${tsCol} 范围: ${timingRange.min} ~ ${timingRange.max}`);

    const timingYearStats = timingDb.prepare(`
      SELECT substr(${tsCol}, 1, 4) as year, COUNT(*) as cnt
      FROM odds_timing
      WHERE ${tsCol} IS NOT NULL
      GROUP BY year ORDER BY year
    `).all();
    console.log('\nodds_timing 按年份:');
    timingYearStats.forEach(r => console.log(`  ${r.year}: ${r.cnt} 条`));
  }

  timingDb.close();
} catch (e) {
  console.log('odds_timing.db 查询失败:', e.message);
}

console.log('\n=== 8. 数据陈旧度评估 ===');
// 计算不同时间窗口的数据占比
const today = new Date();
const todayStr = today.toISOString().slice(0, 10);

const windowStats = oddsDb.prepare(`
  SELECT
    SUM(CASE WHEN match_date >= date(?, '-90 days') THEN 1 ELSE 0 END) as last_90d,
    SUM(CASE WHEN match_date >= date(?, '-180 days') THEN 1 ELSE 0 END) as last_180d,
    SUM(CASE WHEN match_date >= date(?, '-365 days') THEN 1 ELSE 0 END) as last_365d,
    SUM(CASE WHEN match_date >= date(?, '-730 days') THEN 1 ELSE 0 END) as last_730d,
    SUM(CASE WHEN match_date >= date(?, '-1095 days') THEN 1 ELSE 0 END) as last_1095d,
    COUNT(*) as total
  FROM matches
  WHERE match_date IS NOT NULL
`).get(todayStr, todayStr, todayStr, todayStr, todayStr);

console.log(`\n以今日 ${todayStr} 为基准的数据时间窗口分布:`);
console.log(`  最近 90 天:  ${windowStats.last_90d} 场 (${(windowStats.last_90d / windowStats.total * 100).toFixed(1)}%)`);
console.log(`  最近 180 天: ${windowStats.last_180d} 场 (${(windowStats.last_180d / windowStats.total * 100).toFixed(1)}%)`);
console.log(`  最近 365 天: ${windowStats.last_365d} 场 (${(windowStats.last_365d / windowStats.total * 100).toFixed(1)}%)`);
console.log(`  最近 730 天: ${windowStats.last_730d} 场 (${(windowStats.last_730d / windowStats.total * 100).toFixed(1)}%)`);
console.log(`  最近 1095 天:${windowStats.last_1095d} 场 (${(windowStats.last_1095d / windowStats.total * 100).toFixed(1)}%)`);
console.log(`  总计:        ${windowStats.total} 场`);

// 3年前的数据占比
const oldData = oddsDb.prepare(`
  SELECT COUNT(*) as cnt FROM matches
  WHERE match_date IS NOT NULL AND match_date < date(?, '-1095 days')
`).get(todayStr);
console.log(`\n超过3年的旧数据: ${oldData.cnt} 场 (${(oldData.cnt / windowStats.total * 100).toFixed(1)}%)`);

oddsDb.close();
console.log('\n=== 分析完成 ===');
