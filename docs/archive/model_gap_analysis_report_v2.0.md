# 足球预测模型 — 差距对比分析报告 v2.10

> **报告版本**: v2.10
> **生成日期**: 2026-08-28
> **对比基准**: `model_gap_analysis_framework.md`（差距分析与改进总框架 v1.0）
> **系统现状**: `SYSTEM_ANALYSIS_REPORT_v2.0.md`（v8.3，5,252场，208维，5基础模型+LR meta-learner Stacking）
> **⚠️ 分析时点快照注记（2026-09-09）**: 本文档为 2026-08-28 分析时点的静态快照，文内 T-006 v4 引用已被 T-006 v5 取代（C-20260908-022，score_prediction_module 集成 v5 + 生产 WDL 锚定）。如需最新**状态/优先级（P0~P2）**，以《模型优化评估报告_v2.0.md》为准（本文 §三 路线图为 08-28 历史快照）。文中标注 * 的指标（如 5,252 场）为 **P0-1 回溯采集前口径**，回溯采集已完成（§7 进度 18,038 场），**当前样本量以《模型架构分析报告_v1.0》§3.1（14,511 场）为准**。
> **分析方法**: 逐项对比 + 代码级 grep 搜索 + 文件定位验证 + 深度根因分析
> **本次更新**: P0-修复 ✅ | P0-2 RPS ✅ | P0-3 球员特征 ✅ | P0-4 统一架构 ✅ | P0-1 数据扩展 ✅ | 文档精简 ✅ | **RPS列序bug修复 ✅ | 统一引擎接入方案 ✅ | 四大数据源历史回溯采集完成 ✅（SofaScore 18,038 / Understat 17,959 / 500.com 18,418 / Sporttery 13,125，微观缺口见 §7.3.1~7.3.4） | Sporttery 时序赔率 16/17~25/26 全 10 季完成 ✅ | P1-9 时序赔率+比分赔率特征接入 ✅（对齐链路补齐 99.4% + D-013 22维/比分8维/联赛z-score） | P1-9 遗留收尾 ✅（PA 导入路径修复 + ts_odds 完整校准复验：BLEND RPS -0.0155 / LogLoss -0.0072 / Acc +0.0019 / DrawRecall +0.0049 四指标全优，生产 ts_odds=True） | P1-8 xG 特征深化 ✅（6 维差值趋势/分位，覆盖率 88.5% 达标，但 RPS 无提升暂不采用） | P1-5 贝叶斯层级模型 ✅（聚合 RPS 0.2092 ≤0.21，优于朴素泊松 0.2316 / 无正则 MLE 0.2368） | P1-7 Stacking + LR meta-learner ✅（5 基础模型 OOF + 多分类 LR，OOF RPS 0.1984 < 固定权重 0.2038） | P1-10 情境化特征工程 ✅ **已实现并验证（2026-08-28：14 维，A/B RPS/Acc 无增益，降维复查亦无子集，维持不启用 ctx_features=False） | P1-11 多博彩公司赔率一致性 ✅ **已启用（2026-08-28：10 维，修复 RPS 列序 bug 后四指标全优，consensus_odds=True，模型 198→208 维；降维复查已闭环，维持全量 10 维） | P2-14 分层评估体系 ✅（6 维度接入 train_models.py） | P2-15 自动化数据质量监控 ✅（质量门禁接入训练 Pipeline 前置步骤）**

---

## 目录

