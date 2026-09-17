# 新对话执行流程指南（底层逻辑）

> **文档版本**: v2.1
> **创建时间**: 2026-08-06
> **最后更新**: 2026-08-12
> **适用项目**: 五大联赛足球预测模型 v9.0（多任务预测+自动重训）
> **目的**: 作为所有新对话的底层逻辑基础，整合项目规则、执行流程、变更日志机制和模型解析机制
> **核心原则**: 所有新对话必须先解析本文件，再执行任何操作

---

## 零、项目当前状态快照

### 0.1 核心性能指标（基准值 - 必须在每次对话启动时验证）

#### 核心 WDL 预测模型（v9.0.0）

| 指标 | 值 | 验证方式 |
|------|------|----------|
| CV 准确率（114维, LGB） | **49.71% ± 1.09%** | 读取 `assets/advanced_model_20260810_092643.pkl` |
| CV 准确率（114维, XGB） | **50.40% ± 1.85%** | 同上 |
| LogLoss（114维） | **LGB 1.0248, XGB 1.0214** | 同上 |
| 过拟合差距 | **LGB 5.02%, XGB 0.92%** | 同上 |
| 训练数据量 | **5,252场** | 读取 `data/odds.db` matches 表 |
| 特征维度 | **114维**（113核心+is_season_2526） | 训练日志 |
| 比分赔率特征覆盖率 | **63.8%**（3350/5252场） | 运行 `score_features.py` |
| 球队覆盖 | **121 支**（T-005 Elo快照） | 读取 `assets/t005v2_final_elo_ratings.json` |
| 数据库表数 | **11** | 调用 `GET /api/db/stats` |
| 数据库记录数 | **325,214+** | 同上 |
| 测试用例 | **134** | 运行 `python tests/run_all_tests.py` |

#### T-004 总进球预测（v9.0.0）

| 指标 | 值 | 验证方式 |
|------|------|----------|
| 数据量 | **3,916场**（100%覆盖率） | 训练日志 |
| 3类分类 LGB CV | **45.89%**（达标≥40%） | optimization_log §4.15 |
| 大/小球 LGB CV | **57.15%**（增强57.94%，达标≥55%） | optimization_log §4.15/4.17 |
| 7类分类 LGB CV | **22.88%**（Lag版本23.22%） | optimization_log §4.15/4.19 |

#### T-005 让球胜平负预测（v9.0.0）

| 指标 | 值 | 验证方式 |
|------|------|----------|
| 数据量 | **3,915场**（赔率反推16倍扩充） | optimization_log §4.20 |
| v2 测试集 Accuracy | **0.4866**（783场） | optimization_log §4.21 |
| v2 走水预测率偏差 | **1.15pp**（vs 实际0.2248） | optimization_log §4.21 |
| v2 走水召回率 | **0.2386** | optimization_log §4.21 |
| v2 F1 Macro | **0.4435** | optimization_log §4.21 |
| v3 特征维度 | **71维**（含24维对手Lag） | optimization_log §4.23 |
| v3 对手Lag贡献 | **57.1%** 决策权重 | conversation summary |
| 配置 | ratio=1.5 + T=2.150 + 动态阈值 + USE_RULE_ENGINE=False | deploy_t005v2_final.py |

#### 自动重训机制（v9.0.0 新增）

| 机制 | 配置 | 验证方式 |
|------|------|----------|
| 定时触发 | Windows计划任务 T005v3_AutoRetrain 每日8:00 | `schtasks /query /tn T005v3_AutoRetrain` |
| 数据触发 | matches+handicap_history+wdl_history表哈希 | `python scripts/retrain_trigger_runner.py check` |
| 周期触发 | 距上次重训≥7天 | 同上 |
| 性能门禁 | 走水召回率≥0.30，预测率偏差≤0.02 | `deployment/trigger_state.json` |
| 状态持久化 | deployment/trigger_state.json | 读取该文件 |

### 0.2 阶段完成状态

