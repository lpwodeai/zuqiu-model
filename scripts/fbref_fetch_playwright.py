"""
fbref Playwright 完整页面获取器
================================
使用 Playwright 非headless 模式绕过 Cloudflare，获取完整 HTML 并转为 Markdown。
非headless 模式通过 Cloudflare 的概率远高于 headless 模式。

用法:
  python scripts/fbref_fetch_playwright.py <match_url> <output_md_path>
"""

import asyncio
import sys
from pathlib import Path


async def fetch_with_playwright(url: str, output_md: Path, save_html: bool = False):
    """用 Playwright 非headless 模式获取页面"""
    from playwright.async_api import async_playwright
    import html2text

    print(f"🌐 正在获取 (Playwright 非headless): {url}")

    async with async_playwright() as p:
        # 启动非headless Chrome
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )

        # 隐藏 webdriver 标记
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)

        page = await context.new_page()

        # 导航到页面
        print("  📄 导航到页面...")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 等待 Cloudflare 验证完成
        # Cloudflare 验证页标题: "Just a moment..." (英文) 或 "请稍候…" (中文)
        print("  ⏳ 等待 Cloudflare 验证...")

        cloudflare_titles = ["just a moment", "请稍候", "attention required"]
        for attempt in range(45):  # 最多等 90 秒
            await asyncio.sleep(2)
            title = (await page.title()).lower()

            # 检查是否还在 Cloudflare 验证页
            is_cloudflare = any(cf in title for cf in cloudflare_titles)

            if not is_cloudflare:
                # 额外检查是否有真实内容（h1 或 stats 表）
                has_h1 = await page.query_selector("h1")
                has_stats = await page.query_selector('table[id^="stats_"]')
                if has_h1 or has_stats:
                    print(f"  ✅ 页面加载完成 (等待 {attempt * 2 + 2} 秒)")
                    break
            if attempt % 5 == 4:
                current_title = await page.title()
                print(f"  ⏳ 仍在等待... ({attempt * 2 + 2} 秒, 标题: {current_title})")
        else:
            print("  ⚠️ 等待超时，尝试获取当前内容...")

        # 额外等待页面完全加载
        await asyncio.sleep(3)

        # 获取完整 HTML
        html = await page.content()
        print(f"  📦 HTML 长度: {len(html)} 字符")

        # 保存 HTML（可选）
        if save_html:
            html_file = output_md.with_suffix(".html")
            html_file.write_text(html, encoding="utf-8")
            print(f"  💾 HTML 已保存: {html_file}")

        # 关闭浏览器
        await browser.close()

    # 检查是否获取到真实内容
    if len(html) < 10000 or "正在进行安全验证" in html:
        print("  ❌ 未能获取真实页面内容")
        return False

    # 转换为 Markdown
    print("  📝 转换 HTML → Markdown...")
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    h.ignore_emphasis = False
    h.inline_links = True
    h.protect_links = True
    h.wrap_links = False

    markdown = h.handle(html)
    print(f"  ✅ Markdown: {len(markdown)} 字符, {markdown.count(chr(10))} 行")

    # 保存 Markdown
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(markdown, encoding="utf-8")
    print(f"\n💾 Markdown 已保存: {output_md}")
    print(f"   文件大小: {output_md.stat().st_size} bytes")

    # 内容检查
    print(f"\n📊 内容检查:")
    print(f"   'Player Stats' 出现: {markdown.count('Player Stats')} 次")
    print(f"   'Fulham' 出现: {markdown.count('Fulham')} 次")
    print(f"   球员链接数: {markdown.count('https://fbref.com/en/players/')}")

    fulham_idx = markdown.find("Fulham Player Stats")
    if fulham_idx > 0:
        print(f"   ✅ Fulham 统计表存在 (位置: {fulham_idx})")
    else:
        print(f"   ⚠️ Fulham 统计表未找到")

    return True


def main():
    if len(sys.argv) < 3:
        print("用法: python fbref_fetch_playwright.py <match_url> <output_md_path> [--save-html]")
        sys.exit(1)

    url = sys.argv[1]
    output_md = Path(sys.argv[2])
    save_html = "--save-html" in sys.argv

    success = asyncio.run(fetch_with_playwright(url, output_md, save_html))
    if not success:
        print("❌ 获取失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
