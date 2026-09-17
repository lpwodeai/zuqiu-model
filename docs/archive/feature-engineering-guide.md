# 高级特征工程实施指南

> **文档版本**: v2.0
> **生成时间**: 2026-07-23
> **最后更新**: 2026-08-12
> **历史版本**: v1.0（pre-ML JS时代，153→226维，已归档为参考）
> **适用范围**: 五大联赛足球预测模型 ML 时代（2026-08-05 阶段四完成后）

## 文档演进说明

本指南 v1.0 版描述 pre-ML 时代的 JS 特征体系（153→226维，OddsAnalyzer专家系统），已归档为历史参考。v2.0 版新增 **ML 时代特征体系**（第十章），描述 Python 特征管线的实际架构：60维核心 → 114维扩展 → 71维T-005对手Lag。原 v1.0 内容（第一至九章）保留为 pre-ML 时代参考，不再作为当前实施依据。

---

## 一、特征扩展概述（v1.0 pre-ML 时代，归档参考）

本指南详细阐述了将赔率数据回测系统的特征体系从153个维度系统性扩展至226个维度的完整方案。通过引入交互特征、非线性转换特征和领域特定特征，显著增强模型的表达能力和预测准确性。

### 特征扩展效果

| 指标 | 扩展前 | 扩展后 | 提升幅度 |
|------|--------|--------|----------|
| 特征总数 | 153 | 226 | +73 (+47.7%) |
| 模型准确率 | 75.9% | 78.5% | +2.6% |
| AUC | 0.78 | 0.81 | +3.8% |
| F1分数 | 0.74 | 0.77 | +4.1% |

> ⚠️ **注意**: 以上指标为 pre-ML JS 时代数据，基于54场样本循环调优，存在严重过拟合。ML 时代实际性能见第十章。

---

## 二、特征类别详细说明

### 2.1 基础统计特征（51个）

**定义**：对赔率历史数据进行基本统计描述，提供数据的整体分布特征。

**新增特征**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_odds_range | max(winA, draw, winB) - min(winA, draw, winB) | WDL赔率 | 中 |
| wdl_odds_std | 三项赔率的标准差 | WDL赔率 | 中 |
| hcp_odds_range | max(hcp_win, hcp_draw, hcp_lose) - min(...) | 让球赔率 | 中 |
| tg_std | 总进球概率分布的标准差 | 总进球赔率 | 中 |

**原有特征**（47个）：均值、最大值、最小值、总和、标准差、变异系数、极差、IQR、滚动标准差等。

**数据来源**：wdlHistory、handicapHistory、totalGoalsHistory

**特征重要性**：15.0%

---

### 2.2 时间序列特征（78个）

**定义**：捕捉赔率随时间变化的动态特征，反映市场资金流向和信息变化。

**新增特征**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_total_abs_change | 所有赔率变化绝对值之和 | WDL变化序列 | 高 |
| wdl_avg_abs_change | 平均绝对值变化 | WDL变化序列 | 高 |
| wdl_recent_momentum | 最近一次变化的百分比之和 | WDL变化序列 | 高 |
| wdl_weighted_trend | 加权趋势分数（近期权重更高） | WDL变化序列 | 中 |

**原有特征**（74个）：平均变化、最大变化、最小变化、总变化、平均百分比变化、波动性、隐含概率变化等。

**数据来源**：wdlHistory、handicapHistory、totalGoalsHistory

**特征重要性**：25.0%（最高）

---

### 2.3 隐含概率特征（8个）

**定义**：将赔率转换为概率表示，消除赔率尺度差异，便于跨比赛比较。

**特征列表**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| implied_winA_last | 最后时刻主队获胜隐含概率 | WDL赔率 | 最高 |
| implied_draw_last | 最后时刻平局隐含概率 | WDL赔率 | 高 |
| implied_winB_last | 最后时刻客队获胜隐含概率 | WDL赔率 | 最高 |
| implied_overround_last | 最后时刻市场效率因子 | WDL赔率 | 中 |
| implied_winA_change | 主队概率变化量 | WDL赔率 | 高 |
| implied_draw_change | 平局概率变化量 | WDL赔率 | 中 |
| implied_winB_change | 客队概率变化量 | WDL赔率 | 高 |
| implied_overround_change | 市场效率变化量 | WDL赔率 | 低 |

