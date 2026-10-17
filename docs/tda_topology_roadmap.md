# 代数拓扑与同伦论在足球预测中的技术实施方案

> **版本**: v0.2（复盘修订版）  
> **日期**: 2026-10-06  
> **关联**: C-20261006-002/003（T-005 让球双病因修复后，skill≈0，拓扑方法作为"前沿补充"定位）  
> **数据约束**: 现有 odds.db 含 Understat 射门坐标（45.8 万条，10 赛季，1.8 万场，含 x/y/xg/situation）、SofaScore 阵容/球员统计（73.8 万行，含 formation）、伤停、赔率历史。**缺少逐事件传球流与实时位置轨迹**。

---

## 0. 复盘结论（结合全栈框架设计 / change_log / project_memory 三文档，2026-10-06）

启动前对三份核心文档完整复盘后，对本方案的定位与优先级做出以下**修正**：

### 0.1 项目历史的三个强约束事实

1. **市场基准全线告负是项目级结论**：WDL skill -0.129、TG -0.05、比分 -0.13、让球修复后 -0.005，框架文档已将"市场复制"确立为兜底范式。**拓扑特征要证明的不是"比现有模型好一点"，而是"能提供市场赔率未编码的信号"——基准是市场本身**。
2. **"更多特征无效"是反复验证的模式**：change_log 中 T-003/D-011（130→65 维）、D-017（85→60 维，CV 反而 +0.57pp）、衍生特征添加后准确率不升且过拟合等记录，说明本项目特征边际收益早已递减。**拓扑特征若不加约束接入，大概率重蹈覆辙**。
3. **EV 引擎六/七层证伪**（ROI -3.71%~-5.41% 转小注验证）说明：即便概率质量提升也不必然转化为业务价值。拓扑项目验收必须绑定概率指标（RPS/skill），不绑定投注指标。

### 0.2 对原方案的修正

| 原方案内容 | 复盘后修正 |
|------------|-----------|
| P0 目标"让球 RPS -0.005→-0.002" | **改为 kill-criteria 导向**：P0 唯一验收标准=严格时间切分下 RPS 改善 ≥0.005 且拓扑特征 importance ≥1%，达不到即**整体终止，不进 P1**。不预设成功 |
| 拓扑特征 20-50 维直接拼接 | **强制 ≤15 维 + 去相关**：与传统特征 \|r\|>0.8 一律剔除；必须注册 `PRE_MATCH_FEATURE_CATALOG`（D-009）；**射门 PH 是赛后数据，只能用于"下一场"的特征，严禁当场泄漏**——这是 P0 最大工程风险点 |
| P1 传球数据采购 | **冻结**。P0 未过 kill-criteria 前不采购任何数据；P0 通过后先用 StatsBomb 开源约 4 千场试跑 |
| 优先级排序 | **让位于进行中的 shadow**：T-006 v5 比分、TG λ 校准两个 shadow 先收敛；TDA-P0 排在其后，避免多条前沿线并行稀释评估资源 |
| 同伦/函子（原 3.1/3.3） | **降级为纯研究注记**，不进入任何实施排期——缺轨迹数据，且项目历史表明高复杂度方法 ROI 极低 |

### 0.3 最终结论

**TDA 在本项目中的合理定位是"一个有时间盒、有硬性终止条件的廉价证伪实验"，而不是建设计划**：
- 唯一零数据成本的切入点是射门点云 PH（45.8 万条射门，2~3 周可出结论）；
- 但历史证据（特征边际递减 + 市场基准全负 + EV 证伪）表明期望收益为**低到中等**；
- 因此最多批准 Phase 0（≤3 周、≤15 维、硬 kill-criteria），P1/P2 整体冻结直到 P0 拿出反例证据。

以下正文保留为**条件触发式参考**——仅当 P0 通过验收时，相应章节才被激活执行。

---

## 1. 需求分析与目标设定

### 1.1 定位：前沿补充方法，不替代主干

当前主干（WDL→λ→比分/让球/总进球）基于赔率+统计特征，skill 已接近市场平价（T-005 让球 -0.005）。拓扑方法不追求独立预测，而是：
- **特征工程侧**：提取赔率/统计无法编码的"空间结构"信号（射门分布形状、阵型拓扑、高压循环强度）。
- **评估侧**：用拓扑保真度（TFS）衡量预测序列与真实比赛进程的结构相似性，补充 RPS 的逐点误差。
- **可解释侧**：将抽象拓扑量翻译为业务语言（"边路空洞率"、"禁区连通性"），辅助人工审核。

