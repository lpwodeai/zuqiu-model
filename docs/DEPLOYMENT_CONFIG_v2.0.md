# 五大联赛足球预测模型 - 最终部署配置清单 v2.0

> **生成日期**: 2026-08-12
> **模型版本**: T-005 v3 + T-006 v4（方案A+方案D+联赛特定ρ+全批量向量化）
> **应用版本**: five-leagues-predictor v7.5.0 / PM2 配置 v8.0
> **关联文档**: DEPLOYMENT_GUIDE.md, optimization_log.md §4.34, change_log.md

---

## 一、系统环境要求

| 项目 | 最低版本 | 推荐版本 | 说明 |
|------|----------|----------|------|
| 操作系统 | Windows Server 2019 / Ubuntu 20.04 | Windows 11 / Ubuntu 22.04 LTS | 当前生产环境为 Windows |
| Node.js | 18.0 LTS | 20.x LTS | API 服务运行时 |
| Python | 3.11 | 3.14 | 模型训练/推理（当前 .pyc 为 cpython-314） |
| npm | 9.0 | 10.x | 包管理 |
| PM2 | 5.0 | 5.3+ | 进程管理 |
| Redis（可选） | 6.0 | 7.x | 缓存层 |
| Nginx（可选） | 1.18 | 1.24+ | 反向代理 |

### 硬件要求

| 资源 | 最低配置 | 推荐配置 |
|------|----------|----------|
| CPU | 2 核 | 4 核+ |
| 内存 | 2 GB | 4 GB+ |
| 磁盘 | 20 GB | 50 GB+ SSD |
| 网络 | 10 Mbps | 100 Mbps+ |

---

## 二、Node.js 依赖包版本（package.json）

### 2.1 生产依赖 (dependencies)

| 包名 | 版本约束 | 用途 |
|------|----------|------|
| axios | ^1.18.1 | HTTP 客户端（外部 API 调用） |
| bcryptjs | ^2.4.3 | 密码哈希 |
| better-sqlite3 | ^13.0.3 | SQLite 同步驱动（WAL 模式） |
| bull | ^4.12.0 | 任务队列 |
| cheerio | ^1.2.0 | HTML 解析（数据采集） |
| cors | ^2.8.5 | 跨域中间件 |
| dotenv | ^16.3.1 | 环境变量加载 |
| express | ^4.18.2 | Web 框架 |
| js-yaml | ^4.3.0 | YAML 配置解析 |
| jsdom | ^29.1.1 | DOM 模拟 |
| jsonwebtoken | ^9.0.2 | JWT 认证 |
| moment | ^2.29.4 | 日期处理 |
| mongodb | ^6.3.0 | MongoDB 驱动（可选） |
| node-fetch | ^3.3.2 | Fetch API polyfill |
| node-schedule | ^2.1.1 | 定时任务调度 |
| qs | ^6.15.3 | 查询字符串解析 |
| redis | ^4.6.10 | Redis 客户端（可选缓存） |
| sql.js | ^1.14.1 | 浏览器端 SQLite |
| sqlite3 | ^6.0.1 | SQLite 异步驱动 |
| uuid | ^9.0.0 | UUID 生成 |
| ws | ^8.14.2 | WebSocket 服务 |

### 2.2 开发依赖 (devDependencies)

| 包名 | 版本约束 | 用途 |
|------|----------|------|
| @vitejs/plugin-react | ^4.3.1 | Vite React 插件 |
| antd | ^5.12.0 | UI 组件库 |
| concurrently | ^8.2.2 | 并发执行 |
| echarts | ^5.4.3 | 图表库 |
| echarts-for-react | ^3.0.2 | React ECharts 封装 |
| nodemon | ^3.0.2 | 开发热重载 |
| react | ^18.2.0 | UI 框架 |
| react-dom | ^18.2.0 | React DOM |
| react-router-dom | ^6.20.0 | 路由 |
| terser | ^5.48.0 | 代码压缩 |
| vite | ^5.0.0 | 构建工具 |

### 2.3 可选依赖 (optionalDependencies)

| 包名 | 版本约束 | 用途 |
|------|----------|------|
| @esbuild/win32-x64 | ^0.28.1 | esbuild Windows 二进制 |
| @rollup/rollup-win32-x64-msvc | ^4.62.2 | rollup Windows 二进制 |

---

## 三、Python 依赖包版本（推荐）

> 项目无 requirements.txt，以下版本基于代码 import 分析与生产环境验证。建议执行 `pip install -r` 时锁定如下版本。

### 3.1 核心科学计算

| 包名 | 推荐版本 | 用途 | 使用位置 |
|------|----------|------|----------|
| numpy | >=1.26,<2.0 | 数组计算 | 全局（向量化优化核心） |
| pandas | >=2.1,<3.0 | 数据处理 | 数据加载/特征工程 |
| scipy | >=1.11,<2.0 | Poisson 分布 | t006_score_predictor_v4.py |

### 3.2 机器学习框架

