# T-005 路径1：让球盘口线反推模型 — 技术实现文档

> 创建时间：2026-08-10
> 关联决策：D-20260810-032（待添加）
> 关联变更：C-20260810-133（待添加）
> 关联代码：[handicap_line_inference.py](file:///H:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/handicap_line_inference.py)
> 关联验证报告：[hcp_line_inference_validation_20260810_181245.json](file:///H:/zuqiu/五大联赛专属模型/五大联赛专属模型/reports/hcp_line_inference_validation_20260810_181245.json)

---

## 一、背景与目标

### 1.1 问题背景

T-005 让球胜平负预测模块（§4.16）当前面临**样本严重不足**的核心瓶颈：

| 指标 | 当前值 | 问题 |
|------|--------|------|
| 训练样本 | **242 场**（4.6%） | 极少，CV 方差 ±5.93% |
| 联赛覆盖 | 仅意甲(200)+法甲(42) | 其他3大联赛+2赛季完全缺失 |
| 走水预测 | 0/44 正确 | 模型无法预测走水 |
| 盘口线数据 | 仅42场有 handicap_line | 无法从 actual_score 扩充标签 |

**根因**：`handicap_history` 表有 4,014 场比赛的赔率数据（hcp_win/draw/lose），但**没有盘口线字段**；`matches.handicap`（盘口线）仅42场有值（0.8%）。没有盘口线就无法从 actual_score（5,258场100%覆盖）计算 actual_handicap 标签。

### 1.2 解决目标

通过**赔率反推盘口线**（路径1），将训练样本从 242 场扩充至 ~3,908 场（16倍），覆盖5大联赛×3赛季。

### 1.3 两条路径对比结论

| 维度 | 路径1（赔率反推）✅ 推荐 | 路径2（外部补充） |
|------|--------------------------|-------------------|
| 可扩充样本 | 3,672场（确定） | 不确定 |
| 实施周期 | 1-2天 | 1-2周 |
| 数据精度 | 中等（反推有误差） | 高（原始数据） |
| 外部依赖 | 无 | 需爬虫 |
| ROI | 高（1-2天→16倍样本） | 中 |

---

## 二、数据分析

### 2.1 数据库结构

```
handicap_history 表（4,014场比赛，20,068条时序赔率）
    字段：match_id, timestamp, hcp_win, hcp_draw, hcp_lose
    ⚠️ 没有盘口线（handicap_line）字段

matches 表（5,258场）
    actual_score（实际比分）: 5,258场（100%）✅
    actual_handicap（让球结果标签）: 仅242场（4.6%）❌
    handicap（盘口线）: 仅42场（0.8%）❌❌

match_id_mapping 表（3,957条映射）
    history_match_id（中文）→ matches_match_id（英文），覆盖率98.6%
```

### 2.2 验证样本（42场）

同时有盘口线 + 比分 + 让球结果的42场比赛，盘口线分布：

| 盘口线 | 含义 | 场数 | 占比 |
|--------|------|------|------|
| -1.0 | 主队让1球 | 28 | 66.7% |
| +1.0 | 主队受让1球 | 13 | 31.0% |
| -2.0 | 主队让2球 | 1 | 2.4% |

**关键发现**：中国竞彩让球玩法**仅使用整数盘**（-1, 0, 1, -2, 2），不涉及半球/Quarter盘，大幅降低了反推复杂度。

### 2.3 赔率与盘口线的规律

从42场验证样本中观察到的清晰规律：

| 盘口线 | avg hcp_win | avg hcp_draw | avg hcp_lose | 规律 |
|--------|-------------|--------------|--------------|------|
| +1.0（受让1球） | **1.784**（低） | 3.516 | 3.952（高） | 上盘赔率低（受让后易赢） |
| -1.0（让1球） | 3.344（高） | 3.445 | **2.145**（低） | 下盘赔率低（让球后难赢） |
| -2.0（让2球） | 3.45 | 4.0 | **1.7**（极低） | 下盘赔率极低 |

**核心规律**：hcp_win/lose 的赔率差异与盘口线方向/大小高度相关。

### 2.4 让球结果计算公式（42场100%验证通过）

```
adjusted_diff = (home_goals - away_goals) + handicap_line
    adjusted_diff > 0  → 胜（上盘赢, label=0）
    adjusted_diff = 0  → 平（走水,   label=1）
    adjusted_diff < 0  → 负（下盘赢, label=2）
```

### 2.5 可扩充样本量

| 数据类别 | 场数 | 说明 |
|----------|------|------|
| 现有 actual_handicap 标签 | 242 | 意甲200+法甲42 |
| 有赔率+有正常比分+无标签 | **3,672** | 路径1反推目标 |
| 排除"胜其它"等比分 | 6 | 无法解析进球数 |
| **路径1总可用样本** | **3,908** | 242→3,908，16倍 |

---

## 三、反推算法设计

### 3.1 算法整体流程（伪代码）

```
算法：odds-implied handicap line inference
输入：hcp_win, hcp_draw, hcp_lose（赛前最新快照赔率）
输出：handicap_line_pred（反推盘口线），actual_handicap_pred（让球结果标签）

━━━ Phase 1: 特征工程 ━━━

function build_inference_features(hcp_win, hcp_draw, hcp_lose):
    # 原始赔率（3维）
    features = [hcp_win, hcp_draw, hcp_lose]
    
    # 隐含概率（3维）：1/赔率 归一化
    inv_win  = 1 / hcp_win
    inv_draw = 1 / hcp_draw
    inv_lose = 1 / hcp_lose
    inv_sum  = inv_win + inv_draw + inv_lose
    features += [inv_win/inv_sum, inv_draw/inv_sum, inv_lose/inv_sum]
    
    # 赔率关系特征（2维）
    features += [hcp_win / hcp_lose,           # 赔率比值
                 hcp_win - hcp_lose]            # 赔率差值
    
    # 方向与价差特征（5维）
    is_home_favorite = 1 if hcp_win < hcp_lose else 0
    favorite_odds    = min(hcp_win, hcp_lose)
    underdog_odds    = max(hcp_win, hcp_lose)
    odds_spread      = underdog_odds - favorite_odds
    features += [is_home_favorite, favorite_odds, underdog_odds, odds_spread]
    
    # 走水赔率分段（1维）
    draw_level = categorize_draw_odds(hcp_draw)  # 0~5
    features += [draw_level]
    
    return features  # 共13维

━━━ Phase 2: 盘口线分类 ━━━

function infer_handicap_line(features):
    # 逻辑回归多分类（标准化后）
    handicap_line_pred = LogisticRegression.predict(features)
    # 输出 ∈ {-2, -1, 0, +1, +2}
    return handicap_line_pred

━━━ Phase 3: 让球结果标签计算 ━━━

function compute_handicap_result(home_goals, away_goals, handicap_line):
    adjusted_diff = (home_goals - away_goals) + handicap_line
    if adjusted_diff > 0:
        return 0  # 胜（上盘赢）
    elif adjusted_diff == 0:
        return 1  # 平（走水）
    else:
        return 2  # 负（下盘赢）

━━━ Phase 4: 样本扩充 ━━━

function expand_training_samples():
    # 1. 加载242场有真标签的样本（label_source='actual'）
    # 2. 加载3,672场无标签样本
    # 3. 对无标签样本反推盘口线 + 计算标签
    # 4. 合并，标记 label_source='inferred'
    # 5. （可选）按置信度加权
    return expanded_dataset  # ~3,908场
```

### 3.2 规则反推法（基线对比，伪代码）

```
function rule_based_inference(hcp_win, hcp_draw, hcp_lose):
    if |hcp_win - hcp_lose| < 0.15:
        return 0.0   # 平手盘
    elif hcp_win < hcp_lose:
        # 主队热门 → 受让（正盘）
        if hcp_win < 1.5:  return +2.0
        elif hcp_win < 2.2: return +1.0
        else: return 0.0
    else:
        # 客队热门 → 主队让球（负盘）
        if hcp_lose < 1.5:  return -2.0
        elif hcp_lose < 2.2: return -1.0
        else: return 0.0
```

### 3.3 反推误差容忍度分析

**关键洞察**：盘口线反推误差不等于标签误差。当比分差距足够大时，盘口线偏差±1不影响让球结果：

| 比分差距 | 盘口线误差±1的影响 | 标签是否改变 |
|----------|-------------------|-------------|
| ≥3球 | 无影响 | ❌ 不变 |
| 2球 | 极端情况可能影响 | ⚠️ 极少 |
| 1球 | 可能影响走水/胜负边界 | ⚠️ 有可能 |
| 0球（平局） | 直接影响 | ✅ 会改变 |

这解释了为什么逻辑回归盘口线准确率88.1%，但标签准确率高达92.3%——标签对盘口线误差有一定容忍度。

---

## 四、特征工程

### 4.1 特征列表（13维）

| # | 特征名 | 类型 | 描述 | 设计理由 |
|---|--------|------|------|----------|
| 1 | hcp_win | 原始赔率 | 上盘赢赔率 | 直接反映市场对上盘的信心 |
| 2 | hcp_draw | 原始赔率 | 走水赔率 | 整数盘 vs 半球盘的区分信号 |
| 3 | hcp_lose | 原始赔率 | 下盘赢赔率 | 直接反映市场对下盘的信心 |
| 4 | inv_prob_win | 隐含概率 | 1/hcp_win 归一化 | 去除_MARGIN后的真实概率 |
| 5 | inv_prob_draw | 隐含概率 | 1/hcp_draw 归一化 | 走水概率 |
| 6 | inv_prob_lose | 隐含概率 | 1/hcp_lose 归一化 | 下盘赢概率 |
| 7 | win_lose_ratio | 比值 | hcp_win / hcp_lose | 方向与强度信号 |
| 8 | win_lose_diff | 差值 | hcp_win - hcp_lose | 方向与强度信号 |
| 9 | is_home_favorite | 二值 | hcp_win < hcp_lose ? 1 : 0 | 盘口线方向（正/负） |
| 10 | favorite_odds | 聚合 | min(hcp_win, hcp_lose) | 热门方赔率→盘口线大小 |
| 11 | underdog_odds | 聚合 | max(hcp_win, hcp_lose) | 冷门方赔率 |
| 12 | odds_spread | 价差 | underdog_odds - favorite_odds | 实力差距幅度 |
| 13 | draw_odds_level | 分段 | hcp_draw 分段编码(0~5) | 整数盘/半球盘判别 |

### 4.2 走水赔率分段规则

| hcp_draw 值 | level | 含义 |
|-------------|-------|------|
| 0 或 NULL | 0 | 无走水赔率（半球盘） |
| < 3.0 | 1 | 很低（走水可能性大） |
| 3.0 ~ 3.5 | 2 | 正常整数盘 |
| 3.5 ~ 4.0 | 3 | 正常整数盘 |
| 4.0 ~ 5.0 | 4 | 偏高 |
| ≥ 5.0 | 5 | 极高/半球盘特征 |

---

## 五、模型选择

### 5.1 三种模型对比

| 模型 | 盘口线准确率 | 标签准确率 | 优势 | 劣势 |
|------|-------------|-----------|------|------|
| 规则反推法 | 52.4% | 74.4% | 可解释、无训练 | 精度最低 |
| 决策树(DT) | 66.7% | 76.9% | 可解释 | 小样本易过拟合 |
| **逻辑回归(LR)** ✅ | **88.1%** | **92.3%** | **精度最高、稳定** | 需标准化 |

### 5.2 最优模型配置

```python
# 逻辑回归 + 标准化 Pipeline
scaler = StandardScaler()
model = LogisticRegression(
    solver='lbfgs',
    max_iter=1000,
    C=1.0,
    random_state=42,
)
# 注：sklearn 新版本已弃用 multi_class 参数，默认使用 multinomial
```

### 5.3 逻辑回归混淆矩阵（LOOCV）

```
              预测
         -2    -1    +0    +1    +2
真 -2     0     1     0     0     0   (0/1, 误判为-1，仅差1球)
真 -1     0    26     0     2     0   (26/28 = 92.9%)
真 +0     0     0     0     0     0   (无样本)
真 +1     0     2     0    11     0   (11/13 = 84.6%)
真 +2     0     0     0     0     0   (无样本)
```

**误差分析**：主要误差来自 -1 与 +1 之间的混淆（各2场），即"主队让1球"与"主队受让1球"方向判断错误。-2 的1场被误判为 -1（仅差1球，标签可能仍正确）。

### 5.4 标签准确率按类别

| 类别 | 准确率 | 说明 |
|------|--------|------|
| 上盘赢(0) | 13/14 = **92.9%** | 优秀 |
| 走水(1) | 7/8 = **87.5%** | 良好 |
| 下盘赢(2) | 16/17 = **94.1%** | 优秀 |
| **总体** | 36/39 = **92.3%** | ✅ 超过85%标准 |

---

## 六、验证方案

### 6.1 验证方法

采用 **Leave-One-Out Cross-Validation (LOOCV)**，原因：
- 验证样本仅42场，传统K折CV每折样本太少
- LOOCV 最大化利用有限样本，无随机性影响
- 42次迭代，每次用41场训练、1场测试

### 6.2 验证指标

| 指标 | 定义 | 达标标准 | 实际值 | 状态 |
|------|------|----------|--------|------|
| 盘口线准确率 | 反推盘口线 == 真实盘口线 | ≥80% | 88.1% | ✅ |
| 标签准确率 | 反推标签 == 真实让球结果 | ≥85% | 92.3% | ✅ |
| 上盘赢 F1 | 类别0的F1 | — | 92.9% | ✅ |
| 走水 F1 | 类别1的F1 | — | 87.5% | ✅ |
| 下盘赢 F1 | 类别2的F1 | — | 94.1% | ✅ |

### 6.3 防泄露检查

| 检查项 | 结果 |
|--------|------|
| 反推仅使用赛前赔率（hcp_win/draw/lose 最新快照） | ✅ |
| actual_score 用于标签计算（标签非特征） | ✅ |
| 时间戳过滤 2026-07（project_memory 规则） | ✅ |
| 不使用任何赛后统计特征 | ✅ |

---

## 七、预期指标与后续效果

### 7.1 反推模型性能（已验证）

| 指标 | 值 |
|------|-----|
| 验证样本 | 42场 |
| 盘口线准确率 | 88.1% |
| 标签准确率 | 92.3% |
| 验证耗时 | 3.1秒 |
| 最优模型 | 逻辑回归（标准化） |

### 7.2 T-005 模型重训练预期效果

| 指标 | 当前（242场） | 路径1后（~3,908场） | 预期改善 |
|------|---------------|---------------------|----------|
| 样本量 | 242 | ~3,908 | **16倍** |
| 联赛覆盖 | 2联赛1赛季 | 5联赛3赛季 | **全覆盖** |
| CV方差 | ±5.93% | 预计 ±2-3% | **↓50%+** |
| 走水预测 | 0/44 | 预计 F1>0.15 | **从0到可用** |
| CV折数 | 3折 | 5折 | **更稳健** |
| 准确率 | 48.33%（不可靠） | 预计 50%+ | **可靠达标** |

### 7.3 标签噪声影响评估

反推标签准确率 92.3% 意味着约 7.7% 的样本标签错误。影响评估：
- 3,672 × 7.7% ≈ 283 场标签可能有误
- 但 92.3% 的标签正确率远高于随机标签（33.3%）
- 标签噪声会增加训练噪声但不会系统性偏向某个类别
- **缓解措施**：训练时可使用样本权重，高置信度样本权重1.0，低置信度0.5-0.8

---

## 八、风险与缓解

| 风险 | 严重度 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 反推标签噪声（7.7%错误） | 中 | 增加训练噪声 | 置信度加权；92.3%已足够可靠 |
| 盘口线分布不均（-1占67%） | 低 | 模型偏向-1 | class_weight='balanced' |
| 验证样本仅42场 | 中 | LOOCV可能高估泛化误差 | LOOCV是最保守的小样本验证 |
| -2/+2盘口线无验证样本 | 低 | 极少出现（1/42） | 反推时低置信度标记 |
| 数据泄露（P0-15教训） | 高 | 性能虚高 | 仅用赛前赔率；actual_score是标签非特征；detect_leakage验证 |

---

## 九、后续实施步骤

### Phase B: 模型重训练（待执行）

```
Step 1: 反推3,672场无标签比赛的盘口线（用已验证的LR模型）
Step 2: 用 actual_score + 反推盘口线计算 actual_handicap 标签
Step 3: 合并242场真标签 + 3,672场反推标签 = ~3,908场
Step 4: 重新训练 T-005 模型（HCP 15维 + Elo 10维 = 25维）
Step 5: 5折 TimeSeriesSplit CV 验证
Step 6: 对比基线（242场）vs 扩充后（3,908场）
```

### Phase C: 文档同步（待执行）

```
Step 7: change_log.md 添加 C-20260810-133~136
Step 8: optimization_log.md 添加 §4.20
Step 9: key_decisions.md 添加 D-20260810-032
Step 10: project_memory.md 添加反推经验
```

---

## 十、文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `scripts/handicap_line_inference.py` | 代码 | 反推盘口线模型（数据加载+特征工程+LOOCV验证） |
| `reports/hcp_line_inference_validation_20260810_181245.json` | 验证结果 | 42场LOOCV验证详细结果 |
| `docs/T005_handicap_line_inference_技术实现文档.md` | 文档 | 本文档 |

---

**文档版本**: v1.0
**创建时间**: 2026-08-10
**验证状态**: ✅ 盘口线准确率 88.1%（≥80%），标签准确率 92.3%（≥85%），均达标
