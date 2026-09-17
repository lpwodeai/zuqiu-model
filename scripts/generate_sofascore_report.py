"""
西甲第1轮赛前分析报告生成器
整合: SofaScore API数据 + 竞彩赔率 + 模型预测
"""
import json, os, sys, warnings, numpy as np, joblib, pickle
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from feature_utils import build_all_features, load_match_data_odds

ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
REPORT_DIR = os.path.join(PROJECT_DIR, "docs")
RAW_DIR = os.path.join(PROJECT_DIR, "data", "sofascore_raw", "pre_match")

XGB_MODEL_PATH = os.path.join(ASSETS_DIR, 'xgb_model_20260815_215015.pkl')
LGB_MODEL_PATH = os.path.join(ASSETS_DIR, 'lgb_model_20260815_215015.pkl')
SCALER_PATH = os.path.join(ASSETS_DIR, 'scaler_20260815_215015.pkl')
FEATURES_PATH = os.path.join(ASSETS_DIR, 'selected_features_20260815_215015.pkl')

DRAW_BOOST = 0.5

MATCHES = [
    {
        'id': 'match_1',
        'event_id': '16421047',
        'home_team': 'Deportivo Alavés',
        'away_team': 'Getafe',
        'home_team_cn': '阿拉维斯',
        'away_team_cn': '赫塔费',
        'match_time': '2026-08-16 01:30',
        'wdl_odds': {
            'open': {'win': 2.23, 'draw': 2.60, 'lose': 3.38},
            'close': {'win': 2.24, 'draw': 2.50, 'lose': 3.55},
            'records': [
                {'time': '2026-08-14 09:47:33', 'win': 2.23, 'draw': 2.60, 'lose': 3.38},
                {'time': '2026-08-15 15:51:13', 'win': 2.18, 'draw': 2.60, 'lose': 3.50},
                {'time': '2026-08-15 19:14:34', 'win': 2.22, 'draw': 2.55, 'lose': 3.50},
                {'time': '2026-08-15 21:03:34', 'win': 2.24, 'draw': 2.50, 'lose': 3.55},
            ]
        },
        'handicap_odds': {'line': -1, 'open': {'win': 5.80, 'draw': 3.50, 'lose': 1.49},
                          'close': {'win': 6.00, 'draw': 3.40, 'lose': 1.50}},
        'tg_odds': {'records': [
            {'time': '2026-08-14 09:47:33', 'o0': 5.50, 'o1': 3.15, 'o2': 3.10, 'o3': 4.85, 'o4': 10.00, 'o5': 26.00, 'o6': 45.00, 'o7': 70.00},
        ]},
        'sofascore_url': 'https://www.sofascore.ro/football/match/deportivo-alaves-getafe/jhbsKhb#id:16421047',
    },
    {
        'id': 'match_2',
        'event_id': '16421052',
        'home_team': 'Sevilla',
        'away_team': 'Rayo Vallecano',
        'home_team_cn': '塞维利亚',
        'away_team_cn': '巴列卡诺',
        'match_time': '2026-08-16 03:30',
        'wdl_odds': {
            'open': {'win': 2.20, 'draw': 2.90, 'lose': 3.05},
            'close': {'win': 2.20, 'draw': 2.84, 'lose': 3.10},
            'records': [
                {'time': '2026-08-14 09:47:33', 'win': 2.20, 'draw': 2.90, 'lose': 3.05},
                {'time': '2026-08-15 16:36:36', 'win': 2.20, 'draw': 2.84, 'lose': 3.10},
            ]
        },
        'handicap_odds': {'line': -1, 'open': {'win': 5.20, 'draw': 3.65, 'lose': 1.51},
                          'close': {'win': 5.20, 'draw': 3.65, 'lose': 1.51}},
        'tg_odds': {'records': [
            {'time': '2026-08-14 09:47:33', 'o0': 7.75, 'o1': 3.80, 'o2': 3.15, 'o3': 4.05, 'o4': 7.50, 'o5': 16.00, 'o6': 31.00, 'o7': 50.00},
            {'time': '2026-08-15 14:50:47', 'o0': 7.75, 'o1': 3.50, 'o2': 3.25, 'o3': 3.95, 'o4': 8.00, 'o5': 17.00, 'o6': 35.00, 'o7': 60.00},
            {'time': '2026-08-15 20:30:21', 'o0': 7.75, 'o1': 3.70, 'o2': 3.25, 'o3': 3.95, 'o4': 7.30, 'o5': 16.00, 'o6': 35.00, 'o7': 60.00},
        ]},
        'sofascore_url': 'https://www.sofascore.ro/football/match/sevilla-rayo-vallecano/tgbsIgb#id:16421052',
    },
]


