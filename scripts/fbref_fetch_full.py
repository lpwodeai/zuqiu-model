"""
fbref 完整页面获取器
====================
使用 cloudscraper 绕过 Cloudflare，获取完整 HTML 并转为 Markdown。
解决 WebFetch 截断问题（WebFetch 限制 ~19KB，完整页面约 50-100KB）。

用法:
  python scripts/fbref_fetch_full.py <match_url> <output_md_path>
  python scripts/fbref_fetch_full.py <match_url> <output_md_path> --save-html
"""

import sys
import time
from pathlib import Path

import cloudscraper
import html2text


def fetch_full_page(url: str, save_html: bool = False, html_path: Path = None) -> str:
    """用 cloudscraper 获取完整 HTML，转为 Markdown

    Args:
        url: fbref 比赛 URL
        save_html: 是否保存原始 HTML
        html_path: HTML 保存路径

    Returns:
        Markdown 格式的页面内容
    """
    print(f"🌐 正在获取: {url}")

    # 创建 cloudscraper 实例（自动处理 Cloudflare JS 挑战）
    scraper = cloudscraper.create_scraper(
        browser={
            "browser": "chrome",
            "platform": "windows",
            "desktop": True,
        },
        delay=10,  # Cloudflare 挑战等待时间
    )

    # 设置 headers
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    # 获取页面
    response = scraper.get(url, headers=headers, timeout=60)
    print(f"  HTTP 状态: {response.status_code}")
    print(f"  内容长度: {len(response.text)} 字符")

    if response.status_code != 200:
        print(f"  ❌ 获取失败: HTTP {response.status_code}")
        # 检查是否被 Cloudflare 拦截
        if "cf-browser-verification" in response.text or "请稍候" in response.text:
            print("  ⚠️ 被 Cloudflare 拦截")
        return ""

    html = response.text

    # 保存原始 HTML（可选）
    if save_html and html_path:
        html_file = html_path.with_suffix(".html")
        html_file.write_text(html, encoding="utf-8")
        print(f"  💾 HTML 已保存: {html_file} ({len(html)} 字符)")

    # 检查是否被拦截
    if "cf-browser-verification" in html or "正在进行安全验证" in html:
        print("  ⚠️ Cloudflare 验证页面，等待重试...")
        time.sleep(10)
        response = scraper.get(url, headers=headers, timeout=60)
        html = response.text
        if "cf-browser-verification" in html or "正在进行安全验证" in html:
            print("  ❌ Cloudflare 验证仍无法通过")
            return ""
        print(f"  ✅ 重试成功，内容长度: {len(html)} 字符")

    # 转换为 Markdown
    print("  📝 转换 HTML → Markdown...")

    h = html2text.HTML2Text()
    # 配置 html2text 产生与 WebFetch 兼容的格式
    h.ignore_links = False  # 保留链接
    h.ignore_images = True   # 忽略图片（减少输出）
    h.body_width = 0         # 不换行
    h.ignore_emphasis = False  # 保留粗体/斜体
    h.skip_internal_links = False
    h.inline_links = True    # 使用 [text](url) 格式
    h.protect_links = True   # 保留完整 URL
    h.wrap_links = False     # 不换行链接
    h.mark_code = True       # 标记代码块

    markdown = h.handle(html)

    print(f"  ✅ Markdown 转换完成: {len(markdown)} 字符, {markdown.count(chr(10))} 行")

    return markdown


def main():
    if len(sys.argv) < 3:
        print("用法: python fbref_fetch_full.py <match_url> <output_md_path> [--save-html]")
        print("示例:")
        print("  python scripts/fbref_fetch_full.py "
              "https://fbref.com/en/matches/cc5b4244/... "
              "data/fbref_md/cc5b4244.md --save-html")
        sys.exit(1)

    url = sys.argv[1]
    output_path = Path(sys.argv[2])
    save_html = "--save-html" in sys.argv

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 获取并转换
    markdown = fetch_full_page(url, save_html=save_html, html_path=output_path)

    if not markdown:
        print("❌ 获取失败")
        sys.exit(1)

    # 保存 Markdown
    output_path.write_text(markdown, encoding="utf-8")
    print(f"\n💾 Markdown 已保存: {output_path}")
    print(f"   文件大小: {output_path.stat().st_size} bytes")

    # 快速检查内容完整性
    print(f"\n📊 内容检查:")
    print(f"   'Player Stats' 出现次数: {markdown.count('Player Stats')}")
    print(f"   'Fulham' 出现次数: {markdown.count('Fulham')}")
    print(f"   'Manchester Utd' 出现次数: {markdown.count('Manchester Utd')}")
    print(f"   球员链接数: {markdown.count('https://fbref.com/en/players/')}")

    # 检查 Fulham 统计表是否存在
    fulham_stats_idx = markdown.find("Fulham Player Stats")
    if fulham_stats_idx > 0:
        print(f"   ✅ Fulham 球员统计表存在 (位置: {fulham_stats_idx})")
    else:
        print(f"   ⚠️ Fulham 球员统计表未找到")


if __name__ == "__main__":
    main()