### 1.2 性能提升指标（分优先级）

| 指标 | 当前基线 | P0 目标 | P1 目标 | 验证方式 |
|------|----------|---------|---------|----------|
| 让球 RPS | 0.2163（T-005 D2） | -0.005→**-0.002** | **+0.005**（skill 转正） | market_benchmark_hc 回放 |
| 比分 Top3 精确 | 30.56%（T-006 v4） | 31.5% | 33% | shadow 双算 |
| 战术模式识别准确率 | 无 | 人工标注 100 场，聚类纯度 ≥70% | 对手模板匹配 F1 ≥0.60 | 专家回标 |
| 小样本鲁棒性（新升班马首 5 场） | WDL 用 Poisson 降级 | 拓扑特征覆盖率 100%（不依赖历史） | 升班马预测准确率 +3pp | 分群回测 |
| 预测结构保真度 TFS | 无 | 建立度量基准 | 纳入模型选择门禁 | TopoCast 框架 |

### 1.3 融合策略：三轨并行，拓扑特征作为增量输入

```
传统特征（254/71/374 维） ──┐
                            ├─→ 融合层（concat / attention / GNN） ──→ 泊松/二元/DC
拓扑特征（~20-50 维） ──────┘

评估层：RPS + LogLoss + TFS（多目标）
```

融合接口约束：拓扑特征必须与现有 `feature_utils.build_all_features()` 同 pipeline（`TRAE_HCP_TRAIN_CUTOFF` 截止线、中位数填充、训练/服务同公式）。

---

## 2. 数据预处理方案

### 2.1 现有数据可直接使用的输入（P0）

| 数据源 | 表/文件 | 可用字段 | 预处理动作 |
|--------|---------|----------|------------|
| Understat 射门 | `understat_shots` | x, y, xg, minute, situation, result | 坐标归一化（0-1 pitch）、时间窗分片（15min/半场/全场）、按球队/比赛聚类 |
| 阵容 | `match_lineups` | formation, position, is_starter, minutes_played | 阵型标准化（4-3-3→空间网格）、首发 11 人提取、替补时间加权 |
| 球员统计 | `match_player_stats` | passes, tackles, pressures, carries... | 球队级聚合（sum/mean）、位置级聚合（后卫/中场/前锋）、比赛标准化（per90） |
| 伤停 | `match_missing_players` | reason, position | 关键位置缺失标记（门将/核心前锋）、拓扑网络节点删除模拟 |
| 赔率 | `handicap_history` | hcp_win/draw/lose | 去水概率（1/odds 归一），作为 Hodge 分解边权 |

### 2.2 需新增采集的数据（P1）

| 数据类型 | 来源 | 成本 | 优先级 |
|----------|------|------|--------|
| 逐事件传球流（传球者→接收者、坐标、结果） | StatsBomb / FBref（部分免费）/ 自研采集 | 中（StatsBomb 开源 360 数据集可试用） | P1 |
| 球员实时位置轨迹（frame-level x,y） | StatsBomb 360 / Second Spectrum | 高（商业授权） | P2（先证伪） |
| 防守压力事件（press, counterpress） | StatsBomb / 自研标注 | 中 | P1 |

### 2.3 预处理流程（通用）

```python
def preprocess_shot_cloud(match_id, team_side, window='full'):
    """射门点云预处理：归一化 → 去噪 → 分窗 → 输出 numpy array (n_shots, 3:[x,y,xg])"""
    shots = db.query("""SELECT x, y, xg FROM understat_shots
                        WHERE match_id=? AND team_side=? AND minute BETWEEN ? AND ?""",
                     (match_id, team_side, t0, t1))
    coords = np.array([[s['x']/100.0, s['y']/100.0, s['xg']] for s in shots])
    # 去噪：剔除 xg < 0.01 的远射噪点（可选，影响 H1 环路检测）
    coords = coords[coords[:,2] >= 0.01]
    return coords
```

标准化约定：
- 球场坐标统一为 `[0,1]×[0,1]`，进攻方向向右（x→1 为对方球门）。
- 时间分片：15min 滑动窗口（_overlap=5min_）、半场、全场、"最后 15 分钟"。
- 缺失处理：无射门窗口用空点云（PH 返回空持久图，特征向量全 0）。

---

## 3. 拓扑特征提取模块开发

### 3.1 同伦论应用模块（P2，理论验证期）

**目标**：判断战术路径的连续变形等价性，聚类相似进攻套路。

**现状约束**：缺少逐事件传球轨迹，无法直接计算路径空间 $\pi_1$。

