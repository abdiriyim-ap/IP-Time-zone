"""编排层：读状态 → 查 IP（按需）→ 算状态 / 网速 / 时区 → 通知 + 历史 → 快照。

所有外部依赖（HTTP、系统探测、时间、睡眠）都可注入，方便测试。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import __version__, core, providers
from . import system as default_system
from .config import cache_dir, config_path, load_config, state_dir

GEO_TTL = 24 * 3600
HISTORY_KEEP = 200
NOTIFY_WINDOW = 600


class Paths:
    def __init__(self, config: Optional[str] = None, cache: Optional[str] = None, state: Optional[str] = None):
        self.config = config or config_path()
        self.cache = cache or cache_dir()
        self.state_dir = state or state_dir()
        self.state = os.path.join(self.cache, "state.json")
        self.geo_cache = os.path.join(self.cache, "geo_cache.json")
        self.history = os.path.join(self.state_dir, "history.log")


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def append_history(path: str, line: str, keep: int = HISTORY_KEEP) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    lines = core.trim_lines(lines + [line], keep)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def _refresh_ip(get, now_ts: float, geo_cache: Dict[str, Any]) -> Dict[str, Any]:
    ip, ip_provider, attempts = providers.discover_ip(get)
    geo, geo_provider = None, None
    if ip:
        cached = geo_cache.get(ip)
        if cached and now_ts - float(cached.get("fetched_at", 0)) < GEO_TTL:
            geo = cached.get("geo")
            geo_provider = "缓存(%s)" % (geo or {}).get("provider", "?")
        else:
            geo, more = providers.lookup_geo(ip, get)
            attempts += more
    else:
        geo, more = providers.lookup_geo(None, get)
        attempts += more
        if geo:
            ip, ip_provider = geo["ip"], geo["provider"]
    if geo and not geo_provider:
        geo_provider = geo.get("provider")
        geo_cache[ip] = {"fetched_at": now_ts, "geo": geo}
    return {"ip": ip, "geo": geo, "ip_provider": ip_provider, "geo_provider": geo_provider,
            "attempts": attempts}


def collect(paths: Optional[Paths] = None, *, force: bool = False, notify: bool = True,
            get: Callable[[str, float], str] = providers.http_get, sysmod=default_system,
            clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
            sample_seconds: float = 1.0) -> Dict[str, Any]:
    paths = paths or Paths()
    cfg, cfg_errors, cfg_exists = load_config(paths.config)
    state: Dict[str, Any] = load_json(paths.state, {})
    if not isinstance(state, dict):
        state = {}
    geo_cache: Dict[str, Any] = load_json(paths.geo_cache, {})
    if not isinstance(geo_cache, dict):
        geo_cache = {}

    now_ts = clock()
    now = datetime.fromtimestamp(now_ts, timezone.utc)
    checked = state.get("ip_checked_at")
    need = (force or checked is None or not state.get("ip")
            or now_ts - float(checked) >= cfg["refresh_ip_seconds"])

    if need:
        fresh = _refresh_ip(get, now_ts, geo_cache)
        geo_cache = {k: v for k, v in geo_cache.items()
                     if isinstance(v, dict) and now_ts - float(v.get("fetched_at", 0)) < GEO_TTL}
        save_json(paths.geo_cache, geo_cache)
        info = dict(fresh, checked_at=now_ts)
    else:
        info = {"ip": state.get("ip"), "geo": state.get("geo"), "ip_provider": state.get("ip_provider"),
                "geo_provider": state.get("geo_provider"), "attempts": state.get("attempts") or [],
                "checked_at": float(checked)}

    ip, geo = info["ip"], info["geo"] or {}
    status = core.compute_status(ip, cfg["exits"], cfg["real_ip_hints"], geo, now)

    # ---- 时区
    exit_tz_name, exit_src = geo.get("timezone"), "geo"
    if not core.get_zone(exit_tz_name) and status.get("exit") and status["exit"].get("expect_tz"):
        exit_tz_name, exit_src = status["exit"]["expect_tz"], "expect_tz"
    exit_tz = core.describe_tz(exit_tz_name, now)
    sys_tz_name = sysmod.system_timezone()
    sys_tz = core.describe_tz(sys_tz_name, now)
    if sys_tz is None and hasattr(sysmod, "local_offset_seconds"):
        sys_tz = core.describe_fixed_offset(sysmod.local_offset_seconds(), "（时区名未知）", now)
    timezones = {"exit": exit_tz, "exit_source": exit_src if exit_tz else None, "system": sys_tz,
                 "mismatch": bool(exit_tz and sys_tz and exit_tz["offset_seconds"] != sys_tz["offset_seconds"])}

    # ---- 网速
    prev = state.get("counters")
    cur = sysmod.sample_counters()
    if prev and prev.get("names") != cur.get("names"):
        prev = None  # 网卡集合变了（插拔网线 / 换网卡），本次不算
    speed = core.compute_speed(prev, cur)
    if speed is None and sample_seconds > 0:
        sleep(sample_seconds)  # 首次运行或间隔太久：现场采样一次
        first, cur = cur, sysmod.sample_counters()
        if first.get("names") == cur.get("names"):
            speed = core.compute_speed(first, cur)
    speed_info = {"down_bps": speed[0] if speed else None, "up_bps": speed[1] if speed else None,
                  "ifaces": cur.get("ifaces") or []}

    # ---- 辅助信号（只展示，不参与判断）
    signals: Dict[str, Any] = {}
    if getattr(sysmod, "IS_MAC", False):
        qx = sysmod.quantumult_x_running()
        tuns = sysmod.tun_ipv4()
        signals = {"quantumult_x_running": qx, "tun_ipv4": tuns,
                   "proxy_likely_off": bool(qx is False and not tuns
                                            and status["reason"] != core.REASON_WHITELISTED)}

    # ---- 历史 + 通知（只在真正刷新 IP 时）
    sent_notes: List[Dict[str, Any]] = []
    if need:
        first_run = "last_ip" not in state
        last_ip = state.get("last_ip")
        if first_run or last_ip != ip:
            append_history(paths.history, core.history_line(
                datetime.fromtimestamp(now_ts).astimezone(),
                last_ip, "首次记录" if first_run else state.get("last_label"),
                ip, status.get("label"), status["level"], geo.get("country_code")))
        events = core.notification_events(state.get("last_online_ip"), state.get("last_online_label"),
                                          ip, status, geo)
        sent = core.prune_sent(state.get("notified") or {}, now_ts, NOTIFY_WINDOW)
        for key, title, msg in events:
            ok = core.should_notify(key, sent, now_ts, NOTIFY_WINDOW)
            delivered = False
            if ok and notify and cfg["notify"]:
                delivered = sysmod.notify(title, msg)
                sent[key] = now_ts
            sent_notes.append({"key": key, "title": title, "message": msg,
                               "suppressed": not ok, "delivered": delivered})
        state["notified"] = sent
        state["last_ip"] = ip
        state["last_label"] = status.get("label")
        if ip:
            state["last_online_ip"] = ip
            state["last_online_label"] = status.get("label")

    state.update({"ip": ip, "geo": info["geo"], "ip_provider": info["ip_provider"],
                  "geo_provider": info["geo_provider"], "attempts": info["attempts"],
                  "ip_checked_at": info["checked_at"], "counters": cur})
    save_json(paths.state, state)

    return {
        "version": __version__,
        "generated_at": now.isoformat(),
        "ip": ip,
        "geo": info["geo"],
        "status": {k: v for k, v in status.items()},
        "status_text": core.REASON_TEXT[status["reason"]],
        "timezones": timezones,
        "speed": speed_info,
        "signals": signals,
        "source": {"ip_provider": info["ip_provider"], "geo_provider": info["geo_provider"],
                   "refreshed": need, "age_seconds": int(now_ts - info["checked_at"]),
                   "attempts": info["attempts"]},
        "config": {"path": paths.config, "exists": cfg_exists, "exits": len(cfg["exits"]),
                   "real_ip_hints": len(cfg["real_ip_hints"]), "errors": cfg_errors},
        "notifications": sent_notes,
    }


def force_next_refresh(paths: Paths) -> None:
    state = load_json(paths.state, {})
    if isinstance(state, dict):
        state["ip_checked_at"] = None
        save_json(paths.state, state)
