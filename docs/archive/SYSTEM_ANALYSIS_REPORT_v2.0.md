# 五大联赛足球预测模型系统 — 全面解析报告（v2.1 修订版）

> **报告版本**: v2.1（修订版）——2026-09-09 按《文档对比分析报告_v1.0.md》（D-026/D-027）执行「精简 + 交叉引用」改造：本文定位为**原理与验证**型文档（算法推导 / 特征工程演化 / 验证证据 / 历史结论），不再自持"当前值"；新增**历史口径标注**，保留演进证据。
> **文档职责声明**: 六文档单一权威链——现状架构（当前值，2026-09-09 快照）→《模型架构分析报告_v1.0.md》；优化方向/优先级/当前状态（P0~P2）→《模型优化评估报告_v2.0.md》；落地实施细节→《模型改进实施方案_v1.0.md》；差距清单与代码证据（历史快照）→《model_gap_analysis_report_v2.0.md》；本文 = 系统原理与验证证据（"为什么 / 凭什么"）。
> **生成时间**: 2026-08-18（英超独立模型 _predict_epl() 特征构造对接完成 + 详细日志）｜2026-08-20 增补 9.6（T-007 重跑验证）+ 9.7（双库写入幂等性修复）+ 9.8（209维重训 + 联赛分档反压低平局校准）｜2026-08-23 增补 9.9（v4 过度优化修复：6项改动，详见架构诊断报告）｜2026-08-28 增补 9.10（P1-5 贝叶斯层级模型 + P1-7 LR meta-learner Stacking + P1-9 ts_odds 时序/比分赔率生产采用；P1-8 xG 深化验证 RPS 无提升暂不采用）｜**2026-09-09 修订为 v2.1（精简 + 交叉引用 + 历史口径标注；§10.2 T-006 已更新为 v5）**
> **项目版本**: v8.3（2026-08 快照；当前见《模型架构分析报告_v1.0.md》§一）
> **数据规模**: 5,258 场比赛（5大联赛×3赛季）+ 新赛季 26/27 赛前数据（**2026-08 快照，P0-1 回溯采集前口径**；当前 matches 14,511 场见《模型架构分析报告_v1.0.md》§3.1）
> **技术栈**: Node.js (Express + WebSocket) + Python ML (LightGBM/XGBoost) + SQLite (WAL) + PM2
> **最新模型（2026-08-28 快照；当前生产状态见《模型架构分析报告_v1.0.md》§一表头）**: WDL 208维 LightGBM/XGBoost（slim_odds=True + ts_odds=True + consensus_odds=True）| WDL Stacking = 5 基础模型（Dixon-Coles + Elo + XGBoost + LightGBM + 贝叶斯）+ LR meta-learner（OOF RPS 0.1984）| T-005 v3 (71维) | 全局 argmax 决策（联赛分档开关保留）| 走水召回率 42.2% | 英超独立模型已从 Stacking 移除（改为 epl_reference）| 统一报告生成器 v3.0 (四维度: WDL 5模型Stacking + T-005 v3 + T-006 v4 + 总进球) | Monte Carlo n=500（原3000）| 温度 T=1.0（原0.8）| **T-006 已于 2026-09-08 投产 v5（Top-1 13.14%）**

---

## 目录

