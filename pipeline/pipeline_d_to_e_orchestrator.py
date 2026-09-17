#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pipeline D→E 无人值守编排器
================================
监控 25/26 采集完成 → 自动依次执行：
  D1: sofascore_backfill_fields.py      (字段回填)
  D2: sofascore_quality_audit.py         (全量审计报告)
  D3: sofascore_pre_match_features.py    (球员级→球队级特征聚合)
  E1: optuna_tuning.py                   (Optuna超参调优)
  E2: train_models_v2.py                 (模型重训)

设计要点：
  - 只读模式连 DB 查进度（file:...?mode=ro），绝不干扰采集进程写库
  - 完成判断：progress 文件停滞 ≥90s + 二次确认（30s 后 DB 场次不增）→ 采集确实结束
  - 严格串行依赖：D1→D2(独立)→D3(依赖D1)→E(依赖D3)
  - 每步独立超时 + 独立日志文件 + 失败不崩溃
  - 最终生成汇总报告
"""
import subprocess
import sys
import time
import sqlite3
import json
from pathlib import Path
from datetime import datetime

# ============================================================
# 路径配置
# ============================================================
ROOT = Path(__file__).resolve().parent.parent  # 项目根目录 (五大联赛专属模型/五大联赛专属模型)
DB = ROOT / "data" / "odds.db"
PROGRESS_FILE = ROOT / "logs" / "sofascore_progress_25_26.json"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
PIPELINE_LOG = LOG_DIR / f"pipeline_d_to_e_{STAMP}.log"
SUMMARY_REPORT = LOG_DIR / f"pipeline_summary_{STAMP}.md"

# 脚本路径
SCRIPTS = {
    "D1_backfill":  ROOT / "import_data" / "sofascore_backfill_fields.py",
    "D2_audit":     ROOT / "analysis" / "sofascore_quality_audit.py",
    "D3_features":  ROOT / "features" / "sofascore_pre_match_features.py",
    "E1_optuna":    ROOT / "scripts" / "optuna_tuning.py",
    "E2_train":     ROOT / "scripts" / "train_models.py",
}

# 完成判断参数
EXPECTED_TOTAL = 1866
MIN_COLLECTED  = 500        # 至少要有500场才认为是"真正跑过"
PROGRESS_STALL = 90         # progress文件停滞90s
CONFIRM_WAIT   = 30         # 二次确认等待30s
MAX_WAIT_MIN   = 120        # 最多等120分钟

# 各步骤超时(秒)
TIMEOUTS = {
    "D1_backfill":  600,    # 10min
    "D2_audit":     900,    # 15min (C-20260819-012: 从 300s 调高, 5285 场数据 >5min)
    "D3_features":  900,    # 15min
    "E1_optuna":    1800,   # 30min（Optuna可能很慢）
    "E2_train":     1800,   # 30min
}

# ============================================================
# 重训准入检查参数 (C-20260819-013)
# ------------------------------------------------------------
# 赛季初样本不足时自动跳过 E1/E2，避免少量噪声样本带偏模型。
# 三层判断，任一不满足即跳过重训：
#   1. 总样本量     — 历史全量场次 ≥ MIN_TRAIN_TOTAL
#   2. 近窗样本量   — 最近 N 天内场次 ≥ MIN_RECENT_MATCHES (避免长时间未采)
#   3. 联赛覆盖率   — 至少 MIN_LEAGUES_COVERED 个联赛各自 ≥ MIN_PER_LEAGUE 场
#                     (避免单联赛数据失衡)
# 跳过时仅跑 D1/D2/D3 (数据准备)，E1/E2 留到下次样本充足时再跑。
# ============================================================
MIN_TRAIN_TOTAL    = 3000   # 全量至少 3000 场才重训（赛季初通常 <500）
MIN_RECENT_DAYS    = 30     # 近 30 天窗口
MIN_RECENT_MATCHES = 50     # 近 30 天至少 50 场（约 1-2 轮五大联赛）
MIN_LEAGUES_COVERED = 4     # 至少覆盖 4 个联赛
MIN_PER_LEAGUE     = 200    # 每个被覆盖联赛至少 200 场
VALIDATION_OVERRIDE_FILE = ROOT / "data" / "training_override.json"  # C-016: 临时文件回退


def log(msg: str):
    """写日志（控制台 + 文件）"""
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(PIPELINE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ro_query(sql: str) -> list:
    """只读模式查DB，绝不干扰采集进程"""
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log(f"  [DB只读查询异常] {e}")
        return []


def get_db_progress() -> tuple:
    """返回 (总场次, {联赛: 场次})"""
    rows = ro_query("""
        SELECT league, COUNT(1) FROM fbref_match_mapping
        WHERE fbref_match_url LIKE '%sofascore%' GROUP BY league
    """)
    total = sum(r[1] for r in rows)
    return total, dict(rows)


def get_progress_mtime() -> float:
    if not PROGRESS_FILE.exists():
        return 0.0
    return PROGRESS_FILE.stat().st_mtime


# ============================================================
# 重训准入检查 (C-20260819-013)
# ============================================================
def check_train_eligibility() -> tuple:
    """检查样本是否足够重训。返回 (eligible: bool, reason: str, stats: dict)

    三层判断：
      1. 全量样本 ≥ MIN_TRAIN_TOTAL
      2. 近 MIN_RECENT_DAYS 天场次 ≥ MIN_RECENT_MATCHES
      3. 至少 MIN_LEAGUES_COVERED 个联赛各自 ≥ MIN_PER_LEAGUE 场

    C-016: 增加 VALIDATION_OVERRIDE_FILE 回退机制。
    当 training_override.json 存在且声明 recent_matches > 0 时，
    跳过第 2 层（近窗检查），直接使用声明的数量。
    文件格式: { "recent_matches": 4, "reason": "西甲第1轮 4 场已回填" }

    任一不满足即跳过 E1/E2，仅保留 D1/D2/D3 数据准备。
    """
    stats = {}
    override_reason = None
    override_recent = None

    # C-016: 检查 override 文件
    if VALIDATION_OVERRIDE_FILE.exists():
        try:
            import json as _json
            override = _json.loads(VALIDATION_OVERRIDE_FILE.read_text(encoding='utf-8'))
            if override.get("recent_matches", 0) > 0:
                override_recent = override["recent_matches"]
                override_reason = override.get("reason", "override file")
                log(f"  [C-016] 检测到 training_override.json: recent_matches={override_recent}, reason={override_reason}")
        except Exception as e:
            log(f"  [C-016] training_override.json 解析失败: {e}")

    # 1. 全量 + 联赛分布
    rows = ro_query("""
        SELECT league, COUNT(1) FROM fbref_match_mapping
        WHERE fbref_match_url LIKE '%sofascore%' GROUP BY league
    """)
    league_dist = dict(rows)
    total = sum(league_dist.values())
    stats["total"] = total
    stats["league_dist"] = league_dist

    # C-016: 也查询不含 fbref_match_url 过滤的全量 (回填的新记录无 fbref_match_url)
    rows_all = ro_query("SELECT league, COUNT(1) FROM fbref_match_mapping GROUP BY league")
    league_dist_all = dict(rows_all)
    total_all = sum(league_dist_all.values())
    stats["total_all"] = total_all
    stats["league_dist_all"] = league_dist_all
    log(f"  fbref_match_mapping: 过滤 sofascore={total} 条, 全量={total_all} 条")

    if total_all < MIN_TRAIN_TOTAL:
        return (False,
                f"全量样本不足: {total_all} < {MIN_TRAIN_TOTAL} (赛季初阈值)",
                stats)

    # 2. 近窗样本量
    recent_rows = ro_query(f"""
        SELECT COUNT(1) FROM matches
        WHERE match_date >= date('now', '-{MIN_RECENT_DAYS} days')
    """)
    recent_cnt = recent_rows[0][0] if recent_rows else 0

    # C-016: 如果 override 文件有声明，将 recent_cnt 补充/覆盖为 override 值
    #   场景1: DB 查询为 0 — 完全用 override
    #   场景2: DB 查询 < MIN_RECENT_MATCHES — 用 override 补充
    if override_recent is not None and recent_cnt < MIN_RECENT_MATCHES:
        log(f"  [C-016] DB recent_cnt={recent_cnt} < {MIN_RECENT_MATCHES}，使用 override recent_matches={override_recent} ({override_reason})")
        recent_cnt = max(recent_cnt, override_recent)

    stats["recent_days"] = MIN_RECENT_DAYS
    stats["recent_cnt"] = recent_cnt
    if override_reason:
        stats["recent_override"] = override_reason

    if recent_cnt < MIN_RECENT_MATCHES:
        return (False,
                f"近 {MIN_RECENT_DAYS} 天样本不足: {recent_cnt} < {MIN_RECENT_MATCHES}"
                f" (避免长时间未采导致模型过时)",
                stats)

    # 3. 联赛覆盖率 (用全量分布，不过滤 fbref_match_url)
    covered = [lg for lg, c in league_dist_all.items() if c >= MIN_PER_LEAGUE]
    stats["covered_leagues"] = covered
    stats["covered_count"] = len(covered)

    if len(covered) < MIN_LEAGUES_COVERED:
        uncovered = {lg: c for lg, c in league_dist_all.items() if c < MIN_PER_LEAGUE}
        return (False,
                f"联赛覆盖不足: 仅 {len(covered)} 个联赛 ≥ {MIN_PER_LEAGUE} 场"
                f" (要求 {MIN_LEAGUES_COVERED} 个)，未达标: {uncovered}",
                stats)

    return (True, "样本充足，可重训", stats)


# ============================================================
# 阶段 0: 等待采集完成
# ============================================================
def wait_for_collection_complete() -> bool:
    log("=" * 64)
    log("🚀 Pipeline D→E 无人值守编排器启动")
    log(f"   监控 DB      : {DB}")
    log(f"   progress 文件 : {PROGRESS_FILE}")
    log(f"   完成判断      : progress停滞≥{PROGRESS_STALL}s + DB≥{MIN_COLLECTED}场 + 二次确认")
    log(f"   最大等待      : {MAX_WAIT_MIN} 分钟")
    log(f"   主日志        : {PIPELINE_LOG}")
    log("=" * 64)

    start = time.time()
    last_report = 0
    last_total = 0

    while True:
        elapsed = time.time() - start
        if elapsed > MAX_WAIT_MIN * 60:
            total, _ = get_db_progress()
            log(f"⏰ 等待超时 {MAX_WAIT_MIN}分钟，当前 DB={total}场，强制进入D→E")
            return total >= MIN_COLLECTED

        total, detail = get_db_progress()
        cur_mtime = get_progress_mtime()
        stalled = (time.time() - cur_mtime) if cur_mtime > 0 else 999

        # 每60秒报告一次进度
        if int(elapsed) - last_report >= 60:
            delta = total - last_total
            log(f"📊 等待 {int(elapsed/60)}min | DB={total}/{EXPECTED_TOTAL} "
                f"({total/EXPECTED_TOTAL*100:.1f}%) | Δ={delta}/min | progress停滞={int(stalled)}s")
            last_report = int(elapsed)
            last_total = total

        # 完成条件: progress停滞 + 有足够数据
        if stalled > PROGRESS_STALL and total >= MIN_COLLECTED:
            log(f"🔍 progress停滞 {int(stalled)}s + DB={total}场 ≥ {MIN_COLLECTED}，开始二次确认...")
            time.sleep(CONFIRM_WAIT)
            total2, _ = get_db_progress()
            if total2 == total:
                log(f"✅ 二次确认通过: DB={total2}场未增长，采集进程已退出")
                log(f"   各联赛: {detail}")
                return True
            else:
                log(f"🔄 误判: DB {total}→{total2} 仍在增长，继续等待")

        time.sleep(30)


# ============================================================
# 阶段 D1~E2: 串联执行
# ============================================================
def _kill_process_tree(pid: int) -> None:
    """Windows 递归终止进程树：taskkill /PID <pid> /T /F
    C-20260819-012: subprocess.run timeout 在 Windows 上对 Optuna 这种 fork 多子进程
    的场景只能杀主进程，子进程继续跑（上轮 E1 卡 60min 根因）。改用 taskkill /T 递归
    终止整棵进程树，/F 强制终止。"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception as e:
        log(f"   ⚠️ taskkill 异常: {e}")


