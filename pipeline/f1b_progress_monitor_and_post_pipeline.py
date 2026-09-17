"""
F1b (23/24) 监控 + 后处理自动编排器
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
每分钟检查一次 sofascore_progress_23_24.json，输出：
  - 各联赛采集进度（X/目标，百分比）
  - 合计进度（合计/1752，百分比，净新增/分钟）
  - ETA（按最近10分钟平均速率估算）
检测到 F1b 真正结束后（三重判定之一满足），自动串行执行：
  D1 backfill → D2 audit → D3 features → E2 train
每步独立子进程、独立日志、超时保护、DB 前后快照、最终 Markdown 汇总。

用法: python f1b_progress_monitor_and_post_pipeline.py
 （建议后台运行：--run_in_background 或 nohup）
"""
import subprocess
import sys
import time
import sqlite3
import json
import os
import psutil
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录 (五大联赛专属模型/五大联赛专属模型)
DB = ROOT / "data" / "odds.db"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SCRIPTS_DIR = ROOT / "scripts"
DATA_DIR = ROOT / "data"

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_FILE = LOG_DIR / f"f1b_monitor_post_{STAMP}.log"
SUMMARY_REPORT = LOG_DIR / f"f1b_post_pipeline_summary_{STAMP}.md"
PROGRESS_JSON = LOG_DIR / "sofascore_progress_23_24.json"
SUMMARY_GLOB_PATTERN = "sofascore_collector_summary_*.json"

PY = sys.executable

SCRIPTS = {
    "D1_backfill": ROOT / "import_data" / "sofascore_backfill_fields.py",
    "D2_audit":    ROOT / "analysis" / "sofascore_quality_audit.py",
    "D3_features": ROOT / "features" / "sofascore_pre_match_features.py",
    "E2_train":    SCRIPTS_DIR / "train_models.py",
}

TIMEOUTS = {
    "D1_backfill": 15 * 60,
    "D2_audit":    20 * 60,
    "D3_features": 25 * 60,
    "E2_train":    40 * 60,
}

LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]
LEAGUE_TARGETS = {"英超": 380, "西甲": 380, "意甲": 380, "德甲": 306, "法甲": 306}
TOTAL_TARGET = sum(LEAGUE_TARGETS.values())  # 1752
MIN_EXPECTED_TOTAL = 1700  # 留 52 场缓冲应对 Sofascore 虚高

