"""通用赔率回测引擎模块

负责支持高效的数据回测分析，与联赛无关。
通过 league 参数区分不同联赛。

核心功能：
- 时间序列交叉验证回测
- 预测结果评估（准确率、Log Loss、Brier分数等）
- 详细回测报告生成
- 错误案例分析
- 策略回报率计算
"""

import sqlite3
import pandas as pd
import numpy as np
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass
from sklearn.metrics import (
    accuracy_score, log_loss, brier_score_loss, f1_score,
    confusion_matrix, classification_report
)
from sklearn.preprocessing import StandardScaler

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

from .data_loader import (
    load_league_matches,
    get_match_ids_with_complete_odds,
    ODDS_DB_PATH
)
from .temporal_features import build_features_for_matches
from db_utils import connect as _db_connect  # noqa: E402  (sys.path 已由 .data_loader 设置)


@dataclass
class BacktestConfig:
    """回测配置类"""
    train_ratio: float = 0.7
    validation_ratio: float = 0.15
    test_ratio: float = 0.15
    time_series_split: bool = True
    max_history_days: int = 90
    half_life_days: int = 14
    n_splits: int = 5
    random_state: int = 42


class BacktestEngine:
    """回测引擎类"""

    def __init__(self, config: Optional[BacktestConfig] = None, league: str = None):
        self.config = config or BacktestConfig()
        self.league = league
        self.conn = None
        self.features_df = None
        self.target_df = None
        self.match_ids = None

    def connect(self):
        """连接数据库（DB_BACKEND=pg 切 PG, 默认 SQLite）"""
        self.conn = _db_connect(db_path=ODDS_DB_PATH)

    def disconnect(self):
        """断开数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def load_data(self, league: str = None) -> bool:
        """
        加载回测数据

        Args:
            league: 联赛名称

        Returns:
            bool: 是否加载成功
        """
        league = league or self.league or '英超'
        self.league = league

        print(f"🔄 加载{league}回测数据...")

        # 获取具有完整赔率数据的比赛ID
        self.match_ids = get_match_ids_with_complete_odds(league)
        print(f"📊 可用比赛数: {len(self.match_ids)}场")

        if len(self.match_ids) < 10:
            print("❌ 比赛数据不足，无法进行回测")
            return False

        # 加载比赛基本信息
        matches = load_league_matches(league)
        match_dict = {m.match_id: m for m in matches}

        # 构建特征
        print(f"🔧 构建特征...")
        self.features_df, _ = build_features_for_matches(self.match_ids)
        print(f"✅ 生成特征数量: {len(self.features_df.columns)}")

        # 构建目标标签
        target_data = []
        for match_id in self.match_ids:
            match = match_dict.get(match_id)
            if match and match.actual_wdl:
                if match.actual_wdl == '胜':
                    label = 2
                elif match.actual_wdl == '平':
                    label = 1
                elif match.actual_wdl == '负':
                    label = 0
                else:
                    label = None
            else:
                label = None
            target_data.append({'match_id': match_id, 'label': label})

        self.target_df = pd.DataFrame(target_data)

        combined = pd.concat([self.features_df, self.target_df], axis=1)
        combined = combined.dropna(subset=['label'])

        self.features_df = combined.drop(['match_id', 'label'], axis=1)
        self.target_df = combined[['match_id', 'label']]

        print(f"✅ 有效数据: {len(self.target_df)}场")
        return True

    def split_data(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """按时间序列划分训练集和测试集"""
        X = self.features_df.values
        y = self.target_df['label'].values

        if self.config.time_series_split:
            n_samples = len(y)
            train_end = int(n_samples * self.config.train_ratio)
            val_end = train_end + int(n_samples * self.config.validation_ratio)

            X_train, X_val, X_test = X[:train_end], X[train_end:val_end], X[val_end:]
            y_train, y_val, y_test = y[:train_end], y[train_end:val_end], y[val_end:]

            print(f"📈 时间序列划分:")
            print(f"   训练集: {len(y_train)}场 ({len(y_train)/n_samples*100:.1f}%)")
            print(f"   验证集: {len(y_val)}场 ({len(y_val)/n_samples*100:.1f}%)")
            print(f"   测试集: {len(y_test)}场 ({len(y_test)/n_samples*100:.1f}%)")
        else:
            from sklearn.model_selection import train_test_split
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=self.config.test_ratio,
                random_state=self.config.random_state
            )
            X_train, X_val, y_train, y_val = train_test_split(
                X_train, y_train, test_size=self.config.validation_ratio / (1 - self.config.test_ratio),
                random_state=self.config.random_state
            )

        return X_train, X_val, X_test, y_train, y_val, y_test

    def train_model(self, X_train: np.ndarray, y_train: np.ndarray,
                   model_type: str = 'xgboost',
                   X_val: np.ndarray = None, y_val: np.ndarray = None) -> object:
        """训练模型"""
        print(f"\n🏋️ 训练{model_type}模型...")

        try:
            if model_type == 'xgboost':
                from xgboost import XGBClassifier
                model = XGBClassifier(
                    n_estimators=200, max_depth=2, learning_rate=0.03,
                    subsample=0.6, colsample_bytree=0.6,
                    min_child_weight=10, reg_alpha=1.0, reg_lambda=10.0,
                    gamma=0.5, early_stopping_rounds=15,
                    random_state=self.config.random_state,
                    eval_metric='mlogloss', use_label_encoder=False
                )
                model.fit(X_train, y_train,
                          eval_set=[(X_val, y_val)],
                          verbose=False)
            elif model_type == 'lightgbm':
                from lightgbm import LGBMClassifier
                model = LGBMClassifier(
                    n_estimators=200, max_depth=2, learning_rate=0.03,
                    num_leaves=8, subsample=0.6, colsample_bytree=0.6,
                    reg_alpha=1.0, reg_lambda=10.0, min_child_weight=10,
                    min_data_in_leaf=30,
                    random_state=self.config.random_state,
                    verbose=-1
                )
                model.fit(X_train, y_train,
                          eval_set=[(X_val, y_val)],
                          callbacks=[lgb.early_stopping(stopping_rounds=15),
                                     lgb.log_evaluation(period=0)])
            elif model_type == 'random_forest':
                from sklearn.ensemble import RandomForestClassifier
                model = RandomForestClassifier(
                    n_estimators=100, max_depth=2,
                    random_state=self.config.random_state
                )
                model.fit(X_train, y_train)
            else:
                raise ValueError(f"不支持的模型类型: {model_type}")

            print(f"✅ {model_type}模型训练完成")
            return model

        except ImportError as e:
            print(f"❌ 模型导入失败: {e}")
            return None

    def evaluate_model(self, model: object, X: np.ndarray, y: np.ndarray,
                       dataset_name: str = '测试集') -> Dict:
        """评估模型性能"""
        print(f"\n📊 评估{dataset_name}...")

        y_pred = model.predict(X)
        y_proba = model.predict_proba(X)

        results = {
            'dataset': dataset_name,
            'sample_count': len(y),
            'accuracy': accuracy_score(y, y_pred),
            'log_loss': log_loss(y, y_proba),
            'f1_macro': f1_score(y, y_pred, average='macro'),
            'confusion_matrix': confusion_matrix(y, y_pred).tolist(),
            'classification_report': classification_report(y, y_pred, output_dict=True),
            'class_distribution': {
                'actual': dict(pd.Series(y).value_counts()),
                'predicted': dict(pd.Series(y_pred).value_counts())
            }
        }

        # brier_score 仅适用于二分类，多分类时跳过
        n_classes = len(np.unique(y))
        if n_classes == 2:
            try:
                results['brier_score'] = brier_score_loss(y, y_proba[:, 1])
            except Exception:
                pass

        print(f"   准确率: {results['accuracy']:.4f}")
        print(f"   Log Loss: {results['log_loss']:.4f}")
        print(f"   F1 Score: {results['f1_macro']:.4f}")

        return results

    def run_time_series_backtest(self, model_type: str = 'xgboost') -> Dict:
        """执行时间序列交叉验证回测"""
        print("=" * 70)
        print(f"🏁 开始{self.league}时间序列回测")
        print("=" * 70)

        start_time = datetime.now()

        if not self.load_data():
            return {'status': 'failed', 'error': '数据加载失败'}

        X_train, X_val, X_test, y_train, y_val, y_test = self.split_data()

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_val_scaled = scaler.transform(X_val)
        X_test_scaled = scaler.transform(X_test)

        model = self.train_model(X_train_scaled, y_train, model_type, X_val_scaled, y_val)
        if model is None:
            return {'status': 'failed', 'error': '模型训练失败'}

        train_results = self.evaluate_model(model, X_train_scaled, y_train, '训练集')
        val_results = self.evaluate_model(model, X_val_scaled, y_val, '验证集')
        test_results = self.evaluate_model(model, X_test_scaled, y_test, '测试集')

        feature_importance = {}
        if hasattr(model, 'feature_importances_'):
            importances = model.feature_importances_
            features = self.features_df.columns.tolist()
            feature_importance = dict(sorted(
                zip(features, importances),
                key=lambda x: x[1], reverse=True
            ))

        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()

        report = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'duration_seconds': duration,
            'status': 'completed',
            'league': self.league,
            'config': {
                'model_type': model_type,
                'train_ratio': self.config.train_ratio,
                'validation_ratio': self.config.validation_ratio,
                'test_ratio': self.config.test_ratio,
                'time_series_split': self.config.time_series_split,
                'total_matches': len(self.match_ids),
                'valid_matches': len(y_train) + len(y_val) + len(y_test)
            },
            'train_results': train_results,
            'validation_results': val_results,
            'test_results': test_results,
            'feature_importance': feature_importance,
            'feature_count': len(self.features_df.columns)
        }

        print("\n" + "=" * 70)
        print("📋 回测完成")
        print("=" * 70)
        print(f"⏱️ 耗时: {duration:.2f}秒")
        print(f"📊 测试集准确率: {test_results['accuracy']:.4f}")
        print(f"📉 测试集Log Loss: {test_results['log_loss']:.4f}")

        return report

    def calculate_roi(self, predictions: pd.DataFrame, odds_data: pd.DataFrame,
                      stake: float = 100.0) -> Dict:
        """计算策略回报率"""
        results = {
            'total_bets': 0, 'winning_bets': 0, 'losing_bets': 0,
            'total_stake': 0, 'total_return': 0, 'roi': 0, 'hit_rate': 0
        }

        for _, row in predictions.iterrows():
            match_id = row['match_id']
            prediction = row['prediction']
            actual = row['actual']

            if prediction == actual:
                odds = odds_data.loc[odds_data['match_id'] == match_id, 'odds'].values
                if len(odds) > 0:
                    results['winning_bets'] += 1
                    results['total_return'] += stake * odds[0]
            else:
                results['losing_bets'] += 1

            results['total_bets'] += 1
            results['total_stake'] += stake

        if results['total_bets'] > 0:
            results['hit_rate'] = results['winning_bets'] / results['total_bets']
            results['roi'] = (results['total_return'] - results['total_stake']) / results['total_stake']

        return results

    def analyze_error_cases(self, model: object, X_test: np.ndarray, y_test: np.ndarray,
                           match_ids: List[str]) -> List[Dict]:
        """分析错误案例"""
        y_pred = model.predict(X_test)
        y_proba = model.predict_proba(X_test)

        errors = []
        for i, (pred, actual, proba) in enumerate(zip(y_pred, y_test, y_proba)):
            if pred != actual:
                errors.append({
                    'match_id': match_ids[i],
                    'predicted': int(pred),
                    'actual': int(actual),
                    'prediction_confidence': float(proba[pred]),
                    'actual_confidence': float(proba[actual]),
                    'confidence_gap': float(proba[pred] - proba[actual])
                })

        errors.sort(key=lambda x: x['confidence_gap'], reverse=True)
        return errors

    def generate_backtest_report(self, report: Dict, output_dir: str = None) -> str:
        """
        生成详细回测报告（Markdown格式）

        Args:
            report: 回测报告字典
            output_dir: 输出目录

        Returns:
            str: 报告文件路径
        """
        league = self.league or '联赛'
        league_key = league.replace(' ', '_')

        if output_dir is None:
            module_dir = os.path.dirname(os.path.abspath(__file__))
            common_dir = os.path.dirname(module_dir)
            modules_dir = os.path.dirname(common_dir)
            project_root = os.path.dirname(modules_dir)
            output_dir = os.path.join(project_root, 'reports')

        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = os.path.join(output_dir, f'{league_key}_backtest_report_{timestamp}.md')

        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"""# {league}赔率回测报告