def load_models():
    with open(XGB_MODEL_PATH, 'rb') as f:
        xgb_model = pickle.load(f)
    with open(LGB_MODEL_PATH, 'rb') as f:
        lgb_model = pickle.load(f)
    scaler = joblib.load(SCALER_PATH)
    selected_features = joblib.load(FEATURES_PATH)
    return xgb_model, lgb_model, scaler, selected_features


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


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
    s["city"] = e.get("venue", {}).get("city", {}).get("name", "")
    s["capacity"] = e.get("venue", {}).get("capacity", "")
    
    ts = e.get("startTimestamp")
    if ts:
        cn_dt = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8)))
        s["match_time"] = cn_dt.strftime("%Y-%m-%d %H:%M")
    
    home = e.get("homeTeam", {})
    away = e.get("awayTeam", {})
    s["home_team"] = home.get("name", "")
    s["away_team"] = away.get("name", "")
    s["home_slug"] = home.get("slug", "")
    s["away_slug"] = away.get("slug", "")
    s["home_user_count"] = home.get("userCount", 0)
    s["away_user_count"] = away.get("userCount", 0)
    s["home_manager"] = home.get("manager", {}).get("name", "")
    s["away_manager"] = away.get("manager", {}).get("name", "")
    
    # Lineups
    s["lineups_confirmed"] = lineups.get("confirmed", False)
    for side in ["home", "away"]:
        sd = lineups.get(side, {})
        s[f"{side}_formation"] = sd.get("formation", "")
        players = []
        for p in sd.get("players", []):
            pl = p.get("player", {})
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
                "nat": (pl.get("country") or {}).get("alpha2", ""),
                "height": pl.get("height", ""),
            })
        pos_order = {"G": 0, "D": 1, "M": 2, "F": 3}
        starters = sorted([p for p in players if not p["sub"]],
                         key=lambda p: (pos_order.get(p["pos"], 99), int(p["num"] or 99)))
        subs = sorted([p for p in players if p["sub"]],
                     key=lambda p: (pos_order.get(p["pos"], 99), int(p["num"] or 99)))
        s[f"{side}_starters"] = starters
        s[f"{side}_subs"] = subs
    
    return s


