# 统一预测引擎接入 train_models.py 训练流程 — 实施方案

> 版本：v1.0　|　日期：2026-08-23　|　状态：**已实施**（2026-09-09，C→A→B 全阶段完成，详见 change_log §3.143/§3.144：前置 τ/max_goals/ρ 修正 + 阶段C 推理侧引擎路径 + 阶段A λ 反推评估 RPS 0.0025 + 阶段B λ 回归头 NLL 1.4642 优于赔率基准 2.5665；资产 assets/lambda_model_export.js）
> 关联：P0-4 统一架构　|　上游引擎 [unified_prediction_engine.py](../scripts/unified_prediction_engine.py)
> **⚠️ 分析时点快照注记（2026-09-09）**: 文内 T-006 v4 描述已被 T-006 v5 取代（C-20260908-022）；本文方案 C→A→B 已全部实施（状态见上），正文技术细节保留作历史与维护参考。

---

## 1. 背景与目标

### 1.1 背景
T-005（让球）、T-006（比分）、总进球当前是**独立并行模型**，T-006 v4 仅通过 WDL 重加权（C-20260823-019 后处理补丁）缓解与胜平负的不一致，但**根本架构未统一**。

[unified_prediction_engine.py](../scripts/unified_prediction_engine.py) 的 `DixonColesGenerator`（P0-4）已实现「λ → 比分矩阵 → 四维导出」的统一生成能力，但尚未接入训练/推理流程。

### 1.2 目标架构（P0-4 原意）
```
ML 回归模型 → λ_home, λ_away → Dixon-Coles 比分矩阵
    ├─ 胜平负概率（边际分布）
    ├─ 让球概率（条件分布）
    ├─ 大小球概率（条件分布）
    └─ 精确比分（矩阵直接输出）
```
四维预测从**同一个比分矩阵**导出，天然保证一致性，替代分散的独立计算。

---

## 2. 现状梳理（三个关键事实）

### 事实 1：两套重复的 Dixon-Coles 实现，τ 公式不一致

