# P2/P4 身份层统一迁移 — Runbook（C-20261014-095 P0~P4 收官）

> **状态**：设计稿（只读阶段产物）。**窗口前零改动**（C-090/092 铁律）。
> **本轮修正**：D 锚点由"fbref 当地日"改回 **`matches.match_id` 日期口径（竞彩北京日）**（见 §3）。
> **配套仪表**：`scripts/fallback_counter_summary.py`（C-20261016-111）已挂 20:30 链 2d 步；`scripts/window_b_readiness_check.py`（C-20261016-113）已挂 20:30 链 2e 步——**G1(C-108c 结案)+G2(碰撞=0) 同时满足即自动亮灯提醒，但永不自动执行迁移（开窗仍人工决策）**。

---

## 0. 前置与窗口纪律

| 项 | 内容 |
|---|---|
| 触发条件 | `fallback_counter_log.md` L1/L2 持续非零（英文键 review 仍在产生）→ P2/P4 紧迫 |
| 窗口 | **专属重训窗口**，且**排在 C-108c 之后**（C-108c 修特征值语义，P2/P4 修对齐口径，两个变量不得同窗） |
| 铁律 | 窗口前零改动；本 runbook 全部动作标"窗口内执行" |
| 影响面 | 改键 = 改 `build_match_alignment` 对齐覆盖 = 特征矩阵取值变化 → **必须配套重训** |
| 规模 | 实测级联 **~164 万行**（详见 §4）|

---

## 1. 目标（两层，同窗同批）

- **P2（止新噪音）**：读写边界改走 `team_id`（或 canonical EN match_id），名字降级为"可有 N 套的属性"；配**写守卫**——任何写入方的 match_id 生成必须过 canonical 归一，新键不在 canonical 集合 → 拒写/告警。
- **P4（清旧噪音）**：存量混存键统一到 canonical，级联全部带键表（§4）；复用 C-099 迁移机制。

---

## 2. canonical 身份定义（含 A 修正）

**身份 = 名字 + 日期，缺一不可。**

```
canonical_match_id := (team_id_home, team_id_away, canonical_date)
```

### 2.1 team_id（A 修正，最严重）

- **现状缺陷（代码级已确认）**：`build_team_identity.py` 用 `team_id = sorted(canonical_en) 的行号`（`rows = [(i+1, ce, ...) for i, ce in enumerate(canon_list)]`）+ `INSERT OR REPLACE` 全表重建 —— **一队改名，其后的 team_id 集体漂移**。canonical 锚现为 `fbref_match_mapping` 的**当前英文名**，正是 C-099 莱斯特城雷的成因。
- **要求**：
  1. team_id **一次落库、永不重算**（`teams` 表改为 `INSERT OR IGNORE` + 稳定分配，禁止全表重排）；
  2. 派生源必须**改名不变量**（首次出现的稳定 ID，或 SofaScore event/team id）；
  3. 名字只是 team_id 的**可变属性**：`team_id=42 → {canonical_name, fbref_name, sofa_name, 竞彩名}` 各字段独立，改名只改属性。

### 2.2 canonical_date（D 修正）

- **实测锚点**：`build_match_alignment` **通道0 = `history.match_id == matches.match_id` 直等**（占 ~71%），其对齐目标是 **`matches.match_id`**，不是 fbref 映射。matches 由竞彩采集器写入 → 其日期 = **竞彩北京日**（也正是 C-109 定的 `report_date` 口径）。
- **因此 canonical_date 锚定：`matches.match_id` 的日期口径（竞彩北京日）**。若误锚 fbref 当地日，将引入第三种日期口径，反而劈裂已统一的口径。
- ⚠️ 注意通道0 只覆盖 ~71%，其余 29% 走桥表 / `match_id_en` / 名称翻译 —— **canonical 须按四通道并集的目标定义**，不得只盯通道0。
- **单点日期映射模块**：竞彩北京日 / 500 欧洲日 / SofaScore UTC 日 → canonical_date 的偏移规则，集中一处（与 `team_names` 同级），**禁止各脚本各写 ±1 容差**（现存 `resolve_matches_row` / `get_odds_timeline` 的 ±1 为过渡垫片，见 §9）。