**计算公式**：
```
implied_prob = (1 / odds) / sum(1/odds for all outcomes)
overround = sum(1/odds for all outcomes)
```

**数据来源**：wdlHistory

**特征重要性**：12.0%

---

### 2.4 凯利特征（5个）

**定义**：基于凯利公式计算最优投注比例，识别市场低效机会。

**特征列表**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| kelly_winA | max(0, (winA / fairWinA) - 1) | WDL赔率 | 中 |
| kelly_draw | max(0, (draw / fairDraw) - 1) | WDL赔率 | 中 |
| kelly_winB | max(0, (winB / fairWinB) - 1) | WDL赔率 | 中 |
| kelly_max | max(kelly_winA, kelly_draw, kelly_winB) | WDL赔率 | 高 |
| kelly_best_option | 凯利值最大的选项(编码) | WDL赔率 | 低 |

**计算公式**：
```
fair_odds = 1 / implied_probability
kelly = max(0, (actual_odds / fair_odds) - 1)
```

**数据来源**：wdlHistory

**特征重要性**：5.0%

---

### 2.5 跨市场特征（3个）

**定义**：校验不同博彩玩法之间的赔率一致性，发现市场异常。

**特征列表**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_hcp_consistency | WDL与让球赔率隐含概率的一致性得分 | WDL+让球赔率 | 高 |
| tg_score_consistency | 总进球与比分赔率的KL散度一致性 | 总进球+比分赔率 | 中 |
| wdl_over_under_bias | WDL强队倾向与大小球倾向的偏差 | WDL+总进球赔率 | 中 |

**计算公式**：
```
wdl_hcp_consistency = 1 - (|wdl_implied - hcp_implied| / 3)
tg_score_consistency = 1 - KL_divergence(tg_dist, score_dist)
```

**数据来源**：wdlHistory、handicapHistory、totalGoalsHistory、scoreHistory

**特征重要性**：8.0%

---

### 2.6 时间维度特征（4个）

**定义**：捕捉时间因素对赔率的影响，反映信息时效性。

**特征列表**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| hours_until_match | 首次赔率记录距开赛时间(小时) | 时间戳 | 中 |
| history_duration_hours | 赔率记录持续时长(小时) | 时间戳 | 低 |
| last_update_hours_before | 最后更新距开赛时间(小时) | 时间戳 | 中 |
| update_frequency | 每小时更新次数 | 时间戳 | 中 |

**数据来源**：matchDate、wdlHistory.timestamp

**特征重要性**：6.0%

---

### 2.7 波动性特征（14个）

**定义**：衡量赔率变化的剧烈程度，反映市场不确定性。

**特征列表**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_winA_volatility | 主队赔率百分比变化的标准差 | WDL变化序列 | 高 |
| wdl_draw_volatility | 平局赔率百分比变化的标准差 | WDL变化序列 | 中 |
| wdl_winB_volatility | 客队赔率百分比变化的标准差 | WDL变化序列 | 高 |
| hcp_hcp_win_volatility | 让胜赔率百分比变化的标准差 | 让球变化序列 | 中 |
| hcp_hcp_draw_volatility | 让平赔率百分比变化的标准差 | 让球变化序列 | 中 |
| hcp_hcp_lose_volatility | 让负赔率百分比变化的标准差 | 让球变化序列 | 中 |
| tg_0~7+_volatility | 各总进球数赔率百分比变化的标准差 | 总进球变化序列 | 低-中 |

**数据来源**：wdlHistory、handicapHistory、totalGoalsHistory

**特征重要性**：7.0%

---

### 2.8 交互特征（9个）【新增】

**定义**：特征之间的乘积或组合，捕捉非线性交互关系。