def _stream_reader(stream, buf: list) -> None:
    """独立线程持续读 stdout 管道，防止 PIPE 缓冲区(64KB)写满后子进程
    print() 阻塞导致 communicate() 死锁（C-20260819-012 修订: 上一版
    communicate(timeout) 在 Optuna 高频日志场景下无法超时返回的根因）。"""
    try:
        for chunk in iter(lambda: stream.readline(), ""):
            if chunk:
                buf.append(chunk)
            else:
                break
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def run_step(name: str, cmd: list, cwd: Path, timeout: int) -> dict:
    """执行一个步骤 (C-20260819-012: 独立读管道线程 + 主线程轮询超时 + taskkill 进程树)"""
    log("-" * 64)
    log(f"▶ [{name}] 开始 | cmd: {' '.join(cmd)}")
    log(f"          cwd: {cwd} | timeout: {timeout}s")

    start = time.time()
    proc = None
    output_buf: list = []
    reader_thread = None
    try:
        import threading
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            cwd=str(cwd),
            bufsize=1,
        )
        # 独立线程持续读管道，避免 PIPE 写满死锁
        reader_thread = threading.Thread(
            target=_stream_reader, args=(proc.stdout, output_buf), daemon=True
        )
        reader_thread.start()

        # 主线程轮询：每 1s 检查一次进程是否退出 + 是否超时
        timed_out = False
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            if (time.time() - start) > timeout:
                timed_out = True
                break
            time.sleep(1)

        if timed_out:
            duration = time.time() - start
            log(f"⏰ [{name}] 超时 {timeout}s, 递归终止进程树 (PID={proc.pid})...")
            _kill_process_tree(proc.pid)
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            rc = proc.returncode if proc.returncode is not None else -999
            # 等读线程收尾
            reader_thread.join(timeout=3)
            stdout = "".join(output_buf)
            step_log_partial = LOG_DIR / f"pipeline_{name}_{STAMP}.log"
            step_log_partial.write_text(
                f"CMD: {' '.join(cmd)}\nCWD: {cwd}\nEXIT: {rc} (TIMEOUT)\n"
                f"DURATION: {duration:.1f}s\n\n=== OUTPUT (partial) ===\n{stdout}",
                encoding="utf-8",
            )
            log(f"⏰ [{name}] 已强制终止 | {duration:.1f}s | 日志: {step_log_partial.name}")
            tail_lines = stdout.strip().splitlines()[-15:]
            tail = "\n".join(tail_lines)
            if tail:
                for tl in tail.splitlines():
                    log(f"   │ {tl[:200]}")
            return {"ok": False, "dur": duration, "exit": -999,
                    "log": str(step_log_partial), "tail": tail or "TIMEOUT"}

        # 正常结束
        duration = time.time() - start
        reader_thread.join(timeout=5)
        stdout = "".join(output_buf)

        step_log = LOG_DIR / f"pipeline_{name}_{STAMP}.log"
        step_log.write_text(
            f"CMD: {' '.join(cmd)}\n"
            f"CWD: {cwd}\n"
            f"EXIT: {rc}\n"
            f"DURATION: {duration:.1f}s\n\n"
            f"=== OUTPUT ===\n{stdout}",
            encoding="utf-8",
        )

        success = rc == 0
        output_lines = (stdout or "").strip().splitlines()
        tail = "\n".join(output_lines[-15:])

        status_icon = "✅" if success else "❌"
        log(f"{status_icon} [{name}] {'成功' if success else f'失败 exit={rc}'}"
            f" | {duration:.1f}s | 日志: {step_log.name}")
        if tail:
            for tl in tail.splitlines():
                log(f"   │ {tl[:200]}")

        return {
            "ok": success,
            "dur": duration,
            "exit": rc,
            "log": str(step_log),
            "tail": tail,
        }

    except FileNotFoundError as e:
        duration = time.time() - start
        log(f"📂 [{name}] 脚本不存在: {e} | {duration:.1f}s")
        return {"ok": False, "dur": duration, "exit": -404, "log": "", "tail": str(e)}

    except Exception as e:
        duration = time.time() - start
        log(f"💥 [{name}] 异常: {type(e).__name__}: {e} | {duration:.1f}s")
        if proc is not None and proc.poll() is None:
            _kill_process_tree(proc.pid)
        return {"ok": False, "dur": duration, "exit": -1, "log": "", "tail": str(e)}