| 阶段 | 名称 | 完成度 | 关键成果 |
|------|------|--------|----------|
| 一 | 基础防护 | 100% | 项目记忆体系建立、42条硬性规则 |
| 二 | 数据修复 | 100% | match_id映射、赔率覆盖率99% |
| 三 | 模型修复 | 100% | CV 47.8%、XGB/LGB配置修复 |
| 四 | 特征工程 | 100% | 60维核心特征集（无泄露）、Elo/赔率/时序特征 |
| 五 | 模型调优 | 100% | CV 50.95%、Optuna调优、Elo公式修复 |
| 六 | 测试扩展 | 100% | 134测试用例、CI集成、Elo bug修复 |
| 七 | 服务优化 | 100% | better-sqlite3、全局错误处理、模型热更新 |
| 八 | 记忆系统 | 60% | 文档体系已建立；MCP/RAG待实施 |
| 九 | 数据扩充 | 33% | SofaScore采集器完成；全量采集待执行 |
| action_plan | T-001~T-003.7 | 100% | 5,252场数据、114维特征、TEAM_NAME_MAP扩充 |
| **多任务预测** | **T-004/T-005** | **100%** | **T-004总进球+T-005让球v3已完成** |
| **自动重训** | **三重触发** | **100%** | **定时/数据/周期+性能门禁已部署** |
| T-006 | 比分预测 | **100%** | **T-006 v5 已完成（C-20260908-022）：去水 WDL 数值求解 λ + IPF 重加权 + 生产 WDL 锚定，Top-1 13.14%** |

### 0.3 当前生产模型配置

#### 核心 WDL 预测模型

```
模型类型: LightGBM 分类器
特征维度: 114维 (113核心 + is_season_2526)
  - 赔率特征: 66维 (含WDL/HCP/TG/凯利/变化率/市场指标)
  - 基础特征: 12维 (含league one-hot 5维)
  - Elo特征: 10维 (强制保留)
  - D-013时序特征: 10维 (强制保留)
  - 比分赔率特征: 8维 (强制保留，覆盖率63.8%)
  - 非线性变换特征: ~7维 (D-011自动选择，非强制)
CV 准确率: 49.71% ± 1.09% (LGB) / 50.40% ± 1.85% (XGB)
LogLoss: LGB 1.0248, XGB 1.0214
训练数据: 5,252场比赛 (5大联赛 × 3赛季: 2023-24/2024-25/2025-26)
CV方法: 5折 TimeSeriesSplit
过拟合差距: XGB=0.92%, LGB=5.02%
模型文件: advanced_model_20260810_092643.pkl
```

#### T-005 让球胜平负预测模型（v2 最终部署）

```
模型类型: 二阶段模型
  - Stage1: 走水检测器 (LightGBM, class_weight ratio=1.5)
  - Stage2: 方向预测器 (LightGBM)
特征维度: 37维 (15 HCP + 3 WDL + 12 状态 + 3 反推 + 4 市场信号) + 10 Elo = 47维
  - v3扩展: +24维对手调整Lag特征 = 71维
后处理: T=2.150 温度缩放 + 动态阈值 (按7类盘口线)
规则引擎: USE_RULE_ENGINE = False (方案A，关闭)
测试集: 783场
  - Accuracy: 0.4866
  - F1 Macro: 0.4435
  - 走水预测率: 0.2133 (实际0.2248, 偏差1.15pp)
  - 走水召回率: 0.2386
模型文件:
  - assets/t005v2_final_draw_detector.pkl
  - assets/t005v2_final_direction_predictor.pkl
  - assets/t005v2_final_elo_ratings.json (121支球队)
  - assets/t005v2_final_metadata.json
```

#### 自动重训触发器

```
触发机制: 三重触发
  - 定时触发: Windows计划任务 T005v3_AutoRetrain (每日8:00)
  - 数据触发: matches+handicap_history+wdl_history表哈希变更
  - 周期触发: 距上次重训≥7天
性能门禁:
  - 走水召回率 ≥ 0.30
  - 预测率偏差 ≤ 0.02
执行引擎: scripts/retrain_trigger_runner.py
状态持久化: deployment/trigger_state.json
重训脚本: scripts/deploy_t005v3_final.py
```

---

## 一、核心原则与硬性规则

### 1.1 三阶段隔离原则

| 阶段 | 允许操作 | 禁止操作 |
|------|----------|----------|
| **阶段1：模型解析** | 读取所有文档、验证文件完整性、查询API | 修改/创建/删除文件、停止/启动进程 |
| **阶段2：安全启动** | 启动/停止服务、调用健康检查API | 修改项目配置、自动重试、修改代码 |
| **阶段3：受控操作** | 用户明确指令的操作 | 范围外修改、批量操作、未确认的重启 |

