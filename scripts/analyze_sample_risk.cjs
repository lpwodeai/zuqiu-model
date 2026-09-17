const Database = require('better-sqlite3');
const path = require('path');

const ODDS_DB = path.join(__dirname, '..', 'data', 'odds.db');
const oddsDb = new Database(ODDS_DB, { readonly: true });

const TODAY = '2026-08-12';
const LATEST_MATCH = oddsDb.prepare("SELECT MAX(match_date) as d FROM matches").get().d;

console.log(`\n=== 数据量风险评估 ===`);
console.log(`最新比赛日期: ${LATEST_MATCH}`);
console.log(`评估基准日: ${TODAY}`);
console.log(`距最新比赛已过 ${(new Date(TODAY) - new Date(LATEST_MATCH)) / 86400000} 天\n`);

// ============================================================
// 1. 不同时间窗口的绝对样本量
// ============================================================
console.log('--- 1. 不同时间窗口的样本量 ---');
const windowDefs = [
  { label: '全量3赛季', days: 99999 },
  { label: '最近3个赛季(1095天)', days: 1095 },
  { label: '最近2个赛季(730天)', days: 730 },
  { label: '最近1.5个赛季(540天)', days: 540 },
  { label: '最近1个赛季(365天)', days: 365 },
  { label: '最近9个月(270天)', days: 270 },
  { label: '最近半年(180天)', days: 180 },
  { label: '最近90天', days: 90 },
];

const windowStats = [];
for (const w of windowDefs) {
  const cutoff = dateAdd(TODAY, -w.days);
  const r = oddsDb.prepare(`
    SELECT 
      COUNT(*) as total,
      SUM(CASE WHEN actual_wdl='胜' THEN 1 ELSE 0 END) as wins,
      SUM(CASE WHEN actual_wdl='平' THEN 1 ELSE 0 END) as draws,
      SUM(CASE WHEN actual_wdl='负' THEN 1 ELSE 0 END) as losses
    FROM matches
    WHERE match_date >= date(?)
  `).get(cutoff);
  
  const leagues = oddsDb.prepare(`
    SELECT 
      SUM(CASE WHEN match_type LIKE '%英超%' THEN 1 ELSE 0 END) as epl,
      SUM(CASE WHEN match_type LIKE '%西甲%' THEN 1 ELSE 0 END) as laliga,
      SUM(CASE WHEN match_type LIKE '%意甲%' THEN 1 ELSE 0 END) as seriea,
      SUM(CASE WHEN match_type LIKE '%德甲%' THEN 1 ELSE 0 END) as bundesliga,
      SUM(CASE WHEN match_type LIKE '%法甲%' THEN 1 ELSE 0 END) as ligue1
    FROM matches
    WHERE match_date >= date(?)
  `).get(cutoff);

  windowStats.push({
    label: w.label,
    cutoff,
    total: r.total,
    wins: r.wins || 0,
    draws: r.draws || 0,
    losses: r.losses || 0,
    ...leagues,
  });

  const risk = assessCountRisk(r.total);
  const drawRisk = r.draws !== undefined ? assessMinorityRisk(r.draws, r.total) : 'N/A';
  const minLeague = Math.min(leagues.epl, leagues.laliga, leagues.seriea, leagues.bundesliga, leagues.ligue1);
  const leagueRisk = assessCountRisk(minLeague);

  console.log(`\n${w.label} (>=${cutoff}):`);
  console.log(`  总样本: ${r.total}  ${risk}`);
  console.log(`  胜/平/负: ${r.wins}/${r.draws}/${r.losses}  平局风险:${drawRisk}`);
  console.log(`  联赛(英/西/意/德/法): ${leagues.epl}/${leagues.laliga}/${leagues.seriea}/${leagues.bundesliga}/${leagues.ligue1}  最少联赛:${minLeague} ${leagueRisk}`);
}

// ============================================================
// 2. 时间衰减权重的有效样本量分析
// ============================================================
console.log('\n\n--- 2. 时间衰减权重有效样本量（按比赛天数差） ---');

// 取所有比赛+日期
const allMatches = oddsDb.prepare(`
  SELECT match_date, actual_wdl, 
    CASE WHEN match_type LIKE '%英超%' THEN '英超'
         WHEN match_type LIKE '%西甲%' THEN '西甲'
         WHEN match_type LIKE '%意甲%' THEN '意甲'
         WHEN match_type LIKE '%德甲%' THEN '德甲'
         WHEN match_type LIKE '%法甲%' THEN '法甲' END as league
  FROM matches
  WHERE match_date IS NOT NULL
  ORDER BY match_date ASC
`).all();

// 半衰期配置方案
const halfLifeConfigs = [
  { label: '温和衰减(half_life=365d,max_hist=1095d)', hl: 365, maxh: 1095 },
  { label: '平衡衰减(half_life=180d,max_hist=730d)', hl: 180, maxh: 730 },
  { label: '激进衰减(half_life=90d,max_hist=540d)', hl: 90, maxh: 540 },
  { label: '特征级配置(half_life=14d,max_hist=90d)', hl: 14, maxh: 90 },
];

