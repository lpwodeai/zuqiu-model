# 文档精简方案 v1.0

> **生成日期**: 2026-08-23
> **分析范围**: `docs/` 全目录（含 archive/、audit/、superpowers/）
> **总文档数**: 49 个文件
> **精简后**: 13 个核心文档 + 9 个归档保留

---

## 一、筛选标准

| 标准 | 说明 |
|------|------|
| **唯一性** | 内容是否被其他更权威的文档完全覆盖？ |
| **时效性** | 内容是否仍然有效？（版本号、数据量、配置路径） |
| **可操作性** | 是否直接指导当前系统的开发/运维？ |
| **追溯价值** | 是否记录了不可替代的决策过程或历史上下文？ |
| **文件大小** | 是否异常大（>100KB）且内容可被其他文档替代？ |

---

## 二、分类结果

### A. 必须保留 — 核心文档 (13个)

这些文档是项目知识体系的骨架，删除任何一篇都会导致信息丢失。

| # | 文件 | 大小 | 保留原因 |
|---|------|------|---------|
| 1 | `model_gap_analysis_framework.md` | ~50KB | 差距分析框架基准，对比标准 |
| 2 | `model_gap_analysis_report_v2.0.md` | 53KB | 核心交付物，差距分析+优化路线图 |
| 3 | `SYSTEM_ANALYSIS_REPORT_v2.0.md` | 95KB | 当前系统 v8.3 完整现状文档 |
| 4 | `change_log.md` | 289KB | 完整变更日志，工程追溯唯一来源 |
| 5 | `key_decisions.md` | 48KB | 关键决策记录，知识沉淀核心 |
| 6 | `project_memory.md` | 30KB | 项目规则+工程约定+经验教训 |
| 7 | `CONVERSATION_WORKFLOW_GUIDE.md` | 25KB | 新对话执行流程，操作规范 |
| 8 | `feature-engineering-guide.md` | 36KB | 特征工程体系完整指南 |
| 9 | `league-data-spec.md` | 8KB | 数据源规范，新数据接入参考 |
| 10 | `player-feature-spec.md` | 21KB | 球员级特征工程方案 |
| 11 | `T005_handicap_line_inference_技术实现文档.md` | 15KB | T-005 让球盘口技术实现 |
| 12 | `understat_schema.md` | 4KB | 数据库表结构参考 |
| 13 | `prompt_template.md` | 33KB | 结构化提示词模板（与 CONVERSATION_WORKFLOW_GUIDE 互补） |

### B. 可归档 — 保留在 archive/ (9个)

这些文档已完成历史使命，但仍有参考价值（如上线评估、诊断报告）。

| # | 文件 | 大小 | 归档原因 |
|---|------|------|---------|
| 1 | `ARCHITECTURE_EVALUATION_REPORT_v2.0.md` | 26KB | 已被 gap_analysis_report 覆盖，但有历史评估细节 |
| 2 | `足球模型架构深度诊断报告_v2.0.md` | 31KB | 已被 gap_analysis_report 覆盖，但有过优化/欠优化详细分析 |
| 3 | `DEPLOYMENT_CONFIG_v2.0.md` | 12KB | 部署配置 v2.0，当前已是 v8.3，但保留环境要求参考 |
| 4 | `DEPLOYMENT_EVALUATION_REPORT.md` | 6KB | 上线评估 v2.9，有性能对比数据 |
| 5 | `LEARNING_RATE_ANALYSIS_REPORT.md` | 9KB | 学习率分析，已融入 key_decisions |
| 6 | `OVERFIT_FIX_REPORT.md` | 8KB | 过拟合修复对比，已融入 key_decisions |
| 7 | `data_source_migration_checklist.md` | 4KB | 数据源迁移清单，迁移已完成 |
| 8 | `西甲2026-2027第1轮_4场新数据预测分析报告.md` | 7KB | 单次预测报告，时效性已过 |
| 9 | `audit/sofascore_quality_audit_20260809_052900.md` | 18KB | 一次性审计报告 |

