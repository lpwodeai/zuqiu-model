/**
 * Prediction Writer — 预测结果回写数据库模块
 * =============================================
 * 解决数据流断裂问题: generate_unified_report.py 只读消费数据，
 * 从不写回数据库，导致 pipeline 无法自动重训。
 *
 * 核心功能:
 *   1. savePreMatchPrediction() — 赛前写入 matches 表 (odds.db) + model_predictions
 *   2. updatePostMatchResult()  — 比赛结束后回填 actual_score/actual_wdl/actual_total_goals
 *   3. saveXGData()             — 赛后 xG 数据回填到 five_leagues.db
 *   4. batchSavePredictions()  — 批量保存 (一轮比赛)
 *
 * 设计原则:
 *   - 幂等: INSERT OR IGNORE，重复调用安全
 *   - 容错: 单条失败不阻塞其他
 *   - 双库写入: odds.db (预测/赔率数据) + five_leagues.db (SofaScore 数据)
 */

import Database from 'better-sqlite3';
import path from 'path';
import { fileURLToPath } from 'url';
import crypto from 'crypto';
import fs from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, '..');

// DB 路径支持环境变量覆盖，便于单元测试与高并发验证脚本使用隔离的临时库，
// 避免污染真实 odds.db / five_leagues.db。默认仍指向项目 data/ 目录。
const ODDS_DB_PATH = process.env.PREDICTION_WRITER_ODDS_DB || path.join(PROJECT_ROOT, 'data', 'odds.db');
const FL_DB_PATH = process.env.PREDICTION_WRITER_FL_DB || path.join(PROJECT_ROOT, 'data', 'five_leagues.db');
const PREDICTION_LOG_PATH = path.join(PROJECT_ROOT, 'logs', 'prediction_write.log');

// 数据库连接池 (懒加载)
let oddsDb = null;
let flDb = null;

function getOddsDb() {
  if (!oddsDb) {
    if (!fs.existsSync(ODDS_DB_PATH)) {
      throw new Error(`odds.db 不存在: ${ODDS_DB_PATH}`);
    }
    oddsDb = new Database(ODDS_DB_PATH);
    oddsDb.pragma('journal_mode = WAL');
    oddsDb.pragma('foreign_keys = ON');
    oddsDb.pragma('busy_timeout = 5000');
  }
  return oddsDb;
}

function getFlDb() {
  if (!flDb) {
    if (!fs.existsSync(FL_DB_PATH)) {
      throw new Error(`five_leagues.db 不存在: ${FL_DB_PATH}`);
    }
    flDb = new Database(FL_DB_PATH);
    flDb.pragma('journal_mode = WAL');
    flDb.pragma('foreign_keys = ON');
    flDb.pragma('busy_timeout = 5000');
  }
  return flDb;
}

