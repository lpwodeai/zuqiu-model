"""德甲数据清洗器 (兼容层)

注入德甲特定 TEAM_MAPPINGS。
"""
from modules.common.data_cleaner import *
from modules.common.league_config import BUNDESLIGA_TEAM_MAPPINGS
TEAM_MAPPINGS = BUNDESLIGA_TEAM_MAPPINGS