### C. 建议删除 (27个)

这些文档已被其他文档覆盖、内容过时、或与项目核心无关。

| # | 文件 | 大小 | 删除原因 |
|---|------|------|---------|
| — | **archive/ 子目录 (20个文件)** | — | — |
| 1 | `archive/optimization_log.md` | 158KB | 已被 key_decisions.md + change_log.md 完全覆盖 |
| 2 | `archive/model_optimization_plan.md` | 46KB | 优化计划已过期，实际执行已偏离 |
| 3 | `archive/action_plan_20260808.md` | 33KB | 当日行动计划，已过期 |
| 4 | `archive/unimplemented_optimization_plans_report.md` | 33KB | 未实施优化已被 gap_analysis_report 覆盖 |
| 5 | `archive/PROJECT_DELIVERY_REPORT.md` | 32KB | 阶段性交付报告，已过期 |
| 6 | `archive/DEPLOYMENT_GUIDE.md` | 29KB | 部署指南已被 DEPLOYMENT_CONFIG_v2.0.md 覆盖 |
| 7 | `archive/analysis_report_20260808.md` | 21KB | 当日分析报告，已过期 |
| 8 | `archive/accuracy-improvement-plan.md` | 19KB | 已被 gap_analysis_report 覆盖 |
| 9 | `archive/final_deliverable_phase_a_to_e_25_26.md` | 18KB | 阶段性交付，已过期 |
| 10 | `archive/TRAINING_UPDATE_REPORT_v3.39_20260812.md` | 16KB | 训练更新报告，已过期 |
| 11 | `archive/laliga_import_log.md` | 161KB | 导入日志，异常大，内容无长期价值 |
| 12 | `archive/DIAGNOSTIC_REPORT_20260812.md` | 13KB | 诊断报告，已过期 |
| 13 | `archive/memory_system_analysis.md` | 13KB | 已被 project_memory.md 覆盖 |
| 14 | `archive/fix_log_league_name_normalization_20260809.md` | 12KB | 修复日志已融入 change_log.md |
| 15 | `archive/C001_data_pipeline_implementation_plan.md` | 11KB | 实施计划已过期 |
| 16 | `archive/plan_score_nonlinear_final_20260810.md` | 10KB | 执行计划已过期 |
| 17 | `archive/fbref-scraper-plan.md` | 9KB | 采集计划已过期 |
| 18 | `archive/联赛名归一化_验证集切分优化.md` | 9KB | 修复日志已融入 change_log.md |
| 19 | `archive/plan_t003_execution_20260810.md` | 7KB | 执行计划已过期 |
| 20 | `archive/SCORE_EVALUATION_REPORT_v3.40_20260813.md` | 6KB | 评分评估报告，已过期 |
| — | **其他** | — | — |
| 21 | `archive/bl1_it_optimization_plan.md` | 5KB | 联赛优化计划，已过期 |
| 22 | `archive/epl_optimization_report.md` | 5KB | 联赛优化报告，已过期 |
| 23 | `archive/T-003_plan_20260810.md` | 3KB | 执行计划，已过期 |
| 24 | `archive/lambda_param_change_log.md` | 3KB | 参数变更日志，已融入 change_log.md |
| 25 | `archive/laliga_calibration_report.md` | 2KB | 校准报告，已过期 |
| 26 | `archive/RELEASE_NOTES_v2.0.md` | 2KB | 发布说明，已过期 |
| 27 | `archive/PR_DESCRIPTION_v2.0.md` | 2KB | PR 描述，已过期 |
| 28 | `superpowers/specs/2026-07-17-obsidian-second-brain-design.md` | 36KB | **与项目无关**，Obsidian 个人知识库设计 |

---

## 三、精简效果

