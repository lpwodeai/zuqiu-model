"""法甲数据加载器 (兼容层)"""
from modules.common.data_loader import *

def load_ligue1_matches(league='法甲'):
    return load_league_matches(league)