function log(msg) {
  const line = `[${new Date().toISOString()}] [prediction-writer] ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(PREDICTION_LOG_PATH, line + '\n', 'utf-8');
  } catch (_) { /* ignore */ }
}

/**
 * 生成 match_id: YYYY-MM-DD_home_away (与现有格式一致)
 */
export function makeMatchId(date, homeTeam, awayTeam) {
  const clean = (s) => String(s).trim().replace(/\s+/g, ' ');
  return `${date}_${clean(homeTeam)}_${clean(awayTeam)}`;
}

/**
 * 生成 matchHash (MD5)
 */
export function makeMatchHash(date, homeTeam, awayTeam, score) {
  const raw = `${date}|${homeTeam}|${awayTeam}|${score}`;
  return crypto.createHash('md5').update(raw).digest('hex');
}

/**
 * 保存赛前预测到数据库
 *
 * @param {Object} prediction - 预测结果
 * @param {string} prediction.matchDate - 比赛日期 'YYYY-MM-DD'
 * @param {string} prediction.homeTeam - 主队英文名
 * @param {string} prediction.awayTeam - 客队英文名
 * @param {string} prediction.homeTeamCn - 主队中文名
 * @param {string} prediction.awayTeamCn - 客队中文名
 * @param {string} prediction.league - 联赛 (e.g. '西甲')
 * @param {string} prediction.season - 赛季 (e.g. '2026-2027')
 * @param {number} prediction.handicap - 让球盘口 (正数=客让, 负数=主让)
 * @param {Object} prediction.wdl - { home: 0.491, draw: 0.271, away: 0.238 }
 * @param {Object} prediction.handicapProb - { upper: 0.588, draw: 0.076, lower: 0.336 }
 * @param {Object} prediction.scoreTop5 - [{ score: '1:1', prob: 0.149 }, ...]
 * @param {number} prediction.totalGoalsProbOver - 大球概率
 * @param {number} prediction.totalGoalsProbUnder - 小球概率
 * @param {number} prediction.lambdaHome - 主队进球期望
 * @param {number} prediction.lambdaAway - 客队进球期望
 * @param {string} prediction.modelVersion - 模型版本 (e.g. 'v2.0')
 * @returns {string} matchId
 */
export function savePreMatchPrediction(prediction) {
  const {
    matchDate, homeTeam, awayTeam, homeTeamCn, awayTeamCn,
    league = '', season = '', handicap,
    wdl, handicapProb, scoreTop5,
    totalGoalsProbOver, totalGoalsProbUnder,
    lambdaHome, lambdaAway,
    modelVersion = 'v2.0'
  } = prediction;

  const matchId = makeMatchId(matchDate, homeTeam, awayTeam);
  const now = new Date().toISOString();

  const db = getOddsDb();

  // 1. matches 表: 插入比赛记录 (预测时, 无 actual 字段)
  db.prepare(`
    INSERT OR IGNORE INTO matches
      (match_id, match_type, league, home_team, away_team, match_date, handicap,
       actual_wdl, actual_handicap, actual_score, actual_total_goals,
       created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
  `).run(
    matchId,
    season ? `${league}${season}赛季` : league,
    league,
    homeTeam, awayTeam, matchDate, handicap,
    now, now
  );

  // 2. fbref_match_mapping 表
  db.prepare(`
    INSERT OR IGNORE INTO fbref_match_mapping
      (fbref_match_id, league, season, match_date,
       home_team_fbref, away_team_fbref, home_team_cn, away_team_cn,
       fbref_week, collected_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
  `).run(matchId, league, season, matchDate, homeTeam, awayTeam,
         homeTeamCn || null, awayTeamCn || null, now);

  // 3. model_predictions 表: 保存预测结果 (幂等: INSERT OR IGNORE + 唯一约束)
  const insertPred = db.prepare(`
    INSERT OR IGNORE INTO model_predictions
      (match_id, model_name, prediction_type, prediction, probability, confidence, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);

  // WDL 三类概率
  if (wdl) {
    const wdlEntries = [
      { type: 'WDL_home', pred: '主胜', prob: wdl.home, conf: wdl.home },
      { type: 'WDL_draw', pred: '平',   prob: wdl.draw, conf: wdl.draw },
      { type: 'WDL_away', pred: '客胜', prob: wdl.away, conf: wdl.away },
    ];
    const insertMany = db.transaction((entries) => {
      for (const e of entries) {
        insertPred.run(matchId, modelVersion, e.type, e.pred, e.prob, e.conf, now);
      }
    });
    insertMany(wdlEntries);
  }

  // 让球三类概率
  if (handicapProb) {
    const hcEntries = [
      { type: 'HC_upper', pred: '上盘赢', prob: handicapProb.upper, conf: handicapProb.upper },
      { type: 'HC_draw',  pred: '走水',   prob: handicapProb.draw,  conf: handicapProb.draw },
      { type: 'HC_lower', pred: '下盘赢', prob: handicapProb.lower, conf: handicapProb.lower },
    ];
    const insertMany = db.transaction((entries) => {
      for (const e of entries) {
        insertPred.run(matchId, modelVersion, e.type, e.pred, e.prob, e.conf, now);
      }
    });
    insertMany(hcEntries);
  }

  // 总进球
  if (totalGoalsProbOver !== undefined) {
    insertPred.run(matchId, modelVersion, 'TG_over_2_5', '大球',
      totalGoalsProbOver, totalGoalsProbOver, now);
  }
  if (totalGoalsProbUnder !== undefined) {
    insertPred.run(matchId, modelVersion, 'TG_under_2_5', '小球',
      totalGoalsProbUnder, totalGoalsProbUnder, now);
  }

  // Lambda
  if (lambdaHome !== undefined) {
    insertPred.run(matchId, modelVersion, 'Lambda_home', String(lambdaHome),
      lambdaHome, lambdaHome, now);
  }
  if (lambdaAway !== undefined) {
    insertPred.run(matchId, modelVersion, 'Lambda_away', String(lambdaAway),
      lambdaAway, lambdaAway, now);
  }

  log(`✅ 赛前预测已保存: ${matchId} (${homeTeam} vs ${awayTeam})`);
  return matchId;
}

/**
 * 批量保存一轮比赛的赛前预测
 *
 * @param {Array} predictions - savePreMatchPrediction 参数数组
 * @returns {Array} matchIds
 */
export function batchSavePredictions(predictions) {
  const matchIds = [];
  const db = getOddsDb();
  const now = new Date().toISOString();

  // 用事务批量插入，性能好
  const insertMatch = db.prepare(`
    INSERT OR IGNORE INTO matches
      (match_id, match_type, league, home_team, away_team, match_date, handicap,
       actual_wdl, actual_handicap, actual_score, actual_total_goals,
       created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
  `);

  const insertFbref = db.prepare(`
    INSERT OR IGNORE INTO fbref_match_mapping
      (fbref_match_id, league, season, match_date,
       home_team_fbref, away_team_fbref, home_team_cn, away_team_cn,
       fbref_week, collected_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
  `);

  const insertPred = db.prepare(`
    INSERT OR IGNORE INTO model_predictions
      (match_id, model_name, prediction_type, prediction, probability, confidence, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `);

  const tx = db.transaction((preds) => {
    for (const p of preds) {
      const matchId = makeMatchId(p.matchDate, p.homeTeam, p.awayTeam);

      insertMatch.run(
        matchId,
        p.season ? `${p.league}${p.season}赛季` : p.league,
        p.league,
        p.homeTeam, p.awayTeam, p.matchDate, p.handicap,
        now, now
      );

      insertFbref.run(
        matchId, p.league, p.season, p.matchDate,
        p.homeTeam, p.awayTeam, p.homeTeamCn || null, p.awayTeamCn || null, now
      );

      // WDL
      if (p.wdl) {
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'WDL_home', '主胜', p.wdl.home, p.wdl.home, now);
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'WDL_draw', '平',   p.wdl.draw, p.wdl.draw, now);
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'WDL_away', '客胜', p.wdl.away, p.wdl.away, now);
      }

      // 让球
      if (p.handicapProb) {
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'HC_upper', '上盘赢', p.handicapProb.upper, p.handicapProb.upper, now);
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'HC_draw',  '走水',   p.handicapProb.draw,  p.handicapProb.draw, now);
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'HC_lower', '下盘赢', p.handicapProb.lower, p.handicapProb.lower, now);
      }

      // 总进球
      if (p.totalGoalsProbOver !== undefined) {
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'TG_over_2_5', '大球', p.totalGoalsProbOver, p.totalGoalsProbOver, now);
      }
      if (p.totalGoalsProbUnder !== undefined) {
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'TG_under_2_5', '小球', p.totalGoalsProbUnder, p.totalGoalsProbUnder, now);
      }

      // Lambda (进攻期望) — 与 savePreMatchPrediction 保持一致
      if (p.lambdaHome !== undefined) {
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'Lambda_home', String(p.lambdaHome), p.lambdaHome, p.lambdaHome, now);
      }
      if (p.lambdaAway !== undefined) {
        insertPred.run(matchId, p.modelVersion || 'v2.0', 'Lambda_away', String(p.lambdaAway), p.lambdaAway, p.lambdaAway, now);
      }

      matchIds.push(matchId);
    }
  });

  tx(predictions);
  log(`✅ 批量保存完成: ${predictions.length} 场比赛 (matchIds: ${matchIds.join(', ')})`);

  return matchIds;
}

