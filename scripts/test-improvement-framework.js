import OddsDatabase from './assets/modules/data/odds-database.js';
import DataValidator from './assets/modules/data/data-validator.js';
import FeatureExtractor from './assets/modules/features/feature-extractor.js';
import { GradientBoostingClassifier, PoissonRegression, EnsembleModel } from './assets/modules/models/ml-framework.js';
import { WalkForwardBacktest } from './assets/modules/inference/walk-forward-backtest.js';
import { ModelMonitor, AutoRetrainer } from './assets/modules/inference/model-monitor.js';
import fs from 'fs';
import path from 'path';

async function loadExistingBacktestData() {
  const backtestDir = path.join(process.cwd(), 'data-archive', 'validation-tests');
  const files = fs.readdirSync(backtestDir).filter(f => f.endsWith('-backtest.js'));

  const matches = [];

  for (const file of files) {
    try {
      const module = await import(`./data-archive/validation-tests/${file}`);
      if (module.default && module.default.matchData && module.default.actualResults) {
        const matchData = module.default.matchData;
        const actualResults = module.default.actualResults;
        
        const odds = matchData.odds || matchData;
        const matchInfo = matchData.match || matchData;

        matches.push({
          matchId: matchInfo.matchId || `${matchInfo.teamA || 'home'}-${matchInfo.teamB || 'away'}-${matchInfo.matchDate || 'unknown'}`,
          homeTeam: matchInfo.teamA || matchInfo.homeTeam || 'Home',
          awayTeam: matchInfo.teamB || matchInfo.awayTeam || 'Away',
          matchDate: matchInfo.matchDate || '2026-06-15T00:00:00Z',
          matchType: matchData.matchType || 'group_stage',
          handicap: odds.handicapValue ? parseInt(odds.handicapValue) : (matchInfo.handicap ? parseInt(matchInfo.handicap) : -1),
          wdlHistory: odds.winDrawLoss ? odds.winDrawLoss.map(o => ({
            timestamp: o.time,
            winA: o.winA,
            draw: o.draw,
            winB: o.winB
          })) : [],
          handicapHistory: odds.handicap ? odds.handicap.map(o => ({
            timestamp: o.time,
            hcp_win: o.win,
            hcp_draw: o.draw,
            hcp_lose: o.lose
          })) : [],
          totalGoalsHistory: odds.totalGoals || [],
          scoreHistory: odds.scores ? odds.scores.map(o => ({
            timestamp: o.time,
            scores: [
              ...(o.homeWins || []).map(s => ({ score: s.score, odds: s.odds })),
              ...(o.draws || []).map(s => ({ score: s.score, odds: s.odds })),
              ...(o.awayWins || []).map(s => ({ score: s.score, odds: s.odds }))
            ]
          })) : [],
          actualResult: {
            wdl: actualResults.winDrawLoss,
            handicap: actualResults.handicap,
            score: actualResults.score,
            totalGoals: parseInt(actualResults.totalGoals)
          }
        });
      }
    } catch (e) {
      console.warn(`无法加载文件 ${file}: ${e.message}`);
    }
  }

  return matches.sort((a, b) => new Date(a.matchDate) - new Date(b.matchDate));
}

