import os
import sys
import time
import subprocess
import logging
import argparse
from datetime import datetime, timedelta
import sqlite3
import yaml
import psutil
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAIN_SCRIPT = os.path.join(PROJECT_ROOT, 'scripts', 'train_models_v2.py')
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'five_leagues.db')
MODEL_DIR = os.path.join(PROJECT_ROOT, 'assets')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.yaml')
LAST_TRAIN_FILE = os.path.join(PROJECT_ROOT, 'data', 'last_train_time.txt')
LAST_INC_TRAIN_FILE = os.path.join(PROJECT_ROOT, 'data', 'last_inc_train_time.txt')

ANOMALY_THRESHOLD = 0.5
DIVERGENCE_THRESHOLD = 0.5
ERROR_THRESHOLD = 0.8

def load_config():
    """
    加载配置文件
    
    Returns:
        dict: 配置字典，如果配置文件不存在返回空字典
    """
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

logging.basicConfig(
    level=getattr(logging, CONFIG.get('logging', {}).get('level', 'INFO')),
    format=CONFIG.get('logging', {}).get('format', '%(asctime)s - %(levelname)s - %(message)s'),
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, 'logs/auto_train.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def check_resource_usage():
    """
    检查系统资源使用情况
    
    Returns:
        bool: 如果资源使用在限制范围内返回True，否则返回False
    """
    cpu_percent = psutil.cpu_percent(interval=1)
    memory_percent = psutil.virtual_memory().percent
    disk_percent = psutil.disk_usage('/').percent
    
    max_cpu = CONFIG.get('auto_train', {}).get('resource_limits', {}).get('max_cpu', 80)
    max_memory = CONFIG.get('auto_train', {}).get('resource_limits', {}).get('max_memory', 85)
    max_disk = CONFIG.get('auto_train', {}).get('resource_limits', {}).get('max_disk', 90)
    
    if cpu_percent > max_cpu:
        logger.warning(f'CPU使用率过高: {cpu_percent}% > {max_cpu}%')
        return False
    if memory_percent > max_memory:
        logger.warning(f'内存使用率过高: {memory_percent}% > {max_memory}%')
        return False
    if disk_percent > max_disk:
        logger.warning(f'磁盘使用率过高: {disk_percent}% > {max_disk}%')
        return False
    
    logger.info(f'资源使用正常 - CPU: {cpu_percent}%, 内存: {memory_percent}%, 磁盘: {disk_percent}%')
    return True

def check_new_matches(since_date=None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if since_date:
            cursor.execute("""
                SELECT COUNT(*) FROM matches 
                WHERE date >= ? AND homeGoals IS NOT NULL AND awayGoals IS NOT NULL
            """, (since_date,))
            count = cursor.fetchone()[0]
        else:
            one_week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            cursor.execute("""
                SELECT COUNT(*) FROM matches 
                WHERE date >= ? AND homeGoals IS NOT NULL AND awayGoals IS NOT NULL
            """, (one_week_ago,))
            count = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM matches 
            WHERE homeGoals IS NOT NULL AND awayGoals IS NOT NULL
        """)
        total = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT MAX(date) FROM matches 
            WHERE homeGoals IS NOT NULL AND awayGoals IS NOT NULL
        """)
        latest_date = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'new_matches': count,
            'total_matches': total,
            'latest_date': latest_date
        }
    except Exception as e:
        logger.error(f'检查新比赛数据失败: {e}')
        return {'new_matches': 0, 'total_matches': 0, 'latest_date': None}

def get_last_train_time(file_path):
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'r') as f:
            last_train_str = f.read().strip()
            return datetime.fromisoformat(last_train_str)
    except Exception as e:
        logger.error(f'读取上次训练时间失败: {e}')
        return None

def update_last_train_time(file_path):
    try:
        with open(file_path, 'w') as f:
            f.write(datetime.now().isoformat())
        logger.info(f'已更新上次训练时间: {file_path}')
    except Exception as e:
        logger.error(f'更新训练时间失败: {e}')

def should_train():
    schedule_config = CONFIG.get('auto_train', {}).get('schedule', {})
    schedule_type = schedule_config.get('type', 'daily')
    interval_hours = schedule_config.get('interval_hours', 24)
    
    last_train_time = get_last_train_time(LAST_TRAIN_FILE)
    
    if last_train_time is None:
        logger.info('首次训练，开始执行')
        return True
    
    if schedule_type == 'daily':
        time_diff = datetime.now() - last_train_time
        return time_diff.total_seconds() >= interval_hours * 3600
    elif schedule_type == 'weekly':
        time_diff = datetime.now() - last_train_time
        return time_diff.total_seconds() >= 7 * 24 * 3600
    elif schedule_type == 'hourly':
        time_diff = datetime.now() - last_train_time
        return time_diff.total_seconds() >= 3600
    else:
        logger.warning(f'未知的调度类型: {schedule_type}')
        return True

