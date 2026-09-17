"""英超数据加载器 (兼容层)

基于 common.data_loader 的薄兼容层，提供向后兼容的 load_epl_matches 别名。
"""

from modules.common.data_loader import *

# 向后兼容别名
def load_epl_matches(league='英超'):
    """向后兼容：加载英超比赛数据"""
    return load_league_matches(league)