**特征列表**：

| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_winA_draw_interaction | implied_winA * implied_draw | WDL隐含概率 | 中 |
| wdl_winA_winB_interaction | implied_winA * implied_winB | WDL隐含概率 | 中 |
| wdl_draw_winB_interaction | implied_draw * implied_winB | WDL隐含概率 | 中 |
| wdl_change_interaction | winA变化 * draw变化 | WDL变化序列 | 低 |
| wdl_pct_change_interaction | winA%变化 * draw%变化 | WDL变化序列 | 低 |
| wdl_hcp_implied_interaction | wdl_implied_winA * hcp_implied_winA | WDL+让球隐含概率 | 中 |
| wdl_hcp_overround_interaction | wdl_overround * hcp_overround | WDL+让球overround | 低 |
| wdl_tg_interaction | wdl_implied_winA * over2.5_prob | WDL+总进球概率 | 中 |
| wdl_draw_tg_interaction | wdl_implied_draw * over2.5_prob | WDL+总进球概率 | 低 |

**数据来源**：wdlHistory、handicapHistory、totalGoalsHistory

**特征重要性**：6.0%

---

### 2.9 非线性转换特征（29个）【新增】

**定义**：通过数学变换增强特征的表达能力，捕捉非线性关系。

**特征列表**：

#### 对数转换（9个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_winA_log | log(winA) | WDL赔率 | 高 |
| wdl_draw_log | log(draw) | WDL赔率 | 高 |
| wdl_winB_log | log(winB) | WDL赔率 | 高 |
| wdl_implied_winA_log | log(implied_winA + 0.001) | WDL隐含概率 | 中 |
| wdl_implied_draw_log | log(implied_draw + 0.001) | WDL隐含概率 | 中 |
| wdl_implied_winB_log | log(implied_winB + 0.001) | WDL隐含概率 | 中 |
| hcp_win_log | log(hcp_win) | 让球赔率 | 中 |
| hcp_draw_log | log(hcp_draw) | 让球赔率 | 中 |
| hcp_lose_log | log(hcp_lose) | 让球赔率 | 中 |

#### 平方根转换（6个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_winA_sqrt | sqrt(winA) | WDL赔率 | 中 |
| wdl_draw_sqrt | sqrt(draw) | WDL赔率 | 中 |
| wdl_winB_sqrt | sqrt(winB) | WDL赔率 | 中 |
| hcp_win_sqrt | sqrt(hcp_win) | 让球赔率 | 低 |
| hcp_draw_sqrt | sqrt(hcp_draw) | 让球赔率 | 低 |
| hcp_lose_sqrt | sqrt(hcp_lose) | 让球赔率 | 低 |

#### 平方/立方转换（5个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_winA_squared | winA^2 | WDL赔率 | 中 |
| wdl_draw_squared | draw^2 | WDL赔率 | 中 |
| wdl_winB_squared | winB^2 | WDL赔率 | 中 |
| tg_mean_squared | tg_mean^2 | 总进球分布 | 中 |
| tg_mean_cubed | tg_mean^3 | 总进球分布 | 低 |

#### 熵和基尼系数（3个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_implied_entropy | -sum(p * log(p)) | WDL隐含概率 | 高 |
| wdl_implied_gini | 1 - sum(p^2) | WDL隐含概率 | 中 |
| tg_entropy | -sum(p * log(p)) | 总进球分布 | 高 |

#### Overround变换（2个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| wdl_overround_squared | overround^2 | WDL隐含概率 | 低 |
| wdl_overround_log | log(overround) | WDL隐含概率 | 中 |

#### 其他（4个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| tg_mean_log | log(tg_mean + 1) | 总进球分布 | 低 |
| winA_late_to_early_ratio_squared | (late_winA/early_winA)^2 | WDL赔率 | 低 |
| winB_late_to_early_ratio_squared | (late_winB/early_winB)^2 | WDL赔率 | 低 |
| score_entropy | -sum(p * log(p)) | 比分赔率 | 中 |

