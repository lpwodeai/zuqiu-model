"""德甲数据加载器 (兼容层)

基于 common.data_loader 的薄兼容层，提供向后兼容的别名。
"""
from modules.common.data_loader import *

def load_bundesliga_matches(league='德甲'):
    return load_league_matches(league)