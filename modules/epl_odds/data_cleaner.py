"""英超数据清洗器 (兼容层)

基于 common.data_cleaner 的薄兼容层，注入英超特定的 TEAM_MAPPINGS。
"""

from modules.common.data_cleaner import *
from modules.common.league_config import EPL_TEAM_MAPPINGS

# 英超特定球队映射
TEAM_MAPPINGS = EPL_TEAM_MAPPINGS