/**
 * 比赛结束后回填实际结果
 *
 * @param {Object} result - 比赛结果
 * @param {string} result.matchDate - 比赛日期
 * @param {string} result.homeTeam - 主队
 * @param {string} result.awayTeam - 客队
 * @param {number} result.homeScore - 主队进球
 * @param {number} result.awayScore - 客队进球
 * @param {string} result.actualWdl - '主胜'|'平'|'客胜'
 * @param {number} result.handicap - 让球盘口
 * @param {string} result.actualHandicapResult - '胜'|'平'|'负' (让球结果)
 */
export function updatePostMatchResult(result) {
  const {
    matchDate, homeTeam, awayTeam,
    homeScore, awayScore, actualWdl,
    handicap, actualHandicapResult
  } = result;

  const matchId = makeMatchId(matchDate, homeTeam, awayTeam);
  const score = `${homeScore}-${awayScore}`;
  const totalGoals = homeScore + awayScore;
  const now = new Date().toISOString();

  const db = getOddsDb();

  const info = db.prepare('SELECT id FROM matches WHERE match_id = ?').get(matchId);
  if (!info) {
    log(`⚠️  回填结果失败: ${matchId} 未在 matches 表中找到 (比赛记录可能未通过 savePreMatchPrediction 创建)`);
    return null;
  }

  db.prepare(`
    UPDATE matches
    SET actual_wdl = ?, actual_handicap = ?, actual_score = ?,
        actual_total_goals = ?, updated_at = ?
    WHERE match_id = ?
  `).run(actualWdl, actualHandicapResult, score, totalGoals, now, matchId);

  log(`✅ 比赛结果已回填: ${matchId} (${score}, WDL=${actualWdl}, HC=${actualHandicapResult})`);
  return matchId;
}