| 包名 | 推荐版本 | 用途 | 使用位置 |
|------|----------|------|----------|
| scikit-learn | >=1.4,<1.6 | CV/指标/校准 | advanced_model_trainer.py, d016/d017 |
| lightgbm | >=4.1,<5.0 | LGB 分类器 | **T-005 v3 生产模型** |
| xgboost | >=2.0,<3.0 | XGB 分类器 | 训练对比 |
| optuna | >=3.5,<4.0 | 超参调优 | optuna_tuning.py (D-014) |
| joblib | >=1.3,<2.0 | 模型持久化 | 模型加载/保存 |

### 3.3 可视化（可选，仅 SHAP 分析）

| 包名 | 推荐版本 | 用途 |
|------|----------|------|
| matplotlib | >=3.8,<4.0 | 特征重要性图 |
| shap | >=0.44,<1.0 | SHAP 值分析 |

### 3.4 版本兼容性注意事项

- scikit-learn 新版本已弃用 `multi_class` 参数，`LogisticRegression` 默认使用 multinomial
- XGBoost/LightGBM 的 `fit()` 参数需用 `inspect` 签名动态适配（见 advanced_model_trainer.py）
- numpy 2.0 与部分旧版 scipy/lightgbm 存在 ABI 不兼容，建议锁定 <2.0

---

## 四、PM2 运行参数（ecosystem.config.cjs）

### 4.1 进程配置

| 参数 | 值 | 说明 |
|------|-----|------|
| name | `five-leagues` | 进程名 |
| script | `server/index.js` | 入口 |
| exec_mode | `fork` | **单进程模式**（SQLite WAL 兼容） |
| instances | `1` | 单实例 |
| interpreter | `node` | Node.js 解释器 |
| interpreter_args | `--experimental-vm-modules` | 启用 VM 模块 |

### 4.2 进程管理

| 参数 | 值 | 说明 |
|------|-----|------|
| autorestart | `true` | 自动重启 |
| watch | `false` | 关闭文件监听 |
| max_memory_restart | `1G` | 内存超限重启 |
| min_uptime | `10s` | 最小运行时间 |
| max_restarts | `10` | 最大重启次数 |
| restart_delay | `4000` ms | 重启延迟 |
| kill_timeout | `5000` ms | 优雅关闭超时 |
| listen_timeout | `10000` ms | 监听超时 |

### 4.3 环境变量

| 变量 | 开发环境 | 生产环境 | 说明 |
|------|----------|----------|------|
| NODE_ENV | development | production | 运行环境 |
| PORT | 3000 | 3000 | API 端口 |
| CLIENT_URL | http://localhost:5173 | https://your-domain.com | 前端地址 |
| JWT_SECRET | — | ${JWT_SECRET} | JWT 密钥 |
| REDIS_HOST | — | ${REDIS_HOST} | Redis 地址 |
| REDIS_PORT | — | ${REDIS_PORT} | Redis 端口 |
| FOOTBALL_DATA_API_KEY | — | ${FOOTBALL_DATA_API_KEY} | 足球数据 API |
| ODDS_API_KEY | — | ${ODDS_API_KEY} | 赔率 API |

### 4.4 日志配置

| 参数 | 值 |
|------|-----|
| error_file | `logs/error.log` |
| out_file | `logs/out.log` |
| log_file | `logs/combined.log` |
| log_date_format | `YYYY-MM-DD HH:mm:ss Z` |
| merge_logs | `true` |

---

## 五、模型运行参数

### 5.1 T-005 v3（让球胜平负，生产模型）

| 参数 | 值 | 来源 |
|------|-----|------|
| 模型类型 | LightGBM | D-015 结论 |
| 特征维度 | 114 维 | 配置 |
| 温度 T | 0.800 | latest_model.json |
| 统一阈值 | 0.500 | latest_model.json |
| class_weight 比例 | 1.20 | latest_model.json |
| 模型资产 | t005v3_direction_predictor.pkl, t005v3_draw_detector.pkl | assets/ |
| 规则引擎 | **已禁用** (apply_rule_adjustments) | 生产默认 |
| 辅助信号 | generate_rule_warnings（只读提示） | 不修改预测 |

### 5.2 T-006 v4（比分预测，最终优化版）

| 参数 | 值 | 来源 |
|------|-----|------|
| 模型版本 | v4 + 方案A + 方案D + 联赛特定ρ + 全批量向量化 | optimization_log §4.34 |
| 核心方法 | Poisson PMF + Dixon-Coles + Monte Carlo 融合 | t006_score_predictor_v4.py |
| MC 模拟次数 | 3000 | §4.27 优化 |
| 高比分优化 | 方案A（score_implied_total 后处理） | §4.31 |
| 联赛特定ρ | P2-5 校准 | §4.29.2 |
| 批量向量化 | 全阶段启用 | §4.33 |

### 5.3 训练配置（config.yaml 关键参数）

| 参数 | 值 |
|------|-----|
| 时序衰减 | 启用，指数衰减，半衰期 14 天 |
| 增量训练 | 启用，batch_size=100 |
| class_weight | 启用 |
| validation_split | 0.2 |
| 自动训练 | 每日 02:00 |
| 最小比赛数 | 100 |
| 训练超时 | 300s |
| 重试次数 | 3 |
| Optuna | 启用，30 trials，3600s 超时 |