# F1b 关联的 collector 日志（新启动生成的那个）——用于辅助"真正结束"判定
F1B_COLLECTOR_LOG_PATTERN = "sofascore_collector_20260809_073251.log"
F1B_LOG_MARK = "sofascore_collector_20260809_0732"  # 前缀匹配，防文件名微调


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def db_snapshot(tag: str) -> dict:
    """和编排器同款的 DB 卡片快照。"""
    try:
        with sqlite3.connect(str(DB)) as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(DISTINCT CASE WHEN stats_source='sofascore' THEN match_id END) AS sofa_matches,
                    COUNT(DISTINCT p.match_id) AS player_match_count,
                    COUNT(*) AS player_rows
                FROM match_player_stats p
                LEFT JOIN matches m ON m.match_id = p.match_id
            """)
            t_row = cur.fetchone()
            total, sofa_matches, pm_count, player_rows = t_row
            # 按赛季分组（取 season 列）
            cur.execute("""
                SELECT COALESCE(m.season, 'NULL'), stats_source, COUNT(DISTINCT p.match_id) cnt
                FROM match_player_stats p
                LEFT JOIN matches m ON m.match_id = p.match_id
                GROUP BY 1, 2 ORDER BY 1, 2
            """)
            detail = [{"season": r[0], "src": r[1], "matches": r[2]} for r in cur.fetchall()]
    except Exception as e:
        return {"tag": tag, "error": str(e)}
    return {"tag": tag, "total_matches": total or 0, "sofa_matches": sofa_matches or 0,
            "player_rows": player_rows or 0, "detail": detail}


def render_snapshot(snap: dict) -> None:
    if "error" in snap:
        log(f"  📊 [{snap['tag']}] DB snapshot 失败: {snap['error']}")
        return
    log(f"  📊 [{snap['tag']}] 总球员行={snap['player_rows']:>7,}  "
        f"Sofa比赛数={snap['sofa_matches']:>5,}")
    for d in snap.get("detail", [])[:8]:
        log(f"     ├ {d['season'] or 'N/A':<7} src={d['src']:<9} matches={d['matches']:>5,}")


def run_step(name: str, cmd: list, cwd: Path, timeout: int) -> dict:
    """和编排器同款的子步骤执行：独立日志 + 超时 + 尾行。"""
    log("-" * 64)
    log(f"▶ [{name}] 开始 | cmd: {' '.join(cmd)}")
    log(f"          cwd: {cwd} | timeout: {timeout/60:.1f}min")
    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="ignore", timeout=timeout, cwd=str(cwd),
        )
        duration = time.time() - start
        step_log = LOG_DIR / f"f1b_post_{name}_{STAMP}.log"
        with open(step_log, "w", encoding="utf-8") as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\n===== STDERR =====\n")
                f.write(result.stderr)
        success = (result.returncode == 0)
        icon = "✅" if success else "❌"
        log(f"{icon} [{name}] 完成 exit={result.returncode} 用时 {duration:>6.1f}s 日志={step_log.name}")
        tail_lines = [l.strip() for l in result.stdout.splitlines() if l.strip()][-8:]
        if tail_lines:
            log(f"   ── 末尾 8 行 ──")
            for tl in tail_lines:
                log(f"   │ {tl[:200]}")
        return {"ok": success, "dur": duration, "exit": result.returncode,
                "log": str(step_log), "tail": "\n".join(tail_lines)}
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        log(f"⏰ [{name}] 超时 {timeout}s | {duration:.0f}s")
        return {"ok": False, "dur": duration, "exit": -999, "log": "", "tail": "TIMEOUT"}
    except FileNotFoundError as e:
        duration = time.time() - start
        log(f"🚫 [{name}] 文件不存在: {e}")
        return {"ok": False, "dur": duration, "exit": -404, "log": "", "tail": f"FILE_NOT_FOUND: {e}"}


# ─────────────────────────────────────────────
# 进度监控相关
# ─────────────────────────────────────────────
def load_progress() -> dict:
    if not PROGRESS_JSON.exists():
        return {}
    try:
        with open(PROGRESS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def league_count_from_progress(prog: dict, league_name: str) -> int:
    """进度文件结构: {"英超_23/24": [event_id_str, ...], ...}"""
    if not isinstance(prog, dict):
        return 0
    for k, v in prog.items():
        if isinstance(k, str) and k.startswith(f"{league_name}_") and isinstance(v, list):
            return len(v)
    return 0


def f1b_collector_alive() -> bool:
    """检查是否还在跑 F1b 的 final_sofascore_collector.py 进程。"""
    for p in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            cmd = p.info.get("cmdline") or []
            if not cmd:
                continue
            cmd_str = " ".join(cmd).lower()
            if "final_sofascore_collector.py" in cmd_str and "23/24" in cmd_str:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def f1b_summary_generated(script_start_ts: float) -> bool:
    """是否有 sofascore_collector_summary_*.json 是在脚本启动后生成且 season=23/24。"""
    for f in LOG_DIR.glob(SUMMARY_GLOB_PATTERN):
        try:
            if f.stat().st_mtime < script_start_ts:
                continue
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and str(data.get("season", "")) == "23/24":
                return True
        except Exception:
            continue
    return False


def print_progress_bar(percent: float, width: int = 28) -> str:
    filled = int(round(width * percent / 100))
    return "█" * filled + "░" * (width - filled)


def monitor_loop_until_done(script_start_ts: float) -> dict:
    """每分钟快照进度，直到判定 F1b 结束。返回最终进度统计。"""
    log("=" * 72)
    log("🔍 F1b 进度监控启动：每分钟输出一次，目标=1752场，F1b结束后自动接 D1→D2→D3→E")
    log(f"   进度文件: {PROGRESS_JSON.name}")
    log(f"   监控日志: {LOG_FILE.name}")
    log("=" * 72)

    # 保留最近 10 分钟净新增用于 ETA 速率估算
    recent = []  # list of (timestamp, total_count)
    prev_total = 0
    same_count_stall = 0  # 连续 N 分钟进度没动

    tick = 0
    MAX_TICKS_BEFORE_AUTO_STALL_CHECK = 60 * 6  # 6 小时兜底停止监控
    while tick < MAX_TICKS_BEFORE_AUTO_STALL_CHECK:
        tick += 1
        now = datetime.now()

        prog = load_progress()
        per_league = {lg: league_count_from_progress(prog, lg) for lg in LEAGUES}
        total = sum(per_league.values())
        pct = min(100.0, total / TOTAL_TARGET * 100) if TOTAL_TARGET > 0 else 0.0

        net_delta = total - prev_total
        recent.append((time.time(), total))
        # 只保留最近 10 条（10 分钟）
        while len(recent) > 10:
            recent.pop(0)

        # 速率（分钟/场）和 ETA
        eta_str = "–"
        if len(recent) >= 3 and total < TOTAL_TARGET:
            t0, c0 = recent[0]
            t1, c1 = recent[-1]
            mins_passed = (t1 - t0) / 60.0
            rate_per_min = (c1 - c0) / mins_passed if mins_passed > 0.5 else 0
            if rate_per_min > 0.05:
                remain = TOTAL_TARGET - total
                eta_mins = remain / rate_per_min
                eta_dt = now + timedelta(minutes=eta_mins)
                eta_str = f"~{eta_mins:.0f}min (@{rate_per_min:.0f}/min) → {eta_dt.strftime('%H:%M')}"
            else:
                eta_str = f"速率过慢 ({rate_per_min:.1f}/min) — 关注是否卡住"
        elif total >= TOTAL_TARGET:
            eta_str = "🏁 目标达成"

        # 打印表头 + 各联赛进度（每分钟）
        header = f"⏱ Tick-{tick:>3}  {now.strftime('%H:%M:%S')}  合计 {total:>4}/{TOTAL_TARGET}={pct:>5.1f}%  {print_progress_bar(pct)}  净增+{net_delta:>3}/min  ETA {eta_str}"
        log(header)
        for lg in LEAGUES:
            tgt = LEAGUE_TARGETS[lg]
            cnt = per_league[lg]
            lp = min(100.0, cnt / tgt * 100) if tgt > 0 else 0
            log(f"   ├ {lg:<2}: {cnt:>3}/{tgt} = {lp:>5.1f}%  {print_progress_bar(int(lp), 18)}")
        log(f"   └ 剩余 {TOTAL_TARGET - total} 场")

        # 结束判定（三重 OR）
        end_reason = None

        # 1) summary json 新生成（优先级最高，最权威）
        if f1b_summary_generated(script_start_ts):
            end_reason = "检测到 sofascore_collector_summary_*.json（23/24）新生成"
        # 2) 进度连续 3 分钟没变 + 合计达标 >= MIN_EXPECTED_TOTAL
        elif net_delta == 0 and total >= MIN_EXPECTED_TOTAL:
            same_count_stall += 1
            log(f"   ⏳ 进度停滞 +{same_count_stall}/3 分钟（当前合计={total}/{TOTAL_TARGET}）")
            if same_count_stall >= 3:
                end_reason = f"进度连续3分钟无新增 + 已入库 >={MIN_EXPECTED_TOTAL} 场"
        else:
            same_count_stall = 0  # 有进展就重置

        # 3) collector 进程消失 + 合计达标
        if end_reason is None and total >= MIN_EXPECTED_TOTAL:
            if not f1b_collector_alive():
                end_reason = f"F1b 采集进程已退出 + 已入库 >= {MIN_EXPECTED_TOTAL} 场"

        if end_reason is not None:
            log("=" * 72)
            log(f"🏁 F1b 判定结束： {end_reason}")
            log(f"   最终合计: {total}/{TOTAL_TARGET} ({pct:.1f}%)")
            for lg in LEAGUES:
                tgt = LEAGUE_TARGETS[lg]
                cnt = per_league[lg]
                log(f"     ├ {lg:<2}: {cnt:>3}/{tgt}  "
                    f"({cnt/tgt*100:>5.1f}% if tgt else 0%)")
            log("=" * 72)
            return {"total": total, "per_league": per_league, "end_reason": end_reason,
                    "pct": pct}

        prev_total = total
        # 睡眠 60 秒（分 6 段 sleep，每 10 秒响应一次 Ctrl+C）
        for _ in range(6):
            time.sleep(10)

    log("⚠️ 监控已到 6 小时兜底上限，自动跳出进入后处理判断（由 F1b 是否活着决定）")
    prog = load_progress()
    per_league = {lg: league_count_from_progress(prog, lg) for lg in LEAGUES}
    total = sum(per_league.values())
    return {"total": total, "per_league": per_league,
            "end_reason": "监控兜底跳出(6h)", "pct": total / TOTAL_TARGET * 100}


# ─────────────────────────────────────────────
# 后处理编排
# ─────────────────────────────────────────────
def post_pipeline(final_progress: dict) -> dict:
    log("")
    log("🚀 F1b 结束，开始执行后处理流水线 D1 → D2 → D3 → E")
    log("")

    pre = db_snapshot("POST-F1b (D1之前)")
    render_snapshot(pre)

    steps = [
        ("D1_backfill", [PY, str(SCRIPTS["D1_backfill"])],              ROOT,           TIMEOUTS["D1_backfill"]),
        ("D2_audit",    [PY, str(SCRIPTS["D2_audit"])],                 ROOT,           TIMEOUTS["D2_audit"]),
        ("D3_features", [PY, str(SCRIPTS["D3_features"]), "--n-recent", "5"],          ROOT,           TIMEOUTS["D3_features"]),
    ]
    # E 训练单独处理，因为 D3 失败就跳过
    e_step = ("E2_train", [PY, str(SCRIPTS["E2_train"])], SCRIPTS_DIR, TIMEOUTS["E2_train"])

    results = {}
    for name, cmd, cwd, to in steps:
        r = run_step(name, cmd, cwd, to)
        results[name] = r
        if not r["ok"]:
            log(f"   🚨 {name} 失败 —— 仍将继续执行后续步骤（D2/D3 可跑部分数据，E2 待 D3 成功决定）")

    post_mid = db_snapshot("POST-D3 (E之前)")
    render_snapshot(post_mid)

    if results["D3_features"]["ok"]:
        r = run_step(e_step[0], e_step[1], e_step[2], e_step[3])
        results["E2_train"] = r
    else:
        results["E2_train"] = {"ok": False, "dur": 0, "exit": -1, "log": "",
                               "tail": "SKIPPED(D3失败)"}
        log("⏭️  D3 失败 → 跳过 E2 重训")

    post_final = db_snapshot("POST-E (最终)")
    render_snapshot(post_final)

    # 生成总结 Markdown
    lines = []
    lines.append(f"# F1b (23/24) + 后处理 D→E 流水线总结报告  {STAMP}")
    lines.append("")
    lines.append("## 1. F1b 采集完成情况")
    lines.append(f"- 结束判定依据: **{final_progress.get('end_reason','N/A')}**")
    lines.append(f"- 进度文件合计: **{final_progress.get('total',0)} / {TOTAL_TARGET} ({final_progress.get('pct',0):.1f}%)**")
    lines.append(f"- 各联赛分布:")
    for lg in LEAGUES:
        c = final_progress.get("per_league", {}).get(lg, 0)
        lines.append(f"  - {lg}: {c}/{LEAGUE_TARGETS[lg]}")
    lines.append("")
    lines.append("## 2. DB 样本快照")
    for s, lab in [(pre, "采集后/D1前"), (post_mid, "D3之后/E前"), (post_final, "最终(E之后)")]:
        lines.append(f"### {lab}")
        if "error" in s:
            lines.append(f"- 快照失败: {s['error']}")
            continue
        lines.append(f"- 球员行总数: {s.get('player_rows',0):,}")
        lines.append(f"- SofaScore 比赛数: {s.get('sofa_matches',0):,}")
        for d in s.get("detail", []):
            lines.append(f"  - {d['season'] or 'N/A'} / src={d['src']}: matches={d['matches']:,}")
    lines.append("")
    lines.append("## 3. 流水线步骤结果")
    lines.append("| Step | Status | Exit | Duration(s) | Log |")
    lines.append("|------|--------|------|-------------|-----|")
    for name in ["D1_backfill", "D2_audit", "D3_features", "E2_train"]:
        r = results.get(name) or {}
        ok = r.get("ok", False)
        ex = r.get("exit", "—")
        dur = f"{r.get('dur',0):.1f}"
        lf = Path(r.get("log", "")).name or "—"
        lines.append(f"| {name} | {'✅OK' if ok else '❌FAIL'} | {ex} | {dur} | `{lf}` |")
    lines.append("")
    lines.append("## 4. 各步骤尾行")
    for name in ["D1_backfill", "D2_audit", "D3_features", "E2_train"]:
        r = results.get(name) or {}
        lines.append(f"### {name}")
        lines.append("```\n" + (r.get("tail") or "") + "\n```\n")
    lines.append("")
    lines.append(f"— 生成时间: {datetime.now().isoformat(timespec='seconds')}")
    SUMMARY_REPORT.write_text("\n".join(lines), encoding="utf-8")
    log("=" * 72)
    log(f"📄 后处理汇总报告已写入: {SUMMARY_REPORT.name}")
    log("=" * 72)
    return {"results": results, "report": str(SUMMARY_REPORT)}


def main() -> None:
    script_start_ts = time.time()
    log(f"F1b 监控启动 ts={script_start_ts:.0f} @ {datetime.now().isoformat(timespec='seconds')}")

    final_progress = monitor_loop_until_done(script_start_ts)
    post_pipeline(final_progress)

    log("🏁 f1b_progress_monitor_and_post_pipeline.py 全部流程结束。")


if __name__ == "__main__":
    main()
