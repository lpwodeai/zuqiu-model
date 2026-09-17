"""
fbref.com 反 Cloudflare 抓取器
==============================
策略：HTTP 优先（requests.Session + 真实 headers + cf_clearance 复用），
      失败回退 Playwright（非 headless + stealth 补丁 + 预暖首页）。

使用方法：
  fetcher = FbrefFetcher(headless=False)
  html = await fetcher.fetch(url)
"""

import asyncio
import random
import time
from typing import Optional

import requests

# 真实浏览器 headers（镜像 WebFetch 成功的请求模式）
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

# 反爬延迟配置
REQUEST_DELAY = 6.0       # 单场请求间隔（秒）
BATCH_DELAY = 30.0        # 批次间冷却（秒）
BATCH_SIZE = 20           # 每批场次数
DAILY_LIMIT = 200         # 每日最大请求数


def is_cloudflare_blocked(text: str, status_code: int) -> bool:
    """检测是否被 Cloudflare 拦截"""
    if status_code in (403, 503):
        return True
    if not text:
        return True
    # 检查 Cloudflare 验证页特征
    lower_head = text[:2000].lower()
    if '正在进行安全验证' in text:
        return True
    if 'cloudflare' in lower_head and ('challenge' in lower_head or 'browser verification' in lower_head):
        return True
    if 'cf-browser-verification' in lower_head:
        return True
    if 'cf_chl_opt' in text:
        return True
    # fbref 正常页面应含 <title> 或 <h1>
    if '<title>' not in text and '<h1' not in text:
        return True
    return False