**降级方案（P0 可启动）**：
- 用射门点云的 **Vietoris-Rips 复形**近似：将射门序列视为离散采样，VR 复形的 1-单形（边）连接时空邻近的射门点。
- "进攻套路"定义为 VR 复形的同伦类：两个比赛窗口的 VR 复形若同伦等价（由同调群同构判定），则视为相似套路。
- 实现：`scipy.spatial.distance.pdist` + `gudhi` VR 复形构建 → 比较 Betti 数序列（$\beta_0, \beta_1$）作为相似度代理。

**验收**：人工标注 50 场"重复套路"，Betti 序列相似度阈值 0.8 时，召回 ≥60%、精确 ≥55%。

### 3.2 同调/持续同调模块（P0，立即可行）

**3.2.1 持续同调分析射门点云**

```python
def compute_ph_features(coords, max_edge=0.3):
    """输入: (n,2) 射门坐标; 输出: 拓扑特征向量 (10维)"""
    import gudhi as gd
    # 构建 Rips 复形
    rips = gd.RipsComplex(points=coords, max_edge_length=max_edge)
    simplex_tree = rips.create_simplex_tree(max_dimension=2)
    diag = simplex_tree.persistence()
    # H0: 连通分支（射门点聚集模式）
    h0 = [(b,d) for dim,(b,d) in diag if dim == 0 and d < np.inf]
    # H1: 环路（禁区空洞、射门包围圈）
    h1 = [(b,d) for dim,(b,d) in diag if dim == 1]
    # 特征工程
    features = {
        'ph_h0_count': len(h0),                    # 射门聚集簇数
        'ph_h0_mean_life': np.mean([d-b for b,d in h0]) if h0 else 0,
        'ph_h1_count': len(h1),                    # 环路数（禁区空洞）
        'ph_h1_mean_life': np.mean([d-b for b,d in h1]) if h1 else 0,
        'ph_h1_max_life': max([d-b for b,d in h1]) if h1 else 0,
        'ph_entropy': persistence_entropy(diag),    # 持续图熵（整体复杂度）
        'ph_betti0_curve_area': betti_curve_area(h0, max_edge),
        'ph_betti1_curve_peak': betti_curve_peak(h1, max_edge),
    }
    return features
```

**业务翻译**：
- `ph_h1_count` ↑ → "禁区包围圈多"（对手防线紧凑，射门被迫绕圈）。
- `ph_h1_mean_life` ↑ → "空洞持久"（防线长期存在结构性缺口）。
- `ph_entropy` ↑ → "射门分布杂乱"（缺乏组织）。

**3.2.2 比赛阶段持续同调（时间序列）**

输入：控球率/传球链/压力指数时间序列（当前可用 `sofascore_post_match_cache` 或从 `match_player_stats` 聚合）。

```python
def sliding_window_ph(ts, window=15, step=5, embedding_dim=3, delay=1):
    """Takens 延迟嵌入 + 滑动窗口 PH"""
    from gudhi import TimeSeriesTDA  # 或自研
    embedded = takens_embedding(ts, dim=embedding_dim, delay=delay)
    features = []
    for i in range(0, len(embedded)-window, step):
        w = embedded[i:i+window]
        diag = ripser(w)['dgms']
        features.append(extract_window_features(diag))
    return np.array(features)  # (n_windows, n_features)
```

**相变检测**：窗口级 PH 特征的突变点（Betti 数跳变、持久寿命剧增）标记为"僵局→破门→压上→反击"的拓扑相变。

### 3.3 函子/范畴论应用模块（P1，需阵型图数据）

**3.3.1 阵型图网络空间**

将首发 11 人按 position 映射到球场空间网格（4-3-3 → 4 后卫行、3 中场行、3 前锋行），球员交互（传球、对抗）为边，构建加权图 $G=(V,E,w)$。

**3.3.2 等变/不变特征**

- **镜像不变性**：左右翻转球场，图同构 → 特征不变（阵型对称性）。
- **旋转不变性**：45° 旋转（4-3-3 与 4-5-1 在某些视角下拓扑等价）→ 用图同构检验（`networkx.is_isomorphic`）。

实现：将阵型图编码为 **Graph Neural Network 输入**，通过等变层（EGNN）提取旋转/镜像不变特征。

**验收**：同一阵型不同朝向（主场左攻 vs 右攻）的特征向量余弦相似度 ≥0.95。

---

## 4. 具体应用场景落地

### 4.1 对手风格与联赛拓扑画像系统（P0-P1）