**数据来源**：wdlHistory、handicapHistory、totalGoalsHistory、scoreHistory

**特征重要性**：8.0%

---

### 2.10 领域特定特征（23个）【新增】

**定义**：针对足球博彩领域设计的专业指标，提供领域知识支持。

**特征列表**：

#### WDL领域指标（10个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| favorite_strength_index | favorite_prob - underdog_prob | WDL隐含概率 | 最高 |
| draw_compression_ratio | (winA + winB) / (2 * draw) | WDL赔率 | 最高 |
| market_efficiency_score | 1 - (overround - 1) | WDL隐含概率 | 中 |
| wdl_liquidity_score | 1 / overround | WDL隐含概率 | 低 |
| wdl_odds_range | max - min | WDL赔率 | 中 |
| wdl_odds_spread | winA - winB | WDL赔率 | 中 |
| wdl_odds_sum | winA + draw + winB | WDL赔率 | 低 |
| wdl_odds_mean | (winA + draw + winB) / 3 | WDL赔率 | 低 |
| winA_late_to_early_ratio_squared | (late/early)^2 | WDL赔率 | 低 |
| winB_late_to_early_ratio_squared | (late/early)^2 | WDL赔率 | 低 |

#### 让球领域指标（3个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| hcp_overround | sum(1/odds) | 让球赔率 | 中 |
| hcp_odds_range | max - min | 让球赔率 | 低 |
| hcp_odds_spread | hcp_win - hcp_lose | 让球赔率 | 中 |

#### 总进球领域指标（10个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| tg_variance | sum((goals - mean)^2 * prob) | 总进球分布 | 中 |
| tg_std | sqrt(variance) | 总进球分布 | 中 |
| tg_over_under_ratio | over_odds / under_odds | 总进球赔率 | 高 |
| tg_over_under_spread | over_odds - under_odds | 总进球赔率 | 中 |
| tg_most_likely_goals | 概率最大的进球数 | 总进球分布 | 中 |
| tg_second_most_likely | 概率第二大的进球数 | 总进球分布 | 低 |
| tg_mode_confidence | max_prob - second_max_prob | 总进球分布 | 中 |
| tg_skewness | E[(X-μ)^3] / σ^3 | 总进球分布 | 低 |
| tg_kurtosis | E[(X-μ)^4] / σ^4 | 总进球分布 | 低 |

#### 比分领域指标（7个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| score_mode_prob | 最可能比分的概率 | 比分赔率 | 中 |
| score_mode_home | 最可能比分的主队进球 | 比分赔率 | 低 |
| score_mode_away | 最可能比分的客队进球 | 比分赔率 | 低 |
| score_market_overround | sum(1/odds) | 比分赔率 | 低 |
| score_entropy | -sum(p * log(p)) | 比分赔率 | 中 |
| score_high_prob | 总进球≥3的概率 | 比分赔率 | 中 |
| score_draw_prob | 平局比分的概率 | 比分赔率 | 中 |
| score_home_win_prob | 主队获胜比分的概率 | 比分赔率 | 中 |
| score_away_win_prob | 客队获胜比分的概率 | 比分赔率 | 中 |

#### 比赛类型指标（2个）
| 特征名称 | 计算方法 | 数据来源 | 重要性 |
|----------|----------|----------|--------|
| match_type_group | 小组赛=1, 淘汰赛=0 | matchType | 低 |
| match_type_knockout | 淘汰赛=1, 小组赛=0 | matchType | 低 |

**数据来源**：wdlHistory、handicapHistory、totalGoalsHistory、scoreHistory、matchType

**特征重要性**：8.0%

---

## 三、特征重要性评估

### 3.1 类别重要性排序

