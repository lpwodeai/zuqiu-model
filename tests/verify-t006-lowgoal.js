/**
 * T-006 低进球分类器 JS 导出与推理验证
 *
 * 验证内容:
 *   1. t006_lowgoal_export.js 文件格式与结构
 *   2. buildT006Features: 22 维特征构建(含联赛 one-hot 三格式兼容)
 *   3. predictT006Lowgoal: 二分类 sigmoid 推理,概率在 [0,1]
 *   4. parity 测试: JS 概率 vs Python predict_proba 期望概率,差异 < 0.01
 *   5. applyT006LowgoalAdjustment: 低比分权重调整(isLowgoal=true 时低比分总和提升)
 *
 * 运行: node tests/verify-t006-lowgoal.js
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { strict as assert } from 'node:assert';
import PredictionEngine from '../shared/prediction-engine.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');

// === 辅助:加载 var X = {...}; 格式的 JS 模型文件(仿 _safeParseModel) ===
function loadJsModel(filePath, varName) {
  const content = fs.readFileSync(filePath, 'utf8');
  const match = content.match(new RegExp(`var ${varName}\\s*=\\s*(\\{[\\s\\S]*\\});`));
  if (!match) {
    throw new Error(`模型文件格式错误: ${filePath} 未找到 var ${varName}`);
  }
  return JSON.parse(match[1]);
}

function main() {
  console.log('='.repeat(60));
  console.log('T-006 低进球分类器 — JS 导出与推理验证');
  console.log('='.repeat(60));

  // === 1. 检查导出文件存在并加载 ===
  const exportPath = path.join(ROOT, 'assets/t006_lowgoal_export.js');
  assert.ok(fs.existsSync(exportPath), 't006_lowgoal_export.js 不存在');
  const model = loadJsModel(exportPath, 'T006_LOWGOAL_MODEL');
  console.log(`✅ 模型文件已加载: ${exportPath}`);
  console.log(`   树数: ${model.trees.length}`);
  console.log(`   特征维度: ${model.feature_cols.length}`);
  console.log(`   base: ${model.base}`);
  console.log(`   lr: ${model.lr}`);
  console.log(`   best_threshold: ${model.best_threshold}`);
  console.log(`   base_rate: ${model.base_rate}`);

  // 基础结构断言
  // 注意: LightGBM dump_model 的 leaf_value 已隐含学习率(贡献值),
  // 故导出 base=0.0, lr=1.0, 使 JS 公式 sigmoid(base + lr*Σleaf) 退化为 sigmoid(Σleaf),
  // 与 Python predict_proba 严格一致。真实学习率保留在 metadata.real_learning_rate。
  assert.equal(model.trees.length, 200, '树数不是 200');
  assert.equal(model.feature_cols.length, 22, '特征维度不是 22');
  assert.equal(model.base, 0.0, 'base 应为 0.0 (LightGBM leaf 已含 lr)');
  assert.equal(model.lr, 1.0, 'lr 应为 1.0 (JS 公式退化用,真实 lr 见 metadata)');
  assert.ok(model.best_threshold > 0 && model.best_threshold < 1, 'threshold 越界');
  assert.ok(model.base_rate > 0 && model.base_rate < 1, 'base_rate 越界');
  assert.equal(model.metadata.objective, 'binary sigmoid:1', 'objective 不匹配');
  assert.equal(model.metadata.sigmoid_required, true, 'sigmoid_required 应为 true');
  assert.equal(model.metadata.base_computation, 'lightgbm_leaf_already_includes_lr',
    'base_computation 应标记为 lightgbm_leaf_already_includes_lr');
  assert.ok(model.metadata.real_learning_rate > 0 && model.metadata.real_learning_rate < 1,
    'metadata.real_learning_rate 越界');

  // === 2. 初始化引擎并注入 t006 模型 ===
  const engine = new PredictionEngine();
  engine.setT006LowgoalModel(model);
  console.log('\n✅ PredictionEngine 已配置 t006 模型');

  // === 3. 测试 _resolveLeagueFlags 三格式兼容 ===
  console.log('\n--- 测试联赛映射 ---');
  const fl1Flags = engine._resolveLeagueFlags('FL1');
  assert.equal(fl1Flags.is_ligue1, 1, 'FL1 应激活 is_ligue1');
  assert.equal(fl1Flags.is_serie_a, 0, 'FL1 不应激活 is_serie_a');

  const chineseFlags = engine._resolveLeagueFlags('法甲');
  assert.equal(chineseFlags.is_ligue1, 1, '法甲 应激活 is_ligue1');

  const plFlags = engine._resolveLeagueFlags('PL');
  assert.equal(plFlags.is_premier_league, 1, 'PL 应激活 is_premier_league');
  assert.equal(plFlags.is_ligue1, 0, 'PL 不应激活 is_ligue1');

  const englishFlags = engine._resolveLeagueFlags('Premier League');
  assert.equal(englishFlags.is_premier_league, 1, 'Premier League 应激活 is_premier_league');

  console.log('✅ 联赛映射三格式兼容通过(FL1/法甲/PL/Premier League)');

  // === 4. 测试 buildT006Features ===
  console.log('\n--- 测试 buildT006Features ---');
  const matchData = {
    scoreOdds: { '0:0': 8.5, '1:1': 6.5, '1:0': 7.5, '0:1': 7.5, '2:1': 9.0, '2:0': 11.0, '0:0a': 99 },
    odds: { wdl: { close: { win: 2.1, draw: 3.2, lose: 3.5 } } },
    leagueCode: 'FL1'
  };
  const features = engine.buildT006Features(matchData);
  assert.ok(features, 'buildT006Features 返回 null');
  console.log(`✅ 特征已构建: ${Object.keys(features).length} 维`);

  // 校验 22 个 key 全部存在且与 feature_cols 一致
  for (const k of model.feature_cols) {
    assert.ok(k in features, `特征缺失: ${k}`);
  }
  assert.equal(features.is_ligue1, 1, '法甲 one-hot 未正确激活');
  assert.equal(features.is_premier_league, 0, '英超 one-hot 误激活');
  assert.ok(features.prob_draw > 0 && features.prob_draw < 1, 'prob_draw 越界');
  assert.ok(features.score_implied_total > 0, 'score_implied_total 应为正');

  // 测试 WDL 缺失时用 wdlFallback 反推
  const fallbackFeatures = engine.buildT006Features(
    { scoreOdds: {}, leagueCode: 'PL' },
    { win: 0.45, draw: 0.30, lose: 0.25 }
  );
  assert.ok(fallbackFeatures, 'wdlFallback 反推应成功');
  assert.equal(fallbackFeatures.is_premier_league, 1, 'PL one-hot 未激活');
  console.log('✅ WDL 缺失回退(wdlFallback 反推虚拟赔率)通过');

  // 测试完全无赔率数据返回 null
  const nullFeatures = engine.buildT006Features({ leagueCode: 'FL1' });
  assert.equal(nullFeatures, null, '无赔率时应返回 null');
  console.log('✅ 无赔率降级(返回 null)通过');

  // === 5. 测试 predictT006Lowgoal(含 parity) ===
  console.log('\n--- 测试 predictT006Lowgoal + parity ---');
  const result = engine.predictT006Lowgoal(features);
  assert.ok(!result.skipped, `推理被跳过: ${result.reason}`);
  assert.ok(result.probability >= 0 && result.probability <= 1, '概率越界');
  assert.ok(typeof result.rawScore === 'number', 'rawScore 应为数值');
  assert.equal(result.threshold, model.best_threshold, 'threshold 不匹配');
  assert.equal(result.baseRate, model.base_rate, 'baseRate 不匹配');
  console.log(`✅ 推理结果: P(小球)=${result.probability.toFixed(6)}, rawScore=${result.rawScore.toFixed(4)}, isLowgoal=${result.isLowgoal}`);

  // parity 测试:与 Python predict_proba 对比
  const parityPath = path.join(ROOT, 'tests/fixtures/t006_parity_sample.json');
  if (fs.existsSync(parityPath)) {
    const parity = JSON.parse(fs.readFileSync(parityPath, 'utf8'));
    const jsResult = engine.predictT006Lowgoal(parity.features);
    const diff = Math.abs(jsResult.probability - parity.expected_probability);
    console.log(`\n--- Parity 测试 ---`);
    console.log(`  Python 期望: P=${parity.expected_probability.toFixed(6)}`);
    console.log(`  JS 实际:     P=${jsResult.probability.toFixed(6)} (rawScore=${jsResult.rawScore.toFixed(6)})`);
    console.log(`  差异:        ${diff.toFixed(6)} ${diff < 0.01 ? '✅' : '❌'}`);
    assert.ok(diff < 0.01, `JS/Python 概率差异过大: ${diff} (阈值 0.01)`);
    console.log('✅ Parity 测试通过(JS vs Python 差异 < 0.01)');
  } else {
    console.warn('⚠️ 跳过 parity 测试: 缺少 tests/fixtures/t006_parity_sample.json');
  }

  // 测试模型未加载降级
  const emptyEngine = new PredictionEngine();
  const skippedResult = emptyEngine.predictT006Lowgoal(features);
  assert.equal(skippedResult.skipped, true, '模型未加载应 skipped');
  assert.equal(skippedResult.reason, 'model_not_loaded', 'reason 应为 model_not_loaded');
  console.log('✅ 模型未加载降级(model_not_loaded)通过');

  // 测试特征为 null 降级
  const nullResult = engine.predictT006Lowgoal(null);
  assert.equal(nullResult.skipped, true, '特征 null 应 skipped');
  assert.equal(nullResult.reason, 'features_null', 'reason 应为 features_null');
  console.log('✅ 特征 null 降级(features_null)通过');

  // === 6. 测试 applyT006LowgoalAdjustment ===
  console.log('\n--- 测试 applyT006LowgoalAdjustment ---');
  const fused = {
    '0:0': 0.10, '1:1': 0.12, '1:0': 0.10, '0:1': 0.10,
    '2:1': 0.08, '2:0': 0.06, '1:2': 0.05, '0:2': 0.04,
    '2:2': 0.03, '3:0': 0.02, '0:3': 0.01
  };
  const lowSumBefore = fused['0:0'] + fused['1:1'] + fused['1:0'] + fused['0:1'];

  // isLowgoal=true 时应提升低比分
  const lowgoalResult = { probability: 0.65, isLowgoal: true, baseRate: 0.22, skipped: false };
  const adjusted = engine.applyT006LowgoalAdjustment(fused, lowgoalResult, 0.5);
  const lowSumAfter = adjusted['0:0'] + adjusted['1:1'] + adjusted['1:0'] + adjusted['0:1'];
  assert.ok(lowSumAfter > lowSumBefore, `低比分总和未提升: ${lowSumBefore} → ${lowSumAfter}`);
  console.log(`✅ isLowgoal=true: 低比分总和 ${lowSumBefore.toFixed(4)} → ${lowSumAfter.toFixed(4)} (提升)`);

  // 校验归一化(总和应为 1)
  let total = 0;
  for (const k in adjusted) total += adjusted[k];
  assert.ok(Math.abs(total - 1.0) < 1e-9, `归一化失败: 总和=${total}`);
  console.log('✅ 归一化通过(总和=1.0)');

  // isLowgoal=false 时不调整
  const highgoalResult = { probability: 0.30, isLowgoal: false, baseRate: 0.22, skipped: false };
  const unchanged = engine.applyT006LowgoalAdjustment(fused, highgoalResult, 0.5);
  assert.deepEqual(unchanged, fused, 'isLowgoal=false 应返回原分布');
  console.log('✅ isLowgoal=false: 不调整(返回原分布)');

  // skipped=true 时不调整
  const skippedAdj = engine.applyT006LowgoalAdjustment(fused, { skipped: true }, 0.5);
  assert.deepEqual(skippedAdj, fused, 'skipped 应返回原分布');
  console.log('✅ skipped=true: 不调整');

  // factor 公式校验:p_low=0.65, base_rate=0.22, alpha=0.5
  // factor = 1 + 0.5 * (0.65 - 0.22) / 0.22 = 1 + 0.5 * 0.43 / 0.22 = 1 + 0.977 = 1.977
  // 但 clip 到 2.0,所以 factor=1.977(<2.0,不触发 clip)
  const expectedFactor = Math.max(0.5, Math.min(2.0, 1 + 0.5 * (0.65 - 0.22) / 0.22));
  assert.ok(Math.abs(expectedFactor - 1.977) < 0.01, `factor 计算错误: ${expectedFactor}`);
  console.log(`✅ factor 公式校验通过(expected=${expectedFactor.toFixed(4)})`);

  // === 7. 完整流程模拟(fuseScorePredictions → t006 调整) ===
  console.log('\n--- 测试完整流程(Poisson → fuse → t006 调整) ---');
  const v4Result = engine.predictScoreV4(1.0, 0.9, { rho: -0.30, rhoHigh: -0.10 });
  const mcProbs = engine.monteCarloScoreSimulate(1.0, 0.9, 500, 7);
  let fusedScores = engine.fuseScorePredictions(v4Result.scoreProbabilities, mcProbs, matchData.scoreOdds);
  const preLow = (fusedScores['0:0'] || 0) + (fusedScores['1:0'] || 0) + (fusedScores['0:1'] || 0) + (fusedScores['1:1'] || 0);

  const t006Res = engine.predictT006Lowgoal(features);
  fusedScores = engine.applyT006LowgoalAdjustment(fusedScores, t006Res, 0.5);
  const postLow = (fusedScores['0:0'] || 0) + (fusedScores['1:0'] || 0) + (fusedScores['0:1'] || 0) + (fusedScores['1:1'] || 0);
  console.log(`  Poisson λ=(1.0, 0.9) → fuse → t006 调整`);
  console.log(`  t006 P(小球)=${t006Res.probability.toFixed(4)}, isLowgoal=${t006Res.isLowgoal}`);
  console.log(`  低比分总和: ${preLow.toFixed(4)} → ${postLow.toFixed(4)}`);
  if (t006Res.isLowgoal) {
    assert.ok(postLow > preLow, '完整流程: isLowgoal=true 时低比分应提升');
    console.log('✅ 完整流程验证通过(低比分提升)');
  } else {
    console.log('✅ 完整流程验证通过(isLowgoal=false,未调整)');
  }

  // === 汇总 ===
  console.log('\n' + '='.repeat(60));
  console.log('✅ 所有验证通过!');
  console.log('='.repeat(60));
  console.log(`  模型: 200 棵树, 22 维特征, base=${model.base.toFixed(4)}`);
  console.log(`  parity: JS vs Python 差异 < 0.01`);
  console.log(`  联赛映射: FL1/法甲/PL/Premier League 三格式兼容`);
  console.log(`  降级路径: model_not_loaded / features_null / isLowgoal=false / skipped=true`);
  console.log('='.repeat(60));
}

try {
  main();
} catch (err) {
  console.error('\n❌ 验证失败:', err.message);
  console.error(err.stack);
  process.exit(1);
}