> 生成时间: {report['timestamp']}
> 耗时: {report['duration_seconds']:.2f}秒

---

## 一、回测配置

| 配置项 | 值 |
|--------|------|
| 模型类型 | {report['config']['model_type']} |
| 训练集比例 | {report['config']['train_ratio']*100:.0f}% |
| 验证集比例 | {report['config']['validation_ratio']*100:.0f}% |
| 测试集比例 | {report['config']['test_ratio']*100:.0f}% |
| 时间序列划分 | {'是' if report['config']['time_series_split'] else '否'} |
| 总比赛数 | {report['config']['total_matches']}场 |
| 有效比赛数 | {report['config']['valid_matches']}场 |
| 特征数量 | {report['feature_count']}个 |

---

## 二、回测结果

### 2.1 各数据集表现

| 指标 | 训练集 | 验证集 | 测试集 |
|------|--------|--------|--------|
| 样本数 | {report['train_results']['sample_count']} | {report['validation_results']['sample_count']} | {report['test_results']['sample_count']} |
| 准确率 | {report['train_results']['accuracy']:.4f} | {report['validation_results']['accuracy']:.4f} | {report['test_results']['accuracy']:.4f} |
| Log Loss | {report['train_results']['log_loss']:.4f} | {report['validation_results']['log_loss']:.4f} | {report['test_results']['log_loss']:.4f} |
| F1 Score | {report['train_results']['f1_macro']:.4f} | {report['validation_results']['f1_macro']:.4f} | {report['test_results']['f1_macro']:.4f} |

