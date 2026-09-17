import pandas as pd
import numpy as np
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "assets"
DB_PATH = BASE_DIR / "data" / "odds.db"

class PerformanceMonitor:
    def __init__(self):
        self.performance_history = []
        self.degradation_threshold = 0.15
        self.min_samples_for_detection = 20
        self.window_size = 50
    
    def record_performance(self, accuracy, ml_coverage, odds_coverage, match_count, timestamp=None):
        record = {
            'timestamp': timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'accuracy': accuracy,
            'ml_coverage': ml_coverage,
            'odds_coverage': odds_coverage,
            'match_count': match_count
        }
        self.performance_history.append(record)
        
        if len(self.performance_history) > self.window_size * 2:
            self.performance_history = self.performance_history[-self.window_size * 2:]
        
        return record
    
    def detect_degradation(self):
        if len(self.performance_history) < self.min_samples_for_detection * 2:
            return {'degraded': False, 'reason': '样本不足'}
        
        recent = self.performance_history[-self.min_samples_for_detection:]
        historical = self.performance_history[:-self.min_samples_for_detection]
        
        if len(historical) < self.min_samples_for_detection:
            return {'degraded': False, 'reason': '历史样本不足'}
        
        recent_accuracy = np.mean([r['accuracy'] for r in recent])
        historical_accuracy = np.mean([r['accuracy'] for r in historical])
        
        accuracy_drop = historical_accuracy - recent_accuracy
        
        recent_ml_coverage = np.mean([r['ml_coverage'] for r in recent])
        historical_ml_coverage = np.mean([r['ml_coverage'] for r in historical])
        
        ml_coverage_drop = historical_ml_coverage - recent_ml_coverage
        
        degradation_score = accuracy_drop * 0.7 + ml_coverage_drop * 0.3
        
        is_degraded = degradation_score > self.degradation_threshold
        
        return {
            'degraded': is_degraded,
            'degradation_score': degradation_score,
            'accuracy_drop': accuracy_drop,
            'ml_coverage_drop': ml_coverage_drop,
            'recent_accuracy': recent_accuracy,
            'historical_accuracy': historical_accuracy,
            'reason': f"准确率下降 {accuracy_drop:.2f}, ML覆盖率下降 {ml_coverage_drop:.2f}" if is_degraded else '未检测到衰退'
        }
    
    def get_performance_summary(self):
        if not self.performance_history:
            return {'status': 'no_data'}
        
        recent = self.performance_history[-10:]
        overall = self.performance_history
        
        return {
            'total_matches': sum(r['match_count'] for r in overall),
            'overall_accuracy': np.mean([r['accuracy'] for r in overall]),
            'recent_accuracy': np.mean([r['accuracy'] for r in recent]),
            'overall_ml_coverage': np.mean([r['ml_coverage'] for r in overall]),
            'recent_ml_coverage': np.mean([r['ml_coverage'] for r in recent]),
            'record_count': len(self.performance_history)
        }

class IncrementalLearner:
    def __init__(self):
        self.last_train_time = None
        self.last_train_version = None
        self.incremental_batch_size = 50
        self.min_new_matches_for_retrain = 10
        self.max_days_since_last_train = 7
    
    def check_need_retrain(self, new_match_count):
        if self.last_train_time is None:
            return {'need_retrain': True, 'reason': '首次训练'}
        
        days_since_last = (datetime.now() - self.last_train_time).days
        
        if new_match_count >= self.min_new_matches_for_retrain:
            return {'need_retrain': True, 'reason': f'新比赛数达到阈值 ({new_match_count}/{self.min_new_matches_for_retrain})'}
        
        if days_since_last >= self.max_days_since_last_train:
            return {'need_retrain': True, 'reason': f'距上次训练已超过 {days_since_last} 天'}
        
        return {'need_retrain': False, 'reason': '无需重新训练'}
    
    def run_incremental_training(self, new_data=None):
        import subprocess
        import sys
        
        print("\n启动增量训练...")
        
        cmd = [sys.executable, 'train_models.py', '--incremental']
        
        if new_data is not None:
            temp_file = f'temp_new_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
            new_data.to_csv(temp_file, index=False)
            cmd.extend(['--data', temp_file])
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR / "scripts"))
            
            if result.returncode == 0:
                print("增量训练成功!")
                print(result.stdout)
                self.last_train_time = datetime.now()
                self.last_train_version = datetime.now().strftime('%Y%m%d_%H%M%S')
                return {'success': True, 'output': result.stdout}
            else:
                print(f"增量训练失败: {result.stderr}")
                return {'success': False, 'error': result.stderr}
        except Exception as e:
            print(f"增量训练异常: {e}")
            return {'success': False, 'error': str(e)}

