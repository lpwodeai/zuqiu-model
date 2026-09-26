# -*- coding: utf-8 -*-
"""
DoH HTTP 客户端 —— DNS 污染自动绕过工具（DATA-013 自动化实现）
================================================================

背景：
  本地 DNS 被污染时（如 api.sofascore.com / www.sofascore.com 解析失败或
  被劫持），普通 requests/curl_cffi 在 DNS 层即失败。经实测（C-20260918-053），
  绕过链路为：
    ① DoH（DNS over HTTPS）解析域名真实 IP（加密 DNS，不走本地运营商解析器）
    ② curl_cffi impersonate="chrome" 保留 TLS 指纹（Akamai 等反爬不拦截）
    ③ 请求 URL 用 IP，附加 Host 头，verify=False 跳过证书名校验
       （CDN 按 Host 头回源，SNI 为 IP 不影响路由）

能力边界：
  本脚本只解决「DNS 污染/劫持」类网络层阻断；若对方升级为 IP 封禁或
  CAPTCHA 人机验证，本工具无效（需代理或人工验证）。

作为模块复用：
    from doh_http_client import DohSession
    with DohSession() as s:
        r = s.get("https://api.sofascore.com/api/v1/event/16416299")
        print(r.status_code, r.json())

CLI 用法：
  # 诊断域名 DNS 状态（直连 / DoH 结果对比）
  python collection/doh_http_client.py check api.sofascore.com

  # 仅做 DoH 解析
  python collection/doh_http_client.py resolve api.sofascore.com

  # 经绕过链路发请求（默认直连优先，失败自动降级 DoH）
  python collection/doh_http_client.py fetch https://api.sofascore.com/api/v1/event/16416299

  # 强制走 DoH（已知污染，跳过直连探测）
  python collection/doh_http_client.py fetch --force-doh https://api.sofascore.com/api/v1/event/16416299
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CFFI = True
except Exception:  # curl_cffi 缺失时降级 requests（TLS 指纹模拟失效，仅 DoH+IP 仍可用于无反爬站点）
    import requests as cffi_requests
    _HAS_CFFI = False

logger = logging.getLogger("doh_http")

# ============================================================
# DoH 服务商配置（application/dns-json 协议，RFC 8484 JSON 变体）
# 按国内可达性排序；全部支持 GET ?name=xxx&type=A
# ============================================================
@dataclass
class DoHProvider:
    name: str
    endpoint: str          # {name} 占位符替换为查询域名
    headers: Dict[str, str]

DOH_PROVIDERS: List[DoHProvider] = [
    DoHProvider("AliDNS", "https://223.5.5.5/resolve?name={name}&type=A",
                {"Accept": "application/dns-json"}),
    DoHProvider("AliDNS-Backup", "https://223.6.6.6/resolve?name={name}&type=A",
                {"Accept": "application/dns-json"}),
    DoHProvider("DNSPod", "https://1.12.12.12/dns-query?name={name}&type=A",
                {"Accept": "application/dns-json"}),
    DoHProvider("Tencent-DoH", "https://doh.pub/dns-query?name={name}&type=A",
                {"Accept": "application/dns-json"}),
]

DEFAULT_TIMEOUT = 15
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")


# ============================================================
# DoH 解析器
# ============================================================
class DoHResolver:
    """DoH 解析：多服务商容错 + 简单缓存。"""

    def __init__(self, providers: Optional[List[DoHProvider]] = None,
                 cache_ttl: int = 300):
        self.providers = providers or DOH_PROVIDERS
        self._cache: Dict[str, Tuple[float, List[str]]] = {}
        self.cache_ttl = cache_ttl

    def resolve_a(self, host: str) -> List[str]:
        """返回域名的全部 A 记录；全部 DoH 服务商失败时抛 RuntimeError。"""
        cached = self._cache.get(host)
        if cached and time.time() - cached[0] < self.cache_ttl:
            return cached[1]

        last_err: Optional[Exception] = None
        for p in self.providers:
            try:
                import requests as _std_requests
                r = _std_requests.get(
                    p.endpoint.format(name=host),
                    headers=p.headers, timeout=8,
                )
                if r.status_code != 200:
                    last_err = RuntimeError(f"{p.name} HTTP {r.status_code}")
                    continue
                data = r.json()
                # Status=0 表示无错误（RFC 1035 RCODE）
                if data.get("Status", 0) != 0:
                    last_err = RuntimeError(f"{p.name} RCODE={data.get('Status')}")
                    continue
                ips = [a["data"] for a in data.get("Answer", [])
                       if a.get("type") == 1 and "data" in a]
                if ips:
                    self._cache[host] = (time.time(), ips)
                    logger.debug(f"[DoH:{p.name}] {host} -> {ips}")
                    return ips
                last_err = RuntimeError(f"{p.name} 无 A 记录")
            except Exception as e:
                last_err = e
                logger.debug(f"[DoH:{p.name}] {host} 失败: {e}")
                continue
        raise RuntimeError(f"全部 DoH 服务商解析失败 {host}: {last_err}")

    def resolve(self, host: str) -> str:
        """返回首个 A 记录。"""
        return self.resolve_a(host)[0]

    def local_dns_healthy(self, host: str) -> Optional[List[str]]:
        """探测本地 DNS 解析结果；失败（污染/无记录）返回 None。

        注意：污染也可能返回「可解析但为劫持 IP」，此函数只判断能否解析；
        调用方可对比本地结果与 DoH 结果识别劫持。
        """
        try:
            infos = socket.getaddrinfo(host, None, socket.AF_INET)
            ips = list({i[4][0] for i in infos})
            return ips if ips else None
        except socket.gaierror:
            return None


# ============================================================
# DoH 会话（自动降级核心）
# ============================================================
class DohSession:
    """带 DNS 污染自动绕过的 HTTP 会话。

    请求策略（每次请求独立判定）：
      1. 默认先用本地 DNS 直连（未污染时零开销）
      2. 直连失败（DNS 错误 / 连接超时）自动切换：DoH 解析 → IP 直连 + Host 头
      3. force_doh=True 时跳过步骤 1（已知污染场景）
    """

    def __init__(self, force_doh: bool = False,
                 impersonate: str = "chrome",
                 timeout: int = DEFAULT_TIMEOUT,
                 resolver: Optional[DoHResolver] = None):
        self.force_doh = force_doh
        self.timeout = timeout
        self.resolver = resolver or DoHResolver()
        if _HAS_CFFI:
            self._sess = cffi_requests.Session(impersonate=impersonate)
        else:
            self._sess = cffi_requests.Session()
        self._sess.headers.update({"User-Agent": _UA})

    # ---------- URL 改写 ----------
    @staticmethod
    def _rewrite_url(url: str, ip: str) -> str:
        """https://host/path → https://ip/path（保留端口/路径/query）。"""
        p = urlparse(url)
        netloc = ip
        if p.port:  # 非标准端口保留
            netloc = f"{ip}:{p.port}"
        return urlunparse((p.scheme or "https", netloc, p.path or "/",
                           p.params, p.query, p.fragment))

    @staticmethod
    def _is_dns_or_conn_error(exc: Exception) -> bool:
        """判定异常是否为网络层阻断（DNS/连接），值得降级重试。"""
        name = type(exc).__name__
        if name in ("DNSError", "ConnectionError", "Timeout",
                    "gaierror", "CurlError"):
            return True
        msg = str(exc)
        return any(k in msg for k in (
            "Could not resolve", "DNS", "Connection timed out",
            "Failed to connect", "(6)", "(7)", "(28)",
        ))

    # ---------- 通用请求 ----------
    def request(self, method: str, url: str,
                headers: Optional[Dict[str, str]] = None,
                **kwargs: Any) -> Any:
        headers = dict(headers or {})
        kwargs.setdefault("timeout", self.timeout)
        host = urlparse(url).hostname or ""

        # 策略 1：本地 DNS 直连
        if not self.force_doh:
            try:
                return self._sess.request(method, url, headers=headers, **kwargs)
            except Exception as e:
                if not self._is_dns_or_conn_error(e):
                    raise
                logger.info(f"[降级] 直连失败，切换 DoH: {host} ({e})")

        # 策略 2：DoH + IP 直连 + Host 头
        ips = self.resolver.resolve_a(host)
        last_exc: Optional[Exception] = None
        for ip in ips:  # 逐个 A 记录尝试（CDN 多 IP 时自动容错）
            ip_url = self._rewrite_url(url, ip)
            ip_headers = {**headers, "Host": host}
            try:
                return self._sess.request(
                    method, ip_url, headers=ip_headers,
                    verify=False, **kwargs)
            except Exception as e:
                last_exc = e
                if not self._is_dns_or_conn_error(e):
                    raise
                logger.debug(f"[DoH重试] {ip} 失败: {e}")
        raise RuntimeError(f"DoH 绕过失败 {host}，已试 IP {ips}: {last_exc}")

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        try:
            self._sess.close()
        except Exception:
            pass

    def __enter__(self) -> "DohSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ============================================================