### 2.2 测试集分类报告

| 类别 | 准确率 | 召回率 | F1分数 | 支持数 |
|------|--------|--------|--------|--------|
| 客胜(0) | {report['test_results']['classification_report']['0']['precision']:.4f} | {report['test_results']['classification_report']['0']['recall']:.4f} | {report['test_results']['classification_report']['0']['f1-score']:.4f} | {report['test_results']['classification_report']['0']['support']} |
| 平局(1) | {report['test_results']['classification_report']['1']['precision']:.4f} | {report['test_results']['classification_report']['1']['recall']:.4f} | {report['test_results']['classification_report']['1']['f1-score']:.4f} | {report['test_results']['classification_report']['1']['support']} |
| 主胜(2) | {report['test_results']['classification_report']['2']['precision']:.4f} | {report['test_results']['classification_report']['2']['recall']:.4f} | {report['test_results']['classification_report']['2']['f1-score']:.4f} | {report['test_results']['classification_report']['2']['support']} |

---

## 三、特征重要性（Top20）

| 排名 | 特征名称 | 重要性 |
|------|----------|--------|
""")

            for i, (feature, importance) in enumerate(list(report['feature_importance'].items())[:20], 1):
                f.write(f"| {i} | {feature} | {importance:.6f} |\n")

            f.write("""