async function main() {
  console.log('='.repeat(80));
  console.log('赔率预测系统准确度提升方案 - 框架验证');
  console.log('='.repeat(80));
  console.log('\n');

  console.log('1. 加载现有回测数据...');
  const matches = await loadExistingBacktestData();
  console.log(`   加载了 ${matches.length} 场比赛数据`);
  console.log('');

  console.log('2. 数据质量校验...');
  const validator = new DataValidator();
  let validCount = 0;
  let warningCount = 0;
  let errorCount = 0;

  matches.forEach((match, idx) => {
    const result = validator.validateCompleteMatch(match);
    if (result.valid) validCount++;
    warningCount += result.warnings.length;
    errorCount += result.errors.length;
  });

  console.log(`   有效数据: ${validCount}/${matches.length}`);
  console.log(`   警告数: ${warningCount}`);
  console.log(`   错误数: ${errorCount}`);
  console.log('');

  console.log('3. 特征提取...');
  const extractor = new FeatureExtractor();
  const featuresList = matches.map(match => {
    const features = extractor.extractAllFeatures(match);
    return { ...match, features };
  });

  const featureCount = featuresList[0] ? Object.keys(featuresList[0].features).length : 0;
  console.log(`   每场比赛提取了 ${featureCount} 个特征`);
  console.log('');

  console.log('4. Walk-Forward回测...');
  const backtest = new WalkForwardBacktest({
    trainWindowSize: 20,
    testWindowSize: 5,
    stride: 5,
    verbose: true
  });

  const validMatches = featuresList.filter(m => 
    m.actualResult && m.actualResult.handicap && 
    Object.keys(m.features).length > 0
  );

  if (validMatches.length >= 30) {
    const modelFactory = () => new GradientBoostingClassifier({
      nEstimators: 30,
      learningRate: 0.1,
      maxDepth: 3
    });

    const results = backtest.run(validMatches, modelFactory, extractor);

    console.log('\n');
    console.log('='.repeat(80));
    console.log('回测结果汇总');
    console.log('='.repeat(80));
    console.log(`整体准确率: ${(results.overallMetrics.overallAccuracy * 100).toFixed(2)}%`);
    console.log(`总折叠数: ${results.overallMetrics.totalFolds}`);
    console.log(`总样本数: ${results.overallMetrics.totalSamples}`);
    console.log('');

    ['accuracy', 'precision', 'recall', 'f1'].forEach(metric => {
      const m = results.overallMetrics[metric];
      if (m) {
        console.log(`${metric.toUpperCase()}:`);
        console.log(`  均值: ${(m.mean * 100).toFixed(2)}%`);
        console.log(`  标准差: ${(m.std * 100).toFixed(2)}%`);
        console.log(`  范围: ${(m.min * 100).toFixed(2)}% - ${(m.max * 100).toFixed(2)}%`);
      }
    });

    console.log('');
    console.log(backtest.generateReport(results));

    console.log('5. 模型监控验证...');
    const monitor = new ModelMonitor();
    
    results.detailedResults.forEach(result => {
      monitor.recordPrediction(result);
    });

    results.folds.forEach(fold => {
      monitor.recordPerformance(fold.metrics);
    });

    console.log(monitor.generatePerformanceReport());

    console.log('6. 自动训练器验证...');
    const retrainer = new AutoRetrainer();
    results.folds.forEach(fold => {
      retrainer.recordModel({ type: 'GradientBoosting' }, fold.metrics);
    });

    const bestModel = retrainer.getBestModel();
    console.log(`最佳模型版本: ${bestModel?.version}`);
    console.log(`最佳准确率: ${(bestModel?.metrics.accuracy * 100).toFixed(2)}%`);
    console.log('');

    console.log('7. 数据库存储验证...');
    const db = new OddsDatabase();
    try {
      await db.init();
      
      for (const match of matches.slice(0, 5)) {
        await db.insertMatch({
          matchId: match.matchId,
          homeTeam: match.homeTeam,
          awayTeam: match.awayTeam,
          matchDate: match.matchDate,
          matchType: match.matchType,
          handicap: match.handicap
        });
        
        if (match.actualResult) {
          await db.updateMatchResult(match.matchId, match.actualResult);
        }

        if (match.wdlHistory) {
          await db.insertWdlHistory(match.matchId, match.wdlHistory);
        }

        if (match.handicapHistory) {
          await db.insertHandicapHistory(match.matchId, match.handicapHistory);
        }

        if (match.totalGoalsHistory) {
          await db.insertTotalGoalsHistory(match.matchId, match.totalGoalsHistory);
        }

        if (match.scoreHistory) {
          await db.insertScoreHistory(match.matchId, match.scoreHistory);
        }

        if (match.features) {
          await db.insertFeatures(match.matchId, match.features);
        }
      }

      const storedMatches = await db.getAllMatches();
      console.log(`数据库中存储了 ${storedMatches.length} 场比赛`);

      const testMatch = await db.getMatchWithHistory(matches[0].matchId);
      console.log(`测试比赛: ${testMatch.match.home_team} vs ${testMatch.match.away_team}`);
      console.log(`WDL历史记录数: ${testMatch.wdl.length}`);
      console.log(`让球历史记录数: ${testMatch.handicap.length}`);
      console.log(`总进球历史记录数: ${testMatch.totalGoals.length}`);
      console.log(`比分历史记录数: ${testMatch.scores.length}`);
    } catch (e) {
      console.log(`数据库操作跳过（SQLite依赖问题）: ${e.message}`);
    } finally {
      await db.close();
    }
  } else {
    console.log(`数据量不足，需要至少30场有效比赛，当前只有${validMatches.length}场`);
  }

  console.log('\n');
  console.log('='.repeat(80));
  console.log('框架验证完成');
  console.log('='.repeat(80));
}

main().catch(e => {
  console.error('执行失败:', e);
  process.exit(1);
});