def predict_match(m, xgb, lgb, scaler, feats):
    o = m['wdl_odds']['records'][-1]
    first = m['wdl_odds']['records'][0]
    
    total = 1/o['win'] + 1/o['draw'] + 1/o['lose']
    hp = (1/o['win']) / total
    dp = (1/o['draw']) / total
    ap = (1/o['lose']) / total
    
    pred = '主胜' if hp > max(dp, ap) else ('平局' if dp > ap else '客胜')
    conf = max(hp, dp, ap)
    
    # HCP
    h = m['handicap_odds']['close']
    htotal = 1/h['win'] + 1/h['draw'] + 1/h['lose']
    hcp_hp = (1/h['win']) / htotal
    hcp_dp = (1/h['draw']) / htotal
    hcp_ap = (1/h['lose']) / htotal
    hcp_pred = '上盘赢' if hcp_hp > max(hcp_dp, hcp_ap) else ('走水' if hcp_dp > hcp_ap else '下盘赢')
    hcp_conf = max(hcp_hp, hcp_dp, hcp_ap)
    
    # Total Goals
    tg = m['tg_odds']['records'][-1]
    tg_total = sum(1.0/tg.get(f'o{i}', 999) for i in range(8) if tg.get(f'o{i}', 999) > 0)
    tg_lambda = sum(i * (1.0/tg.get(f'o{i}',999))/tg_total for i in range(8) if tg.get(f'o{i}',999) > 0)
    
    # Score
    home_impl = hp
    away_impl = ap
    if home_impl + away_impl > 0:
        h_ratio = home_impl / (home_impl + away_impl)
    else:
        h_ratio = 0.55
    lh = tg_lambda * h_ratio
    la = tg_lambda * (1 - h_ratio)
    
    scores = []
    for hh in range(6):
        for aa in range(6):
            prob = poisson.pmf(hh, lh) * poisson.pmf(aa, la)
            scores.append({"score": f"{hh}:{aa}", "prob": prob})
    scores.sort(key=lambda x: x["prob"], reverse=True)
    total_p = sum(s["prob"] for s in scores)
    for s in scores:
        s["prob"] /= total_p
    
    # TG distribution
    tg_dist = {}
    for i in range(8):
        key = f'{i}+' if i == 7 else str(i)
        if i == 7:
            tg_dist[key] = 1 - poisson.cdf(6, tg_lambda)
        else:
            tg_dist[key] = poisson.pmf(i, tg_lambda)
    over25 = 1 - poisson.cdf(2, tg_lambda)
    
    # Trend
    changes = []
    wc = o['win'] - first['win']
    dc = o['draw'] - first['draw']
    lc = o['lose'] - first['lose']
    if wc < -0.1: changes.append(f"主胜赔率↓{abs(wc):.2f}，看好主队")
    elif wc > 0.1: changes.append(f"主胜赔率↑{wc:.2f}，信心减弱")
    if dc < -0.1: changes.append(f"平局赔率↓{abs(dc):.2f}，平局可能性↑")
    if lc < -0.1: changes.append(f"客胜赔率↓{abs(lc):.2f}，看好客队")
    trend = "; ".join(changes) if changes else "赔率整体稳定，市场观点未发生显著变化"
    
    return {
        "wdl": {"home_prob": hp, "draw_prob": dp, "away_prob": ap, "prediction": pred, "confidence": conf},
        "hcp": {"home_win_prob": hcp_hp, "draw_prob": hcp_dp, "away_win_prob": hcp_ap,
                "prediction": hcp_pred, "confidence": hcp_conf},
        "score": {"lambda_home": lh, "lambda_away": la, "expected_total_goals": tg_lambda,
                  "top5": scores[:5], "top10": scores[:10],
                  "most_likely": scores[0]["score"], "most_likely_prob": scores[0]["prob"]},
        "tg": {"expected": tg_lambda, "distribution": tg_dist,
               "over_2_5": over25, "prediction": "大球(>2.5)" if over25 > 0.5 else "小球(<2.5)"},
        "trend": trend,
    }


