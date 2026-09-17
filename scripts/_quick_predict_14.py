"""
快速14场预测脚本 v3 - 含 SofaScore 数据查询 + 无WDL赔率场次处理
用法: python scripts/_quick_predict_14.py
"""
import json, os, sys, re, warnings, time, sqlite3
import numpy as np
warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
os.chdir(PROJECT_DIR)

from prediction_core import init_models, PredictionCore, CalcEngine
from generate_unified_report import parse_odds_txt_auto
from feature_utils import normalize_team_name

# ============================================
# 目标14场比赛
# ============================================
TARGET_MATCHES = [
    ('2026-08-22 22:00', '英超', '埃弗顿', '水晶宫', '英超'),
    ('2026-08-22 22:00', '英超', '诺丁汉森林', '利兹联', '英超'),
    ('2026-08-22 22:00', '英超', '伊普斯维奇', '桑德兰', '英超'),
    ('2026-08-22 23:00', '西甲', '毕尔巴鄂竞技', '塞维利亚', '西甲'),
    ('2026-08-22 23:15', '法甲', '朗斯', '欧塞尔', '法甲'),
    ('2026-08-23 00:30', '英超', '布伦特福德', '托特纳姆热刺', '英超'),
    ('2026-08-23 00:30', '意甲', '乌迪内斯', '科莫', '意甲'),
    ('2026-08-23 00:30', '意甲', '国际米兰', '蒙扎', '意甲'),
    ('2026-08-23 01:30', '西甲', '巴伦西亚', '维戈塞尔塔', '西甲'),
    ('2026-08-23 02:45', '意甲', '热那亚', '那不勒斯', '意甲'),
    ('2026-08-23 02:45', '意甲', '帕尔马', '卡利亚里', '意甲'),
    ('2026-08-23 02:45', '法甲', '尼斯', '洛里昂', '法甲'),
    ('2026-08-23 02:45', '法甲', '图卢兹', '里昂', '法甲'),
    ('2026-08-23 03:30', '西甲', '西班牙人', '皇家马德里', '西甲'),
]

