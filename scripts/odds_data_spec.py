"""
赔率数据格式规范与训练日志系统
===============================

本文件定义了足球比赛赔率数据的格式规范、数据质量标准、
训练日志系统以及相关工具函数。

使用方式:
    from odds_data_spec import MatchOddsData, TrainingLogger, ODDS_DATA_PROMPT_TEMPLATE

    # 创建赔率数据
    match_data = MatchOddsData(...)

    # 使用训练日志
    logger = TrainingLogger()
    logger.log_data_loading({'total_matches': 1000})
    logger.log_model_training('XGBoost', params, iteration, train_loss, val_loss, metrics)
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
from datetime import datetime

# ==================== 配置常量 ====================

LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

# ==================== 赔率数据字段定义 ====================

@dataclass
class WdlOddsRecord:
    """胜平负赔率记录"""
    timestamp: str    # 发布时间，格式: YYYY-MM-DD HH:MM:SS
    home_odds: float  # 主队胜赔率
    draw_odds: float  # 平局赔率
    away_odds: float  # 客队胜赔率

@dataclass
class HandicapOddsRecord:
    """让球胜平负赔率记录"""
    timestamp: str    # 发布时间，格式: YYYY-MM-DD HH:MM:SS
    handicap: float   # 让球数，负值为主队让球，正值为主队受让（与竞彩 goalLine 口径一致）
    home_odds: float  # 主队胜赔率（让球后）
    draw_odds: float  # 平局赔率（让球后）
    away_odds: float  # 客队胜赔率（让球后）

@dataclass
class TotalGoalsOddsRecord:
    """总进球数赔率记录"""
    timestamp: str       # 发布时间，格式: YYYY-MM-DD HH:MM:SS
    over_05: float       # 大0.5
    under_05: float      # 小0.5
    over_15: float       # 大1.5
    under_15: float      # 小1.5
    over_25: float       # 大2.5
    under_25: float      # 小2.5
    over_35: float       # 大3.5
    under_35: float      # 小3.5
    over_45: float       # 大4.5
    under_45: float      # 小4.5

@dataclass
class ScoreOddsRecord:
    """比分赔率记录"""
    timestamp: str       # 发布时间，格式: YYYY-MM-DD HH:MM:SS
    scores: Dict[str, float]  # 比分:赔率映射，如 {'1:0': 6.75, '2:0': 5.50}

@dataclass
class MatchResult:
    """比赛结果"""
    wdl: str            # 胜平负结果: 'H'(主胜), 'D'(平), 'A'(客胜)
    handicap_result: str # 让球结果
    score: str          # 比分，格式: X:Y
    total_goals: int    # 总进球数

@dataclass
class MatchOddsData:
    """完整比赛赔率数据"""
    match_id: str                    # 比赛唯一标识
    league: str                      # 联赛名称
    season: str                      # 赛季，格式: 2025/2026
    round: str                       # 轮次，格式: 第14轮
    home_team: str                   # 主队名称（英文全称）
    away_team: str                   # 客队名称（英文全称）
    match_date: str                  # 比赛日期，格式: YYYY-MM-DD
    match_time: str                  # 比赛时间，格式: HH:MM
    wdl_odds: List[WdlOddsRecord]    # 胜平负赔率历史
    handicap_odds: List[HandicapOddsRecord]  # 让球赔率历史
    total_goals_odds: List[TotalGoalsOddsRecord]  # 总进球赔率历史
    score_odds: List[ScoreOddsRecord]  # 比分赔率历史
    result: Optional[MatchResult] = None  # 比赛结果（开奖后填充）

# ==================== 数据质量标准 ====================

DATA_QUALITY_STANDARDS = {
    'wdl_min_records': 2,
    'handicap_min_records': 2,
    'total_goals_min_records': 2,
    'score_min_options': 10,
    'odds_min_value': 1.01,
    'odds_max_value': 200.0,
    'max_time_interval_hours': 48,
    'min_time_before_match_hours': 2,
    'required_leagues': ['英超', '西甲', '德甲', '意甲', '法甲'],
    'required_seasons': ['2024/2025', '2025/2026']
}

def validate_odds_data(match_data: MatchOddsData) -> List[str]:
    """验证赔率数据质量"""
    errors = []
    
    # 验证胜平负赔率
    if len(match_data.wdl_odds) < DATA_QUALITY_STANDARDS['wdl_min_records']:
        errors.append(f"胜平负赔率记录不足，需至少{DATA_QUALITY_STANDARDS['wdl_min_records']}条")
    
    # 验证让球赔率
    if len(match_data.handicap_odds) < DATA_QUALITY_STANDARDS['handicap_min_records']:
        errors.append(f"让球赔率记录不足，需至少{DATA_QUALITY_STANDARDS['handicap_min_records']}条")
    
    # 验证总进球赔率
    if len(match_data.total_goals_odds) < DATA_QUALITY_STANDARDS['total_goals_min_records']:
        errors.append(f"总进球赔率记录不足，需至少{DATA_QUALITY_STANDARDS['total_goals_min_records']}条")
    
    # 验证比分赔率
    if match_data.score_odds:
        first_record = match_data.score_odds[0]
        if len(first_record.scores) < DATA_QUALITY_STANDARDS['score_min_options']:
            errors.append(f"比分选项不足，需至少{DATA_QUALITY_STANDARDS['score_min_options']}个")
    
    # 验证赔率值范围
    for record in match_data.wdl_odds:
        for odds in [record.home_odds, record.draw_odds, record.away_odds]:
            if odds < DATA_QUALITY_STANDARDS['odds_min_value'] or odds > DATA_QUALITY_STANDARDS['odds_max_value']:
                errors.append(f"赔率值超出范围: {odds}")
    
    # 验证时间顺序
    for i, record in enumerate(match_data.wdl_odds[1:], 1):
        if record.timestamp < match_data.wdl_odds[i-1].timestamp:
            errors.append("赔率记录时间戳未按时间顺序排列")
            break
    
    return errors

# ==================== 赔率数据Prompt模板 ====================

ODDS_DATA_PROMPT_TEMPLATE = """
你需要提供完整的足球比赛赔率数据用于模型训练。请按照以下格式提供：