**高阶网络表示**：
- 节点 = 球员（按位置聚合为"后卫群/中场群/前锋群"，降低维度）。
- 边 = 交互强度（传球次数、对抗次数、共同出场时间）。
- 权重 = 标准化交互频率。

**Hodge 分解**：
将边流（edge flow）$f: E \to \mathbb{R}$ 分解为：
$$f = f_{\text{grad}} + f_{\text{curl}} + f_{\text{harm}}$$

- **梯度流**（$f_{\text{grad}}$）：等级差异驱动（强队→弱队的单向压制）。
- **旋度流**（$f_{\text{curl}}$）：循环交互（三角传递、边路循环）。
- **调和流**（$f_{\text{harm}}$）：全局平衡模式。

**联赛风格区分**：
- 英超：高旋度/高梯度（高强度循环+等级分明）。
- 西甲：高旋度/低梯度（技术循环，等级差距小）。
- 德甲：低旋度/高梯度（直接冲击，等级分明）。
- 意甲：低旋度/低梯度（保守均衡）。

**接口**：`build_hodge_features(match_id) → dict(gradient_energy, curl_energy, harmonic_energy, curl_ratio)`，接入 `feature_utils` 作为 4 维增量特征。

### 4.2 比赛阶段与相变检测模块（P0）

**输入**：从 `match_player_stats` 聚合的分钟级时间序列（控球率、射门次数、传球成功率）。

**延迟嵌入 + 滑动窗口 PH**：
- 15min 滑动窗口，3 维 Takens 嵌入（控球率, 传球链长度, 压力指数）。
- 每个窗口输出 Betti 数 + 持久寿命。

**相变识别规则（启发式）**：
| 拓扑信号 | 业务含义 |
|----------|----------|
| H0 聚类数骤减 + H1 寿命峰值 | "僵局打破"（多点分散→单点突破） |
| H1 持续出现 + H0 低 | "围攻阶段"（密集射门，缺乏破局） |
| H0 骤增 + 全维寿命低 | "反击转换"（快速分散推进） |
| H2 出现（罕见） | "立体进攻"（传中+远射+渗透多层叠加） |

**比分演化预测**：将相变时间点作为 Poisson 过程强度 $\lambda(t)$ 的变点（change-point），改进进球时间预测。

### 4.3 战术模式分类与对手模板系统（P1，需传球数据）

**单纯复形构建**：
- 0-单形 = 球员/区域。
- 1-单形 = 传球连接（边）。
- 2-单形 = 三角传递（面，3 人互传形成闭环）。
- 3-单形 = 四维配合（罕见，如菱形中场）。

**持久图编码**：
过滤参数 = 传球距离阈值 $r$。随 $r$ 增大，单纯复形从离散点 → 边 → 面 → 高维单形。持续同调记录各维拓扑特征的出生/死亡时间。

**聚类模型**：
将每场比赛的持久图转化为 **Persistence Image**（256 维），用 UMAP 降维至 10 维，K-Means 聚类为战术模板：
- 模板 A：高 H1（高压逼抢，大量三角传递）。
- 模板 B：低 H1 高 H0（深度防守，长传反击）。
- 模板 C：高 H2（边中结合，立体进攻）。

**对手匹配**：赛前提取对手近 5 场持久图，匹配最相似模板，调整让球/比分预测。

### 4.4 预测质量评估与校准模块——TopoCast 框架（P0）

**拓扑保真度 TFS（Topological Fidelity Score）**：

```python
def compute_tfs(pred_sequence, true_sequence, window=15):
    """pred/true: 时间序列（控球率/传球链/压力指数）"""
    pred_ph = sliding_window_ph(pred_sequence, window)
    true_ph = sliding_window_ph(true_sequence, window)
    # 持久图距离（Wasserstein）
    dist = wasserstein_distance(pred_ph, true_ph)
    # 归一化为相似度
    tfs = 1.0 / (1.0 + dist)
    return tfs
```

**应用**：
- **模型选择**：在验证窗选择 TFS 最高的模型（不只选 RPS 最低）。
- **结构合理性**：检查"比分概率矩阵"是否对应合理的比赛进程（如 2-0 领先后的控球率下降是否在预测序列中体现）。
- **异常检测**：TFS 突降 → 预测结构失真，触发人工审核。

### 4.5 小样本与噪声鲁棒性处理模块（P0）

**TDA 抗扰动**：
- 持续同调天然对噪声鲁棒（小扰动只产生短寿命特征，可被过滤）。
- 设定持久寿命阈值 $\tau$（如 $\tau=0.05$），剔除短寿命噪声特征。