# ============================================
# SofaScore 数据查询
# ============================================
def query_sofascore_features(db_path, league, home_cn, away_cn):
    """查询 sofascore_team_features 表"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 正向查询
    cur.execute(
        'SELECT * FROM sofascore_team_features WHERE league=? AND home_team_cn=? AND away_team_cn=?',
        (league, home_cn, away_cn)
    )
    rows = cur.fetchall()
    if not rows:
        # 反向查询
        cur.execute(
            'SELECT * FROM sofascore_team_features WHERE league=? AND home_team_cn=? AND away_team_cn=?',
            (league, away_cn, home_cn)
        )
        rows = cur.fetchall()
    conn.close()
    return len(rows) > 0, len(rows)

# ============================================
# 主流程
# ============================================
def main():
    print("=" * 60)
    print("快速14场四维度预测 v3")
    print("最新模型: WDL 165维 LightGBM/XGBoost (slim_odds=True)")
    print("四维度: WDL(4模型Stacking) + T-005 v3(让球) + T-006 v4(比分) + 总进球")
    print("Monte Carlo n=500 | T=1.0 argmax | XGB已修复 | SofaScore已查询")
    print("=" * 60)

    # 1. 加载 TXT 赔率
    all_txt_matches = []
    txt_files = {
        '英超': 'data/英超2026-2027赛季完整时序赔率.txt',
        '西甲': 'data/西甲2026-2027赛季完整时序赔率.txt',
        '法甲': 'data/法甲2026-2027赛季完整时序赔率.txt',
        '意甲': 'data/意甲2026-2027赛季完整时序赔率.txt',
    }
    for league, fpath in txt_files.items():
        full_path = os.path.join(PROJECT_DIR, fpath)
        if os.path.exists(full_path):
            matches = parse_odds_txt_auto(full_path)
            all_txt_matches.extend(matches)
            print(f"  [TXT] {fpath}: {len(matches)} 场比赛")

    print(f"\n  TXT 总计: {len(all_txt_matches)} 场比赛")

    # 2. 初始化模型
    print("\n" + "=" * 60)
    print("加载模型...")
    print("=" * 60)
    t0 = time.time()
    models = init_models()
    print(f"模型加载完成: {time.time()-t0:.1f}s")
    print(f"  WDL: {models['wdl']['status']} (165维, slim_odds=True)")
    print(f"  T-005 v3: {models['t005']['status']}")
    print(f"  EPL: {'loaded (reference)' if models.get('epl') else 'N/A'}")

    core = PredictionCore(models)

    # 3. 逐场预测
    db_path = os.path.join(PROJECT_DIR, 'data', 'odds.db')
    results = []
    skipped = []

    for i, (match_time, league, home_cn, away_cn, txt_league) in enumerate(TARGET_MATCHES):
        match_date = match_time[:10]
        home_norm = normalize_team_name(home_cn)
        away_norm = normalize_team_name(away_cn)

        # 在 TXT 中匹配
        matched_odds = None
        for tm in all_txt_matches:
            h = normalize_team_name(tm['home_short'])
            a = normalize_team_name(tm['away_short'])
            if h == home_norm and a == away_norm:
                matched_odds = tm
                break
            if h == away_norm and a == home_norm:
                matched_odds = tm
                break

        if not matched_odds:
            print(f"\n  [{i+1}/14] {home_cn} vs {away_cn} ({league}) - TXT无匹配")
            skipped.append({'home': home_cn, 'away': away_cn, 'league': league, 'reason': 'TXT无匹配'})
            continue

        odds_data = matched_odds.get('odds_data', {})
        has_wdl = bool(odds_data.get('wdl_odds', {}).get('close'))
        has_hdp = bool(odds_data.get('handicap_odds', {}).get('close'))

        # 查询 SofaScore 数据
        sofa_has, sofa_rows = query_sofascore_features(db_path, league, home_norm, away_norm)

        # 构建 match dict
        match = {
            'home_team': home_norm,
            'away_team': away_norm,
            'home_team_cn': home_cn,
            'away_team_cn': away_cn,
            'league': league,
            'match_time': match_time,
        }

        print(f"\n{'='*60}")
        print(f"[{i+1}/14] {home_cn} vs {away_cn} ({league} {match_time})")
        if has_wdl:
            wdl_c = odds_data['wdl_odds']['close']
            print(f"WDL赔率: 胜={wdl_c['win']} 平={wdl_c['draw']} 负={wdl_c['lose']}")
        else:
            print(f"WDL赔率: 无 (竞彩未提供)")
        if has_hdp:
            hcp = odds_data['handicap_odds']
            print(f"让球盘: {hcp['line']:+d} 胜={hcp['close']['win']} 平={hcp['close']['draw']} 负={hcp['close']['lose']}")
        print(f"SofaScore: {'有数据('+str(sofa_rows)+'行)' if sofa_has else '无数据'}")
        print(f"{'='*60}")

        # 对于无WDL赔率的场次（如国际米兰让-2球），从让球赔率推算 λ
        if not has_wdl and has_hdp:
            print(f"  ⚠ 无WDL赔率，从让球赔率推算λ...")
            try:
                # 用让球赔率构造临时 odds_data 用于 λ 计算
                temp_odds = dict(odds_data)
                # 从让球赔率反推隐含概率
                hcp_c = odds_data['handicap_odds']['close']
                hcp_line = odds_data['handicap_odds']['line']
                # 过盘率归一化
                total = 1/hcp_c['win'] + 1/hcp_c['draw'] + 1/hcp_c['lose']
                p_up = (1/hcp_c['win']) / total
                p_push = (1/hcp_c['draw']) / total
                p_down = (1/hcp_c['lose']) / total
                print(f"  让球隐含: 上盘={p_up*100:.1f}% 走水={p_push*100:.1f}% 下盘={p_down*100:.1f}%")
                # 从让球盘推算 WDL λ（简化：用上盘/下盘概率作为方向信号）
                if hcp_line < 0:  # 主队让球
                    home_adv = -hcp_line
                    lambda_home = 1.8 + home_adv * 0.5
                    lambda_away = 1.0
                else:  # 客队让球
                    away_adv = hcp_line
                    lambda_home = 1.0
                    lambda_away = 1.8 + away_adv * 0.5
                print(f"  推算λ: home={lambda_home:.3f}, away={lambda_away:.3f}")
            except Exception as e:
                print(f"  ❌ λ推算失败: {e}")
                skipped.append({'home': home_cn, 'away': away_cn, 'league': league, 'reason': f'λ推算失败: {e}'})
                continue

        try:
            result = core.predict_unified(match, odds_data, is_mock=False)

            wdl = result.get('wdl', {})
            hcp = result.get('hcp', {})
            score = result.get('score', {})
            tg = result.get('tg', {})

            print(f"\n--- 预测汇总: {home_cn} vs {away_cn} ---")
            print(f"  WDL: {wdl.get('prediction', 'N/A')} (置信度: {wdl.get('confidence', 0)*100:.1f}%)")
            print(f"       主胜={wdl.get('home_prob', 0)*100:.1f}% 平局={wdl.get('draw_prob', 0)*100:.1f}% 客胜={wdl.get('away_prob', 0)*100:.1f}%")
            print(f"       方法: {wdl.get('method', 'N/A')}")
            print(f"  让球: {hcp.get('prediction', 'N/A')} (置信度: {hcp.get('confidence', 0)*100:.1f}%)")
            print(f"       上盘={hcp.get('home_win_prob', 0)*100:.1f}% 走水={hcp.get('draw_prob', 0)*100:.1f}% 下盘={hcp.get('away_win_prob', 0)*100:.1f}%")
            print(f"  比分: {score.get('most_likely', 'N/A')} ({score.get('most_likely_prob', 0)*100:.1f}%)")
            print(f"  总进球: {tg.get('prediction', 'N/A')} (大球: {tg.get('over_prob', 0)*100:.1f}%)")

            results.append({
                'home': home_cn,
                'away': away_cn,
                'league': league,
                'match_time': match_time,
                'wdl_odds': odds_data.get('wdl_odds', {}).get('close'),
                'sofascore_available': sofa_has,
                'sofascore_rows': sofa_rows,
                'wdl': wdl,
                'handicap': hcp,
                'score': score,
                'total_goals': tg,
            })

        except Exception as e:
            print(f"  ❌ 预测失败: {e}")
            import traceback
            traceback.print_exc()
            skipped.append({'home': home_cn, 'away': away_cn, 'league': league, 'reason': str(e)})

    # 4. 保存结果
    output_path = os.path.join(PROJECT_DIR, 'data', 'predictions_14_matches_20260823.json')
    output_data = {
        'model_version': 'v4.0 (20260823_112658)',
        'model_standards': {
            'wdl': '165维 LightGBM/XGBoost (slim_odds=True, 精简赔率30维)',
            'stacking': '4模型 Stacking (Poisson + DixonColes + SSM + Elo) + LGB + XGB',
            't005': 'T-005 v3 (71维, 两阶段 LGBMClassifier)',
            't006': 'T-006 v4 (Poisson+DC+MC融合, n=500)',
            'temperature': 'T=1.0 (argmax)',
            'monte_carlo': 'n=500',
            'epl_model': '独立参考 (epl_reference, 不参与Stacking)',
            'xgb_fix': '已修复 self.wdl_models[\'xgboost\'].predict',
        },
        'prediction_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'predictions': results,
        'skipped': skipped,
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n{'='*60}")
    print(f"结果已保存: {output_path}")
    print(f"共 {len(results)}/14 场比赛完成预测")
    if skipped:
        print(f"跳过 {len(skipped)} 场: {[s['home']+' vs '+s['away'] for s in skipped]}")
    print(f"{'='*60}")

if __name__ == '__main__':
    main()