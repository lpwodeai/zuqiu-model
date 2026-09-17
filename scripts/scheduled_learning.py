import os
import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

from online_learning_manager import OnlineLearningManager, run_online_learning_check
from multitask_trainer import train_multitask_pipeline

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = os.path.join(LOG_DIR, f'scheduled_learning_{datetime.now().strftime("%Y%m%d")}.log')

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class ScheduledLearningManager:
    def __init__(self, check_interval_hours=24, retrain_interval_hours=168, use_multitask=True):
        self.check_interval_hours = check_interval_hours
        self.retrain_interval_hours = retrain_interval_hours
        self.use_multitask = use_multitask
        self.manager = OnlineLearningManager()
        self.last_retrain_time = None
        self.scheduler = None
        
        if APSCHEDULER_AVAILABLE:
            self.scheduler = BlockingScheduler(timezone='Asia/Shanghai')
    
    def run_daily_check(self):
        """每日检查：性能监测和衰退检测"""
        logger.info("=" * 60)
        logger.info("开始每日在线学习检查")
        logger.info("=" * 60)
        
        try:
            report = run_online_learning_check()
            
            logger.info(f"检查完成:")
            logger.info(f"  总比赛数: {report['performance_summary']['total_matches']}")
            logger.info(f"  整体准确率: {report['performance_summary']['overall_accuracy']:.2%}")
            logger.info(f"  近期准确率: {report['performance_summary']['recent_accuracy']:.2%}")
            logger.info(f"  衰退状态: {'已检测' if report['degradation_status']['degraded'] else '正常'}")
            
            if report['degradation_status']['degraded']:
                logger.warning(f"⚠️ 检测到性能衰退!")
                logger.warning(f"  衰退分数: {report['degradation_status']['degradation_score']:.4f}")
                logger.warning(f"  原因: {report['degradation_status']['reason']}")
                
                self.trigger_retraining(reason="性能衰退检测")
            
        except Exception as e:
            logger.error(f"每日检查失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def trigger_retraining(self, reason="定期更新"):
        """触发模型重新训练"""
        logger.info(f"\n🚀 触发模型重新训练 - {reason}")
        
        try:
            if self.use_multitask:
                logger.info("训练多任务学习模型...")
                trainer, version = train_multitask_pipeline()
                logger.info(f"多任务模型训练完成，版本: {version}")
            else:
                logger.info("训练单任务模型...")
                import subprocess
                result = subprocess.run(
                    [sys.executable, 'train_models.py'],
                    capture_output=True, text=True,
                    cwd=os.path.dirname(os.path.abspath(__file__))
                )
                if result.returncode == 0:
                    logger.info("单任务模型训练完成")
                    logger.info(result.stdout)
                else:
                    logger.error(f"单任务模型训练失败: {result.stderr}")
            
            self.last_retrain_time = datetime.now()
            logger.info(f"✅ 模型重新训练完成，时间: {self.last_retrain_time}")
            
        except Exception as e:
            logger.error(f"模型重新训练失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def run_weekly_retrain(self):
        """每周定期重新训练"""
        logger.info("=" * 60)
        logger.info("开始每周定期模型更新")
        logger.info("=" * 60)
        
        self.trigger_retraining(reason="每周定期更新")
    
    def _run_single_check(self):
        """运行单次检查（用于手动触发或测试）"""
        logger.info("运行单次在线学习检查...")
        self.run_daily_check()
    
    def start_scheduler(self):
        """启动定时调度器"""
        if not self.scheduler:
            logger.error("APScheduler未安装，请先安装: pip install apscheduler")
            return False
        
        logger.info(f"\n启动定时调度器:")
        logger.info(f"  每日检查间隔: {self.check_interval_hours} 小时")
        logger.info(f"  每周重训间隔: {self.retrain_interval_hours} 小时")
        
        # 每日检查 - 每天凌晨3点
        self.scheduler.add_job(
            self.run_daily_check,
            trigger=CronTrigger(hour=3, minute=0),
            id='daily_check',
            name='每日在线学习检查',
            replace_existing=True
        )
        
        # 每周重训 - 每周一凌晨4点
        self.scheduler.add_job(
            self.run_weekly_retrain,
            trigger=CronTrigger(day_of_week='mon', hour=4, minute=0),
            id='weekly_retrain',
            name='每周模型重训',
            replace_existing=True
        )
        
        logger.info("\n定时任务已配置:")
        logger.info("  - 每日检查: 每天 03:00")
        logger.info("  - 每周重训: 每周一 04:00")
        
        try:
            self.scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("\n调度器已停止")
            if self.scheduler:
                self.scheduler.shutdown()
    
    def run_once(self):
        """运行一次检查和训练（用于测试）"""
        logger.info("运行单次检查和训练...")
        self.run_daily_check()
        
        if self.last_retrain_time is None or \
           (datetime.now() - self.last_retrain_time).total_seconds() > self.retrain_interval_hours * 3600:
            self.run_weekly_retrain()

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='定时在线学习管理器')
    parser.add_argument('--mode', type=str, default='once', 
                        choices=['once', 'scheduled', 'check', 'retrain'],
                        help='运行模式: once(单次), scheduled(定时), check(仅检查), retrain(仅重训)')
    parser.add_argument('--interval', type=int, default=24,
                        help='检查间隔（小时）')
    parser.add_argument('--retrain-interval', type=int, default=168,
                        help='重训间隔（小时）')
    parser.add_argument('--multitask', action='store_true', default=True,
                        help='使用多任务学习模型')
    
    args = parser.parse_args()
    
    manager = ScheduledLearningManager(
        check_interval_hours=args.interval,
        retrain_interval_hours=args.retrain_interval,
        use_multitask=args.multitask
    )
    
    if args.mode == 'once':
        manager.run_once()
    elif args.mode == 'scheduled':
        manager.start_scheduler()
    elif args.mode == 'check':
        manager.run_daily_check()
    elif args.mode == 'retrain':
        manager.trigger_retraining(reason="手动触发")

if __name__ == "__main__":
    main()