# 足球预测模型 - 项目记忆

> 创建时间：2026-07-23
> 用途：存储项目核心规则、工程约定、经验教训，确保跨会话一致性
> 关联文档：docs/optimization_log.md, docs/key_decisions.md, docs/change_log.md

---

## 目录

1. [硬性规则](#一硬性规则)
2. [工程约定](#二工程约定)
3. [经验教训](#三经验教训)
4. [记忆系统规则](#四记忆系统规则)
5. [每日对话检索清单](#五每日对话检索清单)

---

## 一、硬性规则

### 1.1 数据规则 (DATA-001 ~ DATA-009)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| DATA-001 | 必须使用真实比赛数据，禁止硬编码或模拟数据 | 2026-07-23 |
| DATA-002 | 赔率数据保持原始精度，不四舍五入 | 2026-07-23 |
| DATA-003 | 日期格式统一为 YYYY-MM-DD HH:MM:SS | 2026-07-23 |
| DATA-004 | 比分格式统一为 X:Y | 2026-07-23 |
| DATA-005 | 球队名称使用中文标准名称 | 2026-07-23 |
| DATA-006 | match_id 格式为 YYYY-MM-DD_主队_客队 | 2026-07-23 |
| DATA-007 | 所有数据导入必须经过验证 | 2026-07-24 |
| DATA-008 | 数据库变更必须记录到 change_log.md | 2026-07-24 |
| DATA-009 | 数据泄露检查：特征不得使用赛后信息 | 2026-07-24 |

### 1.2 特征规则 (FEAT-001 ~ FEAT-012)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| FEAT-001 | 必须区分赛前/赛后特征，预测只用赛前特征 | 2026-07-23 |
| FEAT-002 | 81个赔率时序特征必须保留 | 2026-07-23 |
| FEAT-003 | 训练和预测必须使用相同特征集合 | 2026-07-23 |
| FEAT-004 | StandardScaler 只在训练集 fit，不得在测试集 fit | 2026-07-24 |
| FEAT-005 | 时间序列交叉验证，按时间划分训练/测试集 | 2026-07-24 |
| FEAT-006 | h2h_last_result 不得泄露标签 (L-012) | 2026-07-24 |
| FEAT-007 | 运算符优先级检查：A&B\|C&D 必须加括号 | 2026-07-24 |
| FEAT-008 | 特征重要性排序后保留Top-K特征 | 2026-07-23 |
| FEAT-009 | 时序/比分赔率特征由 ts_odds 开关控制（build_all_features 默认 False），开启后接入 D-013 22维 + T-003.1 8维并做联赛 z-score 归一 | 2026-08-28 |
| FEAT-010 | 时序赔率表（wdl/handicap/total_goals/score_history）对齐 matches 必须使用 build_match_alignment 三通道（直连+match_id_en+桥表），禁止自定义单通道 JOIN | 2026-08-28 |
| FEAT-011 | 多博彩公司赔率一致性特征由 consensus_odds 开关控制（build_all_features 默认 False，生产 True），接入 10 维（mkt_imp_{win,draw,lose} 3 + mkt_dev_{win,draw,lose} 3 + mkt_dev_abs + mkt_dispersion + mkt_company_count + mkt_return），生产启用后模型 198→208 维 | 2026-08-28 |
| FEAT-012 | 情境化特征由 ctx_features 开关控制（build_all_features 默认 False），接入 14 维（休息天数3 + 赛程密度4 + 连续作战2 + 积分压力3 + 德比/新军经验2）；A/B 及降维复查均无 RPS 增益，生产维持 False 不启用 | 2026-08-28 |

### 1.3 模型规则 (MODEL-001 ~ MODEL-007)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| MODEL-001 | 使用 class_weight 处理类别不平衡 | 2026-07-23 |
| MODEL-002 | XGBoost/LightGBM 必须使用 sklearn API | 2026-07-23 |
| MODEL-003 | 集成权重必须动态调整 | 2026-07-23 |
| MODEL-004 | 参数从 config.yaml 读取，禁止硬编码 | 2026-07-23 |
| MODEL-005 | 训练准确率与测试准确率差距不得超过20% | 2026-07-24 |
| MODEL-006 | 必须与基线模型(全押主胜47.22%)比较 | 2026-07-24 |
| MODEL-007 | 模型保存时记录版本号和性能指标 | 2026-07-23 |

### 1.5 概率校准与 EV 决策规则 (CALIB-001 ~ CALIB-009)

> C-20260903-006 概率校准对比实验（严格时序 OOF 11965 场，TimeSeriesSplit(5) 折内 fit→折外 transform，无校准泄漏）后固化的校准选型硬约束。关键基线：当前生产 Platt 校准 LogLoss 0.9907 / 平局召回 13.94% / EV 平注 ROI -5.41%。

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| CALIB-001 | 概率校准**必须**基于 raw 概率反推 logits 后再做温度/向量缩放；**禁止**对 Platt 后概率再叠加同属 parametric 类的缩放（T=1.469 时平局类 T_draw 顶到边界 8.0，导致平局召回坍缩至 1% 以下） | 2026-09-03 §3.106 |
| CALIB-002 | 校准对比实验的「训练/应用」必须与时序 CV 同源（折内训练集拟合校准器，**仅在折外验证集应用**），禁止用全量标签拟合校准器再作用于同一样本（会造成正向偏差 +1~3pp ROI 不可信） | 2026-09-03 §3.106 |
| CALIB-003 | 平局召回率≥0.28 为第一优先级约束，LogLoss/ROI 优化必须在满足平局召回达标前提下进行；平局召回不达标而 ROI「更好」的方案，统一视为不可投产 | 2026-09-03 §3.106（VectorScaling/Iso 平局召回 0.3~2.3%，ROI 再优也无意义） |
| CALIB-004 | **当前首选校准方案 = TempScaling(on raw, NLL 最优搜索 T)**。11965 场 OOF 实测：T≈0.896~0.975（逐折递减，越近赛季越需锐化）、平局召回 27.16%（✅达标，Δ vs Platt +13.22pp）、平注 ROI -3.71%（Δ vs Platt +1.70pp，当前最优）、Top-class ECE 7.70%（略高于 Platt 的 3.73%，属合理 trade-off） | 2026-09-03 §3.106 |
| CALIB-005 | Isotonic-OVR（一对其余保序回归）**禁止单独用于概率校准**。对 Platt 概率再做保序会系统性压低平局识别（平召从 13.94%→2.32%），ROI 也大幅劣化（-5.41%→-8.28%）。保序回归仅可与 DrawCalibrator 叠加做「平局专项恢复」，但仍劣于 Temp(on raw) 单级 | 2026-09-03 §3.106 |
| CALIB-006 | DrawCalibrator factor 在 0.95 以下可显著抬升平局召回，但必须以「温度缩放先压过度自信」为前置条件的 Temp+DrawCal(0.30) 组合为上限；单独 DrawCal 或 factor<0.90 会牺牲 EV 排序质量（平召 39.81% 但 ROI 反而从 -3.71% 恶化到 -5.44%） | 2026-09-03 §3.106 |
| CALIB-007 | 概率校准**本身不能使 EV ROI 转正**（当前 6 方案最佳 Temp ROI 仍 -3.71%，平均 EV 仍 +17~21%，系统性高估仅被部分压缩）。EV ROI 转正必须叠加：①EV 阈值抬升/择场过滤（min_ev 0.05→0.10 + 置信过滤）；②训练端直接以 EV/ROI 为目标（替代 WDL 交叉熵）；③edge 分桶单调回归（>10pp 桶仍 -6.53% 需专项修复） | 2026-09-03 §3.106 结论 |
| CALIB-008 | **EV 择场「阈值抬升 + 置信过滤」双条件经 72 组合全量扫描（C-20260904-001）证实无法使 ROI 转正**（9970 场 OOF，n≥100 全部为负）。实测反直觉规律：①min_ev 抬升 0.02→0.15 时平均 EV 升至 +35~57% 但 ROI 反而恶化（Baseline -5.15%→-7.28%），高 EV 方向实际胜率系统性低于 EV 隐含胜率（**edge 排序失效**）；②置信 P90 过滤后命中率暴跌至 9.8~12.4%（高置信被高赔率爆冷方向绑架）；③最优组合 Temp+min_ev=0.10+P50 仍 -2.93%，分赛季仅 3/9 季转正无一致性。→ **后续 ROI 转正**必须**直接进训练端**（loss 对 EV/ROI 求导或 edge 分桶单调回归修复），纯后处理择场已证伪 | 2026-09-04 §3.107 |
| CALIB-009 | **edge 分桶单调回归修复（C-20260904-002）结论：保序回归只能恢复分桶「单调性（排序修复）」，不能凭空产生正 edge（水平修复）**。实测 6 口径：①唯一单调方案 = Mono-Pooled(on Temp) 单一单调可靠性回归（pool 三类 (p,y) 拟合 p→P(y|p)，argmax 排序不变故平局召回结构性保持 32.3%），分桶 0~3pp -13.4% → 3~6pp -4.9% → 6~10pp -3.1% → >10pp -1.5% **单调递增但全桶仍负**；②整体最优仍 Mono-Pooled(on raw) -3.65%（n=9464）但分桶非单调；③**选择条件化 winner's curse 修正（Mono-Selected）证伪**：对 EV 引擎选中的 max-EV 方向拟合 p→P(win|选中) 再重算 edge/EV，整体平ROI -6.28% 反而更差、分桶非单调、>10pp 桶高估幅度升至 +17.5pp；④Mono-OVR 平召仅 0.92% 再次证实逐类保序坍缩，pooled 单映射是保平局召回的必要设计。→ 所有方案 ROI 均负、转正组合 0 个，**系统性概率高估的根治必须进训练端 EV/ROI 目标改造（loss 直接对 EV/ROI 求导）** | 2026-09-04 §3.108 |

### 1.6 安全规则 (SECURITY-001 ~ SECURITY-005)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| SECURITY-001 | 禁止执行危险SQL：DELETE/DROP/UPDATE/INSERT/ALTER | 2026-07-23 |
| SECURITY-002 | 所有输入必须验证 | 2026-07-23 |
| SECURITY-003 | 禁止硬编码敏感信息 | 2026-07-23 |
| SECURITY-004 | 数据库操作必须使用参数化查询 | 2026-07-23 |
| SECURITY-005 | 配置文件不得包含密码或密钥 | 2026-07-23 |

### 1.7 知识库规则 (KB-001 ~ KB-004)

> C-20260907-011（模块 B1）固化。知识库 = 赛后复盘闭环（模块 A）沉淀的经验知识库（L1 统计先验 / L2 特征工程 / L3 样本权重），不是规则引擎权重库；A5 审核确认（≥4 级）后才可写入。

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| KB-001 | 知识库统一 schema：`data/knowledge_base/{英超,西甲,意甲,德甲,法甲,global}/{feature_insights,sample_weights,rules}.json` + 根目录 `human_corrections.json`，外层 wrapper `{version,league,layer,updated_at,entries[]}`（对齐指南 §3.1）；**所有读写一律走 `scripts/knowledge_base_schema.py`**（load_entries/save_entries/load_human_corrections/add_insight/add_correction），禁止其他脚本直接读改 JSON 造成双写路径 | 2026-09-07 §3.126 |
| KB-002 | 写入门禁：`add_insight` 强制 confidence>=4 才写入 feature_insights（<4 级拒绝）；按 match_id 去重幂等；无 AI 归因主因（primary_cause）不写（仅赛果确认不入库） | 2026-09-07 §3.126 |
| KB-003 | `human_corrections.json` 位于 knowledge_base **根目录**（全局文件，非 global/ 子目录），条目对齐 §B4（11 字段）；每月清理失效条目：`--cleanup` 将 last_verified 超期（默认 90 天）且 status=active 的条目置为 expired（保留可追溯，不删除） | 2026-09-07 §3.126 |
| KB-004 | B2 赛前预测接入用 `get_insights(league)` 读取（同联赛优先 + global 兜底合并，默认过滤 active 且 ≥4 级）；A3 attribution_json 双形态解析统一用 `parse_attribution`/`primary_cause`（A5 基线分级与 B1 写入复用同一实现） | 2026-09-07 §3.126 |
| KB-005 | B2 赛前报告「知识库参考」章节：generate_unified_report.py 读同联赛 L2 feature_insights（get_insights），仅展示指导特征开发**不自动改参**；知识库读取异常降级标注「读取失败，本场结论不受影响」，不阻断报告生成（对齐 ev_engine 降级惯例） | 2026-09-07 §3.127 |
| KB-006 | SQL 子句拼接教训（--league 过滤长期失效根因，C-20260907-012）：`AND 条件` 一律拼接在 WHERE 子句内、**禁止放 ORDER BY 之后**——SQLite 会把 `ORDER BY ... AND league IN (?)` 解析为 ORDER BY 布尔表达式（`league AND league IN (?)`），条件静默失效且不报错 | 2026-09-07 §3.127 |

---

## 二、工程约定

### 2.1 文件结构

```
五大联赛专属模型/
├── 五大联赛专属模型/
│   ├── assets/          # 模型文件、配置
│   ├── client/          # 前端代码
│   ├── data/            # 数据文件、数据库
│   ├── docs/            # 文档
│   ├── logs/            # 日志
│   ├── modules/         # 功能模块（epl_odds, seriea_odds）
│   ├── output/          # 输出
│   ├── reports/         # 报告
│   └── scripts/         # 脚本
├── import_bundesliga.py  # 德甲导入脚本
├── import_ligue1.py      # 法甲导入脚本
└── project_memory.md    # 本文件
```

### 2.2 数据库约定

| 数据库 | 用途 | 路径 |
|--------|------|------|
| odds.db | 主数据库，存储比赛和赔率历史数据 | data/odds.db |
| odds_timing.db | 时序赔率数据库，存储多时间点赔率 | data/odds_timing.db |
| five_leagues.db | 比赛数据库，存储基本比赛信息 | data/five_leagues.db |
| PostgreSQL | odds.db 的 PG 后端（P2-11 迁移，2026-08-29），批处理负载提速 | localhost:5432 odds（DB_BACKEND=pg 切换） |

**数据库访问统一走 db_utils（双后端 SQLite/PG，2026-08-29 固化）：**
- 读：`db_utils.read_sql(sql, conn, params)` 替代 `pd.read_sql`（pandas 不支持非 SQLAlchemy 的 pg8000 连接）。
- 写：`db_utils.write_dataframe(conn, df, table)` 替代 `df.to_sql`（`df.to_sql` 仅支持 SQLAlchemy 引擎 / sqlite3 连接，pg8000 裸连接不兼容）；内部用 `executemany` 批量 INSERT，`NaN/NaT→NULL`、`datetime→ISO 字符串`。
- 建表数值列：`db_utils.numeric_sql_type(conn)` 返回 `REAL`（SQLite）≈ `DOUBLE PRECISION`（PG）。
- 连接：`db_utils.connect(backend=None, db_path=None)`，`DB_BACKEND=pg/sqlite` 环境变量切换，默认 SQLite 零风险。
- 规则：**禁止**在新增脚本里直接 `df.to_sql` / `import sqlite3` 硬连（`five_leagues.db` 旧库兜底读除外）；占位符用 `?`（PG 端自动转 `%s`）。

**odds_timing.db 表结构：**
- matches: 比赛基本信息 (match_id, home_team, away_team, match_date, league)
- wdl_timing: 胜平负时序赔率 (match_id, timestamp, win_a, draw, win_b)
- handicap_timing: 让球时序赔率 (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose)
- total_goals_timing: 总进球时序赔率 (match_id, timestamp, goals_0~goals_7_plus)
- score_timing: 比分时序赔率 (match_id, timestamp, score, odds)
- match_results: 开奖结果 (match_id, actual_score, actual_wdl, actual_handicap, actual_total_goals)
- import_log: 导入日志

**odds.db 内 Understat 表结构（2026-08-22 新增，第三数据源）：**
- understat_match_team_stats: 比赛级 xG（match_id, 主客队, 比分, xG, forecast 胜平负概率）
- understat_player_xg: 球员级 xG 体系（xG/xA/xGChain/xGBuildup/key_passes）
- understat_shots: 射门级（坐标 X/Y、xG、射门部位/情况/结果）

**联赛命名规范：**
- league 字段：`英超2025-2026赛季`, `西甲2025-2026赛季`, `意甲2025-2026赛季`, `德甲2025-2026赛季`, `法甲2025-2026赛季`
- match_type 字段（odds.db）：与 league 字段保持一致

### 2.3 代码风格

- 缩进：4空格
- 命名：snake_case（函数和变量）、PascalCase（类）
- 注释：docstring 风格
- 类型提示：所有函数必须有类型提示
- 错误处理：try-except 必须记录日志

### 2.4 导入脚本规范

1. 解析 txt/csv 文件，提取比赛和赔率数据
2. 检查是否已存在（避免重复）
3. 先清除旧数据，再插入新数据
4. 同步到 odds_timing.db 和 odds.db
5. 写入 import_log 日志
6. 验证数据完整性

### 2.5 特征工程规范（D-009）

1. 所有新特征必须在 `feature_temporal.py` 的 `PRE_MATCH_FEATURE_CATALOG` 中注册
2. 赛后特征（homeGoals/awayGoals/xG/shots/possession等）严禁进入特征矩阵X
3. 训练前必须调用 `validate_no_leakage(X)` 进行断言式验证
4. 特征属性标记表 `feature_attributes.csv` 随每次训练自动生成
5. 泄露检测基于真实特征矩阵X动态检测，禁止使用硬编码静态列表

---

## 三、经验教训

### 3.1 数据相关 (EXP-001 ~ EXP-006)

| 经验ID | 经验描述 | 发现日期 | 影响 |
|--------|----------|----------|------|
| EXP-001 | 德甲/法甲球队行格式不同（D1 vs 法甲 Ligue 1），解析时需适配 | 2026-08-04 | 导入脚本需按联赛定制 |
| EXP-002 | 比分表头用tab分割，而非空格分割（如 '1 : 0' 被空格拆成3部分） | 2026-08-05 | 比分解析必须用 `.split('\t')` |
| EXP-003 | 赔率时间戳有两种：真实发布时间 vs 导入时间(2026-07)，需过滤 | 2026-08-04 | 同步时过滤 2026-07 前缀的时间戳 |
| EXP-004 | 不同联赛的时序赔率记录数差异大（法甲法甲81条/场 vs 德甲15条/场） | 2026-08-05 | 需检查数据完整性 |
| EXP-005 | NOT NULL 约束问题（如 handicap_timing.hcp_draw 可能为 NULL） | 2026-07-24 | 插入前检查表结构约束 |
| EXP-006 | match_id 格式必须统一，否则无法关联两个数据库 | 2026-07-23 | 映射脚本自动生成标准 match_id |

### 3.2 模型相关 (EXP-007 ~ EXP-010)

| 经验ID | 经验描述 | 发现日期 | 影响 |
|--------|----------|----------|------|
| EXP-007 | h2h_last_result 因运算符优先级 bug 导致标签泄露（1:1映射） | 2026-07-24 | 必须添加括号确保 date 过滤 |
| EXP-008 | 虚假高准确率(98.32%)通常意味着数据泄露 | 2026-07-24 | 回测必须用时间序列交叉验证 |
| EXP-009 | 模型真实准确率(41.67%)低于基线(47.22%)说明预测能力不足 | 2026-07-24 | 需增加赔率特征优化 |
| EXP-010 | 平局预测(11.11%)极差，需特殊处理 | 2026-07-24 | 使用 class_weight 或欠采样 |
| EXP-016 | five_leagues.db仅含3联赛875场，odds.db含5联赛1265场，数据源选择直接决定模型上限 | 2026-08-05 | 训练前必须确认数据源完整性 |
| EXP-017 | 赔率特征为0维的根因：球队名中英混杂+时间戳格式不统一，单一match_id格式无法匹配 | 2026-08-05 | build_odds_features必须支持多格式match_id匹配 |
| EXP-018 | pd.to_datetime()对混合格式时间戳报错，必须使用format='mixed' | 2026-08-05 | 所有时间戳解析统一使用format='mixed' |
| EXP-019 | 集成39维赔率特征后，CV准确率从44.7%提升至48.6%，过拟合差距从0.377降至0.292 | 2026-08-05 | 赔率特征是最强预测信号，必须集成 |
| EXP-020 | XGBoost早停轮数25过小导致欠拟合，best_iteration仅19；增大n_estimators至300后best_iteration达93 | 2026-08-05 | n_estimators和early_stopping_rounds需配合调整 |
| EXP-021 | D-009特征泄露检测发现：当前113维特征实际无泄露（homeGoals/awayGoals等只在df中用于计算标签y，未进入X），启动脚本的硬编码静态检查列表是误报 | 2026-08-05 | 泄露检测必须基于真实特征矩阵X动态检测，而非硬编码静态列表 |
| EXP-022 | feature_temporal.py采用黑名单+白名单双重机制：黑名单明确禁止赛后特征，白名单标记每个特征的时序属性（pre_match/derived_pre/post_match），未分类特征触发告警 | 2026-08-05 | 未来新增特征必须在PRE_MATCH_FEATURE_CATALOG中注册，否则触发unknown告警 |
| EXP-023 | 特征属性标记表(feature_attributes.csv)应包含：序号/特征名/类别/时序类型/是否赛前可用/是否泄露风险/来源函数/描述，便于审计和团队协作 | 2026-08-05 | 特征工程必须有完整文档，不可只靠代码注释 |
| EXP-024 | D-010新增17维赔率衍生特征（凯利指数6+变化率6+市场信心度3+价值投注2），CV标准差从4.3%降至1.6%（模型更稳定），但准确率未提升（48.0%持平），过拟合差距略增0.026，说明存在冗余特征 | 2026-08-05 | 衍生特征与原特征高度相关（如bookmaker_margin=wdl_overround-1线性变换），需D-011特征选择剔除冗余 |
| EXP-025 | 凯利指数公式 Kelly=(p×odds-1)/(odds-1)，其中p为归一化隐含概率，odds为收盘赔率，需clip到[-1,1]防止极端值；>0表示赔率被高估有投注价值 | 2026-08-05 | 赔率衍生特征计算必须做异常值处理 |
| EXP-026 | 赔率变化率=(close-open)/open，需clip到[-1,1]；负值表示该结果可能性上升（市场看好），正值表示看淡 | 2026-08-05 | 动量指标方向解读需统一，避免符号混淆 |
| EXP-027 | D-011三方法融合特征选择：相关性分析(剔除|corr|>0.9)+XGBoost重要性(gain+weight双指标avg_rank)+RFE验证，130→65维降维50%，CV准确率48.0%→49.3%，过拟合差距0.318→0.283，验证集CV标准差1.85%→0.85%稳定性提升54% | 2026-08-05 | 三方法融合比单一方法更稳健，相关性分析先剔除冗余，XGB重要性排序，RFE交叉验证 |
| EXP-028 | XGBClassifier的booster.get_score()在sklearn API下返回的键是原始列名而非f0/f1格式，直接用f'f{i}'映射会全部返回0；必须用model.feature_importances_(sklearn标准)作为gain主通道，booster.get_score()作为补充 | 2026-08-05 | XGBoost特征重要性提取必须双通道：sklearn API + booster API，避免特征名映射bug |
| EXP-029 | 强制保留所有赔率特征(56维)的策略有效：赔率特征是最强预测信号，即使相关性高(如wdl_close_win↔wdl_open_win corr=0.95)也保留两者；剩余配额(9维)从基础/球队特征中按XGB avg_rank选取 | 2026-08-05 | 特征选择需结合业务知识，赔率信号不可丢弃，仅在非赔率特征中做选择 |
| EXP-030 | D-011验证脚本(独立5折TimeSeriesSplit)显示降维后CV标准差从1.85%→0.85%(降54%)，但集成到train_models_v2.py(EnhancedTimeSeriesCV)后CV标准差2.27%，说明两种CV实现差异较大；应以train_models_v2.py的CV为准 | 2026-08-05 | 验证脚本和正式训练的CV实现不同，结果会有差异，对比时需注明CV类型 |
| EXP-031 | SofaScore API的反爬机制是Akamai Bot Manager(TLS JA3指纹)，不是IP限流或HTTP headers，标准requests.get(headers={"User-Agent":...})必定403；必须用curl_cffi(impersonate="chrome")模拟Chrome的JA3/JA3S指纹才能正常返回200；无需注册账号、无需API Key | 2026-08-09 | 采集SofaScore必须用curl_cffi impersonate="chrome"，不能用标准requests |
| EXP-032 | SofaScore incidents接口的事件类型字段是`incidentType`（值为"goal"/"substitution"/"card"）而非`type`（type为None会覆盖成phaseStart/matchEnd）；换人原因是`injury`布尔字段而非reason字符串；不注意会导致进球/换人/红黄牌事件解析为0条。球员进球/yellow/red必须用incidents交叉索引覆盖lineups中的statistics.goals值（lineups的statistics.goals偶尔与实际事件流不一致） | 2026-08-09 | ① 事件类型用incidentType判断；② 换人/进球/红黄牌数据必须以incidents接口为准并构建字典索引反查，不可信任lineups统计字段 |
| EXP-033 | fbref_schema.py的4张fbref表结构可直接复用于SofaScore：fbref_match_id存sofascore event_id、fbref_player_id存sofascore player_id、stats_source='sofascore'区分；但match_player_stats的92个fbref列名与SofaScore的statistics key不直接对应（fbref:passes_completed vs sofascore:accuratePass），必须新增34列SofaScore专有扩展列并用SOFA_TO_FBREF_FIELD_MAP动态映射；stats_json列存储全量原始statistics JSON，作为字段映射失败时的兜底；ALTER TABLE ADD COLUMN在列已存在时会报错，需捕获"duplicate column name"异常 | 2026-08-09 | 跨数据源复用DB schema时：① 用stats_source列区分来源；② 用字段映射表+新增扩展列处理异构字段；③ 扩展列ALTER必须有异常忽略重复列；④ stats_json必存，便于未来从历史数据解析新字段 |
| EXP-034 | 比分赔率特征覆盖率仅23.0%（1209/5252场），根因：score_history表使用中文球队名（如"利物浦"），matches表使用英文球队名（如"Liverpool FC"），match_id格式不兼容；通过双向映射（TEAM_NAME_MAP + cn_to_en反向映射）生成多个候选match_id可提升覆盖率，但TEAM_NAME_MAP覆盖不全（当前约80队）导致仍有大量比赛无法匹配 | 2026-08-10 | 比分赔率特征覆盖率提升需要：①扩充TEAM_NAME_MAP至覆盖所有历史球队名称变体；②考虑模糊匹配（编辑距离/拼音相似度）；③记录未匹配案例用于针对性补充映射 |
| EXP-035 | 非线性变换特征（29维）中包含大量与原始赔率特征高度相关的衍生特征（如log(赔率)、sqrt(赔率)与原始赔率线性相关），D-011特征选择会自然剔除这类冗余特征；实际保留的非线性特征维度取决于特征选择阈值，不强制保留可避免引入噪声 | 2026-08-10 | 非线性变换特征设计原则：①优先选择与原始特征非线性关系强的变换（如熵、基尼系数、交互项）；②简单数学变换（log/sqrt/平方）可能被D-011剔除，不应强制保留；③跨特征交互（如概率×凯利）比单特征变换更有价值 |
| EXP-036 | 新特征模块集成到现有管线需遵循固定模式：①创建独立模块（如score_features.py）；②在feature_utils.py的build_all_features()添加include_xxx参数；③在feature_temporal.py的PRE_MATCH_FEATURE_CATALOG注册所有新特征（类别+时序类型+来源函数）；④在feature_selection_d011.py中决定是否强制保留（赔率衍生类强制保留，变换类不强制）；⑤在train_models_v2.py中启用开关并更新特征统计打印 | 2026-08-10 | 五步集成模式确保新特征：①通过泄露检测；②在D-011中正确分类；③在训练日志中可见；④可独立开关控制；⑤不破坏现有特征管线 |
| EXP-037 | Understat 数据端点 getMatchData/{id} 与 getLeagueData/{slug}/{season} 需带 Referer + X-Requested-With 头，否则 404；未开赛比赛(isResult=false)无 rosters/shots 数据需自动跳过；xG/xA/xGChain/xGBuildup 与射门坐标(X/Y)是 Understat 独有字段，弥补 FBref/SofaScore 缺口 | 2026-08-22 | 采集 Understat 必须带双请求头、用 isResult 过滤未开赛、404 不重试；三张独立表存原始 xG 体系（比赛级/球员级/射门级） |
| EXP-038 | build_team_features 主循环对每场比赛调用 `home_hist[home_hist['date'] < match_date]` 全量布尔过滤（主/客各1次 + 对手至多20次），造成 O(n·m) 的 DataFrame 反复分配，全量 14362 场耗时 480.7s；修复：复用 precompute_team_stats 已产出的「按日期升序 + RangeIndex」每队统计表，预提取每队日期 int64 数组构建 lookup，用 `np.searchsorted(side='left')` O(log n) 定位「date<md 最近一场」，并以 `range(len(df))` 替代 `iterrows`；因缓存经 merge 后为 RangeIndex 且严格升序，布尔前缀过滤与 `.iloc[:k]` 完全等价、语义无损 | 2026-08-30 | 特征构建性能优化：全量 build_team_features 480.7s→55.9s（88%提速）；正确性三重校验 0/166 + 0/500 + 0/500 零差异；时序「最近一场」查找优先 searchsorted 而非布尔过滤 |
| EXP-039 | build_all_features 其余模块（Elo/D-013 时序赔率/比分赔率/赔率特征 build_odds_features/三通道对齐 build_match_alignment）的共性瓶颈是「逐行/逐组/逐场」循环与重复 DB 查询/大表 JOIN；统一替换为 numpy 向量化 + `np.searchsorted` O(log n) + 一次性批量加载 + 内存 set/dict 对齐。收益：Elo 29.34s→1.05s、比分赔率 59.65s→28.47s、赔率特征 63.69s→4.82s、D-013 4.58s，端到端 build_all_features 瓶颈消除 | 2026-08-30 | 特征构建二次优化：逐模块 profiling 定位 O(n·m)/iterrows，numpy 向量化 + searchsorted + 整表加载替换；逐字段三重校验零差异 |
| EXP-040 | 总进球赔率特征 legacy bug：`_build_odds_features_legacy` 在末条 total_goals 快照 `goals_3..goals_7_plus` 含 NULL（未开赛/部分数据场次，仅存 under 市场 goals_0/1/2）时，`total_prob=sum(...)` 变 NaN 使归一化跳过，但 `under_25/over_25/tg_expected` 仍在 `if total_prob>0` 块外计算，产出未归一化垃圾值（如 tg_under_25_prob=8.98）；向量化版用 `valid=total>0` 守卫，`total=NaN→valid=False→返回 NaN` 后中位数填充，语义正确。影响范围仅 67 场 partial-NaN 行 + 无数据行中位数微移 | 2026-08-30 | 计算派生特征时，归一化守卫（分母>0）与派生值赋值必须在同一 if 块内，否则部分缺失数据会产出未归一化值；向量化时用 `np.where(matched & valid, ..., np.nan)` 统一守卫 |
| EXP-041 | matches.league 列被活跃写入口（prediction_db_writer.py 等）漏写——联赛信息完整编码在 match_type（如「英超2026-2027赛季」）但 league 列为 NULL，导致 `load_completed_matches` 等用 `league IS NOT NULL` 硬过滤的下游消费端断粮（08-25 后完赛 83 场中 58 场被丢弃）。修复：一次性 `_backfill_league.py` 按 match_type 前缀回填 94 行 + 读取端新增 `_derive_league()` 从 match_type 兜底派生（C-20260911-023） | 2026-09-11 | 写入口分散时，读取端从冗余编码字段兜底派生是单点根治；新写入口应同时写 league 与 match_type；下游过滤慎用 `IS NOT NULL` 硬过滤，优先按业务主键（actual_score）过滤 + 派生兜底 |
| EXP-042 | shadow 配对双跑（同一 match_id 记录 control/treatment 两臂、treatment 永不真实 serve）场景下，门禁必须用配对 McNemar（`ab_test_framework.analyze(paired=True)`）；复用线上 served 分流 z 检验会因 treatment served_n=0 恒报 insufficient-sample。线上真实分流场景沿用 paired=False 原口径不变（C-20260911-022） | 2026-09-11 | 离线 shadow/回放对照 = 配对样本 → 配对检验；线上分流 = 独立样本 → 两比例 z/Mann-Whitney；统计口径必须与实验设计匹配 |
| EXP-043 | 500.com 强刷可能出现公司明细/投注表有真实数据，但 `odds500_ouzhi_summary.company_count` 和摘要赔率字段为 NULL；“采集命令完成”不能等同于摘要有效，必须同时校验 summary 核心字段、company 明细和 betting 数据。报告生成器还会排除已开赛场次，补采后不可直接用全量生成覆盖历史赛前报告 | 2026-09-13 | 500.com 48场强刷：company 明细均有30行、betting各1行，但 summary company_count 48/48 NULL；SofaScore 当日特征77/77有效；需修复摘要写入/解析和历史报告回写策略 |
| EXP-044 | SofaScore 采集器字段映射误把 `accuratePass`（准确传球**数**，整数）映射到 `pass_completion_pct`（**百分比**，0-100）列，导致 `accurate_pass_sofa` 长期未赋值、`sofa_pass_sr_5g` 分子缺失触发 `PASS_SR_MIN` 门禁，报告误报「未采集」；修复：映射改 `accuratePass`→`accurate_pass_sofa` + 迁移脚本 `repair_pass_sr_mapping.py` 把误存值迁回并清空污染（C-20260915-005） | 2026-09-15 | 跨源字段映射必须核对「计数 vs 比例」语义；同一列被两个源以不同单位复用时（FBref 存百分比 / SofaScore 存计数）应拆分为独立列，避免单位污染 |

### 3.3 流程相关 (EXP-011 ~ EXP-015)

| 经验ID | 经验描述 | 发现日期 | 影响 |
|--------|----------|----------|------|
| EXP-011 | 每日对话前需检索所有日志文件，避免遗漏上下文 | 2026-07-23 | 建立日志检索流程 |
| EXP-012 | 任务完成后必须更新所有相关日志文档 | 2026-07-23 | optimization_log + change_log + prompt_template |
| EXP-013 | 阶段切换时必须更新 prompt_template.md | 2026-07-23 | 保持上下文模板与当前阶段同步 |
| EXP-014 | 数据变更必须先备份再操作 | 2026-07-24 | 防止数据丢失 |
| EXP-015 | 多步骤任务必须用 TodoWrite 规划 | 2026-08-04 | 确保任务完整性 |

---

## 四、记忆系统规则

### 4.1 对话策略 (MEM-001 ~ MEM-006)

| 规则ID | 规则内容 |
|--------|----------|
| MEM-001 | 每次新会话开始时，必须读取 prompt_template.md 获取当前上下文 |
| MEM-002 | 每次任务完成后，必须更新 optimization_log.md 和 change_log.md |
| MEM-003 | 关键决策必须记录到 key_decisions.md |
| MEM-004 | 硬性规则变更必须更新本文件 (project_memory.md) |
| MEM-005 | 每日对话前必须执行日志检索流程（见第五章） |
| MEM-006 | 每一项优化必须同步完成实现、验证和文档更新；验证失败或中止也必须记录实际状态、证据和遗留风险，文档未同步不得标记为已完成 |

### 4.2 日志更新规则 (LOG-001 ~ LOG-006)

| 规则ID | 规则内容 |
|--------|----------|
| LOG-001 | optimization_log.md：记录每日完成的任务和状态变化 |
| LOG-002 | change_log.md：记录每次代码/参数/配置变更 |
| LOG-003 | key_decisions.md：记录重要决策及其状态 |
| LOG-004 | prompt_template.md：更新阶段标识、已完成项、待办事项 |
| LOG-005 | project_memory.md：更新经验教训和规则 |
| LOG-006 | import_log 表：数据导入必须写入日志 |

### 4.3 快照机制

每次重要会话结束时，生成会话快照：
- 会话ID和时间戳
- 完成的任务列表
- 关键决策和代码变更
- 下次会话的待办事项

---

## 五、每日对话检索清单

### 5.1 检索流程

每次新对话开始时，按以下顺序检索：

```
1. 读取 project_memory.md（本文件）
   - 获取硬性规则
   - 获取工程约定
   - 获取经验教训

2. 读取 prompt_template.md
   - 获取当前阶段标识
   - 获取已完成优化项
   - 获取性能基准值
   - 获取待办事项

3. 读取 optimization_log.md
   - 查看最新任务完成情况
   - 查看问题修复进度

4. 读取 change_log.md
   - 查看最近的代码/配置变更
   - 了解变更原因和关联决策

5. 读取 key_decisions.md
   - 查看关键决策状态
   - 待实施决策列表
```

### 5.2 快速检查清单

- [ ] 当前阶段和进度已了解
- [ ] 已完成的优化项已记录
- [ ] 关键参数配置已确认
- [ ] 性能基准值已更新
- [ ] 待办事项已明确
- [ ] 硬性规则已检查
- [ ] 变更日志已同步
- [ ] 决策日志已更新

### 5.3 关键文件索引

| 文件 | 路径 | 用途 | 更新频率 |
|------|------|------|----------|
| project_memory.md | 项目根目录 | 规则/约定/经验 | 规则变更时 |
| prompt_template.md | docs/ | 上下文模板 | 每次阶段切换 |

---

## 七、当前模型最新状态（2026-09-12）

> **最后更新**: 2026-09-15
> **最新训练**: ✅ 20260908_004604（全量特征集 **254 维** selected_features，含 slim_odds+ts_odds+consensus+T-007 球员 lag 等）
> **最新模型资产**: assets/lgb_model_20260908_004604.pkl, assets/xgb_model_20260908_004604.pkl, assets/scaler_20260908_004604.pkl, assets/selected_features_20260908_004604.pkl, assets/draw_calibrator_params_20260908_004604.json, assets/feature_bridge.json（训练↔服务端 254 维特征桥接，C-20260910-011）
> **状态唯一权威**: P0~P2 优化项状态以 docs/模型优化评估报告_v2.0.md 为准（C-20260911-020 六文档职责收敛）
> **09-12 数据侧要点**: ①500.com 采集器已升级 curl_cffi + EdgeOne 手动 Cookie（C-20260912-001）；②亚盘刷新通道修复，26/27 完赛场结算盘口 74/74 = 100%（C-20260912-003）；③统一报告新增第十二章球员/阵容（pa_*）与第十四章积分/战意两通道（C-20260912-002）。详见 §6.3.4。

### 6.1 模型架构

| 维度 | 模型 | 特征维度 | 说明 |
|------|------|:------:|------|
| WDL 胜平负 | 5基础模型 (DixonColes + Elo + XGB + LGB + 贝叶斯) + LR meta-learner | **254维** | slim_odds=True（精简赔率30维）+ ts_odds=True（+33：时序22+比分8+扩展3）+ consensus_odds=True（+10）+ 球员 lag/其他全量特征集至 254 维；Stacking 优先 LR meta-learner（OOF RPS 0.1984）、回退固定权重；训练↔服务端 254 维 CI 强制对齐（C-20260910-004）+ feature_bridge 桥接 110 维真实值（C-20260910-011） |
| 统一引擎（比分/总进球） | DixonColes 引擎全量投产 | - | C-20260909-008 全量切换（ScorePredictor/TotalGoalsPredictor 走 DC 引擎，四维统一导出）；P1-C' 双基线补对照四指标完全一致（C-20260910-008） |
| T-005 v3 让球 | 两阶段 LGBMClassifier (draw_detector + direction_predictor) | 71维 | 温度 T=1.0（argmax），走水召回率 42.2% |
| T-006 v5 比分 | Poisson(Dixon-Coles) + 去水 WDL 数值求解 λ + IPF 重加权 + **生产 WDL 锚定** | - | v5 替代 v4（C-20260908-022）：Top-1 13.14%（v4 10.70%）、Top-3 30.44%、Top-5 45.47%，WDL 不劣化；生产锚定读 model_predictions WDL_home/draw/away（缺失回退去水）；⚠️ v5 仅接入 C2 回测模块 `score_prediction_module.py`，实时赛前报告路径 `generate_unified_report→prediction_core.ScorePredictor` 仍为 **T-006 v4**，报告 MODEL_HEADER/比分方法 应标 v4（2026-09-14 端到端验收修正） |
| P1-B 贝叶斯增量（shadow） | EKF 增量更新 attack/defense 后验 | - | 离线滚动 shadow 与在线 Node 预测解耦（`run_shadow_incremental.py`）；τ 逐联赛标定（全 0.01）；生产仍 serve control，全量重滚后 5/5 后验 STABLE、意甲配对门禁 SIGNIFICANT-WIN（p=0.0484/净胜+31）、其余 NO-SIGNIFICANT-DIFF（C-20260910-015 ~ C-20260911-025） |
| 总进球 | Poisson λ + 总进球赔率融合 | - | λ 动态调整 |

### 6.2 关键参数

| 参数 | 值 | 说明 |
|------|:--:|------|
| slim_odds | True | 精简赔率 30维（基础） |
| ts_odds | True | 时序/比分赔率 33维（D-013 22 + T-003.1 8 + 扩展 3）+ 联赛 z-score 归一 |
| consensus_odds | True | 多博彩公司赔率一致性 10维（全量特征集合计 254维） |
| Train-Serving 对齐 | 254 维 | selected_features_20260908_004604.pkl = feature_scaler_params.js feature_names；CI 强制对齐测试 tests/verify-feature-alignment.js；feature_bridge.json 桥接 110 维真实值（C-20260910-004/011） |
| unified_engine | enabled | DixonColes 引擎全量投产（C-20260909-008），legacy 可回退（USE_UNIFIED_ENGINE=0） |
| P1-B shadow τ | 0.01（逐联赛统一） | 英超按 Acc/RPS 权衡取 0.01（C-20260911-018/019）；生产切换前需连续 K=3 轮稳定 + min-n≥60（C-20260911-024） |
| T005V3_TEMPERATURE | 1.0 | 温度缩放取消（argmax） |
| T006_MC_SIMULATIONS | 500 | 蒙特卡洛模拟次数 |
| WDL_TEMPERATURE | 0.8 | WDL 温度缩放 |
| 英超独立模型 | epl_reference | 从 Stacking 移除，独立输出参考 |

### 6.3 数据源

| 数据源 | 路径 | 大小 | 覆盖 |
|--------|------|:--:|------|
| odds.db | data/odds.db | 1,669MB | 14,521 场比赛（league 空值 0，C-20260911-023 已回填；26/27 新写入行仍有 32 行 NULL 待下轮回填），赔率覆盖率 98.3%（双通道） |
| sofascore_team_features | data/odds.db (表) | 18,363行 | 98 字段（68 维 sofa_* + 24 维 pa_* 球员可用性），5 大联赛（英超3,940/西甲3,877/意甲3,876/法甲3,564/德甲3,106；2026-09-12 实测） |
| match_player_stats | data/odds.db (表) | 730,128行 | 2016-08~2026-09 跨 11 赛季 sofa+fbref；09-12 补采巴伦西亚 3 场评级 + 6 队 16 场新赛季统计（C-20260912-001） |
| Understat 三表 | data/odds.db (表) | 529369+451488行 | 五大联赛×10赛季（16/17~25/26）全量完整 |
| 500.com 五表 | data/odds.db (表) | match 19,790 / summary 19,761 / company 542,546 行 | 16/17~25/26 十季 18,038 场 100% + 26/27 五联赛 1,752 场赛程全入库；采集器已升级 curl_cffi + EdgeOne Cookie（C-20260912-001） |
| Sporttery 时序赔率 | data/odds.db (表) | wdl_history / handicap_history / total_goals_history / score_history | 覆盖 16/17~25/26 全部 10 季（已结束赛季 100% 完成），26/27 随赛程推进（sporttery_live_collector 定时采集） |
| 26/27 赛季覆盖 | - | 184场(matches表，54 场完赛有比分) | 赛果采集滞后：完赛仅英超15/西甲19/法甲14/意甲4/德甲2；积分榜/战意通道带不完整保护（C-20260912-002） |
| 赔率 TXT | data/{联赛}2026-2027赛季完整时序赔率.txt | - | 意甲4/英超5/西甲8/法甲4 场 |
| SofaScore 26/27 覆盖 | sofascore_team_features / match_player_stats 表 | 赛前特征已预生成 | 09-12 补采 19 场（巴伦西亚3+6队16），4 场赛前报告完整度 100%；后续轮次仍依赖赛后采集链路 |
| Understat 26/27 覆盖 | understat_match_team_stats 表 | 22场 | 法甲1/西甲20/英超1，待补采 |
| 500.com 26/27 亚盘 | odds500_match 表 | 113/1,752 场有盘口 | 74 场完赛结算盘口 100% 补齐（意甲38/西甲28/英超19/法甲19/德甲9）；未开赛场次临近开赛前跑 `--refresh-odds` 补盘（C-20260912-003） |

#### 6.3.1 赔率导出 Excel 文件（派生数据，非数据源）

`data/` 下两个 Excel 文件均由 `scripts/export_odds_excel.py` 从 `odds.db` 导出的**人工审阅用报表**，不是模型训练的数据源。代码中仅引用 v2 版本作为输出路径。

| 文件 | 大小 | 引用 | 说明 |
|------|:--:|:--:|------|
| `odds_data_export.xlsx` | 1.08MB | **无**（旧版，已废弃） | 缺少「比分赔率时序_样例」sheet，其余内容与 v2 完全一致 |
| `odds_data_export_v2.xlsx` | 1.25MB | `export_odds_excel.py#L15`（OUTPUT） | 当前有效版本，含 7 个 sheet（比赛总览/比赛明细/WDL/让球/总进球/比分赔率时序样例/WDL时间点分布） |

**两个文件内容对比（2026-08-24 查验）**：
- 比赛总览 / 比赛明细 / WDL时序赔率_样例 / 让球时序赔率_样例 / 总进球时序赔率_样例 / WDL时间点分布 — 行数、内容**完全相同**
- 唯一区别：v2 多了「比分赔率时序_样例」sheet（5001 行，对应 `score_history` 表）
- 结论：`odds_data_export.xlsx` 是 v2 之前的旧输出，已无代码引用，可安全删除

#### 6.3.2 文档自动更新机制（2026-08-26 固化）

> 用户要求「以后每次优化都自动更新」。以下为**硬性规则**，每次完成代码/参数/配置/数据/文档优化后必须执行：

1. **变更日志**：在 `docs/change_log.md` 新增 `C-YYYYMMDD-NNN` 变更记录（含变更前/后/原因/验证结果），并同步更新 §5 变更统计表。
2. **数据采集进度**：涉及数据源时，同步更新 `docs/data_collection_progress.md`（完成情况 + 完整度标注）。
3. **项目记忆**：涉及规则/约束/数据源/经验时，同步更新 `docs/project_memory.md` 对应章节。
4. **技能文档**：涉及采集器/脚本时，同步更新 `.trae/skills/local-football-scraper/SKILL.md`。
5. **顺序**：按 P0（阻塞性过时）→ P1（重要差距）→ P2（补充完善）优先级执行，不遗漏关键更新。

#### 6.3.3 自动化调度与生产运维状态（2026-09-11 实测）

Windows 计划任务 5 项全部 Ready：

| 任务 | 触发 | 内容 | 关键状态 |
|------|------|------|----------|
| T005v3_AutoRetrain | 每日 08:00 | 自动重训触发器守护（三重触发 + 性能门禁） | Ready |
| SoccerModel_ConceptDrift | 每日 08:30 | P0-C 概念漂移检测（`concept_drift_gate.py`，仅告警禁止自动重训） | Ready，Last Result 0 |
| SoccerModel_PostMatchReview | 每小时 | P0-A 复盘闭环（`run_post_match_pipeline.py` A2→A6，--catch-up --limit 30） | Ready；post_match_review 77 条 |
| SoccerModel_P0ELiveTrial | 每日 12:00 | P0-E 实盘验证六步编排（`run_daily_p0e.py`，--auto-commit） | Ready；台账 0/300（单日候选 0 属预期） |
| SoccerModel_P1BShadow | 每日 13:00 | P1-B shadow 滚动吸收（`run_shadow_incremental.py --roll`；`setup_shadow_scheduler.py` 管理，`/ru SYSTEM` 非交互登录） | Ready；shadow ab_test_log 28,834 行（配对双跑样本） |

> 运维注意：① P1BShadow 为 `/ru SYSTEM`，若 Python 依赖装在用户 site-packages 可能读不到（需系统级安装或改 `--ru`）；② 任务默认 No Start On Batteries，电池供电会跳过；③ 重新注册用 `scripts/setup_shadow_scheduler.py`（支持 --query/--ru/--password/--st/--python，C-20260911-025）。

#### 6.3.4 500.com EdgeOne 反爬与亚盘刷新运维（2026-09-12，C-20260912-001/003）

1. **反爬本质是 TLS 指纹，不是 JS**：500.com 部署腾讯云 EdgeOne（JS 挑战 + Security Verification 两层），`requests` 的 JA3 指纹被识别。解法为 `curl_cffi`（`Session(impersonate="chrome")`）+ 手动 Cookie，**不是**上 headless 浏览器。
2. **Cookie 三层凭证缺一不可**：`__tst_status`（固定常量）、`EO_Bot_Ssid`、`EO-Bot-Captcha-Token`（真人勾选后写入，唯一放行凭证）；Token 与 UA 绑定，JSON 中需用 `__user_agent` 键记录导出浏览器（Edge 152）UA。文件 `data/cookies_500.json`，采集器 `--cookies` 指定。**Cookie 需每日人工从自己的 Edge 导出刷新**（用户熟悉的手动 cookie 流程，不引入持久化后台方案）。
3. **高频请求会触发 EdgeOne 升级防护**（直接跳过人机层、加重 IP 风控），采集需保持 `--delay`、避免连续探测。
4. **亚盘刷新命令**（只走 getmatch 赛程接口，不逐场抓页面）：
   `python collection/final_500_collector.py --refresh-odds --season 26/27 --rounds 1-8`
   完赛场（status=5）结算盘口全量覆盖并补比分/纠正状态；未开赛场仅在库内盘口为空时补齐。临近开赛每日/赛前数小时重复跑即可，幂等。
5. **完整度口径防复发**：500.com 维度完整度 = 有行 **且** company_count>0/有公司明细；空壳占位行（company_count=0）计缺失（generate_unified_report.py L566-571）。
6. **空壳 = 反爬拦截，不是源站无数据（2026-09-17 修正，C-20260917-001）**：投注页全 `-` / 必发成交为空 / company_count=0 → `anti_bot_blocked`（FAIL），必须刷新 Cookie 重采；**仅当源站真实业务无数据才判 `source_no_data`（WARN）**。二者不可混用。
6. **SofaScore 评级覆盖率教训**：球员 `rating` 缺失被 `fillna(0)` 参与加权会把球队评分拉成 2.x 畸变（巴伦西亚 2.80 事件）。补采用 `scripts/backfill_valencia_ratings.py` / `backfill_recent_player_stats.py`（INSERT OR REPLACE 幂等），补后须删旧特征行重跑 `features/incremental_sofascore_features.py`；长期需在特征聚合前加 rating 覆盖率校验（<80% 告警）。

### 6.4 已知限制

- 赫尔城 vs 曼联（周六007, 2026-08-22 19:30）：英超 TXT 中无此场赔率数据，无法预测
- TXT 源数据球队名为缩写，已通过 TEAM_NAME_MAP 补全映射（C-20260823-016）
- 西甲 TXT 中 R. Racing Club vs Villarreal 无法解析（队名含空格+点号）
- TXT 文件仅支持单赛季格式（2026/2027 Regular Season 第X轮），跨赛季数据需另行处理
- 英超/西甲 26/27 赛季首轮：sofascore_team_features 无历史数据，LGB/XGB fallback 到 Poisson 家族 Stacking（C-20260823-021）
- 法甲/意甲 26/27 赛季首轮：有历史 SofaScore 数据（3-4行），ML 模型可正常推理
- 报告第十二章「伤病/缺阵」为代理指标：pa_availability/pa_missing_impact 基于历史出场连续性推断，**不是官方伤病/停赛名单**；真实名单需新增 SofaScore/Transfermarkt 伤病爬虫（C-20260912-002）
- 第十四章积分榜依赖 matches 表赛果：26/27 赛果采集滞后（09-12 仅 54 场完赛）时章节自动显示不完整警告、战意显「—」；赛果补齐后自动恢复，无需改码
- 26/27 亚盘：未到开盘窗口的场次（约第 5 轮以后）handicap 为空属正常，需临近开赛重复执行 `--refresh-odds`

### 6.5 阶段3 P2/P3 中长期剩余项落地（2026-09-15，C-20260915-007~013）

按《基于预测报告发现的问题.txt》v1.0 尚未落地的 P2/P3 项，新增六个诊断/监控/回测模块（`scripts/`）并固化 pytest 口径（`tests/test_p2p3_modules.py`，22 passed）：

| 模块 | 问题项 | 脚本 | 关键结论 |
|------|--------|------|----------|
| 概率校准分档监控 | P2-03 | `probability_calibration_monitor.py` | 5% 分桶；平局档位加权偏差 +1.06pp、超阈值 5 档（系统性低估） |
| 双轨回测系统 | P2-06 | `dual_track_backtest.py` | 轨道A 内核 RPS=0.1978/LogLoss=0.9907/Acc=52.87%/ECE=3.73%；轨道B EV决策 ROI=-5.42%、最大回撤 5.47%、最长连亏 16 场 |
| 风险监控 | P3-02 | `risk_monitor.py` | 触发「最长连亏 16 场」「EV偏差 +26.5pp（EV 被系统性高估）」告警 |
| 低比分低估诊断 | P2-02 | `low_score_diagnosis.py` | 11144 场：0-0/1-0/0-1 低估 +1.79/+4.36/+3.02pp，为 Dixon-Coles/ZIP 迭代提供基线 |
| 数据源冲突检测 | P2-04 | `data_source_conflict_detector.py` | SofaScore 基本面 vs 500 市场信号；校准后冲突率 52.7%→33.7% |
| 战意量化修正 | P2-01 | `motivation_adjustment.py` | 7 条攻/防系数规则 + 消融验证 |

- 重要现实：轨道B（EV>0 择场）平注 ROI 仍为负，与历史「系统性高估」结论一致 —— EV 决策引擎在当前模型×市场组合下无统计显著正 edge，需校准/训练端根治（参见 3.107~3.116 历史闭环）。
- 六个模块均为独立诊断/回测脚本，不接入实时预测链路，不影响线上 WDL/比分推理。

### 6.6 阶段3 P2/P3 深水项落地（2026-09-15，C-20260915-014~016）

承 6.5 六模块后，继续落地三项「真·深水」中长期项（pytest 22→33）：

| 深水项 | 问题项 | 脚本 | 关键结论 |
|--------|--------|------|----------|
| ZIP 零膨胀泊松比分模型 | P3 | `zip_score_model.py` | 11144 场网格 0.00~0.40：低比分校准最优 k_scale=0.3（≤1 偏差 +8.56→+1.02pp）但 LogLoss 劣化 +0.1056，LogLoss 最优 k_scale=0；**ZIP 无增益，不进入生产**。根因=两队进球负相关（Dixon-Coles ρ），单队独立零膨胀无法建模跨队相关 → 建议调 ρ 或升级双变量泊松 |
| 球员推算首发准确率复盘 | P2 | `player_lineup_accuracy.py` + `player_availability_features.predict_xi_players()` | 小样本 200 场英超平均命中率 65.83%/中位数 63.64%；Crystal Palace 83.73% 最稳、Wolverhampton 51.67% 最易翻车；全量 18471 场×5 联赛落库 `player_xi_accuracy_review`（source=inferred，为官方源基线） |
| 官方/推算伤病区分 + 数据源升级 | P3 | `player_injury_source.py` | `InjuryRecord`(source∈official/inferred) + `player_injuries` 表 + `apply_official_injuries()`（官方缺阵覆盖推算，输出 coverage_of_official）；官方采集探测：transfermarkt 直连 405(Cloudflare)/premierleague 404，需 Playwright 或用户 Edge cookies |

- **低比分低估根因（重要升级结论）**：不是单队零膨胀不足，而是**两队进球负相关**未建模。生产 v5 网格（含 Dixon-Coles ρ=-0.15）仍是 T-006 比分输出锚点，下一步优先网格搜索 ρ∈[-0.30,-0.10] 或升级 bivariate Poisson，而非 ZIP。
- **伤病/首发特征可信度基线**：推算首发整体命中率 ~66%，翻车球队（Wolverhampton/Sunderland/Burnley/Leeds 等 ~55-59%）赛前应下调 pa_*/首发特征置信度权重；官方伤病源一旦接入，`apply_official_injuries` 可校正推算并量化 coverage_of_official。

---

## 六、v3 时代新增经验教训（2026-08-12 补充）

### 6.1 T-005 让球胜平负预测经验

1. **数据扩充是关键**: 赔率反推盘口线将样本从242场扩充至3915场（16倍），CV从48.33%提升至55.37%。逻辑回归反推盘口线标签准确率92.3%，是数据扩充的有效方法。
2. **走水过预测根因**: class_weight='balanced' 从源头导致走水概率偏高，需 ratio=1.5 调整。组合优化（ratio=1.5 + T=2.150 + 动态阈值）使走水预测率从66.9%降至19.6%。
3. **规则引擎副作用**: apply_rule_adjustments() 启发式规则在数据量扩大时累计放大走水信号，导致走水率从16.7%→53.3%。方案A：USE_RULE_ENGINE = False，仅保留核心模型。
4. **对手调整Lag特征价值**: 24维对手Lag特征贡献57.1%决策权重，是走水召回率提升的关键。4组特征设计（对手实力分层+盘口线类别+H2H交锋+市场信号）覆盖走水预测的多维度。
5. **性能优化模式**: O(N²) apply → merge_asof 批量对齐 + groupby+rolling 向量化，是大数据量Lag特征计算的标准优化路径。

### 6.2 自动重训触发器经验

1. **静态配置需执行引擎**: deploy_trigger.flag 仅定义配置，必须配合 retrain_trigger_runner.py 执行引擎才能真正触发重训。
2. **数据触发需组合检测**: 单一表记录数检测不可靠，必须组合 matches 表计数 + handicap_history/wdl_history 最新日期哈希，才能准确检测新数据入库。
3. **性能门禁必须强制**: 仅当 passed=True 或 force=True 时才更新线上模型，防止性能退化的模型上线。
4. **Windows计划任务是定时触发关键**: 通过 schtasks 注册 T005v3_AutoRetrain 任务，每日8:00执行守护进程，确保定时触发在Windows环境生效。
5. **状态持久化必备**: trigger_state.json 记录上次检查时间、重训次数、成功率、最近指标，是触发器可靠运行的基础。

### 6.3 文档同步经验

1. **三时代层级识别**: 10份核心文档可分为 pre-ML（54场JS专家系统）/v2（1265场60维LGB）/v3（5252场114维多任务）三个时代，需按时代层级系统性同步。
2. **P0/P1/P2 优先级排序**: 阻塞性过时（P0）→ 重要差距（P1）→ 补充完善（P2），按优先级顺序执行避免遗漏关键更新。
3. **文档版本号管理**: 每次重大更新需提升文档版本号（如 v1.0→v2.0），并在文末记录更新说明，保持文档可追溯性。
4. **跨文档一致性**: 性能指标、阶段状态、模型配置需在 prompt_template.md、CONVERSATION_WORKFLOW_GUIDE.md、PROJECT_DELIVERY_REPORT.md、model_optimization_plan.md 四份文档间保持一致。

### 6.4 v3 时代新增硬性规则

1. T-005 v3 模型必须使用 ratio=1.5 + T=2.150 + 动态阈值 + USE_RULE_ENGINE=False 配置
2. 自动重训触发器必须配置三重机制（定时/数据/周期）+ 性能门禁（走水召回率≥0.30，预测率偏差≤0.02）
3. 对手调整Lag特征必须通过 shift(1) + rolling(window) 防泄露机制验证
4. trigger_state.json 必须持久化记录触发器运行状态
5. 文档同步必须按 P0→P1→P2 优先级顺序执行
6. 跨文档性能指标必须在 prompt_template.md/CONVERSATION_WORKFLOW_GUIDE.md/PROJECT_DELIVERY_REPORT.md/model_optimization_plan.md 间保持一致

---

---

## 七、v4 时代新增经验教训（2026-08-23 补充）

### 7.1 过度优化修复经验

1. **联赛分档阈值过拟合（多重比较问题）**: 5联赛×6网格点=30次实验选最优，本质是"噪声的最大值"而非信号。全局最优 0.90 仅 +0.09pp（噪声区间），联赛分档的 +1pp 增益下赛季很可能消失。**正确做法**：嵌套CV（外层时间分割+内层网格搜索），或加开关搁置、用新赛季前瞻性验证。

2. **赔率特征精简方法论**: 135维赔率特征（64.6%）中大量 lag/rolling/标准差衍生特征在拟合噪声。A/B测试（5折时间序列CV）显示：精简到30维核心特征后，准确率仅 -0.48pp，但平局召回率 +1.55pp，特征构建速度 1.6x，训练速度 1.8x。**精简收益 > 成本**，核心保留：WDL/HCP 隐含概率 + 凯利指数 + 赔率变化率 + 市场置信度。

3. **pandas frame.insert 碎片化是特征构建性能瓶颈**: 逐个 `frame.insert` 每次触发 DataFrame 内存重分配，52列球员特征产生大量 PerformanceWarning，是特征构建耗时的核心瓶颈。**修复**：改为 `pd.concat` 批量拼接，精简特征构建 318s→210s（省 108s）。

4. **Monte Carlo 3000→500 零风险**: 统计精度损失 <0.3%，比分预测提速 6 倍。**对8×8 Dixon-Coles 修正后的 Poisson 矩阵，Monte Carlo 仅用于验证，解析矩阵已是精确概率分布**。

5. **三重校准叠加（Platt + T=0.8 + 决策阈值）扭曲概率**: 只保留 Platt Scaling 一层，温度 T 回退 1.0，ECE 校准改善。

6. **伪集成（固定权重 Stacking）**: 6模型中 3个 Poisson 变体共享同一 λ，输出高度相关。砍到 4模型（DC + XGB + LGB + Elo），减少冗余推理。

7. **英超独立模型过度拟合**: 1141 样本训 205 维（样本/特征比 5.6:1），CV 47.25% 低于全局 53.99%。从 Stacking 移除，改为独立参考输出。

### 7.2 v4 时代新增硬性规则

1. 特征构建必须使用 `pd.concat` 批量拼接，禁止逐个 `frame.insert`（避免 DataFrame 碎片化）
2. 赔率特征默认使用精简版（30维）而非全量（135维）；全量版仅用于 A/B 对比
3. 联赛分档阈值默认关闭（argmax 模式），保留开关供前瞻性验证
4. 温度缩放默认 T=1.0（等价 argmax），不使用手工 T 值
5. Monte Carlo 模拟次数默认 500，不使用 3000+
6. Stacking 基础模型为 5 个（DC + Elo + XGB + LGB + 贝叶斯），由 LR meta-learner 融合（`apply_stacking_meta_learner`，缺任一模型回退固定权重），英超独立模型不参与集成
7. SofaScore 数据写入 sofascore_team_features 表前必须调用 normalize_team_name 归一化队名（C-20260823-024）

---

## 八、报告完整性门禁时代经验（2026-08-31 补充）

### 8.1 玩法缺位 vs 数据缺失的区分（核心规则）

1. **竞彩「未开胜平负正盘」是玩法缺位，不是数据缺失**: 深盘强队（如皇马让两球半、巴萨）竞彩常只开让球/大小球/比分。判定标准：`wdl_history` 无记录但 `handicap_history`/`total_goals_history`/`score_history` 有记录 → `wdl_not_offered=True`。判定必须用 `find_any_sporttery_match_id()` 四表探针（含 ±3 天日期容差，防误命中历史赛季同名对阵）。
2. **三层文案口径必须一致**: ①单场时序章节「ℹ️ 竞彩未开售胜平负盘（仅让球/大小球/比分），上表为 500.com 初盘参考（非时序缺失）」；②单场诊断表「未开售WDL(500兜底)」；③汇总告警说明列「竞彩未开售胜平负盘（非缺失）」。真缺漂移则分别是「⚠️ 仅 1 条竞彩 WDL 快照需回踩」「仅1快照(缺漂移)」「仅1条快照缺漂移」。
3. **邮件 [缺数据] 红标与调度后置校验只对「真缺数据」触发**: 判定模式为 `仅1条快照缺漂移|无竞彩WDL时序`，不得使用「数据完整性告警」标题判定（否则纯 not_offered 场的当天也会误红标，误导运营做无效回踩）。

### 8.2 「仅1条快照」的两种成因与处置

1. **采集循环未回踩**: live_collector 2 小时循环会在赛前自动补第 2 条快照（实测 08-30 21:19~22:16 三场自愈）；跨天后仍只有 1 条才需手动 `--no-skip-existing` 回踩。
2. **源站赔率零变动**: 竞彩 oddsHistory 自开盘后从未更新（如奥萨苏纳vs赫塔费 08-29 09:36 后无第 2 条），任何回踩都无效，报告如实标注「仅1条快照缺漂移」即可，不是漏采。处置前先用 API 直连探针（getMatchListV1 + getFixedBonusV1 比对 hadList 条数与库内快照数）确认是否与源站同步，避免盲目回踩。

### 8.3 竞彩销售日与实际比赛日错位

竞彩 `businessDate`（销售日归属）常比 matches 表实际开球日早一天（凌晨场归前一日销售日）。报告定位竞彩 match_id 依赖 ±3 天日期容差可正常对齐；统计「当日竞彩场次」时须先确认口径（销售日 vs 开球日），避免误判漏采。

### 8.4 变更日志统计表防脱节

change_log.md §5 统计表曾长期停留在 136 而实际记录已达 554（增量会话只追加记录区、不更新统计区）。追加 C-记录时同步核对 §5.1/§5.2，或定期用脚本按记录行重建（解析 `^\| C-\d{8}-\d{3} \| 时间 \| 类型 \|` 行按列统计）。

## 九、实盘小注验证时代（2026-09-06 补充）

### 9.1 预注册协议（TRIAL-001 ~ TRIAL-005，详见 docs/live_trial_away_favorite_protocol.md）

1. **规则冻结不可改**（TRIAL-001）: 客胜赔率≤2.5 / EV>0（EV=p_away×o_away−1，model_predictions WDL_away 原始概率）/ 平注 ¥20 / 累计 300 注停止 / 未来 7 天窗口 / match_id_en 去重。禁止事后修改规则（防多重比较污染）。
2. **每日节奏**（TRIAL-002）: ①`sporttery_live_collector.py`（采集当日竞彩，无 cookie 可直连 webapi.sporttery.cn）→ ②`generate_unified_report.py --date <当日>`（回写 model_predictions）→ ③`live_trial_away_favorite.py --commit`（dry-run 先看，候选符合才提交）→ 次日 `--settle` 结算（odds500_match status=5 实际比分回填 W/L）。
3. **三源桥接 key**（TRIAL-003）: 竞彩 `{date}_{normalize(home_cn)}_{normalize(away_cn)}`（±1 天日期容差，竞彩官方日 vs 当地日错位）+ 模型 `{date}_{home_en}_{away_en}`（保留空格原样）。竞彩凌晨场归前一销售日、500.com 记当地日，精确 key 仅匹配 8/14 场，±1 天容差后 14/14。
4. **概率口径**（TRIAL-004）: EV 用模型原始 WDL_away 概率，**不叠加 TempScaling**（已知系统性高估 edge → 投注量系统性偏少，保守方向，属预注册接受项）。
5. **评估准则**（TRIAL-005）: 300 注满后 ROI + z 检验 + 分段时间；**以净盈亏为准绳**（Jensen 效应下 z 检验高估显著性，C-20260905-004 教训）。

### 9.2 实盘验证经验（EXP-016）

1. **竞彩采集器无需 cookies**（EXP-016）: sporttery_live_collector.py 直连 webapi.sporttery.cn 公开接口（getMatchListV1 + getFixedBonusV1），HEADERS 无 cookie 可正常采集当日五大联赛开售场次，无需用户提供 Edge cookies（区别于历史 collectors）。
2. **首日 0 笔是规则正常运作**（EXP-017）: 14 场可桥接中 8 场有预测，3 场赔率>2.5、5 场 EV≤0 → 0 笔通过。「宁可少投不可滥投」；300 注需数周累积，不是每日都有。
3. **预测管线只覆盖 odds500_match 清单**（EXP-018）: generate_unified_report 按 odds500_match（status=1）跑，竞彩有开售但 500.com 清单缺失的凌晨场（日期错位）需跑对应日期才生成预测；当日 12/17 场有预测（特征数据不全的跳过）。

---

**文档版本**: v1.23
**创建时间**: 2026-07-23
**最后更新**: 2026-09-12（当前模型最新状态同步至 2026-09-12：500.com curl_cffi 破 EdgeOne + 手动 Cookie 流程、亚盘刷新修复后完赛结算盘口 74/74、统一报告第十二/十四章两通道、SofaScore 19 场补采，C-20260912-001~004）
**更新频率**: 规则或经验变更时更新
**维护人**: 模型优化团队
**更新说明**:
- **v1.23 (2026-09-12)**: ①§7 当前模型最新状态更新至 2026-09-12：500.com 采集器 requests→curl_cffi（impersonate="chrome"）+ EdgeOne 三层 Cookie（含与 UA 绑定的 EO-Bot-Captcha-Token，每日人工 Edge 导出）恢复 500 数据可用，4 场赛前报告完整度 100%（C-20260912-001，详见《故障排查报告_数据采集_20260912》）；②新增 §6.3.4 EdgeOne 反爬与亚盘刷新运维 6 条（TLS 指纹本质、Cookie 凭证、风控节奏、--refresh-odds 用法、完整度口径、rating 覆盖率教训）；③亚盘 refresh_upcoming_odds 修复「先入库后完赛」结算盘丢失 bug，26/27 五联赛 113/1752 有盘口、74 场完赛 100% 结算（C-20260912-003）；④统一报告第十二章接入 24 维 pa_* 代理指标、第十四章新增积分榜/战意（compute_league_standings/classify_zhan_yi）+ 数据不完整保护（C-20260912-002）；⑤数据源表实测刷新（odds.db 1,669MB、500 match 19,790 行、sofascore 特征 18,363 行/98 字段、player_stats 730,128 行、matches 26/27 共 184 行/54 完赛）；⑥§6.4 新增 3 条已知限制（伤病代理指标、积分榜依赖赛果时效、未开盘亚盘）。
- **v1.22 (2026-09-11)**: ①§7 当前模型最新状态同步至 2026-09-11：最新训练 20260908_004604（254 维，slim+ts+consensus+球员 lag 全量特征集）、训练↔服务端 CI 强制对齐 + feature_bridge 桥接（C-20260910-004/011）、统一引擎 DixonColes 全量投产（C-20260909-008）、P1-B 贝叶斯增量 shadow（τ=0.01 逐联赛、意甲 SIGNIFICANT-WIN p=0.0484、生产仍 serve control）；②新增 §6.3.3 自动化调度与生产运维状态（5 项计划任务表 + SYSTEM 账户/电池供电运维注意）；③数据源表更新（odds.db 1,666MB/14,521 场 league 空值 0、26/27 已 152 场、Sporttery 10 季全完成）；④新增 EXP-041（league 列漏写/match_type 兜底派生根因，C-20260911-023）与 EXP-042（shadow 配对 McNemar 口径，C-20260911-022）。P0~P2 状态以《模型优化评估报告_v2.0》为唯一权威（C-20260911-020）。
- **v1.21 (2026-09-06)**: 新增第九章「实盘小注验证时代」：①预注册协议五条规则（TRIAL-001~005）——客胜热门段（away≤2.5）+ EV>0 + ¥20 平注 + 300 注停止，规则冻结防多重比较污染；②每日节奏三步骤（采竞彩→跑预测→生成投注单/结算）；③三源桥接 key 与 ±1 天日期容差（竞彩官方日 vs 当地日错位，14/14 场全匹配）；④概率口径声明（原始 WDL_away 不叠加 TempScaling，系统性高估属预注册接受项）；⑤评估准则以净盈亏为准绳（Jensen 效应）；⑥经验 EXP-016~018（竞彩采集器无 cookie 直连、首日 0 笔是规则正常运作、预测管线只覆盖 odds500 清单）。配套 C-20260906-001 + docs/live_trial_away_favorite_protocol.md。
- **v1.20 (2026-09-04)**: 新增 §1.5 CALIB-009（C-20260904-002 edge 分桶单调回归修复）：①Mono-Pooled(on Temp) 单一单调保序使分桶恢复单调递增（0~3pp -13.4%→3~6pp -4.9%→6~10pp -3.1%→>10pp -1.5%，唯一单调方案）但**全桶仍负、ROI 未转正**，保序只能做「排序修复」不能做「水平修复」；②选择条件化 winner's curse 修正（Mono-Selected）证伪——整体 -6.28% 反而更差、分桶非单调、>10pp 桶高估升至 +17.5pp；③整体最优仍 Mono-Pooled(on raw) -3.65%、Mono-OVR 平召 0.92% 证实逐类保序坍缩（pooled 保平局召回是必要设计）。→ 概率层（校准/保序/择场/选择修正）四连证伪，**系统性高估根治唯一剩路 = 训练端 EV/ROI 目标改造**。
- **v1.18 (2026-09-03)**: 新增 §1.5「概率校准与 EV 决策规则 CALIB-001~007」：①严格时序 OOF 11965 场 × TimeSeriesSplit(5) 折内 fit/折外 transform 的校准对比流程（C-20260903-006）；②Vector/Isotonic 对 Platt 后概率叠加会坍缩平局召回（<2.5%），禁止单用于 WDL 校准；③当前首选 TempScaling(on raw, NLL 最优)：T≈0.896~0.975 逐折递减，平局召回 27.16%（≥0.28 达标）、平注 ROI -3.71%（较基线 +1.70pp 最优）；④校准本身不能使 EV ROI 转正（仍 -3.71%），后续必须叠加 EV 择场 + 训练端 EV 目标 + edge 分桶修复三方面（CALIB-007）。EV ROI 负根因闭环：§3.105 分赛季拆解排除「老赛季赔率质量」、§3.106 校准选型确认「校准仅能压缩高估幅度、无法完全消除」。
- **v1.17 (2026-09-01)**: EV 期望值引擎（决策层）落地：新建 scripts/ev_engine.py（4 dataclass + 8 核心函数 + 17 单测全通过），对接 generate_unified_report.py 第六章、prediction_db_writer.py 行式落库 7 类 EV_* prediction_type（INSERT OR IGNORE 幂等），实现「预测→决策→落库→回测」闭环，预测引擎与投注决策引擎分离（C-20260901-003~006）；特征维度口径复核：生产链 165(slim_odds)→198(+ts_odds)→208(+consensus) 核实无误，当前 feature_utils 实际 211→244→254（T-007 球员特征 52→98 维、+46 死特征，预测管线子集对齐至 208 无错配，24 个非 sofa 列 = pa_* 球员可用性特征）；T-007 球员扩展死特征消融（208 vs 208+8）：三折指标改善≈0.75% 噪声范围内无统计显著，维持 208 维不纳入（C-20260901-002）。
- **v1.16 (2026-08-31)**: 新增第八章「报告完整性门禁时代经验」：not_offered 玩法缺位四表探针判定（C-20260831-001/002）、邮件红标与调度后置校验判定收窄至真缺数据（C-20260831-003/004）、「仅1条快照」两种成因（自愈/源站零变动，C-20260831-005）、竞彩销售日与实际开球日错位提醒、change_log §5 统计表重建（136→554 条防脱节）。
- **v1.15 (2026-08-28)**: P1-10 情境化特征降维复查闭环（逐维 LoO + 分组 ablation + 2-fold A/B，`scripts/_tmp_p110_shap_ablation.py`）：14 维无有价值子集，逐维 RPS 边际 ≤|0.0003|（噪声内）、分组边际 ≤|0.0002|、无子集优于基线（full RPS 0.2018≈基线 0.2017），维持 `ctx_features=False` 不启用（C-20260828-022）；新增 FEAT-012。
- **v1.14 (2026-08-28)**: P1-11 多博彩公司赔率一致性特征生产启用（`consensus_odds=True`，10 维，模型 198→208 维，修复 RPS 列序 bug 后 A/B 四指标全优）；P1-11 降维复查闭环（逐维 LoO + 分组 ablation：无子集优于全量，维持全量 10 维，C-20260828-021）；P1-10 情境化特征 14 维 A/B RPS/Acc 无增益，暂不启用（`ctx_features=False`）；新增 FEAT-011。
- **v1.13 (2026-08-28)**: P1-8/P1-9 收尾落地并同步文档：P1-9 ts_odds 完整校准复验四指标全优（Blend RPS -0.0155），生产 `ts_odds=True`（WDL 特征 165→198 维）+ PA 导入路径修复（C-011）；P1-8 xg_deep 覆盖率 88.5% 达标但 RPS 轻微劣化，保持 `xg_deep=False` 暂不采用；`docs/SYSTEM_ANALYSIS_REPORT_v2.0.md` 同步至 v3.0（C-20260828-015，WDL 架构更新为 198 维 + 5 基础模型 Stacking）。
- **v1.12 (2026-08-28)**: P1-7 Stacking 完成 LR meta-learner（§6.1 WDL 架构更新为 5基础模型+meta-learner；§7.2 硬性规则第6条更新）：`train_stacking_meta.py` 生成 `stacking_meta_learner.json`（多分类 LR），`prediction_core.apply_stacking_meta_learner` 融合，OOF RPS 0.1984 < 固定 0.2038。
- **v1.11 (2026-08-27 23:31)**: Sporttery 时序赔率 16/17~25/26 已结束赛季全部完成（20/21 1,208、21/22 930、22/23 1,001 场入库，合计 handicap 13,125 / wdl 12,575 / total 13,126 / score 13,122）。
- **v1.10 (2026-08-27 21:10)**: Sporttery 时序赔率赛季覆盖更新至 16/17~19/20 + 23/24~25/26 共 7 季（19/20 835 场入库），剩余 20/21~22/23 共 3 季。
- **v1.9 (2026-08-27 20:50)**: Sporttery 时序赔率赛季覆盖更新至 16/17~18/19 + 23/24~25/26 共 6 季（17/18 1,800 场、18/19 1,551 场入库），剩余 19/20~22/23 共 4 季。
- **v1.8 (2026-08-27 16:30)**: 数据源表新增 Sporttery 时序赔率赛季覆盖；采集器 sporttery_collector.py `SEASON_RANGES` 扩展至 16/17~25/26 共 10 季（对标 Understat/SofaScore）。
- **v1.7 (2026-08-26 20:30)**: 数据源表 500.com 更新为 18,038 场（16/17~20/21 回采完成，仅剩 16/17 法甲 380 缺口）。
- **v1.6 (2026-08-26 20:00)**: 数据源表 500.com 更新为 9,288 场（23/24 补齐，阶段一 21/22~25/26 全量完成）；采集器新增 `--skip-existing` 断点续采 + 请求重试。
- **v1.5 (2026-08-26 19:30)**: 数据源表 500.com 更新为 7,536 场（21/22、22/23 回采完成，各 1,826）。
- **v1.4 (2026-08-26 17:30)**: 数据源表 500.com 更新为 4,644 场（SEASONS 16/17~25/26 10 季）；新增 §6.3.2 文档自动更新机制硬性规则。
- **v1.3 (2026-08-23 19:00)**: T-006 v4 模型架构新增 WDL 概率重加权说明；v4 硬性规则新增第7条（SofaScore 队名归一化）；模型状态更新至最新。
- **v1.2 (2026-08-23)**: 新增第七章 v4 时代经验教训，包含过度优化修复 7 条经验 + 6 条新增硬性规则。记录依据架构诊断报告完成的 6 项优化改动。
- **v1.1 (2026-08-12)**: 新增第六章 v3 时代经验教训，包含 T-005 让球预测、自动重训触发器、文档同步三方面经验，以及6条 v3 时代新增硬性规则。
- **v1.0 (2026-07-23)**: 初始版本，包含5大类硬性规则、工程约定、经验教训。