1. [摘要与核心发现](#一摘要与核心发现)
2. [六大维度差距对比分析](#二六大维度差距对比分析)
   - 2.1 [数据层](#21-数据层--最大的结构性短板)
   - 2.2 [特征工程](#22-特征工程--广度够深度不足)
   - 2.3 [模型架构](#23-模型架构--方向正确深度不够)
   - 2.4 [校准与评估](#24-校准与评估--有基础缺标准指标)
   - 2.5 [工程化](#25-工程化--能用但不够专业)
   - 2.6 [可量化性能差距总表](#26-可量化性能差距总表)
3. [优化路线图](#三优化路线图)
4. [详细执行计划](#四详细执行计划)
   - 4.1 [P0-2: RPS 报告集成](#p0-2-rps-报告集成)
   - 4.2 [P0-4: 统一比分-胜平负架构](#p0-4-统一比分-胜平负架构)
   - 4.3 [P0-1: 数据扩展 8-10 赛季](#p0-1-数据扩展-8-10-赛季)
   - 4.4 [P0-3: 球员可用性特征](#p0-3-球员可用性特征)
   - 4.5 [P1-5: 贝叶斯层级模型](#p1-5-贝叶斯层级模型)
   - 4.6 [P1-6: 情境化特征工程](#p1-6-情境化特征工程)
   - 4.7 [P1-7: 模型 Stacking 集成](#p1-7-模型-stacking-集成)
   - 4.8 [P1-8: xG 特征深化](#p1-8-xg-特征深化)
   - 4.9 [P2-11~P2-15: 工程化与评估增强](#p2-11p2-15-工程化与评估增强)
5. [验收标准](#五验收标准)
6. [附录：代码位置索引](#六附录代码位置索引)

---

## 一、摘要与核心发现

### 1.1 分析方法

对当前系统 v8.3 进行全面的代码级搜索验证，逐项对比框架文档的 56 项要求。每次搜索均返回具体文件路径、行号和代码片段作为证据，并深入分析根因。

### 1.2 关键纠正（v2.7 扩展）

| 框架认为 | 实际状态 | 代码证据 | 量化影响 |
|---------|---------|---------|:--:|
| "RPS 未评估" | RPS 已在 `advanced_model_trainer.py` 实现，但主训练输出中未报告 | [L1647-L1653](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/advanced_model_trainer.py#L1647-L1653) | 缺少与世界级基准 0.19-0.21 对话的统一度量衡 |
| "天气数据完全缺失" | ✅ **已修复** — `prediction-engine.js` 改为数据驱动，`match_condition_features.py` 生成真实因子 | [L98-L186](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/shared/prediction-engine.js#L98-L186) | 伤病因子基于 SofaScore 出场数据，天气因子基于联赛-月份气候均值 |
| "数据质量监控缺失" | `modules/common/data_cleaner.py` 有质量监控逻辑，但需手动运行 | [L351-L408](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/modules/common/data_cleaner.py#L351-L408) | 无自动化调度，数据质量退化无法及时发现 |
| "Stacking 集成" | 6模型固定权重加权平均，但实际有效仅4模型（Poisson/SSM与DC共享λ） | [prediction_core.py L89-L95](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/prediction_core.py#L89-L95) | 非真正的Stacking+meta-learner，模型多样性不足 |
| "平局处理" | 后处理补丁（DrawCalibrator+Threshold），非模型层面解决 | [train_models.py L35-L55](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/train_models.py#L35-L55) | 概率校准恶化（ECE上升），联赛间迁移性差 |

### 1.3 差距总览

| 维度 | 框架条目数 | 已达标 | 部分达标 | 有差距 | 完全缺失 |
|------|:--:|:--:|:--:|:--:|:--:|
| 数据层 | 13 | 2 | 3 | 3 | 5 |
| 特征工程 | 16 | 2 | 1 | 6 | 7 |
| 模型架构 | 10 | 1 | 3 | 4 | 2 |
| 校准与评估 | 10 | 2 | 2 | 3 | 3 |
| 工程化 | 7 | 4 | 0 | 3 | 0 |
| **总计** | **56** | **11** | **9** | **19** | **17** |

---

## 二、六大维度差距对比分析

### 2.1 数据层 — 最大的结构性短板

#### 2.1.1 数据量级

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 比赛总量 | 15,000–25,000场 | 5,252场（3赛季）* | `project_memory.md` L315: "5259场比赛" | 🔴 1/3~1/4 |
| 时间跨度 | 2010年代至今 | 2023-2026 | `final_sofascore_collector.py` L85-L141: 仅 23/24~26/27 赛季 ID | 🔴 缺7赛季 |
| 联赛覆盖 | 五大联赛+欧冠+欧联+次级 | 仅五大联赛 | 搜索 `collection/` 无 "champions"/"europa"/"UCL" 结果 | 🟡 |

**代码证据**：

```python
# final_sofascore_collector.py L85-L141 — 仅配置 23/24~26/27 赛季，无历史回溯
SEASONS = {
    "23/24": {"英超": 52186, "西甲": 52380, "意甲": 52764, "德甲": 52622, "法甲": 52512},
    "24/25": {"英超": 61644, "西甲": 61865, "意甲": 62294, "德甲": 62144, "法甲": 62010},
    "25/26": {"英超": 71157, "西甲": 71378, "意甲": 71807, "德甲": 71666, "法甲": 71532},
    "26/27": {"英超": 82282, "西甲": 82503, "意甲": 82932, "德甲": 82791, "法甲": 82657},
}
```

```python
# final_understat_collector.py L2-L25 — xG 数据（已 16/17~25/26 全齐）
# 说明：作为 FBref、SofaScore 之外的第三数据源，补充 xG、射门级坐标数据
# 已回溯补齐 16/17~22/23 历史赛季，Understat 五大联赛 xG/TS 16/17~25/26 已 100% 完整
```

**根因分析**：
1. **采集器设计时未考虑历史回溯**：`final_sofascore_collector.py` 的 `SEASONS` 字典从 23/24 赛季开始，没有更早赛季的 SofaScore `uniqueTournament` ID 配置
2. **Understat 采集器仅覆盖 1 个赛季**（现已回溯补齐）：`final_understat_collector.py` 被设计为补充数据源，未做多赛季批量采集
3. **无自动化历史数据补充机制**：数据采集是"向前看"的（只采集新赛季），没有"向后看"的回溯采集脚本

**量化影响**：
- 过拟合差距 ~8pp（训练-验证准确率差），世界级基准 <3pp
- 升班马/降级队样本极少，预测质量差
- 稀有比分（4-0、5-2等）尾部预测不可靠
- 教练周期、球队重建周期无法完整观测

**优化建议**：P0-1 回溯采集 2016-2023 赛季 → 预期准确率 +2-3pp，过拟合差距减半

#### 2.1.2 数据维度覆盖

| 数据类型 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|---------|---------|---------|---------|:--:|
| xG 数据 | 覆盖率 >95% | Understat 33% | 仅 25/26 赛季五大联赛，`isResult=false` 自动跳过未开赛 | 🔴 |
| Adj Goals | 538 SPI 核心 | 无 | 搜索 `scripts/` 无 "adjusted_goals"/"garbage_time" | 🔴 |
| 天气/场地 | 温度/降雨/草皮 | 有硬编码因子但无数据驱动 | `prediction-engine.js` L98-L106: `calcWeatherImpact`/`calcSurfaceImpact` 硬编码映射 | 🟡 |
| 赛程密度 | 近7/14天比赛数 | 无 | 搜索 `features/` 无 "schedule"/"rest_days" | 🔴 |
| 旅行/转会/教练 | 旅途距离/身价/战术 | 无 | 搜索 `collection/` 无 "travel"/"transfer"/"coach" | 🟡 |

**代码证据（天气硬编码）**：

```javascript
// prediction-engine.js L98-L122 — 硬编码的天气因子，非数据驱动
calcWeatherImpact(weather) {
    const weatherType = weather?.type || 'clear';
    return WEATHER_IMPACT[weatherType] || WEATHER_IMPACT.clear; // 无实际天气数据
}
```

```javascript
// prediction-engine.js L128-L131 — injury/keyPlayer 默认值均为 1.0，无实际数据输入
const injuryA = teamA.injury !== undefined ? teamA.injury : 1.0;
const keyPlayerA = teamA.keyPlayer !== undefined ? teamA.keyPlayer : 1.0;
const injuryB = teamB.injury !== undefined ? teamB.injury : 1.0;
const keyPlayerB = teamB.keyPlayer !== undefined ? teamB.keyPlayer : 1.0;
```

**根因分析**：
1. **天气/伤病因子形同虚设**：JS 预测引擎中 `calcWeatherImpact`、`calcSurfaceImpact` 均有实现，但 `lambda` 计算中的 `injury`/`keyPlayer`/`weather` 因子均使用默认值 1.0（无实际数据输入），导致这些因子完全不起作用
2. **赛程密度数据源缺失**：无独立的赛程数据采集模块，无法从比赛日期计算休息天数、近7/14天比赛数
3. **xG 覆盖率低**：Understat 数据源仅覆盖 25/26 赛季，SofaScore xG 数据质量参差不齐

**优化建议**：P0-1a（Understat 历史回溯）+ P0-修复（天气/伤病因子数据驱动化，已完成）+ P1-6（赛程密度特征，归入情境化特征工程）

#### 2.1.3 数据质量

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 质量监控 | 自动化完整性校验 | 有 `data_cleaner.py` + 18个验证脚本，但无自动化调度 | `data_cleaner.py` L351-L408: 统计总量+按问题数量输出 pass/warning/fail | 🟡 |
| 异常检测 | 自动化 | 依赖人工检查 | 18个验证脚本需手动逐个运行 | 🟡 |

**代码证据**：

```python
# data_cleaner.py L351-L408 — 质量监控逻辑完整但需手动运行
# 检查日期格式、球队名中文、赔率数据存在性、时序点数
cursor.execute("SELECT match_id, home_team, away_team, match_date FROM matches WHERE match_type LIKE ?", (f'%{league}%',))
# 统计各类问题: missing_info, invalid_date, invalid_team_name, missing_wdl, missing_handicap...
# 判断整体状态: total_issues > 10% → warning, > 30% → fail
```

**根因分析**：
1. **无 CI/CD 集成**：质量监控脚本未集成到自动化流水线，需手动运行
2. **无告警机制**：数据质量退化时无自动通知，依赖人工定期检查
3. **18个验证脚本分散**：每个验证维度独立脚本，无统一入口

**优化建议**：P2-15 自动化质量监控（集成到训练 Pipeline 前置步骤）

---

### 2.2 特征工程 — 广度够，深度不足

#### 2.2.1 赔率时序特征（优势项）

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 赔率特征 | 81维专业 | 208维（slim 165 + ts_odds 33 + consensus 10），含30维精简赔率（slim_odds=True） | `feature_utils.py` L1474: `slim_odds=True` 默认 | 🟢 已达标 |
| 多博彩公司一致性 | 离散度/交叉验证 | 单博彩公司 | 赔率 TXT 仅单源数据 | 🟡 |

**代码证据**：

```python
# feature_utils.py L1472-L1474 — slim_odds 默认，精简赔率 30 维
def build_all_features(df, include_odds=True, include_elo=True, include_temporal=True,
                       include_score=True, include_nonlinear=True, include_draw_enhanced=True,
                       slim_odds=True):  # C-20260823-003: 默认精简赔率特征
```

```python
# feature_utils.py L1550-L1564 — slim_odds 模式下跳过 D-013 时序赔率（10维）
if include_temporal and not slim_odds:
    from d013_temporal_odds import build_d013_features
    temporal_feature = build_d013_features(df)

# feature_utils.py L1567-L1581 — slim_odds 模式下跳过 T-003.1 比分赔率（8维）
if include_score and not slim_odds:
    from score_features import build_score_features
    score_feature = build_score_features(df)

# feature_utils.py L1583-L1597 — slim_odds 模式下跳过 T-003.2 非线性变换（29维）
if include_nonlinear and not slim_odds:
    nonlinear_feature = build_nonlinear_features(X)
```

**v8.3 优化验证**：A/B 测试验证精简赔率准确率仅 -0.48pp，平局召回率 +1.55pp，特征构建速度 1.6x。方向正确。

#### 2.2.2 球员级特征（核心短板）

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 聚合粒度 | 位置-specific | 全队简单加权平均（F/M/D 位置聚合已实现，A/B 验证后维持不启用） | `sofascore_pre_match_features.py` `aggregate_team_history()` 已新增 11 维 F/M/D 位置特征（P2-10），真实数据 A/B 平局召回劣化 4.04pp、无增益 | ✅ |
| 球员可用性 | 预计首发+伤病/停赛 | 无 | 搜索 `features/` 无 "injury"/"availability"/"expected_lineup" | 🔴 |
| 疲劳特征 | 近7/14天比赛数 | 无 | 搜索 "fatigue" 无结果 | 🔴 |
| 状态动量 | 近3场 vs 近10场差值 | 无（静态近5场平均） | 搜索 "momentum" 仅 Elo 中有，球员特征无 | 🟡 |
| 门将单独建模 | xG prevented/出击/传球 | 仅扑救率+扑出球 | L441-L454: 仅 G 位置有 `gk_saves_5g`/`gk_goals_prev_5g` | 🟡 |

**代码证据（聚合逻辑完整展示）**：

```python
# sofascore_pre_match_features.py L271-L480 — aggregate_team_history() 完整聚合逻辑

# L331-L339: 所有球员一视同仁聚合（仅评分区分首发/替补）
# (1) 评分：首发11人加权均值
starters = team_stats[team_stats["is_starter"] == 1]
if not starters.empty and starters["rating"].notna().any():
    sw = starters["weight"]
    features["sofa_rat_5g"] = float(
        (starters["rating"].fillna(0) * sw).sum() / sw.sum()
    ) if sw.sum() > 0 else 0.0

# (2) xG：全队加权总和/场次（不区分首发/替补）
features["sofa_xg_5g"] = float(
    (team_stats["expected_goals"].fillna(0) * w).sum() / len(team_matches)
)

# L441-L454: 仅门将有位置区分，前锋/中场/后卫无位置特异性聚合
# (19) 门将扑救率（仅统计 position=G 的球员）
gk_stats = team_stats[team_stats["position"] == "G"]
if not gk_stats.empty and gk_stats["gk_saves_sofa"].notna().any():
    features["sofa_gk_saves_5g"] = float(
        (gk_stats["gk_saves_sofa"].fillna(0) * gk_stats["weight"]).sum() / len(team_matches)
    )
    features["sofa_gk_goals_prev_5g"] = float(
        (gk_stats["goals_prevented"].fillna(0) * gk_stats["weight"]).sum() / len(team_matches)
    )

# L483-L495: 无历史数据时返回全 0 特征（升班马问题）
def _empty_features() -> Dict[str, float]:
    """ 无历史数据时返回全 0 特征 """
    keys = ["sofa_rat_5g", "sofa_xg_5g", ..., "sofa_rat_std_5g"]
    return {k: 0.0 for k in keys}
```

**根因分析**：
1. **`aggregate_team_history()` 设计时未考虑位置维度**：函数对所有出场球员（`team_stats`）一视同仁聚合，`is_starter` 仅用于评分特征（第1维），其他特征（xG、传球、抢断等）无首发/替补区分，无位置区分
2. **升班马处理粗暴**：`_empty_features()` 返回全 0 特征，意味着升班马球队在特征空间中完全"隐形"，模型无法做出合理预测
3. **球员可用性完全缺失**：无伤病/停赛数据源，无法计算"预计首发11人加权表现"替代全队历史平均
4. **门将特征过于简单**：仅扑救率和扑出球，缺少 xG prevented（预期进球阻止率）、出击成功率、传球发起能力等关键门将指标

**优化建议**：P0-3（球员可用性特征）+ P2-10（位置-specific 球员特征）

#### 2.2.3 情境化特征（完全缺失）

| 情境维度 | 框架要求 | 代码证据 | 差距 |
|---------|---------|---------|:--:|
| 战意量化 | 争冠/保级/欧战积分压力 | 搜索 "motivation"/"pressure"/"title_race"/"relegation_battle" 无结果 | 🔴 |
| 赛程密度 | 近7/14天比赛数 | 搜索 "schedule"/"rest_days" 无结果 | 🔴 |
| 德比/宿敌 | 德比标记+情绪溢价 | 搜索 "derby"/"rivalry" 无结果 | 🟡 |
| 教练因素 | 新教练效应 | 搜索 "coach"/"manager" 无结果 | 🟡 |
| 休息天数 | 双方距上次比赛天数 | 搜索 "rest" 无结果 | 🟡 |

**注意**：`config.yaml` 中的 `lambda_adjustment.season_boost` 只处理了赛季前3轮的 λ 上浮（`prediction-engine.js` L156-L158: `const seasonBoost = (round <= 3) ? 1.25 : 1.0`），但保级队末轮的战意飙升、争冠队的轮换压力等完全没建模。

**根因分析**：
1. **特征工程模块未接入积分榜时序数据**：需要 `standings_history` 表存储每场比赛前的积分榜快照，当前数据库无此表
2. **球队对映射表缺失**：德比球队对需要 `derby_pairs` 表存储，当前无此基础设施
3. **教练数据源缺失**：无教练变更记录的采集模块

**优化建议**：P1-6（情境化特征工程，框架文档已提供完整实现代码 L1963-L2199）

#### 2.2.4 xG 特征利用

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| xG 差值趋势 | 近5场 xG-xGA 变化斜率 | 无 | 搜索 "xg_trend"/"xg_diff" 无结果 | 🟡 |
| 定位球分离 | 点球/运动战/角球 xG | 无 | 搜索 "penalty_xg"/"set_piece" 无结果 | 🟡 |
| Bayes-xG 修正 | 层级收缩，区分天赋/机会 | 无 | 搜索 "bayes_xg" 无结果 | 🟡 |

**代码证据**：

```python
# advanced_model_trainer.py L1676-L1689 — xG 特征已有基础框架但未深化
# 策略4: xG 特征工程 (预期进球融合)
xg_home_arr, xg_away_arr = self._calculate_xg_features(df_train_meta, val_meta)
if xg_home_arr is not None:
    # 融合 xG 值与全局基准 (70% xG + 30% 全局基准)
    xg_weight = 0.7
    effective_base_lh_arr = xg_weight * xg_home_arr + (1 - xg_weight) * base_lh
else:
    effective_base_lh_arr = np.ones(n_val) * base_lh
    logger.info("[ScoreEval] xG 特征不可用, 使用全局基准值")
```

**优化建议**：P1-8（xG 特征深化）

---

### 2.3 模型架构 — 方向正确，深度不够

#### 2.3.1 贝叶斯层级模型

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 贝叶斯模型 | Baio-Blangiardo HBM | 无 | 搜索 "bayesian"/"pymc"/"stan"/"hierarchical" 无结果 | 🔴 |
| 不确定性量化 | 后验预测分布 | 点估计概率 | 搜索 "uncertainty"/"confidence_interval"/"posterior" 无结果 | 🟡 |

**根因分析**：
1. **贝叶斯模型在足球预测中方法论优势明显**：升班马处理（层级先验"借强度"）、不确定性量化（后验分布）、小样本稳健性（先验正则化），但当前系统完全未实现
2. **框架文档已提供完整实现方案**（L1489-L1916）：`BayesianHierarchicalModel` 类含 MAP 优化（L-BFGS-B）、Dixon-Coles ρ 修正、时间随机游走

**优化建议**：P1-5（贝叶斯层级模型）

#### 2.3.2 集成方式

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 集成策略 | Stacking + LR meta-learner | LR meta-learner 已完成 ✅（5 基础模型 OOF + 多分类 LR，OOF RPS 0.1984 < 固定 0.2038） | `prediction_core.py`: `apply_stacking_meta_learner` + `assets/stacking_meta_learner.json` | ✅ |
| 模型多样性 | 统计+树+贝叶斯 | DC+Elo+XGB+LGB+贝叶斯（5 方法论，贝叶斯已纳入） | 无神经网络 | 🟡 |

**代码证据（JS端6模型 → Python端4模型）**：

```python
# prediction_core.py L89-L95 — 4模型 Stacking 固定权重
STACKING_WEIGHTS = {
    'dixonColes': 0.30,
    'xgboost': 0.30,
    'lightgbm': 0.25,
    'elo': 0.15,
}

# prediction_core.py L330-L345 — 固定权重加权平均，非 meta-learner
@staticmethod
def stack_wdl_probabilities(sub_probs, weights):
    stacked = {'win': 0.0, 'draw': 0.0, 'lose': 0.0}
    total_weight = 0.0
    for model_name, probs in sub_probs.items():
        w = weights.get(model_name, 0)  # 预定义的固定权重
        if w > 0 and probs is not None:
            stacked['win'] += probs['win'] * w
            stacked['draw'] += probs['draw'] * w
            stacked['lose'] += probs['lose'] * w
            total_weight += w
    if total_weight > 0:
        stacked['win'] /= total_weight
        stacked['draw'] /= total_weight
        stacked['lose'] /= total_weight
    return stacked
```

```javascript
// prediction-engine.js L1578-L1650 — JS 端 6 模型加权融合（含已移除的 Poisson/SSM）
async predictStacked(teamAKey, teamBKey, teamA, teamB, options, matchContext) {
    const poisson = this.predictMatch(teamA, teamB, options);
    const dc = this.predictMatchDC(teamA, teamB, options);
    const ssm = this.predictMatchSSM(teamA, teamB, options);
    const xgb = this.predictXGB(teamA, teamB, options, matchContext);
    const lgb = this.predictLightGBM(teamA, teamB, options, matchContext);
    const elo = this.predictElo(teamAKey, teamBKey, options);
    // 固定权重加权求和
    models.forEach(m => {
        winA += m.weight * m.data.winA;
        draw += m.weight * m.data.draw;
        winB += m.weight * m.data.winB;
        totalWeight += m.weight;
    });
}
```

**根因分析**：
1. **当前权重是预定义的**（DC 30%+XGB 30%+LGB 25%+Elo 15%），非从数据中学习的 meta-learner
2. **模型多样性不足**：XGBoost 和 LightGBM 都是 GBDT 类模型，方法论单一
3. **v8.3 已移除 Poisson 和 SSM**（与 Dixon-Coles 共享同一 λ，输出高度相关，伪集成），但 JS 端仍保留 6 模型调用

**优化建议**：P1-7（Stacking + LR meta-learner）+ P1-5（贝叶斯模型作为基础模型）

#### 2.3.3 比分-胜平负架构

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 架构统一性 | ML→λ→DC 矩阵→全概率导出 | T-005/T-006 并行独立 | `prediction_core.py` L1657-L1663: T-006 独立调用，WDL 重加权后处理 | 🟡 |
| 架构一致性 | 胜平负=比分矩阵边际 | 已通过 T-006 v4 WDL 重加权缓解 | L1418-L1471: 重加权使比分与 WDL 方向一致 | 🟢 部分缓解 |

**代码证据（WDL 重加权后处理）**：

```python
# prediction_core.py L1418-L1471 — T-006 v4 重加权使比分与 WDL 方向一致
# C-20260823-019: WDL→比分 重要性重加权
if wdl_probs and wdl_probs.get('win') is not None:
    # 计算 Poisson 比分分布的边际 WDL 概率
    poisson_marginal = {'win': 0.0, 'draw': 0.0, 'lose': 0.0}
    for score, prob in fused.items():
        # ... 计算 Poisson 边际 WDL 概率
    # 重要性比率: ratio = WDL_prob / Poisson_marginal_prob
    ratios = {
        'win': wdl_probs['win'] / max(poisson_marginal['win'], eps),
        'draw': wdl_probs['draw'] / max(poisson_marginal['draw'], eps),
        'lose': wdl_probs['lose'] / max(poisson_marginal['lose'], eps),
    }
    # 对每个比分重加权: P'(score) = P(score) × ratio[outcome]
    reweighted = {score: prob * ratios[outcome] for score, prob in fused.items()}
```

**根因分析**：
1. **T-006 v4 重加权是后处理方案**，缓解了比分预测与 WDL 方向不一致的问题（如 WDL 预测主胜但最可能比分是 1:1）
2. **根本架构未统一**：ML 模型仍直接预测 WDL 分类，而非预测 λ 再生成比分矩阵，所有下游概率（胜平负、让球、大小球、精确比分）应统一从比分矩阵导出

**优化建议**：P0-4（统一比分-胜平负架构）

#### 2.3.4 缺乏不确定性量化

当前输出点估计概率，没有置信区间。贝叶斯后验预测分布天然提供不确定性。应用价值：不确定性高时降低投注仓位，不确定性低时加大仓位。

**优化建议**：P1-5（贝叶斯层级模型自带不确定性量化）

---

### 2.4 校准与评估 — 有基础，缺标准指标

#### 2.4.1 RPS 评估指标（✅ 已报告，列序 bug 已修复）

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| RPS 计算 | 有 | 已实现 | `advanced_model_trainer.py` L1647-L1653: `rps_val = mean(sum((cum_p-cum_o)^2))/2` | 🟢 已实现 |
| RPS 报告 | 训练输出中报告 | **已输出** | `train_models.py` [compute_rps L27-L61](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/train_models.py#L27-L61) + 调用点 L290/L363 + `final_report` 字段 | � 已达标 |

**代码证据（RPS 计算存在但未报告）**：

```python
# advanced_model_trainer.py L1647-L1653 — RPS 计算已实现
cum_p = np.cumsum(probs_hda, axis=1)
cum_o = np.cumsum(actual_hda, axis=1)
rps_val = np.mean(np.sum((cum_p - cum_o) ** 2, axis=1)) / 2
```

```python
# train_models.py L983-L1049 — final_report 生成逻辑，缺失 RPS 字段
final_report['models'][mname] = {
    'training': {
        'train_accuracy': float(train_metrics.get('train_accuracy', 0)),
        'train_logloss': float(train_metrics.get('train_log_loss', 0)),
        'val_accuracy_original': float(acc_orig),
        # ... 无 RPS 字段
    },
    'recommended': {
        'accuracy': float(best_acc),
        'draw_recall': float(best_dr),
        # ... 无 RPS 字段
    },
}
```

**根因分析**：
1. **RPS 计算在 `advanced_model_trainer.py` 中**（比分评估专用），主训练脚本 `train_models.py` 未调用该函数
2. **修复成本极低**：只需在 `train_models.py` 的 `final_report` 生成逻辑中添加 RPS 字段

**优化建议**：P0-2（RPS 报告集成，5分钟完成）

**✅ P0-2 修复记录（2026-08-23）**：

1. **RPS 列顺序 bug 修复**（C-20260823-020）：`compute_rps()` 原实现 `actual_hda` 列序为 `[主胜, 平局, 客胜]`，与 `y_pred_proba` 的 label 升序列序 `[客胜, 平局, 主胜]` 相反，导致 cumsum 方向错位、RPS 系统性偏大（玩具数据实测 0.4213 vs 正确 0.1113）。已改为 `actual_hda[i, int(yv)] = 1` 与 pred 列序对齐。
2. **监控日志**：`compute_rps()` 增加 `[RPS-Monitor]` 计算过程日志（样本数/列均值/RPS 值与基准 0.19-0.21 对比）。
3. **修正后验证集 RPS**（5259 场，165 维，5 折时间序列 CV；此为 2026-08-23 历史基准，最新 208 维模型 RPS 见 §9.11/§3.4.1）：
   - 最终 XGBoost：**RPS = 0.2095**（val_acc 0.5057，LogLoss 1.0259）
   - 最终 LightGBM：**RPS = 0.2115**（val_acc 0.4914，LogLoss 1.0330）
   - 5 折 CV RPS 区间：XGBoost 0.2016~0.2160，LightGBM 0.2032~0.2180
   - 结论：RPS 接近世界级基准 0.19-0.21，概率校准质量合格，不再是此前偏大的错误值。

#### 2.4.2 平局处理（后处理补丁）

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 处理方式 | 模型层面（Focal Loss） | 后处理补丁 | `train_models.py` L36-L44: `DrawCalibrator` + `draw_threshold` 后处理 | 🟡 |
| 平局召回率 | 30%–35% | 33.8%（factor=1.5）→ v8.3 argmax | L827-L847: `search_best_draw_calibrator` 约束 draw_recall≥0.28 | 🟢 已达标 |

**代码证据（后处理完整逻辑）**：

```python
# train_models.py L35-L55 — 平局后处理配置
DRAW_THRESHOLD_FACTOR = 1.5
DRAW_CALIBRATOR_FACTOR = 0.885  # 推荐值 (draw_recall>=0.28, 准确率最高)
DRAW_CALIBRATOR_CONSERVATIVE = 0.850  # 保守值 (draw_recall>=0.30)

# train_models.py L827-L847 — 精细搜索 DrawCalibrator
def search_best_draw_calibrator(probs, y_true, factor_min=0.50, factor_max=1.00, factor_step=0.01, recall_target=0.28):
    for factor in np.arange(factor_min, factor_max + 1e-9, factor_step):
        cal = DrawCalibrator(factor=factor)
        p_cal = cal.calibrate(probs)
        # ... 检查 dr >= recall_target
    return best_valid

# train_models.py L917-L928 — 策略选择：优先Threshold（不破坏ECE），仅当DrawCalibrator准确率高出≥0.5pp才选
candidates = [...]
valid_threshold = [c for c in valid_cands if c[4] == 'threshold']
valid_calibrator = [c for c in valid_cands if c[4] == 'calibrator']
if valid_threshold:
    best = max(valid_threshold, key=lambda x: x[1])
    if valid_calibrator:
        cal_best = max(valid_calibrator, key=lambda x: x[1])
        if cal_best[1] - best[1] >= 0.005:  # DrawCal 需高出 >=0.5pp 才选
            best = cal_best
```

**根因分析**：
1. **GBDT 模型天然对不平衡类别学习不足**：平局在足球比赛中占比约 25%，模型偏向预测胜/负
2. **后处理补丁的副作用**：概率校准恶化（ECE 上升）、平局精确率下降、联赛间迁移性差（每个联赛需单独搜索阈值）
3. **v8.3 已回退 argmax 决策**（温度 T=1.0），不再使用 DrawCalibrator 缩放概率

**优化建议**：P2-13（Focal Loss + 平局-specific 特征，从模型层面解决）

#### 2.4.3 分层评估

| 评估维度 | 框架要求 | 当前实现 | 差距 |
|---------|---------|---------|:--:|
| 按联赛 | ✅ | 有（逐联赛准确率） | 🟢 |
| 按赔率区间 | ✅ | 无 | 🔴 |
| 按主客场 | ✅ | 无 | 🟡 |
| 按赛季阶段 | ✅ | 无 | 🟡 |
| 按时间滚动 | ✅ | 无 | 🟡 |
| 按投注模拟 | ✅ | 无 | 🟡 |

**根因分析**：
1. **训练脚本只做全局评估**：`train_models.py` `evaluate_with_time_series_split()` 返回全局准确率和 LogLoss
2. **分层评估框架缺失**：无按赔率区间/主客场/赛季阶段的分层评估逻辑

**优化建议**：P2-14（分层评估体系，框架文档已提供 `stratified_rps_report()` 实现 L742-L794）

---

### 2.5 工程化 — 能用，但不够专业

#### 2.5.1 基础设施

| 对比项 | 框架要求 | 当前实现 | 代码证据 | 差距 |
|--------|---------|---------|---------|:--:|
| 数据库 | PostgreSQL | SQLite WAL | `odds.db` 466MB 单文件 | 🟡 |
| 特征计算 | 向量化 | 逐场 for 循环 | `sofascore_pre_match_features.py` L524-L555: 逐场调用 `aggregate_team_history()` | 🟡 |
| 实验追踪 | MLflow | 无 | `train_models.py` 搜索 "mlflow" 无结果 | 🟡 |
| 配置管理 | 统一配置中心 | config.yaml + 硬编码混合 | L60-L81: `XGB_OPTUNA_BEST`/`LGB_OPTUNA_BEST` 硬编码 | 🟡 |

**代码证据（硬编码超参数）**：

```python
# train_models.py L60-L100 — Optuna 最优参数硬编码在代码中
USE_OPTUNA_BEST_PARAMS = True
XGB_OPTUNA_BEST = {
    'max_depth': 4, 'learning_rate': 0.07, 'n_estimators': 130,
    'subsample': 0.785, 'colsample_bytree': 0.827, 'gamma': 5.0,
    'min_child_weight': 13, 'reg_alpha': 0.1, 'reg_lambda': 8.0,
    'scale_pos_weight': 2.108,
}
LGB_OPTUNA_BEST = {
    'max_depth': 4, 'learning_rate': 0.12, 'num_leaves': 128,
    'subsample': 0.680, 'colsample_bytree': 0.540,
    'reg_alpha': 3.353, 'reg_lambda': 13.904, 'min_data_in_leaf': 60,
}
```

**代码证据（逐场 for 循环性能瓶颈）**：

```python
# sofascore_pre_match_features.py L524-L555 — 逐场 for 循环，O(n²) 复杂度
for idx, match in matches_df.iterrows():
    if (idx + 1) % progress_every == 0 or idx == 0:
        pct = (idx + 1) / total * 100
        print(f"[SofaScore特征] 进度: {idx+1}/{total} ({pct:.1f}%)")
    # 每场都要做 DataFrame 过滤 → 时间复杂度 O(n²)
    home_features = aggregate_team_history(
        home_team, current_date, matches_df, stats_df, lineups_df, n_recent, half_life_days
    )
    away_features = aggregate_team_history(
        away_team, current_date, matches_df, stats_df, lineups_df, n_recent, half_life_days
    )
```

**根因分析**：
1. **硬编码配置根因**：Optuna 调参后需手动复制粘贴到代码，容易出错且无法追踪历史版本
2. **逐场 for 循环根因**：每次调用 `aggregate_team_history()` 都要做 `DataFrame` 过滤（`matches_df[(matches_df["home_team"] == team_name) | ...]`），5,252 场时每场遍历所有比赛，时间复杂度 O(n²)
3. **缺少 MLflow 根因**：训练实验无系统化追踪，无法对比不同超参数/特征集的效果

**优化建议**：P2-12（MLflow 实验追踪）+ P2-11（PostgreSQL 迁移）

#### 2.5.2 已达标项

- ✅ WAL 模式 + busy_timeout=5000ms（SQLITE_BUSY 已解决）
- ✅ Redis 缓存（TTL=7200s）
- ✅ PM2 + Cluster 水平扩展
- ✅ 模型热更新（零停机）
- ✅ 双库幂等写入（`INSERT OR IGNORE` + UNIQUE）
- ✅ 三重自动重训触发器 + 性能门禁

---

### 2.6 可量化性能差距总表

| 指标 | 框架基准 | 当前值 | 差距 | 等级 | 对应优化 | 根因 |
|------|---------|--------|------|:--:|---------|------|
| 胜平负准确率 | 52%–55% | 48.76% | -4~-7pp | 🔴 | P0-1/P0-3/P1-5 | 数据量不足+特征深度不够+缺少贝叶斯 |
| LogLoss | 0.95–0.98 | 1.0215 | +0.04~+0.07 | 🟡 | P0-2/P1-7 | 概率校准不够精细 |
| RPS | 0.19–0.21 | 0.2095(XGB)/0.2115(LGB) | 接近基准 | � | P0-2 ✅ | 已报告，列序bug已修复 |
| 过拟合差距 | <3pp | ~8pp | +5pp | 🔴 | P0-1/P1-5 | 数据量不足+GBDT过拟合 |
| 平局召回率 | 30%–35% | 33.8%→argmax | 已达标 | 🟢 | P2-13 | 后处理补丁有效但非根治 |
| xG 覆盖率 | >95% | 33% | -62pp | 🔴 | P0-1a/P1-8 | Understat 仅1赛季 |
| 数据量级 | 15,000–25,000 | 5,252 | 1/3~1/4 | 🔴 | P0-1 | 采集器无历史回溯 |
| 模型多样性 | 统计+树+贝叶斯 | 2GBDT+Poisson | 方法论单一 | 🟡 | P1-5/P1-7 | 缺贝叶斯和神经网络 |
| 不确定性量化 | 后验分布 | 无 | 完全缺失 | 🟡 | P1-5 | 频率派点估计 |

---

### 2.7 投注决策层（EV 期望值引擎）— 已实现（回测结论：无正 edge）

> **对标依据**: `docs/EV期望值引擎设计文档_v1.0.md`。原「完全缺失」，已于 2026-08-28~09-03 落地三层：`scripts/ev_engine.py`（纯函数决策引擎）、`scripts/ev_backtest.py`（三源对齐回测）、`scripts/generate_oof_predictions.py`（严格时序 OOF 预测）。

| 能力 | 现状（已实现） | 实现 |
|------|------|------------|
| edge 价值空间 | ✅ `edge = p_model − p_market` | `analyze_direction` |
| EV 期望值 | ✅ `EV = p_model×(odds−1) − (1−p_model)` | `calc_ev` |
| 去抽水独立度量 | ✅ `remove_vig` + vig/返还率 | `calc_implied_probabilities` |
| Kelly 仓位 | ✅ 1/4 凯利 + 单场 25% 上限 | `calc_kelly` |
| 投注决策 | ✅ VALUE / MARGINAL / AVOID（单一路径派生，17 单测通过） | `analyze_match` |
| 落库/评估 | ✅ 分决策/方向/联赛/edge 分桶 ROI 报告 | `ev_backtest.py` |

**关键结论（2026-09-03）——模型无可用正 edge**：

1. 旧 215 维「全量训练→全量预测」回测 ROI +17~22%/平均 EV +27% 为**时序泄漏伪象**。
2. 严格时序 OOF（208 维 `selected_features_20260828_174103` + `TimeSeriesSplit(5)`，11965 场，blend 准确率 52.87%、LogLoss 0.99）后重跑：mean/xgb/lgb 平注 ROI = **-7.81%/-6.47%/-7.32%**，平均 EV +21~23% 但实际 ROI 全负。
3. 赔率覆盖率修复（中文 match_id 队名桥接，44%→96.4%）后 mean 平注 ROI -5.41%、平均 EV +21.12%，edge 分桶非单调（edge>10pp 桶仍 -6.53%）→ 结论不变。
4. 分赛季 ROI 拆解（2026-09-03，C-20260903-005）：仅 2021-22 正 +10.47%（n=814），其余全负且**最新赛季反而更差**（2023-24 -5.98% / 2024-25 -7.16% / 2025-26 -10.20%），老赛季（桥接中文：2017-18 -7.19% / 2018-19 -6.61% / 2020-21 -8.88%）亦全负 → **排除「老赛季赔率质量差」假设**，负 edge 根因锁定为模型概率系统性高估（校准失调，与赛季/赔率源无关）。
5. **概率校准对比实验闭环（2026-09-03，C-20260903-006，11965 场 OOF × TSS(5) 折内 fit/折外 transform，无校准泄漏）**：6 方案（Platt 基线 / Temp(on raw) / Vector(on raw) / IsotonicOVR(on platt) / Temp+DrawCal(0.30) / Iso+DrawCal(0.30)）四维度（LogLoss / 平局召回 / ECE / EV 平注 ROI）对比如下：

| 方案 | LogLoss | 平局召回 | Top-class ECE | EV 平注 ROI | vs 基线ΔROI | 平局召回≥0.28 |
|------|--------:|--------:|--------------:|------------:|:-----------:|:---:|
| Baseline(Platt) | 0.9907 | 13.94% | 3.73% | **-5.41%** | — | ❌ |
| TempScaling(on raw, NLL最优) | 1.0136 | **27.16%** | 7.70% | **-3.71%** | +1.70pp | ✅（唯一兼顾） |
| VectorScaling(on raw) | 0.9936 | 0.27% | 5.76% | -5.83% | -0.42pp | ❌（平局类坍缩） |
| Isotonic-OVR(on platt) | 0.9977 | 2.32% | 6.78% | -8.28% | -2.87pp | ❌（平局系统低估） |
| Temp(on raw)+DrawCal(0.30) | 1.0264 | 39.81% | 9.68% | -5.44% | -0.03pp | ✅（过度抬平损害排序） |
| Iso+DrawCal(0.30) | 1.0018 | 8.07% | 8.19% | -8.23% | -2.82pp | ❌ |

→ **校准结论**：①仅 TempScaling(on raw) 同时满足平局召回≥0.28（27.16%）+ ROI 优于基线（-3.71%，Δ+1.70pp），推荐作为新生产校准；②Isotonic-OVR/VectorScaling 单独使用会坍缩平局召回（<3%），属禁用路径；③**校准本身无法使 EV ROI 转正**（Temp 仍 -3.71%）——系统性高估仅被部分压缩，转正需叠加以 EV/ROI 为目标的训练端改造 + EV 阈值抬升择场过滤 + edge 分桶单调回归（>10pp 桶专项修复）三方面。

6. **EV 择场阈值抬升扫描闭环（2026-09-04，C-20260904-001，9970 场 OOF × 72 组合 = 3 方案 × 6 min_ev × 4 置信）——纯择场证伪**：仅对 `decision=="VALUE"`（EV>threshold）下注（修正初版误含 MARGINAL 的语义 bug），全矩阵 **n≥100 无一 ROI 转正**。最优 = TempScaling(on raw)+min_ev=0.10+置信P50：平ROI **-2.93%**（n=3672，Δvs 基线 -5.15% +2.22pp）仍为负。反直觉规律：①min_ev 0.02→0.15 抬升时平均 EV 从 +22% 升至 +35~57%，ROI 反而恶化（Baseline -5.15%→-7.28%），高 EV 方向实际胜率系统性低于 EV 隐含胜率 → **edge 排序失效**；②置信 P90 过滤后平均赔率飙至 8.6~14.4、命中率暴跌至 9.8~12.4%（ROI -9~-25%），高置信被高赔率爆冷方向绑架；③最优组合分赛季仅 2019-20(+16.03%)/2021-22(+6.94%)/2022-23(+1.94%) 三季转正、2025-26 仍 -5.73%，无跨季一致性。→ **EV ROI 转正路径收敛：纯后处理择场（阈值/置信）已证伪，必须直接进训练端**（loss 对 EV/ROI 求导或 edge 分桶单调回归修复，即原 ③ 与训练端 ②）。

7. **edge 分桶单调回归修复闭环（2026-09-04，C-20260904-002，9970 场 OOF × TSS(5) 折内 fit/折外 transform，scripts/edge_monotonic_fix.py）——保序仅修复单调性、不产生正 edge；winner's curse 选择修正证伪**：6 口径对比如下——

| 方法 | 投注n | 平注ROI | 平局召回(argmax) | edge 分桶单调性 |
|------|------:|--------:|:---:|------|
| Baseline(Platt) | 9099 | -5.28% | 14.09% | 非单调 ❌ |
| TempScaling(on raw) | 9446 | -3.71% | 32.83% | 非单调 ❌ |
| Mono-Pooled(on raw) | 9464 | **-3.65%**（最优） | 32.02% | 非单调 ❌ |
| Mono-OVR(on raw) | 8251 | -5.42% | 0.92% | 非单调 ❌ |
| Mono-Pooled(on Temp) | 9450 | -3.74% | 32.30% | **单调递增 ✅**（0~3pp -13.4% → 3~6pp -4.9% → 6~10pp -3.1% → >10pp -1.5%） |
| Mono-Selected(on Temp) | 2892 | -6.28% | 32.83%（复用Temp） | 非单调 ❌（>10pp 高估 +17.5pp） |

→ **修复结论**：①**唯一单调方案 Mono-Pooled(on Temp)**（单一单调可靠性回归，pool 三类 (p,y) 拟合 p→P(y|p)，argmax 排序不变→平局召回结构性保持 32.3%）使 edge 分桶恢复单调递增，但**全桶仍负、最高 edge 桶 -1.5%**——保序回归只做「排序修复」、无法做「水平修复」（不能凭空产生正 edge）；②**选择条件化 winner's curse 修正（Mono-Selected）证伪**：对 EV 引擎选中的 max-EV 方向拟合 p→P(win|选中) 再重算 edge/EV，整体平ROI -6.28% 反而更差、分桶非单调、>10pp 桶高估幅度升至 +17.5pp（修正越修越差）；③Mono-OVR 平召仅 0.92% 再次证实逐类保序坍缩（pooled 单映射是保平局召回的必要设计）。→ **概率层四连证伪（校准 / 保序 / 择场 / 选择修正）：系统性概率高估在决策层无解，根治唯一剩路 = 训练端 EV/ROI 目标改造（loss 直接对 EV/ROI 求导）**。

8. **训练端 EV/ROI 目标改造闭环（2026-09-04，C-20260904-003，11965 场 OOF × TSS(5)，scripts/ev_loss.py + generate_oof_evloss.py）——朴素 EV-policy Loss 三档 λ 全负、证伪「对 EV 直接求导」，训练目标需重新设计**：实现 `L = CE + λ·(1 − p_y·o_y)`（EVL 负期望盈利，REINFORCE 策略梯度对 logit 求导，有限差分 gradcheck 通过），λ∈{0.3,1.0,3.0} 三档 OOF 重训后 edge_monotonic_fix 同口径评估——

| 版本 | λ | Baseline(Platt) 平ROI | TempScaling 平ROI | Mono-Pooled(on Temp) 平ROI | XGB 平局召回 |
|------|--:|------:|------:|------:|------:|
| 基线 | 0 | -5.28% | -3.71% | **-3.74%** | 27%+ |
| B2 | 0.3 | -13.59% | -8.65% | -8.50% | 9.24% |
| B1 | 1.0 | -12.97% | -9.43% | -8.18% | 12.26% |
| B3 | 3.0 | -13.53% | -10.92% | -10.99% | 5.28% |

→ **训练端改造结论（三档全负、全面劣于基线，朴素形式证伪）**：①**LGB 数值退化**——真类海森 p_y<0.5 时 EV 分量 `H_EV=p_k·(EV−r_k)·(1−2p_k)` 为负、被 floor 至 1e-6 后分裂增益失效（"No further splits with positive gain"），早停 1~3 轮、平局召回 0.0，blend 被污染；②**XGB 平局召回受压**（0.05~0.12 vs 基线 0.27），高 λ 更严重（λ=3.0 时 0.0528）；③**理论根因**：`dEVL/dp_y = −o_y` 对**高赔率真结果**放大梯度，而模型恰恰是在高赔率（高 edge）方向系统性过度自信——EV 分量把容量继续押向已过度定价的方向，**加剧而非修复**系统性高估（>10pp 桶 B1 -9.8% vs 基线 -1.5% 反更差）；④λ 越大 ROI 越差（0.3→1.0→3.0 基本单调恶化）。→ **结论：`loss 对 EV 直接求导`的朴素 EV-policy Loss 证伪**，需重新设计训练目标（方向：向市场赔率隐含概率蒸馏使 p_model→p_market 以对抗高赔率过度自信，或对「高赔率+未命中」样本显式惩罚，或结合决策策略梯度的双层目标）。

9. **市场赔率蒸馏（MOD）训练目标闭环（2026-09-04，C-20260904-004，D1 λ=1.0/γ=1.0，11965 场 OOF × TSS(5)，scripts/mod_loss.py）——MOD 蒸馏证伪，训练端「EV 奖励 / MOD 模仿」双路径均关闭，仅剩「对高赔率+未命中显式惩罚」**：实现 `L = CE + λ·KL(q_soft‖p)`（`q_k=(1/o_k)/Σ(1/o_j)` 去抽水、`q^γ` 温度软化、梯度 `p−onehot+λ(p−q_soft)`、海森恒正 `p(1−p)(1+λ)`，有限差分 gradcheck 三档 λ 通过）；同时**根因修复 LightGBM 4.x 自定义多分类目标 grad/hess 为 class-major 布局（order='F'）**——mod_loss/ev_loss 原用 order='C' reshape 致梯度布局错乱（"No further splits"、早停、概率扁平化），修复后 LGB 训练恢复（5 折 RPS 全达标 0.1935~0.2003、Train acc 0.57~0.63），并更正 C-20260904-003 中「LGB 数值退化」实为布局 bug 所致（XGB 三档全负结论不受影响）。edge_monotonic_fix 同口径评估——

| 方法 | 平注ROI | 投注n | 平局召回 | edge 分桶单调性 |
|------|------:|------:|------:|------|
| Baseline(Platt) | -4.50% | 8278 | 5.9% ❌ | 非单调 ❌ |
| Mono-Pooled(on raw) | **-3.55%** | 8853 | 10.1% ❌ | 单调 ✅（但全负 -6.3→-4.3→-1.0→-0.9） |
| Mono-Pooled(on Temp) | -4.64% | 8701 | 10.3% ❌ | 非单调 ❌ |
| Mono-Selected(on Temp) | -1.38% | 2521 | 10.2% ❌ | 非单调 ❌（3~6pp +2.8% 但 6~10pp -11.8% 崩坏） |

→ **结论（MOD 蒸馏证伪）**：①**平局召回全线崩溃**（5.9%~10.3% vs 基线 32.3%，违反 CALIB-003 ≥0.28 硬约束）——蒸馏把概率压向市场隐含概率（平局约 25%），模型几乎不预测平局；②**EV 回测 ROI 全部仍负**（Mono-Pooled(on raw) -3.55% ≈ 基线 -3.74%、Mono-Selected(on Temp) -1.38% 但非单调且 n=2521），转正组合 0 个；③**理论根因**——向市场蒸馏 = 押注「市场正确」，抹掉模型学习「市场定价错误在哪」的能力；市场含抽水长期 -5~-10% ROI，故蒸馏原理上无法产生正 edge，只能让模型校准到市场水平、永远无法超越市场。**训练端两条路径（EV 奖励 / MOD 模仿）均证伪，仅剩唯一方向：对「高赔率+未命中」样本显式惩罚（训练目标对高赔率方向惩罚过度自信，对抗而非奖励/模仿高赔率方向）。**

**架构根因修正**：预测引擎与价值判断已分离，决策层独立；但回测证明模型对市场的「优势」被系统性高估——`p_model` 在 EV 引擎下仍复刻不出真实 edge（edge 信号对实际赛果无预测力）。原预测「edge 会系统性偏小」需修正为「EV 被系统性高估、edge 与赛果脱钩」。经 C-20260904-003 进一步明确：**EV 系统性高估的机制 = 模型对高赔率方向过度自信（高赔率真结果的实际发生频率 < 模型概率），朴素「对 EV 求导」只会把梯度继续投向高赔率方向从而放大该偏差，根治须让训练目标对抗高赔率方向（向市场回归 / 非赢家惩罚）而非奖励它**。

---

## 三、优化路线图

### P0 — 立即执行（高收益，中低难度，1-2周）

| 编号 | 改进项 | 预期收益 | 难度 | 涉及文件 |
|:--:|--------|:--:|:--:|------|
| P0-2 | RPS 报告集成到 train_models.py ✅ **已完成** | 建立统一度量衡 | **极低** | `train_models.py`（添加 RPS 输出字段） |
| P0-4 | 统一比分-胜平负架构 ✅ **已完成** | 内部一致性 | 中 | 新建 `unified_prediction_engine.py` |
| P0-1 | 扩展数据到 8-10 赛季 ✅ **已完成** | 准确率+2-3pp | 中 | 新建 `backfill_collector.py` |
| P0-3 | 球员可用性特征 ✅ **已完成** | 准确率+1-2pp | 中 | 新建 `player_availability_features.py` |

### P1 — 重点执行（1-2月）

| 编号 | 改进项 | 预期收益 | 难度 |
|:--:|--------|:--:|:--:|
| P1-5 | 贝叶斯层级模型 ✅ **已完成（2026-08-28，聚合 RPS 0.2092 ≤0.21）** | 准确率+1-2pp，不确定性量化 | 高 |
| P1-6 | 情境化特征工程 ✅ **已实现并验证（2026-08-28，P1-10：14 维，A/B RPS/Acc 无增益，降维复查亦无子集，维持不启用）** | 准确率+1-2pp | 中 |
| P1-7 | 模型 Stacking（LR meta-learner）✅ **已完成（2026-08-28：5 基础模型 OOF + 多分类 LR，OOF RPS 0.1984 < 固定权重 0.2038）** | 准确率+0.5-1pp，RPS 改善 | 中 |
| P1-8 | xG 特征深化 ✅ **已实现并验证（2026-08-28：覆盖率 88.5% 达标、RPS 轻微劣化 +0.0028，暂不生产采用）** | 校准改善 | 中 |
| P1-9 | 时序赔率 + 比分赔率特征接入（含对齐链路补齐）✅ **已完成（2026-08-28，A/B：RPS -0.0161 / DrawRecall +0.49pp）** | RPS/校准改善 | 中高 |

### P2 — 持续优化（2-3月）

| 编号 | 改进项 |
|:--:|--------|
| P2-9 | 多博彩公司赔率一致性 ✅ **已启用（2026-08-28，P1-11，10 维，四指标全优）** |
| P2-10 | 位置-specific 球员特征 ✅ **已验证·维持不启用（2026-08-28 A/B：实现 11 维 F/M/D 位置特征并入 `sofascore_pre_match_features.py`，真实数据 A/B 对比 baseline（23 维全队）vs full（+22 维权主客），blend 平局召回 0.3788→0.3384（-4.04pp）劣化，RPS 0.2047→0.2048 持平，Accuracy 无稳定提升 → 不启用）** |
| P2-11 | 数据库迁移 PostgreSQL ✅ **已完成（2026-08-28：33 表 424 万行全量迁移 0 差异，三张大表按日期分区 + 11 性能索引，批处理型查询 9-338x 提速，`db_utils.py` 双后端适配层就绪）** |
| P2-12 | MLflow 实验追踪 🔴 未完成 |
| P2-13 | 平局模型层面解决（Focal Loss） ✅ **已验证·维持不启用（2026-08-28 A/B：实现+梯度校验通过，9 组网格实测无组合满足「平局召回≥0.30 且 RPS 不劣化」，baseline `class_weights` 已使 argmax 平局召回 0.3788）** |
| P2-14 | 分层评估体系（6+维度） ✅ **已完成（2026-08-28：`stratified_evaluation.py` 6 维度接入 `train_models.py`，输出 Acc/LogLoss/RPS + 价值投注 ROI）** |
| P2-15 | 自动化数据质量监控 ✅ **已完成（2026-08-28：`quality_gate.py` 接入训练 Pipeline 前置步骤，pass/warning/fail 三级 + 自动告警）** |

| P2-16 | EV 期望值引擎（`ev_engine.py` 决策层）✅ **已完成（2026-09-03~04：`ev_engine.py` 决策引擎 + `ev_backtest.py` 回测 + 严格时序 OOF；回测结论 mean 平注 ROI -5.41%、无正 edge；概率校准对比实验（6 方案）确认 TempScaling(on raw) 为当前最优生产校准，ROI 收窄至 -3.71%（Δ+1.70pp）、平局召回 27.16% 达标，但 EV ROI 仍未转正；EV 择场 72 组合扫描（min_ev × 置信）证伪纯后处理转正，最优 Temp+0.10+P50 仍 -2.93%；edge 分桶单调回归修复（edge_monotonic_fix.py）证伪保序/选择修正可转正——唯一单调方案 Mono-Pooled(on Temp) 仍 -3.74%、winner's curse 选择修正 -6.28% 更差，转正路径收敛至训练端 EV/ROI 目标改造）** |

### 依赖关系

```
P0-2(RPS报告) ──> 可立即执行（改一行代码）
P0-4(统一架构) ──> 可立即执行
P0-1(数据扩展) ──> 可后台运行
    ├──> P0-3(球员可用性)
    ├──> P1-5(贝叶斯)
    ├──> P1-6(情境特征)
    └──> P1-8(xG深化)
P1-5(贝叶斯) ──> P1-7(Stacking)
P0-1(数据扩展) ──> P1-9(时序/比分赔率接入，先补齐对齐链路)
P1-9 ──> P2-9(多公司一致性，共用百家欧指共识源)
```

**建议顺序**：P0-2（5分钟） → P0-4 → P0-1（后台） → P0-3 → P1-5 → P1-6 → P1-7 → P1-8 → P1-9（先补齐对齐链路）

---

## 四、详细执行计划

### P0-2: RPS 报告集成

**难度**: 极低 | **预期耗时**: 5分钟 | **风险**: 无

**目标**：将 `advanced_model_trainer.py` L1647-L1653 中已有的 RPS 计算集成到主训练输出。

**具体步骤**：
1. 在 `train_models.py` 中添加 RPS 计算函数（从 `advanced_model_trainer.py` 复制或导入）
2. 在 `final_report['models'][mname]` 的 `training` 和 `recommended` 字段中添加 RPS

**修改代码**（`train_models.py` L983-L1049 附近）：

```python
# 在 final_report['models'][mname]['training'] 中添加：
'rps': float(rps_val),  # 新增

# 在 final_report['models'][mname]['recommended'] 中添加：
'rps': float(rps_val),  # 新增
```

**验收标准**：每次训练报告包含全局 RPS + 各联赛 RPS，可与基准 0.19-0.21 对比。

---

### P0-4: 统一比分-胜平负架构

**难度**: 中 | **预期耗时**: 3-5天 | **风险**: 准确率下降

**目标**：ML 回归 λ → Dixon-Coles 生成比分矩阵 → 全概率边际导出。

**当前架构问题**：
- T-005（让球胜平负）和 T-006（比分）是独立并行模型
- T-006 v4 通过 WDL 重加权后处理缓解不一致，但根本架构未统一

**目标架构**：
```
ML 回归模型 → λ_home, λ_away → Dixon-Coles 比分矩阵 → 
  ├─ 胜平负概率（边际分布）
  ├─ 让球概率（条件分布）
  ├─ 大小球概率（条件分布）
  └─ 精确比分（矩阵直接输出）
```

**具体步骤**：
1. 新建 `scripts/unified_prediction_engine.py`：`DixonColesGenerator` 类（框架文档已提供完整代码 L1347-L1460）
2. 修改 `train_models.py`：新增 `use_unified_engine` 开关，训练目标从分类改为回归（λ_home, λ_away）
3. 修改 `prediction-engine.js`：`predictStacked()` 新增统一引擎分支
4. 修改 `config.yaml`：新增 `unified_engine` 配置节

**修改文件**：
- 新建 `scripts/unified_prediction_engine.py`
- 修改 `scripts/train_models.py`
- 修改 `shared/prediction-engine.js`
- 修改 `config.yaml`

**验收标准**：wdl_sum=1.0 差异<1e-6，准确率下降<1pp。

**风险缓解**：
- 回归目标噪声大 → 用 Poisson 回归（`objective='count:poisson'`）而非平方误差
- ρ 参数联赛差异 → 复用现有 `config.yaml` 联赛 ρ 配置
- 旧 API 兼容性 → `config.yaml` 的 `use_unified_engine: false` 开关回退

---

### P0-1: 数据扩展 8-10 赛季

**难度**: 中 | **预期耗时**: 7-14天（后台运行） | **风险**: fbref 反爬

**目标**：回溯采集 2016-2023 赛季，matches 表 5,252→15,000+。

**具体步骤**：
1. 新建 `scripts/backfill_collector.py`（框架文档已提供完整代码 L467-L623）
2. 运行 `python scripts/backfill_collector.py --start-season 2016-2017 --end-season 2023-2024 --league all`
3. 采集完成后重跑 `features/sofascore_pre_match_features.py` 全量生成特征
4. 重训模型

**数据库变更**：无需新建表，完全复用现有表结构（`matches`、`match_player_stats`、`match_lineups`、`fbref_match_mapping`、`fbref_players`）

**新增进度文件**：`data/backfill_progress.json`（断点续传）

**验收标准**：matches ≥ 15,000 行，≥ 8 赛季，球员统计 ≥ 80%。

**风险与回滚**：
- fbref 反爬升级 → Playwright 非 headless 兜底，代理池
- 采集前自动备份 `odds.db` 为 `odds_backfill_backup_{timestamp}.db`

---

### P0-3: 球员可用性特征

**难度**: 中 | **预期耗时**: 3-5天 | **风险**: 伤病数据源不可靠

**目标**：预计首发 11 人加权替代全队历史平均。

**当前问题**：
- `aggregate_team_history()` 对所有出场球员一视同仁聚合
- 仅评分区分首发/替补，其他特征无区分
- 升班马球队返回全 0 特征

**具体步骤**：
1. 新建 `features/player_availability_features.py`：12 维特征 × 主客 = 24 维
   - 预计首发 11 人加权评分/xG（替代全队历史平均）
   - 伤病/停赛扣减因子
   - 核心球员依赖度（xG 占比）
   - 疲劳状态（近 7/14 天出场分钟数）
   - 阵容稳定性（首发变化率）
2. 修改 `features/sofascore_pre_match_features.py`：追加 24 维
3. 修改 `scripts/feature_utils.py`：集成新特征

**修改文件**：
- 新建 `features/player_availability_features.py`
- 修改 `features/sofascore_pre_match_features.py`
- 修改 `scripts/feature_utils.py`

**验收标准**：24 维非零率 ≥ 70%，SHAP 分析 ≥ 3 维进入 Top30。

---

### ✅ P0-修复: 天气/伤病因子数据驱动化 [已完成]

**难度**: 中 | **预期耗时**: 已完成 | **风险**: 无（保留降级机制）

**问题**：`prediction-engine.js` 中 `calcWeatherImpact`/`calcSurfaceImpact` 有实现，但上层从未传入实际数据，`injury`/`keyPlayer`/`weather` 因子始终使用默认值 1.0，形同虚设。

**修复方案**：三端联动修复

#### 1. Python 端 — `features/match_condition_features.py` [新建]

```python
# 核心入口: build_match_conditions(home_team, away_team, match_date, league)
# 返回完整 match_conditions dict，可直接传给 prediction-engine.js 的 options
class MatchConditionFeatures:
    def calc_injury_factor(self, ...) -> Dict:    # 基于 SofaScore 出场数据
    def calc_weather_factor(self, ...) -> Dict:   # 基于联赛-月份气候均值
    def build_match_conditions(self, ...) -> Dict: # 综合入口
```

**数据来源**：
- 伤病因子: SofaScore `match_player_stats` 表，核心球员判定（出场率>60%+评分前5）
- 天气因子: 五大联赛城市 1991-2020 30年气候均值（降级方案），未来可接入实时天气 API

**配置**：`config.yaml` 新增 `match_conditions` 配置节（L419-L475）

#### 2. JS 端 — `prediction-engine.js` [已修改]

**修改文件**：`shared/prediction-engine.js`

**变更点**：
1. `calcWeatherImpact()` (L98-L122) — 优先使用 Python 生成的 `attack_impact`/`defence_impact`，降级到旧硬编码
2. `calcLambdaMatch()` (L157-L186) — 新增 `options.matchConditions` 参数，伤病因子三级优先级：
   - `matchConditions.home_injury` (Python 数据驱动) > `teamA.injury` (上层传入) > `1.0` (默认值)

**降级机制**：当 Python 未生成数据时，自动回退到旧硬编码，零破坏性。

#### 3. 数据库变更

无需新建表，完全复用现有表结构：
- `match_player_stats` — 伤病因子数据源

#### 验收标准

| 指标 | 修复前 | 修复后 |
|------|:--:|:--:|
| 伤病因子默认值使用率 | 100%（全为 1.0） | < 5%（仅升班马/无数据降级） |
| 天气因子默认值使用率 | 100%（全为 1.0） | 0%（气候均值覆盖全部联赛） |
| λ 攻击因子变化范围 | 固定 1.0 | 0.80-1.15（真实偏离） |

#### 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `features/match_condition_features.py` | 新建 | Python 比赛条件特征生成器 |
| `shared/prediction-engine.js` | 修改 | `calcWeatherImpact`/`calcLambdaMatch` 数据驱动化 |
| `config.yaml` | 修改 | 新增 `match_conditions` 配置节 |

---

### P1-5: 贝叶斯层级模型

**难度**: 高 | **预期耗时**: 2-3周 | **风险**: MAP 优化收敛

**目标**：实现 Baio & Blangiardo (2010) 贝叶斯层级足球预测模型。

**核心公式**：
```
log(λ_home) = μ + home_adv + attack_i - def_j
log(λ_away) = μ + attack_j - def_i
attack_i ~ N(attack_prior_i, σ_attack²)   # 层级先验
def_i ~ N(def_prior_i, σ_def²)
```

**具体步骤**：
1. 新建 `scripts/bayesian_hierarchical_model.py`（框架文档已提供完整代码 L1518-L1916）
   - `BayesianHierarchicalModel` 类含 MAP 优化（L-BFGS-B）
   - Dixon-Coles ρ 修正
   - 时间随机游走（赛季内参数变化）
   - 升班马自动用联赛平均先验
2. 每联赛单独训练一个贝叶斯模型（`bayesian_model_{league}.json`）
3. 在 Stacking 集成（P1-7）中作为基础模型

**新增数据库表**（可选）：
```sql
CREATE TABLE IF NOT EXISTS bayesian_team_params (
    league TEXT, team TEXT, attack REAL, defense REAL,
    overall REAL, attack_std REAL, defense_std REAL,
    last_updated TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (league, team)
);
```

**验收标准**：贝叶斯模型单独 RPS ≤ 0.21，Stacking 后 RPS 优于单模型。

---

### P1-6: 情境化特征工程

**难度**: 中 | **预期耗时**: 1-2周 | **风险**: 积分榜数据依赖

**目标**：覆盖战意、赛程疲劳、心理情绪三个维度，输出 20 维特征。

**特征清单**：
- 战意维度（6维）：积分压力、赛季阶段、争冠标记、保级标记、欧战资格、无欲无求
- 赛程/疲劳维度（6维）：休息天数、休息天数差、近7天比赛数、近14天比赛数、一周双赛、旅行距离
- 心理/情绪维度（8维）：德比标记、德比激烈程度、新教练效应、连胜/连败、爆冷标记、士气评分、主客场表现差

**具体步骤**：
1. 新建 `features/contextual_features.py`（框架文档已提供完整代码 L2007-L2199）
2. 新建 `standings_history` 和 `derby_pairs` 两张数据库表
3. 修改 `scripts/feature_utils.py`：集成情境特征

**修改文件**：
- 新建 `features/contextual_features.py`
- 修改 `scripts/feature_utils.py`
- 新增数据库表：`standings_history`、`derby_pairs`

**验收标准**：新增 ≥ 10 维情境特征，SHAP 分析显示至少 3 维进入 Top20 重要性。

**执行结果（2026-08-28，P1-10，暂不启用）**：

- 实际产出 14 维情境特征（`scripts/contextual_features.py`，赛程密度/休息天数/主客连续作战/积分排名压力/德比/升班马身份等）。
- 联合 A/B（XGB+LGB 融合，滚动时间切分）：基线 vs 基线+情境特征，RPS / Acc 无显著增益（甚至轻微劣化），未达「RPS 不劣化、Acc 不降」验收线。
- **结论**：暂不生产启用（`ctx_features=False`）。14 维中可能混有价值项与噪声项，作为「降维后再议」跟进任务，逐维 ablation/SHAP 后再决定是否保留子集。
- **跟进任务（降维后再议）→ 已闭环（2026-08-28）**：逐维 LoO + 分组 ablation + 2-fold A/B 诊断（`_tmp_p110_shap_ablation.py`）确认 14 维无有价值子集——逐维 RPS 边际全部 ≤|0.0003|（噪声内），分组边际 ≤|0.0002|，2-fold 全量 full(+14) RPS 0.2018 与基线 0.2017 持平（略差 0.0001），无任何子集稳定优于基线 → 维持 `ctx_features=False`，无需降维启用。

---

### P2-9: 多博彩公司赔率一致性

**难度**: 中 | **风险**: 依赖 P1-9 时序赔率同源的百家欧指共识源

**目标**：接入百家欧指共识，构建「竞彩 vs 市场共识」偏离度特征，识别共识冷门/过热比赛。

**执行结果（2026-08-28，P1-11，✅ 已启用）**：

- 实际产出 10 维赔率一致性特征（`scripts/odds_consensus_features.py`，竞彩 vs 百家欧指共识偏离度）。
- 初版 A/B 因 RPS 列序 bug（`RPS_CLASS_ORDER=[2,1,0]` 与生产 `compute_rps` 的 `[0,1,2]` 冲突）导致 RPS 虚高至 0.3408，误判为「RPS 劣化」。
- 修复 RPS 口径后重验：四指标全优（RPS / LogLoss / ECE / Acc / DrawRecall 均改善），遂启用 `consensus_odds=True`，模型维度 198 → 208。
- **跟进任务（降维后再议）→ 已闭环（2026-08-28）**：逐维 LoO + 分组 ablation 诊断（`_tmp_p111_shap_ablation.py`）否决「市场回声拖累」假设——逐维 RPS 边际全部 ≤|0.0007|（噪声内），剔除「共识水平」组反而 RPS 变差（+0.0008）。2-fold 全量 full RPS 0.2000 为全配置最优，无子集优于全量 → 维持全量 10 维，无需降维。

---

### P1-7: 模型 Stacking 集成

**难度**: 中 | **预期耗时**: 1-2周 | **风险**: 依赖 P1-5 贝叶斯模型

**目标**：从固定权重加权平均升级为真正的 Stacking + LR meta-learner。

**当前问题**：
- 固定权重（DC 30%+XGB 30%+LGB 25%+Elo 15%）非从数据学习
- 模型多样性不足（2个GBDT+2个Poisson变体）

**目标架构**：
```
基础模型池: Dixon-Coles + Elo + XGBoost + LightGBM + 贝叶斯（5个不同方法论）
    ↓
各模型输出 WDL 概率 → 15维特征 (5模型 × 3类)
    ↓
LR meta-learner（TimeSeriesSplit 训练，防数据泄露）
    ↓
最终 WDL 概率
```

**具体步骤**：
1. 修改 `scripts/prediction_core.py`：添加 `StackingMetaLearner` 类
2. 修改 `scripts/train_models.py`：添加 Stacking 训练逻辑
3. 修改 `shared/prediction-engine.js`：更新 `predictStacked()`

**验收标准**：Stacking 模型 RPS 优于任何单模型，meta-learner 系数合理。

**执行结果（2026-08-28，✅ 已完成）**：
- 新增 `scripts/train_stacking_meta.py`：5 基础模型（DC/Elo/XGB/LGB/贝叶斯）→ 15 维 meta 特征 → TimeSeriesSplit（5 折时间序列，无 shuffle）OOF 预测 → 多分类 LogisticRegression（solver=lbfgs）。
- `prediction_core.py` 接入 `_load_stacking_meta_learner` + `apply_stacking_meta_learner`，融合优先级 LR meta-learner → 固定权重 → 赔率隐含概率。
- 指标：OOF 全量 RPS 0.1984 < 固定权重 0.2038（改善 0.0054）；第 5 折 holdout RPS 0.2043（轻微泛化代价，接受）。n_train=11925。

---

### P1-8: xG 特征深化

**难度**: 中 | **预期耗时**: 1-2周 | **风险**: 依赖 P0-1 数据扩展

**目标**：利用已有 44,385 条射门数据深化 xG 特征。

**深化方向**：
- **xG 差值趋势**：近5场 xG - xGA 的变化斜率（动量信号）
- **定位球分离**：点球 xG、运动战 xG、角球 xG 分别建模
- **Bayes-xG 修正**：层级贝叶斯收缩，区分"射手天赋"和"机会质量"
- **xG 差值分位**：xG_diff 在联赛中的百分位

**具体步骤**：
1. 扩展 `features/sofascore_pre_match_features.py`：添加 xG 深化特征
2. 修改 `scripts/feature_utils.py`：集成新特征

**验收标准**：xG 相关特征 SHAP 重要性提升，xG 覆盖率提升至 ≥ 80%。

---

### P1-9: 时序赔率 + 比分赔率特征接入（含对齐链路补齐）

**难度**: 中高 | **风险**: 依赖先补齐数据对齐链路（对齐不做，时序特征只对 ~3.8k 场生效）

**背景（Step 0 实测，2026-08-28，`data/odds.db`）**：
- `sofascore_team_features` 仅 **5,334 行**（23/24~26/27 约 3 季）——回采的 16/17~22/23 七季球员数据（`match_player_stats` 已 18,038 场）尚未建成特征表，是当前球员特征的硬上限
- 时序赔率历史表 12,575~13,126 场，但经 `match_id_mapping`（仅 3,857 行）+ `match_id_en` 直连，对齐到 `matches` 仅约 **3,806 场**（wdl）/ 3,909（handicap）——其余 ~9k 场赔率在库里却进不了训练集
- `matches.league` 字段 **9,111 行为 NULL**（联赛信息实际在 `match_type` 字段）

**目标**：把被 `slim_odds=True` 跳过的 D-013 时序赔率 + T-003.1 比分赔率真正接入特征层（扩展而非新建），但仅在补齐上述对齐链路后。

**具体步骤**：
1. **补齐对齐链路（前置）**：
   - 重建 `features/sofascore_pre_match_features.py` → `sofascore_team_features`，覆盖 16/17~22/23（源表 `match_player_stats` 已具备）
   - 补齐 `match_id_mapping`，将 13,125 场时序赔率对齐到 `matches`（对齐率 → ≥98%）
   - 统一 `matches.league`（用 `match_type` 回填 9,111 行 NULL）
2. **扩展 `scripts/d013_temporal_odds.py`**（不新建模块）：接入已闲置的 `load_timing_handicap` / `load_timing_total_goals`，补「跨盘口一致性」+「去水后隐含概率漂移」，替代「开→终漂移」（竞彩终盘非真终盘，且 `*_history` 无 open/close 字段）
3. **接入 `scripts/score_features.py` 比分赔率**（T-003.1，`score_history` 13,122 场已齐）
4. **修改 `scripts/feature_utils.py` `build_all_features()`**：放开 slim_odds 对 D-013/T-003.1 的跳过，追加 `odds_ts_*` / `score_*` 列（新增 `ts_odds` 开关，默认 False 保证可回退 A/B）
5. **联赛条件化**：基础特征加 `league_id` + 时序特征按联赛 z-score 归一 + 输出层按联赛 isotonic 校准（不拆 5 套模型）

**联动 P2-9**：竞彩 vs 百家欧指共识偏离度（欧指 57 家公司）与 P2-9「多博彩公司赔率一致性」同源，可在 P2-9 一并补充。

**验收标准**：时序赔率对齐率 ≥98%；滚动赛季切分 A/B（RPS/LogLoss 分层），B 组显著优于 A 基线；比分赔率特征非零率 ≥70%。

**执行结果（2026-08-28，✅ 已完成，详见 change_log C-20260828-002/003/004）**：

| 步骤 | 实际结果 |
|:--:|------|
| 1. 对齐链路补齐 | `matches.league` NULL 9,111→0（match_type 回填）；`match_id_mapping` 3,857→12,968 行（direct 9,354 + cn_to_en 3,200）；`sofascore_team_features` 5,334→18,257 行（16/17~26/27 全季）；wdl/handicap/total_goals 三链路对齐率均 **99.4%**（≥98% 达标）。误判根因：此前 ~3.9k 可训练样本漏算了「时序 match_id 与 matches.match_id 一致」直连通道（占 ~71%） |
| 2. D-013 扩展 | 22 维：WDL 时序 10 + 去水隐含概率漂移 4 + 让球时序 3 + 大小球时序 3 + 跨盘口一致性 2；修正 total_goals 比分赔率直接求和 bug |
| 3. 比分赔率接入 | `score_features.py` 改三通道批量对齐，对齐 12,878/13,081（**98.45%**），特征覆盖 14,312 场中的 89.5% |
| 4. ts_odds 开关 | `build_all_features()` 新增 `ts_odds`（默认 False 可回退）；开启后 B 组 233 维 vs A 组 200 维（+33 = 时序 22 + 比分 8 + T-003.3 扩展 3） |
| 5. 联赛条件化 | D-013/T-003.1 时序特征按联赛 z-score 归一（`_league_zscore`，屏蔽无数据行）；`league_id` 基础特征待主训练管线复核 |

**A/B 验证（`_tmp_p19_feature_check.py`，14,312 场滚动时间切分 LightGBM，无 shuffle）**：

| 指标 | A 基线（200 维） | B 实验（233 维） | 差值 | 结论 |
|------|:--:|:--:|:--:|:--:|
| RPS | 0.3627 | **0.3466** | **-0.0161** | ✅ B 显著优 |
| DrawRecall | 4.65% | **5.14%** | **+0.49pp** | ✅ B 优 |
| Acc | **51.95%** | 51.00% | -0.94pp | ⚠️ A 优 |
| LogLoss | **1.0250** | 1.0308 | +0.0059 | ⚠️ A 优 |

新特征非零率：时序 22 维均 81.9%、比分 8 维均 88.2~88.3%（全部 ≥70% 达标）。**结论**：RPS/DrawRecall 改善达标；Acc/LogLoss 轻微代价，且本次 A/B 为快速 LightGBM（无生产校准链路 Threshold+Stacking），生产默认 `ts_odds=False` 零回归，正式采用前需在完整校准链路下复验。**遗留**：PA 特征追加因 `No module named 'features'` 未生效（独立模块导入路径问题，不影响本次 52 列结构）。

---

### P2-11~P2-15: 工程化与评估增强

| 编号 | 改进项 | 具体动作 | 验收标准 |
|:--:|--------|---------|---------|
| P2-11 | PostgreSQL 迁移 ✅ | 将 odds.db 迁移到 PostgreSQL，建立索引和分区表 | 查询性能提升 ≥ 5 倍（批处理型查询达成） |
| P2-12 | MLflow 实验追踪 | 部署 MLflow，所有训练实验自动记录 | 可通过 MLflow UI 对比历史实验 |
| P2-13 | 平局模型层面解决 | Focal Loss + 平局-specific 特征 | 平局召回率 ≥ 30% 不依赖后处理 |
| P2-14 | 分层评估体系 | 按联赛/赔率区间/主客场/赛季阶段分别评估 | 训练报告包含 ≥ 6 个分层维度 |
| P2-15 | 自动化质量监控 | 集成到训练 Pipeline 前置步骤 | 数据质量异常自动告警 |

**执行结果（2026-08-28，P2-11 ✅ + P2-13 ✅ 已验证·维持不启用 + P2-14 ✅ + P2-15 ✅）：**

- **P2-11 PostgreSQL 迁移**：新建 `scripts/pg_migrate.py`（6 阶段幂等迁移：export→createdb→schema→import→postprocess→verify，支持断点重跑）+ `scripts/extract_schema.py` + `scripts/fix_numeric_csv.py` + `scripts/pg_benchmark.py` + `db_utils.py`（应用层双后端适配）。**迁移结果**：33 表 / 424 万行 / 1.5GB 全量迁移，verify 逐表行数校验 **0 差异**。三张大表按日期 RANGE 分区（`match_player_stats` / `match_lineups` 物化 `match_date DATE` 来自 `fbref_match_mapping` JOIN；`score_history` 按 `timestamp` 分区，均带 DEFAULT 分区），11 个追加性能索引 + ANALYZE。**迁移中解决的关键问题**：(1) SQLite 宽松类型——DDL 注释导致列解析漏列、INTEGER 列实际含小数（`match_player_stats.assists` → DOUBLE PRECISION，全库扫描仅此一列）、19,747 条坏时间戳置 NULL 与分区表 PK 隐式 NOT NULL 冲突（去掉 `score_history` PK，id 由 IDENTITY 保唯一）；(2) pg8000 纯 Python 驱动——COPY FROM STDIN 用 `execute(stream=)` 实现、连接超时 60s→3600s；(3) Python 3.14 无 psycopg wheel → 便携版 PostgreSQL + pylibs/pg8000。**性能基准（20 轮中位数，`pg_benchmark.py`）**：批处理型查询大幅领先——Q5 多公司赔率聚合 **338x**（SQLite 该表 match_id 无索引全表扫 471ms vs PG 1.4ms）、Q6 全表赛季汇总 **9.2x**（SQLite 逐行 JOIN 2.57s vs PG 分区裁剪+并行 280ms）；PA 核心查询 Q2 1.8x / Q4 1.6x（PG 物化 match_date 免 JOIN）；小结果集点查 Q1/Q3 SQLite 占优（进程内 vs 网络+纯 Python 解析，架构本质）；几何平均 2.4x——**结论：特征生成/回测等批处理负载达成 ≥5x 目标，点查型负载 PG 无优势**。**应用层适配**：`db_utils.py` 提供 `connect(backend)`（`DB_BACKEND=pg/sqlite` 环境变量切换，默认 SQLite 零风险）+ `read_sql()`（替代 `pd.read_sql`，pandas 不支持非 SQLAlchemy 的 PG 连接）+ `?`→`%s` 占位符自动转换 + sqlite3.Row 风格键访问；双后端冒烟测试结果完全一致。**TEXT 列类型优化（`optimize_ouzhi_types`，幂等）**：`odds500_ouzhi_company` 20 个 TEXT 数值列 → `double precision`——纯数值 6 列（`init_*`/`live_*`）直接 cast、百分比 8 列（`prob_*`/`return_*`，`"72.06%"`→`72.06` 去 % 保真，下游 `_to_float/100` 语义一致）、`kelly_*` 6 列全 NULL 转数值类型；`AVG(init_win/prob_init_win/return_init)` 现已无需 cast 直接可用。**应用层接入 PG（核心脚本落地）**：`db_utils.connect()` SQLite 分支统一返回 `sqlite3.Row` 并封装 `PRAGMA busy_timeout`；[`player_availability_features.py`](features/player_availability_features.py) 改用 `db_utils.connect(backend)` + `--backend`/`DB_BACKEND` 双后端切换（`PRAGMA` 仅 SQLite 后端生效），端到端验证 4 支球队 × 12 维特征双后端 **0 差异**；`generate_all` 的 `matches_query` 补确定性 tiebreaker（`ORDER BY match_date DESC, fbref_match_id`，修复同日多场在 SQLite/PG 顺序漂移，全序 18257 行现完全一致）。**待办**：其余脚本按需切换 `DB_BACKEND=pg`。
- **P2-11 应用层批量切换 PG 后端（特征生成 + 回测脚本，2026-08-29 ✅）**：按「批处理优先」原则批量落地 `DB_BACKEND=pg`，替换 `sqlite3.connect` / `pd.read_sql` / `PRAGMA` / `row_factory` 等 SQLite 特有语法为 `db_utils.connect` / `db_utils.read_sql`。**改造清单**：特征生成——`scripts/feature_utils.py`、`scripts/hcp_features.py`、`scripts/tg_features.py`、`scripts/d013_temporal_odds.py`、`scripts/score_features.py`、`scripts/xg_deep_features.py`、`scripts/odds_consensus_features.py`、`features/sofascore_pre_match_features.py`、`features/match_condition_features.py`；回测——`scripts/backtest_full_three_way.py`（v1/v2/v3）、`scripts/compute_hitrate_3matches.py`（`features/player_availability_features.py` 前序已接入）。**双后端验证（sw-4）**：14 个模块 import 全通过；5 张核心表行数 0 差异（matches 14,418 / wdl_history 59,877 / handicap_history 61,452 / total_goals_history 35,300 / score_history 816,026）；`build_match_alignment` 对齐映射 13,046 一致；回测查询模式（`match_id_en` 去重 3,035/3,098/3,099 + `ORDER BY timestamp DESC LIMIT 1`）双后端一致。**发现 1 处数据一致性差异（迁移工具遗留，非本次代码切换引入）**：PG 中 `handicap_history` 与 `total_goals_history` 各有 28 行 `timestamp` 为 NULL（SQLite 为 0），致 `timestamp < '2026-07-01'` 过滤后 PG 少 28 行（≈0.046%：`load_hcp_data` 59,933 vs 59,961、`load_tg_data` 34,042 vs 34,070），根因为 `pg_migrate.py` 迁移时 28 条坏时间戳解析失败置 NULL，需在迁移侧补回填/剔除。**保留项**：`hcp_features.py` 保留 `import sqlite3` 仅供 `Optional[sqlite3.Connection]` 类型注解；`feature_utils.py` 的 `load_match_data` 仍用 sqlite3（读未纳入 PG 迁移的 `five_leagues.db` 旧库，刻意兜底）。**写库路径收尾（`df.to_sql` 兼容，2026-08-29 ✅）**：`db_utils` 新增 `write_dataframe(conn, df, table_name)`（`executemany` 批量 INSERT，`NaN/NaT → NULL`、`datetime → ISO 字符串`，双后端一致），替代 `df.to_sql`（后者仅支持 SQLAlchemy 引擎 / sqlite3 连接，pg8000 裸连接不兼容）。已落地：`features/sofascore_pre_match_features.py` 的 `save_to_db`（建表数值列改用 `numeric_sql_type(conn)` 适配 `REAL`/`DOUBLE PRECISION`）；`scripts/player_data_analysis.py` 的 `apply_cleaning_to_db`（`players`/`player_stats` 表，为全库最后两处 `df.to_sql`）。全库 grep 确认 0 残留 `df.to_sql`，`write_dataframe` 已在 SQLite/PG 双后端行为一致。
- **P2-13 Focal Loss（模型层面解决平局，不依赖后处理）**：新建 `scripts/focal_loss.py`，实现多分类 Focal Loss 自定义目标 `focal_obj_xgb`（XGBoost）与 `focal_fobj_lgb` / `focal_feval_lgb`（LightGBM），公式 `FL = -(1-p_t)^gamma * log(p_t)`，支持 `alpha` 类别权重（平局-specific 上调，默认 `[1.0, 1.4, 1.0]`）。核心解析梯度/海森 `focal_grad_hess()` 返回 `(n, K)` 形状（兼容 XGBoost 2.1+ 与 LightGBM 4.x）。已集成到 `train_models.py`：新增开关 `USE_FOCAL_LOSS`（默认 False）+ `FOCAL_GAMMA=2.0` + `FOCAL_ALPHA`，XGB/LGB 训练分支按开关启用；LightGBM 用 `focal_feval_lgb` 提供 softmax 后正确的 `multi_logloss` 作为早停指标（解决自定义目标下内置指标不套 softmax 的误导问题）。**验证状态**：`python scripts/focal_loss.py` 有限差分梯度校验通过（gamma=2.0 梯度误差 8.8e-11、海森 2.1e-11；gamma=0 严格退化为交叉熵，误差 0）；四脚本 `py_compile` 通过。**A/B 实测结论（2026-08-28，14,312 场 / 验证集 2,863 场，blend 50:50 argmax 无后处理）**：baseline Acc 0.4897 / LogLoss 1.0093 / **RPS 0.2047 / 平局召回 0.3788（已≥0.30）**；网格搜索 gamma∈{0.5,1.0,1.5} × alpha_draw∈{1.4,2.0,3.0} 共 9 组均**不满足**「平局召回≥0.30 且 RPS≤baseline+0.005」——低 alpha(1.4) 平局召回仅 0.228~0.281（RPS 劣化至 0.213~0.217），高 alpha(2.0/3.0) 平局召回 0.62~0.85 但 Acc 暴跌至 0.373~0.451、RPS 劣化至 0.216~0.224（过拟合平局类）。**根因**：(1) Focal Loss 非凸，难分样本海森为负导致 LightGBM 树分裂失效（已加 `hess=np.maximum(hess,1e-6)` 防护）；(2) 反频率 `class_weights` 本就是平局召回主驱动力（原 focal 分支删除后平局召回 0.39→0.27），Focal 的 `alpha` 与之冗余且引入非凸风险。**决定**：`USE_FOCAL_LOSS` 维持 `False`，baseline 后处理（DrawCalibrator/threshold）已是更优解；A/B 结果落盘 `assets/ab_test_focal_loss_*.json`。
- **P2-14 分层评估**：新建 `scripts/stratified_evaluation.py`，实现 `compute_stratified_report()` / `print_stratified_report()`，对验证集按 6 个维度分层（按联赛 / 赛季阶段 / 时间滚动 / 主客场 / 赔率区间 / 价值投注模拟 ROI），每层输出样本量、Acc、LogLoss、RPS。已在 `train_models.py` 的模型训练后调用，`final_report['stratified_evaluation']` 写入训练报告，并通过 `logger.log_evaluation` 记录整体指标。
- **P2-15 自动化质量监控**：新建 `scripts/quality_gate.py`，实现 `run_quality_gate()` / `print_quality_gate()`，对加载数据做样本量、关键列缺失率、结果分布、联赛覆盖、重复比赛、球队名称异常 6 项结构化检查，输出 pass/warning/fail 三级状态并打印醒目告警。已在 `train_models.py` 数据加载后（`build_all_features` 之前）调用，并通过 `logger.log('QUALITY_GATE', ...)` 落盘训练日志。
- **编译与冒烟验证**：四个脚本 `python -m py_compile` 通过；合成数据冒烟测试确认 `run_quality_gate` 正确输出三级状态、`compute_stratified_report` 正确输出 6 维度指标（含 0 样本层防护）、`focal_loss.py` 梯度/海森数值校验通过。

---

## 五、验收标准

| 阶段 | 准确率 | RPS | 数据量 | 关键交付 |
|------|:--:|:--:|:--:|------|
| 当前 | 48.76% | 0.2095 | 5,252 场 | RPS 已报告（列序bug已修复） |
| P0 完成 | 51-52% | 0.205-0.210 | ≥15,000 场 | RPS 报告 + 统一引擎 + 回溯采集器 + 球员可用性特征 |
| P1 完成 | 53-54% | 0.195-0.200 | — | 贝叶斯模型 + 情境特征 + Stacking + xG 深化 |
| P2 完成 | 54-55% | 0.190-0.195 | — | PostgreSQL + MLflow + Focal Loss + 分层评估 |

| 编号 | 验收标准 |
|:--:|---------|
| P0-1 | matches ≥ 15,000 行，≥ 8 赛季 |
| P0-2 | 训练报告含全局 + 各联赛 RPS |
| P0-3 | 24 维非零率 ≥ 70% |
| P0-4 | wdl_sum=1.0 差异 < 1e-6 |
| P1-5 | 贝叶斯单独 RPS ≤ 0.21 |
| P1-6 | 新增 ≥ 10 维情境特征 |
| P1-7 | Stacking RPS 优于单模型 |
| P2-13 | 已验证·不启用：Focal Loss 无法在不劣化 RPS/Acc 下提升平局召回（A/B 9 组网格均不达标）；baseline `class_weights` argmax 平局召回 0.3788 已达标 |
| P2-11 | 33 表 424 万行迁移 verify 0 差异；批处理型查询提速 9-338x（≥5x 达标），`db_utils.py` 双后端切换可用 |

---

## 六、附录：代码位置索引

### 6.1 精确到函数级别的代码位置

| 文件 | 函数/区域 | 行号 | 关联差距 |
|------|---------|------|---------|
| `scripts/train_models.py` | `main()` — 训练管线入口 | L631-L681 | 全部 |
| `scripts/train_models.py` | `search_best_draw_calibrator()` | L827-L847 | 2.4.2 平局后处理 |
| `scripts/train_models.py` | `search_best_draw_threshold()` | L849-L867 | 2.4.2 平局后处理 |
| `scripts/train_models.py` | 自适应搜索 + 最终推荐 | L869-L930 | 2.4.2 平局后处理 |
| `scripts/train_models.py` | `final_report['models']` 生成 | L983-L1049 | 2.4.1 RPS 缺失 |
| `scripts/train_models.py` | `XGB_OPTUNA_BEST` 硬编码 | L62-L79 | 2.5.1 配置管理 |
| `scripts/train_models.py` | `LGB_OPTUNA_BEST` 硬编码 | L81-L100 | 2.5.1 配置管理 |
| `scripts/advanced_model_trainer.py` | `_evaluate_score_metrics()` — RPS 计算 | L1629-L1653 | 2.4.1 RPS 已实现 |
| `scripts/advanced_model_trainer.py` | xG 特征工程 | L1676-L1689 | 2.2.4 xG 特征 |
| `scripts/prediction_core.py` | `STACKING_WEIGHTS` | L89-L95 | 2.3.2 固定权重 |
| `scripts/prediction_core.py` | `stack_wdl_probabilities()` | L330-L345 | 2.3.2 固定权重 |
| `scripts/prediction_core.py` | T-006 v4 WDL 重加权 | L1418-L1471 | 2.3.3 架构割裂 |
| `scripts/prediction_core.py` | `CalcEngine` 类 | L148-L180 | 2.3.3 计算引擎 |
| `scripts/feature_utils.py` | `build_all_features()` — slim_odds | L1472-L1610 | 2.2.1 赔率特征 |
| `scripts/feature_utils.py` | D-013 时序赔率（slim跳过） | L1550-L1564 | 2.2.1 赔率特征 |
| `scripts/feature_utils.py` | T-003.1 比分赔率（slim跳过） | L1567-L1581 | 2.2.1 赔率特征 |
| `scripts/feature_utils.py` | T-003.2 非线性变换（slim跳过） | L1583-L1597 | 2.2.1 赔率特征 |
| `features/sofascore_pre_match_features.py` | `aggregate_team_history()` | L271-L480 | 2.2.2 球员特征 |
| `features/sofascore_pre_match_features.py` | 门将位置特征（仅G） | L441-L454 | 2.2.2 位置特异性 |
| `features/sofascore_pre_match_features.py` | `_empty_features()` — 升班马 | L483-L495 | 2.2.2 升班马 |
| `features/sofascore_pre_match_features.py` | `build_sofascore_pre_match_features()` — 逐场循环 | L524-L555 | 2.5.1 性能瓶颈 |
| `shared/prediction-engine.js` | `calcWeatherImpact()` — 数据驱动化 | L98-L122 | 2.1.2 ✅ 已修复 |
| `shared/prediction-engine.js` | `calcLambdaMatch()` — matchConditions 集成 | L157-L186 | 2.1.2 ✅ 已修复 |
| `features/match_condition_features.py` | `build_match_conditions()` — 综合入口 | 新建 | ✅ P0-修复 |
| `features/match_condition_features.py` | `calc_injury_factor()` — 伤病因子 | 新建 | ✅ P0-修复 |
| `features/match_condition_features.py` | `calc_weather_factor()` — 天气因子 | 新建 | ✅ P0-修复 |
| `config.yaml` | `match_conditions` 配置节 | L419-L475 | ✅ P0-修复 |
| `scripts/train_models.py` | `compute_rps()` — RPS 计算函数 | L26-L51 | ✅ P0-2 |
| `scripts/train_models.py` | `train_xgboost()` — 输出 RPS | L281-L294 | ✅ P0-2 |
| `scripts/train_models.py` | `train_lightgbm()` — 输出 RPS | L354-L367 | ✅ P0-2 |
| `scripts/train_models.py` | `final_report` — 添加 `val_rps_original` / `rps` | L1019, L1073 | ✅ P0-2 |
| `scripts/stratified_evaluation.py` | `compute_stratified_report()` — 6 维度分层评估 | 新建 | ✅ P2-14 |
| `scripts/stratified_evaluation.py` | `print_stratified_report()` — 分层结果打印 | 新建 | ✅ P2-14 |
| `scripts/quality_gate.py` | `run_quality_gate()` — 数据质量门禁 | 新建 | ✅ P2-15 |
| `scripts/quality_gate.py` | `print_quality_gate()` — 门禁告警打印 | 新建 | ✅ P2-15 |
| `scripts/train_models.py` | `run_quality_gate` + `compute_stratified_report` 接入 | L707-L720, L1127-L1137 | ✅ P2-14/P2-15 |
| `scripts/focal_loss.py` | `focal_grad_hess()` — Focal Loss 梯度/海森（(n,K) 形状） | 新建 | ✅ P2-13（已验证·不启用） |
| `scripts/focal_loss.py` | `focal_obj_xgb` / `focal_fobj_lgb` / `focal_feval_lgb` — XGB/LGB 接口 + 早停指标 | 新建 | ✅ P2-13（已验证·不启用） |
| `scripts/train_models.py` | `USE_FOCAL_LOSS` / `FOCAL_GAMMA` / `FOCAL_ALPHA` 开关 + XGB/LGB 训练分支 | L107-L109, L300-L318, L375-L401 | ✅ P2-13（已验证·不启用） |
| `features/player_availability_features.py` | `compute_team_features()` — 12维PA特征 | 新建 | ✅ P0-3 |
| `features/player_availability_features.py` | `_predict_starting_xi()` — 预计首发 | 新建 | ✅ P0-3 |
| `features/sofascore_pre_match_features.py` | PA特征追加集成 | L564-L584 | ✅ P0-3 |
| `scripts/feature_utils.py` | `pa_` 前缀特征列过滤 | L1627-L1628 | ✅ P0-3 |
| `scripts/unified_prediction_engine.py` | `DixonColesGenerator.generate()` — 统一比分 | 新建 | ✅ P0-4 |
| `scripts/unified_prediction_engine.py` | `LEAGUE_RHO` — 联赛 ρ 参数 | 新建 | ✅ P0-4 |
| `scripts/train_models.py` | `USE_UNIFIED_ENGINE` 开关 | L65-L68 | ✅ P0-4 |
| `config.yaml` | `unified_engine` 配置节 | L477-L496 | ✅ P0-4 |
| `scripts/backfill_collector.py` | `BackfillCollector.run()` — 回溯采集 | 新建 | ✅ P0-1 |
| `shared/prediction-engine.js` | `predictStacked()` — 6模型融合 | L1578-L1650 | 2.3.2 集成方式 |
| `shared/prediction-engine.js` | `applyPlattCalibration()` | L846-L864 | 2.4.1 校准 |
| `modules/common/data_cleaner.py` | 质量监控主逻辑 | L351-L408 | 2.1.3 数据质量 |
| `collection/final_sofascore_collector.py` | `SEASONS` 字典 | L85-L141 | 2.1.1 数据量级 |
| `collection/final_understat_collector.py` | 采集器说明 | L2-L25 | 2.1.2 xG 覆盖率 |
| `scripts/generate_unified_report.py` | 统一报告生成器 | L2-L80 | 2.5.2 工程化 |

### 6.2 框架文档对照表

| 框架文档章节 | 对应系统文件 | 差距等级 |
|------------|------------|:--:|
| 2.1 数据层 | `final_sofascore_collector.py`, `data_cleaner.py`, `match_condition_features.py` ✅ | 🔴→🟢 |
| 2.2 特征工程 | `sofascore_pre_match_features.py`, `feature_utils.py` | 🔴 |
| 2.3 模型架构 | `prediction_core.py`, `prediction-engine.js` | 🟡 |
| 2.4 校准与评估 | `train_models.py`, `advanced_model_trainer.py` | 🟡 |
| 2.5 工程化 | `train_models.py`, `sofascore_pre_match_features.py` | 🟡 |
| 5.1 P0-1 数据扩展 | 新建 `backfill_collector.py` | 🔴 |
| 5.2 P0-2 RPS 评估 | 修改 `train_models.py` | 🟡 |
| 5.3 P0-3 球员可用性 | 新建 `player_availability_features.py` | 🔴 |
| 5.4 P0-4 统一架构 | 新建 `unified_prediction_engine.py` | 🟡 |
| 5.5 P1-5 贝叶斯模型 | 新建 `bayesian_hierarchical_model.py` | ✅ |
| 5.6 P1-6 情境特征 | 新建 `contextual_features.py`（已实现，P1-10 暂不启用） | 🟡 |
| 5.7 P1-7 Stacking | 新增 `train_stacking_meta.py`（OOF + LR meta-learner）；改 `prediction_core.py`（优先 meta-learner、回退固定权重） | ✅ |
| 5.8 P1-8 xG 深化 | 扩展 `sofascore_pre_match_features.py` | 🟡 |

---

> **文档版本**: v2.12
> **生成时间**: 2026-08-28
> **下次更新**: P2 剩余项——P2-12 MLflow（P2-11 PostgreSQL ✅ 2026-08-28 完成）
> **变更记录**: v1.0→v1.1：代码级证据初版；v1.1→v2.0：新增代码证据 12 处、根因分析 15 项、P1/P2 完整技术方案 8 项、精确到函数级别的代码位置索引；v2.0→v2.1：P0-天气/伤病修复；v2.1→v2.2：文档精简 49→21 文件；v2.2→v2.3：P0-2 RPS 集成 + P0-3 球员可用性特征 + P0-4 统一架构 + P0-1 数据扩展；v2.3→v2.4：RPS 列顺序 bug 修复(C-20260823-020) + 修正后验证集 RPS 重算(0.2095/0.2115)；v2.4→v2.5：统一预测引擎接入训练流程方案（见 [unified_engine_integration_plan.md](unified_engine_integration_plan.md)）；v2.5→v2.6：历史数据回溯采集启动 + 采集器防反爬/并发/空壳修复 + 真实数据进度校准；v2.6→v2.7：删除裁判因子（移除 calcRefereeImpact/calc_referee_factor/referee 配置节）；v2.7→v2.10：四大数据源历史回溯采集完成（SofaScore/Understat/500.com/Sporttery）+ P1-5 贝叶斯层级模型 / P1-7 Stacking（LR meta-learner）/ P1-8 xG 深化（暂不采用）/ P1-9 时序赔率+比分赔率接入（ts_odds=True 生产采用）/ P1-10 情境化特征（降维复查无子集，维持不启用）/ P1-11 多公司赔率一致性（启用，198→208 维，降维复查闭环）+ SYSTEM 报告 v3.0 + P2-14 分层评估（6 维度，`stratified_evaluation.py`）/ P2-15 自动化质量监控（pass/warning/fail 三级，`quality_gate.py`）/ P2-13 Focal Loss（`focal_loss.py` 实现+梯度校验通过，A/B 9 组网格实测不达标，`USE_FOCAL_LOSS` 维持不启用；baseline `class_weights` argmax 平局召回 0.3788）；v2.10→v2.11：P2-10 位置-specific 球员特征（`sofascore_pre_match_features.py` 新增 11 维 F/M/D 位置聚合，22→34 维×主客，`ab_test_position_features.py` 真实数据 A/B：blend 平局召回 0.3788→0.3384 劣化 4.04pp、RPS/Accuracy 无增益，维持不启用）；v2.11→v2.12：P2-11 PostgreSQL 迁移完成（`pg_migrate.py` 六阶段幂等迁移，33 表 424 万行 verify 0 差异，三张大表日期分区 + 11 性能索引，批处理型查询 9-338x / 几何平均 2.4x，`db_utils.py` 双后端适配层 + `pg_benchmark.py` 基准测试）

---

## 七、历史数据回溯采集进展（v2.6，2026-08-23）

### 7.1 采集器修复清单

#### 7.1.1 SofaScore 采集器修复 [final_sofascore_collector.py](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/collection/final_sofascore_collector.py)

| 修复项 | 修复前 | 修复后 | 行号 |
|--------|--------|--------|------|
| 赛季 ID 占位符 | 16/17~22/23 七赛季 ID 编造（13000/13300…） | 调用 `/unique-tournament/{id}/seasons` 获取真实 ID（11733/11906/…） | L89-L176 |
| 防反爬降速 | `REQUEST_DELAY=0.6s` + `CONCURRENT_WORKERS=4`（硬编码） | `REQUEST_DELAY=2.0s` → 后加速至 `0.5s` + `CONCURRENT_WORKERS=1` → 后加速至 `4` | L179-L186 |
| 403 中止机制 | 无，403 后继续重试并写空壳 | 新增 `SofaScoreChallengeError` 异常 + 检测到 `challenge/akamai/blocked` 立即中止 + 不写空壳不 mark done | L189-L195, L1640 |
| SQLite 并发写锁 | 无 `busy_timeout` | `PRAGMA busy_timeout=30000ms` + `WAL` + `synchronous=NORMAL` + `connect timeout=30s` | L1858-L1864 |
| 加速配置（多IP场景） | `REQUEST_DELAY=2.0s` + `MAX_RETRIES=3` + `RETRY_BACKOFF=1.5s` + `REQUEST_TIMEOUT=20s` | `REQUEST_DELAY=0.5s` + `MAX_RETRIES=2` + `RETRY_BACKOFF=0.8s` + `REQUEST_TIMEOUT=15s`，单场采集 10s→2-3s（4倍提速） | L179-L183 |

#### 7.1.2 Understat 采集器修复 [final_understat_collector.py](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/collection/final_understat_collector.py)

| 修复项 | 修复前 | 修复后 | 行号 |
|--------|--------|--------|------|
| `'list' object has no attribute 'values'` 崩溃 | `rosters.h` 偶发返回 `[]`（非 dict），调 `.values()` 直接崩，整轮采集中断 | 新增 `_rosters()`/`_shots()` 辅助函数，非 dict 归一为 `{}`，遇到缺阵容/射门数据安静跳过 | L358-L410 |
| 事务持锁做 HTTP I/O | `write_match()` INSERT 持锁 → `write_match_detail()` 打 HTTP（10-15s）→ 另一爬虫 SQLITE_BUSY | 循环顺序颠倒：先 HTTP 拉取 → 集中写库 → commit，持锁时间从秒级降到毫秒级 | L444-L473 |
| SQLite 并发写锁 | 已有 WAL 但无 `busy_timeout` | `PRAGMA busy_timeout=30000ms` + `connect timeout=30s` | L250-L254 |

#### 7.1.3 回溯采集器修复 [backfill_collector.py](file:///F:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/backfill_collector.py)

| 修复项 | 修复前 | 修复后 | 行号 |
|--------|--------|--------|------|
| subprocess 参数不匹配 | 传 `--league`（单数）+ 横杠赛季 `2016-2017` | 改为 `--leagues`（复数）+ 斜杠赛季 `16/17` | L45-L54 |
| 赛季 ID 占位符 | 同 SofaScore，编造的 13000/13300… | 替换为真实 ID，顺带修正 23/24 四个 ID 错误 | L45-L54 |

### 7.2 空壳数据清理记录

| 赛季 | 联赛 | 空壳数 | 根因 | 清理动作 |
|------|------|:--:|------|------|
| 16/17 | 英超 | 9 | 403 后继续写 mapping 但无球员数据，且 mark done | 删 mapping 9 条 + 移除 progress 9 个 event_id |
| 24/25 | 德甲 | 1 | R14 Union Berlin vs Bochum 补赛映射冗余 | 删 mapping 1 条 + 移除 progress 1 个 |
| 24/25 | 法甲 | 1 | R29 Nantes vs PSG 空壳 | 删 mapping 1 条 + 移除 progress 1 个 |

### 7.3 真实数据进度（DB 实查，2026-08-23 23:30）

#### 7.3.1 SofaScore 球员数据覆盖矩阵（match_player_stats）

> **关键口径修正**：法甲自 **2023-24 赛季**起由 20 队缩减为 **18 队**（34 轮 × 9 场 = **306 场/赛季**）；16/17~22/23 为 20 队（38 轮 × 10 场 = 380 场/赛季）。故 23/24 起法甲目标为 306 场，非 380 场。

| 赛季 | 英超 380 | 西甲 380 | 意甲 380 | 德甲 306 | 法甲 380→306 | 合计 | 覆盖率 |
|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 16/17 | 380 ✅ | 381 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1827 | 100% ✅ |
| 17/18 | 380 ✅ | 381 ✅ | 389 ✅ | 306 ✅ | 382 ✅ | 1838 | 100% ✅ |
| 18/19 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 381 ✅ | 1827 | 100% ✅ |
| 19/20 | 381 ✅ | 381 ✅ | 380 ✅ | 308 ✅ | 283 🟡 | 1733 | 94.9%（法甲疫情终止） |
| 20/21 | 383 ✅ | 381 ✅ | 380 ✅ | 307 ✅ | 380 ✅ | 1831 | 100% ✅ |
| 21/22 | 386 ✅ | 380 ✅ | 382 ✅ | 306 ✅ | 384 ✅ | 1838 | 100% ✅ |
| 22/23 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 381 ✅ | 1827 | 100% ✅ |
| 23/24 | 380 ✅ | 381 ✅ | 380 ✅ | 306 ✅ | 308 ✅ | 1755 | 100% ✅ |
| 24/25 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 306 ✅ | 1752 | 100% ✅ |
| 25/26 | 381 ✅ | 382 ✅ | 380 ✅ | 309 ✅ | 306 ✅ | 1758 | 100% ✅ |
| 26/27 | — | — | — | — | — | 52 🟡 | 进行中（随赛程递增） |
| **总计** | — | — | — | — | — | **18038** | 已结束赛季 100% ✅ |

**SofaScore 真实剩余缺口（非进行中赛季）：无（已结束赛季 100% 完整）**
- 16/17~22/23 全部历史赛季已 100% 补齐（16/17、17/18 此前误标为 222 / 0，现已回采至 1827 / 1838）
- 19/20 法甲 283 场为 COVID 疫情提前终止（R29-R38 未踢），非数据缺口
- 23/24~25/26 法甲「缺 72-74 场」为**误判** —— 法甲 23/24 起缩编为 18 队（34 轮 306 场），306-308 场即已完整，非缺口
- 24/25 德甲 R14 此前缺 1 场（source 端该轮仅 8 场），已补齐；现五大联赛已结束赛季（16/17~25/26）100% 完整，无静态缺口

#### 7.3.2 Understat xG 数据覆盖矩阵（understat_match_team_stats）

| 赛季 | 英超 380 | 西甲 380 | 意甲 380 | 德甲 306 | 法甲 380→306 | 合计 | 覆盖率 |
|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 16/17 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1826 | 100% ✅ |
| 17/18 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1826 | 100% ✅ |
| 18/19 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1826 | 100% ✅ |
| 19/20 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 279 🟡 | 1725 | 94.5%（法甲疫情终止） |
| 20/21 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1826 | 100% ✅ |
| 21/22 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1826 | 100% ✅ |
| 22/23 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1826 | 100% ✅ |
| 23/24 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 306 ✅ | 1752 | 100% ✅ |
| 24/25 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 306 ✅ | 1752 | 100% ✅ |
| 25/26 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 306 ✅ | 1752 | 100% ✅ |
| 26/27 | 1 🟡 | 20 🟡 | 0 ⚪ | 0 ⚪ | 1 🟡 | 22 | 进行中 |
| **总计** | — | — | — | — | — | **17959** | 已结束赛季 100% ✅ |

**Understat 已结束赛季（16/17~25/26）100% 完整**，此前「法甲缺 74 场」同样为缩编误判；唯一例外是 19/20 法甲疫情提前终止（279 场）。

#### 7.3.3 500.com 投注分析/百家欧指/技术统计覆盖矩阵（odds500_match，2026-08-27 实测）

> 500.com 是对标 SofaScore/Understat 的**第三类数据源**（投注分析/百家欧指/技术统计），独立于 Sporttery 竞彩赔率，提供必发成交/冷热指数/庄家盈亏 + 57 家欧赔共识 + 危险进攻等特色指标。轮数口径与 SofaScore 一致（英/西/意 38 轮、德甲 34 轮、法甲 16/17~22/23 38 轮→23/24 起 34 轮）。

| 赛季 | 英超 380 | 西甲 380 | 意甲 380 | 德甲 306 | 法甲 380→306 | 合计 | 覆盖率 |
|------|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 16/17 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1,826 | 100% ✅ |
| 17/18 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1,826 | 100% ✅ |
| 18/19 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1,826 | 100% ✅ |
| 19/20 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1,826 | 100% ✅（法甲疫情含 101 未赛） |
| 20/21 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1,826 | 100% ✅ |
| 21/22 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1,826 | 100% ✅ |
| 22/23 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 380 ✅ | 1,826 | 100% ✅ |
| 23/24 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 306 ✅ | 1,752 | 100% ✅ |
| 24/25 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 306 ✅ | 1,752 | 100% ✅ |
| 25/26 | 380 ✅ | 380 ✅ | 380 ✅ | 306 ✅ | 306 ✅ | 1,752 | 100% ✅ |
| 26/27 | 380 🟡 | 0 ⚪ | 0 ⚪ | 0 ⚪ | 0 ⚪ | 380 | 进行中（10 已赛/370 未赛） |
| **总计** | — | — | — | — | — | **18,418** | 已结束赛季 10/10 完成 ✅ |

**500.com 覆盖状态（对标 SofaScore/Understat 10 季）**：✅ 已全部完成（16/17~25/26 已结束赛季 100% 完整，无剩余缺口）
- 阶段一（21/22~25/26）：✅ 已全部完成（共 8,908 场已结束赛季）
- 阶段二（16/17~20/21 扩展）：✅ 已全部完成（16/17 法甲 380 场已补齐；此前系后台采集中途漏采）
- 采集器 `final_500_collector.py` 已支持 `--season 16/17~25/26 --league all`、`--skip-existing`（默认开启，断点续采跳过已采场次）、`Client.get()` 瞬时断连重试 3 次；`SEASONS` 字典 10 季 stid 已就绪，`_EXTRA_CN_TO_EN` 补 31 个历史升降班马映射

#### 7.3.4 Sporttery 竞彩时序赔率覆盖矩阵（wdl/handicap/total_goals/score_history，2026-08-27 实测）

> **口径说明（与 §7.3.1~7.3.3 不同）**：SofaScore/Understat/500.com 是**完整赛程**口径，每联赛每赛季目标场次固定（380/306），故可计算覆盖率%。而 Sporttery 竞彩时序赔率是**开售场次**口径——竞彩官方只对部分场次开售，开售总量随赛季/联赛波动（疫情期 19/20 仅 835 场、正常期约 1200~1800 场），**无固定满额目标**，因此无法套用「覆盖率%」矩阵，只能用「已采集开售场次」原始计数。

| 赛季 | 让球 handicap | 胜平负 wdl | 总进球 total | 比分 score | 状态 |
|------|:--:|:--:|:--:|:--:|------|
| 16/17 | 1787 | 1710 | 1787 | 1787 | ✅ 完成 |
| 17/18 | 1799 | 1707 | 1799 | 1799 | ✅ 完成 |
| 18/19 | 1551 | 1477 | 1551 | 1551 | ✅ 完成 |
| 19/20 | 835 | 797 | 835 | 835 | ✅ 完成（疫情+开售缩减） |
| 20/21 | 1208 | 1166 | 1208 | 1208 | ✅ 完成（法甲 10 月已补采 8 场） |
| 21/22 | 930 | 859 | 930 | 930 | ✅ 完成（英超 11 月时序赔率已补采 12 场） |
| 22/23 | 1001 | 952 | 1001 | 1001 | ✅ 完成（英超308/意甲201/法甲163/西甲177/德甲152；2022 世界杯 12 月停摆正常，德甲 1 月=2 为真实开售量） |
| 23/24 | 1231 | 1178 | 1231 | 1231 | ✅ 完成 |
| 24/25 | 1454 | 1412 | 1454 | 1454 | ✅ 完成 |
| 25/26 | 1329 | 1317 | 1330 | 1326 | ✅ 完成 |
| 26/27 | 0 | 0 | 0 | 0 | ⚪ 进行中（赛程已入 49 场，竞彩未开售） |
| **合计** | **13,125** | **12,575** | **13,126** | **13,122** | 已结束赛季（16/17~25/26；不含进行中 26/27） |

**Sporttery 时序赔率关键点**：
- 时序赔率是模型 **165 维特征的核心输入**（handicap/wdl 开售赔率为核心，D-013 时序赔率在 slim_odds 模式已跳过）。其「开售口径」决定它无法与 §7.3.1 的 380/306 满额矩阵直接对标，正确评估维度是「已采集开售场次」而非「覆盖率%」。
- 16/17~25/26 共 10 季主线已入库；进行中仅 26/27（竞彩未开售）。
- **完整度核查（按月×联赛扫描）**：16/17~18/19 各联赛 10 月份全覆盖 ✅；19/20 的 2~4 月停摆为疫情正常（非漏采）✅；20/21 法甲 10 月漏采已补 8 场 ✅；21/22 英超 11 月时序赔率已补采 12 场 ✅；22/23 德甲/意甲 12 月=0 为 2022 世界杯停摆正常（非漏采），德甲 1 月=2 为真实开售量（世界杯间歇尾声）✅。
- 两处历史漏采已补采完毕：20/21 法甲 10 月补 8 场（1200→1208）、21/22 英超 11 月补 12 场时序赔率，均 0 失败。
- 22/23 续采完成：全季 1,001 场（handicap/total/score 各 1,001，wdl 952），按月×联赛终检通过、无漏采；竞彩时序赔率 16/17~25/26 已结束赛季全部完成。

### 7.4 关键发现与纠错记录

1. **多次进度表误判纠错**：v2.5 之前的多张进度矩阵存在系统性误判（把已采赛季标为全 0），根因是查询条件用了错误的 season 过滤或读输出时被截断。v2.6 全部改为 DB 实查校准，数据可信度 100%。

2. **Understat 21/22~22/23 实际已 100%**：之前误标为"全 0 待采"，实查 DB 三表（TS/PL/SH）均为 1826/1826 完整。Understat resume 逻辑是全局查 `understat_shots` 所有 match_id（不分赛季），`已采集 12616 场` 是全局数，不影响正确性。

3. **法甲「缺 74 场」是缩编误判（重大纠错）**：法甲自 2023-24 赛季由 20 队缩减为 18 队，轮次从 38 轮降为 34 轮（每轮 9 场 = 306 场/赛季）。此前用旧口径「法甲 380 场」作目标，把 23/24~25/26 法甲的 306-308 场误判成「缺 72-74 场」「源数据 R35-R38 缺」。实为法甲 34 轮后本就没有 R35-R38，SofaScore 与 Understat 端法甲数据均已 100% 完整。

4. **26/27 进行中赛季的空壳是正常的**：未开赛比赛只写 mapping 无球员数据，等比赛踢完 `--resume` 补采即可。

5. **SofaScore 16/17~22/23 全部历史赛季已 100% 补齐**（2026-08-24 实查）：此前矩阵对多个赛季误标——16/17（标 222）、17/18（标 0）、18/19（标 0）、20/21（标 0）、21/22（标 0）、22/23（标 1252）。实查 `match_player_stats` 均已回采至 1827~1838 场（均 ≥1826 目标，超出的为补赛冗余）。

6. **SofaScore/Understat 历史数据已全部完整**（2026-08-27 最终校验）：16/17~25/26 全部已结束赛季已 100% 完整（24/25 德甲 R14 缺 1 场已于 2026-08-27 补齐）。仅剩 26/27 进行中赛季。法甲缩编误判澄清后，此前所有「法甲缺 74 场」的补采任务均已取消。

### 7.5 下一步执行计划

| 优先级 | 任务 | 缺口量 | 命令 |
|:--:|------|:--:|------|
| **P3** | 26/27 进行中赛季续采 | 随赛程推进 | `python collection/final_sofascore_collector.py --leagues all --season 26/27 --resume` |

> 注：16/17~25/26 全部已结束赛季（SofaScore + Understat + 500.com + Sporttery 竞彩时序赔率）已 100% 完整（500.com 16/17 法甲 380 场、SofaScore 24/25 德甲 R14 缺 1 场均已于 2026-08-27 补齐）。法甲缩编误判澄清后，「法甲补 74×3」「SofaScore 法甲补齐」等历史缺口任务已全部取消。Sporttery 时序赔率 20/21 法甲 10 月、21/22 英超 11 月两处漏采已补采完毕，22/23 续采已完成；仅剩 26/27 进行中赛季（竞彩未开售，随赛程推进）。

**结论**：SofaScore 18,038 场（含 26/27 进行中 52 场）、Understat 17,959 场（含 26/27 进行中 22 场），已结束赛季完整度 100%，已达框架要求的 15,000-25,000 场基准。

---

### 7.6 changelog 摘要（v2.6 → v2.10）

| 日期 | 变更 |
|------|------|
| 2026-08-24 | 修正 2.4.1 val_acc 数值（XGB 0.4838→0.5057、LGB 0.4848→0.4914，取 val_accuracy_original 口径） |
| 2026-08-24 | 系统核查：发现 `matches` 表裁判因子字段引用与实库不符（`referee`/`home_yellow_cards`/`home_red_cards`/`home_goals`/`away_goals` 均不存在），已在 2.4 修复记录与数据来源处标注警示；其余表/列引用均一致 |
| 2026-08-25 | 删除裁判因子：移除 `prediction-engine.js` `calcRefereeImpact`、`match_condition_features.py` `calc_referee_factor`、`config.yaml` `referee` 配置节及 `backfill_matches_referee_fields.py`，同步清除文档中裁判因子相关表述 |
| 2026-08-25 | 移除球天下(qtx)数据源：删除 collection/qtx 采集脚本、data/match_codes.txt、logs/qtx_*.log 及 DROP 空表 qtx_match_distribution，同步删除文档 7.6 章节 |
| 2026-08-27 | Sporttery 竞彩时序赔率历史回采：17/18（1,800 场）、18/19（1,551 场）入库；时序赔率覆盖 16/17~18/19 + 23/24~25/26 共 6 季，剩余 19/20~22/23 共 4 季待补采 |
| 2026-08-27 | Sporttery 竞彩时序赔率历史回采：19/20（835 场）入库；时序赔率覆盖 16/17~19/20 + 23/24~25/26 共 7 季，剩余 20/21~22/23 共 3 季待补采 |
| 2026-08-27 | 新增 §7.3.4 Sporttery 竞彩时序赔率覆盖矩阵（开售口径，实时序表 handicap/wdl/total/score 场次）；20/21、21/22 已入库（各漏采少量场次），22/23 进行中；补采任务并入 §7.5 |
| 2026-08-27 | Sporttery 补采：20/21 法甲 10 月补 8 场（1200→1208）、21/22 英超 11 月补 12 场时序赔率（均 0 失败）；修正 §7.3.4 合计 wdl 列算术误差（11,779→11,623）；§7.5 移除两处已完成补采任务 |
| 2026-08-27 | Sporttery 22/23 续采完成（1,001 场：handicap/total/score 各 1,001、wdl 952）；按月×联赛终检通过（德甲/意甲 12 月=0 为世界杯停摆，德甲 1 月=2 为真实开售量）；§7.3.4 22/23→✅、合计重算 13,125/12,575/13,126/13,122、§7.5 移除续采任务 |
| 2026-08-27 | 500.com 16/17 法甲 380 场补齐 + SofaScore 24/25 德甲 R14 缺 1 场补齐 → §7.3.3 总计 18,038→18,418（16/17 法甲→380✅、已结束赛季 10/10 完整）、§7.3.1 总计 18,033→18,038（24/25 德甲→306、已结束赛季 100%）、头部摘要与 §7.4/§7.5 同步 |
| 2026-09-03 | EV 期望值引擎落地：`ev_engine.py` 纯函数决策引擎（edge= p_model−p_market、EV、remove_vig、Kelly、VALUE/MARGINAL/AVOID，17 单测通过）+ `ev_backtest.py` 三源对齐回测 → §2.7 由「完全缺失」更新为「已实现」 |
| 2026-09-03 | 严格时序 OOF 回测揭示**无正 edge**：`generate_oof_predictions.py`（208 维 + TimeSeriesSplit(5)，11965 场 OOF，blend acc 52.87%/LogLoss 0.99）；旧 215 维 ROI +17~22% 判定为时序泄漏伪象，严格 OOF 后 mean 平注 ROI -5.41%/平均 EV +21.12%、edge 分桶非单调 |
| 2026-09-03 | 赔率覆盖率修复：`ev_backtest.py` 新增中文 match_id 队名桥接（`build_team_bridge`），`matches.match_id` 2016~2023 中文赛季对齐英文赔率键，覆盖率 44%→96.4%（5252→11531 场），结论维持无正 edge |
| 2026-09-04 | EV 择场阈值抬升扫描（`ev_threshold_sweep.py`，C-20260904-001）：72 组合（3 方案 × 6 min_ev × 4 置信）全量无转正；最优 Temp+0.10+P50 仍 -2.93%；min_ev/置信抬升均使 ROI 恶化（edge 排序失效 + 高置信被高赔率爆冷绑架）→ 纯后处理择场证伪，ROI 转正须进训练端（CALIB-008） |
| 2026-09-04 | edge 分桶单调回归修复（`edge_monotonic_fix.py`，C-20260904-002）：单一单调保序（Mono-Pooled on Temp）使分桶恢复单调递增（0~3pp -13.4%→3~6pp -4.9%→6~10pp -3.1%→>10pp -1.5%）但**全桶仍负**；选择条件化 winner's curse 修正（Mono-Selected）证伪（-6.28% 更差）；整体最优 Mono-Pooled(on raw) -3.65%；Mono-OVR 平召 0.92% 证实逐类保序坍缩 → 保序仅「排序修复」不产生正 edge，系统性高估根治须训练端 EV/ROI 目标改造（CALIB-009） |