### 1.2 硬性规则清单（从 project_memory.md 提取）

#### 数据规则 (DATA-001 ~ DATA-009)

| 规则ID | 内容 |
|--------|------|
| DATA-001 | 必须使用真实比赛数据，禁止硬编码或模拟数据 |
| DATA-002 | 赔率数据保持原始精度，不四舍五入 |
| DATA-003 | 日期格式统一为 YYYY-MM-DD HH:MM:SS |
| DATA-004 | 比分格式统一为 X:Y |
| DATA-005 | 球队名称使用中文标准名称 |
| DATA-006 | match_id 格式为 YYYY-MM-DD_主队_客队 |
| DATA-007 | 所有数据导入必须经过验证 |
| DATA-008 | 数据库变更必须记录到 change_log.md |
| DATA-009 | 数据泄露检查：特征不得使用赛后信息 |

#### 特征规则 (FEAT-001 ~ FEAT-008)

| 规则ID | 内容 |
|--------|------|
| FEAT-001 | 必须区分赛前/赛后特征，预测只用赛前特征 |
| FEAT-002 | 81个赔率时序特征必须保留 |
| FEAT-003 | 训练和预测必须使用相同特征集合 |
| FEAT-004 | StandardScaler 只在训练集 fit |
| FEAT-005 | 时间序列交叉验证，按时间划分 |
| FEAT-006 | h2h_last_result 不得泄露标签 |
| FEAT-007 | 运算符优先级检查必须加括号 |
| FEAT-008 | 特征重要性排序后保留Top-K |

#### 模型规则 (MODEL-001 ~ MODEL-007)

| 规则ID | 内容 |
|--------|------|
| MODEL-001 | 使用 class_weight 处理类别不平衡 |
| MODEL-002 | XGBoost/LightGBM 必须使用 sklearn API |
| MODEL-003 | 集成权重必须动态调整 |
| MODEL-004 | 参数从 config.yaml 读取 |
| MODEL-005 | 训练/测试准确率差距不得超过20% |
| MODEL-006 | 必须与基线模型(47.22%)比较 |
| MODEL-007 | 模型保存时记录版本号和性能指标 |

#### 安全规则 (SECURITY-001 ~ SECURITY-005)

| 规则ID | 内容 |
|--------|------|
| SECURITY-001 | 禁止执行危险SQL |
| SECURITY-002 | 所有输入必须验证 |
| SECURITY-003 | 禁止硬编码敏感信息 |
| SECURITY-004 | 数据库操作使用参数化查询 |
| SECURITY-005 | 配置文件不得包含密码或密钥 |

### 1.3 禁止行为清单

| 禁止行为 | 原因 | 正确做法 |
|----------|------|----------|
| ❌ 看到日志报错就自动修复 | 可能修复错误的问题 | 只汇报日志，等待用户指令 |
| ❌ 未读取就覆盖文件 | 可能覆盖其他上下文的修改 | 先 Read 获取最新内容 |
| ❌ 擅自修改 `.env` 配置 | 可能破坏生产环境 | 需用户明确指令 + 确认 |
| ❌ 反复重启服务做"测试" | 服务可能不稳定 | 等待健康检查通过再操作 |
| ❌ 用 `pm2 restart` 代替 `reload` | restart 导致短暂停机 | 优先使用 `pm2 reload` |
| ❌ 在 Windows 下使用 Unix 命令 | 命令不兼容 | 使用 PowerShell 或 npm run |

---

## 二、变更日志机制（强制）

### 2.1 变更日志文件结构

所有变更必须记录到 `docs/change_log.md`，格式如下：

```
| 变更ID | 时间 | 类型 | 范围 | 内容 | 变更前 | 变更后 | 原因 | 关联问题 | 验证结果 | 风险 | 变更人 | 状态 |
```

### 2.2 变更类型分类

