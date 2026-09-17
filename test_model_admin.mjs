// 验证 model-admin API 在无 token 情况下可用性
import http from 'http';
const BASE = { host: 'localhost', port: 3000 };
function request(method, path) {
  return new Promise((resolve, reject) => {
    const req = http.request({ ...BASE, method, path, headers: { 'Content-Type': 'application/json' } }, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch (e) { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

console.log('=== 验证 model-admin API 无 token 可用性 ===\n');

const endpoints = [
  ['GET', '/api/model/info'],
  ['GET', '/api/model/reload/status'],
  ['GET', '/api/model/reload/history?limit=5']
];

for (const [method, path] of endpoints) {
  const r = await request(method, path);
  const ok = r.status === 200 && r.body.success;
  console.log(`${method} ${path}`);
  console.log(`  状态: ${r.status} | success: ${r.body.success}`, ok ? '✅' : '❌');
  if (ok && path === '/api/model/info') {
    console.log(`  模型配置: ${r.body.data.predictionService.modelConfigured} | 球队数: ${r.body.data.predictionService.teamsCount} | Elo: ${r.body.data.predictionService.eloRatingsCount}`);
  }
  if (ok && path === '/api/model/reload/status') {
    console.log(`  统计: ${JSON.stringify(r.body.data).substring(0, 100)}`);
  }
  console.log('');
}

console.log('=== 验证完成 ===');