### 5.4 LGB 超参数（config.yaml）

| 参数 | 值 |
|------|-----|
| max_depth | 3 |
| learning_rate | 0.010611228531870624 |
| num_leaves | 202 |
| subsample | 0.9977564395215165 |
| colsample_bytree | 0.7867816140734276 |
| reg_alpha | 0.15266573596777044 |
| reg_lambda | 1.4618378148431221 |
| min_child_weight | 6 |
| min_data_in_leaf | 23 |
| feature_fraction | 0.9288484948848426 |
| bagging_fraction | 0.6075695015110804 |
| bagging_freq | 8 |
| num_boost_round | 164 |
| early_stopping_rounds | 15 |

---

## 六、自动重训练触发配置

| 配置项 | 值 |
|--------|-----|
| 调度任务名 | `T005v3_AutoRetrain`（Windows schtasks） |
| 执行时间 | 每日 08:00 |
| 执行引擎 | `scripts/retrain_trigger_runner.py` |
| 重训练脚本 | `scripts/deploy_t005v3_final.py` |
| 触发器状态 | `deployment/trigger_state.json` |
| 数据触发检查表 | `matches`, `handicap_history`, `wdl_history` |
| 强制重训练间隔 | 7 天 |
| 性能门禁 | 走水召回率 ≥ 0.30，预测率偏差 ≤ 2pp |

---

## 七、性能基线指标（v2.0 最终）

### 7.1 T-006 v4 比分预测

| 指标 | 值 | 达标 |
|------|-----|------|
| 总比赛数 | 1415 | — |
| 精确比分命中率 | 64.66% | — |
| **1 球内命中率** | **88.20%** | ✅ ≥35% |
| 2 球内命中率 | 95.97% | — |
| 批次标准差 | 3.25% | ✅ σ≤5% |
| 平均排名 | #7.8 / 64 | — |
| Top-8 命中率 | 64.76% | — |

### 7.2 T-006 v4 运行效率（全批量向量化后）

| 指标 | 值 |
|------|-----|
| 逐场评估耗时 | 0.022 ms/场 |
| Top-N 评估占比 | 90.19% |
| 一次性预计算 | 7.041s |
| 批量回测总耗时 | 7.072s（含 ρ 校准 11.428s） |

### 7.3 T-005 v3 性能门禁基线（CI 检查）

| 指标 | 基线 | 当前 |
|------|------|------|
| LGB CV 准确率 | ≥50% | 51.24% |
| XGB CV 准确率 | ≥49% | 51.14% |
| D-017 CV 准确率 | ≥49% | 50.95% |
| 特征维度 | ≥55 | 60 |
| LogLoss | ≤1.10 | 达标 |

---

## 八、关键文件清单

### 8.1 模型资产

| 文件 | 路径 | 用途 |
|------|------|------|
| T-005 v3 方向预测器 | `assets/t005v3_direction_predictor.pkl` | 让球方向 |
| T-005 v3 平局检测器 | `assets/t005v3_draw_detector.pkl` | 平局检测 |
| T-005 v3 元数据 | `assets/t005v3_metadata.json` | 模型配置 |
| T-005 v3 Elo 评级 | `assets/t005v3_elo_ratings.json` | 实力排名 |
| T-006 低进球分类器 | `models/t006_lowgoal_classifier_v1.pkl` | 0-1 球分类 |

### 8.2 核心脚本

| 脚本 | 用途 |
|------|------|
| `scripts/train_models_v2.py` | T-005 训练入口 |
| `scripts/t006_score_predictor_v4.py` | T-006 v4 比分预测 |
| `scripts/deploy_t005v3_final.py` | T-005 v3 部署/重训练 |
| `scripts/retrain_trigger_runner.py` | 重训练触发引擎 |
| `scripts/ci_check.py` | CI 三重检查 |
| `server/index.js` | API 服务入口 |

### 8.3 数据库

| 数据库 | 路径 | 说明 |
|--------|------|------|
| odds.db | 项目根 | 5 联赛 1265 场比赛 + 赔率历史 |
| odds_timing.db | 项目根 | 时序赔率（1275 场 6586 条） |

---

## 九、部署验证步骤

```bash
# 1. 安装 Node.js 依赖
npm install

# 2. 安装 Python 依赖
pip install numpy pandas scipy scikit-learn lightgbm xgboost optuna joblib

# 3. 启动服务
pm2 start ecosystem.config.cjs --env production

# 4. 健康检查
curl http://localhost:3000/api/health

# 5. 模型重载状态
curl http://localhost:3000/api/model/reload/status

# 6. CI 验证
python scripts/ci_check.py

# 7. 性能回归测试
python -m pytest tests/ -v
```

---

## 十、变更记录

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v2.0 | 2026-08-12 | 初版发布。基于 T-006 v4 全批量向量化优化完成（§4.34）与 T-005 v3 生产模型最终状态生成 |
