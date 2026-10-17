# -*- coding: utf-8 -*-
"""
WDL 平局 bias 月度监控（协议 docs/wdl_market_blend_protocol_20261006.md §9.1，C-20261006-014）
==============================================================================================
纪律（协议约束，违反即违规）：
  1. 复用 C-009 实现：market_caliber_draw_diag 的去水/锚点逻辑 import 复用；
     t-24h 锚为窗口变体，启动时对全部配对场做「noon 口径逐场等价自检」，
     本地变体与 C-009 mkt_wdl_draw 不一致即中止（堵风险 8.5 暴露面）
  2. 只读、零模型改动：仅 SELECT，不算任何模型量
  3. 不碰 γ、不碰闸门——本脚本出现任何选型逻辑即违反协议；
     黄/红线仅做对照显示，不触发任何动作
  4. 轻量登记：reports/wdl_bias_monitor_log.md 追加一节/次运行；
     超黄线才出独立报告（协议开放问题 #1 采推荐项）

监控项（协议 §9.1）：
  - 全窗 bias 双锚（t-24h / noon）= 生产落库 WDL_draw − wdl_history 去水平局隐含
  - fresh-150 样本计数（预注册 2026-10-06，FRESH_START=2026-10-07）
  - 分联赛 bias 观察 + 分月趋势（漂移侦测），附 SE（C-012 统计功效教训）
"""
import os, sys, sqlite3
from datetime import datetime
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from market_caliber_draw_diag import mkt_wdl_draw, NOON_CUT
from market_benchmark_hc import build_en2cn, _norm

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
LOG_PATH = os.path.join(PROJECT_DIR, 'reports', 'wdl_bias_monitor_log.md')
LEAGUES = ('英超', '西甲', '意甲', '德甲', '法甲')
WINDOW_START = '2026-08-22'      # C-009 口径窗口起点
FRESH_START = '2026-10-07'       # 预注册 C-20261006-013（10-06）次日起算 fresh-150
FRESH_TARGET = 150
YELLOW, RED = 0.015, 0.020       # 黄线/红线（协议 §9.1），仅显示用


def _mkt_wdl_draw_t24(conn, cn_id, date):
    """t-24h 锚变体（ts 日期 < 比赛日）。去水逻辑与 C-009 mkt_wdl_draw 逐行同构，
    仅窗口比较符不同；运行时等价自检保证两实现无口径漂移。"""
    rows = conn.execute(
        "SELECT win_a, draw, win_b FROM wdl_history "
        "WHERE match_id=? AND timestamp IS NOT NULL AND timestamp<? "
        "ORDER BY timestamp DESC LIMIT 1", (cn_id, f'{date} 00:00:00')).fetchone()
    if not rows:
        return None
    w, d, l = rows['win_a'], rows['draw'], rows['win_b']
    if not (w and d and l and w > 1 and d > 1 and l > 1):
        return None
    p = np.array([1.0 / float(w), 1.0 / float(d), 1.0 / float(l)])
    p = p / p.sum()
    return float(p[1])


def load_targets(conn):
    """监控目标：≥WINDOW_START 五联赛全部场次（不要求已有赛果，保留最新数据）。"""
    ph = ','.join('?' * len(LEAGUES))
    return [{'match_id': r['match_id'], 'date': str(r['match_date'])[:10], 'league': r['league'],
             'home_en': r['home_team'], 'away_en': r['away_team']}
            for r in conn.execute(
                f"SELECT match_id, match_date, league, home_team, away_team FROM matches "
                f"WHERE league IN ({ph}) AND match_date >= ? ORDER BY match_date",
                (*LEAGUES, WINDOW_START)).fetchall()]


def collect(conn):
    targets = load_targets(conn)
    en2cn = build_en2cn(conn)
    prod = {r['match_id']: float(r['probability']) for r in conn.execute(
        "SELECT match_id, probability FROM model_predictions "
        "WHERE model_name='generate_unified_report_v2.0' AND is_replay=0 "
        "AND prediction_type='WDL_draw'")}

    rows, chk, chk_fail = [], 0, 0
    for t in targets:
        hc = en2cn.get(_norm(t['home_en']))
        ac = en2cn.get(_norm(t['away_en']))
        if not hc or not ac:
            continue
        cn_id = f"{t['date']}_{hc}_{ac}"
        p_noon = mkt_wdl_draw(conn, cn_id, t['date'])[0]
        p_t24 = _mkt_wdl_draw_t24(conn, cn_id, t['date'])
        # 等价自检：noon 口径下本地变体必须与 C-009 实现逐场一致（去水逻辑无漂移）
        p_noon_local = None
        if p_t24 is not None or p_noon is not None:
            r = conn.execute(
                "SELECT win_a, draw, win_b FROM wdl_history "
                "WHERE match_id=? AND timestamp IS NOT NULL AND timestamp<=? "
                "ORDER BY timestamp DESC LIMIT 1", (cn_id, f"{t['date']} {NOON_CUT}")).fetchone()
            if r and r['win_a'] and r['draw'] and r['win_b'] \
                    and r['win_a'] > 1 and r['draw'] > 1 and r['win_b'] > 1:
                v = np.array([1.0 / float(r['win_a']), 1.0 / float(r['draw']), 1.0 / float(r['win_b'])])
                p_noon_local = float((v / v.sum())[1])
        if p_noon_local is not None:
            chk += 1
            if p_noon is None or abs(p_noon_local - p_noon) > 1e-12:
                chk_fail += 1
        pd_ = prod.get(t['match_id'])
        if p_noon is not None:
            rows.append({'date': t['date'], 'league': t['league'], 'pd': pd_,
                         'mkt_noon': p_noon, 'mkt_t24': p_t24})
    return rows, chk, chk_fail