---

## 3. 写守卫（P2）

- 位置：所有写入方（预测侧 500 键生成、竞彩采集器、review 写入、SofaScore 特征写）在写 `match_id` 前统一过 canonical 归一。
- 断言（污染门同款）：`归一后键 ∈ canonical 集合`，否则 **拒写 + 告警**（不得静默降级）。
- **未决边界（窗口前必须钉死）**：canonical 集合来源 = 新建权威表 还是 复用 `fbref_match_mapping`？**fbref 未覆盖的低级别联赛**（matches 含非五大联赛）→ 拒写 还是 降级？此边界不钉死，守卫会误拒合法数据。

---

## 4. 表清单（穷举，实测行数与键格式）

> 迁移**必须穷举**，漏一张 = 覆盖率门假过。以下为 `PRAGMA table_info` 全库扫描结果。

### 4.1 严格门（特征对齐消费表 + 预测-of-record）

| 表 | 行数 | 键格式 |
|---|---:|---|
| matches | 18,321 | cn 12,812 / en 5,509（**混存**） |
| wdl_history | 61,668 | cn 全（另带 `match_id_en` 列） |
| handicap_history | 63,387 | cn 全（另带 `match_id_en` 列） |
| total_goals_history | 36,890 | cn 全（另带 `match_id_en` 列） |
| score_history | 850,270 | cn 全（**无 `match_id_en`**） |
| **model_predictions** | 625,446 | cn 376,880 / en 248,566（**预测-of-record，迁错污染全部回测 → 提级为严格门**） |

### 4.2 宽松门（一致性表）

| 表 | 行数 | 备注 |
|---|---:|---|
| post_match_review | 282 | en 全 |
| sofascore_post_match_cache | 282 | en 全 |
| matches_archive | 3,769 | cn 3,287 / en 482 |
| ab_test_log | 29,116 | cn 18,326 / en 10,790 |
| prematch_data_quality | 1,033 | cn 23 / en 1,010 |

### 4.3 桥表 / 映射表（**迁移源资产，非迁移对象**）

| 表 | 行数 | 键 |
|---|---:|---|
| match_id_mapping | 12,968 | `sh_match_id` → `matches_match_id`（**已建好的中→英桥**） |
| match_mapping | 993 | `odds_match_id` → `five_leagues_match_id` |
| match_mapping_old | 119 | 同上（历史） |
| fbref_match_mapping | 19,663 | `odds_match_id` ↔ `fbref_match_id`（event_id 源） |

### 4.4 英文键侧（SofaScore / understat / 500 赔率）

odds500_match 19,790｜odds500_betting 19,790｜odds500_stat 18,233｜odds500_ouzhi_company 547,556｜odds500_ouzhi_summary 19,761｜understat_match_team_stats 18,234｜understat_player_xg 538,405｜understat_shots 459,703｜xgscore_match_stats 43｜match_lineups 739,719（cn 40/en 739,679）｜match_player_stats 739,695｜match_predicted_lineups 2,526｜match_missing_players 86,493｜odds_grid_snapshot 88,633（cn 全）｜**sofascore_team_features 19,662（`event_id` + `match_id_cn`，无 match_id 列 → 迁移须走 event_id）**｜features 0

---

## 5. 迁移步骤（P4，复用 C-099 机制）

复用 `fix_leicester_pollution_c099.py` 的五段式，**按表分阶段**（不搞单一大事务）：

