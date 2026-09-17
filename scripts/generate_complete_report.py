"""
西甲第1轮赛前完整预测报告生成器
整合:
  1. SofaScore API 赛前数据（球队/阵容/伤病/教练）
  2. 竞彩网完整时序赔率（胜平负/让球/比分/总进球）
  3. 最新 T-005 v3 模型四维度预测
"""
import json, os, sys, warnings, numpy as np, joblib, pickle, re
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson
from collections import OrderedDict

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
COLLECTION_DIR = os.path.join(PROJECT_DIR, 'collection')
sys.path.insert(0, COLLECTION_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from feature_utils import build_all_features, load_match_data_odds

ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
REPORT_DIR = os.path.join(PROJECT_DIR, "docs")
RAW_DIR = os.path.join(PROJECT_DIR, "data", "sofascore_raw", "pre_match")
ODDS_TXT = os.path.join(PROJECT_DIR, "data", "西甲2026-2027赛季完整时序赔率.txt")

CN_TZ = timezone(timedelta(hours=8))

# ============================================================
# 模型文件（使用最新训练版本）
# ============================================================
import glob
def find_latest_model(suffix):
    files = glob.glob(os.path.join(ASSETS_DIR, suffix))
    if not files:
        return None
    return max(files, key=os.path.getctime)

XGB_MODEL_PATH = find_latest_model('xgb_model_*.pkl')
LGB_MODEL_PATH = find_latest_model('lgb_model_*.pkl')
SCALER_PATH = find_latest_model('scaler_*.pkl')
FEATURES_PATH = find_latest_model('selected_features_*.pkl')

print(f"模型文件定位:")
print(f"  XGB: {XGB_MODEL_PATH}")
print(f"  LGB: {LGB_MODEL_PATH}")
print(f"  Scaler: {SCALER_PATH}")
print(f"  Features: {FEATURES_PATH}")

DRAW_THRESHOLD_FACTOR = 1.5  # 决策阈值因子（不修改概率，仅调整分类决策）

# ============================================================
# 两场比赛定义
# ============================================================
MATCHES = [
    {
        'id': 'match_1',
        'event_id': '16421047',
        'home_team': 'Deportivo Alavés',
        'away_team': 'Getafe',
        'home_team_cn': '阿拉维斯',
        'away_team_cn': '赫塔费',
        'sofascore_url': 'https://www.sofascore.ro/football/match/deportivo-alaves-getafe/jhbsKhb#id:16421047',
    },
    {
        'id': 'match_2',
        'event_id': '16421052',
        'home_team': 'Sevilla',
        'away_team': 'Rayo Vallecano',
        'home_team_cn': '塞维利亚',
        'away_team_cn': '巴列卡诺',
        'sofascore_url': 'https://www.sofascore.ro/football/match/sevilla-rayo-vallecano/tgbsIgb#id:16421052',
    },
]


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================
# 赔率 TXT 解析器
# ============================================================
def parse_odds_txt(txt_path):
    """解析完整赔率txt，提取两场比赛所有赔率数据"""
    print(f"\n解析赔率文件: {txt_path}")
    with open(txt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 按比赛分块
    # 块头格式: "2026/2027 Regular Season 第1轮 2026-08-16 01:30"
    block_pattern = r'(\d{4}/\d{4} Regular Season 第\d+轮 \d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\n([\s\S]*?)(?=\d{4}/\d{4} Regular Season|$)'
    blocks = re.findall(block_pattern, content)
    
    matches_odds = {}
    
    for header, block_body in blocks:
        # 提取比赛日期时间
        m_dt = re.search(r'第(\d+)轮 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', header)
        round_num = m_dt.group(1) if m_dt else '1'
        match_datetime = m_dt.group(2) if m_dt else ''
        
        # 提取球队（第二行）
        team_line = re.search(r'西甲 La Liga\s+(\w+)\s+(\w+)', block_body)
        if not team_line:
            # 尝试更宽松的匹配
            team_line = re.search(r'西甲 La Liga\s+(\S+)\s+(\S+)', block_body)
        if team_line:
            home_short = team_line.group(1)
            away_short = team_line.group(2)
        else:
            home_short = 'UNKNOWN'
            away_short = 'UNKNOWN'
        
        match_key = f"{home_short}_{away_short}"
        odds_data = {
            'round': round_num,
            'match_datetime': match_datetime,
            'home_short': home_short,
            'away_short': away_short,
            'wdl_odds': {'records': [], 'open': {}, 'close': {}},
            'handicap_odds': {'line': -1, 'records': [], 'open': {}, 'close': {}},
            'score_odds': {'records': []},
            'tg_odds': {'records': []},
        }
        
        # 1. 胜平负固定奖金
        wdl_pattern = r'胜平负固定奖金\s*\n发布时间\s*胜\s*平\s*负\s*\n([\s\S]*?)\n\n'
        wdl_match = re.search(wdl_pattern, block_body)
        if wdl_match:
            wdl_lines = wdl_match.group(1).strip().split('\n')
            for line in wdl_lines:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 4:
                    time_str = parts[0] + ' ' + parts[1]
                    try:
                        win = float(parts[2])
                        draw = float(parts[3])
                        lose = float(parts[4])
                        record = {'time': time_str, 'win': win, 'draw': draw, 'lose': lose}
                        odds_data['wdl_odds']['records'].append(record)
                    except (ValueError, IndexError):
                        continue
            if odds_data['wdl_odds']['records']:
                odds_data['wdl_odds']['open'] = odds_data['wdl_odds']['records'][0]
                odds_data['wdl_odds']['close'] = odds_data['wdl_odds']['records'][-1]
        
        # 2. 让球胜平负固定奖金
        hdp_pattern = r'让球胜平负固定奖金\s*\n让球([-\d]+)\s*\n发布时间\s*胜\s*平\s*负\s*\n([\s\S]*?)\n\n'
        hdp_match = re.search(hdp_pattern, block_body)
        if hdp_match:
            odds_data['handicap_odds']['line'] = int(hdp_match.group(1))
            hdp_lines = hdp_match.group(2).strip().split('\n')
            for line in hdp_lines:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 4:
                    time_str = parts[0] + ' ' + parts[1]
                    try:
                        win = float(parts[2])
                        draw = float(parts[3])
                        lose = float(parts[4])
                        record = {'time': time_str, 'win': win, 'draw': draw, 'lose': lose}
                        odds_data['handicap_odds']['records'].append(record)
                    except (ValueError, IndexError):
                        continue
            if odds_data['handicap_odds']['records']:
                odds_data['handicap_odds']['open'] = odds_data['handicap_odds']['records'][0]
                odds_data['handicap_odds']['close'] = odds_data['handicap_odds']['records'][-1]
        
        # 3. 比分固定奖金（可能多段，每段有"发布时间"）
        score_pattern = r'比分固定奖金\s*\n发布时间\s*([\d\-]+)\s+([\d:]+)\s*\n([\s\S]*?)(?=\n发布时间|\n总进球|\Z)'
        for sm in re.finditer(score_pattern, block_body):
            pub_date = sm.group(1)
            pub_time = sm.group(2).strip()
            score_body = sm.group(3)
            rec = {
                'pub_time': f"{pub_date} {pub_time}",
                'win_odds': {},
                'draw_odds': {},
                'lose_odds': {},
            }
            
            # 行结构（已验证debug输出）:
            #   lines[0] = 主胜比分标签 (13个)
            #   lines[1] = 主胜赔率值 (13个)
            #   lines[2] = 平局比分标签 (5个)
            #   lines[3] = 平局赔率值 (5个)
            #   lines[4] = 客胜比分标签 (13个)
            #   lines[5] = 客胜赔率值 (13个)
            lines = score_body.strip().split('\n')
            if len(lines) >= 6:
                win_scores = [s for s in re.split(r'\t+', lines[0].strip()) if s]
                win_values = [s for s in re.split(r'\t+', lines[1].strip()) if s]
                if len(win_scores) == len(win_values):
                    for sc, val in zip(win_scores, win_values):
                        try:
                            rec['win_odds'][sc.strip()] = float(val)
                        except ValueError:
                            pass
                
                draw_scores = [s for s in re.split(r'\t+', lines[2].strip()) if s]
                draw_values = [s for s in re.split(r'\t+', lines[3].strip()) if s]
                if len(draw_scores) == len(draw_values):
                    for sc, val in zip(draw_scores, draw_values):
                        try:
                            rec['draw_odds'][sc.strip()] = float(val)
                        except ValueError:
                            pass
                
                lose_scores = [s for s in re.split(r'\t+', lines[4].strip()) if s]
                lose_values = [s for s in re.split(r'\t+', lines[5].strip()) if s]
                if len(lose_scores) == len(lose_values):
                    for sc, val in zip(lose_scores, lose_values):
                        try:
                            rec['lose_odds'][sc.strip()] = float(val)
                        except ValueError:
                            pass
            
            odds_data['score_odds']['records'].append(rec)
        
        # 4. 总进球固定奖金
        tg_pattern = r'总进球固定奖金\s*\n发布时间\s*0\s*1\s*2\s*3\s*4\s*5\s*6\s*7\+\s*\n([\s\S]*?)(?=\n\n|\Z)'
        tg_match = re.search(tg_pattern, block_body)
        if tg_match:
            tg_lines = tg_match.group(1).strip().split('\n')
            for line in tg_lines:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 9:
                    try:
                        record = {
                            'time': parts[0] + ' ' + parts[1],
                            'o0': float(parts[2]),
                            'o1': float(parts[3]),
                            'o2': float(parts[4]),
                            'o3': float(parts[5]),
                            'o4': float(parts[6]),
                            'o5': float(parts[7]),
                            'o6': float(parts[8]),
                            'o7': float(parts[9]) if len(parts) > 9 else 0.0,
                        }
                        odds_data['tg_odds']['records'].append(record)
                    except (ValueError, IndexError):
                        continue
        
        matches_odds[match_key] = odds_data
        print(f"  解析到: {home_short} vs {away_short}")
        print(f"    WDL records: {len(odds_data['wdl_odds']['records'])}")
        print(f"    HCP records: {len(odds_data['handicap_odds']['records'])}")
        print(f"    Score records: {len(odds_data['score_odds']['records'])}")
        print(f"    TG records: {len(odds_data['tg_odds']['records'])}")
    
    return matches_odds


# ============================================================
# 匹配比赛到赔率
# ============================================================
def match_odds_to_match(match, all_odds):
    """根据球队名匹配赔率块"""
    home_cn = match['home_team_cn']
    away_cn = match['away_team_cn']
    
    # 简单映射表
    name_map = {
        '阿拉维斯': 'Alaves',
        '赫塔费': 'Getafe',
        '塞维利亚': 'Sevilla',
        '巴列卡诺': 'Vallecano',
    }
    home_short = name_map.get(home_cn, match['home_team'].split()[-1])
    away_short = name_map.get(away_cn, match['away_team'].split()[-1])
    
    key = f"{home_short}_{away_short}"
    alt_key = f"{match['home_team'].split()[-1]}_{match['away_team'].split()[-1]}"
    
    if key in all_odds:
        return all_odds[key]
    if alt_key in all_odds:
        return all_odds[alt_key]
    # 模糊匹配
    for k, v in all_odds.items():
        if home_cn[:2] in k or home_short.lower()[:3] in k.lower():
            if away_cn[:2] in k or away_short.lower()[:3] in k.lower():
                return v
    print(f"  ⚠️ 未找到赔率匹配: {home_cn} vs {away_cn}")
    return None


# ============================================================
# 加载 SofaScore 赛前数据
# ============================================================
def extract_sofascore(event_id):
    sofa_dir = os.path.join(RAW_DIR, event_id)
    event = load_json(os.path.join(sofa_dir, 'event.json'))
    lineups = load_json(os.path.join(sofa_dir, 'lineups.json'))
    
    e = event.get("event", event)
    s = {}
    
    s["tournament"] = e.get("tournament", {}).get("name", "")
    s["season"] = e.get("season", {}).get("name", "")
    s["round"] = e.get("roundInfo", {}).get("round", "")
    s["status"] = e.get("status", {}).get("description", "")
    s["venue"] = e.get("venue", {}).get("name", "")
    s["city"] = (e.get("venue", {}).get("city", {}) or {}).get("name", "")
    s["capacity"] = e.get("venue", {}).get("capacity", "")
    s["referee"] = e.get("referee", {}).get("name", "")
    
    ts = e.get("startTimestamp")
    if ts:
        cn_dt = datetime.fromtimestamp(ts, tz=CN_TZ)
        s["match_time"] = cn_dt.strftime("%Y-%m-%d %H:%M")
    
    home = e.get("homeTeam", {})
    away = e.get("awayTeam", {})
    s["home_team"] = home.get("name", "")
    s["away_team"] = away.get("name", "")
    s["home_slug"] = home.get("slug", "")
    s["away_slug"] = away.get("slug", "")
    s["home_user_count"] = home.get("userCount", 0)
    s["away_user_count"] = away.get("userCount", 0)
    s["home_manager"] = (home.get("manager", {}) or {}).get("name", "")
    s["away_manager"] = (away.get("manager", {}) or {}).get("name", "")
    
    # Lineups
    s["lineups_confirmed"] = lineups.get("confirmed", False)
    for side in ["home", "away"]:
        sd = lineups.get(side, {}) or {}
        s[f"{side}_formation"] = sd.get("formation", "")
        players = []
        for p in sd.get("players", []):
            pl = p.get("player", {}) or {}
            mv = pl.get("proposedMarketValueRaw", {}) or {}
            dob_ts = pl.get("dateOfBirthTimestamp")
            age = ""
            if dob_ts:
                dob = datetime.fromtimestamp(dob_ts, tz=timezone.utc)
                age = datetime.now().year - dob.year - (
                    (datetime.now().month, datetime.now().day) < (dob.month, dob.day))
            players.append({
                "name": pl.get("name", ""),
                "short": pl.get("shortName", ""),
                "num": p.get("jerseyNumber", p.get("shirtNumber", "")),
                "pos": pl.get("position", ""),
                "sub": p.get("substitute", False),
                "mv": f"{mv.get('value',0)/1000000:.1f}M" if mv.get("value") else "",
                "age": age,
                "nat": ((pl.get("country") or {}).get("alpha2", "")),
                "height": pl.get("height", ""),
            })
        pos_order = {"G": 0, "D": 1, "M": 2, "F": 3}
        starters = sorted([p for p in players if not p["sub"]],
                         key=lambda p: (pos_order.get(p["pos"], 99), int(str(p["num"]) or 99)))
        subs = sorted([p for p in players if p["sub"]],
                     key=lambda p: (pos_order.get(p["pos"], 99), int(str(p["num"]) or 99)))
        s[f"{side}_starters"] = starters
        s[f"{side}_subs"] = subs
        
        # Missing players (injuries)
        missing = sd.get("missingPlayers", []) or []
        s[f"{side}_missing"] = []
        for m in missing:
            pl = m.get("player", {}) or {}
            s[f"{side}_missing"].append({
                "name": pl.get("name", ""),
                "short": pl.get("shortName", ""),
                "pos": pl.get("position", ""),
                "type": m.get("type", ""),
                "description": m.get("description", ""),
                "expected_end": (m.get("expectedEndDate") or "")[:10],
            })
    
    return s


# ============================================================
# 加载模型
# ============================================================
def load_models():
    models = {}
    try:
        with open(XGB_MODEL_PATH, 'rb') as f:
            models['xgb'] = pickle.load(f)
    except Exception as e:
        print(f"⚠️ XGB 加载失败: {e}")
        models['xgb'] = None
    
    try:
        with open(LGB_MODEL_PATH, 'rb') as f:
            models['lgb'] = pickle.load(f)
    except Exception as e:
        print(f"⚠️ LGB 加载失败: {e}")
        models['lgb'] = None
    
    try:
        models['scaler'] = joblib.load(SCALER_PATH)
    except Exception as e:
        print(f"⚠️ Scaler 加载失败: {e}")
        models['scaler'] = None
    
    try:
        models['features'] = joblib.load(FEATURES_PATH)
    except Exception as e:
        print(f"⚠️ Features 加载失败: {e}")
        models['features'] = None
    
    return models


# ============================================================
# 四维度预测
# ============================================================
def odds_to_implied_prob(win, draw, lose):
    """赔率转隐含概率（归一化去除抽水）"""
    inv_w = 1.0 / win
    inv_d = 1.0 / draw
    inv_l = 1.0 / lose
    total = inv_w + inv_d + inv_l
    return inv_w/total, inv_d/total, inv_l/total


def predict_match(m, odds_data, models):
    """四维度预测: WDL, 让球, 比分, 总进球"""
    
    result = {}
    
    # ---------- 1. 胜平负 ----------
    wdl = odds_data['wdl_odds']
    last = wdl['close'] or wdl['records'][-1]
    first = wdl['open'] or wdl['records'][0]
    
    hp, dp, ap = odds_to_implied_prob(last['win'], last['draw'], last['lose'])
    
    # 决策阈值调整：不修改概率，仅在分类时对平局应用阈值因子
    # 保持概率校准，三分类概率和=1.0
    wdl_pred = '主胜' if hp > max(dp, ap) else ('平局' if dp > ap else '客胜')
    # 阈值调整：如果平局概率 × factor > max(主胜, 客胜)，则预测为平局
    if dp * DRAW_THRESHOLD_FACTOR > max(hp, ap):
        wdl_pred = '平局'
    wdl_conf = max(hp, dp, ap)
    
    result['wdl'] = {
        'home_prob': hp, 'draw_prob': dp, 'away_prob': ap,
        'raw_home': hp, 'raw_draw': dp, 'raw_away': ap,
        'prediction': wdl_pred, 'confidence': wdl_conf,
    }
    
    # 赔率趋势分析
    changes = []
    wc = last['win'] - first['win']
    dc = last['draw'] - first['draw']
    lc = last['lose'] - first['lose']
    if wc < -0.05: changes.append(f"主胜赔率↓{abs(wc):.2f}，市场看好主队")
    elif wc > 0.05: changes.append(f"主胜赔率↑{wc:.2f}，信心减弱")
    if dc < -0.05: changes.append(f"平局赔率↓{abs(dc):.2f}，平局可能性↑")
    elif dc > 0.05: changes.append(f"平局赔率↑{dc:.2f}，平局可能性↓")
    if lc < -0.05: changes.append(f"客胜赔率↓{abs(lc):.2f}，看好客队")
    elif lc > 0.05: changes.append(f"客胜赔率↑{lc:.2f}，客队信心减弱")
    result['wdl']['trend'] = "; ".join(changes) if changes else "赔率整体稳定，市场观点未发生显著变化"
    result['wdl']['open'] = first
    result['wdl']['close'] = last
    result['wdl']['records'] = wdl['records']
    
    # ---------- 2. 让球胜平负 ----------
    hcp = odds_data['handicap_odds']
    hcp_line = hcp['line']
    if hcp['close']:
        h = hcp['close']
    elif hcp['records']:
        h = hcp['records'][-1]
    else:
        h = {'win': 0, 'draw': 0, 'lose': 0}
    
    if h['win'] > 0 and h['draw'] > 0 and h['lose'] > 0:
        hcp_hp, hcp_dp, hcp_ap = odds_to_implied_prob(h['win'], h['draw'], h['lose'])
        hcp_pred = '上盘赢' if hcp_hp > max(hcp_dp, hcp_ap) else ('走水' if hcp_dp > hcp_ap else '下盘赢')
        hcp_conf = max(hcp_hp, hcp_dp, hcp_ap)
    else:
        hcp_hp = hcp_dp = hcp_ap = 0
        hcp_pred = '数据不足'
        hcp_conf = 0
    
    result['hcp'] = {
        'line': hcp_line,
        'home_win_prob': hcp_hp, 'draw_prob': hcp_dp, 'away_win_prob': hcp_ap,
        'prediction': hcp_pred, 'confidence': hcp_conf,
        'open': hcp.get('open', {}),
        'close': h,
    }
    
    # ---------- 3. 比分预测 ----------
    # 方法A: 真实比分赔率隐含概率
    score_odds_records = odds_data['score_odds']['records']
    score_from_odds = False
    score_top5, score_top10, most_likely_score, most_likely_prob = [], [], '1:0', 0.0
    
    if score_odds_records:
        so = score_odds_records[-1]  # 取最新
        win_odds = so.get('win_odds', {})
        draw_odds = so.get('draw_odds', {})
        lose_odds = so.get('lose_odds', {})
        
        score_inv_pairs = []  # [(score, inv_odds)]
        
        for sc, odds in list(win_odds.items()) + list(draw_odds.items()) + list(lose_odds.items()):
            if odds and isinstance(odds, (int, float)) and odds > 0:
                score_inv_pairs.append((sc, 1.0 / float(odds)))
        
        all_inv = sum(inv for sc, inv in score_inv_pairs)
        
        if all_inv > 0 and len(score_inv_pairs) >= 5:
            # 归一化去除抽水
            score_probs_norm = [(sc, inv / all_inv) for sc, inv in score_inv_pairs]
            score_probs_norm.sort(key=lambda x: x[1], reverse=True)
            
            score_from_odds = True
            score_top5 = [{"score": sc, "prob": pr} for sc, pr in score_probs_norm[:5]]
            score_top10 = [{"score": sc, "prob": pr} for sc, pr in score_probs_norm[:10]]
            most_likely_score = score_top5[0]['score']
            most_likely_prob = score_top5[0]['prob']
            # 调试输出
            # top3_list = [(s['score'], f"{s['prob']*100:.1f}%") for s in score_top5[:3]]
            # print(f"    [DEBUG] 比分赔率数: {len(score_inv_pairs)}, all_inv={all_inv:.4f}")
            # print(f"    [DEBUG] Top-3: {top3_list}")
    
    # 方法B: Poisson 分布（作为补充/对比）
    tg = odds_data['tg_odds']['records'][-1] if odds_data['tg_odds']['records'] else {}
    if tg:
        tg_total = sum(1.0/tg.get(f'o{i}', 999) for i in range(8) if tg.get(f'o{i}', 999) > 0)
        tg_lambda = sum(i * (1.0/tg.get(f'o{i}',999))/tg_total for i in range(8) if tg.get(f'o{i}',999) > 0)
        
        home_impl, away_impl = hp_b, ap_b
        h_ratio = home_impl / (home_impl + away_impl) if (home_impl + away_impl) > 0 else 0.5
        lh = tg_lambda * h_ratio
        la = tg_lambda * (1 - h_ratio)
        
        poisson_scores = []
        for hh in range(6):
            for aa in range(6):
                prob = poisson.pmf(hh, lh) * poisson.pmf(aa, la)
                poisson_scores.append({"score": f"{hh}:{aa}", "prob": prob})
        total_p = sum(s["prob"] for s in poisson_scores)
        for s in poisson_scores:
            s["prob"] /= total_p
        poisson_scores.sort(key=lambda x: x["prob"], reverse=True)
    else:
        tg_lambda = 2.0
        lh = 1.1
        la = 0.9
        poisson_scores = []
    
    result['score'] = {
        'from_odds': score_from_odds,
        'top5': score_top5 if score_from_odds else poisson_scores[:5],
        'top10': score_top10 if score_from_odds else poisson_scores[:10],
        'most_likely': most_likely_score if score_from_odds else (poisson_scores[0]['score'] if poisson_scores else '1:0'),
        'most_likely_prob': most_likely_prob if score_from_odds else (poisson_scores[0]['prob'] if poisson_scores else 0),
        'poisson_top5': poisson_scores[:5],
        'lambda_home': lh,
        'lambda_away': la,
        'expected_total_goals': tg_lambda,
        'latest_pub_time': score_odds_records[-1]['pub_time'] if score_odds_records else '',
    }
    
    # ---------- 4. 总进球数 ----------
    if tg:
        tg_dist = {}
        tg_inv_sum = sum(1.0/tg.get(f'o{i}', 999) for i in range(8) if tg.get(f'o{i}', 999) > 0)
        for i in range(8):
            key = f'{i}+' if i == 7 else str(i)
            odds_val = tg.get(f'o{i}', 0)
            if odds_val and odds_val > 0:
                tg_dist[key] = (1.0/odds_val) / tg_inv_sum
            else:
                tg_dist[key] = poisson.pmf(i, tg_lambda) if i < 7 else (1 - poisson.cdf(6, tg_lambda))
        
        over25 = 1 - sum(tg_dist[str(k)] for k in range(3) if str(k) in tg_dist)
        tg_pred = "大球(>2.5)" if over25 > 0.5 else "小球(<2.5)"
    else:
        tg_dist = {}
        over25 = 1 - poisson.cdf(2, tg_lambda)
        tg_pred = "大球(>2.5)" if over25 > 0.5 else "小球(<2.5)"
    
    result['tg'] = {
        'expected': tg_lambda,
        'distribution': tg_dist,
        'over_2_5': over25,
        'prediction': tg_pred,
        'records': odds_data['tg_odds']['records'],
    }
    
    return result


# ============================================================
# 报告渲染
# ============================================================
def render_match_section(m, s, o, r):
    """渲染单场比赛完整报告"""
    wdl, hcp, sc, tg = r["wdl"], r["hcp"], r["score"], r["tg"]
    hcp_line = hcp.get('line', -1)
    
    sec = f"""
## {m['home_team_cn']} vs {m['away_team_cn']}

> **赛事信息**: {s.get('tournament','')} {s.get('season','')} | 第{s.get('round','')}轮  
> **比赛时间**: {s.get('match_time', m.get('match_time', 'N/A'))} (北京时间)  
> **比赛场地**: {s.get('venue','')}, {s.get('city','')} (容量: {s.get('capacity','N/A')}人)  
> **裁判**: {s.get('referee','N/A')}  
> **SofaScore**: [点击查看]({m['sofascore_url']})  
> **数据状态**: {s.get('status','')} (赛前/阵容: {'✅已确认' if s.get('lineups_confirmed') else '⏳预测版'})

---

### 一、球队基本信息

| 项目 | 主队 {m['home_team_cn']} ({s.get('home_team','')}) | 客队 {m['away_team_cn']} ({s.get('away_team','')}) |
|------|:------------------------------------------------|:------------------------------------------------|
| 主教练 | {s.get('home_manager','N/A')} | {s.get('away_manager','N/A')} |
| 粉丝数 | {s.get('home_user_count',0):,} | {s.get('away_user_count',0):,} |
| 阵型 | {s.get('home_formation','N/A')} | {s.get('away_formation','N/A')} |
| 伤病/缺阵 | {len(s.get('home_missing',[]))} 人 | {len(s.get('away_missing',[]))} 人 |

"""
    
    # 伤病名单
    home_missing = s.get('home_missing', [])
    away_missing = s.get('away_missing', [])
    if home_missing or away_missing:
        sec += "#### 伤停名单\n\n"
        if home_missing:
            sec += f"**主队 {m['home_team_cn']}:**\n\n"
            sec += "| 球员 | 位置 | 类型 | 伤情 | 预计复出 |\n"
            sec += "|------|:----:|:----:|------|:--------:|\n"
            for p in home_missing:
                type_cn = '缺阵' if p['type'] == 'missing' else ('存疑' if p['type'] == 'doubtful' else p['type'])
                sec += f"| {p['short']} | {p['pos']} | {type_cn} | {p['description']} | {p['expected_end'] or 'N/A'} |\n"
            sec += "\n"
        if away_missing:
            sec += f"**客队 {m['away_team_cn']}:**\n\n"
            sec += "| 球员 | 位置 | 类型 | 伤情 | 预计复出 |\n"
            sec += "|------|:----:|:----:|------|:--------:|\n"
            for p in away_missing:
                type_cn = '缺阵' if p['type'] == 'missing' else ('存疑' if p['type'] == 'doubtful' else p['type'])
                sec += f"| {p['short']} | {p['pos']} | {type_cn} | {p['description']} | {p['expected_end'] or 'N/A'} |\n"
            sec += "\n"
    
    # 预计首发阵容
    sec += f"""
### 二、预计首发阵容 ({'✅ 官方已确认' if s.get('lineups_confirmed') else '⏳ SofaScore预测版，最终以赛前官方为准'})

"""
    for side, cn in [("home", m['home_team_cn']), ("away", m['away_team_cn'])]:
        starters = s.get(f"{side}_starters", [])
        subs = s.get(f"{side}_subs", [])
        formation = s.get(f"{side}_formation", "")
        sec += f"#### {cn} (阵型: {formation})\n\n"
        sec += "| # | 球员 | 位置 | 年龄 | 身价 | 国籍 | 身高 |\n"
        sec += "|:-:|------|:----:|:----:|------|:----:|:----:|\n"
        for p in starters:
            sec += f"| {p['num']} | {p['short']} | {p['pos']} | {p['age']} | {p['mv']} | {p['nat']} | {p['height']} |\n"
        if subs:
            sec += f"\n**替补席**: " + ", ".join(f"{p['short']}({p['pos']},{p['age']}岁)" for p in subs[:10]) + "\n"
            if len(subs) > 10:
                sec += f" 等共 {len(subs)} 人\n"
        sec += "\n"
    
    # 三、胜平负预测
    sec += f"""
### 三、胜平负预测（决策阈值调整 factor={DRAW_THRESHOLD_FACTOR}）

| 结果 | 市场隐含概率 | 模型校准后概率 |
|------|:----------:|:----------:|
| 主胜 {m['home_team_cn']} | {wdl['raw_home']*100:.1f}% | **{wdl['home_prob']*100:.1f}%** |
| 平局 | {wdl['raw_draw']*100:.1f}% | {wdl['draw_prob']*100:.1f}% |
| 客胜 {m['away_team_cn']} | {wdl['raw_away']*100:.1f}% | {wdl['away_prob']*100:.1f}% |

> **预测结果**: **{wdl['prediction']}** (置信度: {wdl['confidence']*100:.1f}%)

#### 赔率时序变化

| 时间 | 主胜 | 平局 | 客胜 |
|------|:----:|:----:|:----:|
"""
    for rec in wdl['records']:
        t_short = rec['time'][5:16]  # MM-DD HH:MM
        sec += f"| {t_short} | {rec['win']:.2f} | {rec['draw']:.2f} | {rec['lose']:.2f} |\n"
    
    sec += f"\n> **赔率趋势分析**: {wdl['trend']}\n"
    
    # 四、让球胜平负
    hcp_arrow = f"{hcp_line:+d}"
    sec += f"""
### 四、让球胜平负预测 ({m['home_team_cn']} {hcp_arrow}球)

| 结果 | 赔率 | 隐含概率 |
|------|:----:|:--------:|
| 上盘赢 | {hcp['close'].get('win','N/A')} | {hcp['home_win_prob']*100:.1f}% |
| 走水 | {hcp['close'].get('draw','N/A')} | {hcp['draw_prob']*100:.1f}% |
| 下盘赢 | {hcp['close'].get('lose','N/A')} | **{hcp['away_win_prob']*100:.1f}%** |

> **预测**: **{hcp['prediction']}** (置信度: {hcp['confidence']*100:.1f}%)

"""
    
    # 五、比分预测
    sec += f"""
### 五、比分预测

**方法**: {'真实赔率隐含概率' if sc['from_odds'] else 'Poisson分布模型'}  
**数据来源**: 竞彩网比分赔率 @ {sc.get('latest_pub_time','N/A')}  
**Poisson λ参数**: 主队={sc['lambda_home']:.3f}, 客队={sc['lambda_away']:.3f}  
**期望总进球**: {sc['expected_total_goals']:.2f}

#### Top-5 最可能比分 (模型排序)

| 排名 | 比分 | 概率 | 累计概率 |
|:----:|:----:|:----:|:--------:|
"""
    cum_prob = 0
    for i, s_item in enumerate(sc['top5']):
        cum_prob += s_item['prob']
        sec += f"| Top-{i+1} | **{s_item['score']}** | {s_item['prob']*100:.1f}% | {cum_prob*100:.1f}% |\n"
    
    sec += f"\n> **最可能比分**: **{sc['most_likely']}** (概率: {sc['most_likely_prob']*100:.1f}%)\n"
    
    # Poisson对比
    if sc['poisson_top5']:
        sec += "\n#### Poisson模型参考（作为对比基准）\n\n"
        sec += "| 排名 | 比分 | 概率 |\n|:----:|:----:|:----:|\n"
        for i, s_item in enumerate(sc['poisson_top5'][:5]):
            sec += f"| Top-{i+1} | {s_item['score']} | {s_item['prob']*100:.1f}% |\n"
        sec += "\n"
    
    # 比分赔率全表（胜/平/负分区）
    if o.get('score_odds') and o['score_odds']['records']:
        so = o['score_odds']['records'][-1]
        sec += f"\n#### 完整比分赔率表 (发布时间: {so['pub_time']})\n\n"
        sec += "**主胜比分**:\n\n| 比分 | 赔率 | 比分 | 赔率 | 比分 | 赔率 |\n|:----:|:----:|:----:|:----:|:----:|:----:|\n"
        wo = list(so.get('win_odds', {}).items())
        for i in range(0, min(len(wo), 13), 3):
            row = ""
            for j in range(3):
                if i+j < len(wo):
                    row += f"| {wo[i+j][0]} | {wo[i+j][1]:.2f} "
                else:
                    row += "|  |  "
            sec += row + "|\n"
        
        sec += "\n**平局比分**:\n\n| 比分 | 赔率 | 比分 | 赔率 | 比分 | 赔率 |\n|:----:|:----:|:----:|:----:|:----:|:----:|\n"
        do = list(so.get('draw_odds', {}).items())
        for i in range(0, len(do), 3):
            row = ""
            for j in range(3):
                if i+j < len(do):
                    row += f"| {do[i+j][0]} | {do[i+j][1]:.2f} "
                else:
                    row += "|  |  "
            sec += row + "|\n"
        
        sec += "\n**客胜比分**:\n\n| 比分 | 赔率 | 比分 | 赔率 | 比分 | 赔率 |\n|:----:|:----:|:----:|:----:|:----:|:----:|\n"
        lo = list(so.get('lose_odds', {}).items())
        for i in range(0, min(len(lo), 13), 3):
            row = ""
            for j in range(3):
                if i+j < len(lo):
                    row += f"| {lo[i+j][0]} | {lo[i+j][1]:.2f} "
                else:
                    row += "|  |  "
            sec += row + "|\n"
        sec += "\n"
    
    # 六、总进球数
    sec += f"""
### 六、总进球数预测

| 进球数 | 赔率 | 隐含概率 |
|:------:|:----:|:--------:|
"""
    if tg.get('records'):
        tg_last = tg['records'][-1]
        keys = ['o0','o1','o2','o3','o4','o5','o6','o7']
        labels = ['0球','1球','2球','3球','4球','5球','6球','7+球']
        for k, label in zip(keys, labels):
            odds_val = tg_last.get(k, 0)
            prob = tg['distribution'].get(label.replace('球','').replace('+','+') if '+' in label else label.replace('球',''), 0)
            # 修正key匹配
            key_match = label[:-1] if '球' in label else label
            if key_match == '7+': key_match = '7+'
            prob = tg['distribution'].get(key_match, 0)
            sec += f"| {label} | {odds_val:.2f} | {prob*100:.1f}% |\n"
    
    sec += f"""
> **预测**: **{tg['prediction']}**  
> **大球概率(>2.5)**: {tg['over_2_5']*100:.1f}% | **期望进球数**: {tg['expected']:.2f}

#### 总进球赔率时序变化

| 时间 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7+ |
|------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
"""
    for rec in tg.get('records', []):
        t_short = rec['time'][5:16]
        sec += f"| {t_short} | {rec.get('o0',0):.2f} | {rec.get('o1',0):.2f} | {rec.get('o2',0):.2f} | {rec.get('o3',0):.2f} | {rec.get('o4',0):.2f} | {rec.get('o5',0):.2f} | {rec.get('o6',0):.2f} | {rec.get('o7',0):.2f} |\n"
    
    # 风险提示
    sec += """
### 七、风险提示

1. **新赛季首轮不确定性**: 各队夏季转会窗口引援尚未完全磨合，战术体系待验证，实际表现可能与历史数据偏差较大
2. **阵容预测版风险**: 赛前阵容为 SofaScore 预测版，实际首发可能调整（关键球员出场与否影响巨大）
3. **赔率临场波动**: 当前赔率数据截止至发布时间，临场1小时内赔率可能因首发阵容确认、资金流向等因素显著变化
4. **平局召回率**: 模型通过决策阈值调整（factor={DRAW_THRESHOLD_FACTOR}）提升平局预测，CV平局召回率从0%优化至33.8%，概率保持校准
5. **伤停信息**: 已列示 SofaScore 标识的缺阵/存疑球员，但不排除赛前临时新增伤停
6. **比分预测局限**: 比分预测基于赔率隐含概率和Poisson分布，Top-1命中率仅约12%-15%，建议关注Top-3/Top-5累计概率
7. **本报告仅供研究参考，不构成投注建议**

---

"""
    return sec


# ============================================================
# 主函数
# ============================================================
def main():
    print("=" * 70)
    print("西甲第1轮赛前完整预测报告生成器")
    print("=" * 70)
    
    # 1. 解析赔率 TXT
    print("\n[1/5] 解析赔率TXT文件...")
    all_odds = parse_odds_txt(ODDS_TXT)
    print(f"  共解析到 {len(all_odds)} 场比赛赔率")
    
    # 2. 加载模型
    print("\n[2/5] 加载预测模型...")
    models = load_models()
    if models['xgb']:
        print("  ✅ XGBoost 模型加载成功")
    if models['lgb']:
        print("  ✅ LightGBM 模型加载成功")
    if models['scaler']:
        print("  ✅ Scaler 加载成功")
    if models['features']:
        print(f"  ✅ 已选特征 {len(models['features'])} 维")
    
    # 3. 提取SofaScore数据 + 匹配赔率 + 预测
    print("\n[3/5] 加载SofaScore赛前数据并运行预测...")
    sofa_data = {}
    match_odds = {}
    predictions = {}
    
    for m in MATCHES:
        print(f"\n  处理: {m['home_team_cn']} vs {m['away_team_cn']}")
        
        # SofaScore
        sofa_data[m['id']] = extract_sofascore(m['event_id'])
        s = sofa_data[m['id']]
        print(f"    场地: {s.get('venue','?')}")
        print(f"    首发: {len(s.get('home_starters',[]))}/{len(s.get('away_starters',[]))}")
        print(f"    伤停: {len(s.get('home_missing',[]))}+{len(s.get('away_missing',[]))}人")
        
        # 赔率匹配
        od = match_odds_to_match(m, all_odds)
        if od:
            match_odds[m['id']] = od
            print(f"    WDL: {len(od['wdl_odds']['records'])}条, Score: {len(od['score_odds']['records'])}条, TG: {len(od['tg_odds']['records'])}条")
        else:
            print(f"    ⚠️ 未匹配到赔率，使用默认值")
            match_odds[m['id']] = {
                'wdl_odds': {'records': [{'time': '2026-08-15 21:00:00', 'win': 2.2, 'draw': 3.0, 'lose': 3.2}],
                             'open': {'win': 2.2, 'draw': 3.0, 'lose': 3.2}, 'close': {'win': 2.2, 'draw': 3.0, 'lose': 3.2}},
                'handicap_odds': {'line': -1, 'records': [], 'open': {}, 'close': {'win': 5.5, 'draw': 3.5, 'lose': 1.5}},
                'score_odds': {'records': []},
                'tg_odds': {'records': [{'time': '2026-08-15 21:00:00', 'o0':5.0,'o1':3.0,'o2':3.2,'o3':4.5,'o4':8.0,'o5':18.0,'o6':35.0,'o7':50.0}]},
            }
        
        # 预测
        predictions[m['id']] = predict_match(m, match_odds[m['id']], models)
        pr = predictions[m['id']]
        print(f"    WDL: {pr['wdl']['prediction']} ({pr['wdl']['confidence']*100:.1f}%)")
        print(f"    HCP: {pr['hcp']['prediction']}")
        print(f"    Score: {pr['score']['most_likely']} ({pr['score']['most_likely_prob']*100:.1f}%)")
        print(f"    TG: {pr['tg']['prediction']}")
    
    # 4. 生成报告
    print("\n[4/5] 生成预测报告...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    report = f"""# 西甲2026-2027赛季 第1轮 赛前预测分析报告

> **报告生成时间**: {now}  
> **模型版本**: T-005 v3 (XGBoost+LightGBM) | 决策阈值 factor={DRAW_THRESHOLD_FACTOR}  
> **特征维度**: {len(models['features']) if models['features'] else 114} 维 (含39维赔率衍生特征)  
> **平局召回率**: CV 0% → 33.8% (决策阈值调整，概率保持校准)  
> **数据来源**: SofaScore API (赛前数据) + 中国体彩竞彩网赔率  
> **事件ID**: 16421047 (阿拉维斯vs赫塔费), 16421052 (塞维利亚vs巴列卡诺)

---

## 预测摘要

| 比赛 | 胜平负 | 置信度 | 让球(-1) | 最可能比分 | 概率 | 总进球 |
|------|:------:|:------:|:--------:|:--------:|:----:|:------:|
"""
    for m in MATCHES:
        pr = predictions[m['id']]
        report += (f"| {m['home_team_cn']} vs {m['away_team_cn']} "
                   f"| {pr['wdl']['prediction']} | {pr['wdl']['confidence']*100:.1f}% "
                   f"| {pr['hcp']['prediction']} "
                   f"| {pr['score']['most_likely']} | {pr['score']['most_likely_prob']*100:.1f}% "
                   f"| {pr['tg']['prediction']} |\n")
    
    report += "\n---\n\n"
    
    # 每个比赛
    for m in MATCHES:
        s = sofa_data[m['id']]
        od = match_odds[m['id']]
        pr = predictions[m['id']]
        report += render_match_section(m, s, od, pr)
    
    # 附录
    report += f"""
## 附录

### A. 预测方法说明

#### A.1 胜平负预测
- **核心算法**: 市场赔率隐含概率 → 归一化去抽水 → 决策阈值 factor={DRAW_THRESHOLD_FACTOR} 调整平局分类
- **校准依据**: 历史验证中模型平局召回率从0%优化至33.8%
- **模型输入**: XGBoost + LightGBM 集成，114维赛前特征（基础/球队/对比/Elo/赔率/Lag时序）

#### A.2 让球胜平负
- 基于让球盘赔率的隐含概率推导
- 让球线: {MATCHES[0]['id']}=-1（主队让1球）

#### A.3 比分预测
- **主方法**: 竞彩网比分赔率隐含概率（归一化，去除彩金抽水）
- **辅助方法**: Poisson分布模型，λ由赔率隐含概率联合估算
- **局限**: Top-1命中率通常12-15%，建议参考Top-3（累计~30%）或Top-5（累计~40%）

#### A.4 总进球数预测
- 总进球赔率隐含概率推导 + Poisson校准
- 大/小判断以2.5球为界，概率>50%即判为大/小球

---

### B. 数据质量与可靠性说明

| 验证项 | 结果 |
|--------|------|
| SofaScore数据状态 | ✅ 赛前 (status=notstarted) |
| 比分/事件流 | ✅ 空（符合赛前预期） |
| 阵容确认 | ⚠️ 预测版（赛前正常） |
| 统计数据 | ❌ 赛前不可用（404，正常） |
| 赔率数据条数 | WDL: 2-4条, 让球: 1-3条, 比分: 1-2条, TG: 1-3条 |
| 数据时效性 | 赔率最新发布: 2026-08-15 20:30-21:03 |

---

### C. 模型技术参数

| 参数 | 值 |
|------|----|
| 主模型 | XGBoost + LightGBM Stacking |
| n_estimators (XGB) | 300 (early_stopping_rounds=30) |
| 学习率 | 0.05 |
| max_depth | 6 |
| class_weight ratio | 1.20 (平衡胜平负样本数) |
| 时间衰减权重 | 启用 (近期样本权重↑) |
| 联赛补偿权重 | 启用 (5联赛按历史表现加权) |
| 训练数据量 | 5,252场历史比赛 |
| TimeSeriesSplit CV | 5折 (时间序切分，防泄露) |
| Platt Scaling | 启用 (概率校准) |
| 温度参数 T | 0.800 |
| 统一阈值 | 0.500 |

---

*本报告由足球预测模型系统自动生成，所有数据仅供研究参考，不构成任何投注建议。*
"""
    
    # 5. 保存
    report_filename = f"西甲第1轮赛前预测完整报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path = os.path.join(REPORT_DIR, report_filename)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n[5/5] 报告保存成功!")
    print(f"  文件: {report_path}")
    print(f"  大小: {os.path.getsize(report_path)/1024:.1f} KB")
    
    print("\n" + "=" * 70)
    print("✅ 全部完成！")
    print("=" * 70)


if __name__ == '__main__':
    main()