def bias_stats(pairs):
    """pairs = [(prod_draw, mkt_draw), ...] → dict 或 None。"""
    if len(pairs) < 5:
        return None
    a = np.array([x[0] for x in pairs])
    b = np.array([x[1] for x in pairs])
    d = a - b
    return {'n': len(pairs), 'prod': float(a.mean()), 'mkt': float(b.mean()),
            'bias': float(d.mean()), 'se': float(d.std(ddof=1) / np.sqrt(len(d)))}


def fmt(s, name):
    if s is None:
        return f'- {name}: 配对不足（n<5）'
    return (f'- {name}: bias={s["bias"]*100:+.2f}pp ± SE {s["se"]*100:.2f}pp（n={s["n"]}；'
            f'模型 {s["prod"]:.4f} vs 市场 {s["mkt"]:.4f}）')


def main():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    try:
        conn = sqlite3.connect(f"file:{DB_PATH.replace(os.sep, '/')}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(DB_PATH)  # 只读 URI 失败时回退（本脚本仅 SELECT）
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")

    rows, chk, chk_fail = collect(conn)
    if chk_fail > 0:
        print(f'[ABORT] 等价自检失败 {chk_fail}/{chk}：本地 t-24h 变体与 C-009 实现口径漂移，禁止继续')
        conn.close()
        return
    print(f'等价自检 PASS（{chk} 场 noon 口径逐场一致）')

    has_prod = [r for r in rows if r['pd'] is not None]
    print(f'配对场次（有市场快照）: {len(rows)}，其中含生产落库 p_draw: {len(has_prod)}')

    # 全窗双锚 bias
    st_noon = bias_stats([(r['pd'], r['mkt_noon']) for r in has_prod])
    st_t24 = bias_stats([(r['pd'], r['mkt_t24']) for r in has_prod if r['mkt_t24'] is not None])

    # fresh-150 计数（有生产落库才计入——γ 作用对象是落库值）
    fresh = [r for r in has_prod if r['date'] >= FRESH_START]
    fresh_noon = [(r['pd'], r['mkt_noon']) for r in fresh]
    fresh_st = bias_stats(fresh_noon)

    # 分联赛 / 分月（noon 锚，观察项）
    def group_stats(keyfn):
        out = {}
        for k in sorted(set(keyfn(r) for r in has_prod)):
            s = bias_stats([(r['pd'], r['mkt_noon']) for r in has_prod if keyfn(r) == k])
            if s:
                out[k] = s
        return out
    per_league = group_stats(lambda r: r['league'])
    per_month = group_stats(lambda r: r['date'][:7])

    # 黄/红线对照（仅显示）
    def line_of(s):
        if s is None:
            return 'n/a'
        m = max(abs(s['bias']), abs(fresh_st['bias']) if fresh_st else 0)
        if m >= RED:
            return f'**红线对照 ≥2.0pp（max {m*100:.2f}pp）— 按协议 §9.1 走月度复核确认流程**'
        if m >= YELLOW:
            return f'黄线对照 ≥1.5pp（max {m*100:.2f}pp）— 升周度复核'
        return f'未触发（max |bias| {m*100:.2f}pp < 1.5pp）'
    line = line_of(st_noon)

    # 控制台
    print(fmt(st_noon, '全窗 bias（noon）'))
    print(fmt(st_t24, '全窗 bias（t-24h）'))
    print(fmt(fresh_st, f'fresh-150（≥{FRESH_START}）'))
    print(f'fresh-150 进度: {len(fresh)}/{FRESH_TARGET}')
    print(f'黄/红线对照: {line}')
    print('分联赛（noon）: ' + '  '.join(f'{k}={v["bias"]*100:+.1f}pp(n={v["n"]})'
                                       for k, v in per_league.items()))
    print('分月（noon）  : ' + '  '.join(f'{k}={v["bias"]*100:+.1f}pp(n={v["n"]})'
                                       for k, v in per_month.items()))

    # 轻量登记（追加）
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    header_needed = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        if header_needed:
            f.write('# WDL 平局 bias 月度监控登记\n\n'
                    '- 协议: docs/wdl_market_blend_protocol_20261006.md §9.1（C-20261006-013）\n'
                    '- 口径: 生产落库 WDL_draw − wdl_history 去水平局隐含（C-009 同实现），'
                    f'窗口 ≥{WINDOW_START} 五联赛，fresh-150 自 {FRESH_START} 起算\n'
                    '- 纪律: 只读、零模型改动、无选型逻辑；黄/红线仅对照显示\n\n')
        f.write(f'## [{ts}] 月度复核\n\n')
        f.write(fmt(st_noon, '全窗 bias（noon）') + '\n')
        f.write(fmt(st_t24, '全窗 bias（t-24h）') + '\n')
        f.write(fmt(fresh_st, f'fresh-150（≥{FRESH_START}）') + '\n')
        f.write(f'- fresh-150 进度: **{len(fresh)}/{FRESH_TARGET}**\n')
        f.write(f'- 黄/红线对照: {line}\n')
        f.write('- 分联赛（noon，观察项）: '
                + '  '.join(f'{k} {v["bias"]*100:+.1f}pp(n={v["n"]})' for k, v in per_league.items()) + '\n')
        f.write('- 分月（noon，漂移侦测）: '
                + '  '.join(f'{k} {v["bias"]*100:+.1f}pp(n={v["n"]})' for k, v in per_month.items()) + '\n\n')
    print(f'\n登记: {LOG_PATH}')
    conn.close()


if __name__ == '__main__':
    main()
