# 五大联赛足球预测模型 v7.5

基于Poisson分布和机器学习的全栈足球比赛预测系统，覆盖英超、西甲、意甲、德甲、法甲五大联赛。

## 系统架构

```
football-predictor-v3.7/
├── server/                    # 后端服务
│   ├── index.js               # Express服务器入口
│   ├── routes/                # API路由
│   │   ├── predictions.js     # 预测API
│   │   ├── teams.js           # 球队API
│   │   ├── odds.js            # 赔率API
│   │   ├── auth.js            # 认证API
│   │   └── data.js            # 数据管理API
│   ├── services/              # 业务服务
│   │   ├── prediction-service.js  # 预测模型服务
│   │   └── data-service.js        # 数据服务
│   └── middleware/            # 中间件
│       └── auth.js            # JWT认证中间件
├── client/                    # 前端应用
│   ├── index.html             # HTML入口
│   └ src/                     # React源码
│   │   ├── main.jsx           # React入口
│   │   ├── App.jsx            # 主组件
│   │   ├── components/        # 通用组件
│   │   ├── pages/             # 页面组件
│   │   └ styles/              # 样式文件
├── assets/                    # 静态资源
│   └ model-engine.js          # 预测模型核心
├── scripts/                   # 工具脚本
│   ├── data-collector.js      # 数据采集管道
│   └ update-team-data.js      # 球队数据更新
├── data/                      # 数据存储
│   ├── odds/                  # 赔率数据
│   ├── teams/                 # 球队数据
│   ├── matches/               # 比赛数据
├── docker/                    # Docker配置
│   ├── server.Dockerfile      # 后端镜像
│   ├── client.Dockerfile      # 前端镜像
│   ├── nginx.conf             # Nginx配置
│   ├── docker-compose.yml     # 容器编排
├── package.json               # 项目配置
├── vite.config.js             # Vite配置
└ .env                         # 环境变量
└ README.md                    # 项目文档
```

## 快速开始

### 1. 安装依赖

```bash
npm install
```

### 2. 开发模式

启动后端服务器：
```bash
npm run server
```

启动前端开发服务器：
```bash
npm run client
```

同时启动前后端：
```bash
npm run dev
```

### 3. 生产部署

构建前端：
```bash
npm run build
```

启动生产服务器：
```bash
npm start
```

### 4. Docker部署

使用Docker Compose一键部署：
```bash
cd docker
docker-compose up -d
```

访问：
- 前端：http://localhost
- 后端API：http://localhost:3000
- 健康检查：http://localhost:3000/api/health

## API文档

### 预测API

**POST /api/predict**
- 请求体：`{ homeTeam, awayTeam, options }`
- 返回：预测结果（胜平负、比分、让球、总进球）

**POST /api/predict/with-odds**
- 请求体：`{ homeTeam, awayTeam, odds, options }`
- 返回：融合赔率的预测结果

### 球队API

**GET /api/teams**
- 返回：所有球队列表

**GET /api/teams/:key**
- 返回：指定球队详情

### 赔率API

**GET /api/odds/:matchId**
- 返回：指定比赛赔率数据

**POST /api/odds/:matchId**
- 请求体：赔率数据
- 功能：更新赔率数据

### 数据管理API

**GET /api/data/matches**
- 返回：比赛列表

**POST /api/data/import**
- 请求体：`{ type, data }`
- 功能：导入数据

## 数据采集

### 手动采集

```bash
npm run data:collect
```

### 定时采集

```bash
node scripts/data-collector.js --start
```

### 查看采集状态

```bash
node scripts/data-collector.js --status
```

## 核心功能

### 1. Poisson分布预测
- 计算Lambda值（进攻期望）
- 生成比分概率分布
- 预测胜平负概率

### 2. 赔率融合
- 获取实时赔率数据
- 计算赔率隐含概率
- 识别价值投注

### 3. 球员影响分析
- 量化球员贡献度
- 考虑联赛层级权重
- 调整Lambda值

### 4. 实时WebSocket通信
- 实时推送赔率更新
- 实时推送预测结果

