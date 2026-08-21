"""
平局后处理校准器 (Draw Post-Processing Calibrator)
===================================================

基于 draw_threshold_calibration 实验数据实现：
- factor=0.90: 全局 draw_recall=0.247, acc=0.5525 (最佳准确率)
- factor=0.95: 全局 draw_recall=0.301, acc=0.5497 (兼顾)
- factor=1.00: 全局 draw_recall=0.366, acc=0.5516 (高召回)
- factor=1.04: 全局 draw_recall=0.410, acc=0.5457

策略：降低平局预测阈值（factor < 1.0 使模型更倾向于预测平局），
在保证准确率的前提下将平局召回率提升到 0.28+。
"""

import numpy as np
from sklearn.metrics import accuracy_score, recall_score, log_loss


class DrawCalibrator:
    """平局后处理校准器

    通过调整平局概率阈值来控制平局召回率。
    factor < 1.0: 降低平局判定阈值，增加平局预测（提高召回率）
    factor > 1.0: 提高平局判定阈值，减少平局预测（提高精确率）
    factor = 1.0: 不做调整

    参数:
        factor: 平局概率缩放因子，范围 [0.8, 1.1]
        target_recall: 目标平局召回率（如果设定，自动搜索 factor）
    """

    def __init__(self, factor=0.95, target_recall=None):
        self.factor = factor
        self.target_recall = target_recall
        self.best_factor_ = factor

    def calibrate(self, y_proba, factor=None):
        """对预测概率进行校准

        参数:
            y_proba: shape (n_samples, 3) 的概率矩阵，列顺序 [主胜, 平局, 客胜]
            factor: 缩放因子，如不传则使用 self.factor

        返回:
            校准后的概率矩阵（归一化后）
        """
        f = factor if factor is not None else self.factor
        calibrated = y_proba.copy()
        calibrated[:, 1] *= (2.0 - f)  # factor < 1 → 放大平局概率
        # 重新归一化
        calibrated = calibrated / calibrated.sum(axis=1, keepdims=True)
        return calibrated

    def predict(self, y_proba, factor=None):
        """返回校准后的预测标签"""
        calibrated = self.calibrate(y_proba, factor)
        return np.argmax(calibrated, axis=1)

    def fit(self, y_true, y_proba, target_recall=None):
        """自动搜索最优 factor 以达到目标平局召回率

        搜索策略:
        - factor < 1.0: 放大平局概率，提高召回率
        - factor > 1.0: 缩小平局概率，提高精确率
        - 优先保证达到 target_recall，其次最大化准确率

        参数:
            y_true: 真实标签
            y_proba: 预测概率
            target_recall: 目标平局召回率，如不传则使用 self.target_recall

        返回:
            self
        """
        target = target_recall if target_recall is not None else self.target_recall
        if target is None:
            return self

        best_factor = 0.95
        best_score = -1e9
        best_recall_gap = 1e9  # 跟踪最接近目标的 factor

        # 网格搜索 factor (从低到高，低 factor 增加平局召回率)
        for f in np.arange(0.80, 1.01, 0.01):
            y_pred = self.predict(y_proba, factor=f)
            acc = accuracy_score(y_true, y_pred)
            per_class = recall_score(y_true, y_pred, labels=[0, 1, 2], average=None)
            draw_recall = per_class[1]

            recall_gap = max(0, target - draw_recall)

            if draw_recall >= target:
                # 达标：以准确率为主排序
                score = acc + 1.0  # 达标优先级高于一切
            else:
                # 未达标：优先缩小 recall 差距，其次准确率
                score = -recall_gap * 2.0 + acc * 0.1

            if score > best_score:
                best_score = score
                best_factor = f

        self.best_factor_ = best_factor
        self.factor = best_factor
        return self

    def evaluate(self, y_true, y_proba, factor=None):
        """评估校准效果

        返回包含各项指标的字典
        """
        f = factor if factor is not None else self.factor
        calibrated = self.calibrate(y_proba, f)
        y_pred = np.argmax(calibrated, axis=1)

        acc = accuracy_score(y_true, y_pred)
        ll = log_loss(y_true, calibrated, labels=[0, 1, 2])
        per_class = recall_score(y_true, y_pred, labels=[0, 1, 2], average=None)

        draw_mask = y_pred == 1
        draw_precision = (y_true[draw_mask] == 1).mean() if draw_mask.sum() > 0 else 0.0

        return {
            'factor': f,
            'accuracy': acc,
            'log_loss': ll,
            'draw_recall': per_class[1],
            'draw_precision': draw_precision,
            'draw_pred_rate': draw_mask.mean(),
            'home_recall': per_class[0],
            'away_recall': per_class[2],
        }


def find_optimal_factor(y_true, y_proba, target_recall=0.28):
    """搜索最优 factor 使得平局召回率 >= target_recall 且准确率最高

    参数:
        y_true: 真实标签
        y_proba: 预测概率
        target_recall: 目标平局召回率

    返回:
        (best_factor, metrics_dict)
    """
    calibrator = DrawCalibrator()
    calibrator.fit(y_true, y_proba, target_recall=target_recall)
    metrics = calibrator.evaluate(y_true, y_proba, calibrator.best_factor_)
    return calibrator.best_factor_, metrics


def compare_factors(y_true, y_proba):
    """对比不同 factor 下的各项指标

    返回 factor 列表和对应指标列表
    """
    calibrator = DrawCalibrator()
    results = []
    for f in np.arange(0.80, 1.11, 0.05):
        m = calibrator.evaluate(y_true, y_proba, factor=f)
        results.append(m)
    return results