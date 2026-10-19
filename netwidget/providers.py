"""出口 IP / 地理信息查询：多个公开接口，按顺序回退。

两步：
1. 只查 IP（轻量、限流宽松）：ipify → checkip.amazonaws.com
2. 该 IP 没有 24 小时内的地理缓存时，再按 IP 查地理：ipwho.is → ip-api.com → ipinfo.io
第 1 步全部失败时，直接用地理接口查“自己”的 IP（同样按顺序回退）。

所有函数都接受注入的 http_get，便于用假响应测试。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .core import normalize_ip

USER_AGENT = "netwidget/%s (+https://github.com/abdiriyim-ap/IP-Time-zone)" % __version__
DEFAULT_TIMEOUT = 3.0

HttpGet = Callable[[str, float], str]


class ProviderError(Exception):
    pass


def http_get(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (固定的公开 URL)
        return resp.read(65536).decode("utf-8", "replace")


def _json(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ProviderError("响应不是 JSON") from exc
    if not isinstance(data, dict):
        raise ProviderError("响应格式不对")
    return data


def _require_ip(value: Any) -> str:
    ip = normalize_ip(value or "")
    if not ip:
        raise ProviderError("响应里没有合法 IP")
    return ip


# ---------------------------------------------------------------- 解析器（纯函数）

def parse_ipify(text: str) -> str:
    return _require_ip(_json(text).get("ip"))


def parse_plain_ip(text: str) -> str:
    return _require_ip(text.strip())


def parse_ipwhois(text: str) -> Dict[str, Any]:
    d = _json(text)
    if d.get("success") is False:
        raise ProviderError("ipwho.is: %s" % d.get("message", "失败"))
    conn = d.get("connection") or {}
    tz = d.get("timezone") or {}
    asn = conn.get("asn")
    return {
        "ip": _require_ip(d.get("ip")),
        "country": d.get("country"),
        "country_code": d.get("country_code"),
        "region": d.get("region"),
        "city": d.get("city"),
        "isp": conn.get("isp") or conn.get("org"),
        "org": conn.get("org"),
        "asn": ("AS%s" % asn) if asn else None,
        "timezone": tz.get("id") if isinstance(tz, dict) else None,
    }


def parse_ipapi(text: str) -> Dict[str, Any]:
    d = _json(text)
    if d.get("status") != "success":
        raise ProviderError("ip-api.com: %s" % d.get("message", "失败"))
    asn = (d.get("as") or "").split(" ", 1)[0] or None
    return {
        "ip": _require_ip(d.get("query")),
        "country": d.get("country"),
        "country_code": d.get("countryCode"),
        "region": d.get("regionName"),
        "city": d.get("city"),
        "isp": d.get("isp"),
        "org": d.get("org"),
        "asn": asn,
        "timezone": d.get("timezone"),
    }


def parse_ipinfo(text: str) -> Dict[str, Any]:
    d = _json(text)
    if d.get("bogon") or d.get("error"):
        raise ProviderError("ipinfo.io: %s" % (d.get("error") or "bogon"))
    org = d.get("org") or ""
    asn, _, isp = org.partition(" ")
    if not asn.startswith("AS"):
        asn, isp = "", org
    return {
        "ip": _require_ip(d.get("ip")),
        "country": d.get("country"),  # ipinfo 免费版只给国家代码
        "country_code": d.get("country"),
        "region": d.get("region"),
        "city": d.get("city"),
        "isp": isp or None,
        "org": org or None,
        "asn": asn or None,
        "timezone": d.get("timezone"),
    }


# (名称, 查自己的 URL, 按 IP 查的 URL 模板, 解析器)
IPAPI_FIELDS = "status,message,country,countryCode,regionName,city,isp,org,as,timezone,query"
GEO_PROVIDERS: List[Tuple[str, str, str, Callable[[str], Dict[str, Any]]]] = [
    ("ipwho.is", "https://ipwho.is/", "https://ipwho.is/{ip}", parse_ipwhois),
    # ip-api.com 免费版只支持 http（不支持 https），只作为第二备选
    ("ip-api.com", "http://ip-api.com/json/?fields=" + IPAPI_FIELDS,
     "http://ip-api.com/json/{ip}?fields=" + IPAPI_FIELDS, parse_ipapi),
    ("ipinfo.io", "https://ipinfo.io/json", "https://ipinfo.io/{ip}/json", parse_ipinfo),
]

IP_PROVIDERS: List[Tuple[str, str, Callable[[str], str]]] = [
    ("ipify", "https://api.ipify.org?format=json", parse_ipify),
    ("aws-checkip", "https://checkip.amazonaws.com/", parse_plain_ip),
]


def _attempt(name: str, ok: bool, error: Optional[str] = None) -> Dict[str, Any]:
    d: Dict[str, Any] = {"provider": name, "ok": ok}
    if error:
        d["error"] = error[:200]
    return d


def _err(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP %s" % exc.code
    return "%s: %s" % (type(exc).__name__, exc)


def discover_ip(get: HttpGet = http_get, providers=None, timeout: float = DEFAULT_TIMEOUT
                ) -> Tuple[Optional[str], Optional[str], List[Dict[str, Any]]]:
    """→ (ip, provider 名, attempts)"""
    attempts: List[Dict[str, Any]] = []
    for name, url, parser in (providers if providers is not None else IP_PROVIDERS):
        try:
            ip = parser(get(url, timeout))
            attempts.append(_attempt(name, True))
            return ip, name, attempts
        except Exception as exc:  # 网络错误、限流、解析失败都算这一家失败
            attempts.append(_attempt(name, False, _err(exc)))
    return None, None, attempts


def lookup_geo(ip: Optional[str] = None, get: HttpGet = http_get, providers=None,
               timeout: float = DEFAULT_TIMEOUT) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """ip=None 时查“自己”。→ (geo 或 None, attempts)。geo 里带 provider 字段。"""
    attempts: List[Dict[str, Any]] = []
    for name, self_url, ip_url, parser in (providers if providers is not None else GEO_PROVIDERS):
        url = ip_url.format(ip=ip) if ip else self_url
        try:
            geo = parser(get(url, timeout))
            if ip and geo["ip"] != ip:
                raise ProviderError("返回的 IP %s 与查询的 %s 不一致" % (geo["ip"], ip))
            geo["provider"] = name
            attempts.append(_attempt(name, True))
            return geo, attempts
        except Exception as exc:
            attempts.append(_attempt(name, False, _err(exc)))
    return None, attempts