# CLI
# ============================================================
def _cli_check(args: argparse.Namespace) -> int:
    resolver = DoHResolver()
    print(f"=== DNS 诊断: {args.host} ===\n")

    print("[本地 DNS]")
    local = resolver.local_dns_healthy(args.host)
    print(f"  {local if local else '❌ 解析失败（疑似污染）'}")

    print("\n[DoH 加密解析]")
    try:
        t0 = time.time()
        doh_ips = resolver.resolve_a(args.host)
        print(f"  ✅ {doh_ips}  ({time.time()-t0:.2f}s)")
    except Exception as e:
        print(f"  ❌ {e}")
        return 1

    if local is not None:
        hijacked = set(local) - set(doh_ips)
        if hijacked and not set(doh_ips) & set(local):
            print(f"\n⚠️ 本地结果与 DoH 完全不一致，疑似 DNS 劫持（本地多出 {list(hijacked)}）")
        else:
            print("\n✅ 本地 DNS 与 DoH 结果一致，无污染迹象")
    return 0


def _cli_resolve(args: argparse.Namespace) -> int:
    resolver = DoHResolver()
    try:
        ips = resolver.resolve_a(args.host)
    except Exception as e:
        print(f"❌ {e}")
        return 1
    print("\n".join(ips))
    return 0