## 基本信息
- 联赛: {league}
- 赛季: {season}
- 轮次: {round}
- 主队: {home_team}
- 客队: {away_team}
- 比赛日期: {match_date}
- 比赛时间: {match_time}

## 胜平负固定奖金 (WDL)
发布时间\t胜\t平\t负
{wdl_odds_table}

## 让球胜平负固定奖金 (Handicap)
让球{handicap_value}
发布时间\t胜\t平\t负
{handicap_odds_table}

## 总进球固定奖金 (Total Goals)
发布时间\t大0.5\t小0.5\t大1.5\t小1.5\t大2.5\t小2.5\t大3.5\t小3.5\t大4.5\t小4.5
{total_goals_odds_table}

## 比分固定奖金 (Score)
发布时间\t{score_columns}
{score_odds_table}

## 开奖结果
游戏\t开奖结果\t奖金
胜平负\t{actual_wdl}\t{wdl_odds_last}
让球胜平负\t{actual_handicap}\t{handicap_odds_last}
比分\t{actual_score}\t{score_odds_last}
总进球\t{actual_total_goals}\t{total_goals_odds_last}

## 数据质量要求
1. 赔率记录时间戳必须按时间顺序排列（从早到晚）
2. 最后一条赔率记录必须在比赛开始前至少2小时
3. 赔率值范围: {odds_min} - {odds_max}
4. 胜平负赔率至少需要{wdl_min}条记录
5. 让球赔率至少需要{handicap_min}条记录
6. 总进球赔率至少需要{total_goals_min}条记录
7. 比分赔率至少需要{score_min}个比分选项
8. 各时间戳之间的时间间隔不应超过{max_interval}小时
"""

def generate_odds_prompt(match_data: MatchOddsData) -> str:
    """生成赔率数据Prompt"""
    # 格式化胜平负赔率表格
    wdl_rows = []
    for record in match_data.wdl_odds:
        wdl_rows.append(f"{record.timestamp}\t{record.home_odds:.2f}\t{record.draw_odds:.2f}\t{record.away_odds:.2f}")
    
    # 格式化让球赔率表格
    handicap_value = ""
    handicap_rows = []
    if match_data.handicap_odds:
        handicap_value = str(match_data.handicap_odds[0].handicap)
        for record in match_data.handicap_odds:
            handicap_rows.append(f"{record.timestamp}\t{record.home_odds:.2f}\t{record.draw_odds:.2f}\t{record.away_odds:.2f}")
    
    # 格式化总进球赔率表格
    tg_rows = []
    for record in match_data.total_goals_odds:
        tg_rows.append(f"{record.timestamp}\t{record.over_05:.2f}\t{record.under_05:.2f}\t{record.over_15:.2f}\t{record.under_15:.2f}\t{record.over_25:.2f}\t{record.under_25:.2f}\t{record.over_35:.2f}\t{record.under_35:.2f}\t{record.over_45:.2f}\t{record.under_45:.2f}")
    
    # 格式化比分赔率表格
    score_columns = ""
    score_rows = []
    if match_data.score_odds:
        score_columns = "\t".join(match_data.score_odds[0].scores.keys())
        for record in match_data.score_odds:
            score_values = "\t".join(f"{v:.2f}" for v in record.scores.values())
            score_rows.append(f"{record.timestamp}\t{score_values}")
    
    # 获取最后赔率
    last_wdl = match_data.wdl_odds[-1] if match_data.wdl_odds else None
    last_handicap = match_data.handicap_odds[-1] if match_data.handicap_odds else None
    last_score = match_data.score_odds[-1] if match_data.score_odds else None
    
    return ODDS_DATA_PROMPT_TEMPLATE.format(
        league=match_data.league,
        season=match_data.season,
        round=match_data.round,
        home_team=match_data.home_team,
        away_team=match_data.away_team,
        match_date=match_data.match_date,
        match_time=match_data.match_time,
        wdl_odds_table="\n".join(wdl_rows),
        handicap_value=handicap_value,
        handicap_odds_table="\n".join(handicap_rows),
        total_goals_odds_table="\n".join(tg_rows),
        score_columns=score_columns,
        score_odds_table="\n".join(score_rows),
        actual_wdl=match_data.result.wdl if match_data.result else "N/A",
        wdl_odds_last=f"{last_wdl.home_odds:.2f}" if last_wdl else "N/A",
        actual_handicap=match_data.result.handicap_result if match_data.result else "N/A",
        handicap_odds_last=f"{last_handicap.draw_odds:.2f}" if last_handicap else "N/A",
        actual_score=match_data.result.score if match_data.result else "N/A",
        score_odds_last=f"{list(last_score.scores.values())[0]:.2f}" if last_score else "N/A",
        actual_total_goals=str(match_data.result.total_goals) if match_data.result else "N/A",
        total_goals_odds_last="3.60",
        odds_min=DATA_QUALITY_STANDARDS['odds_min_value'],
        odds_max=DATA_QUALITY_STANDARDS['odds_max_value'],
        wdl_min=DATA_QUALITY_STANDARDS['wdl_min_records'],
        handicap_min=DATA_QUALITY_STANDARDS['handicap_min_records'],
        total_goals_min=DATA_QUALITY_STANDARDS['total_goals_min_records'],
        score_min=DATA_QUALITY_STANDARDS['score_min_options'],
        max_interval=DATA_QUALITY_STANDARDS['max_time_interval_hours']
    )

# ==================== 训练日志系统 ====================

@dataclass
class TrainingLog:
    """训练日志条目"""
    timestamp: str
    stage: str
    action: str
    details: Dict = field(default_factory=dict)
    metrics: Dict = field(default_factory=dict)
    model_params: Dict = field(default_factory=dict)
    data_info: Dict = field(default_factory=dict)
    error: Optional[str] = None

class TrainingLogger:
    """训练日志管理器"""
    
    def __init__(self, log_name: str = None):
        self.log_name = log_name or f"training_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.log_file = os.path.join(LOG_DIR, f"{self.log_name}.json")
        self.log_entries: List[TrainingLog] = []
        self.start_time = datetime.now()
        self.loss_history: Dict[str, List[Dict]] = {}  # 损失曲线历史
    
    def log(self, stage: str, action: str, **kwargs):
        """记录日志条目"""
        entry = TrainingLog(
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            stage=stage,
            action=action,
            details=kwargs.get('details', {}),
            metrics=kwargs.get('metrics', {}),
            model_params=kwargs.get('model_params', {}),
            data_info=kwargs.get('data_info', {}),
            error=kwargs.get('error')
        )
        self.log_entries.append(entry)
        self._save()
        
        print(f"[{stage}] {action} - {kwargs.get('details', {})}")
    
    def log_data_loading(self, data_info: Dict):
        """记录数据加载信息"""
        self.log(
            stage="DATA_LOADING",
            action="数据加载完成",
            data_info=data_info
        )
    
    def log_feature_engineering(self, feature_info: Dict):
        """记录特征工程信息"""
        self.log(
            stage="FEATURE_ENGINEERING",
            action="特征工程完成",
            details=feature_info
        )
    
    def log_model_training(self, model_name: str, params: Dict, iteration: int, 
                          train_loss: float, val_loss: float, metrics: Dict):
        """记录模型训练迭代"""
        # 保存损失历史
        if model_name not in self.loss_history:
            self.loss_history[model_name] = []
        self.loss_history[model_name].append({
            'iteration': iteration,
            'train_loss': train_loss,
            'val_loss': val_loss
        })
        
        self.log(
            stage="MODEL_TRAINING",
            action=f"{model_name} 训练迭代",
            details={
                'model_name': model_name,
                'iteration': iteration
            },
            model_params=params,
            metrics={
                'train_loss': train_loss,
                'val_loss': val_loss,
                **metrics
            }
        )
    
    def log_loss_curve(self, model_name: str, iterations: List[int], 
                       train_losses: List[float], val_losses: List[float]):
        """记录完整损失曲线"""
        self.loss_history[model_name] = [
            {'iteration': i, 'train_loss': tl, 'val_loss': vl}
            for i, tl, vl in zip(iterations, train_losses, val_losses)
        ]
        
        # 记录最终损失曲线
        self.log(
            stage="LOSS_CURVE",
            action=f"{model_name} 损失曲线记录",
            details={
                'model_name': model_name,
                'total_iterations': len(iterations),
                'min_train_loss': min(train_losses),
                'min_val_loss': min(val_losses),
                'final_train_loss': train_losses[-1] if train_losses else 0,
                'final_val_loss': val_losses[-1] if val_losses else 0
            },
            metrics={
                'iterations': iterations,
                'train_losses': train_losses,
                'val_losses': val_losses
            }
        )
    
    def plot_loss_curve(self, model_name: str = None):
        """生成损失曲线图表"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(1, len(self.loss_history) if model_name is None else 1, 
                                    figsize=(12, 6) if model_name is None else (10, 6))
            
            if model_name is None:
                models_to_plot = self.loss_history.keys()
                if len(models_to_plot) == 1:
                    axes = [axes]
            else:
                models_to_plot = [model_name]
                if not isinstance(axes, list):
                    axes = [axes]
            
            for ax, name in zip(axes, models_to_plot):
                if name not in self.loss_history:
                    continue
                
                history = self.loss_history[name]
                iterations = [h['iteration'] for h in history]
                train_losses = [h['train_loss'] for h in history]
                val_losses = [h['val_loss'] for h in history]
                
                ax.plot(iterations, train_losses, label=f'{name} Train Loss', color='#4CAF50')
                ax.plot(iterations, val_losses, label=f'{name} Val Loss', color='#F44336')
                
                ax.set_xlabel('Iteration')
                ax.set_ylabel('Loss')
                ax.set_title(f'{name} Training Loss Curve')
                ax.legend()
                ax.grid(True, alpha=0.3)
            
            plt.tight_layout()
            plot_path = os.path.join(LOG_DIR, f"{self.log_name}_loss_curve.png")
            plt.savefig(plot_path, dpi=150)
            plt.close()
            
            self.log(
                stage="VISUALIZATION",
                action="损失曲线图表生成",
                details={'plot_path': plot_path}
            )
            
            return plot_path
        except ImportError:
            self.log_error("VISUALIZATION", "matplotlib not installed, cannot generate loss curve plot")
            return None
    
    def log_hyperparameter_update(self, model_name: str, old_params: Dict, new_params: Dict):
        """记录超参数调整"""
        changes = {k: f"{old_params.get(k)} -> {v}" for k, v in new_params.items() if old_params.get(k) != v}
        self.log(
            stage="HYPERPARAMETER",
            action=f"{model_name} 超参数调整",
            details={
                'model_name': model_name,
                'old_params': old_params,
                'new_params': new_params,
                'changes': changes
            }
        )
    
    def log_evaluation(self, stage: str, metrics: Dict):
        """记录评估结果"""
        self.log(
            stage="EVALUATION",
            action=f"{stage} 评估完成",
            metrics=metrics
        )
    
    def log_error(self, stage: str, error_msg: str):
        """记录错误信息"""
        self.log(
            stage=stage,
            action="ERROR",
            error=error_msg
        )
    
    def log_training_completion(self, summary: Dict):
        """记录训练完成"""
        duration = (datetime.now() - self.start_time).total_seconds()
        
        # 生成损失曲线图表
        if self.loss_history:
            self.plot_loss_curve()
        
        self.log(
            stage="COMPLETION",
            action="训练完成",
            details={
                'duration_seconds': duration,
                'duration_human': str(datetime.now() - self.start_time),
                **summary
            }
        )
    
    def _save(self):
        """保存日志到文件"""
        log_data = [entry.__dict__ for entry in self.log_entries]
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, ensure_ascii=False, indent=2)
    
    def get_log_summary(self) -> str:
        """获取日志摘要"""
        summary = []
        summary.append(f"训练日志: {self.log_name}")
        summary.append(f"日志文件: {self.log_file}")
        summary.append(f"日志条目数: {len(self.log_entries)}")
        summary.append(f"持续时间: {datetime.now() - self.start_time}")
        summary.append("-" * 50)
        
        stages = {}
        for entry in self.log_entries:
            if entry.stage not in stages:
                stages[entry.stage] = []
            stages[entry.stage].append(entry)
        
        for stage, entries in stages.items():
            summary.append(f"\n【{stage}】")
            for entry in entries:
                line = f"  {entry.timestamp} - {entry.action}"
                if entry.metrics:
                    # 简化显示指标
                    if isinstance(entry.metrics, dict):
                        simple_metrics = {k: v for k, v in entry.metrics.items() 
                                         if k in ['accuracy', 'log_loss', 'brier', 'train_loss', 'val_loss']}
                        if simple_metrics:
                            line += f" | {simple_metrics}"
                if entry.details and entry.details.get('changes'):
                    line += f" | 参数变化: {entry.details['changes']}"
                if entry.error:
                    line += f" | ERROR: {entry.error}"
                summary.append(line)
        
        return "\n".join(summary)