1. **整库文件级备份**：`backups/` 下 DB 全量 + 受影响行 JSON（C-099 `do_backup` 模式）。
2. **dry-run 清单**：每表影响行数、old→new 映射样例、`matches` 侧拼写抽样。
3. **碰撞预检**：新 canonical 键在任一目标表已存在 → 修复前必须清零（C-099 `collision_precheck`）。
4. **六重断言**（沿用 C-099 `load_fix_inputs` 断言骨架）：目标名来自 P0 单一源（`team_names`）｜可归一回 canonical｜实锤集合规模区间｜格式可解析且恰一侧待改｜映射无自反、无合并冲突｜（+本轮新增）**team_id 派生不变性**。
5. **事务内级联 UPDATE**（按表，严格门表优先）；**每表独立覆盖率硬门**：`build_match_alignment(conn, t)` 对齐数 **迁移后 ≥ 迁移前** 且分母不变 —— 任一下降即 **ROLLBACK**（C-099 `alignment_coverage` 模式）。
6. 迁移后复跑 `fallback_counter_summary.py`：L1/L2 应归零（P2/P4 生效验收）。

---

## 6. 复用资产（勿重新推导）

- **`match_id_mapping`（12,968 行）**：已建好的 `sh_match_id → matches_match_id` 桥 → P4 的 old→new 映射**优先取自它**。
- **`match_id_en` 列**（wdl/handicap/total_goals 三表自带）：通道2 已在用 → canonical EN 名**优先取自它**，而非按队名重推。
- ⚠️ `score_history` 无 `match_id_en` → 需经桥表 / 名称翻译补齐，是迁移的**难点表**（85 万行）。

---

## 7. 日期归一（D）

- **主锚 = `matches.match_id` 日期口径（竞彩北京日）**（§2.2）。
- 转换表（单点模块）：`竞彩北京日 / 500 欧洲日 / SofaScore UTC 日 → canonical_date`，各源偏移规则写死一处。
- 迁移时：四张时序表中文键里的日期按转换表折算，与 team_id 拼成三元组。
- **主风险**：凌晨场跨日（北京 03:00 开赛 = 欧洲当地前一日）→ 必须用真实样本对拍（§8）。

---

## 8. 对拍集

- 以 **C-20260930-002 已登记的 d-1 案例**为基准集，验证日期换算方向正确。
- 补充样本：都灵 vs 乌迪内斯（review 键 `2026-10-13_Torino_Udinese` vs 历史键 `2026-10-12_都灵_乌迪内斯`，实测 d-1 容差命中 6 快照）。
- 5 场德甲（500 日期 10-16 / 竞彩北京日 10-17，C-109 归一案例）。

---

## 9. 兜底退出计划

- `resolve_matches_row` + `get_odds_timeline` 的 ±1 容差与名称回退，标注为 **"历史兼容层，新数据不应触发"**。
- 与 **2d 回退计数器**绑定：**P2 生效后 L1/L2 归零 → 可安全移除兜底层**；仍非零 → P2 有漏网，不得移除。

---

## 10. 未决边界（本轮全库取证后裁定）

> 原 4 条为"窗口前必须确认"；本轮经全库取证已裁定（1 条前提被推翻）。残留项单列"仍开放"。

