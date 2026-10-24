# -*- coding: utf-8 -*-
"""collection/zgzcw_zq_collector.py — zgzcw.com 足球赔率采集器（v1：只落 JSON 原始档，不入库）。

数据源
------
  百家欧赔: https://fenxi.zgzcw.com/{fid}/bjop
  亚盘对比: https://fenxi.zgzcw.com/{fid}/ypdb
  大小对比: https://fenxi.zgzcw.com/{fid}/dxdb
足球 URL 没有篮球的 /lq/ 前缀（篮球是 /lq/{fid}/{page}），实测 2026-10-24。

fid 列表来源（实测结论，不是猜测）
---------------------------------
  只有 https://fenxi.zgzcw.com/ 首页可用：正则 href="/(\\d+)/bjop" 实测拿到
  198 个唯一 fid，且与 ypdb 链接数完全一致（198/198），可作为稳定来源。
  但它只含「当前在售」场次，以下地址实测不可用：
    https://fenxi.zgzcw.com/?date=YYYY-MM-DD  -> 与首页字节完全相同（参数被忽略）
    https://fenxi.zgzcw.com/index?date=...    -> 0 个 fid
    https://fenxi.zgzcw.com/zq/               -> 0 个 fid
    https://www.zgzcw.com/jczq/               -> 302 跳到 404 页
  因此 --date 只用于日志/人工记录，不参与 fid 过滤；历史日期需要另找列表源。

页面结构（实测 fid=4460366）
---------------------------
  table0      筛选条
  table1[0]   表头 <th>（bjop 17 列；ypdb/dxdb 各 15 列）
  table2      数据行，每行一家公司（首行为「平均」，也被计入「共[N]家公司」）
  table3      汇总行（平均值/最大值/最小值/离散度，含「共[ N ]家公司」）
  行内赔率字符串原样保留（↑↓ 后缀不剥离），不做数值转换。

用法
----
  python collection/zgzcw_zq_collector.py --smoke
  python collection/zgzcw_zq_collector.py --fids 4460366,4460367
  python collection/zgzcw_zq_collector.py --limit 20 --delay 1.2
  python collection/zgzcw_zq_collector.py --out data/zgzcw_zq_raw --no-resume
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

try:
    from curl_cffi import requests as cffi
    HAS_CFFI = True
except ImportError:
    import requests
    HAS_CFFI = False

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from league_scope import TOP5_LEAGUES, is_top5  # noqa: E402  联赛范围 SSOT

BASE = "https://fenxi.zgzcw.com"
INDEX_URL = f"{BASE}/"
DEFAULT_OUT = ROOT / "data" / "zgzcw_zq_raw"

PAGES = ("bjop", "ypdb", "dxdb")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REFERER = "https://www.zgzcw.com/"

FID_RE = re.compile(r'href="/(\d+)/bjop"')
TITLE_RE = re.compile(r"^\s*(.+?)VS(.+?)(?:欧赔|亚盘|大小指数|大小球|让球|进球数)数据分析")
COMPANY_RE = re.compile(r"共\[\s*(\d+)\s*\]家公司")

# 汇总/筛选行首列文字，不算公司数据
SKIP_FIRST_CELLS = {"全选 反选 恢复", "全选反选恢复", "全选反选", "恢复",
                    "Excel下载", "高级筛选"}
# 正常赔率页 ~19-26 万字节；小于该阈值基本是限流/拦截页
MIN_PAGE_BYTES = 20000
MAX_RETRIES = 3
# 实测：约 36 次请求后开始返回 7.9KB 限流页（status 仍是 200），
# 2s/4s 的短退避不足以恢复，需 5s/20s/45s 阶梯 + 命中后全局冷却。
RETRY_WAITS = (5, 20, 45)
BLOCK_COOLDOWN = 10.0   # 判定限流后，后续请求额外等待的秒数
BATCH_SIZE = 18         # 每 N 次请求插入一次长冷却
BATCH_SLEEP = 15.0


def make_session() -> Any:
    if HAS_CFFI:
        session = cffi.Session(impersonate="chrome131")
    else:
        session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return session


def decode_html(content: bytes) -> str:
    """优先 UTF-8，失败回退 GB18030（页面实测为 UTF-8，保留兜底）。"""
    for enc in ("utf-8", "gb18030"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", "replace")


class Collector:
    def __init__(self, delay: float = 2.0):
        self.delay = delay
        self._last_req = 0.0
        self._blocked_until = 0.0
        self.req_count = 0
        self.session = make_session()

    def __enter__(self) -> "Collector":
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _throttle(self) -> None:
        now = time.time()
        wait = max(self.delay - (now - self._last_req), self._blocked_until - now)
        if wait > 0:
            time.sleep(wait)

    def _mark_blocked(self, reason: str) -> None:
        self._blocked_until = time.time() + BLOCK_COOLDOWN
        print(f"    判定限流（{reason}），全局冷却 {BLOCK_COOLDOWN:.0f}s", flush=True)

    def get(self, url: str) -> Any:
        """GET 一页，非 200 或过小页面按失败重试（阶梯退避 + 限流冷却）。"""
        last_err = "unknown"
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                r = self.session.get(url, headers={"Referer": REFERER}, timeout=30)
                self._last_req = time.time()
                self.req_count += 1
                if r.status_code == 200 and len(r.content) >= MIN_PAGE_BYTES:
                    if self.req_count % BATCH_SIZE == 0:
                        print(f"    批次冷却 {BATCH_SLEEP:.0f}s"
                              f"（已请求 {self.req_count} 次）", flush=True)
                        time.sleep(BATCH_SLEEP)
                    return r
                last_err = f"status={r.status_code} bytes={len(r.content)}"
                if r.status_code == 200:
                    self._mark_blocked(last_err)
            except Exception as e:  # 网络/超时/TLS
                self._last_req = time.time()
                last_err = f"{type(e).__name__}: {e}"
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)]
                print(f"    retry {attempt + 1}/{MAX_RETRIES - 1} in {wait}s ({last_err})",
                      flush=True)
                time.sleep(wait)
        raise RuntimeError(last_err)


def parse_title(raw_title: str) -> tuple[str, str, str]:
    """'哥伦布VS多伦多欧赔数据分析-足彩网' -> ('哥伦布VS多伦多', '哥伦布', '多伦多')。"""
    m = TITLE_RE.match(raw_title)
    if m:
        home, away = m.group(1).strip(), m.group(2).strip()
        return f"{home}VS{away}", home, away
    return raw_title.strip(), "", ""


def parse_page(html: str) -> dict[str, Any]:
    """抽取表头/数据行/公司数；rows 保留原始字符串（含 ↑↓）。"""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    header: list[str] = []
    if len(tables) > 1:
        trs = tables[1].find_all("tr")
        if trs:
            header = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]

    rows: list[list[str]] = []
    if len(tables) > 2:
        for tr in tables[2].find_all("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if not cells or not cells[0]:
                continue
            if cells[0] in SKIP_FIRST_CELLS:
                continue
            rows.append(cells)

    n_companies: int | None = None
    for t in tables[3:]:
        m = COMPANY_RE.search(t.get_text(" ", strip=True))
        if m:
            n_companies = int(m.group(1))
            break
    if n_companies is None:
        n_companies = len(rows)

    title_tag = soup.find("title")
    return {
        "title": title_tag.get_text(strip=True) if title_tag else "",
        "header": header,
        "rows": rows,
        "n_companies": n_companies,
    }


def fetch_match(col: Collector, fid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """采集单场三页；单页失败只记录 errors，不中断整场。"""
    entry: dict[str, Any] = {
        "fid": int(fid),
        "title": "",
        "home": "",
        "away": "",
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pages": {},
    }
    errors: list[dict[str, Any]] = []

    for name in PAGES:
        url = f"{BASE}/{fid}/{name}"
        try:
            r = col.get(url)
            parsed = parse_page(decode_html(r.content))
            raw_title = parsed.pop("title", "")
            if name == "bjop" and raw_title:
                entry["title"], entry["home"], entry["away"] = parse_title(raw_title)
            entry["pages"][name] = {
                "url": url,
                "status": r.status_code,
                "bytes": len(r.content),
                **parsed,
            }
            print(f"    {name}: 200, {len(r.content)} bytes, "
                  f"{parsed['n_companies']} 公司, {len(parsed['rows'])} 行", flush=True)
        except Exception as e:
            entry["pages"][name] = {"url": url, "error": str(e)}
            errors.append({"stage": name, "url": url, "error": str(e)})
            print(f"    {name}: FAIL {e}", flush=True)

    return entry, errors


def parse_index_rows(col: Collector) -> list[dict[str, str]]:
    """首页按 <tr> 解析出 (联赛, 主队, 客队, fid)，去重保序。

    实测结构（fid=4566343）：
      <td class="td_5">德甲</td> ... /team/10321">奥格斯堡</a> ... /4566343/bjop
    只有带 bjop 链接的行才是在售场次，实测 198 行。
    """
    html = decode_html(col.get(INDEX_URL).content)
    rows: list[dict[str, str]] = []
    # 首页 <tr> 多为未闭合标签，按起始标签切块（闭合方式不可靠）
    for tr in html.split("<tr")[1:]:
        m_league = re.search(r'class="td_5">([^<]+)</td>', tr)
        m_fid = re.search(r'href="/(\d+)/bjop"', tr)
        if not (m_league and m_fid):
            continue
        teams = re.findall(r'/team/\d+">([^<]+)</a>', tr)
        if len(teams) < 2:
            continue
        rows.append({
            "league": m_league.group(1).strip(),
            "home": teams[0].strip(),
            "away": teams[1].strip(),
            "fid": m_fid.group(1),
        })
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for r in rows:
        if r["fid"] in seen:
            continue
        seen.add(r["fid"])
        uniq.append(r)
    return uniq


def get_fids_from_index(col: Collector) -> list[str]:
    """首页正则取 fid（去重保序）。实测 198 个，仅含当前在售场次。"""
    return [r["fid"] for r in parse_index_rows(col)]


def parse_fids_arg(raw: str) -> list[str]:
    fids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise SystemExit(f"--fids 含非法 fid: {part!r}")
        fids.append(part)
    return list(dict.fromkeys(fids))


def run_coverage_check(date: str, out_dir: Path, fids: list[str],
                       fids_mode: bool) -> int:
    """覆盖率自检：竞彩在售五大联赛数 vs 本次范围内已落盘数。

    返回 0 覆盖完整 / 2 存在缺口（当日缺口清单落 logs/zgzcw_coverage_*.json）。
    """
    try:
        from export_sporttery_snapshot_csv import fetch_matches
        expected = [m for m in fetch_matches(date) if is_top5(m.get("league"))]
        collected = sum(1 for f in fids if (out_dir / f"match_{f}.json").exists())
        if fids_mode:
            print(f"[coverage] 期望 {len(expected)} / 落盘 {collected}"
                  f"（--fids 模式不做覆盖率判定）", flush=True)
            return 0
        if collected >= len(expected):
            print(f"[coverage] 期望 {len(expected)} / 落盘 {collected} —— 覆盖完整", flush=True)
            return 0
        names = [f"{m['league']} {m['home']}vs{m['away']}" for m in expected]
        print(f"[coverage][ALERT] 竞彩在售五大联赛 {len(expected)} 场，"
              f"本次落盘 {collected} 场，缺 {len(expected) - collected} 场", flush=True)
        (ROOT / "logs").mkdir(parents=True, exist_ok=True)
        (ROOT / "logs" / f"zgzcw_coverage_{date.replace('-', '')}.json").write_text(
            json.dumps({"date": date, "expected": len(expected),
                        "collected": collected, "in_sale": names},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return 2
    except Exception as exc:
        print(f"[coverage][warn] 自检失败（不影响采集）：{exc}", flush=True)
        return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="zgzcw.com 足球赔率采集器（只落 JSON，不入库）")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                    help="记录日期 YYYY-MM-DD；列表页不支持按日期过滤（实测）")
    ap.add_argument("--leagues", default="",
                    help="联赛范围，逗号分隔；留空=读 SSOT 五大联赛；传 all=全联赛（写入门会拒收范围外）")
    ap.add_argument("--fids", default="", help="逗号分隔的 fid，指定后不再读首页列表")
    ap.add_argument("--limit", type=int, default=0, help="最多采集场次（0=不限）")
    ap.add_argument("--delay", type=float, default=2.0, help="请求间隔秒数（默认 2.0，实测 1.2 会触发限流）")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"输出目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True,
                    help="跳过已存在且 size>0 的 match_{fid}.json（默认开启）")
    ap.add_argument("--smoke", action="store_true",
                    help="只跑 1-3 场并打印解析摘要")
    args = ap.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if not HAS_CFFI:
        print("[warn] curl_cffi 未安装，回退 requests（TLS 指纹可能被拦截）", flush=True)

    with Collector(delay=args.delay) as col:
        league_by_fid: dict[str, str] = {}
        if args.fids:
            fids = parse_fids_arg(args.fids)
            # --fids 模式也读一次首页，仅为拿到 fid→league 映射（写入门需要它）
            league_by_fid = {r["fid"]: r["league"] for r in parse_index_rows(col)}
            print(f"[list] 使用 --fids 指定 {len(fids)} 场", flush=True)
        else:
            rows = parse_index_rows(col)
            league_by_fid = {r["fid"]: r["league"] for r in rows}
            scope = args.leagues.strip()
            if not scope:
                keep = set(TOP5_LEAGUES)          # SSOT，非硬编码
                scope_label = f"SSOT {'/'.join(TOP5_LEAGUES)}"
                sel = [r for r in rows if r["league"] in keep]
            elif scope.lower() == "all":
                scope_label = "all（写入门将拒收范围外场次）"
                sel = rows
            else:
                keep = {x.strip() for x in scope.split(",") if x.strip()}
                scope_label = scope
                sel = [r for r in rows if r["league"] in keep]
            fids = [r["fid"] for r in sel]
            print(f"[list] 首页 {INDEX_URL} 共 {len(rows)} 场；"
                  f"范围 [{scope_label}] 命中 {len(fids)} 场", flush=True)
        print(f"[cfg] date={args.date}（仅记录，列表页不能按日期过滤）out={out_dir}", flush=True)

        if args.smoke:
            cap = 3
            fids = fids[:cap]
            print(f"[smoke] 只跑 {len(fids)} 场", flush=True)
        if args.limit > 0:
            fids = fids[:args.limit]

        todo: list[str] = []
        skipped = 0
        for fid in fids:
            path = out_dir / f"match_{fid}.json"
            if args.resume and path.exists() and path.stat().st_size > 0:
                skipped += 1
                continue
            todo.append(fid)

        print(f"[plan] 共 {len(fids)} 场，跳过已采集 {skipped}，待采集 {len(todo)}", flush=True)
        if not todo:
            print("全部已采集，无需操作", flush=True)
            sys.exit(run_coverage_check(args.date, out_dir, fids, bool(args.fids)))

        ok = fail = rejected = 0
        total_errors: list[dict[str, Any]] = []
        rejections_path = out_dir / "rejections.jsonl"
        t0 = time.time()
        for i, fid in enumerate(todo):
            print(f"[{i + 1}/{len(todo)}] fid={fid}", flush=True)
            entry, errors = fetch_match(col, fid)
            if league_by_fid:
                entry["league"] = league_by_fid.get(fid, "")
            total_errors.extend({"fid": fid, **e} for e in errors)

            n_ok = sum(1 for p in entry["pages"].values() if "rows" in p)
            league = entry.get("league") or league_by_fid.get(fid, "")
            if n_ok == 0:
                fail += 1
                print(f"[{i + 1}/{len(todo)}] fid={fid} 三页全失败，不落盘", flush=True)
            elif not is_top5(league):
                # 写入门拒绝（FK 同族哲学）：护栏建在写入点，不依赖调用者自觉
                rejected += 1
                rec = {"fid": fid, "league": league, "title": entry.get("title"),
                       "reason": "out_of_scope",
                       "at": datetime.now().isoformat(timespec="seconds")}
                with rejections_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                print(f"[{i + 1}/{len(todo)}] fid={fid} 拒收：league={league!r} 不在 SSOT",
                      flush=True)
            else:
                data = dict(entry, errors=errors)
                path = out_dir / f"match_{fid}.json"
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                ok += 1
                print(f"[{i + 1}/{len(todo)}] fid={fid} ok {n_ok}/3 页 -> {path.name}",
                      flush=True)

            if args.smoke:
                print("  --- smoke 摘要 ---", flush=True)
                print(f"  title={entry['title']!r} home={entry['home']!r} "
                      f"away={entry['away']!r}", flush=True)
                for name in PAGES:
                    p = entry["pages"].get(name, {})
                    if "rows" not in p:
                        print(f"  {name}: FAILED ({(p.get('error') or '')[:80]})", flush=True)
                        continue
                    print(f"  {name}: status={p['status']} bytes={p['bytes']} "
                          f"n_companies={p['n_companies']} rows={len(p['rows'])} "
                          f"header={len(p['header'])}", flush=True)
                    print(f"    header[:8]={p['header'][:8]}", flush=True)
                    for row in p["rows"][:2]:
                        print(f"    row={row[:10]}", flush=True)

        elapsed = time.time() - t0
        print(f"\n完成: 待采 {len(todo)} 场, ok={ok}, fail={fail}, rejected={rejected}, "
              f"skipped={skipped}, 耗时 {elapsed:.1f}s", flush=True)
        if total_errors:
            print(f"errors={len(total_errors)} 条（已随场写入 JSON 的 errors 字段）：", flush=True)
            for e in total_errors[:10]:
                print(f"  fid={e['fid']} {e['stage']}: {e['error'][:100]}", flush=True)
        print(f"输出目录: {out_dir}", flush=True)
        if rejected:
            print(f"拒收 {rejected} 场（范围外），明细见 {rejections_path}", flush=True)

        # 覆盖率自检：竞彩在售五大联赛数 vs 本次范围内已落盘数（当场暴露缺口）
        sys.exit(run_coverage_check(args.date, out_dir, fids, bool(args.fids)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断", file=sys.stderr, flush=True)
        sys.exit(130)