def _cli_fetch(args: argparse.Namespace) -> int:
    with DohSession(force_doh=args.force_doh) as s:
        t0 = time.time()
        r = s.get(args.url, headers={"Accept": args.accept})
        elapsed = time.time() - t0
        print(f"HTTP {r.status_code} | {len(r.content)} bytes | {elapsed:.2f}s")
        body = r.text
        if args.json:
            try:
                body = json.dumps(r.json(), ensure_ascii=False, indent=2)
            except Exception:
                pass
        print(body[:args.limit] if args.limit else body)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(
        description="DoH HTTP 客户端：DNS 污染自动绕过（DATA-013）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_chk = sub.add_parser("check", help="诊断域名 DNS 污染状态")
    p_chk.add_argument("host")
    p_chk.set_defaults(fn=_cli_check)

    p_res = sub.add_parser("resolve", help="DoH 解析域名")
    p_res.add_argument("host")
    p_res.set_defaults(fn=_cli_resolve)

    p_fetch = sub.add_parser("fetch", help="经绕过链路发 GET 请求")
    p_fetch.add_argument("url")
    p_fetch.add_argument("--force-doh", action="store_true",
                         help="跳过直连，强制 DoH+IP 直连")
    p_fetch.add_argument("--json", action="store_true", help="JSON 美化输出")
    p_fetch.add_argument("--accept", default="*/*")
    p_fetch.add_argument("--limit", type=int, default=0,
                         help="只打印响应前 N 字符")
    p_fetch.set_defaults(fn=_cli_fetch)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
