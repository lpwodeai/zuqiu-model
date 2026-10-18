# -*- coding: utf-8 -*-
"""C-20261018-144 ①: as-of 全量重放（n=297）→ 诚实版 8 系列三指标

背景：C-143 裁决 Elo 泄漏成立——0a-6/0a-7 赛果回填 → 0c Elo 快照无日期截止重建
→ 跨日重生成时子模型表 live 重算，报告 MD 的 Elo 行被赛后快照污染
（k*≥+1 占比 62.6%、corr(Δd,赛果)+0.494、命中差 +8.4pp）。
C-142 的 8 系列读数已标记「污染口径，待 as-of 全量重放修订」。

本脚本按裁决书口径产出诚实版：
  - Elo 行：用 analyze_elo_leak_audit 已验证的 replay_elo/asof_lookup（严格 date<比赛日）
    重放 (elo,momentum)，按生产 calc_win_draw_lose_elo 公式重建 WDL 概率。
  - XGBoost/LightGBM：重放对拍坐实其输出对 10 维 Elo 特征不敏感（swap vs raw
    场均|Δ|≈1.4-1.6pp），Elo 泄漏无法经此通道污染 → 主表复用 C-142 MD 读数；
    诊断变体（raw=今日重建矩阵原值 / swap=10 维 Elo 强制覆写 as-of）保留在 §零
    保真对拍。raw vs MD ≈15pp 为报告时点特征漂移（赔率快照/模型版本/时序累积），
    非 Elo 通道。
  - Dixon-Coles / 贝叶斯：已核实不消费 Elo 特征（DC λ 纯赔率反演；
    bayesian_hierarchical_model.py 全文无 elo），天然干净，直接复用 C-142 MD 读数。
  - 简单平均 / Stacking：用重放后 5 系列按生产机制重算（LR meta-learner 优先，
    固定权重 STACKING_WEIGHTS 回退，dc_odds_fallback=0.0）。
  - 市场去水：复用 C-142 MD 读数。

用法:
    python scripts/analyze_submodel_wdl_asof_replay.py            # 全量重放 + 写报告
    python scripts/analyze_submodel_wdl_asof_replay.py --probe 20 # 前 20 场保真探针，不写报告
"""
import glob
import math
import os
import re
import sqlite3
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from team_names import normalize_team_name as norm
import feature_utils
from feature_utils import load_match_data_odds, build_all_features
from feature_utils import normalize_team_name as fu_norm
from elo_rating import DEFAULT_ELO, HOME_ADVANTAGE, expected_score
from analyze_elo_leak_audit import replay_elo, asof_lookup
from analyze_submodel_wdl_calibration import (
    REPORT_GLOB, DB, MODELS, FN_PAT, parse_report, rps,
)
import prediction_core as pc

OUT = os.path.join(BASE, "reports", "submodel_wdl_calibration_asof_20261018.md")

ELO10 = [
    'home_elo', 'away_elo', 'elo_diff', 'elo_ratio',
    'elo_home_expected', 'elo_away_expected', 'elo_draw_prob',
    'home_elo_momentum', 'away_elo_momentum', 'elo_confidence',
]


def elo10_from_asof(eh, mh, ea, ma):
    """按生产 elo_rating.build_elo_features 公式，用 as-of 评分重建 10 维特征。"""
    elo_diff = eh - ea + HOME_ADVANTAGE
    he = expected_score(eh, ea, HOME_ADVANTAGE)
    ae = 1.0 - he
    ratio = eh / ea if ea > 0 else 1.0
    dn = elo_diff / 200.0
    dp = float(min(1.0, max(0.0, math.exp(-0.5 * dn * dn))))
    conf = (eh + ea) / 3000.0
    return {
        'home_elo': round(float(eh), 2), 'away_elo': round(float(ea), 2),
        'elo_diff': round(float(elo_diff), 2), 'elo_ratio': round(float(ratio), 4),
        'elo_home_expected': round(float(he), 4), 'elo_away_expected': round(float(ae), 4),
        'elo_draw_prob': round(dp, 4),
        'home_elo_momentum': round(float(mh), 2), 'away_elo_momentum': round(float(ma), 2),
        'elo_confidence': round(float(conf), 4),
    }


