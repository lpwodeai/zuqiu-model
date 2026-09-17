import path from 'path';
import { fileURLToPath } from 'url';
import { db } from '../server/database/index.js';
import { dataParser } from '../server/services/data-parser.js';
import { matchDAL, teamDAL, competitionDAL } from '../server/services/data-dal.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const COMPETITION_MAP = {
  'Premier League': { code: 'PL', country: 'England' },
  'Serie A': { code: 'SA', country: 'Italy' },
  'La Liga': { code: 'LL', country: 'Spain' },
  'Bundesliga': { code: 'BL1', country: 'Germany' },
  'Ligue 1': { code: 'FL1', country: 'France' }
};

async function importMatchesFromCsv(filePath) {
  console.log(`开始导入数据文件: ${filePath}`);
  
  await db.connect();
  await db.init();

  try {
    const result = dataParser.parseFile(filePath);
    
    console.log('\n=== 数据解析报告 ===');
    console.log(`总行数: ${result.stats.totalRows}`);
    console.log(`有效行: ${result.stats.validRows}`);
    console.log(`无效行: ${result.stats.invalidRows}`);
    console.log(`成功率: ${((result.stats.validRows / result.stats.totalRows) * 100).toFixed(2)}%`);
    console.log(`缺失值: ${result.stats.missingValues}`);

    if (result.errors.length > 0) {
      console.log('\n=== 错误列表 ===');
      result.errors.slice(0, 10).forEach(e => console.log(e.message));
      if (result.errors.length > 10) {
        console.log(`... 还有 ${result.errors.length - 10} 个错误`);
      }
    }

    if (result.warnings.length > 0) {
      console.log('\n=== 警告列表 ===');
      result.warnings.slice(0, 5).forEach(w => console.log(w.message));
      if (result.warnings.length > 5) {
        console.log(`... 还有 ${result.warnings.length - 5} 个警告`);
      }
    }

    const teams = new Set();
    const competitions = new Set();
    
    result.data.forEach(row => {
      teams.add(row.home_team);
      teams.add(row.away_team);
      competitions.add(row.competition);
    });

    console.log(`\n=== 数据统计 ===`);
    console.log(`涉及球队数: ${teams.size}`);
    console.log(`涉及联赛数: ${competitions.size}`);

    console.log('\n=== 开始导入联赛数据 ===');
    for (const compName of competitions) {
      const info = COMPETITION_MAP[compName] || { code: null, country: null };
      await competitionDAL.findOrCreate(compName, info);
      console.log(`导入联赛: ${compName}`);
    }

    console.log('\n=== 开始导入球队数据 ===');
    const teamResults = await teamDAL.bulkInsert(
      Array.from(teams).map(name => ({ name, shortName: name }))
    );
    console.log(`球队导入完成: 新增 ${teamResults.inserted} 支，跳过 ${teamResults.skipped} 支`);

    console.log('\n=== 开始导入比赛数据 ===');
    const matchRecords = [];
    
    for (const row of result.data) {
      const homeTeam = await teamDAL.getByName(row.home_team);
      const awayTeam = await teamDAL.getByName(row.away_team);
      const competition = await competitionDAL.getByName(row.competition);

      if (!homeTeam || !awayTeam || !competition) {
        console.warn(`跳过比赛: ${row.date} ${row.home_team} vs ${row.away_team} - 缺少关联数据`);
        continue;
      }

      const matchHash = `${row.date}_${row.home_team}_${row.away_team}`;
      
      matchRecords.push({
        date: row.date,
        homeTeamId: homeTeam.id,
        awayTeamId: awayTeam.id,
        homeGoals: row.home_goals || 0,
        awayGoals: row.away_goals || 0,
        competitionId: competition.id,
        homeXg: row.home_xg || null,
        awayXg: row.away_xg || null,
        homeXgot: row.home_xgot || null,
        awayXgot: row.away_xgot || null,
        homeBigChances: row.home_big_chances || 0,
        awayBigChances: row.away_big_chances || 0,
        homeXa: row.home_xa || null,
        awayXa: row.away_xa || null,
        homeSaves: row.home_saves || 0,
        awaySaves: row.away_saves || 0,
        homeTouchesBox: row.home_touches_box || 0,
        awayTouchesBox: row.away_touches_box || 0,
        homeHitsPost: row.home_hits_post || 0,
        awayHitsPost: row.away_hits_post || 0,
        homeShots: row.home_shots || 0,
        homeShotsOnTarget: row.home_shots_on_target || 0,
        awayShots: row.away_shots || 0,
        awayShotsOnTarget: row.away_shots_on_target || 0,
        homePossession: row.home_possession || null,
        homeCorners: row.home_corners || 0,
        awayCorners: row.away_corners || 0,
        homeFouls: row.home_fouls || 0,
        awayFouls: row.away_fouls || 0,
        homeYellowCards: row.home_yellow_cards || 0,
        awayYellowCards: row.away_yellow_cards || 0,
        matchHash
      });
    }

    const matchResults = await matchDAL.bulkInsert(matchRecords);
    console.log(`比赛导入完成: 新增 ${matchResults.inserted} 场，跳过 ${matchResults.skipped} 场`);

    if (matchResults.errors.length > 0) {
      console.log('\n=== 导入错误 ===');
      matchResults.errors.slice(0, 5).forEach(e => console.log(`${e.match}: ${e.error}`));
    }

    console.log('\n=== 导入完成 ===');
    console.log('数据库统计:');
    const teamsCount = await db.get('SELECT COUNT(*) as count FROM teams');
    const matchesCount = await db.get('SELECT COUNT(*) as count FROM matches');
    const compsCount = await db.get('SELECT COUNT(*) as count FROM competitions');
    
    console.log(`- 球队: ${teamsCount.count}`);
    console.log(`- 比赛: ${matchesCount.count}`);
    console.log(`- 联赛: ${compsCount.count}`);

  } catch (err) {
    console.error('导入失败:', err.message);
    process.exit(1);
  } finally {
    await db.close();
  }
}

const csvPath = path.join(__dirname, '../h2h_full_stats_sample(1).csv');
importMatchesFromCsv(csvPath);