**缺失值处理**：
- 新升班马无历史数据 → 用联赛平均持久图作为先验（Bayesian TDA）。
- 伤停导致阵容不完整 → 从阵型图中删除对应节点，重新计算 PH（网络 resilience 分析）。

**可解释形状特征**：
| 拓扑量 | 业务语言 |
|--------|----------|
| H1 计数 | "高压循环强度" |
| H1 平均寿命 | "防线缺口持久度" |
| H0 计数 | "进攻分散度" |
| 梯度能量 | "等级压制强度" |
| 旋度能量 | "战术循环活跃度" |
| 持久熵 | "比赛混乱指数" |

---

## 5. 建模流程实现

### 5.1 滑动窗口图/单纯复形构建

```python
class SlidingWindowComplexBuilder:
    def __init__(self, window_min=15, step_min=5, max_edge=0.3):
        self.window = window_min
        self.step = step_min
        self.max_edge = max_edge

    def build_rips(self, match_id, team_side, feature_type='shots'):
        """构建滑动窗口 Rips 复形序列"""
        if feature_type == 'shots':
            coords = preprocess_shot_cloud(match_id, team_side, window='full')
        # 时间分片
        windows = self._time_slice(coords, self.window, self.step)
        complexes = []
        for w in windows:
            if len(w) < 3:
                complexes.append(None)  # 点不足，PH 无意义
                continue
            rips = gd.RipsComplex(points=w[:, :2], max_edge_length=self.max_edge)
            st = rips.create_simplex_tree(max_dimension=2)
            complexes.append(st)
        return complexes
```

### 5.2 过滤计算持续同调

```python
def compute_filtration_ph(complexes):
    """输入: Rips 复形序列; 输出: 每窗口持久图 + Betti 曲线"""
    results = []
    for st in complexes:
        if st is None:
            results.append({'betti0': 0, 'betti1': 0, 'entropy': 0})
            continue
        diag = st.persistence()
        h0 = [d for dim, d in diag if dim == 0]
        h1 = [d for dim, d in diag if dim == 1]
        results.append({
            'betti0': len(h0),
            'betti1': len(h1),
            'entropy': persistence_entropy(diag),
            'h0_lifespan': np.mean([d[1]-d[0] for d in h0]) if h0 else 0,
            'h1_lifespan': np.mean([d[1]-d[0] for d in h1]) if h1 else 0,
        })
    return pd.DataFrame(results)
```

### 5.3 特征提取与表示

**输出特征向量（每场比赛，主客各一套，全场/半场/最后 15min 三套）**：

```python
TOPO_FEATURES = [
    # PH 射门特征（主/客/差）
    'ph_h0_count_home', 'ph_h0_count_away', 'ph_h0_diff',
    'ph_h1_count_home', 'ph_h1_count_away', 'ph_h1_diff',
    'ph_h1_mean_life_home', 'ph_h1_mean_life_away',
    'ph_entropy_home', 'ph_entropy_away', 'ph_entropy_diff',
    # Hodge 分解（联赛级，每队）
    'hodge_gradient_energy', 'hodge_curl_energy', 'hodge_harmonic_energy',
    'hodge_curl_ratio',
    # 相变检测
    'phase_transition_count', 'phase_dominant_type',
    # TFS（与预测相关的评估特征，训练时可用历史序列近似）
    'tfs_approx',  # 用历史比赛序列自相似度代理
]
# 约 20-25 维（取决于时间窗口数）
```

### 5.4 与传统特征融合

**融合策略 A：直接拼接（P0 最快）**
```python
X_fused = np.hstack([X_traditional, X_topo])  # 254+25=279 维
```

**融合策略 B：Attention 门控（P1，需验证）**
```python
# 拓扑特征先过小型 MLP，输出注意力权重
 topo_gate = MLP(X_topo)  # (batch, 1)
 X_weighted = X_traditional * topo_gate + X_topo * (1 - topo_gate)
```

**融合策略 C：GNN 异质图（P1，需阵容传球数据）**
- 节点：球员（属性 = 传统特征子集）。
- 边：传球/对抗（权重 = 拓扑交互强度）。
- 图级读出：sum/mean pooling → 与传统特征拼接。

### 5.5 模型训练

**基线**：拓扑特征 + LightGBM（与现有 T-005 同超参，公平对比）。
**进阶**：拓扑特征 + GNN（DGL/PyG）→ 图级预测 → 泊松强度 $\lambda$。

**损失函数**：
```python
loss = L_rps + alpha * L_tfs  # alpha=0.1 起步，调参
```

