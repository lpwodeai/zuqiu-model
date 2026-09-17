import sqlite3
import difflib
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
MAIN_DB_PATH = BASE_DIR / "data" / "five_leagues.db"
ODDS_DB_PATH = BASE_DIR / "data" / "odds.db"

STANDARD_CN_NAMES = [
    'AC米兰', '乌迪内斯', '云达不莱梅', '亚特兰大', '伯恩利', '伯恩茅斯',
    '佛罗伦萨', '克雷莫纳', '切尔西', '利兹联', '利物浦', '勒沃库森',
    '勒阿弗尔', '南特', '博洛尼亚', '卡利亚里', '国际米兰', '图卢兹',
    '圣保利', '埃尔切', '埃弗顿', '塞尔塔', '塞维利亚', '多特蒙德',
    '奥格斯堡', '奥维耶多', '奥萨苏纳', '富勒姆', '尤文图斯', '尼斯',
    '巴列卡诺', '巴塞罗那', '巴黎FC', '巴黎圣日耳曼', '布伦特福德', '布莱顿',
    '布雷斯特', '帕尔马', '弗赖堡', '拉齐奥', '拜仁慕尼黑', '摩纳哥',
    '斯图加特', '斯特拉斯堡', '昂热', '曼城', '曼联', '朗斯', '柏林联合',
    '桑德兰', '梅斯', '欧塞尔', '比利亚雷亚尔', '比萨', '毕尔巴鄂',
    '水晶宫', '汉堡', '沃尔夫斯堡', '法兰克福', '洛里昂', '海登海姆',
    '热刺', '热那亚', '狼队', '瓦伦西亚', '皇家社会', '皇家贝蒂斯',
    '皇家马德里', '科莫', '科隆', '纽卡斯尔', '维罗纳', '罗马', '美因茨',
    '莱万特', '莱切', '莱比锡', '萨索洛', '西汉姆', '西班牙人', '诺丁汉森林',
    '赫塔费', '赫罗纳', '那不勒斯', '都灵', '里尔', '里昂', '门兴格拉德巴赫',
    '阿拉维斯', '阿斯顿维拉', '阿森纳', '雷恩', '霍芬海姆', '马德里竞技',
    '马洛卡', '马赛', '弗罗西诺内', '蒙扎', '特鲁瓦', '伊普斯维奇', '马拉加', '沙尔克04',
    # 英冠/英超升降级
    '南安普顿', '莱斯特城', '卢顿', '赫尔城', '沃特福德', '西布罗姆维奇', '谢菲尔德联',
    '斯托克城', '斯旺西', '诺维奇', '米德尔斯堡', '加的夫城', '哈德斯菲尔德',
    # 法甲升降级
    '蒙彼利埃', '圣埃蒂安', '波尔多', '兰斯', '第戎', '卡昂', '甘冈', '亚眠', '南锡',
    '尼姆', '巴斯蒂亚', '阿雅克肖', '克莱蒙', '勒芒',
    # 西甲升降级
    '莱加内斯', '加的斯', '阿尔梅里亚', '格拉纳达', '埃瓦尔', '巴利亚多利德',
    '希洪竞技', '韦斯卡', '拉科鲁尼亚', '桑坦德竞技', '拉斯帕尔马斯',
    # 意甲升降级
    '桑普多利亚', '切沃', '恩波利', '克罗托内', '费拉拉SPAL', '贝内文托', '斯佩齐亚',
    '萨勒尼塔纳', '威尼斯', '佩斯卡拉', '巴勒莫', '布雷西亚',
    # 德甲/德乙升降级
    '柏林赫塔', '波鸿', '汉诺威96', '纽伦堡', '杜塞尔多夫', '比勒费尔德', '达姆施塔特',
    '帕德博恩', '因戈尔施塔特', '菲尔特', '基尔', '不伦瑞克'
]

# 中文简写存续判定：提升 raw in name 的跨队误配门（仅中文简写允许该方向）
_CN_RE = re.compile(r'[\u4e00-\u9fff]')

