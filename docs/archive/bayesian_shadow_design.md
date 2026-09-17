# P1-B shadow 并行落地设计（仅设计，未实施）

> **文档版本**: v1.0（设计稿，**仅评估与设计，不实施**）
> **生成时间**: 2026-09-11
> **对应变更**: C-20260911-016
> **来源基线**: `docs/bayesian_incremental_design.md`（§四并行对照 + §4.3 shadow 并行）＋`scripts/bayesian_incremental.py`（EKF 增量引擎）＋`scripts/ab_test_framework.py`（P1-D shadow 落库）＋`scripts/prediction_core.py`（在线 bayesian 集成点）＋`scripts/run_daily_p0e.py`（每日编排模式）
> **状态**: 方案已产出，实施待触发（shadow 并行属 P1-B 阶段 2 后置步骤）

---

## 一、检查现状（结论）

对「P1-B 是否已具备 shadow 并行」逐项检查结果：

| 能力 | 现状 | 结论 |
|------|------|------|
| 前置对照门禁 | `bayesian_incremental_ab.py --ab` 四指标不劣化门禁 | ✅ 已跑通（τ=0.01 时 5/5 全 PASS） |
| 增量引擎 | `BayesianIncrementalFilter`（EKF 序贯更新 attack/defense） | ✅ 已实现 |
| shadow 落库框架 | `ab_test_framework.record_experiment`（并行记录多 variant + served 标记） | ✅ 已实现，但**属 P1-D、P1-B 未调用** |
| 在线接入 | `server/`（Node）内 **零处**引用 `bayesian_incremental` | ❌ 增量模型未进预测服务 |
| 增量状态持久化 | `_x`/`_P`/`team_index` 纯内存，无 checkpoint | ❌ 缺失 |
| 滚动时序保证 | 仅离线脚本一次切分序贯；无「每日滚动吸收」机制 | ❌ 缺失 |
| 稳定性判定 | 无「连续 N 轮后验稳定」判据 | ❌ 缺失 |

**核心结论**：P1-B 目前只完成了「离线对照门禁」（阶段 2 的前半），shadow 并行（设计文档 §4.3 第 3 步）尚未落地，需补齐本方案所列能力。

---

## 二、架构决策：离线滚动 shadow（与在线预测解耦）

### 2.1 为什么不在在线服务跑增量臂

- 在线预测服务是 **Node.js**（`server/services/prediction-service.js`），bayesian 基模走 `assets/bayesian_model_{league}.json`（全量重训资产）。
- `BayesianIncrementalFilter` 是 **Python/NumPy** 实现，无法直接进 Node；重写一份 JS EKF 得不偿失。
- shadow 的本质是「消费已完赛比赛的赛果」，**不需要实时**，完全可在离线侧按 `match_date` 序贯滚动。

### 2.2 决策

shadow 并行 = **Python 离线侧每日滚动任务**，与生产在线预测解耦、互不影响：

1. **control 臂（served，生产现状）**：加载 `assets/bayesian_model_{league}.json`，冻结 attack/defense，直接 `predict_wdl`。
2. **treatment 臂（shadow，实验）**：以同一资产初始化 `BayesianIncrementalFilter`，逐场「先 predict 后 update」吸收赛果。
3. 两臂概率并行落 `ab_test_log`；`served=control`，增量臂只观测、不服务。
4. 复用 P0-E 的 `run_daily_p0e.py` + Windows `schtasks` 每日编排模式。

```
每日滚动任务（新增 scripts/run_shadow_incremental.py）
  ┌─ 1) 加载资产: BayesianHierarchicalModel.load(assets/bayesian_model_{league}.json)
  ├─ 2) 恢复/初始化增量 filter: 有 checkpoint 则 load_state_dict，否则 init_teams(...)
  ├─ 3) 读取已完赛队列: match_date > cursor.last_absorbed_date（升序，含赛果）
  ├─ 4) 逐场: control.predict_wdl ──┬──> record_experiment(experiment_id=p1b_shadow_{league},
  │            treatment.predict_wdl ┘       variants={control,treatment}, served=control)
  │            treatment.update(...)         ← 吸收赛果（严格先预测后更新）
  ├─ 5) 保存 checkpoint（x/P/team_index + cursor）
  └─ 6) 每 N 轮: ab_test_framework.analyze + 稳定性判据（见 §七）
```

---

## 三、状态持久化（BayesianIncrementalFilter checkpoint）

### 3.1 现状缺口