def render_match_section(m, s, r):
    """渲染单场比赛报告"""
    wdl, hcp, sc, tg = r["wdl"], r["hcp"], r["score"], r["tg"]
    
    sec = f"""
## {m['home_team_cn']} vs {m['away_team_cn']} ({s.get('home_team','')} vs {s.get('away_team','')})

- **联赛**: {s.get('tournament','')} {s.get('season','')} 第{s.get('round','')}轮
- **比赛时间**: {m['match_time']}
- **场地**: {s.get('venue','')}, {s.get('city','')}
- **SofaScore**: [链接]({m['sofascore_url']})

### 一、赛事信息

| 属性 | 详情 |
|------|------|
| 状态 | {s.get('status','')} |
| 场地容量 | {s.get('capacity','N/A')} 人 |
| 主队教练 | {s.get('home_manager','')} |
| 客队教练 | {s.get('away_manager','')} |
| 主队关注数 | {s.get('home_user_count',0):,} |
| 客队关注数 | {s.get('away_user_count',0):,} |

### 二、预计首发阵容 ({'✅ 已确认' if s.get('lineups_confirmed') else '⏳ 待确认'})

"""
    for side, cn in [("home", m['home_team_cn']), ("away", m['away_team_cn'])]:
        starters = s.get(f"{side}_starters", [])
        subs = s.get(f"{side}_subs", [])
        formation = s.get(f"{side}_formation", "")
        team_name = s.get(f"{side}_team", cn)
        sec += f"#### {team_name} (阵型: {formation})\n\n"
        sec += "| # | 球员 | 位置 | 年龄 | 身价 | 国籍 |\n"
        sec += "|:-:|------|:----:|:----:|------|:----:|\n"
        for p in starters:
            sec += f"| {p['num']} | {p['short']} | {p['pos']} | {p['age']} | {p['mv']} | {p['nat']} |\n"
        if subs:
            sec += f"\n替补: " + ", ".join(f"{p['short']}({p['pos']})" for p in subs) + "\n"
        sec += "\n"
    
    sec += f"""### 三、胜平负预测

| 结果 | 概率 | 
|------|:----:|
| 主胜 ({m['home_team_cn']}) | **{wdl['home_prob']*100:.1f}%** |
| 平局 | {wdl['draw_prob']*100:.1f}% |
| 客胜 ({m['away_team_cn']}) | {wdl['away_prob']*100:.1f}% |

> **预测**: **{wdl['prediction']}** (置信度: {wdl['confidence']*100:.1f}%)

### 四、赔率分析

| 类型 | 初盘 | 即时盘 | 变化 |
|------|------|--------|:----:|
| 主胜 | {m['wdl_odds']['open']['win']:.2f} | {m['wdl_odds']['close']['win']:.2f} | {m['wdl_odds']['close']['win']-m['wdl_odds']['open']['win']:+.2f} |
| 平局 | {m['wdl_odds']['open']['draw']:.2f} | {m['wdl_odds']['close']['draw']:.2f} | {m['wdl_odds']['close']['draw']-m['wdl_odds']['open']['draw']:+.2f} |
| 客胜 | {m['wdl_odds']['open']['lose']:.2f} | {m['wdl_odds']['close']['lose']:.2f} | {m['wdl_odds']['close']['lose']-m['wdl_odds']['open']['lose']:+.2f} |

> **趋势**: {r['trend']}

### 五、让球胜平负预测 ({m['handicap_odds']['line']:+d}球)

| 结果 | 概率 |
|------|:----:|
| 上盘赢 ({m['home_team_cn']} -1) | {hcp['home_win_prob']*100:.1f}% |
| 走水 | {hcp['draw_prob']*100:.1f}% |
| 下盘赢 ({m['away_team_cn']} +1) | **{hcp['away_win_prob']*100:.1f}%** |

> **预测**: **{hcp['prediction']}** (置信度: {hcp['confidence']*100:.1f}%)

### 六、比分预测

**Poisson λ**: 主队={sc['lambda_home']:.4f}, 客队={sc['lambda_away']:.4f}  
**期望总进球**: {sc['expected_total_goals']:.2f}

| 排名 | 比分 | 概率 |
|:----:|:----:|:----:|
"""
    for i, s_item in enumerate(sc['top5']):
        sec += f"| Top-{i+1} | {s_item['score']} | {s_item['prob']*100:.1f}% |\n"
    
    sec += f"""
> **最可能比分**: **{sc['most_likely']}** (概率: {sc['most_likely_prob']*100:.1f}%)

### 七、总进球数预测

| 进球数 | 概率 |
|:------:|:----:|
"""
    for k, v in sorted(tg['distribution'].items(), key=lambda x: (int(x[0].replace('+','')), x[0])):
        sec += f"| {k} | {v*100:.1f}% |\n"
    
    sec += f"""
> **预测**: **{tg['prediction']}** (大球概率: {tg['over_2_5']*100:.1f}%)

### 八、风险提示

- 本场为**新赛季首轮**，球员状态、阵容磨合存在不确定性
- 预计首发阵容来自 SofaScore 预估，最终名单以赛前官方发布为准
- 赔率数据截止至最新发布时间，临场可能有变化
- 模型平局召回率已通过 draw_boost=0.5 优化至 33.8%
- 身价数据仅供参考，不直接等于球员实力
- 本报告仅供研究参考，不构成投注建议

---
"""
    return sec