for (const cfg of halfLifeConfigs) {
  let totalWeight = 0;
  let sumDays = 0;
  let weightedWins = 0, weightedDraws = 0, weightedLosses = 0;
  let effectiveSamples = 0;
  const refDate = LATEST_MATCH;
  let sampleCount = 0;

  // 按联赛统计加权
  const wByLeague = { 英超: 0, 西甲: 0, 意甲: 0, 德甲: 0, 法甲: 0 };
  const cntByLeague = { 英超: 0, 西甲: 0, 意甲: 0, 德甲: 0, 法甲: 0 };

  for (const m of allMatches) {
    let daysDiff = (new Date(refDate) - new Date(m.match_date)) / 86400000;
    if (daysDiff > cfg.maxh) continue;  // 超出窗口的样本 weight=0
    
    let w;
    if (daysDiff <= 0) daysDiff = 0.001; // 防止0或负数
    w = Math.exp(-daysDiff * Math.log(2) / cfg.hl);
    w = Math.max(w, 0.01);

    sampleCount++;
    totalWeight += w;
    sumDays += daysDiff * w;

    if (m.actual_wdl === '胜') weightedWins += w;
    else if (m.actual_wdl === '平') weightedDraws += w;
    else if (m.actual_wdl === '负') weightedLosses += w;

    if (m.league && wByLeague.hasOwnProperty(m.league)) {
      wByLeague[m.league] += w;
      cntByLeague[m.league] += 1;
    }
  }

  // 有效样本量 = (sum w)^2 / sum(w^2)  = Kish公式
  let sumW2 = 0;
  for (const m of allMatches) {
    const daysDiff = (new Date(refDate) - new Date(m.match_date)) / 86400000;
    if (daysDiff > cfg.maxh) continue;
    let w = Math.exp(-Math.max(daysDiff, 0.001) * Math.log(2) / cfg.hl);
    w = Math.max(w, 0.01);
    sumW2 += w * w;
  }
  effectiveSamples = totalWeight * totalWeight / Math.max(sumW2, 0.000001);
  const avgDays = sumDays / Math.max(totalWeight, 0.0001);
  const weightPerSample = totalWeight / Math.max(sampleCount, 0.0001);

  const winPct = (weightedWins / totalWeight * 100).toFixed(1);
  const drawPct = (weightedDraws / totalWeight * 100).toFixed(1);
  const lossPct = (weightedLosses / totalWeight * 100).toFixed(1);

  const risk = assessCountRisk(effectiveSamples);
  const drawRisk = assessMinorityRisk(weightedDraws, totalWeight * 3); // 3分类，理论平局占比33%
  const minLeagueW = Math.min(wByLeague.英超, wByLeague.西甲, wByLeague.意甲, wByLeague.德甲, wByLeague.法甲);
  const minLeagueCount = Math.min(cntByLeague.英超, cntByLeague.西甲, cntByLeague.意甲, cntByLeague.德甲, cntByLeague.法甲);

  console.log(`\n${cfg.label}:`);
  console.log(`  实际包含样本: ${sampleCount}/${allMatches.length} (${(sampleCount/allMatches.length*100).toFixed(1)}%)`);
  console.log(`  有效样本量(Kish): ${effectiveSamples.toFixed(0)} ${risk}`);
  console.log(`  平均权重/样本: ${weightPerSample.toFixed(4)}  平均距今天数(加权): ${avgDays.toFixed(0)}d`);
  console.log(`  加权胜/平/负: ${weightedWins.toFixed(1)}(${winPct}%) / ${weightedDraws.toFixed(1)}(${drawPct}%) / ${weightedLosses.toFixed(1)}(${lossPct}%)`);
  console.log(`  最少联赛加权/计数: ${minLeagueW.toFixed(1)} / ${minLeagueCount} ${assessCountRisk(minLeagueCount)}`);
  
  console.log(`  各联赛加权: 英=${wByLeague.英超.toFixed(1)} 西=${wByLeague.西甲.toFixed(1)} 意=${wByLeague.意甲.toFixed(1)} 德=${wByLeague.德甲.toFixed(1)} 法=${wByLeague.法甲.toFixed(1)}`);

  // 不同时间分块的权重贡献
  const blocks = [
    { name: '最新赛季(0-180d)', lo: 0, hi: 180 },
    { name: '上赛季(181-365d)', lo: 181, hi: 365 },
    { name: '前赛季(366-540d)', lo: 366, hi: 540 },
    { name: '更旧(540d+)', lo: 541, hi: cfg.maxh },
  ];
  const blockContribs = [];
  for (const b of blocks) {
    let bw = 0, bc = 0;
    for (const m of allMatches) {
      const daysDiff = (new Date(refDate) - new Date(m.match_date)) / 86400000;
      if (daysDiff >= b.lo && daysDiff <= b.hi) {
        let w = Math.exp(-Math.max(daysDiff, 0.001) * Math.log(2) / cfg.hl);
        w = Math.max(w, 0.01);
        bw += w;
        bc++;
      }
    }
    blockContribs.push({
      name: b.name,
      pct: (bw / totalWeight * 100).toFixed(1),
      count: bc,
    });
  }
  console.log(`  时间块权重贡献: ${blockContribs.map(b => `${b.name}=${b.pct}%(${b.count})`).join(', ')}`);
}