`BayesianIncrementalFilter` 仅持有 `_x`（attack/defense 均值向量）、`_P`（完整协方差矩阵）、`team_index`（队名→索引），**无 `to_dict`/`load`、无进度游标**。shadow 滚动周期跨周，服务/进程重启不得丢失状态。

### 3.2 待实现接口契约（本方案拟定，不落地）

```python
class BayesianIncrementalFilter:
    # 新增字段
    league: str
    _last_absorbed_date: str | None   # 进度游标：最后吸收的比赛日期
    _last_match_id: str | None

    def state_dict(self) -> dict:
        return {
            "schema_version": 1,
            "league": self.league,
            "mu": self.mu, "home_adv": self.home_adv, "rho": self.rho,
            "tau_att": self.tau_att, "tau_def": self.tau_def,
            "sigma_init": self.sigma_init, "max_goals": self.max_goals,
            "team_index": self.team_index,
            "x": self._x.tolist(),          # 长度 2T
            "P": self._P.tolist(),          # (2T)×(2T)
            "cursor": {"last_absorbed_date": self._last_absorbed_date,
                       "last_match_id": self._last_match_id},
        }

    @classmethod
    def from_state_dict(cls, d: dict) -> "BayesianIncrementalFilter": ...
```

### 3.3 存储格式与路径

- **格式**：JSON（与 `bayesian_model_{league}.json` 资产风格一致、便于人工审计）。每联赛约 20 队 → `2T≈40` 维 → `P` 为 40×40=1600 个 float，JSON 体积可控。
- **路径**：`data/shadow/p1b_shadow_{league}_state.json`（与 `data/` 现有知识库/台账目录并列）。
- **幂等**：`schema_version` 不匹配旧 checkpoint 时拒绝加载并从资产重新初始化（安全回退）。

---

## 四、接入点与数据流

### 4.1 资产加载（control 臂）