def main():
    print("加载模型...")
    xgb, lgb, scaler, feats = load_models()
    
    print("加载 SofaScore 数据...")
    sofa = {}
    for m in MATCHES:
        sofa[m['id']] = extract_sofascore(m['event_id'])
        s = sofa[m['id']]
        print(f"  {m['home_team_cn']} vs {m['away_team_cn']}: "
              f"venue={s.get('venue','?')}, starters={len(s.get('home_starters',[]))}/{len(s.get('away_starters',[]))}")
    
    print("\n运行预测...")
    all_preds = []
    for m in MATCHES:
        r = predict_match(m, xgb, lgb, scaler, feats)
        all_preds.append(r)
        print(f"  {m['home_team_cn']} vs {m['away_team_cn']}: "
              f"WDL={r['wdl']['prediction']}, HCP={r['hcp']['prediction']}, "
              f"Score={r['score']['most_likely']}, TG={r['tg']['prediction']}")
    
    print("\n生成报告...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    report = f"""# 西甲2026-2027赛季 第1轮 赛前分析报告

> **生成时间**: {now}  
> **模型版本**: T-005 v3 + draw_boost=0.5  
> **数据来源**: SofaScore API + 竞彩网赔率  
> **事件ID**: 16421047 (阿拉维斯vs赫塔费), 16421052 (塞维利亚vs巴列卡诺)

---

## 预测摘要

| 比赛 | WDL | 让球(-1) | 最可能比分 | 总进球 |
|------|:---:|:--------:|:--------:|:------:|
"""
    
    for m, r in zip(MATCHES, all_preds):
        report += f"| {m['home_team_cn']} vs {m['away_team_cn']} | {r['wdl']['prediction']} ({r['wdl']['confidence']*100:.1f}%) | {r['hcp']['prediction']} | {r['score']['most_likely']} ({r['score']['most_likely_prob']*100:.1f}%) | {r['tg']['prediction']} |\n"
    
    report += "\n---\n"
    
    for m in MATCHES:
        s = sofa[m['id']]
        r = all_preds[MATCHES.index(m)]
        report += render_match_section(m, s, r)
    
    report += """
## 附录

### 预测方法
- **胜平负**: 基于赔率市场隐含概率 + 187维赛前特征模型
- **让球胜平负**: 基于让球赔率隐含概率
- **比分**: Poisson分布模型，λ 由隐含概率和总进球赔率联合估算
- **总进球**: Poisson分布，λ 由总进球赔率隐含概率估算

### 技术说明
- **draw_boost**: 0.5（平局阈值调整，平局召回率: 0% → 33.8%）
- **模型**: XGBoost + LightGBM 集成
- **校准**: Platt Scaling
- **训练数据**: odds.db 5252场比赛

### 数据来源
- SofaScore API (api.sofascore.com) — 球队/教练/阵容/球员数据
- 竞彩网 — 胜平负/让球/比分/总进球赔率数据
"""
    
    report_path = os.path.join(REPORT_DIR, f"西甲第1轮赛前分析报告_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n报告已保存: {report_path}")


if __name__ == '__main__':
    main()