| # | 原边界问题 | 裁定 | 取证依据 |
|---|---|---|---|
| **B1** | 写守卫 canonical 集来源：新建权威表 vs 复用 `fbref_match_mapping` | **用 P0 单一权威源 `team_names.py`**（已存在，C-20261014-096，docstring 声明"全库唯一允许定义球队别名的位置"）。**不复用 `fbref_match_mapping`**——它是**比赛级**映射且覆盖率仅 30%，不能当球队集合。⚠️ 同时修正提问口径：守卫断言的是"**两侧队名可否归一**"（球队级），**不是**"match_id 是否在白名单"（比赛级）——canonical_match_id 是**算出来的**，不是查出来的 | `team_names.py` 存在 + docstring；matches 有 fbref 映射 = 5,496/18,321 = **30.0%** |
| **B2** | 低级别联赛边界：fbref 未覆盖场次 → 拒写 or 降级 | **前提被推翻**：matches 仅含五大联赛（西甲 3887/意甲 3874/英超 3870/法甲 3549/德甲 3109）+ league=None 32 条，**不含低级别联赛**。故边界重定义为**按"队名可否归一"判定**：队名可归一 → **允许写入**（event_id 可为空）；队名无法归一（新队/拼写异常）→ **拒写 + 告警**。fbref 的 70% 未覆盖是"历史场次无 event_id"，**不是"低级别"**。league=None 的 32 条单独核查 | 联赛分布实测；fbref 30% 覆盖性质 |
| **B3** | canonical_date 是否绑定版本 | **不绑**。date 是**规则算出的值**、非持久标识 → 只需**规则模块版本化**（模块内 `VERSION` 常量）；**持久标识 `team_id` 才需版本化**（§2.1）。两者分开，勿混用同一套版本机制 | — |
| **B4** | `score_history` 补齐路径：桥表 vs 名称翻译 | **两级：桥表优先 → 名称翻译兜底**。桥表可覆盖 9,080/13,516 = **67.2%**；剩余 32.8% 走名称翻译（同通道3）；两级均失败 → **不入迁移、记遗留清单**（⚠️ **覆盖率门分母须锁定**，不得因不可桥行放宽门限） | `match_id_mapping` 实测 67.2%；`score_history` 列 = id/match_id/timestamp/score/odds（**无 `match_id_en`**） |

### 仍开放 → 本轮已裁定（2026-10-16）

| # | 项 | 裁定 | 理由 |
|---|---|---|---|
| **R1** | `score_history` 桶装分批粒度（85 万行，最大表） | **按日期窗（赛季）分批**，每桶独立覆盖率门 + 独立回滚 | 时序表天然按时间分区；按联赛会同桶跨赛季且单联赛桶仍巨大；日期窗回滚颗粒度最细 |
| **R2** | `teams` team_id 稳定化改造前是否需冻结快照 | **需要**：改造前先 `snapshot teams → teams_snapshot_pre_p2`（team_id / canonical_en / canonical_cn） | 改造瞬间若发生身份错配可回查基线；成本极低 |

### 窗口排序（本轮判定 C-108c 状态）

> 核实 change_log：**C-108c 已登记（C-20261015-108c），状态"执行严格挂配套重训窗口" → 未执行**。其"唯一待窗口动作" = 零件③ `aggregate_team_history` 分母改 `Σw_avail` 落盘 + 重训。
> 故 **P2/P4 保持排在 C-108c 之后的独立窗口**（两变量不得同窗）。执行序：
> **窗口A = C-108c + 配套重训 → 窗口B = P2/P4 + 配套重训**。

> 裁定后 §11 执行顺序补注：**B4 的"桥表优先"应在 ③ score_history 迁移前先跑一次桥表覆盖率实测**（`p2_p4_dryrun.py` 已内置），锁定 67.2% 与不可桥清单，作为该表覆盖率门的分母基线。

---

## 11. 窗口内执行顺序（建议）

```
① 整库备份（文件级 + 行级）
② team_id 稳定分配改造（INSERT OR IGNORE，禁止全排）→ 重建 teams 校验 166 队身份不变
③ 严格门迁移：matches → 四张时序表（score_history 最后，难点表）
④ model_predictions 迁移（同严格门）
⑤ 宽松门迁移：review / sofa cache / archive / ab_test_log / prematch_data_quality
⑥ 每阶段跑 build_match_alignment 覆盖率硬门，下降即 ROLLBACK 该阶段
⑦ 全量复跑 fallback_counter_summary → L1/L2 归零验收
⑧ 配套重训（因对齐覆盖变化，撞 C-090/092）
```

## 12. 回滚预案

- 任一阶段覆盖率下降 / 断言失败 / 碰撞未清零 → **该阶段事务 ROLLBACK**，保留前一阶段成果。
- 整库级失败 → 从 `backups/` 文件级备份还原。
- 回滚后必查：`fallback_counter_summary` L3 是否异常上升（兜底层是否被误伤）。