| 排名 | 特征类别 | 重要性权重 | 说明 |
|------|----------|------------|------|
| 1 | 时间序列特征 | 25.0% | 捕捉市场动态变化 |
| 2 | 基础统计特征 | 15.0% | 提供基础信息 |
| 3 | 隐含概率特征 | 12.0% | 反映市场预期 |
| 4 | 非线性转换特征 | 8.0% | 增强模型表达能力 |
| 4 | 领域特定特征 | 8.0% | 提供专业信息 |
| 6 | 跨市场特征 | 8.0% | 发现市场异常 |
| 7 | 波动性特征 | 7.0% | 衡量市场不确定性 |
| 8 | 时间维度特征 | 6.0% | 反映信息时效性 |
| 8 | 交互特征 | 6.0% | 捕捉非线性关系 |
| 10 | 凯利特征 | 5.0% | 识别市场低效机会 |

### 3.2 Top 20 重要特征

| 排名 | 特征名称 | 重要性 | 类别 |
|------|----------|--------|------|
| 1 | implied_winA_last | 3.5% | 隐含概率 |
| 2 | implied_winB_last | 3.2% | 隐含概率 |
| 3 | wdl_winA_avg_pct_change | 2.8% | 时间序列 |
| 4 | wdl_winB_avg_pct_change | 2.5% | 时间序列 |
| 5 | favorite_strength_index | 2.2% | 领域特定 |
| 6 | draw_compression_ratio | 2.0% | 领域特定 |
| 7 | wdl_implied_entropy | 1.8% | 非线性 |
| 8 | tg_entropy | 1.5% | 非线性 |
| 9 | wdl_hcp_consistency | 1.4% | 跨市场 |
| 10 | wdl_winA_std | 1.2% | 基础统计 |
| 11 | wdl_winB_std | 1.1% | 基础统计 |
| 12 | wdl_recent_momentum | 1.0% | 时间序列 |
| 13 | wdl_weighted_trend | 0.9% | 时间序列 |
| 14 | kelly_max | 0.8% | 凯利 |
| 15 | tg_mean_squared | 0.7% | 非线性 |
| 16 | hours_until_match | 0.6% | 时间维度 |
| 17 | update_frequency | 0.5% | 时间维度 |
| 18 | wdl_winA_draw_interaction | 0.5% | 交互 |
| 19 | tg_mode_confidence | 0.4% | 领域特定 |
| 20 | score_entropy | 0.4% | 非线性 |

---

## 四、特征扩展维度对比

### 4.1 维度变化汇总

| 特征类别 | 扩展前 | 扩展后 | 新增 | 增长率 |
|----------|--------|--------|------|--------|
| 基础统计特征 | 47 | 51 | +4 | 9% |
| 时间序列特征 | 74 | 78 | +4 | 5% |
| 隐含概率特征 | 8 | 8 | +0 | 0% |
| 凯利特征 | 5 | 5 | +0 | 0% |
| 跨市场特征 | 3 | 3 | +0 | 0% |
| 时间维度特征 | 4 | 4 | +0 | 0% |
| 波动性特征 | 14 | 14 | +0 | 0% |
| 交互特征 | 0 | 9 | +9 | 新增类别 |
| 非线性转换特征 | 0 | 29 | +29 | 新增类别 |
| 领域特定特征 | 0 | 23 | +23 | 新增类别 |
| **总计** | **155** | **224** | **+69** | **+44.5%** |

### 4.2 扩展前后性能对比

| 特征集 | 准确率 | AUC | F1分数 |
|--------|--------|-----|--------|
| 原始特征(153个) | 75.9% | 0.78 | 0.74 |
| 扩展特征(226个) | 78.5% | 0.81 | 0.77 |
| 提升幅度 | +2.6% | +3.8% | +4.1% |

### 4.3 各预测任务性能提升

| 预测任务 | 扩展前准确率 | 扩展后准确率 | 提升幅度 |
|----------|--------------|--------------|----------|
| 让球胜平负 | 75.9% | 78.8% | +2.9% |
| 总进球数 | 79.6% | 82.1% | +2.5% |
| 比分候选 | 68.5% | 71.2% | +2.7% |
| 最终比分 | 42.6% | 45.3% | +2.7% |

---

## 五、特征工程最佳实践

### 5.1 特征选择策略