def run_pipeline():
    results = {}
    py = sys.executable

    # ---- D1: backfill ----
    r = run_step("D1_backfill",
                 [py, str(SCRIPTS["D1_backfill"])],
                 cwd=ROOT, timeout=TIMEOUTS["D1_backfill"])
    results["D1_backfill"] = r

    # ---- D2: audit（即使D1失败也跑，审计当前状态）----
    r = run_step("D2_audit",
                 [py, str(SCRIPTS["D2_audit"])],
                 cwd=ROOT, timeout=TIMEOUTS["D2_audit"])
    results["D2_audit"] = r

    # ---- D3: 特征聚合（依赖D1回填）----
    if results["D1_backfill"]["ok"]:
        r = run_step("D3_features",
                     [py, str(SCRIPTS["D3_features"]), "--n-recent", "5"],
                     cwd=ROOT, timeout=TIMEOUTS["D3_features"])
        results["D3_features"] = r
    else:
        log("⏭️ D1失败 → 跳过D3（特征聚合依赖回填数据）")
        results["D3_features"] = {"ok": False, "dur": 0, "skipped": True}

    # ---- E: 模型训练（依赖D3特征表 + 样本充足）----
    if not results["D3_features"].get("ok"):
        log("⏭️ D3失败/跳过 → 跳过E（模型训练依赖特征表）")
        results["E1_optuna"] = {"ok": False, "dur": 0, "skipped": True, "skip_reason": "D3失败"}
        results["E2_train"]  = {"ok": False, "dur": 0, "skipped": True, "skip_reason": "D3失败"}
        return results

    # C-20260819-013: 重训准入检查 (赛季初样本不足时自动跳过 E1/E2)
    eligible, reason, stats = check_train_eligibility()
    log("-" * 64)
    log("🔍 重训准入检查 (C-20260819-013)")
    log(f"   全量样本: {stats.get('total', 0)} 场 (阈值 {MIN_TRAIN_TOTAL})")
    log(f"   近 {stats.get('recent_days', MIN_RECENT_DAYS)} 天: {stats.get('recent_cnt', 0)} 场 (阈值 {MIN_RECENT_MATCHES})")
    log(f"   联赛覆盖: {stats.get('covered_count', 0)}/{MIN_LEAGUES_COVERED} 个达标"
        f" (每联赛阈值 {MIN_PER_LEAGUE})")
    if not eligible:
        log(f"⏭️ 跳过重训: {reason}")
        log(f"   仅完成 D1/D2/D3 数据准备，E1/E2 留待下次样本充足时执行")
        results["E1_optuna"] = {"ok": False, "dur": 0, "skipped": True,
                               "skip_reason": f"样本不足: {reason}", "eligibility_stats": stats}
        results["E2_train"]  = {"ok": False, "dur": 0, "skipped": True,
                               "skip_reason": f"样本不足: {reason}", "eligibility_stats": stats}
        return results

    log(f"✅ {reason}，进入 E1/E2 重训")
    # E1: Optuna调参
    r = run_step("E1_optuna",
                 [py, str(SCRIPTS["E1_optuna"])],
                 cwd=SCRIPTS["E1_optuna"].parent, timeout=TIMEOUTS["E1_optuna"])
    results["E1_optuna"] = r

    # E2: 模型重训（即使E1失败也尝试，可能用默认参数）
    r = run_step("E2_train",
                 [py, str(SCRIPTS["E2_train"])],
                 cwd=SCRIPTS["E2_train"].parent, timeout=TIMEOUTS["E2_train"])
    results["E2_train"] = r

    return results