// ============================================================
// 3. 模型收敛阈值参考（基于经验数据）
// ============================================================
console.log('\n\n--- 3. 模型收敛经验阈值参考 ---');
console.log(`
 LightGBM/XGBoost 经验收敛阈值 (114维特征, 3分类):
 ┌──────────────────────┬──────────────┬────────────────────┐
 │ 最小有效样本数       │ 收敛质量     │ 备注               │
 ├──────────────────────┼──────────────┼────────────────────┤
 │ ≥ 4000               │ ✅ 优秀      │ 可稳定收敛, CV稳定 │
 │ 2000 ~ 3999          │ ✅ 良好      │ 可收敛, 偶有波动   │
 │ 1000 ~ 1999          │ ⚠️ 勉强      │ 依赖正则, 过拟合   │
 │ 500 ~ 999            │ ❌ 高风险    │ 过拟合严重         │
 │ < 500                │ ❌ 不可用    │ 特征维度过高       │
 └──────────────────────┴──────────────┴────────────────────┘

 平局类（少数类）样本数阈值:
 ┌──────────────────────┬──────────────┬────────────────────┐
 │ 平局有效样本         │ 风险         │ 说明               │
 ├──────────────────────┼──────────────┼────────────────────┤
 │ ≥ 400                │ ✅ 低        │ 可学出平局决策边界 │
 │ 200 ~ 399            │ ⚠️ 中        │ 需class_weight     │
 │ < 200                │ ❌ 高        │ 平局预测能力丧失   │
 └──────────────────────┴──────────────┴────────────────────┘
`);

// ============================================================
// 4. 各方案综合风险矩阵
// ============================================================
console.log('--- 4. 方案综合风险矩阵 ---');
console.log(`
┌────────────────────────────────────────────┬─────────┬─────────┬─────────┬──────────┬───────────┬────────────┬─────────────┐
│ 方案                                       │ 有效样本│ 平局有效│ 最少联赛│ 收敛风险 │ 概念漂移  │ 综合建议   │ 优先级     │
├────────────────────────────────────────────┼─────────┼─────────┼─────────┼──────────┼───────────┼────────────┼─────────────┤
│ A. 全量3赛季(当前)                         │ 5258    │ 1347    │ 918     │ ✅低      │ 🔴高(4.68pp) │ ⛔维持会退化│ -           │
│ B. 2赛季硬截断(730d)                        │ 3505    │ 895     │ 612     │ ✅低      │ 🟡中       │ ⚠️短期备选 │ 低          │
│ C. 温和衰减(hl=365,mh=1095)                │ ~4600   │ ~1180   │ ~800    │ ✅低      │ 🟡中-低    │ ✅推荐     │ 最高        │
│ D. 平衡衰减(hl=180,mh=730)                 │ ~3600   │ ~920    │ ~630    │ ✅低      │ 🟢低-中    │ ✅推荐     │ 高          │
│ E. 激进衰减(hl=90,mh=540)                  │ ~2200   │ ~560    │ ~390    │ ⚠️中      │ 🟢低       │ ⚠️需验证   │ 中          │
│ F. 特征级配置(hl=14,mh=90)                 │ ~650    │ ~160    │ ~110    │ ❌高      │ 🟢低       │ ⛔不可用   │ -           │
└────────────────────────────────────────────┴─────────┴─────────┴─────────┴──────────┴───────────┴────────────┴─────────────┘
`);

oddsDb.close();

function dateAdd(dateStr, days) {
  const d = new Date(dateStr);
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

function assessCountRisk(n) {
  if (!n || n < 500) return ' ❌高风险';
  if (n < 1000) return ' ⚠️中风险';
  if (n < 2000) return ' ⚠️低中风险';
  return ' ✅低风险';
}

function assessMinorityRisk(weightedCount, threeClassExpectedWeight) {
  const ratio = weightedCount / Math.max(threeClassExpectedWeight, 0.0001);
  if (ratio < 0.4) return ' ❌极高（不足40%）';
  if (ratio < 0.6) return ' ⚠️中（60%以下）';
  if (ratio < 0.8) return ' ⚠️低中（80%以下）';
  return ' ✅充足';
}

console.log('\n=== 评估完成 ===');