class ModelVersionManager:
    def __init__(self):
        self.versions = []
        self._load_versions()
    
    def _load_versions(self):
        pkl_files = [f for f in os.listdir(OUTPUT_DIR) if f.endswith('.pkl') and 'model' in f.lower()]
        
        for file in pkl_files:
            parts = file.replace('.pkl', '').split('_')
            if len(parts) >= 3:
                model_type = parts[0]
                version = parts[-1]
                
                existing = next((v for v in self.versions if v['version'] == version), None)
                if not existing:
                    self.versions.append({
                        'version': version,
                        'model_type': model_type,
                        'file': file,
                        'timestamp': datetime.fromtimestamp(os.path.getmtime(os.path.join(OUTPUT_DIR, file)))
                    })
        
        self.versions.sort(key=lambda x: x['timestamp'], reverse=True)
    
    def get_latest_version(self, model_type='xgb'):
        for v in self.versions:
            if v['model_type'] == model_type or model_type in v['model_type']:
                return v
        return None
    
    def list_versions(self):
        return self.versions[:10]
    
    def rollback_to_version(self, version):
        import shutil
        
        version_info = next((v for v in self.versions if v['version'] == version), None)
        if not version_info:
            return {'success': False, 'error': f'版本 {version} 不存在'}
        
        print(f"回滚到版本 {version}...")
        
        target_files = []
        for f in os.listdir(OUTPUT_DIR):
            if version in f:
                target_files.append(f)
        
        if not target_files:
            return {'success': False, 'error': '未找到该版本的文件'}
        
        for f in target_files:
            src = os.path.join(OUTPUT_DIR, f)
            if f.startswith('xgb_model'):
                dest = os.path.join(OUTPUT_DIR, f'xgb_model_latest.pkl')
            elif f.startswith('scaler'):
                dest = os.path.join(OUTPUT_DIR, f'scaler_latest.pkl')
            elif f.startswith('selected_features'):
                dest = os.path.join(OUTPUT_DIR, f'selected_features_latest.pkl')
            else:
                continue
            
            shutil.copy2(src, dest)
            print(f"  复制 {f} -> {dest}")
        
        return {'success': True, 'version': version, 'files': target_files}