TEAM_ALIASES = {
    '利物浦': ['利物浦', 'Liverpool', 'LFC'],
    '切尔西': ['切尔西', 'Chelsea', 'CFC'],
    '阿森纳': ['阿森纳', 'Arsenal', 'AFC'],
    '曼城': ['曼城', 'Manchester City', 'Man City', 'MCI', '曼彻斯特城'],
    '曼联': ['曼联', 'Manchester United', 'Man Utd', 'MUFC', 'MUN', '曼彻斯特联'],
    '热刺': ['热刺', 'Tottenham Hotspur', 'Spurs', 'Tottenham', 'THFC'],
    '纽卡斯尔': ['纽卡斯尔', 'Newcastle United', 'Newcastle', 'Magpies', 'NUFC'],
    '布莱顿': ['布莱顿', 'Brighton & Hove Albion', 'Brighton', 'BHA', '布赖顿'],
    '伯恩茅斯': ['伯恩茅斯', 'AFC Bournemouth', 'Bournemouth'],
    '利兹联': ['利兹联', 'Leeds United', 'Leeds'],
    '埃弗顿': ['埃弗顿', 'Everton', 'EFC'],
    '阿斯顿维拉': ['阿斯顿维拉', 'Aston Villa', 'Villa', 'AVFC'],
    '富勒姆': ['富勒姆', 'Fulham', 'FFC'],
    '桑德兰': ['桑德兰', 'Sunderland', 'SAFC'],
    '西汉姆': ['西汉姆', 'West Ham United', 'West Ham', 'WHU', 'WHFC'],
    '伯恩利': ['伯恩利', 'Burnley', 'Burnley FC'],
    '狼队': ['狼队', 'Wolverhampton Wanderers', 'Wolves', 'Wolverhampton', '伍尔弗汉普顿', '伍尔弗汉普顿流浪者'],
    '诺丁汉森林': ['诺丁汉森林', 'Nottingham Forest', 'Forest', 'NFFC'],
    '布伦特福德': ['布伦特福德', 'Brentford', 'Brentford FC'],
    '水晶宫': ['水晶宫', 'Crystal Palace', 'Palace', 'CPFC'],
    '考文垂': ['考文垂', 'Coventry City', 'Coventry'],
    '拜仁慕尼黑': ['拜仁慕尼黑', 'Bayern Munich', 'Bayern', 'FCB'],
    '多特蒙德': ['多特蒙德', 'Borussia Dortmund', 'Dortmund', 'BVB'],
    '勒沃库森': ['勒沃库森', 'Bayer Leverkusen', 'Leverkusen', 'Bayer'],
    '沃尔夫斯堡': ['沃尔夫斯堡', 'VfL Wolfsburg', 'Wolfsburg'],
    '门兴格拉德巴赫': ['门兴格拉德巴赫', 'Borussia Monchengladbach', 'Monchengladbach', 'Gladbach', 'BMG'],
    '法兰克福': ['法兰克福', 'Eintracht Frankfurt', 'Frankfurt'],
    '斯图加特': ['斯图加特', 'VfB Stuttgart', 'Stuttgart'],
    '莱比锡': ['莱比锡', 'RB Leipzig', 'Leipzig'],
    '弗赖堡': ['弗赖堡', 'SC Freiburg', 'Freiburg'],
    '霍芬海姆': ['霍芬海姆', 'TSG Hoffenheim', 'Hoffenheim'],
    '柏林联合': ['柏林联合', 'Union Berlin'],
    '科隆': ['科隆', 'FC Koln', 'Koln', '1. FC Köln', 'Köln', '1. FC Koln'],
    '奥格斯堡': ['奥格斯堡', 'FC Augsburg', 'Augsburg'],
    '美因茨': ['美因茨', 'Mainz 05', 'Mainz'],
    '云达不莱梅': ['云达不莱梅', 'Werder Bremen', 'Bremen'],
    '圣保利': ['圣保利', 'FC St. Pauli', 'St. Pauli'],
    '海登海姆': ['海登海姆', '1. FC Heidenheim', 'Heidenheim'],
    '汉堡': ['汉堡', 'Hamburger SV', 'Hamburg', 'HSV'],
    '巴黎圣日耳曼': ['巴黎圣日耳曼', 'Paris Saint-Germain', 'PSG', 'Paris SG'],
    '里昂': ['里昂', 'Olympique Lyonnais', 'Lyon', 'OL'],
    '马赛': ['马赛', 'Olympique Marseille', 'Marseille', 'OM'],
    '摩纳哥': ['摩纳哥', 'AS Monaco', 'Monaco'],
    '里尔': ['里尔', 'Lille OSC', 'Lille'],
    '雷恩': ['雷恩', 'Stade Rennais', 'Rennes'],
    '尼斯': ['尼斯', 'OGC Nice', 'Nice'],
    '朗斯': ['朗斯', 'RC Lens', 'Lens'],
    '南特': ['南特', 'FC Nantes', 'Nantes'],
    '斯特拉斯堡': ['斯特拉斯堡', 'RC Strasbourg', 'Strasbourg'],
    '洛里昂': ['洛里昂', 'FC Lorient', 'Lorient'],
    '布雷斯特': ['布雷斯特', 'Stade Brestois', 'Brest'],
    '梅斯': ['梅斯', 'FC Metz', 'Metz'],
    '昂热': ['昂热', 'Angers SCO', 'Angers'],
    '欧塞尔': ['欧塞尔', 'AJ Auxerre', 'Auxerre'],
    '图卢兹': ['图卢兹', 'Toulouse FC', 'Toulouse'],
    '勒阿弗尔': ['勒阿弗尔', 'Le Havre AC', 'Le Havre'],
    '巴黎FC': ['巴黎FC', 'Paris FC'],
    '巴塞罗那': ['巴塞罗那', 'Barcelona', 'Barca', '巴萨', 'FCB'],
    '皇家马德里': ['皇家马德里', 'Real Madrid', 'RM', '皇马'],  # 注：'Madrid' 单词已于 C-20260907-009 移除（子串误命中 Atlético Madrid）
    '马德里竞技': ['马德里竞技', 'Atletico Madrid', 'Atletico', 'Atleti', 'ATM'],
    '塞维利亚': ['塞维利亚', 'Sevilla FC', 'Sevilla'],
    '瓦伦西亚': ['瓦伦西亚', 'Valencia CF', 'Valencia', '巴伦西亚'],
    '比利亚雷亚尔': ['比利亚雷亚尔', 'Villarreal CF', 'Villarreal', 'Yellow Submarine'],
    '皇家贝蒂斯': ['皇家贝蒂斯', 'Real Betis', 'Betis'],
    '皇家社会': ['皇家社会', 'Real Sociedad', 'Sociedad'],
    '毕尔巴鄂': ['毕尔巴鄂', 'Athletic Bilbao', 'Bilbao', 'Athletic'],
    '塞尔塔': ['塞尔塔', 'Celta Vigo', 'Celta'],
    '赫塔费': ['赫塔费', 'Getafe CF', 'Getafe', '赫塔菲'],
    '奥萨苏纳': ['奥萨苏纳', 'CA Osasuna', 'Osasuna'],
    '赫罗纳': ['赫罗纳', 'Girona FC', 'Girona'],
    '西班牙人': ['西班牙人', 'RCD Espanyol', 'Espanyol'],
    '巴列卡诺': ['巴列卡诺', 'Rayo Vallecano', 'Rayo'],
    '马洛卡': ['马洛卡', 'RCD Mallorca', 'Mallorca'],
    '莱万特': ['莱万特', 'Levante UD', 'Levante'],
    '阿拉维斯': ['阿拉维斯', 'Deportivo Alavés', 'Deportivo Alaves', 'Alaves'],
    '奥维耶多': ['奥维耶多', 'Real Oviedo', 'Oviedo'],
    '埃尔切': ['埃尔切', 'Elche CF', 'Elche'],
    '国际米兰': ['国际米兰', 'Inter Milan', 'Inter', 'FCIM', '国米'],
    'AC米兰': ['AC米兰', 'AC Milan', 'Milan', '米兰'],  # '米兰' 精确别名置于最前，优先于国际米兰的子串命中（唯一中文歧义词显式消歧，见 C-20260908-016 残留）
    '尤文图斯': ['尤文图斯', 'Juventus', 'Juve'],
    '罗马': ['罗马', 'AS Roma', 'Roma'],
    '那不勒斯': ['那不勒斯', 'Napoli', 'SSC Napoli'],
    '拉齐奥': ['拉齐奥', 'Lazio', 'SS Lazio'],
    '亚特兰大': ['亚特兰大', 'Atalanta BC', 'Atalanta'],
    '佛罗伦萨': ['佛罗伦萨', 'Fiorentina', 'ACF Fiorentina'],
    '博洛尼亚': ['博洛尼亚', 'Bologna FC', 'Bologna'],
    '都灵': ['都灵', 'Torino FC', 'Torino'],
    '热那亚': ['热那亚', 'Genoa CFC', 'Genoa'],
    '萨索洛': ['萨索洛', 'Sassuolo', 'US Sassuolo'],
    '乌迪内斯': ['乌迪内斯', 'Udinese', 'Udinese Calcio'],
    '维罗纳': ['维罗纳', 'Hellas Verona', 'Verona'],
    '卡利亚里': ['卡利亚里', 'Cagliari', 'Cagliari Calcio'],
    '莱切': ['莱切', 'Lecce', 'US Lecce'],
    '帕尔马': ['帕尔马', 'Parma Calcio', 'Parma'],
    '比萨': ['比萨', 'Pisa SC', 'Pisa'],
    '科莫': ['科莫', 'Como 1907', 'Como'],
    '克雷莫纳': ['克雷莫纳', 'Cremonese', 'US Cremonese'],
    '弗罗西诺内': ['弗罗西诺内', 'Frosinone'],
    '蒙扎': ['蒙扎', 'Monza', 'AC Monza'],
    '特鲁瓦': ['特鲁瓦', 'Troyes', 'ESTAC Troyes'],
    '伊普斯维奇': ['伊普斯维奇', 'Ipswich Town', 'Ipswich'],
    '马拉加': ['马拉加', 'Malaga', 'Malaga CF', 'Málaga', 'Málaga CF'],
    '沙尔克04': ['沙尔克04', 'Schalke 04', 'FC Schalke 04', 'Schalke'],
    # ---- 英冠/英超升降级 ----
    '南安普顿': ['南安普顿', 'Southampton', 'Soton', '南安普敦'],
    '莱斯特城': ['莱斯特城', 'Leicester City', 'Leicester', 'Leicester City FC'],
    '卢顿': ['卢顿', 'Luton Town', 'Luton', 'LTFC'],
    '赫尔城': ['赫尔城', 'Hull City', 'Hull', 'Hull City Tigers'],
    '沃特福德': ['沃特福德', 'Watford', 'Watford FC'],
    '西布罗姆维奇': ['西布罗姆维奇', 'West Bromwich Albion', 'West Brom', 'West Bromwich', 'WBA'],
    '谢菲尔德联': ['谢菲尔德联', 'Sheffield United', 'Sheffield Utd', 'Sheffield'],
    '斯托克城': ['斯托克城', 'Stoke City', 'Stoke'],
    '斯旺西': ['斯旺西', 'Swansea City', 'Swansea'],
    '诺维奇': ['诺维奇', 'Norwich City', 'Norwich'],
    '米德尔斯堡': ['米德尔斯堡', 'Middlesbrough', 'Boro'],
    '加的夫城': ['加的夫城', 'Cardiff City', 'Cardiff'],
    '哈德斯菲尔德': ['哈德斯菲尔德', 'Huddersfield Town', 'Huddersfield'],
    # ---- 法甲升降级 ----
    '蒙彼利埃': ['蒙彼利埃', 'Montpellier', 'MHSC'],
    '圣埃蒂安': ['圣埃蒂安', 'Saint-Étienne', 'Saint Etienne', 'Saint-Etienne', 'ASSE'],
    '波尔多': ['波尔多', 'Girondins de Bordeaux', 'Bordeaux'],
    '兰斯': ['兰斯', 'Stade de Reims', 'Stade Reims', 'Reims'],
    '第戎': ['第戎', 'Dijon FCO', 'Dijon'],
    '卡昂': ['卡昂', 'SM Caen', 'Caen'],
    '甘冈': ['甘冈', 'EA Guingamp', 'Guingamp'],
    '亚眠': ['亚眠', 'Amiens SC', 'Amiens'],
    '南锡': ['南锡', 'AS Nancy', 'Nancy', 'AS Nancy-Lorraine'],
    '尼姆': ['尼姆', 'Nîmes Olympique', 'Nimes Olympique', 'Nîmes', 'Nimes'],
    '巴斯蒂亚': ['巴斯蒂亚', 'SC Bastia', 'Bastia'],
    '阿雅克肖': ['阿雅克肖', 'AC Ajaccio', 'Ajaccio'],
    '克莱蒙': ['克莱蒙', 'Clermont Foot 63', 'Clermont Foot', 'Clermont'],
    '勒芒': ['勒芒', 'Le Mans FC', 'Le Mans'],
    # ---- 西甲升降级 ----
    '莱加内斯': ['莱加内斯', 'CD Leganes', 'Leganés', 'Leganes', 'Leganes CF'],
    '加的斯': ['加的斯', 'Cadiz CF', 'Cádiz', 'Cadiz'],
    '阿尔梅里亚': ['阿尔梅里亚', 'UD Almeria', 'Almería', 'Almeria'],
    '格拉纳达': ['格拉纳达', 'Granada CF', 'Granada'],
    '埃瓦尔': ['埃瓦尔', 'SD Eibar', 'Eibar'],
    '巴利亚多利德': ['巴利亚多利德', 'Real Valladolid', 'Valladolid'],
    '希洪竞技': ['希洪竞技', 'Sporting Gijón', 'Sporting Gijon', 'Gijon', 'Sporting de Gijon'],
    '韦斯卡': ['韦斯卡', 'SD Huesca', 'Huesca'],
    '拉科鲁尼亚': ['拉科鲁尼亚', 'Deportivo La Coruna', 'Deportivo de A Coruña', 'La Coruna', 'Deportivo La Coruña', 'Deportivo'],
    '桑坦德竞技': ['桑坦德竞技', 'Real Racing Club', 'Racing Santander', 'Racing'],
    '拉斯帕尔马斯': ['拉斯帕尔马斯', 'UD Las Palmas', 'Las Palmas'],
    # ---- 意甲升降级 ----
    '桑普多利亚': ['桑普多利亚', 'Sampdoria', 'UC Sampdoria', 'SSD Sampdoria'],
    '切沃': ['切沃', 'Chievo Verona', 'Chievo', 'AC Chievo Verona'],
    '恩波利': ['恩波利', 'Empoli', 'Empoli FC'],
    '克罗托内': ['克罗托内', 'FC Crotone', 'Crotone'],
    '费拉拉SPAL': ['费拉拉SPAL', 'SPAL', 'SPAL Ferrara'],
    '贝内文托': ['贝内文托', 'Benevento Calcio', 'Benevento'],
    '斯佩齐亚': ['斯佩齐亚', 'Spezia Calcio', 'Spezia'],
    '萨勒尼塔纳': ['萨勒尼塔纳', 'US Salernitana 1919', 'Salernitana', 'US Salernitana'],
    '威尼斯': ['威尼斯', 'Venezia FC', 'Venezia'],
    '佩斯卡拉': ['佩斯卡拉', 'Delfino Pescara 1936', 'Pescara'],
    '巴勒莫': ['巴勒莫', 'Palermo', 'Palermo FC'],
    '布雷西亚': ['布雷西亚', 'Brescia Calcio', 'Brescia'],
    # ---- 德甲/德乙升降级 ----
    '柏林赫塔': ['柏林赫塔', 'Hertha BSC', 'Hertha Berlin', 'Hertha'],
    '波鸿': ['波鸿', 'VfL Bochum 1848', 'VfL Bochum', 'Bochum'],
    '汉诺威96': ['汉诺威96', 'Hannover 96', 'Hannover'],
    '纽伦堡': ['纽伦堡', '1. FC Nürnberg', '1. FC Nurnberg', 'Nürnberg', 'Nurnberg', 'Nuremberg'],
    '杜塞尔多夫': ['杜塞尔多夫', 'Fortuna Düsseldorf', 'Fortuna Dusseldorf', 'Düsseldorf', 'Dusseldorf'],
    '比勒费尔德': ['比勒费尔德', 'Arminia Bielefeld', 'DSC Arminia Bielefeld', 'Bielefeld'],
    '达姆施塔特': ['达姆施塔特', 'SV Darmstadt 98', 'Darmstadt 98', 'Darmstadt'],
    '帕德博恩': ['帕德博恩', 'SC Paderborn 07', 'Paderborn 07', 'Paderborn'],
    '因戈尔施塔特': ['因戈尔施塔特', 'FC Ingolstadt 04', 'Ingolstadt'],
    '菲尔特': ['菲尔特', 'SpVgg Greuther Fürth', 'Greuther Fürth', 'Greuther Furth', 'Fürth', 'Furth'],
    '基尔': ['基尔', 'Holstein Kiel', 'Kiel'],
    '不伦瑞克': ['不伦瑞克', 'Eintracht Braunschweig', 'Braunschweig'],
}