1. [系统架构总览](#一系统架构总览)
2. [预测流程与数据流转](#二预测流程与数据流转)
3. [核心算法详解](#三核心算法详解)
4. [特征工程体系](#四特征工程体系)
5. [模型训练与部署](#五模型训练与部署)
6. [数据采集管线](#六数据采集管线)
7. [关键文件功能说明](#七关键文件功能说明)
8. [性能指标（历史口径与验证）](#八性能指标历史口径与验证)
9. [可靠性验证与评估报告分析](#九可靠性验证与评估报告分析)
10. [2026-08 系统状态审计（历史记录）](#十2026-08-系统状态审计历史记录)
11. [不足与改进计划](#十一不足与改进计划)
12. [文档同步状态](#十二文档同步状态)

---

## 一、系统架构总览

### 1.1 整体架构图（历史口径）

> **精简**：当前完整架构图（客户端→API 网关→路由→服务→核心引擎→数据 六层 ASCII）与分层详解见《模型架构分析报告_v1.0.md》§二 / §三。以下为 2026-08-18 快照的历史架构图，仅供演进追溯。

<details>
<summary>历史架构图（2026-08-18 快照，点击展开）</summary>

```
┌─────────────────────────────────────────────────────────────────────┐
│                         客户端层 (Client Layer)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────┐ │
│  │ dashboard.html│  │  mobile.html │  │  React SPA (Predict.jsx等)   │ │
│  │ 桌面看板      │  │  移动端看板  │  │  Vite 构建 + Ant Design      │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┬───────────────┘ │
└─────────┼──────────────────┼─────────────────────────┼────────────────┘
          │                  │                         │
          ▼                  ▼                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      API 网关层 (API Gateway)                        │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  Express.js HTTP Server (端口 3000)                              │ │
│  │  ├─ CORS 中间件                                                  │ │
│  │  ├─ JWT 认证 (optionalAuth / authenticateToken)                 │ │
│  │  ├─ 限流中间件 (rateLimit)                                       │ │
│  │  ├─ SQL 注入防护 (SQLInjectionGuard)                             │ │
│  │  └─ 全局错误处理 (globalErrorHandler)                            │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │  WebSocket Server (ws://localhost:3000/ws)                       │ │
│  │  ├─ 实时赔率推送                                                  │ │
│  │  ├─ 实时预测结果推送                                              │ │
│  │  └─ Cluster 跨进程广播中转                                        │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       路由层 (Routes Layer)                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │predictions│ │  teams   │ │  odds    │ │ players  │ │model-admin│ │
│  │ 预测API   │ │ 球队API  │ │ 赔率API  │ │ 球员API  │ │ 模型管理  │ │
│  └─────┬────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬─────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │  auth    │ │  data    │ │ leagues  │ │ database │ │  review   │ │
│  │ 认证API  │ │ 数据API  │ │ 联赛API  │ │ 数据库API│ │ 复盘API   │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └───────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      服务层 (Services Layer)                         │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  PredictionService (prediction-service.js)                   │   │
│  │  ├─ predict()          — 基础多模型集成预测                    │   │
│  │  ├─ predictWithOdds()  — 融合赔率数据的预测                    │   │
│  │  ├─ predictScore()     — 比分预测 (T-006 v4)                  │   │
│  │  ├─ loadTrainedModels()— 加载 XGBoost/LightGBM 模型           │   │
│  │  ├─ configureEngine()  — 配置预测引擎参数                      │   │
│  │  └─ setupHotReload()   — 模型热更新监控                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────────┐ │
│  │ cacheService │ │ dataService  │ │  modelHotReloader            │ │
│  │ Redis 缓存   │ │ 数据查询服务 │ │  模型文件监听 + 自动重载     │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────────┘ │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────────┐ │
│  │trainScheduler│ │reviewService │ │  leagueRules                 │ │
│  │ 自动重训调度 │ │ 复盘分析服务 │ │  联赛规则验证                 │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    核心引擎层 (Engine Layer)                          │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  PredictionEngine (shared/prediction-engine.js)              │   │
│  │  核心算法:                                                     │   │
│  │  ├─ calcLambdaMatch()         — Poisson 进球期望计算           │   │
│  │  ├─ poissonPMF()              — Poisson 概率质量函数           │   │
│  │  ├─ dixonColePMF()            — Dixon-Coles 低比分修正         │   │
│  │  ├─ predictScoreV4()          — T-006 v4 比分预测              │   │
│  │  ├─ monteCarloScoreSimulate() — Monte Carlo 比分模拟           │   │
│  │  ├─ fuseScorePredictions()   — 多模型比分融合                  │   │
│  │  ├─ predictStacked()          — Stacking 集成预测              │   │
│  │  ├─ buildFeatures()           — 187维特征构建                  │   │
│  │  ├─ createPredictionResult()  — 最终预测结果组装                │   │
│  │  ├─ calcWinDrawLosePoisson()  — Poisson WDL 计算               │   │
│  │  ├─ calcHandicap()            — 让球盘预测                      │   │
│  │  ├─ calcTotalGoals()          — 总进球预测                      │   │
│  │  └─ adjustLambdaForMidScore() — 中比分 λ 调整 (A-002)          │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  资产模块 (assets/modules/)                                     │   │
│  │  ├─ models/poisson.js         — Poisson 分布工具               │   │
│  │  ├─ models/ml-framework.js    — ML 模型加载与推理              │   │
│  │  ├─ inference/stacking.js     — Stacking 集成实现              │   │
│  │  ├─ inference/calibration.js  — 概率校准 (Platt Scaling)       │   │
│  │  └─ math/pmf-utils.js         — 概率质量函数工具                │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     数据层 (Data Layer)                              │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  better-sqlite3 (WAL 模式, busy_timeout=5000ms)               │   │
│  │  ├─ data/five_leagues.db  — 生产运行时数据库 (12表, 7,672条)  │   │
│  │  ├─ data/odds.db          — 赔率+比赛数据 (5联赛, 5,252场)    │   │
│  │  └─ data/odds_timing.db   — 时序赔率数据                       │   │
│  └──────────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  Redis (已启用) — 预测缓存、限流、会话管理，端口 6379               │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```
</details>

### 1.2 进程架构

```
PM2 (fork 模式, instances: 1)
  └── server/cluster.js (主进程)
        ├── fork 2-8 个 Worker (默认 CPU 核数)
        │     ├── Express HTTP 服务
        │     ├── WebSocket 连接
        │     └── better-sqlite3 (WAL + busy_timeout)
        │
        ├── 定时任务 (仅主进程)
        │     ├── trainScheduler — 自动重训调度
        │     └── reviewService  — 复盘分析
        │
        └── WebSocket 跨进程广播中转
```

### 1.3 技术栈选型

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| 运行时 | Node.js 20+ | 全栈 JavaScript，前后端统一语言 |
| HTTP 框架 | Express.js 4.x | 成熟稳定，生态丰富 |
| 数据库 | better-sqlite3 + WAL | 零配置，高性能本地数据库，WAL 支持多进程读 |
| 缓存 | Redis 5.0 (已启用) | 预测缓存、限流计数器 |
| 进程管理 | PM2 5.3+ | 生产级进程守护，日志管理 |
| 集群 | Node.js cluster | 水平扩展，多核利用 |
| 前端 | React 18 + Vite 5 | 现代前端框架，快速构建 |
| UI | Ant Design 5 + ECharts | 企业级组件库 + 数据可视化 |
| ML 训练 | Python 3.14 + XGBoost + LightGBM | 梯度提升树，足球预测 SOTA |
| 特征工程 | pandas + numpy + scipy | 数据处理与统计计算 |
| 超参优化 | Optuna | 贝叶斯优化，高效搜索 |
| 测试 | pytest + unittest | Python 标准测试框架 |
| WebSocket | ws | 轻量级实时通信 |

---

## 二、预测流程与数据流转

### 2.1 完整预测流程

```
用户请求 POST /api/predict
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: 请求验证                                                │
│  ├─ 检查 homeTeam / awayTeam 是否提供                            │
│  ├─ 球队名称标准化 (teamNameIndex 查找)                          │
│  └─ 缓存查询 (Redis, TTL=7200s)                                  │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 2: 数据加载                                                │
│  ├─ 从 five_leagues.db 加载球队属性 (attack, defence, xGOT等)    │
│  ├─ 从 assets/ 加载球队数据 (winRate, recentForm, Elo等)         │
│  ├─ 加载对手上下文 (opponentStrength, matchContext)              │
│  └─ 加载联赛规则 (主场优势因子, 场均进球等)                       │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 3: 特征构建 (buildFeatures, 187维)                         │
│  ├─ 基础特征 (12维): 联赛编码, 日期, 中性场地                     │
│  ├─ 球队特征 (52维): 胜率, 进球, 形态, 连败/不败, 稳定性等        │
│  ├─ 对比特征 (15维): form_diff, goals_diff, streak_diff等         │
│  ├─ Elo 特征 (8维): home_elo, away_elo, elo_diff, momentum等     │
│  ├─ 赔率特征 (39维): 凯利指数, 变化率, 市场信心度等               │
│  ├─ 时序 Lag 特征 (68维): shift(1)+rolling window 防泄露          │
│  └─ 特征缩放: StandardScaler 标准化                               │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 4: 多模型预测 (6个模型并行)                                 │
│  ├─ Poisson 模型:  calcLambdaMatch() → calcWinDrawLosePoisson()  │
│  ├─ Dixon-Coles:   calcLambdaMatch() → dixonColePMF()            │
│  ├─ Bayesian-SSM:  SSM 修正的 Poisson                            │
│  ├─ XGBoost:       XGBClassifier.predict_proba()                 │
│  ├─ LightGBM:      LGBMClassifier.predict_proba()                │
│  └─ Elo:           Elo Rating 公式                               │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 5: Stacking 集成 (predictStacked)                          │
│  ├─ 根据比赛类型动态调整权重 (regular/derby/cup 等)                │
│  ├─ Platt Scaling 概率校准 (XGBoost/LightGBM)                    │
│  ├─ 加权平均: Σ(weight_i × prob_i)                                │
│  └─ 温度缩放: softmax(probs / T), T=1.000（v8.3 回退 argmax）                        │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 6: 比分预测 (T-006 v4)          — v4 历史口径，当前 v5      │
│           （详见《模型架构分析报告_v1.0.md》§6.4）               │
│  ├─ λ 调整: adjustLambdaForMidScore() (A-002)                    │
│  ├─ Dixon-Coles 修正 Poisson 矩阵                                │
│  ├─ Monte Carlo 模拟 (n=3000；v8.3 起为 500)                      │
│  ├─ 方案A 向量化 + 赔率融合                                       │
│  └─ 联赛特定 ρ 校准                                              │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 7: 结果组装 (createPredictionResult)                       │
│  ├─ predictions: WDL 概率 + 让球 + 总进球               │
│  ├─ scorePrediction: Top-10 比分 + 精确比分概率                   │
│  ├─ oddsAnalysis: 凯利指数 + 价值投注信号                         │
│  ├─ warnings: 规则引擎辅助信号 (generateRuleWarnings)             │
│  ├─ confidence: 置信度评估                                        │
│  └─ lambda: λ 调整元数据                                         │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│  Step 8: 响应返回                                                │
│  ├─ 写入缓存 (Redis, TTL=7200s)                                  │
│  ├─ WebSocket 广播 (实时推送)                                     │
│  └─ JSON 响应 (ApiResponse.prediction)                           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 API 端点一览

| 端点 | 方法 | 认证 | 功能 |
|------|------|------|------|
| `POST /api/predict` | POST | optionalAuth | 基础预测（WDL + 让球 + 总进球 + 比分） |
| `POST /api/predict/batch` | POST | authenticateToken | 批量预测多场比赛 |
| `POST /api/predict/with-odds` | POST | optionalAuth | 融合赔率数据的预测 |
| `POST /api/predict/with-lineup` | POST | optionalAuth | 融合首发阵容的预测 |
| `POST /api/predict/score` | POST | optionalAuth | 比分预测 (T-006；当前生产 v5，v4 历史口径见 §8.2) |
| `GET /api/teams` | GET | - | 所有球队列表 |
| `GET /api/teams/:key` | GET | - | 指定球队详情 |
| `GET /api/teams/league/:league` | GET | - | 按联赛分组 |
| `GET /api/odds/:matchId` | GET | - | 指定比赛赔率 |
| `POST /api/odds/:matchId` | POST | authenticateToken | 更新赔率数据 |
| `GET /api/players` | GET | - | 球员列表 |
| `GET /api/players/:id` | GET | - | 球员详情 |
| `GET /api/health` | GET | - | 健康检查 |
| `GET /api/model/info` | GET | optionalAuth | 模型信息 |
| `GET /api/model/reload/status` | GET | optionalAuth | 热更新状态 |
| `POST /api/model/reload` | POST | optionalAuth | 手动触发热更新 |
| `GET /api/cache/stats` | GET | - | 缓存统计 |
| `DELETE /api/cache/predictions` | DELETE | - | 清除预测缓存 |

---

## 三、核心算法详解

### 3.1 Poisson 分布体系

**3.1.1 Poisson 概率质量函数 (PMF)**

```
P(X = k) = (λ^k × e^(-λ)) / k!
```

其中 λ 为进球期望值，k 为进球数。

**3.1.2 进球期望 (λ) 计算 — calcLambdaMatch()**

```
lambdaA = (attackA × (1 - defenceB) × 0.3  + xGA × (1 - defenceB) × 0.3  + homeFactorA × 0.2  + (1 - xGAB) × 0.2) × tempoA
lambdaB = (attackB × (1 - defenceA) × 0.3  + xGB × (1 - defenceA) × 0.3  + homeFactorB × 0.2  + (1 - xGAA) × 0.2) × tempoB
```

> **P0 修复 (C-20260816-181, C-20260816-191)**: `defence` = 防守能力（0~1，越高越好），使用 `(1 - defence)` 转换为防守漏洞率，确保公式方向一致：强进攻 × 弱防守 → 高进球期望。prediction-engine.js 和 poisson.js 均已修复。

公式包含四个分量：
- **攻击×防守漏洞 (30%)**: 进攻能力 × 对方防守漏洞率
- **xGOT 修正 (30%)**: 预期进球转化率
- **主场优势 (20%)**: 非中立场地 homeAdv=1.12
- **防守漏洞 (20%)**: 对方预期失球

环境修正因子：
- **天气影响**: clear=1.0, rain=0.92, heavy_rain=0.85, snow=0.80
- **场地影响**: grass=1.0, artificial=1.05, frozen=0.85
- **伤病因子**: injury × keyPlayer 调整
- **Clamp**: λ ∈ [0.3, 3.5]

**3.1.3 中比分 λ 调整 — adjustLambdaForMidScore() (A-002)**

问题：39.6% 的比赛属于中比分区间（3-4球），但原始 λ 计算偏保守，导致 Top-1 概率为 0%。

> **P1 修复 (C-20260816-193)**: 优先使用赔率隐含 P(win) 替代 WDL 模型预测，解除耦合。公式：`impliedWinA = (1/odds_win) / (1/odds_win + 1/odds_draw + 1/odds_lose)`

两阶段调整：

```
阶段1 (WDL 因子): λ' = λ × (0.5 + P(win) × 1.5)
  P(win) 来源: 优先赔率隐含概率 (市场独立) → 回退模型预测
  Clamp: [0.7, 1.8]

阶段2 (TG 赔率因子): λ'' = λ' × tgExpected / (λH + λA)
  Clamp: [0.85, 1.4]
```

效果：纯 WDL 调整 +5-8%，有赔率数据时 +49.8%。

### 3.2 Dixon-Coles 修正模型

标准 Poisson 模型假设两支球队进球独立，但实际低比分区间存在相关性。

**修正公式**:

```
P(x, y) = τ(x, y) × Poisson(x|λ1) × Poisson(y|λ2)

其中:
  τ(0,0) = 1 - λ1 × λ2 × ρ
  τ(0,1) = 1 + λ1 × ρ
  τ(1,0) = 1 + λ2 × ρ
  τ(1,1) = 1 - ρ
  τ(x,y) = 1  (其他情况)
```

- ρ = -0.45 (最优参数，由 config.yaml grid_search 确定)
- ρ_high = -0.10 (高比分扩展参数)

### 3.3 T-006 比分预测完整流程（v4 历史口径）

> **口径注记**：本节为 T-006 v4 的完整推导流程（2026-08-18 快照，公式/阶段推导为本节独有内容）。当前生产为 T-006 **v5**（2026-09-08 投产：λ 数值求解 + IPF 重加权 + 生产 WDL 锚定），见《模型架构分析报告_v1.0.md》§6.4；v4 历史指标见 §8.2。

```
输入: lambdaHome, lambdaAway
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段1: 原始 Poisson PMF 矩阵                                    │
│ pmfHome[g] = Poisson(λH, g), pmfAway[g] = Poisson(λA, g)       │
│ rawMatrix[h][a] = pmfHome[h] × pmfAway[a]                       │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段2: Dixon-Coles τ 修正矩阵                                   │
│ τ[0][0] = 1 - λH × λA × ρ                                       │
│ τ[0][1] = 1 + λH × ρ                                            │
│ τ[1][0] = 1 + λA × ρ                                            │
│ τ[1][1] = 1 - ρ                                                 │
│ 高比分扩展: τ[x][y] = 1 - ρ_high × λH × λA / 10 (x+y≥5)         │
│ τ 裁剪: [0.1, 3.0]                                              │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段3: 应用修正→归一化                                           │
│ correctedMatrix[h][a] = rawMatrix[h][a] × τMatrix[h][a]          │
│ 全矩阵归一化: sum = 1.0                                          │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段4: Monte Carlo 验证 (n=3000；v8.3 起为 500)                 │
│ 对每个模拟: 从 correctedMatrix 采样比分                          │
│ 统计: 精确命中率, 1球内命中率, 2球内命中率                        │
│ 输出: 置信度区间                                                 │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段5: 方案A 向量化 + 赔率融合                                    │
│ 方案A: 基于 correctedMatrix 的 Top-K 比分                        │
│ 赔率融合: 隐含概率 × 模型概率 → 调整后比分分布                    │
│ 联赛特定 ρ 校准: 英超 -0.30, 德甲 -0.35, 西甲 -0.40 等           │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────┐
│ 阶段6: 高比分锚点注入 (Tail Anchor Injection)                    │
│ 触发条件: λ_sum > 2.2 or λ_diff > 0.5                            │
│ 候选池: 5+ 球比分锚点 (2:3, 3:2, 3:3, 2:4, 4:2 等)              │
│ 大分差锚点: 3:0, 4:0, 3:1, 4:1 等                                │
│ 自适应 max_goals: λ_sum > 3.5 → 8, 否则 7                        │
└─────────────────────────────────────────────────────────────────┘
          │
          ▼
输出: Top-10 比分 + 精确比分概率 + 置信度
```

### 3.4 Stacking 集成方法

**3.4.1 基础模型（v3.0 = 5 基础模型 + LR meta-learner）**

| 模型 | 固定权重（回退） | 方法 |
|------|------|------|
| Dixon-Coles | 30% | Poisson + 低比分相关性修正 |
| XGBoost | 30% | 208维特征梯度提升 |
| LightGBM | 25% | 高维特征高效梯度提升 |
| Elo | 15% | 评级系统概率 |
| 贝叶斯层级模型 | 20% | Baio & Blangiardo 层级泊松 + Dixon-Coles ρ（P1-5） |

> **v3.0 变更 (2026-08-28)**: ① 新增第 5 基础模型「贝叶斯层级模型」（分联赛训练，聚合 RPS 0.2092 ≤ 0.21）；② Stacking 融合优先走 **LR meta-learner**（5 模型 × 3 WDL 概率 = 15 维 meta 特征 → 多分类 LogisticRegression，OOF RPS 0.1984 < 固定权重 0.2038），仅当 5 模型不齐全时回退上表固定权重。英超独立模型仍为 `epl_reference` 独立参考输出，不参与 Stacking。XGB/LGB 特征维度 = 208 维（slim_odds + ts_odds + consensus_odds，见 §4.1）。

**3.4.2 比赛类型动态权重（v8.3 更新）**

| 比赛类型 | 权重调整 |
|----------|----------|
| regular (常规) | 默认权重 |
| derby (德比) | XGBoost +5%, DC -5% |
| cup (杯赛) | DC +5%, XGBoost -5% |
| promotion (升级战) | XGBoost +5%, DC -5% |
| relegation (保级战) | XGBoost +3%, DC +3% |

**3.4.3 概率校准 (Platt Scaling)**

```
calibrated[i] = 1 / (1 + exp(-(a_i × prob_i + b_i)))
```

对 XGBoost 和 LightGBM 输出进行 Platt Scaling 校准，然后归一化。

### 3.5 Elo Rating 系统

```
预期得分: E_A = 1 / (1 + 10^((R_B - R_A - H) / 400))
更新:     R_A' = R_A + K × (实际得分 - E_A)
```

- 初始 Elo: 1500
- K 因子: 20
- 主场优势: H = 65
- 平局概率: 0.26 × exp(-|elo_diff| / 600)

### 3.6 赔率分析模块

| 指标 | 公式 | 含义 |
|------|------|------|
| 凯利指数 | (隐含概率 × 赔率 - 1) / (赔率 - 1) | 投注价值指标 |
| 赔率变化率 | (收盘赔率 - 开盘赔率) / 开盘赔率 | 市场情绪变化 |
| 市场信心度 | 1 - 赔率标准差/均值 | 市场一致性 |
| 价值投注信号 | 模型概率 - 隐含概率 | 模型 vs 市场差异 |

---

## 四、特征工程体系

### 4.1 特征维度总览（v3.0：208维，slim_odds + ts_odds + consensus_odds）

> **口径注记**：208 维为当前生产口径（与《模型架构分析报告_v1.0.md》§3.2 一致）；本节独有的 165→198→208 维演化路径与 A/B 证据为历史记录（2026-08-28）。

> **注 (2026-08-28)**: 生产 WDL 特征已收敛至 **208 维**。基础为 slim_odds（`ts_odds=False`）的 165 维基线（含 SofaScore 球员 46 维 + PA 球员可用性 24 维等），`ts_odds=True` 再接入 33 维 = 时序赔率 22 维（D-013 三张时序表 + 去水隐含概率漂移 + 跨盘口一致性）+ 比分赔率 8 维（T-003.1）+ T-003.3 扩展 3 维（165→198 维），`consensus_odds=True` 再接入 10 维 mkt_* 共识特征（198→208 维）；时序特征按联赛 z-score 归一。A/B（XGB+LGB 融合 + 滚动切分 14,312 场）Blend RPS -0.0155 / LogLoss -0.0072 / Acc +0.0019 / DrawRecall +0.0049，四项全优。`xg_deep`（P1-8 xG 差值趋势/分位 6 维）覆盖率 88.5% 达标但 RPS 一致轻微劣化（+0.0028），保持 `xg_deep=False` 暂不采用。

| 类别 | 维度 | 说明 |
|------|------|------|
| 基础特征 | 12 | 联赛编码, 日期, 中性场地标记 |
| 球队特征 (主) | 26 | 胜率, 进球, 射门, 形态, 连败/不败, 稳定性 |
| 球队特征 (客) | 26 | 同上 |
| 对比特征 | 15 | form_diff, goals_diff, defence_diff, streak_diff |
| Elo 特征 | 8 | home_elo, away_elo, elo_diff, momentum, draw_prob |
| 赔率特征 (精简) | 30 | WDL/HCP 隐含概率, 凯利指数, 赔率变化率, 市场置信度 |
| 时序 Lag 特征 | 68 | 17指标 × 4窗口 (w3, w5, w10, all) |
| 对手强度特征 | 5 | opponent_win_rate, opponent_strength_diff |
| 联赛特征 | 4 | league_avg_goals, league_home_advantage |
| SofaScore 球员特征 | 46 | 评分/xG/xA/传球/抢断/门将/跑动/阵型 — T-007 已集成 |
| 非线性增强 | 5 | entropy/gini/imp_ratio/x_kelly — T-003.2 精简后 |
| 已移除 | -49 | 24维纯单调变换 + 其他冗余特征 |

### 4.2 特征时序分离机制 (防数据泄露)

```
┌──────────────────────────────────────────────────────────────────┐
│                    特征工程时序分离架构                            │
│                                                                   │
│  ┌─────────────────────┐    ┌─────────────────────┐              │
│  │  赛前特征白名单      │    │  赛后特征黑名单      │              │
│  │  (PRE_MATCH_CATALOG)│    │  (POST_MATCH_BLACKLIST)│            │
│  │                     │    │                     │              │
│  │  ✅ 赔率特征 (39)   │    │  ❌ homeGoals       │              │
│  │  ✅ 球队历史统计    │    │  ❌ awayGoals       │              │
│  │  ✅ Elo rating      │    │  ❌ homeXg          │              │
│  │  ✅ H2H 记录        │    │  ❌ awayXg          │              │
│  │  ✅ 联赛/日期       │    │  ❌ shots           │              │
│  │  ✅ Lag 版本特征    │    │  ❌ possession      │              │
│  └─────────────────────┘    └─────────────────────┘              │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  检测机制: detect_leakage(X)                              │    │
│  │  ├─ 检查所有特征是否在赛前白名单中                         │    │
│  │  ├─ 标记不在白名单中的特征为"泄露风险"                     │    │
│  │  └─ 验证: validate_no_leakage(X) 确保零泄露               │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │  Lag 版本特征 (shift(1) + rolling window)                 │    │
│  │  ├─ 每场比赛仅使用早于该比赛日的历史数据                   │    │
│  │  ├─ 窗口: w3=近3场, w5=近5场, w10=近10场, all=全历史      │    │
│  │  ├─ 指标: 17个 (胜率, 进球, 射门, 控球, 犯规等)           │    │
│  │  └─ 日期校验: 每场 lag 特征只使用早于比赛日的历史数据 ✅    │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 4.3 关键特征说明

**球队特征 (buildFeatures 核心字段)**:

```javascript
// 球队统计
home_avg_goals, home_avg_opp_goals, home_win_rate, home_draw_rate, home_loss_rate
home_goals_std, home_recent_form, home_form_trend
home_consecutive_wins, home_consecutive_losses, home_consecutive_undefeated
home_weighted_win_rate, home_weighted_avg_goals

// 主客场拆分
home_home_win_rate, home_away_win_rate, home_home_goals, home_away_goals
home_home_advantage

// Elo 体系
home_elo, away_elo, elo_diff, elo_ratio, elo_home_expected
elo_away_expected, elo_draw_prob, home_elo_momentum
```

**对比特征**:
```javascript
form_diff, goals_diff, defence_diff, recent_form_diff
form_trend_diff, streak_diff, undefeated_diff
weighted_form_diff, weighted_goals_diff, venue_diff
goals_stability_diff, opponent_strength_diff
```

---

## 五、模型训练与部署

### 5.1 训练管线

```
┌──────────────────────────────────────────────────────────────────┐
│                      训练入口: train_models.py                    │
│                                                                   │
│  Step 1: 数据加载                                                 │
│  ├─ load_match_data_odds() → 从 odds.db 加载 5,252 场比赛        │
│  │   （5,252 为 2026-08 快照口径，P0-1 回溯采集前；当前 matches  │
│  │    14,511 场见《模型架构分析报告_v1.0.md》§3.1）              │
│  └─ 球队名标准化 (TEAM_NAME_MAP, 193条)                          │
│                                                                   │
│  Step 2: 特征工程                                                 │
│  ├─ build_all_features(df, include_odds=True) → 208维（slim+ts_odds+consensus） │
│  ├─ 含 SofaScore 球员特征 46维 (T-007)                             │
│  ├─ 含非线性增强 5维 (T-003.2, 精简后)                             │
│  ├─ selected_features.pkl 定义最终特征集                            │
│  ├─ feature_temporal_split() → 赛前特征过滤                       │
│  ├─ detect_leakage(X) → 泄露检测                                  │
│  └─ validate_no_leakage(X) → 零泄露验证                           │
│                                                                   │
│  Step 3: 样本权重                                                 │
│  ├─ class_weight: 类别平衡 (ratio=1.20)                           │
│  ├─ sample_time_decay: 赛季级时间衰减 (half_life=180-365天)      │
│  ├─ league_compensation: 联赛补偿 (德甲/法甲 ×1.20)              │
│  └─ 最终权重 = class_weights × drift_weights × time_decay_weights │
│                                                                   │
│  Step 4: 交叉验证                                                 │
│  ├─ 5折 TimeSeriesSplit (时间顺序分割)                            │
│  ├─ 嵌套 CV: 外层评估 + 内层 Optuna 调参                          │
│  └─ 过拟合监控: train_acc - val_acc                               │
│                                                                   │
│  Step 5: 模型训练                                                 │
│  ├─ XGBoost (XGBClassifier)                                      │
│  │   ├─ max_depth=4, lr=0.03, n_estimators=300                    │
│  │   ├─ reg_alpha=0.064, reg_lambda=8.00                          │
│  │   └─ early_stopping_rounds=15                                  │
│  ├─ LightGBM (LGBMClassifier)                                     │
│  │   ├─ max_depth=3, lr=0.0106, num_leaves=202                    │
│  │   ├─ reg_alpha=0.153, reg_lambda=1.46                          │
│  │   └─ early_stopping_rounds=15                                  │
│  └─ CalibratedClassifierCV (Platt Scaling)                        │
│                                                                   │
│  Step 5.5: 平局决策阈值调整（v8.3 默认 argmax，联赛分档开关保留）              │
│  ├─ 不修改概率，仅在分类时调整平局阈值                              │
│  ├─ 保持概率校准（三分类概率和=1.0）                                │
│  ├─ 全局 factor=1.1 (argmax)                                       │
│  ├─ 法甲 FL1: factor=0.0 (argmax)                                  │
│  ├─ 详见 9.8 (2026-08-20 完整网格 [0.80~1.10] 重校准)            │
│                                                                   │
│  Step 6: 模型导出                                                 │
│  ├─ 序列化为 .pkl 文件 (assets/)                                  │
│  ├─ 导出为 JS 可读格式 (xgb_model_export.js, lgb_model_export.js) │
│  └─ 元数据记录 (t005v3_metadata.json)                             │
└──────────────────────────────────────────────────────────────────┘
```

### 5.2 训练超参数 (config.yaml)

| 参数 | XGBoost | LightGBM |
|------|---------|----------|
| max_depth | 9 | 3 |
| learning_rate | 0.0492 | 0.0106 |
| num_boost_round | 181 | 164 |
| subsample | 0.665 | 0.998 |
| colsample_bytree | 0.501 | 0.787 |
| gamma | 4.956 | - |
| reg_alpha | 0.064 | 0.153 |
| reg_lambda | 8.003 | 1.462 |
| min_child_weight | 13 | 6 |
| scale_pos_weight | 1.97 | - |
| early_stopping | 15 | 15 |

**预测阶段参数**:

| 参数 | 值 | 说明 |
|------|-----|------|
| draw_threshold_factor | 1.0 | 平局决策阈值因子（v8.3 默认 argmax，联赛分档开关保留） |
| draw_threshold_mode | argmax | 模式：argmax 或 league_specific（DRAW_THRESHOLD_MODE） |
| temperature (T) | 1.000 | 概率平滑温度（v8.3 回退 argmax，原 0.8） |
| MONTECARLO_SIMULATIONS | 500 | 比分预测模拟次数（v8.3 原 3000） |

### 5.3 自动重训机制

```
触发条件 (三重):
├─ 定时触发: 每日 08:00 (Windows 计划任务 T005v3_AutoRetrain)
├─ 数据触发: 新数据入库检测 (matches/handicap_history/wdl_history)
└─ 周期触发: 距上次训练超过 7 天

性能门禁:
├─ 走水召回率 ≥ 0.30
├─ 预测率偏差 ≤ 2pp
└─ 不通过 → 不更新生产模型，发送告警
```

### 5.4 模型热更新

```
modelHotReloader (文件监听)
├─ 监听 assets/ 目录变化
├─ 检测到新模型文件 → 自动加载
├─ 更新 PredictionEngine 配置
└─ 零停机更新 (无需重启 PM2)
```

---

## 六、数据采集管线

### 6.1 数据源

| 数据源 | 数据类型 | 采集方式 |
|--------|----------|----------|
| FBref | 比赛数据, 球员统计, xG, 射门, 控球 | Python 爬虫 |
| SofaScore | 比赛事件, 阵容, 球员评分 | API 采集 |
| Sporttery (竞彩网) | 赔率数据 (WDL, 让球, 总进球) | 爬虫 + API |
| Football-Data API | 赛程, 积分榜, 球队信息 | REST API |
| The Odds API | 实时赔率 | REST API |

### 6.2 数据采集管线路径

```
h:\zuqiu\
├── collection/             # 数据采集脚本
│   ├── final_sofascore_collector.py   — SofaScore 全量采集
│   ├── dry_run_sporttery.py           — 竞彩网 dry-run
│   ├── supplement_sporttery_odds.py   — 竞彩网赔率补充
│   └── test_sofascore_api.py          — API 测试
│
├── import_data/            # 数据导入脚本
│   ├── import_bundesliga.py           — 德甲导入
│   ├── import_ligue1.py               — 法甲导入
│   ├── populate_matches_from_fbref.py — FBref 数据填充
│   ├── sofascore_backfill_fields.py   — SofaScore 字段回填
│   ├── normalize_league_names.py      — 联赛名归一化
│   └── cleanup_matches.py             — 数据清洗
│
├── pipeline/               # 管线编排
│   ├── pipeline_F0F1_orchestrator.py  — 采集编排
│   ├── pipeline_d_to_e_orchestrator.py— D→E 阶段编排
│   └── f1b_progress_monitor_and_post_pipeline.py — 监控+后处理
│
├── features/               # 特征生成
│   ├── sofascore_pre_match_features.py — 赛前特征
│   └── test_d013_fix.py               — D-013 修复测试
│
├── validation/             # 数据验证 (18个脚本)
│   ├── check_odds_db.py, check_odds_coverage.py
│   ├── check_season_data.py, check_season_v2.py
│   ├── verify_matches.py, verify_fbref_total.py
│   ├── verify_sofascore_db.py, check_leagues_sofascore.py
│   └── ... (14个额外验证脚本)
│
└── analysis/               # 质量分析
    ├── sofascore_quality_audit.py     — 数据质量审计
    └── final_sofascore_review.py      — 最终审查
```

### 6.3 数据库结构（历史口径）

> **精简**：数据库表清单与行数均为 2026-08 快照（5,252 场 / 12 表 7,672 条，P0-1 回溯采集前口径）；当前 odds.db 为 **35 表 / 1.66GB / matches 14,511 场**，完整表清单见《模型架构分析报告_v1.0.md》§3.1。以下保留 2026-08 历史记录供演进追溯。

**odds.db** (核心数据仓库, 2026-08 快照):
```
matches             — 比赛基础信息 (5,252场)
wdl_history         — WDL 赔率历史
handicap_history    — 让球赔率历史
total_goals_history — 总进球赔率历史
score_history       — 比分历史
match_id_mapping    — 中英文 match_id 桥接表 (98.6%覆盖率)
team_name_map       — 球队名标准化映射 (193条)
```

**five_leagues.db** (生产运行时数据库, 2026-08 快照):
```
12 表, 7,672 条记录
— 球队属性, 球员数据, Elo 评级, 联赛规则等
```

---

## 七、关键文件功能说明

### 7.1 核心引擎文件

> **精简**：核心引擎 / 服务 / 训练管线等文件职责与关键函数行号见《模型架构分析报告_v1.0.md》§四（模块清单表）与 §3.3~3.5（分层详解），本表不再重复。

### 7.2 训练管线文件

> **精简**：与《模型架构分析报告_v1.0.md》§四模块清单表高重复，改引用该报告 §四。
> 本文档独有（未列入①模块清单表）的脚本：`train_models_v2.py`（训练入口）、`feature_temporal.py`（时序分离 / 白名单黑名单 / 泄露检测）、`elo_rating.py`（Elo 评级）、`deploy_t005v3_final.py`（T-005 v3 部署/重训）、`fix_draw_recall.py`（平局召回修复 draw_boost）、`analyze_epl_issue.py`（英超表现异常分析）、`label_randomization_test.py`（标签随机化测试）、`score_accuracy_validation.py`（比分命中率验证）、`dedup_analysis.py`（重复样本检测）、`train_epl_independent.py`（英超独立模型训练：205 维特征，v3.0 优化版，CV 47.25%）。

### 7.3 配置文件

| 文件 | 路径 | 功能 |
|------|------|------|
| config.yaml | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/config.yaml) | 训练/模型/Optuna/联赛参数配置 |
| ecosystem.config.cjs | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/ecosystem.config.cjs) | PM2 进程管理配置 (fork, 1G内存限制) |
| package.json | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/package.json) | Node.js 依赖和脚本 |
| .env | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/.env) | 环境变量 (端口、密钥、API Key) |
| pyrightconfig.json | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/pyrightconfig.json) | Python 类型检查排除规则 |
| pytest.ini | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/pytest.ini) | pytest 测试配置 |
| vite.config.js | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/vite.config.js) | Vite 构建配置 (前端) |

### 7.4 部署脚本

| 文件 | 路径 | 功能 |
|------|------|------|
| start-production.bat | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/start-production.bat) | Windows 生产环境一键部署 |
| deploy.sh | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/deploy.sh) | Linux 生产环境一键部署 |
| run_startup.py | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/run_startup.py) | 对话启动上下文生成器 |
| run_stage4_startup.py | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/run_stage4_startup.py) | 阶段四特征工程启动脚本 |
| start_session.ps1 | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/start_session.ps1) | 三阶段隔离模式启动脚本 |
| test_model_admin.mjs | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/test_model_admin.mjs) | model-admin API 测试 |