class FbrefFetcher:
    """fbref.com 抓取器：HTTP 优先 + Playwright 兜底"""

    def __init__(self, headless: bool = True, force_playwright: bool = False):
        self.headless = headless
        self.force_playwright = force_playwright
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._browser = None
        self._page = None
        self._request_count = 0
        self._cf_clearance = None

    async def fetch(self, url: str) -> Optional[str]:
        """获取 URL 内容，返回 HTML 或 None"""
        if self.force_playwright:
            return await self._fetch_playwright(url)

        # HTTP 优先
        html = self._fetch_http(url)
        if html and not is_cloudflare_blocked(html, 200):
            self._request_count += 1
            return html

        # 回退 Playwright
        print(f"  ⚠️ HTTP 被拦截，回退 Playwright: {url[:80]}")
        html = await self._fetch_playwright(url)
        if html and not is_cloudflare_blocked(html, 200):
            self._request_count += 1
            # 提取 cf_clearance 注入 session
            await self._extract_clearance_from_browser()
            return html

        print(f"  ❌ 两种方式均被拦截: {url[:80]}")
        return None

    def _fetch_http(self, url: str) -> Optional[str]:
        """HTTP 请求"""
        try:
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.text
            print(f"  HTTP {resp.status_code}: {url[:80]}")
            return None
        except Exception as e:
            print(f"  HTTP 异常: {e}")
            return None

    async def _fetch_playwright(self, url: str) -> Optional[str]:
        """Playwright 浏览器抓取（带 stealth 补丁）"""
        try:
            if not self._browser:
                await self._init_stealth_browser()

            # 导航到目标页
            await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # 等待 Cloudflare 验证通过（最多 40 秒，非 headless 给更多时间）
            max_wait = 40 if not self.headless else 20
            await self._wait_for_cloudflare_clear(max_wait=max_wait)

            # 再额外等待页面动态内容加载
            await asyncio.sleep(2)

            html = await self._page.content()

            # 如果仍然被拦截，尝试再等待并刷新一次
            if is_cloudflare_blocked(html, 200):
                print(f"  ⏳ 首次获取仍被拦截，等待 15 秒后重试...")
                await asyncio.sleep(15)
                await self._page.reload(wait_until="domcontentloaded", timeout=60000)
                await self._wait_for_cloudflare_clear(max_wait=30)
                await asyncio.sleep(2)
                html = await self._page.content()

            return html
        except Exception as e:
            print(f"  Playwright 异常: {e}")
            return None

    async def _init_stealth_browser(self):
        """初始化 stealth 浏览器"""
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        context = await self._browser.new_context(
            user_agent=HEADERS['User-Agent'],
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
        )

        # 注入 stealth 补丁
        await context.add_init_script("""
            // 移除 webdriver 标记
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            // 伪造 plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            // 伪造 languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            // 伪造 chrome 对象
            window.chrome = { runtime: {} };
        """)

        self._page = await context.new_page()

        # 预暖：先访问首页让 Cloudflare 验证通过
        print("  🔧 预暖浏览器（访问 fbref 首页）...")
        await self._page.goto("https://fbref.com/", wait_until="domcontentloaded", timeout=60000)
        await self._wait_for_cloudflare_clear()
        print("  ✅ 浏览器预暖完成")

    async def _wait_for_cloudflare_clear(self, max_wait: int = 20):
        """等待 Cloudflare 验证通过

        检测策略：
          1. 页面标题不含 "Just a moment" / "请稍候" / "Cloudflare"
          2. 页面 HTML 含 <title> 且不含 cf-challenge 标记
          3. 页面 URL 不含 challenge 参数
        """
        for i in range(max_wait):
            await asyncio.sleep(1)
            try:
                title = await self._page.title()
                # Cloudflare 验证页标题通常是 "请稍候…" 或 "Just a moment..."
                title_lower = title.lower() if title else ''
                if ('moment' in title_lower or '请稍候' in title or
                    'cloudflare' in title_lower or 'attention' in title_lower):
                    if i % 5 == 0:
                        print(f"  ⏳ 等待 Cloudflare 验证... ({i}s) 标题: {title}")
                    continue

                # 二次验证：检查页面内容是否真有实质内容
                html = await self._page.content()
                if '<title>' in html and 'cf-challenge' not in html and 'cf_chl_opt' not in html:
                    if len(html) > 1000:  # 正常页面通常 > 1KB
                        return
            except Exception:
                continue

        # 超时后继续（可能已通过但标题未变）
        print(f"  ⚠️ Cloudflare 等待超时 ({max_wait}s)，继续尝试获取...")

    async def _extract_clearance_from_browser(self):
        """从浏览器提取 cf_clearance cookie 注入 session"""
        try:
            cookies = await self._page.context.cookies()
            for cookie in cookies:
                if cookie['name'] == 'cf_clearance':
                    self._cf_clearance = cookie['value']
                    self.session.cookies.set('cf_clearance', cookie['value'], domain='.fbref.com')
                    print("  ✅ 已提取 cf_clearance 注入 HTTP session")
                    return
        except Exception as e:
            print(f"  ⚠️ 提取 cf_clearance 失败: {e}")

    def throttle(self):
        """请求节流：6 秒延迟 + 随机抖动 + 批次冷却"""
        delay = REQUEST_DELAY + random.uniform(0, 3)
        time.sleep(delay)

        # 批次冷却
        if self._request_count > 0 and self._request_count % BATCH_SIZE == 0:
            print(f"  ⏸️ 批次冷却 {BATCH_DELAY}s（已完成 {self._request_count} 场）...")
            time.sleep(BATCH_DELAY)

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            try:
                await self._browser.close()
                await self._pw.stop()
            except Exception:
                pass
            self._browser = None
            self._page = None


def build_schedule_url(league_id: int, season: str, league_name_slug: str) -> str:
    """构建赛程页 URL"""
    return f"https://fbref.com/en/comps/{league_id}/{season}/schedule/{season}-{league_name_slug}-Scores-and-Fixtures"


def build_match_url(fbref_match_id: str, slug: str = "") -> str:
    """构建比赛详情页 URL"""
    if slug:
        return f"https://fbref.com/en/matches/{fbref_match_id}/{slug}"
    return f"https://fbref.com/en/matches/{fbref_match_id}"


# ============================================================
# 球队名映射（fbref 英文 → odds.db 中文）
# ============================================================