---

## 6. 验证与优化方案

### 6.1 TFS 预测结构检查

```python
def tfs_gate(pred_df, true_df, threshold=0.6):
    """TFS 低于阈值时，模型预测降级为市场复制"""
    tfs_scores = pred_df.apply(lambda row: compute_tfs(row['pred_seq'], row['true_seq']), axis=1)
    pred_df['tfs'] = tfs_scores
    pred_df['is_structural'] = pred_df['tfs'] >= threshold
    return pred_df
```

### 6.2 回测与校准

- **时间切分**：严格 <2026-08-22 训练，03-01~08-21 验证，08-22~09-21 测试（与 T-005 修复同协议）。
- **校准**：Platt/Isotonic 对拓扑-enhanced 模型概率输出校准（沿用 C-20261006-002 经验）。
- **过拟合防范**：
  - 拓扑特征维度控制在 ≤30（现有 254 维已高，避免维度灾难）。
  - 交叉验证：5-fold 时间序列 CV（避免数据泄漏）。
  - 特征重要性监控：若拓扑特征 importance < 1%，则剔除（避免无效复杂度）。

### 6.3 计算成本优化

| 优化项 | 策略 | 效果 |
|--------|------|------|
| 数据抽样 | 每场比赛射门点云 ≥20 点时子采样至 50 点（均匀空间抽样） | PH 计算 O(n²)→O(2500)，可忽略 |
| 近似算法 | `ripser`（C++ 实现，比 gudhi 快 10-100 倍） | 单场 PH < 10ms |
| 特征降维 | Persistence Image 256 维 → UMAP 10 维 | 聚类/可视化友好 |
| 缓存 | 比赛级拓扑特征持久化 `odds.db.topo_features` | 避免重复计算 |
| 批量计算 | 滑动窗口 PH 用 Numba/Cython 并行 | 全库 1.8 万场 < 30 分钟 |

### 6.4 业务语言转换机制

```python
TOPO_LABELS = {
    'ph_h1_count': '高压循环强度',
    'ph_h1_mean_life': '防线缺口持久度',
    'ph_h0_count': '进攻分散度',
    'ph_entropy': '比赛混乱指数',
    'hodge_curl_energy': '战术循环活跃度',
    'hodge_gradient_energy': '等级压制强度',
    'phase_transition_count': '比赛节奏变化次数',
}

def explain_topo_features(match_id):
    feats = load_topo_features(match_id)
    return {TOPO_LABELS[k]: f"{v:.2f} ({_interpret(v, k)})" for k, v in feats.items()}
```

---

## 7. 集成与部署建议

### 7.1 混合策略

```
Layer 0: 数据采集（赔率 + 统计 + 拓扑）
Layer 1: 传统特征工程（254 维 WDL / 71 维 HC / 374 维 TG）
Layer 2: 拓扑特征工程（20-25 维，增量）
Layer 3: 融合层（concat / attention / GNN）
Layer 4: 任务头
  - WDL: Stacking 5 基模 + LR meta
  - HC: 两阶段（draw_model + market_dir D2）+ 拓扑增量
  - TG: Poisson/DC + 拓扑变点
  - Score: λ → Poisson + 拓扑结构约束
Layer 5: 评估（RPS + TFS + CLV）
```

### 7.2 硬件/软件要求

- **现有**：Python 3.10+, LightGBM, SQLite, NumPy/Pandas。
- **新增**：
  - `ripser`（C++ 扩展，pip installable）：持续同调计算。
  - `gudhi`（可选，ripser 备份）：复杂过滤场景。
  - `networkx` + `scipy`：Hodge 分解。
  - `umap-learn`：持久图降维。
  - `torch-geometric`（P1）：GNN 异质图。
- **硬件**：现有服务器即可（PH 计算 CPU 并行，无 GPU 需求；GNN 训练时需 GPU，可用云端 A100 按需）。

### 7.3 更新与维护机制

- **拓扑特征缓存表**：`odds.db.topo_features`（match_id, team_side, window, feature_json, computed_at）。
- **增量更新**：新比赛入库后，触发拓扑特征计算（异步任务，与现有 `sofascore_post_match_cache` 更新同 pipeline）。
- **漂移监控**：每月检查联赛级 Hodge 特征分布（KL 散度 vs 历史），若漂移 >0.1 则重新拟合联赛画像。
- **版本控制**：拓扑特征提取脚本版本化（`topo_features_v1.py`），与 `feature_utils` 版本对齐。

---

## 8. 参考实现

### 8.1 Hodge 分解用于联赛风格（arXiv:2606.21814）

