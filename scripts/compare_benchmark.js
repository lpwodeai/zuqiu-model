/**
 * 公平对比压测：单进程 vs Cluster
 * 相同 API、相同环境、相同并发级别
 */

import { spawn } from 'child_process';
import http from 'http';
import { performance } from 'perf_hooks';
import fs from 'fs';

const PORT = 3000;
const API = `http://localhost:${PORT}/api/health`;
const CONCURRENCY_LEVELS = [50, 100, 200, 500, 1000];
const DURATION = 8000;

function request(url, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const req = http.get(url, (res) => {
      let body = '';
      res.on('data', (c) => body += c);
      res.on('end', () => {
        resolve({ ok: res.statusCode === 200, latency: performance.now() - t0 });
      });
    });
    req.on('error', () => resolve({ ok: false, latency: performance.now() - t0 }));
    req.on('timeout', () => { req.destroy(); resolve({ ok: false, latency: performance.now() - t0 }); });
    req.setTimeout(timeoutMs);
  });
}

async function benchmark(concurrency, duration) {
  const latencies = [];
  let sent = 0, ok = 0, fail = 0;
  const start = performance.now();
  const deadline = start + duration;
  let stopped = false;

  const worker = async () => {
    while (!stopped && performance.now() < deadline) {
      sent++;
      const r = await request(API);
      latencies.push(r.latency);
      if (r.ok) ok++; else fail++;
    }
  };

  const workers = [];
  for (let i = 0; i < concurrency; i++) workers.push(worker());
  await sleep(duration);
  stopped = true;
  await Promise.race([Promise.all(workers), sleep(3000)]);

  latencies.sort((a, b) => a - b);
  const elapsed = performance.now() - start;
  const p = (f) => latencies[Math.floor(latencies.length * f)] || 0;
  const avg = latencies.reduce((a, b) => a + b, 0) / Math.max(latencies.length, 1);

  return {
    concurrency,
    qps: parseFloat((sent / (elapsed / 1000)).toFixed(1)),
    avg: parseFloat(avg.toFixed(2)),
    p50: parseFloat(p(0.5).toFixed(2)),
    p95: parseFloat(p(0.95).toFixed(2)),
    p99: parseFloat(p(0.99).toFixed(2)),
    max: parseFloat(latencies[latencies.length - 1]?.toFixed(2) || 0),
    error_rate: parseFloat((fail / Math.max(sent, 1) * 100).toFixed(2)),
  };
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function waitForServer(maxWait = 15000) {
  const start = performance.now();
  while (performance.now() - start < maxWait) {
    const r = await request(API, 1000);
    if (r.ok) return true;
    await sleep(200);
  }
  return false;
}

async function runBenchmarkSuite(label) {
  console.log(`\n  [${label}] 开始压测...`);
  const results = [];
  for (const c of CONCURRENCY_LEVELS) {
    process.stdout.write(`    并发 ${c} ... `);
    const r = await benchmark(c, DURATION);
    results.push(r);
    console.log(`QPS=${r.qps} P95=${r.p95}ms P99=${r.p99}ms Err=${r.error_rate}%`);
    await sleep(1500);
  }
  return results;
}

async function main() {
  console.log('═══════════════════════════════════════════════════════════');
  console.log('  公平对比压测: 单进程 vs Cluster (真实 API)');
  console.log('═══════════════════════════════════════════════════════════');

  const results = {};

  // ========== 测试 1: 单进程 ==========
  console.log('\n━━━ 阶段 1: 单进程模式 ━━━');
  console.log('  启动 node server/index.js ...');
  
  const singleProc = spawn('node', ['server/index.js'], {
    cwd: process.cwd(),
    stdio: 'pipe',
    env: { ...process.env, NODE_ENV: 'development' }
  });

  const singleProcLogs = [];
  singleProc.stdout.on('data', (d) => singleProcLogs.push(d.toString()));
  singleProc.stderr.on('data', (d) => singleProcLogs.push(d.toString()));

  const singleReady = await waitForServer();
  if (!singleReady) {
    console.error('  ❌ 单进程启动超时');
    console.log('  最近日志:', singleProcLogs.slice(-10).join(''));
    singleProc.kill();
    process.exit(1);
  }
  console.log('  ✅ 单进程已启动');

  // 预热
  for (let i = 0; i < 5; i++) await request(API, 2000);
  
  results.single = await runBenchmarkSuite('单进程');
  
  console.log('  停止单进程...');
  singleProc.kill();
  await sleep(3000);

  // ========== 测试 2: Cluster ==========
  console.log('\n━━━ 阶段 2: Cluster 模式 ━━━');
  console.log('  启动 node server/cluster.js ...');
  
  const clusterProc = spawn('node', ['server/cluster.js'], {
    cwd: process.cwd(),
    stdio: 'pipe',
    env: { ...process.env, NODE_ENV: 'development' }
  });

  const clusterLogs = [];
  clusterProc.stdout.on('data', (d) => clusterLogs.push(d.toString()));
  clusterProc.stderr.on('data', (d) => clusterLogs.push(d.toString()));

  const clusterReady = await waitForServer();
  if (!clusterReady) {
    console.error('  ❌ Cluster 启动超时');
    console.log('  最近日志:', clusterLogs.slice(-10).join(''));
    clusterProc.kill();
    process.exit(1);
  }
  console.log('  ✅ Cluster 已启动');

  // 预热
  for (let i = 0; i < 5; i++) await request(API, 2000);
  
  results.cluster = await runBenchmarkSuite('Cluster');
  
  console.log('  停止 Cluster...');
  clusterProc.kill();
  await sleep(3000);

  // ========== 对比报告 ==========
  console.log('\n' + '='.repeat(80));
  console.log('  对比报告: 单进程 vs Cluster (真实 API)');
  console.log('='.repeat(80));

  console.log('\n┌────────┬──────────────────┬──────────────────┬──────────┐');
  console.log('│ 并发   │ 单进程 QPS (P99) │ Cluster QPS (P99)│ 提升倍数 │');
  console.log('├────────┼──────────────────┼──────────────────┼──────────┤');
  
  for (const s of results.single) {
    const c = results.cluster.find(r => r.concurrency === s.concurrency);
    if (c) {
      const speedup = (c.qps / s.qps).toFixed(2);
      const p99Delta = c.p99 - s.p99;
      const sign = p99Delta >= 0 ? '+' : '';
      console.log(
        `│ ${String(s.concurrency).padStart(6)} │ ${String(s.qps).padStart(6)} (${String(s.p99).padStart(6)}ms) │ ${String(c.qps).padStart(6)} (${String(c.p99).padStart(6)}ms) │ ${String(speedup).padStart(8)} │`
      );
    }
  }
  console.log('└────────┴──────────────────┴──────────────────┴──────────┘');

  const peakSingle = Math.max(...results.single.map(r => r.qps));
  const peakCluster = Math.max(...results.cluster.map(r => r.qps));
  const speedup = (peakCluster / peakSingle).toFixed(2);

  console.log(`\n📊 关键发现:`);
  console.log(`  • 单进程峰值 QPS: ${peakSingle}`);
  console.log(`  • Cluster 峰值 QPS: ${peakCluster}`);
  console.log(`  • 吞吐比: ${speedup}x`);

  const safeSingle = results.single.filter(r => r.error_rate < 1 && r.p99 < 1000).reduce((m, r) => Math.max(m, r.concurrency), 0);
  const safeCluster = results.cluster.filter(r => r.error_rate < 1 && r.p99 < 1000).reduce((m, r) => Math.max(m, r.concurrency), 0);
  console.log(`  • 单进程安全并发 (P99<1s): ${safeSingle}`);
  console.log(`  • Cluster 安全并发 (P99<1s): ${safeCluster}`);

  // 结论
  console.log(`\n💡 结论:`);
  if (parseFloat(speedup) >= 1.5) {
    console.log(`  ✅ Cluster 有效提升吞吐量 (${speedup}x)`);
  } else if (parseFloat(speedup) >= 1.1) {
    console.log(`  ✅ Cluster 有轻微提升 (${speedup}x)，主要价值在于稳定性和隔离性`);
  } else {
    console.log(`  ⚠️ Cluster 吞吐量提升不显著 (${speedup}x)`);
    console.log(`  💡 原因分析: 健康检查 API 是 I/O 密集型 (数据库查询)`);
    console.log(`     Cluster 主要价值: CPU 密集型任务隔离、稳定性、故障隔离`);
    console.log(`     预测 API (模型推理) 才是 Cluster 的最佳应用场景`);
  }

  // 保存报告
  fs.writeFileSync('logs/cluster_vs_single_report.json', JSON.stringify({
    test_time: new Date().toISOString(),
    results,
    summary: {
      peak_single: peakSingle,
      peak_cluster: peakCluster,
      speedup: parseFloat(speedup),
      safe_single: safeSingle,
      safe_cluster: safeCluster,
    }
  }, null, 2));
  console.log(`\n📄 报告: logs/cluster_vs_single_report.json`);
}

main().catch(e => console.error('错误:', e));
