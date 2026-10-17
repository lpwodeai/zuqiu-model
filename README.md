# 五大联赛足球预测模型

基于 Poisson 分布、XGBoost/LightGBM 集成与贝叶斯先验的全栈足球比赛预测系统，覆盖英超、西甲、意甲、德甲、法甲。

> **权威文档**：本文件仅作门面导航。架构、部署、API、测试、风险的**单一权威来源**为 [`docs/五大联赛全栈框架设计.md`](docs/五大联赛全栈框架设计.md)（1000+ 行），变更流水为 [`docs/change_log.md`](docs/change_log.md)（C-/D- 编号）。本文件不复制细节，避免双文档漂移。

---

## 快速开始

### 1. 安装依赖

**Node.js 侧**（在线推理服务）：
```bash
npm install
```

**Python 侧**（数据采集、特征工程、模型训练、回测）：
```bash
pip install -r requirements.txt
```

### 2. 开发模式

同时启动前后端：
```bash
npm run dev
```

或分开启动：
```bash
npm run server    # 后端 API + WebSocket
npm run client    # 前端 Vite 开发服务器（默认 5173 端口）
```

### 3. 生产部署

**本机（PM2 托管，推荐）**：
```bash
pm2 start ecosystem.config.cjs          # 开发环境（2 worker）
pm2 start ecosystem.config.cjs --env production  # 生产环境
pm2 logs five-leagues                   # 查看日志
pm2 monit                               # 实时监控
```

- 开机自启已配置（Windows 任务计划 `FiveLeagues_PM2_Startup`，登录后约 15s 自动恢复）
- 改过 ecosystem 启动参数后须重跑 `pm2 save`，否则开机按旧快照恢复

**Linux 服务器**：
```bash
bash deploy.sh    # 六步部署（含依赖安装、前端构建、PM2 启动）
```

**Docker**（`docker/`）：
```bash
cd docker && docker-compose up -d
```
> ⚠️ Docker 链路尚未与 PM2/cluster 对齐验证，生产部署以 PM2 为准路。

### 4. 健康检查

```bash
curl http://localhost:3000/api/health
```

---

## 双运行时架构

| | Python 离线侧 | Node.js 在线侧 |
|---|---|---|
| **职责** | 数据采集、特征工程、模型训练、回测、赛前报告 | 在线推理、Web 仪表盘、赔率推送、模型热更新 |
| **入口** | `scripts/train_models.py`、`collection/final_500_collector.py` 等 | `server/cluster.js` → `server/index.js`（PM2 托管） |
| **交接** | 训练导出 `assets/*_export.js` | 服务加载 export.js 做在线推理 |
| **存储** | SQLite（`five_leagues.db` / `odds.db` 等 4 库，WAL 模式） | 同一 SQLite（better-sqlite3 驱动） |

---

## 常用命令

```bash
# 前端构建
npm run build

# 数据采集
npm run data:collect
python collection/final_500_collector.py --cookies data/cookies_500.json

# 模型训练
python scripts/train_models.py

# 测试（Python 298 用例）
python -m pytest tests/ -v

# Node 测试（注意：npm test 当前是空壳，详见框架文档 §9.18）
```

---

## API 速览

服务挂载 12 组路由（`/api/predict`、`/api/teams`、`/api/odds`、`/api/auth`、`/api/data`、`/api/leagues`、`/api/players`、`/api/review`、`/api/model`、`/api/abtest`、`/api/db`、`/api/logs`），完整端点清单与认证要求见 [框架文档 §5](docs/五大联赛全栈框架设计.md)。

---

## 技术栈

- **后端**：Node.js + Express + WebSocket (ws)、better-sqlite3、PM2 cluster
- **前端**：React 18 + Ant Design 5 + ECharts + Vite
- **Python 侧**：XGBoost / LightGBM / Optuna / scikit-learn / pandas
- **存储**：SQLite WAL（主库 `five_leagues.db` 7.7MB + `odds.db` 1.58GB）；Redis 可选（未部署时内存降级）
- **部署**：PM2（生产正路）、Docker Compose（未对齐验证）、Nginx

---

## 版本历史

- v7.5.5: 服务端资源配置修复（BOM 剥离、球队名映射补全、版本号对齐）
- v7.5.4: XGBoost 过拟合诊断与超参数调优（max_depth 9→4 等）
- v7.5.3: cross_league_validation 数据泄露修复 + 时序切分重训（5256 场 / 210 维）
- v7.5.2: scripts 命名规范修正（kebab→snake case）
- v7.5.1: scripts 目录清理（删除临时脚本、空库）
- v7.5.0: 全栈应用重构，前后端分离部署
- v7.4.0: 集成赔率数据，价值投注识别
- v7.3.0: 球员影响分析
- v7.2.0: Poisson 分布模型优化
- v7.1.0: 基础预测功能

> 2026-09 后的变更以 `docs/change_log.md` C-/D- 编号为准，不再在此追加。

---

## 许可证

MIT License
