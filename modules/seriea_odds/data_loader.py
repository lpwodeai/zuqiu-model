"""意甲数据加载器 (兼容层)

基于 common.data_loader 的薄兼容层，提供向后兼容的 load_seriea_matches 别名。
"""

from modules.common.data_loader import *

# 向后兼容别名
def load_seriea_matches(league='意甲'):
    """向后兼容：加载意甲比赛数据"""
    return load_league_matches(league)