| 类型 | 说明 | 示例 |
|------|------|------|
| 文档 | 文档内容变更 | 更新交付报告、更新指南 |
| 配置 | 配置文件变更 | 修改 .env、修改 ecosystem.config.cjs |
| 代码 | 源代码变更 | 修改 server/*.js、修改 shared/*.js |
| 模型 | 模型文件变更 | 替换 xgb_model_export.js、更新 stacking_weights.json |
| 数据库 | 数据库结构或数据变更 | 新增表、导入数据、修改记录 |
| 架构 | 架构级变更 | 引入 better-sqlite3、添加中间件 |

### 2.3 强制更新规则

```
⚠️ 任何文件修改后，必须按以下顺序更新：

1. 【change_log.md】→ 记录变更内容、时间、原因、影响范围
2. 【optimization_log.md】→ 记录任务完成情况
3. 【key_decisions.md】→ 如涉及重要决策
4. 【project_memory.md】→ 如涉及新的经验教训
5. 【prompt_template.md】→ 如阶段或指标发生变化

### 2.4 优化任务文档同步门禁

每一项优化任务必须同时完成“实现、验证、文档更新”三项交付，不能只完成代码或参数修改。

- 优化开始前：明确涉及的文档、变更ID、验证指标和回滚方式。
- 优化完成后：必须更新 `change_log.md`，并按影响范围同步 `optimization_log.md`、`key_decisions.md`、`project_memory.md`、`prompt_template.md` 或相关专项文档。
- 文档更新必须记录实际结果，包括变更前后、验证数据、失败项、遗留风险和当前状态；不得只写“已完成”。
- 若验证失败或任务中止，也必须更新文档，标记为失败/阻塞/待验证，并记录下一步，不得省略。
- 在文档同步完成前，优化任务状态不得标记为“已完成”。
```

### 2.4 变更日志模板

在执行任何变更前，必须先输出以下信息：

```
【变更预告】
- 变更ID: C-{YYYYMMDD}-{序号}
- 变更类型: [文档/配置/代码/模型/数据库/架构]
- 变更范围: [涉及的文件路径]
- 变更内容: [具体变更描述]
- 变更原因: [变更的必要性说明]
- 影响范围: [可能受影响的模块/功能]
- 验证方案: [如何验证变更正确]
- 风险评估: [低/中/高]
```

---

## 三、阶段1：模型解析与验证

**适用场景**: 每次新对话启动时必须执行  
**核心规则**: **绝对不允许修改任何文件或配置**  
**耗时**: 约 60 秒  
**目的**: 确保模型状态与预期一致，为后续操作提供可靠基准

### 3.1 提示词模板（复制使用）

```
【模型解析与验证 - 严格只读】

项目路径: H:\zuqiu\五大联赛专属模型\五大联赛专属模型

请对项目底层逻辑模型进行完整性解析与验证：

## 验证项 1: 文档一致性解析
按以下顺序读取并验证文档内容一致性：
1. 读取 docs/prompt_template.md → 获取当前阶段标识、已完成项、性能基准值
2. 读取 docs/model_optimization_plan.md → 获取阶段进度、性能指标、优化日志
3. 读取 docs/change_log.md → 获取最近变更记录（最近5条）
4. 读取 docs/key_decisions.md → 获取关键决策状态
5. 读取 docs/project_memory.md → 获取硬性规则和经验教训
6. 读取 docs/optimization_log.md → 获取最新任务完成情况
7. 读取 docs/CONVERSATION_WORKFLOW_GUIDE.md → 获取本指南最新版本

验证要点：
- 各文档中的性能指标是否一致（CV准确率、特征维度等）
- 阶段状态描述是否一致
- 已完成任务列表是否有遗漏

## 验证项 2: 配置参数验证（只读）
读取以下文件并验证：
- .env: NODE_ENV, PORT, JWT_SECRET 前8位
- ecosystem.config.cjs: exec_mode, instances, max_memory_restart
- assets/model_config.json: 模型版本和参数

## 验证项 3: 模型资产完整性（只读）
检查 assets/ 目录 8 个核心文件是否存在且非空：
1. xgb_model_export.js (XGBoost 模型)
2. lgb_model_export.js (LightGBM 模型)
3. feature_scaler_params.js (特征标准化)
4. team_attributes.json (球队属性)
5. stacking_weights.json (集成权重)
6. league_tier.json (联赛层级)
7. league_tier_weight.json (联赛权重)
8. model_config.json (模型配置)

## 验证项 4: 进程状态查询（只读）
执行: pm2 status
执行: pm2 describe five-leagues
记录: 状态、PID、运行时长、内存占用、重启次数

## 验证项 5: API 健康验证（只读）
调用: GET http://localhost:3000/api/health
关注: status, database.connected, models.xgbLoaded, models.lgbLoaded

## 输出要求
1. 使用表格形式输出每项验证结果
2. 标注 ✅ 一致/正常、⚠️ 不一致/警告、❌ 异常/缺失
3. 如发现文档不一致，详细列出差异点
4. 如发现文件缺失，列出缺失的文件
5. 如发现性能指标不匹配，列出具体数值对比
6. 最后输出"模型状态评估"总结

⚠️ 严格禁止：
- 修改、删除、创建任何文件
- 停止或重启任何进程
- 安装或卸载任何依赖
- 根据验证结果做任何修复操作
```

### 3.2 验证结果输出格式

```
=== 模型解析与验证结果 ===

[1. 文档一致性]
  prompt_template.md:   阶段=七, CV=50.95%    ✅
  model_optimization_plan.md: 阶段=七, CV=50.95%  ✅
  change_log.md:        最近3条变更记录          ✅
  key_decisions.md:     20个决策, 全部执行       ✅
  project_memory.md:    42条规则, 30条经验       ✅
  optimization_log.md:  阶段七已完成             ✅
  一致性验证: ✅ 全部一致

[2. 配置参数]
  .env:        NODE_ENV=production, PORT=3000    ✅
  ecosystem:   exec_mode=fork, instances=1       ✅
  model_config: 版本匹配                          ✅

[3. 模型资产]
  ✅ xgb_model_export.js     132.6KB
  ✅ lgb_model_export.js      48.1KB
  ... (8/8 通过)

[4. 进程状态]
  状态: online (PID 7120, 运行 8 分钟, 78MB)    ✅

[5. API 健康]
  health: healthy, database.connected=true       ✅

=== 模型状态评估 ===
  状态: ✅ 完全一致，可以安全执行后续操作
  文档版本: 所有文档与当前状态匹配
  风险等级: 低
```

### 3.3 异常处理

如发现不一致，**仅输出差异详情**，不自动修复：

```
=== 异常项 ===

[⚠️ 文档不一致]
  prompt_template.md 记录 CV=50.48%
  model_optimization_plan.md 记录 CV=50.95%
  差异原因: Elo 公式修复后未同步更新 prompt_template.md
  建议: 使用【阶段3：受控操作】更新 prompt_template.md

[❌ 资产缺失]
  lgb_model_export.js 不存在
  建议: 从备份恢复或重新训练模型
```

---

## 四、阶段2：安全启动与验证

### 4.1 服务未在运行时

**提示词模板：**

```
【安全启动 - 仅允许启动服务】

前置条件: 阶段1模型解析完成，PM2 进程未运行

请执行以下启动步骤：
1. 检查端口占用: netstat -ano | findstr :3000
2. 如端口被占用: pm2 delete five-leagues
3. 启动服务: pm2 start ecosystem.config.cjs --env production
4. 等待 5 秒
5. 健康检查: GET http://localhost:3000/api/health
6. 完整验证:
   - GET /api/db/stats (数据库统计)
   - POST /api/predict (阿森纳 vs 利物浦)
7. 输出启动结果表格

⚠️ 严格禁止：修改配置、自动重试、安装依赖
```

### 4.2 服务已在运行时

**提示词模板：**

```
【服务验证 - 只读 API 调用】

前置条件: 阶段1模型解析完成，PM2 进程已在运行

请执行以下只读 API 验证：
1. GET /api/health → 关注 status, database.tables, models
2. GET /api/db/stats → 关注 teams, matches, competitions
3. POST /api/predict (阿森纳 vs 利物浦) → 关注 predictions, ensemble.weights
4. GET /api/model/reload/status → 关注 watchedFiles, reload 统计
5. 输出验证结果表格

⚠️ 严格禁止：修改文件、停止/重启服务
```

---

## 五、阶段3：受控操作

### 5.1 进入条件

必须同时满足：
- ✅ 阶段1模型解析完成且状态正常
- ✅ 阶段2服务验证通过
- ✅ 用户发出明确操作指令

### 5.2 操作流程

```
1. 【变更预告】输出：变更ID、类型、范围、内容、原因、影响、验证方案、风险
2. 征求用户确认
3. 读取目标文件获取最新内容（Read）
4. 执行修改（Edit/Write）
5. 验证修改结果
6. 按【2.3 强制更新规则】更新所有相关文档
7. 输出【变更完成报告】
```

### 5.3 常见操作示例

#### 示例1：更新模型集成权重

```
【变更预告】
- 变更ID: C-20260806-071
- 类型: 模型
- 范围: assets/stacking_weights.json
- 内容: 调整6模型集成权重
- 原因: 优化集成策略
- 影响范围: 预测引擎的结果融合
- 验证方案: 调用 /api/predict 验证新权重生效
- 风险: 低（热更新可回滚）

[征求确认]
... 执行操作 ...

【变更完成报告】
- ✅ stacking_weights.json 已更新
- ✅ ModelHotReloader 自动检测并生效
- ✅ 预测验证通过（WDL/让球/大小球返回正常）
- ✅ change_log.md 已更新
- ✅ optimization_log.md 已更新
```

#### 示例2：修改配置参数

```
【变更预告】
- 变更ID: C-20260806-072
- 类型: 配置
- 范围: .env
- 内容: 更新 JWT_SECRET 为新的随机密钥
- 原因: 安全加固
- 影响范围: 需要认证的管理 API
- 验证方案: 调用 /api/auth/register 获取 token
- 风险: 中（所有现有 token 将失效）

[征求确认]
... 执行操作 ...

【变更完成报告】
- ✅ .env 已更新
- ⚠️ 需要重启服务生效
- 征求确认后执行: pm2 reload five-leagues
- ✅ 服务已重启，健康检查通过
- ✅ change_log.md 已更新
```

---

## 六、场景快速索引

| 场景 | 推荐流程 | 使用的提示词块 |
|------|----------|----------------|
| 首次启动，了解系统 | 阶段1 → 阶段2 | [3.1 模型解析](#31-提示词模板（复制使用）) + [4.2 服务验证](#42-服务已在运行时) |
| 服务未运行，需要启动 | 阶段1 → 阶段2.1 | [3.1 模型解析](#31-提示词模板（复制使用）) + [4.1 启动服务](#41-服务未在运行时) |
| 查看当前模型性能 | 阶段1 → 阶段2 | [3.1 模型解析](#31-提示词模板（复制使用）) + [4.2 服务验证](#42-服务已在运行时) |
| 修改配置参数 | 阶段1 → 阶段2 → 阶段3 | [3.1](#31-提示词模板（复制使用）) + [4.2](#42-服务已在运行时) + [5.2 操作流程](#52-操作流程) |
| 上传新模型文件 | 阶段1 → 阶段2 → 阶段3 | [3.1](#31-提示词模板（复制使用）) + [4.2](#42-服务已在运行时) + [5.2](#52-操作流程) |
| 排查服务异常 | 阶段1（增强） → 阶段2 | [3.1](#31-提示词模板（复制使用）) + [日志增强检查](#61-增强检查项（排查异常时）) |
| 生成交付文档 | 阶段1 → 阶段2 → 阶段3 | [3.1](#31-提示词模板（复制使用）) + [4.2](#42-服务已在运行时) + [5.2](#52-操作流程) |

### 6.1 增强检查项（排查异常时）

```
## 增强验证项 6: 日志分析
读取 logs/error.log 最近 20 行
读取 logs/out.log 最近 20 行
读取 logs/combined.log 最近 20 行
关注: ERROR/FATAL 级别、未捕获异常、降级警告

## 增强验证项 7: 数据库完整性
调用 GET /api/db/stats
对比数据库记录数是否异常

## 增强验证项 8: 模型热更新状态
调用 GET /api/model/reload/status
检查: watchedFiles 数量、successfulReloads、failedReloads
```

---

## 七、完整流程示例

### 7.1 场景：新对话中修改模型配置

```
用户: 新对话中，我想把模型的集成权重调整一下

AI: [执行阶段1：模型解析与验证]
    - 7个文档一致性验证 → ✅ 全部一致
    - 8个资产文件完整性 → ✅ 8/8 通过
    - PM2 进程状态 → ✅ online
    - API 健康检查 → ✅ healthy

AI: [输出验证结果]
    ✅ 模型状态完全一致，可以安全执行操作

AI: [执行阶段2：服务验证]
    - GET /api/health → ✅
    - POST /api/predict → ✅ 返回正常预测

AI: [进入阶段3：受控操作]
    【变更预告】
    - 变更ID: C-20260806-071
    - 类型: 模型
    - ... (完整预告信息)
    是否继续？

用户: 是

AI: [执行操作]
    1. 读取 stacking_weights.json → 当前 6 模型权重
    2. 修改为新权重配置
    3. 等待 ModelHotReloader 自动检测（约 3 秒）
    4. 验证预测结果

AI: [按强制规则更新文档]
    1. change_log.md ← 变更记录
    2. optimization_log.md ← 任务记录
    3. prompt_template.md ← 如指标变化

AI: [输出完成报告]
    ✅ 集成权重已更新
    ✅ 热更新成功
    ✅ 预测验证通过
    ✅ 所有相关文档已同步更新
```

---

## 八、附录

### 8.1 快速命令参考

```bash
# 模型解析（只读）
pm2 status
pm2 describe five-leagues

# 启动/停止服务
pm2 start ecosystem.config.cjs --env production
pm2 stop five-leagues
pm2 delete five-leagues

# 重启服务
pm2 reload five-leagues       # 零停机（推荐）
pm2 restart five-leagues      # 完全重启

# 查看日志
pm2 logs five-leagues --lines 50
pm2 logs five-leagues --err

# API 端点
GET  http://localhost:3000/api/health
GET  http://localhost:3000/api/db/stats
POST http://localhost:3000/api/predict
GET  http://localhost:3000/api/model/reload/status
POST http://localhost:3000/api/model/reload/:key

# 测试
python tests/run_all_tests.py
python tests/ci_check.py --quick
```

### 8.2 关键文件索引

| 文件路径 | 用途 | 修改权限 |
|----------|------|----------|
| `docs/CONVERSATION_WORKFLOW_GUIDE.md`（本文件） | **底层逻辑**：执行流程、规则、机制 | ⚠️ 修改需同步所有引用 |
| `docs/prompt_template.md` | 上下文模板：阶段、进度、性能基准 | ⚠️ 阶段切换时更新 |
| `docs/change_log.md` | 变更日志：所有变更记录 | ✅ 每次变更后必须更新 |
| `docs/optimization_log.md` | 任务日志：任务完成记录 | ✅ 每次任务后更新 |
| `docs/key_decisions.md` | 决策日志：重要决策记录 | ✅ 决策时更新 |
| `docs/project_memory.md` | 项目记忆：规则/约定/经验 | ⚠️ 规则变更时更新 |
| `docs/model_optimization_plan.md` | 优化方案：阶段计划和进度 | ⚠️ 阶段完成后更新 |
| `PROJECT_DELIVERY_REPORT.md` | 项目交付报告 | ⚠️ 重大变更时更新 |
| `DEPLOYMENT_GUIDE.md` | 生产部署指南 | ⚠️ 部署相关变更时更新 |

### 8.3 与模型资产的关系

| 资产路径 | 用途 | 是否可热更新 |
|----------|------|-------------|
| `assets/xgb_model_export.js` | XGBoost 模型 | ✅ |
| `assets/lgb_model_export.js` | LightGBM 模型 | ✅ |
| `assets/stacking_weights.json` | 集成权重 | ✅ |
| `assets/team_attributes.json` | 球队属性 | ✅ |
| `assets/feature_scaler_params.js` | 特征标准化 | ✅ |
| `assets/model_config.json` | 模型配置 | ✅ |
| `server/index.js` | 服务入口 | ❌ 需重启 |
| `server/database/index.js` | 数据库层 | ❌ 需重启 |

### 8.4 版本历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-08-06 | 初始版本，三阶段隔离流程 |
| v1.1 | 2026-08-06 | 新增文档索引和工作记录提醒 |
| v2.0 | 2026-08-06 | **重大升级**：整合所有项目文档的关键信息作为底层逻辑，新增模型解析机制、变更日志机制、性能基准验证 |

---

**文档维护**: 本文件为项目执行的底层逻辑，所有新对话必须首先解析本文件  
**更新频率**: 每次项目状态发生重大变化时更新  
**关联文档**: prompt_template.md, project_memory.md, change_log.md, optimization_log.md, key_decisions.md, model_optimization_plan.md