def should_incremental_train():
    if not CONFIG.get('training', {}).get('incremental', {}).get('enabled', False):
        return False
    
    last_inc_train_time = get_last_train_time(LAST_INC_TRAIN_FILE)
    
    if last_inc_train_time is None:
        return True
    
    time_diff = datetime.now() - last_inc_train_time
    incremental_interval = CONFIG.get('training', {}).get('incremental', {}).get('interval_hours', 6)
    return time_diff.total_seconds() >= incremental_interval * 3600

def run_training(incremental=False, force=False):
    logger.info('========== 开始自动化模型训练 ==========' + ('(增量训练)' if incremental else ''))
    
    if not check_resource_usage() and not force:
        logger.warning('资源使用过高，跳过训练')
        return False
    
    data_check_config = CONFIG.get('auto_train', {}).get('data_check', {})
    min_matches = data_check_config.get('min_matches', 100)
    
    if incremental:
        last_inc_train = get_last_train_time(LAST_INC_TRAIN_FILE)
        since_date = last_inc_train.strftime('%Y-%m-%d') if last_inc_train else None
        data_stats = check_new_matches(since_date)
        logger.info(f'增量训练检查: 新增比赛 {data_stats["new_matches"]} 场, 总计 {data_stats["total_matches"]} 场')
        
        if data_stats['new_matches'] == 0:
            logger.info('无新增比赛数据，跳过增量训练')
            return False
        
        if data_stats['total_matches'] < min_matches:
            logger.warning(f'比赛数据不足{min_matches}场，跳过训练')
            return False
    else:
        data_stats = check_new_matches()
        logger.info(f'全量训练检查: 近7天新比赛 {data_stats["new_matches"]} 场, 总计 {data_stats["total_matches"]} 场')
        
        if data_stats['total_matches'] < min_matches:
            logger.warning(f'比赛数据不足{min_matches}场，跳过训练')
            return False
        
        new_match_days = data_check_config.get('new_match_days', 7)
        if data_stats['new_matches'] == 0 and not force:
            logger.info(f'近{new_match_days}天无新比赛数据，跳过训练')
            return False
    
    logger.info('开始运行训练脚本...')
    start_time = time.time()
    
    timeout = CONFIG.get('auto_train', {}).get('timeout', {}).get('training_seconds', 300)
    
    try:
        cmd = [sys.executable, TRAIN_SCRIPT]
        if incremental:
            cmd.append('--incremental')
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        elapsed_time = time.time() - start_time
        
        if result.returncode == 0:
            logger.info(f'训练完成，耗时 {elapsed_time:.2f} 秒')
            if result.stdout:
                logger.debug(f'训练输出:\n{result.stdout}')
            
            model_files = [
                os.path.join(MODEL_DIR, 'xgb_model_export.js'),
                os.path.join(MODEL_DIR, 'lgb_model_export.js'),
                os.path.join(MODEL_DIR, 'feature_scaler_params.js')
            ]
            
            for mf in model_files:
                if os.path.exists(mf):
                    size = os.path.getsize(mf)
                    logger.info(f'模型文件已生成: {os.path.basename(mf)} ({size} bytes)')
                else:
                    logger.warning(f'模型文件未生成: {os.path.basename(mf)}')
            
            if incremental:
                update_last_train_time(LAST_INC_TRAIN_FILE)
            else:
                update_last_train_time(LAST_TRAIN_FILE)
            
            return True
        else:
            logger.error(f'训练失败，返回码: {result.returncode}')
            if result.stderr:
                logger.error(f'错误输出:\n{result.stderr}')
            return False
    except subprocess.TimeoutExpired:
        logger.error(f'训练超时({timeout}秒)')
        return False
    except Exception as e:
        logger.error(f'训练脚本执行失败: {e}')
        return False

def run_emergency_train():
    logger.info('========== 执行紧急模型更新 ==========')
    success = run_training(force=True)
    if success:
        logger.info('紧急更新成功完成')
    else:
        logger.error('紧急更新失败')
    return success

