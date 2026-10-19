"""配置与路径。配置用 JSON（标准库即可解析，兼容 macOS 自带的 Python 3.9）。"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from .core import get_zone, normalize_ip

DEFAULTS: Dict[str, Any] = {
    "exits": [],
    "real_ip_hints": [],
    "refresh_ip_seconds": 60,
    "notify": True,
}


def _home() -> str:
    return os.path.expanduser("~")


def config_path() -> str:
    if os.environ.get("NETWIDGET_CONFIG"):
        return os.path.expanduser(os.environ["NETWIDGET_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")
    return os.path.join(base, "netwidget", "exits.json")


def cache_dir() -> str:
    if os.environ.get("NETWIDGET_CACHE_DIR"):
        return os.path.expanduser(os.environ["NETWIDGET_CACHE_DIR"])
    if sys.platform == "darwin":
        return os.path.join(_home(), "Library", "Caches", "netwidget")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(_home(), ".cache")
    return os.path.join(base, "netwidget")


def state_dir() -> str:
    """历史记录放这里（Caches 可能被系统清理，所以分开放）。"""
    if os.environ.get("NETWIDGET_STATE_DIR"):
        return os.path.expanduser(os.environ["NETWIDGET_STATE_DIR"])
    if sys.platform == "darwin":
        return os.path.join(_home(), "Library", "Application Support", "netwidget")
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(_home(), ".local", "state")
    return os.path.join(base, "netwidget")


def example_config_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.example.json")


def validate(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """宽松校验：坏条目跳过并给出提示，不让整个插件挂掉。"""
    errors: List[str] = []
    cfg = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return cfg, ["配置文件顶层必须是 JSON 对象"]

    exits = []
    for i, e in enumerate(raw.get("exits") or []):
        if not isinstance(e, dict) or not e.get("ip"):
            errors.append("exits[%d] 缺少 ip" % i)
            continue
        ip = str(e["ip"]).strip()
        if "/" not in ip and not normalize_ip(ip):
            errors.append("exits[%d] ip 不合法：%s" % (i, ip))
            continue
        if "/" in ip:
            import ipaddress
            try:
                ipaddress.ip_network(ip, strict=False)
            except ValueError:
                errors.append("exits[%d] 网段不合法：%s" % (i, ip))
                continue
        tz = e.get("expect_tz")
        if tz and get_zone(tz) is None:
            errors.append("exits[%d] expect_tz 不是有效时区：%s" % (i, tz))
            tz = None
        exits.append({"ip": ip, "label": str(e.get("label") or ip), "expect_tz": tz})
    cfg["exits"] = exits

    hints = []
    for h in raw.get("real_ip_hints") or []:
        import ipaddress
        try:
            ipaddress.ip_network(str(h).strip(), strict=False)
            hints.append(str(h).strip())
        except ValueError:
            errors.append("real_ip_hints 网段不合法：%s" % h)
    cfg["real_ip_hints"] = hints

    try:
        cfg["refresh_ip_seconds"] = max(15, int(raw.get("refresh_ip_seconds", DEFAULTS["refresh_ip_seconds"])))
    except (TypeError, ValueError):
        errors.append("refresh_ip_seconds 必须是整数")
    cfg["notify"] = bool(raw.get("notify", True))
    return cfg, errors


def load_config(path: Optional[str] = None) -> Tuple[Dict[str, Any], List[str], bool]:
    """→ (config, errors, 文件是否存在)"""
    path = path or config_path()
    if not os.path.exists(path):
        return dict(DEFAULTS), [], False
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        return dict(DEFAULTS), ["无法读取 %s：%s" % (path, exc)], True
    cfg, errors = validate(raw)
    return cfg, errors, True