/**
 * 批量回填比赛结果
 */
export function batchUpdatePostMatchResults(results) {
  const matchIds = [];
  const db = getOddsDb();
  const now = new Date().toISOString();

  const update = db.prepare(`
    UPDATE matches
    SET actual_wdl = ?, actual_handicap = ?, actual_score = ?,
        actual_total_goals = ?, updated_at = ?
    WHERE match_id = ?
  `);

  const check = db.prepare('SELECT match_id FROM matches WHERE match_id = ?');

  const tx = db.transaction((resultsList) => {
    for (const r of resultsList) {
      const matchId = makeMatchId(r.matchDate, r.homeTeam, r.awayTeam);
      const exists = check.get(matchId);
      if (!exists) {
        log(`⚠️  批量回填跳过: ${matchId} 未在 matches 表中找到`);
        continue;
      }
      const score = `${r.homeScore}-${r.awayScore}`;
      update.run(
        r.actualWdl, r.actualHandicapResult,
        score, r.homeScore + r.awayScore,
        now, matchId
      );
      matchIds.push(matchId);
    }
  });

  tx(results);
  log(`✅ 批量回填完成: ${matchIds.length} 场比赛`);
  return matchIds;
}

/**
 * 保存 SofaScore xG 数据到 five_leagues.db
 *
 * @param {Object} xgData - xG 数据
 * @param {number} xgData.homeTeamId - 主队 Team ID (five_leagues.db teams 表)
 * @param {number} xgData.awayTeamId - 客队 Team ID
 * @param {string} xgData.date - 比赛日期
 * @param {number} xgData.competitionId - 联赛 ID
 * @param {Object} xgData.stats - 详细统计
 */