def create_anomaly_table():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS anomaly_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER,
                date TEXT,
                home_team_name TEXT,
                away_team_name TEXT,
                competition_name TEXT,
                homeGoals INTEGER,
                awayGoals INTEGER,
                model_prediction INTEGER,
                model_prob_home REAL,
                model_prob_draw REAL,
                model_prob_away REAL,
                odds_implied_home REAL,
                odds_implied_draw REAL,
                odds_implied_away REAL,
                prediction_error REAL,
                model_odds_divergence REAL,
                confidence_score REAL,
                anomaly_type TEXT,
                anomaly_score REAL,
                feedback_count INTEGER DEFAULT 0,
                last_feedback_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (match_id) REFERENCES matches(id)
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_anomaly_date ON anomaly_matches(date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_anomaly_type ON anomaly_matches(anomaly_type)
        """)
        conn.commit()
        conn.close()
        logger.info('异常样本标记表创建成功')
    except Exception as e:
        logger.error(f'创建异常样本标记表失败: {e}')

def calculate_prediction_error(model_probs, actual_result):
    """
    计算预测误差
    
    Args:
        model_probs (list): 模型预测概率，顺序为[客胜, 平局, 主胜]
        actual_result (int): 实际结果，0=客胜, 1=平局, 2=主胜
    
    Returns:
        float: 预测误差，范围[0, 1]，值越大表示误差越大
    """
    if actual_result == 2:
        return 1 - model_probs[2]
    elif actual_result == 1:
        return 1 - model_probs[1]
    elif actual_result == 0:
        return 1 - model_probs[0]
    return 1.0


def calculate_model_odds_divergence(model_probs, odds_probs):
    """
    计算模型与赔率之间的KL散度（分歧度）
    
    Args:
        model_probs (list): 模型预测概率，顺序为[客胜, 平局, 主胜]
        odds_probs (list): 赔率隐含概率，顺序为[客胜, 平局, 主胜]
    
    Returns:
        float: KL散度值，值越大表示模型与赔率分歧越大
    """
    kl_div = 0
    for i in range(3):
        if model_probs[i] > 0 and odds_probs[i] > 0:
            kl_div += model_probs[i] * np.log(model_probs[i] / odds_probs[i])
    return kl_div


def detect_anomaly(model_probs, odds_probs, actual_result, prediction_error, 
                   divergence_threshold=DIVERGENCE_THRESHOLD, error_threshold=ERROR_THRESHOLD):
    anomaly_type = None
    anomaly_score = 0.0
    
    max_model_prob = max(model_probs)
    max_odds_prob = max(odds_probs)
    model_pred = np.argmax(model_probs)
    odds_pred = np.argmax(odds_probs)
    
    divergence = calculate_model_odds_divergence(model_probs, odds_probs)
    
    if prediction_error > error_threshold:
        anomaly_score += prediction_error * 0.6
        
        if model_pred != odds_pred:
            anomaly_type = 'model_odds_conflict'
            anomaly_score += divergence * 0.4
        else:
            anomaly_type = 'high_confidence_error'
            anomaly_score += max_model_prob * 0.4
    elif divergence > divergence_threshold:
        anomaly_score += divergence * 0.7
        
        if model_pred != odds_pred:
            anomaly_type = 'market_disagreement'
            anomaly_score += abs(max_model_prob - max_odds_prob) * 0.3
        else:
            anomaly_type = 'probability_mismatch'
            anomaly_score += abs(model_probs[model_pred] - odds_probs[model_pred]) * 0.3
    else:
        anomaly_type = None
    
    return anomaly_type, min(anomaly_score, 1.0), divergence

def update_feedback_signal(match_data, model_predictions, odds_data=None):
    logger.info('========== 更新反馈信号 ==========')
    
    if match_data is None or len(match_data) == 0:
        logger.warning('match_data为空，跳过反馈信号更新')
        return {'total_matches': 0, 'anomaly_count': 0, 'anomaly_rate': 0.0, 'avg_prediction_error': 0.0, 'avg_divergence': 0.0}
    
    if model_predictions is None or len(model_predictions) == 0:
        logger.warning('model_predictions为空，跳过反馈信号更新')
        return {'total_matches': 0, 'anomaly_count': 0, 'anomaly_rate': 0.0, 'avg_prediction_error': 0.0, 'avg_divergence': 0.0}
    
    if len(match_data) != len(model_predictions):
        logger.error(f'match_data和model_predictions长度不一致: {len(match_data)} vs {len(model_predictions)}')
        return {'total_matches': 0, 'anomaly_count': 0, 'anomaly_rate': 0.0, 'avg_prediction_error': 0.0, 'avg_divergence': 0.0}
    
    if odds_data is not None and len(odds_data) != len(match_data):
        logger.warning(f'odds_data长度({len(odds_data)})与match_data长度({len(match_data)})不一致，将忽略odds_data')
        odds_data = None
    
    create_anomaly_table()
    
    feedback_records = []
    anomaly_records = []
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for i, (_, match) in enumerate(match_data.iterrows()):
        model_probs = model_predictions[i]
        
        if 'result' in match:
            actual_result = match['result']
        else:
            home_goals = match.get('homeGoals', 0)
            away_goals = match.get('awayGoals', 0)
            if home_goals > away_goals:
                actual_result = 2
            elif home_goals == away_goals:
                actual_result = 1
            else:
                actual_result = 0
        
        prediction_error = calculate_prediction_error(model_probs, actual_result)
        model_pred = np.argmax(model_probs)
        
        if odds_data is not None and i < len(odds_data):
            odds_probs = odds_data[i]
            anomaly_type, anomaly_score, divergence = detect_anomaly(
                model_probs, odds_probs, actual_result, prediction_error
            )
        else:
            odds_probs = [1/3, 1/3, 1/3]
            anomaly_type = None
            anomaly_score = 0.0
            divergence = 0.0
        
        confidence_score = max(model_probs)
        
        match_date = match.get('date', '')
        if isinstance(match_date, (datetime, pd.Timestamp)):
            match_date = match_date.strftime('%Y-%m-%d %H:%M:%S')
        
        feedback_record = {
            'match_id': match.get('id'),
            'date': match_date,
            'home_team_name': match.get('home_team_name', ''),
            'away_team_name': match.get('away_team_name', ''),
            'competition_name': match.get('competition_name', ''),
            'homeGoals': match.get('homeGoals'),
            'awayGoals': match.get('awayGoals'),
            'model_prediction': model_pred,
            'model_prob_home': model_probs[2],
            'model_prob_draw': model_probs[1],
            'model_prob_away': model_probs[0],
            'odds_implied_home': odds_probs[2],
            'odds_implied_draw': odds_probs[1],
            'odds_implied_away': odds_probs[0],
            'prediction_error': prediction_error,
            'model_odds_divergence': divergence,
            'confidence_score': confidence_score,
            'anomaly_type': anomaly_type,
            'anomaly_score': anomaly_score,
            'last_feedback_date': current_time
        }
        
        feedback_records.append(feedback_record)
        
        if anomaly_type is not None and anomaly_score > 0.5:
            anomaly_records.append(feedback_record)
    
    if anomaly_records:
        logger.info(f'检测到 {len(anomaly_records)} 个异常样本')
        
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                
                update_count = 0
                insert_count = 0
                
                for record in anomaly_records:
                    cursor.execute("""
                        SELECT id FROM anomaly_matches 
                        WHERE home_team_name = ? AND away_team_name = ? AND date = ?
                    """, (record['home_team_name'], record['away_team_name'], record['date']))
                    existing = cursor.fetchone()
                    
                    if existing:
                        cursor.execute("""
                            UPDATE anomaly_matches SET
                                homeGoals = ?, awayGoals = ?,
                                model_prediction = ?, model_prob_home = ?, 
                                model_prob_draw = ?, model_prob_away = ?,
                                odds_implied_home = ?, odds_implied_draw = ?, odds_implied_away = ?,
                                prediction_error = ?, model_odds_divergence = ?,
                                confidence_score = ?, anomaly_type = ?, anomaly_score = ?,
                                feedback_count = feedback_count + 1,
                                last_feedback_date = ?, updated_at = ?
                            WHERE id = ?
                        """, (
                            record['homeGoals'], record['awayGoals'],
                            record['model_prediction'], record['model_prob_home'],
                            record['model_prob_draw'], record['model_prob_away'],
                            record['odds_implied_home'], record['odds_implied_draw'],
                            record['odds_implied_away'], record['prediction_error'],
                            record['model_odds_divergence'], record['confidence_score'],
                            record['anomaly_type'], record['anomaly_score'],
                            record['last_feedback_date'], current_time,
                            existing[0]
                        ))
                        update_count += 1
                    else:
                        cursor.execute("""
                            INSERT INTO anomaly_matches (
                                match_id, date, home_team_name, away_team_name,
                                competition_name, homeGoals, awayGoals,
                                model_prediction, model_prob_home, model_prob_draw,
                                model_prob_away, odds_implied_home, odds_implied_draw,
                                odds_implied_away, prediction_error, model_odds_divergence,
                                confidence_score, anomaly_type, anomaly_score,
                                feedback_count, last_feedback_date, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            record['match_id'], record['date'], record['home_team_name'],
                            record['away_team_name'], record['competition_name'],
                            record['homeGoals'], record['awayGoals'],
                            record['model_prediction'], record['model_prob_home'],
                            record['model_prob_draw'], record['model_prob_away'],
                            record['odds_implied_home'], record['odds_implied_draw'],
                            record['odds_implied_away'], record['prediction_error'],
                            record['model_odds_divergence'], record['confidence_score'],
                            record['anomaly_type'], record['anomaly_score'],
                            1, record['last_feedback_date'], current_time, current_time
                        ))
                        insert_count += 1
                
                conn.commit()
                logger.info(f'异常记录保存完成: 新增 {insert_count} 条, 更新 {update_count} 条')
        
        except Exception as e:
            logger.error(f'保存异常记录失败: {e}')
    
    anomaly_summary = {
        'total_matches': len(feedback_records),
        'anomaly_count': len(anomaly_records),
        'anomaly_rate': len(anomaly_records) / len(feedback_records) if feedback_records else 0,
        'avg_prediction_error': np.mean([r['prediction_error'] for r in feedback_records]),
        'avg_divergence': np.mean([r['model_odds_divergence'] for r in feedback_records])
    }
    
    logger.info(f'反馈信号更新完成 - 异常率: {anomaly_summary["anomaly_rate"]:.2%}, '
                f'平均预测误差: {anomaly_summary["avg_prediction_error"]:.4f}, '
                f'平均分歧度: {anomaly_summary["avg_divergence"]:.4f}')
    
    return anomaly_summary