# ============================================================
# 汇总报告
# ============================================================
def generate_summary(results: dict, collection_total: int):
    total_dur = sum(r.get("dur", 0) for r in results.values())

    lines = []
    lines.append("# Pipeline D→E 无人值守执行汇总报告")
    lines.append(f"\n**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**采集完成场次**: {collection_total} / {EXPECTED_TOTAL}")
    lines.append(f"**D→E 总耗时**: {total_dur:.0f}s ({total_dur/60:.1f}min)")
    lines.append(f"**主日志**: `{PIPELINE_LOG}`")
    lines.append("\n## 步骤执行结果\n")
    lines.append("| 步骤 | 状态 | 耗时 | 说明 |")
    lines.append("|------|------|------|------|")

    step_desc = {
        "D1_backfill": "字段回填 (duels_total/aerials_total 等)",
        "D2_audit": "全量数据质量审计报告",
        "D3_features": "球员级→球队级46维特征聚合",
        "E1_optuna": "Optuna超参调优",
        "E2_train": "模型重训",
    }
    for step, r in results.items():
        if r.get("skipped"):
            status = "⏭️ 跳过"
            note = r.get("skip_reason", "依赖步骤失败")
            # 若是样本不足跳过，附加准入统计
            if "eligibility_stats" in r:
                s = r["eligibility_stats"]
                note += (f" | 全量={s.get('total',0)}, 近{s.get('recent_days',30)}天={s.get('recent_cnt',0)}"
                         f", 覆盖={s.get('covered_count',0)}/{MIN_LEAGUES_COVERED}联赛")
        elif r["ok"]:
            status = "✅ 成功"
            note = step_desc.get(step, "")
        else:
            status = f"❌ 失败(exit={r.get('exit','?')})"
            note = step_desc.get(step, "") + f" | 日志: {Path(r.get('log','')).name}"
        lines.append(f"| {step} | {status} | {r.get('dur',0):.0f}s | {note} |")

    # DB最终状态
    lines.append("\n## 最终DB状态\n")
    try:
        total, detail = get_db_progress()
        lines.append(f"**fbref_match_mapping (sofascore)**: {total} 场\n")
        lines.append("| 联赛 | 场次 |")
        lines.append("|------|------|")
        for league, cnt in sorted(detail.items(), key=lambda x: -x[1]):
            lines.append(f"| {league} | {cnt} |")

        rows = ro_query("SELECT COUNT(1) FROM match_player_stats WHERE stats_source='sofascore'")
        player_cnt = rows[0][0] if rows else 0
        lines.append(f"\n**match_player_stats (sofascore)**: {player_cnt:,} 行")

        try:
            rows = ro_query("SELECT COUNT(1) FROM sofascore_team_features")
            feat_cnt = rows[0][0] if rows else 0
            lines.append(f"**sofascore_team_features**: {feat_cnt:,} 行")
        except Exception:
            lines.append(f"**sofascore_team_features**: 表不存在或查询失败")
    except Exception as e:
        lines.append(f"DB状态查询异常: {e}")

    lines.append(f"\n---\n*编排器日志目录: `{LOG_DIR}`*")

    report = "\n".join(lines)
    SUMMARY_REPORT.write_text(report, encoding="utf-8")
    log(f"\n📋 汇总报告已保存: {SUMMARY_REPORT}")
    print("\n" + report)


# ============================================================
# 主入口
# ============================================================
def main():
    # 阶段0: 等待采集完成
    collection_ok = wait_for_collection_complete()
    total, _ = get_db_progress()

    if not collection_ok:
        log(f"⚠️ 采集未正常完成(DB={total})，但仍尝试执行D→E...")

    # 确保采集进程真的退出了（再等60秒让它收尾写库）
    log("⏳ 额外等待60秒确保采集进程写库收尾...")
    time.sleep(60)

    # D1→E2
    results = run_pipeline()

    # 汇总
    generate_summary(results, total)

    log("\n🏁 Pipeline D→E 编排器结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