def elo_wdl_from_asof(eh, mh, ea, ma):
    """生产 CalcEngine.calc_win_draw_lose_elo 同款公式（d 含动量、无主场加成）。"""
    d = eh - ea + mh - ma
    ph = 1.0 / (1.0 + 10 ** (-d / 400.0))
    pa = 1.0 / (1.0 + 10 ** (d / 400.0))
    pdr = max(0.20, 0.30 - abs(d) / 2000.0)
    tot = ph + pdr + pa
    return (ph / tot, pdr / tot, pa / tot)  # (win, draw, lose)


def collect_samples():
    """复用 C-142 框架：报告去重 → join 赛果 → MD 解析。返回样本 list。"""
    recs = {}
    for f in glob.glob(REPORT_GLOB):
        if f.endswith("_summary.md"):
            continue
        m = FN_PAT.match(os.path.basename(f))
        if not m:
            continue
        lg, dt, h, a = m.groups()
        key = (dt, h, a)
        dird = os.path.basename(os.path.dirname(f))
        if key not in recs or dird == dt.replace("-", ""):
            recs[key] = (lg, f)

    conn = sqlite3.connect(DB)
    db = {}
    for d, h, a, sc in conn.execute(
            "SELECT match_date, home_team, away_team, actual_score FROM matches "
            "WHERE actual_score IS NOT NULL AND actual_score!=''"):
        db.setdefault((d, norm(h), norm(a)), sc)
    conn.close()

    samples = []
    for (dt, h, a), (lg, f) in sorted(recs.items()):
        sc = db.get((dt, norm(h), norm(a)))
        if not sc:
            continue
        sm = re.match(r"^\s*(\d+)\s*[:：-]\s*(\d+)", sc)
        if not sm:
            continue
        hg, ag = int(sm.group(1)), int(sm.group(2))
        actual = 0 if hg > ag else (1 if hg == ag else 2)
        sub, mkt = parse_report(f)
        samples.append({"dt": dt, "lg": lg, "h": h, "a": a, "actual": actual,
                        "sub": sub, "mkt": mkt, "file": f})
    return samples, len(recs)