def get_anomaly_stats():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM anomaly_matches")
        total = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT anomaly_type, COUNT(*) as count 
            FROM anomaly_matches 
            GROUP BY anomaly_type
        """)
        type_distribution = {row[0]: row[1] for row in cursor.fetchall()}
        
        cursor.execute("SELECT AVG(anomaly_score), MAX(anomaly_score), MIN(anomaly_score) FROM anomaly_matches")
        score_stats = cursor.fetchone()
        
        cursor.execute("SELECT MAX(last_feedback_date) FROM anomaly_matches")
        latest_date = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_anomalies': total,
            'type_distribution': type_distribution,
            'score_stats': {
                'avg': score_stats[0],
                'max': score_stats[1],
                'min': score_stats[2]
            },
            'latest_date': latest_date
        }
    except Exception as e:
        logger.error(f'获取异常统计失败: {e}')
        return {'total_anomalies': 0, 'type_distribution': {}, 'score_stats': {}, 'latest_date': None}

def main():
    parser = argparse.ArgumentParser(description='自动化模型训练脚本')
    parser.add_argument('--emergency', action='store_true', help='执行紧急模型更新')
    parser.add_argument('--force', action='store_true', help='强制执行训练，忽略资源检查')
    parser.add_argument('--incremental', action='store_true', help='执行增量训练')
    parser.add_argument('--feedback', action='store_true', help='更新反馈信号')
    parser.add_argument('--anomaly-stats', action='store_true', help='查看异常统计')
    args = parser.parse_args()
    
    logger.info('自动化训练服务启动')
    
    if args.anomaly_stats:
        stats = get_anomaly_stats()
        logger.info(f'异常统计: {stats}')
        return
    
    if args.emergency:
        run_emergency_train()
        return
    
    if args.incremental:
        success = run_training(incremental=True, force=args.force)
        if success:
            logger.info('增量训练成功完成')
            if args.feedback:
                logger.info('训练完成后更新反馈信号...')
        else:
            logger.error('增量训练失败')
        return
    
    if should_incremental_train():
        logger.info('满足增量训练条件，执行增量训练')
        success = run_training(incremental=True)
        if success:
            logger.info('增量训练成功完成')
        else:
            logger.error('增量训练失败')
        return
    
    if should_train():
        success = run_training(force=args.force)
        if success:
            logger.info('全量训练成功完成')
        else:
            logger.error('全量训练失败')
    else:
        logger.info('距离上次训练不足周期，跳过本次训练')
    
    logger.info('自动化训练服务结束')

if __name__ == '__main__':
    main()