### 5. 用户认证
- JWT Token认证
- 用户权限管理

## 技术栈

### 后端
- Node.js + Express.js
- WebSocket (ws)
- JWT认证
- 数据采集管道

### 前端
- React 18
- Ant Design 5
- ECharts可视化
- Vite构建工具

### 数据存储
- JSON文件存储
- Redis缓存（可选）

### 部署
- Docker容器化
- Nginx反向代理
- Docker Compose编排

## 配置说明

修改 `.env` 文件配置环境变量：

```env
NODE_ENV=production
PORT=3000
JWT_SECRET=your_secret_key
CLIENT_URL=http://localhost
```

## 测试

运行测试：
```bash
npm test
```

## 开发指南

### 添加新球队

```bash
node scripts/update-team-data.js --add
```

### 更新球队数据

```bash
node scripts/update-team-data.js --update brazil
```

### 验证数据完整性

```bash
node scripts/update-team-data.js --validate brazil
```

## 许可证

MIT License

## 版本历史

- v7.5.5: 服务端资源配置修复 + 重启验证（① 修复 `assets/league_tier.json` 首部 UTF-8 BOM（EF BB BF）导致的 JSON 加载失败，字节级剥离 BOM；② `assets/team_name_map.json` 补全「莱比锡」→ `rb leipzig` 短名映射，并修正「法兰克福」→ `eintracht frankfurt` 拼写；③ `server/index.js` 健康检查版本 `8.0.0` → `7.5.0` 对齐 package.json。`pm2 reload` 后启动日志已无 LEAGUE_TIER/莱比锡告警，`/api/health` 返回 healthy + version 7.5.0）
- v7.5.4: 过拟合诊断 + XGBoost 超参数防过拟合调优（分析 `logs/training_20260820_113459.json` 发现 XGBoost 训练/验证准确率差 9.28pp、LogLoss 差 0.075，属中等过拟合，根因为 `max_depth=9` 过深；调整 `config.yaml`：max_depth 9→4、learning_rate 0.0492→0.03、num_boost_round 181→300、early_stopping_rounds 15→20，保留原有强正则项。重训后过拟合差距收窄 1.33pp 至 7.95pp，验证准确率持平 48.48%，模型更稳健）
- v7.5.3: 修复 `cross_league_validation.py` 数据泄露并正式重训模型（将随机切分 `train_test_split` 改为按比赛日期升序的时序切分：前 80% 训练/后 20% 测试；新增详细日志输出切分点日期、训练/测试集样本数与日期区间、标签分布及时间泄露校验。主训练脚本 `train_models.py` 复用「`ORDER BY match_date` + 顺序切分 + `TimeSeriesSplit` 5 折滚动验证」的时序逻辑正式训练完成：5256 场（2023-08-12 ~ 2026-08-17）、210 维特征，80/20 切分 4204 训练/1052 验证，5 折滚动验证 XGBoost 0.5002±0.0217 / LightGBM 0.4929±0.0127，最终 XGBoost 验证准确率 0.4838（LogLoss 1.0211、Brier 0.6123）为最优，LightGBM 0.4829（LogLoss 1.0250、Brier 0.6150））
- v7.5.1: scripts 目录清理（删除 7 个 `_tmp_`/`_check_` 临时脚本、0 字节空库 `match_odds.db`；`api_performance_report.json` 移至 reports/；`sofascoe_client.py` 重命名为 `sofascore_client.py` 并同步 `fill_missing_odds.py` import）
- v7.5.2: scripts 命名规范修正（删除旧重复版本 `count_batch_matches.py`、`delete_worldcup_funcs.py`；`hyperparameter-opt.py` → `hyperparameter_opt.py`、`shap-analyzer.py` → `shap_analyzer.py`，同步更新 `data_source_migration_checklist.md` 引用；重命名后 `py_compile` 与模块导入均验证通过）
- v7.5.0: 全栈应用重构，支持前后端分离部署
- v7.4.0: 集成赔率数据，支持价值投注识别
- v7.3.0: 添加球员影响分析
- v7.2.0: Poisson分布模型优化
- v7.1.0: 基础预测功能