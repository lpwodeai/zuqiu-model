"""西甲数据加载器 (兼容层)"""
from modules.common.data_loader import *

def load_laliga_matches(league='西甲'):
    return load_league_matches(league)