def normalize_team_name(raw_name, fuzzy_threshold=0.8):
    if not raw_name:
        return None
    
    raw = str(raw_name).strip()
    
    for standard_name, aliases in TEAM_ALIASES.items():
        if raw.lower() == standard_name.lower():
            return standard_name
        for alias in aliases:
            if raw.lower() == alias.lower():
                return standard_name
    
    # 子串匹配（变体归一），按中/英文分治：
    #   - `name in raw`：raw 是更长的队名并包含队伍锚词时命中（安全方向）。
    #     兼容源数据变体，如 'FC Bayern München' 含 'Bayern'、'FC St. Pauli' 含
    #     'St. Pauli'、'Coventry City' 命中考文垂完整别名。
    #   - `raw in name`：仅当 raw 含中文时保留——中文短简写唯一性强
    #     （'尤文'→尤文图斯、'门兴'→门兴格拉德巴赫、'米兰'→AC米兰），无跨队误配；
    #     英文通用词（City/United/Madrid/Athletic 等）多队共享，一律禁止该方向，
    #     否则会把任意长队名误配到映射表首个命中项（'Coventry City'→曼城、
    #     'Madrid'→皇家马德里、*United→曼联）。该英文误配根因见
    #     C-20260907-006/007/009（删个别别名属无效补丁）；精确别名已在前循环覆盖。
    has_cn = _CN_RE.search(raw) is not None
    for standard_name, aliases in TEAM_ALIASES.items():
        all_names = [standard_name] + aliases
        for name in all_names:
            nl = name.lower()
            rl = raw.lower()
            # 短 ASCII 缩写（OL/OM/RM/AFC/MUN 等）只做精确匹配，不做子串锚词：
            # 否则 'Olympique de Marseille' 会被 'ol'(里昂缩写) 误配、
            # 'Parma Calcio 1913' 会被 'rm'(皇马缩写) 误配（见 C-20260908-018）。
            if nl in rl and not (nl.isascii() and len(nl) < 4):
                return standard_name
            if has_cn and rl in nl:
                return standard_name
    
    best_match = difflib.get_close_matches(raw.lower(), STANDARD_CN_NAMES, n=1, cutoff=fuzzy_threshold)
    if best_match:
        return best_match[0]
    
    for standard_name, aliases in TEAM_ALIASES.items():
        all_names = [standard_name] + aliases
        for name in all_names:
            seq = difflib.SequenceMatcher(None, raw.lower(), name.lower())
            if seq.ratio() >= fuzzy_threshold:
                return standard_name
    
    return None