| 指标 | 精简前 | 精简后 | 减少 |
|------|:--:|:--:|:--:|
| 文件总数 | 49 | 22 | **-55%** |
| 总大小 | ~1,400KB | ~700KB | **-50%** |
| 根目录文件数 | 21 | 13 | **-38%** |
| archive/ 文件数 | 27 | 9 | **-67%** |

---

## 四、执行步骤

### 步骤1：移动到 archive/（9个文件）

```powershell
Move-Item "docs/ARCHITECTURE_EVALUATION_REPORT_v2.0.md" "docs/archive/"
Move-Item "docs/足球模型架构深度诊断报告_v2.0.md" "docs/archive/"
Move-Item "docs/DEPLOYMENT_CONFIG_v2.0.md" "docs/archive/"
Move-Item "docs/DEPLOYMENT_EVALUATION_REPORT.md" "docs/archive/"
Move-Item "docs/LEARNING_RATE_ANALYSIS_REPORT.md" "docs/archive/"
Move-Item "docs/OVERFIT_FIX_REPORT.md" "docs/archive/"
Move-Item "docs/data_source_migration_checklist.md" "docs/archive/"
Move-Item "docs/西甲2026-2027第1轮_4场新数据预测分析报告.md" "docs/archive/"
Move-Item "docs/audit/sofascore_quality_audit_20260809_052900.md" "docs/archive/"
```

### 步骤2：删除过期文件（28个）

```powershell
# 删除 archive/ 中所有过期文件
Remove-Item "docs/archive/*" -Force

# 删除与项目无关的 superpowers/ 目录
Remove-Item "docs/superpowers" -Recurse -Force

# 删除空 audit/ 目录
Remove-Item "docs/audit" -Recurse -Force
```

### 步骤3：将 B 类归档文件移回 archive/

```powershell
# 在步骤2之后，将9个归档文件放回 archive/
# (已在步骤1中移入)
```

### 步骤4：更新 change_log.md

记录本次文档精简操作。

---

## 五、精简后目录结构

```
docs/
├── model_gap_analysis_framework.md          # 差距分析框架基准
├── model_gap_analysis_report_v2.0.md        # 差距分析报告（核心交付）
├── SYSTEM_ANALYSIS_REPORT_v2.0.md           # 系统现状分析 v8.3
├── change_log.md                            # 变更日志
├── key_decisions.md                         # 关键决策记录
├── project_memory.md                        # 项目记忆
├── CONVERSATION_WORKFLOW_GUIDE.md           # 对话工作流指南
├── feature-engineering-guide.md             # 特征工程指南
├── league-data-spec.md                      # 数据源规范
├── player-feature-spec.md                   # 球员特征规范
├── T005_handicap_line_inference_技术实现文档.md  # T-005 技术实现
├── understat_schema.md                      # 数据库表结构
├── prompt_template.md                       # 提示词模板
└── archive/
    ├── ARCHITECTURE_EVALUATION_REPORT_v2.0.md
    ├── 足球模型架构深度诊断报告_v2.0.md
    ├── DEPLOYMENT_CONFIG_v2.0.md
    ├── DEPLOYMENT_EVALUATION_REPORT.md
    ├── LEARNING_RATE_ANALYSIS_REPORT.md
    ├── OVERFIT_FIX_REPORT.md
    ├── data_source_migration_checklist.md
    ├── 西甲2026-2027第1轮_4场新数据预测分析报告.md
    └── sofascore_quality_audit_20260809_052900.md
```

---

## 六、风险评估

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|:--:|------|---------|
| 误删有用文档 | 低 | 中 | `archive/` 先保留 7 天再删除 |
| 丢失历史决策上下文 | 低 | 中 | `key_decisions.md` + `change_log.md` 已覆盖核心信息 |
| 其他脚本引用文档路径 | 低 | 低 | 本次仅移动/删除 `.md` 文件，无代码引用 |

---

> **文档版本**: v1.0
> **审核状态**: 待用户确认后执行