已实现思路（见 4.1）：将球队交互网络边流分解为梯度/旋度/调和分量，用旋度能量区分联赛风格。

**差异点**：原文用传球网络（高阶数据），我们用赔率去水概率 + 球员统计聚合作为边权代理。**精度降级但立即可行**。

### 8.2 TDA 用于网球预测（arXiv:2607.23509）

**可移植部分**：比赛网络构建（节点=球员状态，边=交互）、持久图编码、持久图像分类。

**需调整**：网球是点对点交互，足球是 11v11 团队运动，需增加"空间嵌入"维度（x,y 坐标）。

### 8.3 TopoCast 框架（arXiv:2606.25439）

**核心移植**：`compute_tfs()` 函数（见 4.4），用 Wasserstein 距离比较预测与真实序列的持久图。

**扩展**：不仅用于评估，还可作为训练损失正则项（$L_{\text{total}} = L_{\text{RPS}} + \lambda L_{\text{TFS}}$）。

### 8.4 足球预测基线融合（抖音/arXiv:2608.05030）

**已有基线**：本项目 WDL→λ→比分/让球/总进球链路已覆盖"攻防强度→Lambda→比分概率矩阵"。

**拓扑增量**：在 $\lambda$ 计算中引入拓扑相变变点（见 4.2），改进进球时间预测；在比分概率矩阵中引入禁区空洞率（H1 寿命）调整低比分概率。

---

## 9. 风险评估与应对策略

| 风险 | 等级 | 影响 | 应对措施 |
|------|------|------|----------|
| **计算复杂度过高** | 中 | PH 计算拖慢 serving | ① ripser 替代 gudhi；② 特征缓存；③ 抽样降点 |
| **特征解释性差** | 中 | 业务方无法理解 | ① 强制业务标签映射（见 6.4）；② SHAP 解释拓扑特征贡献 |
| **数据适应性差** | 高 | 缺少事件流，P1 模块无法落地 | ① P0 先用射门/阵容数据验证价值；② P1 采购 StatsBomb 开源数据（360 数据集免费）；③ P2 理论验证后再投入 |
| **过拟合** | 中 | 高维拓扑特征在低样本联赛过拟合 | ① 维度 cap 30；② 联赛级正则化（共享联赛先验）；③ 特征重要性 <1% 自动剔除 |
| **与传统特征重叠** | 低 | PH 特征与 xG/射门位置统计相关 | ① 相关性分析剔除冗余（|r|>0.8）；② 用 residual（PH 去线性回归后的残差）作为纯拓扑信号 |
| **维护成本高** | 低 | TDA 库更新/兼容性问题 | ① 封装 `topo_core.py` 抽象层；② 锁定 ripser==0.6.4 版本 |

---

## 10. 阶段性实施计划

### Phase 0：基础设施与 P0 验证（2-3 周）

| 周 | 任务 | 交付物 | 验收标准 |
|----|------|--------|----------|
| W1 | ① 安装 ripser/gudhi/networkx；② 构建 `topo_core.py`（PH 计算、Hodge 分解、TFS 基础函数）；③ 射门点云预处理 pipeline | `scripts/topo_core.py` + `tests/test_topo_core.py` | 单场 PH 计算 < 10ms；100 场测试 0 报错 |
| W1-2 | ④ 提取全场射门 PH 特征（H0/H1/熵）；⑤ 与传统特征相关性分析；⑥ 接入 `feature_utils` 增量接口 | `scripts/topo_features_v1.py` + `odds.db.topo_features` 表 | 特征维度 ≤15；与现有特征 |r|<0.8；覆盖率 100%（有 Understat 数据的比赛） |
| W2-3 | ⑦ LightGBM 基线：传统 71 维 + 拓扑 15 维，让球任务；⑧ 时间切分训练/验证/测试；⑨ 与 T-005 D2 对比 | `reports/topo_baseline_v1.md` | RPS 改善 ≥0.003（skill 提升 ≥0.01）或明确证伪（拓扑特征 importance <1%） |
| W3 | ⑩ Hodge 分解（联赛级）实现；⑪ 联赛画像可视化 | `scripts/hodge_league_profile.py` + `reports/hodge_league_v1.md` | 五大联赛旋度/梯度能量区分度 ANOVA p<0.05 |

### Phase 1：P1 模块与深度集成（4-6 周，需新数据）

