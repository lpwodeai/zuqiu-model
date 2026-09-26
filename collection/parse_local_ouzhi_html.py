"""解析本地 ouzhi/touzhu HTML 写入 odds.db（绕过 EdgeOne CAPTCHA）
====================================================================
用户用 Edge 浏览器手动访问 500.com ouzhi/touzhu 页面，完成 CAPTCHA 后
另存为 HTML 文件到 data/ouzhi_html/，本脚本批量解析并写入
odds500_ouzhi_summary / odds500_betting 表。

支持两种文件名格式：
  A) {fid}_{ouzhi|touzhu}.html      —— 优先用此格式（fid 直接读）
  B) {中文队名}(2026_2027{联赛})-{类型}-500彩票网.html
      —— 通过队名反查 odds500_match.home_team_cn 找 fid
      —— 类型识别：「百家欧指」→ ouzhi / 「投注分析」→ touzhu

使用：
  python collection/parse_local_ouzhi_html.py            # 解析全部 HTML
  python collection/parse_local_ouzhi_html.py --fid 1428503  # 仅解析单场
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "collection"))
from final_500_collector import parse_ouzhi, parse_touzhu, write_ouzhi, write_betting

DB = BASE / "data" / "odds.db"
HTML_DIR = BASE / "data" / "ouzhi_html"

# 5 场硬编码 fid → 队名映射（用于中文文件名反查）
FID_BY_HOME = {
    "拜仁慕尼黑": 1428503,
    "蒙扎": 1414186,
    "摩纳哥": 1415118,
    "布伦特福德": 1420423,
    "西班牙人": 1428045,
}


def resolve_fid_and_type(filename: str):
    """从文件名解析 (fid, page_type)。
    返回 (fid, page_type, home_cn_hint)；未识别返回 (None, None, None)
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename

    # 格式 A: {fid}_{ouzhi|touzhu}.html
    m = re.match(r'^(\d+)_(ouzhi|touzhu)$', stem)
    if m:
        return int(m.group(1)), m.group(2), None

    # 格式 B: {队名}VS{客队}(2026_2027{联赛})-{类型}-500彩票网
    # 提取类型
    if "百家欧指" in stem:
        page_type = "ouzhi"
    elif "投注分析" in stem:
        page_type = "touzhu"
    else:
        return None, None, None

    # 提取主队名（在 VS 前）
    # 文件名形如 "拜仁慕尼黑VS柏林联合(2026_2027德甲)-百家欧指-500彩票网"
    m2 = re.match(r'^([^VS()]+)VS', stem)
    if m2:
        home_cn = m2.group(1).strip()
        fid = FID_BY_HOME.get(home_cn)
        if fid:
            return fid, page_type, home_cn
    return None, None, None


def read_html(path: Path) -> str:
    """读取 HTML，500.com 默认 GBK 编码"""
    raw = path.read_bytes()
    # 尝试 GBK（500.com 默认）
    for enc in ("gbk", "gb18030", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fid", type=int, default=0, help="只解析指定 fid")
    args = ap.parse_args()

    if not HTML_DIR.exists():
        print(f"❌ 目录不存在: {HTML_DIR}")
        return

    html_files = sorted(HTML_DIR.glob("*.html"))
    if args.fid:
        html_files = [f for f in html_files if resolve_fid_and_type(f.name)[0] == args.fid]
    if not html_files:
        print(f"❌ 未找到 HTML 文件（{HTML_DIR}/*.html）")
        return

    print(f"=== 发现 {len(html_files)} 个 HTML 文件 ===\n")
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    success, fail = 0, 0
    for hf in html_files:
        fid, page_type, home_hint = resolve_fid_and_type(hf.name)
        if not fid:
            print(f"  ⚠️ 文件名无法识别 fid: {hf.name}")
            fail += 1
            continue

        # 查 fid 的 match_id
        cur.execute("SELECT match_id FROM odds500_match WHERE fid=?", (fid,))
        r = cur.fetchone()
        match_id = r["match_id"] if r else None

        html = read_html(hf)
        # 检测反爬关键字
        if "Security Verification" in html or "TencentEOCaptchaWidget" in html:
            print(f"  ❌ [{hf.name}] 仍是 CAPTCHA 验证页，未获取真实数据")
            fail += 1
            continue

        soup = BeautifulSoup(html, "html.parser")
        if page_type == "ouzhi":
            parsed = parse_ouzhi(soup)
            # parse_ouzhi 返回 {"summary": {...}, "companies": [...]}
            summary = parsed.get("summary", {})
            cc = summary.get("company_count")
            if cc and str(cc) != "None":
                write_ouzhi(conn, fid, match_id, parsed)
                print(f"  ✅ [{hf.name}] fid={fid} ouzhi: company_count={cc} "
                      f"avg_init={summary.get('avg_init')} avg_live={summary.get('avg_live')} "
                      f"companies={len(parsed.get('companies', []))}")
                success += 1
            else:
                print(f"  ⚠️ [{hf.name}] fid={fid} ouzhi 解析为空（company_count=None）")
                fail += 1
        elif page_type == "touzhu":
            parsed = parse_touzhu(soup)
            status = parsed.get("_crawl_status", "ok")
            # parse_touzhu 返回 {"home": {"odds":..,"prob":..,...}, "draw":{...}, "away":{...}, "tips":..}
            home = parsed.get("home", {}) or {}
            home_odds = home.get("odds")
            write_betting(conn, fid, match_id, parsed)
            if status == "ok" and home_odds and str(home_odds) != "None":
                print(f"  ✅ [{hf.name}] fid={fid} touzhu: home_odds={home_odds} "
                      f"prob={home.get('prob')} hot={home.get('hot_index')} status={status}")
                success += 1
            else:
                tips = parsed.get("tips", "")
                print(f"  ⚠️ [{hf.name}] fid={fid} touzhu: status={status} "
                      f"home_odds={home_odds} tips={tips[:60]}")
                fail += 1
        else:
            print(f"  ⚠️ [{hf.name}] 未知 page_type: {page_type}")
            fail += 1

    conn.commit()
    conn.close()
    print(f"\n完成: 成功 {success} / 失败 {fail}")


if __name__ == "__main__":
    main()
