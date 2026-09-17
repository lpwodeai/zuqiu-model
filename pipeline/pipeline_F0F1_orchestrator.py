#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
F0 + F1 无人值守编排器
================================
目标:
  F0 = 补齐 25/26 赛季西甲 + 法甲的 110 场缺口
         (西甲缺37场=进度接口漏抓的最后几轮; 法甲缺73场)
  F1 = 追加 24/25 赛季 + 23/24 赛季五大联赛的 SofaScore 全量采集
         (解过拟合: 样本 ~3,933 → ~7,500，过拟合 gap 缓解 30~40%)
  每步 D1 backfill；最后 D3 特征聚合 + 全量审计 + 模型重训(E2)

执行顺序（严格串行）:
  1. [F0]   西甲 25/26 --resume + 法甲 25/26 --resume       （继续抓缺口）
  2. [F1a]  五大联赛 ALL 24/25 全量                           (~1866场)
  3. [F1b]  五大联赛 ALL 23/24 全量                           (~1866场)
  ↓ 每个采集步骤后都跑 D1 backfill（小步小填，避免最后一次性回填大事务）
  ↓ 全部采集完成后:
  4. [D1-final] 最终统一 backfill 一次（兜底防遗漏）
  5. [D2] 全量审计报告（覆盖23/24+24/25+25/26）
  6. [D3] sofascore_team_features 全量重建（三赛季聚合）
  7. [E2] 模型重训（样本量×1.9，缓解过拟合）

