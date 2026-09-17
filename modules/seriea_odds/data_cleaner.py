"""意甲数据清洗器 (兼容层)

基于 common.data_cleaner 的薄兼容层，注入意甲特定的 TEAM_MAPPINGS。
"""

from modules.common.data_cleaner import *
from modules.common.league_config import SERIEA_TEAM_MAPPINGS

# 意甲特定球队映射
TEAM_MAPPINGS = SERIEA_TEAM_MAPPINGS