# ==================== 训练日志装饰器 ====================

def log_training_stage(stage: str):
    """装饰器：记录训练阶段"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            logger = kwargs.get('logger') or TrainingLogger()
            logger.log(stage, f"开始 {func.__name__}")
            try:
                result = func(*args, **kwargs)
                logger.log(stage, f"完成 {func.__name__}")
                return result
            except Exception as e:
                logger.log_error(stage, str(e))
                raise
        return wrapper
    return decorator

# ==================== 使用示例 ====================

if __name__ == "__main__":
    # 示例1: 生成赔率数据Prompt模板
    sample_wdl = [
        WdlOddsRecord("2025-12-06 10:01:24", 1.20, 5.25, 9.50),
        WdlOddsRecord("2025-12-06 22:38:03", 1.19, 5.45, 9.50)
    ]
    
    sample_handicap = [
        HandicapOddsRecord("2025-12-06 10:01:24", -1, 1.72, 3.75, 3.55),
        HandicapOddsRecord("2025-12-06 22:38:09", -1, 1.69, 3.80, 3.65)
    ]
    
    sample_total_goals = [
        TotalGoalsOddsRecord("2025-12-06 10:01:24", 1.12, 6.50, 1.36, 3.50, 1.95, 2.10, 3.40, 1.50, 6.50, 1.18)
    ]
    
    sample_score = [
        ScoreOddsRecord("2025-12-06 10:01:24", {
            '1:0': 6.75, '2:0': 5.50, '2:1': 7.50, '3:0': 7.50, '0:0': 18.00,
            '1:1': 9.50, '2:2': 21.00, '0:1': 22.00, '0:2': 60.00, '1:2': 25.00
        })
    ]
    
    sample_match = MatchOddsData(
        match_id="20251206_NU_BUR",
        league="英超",
        season="2025/2026",
        round="第15轮",
        home_team="Newcastle United",
        away_team="Burnley",
        match_date="2025-12-06",
        match_time="23:00",
        wdl_odds=sample_wdl,
        handicap_odds=sample_handicap,
        total_goals_odds=sample_total_goals,
        score_odds=sample_score,
        result=MatchResult('H', '(-1)D', '2:1', 3)
    )
    
    # 验证数据质量
    errors = validate_odds_data(sample_match)
    if errors:
        print("数据验证失败:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("数据验证通过")
    
    prompt = ODDS_DATA_PROMPT_TEMPLATE.format(
        league="英超",
        season="2025/2026",
        round="第15轮",
        home_team="Newcastle United",
        away_team="Burnley",
        match_date="2025-12-06",
        match_time="23:00",
        wdl_odds_table="2025-12-06 10:01:24\t1.20\t5.25\t9.50\n2025-12-06 22:38:03\t1.19\t5.45\t9.50",
        handicap_value="-1",
        handicap_odds_table="2025-12-06 10:01:24\t1.72\t3.75\t3.55\n2025-12-06 22:38:09\t1.69\t3.80\t3.65",
        total_goals_odds_table="2025-12-06 10:01:24\t1.12\t6.50\t1.36\t3.50\t1.95\t2.10\t3.40\t1.50\t6.50\t1.18",
        score_columns="1:0\t2:0\t2:1\t3:0\t0:0\t1:1\t2:2\t0:1\t0:2\t1:2",
        score_odds_table="2025-12-06 10:01:24\t6.75\t5.50\t7.50\t7.50\t18.00\t9.50\t21.00\t22.00\t60.00\t25.00",
        actual_wdl="胜",
        wdl_odds_last="1.19",
        actual_handicap="(-1)平",
        handicap_odds_last="3.80",
        actual_score="2:1",
        score_odds_last="7.50",
        actual_total_goals="3",
        total_goals_odds_last="3.60",
        odds_min=1.01,
        odds_max=200.0,
        wdl_min=2,
        handicap_min=2,
        total_goals_min=2,
        score_min=10,
        max_interval=48
    )
    
    print("=" * 60)
    print("赔率数据Prompt模板示例")
    print("=" * 60)
    print(prompt)
    
    # 示例2: 使用训练日志系统
    print("\n" + "=" * 60)
    print("训练日志系统示例")
    print("=" * 60)
    
    logger = TrainingLogger("example_training")
    
    logger.log_data_loading({
        'total_matches': 875,
        'leagues': ['英超', '西甲', '德甲', '意甲', '法甲'],
        'date_range': '2024-08-16 to 2025-12-06',
        'wdl_records': 3500,
        'handicap_records': 3200,
        'total_goals_records': 2800,
        'score_records': 25000
    })
    
    logger.log_feature_engineering({
        'basic_features': 32,
        'team_features': 40,
        'total_features': 72,
        'feature_types': ['统计特征', '历史战绩', '赔率特征', '球队属性']
    })
    
    logger.log_model_training(
        model_name='XGBoost',
        params={'max_depth': 2, 'learning_rate': 0.03, 'n_estimators': 100},
        iteration=50,
        train_loss=0.35,
        val_loss=0.85,
        metrics={'accuracy': 0.78, 'log_loss': 0.85, 'brier': 0.18}
    )
    
    logger.log_hyperparameter_update(
        model_name='XGBoost',
        old_params={'max_depth': 2, 'learning_rate': 0.03},
        new_params={'max_depth': 3, 'learning_rate': 0.05}
    )
    
    logger.log_evaluation('验证集', {
        'accuracy': 0.785,
        'log_loss': 0.845,
        'brier_score': 0.182,
        'wdl_distribution': {'胜': 0.51, '平': 0.21, '负': 0.28},
        'coverage': 1.0
    })
    
    # 模拟训练过程中的损失曲线
    import numpy as np
    iterations = list(range(1, 101))
    train_losses = [0.8 - 0.4 * np.exp(-i/20) + np.random.normal(0, 0.02) for i in iterations]
    val_losses = [0.85 - 0.3 * np.exp(-i/25) + np.random.normal(0, 0.03) for i in iterations]
    logger.log_loss_curve('XGBoost', iterations, train_losses, val_losses)
    
    logger.log_training_completion({
        'models_trained': ['XGBoost', 'LightGBM'],
        'best_model': 'XGBoost',
        'best_accuracy': 0.785,
        'output_dir': 'assets/'
    })
    
    print(logger.get_log_summary())