export function saveXGData(xgData) {
  const {
    homeTeamId, awayTeamId, date, competitionId,
    homeGoals, awayGoals,
    homeXg, awayXg, homeXgot, awayXgot,
    homeShots, awayShots, homeShotsOnTarget, awayShotsOnTarget,
    homePossession, homeCorners, awayCorners,
    homeFouls, awayFouls, homeYellowCards, awayYellowCards,
    homeBigChances, awayBigChances
  } = xgData;

  const matchHash = makeMatchHash(date, String(homeTeamId), String(awayTeamId), `${homeGoals}-${awayGoals}`);
  const now = new Date().toISOString();

  const db = getFlDb();

  db.prepare(`
    INSERT OR IGNORE INTO matches
      (date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
       homeXg, awayXg, homeXgot, awayXgot, homeBigChances, awayBigChances,
       homeShots, homeShotsOnTarget, awayShots, awayShotsOnTarget,
       homePossession, homeCorners, awayCorners,
       homeFouls, awayFouls, homeYellowCards, awayYellowCards,
       matchHash, createdAt, updatedAt)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
    homeXg, awayXg, homeXgot, awayXgot, homeBigChances, awayBigChances,
    homeShots, homeShotsOnTarget, awayShots, awayShotsOnTarget,
    homePossession, homeCorners, awayCorners,
    homeFouls, awayFouls, homeYellowCards, awayYellowCards,
    matchHash, now, now
  );

  log(`✅ xG 数据已保存: ${date} (Team ${homeTeamId} vs ${awayTeamId})`);
  return matchHash;
}

/**
 * 查询比赛记录
 */
export function getMatchById(matchId) {
  const db = getOddsDb();
  return db.prepare('SELECT * FROM matches WHERE match_id = ?').get(matchId) || null;
}

/**
 * 查询指定日期范围内的比赛
 */
export function getMatchesByDateRange(startDate, endDate, league = null) {
  const db = getOddsDb();
  if (league) {
    return db.prepare(
      'SELECT * FROM matches WHERE match_date >= ? AND match_date <= ? AND match_type LIKE ? ORDER BY match_date'
    ).all(startDate, endDate, `%${league}%`);
  }
  return db.prepare(
    'SELECT * FROM matches WHERE match_date >= ? AND match_date <= ? ORDER BY match_date'
  ).all(startDate, endDate);
}

/**
 * 查询近 N 天比赛数 (pipeline eligibility 使用)
 */
export function countRecentMatches(days = 30) {
  const db = getOddsDb();
  const result = db.prepare(
    "SELECT COUNT(1) as cnt FROM matches WHERE match_date >= date('now', ?)"
  ).get(`-${days} days`);
  return result.cnt;
}

/**
 * 获取联赛分布统计 (pipeline eligibility 使用)
 */
export function getLeagueDistribution() {
  const db = getOddsDb();
  return db.prepare(`
    SELECT league, COUNT(1) as cnt FROM fbref_match_mapping
    GROUP BY league
  `).all();
}

/**
 * 关闭数据库连接 (进程退出时调用)
 */
export function close() {
  if (oddsDb) { try { oddsDb.close(); } catch (_) {} }
  if (flDb) { try { flDb.close(); } catch (_) {} }
  oddsDb = null;
  flDb = null;
  log('数据库连接已关闭');
}

// 优雅关闭
process.on('exit', close);
process.on('SIGINT', () => { close(); process.exit(0); });
process.on('SIGTERM', () => { close(); process.exit(0); });

export default {
  savePreMatchPrediction,
  batchSavePredictions,
  updatePostMatchResult,
  batchUpdatePostMatchResults,
  saveXGData,
  getMatchById,
  getMatchesByDateRange,
  countRecentMatches,
  getLeagueDistribution,
  makeMatchId,
  makeMatchHash,
  close,
};
