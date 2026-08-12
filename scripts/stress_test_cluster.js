/**
 * Cluster 模式高并发压测 v2.0
 * 对真实运行的 Cluster 服务（8 Workers）进行压力测试
 *
 * 对比基线：单进程模式峰值 4144 QPS @ 并发 200
 * 目标：验证 Cluster 模式是否能突破单进程瓶颈
 */

import http from 'http';
import { performance } from 'perf_hooks';

const API_BASE = 'http://localhost:3000';
const CONCURRENCY_LEVELS = [50, 100, 200, 500, 1000, 2000, 3000];
const DURATION_PER_LEVEL_MS = 10000;
const RAMP_UP_MS = 500;

function request(url, timeoutMs = 10000) {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const req = http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => {
        resolve({
          ok: res.statusCode === 200,
          status: res.statusCode,
          latency: performance.now() - t0,
        });
      });
    });
    req.on('error', () => resolve({ ok: false, status: 0, latency: performance.now() - t0 }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, status: 0, latency: performance.now() - t0, timeout: true }); });
    req.setTimeout(timeoutMs);
  });
}

async function runConcurrencyLevel(concurrency, durationMs) {
  const latencies = [];
  let totalSent = 0, totalOk = 0, totalFail = 0, totalTimeout = 0;
  const startTime = performance.now();
  const deadline = startTime + durationMs;
  let stopped = false;

  const sender = async () => {
    while (!stopped && performance.now() < deadline) {
      totalSent++;
      const r = await request(`${API_BASE}/api/health`);
      latencies.push(r.latency);
      if (r.ok) totalOk++;
      else if (r.timeout) totalTimeout++;
      else totalFail++;
    }
  };

  const workers = [];
  for (let i = 0; i < concurrency; i++) {
    workers.push(sender());
    if (i % 20 === 0 && i > 0) {
      await sleep(RAMP_UP_MS / Math.max(concurrency / 20, 1));
    }
  }

  await sleep(durationMs);
  stopped = true;

  await Promise.race([
    Promise.all(workers),
    sleep(5000),
  ]);

  const elapsed = performance.now() - startTime;
  latencies.sort((a, b) => a - b);

  const pct = (p) => latencies.length ? latencies[Math.floor(latencies.length * p)] : 0;
  const avg = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : 0;

  return {
    concurrency,
    elapsed_s: parseFloat((elapsed / 1000).toFixed(2)),
    total_sent: totalSent,
    total_ok: totalOk,
    total_fail: totalFail,
    total_timeout: totalTimeout,
    qps: parseFloat((totalSent / (elapsed / 1000)).toFixed(1)),
    avg_latency_ms: parseFloat(avg.toFixed(2)),
    p50_ms: parseFloat(pct(0.5).toFixed(2)),
    p90_ms: parseFloat(pct(0.9).toFixed(2)),
    p95_ms: parseFloat(pct(0.95).toFixed(2)),
    p99_ms: parseFloat(pct(0.99).toFixed(2)),
    max_ms: latencies.length ? parseFloat(latencies[latencies.length - 1].toFixed(2)) : 0,
    error_rate: parseFloat(((totalFail + totalTimeout) / Math.max(totalSent, 1) * 100).toFixed(2)),
  };
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function memMB() {
  const m = process.memoryUsage();
  return {
    rss: parseFloat((m.rss / 1048576).toFixed(1)),
    heapUsed: parseFloat((m.heapUsed / 1048576).toFixed(1)),
  };
}

async function main() {
  console.log('═══════════════════════════════════════════════════════════');
  console.log('  Cluster 模式高并发压测 v2.0');
  console.log('  目标: 真实 API 服务 (8 Workers)');
  console.log('  对比基线: 单进程 4144 QPS @ 并发 200');
  console.log('  测试时间: ' + new Date().toISOString());
  console.log('═══════════════════════════════════════════════════════════\n');

  // 先探测真实服务
  console.log('[0] 探测 API 服务...');
  const probe = await request(`${API_BASE}/api/health`, 3000);
  if (!probe.ok) {
    console.error('❌ API 服务不可用，请先启动服务');
    process.exit(1);
  }
  console.log(`  ✅ API 在线 (延迟 ${probe.latency.toFixed(1)}ms)\n`);

  // 系统基线
  const baseMem = memMB();
  console.log('[系统基线]');
  console.log(`  Node: ${process.version} | PID: ${process.pid}`);
  console.log(`  RSS: ${baseMem.rss} MB | Heap: ${baseMem.heapUsed} MB\n`);

  // 逐级并发压测
  console.log('[压测开始] 每级持续 ' + (DURATION_PER_LEVEL_MS / 1000) + 's:\n');
  const allResults = [];

  for (const c of CONCURRENCY_LEVELS) {
    process.stdout.write(`  ▶ 并发 ${c} ... `);
    const r = await runConcurrencyLevel(c, DURATION_PER_LEVEL_MS);
    allResults.push(r);
    console.log(`QPS=${r.qps} | P50=${r.p50_ms}ms | P95=${r.p95_ms}ms | P99=${r.p99_ms}ms | 错误率=${r.error_rate}%`);
    await sleep(2000);
  }

  // 汇总报告
  console.log('\n' + '='.repeat(70));
  console.log('  Cluster 模式压测报告 vs 单进程基线');
  console.log('='.repeat(70));

  const BASELINE_QPS = 4144;
  const BASELINE_SAFE = 500;

  console.log('\n┌────────┬────────┬─────────┬─────────┬─────────┬─────────┬─────────┬──────────┬──────────┐');
  console.log('│ 并发   │ QPS    │ P50(ms) │ P95(ms) │ P99(ms) │ Max(ms) │ 错误率% │ vs 基线  │ 达标    │');
  console.log('├────────┼────────┼─────────┼─────────┼─────────┼─────────┼─────────┼──────────┼──────────┤');
  for (const r of allResults) {
    const speedup = (r.qps / BASELINE_QPS).toFixed(2) + 'x';
    const safe = r.error_rate < 1 && r.p99_ms < 1000 ? '✅' : '⚠️';
    console.log(
      `│ ${String(r.concurrency).padStart(6)} │ ${String(r.qps).padStart(6)} │ ${String(r.p50_ms).padStart(7)} │ ${String(r.p95_ms).padStart(7)} │ ${String(r.p99_ms).padStart(7)} │ ${String(r.max_ms).padStart(7)} │ ${String(r.error_rate).padStart(7)} │ ${speedup.padStart(8)} │ ${safe.padStart(8)} │`
    );
  }
  console.log('└────────┴────────┴─────────┴─────────┴─────────┴─────────┴─────────┴──────────┴──────────┘');

  // 关键指标
  const maxQps = Math.max(...allResults.map(r => r.qps));
  const saturatedLevel = allResults.find(r => r.qps === maxQps);
  const clusterSpeedup = (maxQps / BASELINE_QPS).toFixed(2);
  const safeConcurrency = allResults.filter(r => r.error_rate < 1 && r.p99_ms < 1000)
    .reduce((max, r) => Math.max(max, r.concurrency), 0);

  console.log('\n📊 关键指标对比:');
  console.log(`  • 单进程峰值: ${BASELINE_QPS} QPS @ 并发 ${BASELINE_SAFE}`);
  console.log(`  • Cluster 峰值: ${maxQps} QPS @ 并发 ${saturatedLevel.concurrency}`);
  console.log(`  • 吞吐提升: ${clusterSpeedup}x`);
  console.log(`  • 安全并发水位: ${safeConcurrency} vs 单进程 ${BASELINE_SAFE}`);

  // 内存
  const finalMem = memMB();
  console.log(`\n💾 内存分析:`);
  console.log(`  • 测试端 RSS: ${baseMem.rss} → ${finalMem.rss} MB (Δ=+${(finalMem.rss - baseMem.rss).toFixed(1)} MB)`);

  // 建议
  console.log('\n⚙️ 生产建议:');
  console.log(`  • 建议限流: ${Math.floor(safeConcurrency * 0.7)} 并发 (安全水位 70%)`);
  console.log(`  • Worker 数量: 8 (当前)`);
  if (maxQps < BASELINE_QPS * 2) {
    console.log(`  • ⚠️ 提升未达预期，可能是 IPC 开销或 SQLite 瓶颈`);
  } else if (maxQps >= BASELINE_QPS * 3) {
    console.log(`  • ✅ 显著提升，Cluster 架构有效`);
  } else {
    console.log(`  • ✅ 有效提升，可考虑增加 Worker`);
  }

  // 保存报告
  const fs = await import('fs');
  const report = {
    test_time: new Date().toISOString(),
    mode: 'CLUSTER',
    workers: 8,
    baseline: { qps: BASELINE_QPS, concurrency: BASELINE_SAFE },
    node_version: process.version,
    concurrency_levels: allResults,
    summary: {
      peak_qps: maxQps,
      speedup: parseFloat(clusterSpeedup),
      saturation_concurrency: saturatedLevel.concurrency,
      safe_concurrency: safeConcurrency,
      recommended_rate_limit: Math.floor(safeConcurrency * 0.7),
    },
  };
  fs.writeFileSync('logs/stress_test_cluster_report.json', JSON.stringify(report, null, 2));
  console.log(`\n📄 报告已保存: logs/stress_test_cluster_report.json`);
}

main().catch(e => { console.error('测试异常:', e); process.exit(1); });