# fbref 常用英文队名 → 中文标准名
# 补充 feature_utils.py 中 TEAM_NAME_MAP 可能缺失的 fbref 特有缩写
FBREF_TEAM_NAME_MAP = {
    # 英超
    'Manchester Utd': '曼彻斯特联',
    'Newcastle': '纽卡斯尔联',
    'Newcastle United': '纽卡斯尔联',
    'Wolves': '狼队',
    'Wolverhampton Wanderers': '狼队',
    'Wolverhampton': '狼队',
    'Tottenham': '托特纳姆热刺',
    'Tottenham Hotspur': '托特纳姆热刺',
    'West Ham': '西汉姆联',
    'West Ham United': '西汉姆联',
    'Nottingham': '诺丁汉森林',
    'Nottingham Forest': '诺丁汉森林',
    'Brighton': '布赖顿',
    'Brighton and Hove Albion': '布赖顿',
    'Brighton & Hove Albion': '布赖顿',
    'Leicester City': '莱切斯特城',
    'Leicester': '莱切斯特城',
    'Manchester City': '曼彻斯特城',
    'Ipswich Town': '伊普斯维奇',
    'Ipswich': '伊普斯维奇',
    'Crystal Palace': '水晶宫',
    'Arsenal': '阿森纳',
    'Liverpool': '利物浦',
    'Chelsea': '切尔西',
    'Brentford': '布伦特福德',
    'Fulham': '富勒姆',
    'Bournemouth': '伯恩茅斯',
    'Everton': '埃弗顿',
    'Southampton': '南安普敦',
    'Aston Villa': '阿斯顿维拉',
    'Burnley': '伯恩利',
    'Luton Town': '卢顿',
    'Luton': '卢顿',
    'Sheffield Utd': '谢菲尔德联',
    'Sheffield United': '谢菲尔德联',
    'Leeds': '利兹联',
    'Leeds United': '利兹联',
    # 意甲
    'Inter': '国际米兰',
    'Inter Milan': '国际米兰',
    'Internazionale': '国际米兰',
    'Milan': 'AC米兰',
    'AC Milan': 'AC米兰',
    'Juventus': '尤文图斯',
    'Napoli': '那不勒斯',
    'Roma': '罗马',
    'AS Roma': '罗马',
    'Lazio': '拉齐奥',
    'Atalanta': '亚特兰大',
    'Fiorentina': '佛罗伦萨',
    'Torino': '都灵',
    'Bologna': '博洛尼亚',
    'Monza': '蒙扎',
    'Udinese': '乌迪内斯',
    'Genoa': '热那亚',
    'Cagliari': '卡利亚里',
    'Verona': '维罗纳',
    'Hellas Verona': '维罗纳',
    'Lecce': '莱切',
    'Frosinone': '弗罗西诺内',
    'Empoli': '恩波利',
    'Salernitana': '萨勒尼塔纳',
    'Sassuolo': '萨索洛',
    'Cremonese': '克雷莫纳',
    'Spezia': '斯佩齐亚',
    'Sampdoria': '桑普多利亚',
    'Como': '科莫',
    'Parma': '帕尔马',
    'Venezia': '威尼斯',
    'Cesc Fabregas': '科莫',
    # 西甲
    'Real Madrid': '皇家马德里',
    'Barcelona': '巴塞罗那',
    'Atletico Madrid': '马德里竞技',
    'Athletico Madrid': '马德里竞技',
    'Athletic Club': '毕尔巴鄂竞技',
    'Athletic Bilbao': '毕尔巴鄂竞技',
    'Real Sociedad': '皇家社会',
    'Villarreal': '比利亚雷亚尔',
    'Real Betis': '皇家贝蒂斯',
    'Betis': '皇家贝蒂斯',
    'Sevilla': '塞维利亚',
    'Valencia': '瓦伦西亚',
    'Girona': '赫罗纳',
    'Getafe': '赫塔费',
    'Osasuna': '奥萨苏纳',
    'Celta Vigo': '塞尔塔',
    'Celta': '塞尔塔',
    'Mallorca': '马洛卡',
    'Rayo Vallecano': '巴列卡诺',
    'Rayo': '巴列卡诺',
    'Las Palmas': '拉斯帕尔马斯',
    'Alaves': '阿拉维斯',
    'Alavés': '阿拉维斯',
    'Cadiz': '加的斯',
    'Cádiz': '加的斯',
    'Granada': '格拉纳达',
    'Almeria': '阿尔梅里亚',
    'Almería': '阿尔梅里亚',
    'Espanyol': '西班牙人',
    'Leganes': '莱加内斯',
    'Leganés': '莱加内斯',
    'Valladolid': '巴拉多利德',
    'Eibar': '埃瓦尔',
    'Elche': '埃尔切',
    # 德甲
    'Bayern Munich': '拜仁慕尼黑',
    'Bayern': '拜仁慕尼黑',
    'Dortmund': '多特蒙德',
    'Borussia Dortmund': '多特蒙德',
    'Borussia M\'gladbach': '门兴格拉德巴赫',
    'Borussia Mönchengladbach': '门兴格拉德巴赫',
    'M\'gladbach': '门兴格拉德巴赫',
    'Mönchengladbach': '门兴格拉德巴赫',
    'RB Leipzig': '莱比锡红牛',
    'Leverkusen': '勒沃库森',
    'Bayer Leverkusen': '勒沃库森',
    'Eintracht Frankfurt': '法兰克福',
    'Ein Frankfurt': '法兰克福',
    'Frankfurt': '法兰克福',
    'Wolfsburg': '沃尔夫斯堡',
    'Freiburg': '弗赖堡',
    'Stuttgart': '斯图加特',
    'VfB Stuttgart': '斯图加特',
    'Mainz 05': '美因茨',
    'Mainz': '美因茨',
    'Werder Bremen': '不来梅',
    'Bremen': '不来梅',
    'Augsburg': '奥格斯堡',
    'Hoffenheim': '霍芬海姆',
    'TSG Hoffenheim': '霍芬海姆',
    'Union Berlin': '柏林联合',
    'Bochum': '波鸿',
    'VfL Bochum': '波鸿',
    'Hertha BSC': '柏林赫塔',
    'Hertha': '柏林赫塔',
    'Schalke 04': '沙尔克04',
    'Schalke': '沙尔克04',
    'Köln': '科隆',
    'Koln': '科隆',
    'FC Köln': '科隆',
    'Darmstadt 98': '达姆施塔特',
    'Darmstadt': '达姆施塔特',
    'Heidenheim': '海登海姆',
    'St. Pauli': '圣保利',
    'Holstein Kiel': '基尔',
    'Kiel': '基尔',
    # 法甲
    'Paris S-G': '巴黎圣日耳曼',
    'Paris Saint-Germain': '巴黎圣日耳曼',
    'Paris Saint Germain': '巴黎圣日耳曼',
    'PSG': '巴黎圣日耳曼',
    'Marseille': '马赛',
    'Olympique Marseille': '马赛',
    'Monaco': '摩纳哥',
    'Lyon': '里昂',
    'Olympique Lyonnais': '里昂',
    'Lille': '里尔',
    'Lille OSC': '里尔',
    'Nice': '尼斯',
    'OGC Nice': '尼斯',
    'Lens': '朗斯',
    'Rennes': '雷恩',
    'Stade Rennais': '雷恩',
    'Strasbourg': '斯特拉斯堡',
    'RC Strasbourg': '斯特拉斯堡',
    'Nantes': '南特',
    'FC Nantes': '南特',
    'Toulouse': '图卢兹',
    'Toulouse FC': '图卢兹',
    'Brest': '布雷斯特',
    'Stade Brestois': '布雷斯特',
    'Montpellier': '蒙彼利埃',
    'Reims': '兰斯',
    'Stade de Reims': '兰斯',
    'Lorient': '洛里昂',
    'FC Lorient': '洛里昂',
    'Le Havre': '勒阿弗尔',
    'Metz': '梅斯',
    'FC Metz': '梅斯',
    'Clermont Foot': '克莱蒙',
    'Clermont': '克莱蒙',
    'Lille OSC': '里尔',
    'Saint-Etienne': '圣埃蒂安',
    'Saint-Étienne': '圣埃蒂安',
    'St Etienne': '圣埃蒂安',
    'St-Étienne': '圣埃蒂安',
    'Angers': '昂热',
    'SCO Angers': '昂热',
    'Auxerre': '欧塞尔',
    'AJ Auxerre': '欧塞尔',
    'Nîmes': '尼姆',
    'Nimes': '尼姆',
    'Amiens': '亚眠',
    'Troyes': '特鲁瓦',
    'Estac Troyes': '特鲁瓦',
}


def normalize_fbref_team(fbref_name: str) -> str:
    """将 fbref 英文队名标准化为中文

    优先使用 FBREF_TEAM_NAME_MAP，回退到 feature_utils 的 TEAM_NAME_MAP，
    最后尝试 team_mapping 表。
    """
    if not fbref_name:
        return ""

    # 1. 本地映射表
    if fbref_name in FBREF_TEAM_NAME_MAP:
        return FBREF_TEAM_NAME_MAP[fbref_name]

    # 2. feature_utils 的 TEAM_NAME_MAP
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from feature_utils import TEAM_NAME_MAP, normalize_team_name
        if fbref_name in TEAM_NAME_MAP:
            return TEAM_NAME_MAP[fbref_name]
        result = normalize_team_name(fbref_name)
        if result and result != fbref_name:
            return result
    except Exception:
        pass

    # 3. 未匹配，返回原名（后续可手动补充映射）
    return fbref_name