1. **过滤法**：使用相关性分析去除高度相关的特征（相关系数 > 0.9）
2. **包装法**：使用递归特征消除(RFE)选择最优特征子集
3. **嵌入法**：利用模型自带的特征重要性进行选择
4. **领域知识**：保留领域特定特征，即使重要性评分较低

### 5.2 特征预处理

1. **标准化**：对数值特征进行Z-score标准化
2. **缺失值处理**：使用均值/中位数填充或标记为特殊值
3. **异常值处理**：使用IQR方法识别并处理异常值
4. **类别编码**：对类别特征进行独热编码或标签编码

### 5.3 特征监控

1. **数据漂移检测**：定期检测特征分布变化
2. **特征重要性监控**：跟踪特征重要性的变化趋势
3. **特征有效性评估**：定期评估特征对模型性能的贡献

---

## 六、代码实现

### 6.1 特征提取器位置

[feature-extractor.js](file:///G:/zuqiu/世界杯2026/world-cup-predictor-v3.7/assets/modules/features/feature-extractor.js)

### 6.2 核心方法

```javascript
// 提取所有特征
const features = extractor.extractAllFeatures(matchData);

// 分类提取
const sentiment = extractor.extractMarketSentimentFeatures(matchData);
const volatility = extractor.extractVolatilityFeatures(matchData);
const crossMarket = extractor.extractCrossMarketFeatures(matchData);
const timeBased = extractor.extractTimeBasedFeatures(matchData);
const interaction = extractor.extractInteractionFeatures(matchData);      // 新增
const nonlinear = extractor.extractNonlinearFeatures(matchData);          // 新增
const domainSpecific = extractor.extractDomainSpecificFeatures(matchData); // 新增
```

### 6.3 特征评估脚本

[evaluate-feature-importance.js](file:///G:/zuqiu/世界杯2026/world-cup-predictor-v3.7/evaluate-feature-importance.js)

---

## 七、未来优化方向

1. **深度学习特征**：使用AutoEncoder自动学习特征表示
2. **时序深度学习**：使用LSTM/Transformer处理时间序列数据
3. **上下文特征**：引入球队历史战绩、球员状态等外部数据
4. **实时特征**：添加实时赔率变化率、成交量等实时特征
5. **对抗特征**：设计对抗噪声的鲁棒特征

---

## 八、附录

### 8.1 特征命名规范

| 前缀 | 含义 | 示例 |
|------|------|------|
| wdl_ | 胜平负相关 | wdl_winA_avg_change |
| hcp_ | 让球相关 | hcp_hcp_win_std |
| tg_ | 总进球相关 | tg_entropy |
| score_ | 比分相关 | score_mode_prob |
| implied_ | 隐含概率相关 | implied_winA_last |
| kelly_ | 凯利相关 | kelly_max |

### 8.2 特征重要性评估方法

本指南中的特征重要性基于以下方法综合评估：
1. **理论分析**：基于领域知识的重要性判断
2. **方差分析**：特征方差对目标变量的解释程度
3. **模型重要性**：GradientBoostingClassifier的特征重要性得分
4. **实践验证**：实际回测中的特征贡献度

---

## 十、ML 时代特征体系（v2.0 新增，当前实施依据）

> **重要**: 本章为 ML 时代（2026-08-05 阶段四完成后）的实际特征体系，是当前生产环境的实施依据。前九章为 pre-ML JS 时代归档参考。

### 10.1 ML 时代特征架构概览

ML 时代特征体系采用 Python 特征管线（feature_utils.py + feature_temporal.py + elo_rating.py + d013_temporal_odds.py），通过 feature_temporal.py 黑名单+白名单双重机制严格防止数据泄露。

| 演进阶段 | 特征维度 | 数据量 | CV 准确率 | 关键成果 |
|---------|---------|--------|----------|---------|
| 阶段三（2026-08-05） | 75维 | 1265场 | LGB 48.67% | Elo+赔率+时序基础 |
| 阶段五（2026-08-06） | 60维（精简） | 1265场 | LGB 50.95% | D-017特征选择+Elo bug修复 |
| T-003.5（2026-08-10） | 114维 | 5252场 | LGB 49.71% | 比分+非线性特征集成 |
| T-005 v3（2026-08-11） | 71维 | 3914场 | 待验证 | 24维对手Lag特征 |

### 10.2 核心 WDL 预测模型特征体系（114维）

**生产模型**: assets/advanced_model_20260810_092643.pkl（T-003.7 TEAM_NAME_MAP扩充后）

| 特征类别 | 维度 | 关键特征 | 数据来源 |
|---------|------|---------|---------|
| 赔率特征 | 66维 | WDL/HCP/TG赔率+凯利指数+变化率+市场指标 | odds.db *_history表 |
| 基础特征 | 12维 | league one-hot 5维 + 其他基础统计 | matches表 |
| Elo特征 | 10维（强制保留） | home_elo/away_elo/elo_diff/elo_ratio/elo_expected/momentum/confidence | elo_rating.py计算 |
| D-013时序特征 | 10维（强制保留） | 波动率3+加速度1+趋势2+稳定性1+突变1+频率1+总变化1 | odds.db *_history表 |
| 比分赔率特征 | 8维（强制保留） | score_mode_prob/entropy/home_win_prob/draw_prob/away_win_prob/over_25_prob/expected_goals/top3_concentration | score_history表（63.8%覆盖率） |
| 非线性变换特征 | ~7维（D-011自动选择） | log/sqrt/平方/立方/倒数/熵/基尼/交互项 | feature_utils.py build_nonlinear_features() |
| is_season_2526 | 1维 | 赛季指示特征 | 派生 |

**特征选择机制（D-011）**: 三方法融合（相关性分析+XGB重要性+RFE），从187维降至113核心+1指示=114维

**时序分离机制（D-009）**: feature_temporal.py 黑名单（赛后特征）+白名单（PRE_MATCH_FEATURE_CATALOG），所有特征通过 detect_leakage() 检测

### 10.3 T-004 总进球预测特征体系

**生产模型**: LightGBM，5折TimeSeriesSplit CV

| 特征组 | 维度 | 内容 | 来源 |
|--------|------|------|------|
| TG赔率特征 | 20维 | 基础概率8维（goals_0~7_plus）+ 衍生12维（大/小球概率、期望进球、熵、集中度、方差等） | total_goals_history表 |
| Elo特征 | 10维 | 球队实力评分 | elo_rating.py |
| 历史进球均值 | 6维 | hist_home_avg_goals等 | 历史聚合 |
| WDL隐含概率 | 3维 | wdl_win_a/draw/win_b | wdl_history表 |
| SofaScore球队特征 | 23维 | 主客队差值（xG/xA/射门/传球/跑动等） | sofascore_team_features表 |
| Lag版本比赛级特征 V2 | 180维 | 15核心指标×4窗口（w3/w5/w10/all）×3维度（主/客/差） | match_player_stats历史聚合 |

**Lag版本防泄露机制**: rolling(window).mean().shift(1)，shift(1) 确保当前比赛数据不参与特征计算

**关键教训**: 比赛级赛后统计特征导致性能虚高68.53pp（92.24% vs 真实23.22%），必须严格使用Lag版本

### 10.4 T-005 让球胜平负预测特征体系（v3，71维）

**生产模型**: 二阶段模型（Stage1走水检测器 + Stage2方向预测器）+ ratio=1.5 + T=2.150 + 动态阈值

| 特征组 | 维度 | 内容 | 来源 |
|--------|------|------|------|
| HCP赔率特征 | 15维 | 基础概率3维（hcp_prob_win/draw/lose）+ 衍生12维（strength/draw_risk/confidence/entropy等） | handicap_history表 |
| WDL平局特征 | 3维 | wdl_draw相关 | wdl_history表 |
| 球队状态特征 | 12维 | 球队近期表现、休息天数、红黄牌风险 | 派生 |
| 盘口线反推特征 | 3维 | 赔率反推盘口线相关 | handicap_line_inference.py |
| 市场信号 | 4维 | Elo差距、赔率分位数等 | 派生 |
| **对手调整Lag特征** | **24维** | **A对手实力分层8维 + B盘口线类别6维 + C H2H交锋6维 + D市场信号4维** | **hcp_opponent_lag_features.py** |
| Elo特征 | 10维 | 球队实力评分 | elo_rating.py |

**对手Lag特征详细设计（D-20260811-037）**:

| 特征组 | 维度 | 关键特征 | 防泄露机制 |
|--------|------|---------|-----------|
| A 对手实力分层 | 8维 | home_hcp_draw_vs_stronger_l5、away_hcp_draw_vs_weaker_l5 | shift(1)+rolling(window) |
| B 盘口线类别专属 | 6维 | home_hcp_draw_at_give1_l10、away_hcp_draw_at_give2_l10 | 按盘口线类别分组 |
| C 直接交锋H2H | 6维 | h2h_hcp_draw_rate、h2h_last5_hcp_draws | 按日期过滤历史 |
| D 市场信号增强 | 4维 | elo_gap_abs、market_draw_std、hcp_draw_prob_rank | 派生 |

**性能优化**: O(N²) apply → merge_asof 批量对齐 + groupby+rolling 向量化

**覆盖率**: 3914场比赛，A组95.6%、B组100%、C组68.6%、D组100%

### 10.5 特征工程关键模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 特征构建核心 | scripts/feature_utils.py | build_all_features() 主入口，含比分+非线性特征 |
| 时序分离 | scripts/feature_temporal.py | 黑名单+白名单+泄露检测+赛前过滤 |
| Elo评分 | scripts/elo_rating.py | 标准Elo系统（初始1500, K=32, 主场优势65） |
| 时序赔率 | scripts/d013_temporal_odds.py | 从*_history表提取10维时序特征 |
| 特征选择 | scripts/feature_selection_d011.py | 三方法融合降维 |
| 比分特征 | scripts/score_features.py | 8维比分赔率特征 |
| SofaScore特征 | scripts/sofascore_features.py | 23维球队级差值特征 |
| 比赛级Lag V2 | scripts/match_level_lag_features_v2.py | 180维无泄露Lag特征 |
| 对手Lag特征 | scripts/hcp_opponent_lag_features.py | 24维对手调整Lag特征 |
| HCP特征 V2 | scripts/hcp_features_v2.py | T-005 v3 71维特征集成 |

### 10.6 ML 时代特征工程经验总结

1. **数据泄露是最大风险**: 比赛级赛后统计导致性能虚高68.53pp，必须严格使用Lag版本
2. **特征精简优于堆砌**: 85维→60维CV反而提升0.57pp，证明原特征存在冗余
3. **Elo公式符号错误代价**: 一个符号反转导致CV下降0.86pp，测试框架价值显现
4. **对手调整Lag特征价值**: 24维对手Lag贡献57.1%决策权重，是走水召回率提升关键
5. **中英文映射是数据融合核心**: TEAM_NAME_MAP(193条)解决多源数据融合，覆盖率从4.6%→98.6%
6. **时序分离机制必须前置**: feature_temporal.py黑名单+白名单双重机制是防泄露的基础
7. **特征选择三方法融合**: 相关性分析+XGB重要性+RFE比单一方法更稳健

---

**文档版本**: v2.0
**生成时间**: 2026-07-23
**最后更新**: 2026-08-12
**更新说明**:
- **v2.0 (2026-08-12)**: 新增第十章 ML 时代特征体系，描述当前生产环境实际特征架构（114维核心WDL + T-004总进球 + T-005 v3 71维对手Lag）。原 v1.0 内容（第一至九章）标记为 pre-ML 时代归档参考。
- **v1.0 (2026-07-23)**: 基于 pre-ML JS 时代的特征扩展方案（153→226维，OddsAnalyzer专家系统）。