| 维度 | `DixonColesGenerator`（P0-4，未接入） | `CalcEngine`（现有生产用） |
|---|---|---|
| 位置 | [unified_prediction_engine.py L99-L224](../scripts/unified_prediction_engine.py#L99-L224) | [prediction_core.py L287-L313](../scripts/prediction_core.py#L287-L313) |
| 结构 | **统一比分矩阵 → 四维导出**（天然一致） | 分散静态方法，各维度独立计算 |
| τ 修正 | `1±ρ`（**简化版**） | `1-λμρ` / `1±λρ` / `1-ρ`（**论文标准版**） |

> ⚠️ **接入前置项 1**：τ 公式必须统一。建议以 `CalcEngine` 的论文标准版为准，修正 `DixonColesGenerator._dc_correction` 为 `1-λμρ`/`1±λρ`/`1-ρ` 形式（当前简化版在 λ 偏离 1 时产生偏差）。

### 事实 2：现有 λ 来自赔率，非 ML 回归
[calc_lambda_from_odds](../scripts/prediction_core.py#L173-L183) 用赔率隐含概率反推 λ，再经 [A-002 两层缩放](../scripts/prediction_core.py#L186-L244)。**无任何 ML 回归直接产出 λ**。

### 事实 3：train_models.py 只训练 WDL 分类器
[train_models.py](../scripts/train_models.py) 训练 XGBoost/LightGBM 的 **WDL 3 类分类器**，产出验证集指标 + 导出 JS 模型。**不产出 λ、不训练比分/让球/总进球**。

---

## 3. 核心矛盾

统一引擎 `generate(λ_home, λ_away)` 需要 **λ 标量输入**；而 `train_models.py` 训练的是 **WDL 分类器（3 类概率）**，两者接口不匹配。因此「接入」的本质是解决 **λ 从哪来**。

---

## 4. 决策记录（已确认）

| 决策点 | 结论 |
|---|---|
| λ 来源路径 | **分阶段 C→A→B**（先小改验证引擎价值，再升级到 λ 回归头） |
| WDL 融合策略 | **分类为主，引擎重加权**（ML WDL 分类器仍是主预测，引擎 wdl 边际仅用于比分重加权） |
| 方案落盘 | 独立方案文档（本文档） |

---

## 5. 三条 λ 来源路径对比

| 路径 | λ 来源 | 改动量 | 对齐目标架构 | 主要风险 |
|:--:|---|:--:|:--:|---|
| **A** | WDL 概率 + 总进球约束反推 | 中 | 部分 | 3 维→2 维反演多解，精度有限 |
| **B** | 新增 λ 回归头（XGB/LGB） | 大 | ✅ 完全 | 需改特征/导出/JS 对齐 |
| **C** | 复用现有赔率 λ | 小 | 否（但验证引擎价值） | λ 质量依赖赔率 |

---

## 6. 四维接入策略总表（分类为主原则）

| 预测维度 | 现有实现 | 接入后策略 | 阶段 |
|---|---|---|---|
| WDL 胜平负 | [WDLPredictor](../scripts/prediction_core.py#L654)（5 模型 Stacking） | **保持 ML 分类为主**；引擎 wdl 边际仅做重加权 | A 起 |
| 比分 | [ScorePredictor](../scripts/prediction_core.py#L1394)（Poisson+DC+MC+赔率） | 引擎 `exact_score` 为主，保留赔率融合 + WDL 重加权 | C 起 |
| 大小球/总进球 | [TotalGoalsPredictor](../scripts/prediction_core.py#L1520)（Poisson λ+赔率） | 引擎 `over_under`/`total_goals` 为主，保留赔率融合 | C 起 |
| 让球 | [HandicapPredictor](../scripts/prediction_core.py#L1158)（T-005 v3 独立 ML） | **保守暂不动**；引擎 `handicap` 做一致性校验 | B 后评估 |

---

## 7. 分阶段详细设计

### 阶段 C：引擎替换分散 DC（推理侧，最小接入）

**目标**：把 `prediction_core.py` 中比分/总进球的分散 DC 计算替换为引擎统一矩阵导出，消除四维不一致。

**改动文件**：
- [unified_prediction_engine.py](../scripts/unified_prediction_engine.py)：修 τ 公式（前置项 1）
- [prediction_core.py](../scripts/prediction_core.py)：`ScorePredictor.predict` / `TotalGoalsPredictor.predict` 内部改为调用 `DixonColesGenerator`

**接口对齐**：
```python
# 引擎 max_goals 与 T-006 现有 max_goals=7 对齐
gen = DixonColesGenerator(max_goals=7)
gen.set_league_rho(league)
result = gen.generate(lambda_home, lambda_away, verbose=False)

# result['exact_score']  → 替换 poisson_score_predict + monte_carlo_score_predict 末段
# result['over_under']    → 替换 calc_total_goals_from_lambda
# result['wdl']           → 参与比分重加权（ratio 机制，保留 C-20260823-019）
```

**保留不变**：赔率融合权重（T006_POISSON_WEIGHT/MC_WEIGHT/SCORE_ODDS_ALPHA 的赔率部分）、WDL→比分重加权机制。

**验收**：引擎导出的比分/大小球在验证集与现有模块 ≥95% 样本一致（排除 τ 修正差异）。

---

### 阶段 A：λ 反推 + 训练时引擎评估（训练侧）

**目标**：用 ML WDL 概率（而非赔率）驱动引擎，并在 `train_models.py` 产出「统一引擎一致性报告」。

**新增函数**（[train_models.py](../scripts/train_models.py)）：
```python
def infer_lambda_from_wdl(wdl_probs: dict, total_goals: float = 2.8):
    """从 WDL 概率 + 总进球约束数值反演 λ_home/λ_away。

    约束方程组：
      total_goals ≈ λ_home + λ_away
      P(win)/P(lose) ≈ 由 λ_home/λ_away 决定的 Poisson 边际比
    用 scipy.optimize 数值求解，解得 (λ_home, λ_away)。
    """

def evaluate_unified_engine(y_true_wdl, y_pred_wdl, df_meta, feature_names):
    """阶段 A: 验证集上量化统一引擎一致性，写入 final_report。"""
```

**final_report 新增字段**（[train_models.py L858](../scripts/train_models.py#L858)）：
```json
"unified_engine": {
  "enabled": true,
  "rho_unified": true,
  "lambda_source": "wdl_infer",
  "wdl_marginal_vs_classifier_rps": 0.21,
  "score_consistency": 0.95,
  "n_evaluated": 1052
}
```

**验收**：训练产出一份「统一引擎一致性报告」，WDL 边际 vs 分类概率的 RPS ≤ 0.22。

---

### 阶段 B：λ 回归头（完整落地，目标架构）

**目标**：实现「ML 特征 → λ → 引擎 → 四维」的完整闭环。

**改动**（[train_models.py](../scripts/train_models.py)）：
1. 新增 `train_lambda_head()`：XGBoost/LightGBM 回归 `λ_home`/`λ_away`，损失为 Poisson NLL（`λ - goals*logλ + log(goals!)`）。
2. 目标 = 每场真实进球数（`FT home goals`/`FT away goals`）。
3. 模型导出：新增 `convert_lambda_to_js()`，与 [convert_xgb_to_js](../scripts/train_models.py#L467)/[convert_lgb_to_js](../scripts/train_models.py#L534) 对齐。
4. JS 推理侧（[prediction-engine.js](../shared/prediction-engine.js)）同步对齐 λ 回归头。

**验收**：λ 回归头验证集 Poisson NLL 不劣于赔率 λ 基准；RPS 维持 ≤ 0.21，不引入准确性回退。

---

## 8. 前置修正项

| # | 修正项 | 位置 | 说明 |
|---|---|---|---|
| 1 | τ 公式统一 | [unified_prediction_engine.py](../scripts/unified_prediction_engine.py#L87-L97) | 改为论文标准版 `1-λμρ`/`1±λρ`/`1-ρ` |
| 2 | max_goals 对齐 | 引擎实例化处 | 与 T-006 现有 `max_goals=7` 对齐 |
| 3 | ρ 默认值统一 | 引擎 vs `T006_RHO=-0.30` | 明确 strong handicap 使用 `T006_RHO_HIGH=-0.10` 的策略是否迁移到引擎 |

---

## 9. 风险与降级

| 风险 | 影响 | 缓解 |
|---|---|---|
| τ 公式修正改变比分分布 | 阶段 C 后比分与历史预测不一致 | 修正前先跑 backtest 对比，量化影响 |
| λ 反演多解（阶段 A） | λ 精度不足 | 用总进球约束 + 正则化；A 仅作验证不直接上生产 |
| λ 回归头过拟合（阶段 B） | 验证集好、线上差 | 时间序列 CV + 正则化（对齐现有 Optuna 约束） |
| 引擎替代破坏现有 T-005/T-006 | 让球/比分回退 | 每阶段独立灰度，保留 `USE_UNIFIED_ENGINE` 开关快速回滚 |

---

## 10. 实施检查清单

- [ ] 前置项 1：修正 `_dc_correction` τ 公式（并跑 backtest 量化影响）
- [ ] 前置项 2/3：max_goals=7、ρ 策略对齐
- [ ] 阶段 C：`ScorePredictor`/`TotalGoalsPredictor` 改引擎 + 一致性验收
- [ ] 阶段 A：`infer_lambda_from_wdl` + `evaluate_unified_engine` + final_report 字段
- [ ] 阶段 B：`train_lambda_head` + 导出 + JS 对齐 + 验收
- [ ] 全阶段：保持 `USE_UNIFIED_ENGINE` 开关，支持快速回滚