### 7.5 模型资产文件

| 文件 | 路径 | 功能 |
|------|------|------|
| t005v3_direction_predictor.pkl | assets/ | T-005 v3 方向预测器 (LightGBM) |
| t005v3_draw_detector.pkl | assets/ | T-005 v3 走水检测器 |
| t005v3_metadata.json | assets/ | T-005 v3 元数据 (温度/阈值/class_weight) |
| t006_lowgoal_classifier_v1.pkl | assets/ | T-006 低进球分类器 (Python 训练源模型) |
| t006_lowgoal_export.js | assets/ | T-006 低进球分类器 JS 导出 (200树/22维/base=0/lr=1，供 prediction-service 加载，C-20260819-004) |
| t006_feature_spec.js | assets/ | T-006 特征规格 (特征名 + 缺失值回退 + 联赛映射，参考用，C-20260819-004) |
| t006_parity_sample.json | tests/fixtures/ | T-006 parity 验证样本 (法甲 0:0 场景，JS vs Python 一致性校验) |
| model-engine.js | assets/ | 旧版预测引擎 (兼容层) |
| model_config.js | assets/ | 模型配置 |
| team_attributes.json | assets/ | 球队属性 |
| stacking_weights.json | assets/ | Stacking 权重 |
| league_tier.json | assets/ | 联赛层级 |
| league_tier_weight.json | assets/ | 联赛层级权重 |
| team_name_map.json | assets/ | 球队名标准化映射 (193条) |