def batch_normalize_team_names(names, fuzzy_threshold=0.8):
    results = {}
    for name in names:
        normalized = normalize_team_name(name, fuzzy_threshold)
        results[name] = normalized
    return results


def load_all_team_names_from_db():
    conn = sqlite3.connect(MAIN_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT name FROM teams ORDER BY name")
    teams = [row[0] for row in cursor.fetchall()]
    conn.close()
    return teams


def verify_mapping_completeness():
    db_teams = load_all_team_names_from_db()
    missing_teams = []
    
    for team in db_teams:
        if team not in TEAM_ALIASES:
            missing_teams.append(team)
    
    if missing_teams:
        print(f"\n警告: 以下球队在 TEAM_ALIASES 中缺失:")
        for team in missing_teams:
            print(f"  - {team}")
    else:
        print(f"\n✓ 所有 {len(db_teams)} 支球队均已在 TEAM_ALIASES 中注册")
    
    return len(missing_teams) == 0


def update_odds_database_with_chinese_names():
    conn = sqlite3.connect(ODDS_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT match_id, home_team, away_team FROM matches")
    matches = cursor.fetchall()
    
    updated_count = 0
    for match_id, home_team, away_team in matches:
        new_home = normalize_team_name(home_team) or home_team
        new_away = normalize_team_name(away_team) or away_team
        
        if new_home != home_team or new_away != away_team:
            new_match_id = match_id.replace(home_team, new_home).replace(away_team, new_away)
            
            cursor.execute("""
                UPDATE matches 
                SET match_id = ?, home_team = ?, away_team = ?
                WHERE match_id = ?
            """, (new_match_id, new_home, new_away, match_id))
            
            cursor.execute("""
                UPDATE wdl_history SET match_id = ? WHERE match_id = ?
            """, (new_match_id, match_id))
            
            cursor.execute("""
                UPDATE handicap_history SET match_id = ? WHERE match_id = ?
            """, (new_match_id, match_id))
            
            cursor.execute("""
                UPDATE total_goals_history SET match_id = ? WHERE match_id = ?
            """, (new_match_id, match_id))
            
            cursor.execute("""
                UPDATE score_history SET match_id = ? WHERE match_id = ?
            """, (new_match_id, match_id))
            
            updated_count += 1
            print(f"更新: {match_id} -> {new_match_id}")
    
    conn.commit()
    conn.close()
    
    print(f"\n共更新 {updated_count} 场比赛")


def verify_mapping():
    main_conn = sqlite3.connect(MAIN_DB_PATH)
    odds_conn = sqlite3.connect(ODDS_DB_PATH)
    
    main_cursor = main_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    main_cursor.execute("""
        SELECT ht.name as home_team, at.name as away_team
        FROM matches m
        LEFT JOIN teams ht ON m.homeTeamId = ht.id
        LEFT JOIN teams at ON m.awayTeamId = at.id
    """)
    
    main_teams = set()
    for row in main_cursor.fetchall():
        main_teams.add(row[0])
        main_teams.add(row[1])
    
    odds_cursor.execute("SELECT DISTINCT home_team FROM matches")
    odds_teams = set([row[0] for row in odds_cursor.fetchall()])
    
    odds_cursor.execute("SELECT DISTINCT away_team FROM matches")
    for row in odds_cursor.fetchall():
        odds_teams.add(row[0])
    
    print(f"\n主数据库中的球队: {sorted(main_teams)[:10]}...")
    print(f"赔率数据库中的球队: {sorted(odds_teams)}")
    
    matched_teams = main_teams & odds_teams
    unmatched_teams = odds_teams - main_teams
    
    print(f"\n匹配的球队: {matched_teams}")
    print(f"未匹配的球队: {unmatched_teams}")
    
    main_conn.close()
    odds_conn.close()


if __name__ == "__main__":
    print("验证球队名称映射完整性...")
    verify_mapping_completeness()
    
    print("\n测试名称标准化功能:")
    test_names = [
        'Liverpool', 'Liverpool FC', 'LFC', '利物浦', '利物浦队',
        'Man Utd', 'Manchester United', '曼联', 'MU',
        'Newcastle', 'Newcastle United', '纽卡斯尔联', '纽卡斯尔',
        'West Ham', 'West Ham United', '西汉姆联', '西汉姆',
        'Wolves', 'Wolverhampton', '狼队',
        'Spurs', 'Tottenham', '热刺',
        'MC', 'Man City', '曼城',
        'Arsenal', 'AFC', '阿森纳',
        '切尔西', 'Chelsea FC', 'CFC',
        '拜仁', 'Bayern Munich', '拜仁慕尼黑',
        '尤文', 'Juventus', '尤文图斯',
        '巴萨', 'Barcelona', '巴塞罗那',
        '皇马', 'Real Madrid', '皇家马德里',
        '国米', 'Inter', '国际米兰',
        '米兰', 'AC Milan', 'AC米兰'
    ]
    
    for name in test_names:
        normalized = normalize_team_name(name)
        status = "✓" if normalized else "✗"
        print(f"  {status} '{name}' -> '{normalized}'")
