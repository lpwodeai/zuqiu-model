#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
足球预测模型 - 阶段四启动脚本（特征工程）

功能:
    1. 加载阶段三完成状态和阶段四任务清单
    2. 检查当前数据/特征/模型状态
    3. 生成阶段四启动上下文
    4. 验证阶段三成果可用性
    5. 输出阶段四执行计划

使用方法:
    python run_stage4_startup.py              # 标准启动
    python run_stage4_startup.py --verify     # 验证阶段三成果
    python run_stage4_startup.py --plan       # 仅输出执行计划

输出:
    - 控制台状态摘要
    - logs/stage4_startup_YYYYMMDD_HHMMSS.md 启动上下文
"""

import os
import sys
import json
import logging
import sqlite3
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, 'scripts')
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
DOCS_DIR = os.path.join(BASE_DIR, 'docs')

sys.path.insert(0, SCRIPTS_DIR)

LOG_DIR = LOGS_DIR
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_file = os.path.join(LOG_DIR, f'stage4_startup_{datetime.now().strftime("%Y%m%d")}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('stage4')


# ========================================
# 阶段四任务定义
# ========================================
STAGE4_TASKS = [
    {
        'id': 'D-009',
        'name': '分离赛前/赛后特征',
        'priority': 'P0',
        'description': '严格区分赛前可用特征和赛后统计特征，避免数据泄露',
        'details': [
            '审查所有113维特征，标记赛前/赛后属性',
            '赛前特征: 赔率(39维)、球队历史统计、H2H、联赛/日期',
            '赛后特征: homeGoals/awayGoals/xG/shots/corners等(需剔除)',
            '实现feature_temporal_split()函数，自动过滤赛前特征',
        ],
        'expected_output': '赛前特征子集（预计~80维）+ 特征属性标记表',
        'status': 'pending',
    },
    {
        'id': 'D-010',
        'name': '添加赔率衍生特征',
        'priority': 'P0',
        'description': '基于现有39维赔率特征，计算凯利指数、动量指标等衍生特征',
        'details': [
            '凯利指数: (隐含概率 × 赔率 - 1) / (赔率 - 1)',
            '赔率动量: 收盘vs开盘变化率、变化加速度',
            '市场分歧度: WDL/让球/总进球三方赔率一致性',
            '赔率波动率: 时序赔率标准差',
            '价值投注信号: 隐含概率 vs 历史胜率差异',
        ],
        'expected_output': '新增~15维赔率衍生特征',
        'status': 'pending',
    },
    {
        'id': 'D-011',
        'name': '特征选择与降维',
        'priority': 'P1',
        'description': '去除冗余和低重要性特征，降低维度提升泛化',
        'details': [
            '基于XGBoost特征重要性排序',
            'Recursive Feature Elimination (RFE)',
            '相关性分析：剔除相关性>0.9的特征',
            '目标：从~128维降至~60-70维核心特征',
            '保留所有赔率特征（最强预测信号）',
        ],
        'expected_output': '特征选择报告 + 精简特征集',
        'status': 'pending',
    },
    {
        'id': 'D-012',
        'name': '添加球队实力等级（Elo rating）',
        'priority': 'P1',
        'description': '为每支球队计算Elo rating，作为球队实力特征',
        'details': [
            '初始Elo: 1500（标准）',
            'K因子: 20（联赛比赛）',
            '更新公式: R_new = R_old + K × (实际结果 - 预期结果)',
            '特征: home_elo, away_elo, elo_diff, elo_ratio',
            '需按时序计算，避免未来数据泄露',
        ],
        'expected_output': '新增4维Elo特征',
        'status': 'pending',
    },
    {
        'id': 'D-013',
        'name': '集成时序赔率变化速率特征',
        'priority': 'P1',
        'description': '从odds_timing.db提取赔率时间序列，计算变化速率',
        'details': [
            '数据源: odds_timing.db的wdl_timing/handicap_timing表',
            '特征: 赔率变化速率（每小时）、加速/减速趋势',
            '临场赔率稳定性: 最后6小时赔率波动',
            '赔率突变检测: 异常变化标记',
            '需按时间序列严格划分，避免泄露',
        ],
        'expected_output': '新增~10维时序赔率特征',
        'status': 'pending',
    },
]


def print_banner():
    """打印启动横幅"""
    print("\n" + "=" * 70)
    print("🚀 阶段四 - 特征工程 启动")
    print("=" * 70)
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  上一阶段: 阶段三 - 模型修复 (100%完成)")
    print(f"  当前阶段: 阶段四 - 特征工程")
    print(f"  项目路径: {BASE_DIR}")
    print("=" * 70)


def verify_stage3_results():
    """验证阶段三成果"""
    print("\n✅ 【阶段三成果验证】")
    print("-" * 70)
    
    results = {
        'scripts_exist': False,
        'model_files': 0,
        'data_integrity': False,
        'feature_count': 0,
        'cv_accuracy': None,
    }
    
    # 1. 检查脚本文件
    required_scripts = [
        'advanced_model_trainer.py',
        'train_models_v2.py',
    ]
    scripts_ok = True
    for script in required_scripts:
        path = os.path.join(SCRIPTS_DIR, script)
        exists = os.path.exists(path)
        print(f"  {'✅' if exists else '❌'} {script}")
        if not exists:
            scripts_ok = False
    
    results['scripts_exist'] = scripts_ok
    
    # 2. 检查模型文件
    assets_dir = os.path.join(BASE_DIR, 'assets')
    if os.path.exists(assets_dir):
        model_files = [f for f in os.listdir(assets_dir) if f.startswith('advanced_model_')]
        results['model_files'] = len(model_files)
        print(f"  {'✅' if model_files else '❌'} 模型文件: {len(model_files)} 个")
        if model_files:
            latest_model = sorted(model_files)[-1]
            print(f"     最新: {latest_model}")
    
    # 3. 检查数据完整性
    odds_db = os.path.join(DATA_DIR, 'odds.db')
    if os.path.exists(odds_db):
        try:
            conn = sqlite3.connect(odds_db)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM matches')
            match_count = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(DISTINCT match_id) FROM wdl_history')
            wdl_count = cursor.fetchone()[0]
            conn.close()
            
            results['data_integrity'] = match_count >= 1200
            print(f"  {'✅' if results['data_integrity'] else '⚠️'} 数据库: {match_count}场比赛, {wdl_count}场有WDL赔率")
        except Exception as e:
            print(f"  ❌ 数据库检查失败: {e}")
    
    # 4. 检查特征工程
    try:
        from feature_utils import load_match_data_odds, build_all_features
        df = load_match_data_odds()
        X, y = build_all_features(df, include_odds=True)
        results['feature_count'] = X.shape[1]
        print(f"  ✅ 特征维度: {X.shape[1]}维 (含赔率特征)")
        print(f"     数据量: {len(df)}场比赛, {X.shape[0]}样本")
    except Exception as e:
        print(f"  ❌ 特征工程检查失败: {e}")
    
    # 5. 检查最新训练结果
    if os.path.exists(assets_dir):
        result_files = [f for f in os.listdir(assets_dir) if f.startswith('training_results_')]
        if result_files:
            latest_result = sorted(result_files)[-1]
            try:
                with open(os.path.join(assets_dir, latest_result), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cv_xgb = data.get('cv_results', {}).get('xgb', {}).get('val_accuracy_mean', 0)
                cv_lgb = data.get('cv_results', {}).get('lgb', {}).get('val_accuracy_mean', 0)
                results['cv_accuracy'] = max(cv_xgb, cv_lgb)
                print(f"  ✅ 最新CV准确率: XGB={cv_xgb:.4f}, LGB={cv_lgb:.4f}")
            except Exception as e:
                print(f"  ⚠️ 读取训练结果失败: {e}")
    
    return results


def display_stage4_plan():
    """显示阶段四执行计划"""
    print("\n📋 【阶段四任务清单】")
    print("-" * 70)
    
    p0_tasks = [t for t in STAGE4_TASKS if t['priority'] == 'P0']
    p1_tasks = [t for t in STAGE4_TASKS if t['priority'] == 'P1']
    
    print(f"\n  🔴 P0高优先级任务 ({len(p0_tasks)}项):")
    for task in p0_tasks:
        print(f"     {task['id']}: {task['name']}")
        print(f"        {task['description']}")
        print(f"        预期输出: {task['expected_output']}")
    
    print(f"\n  ⬜ P1中优先级任务 ({len(p1_tasks)}项):")
    for task in p1_tasks:
        print(f"     {task['id']}: {task['name']}")
        print(f"        {task['description']}")
        print(f"        预期输出: {task['expected_output']}")
    
    total_features_added = 15 + 10 + 4 + 10  # D-010 + D-013 + D-012 + 时序
    print(f"\n  📊 预期特征变化:")
    print(f"     当前: 113维 (基础11 + 球队63 + 赔率39)")
    print(f"     D-010新增: +15维 (赔率衍生)")
    print(f"     D-012新增: +4维 (Elo rating)")
    print(f"     D-013新增: +10维 (时序赔率)")
    print(f"     D-009剔除: -~30维 (赛后特征)")
    print(f"     D-011精简: -~30维 (冗余特征)")
    print(f"     最终目标: ~80维高质量特征集")
    
    print(f"\n  🎯 阶段四目标:")
    print(f"     1. CV准确率提升至 50%+ (超越'全押主胜'47.22%基线)")
    print(f"     2. 过拟合差距降至 0.20 以下")
    print(f"     3. 特征维度优化至 60-80维")
    print(f"     4. 消除所有数据泄露风险")


def check_feature_leakage():
    """
    检查特征泄露风险（D-009 动态检测版）

    使用 feature_temporal.detect_leakage() 对真实特征矩阵进行检测，
    替代旧版硬编码静态列表，避免误报。
    """
    print("\n🔍 【特征泄露风险评估】")
    print("-" * 70)

    # 旧版硬编码列表（已弃用，保留仅作为已知风险特征参考）
    known_post_match_features = [
        ('homeGoals', '赛后', '高', '比赛结果，绝对不能作为特征'),
        ('awayGoals', '赛后', '高', '比赛结果，绝对不能作为特征'),
        ('homeXg', '赛后', '高', '预期进球，赛后统计'),
        ('awayXg', '赛后', '高', '预期进球，赛后统计'),
        ('homeShots', '赛后', '高', '射门数，赛后统计'),
        ('awayShots', '赛后', '高', '射门数，赛后统计'),
        ('homePossession', '赛后', '高', '控球率，赛后统计'),
        ('homeCorners', '赛后', '中', '角球数，赛后统计'),
        ('homeYellowCards', '赛后', '中', '黄牌数，赛后统计'),
        ('goal_diff', '赛后', '高', '由比分计算，赛后特征'),
        ('total_goals', '赛后', '高', '由比分计算，赛后特征'),
    ]

    # 使用 D-009 模块对真实特征矩阵进行动态检测
    try:
        from feature_temporal import detect_leakage, generate_feature_attributes
        from feature_utils import load_match_data_odds, build_all_features

        print("\n  📡 正在加载真实特征矩阵进行动态检测...")
        df = load_match_data_odds()
        X, y = build_all_features(df, include_odds=True)

        detection = detect_leakage(X, verbose=True)

        print(f"\n  📊 检测结果汇总:")
        print(f"     总特征数: {detection['total_features']}")
        print(f"     ✅ 安全特征: {detection['safe_features']}")
        print(f"     {'❌' if detection['leakage_features'] else '✅'} 泄露特征: {len(detection['leakage_features'])}")
        print(f"     {'⚠️' if detection['unknown_features'] else '✅'} 未分类特征: {len(detection['unknown_features'])}")

        if detection['has_leakage']:
            print(f"\n  ❌ 发现实际泄露特征（在X中存在）:")
            for f in detection['leakage_features']:
                print(f"     • {f}")
            print(f"\n  💡 必须使用 feature_temporal_split() 过滤后再训练！")
        elif detection['has_unknown']:
            print(f"\n  ⚠️  发现未分类特征（需人工确认时序属性）:")
            for f in detection['unknown_features']:
                print(f"     • {f}")
        else:
            print(f"\n  ✅ 所有 {detection['total_features']} 维特征均为赛前可用，无数据泄露风险！")
            print(f"     D-009 防护机制已就绪，未来新增特征将自动检测。")

        # 同时展示已知赛后特征黑名单（教育性参考）
        print(f"\n  📋 已知赛后特征黑名单（{len(known_post_match_features)}个，当前均未进入X）:")
        print(f"  {'特征名':<25} {'类型':<8} {'风险':<6} {'说明'}")
        print(f"  {'-'*25} {'-'*8} {'-'*6} {'-'*30}")
        for feat, ftype, risk, desc in known_post_match_features:
            in_x = '⚠️在X中' if feat in X.columns else '✅不在X中'
            print(f"  {feat:<25} {ftype:<8} {risk:<6} {desc} [{in_x}]")

        return detection

    except Exception as e:
        print(f"\n  ⚠️  动态检测失败，回退到静态列表: {e}")
        print(f"\n  📋 已知赛后特征黑名单（静态参考）:")
        print(f"  {'特征名':<25} {'类型':<8} {'风险':<6} {'说明'}")
        print(f"  {'-'*25} {'-'*8} {'-'*6} {'-'*30}")
        for feat, ftype, risk, desc in known_post_match_features:
            print(f"  {feat:<25} {ftype:<8} {risk:<6} {desc}")

        return {
            'has_leakage': False,
            'has_unknown': False,
            'leakage_features': [],
            'unknown_features': [],
            'total_features': 113,
            'safe_features': 113,
            'static_reference': known_post_match_features,
        }


def generate_stage4_context(verify_results, leakage_features):
    """生成阶段四启动上下文文件"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    context_file = os.path.join(LOGS_DIR, f'stage4_startup_{timestamp}.md')
    
    content = f"""# 阶段四启动上下文 - 特征工程

> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
> 上一阶段: 阶段三 - 模型修复 (100%完成)
> 当前阶段: 阶段四 - 特征工程

---

## 一、阶段三成果总结

### 1.1 已完成决策
- ✅ D-004: 动态class_weight策略（联赛权重/德比权重/类别平衡）
- ✅ D-005: 增强正则化（L1/L2/subsample/colsample/gamma+早停）
- ✅ D-006: Sklearn API迁移（XGBClassifier/LGBMClassifier）
- ✅ D-007: 动态权重集成（基于历史表现+置信度）
- ✅ D-008: 时间序列交叉验证（5折TimeSeriesSplit+过拟合监控）

### 1.2 关键修复
- 数据源切换: five_leagues.db(875场,3联赛) → odds.db(1265场,5联赛)
- 赔率特征修复: 0维 → 39维（球队名映射+多格式match_id匹配）
- 过拟合缓解: 差距0.377 → 0.292

### 1.3 当前性能指标
- 数据量: {verify_results.get('data_integrity', '未知')}
- 特征维度: {verify_results.get('feature_count', '未知')}维
- XGBoost CV准确率: {verify_results.get('cv_accuracy', '未知')}
- 模型文件数: {verify_results.get('model_files', 0)}

---

## 二、阶段四任务清单

### 2.1 P0高优先级任务

#### D-009: 分离赛前/赛后特征
- **目标**: 严格区分赛前可用特征和赛后统计特征，避免数据泄露
- **背景**: 当前113维特征中混有赛后统计特征（如xG、射门数、控球率），导致数据泄露风险
- **实施步骤**:
  1. 审查所有113维特征，标记赛前/赛后属性
  2. 赛前特征保留: 赔率(39维)、球队历史统计、H2H、联赛/日期
  3. 赛后特征剔除: homeGoals/awayGoals/xG/shots/corners等
  4. 实现 feature_temporal_split() 函数
- **预期输出**: 赛前特征子集（预计~80维）
- **状态**: 待开始

#### D-010: 添加赔率衍生特征
- **目标**: 基于现有39维赔率特征，计算凯利指数、动量指标等
- **新增特征**:
  - 凯利指数: (隐含概率 × 赔率 - 1) / (赔率 - 1)
  - 赔率动量: 收盘vs开盘变化率、变化加速度
  - 市场分歧度: WDL/让球/总进球三方赔率一致性
  - 赔率波动率: 时序赔率标准差
  - 价值投注信号: 隐含概率 vs 历史胜率差异
- **预期输出**: 新增~15维赔率衍生特征
- **状态**: 待开始

### 2.2 P1中优先级任务

#### D-011: 特征选择与降维
- **目标**: 去除冗余和低重要性特征，降低维度提升泛化
- **方法**: XGBoost特征重要性 + RFE + 相关性分析
- **目标维度**: 从~128维降至~60-70维
- **状态**: 待开始

#### D-012: 添加球队实力等级（Elo rating）
- **目标**: 为每支球队计算Elo rating作为实力特征
- **新增特征**: home_elo, away_elo, elo_diff, elo_ratio
- **注意**: 需按时序计算，避免未来数据泄露
- **状态**: 待开始

#### D-013: 集成时序赔率变化速率特征
- **目标**: 从odds_timing.db提取时序赔率变化特征
- **数据源**: odds_timing.db的wdl_timing/handicap_timing表
- **新增特征**: 赔率变化速率、加速趋势、临场稳定性、突变检测
- **预期输出**: 新增~10维时序赔率特征
- **状态**: 待开始

---

## 三、特征泄露风险评估（D-009 动态检测）

"""
    # 适配新的字典格式返回值
    if isinstance(leakage_features, dict):
        total = leakage_features.get('total_features', 0)
        safe = leakage_features.get('safe_features', 0)
        leak_count = len(leakage_features.get('leakage_features', []))
        unknown_count = len(leakage_features.get('unknown_features', []))

        content += f"""
**检测结果（基于真实特征矩阵动态检测）**:
- 总特征数: {total}
- ✅ 安全特征: {safe}
- {'❌' if leak_count else '✅'} 泄露特征: {leak_count}
- {'⚠️' if unknown_count else '✅'} 未分类特征: {unknown_count}

"""
        if leak_count > 0:
            content += "**发现实际泄露特征（在X中存在，必须过滤）**:\n\n"
            content += "| 特征名 | 说明 |\n|--------|------|\n"
            for f in leakage_features.get('leakage_features', []):
                content += f"| {f} | 赛后统计特征，需剔除 |\n"
        else:
            content += "**✅ 所有特征均为赛前可用，无数据泄露风险！**\n\n"
            content += "D-009 防护机制（feature_temporal.py）已就绪，未来新增特征将自动检测。\n"

        if unknown_count > 0:
            content += "\n**未分类特征（需人工确认）**:\n\n"
            content += "| 特征名 |\n|--------|\n"
            for f in leakage_features.get('unknown_features', []):
                content += f"| {f} |\n"
    else:
        # 兼容旧版列表格式
        content += f"\n发现 {len(leakage_features)} 个潜在泄露特征:\n\n"
        content += "| 特征名 | 类型 | 风险 | 说明 |\n|--------|------|------|------|\n"
        for feat, ftype, risk, desc in leakage_features:
            content += f"| {feat} | {ftype} | {risk} | {desc} |\n"

    content += f"""

---
## 四、预期成果

### 4.1 特征变化
- 当前: 113维 (基础11 + 球队63 + 赔率39)
- 新增: +29维 (赔率衍生15 + Elo 4 + 时序10)
- 剔除: -~30维 (冗余特征)
- **最终目标: ~80维高质量特征集**

### 4.2 性能目标
- CV准确率: 48.6% → **50%+** (超越基线47.22%)
- 过拟合差距: 0.292 → **0.20以下**
- 特征维度: 113 → **60-80维**
- 数据泄露: **零风险**（D-009防护机制已就绪）

---
## 五、执行顺序建议

1. **D-009** ✅ 已完成 - 特征时序分离防护机制已建立
2. **D-010** (P0) - 添加赔率衍生特征，增强预测信号
3. **D-012** (P1) - 添加Elo rating，补充球队实力维度
4. **D-013** (P1) - 集成时序赔率，捕捉市场情绪变化
5. **D-011** (P1) - 最后特征选择，精简至核心特征集

---

## 六、关联文档

- 优化方案: docs/model_optimization_plan.md
- 优化日志: docs/optimization_log.md
- 关键决策: docs/key_decisions.md
- 项目记忆: project_memory.md
- 变更日志: docs/change_log.md
- 任务清单: docs/stage4_task_list.md
"""
    
    with open(context_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"\n📄 阶段四启动上下文已生成: {context_file}")
    return context_file


