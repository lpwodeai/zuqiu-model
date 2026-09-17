# 防守波动 σ 特征 (D-014) 全量重训对比报告

> 生成时间: 20260818_152500
> 数据规模: 5252 场比赛 (5大联赛)
> 特征维度: 基线 209 维 → 新增 212 维

## 一、核心指标对比

| 指标 | 基线 | 新增 | 变化 |
|------|------|------|------|
| CV LGB Acc | 51.34% | 51.59% | +0.25pp |
| CV LGB LogLoss | 1.0011 | 1.0033 | +0.0023 |
| CV LGB Draw Recall | 6.95% | 4.84% | -2.11pp |
| CV XGB Acc | 51.98% | 52.02% | +0.05pp |
| CV XGB LogLoss | 0.9964 | 0.9968 | +0.0004 |
| CV XGB Draw Recall | 0.86% | 0.69% | -0.18pp |
| Full LGB Acc | 49.76% | 48.72% | -1.05pp |
| Full LGB LogLoss | 1.0147 | 1.0204 | +0.0057 |
| Full LGB Draw Recall | 0.72% | 0.72% | +0.00pp |
| Full XGB Acc | 50.14% | 50.33% | +0.19pp |
| Full XGB LogLoss | 1.0110 | 1.0114 | +0.0004 |
| Full XGB Draw Recall | 0.00% | 0.00% | +0.00pp |
| LGB ECE | 0.0117 | 0.0355 | +0.0237 |
| XGB ECE | 0.0236 | 0.0231 | -0.0004 |

## 二、各类别指标 (LGB 全量训练)

| 类别 | 指标 | 基线 | 新增 |
|------|------|------|------|
| 客胜 | precision | 48.27% | 47.13% |
| 客胜 | recall | 50.76% | 47.42% |
| 客胜 | f1-score | 49.48% | 47.27% |
| 平局 | precision | 28.57% | 14.29% |
| 平局 | recall | 0.72% | 0.72% |
| 平局 | f1-score | 1.40% | 1.37% |
| 主胜 | precision | 50.72% | 50.14% |
| 主胜 | recall | 79.73% | 79.73% |
| 主胜 | f1-score | 62.00% | 61.57% |
| macro avg | precision | 42.52% | 37.19% |
| macro avg | recall | 43.74% | 42.62% |
| macro avg | f1-score | 37.63% | 36.74% |
| weighted avg | precision | 44.09% | 39.71% |
| weighted avg | recall | 49.76% | 48.72% |
| weighted avg | f1-score | 42.05% | 41.17% |

## 三、各类别指标 (XGB 全量训练)

| 类别 | 指标 | 基线 | 新增 |
|------|------|------|------|
| 客胜 | precision | 47.34% | 48.01% |
| 客胜 | recall | 51.37% | 51.37% |
| 客胜 | f1-score | 49.27% | 49.63% |
| 平局 | precision | 0.00% | 0.00% |
| 平局 | recall | 0.00% | 0.00% |
| 平局 | f1-score | 0.00% | 0.00% |
| 主胜 | precision | 51.59% | 51.50% |
| 主胜 | recall | 80.63% | 81.08% |
| 主胜 | f1-score | 62.92% | 62.99% |
| macro avg | precision | 32.97% | 33.17% |
| macro avg | recall | 44.00% | 44.15% |
| macro avg | f1-score | 37.40% | 37.54% |
| weighted avg | precision | 36.61% | 36.79% |
| weighted avg | recall | 50.14% | 50.33% |
| weighted avg | f1-score | 42.00% | 42.15% |

## 四、结论

- 平局召回率 (CV LGB): -2.11pp
- 准确率 (CV LGB): +0.25pp
- ECE 校准误差 (LGB): +0.0237
- 结论: 防守 σ 特征在完整特征集下为负收益（与 D-013 时序波动、比分熵等特征信息重叠），不建议并入生产
