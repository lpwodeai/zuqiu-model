/* ============================================================
 * Five Leagues Prediction Engine v7.7
 * 融合双变量泊+ 蒙特卡洛模拟 + xGOT + 战术克制 + 天气修正
 * + SSM贝叶斯状态空+ XGBoost/LightGBM推理集成 + 6模型Stacking融合 + 赛后复盘学习
 * v3.1: Elo非线性映 SSM纳入集成, XGBoost降权, 动态权 球员对位
 * v3.2: 三层数据架构(L1基础骨架+L2战术攻防+L3动态因
 * v3.3: 正交因子合并(15), 正则化钳制[0.90,1.10], XGBoost→Elo逻辑回归
 * v3.4: Python训练真实XGBoost(15030维特, 替代Elo逻辑回归
 * v3.5: 五层赛前分析报告 + 置信区间 + 敏感性分What-if)
 * v3.6: LightGBM集成(6模型), 邮件推送支 predictFullSlip(胜平比分+半全总进
 * v4.4: 因子膨胀控制+战术克制净效果+动态平局下限+Lambda比值钳比分方向修正降级
 * v3.7: Pipeline一键训练流水线, 三分类XGBoost(240+LightGBM(180, 1633条数据训
 * v3.8: P0优化 进球期望防守韧性缩放因+ 中立场地主场优势自动处理
 * v3.9: P2-赔率校准(融合市场隐含概率) + P3-赛后复盘学习(自动更新Elo/状
 * v4.0: 优化A-数据驱动半场/全场转换概率 + 优化B-真实World Football Elo Ratings
 * v4.1: 修复XGB/LGBM推理溢出 + 赔率校准接入 + Elo平局估算 + 权重优化
 * v4.2: 多模型融合比分预+ XGB/LGBM温度平滑 + 置信度评
 * v4.6: 五层防冷机制(残阵/进攻低效/深度防守/模型背离/历史冷门)
 * v4.7: XGB/LGBM动态温度平极端概率钳制+平局保底提升0%+权重调整
 * v6.1: 阵容深度解析模块(parseSquadDepth) 首发完整关键球员影响/深度评分
 * v6.2: 全情报解析套近期战绩/战术情报/历史交锋/球员数据/天气场地/赔率注入
 * v7.6: Delta上限控制机制(35%限制)+赔率校正因子增强+淘汰赛保守因子优10-12%)+多场比赛统计验证模块
 * v7.7: 层次贝叶斯框架参数共MATCH_TYPE_HIERARCHY)+可靠性图可视computeReliabilityDiagram)
 *       +经典冷门压力测试(CLASSIC_UPSET_TESTCASES+stressTestClassicUpsets)+长期ROI计算(computeLongTermROI)
 * ============================================================ */

var FiveLeaguesEngine = (function() {

  var TEAMS = {};

  function loadTeamAttributes() {
    try {
      var xhr = new XMLHttpRequest();
      xhr.open('GET', 'assets/team_attributes.js', false);
      xhr.send();
      if (xhr.status === 200) {
        var match = xhr.responseText.match(/var TEAM_ATTRIBUTES = (\{[\s\S]*\});/);
        if (match) {
          // 安全解析: 使用 Function 构造器替代 eval (C-003/D-003)
          // Function 构造器在严格模式下不访问闭包作用域，比 eval 更安全
          var data = (new Function('return ' + match[1]))();
          for (var key in data) {
            TEAMS[key] = data[key];
          }
          console.log('', Object.keys(TEAMS).length, '');
          return;
        }
      }
    } catch (e) {
      console.log('', e.message);
    }

    var defaultTeams = {
    pl_ars: { name:'', league:'PL', attack:2.85, defence:0.52, tactical:'possession', xGOT:1.8, xGA:0.68, marketValue:11.2, cohesion:0.92, recentForm:0.05, xT:0.27, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_avl: { name:'', league:'PL', attack:2.45, defence:0.65, tactical:'direct', xGOT:1.55, xGA:0.85, marketValue:6.8, cohesion:0.88, recentForm:0.0, xT:0.23, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_bha: { name:'', league:'PL', attack:2.3, defence:0.68, tactical:'possession', xGOT:1.45, xGA:0.88, marketValue:5.2, cohesion:0.85, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_bur: { name:'', league:'PL', attack:1.8, defence:0.85, tactical:'direct', xGOT:1.2, xGA:1.1, marketValue:2.5, cohesion:0.72, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_che: { name:'', league:'PL', attack:2.75, defence:0.55, tactical:'possession', xGOT:1.75, xGA:0.72, marketValue:10.5, cohesion:0.88, recentForm:0.05, xT:0.26, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_cry: { name:'', league:'PL', attack:2.0, defence:0.78, tactical:'direct', xGOT:1.3, xGA:1.0, marketValue:3.8, cohesion:0.78, recentForm:0.0, xT:0.2, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_eve: { name:'', league:'PL', attack:2.05, defence:0.82, tactical:'direct', xGOT:1.32, xGA:1.06, marketValue:3.5, cohesion:0.75, recentForm:0.0, xT:0.2, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_ful: { name:'', league:'PL', attack:2.1, defence:0.8, tactical:'possession', xGOT:1.35, xGA:1.04, marketValue:4.2, cohesion:0.78, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_liv: { name:'', league:'PL', attack:3.05, defence:0.48, tactical:'pressing', xGOT:1.95, xGA:0.62, marketValue:12.8, cohesion:0.95, recentForm:0.05, xT:0.29, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_mci: { name:'', league:'PL', attack:3.3, defence:0.42, tactical:'possession', xGOT:2.1, xGA:0.55, marketValue:14.5, cohesion:0.96, recentForm:0.05, xT:0.32, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_mun: { name:'', league:'PL', attack:2.6, defence:0.6, tactical:'direct', xGOT:1.65, xGA:0.78, marketValue:9.8, cohesion:0.85, recentForm:0.05, xT:0.25, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_new: { name:'', league:'PL', attack:2.5, defence:0.58, tactical:'direct', xGOT:1.6, xGA:0.75, marketValue:7.2, cohesion:0.88, recentForm:0.0, xT:0.24, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_nfo: { name:'', league:'PL', attack:1.9, defence:0.88, tactical:'direct', xGOT:1.25, xGA:1.14, marketValue:3.0, cohesion:0.7, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_shu: { name:'', league:'PL', attack:1.75, defence:0.9, tactical:'direct', xGOT:1.18, xGA:1.17, marketValue:2.2, cohesion:0.68, recentForm:-0.02, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_sou: { name:'', league:'PL', attack:1.95, defence:0.86, tactical:'direct', xGOT:1.28, xGA:1.1, marketValue:3.2, cohesion:0.72, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_tot: { name:'', league:'PL', attack:2.65, defence:0.56, tactical:'pressing', xGOT:1.7, xGA:0.72, marketValue:9.5, cohesion:0.86, recentForm:0.05, xT:0.26, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_whu: { name:'', league:'PL', attack:2.35, defence:0.66, tactical:'direct', xGOT:1.5, xGA:0.86, marketValue:6.0, cohesion:0.82, recentForm:0.0, xT:0.22, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_wol: { name:'', league:'PL', attack:2.0, defence:0.8, tactical:'counter', xGOT:1.3, xGA:1.04, marketValue:3.8, cohesion:0.76, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_bou: { name:'', league:'PL', attack:1.85, defence:0.87, tactical:'direct', xGOT:1.22, xGA:1.12, marketValue:2.8, cohesion:0.7, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_lei: { name:'', league:'PL', attack:2.2, defence:0.72, tactical:'possession', xGOT:1.42, xGA:0.93, marketValue:4.5, cohesion:0.8, recentForm:0.0, xT:0.21, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_bar: { name:'', league:'SA', attack:3.15, defence:0.46, tactical:'possession', xGOT:1.9, xGA:0.6, marketValue:13.2, cohesion:0.94, recentForm:0.05, xT:0.28, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_rma: { name:'', league:'SA', attack:3.08, defence:0.48, tactical:'possession', xGOT:1.85, xGA:0.62, marketValue:12.8, cohesion:0.93, recentForm:0.05, xT:0.28, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_atm: { name:'', league:'SA', attack:2.6, defence:0.52, tactical:'pressing', xGOT:1.65, xGA:0.68, marketValue:8.5, cohesion:0.9, recentForm:0.05, xT:0.25, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_val: { name:'', league:'SA', attack:2.25, defence:0.68, tactical:'balanced', xGOT:1.48, xGA:0.88, marketValue:4.2, cohesion:0.82, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_sev: { name:'', league:'SA', attack:2.35, defence:0.65, tactical:'possession', xGOT:1.52, xGA:0.85, marketValue:4.8, cohesion:0.84, recentForm:0.0, xT:0.23, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_bet: { name:'', league:'SA', attack:2.15, defence:0.72, tactical:'possession', xGOT:1.4, xGA:0.93, marketValue:3.5, cohesion:0.78, recentForm:0.0, xT:0.21, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_vil: { name:'', league:'SA', attack:2.2, defence:0.7, tactical:'possession', xGOT:1.45, xGA:0.9, marketValue:4.0, cohesion:0.8, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_get: { name:'', league:'SA', attack:1.95, defence:0.82, tactical:'defensive', xGOT:1.28, xGA:1.06, marketValue:2.5, cohesion:0.72, recentForm:0.0, xT:0.19, tempo:0.7, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_esp: { name:'', league:'SA', attack:1.9, defence:0.85, tactical:'direct', xGOT:1.25, xGA:1.1, marketValue:2.8, cohesion:0.7, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_cad: { name:'', league:'SA', attack:1.75, defence:0.9, tactical:'direct', xGOT:1.18, xGA:1.17, marketValue:1.8, cohesion:0.65, recentForm:-0.02, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_osa: { name:'', league:'SA', attack:2.0, defence:0.8, tactical:'counter', xGOT:1.3, xGA:1.04, marketValue:2.2, cohesion:0.72, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_alm: { name:'', league:'SA', attack:1.7, defence:0.92, tactical:'direct', xGOT:1.15, xGA:1.2, marketValue:1.5, cohesion:0.62, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_gir: { name:'', league:'SA', attack:2.45, defence:0.62, tactical:'possession', xGOT:1.58, xGA:0.81, marketValue:5.5, cohesion:0.85, recentForm:0.0, xT:0.24, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_elc: { name:'', league:'SA', attack:1.65, defence:0.95, tactical:'direct', xGOT:1.12, xGA:1.24, marketValue:1.2, cohesion:0.6, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_mll: { name:'', league:'SA', attack:1.85, defence:0.88, tactical:'direct', xGOT:1.22, xGA:1.14, marketValue:2.0, cohesion:0.68, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_civ: { name:'', league:'SA', attack:2.1, defence:0.75, tactical:'direct', xGOT:1.38, xGA:0.97, marketValue:2.8, cohesion:0.75, recentForm:0.0, xT:0.21, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_rso: { name:'', league:'SA', attack:2.3, defence:0.68, tactical:'possession', xGOT:1.5, xGA:0.88, marketValue:4.5, cohesion:0.82, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_rva: { name:'', league:'SA', attack:1.9, defence:0.84, tactical:'direct', xGOT:1.25, xGA:1.08, marketValue:2.0, cohesion:0.68, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_leg: { name:'', league:'SA', attack:1.75, defence:0.9, tactical:'defensive', xGOT:1.18, xGA:1.17, marketValue:1.5, cohesion:0.62, recentForm:-0.02, xT:0.18, tempo:0.7, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    sa_zar: { name:'', league:'SA', attack:1.7, defence:0.93, tactical:'direct', xGOT:1.15, xGA:1.2, marketValue:1.8, cohesion:0.6, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_bay: { name:'', league:'BL1', attack:3.25, defence:0.4, tactical:'possession', xGOT:2.05, xGA:0.52, marketValue:15.5, cohesion:0.96, recentForm:0.05, xT:0.31, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_dor: { name:'', league:'BL1', attack:2.9, defence:0.5, tactical:'pressing', xGOT:1.82, xGA:0.65, marketValue:11.2, cohesion:0.92, recentForm:0.05, xT:0.27, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_lev: { name:'', league:'BL1', attack:2.65, defence:0.56, tactical:'possession', xGOT:1.68, xGA:0.72, marketValue:8.8, cohesion:0.88, recentForm:0.05, xT:0.25, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_wob: { name:'', league:'BL1', attack:2.3, defence:0.68, tactical:'possession', xGOT:1.48, xGA:0.88, marketValue:5.5, cohesion:0.82, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_fra: { name:'', league:'BL1', attack:2.25, defence:0.7, tactical:'direct', xGOT:1.45, xGA:0.91, marketValue:5.2, cohesion:0.8, recentForm:0.0, xT:0.22, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_scf: { name:'', league:'BL1', attack:2.15, defence:0.72, tactical:'direct', xGOT:1.4, xGA:0.93, marketValue:4.5, cohesion:0.78, recentForm:0.0, xT:0.21, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_tsg: { name:'', league:'BL1', attack:2.2, defence:0.7, tactical:'possession', xGOT:1.45, xGA:0.91, marketValue:4.8, cohesion:0.8, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_boc: { name:'', league:'BL1', attack:1.85, defence:0.85, tactical:'direct', xGOT:1.22, xGA:1.1, marketValue:2.5, cohesion:0.7, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_fre: { name:'', league:'BL1', attack:2.05, defence:0.78, tactical:'possession', xGOT:1.35, xGA:1.01, marketValue:3.5, cohesion:0.78, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_koe: { name:'', league:'BL1', attack:1.9, defence:0.84, tactical:'direct', xGOT:1.25, xGA:1.08, marketValue:2.8, cohesion:0.72, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_mai: { name:'', league:'BL1', attack:1.95, defence:0.82, tactical:'direct', xGOT:1.28, xGA:1.06, marketValue:3.0, cohesion:0.74, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_aug: { name:'', league:'BL1', attack:1.8, defence:0.88, tactical:'direct', xGOT:1.2, xGA:1.14, marketValue:2.2, cohesion:0.68, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_hel: { name:'', league:'BL1', attack:1.85, defence:0.86, tactical:'direct', xGOT:1.22, xGA:1.11, marketValue:2.5, cohesion:0.7, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_sge: { name:'', league:'BL1', attack:2.1, defence:0.74, tactical:'pressing', xGOT:1.38, xGA:0.96, marketValue:4.0, cohesion:0.78, recentForm:0.0, xT:0.21, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_bmg: { name:'', league:'BL1', attack:2.0, defence:0.76, tactical:'possession', xGOT:1.32, xGA:0.98, marketValue:4.2, cohesion:0.76, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_rbl: { name:'', league:'BL1', attack:2.5, defence:0.6, tactical:'pressing', xGOT:1.58, xGA:0.78, marketValue:7.5, cohesion:0.86, recentForm:0.0, xT:0.24, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    bl1_bvb: { name:'', league:'BL1', attack:1.85, defence:0.85, tactical:'direct', xGOT:1.22, xGA:1.1, marketValue:2.5, cohesion:0.7, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_juv: { name:'', league:'SerieA', attack:2.8, defence:0.5, tactical:'possession', xGOT:1.78, xGA:0.65, marketValue:10.5, cohesion:0.9, recentForm:0.05, xT:0.27, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_mil: { name:'', league:'SerieA', attack:2.65, defence:0.54, tactical:'possession', xGOT:1.68, xGA:0.7, marketValue:9.8, cohesion:0.88, recentForm:0.05, xT:0.25, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_int: { name:'', league:'SerieA', attack:2.75, defence:0.51, tactical:'pressing', xGOT:1.72, xGA:0.68, marketValue:10.2, cohesion:0.9, recentForm:0.05, xT:0.26, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_rom: { name:'', league:'SerieA', attack:2.4, defence:0.62, tactical:'pressing', xGOT:1.55, xGA:0.81, marketValue:7.0, cohesion:0.84, recentForm:0.0, xT:0.23, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_nap: { name:'', league:'SerieA', attack:2.5, defence:0.58, tactical:'possession', xGOT:1.6, xGA:0.75, marketValue:6.5, cohesion:0.86, recentForm:0.0, xT:0.24, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_laz: { name:'', league:'SerieA', attack:2.3, defence:0.66, tactical:'possession', xGOT:1.48, xGA:0.86, marketValue:5.5, cohesion:0.82, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_flo: { name:'', league:'SerieA', attack:2.2, defence:0.68, tactical:'possession', xGOT:1.45, xGA:0.88, marketValue:4.8, cohesion:0.8, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_atl: { name:'', league:'SerieA', attack:2.45, defence:0.6, tactical:'pressing', xGOT:1.58, xGA:0.78, marketValue:5.2, cohesion:0.84, recentForm:0.0, xT:0.24, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_gen: { name:'', league:'SerieA', attack:1.85, defence:0.86, tactical:'direct', xGOT:1.22, xGA:1.11, marketValue:2.8, cohesion:0.7, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_bov: { name:'', league:'SerieA', attack:2.0, defence:0.78, tactical:'direct', xGOT:1.3, xGA:1.01, marketValue:3.2, cohesion:0.74, recentForm:0.0, xT:0.2, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_sal: { name:'', league:'SerieA', attack:1.65, defence:0.95, tactical:'direct', xGOT:1.12, xGA:1.24, marketValue:1.5, cohesion:0.6, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_cre: { name:'', league:'SerieA', attack:1.6, defence:0.98, tactical:'direct', xGOT:1.1, xGA:1.28, marketValue:1.2, cohesion:0.58, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_tor: { name:'', league:'SerieA', attack:2.0, defence:0.78, tactical:'direct', xGOT:1.3, xGA:1.01, marketValue:3.5, cohesion:0.75, recentForm:0.0, xT:0.2, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_udi: { name:'', league:'SerieA', attack:1.95, defence:0.8, tactical:'direct', xGOT:1.28, xGA:1.04, marketValue:2.8, cohesion:0.72, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_emp: { name:'', league:'SerieA', attack:1.85, defence:0.85, tactical:'possession', xGOT:1.22, xGA:1.1, marketValue:2.2, cohesion:0.68, recentForm:0.0, xT:0.18, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_mon: { name:'', league:'SerieA', attack:1.75, defence:0.9, tactical:'direct', xGOT:1.18, xGA:1.17, marketValue:2.0, cohesion:0.65, recentForm:-0.02, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_lec: { name:'', league:'SerieA', attack:1.7, defence:0.92, tactical:'direct', xGOT:1.15, xGA:1.2, marketValue:1.5, cohesion:0.62, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_sam: { name:'', league:'SerieA', attack:1.8, defence:0.87, tactical:'direct', xGOT:1.2, xGA:1.12, marketValue:2.5, cohesion:0.68, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_ver: { name:'', league:'SerieA', attack:1.85, defence:0.85, tactical:'direct', xGOT:1.22, xGA:1.1, marketValue:2.2, cohesion:0.68, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    seriea_spl: { name:'', league:'SerieA', attack:1.65, defence:0.95, tactical:'direct', xGOT:1.12, xGA:1.24, marketValue:1.2, cohesion:0.6, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_psg: { name:'', league:'FL1', attack:3.35, defence:0.38, tactical:'possession', xGOT:2.15, xGA:0.5, marketValue:16.5, cohesion:0.95, recentForm:0.05, xT:0.32, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_mar: { name:'', league:'FL1', attack:2.45, defence:0.62, tactical:'direct', xGOT:1.58, xGA:0.81, marketValue:6.5, cohesion:0.84, recentForm:0.0, xT:0.24, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_lyo: { name:'', league:'FL1', attack:2.35, defence:0.65, tactical:'possession', xGOT:1.52, xGA:0.85, marketValue:5.8, cohesion:0.82, recentForm:0.0, xT:0.23, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_mon: { name:'', league:'FL1', attack:2.4, defence:0.63, tactical:'direct', xGOT:1.55, xGA:0.83, marketValue:6.2, cohesion:0.84, recentForm:0.0, xT:0.23, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_ren: { name:'', league:'FL1', attack:2.25, defence:0.68, tactical:'pressing', xGOT:1.48, xGA:0.88, marketValue:5.0, cohesion:0.8, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.95, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_str: { name:'', league:'FL1', attack:2.0, defence:0.78, tactical:'direct', xGOT:1.3, xGA:1.01, marketValue:3.2, cohesion:0.74, recentForm:0.0, xT:0.2, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_bor: { name:'', league:'FL1', attack:1.9, defence:0.84, tactical:'possession', xGOT:1.25, xGA:1.08, marketValue:3.0, cohesion:0.72, recentForm:0.0, xT:0.19, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_nan: { name:'', league:'FL1', attack:1.95, defence:0.82, tactical:'direct', xGOT:1.28, xGA:1.06, marketValue:2.8, cohesion:0.72, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_rei: { name:'', league:'FL1', attack:2.05, defence:0.76, tactical:'possession', xGOT:1.35, xGA:0.98, marketValue:3.5, cohesion:0.76, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_tou: { name:'', league:'FL1', attack:2.0, defence:0.78, tactical:'possession', xGOT:1.32, xGA:1.01, marketValue:3.0, cohesion:0.74, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_ang: { name:'', league:'FL1', attack:1.8, defence:0.88, tactical:'direct', xGOT:1.2, xGA:1.14, marketValue:2.2, cohesion:0.68, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_met: { name:'', league:'FL1', attack:1.75, defence:0.9, tactical:'direct', xGOT:1.18, xGA:1.17, marketValue:2.0, cohesion:0.65, recentForm:-0.02, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_clo: { name:'', league:'FL1', attack:1.65, defence:0.95, tactical:'direct', xGOT:1.12, xGA:1.24, marketValue:1.5, cohesion:0.6, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_tro: { name:'', league:'FL1', attack:1.6, defence:0.98, tactical:'direct', xGOT:1.1, xGA:1.28, marketValue:1.2, cohesion:0.58, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_aja: { name:'', league:'FL1', attack:1.65, defence:0.95, tactical:'direct', xGOT:1.12, xGA:1.24, marketValue:1.2, cohesion:0.6, recentForm:-0.02, xT:0.17, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_bre: { name:'', league:'FL1', attack:1.9, defence:0.84, tactical:'direct', xGOT:1.25, xGA:1.08, marketValue:2.5, cohesion:0.7, recentForm:0.0, xT:0.19, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_lor: { name:'', league:'FL1', attack:1.85, defence:0.86, tactical:'direct', xGOT:1.22, xGA:1.11, marketValue:2.2, cohesion:0.68, recentForm:0.0, xT:0.18, tempo:0.85, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_hav: { name:'', league:'FL1', attack:1.75, defence:0.9, tactical:'possession', xGOT:1.18, xGA:1.17, marketValue:1.8, cohesion:0.62, recentForm:-0.02, xT:0.18, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_nic: { name:'', league:'FL1', attack:2.2, defence:0.7, tactical:'possession', xGOT:1.45, xGA:0.91, marketValue:4.5, cohesion:0.78, recentForm:0.0, xT:0.22, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_tou: { name:'', league:'FL1', attack:2.0, defence:0.78, tactical:'possession', xGOT:1.32, xGA:1.01, marketValue:3.0, cohesion:0.74, recentForm:0.0, xT:0.2, tempo:0.7, pressIntensity:0.85, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_sun: { name:'', league:'PL', attack:1.0, defence:0.8, tactical:'direct', xGOT:0.8, xGA:1.0, marketValue:3.0, cohesion:0.75, recentForm:0.0, xT:0.12, tempo:0.8, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    fl1_bre: { name:'', league:'PL', attack:1.2, defence:0.75, tactical:'possession', xGOT:0.9, xGA:0.9, marketValue:4.0, cohesion:0.8, recentForm:0.0, xT:0.14, tempo:0.75, pressIntensity:0.8, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_sun: { name:'', league:'PL', attack:1.0, defence:0.8, tactical:'direct', xGOT:0.8, xGA:1.0, marketValue:3.0, cohesion:0.75, recentForm:0.0, xT:0.12, tempo:0.8, pressIntensity:0.7, attackSide:{left:0.25,center:0.50,right:0.25} },
    pl_bre: { name:'', league:'PL', attack:1.2, defence:0.75, tactical:'possession', xGOT:0.9, xGA:0.9, marketValue:4.0, cohesion:0.8, recentForm:0.0, xT:0.14, tempo:0.75, pressIntensity:0.8, attackSide:{left:0.25,center:0.50,right:0.25} },
  };

    for (var key in defaultTeams) {
      if (!TEAMS[key]) {
        TEAMS[key] = defaultTeams[key];
      }
    }
    console.log('', Object.keys(TEAMS).length, '');
  }

  loadTeamAttributes();

  // ============================================================
  // v6.1: 阵容深度解析模块 (Squad Depth Parser)
  // 自动解析赛前阵容数据，计算阵容完整度和关键球员影
  // ============================================================

  // 球队最强阵容基(用于对比实际首发)

  /**
   * 解析阵容数据，计算阵容完整度和关键球员影
   * @param {string} teamKey - 球队key
   * @param {Object} squadData - 赛前阵容数据
   *   {
   *     startingXI: ['球员1','球员2',...],  // 首发11
   *     bench: ['球员A','球员B',...],        // 替补
   *     absent: ['受伤球员X','停赛球员Y'],   // 缺阵
   *     keyPlayers: {                         // 关键球员状
   *       '球员: {rating: 85, status: 'fit'},  // fit|injured|suspended
   *     }
   *   }
   * @returns {Object} {completeness, keyPlayerImpact, depthScore, warning}
   */
  function parseSquadDepth(teamKey, squadData) {
    if (typeof FiveLeagues_SquadParser !== 'undefined') return FiveLeagues_SquadParser.parseSquadDepth(teamKey, squadData, TEAMS);
    if (!squadData) return { completeness: 1.0, keyPlayerImpact: 1.0, depthScore: 0.5, warning: '' };
    return { completeness: 1.0, keyPlayerImpact: 1.0, depthScore: 0.5, overall: 0.5, warning: 'fallback' };
  }

  /**
   * 应用阵容深度修正到球队attack/defence
   * @param {string} teamKey - 球队key
   * @param {Object} squadData - 阵容数据
   */
  function applySquadDepthAdjustment(teamKey, squadData, options) {
    if (typeof FiveLeagues_SquadParser !== 'undefined') return FiveLeagues_SquadParser.applySquadDepthAdjustment(teamKey, squadData, TEAMS, options);
    return parseSquadDepth(teamKey, squadData);
  }

  /**
   * 恢复球队原始attack/defence (赛后调用)
   * @param {string} teamKey - 球队key
   */
  function restoreTeamOriginalStats(teamKey) {
    if (typeof FiveLeagues_SquadParser !== 'undefined') return FiveLeagues_SquadParser.restoreTeamOriginalStats(teamKey, TEAMS);
    var team = TEAMS[teamKey];
    if (!team) return;
    if (team._originalAttack) team.attack = team._originalAttack;
    if (team._originalDefence) team.defence = team._originalDefence;
  }

  /**
   * 完整恢复球队原始数据 (用于predictFullSlip副作用修
   * @param {string} teamKey - 球队key
   */
  function restoreOriginalTeamData(teamKey) {
    if (typeof FiveLeagues_SquadParser !== 'undefined') return FiveLeagues_SquadParser.restoreOriginalTeamData(teamKey, TEAMS);
    var team = TEAMS[teamKey];
    if (!team) return;
    if (team._originalAttack) team.attack = team._originalAttack;
    if (team._originalDefence) team.defence = team._originalDefence;
    if (team._originalInjury) team.injury = team._originalInjury;
  }

  // ============================================================
  // v7.4: 球员赛后评分影响系数模块 (Player Post-Match Rating Impact)
  // 基于实际比赛数据，量化球员impact
  // ============================================================

  var PLAYER_POSTMATCH_RATINGS = {};

  // 联赛层级分类
  var LEAGUE_TIER = {
    // 五大联赛（顶级）
    '': ['', '', '', '', ''],
    // 次级联赛（旅欧水平）- v7.5更新：基于波黑vs卡塔赛后阵容
    '': ['', '', '', '', '', '', '', '', '', '', '', '', ', '', '', '', '''': ['', '', '', '', '', '', '', '', '', '', ''K', '', '', '', '', ', '', '', '', ', '', '', '', '', ', '', '', '', '', '', '', '', '''': 1.0,
    '': 0.75,
    '''''';

      if (LEAGUE_TIER[''].indexOf(league) !== -1) fiveLeagueCount++;
      else if (LEAGUE_TIER['''';
    if (fiveLeagueRatio < 0.3) {
      warning = '' + (fiveLeagueRatio*100).toFixed(1) + '';
    } else if (fiveLeagueRatio >= 0.9) {
      warning = '';
    } else {
      warning = '' + (fiveLeagueRatio*100).toFixed(1) + '%''''undefined') return FiveLeagues_TacticalAnalyzer.parseRecentForm(teamKey, recentMatches, TEAMS);
    if (!recentMatches || recentMatches.length === 0) return { formScore: 0, goalsForAvg: 0, goalsAgainstAvg: 0, winRate: 0, warning: '' };
    return { formScore: 0, warning: 'fallback''undefined''undefined') return FiveLeagues_TacticalAnalyzer.parseHistoricalH2H(h2hRecords);
    return { h2hFactor: 0, winRateA: null, winRateB: null, lastWinner: null, warning: '''undefined') return FiveLeagues_TacticalAnalyzer.parsePlayerStats(teamKey, playerStats);
    return { attackBoost: 0, defenceBoost: 0, overallBoost: 0, warning: '''undefined''''winA' : (bp.draw > Math.max(bp.winA, bp.winB) ? 'draw' : 'winB');
    var oddsBest = oddsWinA > oddsWinB ? 'winA' : (oddsDraw > Math.max(oddsWinA, oddsWinB) ? 'draw' : 'winB''';
    else if (fusedDraw > fusedWinA && fusedDraw > fusedWinB) calibrated.winDrawWin.recommendation = '';
    else calibrated.winDrawWin.recommendation = '';

    return {
      prediction: calibrated,
      calibrated: true,
      modelBest: modelBest,
      oddsBest: oddsBest,
      conflict: modelBest !== oddsBest,
      oddsImplied: { winA: oddsWinA, draw: oddsDraw, winB: oddsWinB },
      oddsCorrectionFactor: oddsCorrectionFactor,
      deviationCorrection: deviationCorrection,
      modelWeight: dynamicModelW,
      marketWeight: marketW,
      modelOddsDiff: modelOddsDiff,
      msg: modelBest !== oddsBest
        ? '''''5'''' + ' : '';
        bonus.description += '' + Math.round(zoneBlockRate * 100) + '%');
      } else if (zoneBlockRate > 0.35) {
        bonus.totalBonus += 0.02;
        bonus.description += bonus.description ? ' + ' : '';
        bonus.description += '' + Math.round(zoneBlockRate * 100) + '%''''';
        } else if (decay.decayRate > 0.25) {
          decay.warning = '''; ' : '') + '''A' : 'B''' + eloDiff + '      };    }        return {      weakTeamxGModifier: 0,      strongTeamxGModifier: 0,      eloDiff: eloDiff,      strongerTeam: eloA >= eloB ? 'A' : 'B',      description: '' + (overAdjustment * 100).toFixed(1) + '%'      };    }        return {      adjustedOverProb: overProb,      adjustedUnderProb: 1 - overProb,      adjustment: 0,      description: ''
      };
    }
    
    var validMatches = recentMatches.slice(0, 10); // 取最0
    var lateGoalsAgainst = 0;
    var totalMatches = 0;
    
    for (var i = 0; i < validMatches.length; i++) {
      var match = validMatches[i];
      if (match.goalsAgainst && match.lateGoalsAgainst !== undefined) {
        lateGoalsAgainst += match.lateGoalsAgainst;
        totalMatches++;
      }
    }
    
    var lateGoalRate = totalMatches > 0 ? lateGoalsAgainst / totalMatches : 0;
    var riskFactor = lateGoalRate > 0.3 ? 0.15 : (lateGoalRate > 0.2 ? 0.1 : 0);
    
    return {
      riskFactor: riskFactor,
      lateGoalRate: lateGoalRate,
      warning: lateGoalRate > 0.3 ? '' : 
               lateGoalRate > 0.2 ? '' : ''
    };
  }

  // ─── 门将失误风险评估 (v6.4新增) ───
  // 基于联赛级别和门将表现评估失误风
  function getKeeperStabilityRisk(keeperData) {
    if (!keeperData || !keeperData.club || !keeperData.league) {
      return {
        ratingPenalty: 0,
        saveRateExpected: 0.72,
        description: '', '', '', '', '', ''];    var midLeagues = ['U21', '', '', '', '', '', '', '', ''];    var lowLeagues = ['', '', ', '', '', '': ' +                   (ratingPenalty < -0.2 ? ' : ratingPenalty < -0.1 ? '' : ''possession' || b.tactical === 'pressing''counter' && b.tactical === 'possession''pressing''mexico','southafrica','southkorea','czechia'],    B: ['canada','bosnia','qatar','switzerland'],    C: ['brazil','morocco','haiti','scotland'],    D: ['usa','paraguay','australia','turkey'],    E: ['germany','curacao','cotedivoire','ecuador'],    F: ['netherlands','japan','sweden','tunisia'],    G: ['belgium','egypt','iran','newzealand'],    H: ['spain','capeverde','saudiarabia','uruguay'],    I: ['france','senegal','iraq','norway'],    J: ['argentina','algeria','austria','jordan'],    K: ['portugal','congodr','uzbekistan','colombia'],    L: ['england','croatia','ghana','panama''MEX-RSA', home: 'mexico', away: 'southafrica', homeScore: 2, awayScore: 0, result: 'winA' },      { fixture: 'KOR-CZE', home: 'southkorea', away: 'czechia', homeScore: 2, awayScore: 1, result: 'winA' }    ],    // B    B: [      { fixture: 'CAN-BOS', home: 'canada', away: 'bosnia', homeScore: 1, awayScore: 1, result: 'draw' },      { fixture: 'QAT-SUI', home: 'qatar', away: 'switzerland', homeScore: 1, awayScore: 1, result: 'draw' }    ],    // C    C: [      { fixture: 'BRA-MAR', home: 'brazil', away: 'morocco', homeScore: 1, awayScore: 1, result: 'draw' },      { fixture: 'HAI-SCO', home: 'haiti', away: 'scotland', homeScore: 0, awayScore: 1, result: 'winB' }    ],    // D    D: [      { fixture: 'USA-PAR', home: 'usa', away: 'paraguay', homeScore: 4, awayScore: 1, result: 'winA' },      { fixture: 'AUS-TUR', home: 'australia', away: 'turkey', homeScore: 2, awayScore: 0, result: 'winA' }    ],    // E    E: [      { fixture: 'GER-CUR', home: 'germany', away: 'curacao', homeScore: 7, awayScore: 1, result: 'winA' },      { fixture: 'CIV-ECU', home: 'cotedivoire', away: 'ecuador', homeScore: 1, awayScore: 0, result: 'winA' }    ],    // F    F: [      { fixture: 'NED-JPN', home: 'netherlands', away: 'japan', homeScore: 2, awayScore: 2, result: 'draw' },      { fixture: 'SWE-TUN', home: 'sweden', away: 'tunisia', homeScore: 5, awayScore: 1, result: 'winA' }    ],    // G    G: [      { fixture: 'BEL-EGY', home: 'belgium', away: 'egypt', homeScore: 1, awayScore: 1, result: 'draw' },      { fixture: 'IRN-NZL', home: 'iran', away: 'newzealand', homeScore: 2, awayScore: 2, result: 'draw' }    ],    // H    H: [      { fixture: 'SPA-CPV', home: 'spain', away: 'capeverde', homeScore: 0, awayScore: 0, result: 'draw' },      { fixture: 'KSA-URU', home: 'saudiarabia', away: 'uruguay', homeScore: 1, awayScore: 1, result: 'draw' }    ],    // I    I: [      { fixture: 'FRA-SEN', home: 'france', away: 'senegal', homeScore: 3, awayScore: 1, result: 'winA' },      { fixture: 'IRQ-NOR', home: 'iraq', away: 'norway', homeScore: 1, awayScore: 4, result: 'winB' }    ],    // J    J: [      { fixture: 'ARG-ALG', home: 'argentina', away: 'algeria', homeScore: 3, awayScore: 0, result: 'winA' },      { fixture: 'AUT-JOR', home: 'austria', away: 'jordan', homeScore: 3, awayScore: 1, result: 'winA' }    ],    // K    K: [      { fixture: 'POR-CDR', home: 'portugal', away: 'congodr', homeScore: 1, awayScore: 1, result: 'draw' },      { fixture: 'UZB-COL', home: 'uzbekistan', away: 'colombia', homeScore: 1, awayScore: 3, result: 'winB' }    ],    // L    L: [      { fixture: 'ENG-CRO', home: 'england', away: 'croatia', homeScore: 4, awayScore: 2, result: 'winA' },      { fixture: 'GHA-PAN', home: 'ghana', away: 'panama', homeScore: 1, awayScore: 0, result: 'winA''CZE-RSA', home: 'czechia', away: 'southafrica', homeScore: 1, awayScore: 1, result: 'draw' },      { fixture: 'MEX-KOR', home: 'mexico', away: 'southkorea', homeScore: 1, awayScore: 0, result: 'winA' }    ],    // B    B: [      { fixture: 'SUI-BOS', home: 'switzerland', away: 'bosnia', homeScore: 4, awayScore: 1, result: 'winA' },      { fixture: 'CAN-QAT', home: 'canada', away: 'qatar', homeScore: 6, awayScore: 0, result: 'winA' }    ],    // C    C: [      { fixture: 'SCO-MAR', home: 'scotland', away: 'morocco', homeScore: 0, awayScore: 1, result: 'winB' },      { fixture: 'BRA-HAI', home: 'brazil', away: 'haiti', homeScore: 3, awayScore: 0, result: 'winA' }    ],    // D    D: [      { fixture: 'USA-AUS', home: 'usa', away: 'australia', homeScore: 2, awayScore: 0, result: 'winA' },      { fixture: 'TUR-PAR', home: 'turkey', away: 'paraguay', homeScore: 0, awayScore: 1, result: 'winB' }    ],    // E    E: [      { fixture: 'GER-CIV', home: 'germany', away: 'cotedivoire', homeScore: 2, awayScore: 1, result: 'winA' },      { fixture: 'ECU-CUR', home: 'ecuador', away: 'curacao', homeScore: 0, awayScore: 0, result: 'draw' }    ],    // F    F: [      { fixture: 'NED-SWE', home: 'netherlands', away: 'sweden', homeScore: 5, awayScore: 1, result: 'winA' },      { fixture: 'TUN-JPN', home: 'tunisia', away: 'japan', homeScore: 0, awayScore: 4, result: 'winB' }    ],    // G    G: [      { fixture: 'BEL-IRN', home: 'belgium', away: 'iran', homeScore: 0, awayScore: 0, result: 'draw' }    ],    // H    H: [      { fixture: 'SPA-KSA', home: 'spain', away: 'saudiarabia', homeScore: 4, awayScore: 0, result: 'winA' },      { fixture: 'URU-CPV', home: 'uruguay', away: 'capeverde', homeScore: 2, awayScore: 2, result: 'draw' }    ],    // I    I: [      { fixture: 'FRA-IRQ', home: 'france', away: 'iraq', homeScore: 3, awayScore: 0, result: 'winA',        date: '2026-06-23 05:00', venue: '',        halfTime: { home: 1, away: 0 },        formations: { home: '4-2-3-1', away: '4-3-3' },        weather: ' 222699%',        specialNotes: '', assist: '', xA: 0.89, score: '1-0' },          { minute: 54, scorer: ', assist: '', xA: 0.64, score: '2-0' },          { minute: 66, scorer: ', assist: '', xA: 0.89, score: '3-0'': 0.89',            ': 0.64',            ': 0.32',            ': 0.19
          },
          away: {
            '', position: '', rating: 6.6, club: '', league: '' },            { number: 5, name: '', position: '', league: '', subTime: 83 },            { number: 4, name: ', position: '', league: '' },            { number: 17, name: '', rating: 7.1, club: '' },            { number: 3, name: '', position: '' },            { number: 6, name: '', position: '', rating: 7.1, club: '' },            { number: 14, name: ', position: '', rating: 7.0, club: '', league: '' },            { number: 7, name: '', league: '', subTime: 68, goals: 1, assists: 1 },            { number: 11, name: '', rating: 8.0, club: '', subTime: 68, assists: 2 },            { number: 12, name: '', position: '', league: '', subTime: 83 },            { number: 10, name: ', position: '', rating: 9.2, club: '', isCaptain: true, goals: 2 }          ],          away: [            { number: 22, name: '-, position: '', rating: 5.5, club: '', league: '' },            { number: 23, name: '', league: '' },            { number: 5, name: '' },            { number: 4, name: ', position: '', rating: 6.4, club: '', subTime: 60 },            { number: 3, name: '', position: ', rating: 6.2, club: '' },            { number: 8, name: '-', position: '', rating: 6.1, club: ', league: '', subTime: 69 },            { number: 14, name: '', position: '', league: '' },            { number: 24, name: '', position: '', rating: 6.8, club: ', league: '', subTime: 60 },            { number: 11, name: '' },            { number: 16, name: ', position: '', rating: 6.6, club: '', subTime: 68 },            { number: 18, name: '-, position: '', rating: 6.7, club: ''winA',          asianHandicap: { line: -3, result: 'draw', odds: 4.20 },          correctScore: { score: '3-0', odds: 4.80 },          totalGoals: { goals: 3, odds: 4.30 },          halfTimeFullTime: { result: 'winA-winA', odds: 1.17 }        }      },      { fixture: 'NOR-SEN', home: 'norway', away: 'senegal', homeScore: 3, awayScore: 2, result: 'winA',        date: '2026-06-23 08:00', venue: '/',        halfTime: { home: 1, away: 0 },        formations: { home: '4-3-3', away: '4-2-3-1' },        weather: ' 222694%''H, assist: null, type: '', xA: 0.21, score: '1-0' },          { minute: 48, scorer: '', xA: 0.75, score: '2-0' },          { minute: 53, scorer: '', assist: '', type: '', xA: 0.63, score: '2-1' },          { minute: 58, scorer: '', type: '', xA: 0.42, score: '3-1' },          { minute: 93, scorer: '', assist: ', type: '', xA: 0.51, score: '3-2''',
            '': 0.42,
            '': 0.36,
            '': 0.63,            ': 0.51',            '': 0.32,            ': 0.21
          }
        },
        // 球员评分
        playerRatings: {
          home: [
            { number: 1, name: '', rating: 6.7, club: '', league: '' },
            { number: 26, name: '', league: '', subTime: 13 },
            { number: 3, name: '' },
            { number: 17, name: '', rating: 7.0, club: ', league: '', subTime: 84 },
            { number: 5, name: '', rating: 6.6, club: '' },
            { number: 8, name: '', position: '', rating: 6.6, club: '', league: '' },
            { number: 10, name: '', rating: 7.1, club: '', isCaptain: true },
            { number: 14, name: '', rating: 6.6, club: '', league: '', subTime: 46 },
            { number: 7, name: '', rating: 6.6, club: '', league: '', subTime: 84 },
            { number: 9, name: '', rating: 8.6, club: '', league: '', goals: 2 },
            { number: 20, name: '', position: '', league: '', subTime: 71 }          ],          away: [            { number: 16, name: '', position: '', rating: 6.0, club: '', league: ''-, position: '', league: '', subTime: 54 },            { number: 19, name: '', position: ', rating: 7.9, club: '', league: '' },            { number: 3, name: '', position: '', rating: 5.1, club: '' },            { number: 8, name: '-, position: '', rating: 6.3, club: '', subTime: 63 },            { number: 5, name: '-', position: '', rating: 7.2, club: ', league: '' },            { number: 26, name: '', position: '', rating: 6.4, club: '', subTime: 54 },            { number: 10, name: '', position: '', rating: 7.0, club: ', league: '', position: '', rating: 6.8, club: '' },            { number: 18, name: '-', position: ', rating: 8.3, club: '', league: '', goals: 2 }
          ]
        },
        // 开奖结
        oddsResults: {
          winDrawLose: 'winA',
          asianHandicap: { line: -1, result: 'draw', odds: 3.85 },
          correctScore: { score: '3-2', odds: 17.00 },
          totalGoals: { goals: 5, odds: 9.05 },
          halfTimeFullTime: { result: 'winA-winA', odds: 3.25 }
        }
      }
    ],
    // J
    J: [
      { fixture: 'JOR-ALG', home: 'jordan', away: 'algeria', homeScore: 1, awayScore: 2, result: 'winB',
        date: '2026-06-23 11:00', venue: '',
        halfTime: { home: 1, away: 0 },
        formations: { home: '3-4-3', away: '4-3-3' },
        weather: '',
        // Opta高阶数据
        opta: {
          xG: { home: 0.65, away: 1.81 },
          xGOT: { home: 0.61, away: 1.73 },
          xA: { home: 0.52, away: 1.66 },
          xGA: { home: 1.73, away: 0.61 }
        },
        // 技术统
        stats: {
          possession: { home: 28, away: 72 },
          shots: { home: 8, away: 17 },
          shotsOnTarget: { home: 4, away: 8 },
          shotsOffTarget: { home: 4, away: 9 },
          hitsWoodwork: { home: 0, away: 1 },
          blockedShots: { home: 5, away: 7 },
          shotsInBox: { home: 3, away: 11 },
          shotsOutsideBox: { home: 5, away: 6 },
          bigChances: { home: 1, away: 3 },
          bigChancesMissed: { home: 0, away: 2 },
          corners: { home: 1, away: 10 },
          fouls: { home: 11, away: 6 },
          offsides: { home: 1, away: 0 },
          yellowCards: { home: 1, away: 1 },
          redCards: { home: 0, away: 0 },
          saves: { home: 3, away: 6 },
          totalPasses: { home: 306, away: 647 },
          successfulPasses: { home: 220, away: 569 },
          passAccuracy: { home: 72, away: 88 },
          attackingThirdPasses: { home: 34, away: 246 },
          throughBalls: { home: 2, away: 9 },
          crosses: { home: { total: 9, successful: 2 }, away: { total: 42, successful: 11 } },
          dangerousAttacks: { home: 24, away: 75 },
          tacklesWon: { home: 26, away: 13 },
          clearances: { home: 41, away: 12 }
        },
        // 进球事件
        goals: [
          { minute: 36, scorer: '', type: '', xA: 0.52, score: '1-0', team: 'home' },
          { minute: 69, scorer: '', assist: '', type: '', xA: 0.78, score: '1-1', team: 'away' },
          { minute: 82, scorer: '', type: '', xA: 0.78, score: '1-2', team: 'away'': 0.52,            '': 0.18          },          away: {            '': 0.78,            '',            '': 0.31,            '', position: '', rating: 7.7, club: '', rating: 6.8, club: ''-, position: '', league: '', position: '', league: ''-', position: 'B2B', rating: 6.9, club: ', league: '', position: '', rating: 6.7, club: '', goals: 1 },            { number: 20, name: '', position: ''-, position: '', rating: 6.4, club: '', league: '', subTime: 84 },            { number: 9, name: '', rating: 6.6, club: ''-, position: '', league: ''-, position: '', rating: 6.5, club: '' },            { number: 15, name: '-', position: ', rating: 7.1, club: '', subTime: 85 },            { number: 21, name: '', position: '', rating: 7.8, club: ', league: '' },            { number: 2, name: '', position: '', league: '' },            { number: 17, name: '', position: ', rating: 6.0, club: '', league: '' },            { number: 22, name: '', position: '', rating: 6.6, club: '' },            { number: 6, name: ', position: '', rating: 6.2, club: '', league: '', subTime: 46 },            { number: 14, name: '', rating: 7.1, club: '', league: '', subTime: 46 },            { number: 10, name: ', position: '', league: '' },            { number: 9, name: ', position: '', rating: 7.3, club: '', league: '', goals: 1, subTime: 86 },            { number: 7, name: '', position: '', rating: 7.2, club: '', league: ''winB',
          asianHandicap: { line: 1, result: 'draw', odds: 3.27 },
          correctScore: { score: '1-2', odds: 6.00 },
          totalGoals: { goals: 3, odds: 3.50 },
          halfTimeFullTime: { result: 'winA-winB', odds: 22.00 }
        }
      }
    ],
    // K
    K: [
      { fixture: 'POR-UZB', home: 'portugal', away: 'uzbekistan', homeScore: 5, awayScore: 0, result: 'winA',
        date: '2026-06-24 01:00', venue: '',
        halfTime: { home: 3, away: 0 },
        formations: { home: '4-2-3-1', away: '3-4-3' },
        weather: ''C, assist: '', xA: 0.64, score: '1-0' },
          { minute: 17, scorer: ''C, type: '', xA: 0.31, score: '2-0' },
          { minute: 39, scorer: 'C, assist: '', type: '', xA: 0.58, score: '3-0' },
          { minute: 60, scorer: '', assist: null, type: ''4-0', isOwnGoal: true },
          { minute: 87, scorer: '', xA: 0.19, score: '5-0'',            '': 0.58,            'C: 0.31',            '',            '', position: '', rating: 6.9, club: '' },            { number: 20, name: ', position: '', league: '', subTime: 46, assists: 1 },            { number: 3, name: ', position: '', league: '' },            { number: 13, name: ', position: '', rating: 7.3, club: '', league: '', yellowCard: 68 },            { number: 25, name: '', league: '', goals: 1 },            { number: 23, name: '', position: '', rating: 7.6, club: '', league: '', subTime: 83 },            { number: 15, name: '', rating: 7.1, club: '', subTime: 76 },            { number: 18, name: '', league: '', subTime: 46 },            { number: 8, name: '', position: '', rating: 8.7, club: '', league: '', assists: 2 },            { number: 11, name: '', position: '', league: '', subTime: 63 },            { number: 7, name: 'C, position: '', rating: 8.6, club: '', isCaptain: true, goals: 2 }
          ],
          away: [
            { number: 12, name: '', position: '', rating: 6.4, club: '', ownGoal: true },
            { number: 13, name: '', position: '', league: ', subTime: 46 },
            { number: 7, name: '', position: '', rating: 6.2, club: '', league: '', subTime: 90 },
            { number: 9, name: '', position: '', rating: 5.7, club: '', subTime: 46, yellowCard: 14 },
            { number: 24, name: '', position: '', league: '',
            { number: 5, name: '', position: '', league: '',
            { number: 18, name: '', position: '', rating: 6.0, club: '', league: '',
            { number: 2, name: '', position: '', league: '' },
            { number: 22, name: '', position: '', rating: 6.6, club: '', rating: 6.9, club: '',
            { number: 14, name: ''winA', odds: 1.88 },          correctScore: { score: '5-0', odds: 15.00 },          totalGoals: { goals: 5, odds: 6.30 },          halfTimeFullTime: { result: 'winA-winA', odds: 1.31 }        }      },      { fixture: 'COL-COD', home: 'colombia', away: 'congo_dr', homeScore: 1, awayScore: 0, result: 'winA',        date: '2026-06-24 10:00', venue: '',        halfTime: { home: 0, away: 0 },        formations: { home: '4-1-2-3', away: '5-3-2' },        weather: '', xA: 0.58, score: '1-0'',            '',            '': 0.21,            '': 0.10          },          away: {            '': 0.30,            '', rating: 7.0, club: '', league: '' },            { number: 2, name: '', position: ', rating: 7.9, club: '', goals: 1 },            { number: 23, name: '', position: '', rating: 7.7, club: ', league: '' },            { number: 3, name: '', yellowCard: 56 },            { number: 17, name: ', position: '', league: '' },            { number: 16, name: '', rating: 7.1, club: '', league: '', yellowCard: 94 },            { number: 14, name: '', position: 'B2B', rating: 6.9, club: ', league: '' },            { number: 11, name: '', position: '', rating: 7.2, club: '', league: '', subTime: 77 },            { number: 10, name: '', position: '', rating: 7.0, club: '', league: '', isCaptain: true, subTime: 58 },            { number: 25, name: '', rating: 6.6, club: '', league: '', subTime: 58 },            { number: 7, name: '', position: ', rating: 6.7, club: '' }          ],          away: [            { number: 1, name: '', position: '', rating: 7.5, club: ', league: '' },            { number: 26, name: '', position: '', league: '', rating: 6.1, club: '', league: '', subTime: 72 },            { number: 8, name: '', position: '', rating: 6.5, club: '', league: '', subTime: 82 },            { number: 6, name: ', position: '', rating: 6.0, club: '', league: '', subTime: 46 },            { number: 3, name: '', position: '', league: '' },            { number: 4, name: '', position: '', rating: 6.5, club: ', league: '' },            { number: 22, name: '', league: '', isCaptain: true },            { number: 2, name: '', position: '', league: '' },            { number: 20, name: '', position: '', rating: 6.7, club: '', league: '' },            { number: 17, name: ', position: '', rating: 6.3, club: '', league: '''winA',
          asianHandicap: { line: -1, result: 'draw', odds: 3.10 },
          correctScore: { score: '1-0', odds: 5.75 },
          totalGoals: { goals: 1, odds: 4.40 },
          halfTimeFullTime: { result: 'draw-winA', odds: 3.86 }
        }
      }
    ],
    // L
    L: [
      { fixture: 'ENG-GHA', home: 'england', away: 'ghana', homeScore: 0, awayScore: 0, result: 'draw',
        date: '2026-06-24 04:00', venue: '',
        halfTime: { home: 0, away: 0 },
        formations: { home: '4-2-3-1', away: '4-4-2' },
        weather: '''',
            '': 0.41,
            '': 0.26,
            '': 0.16
          },
          away: {
            '',
            '', position: '', rating: 6.8, club: ', league: '' },            { number: 24, name: '' },            { number: 2, name: '', position: ', rating: 7.5, club: '' },            { number: 6, name: '', position: '', rating: 8.3, club: ', league: '' },            { number: 25, name: '', subTime: 66 },            { number: 8, name: ', position: '', rating: 7.2, club: '', subTime: 74 },            { number: 4, name: '', position: '', rating: 7.4, club: ', league: '', yellowCard: 41 },            { number: 20, name: '', position: '', subTime: 83 },            { number: 10, name: '', position: '', rating: 6.3, club: '', subTime: 73 },            { number: 18, name: ', position: '', league: '', subTime: 65 },            { number: 9, name: '', position: '', rating: 6.8, club: '', isCaptain: true }
          ],
          away: [
            { number: 16, name: '', position: '', rating: 7.5, club: '', league: '' },
            { number: 14, name: '', league: '' },
            { number: 18, name: '', league: '' },
            { number: 4, name: '', position: '', rating: 6.8, club: '' },
            { number: 26, name: '', rating: 7.3, club: '', league: '', subTime: 87 },
            { number: 8, name: '', position: '', rating: 6.7, club: '', league: '' },
            { number: 5, name: '', position: '', rating: 7.5, club: '' },
            { number: 3, name: '', rating: 6.3, club: '', league: '' },
            { number: 19, name: '', rating: 6.1, club: '', league: '', subTime: 66, yellowCard: 60 },
            { number: 11, name: '', league: '' },
            { number: 9, name: '', position: '', rating: 6.5, club: ''winB', odds: 2.55 },          correctScore: { score: '0-0', odds: 20.00 },          totalGoals: { goals: 0, odds: 20.00 },          halfTimeFullTime: { result: 'draw-draw''mexico', name: ''southkorea', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 2, goalsAgainst: 1, goalDifference: 1, points: 3, rank: 2 },      { team: 'czechia', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 1, goalsAgainst: 2, goalDifference: -1, points: 0, rank: 3 },      { team: 'southafrica', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 0, goalsAgainst: 2, goalDifference: -2, points: 0, rank: 4 }    ],    B: [      { team: 'switzerland', name: '', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 1, goalsAgainst: 1, goalDifference: 0, points: 1, rank: 1 },      { team: 'canada', name: ', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 1, goalsAgainst: 1, goalDifference: 0, points: 1, rank: 2 },      { team: 'qatar', name: ''bosnia', name: '', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 1, goalsAgainst: 1, goalDifference: 0, points: 1, rank: 4 }    ],    C: [      { team: 'scotland', name: ', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 1, goalsAgainst: 0, goalDifference: 1, points: 3, rank: 1 },      { team: 'brazil', name: '', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 1, goalsAgainst: 1, goalDifference: 0, points: 1, rank: 2 },      { team: 'morocco', name: ''haiti', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 0, goalsAgainst: 1, goalDifference: -1, points: 0, rank: 4 }    ],    D: [      { team: 'usa', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 4, goalsAgainst: 1, goalDifference: 3, points: 3, rank: 1 },      { team: 'australia', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 2, goalsAgainst: 0, goalDifference: 2, points: 3, rank: 2 },      { team: 'turkey', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 0, goalsAgainst: 2, goalDifference: -2, points: 0, rank: 3 },      { team: 'paraguay', name: ', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 1, goalsAgainst: 4, goalDifference: -3, points: 0, rank: 4 }
    ],
    E: [
      { team: 'germany', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 7, goalsAgainst: 1, goalDifference: 6, points: 3, rank: 1 },
      { team: 'cotedivoire', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 1, goalsAgainst: 0, goalDifference: 1, points: 3, rank: 2 },
      { team: 'ecuador', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 0, goalsAgainst: 1, goalDifference: -1, points: 0, rank: 3 },
      { team: 'curacao', name: ''sweden', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 5, goalsAgainst: 1, goalDifference: 4, points: 3, rank: 1 },      { team: 'japan', name: '', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 2, goalsAgainst: 2, goalDifference: 0, points: 1, rank: 2 },      { team: 'netherlands', name: '', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 2, goalsAgainst: 2, goalDifference: 0, points: 1, rank: 3 },      { team: 'tunisia', name: ', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 1, goalsAgainst: 5, goalDifference: -4, points: 0, rank: 4 }
    ],
    G: [
      { team: 'newzealand', name: '',
      { team: 'iran', name: '', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 2, goalsAgainst: 2, goalDifference: 0, points: 1, rank: 2 },
      { team: 'belgium', name: '',
      { team: 'egypt', name: '', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 1, goalsAgainst: 1, goalDifference: 0, points: 1, rank: 4 }
    ],
    H: [
      { team: 'uruguay', name: '',
      { team: 'saudiarabia', name: '',
      { team: 'spain', name: '',
      { team: 'capeverde', name: ''norway', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 4, goalsAgainst: 1, goalDifference: 3, points: 3, rank: 1 },      { team: 'france', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 3, goalsAgainst: 1, goalDifference: 2, points: 3, rank: 2 },      { team: 'senegal', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 1, goalsAgainst: 3, goalDifference: -2, points: 0, rank: 3 },      { team: 'iraq', name: ', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 1, goalsAgainst: 4, goalDifference: -3, points: 0, rank: 4 }
    ],
    J: [
      { team: 'argentina', name: '',
      { team: 'austria', name: '',
      { team: 'jordan', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 1, goalsAgainst: 3, goalDifference: -2, points: 0, rank: 3 },
      { team: 'algeria', name: ''colombia', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 3, goalsAgainst: 1, goalDifference: 2, points: 3, rank: 1 },      { team: 'congodr', name: '(', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 1, goalsAgainst: 1, goalDifference: 0, points: 1, rank: 2 },      { team: 'portugal', name: ', played: 1, won: 0, drawn: 1, lost: 0, goalsFor: 1, goalsAgainst: 1, goalDifference: 0, points: 1, rank: 3 },      { team: 'uzbekistan', name: '', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 1, goalsAgainst: 3, goalDifference: -2, points: 0, rank: 4 }    ],    L: [      { team: 'england', name: ''ghana', name: '', played: 1, won: 1, drawn: 0, lost: 0, goalsFor: 1, goalsAgainst: 0, goalDifference: 1, points: 3, rank: 2 },      { team: 'panama', name: ', played: 1, won: 0, drawn: 0, lost: 1, goalsFor: 0, goalsAgainst: 1, goalDifference: -1, points: 0, rank: 3 },      { team: 'croatia', name: ''_M1'] = [teams[0], teams[1], 1];    FIXTURES[gName+'_M2'] = [teams[2], teams[3], 1];    FIXTURES[gName+'_M3'] = [teams[0], teams[2], 2];    FIXTURES[gName+'_M4'] = [teams[1], teams[3], 2];    FIXTURES[gName+'_M5'] = [teams[0], teams[3], 3];    FIXTURES[gName+'_M6'', city:'', altitude:2240, factor:0.82 },    guadalajara: { name:''-', altitude:0, factor:1.00 },    usa_east:    { name:'', city:'/, altitude:10, factor:1.00 },    usa_west:    { name:'', city:'', city:', altitude:130, factor:0.99 },    canada:      { name:''azteca', B: 'guadalajara', C: 'monterrey', D: 'usa_south',    E: 'usa_east', F: 'usa_west', G: 'usa_east', H: 'monterrey',    I: 'usa_east', J: 'usa_south', K: 'canada', L: 'usa_west'' },    groupStage:   { weight: 1.00, lambdaScale: 1.00, drawBoost: 0.00, description: ''32 },    round16:      { weight: 1.15, lambdaScale: 0.92, drawBoost: 0.04, description: '' },    quarterfinal: { weight: 1.18, lambdaScale: 0.90, drawBoost: 0.05, description: '' },    semifinal:    { weight: 1.20, lambdaScale: 0.89, drawBoost: 0.08, description: '' },    final:        { weight: 1.25, lambdaScale: 0.88, drawBoost: 0.10, description: ''groupStage''brazil-morocco-20260614',
      date: '2026-06-14',
      teamA: 'brazil',
      teamB: 'morocco',
      goalsA: 1,
      goalsB: 1,
      xG_a: 1.29,
      xG_b: 1.34,
      xGOT_a: 1.41,
      xGOT_b: 0.72,
      possessionA: 55,
      shotsA: 12,
      shotsB: 10,
      result: 'draw',
      matchType: 'group',
      advancedMetrics: {
        firstHalf: { xG_a: 0.93, xG_b: 1.17, possessionA: 52 },
        secondHalf: { xG_a: 0.36, xG_b: 0.16, possessionA: 58 },
        openPlay: { xG_a: 1.05, xG_b: 1.20 },
        setPiece: { xG_a: 0.24, xG_b: 0.14 },
        xPTS_a: 1.32,
        xPTS_b: 1.40
      },
      notes: ''brazil-haiti-20260620',      date: '2026-06-20',      teamA: 'brazil',      teamB: 'haiti',      goalsA: 3,      goalsB: 0,      xG_a: 1.68,      xG_b: 0.37,      xGOT_a: 2.24,      xGOT_b: 0.69,      possessionA: 62,      shotsA: 18,      shotsB: 4,      result: 'winA',      matchType: 'group',      advancedMetrics: {        firstHalf: { xG_a: 1.34, xG_b: 0.00, possessionA: 70 },        secondHalf: { xG_a: 0.34, xG_b: 0.37, possessionA: 54 },        openPlay: { xG_a: 1.67, xG_b: 0.21 },        setPiece: { xG_a: 0.01, xG_b: 0.18 },        xPTS_a: 2.43,        xPTS_b: 0.38      },      notes: ''scotland-brazil-20260625',
      date: '2026-06-25',
      teamA: 'scotland',
      teamB: 'brazil',
      goalsA: 0,
      goalsB: 3,
      xG_a: 1.14,
      xG_b: 3.98,
      xGOT_a: 1.35,
      xGOT_b: 4.41,
      possessionA: 35,
      shotsA: 6,
      shotsB: 22,
      result: 'winB',
      matchType: 'group',
      advancedMetrics: {
        firstHalf: { xG_a: 0.16, xG_b: 2.56, possessionA: 30 },
        secondHalf: { xG_a: 0.99, xG_b: 1.43, possessionA: 40 },
        openPlay: { xG_a: 0.50, xG_b: 3.88 },
        setPiece: { xG_a: 0.64, xG_b: 0.11 },
        xPTS_a: 0.14,
        xPTS_b: 2.81
      },
      notes: '',
      analysis: {
        correctOutcome: true,
        predictedOutcome: 'draw',
        actualOutcome: 'draw',
        goalErrors: { teamA: '0.36', teamB: '0.09' },
        xGErrors: { teamA: '0.36', teamB: '0.09' },
        overUnder: { teamA: '', teamB: '' },
        marketEdge: { model: 0.27, market: 0.24, edge: 3.0 },
        lessons: [
          '',
          '',
          '',
          ''netherlands-japan-20260615',      date: '2026-06-15',      teamA: 'netherlands',      teamB: 'japan',      goalsA: 2,      goalsB: 2,      xG_a: 1.01,      xG_b: 0.75,      xGOT_a: 2.80,      xGOT_b: 1.68,      possessionA: 48,      shotsA: 10,      shotsB: 8,      result: 'draw',      matchType: 'group',      advancedMetrics: {        firstHalf: { xG_a: 0.53, xG_b: 0.23, possessionA: 52 },        secondHalf: { xG_a: 0.49, xG_b: 0.52, possessionA: 44 },        openPlay: { xG_a: 0.43, xG_b: 0.51 },        setPiece: { xG_a: 0.58, xG_b: 0.24 },        xPTS_a: 1.57,        xPTS_b: 1.10      },      notes: '',      analysis: {        correctOutcome: true,        predictedOutcome: 'winA',        actualOutcome: 'draw',        goalErrors: { teamA: '0.99', teamB: '1.25' },        xGErrors: { teamA: '0.79', teamB: '1.25' },        overUnder: { teamA: '', teamB: '' },        marketEdge: { model: 0.48, market: 0.42, edge: 6.0 },        lessons: [          '',          '',          '',          ''tunisia-japan-20260621',      date: '2026-06-21',      teamA: 'tunisia',      teamB: 'japan',      goalsA: 0,      goalsB: 4,      xG_a: 0.09,      xG_b: 2.02,      xGOT_a: 0.00,      xGOT_b: 2.59,      possessionA: 32,      shotsA: 2,      shotsB: 16,      result: 'winB',      matchType: 'group',      advancedMetrics: {        firstHalf: { xG_a: 0.02, xG_b: 1.08, possessionA: 28 },        secondHalf: { xG_a: 0.05, xG_b: 0.94, possessionA: 36 },        openPlay: { xG_a: 0.00, xG_b: 1.81 },        setPiece: { xG_a: 0.09, xG_b: 0.21 },        xPTS_a: 0.13,        xPTS_b: 2.78      },      notes: '',      analysis: {        correctOutcome: true,        predictedOutcome: 'winB',        actualOutcome: 'winB',        goalErrors: { teamA: '0.09', teamB: '1.98' },        xGErrors: { teamA: '0.09', teamB: '1.98' },        overUnder: { teamA: '', teamB: '' },        marketEdge: { model: 0.82, market: 0.75, edge: 7.0 },        lessons: [          '',          '',          '',          ''japan-sweden-20260626',
      date: '2026-06-26',
      teamA: 'japan',
      teamB: 'sweden',
      goalsA: 1,
      goalsB: 1,
      xG_a: 1.17,
      xG_b: 0.85,
      xGOT_a: 1.23,
      xGOT_b: 1.54,
      possessionA: 42,
      shotsA: 9,
      shotsB: 7,
      result: 'draw',
      matchType: 'group',
      advancedMetrics: {
        firstHalf: { xG_a: 0.17, xG_b: 0.09, possessionA: 45 },
        secondHalf: { xG_a: 1.00, xG_b: 0.76, possessionA: 39 },
        openPlay: { xG_a: 1.17, xG_b: 0.28 },
        setPiece: { xG_a: 0.00, xG_b: 0.58 },
        xPTS_a: 1.63,
        xPTS_b: 1.07
      },
      notes: '',
      analysis: {
        correctOutcome: false,
        predictedOutcome: 'winA',
        actualOutcome: 'draw',
        goalErrors: { teamA: '0.17', teamB: '0.15' },
        xGErrors: { teamA: '0.17', teamB: '0.15' },
        overUnder: { teamA: '', teamB: '' },
        marketEdge: { model: 0.58, market: 0.52, edge: 6.0 },
        lessons: [
          '',
          '',
          '',
          ''germany-paraguay-20260630',
      date: '2026-06-30',
      teamA: 'germany',
      teamB: 'paraguay',
      goalsA: 1,
      goalsB: 1,
      xG_a: 1.49,
      xG_b: 0.42,
      possessionA: 75,
      shotsA: 21,
      shotsB: 7,
      result: 'draw',
      matchType: 'knockout_round32',
      penaltyShootout: {
        goalsA: 3,
        goalsB: 4,
        winner: 'paraguay',
        shootoutSummary: '',      analysis: {        correctOutcome: false,        predictedOutcome: 'winA',        actualOutcome: 'draw',        penaltyOutcome: 'winB',        goalErrors: { teamA: '-0.49', teamB: '+0.58' },        xGErrors: { teamA: '0', teamB: '-0.73' },        overUnder: { teamA: '', teamB: '' },        marketEdge: { model: 0.67, market: 0.645, edge: 2.5 },        lessons: [          '',          '',          '',          '',          ''netherlands-morocco-20260630',
      date: '2026-06-30',
      teamA: 'netherlands',
      teamB: 'morocco',
      goalsA: 1,
      goalsB: 1,
      xG_a: 0.23,
      xG_b: 1.40,
      possessionA: 30,
      shotsA: 6,
      shotsB: 11,
      result: 'draw',
      matchType: 'knockout_round32',
      penaltyShootout: {
        goalsA: 2,
        goalsB: 3,
        winner: 'morocco',
        shootoutSummary: '',      analysis: {        correctOutcome: false,        predictedOutcome: 'winA',        actualOutcome: 'draw',        penaltyOutcome: 'winB',        goalErrors: { teamA: '+0.77', teamB: '-0.40' },        xGErrors: { teamA: '-1.92', teamB: '+0.15' },        overUnder: { teamA: '', teamB: '' },        marketEdge: { model: 0.64, market: 0.529, edge: 11.1 },        lessons: [          '',          '',          '',          '',          ''brazil-morocco-20260614',
      teamA: 'brazil',
      teamB: 'morocco',
      timestamp: '2026-06-14T00:00:00Z',
      venue: 'neutral',
      predictions: {
        poisson: { winA: 0.48, draw: 0.27, winB: 0.25, lambdaA: 1.65, lambdaB: 1.25 },
        xgboost: { winA: 0.45, draw: 0.28, winB: 0.27 },
        lightgbm: { winA: 0.47, draw: 0.26, winB: 0.27 },
        stacked: { winA: 0.46, draw: 0.27, winB: 0.27 }
      },
      marketOdds: { winA: 2.10, draw: 3.20, winB: 3.40 },
      result: {
        goalsA: 1,
        goalsB: 1,
        xG_a: 1.29,
        xG_b: 1.34,
        possessionA: 55,
        shotsA: 12,
        shotsB: 10,
        actualOutcome: 'draw''brazil-haiti-20260620',
      teamA: 'brazil',
      teamB: 'haiti',
      timestamp: '2026-06-20T00:00:00Z',
      venue: 'neutral',
      predictions: {
        poisson: { winA: 0.72, draw: 0.18, winB: 0.10, lambdaA: 2.15, lambdaB: 0.65 },
        xgboost: { winA: 0.75, draw: 0.16, winB: 0.09 },
        lightgbm: { winA: 0.74, draw: 0.17, winB: 0.09 },
        stacked: { winA: 0.73, draw: 0.17, winB: 0.10 }
      },
      marketOdds: { winA: 1.25, draw: 5.50, winB: 12.00 },
      result: {
        goalsA: 3,
        goalsB: 0,
        xG_a: 1.68,
        xG_b: 0.37,
        possessionA: 62,
        shotsA: 18,
        shotsB: 4,
        actualOutcome: 'winA''scotland-brazil-20260625',
      teamA: 'scotland',
      teamB: 'brazil',
      timestamp: '2026-06-25T00:00:00Z',
      venue: 'neutral',
      predictions: {
        poisson: { winA: 0.08, draw: 0.19, winB: 0.73, lambdaA: 0.85, lambdaB: 2.65 },
        xgboost: { winA: 0.06, draw: 0.18, winB: 0.76 },
        lightgbm: { winA: 0.07, draw: 0.17, winB: 0.76 },
        stacked: { winA: 0.07, draw: 0.18, winB: 0.75 }
      },
      marketOdds: { winA: 9.50, draw: 4.80, winB: 1.28 },
      result: {
        goalsA: 0,
        goalsB: 3,
        xG_a: 1.14,
        xG_b: 3.98,
        possessionA: 35,
        shotsA: 6,
        shotsB: 22,
        actualOutcome: 'winB''netherlands-japan-20260615',
      teamA: 'netherlands',
      teamB: 'japan',
      timestamp: '2026-06-15T00:00:00Z',
      venue: 'neutral',
      predictions: {
        poisson: { winA: 0.48, draw: 0.27, winB: 0.25, lambdaA: 1.25, lambdaB: 1.15 },
        xgboost: { winA: 0.52, draw: 0.26, winB: 0.22 },
        lightgbm: { winA: 0.50, draw: 0.27, winB: 0.23 },
        stacked: { winA: 0.51, draw: 0.26, winB: 0.23 }
      },
      marketOdds: { winA: 2.20, draw: 3.40, winB: 3.10 },
      result: {
        goalsA: 2,
        goalsB: 2,
        xG_a: 1.01,
        xG_b: 0.75,
        possessionA: 48,
        shotsA: 10,
        shotsB: 8,
        actualOutcome: 'draw''tunisia-japan-20260621',
      teamA: 'tunisia',
      teamB: 'japan',
      timestamp: '2026-06-21T00:00:00Z',
      venue: 'neutral',
      predictions: {
        poisson: { winA: 0.12, draw: 0.20, winB: 0.68, lambdaA: 0.45, lambdaB: 1.85 },
        xgboost: { winA: 0.08, draw: 0.18, winB: 0.74 },
        lightgbm: { winA: 0.10, draw: 0.17, winB: 0.73 },
        stacked: { winA: 0.09, draw: 0.18, winB: 0.73 }
      },
      marketOdds: { winA: 8.50, draw: 4.20, winB: 1.35 },
      result: {
        goalsA: 0,
        goalsB: 4,
        xG_a: 0.09,
        xG_b: 2.02,
        possessionA: 32,
        shotsA: 2,
        shotsB: 16,
        actualOutcome: 'winB''japan-sweden-20260626',
      teamA: 'japan',
      teamB: 'sweden',
      timestamp: '2026-06-26T00:00:00Z',
      venue: 'neutral',
      predictions: {
        poisson: { winA: 0.52, draw: 0.28, winB: 0.20, lambdaA: 1.35, lambdaB: 0.95 },
        xgboost: { winA: 0.55, draw: 0.26, winB: 0.19 },
        lightgbm: { winA: 0.53, draw: 0.27, winB: 0.20 },
        stacked: { winA: 0.54, draw: 0.27, winB: 0.19 }
      },
      marketOdds: { winA: 2.45, draw: 3.15, winB: 2.90 },
      result: {
        goalsA: 1,
        goalsB: 1,
        xG_a: 1.17,
        xG_b: 0.85,
        possessionA: 42,
        shotsA: 9,
        shotsB: 7,
        actualOutcome: 'draw''brazil-japan-round32-20260630',
      teamA: 'brazil',
      teamB: 'japan',
      timestamp: '2026-06-30T00:00:00Z',
      venue: 'neutral',
      matchType: 'round32''3-1''2-0''2-1''1-0''3-0''4-0''4-1''0-0''1-1''0-1''7+'': 2.50, '': 14.50, '': 29.00,
          '': 4.10, '': 5.60, '': 10.50,
          '': 19.00, '': 14.50, ''2026-06-27_09:14': { winA: 1.66, draw: 3.28, winB: 4.50 },
        '2026-06-27_20:02': { winA: 1.60, draw: 3.35, winB: 4.85 },
        '2026-06-28_10:47': { winA: 1.56, draw: 3.45, winB: 5.05 },
        '2026-06-28_12:20': { winA: 1.54, draw: 3.48, winB: 5.18 },
        '2026-06-28_17:09': { winA: 1.52, draw: 3.56, winB: 5.25 },
        '2026-06-28_21:02': { winA: 1.50, draw: 3.67, winB: 5.25 },
        '2026-06-29_11:04': { winA: 1.50, draw: 3.72, winB: 5.15 },
        '2026-06-29_12:22'', odds: 8.75, modelProb: 0.182, impliedProb: 0.1143, edge: 6.77, rating: '',
          { bet: '', odds: 15.00, modelProb: 0.128, impliedProb: 0.0667, edge: 6.13, rating: '',
          { bet: '', odds: 7.85, modelProb: 0.155, impliedProb: 0.1274, edge: 2.76, rating: '',
          { bet: '', odds: 31.00, modelProb: 0.085, impliedProb: 0.0323, edge: 5.27, rating: '',
          { bet: '',
        keyAdvantages: [
          '',
          '',
          '',
          ''
        ],
        japanWeakness: [
          '',
          '',
          '',
          ''
        ],
        predictedScenario: '',
        mostLikelyScore: '3-1''germany-paraguay-round32-20260630',
      teamA: 'germany',
      teamB: 'paraguay',
      timestamp: '2026-06-30T00:00:00Z',
      venue: 'neutral',
      matchType: 'round32',
      matchConditions: {
        stadium: '',
        temperature: 28,
        humidity: 45,
        heatPressure: 'low',
        surface: 'grass''1-0''2-0''2-1''3-0''3-1''3-2''4-0''4-1''4-2''5-0''5-1''5-2''0-0''1-1''2-2''0-1''0-2''1-2''winOther': 24.00,
          'drawOther': 300.0,
          'lossOther''7+'': 1.59, '': 21.00, '': 57.00,
          '': 4.10, '': 8.10, '': 22.00,
          '': 21.00, '': 21.00, ''2026-06-28_09:40': { winA: 1.27, draw: 4.60, winB: 8.00 },
          '2026-06-28_11:57': { winA: 1.25, draw: 4.75, winB: 8.40 },
          '2026-06-28_16:15': { winA: 1.24, draw: 4.90, winB: 8.40 },
          '2026-06-28_22:29': { winA: 1.22, draw: 5.10, winB: 8.80 },
          '2026-06-29_10:07': { winA: 1.20, draw: 5.18, winB: 9.70 },
          '2026-06-29_12:26': { winA: 1.19, draw: 5.18, winB: 10.50 },
          '2026-06-29_13:58': { winA: 1.20, draw: 5.10, winB: 10.00 },
          '2026-06-29_14:53''2026-06-28_09:40': { winA: 1.90, draw: 3.60, winB: 3.08 },
          '2026-06-28_11:56': { winA: 1.85, draw: 3.60, winB: 3.22 },
          '2026-06-28_16:15': { winA: 1.82, draw: 3.65, winB: 3.28 },
          '2026-06-28_22:29': { winA: 1.78, draw: 3.70, winB: 3.36 },
          '2026-06-29_10:07': { winA: 1.73, draw: 3.75, winB: 3.52 },
          '2026-06-29_11:30': { winA: 1.70, draw: 3.90, winB: 3.52 },
          '2026-06-29_12:26': { winA: 1.68, draw: 3.90, winB: 3.60 },
          '2026-06-29_12:39': { winA: 1.65, draw: 4.07, winB: 3.60 },
          '2026-06-29_14:55'', odds: 7.20, modelProb: 0.18, impliedProb: 0.1389, edge: 4.11, rating: '',
          { bet: '', odds: 5.50, modelProb: 0.22, impliedProb: 0.1818, edge: 3.82, rating: '',
          { bet: '', odds: 15.00, modelProb: 0.08, impliedProb: 0.0667, edge: 1.33, rating: '',
          { bet: '', odds: 7.00, modelProb: 0.15, impliedProb: 0.1429, edge: 0.71, rating: ''4-2-3-1',        avgRating: 7.07,        players: {          goalkeeper: { name: '' },          rightBack: { name: ', rating: 7.40, analysis: '' },          rightCB: { name: '', rating: 7.03, analysis: '', rating: 6.77, analysis: '' },          leftBack: { name: '', rating: 6.75, analysis: '' },          rightDM: { name: '' },          rightWing: { name: '', rating: 6.77, analysis: '' },          attackingMid: { name: '', rating: 7.13, analysis: ' },          leftWing: { name: '' },          striker: { name: ', rating: 7.27, analysis: '' }
        },
        ratingTier: '',
        tacticalLogic: ''5-4-1',        avgRating: 6.72,        players: {          striker: { name: '', rating: 6.50, analysis: '' },          rightMid: { name: '', rating: 7.55, analysis: ' },          rightCM: { name: '' },          leftCM: { name: ', rating: 6.77, analysis: '', rating: 6.20, analysis: '' },          rightWingBack: { name: '', rating: 6.65, analysis: ' },          rightCB: { name: '' },          centerCB: { name: '', rating: 7.27, analysis: '' },          leftCB: { name: ', rating: 6.83, analysis: '' },          leftWingBack: { name: '', rating: 7.07, analysis: '' }        },        ratingTier: '',        tacticalLogic: '',
          top5Players: '',
          avgMarketValue: '',          top5Players: '',          avgMarketValue: '',          '',          ''        ],        paraguay: [          '',          '',          '',          '',          '',
          '',
          '',
          '',
          '',          '',          '',          '',          '',        busBreakingStrategy: '',        predictedScenario: '',        mostLikelyScore: '2-0',        mostLikelyScoreProb: 0.22,        scoreProbabilities: {          '1-0': 0.18, '2-0': 0.22, '2-1': 0.12, '3-0': 0.15, '3-1': 0.08,          '0-0': 0.08, '4-0': 0.06, '4-1': 0.03, '1-1': 0.04, '0-1': 0.02        },        totalGoalsPrediction: {          '0-1': 0.28, '2-3': 0.55, '4+''draw',        penaltyShootout: {          goalsA: 3,          goalsB: 4,          winner: 'paraguay',          shootoutSummary: ''winA'',
          actualOutcome: 'draw',
          penaltyOutcome: 'winB',
          goalErrors: { teamA: '-0.49', teamB: '+0.58' },
          xGErrors: { teamA: '0', teamB: '-0.73' },
          lessons: [
            '',
            '',
            '',
            '',
            ''netherlands-morocco-round32-20260630',      teamA: 'netherlands',      teamB: 'morocco',      timestamp: '2026-06-30T00:00:00Z',      venue: 'neutral',      matchType: 'round32',      matchConditions: {        stadium: '',        temperature: 34,        humidity: 35,        heatPressure: 'high',        surface: 'grass',        note: ''1-0': 7.50, '2-0': 10.00, '2-1': 5.50, '3-0': 21.00, '3-1': 14.00, '3-2': 23.00,
          '4-0': 60.00, '4-1': 50.00, '4-2': 60.00, '5-0': 160.0, '5-1': 125.0, '5-2': 150.0,
          '0-0': 9.00, '1-1': 5.25, '2-2': 10.00, '3-3': 42.00,
          '0-1': 10.50, '0-2': 22.00, '1-2': 12.50, '0-3': 55.00, '1-3': 36.00, '2-3': 45.00
        },
        totalGoals: {
          0: 9.00, 1: 4.70, 2: 3.10, 3: 3.70, 4: 5.70, 5: 11.50, 6: 22.00, '7+': 33.00
        },
        halfFull: {
          '': 3.45, '': 12.50, '': 26.00,
          '': 4.80, '': 4.70, '': 7.50,
          '': 19.00, '': 12.50, '': 6.30
        }
      },
      oddsTimeSeries: {
        '2026-06-27_09:14': { winA: 1.89, draw: 3.07, winB: 3.64 },
        '2026-06-28_21:44': { winA: 1.86, draw: 3.10, winB: 3.72 },
        '2026-06-28_22:17': { winA: 1.89, draw: 3.07, winB: 3.64 },
        '2026-06-29_10:07': { winA: 1.93, draw: 3.02, winB: 3.57 },
        '2026-06-29_11:08': { winA: 1.99, draw: 2.95, winB: 3.48 },
        '2026-06-29_11:51': { winA: 1.97, draw: 2.95, winB: 3.53 },
        '2026-06-29_15:19': { winA: 1.97, draw: 3.00, winB: 3.47 },
        '2026-06-29_16:34': { winA: 2.00, draw: 2.98, winB: 3.40 },
        '2026-06-29_18:43': { winA: 2.00, draw: 2.92, winB: 3.48 }
      },
      impliedProbabilities: {
        rawWinA: 0.5000,
        rawDraw: 0.3425,
        rawWinB: 0.2874,
        bookmakerMargin: 0.1299,
        normalizedWinA: 0.4426,
        normalizedDraw: 0.3032,
        normalizedWinB: 0.2544
      },
      modelVsMarket: {
        rawWinA_edge: 0.63 - 0.5000,
        rawDraw_edge: 0.25 - 0.3425,
        rawWinB_edge: 0.12 - 0.2874,
        normalizedWinA_edge: 0.63 - 0.4426,
        normalizedDraw_edge: 0.25 - 0.3032,
        normalizedWinB_edge: 0.12 - 0.2544,
        deviationLevel: 'high',
        deviationNote: '',
        driftWeight: 0.85,
        driftNote: '',
        adjustedModelProb: {
          winA: 0.63 * 0.85,
          draw: 0.25 * 1.10,
          winB: 0.12 * 1.15
        },
        valueBets: [
          { bet: '' },
          { bet: '', odds: 5.50, modelProb: 0.18, impliedProb: 0.1818, edge: -0.18, rating: '',
          { bet: '', odds: 7.50, modelProb: 0.15, impliedProb: 0.1333, edge: 1.67, rating: '',
          { bet: '', odds: 10.00, modelProb: 0.16, impliedProb: 0.10, edge: 6.0, rating: '',
          { bet: '' },
          { bet: '' },
          { bet: '''4-3-3',
        avgRating: 7.06,
        players: {
          goalkeeper: { name: '', rating: 6.93, analysis: '' },
          rightBack: { name: '', rating: 7.23, analysis: '' },
          rightCB: { name: '' },
          centerCB: { name: '' },
          leftBack: { name: '' },
          rightMid: { name: '' },
          centerMid: { name: '' },
          leftMid: { name: '', rating: 6.63, analysis: '',
          rightWing: { name: '', rating: 6.67, analysis: '',
          striker: { name: '' },
          leftWing: { name: '' }        },        ratingTier: '.937.777.607.33.23.236.87.67.636.60.93',        tacticalLogic: '
      },
      // 摩洛哥首发阵容分(4-2-3-1)
      moroccoLineup: {
        formation: '4-2-3-1',
        avgRating: 6.90,
        players: {
          striker: { name: '' },
          rightWing: { name: '' },
          attackingMid: { name: '' },
          leftWing: { name: '', rating: 6.83, analysis: '' },
          rightDM: { name: '' },
          rightBack: { name: '', rating: 7.33, analysis: '',
          rightCB: { name: '', rating: 7.03, analysis: '',
          centerCB: { name: '' },
          leftCB: { name: '' },
          goalkeeper: { name: '', rating: 6.37, analysis: '',        tacticalLogic: '',
          top5Players: '',
          avgMarketValue: '',          top5Players: '',          avgMarketValue: '',          '',          '',          '',
          '',
          '',
          '',
          '',
          '',
          '',
          '',
          ''
        ],
        moroccoPointsKeys: [
          '',
          '',
          '',
          '',
          '',        heatImpactAnalysis: '',        oddsTrendAnalysis: '',        marketDeviationWarning: '',        driftAlert: '',        predictedScenario: '',        mostLikelyScore: '2-1',        mostLikelyScoreProb: 0.18,        scoreProbabilities: {          '1-0': 0.15, '2-0': 0.16, '2-1': 0.18, '3-0': 0.10, '3-1': 0.08,          '0-0': 0.10, '1-1': 0.12, '4-0': 0.04, '4-1': 0.02, '0-1': 0.03, '0-2': 0.01        },        totalGoalsPrediction: {          '0-1': 0.28, '2-3': 0.57, '4+': 0.15        },        over25Probability: 0.65,        under25Probability: 0.35,        riskAssessment: {          upsetRisk: '',          reason: '',          mitigation: ''draw',
        penaltyShootout: {
          goalsA: 2,
          goalsB: 3,
          winner: 'morocco',
          shootoutSummary: '',        postMatchAnalysis: {          modelAccuracy: false,          predictedOutcome: 'winA',          actualOutcome: 'draw',          penaltyOutcome: 'winB',          goalErrors: { teamA: '+0.77', teamB: '-0.40' },          xGErrors: { teamA: '-1.92', teamB: '+0.15' },          lessons: [            '',            '',            '',            '',            ''-' + teamB + '-''winA' : (goalsA === goalsB ? 'draw' : 'winB');
    entry.result = {
      goalsA: goalsA,
      goalsB: goalsB,
      xG_a: xG_a,
      xG_b: xG_b,
      possessionA: possessionA,
      shotsA: shotsA,
      shotsB: shotsB,
      actualOutcome: actualOutcome,
      notes: notes || ''poisson', 'dixonCole', 'xgboost', 'lightgbm', 'ssm', 'stacked'];
    for (var i = 0; i < models.length; i++) {
      var m = models[i];
      if (entry.predictions[m]) {
        var pred = entry.predictions[m];
        var predOutcome = pred.winA >= pred.draw && pred.winA >= pred.winB ? 'winA' :
                          (pred.draw >= pred.winB ? 'draw' : 'winB''group''winA' :
                      (p.draw >= p.winB ? 'draw' : 'winB'' : (predLambdaA < r.goalsA ? '' : '');
    var overUnderB = predLambdaB > r.goalsB ? '' : (predLambdaB < r.goalsB ? '' : ''winA' ? p.winA : (r.actualOutcome === 'draw' ? p.draw : p.winB);
      var marketProb = r.actualOutcome === 'winA' ? fairProbA : (r.actualOutcome === 'draw'');
    if (goalErrorB > 1.5) lessons.push(b.name + '');
    if (r.possessionA !== undefined && Math.abs(r.possessionA - 50) > 15) {
      lessons.push('' + r.possessionA + '');
    }
    if (r.xG_a !== undefined && r.xG_b !== undefined) {
      if (Math.abs((r.xG_a + r.xG_b) - (r.goalsA + r.goalsB)) > 1.5) {
        lessons.push('');
      }
    }
    if (!correctOutcome) {
      lessons.push('');
    }

    return {
      matchId: entry.matchId,
      teams: a.name + ' ' + r.goalsA + ' - ' + r.goalsB + ' ''home' : (result.goalsA < result.goalsB ? 'away' : 'draw''knockout''draw') {
        drawTotal++;
        if (predOutcome === 'draw') drawCorrect++;
      } else if (actualOutcome === 'home') {
        homeTotal++;
        if (predOutcome === 'home') homeCorrect++;
      } else {
        awayTotal++;
        if (predOutcome === 'away''home' ? Math.pow(1 - (pred.winA || 0), 2) : Math.pow(0 - (pred.winA || 0), 2);
      var brierB = actualOutcome === 'away' ? Math.pow(1 - (pred.winB || 0), 2) : Math.pow(0 - (pred.winB || 0), 2);
      var brierDraw = actualOutcome === 'draw'';
    var biasSeverity = 'none';
    if (Math.abs(meanErrorA) > 0.3 && Math.abs(meanErrorB) > 0.3) {
      if (meanErrorA > 0 && meanErrorB > 0) {
        biasPattern = '';
        biasSeverity = meanErrorA + meanErrorB > 0.8 ? 'severe' : 'moderate';
      } else if (meanErrorA < 0 && meanErrorB < 0) {
        biasPattern = '';
        biasSeverity = Math.abs(meanErrorA) + Math.abs(meanErrorB) > 0.8 ? 'severe' : 'moderate';
      }
    } else if (Math.abs(meanErrorA) > 0.4) {
      biasPattern = meanErrorA > 0 ? ''severe' : 'moderate';
    } else if (Math.abs(meanErrorB) > 0.4) {
      biasPattern = meanErrorB > 0 ? ''severe' : 'moderate''%'
        },
        totalGoals: {
          correct: totalGoalsCorrect,
          total: total,
          rate: (totalGoalsCorrect / total * 100).toFixed(1) + '%'
        },
        overUnder: {
          overCorrect: overCorrect,
          underCorrect: underCorrect,
          pushCount: pushCount,
          overRate: (overCorrect / (overCorrect + underCorrect || 1) * 100).toFixed(1) + '%'
        },
        knockout: knockoutTotal > 0 ? {
          correct: knockoutCorrect,
          total: knockoutTotal,
          rate: (knockoutCorrect / knockoutTotal * 100).toFixed(1) + '%'
        } : null
      },
      brierScore: {
        mean: meanBrier.toFixed(4),
        rating: meanBrier < 0.18 ? '' : (meanBrier < 0.25 ? '' : (meanBrier < 0.35 ? '')''%' },
        home: { correct: homeCorrect, total: homeTotal, rate: (homeCorrect / (homeTotal || 1) * 100).toFixed(1) + '%' },
        away: { correct: awayCorrect, total: awayTotal, rate: (awayCorrect / (awayTotal || 1) * 100).toFixed(1) + '%' }
      },
      confidenceAnalysis: {
        highConfidence: {
          correct: highConfCorrect,
          total: highConfTotal,
          rate: (highConfCorrect / (highConfTotal || 1) * 100).toFixed(1) + '%'
        },
        lowConfidence: {
          correct: lowConfCorrect,
          total: lowConfTotal,
          rate: (lowConfCorrect / (lowConfTotal || 1) * 100).toFixed(1) + '%'
        }
      },
      biasDetection: {
        pattern: biasPattern,
        severity: biasSeverity,
        teamABias: meanErrorA.toFixed(3),
        teamBBias: meanErrorB.toFixed(3),
        recommendation: biasSeverity !== 'none' ? '' : ''group' 'knockout''%',
        brierScoreDelta: (parseFloat(report1.brierScore.mean) - parseFloat(report2.brierScore.mean)).toFixed(4),
        goalErrorDelta: (parseFloat(report2.goalErrors.totalMean) - parseFloat(report1.goalErrors.totalMean)).toFixed(3),
        highConfAccuracyDelta: (parseFloat(report2.confidenceAnalysis.highConfidence.rate) - parseFloat(report1.confidenceAnalysis.highConfidence.rate)).toFixed(1) + '%'
      },
      conclusion: parseFloat(report2.accuracy.outcome.rate) > parseFloat(report1.accuracy.outcome.rate)
        ? phase2 + '' + phase1
        : phase1 + ''stacked'];
    if (!acc || acc.length === 0) return null;
    var correct = acc.filter(function(x) { return x === 1; }).length;
    return {
      total: acc.length,
      correct: correct,
      accuracy: (correct / acc.length * 100).toFixed(1) + '%''winA''winB''draw''%'
            : '0%'');
    }
    if (lowBins.some(function(b) { return b.avgActual > b.avgPredicted + 0.1; })) {
      result.underConfidence = true;
      result.recommendations.push(''
      : (result.calibrationError < 0.10 ? ''
      : (result.calibrationError < 0.15 ? '')''KOR-GER', year: 2002, round: 'group', competition: '',
      favorite: 'germany', favoriteName: '', upset: 'southkorea', upsetName: '',
      oddsFavorite: 1.25, oddsUpset: 13.0,
      result: { goalsA: 1, goalsB: 0, winner: 'upset' }, score: '1-0',
      upsetFactors: ['', '', '']
    },
    {
      match: 'SAU-ARG', year: 2022, round: 'group', competition: '',
      favorite: 'argentina', favoriteName: ''saudiarabia', upsetName: ''upset' }, score: '2-1',
      upsetFactors: ['', '']
    },
    {
      match: 'SEN-FRA', year: 2002, round: 'group', competition: '',
      favorite: 'france', favoriteName: '', upset: 'senegal', upsetName: '',
      oddsFavorite: 1.30, oddsUpset: 10.0,
      result: { goalsA: 1, goalsB: 0, winner: 'upset' }, score: '1-0',
      upsetFactors: ['', '', '']
    },
    {
      match: 'CAM-ARG', year: 1990, round: 'group', competition: '',
      favorite: 'argentina', favoriteName: ''cameroon', upsetName: '',
      oddsFavorite: 1.20, oddsUpset: 12.0,
      result: { goalsA: 1, goalsB: 0, winner: 'upset' }, score: '1-0',
      upsetFactors: ['', '']
    },
    {
      match: 'USA-ENG', year: 1950, round: 'group', competition: '',
      favorite: 'england', favoriteName: ''usa', upsetName: '',
      oddsFavorite: 1.10, oddsUpset: 25.0,
      result: { goalsA: 1, goalsB: 0, winner: 'upset' }, score: '1-0',
      upsetFactors: ['', ''ALG-GER', year: 2014, round: 'group', competition: '',
      favorite: 'germany', favoriteName: '', upset: 'algeria', upsetName: '',
      oddsFavorite: 1.35, oddsUpset: 8.5,
      result: { goalsA: 2, goalsB: 1, winner: 'favorite' }, score: '',
      upsetFactors: [''GRE-POR', year: 2004, round: 'final', competition: '',
      favorite: 'portugal', favoriteName: ''greece', upsetName: '',
      oddsFavorite: 1.45, oddsUpset: 6.0,
      result: { goalsA: 1, goalsB: 0, winner: 'upset' }, score: '1-0',
      upsetFactors: ['', ''upset''upset' ? '' : (tc.nearUpset ? '' : '' && r.modelBetter; }).length;
    var upsetTotalCount = results.filter(function(r) { return r.actualResult === ''; }).length;

    return {
      testcases: results,
      summary: {
        total: results.length,
        passed: passCount,
        passRate: (passCount / results.length * 100).toFixed(1) + '%',
        upsetDetection: {
          correct: upsetCorrectCount,
          total: upsetTotalCount,
          rate: upsetTotalCount > 0 ? (upsetCorrectCount / upsetTotalCount * 100).toFixed(1) + '%' : 'N/A',
          description: ''        : '',      recommendations: [        '',        '',        '',        '': 0.15,      '': 0.12,      '': 0.18,      '': 0.15,      '': 0.10,      '': 0.08,      '': 0.07,      '': 0.12,      '': 0.08,      '': 0.05,      '',      '': 0.12,      '': 0.10,      '',      '': 0.10,      '',      ''undefined''undefined''undefined') return FiveLeagues_ROI.summarizeByOutcome(bets, outcomeType);    var filtered = bets.filter(function(b) { return b.outcome === outcomeType; });    if (filtered.length === 0) return { count: 0, wins: 0, profit: 0 };    var wins = filtered.filter(function(b) { return b.won; }).length;    var profit = filtered.reduce(function(s, b) { return s + b.profit; }, 0);    return { count: filtered.length, wins: wins, losses: filtered.length - wins, winRate: (wins / filtered.length * 100).toFixed(1) + '%''undefined') return FiveLeagues_ROI.summarizeByEdge(bets, minEdge, maxEdge);    var filtered = bets.filter(function(b) { return parseFloat(b.edge) >= minEdge && parseFloat(b.edge) < maxEdge; });    if (filtered.length === 0) return { count: 0, wins: 0, profit: 0 };    var wins = filtered.filter(function(b) { return b.won; }).length;    var profit = filtered.reduce(function(s, b) { return s + b.profit; }, 0);    return { count: filtered.length, wins: wins, winRate: (wins / filtered.length * 100).toFixed(1) + '%''undefined''undefined') return FiveLeagues_ROI.generateROIRecommendations(ROI, winRate, maxDrawdownRatio, profitFactor);    var recs = [];    if (winRate < 45) recs.push('');    if (maxDrawdownRatio > 0.3) recs.push('');    if (profitFactor < 1.5) recs.push('');    if (ROI < 5) recs.push('');    return recs.length > 0 ? recs : ['',      knockoutFactor: 1.0,      matchTypeWeight: 1.0,      matchTypeLambdaScale: 1.0,      matchTypeDrawBoost: 0.0    };    if (!matchType) return factor;    var matchTypeFactor = getMatchTypeFactor(matchType === 'knockout' ? (round || 'round16') : matchType);    factor.matchTypeWeight = matchTypeFactor.weight;    factor.matchTypeLambdaScale = matchTypeFactor.lambdaScale;    factor.matchTypeDrawBoost = matchTypeFactor.drawBoost;    factor.description = matchTypeFactor.description;    if (matchType === 'knockout') {      var teamA = TEAMS[teamAKey];      var teamB = TEAMS[teamBKey];      var attackStyleA = teamA && teamA.attack > 2.5 ? 1.05 : (teamA && teamA.attack < 1.8 ? 0.97 : 1.0);      var attackStyleB = teamB && teamB.attack > 2.5 ? 1.05 : (teamB && teamB.attack < 1.8 ? 0.97 : 1.0);      factor.lambdaModifierA = matchTypeFactor.lambdaScale * attackStyleA;      factor.lambdaModifierB = matchTypeFactor.lambdaScale * attackStyleB;      factor.drawProbModifier = 1.0 + matchTypeFactor.drawBoost;      factor.rhoModifier = 0.08;      factor.knockoutFactor = matchTypeFactor.lambdaScale;    } else if (matchType === 'group') {      factor.lambdaModifierA = matchTypeFactor.lambdaScale;      factor.lambdaModifierB = matchTypeFactor.lambdaScale;      factor.drawProbModifier = 1.0 + matchTypeFactor.drawBoost;      if (round === 3) {        factor.description = ''0-0''knockout''-''1-0'; });    if (oneZeroIndex >= 0) {      calibrated[oneZeroIndex].prob *= 1.10;    }    var zeroOneIndex = calibrated.findIndex(function(p){ return p.score === '0-1''2-0'; });    if (twoZeroIndex >= 0) {      calibrated[twoZeroIndex].prob *= 1.08;    }    var zeroTwoIndex = calibrated.findIndex(function(p){ return p.score === '0-2''1-1''knockout'' + (drawModifier > 1 ? '' : '') +                    (Math.abs(drawModifier - 1) * 100).toFixed(1) + '%''undefined' && FiveLeagues_MathUtils.poissonPMF) return FiveLeagues_MathUtils.poissonPMF(k, lambda);    if (lambda <= 0) return k === 0 ? 1 : 0;    return Math.pow(lambda, k) * Math.exp(-lambda) / factorial(k);  }  function factorial(n) {    if (typeof FiveLeagues_MathUtils !== 'undefined''undefined''%',      goalsPerShot: (goalsScored / shots).toFixed(3),      keyPasses: keyPasses,      aerialWinRate: (aerialDuelsWon / aerialDuelsTotal * 100).toFixed(1) + '%',      groundWinRate: (groundDuelsWon / groundDuelsTotal * 100).toFixed(1) + '%''%',      blockRate: (blocks / Math.max(shots, 1) * 100).toFixed(1) + '%',      goalCreatingChances: goalCreatingChances,      bigChancesMissed: bigChancesMissed,      bigChanceConversion: ((goalsScored) / Math.max(goalsScored + bigChancesMissed, 1) * 100).toFixed(1) + '%',      saves: saves,      saveRate: (saves / Math.max(goalsConceded + saves, 1) * 100).toFixed(1) + '%''sea_level''sea_level''counter' && bestA.stats.sprintSpeed > 34) playerAdjA += 0.012;        if (b.tactical === 'counter''pressing' && bestA.stats.passAccuracyPressure > 88) playerAdjA += 0.008;        if (a.tactical === 'pressing''A''A' : 'B''A''A' : 'B';        scoreProbs.forEach(function(s, idx) {      var goals = s.score.split('-''big';        bigScores.push(categorized);      } else {        categorized.category = 'tight''draw''A' && goalsB > goalsA) {        upsetScores.push(categorized);      } else if (favorite === 'B''-''form_decline',        severity: risk,        detail: '' + favForm.toFixed(2) + ')''underdog_hot',        severity: risk,        detail: '' + undForm.toFixed(2) + ')''odds_drift',          severity: 0.12,          detail: ''injury_crisis',        severity: risk,        detail: '' + (favInjury * 100).toFixed(0) + '%)''overconfidence',        severity: risk,        detail: '' + (favProb * 100).toFixed(0) + '' + eloDiff + ')''first_meeting',        severity: 0.05,        detail: '';
    if (totalRisk >= 0.30) level = '';
    else if (totalRisk >= 0.15) level = '');
    }
    if (totalRisk >= 0.25) {
      suggestions.push('');
      suggestions.push('');
    }
    if (totalRisk >= 0.35) {
      suggestions.push(''-''undefined''undefined''-'': { attack: 0.35, defence: 0.05 },
    '': { attack: 0.30, defence: 0.08 },
    '': { attack: 0.25, defence: 0.10 },
    '': { attack: 0.20, defence: 0.12 },
    '': { attack: 0.15, defence: 0.15 },
    '': { attack: 0.08, defence: 0.22 },
    '': { attack: 0.10, defence: 0.25 },
    '': { attack: 0.05, defence: 0.30 },
    '';
      var weights = POSITION_WEIGHTS[pos] || POSITION_WEIGHTS['', lineupA);
    var adjB = calculateLineupLambdaAdjustment(prediction.teamBKey || ''undefined''winA',
        label: '',
        modelProb: modelPrediction.winA,
        marketProb: implied.winA,
        diff: diffWinA,
        kelly: kellyWinA,
        odds: odds.winA,
        valueLevel: diffWinA > 0.06 ? 'high' : (diffWinA > 0.04 ? 'medium' : 'low')
      });
    }
    if (diffDraw > valueThreshold) {
      valueOpportunities.push({
        type: 'draw',
        label: '',
        modelProb: modelPrediction.draw,
        marketProb: implied.draw,
        diff: diffDraw,
        kelly: kellyDraw,
        odds: odds.draw,
        valueLevel: diffDraw > 0.06 ? 'high' : (diffDraw > 0.04 ? 'medium' : 'low')
      });
    }
    if (diffWinB > valueThreshold) {
      valueOpportunities.push({
        type: 'winB',
        label: '',
        modelProb: modelPrediction.winB,
        marketProb: implied.winB,
        diff: diffWinB,
        kelly: kellyWinB,
        odds: odds.winB,
        valueLevel: diffWinB > 0.06 ? 'high' : (diffWinB > 0.04 ? 'medium' : 'low'',
      confidence: valueOpportunities.length > 0 ? 
        (valueOpportunities[0].valueLevel === 'high' ? ' :          valueOpportunities[0].valueLevel === 'medium' ? ' : ') : ''IRN-NZL-20260616',
      competition: ''iran', teamB: 'newzealand'',
      teamAName: '', teamBName: '',
      venue: 'sofi', stadium: '',
      kickoff: '2026-06-16T09:00:00Z',
      referee: '',
      attendance: 70240,
      environment: {
        grassType: '',
        temperature: 21,
        humidity: 72,
        weather: ''
      },
      result: {
        htScore: '1-1',
        ftScore: '2-2',
        goalsA: 2,
        goalsB: 2,
        totalGoals: 4,
        halfTime: {a:1, b:1},
        wdw: 'draw',
        htWdw: 'draw',
        htft: '',
        overUnder25: 'over'
      },
      xGData: {
        xG: { iran: 1.49, newzealand: 1.24 },
        xGOT: { iran: 1.37, newzealand: 1.19 },
        xA: { iran: 1.06, newzealand: 1.12 },
        xGA: { iran: 1.19, newzealand: 1.37 }
      },
      stats: {
        possession: { iran: '49%', newzealand: '51%' },
        corners: { iran: 4, newzealand: 1 },
        shots: { iran: 17, newzealand: 14 },
        shotsOnTarget: { iran: 4, newzealand: 8 },
        shotsOffTarget: { iran: 8, newzealand: 4 },
        blockedShots: { iran: 5, newzealand: 2 },
        freeKicks: { iran: 8, newzealand: 10 },
        offsides: { iran: 2, newzealand: 0 },
        fouls: { iran: 10, newzealand: 8 },
        yellowCards: { iran: 1, newzealand: 0 },
        redCards: { iran: 0, newzealand: 0 },
        woodwork: { iran: 1, newzealand: 0 },
        passes: { iran: 409, newzealand: 446 },
        passAccuracy: { iran: '76%', newzealand: '84%' },
        bigChances: { iran: 2, newzealand: 2 },
        bigChancesMissed: { iran: 0, newzealand: 0 },
        shotsInBox: { iran: 10, newzealand: 10 },
        shotsOutsideBox: { iran: 7, newzealand: 4 },
        goalkeeperSaves: { iran: 6, newzealand: 2 },
        longPasses: { iran: 61, newzealand: 40 },
        longPassAccuracy: { iran: '44%', newzealand: '53%' },
        crosses: { iran: 19, newzealand: 12 },
        crossAccuracy: { iran: '32%', newzealand: '8%' },
        dribbles: { iran: 11, newzealand: 12 },
        dribbleSuccess: { iran: '36%', newzealand: '50%' },
        tackles: { iran: 17, newzealand: 13 },
        interceptions: { iran: 11, newzealand: 15 },
        clearances: { iran: 27, newzealand: 26 }
      },
      events: [
        {min:7, type:'goal', team:'newzealand', player:'', assist:'', xA:0.63},
        {min:32, type:'goal', team:'iran', player:'', assist:'', xA:0.24},
        {min:54, type:'goal', team:'newzealand', player:'', assist:'', xA:0.63},
        {min:64, type:'goal', team:'iran', player:'', desc:'', xA:0.58}      ],      playerXA: {        iran: [          {player:'', xA:0.58},          {player:'', xA:0.24}
        ],
        newzealand: [
          {player:'',
          {player:'', xA:0.29},
          {player:''4-4-2',          avgAge: 31.3,          marketValue: '1225',          rank: 20,          players: [            { number: 1, position: 'GK', name: '', club: ', league: ''RB', name: ', club: '', apps: 26, goals: 4, assists: 5, keyPasses: 1.2, rating: 9.2, goalsScored: 1 },            { number: 4, position: 'CB', name: '', club: '', league: ''CB', name: ', club: '', apps: 24, goals: 1, assists: 0, keyPasses: 0.2, rating: 6.2 },            { number: 5, position: 'LB', name: '', club: ''RM', name: ''DM', name: ', club: '', apps: 24, goals: 2, assists: 3, keyPasses: 0.7, rating: 6.7, subOff: 65 },            { number: 6, position: 'CM', name: ', club: '', apps: 26, goals: 3, assists: 4, keyPasses: 0.8, rating: 6.7 },            { number: 17, position: 'AM', name: ''ST', name: ', club: '', league: ''ST', name: ', club: '', apps: 30, goals: 12, assists: 6, keyPasses: 1.0, rating: 6.8, subOff: 80, captain: true }          ]        },        teamB: {          formation: '4-2-3-1',          avgAge: 28.5,          marketValue: '2280',          rank: 85,          players: [            { number: 1, position: 'GK', name: '', club: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.7 },            { number: 13, position: 'RB', name: ', club: '', league: '', apps: 23, goals: 0, assists: 1, keyPasses: 0.4, rating: 6.1, subOff: 68 },            { number: 5, position: 'CB', name: '', club: '', apps: 25, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.6 },            { number: 16, position: 'CB', name: ', club: '', league: '', apps: 26, goals: 2, assists: 2, keyPasses: 0.5, rating: 7.2 },            { number: 2, position: 'LB', name: '', club: '', apps: 24, goals: 1, assists: 1, keyPasses: 0.4, rating: 6.1, subOff: 78 },            { number: 8, position: 'DM', name: ', club: '', league: '', apps: 27, goals: 1, assists: 3, keyPasses: 0.6, rating: 6.6, subOff: 90 },            { number: 6, position: 'CM', name: '', club: '', apps: 26, goals: 2, assists: 4, keyPasses: 0.9, rating: 6.7 },            { number: 11, position: 'RW', name: '', club: ', league: '', apps: 26, goals: 8, assists: 5, keyPasses: 1.1, rating: 9.0, goalsScored: 2 },            { number: 10, position: 'AM', name: '', club: '', league: '', apps: 24, goals: 2, assists: 2, keyPasses: 0.5, rating: 5.9, subOff: 90 },            { number: 20, position: 'LW', name: '', apps: 25, goals: 4, assists: 3, keyPasses: 0.7, rating: 6.7, subOff: 68 },            { number: 9, position: 'ST', name: '', club: '', apps: 27, goals: 10, assists: 4, keyPasses: 0.8, rating: 6.5, captain: true }
          ]
        }
      },
      tacticalReview: {
        teamATactics: '',
        teamBTactics: '',
        keyFactors: [
          '',
          '',
          '',
          ''
        ],
        substitutionImpact: {
          iran: ['', '', '', ''],
          newzealand: ['', '',
          teamB: '',          pressure: ''25-09-08', competition:'', result:'', htResult:'0-0', corners:{opponent:2, iran:2}},
          {date:'25-09-04', competition:'', result:'2-2 ', htResult:'0-1', corners:{opponent:4, iran:6}},
          {date:'25-09-01', competition:'', htResult:'0-0', corners:{iran:10, opponent:2}},
          {date:'25-08-29', competition:''2-1', corners:{iran:12, opponent:1}},
          {date:'25-06-11', competition:'', result:' 3-0 ', htResult:'0-0', corners:{iran:2, opponent:6}},
          {date:'25-06-06', competition:'', result:'', htResult:'1-0', corners:{opponent:3, iran:3}},
          {date:'25-03-26', competition:'', result:' 2-2 ', htResult:'0-1', corners:{iran:6, opponent:5}},
          {date:'25-03-21', competition:'', result:''1-0', corners:{iran:4, opponent:1}},
          {date:'24-11-19', competition:'', result:'', htResult:'0-2', corners:{opponent:3, iran:3}},
          {date:'24-11-14', competition:'', result:'', htResult:'0-3', corners:{opponent:3, iran:2}}
        ],
        teamB: [
          {date:'26-03-30', competition:'', result:'4-1 ', htResult:'2-0', corners:{newzealand:7, opponent:2}},
          {date:'26-03-27', competition:'', result:'', htResult:'0-1', corners:{newzealand:2, opponent:7}},
          {date:'25-03-24', competition:'', result:''0-0', corners:{opponent:1, newzealand:13}},
          {date:'25-03-21', competition:'', result:'', htResult:'4-0', corners:{newzealand:6, opponent:0}},
          {date:'24-11-18', competition:'', result:'0-8 , htResult:'0-3', corners:{opponent:0, newzealand:17}},
          {date:'24-11-15', competition:'', result:'8-1 ', htResult:'5-1', corners:null},
          {date:'24-10-11', competition:''1-0', corners:null},
          {date:'24-06-30', competition:'', result:' 0-3 , htResult:'0-1', corners:null},
          {date:'24-06-21', competition:'', result:' 0-4 , htResult:'0-2', corners:null},
          {date:'22-06-15', competition: '', venue:''1-0 , htResult:'1-0', corners:{opponent:1, newzealand:6}}
        ]
      },
      oddsTrajectory: {
        result: {
          wdw: 'draw',
          wdwOdds: 3.38,
          asianHandicap: { handicap: -1, result: 'lose', odds: 2.09 },
          correctScore: { score: '2-2', odds: 19.00 },
          totalGoals: { goals: 4, odds: 7.00 },
          halfTimeFullTime: { result: 'draw-draw', odds: 4.40 }
        },
        european: [
          { time: '2026-06-08 10:02:19', win: 1.56, draw: 3.30, lose: 5.40 },
          { time: '2026-06-15 10:24:30', win: 1.59, draw: 3.28, lose: 5.10 },
          { time: '2026-06-15 17:26:41', win: 1.61, draw: 3.20, lose: 5.10 },
          { time: '2026-06-15 20:32:07', win: 1.59, draw: 3.28, lose: 5.10 },
          { time: '2026-06-15 21:23:07', win: 1.57, draw: 3.33, lose: 5.20 },
          { time: '2026-06-15 21:39:18', win: 1.55, draw: 3.38, lose: 5.30 }
        ],
        asianHandicap: [
          { time: '2026-06-08 10:02:19', win: 3.10, draw: 3.10, lose: 2.07 },
          { time: '2026-06-15 10:24:35', win: 3.11, draw: 3.20, lose: 2.02 },
          { time: '2026-06-15 17:26:47', win: 3.12, draw: 3.26, lose: 1.99 },
          { time: '2026-06-15 20:31:24', win: 3.05, draw: 3.26, lose: 2.02 },
          { time: '2026-06-15 21:22:59', win: 2.95, draw: 3.30, lose: 2.05 },
          { time: '2026-06-15 21:38:56', win: 2.87, draw: 3.30, lose: 2.09 }
        ],
        totalGoals: [
          { time: '2026-06-08 10:02:19', goals0: 8.50, goals1: 3.90, goals2: 3.10, goals3: 3.75, goals4: 6.50, goals5: 14.00, goals6: 25.00, goals7Plus: 40.00 },
          { time: '2026-06-15 14:33:46', goals0: 8.50, goals1: 3.80, goals2: 3.10, goals3: 3.65, goals4: 6.70, goals5: 15.00, goals6: 28.00, goals7Plus: 40.00 },
          { time: '2026-06-15 14:49:19', goals0: 8.50, goals1: 3.50, goals2: 3.10, goals3: 3.65, goals4: 7.00, goals5: 16.00, goals6: 34.00, goals7Plus: 50.00 },
          { time: '2026-06-15 20:13:15', goals0: 8.50, goals1: 3.65, goals2: 3.00, goals3: 3.65, goals4: 7.00, goals5: 16.00, goals6: 34.00, goals7Plus: 50.00 }
        ],
        halfTimeFullTime: [
          { time: '2026-06-08 10:02:19', ww: 2.46, wd: 17.00, wl: 35.00, dw: 4.00, dd: 5.00, dl: 10.50, lw: 22.00, ld: 17.00, ll: 9.00 },
          { time: '2026-06-15 10:24:40', ww: 2.50, wd: 16.50, wl: 34.00, dw: 4.15, dd: 5.00, dl: 10.00, lw: 22.00, ld: 16.50, ll: 8.50 },
          { time: '2026-06-15 18:32:20', ww: 2.57, wd: 16.50, wl: 34.00, dw: 4.15, dd: 4.75, dl: 10.00, lw: 22.00, ld: 16.50, ll: 8.50 },
          { time: '2026-06-15 19:47:18', ww: 2.60, wd: 16.50, wl: 34.00, dw: 4.05, dd: 4.60, dl: 10.50, lw: 22.00, ld: 16.50, ll: 8.80 },
          { time: '2026-06-15 20:40:37', ww: 2.65, wd: 16.50, wl: 34.00, dw: 3.90, dd: 4.40, dl: 11.00, lw: 22.00, ld: 16.50, ll: 9.40 },
          { time: '2026-06-15 21:24:55', ww: 2.60, wd: 16.50, wl: 34.00, dw: 3.85, dd: 4.40, dl: 11.50, lw: 22.00, ld: 16.50, ll: 10.00 }
        ],
        correctScore: [
          { time: '2026-06-08 10:02:19', score10: 5.25, score20: 6.00, score21: 6.70, score30: 11.00, score31: 12.00, score32: 35.00, score40: 26.00, score41: 35.00, score42: 90.00, score50: 75.00, score51: 100.00, score52: 250.00, winOther: 75.00, score00: 8.50, score11: 6.00, score22: 19.00, score33: 125.00, drawOther: 500.00, score01: 12.00, score02: 30.00, score12: 15.00, score03: 100.00, score13: 70.00, score23: 75.00, score04: 400.00, score14: 300.00, score24: 350.00, score05: 1000.00, score15: 900.00, score25: 1000.00, loseOther: 450.00 },
          { time: '2026-06-15 10:24:47', score10: 5.25, score20: 6.00, score21: 6.70, score30: 11.00, score31: 12.00, score32: 38.00, score40: 28.00, score41: 38.00, score42: 90.00, score50: 75.00, score51: 100.00, score52: 250.00, winOther: 75.00, score00: 8.50, score11: 6.00, score22: 19.00, score33: 100.00, drawOther: 400.00, score01: 12.00, score02: 30.00, score12: 15.00, score03: 90.00, score13: 70.00, score23: 70.00, score04: 350.00, score14: 250.00, score24: 300.00, score05: 900.00, score15: 800.00, score25: 900.00, loseOther: 400.00 },
          { time: '2026-06-15 14:47:19', score10: 5.25, score20: 5.80, score21: 6.50, score30: 13.00, score31: 12.00, score32: 35.00, score40: 34.00, score41: 38.00, score42: 90.00, score50: 75.00, score51: 100.00, score52: 250.00, winOther: 75.00, score00: 8.50, score11: 5.80, score22: 19.00, score33: 100.00, drawOther: 400.00, score01: 12.00, score02: 30.00, score12: 15.00, score03: 90.00, score13: 60.00, score23: 70.00, score04: 350.00, score14: 250.00, score24: 300.00, score05: 900.00, score15: 800.00, score25: 900.00, loseOther: 400.00 },
          { time: '2026-06-15 14:51:32', score10: 5.10, score20: 5.80, score21: 6.50, score30: 14.00, score31: 12.00, score32: 35.00, score40: 34.00, score41: 38.00, score42: 90.00, score50: 90.00, score51: 100.00, score52: 250.00, winOther: 75.00, score00: 8.50, score11: 5.80, score22: 19.00, score33: 100.00, drawOther: 400.00, score01: 11.00, score02: 30.00, score12: 16.00, score03: 90.00, score13: 60.00, score23: 80.00, score04: 350.00, score14: 250.00, score24: 300.00, score05: 900.00, score15: 800.00, score25: 900.00, loseOther: 400.00 },
          { time: '2026-06-15 20:13:34', score10: 5.10, score20: 5.50, score21: 6.00, score30: 14.00, score31: 12.00, score32: 38.00, score40: 38.00, score41: 38.00, score42: 90.00, score50: 100.00, score51: 100.00, score52: 250.00, winOther: 90.00, score00: 8.50, score11: 5.80, score22: 19.00, score33: 100.00, drawOther: 400.00, score01: 12.00, score02: 32.00, score12: 17.00, score03: 90.00, score13: 60.00, score23: 80.00, score04: 350.00, score14: 250.00, score24: 300.00, score05: 900.00, score15: 800.00, score25: 900.00, loseOther: 400.00 }
        ]
      }
    },
    {
      matchId: 'BEL-EGY-20260616',
      competition: ''belgium', teamB: 'egypt'',
      teamAName: '',
      venue: 'usa_west', stadium: '',
      kickoff: '2026-06-16T09:00:00Z',
      referee: '',
      attendance: 68700,
      environment: {
        grassType: '',
        roofType: '',
        temperature: 31,
        feelsLike: 34,
        humidity: 33,
        weather: '',
        notes: ''
      },
      keyData: {
        rank: { belgium: 9, egypt: 29 },
        squadValue: { belgium: '', egypt: '' },
        avgAge: { belgium: 29.2, egypt: 29.4 },
        formations: { belgium: '4-2-3-1', egypt: '4-2-3-1' }
      },
      result: {
        htScore: '0-1', ftScore: '1-1',
        goalsA: 1, goalsB: 1, totalGoals: 2,
        halfTime: {a:0, b:1},
        wdw: 'draw', htWdw: 'winB', htft: '',
        handicap: 'winB', overUnder25: 'under',
        goals: [
          {min:34, type:'goal', team:'egypt', player:'', xA: null},
          {min:88, type:'own_goal', team:'egypt', player:'', desc:'', xA: null}
        ]
      },
      xGData: {
        xG: { belgium: 1.85, egypt: 0.62 },
        xGOT: { belgium: 1.71, egypt: 0.58 },
        xA: { belgium: 1.42, egypt: 0.31 },
        xGA: { belgium: 0.58, egypt: 1.71 }
      },
      stats: {
        possession: { belgium: '65%', egypt: '35%' },
        corners: { belgium: 12, egypt: 4 },
        shots: { belgium: 22, egypt: 8 },
        shotsOnTarget: { belgium: 8, egypt: 3 },
        shotsOffTarget: { belgium: 9, egypt: 3 },
        blockedShots: { belgium: 5, egypt: 2 },
        freeKicks: { belgium: 14, egypt: 8 },
        offsides: { belgium: 3, egypt: 2 },
        fouls: { belgium: 9, egypt: 13 },
        yellowCards: { belgium: 1, egypt: 3 },
        redCards: { belgium: 0, egypt: 0 },
        woodwork: { belgium: 1, egypt: 0 },
        passes: { belgium: 586, egypt: 318 },
        passAccuracy: { belgium: '89%', egypt: '76%' },
        bigChances: { belgium: 3, egypt: 1 },
        bigChancesMissed: { belgium: 2, egypt: 0 },
        shotsInBox: { belgium: 14, egypt: 4 },
        shotsOutsideBox: { belgium: 8, egypt: 4 },
        goalkeeperSaves: { belgium: 2, egypt: 7 },
        longPasses: { belgium: 52, egypt: 38 },
        longPassAccuracy: { belgium: '48%', egypt: '32%' },
        crosses: { belgium: 32, egypt: 11 },
        crossAccuracy: { belgium: '31%', egypt: '27%' },
        dribbles: { belgium: 14, egypt: 8 },
        dribbleSuccess: { belgium: '71%', egypt: '63%' },
        tackles: { belgium: 8, egypt: 15 },
        interceptions: { belgium: 6, egypt: 11 },
        clearances: { belgium: 18, egypt: 41 }
      },
      lineups: {
        teamA: {
          formation: '4-2-3-1',
          coach: '',
          starters: [
            {num:1, name:'', club:'', apps:27, goals:0, assists:0, keyPasses:0.1, rating:6.9, position:'', notes:'},
            {num:15, name:'', league:'', apps:24, goals:2, assists:3, keyPasses:0.6, rating:6.1, position:', notes:'',
            {num:25, name:'', league:'', apps:26, goals:2, assists:1, keyPasses:0.3, rating:7.1, position:''},
            {num:4, name:'', league:'', apps:25, goals:2, assists:2, keyPasses:0.5, rating:7.7, position:'', notes:''},
            {num:21, name:'', club:'', apps:26, goals:3, assists:5, keyPasses:0.9, rating:7.8, position:'',
            {num:24, name:'', apps:27, goals:3, assists:2, keyPasses:0.8, rating:6.9, position:'', substituted:56, notes:''},
            {num:8, name:'', club:'', apps:28, goals:4, assists:7, keyPasses:1.2, rating:7.6, position:'', captain:true, notes:''},
            {num:10, name:'', club:'', apps:29, goals:11, assists:8, keyPasses:1.3, rating:7.1, position:'', notes:''},
            {num:7, name:'', club:'', league:'', apps:30, goals:9, assists:14, keyPasses:1.9, rating:6.5, position:'', substituted:86, notes:''},
            {num:11, name:'', club:'', league:'', apps:26, goals:7, assists:6, keyPasses:1.4, rating:6.5, position:'', substituted:86, notes:''},
            {num:17, name:'', club:'', league:'', apps:28, goals:10, assists:4, keyPasses:0.8, rating:7.3, position:'', substituted:66, notes:'','','']        },        teamB: {          formation: '4-2-3-1',          coach: '',          starters: [            {num:23, name:'', notes:'', club:''},            {num:14, name:', club:'', apps:24, goals:1, assists:1, keyPasses:0.3, rating:6.8, position:''88'},            {num:2, name:'', club:'', league:', apps:26, goals:1, assists:0, keyPasses:0.2, rating:6.7, position:'', notes:''-', club:', league:'', ownGoal:true, yellowCard:true, notes:''},            {num:19, name:'', yellowCard:true, notes:'', club:'', league:'', notes:'', club:'', league:'', apps:23, goals:5, assists:4, keyPasses:0.8, rating:6.1, position:'', substituted:76, notes:''},            {num:8, name:'', notes:'', apps:30, goals:14, assists:11, keyPasses:1.6, rating:6.8, position:'', captain:true, substituted:76, yellowCard:true, notes:''},            {num:22, name:'', club:'', league:'', apps:28, goals:12, assists:5, keyPasses:0.7, rating:7.0, position:'', notes:''}          ],          substitutions: [],          keyPlayers: ['','','']        }      },      tacticalAnalysis: {        summary: '',        turningPoint: '',        teamAGrade: 'B',        teamBGrade: 'B+',        manOfMatch: {player:''egypt', rating:7.9},        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '-0',          secondHalf: '1-1-1'        },        playerRatingSummary: {          highest: [            {player:', team:'egypt', rating:7.9}
          ],
          belgiumTop: [
            {player:'', rating:7.8},
            {player:'',
            {player:'', rating:7.6},
            {player:'', rating:7.3},
            {player:'',
            {player:'', rating:7.1}
          ],
          egyptTop: [
            {player:'',
            {player:'', rating:7.0},
            {player:'', rating:7.0}
          ],
          lowest: [
            {player:''belgium', rating:6.1},
            {player:'', team:'egypt', rating:6.0}
          ]
        },
        teamTactics: {
          teamA: '',
          teamB: ''
        },
        substitutionImpact: '',
        drawKeyFactors: ''
      },
      injuries: {
        teamA: [],
        teamB: [],
        notes: ''
      },
      preMatchAnalysis: {
        motivation: {
          teamA: {
            objective: '',
            weakness: '',            bottomLine: ''          }        },        externalFactors: {          squadValue: '',          tacticalMatchup: {            teamBHazard: '',            teamAShortboard: '',
            teamB: ''
          }
        }
      },
      recentMatches: {
        teamA: [
          {date:'25-11-17', competition:'', result:'', htResult:'1-0', corners:{belgium:5, opponent:3}},
          {date:'25-11-14', competition:'', result:' 1-1 , htResult:'0-0', corners:{opponent:4, belgium:6}},
          {date:'25-09-08', competition:'', result:' 2-0 , htResult:'1-0', corners:{opponent:7, belgium:2}},
          {date:'25-09-05', competition:'', result:'3-0 ', htResult:'1-0', corners:{belgium:8, opponent:1}},
          {date:'25-06-10', competition:'', result:'', htResult:'2-0', corners:{belgium:10, opponent:2}},
          {date:'25-06-06', competition:'', result:'2-2 , htResult:'1-1', corners:{opponent:4, belgium:5}},
          {date:'25-03-26', competition:'', result:'2-1 ', htResult:'1-0', corners:{belgium:6, opponent:3}},
          {date:'25-03-22', competition:'', result:''0-2', corners:{opponent:1, belgium:9}},
          {date:'24-11-19', competition:'', result:'', htResult:'2-0', corners:{belgium:7, opponent:2}},
          {date:'24-11-15', competition:'', result:' 1-3 , htResult:'0-1', corners:{opponent:4, belgium:6}}
        ],
        teamB: [
          {date:'25-12-20', competition:'', result:''0-0', corners:{egypt:5, opponent:3}},
          {date:'25-12-16', competition:''1-1', corners:{egypt:4, opponent:6}},
          {date:'25-12-12', competition:'', result:'', htResult:'1-0', corners:{opponent:5, egypt:4}},
          {date:'25-12-09', competition:'', result:'', htResult:'1-0', corners:{egypt:8, opponent:2}},
          {date:'25-10-15', competition:'', result:' 2-0 ', htResult:'1-0', corners:{egypt:6, opponent:1}},
          {date:'25-10-11', competition:'', result:'', htResult:'0-0', corners:{opponent:2, egypt:5}},
          {date:'25-09-08', competition:'', result:' 0-2 ', htResult:'0-1', corners:{opponent:1, egypt:7}},
          {date:'25-09-04', competition:'', result:''2-0', corners:{egypt:9, opponent:0}},
          {date:'25-06-15', competition:'', result:'', htResult:'1-0', corners:{egypt:5, opponent:3}},
          {date:'25-06-09', competition:'', result:' 0-2 ', htResult:'0-1', corners:{opponent:2, egypt:6}}        ]      },      oddsTrajectory: {        result: {          wdw: 'draw',          wdwOdds: 3.60,          asianHandicap: { handicap: -1, result: 'lose', odds: 2.15 },          correctScore: { score: '1-1', odds: 7.50 },          totalGoals: { goals: 2, odds: 3.20 },          halfTimeFullTime: { result: 'lose-draw', odds: 5.50 }        },        european: [          { time: '2026-06-08 10:02:19', win: 1.45, draw: 4.00, lose: 6.50 },          { time: '2026-06-15 10:15:30', win: 1.48, draw: 3.85, lose: 6.20 },          { time: '2026-06-15 18:22:41', win: 1.52, draw: 3.70, lose: 6.00 },          { time: '2026-06-15 21:18:07', win: 1.55, draw: 3.60, lose: 5.80 }        ],        asianHandicap: [          { time: '2026-06-08 10:02:19', win: 2.95, draw: 3.15, lose: 2.10 },          { time: '2026-06-15 10:15:35', win: 2.90, draw: 3.20, lose: 2.12 },          { time: '2026-06-15 18:22:47', win: 2.85, draw: 3.25, lose: 2.15 },          { time: '2026-06-15 21:17:59', win: 2.80, draw: 3.30, lose: 2.18 }        ],        totalGoals: [          { time: '2026-06-08 10:02:19', goals0: 9.00, goals1: 4.20, goals2: 3.30, goals3: 3.80, goals4: 6.50, goals5: 15.00, goals6: 30.00, goals7Plus: 50.00 },          { time: '2026-06-15 15:30:46', goals0: 9.00, goals1: 4.10, goals2: 3.20, goals3: 3.70, goals4: 6.80, goals5: 16.00, goals6: 32.00, goals7Plus: 55.00 },          { time: '2026-06-15 20:00:15', goals0: 9.00, goals1: 4.00, goals2: 3.10, goals3: 3.65, goals4: 7.00, goals5: 17.00, goals6: 35.00, goals7Plus: 60.00 }        ],        halfTimeFullTime: [          { time: '2026-06-08 10:02:19', ww: 2.20, wd: 14.00, wl: 30.00, dw: 3.80, dd: 5.50, dl: 12.00, lw: 25.00, ld: 14.00, ll: 10.00 },          { time: '2026-06-15 10:15:40', ww: 2.25, wd: 13.50, wl: 28.00, dw: 3.90, dd: 5.40, dl: 11.50, lw: 24.00, ld: 13.50, ll: 9.50 },          { time: '2026-06-15 20:00:20', ww: 2.30, wd: 13.00, wl: 26.00, dw: 4.00, dd: 5.20, dl: 11.00, lw: 23.00, ld: 13.00, ll: 9.00 }        ],        correctScore: [          { time: '2026-06-08 10:02:19', score10: 4.80, score20: 5.50, score21: 7.00, score30: 12.00, score31: 14.00, score32: 40.00, winOther: 80.00, score00: 9.00, score11: 7.50, score22: 22.00, score33: 150.00, drawOther: 500.00, score01: 14.00, score02: 35.00, score12: 18.00, score03: 120.00, score13: 80.00, loseOther: 500.00 }        ]      }    },    {      matchId: 'MEX-RSA-20260612',      competition: '',      teamA: 'mexico', teamB: 'southafrica',      venue: 'azteca', neutral: false,      kickoff: '2026-06-12T02:00:00Z',      referee: 'Wilton Sampaio (),      attendance: '83,000+''Estadio Azteca',        city: 'Mexico City',        grassType: 'natural''wet''13-24''cloudy''northeast''mexico''moderate''mexico_advantage''4-1-2-3',          avgRating: 7.24,          avgAge: 27.5,          players: [            { number: 1, position: '', name: '-, club: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 7.3, notes: '' },            { number: 15, position: ', name: '', club: '', apps: 26, goals: 2, assists: 3, keyPasses: 0.7, rating: 7.2, notes: ' },            { number: 3, position: '', apps: 27, goals: 2, assists: 1, keyPasses: 0.4, rating: 6.8, captain: true, redCard: true, notes: '' },            { number: 5, position: '', name: '', club: ', league: '', apps: 25, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.9, notes: '' },            { number: 23, position: '', club: '', apps: 24, goals: 1, assists: 4, keyPasses: 0.6, rating: 6.9, notes: '', name: '', club: '', league: '', apps: 26, goals: 4, assists: 5, keyPasses: 1.0, rating: 7.4, minuteSubstituted: 76, goal: true, notes: '76' },            { number: 8, position: '', name: '-', club: ', league: '', apps: 25, goals: 3, assists: 4, keyPasses: 1.1, rating: 7.1, minuteSubstituted: 66, notes: '', name: '', club: ', league: '', apps: 24, goals: 2, assists: 3, keyPasses: 0.8, rating: 6.9, minuteSubstituted: 66, yellowCard: true, notes: '' },            { number: 25, position: '', apps: 27, goals: 7, assists: 8, keyPasses: 1.3, rating: 8.2, notes: '' },            { number: 9, position: '', name: '-', club: ', league: '', apps: 29, goals: 11, assists: 4, keyPasses: 0.9, rating: 7.6, minuteSubstituted: 76, notes: '' },            { number: 16, position: '', club: '', apps: 28, goals: 9, assists: 7, keyPasses: 1.5, rating: 8.6, minuteSubstituted: 79, goal: true, yellowCard: true, notes: '','',''],
          coach: 'Javier Aguirre',
          marketValue: 25.0
        },
        teamB: {
          formation: '5-3-2',
          avgRating: 6.12,
          avgAge: 27.8,
          players: [
            { number: 1, position: '', name: '', league: '' },
            { number: 6, position: '', league: '' },
            { number: 14, position: '', club: ', league: '' },
            { number: 19, position: '', name: '', league: '' },
            { number: 21, position: '', club: '', league: '' },
            { number: 20, position: '', club: '' },
            { number: 4, position: '', club: '', league: '' },
            { number: 13, position: '', name: '', apps: 24, goals: 3, assists: 3, keyPasses: 0.8, rating: 6.6, minuteSubstituted: 61, notes: '' },
            { number: 15, position: '', name: '', club: '', league: ''6' },
            { number: 9, position: '', name: '', league: '', apps: 25, goals: 8, assists: 3, keyPasses: 0.6, rating: 5.9, minuteSubstituted: 56, notes: '','',''],
          coach: 'Hugo Broos',
          marketValue: 2.0
        }
      },
      postMatchAnalysis: {
        keyFactor: '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        xGAnalysis: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: '',          teamB: '',
        winKeyFactors: ''goal',  team:'mexico',  player:'', assist:'Erik Lira', desc:'', xG: 0.35},
        {min:42, type:'post',  team:'mexico',  player:'', desc:'',
        {min:49, type:'red',   team:'southafrica', player:'Sphephelo Sithole', desc:''last_man_foul'},
        {min:67, type:'goal',  team:'mexico',  player:'', assist:'Roberto Alvarado/Gilberto Mora', desc:'', xG: 0.45},
        {min:84, type:'red',   team:'southafrica', player:'Temba Zwane', desc:''violent_conduct', varReview: true},
        {min:92, type:'red',   team:'mexico',  player:'', desc:''reckless_foul''Alfredo Talavera', position: 'GK', status: 'injured', injury: 'knee', duration: 'out' },          { player: '', position: 'MF', status: 'injured', injury: 'ACL', duration: 'out' },          { player: '', position: 'CB', status: 'suspended', reason: 'red_card', matches: 1 }        ],        teamB: [          { player: 'Sifiso Hlanti', position: 'DF', status: 'injured', injury: 'muscle', duration: 'out' },          { player: 'Thapelo Morena', position: 'DF', status: 'injured', injury: 'hamstring', duration: 'out' },          { player: 'Sphephelo Sithole', position: 'MF', status: 'suspended', reason: 'red_card', matches: 1 },          { player: 'Temba Zwane', position: 'MF', status: 'suspended', reason: 'red_card', matches: 1 }        ],        impactAssessment: {          teamA: 'minor''moderate''2025-07-07', opponent: 'usa', result: '1-2', halfTime: '1-1', venue: 'away', competition: 'Gold Cup', corners: { a: 0, b: 12 } },          { date: '2025-07-03', opponent: 'honduras', result: '1-0', halfTime: '0-0', venue: 'neutral', competition: 'Gold Cup', corners: { a: 2, b: 5 } },          { date: '2025-06-29', opponent: 'saudiarabia', result: '2-0', halfTime: '0-0', venue: 'neutral', competition: 'Gold Cup', corners: { a: 7, b: 2 } },          { date: '2025-06-23', opponent: 'costarica', result: '0-0', halfTime: '0-0', venue: 'neutral', competition: 'Gold Cup', corners: { a: 3, b: 3 } },          { date: '2025-06-19', opponent: 'suriname', result: '0-2', halfTime: '0-0', venue: 'away', competition: 'Gold Cup', corners: { a: 3, b: 5 } },          { date: '2025-06-15', opponent: 'dominican', result: '3-2', halfTime: '1-0', venue: 'neutral', competition: 'Gold Cup', corners: { a: 5, b: 2 } },          { date: '2025-03-24', opponent: 'panama', result: '2-1', halfTime: '1-1', venue: 'neutral', competition: 'CNL', corners: { a: 3, b: 3 } },          { date: '2024-11-20', opponent: 'honduras', result: '4-0', halfTime: '1-0', venue: 'home', competition: 'CNL', corners: { a: 10, b: 1 } },          { date: '2022-12-01', opponent: 'saudiarabia', result: '1-2', halfTime: '0-0', venue: 'away', , corners: { a: 1, b: 8 } },          { date: '2022-11-27', opponent: 'argentina', result: '2-0', halfTime: '0-0', venue: 'away', , corners: { a: 4, b: 2 } }        ],        teamB: [          { date: '2026-01-05', opponent: 'cameroon', result: '1-2', halfTime: '0-1', venue: 'neutral', competition: 'AFCON', corners: { a: 6, b: 3 } },          { date: '2025-12-30', opponent: 'zimbabwe', result: '2-3', halfTime: '1-1', venue: 'away', competition: 'AFCON', corners: { a: 3, b: 3 } },          { date: '2025-12-26', opponent: 'egypt', result: '1-0', halfTime: '1-0', venue: 'away', competition: 'AFCON', corners: { a: 4, b: 7 } },          { date: '2025-12-23', opponent: 'angola', result: '2-1', halfTime: '1-1', venue: 'neutral', competition: 'AFCON', corners: { a: 1, b: 9 } },          { date: '2025-10-15', opponent: 'rwanda', result: '3-0', halfTime: '2-0', venue: 'home', competition: 'AFQ', corners: { a: 7, b: 4 } },          { date: '2025-10-11', opponent: 'zimbabwe', result: '0-0', halfTime: '0-0', venue: 'away', competition: 'AFQ', corners: { a: 4, b: 14 } },          { date: '2025-09-10', opponent: 'nigeria', result: '1-1', halfTime: '1-1', venue: 'home', competition: 'AFQ', corners: { a: 2, b: 3 } },          { date: '2025-09-06', opponent: 'lesotho', result: '0-3', halfTime: '0-1', venue: 'away', competition: 'AFQ', corners: { a: 0, b: 5 } },          { date: '2025-08-19', opponent: 'uganda', result: '3-3', halfTime: '0-1', venue: 'neutral', competition: 'CHAN', corners: { a: 6, b: 5 } },          { date: '2025-08-16', opponent: 'niger', result: '0-0', halfTime: '0-0', venue: 'away', competition: 'CHAN''high',            reasons: ['opening_match', 'group_stage', 'must_win', 'host_nation'],            description: '',            pressure: 'high'          },          teamB: {            level: 'high',            reasons: ['opening_match', 'group_stage', 'history_chance'],            description: '',            pressure: 'medium'          }        },        tactics: {          teamA: {            style: 'possession_pressing',            formation: '4-1-4-1',            strategy: ['high_pressure', 'build_up', 'wing_play', 'set_pieces'],            implementation: {              pressingIntensity: 'high',              possessionTarget: 55,              wingFocus: 'both',              setPieceQuality: 'good'            }          },          teamB: {            style: 'counter_attack',            formation: '3-5-2',            strategy: ['deep_defence', 'quick_transition', 'set_pieces'],            implementation: {              defensiveBlock: 'compact',              counterSpeed: 'medium',              aerialThreat: 'medium',              midfieldCoverage: 'limited'            }          },          tacticalMatchup: {            advantage: 'mexico',            keyBattles: ['midfield_control', 'wing_vs_fullback', 'aerial_duels']          }        },        psychology: {          teamA: {            strengths: ['home_crowd', 'altitude_experience'],            weaknesses: ['opening_curse', 'pressure'],            curse: 'opening_match_winless''medium_high'          },          teamB: {            strengths: ['underdog_mindset', 'recent_afcon_experience'],            weaknesses: ['altitude_adaptation', 'inexperience'],            confidence: 'medium'          }        },        externalFactors: {          altitude: { impact: 'significant', advantage: 'mexico', adaptation: { teamA: 'native', teamB: '2_weeks' } },          travel: { teamA: 'none', teamB: 'long' },          crowd: { attendance: 87000, support: 'mexico_strong' },          logistics: { teamA: 'home', teamB: 'away' },          weather: { impact: 'minor', condition: 'cloudy_23c''57%-61%', b: '40%-43%''2026-06-08 10:02', win: 1.34, draw: 3.92, lose: 7.85 },          { time: '2026-06-09 08:50', win: 1.31, draw: 4.10, lose: 8.30 },          { time: '2026-06-10 12:01', win: 1.30, draw: 4.15, lose: 8.40 },          { time: '2026-06-11 09:15', win: 1.27, draw: 4.35, lose: 8.90 },          { time: '2026-06-11 12:25', win: 1.26, draw: 4.45, lose: 9.00 }        ],        handicap: [          { time: '2026-06-08 10:02', hWin: 2.25, hDraw: 3.18, hLose: 2.70, line: -1 },          { time: '2026-06-08 15:30', hWin: 2.20, hDraw: 3.28, hLose: 2.70, line: -1 },          { time: '2026-06-09 08:50', hWin: 2.13, hDraw: 3.28, hLose: 2.82, line: -1 },          { time: '2026-06-10 12:02', hWin: 2.07, hDraw: 3.28, hLose: 2.93, line: -1 },          { time: '2026-06-11 09:15', hWin: 2.03, hDraw: 3.25, hLose: 3.04, line: -1 },          { time: '2026-06-11 12:25', hWin: 2.00, hDraw: 3.25, hLose: 3.11, line: -1 }        ],        totalGoals: [          { time: '2026-06-08 10:02', goals0: 9.50, goals1: 4.40, goals2: 3.05, goals3: 3.60, goals4: 6.15, goals5: 12.50, goals6: 22.00, goals7plus: 35.00 },          { time: '2026-06-11 11:11', goals0: 9.50, goals1: 4.40, goals2: 3.20, goals3: 3.60, goals4: 6.05, goals5: 12.00, goals6: 20.00, goals7plus: 30.00 },          { time: '2026-06-11 18:32', goals0: 9.50, goals1: 4.40, goals2: 3.20, goals3: 3.50, goals4: 6.05, goals5: 12.50, goals6: 22.00, goals7plus: 30.00 },          { time: '2026-06-11 20:09', goals0: 9.50, goals1: 4.40, goals2: 3.00, goals3: 3.50, goals4: 6.30, goals5: 14.00, goals6: 24.00, goals7plus: 35.00 },          { time: '2026-06-11 21:30', goals0: 10.00, goals1: 4.45, goals2: 3.00, goals3: 3.40, goals4: 6.30, goals5: 14.00, goals6: 24.00, goals7plus: 35.00 }        ],        halfTimeFullTime: [          { time: '2026-06-08 10:02', HH: 1.91, HD: 21.00, HA: 65.00, DH: 3.65, DD: 5.45, DA: 16.00, AH: 30.00, AD: 21.00, AA: 15.00 },          { time: '2026-06-09 08:51', HH: 1.88, HD: 21.00, HA: 65.00, DH: 3.60, DD: 5.60, DA: 17.00, AH: 30.00, AD: 21.00, AA: 16.00 },          { time: '2026-06-11 09:16', HH: 1.83, HD: 20.00, HA: 65.00, DH: 3.70, DD: 6.00, DA: 17.00, AH: 30.00, AD: 20.00, AA: 16.00 },          { time: '2026-06-11 12:55', HH: 1.83, HD: 19.00, HA: 60.00, DH: 3.80, DD: 6.05, DA: 17.00, AH: 28.00, AD: 19.00, AA: 16.00 },          { time: '2026-06-11 18:25', HH: 1.83, HD: 19.00, HA: 51.00, DH: 3.80, DD: 6.05, DA: 17.00, AH: 28.00, AD: 19.00, AA: 16.50 },          { time: '2026-06-11 19:09', HH: 1.83, HD: 18.00, HA: 46.00, DH: 3.80, DD: 6.10, DA: 18.00, AH: 28.00, AD: 18.00, AA: 17.50 },          { time: '2026-06-11 20:04', HH: 1.83, HD: 18.00, HA: 40.00, DH: 3.80, DD: 6.10, DA: 20.00, AH: 25.00, AD: 18.00, AA: 19.00 },          { time: '2026-06-11 20:55', HH: 1.80, HD: 18.00, HA: 40.00, DH: 3.65, DD: 6.30, DA: 23.00, AH: 25.00, AD: 18.00, AA: 22.00 },          { time: '2026-06-11 21:34', HH: 1.80, HD: 18.00, HA: 40.00, DH: 3.55, DD: 6.60, DA: 23.00, AH: 25.00, AD: 18.00, AA: 22.00 }        ],        correctScore: [          { time: '2026-06-08 10:02', score10: 5.10, score20: 5.40, score21: 6.90, score30: 8.00, score31: 12.00, score32: 35.00, score40: 17.00, score41: 27.00, score42: 90.00, score50: 50.00, score51: 75.00, score52: 200.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.00, score22: 21.00, score33: 125.00, scoreDrawOther: 600.00, score01: 14.00, score02: 50.00, score12: 21.00, score03: 200.00, score13: 100.00, score23: 100.00, score04: 600.00, score14: 500.00, score24: 500.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 500.00 },          { time: '2026-06-09 08:52', score10: 5.10, score20: 5.40, score21: 6.90, score30: 8.00, score31: 12.00, score32: 35.00, score40: 17.00, score41: 27.00, score42: 90.00, score50: 50.00, score51: 75.00, score52: 200.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.00, score22: 21.00, score33: 125.00, scoreDrawOther: 500.00, score01: 14.00, score02: 50.00, score12: 21.00, score03: 200.00, score13: 100.00, score23: 100.00, score04: 600.00, score14: 500.00, score24: 500.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 500.00 },          { time: '2026-06-10 11:56', score10: 5.10, score20: 5.25, score21: 6.50, score30: 8.00, score31: 12.00, score32: 35.00, score40: 17.00, score41: 27.00, score42: 80.00, score50: 50.00, score51: 65.00, score52: 175.00, scoreWinOther: 450.00, score00: 9.50, score11: 7.00, score22: 21.00, score33: 125.00, scoreDrawOther: 500.00, score01: 16.00, score02: 55.00, score12: 24.00, score03: 225.00, score13: 120.00, score23: 120.00, score04: 600.00, score14: 500.00, score24: 500.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 500.00 },          { time: '2026-06-11 09:16', score10: 5.10, score20: 5.00, score21: 6.25, score30: 8.00, score31: 11.50, score32: 35.00, score40: 17.00, score41: 27.00, score42: 80.00, score50: 50.00, score51: 65.00, score52: 175.00, scoreWinOther: 450.00, score00: 9.50, score11: 7.00, score22: 23.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.00, score02: 55.00, score12: 28.00, score03: 225.00, score13: 150.00, score23: 150.00, score04: 600.00, score14: 500.00, score24: 500.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 500.00 },          { time: '2026-06-11 11:13', score10: 5.50, score20: 4.75, score21: 6.00, score30: 8.00, score31: 11.50, score32: 35.00, score40: 17.00, score41: 27.00, score42: 70.00, score50: 50.00, score51: 65.00, score52: 150.00, scoreWinOther: 450.00, score00: 9.50, score11: 7.25, score22: 23.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.00, score02: 55.00, score12: 28.00, score03: 225.00, score13: 150.00, score23: 150.00, score04: 600.00, score14: 500.00, score24: 500.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 500.00 },          { time: '2026-06-11 12:16', score10: 5.50, score20: 4.50, score21: 6.00, score30: 8.00, score31: 11.50, score32: 37.00, score40: 17.50, score41: 27.00, score42: 70.00, score50: 50.00, score51: 65.00, score52: 150.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.25, score22: 23.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.00, score02: 60.00, score12: 30.00, score03: 225.00, score13: 160.00, score23: 160.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 13:09', score10: 5.50, score20: 4.50, score21: 6.00, score30: 8.00, score31: 10.50, score32: 42.00, score40: 19.00, score41: 27.00, score42: 70.00, score50: 50.00, score51: 65.00, score52: 150.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.25, score22: 23.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.00, score02: 60.00, score12: 30.00, score03: 250.00, score13: 160.00, score23: 160.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 14:48', score10: 5.60, score20: 4.50, score21: 6.00, score30: 8.00, score31: 10.50, score32: 42.00, score40: 19.00, score41: 27.00, score42: 70.00, score50: 50.00, score51: 65.00, score52: 150.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.25, score22: 23.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.00, score02: 60.00, score12: 30.00, score03: 250.00, score13: 160.00, score23: 120.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 16:01', score10: 5.60, score20: 4.50, score21: 6.00, score30: 8.00, score31: 10.50, score32: 37.00, score40: 20.00, score41: 27.00, score42: 70.00, score50: 50.00, score51: 65.00, score52: 150.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.25, score22: 23.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.00, score02: 60.00, score12: 30.00, score03: 250.00, score13: 160.00, score23: 120.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 17:28', score10: 5.60, score20: 4.25, score21: 6.00, score30: 8.00, score31: 10.00, score32: 37.00, score40: 20.00, score41: 29.00, score42: 75.00, score50: 50.00, score51: 65.00, score52: 150.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.60, score22: 25.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.50, score02: 65.00, score12: 31.00, score03: 250.00, score13: 160.00, score23: 125.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 18:23', score10: 5.60, score20: 4.25, score21: 6.00, score30: 8.00, score31: 10.00, score32: 37.00, score40: 20.00, score41: 29.00, score42: 75.00, score50: 50.00, score51: 65.00, score52: 125.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.70, score22: 26.00, score33: 125.00, scoreDrawOther: 500.00, score01: 18.50, score02: 65.00, score12: 31.00, score03: 250.00, score13: 160.00, score23: 125.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 18:29', score10: 5.65, score20: 4.25, score21: 5.75, score30: 8.00, score31: 9.25, score32: 37.00, score40: 20.00, score41: 29.00, score42: 75.00, score50: 52.00, score51: 65.00, score52: 125.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.75, score22: 27.00, score33: 125.00, scoreDrawOther: 500.00, score01: 19.00, score02: 65.00, score12: 32.00, score03: 250.00, score13: 160.00, score23: 140.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 19:09', score10: 5.65, score20: 4.25, score21: 5.75, score30: 8.00, score31: 9.00, score32: 37.00, score40: 20.00, score41: 29.00, score42: 75.00, score50: 52.00, score51: 65.00, score52: 125.00, scoreWinOther: 500.00, score00: 9.50, score11: 7.75, score22: 27.00, score33: 125.00, scoreDrawOther: 500.00, score01: 19.00, score02: 65.00, score12: 32.00, score03: 300.00, score13: 160.00, score23: 140.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 20:07', score10: 5.65, score20: 4.25, score21: 5.50, score30: 8.50, score31: 8.50, score32: 37.00, score40: 20.00, score41: 29.00, score42: 75.00, score50: 52.00, score51: 70.00, score52: 125.00, scoreWinOther: 500.00, score00: 9.50, score11: 8.75, score22: 29.00, score33: 125.00, scoreDrawOther: 500.00, score01: 19.50, score02: 70.00, score12: 32.00, score03: 300.00, score13: 160.00, score23: 140.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 20:42', score10: 5.65, score20: 4.00, score21: 5.10, score30: 8.50, score31: 7.50, score32: 40.00, score40: 23.00, score41: 34.00, score42: 80.00, score50: 80.00, score51: 80.00, score52: 150.00, scoreWinOther: 700.00, score00: 10.00, score11: 9.10, score22: 32.00, score33: 125.00, scoreDrawOther: 500.00, score01: 19.50, score02: 70.00, score12: 35.00, score03: 300.00, score13: 200.00, score23: 200.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 },          { time: '2026-06-11 21:30', score10: 5.65, score20: 4.00, score21: 5.10, score30: 8.50, score31: 7.50, score32: 40.00, score40: 23.00, score41: 34.00, score42: 80.00, score50: 80.00, score51: 80.00, score52: 150.00, scoreWinOther: 700.00, score00: 10.00, score11: 9.10, score22: 32.00, score33: 125.00, scoreDrawOther: 500.00, score01: 19.50, score02: 70.00, score12: 35.00, score03: 300.00, score13: 200.00, score23: 200.00, score04: 600.00, score14: 600.00, score24: 600.00, score05: 800.00, score15: 800.00, score25: 800.00, scoreLoseOther: 600.00 }        ],        lastUpdate: '2026-06-11 21:34:50'',        keyMoments: '',        modelComparison: '',        redCardImpact: '',        xgAnalysis: '',        nextMatchImpact: ''      }    },    {      matchId: 'KOR-CZE-20260612',      competition: ''southkorea', teamB: 'czechia'',      venue: 'akron', neutral: false,      kickoff: '2026-06-12T02:00:00Z',      referee: 'Armin Mohammed',      attendance: '44,985''Estadio Akron',        city: 'Guadalajara',        grassType: 'hybrid''good''partly_cloudy''southeast''high''korea_advantage''3-4-2-1',          avgRating: 7.18,          avgAge: 28.2,          players: [            { number: 1, position: '', name: '' },            { number: 2, position: ', name: '', league: 'K1', apps: 25, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.8, notes: '' },            { number: 4, position: '', name: ', club: '', league: '', apps: 28, goals: 2, assists: 2, keyPasses: 0.4, rating: 6.9, notes: '', name: '', league: 'K1', apps: 24, goals: 1, assists: 0, keyPasses: 0.2, rating: 6.9, yellowCard: true, notes: '' },            { number: 22, position: ', name: '', league: '', apps: 26, goals: 3, assists: 4, keyPasses: 0.7, rating: 6.6, notes: '' },            { number: 6, position: '', league: '', apps: 29, goals: 6, assists: 8, keyPasses: 1.5, rating: 8.8, minuteSubstituted: 84, goal: true, notes: '' },            { number: 8, position: '', apps: 25, goals: 4, assists: 5, keyPasses: 1.1, rating: 7.1, minuteSubstituted: 84, notes: '' },            { number: 13, position: ', name: ''FC', league: 'K1', apps: 23, goals: 2, assists: 5, keyPasses: 1.0, rating: 7.2, minuteSubstituted: 69, notes: '9' },            { number: 19, position: ', name: '', league: '', apps: 27, goals: 8, assists: 10, keyPasses: 1.4, rating: 8.2, goal: true, notes: '' },            { number: 10, position: ', name: '', league: '', apps: 26, goals: 5, assists: 6, keyPasses: 0.9, rating: 6.9, minuteSubstituted: 62, notes: '' },            { number: 7, position: '', name: '', league: '', apps: 30, goals: 14, assists: 11, keyPasses: 1.3, rating: 6.8, captain: true, yellowCard: true, minuteSubstituted: 69, notes: '' }
          ],
          substitutes: [
            { number: 9, name: '', league: '', apps: 28, goals: 12, assists: 4, keyPasses: 0.8, rating: 7.5, substitutedIn: 62, goal: true, notes: '' }          ],          keyPlayers: ['','',''],          coach: '',          marketValue: 120.0        },        teamB: {          formation: '3-4-2-1',          avgRating: 6.59,          avgAge: 27.8,          players: [            { number: 1, position: '', name: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.0, rating: 7.0, notes: '', club: '', apps: 25, goals: 3, assists: 2, keyPasses: 0.4, rating: 7.1, captain: true, goal: true, notes: '' },            { number: 4, position: '', name: '', club: '', apps: 24, goals: 1, assists: 0, keyPasses: 0.3, rating: 6.3, notes: '' },            { number: 6, position: ', name: '', club: '', league: '', apps: 22, goals: 0, assists: 1, keyPasses: 0.2, rating: 5.9, notes: '' },            { number: 20, position: '', apps: 24, goals: 4, assists: 3, keyPasses: 0.8, rating: 6.8, notes: '' },            { number: 24, position: ', name: '', league: '', apps: 23, goals: 2, assists: 2, keyPasses: 0.6, rating: 6.7, minuteSubstituted: 84, notes: '4' },            { number: 22, position: ', name: '', league: '', apps: 27, goals: 6, assists: 4, keyPasses: 0.7, rating: 6.6, notes: 'B2B' },            { number: 5, position: ', name: '', league: '', apps: 26, goals: 1, assists: 4, keyPasses: 0.5, rating: 6.4, notes: '' },            { number: 15, position: ', name: '', league: '', apps: 25, goals: 3, assists: 4, keyPasses: 0.7, rating: 6.7, minuteSubstituted: 64, notes: '64 },            { number: 17, position: '', club: '', apps: 26, goals: 4, assists: 3, keyPasses: 0.9, rating: 6.9, minuteSubstituted: 64, notes: '' },            { number: 10, position: '', name: '', club: '', league: '', apps: 28, goals: 16, assists: 5, keyPasses: 0.8, rating: 6.4, minuteSubstituted: 64, notes: '','',''],
          coach: '',
          marketValue: 85.0
        }
      },
      postMatchAnalysis: {
        keyFactor: '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        xGAnalysis: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: '',          teamB: '',
        winKeyFactors: ''goal', team:'czechia', player:'Ladislav Krejci', assist:'Vladimir Coufal', desc:'', xG: 0.25},
        {min:67, type:'goal', team:'southkorea', player:'Hwang In-beom', assist:'Lee Kang-in', desc:'', xG: 0.45, manOfMatch: true},
        {min:77, type:'disallowed', team:'czechia', player:'Tomas Soucek', desc:'', reason:'offside'},
        {min:80, type:'goal', team:'southkorea', player:'Oh Hyeon-woo', assist:'Hwang In-beom', desc:''Cho Yu-min', position: 'CB', status: 'injured', injury: 'knee', duration: 'out' },
          { player: 'Kim Tae-hyeon', position: 'DF', status: 'injured', injury: 'ankle', duration: 'match' },
          { player: 'Bae Jun-ho', position: 'FW', status: 'injured', injury: 'ankle', duration: 'bench' }
        ],
        teamB: [
          { player: 'Adam Hlozek', position: 'MF', status: 'returning', injury: 'calf', duration: '64min' }
        ],
        impactAssessment: {
          teamA: 'moderate',
          teamB: 'minor''2025-07-15', opponent: 'japan', result: '0-1', halfTime: '0-1', venue: 'home', competition: 'EAFF', corners: { a: 11, b: 2 } },
          { date: '2025-07-11', opponent: 'hongkong', result: '2-0', halfTime: '1-0', venue: 'neutral', competition: 'EAFF', corners: { a: 7, b: 1 } },
          { date: '2025-07-07', opponent: 'china', result: '3-0', halfTime: '2-0', venue: 'home', competition: 'EAFF', corners: { a: 2, b: 1 } },
          { date: '2025-06-10', opponent: 'kuwait', result: '4-0', halfTime: '1-0', venue: 'home', competition: 'WCQ', corners: { a: 14, b: 2 } },
          { date: '2025-06-06', opponent: 'iraq', result: '2-0', halfTime: '0-0', venue: 'away', competition: 'WCQ', corners: { a: 10, b: 1 } },
          { date: '2025-03-25', opponent: 'jordan', result: '1-1', halfTime: '1-1', venue: 'home', competition: 'WCQ', corners: { a: 10, b: 3 } },
          { date: '2025-03-20', opponent: 'oman', result: '1-1', halfTime: '1-0', venue: 'home', competition: 'WCQ', corners: { a: 3, b: 4 } },
          { date: '2024-11-19', opponent: 'palestine', result: '1-1', halfTime: '1-1', venue: 'neutral', competition: 'WCQ', corners: { a: 8, b: 2 } },
          { date: '2024-11-14', opponent: 'kuwait', result: '3-1', halfTime: '2-0', venue: 'away', competition: 'WCQ', corners: { a: 4, b: 0 } },
          { date: '2024-10-15', opponent: 'iraq', result: '3-2', halfTime: '1-0', venue: 'home', competition: 'WCQ', corners: { a: 1, b: 4 } }
        ],
        teamB: [
          { date: '2026-04-01', opponent: 'denmark', result: '1-1', halfTime: '1-0', venue: 'home', competition: 'EUROQ', corners: { a: 4, b: 3 } },
          { date: '2026-03-27', opponent: 'ireland', result: '2-2', halfTime: '1-2', venue: 'home', competition: 'EUROQ', corners: { a: 5, b: 8 } },
          { date: '2025-11-18', opponent: 'gibraltar', result: '6-0', halfTime: '5-0', venue: 'home', competition: 'EUROQ', corners: { a: 12, b: 1 } },
          { date: '2025-10-13', opponent: 'faroe', result: '1-2', halfTime: '0-0', venue: 'away', competition: 'EUROQ', corners: { a: 7, b: 1 } },
          { date: '2025-10-10', opponent: 'croatia', result: '0-0', halfTime: '0-0', venue: 'home', competition: 'EUROQ', corners: { a: 5, b: 6 } },
          { date: '2025-09-06', opponent: 'montenegro', result: '2-0', halfTime: '1-0', venue: 'away', competition: 'EUROQ', corners: { a: 6, b: 6 } },
          { date: '2025-06-10', opponent: 'croatia', result: '1-5', halfTime: '0-1', venue: 'away', competition: 'EUROQ', corners: { a: 6, b: 0 } },
          { date: '2025-06-07', opponent: 'montenegro', result: '2-0', halfTime: '1-0', venue: 'home', competition: 'EUROQ', corners: { a: 8, b: 4 } },
          { date: '2025-03-26', opponent: 'gibraltar', result: '4-0', halfTime: '1-0', venue: 'neutral', competition: 'EUROQ', corners: { a: 12, b: 0 } },
          { date: '2025-03-23', opponent: 'faroe', result: '2-1', halfTime: '1-0', venue: 'home', competition: 'EUROQ''2026-06-08 10:02', win: 2.43, draw: 2.84, lose: 2.74 },
          { time: '2026-06-11 12:42', win: 2.45, draw: 2.86, lose: 2.69 },
          { time: '2026-06-11 15:42', win: 2.40, draw: 2.86, lose: 2.76 },
          { time: '2026-06-11 18:09', win: 2.40, draw: 2.81, lose: 2.80 },
          { time: '2026-06-12 20:11', win: 2.45, draw: 2.81, lose: 2.75 },
          { time: '2026-06-12 20:56', win: 2.49, draw: 2.75, lose: 2.75 }
        ],
        handicap: [
          { time: '2026-06-08 10:02', hWin: 5.80, hDraw: 4.05, hLose: 1.41, line: -0.5 },
          { time: '2026-06-11 12:42', hWin: 5.95, hDraw: 4.05, hLose: 1.40, line: -0.5 },
          { time: '2026-06-11 15:42', hWin: 5.70, hDraw: 4.00, hLose: 1.42, line: -0.5 },
          { time: '2026-06-12 20:11', hWin: 5.90, hDraw: 4.00, hLose: 1.41, line: -0.5 },
          { time: '2026-06-12 20:56', hWin: 6.25, hDraw: 4.00, hLose: 1.39, line: -0.5 }
        ],
        correctScore: [
          { time: '2026-06-08 10:02', score10: 6.75, score20: 11.00, score21: 8.25, score30: 26.00, score31: 22.00, score32: 35.00, score40: 90.00, score41: 75.00, score42: 125.00, score50: 350.00, score51: 250.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.00, score11: 5.00, score22: 12.00, score33: 80.00, scoreDrawOther: 500.00, score01: 7.25, score02: 12.00, score12: 8.75, score03: 30.00, score13: 26.00, score23: 40.00, score04: 100.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 300.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 09:38', score10: 6.75, score20: 11.00, score21: 8.00, score30: 27.00, score31: 22.00, score32: 35.00, score40: 95.00, score41: 80.00, score42: 125.00, score50: 400.00, score51: 250.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.00, score11: 5.00, score22: 13.00, score33: 80.00, scoreDrawOther: 500.00, score01: 7.25, score02: 12.00, score12: 8.50, score03: 32.00, score13: 26.00, score23: 40.00, score04: 110.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 11:04', score10: 7.00, score20: 11.50, score21: 8.00, score30: 28.00, score31: 22.00, score32: 38.00, score40: 95.00, score41: 80.00, score42: 125.00, score50: 400.00, score51: 250.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.00, score11: 4.70, score22: 13.00, score33: 80.00, scoreDrawOther: 500.00, score01: 7.25, score02: 12.00, score12: 8.50, score03: 32.00, score13: 26.00, score23: 40.00, score04: 110.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 11:44', score10: 7.00, score20: 12.00, score21: 7.50, score30: 30.00, score31: 24.00, score32: 40.00, score40: 100.00, score41: 85.00, score42: 125.00, score50: 400.00, score51: 250.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.00, score11: 4.50, score22: 12.50, score33: 75.00, scoreDrawOther: 500.00, score01: 7.40, score02: 13.50, score12: 8.00, score03: 33.00, score13: 28.00, score23: 42.00, score04: 110.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 12:21', score10: 7.00, score20: 12.00, score21: 7.50, score30: 30.00, score31: 24.00, score32: 40.00, score40: 100.00, score41: 85.00, score42: 125.00, score50: 400.00, score51: 250.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.00, score11: 4.50, score22: 12.50, score33: 75.00, scoreDrawOther: 400.00, score01: 7.40, score02: 13.50, score12: 8.00, score03: 33.00, score13: 28.00, score23: 42.00, score04: 110.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 12:46', score10: 7.00, score20: 12.00, score21: 7.50, score30: 30.00, score31: 24.00, score32: 40.00, score40: 100.00, score41: 85.00, score42: 125.00, score50: 400.00, score51: 250.00, score52: 400.00, scoreWinOther: 150.00, score00: 8.50, score11: 4.50, score22: 12.00, score33: 70.00, scoreDrawOther: 400.00, score01: 7.40, score02: 13.50, score12: 8.00, score03: 33.00, score13: 28.00, score23: 42.00, score04: 110.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 13:20', score10: 7.25, score20: 12.00, score21: 7.50, score30: 30.00, score31: 25.00, score32: 40.00, score40: 100.00, score41: 85.00, score42: 135.00, score50: 450.00, score51: 275.00, score52: 400.00, scoreWinOther: 150.00, score00: 8.50, score11: 4.25, score22: 12.50, score33: 70.00, scoreDrawOther: 400.00, score01: 7.40, score02: 13.50, score12: 8.00, score03: 33.00, score13: 28.00, score23: 42.00, score04: 110.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 16:35', score10: 7.25, score20: 12.00, score21: 7.50, score30: 30.00, score31: 25.00, score32: 40.00, score40: 100.00, score41: 85.00, score42: 135.00, score50: 450.00, score51: 275.00, score52: 400.00, scoreWinOther: 150.00, score00: 8.50, score11: 4.25, score22: 12.50, score33: 65.00, scoreDrawOther: 350.00, score01: 7.50, score02: 13.50, score12: 8.00, score03: 33.00, score13: 28.00, score23: 42.00, score04: 110.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 19:16', score10: 7.50, score20: 12.00, score21: 7.00, score30: 30.00, score31: 25.00, score32: 40.00, score40: 110.00, score41: 85.00, score42: 135.00, score50: 450.00, score51: 275.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.50, score11: 4.25, score22: 12.50, score33: 65.00, scoreDrawOther: 350.00, score01: 8.00, score02: 13.50, score12: 7.50, score03: 35.00, score13: 28.00, score23: 42.00, score04: 125.00, score14: 90.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 20:05', score10: 7.80, score20: 12.00, score21: 7.00, score30: 30.00, score31: 25.00, score32: 42.00, score40: 110.00, score41: 85.00, score42: 135.00, score50: 450.00, score51: 275.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.50, score11: 4.00, score22: 12.50, score33: 65.00, scoreDrawOther: 350.00, score01: 8.25, score02: 13.50, score12: 7.50, score03: 35.00, score13: 30.00, score23: 45.00, score04: 125.00, score14: 95.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 20:47', score10: 7.80, score20: 12.00, score21: 7.00, score30: 35.00, score31: 30.00, score32: 45.00, score40: 110.00, score41: 90.00, score42: 135.00, score50: 450.00, score51: 275.00, score52: 400.00, scoreWinOther: 175.00, score00: 8.50, score11: 3.65, score22: 12.50, score33: 65.00, scoreDrawOther: 350.00, score01: 8.25, score02: 13.50, score12: 7.50, score03: 40.00, score13: 35.00, score23: 50.00, score04: 125.00, score14: 100.00, score24: 150.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 200.00 },
          { time: '2026-06-11 21:26', score10: 7.80, score20: 12.00, score21: 7.20, score30: 35.00, score31: 32.00, score32: 48.00, score40: 120.00, score41: 100.00, score42: 150.00, score50: 450.00, score51: 280.00, score52: 400.00, scoreWinOther: 200.00, score00: 9.00, score11: 3.40, score22: 12.50, score33: 65.00, scoreDrawOther: 350.00, score01: 8.25, score02: 13.50, score12: 7.50, score03: 40.00, score13: 36.00, score23: 52.00, score04: 150.00, score14: 120.00, score24: 175.00, score05: 500.00, score15: 350.00, score25: 500.00, scoreLoseOther: 250.00 }
        ],
        halfTimeFullTime: [
          { time: '2026-06-08 10:02', HH: 4.20, HD: 15.00, HA: 30.00, DH: 5.50, DD: 4.00, DA: 6.00, AH: 28.00, AD: 15.00, AA: 4.65 },
          { time: '2026-06-11 10:22', HH: 4.20, HD: 14.00, HA: 30.00, DH: 5.50, DD: 4.15, DA: 6.00, AH: 28.00, AD: 14.00, AA: 4.65 },
          { time: '2026-06-11 12:43', HH: 4.20, HD: 14.00, HA: 29.00, DH: 5.50, DD: 4.40, DA: 5.85, AH: 28.00, AD: 14.00, AA: 4.50 },
          { time: '2026-06-11 17:07', HH: 4.25, HD: 13.00, HA: 28.00, DH: 5.50, DD: 4.50, DA: 5.85, AH: 27.00, AD: 13.00, AA: 4.50 },
          { time: '2026-06-11 18:12', HH: 4.25, HD: 13.00, HA: 28.00, DH: 5.50, DD: 4.50, DA: 5.85, AH: 27.00, AD: 13.00, AA: 4.70 },
          { time: '2026-06-11 18:38', HH: 4.25, HD: 13.00, HA: 28.00, DH: 5.50, DD: 4.40, DA: 5.85, AH: 27.00, AD: 13.00, AA: 4.70 },
          { time: '2026-06-11 19:59', HH: 4.25, HD: 13.00, HA: 28.00, DH: 5.35, DD: 4.40, DA: 5.95, AH: 27.00, AD: 13.00, AA: 4.75 },
          { time: '2026-06-11 20:12', HH: 4.55, HD: 12.00, HA: 26.00, DH: 5.35, DD: 4.40, DA: 5.95, AH: 25.00, AD: 12.00, AA: 4.85 },
          { time: '2026-06-11 20:49', HH: 4.75, HD: 11.00, HA: 26.00, DH: 5.35, DD: 4.40, DA: 5.95, AH: 25.00, AD: 11.00, AA: 5.00 },
          { time: '2026-06-11 21:23', HH: 4.75, HD: 11.00, HA: 26.00, DH: 5.35, DD: 4.20, DA: 6.30, AH: 25.00, AD: 11.00, AA: 5.00 }
        ],
        totalGoals: [
          { time: '2026-06-08 10:02', over25: 2.05, under25: 1.75, goals0: 8.00, goals1: 4.10, goals2: 2.90, goals3: 3.80, goals4: 6.80, goals5: 15.00, goals6: 26.00, goals7plus: 40.00 },
          { time: '2026-06-11 12:44', over25: 2.00, under25: 1.80, goals0: 8.50, goals1: 4.10, goals2: 3.00, goals3: 3.75, goals4: 6.50, goals5: 14.00, goals6: 25.00, goals7plus: 36.00 },
          { time: '2026-06-11 17:49', over25: 1.95, under25: 1.85, goals0: 8.50, goals1: 4.15, goals2: 3.00, goals3: 3.65, goals4: 6.50, goals5: 14.50, goals6: 28.00, goals7plus: 36.00 },
          { time: '2026-06-11 20:14', over25: 1.85, under25: 1.95, goals0: 8.50, goals1: 4.15, goals2: 2.85, goals3: 3.65, goals4: 6.80, goals5: 15.00, goals6: 28.00, goals7plus: 40.00 },
          { time: '2026-06-11 21:25', over25: 1.80, under25: 2.00, goals0: 9.00, goals1: 4.15, goals2: 2.75, goals3: 3.55, goals4: 7.20, goals5: 16.00, goals6: 30.00, goals7plus: 45.00 }
        ],
        lastUpdate: '2026-06-11 21:26''extremely_high',
            reasons: ['group_stage', 'must_win', 'asian_pride', 'six_point_battle'],
            description: '',
            pressure: 'high',
            worldCupHistory: '',
            groupStageContext: ''high',            reasons: ['group_stage', 'european_reputation', 'return_after_20_years'],            description: ''medium_high'',            worldCupHistory: '',                      }        },        injuries: {          teamA: {            status: 'moderate',            keyPlayers: [              { name: ''CB', injury: '', duration: '8, impact: 'high' },              { name: ''DF', injury: '', duration: '', impact: 'medium' },              { name: ', position: 'FW', injury: '', duration: '', impact: 'low' }
            ],
            impactAssessment: ''
          },
          teamB: {
            status: 'minor',
            keyPlayers: [
              { name: ''ST', reason: '', duration: '', impact: 'medium' },
              { name: '', position: 'ST', injury: '', duration: '', impact: 'high' }
            ],
            impactAssessment: ''possession_pressing',            formation: '3-4-2-1',            strategy: ['high_pressure', 'build_up', 'wing_play', 'technical_penetration'],            implementation: {              pressingIntensity: 'high',              possessionTarget: 62,              wingFocus: 'both',              setPieceQuality: 'medium',              passingAccuracy: 87,              technicalFlow: 'excellent'            },            comment: ''          },          teamB: {            style: 'counter_attack',            formation: '3-4-2-1',            strategy: ['deep_defence', 'quick_transition', 'set_pieces', 'long_balls', 'aerial_bombing'],            implementation: {              defensiveBlock: 'compact',              counterSpeed: 'medium',              aerialThreat: 'high',              throwInWeapon: 'grenade',              avgHeight: 188,              setPieceGoalsRatio: 0.5            },            comment: ''korea',
            keyBattles: ['midfield_control', 'pressing_vs_counter', 'speed_vs_height'],
            analysis: '',              matchResult: '',              significance: ''            }          }        },        psychology: {          teamA: {            strengths: ['mental_toughness', 'never_give_up', 'home_region', 'asian_pride'],            weaknesses: ['slow_start', 'pressure_handling'],            confidence: 'high',            pressureFactor: '',            keyPlayerPressure: {              player: '',              age: 34,              worldCupCount: 4,              milestone: '',              matchPerformance: ''            }          },          teamB: {            strengths: ['european_experience', 'physicality', 'set_piece_dominance'],            weaknesses: ['complacency', 'star_player_dependency', 'world_cup_jinx'],            confidence: 'medium',            ,            starDependency: ''          }        },        externalFactors: {          altitude: { impact: 'moderate', advantage: 'neutral' },          humidity: { impact: 'high', advantage: 'korea' },          travel: { teamA: 'short', teamB: 'long' },          crowd: { attendance: 44985, support: 'korea_strong''62%', b: '38%' },        xG: { a: 1.84, b: 0.83 },        xGOT: { a: 2.0, b: 1.0 },        shots: { a: 15, b: 7 },        shotsOnTarget: { a: 6, b: 4 },        shotsOffTarget: { a: 6, b: 2 },        blockedShots: { a: 3, b: 1 },        hitWoodwork: { a: 0, b: 0 },        bigChances: { a: 4, b: 1 },        bigChancesMissed: { a: 2, b: 0 },        corners: { a: 4, b: 5 },        freeKicks: { a: 12, b: 8 },        clearances: { a: 18, b: 28 },        tackles: { a: 22, b: 28 },        interceptions: { a: 7, b: 5 },        fouls: { a: 9, b: 16 },        yellowCards: { a: 1, b: 0 },        redCards: { a: 0, b: 0 },        offsides: { a: 2, b: 3 },        throwIns: { a: 14, b: 22 },        goalkeeperSaves: { a: 3, b: 4 },        touchesInOppBox: { a: 24, b: 12 },        passes: { a: 542, b: 324 },        passAccuracy: { a: '87%', b: '71%'',        keyMoments: '',        modelComparison: '',        xgAnalysis: '',        turnaroundAnalysis: '',        playerAnalysis: {          korea: {            best: 'Hwang In-beom (1 MOTM),            standout: '',            notes: '',
            disappointment: '',
            notes: ''
          }
        },
        nextMatchImpact: ''
      }
    },
    {
      matchId: 'CZE-RSA-20260619',
      competition: ''czechia', teamB: 'southafrica'',
      teamAName: '', teamBName: '',
      venue: 'usa_east', stadium: '',
      kickoff: '2026-06-19T04:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: '',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 28,
        humidity: 72,
        weather: 'thunderstorms',
        altitude: 330,
        windSpeed: 12,
        windDirection: 'southwest',
        feelsLike: 30,
        impactAssessment: {
          humidityImpact: 'high',
          expectedEffect: 'both_affected',
          stormRisk: true,
          windImpact: 'moderate'
        }
      },
      significance: '',
      preMatchAnalysis: {
        xGData: {
          teamA: { xG: 1.54, xGOT: 1.43, xA: 1.38, xGA: 0.36 },
          teamB: { xG: 0.42, xGOT: 0.36, xA: 0.31, xGA: 1.43 }
        },
        predictedStats: {
          possession: { teamA: '63%', teamB: '37%' },
          shots: { teamA: 20, teamB: 8 },
          shotsOnTarget: { teamA: 8, teamB: 2 },
          shotsInBox: { teamA: 14, teamB: 3 },
          shotsOutsideBox: { teamA: 6, teamB: 5 },
          bigChances: { teamA: 4, teamB: 1 },
          corners: { teamA: 9, teamB: 2 },
          fouls: { teamA: 10, teamB: 19 },
          offsides: { teamA: 2, teamB: 5 },
          saves: { teamA: 2, teamB: 7 },
          passes: { teamA: 690, teamB: 330 },
          successfulPasses: { teamA: 607, teamB: 244 },
          finalThirdPasses: { teamA: 261, teamB: 45 },
          throughBalls: { teamA: 8, teamB: 1 },
          crosses: { teamA: { total: 35, successful: 9 }, teamB: { total: 10, successful: 2 } },
          tackles: { teamA: 15, teamB: 26 },
          clearances: { teamA: 13, teamB: 34 }
        },
        injuries: {
          teamA: {
            status: 'none',
            suspensions: [],
            absentees: [],
            impactAssessment: ''
          },
          teamB: {
            status: 'severe',
            suspensions: [
              { name: '', reason: '', impact: 'high' },
              { name: '', reason: '', impact: 'high' }            ],            absentees: [              { name: '', position: '', injury: '', status: '', impact: 'medium' },              { name: ', position: '', injury: '', status: ''medium' }            ],            impactAssessment: ''          }        },        tactics: {          teamA: {            formation: '3-4-2-1',            style: 'possession_pressing',            strategy: ['build_up', 'wing_play', 'set_pieces', 'aerial_bombing'],            keyPlayers: ['', ', '', ''medium',              wingFocus: 'both',              setPieceQuality: 'high',              aerialThreat: 'high'            }          },          teamB: {            formation: '3-5-2',            style: 'deep_defence_counter',            strategy: ['deep_block', 'quick_transition', 'set_pieces'],            keyPlayers: [', ''],            implementation: {              defensiveBlock: 'compact',              counterSpeed: 'medium',              aerialThreat: 'medium',              setPieceQuality: 'medium'            }          },          tacticalMatchup: {            advantage: 'czechia',            keyBattles: ['midfield_control', 'height_vs_speed', 'wing_play_vs_defence'],            analysis: '+
          }
        },
        psychology: {
          teamA: {
            motivation: 'must_win',
            confidence: 'high',
            pressure: 'extreme',
            strengths: ['european_experience', 'physicality', 'set_piece_dominance'],
            weaknesses: ['slow_central_defence', 'counter_attack_vulnerability']
          },
          teamB: {
            motivation: 'must_not_lose',
            confidence: 'medium',
            pressure: 'extreme',
            strengths: ['defensive_discipline', 'counter_attack_speed', 'mental_toughness'],
            weaknesses: ['midfield_creativity', 'small_height', 'fatigue_after_60']
          }
        },
        leagueGapAnalysis: {
          teamA: '',
          teamB: ''
        }
      },
      lineups: {
        teamA: {
          formation: '3-4-2-1',
          avgRating: 6.92,
          avgAge: 27.5,
          coach: '',
          players: [
            { number: 1, position: '', name: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 7.0, notes: '' },
            { number: 6, position: '', club: '', league: '', apps: 22, goals: 0, assists: 1, keyPasses: 0.2, rating: 6.3, notes: '', name: '', club: '', apps: 24, goals: 1, assists: 0, keyPasses: 0.3, rating: 6.6, notes: '', club: '', apps: 25, goals: 3, assists: 2, keyPasses: 0.4, rating: 7.1, captain: true, notes: '',
            { number: 5, position: '', league: '', apps: 26, goals: 1, assists: 4, keyPasses: 0.5, rating: 6.9, notes: '',
            { number: 22, position: '', league: '', apps: 27, goals: 6, assists: 4, keyPasses: 0.7, rating: 7.3, notes: '' },
            { number: 24, position: '', league: '', apps: 23, goals: 2, assists: 2, keyPasses: 0.6, rating: 6.7, notes: '' },
            { number: 20, position: '', apps: 24, goals: 4, assists: 3, keyPasses: 0.8, rating: 6.8, notes: '' },
            { number: 9, position: '', name: '', club: '', apps: 26, goals: 7, assists: 4, keyPasses: 0.9, rating: 7.0, notes: '' },
            { number: 15, position: '', name: '', league: '', apps: 25, goals: 3, assists: 4, keyPasses: 0.7, rating: 6.7, notes: '' },
            { number: 10, position: '', name: '', club: '', league: '', apps: 28, goals: 16, assists: 5, keyPasses: 0.8, rating: 7.5, notes: '' }
          ],
          keyPlayers: ['','','',''],
          marketValue: 80
        },
        teamB: {
          formation: '3-5-2',
          avgRating: 6.24,
          avgAge: 26.8,
          coach: 'Hugo Broos',
          players: [
            { number: 1, position: '', name: '', league: ', apps: 29, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.3, notes: '',
            { number: 19, position: '', club: '', league: '' },
            { number: 14, position: '', name: '', club: '',
            { number: 21, position: '', club: '', league: '' },
            { number: 6, position: '', league: '', apps: 24, goals: 3, assists: 3, keyPasses: 0.8, rating: 6.6, notes: '' },
            { number: 4, position: '', name: '', league: ', apps: 24, goals: 2, assists: 2, keyPasses: 0.7, rating: 6.5, notes: '' },
            { number: 5, position: '', club: '', apps: 23, goals: 1, assists: 1, keyPasses: 0.4, rating: 6.2, notes: '',
            { number: 20, position: '', club: ', league: '' },
            { number: 15, position: '', name: '', club: '', league: '' },
            { number: 9, position: '', name: '', league: '', apps: 25, goals: 8, assists: 3, keyPasses: 0.6, rating: 6.4, notes: ' }
          ],
          keyPlayers: ['','',''],
          marketValue: 20
        }
      },
      preMatchPrediction: {
        model: 'v4.8-full-integration',
        wdw: {winA:0.72, draw:0.18, winB:0.10, recommendation:''},
        confidence: {level:', agreeCount:'6/6'},
        scores: [{score:'2-0',prob:0.22},{score:'1-0',prob:0.18},{score:'2-1',prob:0.15}],
        htft: [{ht:',ft:',prob:0.38},{ht:',ft:',prob:0.15},{ht:',ft:',prob:0.12}],
        totalGoals: {expected:2.2, over25:0.58, under25:0.42},
        modelComponents: {
          poisson: {winA:0.68, draw:0.20, winB:0.12},
          dixonCole: {winA:0.66, draw:0.22, winB:0.12},
          ssm: {winA:0.70, draw:0.18, winB:0.12},
          xgboost: {winA:0.85, draw:0.08, winB:0.07},
          lightgbm: {winA:0.80, draw:0.10, winB:0.10},
          elo: {winA:0.62, draw:0.22, winB:0.16}
        }
      },
      riskFactors: {
        teamA: {
          high: ['central_defence_pace', 'counter_attack_exposure'],
          medium: ['attacking_fluency_vs_block', 'set_piece_defence']
        },
        teamB: {
          high: ['midfield_creativity', 'aerial_defence', 'fatigue'],
          medium: ['goalkeeper_experience', 'defensive_width']
        }
      },
      recentMatches: {
        teamA: [
          { date: '2026-06-12', competition: ''neutral', opponent: '', result: '1-2', htResult: '0-0', corners: { teamA: 5, teamB: 4 }, outcome: 'loss' },
          { date: '2026-04-01', competition: ''home', opponent: '', result: '1-1', htResult: '1-0', corners: { teamA: 4, teamB: 3 }, outcome: 'draw' },
          { date: '2026-03-27', competition: ''home', opponent: ''2-2', htResult: '1-2', corners: { teamA: 5, teamB: 8 }, outcome: 'draw' },
          { date: '2025-11-18', competition: ''home', opponent: '', result: '6-0', htResult: '5-0', corners: { teamA: 12, teamB: 1 }, outcome: 'win' },
          { date: '2025-10-13', competition: ''away', opponent: '', result: '1-2', htResult: '0-0', corners: { teamA: 7, teamB: 1 }, outcome: 'loss' },
          { date: '2025-10-10', competition: ''home', opponent: '', result: '0-0', htResult: '0-0', corners: { teamA: 5, teamB: 6 }, outcome: 'draw' },
          { date: '2025-09-06', competition: ''away', opponent: '', result: '2-0', htResult: '1-0', corners: { teamA: 6, teamB: 6 }, outcome: 'win' },
          { date: '2025-06-10', competition: ''away', opponent: '', result: '1-5', htResult: '0-1', corners: { teamA: 6, teamB: 0 }, outcome: 'loss' },
          { date: '2025-06-07', competition: ''home', opponent: '', result: '2-0', htResult: '1-0', corners: { teamA: 8, teamB: 4 }, outcome: 'win' },
          { date: '2025-03-26', competition: ''neutral', opponent: '', result: '4-0', htResult: '1-0', corners: { teamA: 12, teamB: 0 }, outcome: 'win' }        ],        teamB: [          { date: '2026-06-12', competition: ''neutral', opponent: ''0-2', htResult: '0-1', corners: { teamA: 1, teamB: 3 }, outcome: 'loss' },          { date: '2026-01-05', competition: ''neutral', opponent: '', result: '1-2', htResult: '0-1', corners: { teamA: 6, teamB: 3 }, outcome: 'loss' },          { date: '2025-12-30', competition: ', venue: 'neutral', opponent: '', result: '3-2', htResult: '1-1', corners: { teamA: 3, teamB: 3 }, outcome: 'win' },          { date: '2025-12-26', competition: ''neutral', opponent: '', result: '0-1', htResult: '0-1', corners: { teamA: 7, teamB: 4 }, outcome: 'loss' },          { date: '2025-12-23', competition: ', venue: 'neutral', opponent: ''2-1', htResult: '1-1', corners: { teamA: 1, teamB: 9 }, outcome: 'win' },          { date: '2025-10-15', competition: ', venue: 'home', opponent: ''3-0', htResult: '2-0', corners: { teamA: 7, teamB: 4 }, outcome: 'win' },          { date: '2025-10-11', competition: ', venue: 'away', opponent: '', result: '0-0', htResult: '0-0', corners: { teamA: 14, teamB: 4 }, outcome: 'draw' },          { date: '2025-09-10', competition: ''home', opponent: '', result: '1-1', htResult: '1-1', corners: { teamA: 2, teamB: 3 }, outcome: 'draw' },          { date: '2025-09-06', competition: ', venue: 'neutral', opponent: ''3-0', htResult: '1-0', corners: { teamA: 5, teamB: 0 }, outcome: 'win' },          { date: '2025-08-19', competition: '', venue: 'home', opponent: ', result: '3-3', htResult: '0-1', corners: { teamA: 6, teamB: 5 }, outcome: 'draw' }
        ],
        teamAStats: {
          matches: 10, wins: 4, draws: 3, losses: 3, goalsFor: 19, goalsAgainst: 12, avgCorners: 7.0, avgGoals: 1.9, winRate: 40, drawRate: 30, lossRate: 30
        },
        teamBStats: {
          matches: 10, wins: 4, draws: 3, losses: 3, goalsFor: 13, goalsAgainst: 12, avgCorners: 4.6, avgGoals: 1.3, winRate: 40, drawRate: 30, lossRate: 30
        }
      },
      oddsData: {
        wdw: [
          { time: '2026-06-16 09:29:38', winA: 1.74, draw: 3.20, winB: 4.15, overround: 1.08 },
          { time: '2026-06-17 19:33:34', winA: 1.69, draw: 3.25, winB: 4.35, overround: 1.08 },
          { time: '2026-06-18 11:01:16', winA: 1.66, draw: 3.36, winB: 4.35, overround: 1.09 },
          { time: '2026-06-18 11:34:09', winA: 1.63, draw: 3.46, winB: 4.40, overround: 1.09 },
          { time: '2026-06-18 12:21:42', winA: 1.66, draw: 3.36, winB: 4.35, overround: 1.09 },
          { time: '2026-06-18 16:19:19', winA: 1.64, draw: 3.40, winB: 4.43, overround: 1.09 },
          { time: '2026-06-18 17:17:00', winA: 1.61, draw: 3.50, winB: 4.50, overround: 1.10 }
        ],
        handicap: [
          { time: '2026-06-16 09:29:38', winA: 3.36, draw: 3.40, winB: 1.86, line: -1 },
          { time: '2026-06-17 19:33:43', winA: 3.25, draw: 3.32, winB: 1.92, line: -1 },
          { time: '2026-06-18 11:34:15', winA: 3.15, draw: 3.32, winB: 1.96, line: -1 },
          { time: '2026-06-18 12:21:37', winA: 3.20, draw: 3.35, winB: 1.93, line: -1 },
          { time: '2026-06-18 15:43:32', winA: 3.12, draw: 3.35, winB: 1.96, line: -1 },
          { time: '2026-06-18 16:53:43', winA: 3.05, draw: 3.35, winB: 1.99, line: -1 },
          { time: '2026-06-18 17:17:11', winA: 3.00, draw: 3.30, winB: 2.03, line: -1 }
        ],
        totalGoals: [
          { time: '2026-06-16 09:29:38', g0: 9.25, g1: 4.25, g2: 3.10, g3: 3.65, g4: 6.20, g5: 12.50, g6: 23.00, g7p: 34.00 },
          { time: '2026-06-18 17:07:27', g0: 9.60, g1: 4.40, g2: 3.10, g3: 3.50, g4: 6.20, g5: 12.50, g6: 23.00, g7p: 34.00 }
        ],
        htft: [
          { time: '2026-06-16 09:29:38', ww: 2.70, wt: 15.00, wl: 36.00, tw: 4.30, tt: 5.00, tl: 9.00, lw: 23.00, lt: 15.00, ll: 7.35 },
          { time: '2026-06-18 11:35:04', ww: 2.55, wt: 14.00, wl: 32.00, tw: 4.40, tt: 5.30, tl: 10.00, lw: 21.00, lt: 14.00, ll: 8.00 }
        ],
        correctScore: [
          { time: '2026-06-16 09:29:38', s10: 6.25, s20: 7.00, s21: 6.75, s30: 13.00, s31: 14.00, s32: 30.00, s40: 38.00, s41: 40.00, s42: 90.00, s00: 9.25, s11: 5.25, s22: 14.00, s01: 11.00, s02: 23.00, s12: 12.00 },
          { time: '2026-06-18 17:07:52', s10: 6.50, s20: 6.25, s21: 5.85, s30: 13.00, s31: 12.00, s32: 27.00, s40: 40.00, s41: 35.00, s42: 75.00, s00: 9.60, s11: 5.75, s22: 16.00, s01: 13.00, s02: 27.00, s12: 13.50 }
        ],
        latestOdds: {
          wdw: { winA: 1.61, draw: 3.50, winB: 4.50 },
          handicap: { winA: 3.00, draw: 3.30, winB: 2.03 },
          totalGoals: { under25: 2.03, over25: 1.77 },
          impliedProb: { winA: 0.54, draw: 0.26, winB: 0.20 }
        }
      },
      matchKey: ''
    },
    {
      matchId: 'MEX-KOR-20260619',
      competition: ''mexico', teamB: 'korea'',
      teamAName: '',
      venue: 'mexico_west', stadium: '',
      kickoff: '2026-06-19T09:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: '',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 22,
        humidity: 70,
        weather: 'cloudy',
        altitude: 1560,
        windSpeed: 3,
        windDirection: 'west',
        feelsLike: 22,
        impactAssessment: {
          humidityImpact: 'medium',
          expectedEffect: 'favorable_home',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: '',
      preMatchAnalysis: {
        xGData: {
          teamA: { xG: 1.40, xGOT: 1.32, xA: 1.27, xGA: 1.18 },
          teamB: { xG: 1.25, xGOT: 1.18, xA: 1.34, xGA: 1.32 }
        },
        predictedStats: {
          possession: { teamA: '60%', teamB: '40%' },
          shots: { teamA: 18, teamB: 14 },
          shotsOnTarget: { teamA: 7, teamB: 6 },
          shotsInBox: { teamA: 13, teamB: 8 },
          shotsOutsideBox: { teamA: 5, teamB: 6 },
          bigChances: { teamA: 3, teamB: 3 },
          corners: { teamA: 8, teamB: 4 },
          fouls: { teamA: 13, teamB: 16 },
          offsides: { teamA: 3, teamB: 4 },
          saves: { teamA: 5, teamB: 6 },
          passes: { teamA: 660, teamB: 420 },
          successfulPasses: { teamA: 581, teamB: 340 },
          passAccuracy: { teamA: '88%', teamB: '81%' },
          finalThirdPasses: { teamA: 243, teamB: 102 },
          throughBalls: { teamA: 7, teamB: 8 },
          crosses: { teamA: { total: 32, successful: 9 }, teamB: { total: 15, successful: 3 } },
          tackles: { teamA: 17, teamB: 24 },
          clearances: { teamA: 16, teamB: 28 }
        },
        injuries: {
          teamA: {
            status: 'high',
            suspensions: [
              { name: '', reason: '', impact: 'critical' }            ],            absentees: [              { name: ', position: '', injury: '', status: '', impact: 'medium' }
            ],
            impactAssessment: ''low',            suspensions: [],            absentees: [              { name: ', position: '', injury: '', impact: 'low' }            ],            impactAssessment: '70
          }
        },
        tactics: {
          teamA: {
            formation: '4-3-3',
            style: 'wing_press_possession',
            strategy: ['build_up', 'wing_attack', 'aerial_bombing', 'set_pieces'],
            keyPlayers: ['', '', ''],
            implementation: {
              possessionTarget: 60,
              pressingIntensity: 'medium',
              wingFocus: 'both',
              setPieceQuality: 'high',
              aerialThreat: 'high'
            }
          },
          teamB: {
            formation: '3-4-3',
            style: 'counter_possession',
            strategy: ['midfield_control', 'quick_transition', 'set_pieces', 'individual_breackthrough'],
            keyPlayers: ['', ''],
            implementation: {
              possessionTarget: 40,
              pressingIntensity: 'medium',
              wingFocus: 'both',
              setPieceQuality: 'high',
              individualThreat: 'very_high'
            }
          },
          tacticalMatchup: {
            advantage: 'even',
            keyBattles: ['midfield_control', 'wing_battle', 'aerial_duels', 'individual_skills'],
            analysis: ''must_win',            confidence: 'high',            pressure: 'high',            strengths: ['home_advantage', 'aerial_power', 'wing_breakthrough'],            weaknesses: ['defensive_organization', 'midfield_control', 'back_line_pace']          },          teamB: {            motivation: 'must_not_lose',            confidence: 'high',            pressure: 'medium',            strengths: ['individual_skills', 'european_experience', 'midfield_engine'],            weaknesses: ['aerial_defence', 'fatigue_after_60', 'wing_back_depth']          }        },        leagueGapAnalysis: {          teamA: '',          teamB: ')()()()()
        }
      },
      lineups: {
        teamA: {
          formation: '4-3-3',
          avgRating: 7.27,
          avgAge: 26.8,
          coach: 'Jimmy Lozano',
          players: [
            { number: 1, position: '', name: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 7.3, notes: '',
            { number: 15, position: ''-', club: ', league: '', apps: 26, goals: 2, assists: 3, keyPasses: 0.7, rating: 7.2, notes: '' },
            { number: 20, position: '', club: '', apps: 25, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.9, notes: ' },
            { number: 5, position: '', club: ', league: '', apps: 26, goals: 1, assists: 1, keyPasses: 0.35, rating: 7.0, notes: '',
            { number: 23, position: '', club: ', league: '', apps: 24, goals: 1, assists: 4, keyPasses: 0.6, rating: 6.9, notes: '',
            { number: 8, position: ''-', club: ', league: '', apps: 25, goals: 3, assists: 4, keyPasses: 1.1, rating: 7.1, notes: '',
            { number: 6, position: '', club: '', league: '', apps: 26, goals: 4, assists: 5, keyPasses: 1.0, rating: 7.4, notes: 'B2B' },
            { number: 26, position: '', club: '', apps: 24, goals: 2, assists: 3, keyPasses: 0.8, rating: 6.9, notes: '' },
            { number: 25, position: '', apps: 27, goals: 7, assists: 8, keyPasses: 1.3, rating: 8.2, notes: '', name: '', club: '', apps: 29, goals: 11, assists: 4, keyPasses: 0.9, rating: 7.6, notes: '', club: '', apps: 28, goals: 9, assists: 7, keyPasses: 1.5, rating: 8.6, notes: '','','',''],          marketValue: 55        },        teamB: {          formation: '3-4-3',          avgRating: 7.48,          avgAge: 25.8,          coach: 'Hong Myung-bo',          players: [            { number: 1, position: '', name: '', league: '', apps: 24, goals: 1, assists: 0, keyPasses: 0.2, rating: 6.9, notes: '' },            { number: 4, position: '', name: '', league: '', apps: 28, goals: 2, assists: 2, keyPasses: 0.4, rating: 7.4, captain: true, notes: '' },            { number: 2, position: ', name: '', league: 'K1', apps: 25, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.8, notes: ' },            { number: 13, position: '', league: '', apps: 23, goals: 2, assists: 5, keyPasses: 1.0, rating: 7.2, notes: '', name: '', league: '', apps: 25, goals: 4, assists: 5, keyPasses: 1.1, rating: 7.1, notes: '' },            { number: 6, position: '', league: '', apps: 29, goals: 6, assists: 8, keyPasses: 1.5, rating: 8.8, notes: '' },            { number: 22, position: '', apps: 26, goals: 3, assists: 4, keyPasses: 0.7, rating: 6.6, notes: '', apps: 26, goals: 5, assists: 6, keyPasses: 0.9, rating: 6.9, notes: '', name: '', league: '', apps: 30, goals: 14, assists: 11, keyPasses: 1.3, rating: 7.8, captain: true, notes: '', league: '', apps: 27, goals: 8, assists: 10, keyPasses: 1.4, rating: 8.2, notes: '','','',''],
          marketValue: 95
        }
      },
      preMatchPrediction: {
        model: 'v4.8-full-integration',
        wdw: {winA:0.45, draw:0.30, winB:0.25, recommendation:''},
        confidence: {level:', agreeCount:'4/6'},
        scores: [{score:'1-1',prob:0.22},{score:'1-2',prob:0.16},{score:'2-1',prob:0.15}],
        htft: [{ht:',ft:',prob:0.14},{ht:',ft:',prob:0.16},{ht:',ft:',prob:0.18}],
        totalGoals: {expected:2.8, over25:0.62, under25:0.38},
        modelComponents: {
          poisson: {winA:0.42, draw:0.32, winB:0.26},
          dixonCole: {winA:0.44, draw:0.30, winB:0.26},
          ssm: {winA:0.43, draw:0.31, winB:0.26},
          xgboost: {winA:0.52, draw:0.28, winB:0.20},
          lightgbm: {winA:0.50, draw:0.29, winB:0.21},
          elo: {winA:0.46, draw:0.28, winB:0.26}
        }
      },
      riskFactors: {
        teamA: {
          high: ['defensive_organization', 'counter_attack_vulnerability'],
          medium: ['midfield_control', 'altitude_adaptation']
        },
        teamB: {
          high: ['aerial_defence', 'fatigue_after_60', 'wing_back_balance'],
          medium: ['individual_consistency', 'set_piece_defence']
        }
      },
      recentMatches: {
        teamA: [
          { date: '2026-06-12', competition: ''home', opponent: '', result: '2-0', htResult: '1-0', corners: { teamA: 3, teamB: 1 }, outcome: 'win' },
          { date: '2025-07-07', competition: ''away', opponent: '', result: '2-1', htResult: '1-1', corners: { teamA: 12, teamB: 0 }, outcome: 'win' },
          { date: '2025-07-03', competition: ''neutral', opponent: '', result: '1-0', htResult: '0-0', corners: { teamA: 2, teamB: 5 }, outcome: 'win' },
          { date: '2025-06-29', competition: ''neutral', opponent: ''2-0', htResult: '0-0', corners: { teamA: 7, teamB: 2 }, outcome: 'win' },
          { date: '2025-06-23', competition: ''neutral', opponent: ''0-0', htResult: '0-0', corners: { teamA: 3, teamB: 3 }, outcome: 'draw' },
          { date: '2025-06-19', competition: ''neutral', opponent: ''2-0', htResult: '0-0', corners: { teamA: 5, teamB: 3 }, outcome: 'win' },
          { date: '2025-06-15', competition: ''neutral', opponent: ''3-2', htResult: '1-0', corners: { teamA: 5, teamB: 2 }, outcome: 'win' },
          { date: '2025-03-24', competition: ''neutral', opponent: ''2-1', htResult: '1-1', corners: { teamA: 3, teamB: 3 }, outcome: 'win' },
          { date: '2022-12-01', competition: ''neutral', opponent: ''2-1', htResult: '0-0', corners: { teamA: 8, teamB: 1 }, outcome: 'win' },
          { date: '2022-11-27', competition: ''neutral', opponent: ''0-2', htResult: '0-0', corners: { teamA: 2, teamB: 4 }, outcome: 'loss' }
        ],
        teamB: [
          { date: '2026-06-12', competition: ''neutral', opponent: '', result: '2-1', htResult: '0-0', corners: { teamA: 4, teamB: 5 }, outcome: 'win' },
          { date: '2025-07-15', competition: ''home', opponent: '', result: '0-1', htResult: '0-1', corners: { teamA: 11, teamB: 2 }, outcome: 'loss' },
          { date: '2025-07-11', competition: ''neutral', opponent: '', result: '2-0', htResult: '0-1', corners: { teamA: 7, teamB: 1 }, outcome: 'win' },
          { date: '2025-07-07', competition: ''home', opponent: '', result: '3-0', htResult: '2-0', corners: { teamA: 2, teamB: 1 }, outcome: 'win' },
          { date: '2025-06-10', competition: ''home', opponent: ''4-0', htResult: '1-0', corners: { teamA: 14, teamB: 2 }, outcome: 'win' },
          { date: '2025-06-06', competition: ''away', opponent: ''2-0', htResult: '0-0', corners: { teamA: 10, teamB: 1 }, outcome: 'win' },
          { date: '2025-03-25', competition: ''home', opponent: '', result: '1-1', htResult: '1-1', corners: { teamA: 10, teamB: 3 }, outcome: 'draw' },
          { date: '2025-03-20', competition: ''home', opponent: '', result: '1-1', htResult: '1-0', corners: { teamA: 3, teamB: 4 }, outcome: 'draw' },
          { date: '2024-11-19', competition: ''neutral', opponent: '', result: '1-1', htResult: '1-1', corners: { teamA: 8, teamB: 2 }, outcome: 'draw' },
          { date: '2024-11-14', competition: ''away', opponent: ', result: '3-1', htResult: '0-2', corners: { teamA: 4, teamB: 0 }, outcome: 'win' }
        ],
        teamAStats: {
          matches: 10, wins: 8, draws: 1, losses: 1, goalsFor: 16, goalsAgainst: 7, avgCorners: 5.2, avgGoals: 1.6, winRate: 80, drawRate: 10, lossRate: 10
        },
        teamBStats: {
          matches: 10, wins: 6, draws: 3, losses: 1, goalsFor: 17, goalsAgainst: 5, avgCorners: 7.3, avgGoals: 1.7, winRate: 60, drawRate: 30, lossRate: 10
        }
      },
      oddsData: {
        wdw: [
          { time: '2026-06-16 09:29:38', winA: 1.86, draw: 3.00, winB: 3.87, overround: 1.08 },
          { time: '2026-06-18 11:14:47', winA: 1.87, draw: 2.96, winB: 3.90, overround: 1.09 },
          { time: '2026-06-18 13:26:17', winA: 1.89, draw: 2.91, winB: 3.90, overround: 1.10 },
          { time: '2026-06-18 17:11:31', winA: 1.91, draw: 2.86, winB: 3.90, overround: 1.11 }
        ],
        handicap: [
          { time: '2026-06-16 09:29:38', winA: 3.78, draw: 3.45, winB: 1.74, line: -1 },
          { time: '2026-06-18 10:41:56', winA: 3.87, draw: 3.38, winB: 1.74, line: -1 },
          { time: '2026-06-18 11:14:53', winA: 3.92, draw: 3.38, winB: 1.73, line: -1 },
          { time: '2026-06-18 13:26:25', winA: 4.05, draw: 3.32, winB: 1.72, line: -1 },
          { time: '2026-06-18 17:11:41', winA: 4.16, draw: 3.32, winB: 1.70, line: -1 }
        ],
        totalGoals: [
          { time: '2026-06-16 09:29:38', g0: 8.00, g1: 4.10, g2: 3.00, g3: 3.75, g4: 6.55, g5: 14.50, g6: 26.00, g7p: 40.00 },
          { time: '2026-06-18 18:14:28', g0: 8.80, g1: 4.35, g2: 3.10, g3: 3.80, g4: 5.90, g5: 12.00, g6: 23.00, g7p: 35.00 }
        ],
        htft: [
          { time: '2026-06-16 09:29:38', ww: 3.05, wt: 15.00, wl: 30.00, tw: 4.50, tt: 4.50, tl: 8.25, lw: 25.00, lt: 15.00, ll: 6.50 },
          { time: '2026-06-18 14:40:23', ww: 3.20, wt: 13.50, wl: 26.00, tw: 4.50, tt: 4.65, tl: 8.25, lw: 23.00, lt: 13.50, ll: 6.50 }
        ],
        correctScore: [
          { time: '2026-06-16 09:29:38', s10: 6.00, s20: 7.50, s21: 7.00, s30: 16.00, s31: 16.00, s32: 30.00, s00: 8.00, s11: 5.10, s22: 15.00, s01: 9.25, s02: 20.00, s12: 10.50 },
          { time: '2026-06-18 18:14:54', s10: 6.60, s20: 8.80, s21: 6.25, s30: 19.00, s31: 16.50, s32: 23.00, s00: 8.80, s11: 5.05, s22: 12.50, s01: 10.00, s02: 23.00, s12: 11.50 }
        ],
        latestOdds: {
          wdw: { winA: 1.91, draw: 2.86, winB: 3.90 },
          handicap: { winA: 4.16, draw: 3.32, winB: 1.70 },
          totalGoals: { under25: 3.80, over25: 2.65 },
          impliedProb: { winA: 0.48, draw: 0.32, winB: 0.20 }
        }
      },
      events: [
        {min:4, type:'yellow', team:'korea', player:''},
        {min:50, type:'goal', team:'mexico', player:'', assist:'', desc:''},
        {min:57, type:'sub', team:'korea', playerOut:''},
        {min:57, type:'sub', team:'korea', playerOut:''},
        {min:58, type:'yellow', team:'korea', player:''},
        {min:71, type:'sub', team:'mexico', playerOut:'', playerIn:'', desc:'',
        {min:71, type:'sub', team:'mexico', playerOut:'', playerIn:'', desc:'',
        {min:71, type:'sub', team:'korea', playerOut:'', desc:''},
        {min:71, type:'sub', team:'korea', playerOut:'', desc:''sub', team:'korea', playerOut:'', desc:''},
        {min:80, type:'sub', team:'mexico', playerOut:'', desc:'},
        {min:80, type:'sub', team:'mexico', playerOut:'', playerIn:'', desc:'',
        {min:84, type:'sub', team:'mexico', playerOut:'', playerIn:'', desc:''},
        {min:87, type:'save', team:'mexico', player:''},
        {min:87, type:'save', team:'mexico', player:''}
      ],
      result: {
        htScore: '0-0', ftScore: '1-0',
        goalsA: 1, goalsB: 0, totalGoals: 1,
        halfTime: {a:0, b:0},
        wdw: 'winA', htWdw: 'draw', htft: '',
        handicap: 'draw', overUnder25: 'under',
        redCards: {mexico:0, korea:0}
      },
      lineups: {
        teamA: {
          formation: '4-1-2-3', coach: 'Jimmy Lozano',
          avgRating: 7.18, avgAge: 28.3,
          starting: [
            {num:1, name:'', league:'', apps:28, goals:0, assists:0, keyPasses:0.1, rating:8.0, position:'', notes:'',
            {num:2, name:'', league:'', apps:26, goals:2, assists:3, keyPasses:0.7, rating:6.6, position:''},
            {num:4, name:'', league:'', apps:27, goals:2, assists:2, keyPasses:0.4, rating:7.3, position:', captain:true, notes:''},
            {num:5, name:'', club:'', apps:26, goals:1, assists:1, keyPasses:0.35, rating:7.1, position:', notes:'',
            {num:23, name:'', club:'', apps:24, goals:1, assists:4, keyPasses:0.6, rating:7.0, position:', notes:''},
            {num:6, name:'', club:'', league:'', apps:26, goals:4, assists:5, keyPasses:1.0, rating:6.4, position:''},
            {num:26, name:'', club:'', apps:24, goals:2, assists:3, keyPasses:0.8, rating:7.1, position:'', substituted:71, notes:''},
            {num:7, name:'', club:'', apps:25, goals:5, assists:6, keyPasses:1.2, rating:8.0, position:'', substituted:71, notes:'},
            {num:25, name:'', league:'', apps:27, goals:7, assists:8, keyPasses:1.3, rating:6.7, position:''0'},
            {num:9, name:'', club:'', apps:29, goals:11, assists:4, keyPasses:0.9, rating:6.4, position:'', substituted:80, notes:''},
            {num:16, name:'', club:'', apps:28, goals:9, assists:7, keyPasses:1.5, rating:6.6, position:', substituted:84, notes:''}
          ],
          keyPlayers: ['','','','']
        },
        teamB: {
          formation: '3-4-3', coach: 'Hong Myung-bo',
          avgRating: 6.74, avgAge: 29.0,
          starting: [
            {num:1, name:'', league:'', notes:''},
            {num:3, name:'', league:'', apps:24, goals:1, assists:0, keyPasses:0.2, rating:6.9, position:''},
            {num:4, name:'', league:'', apps:28, goals:2, assists:2, keyPasses:0.4, rating:6.9, position:'', captain:true, notes:'', league:'', apps:25, goals:1, assists:1, keyPasses:0.3, rating:6.6, position:''},
            {num:22, name:'', apps:26, goals:3, assists:4, keyPasses:0.7, rating:6.3, position:''},
            {num:6, name:'', league:'', apps:29, goals:6, assists:8, keyPasses:1.5, rating:6.5, position:', notes:''},
            {num:8, name:'', league:'', apps:25, goals:4, assists:5, keyPasses:1.1, rating:6.3, position:''},
            {num:15, name:''FC', league:'K1', apps:23, goals:2, assists:5, keyPasses:1.0, rating:6.5, position:', substituted:71, notes:''},
            {num:10, name:'', league:'', apps:26, goals:5, assists:6, keyPasses:0.9, rating:6.7, position:''57'},
            {num:7, name:'', league:'', apps:30, goals:14, assists:11, keyPasses:1.3, rating:6.8, position:'', captain:true, substituted:57, notes:''},
            {num:19, name:'', league:'', apps:27, goals:8, assists:10, keyPasses:1.4, rating:7.0, position:', yellowCard:true, notes:''}
          ],
          keyPlayers: ['','','','']
        }
      },
      stats: {
        possession: {mexico:'42%', korea:'58%'},
        shots: {mexico:8, korea:9},
        shotsOnTarget: {mexico:4, korea:2},
        shotsOffTarget: {mexico:4, korea:7},
        blockedShots: {mexico:4, korea:3},
        shotsInBox: {mexico:5, korea:3},
        shotsOutsideBox: {mexico:3, korea:6},
        bigChances: {mexico:2, korea:3},
        bigChancesMissed: {mexico:1, korea:3},
        corners: {mexico:0, korea:2},
        freeKicks: {mexico:4, korea:7},
        fouls: {mexico:9, korea:7},
        offsides: {mexico:3, korea:6},
        yellowCards: {mexico:0, korea:2},
        redCards: {mexico:0, korea:0},
        saves: {mexico:3, korea:2},
        passes: {mexico:429, korea:579},
        successfulPasses: {mexico:350, korea:486},
        passAccuracy: {mexico:'82%', korea:'84%'},
        finalThirdPasses: {mexico:72, korea:161},
        throughBalls: {mexico:3, korea:6},
        crosses: {mexico:{total:12, successful:2}, korea:{total:21, successful:4}},
        dangerousAttacks: {mexico:17, korea:40},
        tackles: {mexico:22, korea:16},
        clearances: {mexico:29, korea:14}
      },
      xGData: {
        mexico: {xG:0.48, xGOT:0.44, xA:0.41, xGA:0.63},
        korea: {xG:0.69, xGOT:0.63, xA:0.75, xGA:0.44}
      },
      goalDetails: [
        {min:50, scorer:'', assist:'', xA:0.41, type:''}
      ],
      xARanking: {
        mexico: [
          {name:'', xA:0.41},
          {name:'', xA:0.40},          {name:'', xA:0.12}
        ]
      },
      halfTimeStats: {
        htScore: '0-0',
        xG: {mexico:0.19, korea:0.38},
        shots: {mexico:3, korea:5},
        shotsOnTarget: {mexico:1, korea:1},
        possession: {mexico:'40%', korea:'60%'}
      },
      secondHalfStats: {
        xG: {mexico:0.29, korea:0.31},
        shots: {mexico:5, korea:4},
        shotsOnTarget: {mexico:3, korea:1},
        possession: {mexico:'44%', korea:'56%'}
      },
      accuracy: {
        wdw: true, score: true, htft: true,
        totalGoals: false, overUnder25: true, handicap: false,
        total: '4/6', brierScore: 0.35
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          '',
          ''4-1-2-3',          korea: '3-4-3
        },
        rootCauses: [
          '',
          '',
          '',
          '',
          ''
        ],
        modelImprovement: [
          '',
          '',
          ''
        ],
        groupStandings: ''50.0.0',        tacticalSummary: '-1-2-33-4-3',        playerOfMatch: '( - 8.0',        lessonsLearned: '',        xGAnalysis: 'xG 0.481xG 0.690),        tacticalKey: '+++',        leagueGapAnalysis: '()()(),        goalKeyMoments: {          firstHalf: '60%1-0',          secondHalf: '087'        },        playerRatingSummary: {          highest: '.0',          teamABest: '.0.07.3.1.1',          teamBBest: '7.06.96.96.8',          lowest: '.3'        },        teamTactics: {          teamA: '-1-2-3',          teamB: '3-4-3
        },
        substitutionImpact: '',
        oddsResults: {
          wdw: {result:', odds:1.94, payout:'1.94},
          handicap: {result:'(-1), odds:3.32, payout:'3.32},
          correctScore: {result:'1:0', odds:7.00, payout:'7},
          totalGoals: {result:'1, odds:4.35, payout:'4.35},
          htft: {result:'', odds:4.70, payout:'4.70}        }      },      matchKey: ''USA-AUS-20260620',
      competition: ''usa', teamB: 'australia'',
      teamAName: '', teamBName: '',
      venue: 'usa_west', stadium: '',
      kickoff: '2026-06-19T19:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: 'Seattle',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 26,
        humidity: 40,
        weather: 'cloudy',
        altitude: 4,
        windSpeed: 10,
        windDirection: 'west',
        feelsLike: 26,
        impactAssessment: {
          humidityImpact: 'low',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: '',
      finalScore: { teamA: 2, teamB: 0 },
      halfTimeScore: { teamA: 2, teamB: 0 },
      events: [
        { min: 11, type: 'own_goal', team: 'australia', player: '' },
        { min: 43, type: 'goal', team: 'usa', player: '', desc: '' },
        { min: 46, type: 'substitution', team: 'australia', playerIn: '', playerOut: '', desc: '' },
        { min: 46, type: 'substitution', team: 'australia', playerIn: '', playerOut: '', desc: '',
        { min: 61, type: 'substitution', team: 'australia', playerIn: ''-', desc: ' },
        { min: 68, type: 'goal', team: 'usa', player: '', desc: '' },
        { min: 74, type: 'substitution', team: 'usa', playerIn: '', playerOut: '', desc: '',
        { min: 78, type: 'substitution', team: 'australia', playerIn: '', playerOut: '', desc: '',
        { min: 80, type: 'substitution', team: 'usa', playerIn: '', playerOut: '' },
        { min: 80, type: 'substitution', team: 'usa', playerIn: '', desc: '',
        { min: 90, type: 'substitution', team: 'usa', playerIn: '', playerOut: '' }      ],      result: {        htScore: '2-0',        ftScore: '2-0',        goalsA: 2,        goalsB: 0,        totalGoals: 2,        halfTime: { a: 2, b: 0 },        wdw: 'winA',        htWdw: 'winA',        htft: '',        handicap: 'win',        overUnder25: 'under',        redCards: { usa: 0, australia: 0 }      },      lineups: {        teamA: {          formation: '4-2-3-1',          players: [            { number: 24, position: '', name: ', club: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 6.8, notes: '' },            { number: 16, position: '', apps: 25, goals: 4, assists: 5, keyPasses: 0.9, rating: 8.3, goal: true, notes: '', apps: 27, goals: 2, assists: 1, keyPasses: 0.35, rating: 7.4, notes: '', name: '', club: '', apps: 26, goals: 1, assists: 2, keyPasses: 0.3, rating: 7.0, captain: true, notes: '', apps: 27, goals: 3, assists: 6, keyPasses: 0.8, rating: 7.2, minuteSubstituted: 80, notes: '' },            { number: 4, position: '', name: ', club: '', apps: 26, goals: 2, assists: 3, keyPasses: 0.8, rating: 7.0, notes: ' },            { number: 17, position: '', name: '', league: '', apps: 28, goals: 7, assists: 8, keyPasses: 1.2, rating: 7.5, notes: '', league: '', apps: 24, goals: 3, assists: 4, keyPasses: 1.0, rating: 6.9, minuteSubstituted: 80, notes: '' },            { number: 8, position: '', name: '', league: '', apps: 27, goals: 5, assists: 4, keyPasses: 0.9, rating: 6.8, minuteSubstituted: 90, notes: '', club: '', league: '', apps: 25, goals: 8, assists: 3, keyPasses: 0.7, rating: 6.2, minuteSubstituted: 74, notes: '', name: '', apps: 28, goals: 13, assists: 5, keyPasses: 0.8, rating: 6.4, notes: '' }          ],          substitutes: [            { number: 21, name: '', club: '', league: '', apps: 24, goals: 6, assists: 3, keyPasses: 0.9, rating: 6.5, minuteOn: 80, notes: '', club: '', league: '', apps: 26, goals: 4, assists: 7, keyPasses: 1.4, rating: 6.8, minuteOn: 74, notes: ' },            { number: 18, name: ''MLS', apps: 23, goals: 0, assists: 1, keyPasses: 0.3, rating: 6.5, minuteOn: 80, notes: '', club: ', league: 'MLS', apps: 22, goals: 0, assists: 2, keyPasses: 0.6, rating: 6.3, minuteOn: 90, notes: ''5-4-1',          players: [            { number: 18, position: '', name: '-', club: '', league: '', apps: 27, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.5, notes: '' },            { number: 5, position: ', name: '', club: '', apps: 25, goals: 1, assists: 2, keyPasses: 0.4, rating: 6.5, notes: '' },            { number: 21, position: ', name: ''FC', league: '', apps: 24, goals: 1, assists: 0, keyPasses: 0.2, rating: 5.5, minuteSubstituted: 46, notes: ' },            { number: 19, position: '', name: '', club: '', league: '', apps: 28, goals: 3, assists: 1, keyPasses: 0.3, rating: 6.3, captain: true, notes: '' },            { number: 3, position: '', club: '', apps: 23, goals: 1, assists: 1, keyPasses: 0.4, rating: 7.4, notes: '', name: '', club: '', apps: 22, goals: 0, assists: 1, keyPasses: 0.3, rating: 6.0, notes: ' },            { number: 23, position: '', club: '', league: '', apps: 24, goals: 3, assists: 2, keyPasses: 0.5, rating: 6.1, minuteSubstituted: 46, notes: '', club: '', apps: 25, goals: 2, assists: 4, keyPasses: 0.75, rating: 6.9, minuteSubstituted: 78, notes: '' },            { number: 13, position: '', league: '', apps: 23, goals: 1, assists: 2, keyPasses: 0.4, rating: 6.6, notes: '' },            { number: 7, position: '', club: '', league: '', apps: 26, goals: 4, assists: 3, keyPasses: 0.6, rating: 6.3, minuteSubstituted: 61, notes: '' },            { number: 9, position: '', name: '-', club: ', league: '', apps: 26, goals: 9, assists: 2, keyPasses: 0.6, rating: 6.7, minuteSubstituted: 46, notes: '' }
          ],
          substitutes: [
            { number: 15, name: '', club: '', apps: 23, goals: 5, assists: 1, keyPasses: 0.5, rating: 6.4, minuteOn: 46, notes: ' },
            { number: 22, name: '', club: '', apps: 22, goals: 2, assists: 1, keyPasses: 0.4, rating: 6.2, minuteOn: 46, notes: ' },
            { number: 14, name: ''FC', league: '', apps: 21, goals: 3, assists: 2, keyPasses: 0.5, rating: 6.3, minuteOn: 61, notes: ' },
            { number: 16, name: '', club: '', apps: 20, goals: 2, assists: 1, keyPasses: 0.4, rating: 6.2, minuteOn: 78, notes: ' }
          ],
          avgRating: 6.38,
          avgAge: 25.3,
          marketValue: 43.75,
          worldRanking: 23
        }
      },
      stats: {
        possession: { usa: '62%', australia: '38%' },
        shots: { usa: 10, australia: 5 },
        shotsOnTarget: { usa: 2, australia: 2 },
        shotsOffTarget: { usa: 8, australia: 3 },
        blockedShots: { usa: 3, australia: 5 },
        shotsInBox: { usa: 7, australia: 2 },
        shotsOutsideBox: { usa: 3, australia: 3 },
        bigChances: { usa: 2, australia: 0 },
        bigChancesMissed: { usa: 1, australia: 0 },
        corners: { usa: 7, australia: 4 },
        freeKicks: { usa: 9, australia: 6 },
        fouls: { usa: 12, australia: 16 },
        offsides: { usa: 1, australia: 0 },
        yellowCards: { usa: 3, australia: 4 },
        redCards: { usa: 0, australia: 0 },
        woodwork: { usa: 0, australia: 0 },
        passes: { usa: 542, australia: 336 },
        passAccuracy: { usa: 86, australia: 77 },
        finalThirdPasses: { usa: 201, australia: 44 },
        throughBalls: { usa: 7, australia: 2 },
        crosses: { usa: 28, australia: 13 },
        crossAccuracy: { usa: 8, australia: 2 },
        tackles: { usa: 14, australia: 22 },
        interceptions: { usa: 22, australia: 16 },
        clearances: { usa: 13, australia: 31 },
        saves: { usa: 2, australia: 1 }
      },
      xGData: {
        xG: { usa: 1.56, australia: 0.47 },
        xGOT: { usa: 1.42, australia: 0.41 },
        xA: { usa: 1.28, australia: 0.35 },
        xGA: { usa: 0.41, australia: 1.42 }
      },
      xARanking: {
        usa: [
          { name: '',
          { name: '',
          { name: '', xA: 0.35 },          { name: ', xA: 0.14 }
        ]
      },
      halfTimeStats: {
        htScore: '2-0',
        xG: { usa: 0.98, australia: 0.16 },
        shots: { usa: 6, australia: 1 },
        shotsOnTarget: { usa: 2, australia: 0 },
        possession: { usa: '70%', australia: '30%' }
      },
      secondHalfStats: {
        xG: { usa: 0.58, australia: 0.31 },
        shots: { usa: 4, australia: 4 },
        shotsOnTarget: { usa: 0, australia: 2 },
        possession: { usa: '54%', australia: '46%' }
      },
      accuracy: {
        wdw: true, score: false, htft: true,
        totalGoals: false, overUnder25: true, handicap: true,
        total: '4/6', brierScore: 0.38
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          '',
          ''
        ],
        tacticalAnalysis: {
          usa: '',
          australia: '': .5',          ': 6.0.5',          ' 6.16.6',          ': 6.7',          ': 6.5'        ],        modelImprovement: [          ': <6.5',          ': ',          ' +
        ],
        groupStandings: ''.3.5.4.5',        tacticalSummary: '4-2-3-15-4-1',        playerOfMatch: 'A-) - 8.3',        lessonsLearned: '',        xGAnalysis: 'xG 1.562)xG 0.470),        tacticalKey: '+++',        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '113',          secondHalf: '321
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: ''4-2-3-1+',          teamB: '5-4-1+
        },
        substitutionImpact: '',
        winKeyFactors: '', odds:1.44, payout:'1.44},        handicap: {result:'(-1), odds:2.53, payout:'2.53},        correctScore: {result:'2:0', odds:7.00, payout:'7},        totalGoals: {result:'2, odds:3.65, payout:'3.65},        htft: {result:'', odds:2.15, payout:'2.15}
      }
    },
    {
      matchId: 'SCO-MAR-20260620',
      competition: ''scotland', teamB: 'morocco'',
      teamAName: ''usa_east', stadium: '',
      kickoff: '2026-06-19T22:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: 'Boston',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 22,
        humidity: 47,
        weather: 'sunny',
        altitude: 3,
        windSpeed: 12,
        windDirection: 'northeast',
        feelsLike: 20,
        impactAssessment: {
          humidityImpact: 'low',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: '',
      finalScore: { teamA: 0, teamB: 1 },
      halfTimeScore: { teamA: 0, teamB: 1 },
      events: [
        { min: 2, type: 'goal', team: 'morocco', player: '' },
        { min: 36, type: 'yellow', team: 'scotland', player: '' },
        { min: 52, type: 'save', team: 'morocco', player: '', desc: '',
        { min: 52, type: 'post', team: 'morocco', player: '' },
        { min: 60, type: 'substitution', team: 'scotland', playerIn: ', playerOut: ', desc: '' },
        { min: 71, type: 'substitution', team: 'scotland', playerIn: ', playerOut: '', desc: '' },
        { min: 71, type: 'substitution', team: 'scotland', playerIn: ', playerOut: '',
        { min: 84, type: 'substitution', team: 'morocco', playerIn: ', playerOut: '',
        { min: 84, type: 'substitution', team: 'morocco', playerIn: ', playerOut: '' },
        { min: 84, type: 'substitution', team: 'morocco', playerIn: ', playerOut: '' },
        { min: 89, type: 'substitution', team: 'scotland', playerIn: ', playerOut: '' },
        { min: 89, type: 'substitution', team: 'scotland', playerIn: ', playerOut: '', desc: '' },
        { min: 90, type: 'substitution', team: 'morocco', playerIn: ', playerOut: ', desc: ''0-1',        ftScore: '0-1',        goalsA: 0,        goalsB: 1,        totalGoals: 1,        halfTime: { a: 0, b: 1 },        wdw: 'winB',        htWdw: 'winB',        htft: '',        handicap: 'draw',        overUnder25: 'under',        redCards: { scotland: 0, morocco: 0 }      },      lineups: {        teamA: {          formation: '3-5-2',          players: [            { number: 1, position: '', name: '', club: '', league: '', apps: 27, goals: 0, assists: 0, keyPasses: 0.1, rating: 6.8, notes: ' },            { number: 13, position: '', apps: 25, goals: 2, assists: 2, keyPasses: 0.3, rating: 7.6, notes: '', name: '', club: '', apps: 26, goals: 1, assists: 1, keyPasses: 0.3, rating: 7.1, notes: '' },            { number: 6, position: ', name: '', league: '', apps: 24, goals: 2, assists: 4, keyPasses: 0.7, rating: 6.8, minuteSubstituted: 60, notes: '0' },            { number: 22, position: ', name: '', league: '', apps: 25, goals: 3, assists: 2, keyPasses: 0.6, rating: 6.3, minuteSubstituted: 89, notes: '', name: '', club: '', apps: 26, goals: 4, assists: 3, keyPasses: 0.7, rating: 6.3, minuteSubstituted: 71, notes: '1' },            { number: 19, position: '', name: ', club: '', apps: 28, goals: 6, assists: 5, keyPasses: 1.0, rating: 7.3, notes: ' },            { number: 7, position: '', club: '', apps: 27, goals: 5, assists: 8, keyPasses: 1.2, rating: 6.7, minuteSubstituted: 89, notes: '', name: '', league: '', apps: 28, goals: 2, assists: 7, keyPasses: 1.1, rating: 6.4, yellowCard: true, notes: '', name: ', club: '', league: '', apps: 29, goals: 7, assists: 4, keyPasses: 0.8, rating: 6.7, yellowCard: true, notes: '', name: ', club: '', league: '', apps: 26, goals: 9, assists: 3, keyPasses: 0.7, rating: 6.7, minuteSubstituted: 71, notes: '' }
          ],
          substitutes: [
            { number: 15, name: '', club: '', apps: 23, goals: 3, assists: 1, keyPasses: 0.5, rating: 6.5, minuteOn: 71, notes: ' },
            { number: 9, name: '', league: '', apps: 22, goals: 2, assists: 1, keyPasses: 0.4, rating: 6.4, minuteOn: 71, notes: '' }
          ],
          avgRating: 6.8,
          avgAge: 29.6,
          marketValue: 109,
          worldRanking: 37
        },
        teamB: {
          formation: '4-2-3-1',
          players: [
            { number: 1, position: '', name: '', club: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.5, notes: '',
            { number: 3, position: '', club: ', league: '', apps: 26, goals: 3, assists: 5, keyPasses: 0.9, rating: 7.3, notes: '',
            { number: 18, position: ''-, club: '', league: '', apps: 25, goals: 1, assists: 1, keyPasses: 0.35, rating: 7.3, notes: '' },
            { number: 14, position: '', name: '', league: '', apps: 28, goals: 2, assists: 1, keyPasses: 0.3, rating: 6.8, yellowCard: true, notes: '',
            { number: 2, position: '', club: '', league: '', apps: 27, goals: 4, assists: 7, keyPasses: 1.0, rating: 6.9, captain: true, yellowCard: true, notes: '' },
            { number: 6, position: '', name: '', league: '', apps: 26, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.5, notes: '' },
            { number: 24, position: '', name: '', league: '', apps: 27, goals: 3, assists: 4, keyPasses: 0.8, rating: 7.0, notes: '',
            { number: 23, position: '', club: '', league: '', apps: 25, goals: 5, assists: 6, keyPasses: 1.1, rating: 6.8, minuteSubstituted: 84, notes: '' },
            { number: 8, position: '', name: '', league: '', apps: 28, goals: 4, assists: 9, keyPasses: 1.3, rating: 6.7, minuteSubstituted: 90, notes: ' },
            { number: 10, position: '', club: '', apps: 29, goals: 11, assists: 8, keyPasses: 1.4, rating: 7.3, minuteSubstituted: 84, notes: ' },
            { number: 11, position: '', name: '', league: '', apps: 27, goals: 10, assists: 4, keyPasses: 0.7, rating: 7.8, goal: true, minuteSubstituted: 84, notes: '4' }          ],          substitutes: [            { number: 16, name: ', club: '', league: '', apps: 24, goals: 6, assists: 2, keyPasses: 0.6, rating: 6.5, minuteOn: 84, notes: '' },            { number: 7, name: '', club: '', apps: 25, goals: 3, assists: 5, keyPasses: 1.2, rating: 6.4, minuteOn: 84, notes: ''41%', morocco: '59%' },        shots: { scotland: 6, morocco: 12 },        shotsOnTarget: { scotland: 0, morocco: 2 },        shotsOffTarget: { scotland: 6, morocco: 10 },        blockedShots: { scotland: 4, morocco: 5 },        shotsInBox: { scotland: 2, morocco: 8 },        shotsOutsideBox: { scotland: 4, morocco: 4 },        bigChances: { scotland: 1, morocco: 3 },        bigChancesMissed: { scotland: 1, morocco: 2 },        corners: { scotland: 2, morocco: 5 },        freeKicks: { scotland: 6, morocco: 9 },        fouls: { scotland: 11, morocco: 8 },        offsides: { scotland: 1, morocco: 0 },        yellowCards: { scotland: 1, morocco: 1 },        redCards: { scotland: 0, morocco: 0 },        woodwork: { scotland: 0, morocco: 1 },        passes: { scotland: 454, morocco: 671 },        passAccuracy: { scotland: 82, morocco: 87 },        finalThirdPasses: { scotland: 81, morocco: 189 },        throughBalls: { scotland: 2, morocco: 7 },        crosses: { scotland: 20, morocco: 27 },        crossAccuracy: { scotland: 3, morocco: 7 },        tackles: { scotland: 20, morocco: 9 },        interceptions: { scotland: 9, morocco: 16 },        clearances: { scotland: 18, morocco: 13 },        saves: { scotland: 1, morocco: 0 }      },      xGData: {        xG: { scotland: 0.54, morocco: 0.97 },        xGOT: { scotland: 0.22, morocco: 0.91 },        xA: { scotland: 0.46, morocco: 1.08 },        xGA: { scotland: 0.91, morocco: 0.22 }      },      xARanking: {        scotland: [          { name: '', xA: 0.46 }        ],        morocco: [          { name: '', xA: 0.24 },          { name: ', xA: 0.15 }
        ]
      },
      halfTimeStats: {
        htScore: '0-1',
        xG: { scotland: 0.23, morocco: 0.62 },
        shots: { scotland: 3, morocco: 7 },
        shotsOnTarget: { scotland: 0, morocco: 1 },
        possession: { scotland: '43%', morocco: '57%' }
      },
      secondHalfStats: {
        xG: { scotland: 0.31, morocco: 0.35 },
        shots: { scotland: 3, morocco: 5 },
        shotsOnTarget: { scotland: 0, morocco: 1 },
        possession: { scotland: '39%', morocco: '61%' }
      },
      accuracy: {
        wdw: true, score: false, htft: true,
        totalGoals: false, overUnder25: true, handicap: true,
        total: '4/6', brierScore: 0.32
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          '',
          '',
          ''
        ],
        tacticalAnalysis: {
          scotland: '',
          morocco: '' .3.4',          ' 7.17.6',          ': .7',          ' .36.7.3',          ' 0
        ],
        modelImprovement: [
          '',
          '',
          ''C)3
      },
      postMatchAnalysis: {
        keyFactor: '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        xGAnalysis: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''.8',          teamABest: '-.6-.3-7.1',          teamBBest: '.8-.3.3.37.0',          lowest: '.3'        },        teamTactics: {          teamA: '-5-2',          teamB: '-2-3-184/90
        },
        substitutionImpact: '',
        winKeyFactors: '', odds:1.49, payout:'1.49},        handicap: {result:'(+1), odds:3.08, payout:'3.08},        correctScore: {result:'0:1', odds:6.05, payout:'6.05},        totalGoals: {result:'1, odds:4.40, payout:'4.40},        htft: {result:'', odds:2.50, payout:'2.50}
      }
    },
    {
      matchId: 'BRA-HAI-20260620',
      competition: ''brazil', teamB: 'haiti'',
      teamAName: '', teamBName: '',
      venue: 'usa_east', stadium: '',
      kickoff: '2026-06-20T00:30:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: 'Philadelphia',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 23,
        humidity: 43,
        weather: 'sunny',
        altitude: 12,
        windSpeed: 8,
        windDirection: 'southwest',
        feelsLike: 20,
        impactAssessment: {
          humidityImpact: 'low',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: '',
      finalScore: { teamA: 3, teamB: 0 },
      halfTimeScore: { teamA: 3, teamB: 0 },
      events: [
        { min: 23, type: 'goal', team: 'brazil', player: '', desc: '' },
        { min: 36, type: 'goal', team: 'brazil', player: '', desc: '-0' },
        { min: 40, type: 'substitution', team: 'brazil', playerIn: ', playerOut: '', desc: ''goal', team: 'brazil', player: '', assist: '' },
        { min: 46, type: 'substitution', team: 'haiti', playerIn: ', playerOut: ', desc: '' },
        { min: 46, type: 'substitution', team: 'haiti', playerIn: ', playerOut: '', desc: ' },
        { min: 62, type: 'substitution', team: 'haiti', playerIn: ', playerOut: '', desc: ' },
        { min: 63, type: 'save', team: 'brazil', player: '' },
        { min: 64, type: 'substitution', team: 'brazil', playerIn: ', playerOut: ', desc: '',
        { min: 64, type: 'substitution', team: 'brazil', playerIn: ', playerOut: ', desc: '',
        { min: 71, type: 'substitution', team: 'haiti', playerIn: ', playerOut: ', desc: '',
        { min: 81, type: 'substitution', team: 'brazil', playerIn: ', playerOut: '', desc: ' },
        { min: 81, type: 'substitution', team: 'brazil', playerIn: ', playerOut: '', desc: ' },
        { min: 81, type: 'substitution', team: 'haiti', playerIn: ', playerOut: '', desc: ' }
      ],
      result: {
        htScore: '3-0',
        ftScore: '3-0',
        goalsA: 3,
        goalsB: 0,
        totalGoals: 3,
        halfTime: { a: 3, b: 0 },
        wdw: 'winA',
        htWdw: 'winA',
        htft: '',
        handicap: 'win',
        overUnder25: 'over',
        redCards: { brazil: 0,haiti: 0 }
      },
      lineups: {
        teamA: {
          formation: '4-3-3',
          players: [
            { number: 1, position: '', name: '', league: '', apps: 30, goals: 0, assists: 0, keyPasses: 0.15, rating: 7.5, notes: '' },
            { number: 13, position: '', club: '', league: '', apps: 26, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.8, notes: '' },
            { number: 4, position: '', club: '', league: '', apps: 29, goals: 3, assists: 2, keyPasses: 0.4, rating: 7.8, captain: true, notes: ' },
            { number: 3, position: '', name: '', league: '', apps: 28, goals: 2, assists: 1, keyPasses: 0.35, rating: 7.4, notes: '',
            { number: 16, position: ''-, club: '', league: '', apps: 25, goals: 3, assists: 5, keyPasses: 0.9, rating: 6.8, notes: '',
            { number: 8, position: '', club: ', league: '', apps: 28, goals: 4, assists: 6, keyPasses: 1.1, rating: 7.4, minuteSubstituted: 81, notes: '' },
            { number: 5, position: '', name: '', club: '', league: '', apps: 29, goals: 5, assists: 7, keyPasses: 1.2, rating: 7.4, notes: '' },
            { number: 20, position: '', club: '', league: '', apps: 27, goals: 7, assists: 11, keyPasses: 1.5, rating: 7.4, goal: true, minuteSubstituted: 64, notes: '' },
            { number: 11, position: '', club: '', league: '', apps: 28, goals: 10, assists: 8, keyPasses: 1.3, rating: 6.3, minuteSubstituted: 40, notes: '40 },
            { number: 9, position: '', name: '', league: '', apps: 27, goals: 12, assists: 6, keyPasses: 0.9, rating: 9.2, goal: true, minuteSubstituted: 64, notes: '+ },
            { number: 7, position: '', club: ', league: '', apps: 32, goals: 18, assists: 10, keyPasses: 1.6, rating: 8.0, goal: true, minuteSubstituted: 81, notes: '', club: ', league: '', apps: 26, goals: 8, assists: 5, keyPasses: 1.0, rating: 6.8, minuteOn: 40, notes: '', club: ', league: '', apps: 20, goals: 5, assists: 2, keyPasses: 0.7, rating: 6.5, minuteOn: 64, notes: ''5-4-1',          players: [            { number: 1, position: '', name: '', club: '', league: '', apps: 25, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.5, captain: true, notes: ' },            { number: 8, position: '', apps: 22, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.2, notes: '' },            { number: 5, position: ', name: '', league: '', apps: 24, goals: 1, assists: 0, keyPasses: 0.2, rating: 6.0, yellowCard: true, notes: ' },            { number: 22, position: '', name: '', league: '', apps: 23, goals: 1, assists: 1, keyPasses: 0.25, rating: 6.0, notes: '' },            { number: 4, position: ', name: '', club: '', league: '', apps: 22, goals: 1, assists: 1, keyPasses: 0.4, rating: 6.7, notes: '' },            { number: 2, position: '', club: '', apps: 23, goals: 0, assists: 1, keyPasses: 0.3, rating: 6.0, yellowCard: true, minuteSubstituted: 46, notes: '', name: '', league: ', apps: 23, goals: 3, assists: 2, keyPasses: 0.5, rating: 6.3, minuteSubstituted: 71, notes: '', name: '', club: '', apps: 25, goals: 4, assists: 4, keyPasses: 0.7, rating: 6.8, minuteSubstituted: 81, notes: '' },            { number: 17, position: ', name: '', club: '', league: '', apps: 22, goals: 2, assists: 1, keyPasses: 0.4, rating: 6.4, yellowCard: true, notes: '' },            { number: 21, position: '', club: '', league: '', apps: 21, goals: 1, assists: 1, keyPasses: 0.3, rating: 5.5, minuteSubstituted: 62, notes: '' },            { number: 20, position: '', name: ', club: '', apps: 24, goals: 7, assists: 3, keyPasses: 0.6, rating: 6.5, minuteSubstituted: 46, notes: '' }
          ],
          substitutes: [
            { number: 11, name: '', club: '', league: '' },
            { number: 9, name: '', league: '' }          ],          avgRating: 6.3,          avgAge: 28.8,          marketValue: 30.4,          worldRanking: 85        }      },      stats: {        possession: { brazil: '56%', haiti: '44%' },        shots: { brazil: 8, haiti: 7 },        shotsOnTarget: { brazil: 5, haiti: 3 },        shotsOffTarget: { brazil: 3, haiti: 4 },        blockedShots: { brazil: 3, haiti: 4 },        shotsInBox: { brazil: 6, haiti: 2 },        shotsOutsideBox: { brazil: 2, haiti: 5 },        bigChances: { brazil: 3, haiti: 1 },        bigChancesMissed: { brazil: 0, haiti: 1 },        corners: { brazil: 4, haiti: 4 },        freeKicks: { brazil: 8, haiti: 6 },        fouls: { brazil: 13, haiti: 14 },        offsides: { brazil: 8, haiti: 4 },        yellowCards: { brazil: 1, haiti: 3 },        redCards: { brazil: 0, haiti: 0 },        woodwork: { brazil: 1, haiti: 0 },        passes: { brazil: 512, haiti: 396 },        passAccuracy: { brazil: 88, haiti: 83 },        finalThirdPasses: { brazil: 186, haiti: 47 },        throughBalls: { brazil: 9, haiti: 2 },        crosses: { brazil: 29, haiti: 22 },        crossAccuracy: { brazil: 8, haiti: 4 },        tackles: { brazil: 16, haiti: 21 },        interceptions: { brazil: 8, haiti: 12 },        clearances: { brazil: 12, haiti: 36 },        saves: { brazil: 2, haiti: 3 }      },      xGData: {        xG: { brazil: 1.21, haiti: 0.29 },        xGOT: { brazil: 1.15, haiti: 0.24 },        xA: { brazil: 1.46, haiti: 0.21 },        xGA: { brazil: 0.24, haiti: 1.15 }      },      xARanking: {        brazil: [          { name: '', xA: 0.72 },          { name: ', xA: 0.61 },          { name: '', xA: 0.13 }        ],        haiti: [          { name: ''-', xA: 0.09 }        ]      },      halfTimeStats: {        htScore: '3-0',        xG: { brazil: 1.21, haiti: 0.00 },        shots: { brazil: 6, haiti: 0 },        shotsOnTarget: { brazil: 5, haiti: 0 },        possession: { brazil: '62%', haiti: '38%' }      },      secondHalfStats: {        xG: { brazil: 0.00, haiti: 0.29 },        shots: { brazil: 2, haiti: 7 },        shotsOnTarget: { brazil: 0, haiti: 3 },        possession: { brazil: '51%', haiti: '49%' }      },      accuracy: {        wdw: true, score: true, htft: true,        totalGoals: true, overUnder25: true, handicap: true,        total: '6/6', brierScore: 0.08      },      review: {        summary: '3-09.2.0.4.87.47.5Brier0.08()/6',        keyMoments: [          '3: -0',          '6: -0',          '0: ',          '5: 3-0',          '6: ',          '3: ',          '4: ',          '1: 
        ],
        tacticalAnalysis: {
          brazil: '',
          haiti: '': .2.0',          ' .06.0',          ': .3.5',          ' .5',          ' 6.5'        ],        modelImprovement: [          ': ',          ': ',          ' xG<0.3'        ],        groupStandings: 'C4)43)'      },      postMatchAnalysis: {        keyFactor: '.2.0.47.87.4',        tacticalSummary: '4-3-33-0-4-1',        playerOfMatch: ') - 9.2',        lessonsLearned: '+',        xGAnalysis: 'xG 1.213)xG 0.290',        tacticalKey: '+++',        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '3-0362-0453-0',          secondHalf: '63'        },        playerRatingSummary: {          highest: '.2',          teamABest: '9.2.0.87.5/.4',          teamBBest: '.8.76.5',          lowest: '.5'        },        teamTactics: {          teamA: '4-3-30/64/81',          teamB: '5-4-1
        },
        substitutionImpact: '',
        winKeyFactors: '', odds:0, payout:''(-2), odds:1.60, payout:'1.60},        correctScore: {result:'3:0', odds:5.20, payout:'5.20},        totalGoals: {result:'3, odds:4.20, payout:'4.20},        htft: {result:'', odds:1.23, payout:'1.23}
      }
    },
    {
      matchId: 'TUR-PAR-20260620',
      competition: ''turkey', teamB: 'paraguay'',
      teamAName: '', teamBName: '',
      venue: 'usa_west', stadium: '',
      kickoff: '2026-06-20T03:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: 'San Francisco',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 17,
        humidity: 74,
        weather: 'cloudy',
        altitude: 5,
        windSpeed: 6,
        windDirection: 'west',
        feelsLike: 16,
        impactAssessment: {
          humidityImpact: 'medium',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: '',
      finalScore: { teamA: 0, teamB: 1 },
      halfTimeScore: { teamA: 0, teamB: 1 },
      events: [
        { min: 2, type: 'goal', team: 'paraguay', player: '', assist: '' },
        { min: 34, type: 'post', team: 'turkey', player: '' },
        { min: 45, type: 'red', team: 'paraguay', player: '', desc: '',
        { min: 46, type: 'substitution', team: 'turkey', playerIn: ', playerOut: '' },
        { min: 60, type: 'substitution', team: 'turkey', playerIn: ', playerOut: '', desc: ''substitution', team: 'turkey', playerIn: ', playerOut: '',
        { min: 67, type: 'substitution', team: 'paraguay', playerIn: ', playerOut: '' },
        { min: 70, type: 'substitution', team: 'turkey', playerIn: ', playerOut: '', desc: ''substitution', team: 'paraguay', playerIn: ', playerOut: '', desc: ''substitution', team: 'turkey', playerIn: ', playerOut: '' },
        { min: 90, type: 'substitution', team: 'paraguay', playerIn: ', playerOut: '',
        { min: 90, type: 'substitution', team: 'paraguay', playerIn: ', playerOut: '', desc: '' }      ],      result: {        htScore: '0-1',        ftScore: '0-1',        goalsA: 0,        goalsB: 1,        totalGoals: 1,        halfTime: { a: 0, b: 1 },        wdw: 'winB',        htWdw: 'winB',        htft: '',        handicap: 'lose',        overUnder25: 'under',        redCards: { turkey: 0, paraguay: 1 }      },      lineups: {        teamA: {          formation: '4-2-3-1',          players: [            { number: 23, position: '', name: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 6.2, notes: '', club: '', league: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.8, notes: '', club: '', league: '', apps: 27, goals: 2, assists: 1, keyPasses: 0.3, rating: 6.7, notes: '' },            { number: 14, position: '', name: ', club: '', apps: 26, goals: 1, assists: 2, keyPasses: 0.4, rating: 8.1, minuteSubstituted: 86, notes: '' },            { number: 20, position: ', name: '', club: '', league: '', apps: 24, goals: 3, assists: 4, keyPasses: 0.7, rating: 6.8, minuteSubstituted: 70, notes: '' },            { number: 16, position: '', club: '', league: '', apps: 26, goals: 3, assists: 5, keyPasses: 1.0, rating: 7.0, minuteSubstituted: 60, notes: '' },            { number: 10, position: '', name: ', club: '', league: '', apps: 29, goals: 6, assists: 11, keyPasses: 1.4, rating: 7.5, captain: true, notes: '', name: '', league: '', apps: 25, goals: 4, assists: 3, keyPasses: 0.6, rating: 6.3, minuteSubstituted: 60, notes: '' },            { number: 8, position: '', name: '', apps: 24, goals: 5, assists: 7, keyPasses: 1.2, rating: 7.6, yellowCard: true, notes: '', name: '', club: '', league: '', apps: 26, goals: 7, assists: 4, keyPasses: 0.9, rating: 7.3, notes: '' },            { number: 7, position: '', name: '', league: '', apps: 27, goals: 10, assists: 5, keyPasses: 0.8, rating: 6.6, minuteSubstituted: 46, notes: '' }
          ],
          substitutes: [
            { number: 9, name: '', club: '', apps: 24, goals: 6, assists: 2, keyPasses: 0.7, rating: 6.5, minuteOn: 46, notes: '', league: '', apps: 23, goals: 5, assists: 4, keyPasses: 0.9, rating: 6.4, minuteOn: 60, notes: ''4-4-2',
          players: [
            { number: 12, position: '', name: '', club: '', apps: 29, goals: 0, assists: 0, keyPasses: 0.0, rating: 7.9, notes: '' },
            { number: 15, position: ''-, club: '', apps: 28, goals: 2, assists: 1, keyPasses: 0.4, rating: 7.3, captain: true, notes: '' },
            { number: 23, position: '', club: '', apps: 25, goals: 3, assists: 1, keyPasses: 0.3, rating: 7.7, goal: true, minuteSubstituted: 90, notes: '' },
            { number: 14, position: '', name: '', apps: 24, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.9, notes: '',
            { number: 4, position: '', club: '', apps: 27, goals: 2, assists: 5, keyPasses: 0.9, rating: 8.2, minuteSubstituted: 81, notes: '',
            { number: 6, position: '', league: '' },
            { number: 3, position: '', club: '', apps: 26, goals: 2, assists: 2, keyPasses: 0.75, rating: 7.1, notes: 'B2B' },
            { number: 10, position: '', club: '', apps: 27, goals: 8, assists: 9, keyPasses: 1.3, rating: 5.9, yellowCard: true, redCard: true, notes: '' },
            { number: 19, position: '', name: '', apps: 26, goals: 7, assists: 8, keyPasses: 1.4, rating: 7.6, assist: true, minuteSubstituted: 90, notes: '',
            { number: 8, position: '', apps: 23, goals: 4, assists: 5, keyPasses: 0.8, rating: 6.9, minuteSubstituted: 67, notes: '' },
            { number: 25, position: '', name: '', club: '', apps: 24, goals: 6, assists: 2, keyPasses: 0.5, rating: 5.9, minuteSubstituted: 46, notes: '' }          ],          substitutes: [            { number: 9, name: '-', club: ', league: '', apps: 22, goals: 4, assists: 1, keyPasses: 0.4, rating: 6.5, minuteOn: 46, notes: '', club: '', apps: 20, goals: 3, assists: 2, keyPasses: 0.5, rating: 6.4, minuteOn: 67, notes: ' }
          ],
          avgRating: 7.1,
          avgAge: 27.6,
          marketValue: 101,
          worldRanking: 42
        }
      },
      stats: {
        possession: { turkey: '79%', paraguay: '21%' },
        shots: { turkey: 32, paraguay: 7 },
        shotsOnTarget: { turkey: 5, paraguay: 2 },
        shotsOffTarget: { turkey: 27, paraguay: 5 },
        blockedShots: { turkey: 11, paraguay: 20 },
        shotsInBox: { turkey: 18, paraguay: 2 },
        shotsOutsideBox: { turkey: 14, paraguay: 5 },
        bigChances: { turkey: 6, paraguay: 1 },
        bigChancesMissed: { turkey: 6, paraguay: 0 },
        corners: { turkey: 12, paraguay: 0 },
        freeKicks: { turkey: 15, paraguay: 3 },
        fouls: { turkey: 14, paraguay: 15 },
        offsides: { turkey: 2, paraguay: 3 },
        yellowCards: { turkey: 2, paraguay: 1 },
        redCards: { turkey: 0, paraguay: 1 },
        woodwork: { turkey: 1, paraguay: 0 },
        passes: { turkey: 702, paraguay: 241 },
        passAccuracy: { turkey: 89, paraguay: 53 },
        finalThirdPasses: { turkey: 312, paraguay: 29 },
        throughBalls: { turkey: 10, paraguay: 2 },
        crosses: { turkey: 48, paraguay: 6 },
        crossAccuracy: { turkey: 13, paraguay: 1 },
        tackles: { turkey: 11, paraguay: 29 },
        interceptions: { turkey: 8, paraguay: 18 },
        clearances: { turkey: 8, paraguay: 47 },
        saves: { turkey: 1, paraguay: 5 }
      },
      xGData: {
        xG: { turkey: 2.10, paraguay: 0.32 },
        xGOT: { turkey: 1.94, paraguay: 0.28 },
        xA: { turkey: 1.68, paraguay: 0.30 },
        xGA: { turkey: 0.28, paraguay: 1.94 }
      },
      xARanking: {
        turkey: [
          { name: '',
          { name: '',
          { name: '', xA: 0.38 }
        ],
        paraguay: [
          { name: '',
          { name: '', xA: 0.12 }
        ]
      },
      halfTimeStats: {
        htScore: '0-1',
        xG: { turkey: 0.87, paraguay: 0.32 },
        shots: { turkey: 13, paraguay: 4 },
        shotsOnTarget: { turkey: 2, paraguay: 2 },
        possession: { turkey: '72%', paraguay: '28%' }
      },
      secondHalfStats: {
        xG: { turkey: 1.23, paraguay: 0.00 },
        shots: { turkey: 19, paraguay: 3 },
        shotsOnTarget: { turkey: 3, paraguay: 0 },
        possession: { turkey: '85%', paraguay: '15%' }
      },
      accuracy: {
        wdw: false, score: false, htft: false,
        totalGoals: true, overUnder25: true, handicap: false,
        total: '2/6', brierScore: 0.45
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          '',
          '',
          ''4-2-3-146/60/70/86',          paraguay: '4-4-2/67/81/90
        },
        rootCauses: [
          '',
          '',
          '',
          '',
          '' +',          ': +',          ' 25>3'        ],        groupStandings: 'D6)30)'      },      postMatchAnalysis: {        keyFactor: '8.2.97.77.6.1.57.6256',        tacticalSummary: '4-2-3-179%32+4-4-210',        playerOfMatch: '( - 8.2',        lessonsLearned: '+',        xGAnalysis: 'xG 2.100)xG 0.321',        tacticalKey: '++5+3256',        leagueGapAnalysis: '+',        goalKeyMoments: {          firstHalf: '5-0344510',          secondHalf: '1260-1'        },        playerRatingSummary: {          highest: '.2-',          teamABest: '8.17.67.5.3',          teamBBest: '8.2.9.77.67.37.3',          lowest: '.9'        },        teamTactics: {          teamA: '4-2-3-146/60/70/86',          teamB: '-4-2/67/81/90
        },
        substitutionImpact: '',
        winKeyFactors: '', odds:3.92, payout:'3.92},        handicap: {result:'(-1), odds:1.85, payout:'1.85},        correctScore: {result:'0:1', odds:11.50, payout:'11.50},        totalGoals: {result:'1, odds:4.50, payout:'4.50},        htft: {result:'', odds:6.70, payout:'6.70}
      }
    },
    {
      matchId: 'NED-SWE-20260621',
      competition: ''netherlands', teamB: 'sweden'',
      teamAName: '', teamBName: '',
      venue: 'usa_central', stadium: '',
      kickoff: '2026-06-20T17:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: 'Houston',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 29,
        humidity: 77,
        weather: 'thunderstorm',
        altitude: 12,
        windSpeed: 15,
        windDirection: 'southeast',
        feelsLike: 35,
        impactAssessment: {
          humidityImpact: 'high',
          expectedEffect: 'slight_disadvantage_home',
          stormRisk: true,
          windImpact: 'medium'
        }
      },
      significance: '',
      finalScore: { teamA: 5, teamB: 1 },
      halfTimeScore: { teamA: 2, teamB: 0 },
      events: [
        { min: 5, type: 'goal', team: 'netherlands', player: '' },
        { min: 17, type: 'goal', team: 'netherlands', player: '', desc: '' },
        { min: 47, type: 'goal', team: 'netherlands', player: '', desc: '-0' },
        { min: 54, type: 'goal', team: 'netherlands', player: '', desc: '' },
        { min: 59, type: 'goal', team: 'sweden', player: '', desc: '' },
        { min: 89, type: 'goal', team: 'netherlands', player: '', assist: '', desc: '' },
        { min: 46, type: 'substitution', team: 'netherlands', playerIn: ', playerOut: '', desc: ' },
        { min: 55, type: 'substitution', team: 'sweden', playerIn: ', playerOut: ', desc: '',
        { min: 56, type: 'substitution', team: 'sweden', playerIn: ', playerOut: ', desc: '',
        { min: 56, type: 'substitution', team: 'sweden', playerIn: ', playerOut: ', desc: '' },
        { min: 59, type: 'substitution', team: 'netherlands', playerIn: ', playerOut: '', desc: '' },
        { min: 59, type: 'substitution', team: 'netherlands', playerIn: ', playerOut: '', desc: ''substitution', team: 'netherlands', playerIn: ', playerOut: '',
        { min: 79, type: 'substitution', team: 'sweden', playerIn: ', playerOut: '',
        { min: 90, type: 'substitution', team: 'netherlands', playerIn: ', playerOut: '' },
        { min: 90, type: 'substitution', team: 'sweden', playerIn: ', playerOut: '', desc: ''2-0',
        ftScore: '5-1',
        goalsA: 5,
        goalsB: 1,
        totalGoals: 6,
        halfTime: { a: 2, b: 0 },
        wdw: 'winA',
        htWdw: 'winA',
        htft: '',
        handicap: 'win',
        overUnder25: 'over',
        redCards: { netherlands: 0, sweden: 0 }
      },
      lineups: {
        teamA: {
          formation: '4-3-3',
          players: [
            { number: 1, position: '', name: '', club: '', apps: 27, goals: 0, assists: 0, keyPasses: 0.12, rating: 8.3, notes: '' },
            { number: 22, position: '', club: '', league: '', apps: 28, goals: 4, assists: 7, keyPasses: 0.9, rating: 8.8, assist: true, notes: '' },
            { number: 6, position: '', apps: 26, goals: 2, assists: 1, keyPasses: 0.35, rating: 7.3, notes: '', name: '', apps: 30, goals: 3, assists: 2, keyPasses: 0.4, rating: 6.9, captain: true, notes: '',
            { number: 15, position: '', league: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.8, notes: '',
            { number: 8, position: '', apps: 27, goals: 4, assists: 5, keyPasses: 1.0, rating: 6.5, notes: '' },
            { number: 21, position: '', name: '', club: '', league: '', apps: 29, goals: 3, assists: 10, keyPasses: 1.4, rating: 7.6, minuteSubstituted: 59, notes: '' },
            { number: 14, position: '', club: 'AC', league: '', apps: 26, goals: 5, assists: 4, keyPasses: 1.1, rating: 6.2, minuteSubstituted: 59, notes: '59 },
            { number: 18, position: '', club: '', league: '', apps: 28, goals: 11, assists: 6, keyPasses: 0.8, rating: 6.7, minuteSubstituted: 46, notes: '' },
            { number: 19, position: '', name: '', league: '', apps: 29, goals: 14, assists: 5, keyPasses: 0.9, rating: 8.3, goal: true, minuteSubstituted: 72, notes: '' },
            { number: 11, position: '', club: '', apps: 31, goals: 16, assists: 12, keyPasses: 1.6, rating: 9.5, goal: true, minuteSubstituted: 90, notes: '' }          ],          substitutes: [            { number: 10, name: '', club: '', league: '', apps: 26, goals: 7, assists: 6, keyPasses: 0.9, rating: 7.0, assist: true, minuteOn: 46, notes: '' },            { number: 16, name: '', club: ', league: '', apps: 24, goals: 6, assists: 4, keyPasses: 0.8, rating: 7.2, goal: true, minuteOn: 46, notes: '' }
          ],
          avgRating: 7.5,
          avgAge: 26.9,
          marketValue: 475,
          worldRanking: 8
        },
        teamB: {
          formation: '3-5-2',
          players: [
            { number: 23, position: '', name: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.0, rating: 5.6, notes: '',
            { number: 3, position: '', club: '', league: '', apps: 27, goals: 1, assists: 2, keyPasses: 0.3, rating: 5.6, captain: true, yellowCard: true, notes: ' },
            { number: 4, position: '', name: '', club: '', apps: 25, goals: 1, assists: 0, keyPasses: 0.25, rating: 5.7, yellowCard: true, notes: '' },
            { number: 2, position: '', club: '', league: '', apps: 24, goals: 1, assists: 1, keyPasses: 0.4, rating: 6.1, notes: '' },
            { number: 5, position: '', club: ', league: '', apps: 25, goals: 3, assists: 3, keyPasses: 0.5, rating: 6.1, minuteSubstituted: 90, notes: '',
            { number: 18, position: ''-, club: '', league: '', apps: 24, goals: 3, assists: 4, keyPasses: 0.7, rating: 6.2, minuteSubstituted: 79, notes: '' },
            { number: 16, position: '', name: '', league: '', apps: 26, goals: 2, assists: 4, keyPasses: 0.75, rating: 6.5, minuteSubstituted: 56, notes: '56' },
            { number: 10, position: '', apps: 23, goals: 4, assists: 2, keyPasses: 0.4, rating: 5.8, minuteSubstituted: 56, notes: '', league: '', apps: 24, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.2, minuteSubstituted: 55, notes: '' },
            { number: 9, position: '', name: '', apps: 28, goals: 12, assists: 4, keyPasses: 0.7, rating: 7.0, goal: true, notes: '',
            { number: 17, position: '', name: '', club: '', league: '', apps: 27, goals: 10, assists: 3, keyPasses: 0.65, rating: 6.7, notes: '' }
          ],
          substitutes: [
            { number: 20, name: '', league: '', apps: 22, goals: 4, assists: 2, keyPasses: 0.6, rating: 6.8, goal: true, minuteOn: 56, notes: '' },
            { number: 8, name: '', club: '', apps: 23, goals: 3, assists: 4, keyPasses: 0.8, rating: 6.3, minuteOn: 55, notes: ' }
          ],
          avgRating: 6.2,
          avgAge: 27.6,
          marketValue: 263,
          worldRanking: 34
        }
      },
      stats: {
        possession: { netherlands: '52%', sweden: '48%' },
        shots: { netherlands: 10, sweden: 16 },
        shotsOnTarget: { netherlands: 7, sweden: 8 },
        shotsOffTarget: { netherlands: 3, sweden: 8 },
        blockedShots: { netherlands: 3, sweden: 9 },
        shotsInBox: { netherlands: 7, sweden: 6 },
        shotsOutsideBox: { netherlands: 3, sweden: 10 },
        bigChances: { netherlands: 3, sweden: 1 },
        bigChancesMissed: { netherlands: 0, sweden: 1 },
        corners: { netherlands: 2, sweden: 5 },
        freeKicks: { netherlands: 6, sweden: 8 },
        fouls: { netherlands: 9, sweden: 12 },
        offsides: { netherlands: 3, sweden: 3 },
        yellowCards: { netherlands: 0, sweden: 3 },
        redCards: { netherlands: 0, sweden: 0 },
        woodwork: { netherlands: 0, sweden: 1 },
        passes: { netherlands: 450, sweden: 423 },
        passAccuracy: { netherlands: 89, sweden: 86 },
        finalThirdPasses: { netherlands: 194, sweden: 112 },
        throughBalls: { netherlands: 8, sweden: 4 },
        crosses: { netherlands: 24, sweden: 28 },
        crossAccuracy: { netherlands: 7, sweden: 5 },
        tackles: { netherlands: 16, sweden: 18 },
        interceptions: { netherlands: 9, sweden: 11 },
        clearances: { netherlands: 24, sweden: 14 },
        saves: { netherlands: 7, sweden: 2 }
      },
      xGData: {
        xG: { netherlands: 2.47, sweden: 0.94 },
        xGOT: { netherlands: 2.35, sweden: 0.89 },
        xA: { netherlands: 1.86, sweden: 0.73 },
        xGA: { netherlands: 0.89, sweden: 2.35 }
      },
      xARanking: {
        netherlands: [
          { name: '', xA: 0.68 },
          { name: '',
          { name: '', xA: 0.34 },
          { name: '', xA: 0.25 }
        ],
        sweden: [
          { name: '',
          { name: '', xA: 0.19 },
          { name: '', xA: 0.12 }
        ]
      },
      halfTimeStats: {
        htScore: '2-0',
        xG: { netherlands: 1.16, sweden: 0.37 },
        shots: { netherlands: 4, sweden: 7 },
        shotsOnTarget: { netherlands: 3, sweden: 4 },
        possession: { netherlands: '55%', sweden: '45%' }
      },
      secondHalfStats: {
        xG: { netherlands: 1.31, sweden: 0.57 },
        shots: { netherlands: 6, sweden: 9 },
        shotsOnTarget: { netherlands: 4, sweden: 4 },
        possession: { netherlands: '49%', sweden: '51%' }
      },
      accuracy: {
        wdw: true, score: false, htft: true,
        totalGoals: false, overUnder25: true, handicap: true,
        total: '4/6', brierScore: 0.18
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          '',
          ''
        ],
        tacticalAnalysis: {
          netherlands: '',
          sweden: '' 5.6.7',          ' .86.2',          ': .5',          ': .6',          ' .0
        ],
        modelImprovement: [
          '',
          '',
          ''F4)1
      },
      postMatchAnalysis: {
        keyFactor: '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        xGAnalysis: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: ''
        },
        teamTactics: {
          teamA: '',
          teamB: ''46/59/72/905/56/79/90',        winKeyFactors: '57'      },      oddsResults: {        wdw: {result:', odds:1.53, payout:'1.53},        handicap: {result:'(-1), odds:2.75, payout:'2.75},        correctScore: {result:'5:1', odds:70.00, payout:'70},        totalGoals: {result:'6, odds:14.00, payout:'14},        htft: {result:'', odds:2.42, payout:'2.42}
      }
    },
    {
      matchId: 'GER-CIV-20260621',
      competition: ''germany', teamB: 'cotedivoire'',
      teamAName: '', teamBName: '',
      venue: 'canada_central', stadium: '',
      kickoff: '2026-06-20T20:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: '',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 23,
        humidity: 43,
        weather: 'cloudy',
        altitude: 75,
        windSpeed: 12,
        windDirection: 'west',
        feelsLike: 21,
        impactAssessment: {
          humidityImpact: 'low',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: '',
      finalScore: { teamA: 2, teamB: 1 },
      halfTimeScore: { teamA: 0, teamB: 1 },
      events: [
        { min: 30, type: 'goal', team: 'cotedivoire', player: '', assist: '', desc: '' },
        { min: 68, type: 'goal', team: 'germany', player: '' },
        { min: 90, type: 'goal', team: 'germany', player: '' },
        { min: 46, type: 'substitution', team: 'germany', playerIn: ', playerOut: '',
        { min: 60, type: 'substitution', team: 'germany', playerIn: ', playerOut: '', desc: '' },
        { min: 60, type: 'substitution', team: 'germany', playerIn: ', playerOut: ', desc: '' },
        { min: 60, type: 'substitution', team: 'germany', playerIn: ', playerOut: '', desc: ' },
        { min: 75, type: 'substitution', team: 'cotedivoire', playerIn: ', playerOut: '', desc: '' },
        { min: 75, type: 'substitution', team: 'cotedivoire', playerIn: ', playerOut: '',
        { min: 75, type: 'substitution', team: 'cotedivoire', playerIn: ', playerOut: '' },
        { min: 82, type: 'substitution', team: 'cotedivoire', playerIn: ', playerOut: '', desc: ''substitution', team: 'germany', playerIn: ', playerOut: '' },
        { min: 85, type: 'substitution', team: 'cotedivoire', playerIn: ', playerOut: '', desc: '' }      ],      result: {        htScore: '0-1',        ftScore: '2-1',        goalsA: 2,        goalsB: 1,        totalGoals: 3,        halfTime: { a: 0, b: 1 },        wdw: 'winA',        htWdw: 'winB',        htft: '',        handicap: 'win',        overUnder25: 'over',        redCards: { germany: 0, cotedivoire: 0 }      },      lineups: {        teamA: {          formation: '3-4-3',          players: [            { number: 1, position: '', name: '', apps: 29, goals: 0, assists: 0, keyPasses: 0.15, rating: 6.9, notes: '', name: '', league: '', apps: 30, goals: 4, assists: 6, keyPasses: 0.7, rating: 7.1, captain: true, notes: '' },            { number: 4, position: '', name: '', league: '', apps: 28, goals: 2, assists: 2, keyPasses: 0.35, rating: 7.5, notes: '', league: '', apps: 26, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.6, minuteSubstituted: 46, notes: '' },            { number: 23, position: '', apps: 27, goals: 7, assists: 5, keyPasses: 1.0, rating: 7.4, notes: '' },            { number: 10, position: ', name: '', club: '', apps: 31, goals: 14, assists: 12, keyPasses: 1.5, rating: 6.5, minuteSubstituted: 60, notes: '0' },            { number: 5, position: ', name: '', league: '', apps: 25, goals: 3, assists: 4, keyPasses: 0.9, rating: 6.4, minuteSubstituted: 60, notes: '' },            { number: 18, position: '', club: '', league: '', apps: 24, goals: 3, assists: 4, keyPasses: 0.8, rating: 7.2, notes: '' },            { number: 19, position: ', name: '', club: '', apps: 28, goals: 10, assists: 9, keyPasses: 1.2, rating: 6.8, minuteSubstituted: 60, notes: '60 },            { number: 7, position: '', name: '', apps: 29, goals: 9, assists: 6, keyPasses: 0.8, rating: 6.2, minuteSubstituted: 85, notes: '' },            { number: 17, position: '', league: '', apps: 30, goals: 13, assists: 14, keyPasses: 1.6, rating: 7.4, notes: '' }          ],          substitutes: [            { number: 14, name: '', league: '', apps: 26, goals: 5, assists: 7, keyPasses: 1.1, rating: 7.6, assist: true, minuteOn: 60, notes: '' },            { number: 9, name: ', club: '', apps: 27, goals: 8, assists: 3, keyPasses: 0.9, rating: 8.0, goal: true, minuteOn: 46, notes: ' },            { number: 11, name: '', league: '', apps: 25, goals: 6, assists: 5, keyPasses: 0.9, rating: 7.4, assist: true, minuteOn: 60, notes: ''4-3-3',          players: [            { number: 1, position: '', name: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.0, rating: 8.0, notes: '' },            { number: 3, position: ', name: '', club: '', league: '', apps: 25, goals: 1, assists: 3, keyPasses: 0.4, rating: 6.8, notes: '' },            { number: 20, position: '', club: '', apps: 24, goals: 1, assists: 0, keyPasses: 0.25, rating: 6.3, notes: '' },            { number: 7, position: '', name: '', league: '', apps: 26, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.6, notes: '', club: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.4, rating: 6.6, minuteSubstituted: 82, notes: '' },            { number: 8, position: '', club: '', league: '', name: '', league: '', apps: 28, goals: 4, assists: 7, keyPasses: 1.0, rating: 7.4, minuteSubstituted: 75, notes: '5' },            { number: 26, position: ', name: '', league: '', apps: 26, goals: 3, assists: 5, keyPasses: 0.9, rating: 7.3, notes: ' },            { number: 11, position: '', club: '', apps: 26, goals: 6, assists: 4, keyPasses: 0.7, rating: 7.2, minuteSubstituted: 85, assist: true, notes: '' },            { number: 9, position: '', name: '', club: '', league: '', apps: 27, goals: 11, assists: 3, keyPasses: 0.6, rating: 6.4, minuteSubstituted: 75, notes: '' },            { number: 15, position: '', league: '', apps: 25, goals: 5, assists: 6, keyPasses: 0.8, rating: 6.5, minuteSubstituted: 75, notes: '' }          ],          substitutes: [            { number: 10, name: '', club: '', league: '', apps: 24, goals: 4, assists: 3, keyPasses: 0.7, rating: 6.5, minuteOn: 75, notes: '', club: ', league: '', apps: 22, goals: 3, assists: 2, keyPasses: 0.6, rating: 6.4, minuteOn: 82, notes: '' }
          ],
          avgRating: 6.8,
          avgAge: 25.0,
          marketValue: 287,
          worldRanking: 30
        }
      },
      stats: {
        possession: { germany: '60%', cotedivoire: '40%' },
        shots: { germany: 16, cotedivoire: 9 },
        shotsOnTarget: { germany: 7, cotedivoire: 2 },
        shotsOffTarget: { germany: 9, cotedivoire: 7 },
        blockedShots: { germany: 5, cotedivoire: 7 },
        shotsInBox: { germany: 11, cotedivoire: 3 },
        shotsOutsideBox: { germany: 5, cotedivoire: 6 },
        bigChances: { germany: 6, cotedivoire: 2 },
        bigChancesMissed: { germany: 4, cotedivoire: 1 },
        corners: { germany: 8, cotedivoire: 3 },
        freeKicks: { germany: 11, cotedivoire: 5 },
        fouls: { germany: 5, cotedivoire: 7 },
        offsides: { germany: 0, cotedivoire: 1 },
        yellowCards: { germany: 0, cotedivoire: 0 },
        redCards: { germany: 0, cotedivoire: 0 },
        woodwork: { germany: 0, cotedivoire: 0 },
        passes: { germany: 622, cotedivoire: 431 },
        passAccuracy: { germany: 89, cotedivoire: 81 },
        finalThirdPasses: { germany: 231, cotedivoire: 64 },
        throughBalls: { germany: 9, cotedivoire: 3 },
        crosses: { germany: 32, cotedivoire: 14 },
        crossAccuracy: { germany: 9, cotedivoire: 3 },
        tackles: { germany: 26, cotedivoire: 20 },
        interceptions: { germany: 15, cotedivoire: 12 },
        clearances: { germany: 14, cotedivoire: 31 },
        saves: { germany: 1, cotedivoire: 5 }
      },
      xGData: {
        xG: { germany: 1.63, cotedivoire: 0.82 },
        xGOT: { germany: 1.51, cotedivoire: 0.76 },
        xA: { germany: 1.44, cotedivoire: 0.69 },
        xGA: { germany: 0.76, cotedivoire: 1.51 }
      },
      xARanking: {
        germany: [
          { name: '',
          { name: '',
          { name: '', xA: 0.69 },          { name: '', xA: 0.17 }        ]      },      halfTimeStats: {        htScore: '0-1',        xG: { germany: 0.61, cotedivoire: 0.77 },        shots: { germany: 8, cotedivoire: 5 },        shotsOnTarget: { germany: 3, cotedivoire: 2 },        possession: { germany: '61%', cotedivoire: '39%' }      },      secondHalfStats: {        xG: { germany: 1.02, cotedivoire: 0.05 },        shots: { germany: 8, cotedivoire: 4 },        shotsOnTarget: { germany: 4, cotedivoire: 0 },        possession: { germany: '58%', cotedivoire: '42%' }      },      accuracy: {        wdw: true, score: false, htft: false,        totalGoals: true, overUnder25: true, handicap: true,        total: '4/6', brierScore: 0.16      },      review: {        summary: '(62%)2-18.08.0.6.4.47.26.2Brier0.16()/6',        keyMoments: [          '0: 1-0',          '8: ',          '5: ',          '8: 1-1',          '0: -1'        ],        tacticalAnalysis: {          germany: '3-4-30-',          cotedivoire: '4-3-3+'        },        rootCauses: [          ': 6.36.6',          ': 6.6',          ' 75',          ': 6.4',          ': 8.0'        ],        modelImprovement: [          ': xG>1.0',          ': ',          ' >7.0'        ],        groupStandings: 'E6)1
      },
      postMatchAnalysis: {
        keyFactor: '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        xGAnalysis: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: ''3-4-30-',          teamB: '4-3-3+'        },        substitutionImpact: '46/60/60/60/855/75/75/82/85',        winKeyFactors: '308/450890+4'      },      oddsResults: {        wdw: {result:', odds:1.33, payout:'1.33},        handicap: {result:'(-1), odds:3.93, payout:'3.93},        correctScore: {result:'2:1', odds:5.90, payout:'5.90},        totalGoals: {result:'3, odds:3.30, payout:'3.30},        htft: {result:'', odds:20.00, payout:'20}
      }
    },
    {
      matchId: 'ECU-CUR-20260621',
      competition: ''ecuador', teamB: 'curacao'',
      teamAName: '', teamBName: '',
      venue: 'usa_central', stadium: '',
      kickoff: '2026-06-21T00:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: '',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 26,
        humidity: 65,
        weather: 'cloudy',
        altitude: 260,
        windSpeed: 10,
        windDirection: 'south',
        feelsLike: 27,
        impactAssessment: {
          humidityImpact: 'medium',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: ''save', team: 'curacao', player: '', desc: ''substitution', team: 'ecuador', playerIn: ', playerOut: '' },
        { min: 70, type: 'substitution', team: 'ecuador', playerIn: ', playerOut: '', desc: ''substitution', team: 'curacao', playerIn: ', playerOut: '' },
        { min: 76, type: 'substitution', team: 'curacao', playerIn: ', playerOut: '' },
        { min: 76, type: 'substitution', team: 'curacao', playerIn: ', playerOut: '' },
        { min: 83, type: 'substitution', team: 'ecuador', playerIn: ', playerOut: '',
        { min: 83, type: 'substitution', team: 'curacao', playerIn: ', playerOut: '', desc: '' },
        { min: 84, type: 'substitution', team: 'curacao', playerIn: ', playerOut: ', desc: '',
        { min: 89, type: 'substitution', team: 'ecuador', playerIn: ', playerOut: ', desc: '',
        { min: 89, type: 'woodwork', team: 'ecuador', player: '' },
        { min: 35, type: 'yellow_card', team: 'ecuador', player: '' },
        { min: 42, type: 'yellow_card', team: 'curacao', player: '' },
        { min: 55, type: 'yellow_card', team: 'curacao', player: '' },
        { min: 67, type: 'yellow_card', team: 'curacao', player: '' },
        { min: 78, type: 'yellow_card', team: 'curacao', player: '', desc: '',
        { min: 88, type: 'yellow_card', team: 'curacao', player: '' }
      ],
      result: {
        htScore: '0-0',
        ftScore: '0-0',
        goalsA: 0,
        goalsB: 0,
        totalGoals: 0,
        halfTime: { a: 0, b: 0 },
        wdw: 'draw',
        htWdw: 'draw',
        htft: '',
        handicap: 'win',
        overUnder25: 'under',
        redCards: { ecuador: 0, curacao: 0 }
      },
      lineups: {
        teamA: {
          formation: '3-5-2',
          players: [
            { number: 1, position: '', name: '', club: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 8.0, notes: '',
            { number: 21, position: '', club: '', apps: 25, goals: 1, assists: 2, keyPasses: 0.3, rating: 7.3, minuteSubstituted: 83, notes: '3' },
            { number: 6, position: '', name: '', club: '', league: '', apps: 27, goals: 2, assists: 1, keyPasses: 0.35, rating: 7.5, notes: '' },
            { number: 3, position: '', league: '', apps: 26, goals: 1, assists: 2, keyPasses: 0.4, rating: 7.5, notes: '',
            { number: 9, position: '', apps: 24, goals: 4, assists: 3, keyPasses: 0.6, rating: 7.2, minuteSubstituted: 89, notes: '', name: '', apps: 29, goals: 3, assists: 8, keyPasses: 1.2, rating: 8.0, captain: true, notes: '' },
            { number: 5, position: '', name: '', apps: 23, goals: 3, assists: 4, keyPasses: 1.0, rating: 6.6, minuteSubstituted: 46, yellowCard: true, notes: '',
            { number: 15, position: '', club: '', apps: 26, goals: 3, assists: 5, keyPasses: 1.1, rating: 7.7, notes: '',
            { number: 7, position: '', club: '', apps: 27, goals: 2, assists: 6, keyPasses: 0.8, rating: 6.6, minuteSubstituted: 70, notes: '' },
            { number: 19, position: '', name: '', league: '', apps: 28, goals: 8, assists: 7, keyPasses: 0.9, rating: 7.5, notes: '', name: '', club: '', league: '', apps: 29, goals: 12, assists: 4, keyPasses: 0.8, rating: 6.7, captain: true, notes: '' }
          ],
          substitutes: [
            { number: 10, name: '', apps: 25, goals: 6, assists: 4, keyPasses: 0.8, rating: 7.0, minuteOn: 46, notes: '' }
          ],
          avgRating: 7.3,
          avgAge: 27.5,
          marketValue: 270,
          worldRanking: 28
        },
        teamB: {
          formation: '5-3-2',
          players: [
            { number: 1, position: '', name: '', club: '', apps: 29, goals: 0, assists: 0, keyPasses: 0.0, rating: 10.0, notes: '', apps: 24, goals: 1, assists: 2, keyPasses: 0.3, rating: 6.9, minuteSubstituted: 76, notes: '' },
            { number: 5, position: '', club: '', apps: 26, goals: 1, assists: 1, keyPasses: 0.3, rating: 7.6, yellowCard: true, notes: ' },
            { number: 18, position: '', name: '', club: '', league: '', apps: 25, goals: 1, assists: 0, keyPasses: 0.25, rating: 7.2, notes: '',
            { number: 3, position: '', club: '', league: '', apps: 23, goals: 1, assists: 1, keyPasses: 0.4, rating: 6.8, yellowCard: true, notes: '' },
            { number: 20, position: '', club: '', apps: 24, goals: 2, assists: 3, keyPasses: 0.5, rating: 7.5, notes: '',
            { number: 21, position: '', league: '', apps: 26, goals: 4, assists: 4, keyPasses: 0.7, rating: 6.9, minuteSubstituted: 76, notes: '' },
            { number: 10, position: '', name: '', league: '', apps: 27, goals: 2, assists: 3, keyPasses: 0.75, rating: 6.5, captain: true, minuteSubstituted: 75, yellowCard: true, notes: '' },
            { number: 8, position: '', club: '', league: '', apps: 23, goals: 2, assists: 2, keyPasses: 0.4, rating: 6.4, minuteSubstituted: 84, yellowCard: true, notes: '' },
            { number: 7, position: '', club: '', apps: 25, goals: 6, assists: 2, keyPasses: 0.6, rating: 6.7, minuteSubstituted: 75, yellowCard: true, notes: '75' },
            { number: 9, position: '', club: '', league: '', apps: 24, goals: 7, assists: 3, keyPasses: 0.5, rating: 6.5, minuteSubstituted: 83, yellowCard: true, notes: '' }          ],          substitutes: [            { number: 14, name: '', league: '', apps: 22, goals: 3, assists: 2, keyPasses: 0.5, rating: 6.5, minuteOn: 76, yellowCard: true, notes: '' }
          ],
          avgRating: 7.0,
          avgAge: 29.1,
          marketValue: 14,
          worldRanking: 83
        }
      },
      stats: {
        possession: { ecuador: '75%', curacao: '25%' },
        shots: { ecuador: 27, curacao: 10 },
        shotsOnTarget: { ecuador: 15, curacao: 3 },
        shotsOffTarget: { ecuador: 12, curacao: 7 },
        blockedShots: { ecuador: 4, curacao: 14 },
        shotsInBox: { ecuador: 19, curacao: 2 },
        shotsOutsideBox: { ecuador: 8, curacao: 8 },
        bigChances: { ecuador: 5, curacao: 1 },
        bigChancesMissed: { ecuador: 5, curacao: 1 },
        corners: { ecuador: 9, curacao: 0 },
        freeKicks: { ecuador: 12, curacao: 4 },
        fouls: { ecuador: 7, curacao: 10 },
        offsides: { ecuador: 1, curacao: 2 },
        yellowCards: { ecuador: 1, curacao: 5 },
        redCards: { ecuador: 0, curacao: 0 },
        woodwork: { ecuador: 1, curacao: 0 },
        passes: { ecuador: 642, curacao: 224 },
        passAccuracy: { ecuador: 88, curacao: 72 },
        finalThirdPasses: { ecuador: 307, curacao: 31 },
        throughBalls: { ecuador: 12, curacao: 2 },
        crosses: { ecuador: 41, curacao: 7 },
        crossAccuracy: { ecuador: 11, curacao: 1 },
        tackles: { ecuador: 12, curacao: 31 },
        interceptions: { ecuador: 8, curacao: 15 },
        clearances: { ecuador: 9, curacao: 46 },
        saves: { ecuador: 3, curacao: 15 }
      },
      xGData: {
        xG: { ecuador: 3.05, curacao: 0.48 },
        xGOT: { ecuador: 2.91, curacao: 0.42 },
        xA: { ecuador: 2.18, curacao: 0.37 },
        xGA: { ecuador: 0.42, curacao: 2.91 }
      },
      xARanking: {
        ecuador: [
          { name: '',
          { name: '', xA: 0.54 },
          { name: '', xA: 0.43 },
          { name: '', xA: 0.15 }
        ]
      },
      halfTimeStats: {
        htScore: '0-0',
        xG: { ecuador: 1.72, curacao: 0.11 },
        shots: { ecuador: 8, curacao: 0 },
        shotsOnTarget: { ecuador: 6, curacao: 0 },
        possession: { ecuador: '74%', curacao: '26%' }
      },
      secondHalfStats: {
        xG: { ecuador: 1.33, curacao: 0.37 },
        shots: { ecuador: 19, curacao: 10 },
        shotsOnTarget: { ecuador: 9, curacao: 3 },
        possession: { ecuador: '76%', curacao: '24%' }
      },
      accuracy: {
        wdw: false, score: true, htft: true,
        totalGoals: true, overUnder25: true, handicap: true,
        total: '4/6', brierScore: 0.22
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          ''
        ],
        tacticalAnalysis: {
          ecuador: '',
          curacao: ''
        },
        rootCauses: [
          '',
          '',
          '',
          '',
          '': 9.0',          ' -4-1',          ': xG>3.00'        ],        groupStandings: ''      },      postMatchAnalysis: {        keyFactor: '10.05.08.07.7.5.57.5.6.5.4',        tacticalSummary: '',        playerOfMatch: '( - 10.05',        lessonsLearned: '',        xGAnalysis: 'xG 3.050)xG 0.480),        tacticalKey: '+++',        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '0',          secondHalf: '19899'        },        playerRatingSummary: {          highest: '0.0',          teamABest: '8.0.0.7.5.57.5',          teamBBest: '10.07.6.5.26.96.9',          lowest: '.4'        },        teamTactics: {          teamA: '3-5-2+',          teamB: '-3-25/76/83/84'        },        substitutionImpact: '46/70/83/895/76/76/83/84',        winKeyFactors: ''      },      oddsResults: {        wdw: {result:', odds:''},        handicap: {result:'(-2), odds:2.95, payout:'2.95},        correctScore: {result:'0:0', odds:22.00, payout:'22},        totalGoals: {result:'0, odds:22.00, payout:'22},        htft: {result:'', odds:13.00, payout:'13}
      }
    },
    {
      matchId: 'TUN-JPN-20260621',
      competition: ''tunisia', teamB: 'japan'',
      teamAName: '',
      venue: 'mexico_north', stadium: '',
      kickoff: '2026-06-21T04:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: '',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 28,
        humidity: 45,
        weather: 'clear',
        altitude: 540,
        windSpeed: 8,
        windDirection: 'north',
        feelsLike: 30,
        impactAssessment: {
          humidityImpact: 'low',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: '',
      finalScore: { teamA: 0, teamB: 4 },
      halfTimeScore: { teamA: 0, teamB: 2 },
      events: [
        { min: 4, type: 'goal', team: 'japan', player: '', assist: '', desc: '' },
        { min: 10, type: 'save', team: 'tunisia', player: '' },
        { min: 31, type: 'goal', team: 'japan', player: '', assist: ''2-0' },
        { min: 69, type: 'goal', team: 'japan', player: '', assist: '', desc: '' },
        { min: 83, type: 'goal', team: 'japan', player: '', assist: '', desc: '' },
        { min: 46, type: 'substitution', team: 'tunisia', playerIn: ', playerOut: '', desc: '' },
        { min: 46, type: 'substitution', team: 'tunisia', playerIn: ', playerOut: ', desc: '' },
        { min: 64, type: 'substitution', team: 'tunisia', playerIn: ', playerOut: '', desc: '' },
        { min: 73, type: 'substitution', team: 'japan', playerIn: ', playerOut: '', desc: '' },
        { min: 74, type: 'substitution', team: 'japan', playerIn: ', playerOut: ', desc: '',
        { min: 79, type: 'substitution', team: 'japan', playerIn: ', playerOut: '', desc: ' },
        { min: 79, type: 'substitution', team: 'japan', playerIn: ', playerOut: '', desc: ' },
        { min: 84, type: 'substitution', team: 'japan', playerIn: ', playerOut: '', desc: ' },
        { min: 90, type: 'substitution', team: 'tunisia', playerIn: ', playerOut: ', desc: '' },
        { min: 90, type: 'substitution', team: 'tunisia', playerIn: ', playerOut: ', desc: ''0-2',        ftScore: '0-4',        goalsA: 0,        goalsB: 4,        totalGoals: 4,        halfTime: { a: 0, b: 2 },        wdw: 'winB',        htWdw: 'winB',        htft: '',        handicap: 'win',        overUnder25: 'over',        redCards: { tunisia: 0, japan: 0 }      },      lineups: {        teamA: {          formation: '5-3-2',          players: [            { number: 1, position: '', name: ', club: '', league: '', apps: 26, goals: 0, assists: 0, keyPasses: 0.1, rating: 5.3, notes: '', name: '', league: '', apps: 24, goals: 1, assists: 2, keyPasses: 0.4, rating: 6.4, notes: '' },            { number: 4, position: ', name: '', league: '', apps: 25, goals: 1, assists: 1, keyPasses: 0.3, rating: 5.9, notes: '' },            { number: 3, position: '', name: '', league: '', apps: 23, goals: 1, assists: 0, keyPasses: 0.25, rating: 5.8, notes: '', club: '', league: '', apps: 24, goals: 1, assists: 2, keyPasses: 0.35, rating: 6.1, minuteSubstituted: 46, notes: '', league: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.4, minuteSubstituted: 90, notes: '', name: '', league: '', apps: 27, goals: 5, assists: 4, keyPasses: 0.9, rating: 6.8, notes: '', name: ', club: '', league: '', apps: 26, goals: 3, assists: 3, keyPasses: 0.7, rating: 6.0, captain: true, minuteSubstituted: 90, notes: '', name: '', club: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.65, rating: 6.7, notes: ' },            { number: 8, position: '', apps: 24, goals: 7, assists: 2, keyPasses: 0.6, rating: 6.3, minuteSubstituted: 46, notes: '', club: '', league: '', apps: 23, goals: 4, assists: 3, keyPasses: 0.6, rating: 6.3, minuteSubstituted: 64, notes: '' }
          ],
          substitutes: [
            { number: 11, name: '', league: '', apps: 22, goals: 5, assists: 2, keyPasses: 0.5, rating: 6.2, minuteOn: 46, notes: ''3-4-3',
          players: [
            { number: 1, position: '', name: '', club: '', league: '', apps: 26, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.9, notes: '',
            { number: 21, position: '', club: '', league: '', apps: 26, goals: 2, assists: 3, keyPasses: 0.7, rating: 7.3, notes: '' },
            { number: 4, position: '', name: '', league: '', apps: 27, goals: 3, assists: 2, keyPasses: 0.4, rating: 7.9, captain: true, assist: true, notes: ' },
            { number: 22, position: '', club: ', league: '', apps: 28, goals: 2, assists: 4, keyPasses: 0.8, rating: 7.4, minuteSubstituted: 79, notes: '' },
            { number: 13, position: '', club: '', league: '', apps: 25, goals: 9, assists: 6, keyPasses: 1.1, rating: 7.6, goal: true, minuteSubstituted: 79, notes: '9' },
            { number: 15, position: '', club: '', apps: 26, goals: 8, assists: 7, keyPasses: 1.3, rating: 6.6, goal: true, minuteSubstituted: 73, notes: '' },
            { number: 7, position: '', apps: 27, goals: 4, assists: 6, keyPasses: 1.0, rating: 7.2, notes: '', apps: 28, goals: 7, assists: 9, keyPasses: 1.2, rating: 6.7, minuteSubstituted: 74, notes: '', club: '', league: '', apps: 30, goals: 10, assists: 5, keyPasses: 0.9, rating: 7.7, assist: true, notes: '', name: '', club: '', apps: 29, goals: 15, assists: 7, keyPasses: 1.0, rating: 9.6, goal: true, minuteSubstituted: 84, assist: true, notes: '' },
            { number: 14, position: '', club: '', league: '', apps: 27, goals: 11, assists: 8, keyPasses: 1.4, rating: 6.9, goal: true, notes: ' }
          ],
          substitutes: [
            { number: 16, name: '', league: '', apps: 25, goals: 6, assists: 5, keyPasses: 1.1, rating: 7.0, minuteOn: 74, notes: '',
            { number: 19, name: '', club: '', league: '', apps: 26, goals: 7, assists: 3, keyPasses: 0.8, rating: 6.8, minuteOn: 84, notes: '' }
          ],
          avgRating: 7.3,
          avgAge: 27.3,
          marketValue: 161,
          worldRanking: 17
        }
      },
      stats: {
        possession: { tunisia: '38%', japan: '62%' },
        shots: { tunisia: 2, japan: 11 },
        shotsOnTarget: { tunisia: 0, japan: 5 },
        shotsOffTarget: { tunisia: 2, japan: 6 },
        blockedShots: { tunisia: 5, japan: 4 },
        shotsInBox: { tunisia: 0, japan: 8 },
        shotsOutsideBox: { tunisia: 2, japan: 3 },
        bigChances: { tunisia: 0, japan: 4 },
        bigChancesMissed: { tunisia: 0, japan: 1 },
        corners: { tunisia: 3, japan: 5 },
        freeKicks: { tunisia: 4, japan: 7 },
        fouls: { tunisia: 8, japan: 15 },
        offsides: { tunisia: 1, japan: 1 },
        yellowCards: { tunisia: 0, japan: 0 },
        redCards: { tunisia: 0, japan: 0 },
        woodwork: { tunisia: 0, japan: 1 },
        passes: { tunisia: 376, japan: 609 },
        passAccuracy: { tunisia: 80, japan: 90 },
        finalThirdPasses: { tunisia: 59, japan: 267 },
        throughBalls: { tunisia: 1, japan: 10 },
        crosses: { tunisia: 13, japan: 30 },
        crossAccuracy: { tunisia: 2, japan: 8 },
        tackles: { tunisia: 22, japan: 14 },
        interceptions: { tunisia: 8, japan: 6 },
        clearances: { tunisia: 33, japan: 10 },
        saves: { tunisia: 1, japan: 0 }
      },
      xGData: {
        xG: { tunisia: 0.05, japan: 2.07 },
        xGOT: { tunisia: 0.03, japan: 1.95 },
        xA: { tunisia: 0.16, japan: 1.79 },
        xGA: { tunisia: 1.95, japan: 0.03 }
      },
      xARanking: {
        tunisia: [
          { name: '', xA: 0.69 },          { name: '', xA: 0.46 },          { name: ', xA: 0.38 },          { name: '', xA: 0.26 }        ]      },      halfTimeStats: {        htScore: '0-2',        xG: { tunisia: 0.02, japan: 1.14 },        shots: { tunisia: 0, japan: 5 },        shotsOnTarget: { tunisia: 0, japan: 3 },        possession: { tunisia: '34%', japan: '66%' }      },      secondHalfStats: {        xG: { tunisia: 0.03, japan: 0.93 },        shots: { tunisia: 2, japan: 6 },        shotsOnTarget: { tunisia: 0, japan: 2 },        possession: { tunisia: '42%', japan: '58%' }      },      accuracy: {        wdw: true, score: false, htft: true,        totalGoals: false, overUnder25: true, handicap: true,        total: '4/6', brierScore: 0.14      },      review: {        summary: '',        keyMoments: [          '',          '',          '',          '',          ''        ],        tacticalAnalysis: {          tunisia: '',          japan: ''        },        rootCauses: [          '',          '',          '',          '',          ''        ],        modelImprovement: [          '',          '',          ''
      },
      postMatchAnalysis: {
        keyFactor: '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: ''
        },
        teamTactics: {
          teamA: '',
          teamB: ''
        },
        substitutionImpact: '',
        winKeyFactors: ''
      },
      oddsResults: {
        wdw: {result:', odds:1.29, payout:'1.29},
        handicap: {result:'(+1), odds:1.91, payout:'1.91},
        correctScore: {result:'0:4', odds:18.00, payout:'18},
        totalGoals: {result:'4, odds:6.00, payout:'6},
        htft: {result:'', odds:1.85, payout:'1.85}      }    },    {      matchId: 'SUI-BIH-20260619',      competition: ''switzerland', teamB: 'bosnia'',      teamAName: '', teamBName: '',      venue: 'usa_west', stadium: 'SoFi ',      kickoff: '2026-06-19T07:00:00Z',      referee: '',      attendance: 0,      venueConditions: {        stadium: 'SoFi',        city: '',        grassType: 'natural',        grassCondition: 'good',        temperature: 22,        humidity: 57,        weather: 'cloudy',        altitude: 117,        windSpeed: 8,        windDirection: 'north',        feelsLike: 22,        impactAssessment: {          humidityImpact: 'low',          expectedEffect: 'neutral',          stormRisk: false,          windImpact: 'low'        }      },      significance: 'B-1',      preMatchAnalysis: {        xGData: {          teamA: { xG: 1.38, xGOT: 1.30, xA: 1.42, xGA: 0.49 },          teamB: { xG: 0.56, xGOT: 0.49, xA: 0.40, xGA: 1.30 }        },        predictedStats: {          possession: { teamA: '67%', teamB: '33%' },          shots: { teamA: 21, teamB: 9 },          shotsOnTarget: { teamA: 8, teamB: 3 },          shotsInBox: { teamA: 15, teamB: 4 },          shotsOutsideBox: { teamA: 6, teamB: 5 },          bigChances: { teamA: 4, teamB: 1 },          corners: { teamA: 10, teamB: 2 },          fouls: { teamA: 9, teamB: 18 },          offsides: { teamA: 2, teamB: 5 },          saves: { teamA: 3, teamB: 7 },          passes: { teamA: 710, teamB: 320 },          successfulPasses: { teamA: 646, teamB: 243 },          passAccuracy: { teamA: '91%', teamB: '76%' },          finalThirdPasses: { teamA: 286, teamB: 42 },          throughBalls: { teamA: 9, teamB: 2 },          crosses: { teamA: { total: 37, successful: 10 }, teamB: { total: 11, successful: 2 } },          tackles: { teamA: 14, teamB: 25 },          clearances: { teamA: 11, teamB: 33 }        },        injuries: {          teamA: {            status: 'minor',            suspensions: [],            absentees: [              { name: ', position: '', injury: '', status: ''low' }            ],            impactAssessment: '
          },
          teamB: {
            status: 'medium',
            suspensions: [
              { name: '', position: '', reason: '', impact: 'high' }
            ],
            absentees: [
              { name: '', position: '', injury: '', status: '', impact: 'low' }
            ],
            impactAssessment: ''4-2-3-1',            style: 'high_press_possession',            strategy: ['build_up', 'wing_play', 'vertical_penetration', 'set_pieces'],            keyPlayers: ['', ', ''],            implementation: {              possessionTarget: 67,              pressingIntensity: 'high',              wingFocus: 'both',              setPieceQuality: 'high',              verticalThreat: 'high'            }          },          teamB: {            formation: '4-4-2',            style: 'deep_block_counter',            strategy: ['compact_defence', 'quick_transition', 'aerial_threat'],            keyPlayers: [', ''],            implementation: {              defensiveBlock: 'deep',              counterSpeed: 'medium',              aerialThreat: 'medium',              setPieceQuality: 'medium'            }          },          tacticalMatchup: {            advantage: 'switzerland',            keyBattles: ['midfield_control', 'wing_attack_vs_defence', 'positional_play_vs_block'],            analysis: '+
          }
        },
        psychology: {
          teamA: {
            motivation: 'must_win',
            confidence: 'high',
            pressure: 'high',
            strengths: ['european_top_league_experience', 'possession_control', 'wing_attack'],
            weaknesses: ['counter_attack_vulnerability', 'finishing_under_pressure']
          },
          teamB: {
            motivation: 'must_not_lose',
            confidence: 'medium',
            pressure: 'high',
            strengths: ['defensive_discipline', 'aerial_presence', 'mental_toughness'],
            weaknesses: ['midfield_creativity', 'slow_defenders', 'fatigue_after_60']
          }
        },
        leagueGapAnalysis: {
          teamA: '',
          teamB: ''4-2-3-1',          avgRating: 7.32,          avgAge: 27.2,          coach: 'Murat Yakin',          players: [            { number: 1, position: '', name: ', club: '', league: '', apps: 29, goals: 0, assists: 0, keyPasses: 0.12, rating: 7.4, notes: '', name: '', club: '', apps: 26, goals: 2, assists: 2, keyPasses: 0.6, rating: 7.1, notes: '' },            { number: 4, position: ', name: '', club: '', apps: 28, goals: 1, assists: 1, keyPasses: 0.35, rating: 7.3, notes: '' },            { number: 5, position: ', name: '', league: '', apps: 27, goals: 2, assists: 3, keyPasses: 0.45, rating: 7.5, notes: '' },            { number: 13, position: ', name: '', league: '', apps: 25, goals: 3, assists: 6, keyPasses: 0.9, rating: 7.2, notes: '' },            { number: 8, position: '', name: '', club: ', league: '', apps: 26, goals: 2, assists: 4, keyPasses: 0.8, rating: 7.0, notes: '' },            { number: 10, position: '', name: '', club: '', league: '', apps: 30, goals: 4, assists: 10, keyPasses: 1.4, rating: 7.8, captain: true, notes: '', name: ', club: '', league: '', apps: 27, goals: 10, assists: 7, keyPasses: 1.2, rating: 7.3, notes: '', name: '', club: '', league: '', apps: 25, goals: 6, assists: 8, keyPasses: 1.3, rating: 7.4, notes: ' },            { number: 9, position: '', name: '', league: '', apps: 24, goals: 5, assists: 5, keyPasses: 1.0, rating: 7.1, notes: '' },            { number: 7, position: '', name: ', club: '', apps: 28, goals: 12, assists: 5, keyPasses: 0.85, rating: 7.2, notes: ' }
          ],
          keyPlayers: ['','','',''],
          marketValue: 95
        },
        teamB: {
          formation: '4-4-2',
          avgRating: 6.38,
          avgAge: 28.1,
          coach: 'Safet Susic',
          players: [
            { number: 1, position: '', name: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.2, notes: '' },
            { number: 5, position: '', club: '', league: '', apps: 24, goals: 1, assists: 4, keyPasses: 0.7, rating: 6.8, notes: '',
            { number: 4, position: '', club: ', league: '', apps: 25, goals: 1, assists: 0, keyPasses: 0.25, rating: 6.3, notes: '' },
            { number: 18, position: '', club: '', apps: 24, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.4, notes: ' },
            { number: 7, position: '', club: '', league: '', apps: 23, goals: 0, assists: 2, keyPasses: 0.4, rating: 6.1, notes: '',
            { number: 15, position: '', club: '', league: '' },
            { number: 6, position: '', apps: 25, goals: 2, assists: 4, keyPasses: 0.75, rating: 6.5, notes: '' },
            { number: 13, position: ''-, club: '', apps: 26, goals: 3, assists: 5, keyPasses: 0.8, rating: 6.7, notes: 'B2B' },
            { number: 20, position: '', club: '', league: '', apps: 23, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.3, notes: '' },
            { number: 25, position: '', name: '', league: '', apps: 26, goals: 9, assists: 4, keyPasses: 0.65, rating: 6.6, notes: '' },
            { number: 10, position: '', name: '', league: '', apps: 27, goals: 11, assists: 3, keyPasses: 0.7, rating: 6.9, captain: true, notes: '' }          ],          keyPlayers: ['','',''],          marketValue: 35        }      },      preMatchPrediction: {        model: 'v4.8-full-integration',        wdw: {winA:0.68, draw:0.22, winB:0.10, recommendation:''},        confidence: {level:', agreeCount:'5/6'},        scores: [{score:'2-0',prob:0.20},{score:'1-0',prob:0.18},{score:'2-1',prob:0.14}],        htft: [{ht:',ft:',prob:0.35},{ht:',ft:',prob:0.16},{ht:',ft:',prob:0.13}],        totalGoals: {expected:2.0, over25:0.54, under25:0.46},        modelComponents: {          poisson: {winA:0.65, draw:0.22, winB:0.13},          dixonCole: {winA:0.63, draw:0.24, winB:0.13},          ssm: {winA:0.66, draw:0.21, winB:0.13},          xgboost: {winA:0.82, draw:0.10, winB:0.08},          lightgbm: {winA:0.78, draw:0.12, winB:0.10},          elo: {winA:0.58, draw:0.24, winB:0.18}        }      },      riskFactors: {        teamA: {          high: ['counter_attack_exposure', 'finishing_efficiency'],          medium: ['defensive_width', 'set_piece_defence']        },        teamB: {          high: ['midfield_creativity', 'left_back_vulnerability', 'goalkeeper_experience'],          medium: ['fatigue', 'aerial_defence']        }      },      recentMatches: {        teamA: [          { date: '2026-06-14', competition: ', venue: 'neutral', opponent: ''1-1', htResult: '1-0', corners: { teamA: 10, teamB: 3 }, outcome: 'draw' },          { date: '2025-11-19', competition: ', venue: 'away', opponent: ''1-1', htResult: '0-0', corners: { teamA: 1, teamB: 3 }, outcome: 'draw' },          { date: '2025-11-16', competition: ', venue: 'home', opponent: '', result: '4-1', htResult: '1-1', corners: { teamA: 4, teamB: 3 }, outcome: 'win' },          { date: '2025-10-14', competition: ''away', opponent: ''0-0', htResult: '0-0', corners: { teamA: 4, teamB: 3 }, outcome: 'draw' },          { date: '2025-10-11', competition: ''away', opponent: '', result: '2-0', htResult: '0-0', corners: { teamA: 9, teamB: 4 }, outcome: 'win' },          { date: '2025-09-09', competition: ', venue: 'home', opponent: ''3-0', htResult: '3-0', corners: { teamA: 5, teamB: 2 }, outcome: 'win' },          { date: '2025-09-06', competition: ', venue: 'home', opponent: ''4-0', htResult: '4-0', corners: { teamA: 3, teamB: 4 }, outcome: 'win' },          { date: '2024-11-19', competition: ', venue: 'away', opponent: ''2-3', htResult: '0-1', corners: { teamA: 3, teamB: 7 }, outcome: 'loss' },          { date: '2024-11-16', competition: ', venue: 'home', opponent: '', result: '1-1', htResult: '0-0', corners: { teamA: 5, teamB: 3 }, outcome: 'draw' },          { date: '2024-10-16', competition: ''home', opponent: '', result: '2-2', htResult: '2-1', corners: { teamA: 6, teamB: 4 }, outcome: 'draw' }
        ],
        teamB: [
          { date: '2026-06-13', competition: ''neutral', opponent: ''1-1', htResult: '1-0', corners: { teamA: 4, teamB: 9 }, outcome: 'draw' },
          { date: '2026-04-01', competition: ''home', opponent: ''1-1', htResult: '1-0', corners: { teamA: 7, teamB: 2 }, outcome: 'draw' },
          { date: '2026-03-27', competition: ''away', opponent: ''1-1', htResult: '0-0', corners: { teamA: 3, teamB: 5 }, outcome: 'draw' },
          { date: '2025-11-19', competition: ''away', opponent: ''1-1', htResult: '1-0', corners: { teamA: 2, teamB: 4 }, outcome: 'draw' },
          { date: '2025-11-16', competition: ''home', opponent: '', result: '3-1', htResult: '1-0', corners: { teamA: 4, teamB: 1 }, outcome: 'win' },
          { date: '2025-10-10', competition: ''away', opponent: '', result: '2-2', htResult: '2-1', corners: { teamA: 5, teamB: 3 }, outcome: 'draw' },
          { date: '2025-09-10', competition: ''home', opponent: ''1-2', htResult: '0-0', corners: { teamA: 3, teamB: 2 }, outcome: 'loss' },
          { date: '2025-09-07', competition: ''away', opponent: '', result: '6-0', htResult: '1-0', corners: { teamA: 6, teamB: 0 }, outcome: 'win' },
          { date: '2025-06-07', competition: ''home', opponent: '', result: '1-0', htResult: '0-0', corners: { teamA: 5, teamB: 2 }, outcome: 'win' },
          { date: '2025-03-25', competition: ''home', opponent: '', result: '2-1', htResult: '1-1', corners: { teamA: 7, teamB: 7 }, outcome: 'win' }        ],        teamAStats: {          matches: 10, wins: 4, draws: 5, losses: 1, goalsFor: 19, goalsAgainst: 10, avgCorners: 4.7, avgGoals: 1.9, winRate: 40, drawRate: 50, lossRate: 10        },        teamBStats: {          matches: 10, wins: 4, draws: 5, losses: 1, goalsFor: 19, goalsAgainst: 11, avgCorners: 4.6, avgGoals: 1.9, winRate: 40, drawRate: 50, lossRate: 10        }      },      oddsData: {        wdw: [          { time: '2026-06-16 09:29:38', winA: 1.74, draw: 3.20, winB: 4.15, overround: 1.08 },          { time: '2026-06-17 19:33:34', winA: 1.69, draw: 3.25, winB: 4.35, overround: 1.08 },          { time: '2026-06-18 11:01:16', winA: 1.66, draw: 3.36, winB: 4.35, overround: 1.09 },          { time: '2026-06-18 11:34:09', winA: 1.63, draw: 3.46, winB: 4.40, overround: 1.09 },          { time: '2026-06-18 12:21:42', winA: 1.66, draw: 3.36, winB: 4.35, overround: 1.09 },          { time: '2026-06-18 16:19:19', winA: 1.64, draw: 3.40, winB: 4.43, overround: 1.09 },          { time: '2026-06-18 17:17:00', winA: 1.61, draw: 3.50, winB: 4.50, overround: 1.10 }        ],        handicap: [          { time: '2026-06-16 09:29:38', winA: 3.36, draw: 3.40, winB: 1.86, line: -1 },          { time: '2026-06-17 19:33:43', winA: 3.25, draw: 3.32, winB: 1.92, line: -1 },          { time: '2026-06-18 11:34:15', winA: 3.15, draw: 3.32, winB: 1.96, line: -1 },          { time: '2026-06-18 12:21:37', winA: 3.20, draw: 3.35, winB: 1.93, line: -1 },          { time: '2026-06-18 15:43:32', winA: 3.12, draw: 3.35, winB: 1.96, line: -1 },          { time: '2026-06-18 16:53:43', winA: 3.05, draw: 3.35, winB: 1.99, line: -1 },          { time: '2026-06-18 17:17:11', winA: 3.00, draw: 3.30, winB: 2.03, line: -1 }        ],        totalGoals: [          { time: '2026-06-16 09:29:38', g0: 9.25, g1: 4.25, g2: 3.10, g3: 3.65, g4: 6.20, g5: 12.50, g6: 23.00, g7p: 34.00 },          { time: '2026-06-18 17:07:27', g0: 9.60, g1: 4.40, g2: 3.10, g3: 3.50, g4: 6.20, g5: 12.50, g6: 23.00, g7p: 34.00 }        ],        htft: [          { time: '2026-06-16 09:29:38', ww: 2.70, wt: 15.00, wl: 36.00, tw: 4.30, tt: 5.00, tl: 9.00, lw: 23.00, lt: 15.00, ll: 7.35 },          { time: '2026-06-18 11:35:04', ww: 2.55, wt: 14.00, wl: 32.00, tw: 4.40, tt: 5.30, tl: 10.00, lw: 21.00, lt: 14.00, ll: 8.00 }        ],        correctScore: [          { time: '2026-06-16 09:29:38', s10: 5.50, s20: 5.55, s21: 6.80, s30: 8.00, s31: 11.00, s32: 33.00, s40: 18.00, s41: 26.00, s42: 75.00, s00: 10.50, s11: 7.00, s22: 20.00, s01: 16.00, s02: 45.00, s12: 19.00 },          { time: '2026-06-18 17:11:00', s10: 6.00, s20: 5.75, s21: 6.00, s30: 9.25, s31: 9.50, s32: 30.00, s40: 21.00, s41: 27.00, s42: 65.00, s00: 11.50, s11: 6.60, s22: 18.00, s01: 16.00, s02: 45.00, s12: 19.00 }        ],        latestOdds: {          wdw: { winA: 1.61, draw: 3.50, winB: 4.50 },          handicap: { winA: 3.00, draw: 3.30, winB: 2.03 },          totalGoals: { under25: 2.03, over25: 1.77 },          impliedProb: { winA: 0.54, draw: 0.26, winB: 0.20 }        }      },      matchKey: ''CAN-QAT-20260619',
      competition: ''canada', teamB: 'qatar'',
      teamAName: ''canada_west', stadium: '',
      kickoff: '2026-06-19T10:00:00Z',
      referee: '',
      attendance: 0,
      venueConditions: {
        stadium: '',
        city: '',
        grassType: 'artificial',
        grassCondition: 'good',
        temperature: 25,
        humidity: 38,
        weather: 'sunny',
        altitude: 45,
        windSpeed: 5,
        windDirection: 'north',
        feelsLike: 25,
        impactAssessment: {
          humidityImpact: 'low',
          expectedEffect: 'neutral',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      significance: ''65%', teamB: '35%' },
          shots: { teamA: 19, teamB: 7 },
          shotsOnTarget: { teamA: 7, teamB: 2 },
          shotsInBox: { teamA: 14, teamB: 3 },
          shotsOutsideBox: { teamA: 5, teamB: 4 },
          bigChances: { teamA: 4, teamB: 1 },
          corners: { teamA: 9, teamB: 2 },
          fouls: { teamA: 10, teamB: 19 },
          offsides: { teamA: 2, teamB: 5 },
          saves: { teamA: 2, teamB: 7 },
          passes: { teamA: 680, teamB: 310 },
          successfulPasses: { teamA: 605, teamB: 235 },
          passAccuracy: { teamA: '89%', teamB: '76%' },
          finalThirdPasses: { teamA: 258, teamB: 40 },
          throughBalls: { teamA: 8, teamB: 1 },
          crosses: { teamA: { total: 34, successful: 9 }, teamB: { total: 9, successful: 1 } },
          tackles: { teamA: 16, teamB: 27 },
          clearances: { teamA: 12, teamB: 35 }
        },
        injuries: {
          teamA: {
            status: 'medium',
            suspensions: [],
            absentees: [
              { name: '', position: '', injury: '', status: '', impact: 'high' },
              { name: '', injury: '', status: ', impact: 'medium' }
            ],
            impactAssessment: ''
          },
          teamB: {
            status: 'none',
            suspensions: [],
            absentees: [],
            impactAssessment: ''
          }
        },
        tactics: {
          teamA: {
            formation: '4-4-2',
            style: 'high_press_possession',
            strategy: ['build_up', 'wing_play', 'aerial_bombing', 'set_pieces'],
            keyPlayers: ['', '', ''],
            implementation: {
              possessionTarget: 65,
              pressingIntensity: 'high',
              wingFocus: 'both',
              setPieceQuality: 'high',
              aerialThreat: 'high'
            }
          },
          teamB: {
            formation: '4-3-3',
            style: 'deep_defence_counter',
            strategy: ['compact_block', 'quick_transition', 'set_pieces'],
            keyPlayers: ['', '',
            implementation: {
              defensiveBlock: 'deep',
              counterSpeed: 'medium',
              aerialThreat: 'low',
              setPieceQuality: 'medium'
            }
          },
          tacticalMatchup: {
            advantage: 'canada',
            keyBattles: ['midfield_control', 'wing_attack_vs_defence', 'aerial_duels'],
            analysis: ''must_win',            confidence: 'high',            pressure: 'extreme',            strengths: ['home_advantage', 'physicality', 'european_league_experience'],            weaknesses: ['counter_attack_vulnerability', 'wing_depth_after_davis']          },          teamB: {            motivation: 'must_not_lose',            confidence: 'medium',            pressure: 'high',            strengths: ['defensive_discipline', 'technical_skills', 'mental_toughness'],            weaknesses: ['physicality_gap', 'finishing_ability', 'fatigue_after_60']          }        },        leagueGapAnalysis: {          teamA: '',          teamB: '
        }
      },
      lineups: {
        teamA: {
          formation: '4-4-2',
          avgRating: 7.18,
          avgAge: 26.5,
          coach: 'John Herdman',
          players: [
            { number: 16, position: '', name: '', league: '', apps: 27, goals: 0, assists: 0, keyPasses: 0.1, rating: 7.1, notes: '',
            { number: 2, position: '', club: '', league: '', apps: 25, goals: 3, assists: 4, keyPasses: 0.7, rating: 7.0, notes: ' },
            { number: 4, position: '', club: '', league: '', apps: 26, goals: 1, assists: 1, keyPasses: 0.3, rating: 7.2, notes: '',
            { number: 13, position: '', club: '', apps: 28, goals: 2, assists: 1, keyPasses: 0.25, rating: 6.8, notes: '',
            { number: 22, position: '', club: '', apps: 24, goals: 2, assists: 3, keyPasses: 0.8, rating: 6.9, notes: ' },
            { number: 17, position: '', club: '', apps: 26, goals: 5, assists: 6, keyPasses: 1.1, rating: 7.3, notes: ' },
            { number: 7, position: '', club: '', apps: 29, goals: 4, assists: 9, keyPasses: 1.3, rating: 7.6, captain: true, notes: '' },
            { number: 8, position: '', club: '', league: '', apps: 27, goals: 3, assists: 4, keyPasses: 0.9, rating: 7.2, notes: '' },
            { number: 11, position: '', club: '', league: '', apps: 24, goals: 2, assists: 3, keyPasses: 0.7, rating: 6.8, notes: '' },
            { number: 10, position: '', name: '', club: '', league: '', apps: 30, goals: 15, assists: 7, keyPasses: 1.0, rating: 7.7, notes: '' },
            { number: 9, position: '', name: '', club: '', apps: 28, goals: 10, assists: 4, keyPasses: 0.8, rating: 7.3, notes: '','','',''],
          marketValue: 85
        },
        teamB: {
          formation: '4-3-3',
          avgRating: 6.33,
          avgAge: 27.2,
          coach: 'Carlos Queiroz',
          players: [
            { number: 1, position: '', name: '', club: '', league: '', apps: 29, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.3, notes: '' },
            { number: 14, position: '', club: '', league: '', apps: 26, goals: 1, assists: 3, keyPasses: 0.4, rating: 6.1, notes: '' },
            { number: 2, position: '', club: '', league: '', apps: 27, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.5, notes: '' },
            { number: 16, position: ''-', club: '', league: '', apps: 25, goals: 1, assists: 0, keyPasses: 0.2, rating: 6.3, notes: ' },
            { number: 13, position: '', club: '', apps: 23, goals: 0, assists: 2, keyPasses: 0.5, rating: 6.2, notes: '' },
            { number: 4, position: '', league: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.3, notes: '',
            { number: 23, position: '', name: '', league: '', apps: 28, goals: 3, assists: 4, keyPasses: 0.8, rating: 6.6, notes: '' },
            { number: 5, position: '', club: '', league: '', apps: 24, goals: 1, assists: 2, keyPasses: 0.5, rating: 6.2, notes: '',
            { number: 11, position: '', club: '', league: '', apps: 29, goals: 12, assists: 11, keyPasses: 1.4, rating: 7.2, captain: true, notes: '' },
            { number: 15, position: '', name: '', league: '', apps: 27, goals: 9, assists: 5, keyPasses: 0.6, rating: 6.5, notes: ' },
            { number: 8, position: '', club: ', league: '', apps: 26, goals: 7, assists: 4, keyPasses: 0.8, rating: 6.6, notes: ''7.2','6.6','(6.6'],          marketValue: 25        }      },      preMatchPrediction: {        model: 'v4.8-full-integration',        wdw: {winA:0.70, draw:0.20, winB:0.10, recommendation:''},        confidence: {level:', agreeCount:'6/6'},        scores: [{score:'2-0',prob:0.21},{score:'1-0',prob:0.19},{score:'2-1',prob:0.14}],        htft: [{ht:',ft:',prob:0.36},{ht:',ft:',prob:0.15},{ht:',ft:',prob:0.12}],        totalGoals: {expected:2.1, over25:0.56, under25:0.44},        modelComponents: {          poisson: {winA:0.67, draw:0.21, winB:0.12},          dixonCole: {winA:0.65, draw:0.23, winB:0.12},          ssm: {winA:0.68, draw:0.20, winB:0.12},          xgboost: {winA:0.84, draw:0.09, winB:0.07},          lightgbm: {winA:0.81, draw:0.11, winB:0.08},          elo: {winA:0.60, draw:0.24, winB:0.16}        }      },      riskFactors: {        teamA: {          high: ['counter_attack_exposure', 'left_back_vulnerability'],          medium: ['finishing_efficiency', 'central_defence_pace']        },        teamB: {          high: ['midfield_creativity', 'aerial_defence', 'goalkeeper_experience'],          medium: ['physicality_gap', 'fatigue']        }      },      recentMatches: {        teamA: [          { date: '2026-06-13', competition: ''home', opponent: '', result: '1-1', htResult: '0-1', corners: { teamA: 9, teamB: 4 }, outcome: 'draw' },          { date: '2025-06-30', competition: ', venue: 'neutral', opponent: '', result: '1-1', htResult: '1-0', corners: { teamA: 9, teamB: 4 }, outcome: 'draw' },          { date: '2025-06-25', competition: ''neutral', opponent: '', result: '2-0', htResult: '0-0', corners: { teamA: 12, teamB: 1 }, outcome: 'win' },          { date: '2025-06-22', competition: ', venue: 'neutral', opponent: ''1-1', htResult: '1-0', corners: { teamA: 3, teamB: 2 }, outcome: 'draw' },          { date: '2025-06-18', competition: ', venue: 'home', opponent: '', result: '6-0', htResult: '2-0', corners: { teamA: 3, teamB: 3 }, outcome: 'win' },          { date: '2025-03-24', competition: ''home', opponent: '', result: '2-1', htResult: '1-1', corners: { teamA: 3, teamB: 3 }, outcome: 'win' },          { date: '2025-03-21', competition: ', venue: 'neutral', opponent: ''0-2', htResult: '0-1', corners: { teamA: 7, teamB: 2 }, outcome: 'loss' },          { date: '2024-11-20', competition: ', venue: 'home', opponent: ''3-0', htResult: '2-0', corners: { teamA: 5, teamB: 2 }, outcome: 'win' },          { date: '2024-07-14', competition: ', venue: 'neutral', opponent: ''2-2', htResult: '1-1', corners: { teamA: 7, teamB: 6 }, outcome: 'draw' },          { date: '2024-06-30', competition: ', venue: 'neutral', opponent: '', result: '0-0', htResult: '0-0', corners: { teamA: 8, teamB: 4 }, outcome: 'draw' }
        ],
        teamB: [
          { date: '2026-06-14', competition: ''neutral', opponent: '', result: '1-1', htResult: '0-1', corners: { teamA: 3, teamB: 10 }, outcome: 'draw' },
          { date: '2025-12-08', competition: '', venue: 'neutral', opponent: ''0-3', htResult: '0-1', corners: { teamA: 10, teamB: 4 }, outcome: 'loss' },
          { date: '2025-12-05', competition: '', venue: 'away', opponent: ''1-1', htResult: '0-0', corners: { teamA: 7, teamB: 4 }, outcome: 'draw' },
          { date: '2025-12-01', competition: '', venue: 'home', opponent: '', result: '0-1', htResult: '0-0', corners: { teamA: 6, teamB: 3 }, outcome: 'loss' },
          { date: '2025-10-15', competition: ''home', opponent: ''2-1', htResult: '0-0', corners: { teamA: 1, teamB: 2 }, outcome: 'win' },
          { date: '2025-10-08', competition: ''neutral', opponent: '', result: '0-0', htResult: '0-0', corners: { teamA: 5, teamB: 2 }, outcome: 'draw' },
          { date: '2025-06-10', competition: ''away', opponent: '', result: '0-3', htResult: '0-1', corners: { teamA: 1, teamB: 6 }, outcome: 'loss' },
          { date: '2025-06-06', competition: ''home', opponent: '', result: '1-0', htResult: '1-0', corners: { teamA: 3, teamB: 3 }, outcome: 'win' },
          { date: '2025-03-25', competition: ''away', opponent: '', result: '1-3', htResult: '0-1', corners: { teamA: 6, teamB: 5 }, outcome: 'loss' },
          { date: '2025-03-21', competition: ''home', opponent: '', result: '5-1', htResult: '3-0', corners: { teamA: 4, teamB: 7 }, outcome: 'win' }        ],        teamAStats: {          matches: 10, wins: 4, draws: 5, losses: 1, goalsFor: 17, goalsAgainst: 9, avgCorners: 6.7, avgGoals: 1.7, winRate: 40, drawRate: 50, lossRate: 10        },        teamBStats: {          matches: 10, wins: 3, draws: 2, losses: 5, goalsFor: 11, goalsAgainst: 15, avgCorners: 4.6, avgGoals: 1.1, winRate: 30, drawRate: 20, lossRate: 50        }      },      oddsData: {        wdw: [          { time: '2026-06-16 09:29:38', winA: 1.24, draw: 4.70, winB: 9.10, overround: 1.08 },          { time: '2026-06-18 12:26:41', winA: 1.20, draw: 5.00, winB: 10.50, overround: 1.09 },          { time: '2026-06-18 15:50:50', winA: 1.18, draw: 5.25, winB: 11.00, overround: 1.10 },          { time: '2026-06-18 17:05:40', winA: 1.17, draw: 5.45, winB: 11.00, overround: 1.11 }        ],        handicap: [          { time: '2026-06-16 09:29:38', winA: 1.87, draw: 3.45, winB: 3.28, line: -1 },          { time: '2026-06-18 12:26:49', winA: 1.76, draw: 3.55, winB: 3.60, line: -1 },          { time: '2026-06-18 15:50:50', winA: 1.72, draw: 3.55, winB: 3.75, line: -1 },          { time: '2026-06-18 17:05:48', winA: 1.69, draw: 3.55, winB: 3.90, line: -1 }        ],        totalGoals: [          { time: '2026-06-16 09:29:38', g0: 13.00, g1: 5.20, g2: 3.40, g3: 3.45, g4: 5.25, g5: 9.00, g6: 17.00, g7p: 25.00 },          { time: '2026-06-18 17:07:09', g0: 16.00, g1: 5.65, g2: 3.50, g3: 3.35, g4: 4.90, g5: 8.75, g6: 15.00, g7p: 22.00 }        ],        htft: [          { time: '2026-06-16 09:29:38', ww: 1.70, wt: 23.00, wl: 65.00, tw: 3.55, tt: 7.25, tl: 20.00, lw: 28.00, lt: 23.00, ll: 17.00 },          { time: '2026-06-18 17:06:13', ww: 1.65, wt: 20.00, wl: 50.00, tw: 3.65, tt: 8.30, tl: 23.00, lw: 22.00, lt: 20.00, ll: 22.00 }        ],        correctScore: [          { time: '2026-06-16 09:29:38', s10: 6.00, s20: 5.50, s21: 6.75, s30: 7.00, s31: 10.00, s32: 32.00, s40: 13.50, s41: 21.00, s42: 60.00, s00: 13.00, s11: 7.75, s22: 21.00, s01: 21.00, s02: 65.00, s12: 23.00 },          { time: '2026-06-18 17:16:17', s10: 6.50, s20: 4.85, s21: 6.00, s30: 7.00, s31: 8.50, s32: 30.00, s40: 13.50, s41: 21.00, s42: 50.00, s00: 16.00, s11: 8.50, s22: 26.00, s01: 27.00, s02: 70.00, s12: 27.00 }        ],        latestOdds: {          wdw: { winA: 1.17, draw: 5.45, winB: 11.00 },          handicap: { winA: 1.69, draw: 3.55, winB: 3.90 },          totalGoals: { under25: 3.35, over25: 2.65 },          impliedProb: { winA: 0.78, draw: 0.15, winB: 0.07 }        }      },      events: [        {min:16, type:'goal', team:'canada', player:'', assist:''},        {min:29, type:'goal', team:'canada', player:'', assist:', desc:''},        {min:33, type:'red', team:'qatar', player:'', desc:''sub', team:'qatar', playerOut:', playerIn:'', desc:''goal', team:'canada', player:'', assist:', desc:''},        {min:46, type:'sub', team:'qatar', playerOut:'', desc:''sub', team:'qatar', playerOut:'', playerIn:'', desc:''},        {min:46, type:'sub', team:'qatar', playerOut:'', desc:''red', team:'qatar', player:''sub', team:'canada', playerOut:'', playerIn:'', desc:''goal', team:'canada', player:', desc:''},        {min:71, type:'sub', team:'canada', playerOut:'', desc:''},        {min:71, type:'sub', team:'canada', playerOut:'', playerIn:'', desc:''},        {min:75, type:'own_goal', team:'qatar', player:'-', assist:', desc:''},        {min:83, type:'sub', team:'canada', playerOut:'', desc:''},        {min:90, type:'goal', team:'canada', player:'', assist:', desc:''}
      ],
      result: {
        htScore: '3-0', ftScore: '6-0',
        goalsA: 6, goalsB: 0, totalGoals: 6,
        halfTime: {a:3, b:0},
        wdw: 'winA', htWdw: 'winA', htft: '',
        handicap: 'win', overUnder25: 'over',
        redCards: {canada:0, qatar:2}
      },
      lineups: {
        teamA: {
          formation: '4-4-2', coach: 'John Herdman',
          avgRating: 7.18, avgAge: 26.5,
          starting: [
            {num:16, name:'', league:'', apps:27, goals:0, assists:0, keyPasses:0.1, rating:6.6, position:'', notes:'',
            {num:2, name:'', club:'', league:'', apps:25, goals:3, assists:4, keyPasses:0.7, rating:8.6, position:''},
            {num:4, name:'', league:'', apps:26, goals:1, assists:1, keyPasses:0.3, rating:7.3, position:', substituted:71, notes:''},
            {num:13, name:'', league:'', substituted:46, notes:'',
            {num:22, name:'', league:'', apps:24, goals:2, assists:3, keyPasses:0.8, rating:8.8, position:''},
            {num:17, name:'', apps:26, goals:5, assists:6, keyPasses:1.1, rating:8.0, position:''},
            {num:7, name:'', league:'', apps:29, goals:4, assists:9, keyPasses:1.3, rating:8.6, position:''},
            {num:8, name:'', club:'', league:'', apps:27, goals:3, assists:4, keyPasses:0.9, rating:7.1, position:''},
            {num:20, name:'', club:'', league:'', substituted:71, notes:''},
            {num:10, name:'', club:'', league:'', apps:30, goals:15, assists:7, keyPasses:1.0, rating:9.0, position:'', notes:''},
            {num:9, name:'', club:'', apps:28, goals:10, assists:4, keyPasses:0.8, rating:6.9, position:'', notes:''}          ],          substitutions: [            {num:24, name:'-', rating:6.5, substitutedIn:57},            {num:14, name:', rating:6.7, substitutedIn:71},            {num:21, name:'', rating:6.0, substitutedIn:71},            {num:18, name:''}
          ],
          keyPlayers: ['','','','','']
        },
        teamB: {
          formation: '4-3-3', coach: 'Carlos Queiroz',
          avgRating: 6.33, avgAge: 27.2,
          starting: [
            {num:1, name:'', club:'', league:'', apps:29, goals:0, assists:0, keyPasses:0.0, rating:6.2, position:'', notes:'',
            {num:14, name:'', club:'', league:'', apps:26, goals:1, assists:3, keyPasses:0.4, rating:4.5, position:'', league:'', apps:27, goals:1, assists:1, keyPasses:0.3, rating:6.7, position:''},
            {num:16, name:'', club:'', league:'', apps:25, goals:1, assists:0, keyPasses:0.2, rating:6.8, position:'', apps:23, goals:0, assists:2, keyPasses:0.5, rating:5.6, position:''},
            {num:4, name:'', league:'', apps:25, goals:2, assists:3, keyPasses:0.6, rating:6.8, position:', notes:''},
            {num:23, name:'', league:'', apps:28, goals:3, assists:4, keyPasses:0.8, rating:5.6, position:'', redCard:true, notes:'10'},
            {num:5, name:'', league:'', apps:24, goals:1, assists:2, keyPasses:0.5, rating:6.5, position:''},
            {num:11, name:'', league:'', apps:29, goals:12, assists:11, keyPasses:1.4, rating:7.5, position:''},
            {num:15, name:'', league:'', apps:27, goals:9, assists:5, keyPasses:0.6, rating:6.5, position:'', substituted:40, notes:''},
            {num:8, name:'', club:'', apps:26, goals:7, assists:4, keyPasses:0.8, rating:6.5, position:', substituted:46, notes:''.5','6.8','(6.8'],          marketValue: 25        }      },      stats: {        possession: {canada:'79%', qatar:'21%'},        shots: {canada:32, qatar:2},        shotsOnTarget: {canada:10, qatar:0},        shotsOffTarget: {canada:22, qatar:2},        blockedShots: {canada:6, qatar:0},        shotsInBox: {canada:24, qatar:0},        shotsOutsideBox: {canada:8, qatar:2},        bigChances: {canada:5, qatar:0},        bigChancesMissed: {canada:2, qatar:0},        corners: {canada:19, qatar:1},        freeKicks: {canada:10, qatar:3},        fouls: {canada:9, qatar:10},        offsides: {canada:1, qatar:1},        yellowCards: {canada:1, qatar:1},        redCards: {canada:0, qatar:2},        saves: {canada:0, qatar:5},        passes: {canada:566, qatar:164},        successfulPasses: {canada:517, qatar:107},        passAccuracy: {canada:'91%', qatar:'65%'},        finalThirdPasses: {canada:294, qatar:11},        throughBalls: {canada:11, qatar:0},        crosses: {canada:{total:41, successful:12}, qatar:{total:5, successful:0}},        dangerousAttacks: {canada:154, qatar:3},        tackles: {canada:9, qatar:17},        clearances: {canada:8, qatar:42}      },      xGData: {        canada: {xG:4.53, xGOT:4.37, xA:3.12, xGA:0.14},        qatar: {xG:0.18, xGOT:0.14, xA:0.11, xGA:4.37}      },      goalDetails: [        {min:16, scorer:'', assist:', xA:0.71, type:''},        {min:29, scorer:'', assist:''},        {min:45, scorer:'', assist:', xA:0.38, type:''},        {min:64, scorer:'', type:''-', assist:', xA:0.71, type:''},        {min:90, scorer:'', assist:''}
      ],
      xARanking: {
        canada: [
          {name:'',
          {name:'',
          {name:'',
          {name:'', xA:0.11}]      },      halfTimeStats: {        htScore: '3-0',        xG: {canada:2.48, qatar:0.09},        shots: {canada:18, qatar:1},        shotsOnTarget: {canada:6, qatar:0},        possession: {canada:'77%', qatar:'23%'}      },      secondHalfStats: {        xG: {canada:2.05, qatar:0.09},        shots: {canada:14, qatar:1},        shotsOnTarget: {canada:4, qatar:0},        possession: {canada:'81%', qatar:'19%'}      },      accuracy: {        wdw: true, score: false, htft: true,        totalGoals: false, overUnder25: true, handicap: true,        total: '4/6', brierScore: 0.42      },      review: {        summary: '',        keyMoments: [          '',          '',          '',          '',          '',          '',          '',          ''        ],        tacticalAnalysis: {          canada: '',          qatar: '',
          '',
          '',
          '',
          ''
        ],
        modelImprovement: [
          '',
          '',
          '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        xGAnalysis: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: ''
        },
        teamTactics: {
          teamA: '',
          teamB: ''
        },
        substitutionImpact: '',
        oddsResults: {
          wdw: {result:', odds:1.17, payout:'1.17},
          handicap: {result:'(-1), odds:1.67, payout:'1.67},
          correctScore: {result:''27},
          totalGoals: {result:'6, odds:15.00, payout:'15},
          htft: {result:'', odds:1.65, payout:'1.65}        }      },      matchKey: ''    },    {      matchId: 'CAN-BOS-20260612',      competition: ''canada', teamB: 'bosnia'',      teamAName: '',      venue: 'canada', stadium: '',      kickoff: '2026-06-13T03:00:00Z',      referee: '',      attendance: 30000,      events: [        {min:11, type:'yellow', team:'canada', player:'', desc:''},        {min:17, type:'shot', team:'canada', player:'', desc:''},        {min:21, type:'goal', team:'bosnia', player:''},        {min:45, type:'yellow', team:'bosnia', player:''},        {min:45, type:'yellow', team:'bosnia', player:', desc:''},        {min:53, type:'shot_on_post', team:'canada', player:''},        {min:56, type:'save', team:'canada', player:', desc:''},        {min:66, type:'goal_line_clear', team:'bosnia', player:''goal', team:'canada', player:'', assist:'', desc:''},        {min:84, type:'sub_injury', team:'bosnia', player:''}
      ],
      result: {
        htScore: '0-1', ftScore: '1-1',
        goalsA: 1, goalsB: 1, totalGoals: 2,
        halfTime: {a:0, b:1},
        wdw: 'draw', htWdw: 'winB', htft: '',
        handicap: 'loss', overUnder25: 'under'
      },
      lineups: {
        teamA: {
          formation: '4-4-2', coach: '',
          starting: [
            {num:16, name:'', league:'', apps:27, goals:0, assists:0, keyPasses:0.2, rating:6.8, position:'', notes:'', club:'', league:'', apps:22, goals:1, assists:0, keyPasses:0.3, rating:6.7, position:''},
            {num:13, name:'', league:'', apps:26, goals:2, assists:1, keyPasses:0.4, rating:6.6, position:''},
            {num:22, name:'', apps:25, goals:3, assists:5, keyPasses:1.1, rating:8.1, position:''},
            {num:17, name:'', league:'', apps:23, goals:2, assists:4, keyPasses:0.8, rating:6.2, position:''1},
            {num:7, name:'', league:'', apps:28, goals:4, assists:6, keyPasses:1.3, rating:7.0, position:''},
            {num:8, name:'', club:'', league:'', apps:21, goals:1, assists:2, keyPasses:0.5, rating:6.5, position:''},
            {num:11, name:'', club:'', league:'', apps:24, goals:3, assists:3, keyPasses:0.9, rating:6.7, position:'', club:'', league:'', apps:29, goals:12, assists:4, keyPasses:0.8, rating:6.3, position:'', substituted:61, notes:''},
            {num:12, name:'', apps:22, goals:5, assists:2, keyPasses:0.6, rating:6.2, position:'', substituted:76, notes:''}
          ],
          substitutions: [
            {num:24, name:'', club:'', league:'', apps:20, goals:3, assists:2, keyPasses:0.5, rating:6.5, position:'', substitutedIn:61},
            {num:20, name:'', club:'', league:'', substitutedIn:61},
            {num:14, name:'', league:'', apps:25, goals:4, assists:3, keyPasses:0.7, rating:6.7, position:'', substitutedIn:61},
            {num:9, name:'', club:'', apps:27, goals:9, assists:2, keyPasses:0.9, rating:7.6, position:'', substitutedIn:76, notes:''},
            {num:21, name:'', club:'', league:'', substitutedIn:90}          ],          keyPlayers: ['','','']        },        teamB: {          formation: '4-4-2', coach: '',          starting: [            {num:1, name:'', league:'', apps:26, goals:0, assists:0, keyPasses:0.1, rating:6.3, position:'', notes:'', apps:23, goals:1, assists:2, keyPasses:0.6, rating:6.3, position:'', apps:25, goals:0, assists:1, keyPasses:0.4, rating:6.5, position:''},            {num:13, name:'-, club:'', league:'', apps:24, goals:2, assists:3, keyPasses:0.8, rating:7.4, position:''},            {num:20, name:'', club:', league:'', apps:22, goals:1, assists:1, keyPasses:0.5, rating:6.0, position:''4'},            {num:5, name:', club:'', league:'', apps:26, goals:2, assists:4, keyPasses:1.0, rating:7.9, position:''4'},            {num:4, name:'', club:'', league:'', apps:23, goals:3, assists:2, keyPasses:0.9, rating:7.8, position:', notes:''},            {num:18, name:'', league:'', apps:27, goals:5, assists:6, keyPasses:1.2, rating:8.1, position:'', club:'', apps:24, goals:3, assists:2, keyPasses:0.7, rating:6.9, position:', notes:''},            {num:25, name:'', league:'', apps:25, goals:8, assists:3, keyPasses:0.7, rating:7.4, position:'', substituted:62, notes:'', league:'', apps:26, goals:7, assists:2, keyPasses:0.6, rating:6.8, position:'', notes:''}
          ],
          substitutions: [
            {num:8, name:'', club:'', apps:21, goals:2, assists:1, keyPasses:0.5, rating:6.8, position:'', substitutedIn:62},
            {num:9, name:'', club:'', apps:23, goals:4, assists:2, keyPasses:0.4, rating:6.2, position:'', substitutedIn:62},
            {num:19, name:'', league:'', apps:22, goals:3, assists:1, keyPasses:0.6, rating:6.5, position:'', substitutedIn:74},
            {num:14, name:'', league:'', apps:24, goals:1, assists:2, keyPasses:0.5, rating:6.4, position:'', substitutedIn:74},
            {num:17, name:'', club:'', league:'', apps:20, goals:1, assists:3, keyPasses:0.7, rating:6.7, position:'', substitutedIn:84}
          ],
          keyPlayers: ['','','']
        }
      },
      injuries: {
        teamA: [
          {player:'', role:'', injury:'', status:'', impact:'',
          {player:'', injury:', status:''},
          {player:'', role:'', injury:''},
          {player:'', status:'', role:'', status:''},          {player:', role:'', injury:'', status:'', role:''4', status:'}
        ]
      },
      stats: {
        possession: {canada:'58%', bosnia:'42%'},
        shots: {canada:14, bosnia:8},
        shotsOnTarget: {canada:5, bosnia:3},
        corners: {canada:7, bosnia:2},
        fouls: {canada:12, bosnia:9},
        yellowCards: {canada:1, bosnia:3},
        offsides: {canada:2, bosnia:1},
        passAccuracy: {canada:'82%', bosnia:'74%'},
        tackles: {canada:18, bosnia:24},
        interceptions: {canada:11, bosnia:15},
        aerialDuels: {canada:'42%', bosnia:'58%'},
        xG: {canada:1.8, bosnia:0.9}
      },
      preMatchPrediction: {
        model: 'v4.4-anti-inflation',
        wdw: {winA:0.718, draw:0.150, winB:0.132, recommendation:''},
        confidence: {level:', agreeCount:'6/6'},
        scores: [{score:'2-0',prob:0.198},{score:'2-1',prob:0.196},{score:'1-0',prob:0.194}],
        htft: [{ht:',ft:',prob:0.368},{ht:',ft:',prob:0.132},{ht:',ft:',prob:0.118}],
        totalGoals: {expected:3.08, over25:0.627, under25:0.373},
        modelComponents: {
          poisson: {winA:0.680, draw:0.194, winB:0.126},
          dixonCole: {winA:0.678, draw:0.197, winB:0.125},
          ssm: {winA:0.684, draw:0.188, winB:0.128},
          xgboost: {winA:0.876, draw:0.050, winB:0.074},
          lightgbm: {winA:0.818, draw:0.050, winB:0.132},
          elo: {winA:0.578, draw:0.189, winB:0.233}
        }
      },
      accuracy: {
        wdw: false, score: false, htft: false,
        totalGoals: true, overUnder25: true, handicap: true,
        total: '4/6', brierScore: 1.089
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          '',          bosnia: '',
          '',
          '',
          '',
          '',          '',
          '',
          '',
          '',
          '',          file2: '',          file3: ''
      },
      postMatchAnalysis: {
        keyFactor: '',
        tacticalSummary: '',
        playerOfMatch: '',
        lessonsLearned: '',
        xGAnalysis: '',
        tacticalKey: '',
        leagueGapAnalysis: '',
        goalKeyMoments: {
          firstHalf: '',
          secondHalf: ''
        },
        playerRatingSummary: {
          highest: '',
          teamABest: '',
          teamBBest: '',
          lowest: '',          teamB: ''        },        substitutionImpact: '',        drawKeyFactors: ''USA-PAR-20260613',
      competition: ''usa', teamB: 'paraguay'',
      teamAName: '', teamBName: '',
      venue: 'usa_west', stadium: '',
      kickoff: '2026-06-13T01:00:00Z',
      referee: '',
      attendance: 68500,
      events: [
        {min:7,  type:'goal', team:'usa', player:'', assist:''},
        {min:31, type:'goal', team:'usa', player:'', assist:'', desc:''},
        {min:45, type:'goal', team:'usa', player:'', assist:''5, ,  3-0'},
        {min:73, type:'goal', team:'paraguay', player:'', desc:''},
        {min:90, type:'goal', team:'usa', player:''}
      ],
      result: {
        htScore: '3-0', ftScore: '4-1',
        goalsA: 4, goalsB: 1, totalGoals: 5,
        halfTime: {a:3, b:0},
        wdw: 'winA', htWdw: 'winA', htft: '',
        handicap: 'winA', overUnder25: 'over'
      },
      lineups: {
        teamA: {
          formation: '4-2-3-1', coach: '',
          starting: [
            {num:24, name:'', notes:'', apps:25, goals:2, assists:3, keyPasses:0.8, rating:7.3, position:''},
            {num:3, name:'', league:'', apps:24, goals:1, assists:0, keyPasses:0.3, rating:6.9, position:''},
            {num:13, name:'', club:'', league:'', apps:23, goals:2, assists:2, keyPasses:0.4, rating:7.6, position:''},
            {num:5, name:'', apps:26, goals:3, assists:4, keyPasses:1.0, rating:6.9, position:''},
            {num:4, name:'', league:'', apps:27, goals:2, assists:3, keyPasses:0.7, rating:7.0, position:'', notes:'',
            {num:17, name:'', league:'', apps:25, goals:4, assists:5, keyPasses:1.2, rating:7.3, position:'', substituted:82, notes:'2'},
            {num:2, name:'', league:'', apps:22, goals:1, assists:2, keyPasses:0.6, rating:6.7, position:'', substituted:72, notes:''},
            {num:8, name:'', league:'', apps:26, goals:5, assists:4, keyPasses:1.3, rating:7.4, position:', notes:''},
            {num:10, name:'', club:'', league:'', apps:27, goals:8, assists:6, keyPasses:1.5, rating:7.6, position:'', substituted:46, notes:'',
            {num:20, name:'', league:'', apps:28, goals:14, assists:5, keyPasses:1.1, rating:9.0, position:'', substituted:72, notes:''}
          ],
          substitutions: [
            {num:14, name:'', club:'', league:'', apps:23, goals:3, assists:2, keyPasses:0.8, rating:6.8, position:'', substitutedIn:46, notes:'',
            {num:21, name:'', club:'', league:'', apps:24, goals:6, assists:3, keyPasses:0.9, rating:6.5, position:'', substitutedIn:72, notes:'',
            {num:9, name:'', club:'', league:'', apps:25, goals:7, assists:2, keyPasses:0.7, rating:6.3, position:'', substitutedIn:72, notes:''},
            {num:7, name:'', club:'', league:'', apps:26, goals:4, assists:7, keyPasses:1.4, rating:7.8, position:'', substitutedIn:82, notes:''}
          ],
          keyPlayers: ['','','','']
        },
        teamB: {
          formation: '4-4-2', coach: '',
          starting: [
            {num:12, name:'', club:'', apps:27, goals:0, assists:0, keyPasses:0.0, rating:6.1, position:'', notes:''},
            {num:10, name:'', club:'', apps:25, goals:6, assists:4, keyPasses:1.2, rating:6.5, position:', substituted:79, notes:''},
            {num:16, name:'', league:'', apps:23, goals:1, assists:1, keyPasses:0.4, rating:5.5, position:''},
            {num:14, name:'', league:'', apps:24, goals:2, assists:2, keyPasses:0.5, rating:6.9, position:', notes:''},
            {num:8, name:'', league:'', substituted:80, notes:'',
            {num:6, name:'', league:'', apps:24, goals:0, assists:1, keyPasses:0.3, rating:5.3, position:', notes:''},
            {num:3, name:'', league:'', apps:26, goals:2, assists:2, keyPasses:0.6, rating:6.2, position:''},
            {num:15, name:'', apps:27, goals:1, assists:3, keyPasses:0.5, rating:5.9, position:'', club:'', apps:24, goals:5, assists:3, keyPasses:1.0, rating:7.3, position:'', notes:'',
            {num:9, name:'', apps:25, goals:6, assists:2, keyPasses:0.6, rating:6.4, position:'', substituted:62, notes:''}
          ],
          substitutions: [
            {num:11, name:'', club:'', apps:23, goals:4, assists:3, keyPasses:0.7, rating:7.3, position:'', substitutedIn:46, notes:''},
            {num:18, name:'', league:'', substitutedIn:62, yellowCard:true, notes:'},
            {num:2, name:'', club:'', apps:22, goals:0, assists:1, keyPasses:0.3, rating:6.3, position:'', substitutedIn:79, notes:'',
            {num:7, name:'', club:'', apps:23, goals:3, assists:2, keyPasses:0.4, rating:6.6, position:'', substitutedIn:79, notes:'',
            {num:17, name:'', club:'', league:'', apps:21, goals:2, assists:1, keyPasses:0.5, rating:6.4, position:'', substitutedIn:80, notes:''}
          ],
          keyPlayers: ['','','']
        }
      },
      injuries: {
        teamA: [
          {player:'', injury:'', status:''},
          {player:'', injury:'', status:''},
          {player:'', role:'', injury:'', status:''}
        ],
        teamB: [
          {player:'', injury:'', status:'},
          {player:'', injury:'', status:''},
          {player:'', injury:''}
        ]
      },
      stats: {
        possession: {usa:'58%', paraguay:'42%'},
        shots: {usa:18, paraguay:5},
        shotsOnTarget: {usa:8, paraguay:2},
        corners: {usa:6, paraguay:2},
        fouls: {usa:10, paraguay:14},
        yellowCards: {usa:1, paraguay:2},
        offsides: {usa:2, paraguay:1},
        passAccuracy: {usa:'84%', paraguay:'71%'},
        tackles: {usa:14, paraguay:22},
        interceptions: {usa:9, paraguay:12},
        xG: {usa:2.8, paraguay:0.6}
      },
      preMatchPrediction: {
        model: 'v4.3-6model-ensemble',
        wdw: {winA:0.485, draw:0.241, winB:0.274, recommendation:''},
        confidence: {level:', agreeCount:'4/6'},
        scores: [{score:'1-0',prob:0.242},{score:'1-1',prob:0.240},{score:'2-1',prob:0.190}],
        htft: [{ht:',ft:',prob:0.232},{ht:',ft:',prob:0.179},{ht:',ft:',prob:0.157}],
        totalGoals: {expected:2.37, over25:0.469, under25:0.531},
        modelComponents: {
          poisson: {winA:0.485, draw:0.241, winB:0.274},
          dixonCole: {winA:0.485, draw:0.241, winB:0.274},
          ssm: {winA:0.485, draw:0.241, winB:0.274},
          xgboost: {winA:0.485, draw:0.241, winB:0.274},
          lightgbm: {winA:0.485, draw:0.241, winB:0.274},
          elo: {winA:0.485, draw:0.241, winB:0.274}
        }
      },
      accuracy: {
        wdw: true, score: false, htft: true,
        totalGoals: false, overUnder25: true, handicap: true,
        total: '4/6', brierScore: 0.133
      },
      review: {
        summary: '',
        keyMoments: [
          '',
          '',
          '',
          '',
          ''
        ],
        tacticalAnalysis: {
          usa: '',
          paraguay: ''attack: attack=2.04, xG=2.84, ',          'defence: defence=0.62, 4, ',          ': SoFi8500+, Elo+65, ',          ':  ',          ' , "", ',          ': 648.5%/24.1%/27.4%), 
        ],
        modelImprovement: [
          '',
          '',
          '',
          '',
          '',
          '':  ,  Elo+231744;  , ,  Elo-291804'      }    },    {      matchId: 'FRA-SEN-20260617',      competition: ''france', teamB: 'senegal'',      venue: 'metlife', neutral: true,      kickoff: '2026-06-17T03:00:00Z',      referee: 'Alireza Faghani',      venueConditions: {        stadium: 'MetLife Stadium',        city: 'East Rutherford, New Jersey',        grassType: 'natural',        grassCondition: 'good',        temperature: 31,        humidity: 80,        weather: 'sunny',        altitude: 9,        windSpeed: 3,        windDirection: 'northwest',        impactAssessment: {          humidityImpact: 'high',          expectedEffect: 'both_affected',          sunsetGlare: true,          heatFactor: 'extreme'        }      },      oddsTrajectory: {        european: [          { time: '2026-06-08 10:02', winA: 1.38, draw: 3.90, winB: 6.75 },          { time: '2026-06-11 21:40', winA: 1.38, draw: 3.90, winB: 6.75 },          { time: '2026-06-12 12:41', winA: 1.37, draw: 3.95, winB: 6.85 },          { time: '2026-06-13 15:07', winA: 1.36, draw: 4.00, winB: 6.95 },          { time: '2026-06-14 19:27', winA: 1.35, draw: 4.02, winB: 7.15 },          { time: '2026-06-15 11:25', winA: 1.33, draw: 4.15, winB: 7.30 },          { time: '2026-06-15 15:49', winA: 1.31, draw: 4.23, winB: 7.70 },          { time: '2026-06-16 10:11', winA: 1.33, draw: 4.15, winB: 7.30 },          { time: '2026-06-16 18:59', winA: 1.32, draw: 4.20, winB: 7.45 }        ],        handicap: [          { time: '2026-06-08 10:02', win: 2.35, draw: 3.20, loss: 2.56 },          { time: '2026-06-11 21:40', win: 2.35, draw: 3.20, loss: 2.56 },          { time: '2026-06-12 12:41', win: 2.29, draw: 3.20, loss: 2.63 },          { time: '2026-06-13 15:07', win: 2.26, draw: 3.22, loss: 2.66 },          { time: '2026-06-14 19:27', win: 2.23, draw: 3.22, loss: 2.70 },          { time: '2026-06-15 11:25', win: 2.20, draw: 3.25, loss: 2.72 },          { time: '2026-06-15 12:45', win: 2.15, draw: 3.37, loss: 2.72 },          { time: '2026-06-15 15:50', win: 2.12, draw: 3.37, loss: 2.77 },          { time: '2026-06-16 10:12', win: 2.15, draw: 3.37, loss: 2.72 },          { time: '2026-06-16 14:37', win: 2.12, draw: 3.45, loss: 2.72 },          { time: '2026-06-16 18:59', win: 2.07, draw: 3.45, loss: 2.81 }        ],        totalGoals: [          { time: '2026-06-08 10:02', goals0: 10.50, goals1: 4.40, goals2: 3.25, goals3: 3.60, goals4: 5.80, goals5: 11.00, goals6: 20.00, goals7plus: 30.00 },          { time: '2026-06-16 11:34', goals0: 11.50, goals1: 4.70, goals2: 3.30, goals3: 3.60, goals4: 5.60, goals5: 10.00, goals6: 18.00, goals7plus: 26.00 },          { time: '2026-06-16 14:11', goals0: 12.00, goals1: 4.80, goals2: 3.30, goals3: 3.60, goals4: 5.50, goals5: 9.50, goals6: 18.00, goals7plus: 26.00 },          { time: '2026-06-16 15:22', goals0: 12.50, goals1: 5.05, goals2: 3.30, goals3: 3.50, goals4: 5.50, goals5: 9.50, goals6: 17.00, goals7plus: 24.00 },          { time: '2026-06-16 17:21', goals0: 13.00, goals1: 5.20, goals2: 3.35, goals3: 3.50, goals4: 5.25, goals5: 9.25, goals6: 17.00, goals7plus: 23.00 },          { time: '2026-06-16 18:02', goals0: 13.50, goals1: 5.30, goals2: 3.45, goals3: 3.50, goals4: 5.15, goals5: 9.00, goals6: 16.00, goals7plus: 21.00 },          { time: '2026-06-16 19:37', goals0: 13.50, goals1: 5.30, goals2: 3.55, goals3: 3.40, goals4: 5.15, goals5: 9.00, goals6: 16.00, goals7plus: 21.00 },          { time: '2026-06-16 19:57', goals0: 14.00, goals1: 5.35, goals2: 3.60, goals3: 3.40, goals4: 5.00, goals5: 8.75, goals6: 16.00, goals7plus: 21.00 },          { time: '2026-06-16 20:13', goals0: 15.00, goals1: 5.70, goals2: 3.85, goals3: 3.30, goals4: 4.70, goals5: 8.30, goals6: 15.00, goals7plus: 20.00 }        ],        halfTimeFullTime: [          { time: '2026-06-08 10:02', HH: 1.93, HD: 21.00, HA: 50.00, DH: 3.80, DD: 5.90, DA: 14.00, AH: 25.00, AD: 21.00, AA: 13.00 },          { time: '2026-06-15 11:26', HH: 1.89, HD: 21.00, HA: 50.00, DH: 3.80, DD: 6.15, DA: 15.00, AH: 25.00, AD: 21.00, AA: 13.00 },          { time: '2026-06-16 12:04', HH: 1.89, HD: 19.00, HA: 45.00, DH: 3.80, DD: 6.35, DA: 16.00, AH: 23.00, AD: 19.00, AA: 14.00 },          { time: '2026-06-16 14:37', HH: 1.87, HD: 19.00, HA: 45.00, DH: 3.80, DD: 6.60, DA: 16.00, AH: 23.00, AD: 19.00, AA: 14.00 },          { time: '2026-06-16 17:02', HH: 1.87, HD: 17.50, HA: 40.00, DH: 3.90, DD: 6.75, DA: 17.00, AH: 22.00, AD: 17.50, AA: 14.00 }        ],        correctScore: [          { time: '2026-06-08 10:05', score10: 5.50, score20: 5.90, score21: 6.80, score30: 8.50, score31: 11.00, score32: 30.00, score40: 17.00, score41: 26.00, score42: 75.00, score50: 50.00, score51: 75.00, score52: 175.00, scoreWinOther: 45.00, score00: 10.50, score11: 6.80, score22: 20.00, score33: 125.00, scoreDrawOther: 500.00, score01: 15.00, score02: 40.00, score12: 17.00, score03: 150.00, score13: 80.00, score23: 90.00, score04: 500.00, score14: 400.00, score24: 400.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 400.00 },          { time: '2026-06-15 12:13', score10: 5.50, score20: 5.90, score21: 6.50, score30: 8.50, score31: 10.00, score32: 30.00, score40: 18.00, score41: 23.00, score42: 75.00, score50: 50.00, score51: 75.00, score52: 175.00, scoreWinOther: 45.00, score00: 10.50, score11: 6.80, score22: 20.00, score33: 125.00, scoreDrawOther: 500.00, score01: 16.00, score02: 42.00, score12: 20.00, score03: 165.00, score13: 85.00, score23: 95.00, score04: 500.00, score14: 400.00, score24: 400.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 400.00 },          { time: '2026-06-15 13:00', score10: 5.65, score20: 5.90, score21: 6.50, score30: 8.50, score31: 10.00, score32: 30.00, score40: 18.00, score41: 23.00, score42: 60.00, score50: 45.00, score51: 65.00, score52: 150.00, scoreWinOther: 40.00, score00: 10.50, score11: 6.80, score22: 20.00, score33: 125.00, scoreDrawOther: 500.00, score01: 17.00, score02: 45.00, score12: 21.00, score03: 165.00, score13: 85.00, score23: 95.00, score04: 500.00, score14: 400.00, score24: 400.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 400.00 },          { time: '2026-06-15 14:57', score10: 5.65, score20: 5.90, score21: 6.50, score30: 8.50, score31: 10.00, score32: 30.00, score40: 18.00, score41: 23.00, score42: 60.00, score50: 45.00, score51: 65.00, score52: 125.00, scoreWinOther: 38.00, score00: 10.50, score11: 6.85, score22: 20.00, score33: 100.00, scoreDrawOther: 450.00, score01: 17.50, score02: 47.00, score12: 21.00, score03: 165.00, score13: 85.00, score23: 95.00, score04: 500.00, score14: 400.00, score24: 400.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 400.00 },          { time: '2026-06-16 11:34', score10: 5.80, score20: 5.90, score21: 6.20, score30: 8.50, score31: 9.50, score32: 27.00, score40: 18.00, score41: 23.00, score42: 60.00, score50: 45.00, score51: 65.00, score52: 125.00, scoreWinOther: 38.00, score00: 11.50, score11: 6.85, score22: 20.00, score33: 100.00, scoreDrawOther: 450.00, score01: 19.00, score02: 50.00, score12: 21.00, score03: 165.00, score13: 85.00, score23: 95.00, score04: 500.00, score14: 400.00, score24: 400.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 400.00 },          { time: '2026-06-16 14:19', score10: 5.90, score20: 5.90, score21: 6.10, score30: 8.50, score31: 9.50, score32: 27.00, score40: 19.00, score41: 23.00, score42: 55.00, score50: 45.00, score51: 60.00, score52: 125.00, scoreWinOther: 37.00, score00: 12.00, score11: 7.05, score22: 19.00, score33: 70.00, scoreDrawOther: 400.00, score01: 19.00, score02: 50.00, score12: 21.00, score03: 165.00, score13: 85.00, score23: 80.00, score04: 500.00, score14: 400.00, score24: 350.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 400.00 },          { time: '2026-06-16 15:22', score10: 6.00, score20: 5.90, score21: 6.10, score30: 9.00, score31: 9.50, score32: 27.00, score40: 20.00, score41: 22.00, score42: 50.00, score50: 45.00, score51: 55.00, score52: 110.00, scoreWinOther: 33.00, score00: 12.50, score11: 7.10, score22: 18.00, score33: 60.00, scoreDrawOther: 350.00, score01: 19.00, score02: 50.00, score12: 21.00, score03: 165.00, score13: 85.00, score23: 70.00, score04: 500.00, score14: 400.00, score24: 300.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 350.00 },          { time: '2026-06-16 17:22', score10: 6.25, score20: 6.00, score21: 6.10, score30: 9.25, score31: 9.25, score32: 24.00, score40: 20.00, score41: 22.00, score42: 43.00, score50: 45.00, score51: 45.00, score52: 90.00, scoreWinOther: 32.00, score00: 13.00, score11: 7.50, score22: 17.00, score33: 55.00, scoreDrawOther: 300.00, score01: 19.00, score02: 50.00, score12: 21.00, score03: 165.00, score13: 85.00, score23: 70.00, score04: 500.00, score14: 400.00, score24: 300.00, score05: 900.00, score15: 800.00, score25: 800.00, scoreLoseOther: 350.00 }        ]      },      lineups: {        teamA: {          formation: '4-2-3-1',          avgRating: 7.08,          avgAge: 26.8,          players: [            { number: 16, position: '', name: ', club: '', league: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 6.3, notes: '' },            { number: 5, position: '', club: '', league: '', apps: 27, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.6, notes: '' },            { number: 4, position: ', name: '', league: '', apps: 29, goals: 2, assists: 2, keyPasses: 0.4, rating: 7.9, notes: '' },            { number: 17, position: '', name: '', apps: 26, goals: 2, assists: 1, keyPasses: 0.3, rating: 6.5, notes: '' },            { number: 19, position: '', league: '', apps: 28, goals: 4, assists: 6, keyPasses: 0.9, rating: 6.4, notes: '', name: '', club: ', league: '', apps: 30, goals: 4, assists: 5, keyPasses: 0.9, rating: 7.2, notes: '' },            { number: 14, position: '', name: '', league: '', apps: 27, goals: 6, assists: 7, keyPasses: 1.2, rating: 7.4, goal: true, notes: '' },            { number: 11, position: '', name: ', club: '', apps: 25, goals: 7, assists: 9, keyPasses: 1.4, rating: 7.7, notes: ' },            { number: 7, position: '', name: '', league: '', apps: 28, goals: 5, assists: 11, keyPasses: 1.3, rating: 7.0, minuteSubstituted: 80, notes: '' },            { number: 20, position: '', name: '', club: '', league: '', apps: 24, goals: 3, assists: 4, keyPasses: 0.8, rating: 6.7, minuteSubstituted: 87, notes: '' },            { number: 10, position: '', name: ', club: '', apps: 32, goals: 24, assists: 12, keyPasses: 1.6, rating: 8.1, captain: true, goal: true, notes: ' }
          ],
          substitutes: [],
          keyPlayers: ['','','',''],
          coach: 'Didier Deschamps',
          marketValue: 908
        },
        teamB: {
          formation: '4-3-3',
          avgRating: 6.38,
          avgAge: 28.8,
          players: [
            { number: 16, position: '', name: '', club: '', league: '' },
            { number: 25, position: ''-, club: '', league: '', apps: 24, goals: 1, assists: 2, keyPasses: 0.4, rating: 6.1, notes: '' },
            { number: 19, position: '', club: '', league: '', apps: 26, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.5, notes: '' },
            { number: 3, position: '', name: '', club: '' },
            { number: 15, position: '', apps: 25, goals: 1, assists: 3, keyPasses: 0.5, rating: 6.9, notes: '' },
            { number: 26, position: '', name: '', club: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.6, rating: 6.7, minuteSubstituted: 83, notes: ' },
            { number: 5, position: '', name: '', club: '', apps: 28, goals: 3, assists: 4, keyPasses: 0.8, rating: 6.7, minuteSubstituted: 88, notes: '',
            { number: 8, position: '', name: '', league: '', apps: 27, goals: 4, assists: 3, keyPasses: 0.7, rating: 6.1, minuteSubstituted: 76, notes: '76' },
            { number: 10, position: '', club: '', name: '', apps: 27, goals: 8, assists: 4, keyPasses: 0.7, rating: 5.9, minuteSubstituted: 83, notes: '' },
            { number: 18, position: '', club: '', league: '', apps: 26, goals: 9, assists: 5, keyPasses: 0.9, rating: 6.2, minuteSubstituted: 75, notes: '' }          ],          substitutes: [],          keyPlayers: ['','',''],          coach: '',          marketValue: 224        }      },      postMatchAnalysis: {        keyFactor: '',        tacticalSummary: '',        playerOfMatch: '',        lessonsLearned: '',        xGAnalysis: '',        tacticalKey: '',        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '',          secondHalf: ''        },        playerRatingSummary: {          highest: '',          teamABest: '',          teamBBest: '',          lowest: ''        },        teamTactics: {          teamA: '',          teamB: ''        },        substitutionImpact: '',        winKeyFactors: '',
        weatherImpact: '',
        psychologicalFactor: '',
        fanSupport: '',
        refereeHistory: ''
      }
    },
    {
      matchId: 'ARG-ALG-20260617',
      competition: ''argentina', teamB: 'algeria'',
      venue: 'kansas', neutral: true,
      kickoff: '2026-06-17T09:00:00Z',
      referee: 'Szymon Marciniak',
      venueConditions: {
        stadium: 'Kansas City Stadium',
        city: 'Kansas City',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 26,
        humidity: 75,
        weather: 'partly_cloudy',
        altitude: 243,
        windSpeed: 8,
        windDirection: 'southwest',
        impactAssessment: {
          humidityImpact: 'high',
          expectedEffect: 'both_affected',
          stormRisk: true,
          windImpact: 'moderate'
        }
      },
      oddsTrajectory: {
        european: [
          { time: '2026-06-08 10:02', winA: 1.28, draw: 4.25, winB: 8.90 },
          { time: '2026-06-12 12:42', winA: 1.27, draw: 4.35, winB: 8.90 },
          { time: '2026-06-14 13:24', winA: 1.26, draw: 4.40, winB: 9.20 },
          { time: '2026-06-16 18:29', winA: 1.27, draw: 4.33, winB: 9.00 },
          { time: '2026-06-16 20:36', winA: 1.29, draw: 4.20, winB: 8.60 }
        ],
        handicap: [
          { time: '2026-06-08 10:02', win: 2.07, draw: 3.25, loss: 2.95 },
          { time: '2026-06-12 12:42', win: 2.00, draw: 3.32, loss: 3.05 },
          { time: '2026-06-14 13:24', win: 1.95, draw: 3.35, loss: 3.15 },
          { time: '2026-06-15 12:13', win: 1.90, draw: 3.50, loss: 3.15 },
          { time: '2026-06-16 18:29', win: 1.93, draw: 3.48, loss: 3.09 },
          { time: '2026-06-16 19:05', win: 1.96, draw: 3.38, loss: 3.09 },
          { time: '2026-06-16 19:44', win: 2.00, draw: 3.27, loss: 3.09 },
          { time: '2026-06-16 19:59', win: 2.04, draw: 3.17, loss: 3.09 },
          { time: '2026-06-16 20:36', win: 2.09, draw: 3.17, loss: 2.98 }
        ],
        totalGoals: [
          { time: '2026-06-08 10:02', goals0: 11.00, goals1: 4.50, goals2: 3.40, goals3: 3.60, goals4: 5.50, goals5: 10.00, goals6: 19.00, goals7plus: 29.00 },
          { time: '2026-06-16 10:17', goals0: 11.00, goals1: 4.65, goals2: 3.40, goals3: 3.60, goals4: 5.50, goals5: 9.80, goals6: 18.00, goals7plus: 27.00 },
          { time: '2026-06-16 14:23', goals0: 12.00, goals1: 4.65, goals2: 3.40, goals3: 3.50, goals4: 5.50, goals5: 9.80, goals6: 18.00, goals7plus: 27.00 },
          { time: '2026-06-16 17:26', goals0: 12.00, goals1: 5.00, goals2: 3.40, goals3: 3.40, goals4: 5.30, goals5: 9.80, goals6: 18.00, goals7plus: 27.00 }
        ],
        halfTimeFullTime: [
          { time: '2026-06-08 10:02', HH: 1.90, HD: 21.00, HA: 60.00, DH: 3.45, DD: 6.00, DA: 17.00, AH: 30.00, AD: 21.00, AA: 15.00 },
          { time: '2026-06-14 13:25', HH: 1.80, HD: 21.00, HA: 60.00, DH: 3.55, DD: 6.35, DA: 18.00, AH: 30.00, AD: 21.00, AA: 17.00 },
          { time: '2026-06-16 11:50', HH: 1.80, HD: 20.00, HA: 60.00, DH: 3.60, DD: 6.40, DA: 18.50, AH: 27.00, AD: 20.00, AA: 17.50 },
          { time: '2026-06-16 14:23', HH: 1.84, HD: 19.00, HA: 50.00, DH: 3.70, DD: 6.40, DA: 18.50, AH: 25.00, AD: 19.00, AA: 15.50 },
          { time: '2026-06-16 18:30', HH: 1.87, HD: 19.00, HA: 45.00, DH: 3.75, DD: 6.25, DA: 18.00, AH: 25.00, AD: 19.00, AA: 14.50 }
        ],
        correctScore: [
          { time: '2026-06-08 10:02', score10: 5.20, score20: 5.20, score21: 7.00, score30: 8.00, score31: 11.00, score32: 30.00, score40: 15.00, score41: 22.00, score42: 75.00, score50: 35.00, score51: 60.00, score52: 175.00, scoreWinOther: 40.00, score00: 11.00, score11: 7.50, score22: 24.00, score33: 150.00, scoreDrawOther: 800.00, score01: 16.00, score02: 50.00, score12: 25.00, score03: 200.00, score13: 100.00, score23: 100.00, score04: 700.00, score14: 500.00, score24: 500.00, score05: 1000.00, score15: 1000.00, score25: 1000.00, scoreLoseOther: 600.00 },
          { time: '2026-06-14 13:27', score10: 5.50, score20: 4.75, score21: 7.00, score30: 7.25, score31: 10.50, score32: 35.00, score40: 15.00, score41: 22.00, score42: 80.00, score50: 35.00, score51: 60.00, score52: 175.00, scoreWinOther: 40.00, score00: 11.00, score11: 7.75, score22: 24.00, score33: 150.00, scoreDrawOther: 800.00, score01: 18.00, score02: 55.00, score12: 27.00, score03: 200.00, score13: 120.00, score23: 120.00, score04: 700.00, score14: 500.00, score24: 500.00, score05: 1000.00, score15: 1000.00, score25: 1000.00, scoreLoseOther: 600.00 },
          { time: '2026-06-15 12:14', score10: 5.60, score20: 4.75, score21: 7.00, score30: 7.25, score31: 10.50, score32: 35.00, score40: 15.00, score41: 22.00, score42: 70.00, score50: 35.00, score51: 55.00, score52: 150.00, scoreWinOther: 38.00, score00: 11.00, score11: 7.75, score22: 24.00, score33: 150.00, scoreDrawOther: 800.00, score01: 19.00, score02: 55.00, score12: 27.00, score03: 200.00, score13: 120.00, score23: 120.00, score04: 700.00, score14: 500.00, score24: 500.00, score05: 1000.00, score15: 1000.00, score25: 1000.00, scoreLoseOther: 600.00 },
          { time: '2026-06-16 11:56', score10: 5.90, score20: 4.75, score21: 6.75, score30: 7.25, score31: 10.00, score32: 32.00, score40: 15.00, score41: 22.00, score42: 70.00, score50: 35.00, score51: 50.00, score52: 150.00, scoreWinOther: 36.00, score00: 11.00, score11: 8.00, score22: 24.00, score33: 120.00, scoreDrawOther: 800.00, score01: 20.00, score02: 55.00, score12: 27.00, score03: 250.00, score13: 120.00, score23: 120.00, score04: 700.00, score14: 500.00, score24: 500.00, score05: 1000.00, score15: 1000.00, score25: 1000.00, scoreLoseOther: 600.00 },
          { time: '2026-06-16 14:23', score10: 5.90, score20: 4.75, score21: 6.50, score30: 7.25, score31: 10.00, score32: 32.00, score40: 15.00, score41: 22.00, score42: 70.00, score50: 35.00, score51: 50.00, score52: 150.00, scoreWinOther: 36.00, score00: 12.00, score11: 8.00, score22: 24.00, score33: 120.00, scoreDrawOther: 800.00, score01: 20.00, score02: 55.00, score12: 27.00, score03: 250.00, score13: 120.00, score23: 120.00, score04: 700.00, score14: 500.00, score24: 500.00, score05: 1000.00, score15: 1000.00, score25: 1000.00, scoreLoseOther: 600.00 },
          { time: '2026-06-16 16:14', score10: 5.90, score20: 4.75, score21: 6.50, score30: 7.75, score31: 10.00, score32: 32.00, score40: 17.00, score41: 23.00, score42: 50.00, score50: 35.00, score51: 50.00, score52: 100.00, scoreWinOther: 36.00, score00: 12.00, score11: 8.25, score22: 22.00, score33: 75.00, scoreDrawOther: 400.00, score01: 20.00, score02: 55.00, score12: 27.00, score03: 250.00, score13: 120.00, score23: 80.00, score04: 700.00, score14: 500.00, score24: 500.00, score05: 1000.00, score15: 1000.00, score25: 1000.00, scoreLoseOther: 400.00 },
          { time: '2026-06-16 18:33', score10: 6.25, score20: 5.15, score21: 6.25, score30: 8.00, score31: 9.75, score32: 30.00, score40: 18.00, score41: 23.00, score42: 50.00, score50: 35.00, score51: 50.00, score52: 100.00, scoreWinOther: 33.00, score00: 12.00, score11: 8.25, score22: 20.00, score33: 70.00, scoreDrawOther: 400.00, score01: 20.00, score02: 50.00, score12: 23.00, score03: 200.00, score13: 100.00, score23: 70.00, score04: 700.00, score14: 500.00, score24: 400.00, score05: 1000.00, score15: 1000.00, score25: 1000.00, scoreLoseOther: 400.00 },
          { time: '2026-06-16 20:51', score10: 6.25, score20: 5.15, score21: 6.25, score30: 8.50, score31: 9.75, score32: 30.00, score40: 18.50, score41: 23.00, score42: 50.00, score50: 40.00, score51: 50.00, score52: 100.00, scoreWinOther: 33.00, score00: 12.00, score11: 8.25, score22: 20.00, score33: 70.00, scoreDrawOther: 400.00, score01: 19.00, score02: 50.00, score12: 20.00, score03: 150.00, score13: 85.00, score23: 70.00, score04: 700.00, score14: 500.00, score24: 300.00, score05: 1000.00, score15: 1000.00, score25: 800.00, scoreLoseOther: 400.00 }
        ]
      },
      lineups: {
        teamA: {
          formation: '4-3-3',
          players: [
            { number: 23, position: 'GK', name: '', club: 'Aston Villa', league: 'Premier League', age: 31, rating: 84, keyPlayer: true },
            { number: 4, position: 'RB', name: 'Nahuel Molina', club: '', league: 'La Liga', age: 28, rating: 78, keyPlayer: false, injury: 'muscle' },
            { number: 13, position: 'CB', name: 'Cristian Romero', club: 'Tottenham', league: 'Premier League', age: 27, rating: 81, keyPlayer: true },
            { number: 19, position: 'CB', name: '', club: 'Benfica', league: 'Primeira Liga', age: 39, rating: 79, keyPlayer: true },
            { number: 25, position: 'LB', name: 'Facundo Medina', club: 'Lens', league: 'Ligue 1', age: 27, rating: 76, keyPlayer: false },
            { number: 7, position: 'CM', name: 'Rodrigo De Paul', club: '', league: 'La Liga', age: 32, rating: 80, keyPlayer: true },
            { number: 20, position: 'CM', name: 'Alexis Mac Allister', club: 'Liverpool', league: 'Premier League', age: 27, rating: 81, keyPlayer: true },
            { number: 24, position: 'CM', name: '', club: 'Chelsea', league: 'Premier League', age: 24, rating: 82, keyPlayer: true },
            { number: 16, position: 'RW', name: 'Thiago Almada', club: '', league: 'La Liga', age: 24, rating: 77, keyPlayer: false },
            { number: 22, position: 'ST', name: '', club: 'Inter Milan', league: 'Serie A', age: 28, rating: 82, keyPlayer: true },
            { number: 10, position: 'LW', name: 'Lionel Messi', club: 'Inter Miami', league: 'MLS', age: 38, rating: 88, keyPlayer: true, captain: true }
          ],
          keyPlayers: ['Lionel Messi', '', '', ''],
          coach: 'Lionel Scaloni',
          avgRating: 80.5,
          avgAge: 28.5,
          marketValue: 880
        },
        teamB: {
          formation: '4-2-3-1',
          players: [
            { number: 23, position: 'GK', name: 'Luca Zidane', club: 'Granada', league: 'La Liga 2', age: 25, rating: 72, keyPlayer: false },
            { number: 3, position: 'RB', name: 'Yousef Belaili', club: 'Unconfirmed', league: '-', age: 32, rating: 70, keyPlayer: false },
            { number: 2, position: 'CB', name: '', club: 'Lille', league: 'Ligue 1', age: 32, rating: 78, keyPlayer: true },
            { number: 21, position: 'CB', name: 'Samir Chergui', club: '', league: 'Ligue 1', age: 26, rating: 74, keyPlayer: false },
            { number: 15, position: 'LB', name: '', club: 'Manchester City', league: 'Premier League', age: 23, rating: 76, keyPlayer: true },
            { number: 19, position: 'CM', name: 'Nabil Bentaleb', club: 'Lille', league: 'Ligue 1', age: 30, rating: 75, keyPlayer: true },
            { number: 6, position: 'CM', name: 'Hicham Boudaoui', club: 'Nice', league: 'Ligue 1', age: 27, rating: 73, keyPlayer: false },
            { number: 7, position: 'RW', name: 'Riyad Mahrez', club: 'Al-Ahli', league: 'Saudi Pro League', age: 34, rating: 79, keyPlayer: true, captain: true },
            { number: 8, position: 'AM', name: 'Ibrahim Maza', club: 'Bayer Leverkusen', league: 'Bundesliga', age: 22, rating: 76, keyPlayer: true },
            { number: 18, position: 'LW', name: 'Mohamed Amoura', club: 'Wolfsburg', league: 'Bundesliga', age: 24, rating: 75, keyPlayer: true },
            { number: 9, position: 'ST', name: 'Amine Gouiri', club: 'Marseille', league: 'Ligue 1', age: 25, rating: 77, keyPlayer: true }
          ],
          keyPlayers: ['Riyad Mahrez', 'Amine Gouiri', 'Mohamed Amoura', ''],
          coach: 'Djamel Belmadi',
          avgRating: 75.2,
          avgAge: 27.5,
          marketValue: 250
        }
      },
      preMatchAnalysis: {
        tacticalMatchup: '',
        weatherImpact: '',
        psychologicalFactor: '',
        injuryImpact: '',
        keyBattle: ''CAN-BIH-20260613',      competition: ''canada', teamB: 'bosnia'',      venue: 'toronto', neutral: false,      kickoff: '2026-06-13T03:00:00Z',      referee: 'F. Tello',      finalScore: { teamA: 1, teamB: 1 },      halfTimeScore: { teamA: 0, teamB: 1 },      venueConditions: {        stadium: 'BMO Field',        city: 'Toronto',        grassType: 'hybrid_natural',        grassCondition: 'good',        temperature: 23,        humidity: 72,        weather: 'cloudy_rain',        altitude: 75,        windSpeed: 3,        windDirection: 'southwest',        impactAssessment: {          humidityImpact: 'moderate',          expectedEffect: 'both_affected',          stormRisk: false,          windImpact: 'low'        }      },      matchStats: {        possessionA: 61.1, possessionB: 38.9,        shotsA: 13, shotsB: 8,        shotsOnTargetA: 4, shotsOnTargetB: 3,        xG: { teamA: 1.25, teamB: 0.98 },        cornersA: 9, cornersB: 4,        passesA: 412, passesB: 279,        passAccuracyA: 77, passAccuracyB: 67,        foulsA: 10, foulsB: 20,        yellowCardsA: 2, yellowCardsB: 3,        redCardsA: 0, redCardsB: 0,        offsidesA: 1, offsidesB: 0,        crossesA: 30, crossesB: 8,        successfulCrossesA: 5, successfulCrossesB: 5      },      lineups: {        teamA: {          formation: '4-4-2',          players: [            { number: 16, position: 'GK', name: '', club: 'Montpellier', league: 'Ligue 1', apps: 27, goals: 0, assists: 0, keyPasses: 0.2, rating: 6.8 },            { number: 2, position: 'RB', name: 'Alistair Johnston', club: 'Columbus Crew', league: 'MLS', apps: 24, goals: 2, assists: 3, keyPasses: 0.7, rating: 6.9 },            { number: 4, position: 'CB', name: 'Kyle Duncan', club: 'Lille', league: 'Ligue 1', apps: 22, goals: 1, assists: 0, keyPasses: 0.3, rating: 6.7 },            { number: 13, position: 'CB', name: 'Doneil Henry', club: 'Cardiff City', league: 'Championship', apps: 26, goals: 2, assists: 1, keyPasses: 0.4, rating: 6.6 },            { number: 22, position: 'LB', name: 'Sam Adekugbe', club: 'Brugge', league: 'Belgian Pro League', apps: 25, goals: 3, assists: 5, keyPasses: 1.1, rating: 8.1 },            { number: 17, position: 'RM', name: 'Tajon Buchanan', club: 'Brugge', league: 'Belgian Pro League', apps: 23, goals: 2, assists: 4, keyPasses: 0.8, rating: 6.2 },            { number: 7, position: 'CM', name: '', club: 'Porto', league: 'Primeira Liga', apps: 28, goals: 4, assists: 6, keyPasses: 1.3, rating: 7.0 },            { number: 8, position: 'CM', name: '', club: 'Rennes', league: 'Ligue 1', apps: 21, goals: 1, assists: 2, keyPasses: 0.5, rating: 6.5 },            { number: 11, position: 'LM', name: 'Liam Millar', club: 'Celtic', league: 'Scottish Premiership', apps: 24, goals: 3, assists: 3, keyPasses: 0.9, rating: 6.7 },            { number: 10, position: 'ST', name: 'Jonathan David', club: 'Lille', league: 'Ligue 1', apps: 29, goals: 12, assists: 4, keyPasses: 0.8, rating: 6.3 },            { number: 12, position: 'ST', name: '', club: 'St Johnstone', league: 'Scottish Premiership', apps: 22, goals: 5, assists: 2, keyPasses: 0.6, rating: 6.2 }          ],          substitutes: [            { number: 24, name: 'Promise David', club: '', league: 'Belgian Pro League', apps: 20, goals: 3, assists: 2, keyPasses: 0.5, rating: 6.5, minuteOn: 61 },            { number: 20, name: 'Ali Ahmed', club: 'CF Montreal', league: 'MLS', apps: 23, goals: 1, assists: 3, keyPasses: 0.6, rating: 6.6, minuteOn: 61 },            { number: 14, name: 'Shaffelburg', club: 'Rapid Wien', league: 'Austrian Bundesliga', apps: 25, goals: 4, assists: 3, keyPasses: 0.7, rating: 6.7, minuteOn: 61 },            { number: 9, name: 'Cyle Larin', club: 'Mallorca', league: 'La Liga', apps: 27, goals: 9, assists: 2, keyPasses: 0.9, rating: 7.6, minuteOn: 76, goalsScored: 1 },            { number: 21, name: 'Jonathan Osorio', club: 'Toronto FC', league: 'MLS', apps: 22, goals: 2, assists: 1, keyPasses: 0.4, rating: 6.0, minuteOn: 90 }          ],          avgRating: 6.75,          avgAge: 26.8,          marketValue: 180        },        teamB: {          formation: '4-4-2',          players: [            { number: 1, position: 'GK', name: '', club: 'RB Leipzig II', league: '3. Liga', apps: 26, goals: 0, assists: 0, keyPasses: 0.1, rating: 6.3 },            { number: 15, position: 'RB', name: '', club: 'Sturm Graz', league: 'Austrian Bundesliga', apps: 23, goals: 1, assists: 2, keyPasses: 0.6, rating: 6.3 },            { number: 6, position: 'CB', name: '', club: 'Dinamo Zagreb', league: 'Prva HNL', apps: 25, goals: 0, assists: 1, keyPasses: 0.4, rating: 6.5 },            { number: 13, position: 'CB', name: '', club: 'Hoffenheim', league: 'Bundesliga', apps: 24, goals: 2, assists: 3, keyPasses: 0.8, rating: 7.4 },            { number: 20, position: 'LB', name: 'Bajrami', club: 'Almeria', league: 'La Liga', apps: 22, goals: 1, assists: 1, keyPasses: 0.5, rating: 6.0 },            { number: 5, position: 'RM', name: '', club: 'Marseille', league: 'Ligue 1', apps: 26, goals: 2, assists: 4, keyPasses: 1.0, rating: 7.9 },            { number: 4, position: 'CM', name: 'Amar Muharemo', club: 'Udinese', league: 'Serie A', apps: 23, goals: 3, assists: 2, keyPasses: 0.9, rating: 7.8 },            { number: 18, position: 'CM', name: '', club: 'Ferencvaros', league: '', apps: 27, goals: 5, assists: 6, keyPasses: 1.2, rating: 8.1 },            { number: 7, position: 'LM', name: '', club: 'Zurich', league: 'Swiss Super League', apps: 24, goals: 3, assists: 2, keyPasses: 0.7, rating: 6.9 },            { number: 25, position: 'ST', name: '', club: 'Atalanta', league: 'Serie A', apps: 25, goals: 8, assists: 3, keyPasses: 0.7, rating: 7.4, goalsScored: 1 },            { number: 10, position: 'ST', name: 'Smail Prevljak', club: 'Augsburg', league: 'Bundesliga', apps: 26, goals: 7, assists: 2, keyPasses: 0.6, rating: 6.8 }          ],          substitutes: [            { number: 8, name: '', club: 'NK Lokomotiva', league: 'Prva HNL', apps: 21, goals: 2, assists: 1, keyPasses: 0.5, rating: 6.8, minuteOn: 62 },            { number: 9, name: 'Bazdar', club: 'Partizan', league: 'SuperLiga', apps: 23, goals: 4, assists: 2, keyPasses: 0.4, rating: 6.2, minuteOn: 62 },            { number: 19, name: '', club: 'Rijeka', league: 'Prva HNL', apps: 22, goals: 3, assists: 1, keyPasses: 0.6, rating: 6.5, minuteOn: 74 },            { number: 14, name: '', club: 'Gent', league: 'Belgian Pro League', apps: 24, goals: 1, assists: 2, keyPasses: 0.5, rating: 6.4, minuteOn: 74 },            { number: 17, name: '', club: 'Osijek', league: 'Prva HNL', apps: 20, goals: 1, assists: 3, keyPasses: 0.7, rating: 6.7, minuteOn: 84 }          ],          avgRating: 7.02,          avgAge: 27.2,          marketValue: 220        }      },      matchEvents: [        { minute: 11, type: 'yellow_card', team: 'CAN', player: 'Johnston' },        { minute: 17, type: 'shot_save', team: 'CAN', player: 'David' },        { minute: 21, type: 'goal', team: 'BIH', player: '', assist: '', score: '0-1' },        { minute: 45, type: 'yellow_card', team: 'BIH', player: 'Prevljak' },        { minute: 45, type: 'yellow_card', team: 'BIH', player: '' },        { minute: 53, type: 'goal_line_clearance', team: 'BIH', player: '' },        { minute: 61, type: 'substitution', team: 'CAN', out: 'Buchanan', in: 'Shaffelburg' },        { minute: 61, type: 'substitution', team: 'CAN', out: 'Millar', in: 'Ahmed' },        { minute: 61, type: 'substitution', team: 'CAN', out: 'David', in: 'Promise David' },        { minute: 66, type: 'goal_line_clearance', team: 'BIH', player: 'Prevljak' },        { minute: 76, type: 'substitution', team: 'CAN', out: 'Ugbo', in: 'Larin' },        { minute: 78, type: 'goal', team: 'CAN', player: 'Larin', score: '1-1' },        { minute: 84, type: 'injury', team: 'BIH', player: '' },        { minute: 84, type: 'substitution', team: 'BIH', out: '', in: '' },        { minute: 90, type: 'substitution', team: 'CAN', out: '', in: 'Osorio' }      ],      injuryReport: {        teamA: [          { name: 'Alphonso Davies', injury: 'hamstring', status: 'out' },          { name: 'Moise Bombito', injury: 'tibia', status: 'out' },          { name: '', injury: 'fitness', status: 'doubtful' },          { name: 'Marcelo Flores', injury: 'ACL', status: 'out' }        ],        teamB: [          { name: '', injury: 'fitness', status: 'bench' },          { name: 'Smail Prevljak', injury: 'ankle', status: 'bench' }        ]      },      recentMatches: {        teamA: [          { date: '2025-06-30', opponent: 'Guatemala', result: '1-1', halfTime: '1-0', venue: 'neutral', competition: 'Gold Cup', cornersA: 9, cornersB: 4 },          { date: '2025-06-25', opponent: 'El Salvador', result: '2-0', halfTime: '0-0', venue: 'home', competition: 'Gold Cup', cornersA: 12, cornersB: 1 },          { date: '2025-06-22', opponent: 'Curacao', result: '1-1', halfTime: '1-0', venue: 'neutral', competition: 'Gold Cup', cornersA: 3, cornersB: 2 },          { date: '2025-06-18', opponent: 'Honduras', result: '6-0', halfTime: '2-0', venue: 'home', competition: 'Gold Cup', cornersA: 3, cornersB: 3 },          { date: '2025-03-24', opponent: 'USA', result: '2-1', halfTime: '1-1', venue: 'home', competition: 'CONCACAF Nations League', cornersA: 3, cornersB: 3 },          { date: '2025-03-21', opponent: 'Mexico', result: '0-2', halfTime: '0-1', venue: 'neutral', competition: 'CONCACAF Nations League', cornersA: 7, cornersB: 2 },          { date: '2024-11-20', opponent: 'Suriname', result: '3-0', halfTime: '2-0', venue: 'home', competition: 'CONCACAF Nations League', cornersA: 5, cornersB: 2 },          { date: '2024-07-14', opponent: 'Uruguay', result: '2-2', halfTime: '1-1', venue: 'neutral', competition: 'Copa America', cornersA: 7, cornersB: 6 },          { date: '2024-06-30', opponent: 'Chile', result: '0-0', halfTime: '0-0', venue: 'neutral', competition: 'Copa America', cornersA: 8, cornersB: 4 },          { date: '2024-03-24', opponent: 'Trinidad and Tobago', result: '2-0', halfTime: '0-0', venue: 'neutral', competition: 'Copa America', cornersA: 14, cornersB: 0 }        ],        teamB: [          { date: '2026-04-01', opponent: 'Italy', result: '1-1', halfTime: '0-1', venue: 'neutral', competition: 'Euro Qualifiers', cornersA: 7, cornersB: 2 },          { date: '2026-03-27', opponent: 'Wales', result: '1-1', halfTime: '0-0', venue: 'away', competition: 'Euro Qualifiers', cornersA: 3, cornersB: 5 },          { date: '2025-11-19', opponent: 'Austria', result: '1-1', halfTime: '1-0', venue: 'away', competition: 'Euro Qualifiers', cornersA: 2, cornersB: 4 },          { date: '2025-11-16', opponent: 'Romania', result: '3-1', halfTime: '1-0', venue: 'home', competition: 'Euro Qualifiers', cornersA: 4, cornersB: 1 },          { date: '2025-10-10', opponent: 'Cyprus', result: '2-2', halfTime: '2-1', venue: 'away', competition: 'Euro Qualifiers', cornersA: 5, cornersB: 3 },          { date: '2025-09-10', opponent: 'Austria', result: '1-2', halfTime: '0-0', venue: 'home', competition: 'Euro Qualifiers', cornersA: 3, cornersB: 2 },          { date: '2025-09-07', opponent: 'San Marino', result: '6-0', halfTime: '1-0', venue: 'away', competition: 'Euro Qualifiers', cornersA: 6, cornersB: 0 },          { date: '2025-06-07', opponent: 'San Marino', result: '1-0', halfTime: '0-0', venue: 'home', competition: 'Euro Qualifiers', cornersA: 5, cornersB: 2 },          { date: '2025-03-25', opponent: 'Cyprus', result: '2-1', halfTime: '1-1', venue: 'home', competition: 'Euro Qualifiers', cornersA: 7, cornersB: 7 },          { date: '2025-03-22', opponent: 'Romania', result: '1-0', halfTime: '1-0', venue: 'away', competition: 'Euro Qualifiers', cornersA: 1, cornersB: 5 }        ]      },      oddsTrajectory: {        european: [          { time: '2026-06-08 10:02', winA: 1.59, draw: 3.38, winB: 4.90 },          { time: '2026-06-11 17:27', winA: 1.62, draw: 3.32, winB: 4.75 },          { time: '2026-06-12 13:29', winA: 1.61, draw: 3.36, winB: 4.75 },          { time: '2026-06-12 18:30', winA: 1.61, draw: 3.36, winB: 4.75 },          { time: '2026-06-12 21:52', winA: 1.62, draw: 3.32, winB: 4.75 }        ],        handicap: [          { time: '2026-06-08 10:02', win: 2.95, draw: 3.30, loss: 2.05 },          { time: '2026-06-11 17:27', win: 3.04, draw: 3.30, loss: 2.01 },          { time: '2026-06-12 10:01', win: 3.11, draw: 3.20, loss: 2.02 },          { time: '2026-06-12 13:29', win: 3.20, draw: 3.11, loss: 2.02 },          { time: '2026-06-12 14:24', win: 3.32, draw: 3.07, loss: 1.99 },          { time: '2026-06-12 17:07', win: 3.41, draw: 3.00, loss: 1.99 },          { time: '2026-06-12 18:28', win: 3.50, draw: 2.93, loss: 1.99 },          { time: '2026-06-12 20:40', win: 3.55, draw: 2.90, loss: 1.99 },          { time: '2026-06-12 21:56', win: 3.58, draw: 2.90, loss: 1.98 }        ],        totalGoals: [          { time: '2026-06-08 10:02', goals0: 9.50, goals1: 4.30, goals2: 3.10, goals3: 3.60, goals4: 6.20, goals5: 12.50, goals6: 22.00, goals7plus: 35.00 },          { time: '2026-06-12 14:26', goals0: 9.50, goals1: 4.30, goals2: 3.00, goals3: 3.60, goals4: 6.20, goals5: 13.00, goals6: 24.00, goals7plus: 40.00 },          { time: '2026-06-12 16:28', goals0: 9.50, goals1: 4.05, goals2: 2.95, goals3: 3.60, goals4: 6.50, goals5: 14.00, goals6: 26.00, goals7plus: 45.00 },          { time: '2026-06-12 17:17', goals0: 9.50, goals1: 4.05, goals2: 2.85, goals3: 3.50, goals4: 7.00, goals5: 15.00, goals6: 28.00, goals7plus: 48.00 },          { time: '2026-06-12 19:49', goals0: 9.50, goals1: 4.05, goals2: 2.75, goals3: 3.50, goals4: 7.40, goals5: 15.50, goals6: 30.00, goals7plus: 50.00 }        ],        halfTimeFullTime: [          { time: '2026-06-08 10:02', HH: 2.45, HD: 17.00, HA: 40.00, DH: 4.25, DD: 4.80, DA: 10.00, AH: 25.00, AD: 17.00, AA: 8.40 },          { time: '2026-06-12 11:14', HH: 2.51, HD: 16.00, HA: 36.00, DH: 4.25, DD: 4.80, DA: 10.00, AH: 25.00, AD: 16.00, AA: 8.40 },          { time: '2026-06-12 14:06', HH: 2.53, HD: 15.50, HA: 36.00, DH: 4.25, DD: 4.80, DA: 10.00, AH: 25.00, AD: 15.50, AA: 8.40 },          { time: '2026-06-12 16:25', HH: 2.55, HD: 15.00, HA: 36.00, DH: 4.25, DD: 4.85, DA: 10.00, AH: 25.00, AD: 15.00, AA: 8.40 },          { time: '2026-06-12 17:24', HH: 2.60, HD: 13.50, HA: 36.00, DH: 4.25, DD: 4.85, DA: 10.00, AH: 25.00, AD: 13.50, AA: 8.90 },          { time: '2026-06-12 17:55', HH: 2.65, HD: 13.50, HA: 34.00, DH: 4.15, DD: 4.85, DA: 10.00, AH: 25.00, AD: 13.50, AA: 8.90 }        ],        correctScore: [          { time: '2026-06-08 10:02', score10: 5.70, score20: 6.50, score21: 7.00, score30: 11.00, score31: 13.00, score32: 30.00, score40: 27.00, score41: 30.00, score42: 75.00, score50: 75.00, score51: 100.00, score52: 200.00, scoreWinOther: 75.00, score00: 9.50, score11: 6.20, score22: 16.00, score33: 90.00, scoreDrawOther: 500.00, score01: 10.50, score02: 26.00, score12: 14.00, score03: 90.00, score13: 60.00, score23: 60.00, score04: 300.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 300.00 },          { time: '2026-06-11 12:53', score10: 5.70, score20: 6.50, score21: 6.70, score30: 12.00, score31: 14.00, score32: 30.00, score40: 29.00, score41: 32.00, score42: 75.00, score50: 75.00, score51: 100.00, score52: 200.00, scoreWinOther: 75.00, score00: 9.50, score11: 5.80, score22: 16.00, score33: 90.00, scoreDrawOther: 500.00, score01: 10.50, score02: 26.00, score12: 14.00, score03: 90.00, score13: 60.00, score23: 60.00, score04: 300.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 300.00 },          { time: '2026-06-11 19:21', score10: 5.70, score20: 6.50, score21: 6.60, score30: 13.00, score31: 14.00, score32: 32.00, score40: 35.00, score41: 32.00, score42: 75.00, score50: 75.00, score51: 100.00, score52: 200.00, scoreWinOther: 75.00, score00: 9.50, score11: 5.40, score22: 16.00, score33: 90.00, scoreDrawOther: 500.00, score01: 10.50, score02: 26.00, score12: 14.00, score03: 90.00, score13: 60.00, score23: 60.00, score04: 300.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 300.00 },          { time: '2026-06-12 08:33', score10: 5.70, score20: 6.50, score21: 6.25, score30: 14.00, score31: 14.00, score32: 33.00, score40: 38.00, score41: 35.00, score42: 80.00, score50: 85.00, score51: 110.00, score52: 230.00, scoreWinOther: 80.00, score00: 9.50, score11: 5.00, score22: 16.00, score33: 90.00, scoreDrawOther: 500.00, score01: 11.00, score02: 26.00, score12: 14.50, score03: 90.00, score13: 60.00, score23: 60.00, score04: 300.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 300.00 },          { time: '2026-06-12 11:46', score10: 5.40, score20: 6.50, score21: 6.00, score30: 14.00, score31: 14.00, score32: 33.00, score40: 38.00, score41: 37.00, score42: 90.00, score50: 90.00, score51: 125.00, score52: 230.00, scoreWinOther: 85.00, score00: 9.50, score11: 5.00, score22: 16.00, score33: 90.00, scoreDrawOther: 500.00, score01: 11.50, score02: 28.00, score12: 15.00, score03: 90.00, score13: 60.00, score23: 65.00, score04: 350.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 350.00 },          { time: '2026-06-12 13:30', score10: 5.40, score20: 6.50, score21: 5.65, score30: 14.00, score31: 14.00, score32: 33.00, score40: 38.00, score41: 37.00, score42: 90.00, score50: 90.00, score51: 125.00, score52: 230.00, scoreWinOther: 85.00, score00: 9.50, score11: 5.00, score22: 16.00, score33: 80.00, scoreDrawOther: 500.00, score01: 11.50, score02: 31.00, score12: 17.00, score03: 95.00, score13: 60.00, score23: 65.00, score04: 350.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 350.00 },          { time: '2026-06-12 15:28', score10: 5.40, score20: 6.50, score21: 5.65, score30: 14.00, score31: 14.00, score32: 33.00, score40: 38.00, score41: 37.00, score42: 90.00, score50: 90.00, score51: 125.00, score52: 230.00, scoreWinOther: 85.00, score00: 9.50, score11: 5.10, score22: 16.00, score33: 80.00, scoreDrawOther: 500.00, score01: 11.50, score02: 31.00, score12: 16.00, score03: 95.00, score13: 60.00, score23: 65.00, score04: 350.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 350.00 },          { time: '2026-06-12 15:53', score10: 5.30, score20: 6.50, score21: 5.65, score30: 14.00, score31: 14.00, score32: 33.00, score40: 38.00, score41: 40.00, score42: 90.00, score50: 100.00, score51: 125.00, score52: 230.00, scoreWinOther: 90.00, score00: 9.50, score11: 5.30, score22: 16.50, score33: 80.00, scoreDrawOther: 500.00, score01: 11.00, score02: 31.00, score12: 15.00, score03: 95.00, score13: 55.00, score23: 65.00, score04: 350.00, score14: 300.00, score24: 300.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 350.00 },          { time: '2026-06-12 16:52', score10: 5.30, score20: 6.50, score21: 5.65, score30: 14.50, score31: 14.00, score32: 33.00, score40: 40.00, score41: 45.00, score42: 90.00, score50: 120.00, score51: 125.00, score52: 250.00, scoreWinOther: 100.00, score00: 9.50, score11: 5.30, score22: 16.50, score33: 80.00, scoreDrawOther: 500.00, score01: 10.50, score02: 28.00, score12: 14.00, score03: 95.00, score13: 55.00, score23: 70.00, score04: 400.00, score14: 300.00, score24: 350.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 400.00 },          { time: '2026-06-12 17:22', score10: 5.10, score20: 6.50, score21: 5.30, score30: 15.00, score31: 15.00, score32: 35.00, score40: 40.00, score41: 46.00, score42: 90.00, score50: 120.00, score51: 125.00, score52: 250.00, scoreWinOther: 100.00, score00: 9.50, score11: 5.30, score22: 17.50, score33: 80.00, scoreDrawOther: 500.00, score01: 11.00, score02: 28.00, score12: 14.00, score03: 95.00, score13: 55.00, score23: 70.00, score04: 400.00, score14: 300.00, score24: 350.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 400.00 },          { time: '2026-06-12 18:47', score10: 5.10, score20: 6.50, score21: 5.30, score30: 15.00, score31: 15.00, score32: 40.00, score40: 40.00, score41: 46.00, score42: 95.00, score50: 125.00, score51: 150.00, score52: 250.00, scoreWinOther: 110.00, score00: 9.50, score11: 5.10, score22: 18.50, score33: 90.00, scoreDrawOther: 500.00, score01: 11.00, score02: 28.00, score12: 14.00, score03: 95.00, score13: 55.00, score23: 70.00, score04: 400.00, score14: 300.00, score24: 350.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 450.00 },          { time: '2026-06-12 21:19', score10: 5.10, score20: 6.60, score21: 5.30, score30: 15.00, score31: 15.00, score32: 40.00, score40: 40.00, score41: 46.00, score42: 95.00, score50: 125.00, score51: 150.00, score52: 250.00, scoreWinOther: 110.00, score00: 9.50, score11: 4.75, score22: 20.00, score33: 90.00, scoreDrawOther: 500.00, score01: 11.00, score02: 28.00, score12: 15.00, score03: 95.00, score13: 55.00, score23: 70.00, score04: 400.00, score14: 300.00, score24: 350.00, score05: 600.00, score15: 500.00, score25: 600.00, scoreLoseOther: 450.00 }        ]      },      preMatchAnalysis: {        motivation: {          teamA: ''
        },
        externalFactors: {
          temperature: '',
          travel: '',
          homeAdvantage: '',
          altitude: ''
        },
        psychological: {
          teamA: ''        },        finalResults: {          matchResult: 'draw',          matchOdds: 3.32,          handicapResult: 'lose',          handicapOdds: 1.98,          correctScore: '1-1',          correctScoreOdds: 4.75,          totalGoals: 2,          totalGoalsOdds: 2.75,          halfTimeFullTime: 'AD',          halfTimeFullTimeOdds: 13.50        }      },      postMatchAnalysis: {        keyFactor: '',        tacticalSummary: '',        playerOfMatch: '',        lessonsLearned: '',        xGAnalysis: '',        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '',          secondHalf: ''        },        playerRatingSummary: {          highest: '',          teamABest: '',          teamBBest: '',          lowest: '',
          teamB: ''
        },
        substitutionImpact: '',
        drawKeyFactors: ''
      }
    },
    {
      matchId: 'USA-PAR-20260613',
      competition: ''usa', teamB: 'paraguay'',
      venue: 'los_angeles', neutral: false,
      kickoff: '2026-06-13T09:00:00Z',
      referee: 'Danny Makkelie',
      finalScore: { teamA: 4, teamB: 1 },
      halfTimeScore: { teamA: 3, teamB: 0 },
      venueConditions: {
        stadium: 'SoFi Stadium',
        city: 'Los Angeles',
        grassType: 'natural',
        grassCondition: 'good',
        temperature: 22,
        humidity: 70,
        weather: 'sunny',
        altitude: 87,
        windSpeed: 16,
        windDirection: 'southwest',
        hasRoof: true,
        impactAssessment: {
          humidityImpact: 'low',
          expectedEffect: 'both_affected',
          stormRisk: false,
          windImpact: 'low'
        }
      },
      matchStats: {
        possessionA: 65, possessionB: 35,
        shotsA: 16, shotsB: 9,
        shotsOnTargetA: 6, shotsOnTargetB: 1,
        xG: { teamA: 1.88, teamB: 0.6 },
        xGOT: { teamA: 1.8, teamB: 0.48 },
        xGA: { teamA: 0.48, teamB: 1.8 },
        xA: { teamA: 1.22, teamB: 0.27 },
        cornersA: 3, cornersB: 1,
        freeKicksA: 7, freeKicksB: 3,
        passesA: 520, passesB: 234,
        passAccuracyA: 86, passAccuracyB: 74,
        throughPassesA: 129, throughPassesB: 53,
        dangerousAttacksA: 92, dangerousAttacksB: 22,
        totalAttacksA: 158, totalAttacksB: 58,
        foulsA: 13, foulsB: 17,
        yellowCardsA: 1, yellowCardsB: 5,
        redCardsA: 0, redCardsB: 0,
        offsidesA: 2, offsidesB: 1,
        savesA: 0, savesB: 3,
        blockedShotsA: 4, blockedShotsB: 5,
        throwInsA: 23, throwInsB: 17
      },
      lineups: {
        teamA: {
          formation: '4-2-3-1',
          players: [
            { number: 24, position: 'GK', name: 'Matt Turner', club: 'St. Louis City', league: 'MLS', apps: 26, goals: 0, assists: 0, keyPasses: 0.1, rating: 6.0 },
            { number: 16, position: 'RB', name: 'Alex Freeman', club: 'Crystal Palace', league: 'Premier League', apps: 25, goals: 2, assists: 3, keyPasses: 0.8, rating: 7.3 },
            { number: 3, position: 'CB', name: 'Chris Richards', club: 'Crystal Palace', league: 'Premier League', apps: 24, goals: 1, assists: 0, keyPasses: 0.3, rating: 6.9 },
            { number: 13, position: 'CB', name: 'Tim Ream', club: 'West Ham', league: 'Premier League', apps: 23, goals: 2, assists: 2, keyPasses: 0.4, rating: 7.6 },
            { number: 5, position: 'LB', name: 'Antonee Robinson', club: 'Fulham', league: 'Premier League', apps: 26, goals: 3, assists: 4, keyPasses: 1.0, rating: 6.9 },
            { number: 4, position: 'CM', name: 'Tyler Adams', club: 'RB Leipzig', league: 'Bundesliga', apps: 27, goals: 2, assists: 3, keyPasses: 0.7, rating: 7.0 },
            { number: 17, position: 'CM', name: 'Malik Tillman', club: 'PSV', league: 'Eredivisie', apps: 25, goals: 4, assists: 5, keyPasses: 1.2, rating: 7.3, xA: 0.38 },
            { number: 2, position: 'RW', name: '', club: 'AC Milan', league: 'Serie A', apps: 22, goals: 1, assists: 2, keyPasses: 0.6, rating: 6.7 },
            { number: 8, position: 'AM', name: 'Weston McKennie', club: 'Juventus', league: 'Serie A', apps: 26, goals: 5, assists: 4, keyPasses: 1.3, rating: 7.4 },
            { number: 10, position: 'LW', name: 'Christian Pulisic', club: 'AC Milan', league: 'Serie A', apps: 27, goals: 8, assists: 6, keyPasses: 1.5, rating: 7.6, xA: 0.41 },
            { number: 20, position: 'ST', name: 'Folarin Balogun', club: 'Monaco', league: 'Ligue 1', apps: 28, goals: 14, assists: 5, keyPasses: 1.1, rating: 9.0, goalsScored: 2 }
          ],
          substitutes: [
            { number: 14, name: 'Giovanni Bellotti', club: 'Marseille', league: 'Ligue 1', apps: 23, goals: 3, assists: 2, keyPasses: 0.8, rating: 6.8, minuteOn: 46 },
            { number: 21, name: 'Timothy Weah', club: 'Lille', league: 'Ligue 1', apps: 24, goals: 6, assists: 3, keyPasses: 0.9, rating: 6.5, minuteOn: 72 },
            { number: 9, name: 'Ricardo Pepi', club: 'Augsburg', league: 'Bundesliga', apps: 25, goals: 7, assists: 2, keyPasses: 0.7, rating: 6.3, minuteOn: 72 },
            { number: 7, name: 'Giovanni Reyna', club: 'Borussia Dortmund', league: 'Bundesliga', apps: 26, goals: 4, assists: 7, keyPasses: 1.4, rating: 7.8, minuteOn: 82, goalsScored: 1, xA: 0.43 }
          ],
          avgRating: 7.15,
          avgAge: 26.7,
          marketValue: 239,
          worldRanking: 17
        },
        teamB: {
          formation: '4-4-2',
          players: [
            { number: 12, position: 'GK', name: 'Orlando Hill', club: 'Libertad', league: 'Paraguayan Primera', apps: 27, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.1 },
            { number: 10, position: 'RB', name: 'Miguel Almiron', club: 'Newcastle United', league: 'Premier League', apps: 25, goals: 6, assists: 4, keyPasses: 1.2, rating: 6.5 },
            { number: 16, position: 'CB', name: 'Damian Bobadilla', club: 'Monterrey', league: 'Liga MX', apps: 23, goals: 1, assists: 1, keyPasses: 0.4, rating: 5.5 },
            { number: 14, position: 'CB', name: 'Marcelo Cuevas', club: 'River Plate', league: 'Argentine Primera', apps: 24, goals: 2, assists: 2, keyPasses: 0.5, rating: 6.9 },
            { number: 8, position: 'LB', name: 'Diego Gomez', club: 'Portland Timbers', league: 'MLS', apps: 22, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.0 },
            { number: 6, position: 'RM', name: 'Junior Alonso', club: 'Atletico Paranaense', league: 'Brasileirao', apps: 24, goals: 0, assists: 1, keyPasses: 0.3, rating: 5.3 },
            { number: 3, position: 'CM', name: 'Marcos Acuna', club: 'Getafe', league: 'La Liga', apps: 26, goals: 2, assists: 2, keyPasses: 0.6, rating: 6.2 },
            { number: 15, position: 'CM', name: 'Gustavo Gomez', club: 'Palmeiras', league: 'Brasileirao', apps: 27, goals: 1, assists: 3, keyPasses: 0.5, rating: 5.9 },
            { number: 4, position: 'LM', name: 'Juan Caceres', club: 'Olimpia', league: 'Paraguayan Primera', apps: 23, goals: 1, assists: 2, keyPasses: 0.4, rating: 6.3 },
            { number: 19, position: 'ST', name: 'Julio Enciso', club: 'Brighton', league: 'Premier League', apps: 24, goals: 5, assists: 3, keyPasses: 1.0, rating: 7.3, goalsScored: 1, xA: 0.27 },
            { number: 9, position: 'ST', name: 'Antonio Sanabria', club: 'Genoa', league: 'Serie A', apps: 25, goals: 6, assists: 2, keyPasses: 0.6, rating: 6.4 }
          ],
          substitutes: [
            { number: 11, name: 'Mauricio', club: 'Fluminense', league: 'Brasileirao', apps: 23, goals: 4, assists: 3, keyPasses: 0.7, rating: 7.3, minuteOn: 46, goalsScored: 1 },
            { number: 18, name: 'Alexis Arce', club: 'Cerro Porteno', league: 'Paraguayan Primera', apps: 24, goals: 5, assists: 1, keyPasses: 0.5, rating: 6.2, minuteOn: 62 },
            { number: 2, name: 'Gustavo Benitez', club: 'Libertad', league: 'Paraguayan Primera', apps: 22, goals: 0, assists: 1, keyPasses: 0.3, rating: 6.3, minuteOn: 79 },
            { number: 7, name: 'Ramon Sosa', club: 'Guarani', league: 'Paraguayan Primera', apps: 23, goals: 3, assists: 2, keyPasses: 0.4, rating: 6.6, minuteOn: 79 },
            { number: 17, name: 'Cacu', club: 'San Lorenzo', league: 'Argentine Primera', apps: 21, goals: 2, assists: 1, keyPasses: 0.5, rating: 6.4, minuteOn: 80 }
          ],
          avgRating: 6.25,
          avgAge: 28.0,
          marketValue: 98.3,
          worldRanking: 40
        }
      },
      matchEvents: [
        { minute: 7, type: 'own_goal', team: 'PAR', player: 'Bobadilla', score: '1-0' },
        { minute: 31, type: 'goal', team: 'USA', player: 'Balogun', assist: 'Pulisic', score: '2-0' },
        { minute: 45, type: 'goal', team: 'USA', player: 'Balogun', assist: 'Tillman', score: '3-0' },
        { minute: 52, type: 'var_decision', description: '' },
        { minute: 73, type: 'goal', team: 'PAR', player: 'Mauricio', assist: 'Enciso', score: '3-1' },
        { minute: 90, type: 'goal', team: 'USA', player: 'Reyna', assist: 'Freeman', score: '4-1' }
      ],
      injuryReport: {
        teamA: [],
        teamB: [
          { name: 'Julio Enciso', injury: 'muscle', status: 'doubtful' }
        ]
      },
      recentMatches: {
        teamA: [
          { date: '2025-07-07', opponent: 'Mexico', result: '1-2', halfTime: '1-1', venue: 'neutral', competition: 'Gold Cup', cornersA: 0, cornersB: 12 },
          { date: '2025-07-03', opponent: 'Guatemala', result: '2-1', halfTime: '2-0', venue: 'home', competition: 'Gold Cup', cornersA: 2, cornersB: 6 },
          { date: '2025-06-30', opponent: 'Costa Rica', result: '2-2', halfTime: '1-1', venue: 'neutral', competition: 'Gold Cup', cornersA: 9, cornersB: 2 },
          { date: '2025-06-23', opponent: 'Haiti', result: '2-1', halfTime: '1-1', venue: 'home', competition: 'Gold Cup', cornersA: 6, cornersB: 5 },
          { date: '2025-06-20', opponent: 'Saudi Arabia', result: '1-0', halfTime: '0-0', venue: 'neutral', competition: 'Gold Cup', cornersA: 4, cornersB: 2 },
          { date: '2025-06-16', opponent: 'Trinidad and Tobago', result: '5-0', halfTime: '3-0', venue: 'home', competition: 'Gold Cup', cornersA: 10, cornersB: 4 },
          { date: '2025-03-24', opponent: 'Canada', result: '1-2', halfTime: '1-1', venue: 'away', competition: 'CONCACAF Nations League', cornersA: 3, cornersB: 3 },
          { date: '2025-03-21', opponent: 'Panama', result: '0-1', halfTime: '0-0', venue: 'home', competition: 'CONCACAF Nations League', cornersA: 9, cornersB: 3 },
          { date: '2022-12-03', opponent: 'Netherlands', result: '1-3', halfTime: '0-2', venue: 'neutral', , cornersA: 5, cornersB: 4 },
          { date: '2022-11-30', opponent: 'Iran', result: '1-0', halfTime: '1-0', venue: 'neutral', , cornersA: 5, cornersB: 1 }
        ],
        teamB: [
          { date: '2025-10-10', opponent: 'Japan', result: '2-2', halfTime: '1-1', venue: 'away', competition: 'Kirin Cup', cornersA: 1, cornersB: 6 },
          { date: '2025-09-10', opponent: 'Peru', result: '1-0', halfTime: '0-0', venue: 'away', competition: 'South American Qualifiers', cornersA: 7, cornersB: 6 },
          { date: '2025-09-05', opponent: 'Ecuador', result: '0-0', halfTime: '0-0', venue: 'home', competition: 'South American Qualifiers', cornersA: 6, cornersB: 1 },
          { date: '2025-06-11', opponent: 'Brazil', result: '0-1', halfTime: '0-1', venue: 'away', competition: 'South American Qualifiers', cornersA: 3, cornersB: 11 },
          { date: '2025-06-06', opponent: 'Uruguay', result: '2-0', halfTime: '1-0', venue: 'home', competition: 'South American Qualifiers', cornersA: 5, cornersB: 7 },
          { date: '2025-03-26', opponent: 'Colombia', result: '2-2', halfTime: '1-2', venue: 'away', competition: 'South American Qualifiers', cornersA: 4, cornersB: 5 },
          { date: '2025-03-21', opponent: 'Chile', result: '1-0', halfTime: '0-0', venue: 'home', competition: 'South American Qualifiers', cornersA: 6, cornersB: 4 },
          { date: '2024-11-20', opponent: 'Bolivia', result: '2-2', halfTime: '1-0', venue: 'away', competition: 'South American Qualifiers', cornersA: 1, cornersB: 4 },
          { date: '2024-11-15', opponent: 'Argentina', result: '2-1', halfTime: '1-1', venue: 'home', competition: 'South American Qualifiers', cornersA: 6, cornersB: 2 },
          { date: '2024-10-16', opponent: 'Venezuela', result: '2-1', halfTime: '0-1', venue: 'home', competition: 'South American Qualifiers', cornersA: 5, cornersB: 1 }
        ]
      },
      oddsTrajectory: {
        european: [
          { time: '2026-06-08 10:02', winA: 1.87, draw: 3.02, winB: 3.80 },
          { time: '2026-06-09 19:33', winA: 1.83, draw: 3.05, winB: 3.92 },
          { time: '2026-06-11 10:26', winA: 1.80, draw: 3.10, winB: 3.98 },
          { time: '2026-06-11 17:13', winA: 1.78, draw: 3.10, winB: 4.08 },
          { time: '2026-06-12 09:53', winA: 1.70, draw: 3.25, winB: 4.28 },
          { time: '2026-06-12 11:01', winA: 1.72, draw: 3.23, winB: 4.20 },
          { time: '2026-06-12 11:09', winA: 1.71, draw: 3.26, winB: 4.20 },
          { time: '2026-06-12 12:20', winA: 1.73, draw: 3.22, winB: 4.15 },
          { time: '2026-06-12 12:33', winA: 1.74, draw: 3.25, winB: 4.05 },
          { time: '2026-06-12 13:04', winA: 1.74, draw: 3.30, winB: 3.97 },
          { time: '2026-06-12 13:08', winA: 1.77, draw: 3.25, winB: 3.89 },
          { time: '2026-06-12 14:42', winA: 1.74, draw: 3.30, winB: 3.98 },
          { time: '2026-06-12 16:46', winA: 1.75, draw: 3.30, winB: 3.92 },
          { time: '2026-06-12 17:12', winA: 1.76, draw: 3.30, winB: 3.87 },
          { time: '2026-06-12 17:28', winA: 1.79, draw: 3.25, winB: 3.80 }
        ],
        handicap: [
          { time: '2026-06-08 10:02', win: 3.95, draw: 3.40, loss: 1.72 },
          { time: '2026-06-09 19:34', win: 3.80, draw: 3.35, loss: 1.76 },
          { time: '2026-06-11 10:26', win: 3.70, draw: 3.30, loss: 1.80 },
          { time: '2026-06-11 17:13', win: 3.70, draw: 3.23, loss: 1.82 },
          { time: '2026-06-12 09:54', win: 3.57, draw: 3.18, loss: 1.87 },
          { time: '2026-06-12 12:15', win: 3.46, draw: 3.18, loss: 1.90 },
          { time: '2026-06-12 12:21', win: 3.52, draw: 3.22, loss: 1.87 },
          { time: '2026-06-12 12:34', win: 3.54, draw: 3.23, loss: 1.86 },
          { time: '2026-06-12 13:08', win: 3.60, draw: 3.28, loss: 1.83 },
          { time: '2026-06-12 14:42', win: 3.60, draw: 3.22, loss: 1.85 },
          { time: '2026-06-12 15:46', win: 3.68, draw: 3.15, loss: 1.85 },
          { time: '2026-06-12 16:33', win: 3.78, draw: 3.08, loss: 1.85 },
          { time: '2026-06-12 16:46', win: 3.92, draw: 3.08, loss: 1.82 },
          { time: '2026-06-12 17:12', win: 4.02, draw: 3.08, loss: 1.80 }
        ],
        totalGoals: [
          { time: '2026-06-08 10:02', goals0: 8.00, goals1: 4.15, goals2: 2.95, goals3: 3.80, goals4: 6.60, goals5: 14.00, goals6: 26.00, goals7plus: 40.00 },
          { time: '2026-06-12 09:55', goals0: 8.00, goals1: 4.15, goals2: 2.95, goals3: 3.95, goals4: 6.50, goals5: 13.50, goals6: 25.00, goals7plus: 35.00 },
          { time: '2026-06-12 11:25', goals0: 8.70, goals1: 4.15, goals2: 2.95, goals3: 3.80, goals4: 6.50, goals5: 13.50, goals6: 25.00, goals7plus: 35.00 },
          { time: '2026-06-12 13:04', goals0: 8.70, goals1: 4.15, goals2: 2.95, goals3: 3.70, goals4: 6.55, goals5: 14.00, goals6: 27.00, goals7plus: 35.00 },
          { time: '2026-06-12 14:33', goals0: 8.70, goals1: 4.15, goals2: 2.95, goals3: 3.60, goals4: 6.60, goals5: 15.00, goals6: 29.00, goals7plus: 35.00 },
          { time: '2026-06-12 16:10', goals0: 9.00, goals1: 4.15, goals2: 2.90, goals3: 3.50, goals4: 7.00, goals5: 15.50, goals6: 29.00, goals7plus: 35.00 },
          { time: '2026-06-12 19:22', goals0: 8.25, goals1: 4.05, goals2: 2.90, goals3: 3.50, goals4: 7.30, goals5: 16.50, goals6: 31.00, goals7plus: 38.00 },
          { time: '2026-06-12 19:36', goals0: 7.50, goals1: 3.85, goals2: 2.90, goals3: 3.50, goals4: 8.00, goals5: 18.00, goals6: 35.00, goals7plus: 46.00 },
          { time: '2026-06-12 21:24', goals0: 7.50, goals1: 3.85, goals2: 3.05, goals3: 3.50, goals4: 7.60, goals5: 16.50, goals6: 32.00, goals7plus: 42.00 },
          { time: '2026-06-12 21:38', goals0: 7.50, goals1: 3.85, goals2: 3.10, goals3: 3.60, goals4: 7.00, goals5: 16.00, goals6: 32.00, goals7plus: 42.00 }
        ],
        halfTimeFullTime: [
          { time: '2026-06-08 10:02', HH: 3.10, HD: 15.00, HA: 35.00, DH: 4.50, DD: 4.25, DA: 8.00, AH: 27.00, AD: 15.00, AA: 6.65 },
          { time: '2026-06-11 10:34', HH: 2.95, HD: 15.00, HA: 35.00, DH: 4.50, DD: 4.35, DA: 8.25, AH: 27.00, AD: 15.00, AA: 7.00 },
          { time: '2026-06-12 09:55', HH: 2.85, HD: 14.50, HA: 35.00, DH: 4.45, DD: 4.75, DA: 8.25, AH: 27.00, AD: 14.50, AA: 7.00 },
          { time: '2026-06-12 13:09', HH: 2.90, HD: 14.50, HA: 35.00, DH: 4.45, DD: 4.75, DA: 8.25, AH: 27.00, AD: 14.50, AA: 6.70 },
          { time: '2026-06-12 14:44', HH: 2.80, HD: 14.50, HA: 35.00, DH: 4.30, DD: 5.25, DA: 8.25, AH: 27.00, AD: 14.50, AA: 6.70 },
          { time: '2026-06-12 15:48', HH: 2.80, HD: 13.50, HA: 35.00, DH: 4.30, DD: 5.25, DA: 8.75, AH: 27.00, AD: 13.50, AA: 6.85 },
          { time: '2026-06-12 16:20', HH: 2.80, HD: 13.50, HA: 33.00, DH: 4.30, DD: 5.25, DA: 8.90, AH: 25.00, AD: 13.50, AA: 7.00 },
          { time: '2026-06-12 20:54', HH: 2.86, HD: 13.00, HA: 33.00, DH: 4.20, DD: 5.25, DA: 8.90, AH: 25.00, AD: 13.00, AA: 7.15 }
        ],
        correctScore: [
          { time: '2026-06-08 10:02', score10: 6.00, score20: 8.00, score21: 7.25, score30: 16.00, score31: 16.00, score32: 30.00, score40: 50.00, score41: 15.00, score42: 100.00, score50: 150.00, score51: 15.00, score52: 300.00, scoreWinOther: 100.00, score00: 8.00, score11: 5.45, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 8.75, score02: 17.00, score12: 10.50, score03: 60.00, score13: 39.00, score23: 50.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-11 12:48', score10: 5.80, score20: 7.70, score21: 6.80, score30: 16.00, score31: 16.00, score32: 33.00, score40: 50.00, score41: 15.00, score42: 100.00, score50: 150.00, score51: 15.00, score52: 300.00, scoreWinOther: 100.00, score00: 8.00, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 9.50, score02: 21.00, score12: 10.50, score03: 65.00, score13: 39.00, score23: 55.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-11 17:21', score10: 5.80, score20: 7.50, score21: 6.60, score30: 16.00, score31: 15.00, score32: 33.00, score40: 50.00, score41: 15.00, score42: 90.00, score50: 150.00, score51: 12.50, score52: 250.00, scoreWinOther: 90.00, score00: 8.00, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 9.75, score02: 22.00, score12: 11.50, score03: 70.00, score13: 41.00, score23: 55.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 08:30', score10: 5.80, score20: 7.00, score21: 6.25, score30: 16.00, score31: 14.50, score32: 33.00, score40: 50.00, score41: 15.00, score42: 90.00, score50: 150.00, score51: 12.50, score52: 250.00, scoreWinOther: 90.00, score00: 8.00, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 9.85, score02: 23.00, score12: 13.50, score03: 75.00, score13: 45.00, score23: 60.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 09:56', score10: 5.50, score20: 6.75, score21: 6.00, score30: 17.00, score31: 14.50, score32: 33.00, score40: 55.00, score41: 15.00, score42: 80.00, score50: 125.00, score51: 10.00, score52: 200.00, scoreWinOther: 80.00, score00: 8.00, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 11.00, score02: 24.00, score12: 15.00, score03: 80.00, score13: 50.00, score23: 60.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 11:27', score10: 5.50, score20: 6.75, score21: 5.70, score30: 17.00, score31: 14.50, score32: 33.00, score40: 55.00, score41: 15.00, score42: 80.00, score50: 125.00, score51: 10.00, score52: 200.00, scoreWinOther: 80.00, score00: 8.70, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 11.00, score02: 24.00, score12: 15.00, score03: 80.00, score13: 50.00, score23: 60.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 13:13', score10: 5.50, score20: 6.75, score21: 5.70, score30: 17.50, score31: 14.50, score32: 37.00, score40: 70.00, score41: 15.00, score42: 80.00, score50: 125.00, score51: 10.00, score52: 200.00, scoreWinOther: 95.00, score00: 8.70, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.00, score02: 22.00, score12: 15.00, score03: 65.00, score13: 48.00, score23: 60.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 14:14', score10: 5.80, score20: 6.60, score21: 5.50, score30: 17.50, score31: 13.50, score32: 40.00, score40: 70.00, score41: 15.00, score42: 90.00, score50: 125.00, score51: 12.50, score52: 200.00, scoreWinOther: 95.00, score00: 8.70, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.00, score02: 22.00, score12: 15.00, score03: 65.00, score13: 48.00, score23: 60.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 15:34', score10: 5.80, score20: 6.50, score21: 5.35, score30: 17.50, score31: 12.50, score32: 40.00, score40: 70.00, score41: 15.00, score42: 90.00, score50: 125.00, score51: 12.50, score52: 200.00, scoreWinOther: 95.00, score00: 8.70, score11: 5.25, score22: 15.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.50, score02: 23.00, score12: 16.00, score03: 70.00, score13: 48.00, score23: 65.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 16:09', score10: 5.80, score20: 6.25, score21: 5.20, score30: 17.50, score31: 12.00, score32: 43.00, score40: 70.00, score41: 15.00, score42: 100.00, score50: 125.00, score51: 10.00, score52: 200.00, scoreWinOther: 100.00, score00: 9.00, score11: 5.25, score22: 17.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.50, score02: 23.00, score12: 16.00, score03: 70.00, score13: 48.00, score23: 65.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 17:18', score10: 5.80, score20: 6.00, score21: 5.00, score30: 17.50, score31: 14.00, score32: 43.00, score40: 70.00, score41: 15.00, score42: 100.00, score50: 125.00, score51: 10.00, score52: 200.00, scoreWinOther: 100.00, score00: 9.00, score11: 5.25, score22: 18.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.50, score02: 23.00, score12: 16.00, score03: 70.00, score13: 48.00, score23: 65.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 18:10', score10: 5.80, score20: 6.00, score21: 4.75, score30: 17.50, score31: 14.00, score32: 43.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 125.00, score00: 9.00, score11: 5.35, score22: 18.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.50, score02: 23.00, score12: 16.00, score03: 70.00, score13: 48.00, score23: 65.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 19:01', score10: 6.00, score20: 5.80, score21: 4.75, score30: 17.50, score31: 14.00, score32: 43.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 125.00, score00: 9.00, score11: 5.35, score22: 18.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.50, score02: 23.00, score12: 16.00, score03: 70.00, score13: 48.00, score23: 65.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 19:23', score10: 6.00, score20: 5.80, score21: 4.75, score30: 17.50, score31: 14.00, score32: 43.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 150.00, score00: 8.35, score11: 5.35, score22: 19.00, score33: 90.00, scoreDrawOther: 400.00, score01: 10.50, score02: 23.00, score12: 16.00, score03: 70.00, score13: 48.00, score23: 65.00, score04: 200.00, score14: 17.50, score24: 20.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 30.00 },
          { time: '2026-06-12 19:40', score10: 6.00, score20: 5.80, score21: 4.75, score30: 17.50, score31: 14.00, score32: 43.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 150.00, score00: 7.50, score11: 5.35, score22: 20.00, score33: 90.00, scoreDrawOther: 400.00, score01: 9.50, score02: 23.00, score12: 15.00, score03: 70.00, score13: 52.00, score23: 80.00, score04: 300.00, score14: 25.00, score24: 30.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 40.00 },
          { time: '2026-06-12 19:59', score10: 6.00, score20: 5.60, score21: 4.50, score30: 18.50, score31: 14.00, score32: 48.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 150.00, score00: 7.50, score11: 5.80, score22: 20.00, score33: 90.00, scoreDrawOther: 400.00, score01: 9.50, score02: 23.00, score12: 15.00, score03: 70.00, score13: 52.00, score23: 80.00, score04: 300.00, score14: 25.00, score24: 30.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 40.00 },
          { time: '2026-06-12 20:25', score10: 6.00, score20: 5.70, score21: 4.50, score30: 18.50, score31: 14.00, score32: 43.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 150.00, score00: 7.50, score11: 5.85, score22: 20.00, score33: 90.00, scoreDrawOther: 450.00, score01: 9.50, score02: 23.00, score12: 15.00, score03: 70.00, score13: 52.00, score23: 80.00, score04: 300.00, score14: 25.00, score24: 30.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 40.00 },
          { time: '2026-06-12 21:06', score10: 6.00, score20: 5.75, score21: 4.50, score30: 18.50, score31: 14.00, score32: 38.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 125.00, score00: 7.50, score11: 6.00, score22: 20.00, score33: 90.00, scoreDrawOther: 450.00, score01: 9.50, score02: 23.00, score12: 15.00, score03: 70.00, score13: 52.00, score23: 80.00, score04: 300.00, score14: 25.00, score24: 30.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 40.00 },
          { time: '2026-06-12 21:40', score10: 6.00, score20: 5.95, score21: 4.50, score30: 18.50, score31: 14.00, score32: 32.00, score40: 70.00, score41: 15.00, score42: 150.00, score50: 125.00, score51: 15.00, score52: 300.00, scoreWinOther: 125.00, score00: 7.50, score11: 6.05, score22: 25.00, score33: 90.00, scoreDrawOther: 450.00, score01: 8.50, score02: 23.00, score12: 15.00, score03: 75.00, score13: 52.00, score23: 80.00, score04: 300.00, score14: 25.00, score24: 30.00, score05: 50.00, score15: 50.00, score25: 60.00, scoreLoseOther: 40.00 }
        ]
      },
      preMatchAnalysis: {
        motivation: {
          teamA: {
            primaryGoal: '',
            pressureLevel: '',
            historicalAspiration: '',
            tacticalPurpose: '',
            motivationLevel: 9
          },
          teamB: {
            primaryGoal: '',
            strategy: '',
            honorDrive: '',
            motivationLevel: 6
          },
          comparison: '',            teamB: ''          },          injurySituation: {            teamA: '',            teamB: '',
            teamB: ''
          },
          fitness: {
            teamA: '',
            teamB: ''
          },
          historyConflict: '',            lastMeeting: ''          },          mindset: {            teamA: {              advantage: '',              burden: ''            },            teamB: {              advantage: '',              weakness: '',
            teamB: ''
          },
          tacticalPsychology: {
            teamA: '',
            teamB: '',        tacticalSummary: '',        playerOfMatch: '',        lessonsLearned: '',        xGAnalysis: '',        tacticalKey: '',        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '',          secondHalf: ''        },        playerRatingSummary: {          highest: '',          teamABest: '',          teamBBest: '',          lowest: '',
          teamB: ''      }    },    {      matchId: 'QAT-SUI-20260614',      competition: ''qatar', teamB: 'switzerland'',      teamAName: '',      venue: 'usa_west', neutral: false,      kickoff: '2026-06-14T03:00:00Z',      referee: '',      finalScore: { teamA: 1, teamB: 1 },      halfTimeScore: { teamA: 0, teamB: 1 },      venueConditions: {        stadium: '',        city: 'San Francisco Bay Area',        grassType: 'natural',        grassCondition: 'good',        temperature: 27,        temperatureRange: '25-32',        humidity: 40,        weather: 'sunny',        altitude: 4,        windSpeed: 14.4,        windDirection: 'variable',        impactAssessment: {          humidityImpact: 'low',          expectedEffect: 'both_affected',          stormRisk: false,          windImpact: 'low',          specialNote: ''2026-06-08 10:02:19', HH: 24, HD: 27, HA: 29, DH: 27, DD: 8.25, DA: 3.6, AH: 100, AD: 27, AA: 1.52 },
          { time: '2026-06-12 13:17:07', HH: 25, HD: 27, HA: 29, DH: 27, DD: 8.5, DA: 3.7, AH: 100, AD: 27, AA: 1.49 },
          { time: '2026-06-13 11:41:01', HH: 28, HD: 29, HA: 31, DH: 29, DD: 8.5, DA: 3.7, AH: 100, AD: 29, AA: 1.46 },
          { time: '2026-06-13 12:32:58', HH: 28, HD: 30, HA: 31, DH: 29, DD: 8.85, DA: 3.8, AH: 100, AD: 30, AA: 1.43 },
          { time: '2026-06-13 18:19:59', HH: 28, HD: 30, HA: 31, DH: 30, DD: 8.85, DA: 3.8, AH: 90, AD: 30, AA: 1.43 },
          { time: '2026-06-13 19:41:14', HH: 28, HD: 30, HA: 28, DH: 30, DD: 8.85, DA: 3.85, AH: 90, AD: 30, AA: 1.43 },
          { time: '2026-06-13 21:38:19', HH: 28, HD: 30, HA: 25, DH: 30, DD: 9.5, DA: 3.85, AH: 70, AD: 30, AA: 1.43 }
        ],
        totalGoals: [
          { time: '2026-06-08 10:02:19', goals0: 16, goals1: 5.7, goals2: 3.85, goals3: 3.5, goals4: 4.8, goals5: 8.1, goals6: 12, goals7plus: 18 },
          { time: '2026-06-13 11:41:12', goals0: 18, goals1: 5.8, goals2: 3.7, goals3: 3.35, goals4: 4.6, goals5: 8.1, goals6: 15, goals7plus: 20 },
          { time: '2026-06-13 12:44:02', goals0: 18, goals1: 5.9, goals2: 3.7, goals3: 3.25, goals4: 4.5, goals5: 8.2, goals6: 17.5, goals7plus: 20 },
          { time: '2026-06-13 12:46:55', goals0: 20, goals1: 5.9, goals2: 3.7, goals3: 3.25, goals4: 4.4, goals5: 8.2, goals6: 17.5, goals7plus: 20 },
          { time: '2026-06-13 14:58:23', goals0: 20, goals1: 6.2, goals2: 3.7, goals3: 3.25, goals4: 4.4, goals5: 8.2, goals6: 17.5, goals7plus: 17 },
          { time: '2026-06-13 16:51:42', goals0: 20, goals1: 6.4, goals2: 3.75, goals3: 3.25, goals4: 4.5, goals5: 8, goals6: 15, goals7plus: 17 },
          { time: '2026-06-13 17:37:59', goals0: 20, goals1: 6.4, goals2: 3.75, goals3: 3.2, goals4: 4.5, goals5: 8, goals6: 15, goals7plus: 18 },
          { time: '2026-06-13 21:17:48', goals0: 20, goals1: 6.55, goals2: 3.75, goals3: 3.1, goals4: 4.35, goals5: 8.5, goals6: 15, goals7plus: 21 }
        ],
        correctScore: [
          { time: '2026-06-08 10:02:19', score10: 27, score20: 90, score21: 30, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 800, score52: 800, scoreWinOther: 500, score00: 16, score11: 10, score22: 28, score33: 125, scoreDrawOther: 500, score01: 6.2, score02: 5.2, score12: 8, score03: 6.3, score13: 10, score23: 30, score04: 10, score14: 17, score24: 60, score05: 20, score15: 35, score25: 100, scoreLoseOther: 17 },
          { time: '2026-06-12 11:12:48', score10: 27, score20: 95, score21: 32, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 800, score52: 800, scoreWinOther: 500, score00: 16, score11: 10, score22: 28, score33: 125, scoreDrawOther: 500, score01: 6.2, score02: 5, score12: 8, score03: 6.1, score13: 10, score23: 30, score04: 10, score14: 18, score24: 64, score05: 22, score15: 37, score25: 110, scoreLoseOther: 17 },
          { time: '2026-06-12 12:34:49', score10: 27, score20: 95, score21: 32, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 800, score52: 800, scoreWinOther: 500, score00: 16, score11: 10, score22: 28, score33: 125, scoreDrawOther: 500, score01: 6.2, score02: 4.8, score12: 8, score03: 6.1, score13: 10, score23: 30, score04: 10.5, score14: 18, score24: 64, score05: 22, score15: 37, score25: 110, scoreLoseOther: 18 },
          { time: '2026-06-12 13:29:49', score10: 27, score20: 95, score21: 32, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 800, score52: 800, scoreWinOther: 500, score00: 16, score11: 10, score22: 28, score33: 125, scoreDrawOther: 500, score01: 6.2, score02: 4.6, score12: 8, score03: 6.1, score13: 10, score23: 31, score04: 11, score14: 19, score24: 65, score05: 22, score15: 40, score25: 110, scoreLoseOther: 18 },
          { time: '2026-06-12 14:15:40', score10: 27, score20: 95, score21: 32, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 800, score52: 800, scoreWinOther: 500, score00: 16, score11: 10, score22: 28, score33: 125, scoreDrawOther: 500, score01: 6.3, score02: 4.4, score12: 7.5, score03: 6.1, score13: 10, score23: 35, score04: 11.5, score14: 20, score24: 65, score05: 22, score15: 40, score25: 110, scoreLoseOther: 19 },
          { time: '2026-06-12 19:25:00', score10: 27, score20: 95, score21: 32, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 800, score52: 800, scoreWinOther: 500, score00: 16, score11: 11, score22: 28, score33: 125, scoreDrawOther: 500, score01: 6.3, score02: 4.2, score12: 7.5, score03: 6.1, score13: 10, score23: 35, score04: 11.5, score14: 21, score24: 65, score05: 22, score15: 40, score25: 110, scoreLoseOther: 19 },
          { time: '2026-06-13 11:04:30', score10: 30, score20: 95, score21: 35, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 900, score52: 900, scoreWinOther: 500, score00: 18, score11: 12, score22: 31, score33: 125, scoreDrawOther: 500, score01: 6.5, score02: 4.2, score12: 7.3, score03: 5.9, score13: 9.75, score23: 37, score04: 11.5, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 19 },
          { time: '2026-06-13 11:41:22', score10: 30, score20: 95, score21: 35, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 900, score52: 900, scoreWinOther: 500, score00: 18, score11: 12, score22: 31, score33: 125, scoreDrawOther: 500, score01: 6.5, score02: 4, score12: 7.3, score03: 5.7, score13: 9.5, score23: 37, score04: 11.5, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 19 },
          { time: '2026-06-13 12:15:38', score10: 30, score20: 95, score21: 38, score30: 35, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 800, score51: 900, score52: 900, scoreWinOther: 500, score00: 18, score11: 12.5, score22: 32, score33: 125, scoreDrawOther: 500, score01: 6.65, score02: 3.9, score12: 7.3, score03: 5.6, score13: 9.25, score23: 40, score04: 11.5, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 19 },
          { time: '2026-06-13 12:40:43', score10: 32, score20: 95, score21: 38, score30: 250, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 900, score51: 900, score52: 900, scoreWinOther: 500, score00: 18, score11: 14, score22: 34, score33: 125, scoreDrawOther: 500, score01: 6.8, score02: 3.8, score12: 7.2, score03: 5.4, score13: 9.25, score23: 40, score04: 11.5, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 19 },
          { time: '2026-06-13 12:47:21', score10: 32, score20: 95, score21: 38, score30: 250, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 900, score51: 900, score52: 900, scoreWinOther: 500, score00: 20, score11: 14, score22: 34, score33: 125, scoreDrawOther: 500, score01: 6.8, score02: 3.8, score12: 7.2, score03: 5.4, score13: 8.8, score23: 40, score04: 11.5, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 19 },
          { time: '2026-06-13 17:33:20', score10: 32, score20: 95, score21: 38, score30: 250, score31: 17.5, score32: 150, score40: 800, score41: 600, score42: 500, score50: 900, score51: 900, score52: 900, scoreWinOther: 500, score00: 20, score11: 14, score22: 34, score33: 125, scoreDrawOther: 500, score01: 7, score02: 3.8, score12: 7.2, score03: 5.3, score13: 8.4, score23: 40, score04: 12, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 19 },
          { time: '2026-06-13 18:01:17', score10: 32, score20: 80, score21: 38, score30: 250, score31: 150, score32: 150, score40: 600, score41: 500, score42: 500, score50: 900, score51: 900, score52: 900, scoreWinOther: 500, score00: 20, score11: 14, score22: 34, score33: 125, scoreDrawOther: 500, score01: 7.2, score02: 3.8, score12: 7.2, score03: 5.3, score13: 8.4, score23: 40, score04: 12, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 19 },
          { time: '2026-06-13 19:49:29', score10: 32, score20: 80, score21: 38, score30: 250, score31: 150, score32: 150, score40: 600, score41: 500, score42: 500, score50: 900, score51: 900, score52: 900, scoreWinOther: 500, score00: 20, score11: 14, score22: 34, score33: 125, scoreDrawOther: 500, score01: 7.2, score02: 4, score12: 7.2, score03: 5, score13: 8.1, score23: 40, score04: 12, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 20 },
          { time: '2026-06-13 20:14:46', score10: 32, score20: 65, score21: 38, score30: 230, score31: 150, score32: 150, score40: 500, score41: 400, score42: 500, score50: 900, score51: 900, score52: 900, scoreWinOther: 500, score00: 20, score11: 14.5, score22: 35, score33: 125, scoreDrawOther: 500, score01: 7.2, score02: 4, score12: 7.2, score03: 5, score13: 8.1, score23: 40, score04: 12, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 20 },
          { time: '2026-06-13 22:46:26', score10: 32, score20: 65, score21: 38, score30: 200, score31: 150, score32: 150, score40: 500, score41: 400, score42: 500, score50: 900, score51: 900, score52: 900, scoreWinOther: 500, score00: 20, score11: 14.5, score22: 35, score33: 125, scoreDrawOther: 500, score01: 7.3, score02: 4.1, score12: 8, score03: 4.75, score13: 7.5, score23: 40, score04: 12, score14: 21, score24: 65, score05: 22, score15: 40, score25: 120, scoreLoseOther: 20 }
        ],
        asianHandicap: [
          { time: '2026-06-08 10:02:19', win: 1.98, draw: 3.85, lose: 2.74 },
          { time: '2026-06-11 12:22:36', win: 2.02, draw: 3.82, lose: 2.69 },
          { time: '2026-06-12 11:47:14', win: 2.08, draw: 3.75, lose: 2.62 },
          { time: '2026-06-13 11:02:04', win: 2.12, draw: 3.7, lose: 2.58 },
          { time: '2026-06-13 12:22:50', win: 2.17, draw: 3.65, lose: 2.54 },
          { time: '2026-06-13 12:54:12', win: 2.2, draw: 3.6, lose: 2.52 },
          { time: '2026-06-13 13:38:54', win: 2.22, draw: 3.55, lose: 2.52 },
          { time: '2026-06-13 17:04:22', win: 2.3, draw: 3.47, lose: 2.46 },
          { time: '2026-06-13 18:22:54', win: 2.35, draw: 3.42, lose: 2.43 }
        ]
      },
      preMatchAnalysis: {
        motivation: {
          teamA: {
            primaryGoal: '',
            pressureLevel: '',
            historicalAspiration: '',
            tacticalPurpose: '',
            motivationLevel: 5,
            strategy: '',            pressureLevel: '',            historicalAspiration: '',            tacticalPurpose: '',            motivationLevel: 8,            strategy: ''
        },
        externalFactors: {
          squadValue: {
            teamA: '',
            teamB: '',            teamB: '',
            teamB: ''
          },
          tacticalMatchup: {
            teamAWeakness: '',
            teamBStrategy: '',
            teamBWeakness: '',
            teamAStrategy: ''
          },
          leagueGap: '',            psychologicalImpact: '',
              burden: '',
              burden: '',            teamB: ''          },          tacticalPsychology: {            teamA: '',            teamB: ''4-2-3-1',
          avgRating: 6.78,
          avgAge: 23.4,
          players: [
            { number: 1, position: '', name: '', club: '', league: '' },
            { number: 13, position: '', club: '', league: ''0' },
            { number: 2, position: '', league: '' },
            { number: 16, position: ''-', club: '', league: ', apps: 22, goals: 1, assists: 0, keyPasses: 0.3, rating: 6.4, yellowCard: true, notes: '' },
            { number: 14, position: '', club: ', league: '' },
            { number: 5, position: '', name: '', league: ', apps: 21, goals: 0, assists: 1, keyPasses: 0.4, rating: 6.4, minuteSubstituted: 60, yellowCard: true, notes: '' },
            { number: 4, position: '', name: '', league: ', apps: 24, goals: 2, assists: 2, keyPasses: 0.8, rating: 7.2, notes: '',
            { number: 8, position: '', name: '', club: '', league: '' },
            { number: 23, position: '',
            { number: 11, position: '', name: '', league: '' },
            { number: 15, position: '', name: '', club: '', league: '' }          ],          substitutes: [            { number: 12, name: '', club: ''-', club: ', league: '' },            { number: 20, name: '', league: '' },            { number: 26, name: '', league: ''3-4-3',
          avgRating: 7.02,
          avgAge: 26.2,
          players: [
            { number: 1, position: '', name: '', league: '', apps: 27, goals: 0, assists: 0, keyPasses: 0.1, rating: 7.2, notes: '' },
            { number: 4, position: ''-', club: ', league: '', apps: 26, goals: 2, assists: 1, keyPasses: 0.3, rating: 7.1, notes: '' },
            { number: 5, position: '', name: '', league: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.4, rating: 7.5, notes: '' },
            { number: 6, position: '', club: '', apps: 24, goals: 1, assists: 1, keyPasses: 0.3, rating: 6.5, yellowCard: true, notes: '' },
            { number: 13, position: '', league: '', apps: 26, goals: 3, assists: 4, keyPasses: 1.0, rating: 7.6, assist: true, minuteSubstituted: 89, notes: '',
            { number: 10, position: '', name: '', club: '', league: '', apps: 28, goals: 3, assists: 5, keyPasses: 1.2, rating: 7.2, captain: true, yellowCard: true, notes: '',
            { number: 8, position: '', name: '', club: '', league: '', apps: 25, goals: 2, assists: 2, keyPasses: 0.7, rating: 6.5, minuteSubstituted: 89, notes: '' },
            { number: 20, position: '', club: '', league: '', apps: 24, goals: 4, assists: 3, keyPasses: 0.9, rating: 7.4, minuteSubstituted: 65, notes: '' },
            { number: 11, position: '', club: '', league: '', apps: 26, goals: 8, assists: 3, keyPasses: 0.6, rating: 6.3, minuteSubstituted: 65, notes: '' },
            { number: 7, position: '', name: '', league: '', apps: 25, goals: 7, assists: 4, keyPasses: 0.8, rating: 7.0, goal: true, notes: '',
            { number: 17, position: ''-', club: '', league: '', apps: 27, goals: 9, assists: 6, keyPasses: 1.4, rating: 7.9, notes: '' }          ],          substitutes: [            { number: 9, name: ', club: '', league: '', apps: 22, goals: 3, assists: 1, keyPasses: 0.5, rating: 6.1, minuteOn: 65, notes: '' },            { number: 22, name: '', club: '', apps: 23, goals: 2, assists: 2, keyPasses: 0.6, rating: 6.4, minuteOn: 65, notes: '' },            { number: 23, name: '', club: ', league: '', apps: 24, goals: 6, assists: 2, keyPasses: 0.7, rating: 6.9, minuteOn: 79, notes: '' },            { number: 14, name: '', apps: 21, goals: 1, assists: 1, keyPasses: 0.4, rating: 6.3, minuteOn: 89, notes: '' },            { number: 2, name: '', apps: 20, goals: 0, assists: 1, keyPasses: 0.3, rating: 5.4, ownGoal: true, minuteOn: 89, notes: '' }          ]        }      },      matchEvents: [        { minute: 17, type: 'goal', team: 'SUI', player: 'Noah Okafor', assist: 'Ricardo Rodriguez', score: '0-1', description: ''injury', team: 'QAT', player: '', description: '' },        { minute: 90, type: 'injury', team: 'SUI', player: '', description: '' },        { minute: 90, type: 'goal', team: 'QAT', player: 'Mughdish Muharem (', assist: 'Almoez Ali', score: '1-1', description: '' }      ],      playerXA: {        teamA: { 'Homam Ahmed': 0.44 },        teamB: { 'Ricardo Rodriguez': 0.52, 'Granit Xhaka': 0.46, 'Rubn Vargas': 0.39, 'Silvan Widmer': 0.35 }      },      injuryReport: {        teamA: [],        teamB: []      },      recentMatches: {        teamA: [],        teamB: [          { date: '2024-10-13', opponent: 'Serbia', result: '0-2', halfTime: '0-1', venue: 'away', competition: 'UEFA Nations League', cornersA: 7, cornersB: 3 }        ]      },      postMatchAnalysis: {        keyFactor: '',        tacticalSummary: '34360',        playerOfMatch: '-() - 7.9',        lessonsLearned: '(26',        xGAnalysis: 'xG 3.241xG 0.761',        tacticalKey: '',        leagueGapAnalysis: '',        goalKeyMoments: {          firstHalf: '-0',          secondHalf: '891-1'        },        playerRatingSummary: {          highest: '.9',          teamABest: '.4-/-.2',          teamBBest: '7.67.5.4.0',          lowest: '.4
        },
        teamTactics: {
          teamA: '',
          teamB: ''090',        drawKeyFactors: '960'      },      finalResults: {        matchResult: 'draw',        matchOdds: null,        handicapResult: 'win',        handicapOdds: 2.35,        handicapType: '+2',        correctScore: '1-1',        correctScoreOdds: 14.50,        totalGoals: 2,        totalGoalsOdds: 3.75,        halfTimeFullTime: 'DA',        halfTimeFullTimeOdds: 30.00,        ftScore: { teamA: 1, teamB: 1 },        htScore: { teamA: 0, teamB: 1 }      }    },    {      matchId: 'BRA-MAR-20260614',      competition: ''brazil', teamB: 'morocco'',      teamAName: '', teamBName: '',      venue: 'usa_east', neutral: false,      kickoff: '2026-06-14T06:00:00Z',      referee: '(Slavko Vini),      finalScore: { teamA: 1, teamB: 1 },      halfTimeScore: { teamA: 1, teamB: 1 },      venueConditions: {        stadium: '',        city: 'New Jersey/East Rutherford',        grassType: 'natural',        grassCondition: 'good',        temperature: 31.5,        temperatureRange: '31-32',        humidity: 33,        weather: 'sunny',        altitude: 9,        windSpeed: 15,        windDirection: 'variable',        impactAssessment: {          humidityImpact: 'moderate',          expectedEffect: 'both_affected',          stormRisk: false,          windImpact: 'low',          specialNote: ''        }      },      matchStats: {        possessionA: 51, possessionB: 49,        shotsA: 12, shotsB: 14,        shotsOnTargetA: 5, shotsOnTargetB: 3,        shotsOffTargetA: 7, shotsOffTargetB: 11,        blockedShotsA: 4, blockedShotsB: 6,        insideBoxShotsA: 9, insideBoxShotsB: 6,        outsideBoxShotsA: 3, outsideBoxShotsB: 8,        bigChancesA: 1, bigChancesB: 2,        missedChancesA: 1, missedChancesB: 1,        cornersA: 6, cornersB: 2,        freeKicksA: 9, freeKicksB: 7,        foulsA: 16, foulsB: 14,        offsidesA: 0, offsidesB: 1,        yellowCardsA: 2, yellowCardsB: 0,        redCardsA: 0, redCardsB: 0,        savesA: 3, savesB: 4,        totalPassesA: 524, totalPassesB: 493,        passAccuracyA: 84, passAccuracyB: 81,        finalThirdPassesA: 100, finalThirdPassesB: 149,        throughBallsA: 4, throughBallsB: 7,        crossesA: { total: 16, accurate: 4 },        crossesB: { total: 13, accurate: 3 },        dangerousAttacksA: 101, dangerousAttacksB: 103,        throwInsA: 17, throwInsB: 14,        tacklesA: 14, tacklesB: 15,        xG: { teamA: 0.99, teamB: 1.33 },        xGOT: { teamA: 1.07, teamB: 1.18 },        xA: { teamA: 0.62, teamB: 0.78 },        xGA: { teamA: 1.18, teamB: 1.07 }      },      lineups: {        teamA: {          formation: '4-2-3-1',          avgRating: 6.78,          avgAge: 29.1,          players: [            { number: 1, position: '', name: ', club: '', apps: 28, goals: 0, assists: 0, keyPasses: 0.1, rating: 6.9, notes: ' },            { number: 24, position: '', club: '', league: '', apps: 25, goals: 2, assists: 1, keyPasses: 0.3, rating: 6.2, yellowCard: true, minuteSubstituted: 46, notes: '', club: '', league: '', apps: 27, goals: 3, assists: 2, keyPasses: 0.5, rating: 7.3, captain: true, notes: '' },            { number: 3, position: ', name: '', league: '', apps: 26, goals: 2, assists: 3, keyPasses: 0.4, rating: 7.1, notes: '' },            { number: 16, position: '', league: '', apps: 24, goals: 2, assists: 4, keyPasses: 0.9, rating: 7.2, notes: '', name: '', club: '', league: '', apps: 26, goals: 3, assists: 2, keyPasses: 0.6, rating: 6.6, yellowCard: true, minuteSubstituted: 46, notes: '' },            { number: 8, position: '', name: '', club: '', league: '', apps: 27, goals: 4, assists: 5, keyPasses: 1.1, rating: 6.7, minuteSubstituted: 80, notes: ' },            { number: 11, position: '', name: '', club: '', league: '', apps: 28, goals: 8, assists: 7, keyPasses: 1.2, rating: 6.5, notes: '' },            { number: 20, position: '', league: '', apps: 26, goals: 6, assists: 8, keyPasses: 1.4, rating: 6.9, minuteSubstituted: 61, notes: '' },            { number: 7, position: '', name: '', club: '', apps: 30, goals: 15, assists: 9, keyPasses: 1.5, rating: 8.0, goal: true, notes: '', name: '', league: '', apps: 27, goals: 10, assists: 3, keyPasses: 0.8, rating: 6.2, minuteSubstituted: 62, notes: '', league: '', apps: 25, goals: 2, assists: 3, keyPasses: 0.7, rating: 6.8, minuteOn: 46, notes: '', club: '', apps: 26, goals: 2, assists: 4, keyPasses: 0.8, rating: 6.6, minuteOn: 46, notes: '' },            { number: 9, name: '-, club: '', league: '', apps: 27, goals: 7, assists: 4, keyPasses: 0.9, rating: 6.8, minuteOn: 61, notes: '' },            { number: 21, name: '', league: '', apps: 24, goals: 6, assists: 5, keyPasses: 1.0, rating: 7.0, minuteOn: 62, notes: '', apps: 23, goals: 1, assists: 2, keyPasses: 0.5, rating: 6.4, minuteOn: 80, notes: ''4-2-3-1',
          avgRating: 6.86,
          avgAge: 25.6,
          players: [
            { number: 1, position: '', name: '', club: '', league: '', apps: 27, goals: 0, assists: 0, keyPasses: 0.0, rating: 6.9, notes: '' },
            { number: 3, position: '', club: '', apps: 26, goals: 3, assists: 4, keyPasses: 0.8, rating: 7.0, minuteSubstituted: 80, notes: '' },
            { number: 18, position: '', league: '', apps: 24, goals: 1, assists: 2, keyPasses: 0.4, rating: 6.6, notes: '',
            { number: 14, position: '', apps: 25, goals: 2, assists: 1, keyPasses: 0.3, rating: 6.6, notes: '', club: '', league: '', apps: 27, goals: 4, assists: 6, keyPasses: 1.1, rating: 7.2, captain: true, notes: '', name: '', league: '', apps: 23, goals: 1, assists: 1, keyPasses: 0.5, rating: 6.9, notes: '', name: '', apps: 22, goals: 0, assists: 2, keyPasses: 0.6, rating: 6.9, notes: '' },
            { number: 8, position: '', name: '', league: '', apps: 26, goals: 5, assists: 6, keyPasses: 1.2, rating: 6.7, minuteSubstituted: 65, notes: '' },
            { number: 23, position: '', club: '', league: '', apps: 25, goals: 4, assists: 3, keyPasses: 0.9, rating: 7.0, minuteSubstituted: 80, notes: '',
            { number: 10, position: '', name: '', league: '', apps: 28, goals: 7, assists: 8, keyPasses: 1.3, rating: 6.8, assist: true, minuteSubstituted: 65, notes: '',
            { number: 11, position: '', name: '', club: '', apps: 26, goals: 11, assists: 4, keyPasses: 0.7, rating: 7.8, goal: true, minuteSubstituted: 89, notes: '', club: '', league: '', apps: 23, goals: 2, assists: 1, keyPasses: 0.5, rating: 6.6, minuteOn: 65, notes: '', club: '', apps: 24, goals: 3, assists: 2, keyPasses: 0.6, rating: 6.5, minuteOn: 65, notes: '' },            { number: 21, name: '-', club: '', league: '', apps: 22, goals: 3, assists: 1, keyPasses: 0.7, rating: 6.7, minuteOn: 80, notes: '' },            { number: 26, name: '', club: ', league: '', apps: 21, goals: 1, assists: 1, keyPasses: 0.4, rating: 6.7, minuteOn: 80, notes: '' },            { number: 9, name: '', league: '', apps: 23, goals: 4, assists: 2, keyPasses: 0.5, rating: 6.5, minuteOn: 89, notes: '' }
          ]
        }
      },
      matchEvents: [
        { minute: 21, type: 'goal', team: 'MAR', player: 'Abde Ezzalzouli', assist: 'Brahim Diaz', score: '0-1', description: '' },
        { minute: 32, type: 'goal', team: 'BRA', player: 'Vinicius Junior', assist: 'Bruno Guimaraes', score: '1-1', description: ''Bruno Guimaraes': 0.62, 'Lucas Paqueta': 0.19, 'Raphinha': 0.11 },        teamB: { 'Brahim Diaz': 0.56, 'Azzedine Ounahi': 0.22, 'Achraf Hakimi': 0.13 }      },      injuryReport: {        teamA: [          { player: 'Neymar', role: '', injury: '', status: '', impact: '' },          { player: 'Eder Militao', role: '', status: '', impact: '' },          { player: 'Rodrygo', role: '', injury: '', status: '', impact: '' },          { player: 'Wesley', role: ', injury: '', status: '', impact: '' }
        ],
        teamB: [
          { player: 'Nayef Aguerd', role: '', status: 'injured', impact: ' },
          { player: 'Abde Ezzalzouli', role: '', status: 'injured', impact: '' },
          { player: 'Noussair Mazraoui', role: '', status: '', impact: ''2025-10-14', opponent: 'Japan', result: '2-3', halfTime: '2-0', venue: 'neutral', competition: 'Kirin Cup', cornersA: 2, cornersB: 4 },
          { date: '2025-09-10', opponent: 'Bolivia', result: '0-1', halfTime: '0-1', venue: 'away', competition: 'South American Qualifiers', cornersA: 4, cornersB: 5 },
          { date: '2025-09-05', opponent: 'Chile', result: '3-0', halfTime: '1-0', venue: 'home', competition: 'South American Qualifiers', cornersA: 4, cornersB: 3 },
          { date: '2025-06-11', opponent: 'Paraguay', result: '1-0', halfTime: '1-0', venue: 'home', competition: 'South American Qualifiers', cornersA: 11, cornersB: 3 },
          { date: '2025-06-06', opponent: 'Ecuador', result: '0-0', halfTime: '0-0', venue: 'away', competition: 'South American Qualifiers', cornersA: 3, cornersB: 4 },
          { date: '2025-03-26', opponent: 'Argentina', result: '1-4', halfTime: '1-3', venue: 'away', competition: 'South American Qualifiers', cornersA: 0, cornersB: 6 },
          { date: '2025-03-21', opponent: 'Colombia', result: '2-1', halfTime: '1-1', venue: 'home', competition: 'South American Qualifiers', cornersA: 4, cornersB: 2 },
          { date: '2024-11-20', opponent: 'Uruguay', result: '1-1', halfTime: '0-0', venue: 'home', competition: 'South American Qualifiers', cornersA: 4, cornersB: 2 },
          { date: '2024-11-15', opponent: 'Venezuela', result: '1-1', halfTime: '1-0', venue: 'away', competition: 'South American Qualifiers', cornersA: 9, cornersB: 3 },
          { date: '2024-10-16', opponent: 'Peru', result: '4-0', halfTime: '1-0', venue: 'home', competition: 'South American Qualifiers', cornersA: 6, cornersB: 1 }
        ],
        teamB: [
          { date: '2026-01-19', opponent: 'Senegal', result: '3-0', halfTime: '0-0', venue: 'neutral', competition: 'Africa Cup of Nations', cornersA: 8, cornersB: 6 },
          { date: '2026-01-15', opponent: 'Nigeria', result: '0-0', halfTime: '0-0', venue: 'neutral', competition: 'Africa Cup of Nations', cornersA: 4, cornersB: 0 },
          { date: '2026-01-10', opponent: 'Cameroon', result: '2-0', halfTime: '1-0', venue: 'neutral', competition: 'Africa Cup of Nations', cornersA: 7, cornersB: 4 },
          { date: '2026-01-05', opponent: 'Tanzania', result: '1-0', halfTime: '0-0', venue: 'neutral', competition: 'Africa Cup of Nations', cornersA: 4, cornersB: 0 },
          { date: '2025-12-30', opponent: 'Zambia', result: '3-0', halfTime: '2-0', venue: 'neutral', competition: 'Africa Cup of Nations', cornersA: 5, cornersB: 1 },
          { date: '2025-12-27', opponent: 'Mali', result: '1-1', halfTime: '1-0', venue: 'neutral', competition: 'Africa Cup of Nations', cornersA: 6, cornersB: 0 },
          { date: '2025-12-22', opponent: 'Comoros', result: '2-0', halfTime: '0-0', venue: 'neutral', competition: 'Africa Cup of Nations', cornersA: 5, cornersB: 0 },
          { date: '2025-12-19', opponent: 'Jordan', result: '2-2', halfTime: '1-0', venue: 'neutral', competition: 'Arab Cup', cornersA: 7, cornersB: 1 },
          { date: '2025-12-15', opponent: 'UAE', result: '3-0', halfTime: '1-0', venue: 'neutral', competition: 'Arab Cup', cornersA: 3, cornersB: 6 },
          { date: '2025-12-11', opponent: 'Syria', result: '1-0', halfTime: '0-0', venue: 'neutral', competition: 'Arab Cup', cornersA: 8, cornersB: 2 }
        ]
      },
      preMatchAnalysis: {
        motivation: {
          teamA: {
            primaryGoal: '',
            historicalAspiration: '',
            tacticalPurpose: '',
            motivationLevel: 8,
            strategy: '',            pressureLevel: '',            historicalAspiration: ''
        },
        externalFactors: {
          squadValue: {
            teamA: '',
            teamB: '',            teamB: '',            teamBWeakness: '',
            tacticalAdvantage: ''Friendly', result: 'Brazil won' },              { year: 1998, , result: 'Brazil 3-0 Morocco', note: ''Friendly', result: 'Morocco 2-1 Brazil', note: ' }
            ],
            psychologicalImpact: '''xgboost''lightgbm''poisson', 'dixonCole', 'ssm', 'xgboost', 'lightgbm', 'elo''Stacked-Ensemble-v4.2''none', msg: '''winA' : (modelDraw > Math.max(modelWinA, modelWinB) ? 'draw' : 'winB''winA' : (oddsDraw > Math.max(oddsWinA, oddsWinB) ? 'draw' : 'winB''high' : (diff > 0.15 ? 'medium' : 'low');
      return {
        hasConflict: true,
        level: level,
        modelBest: modelBest,
        oddsBest: oddsBest,
        modelProbs: { winA: modelWinA, draw: modelDraw, winB: modelWinB },
        oddsProbs: { winA: oddsWinA, draw: oddsDraw, winB: oddsWinB },
        totalDiff: diff,
        msg: '' + (modelBest==='winA'?'':(modelBest==='draw'?'':'')) +
             '' + (oddsBest==='winA'?'':(oddsBest==='draw'?'':'')) +
             '' + (diff*100).toFixed(1) + '%)'
      };
    }
    return { hasConflict: false, level: 'none', msg: '''odds_drift', detail: ''%, '', risk: risk1, detail: '' + (injuryCountA + injuryCountB) + '' + favForm.toFixed(2) + '''', risk: defRisk, detail: '' + undForm.toFixed(2) + '''';
    var defFormationB = b.formation || '';
    var undFormation = eloA >= eloB ? defFormationB : defFormationA;
    var isDeepDefence = (undFormation.indexOf('5-') === 0 || undFormation.indexOf('3-') === 0);
    if (isDeepDefence && favProb > 0.5) {
      var risk3 = 0.08;
      totalRisk += risk3;
      triggers.push({ layer: 3, name: '' + undFormation + '''winA' : (currentWdw.winB > currentWdw.winA ? 'winB' : 'draw');
    allM.forEach(function(m) {
      var mDir = m.winA > m.winB ? 'winA' : (m.winB > m.winA ? 'winB' : 'draw''');
      if (marketDiverge) detail4.push('');
      triggers.push({ layer: 4, name: '', risk: risk4, detail: detail4.join('; ''winA' : (_aResult.goalsA < _aResult.goalsB ? 'winB' : 'draw');
          var _aPredDir = _aPred.wdw.winA > _aPred.wdw.winB ? 'winA' : 'winB''', risk: 0.06, detail: '' });
    }
    if (isFirstMeeting && favProb > 0.5) {
      risk5 += 0.04;
      triggers.push({ layer: 5, name: '', risk: 0.04, detail: '''' : (totalRisk >= 0.35 ? ' : (totalRisk >= 0.25 ? ' : (totalRisk >= 0.15 ? ' : ''))),
      triggers: triggers,
      triggerCount: triggers.length,
      correction: correction,
      favTeam: favKey,
      undTeam: undKey,
      favProb: favProb,
      undProb: undProb
    };
  }

  // ─── 完整预测输出 (v3.6: 胜平比分+半全总进 ───
  function predictFullSlip(teamAKey, teamBKey, options) {
    options = options || {};
    
    // ─── P0修复: 副作用污染问───
    // 保存球队原始数据，函数结束时恢复，防止数据永久篡
    var teamA = TEAMS[teamAKey];
    var teamB = TEAMS[teamBKey];
    var originalA = teamA ? { attack: teamA.attack, defence: teamA.defence, keyPlayer: teamA.keyPlayer, injury: teamA.injury } : null;
    var originalB = teamB ? { attack: teamB.attack, defence: teamB.defence, keyPlayer: teamB.keyPlayer, injury: teamB.injury } : null;

    try {
      // v6.1: 阵容深度修正 (如果提供了阵容数
      var squadDepthA = null, squadDepthB = null;
      if (options.squadDataA) {
        squadDepthA = applySquadDepthAdjustment(teamAKey, options.squadDataA);
      }
      if (options.squadDataB) {
        squadDepthB = applySquadDepthAdjustment(teamBKey, options.squadDataB);
      }

      // v6.2: 情报数据解析
      var intelA = options.intelA ? parseTacticalIntel(options.intelA) : { overallImpact: 0 };
      var intelB = options.intelB ? parseTacticalIntel(options.intelB) : { overallImpact: 0 };
      var formA = options.recentFormA ? parseRecentForm(teamAKey, options.recentFormA) : { formScore: 0 };
      var formB = options.recentFormB ? parseRecentForm(teamBKey, options.recentFormB) : { formScore: 0 };
      var h2h = options.h2hRecords ? parseHistoricalH2H(options.h2hRecords) : { h2hFactor: 0 };
      var statsA = options.playerStatsA ? parsePlayerStats(teamAKey, options.playerStatsA) : { overallBoost: 0 };
      var statsB = options.playerStatsB ? parsePlayerStats(teamBKey, options.playerStatsB) : { overallBoost: 0 };
      var weather = options.weather ? parseWeather(options.weather) : { impact: 0 };

      // 应用情报修正到球队数
      if (teamA) {
        var intelBoostA = (intelA.overallImpact || 0) + (formA.formScore || 0) * 0.1 + (statsA.overallBoost || 0);
        teamA.attack = teamA.attack * (1 + intelBoostA);
        teamA.defence = teamA.defence * (1 - intelBoostA * 0.5); // 情报好则防守也略提升
      }
      if (teamB) {
        var intelBoostB = (intelB.overallImpact || 0) + (formB.formScore || 0) * 0.1 + (statsB.overallBoost || 0);
        teamB.attack = teamB.attack * (1 + intelBoostB);
        teamB.defence = teamB.defence * (1 - intelBoostB * 0.5);
      }
      // 交锋修正: 应用到主
      if (teamA && h2h.h2hFactor !== 0) {
        teamA.attack = teamA.attack * (1 + h2h.h2hFactor);
      }
      // 天气修正
      if (weather.impact !== 0 && teamA && teamB) {
        teamA.attack = teamA.attack * (1 + weather.impact);
        teamB.attack = teamB.attack * (1 + weather.impact);
      }

    // ─── v6.3 OPT-7 [P0]: xG差距因子 (xG Gap Factor) ───
    // 问题: 模型仅用attack/defence的比 未考虑xG差距的边际效
    // 案例: 西班牙attack=3.29 vs 佛得角attack=1.15, 差距2.14, 但模型线性放
    // 方案: 当xG差距>1.0 对强势方进攻施加递减收益修正(对数压缩)
    var xgGapA_B = (teamA ? teamA.attack : 1.5) - (teamB ? teamB.defence : 1.0);
    var xgGapB_A = (teamB ? teamB.attack : 1.5) - (teamA ? teamA.defence : 1.0);
    // xG差距递减修正: 差距越大, 边际收益越低 (模拟 diminishing returns)
    function xgGapDiminishing(gap) {
      if (gap <= 0) return 1.0; // 负差距不修正
      if (gap <= 0.5) return 1.0; // 小差距无修正
      // 对数压缩: gap=1.0.97, gap=1.5.94, gap=2.0.92
      return 1.0 - Math.log(gap + 1) * 0.04;
    }
    var xgGapModA = xgGapDiminishing(xgGapA_B);
    var xgGapModB = xgGapDiminishing(xgGapB_A);
    if (teamA) teamA.attack = teamA.attack * xgGapModA;
    if (teamB) teamB.attack = teamB.attack * xgGapModB;

    // ─── v6.3 OPT-5 [P2]: xG转化率修(xG Conversion Rate) ───
    // 问题: 高xG不等于高进球, 射正转化率低的球队进攻效率被高估
    // 方案: 用xGOT/attack比值作为转化率指标, 低转化率球队进攻下调
    var convRateA = teamA ? Math.max(0.3, Math.min((teamA.xGOT || 1.0) / Math.max(teamA.attack, 0.5), 0.9)) : 0.5;
    var convRateB = teamB ? Math.max(0.3, Math.min((teamB.xGOT || 1.0) / Math.max(teamB.attack, 0.5), 0.9)) : 0.5;
    // 转化0.45 进攻效率偏低, 下调attack
    // 转化0.65 进攻效率 微调attack
    var convAdjA = convRateA < 0.45 ? 1.0 - (0.45 - convRateA) * 0.3 : (convRateA > 0.65 ? 1.0 + (convRateA - 0.65) * 0.15 : 1.0);
    var convAdjB = convRateB < 0.45 ? 1.0 - (0.45 - convRateB) * 0.3 : (convRateB > 0.65 ? 1.0 + (convRateB - 0.65) * 0.15 : 1.0);
    convAdjA = Math.max(0.92, Math.min(1.05, convAdjA));
    convAdjB = Math.max(0.92, Math.min(1.05, convAdjB));
    if (teamA) teamA.attack = teamA.attack * convAdjA;
    if (teamB) teamB.attack = teamB.attack * convAdjB;

    // ─── v6.3 OPT-3 [P2]: 门将评估修正 (Goalkeeper Assessment) ───
    // 问题: 模型门将评估过于依赖FIFA排名, 未考虑门将个人能力
    // 方案: 基于阵容数据中的门将信息, 对防守端施加修正
    var gkAdjA = 1.0, gkAdjB = 1.0;
    // 从赛前存档中提取门将信息
    for (var _gkIdx = 0; _gkIdx < _pendingPreMatchArchive.length; _gkIdx++) {
      var _gkEntry = _pendingPreMatchArchive[_gkIdx];
      if ((_gkEntry.teamA === teamAKey && _gkEntry.teamB === teamBKey) ||
          (_gkEntry.teamA === teamBKey && _gkEntry.teamB === teamAKey)) {
        // 检查门将扑救数(来自历史复盘)
        if (_gkEntry.goalkeeperSaves) {
          var _gkSavesA = _gkEntry.teamA === teamAKey ? _gkEntry.goalkeeperSaves.teamA : _gkEntry.goalkeeperSaves.teamB;
          var _gkSavesB = _gkEntry.teamA === teamAKey ? _gkEntry.goalkeeperSaves.teamB : _gkEntry.goalkeeperSaves.teamA;
          // 扑救>=8门将状态极 防守+3%
          if (_gkSavesA >= 8) gkAdjA = 0.97;
          if (_gkSavesB >= 8) gkAdjB = 0.97;
          // 扑救<=2门将表现差或对手射正 防守-2%
          if (_gkSavesA !== undefined && _gkSavesA <= 2) gkAdjA = 1.02;
          if (_gkSavesB !== undefined && _gkSavesB <= 2) gkAdjB = 1.02;
        }
        // 检查门将是否缺
        if (_gkEntry.lineups) {
          var _gkMissingA = false, _gkMissingB = false;
          if (_gkEntry.lineups.teamA && _gkEntry.lineups.teamA.absent) {
            for (var _gkA = 0; _gkA < _gkEntry.lineups.teamA.absent.length; _gkA++) {
              if (_gkEntry.lineups.teamA.absent[_gkA].indexOf('') >= 0 ||
                  _gkEntry.lineups.teamA.absent[_gkA].indexOf('GK') >= 0) {
                _gkMissingA = true;
              }
            }
          }
          if (_gkEntry.lineups.teamB && _gkEntry.lineups.teamB.absent) {
            for (var _gkB = 0; _gkB < _gkEntry.lineups.teamB.absent.length; _gkB++) {
              if (_gkEntry.lineups.teamB.absent[_gkB].indexOf('') >= 0 ||
                  _gkEntry.lineups.teamB.absent[_gkB].indexOf('GK') >= 0) {
                _gkMissingB = true;
              }
            }
          }
          if (_gkMissingA) gkAdjA = 1.04; // 门将缺阵, 防守-4%
          if (_gkMissingB) gkAdjB = 1.04;
        }
        break;
      }
    }
    if (teamA) teamA.defence = teamA.defence * gkAdjA;
    if (teamB) teamB.defence = teamB.defence * gkAdjB;

    // ─── v6.3 OPT-1 [P1]: 铁桶阵动态权(Iron Bucket Formation) ───
    // 问题: 原来仅在情报文本中硬编码匹配"铁桶, 无动态阵型识
    // 方案: 从赛前存档中读取实际阵型, 5后卫/3后卫体系动态调
    var ironBucketAdjA = 1.0, ironBucketAdjB = 1.0;
    for (var _ibIdx = 0; _ibIdx < _pendingPreMatchArchive.length; _ibIdx++) {
      var _ibEntry = _pendingPreMatchArchive[_ibIdx];
      if ((_ibEntry.teamA === teamAKey && _ibEntry.teamB === teamBKey) ||
          (_ibEntry.teamA === teamBKey && _ibEntry.teamB === teamAKey)) {
        var _ibFormA = '', _ibFormB = '';
        if (_ibEntry.lineups) {
          _ibFormA = (_ibEntry.teamA === teamAKey ? _ibEntry.lineups.teamA : _ibEntry.lineups.teamB).formation || '';
          _ibFormB = (_ibEntry.teamA === teamAKey ? _ibEntry.lineups.teamB : _ibEntry.lineups.teamA).formation || '';
        }
        // 5后卫体系: 防守+5%, 进攻-3%
        var _ibIsDeepA = _ibFormA.indexOf('5-') === 0 || _ibFormA.indexOf('3-') === 0;
        var _ibIsDeepB = _ibFormB.indexOf('5-') === 0 || _ibFormB.indexOf('3-') === 0;
        if (_ibIsDeepA) { ironBucketAdjA = 0.95; teamA.defence = teamA.defence * 0.95; }
        if (_ibIsDeepB) { ironBucketAdjB = 0.95; teamB.defence = teamB.defence * 0.95; }
        // 铁桶阵遇上强中路: 额外防守加成
        var _oppTacticalA = teamB ? (teamB.tactical || '') : '';
        var _oppTacticalB = teamA ? (teamA.tactical || '') : '';
        if (_ibIsDeepA && (_oppTacticalB.indexOf('direct') >= 0 || _oppTacticalB.indexOf('counter') >= 0)) {
          teamA.defence = teamA.defence * 0.97; // 铁桶阵克制直反击
        }
        if (_ibIsDeepB && (_oppTacticalA.indexOf('direct') >= 0 || _oppTacticalA.indexOf('counter') >= 0)) {
          teamB.defence = teamB.defence * 0.97;
        }
        break;
      }
    }

    // ─── v6.3 OPT-6 [P2]: 新军首战修正 (Debut/Newcomer Bonus) ───
    // 问题: 赛事新军首战往往超预无包斗志旺盛), 但模型仅0.03
    
    var _wcHistoryA = COMPLETED_MATCH_ARCHIVE.filter(function(m) {
      return m.teamA === teamAKey || m.teamB === teamAKey;
    });
    var _wcHistoryB = COMPLETED_MATCH_ARCHIVE.filter(function(m) {
      return m.teamA === teamBKey || m.teamB === teamBKey;
    });
    if (_wcHistoryA.length === 0 && teamA) {
      // 新军: 防守韧5% (无经验但斗志, 进攻-2% (配合生疏)
      teamA.defence = teamA.defence * 0.95;
      teamA.attack = teamA.attack * 0.98;
    }
    if (_wcHistoryB.length === 0 && teamB) {
      teamB.defence = teamB.defence * 0.95;
      teamB.attack = teamB.attack * 0.98;
    }

    // ─── v6.3 OPT-4 [P2]: 核心球员降权 (Key Player Absence) ───
    // 问题: 原keyPlayer因子是静态 未考虑赛前实际阵容中的核心球员缺阵
    // 方案: 从赛前存档中检测核心球员缺阵情 动态调整keyPlayer因子
    var _kpAdjA = 1.0, _kpAdjB = 1.0;
    for (var _kpIdx = 0; _kpIdx < _pendingPreMatchArchive.length; _kpIdx++) {
      var _kpEntry = _pendingPreMatchArchive[_kpIdx];
      if ((_kpEntry.teamA === teamAKey && _kpEntry.teamB === teamBKey) ||
          (_kpEntry.teamA === teamBKey && _kpEntry.teamB === teamAKey)) {
        // 统计缺阵的关键球员数
        var _kpMissA = 0, _kpMissB = 0;
        if (_kpEntry.lineups) {
          var _kpLineA = _kpEntry.teamA === teamAKey ? _kpEntry.lineups.teamA : _kpEntry.lineups.teamB;
          var _kpLineB = _kpEntry.teamA === teamAKey ? _kpEntry.lineups.teamB : _kpEntry.lineups.teamA;
          if (_kpLineA.absent) _kpMissA = _kpLineA.absent.length;
          if (_kpLineB.absent) _kpMissB = _kpLineB.absent.length;
        }
        // 每缺1名关键球 keyPlayer因子下调3%
        _kpAdjA = Math.max(0.85, 1.0 - _kpMissA * 0.03);
        _kpAdjB = Math.max(0.85, 1.0 - _kpMissB * 0.03);
        break;
      }
    }
    if (teamA) teamA.keyPlayer = (teamA.keyPlayer || 1.0) * _kpAdjA;
    if (teamB) teamB.keyPlayer = (teamB.keyPlayer || 1.0) * _kpAdjB;

    // ─── v6.3 OPT-9 [P3]: 球星影响力修(Star Player Impact) ───
    // 问题: 关键球星(如梅西、姆巴佩)的个人能力可改变比赛走势, 但模型仅用静态keyPlayer
    // 方案: 当双方keyPlayer差距>0.15 对球星优势方给予额外进攻加成(上限+3%)
    var _starDiff = (teamA ? (teamA.keyPlayer || 1.0) : 1.0) - (teamB ? (teamB.keyPlayer || 1.0) : 1.0);
    if (Math.abs(_starDiff) > 0.15) {
      var _starBonus = Math.min(0.03, Math.abs(_starDiff) * 0.1);
      if (_starDiff > 0 && teamA) teamA.attack = teamA.attack * (1 + _starBonus);
      else if (teamB) teamB.attack = teamB.attack * (1 + _starBonus);
    }

    var poisson = predictMatch(teamAKey, teamBKey, options);
    var stacked = predictStacked(teamAKey, teamBKey, options);
    var lambdaA = poisson.lambdaA;
    var lambdaB = poisson.lambdaB;
    var rho = poisson.rho || 0.1;
    var maxGoals = 7;

    // ─── 1. 胜平负预───
    var wdw = {
      winA: stacked.winA,
      draw: stacked.draw,
      winB: stacked.winB,
      recommendation: stacked.winA > stacked.winB ?
        (stacked.winA > 0.5 ? '' : '') :
        (stacked.winB > 0.5 ? '' : (stacked.draw > 0.28 ? '' : ''))
    };

    // ─── 2. 比分预测 (TOP5) v4.2多模型融───
    // 融合泊松比分 + Stacking胜平负方+ 赔率市场信息
    var poissonScores = poisson.topScores.slice(0, 8);
    var scorePredictions = poissonScores.map(function(s) {
      var parts = s.score.split('-');
      var sA = parseInt(parts[0]), sB = parseInt(parts[1]);
      var baseProb = s.prob;
      // 方向修正: 如果比分方向与Stacking胜平负一 提升概率
      // v4.4 P2修复: 6/6模型一致时降低加成(8%), 4~5/6时正常加12%), 3/6以下不加
      // 原问 6/6一致时+15%加成过度偏置, 系统性压低冷门比
      var direction = sA > sB ? 'winA' : (sA === sB ? 'draw' : 'winB');
      var dirBoost = 1.0;
      // 计算6模型一致
      var _allM = [stacked.components.poisson, stacked.components.dixonCole, stacked.components.ssm,
                   stacked.components.xgboost, stacked.components.lightgbm, stacked.components.elo];
      var _topDir = stacked.winA > stacked.winB ? 'winA' : (stacked.draw > Math.max(stacked.winA, stacked.winB) ? 'draw' : 'winB');
      var _agreeCnt = 0;
      _allM.forEach(function(m) {
        var mDir = m.winA > m.winB ? 'winA' : (m.draw > Math.max(m.winA, m.winB) ? 'draw' : 'winB');
        if (mDir === _topDir) _agreeCnt++;
      });
      var boostRate = _agreeCnt >= 5 ? 1.08 : (_agreeCnt >= 4 ? 1.12 : 1.0); // 一致性越高加成越
      if (direction === 'winA' && stacked.winA > stacked.winB && stacked.winA > stacked.draw) dirBoost = boostRate;
      else if (direction === 'draw' && stacked.draw > Math.max(stacked.winA, stacked.winB)) dirBoost = boostRate;
      else if (direction === 'winB' && stacked.winB > stacked.winA && stacked.winB > stacked.draw) dirBoost = boostRate;
      // 赔率修正: 如果有赔 融合市场隐含概率
      if (options.odds && options.odds.winA) {
        var mktA = 1 / options.odds.winA;
        var mktD = 1 / options.odds.draw;
        var mktB = 1 / options.odds.winB;
        var mktDir = sA > sB ? 'winA' : (sA === sB ? 'draw' : 'winB');
        var mktProb = mktDir === 'winA' ? mktA : (mktDir === 'draw' ? mktD : mktB);
        var modelDir = direction === 'winA' ? stacked.winA : (direction === 'draw' ? stacked.draw : stacked.winB);
        // 模型与市场一致时额外加成
        if (mktDir === direction && Math.abs(mktProb - modelDir) < 0.15) dirBoost *= 1.08;
      }
      return {
        score: s.score,
        scoreA: sA,
        scoreB: sB,
        prob: baseProb * dirBoost,
        sources: { poisson: baseProb, directionBoost: dirBoost }
      };
    }).sort(function(a, b) { return b.prob - a.prob; }).slice(0, 5);
    // 归一
    var scoreTotal = scorePredictions.reduce(function(sum, s) { return sum + s.prob; }, 0);
    scorePredictions.forEach(function(s) { s.prob /= scoreTotal; });

    // ─── 3. 半全场胜平负预测 ───
    // 半场和全场分别独立建
    // 半场lambda约为全场5分钟/90分钟比例, 但方差不
    // 使用泊松独立性假 P(HT结果, FT结果) = P(HT) × P(FT|HT)
    // 简 半场lambda = 全场lambda × 0.45 (45/90分钟)
    
    // 上半场进球比例约42-48分钟/90分钟, 但存半场胶着效应"
    // 强队倾向于下半场发力, 弱队上半场更保守
    var eloDiff = (ELO_RATINGS[teamAKey] || 1800) - (ELO_RATINGS[teamBKey] || 1800);
    // Elo差距越大, 上半场进球比例越强队试探, 弱队龟缩)
    // v6.0: 半场进球比例 - 加入战术风格因素
    // 防守反击型球队上半场更保守，进球比例更低
    var aTactical = (TEAMS[teamAKey] || {}).tactical || 'balanced';
    var bTactical = (TEAMS[teamBKey] || {}).tactical || 'balanced';
    var tacticalHtPenalty = 0;
    if (aTactical === 'counter') tacticalHtPenalty -= 0.02;
    if (bTactical === 'counter') tacticalHtPenalty -= 0.02;
    if (aTactical === 'pressing' || bTactical === 'pressing') tacticalHtPenalty += 0.01;
    var htRatio = 0.45 - Math.abs(eloDiff) * 0.00005 + tacticalHtPenalty;
    htRatio = Math.max(0.36, Math.min(0.48, htRatio));
    var htLambdaA = lambdaA * htRatio;
    var htLambdaB = lambdaB * htRatio;

    // 计算半场胜平负概
    var htWinA = 0, htDraw = 0, htWinB = 0;
    for (var i = 0; i <= 5; i++) {
      for (var j = 0; j <= 5; j++) {
        var p = bivariatePoissonPMF(i, j, htLambdaA, htLambdaB, rho);
        if (i > j) htWinA += p;
        else if (i === j) htDraw += p;
        else htWinB += p;
      }
    }
    var htTotal = htWinA + htDraw + htWinB;
    htWinA /= htTotal; htDraw /= htTotal; htWinB /= htTotal;

    // 9种半全场组合概率
    // 原理: P(HT=X, FT=Y) P(HT=X) × P(FT=Y) × correlation_factor
    // correlation_factor基于: 如果半场领先,全场获胜概率更高
    // 使用条件概率修正:
    //   P(FT胜|HT > P(FT, P(FT胜|HT < P(FT
    var ftWinA = stacked.winA, ftDraw = stacked.draw, ftWinB = stacked.winB;

    // ─── v4.0 优化A: 数据驱动半场/全场转换概率 ───
    
    // 数据来源: 墨西-0南非(HT1-0→FT2-0), 韩国2-1捷克(HT0-0→FT2-1) 等实际观
    // 动态调 强队领先时守住优势概率更高，弱队落后时逆转概率更低
    var eloDiff = (ELO_RATINGS[teamAKey] || 1800) - (ELO_RATINGS[teamBKey] || 1800);
    var eloFactor = Math.tanh(eloDiff / 400); // [-1, 1], A更强

    
    // 半场领先全场结果
    var baseWinGivenLead  = 0.72 + 0.06 * eloFactor;  // 领先方Elo越高，守住概率越
    var baseDrawGivenLead = 0.18 - 0.03 * eloFactor;
    var baseLossGivenLead = 0.10 - 0.03 * eloFactor;

    // 半场落后全场结果
    var baseWinGivenTrail  = 0.08 - 0.03 * eloFactor; // A越强，B落后时逆转概率越低
    var baseDrawGivenTrail = 0.22 + 0.02 * eloFactor;
    var baseLossGivenTrail = 0.70 + 0.01 * eloFactor;

    // 半场全场结果 (受实力差影响最
    var baseWinGivenHTDraw  = 0.28 + 0.12 * eloFactor; // A越强，平局后取胜概率越
    var baseDrawGivenHTDraw = 0.40 - 0.04 * Math.abs(eloFactor);
    var baseLossGivenHTDraw = 0.32 - 0.08 * eloFactor;

    //  Clamp到合理区间并归一化每
    function norm3(a, b, c) {
      var s = a + b + c;
      return [Math.max(0.01, a/s), Math.max(0.01, b/s), Math.max(0.01, c/s)];
    }
    var lead = norm3(baseWinGivenLead, baseDrawGivenLead, baseLossGivenLead);
    var trail = norm3(baseWinGivenTrail, baseDrawGivenTrail, baseLossGivenTrail);
    var htdraw = norm3(baseWinGivenHTDraw, baseDrawGivenHTDraw, baseLossGivenHTDraw);

    var condWinGivenLead = lead[0];
    var condDrawGivenLead = lead[1];
    var condLossGivenLead = lead[2];
    var condWinGivenTrail = trail[0];
    var condDrawGivenTrail = trail[1];
    var condLossGivenTrail = trail[2];
    var condWinGivenHTDraw = htdraw[0];
    var condDrawGivenHTDraw = htdraw[1];
    var condLossGivenHTDraw = htdraw[2];

    var hfCombinations = [
      { ht: ', ft: ', prob: htWinA * condWinGivenLead },
      { ht: ', ft: ', prob: htWinA * condDrawGivenLead },
      { ht: ', ft: ', prob: htWinA * condLossGivenLead },
      { ht: ', ft: ', prob: htDraw * condWinGivenHTDraw },
      { ht: ', ft: ', prob: htDraw * condDrawGivenHTDraw },
      { ht: ', ft: ', prob: htDraw * condLossGivenHTDraw },
      { ht: ', ft: ', prob: htWinB * condWinGivenTrail },
      { ht: ', ft: ', prob: htWinB * condDrawGivenTrail },
      { ht: ', ft: ', prob: htWinB * condLossGivenTrail },
    ];

    // 归一
    var hfTotal = 0;
    for (var h = 0; h < hfCombinations.length; h++) hfTotal += hfCombinations[h].prob;
    for (var h = 0; h < hfCombinations.length; h++) hfCombinations[h].prob /= hfTotal;

    // 按概率排
    hfCombinations.sort(function(a, b) { return b.prob - a.prob; });

    var halfFull = {
      htProb: { winA: htWinA, draw: htDraw, winB: htWinB },
      combinations: hfCombinations,
      top3: hfCombinations.slice(0, 3),
      recommendation: hfCombinations[0].ht + '/' + hfCombinations[0].ft
    };

    // ─── 4. 总进球数预测 ───
    // 总进= teamA进球 + teamB进球, 服从泊松之和分布
    // P(total=k) = Σ_{i=0}^{k} P(A=i) × P(B=k-i)
    // v6.0: 比赛节奏调整系数
    // 基于12场复 小组赛首轮进球数波动大，需要考虑比赛节奏
    var paceAdjust = 1.0;
    // 弱队防守反击时比赛节奏偏进球
    var aTactical = (TEAMS[teamAKey] || {}).tactical || 'balanced';
    var bTactical = (TEAMS[teamBKey] || {}).tactical || 'balanced';
    if (aTactical === 'counter' || bTactical === 'counter') paceAdjust *= 0.92;
    // 双方都进攻时节奏偏快(进球
    if ((aTactical === 'possession' || aTactical === 'pressing') &&
        (bTactical === 'possession' || bTactical === 'pressing')) paceAdjust *= 1.08;
    // 强弱悬殊时弱队龟缩，但强队效率高，净效果不确
    var eloGap = Math.abs((ELO_RATINGS[teamAKey] || 1800) - (ELO_RATINGS[teamBKey] || 1800));
    if (eloGap > 400) paceAdjust *= 1.05; // 大差距时强队更容易打出大比分
    var totalGoalsProb = [];
    for (var k = 0; k <= 9; k++) {
      var pTotal = 0;
      for (var i = 0; i <= k; i++) {
        var j = k - i;
        if (j >= 0 && j <= maxGoals) {
          pTotal += bivariatePoissonPMF(i, j, lambdaA * paceAdjust, lambdaB * paceAdjust, rho);
        }
      }
      totalGoalsProb.push({ goals: k, prob: pTotal });
    }

    // 归一
    var tgTotal = 0;
    for (var t = 0; t < totalGoalsProb.length; t++) tgTotal += totalGoalsProb[t].prob;
    for (var t = 0; t < totalGoalsProb.length; t++) totalGoalsProb[t].prob /= tgTotal;

    // 大小(2.5球线)
    var over25 = 0, under25 = 0;
    for (var t = 0; t < totalGoalsProb.length; t++) {
      if (totalGoalsProb[t].goals > 2) over25 += totalGoalsProb[t].prob;
      else under25 += totalGoalsProb[t].prob;
    }

    // 大小(3.5球线)
    var over35 = 0, under35 = 0;
    for (var t = 0; t < totalGoalsProb.length; t++) {
      if (totalGoalsProb[t].goals > 3) over35 += totalGoalsProb[t].prob;
      else under35 += totalGoalsProb[t].prob;
    }

    // 最可能总进球数
    var topGoalCounts = totalGoalsProb.slice().sort(function(a,b){return b.prob-a.prob;}).slice(0,3);

    var totalGoals = {
      distribution: totalGoalsProb,
      expected: (lambdaA + lambdaB) * paceAdjust,
      over25: over25,
      under25: under25,
      over35: over35,
      under35: under35,
      topCounts: topGoalCounts,
      recommendation: over25 > under25 ? '.5 : '.5
    };

    // v4.3: 残差修正 + 卡尔曼滤波市场融
    var residualCorrected = applyResidualCorrection(teamAKey, teamBKey, options, wdw);
    wdw.winA = residualCorrected.winA;
    wdw.draw = residualCorrected.draw;
    wdw.winB = residualCorrected.winB;

    // ─── v4.4 P1优化: 动态平局概率下限 ───
    // 问题: 模型系统性低估平局(加拿大VS波黑3.5%, 实际平局)
    // 方案: 基于双方历史平局率和Elo差距, 设定动态平局下限
    // Elo差距越小 平局概率越高 下限越高
    var eloDiff = Math.abs((ELO_RATINGS[teamAKey] || 1800) - (ELO_RATINGS[teamBKey] || 1800));
    // 基础平局概率: Elo6%, 009%, 003%, 00%
    var baseDrawProb = 0.26 * Math.exp(-eloDiff / 600);
    // 动态下 取基础平局概率0%作为下限, 最5%, 最5%
    var dynamicDrawFloor = Math.max(0.15, Math.min(0.25, baseDrawProb * 0.6));
    if (wdw.draw < dynamicDrawFloor) {
      var drawDeficit = dynamicDrawFloor - wdw.draw;
      // 从胜率较高的一方扣除缺
      if (wdw.winA >= wdw.winB) {
        wdw.winA -= drawDeficit;
      } else {
        wdw.winB -= drawDeficit;
      }
      wdw.draw = dynamicDrawFloor;
      // 归一
      var _renorm = wdw.winA + wdw.draw + wdw.winB;
      wdw.winA /= _renorm; wdw.draw /= _renorm; wdw.winB /= _renorm;
    }

    // ─── v4.6: 五层防冷机制 (Anti-Upset System) ───
    // 基于5场复加拿-1波黑、卡塔尔1-1瑞士等冷提炼的冷门特征模
    // 在最终胜平负输出前应 对检测到的冷门风险进行概率修
    var upsetDetection = detectUpsetRisk(teamAKey, teamBKey, options, stacked, wdw);
    if (upsetDetection.upsetRisk >= 0.15) { // v6.3: 阈值从0.30降至0.15, 提高防冷灵敏
      // 应用防冷修正
      var _uc = upsetDetection.correction;
      wdw.winA += _uc.winADelta;
      wdw.draw += _uc.drawDelta;
      wdw.winB += _uc.winBDelta;
      var _uren = wdw.winA + wdw.draw + wdw.winB;
      wdw.winA /= _uren; wdw.draw /= _uren; wdw.winB /= _uren;
      // 同步修正比分预测: 提升平局和弱势方比分概率
      scorePredictions.forEach(function(sp) {
        if (sp.scoreA === sp.scoreB) sp.prob *= (1 + _uc.drawDelta * 2);
        else if ((sp.scoreA > sp.scoreB && _uc.winBDelta > 0) || (sp.scoreB > sp.scoreA && _uc.winADelta > 0)) {
          sp.prob *= (1 + Math.max(_uc.winADelta, _uc.winBDelta) * 1.5);
        }
      });
      var _sren = scorePredictions.reduce(function(s,sp){return s+sp.prob;},0);
      scorePredictions.forEach(function(sp){sp.prob /= _sren;});
    }

    // ─── v4.4.1 残阵对决检───
    // 问题: 加拿大VS波黑双方合计5名主力缺戴维邦比弗洛雷斯 vs 哲科+塔巴科维
    //       残阵对决特征: 进攻效率下降, 比赛趋向保守/平局
    // 方案: 检测双方伤病严重度, 自动提升平局概率+3~5%
    var teamAData = TEAMS[teamAKey] || {};
    var teamBData = TEAMS[teamBKey] || {};
    var injuryA = teamAData.injury || 1.0;
    var injuryB = teamBData.injury || 1.0;
    var weakenedCount = 0;
    if (injuryA < 0.95) weakenedCount++;
    if (injuryB < 0.95) weakenedCount++;
    // 从赛前存档中查找伤病人数 (_pendingPreMatchArchive是数
    for (var _wIdx = 0; _wIdx < _pendingPreMatchArchive.length; _wIdx++) {
      var _wEntry = _pendingPreMatchArchive[_wIdx];
      if ((_wEntry.teamA === teamAKey && _wEntry.teamB === teamBKey) ||
          (_wEntry.teamA === teamBKey && _wEntry.teamB === teamAKey)) {
        if (_wEntry.injuries) {
          var _injA = 0, _injB = 0;
          if (Array.isArray(_wEntry.injuries.teamA)) _injA = _wEntry.injuries.teamA.length;
          else if (Array.isArray(_wEntry.injuries)) _injA = _wEntry.injuries.length;
          if (Array.isArray(_wEntry.injuries.teamB)) _injB = _wEntry.injuries.teamB.length;
          // 每方名伤该方算残
          if (_injA >= 2) weakenedCount++;
          if (_injB >= 2) weakenedCount++;
        }
        break;
      }
    }
    // 双方均残weakenedCount >= 2) 提升平局概率
    if (weakenedCount >= 2) {
      var drawBoost = weakenedCount >= 3 ? 0.04 : 0.03;
      wdw.draw += drawBoost;
      if (wdw.winA >= wdw.winB) { wdw.winA -= drawBoost; }
      else { wdw.winB -= drawBoost; }
      var _renorm2 = wdw.winA + wdw.draw + wdw.winB;
      wdw.winA /= _renorm2; wdw.draw /= _renorm2; wdw.winB /= _renorm2;
    }

    if (options.odds && options.odds.winA && options.odds.draw && options.odds.winB) {
      // 计算模型方差 (6模型分歧
      var _c = stacked.components;
      var _probs = [_c.poisson.winA, _c.dixonCole.winA, _c.ssm.winA, _c.xgboost.winA, _c.lightgbm.winA, _c.elo.winA];
      var _mean = _probs.reduce(function(s,p){return s+p;},0) / _probs.length;
      var _var = _probs.reduce(function(s,p){return s + (p-_mean)*(p-_mean);},0) / _probs.length;
      var kalman = kalmanFilterCalibration(wdw, options.odds, _var);
      wdw.winA = kalman.winA;
      wdw.draw = kalman.draw;
      wdw.winB = kalman.winB;
      wdw.kalmanGain = kalman.kalmanGain;
      wdw.recommendation = wdw.winA > wdw.winB ?
        (wdw.winA > 0.5 ? '' : '') :
        (wdw.winB > 0.5 ? '' : (wdw.draw > 0.28 ? '' : ''));
    }

    // ─── v6.3 OPT-8 [P0]: 让球反向信号 (Handicap Reversal Signal) ───
    // 问题: 当让球盘口与模型预测方向矛盾 往往暗示市场知道模型不知道的信息
    // 案例: 模型预测A队胜5%, 但让球盘A0.5降盘0.25, 说明机构在防范A队不
    // 方案: 检测让球盘口与模型预测的背 触发概率修正
    var handicapSignal = null;
    if (options.oddsTrajectory && options.oddsTrajectory.handicap && options.oddsTrajectory.handicap.length >= 2) {
      var _hcNodes = options.oddsTrajectory.handicap;
      var _hcFirst = _hcNodes[0], _hcLast = _hcNodes[_hcNodes.length - 1];
      // 解析让球盘口变化: 检测热门方让球是否降盘
      var _modelFav = wdw.winA > wdw.winB ? 'A' : 'B';
      var _modelFavProb = Math.max(wdw.winA, wdw.winB);
      // 让球盘口解析 (简 从handicap line文本中提取数
      function parseHandicapLine(line) {
        if (!line) return 0;
        var match = line.match(/-?(\d+\.?\d*)/);
        return match ? parseFloat(match[1]) : 0;
      }
      var _hcLineFirst = parseHandicapLine(_hcFirst.line);
      var _hcLineLast = parseHandicapLine(_hcLast.line);
      // 判断让球方向: 负主队让球, 正客队让球
      var _hcFavIsA = _hcLineFirst <= 0; // 主队让球
      var _hcDrop = _hcFavIsA ?
        (_hcLineLast > _hcLineFirst) : // 主队让球减少(降盘)
        (_hcLineLast < _hcLineFirst);   // 客队让球减少(降盘)
      // 让球降盘 + 模型高置信看好热反向信号
      if (_hcDrop && _modelFavProb > 0.50) {
        var _dropMagnitude = Math.abs(_hcLineLast - _hcLineFirst);
        if (_dropMagnitude >= 0.25) {
          // 让球降盘幅度>=0.25强反向信
          var _hcCorrection = Math.min(0.05, _dropMagnitude * 0.08);
          // 从热门方扣除, 分配给平局和弱势方
          if (_modelFav === 'A') {
            wdw.winA -= _hcCorrection;
            wdw.draw += _hcCorrection * 0.6;
            wdw.winB += _hcCorrection * 0.4;
          } else {
            wdw.winB -= _hcCorrection;
            wdw.draw += _hcCorrection * 0.6;
            wdw.winA += _hcCorrection * 0.4;
          }
          var _hcRenorm = wdw.winA + wdw.draw + wdw.winB;
          wdw.winA /= _hcRenorm; wdw.draw /= _hcRenorm; wdw.winB /= _hcRenorm;
          handicapSignal = {
            triggered: true,
            direction: _modelFav === 'A' ? '' : '',
            dropMagnitude: _dropMagnitude,
            correction: _hcCorrection,
            msg: '' + _dropMagnitude.toFixed(2) + ''%''winA' : 'winB';      if (_bigger === 'winA''-''-');            var sA = parseInt(parts[0]), sB = parseInt(parts[1]);            var baseProb = s.prob;            var direction = sA > sB ? 'winA' : (sA === sB ? 'draw' : 'winB');            var dirBoost = 1.0;            if (direction === 'winA' && wdw.winA > wdw.winB && wdw.winA > wdw.draw) dirBoost = 1.05;            else if (direction === 'draw' && wdw.draw > Math.max(wdw.winA, wdw.winB)) dirBoost = 1.05;            else if (direction === 'winB''.5 : '' : '') :            (wdw.winB > 0.5 ? '' : (wdw.draw > 0.28 ? '' : ''winA' : (wdw.draw > Math.max(wdw.winA, wdw.winB) ? 'draw' : 'winB');    allModels.forEach(function(m) {      var mPred = m.winA > m.winB ? 'winA' : (m.draw > Math.max(m.winA, m.winB) ? 'draw' : 'winB'' : (agreeCount >= 4 ? '' : (agreeCount >= 3 ? ' : ')),      agreeCount: agreeCount + '/6',      score: agreeCount / 6,      top3Scores: scorePredictions.slice(0, 3).map(function(s) { return s.score + '(' + (s.prob*100).toFixed(1) + '%'); }).join(' / ')    };    var result = {      match: (TEAMS[teamAKey] ? TEAMS[teamAKey].name : teamAKey) + ' vs ' + (TEAMS[teamBKey] ? TEAMS[teamBKey].name : teamBKey),      venue: options.venue || 'sea_level',      timestamp: new Date().toISOString(),      model: 'v4.7-wc2026-expanded''sea_level'',      home: {        name: a.name, rank: a.rank, elo: eloA,        xG: a.attack, xGA: a.xGA, xPTS: a.xPTS,        marketValue: a.marketValue, cohesion: a.cohesion      },      away: {        name: b.name, rank: b.rank, elo: eloB,        xG: b.attack, xGA: b.xGA, xPTS: b.xPTS,        marketValue: b.marketValue, cohesion: b.cohesion      },      analysis: '',      verdict: ''Elo(' + Math.abs(eloDiff).toFixed(0) + ', ';      layer1.verdict = eloDiff > 0 ? a.name : b.name;    } else if (Math.abs(eloDiff) > 80) {      layer1.analysis = 'Elo(' + Math.abs(eloDiff).toFixed(0) + ', ' + (eloDiff > 0 ? a.name : b.name) + '';      layer1.verdict = '';    } else {      layer1.analysis = 'Elo(' + Math.abs(eloDiff).toFixed(0) + ', ';      layer1.verdict = '',      home: {        form: a.recentForm, formFactor: formA,        fatigue: fatigueA, injury: a.injury      },      away: {        form: b.recentForm, formFactor: formB,        fatigue: fatigueB, injury: b.injury      },      analysis: '',      verdict: ''    };    var formDiff = (a.recentForm || 0) - (b.recentForm || 0);    if (Math.abs(formDiff) > 0.08) {      layer2.analysis = (formDiff > 0 ? a.name : b.name) + 'form' + (formDiff > 0 ? '+' : '') + (formDiff*100).toFixed(0) + '%');      layer2.verdict = formDiff > 0 ? a.name : b.name;    } else {      layer2.analysis = 'form' + (formDiff > 0 ? '+' : '') + (formDiff*100).toFixed(0) + '%');      layer2.verdict = '';    }    if (fatigueA < 0.96 || fatigueB < 0.96) {      var tiredTeam = fatigueA < fatigueB ? a.name : b.name;      layer2.analysis += ' + tiredTeam + '' + Math.min(fatigueA,fatigueB).toFixed(3) + ')'',pressing:'',counter:'',direct:'',aerial:'',balanced:''};    var layer3 = {      title: '',      home: {        tactical: a.tactical, tacticalName: tacticalNames[a.tactical] || a.tactical,        tempo: a.tempo, pressIntensity: a.pressIntensity,        attackSide: a.attackSide      },      away: {        tactical: b.tactical, tacticalName: tacticalNames[b.tactical] || b.tactical,        tempo: b.tempo, pressIntensity: b.pressIntensity,        attackSide: b.attackSide      },      routeMatchup: route,      analysis: '',      verdict: ''    };    if (Math.abs(route.adjA) > 0.015) {      var advTeam = route.adjA > 0 ? a.name : b.name;      layer3.analysis = advTeam + '(:' + (route.adjA > 0 ? '+' : '') + (route.adjA*100).toFixed(1) + '%');      layer3.verdict = route.adjA > 0 ? a.name : b.name;    } else {      layer3.analysis = '';      layer3.verdict = '' + (tempoDiff > 0 ? a.name + ' : b.name + '',      venue: venue,      altitude: venueData.altitude,      weather: weatherData,      homeAdv: options.neutral ? false : true,      psychA: a.psychFactor || 1.0,      psychB: b.psychFactor || 1.0,      analysis: '',      verdict: ''    };    var envFactors = [];    if (venueData.altitude > 1000) envFactors.push('' + venueData.altitude + 'm)');    if (weatherData.rain > 0.5) envFactors.push('' + (weatherData.rain*100).toFixed(0) + '%');    if (weatherData.wind > 15) envFactors.push('');    if (!options.neutral) envFactors.push(a.name + '');    if ((a.psychFactor || 1.0) < 0.97 || (b.psychFactor || 1.0) < 0.97) {      var weakPsych = (a.psychFactor || 1.0) < (b.psychFactor || 1.0) ? a.name : b.name;      envFactors.push(weakPsych + '');    }    layer4.analysis = envFactors.length > 0 ? envFactors.join(') + ' : '';    layer4.verdict = envFactors.length > 1 ? '' : (envFactors.length === 1 ? '' : '',      stacked: { winA: stacked.winA, draw: stacked.draw, winB: stacked.winB },      components: stacked.components,      model: stacked.model,      analysis: '',      verdict: ''5' + (modelAgreement*100).toFixed(1) + '%), ';      layer5.verdict = '';    } else if (modelAgreement < 0.20) {      layer5.analysis = '' + (modelAgreement*100).toFixed(1) + '%), ';      layer5.verdict = '';    } else {      layer5.analysis = '' + (modelAgreement*100).toFixed(1) + '%), ';      layer5.verdict = '';    }    return {      match: a.name + ' vs ''high' : (agreement < 0.20 ? 'medium' : 'low'',        description: ' + origInjA.toFixed(2) + '',        impact: {          winA: (injA.winA - baseline.winA) * 100,          draw: (injA.draw - baseline.draw) * 100,          winB: (injA.winB - baseline.winB) * 100        },        severity: Math.abs(injA.winA - baseline.winA) > 0.05 ? 'high' : (Math.abs(injA.winA - baseline.winA) > 0.02 ? 'medium' : 'low'',        description: ' + origInjB.toFixed(2) + '',        impact: {          winA: (injB.winA - baseline.winA) * 100,          draw: (injB.draw - baseline.draw) * 100,          winB: (injB.winB - baseline.winB) * 100        },        severity: Math.abs(injB.winB - baseline.winB) > 0.05 ? 'high' : (Math.abs(injB.winB - baseline.winB) > 0.02 ? 'medium' : 'low''possession','pressing','counter','direct','aerial','balanced'];    var bestTactical = null, bestTacticalAdv = 0;    for (var i = 0; i < tacticalOptions.length; i++) {      var origT = a.tactical;      a.tactical = tacticalOptions[i];      var tResult = predictStacked(teamAKey, teamBKey, options);      a.tactical = origT;      if (tResult.winA > bestTacticalAdv) {        bestTacticalAdv = tResult.winA;        bestTactical = tacticalOptions[i];      }    }    if (bestTactical !== a.tactical) {      var origTac = a.tactical;      a.tactical = bestTactical;      var tacResult = predictStacked(teamAKey, teamBKey, options);      a.tactical = origTac;      var tacNames = {possession:'',pressing:'',counter:'',direct:'',aerial:'',balanced:''};      scenarios.push({        name: a.name + '',        description: ' + (tacNames[a.tactical]||a.tactical) + ''high' : (Math.abs(tacResult.winA - baseline.winA) > 0.02 ? 'medium' : 'low''sea_level';    if (origVenue !== 'sea_level') {      var wOrig = WEATHER[origVenue];      if (wOrig) {        var origRain = wOrig.rain;        wOrig.rain = Math.min(1.0, origRain + 0.3);        var wetResult = predictStacked(teamAKey, teamBKey, options);        wOrig.rain = origRain;        scenarios.push({          name: '',          description: ' + (origRain*100).toFixed(0) + '' + (Math.min(1.0,origRain+0.3)*100).toFixed(0) + '%'',          impact: {            winA: (wetResult.winA - baseline.winA) * 100,            draw: (wetResult.draw - baseline.draw) * 100,            winB: (wetResult.winB - baseline.winB) * 100          },          severity: 'low'',      description: '0.3',      impact: {        winA: (fatResult.winA - baseline.winA) * 100,        draw: (fatResult.draw - baseline.draw) * 100,        winB: (fatResult.winB - baseline.winB) * 100      },      severity: 'low''high';}) ? 'high' :                (scenarios.some(function(s){return s.severity==='medium';}) ? 'medium' : 'low''wc2026_predictions';  var BACKUP_KEY = 'wc2026_backup''undefined') {        console.log('Storage: localStorage not available (Node.js environment)''Storage initialization failed:''Save odds trajectory failed:''Load odds trajectory failed:''Save data failed:''Merge update failed:''no data' };            var parsed = JSON.parse(data);      if (!parsed.predictionLog) return { valid: false, reason: 'missing predictionLog' };      if (!parsed.matchHistory) return { valid: false, reason: 'missing matchHistory' };      if (!parsed.completedArchive) return { valid: false, reason: 'missing completedArchive' };            return { valid: true, reason: 'ok' };    } catch (e) {      return { valid: false, reason: 'parse error: ''Clear storage failed:''v7.1: Real-time stats applied to all teams successfully');  } catch (e) {    console.warn('v7.1: Failed to apply real-time stats:''correct',    INCORRECT: 'incorrect',    PARTIAL: 'partial',    UNVERIFIED: 'unverified''winA' :                      (prediction.winB > prediction.winA ? 'winB' : 'draw');        var actualResult = actual.goalsA > actual.goalsB ? 'winA' :                       (actual.goalsB > actual.goalsA ? 'winB' : 'draw');        return {      type: 'winDrawWin''-' + actual.goalsB;    var topScores = prediction.slice ? prediction.slice(0, 5) : [];        var exactMatch = topScores.find(function(s) {      return s.score === actualScore;    });        var goalDiffMatch = topScores.find(function(s) {      var parts = s.score.split('-');      var predA = parseInt(parts[0]), predB = parseInt(parts[1]);      return (predA - predB) === (actual.goalsA - actual.goalsB);    });        return {      type: 'correctScore',      predicted: topScores.map(function(s) { return s.score + '(' + (s.prob * 100).toFixed(1) + '%'); }).join(', ''H' :                     (actual.halfTimeB > actual.halfTimeA ? 'A' : 'D');    var actualFull = actual.goalsA > actual.goalsB ? 'H' :                     (actual.goalsB > actual.goalsA ? 'A' : 'D''HH'', ft: '': 'H', ': 'D', ': 'A' }''(' + (hf.prob * 100).toFixed(1) + '%');    }).join(', ');        return {      type: 'halfTimeFullTime',      predicted: predictedStr || 'N/A''over25' : 'under25';    var actualResult = over25 ? 'over25' : (under25 ? 'under25' : 'exact25');        return {      type: 'totalGoals''penaltyShootout',        predicted: null,        actual: 'not_applicable',        correct: null,        status: REVIEW_STATUS.UNVERIFIED,        reason: 'Match did not go to penalties'      };    }        var predWinner = prediction.teamA > prediction.teamB ? 'teamA' : 'teamB';    var actualWinner = actual.penaltyWinner === 'teamA' || actual.penaltyWinner === 'teamAKey' ? 'teamA' : 'teamB';        return {      type: 'penaltyShootout''handicap',        predicted: null,        actual: null,        correct: null,        status: REVIEW_STATUS.UNVERIFIED,        reason: 'No handicap prediction'      };    }        var hc = prediction.handicap;    var line = hc.line || 0;        var adjustedA = actual.goalsA - line;    var adjustedB = actual.goalsB;        var predResult = hc.winA > hc.winB ? 'winA' :                      (hc.winB > hc.winA ? 'winB' : 'draw');        var actualResult = adjustedA > adjustedB ? 'winA' :                       (adjustedB > adjustedA ? 'winB' : 'draw');        return {      type: 'handicap'') > -1) {      _adjA *= 0.92; _adjD *= 1.20; _adjB *= 1.15;      var _adjT = _adjA + _adjD + _adjB;      _adjA /= _adjT; _adjD /= _adjT; _adjB /= _adjT;    }    _lastEntry.adjustedPredictions = {      winA: _adjA, draw: _adjD, winB: _adjB,      note: '(//''\n');    var result = {      european: [],      handicap: [],      correctScore: [],      totalGoals: [],      halfTimeFullTime: []    };    var currentSection = null;    var currentTimestamp = null;    for (var i = 0; i < lines.length; i++) {      var line = lines[i].trim();      if (!line || line.startsWith('#'') === 0 && line.indexOf('') > -1) { currentSection = 'halfTimeFullTime'; continue; }      if (line.indexOf('') === 0 && line.indexOf('0) > -1) { currentSection = 'totalGoals'; continue; }
      if (line.indexOf('') === 0 && line.indexOf('') > -1) { currentSection = 'correctScore'; continue; }
      if (line.indexOf('') === 0 && line.indexOf('') > -1) { currentSection = 'handicap'; continue; }
      if (line.indexOf('') === 0 && line.indexOf(') > -1 && line.indexOf(') > -1 && line.indexOf(') > -1) { currentSection = 'european'; continue; }      var parts = line.split(',');      if (parts.length < 2) continue;      if (currentSection === 'european' && parts.length >= 4) {        result.european.push({ time: parts[0].trim(), win: parseFloat(parts[1]), draw: parseFloat(parts[2]), lose: parseFloat(parts[3]) });      } else if (currentSection === 'handicap' && parts.length >= 4) {        result.handicap.push({ time: parts[0].trim(), win: parseFloat(parts[1]), draw: parseFloat(parts[2]), lose: parseFloat(parts[3]) });      } else if (currentSection === 'correctScore' && parts.length >= 3) {        var csTime = currentTimestamp || parts[0].trim();        var csEntry = null;        for (var c = 0; c < result.correctScore.length; c++) { if (result.correctScore[c].time === csTime) { csEntry = result.correctScore[c]; break; } }        if (!csEntry) { csEntry = { time: csTime, outcomes: [] }; result.correctScore.push(csEntry); }        csEntry.outcomes.push({ type: '', score: parts[1].trim(), odds: parseFloat(parts[2]) });      } else if (currentSection === 'totalGoals' && parts.length >= 3) {        result.totalGoals.push({          time: parts[0].trim(),          goals: [            {n:0,odds:parseFloat(parts[1])},{n:1,odds:parseFloat(parts[2])},{n:2,odds:parseFloat(parts[3])},            {n:3,odds:parseFloat(parts[4])},{n:4,odds:parseFloat(parts[5])},{n:5,odds:parseFloat(parts[6])},            {n:6,odds:parseFloat(parts[7])},{n:7,odds:parseFloat(parts[8])||32}          ]        });      } else if (currentSection === 'halfTimeFullTime' && parts.length >= 10) {        result.halfTimeFullTime.push({          time: parts[0].trim(),          outcomes: [            {combo:'',odds:parseFloat(parts[1])},{combo:'',odds:parseFloat(parts[2])},{combo:'',odds:parseFloat(parts[3])},            {combo:'',odds:parseFloat(parts[4])},{combo:'',odds:parseFloat(parts[5])},{combo:'',odds:parseFloat(parts[6])},            {combo:'',odds:parseFloat(parts[7])},{combo:'',odds:parseFloat(parts[8])},{combo:''(2'';      if (val < -0.005) return '';      return '';    }    function oddsDriftLabel(oddsOpen, oddsClose) {      var diff = oddsClose - oddsOpen;      if (diff > 0.05) return '';      if (diff < -0.05) return '';      return '';    }    function findOutcome(arr, key, keyField) {      for (var i = 0; i < arr.length; i++) {        if (arr[i][keyField || 'score''score''n');        if (gL) {          goalsDrift[gn + '] = {
            openOdds: tgF.goals[g].odds,
            closeOdds: gL.odds,
            drift: gL.odds - tgF.goals[g].odds,
            impliedDrift: (1 / gL.odds - 1 / tgF.goals[g].odds) * 100,
            label: oddsDriftLabel(tgF.goals[g].odds, gL.odds)
          };
        }
      }
    }

    // 5) 半全场胜平负维度 - 全部分析
    var htftDrift = {};
    if (htft.length >= 2) {
      var hfF = htft[0], hfL = htft[htft.length - 1];
      for (var h = 0; h < hfF.outcomes.length; h++) {
        var combo = hfF.outcomes[h].combo;
        var hL = findOutcome(hfL.outcomes, combo, 'combo');
        if (hL) {
          htftDrift[combo] = {
            openOdds: hfF.outcomes[h].odds,
            closeOdds: hL.odds,
            drift: hL.odds - hfF.outcomes[h].odds,
            impliedDrift: (1 / hL.odds - 1 / hfF.outcomes[h].odds) * 100,
            label: oddsDriftLabel(hfF.outcomes[h].odds, hL.odds)
          };
        }
      }
    }

    // ════════════════════════════════════════════
    // 第二 五维升降汇(driftSummary)
    // ════════════════════════════════════════════
    var driftSummary = {
      european: {
        win: driftLabel(euDrift.win),
        draw: driftLabel(euDrift.draw),
        lose: driftLabel(euDrift.lose),
        detail: { win: (euDrift.win * 100).toFixed(1) + '%', draw: (euDrift.draw * 100).toFixed(1) + '%', lose: (euDrift.lose * 100).toFixed(1) + '%' }
      },
      handicap: {
        win: driftLabel(hcDrift.win),
        draw: driftLabel(hcDrift.draw),
        lose: driftLabel(hcDrift.lose),
        detail: { win: (hcDrift.win * 100).toFixed(1) + '%', draw: (hcDrift.draw * 100).toFixed(1) + '%', lose: (hcDrift.lose * 100).toFixed(1) + '%' }
      },
      keyScores: {},
      keyGoals: {},
      keyHtft: {}
    };
    // 比分关键(取变化最大的
    var scoreEntries = Object.keys(scoreDrift).map(function(k) { return { key: k, v: scoreDrift[k] }; });
    scoreEntries.sort(function(a, b) { return Math.abs(b.v.impliedDrift) - Math.abs(a.v.impliedDrift); });
    for (var si = 0; si < Math.min(5, scoreEntries.length); si++) {
      driftSummary.keyScores[scoreEntries[si].key] = scoreEntries[si].v.label + '(' + scoreEntries[si].v.impliedDrift.toFixed(1) + '%');
    }
    // 进球数关键项
    var goalsEntries = Object.keys(goalsDrift).map(function(k) { return { key: k, v: goalsDrift[k] }; });
    goalsEntries.sort(function(a, b) { return Math.abs(b.v.impliedDrift) - Math.abs(a.v.impliedDrift); });
    for (var gi = 0; gi < Math.min(5, goalsEntries.length); gi++) {
      driftSummary.keyGoals[goalsEntries[gi].key] = goalsEntries[gi].v.label + '(' + goalsEntries[gi].v.impliedDrift.toFixed(1) + '%');
    }
    // 半全场关键项
    var htftEntries = Object.keys(htftDrift).map(function(k) { return { key: k, v: htftDrift[k] }; });
    htftEntries.sort(function(a, b) { return Math.abs(b.v.impliedDrift) - Math.abs(a.v.impliedDrift); });
    for (var hi = 0; hi < Math.min(5, htftEntries.length); hi++) {
      driftSummary.keyHtft[htftEntries[hi].key] = htftEntries[hi].v.label + '(' + htftEntries[hi].v.impliedDrift.toFixed(1) + '%');
    }

    // ════════════════════════════════════════════
    // 第三 单维信号 (singleSignals)
    // ════════════════════════════════════════════
    var singleSignals = [];

    // 胜平负信
    if (euDrift.win < -0.03) singleSignals.push({ dim: ''(' + (euDrift.win * 100).toFixed(1) + '%), ', dir: 'down', strength: 'strong' });
    else if (euDrift.win < -0.015) singleSignals.push({ dim: '' + (euDrift.win * 100).toFixed(1) + '%), dir: 'down', strength: 'medium' });
    else if (euDrift.win > 0.03) singleSignals.push({ dim: ''(' + (euDrift.win * 100).toFixed(1) + '%), ', dir: 'up', strength: 'strong' });
    else if (euDrift.win > 0.015) singleSignals.push({ dim: '' + (euDrift.win * 100).toFixed(1) + '%), dir: 'up', strength: 'medium' });

    if (euDrift.draw > 0.02) singleSignals.push({ dim: ''(' + (euDrift.draw * 100).toFixed(1) + '%), ', dir: 'up', strength: 'medium' });
    if (euDrift.lose > 0.03) singleSignals.push({ dim: '' + (euDrift.lose * 100).toFixed(1) + '', dir: 'up', strength: 'strong' });
    else if (euDrift.lose > 0.015) singleSignals.push({ dim: ''(' + (euDrift.lose * 100).toFixed(1) + '%), dir: 'up', strength: 'medium'', msg: '' + (hcDrift.lose * 100).toFixed(1) + ''up', strength: 'strong' });
    if (hcDrift.win > 0.03) singleSignals.push({ dim: '', msg: '' + (hcDrift.win * 100).toFixed(1) + ''up', strength: 'medium''1:1'] && scoreDrift['1:1'].impliedDrift > 0.5) singleSignals.push({ dim: '', msg: '' + scoreDrift['1:1'].impliedDrift.toFixed(1) + '', dir: 'down', strength: 'medium' });
    if (scoreDrift['0:0'] && scoreDrift['0:0'].impliedDrift > 0.5) singleSignals.push({ dim: '', msg: ''down', strength: 'medium' });
    if (scoreDrift['2:0'] && scoreDrift['2:0'].impliedDrift > 0.5) singleSignals.push({ dim: '', msg: '', dir: 'down', strength: 'medium' });
    if (scoreDrift['0:1'] && scoreDrift['0:1'].impliedDrift > 0.5) singleSignals.push({ dim: '', msg: '', dir: 'down', strength: 'medium''0] && goalsDrift['0].impliedDrift > 0.5) singleSignals.push({ dim: ''0 , dir: 'down', strength: 'medium' });
    if (goalsDrift['1] && goalsDrift['1].impliedDrift > 0.5) singleSignals.push({ dim: ''1 , dir: 'down', strength: 'medium' });
    if (goalsDrift['3] && goalsDrift['3].impliedDrift > 0.5) singleSignals.push({ dim: ''3 , dir: 'down', strength: 'medium' });
    if (goalsDrift['4] && goalsDrift['4].impliedDrift > 0.5) singleSignals.push({ dim: ''4 , dir: 'down', strength: 'medium'''] && htftDrift[''].impliedDrift > 0.5) singleSignals.push({ dim: '', ', dir: 'down', strength: 'medium' });
    if (htftDrift[''] && htftDrift[''].impliedDrift < -0.5) singleSignals.push({ dim: '', dir: 'up', strength: 'medium' });
    if (htftDrift[''] && htftDrift[''].impliedDrift > 0.5) singleSignals.push({ dim: '', ', dir: 'down', strength: 'medium' });
    if (htftDrift[''] && htftDrift[''].impliedDrift < -0.5) singleSignals.push({ dim: '', dir: 'up', strength: 'medium' });
    if (htftDrift[''] && htftDrift[''].impliedDrift > 0.5) singleSignals.push({ dim: '', ', dir: 'down', strength: 'medium''pattern', code: 'EU_UP_HC_DOWN', strength: 'strong',
        desc: ''trap', code: 'EU_DOWN_HC_DOWN', strength: 'strong',
        desc: ''1:0'] && scoreDrift['1:0'].impliedDrift > 0.3) {
      crossSignals.push({
        type: 'pattern', code: 'EU_DOWN_S10_DOWN', strength: 'medium',
        desc: ''1:1'] && scoreDrift['1:1'].impliedDrift > 0.3 &&
        htftDrift[''] && htftDrift[''].impliedDrift > 0.3) {
      crossSignals.push({
        type: 'pattern', code: 'S11_DOWN_HTFT_PP_DOWN', strength: 'strong',
        desc: ''0:0'] && scoreDrift['0:0'].impliedDrift > 0.3 &&
        goalsDrift['0] && goalsDrift['0].impliedDrift > 0.3) {
      crossSignals.push({
        type: 'pattern', code: 'S00_DOWN_G0_DOWN', strength: 'strong',
        desc: ''] && htftDrift[''].impliedDrift < -0.3 &&
        goalsDrift['3] && goalsDrift['3].impliedDrift < -0.3) {
      crossSignals.push({
        type: 'pattern', code: 'HTFT_SS_UP_G3_UP', strength: 'medium',
        desc: ''] && htftDrift[''].impliedDrift > 0.3 && hcDrift.lose < -0.02) {
      crossSignals.push({
        type: 'pattern', code: 'HTFT_LS_DOWN_HC_DOWN', strength: 'medium',
        desc: '':');
      if (parseInt(parts[0]) + parseInt(parts[1]) >= 3 && scoreDrift[hs].impliedDrift > 0.3) { highScoreDown = true; break; }
    }
    for (var hg in goalsDrift) {
      var gn = parseInt(hg);
      if (gn >= 3 && goalsDrift[hg].impliedDrift > 0.3) { highGoalsDown = true; break; }
    }
    if (highScoreDown && highGoalsDown) {
      crossSignals.push({
        type: 'pattern', code: 'HIGH_SCORE_DOWN', strength: 'medium',
        desc: ''] && htftDrift[''].impliedDrift < -0.3) {
      crossSignals.push({
        type: 'pattern', code: 'HC_WIN_UP_HTFT_SS_DOWN', strength: 'medium',
        desc: ''0:1'] && scoreDrift['0:1'].impliedDrift > 0.3 &&
        htftDrift[''] && htftDrift[''].impliedDrift > 0.3) {
      crossSignals.push({
        type: 'pattern', code: 'LOSE_ALL_DOWN', strength: 'strong',
        desc: ''pattern', code: 'EU_DOWN_HC_UP', strength: 'strong',
        desc: ''] && htftDrift[''].impliedDrift < -0.3) {
      crossSignals.push({
        type: 'pattern', code: 'EU_DOWN_SS_UP', strength: 'medium',
        desc: '', val: (curr.w - prev.w) * 100 },
        { dir: '', val: (curr.d - prev.d) * 100 },
        { dir: '', val: (curr.l - prev.l) * 100 }
      ];
      for (var s = 0; s < shifts.length; s++) {
        if (Math.abs(shifts[s].val) > 2.0) {
          steamMoves.push({ time: eu[i].time, direction: shifts[s].dir, magnitude: shifts[s].val, from: eu[i - 1].time });
        }
      }
    }
    if (steamMoves.length > 0) {
      singleSignals.push({ dim: 'Steam', msg: '' + steamMoves.length + '', dir: steamMoves[0].magnitude > 0 ? 'up' : 'down', strength: steamMoves.length >= 3 ? 'strong' : 'medium''up''down''down''up''strong' ? 0.4 : 0.25;
      var isProbDim = (sig.dim === '');
      var isOddsDim = (sig.dim === '' || sig.dim === ''up''down'') >= 0 || sig.msg.indexOf('') >= 0) fusion.winA += w * 0.3;
        if (sig.msg.indexOf('') >= 0) fusion.draw += w * 0.3;
        if (sig.msg.indexOf('') >= 0 || sig.msg.indexOf('') >= 0) fusion.winB += w * 0.3;
        if (sig.msg.indexOf('') >= 0 || sig.msg.indexOf('') >= 0) fusion.winA -= w * 0.3;
        if (sig.msg.indexOf('') >= 0) fusion.draw -= w * 0.3;
        if (sig.msg.indexOf('') >= 0 || sig.msg.indexOf(''strong'') { fusion.winB += 0.03; fusion.winA -= 0.02; }
      else if (smv.direction === '') { fusion.draw += 0.04; fusion.winA -= 0.02; fusion.winB -= 0.02; }
      else if (smv.direction === '',
      confidence: 0
    };

    if (tg.length >= 2 && cs.length >= 2) {
      deduction.enabled = true;

      var tgF = tg[0], tgL = tg[tg.length - 1];
      var goalsChanges = [];
      for (var gd = 0; gd < tgF.goals.length; gd++) {
        var gdn = tgF.goals[gd].n;
        var gdL = findOutcome(tgL.goals, gdn, 'n');
        if (gdL) {
          var gChange = tgF.goals[gd].odds - gdL.odds;
          var gOdds = tgF.goals[gd].odds;
          var gScore = 0;
          if (gChange > 0) {
            gScore = (gChange / gOdds) * (10 / gOdds) + (10 / gOdds);
          } else if (Math.abs(gChange) < 0.2) {
            gScore = 10 / gOdds + (0.1 / gOdds);
          }
          goalsChanges.push({ goals: gdn, change: gChange, initial: gOdds, final: gdL.odds, priorityScore: gScore });
        }
      }
      goalsChanges.sort(function(a, b) { return b.priorityScore - a.priorityScore; });
      deduction.targetGoals = goalsChanges.filter(function(g) { return g.priorityScore > 0.5; }).slice(0, 3).map(function(g) { return g.goals; });

      if (deduction.targetGoals.length === 0) {
        deduction.targetGoals = goalsChanges.filter(function(g) { return g.change === 0 && g.initial < 5; }).slice(0, 2).map(function(g) { return g.goals; });
      }

      if (deduction.targetGoals.length > 0) {
        var csF = cs[0], csL = cs[cs.length - 1];
        var scoreCandidates = [];
        for (var cd = 0; cd < csF.outcomes.length; cd++) {
          var sc = csF.outcomes[cd].score;
          if (sc.indexOf('') >= 0) continue;
          var cL = findOutcome(csL.outcomes, sc, 'score');
          if (cL) {
            var sChange = csF.outcomes[cd].odds - cL.odds;
            var sOdds = csF.outcomes[cd].odds;
            var sScore = 0;
            if (sChange > 0) {
              sScore = (sChange / sOdds) * (10 / sOdds) + (10 / sOdds);
            } else if (sChange === 0) {
              sScore = 10 / sOdds;
            }
            var parts = sc.split(':');
            var total = parseInt(parts[0]) + parseInt(parts[1]);
            if (deduction.targetGoals.includes(total)) {
              scoreCandidates.push({
                score: sc,
                change: sChange,
                initial: sOdds,
                final: cL.odds,
                priorityScore: sScore,
                totalGoals: total,
                goalDiff: parseInt(parts[0]) - parseInt(parts[1])
              });
            }
          }
        }
        scoreCandidates.sort(function(a, b) { return b.priorityScore - a.priorityScore; });
        deduction.candidateScores = scoreCandidates.filter(function(s) { return s.priorityScore > 0.5; }).slice(0, 5);

        if (deduction.candidateScores.length === 0) {
          deduction.candidateScores = scoreCandidates.slice(0, 3);
        }

        var handicapDirection = null;
        var hcAnalysis = '';
        if (hc.length >= 2) {
          var hcF = hc[0], hcL = hc[hc.length - 1];
          var hcWinChange = hcF.win - hcL.win;
          var hcDrawChange = hcF.draw - hcL.draw;
          var hcLoseChange = hcF.lose - hcL.lose;

          var hcWinScore = hcWinChange > 0 ? (hcWinChange / hcF.win) * (10 / hcF.win) + (10 / hcF.win) : (hcWinChange === 0 ? 10 / hcF.win : 0);
          var hcDrawScore = hcDrawChange > 0 ? (hcDrawChange / hcF.draw) * (10 / hcF.draw) + (10 / hcF.draw) : (hcDrawChange === 0 ? 10 / hcF.draw : 0);
          var hcLoseScore = hcLoseChange > 0 ? (hcLoseChange / hcF.lose) * (10 / hcF.lose) + (10 / hcF.lose) : (hcLoseChange === 0 ? 10 / hcF.lose : 0);

          if (hcLoseChange < -0.05 && hcWinChange > 0) {
            if (hcWinChange < 0.15 && hcDrawChange < 0 && Math.abs(hcDrawChange) > 0.2) {
              handicapDirection = 'draw';
              hcAnalysis = '' + hcF.lose + '' + Math.abs(hcLoseChange).toFixed(2) + '';
            } else {
              handicapDirection = 'win';
              hcAnalysis = '' + hcF.lose + '' + Math.abs(hcLoseChange).toFixed(2) + '';
            }
          } else if (hcDrawChange > 0.2) {
            handicapDirection = 'draw';
            hcAnalysis = '' + hcF.draw + '' + hcDrawChange.toFixed(2) + '';
          } else if (hcLoseChange < -0.05 && hcWinChange >= 0) {
            if (hcWinChange < 0.15 && hcDrawChange < 0 && Math.abs(hcDrawChange) > 0.2) {
              handicapDirection = 'draw';
              hcAnalysis = '' + hcF.lose + '' + Math.abs(hcLoseChange).toFixed(2) + '';
            } else {
              handicapDirection = 'win';
              hcAnalysis = '' + hcF.lose + '' + Math.abs(hcLoseChange).toFixed(2) + ''draw';
              hcAnalysis = '' + Math.abs(hcDrawChange).toFixed(2) + '';
            } else {
              handicapDirection = 'win';
              hcAnalysis = '' + hcF.win + '';
            }
          } else if (hcDrawScore >= hcWinScore && hcDrawScore >= hcLoseScore) {
            handicapDirection = 'draw';
            hcAnalysis = '';
          } else {
            handicapDirection = 'lose';
            hcAnalysis = '';
          }
        }

        var handicapNum = trajectory.handicapValue ? parseInt(trajectory.handicapValue) : -1;

        if (handicapDirection && deduction.candidateScores.length > 0) {
          var validated = [];
          deduction.candidateScores.forEach(function(sc) {
            if (handicapDirection === 'win') {
              var winDiff = handicapNum < 0 ? sc.goalDiff >= Math.abs(handicapNum) : sc.goalDiff >= -handicapNum;
              if (winDiff) validated.push(sc);
            } else if (handicapDirection === 'draw') {
              var drawDiff = handicapNum < 0 ? sc.goalDiff === Math.abs(handicapNum) - 1 : sc.goalDiff === -handicapNum;
              if (drawDiff) validated.push(sc);
            } else if (handicapDirection === 'lose') {
              var loseDiff = handicapNum < 0 ? sc.goalDiff <= Math.abs(handicapNum) - 1 : sc.goalDiff <= -handicapNum;
              if (loseDiff) validated.push(sc);
            } else {
              validated.push(sc);
            }
          });
          deduction.validatedScores = validated.slice(0, 3);
        } else {
          deduction.validatedScores = deduction.candidateScores.slice(0, 3);
        }

        if (deduction.validatedScores.length === 0 && deduction.candidateScores.length > 0) {
          deduction.validatedScores = deduction.candidateScores.slice(0, 3);
        }

        if (deduction.validatedScores.length > 0) {
          deduction.finalPrediction = deduction.validatedScores.map(function(s) { return s.score; }).join('');
          var goalStr = deduction.targetGoals.join('';
          var scoreStr = deduction.validatedScores.map(function(s) { return s.score; }).join('');
          deduction.analysis = '' + goalStr + '' + scoreStr;
          if (hcAnalysis) {
            deduction.analysis += ' + hcAnalysis';
          }
          deduction.analysis += '' };
    
    var first = eu[0], last = eu[eu.length - 1];
    var firstP = { w: 1 / first.win, d: 1 / first.draw, l: 1 / first.lose };
    var lastP = { w: 1 / last.win, d: 1 / last.draw, l: 1 / last.lose };
    var firstOverround = firstP.w + firstP.d + firstP.l;
    var lastOverround = lastP.w + lastP.d + lastP.l;
    
    var behavior = {
      manipulation: {
        hardResistance: false,
        softResistance: false,
        aggressivePush: false,
        trapPattern: false
      },
      riskManagement: {
        overroundChange: lastOverround - firstOverround,
        initialOverround: firstOverround,
        finalOverround: lastOverround,
        isProtective: false,
        isAggressive: false
      },
      marketSentiment: {
        moneyFlow: { win: lastP.w - firstP.w, draw: lastP.d - firstP.d, lose: lastP.l - firstP.l },
        mostBet: null,
        leastBet: null,
        volatility: 0
      },
      confidence: 0
    };
    
    var winDrift = lastP.w - firstP.w;
    var loseDrift = lastP.l - firstP.l;
    
    if (Math.abs(winDrift) < 0.01 && first.win > 2.0) {
      behavior.manipulation.hardResistance = true;
    }
    
    if (Math.abs(winDrift) > 0.005 && Math.abs(winDrift) < 0.02) {
      behavior.manipulation.softResistance = true;
    }
    
    if (loseDrift > 0.03 && last.lose < first.lose) {
      behavior.manipulation.aggressivePush = true;
    }
    
    if (lastOverround > firstOverround + 0.05) {
      behavior.riskManagement.isProtective = true;
    } else if (lastOverround < firstOverround - 0.03) {
      behavior.riskManagement.isAggressive = true;
    }
    
    var maxFlow = Math.max(Math.abs(behavior.marketSentiment.moneyFlow.win), 
                           Math.abs(behavior.marketSentiment.moneyFlow.draw), 
                           Math.abs(behavior.marketSentiment.moneyFlow.lose));
    if (Math.abs(behavior.marketSentiment.moneyFlow.win) === maxFlow) {
      behavior.marketSentiment.mostBet = behavior.marketSentiment.moneyFlow.win > 0 ? '' : '');
    } else if (Math.abs(behavior.marketSentiment.moneyFlow.draw) === maxFlow) {
      behavior.marketSentiment.mostBet = behavior.marketSentiment.moneyFlow.draw > 0 ? '' : '');
    } else {
      behavior.marketSentiment.mostBet = behavior.marketSentiment.moneyFlow.lose > 0 ? '' : '' };
    
    var first = eu[0], last = eu[eu.length - 1];
    var firstP = { w: 1 / first.win, d: 1 / first.draw, l: 1 / first.lose };
    var lastP = { w: 1 / last.win, d: 1 / last.draw, l: 1 / last.lose };
    
    var manipulation = {
      type: 'none',
      signals: [],
      confidence: 0,
      recommendation: null,
      riskLevel: 'low'
    };
    
    var isHomeFavorite = first.win < first.lose;
    var homeOddsRise = last.win > first.win;
    
    if (isHomeFavorite && homeOddsRise && (last.win - first.win) > 0.15) {
      manipulation.type = 'block';
      manipulation.signals.push({
        code: 'BLOCK_HOME',
        desc: '',
        strength: 'strong'
      });
    }
    
    if (isHomeFavorite && (first.win - last.win) > 0.1 && lastP.w > firstP.w + 0.05) {
      manipulation.type = 'trap';
      manipulation.signals.push({
        code: 'TRAP_HOME',
        desc: '',
        strength: 'strong'
      });
    }
    
    var isAwayUnderdog = first.lose > first.win;
    var awayOddsDrop = last.lose < first.lose;
    
    if (isAwayUnderdog && awayOddsDrop && (first.lose - last.lose) > 0.2) {
      manipulation.type = 'reverse_trap';
      manipulation.signals.push({
        code: 'REVERSE_TRAP',
        desc: '',
        strength: 'strong'
      });
    }
    
    if (Math.abs(first.win - 2.0) < 0.1 && Math.abs(last.win - 2.0) < 0.1) {
      manipulation.signals.push({
        code: 'PRICE_ANCHOR',
        desc: '',
        strength: 'medium'
      });
    }
    
    var winDrift = lastP.w - firstP.w;
    var loseDrift = lastP.l - firstP.l;
    if (winDrift > 0.02 && loseDrift > 0.02) {
      manipulation.signals.push({
        code: 'CONFLICT_SIGNAL',
        desc: '',
        strength: 'strong'
      });
      manipulation.riskLevel = 'high';
    }
    
    manipulation.confidence = Math.min(1.0, manipulation.signals.length * 0.3);
    
    if (manipulation.type === 'block') {
      manipulation.recommendation = '';
    } else if (manipulation.type === 'trap') {
      manipulation.recommendation = '';
    } else if (manipulation.type === 'reverse_trap') {
      manipulation.recommendation = '' };
    
    var first = eu[0], last = eu[eu.length - 1];
    var firstP = { w: 1 / first.win, d: 1 / first.draw, l: 1 / first.lose };
    var lastP = { w: 1 / last.win, d: 1 / last.draw, l: 1 / last.lose };
    
    var features = {
      market: {
        moneyHeatIndex: 0,
        bookmakerConfidence: 0,
        anomalyScore: 0,
        consensusConcentration: 0,
        timeDecayFactor: 1
      },
      momentum: {
        winMomentum: 0,
        drawMomentum: 0,
        loseMomentum: 0,
        goalMomentum: 0
      },
      pattern: {
        isSteamMove: false,
        isSharpMoney: false,
        isLateMoney: false,
        patternStrength: 0
      },
      timing: {
        criticalTimeWindow: null,
        lastMinuteAction: false
      }
    };
    
    var maxProbChange = Math.max(Math.abs(lastP.w - firstP.w), 
                                 Math.abs(lastP.d - firstP.d), 
                                 Math.abs(lastP.l - firstP.l));
    features.market.moneyHeatIndex = Math.min(1.0, maxProbChange * 10);
    
    var firstOverround = firstP.w + firstP.d + firstP.l;
    var lastOverround = lastP.w + lastP.d + lastP.l;
    features.market.bookmakerConfidence = lastOverround < firstOverround ? 0.7 + (firstOverround - lastOverround) * 2 : 0.5;
    
    var volatility = 0;
    for (var i = 1; i < eu.length; i++) {
      var prev = { w: 1 / eu[i-1].win, d: 1 / eu[i-1].draw, l: 1 / eu[i-1].lose };
      var curr = { w: 1 / eu[i].win, d: 1 / eu[i].draw, l: 1 / eu[i].lose };
      volatility += Math.abs(curr.w - prev.w) + Math.abs(curr.d - prev.d) + Math.abs(curr.l - prev.l);
    }
    features.market.anomalyScore = Math.min(1.0, volatility);
    features.market.consensusConcentration = 0.6;
    
    var hoursToKickoff = matchContext ? matchContext.hoursToKickoff || 24 : 24;
    features.market.timeDecayFactor = hoursToKickoff < 6 ? 0.3 : hoursToKickoff < 12 ? 0.6 : 1.0;
    
    features.momentum.winMomentum = lastP.w - firstP.w;
    features.momentum.drawMomentum = lastP.d - firstP.d;
    features.momentum.loseMomentum = lastP.l - firstP.l;
    
    features.pattern.isSteamMove = maxProbChange > 0.05;
    
    if (eu.length >= 3) {
      var lateStart = eu.length - Math.ceil(eu.length * 0.2);
      var lateFirst = eu[lateStart];
      var latePFirst = { w: 1 / lateFirst.win, d: 1 / lateFirst.draw, l: 1 / lateFirst.lose };
      var lateChange = Math.abs(lastP.w - latePFirst.w) + Math.abs(lastP.d - latePFirst.d) + Math.abs(lastP.l - latePFirst.l);
      features.pattern.isLateMoney = lateChange > 0.03;
    }
    
    if (features.pattern.isLateMoney || features.market.anomalyScore > 0.5) {
      features.timing.criticalTimeWindow = 'late''Stream callback error:', e);
        }
      });
      
      this.updateCallbacks.forEach(function(cb) {
        try {
          cb(matchId, update);
        } catch (e) {
          console.error('Global callback error:', e);
        }
      });
    },
    
    registerGlobalCallback: function(callback) {
      this.updateCallbacks.push(callback);
      return {
        unregister: function() {
          var idx = OddsStreamProcessor.updateCallbacks.indexOf(callback);
          if (idx >= 0) {
            OddsStreamProcessor.updateCallbacks.splice(idx, 1);
          }
        }
      };
    },
    
    simulateUpdate: function(matchId, trajectoryUpdate) {
      this.processUpdate(matchId, {
        type: 'odds_update'') >= 0 || sig.msg.indexOf('') >= 0;
        } else if (actual.draw === 1) {
          wasCorrect = sig.msg && sig.msg.indexOf('') >= 0;
        } else if (actual.winB === 1) {
          wasCorrect = sig.msg && (sig.msg.indexOf('') >= 0 || sig.msg.indexOf('') >= 0);
        }
        if (wasCorrect) evaluation.signalQuality.correctSignals++;
      });
      evaluation.signalQuality.signalAccuracy = evaluation.signalQuality.detectedSignals > 0 
        ? evaluation.signalQuality.correctSignals / evaluation.signalQuality.detectedSignals 
        : 0;
    }
    
    if (evaluation.brierScore > 0.4) {
      evaluation.recommendations.push('');
    }
    if (evaluation.signalQuality.signalAccuracy < 0.4) {
      evaluation.recommendations.push('');
    }
    
    return evaluation;
  }

  function calcOddsTrajectoryCorrection(winDrift, drawDrift, loseDrift, hcDrift, steamMoves, scoreTrend, goalsTrend, htftTrend) {
    var corr = { winA: 0, draw: 0, winB: 0, goalLambda: 0, confidence: 0 };
    if (winDrift < -0.02) corr.winA += winDrift * 0.5;
    if (loseDrift > 0.02) corr.winB += loseDrift * 0.4;
    if (drawDrift > 0.015) corr.draw += drawDrift * 0.6;
    if (hcDrift.lose > 0.03) { corr.winB += 0.02; corr.draw += 0.01; corr.winA -= 0.03; }
    for (var i = 0; i < steamMoves.length; i++) {
      var sm = steamMoves[i];
      if (sm.direction === '') { corr.winB += 0.03; corr.winA -= 0.02; }
      else if (sm.direction === '') { corr.draw += 0.04; corr.winA -= 0.02; corr.winB -= 0.02; }
      else if (sm.direction === '') { corr.winA += 0.02; corr.winB -= 0.02; }
    }
    if (scoreTrend['1:1'] && scoreTrend['1:1'].implied > 0.3) corr.draw += 0.02;
    if (goalsTrend['0] && goalsTrend['0].drift < -0.5) corr.goalLambda -= 0.1;
    if (goalsTrend['1] && goalsTrend['1].drift < -0.3) corr.goalLambda -= 0.05;
    if (htftTrend[''] && htftTrend['''winA' : (lastProb.l > lastProb.d ? 'winB' : 'draw''draw';
    else if (driftDir.lose > 0.02 && driftDir.lose > driftDir.win) trajectoryPredicted = 'winB';
    else if (driftDir.win > 0.02) trajectoryPredicted = 'winA''draw';
        else if (fusion.winA === maxFusion) fusionPrediction = 'winA';
        else fusionPrediction = 'winB''draw';
      else if ((impact.winA || 0) > Math.abs(impact.draw || 0) && (impact.winA || 0) > Math.abs(impact.winB || 0)) signalDirection = 'winA';
      else if ((impact.winB || 0) > Math.abs(impact.draw || 0) && (impact.winB || 0) > Math.abs(impact.winA || 0)) signalDirection = 'winB''');
      var isOddsDim = (sig.dim === '' || sig.dim === '');
      var bullish = false;
      if (isProbDim) bullish = (sig.dir === 'up');
      else if (isOddsDim) bullish = (sig.dir === 'down');
      var sigDirection = null;
      if (sig.msg.indexOf('') >= 0 || sig.msg.indexOf('') >= 0) sigDirection = bullish ? 'winA' : 'winB';
      else if (sig.msg.indexOf('') >= 0) sigDirection = bullish ? 'draw' : null;
      else if (sig.msg.indexOf('') >= 0 || sig.msg.indexOf('') >= 0) sigDirection = bullish ? 'winB' : 'winA''N/A';

    var conclusionParts = [];
    if (mktHit) conclusionParts.push('');
    if (trajectoryHit) conclusionParts.push('');
    if (fusionHit) conclusionParts.push('');
    if (crossHitCount > 0) conclusionParts.push(crossHitCount + '/' + crossTotalCount + '');
    if (conclusionParts.length === 0) conclusionParts.push('''%',
        draw: (fusion.draw * 100).toFixed(1) + '%',
        winB: (fusion.winB * 100).toFixed(1) + '%''; ''teamA:teamB''object') return matchStr;
    if (typeof matchStr !== 'string') return null;
    var parts = matchStr.split(':');
    if (parts.length >= 2) {
      return { home: parts[0].trim(), away: parts[1].trim() };
    }
    return { home: matchStr, away: '' };
  }

  // ============================================================
  // PHASE 3-2: Walk-Forward Backtesting Framework
  // ============================================================
  // historicalMatches = [{teamA, teamB, resultA, resultB, oddsA, oddsD, oddsB}, ...]
  // modelFunc = function(match) => {winA, draw, winB}
  function walkForwardTest(historicalMatches, modelFunc, options) {
    options = options || {};
    var trainSize = options.trainSize || 50;
    var testSize = options.testSize || 20;
    var stepSize = options.stepSize || testSize;
    var bankroll = options.initialBankroll || 1000;
    var kellyFrac = options.kellyFraction || 0.25;

    if (!historicalMatches || historicalMatches.length < trainSize + testSize) {
      return { error: '''winA', bestProb = pred.winA, bestOdds = m.oddsA || 2.0;
      if (pred.draw > bestProb) { bestSel = 'draw'; bestProb = pred.draw; bestOdds = m.oddsD || 3.0; }
      if (pred.winB > bestProb) { bestSel = 'winB''draw') actual = m.resultA === m.resultB ? 1 : 0;
      if (bestSel === 'winB') actual = m.resultB > m.resultA ? 1 : 0;

      var profit = actual * stake * bestOdds - stake;
      bankroll += profit;

      bets.push({
        match: m.teamA + ':''number''-' + (bins[b].upper * 100).toFixed(0) + '%''Bet ' + (i + 1)),
        selection: p.selection || '-',
        fullKelly: kf,
        quarterKelly: kf * 0.25,
        tenthKelly: kf * 0.1,
        recommendation: kf > 0 ? '' + (kf * 0.25 * 100).toFixed(2) + '' : ''wss://api.example.com/ws/odds';
    var reconnectInterval = config.reconnectInterval || 5000;
    var heartbeatInterval = config.heartbeatInterval || 30000;

    var ws = null;
    var reconnectTimer = null;
    var heartbeatTimer = null;

    function connect() {
      if (typeof WebSocket === 'undefined') {
        console.warn('[Realtime] WebSocket not available, using polling fallback');
        initPollingFallback(config);
        return;
      }

      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = function() {
          REALTIME_STATE.connected = true;
          console.log('[Realtime] WebSocket connected');
          // 订阅所有活跃比
          for (var matchId in REALTIME_STATE.activeMatches) {
            ws.send(JSON.stringify({ action: 'subscribe', matchId: matchId }));
          }
          // 启动心跳
          heartbeatTimer = setInterval(function() {
            if (ws && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'ping' }));
            }
          }, heartbeatInterval);
        };

        ws.onmessage = function(event) {
          var data;
          try {
            data = JSON.parse(event.data);
          } catch(e) { return; }
          handleRealtimeData(data);
        };

        ws.onclose = function() {
          REALTIME_STATE.connected = false;
          clearInterval(heartbeatTimer);
          console.log('[Realtime] WebSocket closed, reconnecting in ' + reconnectInterval + 'ms');
          reconnectTimer = setTimeout(connect, reconnectInterval);
        };

        ws.onerror = function(err) {
          console.error('[Realtime] WebSocket error:', err);
        };
      } catch(e) {
        console.error('[Realtime] Failed to create WebSocket:', e);
        initPollingFallback(config);
      }
    }

    connect();

    return {
      disconnect: function() {
        clearTimeout(reconnectTimer);
        clearInterval(heartbeatTimer);
        if (ws) ws.close();
      },
      subscribe: function(matchId) {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: 'subscribe', matchId: matchId }));
        }
      }
    };
  }

  // ─── 4.2 轮询降级方案 (Polling Fallback) ───
  function initPollingFallback(config) {
    config = config || {};
    var pollInterval = config.pollInterval || 10000;
    var apiBaseUrl = config.apiBaseUrl || 'https://api.example.com/v1';

    var timer = setInterval(function() {
      for (var matchId in REALTIME_STATE.activeMatches) {
        fetchOddsUpdate(matchId, apiBaseUrl);
      }
    }, pollInterval);

    return { stop: function() { clearInterval(timer); } };
  }

  // 模拟 API 请求 (实际部署时替换为真实 fetch)
  function fetchOddsUpdate(matchId, baseUrl) {
    // 模拟异步获取赔率更新
    setTimeout(function() {
      var mockUpdate = generateMockOddsUpdate(matchId);
      handleRealtimeData(mockUpdate);
    }, 100);
  }

  // 生成模拟赔率更新 (用于测试)
  function generateMockOddsUpdate(matchId) {
    var baseOdds = { winA: 2.5, draw: 3.2, winB: 2.8 };
    var drift = 0.95 + Math.random() * 0.1; // ±5% 波动
    return {
      type: 'odds_update',
      matchId: matchId,
      timestamp: new Date().toISOString(),
      odds: {
        winA: Math.max(1.01, baseOdds.winA * drift),
        draw: Math.max(1.01, baseOdds.draw * drift),
        winB: Math.max(1.01, baseOdds.winB * (2 - drift))
      },
      source: 'mock'
    };
  }

  // ─── 4.3 实时数据处理中心 ───
  function handleRealtimeData(data) {
    if (!data || !data.type) return;

    switch(data.type) {
      case 'odds_update':
        handleOddsUpdate(data);
        break;
      case 'live_score':
        handleLiveScore(data);
        break;
      case 'match_event':
        handleMatchEvent(data);
        break;
      case 'lineup_change':
        handleLineupChange(data);
        break;
      case 'weather_alert':
        handleWeatherAlert(data);
        break;
    }

    REALTIME_STATE.lastUpdate = new Date().toISOString();
    notifySubscribers(data);
  }

  // ─── 4.4 赔率更新处理 ───
  function handleOddsUpdate(data) {
    var matchId = data.matchId;
    if (!REALTIME_STATE.oddsStreams[matchId]) {
      REALTIME_STATE.oddsStreams[matchId] = [];
    }

    // 记录赔率历史
    REALTIME_STATE.oddsStreams[matchId].push({
      timestamp: data.timestamp,
      odds: data.odds,
      source: data.source
    });

    // 保持最00条记
    if (REALTIME_STATE.oddsStreams[matchId].length > 100) {
      REALTIME_STATE.oddsStreams[matchId].shift();
    }

    // 检测显著赔率变(>10%)
    var significantChange = detectSignificantOddsChange(matchId);
    if (significantChange) {
      triggerAutoRecalc(matchId, 'odds_drift', significantChange);
    }

    console.log('[Realtime] Odds update for', matchId, ':', JSON.stringify(data.odds));
  }

  // 检测显著赔率变
  function detectSignificantOddsChange(matchId) {
    var history = REALTIME_STATE.oddsStreams[matchId];
    if (!history || history.length < 2) return null;

    var current = history[history.length - 1].odds;
    var previous = history[history.length - 2].odds;

    var changes = {};
    var threshold = 0.10; // 10% 阈

    for (var key in current) {
      if (previous[key]) {
        var change = Math.abs(current[key] - previous[key]) / previous[key];
        if (change > threshold) {
          changes[key] = {
            from: previous[key],
            to: current[key],
            changePercent: (change * 100).toFixed(2)
          };
        }
      }
    }

    return Object.keys(changes).length > 0 ? changes : null;
  }

  // ─── 4.5 实时比分处理 ───
  function handleLiveScore(data) {
    var matchId = data.matchId;
    if (!REALTIME_STATE.activeMatches[matchId]) {
      REALTIME_STATE.activeMatches[matchId] = {};
    }

    REALTIME_STATE.activeMatches[matchId].score = data.score;
    REALTIME_STATE.activeMatches[matchId].status = data.status;
    REALTIME_STATE.activeMatches[matchId].minute = data.minute;

    // 进球事件触发重算
    if (data.eventType === 'goal') {
      triggerAutoRecalc(matchId, 'goal', data);
    }

    console.log('[Realtime] Score update for', matchId, ':', data.score.a, '-', data.score.b);
  }

  // ─── 4.6 比赛事件处理 ───
  function handleMatchEvent(data) {
    var matchId = data.matchId;
    var eventType = data.event;

    // 红黄牌、换人、伤病等事件可能影响模型
    var significantEvents = ['red_card', 'key_injury', 'penalty_awarded', 'own_goal'];
    if (significantEvents.indexOf(eventType) !== -1) {
      triggerAutoRecalc(matchId, eventType, data);
    }

    console.log('[Realtime] Match event for', matchId, ':', eventType);
  }

  // ─── 4.7 阵容变更处理 ───
  function handleLineupChange(data) {
    var matchId = data.matchId;
    // 关键球员缺阵/复出触发重算
    if (data.keyPlayerChange) {
      triggerAutoRecalc(matchId, 'lineup_change', data);
    }
  }

  // ─── 4.8 天气预警处理 ───
  function handleWeatherAlert(data) {
    var matchId = data.matchId;
    // 极端天气(暴雨/大风)触发重算
    if (data.severity === 'high') {
      triggerAutoRecalc(matchId, 'weather_alert', data);
    }
  }

  // ─── 4.9 自动模型重算触发───
  var RECALC_QUEUE = [];
  var RECALC_TIMER = null;
  var RECALC_DEBOUNCE_MS = 5000; // 5秒防

  function triggerAutoRecalc(matchId, triggerType, triggerData) {
    // 加入重算队列
    var existing = RECALC_QUEUE.find(function(item) { return item.matchId === matchId; });
    if (existing) {
      existing.triggerType = triggerType;
      existing.triggerData = triggerData;
      existing.timestamp = Date.now();
    } else {
      RECALC_QUEUE.push({
        matchId: matchId,
        triggerType: triggerType,
        triggerData: triggerData,
        timestamp: Date.now()
      });
    }

    // 防抖处理
    clearTimeout(RECALC_TIMER);
    RECALC_TIMER = setTimeout(processRecalcQueue, RECALC_DEBOUNCE_MS);

    // 立即推送通知
    pushNotification({
      type: 'recalc_triggered',
      matchId: matchId,
      triggerType: triggerType,
      message: getTriggerMessage(triggerType, triggerData),
      timestamp: new Date().toISOString()
    });
  }

  function getTriggerMessage(triggerType, data) {
    var messages = {
      'odds_drift': '',
      'goal': '',
      'red_card': '',
      'key_injury': '',
      'penalty_awarded': '',
      'lineup_change': '',
      'weather_alert': ''
    };
    return messages[triggerType] || '';
  }

  function processRecalcQueue() {
    while (RECALC_QUEUE.length > 0) {
      var item = RECALC_QUEUE.shift();
      performRealtimeRecalc(item.matchId, item.triggerType, item.triggerData);
    }
  }

  // 执行实时重算
  function performRealtimeRecalc(matchId, triggerType, triggerData) {
    var match = MATCHES.find(function(m) { return m.matchId === matchId; });
    if (!match) return;

    var teamA = match.teamA;
    var teamB = match.teamB;

    // 根据触发类型调整参数
    var contextAdjustments = {};

    if (triggerType === 'goal') {
      // 进球后调整：领先方防守增强，落后方进攻增
      var scoreA = triggerData.score ? triggerData.score.a : 0;
      var scoreB = triggerData.score ? triggerData.score.b : 0;
      var diff = scoreA - scoreB;
      if (diff !== 0) {
        contextAdjustments.leadDefenceBoost = diff > 0 ? 0.05 : -0.05;
        contextAdjustments.trailAttackBoost = diff > 0 ? -0.03 : 0.03;
      }
    } else if (triggerType === 'red_card') {
      // 红牌：少一人方攻防均下
      var redTeam = triggerData.team;
      if (redTeam === teamA) {
        contextAdjustments.teamAAttackPenalty = -0.15;
        contextAdjustments.teamADefencePenalty = -0.20;
      } else {
        contextAdjustments.teamBAttackPenalty = -0.15;
        contextAdjustments.teamBDefencePenalty = -0.20;
      }
    } else if (triggerType === 'odds_drift') {
      // 赔率变动：融入最新市场信
      var latestOdds = REALTIME_STATE.oddsStreams[matchId];
      if (latestOdds && latestOdds.length > 0) {
        contextAdjustments.latestMarketOdds = latestOdds[latestOdds.length - 1].odds;
      }
    }

    // 重新计算预测
    var newPrediction = predictMatchRealtime(teamA, teamB, match.venue, contextAdjustments);

    // 更新活跃比赛状
    if (!REALTIME_STATE.activeMatches[matchId]) {
      REALTIME_STATE.activeMatches[matchId] = {};
    }
    REALTIME_STATE.activeMatches[matchId].latestPrediction = newPrediction;
    REALTIME_STATE.activeMatches[matchId].lastRecalc = new Date().toISOString();
    REALTIME_STATE.activeMatches[matchId].triggerType = triggerType;

    // 推送重算结
    pushNotification({
      type: 'recalc_complete',
      matchId: matchId,
      triggerType: triggerType,
      prediction: newPrediction,
      message: match.teamA + ' vs ' + match.teamB + '',
      timestamp: new Date().toISOString()
    });

    console.log('[Realtime] Recalc complete for', matchId, 'trigger:', triggerType);
  }

  // ─── 4.10 实时预测接口 (带上下文调整) ───
  function predictMatchRealtime(teamA, teamB, venue, contextAdjustments) {
    contextAdjustments = contextAdjustments || {};

    var a = TEAMS[teamA];
    var b = TEAMS[teamB];
    if (!a || !b) return null;

    // 应用实时调整
    var adjA = 0, adjB = 0;
    var defA = a.defence, defB = b.defence;
    var atkA = a.attack, atkB = b.attack;

    if (contextAdjustments.leadDefenceBoost) {
      defA += contextAdjustments.leadDefenceBoost;
      defB -= contextAdjustments.leadDefenceBoost;
    }
    if (contextAdjustments.trailAttackBoost) {
      atkA += contextAdjustments.trailAttackBoost;
      atkB -= contextAdjustments.trailAttackBoost;
    }
    if (contextAdjustments.teamAAttackPenalty) atkA += contextAdjustments.teamAAttackPenalty;
    if (contextAdjustments.teamADefencePenalty) defA += contextAdjustments.teamADefencePenalty;
    if (contextAdjustments.teamBAttackPenalty) atkB += contextAdjustments.teamBAttackPenalty;
    if (contextAdjustments.teamBDefencePenalty) defB += contextAdjustments.teamBDefencePenalty;

    // 使用调整后的参数计算
    var lambdaA = calcLambda(teamA, teamB, venue);
    var lambdaB = calcLambda(teamB, teamA, venue);

    // 应用实时调整因子
    if (contextAdjustments.teamAAttackPenalty) lambdaA *= (1 + contextAdjustments.teamAAttackPenalty);
    if (contextAdjustments.teamBAttackPenalty) lambdaB *= (1 + contextAdjustments.teamBAttackPenalty);

    // 使用调整后的参数计算（复用已有的双变量泊松模型）
    var predResult = predictMatch(teamA, teamB, { venue: venue });
    var p = { winA: predResult.winA, draw: predResult.draw, winB: predResult.winB };

    // 融入最新赔率（复用已有的赔率校准函数）
    if (contextAdjustments.latestMarketOdds) {
      var calibrated = calibrateWithOdds(p, contextAdjustments.latestMarketOdds);
      if (calibrated) p = calibrated;
    }

    return {
      teamA: teamA,
      teamB: teamB,
      lambdaA: lambdaA,
      lambdaB: lambdaB,
      probabilities: p,
      timestamp: new Date().toISOString(),
      adjustments: contextAdjustments
    };
  }

  // ─── 4.11 推送通知系统 ───
  var NOTIFICATION_CONFIG = {
    enabled: true,
    channels: ['console', 'callback'], // 'console', 'callback', 'email', 'webpush'
    minSeverity: 'info' // 'debug', 'info', 'warning', 'critical'
  };

  function pushNotification(notification) {
    if (!NOTIFICATION_CONFIG.enabled) return;

    var severity = notification.severity || 'info';
    var levels = { debug: 0, info: 1, warning: 2, critical: 3 };
    if (levels[severity] < levels[NOTIFICATION_CONFIG.minSeverity]) return;

    // Console 通道
    if (NOTIFICATION_CONFIG.channels.indexOf('console') !== -1) {
      var prefix = '[Realtime][' + severity.toUpperCase() + ''];
      console.log(prefix, notification.message, notification);
    }

    // Callback 通道
    if (NOTIFICATION_CONFIG.channels.indexOf('callback') !== -1) {
      notifySubscribers(notification);
    }
  }

  // ─── 4.12 订阅者管───
  function subscribeRealtime(callback) {
    if (typeof callback === 'function') {
      REALTIME_STATE.subscribers.push(callback);
    }
  }

  function unsubscribeRealtime(callback) {
    var idx = REALTIME_STATE.subscribers.indexOf(callback);
    if (idx !== -1) {
      REALTIME_STATE.subscribers.splice(idx, 1);
    }
  }

  function notifySubscribers(data) {
    for (var i = 0; i < REALTIME_STATE.subscribers.length; i++) {
      try {
        REALTIME_STATE.subscribers[i](data);
      } catch(e) {
        console.error('[Realtime] Subscriber error:', e);
      }
    }
  }

  // ─── 4.13 实时数据查询接口 ───
  function getRealtimeState(matchId) {
    if (matchId) {
      return REALTIME_STATE.activeMatches[matchId] || null;
    }
    return {
      connected: REALTIME_STATE.connected,
      lastUpdate: REALTIME_STATE.lastUpdate,
      activeMatches: Object.keys(REALTIME_STATE.activeMatches),
      oddsStreams: Object.keys(REALTIME_STATE.oddsStreams)
    };
  }

  function getOddsHistory(matchId) {
    return REALTIME_STATE.oddsStreams[matchId] || [];
  }

  // ─── 4.14 实时仪表盘数───
  function generateRealtimeDashboard() {
    var dashboard = {
      timestamp: new Date().toISOString(),
      connected: REALTIME_STATE.connected,
      activeMatches: [],
      alerts: []
    };

    for (var matchId in REALTIME_STATE.activeMatches) {
      var match = REALTIME_STATE.activeMatches[matchId];
      var matchInfo = MATCHES.find(function(m) { return m.matchId === matchId; });

      dashboard.activeMatches.push({
        matchId: matchId,
        teams: matchInfo ? (matchInfo.teamA + ' vs ' + matchInfo.teamB) : matchId,
        score: match.score || { a: 0, b: 0 },
        status: match.status || 'scheduled',
        minute: match.minute || 0,
        latestPrediction: match.latestPrediction,
        lastRecalc: match.lastRecalc,
        oddsDrift: REALTIME_STATE.oddsStreams[matchId] ?
          REALTIME_STATE.oddsStreams[matchId].length : 0
      });
    }

    // 检测异常并生成警报
    for (var matchId in REALTIME_STATE.activeMatches) {
      var match = REALTIME_STATE.activeMatches[matchId];
      if (match.latestPrediction) {
        var p = match.latestPrediction.probabilities;
        if (p && p.draw > 0.45) {
          dashboard.alerts.push({
            matchId: matchId,
            type: 'high_draw_probability',
            message: '' + (p.draw * 100).toFixed(1) + '',
            severity: 'warning'
          });
        }
      }
    }

    return dashboard;
  }

  // ============================================================
  // PHASE 5: 公开 API (合并所有模
  // ============================================================

  // 合并基础API和v5.1新增API
  var finalAPI = {};
  for (var key in _publicAPI) { finalAPI[key] = _publicAPI[key]; }

  // v5.1 新增/覆盖
  finalAPI.predict = predictMatch;
  finalAPI.predictMatch = predictMatch;
  finalAPI.getMatch = function(id) { return MATCHES.find(function(m) { return m.matchId === id; }); };
  finalAPI.getMatches = function() { return MATCHES; };
  finalAPI.initRealtime = initRealtimeConnection;
  finalAPI.subscribeRealtime = subscribeRealtime;
  finalAPI.unsubscribeRealtime = unsubscribeRealtime;
  finalAPI.getRealtimeState = getRealtimeState;
  finalAPI.getOddsHistory = getOddsHistory;
  finalAPI.generateRealtimeDashboard = generateRealtimeDashboard;
  finalAPI.pushNotification = pushNotification;
    finalAPI.detectOddsModelConflict = detectOddsModelConflict;
    finalAPI.parseSquadDepth = parseSquadDepth;
    finalAPI.applySquadDepthAdjustment = applySquadDepthAdjustment;
    finalAPI.restoreTeamOriginalStats = restoreTeamOriginalStats;
    finalAPI.parseRecentForm = parseRecentForm;
    finalAPI.parseTacticalIntel = parseTacticalIntel;
    finalAPI.parseHistoricalH2H = parseHistoricalH2H;
    finalAPI.parsePlayerStats = parsePlayerStats;
    finalAPI.parseWeather = parseWeather;
    finalAPI.injectOddsIntoModel = injectOddsIntoModel;
    finalAPI.getModelVersion = function() { return 'v7.0-Bayesian'; };
    
    // v7.0: 贝叶斯框架接
    finalAPI.mcmcSamplePosterior = mcmcSamplePosterior;
    finalAPI.bayesianPredictMatch = bayesianPredictMatch;
    finalAPI.calculateImpliedProbabilities = calculateImpliedProbabilities;
    
    // v7.0: 首发阵容微调接口
    finalAPI.calculateLineupLambdaAdjustment = calculateLineupLambdaAdjustment;
    finalAPI.applyLineupCorrection = applyLineupCorrection;
    
    // v7.0: 市场差值分析接
    finalAPI.analyzeMarketValue = analyzeMarketValue;
    finalAPI.analyzeOverUnderValue = analyzeOverUnderValue;
    finalAPI.kellyCriterion = kellyCriterion;
    
    // v7.1: 实时统计数据接口
    finalAPI.getLiveStatsSummary = getLiveStatsSummary;
    finalAPI.calculateLiveStatsFactor = calculateLiveStatsFactor;
    finalAPI.updateTeamAttributesFromLiveStats = updateTeamAttributesFromLiveStats;
    finalAPI.updateAllTeamsFromLiveStats = updateAllTeamsFromLiveStats;
    finalAPI.getTEAMS = function() { return TEAMS; };
    finalAPI.calculatePlayerImpact = calculatePlayerImpact;
    finalAPI.PLAYER_POSTMATCH_RATINGS = PLAYER_POSTMATCH_RATINGS;

    // v6.3 FIX-5: 复盘反馈通道 暴露复盘脚本可调用的接口
    // review_*.js 脚本现在可以通过这些方法将复盘结果写回模
    finalAPI.feedbackMatchResult = function(teamAKey, teamBKey, scoreA, scoreB, options) {
      // 完整复盘反馈: 更新Elo + Form + Cohesion + SSM + Brier + 持久
      return updateAfterMatch(teamAKey, teamBKey, scoreA, scoreB, options);
    };

    finalAPI.feedbackTreeLearning = function(teamAKey, teamBKey, actualOutcomeIdx, options) {
      // 树模型在线学 基于比赛结果微调XGB/LGB叶子
      var features = buildXGBFeatures30(teamAKey, teamBKey, options);
      onlineLearnTreeLeaves('xgboost', features, actualOutcomeIdx);
      onlineLearnTreeLeaves('lightgbm', features, actualOutcomeIdx);
      return { xgbCorrections: TREE_LEAF_CORRECTIONS.xgboost, lgbCorrections: TREE_LEAF_CORRECTIONS.lightgbm };
    };

    finalAPI.feedbackAdjustTeam = function(teamKey, adjustments) {
      // 手动调整球队参数 (用于复盘发现的基础数据修正)
      // adjustments: { attack: delta, defence: delta, recentForm: value, cohesion: value }
      if (!TEAMS[teamKey]) return false;
      var t = TEAMS[teamKey];
      if (typeof adjustments.attack === 'number') t.attack = adjustments.attack;
      if (typeof adjustments.defence === 'number') t.defence = adjustments.defence;
      if (typeof adjustments.recentForm === 'number') t.recentForm = Math.max(-0.3, Math.min(0.3, adjustments.recentForm));
      if (typeof adjustments.cohesion === 'number') t.cohesion = Math.max(0.3, Math.min(1.0, adjustments.cohesion));
      return true;
    };

    finalAPI.feedbackGetLearnedState = function() {
      // 获取当前学习状(用于验证和调
      return {
        eloRatings: ELO_RATINGS,
        brierHistory: MODEL_BRIER_HISTORY,
        treeCorrections: TREE_LEAF_CORRECTIONS,
        ssmState: SSM_STATE,
        dynamicWeights: computeDynamicWeights()
      };
    };

    finalAPI.feedbackPersistState = function() {
      // 手动触发持久
      try {
        var persistData = {
          timestamp: Date.now(),
          eloRatings: ELO_RATINGS,
          teamsSnapshot: {}
        };
        for (var tk in TEAMS) {
          var t = TEAMS[tk];
          persistData.teamsSnapshot[tk] = {
            recentForm: t.recentForm, cohesion: t.cohesion,
            attack: t.attack, defence: t.defence
          };
        }
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem('wc2026_learned_state', JSON.stringify(persistData));
        }
        // v6.3: Node.js环境文件系统持久化后
        try {
          if (typeof require === 'function') {
            var _fs2 = require('fs');
            _fs2.writeFileSync('/tmp/wc2026_learned_state.json', JSON.stringify(persistData));
          }
        } catch(e) {}
        return true;
      } catch(e) { return false; }
    };

    finalAPI.feedbackClearLearnedState = function() {
      // 清除所有学习状(重置为初始
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.removeItem('wc2026_learned_state');
        }
        TREE_LEAF_CORRECTIONS = { xgboost: {}, lightgbm: {} };
        MODEL_BRIER_HISTORY = { poisson: [], dixonCole: [], ssm: [], xgboost: [], lightgbm: [], elo: [] };
        initSSM();
        return true;
      } catch(e) { return false; }
    };

  // ============================================================
  // PHASE 6: 数据质量验证体系 (v6.6)
  // ============================================================

  var DATA_QUALITY_CONFIG = {
    anomalyThreshold: 3,
    completenessThreshold: 0.95,
    consistencyThreshold: 0.05,
    dataSources: ['Statz.ai', 'FBref', 'Understat', 'FIFA', 'Opta'],
    validationRules: {
      attack: { min: 0.5, max: 5.0 },
      defence: { min: 0.3, max: 2.0 },
      xGOT: { min: 0.5, max: 3.0 },
      xT: { min: 0.1, max: 0.5 },
      cohesion: { min: 0.3, max: 1.0 },
      injury: { min: 0.5, max: 1.0 }
    }
  };

  function validateTeamData(teamKey) {
    var team = TEAMS[teamKey];
    if (!team) return { valid: false, errors: ['';

    var errors = [];
    var warnings = [];
    var missingFields = [];

    var requiredFields = ['attack', 'defence', 'xGOT', 'xT', 'xGA', 'cohesion', 'recentForm'];
    for (var i = 0; i < requiredFields.length; i++) {
      if (typeof team[requiredFields[i]] !== 'number') {
        missingFields.push(requiredFields[i]);
      }
    }

    for (var rule in DATA_QUALITY_CONFIG.validationRules) {
      if (typeof team[rule] === 'number') {
        var limits = DATA_QUALITY_CONFIG.validationRules[rule];
        if (team[rule] < limits.min || team[rule] > limits.max) {
          errors.push(rule + '' + team[rule] + '' + limits.min + '-' + limits.max + ')');
        }
      }
    }

    return {
      valid: errors.length === 0 && missingFields.length === 0,
      errors: errors,
      warnings: warnings,
      missingFields: missingFields,
      completeness: (requiredFields.length - missingFields.length) / requiredFields.length
    };
  }

  function detectAnomalies(data, fieldName, teamKey) {
    if (!data || data.length === 0) return [];

    var values = data.map(function(d) { return d[fieldName]; }).filter(function(v) { return typeof v === 'number'; });
    if (values.length < 3) return [];

    var mean = values.reduce(function(s, v) { return s + v; }, 0) / values.length;
    var variance = values.reduce(function(s, v) { return s + Math.pow(v - mean, 2); }, 0) / values.length;
    var stdDev = Math.sqrt(variance);

    var anomalies = [];
    for (var i = 0; i < data.length; i++) {
      var v = data[i][fieldName];
      if (typeof v === 'number') {
        var zScore = Math.abs((v - mean) / stdDev);
        if (zScore > DATA_QUALITY_CONFIG.anomalyThreshold) {
          anomalies.push({
            team: teamKey,
            field: fieldName,
            value: v,
            mean: mean,
            stdDev: stdDev,
            zScore: zScore,
            index: i,
            timestamp: data[i].timestamp
          });
        }
      }
    }

    return anomalies;
  }

  function analyzeDataDistribution(data, fieldName) {
    if (!data || data.length === 0) return null;

    var values = data.map(function(d) { return d[fieldName]; }).filter(function(v) { return typeof v === 'number'; });
    if (values.length === 0) return null;

    values.sort(function(a, b) { return a - b; });

    var mean = values.reduce(function(s, v) { return s + v; }, 0) / values.length;
    var variance = values.reduce(function(s, v) { return s + Math.pow(v - mean, 2); }, 0) / values.length;
    var stdDev = Math.sqrt(variance);
    var min = values[0];
    var max = values[values.length - 1];
    var median = values.length % 2 === 0 ?
      (values[values.length / 2 - 1] + values[values.length / 2]) / 2 :
      values[Math.floor(values.length / 2)];

    var q1 = values[Math.floor(values.length * 0.25)];
    var q3 = values[Math.floor(values.length * 0.75)];
    var iqr = q3 - q1;

    var skewness = values.reduce(function(s, v) {
      return s + Math.pow((v - mean) / stdDev, 3);
    }, 0) / values.length;

    var kurtosis = values.reduce(function(s, v) {
      return s + Math.pow((v - mean) / stdDev, 4);
    }, 0) / values.length - 3;

    return {
      field: fieldName,
      count: values.length,
      mean: mean,
      median: median,
      stdDev: stdDev,
      min: min,
      max: max,
      range: max - min,
      q1: q1,
      q3: q3,
      iqr: iqr,
      skewness: skewness,
      kurtosis: kurtosis,
      cv: stdDev / mean
    };
  }

  function generateDataQualityReport() {
    var report = {
      timestamp: new Date().toISOString(),
      summary: { totalTeams: 0, validTeams: 0, warnings: 0, errors: 0 },
      teamReports: {},
      anomalies: [],
      distributions: {},
      completeness: {}
    };

    report.summary.totalTeams = Object.keys(TEAMS).length;

    for (var key in TEAMS) {
      var validation = validateTeamData(key);
      report.teamReports[key] = validation;

      if (validation.valid) report.summary.validTeams++;
      report.summary.warnings += validation.warnings.length;
      report.summary.errors += validation.errors.length;
    }

    var teamValues = [];
    for (var tk in TEAMS) {
      teamValues.push({ attack: TEAMS[tk].attack, defence: TEAMS[tk].defence, xGOT: TEAMS[tk].xGOT });
    }

    report.distributions.attack = analyzeDataDistribution(teamValues, 'attack');
    report.distributions.defence = analyzeDataDistribution(teamValues, 'defence');
    report.distributions.xGOT = analyzeDataDistribution(teamValues, 'xGOT');

    report.completeness.overall = report.summary.validTeams / report.summary.totalTeams;
    report.completeness.threshold = DATA_QUALITY_CONFIG.completenessThreshold;
    report.completeness.pass = report.completeness.overall >= report.completeness.threshold;

    return report;
  }

  // ============================================================
  // PHASE 7: 模型校准体系 (v6.6)
  // ============================================================

  var CALIBRATION_CONFIG = {
    plattLearningRate: 0.01,
    plattIterations: 100,
    isotonicBins: 10,
    minCalibrationSamples: 20,
    calibrationUpdateInterval: 5
  };

  var CALIBRATION_STATE = {
    plattParams: { A: 0, B: 0 },
    isotonicBins: [],
    lastUpdated: null,
    sampleCount: 0
  };

  function sigmoid(x) {
    if (typeof FiveLeagues_Calibration !== 'undefined') return FiveLeagues_Calibration.sigmoid(x);
    return 1 / (1 + Math.exp(-x));
  }

  function trainPlattScaling(predictions, outcomes) {
    if (typeof FiveLeagues_Calibration !== 'undefined') return FiveLeagues_Calibration.trainPlattScaling(predictions, outcomes, CALIBRATION_CONFIG);
    return { A: 0, B: 0, error: 'Calibration module not loaded' };
  }

  function trainIsotonicRegression(predictions, outcomes) {
    if (typeof FiveLeagues_Calibration !== 'undefined') return FiveLeagues_Calibration.trainIsotonicRegression(predictions, outcomes, CALIBRATION_CONFIG);
    return { bins: [], error: 'Calibration module not loaded' };
  }

  function applyCalibration(prob, method) {
    if (typeof FiveLeagues_Calibration !== 'undefined') return FiveLeagues_Calibration.applyCalibration(prob, method, CALIBRATION_STATE);
    return prob;
  }

  function updateCalibrationModel(predictions, outcomes) {
    if (typeof FiveLeagues_Calibration !== 'undefined') {
      var result = FiveLeagues_Calibration.updateCalibrationModel(predictions, outcomes, CALIBRATION_CONFIG, CALIBRATION_STATE);
      CALIBRATION_STATE.plattParams = result.plattParams;
      CALIBRATION_STATE.isotonicBins = result.isotonicBins;
      CALIBRATION_STATE.lastUpdated = result.lastUpdated;
      CALIBRATION_STATE.sampleCount = result.sampleCount;
      return result;
    }
    return null;
  }

  function generateCalibrationReport() {
    if (typeof FiveLeagues_Calibration !== 'undefined') return FiveLeagues_Calibration.generateCalibrationReport(CALIBRATION_STATE, CALIBRATION_CONFIG);
    return { status: '', recommendations: [''] };
  }

  // ============================================================
  // PHASE 8: 蒙特卡洛模拟优化 (v6.6)
  // ============================================================

  var MONTE_CARLO_CONFIG = {
    defaultSimulations: 10000,
    minSimulations: 5000,
    maxSimulations: 50000,
    convergenceThreshold: 0.001,
    randomSeed: 42
  };

  var mcRandomState = MONTE_CARLO_CONFIG.randomSeed;

  function mcRandom() {
    var x = Math.sin(mcRandomState++) * 10000;
    return x - Math.floor(x);
  }

  function simulateWithConvergence(teamAKey, teamBKey, options) {
    options = options || {};
    var targetSimulations = options.simulations || MONTE_CARLO_CONFIG.defaultSimulations;
    var convergenceCheckInterval = Math.max(1000, Math.floor(targetSimulations / 10));

    var results = [];
    var prevWinA = 0, prevDraw = 0, prevWinB = 0;
    var converged = false;

    for (var i = 1; i <= targetSimulations; i++) {
      var result = simulateMatch(teamAKey, teamBKey, options);
      results.push(result);

      if (i % convergenceCheckInterval === 0 && i >= 5000) {
        var winA = results.filter(function(r) { return r.goalsA > r.goalsB; }).length / i;
        var draw = results.filter(function(r) { return r.goalsA === r.goalsB; }).length / i;
        var winB = results.filter(function(r) { return r.goalsA < r.goalsB; }).length / i;

        var diffWinA = Math.abs(winA - prevWinA);
        var diffDraw = Math.abs(draw - prevDraw);
        var diffWinB = Math.abs(winB - prevWinB);

        if (diffWinA < MONTE_CARLO_CONFIG.convergenceThreshold &&
            diffDraw < MONTE_CARLO_CONFIG.convergenceThreshold &&
            diffWinB < MONTE_CARLO_CONFIG.convergenceThreshold) {
          converged = true;
          break;
        }

        prevWinA = winA;
        prevDraw = draw;
        prevWinB = winB;
      }
    }

    var finalWinA = results.filter(function(r) { return r.goalsA > r.goalsB; }).length / results.length;
    var finalDraw = results.filter(function(r) { return r.goalsA === r.goalsB; }).length / results.length;
    var finalWinB = results.filter(function(r) { return r.goalsA < r.goalsB; }).length / results.length;

    var scoreDistribution = {};
    for (var i = 0; i < results.length; i++) {
      var key = results[i].goalsA + '-' + results[i].goalsB;
      scoreDistribution[key] = (scoreDistribution[key] || 0) + 1;
    }

    return {
      winA: finalWinA,
      draw: finalDraw,
      winB: finalWinB,
      simulations: results.length,
      converged: converged,
      scoreDistribution: scoreDistribution,
      convergenceDetails: {
        targetSimulations: targetSimulations,
        actualSimulations: results.length,
        convergedEarly: converged && results.length < targetSimulations
      }
    };
  }

  function analyzeSimulationConvergence(teamAKey, teamBKey, options) {
    options = options || {};
    var testPoints = [5000, 10000, 20000, 30000, 50000];
    var results = [];

    for (var i = 0; i < testPoints.length; i++) {
      var simResult = simulateWithConvergence(teamAKey, teamBKey, {
        simulations: testPoints[i],
        neutral: options.neutral
      });
      results.push({
        simulations: testPoints[i],
        winA: simResult.winA,
        draw: simResult.draw,
        winB: simResult.winB,
        converged: simResult.converged
      });
    }

    var stdDevs = { winA: 0, draw: 0, winB: 0 };
    for (var key in stdDevs) {
      var mean = results.reduce(function(s, r) { return s + r[key]; }, 0) / results.length;
      stdDevs[key] = Math.sqrt(results.reduce(function(s, r) { return s + Math.pow(r[key] - mean, 2); }, 0) / results.length);
    }

    return {
      convergenceResults: results,
      stability: stdDevs,
      recommendedSimulations: 10000,
      summary: ''
    };
  }

  function compareSimulationMethods(teamAKey, teamBKey, options) {
    var mcResult = simulateWithConvergence(teamAKey, teamBKey, options);

    var poissonResult = predictMatch(teamAKey, teamBKey, options);

    return {
      monteCarlo: {
        winA: mcResult.winA,
        draw: mcResult.draw,
        winB: mcResult.winB,
        simulations: mcResult.simulations
      },
      poisson: {
        winA: poissonResult.winA,
        draw: poissonResult.draw,
        winB: poissonResult.winB
      },
      differences: {
        winA: Math.abs(mcResult.winA - poissonResult.winA),
        draw: Math.abs(mcResult.draw - poissonResult.draw),
        winB: Math.abs(mcResult.winB - poissonResult.winB)
      }
    };
  }

  // ============================================================
  // PHASE 9: 质量控制与警报机(v6.6)
  // ============================================================

  var QUALITY_CONTROL_CONFIG = {
    thresholds: {
      brier: { excellent: 0.20, good: 0.35, medium: 0.50, poor: 0.80 },
      accuracy: { excellent: 0.75, good: 0.65, medium: 0.55, poor: 0.45 },
      calibrationError: { excellent: 0.05, good: 0.10, medium: 0.15, poor: 0.20 },
      dataCompleteness: { excellent: 0.98, good: 0.95, medium: 0.90, poor: 0.85 }
    },
    alertRules: {
      brierConsecutive: { threshold: 0.50, consecutive: 3, severity: 'warning' },
      accuracyConsecutive: { threshold: 0.50, consecutive: 5, severity: 'critical' },
      dataCompleteness: { threshold: 0.85, severity: 'warning' },
      calibrationDrift: { threshold: 0.15, severity: 'warning' }
    },
    reviewSchedule: {
      daily: true,
      weekly: true,
      afterMajorEvents: true
    }
  };

  var QUALITY_ALERTS = [];

  function evaluatePredictionQuality(predictions, actualOutcomes) {
    if (typeof FiveLeagues_QualityControl !== 'undefined') return FiveLeagues_QualityControl.evaluatePredictionQuality(predictions, actualOutcomes, QUALITY_CONTROL_CONFIG);
    return null;
  }

  function checkAlerts(qualityReport) {
    if (typeof FiveLeagues_QualityControl !== 'undefined') {
      var alerts = FiveLeagues_QualityControl.checkAlerts(qualityReport, QUALITY_CONTROL_CONFIG);
      QUALITY_ALERTS = QUALITY_ALERTS.concat(alerts);
      if (QUALITY_ALERTS.length > 100) QUALITY_ALERTS = QUALITY_ALERTS.slice(-100);
      return alerts;
    }
    return [];
  }

  function generateQualityControlReport() {
    var recentPredictions = PREDICTION_LOG.slice(-20);
    var recentResults = COMPLETED_MATCH_ARCHIVE.slice(-20);

    var predictions = [];
    var outcomes = [];

    for (var i = 0; i < recentPredictions.length; i++) {
      var pred = recentPredictions[i];
      var result = recentResults.find(function(r) {
        return r.teamA === pred.teamA && r.teamB === pred.teamB;
      });

      if (result) {
        predictions.push({ winA: pred.winA, draw: pred.draw, winB: pred.winB });
        outcomes.push({
          winA: result.scoreA > result.scoreB ? 1 : 0,
          draw: result.scoreA === result.scoreB ? 1 : 0,
          winB: result.scoreA < result.scoreB ? 1 : 0
        });
      }
    }

    var qualityReport = predictions.length > 0 ? evaluatePredictionQuality(predictions, outcomes) : null;
    var alerts = qualityReport ? checkAlerts(qualityReport) : [];

    var dataQuality = generateDataQualityReport();

    return {
      timestamp: new Date().toISOString(),
      qualityMetrics: qualityReport,
      alerts: alerts,
      dataQuality: dataQuality,
      calibrationStatus: generateCalibrationReport(),
      systemHealth: {
        predictionLogSize: PREDICTION_LOG.length,
        completedMatches: COMPLETED_MATCH_ARCHIVE.length,
        brierHistorySize: Object.keys(MODEL_BRIER_HISTORY).reduce(function(s, k) { return s + MODEL_BRIER_HISTORY[k].length; }, 0)
      }
    };
  }

  function triggerModelReview(reason) {
    var report = generateQualityControlReport();

    pushNotification({
      type: 'model_review',
      message: '' + reason,
      severity: 'critical',
      report: report
    });

    return {
      triggered: true,
      reason: reason,
      report: report,
      timestamp: new Date().toISOString()
    };
  }

  // ============================================================
  // PHASE 10: 完整预测流程 (v6.6)
  // ============================================================

  function executeFullPredictionPipeline(teamAKey, teamBKey, options) {
    options = options || {};

    var pipeline = {
      phase1: { name: '', status: 'pending', result: null },
      phase2: { name: ''pending', result: null },
      phase3: { name: '', status: 'pending', result: null },
      phase4: { name: '', status: 'pending', result: null },
      phase5: { name: '', status: 'pending', result: null }
    };

    pipeline.phase1.status = 'running';
    var dataQuality = validateTeamData(teamAKey);
    var dataQualityB = validateTeamData(teamBKey);
    pipeline.phase1.result = {
      teamA: dataQuality,
      teamB: dataQualityB,
      overall: dataQuality.valid && dataQualityB.valid
    };
    pipeline.phase1.status = pipeline.phase1.result.overall ? 'passed' : 'warning';

    pipeline.phase2.status = 'running';
    var calibrationReport = generateCalibrationReport();
    pipeline.phase2.result = calibrationReport;
    pipeline.phase2.status = calibrationReport.status === ''passed' : 'warning'';

    pipeline.phase3.status = 'running';
    var prediction = predictMatch(teamAKey, teamBKey, options);

    if (CALIBRATION_STATE.lastUpdated) {
      prediction.winA = applyCalibration(prediction.winA, 'platt');
      prediction.draw = applyCalibration(prediction.draw, 'platt');
      prediction.winB = applyCalibration(prediction.winB, 'platt');

      var sum = prediction.winA + prediction.draw + prediction.winB;
      prediction.winA /= sum;
      prediction.draw /= sum;
      prediction.winB /= sum;
    }

    pipeline.phase3.result = prediction;
    pipeline.phase3.status = 'passed';

    pipeline.phase4.status = 'running';
    var mcResult = simulateWithConvergence(teamAKey, teamBKey, options);
    pipeline.phase4.result = mcResult;
    pipeline.phase4.status = mcResult.converged ? 'passed' : 'warning';

    pipeline.phase5.status = 'running';
    var qualityReport = evaluatePredictionQuality(
      [{ winA: prediction.winA, draw: prediction.draw, winB: prediction.winB }],
      [{ winA: 0, draw: 0, winB: 0 }]
    );
    pipeline.phase5.result = qualityReport;
    pipeline.phase5.status = 'passed';

    return {
      pipeline: pipeline,
      finalPrediction: prediction,
      monteCarlo: mcResult,
      qualityMetrics: qualityReport,
      dataQuality: pipeline.phase1.result,
      calibrationStatus: calibrationReport,
      confidence: qualityReport ? (qualityReport.brierLevel === 'excellent' || qualityReport.brierLevel === 'good'', content: '' };
    var p = prediction.winDrawWin || prediction.stacked || prediction;
    var rec = p.recommendation || '';
    return {
      title: teamAName + ' vs ' + teamBName + ' - ' + rec,
      content: '' + ((p.winA * 100) || 0).toFixed(1) + '' + ((p.draw * 100) || 0).toFixed(1) + '' + ((p.winB * 100) || 0).toFixed(1) + '%''undefined') {
        FiveLeagues_DataLoader.init().then(function() {
          loadTeamAttributes();
          loadModelConfig();
          _initialized = true;
          console.log('FiveLeaguesEngine initialized');
          resolve();
        }).catch(function(err) {
          console.error('FiveLeaguesEngine init failed:'