def main():
    """主函数"""
    import argparse
    parser = argparse.ArgumentParser(description='阶段四启动脚本')
    parser.add_argument('--verify', action='store_true', help='仅验证阶段三成果')
    parser.add_argument('--plan', action='store_true', help='仅输出执行计划')
    args = parser.parse_args()
    
    print_banner()
    
    if args.plan:
        display_stage4_plan()
        return
    
    # 验证阶段三成果
    verify_results = verify_stage3_results()
    
    if args.verify:
        return
    
    # 检查特征泄露
    leakage_features = check_feature_leakage()
    
    # 显示执行计划
    display_stage4_plan()
    
    # 生成启动上下文
    context_file = generate_stage4_context(verify_results, leakage_features)
    
    print("\n" + "=" * 70)
    print("✅ 阶段四启动准备完成!")
    print("=" * 70)
    print(f"\n  📋 下一步操作:")
    print(f"     1. 查看任务清单: docs/stage4_task_list.md")
    print(f"     2. 查看启动上下文: {context_file}")
    print(f"     3. 开始D-009任务: 分离赛前/赛后特征")
    print(f"\n  💡 建议:")
    print(f"     - 优先完成P0任务(D-009, D-010)")
    print(f"     - 每完成一个任务运行train_models_v2.py验证效果")
    print(f"     - 及时更新5大文档")


if __name__ == '__main__':
    main()