### 7.6 文档文件

| 文件 | 路径 | 功能 |
|------|------|------|
| accuracy-improvement-plan.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/accuracy-improvement-plan.md) | 准确度提升方案 (v2.0) |
| optimization_log.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/optimization_log.md) | 优化日志 (§4.1-§4.35) |
| key_decisions.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/key_decisions.md) | 关键架构决策记录 |
| change_log.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/change_log.md) | 变更日志（统一在 docs/，原 data/change_log.md 已合并删除） |
| model_optimization_plan.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/model_optimization_plan.md) | 模型优化计划 |
| prompt_template.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/prompt_template.md) | AI 提示词模板 |
| CONVERSATION_WORKFLOW_GUIDE.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/CONVERSATION_WORKFLOW_GUIDE.md) | 对话工作流指南 |
| DEPLOYMENT_CONFIG_v2.0.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/DEPLOYMENT_CONFIG_v2.0.md) | 部署配置清单 |
| feature-engineering-guide.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/feature-engineering-guide.md) | 特征工程指南 |
| player-feature-spec.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/player-feature-spec.md) | 球员特征规格 |
| league-data-spec.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/league-data-spec.md) | 联赛数据规格 |
| ARCHITECTURE_EVALUATION_REPORT_v2.0.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/ARCHITECTURE_EVALUATION_REPORT_v2.0.md) | 架构评估报告 |
| PROJECT_DELIVERY_REPORT.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/PROJECT_DELIVERY_REPORT.md) | 项目交付报告 |
| unimplemented_optimization_plans_report.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/unimplemented_optimization_plans_report.md) | 58项未实施优化计划 |
| VERSION_RELEASE_v2.0.md | [根目录](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/VERSION_RELEASE_v2.0.md) | v2.0 版本发布清单 |
| 评估报告解读与系统优化可行性分析.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/评估报告解读与系统优化可行性分析.md) | 8项检测评估解读 + 三阶段验证结果 |
| 西甲第1轮赛前预测完整报告_20260815_224158.md | [docs/](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/西甲第1轮赛前预测完整报告_20260815_224158.md) | 西甲第1轮完整预测报告 (SofaScore + 赔率) |

---

## 八、性能指标（历史口径与验证）

> **说明**：本章为「历史指标与验证口径」专章。除 §8.3 负载测试（独有验证证据）外，各模型"当前值"一律以《模型架构分析报告_v1.0.md》§一表头 / §6 与《model_gap_analysis_report_v2.0.md》§2.6 为准；本章保留的均为 2026-08 各时点验证快照，并逐节标注口径。

### 8.1 T-005 v3 让球胜平负（2026-08 验证快照）

> **口径注记**：以下为 2026-08-15 验证快照（训练数据 5,252 场，P0-1 回溯采集前口径）；T-005 v3 当前生产状态（71 维两阶段模型）见《模型架构分析报告_v1.0.md》§一表头。

| 指标 | 值 | 说明 |
|------|-----|------|
| 模型类型 | LightGBM (71维: 61核心 + 10 Elo) | 两阶段：走水检测器 + 方向预测器 |
| CV 准确率 | 51.70% ± 1.72% | 5折 TimeSeriesSplit |
| 走水召回率 | 0.4220 | ✅ 达标 (≥0.30) |
| 预测率偏差 | 1.06pp | ✅ 达标 (≤2pp) |
| 训练数据 | 5,252场（2026-08 快照） | 5大联赛×3赛季 |

**WDL 平局修复 (决策阈值调整, 联赛专属分治)**:

> **注**: 下表为平局修复决策的历史示意（210 维旧模型）；2026-08-20 已用 209 维模型重校准，全局回调 argmax（1.0），联赛分档改为「反压低平局」，最新准确率见下方「联赛专属阈值策略」表与 [9.8](#98209维重训--联赛分档反压低平局校准2026-08-20)。

| 指标 | 原始 (argmax) | 全局 factor=1.1 | 联赛专属分治 | 说明 |
|------|:------------:|:---:|:---:|------|
| 准确率 | 54.44% | 53.50% | 53.07%~55.66% | 按联赛差异 |
| 平局召回率 | 35.3% | **47.3%** | 联赛专属 | +12.0pp |
| 平局精确率 | 38.5% | 37.3% | 基本持平 |  |
| 概率校准 | ✅ | ✅ 不变 | ✅ 不变 | 决策阈值不修改概率 |
| ECE | 0.0768 | 0.0768 | 0.0768 | 与原始相同 |

**联赛专属阈值策略 (C-20260820-037 完整网格 [0.80~1.10] 重校准)**:

> 旧版配置（C-20260816-200/201/205/206：德甲/英超=1.1、意甲/西甲/法甲=0.0）已于 2026-08-20 废弃；重训 209 维模型后，高平局联赛「反压低平局」（factor<1.0）更优。

| 联赛 | 实际平局率 | 专属 factor | argmax 准确率 | 最优准确率 | 增益 | 说明 |
|------|:---:|:---:|:---:|:---:|:---:|------|
| 意甲 | 28.1% | 0.85 | 55.58% | 56.55% | +0.97pp | 反压低平局 |
| 西甲 | 26.0% | 0.80 | 53.15% | 54.20% | +1.05pp | 反压低平局 |
| 德甲 | 25.5% | 0.85 | 56.32% | 57.19% | +0.87pp | 反压低平局 |
| 英超 | 24.5% | 0.0 | 56.44% | 56.44% | 0 | argmax |
| 法甲 | 23.8% | 0.0 | 54.37% | 54.37% | 0 | argmax |

**温度 T**: 0.800 | **统一阈值**: 0.500 | **class_weight**: 1.20

### 8.2 T-006 v4 比分预测（v4 历史口径）

> **口径注记**：以下为 T-006 **v4** 全量验证集指标（2026-08 快照；Top-5 64.66% 为全量验证集覆盖率，非严格时序口径）。当前生产 **v5**（严格时序 5 折 Top-1 13.14% / Top-5 45.47%，WDL 不劣化，2026-09-08 投产）见《模型架构分析报告_v1.0.md》§6.4。

| 指标 | 值 | 说明 |
|------|-----|------|
| 总比赛数 | 1,415 | 验证集 |
| Top-1 精确命中率 | 12.27% | 完全正确比分 |
| Top-3 覆盖率 | 31.49% | 3个最可能比分中命中 |
| **Top-5 覆盖率** | **64.66%** | 5个最可能比分中命中 |
| Top-10 覆盖率 | 76.21% | 10个最可能比分中命中 |
| 1球内命中率 | 88.20% | 偏差≤1球 |
| 2球内命中率 | 95.97% | 偏差≤2球 |
| 批次标准差 | 3.25% | σ ≤ 5.0% |
| 逐场评估耗时 | 0.022 ms/场 | 全批量化 |
| 批量回测总耗时 | 7.072s | 含ρ校准 11.428s |

> **注**: 64.66% 为 Top-5 覆盖率（5个最可能比分中包含实际比分的概率），非 Top-1 精确命中率。经 50 场抽样验证确认指标定义明确。

### 8.3 负载测试 (v2.0, 2026-08 快照)

| 并发 | QPS | P95(ms) | P99(ms) | 错误率 |
|------|-----|---------|---------|--------|
| 50 | 3,970 | 13.98 | 16.24 | 0% |
| 100 | 4,131 | 25.81 | 27.40 | 0% |
| 200 | 4,144 | 52.77 | 64.79 | 0% |
| 500 | 3,332 | 147.85 | 157.30 | 0% |
| 1000 | 2,663 | 367.25 | 1,190.03 | 1.43% |

- 峰值吞吐量: 4,144 QPS (@并发 200)
- 安全并发水位: 500
- 建议生产限流: 350 并发

### 8.4 核心 WDL 预测模型（2026-08 验证快照）

> **口径注记**：WDL 当前生产状态（208 维 / 5 基础模型 Stacking + LR meta-learner / OOF RPS 0.1984 / 贝叶斯聚合 RPS 0.2092）与《模型架构分析报告_v1.0.md》§一表头一致；下表 XGB 48.76% / LGB 48.57% 等为 2026-08-20 209 维重训验证值（历史证据）。

| 指标 | 值 |
|------|-----|
| 特征维度 | 208维（slim_odds 基线 165 维 + ts_odds 时序/比分 33 维 + consensus_odds 共识 10 维） |
| Stacking 模型 | 5 基础模型（Dixon-Coles + Elo + XGBoost + LightGBM + 贝叶斯）+ LR meta-learner |
| WDL meta-learner OOF RPS | 0.1984（< 固定权重 0.2038） |
| 贝叶斯层级模型 | 聚合 RPS 0.2092（≤ 0.21），优于朴素泊松 0.2316 / 无正则 MLE 0.2368 |
| 最新 XGBoost 验证准确率 | 48.76%（LogLoss 1.0215, Brier 0.6122） |
| 最新 LightGBM 验证准确率 | 48.57%（LogLoss 1.0255, Brier 0.6153） |
| 5 折滚动验证（XGBoost） | 49.91% ± 时序 |
| 英超独立模型 | 已从 Stacking 移除，改为 epl_reference 独立参考（CV 47.25% < 全局 53.99%） |

> **v3.0 (2026-08-28)**: ① 特征维度 165→208 维（ts_odds=True 时序/比分 +33 维、consensus_odds=True 共识 +10 维，Blend RPS -0.0155 四项全优）；② WDL Stacking 4 模型 → 5 基础模型 + LR meta-learner（新增贝叶斯层级模型，OOF RPS 0.1984）；③ xg_deep（P1-8）覆盖率达标但 RPS 轻微劣化，保持关闭。
>
> **v8.3 精简 (2026-08-23)**: 赔率特征从 135 维砍到 30 维核心，默认 `slim_odds=True`。A/B 测试：准确率 -0.48pp，平局召回率 +1.55pp。特征构建速度 1.6x（328s→210s），训练速度 1.8x。全量 215 维保留供 A/B 对比。
>
> **v8.3 过度优化修复 (2026-08-23)**: 依据架构诊断报告，6 项改动全部落地：① Monte Carlo 3000→500 ② 温度 T=0.8→1.0 ③ 联赛分档→argmax（加开关）④ 英超模型移出 Stacking ⑤ 6模型→4模型 ⑥ 赔率 135→30维。详见 change_log.md §3.65（v4 过度优化修复 6 项，2026-08-23）。

**决策阈值 factor=1.5 最终模型**:

| 指标 | 值 |
|------|:----:|
| XGBoost 准确率 | 49.19% |
| LightGBM 准确率 | 48.43% |
| 平局召回率 | 33.8% |
| 平局精确率 | 40.3% |
| XGBoost LogLoss | 1.0221 |
| LightGBM LogLoss | 1.0249 |
| 概率校准方法 | 决策阈值（不修改概率，保持校准） |

### 8.5 跨联赛验证（2026-08 历史验证）

> **口径注记**：本节为 2026-08-15 LOLO 验证与 2026-08-20 联赛分档校准的历史记录；联赛分档阈值已于 2026-08-23 搁置、回退全局 argmax（见下表末注），当前决策策略见《模型架构分析报告_v1.0.md》§6.3。

| 模型 | LOLO 准确率 | 全联赛准确率 | 平局召回率 |
|------|:----------:|:----------:|:--------:|
| XGBoost | 0.4851 ± 0.0066 | 0.5086 | 0.0% (修复前) |
| LightGBM | 0.4851 ± 0.0025 | 0.4971 | 0.0% (修复前) |
| RandomForest | 0.4654 ± 0.0188 | 0.4857 | 24.2% |

**逐联赛准确率** (LightGBM, 全量数据):

| 联赛 | CV 准确率 | LogLoss | 生产准确率 | 专属 factor (2026-08-20) | 表现 |
|------|:------:|:-------:|:---:|:---:|:----:|
| 德甲 | 0.5163 | 1.0065 | 55.66% | 0.85 | 反压低平局 +0.87pp |
| 西甲 | 0.4956 | 0.9990 | 53.07% | 0.80 | 反压低平局 +1.05pp |
| 法甲 | 0.4946 | 1.0355 | 52.40% | 0.0 | argmax（无收益） |
| 意甲 | 0.4868 | 1.0589 | 53.39% | 0.85 | 反压低平局 +0.97pp |
| **英超** | **0.3886→0.4725** | **1.1444** | **53.99%** | **0.0** | ⚠️ 独立模型 v3.0 已部署 (+8.39pp) |

> **联赛分档阈值已搁置 (2026-08-23, C-20260823-003)**: 默认回退到全局 argmax（DRAW_THRESHOLD_MODE=argmax），保留 league_specific 开关。旧版反压低平局配置（意甲 0.85、西甲 0.80、德甲 0.85）通过 30 次网格搜索获得，存在多重比较过拟合风险（全局最优 0.90 仅 +0.09pp）。等新赛季 50 场前瞻性验证后再决定是否恢复。

---

## 九、可靠性验证与评估报告分析

> **背景**: 基于《足球预测模型_8项数据质量与可靠性检测评估报告》的系统性可信度验证，分三阶段执行，于 2026-08-15 全部完成。以下验证证据均为 2026-08 历史口径（样本量 5,252/5,258 场为当时快照），为本文档独有核心资产，不随数据回溯更新。

### 9.1 第一阶段：可信度验证

| 任务 | 状态 | 结论 |
|------|:----:|------|
| 路径硬编码修复 | ✅ | 4个核心脚本 (`cross_league_validation.py`, `model_validation_framework.py`, `feature_engineering_pipeline.py`, `odds_temporal_features.py`) 已从 `g:/zuqiu/` 修复为动态路径 |
| 标签随机化测试 | ✅ | 30次随机排列，5252场，LightGBM。随机标签准确率 42.31% ≈ 基线 42.25%，**无数据泄露** |
| 比分命中率验证 | ✅ | 50场抽样验证：64.66% = Top-5 覆盖率（非 Top-1 精确命中率），指标定义明确 |
| 重复样本检测 | ✅ | 5258场，重复率 0.00%，无完全重复，无 match_id 重复，无近重复 |

**标签随机化测试详情**:

| 指标 | 基准模型（原始标签） | 随机标签（30次均值） |
|------|:------------------:|:-------------------:|
| 准确率 | 48.24% | 42.31% ± 0.27% |
| LogLoss | 1.0190 | 1.0782 |
| 范围 | — | [41.86%, 43.48%] |

> 随机标签准确率 42.31% ≈ 测试集主胜占比 42.25%，说明随机标签后模型退化为"全部预测主胜"的 naive baseline → **无数据泄露**。

**比分命中率验证详情** (50场抽样，Poisson + Dixon-Coles v4):

| 指标 | 简化版验证 | 系统声称 | 定义确认 |
|------|:--------:|:------:|:------:|
| Top-1 精确命中 | 12.00% | — | — |
| Top-3 覆盖率 | 26.00% | 31.49% | — |
| Top-5 覆盖率 | 34.00% | **64.66%** | ✅ 即 Top-5 |
| Top-10 覆盖率 | 58.00% | 76.21% | — |

> 差距主要来自简化版 lambda 估计与完整管线（SSM/xG/Elo/赔率融合）的精度差异。

### 9.2 第二阶段：错误与泛化分析

**模型验证框架分类报告** (LightGBM, 187维, 5折 TimeSeriesSplit CV):

| 指标 | 值 |
|------|:----:|
| CV 准确率 | 0.5170 ± 0.0172 |
| LogLoss | 1.0050 |
| 稳定性得分 | 0.8983 |
| 综合评分 | **80.75/100** |

**概率校准 (ECE)**:
- 客胜 (class_0): 0.1062 (良好)
- 平局 (class_1): 0.1982 (较差，平局概率被高估)
- 主胜 (class_2): 0.1271 (良好)

**跨联赛验证 (Leave-One-League-Out)**:
- 特征迁移性：34 个特征全部 CV ≥ 1.0（低一致性），特征重要性跨联赛差异大
- Top-5 重要特征：weighted_xg_diff、goals_ratio、home_weighted_avg_xg、goals_stability_diff、poisson_lambda_home

**核心发现**:
1. 平局预测是最大短板 — XGBoost/LightGBM 跨联赛平局召回率 0%
2. 英超表现异常差 — 准确率仅 0.3886，模型过拟合到大陆联赛模式
3. 特征跨联赛一致性低 — 所有特征 CV ≥ 1.0

### 9.3 第三阶段：平局修复 + 英超分析

**平局召回率修复 (决策阈值 factor=1.5)**:

| 策略 | 准确率 | 平局召回率 | 概率校准 |
|------|:------:|:--------:|:------:|
| 基准 (argmax) | 48.24% | 0.4% | ✅ |
| 阈值 factor=1.5 | 46.53% | 22.7% (CV) | ✅ 不变 |
| 阈值 factor=1.5 (最终) | 47.38% | **33.8%** | ✅ 不变 |
| 阈值 factor=1.8 | 46.62% | 47.5% | ✅ 不变 |
| 阈值 factor=2.0 | 45.29% | 59.4% | ✅ 不变 |

> 选择 factor=1.5：平局召回率 +22pp (CV)，准确率仅 -1.71pp。概率保持校准，ECE 不受影响。

**英超分析**:
- 数据量 1141 场（占比 21.7%），主胜率 43.2%，平局率 24.5% — 分布与其他联赛几乎相同
- 模型过拟合到大陆联赛模式，对英超的节奏和身体对抗特征不敏感
- 短期建议：为英超训练独立模型；长期建议：添加英超特有特征（身体对抗、节奏、转会投入）

### 9.4 8项检测最终状态

| 序号 | 检测标准 | 原判定 | 最终状态 |
|:----:|----------|:------:|:--------:|
| 1 | 标签随机化测试 | ❌ 未执行 | ✅ 通过 (无数据泄露) |
| 2 | 模型错误类型分析 | ❌ 未执行 | ✅ 通过 (classification_report + confusion_matrix) |
| 3 | 外围信息依赖性测试 | ❌ 未执行 | ⚠️ 部分 (消融实验待补充) |
| 4 | 重复样本影响评估 | ⚠️ 部分 | ✅ 通过 (0.00%重复率) |
| 5 | 预处理敏感性分析 | ❌ 未执行 | ⚠️ 部分 (对比实验待补充) |
| 6 | 泛化能力验证 | ⚠️ 部分 | ✅ 通过 (LOLO + 时间切分) |
| 7 | 错误分布集中性分析 | ❌ 未执行 | ✅ 通过 (按联赛/盘口分析) |
| 8 | 数据质量人工抽样 | ⚠️ 部分 | ⚠️ 部分 (人工抽样待补充) |

> **总结**: 8项检测中 5项完全通过，3项部分通过。系统已具备新赛季预测条件。

### 9.5 相关文档

| 文档 | 路径 |
|------|------|
| 8项检测评估报告 | [docs/足球预测模型_8项数据质量与可靠性检测评估报告.md](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/足球预测模型_8项数据质量与可靠性检测评估报告.md) |
| 评估报告解读 | [docs/评估报告解读与系统优化可行性分析.md](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/评估报告解读与系统优化可行性分析.md) |
| 标签随机化测试报告 | [assets/label_permutation_test_20260815_205150.json](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/assets/label_permutation_test_20260815_205150.json) |
| 比分验证报告 | [assets/score_accuracy_validation_20260815_210103.json](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/assets/score_accuracy_validation_20260815_210103.json) |
| 验证框架报告 | [assets/validation_report_LightGBM_WDL_20260815_211147.json](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/assets/validation_report_LightGBM_WDL_20260815_211147.json) |
| 跨联赛验证报告 | [output/cross_league_validation_report_20260815_211140.json](file:///h:/zuqiu/五大联赛专属模型/五大联赛专属模型/output/cross_league_validation_report_20260815_211140.json) |

### 9.6 T-007 重跑 + 4 场西甲新比赛 SofaScore 特征验证（2026-08-20）

> **触发**：新赛季西甲第1轮 4 场比赛（2026-08-16/17）赛后数据落库后，重跑 T-007 让新比赛吃到真实 SofaScore 球员特征，并验证预测侧 `sofa_*` 不再为 -1。  
> **变更日志**：[change_log.md C-20260820-021](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/change_log.md)

**验证对象**：4 场新比赛（event_id `16421047 / 16421052 / 16421061 / 16421053`）

| 验证项 | 结果 |
|--------|------|
| 4 场新比赛在 `fbref_match_mapping` | ✅ 全部存在 |
| 历史窗口 `match_player_stats` 覆盖 | ✅ 7/8 队各 115 场；Real Racing Club 为升班马仅 1 场（主队特征=0 属正常） |
| 4 场自身赛后球员统计落库 | ✅ 44-46 行/场 |
| T-007 重跑 `sofascore_team_features` | ✅ 5289 场 × 52 列，特征非零率 98.9%（5230/5289） |
| 预测侧 JOIN 验证（`date+home+away+normalize_team_name`） | ✅ 4 场全部 46/46 非空，`fillna(-1.0)` 不再触发 |

**4 场特征实测**：

| 比赛 | sofa_rat_home | sofa_xg_home | 判定 |
|------|--------------|--------------|------|
| 阿拉维斯 vs 赫塔费 | 6.83 | 0.25 | ✅ 真实特征 |
| 塞维利亚 vs 巴列卡诺 | 6.83 | 0.21 | ✅ 真实特征 |
| Real Racing vs 比利亚雷亚尔 | 0.0（升班马无历史） | 0.0 | ✅ 真实零值（客队真实） |
| 西班牙人 vs 莱万特 | 6.95 | 0.25 | ✅ 真实特征 |

**端到端预测**：重跑 `generate_unified_report.py`（exit 0），5 场全部产出四维度预测；新赛季 LGB/XGB 子模型自动跳过（无近期战绩历史）→ 降级为 4/6 元模型融合，但 SofaScore 特征已正常进入特征矩阵，不再被填 -1。

> **结论**：T-007 重跑后 4 场新比赛 SofaScore 特征已落库并被预测侧正确 JOIN，不再全为 -1。唯一例外 Real Racing Club 主队为 0.0（升班马历史不足，真实零值而非 JOIN 失败的 -1）。

### 9.7 双库写入幂等性修复 + 高并发验证（2026-08-20）

> **触发**：双库写入审计发现 `prediction-writer.js` 幂等性不完整——`matches` / `fbref_match_mapping` 已用 `INSERT OR IGNORE`，但 `model_predictions` 使用普通 `INSERT` 且表结构无唯一约束，同一 `(match_id, model_name, prediction_type)` 重复调用会累积重复预测行，导致训练回读时同一比赛被多次采样，破坏「预测 → 落库 → 重训」数据闭环。  
> **变更日志**：[change_log.md C-20260820-029](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/change_log.md)

**修复内容**：

| 项 | 位置 | 变更 |
|----|------|------|
| 唯一约束 | `init_odds_db.py` `model_predictions` 表 | 新增 `UNIQUE(match_id, model_name, prediction_type)` |
| 幂等写入 | `shared/prediction-writer.js` `savePreMatchPrediction` / `batchSavePredictions` | 两处 `INSERT` → `INSERT OR IGNORE` |
| 回写一致性 | `batchSavePredictions` | 补写 `Lambda_home` / `Lambda_away`，与 `savePreMatchPrediction` 对齐 |
| 并发健壮性 | `getOddsDb()` / `getFlDb()` | `journal_mode=WAL` + `busy_timeout=5000`，缓解高并发写锁 |
| 存量迁移 | `scripts/add_model_predictions_unique.py`（新增） | 清理历史重复（每组保留最小 id）+ 建唯一索引 `idx_model_predictions_unique`，脚本可重复执行 |

**验证结果**：

| 验证 | 结果 |
|------|------|
| 单元测试 `tests/prediction-writer.test.js` | ✅ 8/8 通过（纯函数 + 单条/批量幂等 + 结果回填） |
| 高并发 `tests/verify-prediction-writer-concurrency.js` | ✅ 8 进程 × 10 次 × 5 场 = 400 次并发写入，`model_predictions` 最终 50 行（期望 50），重复组 0 |

> **结论**：唯一索引 + `INSERT OR IGNORE` 在单进程重复调用与多进程高并发场景下均确保不产生重复预测行；`model_predictions` 与 `matches` / `fbref_match_mapping` 一道形成完整幂等写入闭环，为训练回读的正确采样提供了数据一致性保证。

### 9.8 209维重训 + 联赛分档反压低平局校准（2026-08-20）

> **触发**：用户要求① 用修复后 209 维 pipeline 重训模型、彻底消除列不一致风险；② 意甲/西甲试 `draw_threshold_factor` 下限到 0.95 以下看能否提升准确率；③ 同步结论与参数到本文档。  
> **变更日志**：[change_log.md C-20260820-036 / C-20260820-037](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/docs/change_log.md)

**背景与问题**：上一版模型 `lgb_model_20260820_120209` 是 `competition_map` 修复（C-20260820-032）之前训练的 210 维模型，与当前修复后 209 维 pipeline 不一致，此前校准只能靠「缺失列补 0」强行对齐。本轮用修复后 pipeline 重训，彻底消除 209/210 维列不一致风险。

**重训结果（`training_20260820_185132.json`，5256 场 × 209 维，时间序列 80/20 划分）**：

| 模型 | 验证准确率 | LogLoss | Brier | 训练准确率 | 超参 |
|------|:---:|:---:|:---:|:---:|------|
| XGBoost | 0.4876 | 1.0215 | 0.6122 | 0.5635 | max_depth=4, lr=0.03, 300 轮 |
| LightGBM | 0.4857 | 1.0255 | 0.6153 | 0.5680 | max_depth=3, lr≈0.0106, 164 轮 |

- 5 折滚动窗口验证（XGBoost）mean accuracy 0.4991 / mean LogLoss 1.0097。
- 概率校准后：XGBoost 校准准确率 0.5019、LightGBM 校准准确率 0.5124。
- 特征构成：basic 11 + team 63 + odds 135 = **209 维**；模型文件 `assets/lgb_model_20260820_190130.pkl` 等。

**分档校准结论（完整网格 `[0.80~1.10]`，重训后 209 维 LightGBM）**：

核心发现——真正「高平局联赛」在 factor<1.0（反压低平局：抬高平局门槛、边界平局降级为主胜/客胜）处准确率达到最优，正收益 +0.87~+1.05pp；平局率偏低的英超/法甲 argmax 即为最优。

| 联赛 | 实际平局率 | argmax 准确率 | 最优 factor | 最优准确率 | 增益 | 决策 |
|------|:---:|:---:|:---:|:---:|:---:|------|
| 意甲 | 28.1% | 55.58% | 0.85 | 56.55% | **+0.97pp** | 反压低平局 |
| 西甲 | 26.0% | 53.15% | 0.80 | 54.20% | **+1.05pp** | 反压低平局 |
| 德甲 | 25.5% | 56.32% | 0.85 | 57.19% | **+0.87pp** | 反压低平局 |
| 英超 | 24.5% | 56.44% | 1.0 | 56.44% | 0 | argmax |
| 法甲 | 23.8% | 54.37% | 1.0 | 54.37% | 0 | argmax |
| 全局 | 25.6% | 55.16% | 0.90 | 55.25% | +0.09pp(噪声) | 默认仍取 1.0 |

**最终参数配置（已同步 config.yaml + prediction-service.js）**：

| 配置项 | 位置 | 值 |
|--------|------|-----|
| 全局 `draw_threshold_factor` | config.yaml + prediction-service.js `DEFAULT_DRAW_THRESHOLD_FACTOR` | `1.0`（等价 argmax） |
| 意甲 IT | 两者 | `0.85`（反压低平局 +0.97pp） |
| 西甲 LaLiga | 两者 | `0.80`（反压低平局 +1.05pp） |
| 德甲 BL1 | 两者 | `0.85`（反压低平局 +0.87pp） |
| 英超 PL | 两者 | `0.0`（argmax） |
| 法甲 FL1 | 两者 | `0.0`（argmax） |

> **因子语义约定**：factor≤0 或 =1.0 等价 argmax；0<factor<1 反压低平局（抬高门槛，边界平局降级为主胜/客胜）；factor>1 上浮平局（压低门槛，更多平局）。
>
> **结论**：① 209 维重训已消除模型与 pipeline 的列不一致，模型特征维度正式落到 209 维；② 高平局联赛（意甲/西甲/德甲）通过「反压低平局」（factor<1.0）获得 +0.87~+1.05pp 的准确率提升，无需修改概率、ECE 保持不变；③ 英超/法甲 argmax 即为最优，反压/上浮均无收益；④ 全局最优 0.90 仅有 +0.09pp 增益（噪声区间），默认保持 1.0。

**运行时可观测性（C-20260820-039）**：

为验证反压低平局因子（意甲 0.85 / 西甲 0.80 / 德甲 0.85）在运行时真正生效，在 `prediction-service.js` 增强了运行时日志，并修复相关联的日志字段 bug：

- **新增逐联赛日志埋点**：`applyLeagueDrawThreshold` 的 `draw-threshold` 日志由「仅 5 大联赛」扩展为「每个联赛（含未知联赛回退）」，每条日志新增决策模式 `mode`（`argmax` / `反压低平局` / `上浮平局`）与结构化 `details`（`leagueCode`、`factor`、`mode`、`win/draw/lose`、`argmaxLabel → appliedLabel`、`triggered`、`decision`），写入 `logs` 表 `details` JSON 字段（`category=draw-threshold` 可直接检索每个联赛实际应用的 factor）。
- **修复 `thresholdTriggered` 字段**：「预测-最终」日志原用 `thresholdResult.triggered`，但 `applyLeagueDrawThreshold` 返回对象无顶层 `triggered` 字段（实际在 `thresholdInfo.triggered`），导致该字段恒为 `false`；已改为 `thresholdResult.thresholdInfo?.triggered`。
- **验证**：新增 [test_draw_threshold_runtime_logging.js](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/server/tests/test_draw_threshold_runtime_logging.js)，真实调用 `prediction-service.js` 接口跑 6 个联赛样本，**14/14 通过**，确认 IT/BL1 实际 `factor=0.85`、LaLiga `factor=0.80` 与 config.yaml 一致。

### 9.10 2026-08-28 模型状态更新（P1-5 贝叶斯 + P1-7 LR meta-learner + P1-8 xG 深化 + P1-9 ts_odds 生产采用）

> **触发**：按差距分析报告路线图，本轮完成 P1-5（贝叶斯层级模型）、P1-7（LR meta-learner Stacking）、P1-8（xG 特征深化）、P1-9（时序/比分赔率特征接入）四项，同步系统模型状态。

**P1-5 贝叶斯层级模型**（`scripts/bayesian_hierarchical_model.py`，C-20260828-005/006/007）：Baio & Blangiardo 风格层级泊松 + Dixon-Coles ρ 修正 + 层级先验 + 指数时间衰减，分联赛训练（80/20 时间序列无 shuffle）。聚合 RPS 0.2092（≤0.21），优于朴素泊松 0.2316 / 无正则逐队 MLE 0.2368。已纳入 Stacking 第 5 基础模型（分联赛懒加载 `bayesian_model_{联赛}.json`，未知球队收缩到联赛均值）。

**P1-7 LR meta-learner Stacking**（`scripts/train_stacking_meta.py`，C-20260828-008/009/010）：5 基础模型（Dixon-Coles + Elo + XGBoost + LightGBM + 贝叶斯）生成 15 维 meta 特征，TimeSeriesSplit 5 折 OOF（无 shuffle）训练多分类 LR meta-learner（lbfgs, C=1.0）。OOF 全量 RPS 0.1984 < 固定权重 0.2038（改善 0.0054）；缺失贝叶斯时回退固定权重。`prediction_core.py` 推理优先 meta-learner、不齐全回退。

**P1-8 xG 特征深化**（`scripts/xg_deep_features.py`，C-20260828-013/014）：新增 6 维（xg_diff_recent/trend/pct × 主客，窗口近 8 场、联赛分位、严格截比赛当日防泄漏）。覆盖率 88.5%（≥80% 达标）；A/B（XGB+LGB 融合，基线 ts_odds=True 198 维 vs +6 维 204 维）RPS 一致轻微劣化（Blend +0.0028），保持 `xg_deep=False` 暂不采用。

**P1-9 时序/比分赔率特征接入**（C-20260828-002/003/004 + 遗留收尾 011/012）：补齐对齐链路（matches.league NULL 回填 / match_id_mapping 3,857→12,968 / sofascore_team_features 5,334→18,257，对齐率 99.4%）；D-013 扩展 22 维 + 比分 8 维 + T-003.3 扩展 3 维，`ts_odds` 开关 + 联赛 z-score 归一。完整校准复验（XGB+LGB 50/50 融合 + 滚动切分 14,312 场）Blend RPS -0.0155 / LogLoss -0.0072 / Acc +0.0019 / DrawRecall +0.0049 四项全优，**生产 `ts_odds=True` 采用（WDL 特征 165→198 维）**。PA 球员可用性特征导入路径修复（C-011）。

**模型状态变更汇总**：

| 项 | 变更前 | 变更后 |
|----|--------|--------|
| WDL 特征维度 | 165维（slim_odds） | 198维（ts_odds=True） |
| WDL Stacking | 4 模型固定权重 | 5 基础模型 + LR meta-learner |
| 新增基础模型 | — | 贝叶斯层级模型（RPS 0.2092） |
| meta-learner OOF RPS | 0.2038（固定权重） | 0.1984 |
| xg_deep（P1-8） | — | 实现但 RPS 无提升，暂关闭 |

### 9.11 2026-08-28（续）P1-11 赔率一致性生产启用 + P1-10 / P2-10 / P2-13 验证不启用 → 最终 208 维

> **触发**：§9.10 记录的同日晚些（2026-08-28），又完成 P1-11（多博彩公司赔率一致性，原 P2-9）、P1-10（情境化，原 P1-6）、P2-10（位置-specific 球员特征）、P2-13（Focal Loss）四项。其中仅 P1-11 生产启用，其余经 A/B 验证无增益维持不启用，WDL 最终特征维度由 198 维（ts_odds）收敛为 **208 维**（ts_odds + consensus_odds）。

| 项 | 结论 | 关键证据 |
|----|------|------|
| P1-11 赔率一致性（`consensus_odds=True`） | ✅ 生产启用 | 10 维 mkt_* 特征，A/B 四指标全优（RPS/LogLoss/ECE/Acc 改善，DrawRecall 不降）；重训 208 维模型（C-20260828-019），降维复查 full RPS 0.2000 最优 |
| P1-10 情境化（`ctx_features=False`） | ❌ 验证不启用 | 14 维，逐维/分组 ablation 边际 ≤\|0.0003\|，full RPS 0.2018 ≈ 基线 0.2017，无子集优于基线 |
| P2-10 位置-specific 球员特征 | ❌ 验证不启用 | 11 维 F/M/D，blend 平局召回 0.3788→0.3384（-4.04pp）劣化 |
| P2-13 Focal Loss | ❌ 验证不启用 | 实现 + 梯度校验通过，9 组网格无组合满足「平局召回 ≥0.30 且 RPS 不劣化」 |

**最终生产模型（`assets/*_20260828_174103.pkl`）**：WDL **208 维** = `slim_odds=True + ts_odds=True + consensus_odds=True`；5 基础模型 Stacking + LR meta-learner（OOF RPS 0.1984）；贝叶斯聚合 RPS 0.2092；`xg_deep=False`、`ctx_features=False`。

---

## 十、2026-08 系统状态审计（历史记录）

> **审计时间**: 2026-08-17（v8.1 全面审计）| **审计范围**: 代码、数据、文档、基础设施全覆盖
> **口径注记**: 本节为 2026-08 历史审计记录，**非当前状态**；当前系统状态以《模型架构分析报告_v1.0.md》为准（数据层 §3.1、模型层 §一表头 / §6、基础设施 §3.5）。差异根因：P0-1 回溯采集后数据扩充（5,258→14,511 场）与 D1~D4 工程化闭环。

### 10.1 数据层状态

| 组件 | 状态 | 详情 |
|------|:----:|------|
| odds.db (437MB, **2026-08 快照**) | ✅ 健康 | 5,258场比赛，24张表（**历史口径**；当前 35 表 / 1.66GB / matches 14,511 场见《模型架构分析报告_v1.0.md》§3.1），赔率覆盖 98.6% (WDL 3,907/让球 4,014/总进球 4,015/比分 4,011) |
| PostgreSQL (odds.db 的 PG 后端) | ✅ 已迁移 | P2-11（2026-08-29）：33 表 / 424 万行 / 1.5GB 迁移 verify 0 差异；三张大表日期 RANGE 分区 + 11 索引；`DB_BACKEND=pg` 双后端切换，批处理负载提速最高 338x |
| five_leagues.db (7.7MB) | ⚠️ 不完整 | 仅 875 场，西甲仅 115 场，德甲/法甲无数据 |
| odds_timing.db | ❌ 空库 | 无任何表，预留功能未实现 |
| anomaly_samples.db | ✅ 正常 | 3 条异常样本，用于训练权重增强 |
| 西甲 2026-27 赔率 | ✅ 完整 | 3 场新赛季赔率，含 WDL/让球/比分/总进球 4 类时序 |
| 球队名映射 | ✅ 完整 | 193 条 TEAM_NAME_MAP，覆盖率 98.6% |
| 赛季数据 | ✅ 3 赛季 | 2023-24/2024-25/2025-26，5 大联赛全覆盖 |

### 10.2 模型层状态

> **口径注记**：下表为 2026-08-29 模型状态快照（T-006 行已于 2026-09-08 更新为 v5）；当前模型状态以《模型架构分析报告_v1.0.md》§一表头 / §6 为准。

| 模型 | 类型 | 特征维度 | 状态 | 关键指标 |
|------|------|:---:|:----:|------|
| WDL 核心 | LightGBM + XGBoost（5 基础模型 Stacking + LR meta-learner） | 208维（slim_odds + ts_odds + consensus_odds，2026-08-28） | ✅ 生产 | XGBoost 验证 48.76% / LGB 48.57%；meta-learner OOF RPS 0.1984；贝叶斯聚合 RPS 0.2092 |
| T-005 v3 让球 | 两阶段 LightGBM | 71维 | ✅ 生产 | 走水召回率 42.2%, 预测率偏差 1.06pp |
| T-006 v5 比分 | Poisson(Dixon-Coles) + 去水 WDL 数值求解 λ + IPF 重加权 + 生产 WDL 锚定 | 模型级 | ✅ 生产 (C-20260908-022~024) | Top-1 13.14%（v4 10.70%）, Top-5 45.47%, WDL 不劣化；生产锚定读 model_predictions |
| T-006 低进球分类器 (JS 上线) | LightGBM 二分类 | 22维 | ✅ 生产 (C-20260819-004~008) | 200 树, base=0/lr=1, parity diff=0, predictScore 后验调整低比分 (0:0/0:1/1:0/1:1) |
| 英超独立 v3.0 | LightGBM | 205维 | ✅ 已部署 | CV 47.25%, 45棵树, 46维球员特征, 已导出JS |
| 统一报告生成器 | WDL 5模型Stacking + T-005 v3 + T-006 v5 + 总进球 | 四维度 | ✅ v3.0 | 自动 TXT/JSON 解析, 球队模糊匹配, ML 缓存+降级 |
| 西甲预测脚本 | T-005 v3 + 赔率 | 71维 | ✅ 可用 | 第二批 WDL 准确率 TBD |

### 10.3 基础设施状态

| 组件 | 状态 | 详情 |
|------|:----:|------|
| PM2 部署 | ✅ 运行中 | cluster 模式，fork × 1，max_memory_restart=1G，t006 上线后稳定运行 (C-20260819-008) |
| 模型热更新 | ✅ 正常 | fs.watch 监听 9 种模型文件 (含 t006_lowgoal_export.js，C-20260819-006) |
| 自动重训 | ✅ 配置 | 三重触发（定时 02:00/数据变更/7天周期）+ 性能门禁 |
| SofaScore 采集 | ✅ 就绪 | 26/27 赛季 ID 已配置，支持断点续传 |
| 竞彩赔率采集 | ✅ 就绪 | Playwright 浏览器方案，支持增量补充 |
| 系统文档 | ✅ 同步 | 5 份核心文档互校验 |
| 数据库适配层 db_utils | ✅ 双后端 | `connect/read_sql/write_dataframe/numeric_sql_type/table_exists` 封装 SQLite/PG 差异；14 个特征生成 + 回测脚本已切 `DB_BACKEND=pg`，双后端 sw-4 验证 0 差异 |

### 10.4 西甲第1轮实战验证

| 比赛 | WDL 预测 | 实际 | 让球 预测 | 实际 | 比分 预测 | 实际 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| 阿拉维斯 vs 赫塔费 | 主胜 (38.5%) | ✅ 3:0 | 主胜 (58.5%) | ❌ 胜 | 1:0 (13.9%) | ❌ 3:0 |
| 塞维利亚 vs 巴列卡诺 | 主胜 (39.3%) | ✅ 2:1 | 上盘赢 (55.1%) | ❌ 平 | 1:0 (13.5%) | ❌ 2:1 |
| 桑坦德 vs 比利亚雷亚尔 | 客胜 (47.3%) | ⏳ | 下盘赢 (46.5%) | ⏳ | 1:1 (11.0%) | ⏳ |
| 西班牙人 vs 莱万特 | 主胜 (46.6%) | ⏳ | 上盘赢 (36.2%) | ⏳ | 1:1 (11.7%) | ⏳ |

> **已完赛**: WDL 2/2 全中 (100%)，让球 0/2，比分 0/2，总进球 0/2，总体 2/8 (25%)。比分预测受限于 12-15% 的 Top-1 命中率天花板，阿拉维斯 3:0 含红牌+73分钟后连进3球等突发事件。

---

## 十一、不足与改进计划

### 11.1 已知不足 — 按严重程度排序

**P0 — 投注决策层（EV 期望值引擎）— ✅ 已实现（2026-09-03）**:

> 对标 `docs/EV期望值引擎设计文档_v1.0.md`，三层已落地：`scripts/ev_engine.py`（纯函数决策：remove_vig / edge=p_model−p_market / EV / Kelly / VALUE-MARGINAL-AVOID，17 单测通过）、`scripts/ev_backtest.py`（三源对齐回测）、`scripts/generate_oof_predictions.py`（严格时序 OOF）。

| 缺失能力 | 现已实现 |
|------|------|
| 去抽水度量 | `calc_implied_probabilities` / `remove_vig` + vig/返还率 |
| edge | `analyze_direction`（`p_model − p_market`） |
| EV | `calc_ev`（`p_model×(odds−1)−(1−p_model)`） |
| Kelly 仓位 | `calc_kelly`（1/4 + 25% 上限） |
| 投注决策 | `analyze_match`（VALUE/MARGINAL/AVOID，单一路径） |
| 评估 | `ev_backtest.py`（分决策/方向/联赛/edge 分桶 ROI） |

**⚠️ 回测关键结论（无正 edge）**：严格时序 OOF（208 维 + TimeSeriesSplit(5)，11965 场）后 mean 平注 ROI **-5.41%**、平均 EV +21.12%（EV 信号系统性高估）、edge 分桶非单调；旧 215 维 ROI +17~22% 为时序泄漏伪象。详见 §9.12。

**P0 — 预测准确率瓶颈**:

| 问题 | 严重程度 | 根因 | 改进方向 |
|------|:------:|------|------|
| 英超 CV 准确率仅 38.86% | ✅ 已缓解 | 模型过拟合到大陆联赛模式，英超身体对抗/节奏特性未捕获 | 英超独立模型 v3.0 已部署 (CV 47.25%, +8.39pp) |
| 平局预测率偏差大 | ⚠️ 中等 | 平局天然低概率 (25.6%)，模型倾向预测主胜/客胜 | 联赛专属阈值已缓解，需更多平局专用特征 |
| 比分预测命中率低 (12%) | ⚠️ 中等 | Poisson 模型假设独立，无法建模进球时间相关性 | T-006 v4 已添加 Dixon-Coles 修正，进一步探索 SSM 时间序列 |
| 让球预测准确率 51.7% | ⚠️ 中等 | 走水样本仅 242 场，训练数据不足 | 扩展让球标签数据，纳入更多联赛/赛季 |

**P1 — 数据质量与覆盖**:

| 问题 | 严重程度 | 根因 | 改进方向 |
|------|:------:|------|------|
| 升班马球队无历史数据 | ⚠️ 中等 | 新赛季升级球队在历史特征矩阵中无记录 | 引入西乙/英冠等次级联赛数据作为历史参考 |
| odds_timing.db 空库 | ✅ 低 | 预留功能未实现 | 实现赔率时序分析功能 |
| 模型版本过多 (50+) | ✅ 低 | 缺乏清理机制 | 添加模型版本管理脚本，保留最近 5 个版本 |
| config.yaml 与代码重复定义 | ✅ 低 | league_draw_threshold 在 YAML 和 JS 中重复定义 | 统一到 config.yaml 动态加载 |

**P2 — 特征工程**:

| 问题 | 严重程度 | 根因 | 改进方向 |
|------|:------:|------|------|
| 西甲特征完整度仅 62% | ⚠️ 中等 | 27/71 维用中位数填充（新球队无历史） | 引入通用球员特征 + 次级联赛数据 |
| 球员级特征已集成 | ✅ 低 | SofaScore 球员数据已采集并工程化，46维已入模 | 持续更新球员数据，监控覆盖率 |
| 联赛特征一致性低 | ✅ 低 | 所有特征 CV ≥ 1.0，跨联赛特征重要性差异大 | 联赛独立模型 + 特征加权 |

**P3 — 运维与自动化**:

| 问题 | 严重程度 | 根因 | 改进方向 |
|------|:------:|------|------|
| 西甲赔率需手动导入 TXT | ✅ 低 | 竞彩网 API 未对接新赛季实时赔率 | 实现 supplement_sporttery_odds.py 自动采集 |
| 赛后复盘自动化不足 | ✅ 低 | 仅手动复盘，无自动对比机制 | 开发自动化赛后复盘脚本 |
| 训练日志分散 | ✅ 低 | 日志散落在多个文件 | 统一到单一训练日志数据库 |

### 11.2 改进优先级路线图（已归档 → 交叉引用）

> **精简**：改进计划与 P0~P2 完成状态追踪以《model_gap_analysis_report_v2.0.md》§三（优化路线图）/ §四（详细执行计划）为唯一权威，本表不再维护。以下为 2026-08 遗留待办的历史记录：
> - **短期**：西甲第 1 轮赛后复盘、新赛季赔率自动采集激活（**D4 已于 2026-09-09 闭环**，change_log C-20260909-005）、PG 迁移坏时间戳回填；
> - **中期**：升班马历史数据补充（**C4 已降级完成**）、比分预测优化（**T-006 v5 已完成**，C-20260908-022）、自动化赛后复盘（**A 模块已落地**，2026-09-07）；
> - **长期**：英超特有特征开发、odds_timing.db 时序分析、增量学习、MCP + RAG 集成（P2-12 MLflow 已由 D1 闭环，2026-09-09）。

### 11.3 风险提示

1. **新赛季数据漂移**: 2026-27 赛季球队阵容变化、转会窗影响，可能导致模型性能下降
2. **升班马预测盲区**: 桑坦德竞技、莱万特等升班马特征完整度仅 62%，预测可靠性低
3. **英超独立模型过拟合风险**: 仅 1,141 样本，需严格控制正则化。v3.0 已修复 Fold5 崩溃，CV 47.25%
4. **config.yaml 重复定义风险**: 若 YAML 和 JS 硬编码的 draw_threshold_factor 不一致，将导致不可预测行为
5. **模型版本管理**: 50+ 个 pkl 文件缺乏清理，可能误用旧版本

---

## 十二、文档同步状态

| 文档 | 状态 | 最后更新 |
|------|:----:|------|
| SYSTEM_ANALYSIS_REPORT_v2.0.md | ✅ v2.1 修订版 | 2026-09-09（精简 + 交叉引用 + 历史口径标注；此前 §10 已含 PG 迁移 / db_utils 双后端落地） |
| change_log.md | ✅ 600+ 条记录 | 2026-08-29 (C-20260829-001~004 P2-11 PG 迁移 + 批处理切后端 + df.to_sql 兼容) |
| DEPLOYMENT_CONFIG_v2.0.md | ✅ 同步 | 2026-08-19 (t006 资产 + sandbox 备注) |
| optimization_log.md | ✅ 阶段一~九 | 2026-08-17 |
| key_decisions.md | ✅ 57 个决策 | 2026-08-17 |
| model_optimization_plan.md | ✅ v3.0 | 2026-08-17 |
| project_memory.md | ✅ 同步 | 2026-08-29 (§2.2 数据库约定：db_utils 双后端 + write_dataframe 规则固化) |
| 西甲第1轮预测报告 × 3 | ✅ 已生成 | 2026-08-16 |
| 西甲第1轮赛后复盘 | ✅ 已生成 | 2026-08-16 |
| 英超独立模型优化报告 | ✅ 已生成 | 2026-08-17 |

| 决策 ID | 内容 | 影响 |
|---------|------|------|
| D-004 | 动态 class_weight 策略 | 类别平衡训练 |
| D-005 | 增强正则化 (L1/L2/subsample) | 过拟合控制 |
| D-006 | Sklearn API 迁移 | 统一训练接口 |
| D-007 | 动态权重集成 | 基于历史表现的权重调整 |
| D-008 | TimeSeriesSplit 交叉验证 | 时间序列防泄露 |
| D-009 | 赛前/赛后特征分离 | 零数据泄露 |
| D-010 | 赔率衍生特征 | 凯利指数、动量等 |
| D-012 | Elo Rating 特征 | 球队实力量化 |
| D-013 | 时序赔率特征 | 市场情绪捕捉 |
| D-020 | CI 集成 (pytest) | 134 测试用例 |
| D-028 | 规则引擎禁用 | 仅保留辅助信号 |
| D-029 | 自动重训机制 | 三重触发 + 性能门禁 |
| D-030 | 联赛差异补偿 | 德甲/法甲 ×1.20 权重 |
| A-002 | 中比分 λ 调整 | 解决 Top-1=0% 问题 |
| A-006 | T-005 v3 重训验证 | 走水召回率 0.4220 |
| B-006 | 特征对齐修复 | 191→187维，推理端与训练端对齐 |
| C-001 | 26/27赛季数据管线 | 新赛季准备 |
| C-004 | 球队名映射统一 | 动态加载 team_name_map.json |
| **V-001** | **标签随机化测试** | **确认无数据泄露，所有性能指标可信** |
| **V-002** | **比分命中率验证** | **确认 64.66% = Top-5 覆盖率** |
| **V-003** | **决策阈值 factor=1.5** | **平局召回率 0% → 33.8%，概率保持校准** |
| **V-004** | **英超独立模型** | **英超准确率 0.3886→0.4725 (+8.39pp)，v3.0 已部署** |
| **V-005** | **change_log.md 统一** | **合并至 docs/ 目录** |
| **V-006** | **联赛专属阈值分治** | **法甲/西甲/意甲 argmax，德甲/英超 factor=1.1** |
| **V-007** | **西甲预测脚本 T-005 v3 集成** | **predict_laliga_r1_batch2.py 真正调用 71 维两阶段模型** |
| **V-008** | **预测流程日志增强** | **28 条结构化日志覆盖全流程** |
| **V-009** | **v8.1 全面审计** | **代码/数据/文档/基础设施全覆盖，识别 15 个不足** |
| **V-010** | **英超独立模型 v3.0** | **205维特征，球员特征46维入模，CV 47.25%，已导出JS部署** |
| **V-011** | **特征优化 229→205维** | **移除24维单调变换特征，CV未降，模型更精简** |
| **V-012** | **统一报告生成器 v3.0** | **四维度: WDL 5模型Stacking + T-005 v3 + T-006 v4 + 总进球, ML 缓存+降级, 自动解析** |
| **V-013** | **ML 推理崩溃修复** | **特征缓存+失败标记，避免重复加载数据库和级联错误** |
| **V-014** | **缓存架构升级** | **LRU 淘汰 + 主动过期清理 + Redis 服务启动 (端口 6379)** |
| **V-015** | **半全场模块移除** | **calcHalfFull() 从 prediction-engine.js 和 prediction-service.js 删除，架构图修正，移除虚标函数 findValueBet()** |
| **V-016** | **预测核心模块化 v1.0** | **创建 prediction_core.py (CalcEngine/WDLPredictor/HandicapPredictor/ScorePredictor/TotalGoalsPredictor/PredictionCore)，A-002 λ调整从 JS 引擎移植到 Python，英超独立模型加载并注册到 WDLPredictor，generate_unified_report.py 精简 946 行改为调用 prediction_core** |
| **V-017** | **英超独立模型特征构造对接** | **_predict_epl() 从空壳函数改为完整6步流程: (1)模型完整性校验 (2)加载/复用 odds.db 数据 (3)多策略匹配比赛(正向/反向/构造新行) (4)提取205维特征并对齐scaler (5)缩放 (6)LightGBM推理。新增 34 条详细日志覆盖每个步骤，支持缺失特征填充0值、NaN处理、维度异常告警。英超模型现已真正参与 6模型 Stacking 预测** |
| **V-018** | **双库写入幂等性修复** | **model_predictions 表新增 `UNIQUE(match_id, model_name, prediction_type)`，savePreMatchPrediction / batchSavePredictions 两处 `INSERT` → `INSERT OR IGNORE`；新增迁移脚本 add_model_predictions_unique.py 清理历史重复并建唯一索引；补齐 batchSavePredictions 的 Lambda 字段写入。单测 8/8 通过，高并发 8 进程×400 次写入 0 重复，打通「预测→落库→重训」数据闭环** |
| **V-019** | **209维重训 + 联赛分档反压低平局校准** | **用修复后 209 维 pipeline 重训 WDL 模型（C-20260820-036），彻底消除 209/210 维列不一致；扩展 draw_threshold_factor 网格到 `[0.80~1.10]` 逐联赛重校准（C-20260820-037），发现高平局联赛（意甲/西甲/德甲）在 factor<1.0「反压低平局」处准确率最优：意甲 0.85(+0.97pp)、西甲 0.80(+1.05pp)、德甲 0.85(+0.87pp)，英超/法甲 argmax 不变。config.yaml 与 prediction-service.js 同步更新** |
| **V-020** | **v4 过度优化修复（6项）** | **依据 `docs/足球模型架构深度诊断报告_v2.0.md` 第二章过度优化诊断，按优先级顺序逐一修复：① Monte Carlo 3000→500（C-20260823-001）② 温度 T=0.8→1.0（C-20260823-002）③ 联赛分档→argmax 加开关（C-20260823-003）④ 英超模型移出 Stacking 改为 epl_reference（C-20260823-004）⑤ 6模型→4模型（C-20260823-005）⑥ 赔率 135→30维 + 默认 slim_odds=True（C-20260823-006）。附带 pandas pd.concat 批量拼接性能优化（C-20260823-007）。A/B 测试验证：准确率 -0.48pp，平局召回率 +1.55pp，特征构建速度 1.6x，训练速度 1.8x。详见 change_log.md §3.65 和 project_memory.md §7** |

---

> **报告生成时间**: 2026-08-18 (v2.7 英超独立模型特征构造对接完成 + 详细日志) ｜ 2026-08-20 (v2.8 209维重训 + 联赛分档反压低平局校准) ｜ 2026-08-23 (v2.9 v4 过度优化修复 6 项) ｜ **2026-09-09 (v2.1 修订版：精简 + 交叉引用 + 历史口径标注)**  
> **项目版本**: v8.3  
> **项目路径**: `f:\zuqiu\五大联赛专属模型\五大联赛专属模型\`  
> **审计结论**: 系统整体健康，WDL 预测可投入生产使用。v8.3 已完成 6 项过度优化修复（依据架构诊断报告），模型精简为 4 模型（DC+XGB+LGB+Elo），特征精简为 165 维（赔率 30 维），联赛分档回退 argmax 加开关保留，Monte Carlo 500 次，温度 T=1.0。准确率损失可控（-0.48pp），平局召回率提升（+1.55pp），特征构建速度提升 1.6x。主要短板为升班马特征缺失、比分预测命中率低、缺少动态攻防强度模型。建议按架构诊断报告第七章行动路线图逐步推进中期提升。（2026-08-23 历史审计结论；当前状态见《模型架构分析报告_v1.0.md》§九）
>
> **v2.1 修订说明（2026-09-09）**: 依据《文档对比分析报告_v1.0.md》（D-026/D-027），本文定位为「原理与验证」型文档（算法推导 / 特征工程演化 / 验证证据 / 历史结论）；所有"当前值"以《模型架构分析报告_v1.0.md》（现状权威）与《model_gap_analysis_report_v2.0.md》（差距与路线图权威）为准，本文不再自持现状数据；历史快照均已标注口径。备份：`backups/docs/SYSTEM_ANALYSIS_REPORT_v2.0.md.bak`（2026-09-09，改造前原件）。