class OnlineLearningManager:
    def __init__(self):
        self.performance_monitor = PerformanceMonitor()
        self.incremental_learner = IncrementalLearner()
        self.version_manager = ModelVersionManager()
        self._create_monitor_tables()
    
    def _create_monitor_tables(self):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("DROP TABLE IF EXISTS model_performance")
        cursor.execute("DROP TABLE IF EXISTS training_history")
        cursor.execute("DROP TABLE IF EXISTS model_versions")
        
        cursor.execute("""
            CREATE TABLE model_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_version TEXT,
                accuracy REAL,
                ml_coverage REAL,
                odds_coverage REAL,
                match_count INTEGER,
                degradation_detected BOOLEAN,
                degradation_score REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE training_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                training_type TEXT,
                model_version TEXT,
                status TEXT,
                start_time DATETIME,
                end_time DATETIME,
                duration REAL,
                error_message TEXT
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS model_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT UNIQUE,
                model_type TEXT,
                file_path TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
    
    def monitor_and_retrain(self, current_performance):
        print("\n" + "=" * 60)
        print("在线学习监控")
        print("=" * 60)
        
        accuracy = current_performance.get('accuracy', 0)
        ml_coverage = current_performance.get('ml_coverage', 0)
        odds_coverage = current_performance.get('odds_coverage', 0)
        match_count = current_performance.get('match_count', 0)
        
        print(f"当前性能 - 准确率: {accuracy:.2%}, ML覆盖率: {ml_coverage:.2%}, 赔率覆盖率: {odds_coverage:.2%}")
        
        self.performance_monitor.record_performance(accuracy, ml_coverage, odds_coverage, match_count)
        
        degradation = self.performance_monitor.detect_degradation()
        
        if degradation['degraded']:
            print(f"\n⚠️ 检测到性能衰退!")
            print(f"   衰退分数: {degradation['degradation_score']:.4f}")
            print(f"   原因: {degradation['reason']}")
            
            print("\n启动自动重新训练...")
            train_result = self.incremental_learner.run_incremental_training()
            
            if train_result['success']:
                print("✅ 重新训练成功")
                self._record_training_history('auto_retrain', self.incremental_learner.last_train_version, 'success')
            else:
                print(f"❌ 重新训练失败: {train_result.get('error', '未知错误')}")
                self._record_training_history('auto_retrain', None, 'failed', error_message=train_result.get('error'))
            
            return degradation
        else:
            print(f"\n✅ 性能正常 - {degradation['reason']}")
            return degradation
    
    def _record_training_history(self, training_type, version, status, error_message=None):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO training_history (training_type, model_version, status, start_time, end_time, error_message)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
        """, (training_type, version, status, error_message))
        
        conn.commit()
        conn.close()
    
    def save_performance_record(self, performance):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        degradation = self.performance_monitor.detect_degradation()
        
        cursor.execute("""
            INSERT INTO model_performance (
                model_version, accuracy, ml_coverage, odds_coverage, 
                match_count, degradation_detected, degradation_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            self.incremental_learner.last_train_version,
            performance.get('accuracy', 0),
            performance.get('ml_coverage', 0),
            performance.get('odds_coverage', 0),
            performance.get('match_count', 0),
            degradation['degraded'],
            degradation.get('degradation_score', 0)
        ))
        
        conn.commit()
        conn.close()
    
    def get_monitor_report(self):
        summary = self.performance_monitor.get_performance_summary()
        degradation = self.performance_monitor.detect_degradation()
        
        return {
            'performance_summary': summary,
            'degradation_status': degradation,
            'last_train_time': self.incremental_learner.last_train_time,
            'last_train_version': self.incremental_learner.last_train_version,
            'latest_model_version': self.version_manager.get_latest_version(),
            'available_versions': self.version_manager.list_versions()
        }

def run_online_learning_check():
    print("=" * 60)
    print("在线学习检查")
    print("=" * 60)
    
    manager = OnlineLearningManager()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM matches WHERE actual_wdl IS NOT NULL")
    total_matches = cursor.fetchone()[0]
    
    cursor.execute("""
        SELECT 
            SUM(CASE WHEN final_prediction = actual_wdl THEN 1 ELSE 0 END) as correct,
            COUNT(*) as total,
            AVG(CASE WHEN ml_covered THEN 1 ELSE 0 END) as ml_coverage,
            AVG(CASE WHEN odds_covered THEN 1 ELSE 0 END) as odds_coverage
        FROM error_cases
    """)
    result = cursor.fetchone()
    
    conn.close()
    
    if result and result[1] > 0:
        accuracy = result[0] / result[1]
        ml_coverage = result[2] if result[2] is not None else 0
        odds_coverage = result[3] if result[3] is not None else 0
    else:
        accuracy = 0
        ml_coverage = 0
        odds_coverage = 0
    
    current_performance = {
        'accuracy': accuracy,
        'ml_coverage': ml_coverage,
        'odds_coverage': odds_coverage,
        'match_count': total_matches
    }
    
    degradation = manager.monitor_and_retrain(current_performance)
    
    manager.save_performance_record(current_performance)
    
    report = manager.get_monitor_report()
    
    print("\n监控报告:")
    print(f"  总比赛数: {report['performance_summary']['total_matches']}")
    print(f"  整体准确率: {report['performance_summary']['overall_accuracy']:.2%}")
    print(f"  近期准确率: {report['performance_summary']['recent_accuracy']:.2%}")
    print(f"  衰退状态: {'已检测' if degradation['degraded'] else '正常'}")
    print(f"  上次训练: {report['last_train_time']}")
    
    return report

if __name__ == "__main__":
    run_online_learning_check()