全部产物集中写一份最终汇总报告。
"""
import subprocess
import sys
import time
import sqlite3
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent  # 项目根目录 (五大联赛专属模型/五大联赛专属模型)
DB = ROOT / "data" / "odds.db"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SCRIPTS_DIR = ROOT / "scripts"

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
PIPELINE_LOG = LOG_DIR / f"pipeline_F0F1_{STAMP}.log"
SUMMARY_REPORT = LOG_DIR / f"pipeline_F0F1_summary_{STAMP}.md"

SCRIPTS = {
    "collector":    ROOT / "collection" / "final_sofascore_collector.py",
    "D1_backfill":  ROOT / "import_data" / "sofascore_backfill_fields.py",
    "D2_audit":     ROOT / "analysis" / "sofascore_quality_audit.py",
    "D3_features":  ROOT / "features" / "sofascore_pre_match_features.py",
    "E2_train":     SCRIPTS_DIR / "train_models.py",
}

# 每步超时（秒）
TIMEOUTS = {
    "collect_F0":       30 * 60,    # 30min 缺口补采很快
    "collect_24_25":    90 * 60,    # 90min 1866场全量
    "collect_23_24":    90 * 60,
    "D1_backfill":      15 * 60,
    "D2_audit":         20 * 60,
    "D3_features":      25 * 60,
    "E2_train":         40 * 60,
}

# 采集步骤定义（按顺序）
COLLECT_STEPS = [
    # F0: 西甲+法甲 25/26 缺口补采（走 --resume，跳过已采集）
    {"tag": "F0_laLiga_25_26",  "leagues": "西甲", "season": "25/26", "resume": True,  "to": "collect_F0"},
    {"tag": "F0_ligue1_25_26",  "leagues": "法甲", "season": "25/26", "resume": True,  "to": "collect_F0"},
    # F1a: 24/25 全量
    {"tag": "F1a_ALL_24_25",    "leagues": "all",  "season": "24/25", "resume": False, "to": "collect_24_25"},
    # F1b: 23/24 全量
    {"tag": "F1b_ALL_23_24",    "leagues": "all",  "season": "23/24", "resume": False, "to": "collect_23_24"},
]


def log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(PIPELINE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ro_query(sql: str) -> list:
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


def snap_progress(tag: str) -> dict:
    """记录 DB 进度快照"""
    rows = ro_query("""
        SELECT league, season, COUNT(1) FROM fbref_match_mapping
        WHERE fbref_match_url LIKE '%sofascore%'
        GROUP BY league, season
    """)
    rows2 = ro_query("SELECT COUNT(1) FROM match_player_stats WHERE stats_source='sofascore'")
    players = rows2[0][0] if rows2 else 0
    rows3 = ro_query("SELECT COUNT(1) FROM fbref_match_mapping WHERE fbref_match_url LIKE '%sofascore%'")
    total = rows3[0][0] if rows3 else 0
    log(f"  [DB快照 {tag}] 总场次={total:,} | 球员行={players:,}")
    for r in rows:
        log(f"     · {r[0]:<4} {r[1]:<6} {r[2]}场")
    return {"tag": tag, "total": total, "players": players, "detail": rows}


def run_step(name: str, cmd: list, cwd: Path, timeout: int) -> dict:
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
        step_log = LOG_DIR / f"pipeline_{name}_{STAMP}.log"
        step_log.write_text(
            f"CMD: {' '.join(cmd)}\nCWD: {cwd}\nEXIT: {result.returncode}\n"
            f"DURATION: {duration:.1f}s\n\n=== STDOUT ===\n{result.stdout}\n\n"
            f"=== STDERR ===\n{result.stderr}",
            encoding="utf-8",
        )
        success = (result.returncode == 0)
        output_lines = (result.stdout + "\n" + result.stderr).strip().splitlines()
        tail = "\n".join(output_lines[-10:])
        status = "✅" if success else "❌"
        log(f"{status} [{name}] {'成功' if success else f'失败 exit={result.returncode}'}"
            f" | {duration:.0f}s | 日志: {step_log.name}")
        for tl in tail.splitlines():
            log(f"   │ {tl[:200]}")
        return {"ok": success, "dur": duration, "exit": result.returncode,
                "log": str(step_log), "tail": tail}
    except subprocess.TimeoutExpired:
        duration = time.time() - start
        log(f"⏰ [{name}] 超时 {timeout}s | {duration:.0f}s")
        return {"ok": False, "dur": duration, "exit": -999, "log": "", "tail": "TIMEOUT"}
    except FileNotFoundError as e:
        duration = time.time() - start
        log(f"📂 [{name}] 脚本不存在: {e}")
        return {"ok": False, "dur": duration, "exit": -404, "log": "", "tail": str(e)}
    except Exception as e:
        duration = time.time() - start
        log(f"💥 [{name}] 异常: {type(e).__name__}: {e}")
        return {"ok": False, "dur": duration, "exit": -1, "log": "", "tail": str(e)}


def generate_summary(results: dict, snaps: list):
    lines = []
    lines.append("# F0 + F1 多赛季采集管线 最终汇总报告")
    lines.append(f"\n**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**主日志**: `{PIPELINE_LOG}`")
    total_dur = sum(r.get("dur", 0) for r in results.values())
    lines.append(f"**总耗时**: {total_dur:.0f}s ({total_dur/3600:.1f}h)")
    lines.append("\n## 步骤执行结果\n")
    lines.append("| 步骤 | 状态 | 耗时 | 说明 |")
    lines.append("|------|------|------|------|")
    step_desc = {
        "F0_laLiga_25_26": "F0: 西甲25/26缺口补采(--resume)",
        "F0_ligue1_25_26": "F0: 法甲25/26缺口补采(--resume)",
        "F1a_ALL_24_25":   "F1a: 五大联赛 24/25 全量采集",
        "F1b_ALL_23_24":   "F1b: 五大联赛 23/24 全量采集",
        "D1_per_step_after_F0_laLiga":  "D1: F0后小回填",
        "D1_per_step_after_F0_ligue1":  "D1: F0后小回填",
        "D1_per_step_after_F1a":        "D1: 24/25后回填",
        "D1_per_step_after_F1b":        "D1: 23/24后回填",
        "D1_final":         "D1: 最终统一回填(兜底)",
        "D2_audit":         "D2: 三赛季全量审计报告",
        "D3_features":      "D3: 三赛季球队级特征聚合",
        "E2_train":         "E2: 模型重训(缓解过拟合)",
    }
    for step, r in results.items():
        if r.get("skipped"):
            status = "⏭️ 跳过"
            note = "依赖步骤失败"
        elif r["ok"]:
            status = "✅ 成功"
            note = step_desc.get(step, "")
        else:
            status = f"❌ 失败(exit={r.get('exit','?')})"
            note = step_desc.get(step, "") + f" | 日志: {Path(r.get('log','')).name}"
        lines.append(f"| {step} | {status} | {r.get('dur',0):.0f}s | {note} |")

    # DB 进度始末对比
    lines.append("\n## DB 采集进度\n")
    if snaps:
        lines.append("| 阶段 | 总场次 | 球员行数 |")
        lines.append("|------|---:|---:|")
        for s in snaps:
            lines.append(f"| {s['tag']} | {s['total']:,} | {s['players']:,} |")
        lines.append(f"\n**净增场次** = {snaps[-1]['total'] - snaps[0]['total']:,}")
        lines.append(f"**净增球员行** = {snaps[-1]['players'] - snaps[0]['players']:,}")

    # 最终详细分布
    final_detail = ro_query("""
        SELECT league, season, COUNT(1) FROM fbref_match_mapping
        WHERE fbref_match_url LIKE '%sofascore%'
        GROUP BY league, season ORDER BY season, league
    """)
    lines.append("\n### 最终联赛-赛季分布\n")
    lines.append("| 联赛 | 赛季 | 场次 |")
    lines.append("|------|------|---:|")
    for r in final_detail:
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")

    # 特征表+模型资产
    try:
        r1 = ro_query("SELECT COUNT(1) FROM sofascore_team_features")[0][0]
        lines.append(f"\n**sofascore_team_features**: {r1:,} 行")
    except Exception:
        pass
    lines.append(f"\n---\n*日志目录: `{LOG_DIR}`*")
    report = "\n".join(lines)
    SUMMARY_REPORT.write_text(report, encoding="utf-8")
    log(f"\n📋 汇总报告已保存: {SUMMARY_REPORT}")
    print("\n" + report)


def main():
    log("=" * 64)
    log("🚀 F0+F1 编排器启动（西甲/法甲补缺口 + 24/25+23/24 两赛季）")
    log(f"   主日志: {PIPELINE_LOG}")
    py = sys.executable
    results = {}
    snaps = []

    snaps.append(snap_progress("起始状态"))

    # 1-4: 四步采集
    for step in COLLECT_STEPS:
        tag = step["tag"]
        cmd = [py, str(SCRIPTS["collector"]),
               "--leagues", step["leagues"],
               "--season", step["season"]]
        if step["resume"]:
            cmd += ["--resume"]
        r = run_step(tag, cmd, cwd=ROOT, timeout=TIMEOUTS[step["to"]])
        results[tag] = r

        # 采集成功后立即 D1
        if r["ok"]:
            snap_progress(f"{tag}-采集后")
            d1_tag = f"D1_per_step_after_{tag.split('_',1)[0] if tag.startswith('F0') else tag.split('_',1)[0]}"
            # 简化 D1 标签：按大步骤分组
            if tag == "F0_laLiga_25_26":
                d1_tag = "D1_per_step_after_F0_laLiga"
            elif tag == "F0_ligue1_25_26":
                d1_tag = "D1_per_step_after_F0_ligue1"
            elif tag == "F1a_ALL_24_25":
                d1_tag = "D1_per_step_after_F1a"
            elif tag == "F1b_ALL_23_24":
                d1_tag = "D1_per_step_after_F1b"
            r2 = run_step(d1_tag, [py, str(SCRIPTS["D1_backfill"])],
                          cwd=ROOT, timeout=TIMEOUTS["D1_backfill"])
            results[d1_tag] = r2
        else:
            log(f"  ⏭️ 采集失败 → 跳过该步 D1（后续还有 D1_final 兜底）")

    snaps.append(snap_progress("F0+F1 全部采集完成"))

    # 5: D1 最终兜底
    r = run_step("D1_final", [py, str(SCRIPTS["D1_backfill"])],
                 cwd=ROOT, timeout=TIMEOUTS["D1_backfill"])
    results["D1_final"] = r

    # 6: D2 审计
    r = run_step("D2_audit", [py, str(SCRIPTS["D2_audit"])],
                 cwd=ROOT, timeout=TIMEOUTS["D2_audit"])
    results["D2_audit"] = r

    # 7: D3 特征聚合
    r = run_step("D3_features", [py, str(SCRIPTS["D3_features"]), "--n-recent", "5"],
                 cwd=ROOT, timeout=TIMEOUTS["D3_features"])
    results["D3_features"] = r

    # 8: E2 重训
    if results["D3_features"]["ok"]:
        r = run_step("E2_train", [py, str(SCRIPTS["E2_train"])],
                     cwd=SCRIPTS_DIR, timeout=TIMEOUTS["E2_train"])
        results["E2_train"] = r
    else:
        log("⏭️ D3 失败 → 跳过 E2 重训")
        results["E2_train"] = {"ok": False, "dur": 0, "skipped": True}

    snaps.append(snap_progress("D→E 完成"))
    generate_summary(results, snaps)
    log("\n🏁 F0+F1 编排器结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