---

## 四、结论

### 4.1 性能评估

- **测试集准确率**: {test_acc:.4f}
- **过拟合程度**: {overfit:.2f}（训练准确率与测试准确率差距）
- **Log Loss**: {log_loss:.4f}（概率校准质量）

### 4.2 分析

{analysis}

---

**报告结束**
""".format(
                test_acc=report['test_results']['accuracy'],
                overfit=report['train_results']['accuracy'] - report['test_results']['accuracy'],
                log_loss=report['test_results']['log_loss'],
                analysis=self._generate_analysis(report)
            ))

        print(f"\n📝 报告已保存: {report_path}")
        return report_path

    def _generate_analysis(self, report: Dict) -> str:
        """生成分析结论"""
        test_acc = report['test_results']['accuracy']
        train_acc = report['train_results']['accuracy']
        overfit_gap = train_acc - test_acc

        analysis_parts = []

        if test_acc >= 0.55:
            analysis_parts.append("✅ 模型表现优秀，测试集准确率超过55%基线")
        elif test_acc >= 0.47:
            analysis_parts.append("⚠️ 模型表现一般，测试集准确率接近47.22%基线")
        else:
            analysis_parts.append("❌ 模型表现较差，测试集准确率低于基线")

        if overfit_gap > 0.3:
            analysis_parts.append("⚠️ 存在严重过拟合，训练准确率与测试准确率差距超过30%")
        elif overfit_gap > 0.15:
            analysis_parts.append("ℹ️ 存在一定程度过拟合，建议增加正则化")
        else:
            analysis_parts.append("✅ 过拟合程度可控")

        return "\n".join(analysis_parts)


def run_full_backtest(config: Optional[BacktestConfig] = None,
                      model_type: str = 'xgboost',
                      league: str = '英超') -> Dict:
    """
    运行完整回测（便捷函数）

    Args:
        config: 回测配置
        model_type: 模型类型
        league: 联赛名称

    Returns:
        Dict: 回测报告
    """
    engine = BacktestEngine(config, league=league)

    try:
        engine.connect()
        report = engine.run_time_series_backtest(model_type)

        if report['status'] == 'completed':
            engine.generate_backtest_report(report)

        return report

    finally:
        engine.disconnect()


if __name__ == "__main__":
    print("🏁 通用赔率回测引擎测试")
    print("=" * 50)

    config = BacktestConfig(
        train_ratio=0.7,
        validation_ratio=0.15,
        test_ratio=0.15,
        time_series_split=True
    )

    report = run_full_backtest(config, model_type='xgboost', league='英超')

    if report['status'] == 'completed':
        print("\n✅ 回测成功!")
        print(f"测试集准确率: {report['test_results']['accuracy']:.4f}")
    else:
        print(f"\n❌ 回测失败: {report.get('error', '未知错误')}")