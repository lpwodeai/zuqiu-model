# -*- coding: utf-8 -*-
"""λ 队级滚动监控器（C-20261016-108，评估层，零模型影响）

替代已失效的单场 |λ−xG|>0.5 判据（C-20261016-107 取证：基线噪声 82%/告警 93.4%）。
口径钉死：
- 窗口起点 E3=2026-10-14（C-20261014-083 FRESH_START 同锚），E1/E2 旧代 artifact 不入窗
- 双维度分路由：λ 总量偏差【归 Plan 3 复测，不另开线】/ λ 差放大【本器独值】
- 队级视角取符号：diff_model=λ_本队−λ_对手, diff_xg=xG_本队−xG_对手, bias=diff_model−diff_xg
- 两阶段触发：E3 复盘 n≥10 → 初版方向检查；队级记录 ≥5 场/队 → 正式读数
- 阈值为「暂定（混合代）」仅参考不告警；E3 复盘 n≥50 时用纯 E3 数据重算替换（runbook 见 change_log）
- 挂 20:30 链尾部非阻断：任何异常 exit 0；E3 n<10 时静默积累
预期首份有意义读数 ≈2026-12 中旬（≥5 场/队 ≈5-6 周数据）。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from attribution_engine import get_match_basics, get_understat_xg  # noqa: E402
from team_name_mapping import normalize_team_name  # noqa: E402

DB = SCRIPTS.parent / "data" / "odds.db"
LOG = SCRIPTS.parent / "reports" / "lambda_rolling_monitor_log.md"
E3_START = "2026-10-14"
MIN_MATCHES_DIRECTION = 10   # 初版方向检查触发
MIN_TEAM_RECORDS = 5         # 正式读数触发（场/队）
RECALC_THRESHOLD = 50        # E3 复盘 n≥50 → 纯 E3 重算阈值
# 暂定阈值（E1+E2 混合代，C-20261016-107 取证）——仅参考展示，不做判据
PROVISIONAL = {"p50": 1.21, "p75": 2.28, "p90": 3.18}


def _append_log(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(text)


def main() -> int:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    mids = [r[0] for r in conn.execute(
        "SELECT DISTINCT match_id FROM post_match_review WHERE match_date >= ?", (E3_START,))]

    team_records: dict[str, list] = {}
    n_match = 0
    biases, total_devs = [], []
    for mid in mids:
        lh = conn.execute(
            "SELECT probability FROM model_predictions WHERE match_id=? "
            "AND prediction_type='Lambda_home' ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
        la = conn.execute(
            "SELECT probability FROM model_predictions WHERE match_id=? "
            "AND prediction_type='Lambda_away' ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
        if not lh or not la:
            continue
        basics = get_match_basics(conn, mid)
        if not basics:
            continue
        xg = get_understat_xg(conn, basics)
        if not xg or xg.get("home_xg") is None:
            continue
        lh_, la_ = float(lh[0]), float(la[0])
        xh, xa = float(xg["home_xg"]), float(xg["away_xg"])
        n_match += 1
        home = normalize_team_name(basics.get("home_team_cn") or "") or basics.get("home_team")
        away = normalize_team_name(basics.get("away_team_cn") or "") or basics.get("away_team")
        date = basics.get("match_date", "")
        # 主/客两条队级记录（焦点队视角取符号）
        for team, lam_t, lam_o, xg_t, xg_o in (
            (home, lh_, la_, xh, xa),
            (away, la_, lh_, xa, xh),
        ):
            if not team:
                continue
            total_dev = (lam_t + lam_o) - (xg_t + xg_o)   # 维度1：总量偏差【归 Plan 3 复测】
            bias = (lam_t - lam_o) - (xg_t - xg_o)        # 维度2：λ 差放大【本器独值】
            team_records.setdefault(team, []).append((date, total_dev, bias))
            total_devs.append(total_dev)
            biases.append(bias)

    if n_match < MIN_MATCHES_DIRECTION:
        _append_log(f"\n## {today} — 样本积累中（E3 复盘 n={n_match}<{MIN_MATCHES_DIRECTION}，静默）\n")
        conn.close()
        return 0

    # 初版方向检查
    mean = sum(biases) / len(biases)
    pos = sum(1 for b in biases if b > 0.3)
    neg = sum(1 for b in biases if b < -0.3)
    mean_tot = sum(total_devs) / len(total_devs)
    lines = [
        f"\n## {today} — E3 复盘 n={n_match}（队-场记录 {len(biases)}）",
        f"- 维度2 λ差放大（本器独值）：mean(bias)={mean:+.3f}，放大(>+0.3)={pos} / 不足(<-0.3)={neg} / 死区={len(biases)-pos-neg}",
        f"- 维度1 λ总量偏差【归 Plan 3 复测，不另开线】：mean={mean_tot:+.3f}（E1/E2 基线 ≈−0.3 球）",
        f"- 暂定阈值（混合代，仅参考不判据）：p50={PROVISIONAL['p50']} p75={PROVISIONAL['p75']} p90={PROVISIONAL['p90']}"
        + (f"；⚠️ n≥{RECALC_THRESHOLD} 已达，应用纯 E3 数据重算阈值" if n_match >= RECALC_THRESHOLD else f"；n≥{RECALC_THRESHOLD} 时用纯 E3 重算"),
    ]

    # 正式读数：≥5 场/队
    formal = {t: rs for t, rs in team_records.items() if len(rs) >= MIN_TEAM_RECORDS}
    if formal:
        lines.append(f"- 正式读数（≥{MIN_TEAM_RECORDS} 场/队，{len(formal)} 队）：")
        for t in sorted(formal, key=lambda k: -abs(sum(r[2] for r in formal[k]) / len(formal[k]))):
            rs = formal[t]
            mb = sum(r[2] for r in rs) / len(rs)
            mt = sum(r[1] for r in rs) / len(rs)
            lines.append(f"  - {t}（{len(rs)} 场）：λ差放大 bias={mb:+.3f}；总量偏差={mt:+.3f}【Plan 3】")
    else:
        lines.append(f"- 正式读数：暂无队伍满 {MIN_TEAM_RECORDS} 场（首份读数预期 ≈2026-12 中旬）")

    _append_log("\n".join(lines) + "\n")
    conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 链尾部非阻断：任何异常静默 exit 0
        try:
            _append_log(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M')} — 执行异常（非阻断）：{e}\n")
        except Exception:
            pass
        sys.exit(0)