def main():
    probe_n = None
    if "--probe" in sys.argv:
        probe_n = int(sys.argv[sys.argv.index("--probe") + 1])

    print("=" * 70)
    print("C-144 ① as-of 全量重放（诚实版 8 系列三指标）")
    print("=" * 70)

    # 1) 样本框架
    samples, n_recs = collect_samples()
    print(f"[样本] 报告 {n_recs} 份去重 → join 赛果 {len(samples)} 场")

    # 2) as-of Elo 重放底座（审计同款，已验证）
    hist, _results = replay_elo()
    print(f"[Elo] replay_elo 完成: {len(hist)} 队")

    # 3) WDL 模型资产 + 特征矩阵（今日重建；Elo 特征 per-row as-of 天然干净）
    wdl = pc.load_wdl_model()
    scaler, feat_list = wdl['scaler'], wdl['features']
    lgb_model, xgb_model = wdl['lgb_model'], wdl['xgb_model']
    print(f"[模型] scaler {scaler.n_features_in_} 维, 特征列表 {len(feat_list)} 维")

    print("[特征] load_match_data_odds + build_all_features（约 1-3 分钟）...")
    df = load_match_data_odds(dedup=True)
    X_all, _y = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
    print(f"[特征] 矩阵 {X_all.shape}")
    import pandas as pd
    df_dates = pd.to_datetime(df['date'], format='mixed')

    available = [f for f in feat_list if f in X_all.columns]
    if len(available) != scaler.n_features_in_:
        print(f"[警告] 特征维度不匹配 {len(available)}/{scaler.n_features_in_}，XGB/LGB 重放将跳过")

    # 4) 逐场重放
    rows = []
    n_idx_date, n_idx_fallback, n_feat_skip = 0, 0, 0
    import xgboost as _xgb
    platt_cache = {}
    for si, s in enumerate(samples):
        if probe_n and si >= probe_n:
            break
        dt, lg = s["dt"], s["lg"]
        hn_tn, an_tn = norm(s["h"]), norm(s["a"])
        eh, mh = asof_lookup(hist, hn_tn, dt)
        ea, ma = asof_lookup(hist, an_tn, dt)

        # Elo 子模型 as-of 重放
        elo_replay = elo_wdl_from_asof(eh, mh, ea, ma)

        # XGB/LGB 重放（双变体：raw=矩阵原值 / swap=强制覆写 10 维 Elo as-of）
        xgb_p = lgb_p = xgb_raw_p = lgb_raw_p = None
        if len(available) == scaler.n_features_in_:
            hn_f, an_f = fu_norm(s["h"]), fu_norm(s["a"])
            mask = ((df['home_team_name'] == hn_f) & (df['away_team_name'] == an_f)
                    & (df_dates == pd.Timestamp(dt)))
            idx = None
            if mask.sum() > 0:
                idx = df[mask].index[-1]
                n_idx_date += 1
            else:
                mask2 = (df['home_team_name'] == hn_f) & (df['away_team_name'] == an_f)
                if mask2.sum() > 0:
                    idx = df[mask2].index[-1]
                    n_idx_fallback += 1
            if idx is not None and idx < len(X_all):
                row = X_all.iloc[idx]
                base_vec = row[available].astype(float).values.copy()
                # swap 变体：覆写 10 维 Elo（裁决口径）
                swap_map = elo10_from_asof(eh, mh, ea, ma)
                swap_vec = base_vec.copy()
                for j, fn in enumerate(available):
                    if fn in swap_map:
                        swap_vec[j] = swap_map[fn]

                def _infer(vec, model, is_xgb):
                    Xs = scaler.transform([vec])
                    if is_xgb:
                        raw = model.predict(_xgb.DMatrix(Xs))[0]
                    else:
                        raw = model.predict(Xs)[0]
                    p = {'win': float(raw[2]), 'draw': float(raw[1]), 'lose': float(raw[0])}
                    # 分联赛 Platt（生产同口径）
                    key = ('xgboost' if is_xgb else 'lightgbm')
                    if key not in platt_cache:
                        platt_cache[key] = pc._load_platt_calibration(key)
                    params = pc._select_platt_params(platt_cache[key], lg)
                    p = pc._apply_platt_to_wdl(p, params)
                    return (p['win'], p['draw'], p['lose'])

                try:
                    if lgb_model is not None:
                        lgb_p = _infer(swap_vec, lgb_model, False)
                        lgb_raw_p = _infer(base_vec, lgb_model, False)
                    if xgb_model is not None:
                        xgb_p = _infer(swap_vec, xgb_model, True)
                        xgb_raw_p = _infer(base_vec, xgb_model, True)
                except Exception as e:
                    print(f"[警告] {dt} {s['h']}vs{s['a']} 推理异常: {e}")
                    n_feat_skip += 1
        else:
            n_feat_skip += 1

        # 组卷主口径：XGB/LGB 复用 MD（重放对拍坐实其对 Elo 泄漏不敏感，
        # swap vs raw ≈1.4-1.6pp），Elo 用 as-of 重放，DC/贝叶斯/市场复用 MD
        md = s["sub"]
        five = {}
        if "Dixon-Coles" in md:
            five['dixonColes'] = md["Dixon-Coles"]
        if md.get("XGBoost"):
            five['xgboost'] = md["XGBoost"]
        if md.get("LightGBM"):
            five['lightgbm'] = md["LightGBM"]
        five['elo'] = elo_replay
        if "贝叶斯" in md:
            five['bayesian'] = md["贝叶斯"]

        probs = {}
        if "Dixon-Coles" in md:
            probs["Dixon-Coles"] = md["Dixon-Coles"]
        if md.get("XGBoost"):
            probs["XGBoost"] = md["XGBoost"]
        if md.get("LightGBM"):
            probs["LightGBM"] = md["LightGBM"]
        probs["Elo"] = elo_replay
        if "贝叶斯" in md:
            probs["贝叶斯"] = md["贝叶斯"]
        if len(probs) >= 2:
            probs["简单平均"] = tuple(sum(x[i] for x in probs.values()) / len(probs) for i in range(3))
        # Stacking：生产同机制（meta 优先，固定权重回退）
        sub_probs = {k: {'win': v[0], 'draw': v[1], 'lose': v[2]} for k, v in five.items()}
        avail_models = [m for m in pc.STACKING_META_MODELS if sub_probs.get(m) is not None]
        if len(avail_models) >= 2:
            meta_p = pc.apply_stacking_meta_learner(sub_probs, extra_features={'dc_odds_fallback': 0.0})
            if meta_p is not None:
                probs["Stacking"] = (meta_p['win'], meta_p['draw'], meta_p['lose'])
            else:
                st = pc.CalcEngine.stack_wdl_probabilities(sub_probs, pc.STACKING_WEIGHTS)
                probs["Stacking"] = (st['win'], st['draw'], st['lose'])
        if s["mkt"]:
            probs["市场去水"] = s["mkt"]

        rows.append({"s": s, "probs": probs,
                     "md_xgb": md.get("XGBoost"), "md_lgb": md.get("LightGBM"),
                     "md_elo": md.get("Elo"), "md_stack": md.get("Stacking"),
                     "xgb_swap": xgb_p, "lgb_swap": lgb_p,
                     "xgb_raw": xgb_raw_p, "lgb_raw": lgb_raw_p})

    print(f"[重放] 完成 {len(rows)} 场（日期索引 {n_idx_date} / 回退索引 {n_idx_fallback} / 跳过 {n_feat_skip}）")

    # 5) 保真对拍：重放 vs MD 原读数（逐场概率差）
    def _diff_stats(pairs):
        ds = [abs(a - b) for p, q in pairs for a, b in zip(p, q)]
        if not ds:
            return "n=0"
        return f"n={len(pairs)} 场均|Δ|={sum(ds)/len(ds)*100:.2f}pp 最大|Δ|={max(ds)*100:.2f}pp"

    print("\n[保真对拍] 重放 vs MD 原读数（三向概率差）")
    print("  XGB  swap vs MD :", _diff_stats([(r["xgb_swap"], r["md_xgb"]) for r in rows if r["xgb_swap"] and r["md_xgb"]]))
    print("  XGB  raw  vs MD :", _diff_stats([(r["xgb_raw"], r["md_xgb"]) for r in rows if r["xgb_raw"] and r["md_xgb"]]))
    print("  XGB  swap vs raw:", _diff_stats([(r["xgb_swap"], r["xgb_raw"]) for r in rows if r["xgb_swap"] and r["xgb_raw"]]))
    print("  LGB  swap vs MD :", _diff_stats([(r["lgb_swap"], r["md_lgb"]) for r in rows if r["lgb_swap"] and r["md_lgb"]]))
    print("  LGB  raw  vs MD :", _diff_stats([(r["lgb_raw"], r["md_lgb"]) for r in rows if r["lgb_raw"] and r["md_lgb"]]))
    print("  LGB  swap vs raw:", _diff_stats([(r["lgb_swap"], r["lgb_raw"]) for r in rows if r["lgb_swap"] and r["lgb_raw"]]))
    print("  Elo  asof vs MD :", _diff_stats([(r["probs"].get("Elo"), r["md_elo"]) for r in rows if r["probs"].get("Elo") and r["md_elo"]]))

    if probe_n:
        print(f"\n[PROBE {probe_n}] 探针模式结束，不写报告。")
        return

    # 6) 聚合三指标（C-142 同款）
    series = MODELS + ["简单平均", "Stacking", "市场去水"]
    stat = {sn: {"n": 0, "hit": 0, "rps": [], "league": defaultdict(lambda: [0, 0]),
                 "bucket": defaultdict(lambda: [0, 0.0, 0]),
                 "pred_dir": [0, 0, 0]} for sn in series}
    actual_dir = [0, 0, 0]
    elo_dir_stat = [[0, 0], [0, 0], [0, 0]]
    mkt_dir_stat = [[0, 0], [0, 0], [0, 0]]
    elo_month = defaultdict(lambda: [0, 0])
    disagree = [0, 0, 0]
    for r in rows:
        s, actual, lg = r["s"], r["s"]["actual"], r["s"]["lg"]
        actual_dir[actual] += 1
        for sname, p in r["probs"].items():
            if sname not in stat or any(v <= 0 for v in p):
                continue
            st = stat[sname]
            st["n"] += 1
            pred = max(range(3), key=lambda i: p[i])
            hit = int(pred == actual)
            st["hit"] += hit
            st["rps"].append(rps(p, actual))
            st["league"][lg][0] += hit
            st["league"][lg][1] += 1
            st["pred_dir"][pred] += 1
            pconf = p[pred]
            b = "0.4-0.5" if 0.4 <= pconf < 0.5 else ("0.5-0.6" if 0.5 <= pconf < 0.6 else ("0.6+" if pconf >= 0.6 else "<0.4"))
            bk = st["bucket"][b]
            bk[0] += 1
            bk[1] += pconf
            bk[2] += hit
        if "Elo" in r["probs"] and "市场去水" in r["probs"]:
            ep = max(range(3), key=lambda i: r["probs"]["Elo"][i])
            mp = max(range(3), key=lambda i: r["probs"]["市场去水"][i])
            elo_dir_stat[ep][1] += 1
            elo_dir_stat[ep][0] += int(ep == actual)
            mkt_dir_stat[mp][1] += 1
            mkt_dir_stat[mp][0] += int(mp == actual)
            em = elo_month[s["dt"][:7]]
            em[1] += 1
            em[0] += int(ep == actual)
            if ep != mp:
                disagree[0] += int(ep == actual)
                disagree[1] += int(mp == actual)
                disagree[2] += int(ep != actual and mp != actual)

    # 7) C-142 污染口径对照（从 MD 原读数重聚合，同一样本）
    stat_md = {sn: {"n": 0, "hit": 0, "rps": []} for sn in series}
    for r in rows:
        actual = r["s"]["actual"]
        md_map = {"Dixon-Coles": r["s"]["sub"].get("Dixon-Coles"),
                  "XGBoost": r["md_xgb"], "LightGBM": r["md_lgb"],
                  "Elo": r["md_elo"], "贝叶斯": r["s"]["sub"].get("贝叶斯"),
                  "Stacking": r["md_stack"], "市场去水": r["s"]["mkt"]}
        md5 = {k: v for k, v in md_map.items() if k in MODELS and v}
        if len(md5) >= 2:
            md_map["简单平均"] = tuple(sum(x[i] for x in md5.values()) / len(md5) for i in range(3))
        for sname, p in md_map.items():
            if sname not in stat_md or p is None or any(v <= 0 for v in p):
                continue
            st = stat_md[sname]
            st["n"] += 1
            pred = max(range(3), key=lambda i: p[i])
            st["hit"] += int(pred == actual)
            st["rps"].append(rps(p, actual))

    # 8) 报告
    L = []
    L.append("# 子模型 WDL 复盘统计 · as-of 诚实版（C-20261018-144 ① 全量重放）\n")
    L.append("> 生成: analyze_submodel_wdl_asof_replay.py | 样本: 报告 {} 份去重 → join 赛果 {} 场（与 C-142 同框架）".format(n_recs, len(rows)))
    L.append("> 口径: **Elo 行**=审计 as-of（严格 date<比赛日）重放；**XGB/LGB**=复用 C-142 MD 读数（重放对拍坐实其对 Elo 泄漏不敏感：swap vs raw 场均|Δ|≈1.4-1.6pp；raw vs MD ≈15pp 为报告时点特征漂移、非 Elo 通道，见 §零）；**DC/贝叶斯**已核实不消费 Elo 特征，复用 MD；**简单平均/Stacking**=「MD 4 系列 + as-of Elo」按生产机制重算（LR meta-learner 优先、固定权重回退）；**市场**=复用 C-142。")
    L.append("> 本报告落盘后，C-142（submodel_wdl_calibration_20261018.md）的污染口径标记关闭，决策引用一律以本版为准。\n")

    L.append("## 零、保真对拍（重放 vs MD 原读数，三向概率差）\n")
    L.append("| 对拍 | 样本 | 场均|Δ| | 最大|Δ| |")
    L.append("|------|-----:|----------:|----------:|")
    fid_rows = [
        ("XGB swap(覆写Elo) vs MD", [(r["xgb_swap"], r["md_xgb"]) for r in rows if r["xgb_swap"] and r["md_xgb"]]),
        ("XGB raw(矩阵原值) vs MD", [(r["xgb_raw"], r["md_xgb"]) for r in rows if r["xgb_raw"] and r["md_xgb"]]),
        ("XGB swap vs raw（Elo 不敏感坐实）", [(r["xgb_swap"], r["xgb_raw"]) for r in rows if r["xgb_swap"] and r["xgb_raw"]]),
        ("LGB swap(覆写Elo) vs MD", [(r["lgb_swap"], r["md_lgb"]) for r in rows if r["lgb_swap"] and r["md_lgb"]]),
        ("LGB raw(矩阵原值) vs MD", [(r["lgb_raw"], r["md_lgb"]) for r in rows if r["lgb_raw"] and r["md_lgb"]]),
        ("LGB swap vs raw（Elo 不敏感坐实）", [(r["lgb_swap"], r["lgb_raw"]) for r in rows if r["lgb_swap"] and r["lgb_raw"]]),
        ("Elo as-of vs MD（污染对拍）", [(r["probs"].get("Elo"), r["md_elo"]) for r in rows if r["probs"].get("Elo") and r["md_elo"]]),
    ]
    for label, pairs in fid_rows:
        ds = [abs(a - b) for p, q in pairs for a, b in zip(p, q)]
        if ds:
            L.append("| {} | {} | {:.2f}pp | {:.2f}pp |".format(label, len(pairs), sum(ds) / len(ds) * 100, max(ds) * 100))
        else:
            L.append("| {} | 0 | — | — |".format(label))
    L.append("")
    L.append("> 解读基准：「swap vs raw」≈0 → XGB/LGB 输出对 10 维 Elo 特征不敏感，Elo 泄漏无法经此通道污染其读数 → C-142 的 XGB/LGB 读数按干净复用进主表；「raw vs MD」≠0 → 今日重建矩阵与报告时点特征输入存在系统性漂移（赔率快照时点/模型版本/时序特征累积），与 Elo 泄漏无关，但佐证子模型表 live 重算必然漂移、C-144 ②冻结守卫必要；「Elo as-of vs MD」显著 ≠0 → C-143 泄漏污染的定量痕迹。\n")

    L.append("## 一、总览（方向命中率 / 平均 RPS）· as-of 诚实版 vs C-142 污染口径\n")
    L.append("| 系列 | n | as-of 命中 | as-of RPS | C-142 命中 | C-142 RPS | 命中差 |")
    L.append("|------|--:|:---------:|:---------:|:----------:|:---------:|:------:|")
    for sn in series:
        st, stm = stat[sn], stat_md[sn]
        if st["n"]:
            a_hit, a_rps = st["hit"] / st["n"], sum(st["rps"]) / len(st["rps"])
            if stm["n"]:
                m_hit, m_rps = stm["hit"] / stm["n"], sum(stm["rps"]) / len(stm["rps"])
                L.append("| {} | {} | {:.1%} | {:.4f} | {:.1%} | {:.4f} | {:+.1%} |".format(sn, st["n"], a_hit, a_rps, m_hit, m_rps, a_hit - m_hit))
            else:
                L.append("| {} | {} | {:.1%} | {:.4f} | — | — | — |".format(sn, st["n"], a_hit, a_rps))

    L.append("\n## 二、分联赛方向命中率（as-of 诚实版）\n")
    leagues = sorted({lg for sn in series for lg in stat[sn]["league"]})
    L.append("| 系列 | " + " | ".join(leagues) + " |")
    L.append("|------" + "|------:" * len(leagues) + "|")
    for sn in series:
        row = []
        for lg in leagues:
            hh, nn = stat[sn]["league"].get(lg, [0, 0])
            row.append("{:.1%} ({})".format(hh / nn, nn) if nn else "—")
        L.append("| {} | ".format(sn) + " | ".join(row) + " |")

    L.append("\n## 二点五、方向分布对照（argmax 主/平/客 占比 vs 实际赛果分布）\n")
    tot = sum(actual_dir)
    L.append("| 系列 | argmax 主胜 | argmax 平 | argmax 客胜 |")
    L.append("|------|:----------:|:--------:|:----------:|")
    L.append("| **实际赛果** | {:.1%} | {:.1%} | {:.1%} |".format(actual_dir[0] / tot, actual_dir[1] / tot, actual_dir[2] / tot))
    for sn in series:
        st = stat[sn]
        if st["n"]:
            pdd = st["pred_dir"]
            L.append("| {} | {:.1%} | {:.1%} | {:.1%} |".format(sn, pdd[0] / st["n"], pdd[1] / st["n"], pdd[2] / st["n"]))

    L.append("\n## 三、分桶校准（argmax 置信桶：预测均值 vs 实际命中）· as-of 诚实版\n")
    for sn in series:
        st = stat[sn]
        if not st["n"]:
            continue
        L.append("### {}\n".format(sn))
        L.append("| 桶 | n | 预测均值 | 实际命中 | 差距(实-预) |")
        L.append("|----|--:|:-------:|:-------:|:----------:|")
        for b in ["<0.4", "0.4-0.5", "0.5-0.6", "0.6+"]:
            nn, sp, hh = st["bucket"].get(b, [0, 0.0, 0])
            if nn:
                L.append("| {} | {} | {:.1%} | {:.1%} | {:+.1%} |".format(b, nn, sp / nn, hh / nn, hh / nn - sp / nn))
        L.append("")

    L.append("\n## 四、Elo(as-of) × 市场 证伪检查专项\n")
    L.append("- 分方向命中：Elo 主胜 picks {}/{}（{:.1%}）、客胜 picks {}/{}（{:.1%}）；"
             "市场 主胜 picks {}/{}（{:.1%}）、客胜 picks {}/{}（{:.1%}）".format(
                 elo_dir_stat[0][0], elo_dir_stat[0][1],
                 elo_dir_stat[0][0] / max(1, elo_dir_stat[0][1]),
                 elo_dir_stat[2][0], elo_dir_stat[2][1],
                 elo_dir_stat[2][0] / max(1, elo_dir_stat[2][1]),
                 mkt_dir_stat[0][0], mkt_dir_stat[0][1],
                 mkt_dir_stat[0][0] / max(1, mkt_dir_stat[0][1]),
                 mkt_dir_stat[2][0], mkt_dir_stat[2][1],
                 mkt_dir_stat[2][0] / max(1, mkt_dir_stat[2][1])))
    L.append("- 分月命中（Elo as-of）：" + "、".join(
        "{} {:.0%}({})".format(k, v[0] / v[1], v[1]) for k, v in sorted(elo_month.items())))
    L.append("- Elo 与市场方向不一致 {} 场：Elo 对 {}、市场对 {}、皆错 {}".format(sum(disagree), disagree[0], disagree[1], disagree[2]))

    L.append("\n## 五、解读（人工层，重跑脚本会被覆盖——以 change_log C-20261018-144 为准）\n")
    L.append("（待人工填写：as-of 诚实版三指标落盘后，对照 C-142 污染口径逐项确认哪些读数幸存、哪些修订；DC −23pp 是否坐实、XGB/LGB 欠自信是否成立、Stacking<简单平均 是否反转。）")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fo:
        fo.write("\n".join(L))
    print("\n== as-of 诚实版 三指标 ==")
    for sn in series:
        st = stat[sn]
        if st["n"]:
            print("{}: n={} hit={:.1%} rps={:.4f}".format(sn, st["n"], st["hit"] / st["n"], sum(st["rps"]) / len(st["rps"])))
    print("OUT:", OUT)


if __name__ == "__main__":
    main()