复用 [prediction_core.py L697-716](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/prediction_core.py#L697-L716) 相同的 `BayesianHierarchicalModel.load` 语义，直接 `load(assets/bayesian_model_{league}.json)`，得到 `teams/mu/home_adv/rho/attack/defense/attack_std/defense_std`。

### 4.2 增量 filter 初始化（treatment 臂）

```python
flt = BayesianIncrementalFilter(model.mu, model.home_adv, model.rho,
                                tau_att=TAU_ATT, tau_def=TAU_DEF)
if checkpoint_exists(league):
    flt = BayesianIncrementalFilter.from_state_dict(load_json(...))
else:
    flt.init_teams(model.teams, model.attack, model.defense,
                   model.attack_std, model.defense_std)
```

### 4.3 概率转序（关键）

- `predict_wdl` 返回 `{'win' 主胜, 'draw' 平, 'lose' 客胜}`。
- `record_experiment` 的 `predictions` 要求 3 维 **`[客胜, 平局, 主胜]` = `[p['lose'], p['draw'], p['win']]`**（对齐 `train_models.compute_rps` 列序）。转序遗漏会导致 shadow 指标全错，是最高风险点。

---

## 五、时序保证（已完赛吸收队列）

设计文档 §3.4 的「严格时序」约束，在滚动 shadow 中通过**进度游标 + 升序吸收**实现：

1. checkpoint 记录 `last_absorbed_date`。
2. 每日读取 `matches` 中「已完赛且含赛果、`match_date > last_absorbed_date`」的比赛，`ORDER BY match_date ASC`。
3. 逐场：先 `predict_wdl`（两臂）→ `record_experiment` 落库 → `treatment.update()` 吸收本场赛果 → 更新 `last_absorbed_date`/`last_match_id`。
4. **禁止未来数据**：增量 filter 只吸收「已完赛」场次；预测严格先于该场 update（与 `bayesian_incremental_ab.py` 的 `evaluate_incremental` 同一顺序）。
5. **幂等双保险**：`record_experiment` 用 `INSERT OR IGNORE`（主键 `experiment_id+unit_key+variant`）+ checkpoint 游标，重复跑不重复吸收、不重复落库。

---

## 六、shadow 落库（复用 P1-D）

直接复用 [ab_test_framework.py `record_experiment`](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/ab_test_framework.py#L113-L134)，不新造表：

```python
record_experiment(
    conn,
    experiment_id=f"p1b_shadow_{league}",
    unit_key=str(match_id),
    predictions={"control": [c_lose, c_draw, c_win],
                 "treatment": [t_lose, t_draw, t_win]},
    served_variant="control",          # 生产现状仍是全量重训
    match_id=str(match_id),
)
```

`analyze(ODDS_DB, experiment_id, min_n)` 复用其两比例 z / McNemar / 95% CI / 四指标聚合。

---

## 七、稳定判定标准（生产切换的前置条件）

shadow 目标是「观察连续后验稳定性」，判据分两层：

### 7.1 指标层（复用四指标门禁容差）

| 指标 | 判据（treatment 对 control） | 容差 |
|------|------------------------------|------|
| RPS | 不劣化 | ≤ +0.002 |
| LogLoss | 不劣化 | ≤ +0.01 |
| Accuracy | 稳定 | ≥ −1.0pp |
| DrawRecall | 不降 | ≥ −1.0pp（样本不足标 SKIP） |

### 7.2 后验层（新增，防发散）

1. **协方差不发散**：`trace(P)` 不超过阈值（如 `trace(P) ≤ k × 2T`，k 取 2~4，需实测标定），防止 τ 过大导致后验方差爆炸。
2. **均值无阶跃**：相邻两轮 `attack/defense` 均值向量差 `‖x_t − x_{t−1}‖_2` 低于阈值（防换帅/伤病造成的异常阶跃未被识别）。
3. **连续 K 轮满足**（如 K=3 个比赛日/周）→ 触发生产切换评估；任一 FAIL → 维持 `control`（全量重训），shadow 不影响生产、随时可停。

---

## 八、实验配置清单

| 配置项 | 取值 |
|--------|------|
| experiment_id | `p1b_shadow_{league}`（每联赛独立） |
| variants | `["control", "treatment"]` |
| 臂定义 | control=全量重训冻结；treatment=EKF 增量 |
| served_variant | `control`（不改生产） |
| unit_key | `match_id` |
| τ 初始值 | 取 `bayesian_incremental_ab.py` 逐联赛标定值（离线对照 5/5 全 PASS 时 τ≈0.01，最终以逐联赛标定为准） |
| 频率 | 每日滚动（对齐 `run_daily_p0e.py`，schtasks 计划任务） |
| 最小样本 | `MIN_GATE_N = 60`/联赛（显著性下限） |
| 稳定判据 | §七 指标层 + 后验层 + 连续 K 轮 |

---

## 九、实施前依赖项（本方案不改代码，列明待实现）

1. `BayesianIncrementalFilter` 增补 `state_dict`/`from_state_dict` + `league` + 进度游标（§三）。
2. 新增 `scripts/run_shadow_incremental.py`：编排 §二数据流，`--init`/`--roll`/`--analyze` 子命令。
3. 计划任务：`schtasks` 每日调用（对齐 P0-E 的 `SoccerModel_P0ELiveTrial`）。
4. 稳定性判定函数：复用 `ab_test_framework.analyze` + 新增后验层检测（§七 7.2）。
5. 队名一致性校验：确保 `BayesianHierarchicalModel.teams`（`normalize_team_name` 归一化）与增量 filter 吸收队列的队名口径一致。

---

## 十、风险与回滚

| # | 风险 | 影响 | 缓释 |
|---|------|------|------|
| 1 | 概率转序错误（§四 4.3） | shadow 指标全错 | 落库前单测断言 `sum==1` 且列序 `[lose,draw,win]` |
| 2 | 后验发散（τ 过大 / P trace 爆炸） | treatment 臂失效 | §七 7.2 trace 阈值 + 均值阶跃检测 |
| 3 | checkpoint 损坏 / schema 不匹配 | 状态无法恢复 | schema_version 校验，失败则从资产重建重跑 |
| 4 | 队名口径不一致 | 冷启动误判新队 | §九 5 队名归一化一致性校验 |
| 5 | 时序泄露（未来赛果污染预测） | 指标虚高 | §五 严格先 predict 后 update + CI 时序不变性测试 |
| 6 | shadow 干扰生产 | 影响线上 | served 恒为 control，shadow 只写 `ab_test_log`，可随时停 |

**回滚**：shadow 全程不影响生产（`served=control`）；判定 FAIL 时维持全量重训，停用每日任务即可。

---

> 关联文档：`docs/bayesian_incremental_design.md`（§四/§4.3）、`scripts/bayesian_incremental.py`、
> `scripts/bayesian_incremental_ab.py`、`scripts/ab_test_framework.py`、`scripts/run_daily_p0e.py`、
> `docs/模型优化评估报告_v2.0.md` §P1-B、`docs/change_log.md` §3.158/§3.159