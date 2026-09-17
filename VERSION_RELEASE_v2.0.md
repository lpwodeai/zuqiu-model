# 版本发布清单 - v2.0

> **发布日期**: 2026-08-12
> **版本号**: v2.0
> **发布状态**: 正式发布
> **前一版本**: v1.x（T-005 v2 + T-006 v3）

---

## 一、版本概述

v2.0 是五大联赛足球预测模型的重大优化版本，包含 T-005 v3 让球胜平负模型与 T-006 v4 比分预测模型的最终优化成果。T-006 v4 经过 8 轮性能优化（§4.27-§4.34），实现全阶段批量向量化，1 球内命中率达 88.20%。

---

## 二、核心性能指标

### 2.1 T-006 v4 比分预测（最终）

| 指标 | 值 |
|------|-----|
| 总比赛数 | 1415 |
| 精确比分命中率 | 64.66% |
| **1 球内命中率** | **88.20%** |
| 2 球内命中率 | 95.97% |
| 批次标准差 | 3.25% (σ ≤ 5.0%) |
| 逐场评估耗时 | 0.022 ms/场 |
| 批量回测总耗时 | 7.072s（含 ρ 校准 11.428s） |

### 2.2 T-005 v3 让球胜平负

| 指标 | 值 |
|------|-----|
| 模型类型 | LightGBM（114 维） |
| LGB CV 准确率 | 51.24% |
| 温度 T | 0.800 |
| 统一阈值 | 0.500 |
| class_weight 比例 | 1.20 |

### 2.3 负载测试结果（v2.0 新增）

| 并发 | QPS | P95(ms) | P99(ms) | 错误率 |
|------|-----|---------|---------|--------|
| 50 | 3970.2 | 13.98 | 16.24 | 0% |
| 100 | 4130.6 | 25.81 | 27.40 | 0% |
| 200 | **4144.4** | 52.77 | 64.79 | 0% |
| 500 | 3332.3 | 147.85 | 157.30 | 0% |
| 1000 | 2663.2 | 367.25 | 1190.03 | 1.43% |

- 峰值吞吐量: 4144 QPS (@并发 200)
- 安全并发水位: 500
- 建议生产限流: 350 并发

---

## 三、包含文件清单

### 3.1 核心代码

| 文件 | 说明 |
|------|------|
| `scripts/t006_score_predictor_v4.py` | T-006 v4 比分预测（方案A+D+联赛ρ+全批量向量化） |
| `scripts/train_models_v2.py` | T-005 训练入口 |
| `scripts/advanced_model_trainer.py` | 模型训练器 |
| `scripts/deploy_t005v3_final.py` | T-005 v3 部署/重训练脚本 |
| `scripts/retrain_trigger_runner.py` | 重训练触发引擎 |
| `tests/ci_check.py` | CI 三重检查（资产+基线+测试） |
| `scripts/stress_test_v2.js` | 高并发负载测试脚本（v2.0 新增） |
| `scripts/feature_temporal.py` | 特征时序分割（防泄露） |
| `scripts/feature_utils.py` | 特征工程工具 |
| `scripts/elo_rating.py` | Elo 评级计算 |
| `server/index.js` | API 服务入口 |

### 3.2 模型资产

| 文件 | 说明 |
|------|------|
| `assets/t005v3_direction_predictor.pkl` | T-005 v3 方向预测器 |
| `assets/t005v3_draw_detector.pkl` | T-005 v3 平局检测器 |
| `assets/t005v3_metadata.json` | T-005 v3 元数据（含温度/阈值/class_weight） |
| `assets/t006_lowgoal_classifier_v1.pkl` | T-006 低进球分类器 |

### 3.3 配置文件

| 文件 | 说明 |
|------|------|
| `config.yaml` | 训练/模型/Optuna 配置 |
| `ecosystem.config.cjs` | PM2 进程管理配置 |
| `package.json` | Node.js 依赖（v7.5.0） |

### 3.4 文档与报告

| 文件 | 说明 |
|------|------|
| `docs/DEPLOYMENT_CONFIG_v2.0.md` | 部署配置清单（v2.0 新增） |
| `docs/optimization_log.md` | 优化日志（§4.1-§4.34） |
| `docs/change_log.md` | 变更日志 |
| `docs/DEPLOYMENT_GUIDE.md` | 部署指南 |
| `logs/stress_test_report_v2.json` | 负载测试报告（v2.0 新增） |
| `VERSION_RELEASE_v2.0.md` | 本文件 |

---

## 四、优化历程摘要（T-006 v4）

| 阶段 | 优化措施 | 效果 |
|------|----------|------|
| §4.27 | MC n_sim 10000→3000 + bincount | MC 占比 49%→27% |
| §4.29.1 | WDL+Score 批量预加载 | Score 加载 27%→0% |
| §4.29.2 | P2-5 联赛特定 ρ 校准 | 准确性 +0.5pp |
| §4.30 | MC 批量向量化 | MC 39%→0.08% |
| §4.31 | 方案 A 向量化 + 计时 Bug 修复 | 方案 A 45.92%→31.87% |
| §4.32 | Poisson+DC 批量向量化 | Poisson+DC 42%→0% |
| §4.33 | 融合+方案 A 批量向量化 | 方案 A 73.55%→0% |
| §4.34 | evaluate_prediction 审查 | 接近理论下限 |

---

## 五、依赖环境

### 5.1 Node.js
- 运行时: Node.js 20.x LTS
- 依赖: 见 package.json（express ^4.18.2, better-sqlite3 ^13.0.3 等 21 个生产依赖）

### 5.2 Python
- 运行时: Python 3.14
- 核心依赖: numpy, pandas, scipy, scikit-learn, lightgbm, xgboost, optuna, joblib

### 5.3 进程管理
- PM2 5.3+（fork 模式，单实例，max_memory_restart 1G）

---

## 六、部署验证

```bash
# 1. 安装依赖
npm install
pip install numpy pandas scipy scikit-learn lightgbm xgboost optuna joblib

# 2. 启动服务
pm2 start ecosystem.config.cjs --env production

# 3. 健康检查
curl http://localhost:3000/api/health

# 4. CI 验证
python scripts/ci_check.py

# 5. 性能回归测试
python -m pytest tests/ -v
```

---

## 七、已知限制

1. **架构限制**: PM2 fork 单进程 + Express 单线程 + SQLite WAL，水平扩展需改 cluster 模式 + 共享 DB
2. **高并发限制**: 并发超过 500 后 QPS 下降，P99 延迟显著增长
3. **T-006 高进球区间**: 6-7 球 1 球内命中率 8.5%，8+ 球仍为 0%（保守参数 α=0.3, ρ_high=-0.10）
4. **规则引擎**: apply_rule_adjustments() 已禁用（导致平局预测偏差），仅保留 generate_rule_warnings() 辅助信号
5. **数据泄露防护**: 所有特征必须通过 shift(1) + rolling window 确保仅使用历史数据

---

## 八、变更摘要

本版本包含 T-006 v4 从初始版本到全批量向量化的完整优化历程，以及 T-005 v3 生产模型的最终部署配置。详细变更记录见 `docs/change_log.md`。

**v2.0 新增交付物**:
- `docs/DEPLOYMENT_CONFIG_v2.0.md` — 部署配置清单
- `scripts/stress_test_v2.js` — 高并发负载测试脚本
- `logs/stress_test_report_v2.json` — 负载测试报告
- `VERSION_RELEASE_v2.0.md` — 版本发布清单（本文件）
- `release_v2.0.zip` — 代码归档包
