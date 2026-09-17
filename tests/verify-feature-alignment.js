/**
 * P0-D 训练-推理特征对齐强制测试
 *
 * 验证内容:
 *   1. 训练端特征清单（feature_scaler_params.js feature_names, 254 维）
 *      与推理端 buildFeatures 覆盖关系 —— 除「数据源缺口白名单」外必须全覆盖
 *   2. P0-D 补全的 17 维可计算特征数值对齐（与 feature_utils.py 公式一致）:
 *      - 联赛 one-hot (5): league_英超/德甲/意甲/法甲/西甲
 *      - 时间特征 (6): month/day_of_week/is_weekend/is_early/mid/late_season
 *      - 实力差距 (3): strength_closeness/gap_indicator/balance
 *      - 赔率波动相对 (3): draw_odds_stability/odds_volatility_balance/draw_vol_relative
 *   3. 缺口白名单外的任何训练端特征缺失 → 测试失败（强制新特征在推理端实现或显式豁免）
 *
 * 数据源缺口白名单（推理端无数据源，normalizeFeatures 均值填充 → 归一化 0）:
 *   - sofa_*  (68 维): SofaScore 球员级数据，JS 无此数据源
 *   - pa_*    (24 维): 球员可用性/疲劳/阵容分析，JS 无阵容数据
 *   - mkt_*   (10 维): 百家欧指共识，需 odds.db odds500_* 表
 *   - odds_ts_* (12 维): 赔率时序（开→终盘去水漂移等），需 wdl/hcp/tg history 时序表
 *
 * 运行: node tests/verify-feature-alignment.js
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { strict as assert } from 'node:assert';
import PredictionEngine from '../shared/prediction-engine.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');

// 数据源缺口白名单：这些特征推理端无数据源，允许不实现（均值填充 → 归一化 0）
function isGapFeature(name) {
  return name.startsWith('sofa_') || name.startsWith('pa_') || name.startsWith('mkt_') || name.startsWith('odds_ts_');
}

// === 加载 feature_scaler_params.js ===
function loadScalerParams() {
  const content = fs.readFileSync(path.join(ROOT, 'assets/feature_scaler_params.js'), 'utf8');
  const match = content.match(/var FEATURE_SCALER_PARAMS = (\{[\s\S]*\});/);
  if (!match) throw new Error('feature_scaler_params.js 格式错误');
  return JSON.parse(match[1]);
}

// === 构造最小可推理的球队样本 ===
function makeTeam(league, attack, defence) {
  return {
    name: '测试队',
    league,
    attack,
    defence,
    xg: 1.4,
    xGOT: 1.1,
    avgGoals: 1.35,
    avgOppGoals: 1.1,
    winRate: 0.45,
    drawRate: 0.28,
    lossRate: 0.27,
    goalsStd: 0.8,
    gamesPlayed: 10,
    weightedWinRate: 0.44,
    weightedAvgGoals: 1.3,
    homeWinRate: 0.5,
    awayWinRate: 0.4,
    homeGoals: 1.6,
    awayGoals: 1.1,
    recentForm: 0.3,
    formTrend: 0.1,
    consecutiveWins: 2,
    consecutiveLosses: 0,
    consecutiveUndefeated: 3,
    shots: 13,
    shotsOnTarget: 4.5,
    possession: 52,
    corners: 5.5
  };
}

function near(actual, expected, eps = 1e-6) {
  assert.ok(Math.abs(actual - expected) <= eps, `期望 ${expected} 实际 ${actual} (eps=${eps})`);
}

function main() {
  console.log('='.repeat(60));
  console.log('P0-D 训练-推理特征对齐 — 强制对齐测试');
  console.log('='.repeat(60));

  const scaler = loadScalerParams();
  const featureNames = scaler.feature_names;
  console.log(`训练端特征清单维度: ${featureNames.length}`);
  assert.ok(featureNames.length >= 200, '训练端特征清单异常（<200 维）');

  const engine = new PredictionEngine();
  engine.featureScalerParams = scaler;

  // === 1. buildFeatures 覆盖检查 ===
  const teamA = makeTeam('PL', 1.4, 0.8);
  const teamB = makeTeam('BL1', 1.1, 1.0);
  const matchContext = {
    date: '2026-09-10',
    elo: { homeElo: 1600, awayElo: 1500, homeExpected: 0.64, awayExpected: 0.36, drawProb: 0.25, confidence: 0.25 },
    h2h: { matches: 4, homeWinRate: 0.5, awayWinRate: 0.25, drawRate: 0.25, avgGoalsHome: 1.5, avgGoalsAway: 0.75, avgTotalGoals: 2.25, goalDiffAvg: 0.75, lastResult: 1, homeStreak: 2, awayStreak: 0 },
    oddsTiming: { winVolatility: 0.05, drawVolatility: 0.02, loseVolatility: 0.06, winAcceleration: 0.01, lateTrend: -0.03, earlyTrend: 0.02, midStability: 0.6, suddenJump: 0.1, updateFrequency: 3.5, totalChange: 0.4 },
    scoreOdds: { modeProb: 0.12, entropy: 3.2, homeWinProb: 0.42, drawProb: 0.29, awayWinProb: 0.29, over25Prob: 0.55, expectedGoals: 2.6, top3Concentration: 0.31 },
    odds: {
      wdlOpenWin: 2.2, wdlOpenDraw: 3.3, wdlOpenLose: 3.1,
      wdlCloseWin: 2.1, wdlCloseDraw: 3.4, wdlCloseLose: 3.3,
      wdlImpliedWin: 0.45, wdlImpliedDraw: 0.28, wdlImpliedLose: 0.27,
      wdlOverround: 1.08,
      hasWdlOdds: true, hasHcpOdds: true, hasTgOdds: true
    }
  };

  const features = engine.buildFeatures(teamA, teamB, {}, matchContext);

  const keys = new Set(Object.keys(features));
  const missing = featureNames.filter(n => !keys.has(n));
  const notGapMissing = missing.filter(n => !isGapFeature(n));

  console.log(`buildFeatures 输出键数: ${keys.size}`);
  console.log(`缺失特征: ${missing.length}（其中白名单缺口 ${missing.length - notGapMissing.length}）`);

  // 白名单外缺失 → 测试失败（强制实现）
  assert.deepStrictEqual(
    notGapMissing,
    [],
    `以下非白名单特征缺失（必须在 buildFeatures 实现或加入白名单）:\n  ${notGapMissing.join('\n  ')}`
  );

  // 缺口白名单必须能覆盖全部训练端缺口（防止新特征被悄悄忽略）
  const gapInList = featureNames.filter(isGapFeature);
  console.log(`缺口白名单覆盖: ${gapInList.length} 维（sofa_*/pa_*/mkt_*/odds_ts_*）`);

  // === 2. 17 维可计算特征数值对齐（与 feature_utils.py 公式一致）===
  // 联赛 one-hot: teamA.league='PL' → 英超=1
  assert.equal(features.league_英超, 1, 'PL 应映射到 league_英超=1');
  for (const lg of ['league_德甲', 'league_意甲', 'league_法甲', 'league_西甲']) {
    assert.equal(features[lg], 0, `${lg} 应为 0`);
  }

  // 时间特征: 2026-09-10（周四）
  assert.equal(features.month, 9);
  assert.equal(features.day_of_week, 3);       // Python dayofweek: Monday=0, Thursday=3
  assert.equal(features.is_weekend, 0);
  assert.equal(features.is_early_season, 1);
  assert.equal(features.is_mid_season, 0);
  assert.equal(features.is_late_season, 0);

  // 实力差距: elo 1600 vs 1500
  near(features.strength_closeness, 0.36787944117144233);
  near(features.strength_gap_indicator, 0.5);
  near(features.strength_balance, 1500 / (1600 + 1e-6));

  // 赔率波动相对: 无 oddsTiming 上下文 → wdl_*_volatility=0（训练端无数据行同值）
  const noTimingFeatures = engine.buildFeatures(teamA, teamB, {}, {
    date: '2026-09-10',
    elo: matchContext.elo
  });
  near(noTimingFeatures.draw_odds_stability, 1.0);
  near(noTimingFeatures.odds_volatility_balance, 0.0);
  near(noTimingFeatures.draw_vol_relative, 0.0);

  // 有时序数据时（runtime 未接入，仅验证公式分支与 feature_utils 一致）
  near(features.draw_odds_stability, 1.0 / 1.02);
  near(features.odds_volatility_balance, 0.0169967862843964);
  near(features.draw_vol_relative, 0.02 / (0.05 + 0.06 + 1e-6));

  // === 3. normalizeFeatures: 白名单缺口均值填充 → 归一化 0，全 254 维可输出 ===
  const normalized = engine.normalizeFeatures(features);
  const normalizedKeys = Object.keys(normalized);
  assert.equal(normalizedKeys.length, featureNames.length, 'normalizeFeatures 应输出全部训练端特征');
  for (const n of featureNames) {
    assert.ok(normalized[n] !== undefined && isFinite(normalized[n]), `归一化特征 ${n} 非法`);
  }
  // 白名单缺口应归一化为 0（均值填充）
  for (const n of gapInList) {
    near(normalized[n], 0.0, 1e-9);
  }

  // === 4. getFeatureVector: 维度与清单一致 ===
  const vector = engine.getFeatureVector(features);
  assert.equal(vector.length, featureNames.length, 'getFeatureVector 维度应与训练端一致');

  console.log('\n[PASS] P0-D 训练-推理特征对齐验证通过');
  console.log(`   - 254 维全覆盖（${keys.size} 直接输出 + ${gapInList.length} 白名单均值填充）`);
  console.log('   - 17 维可计算特征与 feature_utils.py 公式数值对齐');
}

main();