| 周 | 任务 | 交付物 | 验收标准 |
|----|------|--------|----------|
| W4 | ① 采购/爬取 StatsBomb 360 开源数据（或 FBref 传球统计）；② 构建传球网络单纯复形 | `data/statsbomb_events/` + `scripts/topo_pass_network.py` | 传球数据覆盖 ≥1000 场；单纯复形构建 0 报错 |
| W4-5 | ③ 持续同调分析比赛阶段（滑动窗口 PH）；④ 相变检测规则调优 | `scripts/topo_phase_transition.py` | 人工标注 50 场相变，检测 F1 ≥0.55 |
| W5-6 | ⑤ 战术模式聚类（Persistence Image + UMAP + K-Means）；⑥ 对手模板匹配系统 | `scripts/topo_tactic_template.py` | 聚类纯度 ≥70%；模板与人工标签 Cohen's κ ≥0.50 |
| W6-7 | ⑦ 拓扑特征 + GNN 融合（DGL 异质图）；⑧ 比分/让球联合训练 | `scripts/topo_gnn_model.py` | GNN 验证 RPS 优于 LightGBM 基线 ≥0.005 |
| W7-8 | ⑨ TopoCast TFS 集成到评估 pipeline；⑩ 多目标训练（RPS + TFS） | `scripts/topo_cast_eval.py` | TFS 与 RPS 排名相关性 Kendall τ ≥0.30（正相关） |

### Phase 2：生产化与前沿探索（4-6 周）

| 周 | 任务 | 交付物 | 验收标准 |
|----|------|--------|----------|
| W9-10 | ① 拓扑特征异步计算 pipeline（赛后自动触发）；② 特征漂移监控；③ 业务标签解释面板 | `scripts/topo_pipeline.py` + 监控看板 | 新比赛 topo_features 入库延迟 < 1 小时；漂移告警准确率 ≥80% |
| W10-12 | ④ 同伦/函子模块原型（理论验证）；⑤ 等变 GNN 实验；⑥ 范畴论应用（若数据支持） | `scripts/topo_category_theory.py`（实验） | 原型跑通；发表论文/技术博客（可选） |
| W12-14 | ⑦ A/B 测试：拓扑增强模型 vs 基线（统一报告轨）；⑧ 回测与校准；⑨ 最终上线决策 | `reports/topo_ab_test_v1.md` | A/B 测试样本 ≥500 场；拓扑增强组 RPS 显著优于基线（p<0.05）或明确下线 |

---

## 附录 A：现有数据可用性清单

| 数据 | 表 | 记录数 | 时间跨度 | 拓扑可用性 |
|------|-----|--------|----------|------------|
| 射门坐标 | `understat_shots` | 458,347 | 2016-2026 | **P0 核心**（点云 PH） |
| 球员统计 | `match_player_stats` | 738,111 | 2016-2026 | **P0**（Hodge 边权代理） |
| 阵容 | `match_lineups` | 738,135 | 2016-2026 | **P0**（阵型图节点） |
| 赔率 | `handicap_history` | ~13,125 | 2024-2026 | **P0**（Hodge 边权） |
| 伤停 | `match_missing_players` | 少量 | 2024-2026 | **P0**（网络 resilience） |
| 赛后统计 | `sofascore_post_match_cache` | 变化 | 2024-2026 | **P0**（时间序列 PH） |
| **逐事件传球** | **无** | **0** | **—** | **P1 需采购** |
| **实时位置轨迹** | **无** | **0** | **—** | **P2 需商业授权** |

## 附录 B：依赖库与安装

```bash
pip install ripser==0.6.4 gudhi networkx umap-learn scikit-learn
# P1 可选
pip install torch-geometric dgl
```

## 附录 C：术语对照表

| 数学术语 | 业务术语 | 足球含义 |
|----------|----------|----------|
| 持续同调 (Persistent Homology) | 拓扑指纹 | 射门/传球分布的多尺度形状特征 |
| Betti 数 | 拓扑计数 | H0=聚集簇数, H1=环路/空洞数 |
| 持久图 (Persistence Diagram) | 寿命谱 | 拓扑特征的出生-死亡时间分布 |
| Hodge 分解 | 流分解 | 球队交互的梯度(等级)+旋度(循环)+调和(平衡) |
| 等变 (Equivariant) | 对称不变 | 阵型旋转/镜像后特征不变 |
| Vietoris-Rips 复形 | 邻近网络 | 距离 < r 的点/边/面连接结构 |

---

> **下一步行动**: 如需启动 Phase 0，请确认：①是否接受先以射门点云 PH 为切入点（立即可行）；②是否采购 StatsBomb 360 数据（P1 前提）；③Phase 0 验证周期（建议 2-3 周）。
